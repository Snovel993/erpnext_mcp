# SPDX-License-Identifier: MIT
"""Payroll → the general ledger. Four tools, and only one of them writes.

v0.40.0. THE ACCOUNTING LOOP WAS OPEN. v0.30.0 computed payroll, v0.35.0 fed it
the shift register's hours, v0.36.0 drew the tax forms — and a completed run
produced Farm Payroll Slips and no Journal Entries. Wages are the largest number
on a farm's income statement and they were the one number somebody had to key in
twice, from a report, into the ledger, by hand, every fortnight.

THE FOUR TOOLS, AND WHY THE SPLIT IS WHERE IT IS.

`get_payroll_account_mapping` and `preview_payroll_gl` are READS and on by
default. The preview refuses NOTHING — an incomplete mapping is exactly what
somebody is calling it to find out about, and a preview that raised instead of
reporting would make the tool useless for the job it exists for.

`configure_payroll_accounts` and `post_payroll_to_gl` are MUTATING and off until
an operator turns them on. The posting creates DRAFT Journal Entries and stops,
the same as every other Journal Entry this app produces: `submit_journal_entry`
is a separate tool behind a separate switch, because arithmetic anybody can redo
and a statement that this is what the farm paid are different acts.

THE ARITHMETIC IS NOT HERE. Everything about which account a component lands on
and which side it lands on is `erpnext_mcp/payroll_gl.py`, which is pure and
reads no database. This module loads records, hands them over, and writes what
comes back — so the entry a preview describes and the entry a posting inserts
come out of one function and cannot drift.

POSTING TWICE IS THE FAILURE THIS GUARDS HARDEST AGAINST. A payroll posted
twice is a doubled wage expense and a doubled liability, and it is the easiest
mistake in the world to make from a chat client that lost its scrollback. Every
draft entry is linked back onto the Farm Payroll Entry's `gl_postings` table,
and a run whose postings are still live is refused by name. A run whose drafts
were deleted, or whose entries were cancelled, can be posted again — because
then there is genuinely nothing in the ledger.
"""

from __future__ import annotations

import frappe

from .. import compat, payroll_gl
from ..args import as_bool, as_date, as_str, resolve_account, resolve_company, resolve_cost_center
from ..errors import ToolError
from ..result import ToolResult
from . import mutate

MAPPING = "Farm Payroll Account Mapping"
MAP_ROW = "Farm Payroll Account Map Row"
PAYROLL_ENTRY = "Farm Payroll Entry"
GL_POSTING = "Farm Payroll GL Posting"
JOURNAL_ENTRY = "Journal Entry"
EMPLOYEE = "Employee"

#: The chain a cost center split walks. A Farm Task Assignment records who and
#: how long, the Farm Task it points at records WHERE through its
#: `location_doctype`/`location` pair, and a `Field` — what a farm calls a block —
#: records the Cost Center that block's costs book to.
FARM_TASK = "Farm Task"
FARM_TASK_ASSIGNMENT = "Farm Task Assignment"
BLOCK = "Field"

#: Most assignments one payroll period will be split from. A biweekly run on a
#: forty-person crew filing ten tasks a day each is under nine thousand; the cap
#: is what stops a period somebody typed wrong reading a season into memory.
ASSIGNMENT_CAP = 20000

#: What the split needs off one assignment. `state` is read for the report
#: rather than for a filter — see `_labor_allocations` for why no state is
#: excluded — and `actual_duration_minutes` is the segment sum, not a wall clock.
_ASSIGNMENT_FIELDS = (
	"name",
	"task",
	"assigned_to",
	"state",
	"started_at",
	"completed_at",
	"actual_duration_minutes",
)

#: Statuses a payroll run can be posted from. `Draft` has no computed slips and
#: `Cancelled` is a run somebody withdrew — booking either into the ledger would
#: be booking a payroll that does not exist.
POSTABLE_STATUSES = ("Calculated", "Submitted")

#: The slip fields the GL side reads. Everything `payroll_gl.component_amounts`
#: might look for, plus what the remarks and warnings are built from.
_SLIP_FIELDS = (
	"employee",
	"employee_name",
	"gross_pay",
	"federal_withholding",
	"state_withholding",
	"social_security",
	"medicare",
	"total_deductions",
	"net_pay",
	"social_security_employer",
	"medicare_employer",
	"futa",
	"state_unemployment",
	"state_employer_other",
	"total_employer_taxes",
	"minimum_wage_check",
	"minimum_wage_makeup",
	"earned_gross",
	# v0.101.0. The denominator of the cost center split: what payroll actually
	# paid this person for. Read off the slip rather than recounted off the shift
	# register, because this is the figure the gross was computed from.
	"total_hours",
)


def _num(value, default: float = 0.0) -> float:
	if value is None or value == "":
		return default
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _get(row, key, default=None):
	"""One field off a child row, whichever shape the framework handed back."""
	if isinstance(row, dict):
		return row.get(key, default)
	return getattr(row, key, default)


# ── Loading ───────────────────────────────────────────────────────────────


def _mapping_doc(company: str):
	"""The company's account mapping document, or None."""
	name = frappe.db.get_value(MAPPING, {"company": company}, "name")
	if not name:
		return None
	return frappe.get_doc(MAPPING, name)


