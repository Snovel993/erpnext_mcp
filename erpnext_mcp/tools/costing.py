# SPDX-License-Identifier: MIT
"""Phase 3: what a growing crop is worth, what things should cost, and the gap.

THREE PIECES, AND THE THIRD IS THE ONLY ONE THAT IS A CONTROL.

BIOLOGICAL ASSETS answer a question ERPNext's Asset cannot hold. An Asset is a
depreciating purchase — a cost, a useful life, a schedule that writes it down. A
bearer plant is close to that (IAS 16 moved bearer plants into property, plant
and equipment in 2014, and a mature block IS depreciated) but a CONSUMABLE
biological asset is its opposite: an annual planting grows in value until it is
harvested and then ceases to exist. Modelling that as an Asset with a negative
depreciation rate would be a lie every report repeated.

STANDARD COSTS are dated rows rather than a value on an item, and the date is
the whole design. Costs move every season, and a variance computed against the
wrong season's standard is not a missing number — it is a confident wrong one.
`select_effective` picks the row that covered the date being asked about, which
is the same shape `Piecework Rate` and `Position Wage Default` already use here.

THE VARIANCE IS THE CONTROL. A standard nobody compares against is a number in a
table; the comparison is what turns "we know what a bin should cost" into "we
know what a bin cost, and why the two differ". `get_cost_variance_report` reads
the actual side out of the ledger where a standard names a cost center or an
account, and SAYS SO WHERE IT CANNOT — because a variance against an actual of
zero would report every standard on the site as massively under budget, which is
the most confidently wrong output this module could produce.

ABSORPTION COSTING IS A TOGGLE ON THE READ, NOT A SECOND SET OF BOOKS. See
`get_absorption_cost_report`: the ledger holds one set of numbers and the
question "what did a bin cost me, including the packhouse overhead it absorbed"
is a way of reading them. An app that maintained a parallel absorbed-cost ledger
would have two numbers for one bin and no way to reconcile them.
"""

import frappe

from .. import compat, enforcement
from ..args import as_bool, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

BIO_ASSET = "Biological Asset"
BIO_VALUATION = "Biological Asset Valuation"
STANDARD = "Standard Cost"

_HINT = "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app."

RECORD_CAP = 500

ASSET_TYPES = ("Bearer", "Consumable")
VALUATION_METHODS = ("Fair Value", "Cost")
ASSET_STATUSES = ("Establishing", "Growing", "Mature", "Harvested", "Removed")
COST_BASES = ("Per Unit", "Per Acre", "Per Hour", "Per Bin", "Per Box", "Per Ton")
COST_CATEGORIES = ("Labor", "Materials", "Machinery", "Overhead", "Packing", "Storage", "Freight", "Other")

#: Default band beyond which a variance is worth somebody's attention, used when
#: a standard names none of its own. Ten percent is a convention rather than a
#: finding — which is exactly why it is overridable per standard.
DEFAULT_TOLERANCE_PCT = 10.0

_ASSET_FIELDS = (
	"name",
	"asset_name",
	"company",
	"asset_type",
	"valuation_method",
	"status",
	"parcel",
	"field",
	"current_value",
	"last_valuation_date",
	"cost_center",
	"notes",
	"modified",
	"owner",
)

_STANDARD_FIELDS = (
	"name",
	"subject",
	"company",
	"cost_basis",
	"standard_rate",
	"currency",
	"effective_from",
	"effective_to",
	"cost_category",
	"cost_center",
	"expense_account",
	"variance_tolerance_pct",
	"notes",
	"modified",
	"owner",
)


def _get(row, key, default=None):
	if isinstance(row, dict):
		return row.get(key, default)
	return getattr(row, key, default)


def _require_assets() -> None:
	compat.require_doctype(BIO_ASSET, _HINT)


def _require_standards() -> None:
	compat.require_doctype(STANDARD, _HINT)


# ── biological assets ───────────────────────────────────────────────────────
def _asset_doc(reference: str, company: str = ""):
	reference = (reference or "").strip()
	if not reference:
		raise ToolError("asset is required — its docname or its asset_name.")
	if frappe.db.exists(BIO_ASSET, reference):
		return frappe.get_doc(BIO_ASSET, reference)
	filters = {"asset_name": reference}
	if company:
		filters["company"] = company
	matches = frappe.db.get_all(BIO_ASSET, filters=filters, pluck="name", limit=5)
	if len(matches) == 1:
		return frappe.get_doc(BIO_ASSET, matches[0])
	if len(matches) > 1:
		raise ToolError(
			f"{reference!r} names {len(matches)} biological assets: {', '.join(sorted(matches))}. "
			"Pass the docname, or set company to narrow it."
		)
	raise ToolError(
		f"no Biological Asset called {reference!r} on this site. list_biological_assets has the register."
	)


