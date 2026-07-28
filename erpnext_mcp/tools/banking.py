# SPDX-License-Identifier: MIT
"""The record that ties a real bank account to an account on the chart.

WHAT A BANK ACCOUNT ACTUALLY IS IN ERPNEXT, because the name is misleading. It is
not a ledger account and it holds no balance. It is a *mapping*: this institution,
this account number, posts to that GL account. Bank Transactions hang off it,
reconciliation reads it, and a bank feed writes into it. The money lives in the
Account it points at.

WHY PRE-CREATE ONE AT ALL, when a bank feed will make it on first sync. Because
the one a feed makes is named after whatever the feed calls the account, points at
a GL account the feed picked or created, and belongs to no cost center anybody
chose. Renaming it afterwards is possible and repointing it is not: the moment
transactions have been imported against a Bank Account, the GL account it names is
where they will reconcile to, and moving that is a manual re-reconciliation. Ten
minutes before the first sync is the cheap moment.

TWO DOCTYPES, ONE CALL. ERPNext splits the institution (`Bank`) from the account
at it (`Bank Account`), so "Wells Fargo" exists once and the four accounts at it
point back at it. A caller naming a bank this site has never seen means to open
the account, not to be told to create a Bank first — so this creates it, and says
that it did.

THE ROOT-TYPE CHECK IS THE ONE THAT MATTERS. A bank account posts to an Asset;
a credit card posts to a Liability. Pointing a Bank Account at an Income account
is not caught by ERPNext and produces a reconciliation screen where every deposit
increases revenue. It is refused here, by root type, with the account named.
"""

import frappe

from .. import compat
from ..args import as_bool, as_str, resolve_account, resolve_company
from ..errors import ToolError
from ..result import ToolResult

BANK = "Bank"
BANK_ACCOUNT = "Bank Account"

#: Fields read off a Bank Account. Filtered through `compat` before any query:
#: `is_company_account`, `disabled` and `bank_account_no` are all younger than the
#: doctype, and an operator on an old ERPNext should get a degraded answer rather
#: than a SQL error.
_BANK_ACCOUNT_FIELDS = (
	"name",
	"account_name",
	"bank",
	"company",
	"account",
	"iban",
	"bank_account_no",
	"branch_code",
	"is_company_account",
	"is_default",
	"party_type",
	"party",
	"disabled",
)

#: What a GL account has to be for a Bank Account to point at it, by root type.
#:
#: The Asset row's account_type list is not decoration: ERPNext's own link filter
#: on the Bank Account form is `account_type = "Bank"`, and its bank
#: reconciliation tool selects on the same flag. An untyped asset account accepted
#: here would produce a Bank Account that saves, imports transactions and then
#: cannot be reconciled, which is the worst of the three outcomes.
_ACCOUNT_RULES = {
	"Asset": {
		"account_type": ("Bank", "Cash"),
		"what": "a bank or cash account",
	},
	"Liability": {
		# "Credit Card" is not an account_type on every ERPNext — it is absent from
		# v15's list — so this cannot require it. What it can refuse is a Liability
		# that is plainly something else: a payable, a tax account, a loan.
		"account_type": ("Credit Card", "Bank", "Cash", ""),
		"what": "a credit-card liability",
	},
}

#: Frappe's own docname rule, from `frappe.model.naming.validate_name`. Reproduced
#: rather than imported because the failure it prevents arrives, unimported, as a
#: framework throw from three frames down with no mention of which argument caused
#: it.
_FORBIDDEN_NAME_CHARACTERS = "<>"


def _require_bank_doctypes() -> None:
	compat.require_doctype(BANK_ACCOUNT, "It ships with ERPNext's Accounts module.")


def _bank_account_row(name: str) -> dict:
	fields = compat.existing_fields(BANK_ACCOUNT, _BANK_ACCOUNT_FIELDS)
	row = frappe.db.get_value(BANK_ACCOUNT, name, fields, as_dict=True)
	return dict(row) if row else {}


def _describe(row: dict) -> dict:
	"""The Bank Account shape this module returns."""
	return {
		"name": row.get("name"),
		"account_name": row.get("account_name"),
		"bank": row.get("bank"),
		"company": row.get("company"),
		"account": row.get("account") or None,
		"iban": row.get("iban") or None,
		"bank_account_no": row.get("bank_account_no") or None,
		"is_company_account": bool(int(row.get("is_company_account") or 0)),
		"party_type": row.get("party_type") or None,
		"party": row.get("party") or None,
		"disabled": bool(int(row.get("disabled") or 0)),
	}


