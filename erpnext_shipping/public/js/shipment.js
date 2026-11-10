// Copyright (c) 2020, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on("Shipment", {
	refresh: function (frm) {
		if (frm.doc.docstatus === 1 && !frm.doc.shipment_id) {
			frm.add_custom_button(__("Fetch Shipping Rates"), function () {
				if (frm.doc.shipment_parcel.length > 1) {
					frappe.confirm(
						__(
							"If your shipment contains packages with varying weights, the estimated shipping rates may differ from the final price charged by your carrier. Do you wish to proceed?",
						),
						function () {
							frm.events.fetch_shipping_rates(frm);
						},
					);
				} else {
					frm.events.fetch_shipping_rates(frm);
				}
			});
		}
		if (frm.doc.shipment_id) {
			frm.add_custom_button(
				__("Print Shipping Label"),
				function () {
					return frm.events.print_shipping_label(frm);
				},
				__("Tools"),
			);
			if (frm.doc.tracking_status != "Delivered") {
				frm.add_custom_button(
					__("Update Tracking"),
					function () {
						return frm.events.update_tracking(frm, frm.doc.service_provider, frm.doc.shipment_id);
					},
					__("Tools"),
				);

				frm.add_custom_button(
					__("Track Status"),
					function () {
						if (frm.doc.tracking_url) {
							const urls = frm.doc.tracking_url.split(", ");
							urls.forEach((url) => window.open(url));
						} else {
							let msg = __("Please complete Shipment (ID: {0}) on {1} and Update Tracking.", [
								frm.doc.shipment_id,
								frm.doc.service_provider,
							]);
							frappe.msgprint({ message: msg, title: __("Incomplete Shipment") });
						}
					},
					__("View"),
				);
			}
		}
		if (frm.doc.service_provider === "Starshipit" && frm.doc.shipment_id && frm.doc.docstatus === 1) {
			add_starshipit_buttons(frm);
		}
	},

	fetch_shipping_rates: function (frm) {
		if (!frm.doc.shipment_id) {
			frappe.call({
				method: "erpnext_shipping.erpnext_shipping.shipping.fetch_shipping_rates",
				freeze: true,
				freeze_message: __("Fetching Shipping Rates"),
				args: {
					pickup_from_type: frm.doc.pickup_from_type,
					delivery_to_type: frm.doc.delivery_to_type,
					pickup_address_name: frm.doc.pickup_address_name,
					delivery_address_name: frm.doc.delivery_address_name,
					parcels: frm.doc.shipment_parcel,
					description_of_content: frm.doc.description_of_content,
					pickup_date: frm.doc.pickup_date,
					pickup_contact_name:
						frm.doc.pickup_from_type === "Company"
							? frm.doc.pickup_contact_person
							: frm.doc.pickup_contact_name,
					delivery_contact_name: frm.doc.delivery_contact_name,
					value_of_goods: frm.doc.value_of_goods,
					shipment: frm.doc.name,
				},
				callback: function (r) {
					if (r.message && r.message.length) {
						select_from_available_services(frm, r.message);
					} else {
						frappe.msgprint({
							message: __("No Shipment Services available"),
							title: __("Note"),
						});
					}
				},
			});
		} else {
			frappe.throw(__("Shipment already created"));
		}
	},

	print_shipping_label: function (frm) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.print_shipping_label",
			freeze: true,
			freeze_message: __("Printing Shipping Label"),
			args: {
				shipment: frm.doc.name,
			},
			callback: function (r) {
				if (r.message) {
					if (frm.doc.service_provider == "LetMeShip") {
						var array = JSON.parse(r.message);
						// Uint8Array for unsigned bytes
						array = new Uint8Array(array);
						const file = new Blob([array], { type: "application/pdf" });
						const file_url = URL.createObjectURL(file);
						window.open(file_url);
					} else {
						if (Array.isArray(r.message)) {
							r.message.forEach((url) => window.open(url));
						} else {
							window.open(r.message);
						}
					}
				}
			},
		});
	},

	update_tracking: function (frm, service_provider, shipment_id) {
		const delivery_notes = frm.doc.shipment_delivery_note.map((d) => d.delivery_note);

		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.update_tracking",
			freeze: true,
			freeze_message: __("Updating Tracking"),
			args: {
				shipment: frm.doc.name,
				shipment_id: shipment_id,
				service_provider: service_provider,
				delivery_notes: delivery_notes,
			},
			callback: function (r) {
				if (!r.exc) {
					frm.reload_doc();
				}
			},
		});
	},
});

function select_from_available_services(frm, available_services) {
	frappe.require("shipping.bundle.js", () => show_service_selector(frm, available_services));
}

