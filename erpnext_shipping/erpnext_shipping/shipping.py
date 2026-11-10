# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import json

import frappe
from erpnext.stock.doctype.shipment.shipment import get_company_contact

from erpnext_shipping.erpnext_shipping.doctype.letmeship.letmeship import (
	LETMESHIP_PROVIDER,
	get_letmeship_utils,
)
from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SENDCLOUD_PROVIDER, SendCloudUtils
from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import (
	STARSHIPIT_PROVIDER,
	StarshipitUtils,
)
from erpnext_shipping.erpnext_shipping.utils import (
	get_address,
	get_contact,
	match_parcel_service_type_carrier,
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
	value_of_goods: float,
	pickup_contact_name: str | None = None,
	delivery_contact_name: str | None = None,
	shipment: str | None = None,
):
	# Return Shipping Rates for the various Shipping Providers
	frappe.has_permission("Shipment", "create", throw=True)
	return shipping_rates(
		pickup_from_type,
		delivery_to_type,
		pickup_address_name,
		delivery_address_name,
		parcels,
		description_of_content,
		pickup_date,
		value_of_goods,
		pickup_contact_name,
		delivery_contact_name,
		shipment,
	)


def shipping_rates(
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
	shipment=None,
):
	"""Every enabled provider's services and prices for a Shipment's details, cheapest first."""
	shipment_prices = []
	letmeship_enabled = frappe.db.get_single_value("LetMeShip", "enabled")
	sendcloud_enabled = frappe.db.get_single_value("SendCloud", "enabled")
	starshipit_enabled = frappe.db.get_single_value("Starshipit", "enabled")
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

	if starshipit_enabled:
		starshipit_prices = StarshipitUtils().get_available_services(
			pickup_address=pickup_address,
			delivery_address=delivery_address,
			parcels=parcels,
			value_of_goods=value_of_goods,
		)
		starshipit_prices = match_parcel_service_type_carrier(starshipit_prices, "carrier", "service_name")
		shipment_prices += starshipit_prices

	shipment_prices = [item for item in shipment_prices if "total_price" in item]
	shipment_prices = sorted(shipment_prices, key=lambda k: k["total_price"])
	if shipment:
		from erpnext_shipping.erpnext_shipping.shop_integration import mark_preferred

		shipment_prices = mark_preferred(shipment_prices, shipment)
	return shipment_prices


def get_shipping_rates(request: frappe._dict, providers: list[str] | None = None) -> list[dict]:
	"""Services and prices from every enabled provider (or only those named), cheapest first, for
	store checkout (the Shipment form's Fetch Shipping Rates asks each provider itself).

	request has pickup_address, delivery_address, parcels, value_of_goods, description_of_content,
	pickup_date, delivery_to_type, and either pickup_from_type with pickup_contact_name and
	delivery_contact_name, or pickup_contact and delivery_contact. checkout=True makes a provider
	that supports it raise on a failed quote instead of showing an alert."""
	shipment_prices = []
	for provider in providers or list(rate_providers()):
		if not frappe.db.get_single_value(provider, "enabled"):
			continue
		prices = rate_providers()[provider](request) or []
		shipment_prices += match_parcel_service_type_carrier(prices, "carrier", "service_name")

	shipment_prices = [item for item in shipment_prices if "total_price" in item]
	return sorted(shipment_prices, key=lambda k: k["total_price"])


def rate_providers() -> dict:
	"""Each provider (its settings DocType and service_provider name) and how it quotes."""
	return {
		LETMESHIP_PROVIDER: letmeship_rates,
		SENDCLOUD_PROVIDER: sendcloud_rates,
		STARSHIPIT_PROVIDER: starshipit_rates,
	}


def letmeship_rates(request: frappe._dict) -> list[dict]:
	pickup_contact = request.pickup_contact or get_pickup_contact(
		request.pickup_from_type, request.pickup_contact_name
	)
	delivery_contact = request.delivery_contact or get_contact(request.delivery_contact_name)
	# LetMeShip rewrites the phone numbers it is given
	return get_letmeship_utils().get_available_services(
		delivery_to_type=request.delivery_to_type,
		pickup_address=frappe._dict(request.pickup_address),
		delivery_address=frappe._dict(request.delivery_address),
		parcels=request.parcels,
		description_of_content=request.description_of_content,
		pickup_date=request.pickup_date,
		value_of_goods=request.value_of_goods,
		pickup_contact=frappe._dict(pickup_contact),
		delivery_contact=frappe._dict(delivery_contact),
		checkout=bool(request.checkout),
	)


def sendcloud_rates(request: frappe._dict) -> list[dict]:
	return SendCloudUtils().get_available_services(
		delivery_address=request.delivery_address,
		pickup_address=request.pickup_address,
		parcels=request.parcels,
		checkout=bool(request.checkout),
	)


def starshipit_rates(request: frappe._dict) -> list[dict]:
	return StarshipitUtils().get_available_services(
		pickup_address=request.pickup_address,
		delivery_address=request.delivery_address,
		parcels=request.parcels,
		value_of_goods=request.value_of_goods,
		checkout=bool(request.checkout),
	)


def get_pickup_contact(pickup_from_type: str, pickup_contact_name: str, require_last_name: bool = True):
	if pickup_from_type != "Company":
		return get_contact(pickup_contact_name, require_last_name=require_last_name)
	pickup_contact = get_company_contact(user=pickup_contact_name)
	pickup_contact.email_id = pickup_contact.pop("email", None)
	return pickup_contact


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
	value_of_goods: float,
	service_data: str,
	shipment_notific_email: str | None = None,
	tracking_notific_email: str | None = None,
	pickup_contact_name: str | None = None,
	delivery_contact_name: str | None = None,
	delivery_notes: str | list | None = None,
):
	frappe.get_doc("Shipment", shipment).check_permission("write")
	return book_shipment(
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
		pickup_contact_name=pickup_contact_name,
		delivery_contact_name=delivery_contact_name,
		delivery_notes=delivery_notes,
	)