def _describe_asset(doc) -> dict:
	valuations = []
	for row in doc.get("valuations") or []:
		valuations.append(
			{
				"idx": int(_get(row, "idx") or 0),
				"valuation_date": str(_get(row, "valuation_date") or ""),
				"valuation_method": _get(row, "valuation_method"),
				"value": float(_get(row, "value") or 0),
				"basis": _get(row, "basis") or None,
				"recorded_by": _get(row, "recorded_by") or None,
				"notes": _get(row, "notes") or None,
			}
		)
	valuations.sort(key=lambda row: (row["valuation_date"], row["idx"]))

	out = {
		"name": doc.name,
		"asset_name": doc.asset_name,
		"company": doc.company,
		"asset_type": doc.asset_type,
		"valuation_method": doc.valuation_method,
		"status": doc.status,
		"parcel": doc.parcel or None,
		"field": doc.field or None,
		"cost_center": doc.cost_center or None,
		"current_value": float(doc.current_value) if doc.current_value is not None else None,
		"last_valuation_date": str(doc.last_valuation_date or "") or None,
		"valuations": valuations,
		"valuation_count": len(valuations),
		"notes": doc.notes or None,
	}
	if not valuations:
		out["valuation_note"] = (
			"This asset has never been valued, so `current_value` is null rather than zero. 'Not yet "
			"valued' and 'valued at nothing' are different statements about a growing crop, and this "
			"app will not collapse them. record_biological_asset_valuation is how the first one is put on."
		)
	elif len(valuations) >= 2:
		first, last = valuations[0], valuations[-1]
		out["movement"] = {
			"from": first["value"],
			"to": last["value"],
			"change": round(last["value"] - first["value"], 2),
			"from_date": first["valuation_date"],
			"to_date": last["valuation_date"],
		}
	if doc.asset_type == "Consumable":
		out["type_note"] = (
			"A CONSUMABLE biological asset IS the harvest — it grows in value until it is picked and "
			"then ceases to exist. It is never depreciated; it is remeasured. A bearer asset (an "
			"orchard block) is the other case: it bears a harvest, remains, and is depreciated once "
			"Mature."
		)
	return out


def create_biological_asset(args: dict) -> ToolResult:
	"""Register a growing crop carried at a value. MUTATING."""
	_require_assets()
	company = resolve_company(as_str(args, "company"), required=True)
	asset_name = as_str(args, "asset_name", required=True)
	if frappe.db.exists(BIO_ASSET, {"asset_name": asset_name, "company": company}):
		raise ToolError(
			f"{asset_name!r} already names a Biological Asset for {company}. "
			"update_biological_asset edits it. Nothing was created."
		)

	asset_type = as_str(args, "asset_type") or "Bearer"
	if asset_type not in ASSET_TYPES:
		raise ToolError(
			f"asset_type must be Bearer or Consumable. Got {asset_type!r}. BEARER bears a harvest and "
			"remains (an orchard block); CONSUMABLE is the harvest (an annual planting). The "
			"difference is what happens at the end, and it decides whether the asset is depreciated "
			"or remeasured. Nothing was created."
		)
	status = as_str(args, "status") or "Growing"
	if status not in ASSET_STATUSES:
		raise ToolError(f"status must be one of: {', '.join(ASSET_STATUSES)}. Got {status!r}.")

	doc = frappe.new_doc(BIO_ASSET)
	doc.asset_name = asset_name
	doc.company = company
	doc.asset_type = asset_type
	doc.status = status
	for key in ("parcel", "field", "cost_center"):
		value = as_str(args, key)
		if value:
			doc.set(key, value)
	notes = as_str(args, "notes")
	if notes:
		doc.notes = notes

	# An opening valuation is optional but usual — an asset registered with a
	# known carrying value should not need a second call to say so.
	value = args.get("current_value")
	if value is not None:
		method = as_str(args, "valuation_method") or "Cost"
		if method not in VALUATION_METHODS:
			raise ToolError(f"valuation_method must be Fair Value or Cost. Got {method!r}.")
		doc.append(
			"valuations",
			{
				"valuation_date": as_date(args, "valuation_date") or frappe.utils.today(),
				"valuation_method": method,
				"value": float(value or 0),
				"basis": as_str(args, "basis") or "Opening value at registration",
				"recorded_by": frappe.session.user,
			},
		)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = _describe_asset(doc)
	return ToolResult(
		data={"asset": described},
		summary=(
			f"registered {asset_type} biological asset {doc.name} ({company}), {status}"
			+ (f", valued at {described['current_value']}" if described["current_value"] is not None else ", not yet valued")
		),
		docstatus_delta="none → 0 (draft)",
	)


def get_biological_asset(args: dict) -> ToolResult:
	"""One growing crop with its full valuation history. Read-only."""
	_require_assets()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _asset_doc(as_str(args, "asset", required=True), company)
	described = _describe_asset(doc)
	return ToolResult(
		data=described,
		summary=(
			f"{doc.asset_name} ({doc.asset_type}, {doc.company}), {doc.status}: "
			+ (
				f"{described['current_value']} on {described['last_valuation_date']} "
				f"({doc.valuation_method}), {described['valuation_count']} valuation(s)"
				if described["current_value"] is not None
				else "never valued"
			)
		),
	)


