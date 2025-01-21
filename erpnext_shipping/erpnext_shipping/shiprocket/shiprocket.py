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
	total_weight = sum([parcels.get("weight",0) for parcels in shipment_parcel])
	return total_weight