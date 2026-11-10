"""Shipping providers for the Shop app: live rates at checkout, and store parcels handed to Shipments.

Both are registered in hooks.py and only run when the Shop app calls them:

- shop_shipping_rate_providers: rates for one parcel of the shopper's cart (what one warehouse
  sends) to their address, from every enabled provider set to offer "Rates at Store Checkout".
  Boxes come from the items' weights and Parcel Templates, packed the same way as a Shipment's
  parcels (erpnext_shipping.utils.parcels_for_items). Answers are cached for 15 minutes per
  provider, since checkout asks again as the shopper types; failures aren't.
- shop_fulfillment_providers["erpnext_shipping"]: when a paid order's parcel is sent for
  fulfilment, its items are delivered from its warehouse (Delivery Note) and a draft Shipment is
  made from its Ship From Address, with the service the customer chose shown first in Fetch
  Shipping Rates. You check the parcels, submit, and book it with the provider as usual, or book
  it and print its label from the Shop admin's order page.
  "starshipit" does the same with Starshipit rates only, for stores and records set up with it.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, flt, getdate, nowdate

from erpnext_shipping.erpnext_shipping.doctype.starshipit.starshipit import (
	STARSHIPIT_PROVIDER,
	kg_per_unit,
)
from erpnext_shipping.erpnext_shipping.utils import get_address, parcels_for_items

RATE_CACHE_SECONDS = 15 * 60
RATE_CACHE_PREFIX = "shipping_rates:"
# statuses before the courier has the parcel: a printed label is not shipped yet
NOT_YET_SHIPPED = {"", "unshipped", "saved", "new", "printed"}
SENT_STATUSES = ("In Progress", "Delivered", "Returned", "Lost")


# --- checkout rates ---------------------------------------------------------------


def settings():
	"""Starshipit's settings, which also hold the store's defaults for Shipments of store orders."""
	return frappe.get_cached_doc("Starshipit")


def shipping_providers() -> list[str]:
	from erpnext_shipping.erpnext_shipping.shipping import rate_providers

	return list(rate_providers())


def offers_checkout_rates(provider: str) -> bool:
	s = frappe.get_cached_doc(provider)
	return bool(s.enabled and s.get("shop_checkout_rates"))


def checkout_providers(location_provider: str | None = None) -> list[str]:
	"""Providers quoting at checkout for a store location sent by this Shop fulfilment provider.

	A location sent by another fulfilment provider gets no rates from here; no location (a Shop
	from before fulfilment locations) gets them from every provider."""
	if location_provider:
		fulfillment = FULFILLMENT_PROVIDERS.get(location_provider)
		if not fulfillment:
			return []
		names = list(fulfillment.shipping_providers or shipping_providers())
	else:
		names = shipping_providers()
	return [name for name in names if offers_checkout_rates(name)]


def checkout_rates(context) -> dict | None:
	"""Services and prices for one parcel of a cart going to an address (Shop rate provider).

	None when no provider offers store rates for the parcel's location, so Shop knows they are
	not in use. Prices a provider gives without a currency are taken to be in the store's."""
	providers = checkout_providers(context.get("location_provider"))
	if not providers:
		return None
	lines = cart_lines(context.cart_items)
	if not lines:
		return None  # nothing in the parcel is sent
	try:
		origin = origin_address(
			context.get("ship_from"), context.get("warehouse"), context.get("location_provider")
		)
	except frappe.ValidationError:
		frappe.clear_messages()
		frappe.log_error(
			title="Store shipping: Ship From Address is incomplete", message=cstr(context.get("ship_from"))
		)
		return cannot_quote()
	if not origin:
		return None  # nowhere to send from
	address = context.address
	try:
		parcels = parcels_for_items(lines, None, settings().get("default_parcel_template"))
	except frappe.ValidationError:
		frappe.clear_messages()
		parcels = []
	if not parcels:
		frappe.log_error(title="Store shipping: items have no weight", message=json.dumps(context.cart_items))
		return cannot_quote()
	# rates depend on the addresses and parcels, not on who receives them
	contact = user_contact(pickup_contact_user(context.get("warehouse")))
	request = frappe._dict(
		pickup_from_type="Company",
		delivery_to_type="Customer",
		pickup_address=origin,
		delivery_address=destination_address(address),
		parcels=parcels,
		value_of_goods=flt(context.subtotal),
		description_of_content=content_description(),
		pickup_date=next_business_day(
			company=context.get("warehouse")
			and frappe.db.get_value("Warehouse", context.warehouse, "company")
		),
		pickup_contact=contact,
		delivery_contact=contact,
		checkout=True,
	)
	options, failed = [], False
	for provider in providers:
		services = provider_rates(provider, request)
		if services is None:
			failed = True
			continue
		options += checkout_options(
			provider, services, context.get("currency"), sum(cint(p.get("count")) or 1 for p in parcels)
		)
	if failed and not options:
		return {
			"options": [],
			"messages": [_("Live shipping prices aren't available right now. Please try again in a moment.")],
		}
	return {"options": sorted(options, key=lambda option: option["amount"])}


