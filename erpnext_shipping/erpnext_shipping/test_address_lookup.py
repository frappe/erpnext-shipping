from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_shipping.erpnext_shipping import address_lookup

PHOTON = {
	"features": [
		{
			"properties": {
				"type": "house",
				"housenumber": "12",
				"street": "Queen Street",
				"district": "Ōtāhuhu",
				"city": "Auckland",
				"state": "Auckland",
				"postcode": "1062",
				"countrycode": "NZ",
			}
		},
		{
			"properties": {
				"type": "house",
				"housenumber": "12",
				"street": "Queen Street",
				"city": "Auckland",
				"countrycode": "AU",
			}
		},
		{"properties": {"type": "city", "name": "Auckland", "countrycode": "NZ"}},
	]
}
GOOGLE_SUGGESTIONS = {
	"suggestions": [
		{
			"placePrediction": {
				"placeId": "place-1",
				"text": {"text": "3/45 Rata Road, Hataitai, Wellington"},
				"structuredFormat": {
					"mainText": {"text": "3/45 Rata Road"},
					"secondaryText": {"text": "Hataitai, Wellington"},
				},
			}
		}
	]
}
GOOGLE_PLACE = {
	"addressComponents": [
		{"longText": "3", "types": ["subpremise"]},
		{"longText": "45", "types": ["street_number"]},
		{"longText": "Rata Road", "types": ["route"]},
		{"longText": "Hataitai", "types": ["sublocality_level_1", "sublocality"]},
		{"longText": "Wellington", "types": ["locality"]},
		{"longText": "Wellington", "types": ["administrative_area_level_1"]},
		{"longText": "6021", "types": ["postal_code"]},
		{"longText": "New Zealand", "shortText": "NZ", "types": ["country"]},
	]
}


def answer(body):
	response = MagicMock()
	response.json.return_value = body
	return response


class TestAddressLookup(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		settings = frappe.get_doc("Address Lookup Settings")
		self.addCleanup(self.restore, settings.service, settings.photon_server)
		frappe.cache.delete_keys("address_lookup:")

	def restore(self, service, server):
		frappe.db.set_single_value(
			"Address Lookup Settings", {"service": service, "photon_server": server, "daily_limit": 300}
		)
		frappe.clear_document_cache("Address Lookup Settings", "Address Lookup Settings")

	def use(self, service, **values):
		settings = frappe.get_doc("Address Lookup Settings")
		settings.service = service
		settings.update(values)
		settings.save()

	def test_off_until_a_service_is_chosen(self):
		self.use("")
		self.assertEqual(address_lookup.suggestions("12 Queen Street"), {"enabled": False, "suggestions": []})

	@patch("erpnext_shipping.erpnext_shipping.address_lookup.requests.get")
	def test_photon_suggests_addresses_in_the_form_country(self, get):
		self.use("Photon", photon_server="https://photon.example.com/")
		get.return_value = answer(PHOTON)

		result = address_lookup.suggestions("12 Queen", country="New Zealand", session="s1")

		self.assertTrue(result["enabled"])
		self.assertEqual(result["attribution"], "Addresses © OpenStreetMap contributors")
		self.assertEqual(len(result["suggestions"]), 1)
		suggestion = result["suggestions"][0]
		self.assertEqual(suggestion["label"], "12 Queen Street")
		self.assertEqual(suggestion["detail"], "Ōtāhuhu, Auckland, 1062, New Zealand")
		self.assertEqual(
			suggestion["address"],
			{
				"address_line1": "12 Queen Street",
				"address_line2": "Ōtāhuhu",
				"city": "Auckland",
				"state": "Auckland",
				"pincode": "1062",
				"country": "New Zealand",
			},
		)
		url, params = get.call_args.args[0], get.call_args.kwargs["params"]
		self.assertEqual(url, "https://photon.example.com/api/")
		self.assertIn(("countrycode", "NZ"), params)

		# the same text again is answered from the cache
		address_lookup.suggestions("12 queen", country="New Zealand")
		self.assertEqual(get.call_count, 1)

	@patch("erpnext_shipping.erpnext_shipping.address_lookup.requests.get")
	def test_short_text_asks_nothing(self, get):
		self.use("Photon")
		self.assertEqual(address_lookup.suggestions("12")["suggestions"], [])
		get.assert_not_called()

	@patch("erpnext_shipping.erpnext_shipping.address_lookup.requests.get")
	@patch("erpnext_shipping.erpnext_shipping.address_lookup.requests.post")
	def test_google_places_suggests_then_gives_the_address(self, post, get):
		self.use("Google Places", google_api_key="test-key")
		post.return_value = answer(GOOGLE_SUGGESTIONS)
		get.return_value = answer(GOOGLE_PLACE)

		result = address_lookup.suggestions("45 Rata", country="New Zealand", session="s2")

		self.assertEqual(result["attribution"], "Powered by Google")
		self.assertEqual(
			result["suggestions"],
			[{"id": "place-1", "label": "3/45 Rata Road", "detail": "Hataitai, Wellington"}],
		)
		body, headers = post.call_args.kwargs["json"], post.call_args.kwargs["headers"]
		self.assertEqual(body["includedRegionCodes"], ["nz"])
		self.assertEqual(body["sessionToken"], "s2")
		self.assertEqual(headers["X-Goog-Api-Key"], "test-key")

		found = address_lookup.details("place-1", session="s2")

		self.assertEqual(
			found,
			{
				"address_line1": "3/45 Rata Road",
				"address_line2": "Hataitai",
				"city": "Wellington",
				"state": "Wellington",
				"pincode": "6021",
				"country": "New Zealand",
			},
		)
		self.assertEqual(get.call_args.kwargs["params"], {"sessionToken": "s2"})
		self.assertEqual(get.call_args.kwargs["headers"]["X-Goog-FieldMask"], "addressComponents")

	@patch("erpnext_shipping.erpnext_shipping.address_lookup.requests.post")
	def test_google_stops_at_the_daily_limit(self, post):
		self.use("Google Places", google_api_key="test-key", daily_limit=2)
		frappe.cache.delete_value(f"address_lookup:google:{frappe.utils.nowdate()}")
		post.return_value = answer(GOOGLE_SUGGESTIONS)
		for text in ("45 Rata", "46 Rata", "47 Rata"):
			address_lookup.suggestions(text, country="New Zealand")
		self.assertEqual(post.call_count, 2)

	def test_the_api_key_stays_on_the_server(self):
		self.use("Google Places", google_api_key="secret-key")
		self.assertNotIn("secret-key", frappe.as_json(address_lookup.status()))
		self.assertEqual(address_lookup.status()["service"], "Google Places")
		self.assertNotIn("secret-key", frappe.as_json(frappe.get_doc("Address Lookup Settings").as_dict()))
