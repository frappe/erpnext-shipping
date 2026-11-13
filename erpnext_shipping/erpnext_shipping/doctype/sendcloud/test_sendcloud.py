# Copyright (c) 2020, Frappe and Contributors
# See license.txt

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_shipping.erpnext_shipping.shipping import create_shipment, fetch_shipping_rates

REQUESTS_POST = "erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud.requests.post"


def shipping_option(code, carrier, name, quotes, quote_error=None):
	return {
		"code": code,
		"name": name,
		"carrier": {"code": carrier.lower(), "name": carrier},
		"quotes": quotes,
		"quote_error": quote_error,
	}


PRICED_OPTION = shipping_option(
	"dhl:paket",
	"DHL",
	"DHL Paket",
	quotes=[{"price": {"total": {"value": "5.90", "currency": "EUR"}}}],
)
UNPRICED_OPTION = shipping_option(
	"ups:standard",
	"UPS",
	"UPS Standard",
	quotes=[],
	quote_error={
		"code": "no_price_for_route",
		"title": "Price missing",
		"description": "No price is available for this route.",
	},
)


class TestSendCloud(FrappeTestCase):
	def setUp(self):
		frappe.db.set_single_value("LetMeShip", "enabled", 0)
		settings = frappe.get_single("SendCloud")
		settings.enabled = 1
		settings.api_key = "test-key"
		settings.api_secret = "test-secret"
		settings.save()

	def make_address(self, title):
		# ERPNext renders the address on save, which needs an Address Template
		if not frappe.db.exists("Address Template", "Germany"):
			frappe.get_doc({"doctype": "Address Template", "country": "Germany"}).insert()

		return frappe.get_doc(
			{
				"doctype": "Address",
				"address_title": title,
				"address_type": "Shipping",
				"address_line1": "Teststrasse 1",
				"city": "Berlin",
				"pincode": "10115",
				"country": "Germany",
			}
		).insert()

	def test_unpriced_options_are_returned_with_reason(self):
		pickup_address = self.make_address("Test Pickup")
		delivery_address = self.make_address("Test Delivery")
		response = MagicMock()
		response.json.return_value = {"data": [UNPRICED_OPTION, PRICED_OPTION]}

		with patch(REQUESTS_POST, return_value=response):
			services = fetch_shipping_rates(
				pickup_from_type="Company",
				delivery_to_type="Customer",
				pickup_address_name=pickup_address.name,
				delivery_address_name=delivery_address.name,
				parcels=json.dumps([{"weight": 1, "count": 1}]),
				description_of_content="Test",
				pickup_date="2026-01-01",
				value_of_goods=10,
			)

		service_names = [service.service_name for service in services]
		self.assertEqual(service_names, ["DHL Paket", "UPS Standard"])

		unpriced_service = services[1]
		self.assertIsNone(unpriced_service.get("total_price"))
		self.assertEqual(unpriced_service.quote_error_title, "Price missing")
		self.assertEqual(unpriced_service.quote_error_description, "No price is available for this route.")

	def test_unpriced_service_cannot_be_booked(self):
		service_data = json.dumps(
			{
				"service_provider": "SendCloud",
				"carrier": "UPS",
				"service_name": "UPS Standard",
				"service_id": "ups:standard",
			}
		)

		with patch(REQUESTS_POST) as post, self.assertRaises(frappe.ValidationError):
			create_shipment(
				shipment="does-not-matter",
				pickup_from_type="Company",
				delivery_to_type="Customer",
				pickup_address_name="does-not-matter",
				delivery_address_name="does-not-matter",
				shipment_parcel="[]",
				description_of_content="Test",
				pickup_date="2026-01-01",
				value_of_goods=10,
				service_data=service_data,
			)

		post.assert_not_called()
