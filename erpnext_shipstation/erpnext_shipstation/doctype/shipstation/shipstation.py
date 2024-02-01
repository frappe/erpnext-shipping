# -*- coding: utf-8 -*-
# Copyright (c) 2020, Frappe Technologies and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import base64
import datetime
import requests
import frappe
import json
import re
from frappe import _
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password
from erpnext_shipstation.erpnext_shipstation.utils import show_error_alert

SHIPSTATION_PROVIDER = 'ShipStation'
BASE_URL = 'https://ssapi.shipstation.com'
CARRIER_CODE = 'stamps_com'

class ShipStation(Document): pass

class ShipStationUtils():
    def __init__(self):
        self.api_password = get_decrypted_password('ShipStation', 'ShipStation', 'api_password', raise_exception=False)
        self.api_id, self.enabled = frappe.db.get_value('ShipStation', 'ShipStation', ['api_id', 'enabled'])

        if not self.enabled:
            link = frappe.utils.get_link_to_form('ShipStation', 'ShipStation', frappe.bold('ShipStation Settings'))
            frappe.throw(_('Please enable ShipStation Integration in {0}'.format(link)), title=_('Mandatory'))

    def get_available_services(self, delivery_to_type, pickup_address,
        delivery_address, shipment_parcel, description_of_content, pickup_date,
        value_of_goods, pickup_contact=None, delivery_contact=None):
        # Retrieve rates at LetMeShip from specification stated.
        if not self.enabled or not self.api_id or not self.api_password:
            return []
        
        self.set_shipstation_specific_fields(pickup_contact, delivery_contact)
        pickup_address.address_title = self.trim_address(pickup_address)
        delivery_address.address_title = self.trim_address(delivery_address)
        parcel_list = self.get_parcel_list(json.loads(shipment_parcel), description_of_content)

        url = BASE_URL + '/shipments/getrates'
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Access-Control-Allow-Origin': 'string'
        }

        payload = {
            "carrierCode": CARRIER_CODE,
            "serviceCode": None,
            "packageCode": None,
            "fromPostalCode": pickup_address.pincode,
            "toState": delivery_address.state,
            "toCountry": delivery_address.country_code,
            "toPostalCode": delivery_address.pincode,
            "toCity": delivery_address.city,
            "weight": {
                "value": parcel_list[0]["weight"] * 1000,
                "units": "grams"
            },
            "dimensions": {
                "units": "centimeters",
                "length": parcel_list[0]["length"],
                "width": parcel_list[0]["width"],
                "height": parcel_list[0]["height"]
            },
            "confirmation": "delivery",
            "residential": delivery_to_type == 'Company'
        }

        try:
            available_services = []
            response_data = requests.post(
                url=url,
                auth=(self.api_id, self.api_password),
                headers=headers,
                data=json.dumps(payload)
            )
            response_data = json.loads(response_data.text)
            
            if 'ExceptionMessage' not in response_data:
                for response in response_data:
                    available_service = self.get_service_dict(response)
                    available_services.append(available_service)

                return available_services
            else:
                frappe.throw(_('An Error occurred while fetching ShipStation prices: {0}')
                .format(response_data['Message']))
        except Exception:
            show_error_alert("fetching ShipStation prices")

        return []

    def create_shipment(self, carrier_code, service_code, package_code, confirmation, ship_date, weight, 
                    dimensions, ship_from, ship_to, insurance_options, internationalOptions,
                    advanced_options, test_label):
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
            tracking_num = response_data['trackingNumber']
            label_data = response_data['labelData']

            # print('orderId: {}'.format(order_id))
            # print('trackingNumber: {}'.format(tracking_num))
            # print('labelData: {}'.format(label_data))

            # 라벨 PDF로 뽑기
            pdf_bytes = base64.b64decode(label_data)  # base64 디코딩
            with open("LabelPDF.pdf", "wb") as pdf_file:  # PDF 파일로 저장
                pdf_file.write(pdf_bytes)

            orderId = 2
            # orderId로 shipments 리스트를 불러옴
            list_shipments_url = f'{BASE_URL}/shipments?orderId={orderId}'
            list_response_response = requests.get(
                url=list_shipments_url,
                auth={self.api_id, self.api_password}
            )

            list_response_data = json.loads(list_response_response.text)
            print(list_response_data)
            
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

    def get_label(self, shipment_id):
        # Retrieve shipment label from ShipStation
        try:
            headers = {
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Access-Control-Allow-Origin': 'string'
            }
            url = 'https://api.letmeship.com/v1/shipments/{id}/documents?types=LABEL'.format(id=shipment_id)
            shipment_label_response = requests.get(
                url,
                auth=(self.api_id, self.api_password),
                headers=headers
            )
            shipment_label_response_data = json.loads(shipment_label_response.text)
            if 'documents' in shipment_label_response_data:
                for label in shipment_label_response_data['documents']:
                    if 'data' in label:
                        return json.dumps(label['data'])
            else:
                frappe.throw(_('Error occurred while printing Shipment: {0}')
                    .format(shipment_label_response_data['message']))
        except Exception:
            show_error_alert("printing LetMeShip Label")

    def get_tracking_data(self, shipment_id):
        from erpnext_shipstation.erpnext_shipstation.utils import get_tracking_url
        # return letmeship tracking data
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Access-Control-Allow-Origin': 'string'
        }
        try:
            url = 'https://api.letmeship.com/v1/tracking?shipmentid={id}'.format(id=shipment_id)
            tracking_data_response = requests.get(
                url,
                auth=(self.api_id, self.api_password),
                headers=headers
            )
            tracking_data = json.loads(tracking_data_response.text)
            if 'awbNumber' in tracking_data:
                tracking_status = 'In Progress'
                if tracking_data['lmsTrackingStatus'].startswith('DELIVERED'):
                    tracking_status = 'Delivered'
                if tracking_data['lmsTrackingStatus'] == 'RETURNED':
                    tracking_status = 'Returned'
                if tracking_data['lmsTrackingStatus'] == 'LOST':
                    tracking_status = 'Lost'
                tracking_url = get_tracking_url(
                    carrier=tracking_data['carrier'],
                    tracking_number=tracking_data['awbNumber']
                )
                return {
                    'awb_number': tracking_data['awbNumber'],
                    'tracking_status': tracking_status,
                    'tracking_status_info': tracking_data['lmsTrackingStatus'],
                    'tracking_url': tracking_url,
                }
            elif 'message' in tracking_data:
                frappe.throw(_('Error occurred while updating Shipment: {0}')
                    .format(tracking_data['message']))
        except Exception:
            show_error_alert("updating LetMeShip Shipment")

    def generate_payload(self, pickup_address, pickup_contact, delivery_address, delivery_contact,
        description_of_content, value_of_goods, parcel_list, pickup_date, service_info=None):
        payload = {
            'pickupInfo': self.get_pickup_delivery_info(pickup_address, pickup_contact),
            'deliveryInfo': self.get_pickup_delivery_info(delivery_address, delivery_contact),
            'shipmentDetails': {
                'contentDescription': description_of_content,
                'shipmentType': 'PARCEL',
                'shipmentSettings': {
                    'saturdayDelivery': False,
                    'ddp': False,
                    'insurance': False,
                    'pickupOrder': False,
                    'pickupTailLift': False,
                    'deliveryTailLift': False,
                    'holidayDelivery': False,
                },
                'goodsValue': value_of_goods,
                'parcelList': parcel_list,
                'pickupInterval': {
                    'date': pickup_date
                }
            }
        }

        if service_info:
            payload['service'] = {
                'baseServiceDetails': {
                    'id': service_info['id'],
                    'name': service_info['service_name'],
                    'carrier': service_info['carrier'],
                    'priceInfo': service_info['price_info'],
                },
                'supportedExWorkType': [],
                'messages': [''],
                'description': '',
                'serviceInfo': '',
            }
            payload['shipmentNotification'] = {
                'trackingNotification': {
                    'deliveryNotification': True,
                    'problemNotification': True,
                    'emails': [],
                    'notificationText': '',
                },
                'recipientNotification': {
                    'notificationText': '',
                    'emails': []
                }
            }
            payload['labelEmail'] = True
        return payload

    def trim_address(self, address):
        # LetMeShip has a limit of 30 characters for Company field
        if len(address.address_title) > 30:
            return address.address_title[:30]

    def get_service_dict(self, response):
        """Returns a dictionary with service info."""
        available_service = frappe._dict()
        # basic_info = response['baseServiceDetails']
        # price_info = basic_info['priceInfo']
        # available_service.service_provider = SHIPSTATION_PROVIDER
        # available_service.id = basic_info['id']
        # available_service.carrier = basic_info['carrier']
        # available_service.carrier_name = basic_info['name']
        # available_service.service_name = ''
        # available_service.is_preferred = 0
        # available_service.real_weight = price_info['realWeight']
        # available_service.total_price = price_info['netPrice']
        # available_service.price_info = price_info
        available_service.service_provider = SHIPSTATION_PROVIDER
        available_service.service_code = response['serviceCode']
        available_service.servicename = response['serviceName']
        available_service.carriername = 'Stamps.com'
        available_service.is_preferred = 0
        available_service.total_price = response["shipmentCost"]
        available_service.price_info = response["shipmentCost"]
        available_service.other_cost = response["otherCost"]

        return available_service

    def set_shipstation_specific_fields(self, pickup_contact, delivery_contact):
        pickup_contact.phone_prefix = pickup_contact.phone[:3]
        pickup_contact.phone = re.sub('[^A-Za-z0-9]+', '', pickup_contact.phone[3:])

        pickup_contact.title = 'MS'
        if pickup_contact.gender == 'Male':
            pickup_contact.title = 'MR'

        delivery_contact.phone_prefix = delivery_contact.phone[:3]
        delivery_contact.phone = re.sub('[^A-Za-z0-9]+', '', delivery_contact.phone[3:])

        delivery_contact.title = 'MS'
        if delivery_contact.gender == 'Male':
            delivery_contact.title = 'MR'

    def get_parcel_list(self, shipment_parcel, description_of_content):
        parcel_list = []
        for parcel in shipment_parcel:
            formatted_parcel = {}
            formatted_parcel['height'] = parcel.get('height')
            formatted_parcel['width'] = parcel.get('width')
            formatted_parcel['length'] = parcel.get('length')
            formatted_parcel['weight'] = parcel.get('weight')
            formatted_parcel['quantity'] = parcel.get('count')
            formatted_parcel['contentDescription'] = description_of_content
            parcel_list.append(formatted_parcel)
        return parcel_list

    def get_pickup_delivery_info(self, address, contact):
        return {
            'address': {
                'countryCode': address.country_code,
                'zip': address.pincode,
                'city': address.city,
                'street': address.address_line1,
                'addressInfo1': address.address_line2,
                'houseNo': '',
            },
            'company': address.address_title,
            'person': {
                'title': contact.title,
                'firstname': contact.first_name,
                'lastname': contact.last_name
            },
            'phone': {
                'phoneNumber': contact.phone,
                'phoneNumberPrefix': contact.phone_prefix
            },
            'email': contact.email
        }
