# SPDX-License-Identifier: MIT
"""Eight tools over the two company-wide wage tables, and the reads payroll makes.

v0.61.0. `wage_defaults.py` holds the ORDER — structure rate, then company rate,
then refuse — and holds it as pure functions so it can be tested without a bench.
This module is the database half: the four tools over Piecework Rate, the four
over Position Wage Default, and the two loaders (`rates_for_company`,
`defaults_for_company`) that `tools/payroll.py` calls on every run.

FOUR TOOLS EACH, AND THERE IS NO DELETE. A rate that paid a period is the record
of what that period paid, and a table that forgets it cannot answer a wage claim.
`update_piecework_rate` can set `is_active` to 0, which takes a row out of every
future lookup and leaves it readable — the same posture `remove_authorized_signer`
takes for the same reason.

CLOSING A RATE AND REPLACING IT ARE DIFFERENT ACTIONS AND THE TOOLS KEEP THEM
APART. Raising a rate is `create_piecework_rate` with a new `effective_from`;
`select_effective` gives the later start date the period from that date onwards,
so the old row goes on paying the periods it paid. Editing the live row instead
would restate what last month cost, which is the thing a rate table with dates on
it exists to prevent — so `update_piecework_rate` says so on every result where
the caller moved `rate_per_unit` on a row that has already been in force.
"""

from __future__ import annotations

import frappe
from frappe.utils import today

from .. import compat, wage_defaults
from ..args import as_bool, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

PIECEWORK_RATE = wage_defaults.PIECEWORK_RATE
POSITION_WAGE_DEFAULT = wage_defaults.POSITION_WAGE_DEFAULT
DESIGNATION = "Designation"

_MIGRATE_HINT = "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app."

#: Every column a lookup or a description reads. Named once so the loaders below,
#: which run inside a payroll run, cannot drift from what `_describe` expects.
_RATE_FIELDS = (
	"name",
	"company",
	"activity",
	"rate_per_unit",
	"effective_from",
	"effective_to",
	"description",
	"is_active",
	"modified",
)
_DEFAULT_FIELDS = (
	"name",
	"company",
	"designation",
	"hourly_rate",
	"effective_from",
	"effective_to",
	"notes",
	"is_active",
	"modified",
)


def _require_rates() -> None:
	compat.require_doctype(PIECEWORK_RATE, _MIGRATE_HINT)


def _require_defaults() -> None:
	compat.require_doctype(POSITION_WAGE_DEFAULT, _MIGRATE_HINT)


def _company_filter(args: dict) -> str:
	"""A Company to filter a READ on — only where the caller actually named one.

	The same distinction `tools/feeds.py` draws: `resolve_company("")` infers the
	single company on a single-company site, which is right for a create and
	wrong for a filter.
	"""
	named = as_str(args, "company")
	return resolve_company(named) or "" if named else ""


def _num(value, default: float = 0.0) -> float:
	if value is None or value == "":
		return default
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _rate_arg(args: dict, key: str, required: bool = False):
	"""A currency argument, refused if it is not a number. None where absent.

	None rather than 0.0 for an absent value, because `update_*` has to tell
	"leave the rate alone" apart from "set the rate to nothing".
	"""
	value = args.get(key)
	if value is None or value == "":
		if required:
			raise ToolError(f"{key} is required. Nothing was written.")
		return None
	try:
		return float(value)
	except (TypeError, ValueError):
		raise ToolError(f"{key} must be a number, got {value!r}. Nothing was written.") from None


def _refuse(errors: list) -> None:
	if errors:
		raise ToolError("; ".join(errors) + ". Nothing was written.")


# ── the loaders payroll uses ──────────────────────────────────────────────


