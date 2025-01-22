import json

import frappe
import requests
from frappe.utils.password import get_decrypted_password


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
def calculate_total_weight(shipment_parcel):
	shipment_parcel = json.loads(shipment_parcel)
	total_weight = sum([parcels.get("weight",0) for parcels in shipment_parcel])
	return total_weight

def get_tracking_details(shipment_id, bearer):
	try:
		url = "https://apiv2.shiprocket.in/v1/external/courier/track/shipment/{shipment_id}"

		payload = {}
		headers = {
		'Content-Type': 'application/json',
		 'Authorization':f'Bearer {bearer}'
		}

		response = requests.request("GET", url, headers=headers, data=payload)

		print(response.text)

	except requests.exceptions.RequestException as e:
		frappe.log_error(title="Shiprocket Authentication Error", message=str(e))

