# SPDX-License-Identifier: MIT
"""The mutating tools. Every one is off until an operator turns it on.

THE SHAPE OF THE PERMISSION. There is no `post_journal_entry` here. There is
`create_journal_entry`, which can only ever produce a draft, and a separate
`submit_journal_entry`, which can only act on a draft that already exists. An
operator who enables the first and not the second has given an AI a scratchpad
in the general ledger and nothing more: it can propose entries all day and not
one of them touches a balance. That split is the whole point, and it is why
`create_journal_entry` will not accept a `docstatus` argument and cannot be
talked into submitting.

WHAT THESE TOOLS DO NOT DO. They do not bypass ERPNext. Every write goes through
`frappe.get_doc(...).insert()` / `.submit()` / `.cancel()`, so the doctype's own
validation, the fiscal-year check, period-closing vouchers, account freezing,
mandatory dimensions and every `on_submit` hook all run exactly as they do for a
human in the UI. There is no raw SQL in this file, and there should never be:
the day an MCP tool writes a GL Entry directly is the day this app can corrupt a
ledger.

DOUBLE ENTRY IS CHECKED HERE ANYWAY. ERPNext validates that debits equal credits
on submit, but `create_journal_entry` checks before insert so an unbalanced
proposal fails with an arithmetic message the model can act on, instead of
leaving a broken draft behind for a human to find.
"""

import frappe

from .. import compat
from ..args import as_date, as_float, as_str, resolve_account, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from .read import _resolve_bank_account

#: Rounding tolerance for the double-entry check. ERPNext stores currency to the
#: precision configured on the site (2 by default); half a cent is comfortably
#: below one unit of the smallest tracked amount and above float noise.
BALANCE_TOLERANCE = 0.005

#: Per-line fields a caller may set on a Journal Entry Account row. Anything
#: else is rejected by name rather than silently dropped — a model that thought
#: it was setting `amount` should be told the field is `debit` or `credit`, not
#: get back a zero-value entry.
_LINE_FIELDS = (
	"account",
	"debit",
	"credit",
	"debit_in_account_currency",
	"credit_in_account_currency",
	"party_type",
	"party",
	"cost_center",
	"project",
	"against_account",
	"reference_type",
	"reference_name",
	"reference_due_date",
	"user_remark",
	"exchange_rate",
	"account_currency",
	"is_advance",
	"bank_account",
)

#: The per-line escape hatch for accounting dimensions. Kept out of
#: `_LINE_FIELDS` on purpose: `dimensions` is not itself a field on Journal Entry
#: Account, it is an object whose *keys* are, and every key is checked against
#: this site's own meta before it is written.
#:
#: Why an object rather than more allowlisted keys: a dimension's fieldname is
#: invented by whoever created it (`member`, `bbch_stage`, `block`), so there is
#: no list this app could ship. Why not simply stop rejecting unknown keys:
#: because then `amount` — which a model will send, meaning `debit` — would be
#: silently dropped instead of corrected. Unknown keys stay refused; dimensions
#: get their own door, and going through it is an assertion that the caller meant
#: a dimension rather than mistyped a field.
_DIMENSIONS_KEY = "dimensions"

#: Journal Entry lines are `Journal Entry Account` rows, and that is the doctype
#: ERPNext puts accounting dimension fields on. See `tools/dimensions.py`.
_LINE_DOCTYPE = "Journal Entry Account"


