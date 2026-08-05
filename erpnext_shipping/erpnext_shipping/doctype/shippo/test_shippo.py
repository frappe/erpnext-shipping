# Copyright (c) 2025, Frappe and Contributors
# See license.txt
import requests
import frappe
import json
import unittest
from requests.exceptions import RequestException
from unittest.mock import MagicMock, patch
from erpnext_shipping.erpnext_shipping.doctype.shippo.shippo import ShippoUtils


def _make_utils(api_key="shippo_test_token", name="TEST-SHIPPO", company="_Test Company"):
	"""Return a ShippoUtils instance with __init__ bypassed."""
	utils = ShippoUtils.__new__(ShippoUtils)
	utils.api_key = api_key
	utils.name = name
	utils.company = company
	return utils


SHIPPO_STATUS_MAP = {
	"PRE_TRANSIT": "In Progress",
	"TRANSIT": "In Progress",
	"OUT_FOR_DELIVERY": "In Progress",
	"DELIVERED": "Delivered",
	"FAILURE": "Lost",
	"RETURNED": "Returned",
	"UNKNOWN": "In Progress",
}
SAMPLE_RATE = {
	"provider": "USPS",
	"servicelevel": {"display_name": "Priority Mail"},
	"amount": "12.50",
	"object_id": "rate_abc",
	"currency": "USD",
}

SAMPLE_ADDRESS = frappe._dict(
	{
		"address_title": "Test HQ",
		"address_line1": "123 Main St",
		"address_line2": "Suite 4",
		"city": "New York",
		"state": "NY",
		"pincode": "10001",
		"country_code": "us",
		"phone": "+12125550100",
		"email_id": "test@example.com",
	}
)

SAMPLE_SERVICE_INFO = {
	"service_provider": "Shippo",
	"service_id": "rate_abc",
	"carrier": "USPS",
	"service_name": "Priority Mail",
	"total_price": 12.50,
}


class TestShippoUtilsMakeRequest(unittest.TestCase):
	def setUp(self):
		self.utils = _make_utils()

	def _mock_response(self, json_data=None, raise_status=False):
		mock_resp = MagicMock()
		if raise_status:
			mock_resp.raise_for_status.side_effect = requests.HTTPError("HTTP Error")
		else:
			mock_resp.raise_for_status.return_value = None
		mock_resp.json.return_value = json_data or {}
		return mock_resp

	@patch("requests.request")
	def test_get_request_constructs_url_correctly(self, mock_req):
		"""URL = BASE_URL + endpoint, correct headers forwarded."""
		mock_req.return_value = self._mock_response({"ok": True})
		result = self.utils.make_request("GET", "/transactions/abc/")

		call_kwargs = mock_req.call_args
		self.assertEqual(call_kwargs.args[0], "GET")
		self.assertIn("https://api.goshippo.com/transactions/abc/", call_kwargs.args[1])
		self.assertIn("ShippoToken", call_kwargs.kwargs["headers"]["Authorization"])
		self.assertEqual(result, {"ok": True})

	@patch("requests.request")
	def test_post_with_payload_passes_data_kwarg(self, mock_req):
		"""payload= arg maps to data= in requests.request."""
		mock_req.return_value = self._mock_response({})
		self.utils.make_request("POST", "/shipments/", payload='{"key":"val"}')

		self.assertEqual(mock_req.call_args.kwargs["data"], '{"key":"val"}')
		self.assertIsNone(mock_req.call_args.kwargs["json"])

	@patch("requests.request")
	def test_post_with_json_passes_json_kwarg(self, mock_req):
		"""json= arg maps to json= in requests.request (used by create_shipment)."""
		mock_req.return_value = self._mock_response({})
		self.utils.make_request("POST", "/transactions/", json={"rate": "id"})

		self.assertEqual(mock_req.call_args.kwargs["json"], {"rate": "id"})
		self.assertIsNone(mock_req.call_args.kwargs["data"])

	@patch("erpnext_shipping.erpnext_shipping.doctype.shippo.shippo.handle_shipping_error")
	@patch("requests.request", side_effect=RequestException("timeout"))
	def test_request_exception_with_raise_false_is_soft_fail(self, _mock_req, mock_handle):
		"""raise_exception=False → returns None, handler still called."""
		result = self.utils.make_request("GET", "/test/", raise_exception=False)

		self.assertIsNone(result)
		mock_handle.assert_called_once()
		args = mock_handle.call_args.args
		self.assertFalse(args[4])  # raise_exception=False forwarded

	@patch("erpnext_shipping.erpnext_shipping.doctype.shippo.shippo.handle_shipping_error")
	@patch("requests.request")
	def test_value_error_from_json_parsing_is_caught(self, mock_req, mock_handle):
		"""response.json() raising ValueError is caught → returns None."""
		mock_resp = MagicMock()
		mock_resp.raise_for_status.return_value = None
		mock_resp.json.side_effect = ValueError("No JSON")
		mock_req.return_value = mock_resp

		result = self.utils.make_request("GET", "/test/")

		self.assertIsNone(result)
		mock_handle.assert_called_once()

	@patch("erpnext_shipping.erpnext_shipping.doctype.shippo.shippo.handle_shipping_error")
	@patch("requests.request")
	def test_http_error_status_is_caught(self, mock_req, mock_handle):
		"""raise_for_status() raising HTTPError is caught gracefully."""
		mock_req.return_value = self._mock_response(raise_status=True)

		result = self.utils.make_request("GET", "/test/")

		self.assertIsNone(result)
		mock_handle.assert_called_once()

	@patch("requests.request")
	def test_timeout_value_forwarded_to_requests(self, mock_req):
		"""REQUEST_TIMEOUT=30 must always be forwarded — prevents hanging."""
		mock_req.return_value = self._mock_response({})
		self.utils.make_request("GET", "/test/")

		self.assertEqual(mock_req.call_args.kwargs["timeout"], 30)


