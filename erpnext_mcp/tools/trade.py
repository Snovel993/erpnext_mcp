# SPDX-License-Identifier: MIT
"""Sales and purchasing: order books and receivables ageing.

ABOUT THE AGEING BUCKETS. `get_outstanding_invoices` reports an extra bucket the
brief did not ask for — `current` — for invoices that are not yet due. Folding
those into `0-30` is how an AR summary comes out looking worse than the business
is: an invoice issued yesterday on 30-day terms is not thirty days of exposure,
it is zero. Days overdue is `as_of - due_date`, so anything with a due date in
the future gets a negative number and its own bucket, and the buckets the brief
asked for keep exactly the meaning it asked for.

Invoices with no `due_date` are bucketed as `unknown` rather than assumed
current, because an invoice nobody put terms on is a real thing that a
receivables review should surface rather than hide.
"""

import frappe

from .. import compat
from ..args import as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

#: Upper bound of each ageing bucket, in days overdue.
AGEING_BUCKETS = (("0-30", 30), ("31-60", 60), ("61-90", 90), ("90+", None))


# ── 31. list_sales_orders ───────────────────────────────────────────────────
def list_sales_orders(args: dict) -> ToolResult:
	"""Sales Order headers, newest first."""
	return _order_book(
		args,
		doctype="Sales Order",
		party_arg="customer",
		party_field="customer",
		party_name_field="customer_name",
		promise_field="delivery_date",
		progress_field="per_delivered",
	)


# ── 33. list_purchase_orders ────────────────────────────────────────────────
def list_purchase_orders(args: dict) -> ToolResult:
	"""Purchase Order headers, newest first."""
	return _order_book(
		args,
		doctype="Purchase Order",
		party_arg="supplier",
		party_field="supplier",
		party_name_field="supplier_name",
		promise_field="schedule_date",
		progress_field="per_received",
	)


def _order_book(
	args: dict,
	*,
	doctype: str,
	party_arg: str,
	party_field: str,
	party_name_field: str,
	promise_field: str,
	progress_field: str,
) -> ToolResult:
	"""Sales and Purchase Orders are the same query with the nouns swapped.

	Keeping them one function rather than two near-copies means a fix to the
	date handling or the truncation reporting cannot land on one side only.
	"""
	compat.require_doctype(doctype, "It ships with ERPNext's Selling/Buying modules.")
	status = as_str(args, "status")
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	party = as_str(args, party_arg)
	company = resolve_company(as_str(args, "company"))
	limit = as_limit(args)

	filters = {}
	if status:
		filters["status"] = status
	if party:
		filters[party_field] = party
	if company:
		filters["company"] = company
	if from_date and to_date:
		if from_date > to_date:
			raise ToolError(f"from_date {from_date} is after to_date {to_date}")
		filters["transaction_date"] = ("between", [from_date, to_date])
	elif from_date:
		filters["transaction_date"] = (">=", from_date)
	elif to_date:
		filters["transaction_date"] = ("<=", to_date)

	fields = compat.existing_fields(
		doctype,
		[
			"name",
			party_field,
			party_name_field,
			"transaction_date",
			promise_field,
			"grand_total",
			"rounded_total",
			"currency",
			"status",
			progress_field,
			"per_billed",
			"docstatus",
			"company",
			"owner",
		],
	)
	rows = frappe.db.get_all(
		doctype,
		filters=filters,
		fields=fields,
		order_by="transaction_date desc, creation desc",
		limit=limit,
	)
	for row in rows:
		row["docstatus_label"] = _docstatus_label(row.get("docstatus"))

	by_status = {}
	for row in rows:
		key = row.get("status") or "<none>"
		by_status[key] = by_status.get(key, 0) + 1

	data = {
		"orders": rows,
		"doctype": doctype,
		"count": len(rows),
		"limit": limit,
		"truncated": len(rows) == limit,
		"total_value": round(sum(float(row.get("grand_total") or 0) for row in rows), 2),
		"by_status": by_status,
		"filters": {
			"status": status or None,
			party_arg: party or None,
			"company": company,
			"from_date": from_date,
			"to_date": to_date,
		},
		"note": (
			f"total_value sums grand_total across the rows returned, which is a "
			f"partial figure when truncated is true. {progress_field} and "
			"per_billed are percentages, 0-100."
		),
	}
	return ToolResult(data, f"{len(rows)} {doctype} row(s)")


# ── 32. get_outstanding_invoices ────────────────────────────────────────────
def get_outstanding_invoices(args: dict) -> ToolResult:
	"""Sales Invoices with money still owed, aged against a date."""
	compat.require_doctype("Sales Invoice", "It ships with ERPNext's Accounts module.")
	customer = as_str(args, "customer")
	company = resolve_company(as_str(args, "company"))
	as_of = as_date(args, "as_of") or frappe.utils.today()
	limit = as_limit(args)

	filters = {"outstanding_amount": (">", 0), "docstatus": 1}
	if customer:
		filters["customer"] = customer
	if company:
		filters["company"] = company

	fields = compat.existing_fields(
		"Sales Invoice",
		[
			"name",
			"customer",
			"customer_name",
			"posting_date",
			"due_date",
			"grand_total",
			"outstanding_amount",
			"currency",
			"status",
			"company",
			"is_return",
		],
	)
	rows = frappe.db.get_all(
		"Sales Invoice", filters=filters, fields=fields, order_by="due_date asc", limit=limit
	)

	buckets = {label: {"count": 0, "outstanding": 0.0} for label, _ in AGEING_BUCKETS}
	buckets["current"] = {"count": 0, "outstanding": 0.0}
	buckets["unknown"] = {"count": 0, "outstanding": 0.0}

	total = 0.0
	for row in rows:
		outstanding = float(row.get("outstanding_amount") or 0)
		total += outstanding
		days = _days_overdue(row.get("due_date"), as_of)
		label = _bucket(days)
		row["days_overdue"] = days
		row["ageing_bucket"] = label
		buckets[label]["count"] += 1
		buckets[label]["outstanding"] = round(buckets[label]["outstanding"] + outstanding, 2)

	data = {
		"invoices": rows,
		"count": len(rows),
		"limit": limit,
		"truncated": len(rows) == limit,
		"as_of": as_of,
		"total_outstanding": round(total, 2),
		"buckets": buckets,
		"filters": {"customer": customer or None, "company": company},
		"bucket_definition": (
			"days_overdue = as_of - due_date. 'current' is not yet due "
			"(days_overdue <= 0); '0-30', '31-60', '61-90' and '90+' are days past "
			"due; 'unknown' is an invoice with no due_date."
		),
	}
	return ToolResult(
		data,
		f"{len(rows)} outstanding invoice(s) as of {as_of}, {round(total, 2)} total",
	)


def _days_overdue(due_date, as_of: str):
	if not due_date:
		return None
	try:
		return (frappe.utils.getdate(as_of) - frappe.utils.getdate(due_date)).days
	except Exception:
		return None


def _bucket(days) -> str:
	if days is None:
		return "unknown"
	if days <= 0:
		return "current"
	for label, upper in AGEING_BUCKETS:
		if upper is None or days <= upper:
			return label
	return "90+"


def _docstatus_label(docstatus) -> str:
	return {0: "draft", 1: "submitted", 2: "cancelled"}.get(int(docstatus or 0), "unknown")
