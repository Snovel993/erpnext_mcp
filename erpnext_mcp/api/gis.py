# SPDX-License-Identifier: MIT
"""The county's copy of the shape, and the one path a drawn boundary takes to disk.

v0.33.0. THE THIRD TRANSPORT, and the smallest. `mcp.handle` serves the AI,
`api/mobile.py` serves forty phones, and these two methods serve exactly one
thing: the Leaflet map on a Desk form, driven by a person who is already signed
in to that Desk and already looking at the record.

────────────────────────────────────────────────────────────────────────────
WHY A BOUNDARY MAY NOW BE DRAWN, AFTER v0.32.0 SAID IT MAY NOT
────────────────────────────────────────────────────────────────────────────

v0.32.0's widget was read-only and said so at length: "a map that could nudge a
vertex would be a way to change all of that by accident, with no validation and
no audit row." Every word of that is still true, and it is the reason this file
exists rather than the reason it should not.

The thing v0.32.0 was refusing was a map that WROTE TO THE FIELD. What
`save_boundary` does instead is call `set_parcel_boundary`, `set_field_boundary`
or `set_zone_boundary` — the same three tools the AI calls, unchanged, with every
check they have always made: the polygon is parsed, self-intersection is refused,
the enclosed area is compared against the recorded acreage and a disagreement
past a quarter is REFUSED outright, containment against the shape above is
reported, and every derived field (centroid, bounding box, H3 coverage, computed
acres) is recomputed from the polygon rather than typed. A vertex nudged by
accident does not get saved quietly; it gets an area disagreement and a refusal.

So the map is not a way round the boundary tools. It is a second caller of them,
and the one whose caller can see what they are about to change.

AND THE FIELD WAS ALWAYS WRITABLE ANYWAY. `boundary_geojson` is an ordinary Long
Text on Parcel, Field and Irrigation Zone with no `read_only` flag: anybody with
write permission could already paste a polygon straight into the form and press
save — which stores it with NOTHING derived, no validation, no area check, and a
centroid left over from the previous shape. This endpoint is strictly the safer
of the two doors, and it is the one with a map next to it.

────────────────────────────────────────────────────────────────────────────
THE GATE IS FRAPPE'S OWN PERMISSION ON THE DOCUMENT, AND NOTHING ELSE
────────────────────────────────────────────────────────────────────────────

`api/guard.py` is not used here and must not be. Its seven checks are built for
an endpoint on the open internet reached by a phone with an API key, and its
fourth check — an Active Mobile Access Grant — would mean the operator could not
draw a boundary on their own Desk until they had enrolled themselves as a field
device. That is the wrong gate on the wrong door.

The right gate is the one the Desk already applies to the form the button is on:

  1. a named user (Guest is refused before anything is read or fetched),
  2. `frappe.has_permission(doctype, "write", doc=name, throw=True)` on the
     SPECIFIC document — not the doctype in general, so a User Permission that
     scopes somebody to one company scopes this too,
  3. a closed list of three doctypes. There is no dispatcher and no
     method-name argument: `SAVEABLE` below is the whole reachable surface and
     `create_journal_entry` is not in it.

Check 2 matters more than it looks. The boundary tools end in
`doc.save(ignore_permissions=True)` — correct for them, because the MCP transport
did its own authorisation three layers earlier — so a wrapper that forgot to ask
would have handed every signed-in user on the site a write to every parcel.

THE `allow_<tool>` SWITCHES ARE DELIBERATELY NOT CONSULTED, the same call
`api/__init__.py` made for the phone and for the same reason: those switches are
the AI's leash. `allow_set_parcel_boundary` off means "the model may not redraw
the farm", and reading it here would mean an operator who distrusts the model
also loses the ability to trace a parcel by hand — which is not what they asked
for and not what the switch says.

────────────────────────────────────────────────────────────────────────────
THE COUNTY LOOKUP, AND WHY IT IS PROXIED RATHER THAN FETCHED BY THE BROWSER
────────────────────────────────────────────────────────────────────────────

Wasco County publishes its tax lots as an ArcGIS FeatureServer, free, with no
key, in WGS84 on request. That is the same polygon the assessor, the deed and
the tax bill are describing, which makes it a far better starting point than a
person tracing an outline off a satellite image by eye — and tracing it by eye
is what everybody does when importing is hard.

The browser could call it directly. It should not, for three reasons:

  * CORS is not ours to promise. The endpoint may or may not send
    `Access-Control-Allow-Origin` today and may stop tomorrow, and the failure
    is a console error on somebody's form with no server-side trace of why.
  * One URL, one place. A county's endpoint that moves is one constant here
    rather than a string in a JavaScript file that a browser has cached.
  * The `where` clause is a query language, and the browser is the wrong place
    to be careful about it. See `_tax_lot_clause`.

`requests` IS IMPORTED DEFENSIVELY, like shapely and h3 and segno before it. It
is a Frappe dependency and every real bench has it; a bench that somehow does not
loses the county lookup BY NAME, with the reason, rather than failing to import
this module and taking `save_boundary` down with it. Drawing a boundary by hand
needs no network at all and must keep working.

NOTHING HERE IS A GENERAL HTTP PROXY. The host and path come from `COUNTIES`
below, which is a literal in this file; the caller chooses a key in that dict and
supplies a tax lot or a coordinate pair, both validated before they are
formatted. There is no argument from which a URL could be built.
"""

