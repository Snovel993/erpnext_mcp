# SPDX-License-Identifier: MIT
"""Budget vs. actual, as a record. Seven tools, and only four of them write.

v0.42.0. THE ARITHMETIC IS NOT HERE. Everything about what counts as a variance
and how far over its threshold a breach is lives in `erpnext_mcp/budget_engine.py`,
which is pure and reads no database — the same split `payroll_gl.py` and
`tools/payroll_gl.py` keep, for the same reason: the figures a caller previews
and the figures written back onto the record come out of one computation and
cannot drift apart.

WHAT THIS MODULE ADDS ON TOP OF THE PURE ENGINE is the two things a pure
function cannot do — read the general ledger and the KPI framework, and write
the result back onto a Budget document:

  * `_gl_balances_for` sums GL Entry movement per account, year-to-date within
    the budget's own fiscal year, the same `debit − credit` / `credit − debit`
    sign convention `kpi_engine`'s GL inputs use so a budget line and a KPI
    ratio built from the same account never disagree about its sign.
  * KPI targets read `kpi_engine.compute_kpi(..., use_cache=True)` — THE SAME
    CACHED FIGURE THE DASHBOARD SHOWS, filled by the 3am KPI history sweep,
    rather than a fresh recomputation. A budget and a KPI dashboard reading two
    different numbers for one KPI would be worse than either being slow.

REFRESHING WRITES COMPUTED FIELDS AND NOTHING ELSE. `refresh_budget` updates
`actual_amount`, `variance_amount` and `variance_pct` on the budget's own child
rows and touches no Account, no GL Entry and no Financial KPI History. It does
NOT write a Compliance Alert directly — see `alerts/rules.py`'s
`budget_variance_breach`, which reads these same stored fields on every Active
budget the way `financial_kpi_threshold_breach` reads the KPI cache. That
keeps ONE rule engine deciding what reaches the compliance calendar, rather
than a second alerting path for money that the sweep's dismissal, snooze and
auto-clear machinery does not know about.

WHO MAY CALL THE MUTATING FOUR. The same three roles the KPI framework uses —
System Manager, Accounts Manager, Farm Manager — via `kpi_tools.require_kpi_role`.
A budget is the same class of financial judgement as a KPI threshold: what a
lender is shown and what counts as off-plan.
"""

from __future__ import annotations

import frappe

from .. import budget_engine, compat
from ..args import as_float, as_limit, as_str, resolve_account, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from ..services import kpi_engine
from . import kpi as kpi_tools

DOCTYPE = "Budget"
LINE_ITEM = "Budget Line Item"
KPI_TARGET = "Budget KPI Target"

#: Most budgets one list call returns. A site with two hundred budgets has a
#: problem this cap does not solve, but a cap that is reported beats a register
#: that silently stops somewhere nobody chose.
RECORD_CAP = 200


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _get(row, key, default=None):
	"""One field off a child row, whichever shape the framework handed back."""
	if isinstance(row, dict):
		return row.get(key, default)
	return getattr(row, key, default)


def _num_or_zero(value) -> float:
	return float(value) if value is not None else 0.0


# ── resolving ─────────────────────────────────────────────────────────────


def _resolve(reference: str):
	reference = str(reference or "").strip()
	if not reference:
		raise ToolError(
			"budget is required — a Budget docname or budget_name. list_budgets has the "
			"register. Nothing was changed."
		)
	if frappe.db.exists(DOCTYPE, reference):
		return frappe.get_doc(DOCTYPE, reference)
	found = frappe.db.get_value(DOCTYPE, {"budget_name": reference}, "name")
	if found:
		return frappe.get_doc(DOCTYPE, str(found))
	raise ToolError(
		f"no Budget called {reference!r} on this site. list_budgets has the register. Nothing was changed."
	)


def _reference(args: dict) -> str:
	return as_str(args, "budget") or as_str(args, "name") or as_str(args, "budget_name", required=True)


def _resolve_fiscal_year(fiscal_year: str) -> str:
	fiscal_year = str(fiscal_year or "").strip()
	if not fiscal_year:
		raise ToolError("fiscal_year is required. list_fiscal_years names the ones this site has.")
	if not frappe.db.exists("Fiscal Year", fiscal_year):
		raise ToolError(f"no Fiscal Year called {fiscal_year!r} on this site. Nothing was changed.")
	return fiscal_year


