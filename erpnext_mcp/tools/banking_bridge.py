# SPDX-License-Identifier: MIT
"""The bank statement, and whether anybody can say what each line was.

v0.71.0, Sprint 6 — the capstone. Sprints 2 to 5 built the paper: a photographed
receipt, a bill from a supplier, a settlement from the packer, a cheque received,
a payroll run. Every one of those is a claim that money moved. This module is
about the OTHER record of the same movement — the bank's — and about the gap
between the two, which is the only place a farm finds out that a claim was wrong.

THREE SEPARATE QUESTIONS, DELIBERATELY NOT ONE FEATURE. They get confused
constantly and they have different answers:

  1. RECONCILIATION (ERPNext's own): is this Bank Transaction allocated against a
     Payment Entry or a Journal Entry, so the bank account's ledger balance is
     the statement balance? That already exists — `reconcile_bank_transaction`
     does it and this module does not duplicate it.
  2. EVIDENCE: is there a *receipt* behind this line — the slip from the pump,
     the invoice from the parts counter? A transaction can be perfectly
     reconciled to a Payment Entry and still have no paper behind it, which is
     exactly what an auditor asks about and what a deduction gets disallowed for.
  3. CATEGORISATION: what KIND of expense was it? A statement line reading
     `CHEVRON 0093746 PASCO WA` is not a category, and somebody has to say that
     it is Fuel and that fuel books to 5200.

`get_bank_reconciliation_status` answers all three, side by side, and never adds
them together. A dashboard that reported one number for "reconciled" would be
wrong in whichever of the three senses the reader had in mind.

MATCHING IS PROPOSED, NEVER COMMITTED IN BULK. `auto_match_receipts` is a READ
tool. It scores every unmatched receipt against every unmatched withdrawal and
hands back a ranked list with the exact call that would commit each one — and it
writes nothing. That is not timidity about a hard problem; it is the whole design
of this app applied to the one place it matters most. A wrong receipt-to-bank
link is invisible: both documents exist, both amounts are right, and the only
thing that is wrong is which slip is filed against which withdrawal. Nobody finds
that in a review, because there is nothing to see. So a person accepts each one,
and `bank_match_method` records that a machine proposed it.

CATEGORISATION IS DIFFERENT AND IS ALLOWED TO WRITE. A rule is deterministic and
inspectable — this pattern, this field, this category — and its output says which
rule produced it, so an operator who disagrees can read the rule, fix it, and run
it again. `apply_categorization_rules` writes for that reason, and only ever onto
transactions that have no category yet unless asked otherwise.

WHY THE RULES ARE A DOCTYPE AND NOT A DICTIONARY IN THIS FILE. Same argument the
compliance framework makes: a farm that changes fuel suppliers should not need a
release. `seed_farm_categorization_rules` puts a starting book of them on a site
and then gets out of the way — everything it seeds is an ordinary record an
operator can edit, disable or delete.

WHAT THIS MODULE ADDS TO A DOCTYPE IT DOES NOT OWN, AND WHY. Three Custom Fields
on ERPNext's Bank Transaction: `farm_category`, `farm_expense_account` and
`categorization_rule`. `compliance_fields.py` argues the general case for
extending somebody else's doctype and `sales.ensure_settlement_link_field` is the
narrow precedent. The argument here is that the alternative — a parallel
"Bank Transaction Categorisation" doctype with one row per transaction — is a
shadow record of a thing that already exists, and shadow records drift. The
fields are `allow_on_submit`, because a bank feed's transactions are submitted
and a bookkeeper correcting a category must not have to cancel one.

NOTHING IN THIS MODULE POSTS TO THE LEDGER. Not one function writes a GL Entry, a
Journal Entry or a Payment Entry. Categorising a transaction says what it was;
turning that into a posting is `create_journal_entry`, which has its own switch
and its own review. The line is deliberate: a tool that could both decide what a
statement line means and post it would be a tool that writes a farm's books from
a memo field.
"""

from __future__ import annotations

import datetime

import frappe
from frappe.utils import getdate, today

from .. import compat
from ..args import (
	as_bool,
	as_date,
	as_float,
	as_int,
	as_limit,
	as_str,
	resolve_account,
	resolve_company,
)
from ..errors import ToolError
from ..result import ToolResult
from . import expenses, read, receipts

BANK_TRANSACTION = "Bank Transaction"
BANK_ACCOUNT = "Bank Account"
EXPENSE_RECEIPT = expenses.EXPENSE_RECEIPT
RULE = "Bank Categorization Rule"
CUSTOM_FIELD = "Custom Field"

PAYMENT_ENTRY = "Payment Entry"
SALES_INVOICE = "Sales Invoice"
PURCHASE_INVOICE = "Purchase Invoice"
SETTLEMENT_STATEMENT = "Settlement Statement"
PAYROLL_ENTRY = "Farm Payroll Entry"

#: The three columns this module adds to Bank Transaction. See the module
#: docstring for why they are Custom Fields on ERPNext's doctype rather than a
#: parallel record of our own.
CATEGORY_FIELD = "farm_category"
ACCOUNT_FIELD = "farm_expense_account"
RULE_FIELD = "categorization_rule"

#: What Expense Receipt gained in v0.71.0. Shipped in the doctype JSON — this is
#: our own doctype, so there is no Custom Field dance — but read through
#: `compat.has_field` anyway, because a bench that pulled the code and has not
#: run `bench migrate` has the tools and not the columns.
RECEIPT_LINK_FIELD = "bank_transaction"
RECEIPT_METHOD_FIELD = "bank_match_method"
RECEIPT_CONFIDENCE_FIELD = "bank_match_confidence"
RECEIPT_MATCHED_ON_FIELD = "bank_matched_on"

#: How a link came to exist. `Manual` is a person naming both documents;
#: `Proposed` is a match `auto_match_receipts` scored and a person accepted.
MATCH_MANUAL = "Manual"
MATCH_PROPOSED = "Proposed"
MATCH_METHODS = (MATCH_MANUAL, MATCH_PROPOSED)

#: How many days after the receipt a card charge may post and still be the same
#: purchase. Seven covers a weekend plus a slow merchant batch; a fuel card
#: settling weekly is the case that needs the tail. Configurable per call.
DEFAULT_DATE_WINDOW_DAYS = 7

#: The furthest a caller may open the window. Beyond a month, "same amount, same
#: vendor" stops being evidence of anything on a farm that buys diesel weekly.
MAX_DATE_WINDOW_DAYS = 60

#: How far the transaction may predate the receipt and still be a candidate. One
#: day, for a purchase made near midnight or a bank posting in another timezone.
#: A transaction two days before the paper is not that purchase.
BACKDATE_TOLERANCE_DAYS = 1

#: How far apart two amounts may be and still be the same purchase, in currency.
#: Two cents is rounding, not a tip: this is a GATE, not a fuzzy score, and a
#: caller who wants to catch restaurant tips or a fuel pre-authorisation raises
#: it deliberately and sees the looser matches marked as such.
DEFAULT_AMOUNT_TOLERANCE = 0.02

#: What the three signals are worth in a match score. The amount is half of it
#: because it is the only one that is nearly impossible to coincide by accident
#: at farm transaction volumes; the date is next; the merchant is worth least
#: because a bank memo line is a mangled version of a name at best and a terminal
#: ID at worst. Tuned so an exact amount within a day scores above the default
#: threshold even when the memo line is unreadable, and an exact amount a week
#: later with no name agreement does not.
WEIGHT_AMOUNT = 0.5
WEIGHT_DATE = 0.3
WEIGHT_MERCHANT = 0.2

#: A similarity algorithm is never certain it has found the same purchase, only
#: that three numbers agree. Same ceiling, and the same reasoning, as
#: `receipts._MATCH_CEILING` and `classify_receipt`.
CONFIDENCE_CEILING = 0.95

#: Below this, a proposal is not worth a person's attention by default.
DEFAULT_MIN_CONFIDENCE = 0.70

#: How many rows a scanning tool will read before it stops and says it stopped.
#: A season of transactions on a working farm is a few thousand; this is above
#: that and far below the point where the Python-side scoring gets slow.
MAX_SCAN = 5000

#: The Bank Transaction fields every read here wants, filtered through `compat`
#: before any query — `bank_party_name` and the deposit/withdrawal pair are all
#: younger than the doctype.
_TRANSACTION_FIELDS = (
	"name",
	"date",
	"bank_account",
	"company",
	"description",
	"status",
	"reference_number",
	"currency",
	"party_type",
	"party",
	"bank_party_name",
	"docstatus",
	"deposit",
	"withdrawal",
	"amount",
	"allocated_amount",
	"unallocated_amount",
)

#: What a receipt row carries through this module. Deliberately smaller than
#: `expenses._LIST_FIELDS`: matching needs the money, the date, the vendor and
#: the link, and a caller who wants the photograph calls `get_expense_receipt`.
_RECEIPT_FIELDS = (
	"name",
	"merchant",
	"amount",
	"receipt_date",
	"category",
	"status",
	"company",
	"supplier",
	"cost_center",
	"linked_doctype",
	"linked_document",
)

#: Rule fields, in the order the evaluator wants them.
_RULE_FIELDS = (
	"name",
	"rule_name",
	"company",
	"category",
	"account",
	"cost_center",
	"enabled",
	"priority",
	"match_field",
	"match_type",
	"pattern",
	"direction",
	"amount_min",
	"amount_max",
	"party_type",
	"party",
	"times_applied",
	"last_applied",
	"notes",
)

_ERPNEXT_HINT = "It ships with ERPNext's Accounts module."


# ── schema: the columns this module needs, made to exist ─────────────────────


def ensure_categorization_fields() -> bool:
	"""Give Bank Transaction the three columns a categorisation writes into.

	Custom Fields, which is Frappe's supported way for one app to extend
	another's doctype — the mechanism `compliance_fields.py` defends at length
	and `sales.ensure_settlement_link_field` uses for the settlement back-link.

	`allow_on_submit` on all three, and that is load-bearing. A bank feed's
	transactions are submitted (`docstatus` 1), and a field without the flag
	cannot be changed on one without cancelling it — which would detach every
	allocation already made against it. A bookkeeper correcting a category must
	never have to do that.

	Created on first use as well as at install, so a bench that upgraded without
	running the installer still works the first time somebody categorises.

	NEVER RAISES. A site that will not take the fields loses categorisation and
	keeps everything else; every tool here reports `fields_installed: false` with
	the reason rather than pretending the columns are there.
	"""
	try:
		if _categorization_fields_present():
			return True
		if not compat.doctype_exists(CUSTOM_FIELD) or not compat.doctype_exists(BANK_TRANSACTION):
			return False
	except Exception:
		return False

	specification = (
		{
			"fieldname": CATEGORY_FIELD,
			"label": "Farm Category",
			"fieldtype": "Data",
			"insert_after": "description",
			"description": (
				"What kind of expense this line is — Fuel, Chemicals/Spray, Irrigation. Written by "
				"apply_categorization_rules from a Bank Categorization Rule, and editable by hand: a "
				"rule is a starting point, not a verdict."
			),
		},
		{
			"fieldname": ACCOUNT_FIELD,
			"label": "Farm Expense Account",
			"fieldtype": "Link",
			"options": "Account",
			"insert_after": CATEGORY_FIELD,
			"description": (
				"Where this line belongs in the chart of accounts, per the rule that categorised it. "
				"NOT a posting — nothing has been booked to this account until a Journal Entry or a "
				"Payment Entry says so."
			),
		},
		{
			"fieldname": RULE_FIELD,
			"label": "Categorization Rule",
			"fieldtype": "Link",
			"options": RULE,
			"insert_after": ACCOUNT_FIELD,
			"read_only": 1,
			"description": (
				"Which rule categorised this transaction. Read-only and the whole point of the "
				"exercise: a category nobody can trace back to a rule is a category nobody can argue "
				"with."
			),
		},
	)

	created = False
	for field in specification:
		try:
			if compat.has_field(BANK_TRANSACTION, field["fieldname"]):
				continue
			if frappe.db.exists(CUSTOM_FIELD, {"dt": BANK_TRANSACTION, "fieldname": field["fieldname"]}):
				continue
			doc = frappe.new_doc(CUSTOM_FIELD)
			doc.dt = BANK_TRANSACTION
			doc.allow_on_submit = 1
			for key, value in field.items():
				doc.set(key, value)
			doc.insert(ignore_permissions=True)
			created = True
		except Exception:
			frappe.log_error(
				title=f"erpnext_mcp: could not add {field['fieldname']} to Bank Transaction",
				message=compat.traceback_text(),
			)
			return False

	if created:
		try:
			frappe.clear_cache(doctype=BANK_TRANSACTION)
		except Exception:
			pass
	return _categorization_fields_present()