def _mapping_rows(doc) -> list[dict]:
	"""The mapping's component rows as plain dicts."""
	if doc is None:
		return []
	rows = []
	for row in doc.get("components") or []:
		component = str(_get(row, "component") or "").strip()
		if not component:
			continue
		rows.append(
			{
				"component": component,
				"debit_account": str(_get(row, "debit_account") or "").strip(),
				"credit_account": str(_get(row, "credit_account") or "").strip(),
				"notes": str(_get(row, "notes") or ""),
			}
		)
	return rows


def _payroll_doc(args: dict):
	"""The Farm Payroll Entry named in the arguments."""
	name = as_str(args, "payroll_entry") or as_str(args, "name") or as_str(args, "entry")
	if not name:
		raise ToolError(
			"payroll_entry is required — the Farm Payroll Entry docname for the run to "
			"post. list_payroll_entries has them."
		)
	if not frappe.db.exists(PAYROLL_ENTRY, name):
		raise ToolError(f"no Farm Payroll Entry called {name!r} on this site.")
	return frappe.get_doc(PAYROLL_ENTRY, name)


def _slips_of(doc) -> tuple[list[dict], dict]:
	"""A payroll run's child rows as the slip dicts `payroll_gl` reads.

	Returns the slips and a map of employee → child row docname, so each
	per-employee entry's remark can name the exact Farm Payroll Slip it came out
	of rather than the run it was in.
	"""
	slips = []
	names = {}
	for row in doc.get("slips") or []:
		slip = {field: _get(row, field) for field in _SLIP_FIELDS}
		employee = str(slip.get("employee") or "")
		slip["employee"] = employee
		slip["employee_name"] = str(slip.get("employee_name") or "") or employee
		slip["company"] = doc.company
		slip["pay_period_start"] = str(doc.pay_period_start or "")
		slip["pay_period_end"] = str(doc.pay_period_end or "")
		check = _get(row, "minimum_wage_check")
		slip["minimum_wage_check"] = bool(int(check or 0)) if check not in (None, "") else True
		slips.append(slip)
		child = _get(row, "name")
		if employee and child:
			names[employee] = str(child)
	return slips, names


def _live_postings(doc) -> tuple[list[dict], list[dict]]:
	"""Journal Entries this run has already produced, split live from gone.

	LIVE is a draft or a submitted entry that still exists. GONE is a link whose
	entry was deleted or cancelled — the ledger has nothing in it from this run,
	so posting again is not a double posting and is allowed. The distinction is
	the whole of the idempotency rule: it is about what is in the ledger, not
	about what this table remembers.
	"""
	live, gone = [], []
	for row in doc.get("gl_postings") or []:
		name = str(_get(row, "journal_entry") or "")
		if not name:
			continue
		record = {
			"journal_entry": name,
			"employee": _get(row, "employee") or "",
			"employee_name": _get(row, "employee_name") or "",
			"posting_mode": _get(row, "posting_mode") or "",
			"posting_date": str(_get(row, "posting_date") or ""),
			"total_debit": _num(_get(row, "total_debit")),
		}
		if not frappe.db.exists(JOURNAL_ENTRY, name):
			record["state"] = "deleted"
			gone.append(record)
			continue
		docstatus = int(frappe.db.get_value(JOURNAL_ENTRY, name, "docstatus") or 0)
		record["docstatus"] = docstatus
		if docstatus == 2:
			record["state"] = "cancelled"
			gone.append(record)
		else:
			record["state"] = "draft" if docstatus == 0 else "submitted"
			live.append(record)
	return live, gone


# ── Where the labour actually happened ────────────────────────────────────
#
# v0.101.0, item 18. A payroll entry used to carry ONE cost center on every
# line, so "labour by block" was a question the ledger could not be asked. The
# farm already answers it: a Farm Task names its block through the
# `location_doctype`/`location` pair, a `Field` names its Cost Center
# (`link_field_to_cost_center`), and a Farm Task Assignment records the minutes.
# This section reads that chain back out, one employee at a time, and hands
# `payroll_gl` the proportions. The arithmetic — including the largest-remainder
# rounding that keeps the entry balanced — is over there and is pure.


def _assignment_minutes(row) -> float:
	"""How long one assignment actually took.

	`actual_duration_minutes` FIRST, because since v0.79.0 it is the sum of the
	closed time segments — start to pause, resume to completion — and the wall
	clock across an interruption bills the valve repair to the irrigating. The
	span is the fallback for rows written before segments existed and for rows a
	Desk edit left without a duration, which is exactly `active_minutes`' own
	posture in `tools/dispatch.py`.
	"""
	minutes = _num(row.get("actual_duration_minutes"))
	if minutes > 0:
		return minutes
	started = row.get("started_at")
	completed = row.get("completed_at")
	if not (started and completed):
		return 0.0
	try:
		return max(frappe.utils.time_diff_in_seconds(completed, started) / 60.0, 0.0)
	except Exception:
		return 0.0


