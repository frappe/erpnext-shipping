# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
import re

import frappe
import requests
from frappe import _
from frappe.utils.data import get_link_to_form

COUNTRIESNOW_STATES_URL = "https://countriesnow.space/api/v0.1/countries/states"
COUNTRY_STATES_CACHE_PREFIX = "countriesnow_states"
COUNTRY_STATES_CACHE_TTL = 60 * 60 * 24  # 24 hours


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


def _normalize_state_key(state: str) -> str:
	"""Normalize a state name for map lookup (lowercase, no spaces)."""
	return re.sub(r"\s+", "", str(state).strip().lower())


def _build_states_map(states: list[dict]) -> dict[str, str]:
	"""Build a lookup of normalized state name → state_code from API state rows."""
	states_map: dict[str, str] = {}
	for item in states or []:
		name = (item.get("name") or "").strip()
		code = (item.get("state_code") or "").strip()
		if not name or not code:
			continue
		states_map[name.lower()] = code
		states_map[_normalize_state_key(name)] = code
	return states_map


def _fetch_states_from_api(country: str) -> dict[str, str]:
	"""POST to countriesnow API and return name→code map for the country."""
	try:
		response = requests.post(
			COUNTRIESNOW_STATES_URL,
			headers={"Content-Type": "application/json"},
			json={"country": country},
			timeout=15,
		)
		response.raise_for_status()
		payload = response.json()
	except Exception:
		frappe.log_error(
			title=f"Failed to fetch states for country: {country}",
			message=frappe.get_traceback(),
		)
		return {}

	if payload.get("error"):
		frappe.log_error(
			title=f"CountriesNow API error for country: {country}",
			message=str(payload),
		)
		return {}

	states = (payload.get("data") or {}).get("states") or []
	return _build_states_map(states)


def get_states_for_country(country: str) -> dict[str, str]:
	"""Return a mapping of state name keys → state codes for a country.

	Results are stored in ``frappe.cache`` keyed by country name. On a cache hit
	the cached map is returned; on a miss the countriesnow API is called and the
	response is cached for later use.
	"""
	if not country:
		return {}

	country_key = str(country).strip().lower()
	if not country_key:
		return {}

	cache_key = f"{COUNTRY_STATES_CACHE_PREFIX}:{country_key}"
	cached = frappe.cache.get_value(cache_key)
	if cached is not None:
		return cached

	states_map = _fetch_states_from_api(country_key)
	# Cache successful lookups; avoid long-lived empty cache on transient API failures
	if states_map:
		frappe.cache.set_value(cache_key, states_map, expires_in_sec=COUNTRY_STATES_CACHE_TTL)

	return states_map


def get_state_code(state: str | None, country: str | None) -> str | None:
	"""Resolve a state name to its state code for the given country.

	Uses the countriesnow states API (via cache). If no match is found, returns
	the original state string so callers can still send a value to the carrier.
	"""
	if not state:
		return None

	state = str(state).strip()
	if not country:
		return state

	states_map = get_states_for_country(country)
	if not states_map:
		return state

	return states_map.get(state.lower()) or states_map.get(_normalize_state_key(state)) or state


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
		fields=[
			"name",
			"service_provider",
			"shipment_id",
			"shipment_delivery_note",
		],
	)

	for shipment in shipments:
		tracking_info = update_tracking(
			shipment.name,
			shipment.service_provider,
			shipment.shipment_id,
			shipment.shipment_delivery_note,
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


def get_enabled_doc_for_company(doctype: str, company: str) -> dict | None:
	filters = {"company": company, "enabled": True}

	if frappe.db.exists(doctype, filters):
		return frappe.get_doc(doctype, filters)

	return None


def handle_shipping_error(
	name: str, provider: str, message: str, details: str, raise_exception: bool = True
) -> None:
	"""Log the error; show a message (and optionally raise) only when raise_exception is True.

	When raise_exception is False (e.g. soft rate/carrier probes), only write Error Log
	so the UI is not flooded with provider failures for individual carriers.
	"""
	frappe.log_error(title=f"{provider}: {message}", message=str(details))
	if not raise_exception:
		return

	frappe.msgprint(
		msg=_("<b>{0}:</b> {1}<br>Disable the {0} Account if you need to continue without {0}: {2}").format(
			provider, f"{message}: {details}", get_link_to_form(provider, name)
		),
		raise_exception=raise_exception,
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
	if url and not content:
		import requests as _requests

		resp = _requests.get(url, timeout=30)
		resp.raise_for_status()
		content = resp.content
	attachment.content = content
	attachment.folder = "Home/Attachments"
	attachment.attached_to_doctype = "Shipment"
	attachment.attached_to_name = shipment
	attachment.is_private = 1
	attachment.file_url = url
	attachment.save()
	return attachment.file_url
