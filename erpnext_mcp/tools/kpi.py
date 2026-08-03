# SPDX-License-Identifier: MIT
"""The six tools behind Sustainable CF/Acre: propose, approve, refuse, classify, read.

v0.19.5. FIVE OF THE SIX EXIST TO KEEP AN AI ON THE PROPOSING SIDE OF A LINE.

An AI is genuinely good at the first half of a normalization. The items are
scattered — an insurance recovery three months after the hailstorm, a settlement
booked to a miscellaneous income account, a working-capital swing that is really
a customer paying two days either side of a quarter end — and nobody reads a
ledger line by line looking for them. Finding them is worth a great deal.

Deciding is a different act. Whether a hailstorm in a region that hails every
third year is non-recurring is a judgement with a lender on the other end of it,
and it will be argued across a table by a person who has to defend it. So
`create_normalization_adjustment` produces a DRAFT and can produce nothing else;
`approve_normalization_adjustment` is a separate tool, with a separate switch,
that takes a signature and writes a timestamp it will not take as input. It is
the same split `record_training` makes between filing the record and signing the
supervisor's review, and it is this app's standing position: AI proposes, human
approves.

────────────────────────────────────────────────────────────────────────────
THE ROLE GATE IS NOT THE HR ONE
────────────────────────────────────────────────────────────────────────────

`employee.require_hr_role` gates the personnel register on HR Manager, HR User
and Farm Manager, and none of those is who signs off a normalization. This gate
is Accounts Manager, Farm Manager and System Manager: the accountant who has to
defend the add-back, the manager who knows whether the pump was a replacement,
and the operator. An HR User who can file a training record has no business
moving the number a lender reads, and the two lists are separate so that stays
true when either one is widened.

Company scope is the shared one — Frappe's rule, where a principal with no
Company User Permission is unrestricted, and a scoped principal is held to it.
Every tool here checks it, because a normalization on the wrong entity flatters
one set of books with another entity's insurance claim.

────────────────────────────────────────────────────────────────────────────
`backfill_asset_capex_type` IS DRY BY DEFAULT AND SAYS WHAT IT WOULD DO
────────────────────────────────────────────────────────────────────────────

Classifying the history is a real problem — an operation adopting this in year
nine has nine years of assets with no `capex_type` — and the honest heuristic is
narrow: everything bought before somebody started tracking is generally
maintenance, because it is the existing productive plant carrying on. The tool
applies exactly that heuristic, only to rows with NO classification, and never
overwrites one somebody made. It is idempotent by construction: a second run
finds nothing to do.

IT IS A STARTING POSITION, NOT AN ANSWER. The report says so, and the new block
planted in year six was growth and will be wrong until somebody fixes it in the
Desk. That is a better place to start from than nine years of nulls, and worse
than nine years of real classifications — both of which are true and both of
which the result says.
"""

from __future__ import annotations

import frappe

from .. import compat, roles, security
from .. import kpi as kpi_module
from ..args import as_bool, as_choice, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from ..services import sustainable_cf_per_acre as service
from . import employee as employee_tool
from . import shifts as shift_tools

DOCTYPE = kpi_module.DOCTYPE

RECORD_CAP = 500

#: Most Assets one backfill call will classify. Past this the caller is
#: reclassifying a database rather than a farm's history, and should be doing it
#: in batches whose report they can actually read.
BACKFILL_CAP = 5000

