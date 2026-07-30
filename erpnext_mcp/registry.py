# SPDX-License-Identifier: MIT
"""The tool catalogue: one entry per tool, and the dispatcher that runs them.

This is the file to read first, and the only file to edit to add a tool.

DESCRIPTIONS ARE THE INTERFACE. The `description` on each tool is the entire
basis on which a model decides whether to call it, so each one says what the
tool returns, what it is *for*, and — for the mutating ones — what it cannot do.
"MUTATING" and "Read-only." are spelled out in the text as well as in the
annotations, because a client that ignores annotations still shows the model the
description.

WHY EVERY TOOL HAS A SWITCH. Read tools are gated identically to write tools.
The switch is not a security boundary for reads — anyone with the bearer token
could read the same data through Frappe's own API — it is a *surface* control.
An operator running this for bank reconciliation can turn the chart-of-accounts
tools off and stop a client wandering through the whole ledger looking for
context it does not need.

ONE DISPATCH PATH. `dispatch` is the only way a tool runs, so the switch check,
the availability check, the audit row, the rollback-on-failure and the
never-raise contract cannot be bypassed by adding a tool: a handler that is not
in `TOOLS` is not reachable, and one that is gets all five for free.

AVAILABILITY IS NOT THE SAME AS ENABLED. A tool's `available` predicate answers
"could this ever work on this site" — is `hrms` installed, does the Bank
Statement doctype exist. A tool that fails it is not advertised and cannot be
called, whatever the operator has ticked. The distinction matters because the
two failures need different words: "your operator turned this off" is a request
to go and ask them, while "this site does not have Frappe HR" is a request to
stop trying.
"""

import json

import frappe

from . import audit, settings
from .compat import doctype_exists, traceback_text
from .errors import ToolError
from .result import ToolResult
from .tools import (
	accounts,
	assets,
	banking,
	collab,
	dimensions,
	files,
	fiscal,
	governance,
	hr,
	investment_report,
	meta,
	mutate,
	notes,
	opening,
	packets,
	parties,
	read,
	realestate,
	reports,
	tax,
	trade,
	workflow,
)

_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_INTEGER = {"type": "integer"}
_BOOLEAN = {"type": "boolean"}
_OBJECT = {"type": "object"}
_STRING_ARRAY = {"type": "array", "items": {"type": "string"}}


def _field(kind: dict, description: str) -> dict:
	return {**kind, "description": description}


def _always() -> bool:
	return True


def _app_installed(app: str):
	"""Predicate: is `app` in this bench's installed apps for this site?

	`frappe.get_installed_apps()` is per-site and cached by the framework, so
	calling it once per tool per `tools/list` is cheap. Wrapped in a try because
	a request that lands mid-migrate can hit it before the app table is readable,
	and the safe answer there is "no".
	"""

	def predicate() -> bool:
		try:
			return app in (frappe.get_installed_apps() or [])
		except Exception:
			return False

	return predicate


def _needs_doctype(*doctypes: str):
	"""Predicate: does this site have any one of `doctypes`?

	Takes several because a tool that handles a renamed doctype (Client Script
	was Custom Script before v13) is available wherever *either* exists. A
	predicate narrower than the tool advertises a fallback the tool can never
	reach, which is worse than having no fallback.
	"""

	def predicate() -> bool:
		try:
			return any(doctype_exists(doctype) for doctype in doctypes)
		except Exception:
			return False

	return predicate


def _tool(
	handler,
	description: str,
	properties: dict,
	required=(),
	*,
	mutating: bool = False,
	destructive: bool = False,
	idempotent: bool = False,
	title: str = "",
	available=None,
	requires: str = "",
) -> dict:
	"""One catalogue entry.

	`annotations` are MCP's standard hints. `readOnlyHint` is the inverse of
	`mutating` by construction rather than by hand, so the two can never drift
	apart and let a write tool advertise itself as safe.

	`available` is a zero-argument predicate; `requires` is the sentence a caller
	sees when it returns False. Both default to "always available", which is what
	a tool touching only core Frappe/ERPNext doctypes should be.
	"""
	return {
		"handler": handler,
		"mutating": mutating,
		"description": description,
		"available": available or _always,
		"requires": requires,
		"inputSchema": {
			"type": "object",
			"properties": properties,
			"required": list(required),
			"additionalProperties": False,
		},
		"annotations": {
			"title": title,
			"readOnlyHint": not mutating,
			"destructiveHint": destructive,
			"idempotentHint": idempotent,
			# Every tool reads a live ERPNext site whose contents this app does
			# not control, which is exactly what openWorldHint means.
			"openWorldHint": True,
		},
	}


_COMPANY = _field(
	_STRING,
	"Company name (or its abbreviation). Optional on a single-company site, "
	"where it is inferred; required when the site has several.",
)
_LIMIT = _field(_INTEGER, "Maximum rows to return. Default 100, hard maximum 500.")


