# SPDX-License-Identifier: MIT
"""Salary structure + payroll tools.

v0.30.0. Links the calc engine to the MCP surface. Read tools are dry-run
previews and lookups; mutating tools create salary structures and payroll
entries.

v0.35.0. THE HOURS NOW COME OFF THE SHIFT REGISTER. `_load_shifts` used to
return the crew-wide span for every worker with zero overtime and zero piece
units, so a payroll run was a shell around numbers somebody keyed in by hand.
It now goes through `payroll_integration`, which is pure and testable, and
every tool here — including the three v0.30.0 ones — reads the same
per-worker spans, the same weekly overtime split and the same piece counts.

THE THREE NEW TOOLS ARE COMPANY-WIDE. `preview_payroll_for_period` and
`run_payroll_for_period` do for a whole company what `preview_payroll` does for
one person, and `get_employee_timesheet_summary` is the hours WITHOUT the
money — the read a foreman opens when somebody asks why their cheque is what it
is, and the one that does not need the payroll switches turned on to answer.

WHERE THE PIECE UNITS COME FROM IS A PROPERTY OF THE SITE, and every result
says which source answered. The BucketLog bridge is written by whichever version
of the iPad app is in the field this season, and Farm Task Assignment may or may
not carry a count; `_load_piece_rows` looks for both, uses what is there, and
NAMES what is not rather than reporting a piece-rate worker's day as zero
buckets without comment.
"""
from __future__ import annotations

import json

import frappe
from frappe.utils import today

from .. import compat, payroll_integration
from ..args import as_date, as_int, as_str, resolve_company
from ..errors import ToolError
from ..payroll_calc import (
	MINIMUM_WAGE_RATES,
	calculate_full_payroll,
	calculate_gross_pay,
	check_minimum_wage,
)
from ..result import ToolResult
from ..state_withholding import SUPPORTED_STATES
from ..withholding import FILING_STATUS_MAP, PERIODS_PER_YEAR

SALARY_STRUCTURE = "Farm Salary Structure"
PAYROLL_ENTRY = "Farm Payroll Entry"
PAYROLL_SLIP = "Farm Payroll Slip"
EMPLOYEE = "Employee"
W4_FORM = "W-4 Form"
FICA_CONFIG = "FICA Configuration"
FEDERAL_TAX_TABLE = "Federal Tax Table"
STATE_TAX_CONFIG = "State Tax Configuration"
STATE_TAX_TABLE = "State Tax Table"
FARM_SHIFT = "Farm Shift"
CREW_MEMBER = "Farm Shift Crew Member"
FARM_TASK_ASSIGNMENT = "Farm Task Assignment"

#: The BucketLog bridge. Optional, external, and written by whichever version of
#: the iPad app is in the field this season — so every column it might carry is
#: looked for rather than assumed. `compliance_fields.py` says the same thing at
#: length about the five traceability columns it grafts on.
BUCKET_LOG = "Bucket Log Entry"

#: Most shifts one payroll run will read. A fortnight of a large crew is a few
#: hundred; five thousand is a date range somebody typed wrong, and paying out
#: against it silently would be worse than refusing.
SHIFT_CAP = 5000


def _as_float(args: dict, key: str, default: float = 0.0, required: bool = False) -> float:
	val = args.get(key)
	if val is None or val == "":
		if required:
			raise ToolError(f"{key} is required.")
		return default
	try:
		return float(val)
	except (TypeError, ValueError):
		raise ToolError(f"{key} must be a number, got {val!r}.")


def _num(value, default: float = 0.0) -> float:
	"""A float off a row, with a default where the column is absent or empty.

	Distinct from `_as_float` above, which coerces a tool ARGUMENT and refuses
	what it cannot read. A database column that came back None is not a caller
	mistake and should not raise into a payroll run.
	"""
	if value is None or value == "":
		return default
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _resolve_employee(args: dict) -> str:
	emp = as_str(args, "employee") or as_str(args, "name") or as_str(args, "employee_name")
	if not emp:
		raise ToolError("employee is required.")
	if frappe.db.exists(EMPLOYEE, emp):
		return emp
	found = frappe.db.get_value(EMPLOYEE, {"employee_name": emp}, "name")
	if found:
		return str(found)
	raise ToolError(f"no Employee called {emp!r} on this site.")


# ── Read tools ────────────────────────────────────────────────────────────


def get_salary_structure(args: dict) -> ToolResult:
	"""Get the active salary structure for an employee."""
	employee = _resolve_employee(args)

	filters = {"employee": employee, "is_active": 1}
	company = as_str(args, "company")
	if company:
		filters["company"] = resolve_company(company)

	name = frappe.db.get_value(
		SALARY_STRUCTURE, filters, "name",
		order_by="effective_from desc",
	)
	if not name:
		raise ToolError(f"no active salary structure for employee {employee!r}.")

	fields = [
		"name", "employee", "employee_name", "company", "pay_type",
		"base_rate", "effective_from", "effective_to", "is_active", "notes",
	]
	row = frappe.db.get_value(SALARY_STRUCTURE, name, fields, as_dict=True)
	data = {k: (str(v) if v is not None else None) for k, v in row.items()}
	return ToolResult(
		data=data,
		summary=f"Salary structure for {employee}: {row.get('pay_type')} at {row.get('base_rate')}",
	)


def list_salary_structures(args: dict) -> ToolResult:
	"""List salary structures with optional filters."""
	filters = {}
	company = as_str(args, "company")
	if company:
		filters["company"] = resolve_company(company)
	pay_type = as_str(args, "pay_type")
	if pay_type:
		filters["pay_type"] = pay_type
	is_active = args.get("is_active")
	if is_active is not None:
		filters["is_active"] = int(is_active)
	employee = as_str(args, "employee")
	if employee:
		filters["employee"] = _resolve_employee({"employee": employee})
	limit = as_int(args, "limit", 100)
	if limit and limit > 500:
		limit = 500

	rows = frappe.db.get_all(
		SALARY_STRUCTURE,
		filters=filters,
		fields=[
			"name", "employee", "employee_name", "company", "pay_type",
			"base_rate", "effective_from", "effective_to", "is_active",
		],
		limit_page_length=limit,
		order_by="modified desc",
	)
	data = {"structures": [dict(r) for r in rows], "count": len(rows)}
	return ToolResult(data=data, summary=f"{len(rows)} salary structure(s)")


