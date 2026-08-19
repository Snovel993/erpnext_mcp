# SPDX-License-Identifier: MIT
"""Phase 2: revenue contracts, recognition, and the chain from promise to cash.

WHAT ASC 606 ASKS THAT A SALES ORDER CANNOT ANSWER. Four things: what the
distinct performance obligations are, how the transaction price is allocated
between them, whether each transfers control at a point in time or over time, and
when each was actually satisfied. A Sales Order is a fulfilment document — what
to ship, when, at what price — and none of those four is a field on it.

ON A PRODUCE OPERATION THE GAP IS AT ITS WIDEST, which is why this is worth a
doctype rather than a spreadsheet. Fruit is delivered in September against a
consignment pool that settles in December at a price nobody knew in September.
Between those two dates the operation has transferred control of something and
does not yet know what it is entitled to — which is exactly ASC 606's variable
consideration, and exactly the state a Sales Order has no way to represent.

`trace_contract_to_cash` IS THE READ THIS PHASE EXISTS FOR. An auditor's question
about revenue is almost never "what is the balance" — it is "show me one sale all
the way through", and answering it usually means somebody opening five documents
in five modules and writing the links down on paper. The chain is
contract → settlement → invoice → payment → GL entries, and every hop is a link
that already exists on a record; what was missing was one read that walks them.
IT REPORTS THE BREAK RATHER THAN HIDING IT: a chain that stops at the invoice
comes back saying so, because "this revenue has not been collected" is the most
useful thing that read can tell anybody.

NOTHING HERE POSTS. `recognize_revenue_milestone` writes a DRAFT journal entry
through the same `insert_draft_journal_entry` every other tool in this app uses.
Recognising revenue is a posting somebody signs off, not a side effect of ticking
a box — and it is the single posting most likely to be wrong in a way that
matters, because it is the one that moves the top line.
"""

import frappe

from .. import compat, enforcement
from ..args import as_bool, as_date, as_limit, as_str, resolve_account, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import mutate

CONTRACT = "Revenue Contract"
OBLIGATION = "Revenue Performance Obligation"
SCHEDULE = "Revenue Recognition Schedule"
SETTLEMENT = "Settlement Statement"

_HINT = "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app."

RECORD_CAP = 500

_CONTRACT_FIELDS = (
	"name",
	"contract_name",
	"company",
	"customer",
	"contract_date",
	"status",
	"recognition_method",
	"start_date",
	"end_date",
	"total_value",
	"currency",
	"scheduled_amount",
	"recognized_amount",
	"unrecognized_amount",
	"revenue_account",
	"receivable_account",
	"notes",
	"modified",
	"owner",
)

STATUSES = ("Draft", "Active", "Completed", "Cancelled")
METHODS = ("Point in Time", "Over Time")
BASES = ("Milestone", "Time")


def _require() -> None:
	compat.require_doctype(CONTRACT, _HINT)


def _get(row, key, default=None):
	"""One field off a child row that may be a Document or a dict.

	The same two-shapes problem `receipts._lines_out` solves, and solved the same
	way: a child row is a Frappe Document on a bench and a plain dict under the
	standalone double.
	"""
	if isinstance(row, dict):
		return row.get(key, default)
	return getattr(row, key, default)


def _contract_doc(reference: str, company: str = ""):
	reference = (reference or "").strip()
	if not reference:
		raise ToolError("contract is required — its docname or its contract_name.")
	if frappe.db.exists(CONTRACT, reference):
		return frappe.get_doc(CONTRACT, reference)
	filters = {"contract_name": reference}
	if company:
		filters["company"] = company
	matches = frappe.db.get_all(CONTRACT, filters=filters, pluck="name", limit=5)
	if len(matches) == 1:
		return frappe.get_doc(CONTRACT, matches[0])
	if len(matches) > 1:
		raise ToolError(
			f"{reference!r} names {len(matches)} contracts: {', '.join(sorted(matches))}. "
			"Pass the docname, or set company to narrow it."
		)
	raise ToolError(
		f"no Revenue Contract called {reference!r} on this site. list_revenue_contracts has the register."
	)