def _categorization_fields_present() -> bool:
	try:
		return all(
			compat.has_field(BANK_TRANSACTION, fieldname)
			for fieldname in (CATEGORY_FIELD, ACCOUNT_FIELD, RULE_FIELD)
		)
	except Exception:
		return False


def _receipt_can_link() -> bool:
	"""Whether this site's Expense Receipt has the v0.71.0 bank-match columns."""
	try:
		return compat.has_field(EXPENSE_RECEIPT, RECEIPT_LINK_FIELD)
	except Exception:
		return False


def _require_receipt_link_field() -> None:
	if _receipt_can_link():
		return
	raise ToolError(
		f"this site's Expense Receipt has no {RECEIPT_LINK_FIELD!r} column, so a receipt cannot "
		"record which bank transaction it is the paper for. It ships with erpnext_mcp v0.71.0 — "
		"run `bench migrate`. Nothing was written."
	)


# ── shared resolvers and shapes ──────────────────────────────────────────────


def _require_bank_transaction() -> None:
	compat.require_doctype(BANK_TRANSACTION, _ERPNEXT_HINT)


def _require_receipts() -> None:
	compat.require_doctype(
		EXPENSE_RECEIPT,
		"It ships with erpnext_mcp — run `bench migrate` after installing v0.31.0 or later.",
	)


def _require_rules() -> None:
	compat.require_doctype(RULE, "It ships with erpnext_mcp — run `bench migrate` after installing v0.71.0.")


def _bank_account(args: dict, *, required: bool = False) -> str:
	"""A Bank Account docname from any of the three names a caller might use.

	Delegates the docname-or-label resolution to `read._resolve_bank_account`
	rather than growing a second one: a bank account named two ways by two tools
	in one app is how a reconciliation ends up scoped to nothing.
	"""
	value = as_str(args, "bank_account") or as_str(args, "account") or as_str(args, "bank")
	if not value:
		if required:
			raise ToolError("bank_account is required — a reconciliation is always about one account.")
		return ""
	compat.require_doctype(BANK_ACCOUNT, _ERPNEXT_HINT)
	return read._resolve_bank_account(value)


def _date_window(args: dict) -> int:
	days = as_int(args, "date_window_days", DEFAULT_DATE_WINDOW_DAYS)
	days = DEFAULT_DATE_WINDOW_DAYS if days is None else int(days)
	if days < 0:
		raise ToolError("date_window_days cannot be negative.")
	if days > MAX_DATE_WINDOW_DAYS:
		raise ToolError(
			f"date_window_days is capped at {MAX_DATE_WINDOW_DAYS}. Past a month, an equal amount "
			"from a similar-looking vendor is a coincidence rather than evidence — on a farm that "
			"buys diesel every week it is a near-certain one."
		)
	return days


def _amount_tolerance(args: dict) -> float:
	raw = args.get("amount_tolerance")
	if raw in (None, ""):
		return DEFAULT_AMOUNT_TOLERANCE
	tolerance = as_float(raw, "amount_tolerance")
	if tolerance < 0:
		raise ToolError("amount_tolerance cannot be negative.")
	return round(tolerance, 4)


def _min_confidence(args: dict) -> float:
	raw = args.get("min_confidence")
	if raw in (None, ""):
		return DEFAULT_MIN_CONFIDENCE
	value = as_float(raw, "min_confidence")
	if value < 0 or value > 1:
		raise ToolError("min_confidence is a fraction from 0 to 1, not a percentage.")
	return round(value, 4)


def _apply_date_range(filters: dict, fieldname: str, from_date, to_date) -> None:
	if from_date and to_date:
		filters[fieldname] = ("between", [from_date, to_date])
	elif from_date:
		filters[fieldname] = (">=", from_date)
	elif to_date:
		filters[fieldname] = ("<=", to_date)


def _transaction_rows(filters: dict, *, limit: int = MAX_SCAN, order_by: str = "date asc, name asc") -> list:
	# The three categorisation columns are asked for and filtered out by
	# `existing_fields` when they are not there, so one query serves a site that
	# has them and a site that has never categorised anything.
	fields = compat.existing_fields(
		BANK_TRANSACTION, [*list(_TRANSACTION_FIELDS), CATEGORY_FIELD, ACCOUNT_FIELD, RULE_FIELD]
	)
	rows = frappe.db.get_all(BANK_TRANSACTION, filters=filters, fields=fields, order_by=order_by, limit=limit)
	money = compat.bank_transaction_amount_fields()
	out = []
	for row in rows:
		row = dict(row)
		row["amount_signed"] = round(compat.signed_amount(row, money), 2)
		row["gross_amount"] = round(compat.gross_amount(row, money), 2)
		row["direction"] = "Deposit" if row["amount_signed"] > 0 else "Withdrawal"
		out.append(row)
	return out


def _transaction_out(row: dict) -> dict:
	"""The Bank Transaction shape every tool in this module returns."""
	out = {
		"name": row.get("name"),
		"date": _date_text(row.get("date")),
		"bank_account": row.get("bank_account"),
		"company": row.get("company"),
		"description": row.get("description"),
		"reference_number": row.get("reference_number"),
		"status": row.get("status"),
		"amount_signed": row.get("amount_signed"),
		"gross_amount": row.get("gross_amount"),
		"direction": row.get("direction"),
		"party_type": row.get("party_type") or None,
		"party": row.get("party") or None,
		"bank_party_name": row.get("bank_party_name") or None,
		"unallocated_amount": _money(row.get("unallocated_amount")),
		"allocated_amount": _money(row.get("allocated_amount")),
	}
	for fieldname in (CATEGORY_FIELD, ACCOUNT_FIELD, RULE_FIELD):
		if fieldname in row:
			out[fieldname] = row.get(fieldname) or None
	return out


def _receipt_out(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"merchant": row.get("merchant"),
		"amount": _money(row.get("amount")),
		"receipt_date": _date_text(row.get("receipt_date")),
		"category": row.get("category"),
		"status": row.get("status"),
		"company": row.get("company"),
		"supplier": row.get("supplier") or None,
		"cost_center": row.get("cost_center") or None,
		"bank_transaction": row.get(RECEIPT_LINK_FIELD) or None,
	}


def _money(value) -> float:
	try:
		return round(float(value or 0), 2)
	except (TypeError, ValueError):
		return 0.0


def _date_text(value) -> str | None:
	if not value:
		return None
	if isinstance(value, (datetime.date, datetime.datetime)):
		return str(getdate(value))
	return str(value)


def _as_date(value):
	try:
		return getdate(value)
	except Exception:
		return None


# ── scoring: how alike a receipt and a statement line are ────────────────────


def score_match(receipt: dict, transaction: dict, *, tolerance: float, window: int) -> dict:
	"""Three signals and one number, with every part of it shown.

	Returns the breakdown as well as the total, always. A confidence with no
	breakdown is a number a person can only accept or reject; a breakdown is
	something they can disagree with a *part* of — "the amount is right and the
	date is eight days out" is the sentence that finds a duplicate charge.

	`eligible` is the gate and is separate from the score: an amount outside
	tolerance, a wrong direction, or a transaction that posted before the paper
	existed are all disqualifications rather than low scores, because a very good
	name match must never drag one of them over a threshold.
	"""
	receipt_amount = _money(receipt.get("amount"))
	gross = _money(transaction.get("gross_amount"))
	amount_gap = round(abs(receipt_amount - gross), 2)

	receipt_date = _as_date(receipt.get("receipt_date"))
	transaction_date = _as_date(transaction.get("date"))
	day_gap = None
	if receipt_date and transaction_date:
		day_gap = (transaction_date - receipt_date).days

	merchant_score = receipts._merchant_similarity(
		receipt.get("merchant") or "",
		transaction.get("description") or transaction.get("bank_party_name") or "",
	)

	reasons = []
	if transaction.get("direction") != "Withdrawal":
		reasons.append("the transaction is money IN, and an expense receipt is money out")
	if amount_gap > tolerance:
		reasons.append(
			f"amounts differ by {amount_gap} (receipt {receipt_amount}, transaction {gross}), "
			f"outside the {tolerance} tolerance"
		)
	if day_gap is None:
		reasons.append("one of the two documents has no date")
	elif day_gap < -BACKDATE_TOLERANCE_DAYS:
		reasons.append(f"the transaction posted {abs(day_gap)} days BEFORE the receipt was written")
	elif day_gap > window:
		reasons.append(
			f"the transaction posted {day_gap} days after the receipt, outside the {window}-day window"
		)

	amount_score = 0.0
	if amount_gap <= tolerance:
		amount_score = 1.0 if tolerance <= 0 else round(1.0 - (amount_gap / tolerance) * 0.1, 4)
	date_score = 0.0
	if day_gap is not None and window > 0:
		date_score = max(0.0, round(1.0 - abs(day_gap) / window, 4))
	elif day_gap == 0:
		date_score = 1.0

	confidence = round(
		min(
			CONFIDENCE_CEILING,
			WEIGHT_AMOUNT * amount_score + WEIGHT_DATE * date_score + WEIGHT_MERCHANT * merchant_score,
		),
		4,
	)

	return {
		"bank_transaction": transaction.get("name"),
		"expense_receipt": receipt.get("name"),
		"confidence": confidence if not reasons else 0.0,
		"eligible": not reasons,
		"blockers": reasons,
		"signals": {
			"amount_gap": amount_gap,
			"amount_score": round(amount_score, 4),
			"day_gap": day_gap,
			"date_score": date_score,
			"merchant_score": merchant_score,
			"receipt_amount": receipt_amount,
			"transaction_amount": gross,
			"merchant": receipt.get("merchant"),
			"description": transaction.get("description"),
		},
	}


