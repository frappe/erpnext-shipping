# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from requests.exceptions import RequestException
from erpnext_shipping.erpnext_shipping.doctype.shiprocket.constants import (
	SHIPROCKET_API_BASE_URL,
	SHIPROCKET_PROVIDER,
)
from erpnext_shipping.erpnext_shipping.doctype.shiprocket.payloads import get_order_creation_payload
from erpnext_shipping.erpnext_shipping.utils import (
	get_enabled_doc_for_company,
	throw_shipping_error,
	validate_enabled_service,
)

REQUEST_TIMEOUT = 30


class Shiprocket(Document):
	def validate(self):
		if not self.enabled:
			return
		self.check_enabled()

	def check_enabled(self):
		self.enabled = validate_enabled_service(doctype=self.doctype, name=self.name, company=self.company)


class ShiprocketUtils:
	def __init__(self, company: str) -> None:
		settings = get_enabled_doc_for_company(SHIPROCKET_PROVIDER, company)
		self.bearer_token: str = settings.get("bearer_key")
		self.company: str = settings.get("company")
		self.name: str = settings.get("name")
		self.user_key = settings.get("api_id")
		self.user_secret = settings.get_password("api_password")
		if not self.bearer_token:
			self.generate_token()

	def get_headers(self) -> dict:
		return {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {self.bearer_token}",
		}

	def request_call(
		self,
		payload: dict,
		request_type: str,
		url: str,
		retry: bool = None,
		header: dict = None,
		raise_exception: bool = True,
	) -> dict:
		headers = self.get_headers() if not header else header
		try:
			response = requests.request(
				request_type, url, headers=headers, data=json.dumps(payload), timeout=REQUEST_TIMEOUT
			)
			response.raise_for_status()
			if response.status_code == 200:
				return response.json()
			else:
				throw_shipping_error(
					self.name,
					SHIPROCKET_PROVIDER,
					f"Error {response.status_code} during {request_type} request to {url}: {response.text}",
					raise_exception,
				)
		except requests.exceptions.HTTPError:
			if response.status_code in (401, 403) and not retry:
				self.generate_token(retry=True)
				return self.request_call(payload, request_type, url, retry=True, raise_exception=False)
			else:
				throw_shipping_error(
					self.name,
					SHIPROCKET_PROVIDER,
					f"Error {response.status_code} during {request_type} request to {url}: {response.text}",
					raise_exception,
				)
		except (RequestException, ValueError) as e:
			throw_shipping_error(
				self.name,
				SHIPROCKET_PROVIDER,
				f"Error during {request_type} request to {url}: {e}",
				raise_exception,
			)
		return {}

	def get_available_services(
		self,
		pickup_address: dict,
		delivery_address: dict,
		parcels: list,
		description_of_content: str = None,
	) -> list:
		"""Fetch available courier services and rates from Shiprocket."""
		pickup_postcode = pickup_address.get("pincode", "")
		delivery_postcode = delivery_address.get("pincode", "")
		total_weight = self.calculate_total_weight(parcels)

		url = f"{SHIPROCKET_API_BASE_URL}/courier/serviceability"
		payload = {
			"pickup_postcode": pickup_postcode,
			"delivery_postcode": delivery_postcode,
			"cod": 0,
			"weight": total_weight,
		}
		data = self.request_call(payload, "GET", url, raise_exception=False)
		services_available = data.get("data", {}).get("available_courier_companies", [])
		return [self._get_service_dict(service, parcels) for service in services_available]

	def calculate_total_weight(self, parcels: list) -> float:
		"""Calculate total weight from parcels list."""
		return sum(float(parcel.get("weight", 0)) * int(parcel.get("count", 1)) for parcel in parcels)

	def _get_service_dict(self, service: dict, parcels: list) -> dict:
		"""Returns a dictionary with service info."""
		return frappe._dict(
			{
				"service_provider": SHIPROCKET_PROVIDER,
				"carrier": str(service["courier_company_id"]),
				"service_name": service["courier_name"],
				"currency": "INR",
				"total_price": self._calculate_total_price(service["freight_charge"], parcels),
				"service_id": service["id"],
			}
		)

	def _calculate_total_price(self, price: float, parcels: list) -> float:
		return float(price) * sum(parcel.get("count", 0) for parcel in parcels)

	def create_shiprocket_shipment(self, **kwargs) -> dict:
		shipment = frappe.get_doc("Shipment", kwargs.get("shipment"))
		if not all([shipment, kwargs["pickup_address"]]):
			frappe.throw(_("Missing required shipment details."))
		url = f"{SHIPROCKET_API_BASE_URL}/orders/create/adhoc"
		payload = get_order_creation_payload(
			shipment.name,
			kwargs["pickup_date"],
			kwargs["delivery_company_name"],
			kwargs["delivery_contact"],
			kwargs["delivery_address"],
			kwargs["pickup_contact"],
			kwargs["pickup_address"],
			json.loads(kwargs["shipment_parcel"]),
			kwargs["description_of_content"],
			kwargs["value_of_goods"],
			kwargs["service_info"],
			kwargs["total_weight"],
		)
		response_data = self.request_call(payload, "POST", url)
		if response_data.get("status") == "CANCELED":
			frappe.throw(_("Could not make the shipment"))
		shipment_id = response_data.get("shipment_id")
		if shipment_id:
			return self._assign_awb(shipment_id, kwargs["service_info"])
		return {}

	def _assign_awb(self, shipment_id: str, service_info: dict) -> dict:
		url = f"{SHIPROCKET_API_BASE_URL}/courier/assign/awb"
		payload = {"shipment_id": shipment_id, "courier_id": service_info.get("carrier")}
		response_data = self.request_call(payload, "POST", url)
		if not response_data.get("awb_assign_status"):
			frappe.throw(_("Cannot create shipment, Shipping order created"))
		return {
			"service_provider": SHIPROCKET_PROVIDER,
			"shipment_id": shipment_id,
			"carrier": SHIPROCKET_PROVIDER,
			"carrier_service": service_info["service_name"],
			"shipment_amount": service_info["total_price"],
			"awb_number": response_data["response"]["data"]["awb_code"],
		}

	def get_label(self, shipment_id: str) -> str:
		"""Generate and return the label URL for a shipment."""
		url = f"{SHIPROCKET_API_BASE_URL}/courier/generate/label"
		payload = {"shipment_id": [shipment_id]}
		response_data = self.request_call(payload, "POST", url)
		if response_data.get("label_created"):
			label_url = response_data.get("label_url")
			if label_url:
				return label_url
		frappe.throw(_("Failed to generate shipping label for Shiprocket shipment {0}").format(shipment_id))

	def get_tracking_data(self, shipment_id: str) -> dict:
		url = f"{SHIPROCKET_API_BASE_URL}/courier/track/shipment/{shipment_id}"
		data = self.request_call({}, "GET", url)
		tracking_data = data.get("tracking_data", {})
		if not tracking_data.get("track_status"):
			frappe.throw(_("Failed to track order"))
		return {
			"awb_number": ", ".join(
				track.get("awb_code", "") for track in tracking_data.get("shipment_track", [])
			),
			"tracking_status": ", ".join(
				track.get("current_status", "") for track in tracking_data.get("shipment_track", [])
			),
			"tracking_status_info": ", ".join(
				track.get("courier_name", "") for track in tracking_data.get("shipment_track", [])
			),
			"tracking_url": tracking_data.get("track_url", ""),
		}

	def generate_token(self, retry: bool = None) -> None:
		url = f"{SHIPROCKET_API_BASE_URL}/auth/login"
		payload = {
			"email": self.user_key,
			"password": self.user_secret,
		}
		header = {
			"Content-Type": "application/json",
		}
		data = self.request_call(payload, "POST", url, header=header, retry=retry, raise_exception=False)
		self.bearer_token = data.get("token")
		frappe.db.set_value(SHIPROCKET_PROVIDER, self.name, "bearer_key", self.bearer_token)
