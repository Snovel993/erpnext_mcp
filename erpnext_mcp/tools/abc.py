# SPDX-License-Identifier: MIT
"""Activity-based costing: what each activity cost, and which block consumed it.

THE PROBLEM ABC EXISTS TO SOLVE, IN ORCHARD TERMS. Every farm can already say
what it spent. Almost none can say what the Home Block cost per acre, because the
spend does not arrive labelled by block — it arrives labelled by supplier, by
account and, on a good site, by cost center. Dividing the overhead account by
total acreage answers the question in a way that is arithmetically fine and
managerially useless: it charges the twelve-year-old Gala and the newly grafted
replant the same spray cost, when one of them was sprayed nine times and the
other four.

ABC's answer is a two-step: gather cost into a POOL per ACTIVITY, then push each
pool out to blocks in proportion to how much of that activity each block actually
consumed — the COST DRIVER. Both steps are stored, which is the design decision
that matters most here.

WHY THE INTERMEDIATES ARE STORED RATHER THAN RECOMPUTED. A per-acre cost is a
quotient of two numbers that both moved during the year: the pool, and the acres.
An operation that keeps only the quotient can watch it rise for four seasons and
never learn whether the block got dearer or simply smaller. So `ABC Cost
Assignment` holds the driver quantity, the share, the pool, the amount assigned
AND the acres behind every single line, and a rerun appends rather than
overwrites. The history of what this operation believed its costs were is itself
a record.

WHAT THIS ENGINE WILL NOT DO IS INVENT A DRIVER QUANTITY. Two drivers are
DERIVABLE from what the site already holds — `Acres`, from each Field's acreage
weighted by the days it was productive, and `Direct Assignment`, where the pool
names the one block it was incurred for. Every other driver is a measurement
somebody took: hours worked, applications made, bins picked. Where those are not
supplied, the activity is reported UNALLOCATED with its full amount and the
measurement that would fix it — never spread evenly across blocks. An even
spread is indistinguishable in the output from a measured one, and it is exactly
the answer ABC was adopted to stop giving.

THE `field` ARGUMENT NARROWS THE ROWS AND NEVER THE ARITHMETIC. Driver shares are
always computed against every block that consumed the activity, because a share
computed against one block is 100% by construction. `compute_abc_allocation`
stores the whole run and filters only what it hands back.

THE WATERFALL IS THE READ THAT MANAGEMENT ACTUALLY USES. Cost does not land on a
bin all at once; it accumulates as fruit moves Growing → Harvest → Post-Harvest →
Packing → Sales, and the question "where did this get expensive" is answered by
the shape of that accumulation rather than by the total. `get_phase_waterfall`
reports cost added and cost cumulative at each of the five stages, per acre
always and per unit when somebody supplies the units — because picking a
denominator is the step that turns a costing report into fiction, and this module
inherits that rule from `get_absorption_cost_report` rather than reopening it.
"""

import frappe

from .. import compat, kpi
from ..args import as_bool, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from ..services import sustainable_cf_per_acre as acre_service

ACTIVITY = "Cost Activity"
POOL = "Activity Cost Pool"
ASSIGNMENT = "ABC Cost Assignment"

_HINT = "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app."

RECORD_CAP = 500

#: The waterfall, in order. THE ORDER IS THE WHOLE POINT of the read: a bin
#: leaves the block carrying its growing cost and picks up the rest on the way
#: to the customer, so cumulative cost per unit at each stop is what says where
#: the operation got expensive. Anything not on this list is not a phase.
PHASES = ("Growing", "Harvest", "Post-Harvest", "Packing", "Sales")

ACTIVITY_TYPES = (
	"Cultural",
	"Pest Management",
	"Irrigation",
	"Fertility",
	"Harvest",
	"Transport",
	"Packing",
	"Storage",
	"Sales",
	"Administration",
	"Other",
)

COST_DRIVERS = (
	"Acres",
	"Hours",
	"Machine Hours",
	"Applications",
	"Units Harvested",
	"Bins",
	"Boxes",
	"Deliveries",
	"Employees",
	"Direct Assignment",
)

#: The two this app can work out for itself. Everything else is a measurement.
DERIVABLE_DRIVERS = ("Acres", "Direct Assignment")

POOL_STATUSES = ("Draft", "Ready", "Allocated")
AMOUNT_SOURCES = ("Ledger", "Manual")

GROUP_BY = ("field", "activity", "phase")

#: How many GL rows one pool may be built from. The same ceiling
#: `get_absorption_cost_report` uses, for the same reason: a pool that silently
#: stopped counting at the limit would be a wrong number that looked right.
GL_CAP = 5000

_ACTIVITY_FIELDS = (
	"name",
	"activity_name",
	"company",
	"activity_type",
	"phase",
	"cost_driver",
	"driver_uom",
	"disabled",
	"cost_center",
	"notes",
	"modified",
	"owner",
)

_POOL_FIELDS = (
	"name",
	"activity",
	"company",
	"fiscal_year",
	"period_start",
	"period_end",
	"pool_amount",
	"currency",
	"amount_source",
	"status",
	"cost_object",
	"notes",
	"modified",
	"owner",
)


def _get(row, key, default=None):
	if isinstance(row, dict):
		return row.get(key, default)
	return getattr(row, key, default)


def _require_activities() -> None:
	compat.require_doctype(ACTIVITY, _HINT)


def _require_pools() -> None:
	compat.require_doctype(ACTIVITY, _HINT)
	compat.require_doctype(POOL, _HINT)


def _require_assignments() -> None:
	_require_pools()
	compat.require_doctype(ASSIGNMENT, _HINT)


# ── shared resolution ───────────────────────────────────────────────────────
def _activity_doc(reference: str, company: str = ""):
	"""One Cost Activity by docname or by the name somebody calls it on the ground."""
	reference = (reference or "").strip()
	if not reference:
		raise ToolError("activity is required — its docname or its activity_name.")
	if frappe.db.exists(ACTIVITY, reference):
		return frappe.get_doc(ACTIVITY, reference)
	filters = {"activity_name": reference}
	if company:
		filters["company"] = company
	matches = frappe.db.get_all(ACTIVITY, filters=filters, pluck="name", limit=5)
	if len(matches) == 1:
		return frappe.get_doc(ACTIVITY, matches[0])
	if len(matches) > 1:
		raise ToolError(
			f"{reference!r} names {len(matches)} cost activities: {', '.join(sorted(matches))}. "
			"Pass the docname, or set company to narrow it."
		)
	raise ToolError(
		f"no Cost Activity called {reference!r} on this site. list_cost_activities has the register."
	)


def _fiscal_window(fiscal_year: str, args: dict) -> tuple[str, str]:
	"""The dates a fiscal year covers, with an explicit override honoured.

	The window is COPIED onto the pool and the assignment rather than re-derived
	on every read: a Fiscal Year's dates can be edited afterwards, and a stored
	allocation that silently re-scoped itself would change a prior year's numbers
	with nothing anywhere saying so.
	"""
	start = as_date(args, "period_start")
	end = as_date(args, "period_end")
	if start and end:
		if end < start:
			raise ToolError(f"period_end ({end}) is before period_start ({start}).")
		return start, end
	if start or end:
		raise ToolError(
			"period_start and period_end go together — one without the other is not a window. "
			"Pass both to override the fiscal year's own dates, or neither to use them."
		)

	if not compat.doctype_exists("Fiscal Year"):
		raise ToolError(
			"this site has no Fiscal Year doctype, so a year cannot be turned into a window. "
			"Pass period_start and period_end instead."
		)
	row = frappe.db.get_value(
		"Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
	)
	if not row:
		known = frappe.db.get_all("Fiscal Year", pluck="name", limit=20)
		raise ToolError(
			f"no Fiscal Year named {fiscal_year!r} on this site. Known fiscal years: "
			f"{', '.join(sorted(known)) or 'none'}. list_fiscal_years has the register."
		)
	start = str(row.get("year_start_date") or "")
	end = str(row.get("year_end_date") or "")
	if not start or not end:
		raise ToolError(
			f"Fiscal Year {fiscal_year!r} has no start or end date, so it cannot be turned into a "
			"window. Pass period_start and period_end."
		)
	return start, end


