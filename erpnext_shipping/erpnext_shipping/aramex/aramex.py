import json
import time
from datetime import datetime, timedelta

import frappe
import pytz
import requests
from frappe import _
from requests.exceptions import HTTPError

from erpnext_shipping.erpnext_shipping.utils import get_shipping_provider, save_lable, show_error_alert

ARAMEX_PROVIDER = "Aramex"


class AramexUtils:
	def __init__(self, company):
		settings = get_shipping_provider(company, "Aramex")
		settings = frappe.get_doc("Shipping Provider", settings["name"])
		self.service_provider = settings.service_provider
		self.company = settings.company
		self.user = settings.user_key
		self.password = settings.get_password("user_secret")
		self.account = settings.account_number
		self.account_pin = settings.get_password("account_pin")
		self.enable = settings.enable
		self.account_entity = settings.account_entity
		self.account_country_code = settings.account_country_entity
		self.version = settings.version
		self.source = settings.source

		if not self.enable:
			frappe.throw(_("Aramex Integration is disabled. Please enable it in Shipping Provider Settings."))

	def get_available_services(self, delivery_address, pickup_address, parcels, weight):
		if not self.enable:
			return []

		headers = {
			"Content-Type": "application/json",
			"Accept": "application/json",
		}
		url = "https://ws.sbx.aramex.net/ShippingAPI.V2/RateCalculator/Service_1_0.svc/json/CalculateRate"
		payload = self.get_rate_payload(delivery_address, pickup_address, parcels, weight)
		available_services = []
		try:
			response = requests.post(url, headers=headers, data=json.dumps(payload))
			response_data = response.json()
			if response.status_code == 200:
				if not response_data.get("HasErrors"):
					rate_details = response_data.get("RateCalculatorResponse")
					for rate in rate_details:
						available_service = self.get_service_dict(rate.get("TotalAmount"))
						available_services.append(available_service)
					return available_services
		except Exception:
			show_error_alert("fetching Aramex prices")

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
		delivery_contact_name,
		pickup_contact_name,
		weight,
	):
		headers = {
			"Content-Type": "application/json",
			"Accept": "application/json",
			"Authorization": "Basic Og==",
		}
		url = "https://ws.sbx.aramex.net/shippingapi.v2/shipping/service_1_0.svc/json/CreateShipments"
		parcel_data = self.get_shipment_payload(
			shipment,
			delivery_company_name,
			delivery_address,
			delivery_contact,
			service_info,
			description_of_content,
			value_of_goods,
			pickup_address,
			pickup_address_name,
			weight,
			shipment_parcel,
			delivery_contact_name,
			pickup_contact_name,
		)
		try:
			response = requests.request("POST", url, headers=headers, data=parcel_data)
			shipment = response.json()
			if response.status_code == 200:
				shipment_ids = [s.get("ID", "") for s in shipment.get("Shipments", [])]
				amount = (
					shipment.get("Shipments", [{}])[0]
					.get("ShipmentDetails", {})
					.get("Charges", {})
					.get("Value", 0)
				)

				save_lable(shipment, shipment.get("Shipments", [{}])[0].get("LabelURL", {}))
				return {
					"service_provider": "Aramex",
					"shipment_id": ", ".join(shipment_ids),
					"carrier": "Aramex",
					"carrier_service": "Priority Express",
					"shipment_amount": amount,
					"awb_number": ", ".join(shipment_ids),
				}
		except Exception:
			show_error_alert("creating Aramex shipment")

	def get_tracking_data(self, shipment_id):
		headers = {"Content-Type": "application/json", "Accept": "application/json"}
		url = "https://ws.sbx.aramex.net/ShippingAPI.V2/Tracking/Service_1_0.svc/json/TrackShipments"
		shipment_id_list = shipment_id.split(", ")
		try:
			awb_number, tracking_status, tracking_status_info = [], [], []
			for ship_id in shipment_id_list:
				params = {
					"ClientInfo": self.get_client_info(),
					"GetLastTrackingUpdateOnly": True,
					"Shipments": [ship_id],
					"Transaction": {
						"Reference1": "",
						"Reference2": "",
					},
				}
				response = requests.get(url, headers=headers, params=json.dumps(params))
				if response.status_code == 200:
					tracking_data = json.loads(response.text)
					if "TrackingResults" in tracking_data and tracking_data["TrackingResults"]:
						shipment = tracking_data["TrackingResults"][0]

						awb_number.append(shipment.get("WaybillNumber", ""))
						tracking_status.append(shipment.get("UpdateDescription", ""))
						tracking_status_info.append(shipment.get("Comments", ""))
			return {
				"awb_number": ", ".join(awb_number),
				"tracking_status": ", ".join(tracking_status),
				"tracking_status_info": ", ".join(tracking_status_info),
				"tracking_url": "",
			}
		except Exception:
			show_error_alert("updating Delhivery Shipment")

	def get_service_dict(self, service):
		available_service = frappe._dict()
		available_service.service_provider = "Aramex"
		available_service.carrier = "Aramex"
		available_service.service_name = "Express"
		available_service.total_price = service[0].get("Value", 0.0)

		available_service.currency = "OMR"
		available_service.service_id = "Express"

		return available_service

	def get_rate_payload(self, delivery_address, pickup_address, parcels, weight):
		"""Construct the payload for Aramex's Rate Calculator API."""
		return {
			"ClientInfo": self.get_client_info(),
			"DestinationAddress": {
				"Line1": "",
				"Line2": "",
				"Line3": "",
				"City": delivery_address.city,
				"StateOrProvinceCode": "",
				"PostCode": delivery_address.pincode,
				"CountryCode": delivery_address.country_code.upper(),
				"Longitude": 0,
				"Latitude": 0,
				"BuildingNumber": "",
				"BuildingName": "",
				"Floor": "null",
				"Apartment": "null",
				"POBox": "null",
				"Description": "null",
			},
			"OriginAddress": {
				"Line1": "null",
				"Line2": "null",
				"Line3": "null",
				"City": pickup_address.city,
				"StateOrProvinceCode": "",
				"PostCode": pickup_address.pincode,
				"CountryCode": pickup_address.country_code.upper(),
				"Longitude": 0,
				"Latitude": 0,
				"BuildingNumber": "",
				"BuildingName": "null",
				"Floor": "null",
				"Apartment": "null",
				"POBox": "null",
				"Description": "null",
			},
			"PreferredCurrencyCode": "OMR",
			"ShipmentDetails": {
				"Dimensions": {"Length": "", "Width": "", "Height": "", "Unit": ""},
				"ActualWeight": {"Value": weight, "Unit": "KG"},
				"ChargeableWeight": {"Value": weight, "Unit": "KG"},
				"DescriptionOfGoods": "gi",
				"GoodsOriginCountry": pickup_address.country_code.upper(),
				"NumberOfPieces": 1,
				"ProductGroup": "DOM",
				"ProductType": "OND",
				"PaymentType": "C",
				"PaymentOptions": "ASCC",
				"CustomsValueAmount": {"Value": 0, "CurrencyCode": ""},
				"CashOnDeliveryAmount": {"Value": 0, "CurrencyCode": ""},
				"InsuranceAmount": {"Value": 0, "CurrencyCode": ""},
				"CashAdditionalAmount": {"Value": 0, "CurrencyCode": ""},
				"CashAdditionalAmountDescription": "",
				"CollectAmount": {"Value": 0, "CurrencyCode": ""},
				"Services": "",
				"Items": [],
				"DeliveryInstructions": "null",
			},
			"Transaction": {
				"Reference1": "",
				"Reference2": "",
			},
		}

	def get_shipment_payload(
		self,
		shipment,
		delivery_company_name,
		delivery_address,
		delivery_contact,
		service_info,
		description_of_content,
		value_of_goods,
		pickup_address,
		pickup_address_name,
		weight,
		pickup_contact_name,
		delivery_contact_name,
		shipment_parcel,
	):
		client = self.get_client_info()
		pickup_contact = frappe.db.get_value(
			"Address", {pickup_contact_name}, ["full_name", "phone", "mobile_no", "email"], as_dict=True
		)
		delivery_contact = frappe.db.get_value(
			"Address", {delivery_contact_name}, ["full_name", "phone", "mobile_no", "email"], as_dict=True
		)
		pickup_date = frappe.db.get_value("Shipment", shipment, ["pickup_date", "pickup_from"], as_dict=True)
		pickup_datetime_str = f"{pickup_date.pickup_date} {pickup_date.pickup_from}"
		if "." not in pickup_datetime_str:
			pickup_datetime_str += ".000000"
		pickup_datetime = datetime.strptime(pickup_datetime_str, "%Y-%m-%d %H:%M:%S.%f")
		formatted_datetime = pickup_datetime.strftime("%Y-%m-%d %H:%M:%S")
		dimensions = []

		for parcel in shipment_parcel:
			dimensions.append(
				{"Length": parcel.length, "Width": parcel.width, "Height": parcel.height, "Unit": "CM"}
			)

		return {
			"ClientInfo": self.get_client_info(),
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
						"AccountNumber": client["AccountNumber"],
						"AccountEntity": client["AccountEntity"],
						"PartyAddress": {
							"Line1": pickup_address.address_line1 or "",
							"Line2": pickup_address.address_line2 or "",
							"Line3": "",
							"City": pickup_address.city or "",
							"StateOrProvinceCode": pickup_address.state or "",
							"PostCode": pickup_address.pincode or "",
							"CountryCode": pickup_address.country.upper(),
						},
						"Contact": {
							"Department": "",
							"PersonName": pickup_contact.full_name,
							"Title": "",
							"CompanyName": pickup_contact.full_name,
							"PhoneNumber1": pickup_contact.phone,
							"PhoneNumber1Ext": "",
							"PhoneNumber2": "",
							"FaxNumber": "",
							"CellPhone": pickup_contact.mobile_no,
							"EmailAddress": pickup_contact.email,
							"Type": "",
						},
					},
					"Consignee": {
						"Reference1": "",
						"Reference2": "",
						"AccountNumber": "",
						"AccountEntity": "",
						"PartyAddress": {
							"Line1": delivery_address.address_line1 or "",
							"Line2": delivery_address.address_line2 or "",
							"Line3": "",
							"City": delivery_address.city or "",
							"StateOrProvinceCode": delivery_address.state or "",
							"PostCode": delivery_address.pincode or "",
							"CountryCode": delivery_address.country.upper(),
						},
						"Contact": {
							"Department": "",
							"PersonName": delivery_contact.full_name,
							"Title": "",
							"CompanyName": delivery_contact.full_name,
							"PhoneNumber1": delivery_contact.phone,
							"PhoneNumber1Ext": "",
							"PhoneNumber2": "",
							"FaxNumber": "",
							"CellPhone": delivery_contact.mobile_no,
							"EmailAddress": delivery_contact.email,
							"Type": "",
						},
					},
					"ShippingDateTime": self.to_dotnet_datetime(formatted_datetime),
					"DueDate": self.to_dotnet_datetime(formatted_datetime),
					"Comments": description_of_content,
					"PickupLocation": pickup_address.city or "",
					"OperationsInstructions": "",
					"AccountsInstructions": "",
					"Details": {
						"Dimensions": dimensions,
						"ActualWeight": {"Value": weight, "Unit": "KG"},
						"ChargeableWeight": {"Value": weight, "Unit": "KG"},
						"NumberOfPieces": "",
						"ProductGroup": self.product_group,
						"ProductType": self.product_type,
						"PaymentType": self.payment_type,
						"PaymentOptions": self.payment_option,
						"Services": self.services,
						"DescriptionOfGoods": description_of_content,
						"GoodsOriginCountry": client["AccountCountryCode"],
						"CustomsValueAmount": {"Value": "", "CurrencyCode": "OMR"},
						"CashOnDeliveryAmount": {"Value": "", "CurrencyCode": "OMR"},
						"InsuranceAmount": {"Value": "", "CurrencyCode": ""},
						"CashAdditionalAmount": {"Value": "", "CurrencyCode": ""},
						"CashAdditionalDescription": "",
						"CollectAmount": {
							"Value": self.value_of_goods if self.value_of_goods else 0,
							"CurrencyCode": "OMR"
							if self.payment_type == "C" and self.payment_option == "ARCC"
							else "",
						},
						"Items": [],
					},
				}
			],
		}

	def to_dotnet_datetime(dt):
		if isinstance(dt, str):
			try:
				dt = datetime.strptime(dt, "%Y-%m-%d %H:%M:%S")
			except ValueError as e:
				raise ValueError(
					f"Invalid date string format: {dt}. Expected format: 'YYYY-MM-DD HH:MM:SS'."
				) from e

		if not isinstance(dt, datetime):
			raise TypeError(f"Expected a datetime object, got {type(dt)}.")

		utc_timezone = pytz.UTC
		dt = dt.replace(tzinfo=pytz.utc) if dt.tzinfo is None else dt.astimezone(utc_timezone)

		timestamp = int(time.mktime(dt.timetuple()) * 1000)

		return f"/Date({timestamp})/"

	def get_client_info(self):
		"""Construct the client information for Aramex API requests."""
		return {
			"UserName": self.user,
			"Password": self.password,
			"Version": self.version,
			"AccountNumber": self.account,
			"AccountPin": self.account_pin,
			"AccountEntity": self.account_entity,
			"AccountCountryCode": self.account_country_code,
			"Source": self.source,
		}
