# Copyright (c) 2025, Frappe and Contributors
# See license.txt

import json

import frappe
from erpnext.tests.utils import ERPNextTestSuite

from erpnext_shipping.erpnext_shipping.doctype.envia.envia import (
	ENVIA_PROVIDER,
	EnviaUtils,
	map_envia_tracking_status,
)


class TestEnvia(ERPNextTestSuite):
	def setUp(self):
		self.envia_settings = get_live_envia_settings()
		if not self.envia_settings:
			self.skipTest("No enabled sandbox Envia record found.")

		self.company = frappe.get_doc("Company", self.envia_settings.company)
		frappe.db.set_value("Company", self.company.name, "default_currency", "INR")

		self.pickup_address = create_shipment_address(
			"Envia Pickup", self.company.name, "641605", city="Test", state="Tamil Nadu"
		)
		self.pickup_address_dict = get_address_dict(self.pickup_address, "IN")
		self.pickup_contact = create_customer_contact(
			"Pickup", "Contact", phone="+919876543210", email="randomme@email.com"
		)
		self.pickup_contact_dict = self.pickup_contact.as_dict()

		self.delivery_address = create_shipment_address(
			"Envia Delivery", "Delivery Company", "641605", city="Test", state="Tamil Nadu"
		)
		self.delivery_address_dict = get_address_dict(self.delivery_address, "IN")
		self.delivery_contact = create_customer_contact(
			"Delivery", "Contact", phone="+919876543210", email="delivery@example.com"
		)
		self.delivery_contact_dict = self.delivery_contact.as_dict()

		self.utils = EnviaUtils(company=self.company.name)

	def test_uses_sandbox_urls(self):
		self.assertTrue(self.envia_settings.sandbox)
		self.assertEqual(self.utils.api_url, self.envia_settings.test_base_api_url.rstrip("/"))
		self.assertEqual(self.utils.query_url, self.envia_settings.test_base_url_query.rstrip("/"))

	def test_map_envia_tracking_status(self):
		self.assertEqual(map_envia_tracking_status(3), "Delivered")
		self.assertEqual(map_envia_tracking_status(10), "Lost")
		self.assertEqual(map_envia_tracking_status("Delivered"), "Delivered")
		self.assertEqual(map_envia_tracking_status("lost"), "Lost")
		self.assertEqual(map_envia_tracking_status("unknown"), "In Progress")
		self.assertEqual(map_envia_tracking_status(None), "In Progress")

	def test_extract_envia_error(self):
		self.assertEqual(
			EnviaUtils._extract_envia_error({"error": {"message": "invalid payload"}}), "invalid payload"
		)
		self.assertEqual(
			EnviaUtils._extract_envia_error({"meta": "error", "error": "unauthorized"}), "unauthorized"
		)
		self.assertEqual(
			EnviaUtils._extract_envia_error({"meta": "error", "error": {"code": 102}}),
			"Envia error code 102",
		)
		self.assertEqual(EnviaUtils._extract_envia_error({"error": "something else"}), "something else")
		self.assertIsNone(EnviaUtils._extract_envia_error({"data": []}))
		self.assertIsNone(EnviaUtils._extract_envia_error("not a dict"))

	def test_build_address(self):
		address_dict = self.utils.build_address(
			self.pickup_address_dict, self.pickup_contact_dict, self.company.name
		)

		self.assertEqual(address_dict["name"], "Pickup Contact")
		self.assertEqual(address_dict["company"], self.company.name)
		self.assertEqual(address_dict["email"], "randomme@email.com")
		self.assertEqual(address_dict["street"], self.pickup_address.address_line1)
		self.assertEqual(address_dict["country"], "IN")
		self.assertEqual(address_dict["phone"], "+919876543210")
		self.assertEqual(address_dict["postalCode"], "641605")

	def test_build_packages(self):
		parcels = [
			{"length": 5, "width": 5, "height": 5, "weight": 5, "count": 2},
			{"length": 10, "width": 8, "height": 6, "weight": 3, "count": 1},
		]
		packages = self.utils.build_packages(
			parcels, total_weight=13, value_of_goods=100.5, description_of_content="Test Box"
		)

		self.assertEqual(len(packages), 2)
		self.assertEqual(packages[0]["content"], "Test Box")
		self.assertEqual(packages[0]["amount"], 2)
		self.assertEqual(packages[0]["weight"], 5)
		self.assertEqual(packages[0]["insurance"], 100)
		self.assertEqual(packages[0]["declaredValue"], 100)
		self.assertEqual(packages[0]["dimensions"], {"length": 5, "width": 5, "height": 5})
		self.assertEqual(packages[1]["dimensions"], {"length": 10, "width": 8, "height": 6})

	def test_get_available_services(self):
		services = self.utils.get_available_services(
			pickup_address=self.pickup_address_dict,
			delivery_address=self.delivery_address_dict,
			parcels=[{"length": 30, "width": 20, "height": 10, "weight": 2.5, "count": 1}],
			pickup_contact=self.pickup_contact_dict,
			delivery_contact=self.delivery_contact_dict,
			value_of_goods=1200,
			total_weight=2.5,
			pickup_company=self.company.name,
			description_of_content="Integration test",
		)

		self.assertIsInstance(services, list)
		if services:
			self.assertIn("carrier", services[0])
			self.assertIn("service_name", services[0])
			self.assertIn("total_price", services[0])

	def test_create_shipment_and_tracking_live(self):
		services = self.utils.get_available_services(
			pickup_address=self.pickup_address_dict,
			delivery_address=self.delivery_address_dict,
			parcels=[{"length": 30, "width": 20, "height": 10, "weight": 2.5, "count": 1}],
			pickup_contact=self.pickup_contact_dict,
			delivery_contact=self.delivery_contact_dict,
			value_of_goods=1200,
			total_weight=2.5,
			pickup_company=self.company.name,
			description_of_content="Integration test",
		)

		if not services:
			self.skipTest("No services available in Envia sandbox for this route.")

		service_info = services[0]
		shipment_data = self.utils.create_shipment(
			description_of_content="Integration test",
			pickup_company=self.company.name,
			pickup_address=self.pickup_address_dict,
			pickup_contact=self.pickup_contact_dict,
			delivery_address=self.delivery_address_dict,
			delivery_contact=self.delivery_contact_dict,
			delivery_company_name="Delivery Company",
			shipment_parcel=json.dumps(
				[{"length": 30, "width": 20, "height": 10, "weight": 2.5, "count": 1}]
			),
			total_weight=2.5,
			value_of_goods=1200,
			service_info={"carrier": service_info["carrier"], "service_id": service_info["service_id"]},
			shipment="SHP-ENVIA-LIVE-TEST",
		)

		self.assertEqual(shipment_data["service_provider"], ENVIA_PROVIDER)
		self.assertTrue(shipment_data["shipment_id"])
		self.assertTrue(shipment_data["awb_number"])

		tracking_data = self.utils.get_tracking_data(shipment_data["awb_number"])
		self.assertEqual(tracking_data["awb_number"], shipment_data["awb_number"])
		self.assertIn("tracking_status", tracking_data)