def _describe_activity(doc) -> dict:
	accounts = [
		{"account": _get(row, "account"), "notes": _get(row, "notes") or None}
		for row in doc.get("accounts") or []
	]
	out = {
		"name": doc.name,
		"activity_name": doc.activity_name,
		"company": doc.company,
		"activity_type": doc.activity_type,
		"phase": doc.phase,
		"cost_driver": doc.cost_driver,
		"driver_uom": doc.driver_uom or None,
		"disabled": bool(compat.checked(doc.disabled)),
		"cost_center": doc.cost_center or None,
		"accounts": accounts,
		"account_count": len(accounts),
		"notes": doc.notes or None,
		"driver_is_derivable": doc.cost_driver in DERIVABLE_DRIVERS,
	}
	if not doc.cost_center and not accounts:
		out["pool_note"] = (
			"This activity names neither a cost center nor an account, so create_activity_cost_pool "
			"cannot total its cost off the ledger and will require a pool_amount to be supplied — "
			"which is a legitimate pool, and is labelled Manual so a reader can tell the two apart."
		)
	if doc.cost_driver not in DERIVABLE_DRIVERS:
		out["driver_note"] = (
			f"{doc.cost_driver} is a MEASUREMENT, not something this app can work out from the "
			"Field table. compute_abc_allocation needs driver_quantities for this activity; "
			"without them its whole pool is reported UNALLOCATED rather than spread evenly across "
			"blocks. An even spread is indistinguishable in the output from a measured one."
		)
	return out


# ── the register ────────────────────────────────────────────────────────────
def create_cost_activity(args: dict) -> ToolResult:
	"""Define one thing the operation does that costs money. MUTATING."""
	_require_activities()
	company = resolve_company(as_str(args, "company"), required=True)
	activity_name = as_str(args, "activity_name") or as_str(args, "name")
	if not activity_name:
		raise ToolError("activity_name is required — what this work is called on the ground.")

	activity_type = as_str(args, "activity_type") or "Cultural"
	if activity_type not in ACTIVITY_TYPES:
		raise ToolError(f"activity_type must be one of: {', '.join(ACTIVITY_TYPES)}. Got {activity_type!r}.")
	phase = as_str(args, "phase") or "Growing"
	if phase not in PHASES:
		raise ToolError(
			f"phase must be one of: {', '.join(PHASES)}. Got {phase!r}. The phase is not a label — "
			"it decides where on the cost waterfall this activity's money enters, and an activity in "
			"the wrong phase reports real cost arriving at the wrong moment."
		)
	cost_driver = as_str(args, "cost_driver") or "Acres"
	if cost_driver not in COST_DRIVERS:
		raise ToolError(f"cost_driver must be one of: {', '.join(COST_DRIVERS)}. Got {cost_driver!r}.")

	doc = frappe.new_doc(ACTIVITY)
	doc.activity_name = activity_name
	doc.company = company
	doc.activity_type = activity_type
	doc.phase = phase
	doc.cost_driver = cost_driver
	for key in ("driver_uom", "cost_center", "notes"):
		value = as_str(args, key)
		if value:
			doc.set(key, value)

	for account in _account_list(args.get("accounts")):
		doc.append("accounts", {"account": account})

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	return ToolResult(
		data={"activity": _describe_activity(doc)},
		summary=(
			f"created Cost Activity {doc.name} ({company}): {activity_type} in the {phase} phase, "
			f"driven by {cost_driver}"
		),
		docstatus_delta="none → 0 (draft)",
	)


def _account_list(raw) -> list:
	if raw is None:
		return []
	if not isinstance(raw, list):
		raise ToolError('accounts must be a list of account docnames, e.g. ["5100 - Sprays - ETC"].')
	out = []
	for entry in raw:
		account = str(entry or "").strip()
		if account and account not in out:
			out.append(account)
	return out


def get_cost_activity(args: dict) -> ToolResult:
	"""One activity with its accounts and every pool ever built for it. Read-only."""
	_require_activities()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _activity_doc(as_str(args, "activity", required=True), company)
	described = _describe_activity(doc)

	pools = []
	if compat.doctype_exists(POOL):
		pools = [
			dict(row)
			for row in frappe.db.get_all(
				POOL,
				filters={"activity": doc.name},
				fields=compat.existing_fields(POOL, _POOL_FIELDS),
				order_by="fiscal_year desc",
				limit=RECORD_CAP,
			)
			or []
		]
	described["pools"] = pools
	described["pool_count"] = len(pools)

	return ToolResult(
		data=described,
		summary=(
			f"{doc.activity_name} ({doc.company}): {doc.activity_type} in the {doc.phase} phase, "
			f"driven by {doc.cost_driver}, {len(pools)} pool(s)"
		),
	)


def list_cost_activities(args: dict) -> ToolResult:
	"""The activity register, with what each one's cost is divided by. Read-only."""
	_require_activities()
	limit = min(as_limit(args), RECORD_CAP)
	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters["company"] = company
	for key in ("activity_type", "phase", "cost_driver", "cost_center"):
		value = as_str(args, key)
		if value:
			filters[key] = value
	if not as_bool(args, "include_disabled", False):
		if compat.has_field(ACTIVITY, "disabled"):
			filters["disabled"] = 0

	rows = [
		dict(row)
		for row in frappe.db.get_all(
			ACTIVITY,
			filters=filters,
			fields=compat.existing_fields(ACTIVITY, _ACTIVITY_FIELDS),
			order_by="phase asc, activity_name asc",
			limit=limit,
		)
		or []
	]

	by_phase: dict = {phase: 0 for phase in PHASES}
	needs_measurement = []
	no_ledger_scope = []
	for row in rows:
		phase = row.get("phase")
		if phase in by_phase:
			by_phase[phase] += 1
		if row.get("cost_driver") not in DERIVABLE_DRIVERS:
			needs_measurement.append(row["name"])
		if not row.get("cost_center"):
			no_ledger_scope.append(row["name"])

	return ToolResult(
		data={
			"activities": rows,
			"activity_count": len(rows),
			"by_phase": by_phase,
			"drivers_needing_measurement": needs_measurement,
			"note": (
				f"{len(needs_measurement)} activity(ies) use a driver this app cannot derive — hours, "
				"applications, bins. compute_abc_allocation needs driver_quantities for those, and "
				"reports them UNALLOCATED rather than spreading them evenly if none arrive."
			)
			if needs_measurement
			else "",
			"cost_center_note": (
				f"{len(no_ledger_scope)} activity(ies) name no cost center. Their pools can only be "
				"Manual, which is a legitimate pool and a different kind of evidence — the label is "
				"on every pool row so the two are never read as the same thing."
			)
			if no_ledger_scope
			else "",
		},
		summary=(
			f"{len(rows)} cost activity(ies)"
			+ (f" for {company}" if company else "")
			+ (f", {len(needs_measurement)} needing measured drivers" if needs_measurement else "")
		),
	)


