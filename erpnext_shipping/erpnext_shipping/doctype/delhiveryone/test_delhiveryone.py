# Copyright (c) 2025, Frappe and Contributors
# See license.txt

import frappe
from erpnext.tests.utils import ERPNextTestSuite

from erpnext_shipping.erpnext_shipping.doctype.delhiveryone.delhiveryone import (
	DelhiveryOneUtils,
)


class TestDelhiveryOne(ERPNextTestSuite):
	def setUp(self):
		self.delhivery_settings = get_live_delhivery_settings()
		if not self.delhivery_settings:
			self.skipTest("No enabled Delhiveryone record found.")

		self.company = frappe.get_doc(
			"Company",
			self.delhivery_settings.company,
		)

		self.pickup_address = create_shipment_address(
			"Delhivery Pickup",
			self.company.name,
			"641605",
			city="Tiruppur",
			state="Tamil Nadu",
		)
		self.pickup_address_dict = get_address_dict(
			self.pickup_address,
			"IN",
		)

		self.delivery_address = create_shipment_address(
			"Delhivery Delivery",
			"Delivery Company",
			"600001",
			city="Chennai",
			state="Tamil Nadu",
		)
		self.delivery_address_dict = get_address_dict(
			self.delivery_address,
			"IN",
		)

		self.utils = DelhiveryOneUtils(company=self.company.name)

	def test_get_common_headers(self):
		headers = self.utils.get_common_headers()

		self.assertEqual(headers["Content-Type"], "application/json")
		self.assertTrue(headers["Authorization"].startswith("Token "))

	def test_get_availability(self):
		available = self.utils.get_availability(self.pickup_address_dict.pincode)

		self.assertIsInstance(available, bool)

	def test_get_available_services(self):
		services = self.utils.get_available_services(
			pickup_address=self.pickup_address_dict,
			delivery_address=self.delivery_address_dict,
			weight=2,
		)

		self.assertIsInstance(services, list)

		if services:
			self.assertIn("carrier", services[0])
			self.assertIn("service_name", services[0])
			self.assertIn("total_price", services[0])

	def test_get_service_dict(self):
		service = {
			"S": [
				{
					"total_amount": 120.50,
				}
			]
		}

		result = self.utils.get_service_dict(service)

		self.assertEqual(result.service_provider, "Delhiveryone")
		self.assertEqual(result.carrier, "Delhiveryone")
		self.assertEqual(result.service_name, "Surface")
		self.assertEqual(result.total_price, 120.50)
		self.assertEqual(result.currency, "INR")

	def test_get_service_dict_empty(self):
		self.assertIsNone(self.utils.get_service_dict({"S": []}))

	def test_get_parcel_dict(self):
		parcel = {
			"count": 1,
			"width": 10,
			"height": 12,
			"weight": 2,
		}

		service = {
			"service_name": "Surface",
		}

		contact = frappe._dict(
			first_name="Test",
			last_name="User",
			phone="+919876543210",
		)

		result = self.utils.get_parcel_dict(
			"SHIP-0001",
			parcel,
			1,
			self.delivery_address_dict,
			contact,
			service,
		)

		self.assertEqual(result["order"], "SHIP-0001-1")
		self.assertEqual(result["shipping_mode"], "Surface")
		self.assertEqual(result["name"], "Test User")
		self.assertEqual(result["quantity"], 1)

	def test_get_tracking_data(self):
		shipment_id = frappe.conf.get("delhivery_test_awb")

		if not shipment_id:
			self.skipTest("No Delhivery test AWB configured.")

		tracking = self.utils.get_tracking_data(shipment_id)

		self.assertIsInstance(tracking, dict)
		self.assertIn("awb_number", tracking)
		self.assertIn("tracking_status", tracking)
		self.assertIn("tracking_status_info", tracking)
		self.assertIn("tracking_url", tracking)

		self.assertIn(
			tracking["tracking_status"],
			[
				"In Progress",
				"Delivered",
				"Returned",
				"Cancelled",
				"Lost",
			],
		)


def get_live_delhivery_settings():
	name = frappe.db.get_value(
		"Delhiveryone",
		{"enabled": 1},
		"name",
	)

	if not name:
		return None

	return frappe.get_doc("Delhiveryone", name)


def create_shipment_address(
	address_title,
	company_name,
	postal_code,
	city="Random City",
	state=None,
):
	addresses = frappe.get_all(
		"Address",
		filters={"address_title": address_title},
		limit=1,
	)

	if addresses:
		address = frappe.get_doc("Address", addresses[0].name)
	else:
		address = frappe.new_doc("Address")

	address.address_title = address_title
	address.address_type = "Shipping"
	address.address_line1 = company_name + " Address Line 1"
	address.address_line2 = company_name + " Address Line 2"
	address.city = city
	address.state = state
	address.country = "India"
	address.pincode = postal_code

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
	contacts = frappe.get_all(
		"Contact",
		filters={
			"first_name": fname,
			"last_name": lname,
		},
		limit=1,
	)

	if contacts:
		contact = frappe.get_doc("Contact", contacts[0].name)
	else:
		contact = frappe.new_doc("Contact")

	contact.first_name = fname
	contact.last_name = lname
	contact.is_primary_contact = 1

	contact.set("email_ids", [])
	contact.set("phone_nos", [])

	contact.append(
		"email_ids",
		{
			"email_id": email,
			"is_primary": 1,
		},
	)

	contact.append(
		"phone_nos",
		{
			"phone": phone,
			"is_primary_phone": 1,
			"is_primary_mobile_no": 1,
		},
	)

	if contact.is_new():
		contact.insert(ignore_permissions=True)
	else:
		contact.save(ignore_permissions=True)

	return contact
