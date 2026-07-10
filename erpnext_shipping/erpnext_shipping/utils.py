# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import re

import frappe
from frappe import _
from frappe.utils.data import get_link_to_form


def get_tracking_url(carrier, tracking_number):
	# Return the formatted Tracking URL.
	tracking_url = ""
	url_reference = frappe.db.get_value("Parcel Service", carrier, "url_reference")
	if url_reference:
		tracking_url = frappe.render_template(url_reference, {"tracking_number": tracking_number})
	return tracking_url


def get_address(address_name):
	address = frappe.db.get_value(
		"Address",
		address_name,
		[
			"address_title",
			"address_line1",
			"address_line2",
			"city",
			"pincode",
			"country",
			"state",
		],
		as_dict=1,
	)
	validate_address(address)

	address.country = address.country.strip()
	address.country_code = get_country_code(address.country)
	address.pincode = address.pincode.replace(" ", "")
	address.city = address.city.strip()

	return address


def validate_address(address):
	if not address.country:
		frappe.throw(f"Please add a valid country in Address {address.address_title}.")

	if not address.pincode or address.pincode.strip() == "":
		frappe.throw(_("Please add a valid pincode in Address {0}.").format(address.address_title))


def validate_parcels(doc, method=None):
	if doc.docstatus != 0:
		return

	for parcel in doc.shipment_parcel:
		for field in ("length", "width", "height"):
			if (parcel.get(field) or 0) < 1:
				frappe.throw(
					_("Parcel row {idx}: {field_label} must be at least 1 cm.").format(
						idx=parcel.idx, field_label=_(parcel.meta.get_label(field))
					)
				)


def validate_phone(doc, method=None):
	if doc.pickup_from_type == "Company":
		phone_number = frappe.db.get_value("User", doc.pickup_contact_person, "phone")
	else:
		phone_number = frappe.db.get_value("Contact", doc.pickup_contact_name, "phone")

	if not phone_number:
		frappe.throw(_("Pickup contact phone is required."))

	if not re.match(r"^\+(?![\s0])[\d\s]+\d$", phone_number):
		frappe.throw(_("Pickup contact phone must consist of a '+' followed by one or more digits."))


def get_country_code(country_name):
	country_code = frappe.db.get_value("Country", country_name, "code")
	if not country_code:
		frappe.throw(_("Country Code not found for {0}").format(country_name))
	return country_code


def get_contact(contact_name):
	fields = ["first_name", "last_name", "email_id", "phone", "mobile_no", "gender"]
	contact = frappe.db.get_value("Contact", contact_name, fields, as_dict=1)

	if not contact.last_name:
		frappe.throw(
			msg=_("Please set Last Name for Contact {0}").format(get_link_to_form("Contact", contact_name)),
			title=_("Last Name is mandatory to continue."),
		)

	if not contact.phone:
		contact.phone = contact.mobile_no

	return contact


def match_parcel_service_type_carrier(
	shipment_prices: list[dict], carrier_fieldname: str, service_fieldname: str
):
	from erpnext_shipping.erpnext_shipping.doctype.parcel_service_type.parcel_service_type import (
		match_parcel_service_type_alias,
	)

	for idx, prices in enumerate(shipment_prices):
		service_name = match_parcel_service_type_alias(
			prices.get(carrier_fieldname), prices.get(service_fieldname)
		)
		is_preferred = frappe.db.get_value(
			"Parcel Service Type", service_name, "show_in_preferred_services_list"
		)
		if is_preferred:
			shipment_prices[idx].is_preferred = is_preferred

	return shipment_prices


def show_error_alert(action):
	log = frappe.log_error(title="Shipping Error")
	link_to_log = get_link_to_form("Error Log", log.name, "See what happened.")
	frappe.msgprint(
		msg=_("An Error occurred while {0}. {1}").format(action, link_to_log), indicator="orange", alert=True
	)


def update_tracking_info_daily():
	"""Daily scheduled event to update Tracking info for not delivered Shipments

	Also Updates the related Delivery Notes.
	"""
	from erpnext_shipping.erpnext_shipping.shipping import update_tracking

	shipments = frappe.get_all(
		"Shipment",
		filters={
			"docstatus": 1,
			"status": "Booked",
			"shipment_id": ["!=", ""],
			"tracking_status": ["!=", "Delivered"],
		},
	)
	for shipment in shipments:
		shipment_doc = frappe.get_doc("Shipment", shipment.name)
		tracking_info = update_tracking(
			shipment.name,
			shipment_doc.service_provider,
			shipment_doc.shipment_id,
			shipment_doc.shipment_delivery_note,
		)

		if tracking_info:
			fields = ["awb_number", "tracking_status", "tracking_status_info", "tracking_url"]
			for field in fields:
				shipment_doc.db_set(field, tracking_info.get(field))


def get_enabled_doc_for_company(doctype: str, company: str) -> dict | None:
	filters = {"company": company, "enabled": True}

	if frappe.db.exists(doctype, filters):
		return frappe.get_doc(doctype, filters)

	return None


def handle_shipping_error(
	name: str, provider: str, message: str, exception: Exception, raise_exception: bool
) -> None:
	frappe.log_error(message, str(exception))
	throw_shipping_error(name, provider, f"{message}: {str(exception)}", raise_exception)


def throw_shipping_error(doc_name: str, provider: str, message: str, raise_exception: bool) -> None:
	"""
	Raises a formatted Frappe validation error with a link to disable the Shipping Provider.
	"""
	frappe.msgprint(
		msg=_(
			f"<b>{provider}:</b> {message}<br>"
			f"Disable the {provider} Account if you need to continue without {provider}: "
			f"{get_link_to_form(provider, doc_name)}"
		),
		raise_exception=_(raise_exception),
	)


def get_shipping_label(shipment: str) -> str | None:
	"""Retrieve the file URL of the shipping label for a given shipment."""
	return frappe.db.get_value("File", filters={"file_name": f"label_{shipment}.pdf"}, fieldname="file_url")


def validate_enabled_service(doctype, name, company):
	existing_doc = frappe.db.exists(doctype, {"company": company, "enabled": True})

	if existing_doc and existing_doc != name:
		frappe.msgprint(_(f"Only one {doctype} can be enabled at a time for the company <b>{company}</b>."))
		return False
	return True


def save_label_as_attachment(shipment: str, content: bytes = None, index: int = None, url: str = None) -> str:
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
	attachment.file_url = url
	attachment.save()
	return attachment.file_url