def rates_for_company(company: str) -> list:
	"""Every Piecework Rate row for one company, active ones only.

	Returned unfiltered by date: choosing the row in force is
	`wage_defaults.select_effective`'s job, and it needs the whole set to tell a
	superseded row from a missing one. A site that has not migrated yet gets an
	empty list rather than an exception — the fallback is then simply
	unreachable, which is exactly how payroll behaved before this release.
	"""
	if not compat.doctype_exists(PIECEWORK_RATE):
		return []
	return frappe.db.get_all(
		PIECEWORK_RATE,
		filters={"company": company, "is_active": 1},
		fields=list(_RATE_FIELDS),
		limit_page_length=0,
	)


def defaults_for_company(company: str) -> list:
	"""Every Position Wage Default row for one company, active ones only."""
	if not compat.doctype_exists(POSITION_WAGE_DEFAULT):
		return []
	return frappe.db.get_all(
		POSITION_WAGE_DEFAULT,
		filters={"company": company, "is_active": 1},
		fields=list(_DEFAULT_FIELDS),
		limit_page_length=0,
	)


def default_hourly_rate(company: str, designation: str, on_date=None):
	"""The Position Wage Default row for one job title, or None.

	What `create_salary_structure` calls. A convenience over the loader rather
	than a second query path, so the create-time default and
	`get_position_wage_default` can never disagree about which row is in force.
	"""
	if not company or not designation:
		return None
	return wage_defaults.position_hourly_rate(defaults_for_company(company), designation, on_date)


# ── describing a row ──────────────────────────────────────────────────────


def _describe_rate(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"company": row.get("company"),
		"activity": wage_defaults.normalize_activity(row.get("activity")),
		"activity_as_entered": row.get("activity"),
		"rate_per_unit": _num(row.get("rate_per_unit")),
		"effective_from": str(row.get("effective_from") or "") or None,
		"effective_to": str(row.get("effective_to") or "") or None,
		"description": row.get("description") or None,
		"is_active": bool(compat.checked(row.get("is_active"))),
	}


def _describe_default(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"company": row.get("company"),
		"designation": row.get("designation"),
		"hourly_rate": _num(row.get("hourly_rate")),
		"effective_from": str(row.get("effective_from") or "") or None,
		"effective_to": str(row.get("effective_to") or "") or None,
		"notes": row.get("notes") or None,
		"is_active": bool(compat.checked(row.get("is_active"))),
	}


def _rate_row_or_refuse(args: dict) -> dict:
	name = as_str(args, "name") or as_str(args, "piecework_rate", required=True)
	row = frappe.db.get_value(PIECEWORK_RATE, name, list(_RATE_FIELDS), as_dict=True)
	if not row:
		raise ToolError(
			f"no Piecework Rate called {name!r} on this site. list_piecework_rates has the register."
		)
	return dict(row)


def _default_row_or_refuse(args: dict) -> dict:
	name = as_str(args, "name") or as_str(args, "position_wage_default", required=True)
	row = frappe.db.get_value(POSITION_WAGE_DEFAULT, name, list(_DEFAULT_FIELDS), as_dict=True)
	if not row:
		raise ToolError(
			f"no Position Wage Default called {name!r} on this site. "
			"list_position_wage_defaults has the register."
		)
	return dict(row)


def _designation_or_refuse(designation: str) -> str:
	"""A Designation docname, refused by name where the site has no such title.

	A Link field would refuse this at insert time with Frappe's own message; this
	is the same refusal one layer earlier, with the register named so the caller
	knows where to add the missing title.
	"""
	if not compat.doctype_exists(DESIGNATION):
		return designation
	if frappe.db.exists(DESIGNATION, designation):
		return designation
	found = frappe.db.get_all(
		DESIGNATION, filters={"designation_name": designation}, pluck="name", limit=2
	)
	if len(found) == 1:
		return str(found[0])
	raise ToolError(
		f"no Designation called {designation!r} on this site. A wage default is keyed by the "
		"site's own job titles so it can be matched against Employee.designation — create the "
		"Designation first. Nothing was written."
	)


# ── 1. create_piecework_rate ──────────────────────────────────────────────


