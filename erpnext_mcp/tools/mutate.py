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

EVERY LINE CARRIES BOTH AMOUNT COLUMNS. A Journal Entry Account row stores each
amount twice — `debit` in the company's currency and `debit_in_account_currency`
in the account's — and ERPNext's `set_amounts_in_account_currency` derives the
first FROM the second on every validate, multiplying by the line's exchange rate.
A line built with `debit` alone therefore inserts looking correct and is written
to the database with its debit silently zeroed, and the entry is refused on
submit with *"Row N: Both Debit and Credit values cannot be zero"* — a draft that
cannot be posted and does not say why. That shipped in v0.8.0 and cost a day of
somebody's life posting opening balances by hand. `validated_journal_lines` now
fills both columns for every line it returns, which is why the fix lives here
rather than in the tool that surfaced it: every Journal Entry this app writes —
opening balances, member events, depreciation, loan payments — comes through this
one function, and one of them getting it right would have left the rest wrong.
"""

import frappe

from .. import compat, settings
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

#: How many entries `bulk_submit_journal_entries` will take in one call. Not a
#: performance limit — each submit is its own transaction and the loop would run
#: all day. It is a legibility limit: a batch whose failures a human has to read
#: through should fit on a screen, and a caller with two thousand drafts is
#: better served by four calls it can check between than by one it cannot.
_MAX_BULK = 500


# ── 11. create_journal_entry ────────────────────────────────────────────────
def create_journal_entry(args: dict) -> ToolResult:
	"""Create a DRAFT Journal Entry. Never submits, whatever it is asked."""
	company = resolve_company(as_str(args, "company"), required=True)
	posting_date = as_date(args, "posting_date", required=True)
	user_remark = as_str(args, "user_remark", required=True)
	lines = validated_journal_lines(args.get("accounts"), company)
	total_debit, total_credit = assert_balanced(lines)
	dimension_fields = sorted({key for line in lines for key in line if key not in _LINE_FIELDS})

	extras = {}
	if as_str(args, "voucher_type"):
		extras["voucher_type"] = as_str(args, "voucher_type")
	for field in ("cheque_no", "cheque_date", "bill_no"):
		value = as_str(args, field)
		if value:
			extras[field] = as_date(args, field) if field.endswith("_date") else value

	doc = insert_draft_journal_entry(company, posting_date, lines, user_remark, extras)

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


def insert_draft_journal_entry(
	company: str,
	posting_date: str,
	lines: list[dict],
	user_remark: str,
	extras: dict | None = None,
):
	"""Insert one balanced DRAFT Journal Entry. The only place this app writes one.

	Public, and used by `tools/governance.py` and `tools/assets.py` as well as by
	`create_journal_entry`, so that every Journal Entry this app produces —
	whatever asked for it — goes through the same insert and the same
	never-submitted assertion below. A second implementation elsewhere would be a
	second chance to ship one that posts.

	`lines` must already have been through `validated_journal_lines`.
	"""
	assert_balanced(lines)
	doc = frappe.new_doc("Journal Entry")
	doc.company = company
	doc.posting_date = posting_date
	doc.user_remark = user_remark
	for field, value in (extras or {}).items():
		doc.set(field, value)
	for line in lines:
		doc.append("accounts", line)

	doc.insert()
	# Belt to the "never submits" braces: if a future ERPNext hook ever
	# submitted on insert, this turns a silent posting into a loud failure.
	if int(doc.docstatus or 0) != 0:
		raise ToolError(
			f"Journal Entry {doc.name} was created with docstatus {doc.docstatus}, "
			"but this app only ever produces draft journal entries. Refusing to "
			"report success — inspect the site's Journal Entry hooks."
		)
	return doc


def validated_journal_lines(raw, company: str) -> list[dict]:
	"""Coerce and check the `accounts` argument into Journal Entry Account rows.

	Public for the same reason `insert_draft_journal_entry` is: the member-event
	and depreciation tools build their own lines and must have them checked by
	exactly the code that checks a hand-written entry's — group accounts refused,
	dimensions verified against this site's meta, one side per line.
	"""
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
		rate = as_float(entry.get("exchange_rate"), f"accounts[{index}].exchange_rate") or 1.0
		if rate <= 0:
			raise ToolError(
				f"accounts[{index}] ({account}) has exchange_rate {rate}; a rate must be "
				"positive. Nothing was created."
			)
		debit_in_account_currency = as_float(
			entry.get("debit_in_account_currency"), f"accounts[{index}].debit_in_account_currency"
		)
		credit_in_account_currency = as_float(
			entry.get("credit_in_account_currency"), f"accounts[{index}].credit_in_account_currency"
		)
		# A caller who gave only the account-currency side means the same line as
		# one who gave only the company-currency side. Filling the other in here
		# rather than refusing keeps `debit`/`credit` the field pair every message
		# in this module talks about, while accepting the pair ERPNext actually
		# reads.
		#
		# And where both were given, the account-currency one wins the arithmetic,
		# because it is the one ERPNext multiplies out on validate. Letting the
		# two disagree would mean this module's double-entry check ran on one set
		# of numbers and the posting on another — an entry that balances here and
		# does not on the site.
		debit = _reconciled_amount(debit, debit_in_account_currency, rate, account, index, "debit")
		credit = _reconciled_amount(credit, credit_in_account_currency, rate, account, index, "credit")
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
		if not entry.get("exchange_rate"):
			_assert_company_currency(account, company, index)
		line = {key: value for key, value in entry.items() if key in _LINE_FIELDS}
		line["account"] = account
		line["debit"] = debit
		line["credit"] = credit
		line.update(
			_account_currency_amounts(
				debit, credit, rate, debit_in_account_currency, credit_in_account_currency
			)
		)
		line.update(_validated_dimensions(entry.get(_DIMENSIONS_KEY), index))
		lines.append(line)
	return lines


def _reconciled_amount(
	amount: float, in_account_currency: float, rate: float, account: str, index: int, side: str
) -> float:
	"""One side's company-currency figure, agreed with its account-currency twin.

	Absent either way it stays absent — a line has one side, and the other's zero
	is not a disagreement. Given only in the account's currency it is multiplied
	out. Given both ways and they differ, it is a refusal rather than a choice:
	the caller sent two numbers that cannot both be true, and the one this module
	would have to discard is the one the ledger would actually have posted.
	"""
	if not in_account_currency:
		return amount
	converted = round(in_account_currency * rate, 2)
	if amount and round(amount, 2) != converted:
		raise ToolError(
			f"accounts[{index}] ({account}) says {side} {amount} and "
			f"{side}_in_account_currency {in_account_currency} at exchange_rate {rate}, which "
			f"comes to {converted}. ERPNext posts the account-currency figure times the rate, so "
			f"these two cannot both be right. Send one of them, or send a pair that agrees. "
			"Nothing was created."
		)
	return converted


def _assert_company_currency(account: str, company: str, index: int) -> None:
	"""Refuse a foreign-currency line that came without an exchange rate.

	The `*_in_account_currency` columns this module now fills in are only equal to
	`debit`/`credit` when the account is in the company's own currency. On a
	foreign account with no rate to divide by, the honest answer is that this app
	does not know what the amount is in that currency — and ERPNext, which looks a
	rate up for itself when it sees `exchange_rate` of 1, would multiply that
	guess back out and post a converted figure nobody chose. Refusing costs a
	round trip; the alternative is a posting that is wrong in a currency the
	caller never mentioned.

	Silent where the site does not have the fields to answer with, because a
	check that cannot be made must not become a check that always fails.
	"""
	if not compat.has_field("Account", "account_currency"):
		return
	account_currency = str(frappe.db.get_value("Account", account, "account_currency") or "").strip()
	if not account_currency:
		return
	company_currency = str(frappe.db.get_value("Company", company, "default_currency") or "").strip()
	if not company_currency or account_currency == company_currency:
		return
	raise ToolError(
		f"accounts[{index}] ({account}) is held in {account_currency} and {company} keeps its "
		f"books in {company_currency}, so this line needs an exchange_rate — the rate that "
		f"turns {account_currency} into {company_currency} on the posting date. Pass it, "
		"together with the amount in the account's own currency as "
		"debit_in_account_currency or credit_in_account_currency. Nothing was created."
	)


def _account_currency_amounts(
	debit: float,
	credit: float,
	rate: float,
	debit_in_account_currency: float,
	credit_in_account_currency: float,
) -> dict:
	"""The `*_in_account_currency` pair for one line. Never omitted. See the module docstring.

	At rate 1 — every line on a single-currency site, which is almost every line —
	the account-currency amount IS the company-currency amount, and it is copied
	rather than computed so no rounding can put a cent between the two columns of
	the same number. Only a genuine foreign-currency line divides, and a caller
	who supplied the account-currency figure themselves keeps it: they know what
	the invoice said, and this does not.
	"""
	if not debit_in_account_currency:
		debit_in_account_currency = debit if rate == 1 else round(debit / rate, 2) if debit else 0.0
	if not credit_in_account_currency:
		credit_in_account_currency = credit if rate == 1 else round(credit / rate, 2) if credit else 0.0
	return {
		"debit_in_account_currency": debit_in_account_currency,
		"credit_in_account_currency": credit_in_account_currency,
		"exchange_rate": rate,
	}


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


def assert_balanced(lines: list[dict]) -> tuple[float, float]:
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


# ── 75. bulk_submit_journal_entries ─────────────────────────────────────────
def bulk_submit_journal_entries(args: dict) -> ToolResult:
	"""Submit many draft Journal Entries in one call, one document at a time.

	WHY THIS EXISTS AND `submit_journal_entry` IS NOT ENOUGH. A migration weekend
	produces drafts in the hundreds — a chart imported, a year of history keyed in,
	an opening balance per account. Posting them one MCP round trip at a time is
	not a different amount of work, it is a different *kind* of work, and the thing
	that actually goes wrong is a human losing track at entry four hundred and
	stopping without knowing which ones went.

	IT CHECKS `submit_journal_entry`'s SWITCH TOO, and fails before touching
	anything if that one is off. That switch is where an operator decided whether
	an AI client may move a balance at all; a second door into the same room with
	its own lock would make the decision meaningless. Same reasoning as
	`submit_member_event`.

	ONE DOCUMENT'S FAILURE IS NOT THE BATCH'S. Each submit runs in its own
	transaction: committed on success, rolled back on failure, then the loop moves
	on. This is the one place in this app that commits mid-call, and it is
	deliberate — the alternative is a batch of five hundred where number four
	hundred fails and the request rolls back the three hundred and ninety-nine
	postings that were fine, which is both surprising and much harder to recover
	from than a report saying which one to look at. It is also what Frappe's own
	bulk submit does.

	NOTHING IS RE-SUBMITTED. An already-submitted entry is reported `ok` and
	`skipped`, not an error: a caller retrying a batch that half-succeeded wants
	the rest posted, not four hundred error messages about work that is already
	done.
	"""
	if not settings.tool_enabled("submit_journal_entry"):
		raise ToolError(
			"this posts Journal Entries, and the submit_journal_entry tool is switched off on "
			"this site. That switch is where an operator decides whether an AI client may move "
			"a balance, so this tool honours it too. An operator must tick "
			"'allow_submit_journal_entry' in ERPNext MCP Settings. Nothing was submitted."
		)
	names = _validated_docnames(args.get("names"))

	results = []
	for name in names:
		results.append(_submit_one(name))

	submitted = [row for row in results if row["ok"] and not row["skipped"]]
	skipped = [row for row in results if row["skipped"]]
	failed = [row for row in results if not row["ok"]]
	data = {
		"total": len(results),
		"submitted": len(submitted),
		"skipped": len(skipped),
		"failed": len(failed),
		"results": results,
		"submitted_names": [row["name"] for row in submitted],
		"failed_names": [row["name"] for row in failed],
		"note": (
			"Each entry was submitted in its own transaction, so the ones that posted have "
			"posted whatever happened to the rest. Nothing here was rolled back by a later "
			"failure, and nothing that failed left a partial posting behind."
		),
		"next_step": (
			f"{len(failed)} entr{'y' if len(failed) == 1 else 'ies'} failed — read `results` for "
			"the reason each gave, fix them, and call this again with just those names. Names "
			"that already posted are safe to include; they come back skipped."
			if failed
			else "Every entry in the batch is posted or was already posted. Nothing is outstanding."
		),
	}
	return ToolResult(
		data,
		f"bulk submit: {len(submitted)} posted, {len(skipped)} already submitted, "
		f"{len(failed)} failed, of {len(results)} asked for",
		docstatus_delta=f"0 → 1 (submitted) × {len(submitted)}",
	)


def _validated_docnames(raw) -> list[str]:
	"""The `names` argument: a non-empty list of docnames, de-duplicated in order."""
	if not isinstance(raw, list) or not raw:
		raise ToolError(
			"names must be a non-empty list of Journal Entry docnames, e.g. "
			'["ACC-JV-2026-00042", "ACC-JV-2026-00043"]. get_journal_entries with '
			"docstatus='draft' lists the ones waiting to be posted."
		)
	if len(raw) > _MAX_BULK:
		raise ToolError(
			f"names has {len(raw)} entries and the limit is {_MAX_BULK} per call. A batch this "
			"size is worth splitting anyway: one failure in the middle is easier to find in "
			f"{_MAX_BULK} results than in {len(raw)}. Nothing was submitted."
		)
	out = []
	for index, value in enumerate(raw, start=1):
		name = str(value or "").strip()
		if not name:
			raise ToolError(f"names[{index}] is empty. Nothing was submitted.")
		if name not in out:
			out.append(name)
	return out


def _submit_one(name: str) -> dict:
	"""Submit one entry, returning a row rather than raising. See `bulk_submit_journal_entries`."""
	row = {"name": name, "ok": False, "skipped": "", "error": None, "docstatus": None}
	try:
		if not frappe.db.exists("Journal Entry", name):
			row["error"] = f"no Journal Entry named {name!r}"
			return row
		doc = frappe.get_doc("Journal Entry", name)
		docstatus = int(doc.docstatus or 0)
		if docstatus == 1:
			row.update(ok=True, skipped="already_submitted", docstatus=1)
			return row
		if docstatus == 2:
			row.update(docstatus=2, error=f"Journal Entry {name} is cancelled and cannot be submitted")
			return row

		doc.submit()
		frappe.db.commit()
		row.update(ok=True, docstatus=1)
		return row
	except Exception as exc:
		# Rolling back here is what makes the next document's submit run against a
		# clean transaction rather than one poisoned by this one's half-write.
		frappe.db.rollback()
		row["error"] = f"{type(exc).__name__}: {exc}"
		return row


# ── 76. delete_draft_journal_entry ──────────────────────────────────────────
def delete_draft_journal_entry(args: dict) -> ToolResult:
	"""Delete a DRAFT Journal Entry outright. Drafts only, and it says what it deleted.

	THE GAP THIS FILLS. `cancel_journal_entry` refuses a draft, correctly — there
	is nothing to reverse, because a draft has moved no balance. But that left an
	unwanted draft with no MCP path at all, and a tool that produced four hundred
	drafts and no way to withdraw them is a tool that makes work rather than doing
	it. So: drafts, and only drafts. A submitted entry is a posting and gets
	cancelled, never deleted, whatever a caller asks for.

	IT IS A REAL DELETE. `frappe.delete_doc`, no soft-delete flag, nothing left in
	the table. What survives is the audit row, which is why `reason` is mandatory
	and why the response — and therefore the log's summary — carries the entry's
	company, date, totals and line count. Once this returns, that row is the only
	description of the document that ever existed, so it has to be one somebody
	can read a year later.
	"""
	name = as_str(args, "name", required=True)
	reason = as_str(args, "reason", required=True)
	if len(reason) < 4:
		raise ToolError("reason must be a real explanation, not a placeholder. Nothing was deleted.")
	doc = _journal_entry(name)
	docstatus = int(doc.docstatus or 0)
	if docstatus == 1:
		raise ToolError(
			f"Journal Entry {name} is submitted: it has written GL Entries and moved balances, "
			"and deleting it would take those balances with it and leave nothing saying why. "
			"Reverse it with cancel_journal_entry, which writes the reversing entries and keeps "
			"the record. Nothing was deleted."
		)
	if docstatus == 2:
		raise ToolError(
			f"Journal Entry {name} is cancelled. ERPNext keeps cancelled entries and their "
			"reversing GL rows on purpose — the pair is the evidence that a posting was made "
			"and undone. Deleting one leaves an audit trail with a hole in it. Nothing was "
			"deleted."
		)

	# Read everything worth keeping BEFORE the document stops existing.
	deleted = {
		"name": doc.name,
		"company": doc.company,
		"posting_date": str(doc.posting_date or ""),
		"voucher_type": doc.get("voucher_type"),
		"user_remark": doc.get("user_remark"),
		"total_debit": float(doc.get("total_debit") or 0),
		"total_credit": float(doc.get("total_credit") or 0),
		"line_count": len(doc.get("accounts") or []),
		"accounts": [
			{
				"account": row.get("account"),
				"debit": float(row.get("debit") or 0),
				"credit": float(row.get("credit") or 0),
			}
			for row in (doc.get("accounts") or [])
		],
	}
	frappe.delete_doc("Journal Entry", name, ignore_permissions=False)

	data = {
		"deleted": deleted,
		"reason": reason,
		"gl_entries_removed": 0,
		"note": (
			"A draft writes no GL Entries, so no balance changed when this was created and none "
			"changed when it was deleted. The MCP Action Log row for this call is now the only "
			"record that the entry existed; it carries the lines above and this reason."
		),
	}
	return ToolResult(
		data,
		f"deleted draft Journal Entry {name} ({deleted['company']}, {deleted['posting_date']}, "
		f"{deleted['line_count']} line(s), {deleted['total_debit']} debit) — {reason}",
		docstatus_delta="0 (draft) → deleted",
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
		if not doctype and entry.get("payment_doctype"):
			# `payment_doctype` is the name the field has almost everywhere else in
			# Frappe, so it is the one a model reaches for first. Naming the right
			# field costs one round trip; the alternative — accepting both — would
			# make this the only tool in the app where a misspelt key is guessed at
			# rather than reported, and the guess would eventually be wrong.
			raise ToolError(
				f"payment_entries[{index}] uses payment_doctype; on ERPNext's Bank Transaction "
				"Payments table the field is called payment_document. Resend it under that name, "
				f"e.g. {{\"payment_document\": {str(entry['payment_doctype'])!r}, \"payment_entry\": "
				'"PE-0001", "allocated_amount": 250.00}. Nothing was changed.'
			)
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