def cannot_quote() -> dict:
	return {"options": [], "messages": [_("We can't work out shipping for these items. Please contact us.")]}


def provider_rates(provider: str, request: frappe._dict) -> list[dict] | None:
	"""A provider's services for the request, from the cache when it was asked recently; None when
	it failed, so the next quote asks again."""
	from erpnext_shipping.erpnext_shipping.shipping import get_shipping_rates

	key = cache_key(provider, request.pickup_address, request.delivery_address, request.parcels)
	services = frappe.cache.get_value(key)
	if services is not None:
		return services
	try:
		services = [dict(service) for service in get_shipping_rates(request, [provider])]
	except frappe.ValidationError:
		services = None  # e.g. a postcode the provider doesn't know, while the shopper types
	except Exception:
		frappe.log_error(title=f"Store shipping: {provider} rates failed")
		services = None
	finally:
		frappe.clear_messages()
	if services is not None:
		frappe.cache.set_value(key, services, expires_in_sec=RATE_CACHE_SECONDS)
	return services


def checkout_options(
	provider: str, services: list[dict], currency: str | None, parcel_count: int = 1
) -> list[dict]:
	s = frappe.get_cached_doc(provider)
	allowed = allowed_services(s)
	options = []
	for service in services:
		if allowed and not allowed & service_codes(service):
			continue
		amount = amount_in(service, currency)
		if amount is None:
			continue
		if amount <= 0 and not s.get("checkout_free_services"):
			# a provider says 0 when it has no price, e.g. a plain label carrier whose custom rates
			# don't cover the parcel
			continue
		if amount > 0:
			# what the courier's price leaves out and is invoiced later (fuel, rural delivery, fees)
			amount = flt(
				amount * (1 + flt(s.get("checkout_rate_percent")) / 100)
				+ flt(s.get("checkout_rate_fixed")) * parcel_count,
				2,
			)
		service_id = cstr(service.get("service_id") or service.get("id"))
		options.append(
			{
				"id": f"{frappe.scrub(provider)}:{service_id}",
				"label": service_label(service),
				"amount": amount,
				# the courier says whether its price includes tax; else the store's setting
				"tax_included": service["tax_included"]
				if service.get("tax_included") is not None
				else bool(s.get("rates_include_tax")),
				"carrier": service.get("carrier"),
				"service": service.get("service_name"),
				"meta": {
					"provider": provider,
					"carrier": service.get("carrier"),
					"service_id": service_id,
					"service_name": service.get("service_name"),
					"carrier_code": service.get("carrier_code"),
					"service_code": service.get("service_code"),
				},
			}
		)
	return options


def amount_in(service: dict, currency: str | None) -> float | None:
	"""The service's price in the store's currency, at ERPNext's exchange rate when the provider
	quotes in another; None when there is no rate."""
	amount = flt(service.get("total_price"))
	quoted_in = service.get("currency")
	if currency and quoted_in and quoted_in != currency:
		from erpnext.setup.utils import get_exchange_rate

		rate = flt(get_exchange_rate(quoted_in, currency))
		frappe.clear_messages()
		if not rate:
			return None
		amount *= rate
	return flt(amount, 2)


def service_label(service: dict) -> str:
	carrier, name = service.get("carrier") or "", service.get("service_name") or ""
	if carrier and carrier.lower() not in name.lower():
		return f"{carrier} {name}".strip()
	return name or carrier


def allowed_services(provider_settings=None) -> set[str]:
	s = provider_settings or settings()
	return {line.strip() for line in (s.get("checkout_services") or "").splitlines() if line.strip()}


def service_codes(service: dict) -> set[str]:
	return {cstr(service.get(field)) for field in ("service_code", "service_id", "id")} - {""}


