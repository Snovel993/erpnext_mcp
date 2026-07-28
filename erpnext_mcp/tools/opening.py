# SPDX-License-Identifier: MIT
"""Where a set of books starts: the balances that were true before day one.

WHY THIS IS NOT JUST `create_journal_entry`. It could be, and that is the
problem. An opening balance is a journal entry with three properties a hand-built
one keeps losing:

  * **It balances against equity, not against something.** Every historical fact
    being brought onto the books — equipment transferred in, proceeds of a sale
    that happened before this ledger existed, the starting value of a portfolio —
    has a real side and a plug side, and the plug is always Opening Balance
    Equity. A caller computing the plug themselves gets it wrong by a few cents
    on the third entry and the ledger never balances again.
  * **It is flagged as opening.** ERPNext's `is_opening` and the `Opening Entry`
    voucher type are what keep these amounts out of the period's activity in
    reports that separate the two. Nothing warns you when they are missing; the
    P&L simply reads as though the company earned its opening equity in January.
  * **It is one event, not one line.** "The equipment came over from PFI" is six
    asset accounts and one equity line, and it wants one remark explaining the
    whole thing rather than six lines each explaining a sixth of it.

So this takes the real side and computes the plug, and refuses everything that
would make the plug meaningless.

WHAT IT STILL DOES NOT DO. Submit. Like every other tool in this app it produces
a DRAFT — and an opening balance is the single entry most worth a human reading
before it moves a balance, because it is the one nobody will ever re-derive.

THE OPENING EQUITY ACCOUNT IS FOUND, NOT GUESSED. `3300` by number first, then a
leaf Equity account named after it, and anything other than exactly one match is
refused with the candidates listed. Posting the plug to whichever equity account
sorted first is an error that surfaces months later as an equity statement nobody
can explain.
"""

import frappe

from .. import compat
from ..args import as_date, as_float, as_str, resolve_account, resolve_company, resolve_cost_center
from ..errors import ToolError
from ..result import ToolResult
from . import mutate

#: The account number this app's own chart template gives Opening Balance Equity,
#: and the conventional one. Tried first, because a number is unambiguous where a
#: name is a spelling.
DEFAULT_OPENING_EQUITY_NUMBER = "3300"

#: How the opening equity account is recognised when it is not numbered 3300.
#: Matched against the account NAME, case-insensitively, over the company's leaf
#: Equity accounts only.
_OPENING_EQUITY_KEYWORDS = (
	"opening balance equity",
	"opening balances equity",
	"opening equity",
	"opening balance",
)

#: What a caller may put on one entry. Anything else is refused by name rather
#: than dropped: a model that sent `debit` meaning `dr_or_cr: "dr"` should be told
#: the field it wants, not have half its entry silently ignored.
_ENTRY_FIELDS = ("account", "dr_or_cr", "amount", "cost_center", "dimensions", "narrative")

_DEBIT_WORDS = ("dr", "debit", "d")
_CREDIT_WORDS = ("cr", "credit", "c")

#: Rounding tolerance on the computed plug, matching the double-entry check in
#: `mutate`. Below this the entries already balance and no plug is written.
BALANCE_TOLERANCE = mutate.BALANCE_TOLERANCE


