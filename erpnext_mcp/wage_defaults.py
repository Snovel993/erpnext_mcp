# SPDX-License-Identifier: MIT
"""Company-wide wage rates, and the order payroll reads them in. v0.61.0.

COLLECT ONCE, USE EVERYWHERE. Until this release a piece rate was a number on
one worker's Farm Salary Structure, which meant $1.25 a bucket was typed once
per picker and a season's raise was a hundred edits nobody could audit. Two
registers replace that:

  **Piecework Rate**        (company, activity) → rate per unit, from a date.
  **Position Wage Default** (company, designation) → hourly rate, from a date.

WHAT EACH ONE IS FOR IS DIFFERENT, AND THE DIFFERENCE IS THE WHOLE DESIGN.

A **Piecework Rate is read at payroll time**, every run, for every worker whose
structure names no rate of its own. Raise the row and the next run pays the new
rate — that is the point of a company-wide table, and it is why the row carries
`effective_from`/`effective_to` rather than being edited in place: a payroll for
a period that ended in May must still be able to find what May paid.

A **Position Wage Default is read once, when a salary structure is CREATED**,
and never again. It seeds `hourly_rate` on a new structure from the employee's
Designation so a Picker hired in June starts on the Picker rate. Once that
number is on the structure it is that worker's number, and editing the default
does not reach back through it. THAT ASYMMETRY IS DELIBERATE. A piece rate is a
property of the WORK — a bucket is a bucket, and the operation pays what the
operation pays. An hourly wage is a property of the EMPLOYMENT — it is what a
person was hired at, it is what a wage claim asks about, and a table that could
silently restate somebody's agreed hourly rate for a period already worked would
be a table that rewrites history.

────────────────────────────────────────────────────────────────────────────
THE LOOKUP ORDER, WHICH IS THE ONLY THING PAYROLL ASKS THIS MODULE
────────────────────────────────────────────────────────────────────────────

    1. the employee's Farm Salary Structure `base_rate`, where it is > 0
    2. the active Piecework Rate for that employee's company and activity
    3. neither — and that is an error, reported by name

STEP 1 WINS BECAUSE IT IS THE MORE SPECIFIC RECORD. A rate on one person's
structure is a rate somebody negotiated with that person; a company table cannot
know about it and must not overrule it. `> 0` rather than "is set" is what makes
the fallback reachable at all: `base_rate` is a required Currency field on Farm
Salary Structure, so a structure created without one holds 0.0, not None, and a
test for None would never fire on a real record.

STEP 3 IS AN ERROR AND NOT A ZERO. A piece-rate worker paid at a rate of nothing
earns nothing at the rate, and what they are then paid is the minimum wage
makeup — which is a real and correct number, and which is exactly why the
silence is dangerous: the slip balances, the run reports no failure, and the only
symptom is a makeup figure that looks like a rate set too low rather than a rate
that was never set at all. `resolve_piece_rate` refuses instead, and names the
company and the activity it could not find a row for.

WHAT A BATCH RUN DOES WITH THAT REFUSAL IS THE BATCH RUN'S BUSINESS, AND IT IS
NOT TO ABORT. `run_payroll_for_period` has said since v0.35.0 that a problem with
one worker does not hold up everybody else's pay — a picker with no salary
structure is reported, not thrown. A picker with no RATE is the same kind of
problem and gets the same treatment: `tools/payroll.py` catches the refusal per
employee into `employees_missing_piece_rates`, which the result carries and the
caller reads. A single-employee `preview_payroll` has nobody else to hold up, so
there the refusal is the answer.

────────────────────────────────────────────────────────────────────────────
WHICH ACTIVITY, WHEN THE HOURS DO NOT SAY
────────────────────────────────────────────────────────────────────────────

Neither Bucket Log Entry nor Farm Task Assignment records WHAT KIND of piecework
a count is — a bucket row is a bucket, full stop. So the activity comes from the
salary structure, which gained an optional `piecework_activity` field in this
release for exactly this: it is the worker's half of the (company, activity) pair
the rate table is keyed by, and it is the same vocabulary `ML Model` already uses.

WHERE A STRUCTURE NAMES NO ACTIVITY, one unambiguous company rate is used and
several are refused. That is not a guess — it is the observation that a company
with one piecework rate in force has already answered the question, and a company
with three has not. The refusal lists the candidates, because "set
piecework_activity on this structure to one of: bucket_segmentation, thinning"
is an instruction somebody can act on and "no rate found" is not.

Activities are compared through `normalize_activity`: case-folded, and spaces and
hyphens read as underscores. `Bucket Segmentation` typed into the Desk and
`bucket_segmentation` posted by the iPad are the same activity, and a rate table
that paid one and not the other would be a rate table that failed on a capital
letter.

────────────────────────────────────────────────────────────────────────────

NOTHING IN THIS MODULE TOUCHES THE DATABASE. It takes rows and returns answers,
so the ordering above is testable without a bench and the same functions serve
the doctype controllers, the MCP tools and the payroll run. The reads live in
`tools/wagedefaults.py`.
"""

