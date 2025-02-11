import json
from datetime import datetime

import frappe
import requests
from frappe import _
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import get_shipping_provider, show_error_alert

SHIPPO_PROVIDER = "Shippo"


class ShippoUtils:
	def __init__(self, company):
		settings = get_shipping_provider(company, "Shippo")
		settings = frappe.get_doc("Shipping Provider", settings["name"])
		self.service_provider = settings.service_provider
		self.company = settings.company
		self.api_key = settings.get_password("user_secret")
		self.enable = settings.enable

		if not self.enable:
			frappe.throw(
				"Shippo One Integration is disabled. Please enable it in Shipping Provider Settings."
			)

	def get_available_services(
		self,
		delivery_address,
		pickup_address,
		parcels,
		delivery_address_name,
		pickup_address_name,
		description_of_content,
	):
		if not self.enable and not self.api_key:
			return []

		from_address = self.get_address(pickup_address, pickup_address_name, description_of_content)
		to_address = self.get_address(delivery_address, delivery_address_name, description_of_content)
		url = "https://api.goshippo.com/shipments/"
		headers = {"Authorization": f"ShippoToken {self.api_key}", "Content-Type": "application/json"}
		rates = self.get_rate_payload(parcels)
		shipment_date = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
		payload = json.dumps(
			{
				"parcels": rates,
				"address_from": from_address,
				"address_to": to_address,
				"object_purpose": "PURCHASE",
				"async": "false",
				"shipment_date": shipment_date,
			}
		)
		available_services = []
		try:
			response = requests.post(url, headers=headers, data=payload)
			response = response.json()
			if response["status"]:
				for rates in response["rates"]:
					available_service = self.get_service_dict(rates)
					available_services.append(available_service)
			return available_services
		except Exception:
			show_error_alert("fetching Shippo prices")

	def get_address(self, address, address_name, description_of_content):
		if address_name:
			address_id = frappe.db.get_value("Address", address_name, ["state", "phone"], as_dict=True)
		url = "https://api.goshippo.com/addresses/"
		headers = {"Authorization": f"ShippoToken {self.api_key}", "Content-Type": "application/json"}
		payload = json.dumps(
			{
				"name": address.address_title,
				"company": "",
				"street1": address.address_line1,
				"street2": address.address_line2,
				"city": address.city,
				"state": address_id.state,
				"zip": address.pincode,
				"country": address.country_code.upper(),
				"phone": address_id.phone,
				"metadata": description_of_content,
				"validate": True,
				"object_purpose": "PURCHASE",
			}
		)
		try:
			response = requests.post(url, headers=headers, data=payload)
			response = response.json()
			if response["validation_results"].get("is_valid"):
				return response["object_id"]
		except Exception:
			show_error_alert("fetching Shippo Address")

	def create_shipment(self, shipment, service_info):
		url = "https://api.goshippo.com//transactions"
		headers = {"Authorization": f"ShippoToken {self.api_key}", "Content-Type": "application/json"}
		payload = {
			"rate": service_info["service_id"],
			"async": "false",
			"label_file_type": "PDF_4x6",
			"metadata": "",
		}
		try:
			response = requests.post(url, headers=headers, json=payload)
			response = response.json()
			if response["status"] == "SUCCESS":
				return {
					"service_provider": "Shippo",
					"shipment_id": response["object_id"],
					"carrier": service_info["carrier"],
					"carrier_service": service_info["service_name"],
					"shipment_amount": service_info["total_price"],
					"awb_number": response["tracking_number"],
				}
		except Exception:
			show_error_alert("creating Shippo Shipment")

	def get_label(self, shipment_id, shipment):
		url = f"https://api.goshippo.com/transactions/{shipment_id}"
		headers = {"Authorization": f"ShippoToken {self.api_key}", "Content-Type": "application/json"}
		label_urls = []
		try:
			response = requests.get(url, headers=headers)
			response = response.json()
			if response["status"] == "SUCCESS":
				label_urls.append(response["label_url"])
				tracking_url = response.get("tracking_url_provider", "")
				frappe.db.set_value("Shipment", shipment, "tracking_url", tracking_url)
			if len(label_urls):
				return label_urls
		except Exception:
			show_error_alert("printing Shippo Label")

	def get_tracking_data(self, awb, carrier, tracking_url):
		url = f"https://api.goshippo.com/tracks/{carrier}/{awb}"
		headers = headers = {
			"Authorization": f"ShippoToken {self.api_key}",
			"Content-Type": "application/json",
		}
		try:
			response = requests.get(url, headers=headers)
			if response.status_code == 200:
				response = response.json()
				a = {
					"awb_number": awb,
					"tracking_status": response["tracking_status"].get("status"),
					"tracking_status_info": response["tracking_status"].get("status_details"),
					"tracking_url": tracking_url,
				}
		except Exception:
			show_error_alert("updating Shippo Shipment")

	def get_rate_payload(self, parcels):
		payload = []
		for parcel in parcels:
			payload.append(
				{
					"height": parcel.get("height", 0),
					"distance_unit": "cm",
					"length": parcel.get("length", 0),
					"width": parcel.get("width", 0),
					"weight": parcel.get("weight", 0),
					"mass_unit": "kg",
				}
			)

		return payload

	def get_service_dict(self, rates):
		available_service = frappe._dict()
		available_service.service_provider = "Shippo"
		available_service.carrier = rates.get("provider")
		available_service.service_name = rates.get("servicelevel").get("display_name")
		available_service.total_price = rates.get("amount")
		available_service.service_id = rates.get("object_id")

		return available_service
