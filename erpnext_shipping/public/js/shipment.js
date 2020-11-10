// Copyright (c) 2020, Frappe and contributors
// For license information, please see license.txt

frappe.ui.form.on('Shipment', {
	refresh: function(frm) {
		if (frm.doc.docstatus === 1 && !frm.doc.shipment_id) {
			frm.add_custom_button(__('Fetch Shipping Rates'), function() {
				return frm.events.fetch_shipping_rates(frm);
			});
		}
		if (frm.doc.shipment_id) {
			frm.add_custom_button(__('Print Shipping Label'), function() {
				return frm.events.print_shipping_label(frm);
			}, __('Tools'));
			if (frm.doc.tracking_status != 'Delivered') {
				frm.add_custom_button(__('Update Tracking'), function() {
					return frm.events.update_tracking(frm, frm.doc.service_provider, frm.doc.shipment_id);
				}, __('Tools'));

				frm.add_custom_button(__('Track Status'), function() {
					const urls = frm.doc.tracking_url.split(', ');
					urls.forEach(url => window.open(url));
				}, __('View'));
			}
		}
	},

	fetch_shipping_rates: function(frm) {
		if (!frm.doc.shipment_id) {
			frappe.call({
				method: "erpnext_integrations.erpnext_integrations.utils.fetch_shipping_rates",
				freeze: true,
				freeze_message: __("Fetching Shipping Rates"),
				args: {
					pickup_from_type: frm.doc.pickup_from_type,
					delivery_to_type: frm.doc.delivery_to_type,
					pickup_address_name: frm.doc.pickup_address_name,
					delivery_address_name: frm.doc.delivery_address_name,
					shipment_parcel: frm.doc.shipment_parcel,
					description_of_content: frm.doc.description_of_content,
					pickup_date: frm.doc.pickup_date,
					pickup_contact_name: frm.doc.pickup_from_type === 'Company' ? frm.doc.pickup_contact_person : frm.doc.pickup_contact_name,
					delivery_contact_name: frm.doc.delivery_contact_name,
					value_of_goods: frm.doc.value_of_goods
				},
				callback: function(r) {
					if (r.message) {
						select_from_available_services(frm, r.message);
					}
					else {
						frappe.throw(__("No Shipment Services available"));
					}
				}
			});
		}
		else {
			frappe.throw(__("Shipment already created"));
		}
	},

	print_shipping_label: function(frm) {
		frappe.call({
			method: "erpnext_integrations.erpnext_integrations.utils.print_shipping_label",
			freeze: true,
			freeze_message: __("Printing Shipping Label"),
			args: {
				shipment_id: frm.doc.shipment_id,
				service_provider: frm.doc.service_provider
			},
			callback: function(r) {
				if (r.message) {
					if (frm.doc.service_provider == "LetMeShip") {
						var array = JSON.parse(r.message);
						// Uint8Array for unsigned bytes
						array = new Uint8Array(array);
						const file = new Blob([array], {type: "application/pdf"});
						const file_url = URL.createObjectURL(file);
						window.open(file_url);
					}
					else {
						if (Array.isArray(r.message)) {
							r.message.forEach(url => window.open(url));
						} else {
							window.open(r.message);
						}
					}
				}
			}
		});
	},

	update_tracking: function(frm, service_provider, shipment_id) {
		let delivery_notes = [];
		(frm.doc.shipment_delivery_note || []).forEach((d) => {
			delivery_notes.push(d.delivery_note);
		});
		frappe.call({
			method: "erpnext_integrations.erpnext_integrations.utils.update_tracking",
			freeze: true,
			freeze_message: __("Updating Tracking"),
			args: {
				shipment: frm.doc.name,
				shipment_id: shipment_id,
				service_provider: service_provider,
				delivery_notes: delivery_notes
			},
			callback: function(r) {
				if (!r.exc) {
					frm.reload_doc();
				}
			}
		});
	}
});

function select_from_available_services(frm, available_services) {
	var headers = [ __("Service Provider"), __("Carrier"), __("Carrier’s Service"), __("Price"), "" ];
	cur_frm.render_available_services = function(d, headers, data){
		const arranged_data = data.reduce((prev, curr) => {
			if (curr.is_preferred) {
				prev.preferred_services.push(curr);
			} else {
				prev.other_services.push(curr);
			}
			return prev;
		}, { preferred_services: [], other_services: [] });
		d.fields_dict.available_services.$wrapper.html(
			frappe.render_template('shipment_service_selector',
				{'header_columns': headers, 'data': arranged_data}
			)
		);
	};
	const d = new frappe.ui.Dialog({
		title: __("Select Shipment Service to create Shipment"),
		fields: [
			{
				fieldtype:'HTML',
				fieldname:"available_services",
				label: __('Available Services')
			}
		]
	});
	cur_frm.render_available_services(d, headers, available_services);
	let shipment_notific_email = [];
	let tracking_notific_email = [];
	(frm.doc.shipment_notification_subscription || []).forEach((d) => {
		if (!d.unsubscribed) {
			shipment_notific_email.push(d.email);
		}
	});
	(frm.doc.shipment_status_update_subscription || []).forEach((d) => {
		if (!d.unsubscribed) {
			tracking_notific_email.push(d.email);
		}
	});
	let delivery_notes = [];
	(frm.doc.shipment_delivery_note || []).forEach((d) => {
		delivery_notes.push(d.delivery_note);
	});
	cur_frm.select_row = function(service_data){
		frappe.call({
			method: "erpnext_integrations.erpnext_integrations.utils.create_shipment",
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
				pickup_contact_name: frm.doc.pickup_from_type === 'Company' ? frm.doc.pickup_contact_person : frm.doc.pickup_contact_name,
				delivery_contact_name: frm.doc.delivery_contact_name,
				value_of_goods: frm.doc.value_of_goods,
				service_data: service_data,
				shipment_notific_email: shipment_notific_email,
				tracking_notific_email: tracking_notific_email,
				delivery_notes: delivery_notes
			},
			callback: function(r) {
				if (!r.exc) {
					frm.reload_doc();
					frappe.msgprint(__("Shipment created with {0}, ID is {1}", [r.message.service_provider, r.message.shipment_id]));
					frm.events.update_tracking(frm, r.message.service_provider, r.message.shipment_id);
				}
			}
		});
		d.hide();
	};
	d.show();
}