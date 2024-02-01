from __future__ import unicode_literals
import requests
import json
from datetime import datetime
import base64

SHIPSTATION_PROVIDER = 'ShipStation'
BASE_URL = 'https://ssapi.shipstation.com'

carrier_code = "stamps_com"
service_code = "usps_first_class_mail"
package_code = "package"
confirmation = "delivery"
shipDate = datetime.now().isoformat()
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
insuranceOptions = None
internationalOptions = None
advancedOptions = None
testLabel = True

def create_shipment(carrier_code, service_code, package_code, confirmation, ship_date, weight, 
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

  # 사용자 이름과 비밀번호
  # 여기에 api key랑 api secret 넣어서 실행하시면 됩니당
  # username=
  # password=

  # Basic Authentication 헤더 생성
  auth_header = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("utf-8")
  headers = {
    "Authorization": f"Basic {auth_header}",
    "Content-Type": "application/json"

  }

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
        auth=("57267da19f264928892b464ec0290748", "48f594033ae54a9fb8b340bd1a3d93f7"),
        headers=headers,
        data=json.dumps(body)
    )

    # print("보내는 거까지 됐다")

    response_data = json.loads(response_data.text)
    # print(response_data)

    order_id = response_data['orderId']
    print('orderId: {}'.format(order_id))

    tracking_num = response_data['trackingNumber']
    print('trackingNumber: {}'.format(tracking_num))

    label_data = response_data['labelData']
    print('labelData: {}'.format(label_data))
    create_label_pdf(label_data)

    list_shipments_url = f'{BASE_URL}/shipments'
    
    
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

def create_label_pdf(label): 
   # base64 디코딩
   pdf_bytes = base64.b64decode(label)

   # PDF 파일로 저장
   with open("LabelPDF.pdf", "wb") as pdf_file:
      pdf_file.write(pdf_bytes)



# def generate_payload(self, pickup_address, pickup_contact, delivery_address, delivery_contact,
#    description_of_content, value_of_goods, parcel_list, pickup_date, service_info=None):
#    payload = {
#       'pickupInfo': self.get_pickup_delivery_info(pickup_address, pickup_contact),
#       'deliveryInfo': self.get_pickup_delivery_info(delivery_address, delivery_contact),
#       'shipmentDetails': {
#          'contentDescription': description_of_content,
#          'shipmentType': 'PARCEL',
#          'shipmentSettings': {
#             'saturdayDelivery': False,
#             'ddp': False,
#             'insurance': False,
#             'pickupOrder': False,
#             'pickupTailLift': False,
#             'deliveryTailLift': False,
#             'holidayDelivery': False,
#          },
#          'goodsValue': value_of_goods,
#          'parcelList': parcel_list,
#          'pickupInterval': {
#             'date': pickup_date
#          }
#       }
#    }

#    if service_info:
#       payload['service'] = {
#          'baseServiceDetails': {
#             'id': service_info['id'],
#             'name': service_info['service_name'],
#             'carrier': service_info['carrier'],
#             'priceInfo': service_info['price_info'],
#          },
#          'supportedExWorkType': [],
#          'messages': [''],
#          'description': '',
#          'serviceInfo': '',
#       }
#       payload['shipmentNotification'] = {
#          'trackingNotification': {
#             'deliveryNotification': True,
#             'problemNotification': True,
#             'emails': [],
#             'notificationText': '',
#          },
#          'recipientNotification': {
#             'notificationText': '',
#             'emails': []
#          }
#       }
#       payload['labelEmail'] = True
#    return payload

# def trim_address(self, address):
#    # LetMeShip has a limit of 30 characters for Company field
#    if len(address.address_title) > 30:
#       return address.address_title[:30]

# def set_shipstation_specific_fields(self, pickup_contact, delivery_contact):
#    pickup_contact.phone_prefix = pickup_contact.phone[:3]
#    pickup_contact.phone = re.sub('[^A-Za-z0-9]+', '', pickup_contact.phone[3:])

#    pickup_contact.title = 'MS'
#    if pickup_contact.gender == 'Male':
#       pickup_contact.title = 'MR'

#    delivery_contact.phone_prefix = delivery_contact.phone[:3]
#    delivery_contact.phone = re.sub('[^A-Za-z0-9]+', '', delivery_contact.phone[3:])

#    delivery_contact.title = 'MS'
#    if delivery_contact.gender == 'Male':
#       delivery_contact.title = 'MR'