def update_cost_activity(args: dict) -> ToolResult:
	"""Change an activity's type, phase, driver, ledger scope or accounts. MUTATING.

	CHANGING THE PHASE OR THE DRIVER DOES NOT RESTATE A STORED RUN, and that is
	deliberate. An `ABC Cost Assignment` line carries the phase and driver it was
	computed under, so last year's waterfall goes on saying what it said. The new
	values apply from the next allocation.
	"""
	_require_activities()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _activity_doc(as_str(args, "activity", required=True), company)
	changed = []

	activity_type = as_str(args, "activity_type")
	if activity_type:
		if activity_type not in ACTIVITY_TYPES:
			raise ToolError(f"activity_type must be one of: {', '.join(ACTIVITY_TYPES)}. Got {activity_type!r}.")
		doc.activity_type = activity_type
		changed.append("activity_type")
	phase = as_str(args, "phase")
	if phase:
		if phase not in PHASES:
			raise ToolError(f"phase must be one of: {', '.join(PHASES)}. Got {phase!r}.")
		doc.phase = phase
		changed.append("phase")
	cost_driver = as_str(args, "cost_driver")
	if cost_driver:
		if cost_driver not in COST_DRIVERS:
			raise ToolError(f"cost_driver must be one of: {', '.join(COST_DRIVERS)}. Got {cost_driver!r}.")
		doc.cost_driver = cost_driver
		changed.append("cost_driver")
	for key in ("activity_name", "driver_uom", "cost_center", "notes"):
		value = as_str(args, key)
		if value:
			doc.set(key, value)
			changed.append(key)

	if args.get("disabled") is not None:
		doc.disabled = 1 if as_bool(args, "disabled", False) else 0
		changed.append("disabled")

	if args.get("accounts") is not None:
		doc.set("accounts", [])
		for account in _account_list(args.get("accounts")):
			doc.append("accounts", {"account": account})
		changed.append("accounts")

	if not changed:
		raise ToolError(
			"nothing to update. Pass at least one of: activity_name, activity_type, phase, "
			"cost_driver, driver_uom, cost_center, accounts, disabled, notes."
		)

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return ToolResult(
		data={
			"activity": _describe_activity(doc),
			"changed": changed,
			"stored_runs_note": (
				"Stored ABC Cost Assignment lines carry the phase and driver they were computed "
				"under and are NOT restated by this edit. A prior year's waterfall goes on saying "
				"what it said; the new values apply from the next compute_abc_allocation."
			)
			if ("phase" in changed or "cost_driver" in changed)
			else "",
		},
		summary=f"updated Cost Activity {doc.name}: {', '.join(changed)}",
		docstatus_delta="0 → 0 (draft, edited)",
	)


# ── the pools ───────────────────────────────────────────────────────────────
def _ledger_pool(activity_doc, from_date: str, to_date: str) -> dict:
	"""One activity's cost totalled off the ledger, itemised by account.

	THE SCOPE IS AN AND, AND THE ITEMISATION IS A BREAKDOWN OF IT. Cost center
	and accounts narrow the same set of GL rows — the same semantics
	`get_absorption_cost_report` uses — and the source rows then split THAT set by
	account. Totalling each filter independently would double-count every entry
	matching both, and would produce a plausible pool with a trail that quietly
	did not add up to it.
	"""
	out = {"measurable": False, "amount": 0.0, "entry_count": 0, "sources": [], "scope": []}
	if not compat.doctype_exists("GL Entry"):
		out["reason"] = "this site has no GL Entry table, so no pool can be read from the ledger."
		return out

	cost_center = str(activity_doc.cost_center or "").strip()
	accounts = [
		str(_get(row, "account") or "").strip()
		for row in activity_doc.get("accounts") or []
		if str(_get(row, "account") or "").strip()
	]
	if not cost_center and not accounts:
		out["reason"] = (
			f"{activity_doc.name} names neither a cost center nor an account, so there is nothing "
			"to total. Give it one with update_cost_activity, or pass pool_amount to record a "
			"Manual pool."
		)
		return out

	filters = {"company": activity_doc.company, "is_cancelled": 0}
	if cost_center:
		filters["cost_center"] = cost_center
		out["scope"].append(f"cost center {cost_center}")
	if accounts:
		filters["account"] = ("in", accounts)
		out["scope"].append(f"accounts {', '.join(accounts)}")
	filters["posting_date"] = ("between", [from_date, to_date])

	rows = frappe.db.get_all(
		"GL Entry", filters=filters, fields=["name", "account", "debit", "credit"], limit=GL_CAP
	)
	rows = list(rows or [])

	by_account: dict = {}
	for row in rows:
		account = row.get("account") or "(no account)"
		entry = by_account.setdefault(account, {"amount": 0.0, "entry_count": 0})
		entry["amount"] += float(row.get("debit") or 0) - float(row.get("credit") or 0)
		entry["entry_count"] += 1

	basis = f"net of debits and credits over {' and '.join(out['scope'])}, {from_date} to {to_date}"
	for account in sorted(by_account):
		entry = by_account[account]
		out["sources"].append(
			{
				"source_type": "Account",
				"reference": account,
				"amount": round(entry["amount"], 2),
				"entry_count": entry["entry_count"],
				"basis": basis,
			}
		)

	# Sum the ROUNDED source rows so the stored trail reaches the stored figure
	# exactly. Rounding the unrounded total independently is how a pool ends up a
	# cent away from the sources that are supposed to evidence it.
	out["amount"] = round(sum(source["amount"] for source in out["sources"]), 2)
	out["entry_count"] = len(rows)
	out["measurable"] = True
	if len(rows) >= GL_CAP:
		out["truncation_warning"] = (
			f"the ledger read stopped at {GL_CAP} entries, so this pool is a FLOOR rather than a "
			"total. Narrow the window or the accounts and record the parts."
		)
	return out