def _describe(doc) -> dict:
	obligations = []
	for row in doc.get("obligations") or []:
		obligations.append(
			{
				"idx": int(_get(row, "idx") or 0),
				"obligation": _get(row, "obligation"),
				"allocated_amount": float(_get(row, "allocated_amount") or 0),
				"satisfied": bool(compat.checked(_get(row, "satisfied"))),
				"satisfied_on": str(_get(row, "satisfied_on") or "") or None,
				"evidence": _get(row, "evidence") or None,
				"notes": _get(row, "notes") or None,
			}
		)
	schedule = []
	for row in doc.get("schedule") or []:
		schedule.append(
			{
				"idx": int(_get(row, "idx") or 0),
				"basis": _get(row, "basis"),
				"obligation": _get(row, "obligation") or None,
				"due_date": str(_get(row, "due_date") or "") or None,
				"amount": float(_get(row, "amount") or 0),
				"recognized": bool(compat.checked(_get(row, "recognized"))),
				"recognized_on": str(_get(row, "recognized_on") or "") or None,
				"journal_entry": _get(row, "journal_entry") or None,
				"notes": _get(row, "notes") or None,
			}
		)
	allocated = round(sum(row["allocated_amount"] for row in obligations), 2)
	total = float(doc.total_value or 0)
	out = {
		"name": doc.name,
		"contract_name": doc.contract_name,
		"company": doc.company,
		"customer": doc.customer,
		"contract_date": str(doc.contract_date or "") or None,
		"status": doc.status,
		"recognition_method": doc.recognition_method,
		"start_date": str(doc.start_date or "") or None,
		"end_date": str(doc.end_date or "") or None,
		"total_value": total,
		"currency": doc.currency or None,
		"revenue_account": doc.revenue_account or None,
		"receivable_account": doc.receivable_account or None,
		"obligations": obligations,
		"obligation_count": len(obligations),
		"allocated_amount": allocated,
		"unallocated_amount": round(total - allocated, 2),
		"schedule": schedule,
		"scheduled_amount": round(sum(row["amount"] for row in schedule), 2),
		"recognized_amount": round(sum(row["amount"] for row in schedule if row["recognized"]), 2),
		"notes": doc.notes or None,
	}
	out["unrecognized_amount"] = round(total - out["recognized_amount"], 2)
	if out["unallocated_amount"] > 0.005 and obligations:
		out["allocation_note"] = (
			f"{out['unallocated_amount']} of the transaction price is not allocated to any "
			"performance obligation. That is a contract somebody is still writing rather than an "
			"error — but revenue cannot be recognised against an obligation that does not exist, "
			"so the unallocated part has nowhere to go."
		)
	return out


def _requested_obligations(raw) -> list[dict]:
	if raw is None:
		return []
	if not isinstance(raw, list):
		raise ToolError(
			'obligations must be a list of objects, e.g. [{"obligation": "Deliver 4,000 bins of '
			'Gala", "allocated_amount": 240000}]'
		)
	rows = []
	for index, entry in enumerate(raw, start=1):
		if isinstance(entry, str):
			entry = {"obligation": entry}
		if not isinstance(entry, dict):
			raise ToolError(f"obligations[{index}] must be an object or a string.")
		text = str(entry.get("obligation") or "").strip()
		if not text:
			raise ToolError(f"obligations[{index}] needs an obligation — what was promised.")
		rows.append(
			{
				"obligation": text,
				"allocated_amount": float(entry.get("allocated_amount") or 0),
				"satisfied": 1 if entry.get("satisfied") else 0,
				"satisfied_on": entry.get("satisfied_on") or None,
				"evidence": str(entry.get("evidence") or "").strip(),
				"notes": str(entry.get("notes") or "").strip(),
			}
		)
	return rows


def _requested_schedule(raw, obligations: list) -> list[dict]:
	if raw is None:
		return []
	if not isinstance(raw, list):
		raise ToolError(
			'schedule must be a list of objects, e.g. [{"basis": "Milestone", "obligation": '
			'"Deliver 4,000 bins of Gala", "amount": 240000}] or [{"basis": "Time", '
			'"due_date": "2026-10-31", "amount": 5000}]'
		)
	known = {row["obligation"] for row in obligations}
	rows = []
	for index, entry in enumerate(raw, start=1):
		if not isinstance(entry, dict):
			raise ToolError(f"schedule[{index}] must be an object.")
		basis = str(entry.get("basis") or "Milestone").strip().title()
		if basis not in BASES:
			raise ToolError(f"schedule[{index}].basis must be Milestone or Time. Got {entry.get('basis')!r}.")
		amount = float(entry.get("amount") or 0)
		if not amount:
			raise ToolError(f"schedule[{index}] needs an amount — a tranche of nothing recognises nothing.")
		obligation = str(entry.get("obligation") or "").strip()
		due_date = entry.get("due_date") or None
		if basis == "Milestone":
			if not obligation:
				raise ToolError(
					f"schedule[{index}] is a Milestone row and names no obligation, so nothing could "
					"ever make it ripe. Name the obligation it waits for, or make it a Time row with "
					"a due_date."
				)
			if known and obligation not in known:
				raise ToolError(
					f"schedule[{index}] waits for obligation {obligation!r}, which is not on this "
					f"contract. Its obligations are: {'; '.join(sorted(known))}."
				)
		elif not due_date:
			raise ToolError(
				f"schedule[{index}] is a Time row and has no due_date, so nothing could ever make it ripe."
			)
		rows.append(
			{
				"basis": basis,
				"obligation": obligation,
				"due_date": due_date,
				"amount": amount,
				"recognized": 0,
				"notes": str(entry.get("notes") or "").strip(),
			}
		)
	return rows


