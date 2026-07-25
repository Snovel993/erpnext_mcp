# SPDX-License-Identifier: MIT
"""The read-only tools. None of these writes anything, ever.

Two conventions run through all ten:

DISCOVER, DON'T ASSUME. Company names, account numbers, fiscal-year labels and
the Bank Transaction schema are all read off the site at call time. There is no
constant in this file that names a real-world company, account or ledger.

EXPLAIN THE SIGN. Accounting sign conventions are where an AI reading a ledger
most reliably goes wrong: a Liability with a $5,000 credit balance is not
"-5000 of something". So every balance is returned twice — `balance` in raw
ledger convention (debit minus credit) and `balance_natural` flipped per the
account's `root_type` so a liability, income or equity balance reads positive
when it is what an accountant would call normal — with `sign_convention` in the
payload naming which is which.
"""

import frappe

from .. import compat
from ..args import (
	MAX_LIMIT,
	as_date,
	as_docstatus,
	as_limit,
	as_str,
	resolve_account,
	resolve_company,
)
from ..errors import ToolError
from ..result import ToolResult

#: Root types whose natural balance is a credit. Everything else (Asset,
#: Expense) is naturally a debit.
_CREDIT_ROOTS = ("Liability", "Income", "Equity")


# ── 1. get_company_topology ─────────────────────────────────────────────────
def get_company_topology(args: dict) -> ToolResult:
	"""The shape of this ERPNext install, in one call.

	Deliberately the first tool in the catalogue: it is what an MCP client should
	call before anything else, because every other tool takes a company, an
	account or a fiscal year that only exists on this particular site.
	"""
	fields = compat.existing_fields(
		"Company",
		[
			"name",
			"abbr",
			"default_currency",
			"country",
			"chart_of_accounts",
			"parent_company",
			"is_group",
			"tax_id",
		],
	)
	companies = frappe.db.get_all("Company", fields=fields, order_by="name asc")
	fiscal_years = _fiscal_years_by_company()

	out = []
	for company in companies:
		name = company["name"]
		roots = frappe.db.get_all(
			"Account",
			filters={"company": name, "parent_account": ("in", ("", None))},
			fields=["name", "account_name", "root_type", "is_group"],
			order_by="root_type asc, name asc",
		)
		out.append(
			{
				**company,
				"default_cost_center": compat.company_default_cost_center(name),
				"fiscal_years": fiscal_years.get(name, []) + fiscal_years["__all__"],
				"root_accounts": roots,
				"root_types": sorted({r["root_type"] for r in roots if r["root_type"]}),
				"account_count": frappe.db.count("Account", {"company": name}),
			}
		)

	data = {
		"companies": out,
		"count": len(out),
		"site": frappe.local.site,
		"optional_doctypes": {
			doctype: compat.doctype_exists(doctype)
			for doctype in ("Bank Transaction", "Bank Statement", "Bank Account", "Bank")
		},
	}
	return ToolResult(data, f"{len(out)} company/companies")


def _fiscal_years_by_company() -> dict:
	"""Fiscal years grouped by the company they are restricted to.

	A Fiscal Year with no rows in its `companies` child table applies to every
	company — that is how ERPNext models "global fiscal year" — so those go into
	the `__all__` bucket and get merged into each company's list.
	"""
	years = frappe.db.get_all(
		"Fiscal Year",
		fields=compat.existing_fields(
			"Fiscal Year", ["name", "year_start_date", "year_end_date", "disabled"]
		),
		order_by="year_start_date desc",
	)
	links = frappe.db.get_all(
		"Fiscal Year Company",
		filters={"parenttype": "Fiscal Year"},
		fields=["parent", "company"],
	)
	by_year = {}
	for link in links:
		by_year.setdefault(link["parent"], []).append(link["company"])

	grouped = {"__all__": []}
	for year in years:
		companies = by_year.get(year["name"], [])
		if not companies:
			grouped["__all__"].append(year)
			continue
		for company in companies:
			grouped.setdefault(company, []).append(year)
	return grouped