TOOLS = {
	# ── read-only ───────────────────────────────────────────────────────────
	"get_company_topology": _tool(
		read.get_company_topology,
		"Map this ERPNext install: every Company with its abbreviation, default "
		"currency, default Cost Center, fiscal years, chart-of-accounts root "
		"accounts and root types, plus which optional banking DocTypes the site "
		"has. Call this FIRST — every other tool takes a company, account or "
		"fiscal year whose names are specific to this site. Read-only.",
		{},
		title="Company topology",
	),
	"get_account_balance": _tool(
		read.get_account_balance,
		"Balance of one Account as of a date, summed from GL Entry (excluding "
		"cancelled entries) so it matches ERPNext's General Ledger report. "
		"Accepts an account docname, account number, or account name. Returns "
		"total debit, total credit, `balance` (debit - credit) and "
		"`balance_natural` (sign-flipped for Liability/Income/Equity so a normal "
		"balance reads positive). Read-only.",
		{
			"account": _field(
				_STRING,
				"Account docname (e.g. '1100 - Cash - ABC'), account number "
				"(e.g. '1100'), or exact account name (e.g. 'Cash').",
			),
			"as_of": _field(_STRING, "Balance date as YYYY-MM-DD. Defaults to today."),
			"company": _COMPANY,
		},
		required=("account",),
		title="Account balance",
	),
	"get_journal_entries": _tool(
		read.get_journal_entries,
		"List Journal Entry headers in a date range, newest first, optionally "
		"filtered by company, by an account appearing on any line, and by "
		"docstatus (0 draft / 1 submitted / 2 cancelled, or the words). Returns "
		"headers only — use get_journal_entry for one entry's account lines. "
		"Read-only.",
		{
			"from_date": _field(_STRING, "Start of the posting-date range, YYYY-MM-DD."),
			"to_date": _field(_STRING, "End of the posting-date range, YYYY-MM-DD."),
			"company": _COMPANY,
			"account": _field(
				_STRING,
				"Only entries with this account on one of their lines. Docname, number or name.",
			),
			"docstatus": _field(
				_STRING,
				"0/'draft', 1/'submitted' or 2/'cancelled'. Omit for all.",
			),
			"limit": _LIMIT,
		},
		required=("from_date", "to_date"),
		title="List journal entries",
	),
	"get_journal_entry": _tool(
		read.get_journal_entry,
		"One Journal Entry in full: header, totals, whether it balances, and "
		"every account line with its debit/credit, party, cost center and "
		"reference document. Read-only.",
		{"name": _field(_STRING, "Journal Entry docname, e.g. 'ACC-JV-2026-00042'.")},
		required=("name",),
		title="Journal entry detail",
	),
	"list_bank_transactions": _tool(
		read.list_bank_transactions,
		"List Bank Transactions, optionally by bank account, date range and "
		"status. Amounts are normalised to one signed `amount_signed` (positive "
		"= money in) whichever way this ERPNext version stores them. Read-only.",
		{
			"bank_account": _field(_STRING, "Bank Account docname or its account_name. Omit for all."),
			"from_date": _field(_STRING, "Earliest transaction date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest transaction date, YYYY-MM-DD."),
			"status": _field(
				_STRING,
				"Bank Transaction status as this site spells it, e.g. 'Pending', "
				"'Settled', 'Reconciled', 'Unreconciled'.",
			),
			"limit": _LIMIT,
		},
		title="List bank transactions",
	),
	"get_bank_statement": _tool(
		read.get_bank_statement,
		"One Bank Statement with every field and child table this site's version "
		"of the doctype carries. Only present on ERPNext versions that ship the "
		"Bank Statement doctype — get_company_topology reports whether this site "
		"has it. Read-only.",
		{"name": _field(_STRING, "Bank Statement docname.")},
		required=("name",),
		title="Bank statement detail",
		available=_needs_doctype("Bank Statement"),
		requires="the Bank Statement DocType, which older ERPNext versions do not ship",
	),
	"list_fiscal_years": _tool(
		read.list_fiscal_years,
		"Every Fiscal Year with its start and end dates and the companies it "
		"applies to (a fiscal year with no company links applies to all of them). "
		"Use before choosing a posting_date: ERPNext rejects dates outside a "
		"fiscal year. Read-only.",
		{"company": _COMPANY},
		title="List fiscal years",
	),
	"get_chart_of_accounts": _tool(
		read.get_chart_of_accounts,
		"A company's chart of accounts as a nested tree, each node carrying its "
		"account number, root type, account type, currency and whether it is a "
		"group. Optionally restricted to one root_type "
		"(Asset/Liability/Income/Expense/Equity). Read-only.",
		{
			"company": _COMPANY,
			"root_type": _field(_STRING, "One of Asset, Liability, Income, Expense, Equity."),
		},
		required=("company",),
		title="Chart of accounts",
	),
	"list_unreconciled_bank_transactions": _tool(
		read.list_unreconciled_bank_transactions,
		"The reconciliation worklist for one bank account: transactions with "
		"money still unallocated, oldest first, with the remaining amount per "
		"row. Uses this site's unallocated_amount column when it has one and "
		"computes gross minus allocated when it does not. Read-only.",
		{
			"bank_account": _field(_STRING, "Bank Account docname or its account_name."),
			"limit": _LIMIT,
		},
		required=("bank_account",),
		title="Unreconciled bank transactions",
	),
	"search_accounts": _tool(
		read.search_accounts,
		"Find Accounts by fragment of number or name, ranked best-first (exact "
		"number, exact name, prefix, substring). Use this to turn a phrase like "
		"'cash clearing' into the docname the other tools need, instead of "
		"guessing ERPNext's '<name> - <company abbr>' key. Read-only.",
		{
			"query": _field(_STRING, "Fragment of an account number or name."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		required=("query",),
		title="Search accounts",
	),
	"propose_clean_chart": _tool(
		accounts.propose_clean_chart,
		"A complete, numbered chart of accounts proposed for one company, ready to "
		"review. Returns the exact JSON shape import_chart_of_accounts takes, so "
		"the workflow is: propose, delete what you do not want, import as a dry "
		"run, then import for real.\n\n"
		"Also reports what is already on the site — the company's existing root "
		"accounts, and any template account number already in use — so a reviewer "
		"can see what the import would collide with before running it. Templates "
		"are static data in this app; the default is 'us_llc_farm'.\n\n"
		"Read-only: it changes nothing and creates nothing.",
		{
			"company": _COMPANY,
			"template": _field(
				_STRING,
				"Template key. Default 'us_llc_farm' (a US farming LLC, written with "
				"tree fruit in mind). An unknown key is refused with the list.",
			),
		},
		required=("company",),
		title="Propose a clean chart of accounts",
	),
	"list_cost_centers": _tool(
		dimensions.list_cost_centers,
		"A company's cost centers as a nested tree, each node carrying its number, "
		"whether it is a group and whether it is disabled. Cost centers are the "
		"axis a posting is filed under alongside the account — the segment, "
		"department or activity the money belongs to — and only leaf cost centers "
		"can be posted to. Disabled ones are left out unless asked for, and the "
		"response says how many that was. Read-only.",
		{
			"company": _COMPANY,
			"include_disabled": _field(
				_BOOLEAN,
				"true to include disabled cost centers. Default false.",
			),
		},
		required=("company",),
		title="List cost centers",
		available=_needs_doctype("Cost Center"),
		requires="the Cost Center DocType, which ships with ERPNext's Accounts module",
	),
	# ── mutating: every one default OFF ─────────────────────────────────────
	"create_journal_entry": _tool(
		mutate.create_journal_entry,
		"MUTATING (default OFF). Create a DRAFT Journal Entry — docstatus 0, "
		"affecting no balance. Debits must equal credits or nothing is created. "
		"This tool cannot submit, and there is no argument that makes it: "
		"posting requires the separate submit_journal_entry tool, which an "
		"operator may not have enabled.",
		{
			"company": _COMPANY,
			"posting_date": _field(_STRING, "Posting date, YYYY-MM-DD."),
			"accounts": {
				"type": "array",
				"minItems": 2,
				"description": (
					"The entry's lines. Each object needs an account and exactly "
					"one of debit or credit, both positive. Optional per line: "
					"party_type, party, cost_center, project, reference_type, "
					"reference_name, user_remark, exchange_rate, and a "
					"`dimensions` object for any custom accounting dimension."
				),
				"items": {
					"type": "object",
					"properties": {
						"account": _field(_STRING, "Account docname, number or name."),
						"debit": _field(_NUMBER, "Debit amount, positive."),
						"credit": _field(_NUMBER, "Credit amount, positive."),
						"party_type": _field(_STRING, "'Customer', 'Supplier', 'Employee', …"),
						"party": _field(_STRING, "Party docname."),
						"cost_center": _field(_STRING, "Cost Center docname."),
						"project": _field(_STRING, "Project docname."),
						"reference_type": _field(_STRING, "Doctype this line settles, e.g. 'Sales Invoice'."),
						"reference_name": _field(_STRING, "That document's name."),
						"user_remark": _field(_STRING, "Per-line remark."),
						"exchange_rate": _field(_NUMBER, "Required for a foreign-currency account."),
						"dimensions": _field(
							_OBJECT,
							"Custom accounting dimensions for THIS line, as fieldname "
							'→ value, e.g. {"member": "Member-01", "bbch_stage": '
							'"BBCH-8"}. Every key is checked against Journal Entry '
							"Account's own fields, and a Link value against the "
							"records it can point at, so a dimension that has not "
							"been created yet is refused by name rather than silently "
							"dropped. Cost centre and project are ordinary line "
							"fields above, not dimensions.",
						),
					},
					"required": ["account"],
					"additionalProperties": False,
				},
			},
			"user_remark": _field(
				_STRING,
				"Why this entry exists. Required — it is what an accountant reads "
				"first, and what the audit log records.",
			),
			"cheque_no": _field(_STRING, "Reference number, if any."),
			"cheque_date": _field(_STRING, "Reference date, YYYY-MM-DD."),
			"voucher_type": _field(
				_STRING,
				"Journal Entry voucher type, e.g. 'Journal Entry', 'Bank Entry'. "
				"Defaults to the doctype's own default.",
			),
		},
		required=("company", "posting_date", "accounts", "user_remark"),
		mutating=True,
		title="Create draft journal entry",
	),
	"submit_journal_entry": _tool(
		mutate.submit_journal_entry,
		"MUTATING (default OFF). Submit an existing DRAFT Journal Entry, "
		"docstatus 0 → 1. This writes GL Entries and moves balances. It takes "
		"only a name: it cannot create the entry it submits.",
		{"name": _field(_STRING, "Docname of a draft Journal Entry.")},
		required=("name",),
		mutating=True,
		title="Submit journal entry",
	),
	"cancel_journal_entry": _tool(
		mutate.cancel_journal_entry,
		"MUTATING (default OFF). Cancel a submitted Journal Entry, docstatus "
		"1 → 2, writing reversing GL Entries. `reason` is mandatory and is "
		"recorded on the document and in the audit log. Nothing is deleted.",
		{
			"name": _field(_STRING, "Docname of a submitted Journal Entry."),
			"reason": _field(_STRING, "Why it is being cancelled. Recorded permanently."),
		},
		required=("name", "reason"),
		mutating=True,
		destructive=True,
		title="Cancel journal entry",
	),
	"create_fiscal_year": _tool(
		fiscal.create_fiscal_year,
		"MUTATING (default OFF). Create one Fiscal Year, so ERPNext will accept "
		"postings dated inside it.\n\n"
		"THIS IS THE PREREQUISITE FOR BOOKING HISTORY. ERPNext refuses any posting "
		"whose date falls outside a fiscal year, and it refuses it from inside the "
		"document being saved — so on a site whose only year is 2026, a March 2025 "
		"equipment transfer fails with an error about a date rather than about a "
		"missing year. `set_opening_balance` and `create_journal_entry` cannot "
		"reach that period until this has run.\n\n"
		"COMPANIES IS OPTIONAL, AND LEAVING IT OUT IS NOT AN OMISSION. ERPNext "
		"models a global fiscal year as one with no company restrictions, which is "
		"what almost every site wants and is the default here. Pass `companies` "
		"only to restrict the year to some of them.\n\n"
		"REFUSES: a year_name already on the site (a Fiscal Year names itself, so "
		"the name is the key); an end date before the start; and a date range that "
		"overlaps an existing fiscal year whose company scope intersects this one — "
		"a global year overlaps everything, two restricted years overlap only if "
		"they share a company. Overlapping years make ERPNext's own get_fiscal_year "
		"ambiguous, and a disabled year does not free its range.\n\n"
		"ALSO REFUSES an end date that is not exactly one year after the start, "
		"less a day, unless is_short_year is set — that is ERPNext's own rule in "
		"FiscalYear.validate_dates, and this says which date it wanted rather than "
		"leaving you to work it out. ERPNext's own overlap check is company-blind "
		"on some versions and is stricter than this one; where it is, its refusal "
		"is passed through unchanged.\n\n"
		"Creates the year and nothing else. No balance moves and nothing is booked "
		"— what changes is which dates the ledger will accept.",
		{
			"year_name": _field(
				_STRING,
				"What the year is called, e.g. '2025' or '2025-26'. Becomes the docname, "
				"and is what every Journal Entry and Budget that names a fiscal year will "
				"hold. Must be free.",
			),
			"year_start_date": _field(_STRING, "First day of the year, YYYY-MM-DD."),
			"year_end_date": _field(
				_STRING,
				"Last day of the year, YYYY-MM-DD. ERPNext requires exactly one year after "
				"the start less a day unless is_short_year is set.",
			),
			"companies": {
				"description": (
					"Companies this year applies to. OMIT IT for a year that applies to "
					"every company, which is how ERPNext models a global fiscal year and is "
					"usually what you want."
				),
				"anyOf": [_STRING_ARRAY, _STRING],
			},
			"is_short_year": _field(
				_BOOLEAN,
				"true when the period is deliberately shorter (or longer) than a year — a "
				"company's first months, or a change of year end. Default false, which "
				"holds the range to ERPNext's exact-one-year rule.",
			),
			"disabled": _field(
				_BOOLEAN,
				"true to create it already disabled. ERPNext still refuses postings dated "
				"inside a disabled year, so this is rarely what you want. Default false.",
			),
			"auto_created": _field(
				_BOOLEAN,
				"Marks the year as one generated by an automatic process rather than "
				"chosen by a person. Informational. Default false.",
			),
		},
		required=("year_name", "year_start_date", "year_end_date"),
		mutating=True,
		title="Create a fiscal year",
		available=_needs_doctype("Fiscal Year"),
		requires="the Fiscal Year DocType, which ships with ERPNext's Accounts module",
	),
	"update_fiscal_year": _tool(
		fiscal.update_fiscal_year,
		"MUTATING (default OFF). Move an existing Fiscal Year's dates, or "
		"enable/disable it.\n\n"
		"RISK, and the reason the date half is guarded. Moving the dates moves no "
		"posting at all. What it changes is which year — or no year at all — every "
		"posting already written falls into, retroactively, including periods "
		"already reported. So the postings that would fall OUT of the new range are "
		"counted before anything is written, and any at all is a refusal with the "
		"count: a GL Entry in no fiscal year drops out of period comparisons and "
		"cannot be corrected without reopening a year that no longer covers it.\n\n"
		"CANNOT RENAME. ERPNext names a Fiscal Year after itself, so the name is the "
		"docname and is the string every Journal Entry, Budget and Period Closing "
		"Voucher that names a year holds. CANNOT change `companies` either — "
		"narrowing the scope of a year with postings in it takes those postings out "
		"of any fiscal year for the companies it drops. Both are Desk decisions and "
		"are refused by name.\n\n"
		"Same overlap check as create_fiscal_year, against every other year whose "
		"company scope intersects this one. Disabling is reversible and deletes "
		"nothing: the entries already in the range remain and still appear in "
		"reports covering them; ERPNext simply refuses NEW postings dated inside a "
		"disabled year.",
		{
			"year_name": _field(_STRING, "The existing Fiscal Year, by name. list_fiscal_years has them."),
			"new_year_start_date": _field(_STRING, "New first day, YYYY-MM-DD."),
			"new_year_end_date": _field(_STRING, "New last day, YYYY-MM-DD."),
			"is_short_year": _field(
				_BOOLEAN,
				"Set or clear the deliberately-short flag. Clearing it holds the range to "
				"ERPNext's exact-one-year rule from the next save onwards.",
			),
			"disabled": _field(_BOOLEAN, "true to disable, false to re-enable."),
		},
		required=("year_name",),
		mutating=True,
		idempotent=True,
		title="Update a fiscal year",
		available=_needs_doctype("Fiscal Year"),
		requires="the Fiscal Year DocType, which ships with ERPNext's Accounts module",
	),
	"set_opening_balance": _tool(
		opening.set_opening_balance,
		"MUTATING (default OFF). Book one historical event onto a set of books as a "
		"DRAFT opening-balance Journal Entry, balancing automatically against "
		"Opening Balance Equity.\n\n"
		"USE THIS RATHER THAN create_journal_entry for equipment transferred in, "
		"proceeds of a sale that predates this ledger, the starting value of a "
		"portfolio, or any other balance that was true before day one. It does "
		"three things a hand-built entry keeps losing: it COMPUTES the offsetting "
		"equity line instead of trusting the caller's arithmetic; it flags the "
		"entry `is_opening` (and `Opening Entry` where the site has that voucher "
		"type), which is what keeps the amounts out of the period's activity in "
		"reports that separate the two; and it takes one remark for the whole "
		"event rather than one per line.\n\n"
		"THE EQUITY ACCOUNT IS FOUND, NOT GUESSED: account_number 3300 first, then "
		"a leaf Equity account named after opening balances. Anything other than "
		"exactly one match is refused with the candidates listed. Override it with "
		"opening_equity_account.\n\n"
		"Refuses a group, disabled or wrong-company account on any line; a group "
		"or disabled cost center; a dimension value that does not exist; a "
		"non-positive amount (direction is dr_or_cr, never a minus sign); and an "
		"opening_equity_account that is not Equity. Nothing is written unless "
		"every line validates.\n\n"
		"Creates a DRAFT. It moves no balance until submit_journal_entry posts it, "
		"and an opening balance is the entry most worth reading first — it is the "
		"one nobody will ever re-derive.",
		{
			"company": _COMPANY,
			"posting_date": _field(
				_STRING,
				"YYYY-MM-DD. Usually the day before the first trading day, or the "
				"fiscal year's opening date. Must fall inside a Fiscal Year this site has.",
			),
			"entries": {
				"type": "array",
				"minItems": 1,
				"description": (
					"The real side of the event, one object per account. The offsetting "
					"line against opening equity is computed — do not include it."
				),
				"items": {
					"type": "object",
					"properties": {
						"account": _field(_STRING, "Account docname, number or name. Must be a leaf."),
						"dr_or_cr": _field(
							_STRING,
							"'dr'/'debit' or 'cr'/'credit'. The direction lives here, never in the sign of the amount.",
						),
						"amount": _field(_NUMBER, "Positive. Always."),
						"cost_center": _field(_STRING, "Optional leaf cost center to file this line under."),
						"dimensions": _field(
							_OBJECT,
							'Optional accounting dimensions for this line, e.g. {"member": "Member-01"}. '
							"Each key must be a field on Journal Entry Account and each value must exist.",
						),
						"narrative": _field(_STRING, "Optional per-line remark. The event's own explanation is user_remark."),
					},
					"required": ["account", "dr_or_cr", "amount"],
					"additionalProperties": False,
				},
			},
			"opening_equity_account": _field(
				_STRING,
				"Override the Opening Balance Equity account the offsetting line lands "
				"in. Must be a leaf Equity account in this company.",
			),
			"user_remark": _field(
				_STRING,
				"MANDATORY. What event these balances came from, e.g. 'Equipment "
				"transferred from PFI on dissolution, per the 2026-01-02 bill of sale'. "
				"The only part of an opening balance nobody can reconstruct later.",
			),
		},
		required=("posting_date", "entries", "user_remark"),
		mutating=True,
		title="Set an opening balance",
	),
	"post_opening_balance_journal_entry": _tool(
		opening.post_opening_balance_journal_entry,
		"MUTATING (default OFF). Book a whole opening balance sheet as ONE Journal "
		"Entry — every line explicit, the balancing line going to an account you "
		"name — and post it in the same call if you ask it to.\n\n"
		"THIS OR set_opening_balance? Use `set_opening_balance` when you know one "
		"side of one historical event ('the equipment came over from PFI') and want "
		"the equity plug computed for you. Use THIS when you are transcribing a "
		"trial balance off the previous system: you already have both sides, they "
		"already balance, and putting them through a one-event-at-a-time tool means "
		"one call and one stray equity line per account.\n\n"
		"THE OFFSET IS NAMED, NOT FOUND. `offset_account` is required exactly when "
		"the lines do not balance, and the difference is written to it as a single "
		"line — normally Opening Balance Equity (account 3300), though retained "
		"earnings or a suspense account are legitimate and are not second-guessed. "
		"Naming one when the lines already balance writes no line and the response "
		"says so.\n\n"
		"SUBMITTING CHECKS TWO SWITCHES. `submit: true` posts the entry through "
		"submit_journal_entry, so THAT switch must be on as well as this tool's — "
		"and it is checked before anything is written, so a site with posting "
		"disabled gets a refusal rather than a draft nobody asked for. The default "
		"is false: a draft, moving no balance.\n\n"
		"Flags the entry `is_opening` and gives it the `Opening Entry` voucher type "
		"where the site has them. Refuses a group, disabled or wrong-company "
		"account on any line or on the offset; a group or disabled cost center; a "
		"dimension value that does not exist; a non-positive amount (direction is "
		"`side`, never a minus sign); and a voucher_type this site does not offer. "
		"Nothing is written unless every line validates.",
		{
			"company": _COMPANY,
			"posting_date": _field(
				_STRING,
				"YYYY-MM-DD. Usually the day before the first trading day, or the fiscal "
				"year's opening date. Must fall inside a Fiscal Year this site has.",
			),
			"lines": {
				"type": "array",
				"minItems": 1,
				"description": (
					"Every line of the entry, one object per account. Unlike "
					"set_opening_balance these are taken as given — the only line this "
					"tool adds is the balancing one against offset_account."
				),
				"items": {
					"type": "object",
					"properties": {
						"account": _field(_STRING, "Account docname, number or name. Must be a leaf."),
						"side": _field(
							_STRING,
							"'debit' or 'credit' ('dr'/'cr' accepted). The direction lives "
							"here, never in the sign of the amount.",
						),
						"amount": _field(_NUMBER, "Positive. Always."),
						"cost_center": _field(_STRING, "Optional leaf cost center to file this line under."),
						"dimensions": _field(
							_OBJECT,
							'Optional accounting dimensions for this line, e.g. {"member": '
							'"Member-01"}. Each key must be a field on Journal Entry Account '
							"and each value must exist.",
						),
						"narrative": _field(
							_STRING,
							"Optional per-line remark. The entry's own explanation is user_remark.",
						),
					},
					"required": ["account", "side", "amount"],
					"additionalProperties": False,
				},
			},
			"offset_account": _field(
				_STRING,
				"Where the difference goes when the lines do not balance — normally "
				"Opening Balance Equity, account 3300. Required in that case and "
				"refused as unnecessary only in the sense that no line is written when "
				"the lines already balance. Must be a leaf account in this company.",
			),
			"user_remark": _field(
				_STRING,
				"MANDATORY. Where these balances came from, e.g. 'trial balance at "
				"2025-12-31 per the prior system, reviewed by TP'. The only part of an "
				"opening balance nobody can reconstruct later.",
			),
			"voucher_type": _field(
				_STRING,
				"Journal Entry voucher type. Defaults to 'Opening Entry' where this site "
				"offers it. A type this site does not have is refused rather than "
				"silently swapped.",
			),
			"submit": _field(
				_BOOLEAN,
				"Post the entry after creating it, docstatus 0 → 1, writing GL Entries "
				"and moving balances. Default false. Requires allow_submit_journal_entry "
				"as well as this tool's own switch.",
			),
		},
		required=("posting_date", "lines", "user_remark"),
		mutating=True,
		title="Post an opening balance journal entry",
	),
	"bulk_submit_journal_entries": _tool(
		mutate.bulk_submit_journal_entries,
		"MUTATING (default OFF). Submit many DRAFT Journal Entries in one call, "
		"docstatus 0 → 1 each. This writes GL Entries and moves balances.\n\n"
		"FOR THE MIGRATION WEEKEND. A chart imported, a year of history keyed in, "
		"an opening balance per account: hundreds of drafts, and posting them one "
		"MCP round trip at a time is not the same job at a different speed — it is "
		"the job where somebody loses track at number four hundred and stops "
		"without knowing which ones went.\n\n"
		"CHECKS submit_journal_entry's SWITCH TOO, and fails before touching "
		"anything if it is off. That switch is where an operator decided whether an "
		"AI client may move a balance at all.\n\n"
		"ONE FAILURE IS NOT THE BATCH'S. Each entry is submitted in its own "
		"transaction — committed on success, rolled back on failure — and the loop "
		"carries on. Returns a row per document with `ok` and the exact error, plus "
		"aggregate counts. An entry that is already submitted comes back `ok` and "
		"`skipped: already_submitted`, never an error, so a half-finished batch is "
		"safe to retry whole. A cancelled entry is a failure: it cannot be posted "
		"again.",
		{
			"names": {
				"type": "array",
				"minItems": 1,
				"description": (
					"Journal Entry docnames to submit, e.g. ['ACC-JV-2026-00042', "
					"'ACC-JV-2026-00043']. Maximum 500 per call. get_journal_entries "
					"with docstatus='draft' lists the ones waiting."
				),
				"items": _STRING,
			}
		},
		required=("names",),
		mutating=True,
		title="Submit journal entries in bulk",
	),
	"delete_draft_journal_entry": _tool(
		mutate.delete_draft_journal_entry,
		"MUTATING (default OFF). Delete a DRAFT Journal Entry outright — docstatus "
		"0 only, a real delete, nothing left in the table.\n\n"
		"THE GAP IT FILLS. cancel_journal_entry refuses a draft, correctly: there "
		"is nothing to reverse, because a draft has moved no balance. That left an "
		"unwanted draft with no MCP path at all and a human opening ERPNext to "
		"tidy up after a tool.\n\n"
		"DRAFTS ONLY, WHATEVER IS ASKED. A SUBMITTED entry has written GL Entries; "
		"deleting it would take those balances with it and leave nothing saying "
		"why, so it is refused and pointed at cancel_journal_entry. A CANCELLED "
		"entry and its reversing rows are the evidence that a posting was made and "
		"undone — deleting one leaves an audit trail with a hole in it — so that is "
		"refused too.\n\n"
		"`reason` is mandatory, and the response carries the deleted entry's "
		"company, date, totals and every line, because once this returns the MCP "
		"Action Log row is the only record that the document ever existed.",
		{
			"name": _field(_STRING, "Docname of a DRAFT Journal Entry."),
			"reason": _field(
				_STRING,
				"Why it is being deleted, e.g. 'duplicate of ACC-JV-2026-00311, keyed "
				"twice during the opening-balance load'. Recorded permanently in the "
				"audit log, which is all that survives the delete.",
			),
		},
		required=("name", "reason"),
		mutating=True,
		destructive=True,
		title="Delete a draft journal entry",
	),
	"create_bank_account": _tool(
		banking.create_bank_account,
		"MUTATING (default OFF). Create the Bank Account record that maps a real "
		"account at a real institution onto an account on the chart — and the Bank "
		"(the institution) too, when this site has never seen it. Both in one "
		"transaction: a failure leaves neither.\n\n"
		"A Bank Account holds no balance. It is the mapping a bank feed writes "
		"into and a reconciliation reads. WHY PRE-CREATE ONE, when a feed makes it "
		"on first sync: the one a feed makes is named whatever the feed calls it "
		"and points at a GL account the feed picked. Renaming it later is fine; "
		"REPOINTING it is not — once transactions have been imported, the GL "
		"account named here is where they reconcile to, and moving that is a "
		"manual re-reconciliation.\n\n"
		"REFUSES: an unknown company; a GL account that does not exist, belongs to "
		"another company, is a group or is disabled; a GL account whose root_type "
		"is neither Asset (a bank account) nor Liability (a credit card); an Asset "
		"account whose account_type is not Bank or Cash, because ERPNext's own "
		"account picker and its bank reconciliation tool both filter on that flag "
		"and an untyped account saves here and then cannot be reconciled; an "
		"account_name already used by another Bank Account in this company; "
		"party/party_type together with is_company_account; and a bank_name Frappe "
		"would refuse as a docname.\n\n"
		"WARNS (does not refuse) when the GL account is already the account of "
		"another Bank Account — legitimate for a sweep arrangement, a mistake "
		"everywhere else.",
		{
			"account_name": _field(
				_STRING,
				"How this account should read in every picker, e.g. "
				"'WF Advisors Cash - ••3158'. Unique per company; the mask is the usual "
				"distinguisher between two accounts at the same bank.",
			),
			"bank_name": _field(
				_STRING,
				"The institution, e.g. 'Wells Fargo'. The Bank record is created if this "
				"site has none by that name, and reused if it has.",
			),
			"account": _field(
				_STRING,
				"The chart-of-accounts account this posts to: docname, number or name. "
				"Required for a company account — it is the whole point of the record.",
			),
			"company": _COMPANY,
			"account_no": _field(
				_STRING,
				"The account number the feed will report — last four, or the whole thing. "
				"Same field as bank_account_no; pass one.",
			),
			"bank_account_no": _field(_STRING, "ERPNext's own name for account_no. Pass one or the other."),
			"iban": _field(_STRING, "IBAN, where the account has one."),
			"is_company_account": _field(
				_BOOLEAN,
				"true (the default) = an account this company owns. false = somebody "
				"else's, recorded for reference, in which case party_type/party say whose.",
			),
			"party_type": _field(_STRING, "For a third-party account: the DocType, e.g. 'Supplier'."),
			"party": _field(_STRING, "For a third-party account: that record's name."),
			"disabled": _field(_BOOLEAN, "true to create it already disabled. Default false."),
		},
		required=("account_name", "bank_name"),
		mutating=True,
		title="Create a bank account",
		available=_needs_doctype("Bank Account"),
		requires="the Bank Account DocType, which ships with ERPNext's Accounts module",
	),
	"create_bank_transaction": _tool(
		mutate.create_bank_transaction,
		"MUTATING (default OFF). Insert a DRAFT Bank Transaction. `amount` is "
		"signed as a human reads a statement: positive money in, negative money "
		"out, mapped onto whichever columns this ERPNext version has. Drafts are "
		"not reconcilable until submitted in ERPNext; this app ships no tool to "
		"submit one.",
		{
			"bank_account": _field(_STRING, "Bank Account docname or its account_name."),
			"date": _field(_STRING, "Transaction date, YYYY-MM-DD."),
			"amount": _field(_NUMBER, "Signed amount: positive = money in, negative = money out."),
			"description": _field(_STRING, "Statement description / narrative."),
			"reference_no": _field(_STRING, "Bank reference number, if any."),
			"company": _COMPANY,
		},
		required=("bank_account", "date", "amount", "description"),
		mutating=True,
		title="Create bank transaction",
	),
	"reconcile_bank_transaction": _tool(
		mutate.reconcile_bank_transaction,
		"MUTATING (default OFF). Attach payment vouchers to a Bank Transaction, "
		"refusing to allocate more than the transaction's remaining amount. "
		"Delegates to ERPNext's own reconciliation method where the site has it, "
		"so clearance dates and status follow the same rules as the Bank "
		"Reconciliation Tool.",
		{
			"name": _field(_STRING, "Bank Transaction docname."),
			"payment_entries": {
				"type": "array",
				"minItems": 1,
				"description": "Vouchers to allocate against this transaction.",
				"items": {
					"type": "object",
					"properties": {
						"payment_document": _field(
							_STRING,
							"Voucher doctype, e.g. 'Payment Entry', 'Journal Entry', 'Sales Invoice'.",
						),
						"payment_entry": _field(_STRING, "That document's name."),
						"allocated_amount": _field(_NUMBER, "Amount to allocate, positive."),
					},
					"required": [
						"payment_document",
						"payment_entry",
						"allocated_amount",
					],
					"additionalProperties": False,
				},
			},
		},
		required=("name", "payment_entries"),
		mutating=True,
		title="Reconcile bank transaction",
	),
	# ── chart of accounts: structural writes, every one default OFF ─────────
	"create_account": _tool(
		accounts.create_account,
		"MUTATING (default OFF). Create one Account under an existing group. "
		"Refuses before writing anything if the parent does not exist or is a "
		"ledger rather than a group, if root_type disagrees with the parent's, if "
		"the account number is already used in that company, or if the "
		"account_type cannot sit under that root_type (a Payable under Income, "
		"say).\n\n"
		"Cannot create a root account — ERPNext treats roots as uneditable once "
		"made, so those come from import_chart_of_accounts, which builds the whole "
		"tree in one reviewable transaction.",
		{
			"company": _COMPANY,
			"account_number": _field(
				_STRING,
				"Account number, unique within the company, e.g. '1120'. Becomes part "
				"of the docname: '<number> - <name> - <company abbr>'.",
			),
			"account_name": _field(_STRING, "Account name as it should read, e.g. 'Checking - Primary'."),
			"root_type": _field(
				_STRING,
				"One of Asset, Liability, Income, Expense, Equity. Must match the "
				"parent's — it is required so the caller states its intent and this "
				"tool can check it, not so it can differ.",
			),
			"parent_account": _field(
				_STRING,
				"The group account this hangs under. Docname, number or name. Must "
				"already exist and be a group.",
			),
			"is_group": _field(
				_BOOLEAN,
				"true to create a group (a heading that cannot be posted to). Default "
				"false, a ledger account.",
			),
			"account_type": _field(
				_STRING,
				"ERPNext account type, e.g. 'Bank', 'Cash', 'Receivable', 'Payable', "
				"'Fixed Asset', 'Expense Account'. Validated against this site's own "
				"option list. Omit if none applies.",
			),
			"account_currency": _field(_STRING, "Currency code, e.g. 'USD'. Defaults to the company's."),
			"tax_rate": _field(_NUMBER, "Percentage, for a tax account."),
		},
		required=("company", "account_number", "account_name", "root_type", "parent_account"),
		mutating=True,
		title="Create an account",
	),
	"update_account": _tool(
		accounts.update_account,
		"MUTATING (default OFF). Rename, renumber, re-type or disable/enable one "
		"Account. Renaming goes through ERPNext's own update_account_number, so "
		"the docname moves with the fields and every link and report label follows "
		"— a rename that changed only the field would leave the chart showing one "
		"thing and reporting another.\n\n"
		"Deliberately CANNOT change the parent: reparenting is move_account, "
		"behind its own switch, so a bad move cannot happen inside a rename.\n\n"
		"Refuses to change the account_type across ERPNext's Receivable/Payable "
		"boundary on an account that already has GL entries, and refuses type and "
		"disabled changes on a root account, which ERPNext will not save at all.",
		{
			"name": _field(_STRING, "The account: docname, number or name."),
			"company": _COMPANY,
			"new_account_name": _field(_STRING, "New account name. The docname is rebuilt to match."),
			"new_account_number": _field(_STRING, "New account number. Must be free in this company."),
			"new_account_type": _field(
				_STRING,
				"New ERPNext account type. Pass an empty string to clear it.",
			),
			"disabled": _field(
				_BOOLEAN,
				"true to disable, false to re-enable. For disabling, prefer "
				"disable_account — it requires a reason and refuses an account the "
				"current fiscal year is still posting through.",
			),
		},
		required=("name",),
		mutating=True,
		idempotent=True,
		title="Update an account",
	),
	"move_account": _tool(
		accounts.move_account,
		"MUTATING (default OFF). Move one Account under a different parent, "
		"changing nothing else about it. Validates that the new parent is a group "
		"in the same company with the same root_type, and that the move would not "
		"create a cycle.\n\n"
		"RISK, and the reason this is separate from update_account: reparenting "
		"moves no GL entry at all, but it changes which subtotal every existing "
		"posting rolls up into — retroactively, for every period, including ones "
		"already reported to a bank or a CPA. A balance sheet run before and after "
		"will not agree. Take it seriously in a way a rename does not deserve.",
		{
			"name": _field(_STRING, "The account to move: docname, number or name."),
			"new_parent_account": _field(
				_STRING,
				"The group it should hang under instead. Must be a group in the same "
				"company with the same root_type.",
			),
			"company": _COMPANY,
		},
		required=("name", "new_parent_account"),
		mutating=True,
		destructive=True,
		title="Move an account",
	),
	"disable_account": _tool(
		accounts.disable_account,
		"MUTATING (default OFF). Disable an Account — ERPNext's soft delete. "
		"Nothing is removed: the account, its history and its GL entries all "
		"remain, and it can be re-enabled with update_account.\n\n"
		"REFUSES if the account carries any GL entry inside the current fiscal "
		"year, because disabling an account this year is still posting through "
		"breaks period comparisons and hides it from the pickers a correction "
		"would need. Also refuses a root account, which ERPNext will not save.\n\n"
		"`reason` is mandatory and is written to the account's comment thread and "
		"to the audit log.",
		{
			"name": _field(_STRING, "The account: docname, number or name."),
			"reason": _field(_STRING, "Why it is being retired. Recorded permanently."),
			"company": _COMPANY,
		},
		required=("name", "reason"),
		mutating=True,
		destructive=True,
		title="Disable an account",
	),
	"delete_account": _tool(
		accounts.delete_account,
		"MUTATING (default OFF). IRREVERSIBLE. Hard-delete an Account that nothing "
		"has ever touched — no GL entries, no journal entry lines, no children, no "
		"company default pointing at it, no Bank Account posting to it. There is no "
		"undo, no draft and no cancelled state; the record is gone.\n\n"
		"WHEN THIS AND NOT disable_account. Almost never. Disabling is right for "
		"any account with history: the postings stay, the reports still balance, "
		"and it drops out of pickers. This exists for the other case — the accounts "
		"a bundled chart of accounts created on day one that nobody ever posted "
		"to. Those cannot be disabled out of the way, because A DISABLED ACCOUNT "
		"STILL HOLDS ITS ACCOUNT NUMBER, and on a company being renumbered onto a "
		"real chart that is the entire problem.\n\n"
		"FOUR CHECKS, all on by default, all refusals rather than warnings, all run "
		"before anything is deleted so one call reports every reason at once: GL "
		"entries (including journal entry lines on unsubmitted drafts, which write "
		"no GL row and would otherwise read as untouched), child accounts (disabled "
		"ones count — they are still children), Company default fields, and Bank "
		"Account records.\n\n"
		"Each check has a force_check_… flag that turns it off. Turning one off "
		"does NOT make a referenced account deletable: Frappe's own link-integrity "
		"check still runs on the delete and will refuse. The flag changes which "
		"error you get, not the outcome.",
		{
			"name": _field(_STRING, "The account: docname, number or name."),
			"company": _COMPANY,
			"force_check_gl_entries": _field(
				_BOOLEAN,
				"true (the default) = refuse if the account has ever carried a GL entry "
				"or appears on any journal entry line, draft included.",
			),
			"force_check_children": _field(
				_BOOLEAN,
				"true (the default) = refuse if it is a group with any child, enabled or disabled.",
			),
			"force_check_company_defaults": _field(
				_BOOLEAN,
				"true (the default) = refuse if any Company field points at it.",
			),
			"force_check_bank_accounts": _field(
				_BOOLEAN,
				"true (the default) = refuse if any Bank Account record posts to it.",
			),
		},
		required=("name",),
		mutating=True,
		destructive=True,
		title="Delete an account",
	),
	"import_chart_of_accounts": _tool(
		accounts.import_chart_of_accounts,
		"MUTATING (default OFF). Create a whole tree of accounts from nested JSON, "
		"parents before children, in ONE transaction — any failure rolls the "
		"entire import back rather than leaving a half-built tree.\n\n"
		"dry_run DEFAULTS TO TRUE. A dry run creates nothing and returns the full "
		"ordered plan: the docname each account would get, its parent, and for "
		"every account already on the site whether it would be skipped (same "
		"number, same name — so re-running an import is safe) or is a conflict "
		"that has to be fixed first. Read the plan, then call again with "
		"dry_run=false.\n\n"
		"Get the JSON from propose_clean_chart, or write it yourself: a list of "
		"root accounts, each with account_number, account_name, root_type, "
		"is_group and children. root_type is required on roots and inherited "
		"below. A root node may name an existing parent_account to graft the "
		"subtree onto the company's current chart instead of adding a new root.\n\n"
		"A root node with NO parent_account becomes a new top-level account, which "
		"works and is reported as such in the plan (`new_root_accounts`). It must "
		"be a group — ERPNext refuses to save a top-level ledger account — and it "
		"is added alongside the company's existing roots rather than replacing "
		"them, because ERPNext will not let a root be moved or renamed into an "
		"existing tree afterwards.",
		{
			"company": _COMPANY,
			"accounts_json": {
				"description": (
					"The chart, as a list of root accounts — or the whole "
					"propose_clean_chart response, whose `accounts` key is used. A JSON "
					"string is accepted too. Per node: account_number, account_name, "
					"root_type (roots), account_type, account_currency, tax_rate, "
					"is_group, description, children. Unknown keys are rejected by name."
				),
				"anyOf": [{"type": "array"}, {"type": "object"}, {"type": "string"}],
			},
			"dry_run": _field(
				_BOOLEAN,
				"true (THE DEFAULT) = report the full plan and change nothing. Set "
				"false only after a human has read the plan.",
			),
		},
		required=("company", "accounts_json"),
		mutating=True,
		idempotent=True,
		title="Import a chart of accounts",
	),
	# ── dimensions: how a posting is classified, every one default OFF ──────
	"create_cost_center": _tool(
		dimensions.create_cost_center,
		"MUTATING (default OFF). Create one Cost Center under an existing group. "
		"A cost center is the second axis a posting is filed under: the account "
		"says what kind of money it is, the cost center says which part of the "
		"business it belongs to.\n\n"
		"Refuses before writing anything if the parent does not exist, is a leaf "
		"rather than a group, or belongs to another company; if the cost center "
		"number is already used in that company; or if the resulting docname is "
		"taken. Reversible in the sense that nothing else is touched — a cost "
		"center with no postings against it can be disabled with "
		"update_cost_center.\n\n"
		"Cannot normally create a root: ERPNext gives every company exactly one, "
		"named after the company, when its chart of accounts is built. Omitting "
		"parent_cost_center on a company that already has one is refused with the "
		"root's name.",
		{
			"company": _COMPANY,
			"cost_center_name": _field(
				_STRING,
				"Cost center name as it should read, e.g. 'Harvest'. Becomes part of "
				"the docname: '<number> - <name> - <company abbr>'.",
			),
			"cost_center_number": _field(
				_STRING,
				"Optional number, unique within the company, e.g. '3200'. Unlike an "
				"account number this is genuinely optional — ERPNext names an "
				"unnumbered cost center '<name> - <abbr>'.",
			),
			"parent_cost_center": _field(
				_STRING,
				"The group this hangs under. Docname, number or name. Must already "
				"exist and be a group. Required unless the company has no root cost "
				"center at all.",
			),
			"is_group": _field(
				_BOOLEAN,
				"true to create a group (a heading that cannot be posted to). Default "
				"false, a leaf cost center.",
			),
		},
		required=("cost_center_name",),
		mutating=True,
		title="Create a cost center",
		available=_needs_doctype("Cost Center"),
		requires="the Cost Center DocType, which ships with ERPNext's Accounts module",
	),
	"update_cost_center": _tool(
		dimensions.update_cost_center,
		"MUTATING (default OFF). Rename, renumber or disable/enable one Cost "
		"Center. The rename writes the fields and then moves the docname, in that "
		"order — a Cost Center's docname encodes its own number and name and is "
		"built once, so changing one without the other leaves the tree showing "
		"one thing and reporting another.\n\n"
		"Deliberately CANNOT change the parent, and this app ships no tool that "
		"can: reparenting moves no posting but changes which subtotal every "
		"existing one rolls up into, retroactively, for periods already reported.\n\n"
		"Refuses to rename the company's root cost center, which ERPNext requires "
		"to be named exactly after the company. Disabling deletes nothing — the "
		"cost center, its history and its GL entries all remain and still appear "
		"in reports covering them; it drops out of pickers, and the response says "
		"how many postings are affected and whether any children were left "
		"enabled.",
		{
			"name": _field(_STRING, "The cost center: docname, number or name."),
			"company": _COMPANY,
			"new_cost_center_name": _field(
				_STRING,
				"New cost center name. The docname is rebuilt to match.",
			),
			"new_cost_center_number": _field(
				_STRING,
				"New number. Must be free in this company. The docname is rebuilt.",
			),
			"disabled": _field(_BOOLEAN, "true to disable, false to re-enable."),
		},
		required=("name",),
		mutating=True,
		idempotent=True,
		title="Update a cost center",
		available=_needs_doctype("Cost Center"),
		requires="the Cost Center DocType, which ships with ERPNext's Accounts module",
	),
	"create_accounting_dimension": _tool(
		dimensions.create_accounting_dimension,
		"MUTATING (default OFF). Create an Accounting Dimension — a third, fourth "
		"or fifth axis to file postings under, beyond account and cost center — "
		"and add its Link field to the documents that should carry it.\n\n"
		"THE THING TO UNDERSTAND FIRST: an Accounting Dimension does not hold its "
		"own values. It points at a DocType, and every record of that DocType is a "
		"value. So a 'Member' dimension needs a Member DocType. Pass an existing "
		"one as master_doctype, or set create_master_if_missing=true to have a "
		"simple one generated (a value field, a description and a disabled flag), "
		"named so that the record's own name IS the value.\n\n"
		"SIDE EFFECTS. Writes up to three kinds of document: the master DocType "
		"(only when generated — a custom DocType, stored in the database, no files "
		"and no developer mode), the Accounting Dimension record, and one Link "
		"Custom Field per target doctype. All in one transaction, so a failure "
		"leaves none of it. ERPNext will separately, in a background job, add the "
		"same field to every doctype in its own list; that is additive and does "
		"not disturb these.\n\n"
		"JOURNAL ENTRY MEANS THE LINE. ERPNext carries dimensions on Journal Entry "
		"Account, never on the Journal Entry header, because one entry books to "
		"several. Asking for 'Journal Entry' wires up the child table and the "
		"response reports the redirection.\n\n"
		"Refuses if a dimension already exists for that label or that DocType "
		"(ERPNext allows one per DocType), if the master is a Single, a child "
		"table or a core doctype, or if any target doctype already has a field of "
		"that name that is not a Link to this master. Not reversible through this "
		"app: removing a dimension means deleting the record and its custom fields "
		"in the Desk.",
		{
			"dimension_name": _field(
				_STRING,
				"The dimension's label, e.g. 'Member' or 'BBCH Stage'. Its scrubbed "
				"form becomes the fieldname ('member', 'bbch_stage'), which is the key "
				"you then use in a journal entry line's `dimensions` object.",
			),
			"master_doctype": _field(
				_STRING,
				"The DocType whose records are this dimension's values. Defaults to "
				"dimension_name. Pass an existing DocType to reuse it.",
			),
			"create_master_if_missing": _field(
				_BOOLEAN,
				"true to generate the master DocType when it does not exist. Default "
				"FALSE — creating a DocType is a schema change, so it has to be asked "
				"for.",
			),
			"document_types": {
				**_STRING_ARRAY,
				"description": (
					"Which documents should carry this dimension. Default: Journal "
					"Entry, Sales Invoice, Purchase Invoice, Payment Entry. Naming a "
					"child table directly works too."
				),
			},
			"disabled": _field(
				_BOOLEAN,
				"true to create it disabled — the field is added but ERPNext ignores "
				"the dimension. Default false.",
			),
		},
		required=("dimension_name",),
		mutating=True,
		title="Create an accounting dimension",
		available=_needs_doctype("Accounting Dimension"),
		requires="the Accounting Dimension DocType, which ERPNext added in v12",
	),
	"create_dimension_value": _tool(
		dimensions.create_dimension_value,
		"MUTATING (default OFF). Add one value to an Accounting Dimension — which "
		"in ERPNext means creating one record in the DocType that dimension points "
		"at. Find the dimension by its label ('Member'), by its DocType, or by its "
		"docname; the response says which master was written to.\n\n"
		"Where the master names itself from a field (which is how the masters this "
		"app generates work), value_name becomes both the field and the docname, so "
		"'Member-01' reads as 'Member-01' everywhere it is linked. Where the master "
		"names itself some other way — a naming series, say — the value is created "
		"anyway and the response reports the name it actually got.\n\n"
		"Refuses if the dimension does not exist, if its DocType is missing, if a "
		"record of that name is already there, or if extra_fields names a field the "
		"master does not have. Creates exactly one record and touches no ledger.",
		{
			"dimension_name": _field(
				_STRING,
				"The dimension's label, DocType or docname, e.g. 'Member'.",
			),
			"value_name": _field(
				_STRING,
				"The value, e.g. 'Member-01' or 'BBCH-8'. Becomes the record's name "
				"where the master allows it.",
			),
			"extra_fields": _field(
				_OBJECT,
				"Further fields to set on the master record, verbatim, e.g. "
				'{"description": "Retired 2026-01-01", "disabled": 1}. Every key is '
				"checked against the master's own fields; an unknown one is refused by "
				"name rather than dropped.",
			),
		},
		required=("dimension_name", "value_name"),
		mutating=True,
		title="Create a dimension value",
		available=_needs_doctype("Accounting Dimension"),
		requires="the Accounting Dimension DocType, which ERPNext added in v12",
	),
	"set_company_defaults": _tool(
		dimensions.set_company_defaults,
		"MUTATING (default OFF). Point a Company's default account and cost centre "
		"fields at real accounts, in one call. These are what a document reaches "
		"for when nothing on the document says — they change no existing posting, "
		"only what the next one picks by default.\n\n"
		"TYPE-CHECKED, not merely existence-checked. default_receivable_account "
		"must point at a Receivable-type account, default_payable_account at a "
		"Payable, default_bank_account at a Bank, and so on; every field also has "
		"to match the right root type. ERPNext keys party ledgers and ageing "
		"reports off account_type rather than off an account's name or number, so "
		"a mismatched default posts fine and stops reconciling a quarter later. "
		"Also refuses group accounts, disabled accounts, accounts belonging to "
		"another company, and a group cost center.\n\n"
		"IDEMPOTENT. Every field is compared before it is written; a re-run of the "
		"same call changes nothing and says so. The response separates `changed` "
		"from `unchanged`. Nothing is written at all unless every value in the "
		"request validates, so a partially-correct call leaves the company exactly "
		"as it was. Pass an empty string for a field to clear it.\n\n"
		"COVERS THE FIELDS A MODULE WILL NOT SAVE A DOCUMENT WITHOUT, not just the "
		"obvious ones: disposal_account (ERPNext refuses to scrap or sell an Asset "
		"without it, and says so from the Asset rather than from the Company), the "
		"stock and asset received-but-not-billed accounts, capital work in "
		"progress, the advance received/paid accounts, and the selling and buying "
		"cost centers. A field this site's ERPNext does not have is refused by "
		"name rather than silently skipped.",
		{
			"company": _COMPANY,
			"defaults": _field(
				_OBJECT,
				"Company field → account (docname, number or name), e.g. "
				'{"default_receivable_account": "1200", "default_bank_account": '
				'"1110", "round_off_cost_center": "Main"}. Supported keys: '
				+ ", ".join(dimensions.SUPPORTED_COMPANY_DEFAULTS)
				+ ". round_off_cost_center takes a Cost Center; every other key takes "
				"an Account. An unsupported key is refused by name.",
			),
		},
		required=("company", "defaults"),
		mutating=True,
		idempotent=True,
		title="Set company defaults",
	),
	# ── workflow ────────────────────────────────────────────────────────────
	"list_workflows": _tool(
		workflow.list_workflows,
		"Every Workflow on this site: which DocType it governs, its states with "
		"their docstatus and editing role, its transitions (from state, action, "
		"to state, allowed role, condition), which states are terminal, and every "
		"role involved. Call this to learn a site's approval structure before "
		"asking about any individual document. Read-only.",
		{},
		title="List workflows",
	),
	"get_workflow_state": _tool(
		workflow.get_workflow_state,
		"Where one document sits in its workflow and where it could go next: the "
		"current state, its docstatus, and every outgoing transition with the role "
		"allowed to take it. Answers 'who can move this'; use "
		"list_available_actions for 'can I move this'. Read-only.",
		{
			"doctype": _field(_STRING, "DocType of the document, e.g. 'Purchase Order'."),
			"name": _field(_STRING, "The document's docname."),
		},
		required=("doctype", "name"),
		title="Workflow state of a document",
	),
	"list_pending_approvals": _tool(
		workflow.list_pending_approvals,
		"The worklist: documents parked in a workflow state that still has an "
		"action available, grouped by workflow and state, with the roles that can "
		"act. Pass `user` to narrow it to states that user's roles can act on — "
		"that is the 'what is waiting on me' question. States with no outgoing "
		"transition are finished and are never listed. Read-only.",
		{
			"user": _field(
				_STRING,
				"Only states this user's roles can act on. Omit for everything pending across the site.",
			),
			"workflow": _field(_STRING, "Restrict to one Workflow by name."),
			"limit": _field(
				_INTEGER,
				"Maximum documents per state. Default 100, hard maximum 500.",
			),
		},
		title="Pending approvals",
	),
	"list_available_actions": _tool(
		workflow.list_available_actions,
		"What workflow actions the acting MCP user can take on this document right "
		"now, resolved through Frappe's own get_transitions where the site exports "
		"it — so transition conditions and the self-approval rule are honoured. "
		"The response says whether conditions were evaluated; when they were not, "
		"the list is a superset. Read-only.",
		{
			"doctype": _field(_STRING, "DocType of the document."),
			"name": _field(_STRING, "The document's docname."),
		},
		required=("doctype", "name"),
		title="Available workflow actions",
	),
	"advance_workflow": _tool(
		workflow.advance_workflow,
		"MUTATING (default OFF). Take a workflow action on a document, through "
		"Frappe's own apply_workflow — the same path the Desk button uses.\n\n"
		"RISK: a transition into a state whose doc_status is 1 SUBMITS the "
		"document. On a Journal Entry that writes GL Entries and moves balances; "
		"on an invoice it books revenue. A transition into doc_status 2 CANCELS "
		"it. What a given action does therefore depends on the site's workflow "
		"design, not on this tool — call list_workflows and read the target "
		"state's doc_status before you rely on an action being harmless.\n\n"
		"USE dry_run=true FIRST. It reports exactly what would happen — the "
		"target state, whether the document would be submitted or cancelled, and "
		"whether the action is even available to the acting user — without doing "
		"any of it. Show that to the human, get agreement, then call again with "
		"dry_run=false. A dry run never fails for an unavailable action; it "
		"reports would_succeed=false and why.\n\n"
		"Refuses, listing what IS available, if the action is not open to the "
		"acting user in the document's current state.",
		{
			"doctype": _field(_STRING, "DocType of the document."),
			"name": _field(_STRING, "The document's docname."),
			"action": _field(
				_STRING,
				"The transition's action label, exactly as list_available_actions "
				"reports it, e.g. 'Approve'.",
			),
			"dry_run": _field(
				_BOOLEAN,
				"true = report what would happen and change nothing. Default false. "
				"Always worth doing first on a transition you have not taken before.",
			),
		},
		required=("doctype", "name", "action"),
		mutating=True,
		destructive=True,
		title="Advance a workflow",
	),
	# ── reports ─────────────────────────────────────────────────────────────
	"list_reports": _tool(
		reports.list_reports,
		"Every Report on this site with its ref_doctype (what it reports on), "
		"report_type (Query Report / Script Report / Report Builder), module and "
		"whether it is disabled. The site's reports are where its accounting "
		"questions have already been answered correctly — check here before "
		"assembling an answer out of primitive queries. Read-only.",
		{
			"module": _field(_STRING, "Restrict to one module, e.g. 'Accounts'."),
			"is_standard": _field(
				_STRING,
				"'Yes' for reports shipped by an app, 'No' for ones built on this site. Omit for both.",
			),
		},
		title="List reports",
	),
	"run_report": _tool(
		reports.run_report,
		"Run a saved report and return its columns and rows. Handles Query and "
		"Script Reports through Frappe's own runner and Report Builder reports "
		"through their saved configuration. Filters are the report's own filter "
		"fieldnames. Unlike the other read tools this one enforces Frappe "
		"permissions, because it runs through the Desk APIs. Read-only.",
		{
			"name": _field(_STRING, "Report docname, exactly as list_reports gives it."),
			"filters": _field(
				_OBJECT,
				"The report's filters as an object, e.g. "
				'{"company": "Example Trading Co", "from_date": "2026-01-01"}. '
				"Omit for the report's defaults.",
			),
			"user": _field(
				_STRING,
				"Run as this user instead of the configured MCP user. Their permissions apply.",
			),
			"limit": _field(
				_INTEGER,
				"Maximum rows returned. Default 100, hard maximum 500. "
				"total_rows reports how many the report produced.",
			),
		},
		required=("name",),
		title="Run a report",
	),
	# ── attachments ─────────────────────────────────────────────────────────
	"list_attachments": _tool(
		files.list_attachments,
		"Every File attached to one document: file name, URL, size, private flag, "
		"who uploaded it and when. Checks read permission on the parent document "
		"first — the attachment tools honour Frappe permissions even though the "
		"ledger read tools do not. Read-only.",
		{
			"doctype": _field(_STRING, "DocType the attachments hang off."),
			"name": _field(_STRING, "That document's docname."),
		},
		required=("doctype", "name"),
		title="List attachments",
	),
	"get_attachment_content": _tool(
		files.get_attachment_content,
		"One attachment's content, base64-encoded, with its mime type. Refuses "
		"anything over the size cap (default 2 MB) with the actual size and the "
		"file_url to fetch it from instead — base64 inflates by a third, so "
		"prefer files measured in kilobytes. Enforces read permission on the "
		"parent document, and treats an unattached private file as its owner's. "
		"Read-only.",
		{
			"name": _field(
				_STRING,
				"The File docname (not the filename) — list_attachments gives it.",
			),
			"max_bytes": _field(
				_INTEGER,
				"Raise or lower the size cap. Default 2097152 (2 MB), hard ceiling 8388608 (8 MB).",
			),
		},
		required=("name",),
		title="Read an attachment",
	),
	"attach_file_to_document": _tool(
		files.attach_file_to_document,
		"MUTATING (default OFF). Attach one file to ANY document on this site — a "
		"brokerage statement onto the Journal Entry that books it, a receipt onto "
		"a Bank Transaction, a purchase contract onto an Asset. Creates a File "
		"linked to that record and nothing else: no balance moves, no docstatus "
		"changes, no existing row is touched.\n\n"
		"NOT attach_governance_document. That one files a NEW Governance Document "
		"and attaches to it, which is right for a trust instrument and useless for "
		"putting December's statement on December's entry. This attaches to the "
		"record you name.\n\n"
		"WHAT IT REFUSES, ALL OF IT READ OFF THE SITE. A doctype or docname that "
		"does not exist. A parent the acting user cannot WRITE (the same "
		"permission the Desk's attach control needs). A CANCELLED parent, unless "
		"allow_cancelled=true — growing the evidence file of an undone document is "
		"rarely meant. A second attachment with a filename the document already "
		"has, naming the existing File so a re-run of a batch attach can skip it. "
		"The parent doctype's own max_attachments. Whatever extension allowlist "
		"System Settings declares — this app carries no list of its own. And "
		"`company`, when given, must match the parent's; a company argument on a "
		"doctype with no company field is an error rather than a guard that "
		"silently did nothing.\n\n"
		"`file_content` is base64 of the bytes, ceiling 8 MB — base64 in a JSON "
		"call is expensive, so a large statement is better uploaded in the Desk "
		"and recorded here with `file_url`. Files are PRIVATE by default: reading "
		"one back through get_attachment_content then requires read permission on "
		"the parent. The result and the audit log both carry the sha256 of what "
		"was stored.\n\n"
		"`dry_run` defaults to FALSE — one File is not worth two round trips. Pass "
		"dry_run=true to validate a parent and see the proposed action without "
		"writing, which is what a batch script should do over its target list once "
		"before running live.",
		{
			"doctype": _field(
				_STRING,
				"The parent DocType to attach to: 'Journal Entry', 'Bank Transaction', 'Asset'.",
			),
			"name": _field(_STRING, "That document's docname, e.g. 'ACC-JV-2026-02329'."),
			"file_name": _field(
				_STRING,
				"Filename to store it as, e.g. 'wfa-statement-2025-12-31.pdf'. Required — an "
				"attachment nobody can identify later is not evidence of anything.",
			),
			"file_content": _field(
				_STRING, "The bytes, base64-encoded, with no data: prefix. Not with file_url."
			),
			"file_url": _field(
				_STRING, "Where the file already lives, instead of uploading it. Not with file_content."
			),
			"is_private": _field(_BOOLEAN, "Store as a private File. Default TRUE. Leave it true."),
			"company": _field(
				_STRING,
				"Optional guard: refuse unless the parent belongs to this company.",
			),
			"allow_cancelled": _field(
				_BOOLEAN,
				"Attach to a cancelled (docstatus 2) parent anyway. Default false.",
			),
			"dry_run": _field(
				_BOOLEAN,
				"Validate the parent and report the proposed attach without writing. Default false.",
			),
		},
		required=("doctype", "name", "file_name"),
		mutating=True,
		title="Attach a file to a document",
	),
	# ── comments and tasks ──────────────────────────────────────────────────
	"list_comments": _tool(
		collab.list_comments,
		"The comment and activity thread on one document, oldest first, with "
		"author and comment_type. Frappe keeps framework chatter ('Info', "
		"'Assigned', 'Workflow', 'Edit') in the same table as things people "
		"typed ('Comment') — filter with comment_type. Checks read permission on "
		"the document. Read-only.",
		{
			"doctype": _field(_STRING, "DocType of the document."),
			"name": _field(_STRING, "That document's docname."),
			"comment_type": _field(
				_STRING,
				"Restrict to one type, e.g. 'Comment' for human remarks only.",
			),
			"limit": _LIMIT,
		},
		required=("doctype", "name"),
		title="List comments",
	),
	"list_assigned_todos": _tool(
		collab.list_assigned_todos,
		"ToDos assigned to somebody — Frappe's built-in task list, and what the "
		"Desk's assignment feature writes. Open ones by default. Each row is "
		"flagged `overdue` when its date has passed. Note the assignee lives in "
		"`allocated_to`, not `owner`, which is whoever created the row; the "
		"response normalises it to `assigned_to`. Read-only.",
		{
			"user": _field(_STRING, "Assignee to filter by. Omit for everyone."),
			"status": _field(_STRING, "'Open' (default), 'Closed' or 'Cancelled'. Empty for all."),
			"limit": _LIMIT,
		},
		title="List assigned ToDos",
	),
	"create_todo": _tool(
		collab.create_todo,
		"MUTATING (default OFF). Assign a ToDo to a user, optionally against a "
		"document. Touches no ledger and submits nothing, but it does put an item "
		"in somebody's queue. `owner` is the person it is assigned TO. ToDo has no "
		"subject field on stock Frappe, so `subject` becomes the first line of the "
		"description — the response says which happened.",
		{
			"subject": _field(_STRING, "One-line summary of the task."),
			"owner": _field(
				_STRING,
				"The User it is assigned to (their email/username). Must exist and be enabled.",
			),
			"description": _field(_STRING, "Longer detail, appended below the subject."),
			"priority": _field(_STRING, "'Low', 'Medium' (default) or 'High'."),
			"reference_doctype": _field(_STRING, "DocType this task is about. Pass with reference_name."),
			"reference_name": _field(_STRING, "That document's docname."),
			"date": _field(_STRING, "Due date, YYYY-MM-DD."),
		},
		required=("subject", "owner"),
		mutating=True,
		title="Create a ToDo",
	),
	# ── HR (only where the hrms app is installed) ───────────────────────────
	"list_employees": _tool(
		hr.list_employees,
		"Employee records — docname, employee_number, name, department, "
		"designation, status and joining date — active ones by default. Pass the "
		"docname to the other HR tools. Read-only.",
		{
			"status": _field(_STRING, "'Active' (default), 'Inactive', 'Left', 'Suspended'. Empty for all."),
			"department": _field(_STRING, "Department docname."),
			"designation": _field(_STRING, "Designation docname."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		title="List employees",
		available=_app_installed("hrms"),
		requires="the Frappe HR (hrms) app, which is not installed on this site",
	),
	"get_attendance_summary": _tool(
		hr.get_attendance_summary,
		"Per-employee counts of Present / Absent / Half Day / On Leave over a date "
		"range, plus site-wide totals. Aggregated rather than day-by-day, because "
		"a month for a team is a thousand rows that say what a count says. Counts "
		"submitted Attendance only. Read-only.",
		{
			"from_date": _field(_STRING, "Start of the range, YYYY-MM-DD."),
			"to_date": _field(_STRING, "End of the range, YYYY-MM-DD."),
			"employee": _field(_STRING, "One employee: docname, employee_number, name or user id."),
			"department": _field(_STRING, "Restrict to one department."),
		},
		required=("from_date", "to_date"),
		title="Attendance summary",
		available=_app_installed("hrms"),
		requires="the Frappe HR (hrms) app, which is not installed on this site",
	),
	"get_leave_balance": _tool(
		hr.get_leave_balance,
		"Remaining leave for one employee, per leave type, as of a date "
		"(default today). Computed by HR's own get_leave_balance_on, so "
		"carry-forward and expiry rules apply — do not try to reproduce this by "
		"subtracting applications from allocations. Omit leave_type for every "
		"type the employee has an allocation for. Read-only.",
		{
			"employee": _field(_STRING, "Employee docname, employee_number, name or user id."),
			"leave_type": _field(_STRING, "One Leave Type. Omit for all allocated types."),
			"as_of": _field(_STRING, "Balance date, YYYY-MM-DD. Defaults to today."),
		},
		required=("employee",),
		title="Leave balance",
		available=_app_installed("hrms"),
		requires="the Frappe HR (hrms) app, which is not installed on this site",
	),
	# ── sales and purchasing ────────────────────────────────────────────────
	"list_sales_orders": _tool(
		trade.list_sales_orders,
		"Sales Order headers by status, date range, customer and company — with "
		"grand_total, delivery_date and per_delivered / per_billed percentages, "
		"newest first. Read-only.",
		{
			"status": _field(
				_STRING,
				"ERPNext Sales Order status, e.g. 'To Deliver and Bill', 'To Bill', 'Completed', 'Closed'.",
			),
			"from_date": _field(_STRING, "Earliest transaction_date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest transaction_date, YYYY-MM-DD."),
			"customer": _field(_STRING, "Customer docname."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		title="List sales orders",
		available=_app_installed("erpnext"),
		requires="the ERPNext app",
	),
	"get_outstanding_invoices": _tool(
		trade.get_outstanding_invoices,
		"Submitted Sales Invoices with outstanding_amount > 0, aged against a "
		"date: each row carries days_overdue and an ageing_bucket, and the "
		"response totals each bucket. Buckets are 'current' (not yet due), "
		"'0-30', '31-60', '61-90', '90+' and 'unknown' (no due date) — invoices "
		"not yet due are kept out of 0-30, which would otherwise overstate the "
		"exposure. Read-only.",
		{
			"customer": _field(_STRING, "Customer docname. Omit for all."),
			"company": _COMPANY,
			"as_of": _field(_STRING, "Date to age against, YYYY-MM-DD. Defaults to today."),
			"limit": _LIMIT,
		},
		title="Outstanding invoices",
		available=_app_installed("erpnext"),
		requires="the ERPNext app",
	),
	"list_purchase_orders": _tool(
		trade.list_purchase_orders,
		"Purchase Order headers by status, date range, supplier and company — "
		"with grand_total, schedule_date and per_received / per_billed "
		"percentages, newest first. Read-only.",
		{
			"status": _field(
				_STRING,
				"ERPNext Purchase Order status, e.g. 'To Receive and Bill', "
				"'To Bill', 'Completed', 'Closed'.",
			),
			"from_date": _field(_STRING, "Earliest transaction_date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest transaction_date, YYYY-MM-DD."),
			"supplier": _field(_STRING, "Supplier docname."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		title="List purchase orders",
		available=_app_installed("erpnext"),
		requires="the ERPNext app",
	),
	# ── site customisation ──────────────────────────────────────────────────
	"list_custom_fields": _tool(
		meta.list_custom_fields,
		"Custom Fields on this site, optionally for one DocType, in form order "
		"with their fieldtype, insert_after, depends_on and hidden/read-only "
		"flags. This is the tool for 'why is my custom field not showing up'. "
		"Read-only.",
		{
			"doctype": _field(_STRING, "Restrict to fields added to this DocType."),
			"limit": _LIMIT,
		},
		title="List custom fields",
	),
	"list_client_scripts": _tool(
		meta.list_client_scripts,
		"Client Scripts (form JavaScript) with their target DocType, view, enabled "
		"flag and the first 500 characters of the body — enough to recognise a "
		"script without pulling thousands of lines of JS into context. "
		"script_length reports the real size. Read-only.",
		{
			"doctype": _field(_STRING, "Restrict to scripts targeting this DocType."),
			"enabled": _field(_BOOLEAN, "true (default) for enabled only, false for disabled only."),
			"limit": _LIMIT,
		},
		title="List client scripts",
		available=_needs_doctype("Client Script", "Custom Script"),
		requires="the Client Script DocType (or Custom Script, its pre-v13 name)",
	),
	# ── compliance packets ──────────────────────────────────────────────────
	"list_compliance_packets": _tool(
		packets.list_compliance_packets,
		"Which compliance packet types this site can produce, what each is for, "
		"who reads it, and the filters it takes. Packet types are site-dependent "
		"— some need apps this site may not have, and each has its own switch — "
		"so call this before generate_compliance_packet rather than guessing a "
		"packet_type. Read-only.",
		{},
		title="List compliance packets",
	),
	"generate_compliance_packet": _tool(
		packets.generate_compliance_packet,
		"Build a compliance packet: a structured, self-describing JSON artefact "
		"for somebody who has to sign something off. Unlike the individual read "
		"tools, a packet carries its own provenance (when, as whom, on which "
		"site, and the MCP Action Log row for this call) and its own `flags` — "
		"anomalies it detected in itself, each INFO/WARN/ERROR. An ERROR flag "
		"means the numbers do not internally agree and the packet should not be "
		"signed.\n\n"
		"Nothing is stored, emailed or filed — the packet is returned to you to "
		"render or hand on. Read-only.\n\n"
		"Call list_compliance_packets for the available packet_types and the "
		"filter shape each one takes.",
		{
			"packet_type": _field(
				_STRING,
				"e.g. 'reconciliation_packet' or 'fiscal_year_audit_packet'. "
				"list_compliance_packets has the current set.",
			),
			"filters": _field(
				_OBJECT,
				"The packet's own arguments, e.g. "
				'{"account": "1100", "period_start": "2026-07-01", '
				'"period_end": "2026-07-31"}. Unknown keys are rejected by name '
				"rather than ignored — a packet scoped differently from what you "
				"asked for is worse than an error.",
			),
		},
		required=("packet_type",),
		title="Generate a compliance packet",
	),
	# ── the member register, its event trail and the governance archive ─────
	"list_cap_table": _tool(
		governance.list_cap_table,
		"One company's member register: for every member, the anonymous id the ledger "
		"is tagged with (Member-01), the legal entity behind it, entity type, "
		"admission and withdrawal dates and ownership percentage. Retired members are "
		"INCLUDED by default — the postings they are tagged on do not disappear when "
		"they leave, so neither should the row that explains them.\n\n"
		"This is the only place on the site that maps an anonymous member id to a "
		"legal name; the chart of accounts, the cost center tree and every journal "
		"entry stay anonymous. The response also totals active ownership and says "
		"whether it comes to 100%. Read-only.",
		{
			"company": _COMPANY,
			"include_retired": _field(
				_BOOLEAN,
				"false to list only current members. Default TRUE, because a cap table "
				"that hides its history cannot explain an old posting.",
			),
		},
		required=("company",),
		title="List the cap table",
		available=_needs_doctype("Cap Table Entry"),
		requires="the Cap Table Entry DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"list_member_events": _tool(
		governance.list_member_events,
		"The equity trail: contributions, distributions, admissions, withdrawals, "
		"transfers and reallocations, newest first, each with its amount, the "
		"Journal Entry that books it (where there is one) and the narrative saying "
		"why it happened. Filter by member, event type and date range.\n\n"
		"Legal names are resolved from the Cap Table Entry each event links to; the "
		"events themselves hold only the anonymous member id. An event with "
		"`superseded_by` set has been corrected by a later one and must not be "
		"totalled twice. Read-only.",
		{
			"company": _COMPANY,
			"member": _field(
				_STRING,
				"One member: a Cap Table Entry docname or a member_id such as 'Member-01'. Omit for all.",
			),
			"event_type": _field(
				_STRING,
				"Contribution, Distribution, Admission, Withdrawal, Transfer or Reallocation.",
			),
			"from_date": _field(_STRING, "Earliest effective_date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest effective_date, YYYY-MM-DD."),
			"include_superseded": _field(
				_BOOLEAN, "false to hide events a later correction has superseded. Default true."
			),
			"limit": _LIMIT,
		},
		required=("company",),
		title="List member events",
		available=_needs_doctype("Member Event"),
		requires="the Member Event DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"list_governance_documents": _tool(
		governance.list_governance_documents,
		"What is in the governance archive for one company: operating agreements, "
		"trust documents, advisory agreements, board resolutions, prior statements "
		"and amendments, with effective and execution dates, the amendment chain "
		"(`supersedes` / `superseded_by`) and how many files are attached to each.\n\n"
		"`operative` is true for a document nothing has superseded — those are the "
		"ones in force. Read the content of one with "
		"get_governance_document_content. Read-only.",
		{
			"company": _COMPANY,
			"category": _field(
				_STRING,
				"Operating Agreement, Trust Document, Advisory Agreement, Board "
				"Resolution, Prior Statement, Amendment or Other. Omit for all.",
			),
			"include_superseded": _field(
				_BOOLEAN,
				"false to list only the documents currently in force. Default true — an "
				"archive that drops what it replaced cannot answer 'what applied in 2031'.",
			),
			"limit": _LIMIT,
		},
		required=("company",),
		title="List governance documents",
		available=_needs_doctype("Governance Document"),
		requires="the Governance Document DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"get_governance_document_content": _tool(
		governance.get_governance_document_content,
		"One archived governing document: its metadata, its place in the amendment "
		"chain, and the bytes of its attachment, base64-encoded. The content goes "
		"through the same path get_attachment_content uses, so the same read "
		"permission on the parent document and the same size cap apply — a "
		"governing document is exactly the kind of file those checks exist for.\n\n"
		"An entry with several attachments returns the first unless `file` names "
		"one, and says so. An entry with none returns its metadata and reports that. "
		"Read-only.",
		{
			"name": _field(_STRING, "Governance Document docname — list_governance_documents gives it."),
			"file": _field(
				_STRING,
				"Which attachment to read, by File docname or file name. Omit for the first.",
			),
			"max_bytes": _field(
				_INTEGER,
				"Raise or lower the size cap. Default 2097152 (2 MB), hard ceiling 8388608 (8 MB).",
			),
		},
		required=("name",),
		title="Read a governance document",
		available=_needs_doctype("Governance Document"),
		requires="the Governance Document DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"create_cap_table_entry": _tool(
		governance.create_cap_table_entry,
		"MUTATING (default OFF). Register one member: the anonymous id the ledger is "
		"tagged with, and the legal entity behind it.\n\n"
		"THE DESIGN THIS BELONGS TO. Family names never go into the chart of "
		"accounts or the cost center tree — those are read by lenders, auditors and "
		"anyone handed an export, and a name once in a statement cannot be taken "
		"out. Postings are tagged with a Member accounting dimension value "
		"('Member-01'); this doctype is the one place that says who that is.\n\n"
		"Refuses before writing anything if the member id is already registered for "
		"that company (one entry per member per company), if the percentage is "
		"outside 0-100, or if the site has a Member accounting dimension and the id "
		"is not one of its values — the cap table names a member the ledger can "
		"already refer to, so the dimension value comes first. A site with no such "
		"dimension yet is allowed and told so.\n\n"
		"Cannot create a member already retired: retiring is close_cap_table_entry, "
		"which records the exit in the event trail instead of flipping a flag. "
		"Reversible in the sense that nothing else is touched — no posting, no "
		"dimension value, no account.",
		{
			"company": _COMPANY,
			"member_id": _field(
				_STRING,
				"The anonymous identifier, e.g. 'Member-01'. This is what journal entry "
				"lines are tagged with, and it is part of the docname; it cannot be "
				"changed afterwards.",
			),
			"legal_entity_name": _field(
				_STRING,
				"The real legal name — an individual, a trust, an LLC. Stored here and "
				"nowhere else on the site.",
			),
			"entity_type": _field(
				_STRING,
				"Individual, Trust, LLC, Corporation, Partnership or Other. Checked "
				"against the doctype's own option list.",
			),
			"admission_date": _field(_STRING, "When this member was admitted, YYYY-MM-DD."),
			"ownership_percentage": _field(_NUMBER, "0-100. The response says whether active members now total 100."),
			"member_cost_center": _field(
				_STRING,
				"Optional, and only for sites whose convention also gives each member a "
				"cost center. Members are a DIMENSION, not a segment of the business, so "
				"every tool here files by the dimension and carries this along.",
			),
			"member_dimension": _field(
				_STRING,
				"The accounting dimension holding member values, if it is not called 'Member'.",
			),
			"notes": _field(_STRING, "Why this member exists, and what paperwork evidences it."),
		},
		required=("company", "member_id", "legal_entity_name", "entity_type", "admission_date"),
		mutating=True,
		title="Register a cap table member",
		available=_needs_doctype("Cap Table Entry"),
		requires="the Cap Table Entry DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"update_cap_table_entry": _tool(
		governance.update_cap_table_entry,
		"MUTATING (default OFF). Change a registered member's legal name, entity "
		"type, admission date, ownership percentage, cost center or notes.\n\n"
		"Deliberately CANNOT do two things. It cannot retire a member — that is "
		"close_cap_table_entry, which takes a withdrawal date and a narrative and "
		"writes a Member Event, so an exit appears in the trail rather than only as "
		"a changed checkbox. And it cannot change the member_id: that is the key "
		"every posting on the site is tagged with, so changing it would leave "
		"journal entry lines pointing at a member that no longer exists.\n\n"
		"Refuses if nothing would change. Historical postings are never touched.",
		{
			"member": _field(_STRING, "The member: a Cap Table Entry docname, or a member_id."),
			"company": _COMPANY,
			"legal_entity_name": _field(_STRING, "New legal name."),
			"entity_type": _field(_STRING, "New entity type."),
			"admission_date": _field(_STRING, "Corrected admission date, YYYY-MM-DD."),
			"ownership_percentage": _field(_NUMBER, "New percentage, 0-100."),
			"member_cost_center": _field(_STRING, "Cost center to carry, or an empty string to clear it."),
			"notes": _field(_STRING, "Replacement notes."),
		},
		required=("member",),
		mutating=True,
		idempotent=True,
		title="Update a cap table member",
		available=_needs_doctype("Cap Table Entry"),
		requires="the Cap Table Entry DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"close_cap_table_entry": _tool(
		governance.close_cap_table_entry,
		"MUTATING (default OFF). Retire a member: set the withdrawal date, mark the "
		"entry retired, and write a Withdrawal event into the trail with the "
		"narrative explaining it.\n\n"
		"MOVES NO MONEY, deliberately. A member leaving usually involves a final "
		"distribution, and that is a separate record_member_event call with its own "
		"amount, accounts and narrative — bundling them would make the tool that "
		"closes a member also a tool that can pay one.\n\n"
		"Nothing is deleted: the entry stays in the register, every posting tagged "
		"with the member id stays exactly as it was, and list_cap_table keeps "
		"showing them. Refuses a member already retired, and a withdrawal date "
		"before the admission date.",
		{
			"member": _field(_STRING, "The member: a Cap Table Entry docname, or a member_id."),
			"withdrawal_date": _field(_STRING, "The exit date, YYYY-MM-DD."),
			"notes": _field(
				_STRING,
				"Why they left and what authorises it. Mandatory, appended to the entry's "
				"notes and used as the Member Event's narrative.",
			),
			"company": _COMPANY,
		},
		required=("member", "withdrawal_date", "notes"),
		mutating=True,
		destructive=True,
		title="Retire a cap table member",
		available=_needs_doctype("Cap Table Entry"),
		requires="the Cap Table Entry DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"record_member_event": _tool(
		governance.record_member_event,
		"MUTATING (default OFF). Record one thing that happened to a member's "
		"interest — and, where it books money, the DRAFT Journal Entry for it.\n\n"
		"WHAT IT WRITES. Always a Member Event, carrying the mandatory narrative: "
		"the numbers survive on their own, the reasons do not, and 'why did "
		"Member-02 take 40,000 in March' is the question asked once the people who "
		"knew have gone. For a Contribution, Distribution, Withdrawal, Transfer or "
		"Reallocation it also creates a draft Journal Entry, unless `offset_je` "
		"names one that already books it. An Admission needs no entry at all.\n\n"
		"THE ENTRY IT BUILDS. Contribution: debit the cash side, credit member "
		"capital. Distribution and Withdrawal: debit member distributions, credit "
		"the cash side. Transfer and Reallocation: debit the capital of `member` "
		"and credit the capital of `counterparty_member` — money never leaves the "
		"company. EVERY line is tagged with the member accounting dimension, "
		"including the cash side, because a balance sheet filtered by member has to "
		"balance.\n\n"
		"ACCOUNTS ARE SHORTLISTED, NEVER GUESSED. With no `capital_account` given, "
		"the company's leaf Equity accounts are matched by name ('Member Capital', "
		"'Distributions'); zero matches or more than one is refused with the "
		"candidates listed. The cash side falls back to the company's default bank "
		"or cash account.\n\n"
		"IT CANNOT POST. The Journal Entry is a draft and has moved no balance. "
		"Posting is submit_member_event, which additionally requires the "
		"submit_journal_entry switch. Refuses without a Member accounting dimension "
		"on Journal Entry Account, since an untagged equity entry is one nobody can "
		"attribute later.",
		{
			"company": _COMPANY,
			"event_type": _field(
				_STRING,
				"Contribution, Distribution, Admission, Withdrawal, Transfer or Reallocation.",
			),
			"effective_date": _field(_STRING, "When it took effect, YYYY-MM-DD. Also the entry's posting date."),
			"amount": _field(
				_NUMBER,
				"Positive. A distribution is its own event type, not a negative "
				"contribution. Zero is only allowed for an Admission.",
			),
			"member": _field(
				_STRING,
				"The member this is about: a Cap Table Entry docname or a member_id. For a "
				"transfer, the member the interest moves FROM.",
			),
			"counterparty_member": _field(
				_STRING, "For a Transfer or Reallocation: the member the interest moves TO. Required for those."
			),
			"narrative": _field(
				_STRING,
				"Why this happened and what authorises it — the resolution, the agreement "
				"clause, the conversation. Mandatory and checked for length.",
			),
			"offset_je": _field(
				_STRING,
				"An existing Journal Entry that already books this event. Given, no entry "
				"is created and the event simply links to it.",
			),
			"capital_account": _field(
				_STRING,
				"The equity account to use, instead of matching one by name. Docname, "
				"number or account name.",
			),
			"counter_account": _field(
				_STRING,
				"The cash/bank side, instead of the company default. Not used by a "
				"Transfer or Reallocation, which have two equity sides.",
			),
			"member_dimension": _field(
				_STRING, "The accounting dimension holding member values, if it is not called 'Member'."
			),
		},
		required=("company", "event_type", "effective_date", "member", "narrative"),
		mutating=True,
		title="Record a member event",
		available=_needs_doctype("Member Event"),
		requires="the Member Event DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"submit_member_event": _tool(
		governance.submit_member_event,
		"MUTATING (default OFF). Post the draft Journal Entry a member event is "
		"waiting on — docstatus 0 → 1, which writes GL Entries and moves balances.\n\n"
		"CHECKS TWO SWITCHES. Its own, and submit_journal_entry's. That second "
		"switch is where an operator decided whether an AI client may move a balance "
		"at all, and a second door into the same room with a different lock would "
		"make the decision meaningless — so this refuses, naming the switch, when "
		"submit_journal_entry is off.\n\n"
		"Takes only an event name: it cannot create the event or the entry it posts. "
		"An event that books no money (an admission, a reallocation of percentages) "
		"has nothing to post and is refused with that said.",
		{"name": _field(_STRING, "Member Event docname — list_member_events gives it.")},
		required=("name",),
		mutating=True,
		title="Post a member event",
		available=_needs_doctype("Member Event"),
		requires="the Member Event DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	"attach_governance_document": _tool(
		governance.attach_governance_document,
		"MUTATING (default OFF). File a governing document in the archive: an "
		"operating agreement, a trust instrument, an advisory agreement, a board "
		"resolution, a prior statement or an amendment — with the document itself "
		"attached.\n\n"
		"THE CHAIN IS THE POINT. An operating agreement amended three times is four "
		"documents, and the question asked in 2050 is 'which one was in force in "
		"2031'. Naming `supersedes` writes the link in both directions, so a reader "
		"can follow the chain forward to whatever is current. Cycles are refused, "
		"and so is superseding a document that has already been superseded — an "
		"amendment goes on the end of the chain, not into the middle.\n\n"
		"CONTENT IS STORED PRIVATE. `file_content` is base64 of the document's "
		"bytes and is attached as a private File on the record; reading it back "
		"goes through get_governance_document_content, which enforces read "
		"permission. Alternatively `file_url` records where an externally hosted "
		"document lives without copying it. There is an 8 MB ceiling on content "
		"moved through a tool call.\n\n"
		"Refuses a second document with the same company, category and title, "
		"because two entries claiming to be the same document is worse than none.",
		{
			"company": _COMPANY,
			"category": _field(
				_STRING,
				"Operating Agreement, Trust Document, Advisory Agreement, Board "
				"Resolution, Prior Statement, Amendment or Other.",
			),
			"title": _field(
				_STRING,
				"How this document is referred to, including its date or version: "
				"'OML Operating Agreement 2020-06-15'.",
			),
			"effective_date": _field(_STRING, "When it takes effect, YYYY-MM-DD."),
			"execution_date": _field(_STRING, "When it was signed, YYYY-MM-DD."),
			"supersedes": _field(_STRING, "The Governance Document this one replaces or amends."),
			"file_content": _field(
				_STRING, "The document's bytes, base64-encoded, with no data: prefix. Needs file_name."
			),
			"file_name": _field(_STRING, "Filename for the uploaded content, e.g. 'operating-agreement-2020.pdf'."),
			"file_url": _field(
				_STRING, "Where the document already lives, instead of uploading it. Not with file_content."
			),
			"parties": _field(_STRING, "Who signed, in plain language. Legal names belong here."),
			"notes": _field(_STRING, "Anything a successor would need to know about this document."),
		},
		required=("company", "category", "title"),
		mutating=True,
		title="File a governance document",
		available=_needs_doctype("Governance Document"),
		requires="the Governance Document DocType, which ships with erpnext_mcp (run bench migrate)",
	),
	# ── assets: usage-based cost splits and note-tenor discipline ───────────
	"depreciation_note_alignment_check": _tool(
		assets.depreciation_note_alignment_check,
		"Where a financed asset's remaining depreciation and its note's remaining "
		"term have parted company. For every asset with a linked note: months "
		"elapsed, months of depreciation left, months of note left, and the delta "
		"between them, with a sentence saying which way it reads — book value "
		"outliving the financing, or interest still being paid on something with no "
		"book value left.\n\n"
		"Reports on every financed asset, not only the broken ones, because "
		"'nothing is wrong' is an answer somebody has to be able to see. A "
		"divergence is not automatically an error; it is something that needs an "
		"explanation, and an explanation nobody wrote down is what this surfaces. "
		"Read-only.",
		{
			"company": _COMPANY,
			"as_of": _field(_STRING, "Date to measure from, YYYY-MM-DD. Defaults to today."),
		},
		required=("company",),
		title="Depreciation / note alignment",
		available=_needs_doctype("Asset"),
		requires="ERPNext's Asset DocType",
	),
	"create_asset": _tool(
		assets.create_asset,
		"MUTATING (default OFF). Create an ERPNext Asset together with the cost "
		"profile that says how it is actually used: a tractor is not a Harvest asset "
		"or a Perennial Care asset, it is 40% one and 60% the other, and its "
		"depreciation should land that way every period without anyone "
		"re-deciding it.\n\n"
		"WHAT IT WRITES. An ordinary ERPNext Asset (a DRAFT — submit it in ERPNext "
		"when the purchase is real), an Asset Cost Profile holding the allocation "
		"and the schedule, and, when the item_code does not exist yet, a "
		"fixed-asset Item. Nothing is grafted onto ERPNext's Asset doctype: no "
		"custom fields, no child tables, so uninstalling this app gives the site "
		"back as it was.\n\n"
		"ERPNEXT'S OWN DEPRECIATION IS SWITCHED OFF on the asset "
		"(calculate_depreciation = 0), and this is the most important thing to "
		"understand about the tool. ERPNext runs a daily job that posts "
		"depreciation for every asset with that flag set, using its own schedule and "
		"its own single cost center. If it also ran here, the asset would depreciate "
		"twice, silently, every month. So this app owns the schedule outright and "
		"run_depreciation_cycle is the only thing that writes for it.\n\n"
		"NOTE TENOR IS ENFORCED BEFORE ANYTHING IS WRITTEN. Name a `linked_note` "
		"and the asset's useful life must equal the note's tenor, so paid-off and "
		"fully-depreciated fall in the same month. A divergence is refused with the "
		"numbers, not silently accepted.\n\n"
		"Refuses an allocation that does not total 100, a group or disabled cost "
		"center, a frequency that does not divide the useful life exactly, a "
		"salvage value at or above the cost, an asset category the site does not "
		"have, and an existing Item that is not flagged as a fixed asset.",
		{
			"company": _COMPANY,
			"asset_name": _field(_STRING, "What the asset is called, e.g. 'Tractor A'."),
			"item_code": _field(
				_STRING,
				"The fixed-asset Item this hangs off. ERPNext requires one; it is created "
				"if missing unless create_item_if_missing is false.",
			),
			"asset_category": _field(
				_STRING,
				"An existing Asset Category. This is where ERPNext keeps the fixed-asset, "
				"accumulated-depreciation and depreciation-expense accounts, per company.",
			),
			"purchase_date": _field(_STRING, "YYYY-MM-DD."),
			"purchase_amount": _field(_NUMBER, "What it cost. Positive."),
			"salvage_value": _field(
				_NUMBER,
				"What it will be worth at the end of its life. Default 0. Depreciation "
				"never takes the book value below it.",
			),
			"useful_life_months": _field(
				_INTEGER,
				"Total life in months. Must be divisible by the frequency, and must equal "
				"the note tenor when a note is linked.",
			),
			"depreciation_frequency_months": _field(
				_INTEGER, "Months per depreciation period: 1 monthly (the default), 3 quarterly, 12 annually."
			),
			"depreciation_method": _field(
				_STRING,
				"Straight Line (default), Written Down Value, Double Declining Balance, or "
				"Manual. Manual means this app computes nothing for the asset.",
			),
			"depreciation_start_date": _field(
				_STRING, "First day of the first period, YYYY-MM-DD. Defaults to the purchase date."
			),
			"cost_center_allocation": {
				"type": "array",
				"description": (
					"How the asset's use is shared out. Objects of {cost_center, "
					"percentage, and optionally bbch_stage and note}, totalling 100. "
					"Omitted, the company's default cost center takes 100%."
				),
				"items": {
					"type": "object",
					"properties": {
						"cost_center": _field(_STRING, "A leaf Cost Center: docname, number or name."),
						"percentage": _field(_NUMBER, "Share of use, above 0 and at most 100."),
						"bbch_stage": _field(
							_STRING,
							"Optional value of this site's BBCH Stage dimension, applied to "
							"the depreciation line this row produces.",
						),
						"note": _field(_STRING, "The usage evidence behind the number."),
					},
					"required": ["cost_center", "percentage"],
					"additionalProperties": False,
				},
			},
			"linked_note": _field(_STRING, "The note financing this asset — a docname."),
			"note_doctype": _field(
				_STRING,
				"Which DocType the note lives in. Worked out from the name where it is a "
				"Notes Payable, a Loan or a Journal Entry.",
			),
			"note_tenor_months": _field(_INTEGER, "The note's term in months, if the note document does not record it."),
			"note_maturity_date": _field(_STRING, "The note's maturity, YYYY-MM-DD, as an alternative to the tenor."),
			"depreciation_expense_account": _field(
				_STRING, "Override the Asset Category's depreciation expense account for this asset."
			),
			"accumulated_depreciation_account": _field(
				_STRING, "Override the Asset Category's accumulated depreciation account for this asset."
			),
			"location": _field(_STRING, "ERPNext Location, where this site's version requires one."),
			"create_item_if_missing": _field(
				_BOOLEAN, "false to refuse rather than create a fixed-asset Item. Default true."
			),
			"notes": _field(_STRING, "Anything about this asset worth keeping with its profile."),
		},
		required=(
			"asset_name",
			"item_code",
			"asset_category",
			"purchase_date",
			"purchase_amount",
			"useful_life_months",
		),
		mutating=True,
		title="Create an asset",
		available=_needs_doctype("Asset"),
		requires="ERPNext's Asset DocType",
	),
	"update_asset_allocation": _tool(
		assets.update_asset_allocation,
		"MUTATING (default OFF). Replace how an asset's cost is shared out across "
		"cost centers. Refuses a set of percentages that does not total 100, a "
		"group or disabled cost center, and a change that would leave the "
		"allocation exactly as it is.\n\n"
		"NOT RETROACTIVE, and that is correct. Depreciation already written keeps "
		"the split it was written with — that is the history, and rewriting it would "
		"change periods that have already been reported. Only future periods follow "
		"the new split, and the response says how many have already been written.",
		{
			"asset": _field(_STRING, "The asset: an Asset docname, or the asset_name it was created with."),
			"new_cost_center_allocation": {
				"type": "array",
				"description": "The replacement split: {cost_center, percentage} objects totalling 100.",
				"items": {
					"type": "object",
					"properties": {
						"cost_center": _field(_STRING, "A leaf Cost Center: docname, number or name."),
						"percentage": _field(_NUMBER, "Share of use, above 0 and at most 100."),
						"bbch_stage": _field(_STRING, "Optional BBCH Stage dimension value for this row's line."),
						"note": _field(_STRING, "The usage evidence behind the number."),
					},
					"required": ["cost_center", "percentage"],
					"additionalProperties": False,
				},
			},
			"company": _COMPANY,
		},
		required=("asset", "new_cost_center_allocation"),
		mutating=True,
		idempotent=True,
		title="Reallocate an asset",
		available=_needs_doctype("Asset"),
		requires="ERPNext's Asset DocType",
	),
	"link_asset_to_note": _tool(
		assets.link_asset_to_note,
		"MUTATING (default OFF). Tie an asset to the note that financed it, and — "
		"by default — refuse the link unless the asset's remaining life equals the "
		"note's remaining term.\n\n"
		"WHY THE REFUSAL IS THE FEATURE. Held apart, the asset is either fully "
		"depreciated while payments continue, or still on the books after the note "
		"is paid off. Either way the matching principle is broken and nobody sees it "
		"until the final year of the loan. Enforcing the match at the moment of "
		"linking is the only cheap place to catch it.\n\n"
		"The tenor is taken from `note_tenor_months`, or from `note_maturity_date`, "
		"or from the note document's own maturity/term field where its doctype has "
		"one — and the response says which. `enforce_tenor=false` links anyway and "
		"records the divergence, which depreciation_note_alignment_check will keep "
		"reporting. Changes no schedule and writes no posting.",
		{
			"asset": _field(_STRING, "The asset: an Asset docname, or its asset_name."),
			"note_doc_ref": _field(
				_STRING, "The note: a Journal Entry docname, or a record of the site's own notes doctype."
			),
			"note_doctype": _field(
				_STRING,
				"Which DocType the note lives in. Worked out from the name where it is a "
				"Notes Payable, a Loan or a Journal Entry.",
			),
			"note_tenor_months": _field(_INTEGER, "The note's term in months."),
			"note_maturity_date": _field(_STRING, "The note's maturity, YYYY-MM-DD."),
			"enforce_tenor": _field(
				_BOOLEAN,
				"true (THE DEFAULT) refuses a life that does not match the tenor. false "
				"links anyway and records the divergence.",
			),
			"company": _COMPANY,
		},
		required=("asset", "note_doc_ref"),
		mutating=True,
		title="Link an asset to its note",
		available=_needs_doctype("Asset"),
		requires="ERPNext's Asset DocType",
	),
	"run_depreciation_cycle": _tool(
		assets.run_depreciation_cycle,
		"MUTATING (default OFF). Write the depreciation due up to a date for every "
		"asset with a cost profile: one DRAFT Journal Entry per asset per period, "
		"debiting depreciation expense split across the asset's cost centers and "
		"crediting accumulated depreciation in one line.\n\n"
		"dry_run DEFAULTS TO TRUE. A dry run writes nothing and returns every period "
		"it would post, with the exact split per cost center. Read it, then call "
		"again with dry_run=false. This is the one tool here that writes to many "
		"documents at once, and a catch-up over a year of missed periods is a page "
		"of journal entries somebody should see first.\n\n"
		"IDEMPOTENT BY RECORD. Every period written is recorded on the asset's cost "
		"profile with the entry that carries it, so running twice cannot post a "
		"period twice. The amounts are computed from the profile each time rather "
		"than read from saved rows, so a catch-up produces exactly what month-by-"
		"month running would have.\n\n"
		"The entries are DRAFTS and have moved no balance — post them with "
		"submit_journal_entry. Assets on the Manual method, assets with nothing due, "
		"and assets whose depreciation accounts are not configured are skipped and "
		"listed with the reason, rather than taking the whole run down.",
		{
			"company": _COMPANY,
			"period_end": _field(
				_STRING, "Depreciate everything whose period ends on or before this date. Defaults to today."
			),
			"asset": _field(_STRING, "Restrict the run to one asset. Omit for every asset in the company."),
			"dry_run": _field(
				_BOOLEAN,
				"true (THE DEFAULT) = report every period that would be written and change "
				"nothing. Set false only after a human has read the plan.",
			),
		},
		required=("company",),
		mutating=True,
		title="Run a depreciation cycle",
		available=_needs_doctype("Asset"),
		requires="ERPNext's Asset DocType",
	),
	# ── notes payable: what the company owes, and on what terms ─────────────
	"list_notes_payable": _tool(
		notes.list_notes_payable,
		"Every note or loan one company owes: the lender, the original principal, "
		"what is still outstanding, the rate and term, the liability account it "
		"posts to, how many payments have been recorded and when the next one "
		"falls due. Closed notes are included by default — a note that has been "
		"paid off is part of the history.\n\n"
		"OUTSTANDING BALANCES HERE ARE A CONVENIENCE FIGURE, maintained by "
		"record_loan_payment. They diverge from the ledger by any payment recorded "
		"as a draft nobody has posted, which in this app is the normal state. "
		"get_account_balance on the note's linked_gl_account is the ledger's "
		"answer. `next_payment_date` is projected from the payment frequency and "
		"the last payment recorded; it is not a schedule the lender agreed to. "
		"Read-only.",
		{
			"company": _COMPANY,
			"borrower": _field(_STRING, "Same as company — the entity that owes. Either name works."),
			"status": _field(
				_STRING,
				"Restrict to one status: Active, Paid Off, Refinanced, Written Off or Superseded.",
			),
			"include_closed": _field(
				_BOOLEAN,
				"false to list only notes that are still Active. Default true.",
			),
			"limit": _LIMIT,
		},
		title="List notes payable",
		available=_needs_doctype("Note Payable"),
		requires="the Note Payable DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_note_payable": _tool(
		notes.create_note_payable,
		"MUTATING (default OFF). Register one outstanding note or loan: who is "
		"owed, what was borrowed, what is left, the rate and term, the liability "
		"account it posts to, and which paper says so.\n\n"
		"WHY THIS AND NOT ERPNEXT'S LOAN MODULE: ERPNext's Loan models the company "
		"as the LENDER, with an application, a disbursement and half a dozen "
		"doctypes. A holding company with four notes outstanding is on the other "
		"side of all of it.\n\n"
		"WHAT IT ADDS TO THE LIABILITY ACCOUNT THAT ALREADY EXISTS: terms (a "
		"balance on 2310 does not say when it is due), provenance (what was agreed, "
		"by whom, where the original is), and what it secures.\n\n"
		"related_asset TIES IT TO link_asset_to_note. Setting it points the asset's "
		"cost profile back at this note and runs the same tenor check: by default "
		"it REFUSES if the asset's useful life does not equal the note's term, "
		"because an asset fully depreciated while payments continue — or still on "
		"the books after the note is paid — is invisible until the last year of the "
		"loan. Pass enforce_asset_tenor=false when the divergence is deliberate. "
		"The note and the link are one transaction: a refused link leaves no note.\n\n"
		"Also refuses a duplicate note name for the same borrower, a non-positive "
		"principal, a negative outstanding balance, a maturity before origination, "
		"interest_type='Zero' with a non-zero rate, a linked_gl_account that is not "
		"a plain Liability (a Payable- or Receivable-typed account would show up as "
		"a supplier balance that never ages out), and an interest_expense_account "
		"that is not an Expense.\n\n"
		"principal_outstanding here is a CONVENIENCE figure. The ledger's answer is "
		"the balance of linked_gl_account.",
		{
			"note_name": _field(
				_STRING,
				"What this note is called, e.g. 'Umpqua Bank - GP Graders Automatic "
				"Defect Sorter'. Unique per borrower; the docname is built from it.",
			),
			"lender": _field(
				_STRING,
				"Who is owed: a bank, an estate, an individual. Free text — the lender on "
				"a family note is usually not a Supplier on this site.",
			),
			"borrower": _field(_STRING, "The company that owes. Same as company; either works."),
			"company": _COMPANY,
			"principal_original": _field(_NUMBER, "What was originally borrowed. Positive."),
			"principal_outstanding": _field(
				_NUMBER,
				"What is still owed on principal today. Defaults to principal_original — "
				"set it when bringing an existing note onto the books mid-term.",
			),
			"interest_rate": _field(_NUMBER, "Annual rate as a percentage, 0-100."),
			"interest_type": _field(_STRING, "Fixed (the default), Variable or Zero."),
			"origination_date": _field(_STRING, "When the note was made, YYYY-MM-DD."),
			"maturity_date": _field(
				_STRING,
				"When the last payment is due, YYYY-MM-DD. Optional, but link_asset_to_note "
				"reads it to work out the term — without it there is no tenor to check.",
			),
			"payment_frequency": _field(
				_STRING,
				"Monthly (the default), Quarterly, Annual, Balloon or Custom. Drives the "
				"next-payment estimate in list_notes_payable.",
			),
			"payment_amount": _field(_NUMBER, "The scheduled payment, principal and interest together."),
			"linked_gl_account": _field(
				_STRING,
				"The Notes Payable liability account on the borrower's chart. Debited for "
				"the principal half of every payment recorded against this note.",
			),
			"interest_expense_account": _field(
				_STRING,
				"Debited for the interest half. record_loan_payment takes an override and "
				"refuses rather than guessing when neither is set.",
			),
			"related_asset": _field(
				_STRING,
				"The Asset this note financed. Also links the asset's cost profile back at "
				"this note — see enforce_asset_tenor.",
			),
			"enforce_asset_tenor": _field(
				_BOOLEAN,
				"true (the default) = refuse if the related asset's useful life does not "
				"equal this note's term. false = link anyway; "
				"depreciation_note_alignment_check keeps reporting the divergence.",
			),
			"document_reference": _field(
				_STRING,
				"Where the paper lives: a Governance Document docname, or a plain "
				"description of the physical original.",
			),
			"notes": _field(_STRING, "What a successor trustee would need to know."),
		},
		required=("note_name", "lender", "principal_original", "origination_date"),
		mutating=True,
		title="Create a note payable",
		available=_needs_doctype("Note Payable"),
		requires="the Note Payable DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"record_loan_payment": _tool(
		notes.record_loan_payment,
		"MUTATING (default OFF). Record one payment against a note: a DRAFT Journal "
		"Entry debiting the liability for the principal, the expense account for "
		"the interest, and crediting the bank for the whole thing — plus a row in "
		"the note's history and a decrement of its outstanding balance.\n\n"
		"THE SPLIT IS THE WHOLE JOB. A payment leaving a bank account is one "
		"number, and its two halves land in completely different places: one "
		"reduces a liability, one is an expense of the period. Booked as a single "
		"line against the liability, the year's interest expense reads as nil and "
		"the balance sheet says the note was paid down by more than it was. Pass "
		"principal_split, interest_split, or one and let the other be derived; the "
		"two must add up to total_amount or nothing is written.\n\n"
		"REFUSES a note that is already closed, a payment dated before the note was "
		"originated, a principal component larger than the balance outstanding (a "
		"payment clearing more principal than is owed is either the wrong split or "
		"a stale balance — neither is fixed by writing a negative one), a negative "
		"component, and an interest component with no expense account to put it in.\n\n"
		"THE DRAFT IS THE POINT. Nothing is posted. The note's outstanding figure "
		"IS decremented immediately, so until the entry is submitted this record "
		"and the liability account disagree by the principal — the response says so "
		"every time.",
		{
			"note": _field(_STRING, "Note Payable docname, or the note_name it was created with."),
			"company": _COMPANY,
			"payment_date": _field(_STRING, "YYYY-MM-DD. The Journal Entry's posting date."),
			"total_amount": _field(_NUMBER, "The whole payment that left the bank. Positive."),
			"principal_split": _field(_NUMBER, "How much of it reduces the note. Derived if only interest is given."),
			"interest_split": _field(_NUMBER, "How much of it is interest. Derived if only principal is given."),
			"offset_bank_account": _field(
				_STRING,
				"Where the money came from: a Bank Account record (preferred — the journal "
				"line then carries it, which is what lets a bank reconciliation match this "
				"entry) or the GL account itself.",
			),
			"notes_payable_account": _field(
				_STRING,
				"Override the liability account debited for the principal. Defaults to the "
				"note's linked_gl_account.",
			),
			"interest_expense_account": _field(
				_STRING,
				"Override the expense account debited for the interest. Defaults to the note's own.",
			),
			"narrative": _field(_STRING, "Anything about this payment worth keeping. Optional."),
		},
		required=("note", "payment_date", "total_amount", "offset_bank_account"),
		mutating=True,
		title="Record a loan payment",
		available=_needs_doctype("Note Payable"),
		requires="the Note Payable DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"close_note_payable": _tool(
		notes.close_note_payable,
		"MUTATING (default OFF). Close a note — Paid Off, Refinanced or Written Off "
		"— recording the disposition and its narrative in the note's own event "
		"history rather than as a silently changed field.\n\n"
		"WRITES NO JOURNAL ENTRY, DELIBERATELY. Relieving a written-off balance is a "
		"posting with real tax consequences (forgiven debt is usually income), and "
		"a refinance moves a balance between two liability accounts. Both should be "
		"entries somebody wrote on purpose with a narrative of their own. The "
		"response spells out exactly which entry is still owed and against which "
		"account, so the omission is impossible to miss.\n\n"
		"REFUSES a note already closed, a disposition date before origination, a "
		"narrative too short to be an explanation, and — for 'Paid Off' — a note "
		"that still shows a balance outstanding. That last one is the useful "
		"refusal: it means either a final payment was never recorded (use "
		"record_loan_payment, which writes the entry that books it) or the balance "
		"carried here is stale. If it is stale, zero_remaining_balance=true writes "
		"it down and records the write-down as an Adjustment in the history.\n\n"
		"For a refinance, superseded_by names the note that replaced this one, so a "
		"reader following the chain forward lands on what is still owed.",
		{
			"note": _field(_STRING, "Note Payable docname, or the note_name it was created with."),
			"company": _COMPANY,
			"disposition": _field(
				_STRING,
				"How it ended: 'Paid Off', 'Refinanced' or 'Written Off'. ('Superseded' is "
				"set by this tool on the note a refinance replaced; it is not asked for.)",
			),
			"disposition_date": _field(_STRING, "When it ended, YYYY-MM-DD."),
			"narrative": _field(
				_STRING,
				"MANDATORY. What was paid, forgiven or rolled over, and what authorises it. "
				"The part of this record nobody can reconstruct later.",
			),
			"superseded_by": _field(
				_STRING,
				"For a Refinanced note: the Note Payable that replaced it. Create it first "
				"with create_note_payable.",
			),
			"zero_remaining_balance": _field(
				_BOOLEAN,
				"For 'Paid Off' only: write a stale outstanding balance down to zero without "
				"a payment, recording an Adjustment in the history. Default false, which "
				"refuses instead.",
			),
		},
		required=("note", "disposition", "disposition_date", "narrative"),
		mutating=True,
		destructive=True,
		title="Close a note payable",
		available=_needs_doctype("Note Payable"),
		requires="the Note Payable DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── real estate: parcels ────────────────────────────────────────────────
	"list_parcels": _tool(
		realestate.list_parcels,
		"One entity's land register: every parcel with its acreage, county, assessor "
		"parcel id, use type, appraised value and the Fixed Asset carrying it, plus "
		"totals for acreage and appraised value and an average per acre. Reports the "
		"oldest and newest appraisal dates, which is how you find out the valuation "
		"is four years stale. Read-only.\n\n"
		"APPRAISED VALUE IS NOT BOOK VALUE. What the balance sheet carries is the "
		"Asset's cost; this is market. They are meant to differ and nothing here "
		"reconciles them — link_parcel_to_asset reports the gap instead.",
		{
			"owning_entity": _field(
				_STRING,
				"The company whose register to read. `company` works as an alias. "
				"Omit on a single-company site.",
			),
			"company": _field(_STRING, "Alias for owning_entity."),
			"county": _field(_STRING, "Only parcels in this county."),
			"use_type": _field(
				_STRING,
				"Only this use: Orchard, Farmstead, Packing and Storage, Residential, "
				"Labor Housing, Bare Land, Mixed or Other.",
			),
			"title_holder": _field(_STRING, "Only parcels whose title is held by this Related Party."),
			"linked_to_asset": _field(
				_BOOLEAN,
				"true for only parcels linked to a Fixed Asset, false for only those not.",
			),
			"limit": _field(_INTEGER, "Maximum parcels returned. Default 100, hard maximum 500."),
		},
		title="List parcels",
		available=_needs_doctype("Parcel"),
		requires="the Parcel DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_parcel": _tool(
		realestate.get_parcel,
		"One parcel in full: its identity, acreage, appraisal, title holder, the "
		"Fixed Asset carrying it with the gap between cost and market spelled out, "
		"and every lease recorded over it in either direction. Read-only.",
		{
			"parcel": _field(
				_STRING,
				"The Parcel docname ('Red Camp - HLD') or just the parcel name ('Red Camp'). "
				"A name matching parcels in two entities is refused with both named.",
			),
			"owning_entity": _field(_STRING, "Narrow a bare parcel name to one entity."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("parcel",),
		title="Get a parcel",
		available=_needs_doctype("Parcel"),
		requires="the Parcel DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_parcel": _tool(
		realestate.create_parcel,
		"MUTATING (default OFF). Register one real estate parcel: name, county, "
		"assessor parcel id, acreage, address, use type, appraised value and the date "
		"it was appraised as of. Creates one Parcel and nothing else — no asset, no "
		"posting, no balance moves.\n\n"
		"THE DOCNAME IS '<parcel_name> - <entity abbr>', so two entities in one "
		"family may each have a 'Home Place'. WHAT IT REFUSES: a second parcel with "
		"the same name for the same entity; a second parcel claiming the same "
		"assessor parcel id (that number is the county's key, so two of them means a "
		"typo); negative acreage or value; a title_holder, appraisal_document or "
		"related_asset belonging to another company.\n\n"
		"WARNS, DOES NOT REFUSE, when a value arrives with no as-of date or no "
		"appraisal document behind it — a figure somebody remembered is still worth "
		"recording, it just should not be mistaken for a valuation.",
		{
			"owning_entity": _field(
				_STRING,
				"The company whose books track it. `company` is an alias. Where title sits "
				"with a trust or another LLC that is not a company here, put that in "
				"title_holder and leave this as the entity doing the tracking.",
			),
			"company": _field(_STRING, "Alias for owning_entity."),
			"parcel_name": _field(_STRING, "What it is called: 'Red Camp', 'Mill Creek'."),
			"parcel_id": _field(_STRING, "The county assessor's parcel number, exactly as printed."),
			"county": _field(_STRING, "County."),
			"state": _field(_STRING, "Two-letter state code. A county with no state is not an address."),
			"address": _field(_STRING, "Street address."),
			"acreage": _field(_NUMBER, "Deeded or GIS acreage. Say which in notes when they disagree."),
			"use_type": _field(
				_STRING,
				"Orchard, Farmstead, Packing and Storage, Residential, Labor Housing, "
				"Bare Land, Mixed or Other.",
			),
			"title_holder": _field(_STRING, "The Related Party holding title, when it is not the entity."),
			"appraised_value": _field(_NUMBER, "Fee simple market value from the latest appraisal."),
			"appraised_as_of": _field(_STRING, "The appraisal's effective date, YYYY-MM-DD."),
			"appraiser": _field(_STRING, "Who signed it, and their designation."),
			"appraisal_document": _field(
				_STRING, "The Governance Document holding the appraisal report."
			),
			"related_asset": _field(
				_STRING, "The Fixed Asset carrying it, if it is already on the balance sheet."
			),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("parcel_name",),
		mutating=True,
		title="Create a parcel",
		available=_needs_doctype("Parcel"),
		requires="the Parcel DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_parcel": _tool(
		realestate.update_parcel,
		"MUTATING (default OFF). Change a registered parcel: county, state, address, "
		"acreage, use type, assessor id, appraised value and date, appraiser, title "
		"holder, appraisal document, notes. Every change is echoed back as "
		"before → after.\n\n"
		"CANNOT re-key: parcel_name is refused because the docname is built from it "
		"and every lease and asset link points at that docname. CANNOT move a parcel "
		"between entities — a parcel changing hands is a conveyance, not an edit. "
		"CANNOT set related_asset; that is link_parcel_to_asset, which checks the "
		"asset is on the same books and that no other parcel claims it.",
		{
			"parcel": _field(_STRING, "The Parcel docname, or its parcel name."),
			"owning_entity": _field(_STRING, "Narrow a bare parcel name to one entity."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"parcel_id": _field(_STRING, "New assessor parcel number. Empty string clears it."),
			"county": _field(_STRING, "New county."),
			"state": _field(_STRING, "New state code."),
			"address": _field(_STRING, "New address."),
			"acreage": _field(_NUMBER, "New acreage."),
			"use_type": _field(_STRING, "New use type. Empty string clears it."),
			"appraised_value": _field(_NUMBER, "New appraised value."),
			"appraised_as_of": _field(_STRING, "New appraisal date, YYYY-MM-DD."),
			"appraiser": _field(_STRING, "New appraiser."),
			"title_holder": _field(_STRING, "New Related Party holding title. Empty string clears it."),
			"appraisal_document": _field(_STRING, "New Governance Document. Empty string clears it."),
			"notes": _field(_STRING, "New notes."),
		},
		required=("parcel",),
		mutating=True,
		title="Update a parcel",
		available=_needs_doctype("Parcel"),
		requires="the Parcel DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"link_parcel_to_asset": _tool(
		realestate.link_parcel_to_asset,
		"MUTATING (default OFF). Point a Parcel at the Fixed Asset that carries it on "
		"the balance sheet, and report the gap between what was paid and what it is "
		"worth. That gap is the point: unrealised appreciation is the number an "
		"estate conversation turns on and neither record shows it alone.\n\n"
		"Sets one field. No balance moves, no depreciation schedule changes, nothing "
		"is posted — land is not depreciated and this does not pretend otherwise.\n\n"
		"REFUSES: an asset that does not exist; an asset on another company's books; "
		"an asset already linked to a different parcel; a parcel already linked to "
		"something else, unless replace=true. `dry_run` validates and reports without "
		"writing.",
		{
			"parcel": _field(_STRING, "The Parcel docname, or its parcel name."),
			"asset": _field(_STRING, "The Asset docname."),
			"owning_entity": _field(_STRING, "Narrow a bare parcel name to one entity."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"replace": _field(
				_BOOLEAN,
				"Repoint a parcel that is already linked. Default false. Right after a "
				"re-capitalisation, wrong when the two assets are two parts of the parcel.",
			),
			"dry_run": _field(_BOOLEAN, "Validate and report without writing. Default false."),
		},
		required=("parcel", "asset"),
		mutating=True,
		idempotent=True,
		title="Link a parcel to an asset",
		available=_needs_doctype("Parcel"),
		requires="the Parcel DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── real estate: leases ─────────────────────────────────────────────────
	"list_leases": _tool(
		realestate.list_leases,
		"One entity's leases in BOTH directions, with the rent roll: annual rent "
		"receivable (leases where we are the lessor), annual rent payable (where we "
		"are the lessee), the net, and which leases run out inside the next 90 days. "
		"Read-only.\n\n"
		"RENT IS ANNUALISED FOR ACTIVE LEASES ONLY, from amount and frequency. A "
		"crop share and a one-time payment have no annual rate: they are listed "
		"under rent_not_annualisable rather than counted as zero, because a rent roll "
		"that treats an unknown as nothing understates the whole portfolio.\n\n"
		"NOTHING HERE EXPIRES A LEASE. A lease marked Active whose expiration date "
		"has passed is reported as such and left exactly as it was — farm ground "
		"routinely runs on month to month past its stated term, and a status that "
		"flipped itself on a calendar would erase the difference between 'still "
		"running' and 'nobody has looked at this in years'.",
		{
			"owning_entity": _field(_STRING, "Whose leases. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"status": _field(_STRING, "Active, Expired or Terminated."),
			"direction": _field(
				_STRING,
				"Outbound (we are the lessor, collecting rent) or Inbound (we are the "
				"lessee, paying it).",
			),
			"parcel": _field(_STRING, "Only leases over this parcel."),
			"counterparty": _field(_STRING, "Only leases with this Related Party on the other side."),
			"active_on": _field(
				_STRING,
				"Only leases in force on this date, by the dates on the record. YYYY-MM-DD.",
			),
			"expiring_within_days": _field(
				_INTEGER, "Window for expiring_soon. Default 90."
			),
			"limit": _field(_INTEGER, "Maximum leases returned. Default 100, hard maximum 500."),
		},
		title="List leases",
		available=_needs_doctype("Lease"),
		requires="the Lease DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_lease": _tool(
		realestate.get_lease,
		"One lease in full, with the parcel it covers, its attachments and whether it "
		"is in force today by the dates on the record. Read `direction` before "
		"reading `rent_amount`: Outbound means the owning entity collects it, Inbound "
		"means it pays it. Read-only.",
		{
			"lease": _field(_STRING, "The Lease docname, or just the lease name."),
			"owning_entity": _field(_STRING, "Narrow a bare lease name to one entity."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("lease",),
		title="Get a lease",
		available=_needs_doctype("Lease"),
		requires="the Lease DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_lease": _tool(
		realestate.create_lease,
		"MUTATING (default OFF). Record one lease, in whichever direction it runs — "
		"ground let out to an operator, or ground taken in from another party. "
		"Creates one Lease and BOOKS NOTHING: no journal entry, no receivable, no "
		"schedule. Recording an agreement and booking its consequences are separate "
		"acts and this is the first one.\n\n"
		"DIRECTION IS STATED, NOT GUESSED. Outbound means the owning entity is the "
		"lessor. The result carries a direction_check saying whether the party names "
		"agree with that claim — reported, never enforced, because a legal name "
		"('Highland Ltd Liability Co.') and a Company docname ('Highland LLC') "
		"routinely differ and a refusal built on string matching is one nobody could "
		"get past.\n\n"
		"REFUSES: a duplicate lease name for the same entity; the same party as both "
		"lessor and lessee; an expiration or termination date before the effective "
		"date; Terminated status with no termination date; negative rent; a parcel, "
		"counterparty or governance document belonging to another company.\n\n"
		"`file_content` is base64 of the executed lease, ceiling 8 MB — base64 in a "
		"JSON call is expensive, so a large scan is better uploaded in the Desk and "
		"recorded with `lease_document_url` instead. Uploaded files are stored "
		"PRIVATE.",
		{
			"owning_entity": _field(_STRING, "The company on our side of it. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"lease_name": _field(
				_STRING, "What it is called: 'Mill Creek Ground Lease 2025'. Name renewals for their term."
			),
			"direction": _field(_STRING, "Outbound (we are the lessor) or Inbound (we are the lessee)."),
			"lessor": _field(_STRING, "The party letting the ground out, by legal name."),
			"lessee": _field(_STRING, "The party taking it in, by legal name."),
			"effective_date": _field(_STRING, "When it starts, YYYY-MM-DD."),
			"expiration_date": _field(_STRING, "When the stated term ends. Omit for no fixed end."),
			"status": _field(_STRING, "Active (default), Expired or Terminated."),
			"termination_date": _field(_STRING, "When it was actually ended early. Required for Terminated."),
			"termination_reason": _field(_STRING, "Why."),
			"parcel": _field(_STRING, "The Parcel it covers, when it is one recorded parcel."),
			"counterparty": _field(_STRING, "The other side, as a Related Party, when they are one."),
			"rent_amount": _field(_NUMBER, "Rent per period. Omit for a crop share."),
			"rent_frequency": _field(
				_STRING,
				"Monthly, Quarterly, Semi-Annual, Annual (default), One-Time, Crop Share or Other.",
			),
			"rent_terms": _field(
				_STRING,
				"Escalators, crop-share percentages, who pays the water assessment — the part "
				"of a farm lease that is never a number.",
			),
			"governance_document": _field(_STRING, "The archive entry for the executed lease."),
			"lease_document_url": _field(
				_STRING, "Where the executed lease already lives. Not with file_content."
			),
			"file_content": _field(
				_STRING, "The executed lease, base64, no data: prefix. Ceiling 8 MB. Not with lease_document_url."
			),
			"file_name": _field(_STRING, "Filename to store it as. Required with file_content."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("lease_name", "direction", "lessor", "lessee", "effective_date"),
		mutating=True,
		title="Create a lease",
		available=_needs_doctype("Lease"),
		requires="the Lease DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_lease": _tool(
		realestate.update_lease,
		"MUTATING (default OFF). Change a recorded lease: status, expiration, "
		"termination date and reason, rent amount and frequency, terms, parties, "
		"parcel, counterparty, governance document, notes. Every change is echoed "
		"back as before → after. Books nothing.\n\n"
		"CANNOT re-key: lease_name is refused because the docname is built from it, "
		"and a renewed lease is a NEW lease with its own term. CANNOT move a lease "
		"between entities. REFUSES marking a lease Terminated without a "
		"termination_date in the same call, and refuses making one party both lessor "
		"and lessee.",
		{
			"lease": _field(_STRING, "The Lease docname, or its lease name."),
			"owning_entity": _field(_STRING, "Narrow a bare lease name to one entity."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"status": _field(_STRING, "Active, Expired or Terminated."),
			"expiration_date": _field(_STRING, "New end of the stated term, YYYY-MM-DD."),
			"termination_date": _field(_STRING, "When it was ended early. Required to mark it Terminated."),
			"termination_reason": _field(_STRING, "Why."),
			"rent_amount": _field(_NUMBER, "New rent per period."),
			"rent_frequency": _field(_STRING, "New frequency."),
			"rent_terms": _field(_STRING, "New terms."),
			"lessor": _field(_STRING, "New lessor."),
			"lessee": _field(_STRING, "New lessee."),
			"parcel": _field(_STRING, "New parcel. Empty string clears it."),
			"counterparty": _field(_STRING, "New Related Party counterparty. Empty string clears it."),
			"governance_document": _field(_STRING, "New archive entry. Empty string clears it."),
			"notes": _field(_STRING, "New notes."),
		},
		required=("lease",),
		mutating=True,
		title="Update a lease",
		available=_needs_doctype("Lease"),
		requires="the Lease DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── related parties ─────────────────────────────────────────────────────
	"list_related_parties": _tool(
		parties.list_related_parties,
		"One company's related-party register: who is related to it, in what "
		"capacity, from when, under which document, and which of them are linked to a "
		"Supplier or a Cap Table Entry. Reports the relationships with no governing "
		"document behind them, which is the first thing an examiner asks for. "
		"Read-only.\n\n"
		"ONE PERSON MAY APPEAR MORE THAN ONCE. A Manager who is also a Member is two "
		"entries, under two instruments, from two dates — `count` counts "
		"relationships and `distinct_people` counts names. Ended relationships are "
		"listed by default: the transactions they explain are still in the ledger.\n\n"
		"This is GOVERNANCE, not accounting. It does not replace or shadow the Party "
		"field on a Journal Entry, which stays Supplier / Customer / Employee.",
		{
			"company": _field(_STRING, "Whose register. Omit on a single-company site."),
			"party_type": _field(
				_STRING,
				"Individual, Trust, LLC, Corporation, Partnership, Family Member or Other.",
			),
			"relationship_to_company": _field(
				_STRING,
				"Member, Manager, Trustee, Beneficiary, Family, Vendor, Officer, Director or Other.",
			),
			"supplier": _field(_STRING, "Only the entry linked to this Supplier."),
			"current_only": _field(
				_BOOLEAN, "Only relationships that have not ended. Default false — ended ones are shown."
			),
			"limit": _field(_INTEGER, "Maximum entries returned. Default 100, hard maximum 500."),
		},
		title="List related parties",
		available=_needs_doctype("Related Party"),
		requires="the Related Party DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_related_party": _tool(
		parties.get_related_party,
		"One relationship in full, with everything on this site pointing at it: the "
		"person's other roles at the same company, their Cap Table Entry, their "
		"Supplier record, the parcels they hold title to and the leases they are "
		"counterparty on. Read-only.\n\n"
		"NEVER RETURNS MORE THAN FOUR DIGITS of a taxpayer id, including from a "
		"linked Supplier — `supplier_detail.tax_id` says only whether one is on file.",
		{
			"party": _field(
				_STRING,
				"The Related Party docname ('Tim Polehn - Manager - OML') or just the name. "
				"A bare name held in two capacities is refused with both docnames listed.",
			),
			"company": _field(_STRING, "Narrow a bare name to one company."),
		},
		required=("party",),
		title="Get a related party",
		available=_needs_doctype("Related Party"),
		requires="the Related Party DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_related_party": _tool(
		parties.create_related_party,
		"MUTATING (default OFF). Register one relationship: who, what kind of entity "
		"they are, in what capacity, from when, and which document establishes it. "
		"Creates one Related Party and nothing else.\n\n"
		"THE DOCNAME IS '<name> - <relationship> - <company abbr>', because somebody "
		"who is both Manager and Member of an LLC is two entries under two "
		"instruments. Registering the same name and role twice is refused; "
		"registering a second role is expected.\n\n"
		"FOUR DIGITS, NEVER NINE. `tax_id_last4` takes exactly four digits and "
		"refuses nine, naming what it thinks it was sent. The full SSN or EIN belongs "
		"on the signed W-9, on paper — the difference between a site holding four "
		"digits and a site holding nine is the difference between an inconvenience "
		"and a notifiable breach.\n\n"
		"ALSO REFUSES: an end_date before the effective_date; a tax id with no type "
		"or a type with no digits; a cap_table_entry or governing_document belonging "
		"to another company.",
		{
			"company": _field(_STRING, "Which company they are related to."),
			"party_name": _field(
				_STRING, "Their legal name, as it appears on the document establishing the relationship."
			),
			"party_type": _field(
				_STRING,
				"What they ARE: Individual, Trust, LLC, Corporation, Partnership, Family "
				"Member or Other.",
			),
			"relationship_to_company": _field(
				_STRING,
				"What they DO here: Member, Manager, Trustee, Beneficiary, Family, Vendor, "
				"Officer, Director or Other.",
			),
			"effective_date": _field(_STRING, "When the relationship started, YYYY-MM-DD."),
			"end_date": _field(_STRING, "When it ended. Omit for current."),
			"tax_id_type": _field(_STRING, "None (default), SSN or EIN."),
			"tax_id_last4": _field(
				_STRING, "The LAST FOUR DIGITS ONLY. Nine digits is refused, not truncated."
			),
			"address": _field(_STRING, "Mailing address, as it should print on a 1099."),
			"cap_table_entry": _field(_STRING, "Their row in the member register, if they hold an interest."),
			"supplier": _field(
				_STRING,
				"Their Supplier record, if they are also paid. This is what makes "
				"generate_1099_prefill flag the payment as a related-party transaction.",
			),
			"governing_document": _field(
				_STRING, "The operating agreement, trust instrument or resolution that establishes it."
			),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("party_name", "party_type", "relationship_to_company", "effective_date"),
		mutating=True,
		title="Create a related party",
		available=_needs_doctype("Related Party"),
		requires="the Related Party DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_related_party": _tool(
		parties.update_related_party,
		"MUTATING (default OFF). Change a registered relationship: party type, "
		"effective and end dates, tax id type and last four, address, and the links "
		"to a Cap Table Entry, a Supplier and a governing document.\n\n"
		"CANNOT re-key. party_name, relationship_to_company and company are the key "
		"and the docname is built from them — a change of role is a NEW relationship, "
		"so register it and set an end_date on this one. An entry is never deleted "
		"when a relationship ends: the transactions it explains are still in the "
		"ledger, and a prior year's disclosure schedule still needs to know who was "
		"who at the time.",
		{
			"party": _field(_STRING, "The Related Party docname, or the name if it is unambiguous."),
			"company": _field(_STRING, "Narrow a bare name to one company."),
			"party_type": _field(_STRING, "New party type."),
			"effective_date": _field(_STRING, "New start date, YYYY-MM-DD."),
			"end_date": _field(_STRING, "When it ended. Empty string clears it."),
			"tax_id_type": _field(_STRING, "None, SSN or EIN."),
			"tax_id_last4": _field(_STRING, "The last four digits only."),
			"address": _field(_STRING, "New address."),
			"cap_table_entry": _field(_STRING, "New Cap Table Entry. Empty string clears it."),
			"supplier": _field(_STRING, "New Supplier. Empty string clears it."),
			"governing_document": _field(_STRING, "New governing document. Empty string clears it."),
			"notes": _field(_STRING, "New notes."),
		},
		required=("party",),
		mutating=True,
		title="Update a related party",
		available=_needs_doctype("Related Party"),
		requires="the Related Party DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── generated documents ─────────────────────────────────────────────────
	"generate_quarterly_investment_report": _tool(
		investment_report.generate_quarterly_investment_report,
		"MUTATING (default OFF). Build the quarter's investment report as a PDF and "
		"file it in the governance archive as a Prior Statement with the PDF "
		"attached. Covers assets under management, ledger activity, the manager and "
		"custody fee accrual, performance against a benchmark with a high-water mark, "
		"the cash clearing balance, and the reconciliation state it was produced "
		"under.\n\n"
		"IT REFUSES A QUARTER THAT IS NOT CLOSED, and names everything that is "
		"missing in one reply rather than one per call. Four things must be true: the "
		"quarter has ended; the custodian's statement is filed as a Prior Statement "
		"governance document with an effective date inside it; no journal entry "
		"touching the investment accounts is still a draft; no bank transaction in "
		"the period is unreconciled. A report generated on a calendar date regardless "
		"of state is a report whose numbers may be wrong, signed by somebody who "
		"assumed the schedule meant something.\n\n"
		"IT INVENTS NOTHING. Without benchmark_rate_percent the return-over-benchmark "
		"and the performance fee are NOT computed and say so — they are not zero and "
		"not estimated, because a performance fee against an assumed benchmark of "
		"nothing overstates what the manager is owed. Same for the high-water mark and "
		"net contributions.\n\n"
		"HOLDINGS COME FROM THE CALLER. This app reads one ERPNext site; the "
		"custodian's positions are not on it. Pass `holdings` and the report "
		"reconciles the snapshot against the ledger and reports the variance; omit it "
		"and assets under management are the ledger balance, stated as such.\n\n"
		"PDF IS THE DEFAULT AND THE RIGHT ANSWER. `output_format='docx'` exists for a "
		"report that has to be edited before signing; a .docx is a file the recipient "
		"may not be able to open. `dry_run=true` runs every precondition and computes "
		"every figure without writing anything.",
		{
			"company": _field(_STRING, "The client company. Omit on a single-company site."),
			"quarter": _field(_STRING, "The quarter, as '2026-Q2'."),
			"output_format": _field(_STRING, "'pdf' (default) or 'docx'."),
			"output_path": _field(
				_STRING,
				"Also write the document here. Relative paths land under the site's "
				"private/files; anything resolving outside the site's file storage is refused. "
				"Omit it — the attachment is the durable copy.",
			),
			"overwrite": _field(_BOOLEAN, "Replace an existing file at output_path. Default false."),
			"investment_accounts": _field(
				_STRING_ARRAY,
				"The accounts holding the portfolio. Omit to match them by name on this "
				"company's chart — the result lists exactly which were included.",
			),
			"cash_clearing_account": _field(
				_STRING, "The clearing account. Omit to match it by name."
			),
			"holdings": _field(
				{"type": "array", "items": _OBJECT},
				"The custodian's positions at quarter end: objects with symbol, description, "
				"quantity, price, market_value and cost_basis. Omit and the ledger is the only "
				"source.",
			),
			"benchmark_rate_percent": _field(
				_NUMBER,
				"The benchmark's ANNUAL rate, e.g. 4.25 for a 10-year Treasury at 4.25%. "
				"Without it, no performance figure against benchmark is produced.",
			),
			"manager_fee_percent": _field(_NUMBER, "The manager's annual rate. Default 1.00."),
			"custody_fee_percent": _field(_NUMBER, "The custodian's annual rate. Default 1.00."),
			"performance_fee_percent": _field(
				_NUMBER, "Share of the gain over benchmark. Default 20."
			),
			"high_water_mark": _field(
				_NUMBER,
				"The high-water mark. Closing assets at or below it earn no performance fee "
				"however the quarter went.",
			),
			"net_contributions": _field(
				_NUMBER,
				"Money in minus money out during the quarter. Without it the return is "
				"computed as if none, which is right only if none moved.",
			),
			"title": _field(_STRING, "Override the archive entry's title. Use it to re-run a quarter."),
			"dry_run": _field(
				_BOOLEAN, "Check every precondition and compute every figure without writing. Default false."
			),
		},
		required=("quarter",),
		mutating=True,
		title="Generate the quarterly investment report",
		available=_needs_doctype("Governance Document"),
		requires="the Governance Document DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"generate_1099_prefill": _tool(
		tax.generate_1099_prefill,
		"MUTATING (default OFF). Aggregate a calendar year of supplier payments into "
		"a 1099-NEC worksheet (xlsx) and a per-recipient form (PDF, Copies A, B and "
		"C), filed together in the governance archive as a Tax Filing.\n\n"
		"IT IS CALLED A PRE-FILL AND IT MEANS IT. Recipient taxpayer ids print as "
		"XXX-XX-nnnn because this site holds four digits on purpose — complete them "
		"from the signed W-9 before filing. Copy A must be the official scannable "
		"red-ink form or an electronic filing; the Copy A page here is stamped as an "
		"information copy. Copies B and C print on plain paper and are the ones that "
		"go out.\n\n"
		"CLASSIFICATION IS NEVER SILENT. Every recipient comes back reportable, "
		"exempt or BORDERLINE with the reason in a sentence. An LLC is borderline "
		"because a disregarded entity is reportable and one taxed as a corporation is "
		"not, and only the W-9 says which. A law firm is borderline because attorneys "
		"are reportable EVEN WHEN INCORPORATED — which is why 'ends in PC, skip it' "
		"is the wrong rule.\n\n"
		"WHERE THE MONEY COMES FROM: GL Entry rows carrying a Supplier party — so "
		"every voucher type, and only submitted ones. Debits only on Payable-type "
		"accounts (a debit to payables is a bill being paid, a credit is one being "
		"raised); debits minus credits everywhere else (the party sits on the expense "
		"line, so a credit is a refund). `by_account` shows both sides so the "
		"arithmetic can be checked rather than believed.\n\n"
		"EXCLUDED AND SAID SO: employees, because that is W-2 territory — the count "
		"and total of employee-party postings is reported anyway, so 'nobody looked' "
		"and 'somebody looked and excluded them' are different-looking answers. "
		"Opening entries. Anything under the threshold, listed with its total so a "
		"case near the line is visible rather than absent.\n\n"
		"REFUSES a tax year that has not ended. `dry_run=true` produces every figure "
		"and classification without writing anything, which is the right first call.",
		{
			"company": _field(_STRING, "The payer. Omit on a single-company site."),
			"tax_year": _field(_INTEGER, "The calendar year, e.g. 2025. Must have ended."),
			"threshold": _field(
				_NUMBER, "Reporting floor. Default 600 — pass the floor for the year being prepared."
			),
			"output_path": _field(
				_STRING,
				"A DIRECTORY to also write the workbook and forms into. Relative paths land "
				"under the site's private/files; anything resolving outside the site's file "
				"storage is refused. Omit it — the attachments are the durable copies.",
			),
			"overwrite": _field(_BOOLEAN, "Replace existing files at output_path. Default false."),
			"payer_address": _field(
				_STRING, "The payer's address as it should print on the forms."
			),
			"include_forms": _field(
				_BOOLEAN, "Produce the per-recipient PDFs. Default true; false gives the workbook alone."
			),
			"title": _field(_STRING, "Override the archive entry's title. Use it to re-run a year."),
			"dry_run": _field(
				_BOOLEAN, "Compute every figure and classification without writing. Default false."
			),
		},
		required=("tax_year",),
		mutating=True,
		title="Generate a 1099-NEC pre-fill",
		available=_needs_doctype("Governance Document"),
		requires="the Governance Document DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
}

#: Tool names in catalogue order, read tools first. Used by the settings doctype
#: generator and the docs to stay in step with this file.
READ_TOOLS = tuple(name for name, spec in TOOLS.items() if not spec["mutating"])
MUTATING_TOOLS = tuple(name for name, spec in TOOLS.items() if spec["mutating"])


def is_available(tool_name: str) -> bool:
	"""Whether this site could run the tool at all, switches aside.

	Never raises: a predicate that blows up is treated as "no", because an
	availability check that errors is not evidence the tool would have worked.
	"""
	spec = TOOLS.get(tool_name)
	if spec is None:
		return False
	try:
		return bool(spec["available"]())
	except Exception:
		return False


def tools_list() -> dict:
	"""The `tools/list` payload: tools that are switched on and could run here.

	A tool an operator has disabled is not advertised at all, rather than
	advertised and then refused — a model cannot be tempted by a tool it cannot
	see. Same for a tool whose site prerequisite is missing: on a site without
	Frappe HR, `get_leave_balance` is not a tool that fails, it is a tool that
	does not exist, and saying so keeps the catalogue an honest description of
	what will work.
	"""
	return {
		"tools": [
			{
				"name": name,
				"description": spec["description"],
				"inputSchema": spec["inputSchema"],
				"annotations": spec["annotations"],
			}
			for name, spec in TOOLS.items()
			if settings.tool_enabled(name) and is_available(name)
		]
	}


def dispatch(tool_name: str, arguments: dict, caller_ip: str = "") -> dict:
	"""Run one tool and return an MCP `tools/call` result. Never raises.

	Order matters: unknown name, then site availability, then switch, then run. A
	tool that cannot run here is never handed its arguments, so an argument that
	would have been rejected by the handler cannot leak the fact that the tool
	exists at all.

	Availability comes before the switch because it is the more actionable
	answer: "this site has no Frappe HR" tells a caller to stop, while "your
	operator disabled it" tells them to go and ask. Reporting the second when the
	first is also true sends somebody to have a pointless conversation.
	"""
	arguments = arguments if isinstance(arguments, dict) else {}
	spec = TOOLS.get(tool_name)

	if spec is None:
		audit.record(tool_name, arguments, audit.STATUS_ERROR, "unknown tool", caller_ip=caller_ip)
		return error_result(f"unknown tool {tool_name!r}")

	if not is_available(tool_name):
		audit.record(
			tool_name,
			arguments,
			audit.STATUS_BLOCKED,
			f"unavailable: requires {spec['requires'] or 'an unmet site prerequisite'}",
			caller_ip=caller_ip,
		)
		return error_result(
			f"the tool {tool_name!r} is not available on this site: it requires "
			f"{spec['requires'] or 'a component this site does not have'}. This is "
			"not something an operator can switch on here."
		)

	if not settings.tool_enabled(tool_name):
		kind = "mutating" if spec["mutating"] else "read"
		audit.record(
			tool_name,
			arguments,
			audit.STATUS_BLOCKED,
			f"blocked: allow_{tool_name} is off",
			caller_ip=caller_ip,
		)
		return error_result(
			f"the {kind} tool {tool_name!r} is switched off on this site. An "
			f"operator must tick 'allow_{tool_name}' in ERPNext MCP Settings "
			"to enable it."
		)

	try:
		result = spec["handler"](arguments)
		if not isinstance(result, ToolResult):  # pragma: no cover - contract guard
			result = ToolResult(data=result or {}, summary="")
		log_name = audit.record(
			tool_name,
			arguments,
			audit.STATUS_SUCCESS,
			result.summary,
			docstatus_delta=result.docstatus_delta,
			caller_ip=caller_ip,
		)
		return ok_result(_stamp_action_log_id(result.data, log_name))

	except ToolError as exc:
		# Expected failure. Roll back first so a half-built document cannot be
		# committed by the framework at the end of the request, then log into
		# the clean transaction and commit that.
		frappe.db.rollback()
		audit.record(
			tool_name,
			arguments,
			audit.STATUS_ERROR,
			str(exc),
			caller_ip=caller_ip,
			commit=True,
		)
		return error_result(str(exc))

	except Exception as exc:
		# A bug here, or a Frappe validation this app did not anticipate (a
		# closed period, a frozen account, a mandatory dimension). The client
		# gets the type and message so it can adapt; the traceback goes to the
		# site's Error Log where an operator can actually use it.
		frappe.db.rollback()
		summary = f"{type(exc).__name__}: {exc}"
		audit.record(
			tool_name,
			arguments,
			audit.STATUS_ERROR,
			summary,
			caller_ip=caller_ip,
			commit=True,
		)
		try:
			frappe.log_error(
				title=f"erpnext_mcp: tool {tool_name} failed",
				message=traceback_text(),
			)
		except Exception:
			pass
		return error_result(summary)


def _stamp_action_log_id(data, log_name):
	"""Fill in a result's `mcp_action_log_id`, now that the audit row exists.

	Compliance packets carry provenance, and the most useful piece of it is the
	MCP Action Log row for the call that produced them — which cannot be known
	inside the handler, because the row is written after it returns. A packet
	ships the key set to None and this fills it, so the payload shape does not
	depend on whether the audit write succeeded.

	Deliberately narrow: only a key that is already present and still None is
	touched. A tool that returns real data under that name is not overwritten.
	"""
	if isinstance(data, dict) and data.get("mcp_action_log_id", "absent") is None:
		data["mcp_action_log_id"] = log_name
	return data


def ok_result(data) -> dict:
	return {
		"content": [{"type": "text", "text": json.dumps(data, default=str, indent=2)}],
		"isError": False,
	}


def error_result(message: str) -> dict:
	return {"content": [{"type": "text", "text": message}], "isError": True}
