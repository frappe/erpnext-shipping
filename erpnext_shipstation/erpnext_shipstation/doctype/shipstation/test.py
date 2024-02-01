from __future__ import unicode_literals
import base64
import datetime
import requests
import json
from frappe import _

SHIPSTATION_PROVIDER = 'ShipStation'
BASE_URL = 'https://ssapi.shipstation.com'
CARRIER_CODE = 'stamps_com'


def create_shipment():
    # Create a transaction at ShipStation
    # if not self.enabled or not self.api_id or not self.api_password:
    #    return []

    # self.set_shipstation_specific_fields(pickup_contact, delivery_contact)
    # pickup_address.address_title = self.trim_address(pickup_address)
    # delivery_address.address_title = self.trim_address(delivery_address)
    # parcel_list = self.get_parcel_list(json.loads(shipment_parcel), description_of_content)

    create_shipment_url = f'{BASE_URL}/shipments/createlabel'
    
    # Basic Authentication 헤더 생성
    auth_header = base64.b64encode(f"{self.api_id}:{self.api_password}".encode("utf-8")).decode("utf-8")
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/json"
    }

    # Create Shipment에 넣은 하드코딩 정보
    carrier_code = "stamps_com"
    service_code = "usps_first_class_mail"
    package_code = "package"
    confirmation = "delivery"
    ship_date = datetime.now().isoformat()
    weight = {
        "value": 3,
        "units": "ounces"
    }
    dimensions = {
        "units": "inches",
        "length": 7,
        "width": 5,
        "height": 6
    }
    ship_from = {
        "name": "Jason Hodges",
        "company": "ShipStation",
        "street1": "2815 Exposition Blvd",
        "street2": "Ste 2353242",
        "street3": None,
        "city": "Austin",
        "state": "TX",
        "postalCode": "78703",
        "country": "US",
        "phone": "+1",
        "residential": False
    }
    ship_to = {
        "name": "The President",
        "company": "US Govt",
        "street1": "1600 Pennsylvania Ave",
        "street2": "Oval Office",
        "street3": None,
        "city": "Washington",
        "state": "DC",
        "postalCode": "20500",
        "country": "US",
        "phone": None,
        "residential": False
    }
    insurance_options = None
    internationalOptions = None
    advanced_options = None
    test_label = True

    body = {
        "carrierCode": carrier_code,
        "serviceCode": service_code,
        "packageCode": package_code,
        "confirmation": confirmation,
        "shipDate": ship_date,
        "weight": weight,
        "dimensions": dimensions,
        "shipFrom": ship_from,
        "shipTo": ship_to,
        "insuranceOptions": insurance_options,
        "internationalOptions": internationalOptions,
        "advancedOptions": advanced_options,
        "testLabel": test_label
    }

    # print(body)

    try:
        response_data = requests.post(
            url=create_shipment_url,
            auth=(self.api_id, self.api_password),
            headers=headers,
            data=json.dumps(body)
        )

        response_data = json.loads(response_data.text)
        # print(response_data)

        order_id = response_data['orderId']
        print('orderId: {}'.format(order_id))

        tracking_num = response_data['trackingNumber']
        print('trackingNumber: {}'.format(tracking_num))

        label_data = response_data['labelData']
        print('labelData: {}'.format(label_data))

        # 라벨 PDF로 뽑기
        # base64 디코딩
        pdf_bytes = base64.b64decode(label_data)

        # PDF 파일로 저장
        with open("LabelPDF.pdf", "wb") as pdf_file:
            pdf_file.write(pdf_bytes)

        list_shipments_url = f'{BASE_URL}/shipments'
        list_response_response = requests.post(
            url=list_shipments_url,
            auth=(self.api_id, self.api_password),
            headers=headers,
            data=json.dumps(body)
        )

        print(list_response_response)
        
        # if 'shipmentId' in response_data:
        #    shipment_amount = response_data['service']['priceInfo']['totalPrice']
        #    awb_number = ''
        #    url = 'https://api.letmeship.com/v1/shipments/{id}'.format(id=response_data['shipmentId'])
        #    tracking_response = requests.get(url, auth=(self.api_id, self.api_password),headers=headers)
        #    tracking_response_data = json.loads(tracking_response.text)
        #    if 'trackingData' in tracking_response_data:
        #       for parcel in tracking_response_data['trackingData']['parcelList']:
        #          if 'awbNumber' in parcel:
        #             awb_number = parcel['awbNumber']
        #    return {
        #       'service_provider': SHIPSTATION_PROVIDER,
        #       'shipment_id': response_data['shipmentId'],
        #       'carrier': service_info['carrier'],
        #       'carrier_service': service_info['service_name'],
        #       'shipment_amount': shipment_amount,
        #       'awb_number': awb_number,
        #    }
        # elif 'message' in response_data:
        #    frappe.throw(_('An Error occurred while creating Shipment: {0}')
            # .format(response_data['message']))

    except Exception as e:
        print(f"에러 발생: {e}")


create_shipment()