from __future__ import annotations

import json
import re

import frappe

from .. import audit, geo, security
from ..errors import ToolError
from ..tools import farm as farm_tools
from ..tools import realestate as realestate_tools

try:  # pragma: no cover - a bench without Frappe's own HTTP client
	import requests

	HAVE_REQUESTS = True
except Exception:  # pragma: no cover
	requests = None
	HAVE_REQUESTS = False


#: Every county this app knows how to ask, and how to ask it.
#:
#: A LITERAL, AND THE ONLY SOURCE OF A HOSTNAME IN THIS FILE. The caller picks a
#: key; it cannot supply a URL, a host, a path or a format. That is what keeps a
#: whitelisted method that makes an outbound request from being a way to make the
#: server fetch anything at all on somebody's behalf.
#:
#: WASCO IS THE ONLY ONE, and the dict shape is not speculation about a second:
#: it is what makes "which county is this parcel in" a question the code can
#: answer badly (with a named refusal listing what it does know) rather than one
#: it cannot ask. A farm two miles east is in Sherman County, whose server is a
#: different vendor entirely.
#:
#: `spatial_reference` IS RECORDED AND NOT SENT. The layer stores Oregon
#: Stateplane North in feet (WKID 2913); `outSR=4326` asks the server to project
#: to WGS84 degrees on the way out, which is the only projection this app can
#: read. It is here so that the number in the release notes and the number in the
#: request agree, and so a future county with a different native grid is a data
#: change rather than a puzzle.
COUNTIES = {
	"wasco": {
		"label": "Wasco County, Oregon",
		"url": "https://public.co.wasco.or.us/gisserver/rest/services/Taxlots/FeatureServer/0/query",
		"tax_lot_field": "MapTaxlot",
		"spatial_reference": 2913,
		#: Where each thing this app wants lives in the county's own schema, in
		#: the order to try. Read case-insensitively — an ArcGIS layer's field
		#: names are whatever the person who published it typed.
		"properties": {
			"tax_lot": ("MapTaxlot", "MAPTAXLOT", "Taxlot"),
			"taxpayer": ("Taxpayer", "TAXPAYER", "OwnerName"),
			"acres": ("CalculatedAcres", "CALCULATEDACRES", "Acres", "GISAcres"),
			"account": ("AccountNum", "ACCOUNTNUM", "Account"),
		},
	}
}

DEFAULT_COUNTY = "wasco"

#: The doctypes a boundary can be saved to, the argument each tool wants it
#: under, and the tool itself. THIS IS THE WHOLE REACHABLE SURFACE of
#: `save_boundary` — there is no lookup by name, no registry consulted and no
#: fourth entry that arrives without somebody editing this line.
SAVEABLE = {
	"Parcel": ("parcel", realestate_tools.set_parcel_boundary),
	"Field": ("field", farm_tools.set_field_boundary),
	"Irrigation Zone": ("zone", farm_tools.set_zone_boundary),
}

