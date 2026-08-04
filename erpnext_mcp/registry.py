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

from . import audit, geo, settings
from .compat import doctype_exists, traceback_text
from .errors import ToolError
from .render import qr
from .result import ToolResult
from .tools import (
	accounts,
	assets,
	auditpacket,
	banking,
	calendar,
	collab,
	company,
	compliance,
	dimensions,
	dispatch,
	employee,
	evidence,
	farm,
	fieldwork,
	files,
	fiscal,
	funnel,
	governance,
	heat,
	housing,
	hr,
	inspections,
	investment_report,
	kpi,
	meta,
	mobile,
	mutate,
	newhire,
	notes,
	opening,
	packets,
	parties,
	printing,
	read,
	realestate,
	reports,
	shifts,
	tax,
	trade,
	training,
	uploads,
	visits,
	weather,
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


def _qr_available() -> bool:
	"""Predicate: can this bench draw a QR code at all?

	v0.17.0. Same shape as the geospatial predicates and for the same reason: a
	bench without `segno` or `qrcode` loses ONE tool by name, with the pip command
	to fix it, rather than losing the other two hundred and five. Everything else
	in the mobile login flow works without it — `generate_api_token` returns the
	same credential as text, and the QR only saves somebody typing it.
	"""
	try:
		return qr.available()
	except Exception:  # pragma: no cover - an encoder that explodes on import
		return False


#: What a geospatial tool needs beyond a DocType: the two libraries that do the
#: geometry. They are declared dependencies, so a normal install has them — but
#: this app's promise is that installing it cannot break a site, so a bench
#: missing them loses these five tools by name rather than failing to load the
#: other hundred and sixteen.
_GEO_REQUIRES = (
	"the Field DocType (run `bench migrate`) and the shapely and h3 Python packages, "
	"which this app declares as dependencies — install them into the bench's environment "
	"with `./env/bin/pip install 'shapely>=2.0' 'h3>=4.0.0'` and restart"
)


def _geo_ready(*doctypes: str):
	"""Predicate: this site has the doctype AND can do geometry."""
	needs_doctype = _needs_doctype(*doctypes)

	def predicate() -> bool:
		try:
			return bool(needs_doctype() and geo.available())
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
	"investigate_je_gl_link": _tool(
		read.investigate_je_gl_link,
		"DIAGNOSTIC. Every line of one Journal Entry beside every GL Entry row it "
		"posted, with the join between them spelled out. Read-only.\n\n"
		"WHAT IT ANSWERS. 'The voucher says one thing and the ageing report says "
		"another — which row is which, and why did the tool meant to keep them in "
		"step match nothing?' Per line: account, account_type, root_type, debit, "
		"credit, party_type, party, `reference_detail_no`, and the GL rows matched "
		"to it with their own party, voucher_type, posting date and "
		"`voucher_detail_no`. Flags every line whose party disagrees with the "
		"ledger, every line that matched no row, and every GL row no single line "
		"explains. `summary` counts all of it; `finding` says in one paragraph what "
		"the counts mean.\n\n"
		"THE THING IT MOST OFTEN EXPLAINS. ERPNext does NOT put the account line's "
		"docname in `GL Entry.voucher_detail_no` for a Journal Entry — it fills that "
		"column from the line's `reference_detail_no`, which names a payment "
		"schedule row on an invoice being settled and is empty on an ordinary line. "
		"That is Sales Invoice Item's convention, not Journal Entry's, and it is "
		"true of every account type. `voucher_detail_no_populated` reports the "
		"field's real state on this voucher so the explanation can be checked.\n\n"
		"Works on drafts (no GL rows exist yet) and on cancelled entries (only the "
		"reversal remains), and says which case it is looking at.",
		{
			"journal_entry": _field(
				_STRING,
				"Journal Entry docname, e.g. 'ACC-JV-2026-00073'.",
			)
		},
		required=("journal_entry",),
		title="Investigate JE/GL link",
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
	"update_journal_entry_party": _tool(
		mutate.update_journal_entry_party,
		"MUTATING (default OFF). Set or change `party_type` and `party` on ONE line "
		"of a Journal Entry — including a SUBMITTED one — and record why.\n\n"
		"THE CASE IT IS FOR. A payment leaves a shared account and only afterwards "
		"does anybody establish which of two sons it was for. The posting is right: "
		"right account, right amount, right date, and one attribution column empty "
		"or wrong. The alternatives are cancel-and-repost, which replaces a clerical "
		"correction with a cancelled voucher, a reversing pair and a new number no "
		"statement reconciles against — or the Desk, which is the thing an MCP "
		"server exists so nobody has to open.\n\n"
		"IT CANNOT MOVE A BALANCE. Account, debit, credit, date, cost center and "
		"remark are not arguments to it. The trial balance after the call is "
		"arithmetically identical to the one before, which is what makes editing a "
		"submitted document defensible: this is attribution, not restatement. No "
		"journal entry is written and nothing is reversed.\n\n"
		"IT WRITES IN BOTH PLACES THE PARTY LIVES. `tabJournal Entry Account` is "
		"what the voucher shows; `tabGL Entry` is what every ageing report, party "
		"ledger and statement of account reads. Updating one and not the other "
		"leaves the voucher and the reports disagreeing with nothing to say which "
		"is right, so a line whose GL rows cannot be identified WITH CERTAINTY is "
		"refused before anything is written. A DRAFT is saved through the document "
		"instead, since it has written no GL Entries and full validation can still "
		"run.\n\n"
		"HOW THE GL ROWS ARE FOUND, AND WHY IT CHANGED IN v0.14.0. ERPNext does "
		"NOT put the account line's docname in `GL Entry.voucher_detail_no` for a "
		"Journal Entry — it fills that column from the line's `reference_detail_no`, "
		"which is empty on an ordinary line. v0.13.0 matched on it and therefore "
		"matched NOTHING on every submitted entry, updating the voucher alone. Rows "
		"are now matched on account plus debit plus credit, with "
		"`voucher_detail_no` preferred where a site does carry it. If v0.13.0 was "
		"used against a submitted entry, the ledger still says what it said before: "
		"investigate_je_gl_link shows which entries are in that state.\n\n"
		"REFUSES: a cancelled entry (evidence with a hole in it); a line index "
		"outside the entry; the rounding or write-off line ERPNext wrote itself; a "
		"bank or cash line, where a party would make an ageing report claim somebody "
		"owes the account balance; a party type this site has not registered; a "
		"party that is not a record in the register its type names; a change that "
		"changes nothing; and a submitted line whose GL rows are ambiguous (two "
		"lines that look alike), merged (ERPNext combines lines sharing an account, "
		"party and cost center into one summed row) or missing. An account whose "
		"type does not normally carry a party is refused unless "
		"`allow_non_party_account=true` says it was meant, and an unidentifiable GL "
		"unless `allow_unmatched_gl=true` accepts a voucher and a ledger that "
		"disagree — both then go through with a warning. `dry_run` reports the whole "
		"plan, including which GL rows would move, without writing.",
		{
			"journal_entry": _field(_STRING, "Journal Entry docname, e.g. 'ACC-JV-2026-00042'."),
			"line_index": _field(
				_INTEGER,
				"Which line to attribute, counting from 1 the way ERPNext numbers them. "
				"get_journal_entry lists them with their accounts and amounts.",
			),
			"party_type": _field(
				_STRING,
				"Supplier, Customer, Employee, Shareholder, Family, Contact — any Party Type "
				"registered on this site. Empty string clears the attribution, and then "
				"`party` must be empty too.",
			),
			"party": _field(
				_STRING,
				"The party's docname in the register its type names, e.g. a Family member's "
				"name. Empty string clears the attribution.",
			),
			"reason": _field(
				_STRING,
				"Why the attribution is being changed — what establishes that this line "
				"belongs to this party. Written to the entry's comment thread and to the "
				"audit log. Mandatory.",
			),
			"allow_non_party_account": _field(
				_BOOLEAN,
				"Go ahead on an account whose type does not normally carry a party (an "
				"expense attributed to the person it was incurred for, say). Default false, "
				"which refuses. Never opens a bank, cash or round-off line.",
			),
			"allow_unmatched_gl": _field(
				_BOOLEAN,
				"Update the voucher even though this line's GL Entry rows cannot be "
				"identified — ambiguous, merged or absent. Default false, which refuses. "
				"Passing true accepts a voucher and a general ledger that say different "
				"things about who this line belongs to, and the result carries that as a "
				"warning. Read investigate_je_gl_link first.",
			),
			"force_gl_sync": _field(
				_BOOLEAN,
				"Write the GL Entry rows even where nothing disagrees with the voucher. Default "
				"false. The GL is already written whenever it has DRIFTED from the voucher — this "
				"only makes the write an explicit act rather than a consequence of a comparison, "
				"which is what repair_drifted_je_attributions passes.",
			),
			"dry_run": _field(_BOOLEAN, "Report the change and the GL rows without writing. Default false."),
		},
		required=("journal_entry", "line_index", "party_type", "party", "reason"),
		mutating=True,
		title="Update a journal entry line's party",
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
						"narrative": _field(
							_STRING, "Optional per-line remark. The event's own explanation is user_remark."
						),
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
	"bulk_wire_default_accounts": _tool(
		dimensions.bulk_wire_default_accounts,
		"MUTATING (default OFF). Wire a Company's default accounts by FINDING "
		"them, rather than by being told their docnames. The setup call to make "
		"after create_company.\n\n"
		"THE GAP IT FILLS. set_company_defaults only sets fields the caller "
		"already knows the accounts for. Run against four freshly-created "
		"companies it comes back 'idempotent' for the four create_company already "
		"did and says nothing about cash, bank, income or expense — because nobody "
		"passed them. A company with no default_income_account does not fail "
		"loudly; it fails weeks later, the first time somebody saves an invoice "
		"line with no account on it.\n\n"
		"WHERE IT LOOKS, IN ORDER. Your `overrides` first; then the well-known "
		"account number for the chart template (1310 receivable, 2110 payable, "
		"1140 cash, 1110 bank — descending into the sub-ledger when the number "
		"names a group, 4100 income, 5100 expense, 5212 round off, 5218 write "
		"off); then an account whose `account_type` means the right thing; then, "
		"only where the field permits an untyped account, the first leaf of the "
		"right root type. Every candidate has to pass the same type checks "
		"set_company_defaults applies to a hand-written value — so it proposes and "
		"those rules dispose.\n\n"
		"IT NEVER FILLS A FIELD WITH SOMETHING MERELY PLAUSIBLE. A field nothing "
		"matched is reported in `unresolved` with what was looked for and how to "
		"fix it, and the rest are still wired — a company with nine of ten "
		"defaults set is better off than one with none, and a chart with no Cost "
		"of Goods Sold account is ordinary rather than broken. `strict=true` "
		"refuses the whole call instead. An `overrides` value that cannot be "
		"resolved is ALWAYS a hard refusal, strict or not.\n\n"
		"Idempotent — a re-run reports everything unchanged and saves nothing. "
		"`dry_run` reports every pick, and what each was picked by, without "
		"writing. Changes no existing posting.",
		{
			"company": _COMPANY,
			"strategy": _field(
				_STRING,
				"How to search: 'standard_with_numbers' (default) tries ERPNext's own "
				"account numbers before matching on account type; 'account_type' skips "
				"the numbers, which is what a hand-written chart needs; 'auto' reads the "
				"company's own chart_of_accounts template and picks between the two.",
			),
			"overrides": _field(
				_OBJECT,
				"Company field → account, pinning a specific answer regardless of what "
				'the search would have found, e.g. {"default_bank_account": "1112"}. '
				"Accepts docname, account number or account name. Fields: "
				+ ", ".join(dimensions.WIRED_COMPANY_DEFAULTS)
				+ ".",
			),
			"strict": _field(
				_BOOLEAN,
				"Refuse the whole call if any field cannot be resolved, instead of wiring "
				"the rest and reporting the gaps. Default false.",
			),
			"dry_run": _field(
				_BOOLEAN,
				"Report every pick and what it was picked by, without writing. Default false.",
			),
		},
		required=("company",),
		mutating=True,
		idempotent=True,
		title="Wire company default accounts",
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
	# ── printing ────────────────────────────────────────────────────────────
	"create_check_print_format": _tool(
		printing.create_check_print_format,
		"MUTATING (default OFF). Create or update the Print Format that cuts a "
		"printed check out of a Payment Entry, for one Company.\n\n"
		"WHAT IT PRODUCES. A Jinja Print Format on Payment Entry — ERPNext's own "
		"check-cutting document — laid out for US laser check stock: three "
		"3.5-inch panels on Letter, check on top, remittance stub in the middle, "
		"remittance stub at the bottom (Deluxe form 1000/9000 and the Costco and "
		"Intuit equivalents). The check panel carries date, payee, the amount in "
		"figures in a box, the amount in words in US convention ('One Thousand Two "
		"Hundred Thirty-Four and 56/100' — no currency word, because the stock "
		"says DOLLARS), the memo and a signature line. Both stubs carry the "
		"invoice-by-invoice detail from the payment's references.\n\n"
		"MICR IS NOT RENDERED. The routing and account numbers along the bottom "
		"are printed in magnetic ink on the stock you buy, against your account. "
		"Nothing here writes them, and nothing should.\n\n"
		"IT IS A CUSTOM FORMAT, so `bench migrate` never overwrites it and a "
		"margin tuned for your printer survives. Idempotent: a re-run with the "
		"same arguments reports `unchanged` and writes nothing. Refuses a format "
		"name already used by a STANDARD (app-shipped) format, whose contents "
		"would be silently replaced at the next upgrade, and one already pointed "
		"at a different doctype. `dry_run` reports the whole plan without writing.",
		{
			"company": _COMPANY,
			"format_name": _field(
				_STRING,
				"What to call it. Defaults to '<Company Abbr> Check — Middle Voucher'. "
				"ERPNext does not scope Print Formats by company, so the name is what "
				"tells two companies' formats apart.",
			),
			"payee_field": _field(
				_STRING,
				"Which Payment Entry field the payee line prints. Default 'party_name' — "
				"the supplier's full name where ERPNext has one; the template falls back "
				"to `party` when it is empty. Refused if this site's Payment Entry has no "
				"such field, rather than printing a blank payee.",
			),
			"signature_image_url": _field(
				_STRING,
				"Optional. A file_url on this site for a signature image printed above "
				"the signature line. Leave empty to print the line and sign by hand.",
			),
			"dry_run": _field(
				_BOOLEAN, "Report what would be created or updated without writing. Default false."
			),
		},
		required=("company",),
		mutating=True,
		idempotent=True,
		available=_needs_doctype("Payment Entry"),
		requires="ERPNext's Payment Entry doctype, which is what a printed check is cut from",
		title="Create check print format",
	),
	# ── streaming uploads ───────────────────────────────────────────────────
	"stage_file_chunk": _tool(
		uploads.stage_file_chunk,
		"MUTATING (default OFF). Stage ONE piece of a file for a later commit. "
		"This is how you move a file bigger than a tool call onto this site.\n\n"
		"WHY IT EXISTS. attach_file_to_document takes up to 8 MB of base64 in one "
		"call, and no caller can reach that: the base64 string has to be COMPOSED "
		"inside the tool call, which runs out at a couple of hundred kilobytes. So "
		"a 5 MB PDF meant writing a script and running it on the box by hand. Now "
		"it means calling this N times and commit_staged_file once.\n\n"
		"CUT THE BYTES, THEN ENCODE. Each `chunk_base64` must be the base64 of ITS "
		"OWN slice of the file's bytes. Do NOT base64 the whole file and cut the "
		"resulting string into pieces — the middle pieces of that are not valid "
		"base64, and the tool will say so. Slices may be any size up to the "
		"200 KB-of-base64 per-call limit and need not divide evenly.\n\n"
		"THE PIECES SURVIVE A RESTART. They are rows in a table, not cache "
		"entries, so a `bench restart` or a worker recycle mid-upload loses "
		"nothing — resume from `next_expected_index`.\n\n"
		"The first call creates the session; every later call must declare the "
		"same `total_chunks`. Re-sending an index REPLACES that piece and says so, "
		"because a caller whose call timed out has no other safe move. Returns how "
		"many pieces have arrived, how many bytes are staged, which index is "
		"expected next and which are still missing.",
		{
			"session_id": _field(
				_STRING,
				"Your own name for this upload — a UUID, or any unique descriptor. "
				"Every piece of one file uses the same one. Unique across the site.",
			),
			"chunk_index": _field(
				_INTEGER,
				"Where this piece goes in the finished file, counting from 0. Pieces may "
				"be sent in any order.",
			),
			"total_chunks": _field(
				_INTEGER,
				"How many pieces there will be in total. Declared on the first call and "
				"fixed from then on. Maximum 600.",
			),
			"chunk_base64": _field(
				_STRING,
				"This piece: the base64 of ITS OWN slice of the file's bytes, up to "
				"200 KB of base64 per call. Not a slice of the base64 of the whole file.",
			),
			"expected_sha256": _field(
				_STRING,
				"Optional. SHA-256 of the WHOLE file, hex, as you computed it before "
				"sending anything. Verified on commit. May be supplied on any call, "
				"including the last, but cannot then be changed.",
			),
			"expected_size": _field(
				_INTEGER,
				"Optional. Size of the WHOLE file in bytes. Verified on commit.",
			),
		},
		required=("session_id", "chunk_index", "total_chunks", "chunk_base64"),
		mutating=True,
		idempotent=True,
		title="Stage a file chunk",
	),
	"commit_staged_file": _tool(
		uploads.commit_staged_file,
		"MUTATING (default OFF). Reassemble a staged upload into a File, verify it "
		"against the caller's own hash, and clear the staging behind it.\n\n"
		"THREE DESTINATIONS. Pass `attach_to_doctype` and `attach_to_name` to hang "
		"the file off a document. Pass `governance_document=true` with `title` and "
		"`category` to file a NEW Governance Document with the file attached — the "
		"same archive entry attach_governance_document creates, for the documents "
		"too big to send in one call. Pass neither and the File lands on the site "
		"attached to nothing.\n\n"
		"IT REFUSES BEFORE IT ASSEMBLES. A missing piece is named by index range. "
		"A parent document that does not exist, cannot be written, is cancelled, "
		"already has a file by this name or belongs to another company is refused "
		"before a byte is reassembled, so a bad argument costs nothing. "
		"`expected_sha256` and `expected_size` are checked against the assembled "
		"bytes and refuse on mismatch.\n\n"
		"NOTHING IS DELETED UNTIL THE FILE EXISTS. Every refusal leaves the staged "
		"pieces exactly where they were, so a rejected commit is fixed by changing "
		"the argument, never by re-sending the file. `dry_run` reassembles and "
		"checks WITHOUT writing or clearing, so its reported sha256 is the sha256 "
		"of the file that would be created.",
		{
			"session_id": _field(_STRING, "The upload to commit, as given to stage_file_chunk."),
			"file_name": _field(
				_STRING,
				"Filename for the File this creates, with its extension — e.g. '2026-appraisal.pdf'.",
			),
			"attach_to_doctype": _field(
				_STRING, "DocType of the document to attach it to. Give with attach_to_name."
			),
			"attach_to_name": _field(_STRING, "That document's docname."),
			"is_private": _field(
				_BOOLEAN,
				"Store it private (default TRUE). A private file is readable only by "
				"somebody with read permission on the document it hangs off.",
			),
			"governance_document": _field(
				_BOOLEAN,
				"File a NEW Governance Document and attach the file to it, instead of "
				"attaching to an existing document. Default false. Needs `title` and "
				"`category`; refuses alongside attach_to_doctype.",
			),
			"title": _field(_STRING, "Governance Document only: what the document is called."),
			"category": _field(
				_STRING,
				"Governance Document only: one of the categories the Governance Document "
				"doctype offers on this site (Operating Agreement, Trust Document, ...).",
			),
			"company": _field(
				_STRING,
				"Governance Document only: whose archive it belongs in. Also serves as "
				"the cross-company guard when attaching to a document.",
			),
			"effective_date": _field(_STRING, "Governance Document only: YYYY-MM-DD."),
			"execution_date": _field(_STRING, "Governance Document only: YYYY-MM-DD."),
			"parties": _field(_STRING, "Governance Document only: who signed it."),
			"notes": _field(_STRING, "Governance Document only: anything else worth recording."),
			"supersedes": _field(
				_STRING,
				"Governance Document only: the docname of the entry this one replaces. "
				"That entry is marked superseded_by this one.",
			),
			"allow_cancelled": _field(
				_BOOLEAN, "Attach to a cancelled (docstatus 2) parent anyway. Default false."
			),
			"dry_run": _field(
				_BOOLEAN,
				"Assemble and verify without writing or clearing the staging. Default false.",
			),
		},
		required=("session_id", "file_name"),
		mutating=True,
		title="Commit a staged file",
	),
	"cancel_staged_upload": _tool(
		uploads.cancel_staged_upload,
		"MUTATING (default OFF). Discard a staged upload and its pieces without "
		"committing anything. What it destroys is half a file nobody has committed; "
		"no File is created and none is removed. Use it when a piece was mis-sent, "
		"when an upload is being restarted under a fresh session id, or to free a "
		"session id. Sessions idle for 24 hours are swept automatically, so this is "
		"for when you do not want to wait.",
		{"session_id": _field(_STRING, "The upload to discard.")},
		required=("session_id",),
		mutating=True,
		idempotent=True,
		title="Cancel a staged upload",
	),
	"list_staged_uploads": _tool(
		uploads.list_staged_uploads,
		"Every chunked upload currently in flight: session id, pieces received out "
		"of pieces expected, WHICH indexes are missing (as compact ranges), bytes "
		"staged, when a piece last arrived, and whether it is ready to commit. The "
		"tool you want when call 43 of 60 failed and you need to resume rather than "
		"re-send. A System Manager sees every session on the site; anybody else "
		"sees their own, which is the same set they may commit. Read-only.",
		{},
		title="List staged uploads",
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
			"regime": _field(
				_STRING,
				"Staple a TRAINING ANNEX to the packet: every Employee Training Record tagged "
				"with this regime over the packet's own period. One of FSMA, GAP, GlobalGAP, "
				"PrimusGFS, NOP, WPS, OR-OSHA, Other. It does not change what the packet "
				"computes — it is the answer to 'send the reconciliation, and your WPS training "
				"records for the same year'. The period comes from the filters' "
				"period_start/period_end, or from the Fiscal Year's own dates, or is ALL TIME "
				"when the filters name no period — which the annex states rather than guesses.",
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
	"regenerate_governance_document_pdf": _tool(
		governance.regenerate_governance_document_pdf,
		"MUTATING (default OFF). Convert a governance document's .docx attachment "
		"to PDF, attach the PDF beside it, and repoint the entry at it.\n\n"
		"WHY. A .docx is an editing format. It renders differently in different "
		"applications, some refuse to open it at all, and 'the copy on file' stops "
		"being one thing the moment two people open it in two programs. A "
		"governance document's primary format is a PDF; the .docx is the version "
		"somebody amends. Several archive entries landed .docx-only and this is how "
		"they get a fixed copy.\n\n"
		"THE .docx IS KEPT. This ADDS a PDF and never removes the source. What it "
		"changes besides adding a file is `attached_file`, which now points at the "
		"PDF so a reader following the archive lands on something that opens.\n\n"
		"IT NEEDS LibreOffice IN THE CONTAINER. Converting a .docx means a layout "
		"engine, and this app does not ship one. A host without it is refused "
		"BEFORE anything is read, naming the package to install, and nothing is "
		"installed at runtime. `dry_run` reports which converter would be used "
		"without converting.\n\n"
		"REFUSES: an entry with no .docx; an entry with SEVERAL .docx attachments "
		"unless `source_docx_file` names one (guessing between an original and an "
		"amendment and being right half the time is worse than asking); a "
		"`source_docx_file` that is not attached here or is not a .docx; and an "
		"entry that already has a PDF, unless `overwrite=true` — which then names "
		"the File it deleted, because removing an attachment from a governance "
		"archive is not something to do quietly.",
		{
			"governance_document": _field(
				_STRING, "Governance Document docname — list_governance_documents gives it."
			),
			"source_docx_file": _field(
				_STRING,
				"Which attachment to convert, by File docname or file name. Omit when the "
				"entry has exactly one .docx; required when it has more than one.",
			),
			"overwrite": _field(
				_BOOLEAN,
				"Replace an existing PDF rather than refusing. Default false. The File "
				"that is deleted is named in the result; the .docx is kept either way.",
			),
			"dry_run": _field(
				_BOOLEAN,
				"Report the source, the converter and what would be replaced, without "
				"converting or writing. Default false.",
			),
		},
		required=("governance_document",),
		mutating=True,
		available=_needs_doctype("Governance Document"),
		requires=(
			"the Governance Document DocType (run bench migrate) and a .docx-to-PDF "
			"converter in the container — LibreOffice headless, or docx2pdf on a host with "
			"Microsoft Word. The tool reports which it found and refuses cleanly with "
			"neither"
		),
		title="Regenerate a governance document PDF",
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
			"ownership_percentage": _field(
				_NUMBER, "0-100. The response says whether active members now total 100."
			),
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
			"effective_date": _field(
				_STRING, "When it took effect, YYYY-MM-DD. Also the entry's posting date."
			),
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
				_STRING,
				"For a Transfer or Reallocation: the member the interest moves TO. Required for those.",
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
			"file_name": _field(
				_STRING, "Filename for the uploaded content, e.g. 'operating-agreement-2020.pdf'."
			),
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
			"note_tenor_months": _field(
				_INTEGER, "The note's term in months, if the note document does not record it."
			),
			"note_maturity_date": _field(
				_STRING, "The note's maturity, YYYY-MM-DD, as an alternative to the tenor."
			),
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
			# v0.19.5. The maintenance/growth call, made HERE because this is where
			# the person who knows why the thing is being bought is standing. See
			# `assets._capex_argument` for why there is no default.
			"capex_type": _field(
				_STRING,
				"REQUIRED once this site has migrated v0.19.5. 'Maintenance' replaces "
				"productive capacity that wore out (a failed irrigation pump, a worn-out "
				"tractor, a replant in kind); 'Growth' adds capacity that was never there "
				"(a new block, a new zone, a second sprayer); 'Mixed' splits across the two "
				"portion fields. There is NO DEFAULT, deliberately: Sustainable CF/Acre "
				"subtracts maintenance capex, so an unclassified purchase quietly read as "
				"maintenance would let growth spending disappear into the line the "
				"replacement budget is built on.",
			),
			"maintenance_portion": _field(
				_NUMBER,
				"For Mixed only: how much of the purchase replaces existing capacity. With "
				"growth_portion it must equal purchase_amount within a cent. Defaulted from "
				"the total for Maintenance and to 0 for Growth.",
			),
			"growth_portion": _field(
				_NUMBER,
				"For Mixed only: how much buys capacity that was not there. Defaulted from "
				"the total for Growth and to 0 for Maintenance.",
			),
			"capex_justification": _field(
				_STRING,
				"REQUIRED for Growth and Mixed: what capacity does this add? Classifying "
				"spend as growth takes it out of the maintenance figure and RAISES "
				"sustainable cash flow per acre — the one direction in which a "
				"misclassification flatters the operation, which is why it is the one that "
				"has to carry a sentence.",
			),
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
						"bbch_stage": _field(
							_STRING, "Optional BBCH Stage dimension value for this row's line."
						),
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
			"principal_split": _field(
				_NUMBER, "How much of it reduces the note. Derived if only interest is given."
			),
			"interest_split": _field(
				_NUMBER, "How much of it is interest. Derived if only principal is given."
			),
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
			"appraisal_document": _field(_STRING, "The Governance Document holding the appraisal report."),
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
	"convey_parcel": _tool(
		realestate.convey_parcel,
		"MUTATING (default OFF). Move one parcel onto ANOTHER entity's books as a "
		"conveyance, carrying everything that hangs off it: the record itself, its "
		"attachments, and every lease, block, irrigation zone, housing unit and "
		"housing assignment pointing at it.\n\n"
		"THIS IS THE DOOR update_parcel REFUSES TO BE. Ground changing hands has a "
		"date, an instrument behind it and consequences for two sets of books; a "
		"tool that let it happen by editing a field would record none of those. "
		"`reason` is mandatory and is the narrative — the deed, the assignment, the "
		"trust amendment.\n\n"
		"IT DELETES AND RECREATES, WHICH IS THE HONEST SHAPE. A Parcel's docname "
		"encodes its entity ('Mill Creek - OML' vs 'Mill Creek - HLD'), the same way "
		"every Account docname carries a company abbreviation, so there is no field "
		"to change that makes the move true. THE PARCEL'S OWN SHORT KEY IS "
		"PRESERVED, which is why the farm registers survive: a Field, an Irrigation "
		"Zone and a Housing Unit are named after the PARCEL's abbreviation, not the "
		"company's, so all of a parcel's cabins keep the docnames they have always "
		"had and only their `parcel` link moves.\n\n"
		"IT WRITES NO JOURNAL ENTRY, DELIBERATELY. Recording that ground changed "
		"hands and booking what that costs are separate acts — basis transfer and "
		"any gain or loss recognised are entries with real tax consequences that "
		"somebody should write on purpose. The result names the entries still owed.\n\n"
		"REFUSES, EACH BECAUSE IT IS A DIFFERENT DOCUMENT'S JOB: an ACTIVE, "
		"unterminated lease whose term covers the conveyance date, with the leases "
		"named — conveying out from under a live lease needs a novation or a "
		"termination first, and a lease with no end date counts as running; a linked "
		"Fixed Asset, which is the balance-sheet side and moves by posting rather "
		"than by filing; a target company with no chart of accounts or no cost "
		"centers; a parcel name, assessor id or abbreviation the target already "
		"uses. EVERY refusal is reported at once rather than one per round trip.\n\n"
		"The appraisal report does NOT follow if it is filed in the old entity's "
		"archive — a governance document belongs to a company. That is reported as "
		"`appraisal_document_status: unlinked_needs_reattach`, never as a silent "
		"null. `dry_run=true` returns the whole plan and the whole refusal list "
		"without touching anything.",
		{
			"parcel": _field(_STRING, "The Parcel docname, or its parcel name."),
			"owning_entity": _field(_STRING, "Narrow a bare parcel name to the entity it is leaving."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"target_company": _field(
				_STRING, "The Company receiving the parcel. Must already have a chart and cost centers."
			),
			"effective_date": _field(
				_STRING,
				"When the conveyance happened, YYYY-MM-DD — the date on the deed, not the date "
				"it is being recorded here.",
			),
			"reason": _field(
				_STRING,
				"Why the ground moved and what authorises it. Mandatory, and written into the "
				"parcel's own conveyance history.",
			),
			"new_title_holder": _field(
				_STRING,
				"The Related Party holding title on the other side. Left out, the existing "
				"holder is kept only if it belongs to the receiving entity — one registered "
				"against the entity the ground just left is dropped and said so.",
			),
			"dry_run": _field(
				_BOOLEAN, "Report the full plan and every refusal without writing. Default false."
			),
		},
		required=("parcel", "target_company", "effective_date", "reason"),
		mutating=True,
		destructive=True,
		title="Convey a parcel to another entity",
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
				"Outbound (we are the lessor, collecting rent) or Inbound (we are the lessee, paying it).",
			),
			"parcel": _field(_STRING, "Only leases over this parcel."),
			"counterparty": _field(_STRING, "Only leases with this Related Party on the other side."),
			"active_on": _field(
				_STRING,
				"Only leases in force on this date, by the dates on the record. YYYY-MM-DD.",
			),
			"expiring_within_days": _field(_INTEGER, "Window for expiring_soon. Default 90."),
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
				_STRING,
				"The executed lease, base64, no data: prefix. Ceiling 8 MB. Not with lease_document_url.",
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
				"What they ARE: Individual, Trust, LLC, Corporation, Partnership, Family Member or Other.",
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
			"cash_clearing_account": _field(_STRING, "The clearing account. Omit to match it by name."),
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
			"performance_fee_percent": _field(_NUMBER, "Share of the gain over benchmark. Default 20."),
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
			"payer_address": _field(_STRING, "The payer's address as it should print on the forms."),
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
	# ── multi-company ───────────────────────────────────────────────────────
	"list_companies": _tool(
		company.list_companies,
		"Every Company on this site with its abbreviation, currency, country, parent "
		"company, chart of accounts and default cost center; whether a tax id is on "
		"file and its last four; the fiscal year period and how many years exist; the "
		"cost center and account counts; and the GL entry count with the first and "
		"last posting dates. Also reports whether this app's custom Party Types "
		"(Family, Contact) are registered. Read-only.\n\n"
		"THE GL COUNTS ARE THE POINT ON A MULTI-COMPANY SITE. A company with no "
		"postings can still have its currency changed; one with postings cannot, and "
		"this is where you find out which you are looking at.",
		{"limit": _field(_INTEGER, "Maximum companies returned. Default 100, hard maximum 500.")},
		title="List companies",
	),
	"create_company": _tool(
		company.create_company,
		"MUTATING (default OFF). Stand up one Company: name, abbreviation, country, "
		"default currency, tax id, optional parent for consolidation, and the fiscal "
		"year start month. ERPNext builds the chart of accounts, the root cost "
		"centers and the defaults on insert; this reports what it ACTUALLY got, "
		"which is not always what was asked for — an account count of zero means the "
		"named chart does not exist on this site.\n\n"
		"IT ALSO CREATES THE FISCAL YEAR containing today for the start month given, "
		"unless one of that name already exists. April (4) is the farm default; 1 is "
		"a calendar year.\n\n"
		"REFUSES: a duplicate company name; a duplicate abbreviation, because every "
		"account, cost center, parcel and lease docname ends in it and two companies "
		"sharing one makes those ambiguous; an abbreviation that is not alphanumeric; "
		"a country or currency this site does not have; a parent company that is not "
		"a group. `dry_run=true` reports the plan and the fiscal year it would create "
		"without writing.",
		{
			"company_name": _field(_STRING, "The legal or trading name, e.g. 'Constancy Farms LLC'."),
			"abbr": _field(
				_STRING,
				"Short key, e.g. 'CF'. Becomes the tail of every account docname on these "
				"books and cannot be changed afterwards.",
			),
			"country": _field(_STRING, "Country as ERPNext spells it. Default 'United States'."),
			"default_currency": _field(_STRING, "ISO code. Default 'USD'."),
			"fiscal_year_start_month": _field(
				_STRING,
				"1-12, or a month name. 4 (April) for a farm year, 1 for a calendar year. Default 1.",
			),
			"tax_id": _field(_STRING, "EIN or equivalent. Only the last four are ever echoed back."),
			"parent_company": _field(
				_STRING,
				"An existing GROUP company to consolidate under. Omit unless you are building "
				"a holding structure.",
			),
			"chart_of_accounts": _field(
				_STRING,
				"Name of a chart ERPNext ships, e.g. 'Standard'. Omit for the default; "
				"import_chart_of_accounts can replace it afterwards.",
			),
			"notes": _field(_STRING, "Stored in the company description where the version has one."),
			"dry_run": _field(_BOOLEAN, "Report the plan without writing. Default false."),
		},
		required=("company_name", "abbr"),
		mutating=True,
		title="Create a company",
	),
	"update_company": _tool(
		company.update_company,
		"MUTATING (default OFF). Change a company's country, tax id, notes — and its "
		"default currency, but ONLY while it has no posted GL entries. Every change "
		"is echoed as before → after, with the tax id redacted to its last four.\n\n"
		"REFUSES THREE THINGS AND SAYS WHY. The abbreviation and the company name, "
		"because both are baked into thousands of docnames and changing either is a "
		"migration rather than an edit. The currency once anything is posted, because "
		"every one of those entries was measured in the old one and a relabel "
		"restates the ledger without touching a number. The fiscal year start month "
		"once any fiscal year exists, because a year that changes shape mid-cycle "
		"produces two periods claiming the same days — a short year created "
		"deliberately with create_fiscal_year is the way to do that.",
		{
			"company": _field(_STRING, "Company docname or abbreviation."),
			"country": _field(_STRING, "New country."),
			"tax_id": _field(_STRING, "New EIN. Empty string clears it."),
			"notes": _field(_STRING, "New description. Empty string clears it."),
			"default_currency": _field(
				_STRING, "New ISO code. Refused outright once anything has been posted."
			),
			"fiscal_year_start_month": _field(
				_STRING, "Always refused, with the reason and the tool that does it properly."
			),
			"abbr": _field(_STRING, "Always refused — see the description."),
			"company_name": _field(_STRING, "Always refused — see the description."),
		},
		required=("company",),
		mutating=True,
		title="Update a company",
	),
	"register_party_types": _tool(
		company.register_party_types,
		"MUTATING (default OFF). Register this app's two custom Party Types so a "
		"Journal Entry line can carry them. Idempotent — a party type already there "
		"is reported, not recreated. They are also seeded on install and on every "
		"`bench migrate`; this is the tool for a site that cannot be migrated right "
		"now.\n\n"
		"WHY THESE TWO. ERPNext ships Customer, Supplier, Employee and Shareholder, "
		"and a family operation pays two kinds of people that fit none of them. "
		"`Family` is a relative receiving money that is neither payroll nor a "
		"purchase — booking those as Suppliers puts family transfers into vendor "
		"spend AND into the 1099 pre-fill, both wrong; a transfer below the gift "
		"threshold needs no W-9. `Contact` is the occasional consultant who is not a "
		"formal Supplier but IS paid for services, so the pre-fill surfaces them as "
		"BORDERLINE rather than dropping them.\n\n"
		"CHANGES NOTHING EXISTING. Rules and Journal Entries using Shareholder, "
		"Employee or Supplier are untouched; this adds party types, it does not "
		"reclassify anything.",
		{"dry_run": _field(_BOOLEAN, "Report what would be registered without writing. Default false.")},
		mutating=True,
		idempotent=True,
		title="Register the custom party types",
		available=_needs_doctype("Party Type"),
		requires="the Party Type DocType, which is core ERPNext",
	),
	# ── the family register ─────────────────────────────────────────────────
	"list_family_members": _tool(
		parties.list_family_members,
		"The family register: everybody a `Family`-party posting can name, with "
		"their relationship, WHOSE they are, whether they are still active, and "
		"whether a related-party record sits behind them. Every row carries "
		"`described_as` — 'Alexander Polehn — Son of Tim Polehn' — which is the "
		"sentence this register exists to be able to say. Read-only.\n\n"
		"THE THREE LISTS AT THE END ARE THE POINT. A missing related-party entry is "
		"not a gap for most of these — a relative who only receives transfers needs "
		"no W-9 and no disclosure. It IS a gap for one who also holds a role: a "
		"member, a lessor, a trustee. `without_related_party` is the list to read; "
		"`without_relationship` is the one to fill in, because 'why did money go to "
		"this person' is the first question these postings get asked; and "
		"`without_related_to` names everybody the register can describe but not "
		"attribute — 'Child' means two different people in an entity with two "
		"members. NOTHING BACKFILLED `related_to` on upgrade and nothing will, "
		"because which member somebody is the child of is a fact only the family "
		"has.",
		{
			"active": _field(_BOOLEAN, "true for only active members, false for only retired ones."),
			"relationship": _field(
				_STRING,
				"Only this relationship: Spouse, Son, Daughter, Child, Parent, Sibling, "
				"Grandchild, Grandparent, In-Law or Other.",
			),
			"related_to": _field(
				_STRING, "Only members whose related_to is this exact name — everybody's children."
			),
			"limit": _field(_INTEGER, "Maximum members returned. Default 100, hard maximum 500."),
		},
		title="List family members",
		available=_needs_doctype("Family"),
		requires="the Family DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_family_member": _tool(
		parties.get_family_member,
		"One family member in full: relationship, active status, the related-party "
		"record behind them if there is one, WHERE THEY SIT IN THE FAMILY, and "
		"EVERY POSTING THAT NAMES THEM — count, first and last date, net amount and "
		"which companies. Read-only.\n\n"
		"`relationship_chain` FOLLOWS `related_to` UPWARD AND THEN CROSSES TO THE "
		"COMPANY. Two different edges: `related_to` goes to another person and is "
		"followed as far as it goes, `related_party` goes to the SAME person's entry "
		"in the register that holds roles and entities and is followed once, at the "
		"top. That is how 'Alex → Son of Tim → Manager of Orchard Meadow, LLC' gets "
		"assembled out of Family → Related Party → Company, which no single record "
		"holds. `relationship_path` is the same walk as one sentence. It terminates "
		"on a cycle, on a depth limit, or on free text, and says which.\n\n"
		"The postings are read from the ledger rather than kept here, so the count "
		"cannot drift from what actually happened. That is the traceability half of "
		"a family petty-cash arrangement: 'we moved money to Alex eleven times last "
		"year' is a question with one true answer and it is in the GL.\n\n"
		"Never returns more than four digits of a taxpayer id, even from the linked "
		"related-party record.",
		{"family_name": _field(_STRING, "The person's name, which is the docname.")},
		required=("family_name",),
		title="Get a family member",
		available=_needs_doctype("Family"),
		requires="the Family DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_family_member": _tool(
		parties.create_family_member,
		"MUTATING (default OFF). Put one person on the family register so a Journal "
		"Entry line can carry `party_type='Family'` and name them.\n\n"
		"WHY THE REGISTER HAS TO EXIST. ERPNext resolves a posting's counterparty as "
		"a Dynamic Link THROUGH its party type: `party_type` is a Link to DocType, "
		"so `Family` only works because this app ships a Family DocType, and `party` "
		"only works if the person is a record in it. Customer, Supplier, Employee "
		"and Shareholder each have one.\n\n"
		"IT HOLDS NO TAX ID, ON PURPOSE. A transfer below the IRS annual gift "
		"exclusion is not compensation for services: no W-9, no 1099, which is the "
		"whole reason this party type is separate from Supplier. A relative who is "
		"genuinely paid for work is a Contact or a Supplier, and the posting should "
		"say so rather than the exclusion being widened. Where a relative ALSO holds "
		"a role worth disclosing — member, lessor, trustee — `related_party` points "
		"at the register that keeps four digits and never more.\n\n"
		"`related_to` ANSWERS 'OF WHOM'. A register that says 'Alexander Polehn — "
		"Son' and cannot say whose son is ambiguous the moment an entity has two "
		"members, and Orchard Meadow has two. Pass the other person's name: a Family "
		"docname, a Related Party docname or party name, or — for somebody in "
		"neither register — their name as plain text. The result reports which "
		"register answered as `related_to_doctype`, and None there means free text "
		"rather than a failure.\n\n"
		"SIMPLE CASE, ONE POINTER: Alex is Tim's son → related_to='Tim Polehn'. "
		"COMPLEX CASE, POINTER PLUS PROSE: Alex is Tim's son AND Donella's grandson "
		"→ related_to='Tim Polehn' and 'also grandson of Donella Polehn' in notes. "
		"One primary pointer is deliberate — a child table of relationships would "
		"turn a register whose job is to make a posting resolve into a genealogy "
		"database. Full genealogical modelling, if it is ever genuinely needed, "
		"belongs in a Family Tree doctype of its own.\n\n"
		"REFUSES a second record for the same name (the name is the docname, and it "
		"is what every posting points at), a `related_party` that does not exist, "
		"and somebody related to themselves.",
		{
			"family_name": _field(
				_STRING,
				"The person's name as a posting should read it. This becomes the docname and "
				"cannot be changed afterwards.",
			),
			"relationship": _field(
				_STRING,
				"Spouse, Son, Daughter, Child, Parent, Sibling, Grandchild, Grandparent, "
				"In-Law or Other. Son and Daughter sit beside Child rather than replacing "
				"it — records already saying Child stay valid.",
			),
			"related_to": _field(
				_STRING,
				"Whose relative this is: a Family docname, a Related Party docname or party "
				"name, or plain text for somebody in neither register.",
			),
			"related_party": _field(
				_STRING,
				"The Related Party entry for this person, when they also hold a role worth "
				"disclosing. Leave blank otherwise.",
			),
			"active": _field(_BOOLEAN, "Default true."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("family_name",),
		mutating=True,
		title="Add a family member",
		available=_needs_doctype("Family"),
		requires="the Family DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_family_member": _tool(
		parties.update_family_member,
		"MUTATING (default OFF). Change a family member's relationship, `related_to`, "
		"related party, active flag or notes. Every change is echoed as "
		"before → after.\n\n"
		"THIS IS WHERE AN EXISTING RECORD ACQUIRES `related_to`. Nothing backfilled "
		"it on upgrade and nothing will: which of two members somebody is the child "
		"of is a fact only the family has, and a migration that guessed would "
		"produce a register that looks complete and is wrong. "
		"`list_family_members` names everybody still missing one.\n\n"
		"CANNOT RENAME THEM. The name IS the docname and every journal entry that "
		"named them points at it; renaming would orphan those postings.\n\n"
		"RETIRING SOMEBODY IS `active=false`, NOT A DELETE, and the result says how "
		"many postings would have been orphaned — which is why the flag exists.",
		{
			"family_name": _field(_STRING, "The person's name, which is the docname."),
			"relationship": _field(
				_STRING,
				"New relationship — Spouse, Son, Daughter, Child, Parent, Sibling, "
				"Grandchild, Grandparent, In-Law or Other. Empty string clears it.",
			),
			"related_to": _field(
				_STRING,
				"Whose relative they are: a Family docname, a Related Party docname or party "
				"name, or plain text. Empty string clears it. Refused if it names them.",
			),
			"related_party": _field(_STRING, "New Related Party. Empty string clears it."),
			"active": _field(_BOOLEAN, "False retires them without deleting the record."),
			"notes": _field(_STRING, "New notes."),
			"new_family_name": _field(_STRING, "Always refused — see the description."),
		},
		required=("family_name",),
		mutating=True,
		title="Update a family member",
		available=_needs_doctype("Family"),
		requires="the Family DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── farm structure: fields ──────────────────────────────────────────────
	"list_fields": _tool(
		farm.list_fields,
		"The block register: every Field with its parcel, acreage, crop, variety, "
		"rootstock, planting year and density, condition, cost center and food-safety "
		"facts — plus totals for acreage, the oldest and newest planting years, a "
		"count by variety, and the varieties already in use on this site (which is "
		"the autosuggest list worth having, because a hardcoded one is wrong the "
		"first time somebody plants something new). Read-only.\n\n"
		"LAST SPRAY DATE COMES FROM TWO PLACES AND SAYS WHICH. What is recorded on "
		"the Field, and — where farm_precision_ag is installed — the newest Spray Log "
		"against it. The later of the two is reported as `last_spray_date` with "
		"`last_spray_source` naming where it came from, and both raw values are "
		"returned so they can be compared rather than believed.",
		{
			"owning_entity": _field(_STRING, "The company whose blocks to read. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"parcel": _field(_STRING, "Only blocks on this parcel. Docname or parcel name."),
			"crop": _field(_STRING, "Only this crop."),
			"variety": _field(_STRING, "Only this variety."),
			"condition": _field(_STRING, "Excellent, Good, Fair, Poor or Fallow."),
			"food_safety_zone": _field(
				_BOOLEAN, "true for only covered-produce blocks, false for only the rest."
			),
			"linked_to_cost_center": _field(
				_BOOLEAN, "true for only blocks with a cost center, false for only those without."
			),
			"limit": _field(_INTEGER, "Maximum blocks returned. Default 100, hard maximum 500."),
		},
		title="List fields",
		available=_needs_doctype("Field"),
		requires="the Field DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_field": _tool(
		farm.get_field,
		"One block in full: its planting, condition, cost center and food-safety "
		"facts, the parcel it sits on, every irrigation zone over it with the water "
		"rights they run under, and how much of the block is not zoned at all. "
		"Read-only.",
		{
			"field": _field(
				_STRING,
				"The Field docname ('Yellow Camp Block 3 - MC') or just the field name. A name "
				"matching blocks on two parcels is refused with both named.",
			),
			"parcel": _field(_STRING, "Narrow a bare field name to one parcel."),
			"owning_entity": _field(_STRING, "Narrow to one company. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("field",),
		title="Get a field",
		available=_needs_doctype("Field"),
		requires="the Field DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_field": _tool(
		farm.create_field,
		"MUTATING (default OFF). Register one planted block under a parcel: acreage, "
		"crop, variety, rootstock, planting year and density, condition, block "
		"number, and the Farm App id for a later sync. Creates one Field and nothing "
		"else — no cost center, no posting.\n\n"
		"THE DOCNAME IS '<field_name> - <parcel abbr>', so every parcel may have a "
		"'Block 3'. The parcel's abbreviation is derived from its name when it has "
		"none.\n\n"
		"FOOD SAFETY FIELDS ARE PART OF THE BLOCK, NOT A SEPARATE LOG. "
		"last_spray_date answers the re-entry interval question before it answers a "
		"WPS report; worker_hygiene_station_present decides whether a crew may work "
		"the block at all. Both are set here.\n\n"
		"REFUSES: a second block with the same name on one parcel; a Farm App id "
		"already claimed by another block; negative acreage or density; and — the one "
		"that catches a bad import — blocks whose acreage would sum to more than the "
		"parcel they are on, named with both figures and the excess.\n\n"
		"WARNS without refusing: no acreage; a food-safety block with no hygiene "
		"station or no water test. Every one of those is a fact worth recording "
		"precisely because it is a problem.",
		{
			"parcel": _field(_STRING, "The Parcel this block is part of. Docname or parcel name."),
			"field_name": _field(_STRING, "What it is called on the radio: 'Yellow Camp Block 3'."),
			"owning_entity": _field(_STRING, "Narrow a bare parcel name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"acreage": _field(_NUMBER, "Planted acres."),
			"crop": _field(_STRING, "What grows here. Default 'Cherry'."),
			"variety": _field(
				_STRING,
				"Bing, Rainier, Sweetheart, Chelan, Skeena — or whatever is actually planted. "
				"Free text; list_fields reports what this site already uses.",
			),
			"rootstock": _field(_STRING, "Mazzard, Gisela 6, Krymsk 5."),
			"planting_year": _field(_INTEGER, "The year the trees went in."),
			"planting_density_per_acre": _field(_INTEGER, "Trees per acre."),
			"condition": _field(_STRING, "Excellent, Good, Fair, Poor or Fallow."),
			"block_number": _field(_STRING, "The legacy block id, free text ('3A', 'N-12')."),
			"external_farm_app_id": _field(_STRING, "The Farm App's own id for this block."),
			# v0.19.5. The dates that decide whether this block is in the per-acre
			# denominator at all. Written at creation because a block planted today
			# is a block whose pre-yield window is known today, and reconstructing
			# them three years later means reconstructing them from a planting year.
			"productive_from_date": _field(
				_STRING,
				"First day this block is productive in its current planting cycle, YYYY-MM-DD "
				"— past the pre-yield years for a perennial. A block with no date is EXCLUDED "
				"from the Sustainable CF/Acre denominator and reported, never assumed "
				"productive.",
			),
			"productive_through_date": _field(
				_STRING,
				"Last day it was productive, YYYY-MM-DD, if it has been pulled or retired. "
				"Leave empty for a block still in production.",
			),
			"pre_yield_end_date": _field(
				_STRING,
				"When a perennial stops being capital under construction and becomes a crop, "
				"YYYY-MM-DD. Commonly year three or four for cherry.",
			),
			"last_spray_date": _field(_STRING, "Last application on this block, YYYY-MM-DD."),
			"water_test_last_date": _field(_STRING, "Agricultural water test date, YYYY-MM-DD."),
			"wildlife_intrusion_last_report": _field(
				_STRING, "Last recorded animal intrusion, YYYY-MM-DD (FSMA Subpart I)."
			),
			"food_safety_zone": _field(
				_BOOLEAN, "Grows produce eaten raw, so inside the Produce Safety Rule."
			),
			"worker_hygiene_station_present": _field(
				_BOOLEAN, "Toilets and handwashing within a quarter mile (FSMA Subpart L, WPS)."
			),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("parcel", "field_name"),
		mutating=True,
		title="Create a field",
		available=_needs_doctype("Field"),
		requires="the Field DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_field": _tool(
		farm.update_field,
		"MUTATING (default OFF). Change a registered block: acreage, crop, variety, "
		"rootstock, planting year and density, condition, block number, Farm App id, "
		"and every food-safety date and flag. Every change is echoed as "
		"before → after.\n\n"
		"CANNOT re-key: field_name is refused because the docname is built from it "
		"and every zone points at that docname. CANNOT move a block to another "
		"parcel — ground does not move, so a block on the wrong parcel was "
		"mis-registered. CANNOT set cost_center; that is link_field_to_cost_center, "
		"which checks the cost centre is on the same books and is not a group.\n\n"
		"The parcel acreage rule applies here too: raising a block's acreage past "
		"what the parcel has left is refused with both figures.",
		{
			"field": _field(_STRING, "The Field docname, or its field name."),
			"owning_entity": _field(_STRING, "Narrow a bare field name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"acreage": _field(_NUMBER, "New acreage."),
			"crop": _field(_STRING, "New crop."),
			"variety": _field(_STRING, "New variety."),
			"rootstock": _field(_STRING, "New rootstock."),
			"planting_year": _field(_INTEGER, "New planting year."),
			"planting_density_per_acre": _field(_INTEGER, "New trees per acre."),
			"condition": _field(_STRING, "New condition. Empty string clears it."),
			"block_number": _field(_STRING, "New block number."),
			"external_farm_app_id": _field(_STRING, "New Farm App id. Empty string clears it."),
			"productive_from_date": _field(
				_STRING, "New productive-from date, YYYY-MM-DD. The Sustainable CF/Acre denominator."
			),
			"productive_through_date": _field(
				_STRING, "New productive-through date, YYYY-MM-DD. Empty means still productive."
			),
			"pre_yield_end_date": _field(_STRING, "New pre-yield end date, YYYY-MM-DD."),
			"last_spray_date": _field(_STRING, "New last spray date, YYYY-MM-DD."),
			"water_test_last_date": _field(_STRING, "New water test date, YYYY-MM-DD."),
			"wildlife_intrusion_last_report": _field(_STRING, "New intrusion report date, YYYY-MM-DD."),
			"food_safety_zone": _field(_BOOLEAN, "New covered-produce flag."),
			"worker_hygiene_station_present": _field(_BOOLEAN, "New hygiene station flag."),
			"notes": _field(_STRING, "New notes."),
			"field_name": _field(_STRING, "Always refused — see the description."),
			"parcel": _field(_STRING, "Always refused — see the description."),
			"cost_center": _field(_STRING, "Always refused — use link_field_to_cost_center."),
		},
		required=("field",),
		mutating=True,
		title="Update a field",
		available=_needs_doctype("Field"),
		requires="the Field DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"link_field_to_cost_center": _tool(
		farm.link_field_to_cost_center,
		"MUTATING (default OFF). Point a block at the Cost Center its costs are "
		"booked to, so per-acre and per-block costing has somewhere to land. Sets one "
		"field; posts nothing and moves no existing entry.\n\n"
		"REFUSES: a cost center on another company's books, because a cost allocated "
		"across two companies is an intercompany transaction rather than a dimension; "
		"a group cost center, which ERPNext will not let a posting land on; a "
		"disabled one; and repointing a block that is already linked, unless "
		"replace=true — repointing means this season's costs and last season's land "
		"in different places.\n\n"
		"REPORTS, rather than refuses, when other blocks already book to the same "
		"cost center. A cost center per orchard is a legitimate design; it just is "
		"not per-block costing, and the result says so. `dry_run` validates and "
		"reports without writing.",
		{
			"field": _field(_STRING, "The Field docname, or its field name."),
			"cost_center": _field(_STRING, "Cost Center docname, number or name."),
			"owning_entity": _field(_STRING, "Narrow a bare field name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"replace": _field(_BOOLEAN, "Repoint a block that is already linked. Default false."),
			"dry_run": _field(_BOOLEAN, "Validate and report without writing. Default false."),
		},
		required=("field", "cost_center"),
		mutating=True,
		idempotent=True,
		title="Link a field to a cost center",
		available=_needs_doctype("Field"),
		requires="the Field DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_parcel_field_summary": _tool(
		farm.get_parcel_field_summary,
		"One parcel rolled up: how many blocks, planted acres against the parcel's "
		"own acreage and the difference, average block size, zone count and average "
		"zones per block, total flow, oldest and newest planting years, counts by "
		"condition and by variety, every water right in use, which blocks are "
		"food-safety blocks, which of those have no hygiene station, and which zones "
		"have no water test. Read-only.\n\n"
		"THE UNASSIGNED ACREAGE IS USUALLY THE INTERESTING NUMBER. Roads, ditches, "
		"headlands and the house are all real, so blocks summing to less than the "
		"parcel is normal — but a large gap on a parcel somebody thinks is fully "
		"blocked out is a missing Field.",
		{
			"parcel": _field(_STRING, "The Parcel docname, or its parcel name."),
			"owning_entity": _field(_STRING, "Narrow a bare parcel name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("parcel",),
		title="Parcel field summary",
		available=_needs_doctype("Field"),
		requires="the Field DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"import_farm_app_fields": _tool(
		farm.import_farm_app_fields,
		"MUTATING (default OFF, DRY RUN BY DEFAULT). Create ERPNext Fields from a "
		"batch of legacy Farm App records, each carrying its Farm App id so a later "
		"sync engine has something to match on. This is the schema-alignment "
		"foundation, NOT the sync: it never updates an existing Field, never deletes, "
		"and never writes back to the Farm App.\n\n"
		"EACH RECORD takes name, parcel_hint, acreage, variety, planting_year, "
		"block_number and farm_app_uuid. An unrecognised key is refused rather than "
		"ignored, because a typo silently dropped is a field somebody thinks they "
		"imported.\n\n"
		"THE WHOLE BATCH IS VALIDATED BEFORE THE FIRST INSERT. A half-imported farm "
		"is worse than an unimported one, because the second run has to work out "
		"which half. A record whose parcel_hint matches no Parcel, a batch that "
		"repeats a name or a Farm App id, a negative acreage — any of those refuses "
		"the lot.\n\n"
		"A block already registered under that name, or already carrying that Farm "
		"App id, is SKIPPED with the reason and the existing docname, so the same "
		"batch can be re-run safely. `apply=true` writes; without it you get the "
		"plan.",
		{
			"records": _field(
				{"type": "array", "items": _OBJECT},
				"The legacy field records. Each is an object with `name` and optionally "
				"parcel_hint, acreage, variety, planting_year, block_number, farm_app_uuid.",
			),
			"parcel": _field(
				_STRING,
				"Default Parcel for records with no parcel_hint. Without it, such a record is "
				"refused rather than guessed at.",
			),
			"owning_entity": _field(_STRING, "The company whose parcels the hints resolve against."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"apply": _field(
				_BOOLEAN, "Actually create the fields. Default false, which reports the plan only."
			),
		},
		required=("records",),
		mutating=True,
		title="Import Farm App fields",
		available=_needs_doctype("Field"),
		requires="the Field DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── farm structure: irrigation zones ────────────────────────────────────
	"list_irrigation_zones": _tool(
		farm.list_irrigation_zones,
		"The zone register: every zone with its block, number, water source, Oregon "
		"water right, flow, sprinkler type and area — plus total acres and gallons "
		"per minute, a count by water source, every water right in use, which zones "
		"have no agricultural water test on record, and which surface-water zones "
		"have no water right named. Read-only.\n\n"
		"THE TWO LISTS AT THE END ARE THE REPORT. A zone with no water test is a zone "
		"whose fruit cannot be cleared under FSMA Subpart E; a creek diversion with "
		"no right is not something Oregon treats as self-evident.",
		{
			"owning_entity": _field(_STRING, "The company whose zones to read. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"field": _field(_STRING, "Only zones on this block. Docname or field name."),
			"parcel": _field(_STRING, "Only zones on this parcel."),
			"water_source": _field(_STRING, "well, creek, municipal, pond, shared or other."),
			"sprinkler_type": _field(_STRING, "drip, micro, impact, gun or sub-surface."),
			"water_source_class": _field(_STRING, "I, II, III or IV."),
			"chlorination_active": _field(
				_BOOLEAN, "true for only chlorinated zones, false for only the rest."
			),
			"limit": _field(_INTEGER, "Maximum zones returned. Default 100, hard maximum 500."),
		},
		title="List irrigation zones",
		available=_needs_doctype("Irrigation Zone"),
		requires="the Irrigation Zone DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_irrigation_zone": _tool(
		farm.get_irrigation_zone,
		"One zone in full: source, water right, flow, sprinkler type, area in both "
		"square feet and acres, and its water-quality compliance — with the block it "
		"waters, how many zones that block has, and this zone's share of it. Names "
		"the compliance gaps in sentences rather than leaving them to be inferred. "
		"Read-only.",
		{
			"zone": _field(_STRING, "The Irrigation Zone docname ('YC3-Zone2 - MC') or just the zone name."),
			"field": _field(_STRING, "Narrow a bare zone name to one block."),
			"owning_entity": _field(_STRING, "Narrow to one company. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("zone",),
		title="Get an irrigation zone",
		available=_needs_doctype("Irrigation Zone"),
		requires="the Irrigation Zone DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_irrigation_zone": _tool(
		farm.create_irrigation_zone,
		"MUTATING (default OFF). Register one irrigation zone under a block: zone "
		"number, water source, Oregon water right, flow in GPM, sprinkler type, area "
		"in square feet, and the FSMA agricultural water facts.\n\n"
		"THE DOCNAME IS '<zone_name> - <parcel abbr>'. Not the block's abbreviation: "
		"a zone name already carries its block ('YC3-Zone2'), and suffixing it with "
		"the block again would say the same thing twice and drop the ground.\n\n"
		"AREA IN ACRES IS COMPUTED from square feet at 43,560 to the acre and cannot "
		"be passed — two figures a caller sets independently are two figures that "
		"will disagree.\n\n"
		"REFUSES: a second zone with the same name on one parcel; a zone number "
		"already used on that block, because that number is what somebody types into "
		"the controller at two in the morning; negative area or flow; and zones whose "
		"area would sum to more than the block they are on.\n\n"
		"WARNS without refusing: no area; surface water with no water right; a "
		"food-safety block whose zone has no water test.",
		{
			"field": _field(_STRING, "The Field this zone waters. Docname or field name."),
			"zone_name": _field(_STRING, "What it is called at the valve: 'YC3-Zone2'."),
			"owning_entity": _field(_STRING, "Narrow a bare field name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"zone_number": _field(_INTEGER, "The zone's number within its block, as the controller has it."),
			"water_source": _field(_STRING, "well, creek, municipal, pond, shared or other."),
			"water_right_id": _field(_STRING, "The Oregon water right or certificate number."),
			"flow_rate_gpm": _field(_NUMBER, "Design flow in gallons per minute."),
			"sprinkler_type": _field(_STRING, "drip, micro, impact, gun or sub-surface."),
			"area_sq_ft": _field(_NUMBER, "Irrigated area in square feet."),
			"water_test_last_date": _field(
				_STRING, "Last agricultural water test, YYYY-MM-DD (FSMA Subpart E)."
			),
			"water_source_class": _field(_STRING, "FSMA water quality class: I, II, III or IV."),
			"chlorination_active": _field(_BOOLEAN, "Running a chlorination or antimicrobial treatment."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
			"area_acres": _field(_NUMBER, "Always refused — computed from area_sq_ft."),
		},
		required=("field", "zone_name"),
		mutating=True,
		title="Create an irrigation zone",
		available=_needs_doctype("Irrigation Zone"),
		requires="the Irrigation Zone DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_irrigation_zone": _tool(
		farm.update_irrigation_zone,
		"MUTATING (default OFF). Change a registered zone: number, water source, "
		"water right, flow, sprinkler type, area, and the water-quality fields. Every "
		"change is echoed as before → after, and area in acres is recomputed.\n\n"
		"CANNOT re-key: zone_name is refused because the docname is built from it. "
		"CANNOT move a zone to another block — pipe does not move. CANNOT set "
		"area_acres directly. Refuses a zone number already used on that block, and "
		"an area that would push the block's zones past its acreage.",
		{
			"zone": _field(_STRING, "The Irrigation Zone docname, or its zone name."),
			"owning_entity": _field(_STRING, "Narrow a bare zone name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"zone_number": _field(_INTEGER, "New zone number."),
			"water_source": _field(_STRING, "New source. Empty string clears it."),
			"water_right_id": _field(_STRING, "New water right. Empty string clears it."),
			"flow_rate_gpm": _field(_NUMBER, "New flow."),
			"sprinkler_type": _field(_STRING, "New sprinkler type. Empty string clears it."),
			"area_sq_ft": _field(_NUMBER, "New area in square feet."),
			"water_test_last_date": _field(_STRING, "New water test date, YYYY-MM-DD."),
			"water_source_class": _field(_STRING, "New class: I, II, III or IV."),
			"chlorination_active": _field(_BOOLEAN, "New chlorination flag."),
			"notes": _field(_STRING, "New notes."),
			"zone_name": _field(_STRING, "Always refused — see the description."),
			"field": _field(_STRING, "Always refused — see the description."),
			"area_acres": _field(_NUMBER, "Always refused — computed from area_sq_ft."),
		},
		required=("zone",),
		mutating=True,
		title="Update an irrigation zone",
		available=_needs_doctype("Irrigation Zone"),
		requires="the Irrigation Zone DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── farm structure: boundaries ──────────────────────────────────────────
	"set_field_boundary": _tool(
		farm.set_field_boundary,
		"MUTATING (default OFF). Give a block its shape on the ground as a GeoJSON "
		"Polygon or MultiPolygon, and derive everything indexable from it: centroid, "
		"bounding box, H3 coverage at resolutions 6-10, and the area the polygon "
		"actually encloses. Sets no other field and posts nothing.\n\n"
		"THE POLYGON IS THE COMPLIANCE EVIDENCE. 'Which block was sprayed' is a "
		"Worker Protection Standard answer and 'was the crew in an authorised area' "
		"is a payroll and food-safety one, and both resolve to a shape. Without it "
		"the record says a name, and a name is not something anybody can check "
		"against a GPS fix.\n\n"
		"EVERY DERIVED FIELD IS REWRITTEN FROM THE POLYGON and none of them can be "
		"set directly — a figure a caller could edit independently is one that will "
		"disagree with the shape, and the disagreement shows up as a geofence saying "
		"no to somebody standing in the right place.\n\n"
		"REFUSES: anything that is not valid GeoJSON, with the parser's own message; "
		"a Point or LineString; a ring that is not closed; coordinates off Earth (a "
		"latitude past 90 usually means the pair is the wrong way round); a "
		"self-intersecting polygon, which has an area a computer will report and a "
		"containment test nobody can trust; and a polygon whose area differs from "
		"the recorded acreage by more than 25%, because at that point one of the two "
		"is about a different piece of ground.\n\n"
		"WARNS, DOES NOT REFUSE: a 5-25% area difference (a deed, a GIS trace and a "
		"tape measure routinely disagree); a shape spanning more than a degree; "
		"coordinates at [0, 0]; and zones on this block that now fall outside it. "
		"`dry_run=true` computes everything and writes nothing.",
		{
			"field": _field(_STRING, "The Field docname, or its field name."),
			"boundary_geojson": _field(
				_STRING,
				"The boundary as GeoJSON, in [longitude, latitude] degrees. A bare geometry, a "
				"Feature, or a FeatureCollection holding exactly one Feature — whichever your "
				"export produced.",
			),
			"owning_entity": _field(_STRING, "Narrow a bare field name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"dry_run": _field(_BOOLEAN, "Validate and compute without writing. Default false."),
		},
		required=("field", "boundary_geojson"),
		mutating=True,
		idempotent=True,
		title="Set a field boundary",
		available=_geo_ready("Field"),
		requires=_GEO_REQUIRES,
	),
	"set_zone_boundary": _tool(
		farm.set_zone_boundary,
		"MUTATING (default OFF). The same for an irrigation zone, plus one more "
		"answer: whether the zone sits inside the block it waters.\n\n"
		"CONTAINMENT IS REPORTED, NEVER ENFORCED. The obvious rule is that a zone "
		"must be inside its field, and it is wrong often enough to matter — a shared "
		"water line crosses a boundary, a pump house sits on the headland, a mainline "
		"runs down a road easement. Refusing those would make them unrecordable, so "
		"`boundary_contained_in_field` comes back true, false, or null when the block "
		"has no boundary of its own to check against.\n\n"
		"Refuses everything set_field_boundary refuses, comparing the area against "
		"the zone's own acreage (which is computed from its square footage).",
		{
			"zone": _field(_STRING, "The Irrigation Zone docname, or its zone name."),
			"boundary_geojson": _field(_STRING, "The boundary as GeoJSON, [longitude, latitude]."),
			"owning_entity": _field(_STRING, "Narrow a bare zone name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"dry_run": _field(_BOOLEAN, "Validate and compute without writing. Default false."),
		},
		required=("zone", "boundary_geojson"),
		mutating=True,
		idempotent=True,
		title="Set an irrigation zone boundary",
		available=_geo_ready("Irrigation Zone"),
		requires=_GEO_REQUIRES,
	),
	"find_fields_containing_point": _tool(
		farm.find_fields_containing_point,
		"Which blocks is this GPS fix inside? Read-only. THIS IS THE GEOFENCE "
		"QUERY — 'is this pick inside an assigned block', 'is this worker on ground "
		"they are rostered to'.\n\n"
		"BOUNDING BOX FIRST, THEN POINT-IN-POLYGON EXACTLY. The bounding box is the "
		"prefilter rather than the H3 index, and that is deliberate: a bbox is a "
		"guaranteed superset of the shape it bounds, so a candidate set built from "
		"it cannot miss the right answer. The exact test settles every candidate.\n\n"
		"THE BOUNDARY COUNTS AS INSIDE. A pick recorded on the edge of a block is in "
		"the block; a geofence that excludes its own boundary tells a picker standing "
		"on the headland that they are nowhere.\n\n"
		"Returns the matching blocks in full, the point's own H3 cell at every stored "
		"resolution, how many blocks were searched and how many survived the bbox "
		"cut — and HOW MANY BLOCKS HAVE NO BOUNDARY AT ALL, because an empty result "
		"on a half-mapped farm means 'not inside any MAPPED block', not 'not on the "
		"farm', and those are different answers to act on.",
		{
			"lat": _field(_NUMBER, "Latitude in decimal degrees, -90 to 90."),
			"lon": _field(_NUMBER, "Longitude in decimal degrees, -180 to 180."),
			"owning_entity": _field(_STRING, "Only this company's blocks. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("lat", "lon"),
		title="Find fields containing a point",
		available=_geo_ready("Field"),
		requires=_GEO_REQUIRES,
	),
	"find_fields_by_h3_cell": _tool(
		farm.find_fields_by_h3_cell,
		"Which blocks does this H3 cell touch? Read-only. The spatial-index query, "
		"for joining against anything else keyed on H3 — a bucket log, a crew "
		"track, a weather grid.\n\n"
		"STORED CELLS ARE EVERY CELL THE SHAPE TOUCHES, not every cell whose centre "
		"is inside it. That matters: an orchard block is smaller than one cell at "
		"resolutions 6 through 8, so a centre-based index returns nothing for most "
		"fields and would answer 'in no field' for a point plainly in one.\n\n"
		"Cells at resolutions 6-10 are matched directly. A finer cell is rolled up "
		"to 10 and a coarser one is compared against each block's rolled-up cells, "
		"so any resolution works and the result says which one the match was made "
		"at.\n\n"
		"A MATCH MEANS THE CELL TOUCHES THE BLOCK, not that everything in the cell "
		"is inside it. Use find_fields_containing_point when the question is about a "
		"specific position.",
		{
			"cell": _field(_STRING, "An H3 cell index, e.g. '8928f66e68fffff'. Any resolution."),
			"owning_entity": _field(_STRING, "Only this company's blocks. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("cell",),
		title="Find fields by H3 cell",
		available=_geo_ready("Field"),
		requires=_GEO_REQUIRES,
	),
	"import_field_boundary_geojson": _tool(
		farm.import_field_boundary_geojson,
		"MUTATING (default OFF, DRY RUN BY DEFAULT). Set boundaries on blocks that "
		"already exist, from a GeoJSON FeatureCollection — the tool for migrating a "
		"farm's existing polygons in one go. Each Feature's `properties` needs "
		"`field_name`, and `parcel_hint` unless a default `parcel` is given.\n\n"
		"PER-FEATURE, NOT WHOLE-BATCH, and that is the opposite of "
		"import_farm_app_fields on purpose. That tool CREATES records, so a half-run "
		"leaves a farm somebody has to reconcile. This one only sets a field on "
		"records that already exist, so one bad feature in forty is a bad feature: "
		"naming it and applying the other thirty-nine beats refusing the lot.\n\n"
		"NEVER CREATES A FIELD. A feature naming a block that is not registered is "
		"skipped with that said — register it first with create_field or "
		"import_farm_app_fields.\n\n"
		"Every per-feature refusal set_field_boundary makes applies here too, "
		"including the 25% area rule. `apply=true` writes; without it you get the "
		"plan.",
		{
			"feature_collection": _field(
				_OBJECT,
				"A GeoJSON FeatureCollection. Accepted as an object or as a JSON string.",
			),
			"parcel": _field(
				_STRING,
				"Default Parcel for features with no `parcel_hint`. Without it, such a feature "
				"is skipped rather than guessed at.",
			),
			"owning_entity": _field(_STRING, "The company whose parcels the hints resolve against."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"apply": _field(
				_BOOLEAN, "Actually set the boundaries. Default false, which reports the plan only."
			),
		},
		required=("feature_collection",),
		mutating=True,
		title="Import field boundaries from GeoJSON",
		available=_geo_ready("Field"),
		requires=_GEO_REQUIRES,
	),
	# ── labor camp housing ──────────────────────────────────────────────────
	"list_housing_units": _tool(
		housing.list_housing_units,
		"The camp register: every Housing Unit with its type, parcel, square footage, "
		"capacity, condition, access-card zone and compliance dates, plus who is "
		"currently in it — and totals for capacity, bodies and open beds. Also lists "
		"the units with overdue habitability inspections, the ones marked "
		"Uninhabitable, the ones that are FSMA worker facilities, and the ones whose "
		"recorded capacity exceeds what their floor area lawfully allows. "
		"Read-only.\n\n"
		"CAPACITY AND LAWFUL OCCUPANCY ARE DIFFERENT QUESTIONS and both are reported. "
		"One is how the operation uses the unit; the other is what 50 square feet per "
		"occupant allows. A gap between them is the finding.",
		{
			"owning_entity": _field(_STRING, "The company whose camp to read. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"parcel": _field(_STRING, "Only units on this parcel."),
			"unit_type": _field(
				_STRING,
				"Cabin, Toilet-Shower, Kitchen, Single-Family House, Multi-Unit Building, "
				"Manufactured Home, Bath House, Barn or Shop.",
			),
			"condition": _field(_STRING, "Excellent, Good, Fair, Poor, Needs Repair or Uninhabitable."),
			"or_housing_law_compliant": _field(_STRING, "Yes, No, Unknown or Not Applicable."),
			"fsma_worker_facility": _field(_BOOLEAN, "true for only FSMA worker facilities."),
			"limit": _field(_INTEGER, "Maximum units returned. Default 100, hard maximum 500."),
		},
		title="List housing units",
		available=_needs_doctype("Housing Unit"),
		requires="the Housing Unit DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_housing_unit": _tool(
		housing.get_housing_unit,
		"One unit in full, with everyone currently in it AND everyone who has ever "
		"been assigned to it, newest first. Names the compliance gaps in sentences: "
		"capacity over the lawful occupancy, no habitability inspection in a year, no "
		"smoke or CO detector test on record, Uninhabitable, subject to FSMA Subpart "
		"L. Read-only.",
		{
			"unit": _field(_STRING, "The Housing Unit docname ('MC-Cabin-01 - MC') or just the unit name."),
			"owning_entity": _field(_STRING, "Narrow a bare unit name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
		},
		required=("unit",),
		title="Get a housing unit",
		available=_needs_doctype("Housing Unit"),
		requires="the Housing Unit DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_housing_unit": _tool(
		housing.create_housing_unit,
		"MUTATING (default OFF). Register one building on a camp: type, square "
		"footage, capacity, year built, condition, the Fixed Asset carrying it, an "
		"access-card zone for a system not yet installed, and the compliance facts — "
		"FSMA worker facility, Oregon housing law status, habitability and detector "
		"test dates.\n\n"
		"LAWFUL OCCUPANCY IS COMPUTED from square footage at 50 sq ft per occupant "
		"(29 CFR 1910.142(b)(1), which Oregon's agricultural labor housing rules "
		"follow) unless you pass one. It is a default, not a derivation: a number "
		"somebody worked out for a fixed bunk layout is kept.\n\n"
		"REFUSES: a second unit with the same name on one parcel; an Asset on another "
		"company's books or already carrying a different unit.\n\n"
		"WARNS without refusing a capacity over 20 in anything not typed Multi-Unit "
		"Building — a twenty-person cabin is barracks by another name, and some "
		"really are. Also warns about missing square footage, missing detector tests "
		"and a missing habitability inspection.",
		{
			"parcel": _field(_STRING, "The Parcel the building stands on. Docname or parcel name."),
			"unit_name": _field(_STRING, "What is painted on the door: 'MC-Cabin-01'."),
			"owning_entity": _field(_STRING, "Narrow a bare parcel name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"unit_type": _field(
				_STRING,
				"Cabin, Toilet-Shower, Kitchen, Single-Family House, Multi-Unit Building, "
				"Manufactured Home, Bath House, Barn or Shop.",
			),
			"square_footage": _field(_NUMBER, "Floor area. Drives the lawful occupancy."),
			"capacity": _field(_INTEGER, "How many people it sleeps, as the operation uses it."),
			"year_built": _field(_INTEGER, "Year built."),
			"condition": _field(_STRING, "Excellent, Good, Fair, Poor, Needs Repair or Uninhabitable."),
			"related_asset": _field(_STRING, "The Fixed Asset carrying the building, if there is one."),
			"access_card_zone": _field(_STRING, "Access-control zone name, for a card system to come."),
			"fsma_worker_facility": _field(
				_BOOLEAN, "Subject to FSMA Produce Safety Rule Subpart L worker facility requirements."
			),
			"or_housing_law_compliant": _field(
				_STRING, "Yes, No, Unknown or Not Applicable. Default Unknown."
			),
			"max_occupants_per_or_law": _field(
				_INTEGER, "Override the computed occupancy limit. Omit to compute it from square footage."
			),
			"last_habitability_inspection": _field(_STRING, "YYYY-MM-DD."),
			"smoke_detector_last_test": _field(_STRING, "YYYY-MM-DD."),
			"co_detector_last_test": _field(_STRING, "YYYY-MM-DD."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("parcel", "unit_name"),
		mutating=True,
		title="Create a housing unit",
		available=_needs_doctype("Housing Unit"),
		requires="the Housing Unit DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_housing_unit": _tool(
		housing.update_housing_unit,
		"MUTATING (default OFF). Change a registered unit: type, square footage, "
		"capacity, year built, condition, asset link, access-card zone, and every "
		"compliance flag and date. Every change is echoed as before → after.\n\n"
		"CANNOT re-key: unit_name is refused because the docname is built from it and "
		"every assignment points at that docname. CANNOT move a building between "
		"parcels — even a manufactured home that really was moved should be "
		"re-registered where it stands, so the assignment history stays attached to "
		"the ground it happened on.\n\n"
		"CHANGING THE SQUARE FOOTAGE RECOMPUTES THE LAWFUL OCCUPANCY, but only when "
		"the stored limit was the computed one. A figure somebody typed themselves is "
		"kept.",
		{
			"unit": _field(_STRING, "The Housing Unit docname, or its unit name."),
			"owning_entity": _field(_STRING, "Narrow a bare unit name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"unit_type": _field(_STRING, "New unit type. Empty string clears it."),
			"square_footage": _field(_NUMBER, "New floor area."),
			"capacity": _field(_INTEGER, "New capacity."),
			"year_built": _field(_INTEGER, "New year built."),
			"condition": _field(_STRING, "New condition. Empty string clears it."),
			"related_asset": _field(_STRING, "New Fixed Asset. Empty string clears it."),
			"access_card_zone": _field(_STRING, "New access-card zone."),
			"fsma_worker_facility": _field(_BOOLEAN, "New FSMA worker facility flag."),
			"or_housing_law_compliant": _field(_STRING, "Yes, No, Unknown or Not Applicable."),
			"max_occupants_per_or_law": _field(_INTEGER, "New occupancy limit."),
			"last_habitability_inspection": _field(_STRING, "New inspection date, YYYY-MM-DD."),
			"smoke_detector_last_test": _field(_STRING, "New smoke detector test, YYYY-MM-DD."),
			"co_detector_last_test": _field(_STRING, "New CO detector test, YYYY-MM-DD."),
			"notes": _field(_STRING, "New notes."),
			"unit_name": _field(_STRING, "Always refused — see the description."),
			"parcel": _field(_STRING, "Always refused — see the description."),
		},
		required=("unit",),
		mutating=True,
		title="Update a housing unit",
		available=_needs_doctype("Housing Unit"),
		requires="the Housing Unit DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_housing_assignments": _tool(
		housing.list_housing_assignments,
		"Who is housed where. Defaults to current assignments only; pass "
		"current_only=false with a date range for the historical roster. Reports the "
		"distinct units and people, which assignments took a wage deduction, and how "
		"much deposit is still held. Read-only.\n\n"
		"THE WAGE DEDUCTION LIST IS THE COMPLIANCE ANSWER. ORS 653 and OAR 839-015 "
		"constrain deducting housing from wages, and this is where the assignments "
		"that did are named.",
		{
			"owning_entity": _field(_STRING, "The company whose camp to read. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"unit": _field(_STRING, "Only assignments on this unit."),
			"parcel": _field(_STRING, "Only assignments on this parcel."),
			"employee": _field(_STRING, "Only assignments for this employee id."),
			"current_only": _field(
				_BOOLEAN,
				"Only assignments with no end date. Default TRUE — pass false for history.",
			),
			"from_date": _field(_STRING, "Earliest assigned date, YYYY-MM-DD. Needs current_only=false."),
			"to_date": _field(_STRING, "Latest assigned date, YYYY-MM-DD. Needs current_only=false."),
			"limit": _field(_INTEGER, "Maximum assignments returned. Default 100, hard maximum 500."),
		},
		title="List housing assignments",
		available=_needs_doctype("Housing Assignment"),
		requires="the Housing Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_housing_assignment": _tool(
		housing.create_housing_assignment,
		"MUTATING (default OFF). Put one person in one unit from a date, with the "
		"deposit taken and whether a housing charge was deducted from their wages. "
		"Auto-named HA-YYYY-MM-<seq>, so a camp's intake sorts into seasons without a "
		"report.\n\n"
		"THIS RECORD IS THE AUDIT TRAIL for an IRS Section 119 exclusion — lodging on "
		"the business premises, for the employer's convenience, required as a "
		"condition of employment. It records the facts; it does not make the "
		"determination.\n\n"
		"REFUSES: an overlapping assignment on the same unit, naming the one already "
		"there — unless allow_multi_occupancy=true, which is the barracks case and is "
		"legitimate; a unit typed Toilet-Shower, Kitchen, Bath House, Barn or Shop, "
		"because nobody is assigned to a shower block; a unit marked Uninhabitable; "
		"and — where an HR app is installed — an employee who is not on file, because "
		"a roster naming somebody payroll has never heard of has already drifted.\n\n"
		"WHERE NO HR APP IS INSTALLED the employee is stored as text and the tool "
		"says so, because a camp roster that cannot be written until an HR module "
		"exists is a camp roster nobody keeps.",
		{
			"unit": _field(_STRING, "The Housing Unit docname, or its unit name."),
			"employee": _field(
				_STRING,
				"The Employee id, or their name where an HR app can resolve it. Free text on a "
				"site with no HR app.",
			),
			"employee_name": _field(_STRING, "The person's name. Required when no employee id is given."),
			"assigned_date": _field(_STRING, "The date they moved in, YYYY-MM-DD."),
			"end_date": _field(
				_STRING, "The date they moved out, if it is already known. Blank means current."
			),
			"owning_entity": _field(_STRING, "Narrow a bare unit name to one company."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"deposit_paid": _field(_NUMBER, "Deposit taken."),
			"deposit_returned": _field(_NUMBER, "Deposit already returned. Cannot exceed deposit_paid."),
			"housing_deduction_from_wages": _field(
				_STRING,
				"Yes, No or Unknown. ORS 653 constrains the deduction; Unknown is the answer "
				"that cannot be defended later.",
			),
			"allow_multi_occupancy": _field(
				_BOOLEAN,
				"Accept an overlapping assignment on this unit. Default false. Right for a bunk "
				"room, wrong for a typo.",
			),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("unit", "assigned_date"),
		mutating=True,
		title="Create a housing assignment",
		available=_needs_doctype("Housing Assignment"),
		requires="the Housing Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"end_housing_assignment": _tool(
		housing.end_housing_assignment,
		"MUTATING (default OFF). Write the date somebody moved out, and optionally "
		"the deposit returned. NEVER DELETES: an assignment removed when the person "
		"leaves cannot defend a Section 119 classification, cannot answer a wage "
		"claim, and cannot tell an investigator who was in the camp that week.\n\n"
		"REFUSES: an assignment that has already ended, because re-dating a departure "
		"is a correction rather than a close; an end date before the start; a deposit "
		"returned larger than the one on record as paid. REPORTS a deposit still "
		"held, so it is either refunded or explained. `dry_run` validates and reports "
		"without writing.",
		{
			"assignment": _field(_STRING, "The Housing Assignment docname, e.g. 'HA-2026-06-00003'."),
			"end_date": _field(_STRING, "The date they moved out, YYYY-MM-DD."),
			"deposit_returned": _field(_NUMBER, "How much of the deposit went back."),
			"notes": _field(_STRING, "Appended to the existing notes rather than replacing them."),
			"dry_run": _field(_BOOLEAN, "Validate and report without writing. Default false."),
		},
		required=("assignment", "end_date"),
		mutating=True,
		title="End a housing assignment",
		available=_needs_doctype("Housing Assignment"),
		requires="the Housing Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_housing_capacity": _tool(
		housing.get_housing_capacity,
		"Beds, bodies and what is overdue, broken down per parcel and totalled: "
		"residential units, capacity as the operation uses it, the lawful capacity "
		"the floor areas allow, how many are currently assigned, how many beds are "
		"open, and the units with overdue habitability inspections, marked "
		"Uninhabitable, or filled past what their floor area allows. Includes a plain "
		"`readout` — one sentence per parcel. Read-only.\n\n"
		"NON-RESIDENTIAL UNITS ARE COUNTED BUT NOT IN THE CAPACITY. A bath house and "
		"a shop are part of the camp; nobody sleeps in them, and adding their zero "
		"capacity to the total would make the register look thinner than it is.",
		{
			"owning_entity": _field(_STRING, "The company whose camp to read. `company` is an alias."),
			"company": _field(_STRING, "Alias for owning_entity."),
			"parcel": _field(_STRING, "Only this parcel. Omit for every parcel with housing."),
		},
		title="Housing capacity",
		available=_needs_doctype("Housing Unit"),
		requires="the Housing Unit DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_employee_housing_history": _tool(
		housing.get_employee_housing_history,
		"Everywhere one person has been housed, in order: every unit, every date "
		"range, whether they are currently assigned, deposits paid and returned and "
		"still outstanding, and which assignments took a wage deduction. Includes a "
		"plain `readout` — 'Antony assigned MC-Cabin-12 2026-06-01 → 2026-07-15', and "
		"a closing line when they are currently unassigned. Read-only.\n\n"
		"MATCHES ON THE EMPLOYEE ID FIRST, then on the name, because a site with no "
		"HR app records the name and a site with one records the id.",
		{"employee": _field(_STRING, "The Employee id, or the person's name as the roster has it.")},
		required=("employee",),
		title="Employee housing history",
		available=_needs_doctype("Housing Assignment"),
		requires="the Housing Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.15.0: Sprint 7 compliance framework ──────────────────────────────
	#
	# Read tools first inside each wave, mutating after, the same order the rest of
	# this catalogue keeps. Every mutating tool here defaults OFF except
	# `install_compliance_fields`, which is an installer rather than a writer of
	# somebody's data and is argued for in compliance_fields.py.
	"get_compliance_field_map": _tool(
		compliance.get_compliance_field_map,
		"What compliance requires of an OPERATIONAL record on this site, field by "
		"field: which doctype carries it, which framework wants it, why that "
		"framework wants it, and — the part that matters — what breaks in the "
		"day-to-day WORK if it is missing. Reports which fields are actually present "
		"here and which are not. Read-only.\n\n"
		"THE TEST IT ENCODES. Does removing a field break OPERATIONS, or only break "
		"COMPLIANCE REPORTING? Breaks operations too → compliance is woven in "
		"correctly. Only breaks reporting → it is a shadow layer and belongs "
		"somewhere else. Every field carries its answer under `breaks_operationally`.",
		{},
		title="Compliance field map",
	),
	"install_compliance_fields": _tool(
		compliance.install_compliance_fields,
		"MUTATING (default ON — the only one in this app). Adds the compliance "
		"columns to the doctypes where the work happens: applicator, EPA registration "
		"number, REI, PHI and weather on Spray Log; I-9 status, W-4 status, wage-law "
		"jurisdiction and farm labor contractor licensing on Employee; picker, crew, "
		"block, bin and shipment on the BucketLog bridge. Idempotent — the same "
		"installer runs on every migrate and a second run creates nothing.\n\n"
		"THIS IS THE ONE PLACE erpnext_mcp EXTENDS A DOCTYPE IT DID NOT CREATE, and "
		"it is deliberate. Compliance woven into the operational record is defensible "
		"under audit; a shadow log filled in afterwards drifts from reality the first "
		"busy week of harvest, and an auditor who finds two records of one spray that "
		"disagree has found something far worse than a missing field. The cost is "
		"real and stated: uninstalling this app drops these columns and everything "
		"typed into them.\n\n"
		"EVERY FIELD IS A CUSTOM FIELD, so the target app's own repository and "
		"migrations are untouched, and a version of farm_precision_ag that later adds "
		"the same field finds it already there rather than ending up with two.\n\n"
		"THE NUMBER WORTH READING IS THE BACKLOG. Three of the Employee fields and "
		"four of the Spray Log fields are REQUIRED, and Frappe enforces that on save "
		"rather than retroactively — so existing records stay readable and stop being "
		"re-saveable. `backlog` counts them per field. That is the operation's "
		"compliance debt stated in rows.\n\n"
		"A doctype that is not on this site is skipped BY NAME rather than failing. "
		"Supports dry_run.",
		{
			"dry_run": _field(
				_BOOLEAN,
				"Report what would be added, including the backlog counts, and write nothing. Default false.",
			)
		},
		mutating=True,
		idempotent=True,
		title="Install compliance fields",
	),
	# ── Wave 2: external evidence ───────────────────────────────────────────
	"list_compliance_policies": _tool(
		evidence.list_compliance_policies,
		"The SOP library: every written procedure, its category, version, effective "
		"date and review date, with the ones overdue for review and — the list worth "
		"acting on first — the ones with NO document attached. A policy record with "
		"no attached procedure is a claim that a procedure exists, and an auditor "
		"asks to read the procedure. Read-only.",
		{
			"company": _COMPANY,
			"category": _field(
				_STRING,
				"Harvest Hygiene, Spray SOP, Worker Training, Food Defense, Water Testing, "
				"Equipment Sanitation, Recall and Traceability, Worker Safety, Housing or Other.",
			),
			"status": _field(_STRING, "Draft, Active, Superseded or Retired."),
			"in_force_only": _field(_BOOLEAN, "Only Active policies. Default false."),
			"limit": _LIMIT,
		},
		title="List compliance policies",
		available=_needs_doctype("Compliance Policy"),
		requires="the Compliance Policy DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_compliance_policy": _tool(
		evidence.get_compliance_policy,
		"One procedure in full, with its WHOLE version chain — walked in both "
		"directions from this record — and every audit corrective action that named "
		"it as evidence. Read-only.\n\n"
		"THE CHAIN IS WHY THIS EXISTS. An audit asks which procedure was in force on "
		"the day something happened, and the answer is usually not the current "
		"version. A superseded policy is historical rather than wrong: it was correct "
		"on the dates it was in force.",
		{
			"policy": _field(_STRING, "The Compliance Policy docname, which is its name."),
			"company": _COMPANY,
		},
		required=("policy",),
		title="Get a compliance policy",
		available=_needs_doctype("Compliance Policy"),
		requires="the Compliance Policy DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_compliance_policy": _tool(
		evidence.create_compliance_policy,
		"MUTATING (default OFF). Register one written procedure at one version: "
		"category, version, effective date, review date, the person answerable for "
		"it, and the document itself.\n\n"
		"THE VERSION IS A FIELD, NOT PART OF THE NAME. A policy at v3 is the same "
		"policy it was at v1, and every audit finding that cited it should still "
		"resolve to it — so a second policy under an existing name is refused, and "
		"supersede_compliance_policy is how a genuinely separate revision is chained.\n\n"
		"REFUSES a review date before the effective date, which would be a procedure "
		"overdue for review before it took effect. WARNS about a missing document, a "
		"missing effective date and a missing review date, without refusing any of "
		"them.",
		{
			"policy_name": _field(
				_STRING,
				"What the procedure is called, in the words the crew and the auditor both use: "
				"'Harvest Hygiene SOP'. Becomes the docname.",
			),
			"category": _field(
				_STRING,
				"Harvest Hygiene, Spray SOP, Worker Training, Food Defense, Water Testing, "
				"Equipment Sanitation, Recall and Traceability, Worker Safety, Housing or Other.",
			),
			"version": _field(_STRING, "As the document states it: 'v3', '2026.1', 'Rev C'."),
			"company": _COMPANY,
			"policy_owner": _field(
				_STRING,
				"The User an auditor's question gets forwarded to. Login email or full name; "
				"one that does not exist is refused rather than silently dropped.",
			),
			"status": _field(_STRING, "Draft, Active, Superseded or Retired. Default Active."),
			"effective_date": _field(_STRING, "When this version took effect, YYYY-MM-DD."),
			"review_due_date": _field(_STRING, "When it is next due to be read and re-adopted."),
			"attached_document": _field(
				_STRING,
				"file_url of the procedure itself. Upload it with stage_file_chunk + "
				"commit_staged_file, or attach_file_to_document afterwards.",
			),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("policy_name", "category"),
		mutating=True,
		title="Create a compliance policy",
		available=_needs_doctype("Compliance Policy"),
		requires="the Compliance Policy DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_compliance_policy": _tool(
		evidence.update_compliance_policy,
		"MUTATING (default OFF). Change a procedure's category, version, status, "
		"dates, owner, document or notes. Every change is echoed as before → after.\n\n"
		"CANNOT re-key it — the docname is what every audit finding points at. CANNOT "
		"set either end of the version chain: a chain written from one end only says "
		"something different to a reader coming from the other, so both links are "
		"written in one act by supersede_compliance_policy.",
		{
			"policy": _field(_STRING, "The Compliance Policy docname."),
			"company": _COMPANY,
			"category": _field(_STRING, "New category."),
			"version": _field(_STRING, "New version."),
			"status": _field(_STRING, "Draft, Active, Superseded or Retired."),
			"effective_date": _field(_STRING, "New effective date, YYYY-MM-DD."),
			"review_due_date": _field(_STRING, "New review date, YYYY-MM-DD."),
			"policy_owner": _field(_STRING, "New owner. Empty string clears it."),
			"attached_document": _field(_STRING, "New file_url."),
			"notes": _field(_STRING, "New notes."),
			"policy_name": _field(_STRING, "Always refused — see the description."),
			"supersedes": _field(_STRING, "Always refused — see the description."),
			"superseded_by": _field(_STRING, "Always refused — see the description."),
		},
		required=("policy",),
		mutating=True,
		title="Update a compliance policy",
		available=_needs_doctype("Compliance Policy"),
		requires="the Compliance Policy DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"supersede_compliance_policy": _tool(
		evidence.supersede_compliance_policy,
		"MUTATING (default OFF). Replace one procedure with another, writing BOTH "
		"ends of the chain in one act and setting the old one's status to "
		"Superseded. Requires a `reason` and records it on both timelines.\n\n"
		"BOTH ENDS, BECAUSE 'WHICH PROCEDURE WAS IN FORCE ON THE DAY THIS HAPPENED' "
		"is asked from whichever end the auditor happens to start. A half-written "
		"chain tells the two readers different things.\n\n"
		"REFUSES: a policy superseding itself; one that was already superseded (a "
		"procedure has one successor, and two would make 'what was in force' "
		"unanswerable); a successor whose effective date PREDATES the one it "
		"replaces, which would leave a period with two procedures in force and no "
		"way to tell which.\n\n"
		"The superseded policy is NOT deleted and is NOT wrong — it was correct on "
		"the dates it was in force. Supports dry_run.",
		{
			"policy": _field(_STRING, "The procedure being replaced."),
			"superseded_by": _field(_STRING, "The procedure replacing it."),
			"reason": _field(
				_STRING,
				"Why the procedure was revised. Recorded on both records; must be a real "
				"sentence, not a word.",
			),
			"company": _COMPANY,
			"dry_run": _field(_BOOLEAN, "Report what would be written and write nothing."),
		},
		required=("policy", "superseded_by", "reason"),
		mutating=True,
		title="Supersede a compliance policy",
		available=_needs_doctype("Compliance Policy"),
		requires="the Compliance Policy DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_certifications": _tool(
		evidence.list_certifications,
		"The certificate and licence register, SOONEST EXPIRY FIRST, which is the "
		"order somebody works them in. Reports what has expired, what is inside its "
		"renewal window, and what has no certificate attached. Read-only.\n\n"
		"`expired` IS READ FROM THE DATE, NOT FROM THE STATUS FIELD. Nothing in this "
		"app rewrites a status when a date passes: a controller that did would only "
		"run on documents somebody saved, so the expired certificates would be "
		"exactly the ones still reading Active.",
		{
			"company": _COMPANY,
			"cert_type": _field(
				_STRING,
				"GAP, GlobalGAP, PrimusGFS, Organic, Applicator License, Farm Labor Contractor "
				"License, Commercial Driver License, Food Safety Training, First Aid / CPR, "
				"Water Test Certification or Other.",
			),
			"status": _field(
				_STRING, "Active, Expired, Suspended, Revoked, Superseded or Not Yet Effective."
			),
			"holder": _field(_STRING, "Only certificates held by this person or company."),
			"expiring_only": _field(_BOOLEAN, "Only ones inside their renewal window. Default false."),
			"limit": _LIMIT,
		},
		title="List certifications",
		available=_needs_doctype("Certification"),
		requires="the Certification DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_certification": _tool(
		evidence.get_certification,
		"One certificate with its FULL renewal history, including every period it "
		"was allowed to lapse. Resolves the holder's name against the Related Party, "
		"Family, Employee and Company registers and reports which one answered — a "
		"name in none of them is a contractor on nobody's payroll, which is a "
		"legitimate answer rather than a failure. Read-only.\n\n"
		"THE LAPSES ARE THE POINT. A certificate renewed four times and one issued "
		"yesterday look identical from the current dates alone, and only one of them "
		"is evidence of a maintained programme. Renewing late does not close a gap "
		"that already happened, and the history keeps it visible.",
		{
			"certification": _field(_STRING, "The Certification docname."),
			"company": _COMPANY,
		},
		required=("certification",),
		title="Get a certification",
		available=_needs_doctype("Certification"),
		requires="the Certification DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_certification": _tool(
		evidence.create_certification,
		"MUTATING (default OFF). Register one certificate or licence: type, holder, "
		"issuing body, issue and expiration dates, renewal window and the certificate "
		"itself.\n\n"
		"THE RENEWAL WINDOW IS A LEAD TIME, NOT A REMINDER PREFERENCE. It defaults to "
		"90 days because that is roughly what an Oregon farm labor contractor licence "
		"renewal takes once the bond and background check are counted. Setting it to "
		"7 does not make the agency faster; it makes the alert arrive too late to act "
		"on.\n\n"
		"REFUSES an expiration before the issue date (a transposition every time) and "
		"a duplicate name — a renewal is not a new record, it is renew_certification, "
		"which keeps the history of every previous term.",
		{
			"cert_name": _field(
				_STRING,
				"Distinct enough that two cannot collide: 'GlobalGAP — Highland LLC 2026'. "
				"Becomes the docname.",
			),
			"cert_type": _field(
				_STRING,
				"GAP, GlobalGAP, PrimusGFS, Organic, Applicator License, Farm Labor Contractor "
				"License, Commercial Driver License, Food Safety Training, First Aid / CPR, "
				"Water Test Certification or Other.",
			),
			"company": _COMPANY,
			"holder": _field(
				_STRING,
				"Whose certificate it is. Free text: the holder may be a Related Party, a Family "
				"member, an employee, a company or somebody in no register at all, and a Frappe "
				"Link points at exactly one doctype.",
			),
			"issuing_body": _field(_STRING, "'Oregon Department of Agriculture', 'Primus Auditing Ops'."),
			"issued_date": _field(_STRING, "YYYY-MM-DD."),
			"expiration_date": _field(
				_STRING, "The date it stops being a defence. The most load-bearing column here."
			),
			"renewal_window_days": _field(_INTEGER, "How long the issuing body actually takes. Default 90."),
			"certificate_number": _field(_STRING, "The number printed on it."),
			"status": _field(
				_STRING, "Active, Expired, Suspended, Revoked, Superseded or Not Yet Effective."
			),
			"attached_certificate": _field(_STRING, "file_url of the certificate itself."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("cert_name", "cert_type"),
		mutating=True,
		title="Create a certification",
		available=_needs_doctype("Certification"),
		requires="the Certification DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_certification": _tool(
		evidence.update_certification,
		"MUTATING (default OFF). Change a certificate's type, status, holder, issuing "
		"body, dates, renewal window, number, document or notes.\n\n"
		"MOVING THE EXPIRATION FORWARD IS REFUSED — that is a RENEWAL, and "
		"renew_certification is the tool. Editing the date in place would produce a "
		"certificate that looks as though it never expired, which is exactly the fact "
		"somebody would want hidden and exactly the fact an auditor asks about.",
		{
			"certification": _field(_STRING, "The Certification docname."),
			"company": _COMPANY,
			"cert_type": _field(_STRING, "New type."),
			"status": _field(_STRING, "New status."),
			"holder": _field(_STRING, "New holder."),
			"issuing_body": _field(_STRING, "New issuing body."),
			"issued_date": _field(_STRING, "New issue date, YYYY-MM-DD."),
			"expiration_date": _field(
				_STRING, "New expiration. Moving it FORWARD is refused — see the description."
			),
			"renewal_window_days": _field(_INTEGER, "New renewal window."),
			"certificate_number": _field(_STRING, "New certificate number."),
			"attached_certificate": _field(_STRING, "New file_url."),
			"notes": _field(_STRING, "New notes."),
			"cert_name": _field(_STRING, "Always refused — it is the docname."),
		},
		required=("certification",),
		mutating=True,
		title="Update a certification",
		available=_needs_doctype("Certification"),
		requires="the Certification DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"renew_certification": _tool(
		evidence.renew_certification,
		"MUTATING (default OFF). Move a certificate's expiration OUT and record what "
		"was actually done to earn it — the audit that was passed, the fee that was "
		"paid, the training that was completed. Appends to the renewal history rather "
		"than editing the date in place.\n\n"
		"A LAPSE IS REPORTED, NOT HIDDEN. A renewal recorded after the previous "
		"expiration is a period the operation was uncertified, and renewing late does "
		"not close a gap that already happened — `lapsed_days` names it and the "
		"renewal row keeps it.\n\n"
		"REFUSES a new expiration that is not after the current one: that is a "
		"correction, not a renewal, and recording it as one would put a term in the "
		"history that never existed. Sets the status back to Active where it had "
		"lapsed. Supports dry_run.",
		{
			"certification": _field(_STRING, "The Certification docname."),
			"new_expiration": _field(
				_STRING, "The new expiration date, YYYY-MM-DD. Must be later than the current one."
			),
			"what_was_done": _field(
				_STRING,
				"What was actually done to renew it. The part nobody can reconstruct from the "
				"dates; must be a real sentence.",
			),
			"renewed_on": _field(
				_STRING, "When the renewal happened. Defaults to today; a future date is refused."
			),
			"renewed_by": _field(_STRING, "Who did it. Defaults to the calling user."),
			"certificate_number": _field(_STRING, "New certificate number, where a renewal issues one."),
			"attached_certificate": _field(_STRING, "file_url of the new certificate."),
			"company": _COMPANY,
			"dry_run": _field(_BOOLEAN, "Report the lapse and the new term, and write nothing."),
		},
		required=("certification", "new_expiration", "what_was_done"),
		mutating=True,
		title="Renew a certification",
		available=_needs_doctype("Certification"),
		requires="the Certification DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_regulatory_filings": _tool(
		evidence.list_regulatory_filings,
		"What was filed, to whom, when, under what docket number and what came back. "
		"Calls out the ones still awaiting a response, the ones whose response is "
		"overdue, and the ones with no document attached. Read-only.\n\n"
		"A FILING NOBODY CAN PROVE WAS MADE IS A FILING THAT WAS NOT MADE. The "
		"agency's position in a dispute is that they have no record, and 'we sent it' "
		"is worth nothing against that without a date, a confirmation number and the "
		"document.",
		{
			"company": _COMPANY,
			"agency": _field(
				_STRING, "USDA, ODA, DOL, EPA, IRS, OR-DOR, OR-BOLI, OSHA, FDA, WA-L&I or Other."
			),
			"filing_type": _field(_STRING, "'1099-NEC', 'OSHA-300A', 'Pesticide-Application-Report'."),
			"status": _field(_STRING, "Draft, Submitted, Accepted, Rejected, Amended or Withdrawn."),
			"from_date": _field(_STRING, "Earliest submission date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest submission date, YYYY-MM-DD."),
			"limit": _LIMIT,
		},
		title="List regulatory filings",
		available=_needs_doctype("Regulatory Filing"),
		requires="the Regulatory Filing DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_regulatory_filing": _tool(
		evidence.get_regulatory_filing,
		"One filing with its response, both attached documents, and what is missing "
		"from the proof it was made. Read-only.",
		{
			"filing": _field(_STRING, "The Regulatory Filing docname."),
			"company": _COMPANY,
		},
		required=("filing",),
		title="Get a regulatory filing",
		available=_needs_doctype("Regulatory Filing"),
		requires="the Regulatory Filing DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_regulatory_filing": _tool(
		evidence.create_regulatory_filing,
		"MUTATING (default OFF). Record something submitted to an agency: the agency, "
		"the form, the period it covers, when it went, the docket number, the "
		"documents, and any response deadline.\n\n"
		"REFUSES a filing marked Submitted (or Accepted, Rejected, Amended) with NO "
		"submission date. A half-filled filing record is more dangerous than none: an "
		"audit packet would include it and an auditor would read it as evidence of "
		"something that may not have happened. A Draft with no dates is exactly what "
		"a filing being prepared looks like and is allowed.\n\n"
		"Also refuses a submission date in the future, a response received before the "
		"filing was sent, and a response deadline that had passed before it was sent.",
		{
			"filing_name": _field(
				_STRING,
				"Said once and distinctly: '1099-NEC 2025 — Orchard Meadow LLC'. Becomes the docname.",
			),
			"agency": _field(
				_STRING, "USDA, ODA, DOL, EPA, IRS, OR-DOR, OR-BOLI, OSHA, FDA, WA-L&I or Other."
			),
			"filing_type": _field(
				_STRING,
				"The form in the agency's own name for it: '1099-NEC', 'OSHA-300A', 'Form 943'. "
				"Free text — agencies invent forms faster than a Select can be maintained.",
			),
			"company": _COMPANY,
			"period_covered": _field(_STRING, "As the filing states it: '2025', 'Q3 2026'."),
			"status": _field(
				_STRING, "Draft, Submitted, Accepted, Rejected, Amended or Withdrawn. Default Submitted."
			),
			"submission_date": _field(_STRING, "When it actually went, YYYY-MM-DD."),
			"docket_number": _field(
				_STRING, "The agency's own reference — docket, confirmation, certified mail number."
			),
			"response_due_date": _field(_STRING, "When a response is expected or required by."),
			"response_received_date": _field(
				_STRING, "When they answered. Setting this dismisses the response alert."
			),
			"response": _field(_STRING, "What the agency said, in their words where possible."),
			"attached_filing": _field(_STRING, "file_url of the filing AS SUBMITTED — not the working copy."),
			"attached_response": _field(_STRING, "file_url of their reply."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("filing_name", "agency", "filing_type"),
		mutating=True,
		title="Create a regulatory filing",
		available=_needs_doctype("Regulatory Filing"),
		requires="the Regulatory Filing DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_regulatory_filing": _tool(
		evidence.update_regulatory_filing,
		"MUTATING (default OFF). Record the response, the docket number, the attached "
		"documents or a change of status. Recording a response date auto-dismisses "
		"the filing's response alert on the next sweep — nobody has to switch it off.",
		{
			"filing": _field(_STRING, "The Regulatory Filing docname."),
			"company": _COMPANY,
			"agency": _field(_STRING, "New agency."),
			"filing_type": _field(_STRING, "New form."),
			"period_covered": _field(_STRING, "New period."),
			"status": _field(_STRING, "New status."),
			"submission_date": _field(_STRING, "New submission date, YYYY-MM-DD."),
			"docket_number": _field(_STRING, "New docket or confirmation number."),
			"response_due_date": _field(_STRING, "New response deadline."),
			"response_received_date": _field(_STRING, "When they answered."),
			"response": _field(_STRING, "What they said."),
			"attached_filing": _field(_STRING, "New file_url for the filing."),
			"attached_response": _field(_STRING, "New file_url for the response."),
			"notes": _field(_STRING, "New notes."),
			"filing_name": _field(_STRING, "Always refused — it is the docname."),
		},
		required=("filing",),
		mutating=True,
		title="Update a regulatory filing",
		available=_needs_doctype("Regulatory Filing"),
		requires="the Regulatory Filing DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_audit_events": _tool(
		evidence.list_audit_events,
		"Every audit and inspection, with open corrective actions counted and the "
		"OVERDUE ones named. Read-only.\n\n"
		"AN OPERATION IS NOT JUDGED ON HAVING NO FINDINGS. Every audit produces some, "
		"and a clean report usually means the auditor did not look hard. It is judged "
		"on closing them, which is what `overdue_corrective_actions` counts and what "
		"the same auditor asks about on the next visit.",
		{
			"company": _COMPANY,
			"audit_type": _field(
				_STRING,
				"FSMA, GAP, GlobalGAP, PrimusGFS, Organic, OSHA, USDA, DOL, EPA, ODA, FDA, "
				"Buyer Audit, Internal Audit or Other.",
			),
			"result": _field(_STRING, "Pending, Passed, Passed With Conditions, Failed or Not Scored."),
			"from_date": _field(_STRING, "Earliest audit date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest audit date, YYYY-MM-DD."),
			"open_only": _field(_BOOLEAN, "Only audits whose corrective actions are not all closed."),
			"limit": _LIMIT,
		},
		title="List audit events",
		available=_needs_doctype("Audit Event"),
		requires="the Audit Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_audit_event": _tool(
		evidence.get_audit_event,
		"One audit in full: scope, findings, and every corrective action with its "
		"severity, its deadline, how many days past it is, what was actually done and "
		"what proves it. Read-only.\n\n"
		"Flags a conditional pass or a failure with NO corrective actions recorded — "
		"a conditional pass has conditions and a failure has reasons, and if neither "
		"is written down here they are not written down anywhere.",
		{
			"audit": _field(_STRING, "The Audit Event docname."),
			"company": _COMPANY,
		},
		required=("audit",),
		title="Get an audit event",
		available=_needs_doctype("Audit Event"),
		requires="the Audit Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_audit_event": _tool(
		evidence.create_audit_event,
		"MUTATING (default OFF). Record an audit or inspection: who came, when, what "
		"they looked at, what they found, and one row per thing that has to be "
		"fixed.\n\n"
		"THE CORRECTIVE ACTIONS TABLE IS THE SUBSTANCE. Each row takes a finding IN "
		"THE AUDITOR'S WORDS, a severity, an owner who is a PERSON rather than a "
		"department, and a deadline — most schemes give 28 days for a Major and days "
		"for a Critical. A row with no due date is warned about, because nothing will "
		"ever say it is late.\n\n"
		"Warns about a missing report (it is the auditor's own words, and nothing "
		"else is) and about a conditional pass or failure with no actions recorded.",
		{
			"audit_name": _field(
				_STRING, "'PrimusGFS 2026 — Highland Ranch', 'ODA Pesticide Inspection 2026-06-14'."
			),
			"audit_type": _field(
				_STRING,
				"FSMA, GAP, GlobalGAP, PrimusGFS, Organic, OSHA, USDA, DOL, EPA, ODA, FDA, "
				"Buyer Audit, Internal Audit or Other.",
			),
			"audit_date": _field(_STRING, "The date they were on the ground, YYYY-MM-DD."),
			"auditor": _field(_STRING, "Who did it — the name, and the firm if they differ."),
			"company": _COMPANY,
			"result": _field(
				_STRING,
				"Pending, Passed, Passed With Conditions, Failed or Not Scored. Default Pending — "
				"the report often arrives weeks after the visit.",
			),
			"scope": _field(
				_STRING,
				"What they actually looked at. An audit of the packing house is not evidence about the camp.",
			),
			"findings": _field(_STRING, "The findings as written, in full."),
			"corrective_actions": _field(
				{"type": "array", "items": _OBJECT},
				"One object per finding: {finding, severity, status, assigned_to, due_date, "
				"closed_date, corrective_action, evidence, notes}. `finding` is required on each. "
				"Severity is Observation, Minor, Major or Critical.",
			),
			"attached_report": _field(_STRING, "file_url of the audit report."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
		},
		required=("audit_name", "audit_type", "audit_date"),
		mutating=True,
		title="Create an audit event",
		available=_needs_doctype("Audit Event"),
		requires="the Audit Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_audit_event": _tool(
		evidence.update_audit_event,
		"MUTATING (default OFF). Change the scope, findings, result or report; ADD "
		"corrective actions; and CLOSE one by index.\n\n"
		"CLOSING ONE REQUIRES SAYING WHAT WAS DONE. 'Trained the crew' is not a "
		"corrective action; 'added a hand-wash check to the pre-harvest walk and "
		"retrained the four crew bosses on 2026-08-03' is. A tick in a box is what an "
		"auditor is specifically trained to disbelieve, so it is refused.\n\n"
		"`corrective_actions` REPLACES the whole table when given, which is the only "
		"safe semantics for rows addressed by index — a merge would silently reorder "
		"them and close the wrong finding. `add_corrective_actions` appends and "
		"`close_corrective_action` closes one, and both exist so nobody has to resend "
		"every row exactly to change one.\n\n"
		"CANNOT set corrective_actions_closed — close_audit_event does that, and "
		"refuses while any action is open.",
		{
			"audit": _field(_STRING, "The Audit Event docname."),
			"company": _COMPANY,
			"audit_type": _field(_STRING, "New audit type."),
			"auditor": _field(_STRING, "New auditor."),
			"audit_date": _field(_STRING, "New audit date, YYYY-MM-DD."),
			"result": _field(_STRING, "New result."),
			"scope": _field(_STRING, "New scope."),
			"findings": _field(_STRING, "New findings."),
			"corrective_actions": _field(
				{"type": "array", "items": _OBJECT},
				"REPLACES the whole table. Same shape as create_audit_event.",
			),
			"add_corrective_actions": _field(
				{"type": "array", "items": _OBJECT}, "Appends to the table without disturbing it."
			),
			"close_corrective_action": _field(
				_INTEGER, "Close the action at this index, counting from 1. get_audit_event lists them."
			),
			"corrective_action": _field(
				_STRING, "What actually changed. Required when closing one; a tick in a box is refused."
			),
			"closed_date": _field(_STRING, "When it was closed. Defaults to today."),
			"evidence": _field(
				_STRING,
				"What proves it: a photo, a training sign-in sheet, a revised policy's docname. "
				"A Compliance Policy named here is resolved and pulled into audit packets.",
			),
			"attached_report": _field(_STRING, "New file_url for the report."),
			"notes": _field(_STRING, "New notes."),
			"audit_name": _field(_STRING, "Always refused — it is the docname."),
			"corrective_actions_closed": _field(_STRING, "Always refused — see close_audit_event."),
		},
		required=("audit",),
		mutating=True,
		title="Update an audit event",
		available=_needs_doctype("Audit Event"),
		requires="the Audit Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"close_audit_event": _tool(
		evidence.close_audit_event,
		"MUTATING (default OFF). Declare an audit finished, with a mandatory closure "
		"note saying how the closure was accepted — the auditor's confirmation, the "
		"certificate that issued, the re-inspection that passed.\n\n"
		"REFUSES WHILE ANY CORRECTIVE ACTION IS STILL OPEN, naming every one. An "
		"audit marked closed over an open finding is the most misleading thing this "
		"app could record: generate_audit_packet reads that date as 'this audit is "
		"finished', would assemble it into a packet, and the packet would be "
		"contradicted by the auditor's first question — open items are what auditors "
		"look for.\n\n"
		"An audit that raised NO findings at all is closeable; a clean PrimusGFS is a "
		"real event. Supports dry_run.",
		{
			"audit": _field(_STRING, "The Audit Event docname."),
			"closure_note": _field(
				_STRING,
				"How the closure was accepted. Must be a real sentence, not a word.",
			),
			"closed_date": _field(_STRING, "When. Defaults to today; before the audit date is refused."),
			"closed_by": _field(_STRING, "Who. Defaults to the calling user."),
			"company": _COMPANY,
			"dry_run": _field(_BOOLEAN, "Check the gate and write nothing."),
		},
		required=("audit", "closure_note"),
		mutating=True,
		title="Close an audit event",
		available=_needs_doctype("Audit Event"),
		requires="the Audit Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── Wave 3: the kairotic compliance calendar ────────────────────────────
	"get_compliance_calendar": _tool(
		calendar.get_compliance_calendar,
		"WHAT IS DUE AND WHAT IS LATE, worst first, grouped by category. The main "
		"read of the whole compliance framework. Read-only.\n\n"
		"EVERY ALERT IS STATE-DRIVEN, NOT CALENDAR-DRIVEN. Nothing here fires because "
		"a date arrived: a certificate raises an alert when it is genuinely inside "
		"the lead time its own issuing body takes, a block raises one when it is in "
		"active spray rotation AND its agricultural water is untested, an employee "
		"raises one when their I-9 has actually expired. A rule that fired on the "
		"calendar alone would fire on fallow ground and on ground tested last week, "
		"and would be ignored by the third month.\n\n"
		"ALERTS AUTO-DISMISS WHEN THE CONDITION RESOLVES. The water test is done, the "
		"licence is renewed, the inspection happens — and the alert goes away on the "
		"next sweep without anybody switching it off.\n\n"
		"GROUPED SO A WHOLE GROUP CAN BE CLEARED IN ONE AFTERNOON: every housing item "
		"is one walk round the camp, every certificate is one trip to an agency "
		"website. Snoozed alerts are hidden by default and counted; an overdue alert "
		"is NEVER hidden by `days_ahead`, because it was due in the past.\n\n"
		"Reports which rules cannot run on this site at all — an empty category is "
		"not the same as a clean one.",
		{
			"company": _COMPANY,
			"severity_min": _field(
				_STRING, "Critical, Warning or Info. Only this severity and worse. Default Info (everything)."
			),
			"days_ahead": _field(
				_INTEGER,
				"Only alerts due within this many days. Overdue alerts are always shown "
				"regardless. Omit for no horizon.",
			),
			"category": _field(
				_STRING,
				"Certifications, Policies, Workforce, Records, Housing, Water and Sanitation, Spray and "
				"Pesticides, Filings, Audits or Other.",
			),
			"alert_type": _field(_STRING, "One rule's alerts only. list_compliance_rules names them."),
			"regime": _field(
				_STRING,
				"Only alerts that are evidence for ONE audit: FSMA, GAP, GlobalGAP, PrimusGFS, "
				"NOP, OTCO, WPS, OR-OSHA, Internal or Other. This is how a calendar is read one "
				"inspection at a time — 'everything OR-OSHA will ask about in October' is one "
				"afternoon's work and 'everything' is not. Matching is by TAG, never by substring: "
				"'GlobalGAP' contains 'GAP', and a substring match would put another scheme's "
				"findings in front of a USDA GAP auditor. An unrecognised value is REFUSED rather "
				"than ignored, because an empty compliance calendar reads as a clean one. "
				"`Internal` means the operation's own standard — real work with no outside auditor.",
			),
			"include_snoozed": _field(_BOOLEAN, "Show snoozed alerts too. Default false."),
			"include_dismissed": _field(_BOOLEAN, "Show dismissed alerts too. Default false."),
			"as_of": _field(_STRING, "Read the calendar as of this date, YYYY-MM-DD. Defaults to today."),
			"limit": _LIMIT,
		},
		title="Compliance calendar",
		available=_needs_doctype("Compliance Alert"),
		requires="the Compliance Alert DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_compliance_rules": _tool(
		calendar.list_compliance_rules,
		"Every alert rule this app has, with the STATE that makes each one fire "
		"rather than the date — each rule carries a `kairotic_gate` saying exactly "
		"what ripeness means for it — plus the compliance framework it serves and "
		"whether it can run on this site at all. Read-only.\n\n"
		"A rule listed as unavailable raises nothing AND dismisses nothing: an absent "
		"DocType is not evidence that anybody did the work.",
		{},
		title="List compliance rules",
	),
	"get_audit_readiness": _tool(
		calendar.get_audit_readiness,
		"Audit readiness as ONE COMPARABLE NUMBER — resolved alerts over alerts "
		"raised, as a percentage — plus the honesty check on it. Read-only.\n\n"
		"WHY A RATIO RATHER THAN A COUNT. 'Eleven open warnings' is not actionable on "
		"a Tuesday in July, because it means nothing to anybody who does not already "
		"know what normal looks like. A percentage is comparable to yesterday's, "
		"which is the property that makes a number worth putting on a wall.\n\n"
		"IT REPORTS HOW IT WAS EARNED. `resolved_by_hand_percent` splits conditions "
		"that cleared themselves from dismissals somebody made by hand. An operation "
		"at 95% through dismissals is a different operation from one at 95% because "
		"the work got done, and a score that could not tell them apart would be a "
		"score worth gaming. A single open Critical is called out regardless of the "
		"percentage.",
		{
			"company": _COMPANY,
			"as_of": _field(_STRING, "YYYY-MM-DD. Defaults to today."),
		},
		title="Audit readiness score",
		available=_needs_doctype("Compliance Alert"),
		requires="the Compliance Alert DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"refresh_compliance_alerts": _tool(
		calendar.refresh_compliance_alerts,
		"MUTATING (default OFF). Run the whole rule set NOW instead of waiting for "
		"tonight's scheduled sweep. Creates new alerts, refreshes existing ones, "
		"reopens ones whose condition has come back, and AUTO-DISMISSES ones whose "
		"condition has resolved.\n\n"
		"IT TOUCHES NO OPERATIONAL RECORD. Every rule is a read over certificates, "
		"policies, employees, blocks, cabins, filings and audits; the only rows "
		"written are this app's own Compliance Alerts. That is why it is safe to run "
		"at any moment and why the nightly scheduler calls the same function.\n\n"
		"IT CANNOT DUPLICATE AN ALERT. Each alert's docname is derived from its rule "
		"and its source record and from nothing that changes daily, so tonight's "
		"sweep finds and refreshes what last night's wrote — and a snooze somebody "
		"set last week survives.\n\n"
		"A DISMISSAL A PERSON MADE IS NEVER REOPENED. Somebody looked at it and "
		"decided; the sweep does not overrule them by noticing the same thing again. "
		"Supports dry_run.",
		{
			"company": _COMPANY,
			"as_of": _field(_STRING, "Evaluate every rule as of this date, YYYY-MM-DD. Defaults to today."),
			"dry_run": _field(_BOOLEAN, "Report what would change and write nothing. Default false."),
			"regime": _field(
				_STRING,
				"Run ONLY the rules that raise this audit's evidence: FSMA, GAP, GlobalGAP, "
				"PrimusGFS, NOP, OTCO, WPS, OR-OSHA, Internal or Other. For the morning before an "
				"inspection, when re-scanning every block's water is a minute nobody has. A rule "
				"it skips raises nothing AND DISMISSES NOTHING — a narrowed sweep that cleared the "
				"rules it did not run would empty most of the calendar and look like progress — so "
				"the counts it reports are about that regime only. `rules_skipped` names each one.",
			),
		},
		mutating=True,
		idempotent=True,
		title="Refresh compliance alerts",
		available=_needs_doctype("Compliance Alert"),
		requires="the Compliance Alert DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"snooze_alert": _tool(
		calendar.snooze_alert,
		"MUTATING (default OFF). Hide one alert until a date.\n\n"
		"NOT A DISMISSAL. The condition is still true and the alert still exists; it "
		"is hidden from the calendar until the date and reappears ON ITS OWN, without "
		"anybody having to clear a flag. It is the honest way to say 'not this "
		"week'.\n\n"
		"REFUSES a date that is not in the future — a snooze that has already expired "
		"is not a snooze — and refuses to snooze an already-dismissed alert.",
		{
			"alert": _field(_STRING, "The Compliance Alert docname. get_compliance_calendar lists them."),
			"until_date": _field(_STRING, "Hide it until this date, YYYY-MM-DD. Must be in the future."),
			"reason": _field(_STRING, "Why not this week. Optional; recorded on the alert's timeline."),
		},
		required=("alert", "until_date"),
		mutating=True,
		title="Snooze a compliance alert",
		available=_needs_doctype("Compliance Alert"),
		requires="the Compliance Alert DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"dismiss_alert": _tool(
		calendar.dismiss_alert,
		"MUTATING (default OFF). Take one alert off the calendar, with a MANDATORY "
		"reason.\n\n"
		"THE REASON IS THE POINT. It is the only part of this record nobody can "
		"reconstruct — the alert itself the sweep can rebuild from the source record, "
		"but why somebody judged it unnecessary exists nowhere else. It is also the "
		"answer when the same finding turns up next year.\n\n"
		"THE ALERT IS NOT DELETED. The record that somebody looked at this and "
		"decided is itself compliance evidence. And a dismissal a person made is "
		"never reopened by the sweep — if the condition is still true tomorrow night, "
		"the judgement stands.\n\n"
		"DISMISSING AN ALERT CHANGES NOTHING UNDERNEATH IT. Dismissing one about an "
		"expired certificate does not renew the certificate.",
		{
			"alert": _field(_STRING, "The Compliance Alert docname."),
			"reason": _field(
				_STRING,
				"Why this does not need doing. Must be a real explanation, not a word — see the description.",
			),
		},
		required=("alert", "reason"),
		mutating=True,
		title="Dismiss a compliance alert",
		available=_needs_doctype("Compliance Alert"),
		requires="the Compliance Alert DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"dismiss_alert_bulk": _tool(
		calendar.dismiss_alert_bulk,
		"MUTATING (default OFF). Dismiss every alert matching a filter, with one "
		"reason written onto all of them. DRY RUN DEFAULTS TRUE and the first call "
		"writes nothing.\n\n"
		"THE DRY RUN IS NOT POLITENESS. The whole calendar is one filter away: a "
		"`severity` typed where an `alert_type` was meant matches everything, fails "
		"nothing, looks exactly like success, and leaves an operation reading as "
		"compliant while nothing has been fixed. So the first call returns exactly "
		"what would be dismissed, and a second call with dry_run=false and the same "
		"filter does the work.\n\n"
		"REFUSES A CALL WITH NO FILTER at all. Capped at 200 alerts per run: a filter "
		"matching more than that is a filter worth reading again.",
		{
			"reason": _field(_STRING, "Written onto every alert this touches. Must be a real explanation."),
			"alert_type": _field(_STRING, "One rule's alerts. list_compliance_rules names them."),
			"category": _field(
				_STRING,
				"Certifications, Policies, Workforce, Records, Housing, Water and Sanitation, Spray and "
				"Pesticides, Filings, Audits or Other.",
			),
			"severity": _field(
				_STRING, "Critical, Warning or Info — EXACTLY this severity, not 'and worse'."
			),
			"source_doctype": _field(_STRING, "Only alerts whose source is this doctype."),
			"company": _COMPANY,
			"dry_run": _field(
				_BOOLEAN, "DEFAULTS TRUE. Pass false, with the same filter, to actually dismiss."
			),
		},
		required=("reason",),
		mutating=True,
		destructive=True,
		title="Dismiss compliance alerts in bulk",
		available=_needs_doctype("Compliance Alert"),
		requires="the Compliance Alert DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── Wave 4: audit packets ───────────────────────────────────────────────
	"list_audit_packet_types": _tool(
		auditpacket.list_audit_packet_types,
		"Which audit regimes this app can assemble an evidence packet for — FSMA, "
		"GAP, GlobalGAP, OSHA, DOL, EPA, USDA_NIFA and an unscoped Other — with the "
		"sections each one pulls in, what it is scoped to, and which sections will be "
		"EMPTY on this site because the DocType behind them is not installed. "
		"Read-only.\n\n"
		"A section with nothing behind it SAYS SO in the packet rather than being "
		"omitted: an absent traceability section reads as an operation with nothing "
		"to declare, and a section saying the BucketLog bridge is not installed reads "
		"as the truth.",
		{},
		title="List audit packet types",
	),
	"generate_audit_packet": _tool(
		auditpacket.generate_audit_packet,
		"MUTATING (default OFF). Assemble every piece of evidence for one audit type "
		"over one period into a PDF, and file it as a Governance Document in the "
		"company's archive. Returns the file_url and the counts — never the bytes.\n\n"
		"IT PULLS FROM THE OPERATIONAL RECORDS, NOT FROM A COPY. The spray records "
		"ARE the spray logs; the worker facility records ARE the housing register; "
		"the traceability rows ARE the bucket log. Nothing in the packet is a "
		"compliance copy, which is why nothing in it can have drifted from what was "
		"actually done.\n\n"
		"THE KAIROTIC GATE IS A REFUSAL, NOT A WARNING. A packet asserts a compliant "
		"period. It is refused on a period that has not finished, and on one whose "
		"corrective actions are still OPEN — because an open finding inside the "
		"period contradicts the assertion, and a warning at the top of a printed "
		"document is not read by the person the document is handed to. Every open "
		"action is named in the refusal. `allow_open_actions=true` produces it "
		"anyway, with the open items in a section at the FRONT.\n\n"
		"IDEMPOTENT BY (audit_type, company, period, regime): a second call is refused "
		"without overwrite=true, because two packets for one audit period differing "
		"in whatever changed between them is a question nobody wants to be asked.\n\n"
		"IT CARRIES A WORKER TRAINING SECTION (v0.19.0), scoped to the regimes this "
		"audit type is entitled to see — a GAP packet takes GAP and WPS training, an "
		"OSHA packet takes OR-OSHA and WPS, an EPA packet takes WPS alone. One "
		"training session can satisfy several regimes at once and the record carries "
		"every tag it earned, so one afternoon in a shed appears in every packet it "
		"answers and in none it does not. `regime` narrows it further. Records with "
		"no trainee signature or no §112.161(b) supervisor review are DISCLOSED in "
		"the section rather than filtered out of it.\n\n"
		"IT ALSO CARRIES THE OPEN COMPLIANCE-CALENDAR ITEMS (v0.19.2), scoped the "
		"same way, and that is a disclosure rather than a confession: the gate above "
		"has already refused the packet if any corrective action from inside the "
		"period is open, so what is left is forward-looking work — an operation "
		"demonstrating that it knows what it owes, from a list its own records "
		"generated rather than somebody's memory the night before. Snoozed and "
		"dismissed items are excluded; neither is an open obligation.\n\n"
		"PDF by default; DOCX available. Supports dry_run.",
		{
			"audit_type": _field(_STRING, "FSMA, GAP, GlobalGAP, OSHA, DOL, EPA, USDA_NIFA or Other."),
			"company": _COMPANY,
			"regime": _field(
				_STRING,
				"Narrow the WORKER TRAINING and OPEN-ITEMS sections to one scheme: FSMA, GAP, "
				"GlobalGAP, PrimusGFS, NOP, OTCO, WPS, OR-OSHA, Internal or Other. Every other "
				"section is unchanged. Each audit type already pulls its own regimes — a GAP packet takes "
				"GAP and WPS training, an OSHA packet takes OR-OSHA and WPS — so this is for "
				"the buyer who asks for one scheme by name, and for the inspector wearing a "
				"different hat from the packet's title, which in Oregon is the ordinary case: "
				"the same ODA auditor runs a GAP audit one day and an FDA-contracted FSMA "
				"inspection the next. It is PART OF THE IDEMPOTENCE KEY, so a narrowed packet "
				"never silently overwrites the full one.",
			),
			"period_start": _field(_STRING, "First day the packet covers, YYYY-MM-DD."),
			"period_end": _field(
				_STRING, "Last day it covers, YYYY-MM-DD. A period that has not finished is refused."
			),
			"output_format": _field(
				_STRING,
				"'pdf' (default) or 'docx'. PDF is the one to use — a .docx is a file the "
				"recipient may not be able to open.",
			),
			"output_path": _field(
				_STRING,
				"ALSO write it to a path under the site's private/files or public/files. The "
				"attachment is the durable copy either way.",
			),
			"stage_via_chunks": _field(
				_BOOLEAN,
				"Route the assembled bytes through the staging pipeline for a checkpoint. "
				"Defaults on for packets over 2 MB and off below that, where the checkpoint costs "
				"more than the failure it guards against.",
			),
			"allow_open_actions": _field(
				_BOOLEAN,
				"Produce the packet over open corrective actions, disclosing them in a section "
				"at the FRONT. Default false.",
			),
			"overwrite": _field(_BOOLEAN, "Replace an existing packet for the same type and period."),
			"dry_run": _field(_BOOLEAN, "Assemble it, report every count, and write nothing."),
		},
		required=("audit_type", "period_start", "period_end"),
		mutating=True,
		title="Generate an audit packet",
		available=_needs_doctype("Governance Document"),
		requires="the Governance Document DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.15.0: Journal Entry attribution drift ────────────────────────────
	"find_drifted_je_attributions": _tool(
		read.find_drifted_je_attributions,
		"DIAGNOSTIC. Every submitted Journal Entry in a date range whose VOUCHER and "
		"GENERAL LEDGER disagree about who a line belongs to. Read-only.\n\n"
		"THE DAMAGE CLASS IT FINDS. v0.13.0's update_journal_entry_party looked its "
		"GL rows up by `voucher_detail_no == line.name`, which is the Sales Invoice "
		"Item convention and NOT the Journal Entry one. Every call against a "
		"submitted entry therefore matched zero rows, wrote the voucher, and left the "
		"ledger alone — so the voucher says one party and every ageing report, party "
		"ledger and statement of account says another, and nothing in either table "
		"admits to it.\n\n"
		"NOT LIMITED TO THAT. Drift also arrives from a direct database edit, a "
		"restored backup, or a migration that moved one table and not the other. The "
		"scan reads the current state of both and does not care what caused it, which "
		"is why `by_vintage` is reported BESIDE the finding rather than used to "
		"filter it.\n\n"
		"`repair_input` IS THE LIST repair_drifted_je_attributions TAKES VERBATIM. "
		"Lines whose GL rows cannot be matched with certainty are reported separately "
		"as `ambiguous` and are NOT in it — reporting a coin toss as a finding would "
		"be worse than reporting nothing.\n\n"
		"Three queries whatever the range, and matched by the same function the "
		"repair writes through.",
		{
			"from_date": _field(_STRING, "Start of the posting-date range, YYYY-MM-DD."),
			"to_date": _field(_STRING, "End of the posting-date range, YYYY-MM-DD."),
			"company": _COMPANY,
			"limit": _field(_INTEGER, "Maximum entries to scan. Default 500, hard maximum 500."),
			"vintage_from": _field(
				_STRING,
				"Start of the window when YOUR site was running v0.13.0. Defaults to 2026-07-30, "
				"when it shipped upstream.",
			),
			"vintage_to": _field(
				_STRING, "End of that window. Defaults to 2026-07-31, when v0.14.0 fixed it."
			),
		},
		required=("from_date", "to_date"),
		title="Find drifted JE attributions",
	),
	"repair_drifted_je_attributions": _tool(
		mutate.repair_drifted_je_attributions,
		"MUTATING (default OFF). Bring drifted GL Entry rows back into step with "
		"their vouchers, in a batch. Takes find_drifted_je_attributions' "
		"`repair_input` VERBATIM. DRY RUN DEFAULTS TRUE.\n\n"
		"IT REPAIRS IN ONE DIRECTION: every item brings the LEDGER into line with the "
		"VOUCHER. For the v0.13.0 damage class that is right by construction — the "
		"broken tool wrote the voucher and failed to write the ledger, so the voucher "
		"holds the attribution somebody actually intended. If a line drifted for some "
		"other reason and the LEDGER is the correct side, use "
		"update_journal_entry_party on it individually.\n\n"
		"MOVES NO BALANCE, EVER. `party` is an attribution column: every debit, "
		"credit, account and date is untouched, so the trial balance after a repair "
		"of two hundred lines is arithmetically identical to the one before it. That "
		"is what makes a batch write to submitted vouchers defensible at all.\n\n"
		"IT DOES NOT ABORT ON THE FIRST FAILURE. Each item is a different voucher, "
		"and a run that stopped half way would leave the ledger in a state neither "
		"the report before it nor the report after it describes. Every item is "
		"attempted and every outcome is reported per item.\n\n"
		"Capped at 200 items. Requires a `reason`, written onto every entry touched.",
		{
			"repairs": _field(
				{"type": "array", "items": _OBJECT},
				"One object per line: {journal_entry, line_index, party_type, party}. This is "
				"exactly find_drifted_je_attributions' `repair_input` — pass it through.",
			),
			"reason": _field(
				_STRING,
				"Why these attributions are being repaired. Written onto every entry touched; "
				"must be a real explanation.",
			),
			"dry_run": _field(
				_BOOLEAN,
				"DEFAULTS TRUE. Every item is checked against the live voucher and its GL rows, "
				"so an item reported as would_repair is one that will.",
			),
		},
		required=("repairs", "reason"),
		mutating=True,
		title="Repair drifted JE attributions",
	),
	# ── v0.16.0: Farm Task Dispatch ─────────────────────────────────────────
	"list_available_tasks": _tool(
		dispatch.list_available_tasks,
		"THE POOL. Every Farm Task a worker could pick up right now, worst urgency "
		"first, optionally narrowed to a location, a skill or a task type. Pass "
		"worker_id and it also reports how many tasks that person is already "
		"holding and whether they may claim another. Read-only.\n\n"
		"ONLY SELF-PICK AND EITHER TASKS ARE HERE. Dispatched work is deliberately "
		"absent from the pool: somebody has to be SENT to it by name, because that "
		"is how this app marks work where the named licence holder matters.",
		{
			"worker_id": _field(
				_STRING, "Employee id. Adds this worker's claim count and whether they may claim."
			),
			"location": _field(
				_STRING, "Only tasks at this place (a Housing Unit, Field, Zone or Parcel docname)."
			),
			"skill": _field(_STRING, "Only tasks needing this skill, e.g. 'camp_maintenance'."),
			"task_type": _field(
				_STRING,
				"Inspection, Test, Spray, Repair, Harvest, Training, Compliance-Audit, "
				"Housing-Cleanup, Water-Sampling or Other.",
			),
			"urgency": _field(_STRING, "Low, Normal, High or Critical."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		title="List available tasks",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_dispatched_tasks": _tool(
		dispatch.list_dispatched_tasks,
		"What one worker is holding right now — claimed and in progress — with the "
		"full task behind each assignment. Pass include_finished=true or a state for "
		"their history. Read-only.",
		{
			"worker_id": _field(_STRING, "The Employee id. `assigned_to` is an alias."),
			"assigned_to": _field(_STRING, "Alias for worker_id."),
			"state": _field(_STRING, "Claimed, In-Progress, Completed or Rejected."),
			"include_finished": _field(
				_BOOLEAN, "Include completed and rejected assignments. Default false."
			),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		required=("worker_id",),
		title="List a worker's tasks",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_dispatch_board": _tool(
		dispatch.list_dispatch_board,
		"THE KANBAN AS JSON. Every Farm Task grouped into its state column, worst "
		"urgency first, with the pool, the open Critical work and the count of tasks "
		"that came from a compliance alert. Closed states are excluded unless asked "
		"for. Read-only.\n\n"
		"The fraction of the board that came from an alert is the honest measure of "
		"whether the compliance calendar is driving work or being read and ignored.\n\n"
		"The same board renders in the Desk at "
		"`/app/farm-task/view/kanban/Farm Task Dispatch`, where a foreman drags cards "
		"between columns and Frappe writes the state.",
		{
			"company": _COMPANY,
			"state_filter": _field(
				_STRING,
				"One state or a comma-separated list: Draft, Available, Claimed, In-Progress, "
				"Awaiting-Review, Completed, Rejected, Cancelled.",
			),
			"include_closed": _field(_BOOLEAN, "Include Completed, Rejected and Cancelled. Default false."),
			"task_type": _field(_STRING, "Only this task type."),
			"urgency": _field(_STRING, "Only this urgency."),
			"assigned_to": _field(_STRING, "Only tasks with this Employee id on them."),
			"skill_required": _field(_STRING, "Only tasks needing this skill."),
			"limit": _LIMIT,
		},
		title="Dispatch board",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_farm_task": _tool(
		dispatch.get_farm_task,
		"One task in full: its evidence contract in sentences, every assignment it "
		"has ever had with the evidence filed against each, every rejection and the "
		"reason given, the compliance record its completion produced, and the alert "
		"it came from — including whether that alert has since auto-dismissed, which "
		"is the loop visibly closing. Read-only.",
		{"task": _field(_STRING, "The Farm Task docname, e.g. 'FT-2026-07-00012'.")},
		required=("task",),
		title="Get a farm task",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_farm_task": _tool(
		dispatch.create_farm_task,
		"MUTATING (default OFF). Raise one piece of work — its type, where it is, "
		"what skill it needs, how urgent it is, whether it is dispatched or "
		"self-picked, and what record completing it produces.\n\n"
		"`evidence_required` IS MANDATORY AND IS THE POINT OF THE WHOLE DOCTYPE. It "
		'is JSON: {"photos": true, "signature": true, "findings_text": true, '
		'"witness": false}. A task that requires no evidence is a task that gets '
		"closed with a tick in a box, and a tick in a box is what an auditor is "
		"trained to disbelieve. An empty contract is refused; so is a misspelt key, "
		'because `{"photo": true}` asks for nothing and looks like it asks for '
		"something.\n\n"
		"REFUSES: a `creates_record` naming a DocType this site does not have — a "
		"task promising a record nobody can write is a promise that fails in front "
		"of a worker stood in a cabin; a location that does not exist; a "
		"`source_alert` that already has a task, because one alert is one job.\n\n"
		"Tasks are named FT-YYYY-MM-<seq>, so the same annual walk can be raised "
		"every year without colliding with its own history.",
		{
			"task_name": _field(
				_STRING, "What a foreman calls it out loud: 'Habitability walk — MC-Cabin-01'."
			),
			"task_type": _field(
				_STRING,
				"Inspection, Test, Spray, Repair, Harvest, Training, Compliance-Audit, "
				"Housing-Cleanup, Water-Sampling or Other.",
			),
			"evidence_required": _field(
				_OBJECT,
				"JSON object. Keys: photos, signature, findings_text, witness. REQUIRED — at "
				"least one must be true.",
			),
			"location_doctype": _field(
				_STRING, "The register the place is in: Housing Unit, Field, Irrigation Zone or Parcel."
			),
			"location": _field(_STRING, "The docname of the cabin, block, zone or parcel."),
			"company": _COMPANY,
			"skill_required": _field(_STRING, "e.g. 'camp_maintenance', 'applicator_license'."),
			"urgency": _field(_STRING, "Low, Normal, High or Critical. Default Normal."),
			"dispatch_mode": _field(
				_STRING,
				"Either (default), Dispatched (a foreman sends somebody by name) or Self-pick "
				"(workers take it from the pool).",
			),
			"estimated_duration_minutes": _field(_INTEGER, "How long it should take."),
			"creates_record": _field(
				_STRING, "Housing Inspection, Detector Test or Water Test. Refused if the site lacks it."
			),
			"creates_record_data": _field(
				_OBJECT, "JSON template merged under whatever the completion supplies."
			),
			"source_alert": _field(_STRING, "The Compliance Alert this answers, if any."),
			"source_workorder": _field(_STRING, "A work order in another system this task answers."),
			"assigned_to": _field(_STRING, "Dispatch it to this Employee id straight away."),
			"assigned_to_name": _field(_STRING, "Their name, where no HR app can resolve it."),
			"draft": _field(_BOOLEAN, "Hold it in Draft rather than publishing to the pool. Default false."),
			"notes": _field(_STRING, "Instructions: where the key is, which breaker, who to ask."),
		},
		required=("task_name", "task_type", "evidence_required"),
		mutating=True,
		title="Create a farm task",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"assign_farm_task": _tool(
		dispatch.assign_farm_task,
		"MUTATING (default OFF). Send one named person to one task — the foreman's "
		"half of the dual mode, for work where the named holder matters.\n\n"
		"REFUSES to take work off somebody who already holds it unless you pass "
		"reassign=true AND a reason, which is written onto their assignment. 'Taken "
		"off them with no explanation' is a record nobody can defend. Refuses a task "
		"that is already Completed, Rejected or Cancelled — reassigning finished "
		"work rewrites history rather than dispatching anybody.",
		{
			"task": _field(_STRING, "The Farm Task docname."),
			"assigned_to": _field(_STRING, "The Employee id to send."),
			"assigned_to_name": _field(_STRING, "Their name, where no HR app can resolve it."),
			"reassign": _field(_BOOLEAN, "Take it off whoever holds it. Requires `reason`."),
			"reason": _field(_STRING, "Why it is being taken off them. Written onto their assignment."),
		},
		required=("task", "assigned_to"),
		mutating=True,
		title="Assign a farm task",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"claim_farm_task": _tool(
		dispatch.claim_farm_task,
		"MUTATING (default OFF). A worker takes one task from the pool — the "
		"self-pick half of the dual mode, for general labour.\n\n"
		"CAPPED AT THREE CONCURRENT CLAIMS PER WORKER. This is a hoarding limit and "
		"not a productivity one: completing or rejecting a task frees a slot in the "
		"same instant, and the point is that nobody can pull the whole pool onto "
		"their own name and leave a board that looks worked.\n\n"
		"REFUSES a Dispatched task — somebody has to be SENT to that by name, and "
		"self-picking it would put the wrong person on a regulated record. Refuses a "
		"task somebody else already holds, and a Draft that is not in the pool yet. "
		"Returns the evidence the worker will need to close it.",
		{
			"task": _field(_STRING, "The Farm Task docname."),
			"worker_id": _field(_STRING, "The claiming Employee id."),
			"worker_name": _field(_STRING, "Their name, where no HR app can resolve it."),
		},
		required=("task", "worker_id"),
		mutating=True,
		title="Claim a farm task",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"start_farm_task": _tool(
		dispatch.start_farm_task,
		"MUTATING (default OFF). Clock in on one claimed task.\n\n"
		"THIS IS THE CLOCK-IN FOR THE TASK, NOT FOR THE SHIFT. A worker on the clock "
		"all morning did this particular cabin between ten and half past, and that "
		"is what an hour charged to a job has to mean. Starting twice is refused: it "
		"would move the clock-in forward and shorten the hour actually spent.",
		{
			"assignment": _field(_STRING, "The Farm Task Assignment docname. Or pass `task`."),
			"task": _field(_STRING, "The Farm Task docname — its live assignment is used."),
			"worker_id": _field(_STRING, "Checked against who holds it, if given."),
			"started_at": _field(_STRING, "Override the clock-in time. Defaults to now."),
		},
		mutating=True,
		title="Start a farm task",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"complete_farm_task": _tool(
		dispatch.complete_farm_task,
		"MUTATING (default OFF). Finish one task: check the evidence against the "
		"contract, file it, and WRITE THE COMPLIANCE RECORD the task promised — the "
		"actual Housing Inspection, Detector Test or Water Test, with the "
		"photographs on it. That record moves the register forward, and the alert "
		"that asked for the work auto-dismisses on the next sweep because its "
		"condition is no longer true. Nothing here touches an alert directly, and "
		"nothing needs to.\n\n"
		"REFUSES A SUBMISSION THAT DOES NOT MEET THE EVIDENCE CONTRACT, naming each "
		"requirement that is short. This is the refusal the whole doctype exists "
		"for. Note the findings_text rule: PASS AN EMPTY STRING to record that "
		"nothing was wrong — a clean inspection is a positive statement, and leaving "
		"the argument out records that nobody was asked.\n\n"
		"REFUSES a completion filed by anybody other than the worker holding the "
		"task. A completion by somebody who was not there is not a chain of "
		"custody, it is a rumour, and it is the first thing an auditor pulls on.\n\n"
		"LANDS IN Awaiting-Review WHEN THE RECORD FOUND SOMETHING — a water stain, a "
		"dead detector, a coliform count. The work IS done and the register IS "
		"updated; what needs a person is the finding, and a Critical alert now "
		"stands against the record. A clean completion goes straight to Completed, "
		"because routing clean work through a review queue is how a review queue "
		"stops being read.\n\n"
		"IDEMPOTENT SINCE v0.20.1. An identical resubmission — same assignment, "
		"same worker, same evidence, same words, same completed_at — returns the "
		"completion already on record with `x_idempotent: true` and writes "
		"nothing: no second compliance record, no duplicated evidence rows. A "
		"resubmission that DIFFERS in any of those is still refused. The client "
		"that cannot know whether its request arrived is the case this exists "
		"for; two genuinely different completions of one task is not.",
		{
			"assignment": _field(_STRING, "The Farm Task Assignment docname. Or pass `task`."),
			"task": _field(_STRING, "The Farm Task docname — its live assignment is used."),
			"worker_id": _field(_STRING, "The worker completing it. Must be the one holding the task."),
			"evidence_files": _field(
				{"type": "array", "items": {"type": ["string", "object"]}},
				"File docnames from commit_staged_file, file URLs, or objects like "
				'{"file": "...", "evidence_type": "Photo", "caption": "north wall"}. Max 40.',
			),
			"signature_file": _field(_STRING, "The signature capture's file URL or File docname."),
			"completion_narrative": _field(_STRING, "What the worker did, in their words."),
			"findings_text": _field(
				_STRING,
				"What was WRONG. Pass an empty string to record that nothing was — that is how a "
				"clean inspection is stated, and it satisfies a findings_text requirement.",
			),
			"witness": _field(_STRING, "Somebody else who was there, where the contract asks."),
			"actual_duration_minutes": _field(
				_INTEGER, "Minutes spent. Computed from the clock-in where both times exist."
			),
			"completed_at": _field(_STRING, "Override the clock-out time. Defaults to now."),
			"record_data": _field(
				_OBJECT,
				"Extra fields for the compliance record — a laboratory's results, a detector's "
				"pass/fail, the irrigation zone a sample came from. Merged over the task's own "
				"`creates_record_data`.",
			),
			"visit_id": _field(
				_STRING,
				"The trip this completion belongs to. One identifier reused across every task "
				"closed on one walk to one place; list_visits reports the rollup. Free text, "
				"unvalidated, and not part of what makes a resubmission identical.",
			),
		},
		required=("worker_id",),
		mutating=True,
		title="Complete a farm task",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"reject_farm_task": _tool(
		dispatch.reject_farm_task,
		"MUTATING (default OFF). Hand one task back with a MANDATORY reason, and "
		"return it to the pool.\n\n"
		"REJECTION IS A FIRST-CLASS STATE AND THE REASON IS THE POINT. It is what "
		"turns 'nobody got to it and dispatch never followed up' — the answer nobody "
		"can defend — into 'the ladder is broken and I could not reach the detector', "
		"which is a fact somebody can act on. The rejected assignment STAYS on the "
		"record: it is the proof somebody was sent, went, and could not do it, which "
		"answers an auditor in a way an absence never does.",
		{
			"assignment": _field(_STRING, "The Farm Task Assignment docname. Or pass `task`."),
			"task": _field(_STRING, "The Farm Task docname — its live assignment is used."),
			"worker_id": _field(_STRING, "Checked against who holds it, if given."),
			"reason": _field(_STRING, "Why it could not be done. Mandatory."),
			"cancel": _field(
				_BOOLEAN,
				"Cancel the task instead of returning it to the pool — for work that turned out "
				"not to need doing at all. Default false.",
			),
		},
		required=("reason",),
		mutating=True,
		title="Reject a farm task",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"generate_tasks_from_compliance_alerts": _tool(
		dispatch.generate_tasks_from_compliance_alerts,
		"MUTATING (default OFF). THE BRIDGE. Turns every open Compliance Alert into "
		"a dispatchable Farm Task carrying the evidence its completion has to "
		"produce. This is the tool that makes the compliance calendar actionable "
		"rather than merely readable.\n\n"
		"Each alert type maps to the SHAPE of work it actually is: a habitability "
		"walk is Self-pick general labour needing photos, a signature and findings; "
		"an I-9 re-verification is Dispatched, because the named holder matters. "
		"Urgency follows severity — Critical becomes High, Warning becomes Normal, "
		"Info becomes Low — deliberately not the identity mapping, because a board "
		"where everything is Critical is a board nobody reads.\n\n"
		"IDEMPOTENT BY CONSTRUCTION. A task carries the alert that produced it, so a "
		"second run finds what the first raised and skips it. Re-running after "
		"fixing half the camp raises tasks only for the half still outstanding.\n\n"
		"An alert type with no recipe is REPORTED BY NAME rather than turned into a "
		"generic task: a task with a made-up evidence contract produces a compliance "
		"record nobody can rely on.\n\n"
		"`dry_run` defaults FALSE, unlike dismiss_alert_bulk. The failure mode here "
		"is too many idempotent tasks on a board, not an operation reading as "
		"compliant while nothing was fixed.",
		{
			"company": _COMPANY,
			"dry_run": _field(
				_BOOLEAN, "Report what would be raised without writing anything. Default FALSE."
			),
			"alert_types": _field(
				{"type": "array", "items": _STRING},
				"Only these rules, e.g. ['housing_inspection_overdue']. A comma-separated string "
				"is accepted. Omit for all. list_compliance_rules has the names.",
			),
			"limit": _field(_INTEGER, "Most alerts to consider in one call. Default and maximum 500."),
		},
		mutating=True,
		title="Generate tasks from compliance alerts",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.16.0: the compliance records a completion produces ───────────────
	"list_housing_inspections": _tool(
		inspections.list_housing_inspections,
		"Every habitability walk, newest first, with the ones that FOUND SOMETHING "
		"and nobody has closed named separately — that list is the one worth acting "
		"on, because an operation is judged on closing findings rather than on "
		"having none. Drafts are counted and named too: a draft writes nothing to "
		"the register and dismisses no alert. Read-only.",
		{
			"unit": _field(_STRING, "Only walks of this Housing Unit. `subject` is an alias."),
			"subject": _field(_STRING, "Alias for unit."),
			"state": _field(_STRING, "Draft, Recorded or Corrective Action Required."),
			"from_date": _field(_STRING, "Earliest inspection date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest inspection date, YYYY-MM-DD."),
			"source_task_only": _field(_BOOLEAN, "Only walks produced by completing a Farm Task."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		title="List housing inspections",
		available=_needs_doctype("Housing Inspection"),
		requires="the Housing Inspection DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_housing_inspection": _tool(
		inspections.get_housing_inspection,
		"One walk in full: findings, corrective action, every photograph and the "
		"signature, plus the unit's whole inspection history and — where this one "
		"found something — the later clean walk that superseded it. Read-only.",
		{
			"record": _field(_STRING, "The Housing Inspection docname, e.g. 'HI-2026-07-00003'."),
			"housing_inspection": _field(_STRING, "Alias for record."),
		},
		title="Get a housing inspection",
		available=_needs_doctype("Housing Inspection"),
		requires="the Housing Inspection DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_housing_inspection": _tool(
		inspections.create_housing_inspection,
		"MUTATING (default OFF). Record one habitability walk, and move the unit's "
		"`last_habitability_inspection` forward — which is the whole mechanism by "
		"which doing the work takes `housing_inspection_overdue` off the calendar.\n\n"
		"THE STATE IS COMPUTED FROM THE FINDINGS, NEVER CHOSEN. Blank findings means "
		"the walk was clean and the record is Recorded. Anything written in the "
		"findings routes it to Corrective Action Required and raises a Critical "
		"alert that persists until the finding is closed or a later clean walk of "
		"the same unit supersedes it. Somebody who has typed 'water stain, north "
		"wall' is not offered the option of marking it passed.\n\n"
		"IT ONLY EVER MOVES THE DATE FORWARD. A back-dated walk is filed as evidence "
		"and does not move a register that already knows about something later — "
		"that would re-raise an alert about work which has since been done.",
		{
			"unit": _field(_STRING, "The Housing Unit that was walked."),
			"inspection_date": _field(
				_STRING, "The day somebody was stood in it, YYYY-MM-DD. Defaults to today."
			),
			"inspector": _field(_STRING, "The Employee id of whoever walked it."),
			"inspector_name": _field(_STRING, "Their name, where no HR app can resolve it."),
			"findings": _field(
				_STRING,
				"WHAT WAS WRONG. Leave it out or empty for a clean walk — that is a positive "
				"statement, and it is what keeps the record in Recorded.",
			),
			"corrective_action": _field(_STRING, "What is going to be done about it, and by when."),
			"signature": _field(_STRING, "The inspector's signature capture: a file URL or File docname."),
			"photos": _field(
				{"type": "array", "items": {"type": ["string", "object"]}},
				"File docnames, file URLs, or objects with a type and a caption. Max 40.",
			),
			"source_task": _field(_STRING, "The Farm Task this came from, if any."),
			"keep_as_draft": _field(
				_BOOLEAN,
				"Hold it in Draft — a walk started in a cabin with no signal and finished in the "
				"evening. A Draft writes nothing to the unit. Default false.",
			),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
			"company": _COMPANY,
		},
		required=("unit",),
		mutating=True,
		title="Record a housing inspection",
		available=_needs_doctype("Housing Inspection"),
		requires="the Housing Inspection DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_housing_inspection": _tool(
		inspections.update_housing_inspection,
		"MUTATING (default OFF). Correct or close one walk: findings, corrective "
		"action, signature, extra photographs, and the closure. Every change is "
		"echoed as before → after, and the state is recomputed from the findings.\n\n"
		"CLOSING A FINDING REQUIRES A CLOSURE NOTE saying what was actually done. A "
		"date with nothing beside it is what an auditor is trained to disbelieve.",
		{
			"record": _field(_STRING, "The Housing Inspection docname."),
			"housing_inspection": _field(_STRING, "Alias for record."),
			"findings": _field(
				_STRING, "New findings. An empty string clears them and returns it to Recorded."
			),
			"corrective_action": _field(_STRING, "What is being done about it."),
			"corrective_action_closed": _field(_STRING, "The day it was fixed, YYYY-MM-DD."),
			"closure_note": _field(_STRING, "What was done. Required with a closure date."),
			"signature": _field(_STRING, "The signature capture."),
			"inspection_date": _field(_STRING, "Correct the date, YYYY-MM-DD."),
			"photos": _field(
				{"type": "array", "items": {"type": ["string", "object"]}}, "Photographs to ADD."
			),
			"keep_as_draft": _field(_BOOLEAN, "Clear this to publish a draft."),
			"notes": _field(_STRING, "New notes."),
		},
		mutating=True,
		title="Update a housing inspection",
		available=_needs_doctype("Housing Inspection"),
		requires="the Housing Inspection DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_detector_tests": _tool(
		inspections.list_detector_tests,
		"Every smoke and CO detector test, newest first, with the failures and the "
		"buildings that have no detector at all named as open findings. Read-only.",
		{
			"unit": _field(_STRING, "Only tests of this Housing Unit. `subject` is an alias."),
			"subject": _field(_STRING, "Alias for unit."),
			"state": _field(_STRING, "Draft, Recorded or Corrective Action Required."),
			"from_date": _field(_STRING, "Earliest test date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest test date, YYYY-MM-DD."),
			"source_task_only": _field(_BOOLEAN, "Only tests produced by completing a Farm Task."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		title="List detector tests",
		available=_needs_doctype("Detector Test"),
		requires="the Detector Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_detector_test": _tool(
		inspections.get_detector_test,
		"One test in full, with its photographs, the building's whole testing "
		"history and — where this one failed — the later clean test that superseded "
		"it. Read-only.",
		{
			"record": _field(_STRING, "The Detector Test docname, e.g. 'DT-2026-07-00004'."),
			"detector_test": _field(_STRING, "Alias for record."),
		},
		title="Get a detector test",
		available=_needs_doctype("Detector Test"),
		requires="the Detector Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_detector_test": _tool(
		inspections.create_detector_test,
		"MUTATING (default OFF). Record one smoke and CO detector test and move the "
		"unit's detector dates forward, which is what takes "
		"`housing_detector_test_stale` off the calendar.\n\n"
		"A FAILED TEST STILL WRITES THE DATE, and that is deliberate: the stale "
		"alert asks whether anybody KNOWS the detector works, and a Fail answers it. "
		"The answer is bad, so the record routes to Corrective Action Required and "
		"raises a Critical alert of its own — but the ignorance is over.\n\n"
		"NOT PRESENT WRITES NO DATE, for the mirror reason. There is nothing to have "
		"tested, so the stale alert goes on saying so — and a building somebody "
		"sleeps in with no CO detector is the most dangerous state this app records.\n\n"
		"WHERE A REPLACEMENT IS NEEDED IT RAISES A FARM TASK to go and fit one. "
		"'Replacement needed' as a checkbox with nobody dispatched against it is a "
		"finding that stays true until next year's test discovers it again.",
		{
			"unit": _field(_STRING, "The Housing Unit whose detectors were tested."),
			"test_date": _field(_STRING, "The day the button was pressed, YYYY-MM-DD. Defaults to today."),
			"tester": _field(_STRING, "The Employee id of whoever tested it."),
			"tester_name": _field(_STRING, "Their name, where no HR app can resolve it."),
			"smoke_detector_result": _field(_STRING, "Pass, Fail or Not Present. Default Pass."),
			"co_detector_result": _field(_STRING, "Pass, Fail or Not Present. Default Pass."),
			"replacement_needed": _field(
				_BOOLEAN,
				"Set for you on any Fail or Not Present. Set it by hand for a detector that "
				"passed but is past its service life — most are rated ten years.",
			),
			"findings": _field(_STRING, "Anything else seen while testing."),
			"photos": _field(
				{"type": "array", "items": {"type": ["string", "object"]}}, "Photographs. Max 40."
			),
			"source_task": _field(_STRING, "The Farm Task this came from, if any."),
			"keep_as_draft": _field(_BOOLEAN, "Hold it in Draft. Default false."),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
			"company": _COMPANY,
		},
		required=("unit",),
		mutating=True,
		title="Record a detector test",
		available=_needs_doctype("Detector Test"),
		requires="the Detector Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_detector_test": _tool(
		inspections.update_detector_test,
		"MUTATING (default OFF). Correct one detector test or close its fault: "
		"results, replacement flag, findings, extra photographs and the closure. "
		"Closing requires a note saying what was fitted or repaired.",
		{
			"record": _field(_STRING, "The Detector Test docname."),
			"detector_test": _field(_STRING, "Alias for record."),
			"smoke_detector_result": _field(_STRING, "Pass, Fail or Not Present."),
			"co_detector_result": _field(_STRING, "Pass, Fail or Not Present."),
			"replacement_needed": _field(_BOOLEAN, "New replacement flag."),
			"findings": _field(_STRING, "New findings."),
			"corrective_action_closed": _field(_STRING, "The day it was fixed, YYYY-MM-DD."),
			"closure_note": _field(_STRING, "What was fitted or repaired. Required with a closure date."),
			"test_date": _field(_STRING, "Correct the date, YYYY-MM-DD."),
			"photos": _field(
				{"type": "array", "items": {"type": ["string", "object"]}}, "Photographs to ADD."
			),
			"keep_as_draft": _field(_BOOLEAN, "Clear this to publish a draft."),
			"notes": _field(_STRING, "New notes."),
		},
		mutating=True,
		title="Update a detector test",
		available=_needs_doctype("Detector Test"),
		requires="the Detector Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_water_tests": _tool(
		inspections.list_water_tests,
		"Every agricultural water sample, newest first, with the contaminated ones "
		"nobody has resolved named. Read-only.",
		{
			"source": _field(_STRING, "Only samples from this Irrigation Zone. `subject` is an alias."),
			"subject": _field(_STRING, "Alias for source."),
			"state": _field(_STRING, "Draft, Recorded or Corrective Action Required."),
			"from_date": _field(_STRING, "Earliest sample date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest sample date, YYYY-MM-DD."),
			"source_task_only": _field(_BOOLEAN, "Only samples produced by completing a Farm Task."),
			"company": _COMPANY,
			"limit": _LIMIT,
		},
		title="List water tests",
		available=_needs_doctype("Water Test"),
		requires="the Water Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_water_test": _tool(
		inspections.get_water_test,
		"One sample in full: the laboratory, the results, the report, the sampling "
		"photographs, the zone's whole testing history and — where this one was "
		"dirty — the later clean sample that superseded it. Read-only.",
		{
			"record": _field(_STRING, "The Water Test docname, e.g. 'WT-2026-07-00002'."),
			"water_test": _field(_STRING, "Alias for record."),
		},
		title="Get a water test",
		available=_needs_doctype("Water Test"),
		requires="the Water Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_water_test": _tool(
		inspections.create_water_test,
		"MUTATING (default OFF). Record one agricultural water sample and move BOTH "
		"the zone's and the parent block's `water_test_last_date` forward. Both, "
		"because the sample came out of the zone but `water_test_stale` reads the "
		"BLOCK — Subpart E is engaged by water contacting a crop, and the crop is on "
		"the block.\n\n"
		"RESULTS ARE READ BOTH WAYS, because a laboratory says the same thing eight "
		"ways: words first ('Absent', 'Present', '<1'), then any number, where "
		"anything above zero is a detection and generic E. coli is compared against "
		"the FSMA 112.44(b) criterion of 126 CFU/100 mL.\n\n"
		"AN UNREADABLE RESULT IS NOT A CLEAN RESULT. Where neither reading works the "
		"record routes to Corrective Action Required and somebody has to go and look "
		"at the report. Treating an uninterpretable result as a pass is how a "
		"compliance file becomes a clean record of nothing.\n\n"
		"DRAFT IS THE NORMAL FIRST STATE HERE: a sample is taken on Monday and "
		"answered on Thursday. Use keep_as_draft, then file the answer with "
		"update_water_test.",
		{
			"source": _field(_STRING, "The Irrigation Zone the sample came from."),
			"test_date": _field(
				_STRING,
				"The day the sample was TAKEN, YYYY-MM-DD — which is what Subpart E's ninety days "
				"count back from, not the day the laboratory answered. Defaults to today.",
			),
			"tester": _field(_STRING, "The Employee id of whoever took it."),
			"tester_name": _field(_STRING, "Their name, where no HR app can resolve it."),
			"laboratory": _field(_STRING, "Who did the analysis."),
			"lab_sample_id": _field(_STRING, "The laboratory's own sample identifier."),
			"sample_collected_on": _field(_STRING, "Datetime the sample was collected."),
			"lab_reported_on": _field(_STRING, "The day the laboratory answered, YYYY-MM-DD."),
			"coliform_result": _field(_STRING, "Total coliform, as the laboratory wrote it."),
			"ecoli_result": _field(_STRING, "Generic E. coli, as the laboratory wrote it."),
			"lab_report": _field(_STRING, "The laboratory's own report: a file URL or File docname."),
			"findings": _field(_STRING, "Anything else about the sample or the source."),
			"sample_photos": _field(
				{"type": "array", "items": {"type": ["string", "object"]}},
				"Photographs of the sampling point. Max 40.",
			),
			"source_task": _field(_STRING, "The Farm Task this came from, if any."),
			"farm_location_gps": _field(
				_STRING,
				'WHERE the sample was drawn — "45.5152,-122.6784" or a place name like '
				'"North standpipe". The zone says which water; §112.161(a)(1)(i) also asks '
				"where somebody stood. Optional.",
			),
			"keep_as_draft": _field(
				_BOOLEAN, "Hold it in Draft until the laboratory answers. Default false."
			),
			"notes": _field(_STRING, "Anything the fields cannot hold."),
			"company": _COMPANY,
		},
		required=("source",),
		mutating=True,
		title="Record a water test",
		available=_needs_doctype("Water Test"),
		requires="the Water Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"update_water_test": _tool(
		inspections.update_water_test,
		"MUTATING (default OFF). File the laboratory's answer against a sample taken "
		"days earlier, or close a contamination finding. THIS IS WHAT THE DRAFT "
		"STATE IS FOR: a sample and its result are one record, and filing the answer "
		"as a second one would produce two rows about one sample whose only "
		"difference is which was typed second.\n\n"
		"Clearing keep_as_draft is what publishes it, recomputes the state from the "
		"results, and moves both registers forward.",
		{
			"record": _field(_STRING, "The Water Test docname."),
			"water_test": _field(_STRING, "Alias for record."),
			"laboratory": _field(_STRING, "Who did the analysis."),
			"lab_sample_id": _field(_STRING, "The laboratory's own identifier."),
			"lab_reported_on": _field(_STRING, "The day they answered, YYYY-MM-DD."),
			"coliform_result": _field(_STRING, "Total coliform, as written."),
			"ecoli_result": _field(_STRING, "Generic E. coli, as written."),
			"lab_report": _field(_STRING, "The report itself."),
			"findings": _field(_STRING, "New findings."),
			"corrective_action_closed": _field(_STRING, "The day the water was dealt with, YYYY-MM-DD."),
			"closure_note": _field(
				_STRING,
				"What was done — treated, source switched, line flushed. Required with a closure date.",
			),
			"test_date": _field(_STRING, "Correct the sample date, YYYY-MM-DD."),
			"sample_photos": _field(
				{"type": "array", "items": {"type": ["string", "object"]}}, "Photographs to ADD."
			),
			"keep_as_draft": _field(
				_BOOLEAN, "Clear this to publish the record once the laboratory has answered."
			),
			"notes": _field(_STRING, "New notes."),
		},
		mutating=True,
		title="Update a water test",
		available=_needs_doctype("Water Test"),
		requires="the Water Test DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.17.0: multi-entity scoping, mobile auth, Tailscale Funnel ────────
	"list_mobile_users": _tool(
		mobile.list_mobile_users,
		"EVERY MOBILE ACCOUNT AND WHAT IS WRONG WITH IT. Who has a phone, which of "
		"the six roles they hold, which entities their User Permissions actually "
		"allow, how old their credential is, and — the part worth reading — a "
		"`concerns` list per account. Also returns the role catalogue, so a client "
		"can show what each role is for without a second call. Read-only.\n\n"
		"THE CONCERNS ARE THE POINT. Each one is a state that looks fine on a list "
		"and is not: an account with NO Company User Permission (which in Frappe "
		"means it sees EVERY entity), a grant that says one set of entities while "
		"the live permissions say another, an account marked Revoked whose token "
		"still works, a credential past its review date. entity_access is read from "
		"the LIVE permission rows, so a scoping somebody changed in the Desk shows "
		"as drift rather than agreeing with a stale record.",
		{
			"role": _field(
				_STRING,
				"Only this role: Field Worker, Foreman, Compliance Officer, Farm Manager, "
				"Family Member or Advisor.",
			),
			"company": _field(_STRING, "Only accounts whose entity access includes this Company."),
			"state": _field(_STRING, "Active, Expired or Revoked."),
			"include_revoked": _field(
				_BOOLEAN, "Include revoked accounts. Default false — they are history, not roster."
			),
		},
		title="List mobile users",
		available=_needs_doctype("Mobile Access Grant"),
		requires="the Mobile Access Grant DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_current_user_context": _tool(
		mobile.get_current_user_context,
		"WHO IS CALLING, AND WHAT THEY MAY SEE. The mobile app's first call after "
		"enrolment: the user, their mobile roles, the entities their User "
		"Permissions allow, which entity to open on, and plain-language `can` / "
		"`cannot` lists for the account screen. Read-only.\n\n"
		"THE IDENTITY COMES FROM THE REQUEST, NOT FROM AN ARGUMENT. A client "
		"identifies itself by sending `Authorization: token <api_key>:<api_secret>` "
		"ALONGSIDE the X-MCP-Token header; this reports whichever Frappe user that "
		"authenticated. A request that authenticates as one person and passes "
		"`user` naming another is REFUSED — an account that can name somebody else "
		"in a request body is not scoped to anything. With no per-user credential "
		"at all (an operator's desktop client), `user` is accepted, and "
		"`identity_source` always says which of the two happened.\n\n"
		"An empty entity list means UNRESTRICTED in Frappe, not 'nothing', and the "
		"result says so where it happens.",
		{
			"user": _field(
				_STRING,
				"Only honoured when the request carries no per-user credential. Refused when it "
				"does and names somebody else.",
			)
		},
		title="Current user context",
	),
	"list_my_tasks": _tool(
		fieldwork.list_my_tasks,
		"WHAT THE CALLER IS HOLDING RIGHT NOW — claimed and in progress — with the "
		"full job behind each one and a `next` block naming the tool each is "
		"waiting for, so a screen can draw the right button without owning the "
		"rule. The worker is resolved from the authenticated request through their "
		"Employee record; the phone never has to know it is EMP-0042. Read-only.\n\n"
		"A login with no Employee record is REFUSED BY NAME rather than answered "
		"with an empty list — 'nothing to do today' is a different and much worse "
		"answer than 'your login is not linked to your employee record'.",
		{
			"state_filter": _field(_STRING, "Claimed, In-Progress, Completed or Rejected."),
			"include_finished": _field(_BOOLEAN, "Include completed and rejected. Default false."),
			"company": _field(_STRING, "One of the caller's entities. Defaults to their preferred one."),
			"user": _field(_STRING, "Only when the request carries no per-user credential."),
			"limit": _LIMIT,
		},
		title="My tasks",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_available_for_me": _tool(
		fieldwork.list_available_for_me,
		"THE POOL THE CALLER COULD TAKE FROM, worst urgency first, with how many "
		"claims they have left. Scoped to their entities; optionally narrowed to a "
		"location. Read-only.\n\n"
		"IT IS HONEST ABOUT SKILLS. Nothing on a Frappe site records what skills a "
		"worker HAS — there is no register on Employee, in this app or in Frappe "
		"HR. So `skill` filters if you pass it, a site that has added its own "
		"skills field to Employee is read, and otherwise THE WHOLE POOL COMES BACK "
		"AND `skill_matching` SAYS SO. Guessing from a job title was the "
		"alternative, and hiding a spraying task from somebody because their title "
		"said 'Harvest Crew' would be hiding work with no way to tell.",
		{
			"location_filter": _field(
				_STRING, "Only tasks at this place (a Housing Unit, Field, Zone or Parcel docname)."
			),
			"skill": _field(_STRING, "Only tasks needing this skill, e.g. 'camp_maintenance'."),
			"task_type": _field(_STRING, "Inspection, Test, Spray, Repair, Harvest, Training, and so on."),
			"urgency": _field(_STRING, "Low, Normal, High or Critical."),
			"company": _field(_STRING, "One of the caller's entities. Defaults to their preferred one."),
			"user": _field(_STRING, "Only when the request carries no per-user credential."),
			"limit": _LIMIT,
		},
		title="Pool for me",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_task_with_evidence_contract": _tool(
		fieldwork.get_task_with_evidence_contract,
		"ONE TASK SHAPED FOR A SCREEN: the job, and its evidence contract as a "
		"CHECKLIST — each requirement with what it means in a worker's words, "
		"whether it is already satisfied, and which part of the app collects it "
		"(camera, signature pad, text field). Plus `next`, naming the tool this "
		"task is waiting for. Read-only.\n\n"
		"Same facts get_farm_task returns; different reader. get_farm_task answers "
		"an auditor, this answers a phone. A task belonging to an entity the caller "
		"has no access to is refused by name rather than returned.",
		{
			"task_name": _field(_STRING, "The Farm Task docname, e.g. 'FT-2026-07-00012'."),
			"task": _field(_STRING, "Alias for task_name."),
			"user": _field(_STRING, "Only when the request carries no per-user credential."),
		},
		title="Task with evidence contract",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_compliance_calendar_for_me": _tool(
		fieldwork.list_compliance_calendar_for_me,
		"THE COMPLIANCE CALENDAR, NARROWED TO THE ENTITIES THE CALLER MAY SEE — one "
		"call per entity, merged, Critical first, with each alert stamped with the "
		"company it came from. Read-only.\n\n"
		"THE SCOPING IS EXPLICIT AND HAS TO BE. This app reads through "
		"`frappe.db.get_all`, which does not consult User Permissions, so asking "
		"the calendar once with no company would return every entity on the site. "
		"An account with NO Company User Permission is refused rather than shown "
		"everything under a name like this one.\n\n"
		"An entity whose calendar could not be read is named in `failed_entities` "
		"rather than silently contributing nothing — a clean calendar and an unread "
		"one look identical otherwise.",
		{
			"company": _field(_STRING, "Just this one entity, instead of all of the caller's."),
			"severity_min": _field(_STRING, "Info, Warning or Critical. Default Info."),
			"days_ahead": _field(
				_INTEGER, "Only what is due inside this many days. Overdue is never hidden."
			),
			"category": _field(_STRING, "One alert category."),
			"alert_type": _field(_STRING, "One rule, e.g. 'certification_expiring'."),
			"regime": _field(
				_STRING,
				"Only alerts that are evidence for one audit: FSMA, GAP, GlobalGAP, PrimusGFS, "
				"NOP, OTCO, WPS, OR-OSHA, Internal or Other. Applied to every entity the caller "
				"can see, and refused by name if it is not one this app knows.",
			),
			"as_of": _field(_STRING, "Evaluate as of this date, YYYY-MM-DD. Defaults to today."),
			"user": _field(_STRING, "Only when the request carries no per-user credential."),
			"limit": _LIMIT,
		},
		title="My compliance calendar",
		available=_needs_doctype("Compliance Alert"),
		requires="the Compliance Alert DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"validate_public_endpoint": _tool(
		funnel.validate_public_endpoint,
		"IS THIS SITE ACTUALLY REACHABLE FROM THE INTERNET? Opens a TLS connection "
		"to the public hostname, reads the certificate (issuer, expiry, SANs), "
		"POSTs a real MCP `tools/list` to the endpoint and reports the status, the "
		"latency and a verdict with a next step. Read-only.\n\n"
		"IT PROBES UNAUTHENTICATED BY DEFAULT, AND A 401 IS THE BEST RESULT: it "
		"proves the path is reachable, the certificate is valid, and the token gate "
		"is holding. `authenticate=true` proves the whole round trip and REFUSES "
		"any URL that is not this site's own configured public_url — a tool that "
		"will POST your bearer token to a hostname in its arguments is a tool that "
		"exfiltrates it.\n\n"
		"The reachable targets are the configured public_url and hosts under "
		"`.ts.net`, over HTTPS, base URL only, redirects not followed. This makes "
		"an outbound request from inside the site's network, which is the shape of "
		"every server-side request forgery there has ever been.",
		{
			"url": _field(
				_STRING,
				"The base URL to probe, e.g. https://umbrel.tail1234.ts.net. Defaults to "
				"`public_url` from ERPNext MCP Settings. No path, no query.",
			),
			"authenticate": _field(
				_BOOLEAN,
				"Send this site's own X-MCP-Token, proving the whole round trip. Only allowed "
				"against the configured public_url. Default false.",
			),
			"timeout_seconds": _field(_INTEGER, "1–30. Default 8."),
		},
		title="Validate public endpoint",
	),
	"get_tailscale_funnel_config": _tool(
		funnel.get_tailscale_funnel_config,
		"WHAT THIS MACHINE THINKS IT IS SERVING: the Funnel ports and URLs, the "
		"serve config, the node's own tailnet DNS name, and whether the configured "
		"public_url matches any of it. Read-only.\n\n"
		"IT DEGRADES INSTEAD OF FAILING. A containerised Frappe worker normally has "
		"neither the `tailscale` binary nor the host's tailscaled socket — that is "
		"the EXPECTED state on an Umbrel and it is not a fault, because Funnel "
		"forwards to the port nginx already serves and needs no cooperation from "
		"this process. When it cannot read the config it says which of the two "
		"situations it is in (no Tailscale at all, versus Tailscale on the host and "
		"invisible from in here) and points at validate_public_endpoint, which asks "
		"from outside and needs none of this.\n\n"
		"NOTHING IN THIS APP CAN TURN FUNNEL ON OR OFF, and nothing will. Changing "
		"what is reachable from the entire internet is an operator decision made "
		"deliberately. The commands are in the README.",
		{"timeout_seconds": _field(_INTEGER, "1–20. Default 8.")},
		title="Tailscale Funnel config",
	),
	"create_mobile_user": _tool(
		mobile.create_mobile_user,
		"MUTATING (default OFF). One call for what four Desk forms do in ten "
		"minutes: the User, one of the six mobile roles, a Company User Permission "
		"per entity, the Mobile Access Grant, and the API credential — WHICH IS "
		"READABLE IN THE RESULT EXACTLY ONCE.\n\n"
		"`entity_access` IS MANDATORY AND THERE IS NO OVERRIDE. In Frappe a user "
		"with NO User Permission on Company sees EVERY company on the site, so an "
		"account created without entities would be the LEAST scoped account here, "
		"not the most — which in a release about scoping is the one mistake that "
		"must be impossible.\n\n"
		"THE ROLE SAYS WHAT KIND OF WORK; THE USER PERMISSIONS SAY WHOSE. That is "
		"why no company name appears in any role definition, and why the same "
		"Foreman role serves the operating company and the holding company.\n\n"
		"REFUSES an existing User unless update_existing=true — re-running this on "
		"a live account rewrites its roles and its scoping, which is a decision "
		"rather than a retry. To only re-issue a credential, use generate_api_token.\n\n"
		"It will NOT grant permissions on doctypes other apps own (Employee, "
		"Company). It assigns the owning app's own role where the site has it and "
		"says so where it does not — writing a Custom DocPerm on somebody else's "
		"doctype would make Frappe ignore every standard permission on it, for "
		"every role on the site.",
		{
			"email": _field(_STRING, "The address the account signs in with, and its docname."),
			"full_name": _field(
				_STRING,
				"Their name. Required for a new account — a dispatch board and an evidence "
				"record both name the person.",
			),
			"role": _field(
				_STRING,
				"One of: Field Worker, Foreman, Compliance Officer, Farm Manager, Family Member, "
				"Advisor. list_mobile_users returns what each is for and what each cannot do.",
			),
			"entity_access": _field(
				{"type": "array", "items": _STRING},
				"The Companies this account may see. REQUIRED, at least one. Names or "
				"abbreviations; a comma-separated string is accepted too.",
			),
			"preferred_company": _field(
				_STRING, "Which entity the app opens on. Must be in entity_access. Defaults to the first."
			),
			"token_expiry_days": _field(
				_INTEGER,
				"When the credential should be REVIEWED, in days. Default 120. Not an expiry — "
				"Frappe API secrets do not expire on their own and this app installs no job "
				"that revokes one.",
			),
			"generate_token": _field(
				_BOOLEAN,
				"Issue an API credential now. DEFAULTS TRUE FOR A NEW ACCOUNT (one with no token "
				"cannot sign in) and FALSE FOR AN UPDATE (that person has a phone in their "
				"pocket, and re-scoping them should not silently invalidate it). Pass it "
				"explicitly to override either.",
			),
			"update_existing": _field(
				_BOOLEAN, "Rewrite the roles and scoping of an account that already exists. Default false."
			),
			"notes": _field(_STRING, "Anything about this account somebody will want in six months."),
			"url": _field(_STRING, "The public base URL to record on the grant. Defaults to public_url."),
		},
		required=("email", "role", "entity_access"),
		mutating=True,
		title="Create a mobile user",
		available=_needs_doctype("Mobile Access Grant"),
		requires="the Mobile Access Grant DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_employee": _tool(
		employee.create_employee,
		"MUTATING (default OFF). One Employee record — the register every Farm Ops "
		"method scopes work by.\n\n"
		"THIS IS THE RECORD THAT MAKES A LOGIN A PERSON. `list_my_tasks` and the "
		"other ten mobile methods answer for an EMPLOYEE, not for a User: a task "
		"board is a list of what somebody is assigned, and the Employee is the only "
		"thing on a Frappe site that says which somebody a login is. An account with "
		"no Employee behind it enrols perfectly and then gets refused, correctly, "
		"with 'set user_id on their Employee record to this email address'.\n\n"
		"IT WRITES FOURTEEN FIELDS AND REFUSES EVERYTHING ELSE BY NAME. Identity and "
		"assignment only: who this person is, which entity hired them, what they do, "
		"when they started, how to reach them. Payroll, tax and banking fields — "
		"salary structure, income tax slab, bank account, CTC — are refused with "
		"their own message, because each has a form, an approval and a retention "
		"rule this app knows nothing about, and a tool that set `ctc` because it "
		"appeared in a sentence would be a tool that quietly moved money.\n\n"
		"THE SCHEMA IS ASKED, NEVER ASSUMED. Every Link is checked against this "
		"site's own records (Department, Designation, Employment Type, Gender), "
		"every Select against this site's own options, and a field this site's "
		"Employee doctype does not carry is REPORTED rather than silently dropped. "
		"If Frappe HR here marks a field mandatory — stock installs require `gender` "
		"and `date_of_birth` — the refusal names it rather than passing a controller "
		"traceback back.\n\n"
		"IT REFUSES A SECOND RECORD for the same name at the same company, naming "
		"the one that exists. Two Employee records for one person puts them on the "
		"dispatch board twice and in the payroll register once, and is far easier to "
		"make than to find. `allow_duplicate_name=true` covers two real people with "
		"one name.\n\n"
		"Requires System Manager, HR Manager, HR User or Farm Manager on the account "
		"this app acts as, and refuses a company that account cannot see.",
		{
			"employee_name": _field(
				_STRING,
				"Their name, first and last. An I-9, a payroll register and a dispatch board "
				"all name the same person; one word names nobody findable.",
			),
			"company": _field(_STRING, "The hiring entity. Name or abbreviation."),
			"first_name": _field(_STRING, "Defaults to the first token of employee_name."),
			"last_name": _field(_STRING, "Defaults to the last token of employee_name."),
			"date_of_joining": _field(_STRING, "YYYY-MM-DD. Defaults to today."),
			"date_of_birth": _field(
				_STRING, "YYYY-MM-DD. Mandatory on a stock Frappe HR; the refusal says so."
			),
			"gender": _field(
				_STRING,
				"Must be a Gender record on this site. Mandatory on a stock Frappe HR. The "
				"refusal lists what this site has.",
			),
			"department": _field(_STRING, "Must be a Department on this site."),
			"designation": _field(_STRING, "Their job title. Must be a Designation on this site."),
			"employment_type": _field(
				_STRING,
				"Full-time, Part-time, Seasonal Worker — whatever this site's Employment Type "
				"records are. The refusal lists them.",
			),
			"status": _field(_STRING, "Active (default), Inactive, Suspended or Left."),
			"user_id": _field(
				_STRING,
				"The login this person signs in with. REFUSED if it is already another "
				"Employee's login, and refused if the account has no Farm Ops role and no "
				"Mobile Access Grant — a link that changes nothing today would silently grant "
				"a task board the day somebody grants that account a role for an unrelated "
				"reason. allow_unenrolled_user=true is the deliberate escape.",
			),
			"personal_email": _field(_STRING, "A personal address for the record. Not the login."),
			"cell_number": _field(_STRING, "A mobile number for the Employee record."),
			"allow_unenrolled_user": _field(
				_BOOLEAN,
				"Link a user_id that has no Farm Ops role or grant yet. Default false. Right "
				"when you are deliberately creating the Employee ahead of the enrolment.",
			),
			"allow_duplicate_name": _field(
				_BOOLEAN,
				"Create a second Employee with a name this company already has. Default false. "
				"For two real people with one name.",
			),
		},
		required=("employee_name", "company"),
		mutating=True,
		title="Create an employee",
		available=_needs_doctype("Employee"),
		requires="the Employee DocType, which Frappe HR ships",
	),
	"update_employee": _tool(
		employee.update_employee,
		"MUTATING (default OFF). Change the identity and assignment fields on an "
		"Employee that already exists — the same fourteen create_employee writes, "
		"and nothing else.\n\n"
		"THE COMMON USE IS `user_id` ON A RECORD THAT WAS MADE WITHOUT ONE, which is "
		"what stands between a working mobile credential and a task board. "
		"link_employee_to_user does that one job with the linkage checks spelled "
		"out; this is the tool when several fields change at once.\n\n"
		"PAYROLL, TAX AND BANKING FIELDS ARE REFUSED BY NAME — salary structure, "
		"income tax slab, bank account, CTC. Those need the HR module's own form, "
		"where its approvals and retention rules run. A field that is real on this "
		"site but outside the fourteen gets a different refusal from a field that "
		"does not exist at all, because they are different mistakes.\n\n"
		"IT REPORTS WHAT ACTUALLY CHANGED, field by field, with the previous value — "
		"a value that was already what you asked for lands in `unchanged` rather "
		"than being reported as a write that did not happen.\n\n"
		"RE-POINTING AN EXISTING LINK NEEDS `replace_user=true`: moving an Employee "
		"to a different login moves that person's whole task history with it, which "
		"is a decision rather than a retry.\n\n"
		"Requires System Manager, HR Manager, HR User or Farm Manager, and refuses "
		"an employee whose company that account cannot see.",
		{
			"name": _field(
				_STRING,
				"The Employee. Accepts its docname, employee number, employee name, or the "
				"login already linked to it.",
			),
			"employee": _field(_STRING, "Alias for name."),
			"employee_name": _field(_STRING, "Their name, first and last."),
			"company": _field(_STRING, "Move them to another entity. Must be one you can see."),
			"first_name": _field(_STRING, "Given name."),
			"last_name": _field(_STRING, "Family name."),
			"date_of_joining": _field(_STRING, "YYYY-MM-DD."),
			"date_of_birth": _field(_STRING, "YYYY-MM-DD."),
			"gender": _field(_STRING, "Must be a Gender record on this site."),
			"department": _field(_STRING, "Must be a Department on this site."),
			"designation": _field(_STRING, "Must be a Designation on this site."),
			"employment_type": _field(_STRING, "Must be an Employment Type on this site."),
			"status": _field(
				_STRING,
				"Active, Inactive, Suspended or Left. The mobile methods answer for Active employees.",
			),
			"user_id": _field(
				_STRING,
				"The login. Refused if it belongs to another Employee; needs replace_user=true "
				"to displace one already on this record.",
			),
			"personal_email": _field(_STRING, "A personal address. Not the login."),
			"cell_number": _field(_STRING, "A mobile number."),
			"replace_user": _field(
				_BOOLEAN, "Re-point an Employee that is already linked to a different login. Default false."
			),
			"allow_unenrolled_user": _field(
				_BOOLEAN, "Accept a user_id with no Farm Ops role or grant yet. Default false."
			),
		},
		required=(),
		mutating=True,
		idempotent=True,
		title="Update an employee",
		available=_needs_doctype("Employee"),
		requires="the Employee DocType, which Frappe HR ships",
	),
	"link_employee_to_user": _tool(
		employee.link_employee_to_user,
		"MUTATING (default OFF). Point one Employee at one login — the single field "
		"that turns a working mobile credential into a working task board.\n\n"
		"CALL THIS WHEN A PHONE ENROLS AND THEN SHOWS NOTHING. Every Farm Ops method "
		"resolves the caller to an Employee through `Employee.user_id` and refuses "
		"an account it cannot resolve, with 'set user_id on their Employee record to "
		"this email address'. This is that setting.\n\n"
		"IT REPORTS WHETHER THE PHONE WILL NOW WORK, not merely whether the field "
		"was written. `linkage.farm_ops_ready` is true only when the account holds a "
		"Farm Ops role AND its Mobile Access Grant is Active AND the Employee is "
		"Active; when it is false the note says which of the three is missing and "
		"which tool fixes it.\n\n"
		"ONE PERSON, ONE LOGIN. Refused if the User already belongs to another "
		"Employee — two records naming one login gives list_my_tasks two answers "
		"where it needs one. Refused if this Employee already has a different login, "
		"unless replace_user=true. IDEMPOTENT when the link already says exactly "
		"what you asked for: it reports 'already linked' and writes nothing.\n\n"
		"REFUSED FOR A USER WITH NO FARM OPS ROLE AND NO GRANT, because the link "
		"would change nothing today and silently grant a task board on the day "
		"somebody grants that account a role for an unrelated reason. Run "
		"create_mobile_user first, or pass allow_unenrolled_user=true to link ahead "
		"of the enrolment deliberately.",
		{
			"employee_name": _field(
				_STRING,
				"The Employee. Accepts its docname, employee number, employee name, or a login "
				"already linked to it.",
			),
			"employee": _field(_STRING, "Alias for employee_name."),
			"name": _field(_STRING, "Alias for employee_name."),
			"user_id": _field(_STRING, "The login this person signs in with."),
			"replace_user": _field(
				_BOOLEAN,
				"Displace a different login already on this Employee. Default false — it moves "
				"that person's whole task history.",
			),
			"allow_unenrolled_user": _field(
				_BOOLEAN,
				"Link an account that has no Farm Ops role and no Mobile Access Grant yet. Default false.",
			),
		},
		required=("user_id",),
		mutating=True,
		idempotent=True,
		title="Link an employee to a login",
		available=_needs_doctype("Employee"),
		requires="the Employee DocType, which Frappe HR ships",
	),
	"onboard_employee": _tool(
		newhire.onboard_employee,
		"MUTATING (default OFF). One call for a new hire, in the only order that "
		"works: the Employee record, their paperwork filed privately ON THAT "
		"RECORD, a scoped login with a credential, THE LINK BETWEEN THE TWO, "
		"optionally the login QR, and optionally the two first-day tasks nobody "
		"skips.\n\n"
		"v0.18.1 ADDED THE LINK STEP AND FIXED THE ORDER. Until then this created "
		"the Employee with `user_id` already filled in and THEN created the User — "
		"and `Employee.user_id` is a Link, so Frappe refused the very first step of "
		"any onboarding that named an email. Employee, then login, then link.\n\n"
		"IT IS IDEMPOTENT. A second run with the same arguments finds the Employee "
		"(by login, then by name and hiring company), finds the account, finds the "
		"link already made, and reports each as reused. Nothing is duplicated. The "
		"one thing a re-run does differently is mint a fresh QR, and only when "
		"asked — issuing a credential twice is the point of asking twice.\n\n"
		"THE PAPERWORK GOES ON THE EMPLOYEE, NOT IN THE GOVERNANCE ARCHIVE, and "
		"that is the whole reason this tool exists rather than a checklist. An "
		"I-9, a W-4 and a photograph of somebody's licence are PERSONAL records "
		"about one person; attach_governance_document files into the register that "
		"holds trust instruments, operating agreements and SOPs — the documents "
		"describing the BUSINESS — which an auditor, an advisor and a family "
		"member browse. Filing forty people's immigration paperwork there would "
		"look tidy and be a disclosure. Every document here goes through "
		"attach_file_to_document(doctype='Employee') as a PRIVATE attachment, and "
		"is_private is not an argument.\n\n"
		"IT REUSES AN EXISTING EMPLOYEE rather than making a second one. Two "
		"Employee records for one person puts them on the dispatch board twice and "
		"in the payroll register once, and is far easier to make than to find.\n\n"
		"IT STOPS AT THE FIRST REAL REFUSAL AND REPORTS WHAT IT ALREADY DID, step "
		"by step. Re-running is safe: each underlying tool is idempotent about the "
		"thing it owns. The first-day tasks are the one step allowed to fail "
		"quietly — a training task that could not be raised must not undo an "
		"onboarding that worked, so it lands in `skipped` for somebody to raise by "
		"hand.\n\n"
		"THE PLAINTEXT CREDENTIAL IS NOT REPEATED IN THIS RESULT. create_mobile_user "
		"returns a secret exactly once; echoing it into an orchestrator's summary "
		"would put a live credential in a second, much more pasteable place. "
		"`issue_qr=true` returns the scannable PNG — which encodes the same secret, "
		"unavoidably, because that is what enrolment by QR IS — and still not the "
		"decoded payload, because nobody pastes a PNG into a chat window by "
		"accident.",
		{
			"full_name": _field(
				_STRING,
				"Their name, first and last. An I-9, a payroll register and a dispatch board "
				"all name the same person; one word names nobody findable.",
			),
			"company": _field(_STRING, "The hiring entity. Name or abbreviation."),
			"email": _field(
				_STRING,
				"The address they sign in with. Optional — without it the Employee is created "
				"and no login is, which is right for somebody who will never hold a phone.",
			),
			"employee": _field(
				_STRING, "An existing Employee docname to onboard against instead of creating one."
			),
			"role": _field(
				_STRING,
				"Their mobile role. Default 'Field Worker'. One of the six — list_mobile_users "
				"returns what each is for.",
			),
			"entity_access": _field(
				{"type": "array", "items": _STRING},
				"The Companies the login may see. Defaults to the hiring company alone, which "
				"is the right default and the scoped one.",
			),
			"preferred_company": _field(_STRING, "Which entity the app opens on. Defaults to company."),
			"documents": _field(
				{"type": "object"},
				"The paperwork, keyed by kind: i9, w4, photo_id, direct_deposit, signed_offer. "
				'Each value is {"file_name": "...", "file_content": "<base64>"} or '
				'{"file_url": "..."}. All filed PRIVATELY on the Employee record.',
			),
			"date_of_joining": _field(_STRING, "YYYY-MM-DD. Defaults to today."),
			"date_of_birth": _field(_STRING, "YYYY-MM-DD."),
			"designation": _field(_STRING, "Their job title."),
			"gender": _field(_STRING, "As the Employee doctype records it."),
			"phone": _field(_STRING, "A mobile number for the Employee record."),
			"housing_unit": _field(
				_STRING,
				"The Housing Unit they are moving into. Used to point the camp-orientation "
				"task at the right cabin.",
			),
			"first_day_tasks": _field(
				_BOOLEAN,
				"Raise safety training and camp orientation as Farm Tasks. Default false. Both "
				"demand a signature — 'I was told about the machinery' is a claim whose whole "
				"value is that somebody attested to it.",
			),
			"update_existing": _field(
				_BOOLEAN,
				"Let the login step rewrite an account that already exists. Default false, and "
				"the refusal is create_mobile_user's.",
			),
			"issue_qr": _field(
				_BOOLEAN,
				"Return the login QR as a base64 PNG in this same result. DEFAULT FALSE, and "
				"deliberately: minting a QR rotates the account's API secret, so a default-true "
				"would mean re-running an onboarding to add a W-4 silently knocked a phone "
				"already in somebody's pocket offline.",
			),
			"url": _field(
				_STRING,
				"The public base URL to encode on the QR. Defaults to public_url on ERPNext MCP "
				"Settings. Only read when issue_qr is true.",
			),
		},
		required=("full_name", "company"),
		mutating=True,
		title="Onboard a new employee",
		available=_needs_doctype("Employee"),
		requires="the Employee DocType, which Frappe HR ships",
	),
	# ── v0.19.0: the training register ──────────────────────────────────────
	"record_training": _tool(
		training.record_training,
		"MUTATING (default OFF). File one training event for one person, tagged with "
		"every audit it answers.\n\n"
		"ONE AFTERNOON, FOUR AUDITS. WPS wants pesticide training every twelve "
		"months (40 CFR 170.401/.501). Oregon's heat rule wants it annually before "
		"the first shift at 80 °F (OAR 437-004-1131). FSMA Subpart C wants food "
		"safety on hiring and periodically (§112.21–.30). USDA GAP wants a worker "
		"health and hygiene log with the signature attached. A single session "
		"covering all of it satisfies all of it — so `regimes` is a TAG LIST and one "
		"record appears in every packet it earned. Filing it four times produces four "
		"records that disagree by August.\n\n"
		"`content_topics_covered` IS REQUIRED, and that is what makes a tag "
		"defensible rather than optimistic: Oregon's heat rule names six topics that "
		"must be covered annually, and a record claiming OR-OSHA without them is a "
		"record an inspector will disallow. 'Heat, water, shade, symptoms, reporting, "
		"emergency response' is a curriculum; 'safety meeting' is not.\n\n"
		"NO `expires_date` MEANS ONE-TIME, and the compliance calendar will never ask "
		"for it to be renewed. Right for a new-hire orientation or a PSA grower "
		"certificate; WRONG for WPS, heat illness or annual GAP hygiene — the result "
		"says so when you leave it empty.\n\n"
		"IT WRITES THE §112.161 FIELDS AT THE TIME: the trainee's signature, the farm "
		"name snapshotted onto the record itself, and a date-and-time activity stamp. "
		"The SUPERVISOR review is a separate tool on purpose — §112.161(b) asks for a "
		"review 'within a reasonable time AFTER the record is made', and a call that "
		"took both signatures at one instant would produce records an inspector reads "
		"as assembled rather than kept. Whatever is still missing is REPORTED rather "
		"than refused: a training that happened and was recorded imperfectly is "
		"better evidence than no record at all.\n\n"
		"A renewal ADDS a record and never edits one — last year's card is the "
		"evidence about last year — and the result names every earlier record it "
		"supersedes so nobody deletes them to tidy up.\n\n"
		"Requires System Manager, HR Manager, HR User or Farm Manager on the account "
		"this app acts as, and refuses an employee whose company that account cannot "
		"see.",
		{
			"employee": _field(
				_STRING,
				"Who was trained. Accepts an Employee docname, employee number, employee name, "
				"or the login linked to them.",
			),
			"training_type": _field(
				_STRING,
				"What it was, in the words the certificate or sign-in sheet uses: 'PSA Grower "
				"Training', 'WPS Handler Training', 'Heat Illness Prevention', 'OSHA 30', "
				"'Applicator License Renewal'. Accepts an existing Training Type OR free text: a "
				"course this site has not run before is CREATED as a Training Type rather than "
				"refused, tagged with the regimes its name implies, and the result says so. "
				"Matching is case- and space-insensitive, so 'wps handler training' finds the "
				"existing curriculum instead of splitting its history across two masters.",
			),
			"completed_date": _field(
				_STRING,
				"The day it was delivered, YYYY-MM-DD. A future date is refused — §112.161(a)(2) "
				"requires a record created at the time of the activity.",
			),
			"regimes": _field(
				_STRING_ARRAY,
				"Which audits this counts towards. One or more of FSMA, GAP, GlobalGAP, "
				"PrimusGFS, NOP, OTCO, WPS, OR-OSHA, Internal, Other. A comma-separated string is "
				"accepted too. These describe THIS SESSION, not the curriculum: a heat session "
				"that ran out of time before the emergency-response topic did not satisfy the "
				"heat rule that day, and the record is entitled to say so. "
				"REQUIRED: an untagged record appears in no packet. A near-miss ('OSHA' for "
				"'OR-OSHA') is refused rather than corrected, because it would file the evidence "
				"where nothing looks for it.",
			),
			"content_topics_covered": _field(
				_STRING_ARRAY,
				"What was actually covered. A comma-separated string is accepted too. REQUIRED — "
				"see the description.",
			),
			"expires_date": _field(
				_STRING,
				"When it stops counting, YYYY-MM-DD. LEAVE EMPTY FOR ONE-TIME TRAINING. Twelve "
				"months out for WPS and for Oregon heat illness.",
			),
			"provider": _field(
				_STRING, "The instructor or organisation: 'Produce Safety Alliance', 'OSU Extension'."
			),
			"training_source": _field(_STRING, "Internal (default), External, Contractor or Online-Course."),
			"completed_time": _field(
				_STRING,
				"Time of day, HH:MM. Optional, and worth having: §112.161(a)(1)(v) asks for date "
				"AND time of the activity.",
			),
			"certificate_file": _field(
				_STRING,
				"The certificate, card or sign-in sheet, as a File docname or file_url. Upload it "
				"with attach_file_to_document or stage_file_chunk first.",
			),
			"person_performed_signature": _field(
				_STRING,
				"The TRAINEE's signature file, captured at completion. §112.161(a)(4), and one "
				"of the standard GAP section failures when it is missing.",
			),
			"company": _field(
				_STRING,
				"The employing entity. Defaults to the Employee's own company, and a mismatch is "
				"refused rather than reconciled.",
			),
			"notes": _field(
				_STRING,
				"Language delivered in, assessment result, who else was in the room — anything an "
				"auditor will ask about in two years.",
			),
		},
		required=("employee", "training_type", "completed_date", "regimes", "content_topics_covered"),
		mutating=True,
		title="Record a training",
		available=_needs_doctype("Employee Training Record"),
		requires="the Employee Training Record DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_trainings": _tool(
		training.list_trainings,
		"The training register: who was taught what, when, under which regimes, and "
		"what has lapsed. Read-only.\n\n"
		"`regime` IS THE AUDIT FILTER. 'Every NOP training record for the last five "
		"years' and 'every WPS record for 2026' are the two questions an audit packet "
		"is assembled from, and both are one call. Matching is by TAG, never by "
		"substring — 'GlobalGAP' contains 'GAP', and a substring match would hand a "
		"USDA GAP auditor evidence from a different scheme.\n\n"
		"`status` AND `expiring_within_days` ARE COMPUTED AS OF TODAY from the expiry "
		"date, not read off a stored column: a record last saved in March holds "
		"March's answer, and filtering on it would report the lapsed set as current. "
		"The same reason the compliance calendar reads dates rather than statuses.\n\n"
		"IT REPORTS WHAT IS MISSING. `without_supervisor_review` is the FSMA "
		"§112.161(b) gap — the element a GAP-only operation most often lacks and "
		"which FDA cites even where the training itself was fine — and "
		"`without_trainee_signature` is §112.161(a)(4). `by_regime` counts the "
		"selection per tag, which is what tells you whether a packet will be empty "
		"before you generate it.\n\n"
		"Scoped to the companies the calling account may actually reach.",
		{
			"company": _COMPANY,
			"employee": _field(_STRING, "One person. Docname, employee number, name, or their login."),
			"regime": _field(
				_STRING,
				"FSMA, GAP, GlobalGAP, PrimusGFS, NOP, WPS, OR-OSHA or Other. The response also "
				"returns what each one means and the rule behind it.",
			),
			"status": _field(
				_STRING,
				"Active, Expiring (inside 90 days) or Expired — as of TODAY. One-time training "
				"with no expiry is always Active.",
			),
			"expiring_within_days": _field(
				_INTEGER,
				"Only training expiring inside this many days. 0 gives the already-lapsed and "
				"anything expiring today.",
			),
			"unreviewed_only": _field(
				_BOOLEAN,
				"Only records with no §112.161(b) supervisor review. The worklist for closing "
				"the gap FDA cites most.",
			),
			"from_date": _field(_STRING, "Earliest completion date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest completion date, YYYY-MM-DD."),
			"limit": _field(_INTEGER, "Maximum records. Default 100, hard maximum 500."),
		},
		title="List trainings",
		available=_needs_doctype("Employee Training Record"),
		requires="the Employee Training Record DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_training": _tool(
		training.get_training,
		"One training record in full, with that person's whole training history "
		"beside it. Read-only.\n\n"
		"IT ANSWERS THE RETENTION QUESTION, which nothing else on the site does. A "
		"record tagged NOP is a FIVE-year record (7 CFR 205.103(b)(4)); one tagged "
		"OR-OSHA is three; FSMA and WPS are two (21 CFR 112.164(a)(1) and 40 CFR "
		"170.309). Where a record carries several tags the LONGEST governs, because "
		"destroying it at two years would destroy the five-year evidence. The "
		"citation comes back with the number.\n\n"
		"IT LISTS THE §112.161 ELEMENTS THIS RECORD LACKS, in the rule's own terms, "
		"rather than fixing them: a signature added now would be a signature dated "
		"now, and a record assembled before an inspection is what an inspector is "
		"trained to spot.\n\n"
		"`superseded_by` names later records of the same kind for the same person. "
		"They do NOT make this one deletable — an auditor asking about last season "
		"wants the row that was true last season.",
		{
			"name": _field(_STRING, "The Employee Training Record docname, e.g. ETR-2026-07-00003."),
			"training": _field(_STRING, "Alias for name."),
			"record": _field(_STRING, "Alias for name."),
		},
		title="Get a training record",
		available=_needs_doctype("Employee Training Record"),
		requires="the Employee Training Record DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"sign_training_supervisor_review": _tool(
		training.sign_training_supervisor_review,
		"MUTATING (default OFF). Record the FSMA §112.161(b) supervisor review on one "
		"training record.\n\n"
		"THIS IS THE GAP A GAP-ONLY OPERATION HAS. §112.161(b) requires worker "
		"training records — among others — to be reviewed, dated and signed by a "
		"supervisor or responsible party within a reasonable time after the record is "
		"made. USDA GAP does not ask for it, so an operation with an otherwise "
		"immaculate GAP binder fails on it, and FDA writes it up even where the "
		"underlying training was fine. It is the single most common FSMA finding "
		"against farms whose actual practice is sound.\n\n"
		"A SEPARATE CALL FROM record_training, DELIBERATELY. The rule's own phrase is "
		"'after the record is made' — a sequence, not a form field. A tool that took "
		"both signatures at once would make simultaneous timestamps the default, and "
		"simultaneous timestamps are the shape of a record an inspector reads as "
		"assembled rather than kept. The result reports the LAG and says so when it "
		"is long.\n\n"
		"IT REFUSES a self-review, a supervisor from another entity, a review dated "
		"before the training it reviews, and — without replace_reviewer=true — "
		"overwriting a signature already on the record.",
		{
			"name": _field(_STRING, "The Employee Training Record docname."),
			"training": _field(_STRING, "Alias for name."),
			"record": _field(_STRING, "Alias for name."),
			"supervisor": _field(
				_STRING,
				"Who reviewed it. Docname, employee number, name or login. Cannot be the trainee, "
				"and cannot belong to another company.",
			),
			"reviewed_on": _field(
				_STRING,
				"When the review happened, YYYY-MM-DD HH:MM:SS. Defaults to now. Earlier than the "
				"training itself is refused.",
			),
			"supervisor_signature": _field(
				_STRING,
				"The signature file, as a File docname or file_url — upload it through "
				"stage_file_chunk first. Without it the review is recorded by name and date only, "
				"and the result says so.",
			),
			"replace_reviewer": _field(
				_BOOLEAN,
				"Overwrite a review already on this record. Default false — replacing a signature "
				"on a compliance record is a decision rather than a retry.",
			),
		},
		required=("supervisor",),
		mutating=True,
		title="Sign a training supervisor review",
		available=_needs_doctype("Employee Training Record"),
		requires="the Employee Training Record DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.19.3: the shift, and the heat record anchored to it ──────────────
	"start_shift": _tool(
		shifts.start_shift,
		"MUTATING (default OFF). Form a crew at a place and start the exposure "
		"period every compliance question about it is asked against.\n\n"
		"THE SHIFT IS THE ANCHOR, and that is what this release is for. A task "
		"completion carries a point-in-time reading; a shift carries a timeline. "
		"Oregon OSHA does not ask what the temperature was when one job closed — it "
		"asks whether the July 15 shift complied with OAR 437-004-1131 from start to "
		"finish, and only a record spanning the exposure period can answer.\n\n"
		"THE FOREMAN FORMS THE CREW. There is no clock-in tool here and there will "
		"not be one: -1131 puts the water, shade, rest-cycle and observation "
		"obligations on a NAMED responsible person, and FSMA §112.161(b) asks that "
		"person to sign. A crew of thirty each clocking themselves in is a shift "
		"with nobody responsible for the record, and the observable failure is that "
		"nobody logged the water break because everybody assumed somebody else had.\n\n"
		"PER-WORKER ATTENDANCE IS NOT LOST TO THIS. Every crew row carries its own "
		"joined_at and left_at, and `end_shift` writes one Attendance record per "
		"person for their OWN span — not the shift's.\n\n"
		"`farm_location_gps` IS THE WEATHER ANCHOR. v0.19.4 asks Open-Meteo what the "
		"conditions are at that place every fifteen minutes while the shift is open; "
		"a shift with no coordinates gets no timeline, and the result says so.\n\n"
		"Requires System Manager, HR Manager, HR User or Farm Manager, and refuses a "
		"foreman or a crew member whose company the calling account cannot see.",
		{
			"foreman": _field(
				_STRING,
				"The supervisor forming this crew and answerable for it. Docname, employee "
				"number, name, or their login. NOT optional — a shift with nobody's name on it "
				"answers neither -1131 nor §112.161(b).",
			),
			"location": _field(
				_STRING,
				"Where the work is: a block name, a camp name, a shop. Free text, because a "
				"shift can be at a place this site has no record of and refusing until somebody "
				"creates a master is how a compliance record stops being written.",
			),
			"shift_type": _field(
				_STRING,
				"Spray, Harvest, Prune, Irrigation, Housing Work, Detector Test Round, "
				"Maintenance or General (the default). It is what decides which regime's "
				"timeline matters — a Spray shift is read against wind speed and a Harvest "
				"shift against heat index.",
			),
			"farm_location_gps": _field(
				_STRING,
				"Latitude,longitude or a place name — '45.52,-122.68'. The weather anchor. "
				"Same spelling Farm Task Assignment has used since v0.19.1.",
			),
			"crew_employees": _field(
				_STRING_ARRAY,
				"Who is on the crew at the start. Docnames, employee numbers, names or logins; "
				"a comma-separated string is accepted too, and so is a list of objects with "
				"`employee`, `role` and `joined_at`. Their joined_at defaults to the SHIFT'S "
				"OWN START rather than to now — everybody rostered at the beginning was there "
				"at the beginning, and stamping them with the moment the call landed would "
				"shave minutes off every one of their days. Late arrivals go through "
				"add_worker_to_shift. Maximum 60.",
			),
			"start_datetime": _field(_STRING, "When the crew started, YYYY-MM-DD HH:MM:SS. Defaults to now."),
			"company": _field(
				_STRING,
				"The entity whose crew this is. Defaults to the foreman's own company, and a "
				"mismatch is refused rather than reconciled.",
			),
		},
		required=("foreman",),
		mutating=True,
		title="Start a crew shift",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"add_worker_to_shift": _tool(
		shifts.add_worker_to_shift,
		"MUTATING (default OFF). Roster somebody onto a shift already running — a "
		"late arrival, or a transfer off another block.\n\n"
		"`joined_at` DEFAULTS TO NOW, which is the opposite of start_shift's default "
		"and right for the same reason: a worker rostered at the beginning was there "
		"at the beginning, and a worker added mid-shift arrived when somebody said "
		"so. Their Attendance record on close spans from here, not from the shift's "
		"start.\n\n"
		"REFUSES A SECOND ROW FOR SOMEBODY ALREADY ON THE CREW. Two rows look "
		"deliberate on the form and become two Attendance days for one person when "
		"the shift closes — somebody who left and came back is one row spanning "
		"both. Also refuses a closed shift: the Attendance rows are already written, "
		"so a crew row added afterwards would be a person with no payroll day.\n\n"
		"Where the shift has already crossed the 80 °F heat index, the result says "
		"so — somebody arriving mid-shift has had none of the morning's water breaks "
		"and none of the crew's acclimatization, and OAR 437-004-1131(g) is about "
		"exactly that person.",
		{
			"shift": _field(_STRING, "The Farm Shift docname, e.g. SHIFT-2026-0001."),
			"name": _field(_STRING, "Alias for shift."),
			"employee": _field(_STRING, "Who is joining. Docname, employee number, name or login."),
			"role": _field(
				_STRING,
				"Worker (the default), Lead Worker or Trainee. Trainee is the one that carries "
				"a legal consequence — -1131(g) asks for an acclimatization plan for workers "
				"with under fourteen days in the heat.",
			),
			"joined_at": _field(
				_STRING, "When they started on the shift, YYYY-MM-DD HH:MM:SS. Defaults to now."
			),
			"notes": _field(_STRING, "Anything about this person on this shift. Kept on their crew row."),
		},
		required=("employee",),
		mutating=True,
		title="Add a worker to a shift",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"remove_worker_from_shift": _tool(
		shifts.remove_worker_from_shift,
		"MUTATING (default OFF). End one worker's time on a shift that continues "
		"without them.\n\n"
		"IT SETS `left_at`; IT DOES NOT DELETE THE ROW, and the difference is the "
		"whole point. The row is the only record that this person was on this shift "
		"at all — which is the record a wage claim turns on, and the record that "
		"says who was exposed on a hot afternoon before they were sent home. The "
		"name of the tool is the operational verb and the storage is the compliance "
		"one.\n\n"
		"Their Attendance record on close spans their joined_at to this left_at "
		"rather than to the shift's end — a worker who arrived late and left early "
		"did not work the whole day, and a row claiming they did is wrong in the "
		"employer's favour, which is the direction that gets litigated.\n\n"
		"Calling it twice on somebody who has already left is refused unless "
		"`left_at` is given explicitly: a silent second call would move their "
		"departure to now and lengthen a day that has already ended.",
		{
			"shift": _field(_STRING, "The Farm Shift docname."),
			"name": _field(_STRING, "Alias for shift."),
			"employee": _field(_STRING, "Who is leaving. Docname, employee number, name or login."),
			"left_at": _field(
				_STRING,
				"When they left, YYYY-MM-DD HH:MM:SS. Defaults to now, and is REQUIRED to "
				"correct a departure already recorded.",
			),
			"notes": _field(
				_STRING,
				"Why they left. 'Sent to the shop at 11:00, cramping' is a compliance fact "
				"about a heat shift and belongs on the person.",
			),
		},
		required=("employee",),
		mutating=True,
		title="Remove a worker from a shift",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"log_shift_event": _tool(
		shifts.log_shift_event,
		"MUTATING (default OFF). Record one thing the foreman did about the "
		"conditions, at the moment it happened.\n\n"
		"THE TIMELINE IS THE EVIDENCE. Oregon's heat rule does not ask whether water "
		"was available in principle — it asks what happened during the shift, and "
		"four water breaks with timestamps answer that in a way an annual policy "
		"document never can. `create_heat_exposure_event` is the claim; this is what "
		"the claim rests on, and an inspector asks for the second.\n\n"
		"LOGGED AT THE TIME. A timeline written from memory in the evening is what "
		"an investigator discounts, which is why this is a separate call from the "
		"close rather than a list argument on it.\n\n"
		"`producer_record_doctype` and `producer_record_name` point back at whatever "
		"documented the event — a Farm Task Assignment somebody closed, a Heat "
		"Exposure Event. Plain strings rather than a link, because a producer can "
		"belong to an app this site does not have and a link that refuses to save is "
		"a compliance event that does not get logged.\n\n"
		"An event timestamped outside the shift is KEPT AND REPORTED rather than "
		"refused: a clock five minutes out is not a false record, and refusing would "
		"mean the break goes unlogged rather than logged approximately.",
		{
			"shift": _field(_STRING, "The Farm Shift docname."),
			"name": _field(_STRING, "Alias for shift."),
			"event_type": _field(
				_STRING,
				"Water Break, Shade Break, Rest Cycle, Supervisor Observation, Heat Illness "
				"Signs Check, Cool-Down, Threshold Crossed, Acclimatization Reminder or Other. "
				"A closed vocabulary because a timeline is only readable if the rows are "
				"comparable — four Water Breaks across a shift are a cadence, and four "
				"free-text sentences are four sentences somebody has to read.",
			),
			"event_datetime": _field(
				_STRING,
				"When it happened, YYYY-MM-DD HH:MM:SS. Defaults to now, which is the right "
				"answer when the call is made as it happens and the wrong one otherwise.",
			),
			"logged_by": _field(
				_STRING,
				"Who called it. Defaults to the shift's foreman, and is worth setting when it "
				"was not — a lead worker calling a break at the far end of the block is the "
				"ordinary case, and attributing it to the foreman would be wrong in exactly the "
				"place an investigator looks.",
			),
			"description": _field(
				_STRING,
				"What actually happened. 'Cooler refilled, whole crew drank, two moved to the "
				"shaded end' is evidence; 'water break' is already the type.",
			),
			"producer_record_doctype": _field(
				_STRING, "The doctype of the record that documented this, e.g. 'Farm Task Assignment'."
			),
			"producer_record_name": _field(
				_STRING, "Its docname. Refused without the doctype — a name with nowhere to look it up."
			),
			"evidence_file_token": _field(
				_STRING,
				"A photograph or signature for this one event, as a File docname or file_url — "
				"the cooler being refilled, the shade at 13:00. Upload it with stage_file_chunk "
				"first. One pointing at nothing is refused.",
			),
			"weather_snapshot_temp_f": _field(
				_NUMBER, "Air temperature at this instant, where somebody has one. v0.19.4 fills it."
			),
			"weather_snapshot_heat_index_f": _field(
				_NUMBER,
				"Heat index at this instant. THE NUMBER THE RULE TURNS ON — 88 °F at 70 % "
				"humidity is a 100 °F index and a citation while the thermometer reads mild.",
			),
		},
		required=("event_type",),
		mutating=True,
		title="Log a shift compliance event",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"end_shift": _tool(
		shifts.end_shift,
		"MUTATING (default OFF). Close a shift with the supervisor's signature, and "
		"write the crew's payroll rows.\n\n"
		"THE SIGNATURE IS REQUIRED AND IT IS WHY THIS IS A TOOL. An unsigned close is "
		"an UPDATE setting a timestamp; the signature is what makes it the "
		"attestation FSMA §112.161(b) asks for — a review that is dated AND SIGNED by "
		"a supervisor or responsible party rather than merely recorded. Without one "
		"the shift stays open and nothing is written.\n\n"
		"IT WRITES ONE ATTENDANCE RECORD PER CREW MEMBER, each spanning that "
		"person's own joined_at to their own left_at — or to the shift's end where "
		"they stayed to it. Not the shift's span: a worker who arrived an hour late "
		"and left two hours early worked six hours of a nine-hour shift, and a row "
		"claiming nine is wrong in the employer's favour. So farm_hr keeps one "
		"canonical answer to 'when was Ana at work' and get_attendance_summary "
		"counts a shift-formed day exactly as it counts a hand-entered one.\n\n"
		"THE BRIDGE NEVER BLOCKS THE CLOSE. A site without Frappe HR, an employee "
		"archived since the shift ran, a day somebody already keyed in by hand — "
		"every one of those is REPORTED and none of them stops a signed shift being "
		"closed. The signature is the compliance act; the payroll row is the "
		"convenience.\n\n"
		"WHAT IT DOES NOT REFUSE: a shift with obligations unmet or an empty event "
		"timeline. A day where the shade trailer broke down and the crew went home "
		"at eleven is a real shift with a real gap, and the result says so rather "
		"than the tool refusing to record it.",
		{
			"shift": _field(_STRING, "The Farm Shift docname."),
			"name": _field(_STRING, "Alias for shift."),
			"supervisor_signature_file_token": _field(
				_STRING,
				"The foreman's signature, as a File docname or file_url. REQUIRED — upload it "
				"with stage_file_chunk and commit_staged_file first. One pointing at nothing is "
				"refused, because a signature that proves nothing is the one kind of missing "
				"evidence nobody discovers until an auditor clicks it.",
			),
			"end_datetime": _field(
				_STRING,
				"When the crew finished, YYYY-MM-DD HH:MM:SS. Defaults to now. Earlier than the "
				"start is refused, and so is one before a crew member's recorded departure.",
			),
			"foreman_notes": _field(
				_STRING,
				"What the foreman wants on the record: who was struggling in the heat, why a "
				"block was left, what the crew was told. The part no structured field holds and "
				"the part an investigator reads first.",
			),
			"reviewed_on": _field(_STRING, "When the review happened, YYYY-MM-DD HH:MM:SS. Defaults to now."),
		},
		required=("supervisor_signature_file_token",),
		mutating=True,
		title="End a crew shift",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"create_heat_exposure_event": _tool(
		heat.create_heat_exposure_event,
		"MUTATING (default OFF). Document OAR 437-004-1131 for one shift, signed and "
		"submitted.\n\n"
		"THIS IS THE ANSWER TO 'PROVE THE JULY 15 CREW COMPLIED'. One record, "
		"anchored to one shift, saying what was actually done — water at the "
		"required rate, shade within reach, the rest cycle TAKEN rather than offered, "
		"the crew observed, the training current — with the shift's crew list and "
		"event timeline behind it as the evidence for every claim.\n\n"
		"ONE PER SHIFT, and the second call is refused by name. Two records about one "
		"exposure period will disagree, and the one an inspector finds will be "
		"whichever was filed second.\n\n"
		"IT CHECKS `training_verified` AGAINST THE TRAINING REGISTER, as of the DAY "
		"OF THE SHIFT rather than today — a card that expired last week was current "
		"in July. Claiming verified training for a crew with an untrained worker is "
		"REFUSED, because the same audit packet carries both this record and the "
		"register, and a packet that contradicts itself is worse than one with a "
		"gap. Claiming false is accepted and the missing names are reported: a shift "
		"that ran that way happened, and the record of it is what the operation "
		"needs to have.\n\n"
		"SIGNS OBSERVED WITH NO RESPONSE AND NO NOTES IS REFUSED. Signs seen and "
		"nothing done is the sequence that kills people. There are legitimate "
		"versions — the worker recovered in shade within minutes and declined "
		"further help — and every one of them is a sentence somebody can write.\n\n"
		"Everything else is RECORDED WITH THE GAP STATED. A day where the shade "
		"trailer broke down is a real shift with a real gap, and a tool that would "
		"not let it be recorded would produce either a false record or no record.",
		{
			"farm_shift": _field(
				_STRING, "The Farm Shift this documents, e.g. SHIFT-2026-0001. Required and unique."
			),
			"shift": _field(_STRING, "Alias for farm_shift."),
			"supervisor_signature_file_token": _field(
				_STRING,
				"The supervisor's signature, as a File docname or file_url. REQUIRED — "
				"submitting is the attestation, and an unsigned heat record is a claim with "
				"nobody behind it.",
			),
			"water_provided": _field(
				_BOOLEAN,
				"Drinking water at the rate the rule sets — one quart per worker per hour, "
				"suitably cool, enough for the whole shift.",
			),
			"shade_provided": _field(
				_BOOLEAN,
				"Shade available AND ACCESSIBLE. Shade four hundred yards away at the end of "
				"the block is shade nobody uses, which is why the rule speaks of proximity.",
			),
			"mandatory_rest_taken": _field(
				_BOOLEAN,
				"The preventative cool-down rest cycle was TAKEN, not offered. A crew declining "
				"a break on a piece rate is the failure the requirement exists for.",
			),
			"heat_illness_signs_observed": _field(
				_BOOLEAN,
				"The supervisor SAW signs in somebody. Ticking it is not an admission of "
				"failure — observing is the obligation and finding something is it working. "
				"What it does is make emergency_response_activated mandatory.",
			),
			"worker_reported_symptoms": _field(
				_BOOLEAN,
				"A worker said they felt unwell. Separate from the supervisor's own "
				"observation because they are different evidence — self-report is what the "
				"two-way communication requirement is testing.",
			),
			"emergency_response_activated": _field(
				_BOOLEAN,
				"Moved to shade and cooled, somebody stayed with them, medical services called "
				"where the signs warranted. Required whenever signs were observed, unless the "
				"notes explain why not.",
			),
			"training_verified": _field(
				_BOOLEAN,
				"Every worker on the crew had current heat illness prevention training. Checked "
				"against the register as of the day of the shift; a true claim the register "
				"contradicts is refused.",
			),
			"max_temp_f": _field(_NUMBER, "Highest air temperature during the shift."),
			"max_heat_index_f": _field(
				_NUMBER,
				"Highest heat index. THE NUMBER THE RULE TURNS ON — it engages at 80 °F index "
				"and adds obligations at 90 °F, and an 88 °F day at 70 % humidity is a 100 °F "
				"index. Entered by hand until v0.19.4 computes it from the shift's weather "
				"timeline.",
			),
			"threshold_crossed_at": _field(
				_STRING,
				"When conditions first reached 80 °F, YYYY-MM-DD HH:MM:SS. The moment the "
				"obligations START, so it is the clock every water break on the shift is read "
				"against.",
			),
			"acclimatization_plan": _field(
				_STRING_ARRAY,
				"Workers with fewer than fourteen days in the heat, whom -1131(g) requires a "
				"written plan for. NAMED individually rather than counted — these are the "
				"people most likely to be hospitalised, and a plan for 'the new workers' is "
				"one an inspector cannot check. Somebody not on the shift's crew is refused.",
			),
			"event_date": _field(_STRING, "The day, YYYY-MM-DD. Defaults to the shift's own start date."),
			"notes": _field(
				_STRING,
				"Everything the checkboxes cannot hold: who showed signs and what was done, who "
				"had no current training and how the shift was adjusted for them. Where an "
				"obligation was not met this is the record of what happened instead, and an "
				"honest one is worth more under investigation than a row of ticks.",
			),
			"regulation_citation": _field(
				_STRING,
				"Defaults to OAR 437-004-1131. A field rather than a constant, because Oregon renumbers.",
			),
		},
		required=("supervisor_signature_file_token",),
		mutating=True,
		title="Create a heat exposure event",
		available=_needs_doctype("Heat Exposure Event"),
		requires="the Heat Exposure Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_shifts": _tool(
		shifts.list_shifts,
		"The shift register: who was where, with which crew, for how long, and what "
		"is still open. Read-only.\n\n"
		"`status` IS COMPUTED from whether the shift has an end time rather than read "
		"off a stored column — an OPEN shift is what the v0.19.4 weather sweep walks, "
		"and a record last saved in March holding March's answer would drop a live "
		"shift out of the fetch.\n\n"
		"`closed_without_a_signature` IS THE ONE TO READ. end_shift cannot produce "
		"one, so anything in that list was closed in the Desk or by an import — and "
		"FSMA §112.161(b) asks for a review dated and signed. A signature added now "
		"is dated now.\n\n"
		"`employee` answers 'which shifts was Ana on', walking the crew tables. "
		"Scoped to the companies the calling account may actually reach.",
		{
			"company": _COMPANY,
			"foreman": _field(_STRING, "One supervisor. Docname, employee number, name or login."),
			"employee": _field(_STRING, "Shifts this person was on the crew of, whether or not they led it."),
			"status": _field(_STRING, "Active, Closed or Cancelled — computed, not stored."),
			"shift_type": _field(
				_STRING,
				"Spray, Harvest, Prune, Irrigation, Housing Work, Detector Test Round, Maintenance or General.",
			),
			"from_date": _field(_STRING, "Earliest shift start date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest shift start date, YYYY-MM-DD."),
			"limit": _field(_INTEGER, "Maximum shifts. Default 100, hard maximum 500."),
		},
		title="List shifts",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_shift": _tool(
		shifts.get_shift,
		"One shift in full: the crew and their individual spans, the compliance-event "
		"timeline, the weather timeline, and the heat record if one exists. "
		"Read-only.\n\n"
		"THIS IS THE EVIDENCE CHAIN AN INSPECTOR IS HANDED. 'Prove the July 15 shift "
		"complied' is answered by the Heat Exposure Event's claims plus this — who "
		"was there and from when, what breaks were called and at what time, what the "
		"conditions were, and whose signature is on the close.\n\n"
		"Each crew row reports `present_until`, which is the honest reading of an "
		"empty left_at: they were there to the end. It is computed rather than "
		"written back, because writing it would destroy the distinction between "
		"'left at 13:00' and 'stayed to the end' the moment the end time changed.",
		{
			"name": _field(_STRING, "The Farm Shift docname, e.g. SHIFT-2026-0001."),
			"shift": _field(_STRING, "Alias for name."),
		},
		title="Get a shift",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_heat_exposure_events": _tool(
		heat.list_heat_exposure_events,
		"The heat register: every documented hot shift, what each one claims, and "
		"where the claims stop. Read-only.\n\n"
		"`with_signs_observed` IS THE FIRST THING AN INVESTIGATION READS, and a shift "
		"on that list is one where the observation obligation was working rather "
		"than failing — somebody looked and found something.\n\n"
		"`without_verified_training` is the list that becomes a citation on the first "
		"hot morning: OAR 437-004-1131 requires heat illness prevention training "
		"annually AND before work at a site where the heat index will reach 80 °F.\n\n"
		"`without_a_signature` should be empty — create_heat_exposure_event cannot "
		"produce an unsigned record, so anything there was written in the Desk.\n\n"
		"Scoped to the companies the calling account may actually reach.",
		{
			"company": _COMPANY,
			"from_date": _field(_STRING, "Earliest event date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest event date, YYYY-MM-DD."),
			"with_gaps_only": _field(
				_BOOLEAN,
				"Only records that do not claim every -1131 obligation was met. The worklist, "
				"and the set an inspector would open first.",
			),
			"limit": _field(_INTEGER, "Maximum records. Default 100, hard maximum 500."),
		},
		title="List heat exposure events",
		available=_needs_doctype("Heat Exposure Event"),
		requires="the Heat Exposure Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_heat_exposure_event": _tool(
		heat.get_heat_exposure_event,
		"One heat record in full, with the shift behind it — crew, timeline, weather. "
		"Read-only.\n\n"
		"IT REPORTS THE OBLIGATIONS THE RECORD DOES NOT CLAIM WERE MET, in the rule's "
		"own terms, rather than fixing them: a tick added now is a tick added now, "
		"and a record completed before an inspection is what an inspector is trained "
		"to spot.\n\n"
		"WHERE THE SHIFT'S EVENT TIMELINE IS EMPTY IT SAYS SO, and that is the gap "
		"worth knowing about before an audit rather than during one. The checkboxes "
		"on this record are an ASSERTION; the shift's logged water breaks, rest "
		"cycles and observations are the EVIDENCE for it, and an inspector asks for "
		"the second.",
		{
			"name": _field(_STRING, "The Heat Exposure Event docname, e.g. HEAT-2026-0001."),
			"heat_exposure_event": _field(_STRING, "Alias for name."),
			"record": _field(_STRING, "Alias for name."),
		},
		title="Get a heat exposure event",
		available=_needs_doctype("Heat Exposure Event"),
		requires="the Heat Exposure Event DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.19.4: the weather timeline the shift is read against ─────────────
	"fetch_weather_now": _tool(
		weather.fetch_weather_now,
		"MUTATING (default OFF). Append one Open-Meteo reading to an OPEN shift "
		"right now, bypassing the cache.\n\n"
		"FOR THE MOMENT THE SCHEDULE IS TOO SLOW FOR. A cron documents every open "
		"shift every fifteen minutes, which is right for a whole day and wrong for "
		"a foreman watching a crew struggle at eleven who wants the conditions ON "
		"THE RECORD before deciding anything.\n\n"
		"IT LOGS A `Threshold Crossed` COMPLIANCE EVENT WHERE THE READING CROSSES, "
		"once per shift rather than once per reading — a nine-hour afternoon above "
		"eighty degrees would otherwise bury the water breaks under thirty-six "
		"identical rows. It NEVER creates a Heat Exposure Event: that record says "
		"which crew was exposed, what water was provided, whether the rest cycle "
		"was taken and whether anybody showed signs, and it carries a signature. "
		"Those are judgements by the person who was standing there, and "
		"create_heat_exposure_event is where a person writes them.\n\n"
		"REFUSES A CLOSED SHIFT — a `current` reading filed against a crew who went "
		"home is true about the place and false about the shift. Use "
		"backfill_weather_for_shift. Refuses a shift with no farm_location_gps, "
		"because there is no place to ask about.\n\n"
		"Requires System Manager, HR Manager, HR User or Farm Manager, the shift's "
		"own company in the calling account's scope, and `enabled` ticked on "
		"Weather Settings.",
		{
			"shift": _field(_STRING, "The Farm Shift docname, e.g. SHIFT-2026-0001."),
			"name": _field(_STRING, "Alias for shift."),
		},
		mutating=True,
		title="Fetch weather now",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"backfill_weather_for_shift": _tool(
		weather.backfill_weather_for_shift,
		"MUTATING (default OFF). Reconstruct a CLOSED shift's weather timeline from "
		"Open-Meteo's historical archive.\n\n"
		"EVERY SITE THAT INSTALLS THIS HAS A SEASON OF SHIFTS WITH AN EMPTY WEATHER "
		"TABLE, and a shift with no timeline is not a shift that was compliant or "
		"non-compliant — it is one nobody can say anything about. Open-Meteo still "
		"knows what the weather was that day.\n\n"
		"IDEMPOTENT, AND IT REPORTS THE NUMBERS: every reading is matched against "
		"the minute already on the timeline, so a second run adds nothing and says "
		"what it skipped. A reading is never edited, so running this over a shift "
		"that was ALSO swept live keeps the live readings and fills the gaps.\n\n"
		"HOURLY, NOT FIFTEEN-MINUTE. That is the archive's own granularity, and "
		"every row says so in its `source` column so a reconstruction is never read "
		"as something contemporaneous.\n\n"
		"IT WRITES NO COMPLIANCE EVENTS. A Threshold Crossed row dated last July on "
		"a closed and signed shift would be an observation nobody made, sitting "
		"beside water breaks somebody did. The crossings are COUNTED and reported "
		"for a human to read.\n\n"
		"Refuses an open shift (the archive is a reanalysis of hours that have "
		"already happened) and one with no coordinates.",
		{
			"shift": _field(_STRING, "The Farm Shift docname, e.g. SHIFT-2026-0001."),
			"name": _field(_STRING, "Alias for shift."),
		},
		mutating=True,
		idempotent=True,
		title="Backfill weather for a shift",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_shifts_missing_weather": _tool(
		weather.list_shifts_missing_weather,
		"Closed shifts whose weather timeline is thinner than their own length — "
		"the worklist for backfill_weather_for_shift. Read-only.\n\n"
		"THE TEST IS ONE READING PER HOUR OF SHIFT, which is the archive's own "
		"granularity: a fully backfilled shift never appears here and a live-swept "
		"one clears the bar four times over. It is a heuristic and says so — a "
		"shift swept for its first two hours and then missed has readings and is "
		"still missing most of its timeline, and this is what finds it.\n\n"
		"SHIFTS WITH NO `farm_location_gps` ARE REPORTED SEPARATELY, because no "
		"amount of backfilling will document one and the fix is a different action: "
		"put coordinates on the shift first. Scoped to the companies the calling "
		"account may actually reach.",
		{
			"company": _COMPANY,
			"from_date": _field(_STRING, "Earliest shift start date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest shift start date, YYYY-MM-DD."),
			"limit": _field(_INTEGER, "Maximum shifts. Default 100, hard maximum 500."),
		},
		title="List shifts missing weather",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_weather_timeline": _tool(
		weather.get_weather_timeline,
		"One shift's conditions across its exposure period, with the extremes and "
		"the times it was at or above threshold. Read-only.\n\n"
		"THE ANSWER TO 'HOW HOT WAS IT WHEN THE BREAK WAS CALLED' without reading "
		"the whole shift. `get_shift` returns the timeline beside the crew, the "
		"events and the heat record, which is right for an audit and too much for a "
		"question about one hour.\n\n"
		"`from_datetime` and `to_datetime` window it. `first_crossing` is the "
		"instant OAR 437-004-1131's obligations start running from, and the shift's "
		"own compliance events are what say whether they happened.\n\n"
		"IT SAYS WHERE EACH READING CAME FROM. A live fifteen-minute timeline and "
		"one reconstructed from the hourly archive are different kinds of evidence, "
		"and a MIXED timeline is called out by name.",
		{
			"shift": _field(_STRING, "The Farm Shift docname, e.g. SHIFT-2026-0001."),
			"name": _field(_STRING, "Alias for shift."),
			"from_datetime": _field(_STRING, "Earliest reading, YYYY-MM-DD HH:MM:SS. Optional."),
			"to_datetime": _field(_STRING, "Latest reading, YYYY-MM-DD HH:MM:SS. Optional."),
		},
		title="Get a shift's weather timeline",
		available=_needs_doctype("Farm Shift"),
		requires="the Farm Shift DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_weather_settings": _tool(
		weather.get_weather_settings,
		"What this site fetches, how often, and the thresholds a reading is read "
		"against — including the per-company overrides. Read-only.\n\n"
		"THERE IS NO WRITE COUNTERPART AND THERE WILL NOT BE ONE. The three URLs "
		"here are outbound endpoints and the three thresholds decide whether a hot "
		"afternoon is logged at all; a tool that could change either would be one "
		"sentence away from pointing this site's weather somewhere else, or from "
		"raising the heat threshold past anything Oregon produces so that no shift "
		"ever crosses it — producing a site that behaves normally and never says "
		"anything is wrong. An operator edits Weather Settings in the Desk.\n\n"
		"IT REPORTS OVERRIDES BY COMPANY, because 'the threshold is 80' is false on "
		"a site where one entity set 75, and it answers WHY nothing is being "
		"fetched — so it works even when the kill switch is off.\n\n"
		"THE CRON IS THE CEILING AND `fetch_interval_minutes` IS THE FLOOR: a "
		"Frappe cron expression cannot be rewritten from a form, so raising the "
		"setting gets readings less often and lowering it below fifteen changes "
		"nothing.",
		{},
		title="Get weather settings",
		available=_needs_doctype("Weather Settings"),
		requires="the Weather Settings DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.19.5: Sustainable CF/Acre ────────────────────────────────────────
	#
	# Four mutating and two read. The split between the first two mutating tools
	# is the whole compliance posture: `create_normalization_adjustment` produces
	# a DRAFT and can produce nothing else, and `approve_…` is a separate switch
	# that takes a signature. Finding a non-recurring item in a ledger nobody
	# reads line by line is worth a great deal and is something a model is good
	# at; deciding that it will not recur is a judgement with a lender on the
	# other end of it.
	"create_normalization_adjustment": _tool(
		kpi.create_normalization_adjustment,
		"MUTATING (default OFF). Propose one add-back to or subtraction from "
		"operating cash flow for a company and a period, with the sentence that "
		"says why it will not recur. CREATES A DRAFT, ALWAYS — a draft does NOT "
		"count towards Sustainable CF/Acre and nothing here can make it count.\n\n"
		"THAT SPLIT IS THE POINT. Insurance recoveries, litigation settlements, "
		"weather-event losses and quarter-end working-capital timing are scattered "
		"through a ledger nobody reads line by line, and finding them is genuinely "
		"useful. Deciding that a hailstorm in a region that hails every third year "
		"is non-recurring is a judgement somebody has to defend across a table, "
		"and approve_normalization_adjustment is where that happens.\n\n"
		"`amount` IS ALWAYS POSITIVE; the sign lives in `direction`. A negative "
		"amount beside a Subtract is a double negative, and a double negative is "
		"how an adjustment moves the number the wrong way in a lender's pack.\n\n"
		"`justification` HAS A FORTY-CHARACTER FLOOR. Not a quality bar — no "
		"character count is one — but a floor under 'one-time', which is what gets "
		"written when the field is merely required and which an auditor reads as an "
		"admission that nobody thought about it.",
		{
			"company": _COMPANY,
			"fiscal_year": _field(
				_STRING, "Fiscal Year docname, e.g. '2026'. The period must fall inside it."
			),
			"period_start": _field(
				_STRING, "First day the adjustment covers, YYYY-MM-DD. Usually a quarter or month start."
			),
			"period_end": _field(_STRING, "Last day it covers, inclusive, YYYY-MM-DD."),
			"amount": _field(
				_NUMBER, "The size of the adjustment. ALWAYS POSITIVE — direction carries the sign."
			),
			"direction": _field(
				_STRING,
				"'Add-back to OCF' where cash the operation really spent should not be read as "
				"recurring, or 'Subtract from OCF' where cash it really received should not be "
				"read as earning power.",
			),
			"category": _field(
				_STRING,
				"One of Insurance-Proceeds, Litigation-Settlement, Weather-Event-Loss, "
				"Asset-Sale-Gain, Working-Capital-Timing, Discontinued-Operation, Restructuring, "
				"Other. One approved adjustment per company, period and category.",
			),
			"justification": _field(
				_STRING,
				"Why this will not recur, in at least 40 characters. The question it has to "
				"answer is the one every buyer asks.",
			),
			"supporting_document_file_token": _field(
				_STRING,
				"Optional File docname or file_url for the paper behind the sentence — the "
				"insurance determination, the settlement agreement, the board minute.",
			),
		},
		required=(
			"company",
			"fiscal_year",
			"period_start",
			"period_end",
			"amount",
			"direction",
			"category",
			"justification",
		),
		mutating=True,
		title="Propose a normalization adjustment",
		available=_needs_doctype("Normalization Adjustment"),
		requires="the Normalization Adjustment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"approve_normalization_adjustment": _tool(
		kpi.approve_normalization_adjustment,
		"MUTATING (default OFF). Accept a proposed normalization: status to "
		"Approved, the approver's signature attached, and an approval timestamp "
		"WRITTEN RATHER THAN TAKEN AS INPUT.\n\n"
		"THE SIGNATURE IS WHAT APPROVAL MEANS. The whole argument for this record "
		"is that a normalization is a judgement with somebody's name against it, "
		"and every buyer and lender who reads the resulting figure tests the "
		"adjustments one at a time — an unsigned add-back is the one they stop at. "
		"There is no unsigned path through this tool.\n\n"
		"REFUSED WHERE ANOTHER APPROVED ADJUSTMENT ALREADY COVERS THE SAME "
		"COMPANY, PERIOD AND CATEGORY: two approved adjustments are two answers to "
		"one question and the one a reader finds will be whichever sorted first. A "
		"correction SUPERSEDES rather than duplicates.\n\n"
		"`approver_employee` defaults to the Employee linked to the acting user, "
		"and is empty where the app runs as a service principal — which is the "
		"ordinary configuration and is not a failure.",
		{
			"name": _field(_STRING, "The adjustment's docname, e.g. NADJ-2026-0001."),
			"approver_signature_file_token": _field(
				_STRING, "File docname or file_url for the approver's signature. REQUIRED."
			),
			"approver_employee": _field(
				_STRING, "Employee docname, number, name or login. Defaults to the acting user's Employee."
			),
		},
		required=("name", "approver_signature_file_token"),
		mutating=True,
		title="Approve a normalization adjustment",
		available=_needs_doctype("Normalization Adjustment"),
		requires="the Normalization Adjustment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"reject_normalization_adjustment": _tool(
		kpi.reject_normalization_adjustment,
		"MUTATING (default OFF). Refuse a proposed normalization, on the record, "
		"with the reason attached.\n\n"
		"THE REJECTION IS KEPT rather than deleted, for the same reason a rejected "
		"insurance claim is kept: a refusal with a reason teaches the next "
		"proposal, and a register with only its successes in it says nothing about "
		"how hard the successes were to get.\n\n"
		"AN ALREADY-APPROVED ADJUSTMENT CANNOT BE REJECTED. It has been counted, "
		"and rejecting it now would rewrite a decision rather than record one — "
		"supersede it instead.",
		{
			"name": _field(_STRING, "The adjustment's docname, e.g. NADJ-2026-0001."),
			"rejection_reason": _field(_STRING, "Why the justification was not accepted. REQUIRED."),
		},
		required=("name", "rejection_reason"),
		mutating=True,
		title="Reject a normalization adjustment",
		available=_needs_doctype("Normalization Adjustment"),
		requires="the Normalization Adjustment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"backfill_asset_capex_type": _tool(
		kpi.backfill_asset_capex_type,
		"MUTATING (default OFF). Classify Assets that have no `capex_type` in "
		"bulk. DRY RUN BY DEFAULT — it reports what it would do and writes "
		"nothing until `dry_run=false`.\n\n"
		"THE HEURISTIC, IN ONE SENTENCE: everything bought before the operation "
		"started tracking is generally MAINTENANCE, because it is the existing "
		"productive plant carrying on. That is the whole of the rule.\n\n"
		"IT NEVER OVERWRITES A CLASSIFICATION SOMEBODY MADE, only fills in nulls, "
		"so a second run finds nothing to do. `cutoff_purchase_date` restricts it "
		"to assets bought before a date — which is how 'everything before we "
		"started tracking' is actually expressed.\n\n"
		"A STARTING POSITION, NOT AN ANSWER. The new block planted in year six was "
		"growth and will be recorded as maintenance until somebody fixes it on the "
		"Asset. That understates Sustainable CF/Acre; a register of nulls "
		"overstates it, because unclassified purchases are excluded entirely. "
		"Mixed is refused as a bulk default: a split is a judgement about one "
		"invoice.",
		{
			"default_capex_type": _field(
				_STRING, "Maintenance (the default) or Growth. Mixed is refused — see above."
			),
			"cutoff_purchase_date": _field(
				_STRING, "Only classify assets purchased BEFORE this date, YYYY-MM-DD. Optional."
			),
			"company": _COMPANY,
			"dry_run": _field(_BOOLEAN, "Default TRUE. Report what would change and write nothing."),
		},
		mutating=True,
		idempotent=True,
		title="Backfill Asset capex classification",
		available=_needs_doctype("Asset"),
		requires="ERPNext's Asset DocType, plus the v0.19.5 capex columns (run `bench migrate`)",
	),
	"list_normalization_adjustments": _tool(
		kpi.list_normalization_adjustments,
		"The normalization register: every adjustment proposed, approved, refused "
		"or superseded, with its justification and who signed it. Read-only.\n\n"
		"`counted_in_the_kpi` IS THE LIST THAT MATTERS — only Approved rows move "
		"Sustainable CF/Acre. Drafts, pending proposals, rejections and superseded "
		"rows are all in the register and none of them changes the number.\n\n"
		"`awaiting_a_decision` is the other one worth reading at quarter end: a "
		"proposal nobody has decided is not a neutral state, it is a figure that "
		"will change after the pack goes out.\n\n"
		"Scoped to the companies the caller may see.",
		{
			"company": _COMPANY,
			"fiscal_year": _field(_STRING, "Fiscal Year docname, e.g. '2026'. Optional."),
			"status": _field(_STRING, "Draft, Pending Approval, Approved, Rejected or Superseded. Optional."),
			"limit": _LIMIT,
		},
		title="List normalization adjustments",
		available=_needs_doctype("Normalization Adjustment"),
		requires="the Normalization Adjustment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"get_sustainable_cf_per_acre": _tool(
		kpi.get_sustainable_cf_per_acre,
		"Sustainable CF/Acre for one company over one period — (normalized "
		"operating cash flow minus maintenance capex) divided by productive acres — "
		"WITH EVERY "
		"INGREDIENT ITEMIZED. Read-only.\n\n"
		"THE COMPONENTS ARE NOT OPTIONAL EXTRAS. A normalized figure nobody can "
		"inspect is indistinguishable from an arranged one, and every buyer, "
		"lender and auditor who reads this number will test it one add-back at a "
		"time. So the payload carries each approved adjustment with its "
		"justification and signature, each maintenance-capex asset with its "
		"purchase date and portion, and each productive field with the days it was "
		"in service.\n\n"
		"RAW OCF IS COMPUTED FROM GL ENTRY BY THE DIRECT METHOD — cash and bank "
		"movement per submitted voucher, apportioned to operating / investing / "
		"financing by the accounts on the other side — rather than read off "
		"ERPNext's Cash Flow report, so it can be traced back to rows.\n\n"
		"MAINTENANCE CAPEX IS ACTUAL SPEND, NEVER A PERCENTAGE OF REVENUE. Under-"
		"investment is the signal the metric exists to show, and a percentage "
		"formula reports a well-maintained farm every time, including in the years "
		"it matters. Assets with no `capex_type` are EXCLUDED and counted, not "
		"guessed at in either direction.\n\n"
		"THE DENOMINATOR IS WHAT IS PRODUCTIVE, NOT WHAT IS OWNED: fallow ground "
		"and pre-yield plantings are out and counted separately, and a block that "
		"came into bearing in February is weighted for the part of the period it "
		"was actually earning.\n\n"
		"`sustainable_cf_per_acre` is null — not zero — where there are no "
		"productive acres. Read `computation_warnings` before quoting the figure.\n\n"
		"v0.19.6: IT DEFAULTS TO A TRAILING TWELVE MONTHS. Call it with only a "
		"company and you get the TTM window ending at the last completed month, "
		"the month just finished beside it, and five years of prior TTM values "
		"with their mean, median, spread and the two deltas. Agricultural revenue "
		"is aggressively seasonal, and a single-period figure compared with "
		"another single period says the farm collapsed in January and recovered "
		"in September — every year, on every farm.\n\n"
		"PASSING `period_start` AND `period_end` GETS THE OLD v0.19.5 PAYLOAD "
		"back, exactly, with a deprecation note in `computation_warnings`. That "
		"path still works because this figure is quoted in packs that were sent "
		"before the window existed.",
		{
			"company": _COMPANY,
			"as_of": _field(
				_STRING,
				"The reporting moment, YYYY-MM-DD. Defaults to today. The window ends at the "
				"last COMPLETED computation step on or before it — read on 2026-08-03 with a "
				"Monthly step, the window ends 2026-07-31.",
			),
			"window_type": _field(
				_STRING,
				"Snapshot, TTM (the default), MTD, QTD, YTD or Custom. TTM is twelve rolling "
				"months; the to-date windows accumulate from the start of the current period "
				"to as_of.",
			),
			"window_months": _field(
				_INTEGER, "Months in the window. 12 for TTM, which is what the T and the M mean."
			),
			"computation_step": _field(
				_STRING,
				"Daily, Weekly, Monthly (the default), Quarterly or Yearly. Sets both the "
				"boundary the window ends on and the spacing of the historical series.",
			),
			"historical_lookback_years": _field(
				_INTEGER, "How far back to build the prior-window series. Default 5, maximum 10."
			),
			"include_historical_averages": _field(
				_BOOLEAN, "Default TRUE. False skips the history entirely and answers faster."
			),
			"period_start": _field(
				_STRING,
				"DEPRECATED (v0.19.5 signature). First day of an explicit period, YYYY-MM-DD. "
				"Pass with period_end to get the old point-in-time payload.",
			),
			"period_end": _field(
				_STRING,
				"DEPRECATED (v0.19.5 signature). Last day of the explicit period, inclusive.",
			),
		},
		title="Sustainable CF/Acre",
		available=_needs_doctype("Normalization Adjustment"),
		requires="the Normalization Adjustment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.19.6: the window standard ────────────────────────────────────────
	#
	# Two read and one mutating. `get_windowed_report` is the generic entry
	# point and the reason the standard generalizes: a report registered in
	# services/financial_reports.py is reachable through it without another tool,
	# another switch and another catalogue section. A framework whose every KPI
	# costs a tool is a framework with six KPIs in it.
	"get_windowed_report": _tool(
		kpi.get_windowed_report,
		"Any registered financial report over a window, with its own history "
		"beside it. TRAILING TWELVE MONTHS BY DEFAULT. Read-only.\n\n"
		"`report_name` selects among the registered computers: "
		"'sustainable_cf_per_acre', 'ocf' (raw and normalized operating cash "
		"flow) and 'revenue'. The payload lists what this site has.\n\n"
		"WHY TTM IS THE DEFAULT AND NOT AN OPTION. Agricultural revenue is "
		"aggressively seasonal: Q3 is harvest and Q1 is pruning, so comparing "
		"two quarters says the operation collapsed in January and recovered in "
		"September, every year, on every farm. A rolling twelve months contains "
		"the whole cycle exactly once however it is read, which is why it is the "
		"standard lens for lender covenants.\n\n"
		"THREE BLOCKS, AND EACH CORRECTS THE OTHER TWO. `point_in_time` is the "
		"period just finished. `window` (also `ttm` when the type is TTM) is the "
		"rolling figure with its components — the summed adjustments, the "
		"aggregated maintenance capex, the time-weighted acres, each still "
		"inspectable. `historical_averages` is what that window has been worth "
		"before, with mean, median, min, max, standard deviation, the delta "
		"against the mean and the delta against the same window a year ago. A "
		"TTM figure means one thing above its five-year mean and the opposite "
		"below it, and the first two blocks cannot say which.\n\n"
		"THE WINDOW ENDS AT THE LAST COMPLETED STEP, never at a part-finished "
		"one: three days of August against twelve months of everything else is a "
		"figure that falls every first of the month and recovers by the "
		"thirty-first.\n\n"
		"PARTIAL HISTORY IS SAID OUT LOUD, never annualized. A site with four "
		"months of ledger gets four months of ledger and a warning, because "
		"annualizing it would invent eight months of a season that has not "
		"happened. Read `computation_warnings` before quoting anything here.\n\n"
		"IT WARMS A CACHE, AND THAT IS THE ONE THING IT WRITES. Snapshots it had "
		"to compute are saved to Financial KPI History so the next caller does "
		"not recompute them — a five-year Monthly history is sixty full "
		"computations over twelve months of GL each. NOTHING IN YOUR LEDGER IS "
		"TOUCHED: no Account, no GL Entry, no Journal Entry, no Asset, no Field, "
		"no adjustment. Every row it writes is derived state that the overnight "
		"sweep would have written anyway, and deleting the lot changes no "
		"answer this tool gives — only how long it takes to give it.",
		{
			"report_name": _field(
				_STRING,
				"Which registered report: 'sustainable_cf_per_acre', 'ocf' or 'revenue'. The "
				"result lists the registry under `available_reports`.",
			),
			"company": _COMPANY,
			"as_of": _field(
				_STRING,
				"The reporting moment, YYYY-MM-DD. Defaults to today. The window ends at the "
				"last COMPLETED computation step on or before it.",
			),
			"window_type": _field(
				_STRING,
				"Snapshot, TTM (the default), MTD, QTD, YTD or Custom. The to-date windows run "
				"from the start of the current period to as_of, which is what makes them "
				"comparable with the same span of the prior year.",
			),
			"window_months": _field(_INTEGER, "Months in the window. Default 12."),
			"computation_step": _field(
				_STRING,
				"Daily, Weekly, Monthly (the default), Quarterly or Yearly. Quarterly and "
				"Yearly boundaries follow the company's own FISCAL year; the payload reports "
				"`fiscal_year_start_month` so nobody has to infer it.",
			),
			"historical_lookback_years": _field(
				_INTEGER, "How far back to build the prior-window series. Default 5, maximum 10."
			),
			"include_historical_averages": _field(
				_BOOLEAN, "Default TRUE. False skips the history entirely and answers faster."
			),
		},
		required=("report_name",),
		title="Windowed financial report",
		available=_needs_doctype("Normalization Adjustment"),
		requires="the Normalization Adjustment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"list_financial_kpi_history": _tool(
		kpi.list_financial_kpi_history,
		"The precomputed KPI cache, read directly: the time series without the "
		"apparatus around it. Read-only.\n\n"
		"USE THIS TO DRAW A LINE. get_windowed_report answers 'what is this worth "
		"now, and is that good'; this answers 'show me the series', and a caller "
		"charting sixty months does not want sixty copies of the components dict "
		"to get sixty numbers.\n\n"
		"A GAP HERE IS NOT A GAP IN THE BUSINESS. It is a window nobody has "
		"computed yet, or one that was invalidated by a retroactively approved "
		"normalization adjustment and not yet rebuilt — and plotting it as a "
		"continuous line draws a trend that did not happen. The payload says how "
		"many rows had no value and why.\n\n"
		"`source_versions` MATTERS ON A LONG SERIES: where a release changed how "
		"a figure is computed, a series spanning the change holds two definitions "
		"of one KPI on one line with nothing marking the join. The tool reports "
		"it rather than leaving it to be noticed.\n\n"
		"Scoped to the companies the caller may see.",
		{
			"kpi_key": _field(
				_STRING, "'sustainable_cf_per_acre', 'ocf' or 'revenue'. Optional — omit for all."
			),
			"company": _COMPANY,
			"computation_step": _field(_STRING, "Daily, Weekly, Monthly, Quarterly or Yearly. Optional."),
			"window_type": _field(_STRING, "Snapshot, TTM, MTD, QTD, YTD or Custom. Optional."),
			"from_date": _field(_STRING, "Earliest `as_of` to return, YYYY-MM-DD. Optional."),
			"to_date": _field(_STRING, "Latest `as_of` to return, YYYY-MM-DD. Optional."),
			"limit": _LIMIT,
		},
		title="List financial KPI history",
		available=_needs_doctype("Financial KPI History"),
		requires="the Financial KPI History DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"recompute_kpi_history": _tool(
		kpi.recompute_kpi_history,
		"MUTATING (default OFF). Rebuild the cached history for one KPI across "
		"one or every company. IDEMPOTENT unless `force`, which clears the series "
		"and builds it again.\n\n"
		"THE ONLY THING IT CAN CHANGE IS A CACHE. Every row it writes is what the "
		"live computation would have produced for that window, and every row it "
		"deletes comes back on the next read or the next overnight sweep. Nothing "
		"here is the only copy of anything, so the worst outcome of running it at "
		"the wrong moment is time spent.\n\n"
		"IT IS THE ANSWER TO A RETROACTIVE APPROVAL. Approving a normalization "
		"adjustment for a period the history already covers invalidates the "
		"snapshots whose window contained it; this rebuilds them NOW, with the "
		"result in front of you, rather than overnight — which is what you want "
		"when the pack goes out this afternoon. A Field productive-date backfill "
		"is the other case: it moves the denominator of every window containing "
		"the corrected block.\n\n"
		"`force=true` CLEARS AND REBUILDS. Use it after a release changes how a "
		"figure is computed: an incremental fill leaves the old rows in place, "
		"and a series holding two definitions of one KPI is a line with an "
		"unmarked join in it.",
		{
			"kpi_key": _field(_STRING, "'sustainable_cf_per_acre', 'ocf' or 'revenue'. REQUIRED."),
			"company": _COMPANY,
			"back_years": _field(_INTEGER, "How many years of history to build. Default 5, maximum 10."),
			"force": _field(
				_BOOLEAN,
				"Default FALSE, which fills only what is missing. TRUE deletes the series "
				"first and rebuilds every snapshot under the current code.",
			),
		},
		required=("kpi_key",),
		mutating=True,
		idempotent=True,
		title="Recompute KPI history",
		available=_needs_doctype("Financial KPI History"),
		requires="the Financial KPI History DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"revoke_mobile_user": _tool(
		mobile.revoke_mobile_user,
		"MUTATING (default OFF). End one account: disable the login, destroy the "
		"API credential, and RECORD WHY.\n\n"
		"`reason` IS REQUIRED AND IS THE POINT. 'Left at the end of harvest', "
		"'phone lost in the orchard' and 'dismissed for cause' are three different "
		"answers to the question an auditor asks about why somebody's access "
		"ended, and the grant is the only place any of them survives — Frappe keeps "
		"the access and none of the story.\n\n"
		"THE ROLES ARE LEFT ON THE ACCOUNT, DELIBERATELY. A disabled user with no "
		"live token cannot sign in, and keeping the roles means the record still "
		"says what this person WAS. An account stripped of its roles is one nobody "
		"can later answer 'what could they see' about.\n\n"
		"This is 'they no longer work here'. For 'they lost their phone', use "
		"revoke_api_token, which kills the credential and leaves the account.",
		{
			"email": _field(_STRING, "The account to revoke."),
			"user": _field(_STRING, "Alias for email."),
			"reason": _field(
				_STRING, "Why. Required, and it has to be a real one — at least eight characters."
			),
			"keep_user_permissions": _field(
				_BOOLEAN,
				"Keep the Company User Permissions as evidence of what this account could see. Default true.",
			),
		},
		required=("email", "reason"),
		mutating=True,
		title="Revoke a mobile user",
		available=_needs_doctype("Mobile Access Grant"),
		requires="the Mobile Access Grant DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"generate_api_token": _tool(
		mobile.generate_api_token,
		"MUTATING (default OFF). Mint a fresh Frappe API key/secret for one user "
		"and return the pair — THE ONLY TIME THE SECRET APPEARS IN A RESULT. "
		"Issuing a new one stops the previous one working, which is what makes "
		"this the answer to a lost phone.\n\n"
		"`expiry_days` SETS A REVIEW DATE, NOT AN EXPIRY, and the result says so. "
		"Frappe API secrets do not expire on their own, and this app installs no "
		"scheduled job that revokes one — a job rewriting another app's User "
		"records at three in the morning is not a thing this app does. "
		"list_mobile_users flags an overdue grant loudly; revoke_api_token is what "
		"actually ends it. Calling a reminder an expiry would be a false assurance "
		"about a credential, which is worse than no assurance.\n\n"
		"THE CREDENTIAL BUYS IDENTITY, NOT ENTRY. A mobile request still presents "
		"the shared X-MCP-Token and still has to come from an allowed CIDR; this "
		"header goes alongside it and is what makes get_current_user_context and "
		"every list_*_for_me tool answer for the right person.\n\n"
		"Refuses a disabled user — a token minted for a login that cannot sign in "
		"is a token somebody will spend an afternoon debugging.",
		{
			"user": _field(_STRING, "The account to issue for."),
			"expiry_days": _field(_INTEGER, "Days until the credential should be reviewed. Default 120."),
			"url": _field(_STRING, "Public base URL to report in the endpoint. Defaults to public_url."),
		},
		required=("user",),
		mutating=True,
		title="Generate an API token",
	),
	"revoke_api_token": _tool(
		mobile.revoke_api_token,
		"MUTATING (default OFF). Destroy one user's API credential. THE ACCOUNT "
		"ITSELF IS UNTOUCHED and stays enabled — this is 'they lost their phone', "
		"where revoke_mobile_user is 'they no longer work here'.\n\n"
		"Both halves of the credential go, not just the secret: an api_key left on "
		"the row reads like a live credential to anybody scanning the User list, "
		"and the whole value of a revocation is that somebody can tell at a glance "
		"that it happened.",
		{
			"user": _field(_STRING, "The account whose credential to destroy."),
			"reason": _field(_STRING, "Why — appended to the grant's notes."),
		},
		required=("user",),
		mutating=True,
		title="Revoke an API token",
	),
	"generate_mobile_login_qr": _tool(
		mobile.generate_mobile_login_qr,
		"MUTATING (default OFF). The enrolment card: a scannable PNG carrying the "
		"public URL, the user and the credential, returned base64. The app scans "
		"it, stores the token in the Keychain, and every call after that carries "
		"it as a header. The alternative is somebody typing a 15-character secret "
		"into a phone keyboard in a farm office, which is how the secret ends up on "
		"a whiteboard.\n\n"
		"THIS IMAGE IS A LIVE CREDENTIAL. Anybody who photographs it over "
		"somebody's shoulder has the account. That is inherent to enrolment by QR "
		"and the mitigation is time: `expires_at` is 24 hours by default and is the "
		"deadline for ENROLLING, and rotate_token (default TRUE) mints a fresh "
		"secret so an older photograph of an older card stops working — and so does "
		"any phone already enrolled on this account, which must re-scan.\n\n"
		"REFUSES A NON-HTTPS ENDPOINT. Encoding a live credential for a plaintext "
		"URL would put it on the wire in the clear at every call, forever. Refuses "
		"a disabled user, and refuses when the site does not know its own public "
		"URL — fill in `public_url` with the Tailscale Funnel address.\n\n"
		"`archive=true` files it as a PRIVATE attachment on a Governance Document, "
		"which is the offline distribution path for a camp office at the end of a "
		"gravel road. Delete that document once the phone is enrolled: the durable "
		"record is the Mobile Access Grant, and it holds no secret.",
		{
			"user": _field(_STRING, "The account to enrol."),
			"expiry_hours": _field(_INTEGER, "How long the QR stays valid to enrol with. 1–168, default 24."),
			"rotate_token": _field(
				_BOOLEAN,
				"Mint a fresh secret for the card. DEFAULT TRUE, which invalidates any phone "
				"already enrolled. False re-prints the existing credential.",
			),
			"url": _field(_STRING, "Public base URL for the card. Defaults to public_url. Must be https."),
			"archive": _field(
				_BOOLEAN, "Also file it as a private attachment on a Governance Document. Default false."
			),
			"company": _field(_STRING, "Which entity to file the archived copy under."),
			"error_correction": _field(_STRING, "L, M, Q or H. Default M."),
		},
		required=("user",),
		mutating=True,
		title="Generate a mobile login QR",
		available=_qr_available,
		requires=qr.REQUIRES,
	),
	"claim_task_via_mobile": _tool(
		fieldwork.claim_task_via_mobile,
		"MUTATING (default OFF). Take one task from the pool AS THE AUTHENTICATED "
		"CALLER — claim_farm_task with the worker resolved from the request instead "
		"of named in the body.\n\n"
		"IT ADDS NO RULE AND WEAKENS NONE. The three-concurrent-claim limit, the "
		"refusal to self-pick Dispatched work and the refusal of a Draft all still "
		"come from claim_farm_task, because it IS claim_farm_task — a wrapper with "
		"its own copy of those rules would be a second set to keep in step.",
		{
			"task_name": _field(_STRING, "The Farm Task docname."),
			"task": _field(_STRING, "Alias for task_name."),
			"user": _field(_STRING, "Only when the request carries no per-user credential."),
		},
		mutating=True,
		title="Claim a task (mobile)",
		available=_needs_doctype("Farm Task"),
		requires="the Farm Task DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"start_task_via_mobile": _tool(
		fieldwork.start_task_via_mobile,
		"MUTATING (default OFF). Clock in on one claimed task as the authenticated "
		"caller — start_farm_task with the worker resolved from the request. Pass "
		"the assignment, or the task and its live assignment is used.\n\n"
		"This is the clock-in for the TASK, not for the shift: a worker on the "
		"clock all morning did this particular cabin between ten and half past.",
		{
			"assignment_name": _field(_STRING, "The Farm Task Assignment docname."),
			"assignment": _field(_STRING, "Alias for assignment_name."),
			"task_name": _field(_STRING, "The Farm Task docname — its live assignment is used."),
			"task": _field(_STRING, "Alias for task_name."),
			"started_at": _field(_STRING, "Override the clock-in time. Defaults to now."),
			"user": _field(_STRING, "Only when the request carries no per-user credential."),
		},
		mutating=True,
		title="Start a task (mobile)",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	"complete_task_via_mobile": _tool(
		fieldwork.complete_task_via_mobile,
		"MUTATING (default OFF). Finish one task as the authenticated caller: check "
		"the evidence against the contract, file it, and WRITE THE COMPLIANCE "
		"RECORD the task promised.\n\n"
		"`evidence` IS A LIST OF FILE REFERENCES, NOT BYTES. The photographs go up "
		"first through stage_file_chunk / commit_staged_file; this call carries "
		"their docnames.\n\n"
		"NOTE THE findings_text RULE: PASS AN EMPTY STRING to record that nothing "
		"was wrong. A clean inspection is a positive statement, and leaving the "
		"argument out records that nobody was asked — the two are distinguished and "
		"this wrapper preserves the distinction.\n\n"
		"Every refusal comes from complete_farm_task and is unchanged: a submission "
		"short of the evidence contract, a completion filed by anybody but the "
		"worker holding the task, an unclaimed task. A completion whose record "
		"found something lands in Awaiting-Review — the work IS done and the "
		"register IS updated; what needs a person is the finding.\n\n"
		"v0.20.1: SAFE TO SEND TWICE. An identical resubmission of a completion "
		"already on record returns that completion with `x_idempotent: true` and "
		"changes nothing — no second compliance record, no duplicated evidence. "
		"That is for the client which cannot know whether its request landed "
		"before the connection dropped. A submission that DIFFERS — another "
		"worker, other evidence, a different account of the work — is still "
		"refused as the conflict it is.",
		{
			"assignment_name": _field(_STRING, "The Farm Task Assignment docname."),
			"assignment": _field(_STRING, "Alias for assignment_name."),
			"task_name": _field(_STRING, "The Farm Task docname — its live assignment is used."),
			"task": _field(_STRING, "Alias for task_name."),
			"evidence": _field(
				{"type": "array", "items": {"type": ["string", "object"]}},
				"File docnames from commit_staged_file, file URLs, or objects like "
				'{"file": "...", "evidence_type": "Photo", "caption": "north wall"}. Max 40.',
			),
			"evidence_files": _field(
				{"type": "array", "items": {"type": ["string", "object"]}}, "Alias for evidence."
			),
			"signature_file": _field(_STRING, "The signature capture's file URL or File docname."),
			"completion_narrative": _field(_STRING, "What the worker did, in their words."),
			"findings_text": _field(_STRING, "What was WRONG. An empty string records that nothing was."),
			"witness": _field(_STRING, "Somebody else who was there, where the contract asks."),
			"farm_location_gps": _field(
				_STRING,
				'WHERE the work was done — "45.5152,-122.6784" or a place name like '
				'"MC-Cabin-01". FSMA §112.161(a)(1)(i) asks an activity record for the farm\'s '
				"name AND its location; this is the second half. Optional, and only written "
				"when given — an empty value leaves any location already on the assignment alone.",
			),
			"actual_duration_minutes": _field(_INTEGER, "Minutes spent."),
			"completed_at": _field(_STRING, "Override the clock-out time. Defaults to now."),
			"record_data": _field(
				_OBJECT,
				"Extra fields for the compliance record — laboratory results, a detector's pass/fail.",
			),
			"visit_id": _field(
				_STRING,
				"The trip this completion belongs to — one identifier the client mints when a "
				"worker arrives somewhere and reuses for every task they close before they "
				"leave. Five cabins on one walk is ONE visit and five completions. Free text; "
				"unvalidated. list_visits reports the rollup.",
			),
			"user": _field(_STRING, "Only when the request carries no per-user credential."),
		},
		mutating=True,
		title="Complete a task (mobile)",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
	# ── v0.20.1: the trip, rather than the tasks it contained ───────────────
	"list_visits": _tool(
		visits.list_visits,
		"Completed task assignments grouped into the TRIPS their handsets "
		"recorded. Five cabins closed on one walk to the north block is ONE visit "
		"and five completions. Read-only.\n\n"
		"THE GROUPING IS THE HANDSET'S, NOT A GUESS FROM TIMESTAMPS. The app mints "
		"a `visit_id` when a worker arrives somewhere and reuses it for every task "
		"they close before they leave, because the phone is the only thing that "
		"was there. Two cabins forty minutes apart on one unhurried walk are one "
		"trip; two a minute apart from opposite ends of the property are two, and "
		"no timestamp threshold gets both right.\n\n"
		"A COMPLETION WITH NO `visit_id` IS IN NO VISIT — not in a synthetic "
		"one-task visit, and not in an 'unassigned' bucket dressed as a trip. "
		"Everything filed before v0.20.1 is in that group; `ungrouped_completions` "
		"reports how many.\n\n"
		"ONE-TASK VISITS ARE RETURNED. Somebody drove out, did one job and drove "
		"back, which is exactly what a question about wasted travel is looking "
		"for. `single_task_visits` counts them so a caller can filter knowingly.\n\n"
		"`duration_minutes` IS FIRST COMPLETION TO LAST and says nothing about the "
		"drive out or the walk in — a one-task visit measures zero, because one "
		"completion is one instant. `total_evidence_files` counts distinct FILES "
		"and not evidence rows: one signature filed against three cabins is one "
		"photograph.\n\n"
		"Scoped to the companies the calling account may actually reach.",
		{
			"company": _COMPANY,
			"worker": _field(_STRING, "Whose visits. Docname, employee number, name or login."),
			"location": _field(
				_STRING,
				"Visits that touched this place — the Farm Task's `location` docname. A trip "
				"that also went elsewhere is returned WHOLE, with its other tasks: reporting "
				"a visit with half its work missing would answer a different question.",
			),
			"from_date": _field(_STRING, "Earliest completion date, YYYY-MM-DD."),
			"to_date": _field(_STRING, "Latest completion date, YYYY-MM-DD."),
			"limit": _field(_INTEGER, "Maximum visits. Default 100, hard maximum 500."),
		},
		title="List visits",
		available=_needs_doctype("Farm Task Assignment"),
		requires="the Farm Task Assignment DocType, which ships with erpnext_mcp — run `bench migrate`",
	),
}

#: Tool names in catalogue order, read tools first. Used by the settings doctype
#: generator and the docs to stay in step with this file.
READ_TOOLS = tuple(name for name, spec in TOOLS.items() if not spec["mutating"])
MUTATING_TOOLS = tuple(name for name, spec in TOOLS.items() if spec["mutating"])

#: THE MUTATING TOOLS THAT SHIP WITH THEIR SWITCH ON, and the argument for each.
#:
#: "Mutating tools default off" is one of this app's two or three load-bearing
#: promises, and a promise with an undocumented exception is not a promise. So the
#: exceptions live here, by name, with their reasoning — one place a reader can
#: check, one place a test asserts against, and a new exception cannot arrive
#: without somebody writing the sentence that justifies it.
#:
#: The settings form's "mutating tools are live" warning skips these, because a
#: warning that fires on every save of a default configuration is noise, and its
#: whole job is to make enabling `submit_journal_entry` conspicuous.
DEFAULT_ON_MUTATING_TOOLS = {
	"install_compliance_fields": (
		"It is an INSTALLER, not a writer of anybody's data: it adds columns to a schema "
		"and touches no record. A compliance field that arrives only when an operator "
		"remembers to tick a box is a compliance field that is missing on the sites that "
		"needed it most — the applicator's name would be absent from exactly the spray "
		"records a Worker Protection Standard inspection asks for. Turning it off is a "
		"real and supported choice, and then no field is ever added. See "
		"erpnext_mcp/compliance_fields.py for the whole argument, and `before_uninstall` "
		"for what it costs."
	),
}


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