# ── who is matched to what ───────────────────────────────────────────────────


def _receipts_by_transaction(names: list) -> dict:
	"""{bank transaction → [receipt row]} for the transactions named.

	One query for the whole set rather than one per transaction: the batch tools
	ask this about a season at a time.
	"""
	if not names or not _receipt_can_link():
		return {}
	rows = frappe.db.get_all(
		EXPENSE_RECEIPT,
		filters={RECEIPT_LINK_FIELD: ("in", list(names))},
		fields=["name", "merchant", "amount", "receipt_date", "status", RECEIPT_LINK_FIELD],
		limit=MAX_SCAN,
	)
	grouped = {}
	for row in rows:
		grouped.setdefault(row.get(RECEIPT_LINK_FIELD), []).append(dict(row))
	return grouped


def _allocation_state(row: dict) -> dict:
	"""What ERPNext itself thinks of this transaction, on either schema.

	`unallocated_amount` is the column when the site has it and `gross -
	allocated` when it does not, exactly as `list_unreconciled_bank_transactions`
	computes it — the two answers have to agree or an operator reading both tools
	gets two reconciliation worklists.
	"""
	money = compat.bank_transaction_amount_fields()
	gross = _money(row.get("gross_amount"))
	allocated = _money(row.get(money["allocated"])) if money["allocated"] else 0.0
	if money["unallocated"] and row.get(money["unallocated"]) is not None:
		unallocated = _money(row.get(money["unallocated"]))
	else:
		unallocated = round(gross - allocated, 2)
	return {
		"allocated": allocated,
		"unallocated": unallocated,
		"fully_allocated": unallocated <= 0 and gross > 0,
	}


# ── 1. match_receipt_to_bank_transaction ─────────────────────────────────────


def match_receipt_to_bank_transaction(args: dict) -> ToolResult:
	"""Link one receipt to one statement line — or, with no line named, rank the candidates.

	TWO MODES IN ONE TOOL, on purpose. Called with a `bank_transaction`, this is
	a person saying "that slip is this withdrawal", and it writes the link.
	Called without one, it scores every eligible transaction against the receipt
	and WRITES NOTHING — the candidate list is the answer, and the caller makes
	the second call with the one they picked.

	It is one tool rather than two because the second call is the first call with
	one more argument, and a client that has to know two tool names to do one
	thing will use the wrong one.

	WHAT IS REFUSED, ALL OF IT BEFORE ANYTHING IS WRITTEN: a deposit, a receipt
	and a transaction belonging to different companies, a rejected receipt, a
	transaction that already has a different receipt against it, and a receipt
	that is already matched elsewhere (unless `replace` says so, and then the old
	link is named in the result). Every one of those is a mistake that produces
	two documents which both look right.

	THE SCORE IS NOT IN THAT LIST, DELIBERATELY. An amount two cents out or a
	date eight days late is a judgement, and a person naming both documents
	outranks an algorithm — that link is made, the objections are returned, and
	the stored confidence is 0 so the pair surfaces in any later review. What
	cannot be overruled is the direction, which is not a judgement: money
	arriving is not an expense.
	"""
	_require_bank_transaction()
	_require_receipts()

	receipt = _receipt_row(args)
	replace = bool(as_bool(args, "replace", False))
	tolerance = _amount_tolerance(args)
	window = _date_window(args)

	target = as_str(args, "bank_transaction") or as_str(args, "transaction")
	if not target:
		return _match_candidates(receipt, args, tolerance=tolerance, window=window)

	_require_receipt_link_field()
	transaction = _transaction_row(target)

	# THE DIRECTION IS A HARD REFUSAL AND THE SCORE IS NOT. An amount two cents
	# out or a date eight days late is a judgement a person is allowed to
	# overrule; money arriving is not an expense under any judgement, and a
	# receipt filed against a deposit would net a cost against a payment.
	if transaction.get("direction") != "Withdrawal":
		raise ToolError(
			f"transaction {transaction['name']} is money IN ({transaction['amount_signed']}), and "
			"an Expense Receipt is money out. A receipt filed against a deposit would net a cost "
			"against a payment received. If the receipt is actually a refund, it is a credit note "
			"rather than an expense receipt. Nothing was written."
		)
	if str(transaction.get("company") or "") != str(receipt.get("company") or ""):
		raise ToolError(
			f"receipt {receipt['name']} belongs to {receipt.get('company')!r} and transaction "
			f"{transaction['name']} to {transaction.get('company')!r}. One company's paper cannot "
			"be the evidence for another company's bank line. Nothing was written."
		)
	if str(receipt.get("status") or "") == expenses.REJECTED:
		raise ToolError(
			f"receipt {receipt['name']} was rejected, and a rejected receipt is not evidence of "
			"anything. If the rejection was wrong, that is the decision to revisit. Nothing was "
			"written."
		)

	existing_link = receipt.get(RECEIPT_LINK_FIELD)
	if existing_link and existing_link != transaction["name"] and not replace:
		raise ToolError(
			f"receipt {receipt['name']} is already matched to {existing_link}. Two bank lines for "
			"one slip means one of them has no paper behind it. Pass replace=true if this one is "
			"the right line — the result will name what was unlinked. Nothing was written."
		)

	other = _other_receipts_on(transaction["name"], receipt["name"])
	if other:
		raise ToolError(
			f"transaction {transaction['name']} already has receipt(s) {', '.join(other)} matched "
			"to it. One withdrawal is one purchase; a second slip against it is either a duplicate "
			"receipt or the wrong line. Unmatch the other one first. Nothing was written."
		)

	score = score_match(receipt, transaction, tolerance=tolerance, window=window)

	# Filtered through `has_field` because the four columns arrived together in
	# v0.71.0 and a half-migrated bench would otherwise take a write against a
	# column it does not have. The link itself is already required above.
	payload = {
		RECEIPT_LINK_FIELD: transaction["name"],
		RECEIPT_METHOD_FIELD: _method(args),
		RECEIPT_CONFIDENCE_FIELD: score["confidence"] if score["eligible"] else 0.0,
		RECEIPT_MATCHED_ON_FIELD: today(),
	}
	frappe.db.set_value(
		EXPENSE_RECEIPT,
		receipt["name"],
		{key: value for key, value in payload.items() if compat.has_field(EXPENSE_RECEIPT, key)},
	)

	data = {
		"expense_receipt": receipt["name"],
		"bank_transaction": transaction["name"],
		"linked": True,
		"replaced": existing_link if existing_link and existing_link != transaction["name"] else None,
		"match_method": _method(args),
		"confidence": score["confidence"],
		"eligible": score["eligible"],
		"blockers": score["blockers"],
		"signals": score["signals"],
		"receipt": _receipt_out({**receipt, RECEIPT_LINK_FIELD: transaction["name"]}),
		"transaction": _transaction_out(transaction),
		"note": (
			"The link is EVIDENCE, not a posting. Nothing has been booked and no allocation has "
			"been made — reconcile_bank_transaction is what settles a transaction against a "
			"Payment Entry or a Journal Entry, and it is a separate tool with a separate switch."
		),
	}
	if not score["eligible"]:
		data["warning"] = (
			"This link was made against the scoring rules, which is allowed — a person naming both "
			"documents outranks an algorithm. What the score objected to: "
			+ "; ".join(score["blockers"])
			+ ". The stored confidence is 0.0 so the pair shows up in any later review."
		)

	return ToolResult(
		data,
		f"matched receipt {receipt['name']} to {transaction['name']} (confidence {data['confidence']})",
		docstatus_delta="none (a link, not a posting)",
	)


def _method(args: dict) -> str:
	"""How this link came to be — a person, or a proposal a person accepted."""
	value = as_str(args, "match_method") or MATCH_MANUAL
	if value not in MATCH_METHODS:
		raise ToolError(f"match_method must be one of: {', '.join(MATCH_METHODS)} — got {value!r}.")
	return value


def _match_candidates(receipt: dict, args: dict, *, tolerance: float, window: int) -> ToolResult:
	"""Every eligible transaction for one receipt, best first. Writes nothing."""
	limit = as_limit(args)
	candidates = _rank_candidates(receipt, args, tolerance=tolerance, window=window)
	eligible = [row for row in candidates if row["eligible"]][:limit]

	data = {
		"expense_receipt": receipt["name"],
		"receipt": _receipt_out(receipt),
		"linked": False,
		"candidates": eligible,
		"count": len(eligible),
		"scanned": len(candidates),
		"amount_tolerance": tolerance,
		"date_window_days": window,
		"next_step": (
			"Call this tool again with bank_transaction set to the one you picked. Nothing has been written."
			if eligible
			else (
				"No transaction on this site is within the amount tolerance and the date window. "
				"Widen date_window_days or amount_tolerance, check the receipt's own amount and "
				"date, or accept that the charge has not landed on a statement yet."
			)
		),
	}
	if receipt.get(RECEIPT_LINK_FIELD):
		data["already_matched_to"] = receipt.get(RECEIPT_LINK_FIELD)
	return ToolResult(
		data, f"{len(eligible)} candidate transaction(s) for receipt {receipt['name']} — nothing written"
	)


def _rank_candidates(receipt: dict, args: dict, *, tolerance: float, window: int) -> list:
	"""Score the transactions that could possibly be this receipt, best first.

	The date range is pushed into SQL rather than scored in Python: a receipt is
	compared against the fortnight around it, not against the site.
	"""
	receipt_date = _as_date(receipt.get("receipt_date"))
	filters = {"docstatus": ("<", 2)}
	if receipt.get("company"):
		filters["company"] = receipt["company"]
	bank_account = _bank_account(args)
	if bank_account:
		filters["bank_account"] = bank_account
	if receipt_date:
		filters["date"] = (
			"between",
			[
				str(receipt_date - datetime.timedelta(days=BACKDATE_TOLERANCE_DAYS)),
				str(receipt_date + datetime.timedelta(days=window)),
			],
		)

	rows = _transaction_rows(filters)
	taken = _receipts_by_transaction([row["name"] for row in rows])
	scored = []
	for row in rows:
		others = [entry["name"] for entry in taken.get(row["name"], []) if entry["name"] != receipt["name"]]
		score = score_match(receipt, row, tolerance=tolerance, window=window)
		if others:
			score["eligible"] = False
			score["confidence"] = 0.0
			score["blockers"] = [
				*list(score["blockers"]),
				f"already matched to receipt(s) {', '.join(others)}",
			]
		score["transaction"] = _transaction_out(row)
		scored.append(score)
	scored.sort(key=lambda row: (-row["confidence"], str(row["bank_transaction"])))
	return scored


def _receipt_row(args: dict) -> dict:
	"""The Expense Receipt this call is about, read once."""
	name = as_str(args, "expense_receipt") or as_str(args, "receipt") or as_str(args, "name")
	if not name:
		raise ToolError("expense_receipt is required — which slip is this about?")
	fields = compat.existing_fields(
		EXPENSE_RECEIPT, [*list(_RECEIPT_FIELDS), RECEIPT_LINK_FIELD, RECEIPT_CONFIDENCE_FIELD]
	)
	row = frappe.db.get_value(EXPENSE_RECEIPT, name, fields, as_dict=True)
	if not row:
		raise ToolError(f"no Expense Receipt named {name!r} on this site.")
	return dict(row)


