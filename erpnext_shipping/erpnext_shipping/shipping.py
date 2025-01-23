# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe
from erpnext.stock.doctype.shipment.shipment import get_company_contact

from erpnext_shipping.erpnext_shipping.aramex.aramex import AramexUtils
from erpnext_shipping.erpnext_shipping.delhivery_one.delhivery_one import (
	DELHIVERY_PROVIDER,
	DelhiveryOneUtils,
)
from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import (
	LETMESHIP_PROVIDER,
	get_letmeship_utils,
)
from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SENDCLOUD_PROVIDER, SendCloudUtils
from erpnext_shipping.erpnext_shipping.shiprocket.shiprocket import (
	SHIPROCKET_PROVIDER,
	ShiprocketUtils,
)
from erpnext_shipping.erpnext_shipping.utils import (
	get_address,
	get_contact,
	get_shipping_provider,
	match_parcel_service_type_carrier,
)


@frappe.whitelist()
def fetch_shipping_rates(
	pickup_from_type,
	delivery_to_type,
	pickup_address_name,
	delivery_address_name,
	parcels,
	description_of_content,
	pickup_date,
	value_of_goods,
	pickup_contact_name=None,
	delivery_contact_name=None,
	pickup_company=None,
	total_weight=None,
):
	# Return Shipping Rates for the various Shipping Providers
	shipment_prices = []
	letmeship_enabled = frappe.db.get_single_value("LetMeShip", "enabled")
	sendcloud_enabled = frappe.db.get_single_value("SendCloud", "enabled")
	delhivery_one_enabled = frappe.db.get_value("Shipping Provider", "c0jp3n9u1g", "enable")
	aramex_enabled = frappe.db.get_value("Shipping Provider", "7s5mnr0hbc", "enable")
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	parcels = json.loads(parcels)

	if pickup_company:
		shipping_providers = get_shipping_provider(pickup_company, "Shiprocket")
		if shipping_providers:
			shiprocket = ShiprocketUtils(company=pickup_company)
			shiprocket_prices = shiprocket.get_available_services(
				parcels,
				delivery_address_name,
				pickup_address_name,
				total_weight,
			)
			shiprocket_prices = match_parcel_service_type_carrier(
				shiprocket_prices, "carrier", "service_name"
			)
			shipment_prices += shiprocket_prices
	if letmeship_enabled:
		pickup_contact = None
		delivery_contact = None
		if pickup_from_type != "Company":
			pickup_contact = get_contact(pickup_contact_name)
		else:
			pickup_contact = get_company_contact(user=pickup_contact_name)
			pickup_contact.email_id = pickup_contact.pop("email", None)

		if delivery_to_type != "Company":
			delivery_contact = get_contact(delivery_contact_name)
		else:
			delivery_contact = get_company_contact(user=pickup_contact_name)
			delivery_contact.email_id = delivery_contact.pop("email", None)

		letmeship = get_letmeship_utils()
		letmeship_prices = (
			letmeship.get_available_services(
				delivery_to_type=delivery_to_type,
				pickup_address=pickup_address,
				delivery_address=delivery_address,
				parcels=parcels,
				description_of_content=description_of_content,
				pickup_date=pickup_date,
				value_of_goods=value_of_goods,
				pickup_contact=pickup_contact,
				delivery_contact=delivery_contact,
			)
			or []
		)
		letmeship_prices = match_parcel_service_type_carrier(letmeship_prices, "carrier", "service_name")
		shipment_prices += letmeship_prices

	if sendcloud_enabled and pickup_from_type == "Company":
		sendcloud = SendCloudUtils()
		sendcloud_prices = (
			sendcloud.get_available_services(delivery_address=delivery_address, parcels=parcels) or []
		)
		sendcloud_prices = match_parcel_service_type_carrier(sendcloud_prices, "carrier", "service_name")
		shipment_prices += sendcloud_prices

	if delhivery_one_enabled and pickup_from_type == "Company":
		delhivery = DelhiveryOneUtils()
		delhivery_prices = (
			delhivery.get_available_services(
				delivery_address=delivery_address, pickup_address=pickup_address, weight=10
			)
			or []
		)
		delhivery_prices = match_parcel_service_type_carrier(delhivery_prices, "carrier", "service_name")
		shipment_prices += delhivery_prices

	shipment_prices = sorted(shipment_prices, key=lambda k: k["total_price"])
	return shipment_prices


