# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
from __future__ import unicode_literals
import frappe
import json
from six import string_types
from frappe import _
from frappe.utils import flt
from erpnext.accounts.party import get_party_shipping_address
from frappe.contacts.doctype.contact.contact import get_default_contact
from erpnext.stock.doctype.shipment.shipment import get_company_contact, get_address_name, get_contact_name
from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import LETMESHIP_PROVIDER, LetMeShipUtils
from erpnext_shipping.erpnext_shipping.doctype.packlink.packlink import PACKLINK_PROVIDER, PackLinkUtils
from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SENDCLOUD_PROVIDER, SendCloudUtils
from erpnext_shipping.erpnext_shipping.doctype.parcel_service_type.parcel_service_type import match_parcel_service_type_alias


def get_tracking_url(carrier, tracking_number):
	# Return the formatted Tracking URL.
	tracking_url = ''
	url_reference = frappe.get_value('Parcel Service', carrier, 'url_reference')
	if url_reference:
		tracking_url = frappe.render_template(url_reference, {'tracking_number': tracking_number})
	return tracking_url

@frappe.whitelist()
def fetch_shipping_rates(pickup_from_type, delivery_to_type, pickup_address_name, delivery_address_name,
		shipment_parcel, description_of_content, pickup_date, value_of_goods,
		pickup_contact_name=None, delivery_contact_name=None):
	# Return Shipping Rates for the various Shipping Providers
	shipment_prices = []
	letmeship_enabled = frappe.db.get_single_value('LetMeShip','enabled')
	packlink_enabled = frappe.db.get_single_value('Packlink','enabled')
	sendcloud_enabled = frappe.db.get_single_value('SendCloud','enabled')
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)

	if letmeship_enabled:
		pickup_contact = None
		delivery_contact = None
		if pickup_from_type != 'Company':
			pickup_contact = get_contact(pickup_contact_name)
		else:
			pickup_contact = get_company_contact(user=pickup_contact_name)

		if delivery_to_type != 'Company':
			delivery_contact = get_contact(delivery_contact_name)
		else:
			delivery_contact = get_company_contact(user=pickup_contact_name)

		letmeship = LetMeShipUtils()
		letmeship_prices = letmeship.get_available_services(
			delivery_to_type=delivery_to_type,
			pickup_address=pickup_address,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			pickup_date=pickup_date,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
		) or []
		letmeship_prices = match_parcel_service_type_carrier(letmeship_prices, ['carrier', 'carrier_name'])
		shipment_prices = shipment_prices + letmeship_prices

	if packlink_enabled:
		packlink = PackLinkUtils()
		packlink_prices = packlink.get_available_services(
			pickup_address=pickup_address,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			pickup_date=pickup_date
		) or []
		packlink_prices = match_parcel_service_type_carrier(packlink_prices, ['carrier_name', 'carrier'])
		shipment_prices = shipment_prices + packlink_prices

	if sendcloud_enabled and pickup_from_type == 'Company':
		sendcloud = SendCloudUtils()
		sendcloud_prices = sendcloud.get_available_services(
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel
		) or []
		shipment_prices = shipment_prices + sendcloud_prices
	shipment_prices = sorted(shipment_prices, key=lambda k:k['total_price'])
	return shipment_prices

@frappe.whitelist()
def create_shipment(shipment, pickup_from_type, delivery_to_type, pickup_address_name,
		delivery_address_name, shipment_parcel, description_of_content, pickup_date,
		value_of_goods, service_data, shipment_notific_email=None, tracking_notific_email=None,
		pickup_contact_name=None, delivery_contact_name=None, delivery_notes=[]):
	# Create Shipment for the selected provider
	service_info = json.loads(service_data)
	shipment_info, pickup_contact,  delivery_contact = None, None, None
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)

	if pickup_from_type != 'Company':
		pickup_contact = get_contact(pickup_contact_name)
	else:
		pickup_contact = get_company_contact(user=pickup_contact_name)

	if delivery_to_type != 'Company':
		delivery_contact = get_contact(delivery_contact_name)
	else:
		delivery_contact = get_company_contact(user=pickup_contact_name)

	if service_info['service_provider'] == LETMESHIP_PROVIDER:
		letmeship = LetMeShipUtils()
		shipment_info = letmeship.create_shipment(
			pickup_address=pickup_address,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			pickup_date=pickup_date,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
			service_info=service_info
		)

	if service_info['service_provider'] == PACKLINK_PROVIDER:
		packlink = PackLinkUtils()
		shipment_info = packlink.create_shipment(
			pickup_address=pickup_address,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			pickup_date=pickup_date,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)

	if service_info['service_provider'] == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipment_info = sendcloud.create_shipment(
			shipment=shipment,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			value_of_goods=value_of_goods,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)

	if shipment_info:
		fields = ['service_provider', 'carrier', 'carrier_service', 'shipment_id', 'shipment_amount', 'awb_number']
		for field in fields:
			frappe.db.set_value('Shipment', shipment, field, shipment_info.get(field))
		frappe.db.set_value('Shipment', shipment, 'status', 'Booked')

		if delivery_notes:
			update_delivery_note(delivery_notes=delivery_notes, shipment_info=shipment_info)

	return shipment_info

