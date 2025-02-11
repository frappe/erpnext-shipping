import json

import frappe
import requests

from erpnext_shipping.erpnext_shipping.shiprocket.utils import (
	SHIPROCKET_API_BASE_URL,
	get_order_creation_payload,
)
from erpnext_shipping.erpnext_shipping.utils import (
	custom_frappe_throw,
	get_pickup_location,
	get_shipping_provider,
)

SHIPROCKET_PROVIDER = "Shiprocket"


class ShiprocketUtils:
	def __init__(self, company):
		self.doc = get_shipping_provider(company, "Shiprocket")
		self.bearer_token = self.doc.get("bearer_key")
		self.company = self.doc.get("company")
		self.name = self.doc.get("name")
		if not self.bearer_token:
			self.generate_token()

	def _get_headers(self):
		"""Generate headers for API requests."""
		return {
			"Content-Type": "application/json",
			"Authorization": f"Bearer {self.bearer_token}",
		}

	def get_shiprocket_shipping_address(self, address_id=None):
		try:
			url = f"{SHIPROCKET_API_BASE_URL}/settings/company/pickup"

			headers = self._get_headers()

			response = requests.request("GET", url, headers=headers)
			response_dict = json.loads(response.text)
			data = response_dict.get("data")
			if data:
				shipping_address = data["shipping_address"]
				if not address_id:
					return shipping_address
				for address in shipping_address:
					if str(address["id"]) == address_id:
						return address
		except Exception:
			frappe.log_error("Error fetching shipping address from Shiprocket", frappe.get_traceback())
			custom_frappe_throw(
				self.name, "Shiprocket", "Something went wrong check error log for more details"
			)

	def get_available_services(self, parcels, delivery_address_name, pickup_address_name, total_weight):
		weight = total_weight
		pickup_postcode = self.get_post_code(pickup_address_name)
		delivery_postcode = self.get_post_code(delivery_address_name)
		try:
			url = f"{SHIPROCKET_API_BASE_URL}/courier/serviceability"
			headers = self._get_headers()
			payload = {
				"pickup_postcode": pickup_postcode,
				"delivery_postcode": delivery_postcode,
				"cod": 0,
				"weight": weight,
			}
			response = requests.get(url, json=payload, headers=headers)
			response_dict = response.json()
			if response.status_code == 200:
				services_available = response.json().get("data", [])
				services_available = services_available["available_courier_companies"]
				available_services = []
				for services in services_available:
					available_service = self.get_service_dict(services, parcels)
					available_services.append(available_service)
				return available_services
			elif response.status_code == 401:
				self.generate_token()
				return self.get_available_services(
					parcels, delivery_address_name, pickup_address_name, total_weight
				)
			else:
				frappe.log_error("Shiprocket Error", response_dict["message"])
				custom_frappe_throw(self.name, "Shiprocket", response_dict["message"])
				return []
		except Exception:
			frappe.log_error("Shiprocket error in fetching services", frappe.get_traceback())
			custom_frappe_throw(
				self.name, "Shiprocket", "Something went wrong check error log for more details"
			)

	def get_post_code(self, address_name):
		if not frappe.db.exists("Address", address_name):
			return
		address = frappe.get_doc("Address", address_name)
		return address.pincode

	def get_service_dict(self, service, parcels: list[dict]):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = "Shiprocket"
		available_service.carrier = str(service["courier_company_id"])
		available_service.service_name = service["courier_name"]
		available_service.currency = "INR"
		price = service["freight_charge"]
		available_service.total_price = self.total_parcel_price(price, parcels)

		available_service.service_id = service["id"]

		return available_service

	def total_parcel_price(self, price, parcels):
		count = 0
		for parcel in parcels:
			count += parcel.get("count")
		return float(price) * count

	def create_shiprocket_shipment(self, **kwargs):
		shipment = frappe.get_doc("Shipment", kwargs.get("shipment"))
		pickup_location_id = get_pickup_location(kwargs.get("pickup_company"), "Shiprocket")
		pickup_address = self.get_shiprocket_shipping_address(pickup_location_id)
		if not shipment or not pickup_location_id or not pickup_address:
			frappe.throw("Missing required shipment details.")

		try:
			url = f"{SHIPROCKET_API_BASE_URL}/orders/create/adhoc"

			payload = get_order_creation_payload(
				shipment.name,
				kwargs["pickup_date"],
				kwargs["delivery_company_name"],
				kwargs["delivery_contact"],
				kwargs["delivery_address"],
				kwargs["pickup_contact"],
				pickup_address,
				json.loads(kwargs["shipment_parcel"]),
				kwargs["description_of_content"],
				kwargs["value_of_goods"],
				kwargs["service_info"],
				kwargs["total_weight"],
			)

			headers = self._get_headers()
			response = requests.post(url, json=payload, headers=headers)
			response_data = response.json()
			if response.status_code == 200:
				if response_data.get("status") == "CANCELED":
					frappe.throw("Could not make the shippment")

				shipment_id = response_data.get("shipment_id")

				if shipment_id:
					return self._assign_awb(shipment_id, kwargs["service_info"])
			elif response.status_code == 401:
				self.generate_token()
				return self.create_shiprocket_shipment(**kwargs)
			else:
				frappe.log_error("Shiprocket error in creating order", str(response_data))
				custom_frappe_throw(self.name, "Shiprocket", "Shiprocket error in creating order")
		except Exception:
			frappe.log_error("Shiprocket error in creating order", frappe.get_traceback())
			custom_frappe_throw(
				self.name, "Shiprocket", "Something went wrong check error log for more details"
			)

	def _assign_awb(self, shipment_id, service_info):
		try:
			url = f"{SHIPROCKET_API_BASE_URL}/courier/assign/awb"
			payload = {
				"shipment_id": shipment_id,
				"courier_id": service_info.get("carrier"),
			}

			header = self._get_headers()

			response = requests.post(url, json=payload, headers=header)
			response_dict = response.json()
			if response.status_code == 200:
				if not response_dict["awb_assign_status"]:
					frappe.throw("Cannot create shipment, Shipping order clreated")
				ship_now_response_data = response_dict["response"]
				return {
					"service_provider": "Shiprocket",
					"shipment_id": shipment_id,
					"carrier": "Shiprocket",
					"carrier_service": service_info["service_name"],
					"shipment_amount": service_info["total_price"],
					"awb_number": ship_now_response_data["data"]["awb_code"],
				}
			elif response.status_code == 401:
				self.generate_token()
				return self._assign_awb(shipment_id, service_info)
			else:
				frappe.log_error("Failed to move shipment to 'Ship Now'", str(response_dict))
				custom_frappe_throw(
					self.name, "Shiprocket", "Something went wrong check error log for more details"
				)
		except Exception:
			frappe.log_error("Shiprocket error in moving shipment to 'Ship Now'", frappe.get_traceback())
			custom_frappe_throw(
				self.name, "Shiprocket", "Something went wrong check error log for more details"
			)

	def generate_lable(self, shipment_id):
		try:
			url = f"{SHIPROCKET_API_BASE_URL}/courier/generate/label"

			payload = json.dumps({"shipment_id": [shipment_id]})
			headers = self._get_headers()

			response = requests.request("POST", url, headers=headers, data=payload)
			response_dict = response.json()
			if response.status_code == 200:
				if not response_dict["label_created"]:
					return None
				return response_dict["label_url"]
			elif response.status_code == 401:
				self.generate_token()
				return self.generate_lable(shipment_id)
			else:
				frappe.log_error("Unable to generate shiprocket label", str(response_dict))
				custom_frappe_throw(self.name, "Shiprocket", response_dict["message"])

		except Exception:
			frappe.log_error("Error generating shiprock label", frappe.get_traceback())
			custom_frappe_throw(
				self.name, "Shiprocket", "Something went wrong check error log for more details"
			)

	def get_tracking_data(self, shipment_id):
		try:
			url = f"{SHIPROCKET_API_BASE_URL}/courier/track/shipment/{shipment_id}"

			headers = self._get_headers()

			response = requests.request("GET", url, headers=headers)
			response_dict = response.json()

			if response.status_code == 200:
				if not response_dict.get("tracking_data", {}).get("track_status"):
					frappe.throw("Failed to track order")

				tracking_data = response_dict["tracking_data"]

				awb_numbers = [track.get("awb_code", "") for track in tracking_data.get("shipment_track", [])]
				tracking_status = [
					track.get("current_status", "") for track in tracking_data.get("shipment_track", [])
				]
				tracking_status_info = [
					track.get("courier_name", "") for track in tracking_data.get("shipment_track", [])
				]
				tracking_urls = [tracking_data.get("track_url", "")]

				return {
					"awb_number": ", ".join(filter(None, awb_numbers)),
					"tracking_status": ", ".join(filter(None, tracking_status)),
					"tracking_status_info": ", ".join(filter(None, tracking_status_info)),
					"tracking_url": ", ".join(filter(None, tracking_urls)),
				}
			elif response.status_code == 401:
				self.generate_token()
				return self.get_tracking_data(shipment_id)
			else:
				custom_frappe_throw(self.name, "Shiprocket", response_dict["message"])
		except Exception:
			frappe.log_error("Error tracking shiprocket order", frappe.get_traceback())

	def generate_token(self):
		docname = self.name
		doc = frappe.get_doc("Shipping Provider", docname)
		try:
			url = f"{SHIPROCKET_API_BASE_URL}/auth/login"

			payload = json.dumps({"email": doc.user_key, "password": doc.get_password("user_secret")})
			headers = {"Content-Type": "application/json"}

			response = requests.request("POST", url, headers=headers, data=payload)
			response_dict = json.loads(response.text)
			if response.status_code == 200:
				token = response_dict["token"]
				self.bearer_token = token
				frappe.db.set_value("Shipping Provider", docname, "bearer_key", token)
			else:
				frappe.throw(f"Shiprocket: {response_dict['message']}")

		except requests.exceptions.RequestException as e:
			frappe.log_error(title="Shiprocket Authentication Error", message=str(e))