def preview_payroll(args: dict) -> ToolResult:
	"""Dry-run payroll for a single employee — no records created."""
	employee = _resolve_employee(args)
	pay_period_start = as_date(args, "pay_period_start", required=True)
	pay_period_end = as_date(args, "pay_period_end", required=True)
	pay_frequency = as_str(args, "pay_frequency") or "Biweekly"
	if pay_frequency not in PERIODS_PER_YEAR:
		raise ToolError(f"pay_frequency must be one of: {', '.join(PERIODS_PER_YEAR)}.")

	company = as_str(args, "company")
	if company:
		company = resolve_company(company)

	# Load salary structure
	ss_filters = {"employee": employee, "is_active": 1}
	if company:
		ss_filters["company"] = company
	ss_name = frappe.db.get_value(
		SALARY_STRUCTURE, ss_filters, "name", order_by="effective_from desc",
	)
	if not ss_name:
		raise ToolError(f"no active salary structure for {employee}.")

	ss = frappe.db.get_value(
		SALARY_STRUCTURE, ss_name,
		["name", "pay_type", "base_rate", "employee_name"],
		as_dict=True,
	)

	# Load shifts
	shifts = _load_shifts(employee, pay_period_start, pay_period_end, company)

	# Load tax config
	tax_config = _build_tax_config(employee, pay_frequency, company)

	employee_name = ss.get("employee_name") or frappe.db.get_value(EMPLOYEE, employee, "employee_name") or employee

	result = calculate_full_payroll(
		{"employee": employee, "employee_name": employee_name},
		shifts,
		{"pay_type": ss.pay_type, "base_rate": float(ss.base_rate or 0), "name": ss_name},
		tax_config,
	)

	return ToolResult(
		data=result,
		summary=f"Payroll preview for {employee_name}: gross ${result['gross_pay']}, "
		        f"net ${result['net_pay']}, {len(shifts)} shift(s)",
	)


def get_payroll_entry(args: dict) -> ToolResult:
	"""Get a payroll entry with all its slips."""
	name = as_str(args, "name") or as_str(args, "payroll_entry")
	if not name:
		raise ToolError("name (payroll entry docname) is required.")
	if not frappe.db.exists(PAYROLL_ENTRY, name):
		raise ToolError(f"no Farm Payroll Entry called {name!r}.")

	doc = frappe.get_doc(PAYROLL_ENTRY, name)
	slips = []
	for slip in doc.get("slips") or []:
		_g = slip.get if isinstance(slip, dict) else lambda k, d=None: getattr(slip, k, d)
		slip_data = {
			"employee": _g("employee"),
			"employee_name": _g("employee_name"),
			"pay_type": _g("pay_type"),
			"work_state": _g("work_state"),
			"total_hours": float(_g("total_hours") or 0),
			"regular_hours": float(_g("regular_hours") or 0),
			"overtime_hours": float(_g("overtime_hours") or 0),
			"piece_units": float(_g("piece_units") or 0),
			"piece_rate": float(_g("piece_rate") or 0),
			"gross_pay": float(_g("gross_pay") or 0),
			"federal_withholding": float(_g("federal_withholding") or 0),
			"state_withholding": float(_g("state_withholding") or 0),
			"social_security": float(_g("social_security") or 0),
			"medicare": float(_g("medicare") or 0),
			"total_deductions": float(_g("total_deductions") or 0),
			"net_pay": float(_g("net_pay") or 0),
			"salary_structure": _g("salary_structure"),
			"minimum_wage_check": bool(int(_g("minimum_wage_check") or 0)),
			"effective_hourly_rate": float(_g("effective_hourly_rate") or 0),
		}
		raw_detail = _g("state_taxes_detail")
		if raw_detail:
			try:
				slip_data["state_taxes_detail"] = json.loads(raw_detail)
			except (json.JSONDecodeError, TypeError):
				slip_data["state_taxes_detail"] = raw_detail
		slips.append(slip_data)

	data = {
		"name": doc.name,
		"company": doc.company,
		"pay_period_start": str(doc.pay_period_start),
		"pay_period_end": str(doc.pay_period_end),
		"pay_frequency": doc.pay_frequency,
		"status": doc.status,
		"total_gross": float(doc.total_gross or 0),
		"total_deductions": float(doc.total_deductions or 0),
		"total_net": float(doc.total_net or 0),
		"employee_count": int(doc.employee_count or 0),
		"slips": slips,
	}
	return ToolResult(
		data=data,
		summary=f"Payroll entry {name}: {doc.status}, {len(slips)} slip(s), "
		        f"gross ${doc.total_gross}, net ${doc.total_net}",
	)


def list_payroll_entries(args: dict) -> ToolResult:
	"""List payroll entries with optional filters."""
	filters = {}
	company = as_str(args, "company")
	if company:
		filters["company"] = resolve_company(company)
	status = as_str(args, "status")
	if status:
		filters["status"] = status
	pay_frequency = as_str(args, "pay_frequency")
	if pay_frequency:
		filters["pay_frequency"] = pay_frequency
	limit = as_int(args, "limit", 100)
	if limit and limit > 500:
		limit = 500

	rows = frappe.db.get_all(
		PAYROLL_ENTRY,
		filters=filters,
		fields=[
			"name", "company", "pay_period_start", "pay_period_end",
			"pay_frequency", "status", "total_gross", "total_deductions",
			"total_net", "employee_count",
		],
		limit_page_length=limit,
		order_by="pay_period_start desc",
	)
	data = {"entries": [dict(r) for r in rows], "count": len(rows)}
	return ToolResult(data=data, summary=f"{len(rows)} payroll entry(ies)")


# ── Mutating tools ────────────────────────────────────────────────────────


def create_salary_structure(args: dict) -> ToolResult:
	"""Create a salary structure for an employee."""
	employee = _resolve_employee(args)
	company = resolve_company(as_str(args, "company"), required=True)
	pay_type = as_str(args, "pay_type", required=True)
	if pay_type not in ("Piece Rate", "Hourly", "Salary"):
		raise ToolError("pay_type must be Piece Rate, Hourly, or Salary.")
	base_rate = _as_float(args, "base_rate", required=True)
	if base_rate <= 0:
		raise ToolError("base_rate must be positive.")
	effective_from = as_date(args, "effective_from") or today()
	effective_to = as_date(args, "effective_to")
	notes = as_str(args, "notes")

	doc = frappe.get_doc({
		"doctype": SALARY_STRUCTURE,
		"employee": employee,
		"company": company,
		"pay_type": pay_type,
		"base_rate": base_rate,
		"effective_from": effective_from,
		"effective_to": effective_to or None,
		"is_active": 1,
		"notes": notes or None,
	})
	doc.flags.ignore_permissions = True
	doc.insert()

	emp_name = frappe.db.get_value(EMPLOYEE, employee, "employee_name") or employee
	return ToolResult(
		data={
			"name": doc.name,
			"employee": employee,
			"employee_name": emp_name,
			"pay_type": pay_type,
			"base_rate": base_rate,
			"effective_from": str(effective_from),
		},
		summary=f"Salary structure created for {emp_name}: {pay_type} at {base_rate}",
	)


def deactivate_salary_structure(args: dict) -> ToolResult:
	"""Soft-deactivate a salary structure."""
	name = as_str(args, "name") or as_str(args, "salary_structure")
	if not name:
		employee = as_str(args, "employee")
		if employee:
			employee = _resolve_employee({"employee": employee})
			name = frappe.db.get_value(
				SALARY_STRUCTURE,
				{"employee": employee, "is_active": 1},
				"name",
				order_by="effective_from desc",
			)
	if not name:
		raise ToolError("name or employee is required to identify the salary structure.")
	if not frappe.db.exists(SALARY_STRUCTURE, name):
		raise ToolError(f"no Farm Salary Structure called {name!r}.")

	frappe.db.set_value(SALARY_STRUCTURE, name, {
		"is_active": 0,
		"effective_to": today(),
	})

	emp = frappe.db.get_value(SALARY_STRUCTURE, name, "employee")
	emp_name = frappe.db.get_value(EMPLOYEE, emp, "employee_name") if emp else name
	return ToolResult(
		data={"name": name, "is_active": 0, "effective_to": today()},
		summary=f"Salary structure {name} deactivated for {emp_name}",
	)


