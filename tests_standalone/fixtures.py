# SPDX-License-Identifier: MIT
"""A small, entirely invented ERPNext site for the standalone tests.

Two companies on purpose. A single-company fixture would let
`resolve_company`'s inference hide every place a tool needs an explicit company,
and "works on my one-company site" is exactly the bug class this app has to
avoid. So the default fixture has two, with overlapping account *names* under
different docnames — which is what makes the ambiguity paths in
`args.resolve_account` testable.

Nothing here refers to a real business, account number or person. The numbers
are a plain textbook chart of accounts.
"""

import json
import sys
import types

from .harness import STORE, MCPTestCase, add_field, register_doctype, set_roles

MAIN = "Example Trading Co"
OTHER = "Second Example Ltd"
MAIN_ABBR = "ETC"
OTHER_ABBR = "SEL"


def seed_site() -> None:
	"""Load the whole fixture. Call after `STORE.reset()`."""
	_modules()
	_currencies()
	_companies()
	_fiscal_years()
	_accounts()
	_cost_centers()
	_gl_entries()
	_journal_entries()
	_banking()


def _modules() -> None:
	"""Module Defs, because a generated DocType has to belong to one.

	`Accounts` and `Custom` are the two `create_accounting_dimension` looks for,
	in that order. Both are here so the preferred branch is the one the tests
	exercise; a test that wants the fallback removes the Accounts row.
	"""
	STORE.seed(
		"Module Def",
		[
			{"name": "Accounts", "app_name": "erpnext"},
			{"name": "Custom", "app_name": "frappe"},
		],
	)


def _currencies() -> None:
	"""Two, so `create_account`'s currency check has something to reject."""
	STORE.seed("Currency", [{"name": "USD", "enabled": 1}, {"name": "CAD", "enabled": 1}])


def _companies() -> None:
	STORE.seed(
		"Company",
		[
			{
				"name": MAIN,
				"abbr": MAIN_ABBR,
				"default_currency": "USD",
				"country": "United States",
				"chart_of_accounts": "Standard",
				"is_group": 0,
				"cost_center": f"Main - {MAIN_ABBR}",
			},
			{
				"name": OTHER,
				"abbr": OTHER_ABBR,
				"default_currency": "USD",
				"country": "United States",
				"chart_of_accounts": "Standard",
				"is_group": 0,
				# No cost_center: a company created before its chart of accounts
				# is a legitimate half-set-up state, and topology must not choke.
			},
		],
	)


def _fiscal_years() -> None:
	STORE.seed(
		"Fiscal Year",
		[
			{
				"name": "2026",
				"year": "2026",
				"year_start_date": "2026-01-01",
				"year_end_date": "2026-12-31",
				"disabled": 0,
				"companies": [{"company": MAIN}],
			},
			{
				"name": "2025",
				"year": "2025",
				"year_start_date": "2025-01-01",
				"year_end_date": "2025-12-31",
				"disabled": 0,
				# No company rows: applies to every company.
				"companies": [],
			},
		],
	)


#: (account_name, number, root_type, account_type, is_group, parent)
_CHART = [
	("Application of Funds (Assets)", "", "Asset", "", 1, None),
	("Current Assets", "1000", "Asset", "", 1, "Application of Funds (Assets)"),
	("Cash", "1100", "Asset", "Cash", 0, "Current Assets"),
	("Bank Checking", "1110", "Asset", "Bank", 0, "Current Assets"),
	("Cash Clearing", "1190", "Asset", "Bank", 0, "Current Assets"),
	("Source of Funds (Liabilities)", "", "Liability", "", 1, None),
	("Accounts Payable", "2100", "Liability", "Payable", 0, "Source of Funds (Liabilities)"),
	("Income", "", "Income", "", 1, None),
	("Sales", "4100", "Income", "", 0, "Income"),
	("Expenses", "", "Expense", "", 1, None),
	("Office Supplies", "5100", "Expense", "", 0, "Expenses"),
]


def _label(account_name: str) -> str:
	"""ERPNext's Account docname stem: `"<number> - <name>"` when numbered.

	Parent links have to use the docname, not the account name — getting that
	wrong is how a chart-of-accounts tree comes back flat, so the fixture derives
	it rather than spelling it out twice.
	"""
	for name, number, *_rest in _CHART:
		if name == account_name:
			return f"{number} - {name}" if number else name
	raise KeyError(account_name)


def _accounts() -> None:
	rows = []
	counter = 0
	for company, abbr in ((MAIN, MAIN_ABBR), (OTHER, OTHER_ABBR)):
		for account_name, number, root_type, account_type, is_group, parent in _CHART:
			counter += 1
			label = _label(account_name)
			rows.append(
				{
					"name": f"{label} - {abbr}",
					"account_name": account_name,
					"account_number": number,
					"parent_account": f"{_label(parent)} - {abbr}" if parent else "",
					"is_group": is_group,
					"root_type": root_type,
					"account_type": account_type,
					"account_currency": "USD",
					"disabled": 0,
					"company": company,
					"lft": counter * 2,
					"rgt": counter * 2 + 1,
				}
			)
	STORE.seed("Account", rows)


def cash(company_abbr: str = MAIN_ABBR) -> str:
	return f"1100 - Cash - {company_abbr}"


def sales(company_abbr: str = MAIN_ABBR) -> str:
	return f"4100 - Sales - {company_abbr}"


def supplies(company_abbr: str = MAIN_ABBR) -> str:
	return f"5100 - Office Supplies - {company_abbr}"


#: (cost_center_name, number, is_group, parent_name, disabled)
#:
#: Shaped like a real one: the root is named after the company (ERPNext's own
#: rule, and what makes `create_cost_center` refuse a second root), `Main` is the
#: unnumbered default every company gets, and `Operations` is a numbered group
#: with a numbered child — which is what the renumber and rename paths need.
_COST_CENTERS = [
	("__company__", "", 1, None, 0),
	("Main", "", 0, "__company__", 0),
	("Operations", "100", 1, "__company__", 0),
	("Field Work", "110", 0, "Operations", 0),
	("Retired Depot", "190", 0, "Operations", 1),
]


def _cost_center_docname(cost_center_name: str, number: str, abbr: str) -> str:
	parts = [cost_center_name, abbr]
	if number:
		parts.insert(0, number)
	return " - ".join(parts)


def cost_center(name: str, company_abbr: str = MAIN_ABBR) -> str:
	"""A fixture cost center's docname, from its plain name."""
	company = MAIN if company_abbr == MAIN_ABBR else OTHER
	for cost_center_name, number, *_rest in _COST_CENTERS:
		plain = company if cost_center_name == "__company__" else cost_center_name
		if plain == name or cost_center_name == name:
			return _cost_center_docname(plain, number, company_abbr)
	raise KeyError(name)


