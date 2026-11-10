# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt
"""Shop app integration with several warehouses and every shipping provider.

All HTTP calls are mocked; nothing reaches Starshipit, LetMeShip or SendCloud."""

import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate
from frappe.utils.password import set_encrypted_password

from erpnext_shipping.erpnext_shipping import shop_integration as si
from erpnext_shipping.erpnext_shipping.utils import CHECKOUT_TIMEOUT

REQUEST = "requests.request"
POST = "requests.post"
EXCHANGE_RATE = "erpnext.setup.utils.get_exchange_rate"
PROVIDERS = ("Starshipit", "LetMeShip", "SendCloud")


def response(payload, status=200):
	mock = MagicMock()
	mock.status_code = status
	mock.ok = 200 <= status < 300
	mock.json.return_value = payload
	mock.headers = {}
	return mock


STARSHIPIT_RATES = {
	"services": [
		{
			"carrier": "CourierPost",
			"carrier_name": "Post Domestic",
			"service_name": "Parcel Post",
			"service_code": "CPOLP",
			"total_price": 9.5,
		}
	],
	"success": True,
}
LETMESHIP_RATES = {
	"serviceList": [
		{
			"baseServiceDetails": {
				"id": 42,
				"carrier": "UPS",
				"name": "Standard",
				"priceInfo": {"realWeight": 1, "netPrice": 10.0},
			}
		}
	]
}
SENDCLOUD_RATES = {
	"data": [
		{
			"carrier": {"name": "DHL"},
			"name": "DHL Parcel",
			"code": "dhl:parcel",
			"quotes": [{"price": {"total": {"value": "7.50", "currency": "EUR"}}}],
		}
	]
}


def starshipit_or_letmeship(method, url, **kwargs):
	"""Starshipit and LetMeShip both call requests.request; answer each by its URL."""
	if "letmeship" in url:
		return response(LETMESHIP_RATES)
	return response(STARSHIPIT_RATES)


class StoreTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		if "shop" not in frappe.get_installed_apps():
			raise unittest.SkipTest("Shop is not installed")
		super().setUpClass()

	def setUp(self):
		self.company = frappe.db.get_value("Company", {}, "name")
		self.currency = frappe.get_cached_value("Company", self.company, "default_currency")
		self.country = frappe.get_cached_value("Company", self.company, "country")
		self.sender = self.address("_Test Store Sender", "1 Main Road", "1010")
		self.box = self.parcel_template()
		self.item = self.weighed_item("_Test Store Parcel Item", 0.4)
		self.pickup_user = self.user("_test_store_pickup@example.com", "+1 555 0100")
		for provider in PROVIDERS:
			frappe.db.set_single_value(
				provider,
				{"enabled": 0, "shop_checkout_rates": 0, "checkout_services": "", "rates_include_tax": 0},
			)
		frappe.db.set_single_value(
			"Starshipit",
			{
				"api_key": "test-key",
				"subscription_key": "test-subscription",
				"sender_address": self.sender,
				"pickup_contact": self.pickup_user,
				"default_parcel_template": None,
			},
		)
		frappe.db.set_single_value("LetMeShip", {"api_id": "test-id", "use_test_environment": 1})
		set_encrypted_password("LetMeShip", "LetMeShip", "test-password", "api_password")
		frappe.db.set_single_value("SendCloud", "api_key", "test-key")
		set_encrypted_password("SendCloud", "SendCloud", "test-secret", "api_secret")
		self.clear()
		sleep = patch("erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit.time.sleep")
		sleep.start()
		self.addCleanup(sleep.stop)

	def tearDown(self):
		for provider in PROVIDERS:
			frappe.db.set_single_value(provider, {"enabled": 0, "shop_checkout_rates": 0})
		self.clear()

	def clear(self):
		for provider in PROVIDERS:
			frappe.clear_cache(doctype=provider)
		frappe.cache.delete_keys(si.RATE_CACHE_PREFIX)
		frappe.local.cache.clear()  # this process's copy of what was cached

	def offer(self, *providers, **values):
		for provider in providers:
			frappe.db.set_single_value(provider, {"enabled": 1, "shop_checkout_rates": 1, **values})
		self.clear()

	def address(self, title, line1, pincode, country=None, link=None) -> str:
		"""An Address of the company (as a Shipment's pickup needs), or of the party linked."""
		name = f"{title}-Shipping"
		link_doctype, link_name = link or ("Company", self.company)
		if frappe.db.exists("Address", name):
			doc = frappe.get_doc("Address", name)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "Address",
					"address_title": title,
					"address_type": "Shipping",
					"address_line1": line1,
					"city": "Testville",
					"pincode": pincode,
					"country": country or self.country,
				}
			)
		if not any(row.link_doctype == link_doctype and row.link_name == link_name for row in doc.links):
			doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})
		doc.is_your_company_address = int(link_doctype == "Company")
		doc.save(ignore_permissions=True)
		return name

	def parcel_template(self) -> str:
		name = "_Test Store Box"
		if not frappe.db.exists("Shipment Parcel Template", name):
			frappe.get_doc(
				{
					"doctype": "Shipment Parcel Template",
					"parcel_template_name": name,
					"length": 20,
					"width": 15,
					"height": 10,
					"weight": 0.1,
				}
			).insert(ignore_permissions=True)
		return name

	def weighed_item(self, code: str, kg: float) -> str:
		if not frappe.db.exists("Item", code):
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": code,
					"item_name": code,
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"weight_per_unit": kg,
					"weight_uom": "Kg",
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Item", code, {"shipping_parcel_template": self.box})
		return code

	def user(self, email: str, phone: str) -> str:
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Pat",
					"last_name": "Picker",
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("User", email, "phone", phone)
		return email

	def context(self, items=None, **values):
		context = frappe._dict(
			cart_items=items or [{"item_code": self.item, "qty": 2, "rate": 100}],
			address=frappe._dict(
				address_line1="5 High St",
				address_line2="",
				city="Elsewhere",
				state="",
				pincode="9016",
				country=self.country,
				country_code=(frappe.db.get_value("Country", self.country, "code") or "").upper(),
			),
			subtotal=200,
			company=self.company,
			currency=self.currency,
		)
		context.update(values)
		return context


