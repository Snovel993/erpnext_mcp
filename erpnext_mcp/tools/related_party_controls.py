# SPDX-License-Identifier: MIT
"""Related-party transactions: found in the ledger, priced against the market, disclosed.

WHAT THIS ADDS TO THE REGISTER THAT ALREADY EXISTED. `tools/parties.py` answers
"who is related to this company, how, and since when" — a state. This module
answers the three questions that follow from it and that no register can:

    WHICH TRANSACTIONS were with those parties      get_related_party_transactions
    WAS EACH ONE PRICED at arm's length, on paper   flag_related_party_transaction
    WHAT DOES THE SCHEDULE SAY at year end          generate_related_party_disclosure

THE MATCH IS THE HARD PART, AND IT IS DONE BY LINK RATHER THAN BY NAME. A
Related Party row carries a `supplier` link, and that link — not a string
comparison — is what turns a payment in the ledger into a disclosable
transaction. Name matching is offered as a SECOND and clearly labelled signal
(`match: "name"`), never as the primary one, because "T. Polehn Trucking LLC" and
"Polehn Trucking" are the same vendor to a person and different strings to a
database, and a disclosure schedule that silently guessed which is which is worse
than one that names what it could not resolve. Every unresolved candidate comes
back in `unmatched_parties` rather than being dropped.

WHY THE LEDGER SCAN DOES NOT GUESS A TRANSACTION TYPE. A Purchase Invoice could
be goods, hauling, a management fee or rent, and the voucher does not say which.
So the scan reports the transaction and its voucher type, and coverage is tested
on PARTY, DATE and AMOUNT — the three facts the ledger actually knows. A memo's
`transaction_type` narrows the match only when a caller states one, which is the
case where somebody has said what the dealing was. Inferring "Management Fee"
from a Journal Entry would manufacture findings against farms that had done the
work, and a control that cries wolf is a control that gets switched off.

WHAT "DOCUMENTED" MEANS is defined in exactly one place — `covers_row` on the
Transfer Pricing Documentation controller — and every function here calls it. The
gate, the register and the year-end schedule therefore cannot come to disagree,
which is the failure that matters most: an operator told they are covered by one
tool and refused by another stops believing both.

ADVISORY BY DEFAULT, LIKE EVERY OTHER CONTROL POINT. `flag_related_party_
transaction` reports an undocumented dealing and lets it through; the same call
on a site that has set the rule to Enforced refuses it, having written the same
alert. See `erpnext_mcp/enforcement.py` — this module owns no part of that
decision, it only supplies the findings.
"""

import frappe

from .. import compat, enforcement
from ..args import as_bool, as_choice, as_date, as_float, as_limit, as_str, resolve_company
from ..erpnext_mcp.doctype.transfer_pricing_documentation.transfer_pricing_documentation import (
	AMOUNT_TOLERANCE,
	COMPLETE,
	DRAFT,
	covers_row,
)
from ..errors import ToolError
from ..result import ToolResult
from .parties import DISCLOSABLE_RELATIONSHIPS, RELATED_PARTY

TPD = "Transfer Pricing Documentation"
GOVERNANCE_DOCUMENT = "Governance Document"

#: The control point this module supplies findings to. Declared in
#: `enforcement.CONTROL_POINTS`, seeded as a Compliance Rule in Advisory mode.
CONTROL_POINT = "related_party_transfer_pricing"

#: Party types on a GL Entry that can be a related party. `Employee` is
#: deliberately absent: wages to a family member who works here are payroll, they
#: are disclosed as compensation rather than as a related-party transaction, and
#: sweeping them in here would bury four real findings under sixty pay slips.
LEDGER_PARTY_TYPES = ("Supplier", "Customer")

#: A scan that would have to read more than this refuses rather than truncating.
#: A disclosure schedule built from the first N postings is a wrong schedule that
#: looks right, which is the one failure mode a disclosure tool must not have.
LEDGER_CAP = 20000

_TPD_FIELDS = (
	"name",
	"company",
	"related_party",
	"transaction_type",
	"period_start",
	"period_end",
	"status",
	"superseded_by",
	"amount",
	"currency",
	"pricing_method",
	"market_rate_reference",
	"justification",
	"comparables",
	"supporting_document",
	"prepared_by",
	"prepared_on",
	"reviewed_by",
	"reviewed_on",
	"notes",
	"creation",
	"owner",
)

_PARTY_FIELDS = (
	"name",
	"party_name",
	"company",
	"party_type",
	"relationship_to_company",
	"effective_date",
	"end_date",
	"supplier",
	"governing_document",
)


def available() -> bool:
	return compat.doctype_exists(TPD) and compat.doctype_exists(RELATED_PARTY)


