"""Addresses suggested while one is typed, from the service in Address Lookup Settings: Photon
(OpenStreetMap; komoot's public server or your own) or Google Places. The service is asked from
this site, so its key stays here; answers are cached.

Desk's Address form uses `suggest` and `address`; other apps (Shop's storefront) call
`suggestions` and `details` behind endpoints of their own."""

import hashlib

import frappe
import requests
from frappe import _
from frappe.rate_limiter import rate_limit

PHOTON_SERVER = "https://photon.komoot.io"
GOOGLE_PLACES = "https://places.googleapis.com/v1"
MIN_TEXT = 3
LIMIT = 6
TIMEOUT = 5
CACHE_SECONDS = 24 * 60 * 60


@frappe.whitelist()
@rate_limit(limit=90, seconds=60)
def suggest(text: str, country: str | None = None, session: str | None = None) -> dict:
	return suggestions(text, country, session)


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def address(id: str, session: str | None = None) -> dict:
	return details(id, session)


def suggestions(text: str, country: str | None = None, session: str | None = None) -> dict:
	"""Addresses matching what has been typed so far, in `country` (an ERPNext Country) when given."""
	service = active_service()
	if not service:
		return {"enabled": False, "suggestions": []}
	text = (text or "").strip()[:200]
	if len(text) < MIN_TEXT:
		return {"enabled": True, "suggestions": [], "attribution": service.attribution}
	code = country_code(country)
	key = cache_key("suggest", service.name, service.source, text.lower(), code)
	found = frappe.cache.get_value(key)
	if found is None:
		try:
			found = service.suggest(text, code, session)
		except requests.RequestException:
			return {"enabled": True, "suggestions": [], "attribution": service.attribution}
		frappe.cache.set_value(key, found, expires_in_sec=CACHE_SECONDS)
	return {"enabled": True, "suggestions": found, "attribution": service.attribution}


def details(id: str, session: str | None = None) -> dict:
	"""The address fields of a suggestion that came without them (Google Places)."""
	service = active_service()
	if not service:
		frappe.throw(_("Address lookup is turned off in Address Lookup Settings"))
	key = cache_key("address", service.name, service.source, id)
	found = frappe.cache.get_value(key)
	if found is None:
		try:
			found = service.address(id, session)
		except requests.RequestException:
			frappe.throw(_("The address couldn't be looked up just now. Please type it in."))
		frappe.cache.set_value(key, found, expires_in_sec=CACHE_SECONDS)
	return found


def status() -> dict:
	"""Which service suggests addresses, for settings pages elsewhere."""
	service = active_service()
	return {
		"enabled": bool(service),
		"service": service.name if service else None,
		"settings_url": "/app/address-lookup-settings",
	}


def active_service():
	settings = frappe.get_cached_doc("Address Lookup Settings")
	if settings.service == "Photon":
		return Photon(settings.photon_server or PHOTON_SERVER)
	if settings.service == "Google Places":
		api_key = settings.get_password("google_api_key", raise_exception=False)
		return GooglePlaces(api_key, settings.daily_limit) if api_key else None
	return None


class Photon:
	name = "Photon"
	attribution = "Addresses © OpenStreetMap contributors"

	def __init__(self, server: str):
		self.server = self.source = server.rstrip("/")

	def suggest(self, text: str, code: str | None, session: str | None) -> list[dict]:
		params = [("q", text), ("limit", LIMIT * 2), ("lang", "en"), ("layer", "house"), ("layer", "street")]
		if code:
			params.append(("countrycode", code))
		response = requests.get(
			f"{self.server}/api/", params=params, headers={"User-Agent": user_agent()}, timeout=TIMEOUT
		)
		response.raise_for_status()
		suggestions, seen = [], set()
		for feature in response.json().get("features") or []:
			found = photon_address(feature.get("properties") or {})
			if not found or (code and feature["properties"].get("countrycode", "").upper() != code):
				continue
			label, detail = found["address_line1"], place_line(found)
			if (label, detail) in seen:
				continue
			seen.add((label, detail))
			suggestions.append(
				{"id": f"{label}|{detail}", "label": label, "detail": detail, "address": found}
			)
		return suggestions[:LIMIT]

	def address(self, id: str, session: str | None) -> dict:
		frappe.throw(_("Photon's suggestions carry their address"))