def list_biological_assets(args: dict) -> ToolResult:
	"""The growing-crop register with each asset's carrying value. Read-only."""
	_require_assets()
	limit = min(as_limit(args), RECORD_CAP)
	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters["company"] = company
	for key in ("asset_type", "status", "valuation_method", "parcel", "field"):
		value = as_str(args, key)
		if value:
			filters[key] = value

	rows = frappe.db.get_all(
		BIO_ASSET,
		filters=filters,
		fields=compat.existing_fields(BIO_ASSET, _ASSET_FIELDS),
		order_by="asset_name asc",
		limit=limit,
	)
	out = [dict(row) for row in rows or []]
	valued = [row for row in out if row.get("current_value") is not None]
	total = round(sum(float(row.get("current_value") or 0) for row in valued), 2)
	return ToolResult(
		data={
			"assets": out,
			"asset_count": len(out),
			"valued_count": len(valued),
			"unvalued_count": len(out) - len(valued),
			"total_carrying_value": total,
			"note": (
				f"{len(out) - len(valued)} asset(s) have never been valued and are EXCLUDED from the "
				"total rather than counted as zero — a carrying value that silently included "
				"un-valued crops would understate the balance sheet and look like a measurement."
			)
			if len(valued) != len(out)
			else "",
		},
		summary=(
			f"{len(out)} biological asset(s)"
			+ (f" for {company}" if company else "")
			+ f", {total} total carrying value across {len(valued)} valued"
		),
	)


def update_biological_asset(args: dict) -> ToolResult:
	"""Change a crop's type, status, ground or cost center. MUTATING.

	DOES NOT CHANGE THE VALUE. `current_value` is derived from the newest
	valuation row and can only move by recording another one — which is the whole
	point of the doctype. A figure no valuation supports is precisely the
	unevidenced assertion this register exists to prevent.
	"""
	_require_assets()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _asset_doc(as_str(args, "asset", required=True), company)
	changed = []

	asset_type = as_str(args, "asset_type")
	if asset_type:
		if asset_type not in ASSET_TYPES:
			raise ToolError(f"asset_type must be Bearer or Consumable. Got {asset_type!r}.")
		doc.asset_type = asset_type
		changed.append("asset_type")
	status = as_str(args, "status")
	if status:
		if status not in ASSET_STATUSES:
			raise ToolError(f"status must be one of: {', '.join(ASSET_STATUSES)}. Got {status!r}.")
		doc.status = status
		changed.append("status")
	for key in ("parcel", "field", "cost_center", "notes"):
		value = as_str(args, key)
		if value:
			doc.set(key, value)
			changed.append(key)

	if args.get("current_value") is not None:
		raise ToolError(
			"current_value cannot be set here. It is DERIVED from the newest valuation row, so a "
			"figure written directly would be a carrying value no valuation supports — which is "
			"exactly the unevidenced assertion this register exists to prevent. Use "
			"record_biological_asset_valuation. Nothing was changed."
		)

	if not changed:
		raise ToolError(
			"nothing to update. Pass at least one of: asset_type, status, parcel, field, "
			"cost_center, notes. To change the value, record a valuation."
		)

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return ToolResult(
		data={"asset": _describe_asset(doc), "changed": changed},
		summary=f"updated Biological Asset {doc.name}: {', '.join(changed)}",
		docstatus_delta="0 → 0 (draft, edited)",
	)


def record_biological_asset_valuation(args: dict) -> ToolResult:
	"""Remeasure a growing crop, appending to its valuation history. MUTATING.

	IAS 41 remeasures at each reporting date, and the SEQUENCE is what an auditor
	tests — so this appends rather than overwrites, and `current_value` follows
	the newest row. A `basis` is required: a fair value with no stated basis is
	somebody's opinion formatted as a figure, and it is the first thing anybody
	asks about.
	"""
	_require_assets()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _asset_doc(as_str(args, "asset", required=True), company)

	value = args.get("value")
	if value is None:
		raise ToolError("value is required — what the asset is now carried at.")
	method = as_str(args, "valuation_method") or doc.valuation_method or "Cost"
	if method not in VALUATION_METHODS:
		raise ToolError(f"valuation_method must be Fair Value or Cost. Got {method!r}.")
	basis = as_str(args, "basis", required=True)
	valuation_date = as_date(args, "valuation_date") or frappe.utils.today()

	previous = float(doc.current_value) if doc.current_value is not None else None

	doc.append(
		"valuations",
		{
			"valuation_date": valuation_date,
			"valuation_method": method,
			"value": float(value or 0),
			"basis": basis,
			"recorded_by": frappe.session.user,
			"notes": as_str(args, "notes"),
		},
	)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = _describe_asset(doc)
	data = {"asset": described, "previous_value": previous, "recorded": {
		"valuation_date": valuation_date,
		"valuation_method": method,
		"value": float(value or 0),
		"basis": basis,
	}}
	if previous is not None:
		change = round(float(value or 0) - previous, 2)
		data["change"] = change
		data["gain_or_loss_note"] = (
			f"The carrying value moved by {change}. UNDER IAS 41.26 A CHANGE IN FAIR VALUE LESS "
			"COSTS TO SELL GOES TO PROFIT OR LOSS IN THE PERIOD IT ARISES — and this tool has NOT "
			"posted it. No journal entry was written, because which accounts a biological gain "
			"belongs in is a decision about this operation's chart, and an app that guessed would "
			"put a gain somewhere nobody looked. Use create_journal_entry when you have decided."
		)
	return ToolResult(
		data=data,
		summary=(
			f"valued {doc.name} at {float(value or 0)} on {valuation_date} ({method})"
			+ (f", from {previous}" if previous is not None else ", its first valuation")
		),
		docstatus_delta="0 → 0 (draft, valuation appended)",
	)


