from frappe.custom.doctype.customize_form.customize_form import docfield_properties, doctype_properties
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def identity(value):
	"""Used for dummy translation"""
	return value


def make_property_setters(property_setter):
	for prop_setter in property_setter:
		if prop_setter[1]:
			for_doctype = False
			fieldtype = docfield_properties[prop_setter[2]]
		else:
			for_doctype = True
			# Use workaround for field_order property, maybe add to incustomize_form.py (?)
			fieldtype = doctype_properties[prop_setter[2]] if prop_setter[2] != "field_order" else "Data"

		make_property_setter(*prop_setter[:4], fieldtype, for_doctype=for_doctype)
