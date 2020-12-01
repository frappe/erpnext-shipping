# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import requests
import frappe
import json
from frappe import _
from frappe.utils import flt
from frappe.model.document import Document

SENDCLOUD_PROVIDER = 'SendCloud'

class SendCloud(Document):
	pass
class SendCloudUtils():
	def get_settings_data(self):
		api_key, api_secret, enabled = frappe.db.get_value('SendCloud', 'SendCloud',
			['api_key', 'api_secret', 'enabled'])
		if not enabled:
			link = frappe.utils.get_link_to_form('SendCloud', 'SendCloud', frappe.bold('SendCloud Settings'))
			frappe.throw(_('Please enable SendCloud Integration in {0}'.format(link)), title=_('Mandatory'))

		return api_key, api_secret, enabled

	def get_available_services(self, delivery_address, shipment_parcel):
		# Retrieve rates at SendCloud from specification stated.
		api_key, api_secret, enabled = self.get_settings_data()
		if not enabled or not api_key or not api_secret:
			return []

		try:
			url = 'https://panel.sendcloud.sc/api/v2/shipping_methods'
			responses = requests.get(url, auth=(api_key, api_secret))
			responses_dict = json.loads(responses.text)

			available_services = []
			for service in responses_dict['shipping_methods']:
				for country in service['countries']:
					if country['iso_2'] == delivery_address.country_code:
						available_service = self.get_service_dict(service, country, shipment_parcel)
						available_services.append(available_service)

			return available_services
		except Exception:
			self.show_error_alert("fetching SendCloud prices")

	def create_shipment(self, shipment, delivery_address, delivery_contact, service_info, shipment_parcel,
		description_of_content, value_of_goods):
		# Create a transaction at SendCloud
		api_key, api_secret, enabled = self.get_settings_data()
		if not enabled or not api_key or not api_secret:
			return []

		parcels = []
		for i, parcel in enumerate(json.loads(shipment_parcel), start=1):
			parcel_data = {
				'name': "{} {}".format(delivery_contact.first_name, delivery_contact.last_name),
				'company_name': delivery_address.address_title,
				'address': delivery_address.address_line1,
				'address_2': delivery_address.address_line2 or '',
				'city': delivery_address.city,
				'postal_code': delivery_address.pincode,
				'telephone': delivery_contact.phone,
				'request_label': True,
				'email': delivery_contact.email,
				'data': [],
				'country': delivery_address.country_code,
				'shipment': {
					'id': service_info['service_id']
				},
				'order_number': "{}-{}".format(shipment, i),
				'external_reference': "{}-{}".format(shipment, i),
				'weight': parcel.get('weight'),
				'parcel_items': self.get_parcel_items(parcel, description_of_content, value_of_goods)
			}
			parcels.append(parcel_data)
		data = { 'parcels': parcels }
		try:
			url = 'https://panel.sendcloud.sc/api/v2/parcels?errors=verbose'
			response_data = requests.post(url, json=data, auth=(api_key, api_secret))
			response_data = json.loads(response_data.text)
			if 'failed_parcels' in response_data:
				error = response_data['failed_parcels'][0]['errors']
				frappe.msgprint(_('Error occurred while creating Shipment: {0}').format(error),
					indicator='orange', alert=True)
			else:
				shipment_id = ', '.join([str(x['id']) for x in response_data['parcels']])
				awb_number = ', '.join([str(x['tracking_number']) for x in response_data['parcels']])
				return {
					'service_provider': 'SendCloud',
					'shipment_id': shipment_id,
					'carrier': service_info['carrier'],
					'carrier_service': service_info['service_name'],
					'shipment_amount': service_info['total_price'],
					'awb_number': awb_number
				}
		except Exception:
			self.show_error_alert("creating Shipment")

	def get_label(self, shipment_id):
		# Retrieve shipment label from SendCloud
		api_key, api_secret, enabled = self.get_settings_data()
		shipment_id_list = shipment_id.split(', ')
		label_urls = []
		for ship_id in shipment_id_list:
			shipment_label_response = \
				requests.get('https://panel.sendcloud.sc/api/v2/labels/{id}'.format(id=ship_id), auth=(api_key, api_secret))
			shipment_label = json.loads(shipment_label_response.text)
			label_urls.append(shipment_label['label']['label_printer'])
		if len(label_urls):
			return label_urls
		else:
			message = _("Please make sure Shipment (ID: {0}), exists and is a complete Shipment on SendCloud.").format(shipment_id)
			frappe.msgprint(msg=_(message), title=_("Label Not Found"))

	def get_tracking_data(self, shipment_id):
		# return SendCloud tracking data
		api_key, api_secret, enabled = self.get_settings_data()
		try:
			shipment_id_list = shipment_id.split(', ')
			awb_number, tracking_status, tracking_status_info, tracking_urls = [], [], [], []

			for ship_id in shipment_id_list:
				tracking_data_response = \
					requests.get('https://panel.sendcloud.sc/api/v2/parcels/{id}'.format(id=ship_id),
						auth=(api_key, api_secret))
				tracking_data = json.loads(tracking_data_response.text)
				tracking_urls.append(tracking_data['parcel']['tracking_url'])
				awb_number.append(tracking_data['parcel']['tracking_number'])
				tracking_status.append(tracking_data['parcel']['status']['message'])
				tracking_status_info.append(tracking_data['parcel']['status']['message'])
			return {
				'awb_number': ', '.join(awb_number),
				'tracking_status': ', '.join(tracking_status),
				'tracking_status_info': ', '.join(tracking_status_info),
				'tracking_url': ', '.join(tracking_urls)
			}
		except Exception:
			self.show_error_alert("updating Shipment")

	def total_parcel_price(self, parcel_price, shipment_parcel):
		count = 0
		for parcel in shipment_parcel:
			count += parcel.get('count')
		return flt(parcel_price) * count

	def get_parcel_items(self, parcel, description_of_content, value_of_goods):
		parcel_list = []
		formatted_parcel = {}
		formatted_parcel['description'] = description_of_content
		formatted_parcel['quantity'] = parcel.get('count')
		formatted_parcel['weight'] = parcel.get('weight')
		formatted_parcel['value'] = value_of_goods
		parcel_list.append(formatted_parcel)
		return parcel_list

	def get_service_dict(self, service, country, shipment_parcel):
		"""Returns a dictionary with service info."""
		available_service = frappe._dict()
		available_service.service_provider = 'SendCloud'
		available_service.carrier = service['carrier']
		available_service.service_name = service['name']
		available_service.total_price = self.total_parcel_price(country['price'], json.loads(shipment_parcel))
		available_service.service_id = service['id']
		return available_service

	def show_error_alert(self, action):
		log = frappe.log_error(frappe.get_traceback())
		link_to_log = frappe.utils.get_link_to_form("Error Log", log.name, "See what happened.")
		frappe.msgprint(_('An Error occurred while {0}. {1}').format(action, link_to_log), indicator='orange', alert=True)