def _postable_cost_center(cost_center: str, company: str) -> str:
	"""A block's Cost Center, or "" with the reason it cannot carry a posting.

	`link_field_to_cost_center` refuses a group cost center and a disabled one
	when the link is MADE, which is the right place for it. It is checked again
	here because a link made through the Desk never met that tool, and a payroll
	posting is the wrong moment to discover it: ERPNext refuses the whole entry,
	and forty people's wages fail over one block somebody pointed at a heading.
	Falling back to the blanket cost center posts the payroll and reports the
	block, which is the recoverable version of the same fact.
	"""
	detail = frappe.db.get_value(
		"Cost Center",
		cost_center,
		["name", "company", "is_group", "disabled"],
		as_dict=True,
	)
	if not detail:
		return ""
	if company and detail.get("company") and detail["company"] != company:
		return ""
	if compat.checked(detail.get("is_group")) or compat.checked(detail.get("disabled")):
		return ""
	return str(detail["name"])


def _block_cost_centers(blocks: list[str], company: str) -> tuple[dict, list[dict]]:
	"""Block docname → the Cost Center its labour books to, plus what was refused."""
	if not blocks:
		return {}, []

	rows = frappe.db.get_all(
		BLOCK,
		filters={"name": ("in", sorted(set(blocks)))},
		fields=list(compat.existing_fields(BLOCK, ("name", "cost_center", "owning_entity"))),
		limit_page_length=0,
	)

	mapped: dict[str, str] = {}
	refused: list[dict] = []
	seen = set()
	for row in rows or []:
		block = str(row.get("name") or "")
		wanted = str(row.get("cost_center") or "").strip()
		if not wanted:
			refused.append(
				{
					"block": block,
					"why": (
						f"{block} has no Cost Center. Its labour cannot be booked to it — "
						"link_field_to_cost_center points a block at one."
					),
				}
			)
			continue
		resolved = mapped.get(wanted)
		if resolved is None and wanted not in seen:
			seen.add(wanted)
			resolved = _postable_cost_center(wanted, company)
		if not resolved:
			refused.append(
				{
					"block": block,
					"cost_center": wanted,
					"why": (
						f"{block} points at Cost Center {wanted!r}, which is not a leaf on "
						f"{company or 'this company'}'s own tree, or is disabled. ERPNext will "
						"not let a posting land on it."
					),
				}
			)
			continue
		mapped[block] = resolved
	return mapped, refused