def origin_name(
	ship_from: str | None = None, warehouse: str | None = None, fulfillment_provider: str | None = None
) -> str | None:
	"""The Address a parcel leaves from: its store location's, else Starshipit's Ship From Address
	when the parcel is sent with Starshipit or Starshipit is in use, else the warehouse's, else the
	company's."""
	if ship_from:
		return ship_from
	s = settings()
	if s.get("sender_address") and (s.enabled or fulfillment_provider == StarshipitFulfillment.key):
		return s.sender_address
	from frappe.contacts.doctype.address.address import get_default_address

	if warehouse and (name := get_default_address("Warehouse", warehouse)):
		return name
	company = (
		warehouse and frappe.db.get_value("Warehouse", warehouse, "company")
	) or frappe.db.get_single_value("Shop Settings", "company")
	if not company:
		return None
	from erpnext.setup.doctype.company.company import get_default_company_address

	return get_default_company_address(company)


def origin_address(
	ship_from: str | None = None, warehouse: str | None = None, fulfillment_provider: str | None = None
) -> frappe._dict | None:
	name = origin_name(ship_from, warehouse, fulfillment_provider)
	return get_address(name) if name else None


def destination_address(address) -> frappe._dict:
	return frappe._dict(
		address_title=address.get("address_title") or "",
		address_line1=address.address_line1,
		address_line2=address.address_line2,
		city=address.city,
		state=address.state,
		pincode=(address.pincode or "").replace(" ", ""),
		country=address.country,
		country_code=address.country_code or "",
	)


def content_description() -> str:
	return settings().get("content_description") or _("Goods")


def cart_lines(items: list[dict]) -> list[dict]:
	"""Item lines for parcels_for_items."""
	lines = []
	meta = frappe.get_meta("Item")
	fields = ["item_name", "weight_per_unit", "weight_uom", "is_stock_item"]
	for field in ("shipping_parcel_template", "ships_in_own_box"):
		if meta.has_field(field):
			fields.append(field)
	for row in items:
		item = frappe.db.get_value("Item", row["item_code"], fields, as_dict=True)
		if not item:
			continue
		if not item.is_stock_item and not item.weight_per_unit:
			continue  # a service, not something in the parcel
		lines.append(
			frappe._dict(
				item_code=row["item_code"],
				qty=flt(row["qty"]),
				kg=kg_per_unit(item.weight_per_unit, item.weight_uom) or 0,
				template=item.get("shipping_parcel_template"),
				own_box=cint(item.get("ships_in_own_box")),
			)
		)
	return lines


def cache_key(provider: str, origin, destination, parcels) -> str:
	raw = json.dumps(
		[
			provider,
			[origin.address_line1, origin.city, origin.pincode, origin.country],
			[destination.address_line1, destination.city, destination.pincode, destination.country],
			parcels,
		],
		sort_keys=True,
		default=str,
	)
	return RATE_CACHE_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:32]


def pickup_contact_user(warehouse: str | None = None) -> str | None:
	"""Who hands parcels to the courier: the warehouse's Pickup Contact, else Starshipit's."""
	user = None
	if warehouse and frappe.get_meta("Warehouse").has_field("shipping_pickup_contact"):
		user = frappe.db.get_value("Warehouse", warehouse, "shipping_pickup_contact")
	return user or settings().get("pickup_contact")


def user_contact(user: str | None) -> frappe._dict | None:
	"""A user's details as providers take a pickup contact."""
	if not user:
		return None
	contact = frappe.db.get_value(
		"User", user, ["first_name", "last_name", "email", "phone", "mobile_no", "gender"], as_dict=True
	)
	if not contact:
		return None
	contact.email_id = contact.pop("email", None)
	contact.phone = contact.phone or contact.mobile_no
	return contact


# --- fulfilment --------------------------------------------------------------------

try:
	from shop.fulfillment.provider import FulfillmentProvider
except ImportError:  # Shop is not installed
	FulfillmentProvider = object


