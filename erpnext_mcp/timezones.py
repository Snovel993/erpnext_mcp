# SPDX-License-Identifier: MIT
"""Saying which six o'clock, on a site whose columns cannot.

A worker turned a valve on at six in the morning. Every API this app publishes
answered `2026-07-24 06:00:00`, and the sentence a reader had to complete before
that meant anything — "…in which zone?" — had no answer anywhere in the payload.
This module is that answer, alongside every timestamp the new endpoints return.

WHAT IS ACTUALLY STORED, AND WHY IT IS NOT UTC. Frappe writes naive datetimes in
the SITE'S timezone: `frappe.utils.now()` is `convert_utc_to_system_timezone`
applied to the clock, and a MariaDB `DATETIME` column has nowhere to put a zone.
Every timestamp in this app — `Asset State Log.performed_at`, `Farm Task
Assignment.completed_at`, `Farm Shift.start_datetime` — is that. Storing UTC in
the new columns instead would put two zones in one table: an `open_valve` at
06:00 site-local and a `close_valve` at 13:00 UTC are the same seven o'clock, and
`get_irrigation_runtime` pairing them would report an hour of irrigation that was
sixty seconds. THE STORAGE IS LEFT ALONE ON PURPOSE. It is one consistent zone,
the framework's own, and the fix a caller actually needs is at the boundary where
the value is read — which is here.

WHAT IS RETURNED. Every timestamp keeps its existing key, unchanged and naive, so
nothing that decodes it today stops working; alongside it goes a `*_local` key
carrying the same instant as ISO 8601 with the offset spelled out —
`2026-07-24T06:00:00.000-07:00`. Additive rather than in place, because
`FrappeDate.parse` on the handset tries three naive formats and would fail the
whole row on a string it has never seen, and a client cannot be upgraded from
here.

THE MILLISECONDS ARE LOAD-BEARING AND ARE NOT DECORATION. `FrappeDate.parse`
reaches for `ISO8601DateFormatter` with `[.withInternetDateTime,
.withFractionalSeconds]`, and that option is a REQUIREMENT rather than a
tolerance: given `2026-07-24T06:00:00-07:00` it returns nil, and given
`2026-07-24T06:00:00.000-07:00` it returns the date. So every value here is
rendered to milliseconds, and the shipped app parses it without a new build.

DST IS WHY THERE IS NO SINGLE `utc_offset` IN THE RESPONSE. Pacific is -07:00 in
July and -08:00 in January, and a report covering a window that crosses the
change would have one of its two halves an hour out against a fixed offset. Each
timestamp carries the offset in force at ITS OWN instant, computed by `zoneinfo`,
and there is no site-wide constant to get wrong.

THE ZONE IS NEVER GUESSED. `site_timezone` reads System Settings and says so; a
site that has configured none falls back to UTC and the payload SAYS it fell back
(`timezone_source`), because a farm in the Pacific being told `+00:00` by a
server that assumed is the bug this module exists to end, and quietly assuming
`America/Los_Angeles` instead would be the same bug pointed at a different farm.
"""

from __future__ import annotations

import datetime
import zoneinfo

import frappe

#: Where a Frappe site records the zone its naive datetimes are written in.
SETTINGS_DOCTYPE = "System Settings"
SETTINGS_FIELD = "time_zone"

#: The zone assumed when the site names none. UTC rather than anything nearer to
#: a particular farm: an offset of zero is obviously an unset default the first
#: time somebody reads it, and a plausible-looking wrong zone is not.
FALLBACK = "UTC"

#: `2026-07-24T06:00:00.000-07:00`. The milliseconds are required by the
#: handset's parser — see the module docstring.
_PRECISION = "milliseconds"


def known(name: str) -> bool:
	"""Whether `name` is an IANA zone this machine's database has."""
	try:
		zoneinfo.ZoneInfo(str(name or ""))
	except (zoneinfo.ZoneInfoNotFoundError, ValueError, KeyError):
		return False
	return True