@frappe.whitelist()
def create_shipment(
	shipment,
	pickup_from_type,
	delivery_to_type,
	pickup_address_name,
	delivery_address_name,
	shipment_parcel,
	description_of_content,
	pickup_date,
	value_of_goods,
	service_data,
	total_weight,
	shipment_notific_email=None,
	tracking_notific_email=None,
	pickup_contact_name=None,
	delivery_contact_name=None,
	delivery_notes=None,
	pickup_company=None,
):
	# Create Shipment for the selected provider
	if delivery_notes is None:
		delivery_notes = []

	service_info = json.loads(service_data)
	shipment_info, pickup_contact, delivery_contact = None, None, None
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	delivery_company_name = get_delivery_company_name(shipment)

	if pickup_from_type != "Company":
		pickup_contact = get_contact(pickup_contact_name)
	else:
		pickup_contact = get_company_contact(user=pickup_contact_name)
		pickup_contact.email_id = pickup_contact.pop("email", None)

	if delivery_to_type != "Company":
		delivery_contact = get_contact(delivery_contact_name)
	else:
		delivery_contact = get_company_contact(user=pickup_contact_name)
		pickup_contact.email_id = pickup_contact.pop("email", None)

	if service_info["service_provider"] == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipment_info = letmeship.create_shipment(
			pickup_address=pickup_address,
			delivery_company_name=delivery_company_name,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			pickup_date=pickup_date,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)

	if service_info["service_provider"] == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipment_info = sendcloud.create_shipment(
			shipment=shipment,
			delivery_company_name=delivery_company_name,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			value_of_goods=value_of_goods,
			delivery_contact=delivery_contact,
			service_info=service_info,
		)
	if service_info["service_provider"] == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils()
		shipment_info = delhivery.create_shipment(
			shipment=shipment,
			delivery_company_name=delivery_company_name,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			value_of_goods=value_of_goods,
			delivery_contact=delivery_contact,
			service_info=service_info,
			pickup_address=pickup_address,
			pickup_address_name=pickup_address_name,
		)

	if service_info["service_provider"] == SHIPROCKET_PROVIDER:
		shiprocket = ShiprocketUtils(company=pickup_company)
		shipment_info = shiprocket.create_shiprocket_shipment(
			shipment=shipment,
			delivery_company_name=delivery_company_name,
			delivery_address=delivery_address,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			pickup_date=pickup_date,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
			service_info=service_info,
			pickup_company=pickup_company,
			total_weight=total_weight,
		)

	if shipment_info:
		shipment = frappe.get_doc("Shipment", shipment)
		shipment.db_set(
			{
				"service_provider": shipment_info.get("service_provider"),
				"carrier": shipment_info.get("carrier"),
				"carrier_service": shipment_info.get("carrier_service"),
				"shipment_id": shipment_info.get("shipment_id"),
				"shipment_amount": shipment_info.get("shipment_amount"),
				"awb_number": shipment_info.get("awb_number"),
				"status": "Booked",
			}
		)

		if delivery_notes:
			update_delivery_note(delivery_notes=delivery_notes, shipment_info=shipment_info)

	return shipment_info


def get_delivery_company_name(shipment: str) -> str | None:
	shipment_doc = frappe.get_doc("Shipment", shipment)
	if shipment_doc.delivery_customer:
		return frappe.db.get_value("Customer", shipment_doc.delivery_customer, "customer_name")
	if shipment_doc.delivery_supplier:
		return frappe.db.get_value("Supplier", shipment_doc.delivery_supplier, "supplier_name")
	if shipment_doc.delivery_company:
		return frappe.db.get_value("Company", shipment_doc.delivery_company, "company_name")

	return None