def _transaction_row(name: str) -> dict:
	rows = _transaction_rows({"name": name}, limit=1)
	if not rows:
		raise ToolError(f"no Bank Transaction named {name!r} on this site.")
	return rows[0]


def _other_receipts_on(transaction: str, this_receipt: str) -> list:
	return sorted(
		entry["name"]
		for entry in _receipts_by_transaction([transaction]).get(transaction, [])
		if entry["name"] != this_receipt
	)


# ── 2. auto_match_receipts ───────────────────────────────────────────────────


def auto_match_receipts(args: dict) -> ToolResult:
	"""Score every unmatched receipt against every unmatched withdrawal. Writes NOTHING.

	This is the batch half of the matching pair and it is a READ tool, which is
	the most important sentence in this module. See the module docstring for why:
	a wrong receipt-to-bank link is invisible after the fact, so a person accepts
	each one and the record says a machine proposed it.

	CONTESTED PROPOSALS ARE REPORTED, NOT RESOLVED. Two receipts for $47.83 at
	the same station on the same day genuinely happen — two trucks, two drivers —
	and the algorithm has no way to tell which slip is which withdrawal. When
	more than one receipt's best match is the same transaction, the highest
	scorer is proposed and the others come back under `contested` with the
	transaction named. Dropping them silently would leave a bookkeeper believing
	those receipts had no bank line at all.
	"""
	_require_bank_transaction()
	_require_receipts()

	tolerance = _amount_tolerance(args)
	window = _date_window(args)
	threshold = _min_confidence(args)
	limit = as_limit(args)

	receipt_rows = _unmatched_receipt_rows(args)
	transaction_rows = _unmatched_transaction_rows(args, direction="Withdrawal")
	by_name = {row["name"]: row for row in transaction_rows}

	proposals, contested = [], []
	best_by_transaction = {}
	for receipt in receipt_rows:
		best = None
		for transaction in transaction_rows:
			score = score_match(receipt, transaction, tolerance=tolerance, window=window)
			if not score["eligible"] or score["confidence"] < threshold:
				continue
			if best is None or score["confidence"] > best["confidence"]:
				best = score
		if best is None:
			continue
		held = best_by_transaction.get(best["bank_transaction"])
		if held is None or best["confidence"] > held["confidence"]:
			if held is not None:
				contested.append(held)
			best_by_transaction[best["bank_transaction"]] = best
		else:
			contested.append(best)

	for score in sorted(
		best_by_transaction.values(), key=lambda row: (-row["confidence"], str(row["expense_receipt"]))
	)[:limit]:
		proposals.append(_proposal(score, by_name))

	data = {
		"proposals": proposals,
		"count": len(proposals),
		"contested": [_proposal(score, by_name, contested=True) for score in contested],
		"unmatched_receipts_scanned": len(receipt_rows),
		"unmatched_transactions_scanned": len(transaction_rows),
		"receipts_with_no_candidate": sorted(
			set(row["name"] for row in receipt_rows)
			- set(score["expense_receipt"] for score in best_by_transaction.values())
			- set(score["expense_receipt"] for score in contested)
		)[:limit],
		"settings": {
			"min_confidence": threshold,
			"amount_tolerance": tolerance,
			"date_window_days": window,
		},
		"committed": False,
		"note": (
			"NOTHING WAS WRITTEN. Each proposal carries the exact "
			"match_receipt_to_bank_transaction call that would commit it. A wrong link between a "
			"slip and a withdrawal is invisible afterwards — both documents exist and both amounts "
			"are right — so each one is a person's decision."
		),
	}
	if contested:
		data["warning"] = (
			f"{len(contested)} receipt(s) lost a transaction to a higher-scoring receipt. Two slips "
			"for the same amount on the same day at the same vendor are a real thing on a farm with "
			"two trucks, and no algorithm can say which is which. They are listed rather than "
			"dropped."
		)

	return ToolResult(
		data,
		f"{len(proposals)} proposed match(es) from {len(receipt_rows)} unmatched receipt(s) "
		f"against {len(transaction_rows)} unmatched transaction(s) — nothing written",
	)


def _proposal(score: dict, by_name: dict, *, contested: bool = False) -> dict:
	row = dict(score)
	transaction = by_name.get(score["bank_transaction"])
	if transaction:
		row["transaction"] = _transaction_out(transaction)
	row.pop("blockers", None)
	row["contested"] = contested
	row["commit_with"] = {
		"tool": "match_receipt_to_bank_transaction",
		"arguments": {
			"expense_receipt": score["expense_receipt"],
			"bank_transaction": score["bank_transaction"],
			"match_method": MATCH_PROPOSED,
		},
	}
	return row


def _unmatched_receipt_rows(args: dict) -> list:
	"""Receipts with no bank transaction against them, within the caller's scope."""
	filters = {}
	company = resolve_company(as_str(args, "company"))
	if company:
		filters["company"] = company
	if _receipt_can_link():
		# `("is", "not set")` rather than `("in", ["", None])`: SQL's IN never
		# matches NULL, so the obvious filter would silently return only the
		# receipts whose link is the empty string and miss every one that has
		# never been touched.
		filters[RECEIPT_LINK_FIELD] = ("is", "not set")
	status = as_str(args, "status")
	if status:
		if status not in expenses.STATUSES:
			raise ToolError(f"status must be one of: {', '.join(expenses.STATUSES)}.")
		filters["status"] = status
	else:
		# A rejected receipt is not evidence of anything, so it is never a
		# matching candidate unless a caller asks for that status by name.
		filters["status"] = ("!=", expenses.REJECTED)
	category = as_str(args, "category")
	if category:
		filters["category"] = category
	_apply_date_range(filters, "receipt_date", as_date(args, "from_date"), as_date(args, "to_date"))

	minimum = args.get("min_amount")
	maximum = args.get("max_amount")
	if minimum not in (None, "") and maximum not in (None, ""):
		filters["amount"] = ("between", [as_float(minimum, "min_amount"), as_float(maximum, "max_amount")])
	elif minimum not in (None, ""):
		filters["amount"] = (">=", as_float(minimum, "min_amount"))
	elif maximum not in (None, ""):
		filters["amount"] = ("<=", as_float(maximum, "max_amount"))

	fields = compat.existing_fields(EXPENSE_RECEIPT, [*list(_RECEIPT_FIELDS), RECEIPT_LINK_FIELD])
	rows = frappe.db.get_all(
		EXPENSE_RECEIPT,
		filters=filters,
		fields=fields,
		order_by="receipt_date asc, name asc",
		limit=MAX_SCAN,
	)
	return [dict(row) for row in rows]


def _unmatched_transaction_rows(args: dict, *, direction: str = "") -> list:
	"""Transactions with no receipt against them, within the caller's scope."""
	filters = {"docstatus": ("<", 2)}
	company = resolve_company(as_str(args, "company"))
	if company:
		filters["company"] = company
	bank_account = _bank_account(args)
	if bank_account:
		filters["bank_account"] = bank_account
	_apply_date_range(filters, "date", as_date(args, "from_date"), as_date(args, "to_date"))

	rows = _transaction_rows(filters)
	taken = _receipts_by_transaction([row["name"] for row in rows])
	out = []
	for row in rows:
		if taken.get(row["name"]):
			continue
		if direction and row["direction"] != direction:
			continue
		out.append(row)
	return out


# ── 3. get_bank_reconciliation_status ────────────────────────────────────────


def get_bank_reconciliation_status(args: dict) -> ToolResult:
	"""Three reconciliation questions for one account and period, answered separately.

	Ledger allocation, receipt evidence and categorisation are three different
	states a transaction can be in, and a transaction can be in any combination
	of them. A dashboard number that merged them would be wrong for whichever one
	the reader meant — the commonest real case being a fully reconciled statement
	where a third of the withdrawals have no paper behind them.

	Amounts are gross (absolute) throughout, split by direction, because "$40,000
	unmatched" means nothing without knowing which way it went.
	"""
	_require_bank_transaction()
	bank_account = _bank_account(args)
	company = resolve_company(as_str(args, "company"))
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")

	filters = {"docstatus": ("<", 2)}
	if bank_account:
		filters["bank_account"] = bank_account
	if company:
		filters["company"] = company
	_apply_date_range(filters, "date", from_date, to_date)

	rows = _transaction_rows(filters)
	truncated = len(rows) == MAX_SCAN
	taken = _receipts_by_transaction([row["name"] for row in rows])
	categorised_ready = _categorization_fields_present()

	totals = {"count": len(rows), "deposits": 0.0, "withdrawals": 0.0}
	ledger = _empty_bucket()
	evidence = _empty_bucket()
	categorisation = _empty_bucket()
	by_category = {}
	receipts_linked = 0

	for row in rows:
		gross = row["gross_amount"]
		if row["direction"] == "Deposit":
			totals["deposits"] += gross
		else:
			totals["withdrawals"] += gross

		state = _allocation_state(row)
		_bump(ledger, state["fully_allocated"], gross)

		matched_receipts = taken.get(row["name"]) or []
		receipts_linked += len(matched_receipts)
		_bump(evidence, bool(matched_receipts), gross)

		category = (row.get(CATEGORY_FIELD) or "").strip() if categorised_ready else ""
		_bump(categorisation, bool(category), gross)
		if category:
			bucket = by_category.setdefault(category, {"count": 0, "amount": 0.0})
			bucket["count"] += 1
			bucket["amount"] = round(bucket["amount"] + gross, 2)

	totals["deposits"] = round(totals["deposits"], 2)
	totals["withdrawals"] = round(totals["withdrawals"], 2)
	totals["net"] = round(totals["deposits"] - totals["withdrawals"], 2)

	data = {
		"bank_account": bank_account or None,
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"transactions": totals,
		"ledger_allocation": {
			**_finish(ledger),
			"question": "Is this transaction allocated against a Payment Entry or a Journal Entry?",
			"tool": "reconcile_bank_transaction",
		},
		"receipt_evidence": {
			**_finish(evidence),
			"receipts_linked": receipts_linked,
			"question": "Is there a receipt on file that is the paper for this transaction?",
			"tool": "auto_match_receipts",
		},
		"categorization": {
			**_finish(categorisation),
			"by_category": dict(sorted(by_category.items())),
			"question": "Does anybody know what KIND of expense this was?",
			"tool": "apply_categorization_rules",
			"fields_installed": categorised_ready,
		},
		"scanned": len(rows),
		"truncated": truncated,
		"note": (
			"The three sections are independent states, not stages. A transaction can be allocated "
			"to a Payment Entry and still have no receipt behind it — which is the case an audit "
			"asks about — and one with a receipt can be uncategorised and still perfectly "
			"reconciled. They are never added together."
		),
	}
	if not categorised_ready:
		data["categorization"]["why"] = (
			"This site's Bank Transaction has no farm_category column yet, so every transaction "
			"counts as uncategorised. apply_categorization_rules or seed_farm_categorization_rules "
			"creates it on first use; `bench migrate` does it at install."
		)
	if truncated:
		data["warning"] = (
			f"Stopped at {MAX_SCAN} transactions. Every count and total above is for the rows read, "
			"not for the period — narrow it with from_date/to_date or bank_account."
		)

	return ToolResult(
		data,
		f"{len(rows)} transaction(s): {ledger['matched']} allocated, {evidence['matched']} with a "
		f"receipt, {categorisation['matched']} categorised",
	)


