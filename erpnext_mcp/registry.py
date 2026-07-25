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
the audit row, the rollback-on-failure and the never-raise contract cannot be
bypassed by adding a tool: a handler that is not in `TOOLS` is not reachable,
and one that is gets all four for free.
"""

import json

import frappe

from . import audit, settings
from .compat import traceback_text
from .errors import ToolError
from .result import ToolResult
from .tools import mutate, read

_STRING = {"type": "string"}
_NUMBER = {"type": "number"}
_INTEGER = {"type": "integer"}


def _field(kind: dict, description: str) -> dict:
	return {**kind, "description": description}


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
) -> dict:
	"""One catalogue entry.

	`annotations` are MCP's standard hints. `readOnlyHint` is the inverse of
	`mutating` by construction rather than by hand, so the two can never drift
	apart and let a write tool advertise itself as safe.
	"""
	return {
		"handler": handler,
		"mutating": mutating,
		"description": description,
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
_LIMIT = _field(
	_INTEGER, "Maximum rows to return. Default 100, hard maximum 500."
)


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
			"as_of": _field(
				_STRING, "Balance date as YYYY-MM-DD. Defaults to today."
			),
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
				"Only entries with this account on one of their lines. Docname, "
				"number or name.",
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
			"bank_account": _field(
				_STRING, "Bank Account docname or its account_name. Omit for all."
			),
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
		"of the doctype carries. Only available on ERPNext versions that ship the "
		"Bank Statement doctype — get_company_topology reports whether this site "
		"has it, and this tool says so plainly if not. Read-only.",
		{"name": _field(_STRING, "Bank Statement docname.")},
		required=("name",),
		title="Bank statement detail",
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
			"root_type": _field(
				_STRING, "One of Asset, Liability, Income, Expense, Equity."
			),
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
			"bank_account": _field(
				_STRING, "Bank Account docname or its account_name."
			),
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
						"account": _field(
							_STRING, "Account docname, number or name."
						),
						"debit": _field(_NUMBER, "Debit amount, positive."),
						"credit": _field(_NUMBER, "Credit amount, positive."),
						"party_type": _field(
							_STRING, "'Customer', 'Supplier', 'Employee', …"
						),
						"party": _field(_STRING, "Party docname."),
						"cost_center": _field(_STRING, "Cost Center docname."),
						"project": _field(_STRING, "Project docname."),
						"reference_type": _field(
							_STRING, "Doctype this line settles, e.g. 'Sales Invoice'."
						),
						"reference_name": _field(_STRING, "That document's name."),
						"user_remark": _field(_STRING, "Per-line remark."),
						"exchange_rate": _field(
							_NUMBER, "Required for a foreign-currency account."
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
			"reason": _field(
				_STRING, "Why it is being cancelled. Recorded permanently."
			),
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
			"bank_account": _field(
				_STRING, "Bank Account docname or its account_name."
			),
			"date": _field(_STRING, "Transaction date, YYYY-MM-DD."),
			"amount": _field(
				_NUMBER, "Signed amount: positive = money in, negative = money out."
			),
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
							"Voucher doctype, e.g. 'Payment Entry', "
							"'Journal Entry', 'Sales Invoice'.",
						),
						"payment_entry": _field(_STRING, "That document's name."),
						"allocated_amount": _field(
							_NUMBER, "Amount to allocate, positive."
						),
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
}

#: Tool names in catalogue order, read tools first. Used by the settings doctype
#: generator and the docs to stay in step with this file.
READ_TOOLS = tuple(name for name, spec in TOOLS.items() if not spec["mutating"])
MUTATING_TOOLS = tuple(name for name, spec in TOOLS.items() if spec["mutating"])


def tools_list() -> dict:
	"""The `tools/list` payload: only tools whose switch is on.

	A tool an operator has disabled is not advertised at all, rather than
	advertised and then refused. A model cannot be tempted by a tool it cannot
	see, and this keeps the catalogue an honest description of what will work.
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
			if settings.tool_enabled(name)
		]
	}


def dispatch(tool_name: str, arguments: dict, caller_ip: str = "") -> dict:
	"""Run one tool and return an MCP `tools/call` result. Never raises.

	Order matters: unknown name, then switch, then run. A disabled tool is never
	handed its arguments, so an argument that would have been rejected by the
	handler cannot leak the fact that the tool exists at all.
	"""
	arguments = arguments if isinstance(arguments, dict) else {}
	spec = TOOLS.get(tool_name)

	if spec is None:
		audit.record(
			tool_name, arguments, audit.STATUS_ERROR, "unknown tool", caller_ip=caller_ip
		)
		return error_result(f"unknown tool {tool_name!r}")

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