function show_service_selector(frm, available_services) {
	const arranged_services = available_services.reduce(
		(prev, curr) => {
			if (curr.is_preferred) {
				prev.preferred_services.push(curr);
			} else {
				prev.other_services.push(curr);
			}
			return prev;
		},
		{ preferred_services: [], other_services: [] },
	);

	const dialog = new frappe.ui.Dialog({
		title: __("Select Service to Create Shipment"),
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "available_services",
				label: __("Available Services"),
			},
		],
	});

	const delivery_notes = frm.doc.shipment_delivery_note.map((d) => d.delivery_note);

	dialog.fields_dict.available_services.$wrapper.html(
		frappe.render_template("shipment_service_selector", {
			header_columns: [__("Platform"), __("Carrier"), __("Parcel Service"), __("Price"), ""],
			data: arranged_services,
		}),
	);

	dialog.$body.on("click", ".btn", function () {
		let service_type = $(this).attr("data-type");
		let service_index = cint($(this).attr("id").split("-")[2]);
		let service_data = arranged_services[service_type][service_index];
		frm.select_row(service_data);
	});

	frm.select_row = function (service_data) {
		frappe.call({
			method: "erpnext_shipping.erpnext_shipping.shipping.create_shipment",
			freeze: true,
			freeze_message: __("Creating Shipment"),
			args: {
				shipment: frm.doc.name,
				pickup_from_type: frm.doc.pickup_from_type,
				delivery_to_type: frm.doc.delivery_to_type,
				pickup_address_name: frm.doc.pickup_address_name,
				delivery_address_name: frm.doc.delivery_address_name,
				shipment_parcel: frm.doc.shipment_parcel,
				description_of_content: frm.doc.description_of_content,
				pickup_date: frm.doc.pickup_date,
				pickup_contact_name:
					frm.doc.pickup_from_type === "Company"
						? frm.doc.pickup_contact_person
						: frm.doc.pickup_contact_name,
				delivery_contact_name: frm.doc.delivery_contact_name,
				value_of_goods: frm.doc.value_of_goods,
				service_data: service_data,
				delivery_notes: delivery_notes,
			},
			callback: function (r) {
				if (!r.exc) {
					frm.reload_doc();
					frappe.msgprint({
						message: __("Shipment {1} has been created with {0}.", [
							r.message.service_provider,
							r.message.shipment_id.bold(),
						]),
						title: __("Shipment Created"),
						indicator: "green",
					});
					frm.events.update_tracking(frm, r.message.service_provider, r.message.shipment_id);
				}
			},
		});
		dialog.hide();
	};
	dialog.show();
}

const STARSHIPIT_METHOD = "erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit.";

function add_starshipit_buttons(frm) {
	frm.add_custom_button(
		__("Manifest"),
		() =>
			frappe.confirm(__("Manifest this order? It is sent to the courier for billing."), () =>
				frappe.call({
					method: STARSHIPIT_METHOD + "manifest_shipment",
					args: { shipment: frm.doc.name },
					freeze: true,
					freeze_message: __("Manifesting"),
					callback: (r) => {
						if (r.message) window.open(r.message);
						frm.reload_doc();
					},
				}),
			),
		__("Starshipit"),
	);
	frm.add_custom_button(__("Book Pickup"), () => show_pickup_dialog(frm), __("Starshipit"));
	frm.add_custom_button(
		__("Commercial Invoice"),
		() =>
			frappe.call({
				method: STARSHIPIT_METHOD + "create_commercial_invoice",
				args: { shipment: frm.doc.name },
				freeze: true,
				freeze_message: __("Creating Commercial Invoice"),
				callback: (r) => {
					(r.message || []).forEach((url) => window.open(url));
					frm.reload_doc();
				},
			}),
		__("Starshipit"),
	);
}

function show_pickup_dialog(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Book Courier Pickup"),
		fields: [
			{
				fieldname: "pickup_date",
				fieldtype: "Date",
				label: __("Pickup Date"),
				reqd: 1,
				default: frm.doc.pickup_date || frappe.datetime.get_today(),
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "pickup_from",
				fieldtype: "Time",
				label: __("Pickup From"),
				reqd: 1,
				default: frm.doc.pickup_from || "09:00:00",
			},
			{ fieldtype: "Column Break" },
			{
				fieldname: "pickup_to",
				fieldtype: "Time",
				label: __("Pickup To"),
				reqd: 1,
				default: frm.doc.pickup_to || "17:00:00",
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "special_instructions", fieldtype: "Small Text", label: __("Special Instructions") },
		],
		primary_action_label: __("Book Pickup"),
		primary_action(values) {
			const book = (book_again) =>
				frappe.call({
					method: STARSHIPIT_METHOD + "book_pickup",
					args: { shipment: frm.doc.name, ...values, book_again },
					freeze: true,
					freeze_message: __("Booking Pickup"),
					callback: (r) => {
						const result = r.message || {};
						if (result.confirm) {
							frappe.confirm(result.confirm, () => book(1));
							return;
						}
						dialog.hide();
						frappe.msgprint({
							title: __("Pickup Booked"),
							message: __("Job number: {0}", [result.job_number || "-"]),
							indicator: "green",
						});
						frm.reload_doc();
					},
					error: () => frm.reload_doc(),
				});
			book(0);
		},
	});
	dialog.show();
}