def photon_address(place: dict) -> dict | None:
	street = place.get("street") or (place.get("name") if place.get("type") == "street" else None)
	if not street:
		return None
	city = place.get("city") or place.get("town") or place.get("village") or place.get("county")
	suburb = place.get("district") or place.get("locality")
	return {
		"address_line1": " ".join(part for part in [place.get("housenumber"), street] if part),
		"address_line2": suburb if suburb and suburb != city else "",
		"city": city or suburb or "",
		"state": place.get("state") or "",
		"pincode": place.get("postcode") or "",
		"country": country_name(place.get("countrycode")),
	}


class GooglePlaces:
	name = "Google Places"
	attribution = "Powered by Google"

	source = GOOGLE_PLACES

	def __init__(self, api_key: str, daily_limit: int = 0):
		self.api_key = api_key
		self.daily_limit = daily_limit

	def spend(self) -> None:
		"""Count a paid request; past the day's limit no more are sent."""
		if not self.daily_limit:
			return
		key = frappe.cache.make_key(f"address_lookup:google:{frappe.utils.nowdate()}")
		count = frappe.cache.incrby(key, 1)
		if count == 1:
			frappe.cache.expire(key, 2 * 24 * 60 * 60)
		if count > self.daily_limit:
			raise requests.RequestException("Address lookup's daily limit is reached")

	def suggest(self, text: str, code: str | None, session: str | None) -> list[dict]:
		body = {"input": text, "includedPrimaryTypes": ["street_address", "premise", "subpremise", "route"]}
		if code:
			body["includedRegionCodes"] = [code.lower()]
		if session:
			body["sessionToken"] = session
		self.spend()
		response = requests.post(
			f"{GOOGLE_PLACES}/places:autocomplete",
			json=body,
			headers={"X-Goog-Api-Key": self.api_key},
			timeout=TIMEOUT,
		)
		response.raise_for_status()
		suggestions = []
		for row in response.json().get("suggestions") or []:
			place = row.get("placePrediction")
			if not place:
				continue
			parts = place.get("structuredFormat") or {}
			suggestions.append(
				{
					"id": place["placeId"],
					"label": (parts.get("mainText") or {}).get("text") or place["text"]["text"],
					"detail": (parts.get("secondaryText") or {}).get("text") or "",
				}
			)
		return suggestions[:LIMIT]

	def address(self, id: str, session: str | None) -> dict:
		self.spend()
		response = requests.get(
			f"{GOOGLE_PLACES}/places/{requests.utils.quote(id, safe='')}",
			params={"sessionToken": session} if session else None,
			headers={"X-Goog-Api-Key": self.api_key, "X-Goog-FieldMask": "addressComponents"},
			timeout=TIMEOUT,
		)
		response.raise_for_status()
		return google_address(response.json().get("addressComponents") or [])


def google_address(components: list[dict]) -> dict:
	def part(*types, short=False):
		for kind in types:
			for component in components:
				if kind in component.get("types", []):
					return component.get("shortText" if short else "longText") or ""
		return ""

	unit, number, street = part("subpremise"), part("street_number"), part("route")
	city = part("locality", "postal_town", "administrative_area_level_2")
	suburb = part("sublocality_level_1", "sublocality", "neighborhood")
	house = f"{unit}/{number}" if unit and number else number
	return {
		"address_line1": " ".join(p for p in [house, street] if p),
		"address_line2": suburb if suburb != city else "",
		"city": city,
		"state": part("administrative_area_level_1"),
		"pincode": part("postal_code"),
		"country": country_name(part("country", short=True)),
	}


def country_code(country: str | None) -> str | None:
	code = frappe.db.get_value("Country", country, "code") if country else None
	return code.upper() if code else None


def country_name(code: str | None) -> str:
	if not code:
		return ""
	return frappe.db.get_value("Country", {"code": code.lower()}, "name") or ""


def place_line(found: dict) -> str:
	parts = [found["address_line2"], found["city"], found["pincode"], found["country"]]
	return ", ".join(part for part in parts if part)


def cache_key(*parts) -> str:
	return "address_lookup:" + hashlib.sha1("|".join(str(part) for part in parts).encode()).hexdigest()


def user_agent() -> str:
	return f"ERPNext Shipping ({frappe.utils.get_url()})"
