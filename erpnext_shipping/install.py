import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from .custom_fields import get_custom_fields
from .property_setters import get_property_setters
from .utils import make_property_setters


def after_install():
	create_custom_fields(get_custom_fields())
	make_property_setters(get_property_setters())
	# Float columns are sized by precision, so apply the parcel weight precision to the column too
	frappe.db.updatedb("Shipment Parcel")
	copy_to_variants(("shipping_parcel_template", "ships_in_own_box"))


def copy_to_variants(fieldnames) -> None:
	"""Have ERPNext copy the item's shipping fields to its variants (Item Variant Settings)."""
	settings = frappe.get_single("Item Variant Settings")
	copied = {row.field_name for row in settings.fields}
	missing = [name for name in fieldnames if name not in copied and frappe.get_meta("Item").has_field(name)]
	if not missing:
		return
	for name in missing:
		settings.append("fields", {"field_name": name})
	settings.save(ignore_permissions=True)
