# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Starshipit tests. All HTTP calls are mocked; nothing reaches Starshipit.

Request and response shapes follow https://support.starshipit.com/developers/api-reference/starshipit
"""

import base64
import hashlib
import hmac
import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase
from frappe.utils import getdate

from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import (
	STARSHIPIT_PROVIDER,
	StarshipitUtils,
	erpnext_tracking_status,
	kg_per_unit,
	local_to_utc,
	normalize_status,
	order_carrier,
	split_service_id,
	status_dict,
	sync_rows,
	tracking_result,
	valid_signature,
)

# The settings link to Shipment Parcel Template; its sample records need a full ERPNext test setup
# and are not used here.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Shipment Parcel Template", "Address", "User"]

MODULE = "erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit"
REQUESTS = MODULE + ".requests.request"
API = "https://api.starshipit.com/api"


_original_system_setting = frappe.get_system_settings


def _system_setting(key):
	return _original_system_setting(key)


def response(payload, status=200):
	mock = MagicMock()
	mock.status_code = status
	mock.ok = 200 <= status < 300
	mock.json.return_value = payload
	mock.headers = {}
	return mock


def not_found():
	return response({"success": False, "errors": [{"message": "Order not found"}]}, status=404)


def no_order():
	return response({"orders": [], "total_pages": 0, "success": True})


def found(order, number="SHIP-TEST-1"):
	"""Search Orders finding the order, then Get Order with its details."""
	return [
		response(
			{
				"orders": [{"order_id": order["order_id"], "order_number": number, "archived": False}],
				"success": True,
			}
		),
		response({"order": order, "success": True}),
	]


def address(**values):
	data = {
		"address_title": "Test",
		"address_line1": "1 Queen Street",
		"address_line2": "Auckland Central",
		"city": "Auckland",
		"state": "",
		"pincode": "1010",
		"country": "New Zealand",
		"country_code": "nz",
	}
	data.update(values)
	return frappe._dict(data)


def calls(request):
	return [(c.args[0], c.args[1].replace(API, "")) for c in request.call_args_list]


class TestStarshipitHelpers(UnitTestCase):
	def test_normalize_status(self):
		self.assertEqual(normalize_status("InTransit"), "In Transit")
		self.assertEqual(normalize_status("OutForDelivery"), "Out For Delivery")
		self.assertEqual(normalize_status(None), "")

	def test_tracking_status_uses_shipment_options(self):
		# Shipment.tracking_status only allows "", In Progress, Delivered, Returned, Lost
		self.assertEqual(erpnext_tracking_status("Printed"), "In Progress")
		self.assertEqual(erpnext_tracking_status("InTransit"), "In Progress")
		self.assertEqual(erpnext_tracking_status("Delivered"), "Delivered")
		self.assertEqual(erpnext_tracking_status(""), "")
		self.assertEqual(erpnext_tracking_status("Saved"), "")  # no label yet
		self.assertEqual(erpnext_tracking_status("Unshipped"), "")
		self.assertEqual(erpnext_tracking_status("ReturnedToSender"), "Returned")
		self.assertEqual(erpnext_tracking_status("ReturnLabel"), "Returned")
		self.assertEqual(erpnext_tracking_status("Lost"), "Lost")
		self.assertEqual(erpnext_tracking_status("Exception"), "In Progress")

	def test_status_from_order_and_tracking(self):
		printed = status_dict({"status": "Printed", "packages": [{"tracking_number": None}]})
		self.assertEqual(
			(printed["tracking_status"], printed["tracking_status_info"]), ("In Progress", "Printed")
		)
		manifested = status_dict({"status": "Printed", "manifested": True})
		self.assertEqual(manifested["tracking_status_info"], "Manifested")
		delivered = status_dict(
			{"status": "Printed"},
			{
				"tracking_number": "TN1",
				"tracking_status": "Delivered",
				"tracking_url": "https://t/TN1",
				"tracking_events": [{"details": "Signed for"}],
			},
		)
		self.assertEqual(delivered["tracking_status"], "Delivered")
		self.assertEqual(delivered["tracking_status_info"], "Delivered: Signed for")
		self.assertEqual((delivered["awb_number"], delivered["tracking_url"]), ("TN1", "https://t/TN1"))

	def test_cancelled_is_not_in_transit(self):
		self.assertEqual(erpnext_tracking_status("Cancelled"), "")
		self.assertEqual(erpnext_tracking_status("Exception"), "In Progress")

	def test_sync_rows_reuses_ids_and_deletes_leftovers(self):
		# Update Order adds rows sent without an id, so a retry must reuse the existing ids
		existing = [{"package_id": 1, "weight": 0.5}, {"package_id": 2, "weight": 0.7}]
		self.assertEqual(
			sync_rows([{"weight": 0.9}], existing, "package_id"),
			[{"weight": 0.9, "package_id": 1}, {"package_id": 2, "delete": True}],
		)
		self.assertEqual(
			sync_rows([{"weight": 0.9}, {"weight": 1.1}], existing[:1], "package_id"),
			[{"weight": 0.9, "package_id": 1}, {"weight": 1.1}],
		)
		self.assertEqual(sync_rows([{"weight": 1}], None, "package_id"), [{"weight": 1}])

	def test_tracking_result_object_or_list(self):
		# /track documents "results" as a list but returns a single object
		self.assertEqual(
			tracking_result({"tracking_status": "Delivered"}, "S1"), {"tracking_status": "Delivered"}
		)
		results = [
			{"order_number": "S2", "tracking_status": "Printed"},
			{"order_number": "S1", "tracking_status": "InTransit"},
		]
		self.assertEqual(tracking_result(results, "S1")["tracking_status"], "InTransit")
		self.assertEqual(tracking_result(None, "S1"), {})

	def test_order_carrier_unset_is_none(self):
		self.assertIsNone(order_carrier({"carrier": -1}))
		self.assertIsNone(order_carrier({}))
		self.assertEqual(order_carrier({"carrier": "CourierPost"}), "CourierPost")

	def test_tracking_url_from_packages(self):
		order = {
			"status": "Printed",
			"packages": [{"tracking_number": "T1", "tracking_url": "https://track/T1"}],
		}
		self.assertEqual(status_dict(order)["tracking_url"], "https://track/T1")
		order["packages"][0]["tracking_url"] = "not-a-url"
		self.assertEqual(status_dict(order)["tracking_url"], "")

	def test_split_service_id(self):
		self.assertEqual(split_service_id({"service_id": "NZPost|CPOLP"}), ("NZPost", "CPOLP"))

	def test_kg_per_unit(self):
		self.assertEqual(kg_per_unit(0.25, "Kg"), 0.25)
		self.assertEqual(kg_per_unit(250, "Gram"), 0.25)
		self.assertIsNone(kg_per_unit(1, "Nos"))
		self.assertIsNone(kg_per_unit(0, "Kg"))

	def test_webhook_signature(self):
		body = b'{"order_number":"SHIP-1","tracking_status":"Delivered"}'
		signature = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
		self.assertTrue(valid_signature(body, signature, "s3cret"))
		self.assertTrue(valid_signature(body, signature.upper(), "s3cret"))
		self.assertFalse(valid_signature(body, signature, "other"))
		self.assertFalse(valid_signature(body, None, "s3cret"))

	def test_pickup_window_to_utc(self):
		# NZ daylight time (UTC+13) in November
		self.assertEqual(local_to_utc("2026-11-10", "09:00:00", "Pacific/Auckland"), "2026-11-09T20:00:00Z")


class TestStarshipit(IntegrationTestCase):
	def setUp(self):
		settings = frappe.get_single("Starshipit")
		settings.enabled = 1
		settings.api_key = "test-key"
		settings.subscription_key = "test-subscription"
		settings.allow_label_printing = 0
		settings.default_parcel_template = None
		settings.webhook_secret = None
		settings.save()
		sleep = patch(MODULE + ".time.sleep")
		sleep.start()
		self.addCleanup(sleep.stop)
		# requests in a test needn't wait their turn for the API's rate limit
		pacing = patch(MODULE + ".MIN_REQUEST_INTERVAL", 0.001)
		pacing.start()
		self.addCleanup(pacing.stop)

	def tearDown(self):
		frappe.db.set_single_value("Starshipit", "enabled", 0)

	def shipment(self, **values):
		# a real (unsaved) Shipment, so the code can only use fields Shipment actually has
		doc = frappe.new_doc("Shipment")
		doc.name = values.pop("name", "SHIP-TEST-1")
		doc.pickup_from_type = "Company"
		doc.pickup_company = frappe.db.get_value("Company", {}, "name")
		doc.update(values)
		return doc

	def create(self, request, shipment):
		original_get_doc = frappe.get_doc

		def get_doc(*args, **kwargs):
			if args and args[0] == "Shipment":
				return shipment
			return original_get_doc(*args, **kwargs)

		with patch("frappe.get_doc", side_effect=get_doc):
			return StarshipitUtils().create_shipment(
				shipment=shipment.name,
				pickup_address=address(),
				pickup_contact=frappe._dict(
					first_name="Sam", last_name="Taylor", email_id="a@b.nz", phone="+6421"
				),
				delivery_address=address(address_line1="5 High St", city="Dunedin", pincode="9016"),
				delivery_contact=frappe._dict(first_name="Ann", last_name="Lee", email_id="ann@example.com"),
				delivery_company_name="Lee Farms",
				shipment_parcel=json.dumps(
					[{"length": 20, "width": 15, "height": 10, "weight": 0.8, "count": 1}]
				),
				description_of_content="IoT controller",
				value_of_goods=250,
				service_info={
					"service_id": "PlainLabel|Plain Label",
					"service_name": "Plain Label - My Carrier",
					"carrier": "Plain Label",
					"total_price": 0,
				},
			)

	def test_webhook_url_has_token(self):
		settings = frappe.get_single("Starshipit")
		self.assertIn(settings.webhook_token, settings.webhook_url)

	@patch(REQUESTS)
	def test_rates_from_delivery_services_in_metres(self, request):
		request.return_value = response(
			{
				"services": [
					{
						"carrier": "NZPost",
						"carrier_name": "NZ Post",
						"service_name": "Overnight",
						"service_code": "CPOLP",
						"total_price": 9.5,
					},
					{
						"carrier": "NZPost",
						"carrier_name": "NZ Post",
						"service_name": "Economy",
						"service_code": "CPOLE",
						"total_price": 7.2,
					},
				],
				"success": True,
			}
		)
		rates = StarshipitUtils().get_available_services(
			address(),
			address(city="Christchurch", pincode="8011"),
			[{"length": 30, "width": 20, "height": 10, "weight": 1.2, "count": 2}],
			value_of_goods=150,
		)
		self.assertEqual(calls(request), [("POST", "/deliveryservices")])
		payload = request.call_args.kwargs["json"]
		headers = request.call_args.kwargs["headers"]
		self.assertEqual(headers["StarShipIT-Api-Key"], "test-key")
		self.assertEqual(headers["Ocp-Apim-Subscription-Key"], "test-subscription")
		self.assertEqual(payload["destination"]["country_code"], "NZ")
		self.assertEqual(len(payload["packages"]), 2)
		self.assertEqual(
			payload["packages"][0],
			{"name": "Parcel 1", "weight": 1.2, "length": 0.3, "width": 0.2, "height": 0.1},
		)
		self.assertEqual([r.service_name for r in rates], ["Economy", "Overnight"])
		self.assertEqual((rates[0].service_id, rates[0].carrier), ("NZPost|CPOLE", "NZ Post"))

	@patch(REQUESTS)
	def test_api_errors_are_shown(self, request):
		request.return_value = response(
			{"success": False, "errors": [{"message": "Invalid post code", "details": "8011x"}]}, status=400
		)
		frappe.clear_messages()
		# Desk keeps the other providers' rates: Starshipit has none, and says why
		self.assertEqual(StarshipitUtils().get_available_services(address(), address(), [{"weight": 1}]), [])
		self.assertIn("Invalid post code", str(frappe.message_log))
		frappe.clear_messages()
		with self.assertRaises(frappe.ValidationError) as context:
			StarshipitUtils().get_available_services(address(), address(), [{"weight": 1}], checkout=True)
		self.assertIn("Invalid post code", str(context.exception))

	@patch(REQUESTS)
	def test_create_order_then_set_courier(self, request):
		request.side_effect = [
			no_order(),
			response({"order": {"order_id": 987654}, "success": True}),
			response({"order": {"order_id": 987654, "carrier": "PlainLabel"}, "success": True}),
		]
		info = self.create(request, self.shipment())

		self.assertEqual(calls(request), [("GET", "/orders/search"), ("POST", "/orders"), ("PUT", "/orders")])
		lookup, create, set_courier = request.call_args_list
		self.assertEqual(
			lookup.kwargs["params"], {"phrase": "SHIP-TEST-1", "fields": "Order_Number", "status": "All"}
		)
		order = create.kwargs["json"]["order"]
		# Create Order - Order Model has no courier fields
		self.assertNotIn("carrier", order)
		self.assertNotIn("carrier_service_code", order)
		self.assertEqual(order["order_number"], "SHIP-TEST-1")
		self.assertEqual(order["shipping_method"], "Plain Label - My Carrier")
		self.assertEqual(order["destination"]["name"], "Ann Lee")
		self.assertEqual(order["destination"]["company"], "Lee Farms")
		self.assertEqual(order["sender_details"]["name"], "Sam Taylor")
		self.assertEqual(order["items"][0]["description"], "IoT controller")
		self.assertEqual(
			order["packages"],
			[{"name": "Parcel 1", "weight": 0.8, "length": 0.2, "width": 0.15, "height": 0.1}],
		)
		self.assertEqual(
			set_courier.kwargs["json"],
			{"order": {"order_id": 987654, "carrier": "PlainLabel", "carrier_service_code": "Plain Label"}},
		)
		self.assertEqual((info["shipment_id"], info["service_provider"]), ("987654", STARSHIPIT_PROVIDER))

	@patch(REQUESTS)
	def test_repeat_create_updates_instead_of_duplicating(self, request):
		existing = {
			"order_id": 555,
			"status": "Saved",
			"items": [{"item_id": 71, "sku": "OLD"}],
			"packages": [{"package_id": 81, "weight": 0.5}, {"package_id": 82, "weight": 0.5}],
		}
		request.side_effect = [*found(existing), response({"order": {"order_id": 555}, "success": True})]
		# booked before (its order id is on the Shipment), and booked again
		info = self.create(request, self.shipment(shipment_id="555"))
		# one update that replaces rows by id (rows without an id would be added) and sets the courier
		self.assertEqual(calls(request), [("GET", "/orders/search"), ("GET", "/orders"), ("PUT", "/orders")])
		order = request.call_args.kwargs["json"]["order"]
		self.assertEqual(
			(order["order_id"], order["carrier"], order["carrier_service_code"]),
			(555, "PlainLabel", "Plain Label"),
		)
		self.assertEqual([row.get("item_id") for row in order["items"]], [71])
		self.assertEqual(
			order["packages"],
			[
				{
					"name": "Parcel 1",
					"weight": 0.8,
					"length": 0.2,
					"width": 0.15,
					"height": 0.1,
					"package_id": 81,
				},
				{"package_id": 82, "delete": True},
			],
		)
		self.assertEqual(info["shipment_id"], "555")

	@patch(REQUESTS)
	def test_individual_customer_has_no_company(self, request):
		if not frappe.db.exists("Customer", "_Test Starshipit Person"):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": "_Test Starshipit Person",
					"customer_type": "Individual",
				}
			).insert(ignore_permissions=True)
		request.side_effect = [
			no_order(),
			response({"order": {"order_id": 9}, "success": True}),
			response({"order": {"order_id": 9}, "success": True}),
		]
		self.create(
			request, self.shipment(delivery_to_type="Customer", delivery_customer="_Test Starshipit Person")
		)
		order = request.call_args_list[1].kwargs["json"]["order"]
		self.assertEqual(order["destination"]["company"], "")
		self.assertEqual(order["destination"]["name"], "Ann Lee")

	def test_label_and_rates_need_shipment_permission(self):
		from erpnext_shipping.erpnext_shipping.shipping import fetch_shipping_rates, print_shipping_label

		shipment = self.shipment(service_provider=STARSHIPIT_PROVIDER, shipment_id="1")
		original_get_doc = frappe.get_doc
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, "Administrator")
		with (
			patch(
				"frappe.get_doc",
				side_effect=lambda *a, **k: (
					shipment if a and a[0] == "Shipment" else original_get_doc(*a, **k)
				),
			),
			patch(REQUESTS) as request,
		):
			with self.assertRaises(frappe.PermissionError):
				print_shipping_label("SHIP-TEST-1")
			with self.assertRaises(frappe.PermissionError):
				fetch_shipping_rates("Company", "Customer", "A", "B", "[]", "x", "2026-11-10", 1)
			request.assert_not_called()

	@patch(REQUESTS)
	def test_repeat_create_leaves_printed_order_alone(self, request):
		printed = {
			"order_id": 556,
			"status": "Printed",
			"carrier": "PlainLabel",
			"carrier_service_code": "Plain Label",
		}
		request.side_effect = found(printed)
		info = self.create(request, self.shipment(shipment_id="556"))
		self.assertEqual(calls(request), [("GET", "/orders/search"), ("GET", "/orders")])
		self.assertEqual(info["shipment_id"], "556")
		# another service can't be set once the label is printed
		request.side_effect = found(dict(printed, carrier_service_code="Express"))
		with self.assertRaises(frappe.ValidationError):
			self.create(request, self.shipment(shipment_id="556"))

	@patch(REQUESTS)
	def test_another_order_with_the_same_number_is_never_taken_over(self, request):
		# a delivered order of another site (or of a Shipment whose number was used again)
		theirs = {"order_id": 900, "status": "Delivered", "destination": {"name": "Someone Else"}}
		request.side_effect = found(theirs)
		with self.assertRaises(frappe.ValidationError):
			self.create(request, self.shipment())
		self.assertNotIn(("PUT", "/orders"), calls(request))

	def test_an_order_a_timed_out_booking_made_is_the_shipments_own(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import own_order

		ours = {"reference": "DN-1", "destination": {"name": "Ann", "street": "1 Road", "post_code": "9016"}}
		just_made = dict(ours, order_id=901, status="Saved")
		self.assertTrue(own_order(self.shipment(), just_made, ours))
		elsewhere = dict(just_made, destination=dict(ours["destination"], street="2 Other Road"))
		self.assertFalse(own_order(self.shipment(), elsewhere, ours))
		self.assertFalse(own_order(self.shipment(), dict(just_made, status="Printed"), ours))
		self.assertTrue(own_order(self.shipment(shipment_id="901"), dict(just_made, status="Printed"), ours))

	@patch(REQUESTS)
	def test_a_failed_lookup_is_not_taken_for_a_missing_order(self, request):
		request.side_effect = [response({"success": False}, status=503)]
		with self.assertRaises(frappe.ValidationError):
			self.create(request, self.shipment())
		self.assertEqual(calls(request), [("GET", "/orders/search")])

	@patch(REQUESTS)
	def test_an_archived_order_is_sent_again(self, request):
		request.side_effect = [
			response(
				{
					"orders": [{"order_id": 77, "order_number": "SHIP-TEST-1", "archived": True}],
					"success": True,
				}
			),
			response({"order": {"order_id": 78}, "success": True}),
			response({"order": {"order_id": 78}, "success": True}),
		]
		info = self.create(request, self.shipment())
		self.assertEqual(calls(request), [("GET", "/orders/search"), ("POST", "/orders"), ("PUT", "/orders")])
		self.assertEqual(info["shipment_id"], "78")

	@patch(REQUESTS)
	def test_label_refused_unless_allowed(self, request):
		request.return_value = response({"order": {"order_id": 123, "status": "Saved"}, "success": True})
		with self.assertRaises(frappe.ValidationError):
			StarshipitUtils().print_label(self.shipment(shipment_id="123"))
		self.assertEqual(calls(request), [("GET", "/orders")])

	@patch(REQUESTS)
	def test_label_sends_the_order_courier(self, request):
		frappe.db.set_single_value("Starshipit", "allow_label_printing", 1)
		pdf = b"%PDF-1.4 label"
		request.side_effect = [
			response(
				{
					"order": {
						"order_id": 456,
						"status": "Saved",
						"carrier": "PlainLabel",
						"carrier_service_code": "Plain Label",
					},
					"success": True,
				}
			),
			response(
				{"labels": [base64.b64encode(pdf).decode()], "tracking_numbers": [None], "success": True}
			),
		]
		labels, tracking_numbers = StarshipitUtils().print_label(self.shipment(shipment_id="456"))
		self.assertEqual(calls(request), [("GET", "/orders"), ("POST", "/orders/shipment")])
		self.assertEqual(
			request.call_args.kwargs["json"],
			{
				"order_id": 456,
				"reprint": False,
				"carrier": "PlainLabel",
				"carrier_service_code": "Plain Label",
			},
		)
		self.assertEqual((labels, tracking_numbers), ([pdf], []))  # plain labels have no tracking number

	@patch(REQUESTS)
	def test_printed_order_is_reprinted_not_booked_again(self, request):
		request.side_effect = [
			response({"order": {"order_id": 789, "status": "Printed"}, "success": True}),
			response({"labels": [], "tracking_numbers": [], "success": True}),
		]
		StarshipitUtils().print_label(self.shipment(shipment_id="789"))
		self.assertEqual(request.call_args.kwargs["json"], {"order_id": 789, "reprint": True})

	@patch(REQUESTS)
	def test_a_label_printed_again_after_no_answer_is_never_bought_twice(self, request):
		import requests as http

		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import StarshipitNoAnswerError

		frappe.db.set_single_value("Starshipit", "allow_label_printing", 1)
		saved = {
			"order_id": 456,
			"status": "Saved",
			"carrier": "PlainLabel",
			"carrier_service_code": "Plain Label",
		}
		label = {
			"labels": [base64.b64encode(b"%PDF-1.4").decode()],
			"tracking_numbers": ["TRK1"],
			"success": True,
		}
		shipment = self.shipment(shipment_id="456")
		for status, expected in (
			# Starshipit printed it before its answer was lost: the label is printed again for free
			("Printed", {"order_id": 456, "reprint": True}),
			# it never printed: the label is bought now, once
			(
				"Saved",
				{
					"order_id": 456,
					"reprint": False,
					"carrier": "PlainLabel",
					"carrier_service_code": "Plain Label",
				},
			),
		):
			with self.subTest(status=status):
				request.reset_mock()
				request.side_effect = [response({"order": saved, "success": True}), http.Timeout()]
				with self.assertRaises(StarshipitNoAnswerError):
					StarshipitUtils().print_label(shipment)
				request.side_effect = [
					response({"order": dict(saved, status=status), "success": True}),
					response(label),
				]
				labels, numbers = StarshipitUtils().print_label(shipment)
				shipment_requests = [
					c for c in request.call_args_list if c.args[:2] == ("POST", f"{API}/orders/shipment")
				]
				self.assertEqual(len(shipment_requests), 2)
				self.assertEqual(shipment_requests[-1].kwargs["json"], expected)
				self.assertEqual((labels, numbers), ([b"%PDF-1.4"], ["TRK1"]))

	@patch(REQUESTS)
	def test_manifest(self, request):
		pdf = b"%PDF manifest"
		request.side_effect = [
			response({"order": {"order_id": 321, "status": "Printed"}, "success": True}),
			response(
				{"label_type": "ManifestReport", "pdf": base64.b64encode(pdf).decode(), "success": True}
			),
		]
		self.assertEqual(StarshipitUtils().manifest(self.shipment(shipment_id="321")), pdf)
		self.assertEqual(calls(request)[-1], ("POST", "/orders/manifest"))
		self.assertEqual(request.call_args.kwargs["json"], {"order_ids": [321]})

	@patch(REQUESTS)
	def test_manifest_only_once(self, request):
		request.return_value = response(
			{"order": {"order_id": 323, "status": "Printed", "manifested": True}, "success": True}
		)
		with self.assertRaises(frappe.ValidationError):
			StarshipitUtils().manifest(self.shipment(shipment_id="323"))
		self.assertNotIn(("POST", "/orders/manifest"), calls(request))

	@patch(REQUESTS)
	def test_manifest_needs_a_label(self, request):
		request.return_value = response({"order": {"order_id": 322, "status": "Saved"}, "success": True})
		with self.assertRaises(frappe.ValidationError):
			StarshipitUtils().manifest(self.shipment(shipment_id="322"))
		self.assertNotIn(("POST", "/orders/manifest"), calls(request))

	@patch(REQUESTS)
	def test_commercial_invoice(self, request):
		pdf = b"%PDF invoice"
		request.return_value = response(
			{
				"invoiceList": [{"order_id": 111, "invoice_base64_string": base64.b64encode(pdf).decode()}],
				"success": True,
			}
		)
		self.assertEqual(StarshipitUtils().commercial_invoices(self.shipment(shipment_id="111")), [pdf])
		self.assertEqual(request.call_args.kwargs["json"], {"order_ids": [111]})

	@patch(
		MODULE + ".frappe.get_system_settings",
		side_effect=lambda key=None: "Pacific/Auckland" if key == "time_zone" else _system_setting(key),
	)
	@patch(REQUESTS)
	def test_book_pickup(self, request, _zone):
		request.side_effect = [
			response(
				{
					"order": {
						"order_id": 654,
						"status": "Printed",
						"carrier": "Fastways",
						"carrier_name": "Aramex",
						"carrier_service_code": "P",
						"sender_details": {
							"name": "Sam",
							"street": "1 Queen Street",
							"city": "Auckland",
							"post_code": "1010",
							"country": "New Zealand",
						},
						"destination": {
							"name": "Ann",
							"street": "5 High St",
							"city": "Dunedin",
							"post_code": "9016",
							"country": "New Zealand",
						},
						"packages": [
							{"weight": 0.8, "length": 0.2, "width": 0.15, "height": 0.1},
							{"weight": 1.5, "length": 0.4, "width": 0.3, "height": 0.2},
						],
					},
					"success": True,
				}
			),
			response({"job_number": "4500", "success": True}),
		]
		shipment = self.shipment(
			shipment_id="654",
			awb_number="TN9",
			pickup_date="2026-11-10",
			pickup_from="09:00:00",
			pickup_to="12:00:00",
		)
		self.assertEqual(StarshipitUtils().book_pickup(shipment, "Side door"), "4500")
		self.assertEqual(calls(request)[-1], ("POST", "/pickup/book"))
		payload = request.call_args.kwargs["json"]
		# the courier's name, as Book Pickup's documented example has it (not the carrier code)
		self.assertEqual(payload["carrier_name"], "Aramex")
		self.assertNotIn("shipment_ids", payload)
		self.assertEqual(payload["tracking_number"], "TN9")
		self.assertEqual(payload["pickup_timeslot_start"], "2026-11-09T20:00:00Z")
		self.assertEqual(payload["pickup_timeslot_end"], "2026-11-09T23:00:00Z")
		self.assertEqual(payload["pickup_timezone_id"], "New Zealand Standard Time")
		self.assertEqual(payload["collection_address"]["address_line1"], "1 Queen Street")
		self.assertEqual(payload["package_count"], 2)
		self.assertEqual(payload["total_weight"], 2.3)
		# pickup sizes are in centimetres
		self.assertEqual(
			(payload["max_package_length"], payload["max_package_width"], payload["max_package_height"]),
			(40.0, 30.0, 20.0),
		)

	@patch(REQUESTS)
	def test_pickup_needs_a_label(self, request):
		request.return_value = response({"order": {"order_id": 655, "status": "Saved"}, "success": True})
		shipment = self.shipment(
			shipment_id="655", pickup_date="2026-11-10", pickup_from="09:00:00", pickup_to="12:00:00"
		)
		with self.assertRaises(frappe.ValidationError):
			StarshipitUtils().book_pickup(shipment)

	def test_pickup_times_are_in_the_pickup_places_time_zone(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import pickup_time_zone

		original = frappe.get_system_settings
		with patch(
			"frappe.get_system_settings",
			side_effect=lambda key=None: "Australia/Sydney" if key == "time_zone" else original(key),
		):
			# the setting first, then the system's zone when it is one of the country's
			self.assertEqual(pickup_time_zone("Australia/Perth", "Australia"), "Australia/Perth")
			self.assertEqual(pickup_time_zone(None, "Australia"), "Australia/Sydney")
			# a country with one time zone: its own, whatever the system's
			self.assertEqual(pickup_time_zone(None, "United Kingdom"), "Europe/London")
			self.assertEqual(pickup_time_zone(None, None), "Australia/Sydney")
		settings = frappe.get_single("Starshipit")
		settings.pickup_time_zone = "Mars/Olympus"
		with self.assertRaises(frappe.ValidationError):
			settings.save()

	def test_the_daily_tracking_update_carries_on_past_a_failing_shipment(self):
		from erpnext_shipping.erpnext_shipping.utils import update_tracking_info_daily

		shipments = [frappe._dict(name="SHIP-A"), frappe._dict(name="SHIP-B")]
		docs = {
			name: MagicMock(service_provider="Starshipit", shipment_id=name) for name in ("SHIP-A", "SHIP-B")
		}
		tracked = {
			"awb_number": "T1",
			"tracking_status": "In Progress",
			"tracking_status_info": "",
			"tracking_url": "",
		}

		def update_tracking(name, *args):
			if name == "SHIP-A":
				raise frappe.ValidationError("timed out")
			return tracked

		with (
			patch("frappe.get_all", return_value=shipments),
			patch("frappe.get_doc", side_effect=lambda doctype, name: docs[name]),
			patch("erpnext_shipping.erpnext_shipping.shipping.update_tracking", side_effect=update_tracking),
			patch("frappe.db.commit") as commit,
			patch("frappe.db.rollback") as rollback,
			patch("frappe.log_error") as log_error,
		):
			update_tracking_info_daily()
		rollback.assert_called_once()
		commit.assert_called_once()
		log_error.assert_called_once()
		docs["SHIP-B"].db_set.assert_any_call("awb_number", "T1")

	@patch(REQUESTS)
	def test_cancel_deletes_unprinted_and_archives_printed(self, request):
		request.return_value = response({"success": True})
		self.assertEqual(StarshipitUtils().cancel_order("123"), "deleted")
		self.assertEqual(calls(request)[-1], ("DELETE", "/orders/delete"))
		self.assertEqual(StarshipitUtils().cancel_order("123", printed=True), "archived")
		self.assertEqual(calls(request)[-1], ("POST", "/orders/archive"))
		self.assertEqual(request.call_args.kwargs["params"], {"order_id": "123"})

	@patch(REQUESTS)
	def test_cancel_when_the_order_is_already_gone(self, request):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import cancel_shipment_order

		shipment = self.shipment(service_provider=STARSHIPIT_PROVIDER, shipment_id="123")
		request.side_effect = [response({"success": False, "errors": [{"message": "Not found"}]}, status=404)]
		cancel_shipment_order(shipment)
		self.assertEqual(calls(request), [("GET", "/orders")])

		unshipped = {"order_id": 123, "status": "Unshipped"}
		request.side_effect = [response({"order": unshipped, "success": True}), response({"success": True})]
		cancel_shipment_order(shipment)
		# nothing is removed until the cancel is committed
		self.assertNotIn(("DELETE", "/orders/delete"), calls(request))
		shipment.add_comment = MagicMock()
		with patch("frappe.get_doc", return_value=shipment), patch("frappe.db.commit"):
			frappe.db.after_commit.run()
		self.assertEqual(calls(request)[-1], ("DELETE", "/orders/delete"))
		self.assertIn("deleted", shipment.add_comment.call_args.args[1])

	@patch(REQUESTS)
	def test_a_cancel_stops_when_starshipit_does_not_answer(self, request):
		import requests as http

		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import cancel_shipment_order

		shipment = self.shipment(service_provider=STARSHIPIT_PROVIDER, shipment_id="123")
		request.side_effect = http.Timeout()
		with self.assertRaises(frappe.ValidationError):
			cancel_shipment_order(shipment)
		frappe.db.after_commit.reset()

	@patch(REQUESTS)
	def test_a_rolled_back_cancel_leaves_the_starshipit_order(self, request):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import cancel_shipment_order

		shipment = self.shipment(service_provider=STARSHIPIT_PROVIDER, shipment_id="123")
		unshipped = {"order_id": 123, "status": "Unshipped"}
		request.side_effect = [response({"order": unshipped, "success": True})]
		cancel_shipment_order(shipment)
		frappe.db.after_commit.reset()  # what a rollback does
		self.assertNotIn(("DELETE", "/orders/delete"), calls(request))
		self.assertNotIn(("POST", "/orders/archive"), calls(request))

	@patch(REQUESTS)
	def test_status_from_order_when_no_courier_tracking(self, request):
		request.side_effect = [
			*found({"order_id": 9, "status": "Printed", "manifested": True}),
			not_found(),
		]
		data = StarshipitUtils().get_tracking_data("SHIP-TEST-1")
		self.assertEqual(calls(request), [("GET", "/orders/search"), ("GET", "/orders"), ("GET", "/track")])
		self.assertEqual(
			(data["tracking_status"], data["tracking_status_info"]), ("In Progress", "Manifested")
		)

	@patch(REQUESTS)
	def test_rate_limit_is_retried_once(self, request):
		limited = response({}, status=429)
		limited.headers = {"Retry-After": "1"}
		request.side_effect = [limited, response({"orders": [], "success": True})]
		StarshipitUtils().request("GET", "/orders/unshipped")
		self.assertEqual(request.call_count, 2)

	def webhook_request(self, body: bytes, query: dict | None = None, headers: dict | None = None):
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		builder = EnvironBuilder(
			method="POST",
			path="/",
			query_string=query or {},
			data=body,
			headers=headers or {},
			content_type="application/json",
		)
		frappe.local.request = Request(builder.get_environ())
		self.addCleanup(lambda: hasattr(frappe.local, "request") and delattr(frappe.local, "request"))

	def test_webhook_token_in_url(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import webhook

		body = json.dumps({"order_number": "NO-SUCH-SHIPMENT", "tracking_status": "Dispatched"}).encode()
		self.webhook_request(body, {"token": "wrong"})
		with self.assertRaises(frappe.PermissionError):
			webhook()
		self.webhook_request(body, {"token": frappe.db.get_single_value("Starshipit", "webhook_token")})
		self.assertEqual(webhook(), {"ok": True, "matched": False})

	def test_webhook_signature_when_secret_set(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import webhook

		settings = frappe.get_single("Starshipit")
		settings.webhook_secret = "s3cret"
		settings.save()
		body = json.dumps({"order_number": "NO-SUCH-SHIPMENT", "tracking_status": "Dispatched"}).encode()
		token = frappe.db.get_single_value("Starshipit", "webhook_token")
		self.webhook_request(body, {"token": token})  # a token alone is not enough once a secret is set
		with self.assertRaises(frappe.PermissionError):
			webhook()
		signature = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
		self.webhook_request(body, headers={"X-Starshipit-Signature": signature})
		self.assertEqual(webhook(), {"ok": True, "matched": False})

	def test_an_older_webhook_event_is_dropped(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit import starshipit

		frappe.cache.delete_value("starshipit:last_event:SHIP-TEST-1")
		token = frappe.db.get_single_value("Starshipit", "webhook_token")

		def event(status, updated, stored="TRK1"):
			body = {
				"order_number": "SHIP-TEST-1",
				"tracking_number": "TRK1",
				"tracking_status": status,
				"last_updated_date": updated,
			}
			self.webhook_request(json.dumps(body).encode(), {"token": token})
			original_get_value = frappe.db.get_value

			def get_value(doctype, *a, **k):
				if doctype == "Shipment" and isinstance(a[0], dict):
					return "SHIP-TEST-1"
				if doctype == "Shipment" and a[:2] == ("SHIP-TEST-1", "awb_number"):
					return stored
				return original_get_value(doctype, *a, **k)

			with (
				patch("frappe.db.get_value", side_effect=get_value),
				patch.object(starshipit, "apply_status") as apply,
				patch("frappe.enqueue") as enqueue,
			):
				result = starshipit.webhook()
			return result, apply, enqueue

		result, apply, enqueue = event("InTransit", "2026-11-09T10:00:00Z")
		apply.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["job_id"], "starshipit-status-SHIP-TEST-1")
		result, apply, _enqueue = event("Printed", "2026-11-09T08:00:00Z")
		self.assertTrue(result.get("stale"))
		apply.assert_not_called()

		# before the Shipment has a tracking number, an event could be another site's order with the
		# same number: the Shipment's own order is read instead
		frappe.cache.delete_value("starshipit:last_event:SHIP-TEST-1")
		result, apply, enqueue = event("Delivered", "2026-11-09T12:00:00Z", stored=None)
		apply.assert_not_called()
		self.assertTrue(enqueue.call_args.args[0].endswith("starshipit.refresh_status"))

	def box(self, name, length, width, height, weight):
		if not frappe.db.exists("Shipment Parcel Template", name):
			frappe.get_doc(
				{
					"doctype": "Shipment Parcel Template",
					"parcel_template_name": name,
					"length": length,
					"width": width,
					"height": height,
					"weight": weight,
				}
			).insert(ignore_permissions=True)
		return name

	def test_parcels_for_items(self):
		from erpnext_shipping.erpnext_shipping.utils import parcels_for_items

		small = self.box("_Test Box Small", 20, 15, 10, 0.1)
		large = self.box("_Test Box Large", 40, 30, 20, 0.3)
		psu = self.box("_Test Box PSU", 30, 20, 15, 0.2)
		node = {"qty": 2, "kg": 0.25, "template": small, "own_box": 0}
		hat = {"qty": 3, "kg": 0.12, "template": large, "own_box": 0}
		power = {"qty": 2, "kg": 1.5, "template": psu, "own_box": 1}

		# items share the largest of their boxes; own-box items get one parcel per unit
		self.assertEqual(
			parcels_for_items([node, hat, power], None, small),
			[
				{"length": 40, "width": 30, "height": 20, "weight": 1.16, "count": 1},
				{"length": 30, "width": 20, "height": 15, "weight": 1.7, "count": 2},
			],
		)
		# the Shipment's Parcel Template wins for the shared parcel
		self.assertEqual(parcels_for_items([node], small, large)[0]["length"], 20)
		# no item box: Starshipit's default
		self.assertEqual(
			parcels_for_items([{"qty": 1, "kg": 0.5}], None, large),
			[{"length": 40, "width": 30, "height": 20, "weight": 0.8, "count": 1}],
		)
		# nothing to weigh in the shared parcel: no parcel (ERPNext refuses 0 kg parcels)
		self.assertEqual(parcels_for_items([{"qty": 1, "kg": 0}], None, large), [])
		# an own-box item without any weight can't be priced, so it is not left out quietly
		with self.assertRaises(frappe.ValidationError):
			parcels_for_items([{"item_code": "X", "qty": 1, "kg": 0, "own_box": 1}], None, None)

	def test_shared_items_fill_as_many_boxes_as_their_weight_needs(self):
		from erpnext_shipping.erpnext_shipping.utils import parcels_for_items

		satchel = self.box("_Test Satchel 5kg", 30, 25, 10, 0.5)
		frappe.db.set_value("Shipment Parcel Template", satchel, "max_weight", 5)
		frappe.clear_document_cache("Shipment Parcel Template", satchel)
		self.addCleanup(frappe.clear_document_cache, "Shipment Parcel Template", satchel)
		# nine 2 kg units in boxes holding 4.5 kg of items: four boxes of two, one of one
		self.assertEqual(
			parcels_for_items([{"qty": 9, "kg": 2}], None, satchel),
			[
				{"length": 30, "width": 25, "height": 10, "weight": 4.5, "count": 4},
				{"length": 30, "width": 25, "height": 10, "weight": 2.5, "count": 1},
			],
		)
		# a unit heavier than the box allows still goes, on its own
		self.assertEqual(parcels_for_items([{"qty": 1, "kg": 7}], None, satchel)[0]["weight"], 7.5)

	def test_parcels_are_filled_only_for_store_or_starshipit_shipments(self):
		from erpnext_shipping.erpnext_shipping.utils import fill_parcels_from_items

		shipment = self.shipment(service_provider="LetMeShip")
		shipment.append("shipment_delivery_note", {"delivery_note": "DN-TEST-1"})
		with patch("frappe.get_all", return_value=[]) as get_all:
			fill_parcels_from_items(shipment)
		get_all.assert_not_called()
		self.assertFalse(shipment.shipment_parcel)
		# an item that can't be weighed leaves the parcels to staff instead of blocking the save
		shipment.service_provider = STARSHIPIT_PROVIDER
		unweighed = frappe._dict(item_code="X", stock_qty=1, weight_per_unit=0, weight_uom=None)
		with (
			patch("frappe.get_all", return_value=[unweighed]),
			patch("frappe.get_cached_value", return_value=(None, 1)),
		):
			fill_parcels_from_items(shipment)
		self.assertFalse(shipment.shipment_parcel)

	def test_parcel_weight_keeps_grams(self):
		self.assertEqual(frappe.get_meta("Shipment Parcel").get_field("weight").precision, "3")
		column = frappe.db.sql("show columns from `tabShipment Parcel` like 'weight'")[0][1]
		self.assertEqual(column, "decimal(21,3)")


class TestStarshipitStore(IntegrationTestCase):
	"""Starshipit for the Shop app: checkout rates and order status."""

	@classmethod
	def setUpClass(cls):
		if "shop" not in frappe.get_installed_apps():
			raise unittest.SkipTest("Shop is not installed")
		super().setUpClass()

	def setUp(self):
		from erpnext_shipping.erpnext_shipping import shop_integration

		self.si = shop_integration
		settings = frappe.get_single("Starshipit")
		settings.enabled = 1
		settings.api_key = "test-key"
		settings.subscription_key = "test-subscription"
		settings.shop_checkout_rates = 1
		settings.checkout_services = ""
		settings.sender_address = self.sender()
		settings.save()
		frappe.clear_cache(doctype="Starshipit")
		frappe.cache.delete_keys("shipping_rates:")
		self.item = self.weighed_item("_Test Starshipit Store Item", 0.4)
		sleep = patch(MODULE + ".time.sleep")
		sleep.start()
		self.addCleanup(sleep.stop)
		pacing = patch(MODULE + ".MIN_REQUEST_INTERVAL", 0.001)
		pacing.start()
		self.addCleanup(pacing.stop)

	def tearDown(self):
		frappe.db.set_single_value("Starshipit", {"enabled": 0, "shop_checkout_rates": 0})
		frappe.clear_cache(doctype="Starshipit")

	def sender(self) -> str:
		name = "_Test Starshipit Sender-Shipping"
		if not frappe.db.exists("Address", name):
			frappe.get_doc(
				{
					"doctype": "Address",
					"address_title": "_Test Starshipit Sender",
					"address_type": "Shipping",
					"address_line1": "1 Queen Street",
					"city": "Auckland",
					"pincode": "1010",
					"country": "New Zealand",
				}
			).insert(ignore_permissions=True)
		return name

	def weighed_item(self, code: str, kg: float) -> str:
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": code,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"weight_per_unit": kg,
					"weight_uom": "Kg",
				}
			).insert(ignore_permissions=True)
		return code

	def context(self, country="New Zealand", code="nz", items=None):
		return frappe._dict(
			cart_items=items or [{"item_code": self.item, "qty": 2, "rate": 100}],
			address=frappe._dict(
				address_line1="5 High St",
				address_line2="",
				city="Dunedin",
				state="",
				pincode="9016",
				country=country,
				country_code=code.upper(),
			),
			subtotal=200,
			company=frappe.db.get_value("Company", {}, "name"),
			currency="NZD",
		)

	def test_switched_off_returns_nothing(self):
		frappe.db.set_single_value("Starshipit", "shop_checkout_rates", 0)
		frappe.clear_cache(doctype="Starshipit")
		self.assertIsNone(self.si.checkout_rates(self.context()))

	@patch(REQUESTS)
	def test_nothing_to_send_asks_nothing(self, request):
		if not frappe.db.exists("Item", "_Test Starshipit Service"):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": "_Test Starshipit Service",
					"item_name": "_Test Starshipit Service",
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 0,
				}
			).insert(ignore_permissions=True)
		items = [{"item_code": "_Test Starshipit Service", "qty": 1, "rate": 80}]
		self.assertIsNone(self.si.checkout_rates(self.context(items=items)))
		request.assert_not_called()

	@patch(REQUESTS)
	def test_rates_from_starshipit_are_cached(self, request):
		request.return_value = response(
			{
				"services": [
					{
						"carrier": "CourierPost",
						"carrier_name": "NZ Post Domestic",
						"service_name": "Parcel Post",
						"service_code": "CPOLP",
						"total_price": 9.5,
					},
					{
						"carrier": "CourierPost",
						"carrier_name": "NZ Post Domestic",
						"service_name": "Courier",
						"service_code": "CPOLE",
						"total_price": 12.0,
					},
				],
				"success": True,
			}
		)
		result = self.si.checkout_rates(self.context())
		self.assertEqual([o["amount"] for o in result["options"]], [9.5, 12.0])
		self.assertEqual(result["options"][0]["id"], "starshipit:CourierPost|CPOLP")
		self.assertEqual(result["options"][0]["label"], "NZ Post Domestic Parcel Post")
		package = request.call_args.kwargs["json"]["packages"][0]
		self.assertEqual(package["weight"], 0.8)  # two items of 0.4 kg, no box set
		self.si.checkout_rates(self.context())
		self.assertEqual(request.call_count, 1)  # the second quote came from the cache

	@patch(REQUESTS)
	def test_only_the_chosen_services_are_offered(self, request):
		frappe.db.set_single_value("Starshipit", "checkout_services", "CPOLE")
		frappe.clear_cache(doctype="Starshipit")
		request.return_value = response(
			{
				"services": [
					{
						"carrier": "CourierPost",
						"carrier_name": "NZ Post Domestic",
						"service_name": "Parcel Post",
						"service_code": "CPOLP",
						"total_price": 9.5,
					},
					{
						"carrier": "CourierPost",
						"carrier_name": "NZ Post Domestic",
						"service_name": "Courier",
						"service_code": "CPOLE",
						"total_price": 12.0,
					},
				],
				"success": True,
			}
		)
		options = self.si.checkout_rates(self.context())["options"]
		self.assertEqual([o["meta"]["service_code"] for o in options], ["CPOLE"])

	def test_a_late_event_does_not_move_a_delivered_parcel_back(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit import starshipit

		stored = frappe._dict(
			awb_number="TRACK1",
			tracking_url="https://track/1",
			tracking_status="Delivered",
			tracking_status_info="Delivered",
		)

		def get_value(doctype, name, fields, *args, **kwargs):
			return stored.tracking_status if fields == "tracking_status" else stored

		with (
			patch("frappe.db.get_value", side_effect=get_value),
			patch("frappe.db.set_value") as set_value,
			patch("frappe.get_all", return_value=["DN-TEST-1"]),
			patch("erpnext_shipping.erpnext_shipping.shipping.update_delivery_note") as update,
		):
			starshipit.apply_status(
				"SHIP-TEST-1",
				{"tracking_status": "In Progress", "tracking_status_info": "In Transit", "awb_number": ""},
			)
		self.assertNotIn("tracking_status", set_value.call_args.args[2])
		self.assertEqual(update.call_args.kwargs["tracking_info"], stored)

	@patch(MODULE + ".MIN_REQUEST_INTERVAL", 0.55)
	def test_requests_take_turns_across_workers(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit import starshipit

		key = starshipit.pacing_key("test-key")
		frappe.cache.delete(key)
		starshipit.wait_for_turn("test-key")
		# another worker's request holds the slot, read from Redis rather than this worker's memory
		self.assertTrue(frappe.cache.pttl(key) > 0)
		with patch("time.sleep") as sleep:
			sleep.side_effect = lambda seconds: frappe.cache.delete(key)
			starshipit.wait_for_turn("test-key")
		sleep.assert_called_once()

	def test_a_request_gives_up_waiting_for_its_turn_at_the_deadline(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit import starshipit

		key = starshipit.pacing_key("test-key")
		# nosemgrep: frappe-semgrep-rules.rules.frappe-cache-breaks-multitenancy -- the pacing key is shared by sites
		frappe.cache.set(key, 1, px=60000)
		self.addCleanup(frappe.cache.delete, key)
		with patch("time.sleep"), self.assertRaises(starshipit.StarshipitError):
			starshipit.wait_for_turn("test-key", deadline=0)

	def book(self, stored, book_again=0, error=None):
		"""Book a pickup for 2026-11-10 with the Shipment's booking fields as stored; returns the
		result (or raised error), whether Starshipit was asked and what was written."""
		from erpnext_shipping.erpnext_shipping.doctype.starshipit import starshipit

		shipment = frappe._dict(
			name="SHIP-TEST-1", pickup_date="2026-11-10", pickup_from="08:00:00", pickup_to="12:00:00"
		)
		shipment.add_comment = MagicMock()
		original_get_value = frappe.db.get_value

		def get_value(doctype, name=None, fields=None, *args, **kwargs):
			if doctype == "Shipment" and isinstance(fields, list):
				return stored
			if doctype == "Shipment":
				return name
			return original_get_value(doctype, name, fields, *args, **kwargs)

		writes = []
		with (
			patch.object(starshipit, "get_starshipit_shipment", return_value=shipment),
			patch("frappe.db.get_value", side_effect=get_value),
			patch("frappe.db.set_value", side_effect=lambda *args: writes.append(args[2:])),
			patch("frappe.db.commit"),
			patch.object(starshipit.StarshipitUtils, "__init__", return_value=None),
			patch.object(
				starshipit.StarshipitUtils, "book_pickup", side_effect=error, return_value="J42"
			) as book,
		):
			try:
				result = starshipit.book_pickup("SHIP-TEST-1", book_again=book_again)
			except frappe.ValidationError as e:
				result = e
		return result, book.called, writes

	def test_a_pickup_already_booked_for_the_date_is_not_booked_again(self):
		result, asked, _writes = self.book(("2026-11-10", "J41"))
		self.assertIsInstance(result, frappe.ValidationError)
		self.assertFalse(asked)

	def test_a_booking_that_got_no_answer_is_only_retried_once_confirmed(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import PICKUP_PENDING

		result, asked, _writes = self.book(("2026-11-10", PICKUP_PENDING))
		self.assertIn("confirm", result)
		self.assertFalse(asked)
		result, asked, writes = self.book(("2026-11-10", PICKUP_PENDING), book_again=1)
		self.assertEqual(result, {"job_number": "J42"})
		self.assertTrue(asked)
		self.assertEqual(writes[-1], ("pickup_job_number", "J42"))

	def test_a_booking_is_pending_until_starshipit_answers(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import (
			PICKUP_PENDING,
			StarshipitError,
			StarshipitNoAnswerError,
		)

		result, asked, writes = self.book((None, None), error=StarshipitNoAnswerError("no answer"))
		self.assertIsInstance(result, StarshipitNoAnswerError)
		self.assertTrue(asked)
		self.assertEqual(
			writes, [({"pickup_booked_for": getdate("2026-11-10"), "pickup_job_number": PICKUP_PENDING},)]
		)
		# a booking Starshipit refused leaves nothing pending
		result, asked, writes = self.book((None, None), error=StarshipitError("refused"))
		self.assertIsInstance(result, StarshipitError)
		self.assertEqual(writes[-1], ({"pickup_booked_for": None, "pickup_job_number": None},))

	def test_store_fulfilment_follows_a_status_update_at_once(self):
		with (
			patch("frappe.get_all", return_value=["FUL-TEST-1"]) as get_all,
			patch("shop.fulfillment.service.sync") as sync,
		):
			self.si.sync_store_fulfillment("SHIP-TEST-1")
		self.assertEqual(get_all.call_args.kwargs["filters"]["external_id"], "SHIP-TEST-1")
		sync.assert_called_once_with("FUL-TEST-1")

	def test_shipment_names_the_pickup_contact(self):
		shipment = frappe._dict()
		self.si.set_pickup_contact(shipment, "Administrator")
		self.assertIn(frappe.db.get_value("User", "Administrator", "first_name"), shipment.pickup_contact)
		self.assertEqual(shipment.pickup_contact_email, frappe.db.get_value("User", "Administrator", "email"))

	def test_order_status_follows_the_shipment(self):
		shipment = frappe._dict(
			docstatus=1,
			awb_number="",
			carrier="NZ Post",
			tracking_url="",
			tracking_status="",
			tracking_status_info="",
		)
		fulfillment = frappe._dict(external_id="SHIP-X", status="Accepted")
		provider = self.si.StarshipitFulfillment()
		for tracking_status, info, expected in (
			("", "Saved", "Accepted"),
			("In Progress", "Printed", "Accepted"),  # a label is not a shipped parcel
			("In Progress", "Manifested", "Shipped"),
			("In Progress", "In Transit: Picked up", "Shipped"),
			("Delivered", "Delivered", "Delivered"),
			("Returned", "Returned To Sender", "Failed"),
			("Lost", "Lost", "Failed"),
		):
			shipment.update(tracking_status=tracking_status, tracking_status_info=info)
			with (
				patch("frappe.db.exists", return_value=True),
				patch("frappe.db.get_value", return_value=shipment),
			):
				self.assertEqual(provider.fetch_status(fulfillment)["status"], expected, info)

	def test_customers_choice_is_marked(self):
		services = [
			{
				"service_provider": STARSHIPIT_PROVIDER,
				"service_id": "CourierPost|CPOLP",
				"service_name": "Parcel Post",
			},
			{
				"service_provider": STARSHIPIT_PROVIDER,
				"service_id": "CourierPost|CPOLE",
				"service_name": "Courier",
			},
		]
		choice = {"provider": STARSHIPIT_PROVIDER, "service_id": "CourierPost|CPOLE"}
		with (
			patch("frappe.db.exists", return_value=True),
			patch("frappe.db.get_value", return_value=json.dumps(choice)),
		):
			marked = self.si.mark_preferred(services, "SHIP-X")
		self.assertEqual([s.get("is_preferred") for s in marked], [None, 1])
		# a choice that wasn't a provider's service (e.g. a flat rate) marks none
		with (
			patch("frappe.db.exists", return_value=True),
			patch("frappe.db.get_value", return_value=json.dumps({"label": "Standard shipping"})),
		):
			marked = self.si.mark_preferred([dict(s, is_preferred=None) for s in services], "SHIP-X")
		self.assertEqual([s.get("is_preferred") for s in marked], [None, None])


class TestInstall(IntegrationTestCase):
	def test_variants_get_the_items_shipping_fields(self):
		from erpnext_shipping.install import copy_to_variants

		copy_to_variants(("shipping_parcel_template", "ships_in_own_box"))
		copied = {row.field_name for row in frappe.get_single("Item Variant Settings").fields}
		self.assertTrue({"shipping_parcel_template", "ships_in_own_box"} <= copied)