def _cost_centers() -> None:
	rows = []
	counter = 0
	for company, abbr in ((MAIN, MAIN_ABBR), (OTHER, OTHER_ABBR)):
		for cost_center_name, number, is_group, parent, disabled in _COST_CENTERS:
			counter += 1
			plain = company if cost_center_name == "__company__" else cost_center_name
			parent_plain = company if parent == "__company__" else parent
			rows.append(
				{
					"name": _cost_center_docname(plain, number, abbr),
					"cost_center_name": plain,
					"cost_center_number": number,
					"parent_cost_center": (cost_center(parent_plain, abbr) if parent else ""),
					"is_group": is_group,
					"disabled": disabled,
					"company": company,
					"lft": counter * 2,
					"rgt": counter * 2 + 1,
				}
			)
	STORE.seed("Cost Center", rows)


def _gl_entries() -> None:
	STORE.seed(
		"GL Entry",
		[
			# Cash: 1000 in, 250 out, plus a cancelled 9999 that must be ignored.
			_gl(cash(), "2026-01-15", debit=1000),
			_gl(cash(), "2026-02-10", credit=250),
			_gl(cash(), "2026-03-01", debit=9999, is_cancelled=1),
			# ...and one after the as_of date the balance tests use, with its
			# counterpart. Double entry has to hold across the whole fixture:
			# fiscal_year_audit_packet checks that cumulative debits equal
			# cumulative credits, and it was this stray 500 that first failed it.
			_gl(cash(), "2026-12-31", debit=500),
			_gl(sales(), "2026-12-31", credit=500),
			_gl(sales(), "2026-01-15", credit=1000),
			_gl(supplies(), "2026-02-10", debit=250),
		],
	)


def _gl(account, posting_date, debit=0, credit=0, is_cancelled=0):
	return {
		"account": account,
		"posting_date": posting_date,
		"debit": debit,
		"credit": credit,
		"company": MAIN,
		"is_cancelled": is_cancelled,
		"voucher_type": "Journal Entry",
		"voucher_no": "ACC-JV-2026-00001",
	}


def _je_line(account, idx, debit=0, credit=0):
	"""One Journal Entry Account row, with both amount columns ERPNext stores.

	A real row always carries `debit_in_account_currency` as well as `debit` —
	ERPNext derives the second from the first on every validate, and a fixture
	giving only `debit` is a row no site has ever had. It is also a row that
	cannot be submitted, now that `harness.JournalEntryDocument` models that
	derivation, which is exactly the failure v0.8.0 shipped.
	"""
	return {
		"account": account,
		"idx": idx,
		"debit": debit,
		"credit": credit,
		"debit_in_account_currency": debit,
		"credit_in_account_currency": credit,
		"exchange_rate": 1,
	}


def _journal_entries() -> None:
	STORE.seed(
		"Journal Entry",
		[
			{
				"name": "ACC-JV-2026-00001",
				"posting_date": "2026-01-15",
				"company": MAIN,
				"voucher_type": "Journal Entry",
				"total_debit": 1000,
				"total_credit": 1000,
				"user_remark": "Opening sale",
				"docstatus": 1,
				"accounts": [
					_je_line(cash(), 1, debit=1000),
					_je_line(sales(), 2, credit=1000),
				],
			},
			{
				"name": "ACC-JV-2026-00002",
				"posting_date": "2026-02-10",
				"company": MAIN,
				"voucher_type": "Journal Entry",
				"total_debit": 250,
				"total_credit": 250,
				"user_remark": "Stationery",
				"docstatus": 0,
				"accounts": [
					_je_line(supplies(), 1, debit=250),
					_je_line(cash(), 2, credit=250),
				],
			},
			{
				"name": "ACC-JV-2025-00009",
				"posting_date": "2025-06-01",
				"company": MAIN,
				"voucher_type": "Journal Entry",
				"total_debit": 40,
				"total_credit": 40,
				"user_remark": "Prior year",
				"docstatus": 1,
				"accounts": [
					_je_line(supplies(), 1, debit=40),
					_je_line(cash(), 2, credit=40),
				],
			},
		],
	)


BANK_ACCOUNT = "Operating - Example Bank"


def _banking() -> None:
	STORE.seed("Bank", [{"name": "Example Bank", "bank_name": "Example Bank"}])
	STORE.seed(
		"Bank Account",
		[
			{
				"name": BANK_ACCOUNT,
				"account_name": "Operating",
				"bank": "Example Bank",
				"company": MAIN,
				"account": f"1110 - Bank Checking - {MAIN_ABBR}",
				"is_company_account": 1,
			}
		],
	)
	STORE.seed(
		"Bank Transaction",
		[
			{
				"name": "BT-2026-0001",
				"date": "2026-01-15",
				"bank_account": BANK_ACCOUNT,
				"company": MAIN,
				"description": "Customer deposit",
				"status": "Unreconciled",
				"deposit": 1000,
				"withdrawal": 0,
				"allocated_amount": 0,
				"unallocated_amount": 1000,
				"currency": "USD",
				"docstatus": 1,
				"payment_entries": [],
			},
			{
				"name": "BT-2026-0002",
				"date": "2026-02-10",
				"bank_account": BANK_ACCOUNT,
				"company": MAIN,
				"description": "Supplier payment",
				"status": "Reconciled",
				"deposit": 0,
				"withdrawal": 250,
				"allocated_amount": 250,
				"unallocated_amount": 0,
				"currency": "USD",
				"docstatus": 1,
				"payment_entries": [
					{
						"payment_document": "Payment Entry",
						"payment_entry": "PE-0001",
						"allocated_amount": 250,
					}
				],
			},
		],
	)
	STORE.seed(
		"Payment Entry",
		[
			{"name": "PE-0001", "posting_date": "2026-02-10", "paid_amount": 250, "docstatus": 1},
			{"name": "PE-0002", "posting_date": "2026-01-16", "paid_amount": 400, "docstatus": 1},
		],
	)
	STORE.seed(
		"Bank Statement",
		[
			{
				"name": "BS-2026-01",
				"bank_account": BANK_ACCOUNT,
				"from_date": "2026-01-01",
				"to_date": "2026-01-31",
				"opening_balance": 0,
				"closing_balance": 1000,
				"company": MAIN,
			}
		],
	)
	#: Administrator is seeded because EVERY Frappe site has it and this app writes
	#: it into Link-to-User columns: `settings.FALLBACK_USER` is what a mutation is
	#: attributed to when no MCP System User is configured, and it is what
	#: `frappe.session.user` reads as in this double. Without the row, a Link field
	#: holding it fails `_validate_links` — which is the double refusing something
	#: the real framework accepts, the mirror of the v0.12.1 failure the link
	#: validation was added for.
	STORE.seed(
		"User",
		[
			{"name": "Administrator", "enabled": 1, "full_name": "Administrator"},
			{"name": "mcp@example.test", "enabled": 1, "full_name": "MCP Bot"},
		],
	)


class SeededTestCase(MCPTestCase):
	"""MCPTestCase plus the fixture site. What most tests want."""

	def setUp(self):
		super().setUp()
		seed_site()


# ── v0.2.0 fixtures ─────────────────────────────────────────────────────────
#: A synthetic approval workflow over Purchase Order. Three transitions, one
#: terminal pair, one condition and one self-approval rule — the four shapes the
#: workflow tools have to get right.
WORKFLOW_NAME = "Purchase Order Approval"
BUYER = "buyer@example.test"
APPROVER = "approver@example.test"


