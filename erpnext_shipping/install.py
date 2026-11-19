import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .custom_fields import get_custom_fields
from .property_setters import get_property_setters
from .utils import make_property_setters


def after_install():
	create_custom_fields(get_custom_fields())
	make_property_setters(get_property_setters())
	enable_letmeship_party_name_as_company_name()


def enable_letmeship_party_name_as_company_name():
	"""LetMeShip has always printed the party name on the label, so keep that as the default.

	Single DocTypes don't store field defaults, so the value has to be set explicitly.
	"""
	frappe.reload_doc("erpnext_shipping", "doctype", "letmeship")
	frappe.db.set_single_value("LetMeShip", "use_party_name_as_company_name", 1)