class TestShippoUtilsAvailableServices(unittest.TestCase):
	def setUp(self):
		self.utils = _make_utils()
		self.parcels = [{"height": 10, "length": 20, "width": 15, "weight": 2.5}]

	def test_returns_empty_list_when_api_key_is_empty(self):
		"""No API key → immediate empty return, no HTTP calls."""
		self.utils.api_key = ""
		with patch.object(self.utils, "make_request") as mock_req:
			result = self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "test")

		self.assertEqual(result, [])
		mock_req.assert_not_called()

	@patch("erpnext_shipping.erpnext_shipping.doctype.shippo.shippo.handle_shipping_error")
	def test_returns_empty_list_when_both_addresses_invalid(self, mock_handle):
		"""Both addresses None → handle_shipping_error called, empty list."""
		with patch.object(self.utils, "get_address", return_value=None):
			result = self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "test")

		self.assertEqual(result, [])
		mock_handle.assert_called_once()
		self.assertFalse(mock_handle.call_args.args[4])  # raise_exception=False

	@patch("erpnext_shipping.erpnext_shipping.doctype.shippo.shippo.handle_shipping_error")
	def test_returns_empty_list_when_from_address_invalid(self, _):
		"""from_address None (to_address valid) → still empty list."""
		with patch.object(self.utils, "get_address", side_effect=[None, "addr_to"]):
			result = self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "test")

		self.assertEqual(result, [])

	def test_shipment_payload_structure_to_api(self):
		"""Payload contains all required Shippo shipment fields."""
		success_response = {"status": "SUCCESS", "rates": []}
		with (
			patch.object(self.utils, "get_address", return_value="addr_id"),
			patch.object(self.utils, "make_request", return_value=success_response) as mock_req,
		):
			self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "fragile goods")

		call_kwargs = mock_req.call_args
		payload = json.loads(call_kwargs.kwargs["payload"])
		self.assertIn("parcels", payload)
		self.assertEqual(payload["address_from"], "addr_id")
		self.assertEqual(payload["address_to"], "addr_id")
		self.assertEqual(payload["object_purpose"], "PURCHASE")
		self.assertFalse(payload["async"])
		self.assertIn("shipment_date", payload)

	def test_parses_rates_from_successful_response(self):
		"""SUCCESS status + rates → list of service dicts."""
		rates_response = {"status": "SUCCESS", "rates": [SAMPLE_RATE, SAMPLE_RATE]}
		with (
			patch.object(self.utils, "get_address", return_value="addr_id"),
			patch.object(self.utils, "make_request", return_value=rates_response),
		):
			result = self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "test")

		self.assertEqual(len(result), 2)
		self.assertEqual(result[0]["carrier"], "USPS")

	def test_returns_empty_list_when_status_not_success(self):
		"""Non-SUCCESS status → empty list."""
		with (
			patch.object(self.utils, "get_address", return_value="addr_id"),
			patch.object(self.utils, "make_request", return_value={"status": "ERROR", "rates": []}),
		):
			result = self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "test")

		self.assertEqual(result, [])

	def test_returns_empty_list_when_make_request_returns_none(self):
		"""HTTP failure → empty list, no AttributeError."""
		with (
			patch.object(self.utils, "get_address", return_value="addr_id"),
			patch.object(self.utils, "make_request", return_value=None),
		):
			result = self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "test")

		self.assertEqual(result, [])

	def test_returns_empty_list_when_rates_list_is_empty(self):
		"""SUCCESS but no rates → empty list."""
		with (
			patch.object(self.utils, "get_address", return_value="addr_id"),
			patch.object(self.utils, "make_request", return_value={"status": "SUCCESS", "rates": []}),
		):
			result = self.utils.get_available_services(SAMPLE_ADDRESS, SAMPLE_ADDRESS, self.parcels, "test")

		self.assertEqual(result, [])