#: Who may propose, approve or refuse a normalization, and who may reclassify
#: capex. NOT the HR list — see the module docstring. `Administrator` holds every
#: role Frappe has, so the default configuration passes; an operator who pointed
#: `mcp_system_user` at a purpose-built account gets a refusal naming the account
#: and these three, because "permission denied" on a principal the operator chose
#: is a one-line fix they cannot make without knowing which line.
KPI_ROLES = ("System Manager", "Accounts Manager", "Farm Manager")


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def require_kpi_role() -> str:
	"""The principal this call is attributed to, once it has proved it may sign off money.

	Same shape and same identity resolution as `employee.require_hr_role` —
	whichever of the request's authenticated caller and the session user is
	present — against a different list. See the module docstring on why the two
	lists are separate rather than one widened.
	"""
	actor = security.caller_identity() or str(getattr(frappe.session, "user", "") or "")
	if not actor or actor == "Guest":
		raise ToolError(
			"this call has no identity to attribute a normalization to. A normalization is a "
			"judgement somebody signs, and an unattributable one is the thing this doctype "
			"exists to prevent. Nothing was changed."
		)
	held = set(frappe.get_roles(actor) or []) or set(roles.all_roles_of(actor) or [])
	if not held & set(KPI_ROLES):
		raise ToolError(
			f"{actor} may not touch the normalization register or the capex classification: it "
			f"holds none of {', '.join(KPI_ROLES)}. These adjustments move the cash flow figure "
			"a lender reads, so the gate is the accountant, the farm manager and the operator "
			"rather than the wider personnel list. This is the account this app acts as — an "
			"operator sets it with `mcp_system_user` on ERPNext MCP Settings, and grants it a "
			"role in the Desk. Nothing was changed."
		)
	return actor


def _resolve_adjustment(args: dict) -> dict:
	name = (
		as_str(args, "name")
		or as_str(args, "normalization_adjustment")
		or as_str(args, "adjustment", required=True)
	).strip()
	if not frappe.db.exists(DOCTYPE, name):
		raise ToolError(
			f"no {DOCTYPE} called {name!r} on this site. list_normalization_adjustments has the "
			"register; a docname looks like NADJ-2026-0001. Nothing was changed."
		)
	return dict(
		frappe.db.get_value(DOCTYPE, name, compat.existing_fields(DOCTYPE, kpi_module.FIELDS), as_dict=True)
		or {}
	)


def _scoped_companies(actor: str, args: dict) -> dict:
	"""Company filter for a read: the one asked for, or every one this actor may see."""
	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	else:
		allowed = shift_tools._readable_companies(actor)
		if allowed:
			filters["company"] = ("in", allowed)
	return {"company": company, "filters": filters}