def _empty_bucket() -> dict:
	return {"matched": 0, "unmatched": 0, "matched_amount": 0.0, "unmatched_amount": 0.0}


def _bump(bucket: dict, matched: bool, amount: float) -> None:
	if matched:
		bucket["matched"] += 1
		bucket["matched_amount"] = round(bucket["matched_amount"] + amount, 2)
	else:
		bucket["unmatched"] += 1
		bucket["unmatched_amount"] = round(bucket["unmatched_amount"] + amount, 2)


def _finish(bucket: dict) -> dict:
	total = bucket["matched"] + bucket["unmatched"]
	return {
		**bucket,
		"total": total,
		"matched_pct": round(100.0 * bucket["matched"] / total, 2) if total else None,
	}


# ── 4. list_unmatched_receipts ───────────────────────────────────────────────


def list_unmatched_receipts(args: dict) -> ToolResult:
	"""Receipts with no bank transaction against them — the evidence worklist.

	Oldest first, because the receipt that has been waiting longest is the one
	whose charge is most likely never to have landed at all — a card declined, a
	slip photographed twice, an expense somebody paid in cash.
	"""
	_require_receipts()
	rows = _unmatched_receipt_rows(args)
	limit = as_limit(args)
	shown = rows[:limit]
	total = round(sum(_money(row.get("amount")) for row in shown), 2)
	by_category = {}
	for row in shown:
		bucket = by_category.setdefault(row.get("category") or "Uncategorised", {"count": 0, "amount": 0.0})
		bucket["count"] += 1
		bucket["amount"] = round(bucket["amount"] + _money(row.get("amount")), 2)

	data = {
		"receipts": [_receipt_out(row) for row in shown],
		"count": len(shown),
		"total_amount": total,
		"by_category": dict(sorted(by_category.items())),
		"scanned": len(rows),
		"truncated": len(rows) > limit,
		"link_field_present": _receipt_can_link(),
	}
	if not _receipt_can_link():
		data["warning"] = (
			f"This site's Expense Receipt has no {RECEIPT_LINK_FIELD!r} column, so EVERY receipt is "
			"listed here — none of them can record a match yet. Run `bench migrate`."
		)
	return ToolResult(data, f"{len(shown)} unmatched receipt(s) totalling {total}")


# ── 5. list_unmatched_bank_transactions ──────────────────────────────────────


def list_unmatched_bank_transactions(args: dict) -> ToolResult:
	"""Statement lines with nothing behind them, and WHICH kind of nothing.

	"Unmatched" means two different things — no allocation in the ledger, and no
	receipt on file — and a transaction can be either, both, or neither. Each row
	carries `unmatched_reasons` saying which, and the default is the union: a
	line missing either one is worth a person's time.

	`require` narrows it: `receipt` for the audit question (what did we spend
	this on and where is the paper), `allocation` for the bookkeeping one (what
	is stopping this statement from tying out).
	"""
	_require_bank_transaction()
	require = (as_str(args, "require") or "any").strip().lower()
	if require not in ("any", "receipt", "allocation", "both"):
		raise ToolError("require must be one of: any, receipt, allocation, both.")

	direction = as_str(args, "direction")
	if direction and direction not in ("Deposit", "Withdrawal"):
		raise ToolError("direction must be 'Deposit' or 'Withdrawal'.")

	filters = {"docstatus": ("<", 2)}
	company = resolve_company(as_str(args, "company"))
	if company:
		filters["company"] = company
	bank_account = _bank_account(args)
	if bank_account:
		filters["bank_account"] = bank_account
	_apply_date_range(filters, "date", as_date(args, "from_date"), as_date(args, "to_date"))

	rows = _transaction_rows(filters)
	taken = _receipts_by_transaction([row["name"] for row in rows])
	minimum = args.get("min_amount")
	maximum = args.get("max_amount")
	floor = as_float(minimum, "min_amount") if minimum not in (None, "") else None
	ceiling = as_float(maximum, "max_amount") if maximum not in (None, "") else None

	limit = as_limit(args)
	out, scanned = [], 0
	for row in rows:
		if direction and row["direction"] != direction:
			continue
		if floor is not None and row["gross_amount"] < floor:
			continue
		if ceiling is not None and row["gross_amount"] > ceiling:
			continue

		state = _allocation_state(row)
		has_receipt = bool(taken.get(row["name"]))
		reasons = []
		if not state["fully_allocated"]:
			reasons.append("no allocation in the ledger")
		if not has_receipt:
			reasons.append("no receipt on file")
		if not reasons:
			continue
		if require == "receipt" and has_receipt:
			continue
		if require == "allocation" and state["fully_allocated"]:
			continue
		if require == "both" and len(reasons) < 2:
			continue

		scanned += 1
		if len(out) >= limit:
			continue
		out.append(
			{
				**_transaction_out(row),
				"unmatched_reasons": reasons,
				"unallocated_amount_effective": state["unallocated"],
				"allocated_amount_effective": state["allocated"],
				"has_receipt": has_receipt,
			}
		)

	data = {
		"bank_transactions": out,
		"count": len(out),
		"matching": scanned,
		"truncated": scanned > len(out),
		"total_unmatched_amount": round(sum(row["gross_amount"] for row in out), 2),
		"require": require,
		"filters": {
			"company": company,
			"bank_account": bank_account or None,
			"direction": direction or None,
			"min_amount": floor,
			"max_amount": ceiling,
		},
		"note": (
			"'no allocation in the ledger' is a bookkeeping gap and 'no receipt on file' is an "
			"evidence gap. They are unrelated: a transaction settled against a Payment Entry can "
			"still have no paper, and that is the pair an audit asks about."
		),
	}
	return ToolResult(data, f"{len(out)} unmatched transaction(s) of {scanned} matching")


# ── 6. create_bank_categorization_rule ───────────────────────────────────────


def create_bank_categorization_rule(args: dict) -> ToolResult:
	"""One rule: when a statement line looks like THIS, it is THAT.

	The account is OPTIONAL and never guessed. A rule with a category and no
	account still sorts a statement, which is most of the value; picking a leaf
	expense account by name on the operator's behalf would put a season of
	spraying somewhere nobody chose. When one is given it is checked against the
	company, against being a group, and against being disabled — all three
	before anything is written.

	OVERLAP IS REPORTED, NOT REFUSED. Rules are meant to overlap: `CHEVRON` at
	priority 10 and `FUEL` at priority 100 is the intended shape, first match
	wins. What the result names is which existing rules also match this pattern
	and which of them would win, so an operator finds out here rather than from a
	rule that never fires.
	"""
	_require_rules()
	company = resolve_company(as_str(args, "company"), required=True)
	rule_name = as_str(args, "rule_name", required=True)
	category = as_str(args, "category", required=True)
	pattern = as_str(args, "pattern", required=True)

	match_field = as_str(args, "match_field") or "description"
	match_type = as_str(args, "match_type") or "contains"
	direction = as_str(args, "direction") or "Any"
	priority = as_int(args, "priority", 100)

	duplicate = frappe.db.get_value(RULE, {"rule_name": rule_name, "company": company}, "name")
	if duplicate:
		raise ToolError(
			f"{company} already has a rule called {rule_name!r} ({duplicate}). Two rules with one "
			"name are indistinguishable in the report that says which rule categorised a "
			"transaction. Nothing was created."
		)

	account = _rule_account(args, company)
	cost_center = as_str(args, "cost_center")
	party_type, party = _rule_party(args)

	doc = frappe.new_doc(RULE)
	doc.rule_name = rule_name
	doc.company = company
	doc.category = category
	doc.pattern = pattern
	doc.match_field = match_field
	doc.match_type = match_type
	doc.direction = direction
	doc.priority = 100 if priority is None else int(priority)
	doc.enabled = 1 if as_bool(args, "enabled", True) else 0
	if account:
		doc.account = account
	if cost_center:
		doc.cost_center = cost_center
	if party_type:
		doc.party_type = party_type
		doc.party = party
	for key in ("amount_min", "amount_max"):
		value = args.get(key)
		if value not in (None, ""):
			doc.set(key, as_float(value, key))
	notes = as_str(args, "notes")
	if notes:
		doc.notes = notes
	doc.insert()

	overlapping = _overlapping_rules(doc)
	data = {
		"name": doc.name,
		"rule": _rule_out(_rule_row(doc.name)),
		"overlaps": overlapping,
		"note": (
			"Rules are tried in priority order and the FIRST match wins. Nothing has been "
			"categorised by creating this — apply_categorization_rules is what runs it, and it only "
			"touches transactions that have no category yet."
		),
	}
	if not account:
		data["account_note"] = (
			"No expense account is set, so this rule sorts a statement but cannot tell a Journal "
			"Entry where to post. That is deliberate: the account is never guessed. Add one with "
			"the Desk or by recreating the rule when you have chosen it."
		)
	if overlapping:
		data["warning"] = (
			f"{len(overlapping)} existing rule(s) also match this pattern. That is normal and is "
			"what priority is for — read `wins` on each to see which one a transaction matching "
			"both would get."
		)

	return ToolResult(
		data,
		f"created categorization rule {doc.name} ({rule_name}) — {match_type} {pattern!r} → {category}",
		docstatus_delta="none → 0 (created)",
	)


def _rule_account(args: dict, company: str) -> str:
	requested = as_str(args, "account") or as_str(args, "expense_account")
	if not requested:
		return ""
	docname = resolve_account(requested, company)
	row = (
		frappe.db.get_value(
			"Account",
			docname,
			compat.existing_fields("Account", ("name", "company", "is_group", "disabled", "root_type")),
			as_dict=True,
		)
		or {}
	)
	if str(row.get("company") or "") != company:
		raise ToolError(
			f"account {docname!r} belongs to {row.get('company')!r}, not {company!r}. A rule cannot "
			"book one company's transactions into another's ledger. Nothing was created."
		)
	if int(row.get("is_group") or 0):
		raise ToolError(
			f"{docname!r} is a group account, and ERPNext posts only to leaf accounts. Nothing was created."
		)
	if int(row.get("disabled") or 0):
		raise ToolError(f"{docname!r} is disabled, so nothing can post to it. Nothing was created.")
	return docname


def _rule_party(args: dict) -> tuple:
	party_type = as_str(args, "party_type")
	party = as_str(args, "party")
	if not party_type and not party:
		return "", ""
	if not party_type or not party:
		raise ToolError(
			"party and party_type go together: party_type is the DocType ('Supplier'), party is the "
			"record. Nothing was created."
		)
	if not compat.doctype_exists(party_type):
		raise ToolError(f"no DocType named {party_type!r} on this site. Nothing was created.")
	if not frappe.db.exists(party_type, party):
		raise ToolError(f"no {party_type} named {party!r} on this site. Nothing was created.")
	return party_type, party