# def get_parcel_list(self, shipment_parcel, description_of_content):
#    parcel_list = []
#    for parcel in shipment_parcel:
#       formatted_parcel = {}
#       formatted_parcel['height'] = parcel.get('height')
#       formatted_parcel['width'] = parcel.get('width')
#       formatted_parcel['length'] = parcel.get('length')
#       formatted_parcel['weight'] = parcel.get('weight')
#       formatted_parcel['quantity'] = parcel.get('count')
#       formatted_parcel['contentDescription'] = description_of_content
#       parcel_list.append(formatted_parcel)
#    return parcel_list

# def get_pickup_delivery_info(self, address, contact):
#    return {
#       'address': {
#          'countryCode': address.country_code,
#          'zip': address.pincode,
#          'city': address.city,
#          'street': address.address_line1,
#          'addressInfo1': address.address_line2,
#          'houseNo': '',
#       },
#       'company': address.address_title,
#       'person': {
#          'title': contact.title,
#          'firstname': contact.first_name,
#          'lastname': contact.last_name
#       },
#       'phone': {
#          'phoneNumber': contact.phone,
#          'phoneNumberPrefix': contact.phone_prefix
#       },
#       'email': contact.email
#    }



create_shipment(carrier_code, service_code, package_code, confirmation, shipDate, weight, 
                dimensions, ship_from, ship_to, insuranceOptions, internationalOptions,
                advancedOptions, testLabel)