def create_revenue_contract(args: dict) -> ToolResult:
	"""Record what was promised, what it is worth, and when each part is earned. MUTATING."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	contract_name = as_str(args, "contract_name", required=True)
	customer = as_str(args, "customer", required=True)
	if not frappe.db.exists("Customer", customer):
		raise ToolError(
			f"no Customer called {customer!r} on this site. ASC 606 is about contracts WITH "
			"CUSTOMERS — an arrangement with no customer on it is something else. Nothing was created."
		)
	if frappe.db.exists(CONTRACT, {"contract_name": contract_name, "company": company}):
		raise ToolError(
			f"{contract_name!r} already names a Revenue Contract for {company}. "
			"update_revenue_contract edits it. Nothing was created."
		)

	status = as_str(args, "status") or "Draft"
	if status not in STATUSES:
		raise ToolError(f"status must be one of: {', '.join(STATUSES)}. Got {status!r}.")
	method = as_str(args, "recognition_method") or "Point in Time"
	if method not in METHODS:
		raise ToolError(f"recognition_method must be one of: {', '.join(METHODS)}. Got {method!r}.")

	doc = frappe.new_doc(CONTRACT)
	doc.contract_name = contract_name
	doc.company = company
	doc.customer = customer
	doc.status = status
	doc.recognition_method = method
	doc.contract_date = as_date(args, "contract_date")
	doc.start_date = as_date(args, "start_date")
	doc.end_date = as_date(args, "end_date")
	doc.total_value = float(args.get("total_value") or 0)
	currency = as_str(args, "currency")
	if currency:
		doc.currency = currency
	for field, label in (
		("revenue_account", "revenue_account"),
		("receivable_account", "receivable_account"),
	):
		value = as_str(args, label)
		if value:
			doc.set(field, resolve_account(value, company))
	notes = as_str(args, "notes")
	if notes:
		doc.notes = notes

	obligations = _requested_obligations(args.get("obligations"))
	for row in obligations:
		doc.append("obligations", row)
	for row in _requested_schedule(args.get("schedule"), obligations):
		doc.append("schedule", row)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = _describe(doc)
	return ToolResult(
		data={"contract": described, "control": enforcement.status("revenue_recognition")},
		summary=(
			f"created Revenue Contract {doc.name} for {customer} ({company}): "
			f"{described['total_value']} across {described['obligation_count']} obligation(s), "
			f"{len(described['schedule'])} scheduled tranche(s)"
		),
		docstatus_delta="none → 0 (draft)",
	)


def get_revenue_contract(args: dict) -> ToolResult:
	"""One contract in full: obligations, schedule, and what is left to recognise. Read-only."""
	_require()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _contract_doc(as_str(args, "contract", required=True), company)
	described = _describe(doc)
	described["control"] = enforcement.status("revenue_recognition")
	described["settlements"] = _settlements_for(doc.name)
	return ToolResult(
		data=described,
		summary=(
			f"{doc.contract_name} ({doc.customer}, {doc.company}), {doc.status}: "
			f"{described['recognized_amount']} recognised of {described['total_value']}, "
			f"{described['unrecognized_amount']} to go"
		),
	)


def list_revenue_contracts(args: dict) -> ToolResult:
	"""The contract register with each one's recognition progress. Read-only."""
	_require()
	limit = min(as_limit(args), RECORD_CAP)
	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters["company"] = company
	customer = as_str(args, "customer")
	if customer:
		filters["customer"] = customer
	status = as_str(args, "status")
	if status:
		filters["status"] = status
	method = as_str(args, "recognition_method")
	if method:
		filters["recognition_method"] = method

	rows = frappe.db.get_all(
		CONTRACT,
		filters=filters,
		fields=compat.existing_fields(CONTRACT, _CONTRACT_FIELDS),
		order_by="contract_date desc, contract_name asc",
		limit=limit,
	)
	out = [dict(row) for row in rows or []]
	total = round(sum(float(row.get("total_value") or 0) for row in out), 2)
	recognized = round(sum(float(row.get("recognized_amount") or 0) for row in out), 2)
	return ToolResult(
		data={
			"contracts": out,
			"contract_count": len(out),
			"total_value": total,
			"recognized_amount": recognized,
			"unrecognized_amount": round(total - recognized, 2),
			"control": enforcement.status("revenue_recognition"),
		},
		summary=(
			f"{len(out)} revenue contract(s)"
			+ (f" for {company}" if company else "")
			+ f": {recognized} recognised of {total}"
		),
	)


