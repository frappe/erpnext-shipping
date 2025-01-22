import json

import frappe
import requests
from frappe.utils.password import get_decrypted_password
from erpnext_shipping.erpnext_shipping.utils  import  get_pickup_location

SHIPROCKET_PROVIDER = "Shiprocket"


@frappe.whitelist()
def generate_token(doc):
	try:
		url = "https://apiv2.shiprocket.in/v1/external/auth/login"

		payload = json.dumps({"email": doc.user_key, "password": doc.get_password("user_secret")})
		headers = {"Content-Type": "application/json"}

		response = requests.request("POST", url, headers=headers, data=payload)

		response_dict = json.loads(response.text)
		if "token" in response_dict:
			token = response_dict["token"]
			doc.barer_key = token
		else:
			frappe.throw("Invalid email and password combination.")

	except requests.exceptions.RequestException as e:
		frappe.log_error(title="Shiprocket Authentication Error", message=str(e))


@frappe.whitelist()
def get_shipping_address(bearer, address_id=None):
	url = "https://apiv2.shiprocket.in/v1/external/settings/company/pickup"

	headers = {"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"}

	response = requests.request("GET", url, headers=headers)
	response_dict = json.loads(response.text)
	data = response_dict.get("data")
	if data:
		shipping_address = data["shipping_address"]
		if not address_id:
			return shipping_address
		for address in shipping_address:
			if str(address["id"]) == address_id:
				return address


def get_available_services(token, parcels, delivery_address_name, pickup_address_name, total_weight):
	weight = total_weight
	pickup_postcode = get_post_code(pickup_address_name)
	delivery_postcode = get_post_code(delivery_address_name)
	if not token:
		return
	url = "https://apiv2.shiprocket.in/v1/external/courier/serviceability"
	headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
	payload = {
		"pickup_postcode": pickup_postcode,
		"delivery_postcode": delivery_postcode,
		"cod": 0,
		"weight": weight,
	}
	response = requests.get(url, json=payload, headers=headers)
	if response.status_code == 200:
		services_available = response.json().get("data", [])
		services_available = services_available["available_courier_companies"]
		available_services = []
		for services in services_available:
			# if services and check_weight(parcels, services):
			available_service = get_service_dict(services, parcels, token)
			available_services.append(available_service)
		return available_services
	else:
		return {"error": response.json()}


def check_weight(parcels, services):
	chnarge_weight = float(services["charge_weight"])


def get_post_code(address_name):
	if not frappe.db.exists("Address", address_name):
		return
	address = frappe.get_doc("Address", address_name)
	return address.pincode

def get_service_dict(service, parcels: list[dict], token):
	"""Returns a dictionary with service info."""
	available_service = frappe._dict()
	available_service.service_provider = "Shiprocket"
	available_service.carrier = str(service["courier_company_id"])
	available_service.service_name = service["courier_name"]
	available_service.currency = "INR"
	available_service.token = token
	price = service["freight_charge"]
	available_service.total_price = total_parcel_price(price, parcels)

	available_service.service_id = service["id"]

	return available_service


def total_parcel_price(price, parcels):
	count = 0
	for parcel in parcels:
		count += parcel.get("count")
	return float(price) * count


