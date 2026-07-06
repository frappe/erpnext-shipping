# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
import re

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt
from frappe.utils.data import get_link_to_form
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

SENDCLOUD_PROVIDER = "SendCloud"
WEIGHT_DECIMALS = 3
CURRENCY_DECIMALS = 2

BASE_URL = "https://panel.sendcloud.sc/api"
SHIPPING_OPTIONS_URL = f"{BASE_URL}/v3/shipping-options"
SHIPMENTS_URL = f"{BASE_URL}/v3/shipments"
SHIPMENTS_ANNOUNCE_URL = f"{SHIPMENTS_URL}/announce"
LABELS_URL = f"{BASE_URL}/v2/labels"
PARCELS_URL = f"{BASE_URL}/v2/parcels"


class SendCloud(Document):
	pass


class SendCloudUtils:
	def __init__(self):
		settings = frappe.get_single("SendCloud")
		self.api_key = settings.api_key
		self.api_secret = settings.get_password("api_secret")
		self.enabled = settings.enabled

		if not self.enabled:
			link = get_link_to_form("SendCloud", "SendCloud", _("SendCloud Settings"))
			frappe.throw(_("Please enable SendCloud Integration in {0}").format(link))

	def get_available_services(self, delivery_address, pickup_address, parcels: list[dict]):
		# Retrieve rates at SendCloud from specification stated.
		if not self.enabled or not self.api_key or not self.api_secret:
			return []

		max_weight = max(parcel.get("weight", 0) for parcel in parcels)
		max_length = max(parcel.get("length", 0) for parcel in parcels)
		max_width = max(parcel.get("width", 0) for parcel in parcels)
		max_height = max(parcel.get("height", 0) for parcel in parcels)

		for parcel in parcels:
			self.warn_partial_dimensions(parcel)

		to_country = delivery_address.country_code.upper()
		from_country = pickup_address.country_code.upper()

		parcel_data = {
			"weight": {"value": flt(max_weight, WEIGHT_DECIMALS), "unit": "kg"},
		}

		if max_length > 0 and max_width > 0 and max_height > 0:
			parcel_data["dimensions"] = {
				"length": max_length,
				"width": max_width,
				"height": max_height,
				"unit": "cm",
			}

		payload = {
			"from_address": {
				"country_code": from_country,
				"postal_code": pickup_address.pincode,
				"city": pickup_address.city,
				"address_line_1": pickup_address.address_line1,
			},
			"to_address": {
				"country_code": to_country,
				"postal_code": delivery_address.pincode,
				"city": delivery_address.city,
				"address_line_1": delivery_address.address_line1,
			},
			"parcels": [parcel_data],
			"calculate_quotes": True,
		}

		try:
			response = requests.post(
				SHIPPING_OPTIONS_URL,
				json=payload,
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json", "Content-Type": "application/json"},
			)

			response_data = response.json()

			if errors := response_data.get("errors"):
				frappe.throw(self.format_api_errors(errors), title=_("SendCloud"))

			if "data" not in response_data or not response_data["data"]:
				frappe.throw(_("No shipping options found for this destination."), title=_("SendCloud"))

			available_services = []
			for service in response_data["data"]:
				available_service = self.get_service_dict(service, parcels)
				available_services.append(available_service)

			return available_services
		except frappe.ValidationError:
			raise
		except Exception:
			show_error_alert("fetching SendCloud prices")

	def create_shipment(
		self,
		shipment,
		pickup_address,
		pickup_contact,
		delivery_address,
		delivery_contact,
		service_info,
		shipment_parcel,
	):
		if not self.enabled or not self.api_key or not self.api_secret:
			return []

		parcels = []
		index = 0
		for parcel in json.loads(shipment_parcel):
			self.warn_partial_dimensions(parcel)
			for _parcel in range(parcel.get("count", 1)):
				index += 1
				parcels.append(self.get_parcel(parcel, shipment, index))

		house_number, address = self.extract_house_number(pickup_address.address_line1)

		payload = {
			"parcels": parcels,
			"to_address": {
				"company_name": delivery_address.address_title,
				"name": f"{delivery_contact.first_name} {delivery_contact.last_name}",
				"address_line_1": delivery_address.address_line1,
				"postal_code": delivery_address.pincode,
				"city": delivery_address.city,
				"country_code": delivery_address.country_code.upper(),
				"phone_number": delivery_contact.phone,
				"email": delivery_contact.email_id,
			},
			"from_address": {
				"name": f"{pickup_contact.first_name} {pickup_contact.last_name}",
				"company_name": pickup_address.address_title,
				"address_line_1": address
				or pickup_address.address_line1,  # Using original address if parsing fails
				"house_number": house_number
				or " ",  # API requires a house number. If None, we use a U+200A HAIR SPACE to bypass validation without displaying a number
				"postal_code": pickup_address.pincode,
				"city": pickup_address.city,
				"country_code": pickup_address.country_code.upper(),
				"phone_number": pickup_contact.phone,
				"email": pickup_contact.email_id,
			},
			"ship_with": {
				"type": "shipping_option_code",
				"properties": {
					"shipping_option_code": service_info["service_id"],
				},
			},
		}

		shipments_results = []
		failed_parcels = []

		for parcel in parcels:
			payload_single = payload.copy()
			payload_single["parcels"] = [parcel]
			order_number = parcel.get("order_number")
			try:
				response = requests.post(
					SHIPMENTS_ANNOUNCE_URL,
					json=payload_single,
					auth=(self.api_key, self.api_secret),
				)
				response_data = response.json()
				if errors := response_data.get("errors"):
					failed_parcels.append((order_number, self.format_api_errors(errors)))
					continue

				parcels_data = response_data.get("data", {}).get("parcels", [])
				if not parcels_data:
					failed_parcels.append((order_number, _("No parcel data returned from SendCloud.")))
					continue

				parcel_data = parcels_data[0]
				shipments_results.append(
					{
						"shipment_id": str(parcel_data["id"]),
						"awb_number": parcel_data.get("tracking_number", ""),
						"tracking_url": parcel_data.get("tracking_url", ""),
						"carrier": self.get_carrier(service_info["carrier"], post_or_get="post"),
						"carrier_service": service_info["service_name"],
						"shipment_amount": service_info["total_price"],
					}
				)
			except Exception:
				show_error_alert(f"creating SendCloud Shipment for parcel {order_number}")
				failed_parcels.append((order_number, _("Unexpected error. See Error Log.")))

		if len(shipments_results) == len(parcels):
			return {
				"service_provider": "SendCloud",
				"shipment_id": ", ".join(
					item["shipment_id"] for item in shipments_results if item.get("shipment_id")
				),
				"carrier": shipments_results[0]["carrier"],
				"carrier_service": shipments_results[0]["carrier_service"],
				"shipment_amount": service_info["total_price"],
				"awb_number": ", ".join(
					item["awb_number"] for item in shipments_results if item.get("awb_number")
				),
				"tracking_url": ", ".join(
					item["tracking_url"] for item in shipments_results if item.get("tracking_url")
				),
			}

		for order_number, reason in failed_parcels:
			frappe.msgprint(
				_("Error occurred while creating shipment for parcel {0}:").format(order_number)
				+ f"\n{reason}",
				indicator="red",
				alert=True,
			)

		if shipments_results:
			cancelled_ids = []
			cancel_failed = []
			for result in shipments_results:
				shipment_id = result["shipment_id"]
				success, error = self.cancel_shipment(shipment_id)
				if success:
					cancelled_ids.append(shipment_id)
				else:
					cancel_failed.append((shipment_id, error))

			if cancelled_ids:
				frappe.msgprint(
					_("Cancelled SendCloud shipment IDs: {0}").format(", ".join(cancelled_ids)),
					indicator="orange",
					alert=True,
				)
			if cancel_failed:
				failed_details = "\n".join(
					f"ID {shipment_id}: {error}" for shipment_id, error in cancel_failed
				)
				frappe.msgprint(
					_("Failed to cancel SendCloud shipment IDs (manual cleanup needed):")
					+ f"\n{failed_details}",
					indicator="red",
					alert=True,
				)

		return None

	def cancel_shipment(self, shipment_id):
		try:
			response = requests.post(
				f"{SHIPMENTS_URL}/{shipment_id}/cancel",
				auth=(self.api_key, self.api_secret),
			)
			response_data = response.json()
			if errors := response_data.get("errors"):
				return False, self.format_api_errors(errors)
			return True, None
		except Exception:
			return False, _("Request failed")

	def get_label(self, shipment_id):
		# Retrieve shipment label from SendCloud
		shipment_id_list = shipment_id.split(", ")
		label_urls = []

		for ship_id in shipment_id_list:
			try:
				response = requests.get(
					f"{LABELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
					headers={"Accept": "application/json"},
				)
				response.raise_for_status()
				data = response.json()
				label_url = data.get("label", {}).get("label_printer")
				if label_url:
					label_urls.append(label_url)
				else:
					frappe.msgprint(
						msg=_(
							"Please make sure Shipment (ID: {0}), exists and is a complete Shipment on SendCloud."
						).format(ship_id),
						title=_("Label Not Found"),
					)
			except Exception:
				show_error_alert("printing SendCloud Label")

		return label_urls

	def download_label(self, label_url: str):
		"""Download label from SendCloud."""
		try:
			resp = requests.get(label_url, auth=(self.api_key, self.api_secret))
			resp.raise_for_status()
			return resp.content
		except HTTPError:
			frappe.msgprint(
				_("An error occurred while downloading label from SendCloud"), indicator="orange", alert=True
			)

	def get_tracking_data(self, shipment_id):
		# return SendCloud tracking data
		shipment_id_list = shipment_id.split(", ")
		awb_number, tracking_status, tracking_urls = [], [], []

		for ship_id in shipment_id_list:
			try:
				response = requests.get(
					f"{PARCELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
					headers={"Accept": "application/json"},
				)
				response.raise_for_status()
				tracking_data = response.json()
			except Exception:
				show_error_alert("updating SendCloud Shipment")
				continue

			parcel_data = tracking_data.get("parcel", {})

			tracking_url = parcel_data.get("tracking_url")
			if tracking_url:
				tracking_urls.append(tracking_url)

			tracking_number = parcel_data.get("tracking_number")
			if tracking_number:
				awb_number.append(tracking_number)

			status_message = parcel_data.get("status", {}).get("message")
			if status_message:
				tracking_status.append(status_message)

		return {
			"awb_number": ", ".join(awb_number),
			"tracking_status": ", ".join(tracking_status),
			"tracking_status_info": ", ".join(tracking_status),
			"tracking_url": ", ".join(tracking_urls),
		}

	def total_parcel_price(self, parcel_price, parcels: list[dict]):
		count = 0
		for parcel in parcels:
			count += parcel.get("count")
		return flt(parcel_price) * count

	def get_service_dict(self, service, parcels: list[dict]):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = "SendCloud"
		available_service.carrier = service["carrier"]["name"]
		available_service.service_name = service["product"]["name"]
		available_service.service_id = service["code"]

		quotes = service.get("quotes", [])
		if quotes:
			price_data = quotes[0].get("price", {}).get("total", {})
			available_service.total_price = self.total_parcel_price(
				float(price_data.get("value", 0)), parcels
			)
			available_service.currency = price_data.get("currency")

		return available_service

	def get_carrier(self, carrier_name, post_or_get=None):
		# make 'sendcloud' => 'SendCloud' while displaying rates
		# reverse the same while creating shipment
		if carrier_name in ("sendcloud", "SendCloud"):
			return "SendCloud" if post_or_get == "get" else "sendcloud"
		else:
			return carrier_name.upper() if post_or_get == "get" else carrier_name.lower()

	def get_parcel(self, parcel, shipment, index):
		data = {
			"weight": {"value": flt(parcel.get("weight", 0), WEIGHT_DECIMALS), "unit": "kg"},
			"order_number": f"{shipment}-{index}",
		}

		length = parcel.get("length", 0)
		width = parcel.get("width", 0)
		height = parcel.get("height", 0)

		if length > 0 and width > 0 and height > 0:
			data["dimensions"] = {
				"length": length,
				"width": width,
				"height": height,
				"unit": "cm",
			}

		return data

	def extract_house_number(self, address):
		pattern = r"\b\d+[/-]?\w*(?:-\d+\w*)?\b"
		match = re.search(pattern, address)
		if match:
			house_number = match.group(0)
			cleaned_address = re.sub(pattern, "", address).strip()
			return house_number, cleaned_address
		else:
			return None, None

	def warn_partial_dimensions(self, parcel):
		dimensions = [parcel.get("length", 0), parcel.get("width", 0), parcel.get("height", 0)]
		if any(dim > 0 for dim in dimensions) and not all(dim > 0 for dim in dimensions):
			frappe.msgprint(
				_(
					"SendCloud ignores incomplete parcel dimensions; provide length, width, and height or leave all empty."
				),
				indicator="orange",
				alert=True,
			)

	def format_api_errors(self, errors):
		return "\n".join(
			f"Field: {(err.get('source') or {}).get('pointer', 'N/A')}, "
			+ f"Code: {err.get('code', 'N/A')}, "
			+ f"Detail: {err.get('detail', 'N/A')}"
			for err in errors
		)
