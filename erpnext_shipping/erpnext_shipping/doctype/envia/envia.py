# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe.model.document import Document

from erpnext_shipping.erpnext_shipping.constants import state_codes
from erpnext_shipping.erpnext_shipping.doctype.envia.constants import (
	BASE_URL_API,
	BASE_URL_QUERY,
	TEST_BASE_URL_API,
	TEST_BASE_URL_QUERY,
)
from erpnext_shipping.erpnext_shipping.utils import (
	get_enabled_doc_for_company,
	handle_shipping_error,
	save_label_as_attachment,
	validate_enabled_service,
)

ENVIA_PROVIDER = "Envia"


class Envia(Document):
	def validate(self):
		if not self.enabled:
			return
		self.check_enabled()

	def check_enabled(self):
		self.enabled = validate_enabled_service(doctype=self.doctype, name=self.name, company=self.company)


class EnviaUtils:
	def __init__(self, company: str):
		settings = get_enabled_doc_for_company(ENVIA_PROVIDER, company)
		self.name = settings.get("name")
		self.api_url = TEST_BASE_URL_API if settings.get("sandbox") else BASE_URL_API
		self.query_url = TEST_BASE_URL_QUERY if settings.get("sandbox") else BASE_URL_QUERY
		self.api_key = settings.get_password("api_key")

	def get_common_headers(self) -> dict:
		return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

	def log_error(self, message: str, details: str = None):
		frappe.log_error(details or frappe.get_traceback(), message)

	def _make_request(self, method: str, url: str, data: dict = None, raise_exception: bool = True) -> dict:
		try:
			headers = self.get_common_headers()
			response = requests.request(method, url, headers=headers, json=data)
			response_data = response.json()
			if response.status_code != 200:
				handle_shipping_error(
					self.name,
					ENVIA_PROVIDER,
					f"Error in {method} request to {url}",
					str(response_data),
					raise_exception,
				)
				return {}
			return response_data.get("data", [])
		except Exception as e:
			handle_shipping_error(
				self.name, ENVIA_PROVIDER, f"Exception in {method} request to {url}", str(e), raise_exception
			)
			return {}

	def get_available_couriers(self, country_code: str, is_international: int) -> list[str]:
		url = f"{self.query_url}/available-carrier/{country_code}/{is_international}"
		carriers = self._make_request("GET", url, raise_exception=False)
		return [carrier.get("name") for carrier in carriers]

	def get_available_services(
		self,
		pickup_address: dict,
		delivery_address: dict,
		parcels: list[dict],
		pickup_contact: dict,
		delivery_contact: dict,
		value_of_goods: float,
		total_weight: float,
		pickup_company: str,
		description_of_content: str,
	) -> list[dict]:
		carriers = self.get_available_couriers(pickup_address.get("country_code", "").upper(), 0)
		origin = self.build_address(pickup_address, pickup_contact)
		destination = self.build_address(delivery_address, delivery_contact)
		packages = self.build_packages(parcels, total_weight, value_of_goods, description_of_content)
		currency = self.get_company_currency(pickup_company)

		available_services = []
		if currency:
			for carrier in carriers:
				payload = {
					"origin": origin,
					"destination": destination,
					"packages": packages,
					"shipment": {"carrier": carrier, "type": 1},
					"settings": {
						"printFormat": "PDF",
						"printSize": "STOCK_4X6",
						"currency": currency,
						"cashOnDelivery": value_of_goods,
						"comments": "Handle with care",
					},
				}
				services = self._make_request(
					"POST", f"{self.api_url}/ship/rate/", payload, raise_exception=False
				)
				available_services.extend([self.parse_service_data(service, parcels) for service in services])

		return available_services

	def create_shipment(self, **kwargs) -> dict:
		payload = {
			"origin": self.build_address(kwargs["pickup_address"], kwargs["pickup_contact"]),
			"destination": self.build_address(kwargs["delivery_address"], kwargs["delivery_contact"]),
			"packages": self.build_packages(
				json.loads(kwargs["shipment_parcel"]),
				kwargs["total_weight"],
				kwargs["value_of_goods"],
				kwargs["description_of_content"],
			),
			"shipment": {
				"carrier": kwargs["service_info"].get("carrier"),
				"service": kwargs["service_info"].get("service_id"),
				"type": 1,
			},
			"settings": {"printFormat": "PDF", "printSize": "STOCK_4X6", "comments": "Handle with care"},
		}
		shipment_data = self._make_request("POST", f"{self.api_url}/ship/generate/", payload)

		if shipment_data:
			save_label_as_attachment(shipment=kwargs["shipment"], url=shipment_data[0].get("label"))
			return {
				"service_provider": ENVIA_PROVIDER,
				"shipment_id": shipment_data[0].get("shipmentId"),
				"carrier": shipment_data[0].get("carrier"),
				"shipment_amount": shipment_data[0].get("totalPrice"),
				"awb_number": shipment_data[0].get("trackingNumber"),
			}
		return {}

	def build_address(self, address: dict, contact: dict | str) -> dict:
		if not (contact and address):
			return None
		if isinstance(contact, str):
			contact = contact.split("<br>")

		name = contact[0] if isinstance(contact, list) else contact.get("first_name")
		email = contact[1] if isinstance(contact, list) else contact.get("email_id")
		phone = contact[2] if isinstance(contact, list) else contact.get("phone")

		return {
			"name": name,
			"company": "Envia India",
			"email": email,
			"phone": phone,
			"street": address.get("address_line1"),
			"number": address.get("address_line2"),
			"district": address.get("city"),
			"city": address.get("city"),
			"state": state_codes.get(address.get("state", "").replace(" ", "").lower()),
			"category": 1,
			"country": address.get("country_code", "").upper(),
			"postalCode": address.get("pincode"),
			"reference": "",
		}

	def build_packages(
		self, parcels: list[dict], total_weight: float, value_of_goods: float, description_of_content: str
	) -> list[dict]:
		return [
			{
				"content": description_of_content,
				"amount": item.get("count", 1),
				"type": "box",
				"weight": item.get("weight", 0),
				"insurance": int(value_of_goods),
				"declaredValue": int(value_of_goods),
				"weightUnit": "KG",
				"lengthUnit": "CM",
				"dimensions": {
					"length": item.get("length", 0),
					"width": item.get("width", 0),
					"height": item.get("height", 0),
				},
			}
			for item in parcels
		]

	def parse_service_data(self, service: dict, parcels: list[dict]) -> dict:
		return {
			"service_provider": ENVIA_PROVIDER,
			"carrier": service["carrier"],
			"service_name": service["serviceDescription"],
			"currency": service["currency"],
			"total_price": service["totalPrice"],
			"carrier_id": service["carrierId"],
			"service_id": service["service"],
		}

	def get_tracking_data(self, awb_number: str) -> dict:
		payload = {"trackingNumbers": [awb_number]}
		tracking_data = self._make_request("POST", f"{self.api_url}/ship/generaltrack/", payload)
		tracking_data = tracking_data[0] if tracking_data else {}
		if not tracking_data:
			return {}

		return {
			"awb_number": awb_number,
			"tracking_status": tracking_data.get("status"),
			"tracking_status_info": tracking_data.get("trackingNumber"),
			"tracking_url": tracking_data.get("trackUrl"),
		}

	def get_company_currency(self, company: str) -> str:
		return frappe.db.get_value("Company", company, "default_currency")
