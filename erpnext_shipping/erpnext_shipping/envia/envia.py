import json

import frappe
import requests

from erpnext_shipping.erpnext_shipping.utils import custom_frappe_throw, get_shipping_provider, save_lable

ENVIA_PROVIDER = "Envia"
BASE_URL_API = "https://api-test.envia.com/"
BASE_URL_QUERY = "https://queries-test.envia.com"


class Enviautils:
	def __init__(self, company):
		self.doc = get_shipping_provider(company, "Envia")
		self.name = self.doc.get("name")
		doc = frappe.get_doc("Shipping Provider", self.name)
		self.api_key = doc.get_password("api_key")

	def get_avilabele_couriers(self, countryCode, international):
		try:
			url = f"{BASE_URL_QUERY}/available-carrier/{countryCode}/{international}"

			payload = {}
			headers = {"Authorization": f"Bearer {self.api_key}"}

			response = requests.request("GET", url, headers=headers, data=payload)
			response_dict = response.json()
			carrier_list = []
			if response.status_code == 200:
				carriers = response_dict["data"]
				for carrier in carriers:
					carrier_list.append(carrier["name"])

			else:
				custom_frappe_throw(
					self.name, "Envia", "Something went wrong check error log for more details"
				)

			return carrier_list

		except Exception:
			frappe.log_error("Error fetching available couriers from Envia", frappe.get_traceback())
			custom_frappe_throw(self.name, "Envia", "Something went wrong check error log for more details")

	def get_avilable_services(
		self,
		pickup_address,
		delivery_address,
		parcels,
		pickup_contact_name,
		pickup_contact,
		delivery_contact,
		value_of_goods,
		total_weight,
	):
		from_country = pickup_address.get("country_code", "").upper()
		to_country = delivery_address.get("country_code", "").upper()
		carriers = self.get_avilabele_couriers(from_country, 0)
		origin = self.get_address(from_country, pickup_contact, pickup_address, "TN")
		destination = self.get_address(to_country, delivery_contact, delivery_address, "TN")
		packages = self.get_packages(parcels, total_weight, value_of_goods)
		available_services = []

		for carrier in carriers:
			try:
				url = f"{BASE_URL_API}/ship/rate/"

				payload = json.dumps(
					{
						"origin": origin,
						"destination": destination,
						"packages": packages,
						"shipment": {"carrier": carrier, "type": 1},
						"settings": {
							"printFormat": "PDF",
							"printSize": "STOCK_4X6",
							"currency": "INR",
							"cashOnDelivery": value_of_goods,
							"comments": "Handle with care",
						},
						"additionalServices": [],
					}
				)

				headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

				response = requests.request("POST", url, headers=headers, data=payload)
				response_dict = response.json()
				if response.status_code == 200:
					data = response_dict.get("data")
					if data:
						for service in data:
							available_service = self.get_service_dict(service, parcels)
							available_services.append(available_service)
				else:
					frappe.log_error("Error creating shipment with Envia", str(response_dict))
			except Exception:
				frappe.log_error("Error creating shipment with Envia", frappe.get_traceback())

		return available_services

	def create_shipment(self, **kwargs):
		from_country = kwargs["pickup_address"].get("country_code", "").upper()
		to_country = kwargs["delivery_address"].get("country_code", "").upper()
		parcels = json.loads(kwargs["shipment_parcel"])
		origin = self.get_address(from_country, kwargs["pickup_contact"], kwargs["pickup_address"], "TN")
		destination = self.get_address(
			to_country, kwargs["delivery_contact"], kwargs["delivery_address"], "TN"
		)
		packages = self.get_packages(parcels, kwargs["total_weight"], kwargs["value_of_goods"])
		try:
			url = f"{BASE_URL_API}/ship/generate/"
			payload = json.dumps(
				{
					"origin": origin,
					"destination": destination,
					"packages": packages,
					"shipment": {
						"carrier": kwargs["service_info"].get("carrier"),
						"service": kwargs["service_info"].get("service_id"),
						"type": 1,
					},
					"settings": {
						"printFormat": "PDF",
						"printSize": "STOCK_4X6",
						"comments": "Handle with care",
					},
				}
			)

			headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

			response = requests.request("POST", url, headers=headers, data=payload)
			response_dict = response.json()
			if response.status_code == 200:
				data = response_dict.get("data")
				if data:
					save_lable(kwargs["shipment"], data[0].get("label"))

					return {
						"service_provider": "Envia",
						"shipment_id": data[0].get("shipmentId"),
						"carrier": "Envia",
						"carrier_service": data[0].get("carrier"),
						"shipment_amount": data[0].get("totalPrice"),
						"awb_number": data[0].get("trackingNumber"),
					}
			else:
				frappe.log_error("Error creating shipment with Envia", str(response_dict))
		except Exception:
			frappe.log_error("Error creating shipment with Envia", frappe.get_traceback())

	def get_address(self, country, contact, address, state):
		if not (country and contact and address):
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
			"phone": f"{phone}",
			"street": address.get("address_line1"),
			"number": address.get("address_line2"),
			"district": address.get("city"),
			"city": address.get("city"),
			"state": state,
			"category": 1,
			"country": country,
			"postalCode": address.get("pincode"),
			"reference": "",
		}

	def get_packages(self, parcel, total_weight, value_of_goods):
		return [
			{
				"content": "Electronics",
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
			for item in parcel
		]

	def get_service_dict(self, service, parcels: list[dict]):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = "Envia"
		available_service.carrier = str(service["carrier"])
		available_service.service_name = service["serviceDescription"]
		available_service.currency = service["currency"]
		available_service.total_price = service["totalPrice"]

		available_service.carrier_id = service["carrierId"]
		available_service.service_id = service["service"]

		return available_service

	def get_tracking_data(self, awb_number):
		try:
			url = f"{BASE_URL_API}/ship/generaltrack/"

			payload = json.dumps({"trackingNumbers": [awb_number]})
			headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}

			response = requests.request("POST", url, headers=headers, data=payload)
			response_dict = response.json()
			if response.status_code == 200:
				data = response_dict.get("data")
				if data:
					return {
						"awb_number": awb_number,
						"tracking_status": data[0].get("status"),
						"tracking_status_info": data[0].get("trackingNumber"),
						"tracking_url": data[0].get("trackUrlSite"),
					}
			else:
				frappe.log_error("Error getting tracking data with Envia", str(response_dict))

		except Exception:
			frappe.log_error("Error getting tracking data with Envia", frappe.get_traceback())