# ── reading a document ───────────────────────────────────────────────────


def _line_items_of(doc) -> list:
	return [
		{
			"account": _get(row, "account") or "",
			"budgeted_amount": _num_or_zero(_get(row, "budgeted_amount")),
			"actual_amount": _num_or_zero(_get(row, "actual_amount")),
			"variance_amount": _num_or_zero(_get(row, "variance_amount")),
			"variance_pct": _num_or_zero(_get(row, "variance_pct")),
			"threshold_pct": _num_or_zero(_get(row, "threshold_pct")) or budget_engine.DEFAULT_THRESHOLD_PCT,
		}
		for row in doc.get("line_items") or []
	]


def _kpi_targets_of(doc) -> list:
	return [
		{
			"kpi_definition": _get(row, "kpi_definition") or "",
			"target_value": _num_or_zero(_get(row, "target_value")),
			"actual_value": _num_or_zero(_get(row, "actual_value")),
			"variance_pct": _num_or_zero(_get(row, "variance_pct")),
			"threshold_pct": _num_or_zero(_get(row, "threshold_pct")) or budget_engine.DEFAULT_THRESHOLD_PCT,
		}
		for row in doc.get("kpi_targets") or []
	]


def _budget_doc_dict(doc) -> dict:
	"""The document read into the plain shape `budget_engine` takes."""
	return {
		"line_items": [
			{
				"account": _get(row, "account"),
				"budgeted_amount": _get(row, "budgeted_amount"),
				"threshold_pct": _get(row, "threshold_pct"),
			}
			for row in doc.get("line_items") or []
		],
		"kpi_targets": [
			{
				"kpi_definition": _get(row, "kpi_definition"),
				"target_value": _get(row, "target_value"),
				"threshold_pct": _get(row, "threshold_pct"),
			}
			for row in doc.get("kpi_targets") or []
		],
	}


def _describe(doc) -> dict:
	return {
		"name": doc.name,
		"budget_name": doc.budget_name,
		"company": doc.company,
		"fiscal_year": doc.fiscal_year,
		"status": doc.status,
		"notes": doc.get("notes") or None,
		"last_refreshed": str(doc.get("last_refreshed") or "") or None,
		"line_items": _line_items_of(doc),
		"kpi_targets": _kpi_targets_of(doc),
	}


# ── reading arguments ─────────────────────────────────────────────────────


def _requested_line_items(raw, company: str) -> list:
	"""`line_items` as `create_budget`/`update_budget` take it: a list of objects."""
	if raw is None:
		return []
	if not isinstance(raw, list):
		raise ToolError(
			"line_items must be a list of objects, each with an account and a budgeted_amount. "
			"Nothing was changed."
		)
	rows = []
	seen = set()
	for index, entry in enumerate(raw, start=1):
		if not isinstance(entry, dict):
			raise ToolError(f"line_items[{index}] must be an object, got {type(entry).__name__}.")
		account = str(entry.get("account") or "").strip()
		if not account:
			raise ToolError(f"line_items[{index}] has no account. Nothing was changed.")
		resolved = resolve_account(account, company)
		if resolved in seen:
			raise ToolError(
				f"line_items[{index}]: {resolved} already appears in this call. One row per "
				"account. Nothing was changed."
			)
		seen.add(resolved)
		row = {
			"account": resolved,
			"budgeted_amount": as_float(entry.get("budgeted_amount"), "budgeted_amount"),
		}
		if entry.get("threshold_pct") not in (None, ""):
			row["threshold_pct"] = as_float(entry.get("threshold_pct"), "threshold_pct")
		rows.append(row)
	return rows