def create_piecework_rate(args: dict) -> ToolResult:
	"""Set what one unit of one piecework activity pays at one company, from one date."""
	_require_rates()
	company = resolve_company(as_str(args, "company"), required=True)
	activity = as_str(args, "activity", required=True)
	candidate = {
		"company": company,
		"activity": activity,
		"rate_per_unit": _rate_arg(args, "rate_per_unit", required=True),
		"effective_from": as_date(args, "effective_from") or today(),
		"effective_to": as_date(args, "effective_to"),
	}
	_refuse(wage_defaults.validate_piecework_rate(candidate))

	doc = frappe.new_doc(PIECEWORK_RATE)
	doc.company = company
	doc.activity = activity
	doc.rate_per_unit = candidate["rate_per_unit"]
	doc.effective_from = candidate["effective_from"]
	doc.effective_to = candidate["effective_to"] or None
	doc.description = as_str(args, "description") or None
	active = as_bool(args, "is_active", default=True)
	doc.is_active = 1 if active else 0

	try:
		doc.insert(ignore_permissions=True)
	except Exception as exc:
		raise ToolError(f"{exc}") from None

	normalised = wage_defaults.normalize_activity(activity)
	superseded = [
		row["name"]
		for row in rates_for_company(company)
		if row["name"] != doc.name
		and wage_defaults.normalize_activity(row.get("activity")) == normalised
		and str(row.get("effective_from") or "") < str(doc.effective_from)
		and not row.get("effective_to")
	]
	return ToolResult(
		data={
			**_describe_rate(doc.as_dict()),
			"supersedes": superseded,
			"note": (
				(
					f"From {doc.effective_from} this rate pays {normalised}, in place of "
					f"{', '.join(superseded)} — which stays readable and goes on paying the "
					"periods it already paid. That is what the dates are for. "
					if superseded
					else ""
				)
				+ "Payroll reads this row only for workers whose Farm Salary Structure has "
				"base_rate 0 — a rate negotiated with one person is the more specific record "
				"and still wins."
			),
			"next": ["list_piecework_rates", "preview_payroll_for_period"],
		},
		summary=f"piecework rate {doc.name}: {normalised} at {doc.rate_per_unit} per unit for {company}",
		docstatus_delta="none → 0 (created)",
	)


# ── 2. list_piecework_rates ───────────────────────────────────────────────


def list_piecework_rates(args: dict) -> ToolResult:
	"""The register: every piecework rate, and which of them is in force."""
	_require_rates()
	filters: dict = {}
	company = _company_filter(args)
	if company:
		filters["company"] = company
	is_active = as_bool(args, "is_active")
	if is_active is not None:
		filters["is_active"] = 1 if is_active else 0

	rows = frappe.db.get_all(
		PIECEWORK_RATE,
		filters=filters,
		fields=list(_RATE_FIELDS),
		order_by="company asc, activity asc, effective_from desc",
		limit_page_length=as_limit(args),
	)
	wanted = wage_defaults.normalize_activity(as_str(args, "activity"))
	if wanted:
		rows = [row for row in rows if wage_defaults.normalize_activity(row.get("activity")) == wanted]

	on_date = as_date(args, "on_date")
	described = [_describe_rate(row) for row in rows]

	# Which row each (company, activity) pair would actually be paid at. The
	# register is nearly useless without it: five rows for one activity is the
	# normal state of a rate table with a history, and the only question anybody
	# opening it has is which one is live.
	in_force = {}
	for row in rows:
		key = (row.get("company"), wage_defaults.normalize_activity(row.get("activity")))
		in_force.setdefault(key, []).append(row)
	effective = []
	for key in sorted(in_force, key=lambda pair: (str(pair[0]), pair[1])):
		chosen = wage_defaults.select_effective(in_force[key], on_date)
		if chosen:
			effective.append(_describe_rate(chosen))

	return ToolResult(
		data={
			"count": len(described),
			"as_of": on_date or today(),
			"rates": described,
			"in_force": effective,
			"note": (
				"`in_force` is the one row per (company, activity) a payroll run dated `as_of` "
				"would read — the latest effective_from that covers the date. Everything else in "
				"`rates` is history, and it is kept because a period already paid must still be "
				"explicable."
			),
		},
		summary=f"{len(described)} piecework rate(s), {len(effective)} in force",
	)