# ── 66. set_opening_balance ─────────────────────────────────────────────────
def set_opening_balance(args: dict) -> ToolResult:
	"""Book one historical event as a DRAFT opening-balance Journal Entry."""
	company = resolve_company(as_str(args, "company"), required=True)
	posting_date = as_date(args, "posting_date", required=True)
	user_remark = as_str(args, "user_remark", required=True)
	if len(user_remark) < 8:
		raise ToolError(
			"user_remark must be a real explanation of the event these balances came from — "
			"'equipment transferred from PFI on dissolution', not 'opening'. It is the only "
			"part of an opening balance nobody can reconstruct later. Nothing was created."
		)

	entries = _validated_entries(args.get("entries"), company)
	equity_account, equity_source = _opening_equity_account(args, company)

	total_debit = round(sum(entry["debit"] for entry in entries), 2)
	total_credit = round(sum(entry["credit"] for entry in entries), 2)
	difference = round(total_debit - total_credit, 2)

	raw_lines = [_raw_line(entry) for entry in entries]
	plug = None
	if abs(difference) > BALANCE_TOLERANCE:
		plug = {
			"account": equity_account,
			("credit" if difference > 0 else "debit"): abs(difference),
			"user_remark": f"Opening balance: {user_remark}"[:140],
		}
		raw_lines.append(plug)
	elif len(raw_lines) < 2:
		raise ToolError(
			"a single entry that balances against nothing is not an opening balance. Pass the "
			"real side of the event and let the offsetting line be computed, or use "
			"create_journal_entry if this is an ordinary posting. Nothing was created."
		)

	lines = mutate.validated_journal_lines(raw_lines, company)
	extras = _opening_extras()
	doc = mutate.insert_draft_journal_entry(company, posting_date, lines, user_remark, extras)

	data = {
		"name": doc.name,
		"docstatus": 0,
		"docstatus_label": "draft",
		"company": company,
		"posting_date": posting_date,
		"opening_equity_account": equity_account,
		"opening_equity_resolved_by": equity_source,
		"opening_equity_side": (
			None if plug is None else ("credit" if difference > 0 else "debit")
		),
		"opening_equity_amount": None if plug is None else abs(difference),
		"entries": [
			{
				"account": entry["account"],
				"debit": entry["debit"],
				"credit": entry["credit"],
				"cost_center": entry["cost_center"] or None,
				"dimensions": entry["dimensions"] or None,
				"narrative": entry["narrative"] or None,
			}
			for entry in entries
		],
		"line_count": len(lines),
		"total_debit": round(sum(float(line["debit"]) for line in lines), 2),
		"total_credit": round(sum(float(line["credit"]) for line in lines), 2),
		"entered_debit": total_debit,
		"entered_credit": total_credit,
		"balancing_difference": difference,
		"flags_set": extras,
		"user_remark": user_remark,
		"note": (
			(
				f"The {abs(difference)} offsetting line against {equity_account} was computed, "
				"not supplied: every historical fact brought onto a set of books balances "
				"against opening equity, and computing the plug is what stops the ledger "
				"drifting a few cents per event."
				if plug is not None
				else (
					"The entries already balanced, so no offsetting line was written and "
					f"{equity_account} was not touched."
				)
			)
			+ (
				f" The entry is flagged {extras}, which is what keeps these amounts out of the "
				"period's activity in reports that separate opening balances from trading."
				if extras
				else " This ERPNext has no is_opening flag to set, so the entry is an ordinary "
				"journal entry as far as reporting is concerned."
			)
		),
		"next_step": (
			f"Journal Entry {doc.name} is a DRAFT and has moved no balance. Read it, then post it "
			"with submit_journal_entry. An opening balance is the entry nobody will ever "
			"re-derive, so it is worth reading twice."
		),
	}
	return ToolResult(
		data,
		f"created draft opening-balance Journal Entry {doc.name} for {company} on {posting_date}: "
		f"{len(entries)} entry line(s), "
		+ (
			f"{abs(difference)} {'credited to' if difference > 0 else 'debited from'} {equity_account}"
			if plug is not None
			else "already balanced, no equity plug"
		),
		docstatus_delta="none → 0 (draft)",
	)


def _raw_line(entry: dict) -> dict:
	line = {"account": entry["account"]}
	if entry["debit"]:
		line["debit"] = entry["debit"]
	else:
		line["credit"] = entry["credit"]
	if entry["cost_center"]:
		line["cost_center"] = entry["cost_center"]
	if entry["narrative"]:
		line["user_remark"] = entry["narrative"]
	if entry["dimensions"]:
		line["dimensions"] = entry["dimensions"]
	return line