def _overlapping_rules(doc) -> list:
	"""Existing rules whose pattern also matches this one's, and which would win."""
	out = []
	for row in _enabled_rules(doc.company):
		if row["name"] == doc.name:
			continue
		if row.get("match_field") != doc.match_field:
			continue
		if not (
			_rule_doc(row).matches_text(doc.pattern) or _rule_doc(doc.as_dict()).matches_text(row["pattern"])
		):
			continue
		out.append(
			{
				"name": row["name"],
				"rule_name": row.get("rule_name"),
				"pattern": row.get("pattern"),
				"category": row.get("category"),
				"priority": row.get("priority"),
				"wins": "this rule"
				if int(doc.priority or 0) < int(row.get("priority") or 0)
				else row["name"],
			}
		)
	return out


# ── 7. list_bank_categorization_rules ────────────────────────────────────────


def list_bank_categorization_rules(args: dict) -> ToolResult:
	"""The book of rules, in the order they are tried.

	Priority order rather than alphabetical, deliberately: the list IS the
	evaluation order, and reading it top to bottom is how somebody works out why
	a transaction got the category it did.
	"""
	_require_rules()
	filters = {}
	company = resolve_company(as_str(args, "company"))
	if company:
		filters["company"] = company
	enabled = as_bool(args, "enabled", None)
	if enabled is not None:
		filters["enabled"] = 1 if enabled else 0
	category = as_str(args, "category")
	if category:
		filters["category"] = category
	limit = as_limit(args)

	fields = compat.existing_fields(RULE, list(_RULE_FIELDS))
	rows = frappe.db.get_all(
		RULE, filters=filters, fields=fields, order_by="priority asc, rule_name asc", limit=limit
	)
	rules = [_rule_out(dict(row)) for row in rows]
	never_fired = [rule["name"] for rule in rules if rule["enabled"] and not rule["times_applied"]]
	no_account = [rule["name"] for rule in rules if not rule["account"]]

	data = {
		"rules": rules,
		"count": len(rules),
		"limit": limit,
		"truncated": len(rules) == limit,
		"enabled_count": sum(1 for rule in rules if rule["enabled"]),
		"never_fired": never_fired,
		"without_account": no_account,
		"note": (
			"Listed in evaluation order — lower priority runs first and the FIRST match wins. A rule "
			"in `never_fired` has matched nothing since it was created, which usually means an "
			"earlier rule is swallowing its transactions."
		),
	}
	return ToolResult(data, f"{len(rules)} categorization rule(s)" + (f" for {company}" if company else ""))


def _rule_row(name: str) -> dict:
	fields = compat.existing_fields(RULE, list(_RULE_FIELDS))
	row = frappe.db.get_value(RULE, name, fields, as_dict=True)
	return dict(row) if row else {}


def _rule_out(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"rule_name": row.get("rule_name"),
		"company": row.get("company"),
		"category": row.get("category"),
		"account": row.get("account") or None,
		"cost_center": row.get("cost_center") or None,
		"enabled": bool(int(row.get("enabled") or 0)),
		"priority": int(row.get("priority") or 0),
		"match_field": row.get("match_field"),
		"match_type": row.get("match_type"),
		"pattern": row.get("pattern"),
		"direction": row.get("direction"),
		"amount_min": _money(row.get("amount_min")) if row.get("amount_min") not in (None, "") else None,
		"amount_max": _money(row.get("amount_max")) if row.get("amount_max") not in (None, "") else None,
		"party_type": row.get("party_type") or None,
		"party": row.get("party") or None,
		"times_applied": int(row.get("times_applied") or 0),
		"last_applied": _date_text(row.get("last_applied")),
		"notes": row.get("notes") or None,
	}


def _enabled_rules(company: str) -> list:
	fields = compat.existing_fields(RULE, list(_RULE_FIELDS))
	rows = frappe.db.get_all(
		RULE,
		filters={"company": company, "enabled": 1},
		fields=fields,
		order_by="priority asc, name asc",
		limit=MAX_SCAN,
	)
	return [dict(row) for row in rows]


def _rule_doc(row: dict):
	"""A rule as its controller, so `matches_text` is the one implementation.

	`frappe.get_doc(dict)` builds an unsaved document of the right class without
	touching the database — the evaluator and the Desk then agree about what a
	pattern matches, which is the only way a category can be explained.
	"""
	payload = {key: value for key, value in dict(row).items() if key != "doctype"}
	payload["doctype"] = RULE
	return frappe.get_doc(payload)


# ── 8. apply_categorization_rules ────────────────────────────────────────────


def apply_categorization_rules(args: dict) -> ToolResult:
	"""Run the rules against uncategorised transactions, and say what each one did.

	MUTATING, and the one thing in this module that writes without a person
	reviewing each row. The justification is the difference between this and
	receipt matching: a rule is deterministic and inspectable, its output records
	WHICH rule produced it, and running it again after fixing the rule corrects
	the answer. A fuzzy amount-and-date match has none of those properties.

	WRITES WITH `db.set_value`, NOT `doc.save()`. A bank feed's transactions are
	submitted, and `save()` on a submitted document either refuses or takes the
	whole document through validation it does not need. The Custom Fields are
	`allow_on_submit` so a bookkeeper can also correct a category in the Desk.

	NEVER OVERWRITES A CATEGORY unless `overwrite` says so. A category somebody
	typed by hand is a decision, and a rule run that silently replaced it would
	make the hand correction pointless. With `overwrite`, transactions that had a
	category are reported separately with what it was before.

	`dry_run` runs the whole thing and writes nothing, which is the sensible
	first call after seeding a book of rules.
	"""
	_require_bank_transaction()
	_require_rules()
	company = resolve_company(as_str(args, "company"), required=True)
	dry_run = bool(as_bool(args, "dry_run", False))
	overwrite = bool(as_bool(args, "overwrite", False))

	installed = _categorization_fields_present() or ensure_categorization_fields()
	if not installed and not dry_run:
		raise ToolError(
			"this site's Bank Transaction has no farm_category column and the Custom Fields could "
			"not be created, so a categorisation has nowhere to go. Check the Error Log for the "
			"reason, or run this with dry_run=true to see what the rules WOULD do. Nothing was "
			"written."
		)

	rules = _enabled_rules(company)
	only = as_str(args, "rule")
	if only:
		rules = [row for row in rules if row["name"] == only or row.get("rule_name") == only]
		if not rules:
			raise ToolError(f"no enabled Bank Categorization Rule named {only!r} for {company}.")
	if not rules:
		raise ToolError(
			f"{company} has no enabled categorization rules, so there is nothing to apply. "
			"seed_farm_categorization_rules puts a starting book of them on the site."
		)

	filters = {"docstatus": ("<", 2), "company": company}
	bank_account = _bank_account(args)
	if bank_account:
		filters["bank_account"] = bank_account
	_apply_date_range(filters, "date", as_date(args, "from_date"), as_date(args, "to_date"))

	rows = _transaction_rows(filters)
	limit = as_limit(args)

	applied, skipped, per_rule = [], [], {}
	uncategorised = []
	for row in rows:
		existing = (row.get(CATEGORY_FIELD) or "").strip() if installed else ""
		if existing and not overwrite:
			skipped.append(
				{"bank_transaction": row["name"], "reason": f"already categorised as {existing!r}"}
			)
			continue

		hit = _first_matching_rule(rules, row)
		if hit is None:
			uncategorised.append(
				{
					**_transaction_out(row),
					"reason": "no enabled rule matched this description, direction and amount",
				}
			)
			continue
		if len(applied) >= limit:
			skipped.append({"bank_transaction": row["name"], "reason": f"limit of {limit} reached"})
			continue

		change = {
			"bank_transaction": row["name"],
			"date": _date_text(row.get("date")),
			"description": row.get("description"),
			"amount_signed": row["amount_signed"],
			"rule": hit["name"],
			"rule_name": hit.get("rule_name"),
			"category": hit.get("category"),
			"account": hit.get("account") or None,
			"cost_center": hit.get("cost_center") or None,
			"previous_category": existing or None,
			"party_set": None,
		}

		if not dry_run:
			payload = {
				CATEGORY_FIELD: hit.get("category"),
				ACCOUNT_FIELD: hit.get("account") or None,
				RULE_FIELD: hit["name"],
			}
			if hit.get("party_type") and hit.get("party") and not row.get("party"):
				payload["party_type"] = hit["party_type"]
				payload["party"] = hit["party"]
				change["party_set"] = f"{hit['party_type']}: {hit['party']}"
			frappe.db.set_value(BANK_TRANSACTION, row["name"], payload)
		elif hit.get("party_type") and hit.get("party") and not row.get("party"):
			change["party_set"] = f"{hit['party_type']}: {hit['party']} (would be set)"

		applied.append(change)
		bucket = per_rule.setdefault(
			hit["name"], {"rule_name": hit.get("rule_name"), "category": hit.get("category"), "count": 0}
		)
		bucket["count"] += 1

	if not dry_run:
		_record_rule_usage(per_rule)

	data = {
		"company": company,
		"dry_run": dry_run,
		"overwrite": overwrite,
		"rules_evaluated": len(rules),
		"transactions_scanned": len(rows),
		"categorized": applied,
		"categorized_count": len(applied),
		"per_rule": per_rule,
		"still_uncategorized": uncategorised[:limit],
		"still_uncategorized_count": len(uncategorised),
		"skipped": skipped[:limit],
		"skipped_count": len(skipped),
		"fields_installed": installed,
		"note": (
			"Categorising says what a transaction WAS. It posts nothing: no GL Entry, no Journal "
			"Entry, no allocation. Turning a categorised statement into postings is "
			"create_journal_entry, which is a separate tool with a separate switch and its own "
			"review."
		),
	}
	if uncategorised:
		data["next_step"] = (
			f"{len(uncategorised)} transaction(s) matched no rule. Read their descriptions in "
			"`still_uncategorized` — each one is either a new rule worth writing or a genuinely "
			"one-off payment that belongs in a Journal Entry by hand."
		)
	if dry_run:
		data["warning"] = "dry_run=true — NOTHING was written. Run again without it to apply."

	return ToolResult(
		data,
		("would categorise" if dry_run else "categorised")
		+ f" {len(applied)} of {len(rows)} transaction(s) for {company} using {len(rules)} rule(s)",
		docstatus_delta="none (fields on existing transactions)" if not dry_run else "",
	)


def _first_matching_rule(rules: list, row: dict):
	"""The first rule in priority order that matches this transaction, or None."""
	for rule in rules:
		direction = rule.get("direction") or "Any"
		if direction != "Any" and direction != row["direction"]:
			continue
		gross = row["gross_amount"]
		if rule.get("amount_min") not in (None, "") and gross < float(rule["amount_min"]):
			continue
		if rule.get("amount_max") not in (None, "") and gross > float(rule["amount_max"]):
			continue
		text = row.get(rule.get("match_field") or "description") or ""
		if _rule_doc(rule).matches_text(text):
			return rule
	return None


def _record_rule_usage(per_rule: dict) -> None:
	"""Bump `times_applied` and stamp `last_applied` on the rules that fired.

	Written with `db.set_value` rather than a save so that counting a rule's use
	never re-runs its validation — a rule pointed at an account somebody has
	since disabled must not become unable to record that it fired.
	"""
	stamp = today()
	for name, bucket in per_rule.items():
		try:
			current = int(frappe.db.get_value(RULE, name, "times_applied") or 0)
			frappe.db.set_value(
				RULE, name, {"times_applied": current + bucket["count"], "last_applied": stamp}
			)
		except Exception:
			continue