# ── 2. get_account_balance ──────────────────────────────────────────────────
def get_account_balance(args: dict) -> ToolResult:
	"""Balance of one account from GL Entry, as of a date.

	Sums the ledger rather than reading a cached figure, so the answer matches
	what ERPNext's own General Ledger report would print — including the
	`is_cancelled` exclusion, which is the single easiest way to compute a wrong
	balance on a site that has ever cancelled a voucher.
	"""
	company = resolve_company(as_str(args, "company"))
	account = resolve_account(as_str(args, "account", required=True), company or "")
	as_of = as_date(args, "as_of") or frappe.utils.today()

	meta = frappe.db.get_value(
		"Account",
		account,
		["account_name", "account_number", "root_type", "account_type", "company",
		 "account_currency", "is_group", "freeze_account"],
		as_dict=True,
	)

	filters = {"account": account, "posting_date": ("<=", as_of)}
	if compat.has_field("GL Entry", "is_cancelled"):
		filters["is_cancelled"] = 0
	totals = frappe.db.get_all(
		"GL Entry",
		filters=filters,
		fields=["sum(debit) as debit", "sum(credit) as credit", "count(name) as entries"],
	)
	row = (totals or [{}])[0] or {}
	debit = float(row.get("debit") or 0)
	credit = float(row.get("credit") or 0)
	balance = round(debit - credit, 2)
	natural = -balance if (meta or {}).get("root_type") in _CREDIT_ROOTS else balance

	data = {
		"account": account,
		"account_name": (meta or {}).get("account_name"),
		"account_number": (meta or {}).get("account_number"),
		"company": (meta or {}).get("company"),
		"currency": (meta or {}).get("account_currency"),
		"root_type": (meta or {}).get("root_type"),
		"account_type": (meta or {}).get("account_type"),
		"is_group": bool((meta or {}).get("is_group")),
		"as_of": as_of,
		"total_debit": round(debit, 2),
		"total_credit": round(credit, 2),
		"gl_entry_count": int(row.get("entries") or 0),
		"balance": balance,
		"balance_natural": round(natural, 2),
		"sign_convention": (
			"balance = debit - credit (raw ledger). balance_natural flips the sign "
			"for Liability/Income/Equity so a normal balance reads positive."
		),
	}
	if data["is_group"]:
		data["note"] = (
			"This is a group account. GL Entries post to leaf accounts, so this "
			"balance covers only entries booked directly against the group — use "
			"get_chart_of_accounts to walk its children."
		)
	return ToolResult(
		data, f"{account} balance {balance} as of {as_of} ({data['gl_entry_count']} GL rows)"
	)


