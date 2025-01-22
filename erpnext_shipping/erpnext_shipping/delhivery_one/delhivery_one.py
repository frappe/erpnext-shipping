import json

import frappe
import requests
from frappe import _
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

DELHIVERY_PROVIDER = "Delhivery"


class DelhiveryOneUtils:
	def __init__(self):
		settings = frappe.get_doc("Shipping Provider", "c0jp3n9u1g")
		self.service_provider = settings.service_provider
		self.company = settings.company
		self.api_key = settings.get_password("api_key")
		self.enable = settings.enable

		if not self.enable:
			frappe.throw(
				"Delhivery One Integration is disabled. Please enable it in Shipping Provider Settings."
			)

	def get_availability(self, pickup_code):
		headers = {"Content-Type": "application/json"}
		url = "https://track.delhivery.com/c/api/pin-codes/json/"
		params = {"token": self.api_key, "filter_codes": pickup_code}
		try:
			response = requests.get(url, headers=headers, params=params)
			if response.status_code == 200:
				service_data = response.json()
				if "delivery_codes" in service_data and service_data["delivery_codes"]:
					return True
				else:
					return False
		except Exception:
			show_error_alert("fetching Delhivery availability")

	def get_available_services(self, delivery_address, pickup_address, weight=1000):
		if not self.enable and not self.api_key:
			return []
		self.get_waybill()
		pickup_code = pickup_address.pincode
		delhivery_code = pickup_address.pincode
		if not self.get_availability(pickup_code):
			return []
		headers = {"Content-Type": "application/json", "Authorization": f"Token {self.api_key}"}

		url = "https://track.delhivery.com/api/kinko/v1/invoice/charges/.json"
		services = []
		available_services = []

		for mode in ["S", "E"]:
			params = {
				"md": mode,
				"ss": "Delivered",
				"d_pin": pickup_code,
				"o_pin": 201016,
				"cgm": weight,
			}

			try:
				response = requests.get(url, headers=headers, params=params)

				if response.status_code == 200:
					services.append({mode: response.json()})

			except Exception:
				show_error_alert("fetching Delhivery prices")

		for rates in services:
			available_service = self.get_service_dict(rates)
			available_services.append(available_service)
		return available_services

	def get_waybill(self):
		headers = {"Content-Type": "application/json"}

		url = "https://track.delhivery.com/waybill/api/bulk/json/"
		params = {"token": self.api_key, "count": 1}

		try:
			response = requests.get(url, headers=headers, params=params)

			if response.status_code == 200:
				return response.text
		except Exception:
			show_error_alert("fetching waybill")

	def create_shipment(
		self,
		shipment,
		delivery_company_name,
		delivery_address,
		shipment_parcel,
		description_of_content,
		value_of_goods,
		delivery_contact,
		service_info,
	):
		headers = {
			"Authorization": f"Token {self.api_key}",
			"Content-Type": "application/json",
		}
		url = "https://track.delhivery.com/api/cmu/create.json"
		shipments = []
		for i, parcel in enumerate(json.loads(shipment_parcel), start=1):
			parcel_data = self.get_parcel_dict(
				shipment,
				parcel,
				i,
				delivery_company_name,
				delivery_address,
				delivery_contact,
				service_info,
				description_of_content,
				value_of_goods,
			)
			shipments.append(parcel_data)
		payload = {
			"format": "json",
			"data": {
				"pickup_location": {
					"add": delivery_address.address_line1,
					"country": delivery_address.country_code,
					"pin": delivery_address.pincode,
					"phone": delivery_contact.phone,
					"city": delivery_address.city,
					"name": delivery_company_name or delivery_address.address_title,
					"state": delivery_address.state,
				},
				"shipments": shipments,
			},
		}

		try:
			response = requests.post(url, headers=headers, json=payload)

			if response.status_code == 200:
				return
				# return {
				# 	"service_provider": "Delhivery",
				# 	"shipment_id": 1234,
				# 	"carrier": "Delhivery",
				# 	"carrier_service": "Surface",
				# 	"shipment_amount": 100,
				# 	"awb_number": 12345,
				# }
		except Exception:
			show_error_alert("creating Delhivery Shipment")

	def get_label(self, shipment_id):
		headers = {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}
		url = "https://track.delhivery.com/api/p/packing_slip"
		params = {"wbns": shipment_id, "pdf": "true"}
		try:
			response = requests.get(url, headers=headers, params=params)
			if response.status_code == 200:
				return []
		except Exception:
			show_error_alert("printing Delhivery Label")

	def get_tracking_data(self, shipment_id):
		headers = {"Content-Type": "application/json"}
		url = "https://track.delhivery.com/api/v1/packages/json"
		params = {"token": self.api_key, "waybill": self.get_waybill()}
		try:
			response = requests.get(url, headers=headers, params=params)
			if response.status_code == 200:
				tracking_data = response.json()
				return tracking_data
				# return {
				# 	"awb_number": ", ".join(awb_number),
				# 	"tracking_status": ", ".join(tracking_status),
				# 	"tracking_status_info": ", ".join(tracking_status_info),
				# 	"tracking_url": ", ".join(tracking_urls),
				# }
		except Exception:
			show_error_alert("updating Delhivery Shipment")

	def get_service_dict(self, service):
		service_type = next(iter(service))
		service_details = service[service_type][0]

		available_service = frappe._dict()
		available_service.service_provider = "Delhivery"
		available_service.carrier = "Delhivery"
		available_service.service_name = "Surface" if service_type == "S" else "Express"
		available_service.total_price = service_details.get("total_amount", 0.0)
		available_service.currency = "INR"
		available_service.service_id = service_type

		return available_service

	def get_parcel_dict(
		self,
		shipment,
		parcel,
		index,
		delivery_company_name,
		delivery_address,
		delivery_contact,
		service_info,
		description_of_content,
		value_of_goods,
	):
		return {
			"name": f"{delivery_contact.first_name} {delivery_contact.last_name}",
			"country": delivery_address.country_code,
			"city": delivery_address.city,
			"add": delivery_address.address_line1,
			"pin": delivery_address.pincode,
			"phone": delivery_contact.phone,
			"payment_mode": "Prepaid",
			"cod_amount": 0,
			"quantity": parcel.get("count"),
			"order": f"{shipment}-{index}",
		}