def seed_v2() -> None:
	"""Everything the v0.2.0 tool categories read. Additive to `seed_site`."""
	_people()
	_workflow()
	_trade()
	_reports()
	_attachments()
	_collab()
	_customisation()


def _people() -> None:
	STORE.seed(
		"User",
		[
			{"name": BUYER, "enabled": 1, "full_name": "Bea Buyer"},
			{"name": APPROVER, "enabled": 1, "full_name": "Avi Approver"},
			{"name": "retired@example.test", "enabled": 0, "full_name": "Rex Retired"},
		],
	)
	set_roles(BUYER, ["Purchase User"])
	set_roles(APPROVER, ["Purchase Manager", "Purchase User"])
	set_roles("retired@example.test", [])


def _workflow() -> None:
	STORE.seed(
		"Workflow",
		[
			{
				"name": WORKFLOW_NAME,
				"workflow_name": WORKFLOW_NAME,
				"document_type": "Purchase Order",
				"is_active": 1,
				"workflow_state_field": "workflow_state",
				"send_email_alert": 1,
				"states": [
					{"state": "Draft", "doc_status": 0, "allow_edit": "Purchase User"},
					{
						"state": "Pending Approval",
						"doc_status": 0,
						"allow_edit": "Purchase Manager",
					},
					{"state": "Approved", "doc_status": 1, "allow_edit": "Purchase Manager"},
					{"state": "Rejected", "doc_status": 0, "allow_edit": "Purchase Manager"},
				],
				"transitions": [
					{
						"state": "Draft",
						"action": "Submit for Approval",
						"next_state": "Pending Approval",
						"allowed": "Purchase User",
						"allow_self_approval": 1,
					},
					{
						"state": "Pending Approval",
						"action": "Approve",
						"next_state": "Approved",
						"allowed": "Purchase Manager",
						# Nobody approves their own order — the rule the fallback
						# path cannot enforce, which is why the tools delegate.
						"allow_self_approval": 0,
					},
					{
						"state": "Pending Approval",
						"action": "Reject",
						"next_state": "Rejected",
						"allowed": "Purchase Manager",
						"allow_self_approval": 1,
						"condition": "doc.grand_total > 0",
					},
				],
			},
			{
				"name": "Inactive Example",
				"workflow_name": "Inactive Example",
				"document_type": "Sales Order",
				"is_active": 0,
				"workflow_state_field": "workflow_state",
				"states": [{"state": "Draft", "doc_status": 0}],
				"transitions": [],
			},
		],
	)


def _trade() -> None:
	STORE.seed(
		"Purchase Order",
		[
			{
				"name": "PUR-ORD-2026-00001",
				"supplier": "Example Supplies Inc",
				"supplier_name": "Example Supplies Inc",
				"transaction_date": "2026-06-02",
				"schedule_date": "2026-06-20",
				"grand_total": 4200.0,
				"currency": "USD",
				"status": "To Receive and Bill",
				"per_received": 0,
				"per_billed": 0,
				"docstatus": 0,
				"company": MAIN,
				"owner": BUYER,
				"workflow_state": "Pending Approval",
			},
			{
				"name": "PUR-ORD-2026-00002",
				"supplier": "Second Supplier LLC",
				"supplier_name": "Second Supplier LLC",
				"transaction_date": "2026-05-11",
				"schedule_date": "2026-05-30",
				"grand_total": 875.5,
				"currency": "USD",
				"status": "Completed",
				"per_received": 100,
				"per_billed": 100,
				"docstatus": 1,
				"company": MAIN,
				"owner": BUYER,
				"workflow_state": "Approved",
			},
			{
				"name": "PUR-ORD-2026-00003",
				"supplier": "Example Supplies Inc",
				"supplier_name": "Example Supplies Inc",
				"transaction_date": "2026-06-18",
				"schedule_date": "2026-07-01",
				"grand_total": 150.0,
				"currency": "USD",
				"status": "Draft",
				"docstatus": 0,
				"company": MAIN,
				"owner": APPROVER,
				"workflow_state": "Draft",
			},
		],
	)
	STORE.seed(
		"Sales Order",
		[
			{
				"name": "SAL-ORD-2026-00001",
				"customer": "Northwind Grocers",
				"customer_name": "Northwind Grocers",
				"transaction_date": "2026-06-05",
				"delivery_date": "2026-06-19",
				"grand_total": 12500.0,
				"currency": "USD",
				"status": "To Deliver and Bill",
				"per_delivered": 0,
				"per_billed": 0,
				"docstatus": 1,
				"company": MAIN,
			},
			{
				"name": "SAL-ORD-2026-00002",
				"customer": "Southgate Markets",
				"customer_name": "Southgate Markets",
				"transaction_date": "2026-04-22",
				"delivery_date": "2026-05-06",
				"grand_total": 3100.0,
				"currency": "USD",
				"status": "Completed",
				"per_delivered": 100,
				"per_billed": 100,
				"docstatus": 1,
				"company": MAIN,
			},
		],
	)
	# Ageing fixture: as_of 2026-07-24 puts one invoice in each bucket.
	STORE.seed(
		"Sales Invoice",
		[
			_invoice("ACC-SINV-2026-00001", "Northwind Grocers", "2026-08-30", 1000, 1000),
			_invoice("ACC-SINV-2026-00002", "Northwind Grocers", "2026-07-10", 2000, 500),
			_invoice("ACC-SINV-2026-00003", "Southgate Markets", "2026-06-10", 3000, 3000),
			_invoice("ACC-SINV-2026-00004", "Southgate Markets", "2026-05-10", 4000, 4000),
			_invoice("ACC-SINV-2026-00005", "Westbrook Cafe", "2026-01-02", 5000, 5000),
			_invoice("ACC-SINV-2026-00006", "Westbrook Cafe", None, 600, 600),
			# Settled and cancelled rows, which must not appear at all.
			_invoice("ACC-SINV-2026-00007", "Northwind Grocers", "2026-06-01", 900, 0),
			_invoice("ACC-SINV-2026-00008", "Northwind Grocers", "2026-06-01", 700, 700, docstatus=2),
		],
	)


def _invoice(name, customer, due_date, grand_total, outstanding, docstatus=1):
	return {
		"name": name,
		"customer": customer,
		"customer_name": customer,
		"posting_date": "2026-05-01",
		"due_date": due_date,
		"grand_total": grand_total,
		"outstanding_amount": outstanding,
		"currency": "USD",
		"status": "Overdue" if outstanding else "Paid",
		"company": MAIN,
		"docstatus": docstatus,
	}


