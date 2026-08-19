# SPDX-License-Identifier: MIT
"""HR tools. Present only on sites that have the `hrms` app installed.

These three are gated by an availability predicate rather than by degrading at
call time: on a site without `hrms` they do not appear in `tools/list` at all.
The difference matters for a model, which decides what is possible from the
catalogue — a tool that is listed and always fails is a trap, and a client that
sees `get_leave_balance` will eventually try to answer a leave question with it.

`hrms` was split out of ERPNext at v14, so on older sites the same doctypes live
under `erpnext.hr`. `_leave_balance_api` looks in both places before giving up,
which is the same "ask, do not assume" rule the rest of `compat` follows.

WHY THE SUMMARY IS AGGREGATED. `get_attendance_summary` returns counts per
employee, not one row per day. A month of attendance for forty people is 1,200
rows that say nothing an aggregate does not, and the question behind the call is
always "who was out".
"""

import frappe

from .. import compat, minors
from ..args import as_date, as_filter, as_limit, as_str
from ..errors import ToolError
from ..result import ToolResult

#: The statuses ERPNext's Attendance doctype uses. Anything else a site has added
#: is counted under its own name rather than dropped.
ATTENDANCE_STATUSES = ("Present", "Absent", "Half Day", "On Leave", "Work From Home")


# ── 28. list_employees ──────────────────────────────────────────────────────
def list_employees(args: dict) -> ToolResult:
	"""Employee records, active ones by default."""
	compat.require_doctype("Employee", "It comes with the Frappe HR (hrms) app.")
	status = as_filter(args, "status", default="Active")
	department = as_str(args, "department")
	designation = as_str(args, "designation")
	company = as_str(args, "company")
	limit = as_limit(args)

	filters = {}
	if status:
		filters["status"] = status
	if department:
		filters["department"] = department
	if designation:
		filters["designation"] = designation
	if company:
		filters["company"] = company

	fields = compat.existing_fields(
		"Employee",
		[
			"name",
			"employee_name",
			"employee_number",
			"department",
			"designation",
			"status",
			"date_of_joining",
			"relieving_date",
			"company",
			"user_id",
			"reports_to",
			"branch",
			"employment_type",
			# v0.98.0. FETCHED SO `is_minor` CAN BE DERIVED, and derived rather
			# than stored — see `minors.py`. Through `compat.existing_fields` like
			# everything else here, so a site whose Employee has been customised
			# without it loses the two derived keys rather than the read.
			"date_of_birth",
		],
	)
	rows = frappe.db.get_all(
		"Employee", filters=filters, fields=fields, order_by="employee_name asc", limit=limit
	)

	today = frappe.utils.today()
	by_department = {}
	minor_count = 0
	for row in rows:
		key = row.get("department") or "<none>"
		by_department[key] = by_department.get(key, 0) + 1
		row.update(minors.describe(row.get("date_of_birth"), today))
		if row.get("is_minor"):
			minor_count += 1

	data = {
		"employees": rows,
		"count": len(rows),
		"limit": limit,
		"truncated": len(rows) == limit,
		"by_department": by_department,
		"filters": {
			"status": status or "any",
			"department": department or None,
			"designation": designation or None,
			"company": company or None,
		},
		"minors": minor_count,
		"note": (
			"`name` is the Employee docname (HR-EMP-…); `employee_number` is the "
			"payroll number an operator will recognise. Pass `name` to the other "
			"HR tools."
		),
		"minor_note": (
			"`is_minor` is DERIVED from `date_of_birth` on every read and is stored nowhere — a "
			"fifteen-year-old hired in April is sixteen in July, and a ticked column would still "
			"say otherwise. It is THREE-VALUED: null means no date of birth is on file, which is "
			"not the same as an adult. `minor_band` is under-16 or 16-17, because the hour and "
			"time-of-day limits differ between them, and `minor_limits` carries the ceiling with "
			"its citation."
		),
	}
	return ToolResult(
		data,
		f"{len(rows)} employee(s) ({status or 'any status'})"
		+ (f", {minor_count} under 18" if minor_count else ""),
	)


# ── 29. get_attendance_summary ──────────────────────────────────────────────
def get_attendance_summary(args: dict) -> ToolResult:
	"""Per-employee counts of each attendance status over a date range."""
	compat.require_doctype("Attendance", "It comes with the Frappe HR (hrms) app.")
	from_date = as_date(args, "from_date", required=True)
	to_date = as_date(args, "to_date", required=True)
	if from_date > to_date:
		raise ToolError(f"from_date {from_date} is after to_date {to_date}")
	employee = as_str(args, "employee")
	department = as_str(args, "department")

	filters = {"attendance_date": ("between", [from_date, to_date])}
	# Attendance is submittable, and a draft or cancelled row is not a fact about
	# whether somebody turned up.
	filters["docstatus"] = 1
	if employee:
		filters["employee"] = _resolve_employee(employee)
	if department and compat.has_field("Attendance", "department"):
		filters["department"] = department

	fields = compat.existing_fields(
		"Attendance",
		["employee", "employee_name", "attendance_date", "status", "department", "company"],
	)
	rows = frappe.db.get_all("Attendance", filters=filters, fields=fields, order_by="employee asc")

	summary, totals = {}, {}
	for row in rows:
		key = row.get("employee")
		entry = summary.setdefault(
			key,
			{
				"employee": key,
				"employee_name": row.get("employee_name"),
				"department": row.get("department"),
				"counts": {},
				"total_marked": 0,
			},
		)
		status = row.get("status") or "<unset>"
		entry["counts"][status] = entry["counts"].get(status, 0) + 1
		entry["total_marked"] += 1
		totals[status] = totals.get(status, 0) + 1

	# Give every employee the full set of keys so a model does not have to guess
	# whether a missing key means zero or means "this site does not track it".
	seen_statuses = sorted(set(totals) | set(ATTENDANCE_STATUSES))
	for entry in summary.values():
		entry["counts"] = {status: entry["counts"].get(status, 0) for status in seen_statuses}

	employees = sorted(summary.values(), key=lambda entry: entry["employee_name"] or "")
	data = {
		"from_date": from_date,
		"to_date": to_date,
		"employees": employees,
		"employee_count": len(employees),
		"totals": {status: totals.get(status, 0) for status in seen_statuses},
		"records_counted": len(rows),
		"statuses": seen_statuses,
		"filters": {"employee": filters.get("employee"), "department": department or None},
		"note": (
			"Submitted Attendance only (docstatus 1) — drafts and cancelled rows "
			"are not evidence of anything. Days with no Attendance record at all "
			"are absent from these counts rather than counted as Absent."
		),
	}
	return ToolResult(
		data,
		f"attendance {from_date}..{to_date}: {len(employees)} employee(s), {len(rows)} record(s)",
	)


