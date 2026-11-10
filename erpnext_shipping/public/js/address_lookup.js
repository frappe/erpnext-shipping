// Addresses suggested as an Address Line 1 field is typed, in any form or dialog (the Address form,
// the quick entry of a new Customer...), from the service in Address Lookup Settings.
$(document).on("focusin", 'input[data-fieldname="address_line1"]', function () {
	if (!$(this).data("address-lookup")) suggest_addresses($(this));
});

function address_target(input) {
	const dialog = window.cur_dialog;
	if (dialog && input.closest(dialog.$wrapper).length) {
		return {
			has: (fieldname) => !!dialog.fields_dict[fieldname],
			get: (fieldname) => dialog.get_value(fieldname),
			set: (values) => dialog.set_values(values),
		};
	}
	const frm = window.cur_frm;
	if (frm && input.closest(frm.wrapper).length) {
		return {
			has: (fieldname) => !!frm.fields_dict[fieldname],
			get: (fieldname) => frm.doc[fieldname],
			set: (values) => frm.set_value(values),
		};
	}
}

function suggest_addresses(input) {
	input.data("address-lookup", true);
	const new_session = () => frappe.utils.get_random(20);
	let session = new_session();
	let suggestions = [];
	let enabled = true;
	let timer = null;
	let seq = 0;

	const awesomplete = new Awesomplete(input.get(0), {
		minChars: 0,
		maxItems: 8,
		autoFirst: true,
		sort: false,
		filter: () => true,
		replace: () => {},
		item: (item) =>
			item.value.credit
				? $(
						`<li aria-disabled="true" class="small text-muted">${frappe.utils.escape_html(item.label)}</li>`,
				  ).get(0)
				: $(`<li><div><strong>${frappe.utils.escape_html(item.label)}</strong>
					<div class="small text-muted">${frappe.utils.escape_html(item.value.detail || "")}</div></div></li>`).get(0),
		list: [],
	});

	const country_field = (target) => ["country", "country_address"].find((fieldname) => target.has(fieldname));

	input.on("input", () => {
		const target = address_target(input);
		if (!enabled || !target) return;
		clearTimeout(timer);
		const text = (input.val() || "").trim();
		if (text.length < 3) return;
		timer = setTimeout(async () => {
			const mine = ++seq;
			const field = country_field(target);
			const result = await frappe.xcall("erpnext_shipping.erpnext_shipping.address_lookup.suggest", {
				text,
				country: (field && target.get(field)) || frappe.defaults.get_default("country"),
				session,
			});
			if (!result.enabled) {
				enabled = false;
				return;
			}
			if (mine !== seq) return;
			suggestions = result.suggestions || [];
			const credit = result.attribution ? [{ label: result.attribution, value: { credit: true } }] : [];
			awesomplete.list = suggestions
				.map((row, index) => ({ label: row.label, value: { index, detail: row.detail } }))
				.concat(suggestions.length ? credit : []);
			awesomplete.evaluate();
		}, 250);
	});

	input.on("awesomplete-selectcomplete", async (event) => {
		const target = address_target(input);
		const suggestion = suggestions[event.originalEvent.text.value.index];
		if (!suggestion || !target) return;
		const address =
			suggestion.address ||
			(await frappe.xcall("erpnext_shipping.erpnext_shipping.address_lookup.address", {
				id: suggestion.id,
				session,
			}));
		session = new_session();
		const values = {};
		const line2 = address.address_line2 && !target.has("address_line2");
		values.address_line1 = line2
			? `${address.address_line1}, ${address.address_line2}`
			: address.address_line1;
		for (const fieldname of ["address_line2", "city", "state", "pincode"]) {
			if (target.has(fieldname) && address[fieldname]) values[fieldname] = address[fieldname];
		}
		const field = country_field(target);
		if (field && address.country) values[field] = address.country;
		await target.set(values);
	});
}
