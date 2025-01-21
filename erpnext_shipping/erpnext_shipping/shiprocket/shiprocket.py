import json

import frappe
import requests
from frappe.utils.password import get_decrypted_password

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
def get_shipping_address(bearer):
	url = "https://apiv2.shiprocket.in/v1/external/settings/company/pickup"

	headers = {"Content-Type": "application/json", "Authorization": f"Bearer {bearer}"}

	response = requests.request("GET", url, headers=headers)

	print(response.text)


def get_available_services(token, parcels, delivery_address_name, pickup_address_name, total_weight=None):
	weight = "10"
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
			if services and check_weight(parcels, services):
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
	pickup_address,
	delivery_company_name,
	delivery_address,
	shipment_parcel,
	description_of_content,
	pickup_date,
	value_of_goods,
	service_info,
	pickup_contact=None,
	delivery_contact=None,
):
	if not (shipment, token):
		return
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
		"shipping_address": pickup_address["address_line1"],
		"shipping_address_2": pickup_address["address_line2"],
		"shipping_city": pickup_address["city"],
		"shipping_pincode": pickup_address["pincode"],
		"shipping_country": pickup_address["country"],
		"shipping_state": "Tamil Nadu",
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
		"weight": parcels[0]["weight"],
		"ewaybill_no": "",
		"customer_gstin": "",
		"invoice_number": "",
		"order_type": "",
		"pickup_location": "work",
		"courier_company_id": "",
	}

	headers = {
		"Content-Type": "application/json",
		"Authorization": f"Bearer {token}",
	}

	response = requests.post(url, json=payload, headers=headers)

	if response.status_code == 200:
		print("Shipment created successfully:", response.json())
	else:
		print("Failed to create shipment:", response.json())


@frappe.whitelist()
def calculate_total_weight(shipment_parcel):
	total_weight = sum(parcels.get("weight", 0) for parcels in shipment_parcel)
	return total_weight