def create_shiprocket_shipment(
	shipment,
	token,
	delivery_company_name,
	delivery_address,
	shipment_parcel,
	description_of_content,
	pickup_date,
	value_of_goods,
	service_info,
	pickup_company,
	total_weight,
	pickup_contact=None,
	delivery_contact=None,
):
	
	shipment = frappe.get_doc("Shipment", shipment)
	if not (shipment, token, pickup_company, total_weight):
		return
	pickup_location_id = get_pickup_location(pickup_company, "Shiprocket", True)
 
	if not pickup_location_id:
		frappe.throw("Unable to find Pickup Location for the given company.")
  
	mentiond_pickup_address = get_shipping_address(pickup_location_id['bearer_key'],pickup_location_id['pickup_id'])
	if not mentiond_pickup_address:
		frappe.throw("Unable to find Pickup Address for the given company.")
  
	shipment_name = shipment.name

	url = "https://apiv2.shiprocket.in/v1/external/orders/create/adhoc"

	shipment_parcel = json.loads(shipment_parcel)
	parcels = [
		{
			"length": parcel["length"],
			"breadth": parcel["width"],
			"height": parcel["height"],
			"weight": parcel["weight"],
			"units": parcel["count"],
		}
		for parcel in shipment_parcel
	]

	payload = {
		"order_id": shipment_name,
		"order_date": pickup_date,
		"channel_id": "",
		"comment": "",
		"reseller_name": "",
		"company_name": delivery_company_name,
		"billing_customer_name": delivery_contact["first_name"],
		"billing_last_name": delivery_contact["last_name"],
		"billing_address": delivery_address["address_line1"],
		"billing_address_2": delivery_address["address_line2"],
		"billing_isd_code": "",
		"billing_city": delivery_address["city"],
		"billing_pincode": delivery_address["pincode"],
		"billing_state": "Tamil Nadu",
		"billing_country": delivery_address["country"],
		"billing_email": delivery_contact["email_id"],
		"billing_phone": delivery_contact["phone"],
		"billing_alternate_phone": "",
		"shipping_is_billing": True,
		"shipping_customer_name": pickup_contact["first_name"],
		"shipping_last_name": pickup_contact["last_name"],
		"shipping_address": mentiond_pickup_address["address"],
		"shipping_address_2": mentiond_pickup_address["address_2"],
		"shipping_city": mentiond_pickup_address["city"],
		"shipping_pincode": mentiond_pickup_address["pin_code"],
		"shipping_country": mentiond_pickup_address["country"],
		"shipping_state": mentiond_pickup_address['state'],
		"shipping_email": pickup_contact["email_id"],
		"shipping_phone": pickup_contact["phone"],
		"order_items": [
			{
				"name": description_of_content,
				"sku": "N/A",
				"units": sum(parcel["count"] for parcel in shipment_parcel),
				"selling_price": value_of_goods,
				"discount": "",
				"tax": "",
				"hsn": "",
			}
		],
		"payment_method": "cod",
		"shipping_charges": service_info["total_price"],
		"giftwrap_charges": "",
		"transaction_charges": "",
		"total_discount": "",
		"sub_total": int(value_of_goods) * sum(parcel["count"] for parcel in shipment_parcel),
		"length": parcels[0]["length"],
		"breadth": parcels[0]["breadth"],
		"height": parcels[0]["height"],
		"weight": total_weight,
		"ewaybill_no": "",
		"customer_gstin": "",
		"invoice_number": "",
		"order_type": "",
		"pickup_location": mentiond_pickup_address['pickup_location'],
		"courier_company_id": service_info['carrier'],
	}

	headers = {
		"Content-Type": "application/json",
		"Authorization": f"Bearer {token}",
	}

	response = requests.post(url, json=payload, headers=headers)

	if response.status_code == 200:
		shipment_response = response.json()
		if shipment_response['status'] == "CANCELED":
			frappe.throw("Could not make the shippment")
   
		shipment_id = shipment_response.get("shipment_id")

		if shipment_id:
			ship_now_url = f"https://apiv2.shiprocket.in/v1/external/courier/assign/awb"
			ship_now_payload = {
				"shipment_id": shipment_id,
				"courier_id": service_info.get("carrier"),
			}
   
			ship_now_headers = {
				"Content-Type": "application/json",
				"Authorization": f"Bearer {token}",
			}

			ship_now_response = requests.post(ship_now_url, json=ship_now_payload, headers=ship_now_headers)

			if ship_now_response.status_code == 200:
				ship_now_response = ship_now_response.json()
				if not ship_now_response['awb_assign_status']:
					frappe.throw('Cannot create shipment, Shipping order clreated')
				ship_now_response_data = ship_now_response['response']
				return {
					"service_provider": "Shiprocket",
					"shipment_id": shipment_id,
					"carrier": "Shiprocket",
					"carrier_service": service_info["service_name"],
					"shipment_amount": service_info["total_price"],
					"awb_number": ship_now_response_data['data']['awb_code'],
				}
			else:
				print("Failed to move shipment to 'Ship Now':", ship_now_response.json())
	else:
		print("Failed to create shipment:", response.json())


@frappe.whitelist()
def calculate_total_weight(shipment_parcel):
	shipment_parcel = json.loads(shipment_parcel)
	total_weight = sum([parcels.get("weight",0) for parcels in shipment_parcel])
	return total_weight

@frappe.whitelist()
def generate_lable(shipment):
	doc = frappe.get_doc("Shipment", shipment).as_dict()
	key = get_pickup_location(doc['pickup_company'], "Shiprocket", True)
	bearer_key = key['bearer_key']
	
	url = "https://apiv2.shiprocket.in/v1/external/courier/generate/label"

	payload = json.dumps({
	"shipment_id": [doc["shipment_id"]]
	})
	headers = {
		'Content-Type': 'application/json',
		'Authorization': f'Bearer {bearer_key}'
	}

	response = requests.request("POST", url, headers=headers, data=payload)
	response = response.json()
	if not response['label_created']:
		frappe.throw('Failed to generate label')
	return response['label_url']
	

@frappe.whitelist()
def track_order(shipment, shipment_id):
    doc = frappe.get_doc("Shipment", shipment).as_dict()
    key = get_pickup_location(doc['pickup_company'], "Shiprocket", True)
    bearer_key = key['bearer_key']

    url = f"https://apiv2.shiprocket.in/v1/external/courier/track/shipment/{shipment_id}"

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {bearer_key}'
    }

    response = requests.request("GET", url, headers=headers)
    response = response.json()

    if not response.get('tracking_data', {}).get('track_status'):
        frappe.throw('Failed to track order')

    tracking_data = response['tracking_data']

    awb_numbers = [track.get('awb_code', '') for track in tracking_data.get('shipment_track', [])]
    tracking_status = [track.get('current_status', '') for track in tracking_data.get('shipment_track', [])]
    tracking_status_info = [track.get('courier_name', '') for track in tracking_data.get('shipment_track', [])]
    tracking_urls = [tracking_data.get('track_url', '')]

    return {
        "awb_number": ", ".join(filter(None, awb_numbers)),
        "tracking_status": ", ".join(filter(None, tracking_status)),
        "tracking_status_info": ", ".join(filter(None, tracking_status_info)),
        "tracking_url": ", ".join(filter(None, tracking_urls)),
    }

