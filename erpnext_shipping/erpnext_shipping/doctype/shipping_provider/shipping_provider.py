# Copyright (c) 2025, Frappe and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

# from erpnext_shipping.erpnext_shipping.shiprocket.shiprocket import generate_token


class ShippingProvider(Document):
	pass
	# def validate(self):
	# 	if self.service_provider == "Shiprocket":
	# 		try:
	# 			generate_token(self)
	# 		except Exception as e:
	# 			frappe.log_error(title="Error generating token", message=str(e))