def create_activity_cost_pool(args: dict) -> ToolResult:
	"""Gather one activity's cost for one fiscal year into a pool. MUTATING.

	TWO WAYS TO GET AN AMOUNT, AND THE DIFFERENCE IS ON THE RECORD. Pass nothing
	and the pool is totalled off the ledger over the activity's cost center and
	accounts, itemised by account so the figure can be walked back to the books.
	Pass `pool_amount` and it is recorded as Manual — a perfectly legitimate pool,
	and an entirely different kind of evidence, which is why the label is a column
	rather than a footnote.
	"""
	_require_pools()
	company = resolve_company(as_str(args, "company"), required=True)
	activity_doc = _activity_doc(as_str(args, "activity", required=True), company)
	if activity_doc.company != company:
		raise ToolError(
			f"Cost Activity {activity_doc.name} belongs to {activity_doc.company}, not {company}. "
			"Nothing was created."
		)
	fiscal_year = as_str(args, "fiscal_year", required=True)
	period_start, period_end = _fiscal_window(fiscal_year, args)

	status = as_str(args, "status") or "Ready"
	if status not in POOL_STATUSES:
		raise ToolError(f"status must be one of: {', '.join(POOL_STATUSES)}. Got {status!r}.")

	cost_object = as_str(args, "cost_object")
	if cost_object and not frappe.db.exists(kpi.FIELD_DOCTYPE, cost_object):
		raise ToolError(
			f"no Field named {cost_object!r} on this site. cost_object names the ONE block a "
			"directly-assigned pool belongs to; list_fields has the register. Nothing was created."
		)
	if activity_doc.cost_driver == "Direct Assignment" and not cost_object:
		raise ToolError(
			f"{activity_doc.name} is driven by Direct Assignment, which means its cost was incurred "
			"for one block and no other — so the pool has to name that block. Pass cost_object. "
			"Nothing was created."
		)

	supplied = args.get("pool_amount")
	measured = {}
	if supplied is not None:
		amount = round(float(supplied), 2)
		amount_source = "Manual"
		sources = [
			{
				"source_type": "Manual",
				"reference": "",
				"amount": amount,
				"entry_count": 0,
				"basis": as_str(args, "basis") or "supplied by the caller",
			}
		]
	else:
		measured = _ledger_pool(activity_doc, period_start, period_end)
		if not measured["measurable"]:
			raise ToolError(
				measured.get("reason", "this activity's cost cannot be read from the ledger.")
				+ " Nothing was created."
			)
		amount = measured["amount"]
		amount_source = "Ledger"
		sources = measured["sources"]

	if amount < 0:
		raise ToolError(
			f"the pool for {activity_doc.name} over {period_start} to {period_end} comes to "
			f"{amount}, which is negative — the accounts in scope were net credited over the "
			"window. A negative pool allocates a CREDIT to every block in proportion to how much "
			"of the activity it consumed, which is arithmetic nobody asked for. Check the scope, or "
			"book the correction, and try again. Nothing was created."
		)

	doc = frappe.new_doc(POOL)
	doc.activity = activity_doc.name
	doc.company = company
	doc.fiscal_year = fiscal_year
	doc.period_start = period_start
	doc.period_end = period_end
	doc.pool_amount = amount
	doc.amount_source = amount_source
	doc.status = status
	if cost_object:
		doc.cost_object = cost_object
	for key in ("currency", "notes"):
		value = as_str(args, key)
		if value:
			doc.set(key, value)
	for source in sources:
		doc.append("sources", source)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	data = {
		"pool": _describe_pool(doc),
		"amount_source_note": (
			"LEDGER. The figure was totalled off GL Entry and `sources` itemises it by account, so "
			"it can be walked back to the books."
			if amount_source == "Ledger"
			else "MANUAL. Somebody typed this figure. It is a legitimate pool and it carries no "
			"ledger trail, which is why the distinction is a column rather than a footnote — a "
			"report that presented the two identically would claim a traceability half its rows "
			"do not have."
		),
	}
	if measured.get("truncation_warning"):
		data["truncation_warning"] = measured["truncation_warning"]
	if amount == 0:
		data["zero_note"] = (
			"This pool is ZERO. That is a real answer — nothing was booked to this activity's "
			"scope in the window — and it is stored rather than refused, because 'this activity "
			"cost nothing' and 'nobody has computed this activity' are different statements and "
			"only one of them is worth acting on."
		)

	return ToolResult(
		data=data,
		summary=(
			f"created Activity Cost Pool {doc.name}: {amount} for {activity_doc.activity_name} over "
			f"{fiscal_year} ({period_start} to {period_end}), {amount_source.lower()}"
		),
		docstatus_delta="none → 0 (draft)",
	)


def _describe_pool(doc) -> dict:
	sources = [
		{
			"source_type": _get(row, "source_type"),
			"reference": _get(row, "reference") or None,
			"amount": float(_get(row, "amount") or 0),
			"entry_count": int(_get(row, "entry_count") or 0),
			"basis": _get(row, "basis") or None,
		}
		for row in doc.get("sources") or []
	]
	return {
		"name": doc.name,
		"activity": doc.activity,
		"company": doc.company,
		"fiscal_year": doc.fiscal_year,
		"period_start": str(doc.period_start or "") or None,
		"period_end": str(doc.period_end or "") or None,
		"pool_amount": float(doc.pool_amount or 0),
		"currency": doc.currency or None,
		"amount_source": doc.amount_source,
		"status": doc.status,
		"cost_object": doc.cost_object or None,
		"sources": sources,
		"source_count": len(sources),
		"notes": doc.notes or None,
	}


def list_activity_cost_pools(args: dict) -> ToolResult:
	"""Every pool for a company and year, with the ledger/manual split. Read-only."""
	_require_pools()
	limit = min(as_limit(args), RECORD_CAP)
	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters["company"] = company
	for key in ("fiscal_year", "activity", "status", "amount_source"):
		value = as_str(args, key)
		if value:
			filters[key] = value

	rows = [
		dict(row)
		for row in frappe.db.get_all(
			POOL,
			filters=filters,
			fields=compat.existing_fields(POOL, _POOL_FIELDS),
			order_by="fiscal_year desc, activity asc",
			limit=limit,
		)
		or []
	]
	total = round(sum(float(row.get("pool_amount") or 0) for row in rows), 2)
	ledger = round(
		sum(float(row.get("pool_amount") or 0) for row in rows if row.get("amount_source") == "Ledger"), 2
	)
	manual = round(total - ledger, 2)
	draft = [row["name"] for row in rows if row.get("status") == "Draft"]

	return ToolResult(
		data={
			"pools": rows,
			"pool_count": len(rows),
			"total_pool_amount": total,
			"ledger_amount": ledger,
			"manual_amount": manual,
			"draft_pools": draft,
			"note": (
				f"{manual} of the {total} in these pools was typed rather than read from the ledger. "
				"That is a legitimate figure and a different kind of evidence, and it is separated "
				"here so nobody has to take a report's word for which is which."
			)
			if manual
			else "",
			"draft_note": (
				f"{len(draft)} pool(s) are Draft and are EXCLUDED from allocation. A draft pool is "
				"not a zero pool — compute_abc_allocation skips it and says so."
			)
			if draft
			else "",
		},
		summary=(
			f"{len(rows)} activity cost pool(s)"
			+ (f" for {company}" if company else "")
			+ f", {total} total ({ledger} ledger, {manual} manual)"
		),
	)


# ── the engine ──────────────────────────────────────────────────────────────
def _supplied_drivers(raw) -> dict:
	"""`{activity_key: {cost_object: quantity}}` from the caller's own measurements."""
	if raw is None:
		return {}
	if not isinstance(raw, list):
		raise ToolError(
			'driver_quantities must be a list of objects, e.g. [{"activity": "Dormant spray", '
			'"cost_object": "FIELD-0001", "quantity": 42}]'
		)
	out: dict = {}
	for index, entry in enumerate(raw, start=1):
		if not isinstance(entry, dict):
			raise ToolError(f"driver_quantities[{index}] must be an object.")
		activity = str(entry.get("activity") or "").strip()
		if not activity:
			raise ToolError(f"driver_quantities[{index}] needs an activity — its docname or its activity_name.")
		cost_object = str(entry.get("cost_object") or entry.get("field") or "").strip()
		if not cost_object:
			raise ToolError(
				f"driver_quantities[{index}] needs a cost_object naming which block consumed this much "
				"of the activity."
			)
		quantity = entry.get("quantity")
		if quantity is None:
			raise ToolError(f"driver_quantities[{index}] needs a quantity.")
		try:
			quantity = float(quantity)
		except (TypeError, ValueError):
			raise ToolError(f"driver_quantities[{index}] quantity must be a number, got {quantity!r}.") from None
		if quantity < 0:
			raise ToolError(
				f"driver_quantities[{index}] quantity is negative. A negative driver quantity gives one "
				"block a negative share of a positive pool, which credits it at every other block's "
				"expense."
			)
		out.setdefault(activity, {})[cost_object] = out.setdefault(activity, {}).get(cost_object, 0.0) + quantity
	return out


