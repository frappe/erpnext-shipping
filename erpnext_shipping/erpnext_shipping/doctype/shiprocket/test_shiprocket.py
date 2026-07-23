# Copyright (c) 2025, Frappe and Contributors
# See license.txt

import frappe

from erpnext.tests.utils import ERPNextTestSuite
from erpnext_shipping.erpnext_shipping.doctype.shiprocket.shiprocket import (
	ShiprocketUtils,
)


class TestShiprocket(ERPNextTestSuite):
	def setUp(self):
		self.shiprocket_settings = get_live_shiprocket_settings()
		if not self.shiprocket_settings:
			self.skipTest("No enabled Shiprocket record found.")

		self.company = frappe.get_doc(
			"Company",
			self.shiprocket_settings.company,
		)

		self.pickup_address = create_shipment_address(
			"Shiprocket Pickup",
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
			"Shiprocket Delivery",
			"Delivery Company",
			"600001",
			city="Chennai",
			state="Tamil Nadu",
		)
		self.delivery_address_dict = get_address_dict(
			self.delivery_address,
			"IN",
		)

		self.pickup_contact = create_customer_contact(
			"Pickup",
			"Contact",
			phone="+919876543210",
			email="pickup@example.com",
		)
		self.pickup_contact_dict = self.pickup_contact.as_dict()

		self.delivery_contact = create_customer_contact(
			"Delivery",
			"Contact",
			phone="+919876543211",
			email="delivery@example.com",
		)
		self.delivery_contact_dict = self.delivery_contact.as_dict()

		self.utils = ShiprocketUtils(company=self.company.name)

	def test_generate_token(self):
		self.utils.generate_token()
		self.assertTrue(self.utils.bearer_token)

	def test_get_headers(self):
		headers = self.utils.get_headers()

		self.assertEqual(headers["Content-Type"], "application/json")
		self.assertTrue(headers["Authorization"].startswith("Bearer "))

	def test_get_available_services(self):
		services = self.utils.get_available_services(
			pickup_address=self.pickup_address_dict,
			delivery_address=self.delivery_address_dict,
			parcels=[
				{
					"length": 30,
					"width": 20,
					"height": 10,
					"weight": 2,
					"count": 1,
				}
			],
			pickup_contact=self.pickup_contact_dict,
			delivery_contact=self.delivery_contact_dict,
			value_of_goods=1000,
			total_weight=2,
			pickup_company=self.company.name,
			description_of_content="Integration Test",
		)

		self.assertIsInstance(services, list)

		if services:
			self.assertIn("carrier", services[0])
			self.assertIn("service_name", services[0])
			self.assertIn("total_price", services[0])


def get_live_shiprocket_settings():
	name = frappe.db.get_value(
		"Shiprocket",
		{"enabled": 1},
		"name",
	)

	if not name:
		return None

	return frappe.get_doc("Shiprocket", name)


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


def create_customer_contact(
	fname,
	lname,
	phone,
	email,
):
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