def calculate_payroll(args: dict) -> ToolResult:
	"""Generate a full payroll entry for a pay period (Draft status)."""
	company = resolve_company(as_str(args, "company"), required=True)
	pay_period_start = as_date(args, "pay_period_start", required=True)
	pay_period_end = as_date(args, "pay_period_end", required=True)
	pay_frequency = as_str(args, "pay_frequency") or "Biweekly"
	if pay_frequency not in PERIODS_PER_YEAR:
		raise ToolError(f"pay_frequency must be one of: {', '.join(PERIODS_PER_YEAR)}.")

	# Find all active employees with salary structures
	structures = frappe.db.get_all(
		SALARY_STRUCTURE,
		filters={"company": company, "is_active": 1},
		fields=["name", "employee", "employee_name", "pay_type", "base_rate"],
	)
	if not structures:
		raise ToolError(f"no active salary structures for company {company}.")

	# Create the payroll entry
	entry = frappe.get_doc({
		"doctype": PAYROLL_ENTRY,
		"company": company,
		"pay_period_start": pay_period_start,
		"pay_period_end": pay_period_end,
		"pay_frequency": pay_frequency,
		"status": "Draft",
	})

	total_gross = 0.0
	total_deductions = 0.0
	total_net = 0.0

	for ss in structures:
		employee = ss.employee
		shifts = _load_shifts(employee, pay_period_start, pay_period_end, company)
		tax_config = _build_tax_config(employee, pay_frequency, company)

		slip = calculate_full_payroll(
			{"employee": employee, "employee_name": ss.employee_name},
			shifts,
			{"pay_type": ss.pay_type, "base_rate": float(ss.base_rate or 0), "name": ss.name},
			tax_config,
		)

		state_detail = json.dumps(slip.get("state_taxes_detail", {}), default=str)

		entry.append("slips", {
			"employee": employee,
			"employee_name": ss.employee_name,
			"salary_structure": ss.name,
			"pay_type": slip["pay_type"],
			"work_state": slip["work_state"],
			"total_hours": slip["total_hours"],
			"regular_hours": slip["regular_hours"],
			"overtime_hours": slip["overtime_hours"],
			"piece_units": slip["piece_units"],
			"piece_rate": slip["piece_rate"],
			"gross_pay": slip["gross_pay"],
			"federal_withholding": slip["federal_withholding"],
			"state_withholding": slip["state_withholding"],
			"social_security": slip["social_security"],
			"medicare": slip["medicare"],
			"state_taxes_detail": state_detail,
			"total_deductions": slip["total_deductions"],
			"net_pay": slip["net_pay"],
			"minimum_wage_check": 1 if slip["minimum_wage_check"] else 0,
			"effective_hourly_rate": slip["effective_hourly_rate"],
		})

		total_gross += slip["gross_pay"]
		total_deductions += slip["total_deductions"]
		total_net += slip["net_pay"]

	entry.total_gross = round(total_gross, 2)
	entry.total_deductions = round(total_deductions, 2)
	entry.total_net = round(total_net, 2)
	entry.employee_count = len(structures)
	entry.status = "Calculated"

	entry.flags.ignore_permissions = True
	entry.insert()

	return ToolResult(
		data={
			"name": entry.name,
			"company": company,
			"pay_period": f"{pay_period_start} to {pay_period_end}",
			"pay_frequency": pay_frequency,
			"status": "Calculated",
			"employee_count": len(structures),
			"total_gross": entry.total_gross,
			"total_deductions": entry.total_deductions,
			"total_net": entry.total_net,
		},
		summary=f"Payroll entry {entry.name}: {len(structures)} employee(s), "
		        f"gross ${entry.total_gross}, net ${entry.total_net}",
	)


def submit_payroll(args: dict) -> ToolResult:
	"""Move a payroll entry from Calculated to Submitted."""
	name = as_str(args, "name") or as_str(args, "payroll_entry")
	if not name:
		raise ToolError("name (payroll entry docname) is required.")
	if not frappe.db.exists(PAYROLL_ENTRY, name):
		raise ToolError(f"no Farm Payroll Entry called {name!r}.")

	status = frappe.db.get_value(PAYROLL_ENTRY, name, "status")
	if status != "Calculated":
		raise ToolError(
			f"payroll entry {name} is {status!r}. Only Calculated entries can be submitted."
		)

	frappe.db.set_value(PAYROLL_ENTRY, name, "status", "Submitted")

	return ToolResult(
		data={"name": name, "status": "Submitted"},
		summary=f"Payroll entry {name} submitted",
		docstatus_delta="Calculated → Submitted",
	)


# ── v0.35.0: the integrated run ───────────────────────────────────────────


def get_employee_timesheet_summary(args: dict) -> ToolResult:
	"""One employee's hours in a date range, as payroll will read them.

	THE HOURS WITHOUT THE MONEY, and that is the point of it being its own tool.
	"Why is my cheque this?" is answered by the timesheet, not the withholding,
	and a foreman who can show somebody their own spans, their own overtime and
	which week it fell in has answered the question before it becomes a dispute.
	It needs none of the payroll switches, because the hours are not the payroll.
	"""
	employee = _resolve_employee(args)
	start = as_date(args, "start_date") or as_date(args, "pay_period_start", required=True)
	end = as_date(args, "end_date") or as_date(args, "pay_period_end", required=True)
	company = as_str(args, "company")
	if company:
		company = resolve_company(company)

	threshold = _as_float(args, "overtime_threshold", payroll_integration.OVERTIME_THRESHOLD_HOURS)
	anchor = as_date(args, "workweek_anchor")

	shifts, provenance = _load_period_shifts(company, start, end, employees=[employee])
	aggregates = payroll_integration.aggregate_shifts_for_period(
		shifts, start, end, overtime_threshold=threshold, workweek_anchor=anchor,
	)
	aggregate = aggregates.get(employee) or payroll_integration.empty_aggregate(employee)

	employee_name = (
		aggregate.get("employee_name")
		or frappe.db.get_value(EMPLOYEE, employee, "employee_name")
		or employee
	)
	aggregate["employee_name"] = employee_name

	structure = None
	ss_filters = {"employee": employee, "is_active": 1}
	if company:
		ss_filters["company"] = company
	ss_name = frappe.db.get_value(
		SALARY_STRUCTURE, ss_filters, "name", order_by="effective_from desc",
	)
	if ss_name:
		row = frappe.db.get_value(
			SALARY_STRUCTURE, ss_name, ["name", "pay_type", "base_rate"], as_dict=True,
		)
		structure = {
			"name": row.get("name"),
			"pay_type": row.get("pay_type"),
			"base_rate": _num(row.get("base_rate")),
		}

	data = dict(aggregate)
	data["period_start"] = str(start)
	data["period_end"] = str(end)
	data["company"] = company
	data["salary_structure"] = structure
	data["sources"] = provenance

	if structure is None:
		data["no_salary_structure"] = (
			f"{employee_name} has no active salary structure, so these hours cannot be turned "
			"into pay. create_salary_structure is what makes them payable."
		)

	return ToolResult(
		data=data,
		summary=(
			f"{employee_name}: {aggregate['total_hours']}h ({aggregate['overtime_hours']}h OT) "
			f"over {aggregate['shift_count']} shift(s), {start} to {end}"
		),
	)