def update_revenue_contract(args: dict) -> ToolResult:
	"""Change a contract's price, status, accounts, obligations or schedule. MUTATING.

	THE PRICE IS THE FIELD MOST LIKELY TO MOVE, and that is not a defect in the
	original record. A consignment pool's transaction price is an ESTIMATE until
	the settlement arrives — ASC 606's variable consideration — so revising it is
	the normal life of the document rather than a correction. Both sum checks are
	re-run against the new figure, so a price revised DOWNWARDS below what is
	already scheduled is refused rather than quietly leaving a schedule that can
	be drawn past the contract.
	"""
	_require()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _contract_doc(as_str(args, "contract", required=True), company)
	before = _describe(doc)
	changed = []

	status = as_str(args, "status")
	if status:
		if status not in STATUSES:
			raise ToolError(f"status must be one of: {', '.join(STATUSES)}. Got {status!r}.")
		doc.status = status
		changed.append("status")
	method = as_str(args, "recognition_method")
	if method:
		if method not in METHODS:
			raise ToolError(f"recognition_method must be one of: {', '.join(METHODS)}. Got {method!r}.")
		doc.recognition_method = method
		changed.append("recognition_method")
	if args.get("total_value") is not None:
		doc.total_value = float(args.get("total_value") or 0)
		changed.append("total_value")
	for key in ("contract_date", "start_date", "end_date"):
		value = as_date(args, key)
		if value:
			doc.set(key, value)
			changed.append(key)
	for key in ("revenue_account", "receivable_account"):
		value = as_str(args, key)
		if value:
			doc.set(key, resolve_account(value, doc.company))
			changed.append(key)
	notes = as_str(args, "notes")
	if notes:
		doc.notes = notes
		changed.append("notes")

	if args.get("obligations") is not None:
		# SATISFACTION SURVIVES A REWRITE, matched by the obligation's own text —
		# the same rule `update_closing_checklist` applies to a completed step, and
		# for the same reason. Losing the record that control of four thousand bins
		# transferred on 12 September, because somebody reworded a different line,
		# is a data loss nobody asked for and one that would silently un-recognise
		# revenue.
		satisfied = {
			str(_get(row, "obligation") or ""): row
			for row in doc.get("obligations") or []
			if compat.checked(_get(row, "satisfied"))
		}
		rows = _requested_obligations(args.get("obligations"))
		doc.set("obligations", [])
		for row in rows:
			previous = satisfied.get(row["obligation"])
			if previous is not None and not row["satisfied"]:
				row["satisfied"] = 1
				row["satisfied_on"] = _get(previous, "satisfied_on")
				row["evidence"] = row["evidence"] or _get(previous, "evidence") or ""
			doc.append("obligations", row)
		changed.append("obligations")

	if args.get("schedule") is not None:
		# A RECOGNISED TRANCHE IS NOT REPLACED, it is refused. A schedule row that
		# has already produced a journal entry is history: rewriting it would leave
		# an entry on the books pointing at a tranche that no longer says what it
		# said when the entry was written.
		recognized = [row for row in doc.get("schedule") or [] if compat.checked(_get(row, "recognized"))]
		if recognized:
			raise ToolError(
				f"{len(recognized)} tranche(s) on this contract have already been recognised and "
				"produced journal entries, so the schedule cannot be replaced wholesale — the "
				"entries would point at tranches that no longer say what they said. Cancel those "
				"entries first, or add to the schedule on a new contract. Nothing was changed."
			)
		current = [{"obligation": str(_get(row, "obligation") or "")} for row in doc.get("obligations") or []]
		rows = _requested_schedule(args.get("schedule"), current)
		doc.set("schedule", [])
		for row in rows:
			doc.append("schedule", row)
		changed.append("schedule")

	if not changed:
		raise ToolError(
			"nothing to update. Pass at least one of: status, recognition_method, total_value, "
			"contract_date, start_date, end_date, revenue_account, receivable_account, notes, "
			"obligations, schedule."
		)

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)
	return ToolResult(
		data={"contract": _describe(doc), "changed": changed, "was": before},
		summary=f"updated Revenue Contract {doc.name}: {', '.join(changed)}",
		docstatus_delta="0 → 0 (draft, edited)",
	)


# ── settlements ─────────────────────────────────────────────────────────────
def _settlements_for(contract: str) -> list[dict]:
	if not compat.doctype_exists(SETTLEMENT) or not compat.has_field(SETTLEMENT, "revenue_contract"):
		return []
	rows = frappe.db.get_all(
		SETTLEMENT,
		filters={"revenue_contract": contract},
		fields=compat.existing_fields(
			SETTLEMENT,
			(
				"name",
				"statement_number",
				"date",
				"customer",
				"status",
				"net_proceeds",
				"total_gross_revenue",
				"sales_invoice",
				"posted_journal_entry",
			),
		),
		order_by="date asc",
		limit=RECORD_CAP,
	)
	return [dict(row) for row in rows or []]