# ── 30. get_leave_balance ───────────────────────────────────────────────────
def get_leave_balance(args: dict) -> ToolResult:
	"""Remaining leave for one employee, per leave type.

	Uses HR's own `get_leave_balance_on`, which nets allocations against
	applications and handles carry-forward and expiry. Those rules are the whole
	difficulty of the question, and a subtraction done here would be confidently
	wrong on any site with a carry-forward policy.
	"""
	compat.require_doctype("Leave Allocation", "It comes with the Frappe HR (hrms) app.")
	employee = _resolve_employee(as_str(args, "employee", required=True))
	leave_type = as_str(args, "leave_type")
	as_of = as_date(args, "as_of") or frappe.utils.today()

	balance_on = _leave_balance_api()
	if balance_on is None:
		raise ToolError(
			"this site's HR app does not export get_leave_balance_on, so a leave "
			"balance cannot be computed correctly here. Use the Leave Balance "
			"report via run_report instead."
		)

	types = [leave_type] if leave_type else _allocated_leave_types(employee, as_of)
	if leave_type and not frappe.db.exists("Leave Type", leave_type):
		raise ToolError(
			f"no Leave Type named {leave_type!r}. This employee has allocations "
			f"for: {', '.join(_allocated_leave_types(employee, as_of)) or '<none>'}"
		)

	balances, failed = [], []
	for name in types:
		try:
			balance = balance_on(employee, name, as_of)
		except Exception as exc:
			# One misconfigured leave type must not take the whole answer down.
			failed.append({"leave_type": name, "error": f"{type(exc).__name__}: {exc}"})
			continue
		balances.append({"leave_type": name, "balance": _number(balance)})

	employee_name = frappe.db.get_value("Employee", employee, "employee_name")
	data = {
		"employee": employee,
		"employee_name": employee_name,
		"as_of": as_of,
		"balances": balances,
		"count": len(balances),
		"total_balance": round(sum(row["balance"] for row in balances), 3),
		"leave_type_filter": leave_type or None,
		"computed_via": "HR get_leave_balance_on",
	}
	if failed:
		data["failed"] = failed
	return ToolResult(
		data,
		f"{employee_name or employee} leave balance as of {as_of}: "
		f"{len(balances)} type(s), {data['total_balance']} day(s) total",
	)


# ── shared ──────────────────────────────────────────────────────────────────
def _resolve_employee(value: str) -> str:
	"""An Employee docname from a docname, an employee_number, or a name."""
	value = (value or "").strip()
	if not value:
		raise ToolError("employee is required")
	if frappe.db.exists("Employee", value):
		return value
	for filters in (
		{"employee_number": value},
		{"employee_name": value},
		{"user_id": value},
	):
		matches = frappe.db.get_all("Employee", filters=filters, pluck="name", limit=10)
		if len(matches) == 1:
			return matches[0]
		if len(matches) > 1:
			raise ToolError(
				f"{value!r} matches {len(matches)} employees: "
				f"{', '.join(sorted(matches))}. Pass the Employee docname."
			)
	raise ToolError(
		f"no Employee matching {value!r} (tried docname, employee_number, "
		"employee_name and user_id). Use list_employees to find it."
	)


def _allocated_leave_types(employee: str, as_of: str) -> list[str]:
	"""Leave types this employee has an allocation covering `as_of`.

	Better than listing every Leave Type on the site: a balance for a type nobody
	allocated is always zero and only adds noise.
	"""
	filters = {
		"employee": employee,
		"docstatus": 1,
		"from_date": ("<=", as_of),
		"to_date": (">=", as_of),
	}
	types = frappe.db.get_all("Leave Allocation", filters=filters, pluck="leave_type")
	return sorted(set(types))


def _leave_balance_api():
	"""HR's `get_leave_balance_on`, from wherever this site keeps it.

	`hrms` since v14; `erpnext.hr` before the split.
	"""
	paths = (
		"hrms.hr.doctype.leave_application.leave_application",
		"erpnext.hr.doctype.leave_application.leave_application",
	)
	for path in paths:
		try:
			module = __import__(path, fromlist=["get_leave_balance_on"])
		except Exception:
			continue
		function = getattr(module, "get_leave_balance_on", None)
		if callable(function):
			return function
	return None


def _number(value) -> float:
	try:
		return round(float(value or 0), 3)
	except (TypeError, ValueError):
		return 0.0