# ── 65. create_bank_account ─────────────────────────────────────────────────
def create_bank_account(args: dict) -> ToolResult:
	"""Create the Bank Account record a feed and a reconciliation both read.

	Creates the `Bank` too when the institution is new, in the same transaction:
	a failure anywhere below leaves neither, rather than an orphan institution
	with no account at it.
	"""
	_require_bank_doctypes()
	company = resolve_company(as_str(args, "company"), required=True)
	account_name = as_str(args, "account_name", required=True)
	bank_name = as_str(args, "bank_name", required=True)
	iban = as_str(args, "iban")
	disabled = bool(as_bool(args, "disabled", False))
	is_company_account = bool(as_bool(args, "is_company_account", True))
	account_number = _account_number(args)

	party_type, party = _validated_party(args, is_company_account)

	gl_account = _validated_gl_account(args, company, is_company_account)

	duplicate = frappe.db.get_value(
		BANK_ACCOUNT, {"account_name": account_name, "company": company}, "name"
	)
	if duplicate:
		raise ToolError(
			f"{company} already has a Bank Account called {account_name!r} ({duplicate}). Two "
			"records with the same account name are indistinguishable in every picker a "
			"reconciliation uses. Rename this one — the mask is the usual distinguisher, e.g. "
			f"'{account_name} - ••1234' — or edit the existing record. Nothing was created."
		)

	bank, bank_created = _resolve_bank(bank_name)

	sharing = _accounts_sharing_gl_account(gl_account) if gl_account else []

	doc = frappe.new_doc(BANK_ACCOUNT)
	doc.account_name = account_name
	doc.bank = bank
	doc.company = company
	if gl_account:
		doc.account = gl_account
	if iban and compat.has_field(BANK_ACCOUNT, "iban"):
		doc.iban = iban
	if account_number and compat.has_field(BANK_ACCOUNT, "bank_account_no"):
		doc.bank_account_no = account_number
	if compat.has_field(BANK_ACCOUNT, "is_company_account"):
		doc.is_company_account = 1 if is_company_account else 0
	if party_type and compat.has_field(BANK_ACCOUNT, "party_type"):
		doc.party_type = party_type
	if party and compat.has_field(BANK_ACCOUNT, "party"):
		doc.party = party
	if disabled and compat.has_field(BANK_ACCOUNT, "disabled"):
		doc.disabled = 1
	doc.insert()

	row = _bank_account_row(doc.name) or {"name": doc.name}
	data = {
		**_describe(row),
		"bank_created": bank_created,
		"gl_account": gl_account or None,
		"fields_this_site_lacks": sorted(
			field
			for field in ("iban", "bank_account_no", "is_company_account", "disabled")
			if not compat.has_field(BANK_ACCOUNT, field)
		),
		"note": (
			"A Bank Account holds no balance. It maps this institution and account number "
			f"onto {gl_account!r}, which is where the money actually is — every Bank "
			"Transaction imported against this record reconciles to that account."
			if gl_account
			else (
				"No GL account is set, so this record cannot be reconciled to the ledger. That "
				"is only correct for a third-party account recorded for reference."
			)
		),
		"next_step": (
			"Point the bank feed at this record before its first sync. A feed that runs first "
			"creates its own Bank Account, and repointing one that already has transactions "
			"against it means re-reconciling them by hand."
		),
	}
	if sharing:
		data["warning"] = (
			f"{gl_account} is already the GL account of {len(sharing)} other Bank Account(s) "
			f"({', '.join(sharing)}). Two feeds writing transactions that reconcile to one "
			"account is legitimate for a sweep arrangement and is a mistake everywhere else — "
			"the reconciliation screen cannot tell you which account a deposit came from."
		)

	return ToolResult(
		data,
		f"created Bank Account {doc.name} for {company}"
		+ (f" against {gl_account}" if gl_account else "")
		+ (f" (Bank {bank} created)" if bank_created else ""),
		docstatus_delta="none → 0 (created)",
	)


def _account_number(args: dict) -> str:
	"""The account number, from either argument name, refusing a contradiction.

	`account_no` is what a person calls it and `bank_account_no` is what ERPNext
	calls the column. Both are accepted because a caller should not have to know
	which; two *different* values is a caller who has confused two accounts, and
	picking one of them silently is how a feed ends up pointed at the wrong one.
	"""
	account_no = as_str(args, "account_no")
	bank_account_no = as_str(args, "bank_account_no")
	if account_no and bank_account_no and account_no != bank_account_no:
		raise ToolError(
			f"account_no ({account_no!r}) and bank_account_no ({bank_account_no!r}) are both set "
			"and differ. They are two names for the same field — pass one. Nothing was created."
		)
	return account_no or bank_account_no


