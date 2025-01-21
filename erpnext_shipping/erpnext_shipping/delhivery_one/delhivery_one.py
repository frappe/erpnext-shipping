import frappe
import requests
from frappe import _
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import show_error_alert


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

	def get_available_services(self, delivery_address, pickup_address, weight=1000):
		if not self.enable and not self.api_key:
			return []
		self.get_waybill()
		pickup_code = pickup_address.pickup_code
		delhivery_code = pickup_address.delhivery_code

		headers = {"Content-Type": "application/json"}

		url = "https://staging-express.delhivery.com/api/kinko/v1/invoice/charges/json"
		params = {
			"token": self.api_key,
			"ss": "Delivered",
			"d_pin": delhivery_code,
			"o_pin": pickup_code,
			"cgm": weight,
			"pt": "Pre-paid",
			"cod": 0,
		}

		try:
			response = requests.get(url, headers=headers, params=params)

			print("Response Status Code:", response.status_code)
			print("Response Content:", response.text)

			if response.status_code == 200:
				return response.json()
			else:
				print(f"Failed to fetch waybill: {response.status_code} - {response.text}")
				return []
		except HTTPError as http_err:
			print(f"HTTP error occurred: {http_err}")
			return []
		except Exception as err:
			print(f"An error occurred: {err}")
			return []

	def get_waybill(self):
		headers = {"Content-Type": "application/json"}

		url = "https://track.delhivery.com/waybill/api/bulk/json/"
		params = {"token": self.api_key, "count": 1}

		try:
			response = requests.get(url, headers=headers, params=params)

			print("Response Status Code:", response.status_code)
			print("Response Content:", response.text)

			if response.status_code == 200:
				return response.json()
			else:
				print(f"Failed to fetch waybill: {response.status_code} - {response.text}")
				return []
		except HTTPError as http_err:
			print(f"HTTP error occurred: {http_err}")
			return []
		except Exception as err:
			print(f"An error occurred: {err}")
			return []