def _validated_entries(raw, company: str) -> list[dict]:
	"""Check every entry all the way through before anything is written."""
	if not isinstance(raw, list) or not raw:
		raise ToolError(
			"entries must be a non-empty list of objects, e.g. "
			'[{"account": "1710", "dr_or_cr": "dr", "amount": 52650, '
			'"narrative": "Equipment transferred from PFI"}]. The offsetting line against '
			"opening equity is computed — do not include it."
		)

	out = []
	for index, entry in enumerate(raw, start=1):
		if not isinstance(entry, dict):
			raise ToolError(f"entries[{index}] must be an object, got {type(entry).__name__}")
		unknown = sorted(set(entry) - set(_ENTRY_FIELDS))
		if unknown:
			raise ToolError(
				f"entries[{index}] has unsupported field(s): {', '.join(unknown)}. Supported: "
				f"{', '.join(_ENTRY_FIELDS)}. A debit or a credit is chosen with dr_or_cr and "
				"the amount is always positive."
			)

		account = resolve_account(str(entry.get("account") or ""), company)
		_validate_entry_account(account, company, index)

		side = _side(entry.get("dr_or_cr"), index)
		amount = as_float(entry.get("amount"), f"entries[{index}].amount")
		if amount <= 0:
			raise ToolError(
				f"entries[{index}].amount must be positive, got {amount}. The direction is "
				"dr_or_cr; a negative amount is a caller trying to say it twice. Nothing was "
				"created."
			)

		cost_center = ""
		if entry.get("cost_center"):
			cost_center = _validated_cost_center(str(entry["cost_center"]), company, index)

		dimensions = entry.get("dimensions")
		if dimensions not in (None, "") and not isinstance(dimensions, dict):
			raise ToolError(
				f"entries[{index}].dimensions must be an object of fieldname → value, e.g. "
				'{"member": "Member-01"}; got ' + type(dimensions).__name__
			)

		out.append(
			{
				"account": account,
				"debit": amount if side == "debit" else 0.0,
				"credit": amount if side == "credit" else 0.0,
				"cost_center": cost_center,
				"dimensions": dict(dimensions) if dimensions else {},
				"narrative": str(entry.get("narrative") or "").strip(),
			}
		)
	return out


def _side(raw, index: int) -> str:
	text = str(raw or "").strip().lower()
	if text in _DEBIT_WORDS:
		return "debit"
	if text in _CREDIT_WORDS:
		return "credit"
	raise ToolError(
		f"entries[{index}].dr_or_cr must say which side the amount goes on: "
		f"{'/'.join(_DEBIT_WORDS)} or {'/'.join(_CREDIT_WORDS)}. Got {raw!r}. Nothing was created."
	)


def _validate_entry_account(docname: str, company: str, index: int) -> None:
	account = (
		frappe.db.get_value(
			"Account",
			docname,
			compat.existing_fields(
				"Account", ("name", "company", "is_group", "root_type", "disabled")
			),
			as_dict=True,
		)
		or {}
	)
	if str(account.get("company") or "") != company:
		raise ToolError(
			f"entries[{index}]: account {docname!r} belongs to company "
			f"{account.get('company')!r}, not {company!r}. Nothing was created."
		)
	if int(account.get("is_group") or 0):
		raise ToolError(
			f"entries[{index}]: {docname!r} is a group account, and ERPNext posts only to leaf "
			"accounts. Nothing was created."
		)
	if int(account.get("disabled") or 0):
		raise ToolError(
			f"entries[{index}]: {docname!r} is disabled, so nothing can post to it. Nothing was "
			"created."
		)


def _validated_cost_center(requested: str, company: str, index: int) -> str:
	docname = resolve_cost_center(requested, company)
	row = (
		frappe.db.get_value(
			"Cost Center",
			docname,
			compat.existing_fields("Cost Center", ("name", "company", "is_group", "disabled")),
			as_dict=True,
		)
		or {}
	)
	if str(row.get("company") or "") != company:  # pragma: no cover - resolve refuses first
		raise ToolError(
			f"entries[{index}]: cost center {docname!r} belongs to {row.get('company')!r}, not "
			f"{company!r}. Nothing was created."
		)
	if int(row.get("is_group") or 0):
		raise ToolError(
			f"entries[{index}]: {docname!r} is a group cost center, and ERPNext files postings "
			"against leaf cost centers only. Nothing was created."
		)
	if int(row.get("disabled") or 0):
		raise ToolError(
			f"entries[{index}]: {docname!r} is a disabled cost center, so nothing can be filed "
			"against it. Nothing was created."
		)
	return docname