# ── 3. get_piecework_rate ─────────────────────────────────────────────────


def get_piecework_rate(args: dict) -> ToolResult:
	"""One piecework rate in full, and whether it is the row payroll would read."""
	_require_rates()
	row = _rate_row_or_refuse(args)
	described = _describe_rate(row)
	on_date = as_date(args, "on_date")
	siblings = [
		other
		for other in rates_for_company(row.get("company"))
		if wage_defaults.normalize_activity(other.get("activity")) == described["activity"]
	]
	chosen = wage_defaults.select_effective(siblings, on_date)
	is_live = bool(chosen and chosen.get("name") == row.get("name"))
	return ToolResult(
		data={
			**described,
			"as_of": on_date or today(),
			"in_force": is_live,
			"in_force_instead": (chosen or {}).get("name") if not is_live else None,
			"note": (
				"This is the rate a payroll run dated `as_of` would read for this activity."
				if is_live
				else (
					f"This row is NOT what a run dated {on_date or today()} would read — "
					f"{(chosen or {}).get('name') or 'no row at all'} covers that date. The row is "
					"kept because it explains the periods it did pay."
				)
			),
		},
		summary=f"piecework rate {row.get('name')} ({described['activity']} at {described['rate_per_unit']})",
	)


# ── 4. update_piecework_rate ──────────────────────────────────────────────


def update_piecework_rate(args: dict) -> ToolResult:
	"""Edit a piecework rate, or take it out of service by clearing is_active.

	`company` and `activity` are NOT editable here. They are the pair every
	lookup resolves a rate by, and moving one would silently restate which
	workers a period was paid under — the shape `update_model` refuses for the
	same reason. A rate for a different activity is a different row.
	"""
	_require_rates()
	row = _rate_row_or_refuse(args)
	name = row["name"]

	for locked in ("company", "activity"):
		if as_str(args, locked):
			raise ToolError(
				f"{locked} cannot be changed on an existing Piecework Rate — it is half of the "
				"pair payroll resolves a rate by, and moving it would restate which workers a "
				"period was paid under. Create a new rate instead. Nothing was changed."
			)

	doc = frappe.get_doc(PIECEWORK_RATE, name)
	changed = []
	was_rate = _num(row.get("rate_per_unit"))

	new_rate = _rate_arg(args, "rate_per_unit")
	if new_rate is not None and new_rate != was_rate:
		doc.rate_per_unit = new_rate
		changed.append("rate_per_unit")
	for key in ("effective_from", "effective_to"):
		if key in args:
			value = as_date(args, key)
			if str(value or "") != str(row.get(key) or ""):
				setattr(doc, key, value or None)
				changed.append(key)
	description = as_str(args, "description")
	if description and description != (row.get("description") or ""):
		doc.description = description
		changed.append("description")
	is_active = as_bool(args, "is_active")
	if is_active is not None and bool(is_active) != bool(compat.checked(row.get("is_active"))):
		doc.is_active = 1 if is_active else 0
		changed.append("is_active")

	if not changed:
		return ToolResult(
			data={**_describe_rate(doc.as_dict()), "changed": [], "note": "Nothing to change."},
			summary=f"piecework rate {name} unchanged",
		)

	_refuse(wage_defaults.validate_piecework_rate(doc.as_dict()))
	try:
		doc.save(ignore_permissions=True)
	except Exception as exc:
		raise ToolError(f"{exc}") from None

	started = str(row.get("effective_from") or "")
	already_in_force = bool(started and started <= str(today()))
	return ToolResult(
		data={
			**_describe_rate(doc.as_dict()),
			"changed": changed,
			"note": (
				(
					f"WARNING: rate_per_unit moved from {was_rate} to {doc.rate_per_unit} on a row "
					f"that has been in force since {started}. Any payroll re-run over a period "
					"inside that window will now compute a different number than it did. A RAISE "
					"is a NEW row with a later effective_from — create_piecework_rate — which "
					"leaves what was already paid explicable. Use this edit for a rate that was "
					"typed wrong. "
					if "rate_per_unit" in changed and already_in_force
					else ""
				)
				+ (
					"is_active is now 0, so no future run reads this row. It stays readable. "
					if "is_active" in changed and not doc.is_active
					else ""
				)
				+ "Payroll reads this row only for workers whose salary structure has base_rate 0."
			),
		},
		summary=f"updated piecework rate {name} ({', '.join(changed)})",
		docstatus_delta="0 → 0 (amended draft)",
	)