def link_settlement_to_contract(args: dict) -> ToolResult:
	"""Attach a settlement statement to the revenue contract it settles. MUTATING.

	THE JOIN THAT MAKES CONTRACT-TO-CASH TRACEABLE IN ONE HOP. A settlement is
	where a consignment pool stops being an estimate and becomes a number, which
	is the moment ASC 606's variable consideration resolves — so it is the natural
	link between what was promised and what was received.

	REFUSES A CUSTOMER MISMATCH. A settlement from one buyer attached to another
	buyer's contract would corrupt every trace and every revenue figure that reads
	through it, and it is an easy mistake to make from a docname. Pass
	`force=true` to attach anyway — there are real arrangements where the
	settling party is not the contracting party, and refusing those outright would
	be this app deciding it knows the deal better than the operator does.
	"""
	_require()
	compat.require_doctype(SETTLEMENT, _HINT)
	if not compat.has_field(SETTLEMENT, "revenue_contract"):
		raise ToolError(
			"this site's Settlement Statement has no `revenue_contract` column yet. It ships with "
			"erpnext_mcp v0.80.0 — run `bench --site <site> migrate`."
		)

	settlement = as_str(args, "settlement", required=True)
	if not frappe.db.exists(SETTLEMENT, settlement):
		raise ToolError(
			f"no Settlement Statement called {settlement!r} on this site. "
			"list_settlement_statements has the register."
		)
	doc = _contract_doc(as_str(args, "contract", required=True))
	row = frappe.db.get_value(
		SETTLEMENT, settlement, ["customer", "company", "revenue_contract", "net_proceeds"], as_dict=True
	)

	existing = row.get("revenue_contract")
	if existing and existing != doc.name:
		raise ToolError(
			f"Settlement Statement {settlement} is already linked to Revenue Contract {existing}. "
			"Unlink it there first — a settlement counted against two contracts would double-count "
			"the revenue. Nothing was changed."
		)

	warnings = []
	if row.get("customer") and row["customer"] != doc.customer:
		if not as_bool(args, "force", False):
			raise ToolError(
				f"Settlement Statement {settlement} is from {row['customer']!r} and Revenue Contract "
				f"{doc.name} is with {doc.customer!r}. A settlement attached to another buyer's "
				"contract corrupts every trace and every revenue figure that reads through it. Pass "
				"force=true if the settling party genuinely is not the contracting party. Nothing "
				"was changed."
			)
		warnings.append(
			f"the settlement is from {row['customer']!r} and the contract is with {doc.customer!r}; "
			"linked anyway because force was set."
		)
	if row.get("company") and row["company"] != doc.company:
		warnings.append(
			f"the settlement belongs to company {row['company']!r} and the contract to "
			f"{doc.company!r}. That is a cross-entity link and every consolidated figure reading "
			"through it will cross entities too."
		)

	frappe.db.set_value(SETTLEMENT, settlement, "revenue_contract", doc.name)

	data = {
		"settlement": settlement,
		"contract": doc.name,
		"customer": doc.customer,
		"net_proceeds": float(row.get("net_proceeds") or 0),
		"settlements_on_contract": _settlements_for(doc.name),
		"next_step": (
			"trace_contract_to_cash now walks this contract through to its GL entries. Note that "
			"linking a settlement does NOT recognise revenue — recognition follows the performance "
			"obligations, not the cash, which is the whole of ASC 606's argument."
		),
	}
	if warnings:
		data["warnings"] = warnings
	return ToolResult(
		data=data,
		summary=f"linked Settlement Statement {settlement} to Revenue Contract {doc.name}",
		docstatus_delta="0 → 0 (link set)",
	)