# ── standard costs ──────────────────────────────────────────────────────────
def select_effective(rows: list, on_date: str) -> dict:
	"""The standard that covered `on_date`, or `{}`.

	The same selection `Piecework Rate` and `Position Wage Default` use, and it is
	reimplemented here rather than imported because those read their own doctypes'
	column names. Latest `effective_from` that covers the date wins, which matters
	only on a site whose ranges overlap — the controller refuses those, so this is
	a belt to that brace rather than the rule itself.
	"""
	on_date = str(on_date or "")
	covering = []
	for row in rows or []:
		start = str(row.get("effective_from") or "")
		end = str(row.get("effective_to") or "9999-12-31")
		if start and start <= on_date <= end:
			covering.append(row)
	if not covering:
		return {}
	covering.sort(key=lambda row: str(row.get("effective_from") or ""))
	return dict(covering[-1])


def create_standard_cost(args: dict) -> ToolResult:
	"""Define what a thing should cost, for a date range. MUTATING."""
	_require_standards()
	company = resolve_company(as_str(args, "company"), required=True)
	subject = as_str(args, "subject", required=True)
	basis = as_str(args, "cost_basis") or "Per Unit"
	if basis not in COST_BASES:
		raise ToolError(f"cost_basis must be one of: {', '.join(COST_BASES)}. Got {basis!r}.")
	category = as_str(args, "cost_category")
	if category and category not in COST_CATEGORIES:
		raise ToolError(f"cost_category must be one of: {', '.join(COST_CATEGORIES)}. Got {category!r}.")

	rate = args.get("standard_rate")
	if rate is None:
		raise ToolError("standard_rate is required — what this subject should cost per unit of its basis.")

	doc = frappe.new_doc(STANDARD)
	doc.subject = subject
	doc.company = company
	doc.cost_basis = basis
	doc.standard_rate = float(rate or 0)
	doc.effective_from = as_date(args, "effective_from", required=True)
	doc.effective_to = as_date(args, "effective_to")
	if category:
		doc.cost_category = category
	for key in ("currency", "cost_center", "expense_account", "notes"):
		value = as_str(args, key)
		if value:
			doc.set(key, value)
	tolerance = args.get("variance_tolerance_pct")
	doc.variance_tolerance_pct = float(tolerance) if tolerance is not None else DEFAULT_TOLERANCE_PCT

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	data = {"standard": _describe_standard(doc)}
	if not doc.cost_center and not doc.expense_account:
		data["actuals_note"] = (
			"This standard names neither a cost center nor an account, so get_cost_variance_report "
			"cannot read its actual side out of the ledger and will report it as UNMEASURABLE rather "
			"than as a variance against zero. A variance against an actual of zero would flag it as "
			"100% under budget, which is the most confidently wrong output this module could produce."
		)
	return ToolResult(
		data=data,
		summary=(
			f"created Standard Cost {doc.name}: {subject} at {doc.standard_rate} {basis} "
			f"from {doc.effective_from}" + (f" to {doc.effective_to}" if doc.effective_to else " (open-ended)")
		),
		docstatus_delta="none → 0 (draft)",
	)


def _describe_standard(doc) -> dict:
	return {
		"name": doc.name,
		"subject": doc.subject,
		"company": doc.company,
		"cost_basis": doc.cost_basis,
		"standard_rate": float(doc.standard_rate or 0),
		"currency": doc.currency or None,
		"effective_from": str(doc.effective_from or ""),
		"effective_to": str(doc.effective_to or "") or None,
		"open_ended": not doc.effective_to,
		"cost_category": doc.cost_category or None,
		"cost_center": doc.cost_center or None,
		"expense_account": doc.expense_account or None,
		"variance_tolerance_pct": float(doc.variance_tolerance_pct or DEFAULT_TOLERANCE_PCT),
		"notes": doc.notes or None,
	}