def _requested_kpi_targets(raw) -> list:
	"""`kpi_targets` as `create_budget`/`update_budget` take it: a list of objects."""
	if raw is None:
		return []
	if not isinstance(raw, list):
		raise ToolError(
			"kpi_targets must be a list of objects, each with a kpi_definition and a "
			"target_value. Nothing was changed."
		)
	rows = []
	seen = set()
	for index, entry in enumerate(raw, start=1):
		if not isinstance(entry, dict):
			raise ToolError(f"kpi_targets[{index}] must be an object, got {type(entry).__name__}.")
		reference = str(entry.get("kpi_definition") or "").strip()
		if not reference:
			raise ToolError(f"kpi_targets[{index}] has no kpi_definition. Nothing was changed.")
		kpi_row = kpi_engine.definition_row(reference)
		if not kpi_row:
			raise ToolError(
				f"kpi_targets[{index}]: {reference!r} is not a Financial KPI Definition on this "
				"site. list_financial_kpi_definitions has the register. Nothing was changed."
			)
		name = str(kpi_row["name"])
		if name in seen:
			raise ToolError(
				f"kpi_targets[{index}]: {kpi_row.get('kpi_id')} already appears in this call. "
				"One row per KPI. Nothing was changed."
			)
		seen.add(name)
		row = {"kpi_definition": name, "target_value": as_float(entry.get("target_value"), "target_value")}
		if entry.get("threshold_pct") not in (None, ""):
			row["threshold_pct"] = as_float(entry.get("threshold_pct"), "threshold_pct")
		rows.append(row)
	return rows


# ── reading the ledger and the KPI framework ─────────────────────────────


def _fiscal_year_window(fiscal_year: str) -> tuple:
	"""The fiscal year's start date, and its end date or today, whichever is sooner.

	A budget is planned for the whole fiscal year; the actual it is compared
	against is what has genuinely happened SO FAR — a budget six months into its
	year is not "50% under" on every line, it has simply not finished yet.
	"""
	row = frappe.db.get_value("Fiscal Year", fiscal_year, ["year_start_date", "year_end_date"], as_dict=True)
	if not row:
		raise ToolError(f"no Fiscal Year called {fiscal_year!r} on this site.")
	start = str(row.get("year_start_date") or "")
	end = str(row.get("year_end_date") or "")
	today = frappe.utils.today()
	return start, (min(end, today) if end else today)


def _gl_actual(account: str, company: str, start: str, end: str) -> float:
	"""Year-to-date GL movement on one account, signed the way `kpi_engine`'s
	`natural` GL inputs are: credits less debits for Income/Liability/Equity,
	debits less credits for Asset/Expense — so an expense budget and an income
	budget both read positive when the account moved the way that root normally
	does."""
	filters = {"company": company, "account": account, "posting_date": ("between", [start, end])}
	if compat.has_field("GL Entry", "is_cancelled"):
		filters["is_cancelled"] = 0
	totals = frappe.db.get_all(
		"GL Entry", filters=filters, fields=["sum(debit) as debit", "sum(credit) as credit"]
	)
	row = (totals or [{}])[0] or {}
	debit = float(row.get("debit") or 0)
	credit = float(row.get("credit") or 0)
	root_type = str(frappe.db.get_value("Account", account, "root_type") or "")
	if root_type in kpi_engine.CREDIT_ROOTS:
		return round(credit - debit, 2)
	return round(debit - credit, 2)


def _gl_balances_for(accounts, company: str, start: str, end: str) -> dict:
	return {account: _gl_actual(account, company, start, end) for account in accounts if account}


def _kpi_values_for(doc) -> dict:
	values = {}
	for row in doc.get("kpi_targets") or []:
		kpi_definition = _get(row, "kpi_definition")
		if not kpi_definition or kpi_definition in values:
			continue
		try:
			computed = kpi_engine.compute_kpi(kpi_definition, doc.company, use_cache=True)
			values[kpi_definition] = computed.get("value")
		except Exception:  # pragma: no cover - one broken KPI must not stop a refresh
			values[kpi_definition] = None
	return values


def _refresh_budget_doc(doc) -> dict:
	"""Compute and write back one budget's actual/variance columns. Saves the doc."""
	accounts = sorted({_get(row, "account") for row in doc.get("line_items") or [] if _get(row, "account")})
	start, end = _fiscal_year_window(doc.fiscal_year)
	gl_balances = _gl_balances_for(accounts, doc.company, start, end)
	kpi_values = _kpi_values_for(doc)

	result = budget_engine.refresh_budget(_budget_doc_dict(doc), gl_balances, kpi_values)

	by_account = {row["account"]: row for row in result["line_items"]}
	for row in doc.get("line_items") or []:
		computed = by_account.get(_get(row, "account"))
		if not computed:
			continue
		row.actual_amount = _num_or_zero(computed["actual_amount"])
		row.variance_amount = _num_or_zero(computed["variance_amount"])
		row.variance_pct = _num_or_zero(computed["variance_pct"])

	by_kpi = {row["kpi_definition"]: row for row in result["kpi_targets"]}
	for row in doc.get("kpi_targets") or []:
		computed = by_kpi.get(_get(row, "kpi_definition"))
		if not computed:
			continue
		row.actual_value = _num_or_zero(computed["actual_value"])
		row.variance_pct = _num_or_zero(computed["variance_pct"])

	doc.last_refreshed = frappe.utils.now()
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	result["window"] = {"start": start, "end": end}
	return result


