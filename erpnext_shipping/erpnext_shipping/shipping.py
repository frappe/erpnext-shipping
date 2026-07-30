# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe
from erpnext.stock.doctype.shipment.shipment import get_company_contact
from frappe import Any, _

from erpnext_shipping.erpnext_shipping.doctype.delhiveryone.constants import DELHIVERY_PROVIDER
from erpnext_shipping.erpnext_shipping.doctype.delhiveryone.delhiveryone import DelhiveryOneUtils
from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import (
	LETMESHIP_PROVIDER,
	get_letmeship_utils,
)
from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SENDCLOUD_PROVIDER, SendCloudUtils
from erpnext_shipping.erpnext_shipping.utils import (
	get_address,
	get_contact,
	get_enabled_doc_for_company,
	match_parcel_service_type_carrier,
	save_label_as_attachment,
)


@frappe.whitelist()
def fetch_shipping_rates(
	pickup_from_type: str,
	delivery_to_type: str,
	pickup_address_name: str,
	delivery_address_name: str,
	parcels: str,
	description_of_content: str,
	pickup_date: str,
	value_of_goods: str,
	pickup_contact_name: str | None = None,
	delivery_contact_name: str | None = None,
	pickup_company: str | None = None,
	total_weight: float | None = None,
	pickup_contact: dict[str, Any] | None = None,
	delivery_contact: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
	if not frappe.has_permission("Shipment", "write"):
		frappe.throw(_("Not permitted to fetch shipping rates."), frappe.PermissionError)
	# Return Shipping Rates for the various Shipping Providers
	shipment_prices = []
	letmeship_enabled = frappe.db.get_single_value("LetMeShip", "enabled")
	sendcloud_enabled = frappe.db.get_single_value("SendCloud", "enabled")
	delhivery_one_enabled = get_enabled_doc_for_company(DELHIVERY_PROVIDER, pickup_company)
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

	shipment_prices = sorted(shipment_prices, key=lambda k: k.get("total_price", 0))
	return shipment_prices


@frappe.whitelist()
def create_shipment(
	shipment: str,
	pickup_from_type: str,
	delivery_to_type: str,
	pickup_address_name: str,
	delivery_address_name: str,
	shipment_parcel: str,
	description_of_content: str,
	pickup_date: str,
	value_of_goods: str,
	service_data: str,
	total_weight: float | None,
	shipment_notific_email: str | None = None,
	tracking_notific_email: str | None = None,
	pickup_contact_name: str | None = None,
	delivery_contact_name: str | None = None,
	delivery_notes: str | None = None,
	pickup_company: str | None = None,
) -> dict[str, Any] | None:
	if not frappe.has_permission("Shipment", "write"):
		frappe.throw(_("You do not have permission to modify Shipment."), frappe.PermissionError)
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
	delivery_customer, delivery_supplier, delivery_company = frappe.db.get_value(
		"Shipment",
		shipment,
		["delivery_customer", "delivery_supplier", "delivery_company"],
	)
	if delivery_customer:
		return frappe.db.get_value("Customer", delivery_customer, "customer_name")

	if delivery_supplier:
		return frappe.db.get_value("Supplier", delivery_supplier, "supplier_name")

	if delivery_company:
		return frappe.db.get_value("Company", delivery_company, "company_name")

	return None


@frappe.whitelist()
def print_shipping_label(shipment: str):
	if not frappe.has_permission("Shipment", "read"):
		frappe.throw(_("You do not have permission to access Shipment."), frappe.PermissionError)
	service_provider, shipment_id, pickup_company = frappe.db.get_value(
		"Shipment",
		shipment,
		["service_provider", "shipment_id", "pickup_company"],
	)
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
	elif service_provider == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils(company=pickup_company)
		shipping_label = delhivery.get_label(shipment_id)

	return shipping_label


@frappe.whitelist()
def update_tracking(
	shipment: str,
	service_provider: str,
	shipment_id: str,
	delivery_notes: str | None = None,
	awb_number: str | None = None,
) -> dict[str, Any] | None:
	if not frappe.has_permission("Shipment", "write"):
		frappe.throw(_("You do not have permission to modify Shipment."), frappe.PermissionError)
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	shipment = frappe.get_doc("Shipment", shipment)
	pickup_company = shipment.pickup_company

	# Update Tracking info in Shipment
	tracking_data = None
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		tracking_data = letmeship.get_tracking_data(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		tracking_data = sendcloud.get_tracking_data(shipment_id)
	elif service_provider == DELHIVERY_PROVIDER:
		delhivery = DelhiveryOneUtils(company=pickup_company)
		tracking_data = delhivery.get_tracking_data(shipment_id)

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


def update_delivery_note(
	delivery_notes: list[str],
	shipment_info: dict[str, Any] | None = None,
	tracking_info: dict[str, Any] | None = None,
):
	# Update Shipment Info in Delivery Note
	# Using db_set since some services might not exist
	delivery_notes = list(dict.fromkeys(delivery_notes))

	for delivery_note in delivery_notes:
		if shipment_info:
			frappe.db.set_value(
				"Delivery Note",
				delivery_note,
				{
					"delivery_type": "Parcel Service",
					"parcel_service": shipment_info.get("carrier"),
					"parcel_service_type": shipment_info.get("carrier_service"),
				},
			)

		if tracking_info:
			frappe.db.set_value(
				"Delivery Note",
				delivery_note,
				{
					"tracking_number": tracking_info.get("awb_number"),
					"tracking_url": tracking_info.get("tracking_url"),
					"tracking_status": tracking_info.get("tracking_status"),
					"tracking_status_info": tracking_info.get("tracking_status_info"),
				},
			)
