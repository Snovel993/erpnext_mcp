# SPDX-License-Identifier: MIT
"""Salary structure + payroll tools.

v0.30.0. Links the calc engine to the MCP surface. Read tools are dry-run
previews and lookups; mutating tools create salary structures and payroll
entries.
"""
from __future__ import annotations

import json

import frappe
from frappe.utils import today

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
	shifts = _load_shifts(employee, pay_period_start, pay_period_end)

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
		shifts = _load_shifts(employee, pay_period_start, pay_period_end)
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


# ── Internal helpers ──────────────────────────────────────────────────────


def _load_shifts(employee: str, start: str, end: str) -> list[dict]:
	"""Load Farm Shift data for an employee in a date range."""
	try:
		# Get shifts where this employee was a crew member
		shift_names = frappe.db.get_all(
			"Farm Shift Crew Member",
			filters={"employee": employee},
			fields=["parent"],
			pluck="parent",
		)
		if not shift_names:
			return []

		shifts = frappe.db.get_all(
			FARM_SHIFT,
			filters={
				"name": ("in", shift_names),
				"start_datetime": (">=", start),
				"start_datetime": ("<=", f"{end} 23:59:59"),
			},
			fields=["name", "work_state", "start_datetime", "end_datetime"],
		)

		result = []
		for shift in shifts:
			hours = 0.0
			if shift.get("start_datetime") and shift.get("end_datetime"):
				from datetime import datetime
				s = shift["start_datetime"]
				e = shift["end_datetime"]
				if isinstance(s, str):
					s = datetime.fromisoformat(s)
				if isinstance(e, str):
					e = datetime.fromisoformat(e)
				hours = (e - s).total_seconds() / 3600.0

			result.append({
				"work_state": shift.get("work_state", ""),
				"hours": round(hours, 2),
				"overtime_hours": 0.0,
				"piece_units": 0.0,
				"break_hours": 0.0,
			})
		return result
	except Exception:
		return []


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