# ── 1. create_budget ─────────────────────────────────────────────────────


def create_budget(args: dict) -> ToolResult:
	"""Define one budget: which accounts and which KPIs it tracks, and what it
	planned for each. Every actual/variance column starts at zero — refresh_budget
	fills them in."""
	_require()
	actor = kpi_tools.require_kpi_role()

	budget_name = as_str(args, "budget_name", required=True)
	if frappe.db.exists(DOCTYPE, budget_name):
		raise ToolError(
			f"{budget_name!r} already names a Budget on this site. update_budget edits the "
			"existing one; a genuinely different budget wants a different budget_name. "
			"Nothing was changed."
		)

	company = resolve_company(as_str(args, "company"), required=True)
	fiscal_year = _resolve_fiscal_year(as_str(args, "fiscal_year", required=True))
	status = as_str(args, "status") or budget_engine.STATUS_DRAFT
	if status not in budget_engine.STATUSES:
		raise ToolError(
			f"status must be one of {', '.join(budget_engine.STATUSES)}; got {status!r}. Nothing was changed."
		)

	doc = frappe.new_doc(DOCTYPE)
	doc.budget_name = budget_name
	doc.company = company
	doc.fiscal_year = fiscal_year
	doc.status = status
	notes = as_str(args, "notes")
	if notes:
		doc.notes = notes

	for row in _requested_line_items(args.get("line_items"), company):
		doc.append("line_items", row)
	for row in _requested_kpi_targets(args.get("kpi_targets")):
		doc.append("kpi_targets", row)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = _describe(doc)
	data = {"actor": actor, "budget": described}
	if not described["line_items"] and not described["kpi_targets"]:
		data["note"] = (
			"this budget was created with no line items and no KPI targets, so refresh_budget "
			"and get_budget_variance_report will both report nothing to check. update_budget "
			"adds them."
		)
	return ToolResult(
		data=data,
		summary=(
			f"created budget {doc.name} for {company}, FY {fiscal_year}: "
			f"{len(described['line_items'])} line item(s), {len(described['kpi_targets'])} "
			f"KPI target(s), status {status}"
		),
		docstatus_delta="none → 0 (budget created)",
	)


# ── 2. update_budget ──────────────────────────────────────────────────────


def update_budget(args: dict) -> ToolResult:
	"""Edit one budget in place. `line_items`/`kpi_targets`, if passed, REPLACE
	the whole table — including every computed figure on it, which refresh_budget
	then has to rebuild."""
	_require()
	actor = kpi_tools.require_kpi_role()
	doc = _resolve(_reference(args))
	before = _describe(doc)
	changed = []

	new_name = as_str(args, "budget_name")
	if new_name and new_name != doc.budget_name:
		if frappe.db.exists(DOCTYPE, new_name):
			raise ToolError(f"{new_name!r} already names a Budget on this site. Nothing was changed.")
		doc.budget_name = new_name
		changed.append("budget_name")

	company = as_str(args, "company")
	if company:
		doc.company = resolve_company(company, required=True)
		changed.append("company")

	fiscal_year = as_str(args, "fiscal_year")
	if fiscal_year:
		doc.fiscal_year = _resolve_fiscal_year(fiscal_year)
		changed.append("fiscal_year")

	status = as_str(args, "status")
	if status:
		if status not in budget_engine.STATUSES:
			raise ToolError(
				f"status must be one of {', '.join(budget_engine.STATUSES)}; got {status!r}. "
				"Nothing was changed."
			)
		doc.status = status
		changed.append("status")

	if "notes" in args:
		doc.notes = as_str(args, "notes") or None
		changed.append("notes")

	if "line_items" in args:
		doc.set("line_items", [])
		for row in _requested_line_items(args.get("line_items"), doc.company):
			doc.append("line_items", row)
		changed.append("line_items")

	if "kpi_targets" in args:
		doc.set("kpi_targets", [])
		for row in _requested_kpi_targets(args.get("kpi_targets")):
			doc.append("kpi_targets", row)
		changed.append("kpi_targets")

	if not changed:
		raise ToolError(
			"nothing to update. Pass at least one of: budget_name, company, fiscal_year, status, "
			"notes, line_items, kpi_targets. Nothing was changed."
		)

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = _describe(doc)
	data = {"actor": actor, "budget": described, "changed_fields": changed, "previous": before}
	if "line_items" in changed or "kpi_targets" in changed:
		data["recompute_note"] = (
			"the replaced table's actual/variance columns reset to zero. refresh_budget rebuilds "
			"them from the ledger and the KPI framework."
		)
	return ToolResult(
		data=data,
		summary=f"updated budget {doc.name}: {', '.join(changed)}",
		docstatus_delta=f"{len(changed)} field(s) changed on {doc.name}",
	)


