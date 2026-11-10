// Copyright (c) 2026, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Starshipit", {
	refresh(frm) {
		if (frm.doc.enabled && !frm.is_dirty()) {
			frm.add_custom_button(__("Test Connection"), () =>
				frappe.call({
					method: "erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit.test_connection",
					freeze: true,
					callback: (r) => r.message && frappe.show_alert({ message: r.message, indicator: "green" }),
				}),
			);
		}
	},
});