# ── recognition ─────────────────────────────────────────────────────────────
def recognize_revenue_milestone(args: dict) -> ToolResult:
	"""Recognise one scheduled tranche, producing a DRAFT journal entry. MUTATING.

	THE `revenue_recognition` CONTROL RUNS HERE, BEFORE ANYTHING IS WRITTEN. A
	milestone tranche whose obligation is not satisfied is reported and recognised
	anyway (Advisory) or refused (Enforced). A time tranche not yet due is treated
	the same way — recognising next quarter's storage fee this quarter is the same
	error with a different trigger.

	THE ENTRY IS A DRAFT, like every journal entry this app writes. Revenue
	recognition is the single posting most likely to be wrong in a way that
	matters, because it is the one that moves the top line — so it is a posting
	somebody signs off rather than a side effect of ticking a box.
	"""
	_require()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _contract_doc(as_str(args, "contract", required=True), company)
	described = _describe(doc)

	tranche = _pick_tranche(doc, args)
	row = doc.get("schedule")[tranche["index"]]
	amount = float(_get(row, "amount") or 0)
	posting_date = as_date(args, "posting_date") or frappe.utils.today()

	if not doc.revenue_account or not doc.receivable_account:
		raise ToolError(
			f"Revenue Contract {doc.name} has no "
			+ ("revenue_account" if not doc.revenue_account else "receivable_account")
			+ " set, and this tool will not guess one. An account picked by an algorithm is a "
			"misstatement somebody finds at year end. Set it with update_revenue_contract. "
			"Nothing was written."
		)

	# ── the control, before any write ───────────────────────────────────────
	findings = []
	basis = str(_get(row, "basis") or "Milestone")
	if basis == "Milestone":
		obligation_text = str(_get(row, "obligation") or "")
		match = [item for item in described["obligations"] if item["obligation"] == obligation_text]
		if not match or not match[0]["satisfied"]:
			findings.append(
				enforcement.Finding(
					control_point="revenue_recognition",
					message=(
						f"{amount} is being recognised against performance obligation "
						f"{obligation_text!r} on contract {doc.name}, which is not marked satisfied. "
						"ASC 606 recognises revenue when control transfers, not when a schedule says "
						"it should have."
					),
					remedy=(
						"Mark the obligation satisfied with update_revenue_contract once control has "
						"genuinely transferred, recording the date and the evidence for it."
					),
					source_doctype=CONTRACT,
					source_docname=doc.name,
					company=doc.company,
					detail={"obligation": obligation_text, "amount": amount, "basis": basis},
				)
			)
	else:
		due = str(_get(row, "due_date") or "")
		if due and due > posting_date:
			findings.append(
				enforcement.Finding(
					control_point="revenue_recognition",
					message=(
						f"{amount} is being recognised on {posting_date} against a time-based tranche "
						f"of contract {doc.name} that is not due until {due}."
					),
					remedy=(
						"Recognise it on or after its due date, or move the due date with "
						"update_revenue_contract if the schedule was wrong."
					),
					source_doctype=CONTRACT,
					source_docname=doc.name,
					company=doc.company,
					detail={"due_date": due, "posting_date": posting_date, "amount": amount},
				)
			)

	control = enforcement.evaluate("revenue_recognition", findings, company=doc.company)

	# ── the draft entry ─────────────────────────────────────────────────────
	remark = as_str(args, "user_remark") or (
		f"Revenue recognition — {doc.contract_name} — "
		+ (
			str(_get(row, "obligation") or "")
			if basis == "Milestone"
			else f"tranche due {_get(row, 'due_date')}"
		)
	)
	lines = mutate.validated_journal_lines(
		[
			{"account": doc.receivable_account, "debit": amount},
			{"account": doc.revenue_account, "credit": amount},
		],
		doc.company,
	)
	entry = mutate.insert_draft_journal_entry(doc.company, posting_date, lines, remark)

	row.update(
		{
			"recognized": 1,
			"recognized_on": posting_date,
			"journal_entry": entry.name,
		}
	)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	after = _describe(doc)
	return ToolResult(
		data={
			"contract": after,
			"tranche": {
				"index": tranche["index"],
				"basis": basis,
				"obligation": _get(row, "obligation") or None,
				"amount": amount,
			},
			"journal_entry": entry.name,
			"posting_date": posting_date,
			"control": control,
			"next_step": (
				f"Journal Entry {entry.name} is a DRAFT and affects no balance. Submit it in ERPNext, "
				"or via submit_journal_entry if that tool is enabled."
			),
		},
		summary=(
			f"recognised {amount} on {doc.name} ({posting_date}) as draft Journal Entry {entry.name}; "
			f"{after['unrecognized_amount']} left to recognise"
			+ (f" — {len(findings)} advisory control finding(s)" if findings else "")
		),
		docstatus_delta="none → 0 (draft)",
	)


def _pick_tranche(doc, args: dict) -> dict:
	"""Which schedule row to recognise, by index or by obligation."""
	schedule = list(doc.get("schedule") or [])
	if not schedule:
		raise ToolError(
			f"Revenue Contract {doc.name} has no recognition schedule, so there is nothing to "
			"recognise. Add one with update_revenue_contract."
		)

	index = args.get("tranche")
	if index is not None:
		try:
			index = int(index)
		except (TypeError, ValueError):
			raise ToolError(f"tranche must be a whole number (1-based), got {index!r}") from None
		if not 1 <= index <= len(schedule):
			raise ToolError(f"tranche {index} is outside this contract's schedule of {len(schedule)} row(s).")
		position = index - 1
		if compat.checked(_get(schedule[position], "recognized")):
			raise ToolError(
				f"tranche {index} on {doc.name} was already recognised on "
				f"{_get(schedule[position], 'recognized_on')} as Journal Entry "
				f"{_get(schedule[position], 'journal_entry')}. Nothing was written."
			)
		return {"index": position}

	obligation = as_str(args, "obligation")
	candidates = [
		position
		for position, row in enumerate(schedule)
		if not compat.checked(_get(row, "recognized"))
		and (not obligation or str(_get(row, "obligation") or "") == obligation)
	]
	if not candidates:
		if obligation:
			raise ToolError(
				f"every tranche for obligation {obligation!r} on {doc.name} has already been "
				"recognised, or there is no such obligation on its schedule. Nothing was written."
			)
		raise ToolError(f"every tranche on {doc.name} has already been recognised. Nothing was written.")
	return {"index": candidates[0]}


