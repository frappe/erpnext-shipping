from frappe import get_hooks
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
	custom_fields = get_hooks("shipping_custom_fields")
	create_custom_fields(custom_fields)
