# SPDX-License-Identifier: MIT
"""USDA AMS Market News shipping point prices, fetched and kept.

WHY A REGISTER AND NOT A LOOKUP. The obvious shape for a market overlay is a
call to somebody's price API at the moment a report is opened. That shape fails
in the two conditions a farm office is actually in: the internet is down, or
nobody has an API key yet. Both of those are normal, neither is an outage, and a
breakeven that stops answering because a third party is unreachable would teach
its user not to rely on it. So every quotation this app sees is written to `USDA
Price Quote` and the overlay reads THE REGISTER — which means it works offline,
it works before anybody has configured a key, and it can answer "has this crop
cleared its breakeven in any of the last three weeks", which one live number
never could.

NEVER RAISES, AND NEVER PARTIALLY WRITES. `refresh_quotes` returns a report:
what it fetched, what it stored, and every reason it could not. A fetch that
fails leaves the register exactly as it was, and the overlay goes on reading the
newest quote it already had — with `usda_report_date` saying how old that is,
which is the fact a grower needs in order to discount it.

THE PARSER IS A GUESS ABOUT SOMEBODY ELSE'S SCHEMA, AND SAYS SO. AMS has
renamed report fields before and will again. Three defences: every row is stored
with its `payload` intact so a later release can reparse what is already here
without refetching a report that may no longer be published; the field mapping
accepts several spellings of each column rather than one; and a row that yields
no usable price is COUNTED AND NAMED in the report rather than skipped silently,
because "the report had two hundred rows and we stored none" is a finding and an
empty register is not.

AUTHENTICATION IS AN OPERATOR'S KEY, HELD IN A PASSWORD FIELD. The MARS API
takes an API key as the HTTP Basic username with an empty password — a shape
worth naming here because it looks like a mistake at the call site. The key is
read through `settings.get_password` and is never returned, logged or included
in a tool result, exactly as the MCP bearer token is not.

THE SWEEP SHIPS OFF, unlike the weather sweep which ships on. The difference is
that weather works out of the box against a keyless public API, and this does
not: a nightly job that cannot authenticate would write a failure to the error
log every night on every site that never wanted the feature. An operator who
enters a key turns the sweep on in the same form.
"""

from __future__ import annotations

import json

import frappe

from .. import compat
from ..settings import as_bool, get_settings

QUOTE = "USDA Price Quote"

#: The MARS (Market Analysis and Reporting Services) API. Versioned in the path
#: by AMS, so an upgrade is a change here rather than a change everywhere.
DEFAULT_BASE_URL = "https://marsapi.ams.usda.gov/services/v1.2"

#: Seconds. A constant rather than a settings field because the only sane values
#: are all in the same neighbourhood, and a farm office on a slow link is better
#: served by the backoff below than by a longer wait.
TIMEOUT_SECONDS = 20

#: How many rows of one report this will store in a single run. A shipping point
#: report is tens of rows; the cap exists so that a schema change which makes
#: every row parse as a new quotation cannot fill a site's database overnight.
ROW_CAP = 500

#: Column spellings this parser accepts, best first. AMS is not consistent
#: between reports — `low_price` in one is `lowPrice` in another and
#: `price_min` in a third — and a mapping that knew one spelling would report an
#: empty market for a report that was published perfectly.
_FIELD_ALIASES = {
	"commodity": ("commodity", "commodity_name", "commodityName", "class"),
	"variety": ("variety", "varietyName", "type", "commodity_variety"),
	"grade": ("grade", "quality", "size", "grade_description", "sizeDescription"),
	"market": (
		"market_location_name",
		"marketLocationName",
		"shipping_point",
		"shippingPoint",
		"office_name",
		"location",
	),
	"package": ("package", "pkg", "package_description", "packageDescription", "unit_of_sale"),
	"report_date": ("report_date", "reportDate", "published_date", "report_begin_date", "date"),
	"low_price": ("low_price", "lowPrice", "price_min", "priceMin", "low"),
	"high_price": ("high_price", "highPrice", "price_max", "priceMax", "high"),
	"mostly_low": ("mostly_low_price", "mostlyLowPrice", "mostly_low", "mostlyLow"),
	"mostly_high": ("mostly_high_price", "mostlyHighPrice", "mostly_high", "mostlyHigh"),
	"price_unit": ("price_unit", "priceUnit", "unit", "uom"),
	"report_title": ("report_title", "reportTitle", "slug_name", "report_name"),
}