class TestShippoUtilsCreateShipment(unittest.TestCase):
	def setUp(self):
		self.utils = _make_utils()
		self.success_response = {
			"status": "SUCCESS",
			"object_id": "txn_001",
			"tracking_number": "AWB999",
		}

	def test_returns_complete_shipment_info_on_success(self):
		"""Every key in the returned dict is correct."""
		with patch.object(self.utils, "make_request", return_value=self.success_response):
			result = self.utils.create_shipment("SHIP-001", SAMPLE_SERVICE_INFO)

		self.assertEqual(result["service_provider"], "Shippo")
		self.assertEqual(result["shipment_id"], "txn_001")
		self.assertEqual(result["carrier"], "USPS")
		self.assertEqual(result["carrier_service"], "Priority Mail")
		self.assertEqual(result["shipment_amount"], 12.50)
		self.assertEqual(result["awb_number"], "AWB999")

	def test_correct_payload_sent_to_transactions_endpoint(self):
		"""json= kwarg used (not payload=), exact keys verified."""
		with patch.object(self.utils, "make_request", return_value=self.success_response) as mock_req:
			self.utils.create_shipment("SHIP-001", SAMPLE_SERVICE_INFO)

		call_args = mock_req.call_args
		self.assertEqual(call_args.args, ("POST", "/transactions/"))
		self.assertEqual(
			call_args.kwargs["json"],
			{"rate": "rate_abc", "async": False, "label_file_type": "PDF_4x6"},
		)

	def test_returns_none_when_status_not_success(self):
		"""Non-SUCCESS response → None."""
		with patch.object(self.utils, "make_request", return_value={"status": "WAITING"}):
			result = self.utils.create_shipment("SHIP-001", SAMPLE_SERVICE_INFO)

		self.assertIsNone(result)

	def test_returns_none_when_make_request_returns_none(self):
		"""HTTP failure → None, no AttributeError."""
		with patch.object(self.utils, "make_request", return_value=None):
			result = self.utils.create_shipment("SHIP-001", SAMPLE_SERVICE_INFO)

		self.assertIsNone(result)


class TestShippoUtilsGetTrackingData(unittest.TestCase):
	def setUp(self):
		self.utils = _make_utils()

	def test_returns_none_when_awb_is_none(self):
		"""Empty AWB → nothing to track, make_request never called."""
		with patch.object(self.utils, "make_request") as mock_req:
			result = self.utils.get_tracking_data(None, "USPS", "")

		self.assertIsNone(result)
		mock_req.assert_not_called()

	def test_returns_none_when_carrier_is_none(self):
		"""Empty carrier → cannot build endpoint."""
		with patch.object(self.utils, "make_request") as mock_req:
			result = self.utils.get_tracking_data("AWB123", None, "")

		self.assertIsNone(result)
		mock_req.assert_not_called()

	def test_carrier_is_lowercased_in_endpoint(self):
		"""Shippo API requires lowercase carrier in URL."""
		response = {"tracking_status": {"status": "TRANSIT", "status_details": "On the way"}}
		with patch.object(self.utils, "make_request", return_value=response) as mock_req:
			self.utils.get_tracking_data("AWB123", "USPS", "")

		self.assertEqual(mock_req.call_args.args, ("GET", "/tracks/usps/AWB123"))

	def test_all_shippo_statuses_parsed_correctly(self):
		"""Every Shippo status returned in tracking_status field."""
		for status in SHIPPO_STATUS_MAP:
			response = {
				"carrier": "shippo",
				"tracking_number": "AWB123",
				"tracking_status": {"status": status, "status_details": f"{status} details"},
			}
			with patch.object(self.utils, "make_request", return_value=response) as mock_req:
				data = self.utils.get_tracking_data("AWB123", "USPS", "http://track/AWB123")

			self.assertFalse(mock_req.call_args.kwargs["raise_exception"])
			self.assertEqual(data["awb_number"], "AWB123")
			self.assertEqual(data["tracking_status"], status)
			self.assertEqual(data["tracking_status_info"], f"{status} details")
			self.assertEqual(data["tracking_url"], "http://track/AWB123")

	def test_returns_none_when_response_has_no_tracking_status(self):
		"""Malformed response → None, no KeyError."""
		with patch.object(self.utils, "make_request", return_value={"carrier": "usps"}):
			result = self.utils.get_tracking_data("AWB123", "USPS", "")

		self.assertIsNone(result)

	def test_returns_none_when_make_request_returns_none(self):
		"""HTTP failure → None."""
		with patch.object(self.utils, "make_request", return_value=None):
			result = self.utils.get_tracking_data("AWB123", "USPS", "")

		self.assertIsNone(result)

	def test_raise_exception_false_passed_to_make_request(self):
		"""Tracking failure must never abort the caller (scheduler safety)."""
		with patch.object(self.utils, "make_request", return_value=None) as mock_req:
			self.utils.get_tracking_data("AWB123", "USPS", "")

		self.assertFalse(mock_req.call_args.kwargs["raise_exception"])