#: What a Wasco tax lot looks like: `2N11E35BA-01600`. Township, range, section,
#: quarter, lot. Letters, digits, a hyphen, sometimes a dot.
#:
#: THIS IS AN ALLOWLIST AND NOT AN ESCAPE, which is the important part. The value
#: goes into an ArcGIS `where` clause, which is a SQL-ish expression evaluated by
#: somebody else's server — so the safe move is to refuse everything that is not
#: shaped like a tax lot rather than to try to neutralise what a quote could do
#: inside a dialect this app does not implement and cannot test against.
_TAX_LOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,39}$")

#: How long to wait for a county server that a farm's bench reaches over the
#: same connection everything else uses. Fifteen seconds is past a slow answer
#: and short enough that a form does not look hung.
_TIMEOUT_SECONDS = 15

#: How much of a response to read. A tax lot polygon is a few kilobytes; a
#: spatial query that somehow matched the whole county would not be. Capped
#: because an unbounded read of a remote body is a way to fill a worker's memory
#: from outside.
_MAX_BYTES = 4 * 1024 * 1024

#: The most features to hand back to a form. A point lands in one tax lot;
#: overlapping lots and a point on a shared line make two or three. Twenty is far
#: past any honest answer and stops a mistake becoming a payload.
_MAX_FEATURES = 20


def requests_sentence() -> str:
	return (
		"the county GIS lookup needs the `requests` package, which this bench does not have. "
		"Install it with `bench pip install requests` (it is normally already there as a Frappe "
		"dependency), or draw the boundary by hand — that path needs no network at all."
	)


# ── the gates ───────────────────────────────────────────────────────────────
def speaks_frappe(implementation, *args, **kwargs):
	"""Run one of the two implementations below, turning `ToolError` into a modal.

	A `ToolError` means "you asked for something I can't do, and that is not a
	bug" — an unknown tax lot, a polygon that crosses itself, an acreage that
	disagrees with the shape by half. On the MCP transport it becomes a tool
	error with the message intact. Raised out of a `@frappe.whitelist()` method
	it would become an HTTP 500 and a traceback in the browser console, and the
	sentence the tool wrote — the one that says which two acreages disagreed and
	what to do about it — would never reach the person who needs it.

	`frappe.throw` is the Desk's own channel for exactly this: a modal with the
	message in it. Anything that is NOT a ToolError is left alone and still
	reaches the Error Log with its traceback, because that is a bug and hiding it
	would be the wrong favour.

	A FUNCTION AND NOT A DECORATOR, which looks like the clumsier of the two and
	is the correct one here. `frappe.call` reads the whitelisted callable's
	argument names with `inspect.getfullargspec`, which does NOT follow
	`functools.wraps` — so a decorated method presents as `(*args, **kwargs)`,
	and Frappe answers a `(*args, **kwargs)` signature by forwarding the ENTIRE
	form dict, `cmd` and `csrf_token` included. The wrapped function then raises
	TypeError on an argument the browser never sent on purpose. Keeping the real
	signature on the whitelisted function is what stops that.
	"""
	try:
		return implementation(*args, **kwargs)
	except ToolError as error:
		frappe.throw(str(error), title="Boundary")


def _named_user() -> str:
	"""The signed-in user, or a refusal. Guest never gets past this line."""
	user = str(getattr(frappe.session, "user", "") or "")
	if not user or user == "Guest":
		frappe.throw(
			"You must be signed in to use the map tools.",
			frappe.PermissionError,
		)
	return user


def _may_write(doctype: str, name: str = "") -> None:
	"""Frappe's own answer to "may this person change this record".

	`doc=name` rather than the bare doctype ON PURPOSE. A User Permission that
	scopes somebody to one company is enforced per document, and a check written
	against the doctype alone would pass for a parcel they cannot open.
	"""
	frappe.has_permission(doctype, "write", doc=name or None, throw=True)


# ── the county lookup ───────────────────────────────────────────────────────
def _county(name: str = "") -> tuple:
	"""`(key, config)` for a county this app knows, or a refusal that lists them."""
	key = str(name or DEFAULT_COUNTY).strip().lower().replace(" county", "").replace(" ", "_")
	config = COUNTIES.get(key)
	if not config:
		raise ToolError(
			f"no county GIS service is configured for {name!r}. "
			f"Known: {', '.join(sorted(COUNTIES)) or '<none>'}. A county's parcel layer is a "
			"different server and a different schema for every county, so one is added by "
			"naming it rather than by guessing at a URL."
		)
	return key, config