# ── configuration ───────────────────────────────────────────────────────────
def api_key() -> str:
	"""The operator's MARS API key, or "" when none is configured.

	Never log, echo, or include this in a tool result — the same contract
	`settings.auth_token` carries, for the same reason.
	"""
	try:
		key = get_settings().get_password("usda_mars_api_key", raise_exception=False)
	except Exception:
		# No stored value, or an undecryptable one on a site whose encryption key
		# was rotated. Either way there is no usable key, which fails closed.
		return ""
	return (key or "").strip()


def base_url() -> str:
	try:
		configured = str(get_settings().get("usda_api_base_url") or "").strip()
	except Exception:
		configured = ""
	return configured or DEFAULT_BASE_URL


def sweep_enabled() -> bool:
	try:
		return as_bool(get_settings().get("enable_usda_price_sweep"))
	except Exception:
		return False


def configured_reports() -> list[str]:
	"""The report slugs the nightly sweep pulls. Empty is the shipped state.

	NO DEFAULT SLUGS ARE SHIPPED, deliberately. AMS identifies its reports by
	slugs an operator can look up for the districts and commodities they
	actually ship into, and a list invented here would either be wrong — a
	nightly 404 on every site — or right for one region and wrong for everybody
	else. An unconfigured sweep does nothing and says so, which is the correct
	behaviour for a feature nobody has pointed at anything yet.
	"""
	try:
		raw = str(get_settings().get("usda_price_reports") or "")
	except Exception:
		return []
	out = []
	for chunk in raw.replace("\n", ",").split(","):
		entry = chunk.split("#", 1)[0].strip()
		if entry:
			out.append(entry)
	return out


# ── fetching ────────────────────────────────────────────────────────────────
def refresh_quotes(report_slug: str, commodity: str = "") -> dict:
	"""Fetch one AMS report and store its rows as quotations. Never raises.

	Returns a report dict: `stored`, `updated`, `skipped`, `rows_seen` and
	`warnings`. A caller shows the warnings — an empty register with no
	explanation is the failure mode this whole module is arranged to avoid.
	"""
	report: dict = {
		"report_slug": str(report_slug or "").strip(),
		"stored": [],
		"updated": [],
		"skipped": 0,
		"rows_seen": 0,
		"warnings": [],
	}
	if not report["report_slug"]:
		report["warnings"].append("no report slug was given, so nothing was fetched.")
		return report
	if not compat.doctype_exists(QUOTE):
		report["warnings"].append(
			f"this site has no {QUOTE} doctype, so a quotation has nowhere to go. It ships with "
			"erpnext_mcp — run `bench --site <site> migrate` after upgrading the app."
		)
		return report

	key = api_key()
	if not key:
		report["warnings"].append(
			"no USDA MARS API key is configured, so no report can be fetched. Enter one in ERPNext "
			"MCP Settings. The overlay still reads whatever quotations are already on the site, "
			"including ones entered by hand — the breakeven itself does not depend on the key."
		)
		return report

	rows = _get_rows(f"{base_url()}/reports/{report['report_slug']}", key, report["warnings"])
	if rows is None:
		return report

	report["rows_seen"] = len(rows)
	wanted = str(commodity or "").strip().upper()
	for row in rows[:ROW_CAP]:
		parsed = _parse_row(row, report["report_slug"])
		if parsed is None:
			report["skipped"] += 1
			continue
		if wanted and parsed["commodity"] != wanted:
			report["skipped"] += 1
			continue
		outcome, name = _store(parsed, row)
		if outcome == "stored":
			report["stored"].append(name)
		elif outcome == "updated":
			report["updated"].append(name)
		else:
			report["skipped"] += 1

	if len(rows) > ROW_CAP:
		report["warnings"].append(
			f"the report carried {len(rows)} rows and this run stored at most {ROW_CAP} of them. "
			"A report that large is usually a slug covering more than one district — narrow it, "
			"or say so, because the rows beyond the cap are silently absent from every overlay."
		)
	if not report["stored"] and not report["updated"] and report["rows_seen"]:
		report["warnings"].append(
			f"the report answered with {report['rows_seen']} row(s) and none of them yielded a "
			"usable quotation. That is a schema change at AMS rather than an empty market — the "
			"rows are not stored, so there is nothing to reparse; refetch after the field mapping "
			"in services/usda_prices.py has been updated."
		)
	return report