def _reports() -> None:
	STORE.seed(
		"Report",
		[
			{
				"name": "Accounts Receivable Summary",
				"report_name": "Accounts Receivable Summary",
				"ref_doctype": "Sales Invoice",
				"report_type": "Script Report",
				"module": "Accounts",
				"is_standard": "Yes",
				"disabled": 0,
			},
			{
				"name": "Cash Movement",
				"report_name": "Cash Movement",
				"ref_doctype": "GL Entry",
				"report_type": "Query Report",
				"module": "Accounts",
				"is_standard": "No",
				"disabled": 0,
			},
			{
				"name": "Open Purchase Orders",
				"report_name": "Open Purchase Orders",
				"ref_doctype": "Purchase Order",
				"report_type": "Report Builder",
				"module": "Buying",
				"is_standard": "No",
				"disabled": 0,
				"json": json.dumps(
					{
						"columns": [
							["name", "Purchase Order"],
							["supplier", "Purchase Order"],
							["grand_total", "Purchase Order"],
						],
						"filters": [["Purchase Order", "status", "=", "Draft"]],
						"sort_by": "transaction_date",
						"sort_order": "desc",
					}
				),
			},
			{
				"name": "Retired Report",
				"report_name": "Retired Report",
				"ref_doctype": "GL Entry",
				"report_type": "Query Report",
				"module": "Accounts",
				"is_standard": "No",
				"disabled": 1,
			},
			{
				"name": "Exotic Report",
				"report_name": "Exotic Report",
				"ref_doctype": "GL Entry",
				"report_type": "Custom Report",
				"module": "Accounts",
				"is_standard": "No",
				"disabled": 0,
			},
		],
	)

	def receivable(filters, user):
		rows = [
			{"customer": "Northwind Grocers", "outstanding": 2500.0},
			{"customer": "Southgate Markets", "outstanding": 7000.0},
		]
		if filters.get("customer"):
			rows = [row for row in rows if row["customer"] == filters["customer"]]
		return {
			"columns": [
				{
					"fieldname": "customer",
					"label": "Customer",
					"fieldtype": "Link",
					"options": "Customer",
					"width": 200,
				},
				"Outstanding:Currency/USD:120",
			],
			"result": rows,
			"message": "Aged as of report date",
		}

	def cash_movement(filters, user):
		return {
			"columns": ["Date:Date:100", "Amount:Currency/USD:120"],
			"result": [["2026-01-15", 1000.0], ["2026-02-10", -250.0]],
		}

	STORE.report_runners["Accounts Receivable Summary"] = receivable
	STORE.report_runners["Cash Movement"] = cash_movement


ATTACHED_JE = "ACC-JV-2026-00001"


def _attachments() -> None:
	STORE.seed(
		"File",
		[
			{
				"name": "file-public-invoice",
				"file_name": "invoice.txt",
				"file_url": "/files/invoice.txt",
				"file_size": 21,
				"is_private": 0,
				"attached_to_doctype": "Journal Entry",
				"attached_to_name": ATTACHED_JE,
				"owner": BUYER,
			},
			{
				"name": "file-private-contract",
				"file_name": "contract.pdf",
				"file_url": "/private/files/contract.pdf",
				"file_size": 9,
				"is_private": 1,
				"attached_to_doctype": "Journal Entry",
				"attached_to_name": ATTACHED_JE,
				"owner": APPROVER,
			},
			{
				"name": "file-huge-export",
				"file_name": "payroll-export.csv",
				"file_url": "/private/files/payroll-export.csv",
				"file_size": 5 * 1024 * 1024,
				"is_private": 1,
				"attached_to_doctype": "Journal Entry",
				"attached_to_name": ATTACHED_JE,
				"owner": APPROVER,
			},
			{
				"name": "file-orphan-private",
				"file_name": "scratch.txt",
				"file_url": "/private/files/scratch.txt",
				"file_size": 7,
				"is_private": 1,
				"owner": APPROVER,
			},
		],
	)
	STORE.file_contents["file-public-invoice"] = b"invoice line one\r\n---"
	STORE.file_contents["file-private-contract"] = b"top secret"
	STORE.file_contents["file-huge-export"] = b"x" * (5 * 1024 * 1024)
	STORE.file_contents["file-orphan-private"] = b"scratch"


def _collab() -> None:
	STORE.seed(
		"Comment",
		[
			{
				"name": "comment-1",
				"comment_type": "Comment",
				"content": "Checked against the bank statement, agrees.",
				"comment_by": "Avi Approver",
				"comment_email": APPROVER,
				"reference_doctype": "Journal Entry",
				"reference_name": ATTACHED_JE,
				"owner": APPROVER,
				"creation": "2026-01-16 09:00:00",
			},
			{
				"name": "comment-2",
				"comment_type": "Info",
				"content": "Submitted by Administrator",
				"reference_doctype": "Journal Entry",
				"reference_name": ATTACHED_JE,
				"owner": "Administrator",
				"creation": "2026-01-16 10:00:00",
			},
			{
				"name": "comment-3",
				"comment_type": "Comment",
				"content": "Unrelated thread.",
				"reference_doctype": "Journal Entry",
				"reference_name": "ACC-JV-2026-00002",
				"owner": BUYER,
				"creation": "2026-02-11 08:00:00",
			},
		],
	)
	STORE.seed(
		"ToDo",
		[
			{
				"name": "todo-open-overdue",
				"status": "Open",
				"priority": "High",
				"date": "2026-06-01",
				"description": "Chase Northwind on ACC-SINV-2026-00003",
				"reference_type": "Sales Invoice",
				"reference_name": "ACC-SINV-2026-00003",
				"allocated_to": APPROVER,
				"assigned_by": "Administrator",
				"owner": "Administrator",
			},
			{
				"name": "todo-open-future",
				"status": "Open",
				"priority": "Medium",
				"date": "2026-12-01",
				"description": "Year-end close checklist",
				"allocated_to": APPROVER,
				"owner": "Administrator",
			},
			{
				"name": "todo-closed",
				"status": "Closed",
				"priority": "Low",
				"date": "2026-03-01",
				"description": "Old task",
				"allocated_to": BUYER,
				"owner": "Administrator",
			},
		],
	)


def _customisation() -> None:
	STORE.seed(
		"Custom Field",
		[
			{
				"name": "Journal Entry-orchard_block",
				"dt": "Journal Entry",
				"fieldname": "orchard_block",
				"label": "Block",
				"fieldtype": "Data",
				"insert_after": "user_remark",
				"idx": 1,
				"hidden": 0,
				"reqd": 0,
			},
			{
				"name": "Journal Entry-hidden_note",
				"dt": "Journal Entry",
				"fieldname": "hidden_note",
				"label": "Hidden Note",
				"fieldtype": "Small Text",
				"insert_after": "orchard_block",
				"idx": 2,
				"hidden": 1,
				"reqd": 0,
			},
			{
				"name": "Sales Order-delivery_window",
				"dt": "Sales Order",
				"fieldname": "delivery_window",
				"label": "Delivery Window",
				"fieldtype": "Select",
				"insert_after": "delivery_date",
				"idx": 1,
			},
		],
	)
	STORE.seed(
		"Client Script",
		[
			{
				"name": "Journal Entry autofill",
				"dt": "Journal Entry",
				"view": "Form",
				"enabled": 1,
				"script_type": "Client",
				"script": "frappe.ui.form.on('Journal Entry', {\n" + ("// padding\n" * 120),
			},
			{
				"name": "Sales Order legacy",
				"dt": "Sales Order",
				"view": "Form",
				"enabled": 0,
				"script_type": "Client",
				"script": "console.log('disabled');",
			},
		],
	)


