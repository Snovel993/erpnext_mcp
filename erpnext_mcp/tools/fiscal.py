# SPDX-License-Identifier: MIT
"""The calendar the ledger is allowed to post into.

WHY THIS IS A TOOL AND NOT A DESK JOB. ERPNext refuses any posting whose date
falls outside a Fiscal Year, and it refuses it from inside the document being
saved — so "book the March 2025 equipment transfer" fails on a site whose only
fiscal year is 2026, with an error about a date rather than about a missing
year. Bringing historical events onto a set of books is exactly what
`set_opening_balance` is for, and it cannot work at all until the years those
events happened in exist. One is the prerequisite of the other.

A FISCAL YEAR WITH NO COMPANIES IS GLOBAL. That is ERPNext's own model: the
`companies` child table is a *restriction*, and a year with no rows in it applies
everywhere. It is also the shape almost every site wants, so it is the default
here — `companies` is optional, and leaving it out is not an omission.

THE OVERLAP CHECK IS COMPANY-AWARE, AND ERPNEXT'S MAY NOT BE. Two fiscal years
whose dates overlap for the same company make `get_fiscal_year` ambiguous, and
which one a posting lands in stops being a fact about the posting. So this
refuses an overlap where the company scopes intersect — a global year overlaps
everything, and two restricted years overlap only if they share a company.
ERPNext's own `validate_overlap` is company-blind on several versions and throws
a `NameError` for any date collision at all; where that is stricter than this
check, the framework's refusal is what a caller gets, unchanged. This tool never
loosens a rule the framework enforces — it only says the useful part earlier.

CHANGING A YEAR'S DATES IS NOT A RENAME. It silently re-buckets every posting
that falls out of the new range into no fiscal year at all, which is invisible
until somebody runs a comparative report. `update_fiscal_year` counts those
before it writes and refuses rather than orphaning them.
"""

import datetime

import frappe

from .. import compat
from ..args import as_bool, as_date, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

# `add_months` is hand-rolled in `tools/assets.py` — standard-library date maths
# rather than `frappe.utils.add_months` — precisely so the app's own arithmetic
# can be tested without the double having to reimplement a framework helper. The
# same reasoning applies to "one year after the start date", so it is reused
# rather than written a second time.
from .assets import add_months

FISCAL_YEAR = "Fiscal Year"
FISCAL_YEAR_COMPANY = "Fiscal Year Company"

_FISCAL_YEAR_FIELDS = (
	"name",
	"year",
	"year_start_date",
	"year_end_date",
	"disabled",
	"is_short_year",
	"auto_created",
)


def _require_fiscal_year() -> None:
	compat.require_doctype(FISCAL_YEAR, "It ships with ERPNext's Accounts module.")


def _row(year_name: str) -> dict:
	fields = compat.existing_fields(FISCAL_YEAR, _FISCAL_YEAR_FIELDS)
	row = frappe.db.get_value(FISCAL_YEAR, year_name, fields, as_dict=True)
	if not row:
		known = frappe.db.get_all(FISCAL_YEAR, pluck="name", limit=25, order_by="year_start_date desc")
		raise ToolError(
			f"no Fiscal Year named {year_name!r} on this site. Known fiscal years: "
			f"{', '.join(str(name) for name in known) or '<none>'}. list_fiscal_years has them all "
			"with their date ranges."
		)
	return dict(row)


def _companies_of(year_name: str) -> list[str]:
	"""The companies a fiscal year is restricted to. Empty means it is global."""
	if not compat.doctype_exists(FISCAL_YEAR_COMPANY):  # pragma: no cover - core child table
		return []
	return sorted(
		str(name)
		for name in frappe.db.get_all(
			FISCAL_YEAR_COMPANY,
			filters={"parenttype": FISCAL_YEAR, "parent": year_name},
			pluck="company",
			limit=200,
		)
		if name
	)


def _describe(row: dict) -> dict:
	"""The fiscal year shape both tools in this module return."""
	companies = _companies_of(row["name"])
	return {
		"name": row.get("name"),
		"year": row.get("year") or row.get("name"),
		"year_start_date": str(row.get("year_start_date") or "") or None,
		"year_end_date": str(row.get("year_end_date") or "") or None,
		"disabled": bool(int(row.get("disabled") or 0)),
		"is_short_year": bool(int(row.get("is_short_year") or 0)),
		"auto_created": bool(int(row.get("auto_created") or 0)),
		"companies": companies,
		"scope": "every company on this site" if not companies else ", ".join(companies),
	}


