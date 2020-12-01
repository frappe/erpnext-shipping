# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt
from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

def execute():
	dn_fields = {
		"Delivery Note": [
			{
				"fieldname": "shipping_sec_break",
				"label": "Shipping Details",
				"fieldtype": "Section Break",
				"collapsible": 1,
				"insert_after": "sales_team"
			},
			{
				"fieldname": "delivery_type",
				"label": "Delivery Type",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "shipping_sec_break"
			},
			{
				"fieldname": "parcel_service",
				"label": "Parcel Service",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "delivery_type"
			},
			{
				"fieldname": "parcel_service_type",
				"label": "Parcel Service Type",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "parcel_service"
			},
			{
				"fieldname": "shipping_col_break",
				"fieldtype": "Column Break",
				"insert_after": "parcel_service_type"
			},
			{
				"fieldname": "tracking_number",
				"label": "Tracking Number",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "shipping_col_break"
			},
			{
				"fieldname": "tracking_url",
				"label": "Tracking URL",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "tracking_number"
			},
			{
				"fieldname": "tracking_status",
				"label": "Tracking Status",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "tracking_url"
			},
			{
				"fieldname": "tracking_status_info",
				"label": "Tracking Status Information",
				"fieldtype": "Data",
				"read_only": 1,
				"insert_after": "tracking_status"
			}
		]
	}

	if not frappe.get_meta("Delivery Note").has_field("delivery_type"):
		create_custom_fields(dn_fields)