def _standard_doc(reference: str, company: str = ""):
	reference = (reference or "").strip()
	if not reference:
		raise ToolError("standard is required — its docname.")
	if frappe.db.exists(STANDARD, reference):
		return frappe.get_doc(STANDARD, reference)
	filters = {"subject": reference}
	if company:
		filters["company"] = company
	matches = frappe.db.get_all(STANDARD, filters=filters, pluck="name", limit=10)
	if len(matches) == 1:
		return frappe.get_doc(STANDARD, matches[0])
	if len(matches) > 1:
		raise ToolError(
			f"{reference!r} matches {len(matches)} standards: {', '.join(sorted(matches))}. A subject "
			"usually has several — one per season, and one per basis. Pass the docname, or use "
			"get_standard_cost with on_date to pick the one that covered a day."
		)
	raise ToolError(
		f"no Standard Cost matching {reference!r} on this site. list_standard_costs has the register."
	)


def get_standard_cost(args: dict) -> ToolResult:
	"""One standard, or the one that covered a given date. Read-only."""
	_require_standards()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	on_date = as_date(args, "on_date")
	subject = as_str(args, "subject")

	if subject and on_date:
		filters = {"subject": subject}
		if company:
			filters["company"] = company
		basis = as_str(args, "cost_basis")
		if basis:
			filters["cost_basis"] = basis
		rows = [
			dict(row)
			for row in frappe.db.get_all(
				STANDARD,
				filters=filters,
				fields=compat.existing_fields(STANDARD, _STANDARD_FIELDS),
				limit=RECORD_CAP,
			)
			or []
		]
		picked = select_effective(rows, on_date)
		if not picked:
			raise ToolError(
				f"no Standard Cost for {subject!r} covered {on_date}. {len(rows)} standard(s) exist "
				"for that subject with other date ranges — a variance computed against the wrong "
				"season's standard is a confident wrong number, so this refuses rather than picking "
				"the nearest."
			)
		picked["selected_for_date"] = on_date
		picked["candidates_considered"] = len(rows)
		return ToolResult(
			data=picked,
			summary=(
				f"{subject} at {picked['standard_rate']} {picked['cost_basis']} on {on_date} "
				f"(standard {picked['name']})"
			),
		)

	doc = _standard_doc(as_str(args, "standard") or subject, company)
	described = _describe_standard(doc)
	return ToolResult(
		data=described,
		summary=(
			f"{doc.subject} at {described['standard_rate']} {doc.cost_basis} "
			f"from {described['effective_from']}"
			+ (f" to {described['effective_to']}" if described["effective_to"] else " (open-ended)")
		),
	)


def list_standard_costs(args: dict) -> ToolResult:
	"""The standards register. Read-only."""
	_require_standards()
	limit = min(as_limit(args), RECORD_CAP)
	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters["company"] = company
	for key in ("subject", "cost_basis", "cost_category", "cost_center"):
		value = as_str(args, key)
		if value:
			filters[key] = value

	rows = [
		dict(row)
		for row in frappe.db.get_all(
			STANDARD,
			filters=filters,
			fields=compat.existing_fields(STANDARD, _STANDARD_FIELDS),
			order_by="subject asc, effective_from desc",
			limit=limit,
		)
		or []
	]
	on_date = as_date(args, "on_date")
	if on_date:
		by_subject: dict = {}
		for row in rows:
			by_subject.setdefault((row["subject"], row.get("cost_basis")), []).append(row)
		rows = [picked for picked in (select_effective(group, on_date) for group in by_subject.values()) if picked]

	unmeasurable = [row["name"] for row in rows if not row.get("cost_center") and not row.get("expense_account")]
	return ToolResult(
		data={
			"standards": rows,
			"standard_count": len(rows),
			"effective_on": on_date,
			"unmeasurable": unmeasurable,
			"note": (
				f"{len(unmeasurable)} standard(s) name neither a cost center nor an account, so their "
				"actual side cannot be read out of the ledger. get_cost_variance_report reports those "
				"as unmeasurable rather than as a variance against zero."
			)
			if unmeasurable
			else "",
		},
		summary=(
			f"{len(rows)} standard cost(s)"
			+ (f" effective on {on_date}" if on_date else "")
			+ (f" for {company}" if company else "")
		),
	)