def _labor_allocations(
	company: str,
	start: str,
	end: str,
	slips: list[dict],
	fallback_cost_center: str,
) -> tuple[dict, dict]:
	"""Each employee's period as the cost center proportions their wage splits by.

	Returns `(allocations, report)`. `allocations` is what
	`payroll_gl.build_payroll_journal_entries` takes; `report` is the same
	arithmetic written out, so a preview can show why a block got the share it
	got rather than asking somebody to trust it.

	THE DENOMINATOR IS THE SLIP'S OWN `total_hours`, not a re-read of the shift
	register. Those hours are what the gross was computed from — they came off
	each crew row's own `joined_at`/`left_at` through `payroll_integration`, were
	bucketed into workweeks for overtime, and were then STORED on the slip. Going
	back to Farm Shift to recount them would be a second source of truth for a
	number this run already settled, and the two would disagree the first time
	somebody corrected a crew row after a payroll was calculated.

	NO STATE FILTER ON THE ASSIGNMENTS, deliberately. A rejected task is time a
	worker spent walking to a block and looking at it, and a paused one is time
	spent before the radio went. Both were paid for and both happened on that
	block. What is excluded excludes itself: a Claimed assignment nobody started
	has no minutes, and a row with no minutes contributes nothing.
	"""
	report: dict = {
		"available": False,
		"reason": None,
		"assignment_count": 0,
		"task_count": 0,
		"employees": [],
		"blocks_without_cost_center": [],
		"notes": [],
	}

	missing = [name for name in (FARM_TASK, FARM_TASK_ASSIGNMENT, BLOCK) if not compat.doctype_exists(name)]
	if missing:
		report["reason"] = (
			f"{', '.join(missing)} is not on this site, so there is no record of which block "
			"any of this labour happened on. The whole wage posts to the mapping's cost center, "
			"as it did before v0.101.0."
		)
		return {}, report

	employees = [str(slip.get("employee") or "") for slip in slips or []]
	employees = [name for name in employees if name]
	if not employees:
		report["reason"] = "This run has no slips with an employee on them."
		return {}, report

	rows = frappe.db.get_all(
		FARM_TASK_ASSIGNMENT,
		filters={
			"assigned_to": ("in", sorted(set(employees))),
			"started_at": ("between", [f"{start} 00:00:00", f"{end} 23:59:59"]),
		},
		fields=list(compat.existing_fields(FARM_TASK_ASSIGNMENT, _ASSIGNMENT_FIELDS)),
		limit_page_length=ASSIGNMENT_CAP,
	)
	report["assignment_count"] = len(rows or [])
	if len(rows or []) >= ASSIGNMENT_CAP:
		report["notes"].append(
			f"The assignment read stopped at {ASSIGNMENT_CAP} records, so the split below is "
			"computed from part of the period. Check the dates before posting."
		)

	worked = [row for row in rows or [] if _assignment_minutes(row) > 0]
	tasks = sorted({str(row.get("task") or "") for row in worked if row.get("task")})
	report["task_count"] = len(tasks)

	# Which block each task was raised against. `location` is a Dynamic Link and
	# `Field` is only one of the registers it can point at — a task on a parcel,
	# an irrigation zone or a cabin is real work and is simply not block work, so
	# it is left unattributed rather than forced onto a block it never named.
	blocks: dict[str, str] = {}
	if tasks:
		for row in frappe.db.get_all(
			FARM_TASK,
			filters={"name": ("in", tasks)},
			fields=list(compat.existing_fields(FARM_TASK, ("name", "location_doctype", "location"))),
			limit_page_length=0,
		):
			if str(row.get("location_doctype") or "") != BLOCK:
				continue
			location = str(row.get("location") or "").strip()
			if location:
				blocks[str(row.get("name"))] = location

	cost_centers, refused = _block_cost_centers(sorted(set(blocks.values())), company)
	report["blocks_without_cost_center"] = refused

	minutes_by_employee: dict[str, list[dict]] = {}
	for row in worked:
		employee = str(row.get("assigned_to") or "")
		if not employee:
			continue
		block = blocks.get(str(row.get("task") or ""), "")
		minutes_by_employee.setdefault(employee, []).append(
			{
				# A task on no block, or on a block with no usable cost center,
				# lands here with an empty cost center — which `cost_center_shares`
				# counts as unattributed rather than dropping, so the blocks that
				# ARE placed do not inherit its share of the wage.
				"cost_center": cost_centers.get(block, ""),
				"block": block,
				"minutes": _assignment_minutes(row),
				"task_count": 1,
			}
		)

	allocations: dict = {}
	described: list[dict] = []
	for slip in slips or []:
		employee = str(slip.get("employee") or "")
		if not employee:
			continue
		rows_for = minutes_by_employee.get(employee) or []
		computed = payroll_gl.cost_center_shares(
			rows_for,
			paid_minutes=_num(slip.get("total_hours")) * 60.0,
			fallback_cost_center=fallback_cost_center,
		)
		placed = [share for share in computed["shares"] if share["blocks"]]
		if placed:
			allocations[employee] = computed["shares"]
		described.append(
			{
				"employee": employee,
				"employee_name": slip.get("employee_name") or employee,
				"split": bool(placed),
				"paid_minutes": computed["paid_minutes"],
				"attributed_minutes": computed["attributed_minutes"],
				"unattributed_minutes": computed["unattributed_minutes"],
				"unplaced_minutes": computed["unplaced_minutes"],
				"coverage": computed["coverage"],
				"shares": computed["shares"],
			}
		)

	report["employees"] = described
	report["available"] = bool(allocations)
	report["split_employee_count"] = len(allocations)
	if not allocations:
		report["reason"] = (
			"No employee in this run has task time on a block with a Cost Center, so there is "
			"nothing to split by. The whole wage posts to the mapping's cost center."
		)
	else:
		thin = [
			row["employee_name"]
			for row in described
			if row["split"] and row["coverage"] is not None and row["coverage"] < 0.5
		]
		if thin:
			report["notes"].append(
				f"Under half the paid time is on a block for: {', '.join(sorted(thin)[:10])}"
				+ ("…" if len(thin) > 10 else "")
				+ ". The rest posts to the mapping's cost center, which is the honest answer — "
				"but if the farm believes that time WAS block work, the tasks for it were "
				"never raised."
			)
		unplaced = [row["employee_name"] for row in described if row["unplaced_minutes"] > 0]
		if unplaced:
			report["notes"].append(
				f"{len(unplaced)} employee(s) have task time on a block with no usable Cost "
				"Center, counted as unattributed. See blocks_without_cost_center."
			)

	return allocations, report


# ── Read tools ────────────────────────────────────────────────────────────


def get_payroll_account_mapping(args: dict) -> ToolResult:
	"""Which accounts one company's payroll posts to, and what is still missing."""
	company = resolve_company(as_str(args, "company"), required=True)
	doc = _mapping_doc(company)
	rows = _mapping_rows(doc)
	verdict = payroll_gl.validate_mapping(rows)

	data = {
		"company": company,
		"configured": doc is not None,
		"name": doc.name if doc else None,
		"is_active": bool(int(doc.is_active or 0)) if doc else False,
		"default_posting_mode": (doc.default_posting_mode if doc else "Consolidated") or "Consolidated",
		"cost_center": (doc.cost_center if doc else "") or "",
		"notes": (doc.notes if doc else "") or "",
		"components": rows,
		"component_count": len(rows),
		"mapping": verdict,
		"catalogue": payroll_gl.describe_components(),
	}

	if doc is None:
		data["next_step"] = (
			f"{company} has no payroll account mapping, so payroll cannot be posted to the "
			"ledger. configure_payroll_accounts creates one. Nothing is shipped as a default: "
			"an account name that is right on one farm's chart is quietly wrong on another's."
		)
		summary = f"{company} has no payroll account mapping — payroll cannot be posted"
	elif not verdict["complete"]:
		missing = ", ".join(sorted({row["component"] for row in verdict["missing"]}))
		data["next_step"] = (
			f"Incomplete: {missing}. post_payroll_to_gl refuses until every required "
			"component has its account, because a mapping with a hole in it cannot produce "
			"an entry that balances."
		)
		summary = f"{company}: {len(rows)} component(s) mapped, INCOMPLETE — missing {missing}"
	else:
		summary = (
			f"{company}: {len(rows)} component(s) mapped, complete. "
			f"Default mode {data['default_posting_mode']}."
		)

	return ToolResult(data=data, summary=summary)