# ── 3. get_journal_entries ──────────────────────────────────────────────────
def get_journal_entries(args: dict) -> ToolResult:
	"""Journal Entry headers in a date range, newest first.

	Headers only — `get_journal_entry` returns the account lines for one. That
	split is on purpose: a month of JEs with every line expanded is a lot of
	tokens for a question that is usually "which one was it".
	"""
	from_date = as_date(args, "from_date", required=True)
	to_date = as_date(args, "to_date", required=True)
	if from_date > to_date:
		raise ToolError(f"from_date {from_date} is after to_date {to_date}")
	company = resolve_company(as_str(args, "company"))
	account = as_str(args, "account")
	docstatus = as_docstatus(args)
	limit = as_limit(args)

	filters = {"posting_date": ("between", [from_date, to_date])}
	if company:
		filters["company"] = company
	if docstatus is not None:
		filters["docstatus"] = docstatus
	if account:
		resolved = resolve_account(account, company or "")
		names = frappe.db.get_all(
			"Journal Entry Account",
			filters={"account": resolved, "parenttype": "Journal Entry"},
			pluck="parent",
			# One JE can carry the same account on several lines; dedupe below.
			limit=MAX_LIMIT * 10,
		)
		unique = sorted(set(names))
		if not unique:
			return ToolResult(
				{
					"journal_entries": [],
					"count": 0,
					"filters": {"account": resolved, "from_date": from_date, "to_date": to_date},
				},
				f"no Journal Entry touches {resolved}",
			)
		filters["name"] = ("in", unique)

	fields = compat.existing_fields(
		"Journal Entry",
		[
			"name",
			"posting_date",
			"company",
			"voucher_type",
			"total_debit",
			"total_credit",
			"user_remark",
			"cheque_no",
			"cheque_date",
			"bill_no",
			"docstatus",
			"owner",
			"creation",
		],
	)
	rows = frappe.db.get_all(
		"Journal Entry",
		filters=filters,
		fields=fields,
		order_by="posting_date desc, creation desc",
		limit=limit,
	)
	for row in rows:
		row["docstatus_label"] = _docstatus_label(row.get("docstatus"))

	data = {
		"journal_entries": rows,
		"count": len(rows),
		"limit": limit,
		"truncated": len(rows) == limit,
		"filters": {
			"from_date": from_date,
			"to_date": to_date,
			"company": company,
			"account": account or None,
			"docstatus": docstatus,
		},
	}
	return ToolResult(data, f"{len(rows)} Journal Entry row(s) {from_date}..{to_date}")


# ── 4. get_journal_entry ────────────────────────────────────────────────────
def get_journal_entry(args: dict) -> ToolResult:
	"""One Journal Entry with every account line, party and reference."""
	name = as_str(args, "name", required=True)
	if not frappe.db.exists("Journal Entry", name):
		raise ToolError(f"no Journal Entry named {name!r}")
	doc = frappe.get_doc("Journal Entry", name)

	header_fields = compat.existing_fields(
		"Journal Entry",
		[
			"name", "posting_date", "company", "voucher_type", "naming_series",
			"total_debit", "total_credit", "difference", "user_remark", "remark",
			"cheque_no", "cheque_date", "bill_no", "bill_date", "finance_book",
			"docstatus", "owner", "creation", "modified", "modified_by",
			"is_opening", "clearance_date", "mode_of_payment", "multi_currency",
		],
	)
	line_fields = compat.existing_fields(
		"Journal Entry Account",
		[
			"idx", "account", "account_type", "party_type", "party", "debit", "credit",
			"debit_in_account_currency", "credit_in_account_currency", "account_currency",
			"exchange_rate", "against_account", "cost_center", "project",
			"reference_type", "reference_name", "reference_due_date", "user_remark",
			"is_advance",
		],
	)

	data = {field: doc.get(field) for field in header_fields}
	data["docstatus_label"] = _docstatus_label(doc.get("docstatus"))
	data["accounts"] = [
		{field: line.get(field) for field in line_fields} for line in (doc.get("accounts") or [])
	]
	data["balanced"] = (
		abs(float(doc.get("total_debit") or 0) - float(doc.get("total_credit") or 0)) < 0.005
	)
	return ToolResult(
		data,
		f"{name}: {len(data['accounts'])} line(s), "
		f"debit {doc.get('total_debit')} / credit {doc.get('total_credit')}, "
		f"{data['docstatus_label']}",
	)