def _period_run(args: dict, creating: bool) -> tuple[dict, list[dict], dict]:
	"""Everything both period tools do before they diverge.

	One function because a preview that computed anything differently from the
	run it previews would be worse than no preview. The only difference between
	the two tools is whether the slips are written down.
	"""
	company = resolve_company(as_str(args, "company"), required=True)
	start = as_date(args, "pay_period_start", required=True)
	end = as_date(args, "pay_period_end", required=True)
	if str(end) < str(start):
		raise ToolError(
			f"pay_period_end ({end}) is before pay_period_start ({start}). Nothing was "
			f"{'created' if creating else 'calculated'}."
		)
	pay_frequency = as_str(args, "pay_frequency") or "Biweekly"
	if pay_frequency not in PERIODS_PER_YEAR:
		raise ToolError(f"pay_frequency must be one of: {', '.join(PERIODS_PER_YEAR)}.")

	threshold = _as_float(args, "overtime_threshold", payroll_integration.OVERTIME_THRESHOLD_HOURS)
	anchor = as_date(args, "workweek_anchor")
	include_unworked = args.get("include_unworked")
	include_unworked = True if include_unworked is None else bool(int(include_unworked))

	only = as_str(args, "employee")
	employees = [_resolve_employee({"employee": only})] if only else None

	structures = _load_structures(company, employees)
	shifts, provenance = _load_period_shifts(company, start, end, employees=employees)

	# Everybody a slip could be owed to: whoever worked, plus whoever has a
	# structure. The union rather than either half, because a worker with no
	# structure has to be reported and a salaried employee with no shift has to
	# be paid.
	worked = sorted({
		member["employee"]
		for shift in shifts
		for member in shift.get("crew") or []
	})
	known = sorted(set(worked) | set(structures))

	slips = payroll_integration.run_integrated_payroll(
		shifts,
		structures,
		_load_w4_map(known),
		_load_state_configs(company),
		_load_fica_config(),
		start,
		end,
		company=company,
		federal_tax_tables=_load_federal_tables(pay_frequency),
		state_tax_tables=_load_state_tax_tables(),
		pay_frequency=pay_frequency,
		ytd_by_employee=_load_ytd(company, start, known),
		overtime_threshold=threshold,
		workweek_anchor=anchor,
		include_unworked=include_unworked,
	)

	aggregates = payroll_integration.aggregate_shifts_for_period(
		shifts, start, end, overtime_threshold=threshold, workweek_anchor=anchor,
	)
	missing = payroll_integration.employees_missing_structures(aggregates, structures)
	for row in missing:
		row["employee_name"] = (
			row.get("employee_name")
			or frappe.db.get_value(EMPLOYEE, row["employee"], "employee_name")
			or row["employee"]
		)

	context = {
		"company": company,
		"pay_period_start": str(start),
		"pay_period_end": str(end),
		"pay_frequency": pay_frequency,
		"overtime_threshold": threshold,
		"workweek_anchor": str(anchor) if anchor else str(start),
		"employees_missing_structures": missing,
		"sources": provenance,
	}
	return context, slips, payroll_integration.summarize_payroll_run(slips)


def _slip_view(slip: dict, verbose: bool) -> dict:
	"""One slip as a caller reads it. The detail is opt-in, and it is large."""
	view = {
		"employee": slip.get("employee"),
		"employee_name": slip.get("employee_name"),
		"salary_structure": slip.get("salary_structure"),
		"pay_type": slip.get("pay_type"),
		"work_state": slip.get("work_state"),
		"total_hours": slip.get("total_hours"),
		"regular_hours": slip.get("regular_hours"),
		"overtime_hours": slip.get("overtime_hours"),
		"break_hours": slip.get("break_hours"),
		"unpaid_break_hours": slip.get("unpaid_break_hours"),
		"piece_units": slip.get("piece_units"),
		"piece_rate": slip.get("piece_rate"),
		"shift_count": slip.get("shift_count"),
		"hours_by_state": slip.get("hours_by_state"),
		"overtime_hours_by_state": slip.get("overtime_hours_by_state"),
		"state_wages": slip.get("state_wages"),
		"gross_pay": slip.get("gross_pay"),
		"federal_withholding": slip.get("federal_withholding"),
		"state_withholding": slip.get("state_withholding"),
		"social_security": slip.get("social_security"),
		"medicare": slip.get("medicare"),
		"total_deductions": slip.get("total_deductions"),
		"net_pay": slip.get("net_pay"),
		"effective_hourly_rate": slip.get("effective_hourly_rate"),
		"minimum_wage_check": slip.get("minimum_wage_check"),
		"minimum_wage_detail": slip.get("minimum_wage_detail"),
		"weeks": slip.get("weeks"),
		"open_shifts": slip.get("open_shifts"),
	}
	if verbose:
		view["timesheet"] = slip.get("timesheet")
		view["state_taxes_detail"] = slip.get("state_taxes_detail")
		view["gross_detail"] = slip.get("gross_detail")
		view["federal_detail"] = slip.get("federal_detail")
	return view


def preview_payroll_for_period(args: dict) -> ToolResult:
	"""The whole company's payroll for a period, computed and not written.

	The same arithmetic `run_payroll_for_period` performs, off the same shifts,
	through the same code path — and no record at the end of it. Read the three
	lists before the totals: who has no salary structure, whose state fell below
	its minimum wage, and whose shift never got an end time.
	"""
	context, slips, totals = _period_run(args, creating=False)
	verbose = bool(int(args.get("detail") or 0))

	data = dict(context)
	data["slips"] = [_slip_view(slip, verbose) for slip in slips]
	data["totals"] = totals
	data["created"] = None
	data["note"] = (
		"Nothing was written. run_payroll_for_period performs the identical "
		"calculation and stores it as a Farm Payroll Entry."
	)

	return ToolResult(
		data=data,
		summary=(
			f"Payroll preview for {context['company']}, {context['pay_period_start']} to "
			f"{context['pay_period_end']}: {totals['employee_count']} employee(s), "
			f"{totals['total_hours']}h ({totals['total_overtime_hours']}h OT), "
			f"gross ${totals['total_gross']}, net ${totals['total_net']}"
			+ (
				f" — {len(context['employees_missing_structures'])} worker(s) with no salary structure"
				if context["employees_missing_structures"] else ""
			)
			+ (
				f" — {len(totals['below_minimum_wage'])} below minimum wage"
				if totals["below_minimum_wage"] else ""
			)
		),
	)