from __future__ import annotations

from .errors import ToolError

PIECEWORK_RATE = "Piecework Rate"
POSITION_WAGE_DEFAULT = "Position Wage Default"

#: Where a resolved piece rate came from. Carried on every payroll slip that used
#: the fallback, because "why is this picker on $1.30" is a question the run
#: should answer without anybody opening two registers to compare them.
SOURCE_STRUCTURE = "salary_structure"
SOURCE_COMPANY = "piecework_rate"


def normalize_activity(value) -> str:
	"""An activity name, comparable. Case-folded, spaces and hyphens as underscores.

	See the module docstring: the Desk and the iPad spell the same activity
	differently and the rate table must not care.
	"""
	text = str(value or "").strip().lower()
	for separator in (" ", "-"):
		text = text.replace(separator, "_")
	while "__" in text:
		text = text.replace("__", "_")
	return text


def _as_float(value, default: float = 0.0) -> float:
	if value is None or value == "":
		return default
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _date_text(value) -> str:
	"""A date as the comparable YYYY-MM-DD prefix, or "" where there is none.

	String comparison rather than `datetime`, because every date reaching this
	module has come off a Frappe row as either a `date` or an ISO string and
	ISO-8601 dates sort correctly as text. A value that is neither sorts as
	absent, which for `effective_to` means open-ended — the same reading a blank
	gets, and the safe one.
	"""
	text = str(value or "").strip()
	return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else ""


# ── validation, shared by the controllers and the tools ────────────────────


def _validate_window(row: dict, errors: list) -> None:
	start = _date_text(row.get("effective_from"))
	end = _date_text(row.get("effective_to"))
	if not start:
		errors.append("effective_from is required — a rate with no start date covers nothing")
	if start and end and end < start:
		errors.append(f"effective_to ({end}) is before effective_from ({start})")


def validate_piecework_rate(row: dict) -> list:
	"""Everything wrong with one Piecework Rate, as sentences. Empty means valid."""
	errors: list = []
	if not str(row.get("company") or "").strip():
		errors.append("company is required — a rate table belongs to one entity")
	if not normalize_activity(row.get("activity")):
		errors.append(
			"activity is required — it is the other half of the (company, activity) pair "
			"payroll looks a rate up by"
		)
	rate = _as_float(row.get("rate_per_unit"), -1.0)
	if rate < 0:
		errors.append(
			f"rate_per_unit must be a number and cannot be negative, got {row.get('rate_per_unit')!r}"
		)
	_validate_window(row, errors)
	return errors


def validate_position_wage_default(row: dict) -> list:
	"""Everything wrong with one Position Wage Default, as sentences."""
	errors: list = []
	if not str(row.get("company") or "").strip():
		errors.append("company is required — a rate table belongs to one entity")
	if not str(row.get("designation") or "").strip():
		errors.append("designation is required — it is the job title this rate is the default for")
	rate = _as_float(row.get("hourly_rate"), -1.0)
	if rate < 0:
		errors.append(f"hourly_rate must be a number and cannot be negative, got {row.get('hourly_rate')!r}")
	_validate_window(row, errors)
	return errors


# ── choosing the row in force ──────────────────────────────────────────────


def covers(row: dict, on_date) -> bool:
	"""Whether one row is active AND its window contains `on_date`.

	An absent `on_date` means "whatever is active", which is what a caller
	listing the current rates wants and what a create-time default reads.
	"""
	if not int(_as_float(row.get("is_active"), 1.0)):
		return False
	when = _date_text(on_date)
	if not when:
		return True
	start = _date_text(row.get("effective_from"))
	end = _date_text(row.get("effective_to"))
	if start and when < start:
		return False
	if end and when > end:
		return False
	return True


def _recency(row: dict) -> tuple:
	"""Sort key: the row that started most recently, then the one written last.

	`name` is the final tie-break rather than `modified`, because two rows
	created in the same migrate can share a `modified` to the second and a
	resolution that depended on which one a list happened to return first would
	be a rate that changed between two identical payroll runs.
	"""
	return (_date_text(row.get("effective_from")), str(row.get("modified") or ""), str(row.get("name") or ""))


def select_effective(rows, on_date=None) -> dict | None:
	"""The one row in force on `on_date`, or None.

	Where several cover the date, THE LATEST `effective_from` WINS. That is the
	reading that makes a raise work the way somebody setting one expects: add a
	row from 1 June, leave the old one open-ended, and June onwards pays the new
	rate without anybody having to remember to close the old one.
	"""
	candidates = [dict(row) for row in (rows or []) if covers(row, on_date)]
	if not candidates:
		return None
	return max(candidates, key=_recency)