# ── 5. create_position_wage_default ───────────────────────────────────────


def create_position_wage_default(args: dict) -> ToolResult:
	"""Set the default hourly rate for one job title at one company, from one date."""
	_require_defaults()
	company = resolve_company(as_str(args, "company"), required=True)
	designation = _designation_or_refuse(as_str(args, "designation", required=True))
	candidate = {
		"company": company,
		"designation": designation,
		"hourly_rate": _rate_arg(args, "hourly_rate", required=True),
		"effective_from": as_date(args, "effective_from") or today(),
		"effective_to": as_date(args, "effective_to"),
	}
	_refuse(wage_defaults.validate_position_wage_default(candidate))

	doc = frappe.new_doc(POSITION_WAGE_DEFAULT)
	doc.company = company
	doc.designation = designation
	doc.hourly_rate = candidate["hourly_rate"]
	doc.effective_from = candidate["effective_from"]
	doc.effective_to = candidate["effective_to"] or None
	doc.notes = as_str(args, "notes") or None
	active = as_bool(args, "is_active", default=True)
	doc.is_active = 1 if active else 0

	try:
		doc.insert(ignore_permissions=True)
	except Exception as exc:
		raise ToolError(f"{exc}") from None

	return ToolResult(
		data={
			**_describe_default(doc.as_dict()),
			"note": (
				f"create_salary_structure now starts a new {designation} at "
				f"{doc.hourly_rate} an hour where the caller names no rate. It is a DEFAULT and "
				"nothing more: structures that already exist are untouched, and editing this row "
				"later will not change what any of them pays. An hourly wage is what somebody was "
				"hired at, and a table that could restate it retroactively would be a table that "
				"rewrites what a wage claim asks about."
			),
			"next": ["list_position_wage_defaults", "create_salary_structure"],
		},
		summary=f"position wage default {doc.name}: {designation} at {doc.hourly_rate}/hr for {company}",
		docstatus_delta="none → 0 (created)",
	)


# ── 6. list_position_wage_defaults ────────────────────────────────────────


def list_position_wage_defaults(args: dict) -> ToolResult:
	"""The register: every position wage default, and which of them is in force."""
	_require_defaults()
	filters: dict = {}
	company = _company_filter(args)
	if company:
		filters["company"] = company
	designation = as_str(args, "designation")
	if designation:
		filters["designation"] = designation
	is_active = as_bool(args, "is_active")
	if is_active is not None:
		filters["is_active"] = 1 if is_active else 0

	rows = frappe.db.get_all(
		POSITION_WAGE_DEFAULT,
		filters=filters,
		fields=list(_DEFAULT_FIELDS),
		order_by="company asc, designation asc, effective_from desc",
		limit_page_length=as_limit(args),
	)
	on_date = as_date(args, "on_date")
	described = [_describe_default(row) for row in rows]

	grouped: dict = {}
	for row in rows:
		grouped.setdefault((row.get("company"), row.get("designation")), []).append(row)
	effective = []
	for key in sorted(grouped, key=lambda item: (str(item[0]), str(item[1]))):
		chosen = wage_defaults.select_effective(grouped[key], on_date)
		if chosen:
			effective.append(_describe_default(chosen))

	return ToolResult(
		data={
			"count": len(described),
			"as_of": on_date or today(),
			"defaults": described,
			"in_force": effective,
			"note": (
				"`in_force` is the one row per (company, designation) that create_salary_structure "
				"would seed a new structure from as of `as_of`. Existing structures are not "
				"affected by any of these rows — the default is read once, at creation."
			),
		},
		summary=f"{len(described)} position wage default(s), {len(effective)} in force",
	)