def _tax_lot_clause(config: dict, tax_lot: str) -> str:
	"""`MapTaxlot='2N11E35BA-01600'`, from a value that has been proven harmless.

	The quoting here is trivial precisely because the validation above is not:
	the value has already been refused unless it is letters, digits, dots and
	hyphens, so there is no quote to escape and no expression to smuggle.
	"""
	value = str(tax_lot or "").strip().upper()
	if not _TAX_LOT.match(value):
		raise ToolError(
			f"{tax_lot!r} is not shaped like a tax lot number. Wasco County's look like "
			"'2N11E35BA-01600' — township, range, section, quarter, lot — and this lookup "
			"accepts letters, digits, dots and hyphens only."
		)
	return f"{config['tax_lot_field']}='{value}'"


def _degrees(value, label: str, limit: float) -> float:
	number = str(value if value is not None else "").strip()
	try:
		degrees = float(number)
	except (TypeError, ValueError):
		raise ToolError(f"{label} must be a number in degrees, not {value!r}.") from None
	if degrees != degrees or degrees in (float("inf"), float("-inf")):  # NaN or infinity
		raise ToolError(f"{label} must be a real number in degrees, not {value!r}.")
	if abs(degrees) > limit:
		raise ToolError(f"{label} must be between -{limit} and {limit} degrees, not {degrees}.")
	return degrees


def _fetch(url: str, params: dict) -> dict:
	"""The one outbound request this app makes. Returns parsed JSON.

	SEPARATED OUT SO IT CAN BE REPLACED, which is what the tests do — every other
	function here is pure and testable without a network, and this one is the
	only thing between them and a county server that is nobody's dependency.
	"""
	if not HAVE_REQUESTS:
		raise ToolError(requests_sentence())
	try:
		response = requests.get(url, params=params, timeout=_TIMEOUT_SECONDS, stream=True)
	except Exception as error:  # pragma: no cover - exercised by a bench with no route out
		raise ToolError(
			f"the county GIS server could not be reached ({type(error).__name__}: {error}). "
			"Nothing was changed. A boundary can still be drawn by hand on the map, which "
			"needs no network."
		) from None
	try:
		if response.status_code != 200:
			raise ToolError(
				f"the county GIS server answered HTTP {response.status_code}. Nothing was "
				"changed. That is the county's server rather than this site — try again, or "
				"draw the boundary by hand."
			)
		body = b""
		for chunk in response.iter_content(chunk_size=65536):
			body += chunk or b""
			if len(body) > _MAX_BYTES:
				raise ToolError(
					f"the county GIS server sent more than {_MAX_BYTES // (1024 * 1024)} MB for "
					"one query. That is not one parcel — narrow the search to a tax lot number."
				)
	finally:
		try:
			response.close()
		except Exception:  # pragma: no cover
			pass

	try:
		return json.loads(body.decode("utf-8", "replace") or "{}")
	except json.JSONDecodeError as error:
		raise ToolError(
			f"the county GIS server did not answer with JSON ({error}). Nothing was changed."
		) from None


def _property(properties: dict, candidates: tuple):
	"""One value from a county's own schema, whatever case it published it in."""
	if not isinstance(properties, dict):
		return None
	lowered = {str(key).lower(): value for key, value in properties.items()}
	for candidate in candidates:
		if candidate in properties:
			return properties[candidate]
		if str(candidate).lower() in lowered:
			return lowered[str(candidate).lower()]
	return None


def _acres(value):
	try:
		acres = round(float(value), 2)
	except (TypeError, ValueError):
		return None
	return acres if acres > 0 else None