# ── HR, which only exists on sites with the hrms app ────────────────────────
def install_hrms() -> None:
	"""Make the fake site look like it has Frappe HR, and seed HR data.

	Also registers a stand-in for HR's `get_leave_balance_on`, because the leave
	tool delegates to it and a test that skipped the delegation would not be
	testing the tool that ships.
	"""
	if "hrms" not in STORE.installed_apps:
		STORE.installed_apps.append("hrms")
	_hr_data()
	_install_leave_api()


EMPLOYEES = ("HR-EMP-00001", "HR-EMP-00002", "HR-EMP-00003")


def _hr_data() -> None:
	STORE.seed(
		"Employee",
		[
			{
				"name": "HR-EMP-00001",
				"employee_name": "Ada Orchard",
				"employee_number": "E-100",
				"department": "Operations",
				"designation": "Supervisor",
				"status": "Active",
				"date_of_joining": "2024-03-01",
				"company": MAIN,
				"user_id": APPROVER,
			},
			{
				"name": "HR-EMP-00002",
				"employee_name": "Ben Packhouse",
				"employee_number": "E-101",
				"department": "Operations",
				"designation": "Operator",
				"status": "Active",
				"date_of_joining": "2025-01-15",
				"company": MAIN,
			},
			{
				"name": "HR-EMP-00003",
				"employee_name": "Cara Office",
				"employee_number": "E-102",
				"department": "Administration",
				"designation": "Bookkeeper",
				"status": "Left",
				"date_of_joining": "2022-06-01",
				"relieving_date": "2026-02-28",
				"company": MAIN,
			},
		],
	)
	attendance = []
	pattern = [
		("HR-EMP-00001", "Present", 3),
		("HR-EMP-00001", "On Leave", 1),
		("HR-EMP-00002", "Present", 2),
		("HR-EMP-00002", "Absent", 1),
		("HR-EMP-00002", "Half Day", 1),
	]
	day = 1
	for employee, status, count in pattern:
		for _ in range(count):
			attendance.append(
				{
					"name": f"HR-ATT-{day:05d}",
					"employee": employee,
					"employee_name": "Ada Orchard" if employee == "HR-EMP-00001" else "Ben Packhouse",
					"attendance_date": f"2026-06-{day:02d}",
					"status": status,
					"department": "Operations",
					"company": MAIN,
					"docstatus": 1,
				}
			)
			day += 1
	# A draft row, which must not be counted.
	attendance.append(
		{
			"name": "HR-ATT-draft",
			"employee": "HR-EMP-00002",
			"employee_name": "Ben Packhouse",
			"attendance_date": "2026-06-20",
			"status": "Present",
			"department": "Operations",
			"company": MAIN,
			"docstatus": 0,
		}
	)
	STORE.seed("Attendance", attendance)
	STORE.seed(
		"Leave Type",
		[{"name": "Annual Leave"}, {"name": "Sick Leave"}, {"name": "Unpaid Leave"}],
	)
	STORE.seed(
		"Leave Allocation",
		[
			{
				"name": "HR-LAL-00001",
				"employee": "HR-EMP-00001",
				"leave_type": "Annual Leave",
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
				"total_leaves_allocated": 20,
				"docstatus": 1,
			},
			{
				"name": "HR-LAL-00002",
				"employee": "HR-EMP-00001",
				"leave_type": "Sick Leave",
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
				"total_leaves_allocated": 10,
				"docstatus": 1,
			},
			{
				"name": "HR-LAL-00003",
				"employee": "HR-EMP-00001",
				"leave_type": "Unpaid Leave",
				"from_date": "2025-01-01",
				"to_date": "2025-12-31",
				"total_leaves_allocated": 5,
				"docstatus": 1,
			},
		],
	)


#: What the fake `get_leave_balance_on` returns, keyed by leave type. A test can
#: replace an entry with an Exception to exercise the per-type failure path.
LEAVE_BALANCES = {"Annual Leave": 12.5, "Sick Leave": 8.0}


def _install_leave_api() -> None:
	module_path = "hrms.hr.doctype.leave_application.leave_application"
	if module_path in sys.modules:
		return

	def get_leave_balance_on(employee, leave_type, date, **kwargs):
		value = LEAVE_BALANCES.get(leave_type, 0.0)
		if isinstance(value, Exception):
			raise value
		return value

	leaf = types.ModuleType(module_path)
	leaf.get_leave_balance_on = get_leave_balance_on
	built = []
	parts = module_path.split(".")
	for index in range(1, len(parts)):
		name = ".".join(parts[:index])
		if name not in sys.modules:
			sys.modules[name] = types.ModuleType(name)
			built.append(name)
	sys.modules[module_path] = leaf


class V2TestCase(SeededTestCase):
	"""The fixture site plus everything v0.2.0 reads."""

	def setUp(self):
		super().setUp()
		seed_v2()


# ── v0.7.0 fixtures: equity, members, and something to depreciate ───────────
#: The Member accounting dimension as this app's own docs describe building it:
#: a generated master DocType named by its own value field, so `Member-01` is
#: both the record and the value.
MEMBER_MASTER = "Member"
MEMBER_ONE = "Member-01"
MEMBER_TWO = "Member-02"
MEMBER_THREE = "Member-03"

ASSET_CATEGORY = "Farm Equipment"
ITEM_GROUP = "All Item Groups"

EQUITY_ROOT = f"Equity - {MAIN_ABBR}"
MEMBER_CAPITAL = f"3100 - Member Capital - {MAIN_ABBR}"
MEMBER_DISTRIBUTIONS = f"3200 - Member Distributions - {MAIN_ABBR}"
ACCUMULATED_DEPRECIATION = f"1810 - Accumulated Depreciation - {MAIN_ABBR}"
DEPRECIATION_EXPENSE = f"5200 - Depreciation - {MAIN_ABBR}"
BANK = f"1110 - Bank Checking - {MAIN_ABBR}"


#: A third enabled leaf cost center, so a split can be three ways. Two is enough
#: to test a split and not enough to test the rounding: 40/60 divides cleanly,
#: 33.33/33.33/33.34 does not, and the second is where a depreciation entry stops
#: balancing.
HARVEST = f"120 - Harvest - {MAIN_ABBR}"


def seed_v7() -> None:
	"""Everything the governance and asset tools need. Additive to `seed_site`."""
	_equity_chart()
	_member_dimension()
	_third_cost_center()
	_asset_masters()


def _third_cost_center() -> None:
	STORE.seed(
		"Cost Center",
		[
			{
				"name": HARVEST,
				"cost_center_name": "Harvest",
				"cost_center_number": "120",
				"parent_cost_center": cost_center("Operations"),
				"is_group": 0,
				"disabled": 0,
				"company": MAIN,
				"lft": 200,
				"rgt": 201,
			}
		],
	)