# ── 11. create_journal_entry ────────────────────────────────────────────────
def create_journal_entry(args: dict) -> ToolResult:
	"""Create a DRAFT Journal Entry. Never submits, whatever it is asked."""
	company = resolve_company(as_str(args, "company"), required=True)
	posting_date = as_date(args, "posting_date", required=True)
	user_remark = as_str(args, "user_remark", required=True)
	lines = _validated_lines(args.get("accounts"), company)
	total_debit, total_credit = _assert_balanced(lines)
	dimension_fields = sorted({key for line in lines for key in line if key not in _LINE_FIELDS})

	doc = frappe.new_doc("Journal Entry")
	doc.company = company
	doc.posting_date = posting_date
	doc.user_remark = user_remark
	if as_str(args, "voucher_type"):
		doc.voucher_type = as_str(args, "voucher_type")
	for field in ("cheque_no", "cheque_date", "bill_no"):
		value = as_str(args, field)
		if value:
			doc.set(field, as_date(args, field) if field.endswith("_date") else value)
	for line in lines:
		doc.append("accounts", line)

	doc.insert()
	# Belt to the "never submits" braces: if a future ERPNext hook ever
	# submitted on insert, this turns a silent posting into a loud failure.
	if int(doc.docstatus or 0) != 0:
		raise ToolError(
			f"Journal Entry {doc.name} was created with docstatus {doc.docstatus}, "
			"but create_journal_entry only ever produces drafts. Refusing to "
			"report success — inspect the site's Journal Entry hooks."
		)

	data = {
		"name": doc.name,
		"docstatus": 0,
		"docstatus_label": "draft",
		"company": company,
		"posting_date": posting_date,
		"total_debit": round(total_debit, 2),
		"total_credit": round(total_credit, 2),
		"line_count": len(lines),
		"user_remark": user_remark,
		"dimension_fields_set": dimension_fields,
		"next_step": (
			"This is a draft and affects no balance. Submit it in ERPNext, or via "
			"submit_journal_entry if that tool is enabled."
		),
	}
	return ToolResult(
		data,
		f"created draft Journal Entry {doc.name} for {company} on {posting_date}: "
		f"{len(lines)} line(s), {round(total_debit, 2)} debit = "
		f"{round(total_credit, 2)} credit",
		docstatus_delta="none → 0 (draft)",
	)


def _validated_lines(raw, company: str) -> list[dict]:
	"""Coerce and check the `accounts` argument into Journal Entry Account rows."""
	if not isinstance(raw, list) or not raw:
		raise ToolError(
			"accounts must be a non-empty list of objects, each with an account "
			"and a debit or a credit, e.g. "
			'[{"account": "1100 - Cash", "debit": 100}, '
			'{"account": "4100 - Sales", "credit": 100}]'
		)
	if len(raw) < 2:
		raise ToolError(
			"a Journal Entry needs at least two lines — one account cannot balance against itself"
		)

	lines = []
	for index, entry in enumerate(raw, start=1):
		if not isinstance(entry, dict):
			raise ToolError(f"accounts[{index}] must be an object, got {type(entry).__name__}")
		unknown = sorted(set(entry) - set(_LINE_FIELDS) - {_DIMENSIONS_KEY})
		if unknown:
			raise ToolError(
				f"accounts[{index}] has unsupported field(s): {', '.join(unknown)}. "
				f"Supported: {', '.join(_LINE_FIELDS)}. A custom accounting dimension "
				f"goes in the per-line `{_DIMENSIONS_KEY}` object, not alongside these."
			)
		account = resolve_account(str(entry.get("account") or ""), company)
		debit = as_float(entry.get("debit"), f"accounts[{index}].debit")
		credit = as_float(entry.get("credit"), f"accounts[{index}].credit")
		if debit and credit:
			raise ToolError(
				f"accounts[{index}] ({account}) sets both debit and credit; a "
				"Journal Entry line is one or the other"
			)
		if not debit and not credit:
			raise ToolError(f"accounts[{index}] ({account}) has neither a debit nor a credit")
		if debit < 0 or credit < 0:
			raise ToolError(
				f"accounts[{index}] ({account}) has a negative amount. Move the sign "
				"to the other side of the entry instead."
			)
		if frappe.db.get_value("Account", account, "is_group"):
			raise ToolError(
				f"accounts[{index}] ({account}) is a group account; ERPNext posts only to leaf accounts"
			)
		line = {key: value for key, value in entry.items() if key in _LINE_FIELDS}
		line["account"] = account
		line["debit"] = debit
		line["credit"] = credit
		line.update(_validated_dimensions(entry.get(_DIMENSIONS_KEY), index))
		lines.append(line)
	return lines


