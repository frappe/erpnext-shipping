TEST_BASE_URL_API = "https://api-test.envia.com"
TEST_BASE_URL_QUERY = "https://queries-test.envia.com"
BASE_URL_API = "https://api.envia.com"
BASE_URL_QUERY = "https://queries.envia.com"

# ERPNext's Shipment "Tracking Status" field only allows: In Progress, Delivered, Returned, Lost.
# Envia reports ~28 granular statuses (by numeric id or name), so map each to the closest
# ERPNext option.
ENVIA_TRACKING_STATUSES = [
	(1, "Created", "In Progress"),
	(2, "Shipped", "In Progress"),
	(3, "Delivered", "Delivered"),
	(4, "Canceled", "Returned"),
	(5, "Information", "In Progress"),
	(6, "N/A", "In Progress"),
	(7, "Pending", "In Progress"),
	(8, "Picked Up", "In Progress"),
	(9, "Out for Delivery", "In Progress"),
	(10, "Lost", "Lost"),
	(11, "Returned", "Returned"),
	(12, "Pickup at Office", "In Progress"),
	(13, "Delivered at Origin", "Returned"),
	(14, "Damaged", "Lost"),
	(15, "Redirected", "In Progress"),
	(16, "Out for Pickup", "In Progress"),
	(17, "1 delivery attempt", "In Progress"),
	(18, "2 delivery attempts", "In Progress"),
	(19, "3 delivery attempts", "In Progress"),
	(20, "Return problem", "Returned"),
	(21, "Address error", "In Progress"),
	(22, "Undeliverable", "Returned"),
	(23, "Delayed", "In Progress"),
	(24, "Rejected", "Returned"),
	(25, "1 pickup attempt", "In Progress"),
	(26, "Partially Shipped", "In Progress"),
	(27, "Partially Delivered", "In Progress"),
	(28, "Delivery Attempt", "In Progress"),
]

# Lookup tables keyed by numeric id and by the normalized (lower-cased) status name.
ENVIA_STATUS_BY_ID = {id: erpnext_status for id, _, erpnext_status in ENVIA_TRACKING_STATUSES}
ENVIA_STATUS_BY_NAME = {name.lower(): erpnext_status for _, name, erpnext_status in ENVIA_TRACKING_STATUSES}