def _equity_chart() -> None:
	"""An Equity root with a capital and a distributions account, plus the two
	accounts depreciation moves between.

	The fixture's base chart has no Equity root at all — a textbook chart that
	predates anybody caring about members — so this adds one rather than editing
	the base, which keeps every earlier test looking at the site it was written
	against.
	"""
	STORE.seed(
		"Account",
		[
			_account(EQUITY_ROOT, "Equity", "", "Equity", "", is_group=1, parent=""),
			_account(MEMBER_CAPITAL, "Member Capital", "3100", "Equity", "", parent=EQUITY_ROOT),
			_account(
				MEMBER_DISTRIBUTIONS, "Member Distributions", "3200", "Equity", "", parent=EQUITY_ROOT
			),
			_account(
				ACCUMULATED_DEPRECIATION,
				"Accumulated Depreciation",
				"1810",
				"Asset",
				"Accumulated Depreciation",
				parent=f"Application of Funds (Assets) - {MAIN_ABBR}",
			),
			_account(
				DEPRECIATION_EXPENSE,
				"Depreciation",
				"5200",
				"Expense",
				"Depreciation",
				parent=f"Expenses - {MAIN_ABBR}",
			),
		],
	)
	# The cash side of a member contribution. Set here rather than in the base
	# company row so the "this company has no default bank account" refusal stays
	# testable by clearing it.
	STORE.tables["Company"][MAIN]["default_bank_account"] = BANK


def _account(name, account_name, number, root_type, account_type, is_group=0, parent=""):
	return {
		"name": name,
		"account_name": account_name,
		"account_number": number,
		"parent_account": parent,
		"is_group": is_group,
		"root_type": root_type,
		"account_type": account_type,
		"account_currency": "USD",
		"disabled": 0,
		"company": MAIN,
	}


def _member_dimension() -> None:
	"""The Member dimension, wired the way `create_accounting_dimension` wires it.

	Built by hand rather than by calling the tool: a governance test that failed
	because the dimension tool's switch was off would be pointing at the wrong
	thing entirely.
	"""
	register_doctype(
		MEMBER_MASTER,
		[
			{"fieldname": "dimension_value", "fieldtype": "Data", "label": "Member", "reqd": 1},
			{"fieldname": "description", "fieldtype": "Small Text", "label": "Description"},
			{"fieldname": "disabled", "fieldtype": "Check", "label": "Disabled", "default": "0"},
		],
		autoname="field:dimension_value",
	)
	STORE.seed(
		MEMBER_MASTER,
		[
			{"name": MEMBER_ONE, "dimension_value": MEMBER_ONE},
			{"name": MEMBER_TWO, "dimension_value": MEMBER_TWO},
			{"name": MEMBER_THREE, "dimension_value": MEMBER_THREE},
		],
	)
	STORE.seed(
		"Accounting Dimension",
		[
			{
				"name": MEMBER_MASTER,
				"label": MEMBER_MASTER,
				"fieldname": "member",
				"document_type": MEMBER_MASTER,
				"disabled": 0,
			}
		],
	)
	add_field("Journal Entry Account", "member", fieldtype="Link", options=MEMBER_MASTER, label="Member")


def install_bbch_dimension() -> None:
	"""The second dimension, for the asset tests that tag a depreciation line."""
	register_doctype(
		"BBCH Stage",
		[{"fieldname": "dimension_value", "fieldtype": "Data", "label": "BBCH Stage", "reqd": 1}],
		autoname="field:dimension_value",
	)
	STORE.seed("BBCH Stage", [{"name": "BBCH-8", "dimension_value": "BBCH-8"}])
	STORE.seed(
		"Accounting Dimension",
		[
			{
				"name": "BBCH Stage",
				"label": "BBCH Stage",
				"fieldname": "bbch_stage",
				"document_type": "BBCH Stage",
				"disabled": 0,
			}
		],
	)
	add_field(
		"Journal Entry Account", "bbch_stage", fieldtype="Link", options="BBCH Stage", label="BBCH Stage"
	)


def _asset_masters() -> None:
	"""An Asset Category carrying its accounts, and the Item scaffolding ERPNext
	insists an Asset hangs off."""
	STORE.seed("Item Group", [{"name": ITEM_GROUP, "is_group": 1}])
	STORE.seed("UOM", [{"name": "Nos", "enabled": 1}])
	STORE.seed(
		"Asset Category",
		[
			{
				"name": ASSET_CATEGORY,
				"asset_category_name": ASSET_CATEGORY,
				"accounts": [
					{
						"company": MAIN,
						"fixed_asset_account": f"1000 - Current Assets - {MAIN_ABBR}",
						"accumulated_depreciation_account": ACCUMULATED_DEPRECIATION,
						"depreciation_expense_account": DEPRECIATION_EXPENSE,
					}
				],
			},
			# A category with no accounts at all: what a half-configured site looks
			# like, and what run_depreciation_cycle has to skip rather than crash on.
			{"name": "Unconfigured", "asset_category_name": "Unconfigured", "accounts": []},
		],
	)


class V7TestCase(SeededTestCase):
	"""The fixture site plus equity accounts, the Member dimension and asset masters."""

	def setUp(self):
		super().setUp()
		seed_v7()


# ── v0.8.0 fixtures: opening balances, bank accounts and notes payable ──────
#: Where the offsetting side of an opening balance lands, numbered as this app's
#: own chart template numbers it — which is what makes `set_opening_balance`'s
#: "find it by account_number 3300" path the one the tests exercise.
OPENING_EQUITY = f"3300 - Opening Balance Equity - {MAIN_ABBR}"

#: The two sides of a loan payment.
NOTES_PAYABLE = f"2310 - Notes Payable - {MAIN_ABBR}"
INTEREST_EXPENSE = f"5300 - Interest Expense - {MAIN_ABBR}"

#: Something to bring onto the books with an opening balance, and a liability a
#: credit-card Bank Account can point at.
EQUIPMENT = f"1710 - Equipment - {MAIN_ABBR}"
CREDIT_CARD = f"2150 - Credit Card - {MAIN_ABBR}"

#: A second Bank-typed asset account, so `create_bank_account` has one to claim
#: that the fixture's existing Bank Account has not already taken.
BANK_SAVINGS = f"1120 - Bank Savings - {MAIN_ABBR}"

#: A leaf account with no GL entries, no journal entry lines, no children and
#: nothing pointing at it — the only shape `delete_account` will remove. It is in
#: the base fixture chart; named here because the delete tests are entirely about
#: which accounts are and are not this one.
UNUSED_ACCOUNT = f"1190 - Cash Clearing - {MAIN_ABBR}"

ASSETS_ROOT = f"Application of Funds (Assets) - {MAIN_ABBR}"
LIABILITIES_ROOT = f"Source of Funds (Liabilities) - {MAIN_ABBR}"
EXPENSES_ROOT = f"Expenses - {MAIN_ABBR}"


def seed_v8() -> None:
	"""Accounts the v0.8.0 tools post to. Additive to `seed_v7`."""
	STORE.seed(
		"Account",
		[
			_account(
				OPENING_EQUITY, "Opening Balance Equity", "3300", "Equity", "", parent=EQUITY_ROOT
			),
			_account(NOTES_PAYABLE, "Notes Payable", "2310", "Liability", "", parent=LIABILITIES_ROOT),
			_account(CREDIT_CARD, "Credit Card", "2150", "Liability", "", parent=LIABILITIES_ROOT),
			_account(INTEREST_EXPENSE, "Interest Expense", "5300", "Expense", "", parent=EXPENSES_ROOT),
			_account(EQUIPMENT, "Equipment", "1710", "Asset", "Fixed Asset", parent=ASSETS_ROOT),
			_account(BANK_SAVINGS, "Bank Savings", "1120", "Asset", "Bank", parent=ASSETS_ROOT),
		],
	)