@frappe.whitelist()
def print_shipping_label(shipment: str):
	shipment_doc = frappe.get_doc("Shipment", shipment)
	service_provider = shipment_doc.service_provider
	shipment_id = shipment_doc.shipment_id
	shipping_label = None
	pickup_company = shipment_doc.pickup_company

	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipping_label = letmeship.get_label(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipping_label = []
		_labels = sendcloud.get_label(shipment_id)
		for label_url in _labels:
			content = sendcloud.download_label(label_url)
			file_url = save_label_as_attachment(shipment, content)
			shipping_label.append(file_url)
	elif service_provider == SHIPROCKET_PROVIDER:
		shipping_label = []
		shiprocket = ShiprocketUtils(company=pickup_company)
		file_url = shiprocket.generate_lable(shipment_id)
		shipping_label.append(file_url)
	elif service_provider == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils()
		shipping_label = delhivery.get_label(shipment_id)

	return shipping_label


def save_label_as_attachment(shipment: str, content: bytes) -> str:
	"""Store label as attachment to Shipment and return the URL."""
	attachment = frappe.new_doc("File")
	attachment.file_name = f"label_{shipment}.pdf"
	attachment.content = content
	attachment.folder = "Home/Attachments"
	attachment.attached_to_doctype = "Shipment"
	attachment.attached_to_name = shipment
	attachment.is_private = 1
	attachment.save()

	return attachment.file_url


@frappe.whitelist()
def update_tracking(shipment, service_provider, shipment_id, delivery_notes=None):
	if delivery_notes is None:
		delivery_notes = []

	shipment_doc = frappe.get_doc("Shipment", shipment)
	pickup_company = shipment_doc.pickup_company

	# Update Tracking info in Shipment
	tracking_data = None
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		tracking_data = letmeship.get_tracking_data(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		tracking_data = sendcloud.get_tracking_data(shipment_id)
	elif service_provider == SHIPROCKET_PROVIDER:
		shiprocket = ShiprocketUtils(company=pickup_company)
		tracking_data = shiprocket.track_order(shipment_id)

	elif service_provider == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils()
		tracking_data = delhivery.get_tracking_data(shipment_id)
	if not tracking_data:
		return

	shipment = frappe.get_doc("Shipment", shipment)
	shipment.db_set(
		{
			"awb_number": tracking_data.get("awb_number"),
			"tracking_status": tracking_data.get("tracking_status"),
			"tracking_status_info": tracking_data.get("tracking_status_info"),
			"tracking_url": tracking_data.get("tracking_url"),
		}
	)

	if delivery_notes:
		update_delivery_note(delivery_notes=delivery_notes, tracking_info=tracking_data)


def update_delivery_note(delivery_notes, shipment_info=None, tracking_info=None):
	# Update Shipment Info in Delivery Note
	# Using db_set since some services might not exist
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	delivery_notes = list(set(delivery_notes))

	for delivery_note in delivery_notes:
		dl_doc = frappe.get_doc("Delivery Note", delivery_note)
		if shipment_info:
			dl_doc.db_set("delivery_type", "Parcel Service")
			dl_doc.db_set("parcel_service", shipment_info.get("carrier"))
			dl_doc.db_set("parcel_service_type", shipment_info.get("carrier_service"))
		if tracking_info:
			dl_doc.db_set("tracking_number", tracking_info.get("awb_number"))
			dl_doc.db_set("tracking_url", tracking_info.get("tracking_url"))
			dl_doc.db_set("tracking_status", tracking_info.get("tracking_status"))
			dl_doc.db_set("tracking_status_info", tracking_info.get("tracking_status_info"))
