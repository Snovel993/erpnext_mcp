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

from .harness import STORE, MCPTestCase

MAIN = "Example Trading Co"
OTHER = "Second Example Ltd"
MAIN_ABBR = "ETC"
OTHER_ABBR = "SEL"


def seed_site() -> None:
	"""Load the whole fixture. Call after `STORE.reset()`."""
	_companies()
	_fiscal_years()
	_accounts()
	_gl_entries()
	_journal_entries()
	_banking()


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
				"year_start_date": "2026-01-01",
				"year_end_date": "2026-12-31",
				"disabled": 0,
				"companies": [{"company": MAIN}],
			},
			{
				"name": "2025",
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


def _gl_entries() -> None:
	STORE.seed(
		"GL Entry",
		[
			# Cash: 1000 in, 250 out, plus a cancelled 9999 that must be ignored.
			_gl(cash(), "2026-01-15", debit=1000),
			_gl(cash(), "2026-02-10", credit=250),
			_gl(cash(), "2026-03-01", debit=9999, is_cancelled=1),
			# ...and one after the as_of date the balance tests use.
			_gl(cash(), "2026-12-31", debit=500),
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
					{"account": cash(), "debit": 1000, "credit": 0, "idx": 1},
					{"account": sales(), "debit": 0, "credit": 1000, "idx": 2},
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
					{"account": supplies(), "debit": 250, "credit": 0, "idx": 1},
					{"account": cash(), "debit": 0, "credit": 250, "idx": 2},
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
					{"account": supplies(), "debit": 40, "credit": 0, "idx": 1},
					{"account": cash(), "debit": 0, "credit": 40, "idx": 2},
				],
			},
		],
	)


BANK_ACCOUNT = "Operating - Example Bank"


def _banking() -> None:
	STORE.seed(
		"Bank Account",
		[
			{
				"name": BANK_ACCOUNT,
				"account_name": "Operating",
				"bank": "Example Bank",
				"company": MAIN,
				"account": f"1110 - Bank Checking - {MAIN_ABBR}",
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
	STORE.seed("User", [{"name": "mcp@example.test", "enabled": 1, "full_name": "MCP Bot"}])


class SeededTestCase(MCPTestCase):
	"""MCPTestCase plus the fixture site. What most tests want."""

	def setUp(self):
		super().setUp()
		seed_site()
