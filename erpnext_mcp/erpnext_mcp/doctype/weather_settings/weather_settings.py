# SPDX-License-Identifier: MIT
"""Controller for Weather Settings — the four refusals a configuration form owes.

A CONFIGURATION SURFACE IS A PLACE MISTAKES ARE CHEAP TO MAKE AND EXPENSIVE TO
FIND. Every field on this document is read by a scheduled job that runs with
nobody watching, so a value that is merely wrong — a timeout of zero, a URL with
a typo, two rows claiming different thresholds for one company — does not
announce itself. It produces a shift with no weather timeline, and the first
anybody hears about that is an auditor asking why July has a gap in it.

So four things are refused HERE, on the form, at the moment somebody types them,
rather than reported by the sweep into a log nobody reads:

  1. A NON-POSITIVE TIMEOUT OR TTL. A timeout of zero is not "no timeout" to
     `requests`; it is a connection that fails immediately, every time, silently.
     A TTL of zero would ask Open-Meteo the same question once per open shift per
     tick, which is how a free service with no API key starts returning 429.
  2. A BASE URL THAT IS NOT AN HTTP(S) URL. `services/weather.py` refuses to
     fetch one anyway — it will not hand an arbitrary scheme to an HTTP client —
     but a URL refused at fetch time is a weather timeline that quietly stops
     filling, and a URL refused at save time is a sentence on the operator's own
     screen while they still remember what they meant to type.
  3. A NEGATIVE THRESHOLD. -1131 does not engage below zero Fahrenheit and
     nothing on this farm sprays at negative wind; both are a minus key struck by
     accident, and either one makes EVERY reading a threshold crossing.
  4. TWO OVERRIDE ROWS FOR ONE COMPANY. `thresholds_for` reads the first row it
     matches, so a second row is a threshold that depends on grid order — which
     is to say a threshold nobody can defend under audit, because the answer the
     form shows and the answer the sweep used need not be the same one.

WHAT IS NOT REFUSED: an interval below the cron's own fifteen minutes. It is a
floor rather than a clock — see the field description — and a value of 5 is a
perfectly coherent way of saying "every time the sweep runs". Refusing it would
be refusing a configuration that works.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The schemes an outbound endpoint may use. `services/weather.py` enforces the
#: same list at fetch time; this is the copy that produces a readable message.
ALLOWED_SCHEMES = ("http://", "https://")

URL_FIELDS = (
	("open_meteo_base_url_current", "Current / Forecast URL"),
	("open_meteo_base_url_archive", "Archive URL"),
	("open_meteo_base_url_geocoding", "Geocoding URL"),
)

POSITIVE_FIELDS = (
	("http_timeout_seconds", "HTTP Timeout (seconds)"),
	("cache_ttl_seconds", "Cache TTL (seconds)"),
	("fetch_interval_minutes", "Fetch Interval (minutes)"),
)

THRESHOLD_FIELDS = (
	("heat_threshold_temp_f", "Heat Threshold — Temp (°F)"),
	("heat_threshold_heat_index_f", "Heat Threshold — Heat Index (°F)"),
	("wind_threshold_mph_spray_block", "Wind Threshold — Spray Block (mph)"),
)


class WeatherSettings(Document):
	def validate(self):
		self._require_positive_numbers()
		self._require_real_urls()
		self._refuse_negative_thresholds()
		self._refuse_two_rows_for_one_company()

	# ── the parts ───────────────────────────────────────────────────────────
	def _require_positive_numbers(self) -> None:
		for fieldname, label in POSITIVE_FIELDS:
			raw = self.get(fieldname)
			if raw in (None, ""):
				continue
			try:
				value = int(raw)
			except (TypeError, ValueError):
				frappe.throw(
					_("{0} must be a whole number of seconds or minutes, not {1}.").format(label, raw)
				)
			if value <= 0:
				frappe.throw(
					_(
						"{0} is {1}. A zero or negative value is not 'no limit' — a timeout of zero "
						"fails every connection immediately, and a cache lifetime of zero asks "
						"Open-Meteo the same question once per open shift per tick, which is how a "
						"free service with no API key starts refusing this site altogether."
					).format(label, value),
					title=_("Not a Usable Number"),
				)

	def _require_real_urls(self) -> None:
		for fieldname, label in URL_FIELDS:
			value = str(self.get(fieldname) or "").strip()
			if not value:
				continue
			if not value.lower().startswith(ALLOWED_SCHEMES):
				frappe.throw(
					_(
						"{0} is {1!r}, which is not an http:// or https:// URL. This app will not "
						"hand an arbitrary scheme to an HTTP client, so the fetch would be refused "
						"— and a refusal at fetch time is a weather timeline that quietly stops "
						"filling, discovered by whoever reads the shift months later."
					).format(label, value),
					title=_("Not an HTTP URL"),
				)
			self.set(fieldname, value)

	def _refuse_negative_thresholds(self) -> None:
		for fieldname, label in THRESHOLD_FIELDS:
			raw = self.get(fieldname)
			if raw in (None, ""):
				continue
			try:
				value = float(raw)
			except (TypeError, ValueError):
				frappe.throw(_("{0} must be a number, not {1}.").format(label, raw))
			if value < 0:
				frappe.throw(
					_(
						"{0} is {1}. A negative threshold makes EVERY reading a crossing, so every "
						"shift on this site would collect a Threshold Crossed event on its first "
						"fetch and the event would stop meaning anything. Almost certainly a minus "
						"key struck by accident."
					).format(label, value),
					title=_("Negative Threshold"),
				)

	def _refuse_two_rows_for_one_company(self) -> None:
		seen = set()
		for row in self.get("per_company_overrides") or []:
			company = str(row.get("company") or "").strip()
			if not company:
				frappe.throw(
					_(
						"An override row names no company. A threshold that belongs to nobody is "
						"one the sweep will never match and nobody will ever notice is unused."
					),
					title=_("Override Without a Company"),
				)
			if company in seen:
				frappe.throw(
					_(
						"{0} has two override rows. `thresholds_for` reads the first row it "
						"matches, so the second one makes this company's threshold depend on grid "
						"order — which is a threshold nobody can defend under audit, because the "
						"number on this form and the number the sweep used need not be the same. "
						"Put the values on one row."
					).format(company),
					title=_("Two Rows for One Company"),
				)
			seen.add(company)