def get_live_envia_settings():
	name = frappe.db.get_value("Envia", {"enabled": 1, "sandbox": 1}, "name")
	if not name:
		return None
	return frappe.get_doc("Envia", name)


def create_shipment_address(address_title, company_name, postal_code, city="Random City", state=None):
	addresses = frappe.get_all("Address", filters={"address_title": address_title}, limit=1)
	if addresses:
		address = frappe.get_doc("Address", addresses[0].name)
	else:
		address = frappe.new_doc("Address")

	address.address_title = address_title
	address.address_type = "Shipping"
	address.address_line1 = company_name + " address line 1"
	address.address_line2 = company_name + " address line 2"
	address.number = "123"
	address.city = city
	address.state = state
	address.pincode = postal_code
	address.country = "India"
	if address.is_new():
		address.insert(ignore_permissions=True)
	else:
		address.save(ignore_permissions=True)
	return address


def get_address_dict(address, country_code):
	address_dict = address.as_dict()
	address_dict.country_code = country_code
	return address_dict


def create_customer_contact(fname, lname, phone, email="randomme@email.com"):
	contacts = frappe.get_all("Contact", filters={"first_name": fname, "last_name": lname}, limit=1)
	if contacts:
		customer = frappe.get_doc("Contact", contacts[0].name)
	else:
		customer = frappe.new_doc("Contact")

	customer.first_name = fname
	customer.last_name = lname
	customer.is_primary_contact = 1
	customer.set("email_ids", [])
	customer.set("phone_nos", [])
	customer.append("email_ids", {"email_id": email, "is_primary": 1})
	customer.append("phone_nos", {"phone": phone, "is_primary_phone": 1, "is_primary_mobile_no": 1})
	customer.status = "Passive"
	if customer.is_new():
		customer.insert(ignore_permissions=True)
	else:
		customer.save(ignore_permissions=True)
	return customer