# ── 1. create_normalization_adjustment ──────────────────────────────────────
def create_normalization_adjustment(args: dict) -> ToolResult:
	"""Propose one add-back or subtraction on operating cash flow. Draft, always."""
	_require()
	actor = require_kpi_role()

	company = resolve_company(as_str(args, "company"), required=True)
	employee_tool.require_company_scope(actor, company)

	fiscal_year = as_str(args, "fiscal_year", required=True)
	if not frappe.db.exists("Fiscal Year", fiscal_year):
		known = frappe.db.get_all("Fiscal Year", pluck="name", limit=25)
		raise ToolError(
			f"no Fiscal Year named {fiscal_year!r} on this site. This site has: "
			f"{', '.join(sorted(str(name) for name in known)) or '<none>'}. A normalization is "
			"defended inside a closed set of books, so it has to name the year it belongs to. "
			"Nothing was created."
		)

	period_start = as_date(args, "period_start", required=True)
	period_end = as_date(args, "period_end", required=True)
	if period_end < period_start:
		raise ToolError(
			f"period_end ({period_end}) is before period_start ({period_start}). Nothing was created."
		)

	amount = args.get("amount")
	try:
		amount = float(amount)
	except (TypeError, ValueError):
		raise ToolError("amount is required and must be a number. Nothing was created.") from None
	if amount <= 0:
		raise ToolError(
			"amount must be POSITIVE. The direction of the adjustment is the `direction` "
			"argument — 'Add-back to OCF' or 'Subtract from OCF' — and keeping the sign out of "
			"the amount is deliberate: a negative amount beside a Subtract is a double negative, "
			"and a double negative is how an adjustment ends up moving the number the wrong way "
			"in a pack somebody is borrowing against. Nothing was created."
		)

	direction = as_choice(DOCTYPE, "direction", as_str(args, "direction", required=True), "direction")
	category = as_choice(DOCTYPE, "category", as_str(args, "category", required=True), "category")

	justification = as_str(args, "justification", required=True)
	if len(justification.strip()) < kpi_module.MIN_JUSTIFICATION:
		raise ToolError(
			f"the justification is {len(justification.strip())} character(s) and has to be at "
			f"least {kpi_module.MIN_JUSTIFICATION}. THIS IS NOT A LENGTH REQUIREMENT DRESSED UP "
			"AS A QUALITY ONE — no character count is a quality bar. It is a floor under "
			"'one-time' and 'per Tim', which are what gets written when the field is merely "
			"required, and both of which an auditor reads as an admission that nobody thought "
			"about it. What the sentence has to answer is the question every buyer asks: WHY "
			"WILL THIS NOT HAPPEN AGAIN? A hailstorm in a region that hails every third year is "
			"not non-recurring, and this is where that gets settled before the number reaches a "
			"lender. Nothing was created."
		)

	supporting = shift_tools.file_reference(
		as_str(args, "supporting_document_file_token") or as_str(args, "supporting_document"),
		"supporting_document_file_token",
	)

	doc = frappe.new_doc(DOCTYPE)
	doc.company = company
	doc.fiscal_year = fiscal_year
	doc.period_start = period_start
	doc.period_end = period_end
	doc.amount = amount
	doc.direction = direction
	doc.category = category
	doc.justification = justification
	doc.supporting_document = supporting
	doc.status = kpi_module.STATUS_DRAFT
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = kpi_module.describe(dict(doc.as_dict()))
	competing = [
		row["name"]
		for row in kpi_module.rows(
			{
				"company": company,
				"period_start": period_start,
				"period_end": period_end,
				"category": category,
				"status": kpi_module.STATUS_APPROVED,
				"name": ("!=", doc.name),
			},
			limit=5,
		)
	]

	data = {
		**described,
		"actor": actor,
		"note": (
			f"{doc.name} is a DRAFT and does NOT count towards Sustainable CF/Acre. Only an "
			"Approved adjustment does. That split is the whole compliance posture of this "
			"doctype: finding a non-recurring item in a ledger nobody reads line by line is "
			"worth a great deal and is something an AI is good at; deciding that it will not "
			"recur is a judgement with a lender on the other end of it. "
			"approve_normalization_adjustment takes the signature."
		),
		"next_step": (
			f"approve_normalization_adjustment(name={doc.name!r}, approver_signature_file_token=…) "
			f"to accept the justification, or reject_normalization_adjustment(name={doc.name!r}, "
			"rejection_reason=…) to refuse it — a rejection with a reason is worth keeping and "
			"teaches the next proposal."
		),
	}
	if competing:
		data["duplicate_warning"] = (
			f"{competing[0]} is ALREADY an approved {category} adjustment for {company} covering "
			f"{period_start} to {period_end}. This draft can be created, and approving it will "
			f"be refused: two approved adjustments for one company, period and category are two "
			"answers to one question. If this one corrects the other, approve this and then set "
			f"{competing[0]}'s Superseded By to this record with status Superseded — that leaves "
			"the trail of what was believed before, which is the half of a restatement anybody "
			"actually wants."
		)
	if not supporting:
		data["evidence_note"] = (
			"No supporting document is attached. Not required — the paper does not always exist "
			"on the day — and it is the single most persuasive thing in the record when it does. "
			"The insurance claim determination, the settlement agreement, the board minute."
		)
	return ToolResult(
		data=data,
		summary=(
			f"drafted {doc.name}: {direction} of {amount} for {company}, {category}, "
			f"{period_start} to {period_end} (NOT counted until approved)"
		),
		docstatus_delta="none → draft (status Draft)",
	)