def run_payroll_for_period(args: dict) -> ToolResult:
	"""Compute the period off the shift register and store it as a payroll entry.

	Creates a Farm Payroll Entry in **Calculated** status with one slip per
	employee. Calculated rather than Submitted, always: submitting is
	`submit_payroll`, behind its own switch, because the two acts are different —
	one is arithmetic anybody can redo and the other is a statement that this is
	what the farm is paying.

	IT DOES NOT REFUSE A RUN WITH PROBLEMS IN IT. A worker below minimum wage, a
	shift with no end time, a picker with no salary structure — all reported, all
	on the result, none of them a reason to withhold a payroll everybody else is
	waiting on. Same posture `end_shift` takes towards a shift with an unmet
	obligation: state it, keep it.
	"""
	context, slips, totals = _period_run(args, creating=True)
	if not slips:
		raise ToolError(
			f"no payroll slips could be computed for {context['company']} between "
			f"{context['pay_period_start']} and {context['pay_period_end']}. "
			+ (
				"Workers were on shift but none of them has an active salary structure — "
				f"{', '.join(row['employee_name'] for row in context['employees_missing_structures'])}. "
				"create_salary_structure is what makes their hours payable."
				if context["employees_missing_structures"]
				else "No shifts and no active salary structures in this period. Nothing was created."
			)
		)

	entry = frappe.get_doc({
		"doctype": PAYROLL_ENTRY,
		"company": context["company"],
		"pay_period_start": context["pay_period_start"],
		"pay_period_end": context["pay_period_end"],
		"pay_frequency": context["pay_frequency"],
		"status": "Draft",
	})

	for slip in slips:
		entry.append("slips", _slip_row(slip))

	entry.total_gross = totals["total_gross"]
	entry.total_deductions = totals["total_deductions"]
	entry.total_net = totals["total_net"]
	entry.employee_count = totals["employee_count"]
	entry.status = "Calculated"
	entry.flags.ignore_permissions = True
	entry.insert()

	data = dict(context)
	data["name"] = entry.name
	data["status"] = "Calculated"
	data["totals"] = totals
	data["slips"] = [_slip_view(slip, False) for slip in slips]

	return ToolResult(
		data=data,
		summary=(
			f"Payroll entry {entry.name} for {context['company']}, "
			f"{context['pay_period_start']} to {context['pay_period_end']}: "
			f"{totals['employee_count']} employee(s), {totals['total_hours']}h "
			f"({totals['total_overtime_hours']}h OT), gross ${totals['total_gross']}, "
			f"net ${totals['total_net']}"
			+ (
				f" — {len(totals['below_minimum_wage'])} below minimum wage"
				if totals["below_minimum_wage"] else ""
			)
		),
		docstatus_delta="none → Calculated",
	)


def _slip_row(slip: dict) -> dict:
	"""One computed slip, as a Farm Payroll Slip child row."""
	detail = dict(slip.get("state_taxes_detail") or {})
	detail["_wages_by_state"] = slip.get("state_wages") or {}
	detail["_hours_by_state"] = slip.get("hours_by_state") or {}
	detail["_minimum_wage"] = slip.get("minimum_wage_detail") or {}

	minimum = slip.get("minimum_wage_detail") or {}
	meets = minimum.get("meets_minimum_wage")
	if meets is None:
		meets = slip.get("minimum_wage_check", True)

	return {
		"employee": slip.get("employee"),
		"employee_name": slip.get("employee_name"),
		"salary_structure": slip.get("salary_structure"),
		"pay_type": slip.get("pay_type"),
		"work_state": slip.get("work_state"),
		"total_hours": slip.get("total_hours"),
		"regular_hours": slip.get("regular_hours"),
		"overtime_hours": slip.get("overtime_hours"),
		"piece_units": slip.get("piece_units"),
		"piece_rate": slip.get("piece_rate"),
		"gross_pay": slip.get("gross_pay"),
		"federal_withholding": slip.get("federal_withholding"),
		"state_withholding": slip.get("state_withholding"),
		"social_security": slip.get("social_security"),
		"medicare": slip.get("medicare"),
		"state_taxes_detail": json.dumps(detail, default=str),
		"total_deductions": slip.get("total_deductions"),
		"net_pay": slip.get("net_pay"),
		"minimum_wage_check": 1 if meets else 0,
		"effective_hourly_rate": slip.get("effective_hourly_rate"),
	}


# ── Internal helpers ──────────────────────────────────────────────────────


