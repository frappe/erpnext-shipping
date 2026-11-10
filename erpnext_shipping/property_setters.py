def get_property_setters():
	return [
		# Parcel weights in grams matter for courier rates; ERPNext rounds to 0.1 kg by default
		("Shipment Parcel", "weight", "precision", "3"),
		# ("Address", "state", "hidden", 1),
		# ("Address", None, "track_changes", 1),
	]
