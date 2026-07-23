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
			"phone",
			"email_id",
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
		frappe.throw(_("Please add a valid country in Address {0}.").format(address.address_title))

	if not address.pincode or address.pincode.strip() == "":
		frappe.throw(_("Please add a valid pincode in Address {0}.").format(address.address_title))


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

	try:
		shipments = frappe.get_all(
			"Shipment",
			filters={
				"docstatus": 1,
				"status": "Booked",
				"shipment_id": ["!=", ""],
				"tracking_status": ["!=", "Delivered"],
			},
			fields=["name", "service_provider", "shipment_id", "awb_number"],
		)
		for shipment in shipments:
			delivery_notes = frappe.get_all(
				"Shipment Delivery Note",
				filters={"parent": shipment.name},
				pluck="delivery_note",
			)
			tracking_info = update_tracking(
				shipment.name,
				shipment.service_provider,
				shipment.shipment_id,
				delivery_notes,
				shipment.awb_number,
			)

			if tracking_info:
				frappe.db.set_value(
					"Shipment",
					shipment.name,
					{
						"awb_number": tracking_info.get("awb_number"),
						"tracking_status": tracking_info.get("tracking_status"),
						"tracking_status_info": tracking_info.get("tracking_status_info"),
						"tracking_url": tracking_info.get("tracking_url"),
					},
				)
	except Exception:
		frappe.log_error(
			title="Shipment Tracking Update Failed",
			message=frappe.get_traceback(),
		)


def get_enabled_doc_for_company(doctype: str, company: str) -> dict | None:

	filters = {
		"company": company,
		"enabled": True,
	}

	if frappe.db.exists(doctype, filters):
		return frappe.get_doc(doctype, filters)

	return None


def throw_shipping_error(
	doc_name: str,
	provider: str,
	message: str,
	raise_exception: bool,
) -> None:
	"""
	Raises a formatted Frappe validation error with a link
	to disable the Shipping Provider.
	"""
	frappe.msgprint(
		msg=_("<b>{0}:</b> {1}<br>Disable the {0} Account if you need to continue without {0}: {2}").format(
			provider,
			message,
			get_link_to_form(provider, doc_name),
		),
		raise_exception=raise_exception,
	)


def validate_enabled_service(doctype, name, company):
	existing_doc = frappe.db.exists(
		doctype,
		{
			"company": company,
			"enabled": True,
		},
	)
	if existing_doc and existing_doc != name:
		frappe.msgprint(
			_("Only one {0} can be enabled at a time for the company <b>{1}</b>.").format(
				doctype,
				company,
			)
		)
		return False

	return True


def save_label_as_attachment(
	shipment: str,
	content: bytes = None,
	index: int = None,
	url: str = None,
) -> str:
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