def one_year_end(start: str) -> str:
	"""ERPNext's rule: the end date is one year after the start, less a day.

	`FiscalYear.validate_dates` enforces exactly this unless `is_short_year` is
	set, and the framework's message ("Fiscal Year End Date should be one year
	after Fiscal Year Start Date") does not say what date it wanted. This
	computes it so the refusal can.
	"""
	return (add_months(start, 12) - datetime.timedelta(days=1)).isoformat()


def _validate_range(start: str, end: str, is_short_year: bool, verb: str) -> None:
	if end < start:
		raise ToolError(
			f"year_end_date {end} is before year_start_date {start}. Nothing was {verb}."
		)
	if end == start:
		raise ToolError(f"a fiscal year cannot be one day long ({start}). Nothing was {verb}.")
	if is_short_year:
		return
	expected = one_year_end(start)
	if end != expected:
		raise ToolError(
			f"a fiscal year starting {start} has to end {expected} — one year later, less a day. "
			f"ERPNext refuses anything else in FiscalYear.validate_dates unless the year is "
			f"deliberately short. Got {end}. Either use {expected}, or pass is_short_year=true if "
			"this really is a stub period (a company's first months, or a change of year end). "
			f"Nothing was {verb}."
		)


def _validated_companies(raw, verb: str) -> list[str]:
	"""The `companies` argument: resolved, deduped, and empty when the year is global."""
	if raw in (None, ""):
		return []
	if isinstance(raw, str):
		raw = [raw]
	if not isinstance(raw, list):
		raise ToolError(
			"companies must be a list of Company names, e.g. "
			'["Orchard Meadow, LLC"]. Omit it for a fiscal year that applies to every '
			f"company — which is how ERPNext models a global year. Nothing was {verb}."
		)
	out = []
	for entry in raw:
		text = str(entry or "").strip()
		if not text:
			raise ToolError(f"companies contains an empty entry. Nothing was {verb}.")
		company = resolve_company(text, required=True)
		if company not in out:
			out.append(company)
	return out


def _overlaps(start_a: str, end_a: str, start_b: str, end_b: str) -> bool:
	return start_a <= end_b and start_b <= end_a


def _conflicts(start: str, end: str, companies: list[str], exclude: str = "") -> list[dict]:
	"""Every existing fiscal year this range would collide with, for these companies.

	Scope intersection is the whole subtlety. A year with no companies applies to
	all of them, so it collides with anything; two restricted years collide only
	where they share a company. Disabled years are included: ERPNext keeps
	resolving dates through them on several versions, and a range nobody can
	explain is worse than one that has to be renamed.
	"""
	fields = compat.existing_fields(
		FISCAL_YEAR, ("name", "year_start_date", "year_end_date", "disabled")
	)
	found = []
	for row in frappe.db.get_all(FISCAL_YEAR, fields=fields, order_by="year_start_date asc", limit=500):
		if row["name"] == exclude:
			continue
		other_start = str(row.get("year_start_date") or "")
		other_end = str(row.get("year_end_date") or "")
		if not other_start or not other_end:
			continue
		if not _overlaps(start, end, other_start, other_end):
			continue
		other_companies = _companies_of(row["name"])
		shared = sorted(set(companies) & set(other_companies))
		if companies and other_companies and not shared:
			continue
		found.append(
			{
				"name": row["name"],
				"year_start_date": other_start,
				"year_end_date": other_end,
				"disabled": bool(int(row.get("disabled") or 0)),
				"companies": other_companies,
				"shared_companies": shared,
				"why": (
					"it applies to every company"
					if not other_companies
					else (
						"this year would apply to every company"
						if not companies
						else f"they share {', '.join(shared)}"
					)
				),
			}
		)
	return found


def _refuse_conflicts(conflicts: list[dict], start: str, end: str, verb: str) -> None:
	if not conflicts:
		return
	detail = "; ".join(
		f"{row['name']} ({row['year_start_date']} to {row['year_end_date']}"
		+ (", disabled" if row["disabled"] else "")
		+ f") — {row['why']}"
		for row in conflicts[:5]
	)
	raise ToolError(
		f"{start} to {end} overlaps {len(conflicts)} existing fiscal year(s): {detail}"
		+ ("; …" if len(conflicts) > 5 else "")
		+ ". Two fiscal years covering the same day for the same company make "
		"ERPNext's own get_fiscal_year ambiguous, and which year a posting lands in "
		"stops being a fact about the posting. Restrict one of them to a company with "
		"`companies`, or fix the dates. Disabling a year does not free its range. "
		f"Nothing was {verb}."
	)