# ── 3. get_budget ─────────────────────────────────────────────────────────


def get_budget(args: dict) -> ToolResult:
	"""One budget in full, with its current breach state read from its last refresh."""
	_require()
	doc = _resolve(_reference(args))
	described = _describe(doc)
	breaches = budget_engine.check_budget_variances(
		{"line_items": described["line_items"], "kpi_targets": described["kpi_targets"]}
	)
	described["breaches"] = breaches
	described["breach_count"] = len(breaches)

	summary = (
		f"{doc.budget_name} ({doc.company}, FY {doc.fiscal_year}), {doc.status}, "
		f"{len(described['line_items'])} line item(s), {len(described['kpi_targets'])} KPI target(s)"
	)
	if not described["last_refreshed"]:
		summary += " — never refreshed"
	elif breaches:
		summary += f" — {len(breaches)} breach(es) as of last refresh"
	return ToolResult(data=described, summary=summary)


# ── 4. list_budgets ───────────────────────────────────────────────────────


def list_budgets(args: dict) -> ToolResult:
	"""The register: every budget matching the filters, newest first. Read-only."""
	_require()
	limit = min(as_limit(args), RECORD_CAP)

	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters["company"] = company
	fiscal_year = as_str(args, "fiscal_year")
	if fiscal_year:
		filters["fiscal_year"] = fiscal_year
	status = as_str(args, "status")
	if status:
		if status not in budget_engine.STATUSES:
			raise ToolError(f"status must be one of {', '.join(budget_engine.STATUSES)}; got {status!r}.")
		filters["status"] = status

	found = frappe.db.get_all(
		DOCTYPE,
		filters=filters,
		fields=["name", "budget_name", "company", "fiscal_year", "status", "last_refreshed", "modified"],
		order_by="modified desc",
		limit=limit + 1,
	)
	truncated = len(found) > limit
	found = found[:limit]

	rows = []
	for row in found:
		line_count = len(
			frappe.db.get_all(
				LINE_ITEM, filters={"parent": row["name"], "parenttype": DOCTYPE}, pluck="name", limit=1000
			)
			or []
		)
		kpi_count = len(
			frappe.db.get_all(
				KPI_TARGET, filters={"parent": row["name"], "parenttype": DOCTYPE}, pluck="name", limit=1000
			)
			or []
		)
		rows.append({**row, "line_item_count": line_count, "kpi_target_count": kpi_count})

	data = {"budgets": rows, "count": len(rows), "truncated": truncated, "limit": limit}
	summary = f"{len(rows)} budget(s)" + (f", truncated at {limit}" if truncated else "")
	return ToolResult(data=data, summary=summary)


# ── 5. refresh_budget ─────────────────────────────────────────────────────