# ── the trace ───────────────────────────────────────────────────────────────
def trace_contract_to_cash(args: dict) -> ToolResult:
	"""The whole chain: contract → settlement → invoice → payment → GL entries. Read-only.

	THE READ THIS PHASE EXISTS FOR. An auditor's question about revenue is almost
	never "what is the balance" — it is "show me one sale all the way through",
	and answering that usually means opening five documents in five modules and
	writing the links down on paper.

	IT REPORTS THE BREAK RATHER THAN HIDING IT. A chain that stops at the invoice
	comes back saying exactly that, with the hop that is missing named. "This
	revenue was recognised and has not been collected" is the single most useful
	thing this read can tell anybody, and a trace that quietly returned four hops
	instead of five would bury it.
	"""
	_require()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	doc = _contract_doc(as_str(args, "contract", required=True), company)
	described = _describe(doc)

	chain = []
	breaks = []

	chain.append(
		{
			"hop": "contract",
			"doctype": CONTRACT,
			"name": doc.name,
			"date": str(doc.contract_date or "") or None,
			"amount": described["total_value"],
			"detail": {
				"customer": doc.customer,
				"status": doc.status,
				"recognition_method": doc.recognition_method,
				"recognized_amount": described["recognized_amount"],
				"unrecognized_amount": described["unrecognized_amount"],
			},
		}
	)

	settlements = _settlements_for(doc.name)
	if not settlements:
		breaks.append(
			{
				"after": "contract",
				"missing": "settlement",
				"note": (
					"No settlement statement is linked to this contract. On a consignment pool that "
					"means the price is still an estimate and nothing has been settled; on a fixed "
					"price contract it may simply mean this operation does not use settlements. "
					"link_settlement_to_contract is how one is attached."
				),
			}
		)
	for row in settlements:
		chain.append(
			{
				"hop": "settlement",
				"doctype": SETTLEMENT,
				"name": row["name"],
				"date": str(row.get("date") or "") or None,
				"amount": float(row.get("net_proceeds") or 0),
				"detail": {
					"statement_number": row.get("statement_number"),
					"status": row.get("status"),
					"gross": float(row.get("total_gross_revenue") or 0),
				},
			}
		)

	invoices = _invoices(doc, settlements, breaks)
	for row in invoices:
		chain.append(
			{
				"hop": "invoice",
				"doctype": "Sales Invoice",
				"name": row["name"],
				"date": str(row.get("posting_date") or "") or None,
				"amount": float(row.get("grand_total") or 0),
				"detail": {
					"docstatus": int(row.get("docstatus") or 0),
					"outstanding_amount": float(row.get("outstanding_amount") or 0),
					"status": row.get("status"),
				},
			}
		)

	payments = _payments(invoices, breaks)
	for row in payments:
		chain.append(
			{
				"hop": "payment",
				"doctype": "Payment Entry",
				"name": row["name"],
				"date": str(row.get("posting_date") or "") or None,
				"amount": float(row.get("paid_amount") or 0),
				"detail": {"docstatus": int(row.get("docstatus") or 0), "against": row.get("against")},
			}
		)

	vouchers = (
		[row["name"] for row in invoices]
		+ [row["name"] for row in payments]
		+ [str(_get(row, "journal_entry")) for row in doc.get("schedule") or [] if _get(row, "journal_entry")]
	)
	gl = _gl_entries(vouchers, breaks)
	for row in gl:
		chain.append(
			{
				"hop": "gl",
				"doctype": "GL Entry",
				"name": row["name"],
				"date": str(row.get("posting_date") or "") or None,
				"amount": round(float(row.get("debit") or 0) - float(row.get("credit") or 0), 2),
				"detail": {
					"account": row.get("account"),
					"voucher_type": row.get("voucher_type"),
					"voucher_no": row.get("voucher_no"),
				},
			}
		)

	hops = {"contract", "settlement", "invoice", "payment", "gl"}
	present = {entry["hop"] for entry in chain}
	collected = round(sum(float(row.get("paid_amount") or 0) for row in payments), 2)
	outstanding = round(sum(float(row.get("outstanding_amount") or 0) for row in invoices), 2)

	return ToolResult(
		data={
			"contract": doc.name,
			"customer": doc.customer,
			"company": doc.company,
			"chain": chain,
			"hop_count": len(chain),
			"hops_present": sorted(present),
			"hops_missing": sorted(hops - present),
			"complete": not (hops - present),
			"breaks": breaks,
			"contract_value": described["total_value"],
			"recognized_amount": described["recognized_amount"],
			"collected_amount": collected,
			"outstanding_amount": outstanding,
			"note": (
				"A BREAK IS THE POINT OF THIS READ, not a failure of it. `hops_missing` names the "
				"links that are not there and `breaks` says what each one would have meant — "
				"'recognised and not collected' is the most useful answer this tool gives, and a "
				"trace that quietly returned the hops it did find would bury it."
			),
		},
		summary=(
			f"traced {doc.name} ({doc.customer}): {len(chain)} hop(s) across "
			f"{len(present)} of {len(hops)} stages"
			+ (f"; broken at {', '.join(sorted(hops - present))}" if hops - present else "; chain complete")
			+ f". {collected} collected of {described['total_value']}"
		),
	)


