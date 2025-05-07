import frappe
from frappe import _
from frappe.model.document import Document
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict, List, Optional, Union, Any

ONEWORLD_PROVIDER = "One World Express"
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 0.5
TIMEOUT = 30

class OneWorldExpress(Document):
    """One World Express Settings DocType"""
    pass

@frappe.whitelist()
def get_one_world_utils():
    """Get One World Express utils instance"""
    if not frappe.db.get_single_value("One World Express", "enabled"):
        frappe.throw(_("One World Express is not enabled"))

    return OneWorldExpressUtils()

class OneWorldExpressError(Exception):
    """Custom exception for One World Express errors"""
    pass

class OneWorldExpressUtils:
    """One World Express Integration Utils"""
    
    def __init__(self):
        self.settings = frappe.get_single("One World Express")
        self.username = self.settings.username
        self.password = self.settings.password
        self.tracking_url = self.settings.tracking_url
        self.base_url = "https://www.oneworldexpress.com"
        self.login_url = f"{self.base_url}/login"
        self.track_url = f"{self.base_url}/track"
        self.shipped_url = f"{self.base_url}/track/view-shipped"
        
        # Configure session with retry mechanism
        self.session = self._configure_session()
        
    def _configure_session(self) -> requests.Session:
        """Configure session with retry mechanism and timeouts"""
        session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST"]
        )
        
        # Mount the adapter with retry strategy
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set default headers
        session.headers.update({
            "User-Agent": "ERPNext Shipping Integration/1.0",
            "Accept": "application/json",
            "Content-Type": "application/json"
        })
        
        return session

    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with error handling and logging"""
        try:
            response = self.session.request(
                method=method,
                url=url,
                timeout=TIMEOUT,
                **kwargs
            )
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException as e:
            error_msg = f"One World Express API request failed: {str(e)}"
            frappe.log_error(error_msg, "One World Express API Error")
            raise OneWorldExpressError(error_msg)

    def _login(self) -> bool:
        """Login to One World Express"""
        try:
            # Get the login page first to get any necessary cookies/tokens
            self._make_request("GET", self.login_url)
            
            # Prepare login data
            login_data = {
                "username": self.username,
                "password": self.password,
                "remember": "true"
            }
            
            # Perform login
            response = self._make_request(
                "POST",
                f"{self.base_url}/api/auth/login",
                json=login_data
            )
            
            # Store authentication token if provided
            if "token" in response.json():
                self.session.headers.update({
                    "Authorization": f"Bearer {response.json()['token']}"
                })
            
            return True
            
        except OneWorldExpressError:
            return False
        except Exception as e:
            error_msg = f"One World Express login failed: {str(e)}"
            frappe.log_error(error_msg, "One World Express Login Error")
            return False

    def get_available_services(
        self, 
        delivery_address: Dict[str, Any], 
        pickup_address: Dict[str, Any], 
        parcels: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Get available shipping services from One World Express"""
        if not self._login():
            return []

        try:
            # Navigate to the shipped items page
            response = self._make_request("GET", self.shipped_url)
            
            # Parse the page to get available services
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Extract services from the page
            services = []
            service_elements = soup.select('.service-option')  # Update selector based on actual page
            
            for service in service_elements:
                try:
                    services.append({
                        "service_provider": ONEWORLD_PROVIDER,
                        "carrier": "One World Express",
                        "service_name": service.get('data-service-name', 'Standard Delivery'),
                        "total_price": float(service.get('data-price', 0.0)),
                        "currency": "GBP"
                    })
                except (ValueError, TypeError) as e:
                    frappe.log_error(
                        f"Error parsing service data: {str(e)}", 
                        "One World Express Service Parsing Error"
                    )
                    continue
            
            return services if services else [{
                "service_provider": ONEWORLD_PROVIDER,
                "carrier": "One World Express",
                "service_name": "Standard Delivery",
                "total_price": 0.0,
                "currency": "GBP"
            }]
            
        except Exception as e:
            error_msg = f"Failed to get One World Express services: {str(e)}"
            frappe.log_error(error_msg, "One World Express Services Error")
            return []

    def create_shipment(
        self,
        shipment: str,
        delivery_address: Dict[str, Any],
        pickup_address: Dict[str, Any],
        pickup_contact: Dict[str, Any],
        shipment_parcel: Dict[str, Any],
        delivery_contact: Dict[str, Any],
        service_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Create shipment in One World Express"""
        if not self._login():
            return None

        try:
            # Prepare shipment data
            shipment_data = {
                "pickup": {
                    "name": f"{pickup_contact.get('first_name', '')} {pickup_contact.get('last_name', '')}".strip(),
                    "address": pickup_address.get("address_line1", ""),
                    "city": pickup_address.get("city", ""),
                    "postal_code": pickup_address.get("pincode", ""),
                    "country": pickup_address.get("country", ""),
                    "phone": pickup_contact.get("phone", ""),
                    "email": pickup_contact.get("email_id", "")
                },
                "delivery": {
                    "name": f"{delivery_contact.get('first_name', '')} {delivery_contact.get('last_name', '')}".strip(),
                    "address": delivery_address.get("address_line1", ""),
                    "city": delivery_address.get("city", ""),
                    "postal_code": delivery_address.get("pincode", ""),
                    "country": delivery_address.get("country", ""),
                    "phone": delivery_contact.get("phone", ""),
                    "email": delivery_contact.get("email_id", "")
                },
                "parcel": {
                    "weight": float(shipment_parcel.get("weight", 0)),
                    "length": float(shipment_parcel.get("length", 0)),
                    "width": float(shipment_parcel.get("width", 0)),
                    "height": float(shipment_parcel.get("height", 0))
                },
                "service": service_info.get("service_name", "Standard Delivery")
            }

            # Create shipment
            response = self._make_request(
                "POST",
                f"{self.base_url}/api/shipments",
                json=shipment_data
            )
            
            shipment_response = response.json()
            
            return {
                "service_provider": ONEWORLD_PROVIDER,
                "carrier": "One World Express",
                "carrier_service": service_info.get("service_name"),
                "shipment_id": shipment_response.get("id", ""),
                "shipment_amount": float(shipment_response.get("price", 0.0)),
                "awb_number": shipment_response.get("awb", ""),
                "tracking_url": f"{self.tracking_url}?awb={shipment_response.get('awb', '')}"
            }

        except Exception as e:
            error_msg = f"Failed to create One World Express shipment: {str(e)}"
            frappe.log_error(error_msg, "One World Express Shipment Error")
            return None

    def get_tracking_data(self, shipment_id: str) -> Optional[Dict[str, Any]]:
        """Get tracking data from One World Express"""
        if not self._login():
            return None

        try:
            # Get tracking data
            response = self._make_request(
                "GET",
                f"{self.base_url}/api/tracking/{shipment_id}"
            )
            
            tracking_data = response.json()
            
            return {
                "awb_number": tracking_data.get("awb", ""),
                "tracking_status": tracking_data.get("status", "Pending"),
                "tracking_status_info": tracking_data.get("status_description", "Shipment created"),
                "tracking_url": f"{self.tracking_url}?awb={tracking_data.get('awb', '')}"
            }

        except Exception as e:
            error_msg = f"Failed to get One World Express tracking data: {str(e)}"
            frappe.log_error(error_msg, "One World Express Tracking Error")
            return None

    def get_label(self, shipment_id: str) -> Optional[bytes]:
        """Get shipping label from One World Express"""
        if not self._login():
            return None

        try:
            # Get label PDF
            response = self._make_request(
                "GET",
                f"{self.base_url}/api/shipments/{shipment_id}/label",
                headers={"Accept": "application/pdf"}
            )
            
            return response.content

        except Exception as e:
            error_msg = f"Failed to get One World Express label: {str(e)}"
            frappe.log_error(error_msg, "One World Express Label Error")
            return None 