class TestCheckoutRates(StoreTestCase):
	@patch(REQUEST)
	def test_another_fulfilment_providers_location_gets_no_rates(self, request):
		self.offer("Starshipit")
		self.assertIsNone(si.checkout_rates(self.context(location_provider="amazon_mcf")))
		self.assertIsNone(si.checkout_rates(self.context(location_provider="manual")))
		request.assert_not_called()

	@patch(REQUEST)
	def test_this_apps_locations_and_old_callers_get_rates(self, request):
		request.return_value = response(STARSHIPIT_RATES)
		self.offer("Starshipit")
		for location_provider in ("erpnext_shipping", "starshipit", None):
			self.clear()
			options = si.checkout_rates(self.context(location_provider=location_provider))["options"]
			self.assertEqual([o["id"] for o in options], ["starshipit:CourierPost|CPOLP"], location_provider)

	@patch(EXCHANGE_RATE, return_value=1.5)
	@patch(POST)
	@patch(REQUEST, side_effect=starshipit_or_letmeship)
	def test_every_provider_offering_store_rates_quotes(self, request, post, _rate):
		post.return_value = response(SENDCLOUD_RATES)
		self.offer(*PROVIDERS)
		options = si.checkout_rates(self.context(currency="NZD", location_provider="erpnext_shipping"))[
			"options"
		]
		by_id = {o["id"]: o for o in options}
		self.assertEqual(
			list(by_id), ["starshipit:CourierPost|CPOLP", "sendcloud:dhl:parcel", "letmeship:42"]
		)  # cheapest first
		# LetMeShip and SendCloud quote in EUR: shown in the store's currency at ERPNext's rate
		self.assertEqual(by_id["sendcloud:dhl:parcel"]["amount"], 11.25)
		self.assertEqual(by_id["letmeship:42"]["amount"], 15.0)
		self.assertEqual(by_id["starshipit:CourierPost|CPOLP"]["amount"], 9.5)
		self.assertEqual(
			by_id["letmeship:42"]["meta"],
			{
				"provider": "LetMeShip",
				"carrier": "UPS",
				"service_id": "42",
				"service_name": "Standard",
				"carrier_code": None,
				"service_code": None,
			},
		)
		self.assertEqual(by_id["sendcloud:dhl:parcel"]["meta"]["provider"], "SendCloud")
		self.assertEqual(by_id["sendcloud:dhl:parcel"]["meta"]["service_id"], "dhl:parcel")
		letmeship = next(c for c in request.call_args_list if "letmeship" in c.args[1])
		# LetMeShip is sent the parcel's box and the pickup contact
		parcel = letmeship.kwargs["json"]["shipmentDetails"]["parcelList"][0]
		self.assertEqual((parcel["length"], parcel["width"], parcel["height"]), (20, 15, 10))
		self.assertEqual(letmeship.kwargs["json"]["pickupInfo"]["person"]["lastname"], "Picker")

	@patch(POST)
	@patch(REQUEST, side_effect=starshipit_or_letmeship)
	def test_each_provider_has_its_own_store_settings(self, request, post):
		self.offer("Starshipit")
		self.offer("SendCloud", rates_include_tax=1, checkout_services="dhl:parcel")
		frappe.db.set_single_value("LetMeShip", "enabled", 1)  # enabled, but not at checkout
		frappe.clear_cache(doctype="LetMeShip")
		post.return_value = response(SENDCLOUD_RATES)
		with patch(EXCHANGE_RATE, return_value=1):
			options = si.checkout_rates(self.context())["options"]
		self.assertEqual({o["meta"]["provider"] for o in options}, {"Starshipit", "SendCloud"})
		self.assertFalse(any("letmeship" in c.args[1] for c in request.call_args_list))
		taxed = {o["meta"]["provider"]: o["tax_included"] for o in options}
		self.assertEqual(taxed, {"Starshipit": False, "SendCloud": True})

		frappe.db.set_single_value("SendCloud", "checkout_services", "postnl:standard")
		self.clear()
		with patch(EXCHANGE_RATE, return_value=1):
			options = si.checkout_rates(self.context())["options"]
		self.assertEqual([o["meta"]["provider"] for o in options], ["Starshipit"])

	@patch(
		REQUEST,
		return_value=response(
			{
				"services": [
					*STARSHIPIT_RATES["services"],
					{
						"carrier": "PlainLabel",
						"carrier_name": "Plain Label",
						"service_name": "Plain Label - My Carrier",
						"service_code": "Plain Label",
						"total_price": 0.0,
					},
				],
				"success": True,
			}
		),
	)
	def test_a_service_priced_at_nothing_is_offered_only_when_the_store_says_it_is_free(self, request):
		self.offer("Starshipit")
		options = si.checkout_rates(self.context())["options"]
		self.assertEqual([o["service"] for o in options], ["Parcel Post"])
		self.offer("Starshipit", checkout_free_services=1)
		options = si.checkout_rates(self.context())["options"]
		self.assertEqual([o["amount"] for o in options], [0.0, 9.5])

		# what the courier's price leaves out is added to a priced service: 10% and 2.00 per parcel
		self.addCleanup(
			frappe.db.set_single_value,
			"Starshipit",
			{"checkout_rate_percent": 0, "checkout_rate_fixed": 0, "checkout_free_services": 0},
		)
		self.offer("Starshipit", checkout_rate_percent=10, checkout_rate_fixed=2)
		options = si.checkout_rates(self.context())["options"]
		self.assertEqual([o["amount"] for o in options], [0.0, 12.45])

	def test_a_couriers_breakdown_says_whether_its_price_includes_tax(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import breakdown_includes_tax

		# a courier that says its price includes tax, and one that says nothing
		self.assertTrue(breakdown_includes_tax({"Total (incl) GST": "8.86", "Service Code": "CPOLTPA4"}))
		self.assertIsNone(breakdown_includes_tax({"Service Code": "ICOU"}))
		self.assertIsNone(breakdown_includes_tax(None))
		# a courier that lists the tax: none charged means the price has none
		self.assertIs(
			breakdown_includes_tax(
				{"Calculated Price ex GST": "$14.80", "Calculated GST": "$0.00", "Calculated Price": "$14.80"}
			),
			False,
		)
		self.assertTrue(
			breakdown_includes_tax(
				{"Calculated Price ex GST": "$14.80", "Calculated GST": "$1.48", "Calculated Price": "$16.28"}
			)
		)
		# amounts in any currency's writing
		self.assertTrue(breakdown_includes_tax({"VAT": "1,00 €"}))
		self.assertTrue(breakdown_includes_tax({"Calculated VAT": "£2.00"}))
		self.assertIs(breakdown_includes_tax({"Tax": "0,00 kr"}), False)

	def test_breakdown_amounts_in_any_currency(self):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import breakdown_amount

		for text, amount in (
			("$1,234.50", 1234.5),
			("1.234,50 €", 1234.5),
			("£2.00", 2),
			("12,5", 12.5),
			("1,000", 1000),
		):
			self.assertEqual(breakdown_amount(text), amount, text)

	@patch(EXCHANGE_RATE, return_value=0.9)
	def test_rates_in_another_currency_are_converted(self, _rate):
		from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import StarshipitUtils

		service = {"carrier": "Courier", "service_name": "Express", "service_code": "X", "total_price": 20}
		# Starshipit names the currency, else the store's setting says it
		self.assertEqual(si.amount_in(StarshipitUtils.service_dict(dict(service, currency="AUD")), "NZD"), 18)
		self.assertEqual(si.amount_in(StarshipitUtils.service_dict(service, "AUD"), "NZD"), 18)
		self.assertEqual(si.amount_in(StarshipitUtils.service_dict(service), "NZD"), 20)

	def test_the_fixed_amount_is_added_for_each_parcel(self):
		self.addCleanup(
			frappe.db.set_single_value, "Starshipit", {"checkout_rate_percent": 0, "checkout_rate_fixed": 0}
		)
		self.offer("Starshipit", checkout_rate_fixed=2)
		service = dict(STARSHIPIT_RATES["services"][0], service_id="CourierPost|CPOLP")
		options = si.checkout_options("Starshipit", [service], None, parcel_count=3)
		self.assertEqual(options[0]["amount"], 15.5)

	@patch(
		REQUEST,
		return_value=response(
			{
				"services": [
					dict(STARSHIPIT_RATES["services"][0], pricing_breakdown={"Total (incl) GST": "9.50"}),
					{
						"carrier": "MyNZPostBusiness",
						"service_name": "Economy",
						"service_code": "IECON",
						"total_price": 36.79,
						"pricing_breakdown": {"Service Code": "IECON"},
					},
				],
				"success": True,
			}
		),
	)
	def test_the_courier_says_whether_a_rate_includes_tax_before_the_store_setting(self, request):
		self.offer("Starshipit", rates_include_tax=0)
		taxed = {o["service"]: o["tax_included"] for o in si.checkout_rates(self.context())["options"]}
		self.assertEqual(taxed, {"Parcel Post": True, "Economy": False})

	@patch(POST)
	@patch(REQUEST, return_value=response(STARSHIPIT_RATES))
	def test_one_provider_failing_leaves_the_others(self, request, post):
		self.offer("Starshipit", "SendCloud")
		post.return_value = response({"errors": [{"detail": "Service unavailable"}]})
		result = si.checkout_rates(self.context())
		self.assertEqual([o["id"] for o in result["options"]], ["starshipit:CourierPost|CPOLP"])
		self.assertFalse(result.get("messages"))
		si.checkout_rates(self.context())
		self.assertEqual(request.call_count, 1)  # Starshipit's answer was cached
		self.assertEqual(post.call_count, 2)  # SendCloud's failure wasn't

	@patch(POST)
	@patch(REQUEST)
	def test_a_provider_that_does_not_answer_at_checkout_is_asked_again(self, request, post):
		import requests

		def letmeship_times_out(method, url, **kwargs):
			if "letmeship" in url:
				raise requests.Timeout()
			return response(STARSHIPIT_RATES)

		request.side_effect = letmeship_times_out
		post.side_effect = requests.Timeout()
		self.offer(*PROVIDERS)
		result = si.checkout_rates(self.context(location_provider="erpnext_shipping"))
		self.assertEqual([o["id"] for o in result["options"]], ["starshipit:CourierPost|CPOLP"])
		# each call is cut short for the shopper, and no failure is kept as "no services"
		letmeship = next(c for c in request.call_args_list if "letmeship" in c.args[1])
		self.assertEqual(letmeship.kwargs["timeout"], CHECKOUT_TIMEOUT)
		self.assertEqual(post.call_args.kwargs["timeout"], CHECKOUT_TIMEOUT)
		si.checkout_rates(self.context(location_provider="erpnext_shipping"))
		self.assertEqual(post.call_count, 2)
		self.assertEqual(sum("letmeship" in c.args[1] for c in request.call_args_list), 2)

	@patch(POST, return_value=response({"data": []}))
	def test_desk_rates_keep_their_alerts_and_no_timeout(self, post):
		from erpnext_shipping.erpnext_shipping.doctype.sendcloud.sendcloud import SendCloudUtils

		self.offer("SendCloud")
		with self.assertRaises(frappe.ValidationError):
			SendCloudUtils().get_available_services(
				frappe._dict(country_code="nz", pincode="9016", city="Dunedin", address_line1="1 St"),
				frappe._dict(country_code="nz", pincode="1010", city="Auckland", address_line1="2 St"),
				[{"weight": 1}],
			)
		self.assertIsNone(post.call_args.kwargs["timeout"])

	def test_pickups_are_on_the_next_working_day_of_the_companys_holiday_list(self):
		saturday = "2026-11-14"
		with patch("frappe.get_cached_value", return_value=None):
			self.assertEqual(si.next_business_day("2026-11-13", self.company), "2026-11-16")
		holidays = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": "_Test Store Holidays",
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
				"holidays": [{"holiday_date": "2026-11-16", "description": "Closed"}],
			}
		)
		if not frappe.db.exists("Holiday List", holidays.holiday_list_name):
			holidays.insert(ignore_permissions=True)
		with patch("frappe.get_cached_value", return_value=holidays.holiday_list_name):
			# the list's days off, not the weekend, decide
			self.assertEqual(si.next_business_day("2026-11-13", self.company), saturday)
			self.assertEqual(si.next_business_day("2026-11-15", self.company), "2026-11-17")

	@patch(REQUEST, return_value=response({"success": False, "errors": [{"message": "Down"}]}, 503))
	def test_no_rates_at_all_says_so(self, request):
		self.offer("Starshipit")
		result = si.checkout_rates(self.context())
		self.assertEqual(result["options"], [])
		self.assertTrue(result["messages"])

	@patch(REQUEST, return_value=response(STARSHIPIT_RATES))
	def test_the_parcels_ship_from_address_is_the_origin(self, request):
		self.offer("Starshipit")
		north = self.address("_Test Store North", "2 North Road", "0610")
		si.checkout_rates(self.context(ship_from=north, warehouse="_Test Store North"))
		self.assertEqual(request.call_args.kwargs["json"]["sender"]["post_code"], "0610")
		si.checkout_rates(self.context())
		self.assertEqual(request.call_args.kwargs["json"]["sender"]["post_code"], "1010")  # the default
		self.assertEqual(request.call_count, 2)  # each origin is cached on its own
		si.checkout_rates(self.context(ship_from=north))
		self.assertEqual(request.call_count, 2)

	def test_no_address_to_send_from_offers_nothing(self):
		self.offer("Starshipit")
		frappe.db.set_single_value("Starshipit", "sender_address", None)
		self.clear()
		with (
			patch("frappe.contacts.doctype.address.address.get_default_address", return_value=None),
			patch("erpnext.setup.doctype.company.company.get_default_company_address", return_value=None),
		):
			self.assertIsNone(si.checkout_rates(self.context()))

	def test_a_store_without_starshipit_sends_from_the_warehouse_or_company(self):
		frappe.db.set_single_value("Starshipit", "enabled", 0)
		self.clear()
		with (
			patch("frappe.contacts.doctype.address.address.get_default_address", return_value="WH-ADDR"),
			patch(
				"erpnext.setup.doctype.company.company.get_default_company_address", return_value="CO-ADDR"
			),
		):
			self.assertEqual(si.origin_name(None, "_Test Store North - X"), "WH-ADDR")
			self.assertEqual(si.origin_name(), "CO-ADDR")
			self.assertEqual(si.ShippingFulfillment().default_ship_from(), "CO-ADDR")
			# a parcel sent with Starshipit keeps Starshipit's Ship From Address
			self.assertEqual(si.StarshipitFulfillment().default_ship_from(), self.sender)
			self.assertEqual(si.origin_name("LOCATION-ADDR"), "LOCATION-ADDR")