def _validated_dimensions(raw, index: int) -> dict:
	"""Check a line's `dimensions` object against this site's own meta.

	Two checks, both of which exist because the failure they prevent is silent.
	The field has to exist on Journal Entry Account — a dimension the operator has
	not created yet would otherwise be written to a Document attribute that never
	reaches a column, and the entry would look filed and not be. And where the
	field is a Link, the value has to be a record of what it links to, because
	ERPNext's own link validation runs on *submit*, so a bad value here produces a
	draft that cannot be posted rather than a call that failed.
	"""
	if raw in (None, ""):
		return {}
	if not isinstance(raw, dict):
		raise ToolError(
			f"accounts[{index}].{_DIMENSIONS_KEY} must be an object of fieldname → "
			f'value, e.g. {{"member": "Member-01", "bbch_stage": "BBCH-8"}}; got '
			f"{type(raw).__name__}"
		)

	out = {}
	for key in sorted(raw):
		fieldname = str(key or "").strip()
		field = compat.field_meta(_LINE_DOCTYPE, fieldname)
		if field is None:
			raise ToolError(
				f"accounts[{index}].{_DIMENSIONS_KEY} has no field {fieldname!r} on "
				f"{_LINE_DOCTYPE}. An accounting dimension only becomes a field once it "
				"exists — create it with create_accounting_dimension, which adds the "
				"Link field to this doctype. Nothing was created."
			)
		value = raw[key]
		text = "" if value is None else str(value).strip()
		options = str(field.get("options") or "")
		if text and str(field.get("fieldtype") or "") == "Link" and options:
			if not frappe.db.exists(options, text):
				raise ToolError(
					f"accounts[{index}].{_DIMENSIONS_KEY}.{fieldname} is {text!r}, which "
					f"is not a {options} on this site. Dimension values are {options} "
					"records — create one with create_dimension_value. Nothing was created."
				)
		out[fieldname] = text or None
	return out


def _assert_balanced(lines: list[dict]) -> tuple[float, float]:
	total_debit = sum(float(line["debit"]) for line in lines)
	total_credit = sum(float(line["credit"]) for line in lines)
	if abs(total_debit - total_credit) > BALANCE_TOLERANCE:
		raise ToolError(
			f"debits ({round(total_debit, 2)}) do not equal credits "
			f"({round(total_credit, 2)}); difference "
			f"{round(total_debit - total_credit, 2)}. Nothing was created."
		)
	return total_debit, total_credit


# ── 12. submit_journal_entry ────────────────────────────────────────────────
def submit_journal_entry(args: dict) -> ToolResult:
	"""Submit an existing DRAFT Journal Entry (docstatus 0 → 1).

	This is the tool that moves a balance, and it is the narrowest possible
	version of that power: it takes a name, not a document. It cannot create the
	entry it submits, so enabling it means "post things a human or an earlier
	tool call already wrote down", never "post something new right now".
	"""
	name = as_str(args, "name", required=True)
	doc = _journal_entry(name)
	if int(doc.docstatus or 0) == 1:
		raise ToolError(f"Journal Entry {name} is already submitted")
	if int(doc.docstatus or 0) == 2:
		raise ToolError(f"Journal Entry {name} is cancelled and cannot be submitted")

	doc.submit()
	data = {
		"name": doc.name,
		"docstatus": 1,
		"docstatus_label": "submitted",
		"company": doc.company,
		"posting_date": str(doc.posting_date),
		"total_debit": doc.total_debit,
		"total_credit": doc.total_credit,
		"gl_entries_created": frappe.db.count(
			"GL Entry", {"voucher_type": "Journal Entry", "voucher_no": doc.name}
		),
	}
	return ToolResult(
		data,
		f"submitted Journal Entry {doc.name} ({doc.company}, {doc.posting_date}, {doc.total_debit})",
		docstatus_delta="0 → 1 (submitted)",
	)


# ── 13. cancel_journal_entry ────────────────────────────────────────────────
def cancel_journal_entry(args: dict) -> ToolResult:
	"""Cancel a submitted Journal Entry (docstatus 1 → 2), recording why.

	`reason` is mandatory and is written twice: to the document's own comment
	thread, where an accountant looking at the JE will see it, and to the audit
	log, where it survives even if the document is later deleted. A cancellation
	nobody can explain is the thing that makes a year-end close miserable.
	"""
	name = as_str(args, "name", required=True)
	reason = as_str(args, "reason", required=True)
	if len(reason) < 4:
		raise ToolError("reason must be a real explanation, not a placeholder")
	doc = _journal_entry(name)
	if int(doc.docstatus or 0) == 0:
		raise ToolError(
			f"Journal Entry {name} is a draft, not a submitted entry. Drafts affect "
			"no balance; delete it in ERPNext if it is not wanted."
		)
	if int(doc.docstatus or 0) == 2:
		raise ToolError(f"Journal Entry {name} is already cancelled")

	doc.cancel()
	try:
		doc.add_comment("Comment", f"Cancelled via MCP (erpnext_mcp). Reason: {reason}")
	except Exception:
		# The cancellation itself succeeded and is the operation the caller
		# asked for; a failed comment must not report it as a failure. The
		# reason is in the audit log regardless.
		frappe.log_error(
			title="erpnext_mcp: could not attach cancellation reason comment",
			message=compat.traceback_text(),
		)

	data = {
		"name": doc.name,
		"docstatus": 2,
		"docstatus_label": "cancelled",
		"company": doc.company,
		"posting_date": str(doc.posting_date),
		"reason": reason,
		"note": ("ERPNext keeps cancelled entries and their reversing GL rows; nothing was deleted."),
	}
	return ToolResult(
		data,
		f"cancelled Journal Entry {doc.name} — {reason}",
		docstatus_delta="1 → 2 (cancelled)",
	)


