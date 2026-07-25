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
from .tools import collab, files, hr, meta, mutate, read, reports, trade, workflow

_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_INTEGER = {"type": "integer"}
_BOOLEAN = {"type": "boolean"}
_OBJECT = {"type": "object"}


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
					"reference_name, user_remark, exchange_rate."
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
		"Frappe's own apply_workflow — the same path the Desk button uses, so the "
		"state change and any resulting submit or cancel behave identically. "
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
		},
		required=("doctype", "name", "action"),
		mutating=True,
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
		audit.record(
			tool_name,
			arguments,
			audit.STATUS_SUCCESS,
			result.summary,
			docstatus_delta=result.docstatus_delta,
			caller_ip=caller_ip,
		)
		return ok_result(result.data)

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


def ok_result(data) -> dict:
	return {
		"content": [{"type": "text", "text": json.dumps(data, default=str, indent=2)}],
		"isError": False,
	}


def error_result(message: str) -> dict:
	return {"content": [{"type": "text", "text": message}], "isError": True}