class V8TestCase(V7TestCase):
	"""...and the accounts opening balances, bank accounts and notes payable use."""

	def setUp(self):
		super().setUp()
		seed_v8()


class HRTestCase(V2TestCase):
	"""...and with Frappe HR installed."""

	def setUp(self):
		super().setUp()
		install_hrms()


# ── v0.11.0 fixtures: land, leases, related parties and a year of vendor pay ──
#: The portfolio the quarterly report reads, and the clearing account beside it.
#: `1190 - Cash Clearing` is already in the base chart (`UNUSED_ACCOUNT`), which
#: is why the report's clearing section has something to find without this
#: fixture adding one — and why a test that wants a NON-zero clearing balance has
#: to post to it deliberately.
MARKETABLE_SECURITIES = f"1310 - Marketable Securities - {MAIN_ABBR}"

#: A Payable-typed account, so the 1099 rule that counts debits only on payables
#: is exercised against a real account type rather than a mocked one.
ACCOUNTS_PAYABLE = f"2110 - Accounts Payable - {MAIN_ABBR}"
PROFESSIONAL_FEES = f"5400 - Professional Fees - {MAIN_ABBR}"
CONTRACT_LABOR = f"5410 - Contract Labor - {MAIN_ABBR}"

#: The vendors the 1099 tests classify. Each one is a distinct branch of
#: `tax._verdict`, and the names are the shapes that occur in life: an accountant
#: trading as an LLC, a person, an incorporated law firm, a plain corporation, a
#: partnership with no supplier type set, and somebody under the threshold.
SORREN = "Sorren Accounting LLC"
MITCHELL = "Mitchell Huru"
FRIEND_REAGAN = "Friend & Reagan PC"
BRIGHT_ORCHARD = "Bright Orchard Supply Inc"
COOPER = "Cooper Family Orchards"
QUILL = "Quill Stationery"

#: The tax year the vendor postings sit in. One whole year in the past relative
#: to the fixture's frozen today (2026-07-24), so `generate_1099_prefill` can run
#: for it and refuse for 2026.
TAX_YEAR = 2025

#: The quarter the investment report tests use. Ended 2026-06-30, comfortably
#: before the fixture's today, which is what makes it reportable at all.
QUARTER = "2026-Q2"
QUARTER_START = "2026-04-01"
QUARTER_END = "2026-06-30"

#: The Prior Statement that has to be on file before a quarter can be reported.
STATEMENT_DOC = "GOV-STATEMENT-2026-Q2"


def seed_v11() -> None:
	"""Accounts, vendors, a year of payments and a quarter of portfolio activity."""
	_v11_accounts()
	_suppliers()
	_vendor_payments()
	_investment_activity()
	_quarter_statement()


def _v11_accounts() -> None:
	STORE.seed(
		"Account",
		[
			_account(
				MARKETABLE_SECURITIES,
				"Marketable Securities",
				"1310",
				"Asset",
				"",
				parent=ASSETS_ROOT,
			),
			_account(
				ACCOUNTS_PAYABLE, "Accounts Payable", "2110", "Liability", "Payable", parent=LIABILITIES_ROOT
			),
			_account(PROFESSIONAL_FEES, "Professional Fees", "5400", "Expense", "", parent=EXPENSES_ROOT),
			_account(CONTRACT_LABOR, "Contract Labor", "5410", "Expense", "", parent=EXPENSES_ROOT),
		],
	)


def _suppliers() -> None:
	STORE.seed(
		"Supplier",
		[
			{"name": SORREN, "supplier_name": SORREN, "supplier_type": "Company", "supplier_group": "Services"},
			{"name": MITCHELL, "supplier_name": MITCHELL, "supplier_type": "Individual"},
			{
				"name": FRIEND_REAGAN,
				"supplier_name": FRIEND_REAGAN,
				"supplier_type": "Company",
				"supplier_group": "Legal",
			},
			{"name": BRIGHT_ORCHARD, "supplier_name": BRIGHT_ORCHARD, "supplier_type": "Company"},
			# No supplier_type at all — the "nothing on this site says what this is"
			# branch, and the one a related-party entry is meant to resolve.
			{"name": COOPER, "supplier_name": COOPER, "supplier_group": "Farm Labor"},
			{"name": QUILL, "supplier_name": QUILL, "supplier_type": "Individual"},
		],
	)


def _party_gl(account, posting_date, party, debit=0, credit=0, cost_center_name="Main", **extra):
	row = _gl(account, posting_date, debit=debit, credit=credit)
	row.update(
		{
			"name": f"GL-{party}-{posting_date}-{debit}-{credit}-{len(STORE.rows('GL Entry'))}",
			"party_type": "Supplier",
			"party": party,
			"cost_center": cost_center(cost_center_name),
			"voucher_no": f"ACC-JV-{posting_date[:4]}-{abs(hash((party, posting_date, debit, credit))) % 100000:05d}",
			"is_opening": "No",
		}
	)
	row.update(extra)
	return row


def _vendor_payments() -> None:
	"""A year of supplier postings, one shape per rule the classifier has.

	Every payment carries its own balancing credit to the bank with NO party, so
	the fixture stays double-entry without those rows reaching the 1099 — which
	is itself worth having, since a counter-leg wrongly picked up would double
	every recipient's Box 1.
	"""
	rows = [
		# Sorren: paid monthly, across two cost centers, straight from expense.
		_party_gl(PROFESSIONAL_FEES, "2025-03-31", SORREN, debit=6090, cost_center_name="Main"),
		_party_gl(PROFESSIONAL_FEES, "2025-06-30", SORREN, debit=6090, cost_center_name="Main"),
		_party_gl(PROFESSIONAL_FEES, "2025-09-30", SORREN, debit=6090, cost_center_name="Operations"),
		_party_gl(PROFESSIONAL_FEES, "2025-12-31", SORREN, debit=6090, cost_center_name="Operations"),
		# Mitchell: paid, then refunded part of it. The credit reduces Box 1
		# because it is not on a payable account.
		_party_gl(CONTRACT_LABOR, "2025-08-15", MITCHELL, debit=1450, cost_center_name="Field Work"),
		_party_gl(CONTRACT_LABOR, "2025-09-01", MITCHELL, credit=200, cost_center_name="Field Work"),
		# The incorporated law firm.
		_party_gl(PROFESSIONAL_FEES, "2025-09-08", FRIEND_REAGAN, debit=675, cost_center_name="Main"),
		# A plain corporation, over the threshold and exempt.
		_party_gl(supplies(), "2025-05-02", BRIGHT_ORCHARD, debit=5000, cost_center_name="Operations"),
		# Cooper, booked through payables: a bill raised (credit) and paid
		# (debit). Only the debit is a payment, and that is the whole point of
		# the payable rule.
		_party_gl(ACCOUNTS_PAYABLE, "2025-07-01", COOPER, credit=3200, cost_center_name="Field Work"),
		_party_gl(ACCOUNTS_PAYABLE, "2025-07-20", COOPER, debit=3200, cost_center_name="Field Work"),
		# Under the threshold.
		_party_gl(supplies(), "2025-04-04", QUILL, debit=120, cost_center_name="Main"),
		# Excluded: an opening entry, a cancelled voucher, and the next year.
		_party_gl(PROFESSIONAL_FEES, "2025-01-01", SORREN, debit=999, is_opening="Yes"),
		_party_gl(PROFESSIONAL_FEES, "2025-02-02", SORREN, debit=888, is_cancelled=1),
		_party_gl(PROFESSIONAL_FEES, "2026-01-31", SORREN, debit=6090),
	]
	# Excluded: wages, which are W-2 territory. Counted and reported, not silent.
	wages = _party_gl(CONTRACT_LABOR, "2025-11-15", "HR-EMP-00001", debit=45000)
	wages["party_type"] = "Employee"
	rows.append(wages)

	# The balancing bank legs, party-free by construction.
	balancing = []
	for row in rows:
		if row.get("is_cancelled"):
			continue
		leg = _gl(BANK, row["posting_date"], debit=row.get("credit") or 0, credit=row.get("debit") or 0)
		leg["name"] = f"{row['name']}-leg"
		leg["voucher_no"] = row["voucher_no"]
		leg["is_opening"] = row.get("is_opening", "No")
		balancing.append(leg)

	STORE.seed("GL Entry", rows + balancing)