# ── 9. get_cash_flow_summary ─────────────────────────────────────────────────


def get_cash_flow_summary(args: dict) -> ToolResult:
	"""What came in, what went out, and on which basis each number is true.

	    THE ONE THING THIS TOOL REFUSES TO DO IS ADD THEM UP INTO ONE NUMBER. A
	    settlement statement, a sales invoice and a deposit on the bank statement can
	    all be the same money, arriving three times in three doctypes at three
	    different moments. A summary that summed them would triple a season's
	    revenue, and it would look completely reasonable.

	    So the answer is in two blocks that are never combined:

	  CASH — the bank statement itself. Deposits, withdrawals, net. This is the
	  only section that is money that actually moved, and `net_cash` is the only
	  total in the response.

	  COMMITMENTS — the documents. Payments made and received, invoices out and
	  in, settlements, payroll, receipts. Each says its doctype, its count, its
	  amount and its basis, and each is a different question about the same
	  season.

	`by_category` DEDUPLICATES ACROSS THE TWO, which is the one place they touch:
	an expense receipt matched to a bank withdrawal is one purchase with two
	records, so the withdrawal is dropped from the category totals and the
	receipt is kept — the receipt is the one with the better category. That is
	the reconciliation this whole sprint builds, spending itself on the one
	number where the double count would otherwise be invisible.
	"""
	company = resolve_company(as_str(args, "company"), required=True)
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")

	cash = _cash_block(company, from_date, to_date, args)
	inflows = {
		"payments_received": _payment_block(company, from_date, to_date, "Receive"),
		"sales_invoices": _invoice_block(SALES_INVOICE, company, from_date, to_date),
		"settlements": _settlement_block(company, from_date, to_date),
	}
	outflows = {
		"payments_made": _payment_block(company, from_date, to_date, "Pay"),
		"purchase_invoices": _invoice_block(PURCHASE_INVOICE, company, from_date, to_date),
		"expense_receipts": _receipt_block(company, from_date, to_date),
		"payroll": _payroll_block(company, from_date, to_date),
	}

	data = {
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"cash": cash,
		"inflows": inflows,
		"outflows": outflows,
		"by_category": _category_block(company, from_date, to_date, args),
		"basis_note": (
			"`cash` is the bank statement and is the only section where the money has moved. "
			"Everything under `inflows` and `outflows` is a DOCUMENT — a settlement, an invoice, a "
			"payment, a receipt — and the same money appears in several of them at different "
			"moments. They are reported side by side and never summed."
		),
	}
	if not cash["available"]:
		data["warning"] = (
			"There are no bank transactions in this period, so there is no cash picture — only the "
			"documents. Import or sync the statement before reading this as cash flow."
		)
	return ToolResult(
		data,
		f"{company}: net cash {cash.get('net')} over {from_date or 'the beginning'} to {to_date or 'today'}",
	)


def _cash_block(company: str, from_date, to_date, args: dict) -> dict:
	if not compat.doctype_exists(BANK_TRANSACTION):
		return {"available": False, "why": f"this site has no {BANK_TRANSACTION} doctype"}
	filters = {"docstatus": ("<", 2), "company": company}
	bank_account = _bank_account(args)
	if bank_account:
		filters["bank_account"] = bank_account
	_apply_date_range(filters, "date", from_date, to_date)
	rows = _transaction_rows(filters)
	deposits = round(sum(row["gross_amount"] for row in rows if row["direction"] == "Deposit"), 2)
	withdrawals = round(sum(row["gross_amount"] for row in rows if row["direction"] == "Withdrawal"), 2)
	return {
		"available": bool(rows),
		"doctype": BANK_TRANSACTION,
		"basis": "cash",
		"bank_account": bank_account or None,
		"count": len(rows),
		"deposits": deposits,
		"withdrawals": withdrawals,
		"net": round(deposits - withdrawals, 2),
		"truncated": len(rows) == MAX_SCAN,
	}


def _payment_block(company: str, from_date, to_date, payment_type: str) -> dict:
	if not compat.doctype_exists(PAYMENT_ENTRY):
		return {"available": False, "why": f"this site has no {PAYMENT_ENTRY} doctype"}
	filters = {"docstatus": 1, "company": company, "payment_type": payment_type}
	_apply_date_range(filters, "posting_date", from_date, to_date)
	fields = compat.existing_fields(
		PAYMENT_ENTRY, ("name", "paid_amount", "received_amount", "party_type", "party")
	)
	rows = frappe.db.get_all(PAYMENT_ENTRY, filters=filters, fields=fields, limit=MAX_SCAN)
	amount_field = "received_amount" if payment_type == "Receive" else "paid_amount"
	total = round(sum(_money(row.get(amount_field) or row.get("paid_amount")) for row in rows), 2)
	by_party = {}
	for row in rows:
		key = row.get("party") or "(no party)"
		bucket = by_party.setdefault(key, {"count": 0, "amount": 0.0})
		bucket["count"] += 1
		bucket["amount"] = round(
			bucket["amount"] + _money(row.get(amount_field) or row.get("paid_amount")), 2
		)
	return {
		"available": True,
		"doctype": PAYMENT_ENTRY,
		"basis": "cash (submitted payment entries)",
		"count": len(rows),
		"amount": total,
		"by_party": dict(sorted(by_party.items(), key=lambda item: -item[1]["amount"])[:20]),
	}


def _invoice_block(doctype: str, company: str, from_date, to_date) -> dict:
	if not compat.doctype_exists(doctype):
		return {"available": False, "why": f"this site has no {doctype} doctype"}
	filters = {"docstatus": 1, "company": company}
	_apply_date_range(filters, "posting_date", from_date, to_date)
	fields = compat.existing_fields(doctype, ("name", "grand_total", "outstanding_amount"))
	rows = frappe.db.get_all(doctype, filters=filters, fields=fields, limit=MAX_SCAN)
	return {
		"available": True,
		"doctype": doctype,
		"basis": "accrual (submitted invoices)",
		"count": len(rows),
		"amount": round(sum(_money(row.get("grand_total")) for row in rows), 2),
		"outstanding": round(sum(_money(row.get("outstanding_amount")) for row in rows), 2),
	}


def _settlement_block(company: str, from_date, to_date) -> dict:
	if not compat.doctype_exists(SETTLEMENT_STATEMENT):
		return {"available": False, "why": f"this site has no {SETTLEMENT_STATEMENT} doctype"}
	filters = {"company": company, "status": ("in", ["Submitted", "Posted"])}
	_apply_date_range(filters, "date", from_date, to_date)
	fields = compat.existing_fields(
		SETTLEMENT_STATEMENT,
		("name", "net_proceeds", "total_gross_revenue", "total_deductions", "status"),
	)
	rows = frappe.db.get_all(SETTLEMENT_STATEMENT, filters=filters, fields=fields, limit=MAX_SCAN)
	return {
		"available": True,
		"doctype": SETTLEMENT_STATEMENT,
		"basis": "accrual (filed settlements — the cheque may not have arrived)",
		"count": len(rows),
		"amount": round(sum(_money(row.get("net_proceeds")) for row in rows), 2),
		"gross_revenue": round(sum(_money(row.get("total_gross_revenue")) for row in rows), 2),
		"deductions": round(sum(_money(row.get("total_deductions")) for row in rows), 2),
	}


def _receipt_block(company: str, from_date, to_date) -> dict:
	if not compat.doctype_exists(EXPENSE_RECEIPT):
		return {"available": False, "why": f"this site has no {EXPENSE_RECEIPT} doctype"}
	filters = {"company": company, "status": ("!=", expenses.REJECTED)}
	_apply_date_range(filters, "receipt_date", from_date, to_date)
	fields = compat.existing_fields(EXPENSE_RECEIPT, ("name", "amount", "category"))
	rows = frappe.db.get_all(EXPENSE_RECEIPT, filters=filters, fields=fields, limit=MAX_SCAN)
	return {
		"available": True,
		"doctype": EXPENSE_RECEIPT,
		"basis": "evidence (a slip, whatever it was paid with)",
		"count": len(rows),
		"amount": round(sum(_money(row.get("amount")) for row in rows), 2),
	}


def _payroll_block(company: str, from_date, to_date) -> dict:
	if not compat.doctype_exists(PAYROLL_ENTRY):
		return {"available": False, "why": f"this site has no {PAYROLL_ENTRY} doctype"}
	filters = {"company": company}
	_apply_date_range(filters, "pay_period_end", from_date, to_date)
	fields = compat.existing_fields(
		PAYROLL_ENTRY, ("name", "total_net", "total_gross", "total_deductions", "employee_count", "status")
	)
	rows = frappe.db.get_all(PAYROLL_ENTRY, filters=filters, fields=fields, limit=MAX_SCAN)
	return {
		"available": True,
		"doctype": PAYROLL_ENTRY,
		"basis": "accrual (net pay on filed payroll runs)",
		"count": len(rows),
		"amount": round(sum(_money(row.get("total_net")) for row in rows), 2),
		"gross": round(sum(_money(row.get("total_gross")) for row in rows), 2),
		"employees": sum(int(row.get("employee_count") or 0) for row in rows),
	}


def _category_block(company: str, from_date, to_date, args: dict) -> dict:
	"""Outflow by category, with the receipt-to-bank double count removed.

	A receipt matched to a withdrawal is ONE purchase. The withdrawal is dropped
	and the receipt kept, because a receipt carries the category somebody chose
	from the paper and a bank line carries the one a pattern guessed from a memo
	field.
	"""
	buckets = {}
	matched_transactions = set()

	if compat.doctype_exists(EXPENSE_RECEIPT):
		filters = {"company": company, "status": ("!=", expenses.REJECTED)}
		_apply_date_range(filters, "receipt_date", from_date, to_date)
		fields = compat.existing_fields(EXPENSE_RECEIPT, ("name", "amount", "category", RECEIPT_LINK_FIELD))
		for row in frappe.db.get_all(EXPENSE_RECEIPT, filters=filters, fields=fields, limit=MAX_SCAN):
			key = row.get("category") or "Uncategorised"
			bucket = buckets.setdefault(key, {"amount": 0.0, "count": 0, "sources": []})
			bucket["amount"] = round(bucket["amount"] + _money(row.get("amount")), 2)
			bucket["count"] += 1
			if "expense receipts" not in bucket["sources"]:
				bucket["sources"].append("expense receipts")
			if row.get(RECEIPT_LINK_FIELD):
				matched_transactions.add(row[RECEIPT_LINK_FIELD])

	deduplicated = 0
	if _categorization_fields_present():
		filters = {"docstatus": ("<", 2), "company": company}
		bank_account = _bank_account(args)
		if bank_account:
			filters["bank_account"] = bank_account
		_apply_date_range(filters, "date", from_date, to_date)
		for row in _transaction_rows(filters):
			if row["direction"] != "Withdrawal":
				continue
			if row["name"] in matched_transactions:
				deduplicated += 1
				continue
			key = (row.get(CATEGORY_FIELD) or "").strip()
			if not key:
				continue
			bucket = buckets.setdefault(key, {"amount": 0.0, "count": 0, "sources": []})
			bucket["amount"] = round(bucket["amount"] + row["gross_amount"], 2)
			bucket["count"] += 1
			if "bank transactions" not in bucket["sources"]:
				bucket["sources"].append("bank transactions")

	return {
		"categories": dict(sorted(buckets.items(), key=lambda item: -item[1]["amount"])),
		"total": round(sum(bucket["amount"] for bucket in buckets.values()), 2),
		"deduplicated_transactions": deduplicated,
		"note": (
			f"{deduplicated} bank withdrawal(s) were dropped from these totals because a receipt "
			"matched to them is already counted. Everything this sprint builds exists so that "
			"number is right."
		),
	}


