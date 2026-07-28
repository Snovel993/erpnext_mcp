# SPDX-License-Identifier: MIT
"""What the company still owes, and to whom: notes payable.

WHY THIS IS NOT ERPNEXT'S LOAN MODULE. ERPNext's Loan models the company as the
*lender* — an application, a disbursement, a repayment schedule, its own
accounting, half a dozen doctypes. A holding company with four notes outstanding
is on the other side of every one of those, and installing a lending product to
record them is a lot of machinery to answer "what is left on the sorter".

WHAT A NOTE PAYABLE RECORD IS FOR, given that the liability already has a GL
account. Three things the ledger cannot tell you:

  * **Terms.** The rate, the maturity, the payment frequency. A balance on
    account 2310 does not say when it is due.
  * **Provenance.** Which paper says so, and — for a family note traced back to
    2003 — what was agreed and by whom. The account balance is the number; this
    is the sentence that explains it.
  * **What it secures.** A note that financed an asset should mature in the month
    that asset is fully depreciated, and `link_asset_to_note` holds the two
    together. It can only do that if the note is a document with a maturity date
    on it.

THE BALANCE HERE IS A CONVENIENCE. `principal_outstanding` is maintained by
`record_loan_payment` for readability. The authoritative figure is the balance of
the linked GL account, and the two diverge whenever a payment has been recorded
as a draft that nobody has posted — which is the normal state of affairs here,
because nothing in this app submits. Every response that reports the field says
so.

NO TOOL HERE POSTS. `record_loan_payment` writes a DRAFT Journal Entry, like
every other tool that touches the ledger. `close_note_payable` writes no journal
entry at all, deliberately: relieving a written-off balance is a real posting with
real tax consequences, and it should be a journal entry somebody wrote on purpose
rather than a side effect of setting a status.
"""

import frappe

from .. import compat
from ..args import (
	as_bool,
	as_date,
	as_float,
	as_limit,
	as_str,
	resolve_account,
	resolve_company,
)
from ..errors import ToolError
from ..result import ToolResult
from . import assets, mutate

# `_validated_choice` reads a Select field's options off this site's own meta
# rather than carrying a copy of the list. Imported rather than re-implemented:
# a second copy would accept a value the doctype rejects the moment somebody
# customises the options, and the failure would arrive as a framework traceback.
from .governance import _validated_choice

NOTE = "Note Payable"

_NOTE_FIELDS = (
	"name",
	"note_name",
	"borrower",
	"lender",
	"status",
	"superseded_by",
	"document_reference",
	"principal_original",
	"principal_outstanding",
	"interest_rate",
	"interest_type",
	"origination_date",
	"maturity_date",
	"payment_frequency",
	"payment_amount",
	"linked_gl_account",
	"interest_expense_account",
	"related_asset",
	"notes",
	"creation",
	"owner",
)

#: A note in one of these states is finished. Nothing may be recorded against it
#: and it cannot be closed again.
CLOSED_STATUSES = ("Paid Off", "Refinanced", "Written Off", "Superseded")

#: How a disposition maps onto the status the note ends up in. The three a caller
#: may ask for; `Superseded` is set by the tool on the note a refinance replaced,
#: never requested directly.
DISPOSITIONS = ("Paid Off", "Refinanced", "Written Off")

#: Months between scheduled payments, for the next-payment estimate.
_FREQUENCY_MONTHS = {"Monthly": 1, "Quarterly": 3, "Annual": 12}

#: Currency rounding. Matches `tools/assets.py` and ERPNext's own default.
MONEY_PLACES = 2

#: Tolerance on the principal + interest = total check, matching the
#: double-entry tolerance in `mutate`.
SPLIT_TOLERANCE = mutate.BALANCE_TOLERANCE