@frappe.whitelist()
def print_shipping_label(service_provider, shipment_id):
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = LetMeShipUtils()
		shipping_label = letmeship.get_label(shipment_id)
	elif service_provider == PACKLINK_PROVIDER:
		packlink = PackLinkUtils()
		shipping_label = packlink.get_label(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipping_label = sendcloud.get_label(shipment_id)
	return shipping_label

@frappe.whitelist()
def update_tracking(shipment, service_provider, shipment_id, delivery_notes=[]):
	# Update Tracking info in Shipment
	tracking_data = None
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = LetMeShipUtils()
		tracking_data = letmeship.get_tracking_data(shipment_id)
	elif service_provider == PACKLINK_PROVIDER:
		packlink = PackLinkUtils()
		tracking_data = packlink.get_tracking_data(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		tracking_data = sendcloud.get_tracking_data(shipment_id)

	if tracking_data:
		fields = ['awb_number', 'tracking_status', 'tracking_status_info', 'tracking_url']
		for field in fields:
			frappe.db.set_value('Shipment', shipment, field, tracking_data.get(field))

		if delivery_notes:
			update_delivery_note(delivery_notes=delivery_notes, tracking_info=tracking_data)

def update_delivery_note(delivery_notes, shipment_info=None, tracking_info=None):
	# Update Shipment Info in Delivery Note
	# Using db_set since some services might not exist
	if isinstance(delivery_notes, string_types):
		delivery_notes = json.loads(delivery_notes)

	delivery_notes = list(set(delivery_notes))

	for delivery_note in delivery_notes:
		dl_doc = frappe.get_doc('Delivery Note', delivery_note)
		if shipment_info:
			dl_doc.db_set('delivery_type', 'Parcel Service')
			dl_doc.db_set('parcel_service', shipment_info.get('carrier'))
			dl_doc.db_set('parcel_service_type', shipment_info.get('carrier_service'))
		if tracking_info:
			dl_doc.db_set('tracking_number', tracking_info.get('awb_number'))
			dl_doc.db_set('tracking_url', tracking_info.get('tracking_url'))
			dl_doc.db_set('tracking_status', tracking_info.get('tracking_status'))
			dl_doc.db_set('tracking_status_info', tracking_info.get('tracking_status_info'))

def get_address(address_name):
	fields = ['address_title', 'address_line1', 'address_line2', 'city', 'pincode', 'country']
	address = frappe.db.get_value('Address', address_name, fields, as_dict=1)
	address.country_code = frappe.db.get_value('Country', address.country, 'code').upper()

	if not address.pincode or address.pincode == '':
		frappe.throw(_("Postal Code is mandatory to continue. </br> \
				Please set Postal Code for Address <a href='#Form/Address/{0}'>{1}</a>"
			).format(address_name, address_name))
	address.pincode = address.pincode.replace(' ', '')
	address.city = address.city.strip()
	return address

def get_contact(contact_name):
	fields = ['first_name', 'last_name', 'email_id', 'phone', 'mobile_no', 'gender']
	contact = frappe.db.get_value('Contact', contact_name, fields, as_dict=1)

	if not contact.last_name:
		frappe.throw(_("Last Name is mandatory to continue. </br> \
				Please set Last Name for Contact <a href='#Form/Contact/{0}'>{1}</a>"
			).format(contact_name, contact_name))
	if not contact.phone:
		contact.phone = contact.mobile_no
	return contact

def match_parcel_service_type_carrier(shipment_prices, reference):
	for idx, prices in enumerate(shipment_prices):
		service_name = match_parcel_service_type_alias(prices.get(reference[0]), prices.get(reference[1]))
		is_preferred = frappe.db.get_value('Parcel Service Type', service_name, 'show_in_preferred_services_list')
		shipment_prices[idx].service_name = service_name
		shipment_prices[idx].is_preferred = is_preferred
	return shipment_prices

def update_tracking_info_daily():
	# Daily scheduled event to update Tracking info for not delivered Shipments
	# Also Updates the related Delivery Notes
	shipments = frappe.get_all('Shipment', filters={
		'docstatus': 1,
		'status': 'Booked',
		'shipment_id': ['!=', ''],
		'tracking_status': ['!=', 'Delivered'],
	})
	for shipment in shipments:
		shipment_doc = frappe.get_doc('Shipment', shipment.name)
		tracking_info = update_tracking(shipment_doc.service_provider, shipment_doc.shipment_id,
				shipment_doc.shipment_delivery_notes)

		if tracking_info:
			fields = ['awb_number', 'tracking_status', 'tracking_status_info', 'tracking_url']
			for field in fields:
				shipment_doc.db_set(field, tracking_info.get(field))