def parse_features(payload: dict, config: dict) -> tuple:
	"""`(features, warnings)` — the county's answer in this app's own vocabulary.

	PURE, AND SEPARATE FROM THE FETCH, so the shape of a real ArcGIS response is
	something the test suite pins down without a network.

	AN ArcGIS ERROR IS A 200. The service answers `{"error": {"code": 400,
	"message": "..."}}` with an HTTP 200 and a JSON content type, so a caller that
	checks only the status code reads a failure as an empty result — which on this
	path would mean "the county has never heard of your parcel" when what happened
	was a malformed query. It is checked first, by name.

	A POINT OR A LINE IS DROPPED RATHER THAN RETURNED. Some parcel layers carry
	annotation geometry alongside the lots. A boundary is an area, the three
	boundary tools refuse anything that is not, and handing the form a shape it
	will only be refused for later is worse than saying so here.
	"""
	warnings = []
	if not isinstance(payload, dict):
		raise ToolError("the county GIS server sent something that is not a GeoJSON object.")

	error = payload.get("error")
	if isinstance(error, dict):
		message = str(error.get("message") or "no message").strip()
		details = "; ".join(str(line) for line in (error.get("details") or []) if line)
		raise ToolError(
			f"the county GIS service refused the query: {message}"
			f"{' — ' + details if details else ''}. Nothing was changed."
		)

	raw = payload.get("features")
	if not isinstance(raw, list):
		raise ToolError(
			"the county GIS server did not send a GeoJSON FeatureCollection. Nothing was changed."
		)

	names = config.get("properties") or {}
	out = []
	dropped = 0
	for entry in raw:
		if not isinstance(entry, dict):
			continue
		geometry = entry.get("geometry")
		if not isinstance(geometry, dict) or geometry.get("type") not in ("Polygon", "MultiPolygon"):
			dropped += 1
			continue
		properties = entry.get("properties") or entry.get("attributes") or {}
		feature = {
			"tax_lot": _text(_property(properties, names.get("tax_lot", ()))),
			"taxpayer": _text(_property(properties, names.get("taxpayer", ()))),
			"county_acres": _acres(_property(properties, names.get("acres", ()))),
			"account": _text(_property(properties, names.get("account", ()))),
			"geometry": geometry,
			"area_computed_acres": _computed_acres(geometry),
		}
		out.append(feature)
		if len(out) >= _MAX_FEATURES:
			break

	if dropped:
		warnings.append(
			f"{dropped} shape(s) in the county's answer were not areas and were left out. A "
			"boundary has to be a Polygon or a MultiPolygon."
		)
	if len(raw) > _MAX_FEATURES:
		warnings.append(
			f"The county matched {len(raw)} shapes and the first {_MAX_FEATURES} are shown. "
			"Search by tax lot number to get one."
		)
	return out, warnings


def _text(value):
	text = str(value if value is not None else "").strip()
	return text or None


def _computed_acres(geometry: dict):
	"""What this app makes of the county's polygon, on a bench that can measure.

	Reported ALONGSIDE the county's own `CalculatedAcres` and never instead of
	it. They are two measurements — the county's on its own projected grid, this
	one spherical — and where they disagree by more than a rounding, that is
	information rather than an error to hide.
	"""
	if not geo.available():
		return None
	try:
		return geo.area_acres(geo.parse(geometry, "county boundary"))
	except Exception:
		return None


# ── the whitelisted surface: two methods ────────────────────────────────────
@frappe.whitelist()
def query_county_parcels(county=None, tax_lot=None, lat=None, lon=None):
	"""Ask a county's parcel layer for a shape, by tax lot number or by a point."""
	return speaks_frappe(_query_county_parcels, county=county, tax_lot=tax_lot, lat=lat, lon=lon)


@frappe.whitelist()
def save_boundary(doctype=None, name=None, geojson=None, dry_run=0):
	"""Save a drawn or imported boundary, through the tool that validates it."""
	return speaks_frappe(_save_boundary, doctype=doctype, name=name, geojson=geojson, dry_run=dry_run)