class ShippingFulfillment(FulfillmentProvider):
	"""A store parcel becomes a draft Shipment, to book with any enabled shipping provider."""

	key = "erpnext_shipping"
	needs_ship_from = True
	shipping_providers = None  # every provider of this app

	def default_ship_from(self) -> str | None:
		"""Where a location without its own Ship From Address sends from."""
		return origin_name(fulfillment_provider=self.key)

	@property
	def label(self) -> str:
		return _("ERPNext Shipping (Shipment)")

	def is_configured(self) -> bool:
		return any(
			frappe.db.get_single_value(provider, "enabled")
			for provider in self.shipping_providers or shipping_providers()
		)

	def setup_hint(self) -> str:
		return _(
			"Enable a shipping provider ({0}), and give each warehouse a Pickup Contact whose phone starts "
			"with + and the country code."
		).format(", ".join(shipping_providers()))

	def create_shipment(self, order, items: list[dict] | None = None, fulfillment=None) -> dict:
		"""Deliver the parcel and make a draft Shipment for it.

		Shop may pass the Shop Fulfillment being sent; when it doesn't, it is the latest Pending one
		of this provider for the order (and the items' warehouse)."""
		fulfillment = fulfillment or pending_fulfillment(order.name, self.key, items)
		shipment = create_shipment_for_order(order.name, items, fulfillment, self.key)
		return {
			"status": "Accepted",
			"external_id": shipment,
			"raw": {"shipment": shipment},
		}

	def fetch_status(self, fulfillment) -> dict:
		if not fulfillment.external_id or not frappe.db.exists("Shipment", fulfillment.external_id):
			return {"status": fulfillment.status}
		shipment = frappe.db.get_value(
			"Shipment",
			fulfillment.external_id,
			["docstatus", "awb_number", "carrier", "tracking_url", "tracking_status", "tracking_status_info"],
			as_dict=True,
		)
		if shipment.docstatus == 2:
			return {"status": "Cancelled"}
		result = {"raw": shipment, "carrier": shipment.carrier}
		info = (shipment.tracking_status_info or "").split(":")[0].strip().lower()
		if shipment.tracking_status == "Delivered":
			result["status"] = "Delivered"
		elif shipment.tracking_status in ("Returned", "Lost"):
			result["status"] = "Failed"
		elif shipment.tracking_status == "In Progress" and info not in NOT_YET_SHIPPED:
			# manifested, or the courier has scanned it
			result["status"] = "Shipped"
		else:
			result["status"] = "Accepted"
		if shipment.awb_number:
			result["tracking_number"] = shipment.awb_number.split(",")[0].strip()
		if shipment.tracking_url:
			result["tracking_url"] = shipment.tracking_url
		return result

	def cancel(self, fulfillment) -> dict:
		"""Cancel the Shipment (and its provider's order) and put the stock back: an unbilled Delivery
		Note is cancelled, an invoiced one is returned with ERPNext's return Delivery Note."""
		name = fulfillment.external_id
		if name and frappe.db.exists("Shipment", name):
			doc = frappe.get_doc("Shipment", name)
			# a label bought but not yet tracked is paid for: it isn't thrown away from the store
			if doc.docstatus == 1 and (
				doc.awb_number or doc.tracking_status in SENT_STATUSES or label_files(doc.name)
			):
				frappe.throw(
					_(
						"Shipment {0} has a label or is with the courier, so it can't be cancelled from the store. "
						"Cancel the consignment with the courier, then cancel the Shipment."
					).format(frappe.bold(name)),
					title=_("Parcel already sent"),
				)
			notes = [row.delivery_note for row in doc.shipment_delivery_note]
			if doc.docstatus == 0:
				doc.delete(ignore_permissions=True)
			elif doc.docstatus == 1:
				doc.flags.ignore_permissions = True
				doc.cancel()  # also deletes or archives a Starshipit order
			for note in notes:
				note_doc = frappe.get_doc("Delivery Note", note)
				if note_doc.docstatus != 1:
					continue
				if not flt(note_doc.per_billed):
					note_doc.flags.ignore_permissions = True
					note_doc.cancel()
				else:
					return_note(note)
		return {"status": "Cancelled"}

	def courier(self, fulfillment) -> dict | None:
		"""Where the parcel's courier booking and label stand."""
		doc = parcel_shipment(fulfillment)
		if not doc or doc.docstatus == 2:
			return None
		return {
			"booked": bool(doc.shipment_id),
			"provider": doc.service_provider if doc.shipment_id else None,
			"service": doc.carrier_service if doc.shipment_id else None,
			"amount": flt(doc.shipment_amount) if doc.shipment_id else None,
			"currency": shipment_currency(doc),
			"labels": len(label_files(doc.name)),
			"can_book": not doc.shipment_id,
			"can_print_label": bool(doc.shipment_id),
		}

	def courier_services(self, fulfillment) -> list[dict]:
		"""The services the parcel can be booked with and their prices, cheapest first."""
		doc = unbooked_shipment(fulfillment)
		return [
			{
				"id": service_key(service),
				"provider": service.get("service_provider"),
				"carrier": service.get("carrier"),
				"service": service.get("service_name"),
				"amount": flt(service.get("total_price")),
				"currency": service.get("currency") or shipment_currency(doc),
				"preferred": bool(service.get("is_preferred")),
			}
			for service in self.services_for(doc)
		]

	def book_courier(self, fulfillment, service_id: str) -> dict:
		"""Submit the parcel's Shipment if it is a draft, and book it with the service chosen, at the
		provider's current price."""
		from erpnext_shipping.erpnext_shipping.shipping import book_shipment

		doc = unbooked_shipment(fulfillment)
		if not doc.delivery_contact_name:
			frappe.throw(
				_("Shipment {0} has no delivery contact. Add the customer's contact to it in Desk.").format(
					frappe.bold(doc.name)
				)
			)
		service = next((s for s in self.services_for(doc) if service_key(s) == service_id), None)
		if not service:
			frappe.throw(_("That service isn't offered for this parcel any more. Choose another."))
		if doc.docstatus == 0:
			doc.flags.ignore_permissions = True
			doc.submit()
		book_shipment(
			doc.name,
			**shipment_details(doc),
			shipment_parcel=parcels_json(doc),
			service_data=json.dumps(service, default=str),
			delivery_notes=[row.delivery_note for row in doc.shipment_delivery_note],
		)
		return self.fetch_status(fulfillment)

	def print_label(self, fulfillment) -> dict:
		"""The parcel's label files, bought from the provider the first time; the parcel's status with
		any tracking number the label brought."""
		from erpnext_shipping.erpnext_shipping.shipping import save_label_as_attachment, shipping_label

		doc = parcel_shipment(fulfillment)
		if not doc or doc.docstatus != 1 or not doc.shipment_id:
			frappe.throw(_("Book a courier for this parcel first."))
		files = label_files(doc.name)
		if not files:
			label = shipping_label(doc)
			if isinstance(label, str):
				# LetMeShip answers with the label's bytes
				label = [save_label_as_attachment(doc.name, bytes(json.loads(label)))]
			files = label_files(doc.name) or list(label or [])
		return {**self.fetch_status(fulfillment), "files": files}

	def labels(self, fulfillment) -> list[str]:
		"""The labels printed for the parcel so far."""
		doc = parcel_shipment(fulfillment)
		return label_files(doc.name) if doc else []

	def services_for(self, doc) -> list[dict]:
		from erpnext_shipping.erpnext_shipping.shipping import shipping_rates

		services = shipping_rates(**shipment_details(doc), parcels=parcels_json(doc), shipment=doc.name)
		if self.shipping_providers:
			services = [s for s in services if s.get("service_provider") in self.shipping_providers]
		return services


