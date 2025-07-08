# Copyright (c) 2025, Frappe and Contributors
# See license.txt
from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from erpnext_shipping.erpnext_shipping import shipping as shipping_module
from erpnext_shipping.erpnext_shipping.doctype.shippo.shippo import ShippoUtils

SHIPPO_STATUS_MAP = {
	"PRE_TRANSIT": "In Progress",
	"TRANSIT": "In Progress",
	"OUT_FOR_DELIVERY": "In Progress",
	"DELIVERED": "Delivered",
	"FAILURE": "Lost",
	"RETURNED": "Returned",
	"UNKNOWN": "In Progress",
}


class TestShippo(FrappeTestCase):
	def test_get_tracking_data_url_and_state_parsing(self):
		"""Shippo tracking uses the documented /tracks/{carrier}/{number} endpoint."""
		utils = ShippoUtils.__new__(ShippoUtils)  # bypass __init__ (no Shippo settings needed)
		utils.name = "TEST-SHIPPO"
		utils.api_key = "shippo_test_token"

		for state in SHIPPO_STATUS_MAP:
			response = {
				"carrier": "shippo",
				"tracking_number": "AWB123",
				"tracking_status": {"status": state, "status_details": f"{state} details"},
			}
			with patch.object(utils, "make_request", return_value=response) as mock_request:
				data = utils.get_tracking_data("AWB123", "USPS", "http://track/AWB123")

			self.assertEqual(mock_request.call_args.args, ("GET", "/tracks/usps/AWB123"))
			self.assertFalse(mock_request.call_args.kwargs["raise_exception"])
			self.assertEqual(data["awb_number"], "AWB123")
			self.assertEqual(data["tracking_status"], state)
			self.assertEqual(data["tracking_status_info"], f"{state} details")
			self.assertEqual(data["tracking_url"], "http://track/AWB123")

	def test_shippo_statuses_are_mapped_to_shipment_field_values(self):
		for shippo_status, shipment_status in SHIPPO_STATUS_MAP.items():
			tracking_data = shipping_module.normalize_tracking_data(
				{"tracking_status": shippo_status, "awb_number": "AWB123"}
			)
			self.assertEqual(tracking_data.tracking_status, shipment_status)

	def test_create_shipment_purchases_the_selected_rate(self):
		utils = ShippoUtils.__new__(ShippoUtils)
		response = {
			"status": "SUCCESS",
			"object_id": "transaction-id",
			"tracking_number": "AWB123",
		}
		service = {
			"service_id": "rate-id",
			"carrier": "USPS",
			"service_name": "Priority Mail",
			"total_price": 12.5,
		}
		with patch.object(utils, "make_request", return_value=response) as mock_request:
			shipment_info = utils.create_shipment("SHIPMENT-TEST", service)

		self.assertEqual(mock_request.call_args.args, ("POST", "/transactions/"))
		self.assertEqual(
			mock_request.call_args.kwargs["json"],
			{"rate": "rate-id", "async": False, "label_file_type": "PDF_4x6"},
		)
		self.assertEqual(shipment_info["shipment_id"], "transaction-id")
		self.assertEqual(shipment_info["awb_number"], "AWB123")

	def test_update_tracking_no_data_is_noop(self):
		"""If the carrier returns nothing, update_tracking should return None and
		not raise."""
		fake_shippo = MagicMock()
		fake_shippo.get_tracking_data.return_value = None
		shipment = MagicMock(
			pickup_company="_Test Company", carrier="USPS", tracking_url="", awb_number="AWB123"
		)
		with (
			patch.object(shipping_module.frappe, "get_doc", return_value=shipment),
			patch.object(shipping_module, "ShippoUtils", return_value=fake_shippo),
		):
			result = shipping_module.update_tracking(
				shipment="SHIPMENT-TEST",
				service_provider="Shippo",
				shipment_id="txn_test",
				delivery_notes=[],
				awb_number="AWB123",
			)
		self.assertIsNone(result)