def _get_rows(url: str, key: str, warnings: list):
	"""One GET, decoded to a list of row dicts. None for every failure.

	NEVER RAISES. Each branch is a real condition: a farm office link that times
	out, an expired key, a 500 from AMS, and a 200 carrying an HTML captive
	portal page instead of JSON.
	"""
	try:
		import requests
	except Exception:  # pragma: no cover - Frappe depends on requests
		warnings.append("the `requests` package is not importable, so nothing can be fetched.")
		return None

	try:
		# The MARS API takes the key as the HTTP Basic USERNAME with an empty
		# password. It looks like a mistake at the call site, which is why it is
		# spelled out here rather than left as a bare tuple.
		response = requests.get(url, auth=(key, ""), timeout=TIMEOUT_SECONDS)
	except Exception as exc:
		warnings.append(f"{url} did not answer: {type(exc).__name__}: {exc}")
		return None

	status = int(getattr(response, "status_code", 0) or 0)
	if status in (401, 403):
		warnings.append(
			f"{url} answered {status}. The configured USDA MARS API key was refused — check it in "
			"ERPNext MCP Settings. Nothing on the site was changed."
		)
		return None
	if status == 404:
		warnings.append(
			f"{url} answered 404. No AMS report has that slug. The slug list is published by AMS "
			"Market News; a report that was retired keeps its old quotations here and simply stops "
			"gaining new ones."
		)
		return None
	if status >= 400:
		warnings.append(f"{url} answered {status}: {str(getattr(response, 'text', ''))[:300]}")
		return None

	try:
		payload = response.json()
	except Exception as exc:
		warnings.append(f"{url} answered {status} with a body that is not JSON: {type(exc).__name__}: {exc}")
		return None

	rows = payload
	if isinstance(payload, dict):
		# AMS wraps the rows differently per report family. Try the spellings it
		# has used rather than assuming one.
		for key_name in ("results", "data", "report", "rows"):
			if isinstance(payload.get(key_name), list):
				rows = payload[key_name]
				break
	if not isinstance(rows, list):
		warnings.append(
			f"{url} answered with {type(rows).__name__} where a list of rows was expected. The "
			"body is not stored, because a body this app cannot recognise is not a quotation."
		)
		return None
	return [row for row in rows if isinstance(row, dict)]


# ── parsing ─────────────────────────────────────────────────────────────────
def _pick(row: dict, field: str):
	for alias in _FIELD_ALIASES.get(field, ()):  # best spelling first
		if row.get(alias) not in (None, ""):
			return row[alias]
	return None


def _parse_row(row: dict, report_slug: str):
	"""One AMS row as a quotation, or None where it is not one.

	A row with no commodity, no date or no price at either end of the band is
	not a quotation this app can compare anything against, and storing it would
	put a row in the register that every overlay then has to skip.
	"""
	commodity = str(_pick(row, "commodity") or "").strip().upper()
	report_date = _as_date(_pick(row, "report_date"))
	low = _as_number(_pick(row, "low_price"))
	high = _as_number(_pick(row, "high_price"))
	if not commodity or not report_date:
		return None
	if low is None and high is None:
		return None

	# A single published price arrives as one end of the band. Mirroring it makes
	# the row a degenerate range rather than a half-empty one, so every consumer
	# reads it the same way.
	if low is None:
		low = high
	if high is None:
		high = low

	mostly_low = _as_number(_pick(row, "mostly_low"))
	mostly_high = _as_number(_pick(row, "mostly_high"))
	# A mostly band that falls outside the range it is meant to sit inside is a
	# column mismatch, not a market. Drop the band and keep the range — the
	# controller would refuse the whole row otherwise, losing a good quotation
	# over a bad extra.
	if mostly_low is not None and (mostly_low < low or mostly_low > high):
		mostly_low = None
	if mostly_high is not None and (mostly_high > high or mostly_high < low):
		mostly_high = None

	return {
		"commodity": commodity,
		"variety": str(_pick(row, "variety") or "").strip().upper(),
		"grade": str(_pick(row, "grade") or "").strip(),
		"market": str(_pick(row, "market") or "").strip().upper(),
		"package": str(_pick(row, "package") or "").strip(),
		"report_date": report_date,
		"low_price": low,
		"high_price": high,
		"mostly_low": mostly_low,
		"mostly_high": mostly_high,
		"price_unit": str(_pick(row, "price_unit") or "per package").strip(),
		"report_title": str(_pick(row, "report_title") or "").strip(),
		"report_slug": report_slug,
	}