# ── 5. list_bank_transactions ───────────────────────────────────────────────
def list_bank_transactions(args: dict) -> ToolResult:
	"""Bank Transactions, filtered the way a reconciliation actually asks.

	Amounts are normalised to one signed `amount` (positive in, negative out)
	whichever way this ERPNext version stores them — see `compat`.
	"""
	compat.require_doctype("Bank Transaction", "It ships with ERPNext's Accounts module.")
	bank_account = as_str(args, "bank_account")
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	status = as_str(args, "status")
	limit = as_limit(args)

	money = compat.bank_transaction_amount_fields()
	filters = {}
	if bank_account:
		filters["bank_account"] = _resolve_bank_account(bank_account)
	if from_date and to_date:
		filters["date"] = ("between", [from_date, to_date])
	elif from_date:
		filters["date"] = (">=", from_date)
	elif to_date:
		filters["date"] = ("<=", to_date)
	if status:
		filters["status"] = status

	fields = compat.existing_fields(
		"Bank Transaction",
		[
			"name", "date", "bank_account", "company", "description", "status",
			"reference_number", "currency", "party_type", "party", "bank_party_name",
			"docstatus", "deposit", "withdrawal", "amount", "allocated_amount",
			"unallocated_amount",
		],
	)
	rows = frappe.db.get_all(
		"Bank Transaction",
		filters=filters,
		fields=fields,
		order_by="date desc, creation desc",
		limit=limit,
	)
	for row in rows:
		row["amount_signed"] = round(compat.signed_amount(row, money), 2)

	data = {
		"bank_transactions": rows,
		"count": len(rows),
		"limit": limit,
		"truncated": len(rows) == limit,
		"amount_layout": money["style"],
		"sign_convention": "amount_signed is positive for money in, negative for money out.",
		"filters": {
			"bank_account": filters.get("bank_account"),
			"from_date": from_date,
			"to_date": to_date,
			"status": status or None,
		},
	}
	return ToolResult(data, f"{len(rows)} Bank Transaction row(s)")


# ── 6. get_bank_statement ───────────────────────────────────────────────────
def get_bank_statement(args: dict) -> ToolResult:
	"""One Bank Statement, on the versions of ERPNext that have the doctype.

	Bank Statement arrived later than Bank Transaction, so this is the one tool
	that can legitimately be unavailable on a supported site. It says so in
	words rather than raising a schema error, and `get_company_topology`
	reports the doctype's presence up front so a client need not find out here.
	"""
	compat.require_doctype(
		"Bank Statement",
		"It is only present on ERPNext versions that ship the Bank Statement "
		"doctype; get_company_topology reports whether this site has it.",
	)
	name = as_str(args, "name", required=True)
	if not frappe.db.exists("Bank Statement", name):
		raise ToolError(f"no Bank Statement named {name!r}")

	doc = frappe.get_doc("Bank Statement", name)
	# The field set has changed across versions, so mirror whatever is there
	# rather than naming columns — minus the framework's own bookkeeping.
	skip = {"doctype", "parent", "parentfield", "parenttype", "idx", "_user_tags",
		"_comments", "_assign", "_liked_by"}
	data = {
		key: value
		for key, value in doc.as_dict(no_nulls=False).items()
		if key not in skip and not isinstance(value, list)
	}
	data["child_tables"] = {
		key: [row.as_dict() for row in value]
		for key, value in doc.as_dict().items()
		if isinstance(value, list) and value
	}
	return ToolResult(data, f"Bank Statement {name}")


# ── 7. list_fiscal_years ────────────────────────────────────────────────────
def list_fiscal_years(args: dict) -> ToolResult:
	"""Every Fiscal Year and the companies it applies to.

	Needed because ERPNext will refuse a posting_date that falls outside a
	fiscal year, which is otherwise a confusing failure for a client picking
	dates on its own.
	"""
	company = resolve_company(as_str(args, "company"))
	grouped = _fiscal_years_by_company()
	if company:
		years = grouped.get(company, []) + grouped["__all__"]
		scope = f"company {company}"
	else:
		years = [year for key, value in grouped.items() if key != "__all__" for year in value]
		years += grouped["__all__"]
		scope = "all companies"

	seen, unique = set(), []
	for year in sorted(years, key=lambda y: str(y.get("year_start_date") or ""), reverse=True):
		if year["name"] in seen:
			continue
		seen.add(year["name"])
		unique.append(year)

	data = {
		"fiscal_years": unique,
		"count": len(unique),
		"company": company,
		"company_agnostic_years": [y["name"] for y in grouped["__all__"]],
		"note": (
			"A Fiscal Year with no company links applies to every company; those "
			"are listed in company_agnostic_years."
		),
	}
	return ToolResult(data, f"{len(unique)} fiscal year(s) for {scope}")