class TestStoreFulfillment(StoreTestCase):
	def setUp(self):
		super().setUp()
		self.north = self.warehouse("_Test Store North")
		self.south = self.warehouse("_Test Store South")
		self.north_address = self.address("_Test Store North", "2 North Road", "0610")
		self.south_address = self.address("_Test Store South", "4 South Road", "9010")
		self.other_item = self.weighed_item("_Test Store Parcel Item 2", 1.0)
		self.north_user = self.user("_test_store_north@example.com", "+1 555 0101")
		frappe.db.set_value("Warehouse", self.north, "shipping_pickup_contact", self.north_user)
		self.receive(self.item, self.north)
		self.receive(self.other_item, self.south)
		self.customer = self.make_customer()

	def warehouse(self, name: str) -> str:
		existing = frappe.db.get_value("Warehouse", {"warehouse_name": name, "company": self.company})
		if existing:
			return existing
		return (
			frappe.get_doc({"doctype": "Warehouse", "warehouse_name": name, "company": self.company})
			.insert(ignore_permissions=True)
			.name
		)

	def receive(self, item: str, warehouse: str, qty: float = 10) -> None:
		entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.company,
				"items": [{"item_code": item, "qty": qty, "t_warehouse": warehouse, "basic_rate": 10}],
			}
		)
		entry.insert(ignore_permissions=True)
		entry.submit()

	def make_customer(self) -> str:
		name = "_Test Store Shopper"
		if not frappe.db.exists("Customer", name):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0})
					or "All Customer Groups",
					"territory": frappe.db.get_value("Territory", {"is_group": 0}) or "All Territories",
				}
			).insert(ignore_permissions=True)
		self.shopper_address = self.address(
			"_Test Store Shopper", "5 High St", "9016", link=("Customer", name)
		)
		return name

	def order(self, *lines):
		order = frappe.get_doc(
			{
				"doctype": "Sales Order",
				"customer": self.customer,
				"company": self.company,
				"transaction_date": nowdate(),
				"delivery_date": add_days(nowdate(), 3),
				"currency": self.currency,
				"conversion_rate": 1,
				"shipping_address_name": self.shopper_address,
				"items": [
					{"item_code": item, "qty": qty, "rate": 100, "warehouse": warehouse}
					for item, qty, warehouse in lines
				],
			}
		)
		order.insert(ignore_permissions=True)
		order.submit()
		return order

	def option(self, provider="SendCloud", service_id="dhl:parcel"):
		return json.dumps(
			{
				"id": f"{frappe.scrub(provider)}:{service_id}",
				"label": "DHL Parcel",
				"amount": 11.25,
				"meta": {
					"provider": provider,
					"carrier": "DHL",
					"service_id": service_id,
					"service_name": "DHL Parcel",
				},
			}
		)

	def parcel(self, warehouse, item, qty, ship_from=None, option=None):
		items = [{"item_code": item, "item_name": item, "qty": qty, "warehouse": warehouse}]
		fulfillment = frappe._dict(
			warehouse=warehouse, ship_from_address=ship_from, shipping_option=option, external_id=None
		)
		return items, fulfillment

	def test_both_provider_keys_are_registered(self):
		hooks = frappe.get_hooks("shop_fulfillment_providers")
		self.assertIn("erpnext_shipping", hooks)
		self.assertIn("starshipit", hooks)  # for Shop Fulfillments made before
		self.assertFalse(si.ShippingFulfillment().is_configured())
		frappe.db.set_single_value("SendCloud", "enabled", 1)
		self.assertTrue(si.ShippingFulfillment().is_configured())
		self.assertFalse(si.StarshipitFulfillment().is_configured())  # Starshipit itself is off

	def test_each_parcel_is_delivered_and_shipped_from_its_own_warehouse(self):
		order = self.order((self.item, 2, self.north), (self.other_item, 1, self.south))
		provider = si.ShippingFulfillment()

		items, fulfillment = self.parcel(self.north, self.item, 2, self.north_address, self.option())
		north = provider.create_shipment(order, items, fulfillment)["external_id"]
		items, fulfillment = self.parcel(self.south, self.other_item, 1, self.south_address)
		south = provider.create_shipment(order, items, fulfillment)["external_id"]

		self.assertNotEqual(north, south)
		for shipment, warehouse, address, item, qty, user in (
			(north, self.north, self.north_address, self.item, 2, self.north_user),
			(south, self.south, self.south_address, self.other_item, 1, self.pickup_user),
		):
			doc = frappe.get_doc("Shipment", shipment)
			self.assertEqual(doc.docstatus, 0)  # booking stays with staff
			self.assertEqual(doc.pickup_address_name, address)
			self.assertEqual(doc.pickup_contact_person, user)  # the warehouse's, else Starshipit's
			note = frappe.get_doc("Delivery Note", doc.shipment_delivery_note[0].delivery_note)
			self.assertEqual(
				[(r.item_code, r.qty, r.warehouse) for r in note.items], [(item, qty, warehouse)]
			)
		self.assertEqual(frappe.db.get_value("Sales Order", order.name, "per_delivered"), 100)

		choice = json.loads(frappe.db.get_value("Shipment", north, "preferred_service"))
		self.assertEqual((choice["provider"], choice["service_id"]), ("SendCloud", "dhl:parcel"))
		services = [
			frappe._dict(service_provider="SendCloud", service_id="dhl:parcel", service_name="DHL Parcel"),
			frappe._dict(service_provider="Starshipit", service_id="dhl:parcel", service_name="DHL Parcel"),
		]
		self.assertEqual([s.get("is_preferred") for s in si.mark_preferred(services, north)], [1, None])

		# the same parcel sent again gets its Shipment back
		items, fulfillment = self.parcel(self.north, self.item, 2, self.north_address)
		fulfillment.external_id = north
		self.assertEqual(provider.create_shipment(order, items, fulfillment)["external_id"], north)

		# cancelling one parcel puts back only its stock
		provider.cancel(frappe._dict(external_id=north))
		self.assertFalse(frappe.db.exists("Shipment", north))
		delivered = dict(
			frappe.get_all(
				"Sales Order Item",
				filters={"parent": order.name},
				fields=["item_code", "delivered_qty"],
				as_list=True,
			)
		)
		self.assertEqual(delivered, {self.item: 0, self.other_item: 1})
		self.assertEqual(
			provider.fetch_status(frappe._dict(external_id=south, status="Accepted"))["status"], "Accepted"
		)

	def test_a_parcel_takes_only_what_is_left_to_deliver(self):
		order = self.order((self.item, 1, self.north))
		items, fulfillment = self.parcel(self.north, self.item, 3)
		with self.assertRaises(frappe.ValidationError):
			si.ShippingFulfillment().create_shipment(order, items, fulfillment)

	def test_an_order_sent_whole_is_delivered_whole(self):
		# Shop before fulfilment locations: no warehouse, no fulfilment record for the parcel
		order = self.order((self.item, 2, self.north), (self.other_item, 1, self.south))
		items = [
			{"item_code": self.item, "item_name": self.item, "qty": 2},
			{"item_code": self.other_item, "item_name": self.other_item, "qty": 1},
		]
		shipment = si.StarshipitFulfillment().create_shipment(order, items)["external_id"]
		doc = frappe.get_doc("Shipment", shipment)
		self.assertEqual(doc.pickup_address_name, self.sender)
		note = frappe.get_doc("Delivery Note", doc.shipment_delivery_note[0].delivery_note)
		self.assertEqual(len(note.items), 2)
		# asked again, the order's Shipment is found rather than made twice
		self.assertEqual(si.StarshipitFulfillment().create_shipment(order, items)["external_id"], shipment)

	def test_the_pending_fulfilment_is_found_when_shop_passes_only_the_items(self):
		if not frappe.get_meta("Shop Fulfillment").has_field("warehouse"):
			self.skipTest("Shop has no fulfilment locations")
		order = self.order((self.item, 2, self.north), (self.other_item, 1, self.south))
		records = {}
		for warehouse, item, qty, address in (
			(self.north, self.item, 2, self.north_address),
			(self.south, self.other_item, 1, self.south_address),
		):
			records[warehouse] = frappe.get_doc(
				{
					"doctype": "Shop Fulfillment",
					"sales_order": order.name,
					"provider": "erpnext_shipping",
					"status": "Pending",
					"warehouse": warehouse,
					"ship_from_address": address,
					"shipping_option": self.option() if warehouse == self.south else None,
					"items": [{"item_code": item, "item_name": item, "qty": qty}],
				}
			).insert(ignore_permissions=True)
		items = [
			{"item_code": self.other_item, "item_name": self.other_item, "qty": 1, "warehouse": self.south}
		]
		found = si.pending_fulfillment(order.name, "erpnext_shipping", items)
		self.assertEqual(found.name, records[self.south].name)
		shipment = si.ShippingFulfillment().create_shipment(order, items)["external_id"]
		self.assertEqual(frappe.db.get_value("Shipment", shipment, "pickup_address_name"), self.south_address)

		records[self.south].db_set("external_id", shipment)
		records[self.north].db_set("external_id", "SHIP-ELSEWHERE")
		with patch("shop.fulfillment.service.sync") as sync:
			si.sync_store_fulfillment(shipment)
		sync.assert_called_once_with(records[self.south].name)

	def booked(self, order, qty=2):
		"""A parcel's Shipment, submitted and booked with Starshipit but without a label."""
		items, fulfillment = self.parcel(self.north, self.item, qty, self.north_address, self.option())
		name = si.StarshipitFulfillment().create_shipment(order, items, fulfillment)["external_id"]
		doc = frappe.get_doc("Shipment", name)
		doc.flags.ignore_permissions = True
		doc.submit()
		doc.db_set({"service_provider": "Starshipit", "shipment_id": "321"})
		return doc

	def in_stock(self) -> float:
		return (
			frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": self.north}, "actual_qty") or 0
		)

	@patch(REQUEST)
	def test_a_booked_parcel_is_cancelled_with_its_starshipit_order(self, request):
		frappe.db.set_single_value("Starshipit", "enabled", 1)
		order = self.order((self.item, 2, self.north))
		stock = self.in_stock()
		shipment = self.booked(order)
		note = shipment.shipment_delivery_note[0].delivery_note
		self.assertEqual(self.in_stock(), stock - 2)
		request.return_value = MagicMock(
			status_code=200,
			ok=True,
			json=MagicMock(return_value={"order": {"order_id": 321, "status": "Unshipped"}, "success": True}),
		)
		si.StarshipitFulfillment().cancel(frappe._dict(external_id=shipment.name))
		self.assertEqual(frappe.db.get_value("Shipment", shipment.name, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Delivery Note", note, "docstatus"), 2)
		self.assertEqual(self.in_stock(), stock)
		# Starshipit is asked about the order now, and it is removed once the cancel is saved
		self.assertEqual(request.call_args.args[:2], ("GET", "https://api.starshipit.com/api/orders"))
		self.assertTrue(frappe.db.after_commit._functions)
		frappe.db.after_commit.reset()

	def test_an_invoiced_parcel_is_returned_and_its_invoice_kept(self):
		from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_invoice

		order = self.order((self.item, 2, self.north))
		stock = self.in_stock()
		items, fulfillment = self.parcel(self.north, self.item, 2, self.north_address)
		shipment = si.ShippingFulfillment().create_shipment(order, items, fulfillment)["external_id"]
		note = frappe.db.get_value("Shipment Delivery Note", {"parent": shipment}, "delivery_note")
		invoice = make_sales_invoice(note)
		invoice.insert(ignore_permissions=True)
		invoice.submit()

		si.ShippingFulfillment().cancel(frappe._dict(external_id=shipment))
		self.assertFalse(frappe.db.exists("Shipment", shipment))
		self.assertEqual(frappe.db.get_value("Delivery Note", note, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Sales Invoice", invoice.name, "docstatus"), 1)
		returns = frappe.get_all(
			"Delivery Note",
			filters={"is_return": 1, "return_against": note, "docstatus": 1},
			fields=["name"],
		)
		self.assertEqual(len(returns), 1)
		self.assertEqual(frappe.db.get_value("Delivery Note Item", {"parent": returns[0].name}, "qty"), -2)
		self.assertEqual(self.in_stock(), stock)

	def test_a_parcel_with_a_label_or_with_the_courier_is_not_cancelled_from_the_store(self):
		order = self.order((self.item, 1, self.north))
		shipment = self.booked(order, qty=1)
		note = shipment.shipment_delivery_note[0].delivery_note
		for values in ({"awb_number": "TRK1"}, {"awb_number": "", "tracking_status": "In Progress"}):
			with self.subTest(**values):
				shipment.db_set(values)
				with self.assertRaises(frappe.ValidationError):
					si.StarshipitFulfillment().cancel(frappe._dict(external_id=shipment.name))
				self.assertEqual(frappe.db.get_value("Shipment", shipment.name, "docstatus"), 1)
				self.assertEqual(frappe.db.get_value("Delivery Note", note, "docstatus"), 1)

	@patch(REQUEST)
	def test_staff_book_a_parcel_and_print_its_label_from_the_store(self, request):
		import base64
		from io import BytesIO

		from pypdf import PdfWriter

		frappe.db.set_single_value("Starshipit", {"enabled": 1, "allow_label_printing": 1})
		pdf = PdfWriter()
		pdf.add_blank_page(288, 432)
		content = BytesIO()
		pdf.write(content)
		label = base64.b64encode(content.getvalue()).decode()
		printed = []

		def starshipit(method, url, **kwargs):
			path = url.rsplit("/api", 1)[1]
			if path == "/deliveryservices":
				return response(STARSHIPIT_RATES)
			if path == "/orders/search":
				return response({"orders": [], "success": True})
			if (method, path) == ("POST", "/orders"):
				return response({"order": {"order_id": 555}, "success": True})
			if (method, path) == ("POST", "/orders/shipment"):
				printed.append(kwargs["json"])
				return response({"labels": [label], "tracking_numbers": ["TRK9"], "success": True})
			if (method, path) == ("GET", "/orders"):
				status = "Printed" if printed else "Saved"
				return response({"order": {"order_id": 555, "status": status}, "success": True})
			return response({"success": True})

		request.side_effect = starshipit
		order = self.order((self.item, 1, self.north))
		items, fulfillment = self.parcel(self.north, self.item, 1, self.north_address, self.option())
		provider = si.StarshipitFulfillment()
		fulfillment.external_id = provider.create_shipment(order, items, fulfillment)["external_id"]
		courier = provider.courier(fulfillment)
		self.assertEqual((courier["booked"], courier["can_book"], courier["labels"]), (False, True, 0))
		with self.assertRaises(frappe.ValidationError):
			provider.print_label(fulfillment)

		services = provider.courier_services(fulfillment)
		self.assertEqual([(s["service"], s["amount"]) for s in services], [("Parcel Post", 9.5)])
		with self.assertRaises(frappe.ValidationError):
			provider.book_courier(fulfillment, services[0]["id"])  # no one to deliver to yet
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Store",
				"last_name": "Shopper",
				"email_ids": [{"email_id": "store.shopper@example.com", "is_primary": 1}],
				"phone_nos": [{"phone": "+1 555 0102", "is_primary_phone": 1}],
				"links": [{"link_doctype": "Customer", "link_name": self.customer}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Shipment", fulfillment.external_id, "delivery_contact_name", contact.name)
		with self.assertRaises(frappe.ValidationError):
			provider.book_courier(fulfillment, "Starshipit|Someone|else|entirely")
		self.assertEqual(frappe.db.get_value("Shipment", fulfillment.external_id, "docstatus"), 0)

		result = provider.book_courier(fulfillment, services[0]["id"])
		shipment = frappe.get_doc("Shipment", fulfillment.external_id)
		self.assertEqual((shipment.docstatus, shipment.shipment_id, shipment.status), (1, "555", "Booked"))
		self.assertEqual(result["status"], "Accepted")
		courier = provider.courier(fulfillment)
		self.assertEqual(
			(courier["booked"], courier["can_book"], courier["service"]), (True, False, "Parcel Post")
		)
		with self.assertRaises(frappe.ValidationError):
			provider.book_courier(fulfillment, services[0]["id"])

		label_result = provider.print_label(fulfillment)
		self.assertEqual(len(label_result["files"]), 1)
		self.assertEqual(label_result["tracking_number"], "TRK9")
		self.assertEqual(provider.labels(fulfillment), label_result["files"])
		# opened again, the label is not bought again
		self.assertEqual(provider.print_label(fulfillment)["files"], label_result["files"])
		self.assertEqual(len(printed), 1)