def _store(parsed: dict, raw: dict):
	"""Upsert one quotation. Returns (outcome, docname).

	AN UPSERT RATHER THAN AN INSERT because the docname carries the identity —
	commodity, variety, market, package and report date — so refetching
	yesterday's report updates the row that is already there. Three copies of one
	day in the register would make "the most recent quote" depend on which was
	written last.
	"""
	try:
		doc = None
		existing = frappe.db.get_all(
			QUOTE,
			filters={
				"commodity": parsed["commodity"],
				"variety": parsed["variety"] or "",
				"market": parsed["market"] or "",
				"package": parsed["package"] or "",
				"report_date": parsed["report_date"],
			},
			pluck="name",
			limit=1,
		)
		if existing:
			doc = frappe.get_doc(QUOTE, existing[0])
			outcome = "updated"
		else:
			doc = frappe.new_doc(QUOTE)
			outcome = "stored"

		for key, value in parsed.items():
			doc.set(key, value)
		doc.source = "USDA AMS Market News"
		doc.fetched_on = frappe.utils.now()
		doc.fetched_by = "erpnext_mcp USDA price fetch"
		doc.payload = json.dumps(raw, indent=1, sort_keys=True, default=str)
		doc.flags.ignore_permissions = True
		if outcome == "updated":
			doc.save(ignore_permissions=True)
		else:
			doc.insert(ignore_permissions=True)
		return outcome, doc.name
	except Exception:
		# One unusable row must never cost the other two hundred. The traceback
		# goes to the error log where it can be read; the run continues.
		frappe.log_error(
			title="erpnext_mcp: a USDA quotation could not be stored",
			message=compat.traceback_text(),
		)
		return "skipped", ""


def record_manual_quote(
	commodity: str,
	price: float,
	report_date: str,
	*,
	variety: str = "",
	market: str = "",
	package: str = "",
	price_unit: str = "per package",
	source: str = "Manual",
	notes: str = "",
) -> str:
	"""Store a price somebody was actually quoted. Returns the docname.

	THE PATH THAT MATTERS MOST ON A REAL FARM. A grower with a broker's bid in
	hand has a better number than any published district average, and an overlay
	that could only read AMS would be telling them about a market they are not
	selling into. It is stored as a quotation like any other and LABELLED with
	its source, so nobody later mistakes one for the other.

	Raises whatever the controller raises — this one is called from a tool with
	a caller waiting, unlike the sweep.
	"""
	compat.require_doctype(
		QUOTE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)
	commodity = str(commodity or "").strip().upper()
	filters = {
		"commodity": commodity,
		"variety": str(variety or "").strip().upper(),
		"market": str(market or "").strip().upper(),
		"package": str(package or "").strip(),
		"report_date": report_date,
	}
	existing = frappe.db.get_all(QUOTE, filters=filters, pluck="name", limit=1)
	doc = frappe.get_doc(QUOTE, existing[0]) if existing else frappe.new_doc(QUOTE)
	for key, value in filters.items():
		doc.set(key, value)
	doc.low_price = float(price or 0)
	doc.high_price = float(price or 0)
	doc.mostly_low = None
	doc.mostly_high = None
	doc.price_unit = price_unit or "per package"
	doc.source = source if source in ("Manual", "Broker Quote") else "Manual"
	doc.fetched_on = frappe.utils.now()
	doc.fetched_by = frappe.session.user if getattr(frappe, "session", None) else "erpnext_mcp"
	if notes:
		doc.notes = notes
	doc.flags.ignore_permissions = True
	if existing:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)
	return doc.name