class StarshipitFulfillment(ShippingFulfillment):
	"""The same with Starshipit rates only, for stores and fulfilments set up with it."""

	key = "starshipit"
	shipping_providers = (STARSHIPIT_PROVIDER,)

	@property
	def label(self) -> str:
		return _("Starshipit (Shipment)")

	def is_configured(self) -> bool:
		s = settings()
		return bool(
			s.enabled and s.get("sender_address") and s.get("pickup_contact") and pickup_phone_valid()
		)

	def setup_hint(self) -> str:
		return _(
			"Enable Starshipit and set the Ship From Address and a Pickup Contact whose phone starts with + and "
			"the country code, in Starshipit settings."
		)


FULFILLMENT_PROVIDERS = {provider.key: provider for provider in (ShippingFulfillment, StarshipitFulfillment)}


def sync_store_fulfillment(shipment: str) -> None:
	"""Bring the store's fulfilment of a Shipment up to date when its provider has news of it, so
	the customer hears it has shipped or arrived without waiting for Shop's hourly sync."""
	if "shop" not in frappe.get_installed_apps():
		return
	from shop.fulfillment import service

	for name in frappe.get_all(
		"Shop Fulfillment",
		filters={
			"provider": ["in", list(FULFILLMENT_PROVIDERS)],
			"external_id": shipment,
			"status": ["in", ("Pending", "Accepted", "Shipped")],
		},
		pluck="name",
	):
		service.sync(name)