def _load_period_shifts(
	company: str | None,
	start: str,
	end: str,
	employees: list[str] | None = None,
) -> tuple[list[dict], dict]:
	"""Farm Shifts in the window, each with its crew and its piece counts.

	Returns the shift dicts in the shape `payroll_integration` reads, plus a
	provenance dict naming every source that answered and every source that was
	not on this site. The second half is not decoration: a piece-rate run that
	found no bucket log has produced a set of zeros, and whether that means
	nobody picked anything or means the bridge is not installed is the whole
	difference between a payroll and a mistake.
	"""
	provenance: dict = {"sources": [], "notes": []}

	if not compat.doctype_exists(FARM_SHIFT):
		provenance["notes"].append(
			f"The {FARM_SHIFT} doctype is not on this site, so no hours could be read from "
			"the shift register. Run `bench migrate` after installing v0.19.3 or later."
		)
		return [], provenance

	filters: list = [
		["start_datetime", ">=", f"{start} 00:00:00"],
		["start_datetime", "<=", f"{end} 23:59:59"],
	]
	if company:
		filters.append(["company", "=", company])

	rows = frappe.db.get_all(
		FARM_SHIFT,
		filters=filters,
		fields=[
			"name", "company", "work_state", "start_datetime", "end_datetime",
			"status", "cancelled", "shift_type", "location",
		],
		order_by="start_datetime asc",
		limit_page_length=SHIFT_CAP,
	)

	kept = []
	cancelled = 0
	for row in rows:
		if compat.checked(row.get("cancelled")) or row.get("status") == "Cancelled":
			cancelled += 1
			continue
		kept.append(row)

	if len(rows) >= SHIFT_CAP:
		provenance["notes"].append(
			f"The shift read stopped at {SHIFT_CAP} records. This period covers more shifts "
			"than one payroll run should — check the dates before paying anything out."
		)
	if cancelled:
		provenance["notes"].append(
			f"{cancelled} cancelled shift(s) in this period were not counted. A cancelled "
			"shift is a shift that did not happen."
		)

	# A per-worker piece count on the crew row is not a column this app ships —
	# Farm Shift Crew Member records who and when, not how much. A site that has
	# added one is recording exactly the right thing in exactly the right place,
	# so it is read where it exists rather than ignored in favour of a bridge
	# doctype that may not be installed.
	units_field = compat.first_field(
		CREW_MEMBER, "piece_units", "units", "bins", "bucket_count", "quantity",
	)
	break_field = compat.first_field(CREW_MEMBER, "break_hours", "paid_break_hours")
	meal_field = compat.first_field(CREW_MEMBER, "unpaid_break_hours", "meal_break_hours")

	names = [row["name"] for row in kept]
	crew_by_shift: dict[str, list[dict]] = {}
	if names:
		wanted = set(employees) if employees else None
		fields = ["parent", "employee", "employee_name", "joined_at", "left_at"]
		fields += [f for f in (units_field, break_field, meal_field) if f]
		for member in frappe.db.get_all(
			CREW_MEMBER,
			filters={"parent": ("in", names)},
			fields=fields,
			limit_page_length=0,
		):
			if not member.get("employee"):
				continue
			if wanted is not None and member["employee"] not in wanted:
				continue
			row = {
				"employee": member["employee"],
				"employee_name": member.get("employee_name") or "",
				"joined_at": member.get("joined_at"),
				"left_at": member.get("left_at"),
			}
			if units_field and member.get(units_field) not in (None, ""):
				row["piece_units"] = _num(member.get(units_field))
			if break_field and member.get(break_field) not in (None, ""):
				row["break_hours"] = _num(member.get(break_field))
			if meal_field and member.get(meal_field) not in (None, ""):
				row["unpaid_break_hours"] = _num(member.get(meal_field))
			crew_by_shift.setdefault(member["parent"], []).append(row)

	shifts = []
	for row in kept:
		crew = crew_by_shift.get(row["name"])
		if not crew:
			continue
		shifts.append({
			"name": row["name"],
			"company": row.get("company"),
			"work_state": row.get("work_state") or "",
			"shift_type": row.get("shift_type"),
			"location": row.get("location"),
			"start_datetime": row.get("start_datetime"),
			"end_datetime": row.get("end_datetime"),
			"crew": crew,
		})

	provenance["sources"].append(FARM_SHIFT)
	provenance["shift_count"] = len(shifts)

	piece_rows, piece_provenance = _load_piece_rows(company, start, end, employees)
	provenance["sources"].extend(piece_provenance["sources"])
	provenance["notes"].extend(piece_provenance["notes"])

	unmatched = _attach_piece_rows(shifts, piece_rows)
	if unmatched:
		shifts.extend(_orphan_piece_shifts(unmatched, shifts))
		provenance["piece_rows_without_a_shift"] = len(unmatched)
		provenance["notes"].append(
			f"{len(unmatched)} piece record(s) fell on a day the worker has no shift on. They "
			"are PAID — the buckets are in the barn either way — but they carry no hours, so "
			"they add nothing to the overtime count and nothing to the minimum wage divisor. "
			"A day of picking with no shift behind it is a gap in the shift register."
		)
	provenance["piece_row_count"] = len(piece_rows)

	return shifts, provenance


def _load_piece_rows(
	company: str | None,
	start: str,
	end: str,
	employees: list[str] | None = None,
) -> tuple[list[dict], dict]:
	"""Piece counts for the period, from whichever source this site has.

	Two candidate sources, both optional and neither owned by this app, so both
	are inspected rather than assumed: the BucketLog bridge's Bucket Log Entry,
	and Farm Task Assignment where the site has added a count column to it. What
	is absent is named in the notes.
	"""
	provenance: dict = {"sources": [], "notes": []}
	rows: list[dict] = []
	wanted = set(employees) if employees else None

	rows.extend(_bucket_log_rows(company, start, end, wanted, provenance))
	rows.extend(_task_assignment_rows(company, start, end, wanted, provenance))
	return rows, provenance


def _bucket_log_rows(company, start, end, wanted, provenance) -> list[dict]:
	"""Piece counts off the BucketLog bridge, or a note saying why there are none."""
	if not compat.doctype_exists(BUCKET_LOG):
		provenance["notes"].append(
			f"The {BUCKET_LOG} doctype (the BucketLog bridge) is not on this site, so no "
			"bucket counts were read. Piece-rate pay for this period comes from whatever "
			"units were recorded elsewhere."
		)
		return []

	picker_field = compat.first_field(BUCKET_LOG, "picker_id", "employee", "picker", "worker")
	if not picker_field:
		provenance["notes"].append(
			f"{BUCKET_LOG} is installed but carries no picker column on this site, so a "
			"bucket cannot be attributed to anybody. `import_farm_app_fields` installs the "
			"five traceability columns, of which picker_id is one."
		)
		return []

	date_field = compat.first_field(
		BUCKET_LOG, "logged_at", "log_date", "date", "posting_date", "creation",
	)
	unit_field = compat.first_field(
		BUCKET_LOG, "piece_units", "units", "quantity", "qty", "bucket_count", "count", "bins",
	)

	filters: list = []
	if date_field:
		filters.append([date_field, ">=", f"{start} 00:00:00"])
		filters.append([date_field, "<=", f"{end} 23:59:59"])
	if company and compat.has_field(BUCKET_LOG, "company"):
		filters.append(["company", "=", company])

	fields = ["name", picker_field]
	if date_field:
		fields.append(date_field)
	if unit_field:
		fields.append(unit_field)

	rows = frappe.db.get_all(
		BUCKET_LOG, filters=filters, fields=fields, limit_page_length=0,
	)

	out = []
	for row in rows:
		employee = str(row.get(picker_field) or "")
		if not employee or (wanted is not None and employee not in wanted):
			continue
		# A row with no count column IS one bucket. The row is the record of the
		# bucket, so counting it as one is reading it, not guessing at it.
		units = 1.0 if not unit_field else _num(row.get(unit_field), 1.0)
		out.append({
			"employee": employee,
			"date": str(row.get(date_field) or "")[:10] if date_field else "",
			"units": units,
			"source": BUCKET_LOG,
		})

	provenance["sources"].append(BUCKET_LOG)
	if not unit_field:
		provenance["notes"].append(
			f"{BUCKET_LOG} carries no count column on this site, so each entry was counted as "
			"ONE unit. That is what the record means — one row, one bucket — but a site that "
			"logs a bin per row is being paid per bin at the per-bucket rate."
		)
	return out


def _task_assignment_rows(company, start, end, wanted, provenance) -> list[dict]:
	"""Piece counts off completed Farm Task Assignments, where the site has them."""
	if not compat.doctype_exists(FARM_TASK_ASSIGNMENT):
		return []

	unit_field = compat.first_field(
		FARM_TASK_ASSIGNMENT,
		"piece_units", "units", "units_completed", "quantity", "qty", "bins", "bucket_count",
	)
	if not unit_field:
		provenance["notes"].append(
			f"{FARM_TASK_ASSIGNMENT} carries no piece-count column on this site, so completed "
			"field work contributed no units. Task completion records duration, not output; a "
			"site paying piece rate off tasks needs a count column on the assignment."
		)
		return []

	filters: list = [
		["completed_at", ">=", f"{start} 00:00:00"],
		["completed_at", "<=", f"{end} 23:59:59"],
		["state", "=", "Completed"],
	]
	if company:
		filters.append(["company", "=", company])

	rows = frappe.db.get_all(
		FARM_TASK_ASSIGNMENT,
		filters=filters,
		fields=["name", "assigned_to", "completed_at", unit_field],
		limit_page_length=0,
	)

	out = []
	for row in rows:
		employee = str(row.get("assigned_to") or "")
		if not employee or (wanted is not None and employee not in wanted):
			continue
		out.append({
			"employee": employee,
			"date": str(row.get("completed_at") or "")[:10],
			"units": _num(row.get(unit_field), 0.0),
			"source": FARM_TASK_ASSIGNMENT,
		})

	provenance["sources"].append(FARM_TASK_ASSIGNMENT)
	return out


