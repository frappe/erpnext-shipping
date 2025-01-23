import json

import frappe
import requests
from frappe import _
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import get_shipping_provider, show_error_alert

DELHIVERY_PROVIDER = "Delhivery"


class DelhiveryOneUtils:
	def __init__(self, company):
		settings = get_shipping_provider(company, "Delhiveryone")
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

	def get_available_services(self, delivery_address, pickup_address, weight):
		if not self.enable and not self.api_key:
			return []
		pickup_code = pickup_address.pincode
		delhivery_code = delivery_address.pincode
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
				"d_pin": delhivery_code,
				"o_pin": pickup_code,
				"cgm": weight * 1000,
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
		pickup_address,
		pickup_address_name,
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
		pickup_phone = frappe.db.get_value("Address", pickup_address_name, "phone")
		payload = {
			"data": {
				"pickup_location": {
					"add": pickup_address.address_title,
					"country": pickup_address.country_code.upper(),
					"pin": pickup_address.pincode,
					"phone": pickup_phone,
					"city": pickup_address.city,
					"name": pickup_address_name,
				},
				"shipments": shipments,
			},
		}
		json_data = json.dumps(payload["data"])
		formatted_payload = f"format=json&data={json_data}"
		try:
			response = requests.post(url, headers=headers, data=formatted_payload)
			shipment = response.json()
			if response.status_code == 200:
				awb = []
				for i in shipment["packages"]:
					awb.append(i["waybill"])
				return {
					"service_provider": "Delhivery",
					"shipment_id": ", ".join(awb),
					"carrier": "Delhivery",
					"carrier_service": service_info.get("service_name"),
					"shipment_amount": shipment["cod_amount"],
					"awb_number": ", ".join(awb),
				}
		except Exception:
			show_error_alert("creating Delhivery Shipment")

	def get_label(self, shipment_id):
		headers = {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}
		url = "https://track.delhivery.com/api/p/packing_slip"
		shipments = shipment_id.split(" ,")
		label_urls = []
		try:
			for ship_id in shipments:
				params = {"wbns": ship_id, "pdf": "true"}
				response = requests.get(url, headers=headers, params=params)
				if response.status_code == 200:
					shipment_label = json.loads(response.text)
					if shipment_label["packages"]:
						label_urls.append(shipment_label["packages"][0]["pdf_download_link"])
			if len(label_urls):
				return label_urls
		except Exception:
			show_error_alert("printing Delhivery Label")

	def get_tracking_data(self, shipment_id):
		headers = {"Content-Type": "application/json"}
		url = "https://track.delhivery.com/api/v1/packages/json"
		shipment_id_list = shipment_id.split(", ")
		try:
			awb_number, tracking_status, tracking_status_info = [], [], []
			for ship_id in shipment_id_list:
				params = {"token": self.api_key, "waybill": ship_id}
				response = requests.get(url, headers=headers, params=params)
				if response.status_code == 200:
					tracking_data = json.loads(response.text)
					if "ShipmentData" in tracking_data and tracking_data["ShipmentData"]:
						shipment = tracking_data["ShipmentData"][0]["Shipment"]

						awb_number.append(shipment.get("AWB", "N/A"))
						tracking_status.append(shipment["Status"]["Status"])
						tracking_status_info.append(shipment["Status"]["Instructions"])
			return {
				"awb_number": ", ".join(awb_number),
				"tracking_status": ", ".join(tracking_status),
				"tracking_status_info": ", ".join(tracking_status_info),
				"tracking_url": "",
			}
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