def _journal_entry(name: str):
	if not frappe.db.exists("Journal Entry", name):
		raise ToolError(f"no Journal Entry named {name!r}")
	return frappe.get_doc("Journal Entry", name)


# ── 14. create_bank_transaction ─────────────────────────────────────────────
def create_bank_transaction(args: dict) -> ToolResult:
	"""Insert a Bank Transaction as a DRAFT.

	`amount` is signed the way a human reads a statement — positive is money in,
	negative is money out — and is mapped onto whichever columns this ERPNext
	version has (`deposit`/`withdrawal`, or a single signed `amount`).

	Left as a draft for the same reason a Journal Entry is: a submitted Bank
	Transaction is eligible for reconciliation and will start matching against
	payments. Submitting is a human step in ERPNext; this app deliberately ships
	no tool for it.
	"""
	compat.require_doctype("Bank Transaction", "It ships with ERPNext's Accounts module.")
	bank_account = _resolve_bank_account(as_str(args, "bank_account", required=True))
	date = as_date(args, "date", required=True)
	description = as_str(args, "description", required=True)
	reference_no = as_str(args, "reference_no")
	amount = as_float(args.get("amount"), "amount")
	if not amount:
		raise ToolError("amount must be non-zero (positive = money in, negative = money out)")

	money = compat.bank_transaction_amount_fields()
	doc = frappe.new_doc("Bank Transaction")
	doc.bank_account = bank_account
	doc.date = date
	doc.description = description
	if money["style"] == "deposit_withdrawal":
		doc.set(money["deposit"], amount if amount > 0 else 0)
		doc.set(money["withdrawal"], -amount if amount < 0 else 0)
	else:
		doc.set(money["amount"], amount)
	if reference_no and compat.has_field("Bank Transaction", "reference_number"):
		doc.reference_number = reference_no

	if compat.has_field("Bank Transaction", "company"):
		company = frappe.db.get_value("Bank Account", bank_account, "company")
		doc.company = company or resolve_company(as_str(args, "company"), required=True)
	if compat.has_field("Bank Transaction", "currency") and not doc.get("currency"):
		account = frappe.db.get_value("Bank Account", bank_account, "account")
		currency = frappe.db.get_value("Account", account, "account_currency") if account else None
		if currency:
			doc.currency = currency

	doc.insert()
	data = {
		"name": doc.name,
		"docstatus": int(doc.docstatus or 0),
		"docstatus_label": "draft",
		"bank_account": bank_account,
		"date": date,
		"amount_signed": amount,
		"amount_layout": money["style"],
		"description": description,
		"reference_no": reference_no or None,
		"next_step": (
			"Draft Bank Transactions are not reconcilable. Submit it in ERPNext to "
			"include it in bank reconciliation."
		),
	}
	return ToolResult(
		data,
		f"created draft Bank Transaction {doc.name} on {bank_account}: {amount} on {date}",
		docstatus_delta="none → 0 (draft)",
	)