def _investment_activity() -> None:
	"""A portfolio position before the quarter, and two trades inside it."""
	rows = [
		("2025-12-31", 1400000, 0),
		("2026-05-15", 100000, 0),
		("2026-06-20", 0, 25000),
	]
	entries = []
	for index, (posting_date, debit, credit) in enumerate(rows, start=1):
		entry = _gl(MARKETABLE_SECURITIES, posting_date, debit=debit, credit=credit)
		entry["name"] = f"GL-INV-{index}"
		entry["voucher_no"] = f"ACC-JV-INV-{index:05d}"
		entry["cost_center"] = cost_center("Operations")
		entry["is_opening"] = "No"
		entries.append(entry)
		leg = _gl(BANK, posting_date, debit=credit, credit=debit)
		leg["name"] = f"GL-INV-{index}-leg"
		leg["voucher_no"] = entry["voucher_no"]
		leg["is_opening"] = "No"
		entries.append(leg)
	STORE.seed("GL Entry", entries)


def _quarter_statement() -> None:
	"""The custodian's quarter-end statement, filed where the report looks for it."""
	STORE.seed(
		"Governance Document",
		[
			{
				"name": STATEMENT_DOC,
				"title": f"WFA Statement {QUARTER_END}",
				"category": "Prior Statement",
				"company": MAIN,
				"effective_date": QUARTER_END,
			}
		],
	)


class V11TestCase(V8TestCase):
	"""...and the land, party, vendor-payment and portfolio fixtures."""

	def setUp(self):
		super().setUp()
		seed_v11()


# ── v0.12.0 fixtures: party types, countries, and two family payees ─────────
#: A relative who gets money that is not payroll and not a purchase. The whole
#: reason the `Family` party type exists: without it this posting would carry a
#: Supplier party and land on a 1099 the recipient owes no tax on.
ALEX = "Alex Bramwell"

#: A consultant paid twice a year who is not a formal vendor. `Contact` is the
#: other half of the same problem — leaving them unclassified means the 1099
#: pre-fill has nothing to go on, which is how a reportable payment goes unfiled.
ANTONY = "Antony Sedge"


def seed_v12() -> None:
	"""The custom party types, a country list, and a year of Family/Contact pay."""
	_party_types()
	_countries()
	_party_registers()
	_family_and_contact_payments()


def _party_types() -> None:
	"""ERPNext's four, plus the two this app registers.

	The stock four are seeded here rather than assumed because
	`register_party_types` has to be able to report "already registered" for
	something, and because a test that Family is absent needs the others present
	to prove the absence is specific.
	"""
	STORE.seed(
		"Party Type",
		[
			{"name": "Customer", "party_type": "Customer", "account_type": "Receivable"},
			{"name": "Supplier", "party_type": "Supplier", "account_type": "Payable"},
			{"name": "Employee", "party_type": "Employee", "account_type": "Payable"},
			{"name": "Shareholder", "party_type": "Shareholder", "account_type": "Payable"},
		],
	)


def _party_registers() -> None:
	"""The records a Family and a Contact posting actually point AT.

	Not decoration. A posting's `party` is a Dynamic Link resolved through its
	`party_type`, so a GL Entry saying `party_type="Family", party="Alex
	Bramwell"` is only valid if Alex is a row on the Family register. A fixture
	that seeded the postings without the people would be a fixture describing a
	site that cannot exist — which is exactly the gap that let v0.12.0 ship.
	"""
	STORE.seed("Family", [{"name": ALEX, "family_member_name": ALEX, "relationship": "Sibling", "active": 1}])
	STORE.seed(
		"Contact",
		[{"name": ANTONY, "first_name": "Antony", "last_name": "Sedge", "company_name": ""}],
	)


def _countries() -> None:
	"""Two, so `create_company`'s ISO check has something real to refuse against."""
	STORE.seed(
		"Country",
		[{"name": "United States", "code": "us"}, {"name": "Canada", "code": "ca"}],
	)


def _family_and_contact_payments() -> None:
	"""One payee of each new party type, both over the 1099 threshold.

	Both are over $600 on purpose. A Family payment under the threshold would be
	excluded by the threshold rather than by the party type, and the test would
	pass for the wrong reason — which is the bug this fixture exists to prevent.
	"""
	rows = [
		_party_gl(CONTRACT_LABOR, "2025-05-10", ALEX, debit=4000, cost_center_name="Main"),
		_party_gl(CONTRACT_LABOR, "2025-10-10", ALEX, debit=3500, cost_center_name="Main"),
		_party_gl(PROFESSIONAL_FEES, "2025-04-18", ANTONY, debit=1800, cost_center_name="Operations"),
		_party_gl(PROFESSIONAL_FEES, "2025-11-06", ANTONY, debit=1200, cost_center_name="Operations"),
	]
	for row in rows[:2]:
		row["party_type"] = "Family"
	for row in rows[2:]:
		row["party_type"] = "Contact"

	balancing = []
	for row in rows:
		leg = _gl(BANK, row["posting_date"], debit=row.get("credit") or 0, credit=row.get("debit") or 0)
		leg["name"] = f"{row['name']}-leg"
		leg["voucher_no"] = row["voucher_no"]
		leg["is_opening"] = "No"
		balancing.append(leg)

	STORE.seed("GL Entry", rows + balancing)


class V12TestCase(V11TestCase):
	"""...and the party types, countries and family/contact payments."""

	def setUp(self):
		super().setUp()
		seed_v12()