"""
 LetMeShip
 {
  "auxiliaryInfo": {
    "address": {
      "addressInfo1": "string",
      "addressInfo2": "string",
      "city": "string",
      "countryCode": "DE",
      "houseNo": "string",
      "specialField": "string",
      "stateCode": "string",
      "street": "string",
      "zip": 22529
    },
    "company": "string",
    "email": "email@email.com",
    "person": {
      "firstname": "string",
      "lastname": "string",
      "title": "MRS"
    },
    "phone": {
      "phoneNumber": "07111444555",
      "phoneNumberPrefix": "+49"
    }
  },
  "cinData": {
    "cinDataItem": [
      {
        "commodityCode": "string",
        "grossWeight": 0,
        "itemDescription": "string",
        "manufactureCountry": "string",
        "netWeight": 0,
        "order": 0,
        "quantity": 0,
        "quantityUnitOfMeasure": "BOXES",
        "unitPrice": 0
      }
    ],
    "eori": "string",
    "exportReason": "COMMERCIAL_PURPOSE_OR_SALE",
    "invDate": "2022-12-31",
    "invoiceNumber": "string",
    "invoiceValue": 0,
    "mrn": 123456789102314540
  },
  "deliveryInfo": {
    "address": {
      "addressInfo1": "string",
      "addressInfo2": "string",
      "city": "string",
      "countryCode": "DE",
      "houseNo": "string",
      "specialField": "string",
      "stateCode": "string",
      "street": "string",
      "zip": 22529
    },
    "company": "string",
    "email": "email@email.com",
    "person": {
      "firstname": "string",
      "lastname": "string",
      "title": "MRS"
    },
    "phone": {
      "phoneNumber": "07111444555",
      "phoneNumberPrefix": "+49"
    }
  },
  "enableETD": true,
  "labelEmail": true,
  "labelOptions": {
    "labelSize": "DEFAULT"
  },
  "pickupInfo": {
    "address": {
      "addressInfo1": "string",
      "addressInfo2": "string",
      "city": "string",
      "countryCode": "DE",
      "houseNo": "string",
      "specialField": "string",
      "stateCode": "string",
      "street": "string",
      "zip": 22529
    },
    "company": "string",
    "email": "email@email.com",
    "person": {
      "firstname": "string",
      "lastname": "string",
      "title": "MRS"
    },
    "phone": {
      "phoneNumber": "07111444555",
      "phoneNumberPrefix": "+49"
    }
  },
  "service": {
    "baseServiceDetails": {
      "carrier": "FedEx",
      "carrierImage": "string",
      "id": 0,
      "name": "string",
      "priceInfo": {
        "basePrice": 0,
        "billingWeight": 0,
        "dimensionalWeight": 0,
        "discountedPrice": 0,
        "netPrice": 0,
        "realWeight": 0,
        "specialSurcharges": [
          {
            "amount": 0,
            "name": "Fuel surcharge",
            "percentage": 0
          }
        ],
        "surcharges": [
          {
            "amount": 0,
            "name": "Fuel surcharge",
            "percentage": 0
          }
        ],
        "totalPrice": 0,
        "totalVat": 0
      }
    },
    "cutOffTime": "09:30:00",
    "description": "string",
    "isCinDataRequired": true,
    "isCommercialInvoiceAllowed": true,
    "isEadDocumentRequired": true,
    "isEoriRequired": true,
    "isInvoiceRequired": true,
    "isMrnRequired": true,
    "isProformaInvoiceAllowed": true,
    "messages": [
      "string"
    ],
    "serviceInfo": "string",
    "supportedExWorkType": [
      "RECEIVER_PAYS"
    ],
    "transferTime": "string"
  },
  "shipmentDetails": {
    "contentDescription": "string",
    "customReferenceCodes": [
      {
        "label": "string",
        "value": "string"
      }
    ],
    "ddp": true,
    "defaultReferenceCode": "string",
    "deliveryTailLift": true,
    "driverInfo": "string",
    "exworks": {
      "accountNumber": "string",
      "address": {
        "address": {
          "addressInfo1": "string",
          "addressInfo2": "string",
          "city": "string",
          "countryCode": "DE",
          "houseNo": "string",
          "specialField": "string",
          "stateCode": "string",
          "street": "string",
          "zip": 22529
        },
        "company": "string",
        "email": "email@email.com",
        "person": {
          "firstname": "string",
          "lastname": "string",
          "title": "MRS"
        },
        "phone": {
          "phoneNumber": "07111444555",
          "phoneNumberPrefix": "+49"
        }
      },
      "exworkType": "string"
    },
    "goodsValue": 100,
    "holidayDelivery": true,
    "insurance": true,
    "note": "string",
    "parcelList": [
      {
        "additionalHandling": true,
        "cashOnDelivery": {
          "currency": "string",
          "paymentMethod": "string",
          "value": 0
        },
        "contentDescription": "string",
        "dangerousGoods": {
          "accessibility": "ACCESSIBLE",
          "adrItemNumber": "string",
          "adrPackagingGroupLetter": "string",
          "chemicalRecordIdentifier": "string",
          "classification": "string",
          "classificationDivision": "string",
          "contact": [
            {
              "emergencyPhoneNumber": "string",
              "name": "string",
              "place": "string",
              "title": "string"
            }
          ],
          "idNumber": "string",
          "numberOfContainers": 0,
          "packagingGroup": "string",
          "packagingInstructions": "string",
          "packagingType": "string",
          "packagingTypeQuantity": "string",
          "properShippingName": "string",
          "quantity": "string",
          "recordIdent1": "string",
          "recordIdent2": "string",
          "recordIdent3": "string",
          "regulation": "string",
          "regulationLevel": "string",
          "specialServiceType": "string",
          "subRiskClass": "string",
          "transportationMode": "string",
          "unitOfMeasure": "GRAMM"
        },
        "height": 0,
        "length": 0,
        "quantity": 1,
        "tariffNumbers": [
          "string"
        ],
        "weight": 0,
        "width": 0
      }
    ],
    "pickupInterval": {
      "date": "2022-12-01",
      "timeFrom": "09:00:00",
      "timeTo": "09:30:00"
    },
    "pickupOrder": true,
    "pickupTailLift": true,
    "saturdayDelivery": true,
    "shipmentSettings": {
      "ddp": false,
      "deliveryTailLift": false,
      "holidayDelivery": false,
      "insurance": false,
      "pickupOrder": true,
      "pickupTailLift": true,
      "saturdayDelivery": false
    },
    "shipmentType": "PARCEL",
    "transportType": "STANDARD"
  },
  "shipmentNotification": {
    "addtionalCarrierNotification": {
      "email": "string",
      "mobilePhone": {
        "phoneNumber": "07111444555",
        "phoneNumberPrefix": "+49"
      }
    },
    "recipientNotification": {
      "emails": [
        "string"
      ],
      "notificationText": "string"
    },
    "trackingNotification": {
      "deliveryNotification": true,
      "emails": [
        "string"
      ],
      "notificationText": "string",
      "problemNotification": true
    }
  },
  "tddDocumentIdList": {
    "tddDocuments": [
      {
        "createdDate": "string",
        "documentId": 0,
        "documentName": "string",
        "documentType": "LABEL"
      }
    ]
  }
}
"""