# ── 2. list_normalization_adjustments ───────────────────────────────────────
def list_normalization_adjustments(args: dict) -> ToolResult:
	"""The normalization register: what has been proposed, approved and refused."""
	_require()
	actor = require_kpi_role()
	limit = min(as_limit(args), RECORD_CAP)

	scope = _scoped_companies(actor, args)
	filters = scope["filters"]

	fiscal_year = as_str(args, "fiscal_year")
	if fiscal_year:
		filters["fiscal_year"] = fiscal_year
	status = as_str(args, "status")
	if status:
		filters["status"] = as_choice(DOCTYPE, "status", status, "status")

	found = kpi_module.rows(filters, limit=max(limit * 2, limit))
	described = [kpi_module.describe(row) for row in found]
	truncated = len(described) > limit
	described = described[:limit]

	approved = [entry for entry in described if entry["status"] == kpi_module.STATUS_APPROVED]
	unsigned = [entry["name"] for entry in approved if not entry["has_approver_signature"]]
	awaiting = [
		entry["name"]
		for entry in described
		if entry["status"] in (kpi_module.STATUS_DRAFT, kpi_module.STATUS_PENDING)
	]

	data = {
		"company": scope["company"],
		"count": len(described),
		"limit": limit,
		"truncated": truncated,
		"records": described,
		"counted_in_the_kpi": [entry["name"] for entry in approved],
		"awaiting_a_decision": awaiting,
		"totals_of_approved": kpi_module.signed_total(approved),
		"note": (
			f"{len(approved)} of {len(described)} adjustment(s) here are Approved and therefore "
			"count towards Sustainable CF/Acre. Drafts, pending proposals, rejections and "
			"superseded rows are all in the register and none of them moves the number."
		),
	}
	if awaiting:
		data["awaiting_note"] = (
			f"{len(awaiting)} adjustment(s) are waiting on somebody. A proposal nobody has "
			"decided is not a neutral state at quarter end — it is a figure that will change "
			"after the pack goes out."
		)
	if unsigned:  # pragma: no cover - the doctype refuses this on save
		data["signature_note"] = (
			f"{len(unsigned)} approved adjustment(s) carry no approver signature: "
			+ ", ".join(unsigned)
			+ ". These predate the signature rule or were written directly to the database."
		)
	if truncated:
		data["truncation_note"] = (
			f"More than {limit} record(s) matched and this is the first {limit}. Narrow by "
			"company, fiscal year or status before relying on the totals above."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} normalization adjustment(s)"
			+ (f" for {scope['company']}" if scope["company"] else "")
			+ f"; {len(approved)} approved, {len(awaiting)} awaiting a decision"
		),
	)