# ── 8. get_chart_of_accounts ────────────────────────────────────────────────
def get_chart_of_accounts(args: dict) -> ToolResult:
	"""The company's chart of accounts as a nested tree.

	Built in one query and assembled in Python: walking `parent_account` with a
	query per node is the obvious implementation and is also how you make a
	2,000-account chart take thirty seconds.
	"""
	company = resolve_company(as_str(args, "company"), required=True)
	root_type = as_str(args, "root_type")

	filters = {"company": company}
	if root_type:
		valid = ("Asset", "Liability", "Income", "Expense", "Equity")
		if root_type not in valid:
			raise ToolError(f"root_type must be one of {', '.join(valid)}, got {root_type!r}")
		filters["root_type"] = root_type

	fields = compat.existing_fields(
		"Account",
		[
			"name", "account_name", "account_number", "parent_account", "is_group",
			"root_type", "account_type", "account_currency", "disabled", "freeze_account",
			"lft", "rgt",
		],
	)
	# `lft` is the nested-set left bound: ordering by it yields parents before
	# children, so the tree assembles in one pass. Sites where the nested set
	# is absent fall back to name order, which still assembles correctly
	# because every node is created before it is linked.
	order_by = "lft asc" if compat.has_field("Account", "lft") else "name asc"
	accounts = frappe.db.get_all("Account", filters=filters, fields=fields, order_by=order_by)

	nodes = {row["name"]: {**row, "children": []} for row in accounts}
	roots = []
	for row in accounts:
		parent = row.get("parent_account")
		if parent and parent in nodes:
			nodes[parent]["children"].append(nodes[row["name"]])
		else:
			# Either a real root, or a node whose parent was filtered out by
			# root_type — both belong at the top of *this* response.
			roots.append(nodes[row["name"]])

	data = {
		"company": company,
		"root_type": root_type or None,
		"accounts": roots,
		"flat_count": len(accounts),
		"note": "children[] is nested; flat_count is every account in the response.",
	}
	return ToolResult(data, f"{len(accounts)} account(s) for {company}")


# ── 9. list_unreconciled_bank_transactions ──────────────────────────────────
def list_unreconciled_bank_transactions(args: dict) -> ToolResult:
	"""Bank Transactions with money still unallocated — the reconciliation worklist.

	Prefers this site's `unallocated_amount` when it has one, and otherwise
	computes `gross - allocated` itself, so the answer is the same on either
	schema.
	"""
	compat.require_doctype("Bank Transaction", "It ships with ERPNext's Accounts module.")
	bank_account = _resolve_bank_account(as_str(args, "bank_account", required=True))
	limit = as_limit(args)
	money = compat.bank_transaction_amount_fields()

	filters = {"bank_account": bank_account, "docstatus": ("<", 2)}
	if money["unallocated"]:
		filters[money["unallocated"]] = (">", 0)

	fields = compat.existing_fields(
		"Bank Transaction",
		[
			"name", "date", "bank_account", "company", "description", "status",
			"reference_number", "currency", "party_type", "party", "docstatus",
			"deposit", "withdrawal", "amount", "allocated_amount", "unallocated_amount",
		],
	)
	rows = frappe.db.get_all(
		"Bank Transaction",
		filters=filters,
		fields=fields,
		order_by="date asc",
		# Without an unallocated_amount column the filter cannot be pushed into
		# SQL, so over-fetch and cut in Python.
		limit=limit if money["unallocated"] else MAX_LIMIT * 4,
	)

	out = []
	for row in rows:
		gross = round(compat.gross_amount(row, money), 2)
		allocated = round(float(row.get(money["allocated"]) or 0), 2) if money["allocated"] else 0.0
		unallocated = (
			round(float(row.get(money["unallocated"]) or 0), 2)
			if money["unallocated"]
			else round(gross - allocated, 2)
		)
		if unallocated <= 0:
			continue
		out.append(
			{
				**row,
				"amount_signed": round(compat.signed_amount(row, money), 2),
				"gross_amount": gross,
				"allocated_amount_effective": allocated,
				"unallocated_amount_effective": unallocated,
			}
		)
		if len(out) >= limit:
			break

	data = {
		"unreconciled": out,
		"count": len(out),
		"bank_account": bank_account,
		"limit": limit,
		"truncated": len(out) == limit,
		"amount_layout": money["style"],
		"unallocated_source": "column" if money["unallocated"] else "computed (gross - allocated)",
	}
	return ToolResult(data, f"{bank_account}: {len(out)} unreconciled transaction(s)")


