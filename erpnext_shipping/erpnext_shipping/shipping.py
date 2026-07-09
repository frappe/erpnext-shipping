# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe
from erpnext.stock.doctype.shipment.shipment import get_company_contact
from frappe import _

from erpnext_shipping.erpnext_shipping.doctype.aramex.aramex import ARAMEX_PROVIDER, AramexUtils
from erpnext_shipping.erpnext_shipping.doctype.delhiveryone.delhiveryone import (
	DELHIVERY_PROVIDER,
	DelhiveryOneUtils,
)
from erpnext_shipping.erpnext_shipping.doctype.envia.envia import (
	ENVIA_PROVIDER,
	EnviaUtils,
)
from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import (
	LETMESHIP_PROVIDER,
	get_letmeship_utils,
)
from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SENDCLOUD_PROVIDER, SendCloudUtils
from erpnext_shipping.erpnext_shipping.doctype.shippo.shippo import SHIPPO_PROVIDER, ShippoUtils
from erpnext_shipping.erpnext_shipping.doctype.shiprocket.shiprocket import (
	SHIPROCKET_PROVIDER,
	ShiprocketUtils,
)
from erpnext_shipping.erpnext_shipping.utils import (
	get_address,
	get_contact,
	get_enabled_doc_for_company,
	get_shipping_label,
	match_parcel_service_type_carrier,
	save_label_as_attachment,
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
	pickup_contact=None,
	delivery_contact=None,
):
	# Return Shipping Rates for the various Shipping Providers
	shipment_prices = []
	letmeship_enabled = frappe.db.get_single_value("LetMeShip", "enabled")
	sendcloud_enabled = frappe.db.get_single_value("SendCloud", "enabled")
	shiprocket_enabled = get_enabled_doc_for_company(SHIPROCKET_PROVIDER, pickup_company)
	envia_enabled = get_enabled_doc_for_company(ENVIA_PROVIDER, pickup_company)
	delhivery_one_enabled = get_enabled_doc_for_company(DELHIVERY_PROVIDER, pickup_company)
	shippo_enabled = get_enabled_doc_for_company(SHIPPO_PROVIDER, pickup_company)
	aramex_enabled = get_enabled_doc_for_company(ARAMEX_PROVIDER, pickup_company)
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	parcels = json.loads(parcels)

	if letmeship_enabled:
		pickup_contact = None
		delivery_contact = None
		if pickup_from_type != "Company":
			pickup_contact = get_contact(pickup_contact_name)
		else:
			pickup_contact = get_company_contact(user=pickup_contact_name)
			pickup_contact.email_id = pickup_contact.pop("email", None)

		delivery_contact = get_contact(delivery_contact_name)

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

	if sendcloud_enabled:
		sendcloud = SendCloudUtils()
		sendcloud_prices = (
			sendcloud.get_available_services(
				delivery_address=delivery_address, pickup_address=pickup_address, parcels=parcels
			)
			or []
		)
		sendcloud_prices = match_parcel_service_type_carrier(sendcloud_prices, "carrier", "service_name")
		shipment_prices += sendcloud_prices

	if shiprocket_enabled:
		shiprocket = ShiprocketUtils(company=pickup_company)
		shiprocket_prices = shiprocket.get_available_services(
			parcels,
			delivery_address_name,
			pickup_address_name,
			total_weight,
		)
		shiprocket_prices = match_parcel_service_type_carrier(shiprocket_prices, "carrier", "service_name")
		shipment_prices += shiprocket_prices

	if envia_enabled:
		envia = EnviaUtils(company=pickup_company)
		envia = envia.get_available_services(
			delivery_address=delivery_address,
			pickup_address=pickup_address,
			parcels=parcels,
			delivery_contact=delivery_contact,
			pickup_contact=pickup_contact,
			total_weight=total_weight,
			value_of_goods=value_of_goods,
			pickup_company=pickup_company,
			description_of_content=description_of_content,
		)
		envia_prices = match_parcel_service_type_carrier(envia, "carrier", "service_name")
		shipment_prices += envia_prices

	if delhivery_one_enabled:
		delhivery = DelhiveryOneUtils(company=pickup_company)
		delhivery_prices = (
			delhivery.get_available_services(
				delivery_address=delivery_address, pickup_address=pickup_address, weight=total_weight
			)
			or []
		)
		delhivery_prices = match_parcel_service_type_carrier(delhivery_prices, "carrier", "service_name")
		shipment_prices += delhivery_prices

	if shippo_enabled:
		shippo = ShippoUtils(company=pickup_company)
		shippo_prices = (
			shippo.get_available_services(
				delivery_address=delivery_address,
				pickup_address=pickup_address,
				parcels=parcels,
				description_of_content=description_of_content,
			)
			or []
		)
		shippo_prices = match_parcel_service_type_carrier(shippo_prices, "carrier", "service_name")
		shipment_prices += shippo_prices

	if aramex_enabled:
		aramex = AramexUtils(company=pickup_company)
		aramex_prices = (
			aramex.get_available_services(
				delivery_address=delivery_address,
				pickup_address=pickup_address,
				weight=total_weight,
				parcels=parcels,
			)
			or []
		)
		aramex_prices = match_parcel_service_type_carrier(aramex_prices, "carrier", "service_name")
		shipment_prices += aramex_prices

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
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

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

	delivery_contact = get_contact(delivery_contact_name)

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
			delivery_address=delivery_address,
			pickup_address=pickup_address,
			pickup_contact=pickup_contact,
			shipment_parcel=shipment_parcel,
			delivery_contact=delivery_contact,
			service_info=service_info,
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
			pickup_address=pickup_address,
			value_of_goods=value_of_goods,
			pickup_contact=pickup_contact,
			delivery_contact=delivery_contact,
			service_info=service_info,
			pickup_company=pickup_company,
			total_weight=total_weight,
		)

	if service_info["service_provider"] == ENVIA_PROVIDER:
		envia = EnviaUtils(company=pickup_company)
		shipment_info = envia.create_shipment(
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
			total_weight=total_weight,
			shipment=shipment,
		)

	if service_info["service_provider"] == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils(company=pickup_company)
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

	if service_info["service_provider"] == SHIPPO_PROVIDER:
		shippo = ShippoUtils(company=pickup_company)
		shipment_info = shippo.create_shipment(shipment=shipment, service_info=service_info)

	if service_info["service_provider"] == ARAMEX_PROVIDER:
		aramex = AramexUtils()
		shipment_info = aramex.create_shipment(
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
			pickup_contact_name=pickup_contact_name,
			delivery_contact_name=delivery_contact_name,
			weight=total_weight,
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
	pickup_company = shipment_doc.pickup_company
	shipping_label = None

	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipping_label = letmeship.get_label(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipping_label = []
		_labels = sendcloud.get_label(shipment_id)
		for i, label_url in enumerate(_labels, start=1):
			content = sendcloud.download_label(label_url)
			file_url = save_label_as_attachment(shipment=shipment, content=content, index=i)
			shipping_label.append(file_url)
	elif service_provider == SHIPROCKET_PROVIDER:
		shipping_label = []
		shiprocket = ShiprocketUtils(company=pickup_company)
		label_url = shiprocket.generate_label(shipment_id, shipment)
		if not label_url:
			frappe.throw(_("Failed to generate label."))
		file_url = save_label_as_attachment(shipment=shipment, url=label_url)
		shipping_label.append(file_url)
	elif service_provider == ENVIA_PROVIDER:
		shipping_label = []
		file_url = get_shipping_label(shipment)
		if not file_url:
			frappe.throw(_("Failed to generate label."))
		shipping_label.append(file_url)
	elif service_provider == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils(company=pickup_company)
		shipping_label = delhivery.get_label(shipment_id)
	elif service_provider == SHIPPO_PROVIDER:
		shipping_label = []
		shippo = ShippoUtils(company=pickup_company)
		label_url = shippo.get_label(shipment_id, shipment)
		if not label_url:
			frappe.throw(_("Failed to generate label."))
		file_url = save_label_as_attachment(shipment=shipment, url=label_url)
		shipping_label.append(file_url)
	elif service_provider == ARAMEX_PROVIDER:
		shipping_label = []
		file_url = get_shipping_label(shipment)
		if not file_url:
			frappe.throw(_("Failed to generate label."))
		shipping_label.append(file_url)
	return shipping_label


@frappe.whitelist()
def update_tracking(shipment, service_provider, shipment_id, delivery_notes=None, awb_number=None):
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	shipment = frappe.get_doc("Shipment", shipment)
	pickup_company = shipment.pickup_company
	carrier = shipment.carrier
	tracking_url = shipment.tracking_url

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
		tracking_data = shiprocket.get_tracking_data(shipment_id)
	elif service_provider == ENVIA_PROVIDER and awb_number:
		envia = EnviaUtils(company=pickup_company)
		tracking_data = envia.get_tracking_data(awb_number)
	elif service_provider == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils(company=pickup_company)
		tracking_data = delhivery.get_tracking_data(shipment_id)
	elif service_provider == SHIPPO_PROVIDER:
		shippo = ShippoUtils(company=pickup_company)
		tracking_data = shippo.get_tracking_data(awb_number, carrier, tracking_url)
	elif service_provider == ARAMEX_PROVIDER:
		aramex = AramexUtils(company=pickup_company)
		tracking_data = aramex.get_tracking_data(shipment_id)

	if not tracking_data:
		return

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