def _drivers_for(activity_doc, pool_row: dict, supplied: dict, acres_by_field: dict) -> dict:
	"""Which cost objects this activity's pool reaches, and in what proportion.

	Returns `{"objects": [(cost_object, quantity)], "source": str}` or
	`{"objects": [], "reason": str}` — and the empty case is a first-class answer
	rather than an error, because "this activity reached no block" is exactly the
	finding the run exists to surface.
	"""
	measured = supplied.get(activity_doc.name) or supplied.get(activity_doc.activity_name) or {}
	if measured:
		return {
			"objects": sorted(measured.items()),
			"source": (
				f"supplied by the caller: measured {activity_doc.cost_driver.lower()} per block"
				+ (
					" (overriding the acreage this app would otherwise have derived)"
					if activity_doc.cost_driver == "Acres"
					else ""
				)
			),
		}

	if activity_doc.cost_driver == "Direct Assignment":
		cost_object = str(pool_row.get("cost_object") or "").strip()
		if not cost_object:
			return {
				"objects": [],
				"reason": (
					"driven by Direct Assignment but the pool names no cost_object, so there is no "
					"block to assign it to. Set one on the pool."
				),
			}
		return {
			"objects": [(cost_object, 1.0)],
			"source": "direct assignment: the pool names the one block this cost was incurred for",
		}

	if activity_doc.cost_driver == "Acres":
		objects = [(field, acres) for field, acres in sorted(acres_by_field.items()) if acres > 0]
		if not objects:
			return {
				"objects": [],
				"reason": (
					"driven by Acres, and no block was productive for any part of the period — so "
					"there is no acreage to divide by. A block still in its pre-yield years is not a "
					"denominator."
				),
			}
		return {
			"objects": objects,
			"source": (
				"derived: each block's acreage weighted by the days it was productive in the period, "
				"from the Field register"
			),
		}

	return {
		"objects": [],
		"reason": (
			f"driven by {activity_doc.cost_driver}, which is a MEASUREMENT this app cannot derive "
			"from the Field register. Pass driver_quantities for this activity. Its cost is reported "
			"here in full rather than spread evenly across blocks — an even spread is "
			"indistinguishable in the output from a measured one."
		),
	}


def _allocate(pool_amount: float, objects: list) -> list:
	"""Split a pool across cost objects by driver quantity, exactly.

	THE RESIDUAL IS PLACED, NOT DROPPED. Rounding each share to the cent leaves a
	few cents that belong to nobody, and a run whose lines do not add up to its
	pools is a run whose totals disagree with themselves. The residual goes to the
	largest consumer, which is where it is proportionally smallest.
	"""
	total_quantity = sum(quantity for _, quantity in objects)
	if total_quantity <= 0:
		return []
	ordered = sorted(objects, key=lambda pair: (-pair[1], pair[0]))
	lines = []
	for cost_object, quantity in ordered:
		share = quantity / total_quantity
		lines.append(
			{
				"cost_object": cost_object,
				"driver_quantity": round(quantity, 4),
				"driver_share": round(share * 100.0, 4),
				"assigned_amount": round(pool_amount * share, 2),
			}
		)
	residual = round(pool_amount - sum(line["assigned_amount"] for line in lines), 2)
	if residual:
		lines[0]["assigned_amount"] = round(lines[0]["assigned_amount"] + residual, 2)
		lines[0]["rounding_residual"] = residual
	return lines