# ── 72. create_fiscal_year ──────────────────────────────────────────────────
def create_fiscal_year(args: dict) -> ToolResult:
	"""Create one Fiscal Year so the ledger will accept postings dated inside it."""
	_require_fiscal_year()
	year_name = as_str(args, "year_name", required=True)
	start = as_date(args, "year_start_date", required=True)
	end = as_date(args, "year_end_date", required=True)
	disabled = bool(as_bool(args, "disabled", False))
	auto_created = bool(as_bool(args, "auto_created", False))
	is_short_year = bool(as_bool(args, "is_short_year", False))

	if frappe.db.exists(FISCAL_YEAR, year_name):
		existing = _row(year_name)
		raise ToolError(
			f"a Fiscal Year named {year_name!r} already exists "
			f"({existing.get('year_start_date')} to {existing.get('year_end_date')}). A fiscal "
			"year names itself, so the name is the key and there cannot be two. Change its dates "
			"with update_fiscal_year, or pick another name. Nothing was created."
		)

	_validate_range(start, end, is_short_year, "created")
	companies = _validated_companies(args.get("companies"), "created")
	conflicts = _conflicts(start, end, companies)
	_refuse_conflicts(conflicts, start, end, "created")

	doc = frappe.new_doc(FISCAL_YEAR)
	if compat.has_field(FISCAL_YEAR, "year"):
		doc.year = year_name
	else:  # pragma: no cover - every supported ERPNext has the field
		doc.name = year_name
	doc.year_start_date = start
	doc.year_end_date = end
	if compat.has_field(FISCAL_YEAR, "disabled"):
		doc.disabled = 1 if disabled else 0
	if is_short_year and compat.has_field(FISCAL_YEAR, "is_short_year"):
		doc.is_short_year = 1
	if auto_created and compat.has_field(FISCAL_YEAR, "auto_created"):
		doc.auto_created = 1
	for company in companies:
		doc.append("companies", {"company": company})
	doc.insert()

	data = {
		**_describe(_row(doc.name)),
		"requested_name": year_name,
		"expected_end_date_for_a_full_year": one_year_end(start),
		"note": (
			"A Fiscal Year is a permission for a date, not a posting. Nothing was booked and no "
			"balance moved — what changed is that ERPNext will now accept a posting_date between "
			f"{start} and {end}"
			+ (
				" for every company on this site."
				if not companies
				else f" for {', '.join(companies)}."
			)
		),
		"next_step": (
			"Historical events for this period can now be booked. set_opening_balance is the tool "
			"for balances that were true before these books started; create_journal_entry is the "
			"tool for a transaction that actually happened in the period."
		),
	}
	if disabled:
		data["warning"] = (
			"This year was created DISABLED, so ERPNext will still refuse postings dated inside "
			"it. Re-enable it with update_fiscal_year(disabled=false) when you are ready to post."
		)
	return ToolResult(
		data,
		f"created Fiscal Year {doc.name} ({start} to {end}) for "
		+ (", ".join(companies) if companies else "every company")
		+ (" — disabled" if disabled else ""),
		docstatus_delta="none → 0 (created)",
	)