def pending_fulfillment(order_name: str, provider_key: str, items: list[dict] | None = None):
	"""The Shop Fulfillment being sent: the latest Pending one of this provider for the order, of
	the items' warehouse when they name one."""
	if not frappe.db.exists("DocType", "Shop Fulfillment"):
		return None
	filters = {"sales_order": order_name, "status": "Pending", "provider": provider_key}
	warehouses = {row.get("warehouse") for row in items or []} - {None, ""}
	if len(warehouses) == 1 and frappe.get_meta("Shop Fulfillment").has_field("warehouse"):
		filters["warehouse"] = warehouses.pop()
	name = frappe.db.get_value("Shop Fulfillment", filters, "name", order_by="creation desc")
	return frappe.get_doc("Shop Fulfillment", name) if name else None


def parcel_of(fulfillment, items: list[dict] | None) -> frappe._dict:
	"""The parcel being sent: its items, warehouse, Ship From Address and the shopper's choice."""
	fulfillment = fulfillment or frappe._dict()
	if not items and fulfillment.get("items"):
		items = [
			{"item_code": row.item_code, "item_name": row.item_name, "qty": flt(row.qty)}
			for row in fulfillment.get("items")
		]
	items = [frappe._dict(row) for row in items or []]
	warehouse = fulfillment.get("warehouse") or next(
		(row.warehouse for row in items if row.get("warehouse")), None
	)
	return frappe._dict(
		lines=items,
		warehouse=warehouse,
		ship_from=fulfillment.get("ship_from_address"),
		shipping_option=fulfillment.get("shipping_option"),
		shipment=fulfillment.get("external_id"),
	)


def parcel_shipment(fulfillment):
	"""The Shipment a store parcel was handed to, if it is still there."""
	name = fulfillment.external_id
	return frappe.get_doc("Shipment", name) if name and frappe.db.exists("Shipment", name) else None


def unbooked_shipment(fulfillment):
	doc = parcel_shipment(fulfillment)
	if not doc or doc.docstatus == 2:
		frappe.throw(_("This parcel has no Shipment to book. Send it to fulfilment again."))
	if doc.shipment_id:
		frappe.throw(
			_("Shipment {0} is already booked with {1}.").format(frappe.bold(doc.name), doc.service_provider)
		)
	return doc


def shipment_details(doc) -> dict:
	"""A Shipment's details as rates and booking take them, as the Shipment form sends them."""
	return {
		"pickup_from_type": doc.pickup_from_type,
		"delivery_to_type": doc.delivery_to_type,
		"pickup_address_name": doc.pickup_address_name,
		"delivery_address_name": doc.delivery_address_name,
		"description_of_content": doc.description_of_content,
		"pickup_date": doc.pickup_date,
		"value_of_goods": doc.value_of_goods,
		"pickup_contact_name": doc.pickup_contact_person
		if doc.pickup_from_type == "Company"
		else doc.pickup_contact_name,
		"delivery_contact_name": doc.delivery_contact_name,
	}


def shipment_currency(doc) -> str | None:
	"""What the Shipment's prices are in: its company's currency."""
	company = doc.get("pickup_company") or doc.get("delivery_company")
	if company:
		return frappe.get_cached_value("Company", company, "default_currency")
	return frappe.db.get_default("currency")


def parcels_json(doc) -> str:
	return json.dumps([row.as_dict() for row in doc.shipment_parcel], default=str)


def service_key(service: dict) -> str:
	"""A service told apart from the others offered: its provider, carrier and service."""
	return "|".join(
		cstr(service.get(key)) for key in ("service_provider", "carrier", "service_id", "service_name")
	)


def label_files(shipment: str) -> list[str]:
	"""The label PDFs attached to a Shipment, in order."""
	return frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Shipment",
			"attached_to_name": shipment,
			"file_name": ["like", "label%"],
		},
		pluck="file_url",
		order_by="file_name",
	)


def return_note(delivery_note: str) -> str:
	"""ERPNext's return Delivery Note for what an invoiced Delivery Note sent, so the stock is back."""
	from erpnext.stock.doctype.delivery_note.delivery_note import make_sales_return

	returned = make_sales_return(delivery_note)
	returned.flags.ignore_permissions = True
	returned.insert(ignore_permissions=True)
	returned.submit()
	return returned.name


