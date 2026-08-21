DELHIVERY_PROVIDER = "Delhiveryone"

DELHIVERY_API_BASE_URL = "https://track.delhivery.com"

DELHIVERY_CREATE_SHIPMENT_ENDPOINT = "/api/cmu/create.json"
DELHIVERY_TRACKING_ENDPOINT = "/api/v1/packages/json/"
DELHIVERY_PINCODE_CHECK_ENDPOINT = "/c/api/pin-codes/json/"
DELHIVERY_RATE_CALCULATOR_ENDPOINT = "/api/kinko/v1/invoice/charges/.json"
DELHIVERY_PACKING_SLIP_ENDPOINT = "/api/p/packing_slip"

DELHIVERY_STATUS_MAPPING = {
	"Manifested": "In Progress",
	"Manifestation Pending": "In Progress",
	"Pickup Scheduled": "In Progress",
	"Pickup Pending": "In Progress",
	"Picked Up": "In Progress",
	"In Transit": "In Progress",
	"Dispatched": "In Progress",
	"Bagged": "In Progress",
	"Received at Facility": "In Progress",
	"Reached Destination Hub": "In Progress",
	"Out For Delivery": "In Progress",
	"Delivered": "Delivered",
	"RTO": "Returned",
	"RTO In Transit": "Returned",
	"RTO Delivered": "Returned",
	"Returned": "Returned",
	"Cancelled": "Cancelled",
	"Lost": "Lost",
}