# ── 10. search_accounts ─────────────────────────────────────────────────────
def search_accounts(args: dict) -> ToolResult:
	"""Fuzzy account lookup — the tool that turns "Cash Clearing" into a docname.

	Exists so a client never has to guess ERPNext's `"<name> - <abbr>"` primary
	key. Results are ranked (exact number, exact name, prefix, then substring)
	because the top hit being right is what saves the follow-up call.
	"""
	query = as_str(args, "query", required=True)
	company = resolve_company(as_str(args, "company"))
	limit = as_limit(args)

	base = {"company": company} if company else {}
	fields = compat.existing_fields(
		"Account",
		[
			"name", "account_name", "account_number", "company", "root_type",
			"account_type", "is_group", "disabled", "parent_account", "account_currency",
		],
	)
	pattern = f"%{query}%"
	found = {}
	for filters in (
		{**base, "account_number": query},
		{**base, "account_name": query},
		{**base, "account_number": ("like", pattern)},
		{**base, "account_name": ("like", pattern)},
		{**base, "name": ("like", pattern)},
	):
		for row in frappe.db.get_all("Account", filters=filters, fields=fields, limit=limit * 4):
			found.setdefault(row["name"], row)

	needle = query.lower()

	def rank(row):
		number = str(row.get("account_number") or "").lower()
		account_name = str(row.get("account_name") or "").lower()
		if number and number == needle:
			return (0, account_name)
		if account_name == needle:
			return (1, account_name)
		if account_name.startswith(needle) or number.startswith(needle):
			return (2, account_name)
		if needle in account_name or needle in number:
			return (3, account_name)
		return (4, account_name)

	ranked = sorted(found.values(), key=rank)[:limit]
	data = {
		"query": query,
		"company": company,
		"matches": ranked,
		"count": len(ranked),
		"total_before_limit": len(found),
		"note": "Ranked best-first: exact number, exact name, prefix, substring.",
	}
	return ToolResult(data, f"search {query!r}: {len(ranked)} of {len(found)} match(es)")


# ── shared ──────────────────────────────────────────────────────────────────
def _resolve_bank_account(value: str) -> str:
	"""A Bank Account docname from a docname or an account_name.

	ERPNext's Bank Account primary key is `"<label> - <bank>"`, so the same
	problem as Account, solved the same way.
	"""
	value = (value or "").strip()
	if not value:
		return ""
	if frappe.db.exists("Bank Account", value):
		return value
	matches = frappe.db.get_all("Bank Account", filters={"account_name": value}, pluck="name")
	if len(matches) == 1:
		return matches[0]
	if len(matches) > 1:
		raise ToolError(
			f"{value!r} matches {len(matches)} Bank Accounts: {', '.join(sorted(matches)[:10])}"
		)
	known = frappe.db.get_all("Bank Account", pluck="name", limit=25)
	raise ToolError(
		f"no Bank Account matching {value!r}. "
		f"Known bank accounts: {', '.join(sorted(known)) or '<none>'}"
	)


def _docstatus_label(docstatus) -> str:
	return {0: "draft", 1: "submitted", 2: "cancelled"}.get(int(docstatus or 0), "unknown")