def create_shipment_for_order(
	order_name: str,
	items: list[dict] | None = None,
	fulfillment=None,
	fulfillment_provider: str | None = None,
) -> str:
	"""Deliver a store order, or one parcel of it, and make a draft Shipment for it. Returns the
	Shipment name.

	A parcel (its fulfilment or items name a warehouse) has only its items delivered, from that
	warehouse, and is picked up from its Ship From Address. Without one the whole order is
	delivered, as for orders sent before fulfilment locations."""
	from erpnext.stock.doctype.delivery_note.delivery_note import make_shipment

	parcel = parcel_of(fulfillment, items)
	if parcel.warehouse:
		if parcel.shipment and frappe.db.get_value("Shipment", parcel.shipment, "docstatus") in (0, 1):
			return parcel.shipment
		delivery_note = deliver_items(order_name, parcel.lines, parcel.warehouse)
	else:
		existing = (
			frappe.db.get_value(
				"Shipment Delivery Note",
				{"delivery_note": ["in", delivery_notes(order_name)], "docstatus": ["<", 2]},
				"parent",
			)
			if delivery_notes(order_name)
			else None
		)
		if existing:
			return existing
		delivery_note = deliver(order_name)
	pickup_user = pickup_contact_user(parcel.warehouse)
	shipment = make_shipment(delivery_note)
	shipment.pickup_from_type = "Company"
	shipment.pickup_address_name = origin_name(parcel.ship_from, parcel.warehouse, fulfillment_provider)
	shipment.pickup_contact_person = pickup_user
	set_pickup_contact(shipment, pickup_user)
	shipment.pickup_date = next_business_day(company=shipment.pickup_company)
	shipment.description_of_content = content_description()
	shipment.parcel_template = None
	shipment.flags.fill_parcels_from_items = True
	choice = chosen_service(parcel.shipping_option or order_shipping_option(order_name))
	if choice:
		shipment.preferred_service = json.dumps(choice)
	shipment.flags.ignore_permissions = True
	shipment.insert(ignore_permissions=True)
	if choice:
		shipment.add_comment(
			"Info", _("The customer chose {0} at checkout.").format(frappe.bold(choice.get("label") or ""))
		)
	return shipment.name


def set_pickup_contact(shipment, user: str | None) -> None:
	"""The pickup contact's details on the Shipment, as its form fills them from the user."""
	if not user:
		return
	contact = frappe.db.get_value(
		"User", user, ["first_name", "last_name", "email", "phone", "mobile_no"], as_dict=True
	)
	if not contact:
		return
	lines = [
		" ".join(name for name in [contact.first_name, contact.last_name] if name),
		contact.email,
		contact.phone or contact.mobile_no,
	]
	shipment.pickup_contact = "<br>".join(line for line in lines if line)
	shipment.pickup_contact_email = contact.email


def pickup_phone_valid(user: str | None = None) -> bool:
	"""The pickup contact's phone is in the form a Shipment needs: + and the country code."""
	import re

	phone = frappe.db.get_value("User", user or settings().get("pickup_contact"), "phone")
	return bool(phone and re.match(r"^\+(?![\s0])[\d\s]+\d$", phone))


def delivery_notes(order_name: str) -> list[str]:
	return frappe.get_all(
		"Delivery Note Item",
		filters={"against_sales_order": order_name, "docstatus": 1},
		pluck="parent",
		distinct=True,
	)


def make_delivery_note(order_name: str):
	try:
		from shop.compat import make_delivery_note
	except ImportError:
		from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
	return make_delivery_note(order_name)


def deliver(order_name: str) -> str:
	"""A submitted Delivery Note for what the order still has to deliver; when it is all delivered,
	its latest Delivery Note."""
	existing = delivery_notes(order_name)
	if existing and flt(frappe.db.get_value("Sales Order", order_name, "per_delivered")) >= 100:
		return existing[-1]
	note = make_delivery_note(order_name)
	note.flags.ignore_permissions = True
	note.insert(ignore_permissions=True)
	note.submit()
	return note.name