def _attach_piece_rows(shifts: list[dict], piece_rows: list[dict]) -> list[dict]:
	"""Hang each piece row on the shift its worker was on that day.

	Matched by (employee, date) rather than by a link, because neither candidate
	source has one — a bucket log knows a picker and a timestamp, and the shift
	that timestamp fell inside is a join this app has to make for itself.

	Returns the rows that matched nothing, for the caller to report.
	"""
	index: dict[tuple, dict] = {}
	for shift in shifts:
		day = str(shift.get("start_datetime") or "")[:10]
		for member in shift.get("crew") or []:
			index.setdefault((member["employee"], day), shift)

	unmatched = []
	for row in piece_rows:
		shift = index.get((row["employee"], row["date"]))
		if shift is None:
			unmatched.append(row)
			continue
		shift.setdefault("piece_rows", []).append({
			"employee": row["employee"],
			"piece_units": row["units"],
		})
	return unmatched


def _orphan_piece_shifts(unmatched: list[dict], shifts: list[dict]) -> list[dict]:
	"""Zero-hour stand-in shifts so unmatched piece work is still paid.

	The alternative is dropping the units, and dropping somebody's buckets
	because the shift register has a hole in it would be paying them nothing for
	a day they worked. The stand-in carries the units and NO hours, which is the
	honest shape: the money is owed, the time was never recorded, and both facts
	survive into the slip.

	The work state is taken from where that worker spent their other hours in the
	period, because a bucket log does not carry one and the alternative is
	allocating the earnings to no state at all.
	"""
	dominant: dict[str, dict[str, float]] = {}
	for shift in shifts:
		state = shift.get("work_state") or ""
		if not state:
			continue
		for member in shift.get("crew") or []:
			tally = dominant.setdefault(member["employee"], {})
			tally[state] = tally.get(state, 0) + 1

	grouped: dict[tuple, float] = {}
	for row in unmatched:
		key = (row["employee"], row["date"])
		grouped[key] = grouped.get(key, 0.0) + row["units"]

	out = []
	for (employee, day), units in sorted(grouped.items()):
		tally = dominant.get(employee) or {}
		state = max(tally, key=tally.get) if tally else ""
		when = f"{day} 00:00:00" if day else None
		out.append({
			"name": "",
			"work_state": state,
			"start_datetime": when,
			"end_datetime": when,
			"piece_units_only": True,
			"crew": [{"employee": employee, "hours": 0.0, "piece_units": units}],
		})
	return out


def _load_shifts(employee: str, start: str, end: str, company: str | None = None) -> list[dict]:
	"""One employee's period, as the per-state rows `calculate_full_payroll` reads.

	The v0.30.0 entry point, rewired. It answers the same question with the same
	shape and gets it right now: per-worker spans rather than the crew's, weekly
	overtime rather than none, and piece units rather than zero.

	It still swallows its own failures. A payroll preview on a site whose shift
	register is half-migrated should report no hours, not refuse to run — the
	same posture the Attendance bridge takes on a close.
	"""
	try:
		shifts, _ = _load_period_shifts(company, start, end, employees=[employee])
		aggregates = payroll_integration.aggregate_shifts_for_period(shifts, start, end)
		aggregate = aggregates.get(employee)
		if not aggregate:
			return []
		return payroll_integration.engine_shift_rows(aggregate)
	except Exception:
		return []


def _load_structures(company: str, employees: list[str] | None = None) -> dict:
	"""Active salary structures for a company, keyed by employee.

	Newest `effective_from` wins where a site has left two active. Deactivating
	the old one is what `deactivate_salary_structure` is for, but a payroll run
	is the wrong moment to refuse over it — the most recent rate is the one the
	employer meant, and the duplicate is a data problem for another tool.
	"""
	filters: dict = {"company": company, "is_active": 1}
	if employees:
		filters["employee"] = ("in", employees)

	rows = frappe.db.get_all(
		SALARY_STRUCTURE,
		filters=filters,
		fields=["name", "employee", "employee_name", "pay_type", "base_rate", "effective_from"],
		order_by="effective_from asc",
		limit_page_length=0,
	)

	structures: dict[str, dict] = {}
	for row in rows:
		employee = row.get("employee")
		if not employee:
			continue
		structures[employee] = {
			"name": row.get("name"),
			"employee": employee,
			"employee_name": row.get("employee_name") or "",
			"pay_type": row.get("pay_type") or "Hourly",
			"base_rate": _num(row.get("base_rate")),
		}
	return structures


def _load_w4_map(employees: list[str]) -> dict:
	"""Each employee's active W-4, or the single-no-adjustments default."""
	return {employee: _load_w4_data(employee) for employee in employees}


def _load_state_configs(company: str | None = None) -> dict:
	"""Every supported state's tax configuration this company has."""
	configs = {}
	for state in SUPPORTED_STATES:
		config = _load_state_config(state, company)
		if config:
			configs[state] = config
	return configs


def _load_state_tax_tables(tax_year: int = 2025, filing_status: str = "Single") -> dict:
	"""State income tax brackets, for the states that have them.

	Only Oregon: Washington has no personal income tax, so there is no table to
	load and `state_withholding` never asks for one.
	"""
	tables = {}
	for state in SUPPORTED_STATES:
		if state != "OR":
			continue
		rows = _load_state_tax_table(state, tax_year, filing_status)
		if rows:
			tables[state] = rows
	return tables


def _load_federal_tables(pay_frequency: str, tax_year: int = 2025) -> dict:
	"""Federal brackets for every filing status, keyed by status.

	Loaded once per run rather than once per employee. A company of forty people
	is forty identical bracket reads otherwise, and they are the same three
	tables every time.
	"""
	tables = {}
	for status in sorted(set(FILING_STATUS_MAP.values())):
		rows = _load_federal_tax_table(tax_year, status, pay_frequency)
		if rows:
			tables[status] = rows
	return tables


