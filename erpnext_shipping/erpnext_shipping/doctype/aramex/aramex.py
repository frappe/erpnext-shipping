# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import json

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.doctype.aramex.constants import ARAMEX_BASE_URL, ARAMEX_VERSION
from erpnext_shipping.erpnext_shipping.utils import (
	get_enabled_doc_for_company,
	handle_shipping_error,
	save_label_as_attachment,
	validate_enabled_service,
)

ARAMEX_PROVIDER = "Aramex"


class Aramex(Document):
	def validate(self):
		if not self.enabled:
			return
		self.check_enabled()

	def check_enabled(self):
		self.enabled = validate_enabled_service(doctype=self.doctype, name=self.name, company=self.company)


class AramexUtils:
	def __init__(self, company):
		settings = get_enabled_doc_for_company(ARAMEX_PROVIDER, company)
		self.name = settings.name
		self.service_provider = settings.service_provider
		self.company = settings.company
		self.user = settings.api_id
		self.password = settings.get_password("api_password")
		self.account = settings.account_number
		self.account_pin = settings.get_password("account_pin")
		self.account_entity = settings.account_entity
		self.account_country_code = frappe.db.get_value("Country", settings.account_country, "code")
		self.version = settings.version

	def make_request(self, method, endpoint, payload=None, raise_exception=True):
		url = f"{ARAMEX_BASE_URL}/{ARAMEX_VERSION}/{endpoint}"
		headers = {"Content-Type": "application/json", "Accept": "application/json"}

		try:
			response = requests.request(
				method, url, headers=headers, data=json.dumps(payload) if payload else None
			)
			response.raise_for_status()
			return response.json()
		except HTTPError as http_err:
			handle_shipping_error(
				self.name, ARAMEX_PROVIDER, f"HTTP error occurred on {url} Aramex", http_err, raise_exception
			)
		except Exception as err:
			handle_shipping_error(
				self.name, ARAMEX_PROVIDER, f"An error occurred on {url} Aramex", err, raise_exception
			)
		return None

	def get_available_services(self, delivery_address, pickup_address, parcels, weight):
		payload = self.get_rate_payload(delivery_address, pickup_address, parcels, weight)
		response_data = self.make_request(
			"POST", "RateCalculator/Service_1_0.svc/json/CalculateRate", payload, raise_exception=False
		)
		available_services = []

		if response_data and not response_data.get("HasErrors"):
			rate_details = response_data.get("RateCalculatorResponse", [])
			for rate in rate_details:
				available_services.append(self.get_service_dict(rate.get("TotalAmount")))

		return available_services

	def create_shipment(self, **kwargs):
		payload = self.get_shipment_payload(**kwargs)
		response_data = self.make_request(
			"POST", "shippingapi.v2/shipping/service_1_0.svc/json/CreateShipments", payload
		)

		if response_data:
			shipments = response_data.get("Shipments", [{}])[0]
			if shipments.get("LabelURL"):
				save_label_as_attachment(shipment=kwargs["shipment"], url=shipments["LabelURL"])
			return {
				"service_provider": "Aramex",
				"shipment_id": shipments.get("ID", ""),
				"carrier": "Aramex",
				"carrier_service": "Priority Express",
				"shipment_amount": shipments.get("ShipmentDetails", {}).get("Charges", {}).get("Value", 0),
				"awb_number": shipments.get("ID", ""),
			}

	def get_tracking_data(self, shipment_id):
		payload = {
			"ClientInfo": self.get_client_info(),
			"GetLastTrackingUpdateOnly": True,
			"Shipments": [shipment_id],
		}
		response_data = self.make_request("GET", "Tracking/Service_1_0.svc/json/TrackShipments", payload)

		if response_data:
			tracking_results = response_data.get("TrackingResults", [{}])[0]
			return {
				"awb_number": tracking_results.get("WaybillNumber", ""),
				"tracking_status": tracking_results.get("UpdateDescription", ""),
				"tracking_status_info": tracking_results.get("Comments", ""),
				"tracking_url": "",
			}

	def get_service_dict(self, service):
		return {
			"service_provider": "Aramex",
			"carrier": "Aramex",
			"service_name": "Express",
			"total_price": service[0].get("Value", 0.0),
			"currency": "OMR",
			"service_id": "Express",
			"is_preferred": self.is_preferred,
		}

	def get_rate_payload(self, delivery_address, pickup_address, parcels, weight):
		return {
			"ClientInfo": self.get_client_info(),
			"DestinationAddress": self.get_address_payload(delivery_address),
			"OriginAddress": self.get_address_payload(pickup_address),
			"PreferredCurrencyCode": "OMR",
			"ShipmentDetails": {
				"ActualWeight": {"Value": weight, "Unit": "KG"},
				"ProductGroup": "DOM",
				"ProductType": "OND",
			},
			"Transaction": {"Reference1": "", "Reference2": ""},
		}

	def get_shipment_payload(self, **kwargs):
		client = self.get_client_info()

		required_fields = [
			"shipment",
			"delivery_company_name",
			"delivery_address",
			"delivery_contact_name",
			"description_of_content",
			"value_of_goods",
			"pickup_address",
			"pickup_address_name",
			"weight",
			"pickup_contact_name",
			"shipment_parcel",
		]

		missing_fields = [field for field in required_fields if not kwargs.get(field)]
		if missing_fields:
			frappe.throw(_("Missing required fields: {0}").format(", ".join(missing_fields)))

		pickup_contact = frappe.get_doc("Address", kwargs["pickup_contact_name"])
		delivery_contact = frappe.get_doc("Address", kwargs["delivery_contact_name"])

		dimensions = [
			{"Length": parcel.length, "Width": parcel.width, "Height": parcel.height, "Unit": "CM"}
			for parcel in kwargs["shipment_parcel"]
		]

		return {
			"ClientInfo": client,
			"shipment": [
				{
					"Reference1": "",
					"Reference2": "",
					"Reference3": "",
					"ForeignHAWB": "",
					"TransportType": 0,
					"Shipper": {
						"Reference1": "",
						"Reference2": "",
						"AccountNumber": client.get("AccountNumber"),
						"AccountEntity": client.get("AccountEntity"),
						"PartyAddress": {
							"Line1": kwargs["pickup_address"].get("address_line1", ""),
							"Line2": kwargs["pickup_address"].get("address_line2", ""),
							"City": kwargs["pickup_address"].get("city", ""),
							"StateOrProvinceCode": kwargs["pickup_address"].get("state", ""),
							"PostCode": kwargs["pickup_address"].get("pincode", ""),
							"CountryCode": kwargs["pickup_address"].get("country", "").upper(),
						},
						"Contact": {
							"PersonName": pickup_contact.full_name,
							"CompanyName": pickup_contact.full_name,
							"PhoneNumber1": pickup_contact.phone,
							"CellPhone": pickup_contact.mobile_no,
							"EmailAddress": pickup_contact.email,
						},
					},
					"Consignee": {
						"Reference1": "",
						"Reference2": "",
						"PartyAddress": {
							"Line1": kwargs["delivery_address"].get("address_line1", ""),
							"Line2": kwargs["delivery_address"].get("address_line2", ""),
							"City": kwargs["delivery_address"].get("city", ""),
							"StateOrProvinceCode": kwargs["delivery_address"].get("state", ""),
							"PostCode": kwargs["delivery_address"].get("pincode", ""),
							"CountryCode": kwargs["delivery_address"].get("country", "").upper(),
						},
						"Contact": {
							"PersonName": delivery_contact.full_name,
							"CompanyName": kwargs["delivery_company_name"],
							"PhoneNumber1": delivery_contact.phone,
							"CellPhone": delivery_contact.mobile_no,
							"EmailAddress": delivery_contact.email,
						},
					},
					"ShipmentDetails": {
						"DescriptionOfGoods": kwargs["description_of_content"],
						"GoodsValue": kwargs["value_of_goods"],
						"Weight": {"Value": kwargs["weight"], "Unit": "KG"},
						"Dimensions": dimensions,
					},
				}
			],
		}

	def get_address_payload(self, address):
		return {
			"City": address.city,
			"PostCode": address.pincode,
			"CountryCode": address.country_code.upper(),
		}

	def get_client_info(self):
		return {
			"UserName": self.user,
			"Password": self.password,
			"AccountNumber": self.account,
			"AccountPin": self.account_pin,
			"AccountEntity": self.account_entity,
			"AccountCountryCode": self.account_country_code.upper(),
			"Version": self.version,
		}