def deliver_items(order_name: str, items: list[dict], warehouse: str | None = None) -> str:
	"""A submitted Delivery Note for one parcel of the order: only these items, from their warehouse.

	Each item's quantity is taken from the order's lines still to deliver, lines of the same
	warehouse first."""
	note = make_delivery_note(order_name)
	remaining = {row.idx: flt(row.qty) for row in note.items}
	taken = {}
	for item in items:
		item = frappe._dict(item)
		source = item.get("warehouse") or warehouse
		qty = flt(item.qty)
		rows = [row for row in note.items if row.item_code == item.item_code]
		rows.sort(key=lambda row: row.warehouse != source)
		for row in rows:
			if qty <= 0:
				break
			share = min(qty, remaining[row.idx])
			if share <= 0:
				continue
			remaining[row.idx] -= share
			qty -= share
			taken.setdefault(row.idx, [0.0, source])[0] += share
		if qty > 0:
			frappe.throw(
				_("{0} of {1} is not left to deliver on Sales Order {2}").format(
					qty, frappe.bold(item.get("item_name") or item.item_code), order_name
				)
			)
	if not taken:
		frappe.throw(_("Nothing in this parcel is left to deliver on Sales Order {0}").format(order_name))
	rows = []
	for row in note.items:
		if row.idx not in taken:
			continue
		row.qty, source = taken[row.idx]
		row.stock_qty = flt(row.qty) * flt(row.conversion_factor or 1)
		if source:
			row.warehouse = source
		rows.append(row)
	note.set("items", rows)
	if warehouse and note.get("set_warehouse"):
		note.set_warehouse = warehouse
	note.flags.ignore_permissions = True
	note.insert(ignore_permissions=True)
	note.submit()
	return note.name


def order_shipping_option(order_name: str) -> str | None:
	"""The shipping option the shopper chose for the whole order, before fulfilment locations."""
	if not frappe.get_meta("Sales Order").has_field("shop_shipping_option"):
		return None
	return frappe.db.get_value("Sales Order", order_name, "shop_shipping_option")


def option_provider(option: dict) -> str | None:
	"""The shipping provider whose service a checkout option is, from its meta or its id's prefix."""
	providers = shipping_providers()
	provider = (option.get("meta") or {}).get("provider")
	if provider in providers:
		return provider
	prefix = cstr(option.get("id")).split(":", 1)[0]
	return next((name for name in providers if frappe.scrub(name) == prefix), None)


def chosen_service(option) -> dict | None:
	"""What a Shipment keeps of the shopper's choice: its label and price and, for a provider's
	service, which provider and service it is."""
	if isinstance(option, str):
		try:
			option = json.loads(option)
		except ValueError:
			return None
	if not isinstance(option, dict) or not option:
		return None
	choice = {"label": option.get("label"), "amount": option.get("amount")}
	provider = option_provider(option)
	if not provider:
		return choice
	meta = option.get("meta") or {}
	choice.update(
		provider=provider,
		carrier=meta.get("carrier") or option.get("carrier"),
		service_id=meta.get("service_id"),
		service_name=meta.get("service_name"),
	)
	return choice


def next_business_day(start: str | None = None, company: str | None = None) -> str:
	"""The next day after start that isn't a holiday in the company's default Holiday List, or with
	no Holiday List, the next weekday."""
	from erpnext.setup.doctype.holiday_list.holiday_list import is_holiday

	company = company or frappe.db.get_single_value("Shop Settings", "company")
	holidays = frappe.get_cached_value("Company", company, "default_holiday_list") if company else None
	day = getdate(add_days(start or nowdate(), 1))
	for _attempt in range(366):
		if not (is_holiday(holidays, day) if holidays else day.weekday() >= 5):
			break
		day = getdate(add_days(day, 1))
	return str(day)


def mark_preferred(services: list[dict], shipment: str | None) -> list[dict]:
	"""Flag the service the customer chose at checkout in the Shipment's service list."""
	if not shipment or not frappe.db.exists("Shipment", shipment):
		return services
	raw = (
		frappe.db.get_value("Shipment", shipment, "preferred_service")
		if frappe.get_meta("Shipment").has_field("preferred_service")
		else None
	)
	if not raw:
		return services
	try:
		choice = json.loads(raw)
	except ValueError:
		return services
	provider = choice.get("provider")
	if not provider:
		return services
	service_id = cstr(choice.get("service_id"))
	for service in services:
		if service.get("service_provider") != provider:
			continue
		if service_id:
			matches = cstr(service.get("service_id") or service.get("id")) == service_id
		else:
			matches = (
				bool(choice.get("service_name")) and service.get("service_name") == choice["service_name"]
			)
		if matches:
			service["is_preferred"] = 1
	return services