def _query_county_parcels(county=None, tax_lot=None, lat=None, lon=None):
	"""Ask a county's parcel layer for a shape, by tax lot number or by a point.

	WRITE PERMISSION ON Parcel IS THE GATE, and it is deliberately stricter than
	the read this method performs. The only thing an imported polygon is for is
	setting a parcel's boundary; gating on `read` would leave the site hosting an
	outbound HTTP fetch that any signed-in account — a Family Member, an Advisor
	— could drive. That is a small thing to hand out and there is no reason to.
	"""
	_named_user()
	_may_write("Parcel")

	key, config = _county(county)
	tax_lot = str(tax_lot or "").strip()
	has_point = lat not in (None, "") and lon not in (None, "")

	if tax_lot and has_point:
		raise ToolError(
			"pass either a tax lot number or a point, not both. They are two different questions "
			"and answering them together would hide which one matched."
		)

	params = {
		"outFields": "*",
		"outSR": 4326,
		"returnGeometry": "true",
		"f": "geojson",
	}
	if tax_lot:
		asked = {"tax_lot": str(tax_lot).strip().upper()}
		params["where"] = _tax_lot_clause(config, tax_lot)
	elif has_point:
		latitude = _degrees(lat, "lat", 90.0)
		longitude = _degrees(lon, "lon", 180.0)
		asked = {"lat": latitude, "lon": longitude}
		# `x` is longitude and `y` is latitude — the opposite order from every
		# other pair in this app, and the mistake this comment exists to stop.
		# Getting it round the wrong way returns the tax lot at 45.6°E,
		# -121.18°N, which is in the Southern Ocean and comes back empty rather
		# than wrong, so nothing would ever say what happened.
		params["geometry"] = json.dumps({"x": longitude, "y": latitude})
		params["geometryType"] = "esriGeometryPoint"
		params["inSR"] = 4326
		params["spatialRel"] = "esriSpatialRelIntersects"
	else:
		raise ToolError("pass a tax lot number, or a lat and lon to look under a point on the map.")

	payload = _fetch(config["url"], params)
	features, warnings = parse_features(payload, config)

	if not features:
		warnings.append(
			f"{config['label']} has no parcel matching that "
			f"{'tax lot number' if tax_lot else 'point'}. Nothing was changed."
		)

	audit.record(
		tool_name="desk:query_county_parcels",
		arguments={"county": key, **asked},
		summary=f"{config['label']}: {len(features)} parcel(s) matched",
		caller_ip=_caller_ip(),
	)

	return {
		"county": key,
		"label": config["label"],
		"query": asked,
		"count": len(features),
		"features": features,
		"warnings": warnings,
	}


def _save_boundary(doctype=None, name=None, geojson=None, dry_run=0):
	"""Save a drawn or imported boundary, through the tool that validates it.

	THIS FUNCTION DOES NOT WRITE A FIELD. It checks who is asking, checks they
	may write to this exact document, and then calls the boundary tool — which
	parses the polygon, refuses a self-intersection, compares the enclosed area
	against the recorded acreage, reports what now falls outside the shape and
	recomputes every derived field. Everything that makes a boundary trustworthy
	lives there and none of it is reimplemented here.

	`dry_run` GOES STRAIGHT THROUGH, because the tools already have it and the
	map has an obvious use for it: "what would this shape do" before "do it".
	"""
	user = _named_user()
	doctype = str(doctype or "").strip()
	name = str(name or "").strip()

	if doctype not in SAVEABLE:
		raise ToolError(
			f"{doctype or '<none>'} does not carry a boundary this way. Known: {', '.join(sorted(SAVEABLE))}."
		)
	if not name:
		raise ToolError(f"which {doctype}? A record name is required.")
	if not frappe.db.exists(doctype, name):
		raise ToolError(f"there is no {doctype} named {name!r} on this site. Nothing was changed.")

	_may_write(doctype, name)

	argument, tool = SAVEABLE[doctype]
	arguments = {
		argument: name,
		"boundary_geojson": geojson,
		"dry_run": 1 if str(dry_run) in ("1", "true", "True") else 0,
	}

	# The tools resolve a company when they are not given one, and on a
	# multi-company site that is a refusal rather than a guess. The record in
	# front of the user already knows which company it belongs to, so read it
	# from there — a person who has the form open should never be asked which of
	# their companies the parcel they are looking at is on.
	owner = frappe.db.get_value(doctype, name, "owning_entity")
	if owner:
		arguments["owning_entity"] = owner

	result = tool(arguments)
	data = dict(result.data or {})

	audit.record(
		tool_name=f"desk:{tool.__name__}",
		arguments={"doctype": doctype, "name": name, "dry_run": arguments["dry_run"]},
		summary=result.summary,
		docstatus_delta=result.docstatus_delta,
		caller_ip=_caller_ip(),
	)

	return {
		"doctype": doctype,
		"name": name,
		"user": user,
		"changed": bool(data.get("changed")),
		"dry_run": bool(data.get("dry_run")),
		"summary": result.summary,
		"warnings": list(data.get("warnings") or []),
		"area_computed_acres": data.get("area_computed_acres"),
		"boundary_centroid": data.get("boundary_centroid"),
		"data": data,
	}


def _caller_ip() -> str:
	try:
		return security.caller_ip()
	except Exception:  # pragma: no cover - a call with no request behind it
		return ""