def compute_abc_allocation(args: dict) -> ToolResult:
	"""Push every activity pool out to the blocks that consumed it. MUTATING.

	WHAT IT WRITES is one `ABC Cost Assignment` holding every line of the run —
	driver quantity, share, pool, amount and acres — and it APPENDS rather than
	replacing, so a rerun after a corrected pool leaves the earlier belief on the
	record. `dry_run` computes the whole thing and writes nothing.

	WHAT IT REFUSES TO DO is estimate a driver quantity. An activity whose driver
	is a measurement nobody supplied is reported UNALLOCATED with its full amount
	and the sentence naming what would fix it. Its money is in `unassigned_amount`
	rather than in the assigned total or spread evenly across blocks.

	`field` NARROWS THE ROWS RETURNED AND NEVER THE ARITHMETIC. Every share is
	computed against every block that consumed the activity, because a share
	computed against one block is 100% by construction — and the document stores
	the whole run for the same reason.
	"""
	_require_assignments()
	company = resolve_company(as_str(args, "company"), required=True)
	fiscal_year = as_str(args, "fiscal_year", required=True)
	period_start, period_end = _fiscal_window(fiscal_year, args)
	dry_run = as_bool(args, "dry_run", False)
	only_field = as_str(args, "field")
	if only_field and not frappe.db.exists(kpi.FIELD_DOCTYPE, only_field):
		raise ToolError(
			f"no Field named {only_field!r} on this site. list_fields has the register. Nothing was "
			"computed."
		)
	supplied = _supplied_drivers(args.get("driver_quantities"))

	acres = acre_service.productive_acres(company, period_start, period_end)
	acres_by_field = {
		str(row["field"]): float(row["time_weighted_acres"] or 0) for row in acres.get("itemized") or []
	}
	total_acres = float(acres.get("time_weighted") or 0)

	pool_rows = [
		dict(row)
		for row in frappe.db.get_all(
			POOL,
			filters={"company": company, "fiscal_year": fiscal_year},
			fields=compat.existing_fields(POOL, _POOL_FIELDS),
			order_by="activity asc",
			limit=RECORD_CAP,
		)
		or []
	]
	if not pool_rows:
		raise ToolError(
			f"no Activity Cost Pool for {company} in {fiscal_year}. An allocation needs pools to "
			"push out — create_activity_cost_pool is where one starts, and list_cost_activities "
			"has the activities that can have them."
		)

	skipped_drafts = [row["name"] for row in pool_rows if row.get("status") == "Draft"]
	live = [row for row in pool_rows if row.get("status") != "Draft"]
	if not live:
		raise ToolError(
			f"every Activity Cost Pool for {company} in {fiscal_year} is Draft, so there is nothing "
			f"to allocate ({len(skipped_drafts)} pool(s) skipped). A draft pool is not a zero pool. "
			"Set status to Ready on the ones that are finished."
		)

	lines = []
	unallocated = []
	total_pool = 0.0
	total_assigned = 0.0
	residuals = 0.0
	consumed_pools = []

	for pool_row in live:
		try:
			activity_doc = frappe.get_doc(ACTIVITY, pool_row["activity"])
		except Exception:
			unallocated.append(
				{
					"pool": pool_row["name"],
					"activity": pool_row["activity"],
					"activity_name": pool_row["activity"],
					"phase": None,
					"cost_driver": None,
					"pool_amount": round(float(pool_row.get("pool_amount") or 0), 2),
					"reason": (
						"its Cost Activity is missing from this site, so neither its phase nor its "
						"driver can be read. The pool's money is reported here rather than dropped."
					),
				}
			)
			total_pool += float(pool_row.get("pool_amount") or 0)
			continue

		pool_amount = round(float(pool_row.get("pool_amount") or 0), 2)
		total_pool += pool_amount
		consumed_pools.append(pool_row["name"])

		resolved = _drivers_for(activity_doc, pool_row, supplied, acres_by_field)
		if not resolved["objects"]:
			unallocated.append(
				{
					"pool": pool_row["name"],
					"activity": activity_doc.name,
					"activity_name": activity_doc.activity_name,
					"phase": activity_doc.phase,
					"cost_driver": activity_doc.cost_driver,
					"pool_amount": pool_amount,
					"reason": resolved["reason"],
				}
			)
			lines.append(
				{
					"activity": activity_doc.name,
					"activity_name": activity_doc.activity_name,
					"phase": activity_doc.phase,
					"cost_driver": activity_doc.cost_driver,
					"cost_object_type": "Company",
					"cost_object": company,
					"cost_object_name": company,
					"driver_quantity": 0.0,
					"driver_share": 0.0,
					"pool_amount": pool_amount,
					"assigned_amount": 0.0,
					"productive_acres": 0.0,
					"cost_per_acre": None,
					"driver_source": f"UNALLOCATED — {resolved['reason']}",
				}
			)
			continue

		for split in _allocate(pool_amount, resolved["objects"]):
			cost_object = split["cost_object"]
			object_acres = round(acres_by_field.get(cost_object, 0.0), 4)
			assigned = split["assigned_amount"]
			total_assigned += assigned
			residuals += split.get("rounding_residual", 0.0)
			lines.append(
				{
					"activity": activity_doc.name,
					"activity_name": activity_doc.activity_name,
					"phase": activity_doc.phase,
					"cost_driver": activity_doc.cost_driver,
					"cost_object_type": "Field",
					"cost_object": cost_object,
					"cost_object_name": frappe.db.get_value(kpi.FIELD_DOCTYPE, cost_object, "field_name")
					or cost_object,
					"driver_quantity": split["driver_quantity"],
					"driver_share": split["driver_share"],
					"pool_amount": pool_amount,
					"assigned_amount": assigned,
					"productive_acres": object_acres,
					"cost_per_acre": round(assigned / object_acres, 4) if object_acres > 0 else None,
					"driver_source": resolved["source"],
				}
			)

	total_pool = round(total_pool, 2)
	total_assigned = round(total_assigned, 2)
	unassigned = round(total_pool - total_assigned, 2)

	basis = (
		f"{len(live)} pool(s) over {period_start} to {period_end}. Acres driven activities were "
		f"derived from the Field register weighted by days productive ({total_acres} time-weighted "
		f"acres across {acres.get('field_count_productive', 0)} block(s)); "
		f"{len(supplied)} activity(ies) used driver quantities supplied by the caller. "
		f"{len(unallocated)} activity(ies) reached no block."
	)
	unallocated_note = (
		"; ".join(f"{row['activity_name']}: {row['reason']}" for row in unallocated)[:1000]
		if unallocated
		else ""
	)

	assignment_name = None
	if not dry_run:
		doc = frappe.new_doc(ASSIGNMENT)
		doc.company = company
		doc.fiscal_year = fiscal_year
		doc.period_start = period_start
		doc.period_end = period_end
		doc.computed_on = frappe.utils.now()
		doc.computed_by = frappe.session.user
		doc.total_pool_amount = total_pool
		doc.total_assigned = total_assigned
		doc.unassigned_amount = unassigned
		doc.productive_acres = round(total_acres, 4)
		doc.activity_count = len(live)
		doc.basis = basis
		doc.unallocated_note = unallocated_note
		for line in lines:
			doc.append("lines", line)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		assignment_name = doc.name

		# The flag records that a run consumed the pool. It does NOT lock it — a
		# corrected pool that could not be re-allocated would freeze an error in
		# place, which is the opposite of what an audit trail is for.
		for pool_name in consumed_pools:
			frappe.db.set_value(POOL, pool_name, "status", "Allocated", update_modified=False)

	shown = [line for line in lines if not only_field or line["cost_object"] == only_field]

	data = {
		"company": company,
		"fiscal_year": fiscal_year,
		"period_start": period_start,
		"period_end": period_end,
		"assignment": assignment_name,
		"dry_run": dry_run,
		"total_pool_amount": total_pool,
		"total_assigned": total_assigned,
		"unassigned_amount": unassigned,
		"productive_acres": round(total_acres, 4),
		"cost_per_acre": round(total_assigned / total_acres, 4) if total_acres > 0 else None,
		"activity_count": len(live),
		"line_count": len(lines),
		"lines": shown,
		"unallocated": unallocated,
		"skipped_draft_pools": skipped_drafts,
		"rounding_residual_placed": round(residuals, 2),
		"basis": basis,
		"identity": (
			f"total_assigned ({total_assigned}) + unassigned_amount ({unassigned}) = "
			f"total_pool_amount ({total_pool}). Both halves are stored so a reader can check the "
			"identity without rerunning the engine."
		),
	}
	if unassigned:
		data["unassigned_note"] = (
			f"{unassigned} of pool money reached no block. It is NOT in the assigned total and it "
			"was NOT spread evenly across blocks — an even spread is indistinguishable in the "
			"output from a measured one, which is the answer activity-based costing was adopted to "
			"stop giving. `unallocated` names each activity and the measurement that would fix it."
		)
	if only_field:
		data["filter_note"] = (
			f"Rows are narrowed to {only_field}, and the arithmetic is NOT. Every driver share above "
			"was computed against every block that consumed its activity, because a share computed "
			f"against one block is 100% by construction. {len(lines)} line(s) were computed and "
			f"{len(shown)} are shown; the stored assignment holds all of them."
		)
	if total_acres <= 0:
		data["acreage_note"] = (
			"No block was productive for any part of this period, so there is no per-acre figure — "
			"reported as null rather than zero, because zero is a per-acre cost for a division "
			"nobody performed. Fields need `productive_from_date` set for the acreage to count."
		)
	if skipped_drafts:
		data["draft_note"] = (
			f"{len(skipped_drafts)} Draft pool(s) were skipped. A draft pool is not a zero pool."
		)

	return ToolResult(
		data=data,
		summary=(
			f"{'computed (dry run)' if dry_run else f'allocated into {assignment_name}'}: "
			f"{total_assigned} of {total_pool} across {len(lines)} line(s) for {company} "
			f"{fiscal_year}"
			+ (f", {unassigned} unassigned" if unassigned else "")
			+ (f", {round(total_assigned / total_acres, 2)} per acre" if total_acres > 0 else "")
		),
		docstatus_delta="" if dry_run else "none → 0 (draft)",
	)


# ── the reads ───────────────────────────────────────────────────────────────
def _assignment_doc(company: str, fiscal_year: str, reference: str = ""):
	"""One stored run — the named one, or the newest for the year."""
	if reference:
		if not frappe.db.exists(ASSIGNMENT, reference):
			raise ToolError(
				f"no ABC Cost Assignment named {reference!r} on this site. Omit `assignment` to read "
				"the newest run for the year."
			)
		return frappe.get_doc(ASSIGNMENT, reference)
	rows = frappe.db.get_all(
		ASSIGNMENT,
		filters={"company": company, "fiscal_year": fiscal_year},
		fields=["name", "computed_on"],
		order_by="computed_on desc, creation desc",
		limit=1,
	)
	if not rows:
		raise ToolError(
			f"no ABC Cost Assignment for {company} in {fiscal_year}. compute_abc_allocation is what "
			"produces one — this read never computes, so a report can never disagree with the run "
			"it claims to be reporting."
		)
	return frappe.get_doc(ASSIGNMENT, rows[0]["name"])