def update_standard_cost(args: dict) -> ToolResult:
	"""Change a standard's rate, range, classification or tolerance. MUTATING.

	CLOSING A STANDARD IS HOW A NEW SEASON'S IS MADE. Set `effective_to` on the
	current row, then create the next one — the controller refuses overlapping
	ranges, so there is never a day with two answers to 'what should this have
	cost'.
	"""
	_require_standards()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _standard_doc(as_str(args, "standard", required=True), company)
	before = _describe_standard(doc)
	changed = []

	if args.get("standard_rate") is not None:
		doc.standard_rate = float(args.get("standard_rate") or 0)
		changed.append("standard_rate")
	for key in ("effective_from", "effective_to"):
		value = as_date(args, key)
		if value:
			doc.set(key, value)
			changed.append(key)
	basis = as_str(args, "cost_basis")
	if basis:
		if basis not in COST_BASES:
			raise ToolError(f"cost_basis must be one of: {', '.join(COST_BASES)}. Got {basis!r}.")
		doc.cost_basis = basis
		changed.append("cost_basis")
	category = as_str(args, "cost_category")
	if category:
		if category not in COST_CATEGORIES:
			raise ToolError(f"cost_category must be one of: {', '.join(COST_CATEGORIES)}. Got {category!r}.")
		doc.cost_category = category
		changed.append("cost_category")
	for key in ("currency", "cost_center", "expense_account", "notes"):
		value = as_str(args, key)
		if value:
			doc.set(key, value)
			changed.append(key)
	if args.get("variance_tolerance_pct") is not None:
		doc.variance_tolerance_pct = float(args.get("variance_tolerance_pct") or 0)
		changed.append("variance_tolerance_pct")

	if not changed:
		raise ToolError(
			"nothing to update. Pass at least one of: standard_rate, effective_from, effective_to, "
			"cost_basis, cost_category, currency, cost_center, expense_account, "
			"variance_tolerance_pct, notes."
		)

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return ToolResult(
		data={"standard": _describe_standard(doc), "changed": changed, "was": before},
		summary=f"updated Standard Cost {doc.name}: {', '.join(changed)}",
		docstatus_delta="0 → 0 (draft, edited)",
	)


# ── the variance ────────────────────────────────────────────────────────────
def _actuals(standard: dict, from_date: str, to_date: str) -> dict:
	"""What actually hit the ledger for one standard's subject, in a window.

	Returns `{"measurable": bool, ...}` and the flag is the important key. A
	standard naming neither a cost center nor an account has no actual side this
	app can compute, and saying so is the only honest answer — reporting zero
	would flag it as 100% under budget, which reads exactly like a real finding.
	"""
	out = {
		"measurable": False,
		"actual_amount": None,
		"entry_count": 0,
		"basis": "",
	}
	if not compat.doctype_exists("GL Entry"):
		out["basis"] = "this site has no GL Entry doctype, so no actual can be read."
		return out

	filters = {"is_cancelled": 0, "company": standard.get("company")}
	scope = []
	if standard.get("expense_account"):
		filters["account"] = standard["expense_account"]
		scope.append(f"account {standard['expense_account']}")
	if standard.get("cost_center"):
		filters["cost_center"] = standard["cost_center"]
		scope.append(f"cost center {standard['cost_center']}")
	if not scope:
		out["basis"] = (
			"this standard names neither a cost center nor an account, so there is nothing to read "
			"the actual off. It is reported as unmeasurable rather than as a variance against zero."
		)
		return out
	if from_date and to_date:
		filters["posting_date"] = ("between", [from_date, to_date])

	rows = frappe.db.get_all(
		"GL Entry", filters=filters, fields=["name", "debit", "credit"], limit=5000
	)
	total = round(sum(float(row.get("debit") or 0) - float(row.get("credit") or 0) for row in rows or []), 2)
	out.update(
		{
			"measurable": True,
			"actual_amount": total,
			"entry_count": len(rows or []),
			"basis": f"net of debits and credits on {' and '.join(scope)}"
			+ (f" between {from_date} and {to_date}" if from_date and to_date else ""),
		}
	)
	return out


