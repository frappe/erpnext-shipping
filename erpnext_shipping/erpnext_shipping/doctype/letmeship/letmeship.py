# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt

import json
import re
from json import dumps as json_dumps

import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils.data import get_link_to_form

from erpnext_shipping.erpnext_shipping.utils import show_error_alert

LETMESHIP_PROVIDER = "LetMeShip"
PROD_BASE_URL = "https://api.letmeship.com/v1"
TEST_BASE_URL = "https://api.test.letmeship.com/v1"


class LetMeShip(Document):
	pass


class LetMeShipUtils:
	def __init__(self, base_url: str, api_id: str, api_password: str):
		self.base_url = base_url
		self.api_password = api_password
		self.api_id = api_id

	def request(self, method: str, endpoint: str, json: dict | None = None, params: dict | None = None):
		"""Make a request to LetMeShip API."""
		response = requests.request(
			method,
			f"{self.base_url}/{endpoint}",
			auth=(self.api_id, self.api_password),
			headers={
				"Content-Type": "application/json",
				"Accept": "application/json",
				"Access-Control-Allow-Origin": "string",
			},
			params=params,
			json=json,
		)

		data = response.json()
		if "status" in data and data["status"]["code"] != "0":
			frappe.throw(
				_("An Error occurred while fetching LetMeShip {0}:\n{1}").format(
					endpoint, json_dumps(data["status"], indent=4)
				)
			)

		return data

	def get_available_services(
		self,
		delivery_to_type,
		pickup_address,
		delivery_address,
		parcels,
		description_of_content,
		pickup_date,
		value_of_goods,
		pickup_contact=None,
		delivery_contact=None,
	):
		self.set_letmeship_specific_fields(pickup_contact, delivery_contact)
		pickup_address.address_title = self.first_30_chars(pickup_address.address_title)
		delivery_address.address_title = self.first_30_chars(delivery_address.address_title)
		parcel_list = self.get_parcel_list(parcels, description_of_content)
		payload = self.generate_payload(
			pickup_address=pickup_address,
			pickup_contact=pickup_contact,
			delivery_address=delivery_address,
			delivery_contact=delivery_contact,
			description_of_content=description_of_content,
			value_of_goods=value_of_goods,
			parcel_list=parcel_list,
			pickup_date=pickup_date,
		)

		try:
			response_data = self.request("POST", "available", json=payload)
			if "serviceList" in response_data and response_data["serviceList"]:
				available_services = []
				for response in response_data["serviceList"]:
					available_service = self.get_service_dict(response)
					available_services.append(available_service)

				return available_services
		except Exception:
			show_error_alert("fetching LetMeShip prices")

		return []

	def create_shipment(
		self,
		pickup_address,
		delivery_company_name,
		delivery_address,
		shipment_parcel,
		description_of_content,
		pickup_date,
		value_of_goods,
		service_info,
		pickup_contact=None,
		delivery_contact=None,
	):
		self.set_letmeship_specific_fields(pickup_contact, delivery_contact)
		pickup_address.address_title = self.first_30_chars(pickup_address.address_title)
		delivery_address.address_title = self.first_30_chars(
			delivery_company_name or delivery_address.address_title
		)
		parcel_list = self.get_parcel_list(json.loads(shipment_parcel), description_of_content)

		payload = self.generate_payload(
			pickup_address=pickup_address,
			pickup_contact=pickup_contact,
			delivery_address=delivery_address,
			delivery_contact=delivery_contact,
			description_of_content=description_of_content,
			value_of_goods=value_of_goods,
			parcel_list=parcel_list,
			pickup_date=pickup_date,
			service_info=service_info,
		)
		try:
			response_data = self.request("POST", "shipments", json=payload)
			if "shipmentId" in response_data:
				shipment_amount = response_data["service"]["baseServiceDetails"]["priceInfo"]["totalPrice"]
				shipment_id = response_data["shipmentId"]

				return {
					"service_provider": LETMESHIP_PROVIDER,
					"shipment_id": shipment_id,
					"carrier": response_data["service"]["baseServiceDetails"]["carrier"],
					"carrier_service": response_data["service"]["baseServiceDetails"]["name"],
					"shipment_amount": shipment_amount,
					"awb_number": self.get_awb_number(shipment_id),
				}
		except Exception:
			show_error_alert("creating LetMeShip Shipment")

	def get_awb_number(self, shipment_id: str):
		shipment_data = self.request("GET", f"shipments/{shipment_id}")
		if "trackingData" in shipment_data:
			return shipment_data["trackingData"].get("awbNumber", "")

		return ""

	def get_label(self, shipment_id):
		try:
			shipment_label_response_data = self.request(
				"GET", f"shipments/{shipment_id}/documents", params={"types": "LABEL"}
			)
			if "documents" in shipment_label_response_data:
				for label in shipment_label_response_data["documents"]:
					if "data" in label:
						return json.dumps(label["data"])
			else:
				frappe.throw(
					_("Error occurred while printing Shipment: {0}").format(
						shipment_label_response_data["message"]
					)
				)
		except Exception:
			show_error_alert("printing LetMeShip Label")

	def get_tracking_data(self, shipment_id):
		from erpnext_shipping.erpnext_shipping.utils import get_tracking_url

		try:
			tracking_data = self.request("GET", "tracking", params={"shipmentid": shipment_id})

			if "awbNumber" in tracking_data:
				tracking_status = "In Progress"
				if tracking_data["lmsTrackingStatus"].startswith("DELIVERED"):
					tracking_status = "Delivered"
				if tracking_data["lmsTrackingStatus"] == "RETURNED":
					tracking_status = "Returned"
				if tracking_data["lmsTrackingStatus"] == "LOST":
					tracking_status = "Lost"
				tracking_url = get_tracking_url(
					carrier=tracking_data["carrier"], tracking_number=tracking_data["awbNumber"]
				)
				return {
					"awb_number": tracking_data["awbNumber"],
					"tracking_status": tracking_status,
					"tracking_status_info": tracking_data["lmsTrackingStatus"],
					"tracking_url": tracking_url,
				}
			elif "message" in tracking_data:
				frappe.throw(
					_("Error occurred while updating Shipment: {0}").format(tracking_data["message"])
				)
		except Exception:
			show_error_alert("updating LetMeShip Shipment")

	def generate_payload(
		self,
		pickup_address,
		pickup_contact,
		delivery_address,
		delivery_contact,
		description_of_content,
		value_of_goods,
		parcel_list,
		pickup_date,
		service_info=None,
	):
		payload = {
			"pickupInfo": self.get_pickup_delivery_info(pickup_address, pickup_contact),
			"deliveryInfo": self.get_pickup_delivery_info(delivery_address, delivery_contact),
			"shipmentDetails": {
				"contentDescription": description_of_content,
				"shipmentType": "PARCEL",
				"shipmentSettings": {
					"saturdayDelivery": False,
					"ddp": False,
					"insurance": False,
					"pickupOrder": False,
					"pickupTailLift": False,
					"deliveryTailLift": False,
					"holidayDelivery": False,
				},
				"goodsValue": float(value_of_goods),
				"parcelList": parcel_list,
				"pickupInterval": {"date": pickup_date},
			},
		}

		if service_info:
			payload["service"] = {
				"baseServiceDetails": {
					"id": service_info["id"],
					"name": service_info["service_name"],
					"carrier": service_info["carrier"],
					"priceInfo": service_info["price_info"],
				},
				"supportedExWorkType": [],
				"messages": [""],
				"description": "",
				"serviceInfo": "",
			}
			payload["shipmentNotification"] = {
				"trackingNotification": {
					"deliveryNotification": True,
					"problemNotification": True,
					"emails": [],
					"notificationText": "",
				},
				"recipientNotification": {"notificationText": "", "emails": []},
			}
			payload["labelEmail"] = True
		return payload

	def first_30_chars(self, address_title: str):
		# LetMeShip has a limit of 30 characters for Company field
		return address_title[:30] if len(address_title) > 30 else address_title

	def get_service_dict(self, response):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		basic_info = response["baseServiceDetails"]
		price_info = basic_info["priceInfo"]
		available_service.service_provider = LETMESHIP_PROVIDER
		available_service.id = basic_info["id"]
		available_service.carrier = basic_info["carrier"]
		available_service.carrier_name = basic_info["carrier"]
		available_service.service_name = basic_info["name"]
		available_service.is_preferred = 0
		available_service.real_weight = price_info["realWeight"]
		available_service.total_price = price_info["netPrice"]
		available_service.price_info = price_info
		return available_service

	def set_letmeship_specific_fields(self, pickup_contact, delivery_contact):
		pickup_contact.phone_prefix = pickup_contact.phone[:3]
		pickup_contact.phone = re.sub("[^A-Za-z0-9]+", "", pickup_contact.phone[3:])

		pickup_contact.title = "MS"
		if pickup_contact.gender == "Male":
			pickup_contact.title = "MR"

		delivery_contact.phone_prefix = delivery_contact.phone[:3]
		delivery_contact.phone = re.sub("[^A-Za-z0-9]+", "", delivery_contact.phone[3:])

		delivery_contact.title = "MS"
		if delivery_contact.gender == "Male":
			delivery_contact.title = "MR"

	def get_parcel_list(self, parcels, description_of_content):
		parcel_list = []
		for parcel in parcels:
			formatted_parcel = {}
			formatted_parcel["height"] = parcel.get("height")
			formatted_parcel["width"] = parcel.get("width")
			formatted_parcel["length"] = parcel.get("length")
			formatted_parcel["weight"] = parcel.get("weight")
			formatted_parcel["quantity"] = parcel.get("count")
			formatted_parcel["contentDescription"] = description_of_content
			parcel_list.append(formatted_parcel)
		return parcel_list

	def get_pickup_delivery_info(self, address, contact):
		return {
			"address": {
				"countryCode": address.country_code,
				"zip": address.pincode,
				"city": address.city,
				"street": address.address_line1,
				"addressInfo1": address.address_line2,
				"houseNo": "",
			},
			"company": address.address_title,
			"person": {
				"title": contact.title,
				"firstname": contact.first_name,
				"lastname": contact.last_name,
			},
			"phone": {"phoneNumber": contact.phone, "phoneNumberPrefix": contact.phone_prefix},
			"email": contact.email_id,
		}


def get_letmeship_utils() -> "LetMeShipUtils":
	settings = frappe.get_single("LetMeShip")
	if not settings.enabled:
		link = get_link_to_form("LetMeShip", "LetMeShip", frappe.bold("LetMeShip Settings"))
		frappe.throw(_(f"Please enable LetMeShip Integration in {link}"), title=_("Mandatory"))

	return LetMeShipUtils(
		base_url=TEST_BASE_URL if settings.use_test_environment else PROD_BASE_URL,
		api_id=settings.api_id,
		api_password=settings.get_password("api_password"),
	)