def _opening_equity_account(args: dict, company: str) -> tuple:
	"""The account the plug lands in: named by the caller, or found by number then name."""
	explicit = as_str(args, "opening_equity_account")
	if explicit:
		docname = resolve_account(explicit, company)
		_validate_equity_account(docname, company, "opening_equity_account")
		return docname, "argument"

	candidates = _leaf_equity_accounts(company)
	numbered = [
		row
		for row in candidates
		if str(row.get("account_number") or "").strip() == DEFAULT_OPENING_EQUITY_NUMBER
	]
	if len(numbered) == 1:
		return numbered[0]["name"], f"account_number {DEFAULT_OPENING_EQUITY_NUMBER}"

	matches = [
		row
		for row in candidates
		if any(keyword in str(row.get("account_name") or "").lower() for keyword in _OPENING_EQUITY_KEYWORDS)
	]
	if len(matches) == 1:
		return matches[0]["name"], "account name match"

	listing = ", ".join(row["name"] for row in candidates) or "<none>"
	if not matches:
		raise ToolError(
			f"could not find an Opening Balance Equity account for {company}: no leaf Equity "
			f"account is numbered {DEFAULT_OPENING_EQUITY_NUMBER} or named after opening "
			"balances. Name it with opening_equity_account, or create one with "
			f"create_account(account_number='{DEFAULT_OPENING_EQUITY_NUMBER}', "
			"account_name='Opening Balance Equity', root_type='Equity'). Leaf equity accounts "
			f"on this company: {listing}. Nothing was created."
		)
	raise ToolError(
		f"{len(matches)} leaf Equity accounts could be the opening balance equity account for "
		f"{company}: {', '.join(row['name'] for row in matches)}. Name the one you mean with "
		"opening_equity_account. Nothing was created."
	)


def _leaf_equity_accounts(company: str) -> list[dict]:
	fields = compat.existing_fields(
		"Account", ("name", "account_name", "account_number", "is_group", "disabled", "root_type")
	)
	rows = frappe.db.get_all(
		"Account",
		filters={"company": company, "root_type": "Equity"},
		fields=fields,
		order_by="name asc",
		limit=500,
	)
	return [
		dict(row)
		for row in rows
		if not int(row.get("is_group") or 0) and not int(row.get("disabled") or 0)
	]


def _validate_equity_account(docname: str, company: str, label: str) -> None:
	account = (
		frappe.db.get_value(
			"Account",
			docname,
			compat.existing_fields(
				"Account", ("name", "company", "is_group", "root_type", "disabled")
			),
			as_dict=True,
		)
		or {}
	)
	if str(account.get("company") or "") != company:
		raise ToolError(
			f"{label}: {docname!r} belongs to company {account.get('company')!r}, not "
			f"{company!r}. Nothing was created."
		)
	if int(account.get("is_group") or 0):
		raise ToolError(f"{label}: {docname!r} is a group account. Nothing was created.")
	if int(account.get("disabled") or 0):
		raise ToolError(f"{label}: {docname!r} is disabled. Nothing was created.")
	root_type = str(account.get("root_type") or "")
	if root_type != "Equity":
		raise ToolError(
			f"{label}: {docname!r} is {root_type or 'untyped'}, and the offsetting side of an "
			"opening balance is equity by definition — it is the accumulated result of "
			"everything that happened before these books started. Nothing was created."
		)


def _opening_extras() -> dict:
	"""The flags that mark this entry as an opening balance, where this site has them.

	Both are conditional on the site's own meta. `is_opening` has been on Journal
	Entry for as long as this app supports, but the `Opening Entry` voucher type is
	an option on a Select, and an option a site has removed would make the insert
	fail on a field that is decoration rather than substance.
	"""
	extras = {}
	if compat.has_field("Journal Entry", "is_opening"):
		extras["is_opening"] = "Yes"
	voucher_type = compat.field_meta("Journal Entry", "voucher_type") or {}
	options = [line.strip() for line in str(voucher_type.get("options") or "").split("\n") if line.strip()]
	if "Opening Entry" in options:
		extras["voucher_type"] = "Opening Entry"
	return extras
