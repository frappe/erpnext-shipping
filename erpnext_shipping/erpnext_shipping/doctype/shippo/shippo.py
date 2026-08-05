# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import json
from datetime import datetime

import frappe
import requests
from frappe.model.document import Document
from requests.exceptions import RequestException

from erpnext_shipping.erpnext_shipping.doctype.shippo.constants import SHIPPO_API_BASE_URL, SHIPPO_PROVIDER
from erpnext_shipping.erpnext_shipping.utils import (
	get_enabled_doc_for_company,
	handle_shipping_error,
	validate_enabled_service,
)

REQUEST_TIMEOUT = 30


class Shippo(Document):
	def validate(self):
		if not self.enabled:
			return
		self.check_enabled()

	def check_enabled(self):
		self.enabled = validate_enabled_service(doctype=self.doctype, name=self.name, company=self.company)


class ShippoUtils:
	def __init__(self, company: str):
		settings = get_enabled_doc_for_company(SHIPPO_PROVIDER, company)
		self.company = settings.get("company")
		self.name = settings.get("name")
		self.api_key = settings.get_password("api_key")

	def make_request(
		self,
		method: str,
		endpoint: str,
		payload: dict = None,
		json: dict = None,
		raise_exception: bool = True,
	):
		url = f"{SHIPPO_API_BASE_URL}{endpoint}"
		headers = self.get_common_headers()
		try:
			response = requests.request(
				method, url, headers=headers, data=payload, json=json, timeout=REQUEST_TIMEOUT
			)
			response.raise_for_status()
			return response.json()
		except (RequestException, ValueError) as e:
			handle_shipping_error(
				self.name, SHIPPO_PROVIDER, f"Exception in {method} request to {url}", e, raise_exception
			)
			return None

	def get_common_headers(self) -> dict:
		return {"Authorization": f"ShippoToken {self.api_key}", "Content-Type": "application/json"}

	def get_available_services(
		self, delivery_address, pickup_address, parcels, description_of_content
	) -> list[dict]:
		if not self.api_key:
			return []

		from_address = self.get_address(pickup_address, description_of_content)
		to_address = self.get_address(delivery_address, description_of_content)
		shipment_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

		if not (from_address and to_address):
			handle_shipping_error(
				self.name,
				SHIPPO_PROVIDER,
				"Shippo Error",
				"Should provide a valid address to create a shipment",
				False,
			)
			return []
		payload = json.dumps(
			{
				"parcels": self.get_rate_payload(parcels),
				"address_from": from_address,
				"address_to": to_address,
				"object_purpose": "PURCHASE",
				"async": False,
				"shipment_date": shipment_date,
			}
		)
		response = self.make_request("POST", "/shipments/", payload=payload, raise_exception=False)
		if response and response.get("status") == "SUCCESS":
			return [self.get_service_dict(rate) for rate in response.get("rates", [])]
		return []

	def get_address(self, address, description_of_content):
		payload = {
			"name": address.address_title,
			"street1": address.address_line1,
			"street2": address.address_line2,
			"city": address.city,
			"state": address.state,
			"zip": address.pincode,
			"country": address.country_code.upper(),
			"phone": address.get("phone"),
			"email": address.get("email_id"),
			"metadata": description_of_content,
			"validate": True,
			"object_purpose": "PURCHASE",
		}

		payload = json.dumps(payload)
		response = self.make_request("POST", "/addresses/", payload=payload, raise_exception=False)
		return (
			response.get("object_id")
			if response and response.get("validation_results", {}).get("is_valid")
			else None
		)

	def create_shipment(self, shipment, service_info):
		payload = {
			"rate": service_info["service_id"],
			"async": False,
			"label_file_type": "PDF_4x6",
		}
		response = self.make_request("POST", "/transactions/", json=payload)
		if response and response.get("status") == "SUCCESS":
			return {
				"service_provider": SHIPPO_PROVIDER,
				"shipment_id": response["object_id"],
				"carrier": service_info["carrier"],
				"carrier_service": service_info["service_name"],
				"shipment_amount": service_info["total_price"],
				"awb_number": response["tracking_number"],
			}
		return None

	def get_label(self, shipment_id, shipment):
		response = self.make_request("GET", f"/transactions/{shipment_id}/")
		if response and response.get("status") == "SUCCESS":
			frappe.db.set_value(
				"Shipment", shipment, "tracking_url", response.get("tracking_url_provider", "")
			)
			return response.get("label_url") or []
		return []

	def get_tracking_data(self, awb, carrier, tracking_url):
		if not awb or not carrier:
			return None

		response = self.make_request(
			"GET",
			f"/tracks/{carrier.lower()}/{awb}",
			raise_exception=False,
		)

		if response and response.get("tracking_status"):
			return {
				"awb_number": awb,
				"tracking_status": response["tracking_status"].get("status"),
				"tracking_status_info": response["tracking_status"].get("status_details"),
				"tracking_url": tracking_url,
			}

	def get_rate_payload(self, parcels):
		return [
			{
				"height": parcel.get("height", 0),
				"distance_unit": "cm",
				"length": parcel.get("length", 0),
				"width": parcel.get("width", 0),
				"weight": parcel.get("weight", 0),
				"mass_unit": "kg",
			}
			for parcel in parcels
		]

	def get_service_dict(self, rates):
		return frappe._dict(
			{
				"service_provider": SHIPPO_PROVIDER,
				"carrier": rates.get("provider"),
				"service_name": rates.get("servicelevel", {}).get("display_name"),
				"total_price": float(rates.get("amount")),
				"service_id": rates.get("object_id"),
				"currency": rates["currency"],
			}
		)