# ── shared lookups ──────────────────────────────────────────────────────────
def _require_notes() -> None:
	compat.require_doctype(
		NOTE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def note_payable(note: str, company: str = "") -> dict:
	"""One Note Payable as a dict, from its docname or its note_name.

	Two ways for the same reason `cap_table_entry` takes two: a caller who created
	the note knows it by the name it asked for, and everything that links to it
	holds the docname with the company abbreviation on the end.
	"""
	note = (note or "").strip()
	if not note:
		raise ToolError("note is required (a Note Payable docname, or the note_name it was created with)")
	fields = compat.existing_fields(NOTE, _NOTE_FIELDS)

	if frappe.db.exists(NOTE, note):
		row = frappe.db.get_value(NOTE, note, fields, as_dict=True)
		if company and row and row.get("borrower") != company:
			raise ToolError(
				f"Note Payable {note!r} is owed by {row.get('borrower')!r}, not {company!r}"
			)
		return dict(row)

	filters = {"note_name": note}
	if company:
		filters["borrower"] = company
	matches = frappe.db.get_all(NOTE, filters=filters, fields=fields, limit=25)
	if len(matches) == 1:
		return dict(matches[0])
	if len(matches) > 1:
		raise ToolError(
			f"note_name {note!r} exists for {len(matches)} borrowers "
			f"({', '.join(sorted(str(row['borrower']) for row in matches))}). Pass company to narrow it."
		)
	known = frappe.db.get_all(
		NOTE, filters={"borrower": company} if company else None, pluck="note_name", limit=25
	)
	scope = f" for {company}" if company else ""
	raise ToolError(
		f"no Note Payable called {note!r}{scope}. Known notes: "
		f"{', '.join(sorted(str(name) for name in known)) or '<none>'}. "
		"Create one with create_note_payable."
	)


def _describe(row: dict) -> dict:
	"""The note shape every tool in this module returns."""
	return {
		"name": row.get("name"),
		"note_name": row.get("note_name"),
		"borrower": row.get("borrower"),
		"lender": row.get("lender"),
		"status": row.get("status"),
		"principal_original": float(row.get("principal_original") or 0),
		"principal_outstanding": float(row.get("principal_outstanding") or 0),
		"interest_rate": float(row.get("interest_rate") or 0),
		"interest_type": row.get("interest_type") or None,
		"origination_date": str(row.get("origination_date") or "") or None,
		"maturity_date": str(row.get("maturity_date") or "") or None,
		"payment_frequency": row.get("payment_frequency") or None,
		"payment_amount": float(row.get("payment_amount") or 0) or None,
		"linked_gl_account": row.get("linked_gl_account") or None,
		"interest_expense_account": row.get("interest_expense_account") or None,
		"related_asset": row.get("related_asset") or None,
		"superseded_by": row.get("superseded_by") or None,
		"document_reference": row.get("document_reference") or None,
	}


def _events(name: str) -> list[dict]:
	doc = frappe.get_doc(NOTE, name)
	return [
		{
			"event_type": row.get("event_type"),
			"event_date": str(row.get("event_date") or "") or None,
			"amount": float(row.get("amount") or 0),
			"principal_component": float(row.get("principal_component") or 0),
			"interest_component": float(row.get("interest_component") or 0),
			"principal_outstanding_after": float(row.get("principal_outstanding_after") or 0),
			"journal_entry": row.get("journal_entry") or None,
			"narrative": row.get("narrative") or None,
		}
		for row in (doc.get("payment_events") or [])
	]


def _vetted_account(requested: str, company: str, label: str, root_types, forbidden_types=()) -> str:
	"""One account resolved and checked: this company, a leaf, enabled, right root."""
	docname = resolve_account(requested, company)
	account = (
		frappe.db.get_value(
			"Account",
			docname,
			compat.existing_fields(
				"Account", ("name", "company", "is_group", "root_type", "account_type", "disabled")
			),
			as_dict=True,
		)
		or {}
	)
	if str(account.get("company") or "") != company:
		raise ToolError(
			f"{label}: account {docname!r} belongs to company {account.get('company')!r}, not "
			f"{company!r}. Nothing was changed."
		)
	if int(account.get("is_group") or 0):
		raise ToolError(
			f"{label}: {docname!r} is a group account, and ERPNext posts only to leaf accounts. "
			"Nothing was changed."
		)
	if int(account.get("disabled") or 0):
		raise ToolError(f"{label}: {docname!r} is disabled, so nothing can post to it. Nothing was changed.")

	root_type = str(account.get("root_type") or "")
	if root_type not in root_types:
		raise ToolError(
			f"{label} has to be {' or '.join(root_types)}; {docname!r} is "
			f"{root_type or 'untyped'}. Nothing was changed."
		)
	account_type = str(account.get("account_type") or "")
	if account_type and account_type in forbidden_types:
		raise ToolError(
			f"{label}: {docname!r} has account_type {account_type!r}. ERPNext keys party ledgers "
			"and every ageing report off that flag, so a note's principal booked there would "
			"appear as a supplier or customer balance that never ages out. Use a plain liability "
			"account for notes payable. Nothing was changed."
		)
	return docname


# ── 67. create_note_payable ─────────────────────────────────────────────────
def create_note_payable(args: dict) -> ToolResult:
	"""Register one outstanding note: its terms, its paper, and what it secures."""
	_require_notes()
	company = resolve_company(as_str(args, "borrower") or as_str(args, "company"), required=True)
	note_name = as_str(args, "note_name", required=True)
	lender = as_str(args, "lender", required=True)
	origination_date = as_date(args, "origination_date", required=True)
	maturity_date = as_date(args, "maturity_date")
	principal_original = as_float(args.get("principal_original"), "principal_original")
	interest_rate = as_float(args.get("interest_rate"), "interest_rate")
	payment_amount = as_float(args.get("payment_amount"), "payment_amount")
	document_reference = as_str(args, "document_reference")
	notes = as_str(args, "notes")
	enforce_asset_tenor = bool(as_bool(args, "enforce_asset_tenor", True))

	if "status" in args or "superseded_by" in args:
		raise ToolError(
			"a Note Payable cannot be created already closed. Create it, then close it with "
			"close_note_payable, which records the disposition in the note's event history "
			"rather than silently setting a field. Nothing was created."
		)

	if principal_original <= 0:
		raise ToolError(
			f"principal_original must be positive, got {principal_original}. A note for nothing "
			"is not a note. Nothing was created."
		)
	outstanding = (
		as_float(args.get("principal_outstanding"), "principal_outstanding")
		if args.get("principal_outstanding") not in (None, "")
		else principal_original
	)
	if outstanding < 0:
		raise ToolError(
			f"principal_outstanding cannot be negative, got {outstanding}. Nothing was created."
		)
	if interest_rate < 0 or interest_rate > 100:
		raise ToolError(f"interest_rate is an annual percentage between 0 and 100, got {interest_rate}")

	interest_type = _validated_choice(
		NOTE, "interest_type", as_str(args, "interest_type") or "Fixed", "interest_type"
	)
	payment_frequency = _validated_choice(
		NOTE, "payment_frequency", as_str(args, "payment_frequency") or "Monthly", "payment_frequency"
	)
	if interest_type == "Zero" and interest_rate:
		raise ToolError(
			f"interest_type is Zero but interest_rate is {interest_rate}. One of the two is "
			"wrong, and quietly keeping both would make every interest figure computed from this "
			"note indefensible. Nothing was created."
		)
	if maturity_date and maturity_date < origination_date:
		raise ToolError(
			f"maturity_date {maturity_date} is before origination_date {origination_date}. "
			"Nothing was created."
		)

	duplicate = frappe.db.get_value(NOTE, {"note_name": note_name, "borrower": company}, "name")
	if duplicate:
		raise ToolError(
			f"{company} already has a note called {note_name!r} ({duplicate}). One note per name "
			"per borrower — a second one with the same name is two records nobody can tell apart "
			"in a payment. Give this one a name that distinguishes it (the year is the usual "
			"one), or record a payment against the existing note. Nothing was created."
		)

	linked_gl_account = ""
	if as_str(args, "linked_gl_account"):
		linked_gl_account = _vetted_account(
			as_str(args, "linked_gl_account"),
			company,
			"linked_gl_account",
			("Liability",),
			forbidden_types=("Payable", "Receivable"),
		)
	interest_expense_account = ""
	if as_str(args, "interest_expense_account"):
		interest_expense_account = _vetted_account(
			as_str(args, "interest_expense_account"), company, "interest_expense_account", ("Expense",)
		)

	related_asset = ""
	if as_str(args, "related_asset"):
		related_asset = assets.resolve_asset(as_str(args, "related_asset"), company)

	doc = frappe.new_doc(NOTE)
	doc.note_name = note_name
	doc.borrower = company
	doc.lender = lender
	doc.status = "Active"
	doc.principal_original = principal_original
	doc.principal_outstanding = outstanding
	doc.interest_rate = interest_rate
	doc.interest_type = interest_type
	doc.origination_date = origination_date
	if maturity_date:
		doc.maturity_date = maturity_date
	doc.payment_frequency = payment_frequency
	if payment_amount:
		doc.payment_amount = payment_amount
	if linked_gl_account:
		doc.linked_gl_account = linked_gl_account
	if interest_expense_account:
		doc.interest_expense_account = interest_expense_account
	if related_asset:
		doc.related_asset = related_asset
	if document_reference:
		doc.document_reference = document_reference
	if notes:
		doc.notes = notes
	doc.insert()

	asset_link = None
	if related_asset:
		# Delegates to the tenor check that already exists rather than repeating it.
		# A refusal here rolls the whole call back — the note and the link are one
		# transaction, so there is never a note whose asset link was refused.
		asset_link = assets.link_asset_to_note(
			{
				"asset": related_asset,
				"company": company,
				"linked_note": doc.name,
				"note_doctype": NOTE,
				"enforce_tenor": enforce_asset_tenor,
			}
		).data

	row = _describe(dict(frappe.db.get_value(NOTE, doc.name, compat.existing_fields(NOTE, _NOTE_FIELDS), as_dict=True)))
	data = {
		**row,
		"asset_link": asset_link,
		"tenor_months": _tenor_months(origination_date, maturity_date),
		"note": (
			"principal_outstanding is a convenience figure maintained by record_loan_payment. "
			+ (
				f"The authoritative balance is {linked_gl_account}, and the two diverge whenever "
				"a payment has been recorded as a draft nobody has posted."
				if linked_gl_account
				else "No GL account is linked, so nothing on this record ties back to the ledger "
				"at all — set linked_gl_account so a payment can be booked against it."
			)
		),
		"next_step": (
			"Record payments with record_loan_payment, which writes a DRAFT journal entry "
			"splitting principal from interest and decrements the balance here."
			+ (
				f" The asset {related_asset} now points back at this note, so "
				"depreciation_note_alignment_check will report if their terms part company."
				if asset_link
				else ""
			)
		),
	}
	if outstanding > principal_original + SPLIT_TOLERANCE:
		data["warning"] = (
			f"principal_outstanding ({outstanding}) is greater than principal_original "
			f"({principal_original}). That is legitimate on a note that capitalises unpaid "
			"interest and is a typo everywhere else — it was not refused, but check it."
		)
	return ToolResult(
		data,
		f"registered Note Payable {doc.name}: {lender} owed {outstanding} of an original "
		f"{principal_original}, originated {origination_date}"
		+ (f", maturing {maturity_date}" if maturity_date else ", no maturity date recorded"),
		docstatus_delta="none → 0 (created)",
	)


def _tenor_months(origination_date: str, maturity_date: str) -> int | None:
	if not maturity_date:
		return None
	return assets.months_between(origination_date, maturity_date)


# ── 68. record_loan_payment ─────────────────────────────────────────────────
def record_loan_payment(args: dict) -> ToolResult:
	"""Split one payment into principal and interest, and draft the entry that books it.

	The split is the whole job. A loan payment leaving a bank account is one
	number, and the two halves of it land in completely different places — one
	reduces a liability and one is an expense of the period. Booked as a single
	line against the liability, the year's interest expense is nil and the balance
	sheet says the note was paid down by more than it was.
	"""
	_require_notes()
	company = as_str(args, "company")
	note = note_payable(as_str(args, "note", required=True), company)
	company = note["borrower"]
	payment_date = as_date(args, "payment_date", required=True)
	narrative = as_str(args, "narrative")

	if str(note.get("status") or "") in CLOSED_STATUSES:
		raise ToolError(
			f"Note Payable {note['name']} is {note['status']}, so nothing further is recorded "
			"against it. If a payment was missed before it closed, that is a correction to make "
			"in the Desk. Nothing was created."
		)
	origination = str(note.get("origination_date") or "")
	if origination and payment_date < origination:
		raise ToolError(
			f"payment_date {payment_date} is before the note was originated ({origination}). "
			"Nothing was created."
		)

	total, principal, interest = _validated_split(args)

	outstanding = round(float(note.get("principal_outstanding") or 0), MONEY_PLACES)
	if principal > outstanding + SPLIT_TOLERANCE:
		raise ToolError(
			f"the principal half of this payment ({principal}) is more than the {outstanding} "
			f"still outstanding on {note['name']}. A payment that clears more principal than is "
			"owed is either the wrong split or a note whose balance is stale — neither is fixed "
			"by writing a negative balance. Nothing was created."
		)

	notes_payable_account = _payment_account(
		args, note, "notes_payable_account", "linked_gl_account", company, ("Liability",),
		forbidden_types=("Payable", "Receivable"),
		missing=(
			"this note has no linked_gl_account and no notes_payable_account was passed, so "
			"there is no liability account to debit. Set one on the note in the Desk, or pass "
			"notes_payable_account. Nothing was created."
		),
	)
	interest_account = ""
	if interest:
		interest_account = _payment_account(
			args, note, "interest_expense_account", "interest_expense_account", company, ("Expense",),
			missing=(
				"this payment has an interest component and the note has no "
				"interest_expense_account, so there is no expense account to debit. Set one on "
				"the note, or pass interest_expense_account. Nothing was created."
			),
		)

	offset_gl, offset_bank_account = _offset_account(args, company)

	raw_lines = []
	if principal:
		raw_lines.append(
			{
				"account": notes_payable_account,
				"debit": principal,
				"user_remark": f"Principal — {note['note_name']}",
			}
		)
	if interest:
		raw_lines.append(
			{
				"account": interest_account,
				"debit": interest,
				"user_remark": f"Interest — {note['note_name']}",
			}
		)
	credit = {
		"account": offset_gl,
		"credit": total,
		"user_remark": f"Payment to {note['lender']} — {note['note_name']}",
	}
	if offset_bank_account and compat.has_field("Journal Entry Account", "bank_account"):
		credit["bank_account"] = offset_bank_account
	raw_lines.append(credit)

	lines = mutate.validated_journal_lines(raw_lines, company)
	remark = (
		f"Loan payment {total} on {note['name']} ({note['lender']}): {principal} principal, "
		f"{interest} interest." + (f" {narrative}" if narrative else "")
	)
	entry = mutate.insert_draft_journal_entry(company, payment_date, lines, remark)

	remaining = round(outstanding - principal, MONEY_PLACES)
	doc = frappe.get_doc(NOTE, note["name"])
	doc.principal_outstanding = remaining
	doc.append(
		"payment_events",
		{
			"event_type": "Payment",
			"event_date": payment_date,
			"amount": total,
			"principal_component": principal,
			"interest_component": interest,
			"principal_outstanding_after": remaining,
			"journal_entry": entry.name,
			"narrative": narrative or f"Payment to {note['lender']}",
			"recorded_on": frappe.utils.now(),
		},
	)
	doc.save()

	data = {
		"note": note["name"],
		"note_name": note["note_name"],
		"lender": note["lender"],
		"payment_date": payment_date,
		"total_amount": total,
		"principal_split": principal,
		"interest_split": interest,
		"principal_outstanding_before": outstanding,
		"principal_outstanding_after": remaining,
		"journal_entry": entry.name,
		"accounts_used": {
			"notes_payable_account": notes_payable_account,
			"interest_expense_account": interest_account or None,
			"offset_account": offset_gl,
			"offset_bank_account": offset_bank_account or None,
		},
		"lines": [
			{"account": line["account"], "debit": line.get("debit") or 0, "credit": line.get("credit") or 0}
			for line in lines
		],
		"payment_count": len(_events(note["name"])),
		"note_text": (
			f"Journal Entry {entry.name} is a DRAFT and has moved no balance. The outstanding "
			f"figure on the note has already been decremented to {remaining}, so until the entry "
			f"is posted this record and the balance of {notes_payable_account} disagree by "
			f"{principal}. That is the normal state in this app — nothing here submits."
		),
		"next_step": (
			f"Post it with submit_journal_entry. The note still shows {remaining} outstanding; "
			"close it with close_note_payable when it reaches zero."
			if remaining > SPLIT_TOLERANCE
			else (
				"Post it with submit_journal_entry. The note is now down to zero — close it with "
				"close_note_payable(disposition='Paid Off'), which is a separate call so that a "
				"final payment and the decision to close are two deliberate acts."
			)
		),
	}
	return ToolResult(
		data,
		f"recorded {total} against {note['name']} ({principal} principal, {interest} interest) "
		f"on {payment_date}; draft Journal Entry {entry.name}, {remaining} outstanding",
		docstatus_delta="none → 0 (draft)",
	)


def _validated_split(args: dict) -> tuple:
	"""total, principal and interest, with the missing one derived and all three checked."""
	total = as_float(args.get("total_amount"), "total_amount")
	if total <= 0:
		raise ToolError(f"total_amount must be positive, got {total}. Nothing was created.")

	has_principal = args.get("principal_split") not in (None, "")
	has_interest = args.get("interest_split") not in (None, "")
	if not has_principal and not has_interest:
		raise ToolError(
			"pass principal_split, interest_split, or both. A payment booked without the split "
			"puts the interest into the liability, which understates the year's interest expense "
			"and overstates how much of the note has been paid off. If this payment really is all "
			"principal, say so with principal_split equal to total_amount. Nothing was created."
		)

	principal = as_float(args.get("principal_split"), "principal_split") if has_principal else None
	interest = as_float(args.get("interest_split"), "interest_split") if has_interest else None
	if principal is None:
		principal = round(total - interest, MONEY_PLACES)
	if interest is None:
		interest = round(total - principal, MONEY_PLACES)

	if principal < 0 or interest < 0:
		raise ToolError(
			f"the split works out to {principal} principal and {interest} interest, and neither "
			"can be negative. Check that they belong to this payment. Nothing was created."
		)
	if abs(principal + interest - total) > SPLIT_TOLERANCE:
		raise ToolError(
			f"principal_split ({principal}) plus interest_split ({interest}) is "
			f"{round(principal + interest, MONEY_PLACES)}, not total_amount ({total}). The "
			"journal entry would not balance. Nothing was created."
		)
	return round(total, MONEY_PLACES), round(principal, MONEY_PLACES), round(interest, MONEY_PLACES)


def _payment_account(args, note, argument, field, company, root_types, missing, forbidden_types=()):
	"""An account for one side of a payment: the argument, else the note, else refuse."""
	requested = as_str(args, argument)
	if requested:
		return _vetted_account(requested, company, argument, root_types, forbidden_types)
	stored = str(note.get(field) or "")
	if not stored:
		raise ToolError(missing)
	return _vetted_account(stored, company, f"the note's {field}", root_types, forbidden_types)


def _offset_account(args: dict, company: str) -> tuple:
	"""Where the money left from: a Bank Account record, or the GL account itself.

	Both are accepted because both are what somebody means by "the account it came
	out of". Naming the Bank Account record is better — the journal entry line then
	carries it, which is what lets a bank reconciliation match this entry — so when
	one is named, its GL account is used and the record is carried along.
	"""
	requested = as_str(args, "offset_bank_account", required=True)

	if compat.doctype_exists("Bank Account"):
		row = frappe.db.get_value(
			"Bank Account",
			requested,
			compat.existing_fields("Bank Account", ("name", "account", "company")),
			as_dict=True,
		)
		if row is None:
			match = frappe.db.get_all(
				"Bank Account",
				filters={"account_name": requested, "company": company},
				fields=compat.existing_fields("Bank Account", ("name", "account", "company")),
				limit=2,
			)
			row = dict(match[0]) if len(match) == 1 else None
		if row:
			if row.get("company") and row["company"] != company:
				raise ToolError(
					f"Bank Account {row['name']!r} belongs to {row['company']!r}, not {company!r}. "
					"Nothing was created."
				)
			if not row.get("account"):
				raise ToolError(
					f"Bank Account {row['name']!r} has no GL account set, so there is nothing to "
					"credit. Set one on the record, or pass the account directly. Nothing was created."
				)
			gl = _vetted_account(row["account"], company, "offset_bank_account", ("Asset", "Liability"))
			return gl, row["name"]

	gl = _vetted_account(requested, company, "offset_bank_account", ("Asset", "Liability"))
	return gl, ""


# ── 69. list_notes_payable ──────────────────────────────────────────────────
def list_notes_payable(args: dict) -> ToolResult:
	"""What is still owed, by whom, and when the next payment falls due."""
	_require_notes()
	company = resolve_company(as_str(args, "borrower") or as_str(args, "company"), required=True)
	limit = as_limit(args)
	include_closed = as_bool(args, "include_closed", True)

	filters = {"borrower": company}
	status = as_str(args, "status")
	if status:
		filters["status"] = _validated_choice(NOTE, "status", status, "status")

	fields = compat.existing_fields(NOTE, _NOTE_FIELDS)
	rows = frappe.db.get_all(NOTE, filters=filters, fields=fields, order_by="note_name asc", limit=limit)

	notes, total_outstanding, total_original = [], 0.0, 0.0
	for row in rows:
		described = _describe(dict(row))
		if described["status"] in CLOSED_STATUSES and not include_closed:
			continue
		events = _events(described["name"])
		payments = [event for event in events if event["event_type"] == "Payment"]
		described["payment_count"] = len(payments)
		described["last_payment_date"] = payments[-1]["event_date"] if payments else None
		described["next_payment_date"] = _next_payment_date(described, payments)
		described["closed"] = described["status"] in CLOSED_STATUSES
		if not described["closed"]:
			total_outstanding = round(total_outstanding + described["principal_outstanding"], MONEY_PLACES)
			total_original = round(total_original + described["principal_original"], MONEY_PLACES)
		notes.append(described)

	active = [note for note in notes if not note["closed"]]
	data = {
		"company": company,
		"notes": notes,
		"count": len(notes),
		"active_count": len(active),
		"closed_included": bool(include_closed),
		"total_original_principal_active": total_original,
		"total_outstanding_active": total_outstanding,
		"limit": limit,
		"filters": {"status": status or None, "include_closed": bool(include_closed)},
		"note": (
			"Outstanding balances are the figure maintained by record_loan_payment, not the "
			"balance of the linked GL account. They diverge by any payment recorded as a draft "
			"nobody has posted — get_account_balance on linked_gl_account is the ledger's answer. "
			"next_payment_date is an estimate from the payment frequency and the last payment "
			"recorded; it is not a schedule the lender agreed to."
		),
	}
	return ToolResult(
		data,
		f"{len(notes)} note(s) payable for {company}: {len(active)} active, "
		f"{total_outstanding} outstanding",
	)


def _next_payment_date(note: dict, payments: list) -> str | None:
	"""When the next payment is due, estimated from the frequency. None when unknowable."""
	if note["status"] in CLOSED_STATUSES:
		return None
	months = _FREQUENCY_MONTHS.get(str(note.get("payment_frequency") or ""))
	if not months:
		# Balloon and Custom have no cadence to project from. The maturity date is
		# the only date that means anything for a balloon, so say that instead of
		# inventing a monthly schedule.
		return note.get("maturity_date") if note.get("payment_frequency") == "Balloon" else None
	last = payments[-1]["event_date"] if payments else note.get("origination_date")
	if not last:
		return None
	nxt = assets.add_months(last, months).isoformat()
	maturity = note.get("maturity_date")
	if maturity and nxt > maturity:
		return maturity
	return nxt


# ── 70. close_note_payable ──────────────────────────────────────────────────
def close_note_payable(args: dict) -> ToolResult:
	"""Close a note, recording the disposition in its history rather than as a flag.

	Writes NO journal entry, deliberately. Relieving a written-off balance is a
	posting with real tax consequences — debt forgiveness is usually income — and a
	refinance moves a balance between two liability accounts. Both should be
	entries somebody wrote on purpose, with a narrative of their own, rather than
	a side effect of setting a status. The response says which entry is still owed.
	"""
	_require_notes()
	company = as_str(args, "company")
	note = note_payable(as_str(args, "note", required=True), company)
	company = note["borrower"]
	disposition_date = as_date(args, "disposition_date", required=True)
	narrative = as_str(args, "narrative", required=True)
	if len(narrative) < 8:
		raise ToolError(
			"narrative must be a real explanation of how the note ended — what was paid, "
			"forgiven or rolled over, and what authorises it. It is the part of this record "
			"nobody can reconstruct later. Nothing was changed."
		)

	disposition = as_str(args, "disposition", required=True)
	matched = [option for option in DISPOSITIONS if option.lower() == disposition.lower()]
	if not matched:
		raise ToolError(
			f"disposition must be one of: {', '.join(DISPOSITIONS)}. Got {disposition!r}. "
			"('Superseded' is set by this tool on the note a refinance replaced; it is not asked "
			"for directly.) Nothing was changed."
		)
	disposition = matched[0]

	if str(note.get("status") or "") in CLOSED_STATUSES:
		raise ToolError(
			f"Note Payable {note['name']} is already {note['status']}. Nothing was changed."
		)
	origination = str(note.get("origination_date") or "")
	if origination and disposition_date < origination:
		raise ToolError(
			f"disposition_date {disposition_date} is before the note was originated "
			f"({origination}). Nothing was changed."
		)

	outstanding = round(float(note.get("principal_outstanding") or 0), MONEY_PLACES)
	zero_remaining = bool(as_bool(args, "zero_remaining_balance", False))
	if disposition == "Paid Off" and outstanding > SPLIT_TOLERANCE and not zero_remaining:
		raise ToolError(
			f"{note['name']} still shows {outstanding} outstanding, and 'Paid Off' says it does "
			"not. Either a final payment has not been recorded — record_loan_payment is the way "
			"to do that, and it writes the entry that books it — or the balance carried on this "
			"record is stale. If it is stale and you want it written down to zero without a "
			"payment, pass zero_remaining_balance=true, which records the write-down as an "
			"Adjustment in the note's history. Nothing was changed."
		)

	successor = as_str(args, "superseded_by")
	if successor:
		if disposition != "Refinanced":
			raise ToolError(
				"superseded_by names the note that replaced this one, which only makes sense for "
				"a Refinanced disposition. Nothing was changed."
			)
		successor_row = note_payable(successor, company)
		if successor_row["name"] == note["name"]:
			raise ToolError("a note cannot supersede itself. Nothing was changed.")
		successor = successor_row["name"]

	doc = frappe.get_doc(NOTE, note["name"])
	doc.status = disposition
	doc.principal_outstanding = 0
	if successor:
		doc.superseded_by = successor
	if disposition == "Paid Off" and zero_remaining and outstanding > SPLIT_TOLERANCE:
		doc.append(
			"payment_events",
			{
				"event_type": "Adjustment",
				"event_date": disposition_date,
				"amount": outstanding,
				"principal_component": outstanding,
				"interest_component": 0,
				"principal_outstanding_after": 0,
				"narrative": (
					f"Balance of {outstanding} written down to zero at close without a payment "
					f"(zero_remaining_balance). {narrative}"
				),
				"recorded_on": frappe.utils.now(),
			},
		)
	doc.append(
		"payment_events",
		{
			"event_type": disposition,
			"event_date": disposition_date,
			"amount": outstanding,
			"principal_component": 0,
			"interest_component": 0,
			"principal_outstanding_after": 0,
			"narrative": narrative,
			"recorded_on": frappe.utils.now(),
		},
	)
	doc.save()

	after = dict(frappe.db.get_value(NOTE, note["name"], compat.existing_fields(NOTE, _NOTE_FIELDS), as_dict=True))
	data = {
		**_describe(after),
		"disposition": disposition,
		"disposition_date": disposition_date,
		"narrative": narrative,
		"principal_outstanding_at_close": outstanding,
		"balance_zeroed_without_payment": bool(
			disposition == "Paid Off" and zero_remaining and outstanding > SPLIT_TOLERANCE
		),
		"events": _events(note["name"]),
		"journal_entry": None,
		"note": (
			"NO journal entry was written. Closing a note is a change to this record only — the "
			+ (
				f"balance of {note.get('linked_gl_account')} is untouched"
				if note.get("linked_gl_account")
				else "ledger is untouched"
			)
			+ ". "
			+ _ledger_advice(disposition, outstanding, note)
		),
		"next_step": (
			f"{successor} is now recorded as the note that replaced this one."
			if successor
			else (
				"A refinance usually has a successor note. Create it with create_note_payable and "
				"re-run this call with superseded_by naming it, so a reader following the chain "
				"forward lands on the note that is still owed."
				if disposition == "Refinanced"
				else "Nothing further is recorded against this note; list_notes_payable still "
				"shows it, because a closed note is part of the history."
			)
		),
	}
	return ToolResult(
		data,
		f"closed Note Payable {note['name']} as {disposition} on {disposition_date} "
		f"({outstanding} outstanding at close)"
		+ (f", superseded by {successor}" if successor else ""),
		docstatus_delta="",
	)


def _ledger_advice(disposition: str, outstanding: float, note: dict) -> str:
	"""The posting this tool deliberately did not make, spelled out."""
	account = note.get("linked_gl_account") or "the note's liability account"
	if outstanding <= SPLIT_TOLERANCE:
		return (
			"The balance was already zero, so there is probably nothing left to post — but check "
			f"that every payment draft against {account} has actually been submitted."
		)
	if disposition == "Written Off":
		return (
			f"{outstanding} is still sitting in {account}. Relieving it is a journal entry you "
			"have to write: debit the liability, credit whatever your accountant says forgiven "
			"debt is for this entity — it is usually income, and it is usually taxable. "
			"create_journal_entry drafts it."
		)
	if disposition == "Refinanced":
		return (
			f"{outstanding} is still sitting in {account} and now belongs to the successor note. "
			"Move it with a journal entry debiting this liability and crediting the new one — "
			"create_journal_entry drafts it."
		)
	return (
		f"{outstanding} is still sitting in {account} even though the note is marked paid off. "
		"Find out why before anything else: either a payment draft was never submitted, or the "
		"balance carried on this record was wrong."
	)