def activities_available(rows, on_date=None) -> list:
	"""Every distinct activity with a row in force, normalised and sorted.

	What the refusal below lists, so a caller told to name an activity is told
	which activities there are.
	"""
	return sorted(
		{normalize_activity(row.get("activity")) for row in (rows or []) if covers(row, on_date)} - {""}
	)


def piecework_rate_for(rows, activity=None, on_date=None) -> dict | None:
	"""The Piecework Rate row in force for one activity, or None.

	With no `activity`, the sole activity in force is used and several are
	refused — see the module docstring on why that is an observation rather than
	a guess.
	"""
	wanted = normalize_activity(activity)
	if wanted:
		matching = [row for row in (rows or []) if normalize_activity(row.get("activity")) == wanted]
		return select_effective(matching, on_date)
	available = activities_available(rows, on_date)
	if len(available) != 1:
		return None
	matching = [row for row in (rows or []) if normalize_activity(row.get("activity")) == available[0]]
	return select_effective(matching, on_date)


# ── the lookup order payroll asks for ──────────────────────────────────────


def resolve_piece_rate(structure: dict, rates, on_date=None) -> dict:
	"""Structure rate, else company rate, else refuse. See the module docstring.

	Args:
		structure: The employee's Farm Salary Structure as a dict — `base_rate`,
			`company`, `employee_name`, and optionally `piecework_activity`.
		rates: Every Piecework Rate row for that company. Filtering by activity
			and by date is this function's job, not the caller's.
		on_date: The date the rate is wanted for — a pay period's END, so a
			period that straddles a rate change is paid at the rate in force when
			it closed. Absent means "whatever is active now".

	Returns:
		`{"rate", "source", "activity", "piecework_rate"}`. `piecework_rate` is
		the docname the rate came from, or "" where the structure held it.

	Raises:
		ToolError: where neither the structure nor the company table has a rate.
	"""
	structure = dict(structure or {})
	own = _as_float(structure.get("base_rate"))
	activity = normalize_activity(structure.get("piecework_activity"))

	if own > 0:
		return {
			"rate": own,
			"source": SOURCE_STRUCTURE,
			"activity": activity,
			"piecework_rate": "",
		}

	row = piecework_rate_for(rates, activity, on_date)
	if row:
		return {
			"rate": _as_float(row.get("rate_per_unit")),
			"source": SOURCE_COMPANY,
			"activity": normalize_activity(row.get("activity")),
			"piecework_rate": str(row.get("name") or ""),
		}

	raise ToolError(no_rate_message(structure, rates, on_date))


def no_rate_message(structure: dict, rates, on_date=None) -> str:
	"""Why no rate was found, in terms of what to do about it.

	A separate function because the batch run reports this sentence on a list
	rather than raising it, and the two must not drift into saying different
	things about the same missing row.
	"""
	structure = dict(structure or {})
	who = structure.get("employee_name") or structure.get("employee") or "this employee"
	company = structure.get("company") or "their company"
	activity = normalize_activity(structure.get("piecework_activity"))
	available = activities_available(rates, on_date)

	if activity:
		return (
			f"{who} is paid by the piece, their salary structure names no base_rate, and "
			f"{company} has no active Piecework Rate for {activity!r}"
			+ (f" (it has: {', '.join(available)})" if available else " (it has none at all)")
			+ ". Create one with create_piecework_rate, or put a negotiated rate on the "
			"salary structure. Paying at zero would earn the minimum wage makeup and hide "
			"the missing rate behind a number that looks deliberate."
		)
	if len(available) > 1:
		return (
			f"{who}'s salary structure names no base_rate and no piecework_activity, and "
			f"{company} has {len(available)} piecework rates in force ({', '.join(available)}), "
			"so which one pays them is not a question this run can answer. Set "
			"piecework_activity on their salary structure to one of those."
		)
	return (
		f"{who} is paid by the piece, their salary structure names no base_rate, and "
		f"{company} has no active Piecework Rate at all. Create one with "
		"create_piecework_rate, or put a negotiated rate on the salary structure."
	)


def position_hourly_rate(rows, designation: str, on_date=None) -> dict | None:
	"""The default hourly rate for one job title, or None.

	Read ONCE, when a salary structure is created — see the module docstring on
	why this one does not reach back through the structures it seeded.
	"""
	wanted = str(designation or "").strip().casefold()
	if not wanted:
		return None
	matching = [row for row in (rows or []) if str(row.get("designation") or "").strip().casefold() == wanted]
	return select_effective(matching, on_date)