def _invoices(doc, settlements: list, breaks: list) -> list[dict]:
	"""Sales Invoices reachable from the settlements, or from the customer.

	TWO ROUTES, AND THE SECOND IS DELIBERATELY LOOSER. A settlement that names its
	invoice gives an exact link. A contract with no settlements falls back to the
	customer's invoices inside the contract's own date range — which is a
	HEURISTIC, is labelled as one in the output, and is better than the honest
	alternative of reporting no invoices for an operation that simply invoices
	directly.
	"""
	if not compat.doctype_exists("Sales Invoice"):
		return []
	fields = compat.existing_fields(
		"Sales Invoice",
		("name", "posting_date", "grand_total", "outstanding_amount", "status", "docstatus", "customer"),
	)
	named = [row.get("sales_invoice") for row in settlements if row.get("sales_invoice")]
	if named:
		rows = frappe.db.get_all(
			"Sales Invoice", filters={"name": ("in", named)}, fields=fields, limit=RECORD_CAP
		)
		return [dict(row) for row in rows or []]

	if settlements:
		breaks.append(
			{
				"after": "settlement",
				"missing": "invoice",
				"note": (
					"No settlement on this contract names a Sales Invoice. The revenue may have been "
					"recognised by journal entry without ever being invoiced, which is normal on a "
					"consignment pool where the buyer's settlement IS the billing document."
				),
			}
		)
		return []

	filters = {"customer": doc.customer, "docstatus": ("!=", 2)}
	if doc.start_date and doc.end_date:
		filters["posting_date"] = ("between", [str(doc.start_date), str(doc.end_date)])
	rows = frappe.db.get_all(
		"Sales Invoice", filters=filters, fields=fields, order_by="posting_date asc", limit=RECORD_CAP
	)
	out = [dict(row) for row in rows or []]
	if out:
		breaks.append(
			{
				"after": "contract",
				"missing": None,
				"note": (
					f"{len(out)} Sales Invoice(s) were found BY HEURISTIC — this customer, inside the "
					"contract's date range — because no settlement links them explicitly. Treat them "
					"as candidates rather than as part of the chain; link a settlement with "
					"link_settlement_to_contract to make the connection a fact."
				),
			}
		)
	return out


def _payments(invoices: list, breaks: list) -> list[dict]:
	if not invoices or not compat.doctype_exists("Payment Entry Reference"):
		if invoices:
			breaks.append(
				{
					"after": "invoice",
					"missing": "payment",
					"note": "This site has no Payment Entry Reference doctype, so payments cannot be traced.",
				}
			)
		return []
	names = [row["name"] for row in invoices]
	refs = frappe.db.get_all(
		"Payment Entry Reference",
		filters={"reference_doctype": "Sales Invoice", "reference_name": ("in", names)},
		fields=["parent", "reference_name", "allocated_amount"],
		limit=RECORD_CAP,
	)
	parents = sorted({row["parent"] for row in refs or [] if row.get("parent")})
	if not parents:
		breaks.append(
			{
				"after": "invoice",
				"missing": "payment",
				"note": (
					"No Payment Entry references any of these invoices. THIS IS THE MOST USEFUL "
					"BREAK THIS TOOL REPORTS: the revenue exists on the books and the cash has not "
					"arrived."
				),
			}
		)
		return []
	rows = frappe.db.get_all(
		"Payment Entry",
		filters={"name": ("in", parents)},
		fields=compat.existing_fields(
			"Payment Entry", ("name", "posting_date", "paid_amount", "docstatus", "party")
		),
		order_by="posting_date asc",
		limit=RECORD_CAP,
	)
	return [dict(row) for row in rows or []]


def _gl_entries(vouchers: list, breaks: list) -> list[dict]:
	vouchers = sorted({name for name in vouchers if name})
	if not vouchers or not compat.doctype_exists("GL Entry"):
		if vouchers:
			breaks.append({"after": "payment", "missing": "gl", "note": "This site has no GL Entry doctype."})
		return []
	rows = frappe.db.get_all(
		"GL Entry",
		filters={"voucher_no": ("in", vouchers), "is_cancelled": 0},
		fields=compat.existing_fields(
			"GL Entry",
			("name", "posting_date", "account", "debit", "credit", "voucher_type", "voucher_no"),
		),
		order_by="posting_date asc",
		limit=RECORD_CAP,
	)
	out = [dict(row) for row in rows or []]
	if not out:
		breaks.append(
			{
				"after": "payment",
				"missing": "gl",
				"note": (
					"None of the documents in this chain has posted to the general ledger. Every "
					"journal entry this app writes is a DRAFT, so this is the expected state until "
					"somebody submits them — it is not evidence that anything is wrong."
				),
			}
		)
	return out
