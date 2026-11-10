# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class AddressLookupSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		daily_limit: DF.Int
		google_api_key: DF.Password | None
		photon_server: DF.Data | None
		service: DF.Literal["", "Photon", "Google Places"]
	# end: auto-generated types

	def validate(self):
		from urllib.parse import urlsplit

		if self.photon_server and urlsplit(self.photon_server).scheme not in ("http", "https"):
			frappe.throw(_("Photon Server must be a web address, such as https://photon.example.com"))

	def on_update(self):
		frappe.cache.delete_keys("address_lookup:")