# ── 3. approve_normalization_adjustment ─────────────────────────────────────
def approve_normalization_adjustment(args: dict) -> ToolResult:
	"""Accept the justification, with a signature and a timestamp nobody typed."""
	_require()
	actor = require_kpi_role()
	row = _resolve_adjustment(args)
	company = str(row.get("company") or "")
	employee_tool.require_company_scope(actor, company)

	if row.get("status") == kpi_module.STATUS_APPROVED:
		raise ToolError(
			f"{row['name']} is already Approved (on {row.get('approved_on')}). Approving twice "
			"would rewrite the timestamp on a decision somebody already made. Nothing was changed."
		)
	if row.get("status") == kpi_module.STATUS_SUPERSEDED:
		raise ToolError(
			f"{row['name']} has been superseded by {row.get('superseded_by')} and is the record "
			"of what was believed before. Approve the correction instead. Nothing was changed."
		)

	signature = shift_tools.file_reference(
		as_str(args, "approver_signature_file_token") or as_str(args, "approver_signature"),
		"approver_signature_file_token",
	)
	if not signature:
		raise ToolError(
			"approver_signature_file_token is required. THE SIGNATURE IS WHAT APPROVAL MEANS — "
			"the entire argument for this doctype is that a normalization is a judgement with "
			"somebody's name against it, and a status set without one is the status without the "
			"name. Every buyer and lender who reads the resulting figure will test the "
			"adjustments one at a time, and an unsigned add-back is the one they stop at. Upload "
			"the signature with stage_file_chunk and commit_staged_file, then pass its File "
			"docname. Nothing was changed."
		)

	approver = as_str(args, "approver_employee")
	employee = employee_tool.resolve_employee(approver) if approver else _employee_of(actor)

	duplicate = kpi_module.rows(
		{
			"company": company,
			"period_start": row.get("period_start"),
			"period_end": row.get("period_end"),
			"category": row.get("category"),
			"status": kpi_module.STATUS_APPROVED,
			"name": ("!=", row["name"]),
		},
		limit=2,
	)
	if duplicate:
		raise ToolError(
			f"{duplicate[0]['name']} is already an approved {row.get('category')} adjustment for "
			f"{company} covering {row.get('period_start')} to {row.get('period_end')}. TWO "
			"APPROVED ADJUSTMENTS FOR ONE COMPANY, PERIOD AND CATEGORY ARE TWO ANSWERS TO ONE "
			"QUESTION, and the one a reader finds will be whichever sorted first. If this one "
			f"corrects {duplicate[0]['name']}, supersede it: set its Superseded By to "
			f"{row['name']} and its status to {kpi_module.STATUS_SUPERSEDED}, which leaves the "
			"trail of what was believed before. Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	doc.status = kpi_module.STATUS_APPROVED
	doc.approver_signature = signature
	doc.approved_by = employee or None
	# Never taken as input. An approval date somebody can set is an approval date
	# somebody can set to before the quarter closed, which is exactly the thing the
	# timestamp exists to prove.
	doc.approved_on = frappe.utils.now()
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = kpi_module.describe(dict(doc.as_dict()))
	return ToolResult(
		data={
			**described,
			"actor": actor,
			"note": (
				f"{doc.name} now counts towards Sustainable CF/Acre for {company} over "
				f"{described['period_start']} to {described['period_end']}: "
				f"{described['signed_effect_on_ocf']} on operating cash flow. The justification "
				"and the signature travel with it into every computation — "
				"get_sustainable_cf_per_acre itemizes both, because a figure whose adjustments "
				"cannot be inspected is indistinguishable from one somebody arranged."
			),
			"justification_on_the_record": described["justification"],
		},
		summary=(
			f"approved {doc.name} — {described['direction']} of {described['amount']} for "
			f"{company}, signed by {employee or actor}"
		),
		docstatus_delta=f"{row.get('status') or kpi_module.STATUS_DRAFT} → {kpi_module.STATUS_APPROVED}",
	)


def _employee_of(actor: str) -> str:
	"""The Employee record linked to the acting user, or "" where there is none.

	Empty is a working answer rather than a failure. On the ordinary MCP path the
	actor is the MCP System User, which is a service principal and has no Employee
	row by design — and refusing an approval because the app's own account is not
	on the payroll would make the tool unusable in exactly its normal
	configuration. The signature is the identity that matters; `approved_by` is
	the convenience, and a caller who wants it filled passes `approver_employee`.
	"""
	if not compat.doctype_exists("Employee") or not compat.has_field("Employee", "user_id"):
		return ""
	try:
		return str(frappe.db.get_value("Employee", {"user_id": actor}, "name") or "")
	except Exception:
		return ""


# ── 4. reject_normalization_adjustment ──────────────────────────────────────
def reject_normalization_adjustment(args: dict) -> ToolResult:
	"""Refuse the justification, on the record, with the reason attached."""
	_require()
	actor = require_kpi_role()
	row = _resolve_adjustment(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	reason = as_str(args, "rejection_reason", required=True)
	if not reason.strip():
		raise ToolError("rejection_reason is required. Nothing was changed.")

	if row.get("status") == kpi_module.STATUS_APPROVED:
		raise ToolError(
			f"{row['name']} is Approved and has been counted. Rejecting it now would rewrite a "
			"decision rather than record one. Supersede it instead: create the corrected "
			"adjustment, approve that, and set this one's Superseded By to it with status "
			f"{kpi_module.STATUS_SUPERSEDED} — which leaves the trail of what was believed "
			"before. Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	doc.status = kpi_module.STATUS_REJECTED
	doc.rejection_reason = reason
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = kpi_module.describe(dict(doc.as_dict()))
	return ToolResult(
		data={
			**described,
			"actor": actor,
			"note": (
				f"{doc.name} is Rejected and does not count towards Sustainable CF/Acre. It is "
				"KEPT rather than deleted, for the same reason a rejected insurance claim is "
				"kept: a refusal with a reason teaches the next proposal, and a register with "
				"only its successes in it tells a reader nothing about how hard the successes "
				"were to get."
			),
		},
		summary=f"rejected {doc.name} — {reason[:80]}",
		docstatus_delta=f"{row.get('status') or kpi_module.STATUS_DRAFT} → {kpi_module.STATUS_REJECTED}",
	)


# ── 5. backfill_asset_capex_type ────────────────────────────────────────────
def backfill_asset_capex_type(args: dict) -> ToolResult:
	"""Classify unclassified Assets in bulk. Dry by default, idempotent, never overwrites."""
	_require()
	actor = require_kpi_role()

	if not compat.doctype_exists(kpi_module.ASSET_DOCTYPE):
		raise ToolError(
			"this site has no Asset doctype, so there is no fixed-asset register to classify. "
			"Nothing was changed."
		)
	if not kpi_module.capex_installed():
		raise ToolError(
			f"this site's Asset has no {kpi_module.CAPEX_TYPE_FIELD} column yet. It arrives with "
			"the `bench migrate` that installs v0.19.5 — run that first, then this. Nothing was "
			"changed."
		)

	default = as_str(args, "default_capex_type") or kpi_module.CAPEX_MAINTENANCE
	capex_type = next((option for option in kpi_module.CAPEX_TYPES if option.lower() == default.lower()), "")
	if not capex_type:
		raise ToolError(
			f"default_capex_type must be one of: {', '.join(kpi_module.CAPEX_TYPES)}. Got "
			f"{default!r}. Nothing was changed."
		)
	if capex_type == kpi_module.CAPEX_MIXED:
		raise ToolError(
			"Mixed cannot be a bulk default: the split between maintenance and growth is a "
			"per-purchase judgement about one invoice, and applying one to a hundred assets "
			"would be inventing a hundred splits. Backfill as Maintenance or Growth and correct "
			"the mixed purchases individually. Nothing was changed."
		)

	cutoff = as_date(args, "cutoff_purchase_date")
	dry_run = as_bool(args, "dry_run", True)

	company = resolve_company(as_str(args, "company"), required=False)
	filters: dict = {kpi_module.CAPEX_TYPE_FIELD: ("in", (None, ""))}
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	else:
		allowed = shift_tools._readable_companies(actor)
		if allowed:
			filters["company"] = ("in", allowed)
	if cutoff:
		filters["purchase_date"] = ("<", cutoff)

	rows = frappe.db.get_all(
		kpi_module.ASSET_DOCTYPE,
		filters=filters,
		fields=compat.existing_fields(
			kpi_module.ASSET_DOCTYPE,
			("name", "asset_name", "company", "purchase_date", "gross_purchase_amount"),
		),
		order_by="purchase_date asc, name asc",
		limit=BACKFILL_CAP,
	)

	classified, failed = [], []
	total = 0.0
	for row in rows or []:
		gross = round(float(row.get("gross_purchase_amount") or 0), 2)
		split = kpi_module.split_for(capex_type, gross, None, None)
		entry = {
			"asset": row.get("name"),
			"asset_name": row.get("asset_name"),
			"company": row.get("company"),
			"purchase_date": str(row.get("purchase_date") or "") or None,
			"gross_purchase_amount": gross,
			"capex_type": capex_type,
			"maintenance_portion": split["maintenance"],
			"growth_portion": split["growth"],
		}
		if not dry_run:
			try:
				_write_capex(row.get("name"), capex_type, split)
			except Exception as exc:
				failed.append({"asset": row.get("name"), "reason": f"{type(exc).__name__}: {exc}"})
				continue
		classified.append(entry)
		if capex_type == kpi_module.CAPEX_MAINTENANCE:
			total += split["maintenance"]

	data = {
		"actor": actor,
		"dry_run": bool(dry_run),
		"default_capex_type": capex_type,
		"cutoff_purchase_date": cutoff,
		"company": company,
		"considered": len(rows or []),
		"classified": len(classified),
		"failed": failed,
		"maintenance_capex_added": round(total, 2),
		"assets": classified[:200],
		"truncated": len(classified) > 200,
		"heuristic": (
			"Everything bought before the operation started tracking is generally MAINTENANCE: "
			"it is the existing productive plant carrying on. That is the whole of the rule, it "
			"is applied only to assets with NO classification, and it never overwrites one "
			"somebody made — so a second run finds nothing to do."
		),
		"note": (
			"A STARTING POSITION, NOT AN ANSWER. The new block planted in year six was growth "
			"and is now recorded as maintenance, which understates Sustainable CF/Acre until "
			"somebody fixes it on the Asset in the Desk. That is a better place to start from "
			"than a register of nulls — where every purchase is excluded from the KPI and the "
			"figure is overstated instead — and it is worse than real classifications. Both are "
			"true; the first is why this tool exists and the second is why it says so."
		),
	}
	if dry_run:
		data["dry_run_note"] = (
			f"NOTHING WAS WRITTEN. {len(classified)} asset(s) would be classified as "
			f"{capex_type}. Re-run with dry_run=false to apply."
		)
	if len(rows or []) >= BACKFILL_CAP:
		data["cap_note"] = (
			f"{BACKFILL_CAP} is the most this call classifies and that many matched, so there "
			"are probably more. Run again — it is idempotent — or narrow with company and "
			"cutoff_purchase_date."
		)
	if not rows:
		data["empty_note"] = (
			"No unclassified assets matched. Either everything is already classified — which is "
			"the goal — or the filters are narrower than the register."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{'would classify' if dry_run else 'classified'} {len(classified)} asset(s) as "
			f"{capex_type}"
			+ (f" purchased before {cutoff}" if cutoff else "")
			+ (f"; {len(failed)} failed" if failed else "")
		),
		docstatus_delta="none" if dry_run else f"{len(classified)} Asset row(s) classified",
	)


def _write_capex(asset: str, capex_type: str, split: dict) -> None:
	"""Set the four columns on one Asset, without running ERPNext's own validation.

	`db_set` rather than `save`, and the reason is the same one `create_asset`
	makes about `reqd`: a submitted Asset cannot be re-saved through the ordinary
	path, and most of the register this tool exists to classify is submitted.
	Classifying a purchase is metadata about a decision already made — it changes
	no amount, no account, no schedule and no docstatus — so writing the columns
	directly is exactly proportionate, and going through `save` would refuse the
	rows that most need it.
	"""
	values = {
		kpi_module.CAPEX_TYPE_FIELD: capex_type,
		kpi_module.MAINTENANCE_PORTION_FIELD: split["maintenance"],
		kpi_module.GROWTH_PORTION_FIELD: split["growth"],
	}
	for fieldname, value in values.items():
		if compat.has_field(kpi_module.ASSET_DOCTYPE, fieldname):
			frappe.db.set_value(kpi_module.ASSET_DOCTYPE, asset, fieldname, value)


# ── 6. get_sustainable_cf_per_acre ──────────────────────────────────────────
def get_sustainable_cf_per_acre(args: dict) -> ToolResult:
	"""The KPI for one company over one period, with every ingredient on the plate."""
	actor = require_kpi_role()
	company = resolve_company(as_str(args, "company"), required=True)
	employee_tool.require_company_scope(actor, company)

	period_start = as_date(args, "period_start", required=True)
	period_end = as_date(args, "period_end", required=True)
	if period_end < period_start:
		raise ToolError(f"period_end ({period_end}) is before period_start ({period_start}).")

	report = service.compute(company, period_start, period_end)
	per_acre = report["sustainable_cf_per_acre"]

	data = {
		**report,
		"actor": actor,
		"reading_it": (
			"Read the components before the figure. Sustainable CF/Acre is a NORMALIZED number, "
			"which means somebody has decided that money which really did move should not be "
			"read as recurring — and a normalized number nobody can inspect is indistinguishable "
			"from an arranged one. `normalization_adjustments` is every one of those decisions "
			"with its justification and the name behind it; `maintenance_capex.itemized` is what "
			"was actually spent replacing what wore out, never a percentage of revenue; "
			"`productive_acres.itemized` is which blocks were in the denominator and for how "
			"many days of the period."
		),
	}
	if per_acre is None:
		summary = (
			f"{company} {period_start}-{period_end}: no productive acres, so no per-acre figure "
			f"(normalized OCF {report['normalized_ocf']}, maintenance capex "
			f"{report['maintenance_capex']['total']})"
		)
	else:
		summary = (
			f"{company} {period_start}-{period_end}: {per_acre} per acre "
			f"(({report['normalized_ocf']} normalized OCF - "
			f"{report['maintenance_capex']['total']} maintenance capex) ÷ "
			f"{report['productive_acres']['time_weighted']} time-weighted acres), "
			f"{len(report['normalization_adjustments'])} adjustment(s), "
			f"{len(report['computation_warnings'])} warning(s)"
		)
	return ToolResult(data=data, summary=summary)