# ── 15. reconcile_bank_transaction ──────────────────────────────────────────
def reconcile_bank_transaction(args: dict) -> ToolResult:
	"""Attach payment vouchers to a Bank Transaction.

	Hands the work to ERPNext's own `BankTransaction.add_payment_entries` when
	the site's version has it, because that method is where clearance dates,
	allocation arithmetic and the transaction's status live. Reimplementing that
	here would mean guessing at the parts of reconciliation ERPNext does after
	the child row is written — and getting one of them wrong leaves a
	transaction that looks reconciled and isn't. The append-and-save fallback is
	only for versions predating the method.
	"""
	compat.require_doctype("Bank Transaction", "It ships with ERPNext's Accounts module.")
	name = as_str(args, "name", required=True)
	if not frappe.db.exists("Bank Transaction", name):
		raise ToolError(f"no Bank Transaction named {name!r}")
	doc = frappe.get_doc("Bank Transaction", name)
	if int(doc.docstatus or 0) == 2:
		raise ToolError(f"Bank Transaction {name} is cancelled")

	vouchers = _validated_vouchers(args.get("payment_entries"))
	money = compat.bank_transaction_amount_fields()
	gross = round(compat.gross_amount(doc.as_dict(), money), 2)
	already = round(float(doc.get(money["allocated"]) or 0), 2) if money["allocated"] else 0.0
	requested = round(sum(v["allocated_amount"] for v in vouchers), 2)
	if requested - (gross - already) > 0.005:
		raise ToolError(
			f"allocating {requested} would exceed Bank Transaction {name}'s "
			f"remaining {round(gross - already, 2)} (gross {gross}, already "
			f"allocated {already}). Nothing was changed."
		)

	before = int(doc.docstatus or 0)
	# `callable(getattr(...))` rather than `hasattr`: a Frappe Document that
	# resolves unknown attributes to None (which several dict-backed subclasses
	# do) would pass hasattr and then fail on the call.
	delegate = getattr(doc, "add_payment_entries", None)
	if callable(delegate):
		delegate(vouchers)
		doc.reload()
	else:
		for voucher in vouchers:
			doc.append("payment_entries", voucher)
		doc.save()

	unallocated = round(float(doc.get(money["unallocated"]) or 0), 2) if money["unallocated"] else None
	data = {
		"name": doc.name,
		"bank_account": doc.get("bank_account"),
		"gross_amount": gross,
		"allocated_now": requested,
		"allocated_total": (
			round(float(doc.get(money["allocated"]) or 0), 2) if money["allocated"] else None
		),
		"unallocated_amount": unallocated,
		"status": doc.get("status"),
		"payment_entries": [
			{
				"payment_document": row.get("payment_document"),
				"payment_entry": row.get("payment_entry"),
				"allocated_amount": row.get("allocated_amount"),
			}
			for row in (doc.get("payment_entries") or [])
		],
		"applied_via": (
			"ERPNext add_payment_entries" if callable(delegate) else "append + save (legacy ERPNext)"
		),
	}
	after = int(doc.docstatus or 0)
	return ToolResult(
		data,
		f"reconciled {requested} against Bank Transaction {doc.name} "
		f"({len(vouchers)} voucher(s)); status {doc.get('status')}",
		docstatus_delta=f"{before} → {after}" if before != after else "",
	)


def _validated_vouchers(raw) -> list[dict]:
	"""Check the `payment_entries` argument and confirm each voucher exists."""
	if not isinstance(raw, list) or not raw:
		raise ToolError(
			"payment_entries must be a non-empty list, e.g. "
			'[{"payment_document": "Payment Entry", "payment_entry": "PE-0001", '
			'"allocated_amount": 250.00}]'
		)
	out = []
	for index, entry in enumerate(raw, start=1):
		if not isinstance(entry, dict):
			raise ToolError(f"payment_entries[{index}] must be an object, got {type(entry).__name__}")
		doctype = str(entry.get("payment_document") or "").strip()
		docname = str(entry.get("payment_entry") or "").strip()
		if not doctype or not docname:
			raise ToolError(
				f"payment_entries[{index}] needs both payment_document (the voucher "
				"doctype, e.g. 'Payment Entry') and payment_entry (its name)"
			)
		if not compat.doctype_exists(doctype):
			raise ToolError(f"payment_entries[{index}]: no such DocType {doctype!r} on this site")
		if not frappe.db.exists(doctype, docname):
			raise ToolError(f"payment_entries[{index}]: no {doctype} named {docname!r}")
		allocated = as_float(entry.get("allocated_amount"), f"payment_entries[{index}].allocated_amount")
		if allocated <= 0:
			raise ToolError(f"payment_entries[{index}].allocated_amount must be positive")
		out.append(
			{
				"payment_document": doctype,
				"payment_entry": docname,
				"allocated_amount": allocated,
			}
		)
	return out