def get_cost_variance_report(args: dict) -> ToolResult:
	"""Actual against standard, per subject, with the significant gaps flagged. Read-only.

	THE COMPARISON IS THE CONTROL. A standard nobody compares against is a number
	in a table.

	TWO WAYS TO SUPPLY THE ACTUAL AND THE QUANTITY, because neither is always
	available. Pass `actuals` — `[{"subject": ..., "actual_amount": ..., "quantity": ...}]` —
	and the report uses what you give it, which is right when the actual comes from
	a packout sheet rather than the ledger. Pass nothing and it reads the ledger
	for every standard that names a cost center or an account, and reports the
	rest as UNMEASURABLE.

	A VARIANCE NEEDS A QUANTITY TO MEAN ANYTHING. A standard is a rate — per bin,
	per acre — so the expected cost is rate × quantity, and without a quantity the
	report can only give the actual and the rate, which it says rather than
	inventing a denominator.
	"""
	_require_standards()
	company = resolve_company(as_str(args, "company"), required=True)
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	on_date = as_date(args, "on_date") or to_date or frappe.utils.today()

	filters: dict = {"company": company}
	subject = as_str(args, "subject")
	if subject:
		filters["subject"] = subject
	category = as_str(args, "cost_category")
	if category:
		filters["cost_category"] = category

	rows = [
		dict(row)
		for row in frappe.db.get_all(
			STANDARD,
			filters=filters,
			fields=compat.existing_fields(STANDARD, _STANDARD_FIELDS),
			order_by="subject asc, effective_from desc",
			limit=RECORD_CAP,
		)
		or []
	]
	if not rows:
		raise ToolError(
			f"no Standard Cost matches those filters for {company}. A variance report needs a "
			"standard to compare against — create_standard_cost is where one starts."
		)

	# One standard per (subject, basis): the one that covered the reporting date.
	by_key: dict = {}
	for row in rows:
		by_key.setdefault((row["subject"], row.get("cost_basis")), []).append(row)
	selected = [picked for picked in (select_effective(group, on_date) for group in by_key.values()) if picked]

	supplied = {}
	raw = args.get("actuals")
	if raw is not None:
		if not isinstance(raw, list):
			raise ToolError(
				'actuals must be a list of objects, e.g. [{"subject": "HARVEST-GALA", '
				'"actual_amount": 91000, "quantity": 3800}]'
			)
		for index, entry in enumerate(raw, start=1):
			if not isinstance(entry, dict):
				raise ToolError(f"actuals[{index}] must be an object.")
			key = str(entry.get("subject") or "").strip()
			if not key:
				raise ToolError(f"actuals[{index}] needs a subject naming which standard it is for.")
			supplied[key] = entry

	report = []
	flagged = []
	unmeasurable = []
	for standard in selected:
		given = supplied.get(standard["subject"], {})
		quantity = given.get("quantity")
		quantity = float(quantity) if quantity is not None else None

		if given.get("actual_amount") is not None:
			actual = round(float(given["actual_amount"]), 2)
			measured = {"measurable": True, "actual_amount": actual, "entry_count": 0, "basis": "supplied by the caller"}
		else:
			measured = _actuals(standard, from_date, to_date)
			actual = measured["actual_amount"]

		rate = float(standard.get("standard_rate") or 0)
		tolerance = float(standard.get("variance_tolerance_pct") or DEFAULT_TOLERANCE_PCT)
		expected = round(rate * quantity, 2) if quantity is not None else None

		entry = {
			"standard": standard["name"],
			"subject": standard["subject"],
			"cost_basis": standard.get("cost_basis"),
			"cost_category": standard.get("cost_category"),
			"standard_rate": rate,
			"quantity": quantity,
			"expected_amount": expected,
			"actual_amount": actual,
			"measurable": measured["measurable"],
			"actual_basis": measured["basis"],
			"tolerance_pct": tolerance,
			"variance_amount": None,
			"variance_pct": None,
			"significant": False,
			"direction": None,
		}

		if not measured["measurable"]:
			unmeasurable.append(standard["name"])
		elif expected is None:
			entry["note"] = (
				"No quantity was given for this subject, so there is no expected amount to compare "
				"against — a standard is a RATE, and rate times nothing is not a budget. Pass "
				'actuals: [{"subject": ..., "quantity": ...}] to get a variance.'
			)
		elif expected == 0:
			entry["note"] = (
				"The expected amount is zero, so a percentage variance is undefined rather than "
				"infinite. The variance AMOUNT below is still real."
			)
			entry["variance_amount"] = round(actual - expected, 2)
			entry["direction"] = "over" if actual > expected else ("under" if actual < expected else "on")
		else:
			variance = round(actual - expected, 2)
			pct = round(variance / expected * 100.0, 2)
			entry["variance_amount"] = variance
			entry["variance_pct"] = pct
			entry["direction"] = "over" if variance > 0 else ("under" if variance < 0 else "on")
			if abs(pct) >= tolerance:
				entry["significant"] = True
				flagged.append(entry)

		report.append(entry)

	findings = [
		enforcement.Finding(
			control_point="cost_variance",
			message=(
				f"{entry['subject']} came in {abs(entry['variance_pct'])}% {entry['direction']} "
				f"standard: {entry['actual_amount']} actual against {entry['expected_amount']} "
				f"expected ({entry['standard_rate']} {entry['cost_basis']} x {entry['quantity']}), "
				f"past its {entry['tolerance_pct']}% tolerance."
			),
			remedy=(
				"Explain the variance on the record, or move the standard with update_standard_cost "
				f"if {entry['standard_rate']} is no longer what this should cost. A standard nobody "
				"revises becomes a variance report nobody reads."
			),
			source_doctype=STANDARD,
			source_docname=entry["standard"],
			company=company,
			detail=entry,
		)
		for entry in flagged
	]
	# Raises in Enforced mode; reports and files alerts in Advisory. Nothing has
	# been written by this tool, so the ordering constraint in `enforcement.py`
	# is satisfied trivially — this is a read.
	control = enforcement.evaluate("cost_variance", findings, company=company)

	return ToolResult(
		data={
			"company": company,
			"effective_on": on_date,
			"from_date": from_date,
			"to_date": to_date,
			"variances": report,
			"standard_count": len(report),
			"significant": [entry["subject"] for entry in flagged],
			"significant_count": len(flagged),
			"unmeasurable": unmeasurable,
			"control": control,
			"note": (
				"UNMEASURABLE IS NOT ZERO. A standard naming neither a cost center nor an account has "
				"no actual side this app can read, and it is listed rather than reported as 100% "
				"under budget — which is what a variance against an actual of zero looks like, and "
				"is indistinguishable from a real finding."
			),
		},
		summary=(
			f"{len(report)} standard(s) compared for {company} on {on_date}: "
			f"{len(flagged)} significant variance(s)"
			+ (f", {len(unmeasurable)} unmeasurable" if unmeasurable else "")
		),
	)