def preview_payroll_gl(args: dict) -> ToolResult:
	"""What the Journal Entries for a payroll run would look like. Writes nothing."""
	doc, plan, context = _gl_plan(args)

	data = dict(context)
	data["journal_entries"] = plan["journal_entries"]
	data["entry_count"] = plan["entry_count"]
	data["totals"] = plan["totals"]
	data["total_debit"] = plan["total_debit"]
	data["total_credit"] = plan["total_credit"]
	data["balanced"] = plan["balanced"]
	data["unbalanced"] = plan["unbalanced"]
	data["mapping"] = plan["mapping"]
	data["skipped"] = plan["skipped"]
	data["warnings"] = plan["warnings"]
	data["created"] = None

	blockers = _blockers(doc, plan, context)
	data["would_post"] = not blockers
	data["blockers"] = blockers
	data["note"] = (
		"Nothing was written. post_payroll_to_gl performs the identical mapping and "
		"stores the result as DRAFT Journal Entries — which still affect no balance "
		"until a human submits them."
	)

	summary = (
		f"Payroll GL preview for {doc.name}: {plan['entry_count']} {plan['mode']} "
		f"journal entry(ies), {plan['total_debit']} debit = {plan['total_credit']} credit"
		f"{_split_phrase(plan)}"
	)
	if blockers:
		summary += f" — {len(blockers)} blocker(s): {blockers[0]['why']}"

	return ToolResult(data=data, summary=summary)


# ── Mutating tools ────────────────────────────────────────────────────────


def configure_payroll_accounts(args: dict) -> ToolResult:
	"""Set up (or amend) the payroll → GL account mapping for one company."""
	company = resolve_company(as_str(args, "company"), required=True)
	rows = _requested_rows(args, company)

	replace = as_bool(args, "replace", default=False)
	doc = _mapping_doc(company)
	created = doc is None
	if created:
		doc = frappe.get_doc({"doctype": MAPPING, "company": company, "is_active": 1})

	existing = {row["component"]: dict(row) for row in _mapping_rows(doc)}
	before = dict(existing)
	if replace:
		existing = {}

	changes = []
	for row in rows:
		component = row["component"]
		previous = before.get(component)
		existing[component] = row
		if previous is None:
			changes.append({"component": component, "change": "added", **_accounts_of(row)})
		elif previous.get("debit_account") != row.get("debit_account") or previous.get(
			"credit_account"
		) != row.get("credit_account"):
			changes.append(
				{
					"component": component,
					"change": "changed",
					"was": _accounts_of(previous),
					**_accounts_of(row),
				}
			)

	if replace:
		for component in before:
			if component not in existing:
				changes.append(
					{"component": component, "change": "removed", **_accounts_of(before[component])}
				)

	ordered = [existing[component] for component in payroll_gl.COMPONENT_NAMES if component in existing]
	if not ordered:
		raise ToolError(
			"no component rows to write. Pass `components` as a list of objects, each with "
			'a `component` and the account(s) it posts to, e.g. [{"component": "Gross Pay", '
			'"debit_account": "5300 - Field Labor"}, {"component": "Net Pay", '
			'"credit_account": "2210 - Payroll Clearing"}]. get_payroll_account_mapping '
			"lists all eleven components and what each one is for."
		)

	doc.set("components", [])
	for row in ordered:
		doc.append(
			"components",
			{
				"component": row["component"],
				"debit_account": row.get("debit_account") or None,
				"credit_account": row.get("credit_account") or None,
				"notes": row.get("notes") or None,
			},
		)

	mode = as_str(args, "default_posting_mode") or as_str(args, "mode")
	if mode:
		normalised = _posting_mode(mode)
		doc.default_posting_mode = "Per Employee" if normalised == "per_employee" else "Consolidated"
	elif created:
		doc.default_posting_mode = "Consolidated"

	cost_center = as_str(args, "cost_center")
	if cost_center:
		doc.cost_center = resolve_cost_center(cost_center, company)
	elif args.get("cost_center") == "":
		doc.cost_center = None

	is_active = as_bool(args, "is_active", default=None)
	if is_active is not None:
		doc.is_active = 1 if is_active else 0

	notes = as_str(args, "notes")
	if notes:
		doc.notes = notes

	doc.flags.ignore_permissions = True
	if created:
		doc.insert()
	else:
		doc.save()

	verdict = payroll_gl.validate_mapping(_mapping_rows(doc))
	data = {
		"name": doc.name,
		"company": company,
		"created": created,
		"replaced": bool(replace),
		"is_active": bool(int(doc.is_active or 0)),
		"default_posting_mode": doc.default_posting_mode,
		"cost_center": doc.cost_center or "",
		"components": _mapping_rows(doc),
		"changes": changes,
		"mapping": verdict,
	}
	if not verdict["complete"]:
		missing = ", ".join(sorted({row["component"] for row in verdict["missing"]}))
		data["next_step"] = (
			f"Still incomplete: {missing}. The mapping is saved — it is built up a row at a "
			"time on purpose — but post_payroll_to_gl refuses until the gaps are filled."
		)

	summary = (
		f"Payroll account mapping {'created' if created else 'updated'} for {company}: "
		f"{len(data['components'])} component(s), {len(changes)} change(s), "
		f"{'complete' if verdict['complete'] else 'INCOMPLETE'}"
	)
	return ToolResult(
		data=data,
		summary=summary,
		docstatus_delta="none → saved" if created else "",
	)


