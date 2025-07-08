# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.doctype.delhiveryone.constants import DELHIVERY_API_BASE_URL
from erpnext_shipping.erpnext_shipping.utils import (
	get_enabled_doc_for_company,
	handle_shipping_error,
	validate_enabled_service,
)

DELHIVERY_PROVIDER = "Delhiveryone"


class Delhiveryone(Document):
	def validate(self):
		if not self.enabled:
			return
		self.check_enabled()

	def check_enabled(self):
		self.enabled = validate_enabled_service(doctype=self.doctype, name=self.name, company=self.company)


class DelhiveryOneUtils:
	def __init__(self, company: str):
		settings = get_enabled_doc_for_company(DELHIVERY_PROVIDER, company)
		self.company = settings.get("company")
		self.api_key = settings.get_password("api_key")
		self.enable = settings.get("enabled")
		self.name = settings.get("name")

	def _make_request(
		self, method: str, endpoint: str, params=None, data=None, raise_exception: bool = True
	) -> dict:
		url = f"{DELHIVERY_API_BASE_URL}{endpoint}"
		headers = {"Content-Type": "application/json", "Authorization": f"Token {self.api_key}"}

		try:
			response = requests.request(method, url, headers=headers, params=params, data=data)
			response.raise_for_status()
			return response.json()
		except HTTPError as http_err:
			handle_shipping_error(
				self.name,
				DELHIVERY_PROVIDER,
				f"HTTP error in {method} request to {endpoint}",
				str(http_err),
				raise_exception,
			)
		except Exception as err:
			handle_shipping_error(
				self.name,
				DELHIVERY_PROVIDER,
				f"Exception in {method} request to {endpoint}",
				str(err),
				raise_exception,
			)
		return {}

	def get_availability(self, pickup_code: str) -> bool:
		params = {"token": self.api_key, "filter_codes": pickup_code}
		response = self._make_request("GET", "/c/api/pin-codes/json/", params=params, raise_exception=False)
		return bool(response.get("delivery_codes"))

	def get_available_services(self, delivery_address, pickup_address, weight):
		if not self.enable or not self.api_key:
			return []

		if not self.get_availability(pickup_address.pincode):
			return []

		services = []
		for mode in ["S", "E"]:
			params = {
				"md": mode,
				"ss": "Delivered",
				"d_pin": delivery_address.pincode,
				"o_pin": pickup_address.pincode,
				"cgm": int(weight) * 1000,
			}
			response = self._make_request(
				"GET", "/api/kinko/v1/invoice/charges/.json", params=params, raise_exception=False
			)
			services.append({mode: response})

		return [self.get_service_dict(service) for service in services]

	def create_shipment(self, **kwargs):
		pickup_phone = frappe.db.get_value("Address", kwargs["pickup_address_name"], "phone")

		shipments = [
			self.get_parcel_dict(
				kwargs["shipment"],
				parcel,
				i,
				kwargs["delivery_address"],
				kwargs["delivery_contact"],
				kwargs["service_info"],
			)
			for i, parcel in enumerate(json.loads(kwargs["shipment_parcel"]), start=1)
		]

		payload = {
			"data": {
				"pickup_location": {
					"add": kwargs["pickup_address"].address_title,
					"country": kwargs["pickup_address"].country_code.upper(),
					"pin": kwargs["pickup_address"].pincode,
					"phone": pickup_phone,
					"city": kwargs["pickup_address"].city,
					"name": kwargs["pickup_address_name"],
				},
				"shipments": shipments,
			}
		}
		json_data = json.dumps(payload["data"])
		formatted_payload = f"format=json&data={json_data}"
		response = self._make_request("POST", "/api/cmu/create.json", data=formatted_payload)
		if response and response.get("success"):
			awb_numbers = [pkg["waybill"] for pkg in response.get("packages", [])]
			return {
				"service_provider": "Delhiveryone",
				"shipment_id": ", ".join(awb_numbers),
				"carrier": "Delhiveryone",
				"carrier_service": kwargs["service_info"].get("service_name"),
				"shipment_amount": response.get("cod_amount", 0),
				"awb_number": ", ".join(awb_numbers),
			}

		return {}

	def get_label(self, shipment_id):
		shipment_ids = shipment_id.split(", ")
		label_urls = []
		for ship_id in shipment_ids:
			params = {"wbns": ship_id, "pdf": "true"}
			response = self._make_request("GET", "/api/p/packing_slip", params=params)
			if response and response.get("packages"):
				label_urls.append(response["packages"][0]["pdf_download_link"])
		return label_urls

	def get_tracking_data(self, shipment_id):
		shipment_ids = shipment_id.split(", ")
		awb_numbers, tracking_statuses, tracking_info = [], [], []
		for ship_id in shipment_ids:
			params = {"token": self.api_key, "waybill": ship_id}
			response = self._make_request("GET", "/api/v1/packages/json", params=params)
			if response.get("ShipmentData"):
				shipment = response["ShipmentData"][0]["Shipment"]
				awb_numbers.append(shipment.get("AWB", "N/A"))
				tracking_statuses.append(shipment["Status"]["Status"])
				tracking_info.append(shipment["Status"]["Instructions"])
		return {
			"awb_number": ", ".join(awb_numbers),
			"tracking_status": ", ".join(tracking_statuses),
			"tracking_status_info": ", ".join(tracking_info),
			"tracking_url": "",
		}

	def get_service_dict(self, service):
		service_type = next(iter(service))
		service_details = service[service_type][0]
		return frappe._dict(
			service_provider="Delhiveryone",
			carrier="Delhiveryone",
			service_name="Surface" if service_type == "S" else "Express",
			total_price=service_details.get("total_amount", 0.0),
			currency="INR",
			service_id=service_type,
		)

	def get_parcel_dict(
		self,
		shipment,
		parcel,
		index,
		delivery_address,
		delivery_contact,
		service_info,
	):
		return {
			"name": f"{delivery_contact.first_name} {delivery_contact.last_name}",
			"country": delivery_address.country,
			"city": delivery_address.city,
			"add": delivery_address.address_line1,
			"pin": delivery_address.pincode,
			"phone": delivery_contact.phone,
			"payment_mode": "Prepaid",
			"cod_amount": 0,
			"quantity": parcel.get("count"),
			"order": f"{shipment}-{index}",
			"shipment_width": parcel.get("width"),
			"shipment_height": parcel.get("height"),
			"weight": parcel.get("weight"),
			"shipping_mode": service_info.get("service_name"),
		}
