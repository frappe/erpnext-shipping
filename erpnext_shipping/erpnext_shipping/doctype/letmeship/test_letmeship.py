# Copyright (c) 2020, Frappe and Contributors
# See license.txt

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import TEST_BASE_URL, LetMeShipUtils
from erpnext_shipping.install import enable_letmeship_party_name_as_company_name

REQUESTS_REQUEST = "erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship.requests.request"


class TestLetMeShip(FrappeTestCase):
	def test_party_name_setting_is_on_by_default(self):
		field = frappe.get_meta("LetMeShip").get_field("use_party_name_as_company_name")
		self.assertEqual(field.default, "1")

	def test_install_enables_party_name_setting(self):
		frappe.db.set_single_value("LetMeShip", "use_party_name_as_company_name", 0)
		enable_letmeship_party_name_as_company_name()
		self.assertEqual(frappe.db.get_single_value("LetMeShip", "use_party_name_as_company_name"), 1)

	def test_label_uses_party_name_when_enabled(self):
		frappe.db.set_single_value("LetMeShip", "use_party_name_as_company_name", 1)
		company_name = self.book_and_get_company_name(delivery_company_name="Kunde GmbH")
		self.assertEqual(company_name, "Kunde GmbH")

	def test_label_uses_address_title_when_disabled(self):
		frappe.db.set_single_value("LetMeShip", "use_party_name_as_company_name", 0)
		company_name = self.book_and_get_company_name(delivery_company_name="Kunde GmbH")
		self.assertEqual(company_name, "Kunde GmbH-Shipping")

	def test_label_falls_back_to_address_title_without_party(self):
		frappe.db.set_single_value("LetMeShip", "use_party_name_as_company_name", 1)
		company_name = self.book_and_get_company_name(delivery_company_name=None)
		self.assertEqual(company_name, "Kunde GmbH-Shipping")

	def book_and_get_company_name(self, delivery_company_name):
		"""Book a shipment with a mocked LetMeShip API and return the company name sent."""
		delivery_address = frappe._dict(
			address_title="Kunde GmbH-Shipping",
			address_line1="Lieferweg 2",
			address_line2="",
			pincode="20095",
			city="Hamburg",
			country_code="DE",
		)
		pickup_address = frappe._dict(
			address_title="Test Pickup",
			address_line1="Teststrasse 1",
			address_line2="",
			pincode="10115",
			city="Berlin",
			country_code="DE",
		)
		pickup_contact = frappe._dict(
			first_name="Erika", last_name="Muster", phone="+49 30 1234", email_id="", gender=""
		)
		delivery_contact = frappe._dict(pickup_contact)
		service_info = {
			"id": "1",
			"service_name": "UPS Standard",
			"carrier": "UPS",
			"price_info": {"totalPrice": 9.5},
		}
		response = MagicMock()
		response.json.return_value = {
			"shipmentId": "1",
			"service": {
				"baseServiceDetails": {
					"carrier": "UPS",
					"name": "UPS Standard",
					"priceInfo": {"totalPrice": 9.5},
				}
			},
		}

		with patch(REQUESTS_REQUEST, return_value=response) as request:
			LetMeShipUtils(TEST_BASE_URL, "test-id", "test-password").create_shipment(
				pickup_address=pickup_address,
				delivery_company_name=delivery_company_name,
				delivery_address=delivery_address,
				shipment_parcel=json.dumps(
					[{"length": 10, "width": 10, "height": 10, "weight": 1, "count": 1}]
				),
				description_of_content="Test",
				pickup_date="2026-01-01",
				value_of_goods=10,
				service_info=service_info,
				pickup_contact=pickup_contact,
				delivery_contact=delivery_contact,
			)

		booking_call = request.call_args_list[0]
		return booking_call.kwargs["json"]["deliveryInfo"]["company"]
