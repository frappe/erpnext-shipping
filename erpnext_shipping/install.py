import frappe
from frappe import get_hooks
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_shipping.erpnext_shipping.utils import SERVICE_PROVIDERS


def after_install():
	custom_fields = get_hooks("shipping_custom_fields")
	create_custom_fields(custom_fields)
	create_service_provider(SERVICE_PROVIDERS)


def create_service_provider(SERVICE_PROVIDERS):
	for provider in SERVICE_PROVIDERS:
		doc = frappe.new_doc("Service Provider")
		doc.provider_name = provider
		doc.insert()
	frappe.db.commit()
