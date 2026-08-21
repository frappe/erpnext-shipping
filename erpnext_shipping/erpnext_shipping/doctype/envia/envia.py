# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_url_to_list

from erpnext_shipping.erpnext_shipping.doctype.envia.constants import (
	ENVIA_PROVIDER,
	ENVIA_STATUS_BY_ID,
	ENVIA_STATUS_BY_NAME,
)
from erpnext_shipping.erpnext_shipping.utils import (
	get_enabled_doc_for_company,
	get_state_code,
	handle_shipping_error,
	save_label_as_attachment,
	validate_enabled_service,
)

# Envia shipment type: 1 = package (see Envia ship API docs)
ENVIA_SHIPMENT_TYPE = 1

# Re-export for callers that import ENVIA_PROVIDER from this module
__all__ = ["ENVIA_PROVIDER", "Envia", "EnviaUtils", "map_envia_tracking_status"]


def map_envia_tracking_status(status) -> str:
	"""Map an Envia status (numeric id or name) to an ERPNext Tracking Status option.

	Falls back to "In Progress" for unknown/blank statuses so tracking never breaks on a
	value that isn't in the Shipment field's allowed options.
	"""
	if status is None:
		return "In Progress"

	key = str(status).strip()
	if key.isdigit():
		return ENVIA_STATUS_BY_ID.get(int(key), "In Progress")
	return ENVIA_STATUS_BY_NAME.get(key.lower(), "In Progress")


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
		if not settings:
			frappe.throw(
				_("No enabled Envia account found for company {0}. Please configure Envia in {1}.").format(
					frappe.bold(company),
					f'<a href="{get_url_to_list(ENVIA_PROVIDER)}">{ENVIA_PROVIDER}</a>',
				),
				title=_("Envia not configured"),
			)

		self.name = settings.name
		# Prefer DocType URL fields; sandbox uses test URLs when configured
		if settings.sandbox:
			self.api_url = (settings.test_base_api_url or settings.base_api_url or "").rstrip("/")
			self.query_url = (settings.test_base_url_query or settings.base_url_query or "").rstrip("/")
		else:
			self.api_url = (settings.base_api_url or "").rstrip("/")
			self.query_url = (settings.base_url_query or "").rstrip("/")

		if not self.api_url or not self.query_url:
			frappe.throw(
				_(
					"Envia API URLs are not configured for {0}. "
					"Set Base API URL and Base URL Query (and test URLs if Sandbox is enabled)."
				).format(frappe.bold(settings.name)),
				title=_("Envia URLs missing"),
			)

		self.api_key = settings.get_password("api_key")

	def get_common_headers(self) -> dict:
		return {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

	def log_error(self, message: str, details: str = None):
		frappe.log_error(details or frappe.get_traceback(), message)

	@staticmethod
	def _extract_envia_error(response_data) -> str | None:
		"""Parse Envia error payloads (often returned with HTTP 200 + meta=error)."""
		if not isinstance(response_data, dict):
			return None

		err = response_data.get("error")
		if response_data.get("meta") == "error" or err:
			if isinstance(err, dict):
				return (
					err.get("message")
					or err.get("description")
					or f"Envia error code {err.get('code', 'unknown')}"
				)
			if err:
				return str(err)
			return str(response_data)

		return None

	def _make_request(
		self, method: str, url: str, data: dict = None, raise_exception: bool = True
	) -> list | dict:
		try:
			headers = self.get_common_headers()
			response = requests.request(method, url, headers=headers, json=data)
		except Exception as e:
			handle_shipping_error(
				self.name, ENVIA_PROVIDER, f"Exception in {method} request to {url}", str(e), raise_exception
			)
			return []

		try:
			response_data = response.json()
		except ValueError:
			body = response.text.strip()
			message = (
				"Envia returned an empty or invalid response for this carrier "
				f"(HTTP {response.status_code}). This carrier may not support label "
				"generation for this account — please try a different carrier."
			)
			handle_shipping_error(
				self.name,
				ENVIA_PROVIDER,
				message,
				body[:500] or "<empty response body>",
				raise_exception,
			)
			return []

		envia_error = self._extract_envia_error(response_data)
		if response.status_code != 200 or envia_error:
			handle_shipping_error(
				self.name,
				ENVIA_PROVIDER,
				envia_error or f"Error in {method} request to {url}",
				str(response_data),
				raise_exception,
			)
			return []

		data_payload = response_data.get("data", [])
		return data_payload if data_payload is not None else []

	def get_available_couriers(self, country_code: str, is_international: int) -> list[str]:
		url = f"{self.query_url}/available-carrier/{country_code}/{is_international}"
		carriers = self._make_request("GET", url, raise_exception=False)
		if not isinstance(carriers, list):
			return []
		return [
			carrier.get("name") for carrier in carriers if isinstance(carrier, dict) and carrier.get("name")
		]

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
		origin_country = (pickup_address.get("country_code") or "").upper()
		destination_country = (delivery_address.get("country_code") or "").upper()
		is_international = int(
			bool(origin_country and destination_country and origin_country != destination_country)
		)

		carriers = self.get_available_couriers(origin_country, is_international)
		origin = self.build_address(pickup_address, pickup_contact, company=pickup_company)
		destination = self.build_address(delivery_address, delivery_contact)
		packages = self.build_packages(parcels, total_weight, value_of_goods, description_of_content)
		currency = self.get_company_currency(pickup_company)

		available_services = []
		if not carriers:
			frappe.msgprint(
				_("No Envia carriers available for {0} ({1} shipment).").format(
					origin_country, _("international") if is_international else _("domestic")
				),
				indicator="orange",
				alert=True,
			)
			return available_services

		if not currency:
			frappe.msgprint(
				_("Company default currency is not set for {0}.").format(frappe.bold(pickup_company)),
				indicator="orange",
				alert=True,
			)
			return available_services

		for carrier in carriers:
			payload = {
				"origin": origin,
				"destination": destination,
				"packages": packages,
				"shipment": {"carrier": carrier, "type": ENVIA_SHIPMENT_TYPE},
				"settings": {
					"printFormat": "PDF",
					"printSize": "STOCK_4X6",
					"currency": currency,
					"cashOnDelivery": value_of_goods,
					"comments": description_of_content or "",
				},
			}
			services = self._make_request(
				"POST", f"{self.api_url}/ship/rate/", payload, raise_exception=False
			)
			if not isinstance(services, list):
				continue
			available_services.extend(
				[
					self.parse_service_data(service, parcels)
					for service in services
					if isinstance(service, dict)
				]
			)

		return available_services

	def create_shipment(self, **kwargs) -> dict:
		description_of_content = kwargs.get("description_of_content") or "Handle with care"
		pickup_company = kwargs.get("pickup_company")
		if not pickup_company and kwargs.get("pickup_address"):
			pickup_company = kwargs["pickup_address"].get("address_title")

		payload = {
			"origin": self.build_address(
				kwargs["pickup_address"],
				kwargs["pickup_contact"],
				company=pickup_company,
			),
			"destination": self.build_address(
				kwargs["delivery_address"],
				kwargs["delivery_contact"],
				company=kwargs.get("delivery_company_name"),
			),
			"packages": self.build_packages(
				json.loads(kwargs["shipment_parcel"]),
				kwargs["total_weight"],
				kwargs["value_of_goods"],
				description_of_content,
			),
			"shipment": {
				"carrier": kwargs["service_info"].get("carrier"),
				"service": kwargs["service_info"].get("service_id"),
				"type": ENVIA_SHIPMENT_TYPE,
			},
			"settings": {
				"printFormat": "PDF",
				"printSize": "STOCK_4X6",
				"comments": description_of_content,
			},
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

	def build_address(self, address: dict, contact: dict | str, company: str | None = None) -> dict:
		if not (contact and address):
			return None
		if isinstance(contact, str):
			contact = contact.split("<br>")

		if isinstance(contact, list):
			name = contact[0] if len(contact) > 0 else ""
			email = contact[1] if len(contact) > 1 else ""
			phone = contact[2] if len(contact) > 2 else ""
		else:
			name = (contact.get("first_name") or "").strip() or (contact.get("last_name") or "").strip()
			if contact.get("first_name") and contact.get("last_name"):
				name = f"{contact.get('first_name')} {contact.get('last_name')}".strip()
			email = contact.get("email_id") or ""
			phone = contact.get("phone") or contact.get("mobile_no") or ""
		country_code = (address.get("country_code") or "").upper()
		company_name = company or address.get("address_title") or ""

		# Resolve state name → state code via countriesnow API (cached in utils.get_state_code)
		state_code = get_state_code(address.get("state"), address.get("country"))

		return {
			"name": name or company_name,
			"company": company_name,
			"email": email,
			"phone": phone,
			"street": address.get("address_line1"),
			"number": address.get("address_line2"),
			"district": address.get("city"),
			"city": address.get("city"),
			"state": state_code,
			"category": 1,
			"country": country_code,
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
			"carrier": service.get("carrier"),
			"service_name": service.get("serviceDescription"),
			"currency": service.get("currency"),
			"total_price": service.get("totalPrice"),
			"carrier_id": service.get("carrierId"),
			"service_id": service.get("service"),
		}

	def get_tracking_data(self, awb_number: str) -> dict:
		payload = {"trackingNumbers": [awb_number]}
		tracking_data = self._make_request("POST", f"{self.api_url}/ship/generaltrack/", payload)
		tracking_data = tracking_data[0] if tracking_data else {}
		if not tracking_data:
			return {}

		envia_status = tracking_data.get("status")
		return {
			"awb_number": awb_number,
			"tracking_status": map_envia_tracking_status(envia_status),
			"tracking_status_info": envia_status,
			"tracking_url": tracking_data.get("trackUrl"),
		}

	def get_company_currency(self, company: str) -> str:
		return frappe.db.get_value("Company", company, "default_currency")