def refresh_budget(args: dict) -> ToolResult:
	"""Recompute actual/variance from the ledger and the KPI framework, and save
	them onto the budget. Does not itself write a Compliance Alert — see the
	module docstring."""
	_require()
	actor = kpi_tools.require_kpi_role()
	doc = _resolve(_reference(args))

	result = _refresh_budget_doc(doc)
	described = _describe(doc)

	data = {
		"actor": actor,
		"budget": described,
		"breaches": result["breaches"],
		"breach_count": result["breach_count"],
		"critical_count": result["critical_count"],
		"warning_count": result["warning_count"],
		"window": result["window"],
	}
	if result["breaches"]:
		data["alert_note"] = (
			"these breaches are saved onto the budget's own fields, not written as a Compliance "
			"Alert directly. The hourly compliance sweep (or refresh_compliance_alerts) is what "
			"turns a breaching ACTIVE budget into an alert on the calendar — exactly as an "
			"expiring certificate or a breached KPI threshold reaches it — with the same "
			"dismissal, snooze and auto-clear. A Draft or Closed budget's breaches never reach "
			"the calendar."
		)
	if doc.status != budget_engine.STATUS_ACTIVE:
		data["status_note"] = (
			f"this budget is {doc.status!r}, not Active, so the overnight sweep will not refresh "
			"it automatically and its breaches will not reach the compliance calendar even when "
			"present. This call still computed and saved the figures."
		)

	summary = (
		f"refreshed {doc.name}: {result['breach_count']} breach(es) "
		f"({result['critical_count']} critical, {result['warning_count']} warning)"
	)
	return ToolResult(
		data=data,
		summary=summary,
		docstatus_delta=f"computed fields updated on {doc.name}",
	)


def refresh_all_active_budgets() -> dict:
	"""Scheduler entry point — every Active budget's computed fields, refreshed.

	Scheduled after the KPI history sweep (see hooks.py) so every KPI target
	reads a same-night cached figure. NEVER RAISES: one broken budget must not
	stop the rest, the same guarantee every scheduled job in this app makes.
	"""
	report = {"refreshed": [], "failed": []}
	if not compat.doctype_exists(DOCTYPE):
		return report
	names = (
		frappe.db.get_all(DOCTYPE, filters={"status": budget_engine.STATUS_ACTIVE}, pluck="name", limit=1000)
		or []
	)
	for name in names:
		try:
			doc = frappe.get_doc(DOCTYPE, name)
			_refresh_budget_doc(doc)
			report["refreshed"].append(str(name))
		except Exception as exc:  # pragma: no cover - reported, never raised
			report["failed"].append({"name": str(name), "reason": f"{type(exc).__name__}: {exc}"})
	return report


# ── 6. get_budget_variance_report ─────────────────────────────────────────


def get_budget_variance_report(args: dict) -> ToolResult:
	"""The full variance breakdown for one budget, read from its stored fields.
	Never touches the ledger — refresh_budget is what fills them in."""
	_require()
	doc = _resolve(_reference(args))
	described = _describe(doc)
	breaches = budget_engine.check_budget_variances(
		{"line_items": described["line_items"], "kpi_targets": described["kpi_targets"]}
	)
	critical = [b for b in breaches if b["severity"] == budget_engine.SEVERITY_CRITICAL]
	warning = [b for b in breaches if b["severity"] == budget_engine.SEVERITY_WARNING]

	data = {
		"name": doc.name,
		"budget_name": doc.budget_name,
		"company": doc.company,
		"fiscal_year": doc.fiscal_year,
		"status": doc.status,
		"last_refreshed": described["last_refreshed"],
		"line_items": described["line_items"],
		"kpi_targets": described["kpi_targets"],
		"breaches": breaches,
		"breach_count": len(breaches),
		"critical_count": len(critical),
		"warning_count": len(warning),
	}
	if not described["last_refreshed"]:
		data["note"] = (
			"this budget has never been refreshed, so every actual and variance figure above is "
			"a placeholder rather than a figure. refresh_budget fills them in from the ledger and "
			"the KPI framework."
		)
	summary = (
		f"{doc.budget_name}: {len(breaches)} breach(es) ({len(critical)} critical, {len(warning)} warning)"
	)
	return ToolResult(data=data, summary=summary)


# ── 7. close_budget ────────────────────────────────────────────────────────


def close_budget(args: dict) -> ToolResult:
	"""Set status=Closed. A closed budget keeps every figure it last computed
	and stops being refreshed or alerted on."""
	_require()
	actor = kpi_tools.require_kpi_role()
	doc = _resolve(_reference(args))
	if doc.status == budget_engine.STATUS_CLOSED:
		raise ToolError(f"{doc.name} is already Closed. Nothing was changed.")

	previous_status = doc.status
	doc.status = budget_engine.STATUS_CLOSED
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	return ToolResult(
		data={"actor": actor, "name": doc.name, "status": doc.status, "previous_status": previous_status},
		summary=f"closed budget {doc.name} (was {previous_status})",
		docstatus_delta=f"status: {previous_status} → Closed on {doc.name}",
	)
