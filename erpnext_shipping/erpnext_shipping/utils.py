# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import re

import frappe
from frappe import _
from frappe.utils import ceil, flt
from frappe.utils.data import get_link_to_form

# seconds a provider has to quote at store checkout, while the shopper waits
CHECKOUT_TIMEOUT = 8


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
			"state",
			"pincode",
			"country",
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


def get_contact(contact_name, require_last_name=True):
	fields = ["first_name", "last_name", "email_id", "phone", "mobile_no", "gender"]
	contact = frappe.db.get_value("Contact", contact_name, fields, as_dict=1)

	# LetMeShip needs a separate last name; Starshipit takes the full name
	if require_last_name and not contact.last_name:
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
		# one Shipment failing (a provider switched off, a timeout) doesn't stop the others
		try:
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
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Could not update tracking for {shipment.name}")


def fill_parcels_from_items(doc, method=None):
	"""Shipment before_validate: when a draft Shipment made from a store order, or one for
	Starshipit, has no parcels, add them from its items (see parcels_for_items). Parcels can be
	edited by hand; items that can't be weighed leave them for staff to enter."""
	if doc.docstatus != 0 or doc.shipment_parcel or not doc.shipment_delivery_note:
		return
	from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import (
		STARSHIPIT_PROVIDER,
		kg_per_unit,
	)

	starshipit = frappe.db.get_single_value("Starshipit", "enabled") and doc.service_provider in (
		None,
		"",
		STARSHIPIT_PROVIDER,
	)
	if not (doc.flags.fill_parcels_from_items or starshipit):
		return

	lines = []
	for row in doc.shipment_delivery_note:
		for item in frappe.get_all(
			"Delivery Note Item",
			filters={"parent": row.delivery_note},
			fields=["item_code", "stock_qty", "weight_per_unit", "weight_uom"],
			order_by="idx",
		):
			template, own_box = frappe.get_cached_value(
				"Item", item.item_code, ["shipping_parcel_template", "ships_in_own_box"]
			) or (None, 0)
			lines.append(
				frappe._dict(
					item_code=item.item_code,
					qty=flt(item.stock_qty),  # weight_per_unit is per stock unit
					kg=kg_per_unit(item.weight_per_unit, item.weight_uom) or 0,
					template=template,
					own_box=own_box,
				)
			)
	default = frappe.db.get_single_value("Starshipit", "default_parcel_template")
	try:
		parcels = parcels_for_items(lines, doc.parcel_template, default)
	except frappe.ValidationError:
		frappe.clear_messages()
		return
	for parcel in parcels:
		doc.append("shipment_parcel", parcel)


def parcels_for_items(lines: list[dict], shipment_template: str | None, default_template: str | None):
	"""Parcels (cm, kg) for item lines of {qty, kg per unit, template, own_box}.

	Items marked "Ships in Own Box" get one parcel per unit in their own Parcel Template. The others
	share the Shipment's Parcel Template, else the largest of those items', else the default; as
	many of it as their weight needs when it has a Maximum Weight. Parcel weight = items + box."""

	def box(name):
		return frappe.get_cached_doc("Shipment Parcel Template", name) if name else None

	def parcel(template, items_kg, count=1):
		return {
			"length": template.length if template else 0,
			"width": template.width if template else 0,
			"height": template.height if template else 0,
			"weight": flt(items_kg + flt(template.weight if template else 0), 3),
			"count": count,
		}

	parcels, shared, shared_boxes = [], [], []
	for line in lines:
		line = frappe._dict(line)
		if line.own_box:
			template = box(line.template or default_template)
			own = parcel(template, flt(line.kg), max(ceil(flt(line.qty)), 1))
			if own["weight"] <= 0:
				frappe.throw(
					_(
						"Set a weight for {0}, or for its Parcel Template, so its parcels can be priced."
					).format(frappe.bold(line.get("item_code") or _("an item shipped in its own box")))
				)
			parcels.append(own)
			continue
		if flt(line.kg) * flt(line.qty) > 0:
			shared.append(line)
		if line.template:
			shared_boxes.append(box(line.template))
	if shared:
		largest = max(shared_boxes, key=lambda b: flt(b.length) * flt(b.width) * flt(b.height), default=None)
		template = box(shipment_template) or largest or box(default_template)
		capacity = flt(template.get("max_weight")) - flt(template.weight) if template else 0
		loads = (
			pack(shared, capacity) if capacity > 0 else [sum(flt(line.kg) * flt(line.qty) for line in shared)]
		)
		counted = {}
		for load in loads:
			weight = flt(load, 3)
			counted[weight] = counted.get(weight, 0) + 1
		parcels[:0] = [parcel(template, weight, count) for weight, count in counted.items()]
	return parcels


def pack(lines: list[dict], capacity: float) -> list[float]:
	"""Item weights (kg) packed into boxes holding up to capacity each, heaviest first; a unit
	heavier than a box goes in one on its own."""
	units = []
	for line in lines:
		whole = int(flt(line.qty))
		units += [flt(line.kg)] * whole
		if flt(line.qty) > whole:
			units.append(flt(line.kg) * (flt(line.qty) - whole))
	loads = []
	for weight in sorted(units, reverse=True):
		for index, load in enumerate(loads):
			if load + weight <= capacity:
				loads[index] = load + weight
				break
		else:
			loads.append(weight)
	return loads