# ── 73. update_fiscal_year ──────────────────────────────────────────────────
def update_fiscal_year(args: dict) -> ToolResult:
	"""Move a fiscal year's dates, or enable/disable it. Never renames it.

	The dates are the dangerous half. Moving them does not move a single posting;
	it changes which year — or no year at all — every posting already written
	falls into, retroactively. So the postings that would fall out of the new
	range are counted before anything is written, and any at all is a refusal.
	"""
	_require_fiscal_year()
	year_name = as_str(args, "year_name", required=True)
	row = _row(year_name)

	if "new_year_name" in args or "year" in args:
		raise ToolError(
			"a Fiscal Year cannot be renamed by this tool. ERPNext names it after itself, so the "
			"name is the docname, and every Journal Entry, Budget and Period Closing Voucher that "
			"names a fiscal year names this string. Renaming it is a Desk decision. Nothing was "
			"changed."
		)
	if "companies" in args:
		raise ToolError(
			"update_fiscal_year cannot change which companies a fiscal year applies to. Narrowing "
			"the scope of a year that already has postings in it takes those postings out of any "
			"fiscal year for the companies it drops, and widening it can create an overlap this "
			"tool would have refused at creation. Do it in the Desk, where the consequences are "
			"visible. Nothing was changed."
		)

	new_start = as_date(args, "new_year_start_date")
	new_end = as_date(args, "new_year_end_date")
	disabled = as_bool(args, "disabled")
	is_short_year = as_bool(args, "is_short_year")

	if new_start is None and new_end is None and disabled is None and is_short_year is None:
		raise ToolError(
			"nothing to change. Pass at least one of new_year_start_date, new_year_end_date, "
			"is_short_year or disabled."
		)

	old_start = str(row.get("year_start_date") or "")
	old_end = str(row.get("year_end_date") or "")
	start = new_start or old_start
	end = new_end or old_end
	short = bool(int(row.get("is_short_year") or 0)) if is_short_year is None else bool(is_short_year)

	companies = _companies_of(row["name"])
	moved = start != old_start or end != old_end

	orphans = 0
	if moved:
		_validate_range(start, end, short, "changed")
		conflicts = _conflicts(start, end, companies, exclude=row["name"])
		_refuse_conflicts(conflicts, start, end, "changed")
		orphans = _postings_falling_outside(old_start, old_end, start, end, companies)
		if orphans:
			raise ToolError(
				f"moving {row['name']} from ({old_start} to {old_end}) to ({start} to {end}) would "
				f"leave {orphans} GL Entry row(s) outside any fiscal year. Moving the dates moves no "
				"posting: it changes which year every posting already written falls into, "
				"retroactively, and a posting in no fiscal year is one that stops appearing in "
				"period comparisons and cannot be corrected without reopening the year. Widen the "
				"range instead, or create the adjacent year first. Nothing was changed."
			)

	changes = {}
	doc = frappe.get_doc(FISCAL_YEAR, row["name"])
	if new_start and new_start != old_start:
		doc.year_start_date = new_start
		changes["year_start_date"] = [old_start, new_start]
	if new_end and new_end != old_end:
		doc.year_end_date = new_end
		changes["year_end_date"] = [old_end, new_end]
	if is_short_year is not None and bool(int(row.get("is_short_year") or 0)) != bool(is_short_year):
		if not compat.has_field(FISCAL_YEAR, "is_short_year"):
			raise ToolError(
				"this site's Fiscal Year doctype has no `is_short_year` field. Nothing was changed."
			)
		doc.is_short_year = 1 if is_short_year else 0
		changes["is_short_year"] = [bool(int(row.get("is_short_year") or 0)), bool(is_short_year)]
	if disabled is not None and bool(int(row.get("disabled") or 0)) != bool(disabled):
		if not compat.has_field(FISCAL_YEAR, "disabled"):
			raise ToolError(
				"this site's Fiscal Year doctype has no `disabled` field. Nothing was changed."
			)
		doc.disabled = 1 if disabled else 0
		changes["disabled"] = [bool(int(row.get("disabled") or 0)), bool(disabled)]

	if not changes:
		raise ToolError(
			f"{row['name']} already has those values; nothing to change. Pass a value that differs "
			"from the current one."
		)
	doc.save()

	entries = _gl_entries_between(start, end, companies)
	data = {
		**_describe(_row(row["name"])),
		"changes": changes,
		"gl_entries_in_the_new_range": entries,
		"note": (
			"Moving a fiscal year's dates moves no posting. What changes is which year every "
			f"posting already written rolls up into — {entries} GL Entry row(s) now fall inside "
			"this one. Re-run any period report whose figures were quoted."
			if moved
			else "Only flags changed; no date moved and no posting is affected."
		),
	}
	if changes.get("disabled") == [False, True]:
		data["warning"] = (
			f"{row['name']} is now disabled, so ERPNext will refuse new postings dated inside it. "
			f"Nothing was deleted: the {entries} GL Entry row(s) already in this range remain and "
			"still appear in reports covering them."
		)
	summary = ", ".join(f"{field} {before!r} → {after!r}" for field, (before, after) in changes.items())
	return ToolResult(data, f"updated Fiscal Year {row['name']}: {summary}", docstatus_delta="")


def _gl_filters(companies: list[str]) -> dict:
	filters: dict = {}
	if compat.has_field("GL Entry", "is_cancelled"):
		filters["is_cancelled"] = 0
	if companies and compat.has_field("GL Entry", "company"):
		filters["company"] = ("in", companies)
	return filters


def _gl_entries_between(start: str, end: str, companies: list[str]) -> int:
	filters = _gl_filters(companies)
	filters["posting_date"] = ("between", [start, end])
	return frappe.db.count("GL Entry", filters)


def _postings_falling_outside(
	old_start: str, old_end: str, new_start: str, new_end: str, companies: list[str]
) -> int:
	"""How many postings currently inside this year would fall outside the new range.

	Counted from posting dates rather than from GL Entry's own `fiscal_year`
	column, because that column is stamped at submit and is exactly what a date
	change makes stale — asking it would be asking the thing being invalidated.
	"""
	inside_before = _gl_entries_between(old_start, old_end, companies)
	if not inside_before:
		return 0
	# The part of the old range that survives is its intersection with the new
	# one; everything else is orphaned.
	overlap_start = max(old_start, new_start)
	overlap_end = min(old_end, new_end)
	if overlap_start > overlap_end:
		return inside_before
	return inside_before - _gl_entries_between(overlap_start, overlap_end, companies)