def post_payroll_to_gl(args: dict) -> ToolResult:
	"""Turn a payroll run into DRAFT Journal Entries. Never submits."""
	doc, plan, context = _gl_plan(args)

	blockers = _blockers(doc, plan, context)
	if blockers:
		raise ToolError(_refusal(doc, blockers))

	mode_label = "Per Employee" if plan["mode"] == "per_employee" else "Consolidated"
	now = frappe.utils.now()
	user = getattr(frappe.session, "user", "") or ""

	# A link row whose Journal Entry has been DELETED is a dangling link, and
	# saving the run with it still on the table is refused by the framework's own
	# link validation. Dropping it is the honest reading: the thing it recorded
	# no longer exists. A CANCELLED entry is kept — it is still in the ledger and
	# still part of this run's history, and losing it would lose the record that
	# somebody posted this payroll once and reversed it.
	pruned = [row["journal_entry"] for row in context["superseded_postings"] if row["state"] == "deleted"]
	if pruned:
		kept = [
			row for row in doc.get("gl_postings") or [] if str(_get(row, "journal_entry") or "") not in pruned
		]
		doc.set("gl_postings", [])
		for row in kept:
			doc.append(
				"gl_postings",
				{
					field: _get(row, field)
					for field in (
						"journal_entry",
						"posting_mode",
						"employee",
						"employee_name",
						"posting_date",
						"total_debit",
						"posted_on",
						"posted_by",
					)
				},
			)

	created = []
	for entry in plan["journal_entries"]:
		lines = mutate.validated_journal_lines(entry["accounts"], context["company"])
		mutate.assert_balanced(lines)
		journal = mutate.insert_draft_journal_entry(
			context["company"],
			entry["posting_date"],
			lines,
			entry["user_remark"],
		)
		record = {
			"journal_entry": journal.name,
			"employee": entry.get("employee") or "",
			"employee_name": entry.get("employee_name") or "",
			"posting_mode": mode_label,
			"posting_date": entry["posting_date"],
			"total_debit": entry["total_debit"],
			"line_count": entry["line_count"],
		}
		created.append(record)
		doc.append(
			"gl_postings",
			{
				"journal_entry": journal.name,
				"posting_mode": mode_label,
				"employee": record["employee"] or None,
				"employee_name": record["employee_name"] or None,
				"posting_date": entry["posting_date"],
				"total_debit": entry["total_debit"],
				"posted_on": now,
				"posted_by": user,
			},
		)

	doc.gl_status = "Draft Entries Created"
	doc.flags.ignore_permissions = True
	doc.save()

	data = dict(context)
	data["mode"] = plan["mode"]
	data["created"] = created
	data["journal_entries"] = [row["journal_entry"] for row in created]
	data["entry_count"] = len(created)
	data["totals"] = plan["totals"]
	data["total_debit"] = plan["total_debit"]
	data["total_credit"] = plan["total_credit"]
	data["gl_status"] = doc.gl_status
	data["pruned_postings"] = pruned
	data["skipped"] = plan["skipped"]
	data["warnings"] = plan["warnings"]
	data["next_step"] = (
		"These are DRAFTS and affect no balance. Review them in ERPNext and submit them "
		"there, or with submit_journal_entry if that tool is enabled. Posting this run "
		"again is refused while these entries exist."
	)

	return ToolResult(
		data=data,
		summary=(
			f"{len(created)} draft Journal Entry(ies) created for payroll {doc.name} "
			f"({mode_label.lower()}): {plan['total_debit']} debit = {plan['total_credit']} "
			f"credit{_split_phrase(plan)} — "
			f"{', '.join(row['journal_entry'] for row in created[:5])}" + ("…" if len(created) > 5 else "")
		),
		docstatus_delta="none → 0 (draft)",
	)


def _split_phrase(plan: dict) -> str:
	"""How the summary line says the wage was split, or nothing where it was not."""
	split = plan.get("cost_center_split") or []
	if not plan.get("split_by_cost_center") or len(split) < 2:
		return ""
	named = ", ".join(f"{row['cost_center']} {row['amount']:,.2f}" for row in split[:3])
	return f", expense split across {len(split)} cost center(s): {named}" + ("…" if len(split) > 3 else "")


# ── Shared: the plan both tools compute ───────────────────────────────────