def _require() -> None:
	compat.require_doctype(
		TPD,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)
	compat.require_doctype(
		RELATED_PARTY,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


# ── reading the register ────────────────────────────────────────────────────
def _register(company: str, disclosable_only: bool = True) -> list:
	"""The related parties of one company, newest relationship first."""
	rows = frappe.db.get_all(
		RELATED_PARTY,
		filters={"company": company},
		fields=compat.existing_fields(RELATED_PARTY, _PARTY_FIELDS),
		order_by="effective_date desc",
		limit=1000,
	)
	rows = [dict(row) for row in rows]
	if disclosable_only:
		rows = [row for row in rows if row.get("relationship_to_company") in DISCLOSABLE_RELATIONSHIPS]
	return rows


def _by_supplier(register: list) -> dict:
	"""{supplier docname: the related-party row}. The primary, non-guessing match."""
	index = {}
	for row in register:
		supplier = str(row.get("supplier") or "")
		if supplier:
			index.setdefault(supplier, row)
	return index


def _by_name(register: list) -> dict:
	"""{case-folded party name: the row}. The SECOND signal, always labelled as one."""
	index = {}
	for row in register:
		name = str(row.get("party_name") or "").strip().casefold()
		if name:
			index.setdefault(name, row)
	return index


def _resolve_party(party: str, party_type: str, by_supplier: dict, by_name: dict) -> tuple:
	"""(related party row, how it was matched). ('', '') when nothing matched.

	Link first, name second, and the caller is always told which — see the module
	docstring on why a name match is never allowed to look like a link match.
	"""
	if party_type == "Supplier" and party in by_supplier:
		return by_supplier[party], "supplier_link"
	hit = by_name.get(str(party or "").strip().casefold())
	if hit:
		return hit, "name"
	return {}, ""


# ── reading the memos ───────────────────────────────────────────────────────
def _memos(company: str, related_party: str = "") -> list:
	"""Every transfer pricing memo for a company, or for one party of it."""
	filters = {"company": company}
	if related_party:
		filters["related_party"] = related_party
	rows = frappe.db.get_all(
		TPD,
		filters=filters,
		fields=compat.existing_fields(TPD, _TPD_FIELDS),
		order_by="period_start desc",
		limit=1000,
	)
	return [dict(row) for row in rows]


def _covering(memos: list, related_party: str, posting_date: str, amount, transaction_type: str = "") -> list:
	"""The memos that document this dealing. One rule, called from everywhere."""
	return [
		memo
		for memo in memos
		if memo.get("related_party") == related_party
		and covers_row(memo, transaction_type, posting_date, amount)
	]


def _drafts_for(memos: list, related_party: str, posting_date: str) -> list:
	"""Memos that WOULD cover this if somebody finished them. A distinct finding."""
	out = []
	for memo in memos:
		if memo.get("related_party") != related_party or memo.get("status") != DRAFT:
			continue
		date = str(posting_date or "")
		start = str(memo.get("period_start") or "")
		end = str(memo.get("period_end") or "")
		if date and start and date < start:
			continue
		if date and end and date > end:
			continue
		out.append(memo)
	return out


def _describe_memo(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"company": row.get("company"),
		"related_party": row.get("related_party"),
		"transaction_type": row.get("transaction_type"),
		"period_start": str(row.get("period_start") or "") or None,
		"period_end": str(row.get("period_end") or "") or None,
		"status": row.get("status"),
		"superseded_by": row.get("superseded_by") or None,
		"amount": round(frappe.utils.flt(row.get("amount")), 2),
		"currency": row.get("currency") or None,
		"pricing_method": row.get("pricing_method") or None,
		"market_rate_reference": row.get("market_rate_reference") or None,
		"justification": row.get("justification") or None,
		"comparables": row.get("comparables") or None,
		"supporting_document": row.get("supporting_document") or None,
		"prepared_by": row.get("prepared_by") or None,
		"prepared_on": str(row.get("prepared_on") or "") or None,
		"reviewed_by": row.get("reviewed_by") or None,
		"reviewed_on": str(row.get("reviewed_on") or "") or None,
		# A memo the preparer also reviewed is not reviewed, and the schedule
		# reports that rather than counting it as done.
		"independently_reviewed": bool(
			row.get("reviewed_by") and row.get("reviewed_by") != row.get("prepared_by")
		),
		"notes": row.get("notes") or None,
	}


# ── reading the ledger ──────────────────────────────────────────────────────
def _ledger(company: str, start: str, end: str) -> list:
	"""Every party posting in the window. Refuses rather than truncating."""
	fields = compat.existing_fields(
		"GL Entry",
		(
			"name",
			"posting_date",
			"account",
			"debit",
			"credit",
			"party",
			"party_type",
			"voucher_type",
			"voucher_no",
			"remarks",
			"is_cancelled",
			"is_opening",
		),
	)
	rows = frappe.db.get_all(
		"GL Entry",
		filters={
			"company": company,
			"posting_date": ("between", (start, end)),
			"is_cancelled": 0,
			"party": ("is", "set"),
		},
		fields=fields,
		order_by="posting_date asc",
		limit=LEDGER_CAP + 1,
	)
	if len(rows) > LEDGER_CAP:
		raise ToolError(
			f"more than {LEDGER_CAP} party postings for {company} between {start} and {end}. A "
			"disclosure schedule built from the first "
			f"{LEDGER_CAP} would be a wrong schedule that looked right, so this refuses instead. "
			"Narrow the window."
		)
	return [dict(row) for row in rows]


def _transactions(company: str, start: str, end: str, register: list) -> dict:
	"""Ledger postings folded to one row per voucher per related party.

	THE VOUCHER IS THE TRANSACTION. A Purchase Invoice with four lines is one
	dealing with one party, and reporting it as four would make a schedule that
	foots correctly and reads like nonsense. The amount is the absolute net of
	the party's own postings on that voucher, which is the figure that appears on
	the invoice.
	"""
	by_supplier = _by_supplier(register)
	by_name = _by_name(register)
	folded: dict = {}
	unmatched: dict = {}
	opening = 0

	for row in _ledger(company, start, end):
		if str(row.get("is_opening") or "").strip().lower() == "yes":
			opening += 1
			continue
		party_type = row.get("party_type")
		if party_type not in LEDGER_PARTY_TYPES:
			continue
		party = str(row.get("party") or "")
		matched, how = _resolve_party(party, party_type, by_supplier, by_name)
		if not matched:
			bucket = unmatched.setdefault(
				(party_type, party), {"party_type": party_type, "party": party, "count": 0, "total": 0.0}
			)
			bucket["count"] += 1
			bucket["total"] += abs(float(row.get("debit") or 0) - float(row.get("credit") or 0))
			continue

		key = (matched["name"], row.get("voucher_type"), row.get("voucher_no"))
		entry = folded.setdefault(
			key,
			{
				"related_party": matched["name"],
				"party_name": matched.get("party_name"),
				"relationship_to_company": matched.get("relationship_to_company"),
				"ledger_party_type": party_type,
				"ledger_party": party,
				"match": how,
				"voucher_type": row.get("voucher_type"),
				"voucher_no": row.get("voucher_no"),
				"posting_date": str(row.get("posting_date") or ""),
				"amount": 0.0,
				"posting_count": 0,
			},
		)
		entry["amount"] += float(row.get("debit") or 0) - float(row.get("credit") or 0)
		entry["posting_count"] += 1
		date = str(row.get("posting_date") or "")
		if date and (not entry["posting_date"] or date < entry["posting_date"]):
			entry["posting_date"] = date

	transactions = []
	for entry in folded.values():
		entry["amount"] = round(abs(entry["amount"]), 2)
		transactions.append(entry)
	transactions.sort(key=lambda row: (row["posting_date"], -row["amount"]))
	return {
		"transactions": transactions,
		"unmatched_parties": sorted(
			({**bucket, "total": round(bucket["total"], 2)} for bucket in unmatched.values()),
			key=lambda row: -row["total"],
		),
		"opening_postings_skipped": opening,
	}


def _apply_coverage(transactions: list, memos: list) -> None:
	"""Stamp each transaction with whether a memo documents it, and which."""
	for row in transactions:
		covering = _covering(memos, row["related_party"], row["posting_date"], row["amount"])
		drafts = _drafts_for(memos, row["related_party"], row["posting_date"])
		row["documented"] = bool(covering)
		row["transfer_pricing_docs"] = [memo["name"] for memo in covering]
		row["draft_docs"] = [memo["name"] for memo in drafts]
		if covering:
			row["coverage"] = "documented"
		elif drafts:
			row["coverage"] = "draft_only"
		else:
			# Distinguish "nothing at all" from "a memo exists but is too small
			# to cover this" — the remedies are different work.
			near = [
				memo
				for memo in memos
				if memo.get("related_party") == row["related_party"]
				and memo.get("status") == COMPLETE
				and covers_row(memo, "", row["posting_date"], None)
			]
			row["coverage"] = "amount_exceeds_documentation" if near else "undocumented"
			if near:
				row["transfer_pricing_docs"] = [memo["name"] for memo in near]


def _finding_for(row: dict, company: str) -> enforcement.Finding:
	"""One transaction's finding, in the words its `coverage` earned."""
	party = row.get("party_name") or row.get("related_party")
	where = f"{row.get('voucher_type') or 'transaction'} {row.get('voucher_no') or ''}".strip()
	amount = row.get("amount")

	if row["coverage"] == "draft_only":
		message = (
			f"{where} — {amount:,.2f} with {party} ({row.get('relationship_to_company')}) — is "
			f"covered only by DRAFT transfer pricing documentation "
			f"({', '.join(row.get('draft_docs') or [])}). A draft is not a case."
		)
		remedy = (
			"Finish the memo — Pricing Method, Market Rate Reference and the arm's-length "
			"justification — and set its status to Complete with update_transfer_pricing_doc."
		)
	elif row["coverage"] == "amount_exceeds_documentation":
		message = (
			f"{where} — {amount:,.2f} with {party} ({row.get('relationship_to_company')}) — "
			f"exceeds the amount its transfer pricing documentation covers "
			f"({', '.join(row.get('transfer_pricing_docs') or [])}), beyond the "
			f"{int(AMOUNT_TOLERANCE * 100)}% tolerance."
		)
		remedy = (
			"Raise the documented amount on the memo if the arrangement grew, or write a second "
			"memo for the additional dealing, with create_transfer_pricing_doc."
		)
	else:
		message = (
			f"{where} — {amount:,.2f} with {party} ({row.get('relationship_to_company')}) — has NO "
			"transfer pricing documentation behind it. A related-party price nobody tested "
			"against the market is the finding a disclosure schedule exists to prevent."
		)
		remedy = (
			"Record the arm's-length case with create_transfer_pricing_doc: what the price was, "
			"what it was tested against, and why the two agree."
		)

	return enforcement.Finding(
		control_point=CONTROL_POINT,
		message=message,
		remedy=remedy,
		source_doctype=row.get("voucher_type") or "",
		source_docname=row.get("voucher_no") or "",
		company=company,
		detail={
			"related_party": row.get("related_party"),
			"amount": amount,
			"posting_date": row.get("posting_date"),
			"coverage": row.get("coverage"),
			"match": row.get("match"),
			"transfer_pricing_docs": row.get("transfer_pricing_docs") or [],
		},
	)


def _window(args: dict, default_days: int = 365) -> tuple:
	"""(from_date, to_date), defaulting to a year back from today."""
	to_date = as_date(args, "to_date") or frappe.utils.today()
	from_date = as_date(args, "from_date") or str(frappe.utils.add_days(to_date, -default_days))
	if from_date > to_date:
		raise ToolError(f"from_date {from_date} is after to_date {to_date}.")
	return from_date, to_date


# ── the tools ───────────────────────────────────────────────────────────────
def get_related_party_transactions(args: dict) -> ToolResult:
	"""Every transaction in a window that was with somebody on the register."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	from_date, to_date = _window(args)
	only_party = as_str(args, "related_party")
	min_amount = as_float(args.get("min_amount"), "min_amount") if args.get("min_amount") is not None else 0.0
	undocumented_only = as_bool(args, "undocumented_only", default=False)
	limit = as_limit(args)

	register = _register(company, disclosable_only=not as_bool(args, "include_arms_length_vendors", default=False))
	if only_party:
		register = [row for row in register if row["name"] == only_party]
		if not register:
			raise ToolError(
				f"{only_party!r} is not a Related Party of {company}. list_related_parties has the register."
			)

	scan = _transactions(company, from_date, to_date, register)
	memos = _memos(company, only_party)
	_apply_coverage(scan["transactions"], memos)

	rows = scan["transactions"]
	if min_amount:
		rows = [row for row in rows if row["amount"] >= min_amount]
	if undocumented_only:
		rows = [row for row in rows if not row["documented"]]

	total = round(sum(row["amount"] for row in rows), 2)
	undocumented = [row for row in rows if not row["documented"]]
	by_party: dict = {}
	for row in rows:
		bucket = by_party.setdefault(
			row["related_party"],
			{
				"related_party": row["related_party"],
				"party_name": row["party_name"],
				"relationship_to_company": row["relationship_to_company"],
				"count": 0,
				"total": 0.0,
				"undocumented_count": 0,
				"undocumented_total": 0.0,
			},
		)
		bucket["count"] += 1
		bucket["total"] = round(bucket["total"] + row["amount"], 2)
		if not row["documented"]:
			bucket["undocumented_count"] += 1
			bucket["undocumented_total"] = round(bucket["undocumented_total"] + row["amount"], 2)

	data = {
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"count": len(rows),
		"total": total,
		"undocumented_count": len(undocumented),
		"undocumented_total": round(sum(row["amount"] for row in undocumented), 2),
		"transactions": rows[:limit],
		"truncated": len(rows) > limit,
		"by_party": sorted(by_party.values(), key=lambda row: -row["total"]),
		"unmatched_parties": scan["unmatched_parties"],
		"opening_postings_skipped": scan["opening_postings_skipped"],
		# Read-only: shows what enforcement WOULD do without doing it.
		"control": enforcement.status(CONTROL_POINT),
		"how_matched": (
			"A transaction is related-party when its ledger party resolves to a Related Party "
			"row — by the row's `supplier` link (match: supplier_link) or, failing that, by an "
			"exact case-folded name match (match: name). Anything that resolved to neither is in "
			"`unmatched_parties` rather than dropped."
		),
		"how_priced": (
			"`amount` is the absolute net of that party's own postings on the voucher — the "
			"figure on the invoice. Coverage is tested on party, date and amount; a memo's "
			"transaction_type narrows it only when a caller states one, because the ledger does "
			"not know whether an invoice was goods, hauling or rent."
		),
	}
	return ToolResult(
		data=data,
		summary=(
			f"{len(rows)} related-party transaction(s) for {company}, {from_date} to {to_date}, "
			f"{total:,.2f} total, {len(undocumented)} undocumented"
		),
	)


def flag_related_party_transaction(args: dict) -> ToolResult:
	"""Evaluate one dealing against the transfer pricing control, and file what it finds.

	MUTATING because it writes compliance alerts — that is the whole of what it
	changes. Under Advisory it reports and allows; under Enforcement it refuses.
	Either way the same alert lands on the compliance calendar, which is the
	property that lets a farm run a season in Advisory and know exactly what
	Enforcement would have stopped.
	"""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	voucher_type = as_str(args, "voucher_type")
	voucher_no = as_str(args, "voucher_no")
	related_party = as_str(args, "related_party")

	if voucher_no and not voucher_type:
		raise ToolError("voucher_no was given without voucher_type. Say what kind of document it is.")
	if not voucher_no and not related_party:
		raise ToolError(
			"Name the dealing: either voucher_type + voucher_no for something already in the "
			"ledger, or related_party + amount + posting_date for something being considered. "
			"Nothing was evaluated."
		)

	register = _register(company, disclosable_only=False)
	memos = _memos(company)

	if voucher_no:
		rows = _voucher_rows(company, voucher_type, voucher_no, register)
		if not rows:
			raise ToolError(
				f"{voucher_type} {voucher_no} has no posting to a party on this company's "
				f"register, so there is nothing related-party about it to evaluate. "
				"list_related_parties has the register; a party that SHOULD be on it is "
				"registered with create_related_party."
			)
	else:
		rows = [_stated_row(args, company, register, related_party)]

	_apply_coverage(rows, memos)
	findings = [_finding_for(row, company) for row in rows if not row["documented"]]
	control = enforcement.evaluate(CONTROL_POINT, findings, company=company)

	data = {
		"company": company,
		"evaluated": rows,
		"count": len(rows),
		"documented_count": len([row for row in rows if row["documented"]]),
		"finding_count": len(findings),
		"control": control,
	}
	documented = data["documented_count"]
	return ToolResult(
		data=data,
		summary=(
			f"related-party control on {voucher_type + ' ' + voucher_no if voucher_no else related_party}: "
			f"{len(rows)} dealing(s), {documented} documented, {len(findings)} finding(s), "
			f"{control.get('action')}"
		),
	)


def _voucher_rows(company: str, voucher_type: str, voucher_no: str, register: list) -> list:
	"""One voucher's party postings, folded exactly as the scan folds them."""
	fields = compat.existing_fields(
		"GL Entry",
		("posting_date", "debit", "credit", "party", "party_type", "voucher_type", "voucher_no"),
	)
	rows = frappe.db.get_all(
		"GL Entry",
		filters={
			"company": company,
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"is_cancelled": 0,
			"party": ("is", "set"),
		},
		fields=fields,
		limit=500,
	)
	by_supplier = _by_supplier(register)
	by_name = _by_name(register)
	folded: dict = {}
	for row in rows:
		matched, how = _resolve_party(str(row.get("party") or ""), row.get("party_type"), by_supplier, by_name)
		if not matched:
			continue
		entry = folded.setdefault(
			matched["name"],
			{
				"related_party": matched["name"],
				"party_name": matched.get("party_name"),
				"relationship_to_company": matched.get("relationship_to_company"),
				"ledger_party_type": row.get("party_type"),
				"ledger_party": row.get("party"),
				"match": how,
				"voucher_type": voucher_type,
				"voucher_no": voucher_no,
				"posting_date": str(row.get("posting_date") or ""),
				"amount": 0.0,
				"posting_count": 0,
			},
		)
		entry["amount"] += float(row.get("debit") or 0) - float(row.get("credit") or 0)
		entry["posting_count"] += 1
	for entry in folded.values():
		entry["amount"] = round(abs(entry["amount"]), 2)
	return list(folded.values())


def _stated_row(args: dict, company: str, register: list, related_party: str) -> dict:
	"""A dealing somebody is asking about before it exists. Same shape as a found one."""
	match = [row for row in register if row["name"] == related_party]
	if not match:
		raise ToolError(
			f"{related_party!r} is not a Related Party of {company}. Register the relationship "
			"first with create_related_party — the control cannot evaluate a party the site does "
			"not know is related."
		)
	party = match[0]
	amount = as_float(args.get("amount"), "amount") if args.get("amount") is not None else 0.0
	if amount <= 0:
		raise ToolError("amount is required, and must be above zero, when no voucher is named.")
	return {
		"related_party": party["name"],
		"party_name": party.get("party_name"),
		"relationship_to_company": party.get("relationship_to_company"),
		"ledger_party_type": None,
		"ledger_party": None,
		"match": "stated",
		"voucher_type": as_str(args, "voucher_type") or "Proposed Transaction",
		"voucher_no": as_str(args, "reference") or "",
		"posting_date": as_date(args, "posting_date") or frappe.utils.today(),
		"amount": round(amount, 2),
		"posting_count": 0,
	}


def list_related_party_disclosures(args: dict) -> ToolResult:
	"""The disclosure register: every relationship that has to be disclosed, and its gaps."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	from_date, to_date = _window(args)
	include_ended = as_bool(args, "include_ended", default=True)
	limit = as_limit(args)

	register = _register(company)
	if not include_ended:
		today = frappe.utils.today()
		register = [row for row in register if not (row.get("end_date") and str(row["end_date"]) < today)]

	memos = _memos(company)
	scan = _transactions(company, from_date, to_date, register)
	_apply_coverage(scan["transactions"], memos)

	totals: dict = {}
	for row in scan["transactions"]:
		bucket = totals.setdefault(row["related_party"], {"count": 0, "total": 0.0, "undocumented": 0.0})
		bucket["count"] += 1
		bucket["total"] += row["amount"]
		if not row["documented"]:
			bucket["undocumented"] += row["amount"]

	disclosures = []
	for row in register:
		figures = totals.get(row["name"], {"count": 0, "total": 0.0, "undocumented": 0.0})
		party_memos = [memo for memo in memos if memo.get("related_party") == row["name"]]
		gaps = []
		if not row.get("governing_document"):
			gaps.append("no governing document establishes this relationship")
		if not row.get("supplier") and figures["count"] == 0:
			gaps.append(
				"no Supplier is linked, so payments to this party cannot be found in the ledger "
				"by anything but a name match"
			)
		if figures["undocumented"]:
			gaps.append(f"{figures['undocumented']:,.2f} of transactions with no transfer pricing documentation")
		if party_memos and not any(memo.get("status") == COMPLETE for memo in party_memos):
			gaps.append("every transfer pricing memo for this party is a draft")

		disclosures.append(
			{
				"related_party": row["name"],
				"party_name": row.get("party_name"),
				"party_type": row.get("party_type"),
				"relationship_to_company": row.get("relationship_to_company"),
				"effective_date": str(row.get("effective_date") or "") or None,
				"end_date": str(row.get("end_date") or "") or None,
				"current": not (row.get("end_date") and str(row["end_date"]) < frappe.utils.today()),
				"supplier": row.get("supplier") or None,
				"governing_document": row.get("governing_document") or None,
				"transaction_count": figures["count"],
				"transaction_total": round(figures["total"], 2),
				"undocumented_total": round(figures["undocumented"], 2),
				"transfer_pricing_docs": [memo["name"] for memo in party_memos],
				"complete_docs": len([memo for memo in party_memos if memo.get("status") == COMPLETE]),
				"gaps": gaps,
			}
		)

	disclosures.sort(key=lambda row: (-row["transaction_total"], row["party_name"] or ""))
	data = {
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"count": len(disclosures),
		"disclosures": disclosures[:limit],
		"truncated": len(disclosures) > limit,
		"with_gaps": len([row for row in disclosures if row["gaps"]]),
		"transacting": len([row for row in disclosures if row["transaction_count"]]),
		"control": enforcement.status(CONTROL_POINT),
		"note": (
			"Disclosable relationships only — every capacity except a plain arm's-length Vendor. "
			"A relationship that has ended is listed by default: the transactions it explains are "
			"still in the ledger and a prior period's schedule still needs to know who was who."
		),
	}
	return ToolResult(
		data=data,
		summary=(
			f"{len(disclosures)} disclosable relationship(s) for {company}, "
			f"{data['with_gaps']} with gaps"
		),
	)


def generate_related_party_disclosure(args: dict) -> ToolResult:
	"""The related-party schedule for a period: parties, dealings, totals, and what is missing."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	from_date, to_date = _window(args)

	register = _register(company)
	memos = _memos(company)
	scan = _transactions(company, from_date, to_date, register)
	_apply_coverage(scan["transactions"], memos)
	transactions = scan["transactions"]

	by_party: dict = {}
	for row in transactions:
		bucket = by_party.setdefault(
			row["related_party"],
			{
				"related_party": row["related_party"],
				"party_name": row["party_name"],
				"relationship_to_company": row["relationship_to_company"],
				"transaction_count": 0,
				"total": 0.0,
				"documented_total": 0.0,
				"undocumented_total": 0.0,
				"voucher_types": {},
				"transfer_pricing_docs": [],
			},
		)
		bucket["transaction_count"] += 1
		bucket["total"] += row["amount"]
		if row["documented"]:
			bucket["documented_total"] += row["amount"]
		else:
			bucket["undocumented_total"] += row["amount"]
		voucher_type = row.get("voucher_type") or "Other"
		bucket["voucher_types"][voucher_type] = round(
			bucket["voucher_types"].get(voucher_type, 0.0) + row["amount"], 2
		)
		for name in row.get("transfer_pricing_docs") or []:
			if name not in bucket["transfer_pricing_docs"]:
				bucket["transfer_pricing_docs"].append(name)

	schedule = []
	for bucket in by_party.values():
		party_memos = [_describe_memo(memo) for memo in memos if memo["name"] in bucket["transfer_pricing_docs"]]
		schedule.append(
			{
				**bucket,
				"total": round(bucket["total"], 2),
				"documented_total": round(bucket["documented_total"], 2),
				"undocumented_total": round(bucket["undocumented_total"], 2),
				"documentation": party_memos,
				"pricing_methods": sorted({memo["pricing_method"] for memo in party_memos if memo["pricing_method"]}),
			}
		)
	schedule.sort(key=lambda row: -row["total"])

	total = round(sum(row["total"] for row in schedule), 2)
	undocumented = round(sum(row["undocumented_total"] for row in schedule), 2)
	unreviewed = [
		memo["name"]
		for memo in (_describe_memo(row) for row in memos)
		if memo["status"] == COMPLETE and not memo["independently_reviewed"]
	]

	data = {
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"party_count": len(schedule),
		"transaction_count": len(transactions),
		"total": total,
		"documented_total": round(total - undocumented, 2),
		"undocumented_total": undocumented,
		"coverage_pct": round(((total - undocumented) / total * 100), 1) if total else 100.0,
		"schedule": schedule,
		"registered_without_transactions": sorted(
			row["name"] for row in register if row["name"] not in by_party
		),
		"unmatched_parties": scan["unmatched_parties"],
		"memos_not_independently_reviewed": unreviewed,
		"control": enforcement.status(CONTROL_POINT),
		"what_this_is": (
			"The related-party schedule for the period: who the company dealt with among its own, "
			"how much, and whether each dealing has an arm's-length case behind it. It is a "
			"REPORT and nothing here is filed anywhere — it is the working paper somebody writes "
			"the disclosure note from."
		),
		"what_it_cannot_see": (
			"Parties nobody registered. Every counterparty that did not resolve to the register "
			"is listed in `unmatched_parties` with its total, because the commonest way a "
			"related-party schedule is wrong is not a mispriced dealing — it is a relationship "
			"nobody wrote down."
		),
	}
	return ToolResult(
		data=data,
		summary=(
			f"related-party disclosure for {company}, {from_date} to {to_date}: {len(schedule)} "
			f"parties, {total:,.2f} total, {undocumented:,.2f} undocumented"
		),
	)


# ── the memos themselves ────────────────────────────────────────────────────
def create_transfer_pricing_doc(args: dict) -> ToolResult:
	"""Record the arm's-length case for one related-party arrangement."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	related_party = as_str(args, "related_party", required=True)
	party = frappe.db.get_value(
		RELATED_PARTY, related_party, ["name", "company", "party_name"], as_dict=True
	)
	if not party:
		raise ToolError(
			f"{related_party!r} is not a Related Party. Register the relationship first with "
			"create_related_party — a transfer pricing memo about somebody the site does not "
			"know is related documents nothing. Nothing was created."
		)
	if party["company"] != company:
		raise ToolError(
			f"Related Party {related_party} belongs to {party['company']}, not {company}. "
			"Nothing was created."
		)

	transaction_type = as_choice(
		TPD, "transaction_type", as_str(args, "transaction_type", required=True), "transaction_type"
	)
	period_start = as_date(args, "period_start", required=True)
	period_end = as_date(args, "period_end", required=True)
	if period_end < period_start:
		raise ToolError(
			f"period_end {period_end} is before period_start {period_start}. Nothing was created."
		)
	amount = as_float(args.get("amount"), "amount") if args.get("amount") is not None else 0.0
	if amount < 0:
		raise ToolError("amount cannot be negative — record the direction with transaction_type.")

	status = as_choice(TPD, "status", as_str(args, "status") or DRAFT, "status")
	supporting_document = as_str(args, "supporting_document")
	if supporting_document and not frappe.db.exists(GOVERNANCE_DOCUMENT, supporting_document):
		raise ToolError(f"Governance Document {supporting_document!r} does not exist. Nothing was created.")

	doc = frappe.new_doc(TPD)
	doc.company = company
	doc.related_party = related_party
	doc.transaction_type = transaction_type
	doc.period_start = period_start
	doc.period_end = period_end
	doc.amount = amount
	doc.status = status
	doc.market_rate_reference = as_str(args, "market_rate_reference")
	doc.justification = as_str(args, "justification")
	for field, value in (
		("currency", as_str(args, "currency")),
		("pricing_method", as_str(args, "pricing_method")),
		("comparables", as_str(args, "comparables")),
		("supporting_document", supporting_document),
		("prepared_by", as_str(args, "prepared_by") or frappe.session.user),
		("prepared_on", as_date(args, "prepared_on") or frappe.utils.today()),
		("reviewed_by", as_str(args, "reviewed_by")),
		("reviewed_on", as_date(args, "reviewed_on")),
		("notes", as_str(args, "notes")),
	):
		if value:
			doc.set(field, value)
	doc.insert()

	row = frappe.db.get_value(TPD, doc.name, compat.existing_fields(TPD, _TPD_FIELDS), as_dict=True)
	data = _describe_memo(dict(row))
	data["party_name"] = party.get("party_name")
	if status == DRAFT:
		data["next_step"] = (
			"This memo is a DRAFT and does not yet cover any transaction — the control reports a "
			"draft as its own kind of gap. Set status to Complete with "
			"update_transfer_pricing_doc once the justification, the market rate reference and "
			"the pricing method are all in it."
		)
	return ToolResult(
		data=data,
		summary=f"transfer pricing memo {doc.name} for {party.get('party_name')} ({status})",
		docstatus_delta="none → 0 (draft)",
	)


def get_transfer_pricing_doc(args: dict) -> ToolResult:
	"""One memo in full, with the transactions it covers."""
	_require()
	name = as_str(args, "transfer_pricing_doc", required=True)
	row = frappe.db.get_value(TPD, name, compat.existing_fields(TPD, _TPD_FIELDS), as_dict=True)
	if not row:
		raise ToolError(f"Transfer Pricing Documentation {name!r} does not exist.")
	row = dict(row)
	data = _describe_memo(row)

	party = frappe.db.get_value(
		RELATED_PARTY, row.get("related_party"), ["party_name", "relationship_to_company"], as_dict=True
	)
	data["party_name"] = (party or {}).get("party_name")
	data["relationship_to_company"] = (party or {}).get("relationship_to_company")

	register = _register(row["company"], disclosable_only=False)
	scan = _transactions(row["company"], str(row["period_start"]), str(row["period_end"]), register)
	covered = [
		transaction
		for transaction in scan["transactions"]
		if transaction["related_party"] == row["related_party"]
		and covers_row(row, "", transaction["posting_date"], transaction["amount"])
	]
	data["covers_transactions"] = covered
	data["covers_count"] = len(covered)
	data["covers_total"] = round(sum(transaction["amount"] for transaction in covered), 2)
	if row.get("status") == COMPLETE and data["covers_total"] > frappe.utils.flt(row.get("amount")) * (
		1 + AMOUNT_TOLERANCE
	):
		data["note"] = (
			f"The transactions in this period total {data['covers_total']:,.2f}, which is more "
			f"than the {frappe.utils.flt(row.get('amount')):,.2f} this memo documents. The "
			"overflow is reported as its own finding against each transaction."
		)
	return ToolResult(data=data, summary=f"transfer pricing memo {name}")


def list_transfer_pricing_docs(args: dict) -> ToolResult:
	"""The memos on file, newest period first."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	filters = {"company": company}
	related_party = as_str(args, "related_party")
	if related_party:
		filters["related_party"] = related_party
	status = as_str(args, "status")
	if status:
		filters["status"] = as_choice(TPD, "status", status, "status")
	transaction_type = as_str(args, "transaction_type")
	if transaction_type:
		filters["transaction_type"] = as_choice(TPD, "transaction_type", transaction_type, "transaction_type")
	limit = as_limit(args)

	rows = frappe.db.get_all(
		TPD,
		filters=filters,
		fields=compat.existing_fields(TPD, _TPD_FIELDS),
		order_by="period_start desc",
		limit=limit + 1,
	)
	memos = [_describe_memo(dict(row)) for row in rows[:limit]]
	data = {
		"company": company,
		"count": len(memos),
		"truncated": len(rows) > limit,
		"transfer_pricing_docs": memos,
		"complete": len([memo for memo in memos if memo["status"] == COMPLETE]),
		"draft": len([memo for memo in memos if memo["status"] == DRAFT]),
		"not_independently_reviewed": [
			memo["name"] for memo in memos if memo["status"] == COMPLETE and not memo["independently_reviewed"]
		],
	}
	return ToolResult(data=data, summary=f"{len(memos)} transfer pricing memo(s) for {company}")


def update_transfer_pricing_doc(args: dict) -> ToolResult:
	"""Change a memo: its period, its amount, its case, or its status."""
	_require()
	name = as_str(args, "transfer_pricing_doc", required=True)
	if not frappe.db.exists(TPD, name):
		raise ToolError(f"Transfer Pricing Documentation {name!r} does not exist. Nothing was changed.")
	doc = frappe.get_doc(TPD, name)
	before = doc.status

	changed = []
	for field, value in (
		("transaction_type", as_str(args, "transaction_type")),
		("pricing_method", as_str(args, "pricing_method")),
		("status", as_str(args, "status")),
	):
		if value:
			doc.set(field, as_choice(TPD, field, value, field) if field != "pricing_method" else value)
			changed.append(field)
	for field, key in (
		("period_start", "period_start"),
		("period_end", "period_end"),
		("prepared_on", "prepared_on"),
		("reviewed_on", "reviewed_on"),
	):
		value = as_date(args, key)
		if value:
			doc.set(field, value)
			changed.append(field)
	if args.get("amount") is not None:
		doc.amount = as_float(args.get("amount"), "amount")
		changed.append("amount")
	for field in (
		"market_rate_reference",
		"justification",
		"comparables",
		"currency",
		"supporting_document",
		"superseded_by",
		"prepared_by",
		"reviewed_by",
		"notes",
	):
		if field in args:
			# An empty string clears, which is how a link is unset.
			doc.set(field, as_str(args, field) or None)
			changed.append(field)

	if not changed:
		raise ToolError(
			"Nothing to change — no field was given. Pass at least one of: transaction_type, "
			"period_start, period_end, amount, pricing_method, market_rate_reference, "
			"justification, comparables, status, superseded_by, prepared_by, reviewed_by, notes."
		)
	doc.save()

	row = frappe.db.get_value(TPD, name, compat.existing_fields(TPD, _TPD_FIELDS), as_dict=True)
	data = _describe_memo(dict(row))
	data["changed"] = sorted(set(changed))
	if before != doc.status:
		data["status_change"] = f"{before} → {doc.status}"
		if doc.status == COMPLETE:
			data["note"] = (
				"This memo now COVERS transactions in its period, up to its documented amount "
				"plus the tolerance. Re-run flag_related_party_transaction on anything that was "
				"previously reported to confirm the finding clears."
			)
	return ToolResult(
		data=data,
		summary=f"transfer pricing memo {name} updated ({', '.join(sorted(set(changed)))})",
	)