def site_timezone() -> tuple[str, str]:
	"""The zone this site's naive datetime columns are written in, and how it was found.

	Returned as a pair rather than a bare string so every caller reports the
	provenance without deciding to. "UTC" from a configured site and "UTC"
	because nobody configured anything are the same three letters and completely
	different facts, and only one of them is worth acting on.
	"""
	stored = ""
	try:
		stored = str(frappe.db.get_single_value(SETTINGS_DOCTYPE, SETTINGS_FIELD) or "").strip()
	except Exception:  # pragma: no cover - a site without System Settings readable
		stored = ""

	if stored and known(stored):
		return stored, f"{SETTINGS_DOCTYPE}.{SETTINGS_FIELD}"
	if stored:
		return FALLBACK, (
			f"{SETTINGS_DOCTYPE}.{SETTINGS_FIELD} is set to {stored!r}, which is not an IANA zone "
			f"this server recognises, so {FALLBACK} was assumed. Timestamps below are labelled "
			f"{FALLBACK} and may be wrong by whatever that setting meant — fix the setting."
		)
	return FALLBACK, (
		f"{SETTINGS_DOCTYPE}.{SETTINGS_FIELD} is not set on this site, so {FALLBACK} was assumed "
		"rather than guessed at. Set it to the farm's own zone (for example "
		"'America/Los_Angeles') and every timestamp below re-labels itself correctly."
	)


def resolve(args: dict, key: str = "timezone") -> str:
	"""The zone to RENDER in: the caller's if they named one, else the site's.

	An unknown zone is refused rather than silently ignored. A caller that asked
	for `America/Los_Angeles` and got UTC back because of a typo has no way to
	tell from the numbers, and the numbers are what somebody schedules irrigation
	against.
	"""
	from .errors import ToolError

	wanted = str((args or {}).get(key) or "").strip()
	if not wanted:
		return site_timezone()[0]
	if not known(wanted):
		raise ToolError(
			f"{wanted!r} is not an IANA timezone name this server recognises. Use the "
			"Area/Location form — 'America/Los_Angeles', 'America/Denver', 'UTC'. Nothing was "
			"returned rather than answering in a zone you did not ask for."
		)
	return wanted


def _naive(value) -> datetime.datetime | None:
	"""One stored column value as a naive datetime, or None if it is not one.

	A DATE — `2026-07-24`, which is what `acquired_on` and every `from_date` is —
	returns None on purpose. A date has no instant in it and no zone to convert
	between; midnight is a time this function would be inventing.
	"""
	if isinstance(value, datetime.datetime):
		return value.replace(tzinfo=None) if value.tzinfo else value
	if isinstance(value, datetime.date):
		return None

	text = str(value or "").strip()
	if not text or len(text) < 11:
		return None
	try:
		return datetime.datetime.fromisoformat(text.replace("T", " ").split("+")[0].strip())
	except ValueError:
		return None


def local(value, tz_name: str = "", stored_in: str = "") -> str | None:
	"""One stored timestamp as ISO 8601 with its offset, or None.

	`stored_in` is the zone the naive column is WRITTEN in — the site's, and the
	caller passes it rather than this function reading System Settings once per
	timestamp. `tz_name` is the zone to render in. Where the two differ the
	instant is preserved and the wall clock moves, which is the whole point: an
	office in Denver reading a Pacific farm's valve log sees eight o'clock where
	the worker at the valve saw seven, and both are the same moment.
	"""
	stamp = _naive(value)
	if stamp is None:
		return None
	try:
		source = zoneinfo.ZoneInfo(stored_in or site_timezone()[0])
		target = zoneinfo.ZoneInfo(tz_name or stored_in or FALLBACK)
	except (zoneinfo.ZoneInfoNotFoundError, ValueError, KeyError):  # pragma: no cover
		return None
	return stamp.replace(tzinfo=source).astimezone(target).isoformat(timespec=_PRECISION)


class Renderer:
	"""The site zone read once, and every timestamp in one response rendered from it.

	A response carries dozens of timestamps and the site's zone is one query.
	Holding it here is what stops `local()` reading System Settings per row, and
	gives every payload the same three keys under `block()` — so a client that
	learns to read the zone off one endpoint reads it off all of them.
	"""

	def __init__(self, args: dict | None = None, key: str = "timezone"):
		self.stored, self.source = site_timezone()
		self.display = resolve(args or {}, key)

	def __call__(self, value) -> str | None:
		return local(value, self.display, self.stored)

	def add(self, target: dict, *fields: str, suffix: str = "_local") -> dict:
		"""Write `<field>_local` beside each named field already in `target`.

		Only for fields that are PRESENT: a describer that omits a key on a
		record that has no such event must not grow a null twin, and a client
		testing `"closed_at" in row` keeps the answer it had.
		"""
		for field in fields:
			if field in target:
				target[f"{field}{suffix}"] = self(target.get(field))
		return target

	def block(self) -> dict:
		"""The three keys that say which clock the `*_local` values are on."""
		return {
			"timezone": self.display,
			"timezone_source": self.source,
			# What a naive column MEANS on this site. Reported because every
			# untouched timestamp in the same payload is in this zone, and a
			# reader comparing the two spellings of one instant needs to know
			# that rather than infer it.
			"stored_timezone": self.stored,
		}
