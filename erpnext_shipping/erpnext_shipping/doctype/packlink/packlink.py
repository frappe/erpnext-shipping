# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import json
import frappe
import requests
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from erpnext_shipping.erpnext_shipping.utils import show_error_alert

PACKLINK_PROVIDER = 'Packlink'

class Packlink(Document): pass

class PackLinkUtils():
	def __init__(self):
		self.api_key = get_decrypted_password('Packlink', 'Packlink', 'api_key', raise_exception=False)
		self.enabled = frappe.db.get_single_value('Packlink', 'enabled')

		if not self.enabled:
			link = frappe.utils.get_link_to_form('Packlink', 'Packlink', frappe.bold('Packlink Settings'))
			frappe.throw(_('Please enable Packlink Integration in {0}'.format(link)), title=_('Mandatory'))

	def get_available_services(self, pickup_address, delivery_address, shipment_parcel, pickup_date):
		# Retrieve rates at PackLink from specification stated.
		parcel_list = self.get_parcel_list(json.loads(shipment_parcel))
		shipment_parcel_params = self.get_formatted_parcel_params(parcel_list)
		url = self.get_formatted_request_url(pickup_address, delivery_address, shipment_parcel_params)

		if not self.api_key or not self.enabled:
			return []

		try:
			responses = requests.get(url, headers={'Authorization': self.api_key})
			responses_dict = json.loads(responses.text)
			# If an error occured on the api. Show the error message
			if 'messages' in responses_dict:
				error_message = str(responses_dict['messages'][0]['message'])
				frappe.throw(error_message, title=_("PackLink"))

			available_services = []
			for response in responses_dict:
				# display services only if available on pickup date
				if self.parse_pickup_date(pickup_date) in response['available_dates'].keys():
					available_service = self.get_service_dict(response)
					available_services.append(available_service)

			if responses_dict and not available_services:
				# got a response but no service available for given date
				frappe.throw(_("No Services available for {0}").format(pickup_date), title=_("PackLink"))

			return available_services
		except Exception:
			show_error_alert("fetching Packlink prices")

		return []

	def create_shipment(self, pickup_address, delivery_address, shipment_parcel,
		description_of_content, pickup_date, value_of_goods, pickup_contact,
		delivery_contact, service_info):
		# Create a transaction at PackLink
		data = {
			'additional_data': {
				'postal_zone_id_from': '',
				'postal_zone_name_from': pickup_address.country,
				'postal_zone_id_to': '',
				'postal_zone_name_to': delivery_address.country,
			},
			'collection_date': self.parse_pickup_date(pickup_date),
			'collection_time': '',
			'content': description_of_content,
			'contentvalue': value_of_goods,
			'content_second_hand': False,
			'from': self.get_shipment_address_contact_dict(pickup_address, pickup_contact),
			'insurance': {'amount': 0, 'insurance_selected': False},
			'price': {},
			'packages': self.get_parcel_list(json.loads(shipment_parcel)),
			'service_id': service_info['service_id'],
			'to': self.get_shipment_address_contact_dict(delivery_address, delivery_contact)
		}

		url = 'https://api.packlink.com/v1/shipments'
		headers = {
			'Authorization': self.api_key,
			'Content-Type': 'application/json'
		}
		try:
			response_data = requests.post(url, json=data, headers=headers)
			response_data = json.loads(response_data.text)
			if 'reference' in response_data:
				return {
					'service_provider': PACKLINK_PROVIDER,
					'shipment_id': response_data['reference'],
					'carrier': service_info['carrier'],
					'carrier_service': service_info['service_name'],
					'shipment_amount': service_info['actual_price'],
					'awb_number': '',
				}
		except Exception:
			show_error_alert("creating Packlink Shipment")

	def get_label(self, shipment_id):
		# Retrieve shipment label from PackLink
		headers = {
			'Authorization': self.api_key,
			'Content-Type': 'application/json'
		}
		try:
			shipment_label_response = requests.get(
				'https://api.packlink.com/v1/shipments/{id}/labels'.format(id=shipment_id),
				headers=headers
			)
			shipment_label = json.loads(shipment_label_response.text)
			if shipment_label:
				return shipment_label
			else:
				message = _("Please make sure Shipment (ID: {0}), exists and is a complete Shipment on Packlink.") \
					.format(shipment_id)
				frappe.msgprint(msg=_(message), title=_("Label Not Found"))
		except Exception:
			show_error_alert("printing Packlink Label")
		return []

	def get_tracking_data(self, shipment_id):
		# Get Packlink Tracking Info
		from erpnext_shipping.erpnext_shipping.utils import get_tracking_url

		headers = {
			'Authorization': self.api_key,
			'Content-Type': 'application/json'
		}
		try:
			url = 'https://api.packlink.com/v1/shipments/{id}'.format(id=shipment_id)
			tracking_data_response = requests.get(url, headers=headers)
			tracking_data = json.loads(tracking_data_response.text)
			if 'trackings' in tracking_data:
				tracking_status = 'In Progress'
				if tracking_data['state'] == 'DELIVERED':
					tracking_status = 'Delivered'
				if tracking_data['state'] == 'RETURNED':
					tracking_status = 'Returned'
				if tracking_data['state'] == 'LOST':
					tracking_status = 'Lost'
				awb_number = None if not tracking_data['trackings'] else tracking_data['trackings'][0]
				tracking_url = get_tracking_url(
					carrier=tracking_data['carrier'],
					tracking_number=awb_number
				)
				return {
					'awb_number': awb_number,
					'tracking_status': tracking_status,
					'tracking_status_info': tracking_data['state'],
					'tracking_url': tracking_url
				}
		except Exception:
			show_error_alert("updating Packlink Shipment")
		return []

	def get_formatted_request_url(self, pickup_address, delivery_address, shipment_parcel_params):
		"""Returns formatted request URL for Packlink."""
		url = 'https://api.packlink.com/v1/services?from[country]={from_country_code}&from[zip]={from_zip}&to[country]={to_country_code}&to[zip]={to_zip}&{shipment_parcel_params}sortBy=totalPrice&source=PRO'.format(
			from_country_code=pickup_address.country_code,
			from_zip=pickup_address.pincode,
			to_country_code=delivery_address.country_code,
			to_zip=delivery_address.pincode,
			shipment_parcel_params=shipment_parcel_params
		)
		return url

	def get_formatted_parcel_params(self, parcel_list):
		"""Returns formatted parcel params for Packlink URL."""
		shipment_parcel_params = ''
		for (index, parcel) in enumerate(parcel_list):
			shipment_parcel_params += 'packages[{index}][height]={height}&packages[{index}][length]={length}&packages[{index}][weight]={weight}&packages[{index}][width]={width}&'.format(
				index=index,
				height=parcel['height'],
				length=parcel['length'],
				weight=parcel['weight'],
				width=parcel['width']
			)
		return shipment_parcel_params

	def get_service_dict(self, response):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = PACKLINK_PROVIDER
		available_service.carrier = response['carrier_name']
		available_service.carrier_name = response['name']
		available_service.service_name = ''
		available_service.is_preferred = 0
		available_service.total_price = response['price']['base_price']
		available_service.actual_price = response['price']['total_price']
		available_service.service_id = response['id']
		available_service.available_dates = response['available_dates']
		return available_service

	def get_shipment_address_contact_dict(self, address, contact):
		"""Returns a dict with Address and Contact Info for Packlink Payload."""
		return {
			'city': address.city,
			'company': address.address_title,
			'country': address.country_code,
			'email': contact.email,
			'name': contact.first_name,
			'phone': contact.phone,
			'state': address.country,
			'street1': address.address_line1,
			'street2': address.address_line2,
			'surname': contact.last_name,
			'zip_code': address.pincode,
		}

	def get_parcel_list(self, shipment_parcel):
		parcel_list = []
		for parcel in shipment_parcel:
			for count in range(parcel.get('count')):
				formatted_parcel = {}
				formatted_parcel['height'] = parcel.get('height')
				formatted_parcel['width'] = parcel.get('width')
				formatted_parcel['length'] = parcel.get('length')
				formatted_parcel['weight'] = parcel.get('weight')
				parcel_list.append(formatted_parcel)
		return parcel_list

	def parse_pickup_date(self, pickup_date):
		return pickup_date.replace('-', '/')