def _load_ytd(company: str, period_start: str, employees: list[str]) -> dict:
	"""Year-to-date gross and Social Security withheld, before this period.

	The Social Security wage base is an annual per-person cap, so a period run
	in isolation would restart it and over-withhold on anybody who has already
	crossed $176,100. Only `Calculated` and `Submitted` entries count — a Draft
	payroll has not been paid and a Cancelled one was not, which is the same rule
	`tools/taxforms.py` applies for the same reason.
	"""
	year = str(period_start)[:4]
	wanted = set(employees)
	ytd: dict[str, dict] = {}

	entries = frappe.db.get_all(
		PAYROLL_ENTRY,
		filters={
			"company": company,
			"status": ("in", ("Calculated", "Submitted")),
			"pay_period_end": ("<", str(period_start)),
		},
		fields=["name", "pay_period_start", "pay_period_end"],
		order_by="pay_period_end asc",
		limit_page_length=0,
	)

	for entry in entries:
		if str(entry.get("pay_period_end") or "")[:4] != year:
			continue
		try:
			doc = frappe.get_doc(PAYROLL_ENTRY, entry["name"])
		except Exception:
			continue
		for row in doc.get("slips") or []:
			get = row.get if isinstance(row, dict) else (lambda k, d=None: getattr(row, k, d))
			employee = get("employee")
			if not employee or employee not in wanted:
				continue
			bucket = ytd.setdefault(employee, {"ytd_gross": 0.0, "ytd_ss_withheld": 0.0})
			bucket["ytd_gross"] += _num(get("gross_pay"))
			bucket["ytd_ss_withheld"] += _num(get("social_security"))

	return {
		employee: {k: round(v, 2) for k, v in bucket.items()}
		for employee, bucket in ytd.items()
	}


def _build_tax_config(employee: str, pay_frequency: str, company: str | None = None) -> dict:
	"""Build the tax_config dict needed by calculate_full_payroll."""
	w4_data = _load_w4_data(employee)
	fica_config = _load_fica_config()
	filing_status = w4_data.get("filing_status", "Single")
	federal_tax_table = _load_federal_tax_table(
		w4_data.get("_tax_year", 2025), filing_status, pay_frequency,
	)

	state_configs = {}
	state_tax_tables = {}
	for state in SUPPORTED_STATES:
		sc = _load_state_config(state, company)
		if sc:
			state_configs[state] = sc
			if state == "OR":
				st = _load_state_tax_table(state, w4_data.get("_tax_year", 2025), filing_status)
				if st:
					state_tax_tables[state] = st

	return {
		"w4_data": w4_data,
		"fica_config": fica_config,
		"federal_tax_table": federal_tax_table,
		"pay_frequency": pay_frequency,
		"ytd_gross": 0.0,
		"ytd_ss_withheld": 0.0,
		"state_configs": state_configs,
		"state_tax_tables": state_tax_tables,
	}


def _load_w4_data(employee: str) -> dict:
	"""Load the active W-4 for an employee."""
	name = frappe.db.get_value(
		W4_FORM, {"employee": employee, "status": "Active"}, "name",
		order_by="tax_year desc, effective_date desc",
	)
	if not name:
		return {
			"filing_status": "Single",
			"multiple_jobs": False,
			"additional_income_from_other_jobs": 0,
			"dependents_under_17_count": 0,
			"other_dependents_count": 0,
			"total_dependents_credit": 0,
			"other_income": 0,
			"deductions": 0,
			"extra_withholding_per_period": 0,
			"_tax_year": 2025,
		}

	fields = [
		"tax_year", "filing_status", "multiple_jobs",
		"additional_income_from_other_jobs",
		"dependents_under_17_count", "other_dependents_count",
		"total_dependents_credit", "other_income", "deductions",
		"extra_withholding_per_period",
	]
	row = frappe.db.get_value(W4_FORM, name, fields, as_dict=True)
	raw_status = row.get("filing_status", "Single or Married Filing Separately")
	table_status = FILING_STATUS_MAP.get(raw_status, "Single")

	return {
		"filing_status": table_status,
		"multiple_jobs": bool(int(row.get("multiple_jobs") or 0)),
		"additional_income_from_other_jobs": float(row.get("additional_income_from_other_jobs") or 0),
		"dependents_under_17_count": int(row.get("dependents_under_17_count") or 0),
		"other_dependents_count": int(row.get("other_dependents_count") or 0),
		"total_dependents_credit": float(row.get("total_dependents_credit") or 0),
		"other_income": float(row.get("other_income") or 0),
		"deductions": float(row.get("deductions") or 0),
		"extra_withholding_per_period": float(row.get("extra_withholding_per_period") or 0),
		"_tax_year": int(row.get("tax_year") or 2025),
	}


def _load_fica_config() -> dict:
	"""Load FICA configuration as a plain dict."""
	try:
		doc = frappe.get_doc(FICA_CONFIG)
	except Exception:
		return {
			"social_security_rate_employee": 6.2,
			"social_security_rate_employer": 6.2,
			"social_security_wage_base": 176100,
			"medicare_rate_employee": 1.45,
			"medicare_rate_employer": 1.45,
			"additional_medicare_threshold": 200000,
			"additional_medicare_rate": 0.9,
			"futa_rate": 6.0,
			"futa_wage_base": 7000,
			"futa_state_credit_max": 5.4,
		}

	return {
		"social_security_rate_employee": float(doc.social_security_rate_employee or 6.2),
		"social_security_rate_employer": float(doc.social_security_rate_employer or 6.2),
		"social_security_wage_base": float(doc.social_security_wage_base or 176100),
		"medicare_rate_employee": float(doc.medicare_rate_employee or 1.45),
		"medicare_rate_employer": float(doc.medicare_rate_employer or 1.45),
		"additional_medicare_threshold": float(doc.additional_medicare_threshold or 200000),
		"additional_medicare_rate": float(doc.additional_medicare_rate or 0.9),
		"futa_rate": float(doc.futa_rate or 6.0),
		"futa_wage_base": float(doc.futa_wage_base or 7000),
		"futa_state_credit_max": float(doc.futa_state_credit_max or 5.4),
	}


def _load_federal_tax_table(tax_year: int, filing_status: str, pay_frequency: str) -> list[dict]:
	"""Load federal tax brackets."""
	rows = frappe.db.get_all(
		FEDERAL_TAX_TABLE,
		filters={
			"tax_year": tax_year,
			"filing_status": filing_status,
			"payroll_period": pay_frequency,
		},
		fields=["bracket_floor", "bracket_ceiling", "base_tax", "marginal_rate"],
		order_by="bracket_floor asc",
	)
	return [dict(r) for r in rows]


def _load_state_config(state: str, company: str | None = None) -> dict | None:
	"""Load state tax configuration."""
	filters = {"state": state, "status": "Active"}
	if company:
		filters["company"] = company

	name = frappe.db.get_value(STATE_TAX_CONFIG, filters, "name")
	if not name:
		return None

	doc = frappe.get_doc(STATE_TAX_CONFIG, name)
	return doc.as_dict()


def _load_state_tax_table(state: str, tax_year: int, filing_status: str) -> list[dict]:
	"""Load state income tax brackets."""
	rows = frappe.db.get_all(
		STATE_TAX_TABLE,
		filters={
			"state": state,
			"tax_year": tax_year,
			"filing_status": filing_status,
		},
		fields=["bracket_floor", "bracket_ceiling", "base_tax", "marginal_rate"],
		order_by="bracket_floor asc",
	)
	return [dict(r) for r in rows]