def _gl_plan(args: dict) -> tuple:
	"""Everything the preview and the posting do before they diverge.

	One function because a preview that mapped anything differently from the
	posting it previews would be worse than no preview at all. The only
	difference between the two tools is whether anything is inserted afterwards.
	"""
	doc = _payroll_doc(args)
	company = doc.company
	mapping_doc = _mapping_doc(company)
	rows = _mapping_rows(mapping_doc)

	slips, slip_names = _slips_of(doc)

	mode = as_str(args, "mode") or as_str(args, "posting_mode")
	if mode:
		mode = _posting_mode(mode)
	elif mapping_doc is not None:
		mode = _posting_mode(mapping_doc.default_posting_mode or "Consolidated")
	else:
		mode = "consolidated"

	posting_date = as_date(args, "posting_date") or str(doc.pay_period_end or "")
	include_employer = as_bool(args, "include_employer", default=True)

	cost_center = as_str(args, "cost_center")
	if cost_center:
		cost_center = resolve_cost_center(cost_center, company)
	elif mapping_doc is not None and mapping_doc.cost_center:
		cost_center = mapping_doc.cost_center
	else:
		cost_center = ""

	# v0.101.0. WHERE THE WAGE WAS EARNED, where the record says. Default TRUE
	# and safe to leave that way: a site with no task time on a block with a Cost
	# Center produces no allocations, and no allocations is byte-identical to the
	# entry this tool built before the split existed. It is an argument at all so
	# a farm that costs labour some other way can say so, and so a preview can be
	# taken both ways in one sitting.
	split_requested = as_bool(args, "split_by_cost_center", default=True)
	allocations: dict = {}
	allocation_report: dict = {"available": False, "requested": False, "reason": None}
	if split_requested:
		allocations, allocation_report = _labor_allocations(
			company,
			str(doc.pay_period_start or ""),
			str(doc.pay_period_end or ""),
			slips,
			cost_center,
		)
	allocation_report["requested"] = bool(split_requested)

	plan = payroll_gl.build_payroll_journal_entries(
		slips,
		rows,
		posting_date=posting_date,
		company=company,
		payroll_entry=doc.name,
		mode=mode,
		cost_center=cost_center,
		include_employer=include_employer,
		slip_names=slip_names,
		allocations=allocations or None,
	)

	live, gone = _live_postings(doc)
	context = {
		"payroll_entry": doc.name,
		"company": company,
		"pay_period_start": str(doc.pay_period_start or ""),
		"pay_period_end": str(doc.pay_period_end or ""),
		"pay_frequency": doc.pay_frequency,
		"payroll_status": doc.status,
		"gl_status": doc.get("gl_status") or "Not Posted",
		"mode": plan["mode"],
		"posting_date": posting_date,
		"cost_center": cost_center,
		"split_by_cost_center": bool(plan.get("split_by_cost_center")),
		"cost_center_split": plan["cost_center_split"],
		"cost_center_allocation": allocation_report,
		"include_employer": bool(include_employer),
		"slip_count": len(slips),
		"mapping_configured": mapping_doc is not None,
		"mapping_active": bool(int(mapping_doc.is_active or 0)) if mapping_doc else False,
		"existing_postings": live,
		"superseded_postings": gone,
	}
	return doc, plan, context


def _blockers(doc, plan: dict, context: dict) -> list[dict]:
	"""Every reason this run cannot be posted, all of them at once.

	All of them rather than the first: a caller who fixes one refusal and is met
	with the next has been sent round the loop for no reason, and a payroll
	posting is not a thing anybody wants to iterate on.
	"""
	blockers = []

	if context["payroll_status"] not in POSTABLE_STATUSES:
		blockers.append(
			{
				"blocker": "payroll_status",
				"why": (
					f"payroll entry {doc.name} is {context['payroll_status']!r}. Only "
					f"{' and '.join(POSTABLE_STATUSES)} runs can be posted — a Draft has no "
					"computed slips and a Cancelled run is one somebody withdrew."
				),
			}
		)

	if not context["mapping_configured"]:
		blockers.append(
			{
				"blocker": "no_mapping",
				"why": (
					f"{context['company']} has no payroll account mapping. "
					"configure_payroll_accounts creates one; no account name is shipped as a "
					"default because one that is right on this chart of accounts would be "
					"quietly wrong on the next farm's."
				),
			}
		)
	elif not context["mapping_active"]:
		blockers.append(
			{
				"blocker": "mapping_inactive",
				"why": (
					f"the payroll account mapping for {context['company']} is marked inactive. "
					"Tick Active on it, or configure_payroll_accounts with is_active=true."
				),
			}
		)
	elif not plan["mapping"]["complete"]:
		missing = sorted({row["component"] for row in plan["mapping"]["missing"]})
		blockers.append(
			{
				"blocker": "incomplete_mapping",
				"components": missing,
				"detail": plan["mapping"]["missing"],
				"why": (
					f"the account mapping for {context['company']} is missing: {', '.join(missing)}. "
					"A mapping with a hole in it cannot produce an entry that balances."
				),
			}
		)

	if context["existing_postings"]:
		names = ", ".join(row["journal_entry"] for row in context["existing_postings"])
		blockers.append(
			{
				"blocker": "already_posted",
				"journal_entries": [row["journal_entry"] for row in context["existing_postings"]],
				"why": (
					f"payroll entry {doc.name} already has {len(context['existing_postings'])} "
					f"journal entry(ies) against it: {names}. Posting it again would double the "
					"wage expense and double the liability. Delete or cancel those entries first "
					"if they were wrong."
				),
			}
		)

	if not plan["journal_entries"]:
		blockers.append(
			{
				"blocker": "nothing_to_post",
				"why": (
					f"payroll entry {doc.name} produced no journal entry lines. Every slip in it "
					"is zero, so there is nothing to book."
				),
			}
		)

	if plan["unbalanced"]:
		blockers.append(
			{
				"blocker": "unbalanced",
				"detail": plan["unbalanced"],
				"why": (
					f"{len(plan['unbalanced'])} entry(ies) do not balance. A payroll slip whose "
					"gross does not equal its deductions plus its net is a slip with a data "
					"problem, and posting it would put that problem in the ledger."
				),
			}
		)

	return blockers