def _lines_of(doc) -> tuple[list, list]:
	"""`(assigned_lines, unallocated_lines)` off a stored run.

	The split is by `cost_object_type`: a line that reached a block is a Field
	line, and an activity that reached nothing is stored against the company with
	zero assigned. Keeping the second in the document rather than only in prose is
	what lets a report say which stage of the pipeline is under-measured, rather
	than only that something is.
	"""
	assigned, unallocated = [], []
	for row in doc.get("lines") or []:
		line = {
			"activity": _get(row, "activity"),
			"activity_name": _get(row, "activity_name"),
			"phase": _get(row, "phase"),
			"cost_driver": _get(row, "cost_driver"),
			"cost_object_type": _get(row, "cost_object_type"),
			"cost_object": _get(row, "cost_object"),
			"cost_object_name": _get(row, "cost_object_name"),
			"driver_quantity": float(_get(row, "driver_quantity") or 0),
			"driver_share": float(_get(row, "driver_share") or 0),
			"pool_amount": float(_get(row, "pool_amount") or 0),
			"assigned_amount": float(_get(row, "assigned_amount") or 0),
			"productive_acres": float(_get(row, "productive_acres") or 0),
			"cost_per_acre": (
				float(_get(row, "cost_per_acre")) if _get(row, "cost_per_acre") is not None else None
			),
			"driver_source": _get(row, "driver_source"),
		}
		if line["cost_object_type"] == "Company":
			unallocated.append(line)
		else:
			assigned.append(line)
	return assigned, unallocated


def get_abc_assignment(args: dict) -> ToolResult:
	"""One stored allocation run in full, intermediates and all. Read-only."""
	_require_assignments()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _assignment_doc(company, as_str(args, "fiscal_year"), as_str(args, "assignment"))
	assigned, unallocated = _lines_of(doc)
	return ToolResult(
		data={
			"name": doc.name,
			"company": doc.company,
			"fiscal_year": doc.fiscal_year,
			"period_start": str(doc.period_start or "") or None,
			"period_end": str(doc.period_end or "") or None,
			"computed_on": str(doc.computed_on or "") or None,
			"computed_by": doc.computed_by or None,
			"total_pool_amount": float(doc.total_pool_amount or 0),
			"total_assigned": float(doc.total_assigned or 0),
			"unassigned_amount": float(doc.unassigned_amount or 0),
			"productive_acres": float(doc.productive_acres or 0),
			"activity_count": int(doc.activity_count or 0),
			"line_count": int(doc.line_count or 0),
			"lines": assigned,
			"unallocated": unallocated,
			"basis": doc.basis or None,
			"unallocated_note": doc.unallocated_note or None,
		},
		summary=(
			f"{doc.name}: {float(doc.total_assigned or 0)} assigned of "
			f"{float(doc.total_pool_amount or 0)} across {len(assigned)} line(s), "
			f"{float(doc.unassigned_amount or 0)} unassigned"
		),
	)


def get_abc_report(args: dict) -> ToolResult:
	"""Per-acre cost grouped by field, activity or phase. Read-only.

	THE PER-ACRE VIEW IS THE PRIMARY MANAGEMENT REPORT, and the denominator
	changes with the grouping, which is the one thing about it worth reading
	carefully. Grouped BY FIELD, each block is divided by ITS OWN productive
	acres — 'what did the Home Block cost me per acre'. Grouped by activity or by
	phase, the total is divided by the WHOLE operation's productive acres — 'what
	did spraying cost me per acre across the farm'. Both are stated on every
	group, because the two are different numbers and a reader who assumes the
	wrong one is wrong by the ratio of one block to the farm.

	IT NEVER COMPUTES. The report reads a stored run, so it can never disagree
	with the allocation it claims to be reporting.
	"""
	_require_assignments()
	company = resolve_company(as_str(args, "company"), required=True)
	fiscal_year = as_str(args, "fiscal_year")
	group_by = (as_str(args, "group_by") or "field").lower()
	if group_by not in GROUP_BY:
		raise ToolError(f"group_by must be one of: {', '.join(GROUP_BY)}. Got {group_by!r}.")

	doc = _assignment_doc(company, fiscal_year, as_str(args, "assignment"))
	assigned, unallocated = _lines_of(doc)
	total_acres = float(doc.productive_acres or 0)

	only_field = as_str(args, "field")
	if only_field:
		assigned = [line for line in assigned if line["cost_object"] == only_field]

	keys = {
		"field": lambda line: (line["cost_object"], line["cost_object_name"]),
		"activity": lambda line: (line["activity"] or line["activity_name"], line["activity_name"]),
		"phase": lambda line: (line["phase"], line["phase"]),
	}[group_by]

	groups: dict = {}
	for line in assigned:
		key, label = keys(line)
		key = key or "(unset)"
		entry = groups.setdefault(
			key,
			{
				"key": key,
				"label": label or key,
				"assigned_amount": 0.0,
				"line_count": 0,
				"acres": 0.0,
				"activities": set(),
				"phases": set(),
				"fields": set(),
			},
		)
		entry["assigned_amount"] += line["assigned_amount"]
		entry["line_count"] += 1
		entry["activities"].add(line["activity_name"])
		entry["phases"].add(line["phase"])
		entry["fields"].add(line["cost_object"])
		if group_by == "field":
			# The block's own acreage, which is one number however many activities
			# reached it — taking a max rather than a sum, because summing it once
			# per activity would divide by the acreage several times over and make
			# a heavily worked block look cheap.
			entry["acres"] = max(entry["acres"], line["productive_acres"])

	out = []
	for key in sorted(groups, key=lambda name: -groups[name]["assigned_amount"]):
		entry = groups[key]
		acres = entry["acres"] if group_by == "field" else total_acres
		amount = round(entry["assigned_amount"], 2)
		out.append(
			{
				"key": entry["key"],
				"label": entry["label"],
				"assigned_amount": amount,
				"line_count": entry["line_count"],
				"acres": round(acres, 4),
				"acres_basis": (
					"this block's own time-weighted productive acres"
					if group_by == "field"
					else "the whole operation's time-weighted productive acres"
				),
				"cost_per_acre": round(amount / acres, 4) if acres > 0 else None,
				"activities": sorted(value for value in entry["activities"] if value),
				"phases": sorted(value for value in entry["phases"] if value),
				"field_count": len(entry["fields"]),
			}
		)

	assigned_total = round(sum(row["assigned_amount"] for row in out), 2)
	zero_acre = [row["key"] for row in out if not row["acres"]]

	if group_by == "phase":
		# Every phase, whether or not anything reached it. A phase missing from
		# the list reads as "no cost here"; a phase present at zero reads as "no
		# activity is mapped here", and only one of those is a data problem.
		present = {row["key"] for row in out}
		for phase in PHASES:
			if phase not in present:
				out.append(
					{
						"key": phase,
						"label": phase,
						"assigned_amount": 0.0,
						"line_count": 0,
						"acres": round(total_acres, 4),
						"acres_basis": "the whole operation's time-weighted productive acres",
						"cost_per_acre": 0.0 if total_acres > 0 else None,
						"activities": [],
						"phases": [phase],
						"field_count": 0,
						"note": (
							"No activity in this run is in this phase. That is different from this "
							"phase costing nothing, and only one of the two is worth investigating."
						),
					}
				)
		out.sort(key=lambda row: PHASES.index(row["key"]) if row["key"] in PHASES else len(PHASES))

	return ToolResult(
		data={
			"assignment": doc.name,
			"company": doc.company,
			"fiscal_year": doc.fiscal_year,
			"period_start": str(doc.period_start or "") or None,
			"period_end": str(doc.period_end or "") or None,
			"group_by": group_by,
			"groups": out,
			"group_count": len(out),
			"assigned_total": assigned_total,
			"unassigned_amount": float(doc.unassigned_amount or 0),
			"productive_acres": round(total_acres, 4),
			"cost_per_acre_overall": (
				round(float(doc.total_assigned or 0) / total_acres, 4) if total_acres > 0 else None
			),
			"unallocated": unallocated,
			"denominator_note": (
				"GROUPED BY FIELD, each block is divided by ITS OWN productive acres. Grouped by "
				"activity or phase, the group total is divided by the WHOLE operation's productive "
				"acres. `acres_basis` says which on every row, because the two are different "
				"numbers and a reader who assumes the wrong one is wrong by the ratio of one block "
				"to the farm."
			),
			"unassigned_note": (
				f"{float(doc.unassigned_amount or 0)} of pool money reached no block in this run and "
				"is therefore in NONE of these groups. Every per-acre figure here is understated by "
				"its share of that. `unallocated` names the activities."
			)
			if float(doc.unassigned_amount or 0)
			else "",
			"zero_acre_note": (
				f"{len(zero_acre)} group(s) have no productive acres, so their cost_per_acre is null "
				"rather than zero — zero would be a per-acre figure for a division nobody performed."
			)
			if zero_acre
			else "",
		},
		summary=(
			f"{doc.name} by {group_by}: {len(out)} group(s), {assigned_total} assigned"
			+ (f", {round(assigned_total / total_acres, 2)} per acre overall" if total_acres > 0 else "")
		),
	)