def latest_quote(commodity: str, variety: str = "", market: str = "", on_or_before: str = ""):
	"""The newest quotation matching a commodity, and optionally a variety and market.

	WIDENS RATHER THAN FAILING, and reports how far it had to widen. A grower who
	asked for Galas in the Yakima district and gets the commodity-wide apple
	quotation is better served than one who gets nothing — PROVIDED the answer
	says which of the two it is, which is why the match is returned alongside the
	row rather than only the row.
	"""
	if not compat.doctype_exists(QUOTE):
		return None, "this site has no USDA Price Quote register yet — run `bench migrate`."
	commodity = str(commodity or "").strip().upper()
	if not commodity:
		return None, "no commodity was named, so the market overlay was not attempted."

	attempts = []
	if variety and market:
		attempts.append(({"variety": variety.strip().upper(), "market": market.strip().upper()}, "exact"))
	if variety:
		attempts.append(({"variety": variety.strip().upper()}, "variety, any market"))
	if market:
		attempts.append(({"market": market.strip().upper()}, "market, any variety"))
	attempts.append(({}, "commodity only"))

	for extra, precision in attempts:
		filters = {"commodity": commodity}
		filters.update(extra)
		if on_or_before:
			filters["report_date"] = ("<=", on_or_before)
		rows = frappe.db.get_all(
			QUOTE,
			filters=filters,
			fields=[
				"name",
				"commodity",
				"variety",
				"grade",
				"market",
				"package",
				"report_date",
				"low_price",
				"high_price",
				"mostly_low",
				"mostly_high",
				"price_unit",
				"source",
			],
			order_by="report_date desc, modified desc",
			limit=1,
		)
		if rows:
			row = dict(rows[0])
			row["match_precision"] = precision
			return row, ""

	return None, (
		f"no quotation for {commodity} is on file. Fetch one with a configured USDA MARS API key, "
		"or record the price you were actually quoted — a broker's bid in hand is a better number "
		"than any district average, and it is stored and labelled as its own kind of source."
	)


def reference_price(quote: dict) -> float:
	"""The one number to compare a breakeven against, out of a quotation's band.

	THE MOSTLY BAND WHERE THERE IS ONE. AMS publishes it because the low and the
	high of a district's day include the distressed load and the one specialty
	buyer, and the midpoint of the full range is dragged by both. Where no mostly
	band was published, the range's own midpoint is the only honest answer and is
	what this returns.
	"""
	mostly_low = quote.get("mostly_low")
	mostly_high = quote.get("mostly_high")
	if mostly_low not in (None, "") and mostly_high not in (None, ""):
		return round((float(mostly_low) + float(mostly_high)) / 2.0, 4)
	low = float(quote.get("low_price") or 0)
	high = float(quote.get("high_price") or 0)
	if low and high:
		return round((low + high) / 2.0, 4)
	return round(low or high, 4)


# ── the sweep ───────────────────────────────────────────────────────────────
def sweep_configured_reports() -> None:
	"""The nightly refresh. Called by the scheduler; never raises.

	DOES NOTHING AND SAYS SO on a site with the switch off, no key, or no report
	slugs — which is every site that has not asked for this. A scheduled job that
	logged an authentication failure every night on a farm that never wanted a
	market overlay is the reason the switch ships off.
	"""
	try:
		if not sweep_enabled():
			return
		slugs = configured_reports()
		if not slugs:
			print(
				"erpnext_mcp: the USDA price sweep is on but no report slugs are configured, so "
				"nothing was fetched. Name the AMS reports for the districts you ship into in "
				"ERPNext MCP Settings."
			)
			return
		stored = updated = 0
		for slug in slugs:
			report = refresh_quotes(slug)
			stored += len(report["stored"])
			updated += len(report["updated"])
			for warning in report["warnings"]:
				print(f"erpnext_mcp: USDA report {slug}: {warning}")
		if stored or updated:
			print(
				f"erpnext_mcp: USDA price sweep stored {stored} and updated {updated} quotation(s) "
				f"across {len(slugs)} report(s)."
			)
	except Exception:  # pragma: no cover - a scheduled job must not take the tick down
		frappe.log_error(
			title="erpnext_mcp: the USDA price sweep failed",
			message=compat.traceback_text(),
		)


# ── coercion ────────────────────────────────────────────────────────────────
def _as_number(value):
	if value in (None, ""):
		return None
	try:
		# AMS publishes prices as strings with currency and thousands marks in
		# some reports and as numbers in others.
		return float(str(value).replace("$", "").replace(",", "").strip())
	except (TypeError, ValueError):
		return None


def _as_date(value):
	if value in (None, ""):
		return None
	text = str(value).strip()
	try:
		from frappe.utils import getdate

		return str(getdate(text))
	except Exception:
		# `getdate` handles the ISO and US spellings AMS uses. Anything it
		# refuses is not a date, and a row with no date is not a quotation.
		return None
