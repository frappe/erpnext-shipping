import frappe


def execute():
	"""Chnage the column type of tracking_url in Delivery Note to 'text'."""
	custom_field = "Delivery Note-tracking_url"
	if not frappe.db.exists("Custom Field", custom_field):
		return

	frappe.db.set_value("Custom Field", custom_field, "fieldtype", "Small Text")
	frappe.db.change_column_type("Delivery Note", "tracking_url", "text", nullable=True)
