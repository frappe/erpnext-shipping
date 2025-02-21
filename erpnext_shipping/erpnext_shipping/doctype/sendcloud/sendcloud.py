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
FETCH_SHIPPING_OPTIONS_URL = f"{BASE_URL}/v3/fetch-shipping-options"
SHIPMENTS_URL = f"{BASE_URL}/v3/shipments"
SHIPMENTS_ANNOUNCE_URL = f"{BASE_URL}/v3/shipments/announce"
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

		to_country = delivery_address.country_code.upper()
		from_country = pickup_address.country_code.upper()

		payload = {
			"to_country_code": to_country,
			"from_country_code": from_country,
			"weight": {"value": max_weight, "unit": "kg"},
			"dimensions": {"length": max_length, "width": max_width, "height": max_height, "unit": "cm"},
		}

		try:
			response = requests.post(
				FETCH_SHIPPING_OPTIONS_URL,
				json=payload,
				auth=(self.api_key, self.api_secret),
				headers={"Accept": "application/json", "Content-Type": "application/json"},
			)

			response_data = response.json()

			if "error" in response_data:
				error_message = response_data["error"]["message"]
				frappe.throw(error_message, title=_("SendCloud"))

			if "data" not in response_data or not response_data["data"]:
				frappe.throw(_("No shipping options found for this destination."), title=_("Sendcloud"))

			available_services = []
			for service in response_data["data"]:
				available_service = self.get_service_dict(service, parcels)
				available_services.append(available_service)

			return available_services
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
		for i, parcel in enumerate(json.loads(shipment_parcel), start=1):
			parcel_count = parcel.get("count", 1)
			for j in range(parcel_count):
				parcel_data = self.get_parcel(
					parcel,
					shipment,
					i,
				)
				parcels.append(parcel_data)

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
			},
			"ship_with": {
				"type": "shipping_option_code",
				"properties": {
					"shipping_option_code": service_info["service_id"],
				},
			},
		}

		if service_info.get("multicollo"):
			# Multicollo Logic: All packages are processed in a single API call
			try:
				response = requests.post(
					SHIPMENTS_URL,
					json=payload,
					auth=(self.api_key, self.api_secret),
				)
				response_data = response.json()
				if "errors" in response_data and response_data["errors"]:
					error_details = [
						f"Code: {err.get('code', 'N/A')}, Detail: {err.get('detail', 'N/A')}"
						for err in response_data["errors"]
					]
					error_message = "\n".join(error_details)
					frappe.msgprint(
						_("Error occurred while creating shipment for parcel {0}:").format(
							parcel.get("order_number")
						)
						+ f"\n{error_message}",
						indicator="red",
						alert=True,
					)
					return None

				parcels_data = response_data.get("data", {}).get("parcels", [])
				if parcels_data:
					shipment_ids = [str(parcel["id"]) for parcel in parcels_data]
					tracking_numbers = [parcel.get("tracking_number") or "" for parcel in parcels_data]
					tracking_urls = [parcel.get("tracking_url") or "" for parcel in parcels_data]
					return {
						"service_provider": "SendCloud",
						"shipment_id": ", ".join(shipment_ids),
						"carrier": self.get_carrier(service_info["carrier"], post_or_get="post"),
						"carrier_service": service_info["service_name"],
						"shipment_amount": service_info["total_price"],
						"awb_number": ", ".join(tracking_numbers),
						"tracking_url": ", ".join(tracking_urls),
					}
			except Exception:
				show_error_alert("creating SendCloud Shipment (multicollo)")
		else:
			# Non-Multicollo Logic: A separate API call is made for each package
			shipments_results = []
			for parcel in parcels:
				payload_single = payload.copy()
				payload_single["parcels"] = [parcel]
				try:
					response = requests.post(
						SHIPMENTS_ANNOUNCE_URL,
						json=payload_single,
						auth=(self.api_key, self.api_secret),
					)
					response_data = response.json()
					if "errors" in response_data and response_data["errors"]:
						error_details = [
							f"Code: {err.get('code', 'N/A')}, Detail: {err.get('detail', 'N/A')}"
							for err in response_data["errors"]
						]
						error_message = "\n".join(error_details)
						frappe.msgprint(
							_("Error occurred while creating shipment for parcel {0}:").format(
								parcel.get("order_number")
							)
							+ f"\n{error_message}",
							indicator="red",
							alert=True,
						)
						continue

					parcels_data = response_data.get("data", {}).get("parcels", [])
					if parcels_data:
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
					show_error_alert(f"creating SendCloud Shipment for parcel {parcel.get('order_number')}")
			if shipments_results:
				combined_result = {
					"service_provider": "SendCloud",
					"shipment_id": ", ".join(item["shipment_id"] for item in shipments_results),
					"carrier": shipments_results[0]["carrier"],
					"carrier_service": shipments_results[0]["carrier_service"],
					"shipment_amount": service_info["total_price"],
					"awb_number": ", ".join(item["awb_number"] for item in shipments_results),
					"tracking_url": ", ".join(item["tracking_url"] for item in shipments_results),
				}
				return combined_result

		return None

	def get_label(self, shipment_id):
		# Retrieve shipment label from SendCloud
		shipment_id_list = shipment_id.split(", ")
		label_urls = []

		try:
			for ship_id in shipment_id_list:
				shipment_label_response = requests.get(
					f"{LABELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
				)
				shipment_label = json.loads(shipment_label_response.text)
				label_urls.append(shipment_label["label"]["label_printer"])
			if len(label_urls):
				return label_urls
			else:
				message = _(
					"Please make sure Shipment (ID: {0}), exists and is a complete Shipment on SendCloud."
				).format(shipment_id)
				frappe.msgprint(msg=_(message), title=_("Label Not Found"))
		except Exception:
			show_error_alert("printing SendCloud Label")

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
		try:
			shipment_id_list = shipment_id.split(", ")
			awb_number, tracking_status, tracking_status_info, tracking_urls = [], [], [], []

			for ship_id in shipment_id_list:
				tracking_data_response = requests.get(
					f"{PARCELS_URL}/{ship_id}",
					auth=(self.api_key, self.api_secret),
				)
				tracking_data = json.loads(tracking_data_response.text)
				tracking_data_parcel = tracking_data["parcel"]
				tracking_data_parcel_status = tracking_data_parcel["status"]["message"]
				tracking_url = tracking_data_parcel.get("tracking_url")
				if tracking_url:
					tracking_urls.append(tracking_url)
				tracking_number = tracking_data_parcel.get("tracking_number")
				if tracking_number:
					awb_number.append(tracking_number)
				tracking_status.append(tracking_data_parcel_status)
				tracking_status_info.append(tracking_data_parcel_status)
			return {
				"awb_number": ", ".join(awb_number),
				"tracking_status": ", ".join(tracking_status),
				"tracking_status_info": ", ".join(tracking_status_info),
				"tracking_url": ", ".join(tracking_urls),
			}
		except Exception:
			show_error_alert("updating SendCloud Shipment")

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
		available_service.multicollo = service["functionalities"].get("multicollo", False)

		price = 0
		if "quotes" in service and service["quotes"]:
			price = float(service["quotes"][0]["price"]["total"]["value"])
			available_service.total_price = self.total_parcel_price(price, parcels)

		return available_service

	def get_carrier(self, carrier_name, post_or_get=None):
		# make 'sendcloud' => 'SendCloud' while displaying rates
		# reverse the same while creating shipment
		if carrier_name in ("sendcloud", "SendCloud"):
			return "SendCloud" if post_or_get == "get" else "sendcloud"
		else:
			return carrier_name.upper() if post_or_get == "get" else carrier_name.lower()

	def get_parcel(self, parcel, shipment, index):
		return {
			"dimensions": {
				"length": parcel.get("length", 0),
				"width": parcel.get("width", 0),
				"height": parcel.get("height", 0),
				"unit": "cm",
			},
			"weight": {"value": flt(parcel.get("weight", 0), WEIGHT_DECIMALS), "unit": "kg"},
			"order_number": f"{shipment}-{index}",
		}

	def extract_house_number(self, address):
		pattern = r"\b\d+[/-]?\w*(?:-\d+\w*)?\b"
		match = re.search(pattern, address)
		if match:
			house_number = match.group(0)
			cleaned_address = re.sub(pattern, "", address).strip()
			return house_number, cleaned_address
		else:
			return None, None