def book_shipment(
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
	pickup_contact_name=None,
	delivery_contact_name=None,
	delivery_notes=None,
):
	"""Book the Shipment with the provider and service in service_data."""
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	service_info = json.loads(service_data)
	shipment_info, pickup_contact, delivery_contact = None, None, None
	pickup_address = get_address(pickup_address_name)
	delivery_address = get_address(delivery_address_name)
	delivery_company_name = get_delivery_company_name(shipment)

	needs_last_name = service_info["service_provider"] != STARSHIPIT_PROVIDER
	pickup_contact = get_pickup_contact(
		pickup_from_type, pickup_contact_name, require_last_name=needs_last_name
	)
	delivery_contact = get_contact(delivery_contact_name, require_last_name=needs_last_name)

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

	if service_info["service_provider"] == STARSHIPIT_PROVIDER:
		shipment_info = StarshipitUtils().create_shipment(
			shipment=shipment,
			pickup_address=pickup_address,
			pickup_contact=pickup_contact,
			delivery_address=delivery_address,
			delivery_contact=delivery_contact,
			delivery_company_name=delivery_company_name,
			shipment_parcel=shipment_parcel,
			description_of_content=description_of_content,
			value_of_goods=value_of_goods,
			service_info=service_info,
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
	shipment_doc.check_permission("write")  # printing a label is charged by the courier
	return shipping_label(shipment_doc)


def shipping_label(shipment_doc):
	"""Buy (or print again) the booked Shipment's label: the attached label files' URLs, or the
	label's bytes for LetMeShip."""
	shipment = shipment_doc.name
	service_provider = shipment_doc.service_provider
	shipment_id = shipment_doc.shipment_id

	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		shipping_label = letmeship.get_label(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		shipping_label = []
		_labels = sendcloud.get_label(shipment_id)
		for i, label_url in enumerate(_labels, start=1):
			content = sendcloud.download_label(label_url)
			file_url = save_label_as_attachment(shipment, content, i)
			shipping_label.append(file_url)
	elif service_provider == STARSHIPIT_PROVIDER:
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import (
			attach_pdf,
			refresh_status_quietly,
			tracking_numbers,
		)

		labels, numbers = StarshipitUtils().print_label(shipment_doc)
		shipping_label = [
			attach_pdf(shipment, "label", content, i) for i, content in enumerate(labels, start=1)
		]
		if numbers:
			shipment_doc.db_set("awb_number", ", ".join(tracking_numbers(shipment_doc.awb_number, numbers)))
		# the label is bought: a failed status check after it mustn't undo its record
		refresh_status_quietly(shipment)
		for row in shipment_doc.shipment_delivery_note:
			for file_url in shipping_label:
				attach_existing_file(file_url, "Delivery Note", row.delivery_note)

	return shipping_label


def save_label_as_attachment(shipment: str, content: bytes, index: int = None) -> str:
	"""Store label as attachment to Shipment and return the URL."""
	attachment = frappe.new_doc("File")
	if index is not None:
		attachment.file_name = f"label_{shipment}_{index}.pdf"
	else:
		attachment.file_name = f"label_{shipment}.pdf"
	attachment.content = content
	attachment.folder = "Home/Attachments"
	attachment.attached_to_doctype = "Shipment"
	attachment.attached_to_name = shipment
	attachment.is_private = 1
	attachment.save()
	return attachment.file_url


def attach_existing_file(file_url: str, doctype: str, name: str):
	"""Also show an already stored file (e.g. a label) on another document, without copying it."""
	if frappe.db.exists(
		"File", {"file_url": file_url, "attached_to_doctype": doctype, "attached_to_name": name}
	):
		return
	frappe.get_doc(
		{
			"doctype": "File",
			"file_url": file_url,
			"file_name": file_url.rsplit("/", 1)[-1],
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)


@frappe.whitelist()
def update_tracking(
	shipment: str, service_provider: str, shipment_id: str, delivery_notes: str | list | None = None
):
	frappe.get_doc("Shipment", shipment).check_permission("write")
	if isinstance(delivery_notes, str):
		delivery_notes = json.loads(delivery_notes)

	if delivery_notes is None:
		delivery_notes = []

	# Update Tracking info in Shipment
	tracking_data = None
	if service_provider == LETMESHIP_PROVIDER:
		letmeship = get_letmeship_utils()
		tracking_data = letmeship.get_tracking_data(shipment_id)
	elif service_provider == SENDCLOUD_PROVIDER:
		sendcloud = SendCloudUtils()
		tracking_data = sendcloud.get_tracking_data(shipment_id)
	elif service_provider == STARSHIPIT_PROVIDER:
		# Starshipit tracks by order number (the Shipment name) and writes the Shipment and its
		# Delivery Notes itself; return what is now stored so callers don't overwrite it
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import refresh_status

		if not refresh_status(shipment):
			return None
		return frappe.db.get_value(
			"Shipment",
			shipment,
			["awb_number", "tracking_status", "tracking_status_info", "tracking_url"],
			as_dict=True,
		)

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

	from erpnext_shipping.erpnext_shipping.shop_integration import sync_store_fulfillment

	sync_store_fulfillment(shipment.name)


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