def _refusal(doc, blockers: list[dict]) -> str:
	lines = [f"payroll entry {doc.name} cannot be posted to the general ledger. Nothing was created."]
	for index, blocker in enumerate(blockers, start=1):
		lines.append(f"{index}. {blocker['why']}")
	lines.append("preview_payroll_gl shows exactly what the entries would be, without writing anything.")
	return " ".join(lines)


# ── Argument handling ─────────────────────────────────────────────────────


def _posting_mode(value: str) -> str:
	text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
	if text in ("per_employee", "peremployee", "employee", "individual"):
		return "per_employee"
	if text in ("consolidated", "combined", "single", "one", ""):
		return "consolidated"
	raise ToolError(
		f"mode must be 'consolidated' (one journal entry for the whole run) or "
		f"'per_employee' (one each), got {value!r}."
	)


def _accounts_of(row: dict) -> dict:
	return {
		"debit_account": row.get("debit_account") or "",
		"credit_account": row.get("credit_account") or "",
	}


def _requested_rows(args: dict, company: str) -> list[dict]:
	"""The component rows a `configure_payroll_accounts` call is asking for.

	Three shapes accepted, because three are what callers actually send: a list
	of objects, a mapping of component to an object, and a mapping of component
	to a single account name. The third is only unambiguous for a one-sided
	component, so a two-sided one sent that way is refused by name rather than
	guessed at — putting the employer's Social Security expense and its liability
	in the same account is a decision, not a shorthand.
	"""
	raw = args.get("components")
	if raw is None:
		raw = args.get("accounts")
	if raw is None:
		raw = args.get("mapping")
	if raw is None:
		return []

	entries: list[dict] = []
	if isinstance(raw, dict):
		for component, value in raw.items():
			if isinstance(value, dict):
				entries.append({**value, "component": component})
			elif isinstance(value, str):
				entries.append({"component": component, "account": value})
			else:
				raise ToolError(
					f"components[{component!r}] must be an object with debit_account and/or "
					f"credit_account, or an account name, got {type(value).__name__}."
				)
	elif isinstance(raw, list):
		for index, value in enumerate(raw, start=1):
			if not isinstance(value, dict):
				raise ToolError(f"components[{index}] must be an object, got {type(value).__name__}.")
			entries.append(dict(value))
	else:
		raise ToolError("components must be a list of objects or an object keyed by component name.")

	rows = []
	seen = set()
	for index, entry in enumerate(entries, start=1):
		component = str(entry.get("component") or "").strip()
		if not component:
			raise ToolError(f"components[{index}] has no `component`.")
		if component not in payroll_gl.COMPONENT_INDEX:
			raise ToolError(
				f"components[{index}]: {component!r} is not a payroll component. The eleven "
				f"are: {', '.join(payroll_gl.COMPONENT_NAMES)}. get_payroll_account_mapping "
				"says what each one is for."
			)
		if component in seen:
			raise ToolError(
				f"{component!r} appears twice in this call. One row per component — two would "
				"mean two answers to one question."
			)
		seen.add(component)

		definition = payroll_gl.COMPONENT_INDEX[component]
		single = str(entry.get("account") or "").strip()
		debit = str(entry.get("debit_account") or "").strip()
		credit = str(entry.get("credit_account") or "").strip()

		if single:
			if len(definition["sides"]) > 1 and not (debit or credit):
				raise ToolError(
					f"components[{index}]: {component} posts to BOTH sides — an expense and a "
					"liability — so a single `account` cannot say which is which. Send "
					"debit_account and credit_account. They may be the same account if that is "
					"genuinely what this farm wants."
				)
			side = definition["sides"][0]
			if side == "debit":
				debit = debit or single
			else:
				credit = credit or single

		unwanted = [side for side in ("debit", "credit") if side not in definition["sides"]]
		for side in unwanted:
			value = debit if side == "debit" else credit
			if value:
				other = "credit" if side == "debit" else "debit"
				raise ToolError(
					f"components[{index}]: {component} has no {side} side — {definition['about']} "
					f"It takes a {other}_account only."
				)

		row = {"component": component, "debit_account": "", "credit_account": ""}
		for side in definition["sides"]:
			value = debit if side == "debit" else credit
			if value:
				row[f"{side}_account"] = _leaf_account(value, company, component, side)
		notes = str(entry.get("notes") or "").strip()
		if notes:
			row["notes"] = notes
		if not (row["debit_account"] or row["credit_account"]):
			raise ToolError(
				f"components[{index}]: {component} was sent with no account at all. Give it "
				f"{' and '.join(f'{side}_account' for side in definition['sides'])}, or leave "
				"the component out of the call."
			)
		rows.append(row)
	return rows


def _leaf_account(account: str, company: str, component: str, side: str) -> str:
	"""One account name resolved to a docname, refused if it is a group.

	Checked here rather than only at posting time because a mapping is written
	once and posted from every fortnight after that — a group account stored now
	is a payroll run refused in six weeks by somebody who was not the one who
	configured it.
	"""
	resolved = resolve_account(account, company)
	if frappe.db.get_value("Account", resolved, "is_group"):
		raise ToolError(
			f"{component} {side}_account {resolved!r} is a group account. ERPNext posts only "
			"to leaf accounts, so a mapping pointing at a group is one that can never post."
		)
	return resolved