def get_absorption_cost_report(args: dict) -> ToolResult:
	"""Field-to-packhouse cost per unit, with overhead absorbed or excluded. Read-only.

	THE TOGGLE IS ON THE READ, NOT ON A SECOND SET OF BOOKS. `absorption=false`
	(the default) reports direct cost only — what the ledger already says. Turning
	it on adds the indirect pools to the same direct base and divides by the same
	quantity, so the two answers are two readings of ONE ledger rather than two
	ledgers. An app that maintained a parallel absorbed-cost ledger would have two
	numbers for one bin and no way to reconcile them, which is precisely the state
	absorption costing exists to avoid.

	WHAT IT WILL NOT DO IS INVENT AN ALLOCATION BASE. Absorbed cost per unit needs
	a quantity, and if none is given the tool reports the pools and says it cannot
	divide them — rather than picking a denominator, which is the step that turns
	a costing report into fiction.
	"""
	company = resolve_company(as_str(args, "company"), required=True)
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	absorption = as_bool(args, "absorption", False)
	quantity = args.get("quantity")
	quantity = float(quantity) if quantity is not None else None
	uom = as_str(args, "uom") or "unit"

	direct = _pool(company, args.get("direct_cost_centers"), args.get("direct_accounts"), from_date, to_date)
	indirect = (
		_pool(company, args.get("overhead_cost_centers"), args.get("overhead_accounts"), from_date, to_date)
		if absorption
		else {"amount": 0.0, "entry_count": 0, "scope": [], "measurable": False}
	)

	total = round(direct["amount"] + (indirect["amount"] if absorption else 0.0), 2)
	data = {
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"absorption": absorption,
		"direct_cost": direct,
		"indirect_cost": indirect if absorption else None,
		"total_cost": total,
		"quantity": quantity,
		"uom": uom,
		"cost_per_unit": None,
	}

	if not direct["measurable"]:
		data["note"] = (
			"No direct cost centers or accounts were given, so there is nothing to total. Pass "
			"direct_cost_centers and/or direct_accounts naming where field and packhouse cost lands."
		)
		return ToolResult(data=data, summary=f"absorption report for {company}: nothing to total")

	if quantity:
		data["cost_per_unit"] = round(total / quantity, 4)
	else:
		data["quantity_note"] = (
			"No quantity was given, so the pools are reported and not divided. THIS TOOL WILL NOT "
			"PICK AN ALLOCATION BASE — a denominator chosen by an algorithm is the step that turns a "
			"costing report into fiction. Pass quantity (bins packed, boxes shipped, acres) to get a "
			"cost per unit."
		)

	data["absorption_note"] = (
		(
			"ABSORPTION ON. Indirect pools are added to the same direct base and divided by the same "
			"quantity, so this figure and the direct-only figure are two readings of ONE ledger. "
			"Nothing was written and no second set of books exists."
		)
		if absorption
		else (
			"ABSORPTION OFF (the default). This is direct cost only — what the ledger already says. "
			"Pass absorption=true with overhead_cost_centers or overhead_accounts to fold indirect "
			"pools into the same base."
		)
	)
	return ToolResult(
		data=data,
		summary=(
			f"{'absorbed' if absorption else 'direct'} cost for {company}: {total}"
			+ (f" over {quantity} {uom} = {data['cost_per_unit']} per {uom}" if data["cost_per_unit"] else "")
		),
	)


def _pool(company: str, cost_centers, accounts, from_date, to_date) -> dict:
	"""One cost pool totalled off the ledger."""
	out = {"amount": 0.0, "entry_count": 0, "scope": [], "measurable": False}
	if not compat.doctype_exists("GL Entry"):
		return out
	centers = [str(value).strip() for value in (cost_centers or []) if str(value).strip()]
	account_names = [str(value).strip() for value in (accounts or []) if str(value).strip()]
	if not centers and not account_names:
		return out

	filters = {"company": company, "is_cancelled": 0}
	if centers:
		filters["cost_center"] = ("in", centers)
		out["scope"].append(f"cost centers: {', '.join(centers)}")
	if account_names:
		filters["account"] = ("in", account_names)
		out["scope"].append(f"accounts: {', '.join(account_names)}")
	if from_date and to_date:
		filters["posting_date"] = ("between", [from_date, to_date])

	rows = frappe.db.get_all("GL Entry", filters=filters, fields=["name", "debit", "credit"], limit=5000)
	out["amount"] = round(
		sum(float(row.get("debit") or 0) - float(row.get("credit") or 0) for row in rows or []), 2
	)
	out["entry_count"] = len(rows or [])
	out["measurable"] = True
	return out