def get_phase_waterfall(args: dict) -> ToolResult:
	"""Cost accumulating through Growing → Harvest → Post-Harvest → Packing → Sales. Read-only.

	THE SHAPE IS THE ANSWER, NOT THE TOTAL. Cost does not land on a bin all at
	once, and "where did this get expensive" is a question about the accumulation
	rather than the sum. So every phase reports what it ADDED and what the fruit
	is CARRYING by the time it leaves — per acre always, and per unit when
	somebody supplies the units.

	IT WILL NOT INVENT A UNIT COUNT. With no `units`, per-unit is null and the
	report says why. Picking a denominator is the step that turns a costing report
	into fiction, which is the same rule `get_absorption_cost_report` follows and
	is not reopened here.
	"""
	_require_assignments()
	company = resolve_company(as_str(args, "company"), required=True)
	fiscal_year = as_str(args, "fiscal_year")
	doc = _assignment_doc(company, fiscal_year, as_str(args, "assignment"))
	assigned, unallocated = _lines_of(doc)

	only_field = as_str(args, "field")
	if only_field:
		assigned = [line for line in assigned if line["cost_object"] == only_field]

	units = args.get("units")
	units = float(units) if units is not None else None
	if units is not None and units <= 0:
		raise ToolError(
			f"units must be greater than zero; got {units}. A cumulative cost per unit over no units "
			"is a division nobody performed."
		)
	uom = as_str(args, "uom") or "bin"

	total_acres = float(doc.productive_acres or 0)
	if only_field:
		per_field = [line["productive_acres"] for line in assigned]
		total_acres = max(per_field) if per_field else 0.0

	added: dict = {phase: 0.0 for phase in PHASES}
	activities: dict = {phase: set() for phase in PHASES}
	off_waterfall = 0.0
	for line in assigned:
		phase = line["phase"]
		if phase in added:
			added[phase] += line["assigned_amount"]
			activities[phase].add(line["activity_name"])
		else:
			off_waterfall += line["assigned_amount"]

	unallocated_by_phase: dict = {}
	for line in unallocated:
		unallocated_by_phase[line["phase"] or "(unset)"] = round(
			unallocated_by_phase.get(line["phase"] or "(unset)", 0.0) + line["pool_amount"], 2
		)

	stages = []
	cumulative = 0.0
	for phase in PHASES:
		amount = round(added[phase], 2)
		cumulative = round(cumulative + amount, 2)
		stage = {
			"phase": phase,
			"cost_added": amount,
			"cumulative_cost": cumulative,
			"cost_added_per_acre": round(amount / total_acres, 4) if total_acres > 0 else None,
			"cumulative_per_acre": round(cumulative / total_acres, 4) if total_acres > 0 else None,
			"cost_added_per_unit": round(amount / units, 4) if units else None,
			"cumulative_per_unit": round(cumulative / units, 4) if units else None,
			"activity_count": len(activities[phase]),
			"activities": sorted(value for value in activities[phase] if value),
			"unallocated_amount": unallocated_by_phase.get(phase, 0.0),
		}
		if not stage["activity_count"]:
			stage["note"] = (
				"No activity in this run reached a block in this phase. That is different from this "
				"phase costing nothing — an unmapped phase and a free one look identical in a total "
				"and are not the same finding."
			)
		if stage["unallocated_amount"]:
			stage["unallocated_note"] = (
				f"{stage['unallocated_amount']} of this phase's pool money reached no block and is "
				"NOT in the figures above, so this stage of the waterfall is understated by that "
				"much. It is the phase's own measurement gap, which is why it is reported here "
				"rather than only in the run's total."
			)
		stages.append(stage)

	data = {
		"assignment": doc.name,
		"company": doc.company,
		"fiscal_year": doc.fiscal_year,
		"period_start": str(doc.period_start or "") or None,
		"period_end": str(doc.period_end or "") or None,
		"field": only_field or None,
		"phases": stages,
		"total_cost": round(cumulative, 2),
		"productive_acres": round(total_acres, 4),
		"total_per_acre": round(cumulative / total_acres, 4) if total_acres > 0 else None,
		"units": units,
		"uom": uom,
		"total_per_unit": round(cumulative / units, 4) if units else None,
		"unassigned_amount": float(doc.unassigned_amount or 0),
		"unallocated_by_phase": unallocated_by_phase,
		"unallocated": unallocated,
		"reading_it": (
			"Read the SHAPE, not the total. A bin leaves the block carrying its growing cost and "
			"picks up the rest on the way to the customer, so the phase where `cumulative_per_unit` "
			"jumps is the phase worth managing. The total is available from any ledger; the "
			"accumulation is not."
		),
	}
	if units is None:
		data["unit_note"] = (
			"No `units` were given, so the per-unit column is null and the per-acre column carries "
			"the report. THIS TOOL WILL NOT PICK A DENOMINATOR — a unit count chosen by an "
			"algorithm is the step that turns a costing report into fiction. Pass units (bins "
			"packed, boxes shipped) to watch cost per unit accumulate."
		)
	if off_waterfall:
		data["off_waterfall_amount"] = round(off_waterfall, 2)
		data["off_waterfall_note"] = (
			f"{round(off_waterfall, 2)} was assigned by activities whose phase is not one of the "
			f"five ({', '.join(PHASES)}), so it is in no stage above. The total below the stages "
			"excludes it."
		)
	if float(doc.unassigned_amount or 0):
		data["unassigned_note"] = (
			f"{float(doc.unassigned_amount or 0)} of pool money reached no block in this run and is "
			"in no stage of this waterfall. `unallocated_by_phase` says which stages are "
			"understated and by how much."
		)
	if only_field:
		data["field_note"] = (
			f"Narrowed to {only_field}. The amounts are that block's share of each activity, "
			"computed in the stored run against every block that consumed it — this filter reads "
			"the run, it does not re-run it."
		)

	return ToolResult(
		data=data,
		summary=(
			f"{doc.name} waterfall: {round(cumulative, 2)} accumulating across {len(PHASES)} phases"
			+ (f" ({round(cumulative / total_acres, 2)} per acre)" if total_acres > 0 else "")
			+ (f", {round(cumulative / units, 2)} per {uom}" if units else "")
		),
	)