# ── 7. get_position_wage_default ──────────────────────────────────────────


def get_position_wage_default(args: dict) -> ToolResult:
	"""One position wage default in full, and whether it is the row a new hire gets."""
	_require_defaults()
	row = _default_row_or_refuse(args)
	described = _describe_default(row)
	on_date = as_date(args, "on_date")
	siblings = [
		other
		for other in defaults_for_company(row.get("company"))
		if str(other.get("designation") or "") == str(row.get("designation") or "")
	]
	chosen = wage_defaults.select_effective(siblings, on_date)
	is_live = bool(chosen and chosen.get("name") == row.get("name"))
	return ToolResult(
		data={
			**described,
			"as_of": on_date or today(),
			"in_force": is_live,
			"in_force_instead": (chosen or {}).get("name") if not is_live else None,
			"note": (
				f"A {described['designation']} whose salary structure is created on "
				f"{on_date or today()} starts on this rate."
				if is_live
				else (
					f"This row is NOT what a structure created on {on_date or today()} would "
					f"start from — {(chosen or {}).get('name') or 'no row at all'} covers that date."
				)
			),
		},
		summary=f"position wage default {row.get('name')} ({described['designation']} at {described['hourly_rate']})",
	)


# ── 8. update_position_wage_default ───────────────────────────────────────


def update_position_wage_default(args: dict) -> ToolResult:
	"""Edit a position wage default, or take it out of service by clearing is_active.

	`company` and `designation` are not editable, for the reason
	`update_piecework_rate` gives: they are the pair the lookup resolves by.
	"""
	_require_defaults()
	row = _default_row_or_refuse(args)
	name = row["name"]

	for locked in ("company", "designation"):
		if as_str(args, locked):
			raise ToolError(
				f"{locked} cannot be changed on an existing Position Wage Default — it is half "
				"of the pair a new salary structure is seeded by. Create a new default instead. "
				"Nothing was changed."
			)

	doc = frappe.get_doc(POSITION_WAGE_DEFAULT, name)
	changed = []

	new_rate = _rate_arg(args, "hourly_rate")
	if new_rate is not None and new_rate != _num(row.get("hourly_rate")):
		doc.hourly_rate = new_rate
		changed.append("hourly_rate")
	for key in ("effective_from", "effective_to"):
		if key in args:
			value = as_date(args, key)
			if str(value or "") != str(row.get(key) or ""):
				setattr(doc, key, value or None)
				changed.append(key)
	notes = as_str(args, "notes")
	if notes and notes != (row.get("notes") or ""):
		doc.notes = notes
		changed.append("notes")
	is_active = as_bool(args, "is_active")
	if is_active is not None and bool(is_active) != bool(compat.checked(row.get("is_active"))):
		doc.is_active = 1 if is_active else 0
		changed.append("is_active")

	if not changed:
		return ToolResult(
			data={**_describe_default(doc.as_dict()), "changed": [], "note": "Nothing to change."},
			summary=f"position wage default {name} unchanged",
		)

	_refuse(wage_defaults.validate_position_wage_default(doc.as_dict()))
	try:
		doc.save(ignore_permissions=True)
	except Exception as exc:
		raise ToolError(f"{exc}") from None

	return ToolResult(
		data={
			**_describe_default(doc.as_dict()),
			"changed": changed,
			"note": (
				"Salary structures already created from this default are UNCHANGED — the default "
				"is read once, at creation, and what is on a structure is that worker's agreed "
				"rate. New structures created from now on start on the edited number."
			),
		},
		summary=f"updated position wage default {name} ({', '.join(changed)})",
		docstatus_delta="0 → 0 (amended draft)",
	)