def _validated_party(args: dict, is_company_account: bool) -> tuple:
	"""A third-party account's party, checked against the site."""
	party_type = as_str(args, "party_type")
	party = as_str(args, "party")
	if not party_type and not party:
		return "", ""
	if is_company_account:
		raise ToolError(
			"party/party_type describe an account belonging to somebody else — a supplier's "
			"account you pay into, a customer's you collect from. This call also sets "
			"is_company_account, which says the opposite. Pass is_company_account=false, or "
			"drop the party. Nothing was created."
		)
	if not party_type or not party:
		raise ToolError(
			"party and party_type go together: party_type is the DocType (e.g. 'Supplier') and "
			"party is the record. Nothing was created."
		)
	if not compat.doctype_exists(party_type):
		raise ToolError(f"no DocType named {party_type!r} on this site. Nothing was created.")
	if not frappe.db.exists(party_type, party):
		raise ToolError(f"no {party_type} named {party!r} on this site. Nothing was created.")
	return party_type, party


def _validated_gl_account(args: dict, company: str, is_company_account: bool) -> str:
	"""The chart-of-accounts account this Bank Account posts to, fully vetted."""
	requested = as_str(args, "account")
	if not requested:
		if is_company_account:
			raise ToolError(
				"account is required for a company bank account: it is the whole point of the "
				"record — the GL account every transaction imported against it reconciles to. "
				"Pass the account number or name (search_accounts turns a description into "
				"one), or set is_company_account=false for a third-party account recorded for "
				"reference only. Nothing was created."
			)
		return ""

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
			f"account {docname!r} belongs to company {account.get('company')!r}, not {company!r}. "
			"Nothing was created."
		)
	if int(account.get("is_group") or 0):
		raise ToolError(
			f"{docname!r} is a group account, and ERPNext posts only to leaf accounts — a bank "
			"account mapped onto a heading could never be reconciled. Nothing was created."
		)
	if int(account.get("disabled") or 0):
		raise ToolError(
			f"{docname!r} is disabled, so nothing can post to it. Re-enable it with "
			"update_account(disabled=false) first. Nothing was created."
		)

	root_type = str(account.get("root_type") or "")
	rule = _ACCOUNT_RULES.get(root_type)
	if rule is None:
		raise ToolError(
			f"{docname!r} is {root_type or 'an untyped'} account. A bank account is an Asset and "
			"a credit card is a Liability; anything else produces a reconciliation screen where "
			"every deposit moves the wrong subtotal. Nothing was created."
		)

	account_type = str(account.get("account_type") or "")
	if account_type not in rule["account_type"]:
		wanted = ", ".join(value for value in rule["account_type"] if value)
		raise ToolError(
			f"{docname!r} is {root_type} but its account_type is {account_type or 'unset'}, and "
			f"a Bank Account pointed at {rule['what']} needs one of: {wanted}. ERPNext's own "
			"account picker on this form filters on that flag, and its bank reconciliation tool "
			"selects on it — an account without it saves here and then cannot be reconciled. "
			"Set it with update_account(new_account_type='Bank'). Nothing was created."
		)
	return docname


def _accounts_sharing_gl_account(gl_account: str) -> list:
	return sorted(
		frappe.db.get_all(BANK_ACCOUNT, filters={"account": gl_account}, pluck="name", limit=10)
	)


def _resolve_bank(bank_name: str) -> tuple:
	"""The Bank docname, created on the fly when the institution is new."""
	if not compat.doctype_exists(BANK):
		raise ToolError(
			"this site has no Bank DocType, so the institution cannot be recorded. It ships "
			"with ERPNext's Accounts module. Nothing was created."
		)
	if frappe.db.exists(BANK, bank_name):
		return bank_name, False

	match = frappe.db.get_value(BANK, {"bank_name": bank_name}, "name")
	if match:
		return match, False

	bad = sorted({character for character in bank_name if character in _FORBIDDEN_NAME_CHARACTERS})
	if bad:
		raise ToolError(
			f"bank_name {bank_name!r} contains {', '.join(repr(character) for character in bad)}, "
			"which Frappe refuses in a docname (frappe.model.naming.validate_name) — and a Bank "
			"is named after itself. Nothing was created."
		)
	if bank_name.lower().startswith("new "):
		raise ToolError(
			f"bank_name {bank_name!r} starts with 'New ', which Frappe reserves for unsaved "
			"documents and refuses as a docname. Nothing was created."
		)

	doc = frappe.new_doc(BANK)
	doc.bank_name = bank_name
	doc.insert()
	return doc.name, True