# ── 10. seed_farm_categorization_rules ───────────────────────────────────────

#: The starting book of rules, as (category, rule name, pattern, priority,
#: match type).
#:
#: WHAT IS AND IS NOT IN HERE. Patterns that are a NAME — a fuel brand, a
#: chemical distributor, an equipment dealer — plus the generic word that catches
#: the rest of that category at a higher number. Nothing here matches on an
#: amount, and nothing matches a bare transfer or an ATM withdrawal: `TRANSFER`
#: is an owner draw on one farm and a sweep between the operating and payroll
#: accounts on the next, and a seed that guessed would file a year of internal
#: movements as equity leaving the company.
#:
#: The specific rules sit at 10-40 and the generic words at 100+ so a `CHEVRON`
#: line lands in Fuel by name rather than by the word FUEL never appearing in it.
#: Every one of these is an ordinary record: edit it, disable it, delete it.
FARM_RULE_SEEDS = (
	# Fuel
	("Fuel", "Fuel — Chevron", "CHEVRON", 10, "contains"),
	("Fuel", "Fuel — Shell", "SHELL OIL", 10, "contains"),
	("Fuel", "Fuel — Pacific Pride", "PACIFIC PRIDE", 10, "contains"),
	("Fuel", "Fuel — CFN cardlock", "CFN", 12, "contains"),
	("Fuel", "Fuel — cardlock", "CARDLOCK", 20, "contains"),
	("Fuel", "Fuel — generic", "FUEL", 100, "contains"),
	("Fuel", "Fuel — diesel", "DIESEL", 100, "contains"),
	# Chemicals and spray
	("Chemicals/Spray", "Chemicals — Wilbur-Ellis", "WILBUR-ELLIS", 10, "contains"),
	("Chemicals/Spray", "Chemicals — Nutrien", "NUTRIEN", 10, "contains"),
	("Chemicals/Spray", "Chemicals — Helena", "HELENA AGRI", 10, "contains"),
	("Chemicals/Spray", "Chemicals — Simplot Grower", "SIMPLOT GROWER", 12, "contains"),
	("Chemicals/Spray", "Chemicals — Crop Production Services", "CROP PRODUCTION", 12, "contains"),
	("Chemicals/Spray", "Chemicals — generic", "CHEMICAL", 100, "contains"),
	# Equipment and parts
	("Equipment Parts", "Parts — NAPA", "NAPA AUTO", 10, "contains"),
	("Equipment Parts", "Parts — Tractor Supply", "TRACTOR SUPPLY", 10, "contains"),
	("Equipment Parts", "Parts — John Deere", "JOHN DEERE", 10, "contains"),
	("Equipment Parts", "Parts — Kubota", "KUBOTA", 10, "contains"),
	("Equipment Parts", "Parts — O'Reilly", "O'REILLY", 12, "contains"),
	("Equipment Parts", "Parts — generic", "AUTO PARTS", 100, "contains"),
	# Labor services
	("Labor Services", "Labor — farm labor contractor", "FARM LABOR", 20, "contains"),
	("Labor Services", "Labor — ag labor", "AG LABOR", 20, "contains"),
	("Labor Services", "Labor — staffing", "STAFFING", 100, "contains"),
	# Irrigation
	("Irrigation", "Irrigation — district", "IRRIGATION DISTRICT", 10, "contains"),
	("Irrigation", "Irrigation — Netafim", "NETAFIM", 10, "contains"),
	("Irrigation", "Irrigation — Jain", "JAIN IRRIGATION", 10, "contains"),
	("Irrigation", "Irrigation — generic", "IRRIGATION", 100, "contains"),
	# Insurance
	("Insurance", "Insurance — crop", "CROP INSURANCE", 10, "contains"),
	("Insurance", "Insurance — Farm Bureau", "FARM BUREAU", 12, "contains"),
	("Insurance", "Insurance — generic", "INSURANCE", 100, "contains"),
	# Utilities
	("Utilities", "Utilities — PUD", r"\bPUD\b", 10, "regex"),
	("Utilities", "Utilities — electric", "ELECTRIC", 100, "contains"),
	("Utilities", "Utilities — power", "POWER CO", 100, "contains"),
	("Utilities", "Utilities — waste", "WASTE MGMT", 100, "contains"),
	# Feed and supplies
	("Feed", "Feed — feed store", "FEED", 100, "contains"),
	("Supplies", "Supplies — Grainger", "GRAINGER", 10, "contains"),
	("Supplies", "Supplies — farm supply", "FARM SUPPLY", 20, "contains"),
	("Supplies", "Supplies — co-op", "CO-OP", 40, "contains"),
	# Professional services
	("Professional Services", "Professional — CPA", "CPA", 20, "contains"),
	("Professional Services", "Professional — accounting", "ACCOUNTING", 100, "contains"),
	("Professional Services", "Professional — attorney", "ATTORNEY", 100, "contains"),
	("Professional Services", "Professional — law firm", "LAW OFFICE", 100, "contains"),
	# Owner draw. Only the phrases that SAY it — see the note above about
	# transfers.
	("Owner Draw", "Owner Draw — named", "OWNER DRAW", 10, "contains"),
	("Owner Draw", "Owner Draw — member draw", "MEMBER DRAW", 10, "contains"),
	("Owner Draw", "Owner Draw — distribution", "MEMBER DISTRIBUTION", 12, "contains"),
)


def seed_farm_categorization_rules(args: dict) -> ToolResult:
	"""Put a starting book of farm categorisation rules on the site. Idempotent.

	IDEMPOTENT BY (company, rule_name), so running it twice creates nothing the
	second time and running it after an operator has edited a rule leaves the
	edit alone — a changed pattern, a lowered priority and an added account all
	survive.

	A DELETED RULE COMES BACK IF THIS IS RUN AGAIN, and that is worth saying
	plainly rather than solving. The alternative is a tombstone register — a
	record of every rule anybody ever deleted, consulted forever — which is more
	machinery than the problem deserves. DISABLE a rule that does not fit this
	farm instead: `enabled` is left alone by every later run, and a disabled rule
	still says what somebody decided and why.

	ACCOUNTS ARE MAPPED, NEVER GUESSED. Pass `account_map` as
	`{"Fuel": "5200 - Fuel - ABC", ...}` and those categories get rules that can
	tell a Journal Entry where to post; the rest get rules with a category and no
	account, and the result names them under `categories_without_account`. There
	is no keyword search of the chart of accounts here on purpose: a seeder that
	picked an expense account by name would put a season of spraying wherever the
	fuzziest match landed.
	"""
	_require_rules()
	company = resolve_company(as_str(args, "company"), required=True)
	dry_run = bool(as_bool(args, "dry_run", False))
	account_map = _account_map(args, company)

	existing = {
		row["rule_name"]
		for row in frappe.db.get_all(RULE, filters={"company": company}, fields=["rule_name"], limit=MAX_SCAN)
	}

	created, skipped, failed = [], [], []
	for category, rule_name, pattern, priority, match_type in FARM_RULE_SEEDS:
		if rule_name in existing:
			skipped.append({"rule_name": rule_name, "reason": "already on this site"})
			continue
		if dry_run:
			created.append(
				{
					"name": None,
					"rule_name": rule_name,
					"category": category,
					"pattern": pattern,
					"priority": priority,
					"match_type": match_type,
					"account": account_map.get(category),
				}
			)
			continue
		try:
			doc = frappe.new_doc(RULE)
			doc.rule_name = rule_name
			doc.company = company
			doc.category = category
			doc.pattern = pattern
			doc.priority = priority
			doc.match_field = "description"
			doc.match_type = match_type
			# Every seeded rule is an EXPENSE rule. A refund from Chevron is not
			# a tank of diesel, and filing one as a cost is a real error that
			# nets out against nothing.
			doc.direction = "Withdrawal"
			doc.enabled = 1
			if account_map.get(category):
				doc.account = account_map[category]
			doc.insert()
			created.append(
				{
					"name": doc.name,
					"rule_name": rule_name,
					"category": category,
					"pattern": pattern,
					"priority": priority,
					"match_type": match_type,
					"account": account_map.get(category),
				}
			)
		except Exception as exc:
			failed.append({"rule_name": rule_name, "error": f"{type(exc).__name__}: {exc}"})

	categories = sorted({category for category, _, _, _, _ in FARM_RULE_SEEDS})
	without_account = [category for category in categories if category not in account_map]

	data = {
		"company": company,
		"dry_run": dry_run,
		"created": created,
		"created_count": len(created),
		"skipped": skipped,
		"skipped_count": len(skipped),
		"failed": failed,
		"categories": categories,
		"categories_with_account": dict(sorted(account_map.items())),
		"categories_without_account": without_account,
		"note": (
			"Every one of these is an ordinary record. Edit a pattern, change a priority, add an "
			"account — a later run leaves all of that alone. DISABLE one that does not fit this "
			"farm rather than deleting it: a deleted rule is re-created by the next run, and a "
			"disabled one still says what somebody decided. Specific merchant rules sit at "
			"priority 10-40 and generic words at 100+, because the FIRST match wins."
		),
		"next_step": (
			"Run apply_categorization_rules with dry_run=true against a month of statement first. "
			"What it does NOT categorise is the list of rules this farm still needs."
		),
	}
	if without_account:
		data["warning"] = (
			f"{len(without_account)} categor(ies) have no expense account: "
			f"{', '.join(without_account)}. Those rules sort a statement but cannot tell a Journal "
			"Entry where to post. Pass account_map to set them — this tool will not guess an "
			"account from a category name."
		)
	if failed:
		data["failed_note"] = (
			"Rules that would not insert are listed with the framework's own message. The ones that "
			"did insert are unaffected."
		)

	return ToolResult(
		data,
		("would seed" if dry_run else "seeded") + f" {len(created)} categorization rule(s) for {company} "
		f"({len(skipped)} already present, {len(failed)} failed)",
		docstatus_delta="none → 0 (created)" if created and not dry_run else "",
	)


def _account_map(args: dict, company: str) -> dict:
	"""`{category: account}` from the caller, every account fully vetted.

	A bad account in the map is refused before ANY rule is created, so a seeding
	run either produces the book the caller described or produces nothing.
	"""
	raw = args.get("account_map")
	if raw in (None, ""):
		return {}
	if not isinstance(raw, dict):
		raise ToolError(
			"account_map is an object of {category: account}, e.g. "
			'{"Fuel": "5200 - Fuel - ABC"}. Nothing was created.'
		)
	out = {}
	for category, account in raw.items():
		if not account:
			continue
		out[str(category)] = _rule_account({"account": str(account)}, company)
	return out
