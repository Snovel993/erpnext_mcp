# SPDX-License-Identifier: MIT
"""Expense receipt capture — the MCP surface over Expense Receipt.

v0.31.0. The write end of this is a phone. A foreman stands at a fuel pump or a
parts counter, photographs the slip, iOS Vision OCR reads it on-device, and the
app posts the extracted fields, the photograph and the raw OCR text in one call
to `submit_expense_receipt`. The read end is a bookkeeper at a desk asking which
receipts are waiting and which of them the scanner was unsure about.

WHY THE OCR RUNS ON THE PHONE AND NOT HERE. The photograph is the largest thing
in the payload and the extraction is the cheapest part of the job; doing it on
the device means the foreman sees the merchant and the total on screen before
they put the phone away, and can correct them while the paper is still in their
hand. By the time this module sees a receipt, a person has already looked at what
the machine read. That is why `submit_expense_receipt` takes extracted fields
rather than an image to parse, and why it takes `ocr_confidence` as data rather
than computing one.

APPROVAL AND REJECTION ARE SEPARATE TOOLS WITH SEPARATE SWITCHES. They are not
one `review_expense_receipt` with a verdict argument, because an operator who
wants a manager to be able to approve reimbursements does not necessarily want
the same surface able to refuse them, and a single switch cannot express that. It
is also the difference between two settings an operator reads in the form and one
they have to reason about.

A REJECTION REQUIRES A REASON. Not because the schema demands a non-empty string,
but because "rejected" with no sentence beside it is the state that generates the
next three messages asking why — and by the time anybody asks, the person who
refused it has forgotten. It is stored on the record, not in a comment, so
`get_expense_receipt` returns it to the phone that submitted the thing.

────────────────────────────────────────────────────────────────────────────
v0.67.0: THE SUPPLIER AND ITEM LINKS, AND WHY THEY SIT BESIDE THE TEXT
────────────────────────────────────────────────────────────────────────────

Sprint 1 gave this app the ability to create a Supplier and an Item; this
release lets a receipt point at them. `supplier` on the header and `item` on
each line are both OPTIONAL and both ADDITIVE — `merchant` and `description`
keep saying exactly what the paper said.

That is the whole design. A slip printed `VALLEY CO-OP #14` and a Supplier
record called `Valley Co-operative` are the same vendor, and a capture that
replaced the first with the second would lose the evidence in the act of
improving the data. Keeping both is what lets a bookkeeper total a year of fuel
per vendor AND still show an auditor the string the machine read.

NEITHER IS EVER INFERRED. No fuzzy match from merchant to Supplier, no lookup
from an OCR'd line to an Item. `HYD HOSE 1/2` matches four items in a real
catalogue, and a guess would put a fabricated consumption figure somewhere a
person would later read as a measurement. A link gets set when a human — or a
client with a picker in front of a human — says so, or it stays empty.

Both are refused by name on a bench without ERPNext rather than written as
dangling links, because a Link column pointing at a doctype the site does not
have is a record that cannot be opened in the Desk.
"""

from __future__ import annotations

import frappe
from frappe.utils import today

from ..args import as_date, as_float, as_int, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

EXPENSE_RECEIPT = "Expense Receipt"
EXPENSE_RECEIPT_ITEM = "Expense Receipt Item"
EMPLOYEE = "Employee"
FARM_TASK = "Farm Task"
SUPPLIER = "Supplier"
ITEM = "Item"

DRAFT = "Draft"
SUBMITTED = "Submitted"
APPROVED = "Approved"
REJECTED = "Rejected"

#: Every status the doctype declares, in the order it moves through them.
STATUSES = (DRAFT, SUBMITTED, APPROVED, REJECTED)

#: What `submit_expense_receipt` will create. A receipt captured on a phone is
#: Submitted — the foreman pressed the button — but the tool accepts Draft so a
#: client with an offline queue can post something it has not finished with.
CREATABLE_STATUSES = (DRAFT, SUBMITTED)

#: The statuses a review decision may still be taken from. Deciding an already
#: decided receipt would overwrite the name and date of whoever decided it first,
#: which is the one thing an approval record exists to preserve.
REVIEWABLE_STATUSES = (DRAFT, SUBMITTED)

#: The categories the Select ships with. Kept here as well as in the DocType JSON
#: so a bad value is refused with the list rather than with a Frappe traceback,
#: and asserted equal to the JSON's options by the tests.
CATEGORIES = (
	"Fuel",
	"Equipment Parts",
	"Supplies",
	"Hardware",
	"Feed",
	"Seed",
	"Fertilizer",
	"Other",
)

#: What every read tool returns for a receipt, minus the raw OCR text and the
#: line items — both of which are large and only `get_expense_receipt` returns.
_LIST_FIELDS = (
	"name",
	"merchant",
	"amount",
	"receipt_date",
	"category",
	"status",
	"company",
	"submitted_by",
	"supplier",
	"farm_task",
	"ocr_confidence",
	"receipt_image",
	"approved_by",
	"approved_date",
	"rejected_by",
	"rejected_date",
	"rejection_reason",
	"notes",
	"modified",
)


# ── helpers ───────────────────────────────────────────────────────────────


def _resolve_employee(value: str, label: str) -> str:
	"""An Employee docname from a docname or an employee_name.

	A phone knows the worker it logged in as; a manager typing into a chat client
	knows a person's name. Both are "the employee" and only one is the key.
	"""
	if not value:
		raise ToolError(f"{label} is required.")
	if frappe.db.exists(EMPLOYEE, value):
		return str(value)
	found = frappe.db.get_value(EMPLOYEE, {"employee_name": value}, "name")
	if found:
		return str(found)
	raise ToolError(f"no Employee called {value!r} on this site.")


def _require_receipt(args: dict) -> str:
	"""The docname of the receipt this call is about, or a refusal naming why."""
	name = as_str(args, "name") or as_str(args, "expense_receipt") or as_str(args, "receipt")
	if not name:
		raise ToolError("name (the Expense Receipt docname) is required.")
	if not frappe.db.exists(EXPENSE_RECEIPT, name):
		raise ToolError(f"no Expense Receipt called {name!r} on this site.")
	return name


def _row_out(row: dict) -> dict:
	"""One receipt as JSON: dates as ISO strings, numbers as numbers."""
	out = {}
	for key, value in dict(row).items():
		if value is None:
			out[key] = None
		elif key in ("amount", "ocr_confidence"):
			out[key] = float(value or 0)
		elif key in ("receipt_date", "approved_date", "rejected_date", "modified"):
			out[key] = str(value)
		else:
			out[key] = value
	return out


def _items_out(doc) -> list[dict]:
	"""The line items off a receipt document, whichever shape the rows are in."""
	items = []
	for row in doc.get("items") or []:
		get = row.get if isinstance(row, dict) else (lambda k, d=None, r=row: getattr(r, k, d))
		items.append(
			{
				"description": get("description"),
				"item": get("item"),
				"quantity": float(get("quantity") or 0),
				"unit_price": float(get("unit_price") or 0),
				"line_total": float(get("line_total") or 0),
			}
		)
	return items


def _linked(doctype: str, value: str, label: str) -> str:
	"""An optional Link argument, proved to exist, or a refusal naming the reason.

	A bench without ERPNext has no Supplier and no Item at all, and the refusal
	says THAT rather than "no Supplier called 'X'" — which would read as a typo
	and send somebody looking for a record that could never be there.
	"""
	if not value:
		return ""
	if not frappe.db.exists("DocType", doctype):
		raise ToolError(
			f"this site has no {doctype} doctype, so {label} cannot be set. {doctype} "
			f"ships with the ERPNext app; the receipt captures fine without it."
		)
	if not frappe.db.exists(doctype, value):
		raise ToolError(f"no {doctype} called {value!r} on this site.")
	return str(value)


def _read_items(args: dict) -> list[dict]:
	"""The `items` argument, validated into rows the child table will accept.

	A missing total is filled from quantity times unit price — OCR reads a bold
	receipt total far more reliably than it reads a column of line arithmetic, so
	the derived number is usually better than the read one. A total the scanner
	DID read is kept: a receipt that charges four at $3 and totals $11.50 after a
	discount is telling the truth, and the multiplication is not.
	"""
	raw = args.get("items")
	if raw in (None, "", []):
		return []
	if not isinstance(raw, list):
		raise ToolError(
			"items must be a list of objects with description, quantity, unit_price and line_total."
		)

	rows = []
	for index, item in enumerate(raw, start=1):
		if not isinstance(item, dict):
			raise ToolError(f"items[{index}] must be an object, got {type(item).__name__}.")
		quantity = as_float(item.get("quantity"), f"items[{index}].quantity")
		unit_price = as_float(item.get("unit_price"), f"items[{index}].unit_price")
		line_total = as_float(item.get("line_total"), f"items[{index}].line_total")
		if not line_total and quantity and unit_price:
			line_total = round(quantity * unit_price, 2)
		rows.append(
			{
				"description": as_str(item, "description") or None,
				"item": _linked(ITEM, as_str(item, "item"), f"items[{index}].item") or None,
				"quantity": quantity,
				"unit_price": unit_price,
				"line_total": line_total,
			}
		)
	return rows


def _confidence(args: dict) -> float | None:
	"""`ocr_confidence` as a fraction, or a refusal saying it is not a percentage."""
	raw = args.get("ocr_confidence")
	if raw in (None, ""):
		return None
	value = as_float(raw, "ocr_confidence")
	if value < 0 or value > 1:
		raise ToolError(
			f"ocr_confidence is a fraction from 0 to 1, not a percentage — got {value}. "
			f"A scanner reporting 87 means 0.87."
		)
	return value


# ── read tools ────────────────────────────────────────────────────────────


def list_expense_receipts(args: dict) -> ToolResult:
	"""Receipts, filtered by status, employee, company and date range."""
	filters = {}

	company = as_str(args, "company")
	if company:
		filters["company"] = resolve_company(company)

	status = as_str(args, "status")
	if status:
		if status not in STATUSES:
			raise ToolError(f"status must be one of: {', '.join(STATUSES)}.")
		filters["status"] = status

	employee = as_str(args, "employee") or as_str(args, "submitted_by")
	if employee:
		filters["submitted_by"] = _resolve_employee(employee, "employee")

	category = as_str(args, "category")
	if category:
		if category not in CATEGORIES:
			raise ToolError(f"category must be one of: {', '.join(CATEGORIES)}.")
		filters["category"] = category

	farm_task = as_str(args, "farm_task")
	if farm_task:
		filters["farm_task"] = farm_task

	supplier = as_str(args, "supplier")
	if supplier:
		filters["supplier"] = _linked(SUPPLIER, supplier, "supplier")

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["receipt_date"] = ("between", [from_date, to_date])
	elif from_date:
		filters["receipt_date"] = (">=", from_date)
	elif to_date:
		filters["receipt_date"] = ("<=", to_date)

	limit = as_int(args, "limit", 100) or 100
	if limit > 500:
		limit = 500

	# Lowest confidence first, so the receipts a person most needs to open the
	# photo for are the ones at the top of the page rather than at the end of it.
	rows = frappe.db.get_all(
		EXPENSE_RECEIPT,
		filters=filters,
		fields=list(_LIST_FIELDS),
		limit_page_length=limit,
		order_by="ocr_confidence asc, receipt_date desc",
	)
	receipts = [_row_out(row) for row in rows]
	total = sum(receipt["amount"] for receipt in receipts)

	data = {
		"receipts": receipts,
		"count": len(receipts),
		"total_amount": round(total, 2),
	}
	return ToolResult(
		data=data,
		summary=f"{len(receipts)} expense receipt(s) totalling {round(total, 2)}",
	)


def get_expense_receipt(args: dict) -> ToolResult:
	"""One receipt in full, including the line items and the raw OCR text."""
	name = _require_receipt(args)
	doc = frappe.get_doc(EXPENSE_RECEIPT, name)

	data = _row_out({field: doc.get(field) for field in _LIST_FIELDS})
	data["ocr_raw_text"] = doc.get("ocr_raw_text")
	data["items"] = _items_out(doc)
	data["items_total"] = round(sum(item["line_total"] for item in data["items"]), 2)

	return ToolResult(
		data=data,
		summary=f"Expense receipt {name}: {doc.get('merchant')} {doc.get('amount')} "
		f"on {doc.get('receipt_date')} — {doc.get('status')}",
	)


# ── mutating tools ────────────────────────────────────────────────────────


def submit_expense_receipt(args: dict) -> ToolResult:
	"""Create a receipt from a photograph and what the scanner read off it."""
	merchant = as_str(args, "merchant", required=True)
	amount = as_float(args.get("amount"), "amount")
	if args.get("amount") in (None, ""):
		raise ToolError("amount is required.")
	if amount < 0:
		raise ToolError("amount cannot be negative. A refund is a credit note, not an expense receipt.")

	receipt_date = as_date(args, "receipt_date", required=True)
	company = resolve_company(as_str(args, "company"), required=True)
	submitted_by = _resolve_employee(as_str(args, "submitted_by") or as_str(args, "employee"), "submitted_by")

	category = as_str(args, "category") or "Other"
	if category not in CATEGORIES:
		raise ToolError(f"category must be one of: {', '.join(CATEGORIES)}.")

	status = as_str(args, "status") or SUBMITTED
	if status not in CREATABLE_STATUSES:
		raise ToolError(
			f"status must be {' or '.join(CREATABLE_STATUSES)} on submission. "
			f"Approval and rejection are separate tools."
		)

	farm_task = as_str(args, "farm_task")
	if farm_task and not frappe.db.exists(FARM_TASK, farm_task):
		raise ToolError(f"no Farm Task called {farm_task!r} on this site.")

	supplier = _linked(SUPPLIER, as_str(args, "supplier"), "supplier")

	doc = frappe.get_doc(
		{
			"doctype": EXPENSE_RECEIPT,
			"merchant": merchant,
			"amount": amount,
			"receipt_date": receipt_date,
			"category": category,
			"company": company,
			"submitted_by": submitted_by,
			"supplier": supplier or None,
			"farm_task": farm_task or None,
			"status": status,
			"receipt_image": as_str(args, "receipt_image") or None,
			"ocr_raw_text": as_str(args, "ocr_raw_text") or None,
			"ocr_confidence": _confidence(args),
			"notes": as_str(args, "notes") or None,
		}
	)
	for row in _read_items(args):
		doc.append("items", row)

	doc.flags.ignore_permissions = True
	doc.insert()

	return ToolResult(
		data={
			"name": doc.name,
			"merchant": merchant,
			"amount": amount,
			"receipt_date": str(receipt_date),
			"category": category,
			"status": status,
			"company": company,
			"submitted_by": submitted_by,
			"supplier": supplier or None,
			"farm_task": farm_task or None,
			"ocr_confidence": doc.get("ocr_confidence"),
			"receipt_image": doc.get("receipt_image"),
			"items": _items_out(doc),
		},
		summary=f"Expense receipt {doc.name} captured: {merchant} {amount} on {receipt_date} "
		f"({category}) by {submitted_by}",
		docstatus_delta=f"none → {status}",
	)


def approve_expense_receipt(args: dict) -> ToolResult:
	"""Approve a receipt, recording who approved it and when."""
	name = _require_receipt(args)
	status = frappe.db.get_value(EXPENSE_RECEIPT, name, "status")
	if status not in REVIEWABLE_STATUSES:
		raise ToolError(
			f"expense receipt {name} is already {status!r}. "
			f"Only {' or '.join(REVIEWABLE_STATUSES)} receipts can be approved."
		)

	approved_by = _resolve_employee(as_str(args, "approved_by") or as_str(args, "employee"), "approved_by")
	approved_date = as_date(args, "approved_date") or today()

	frappe.db.set_value(
		EXPENSE_RECEIPT,
		name,
		{
			"status": APPROVED,
			"approved_by": approved_by,
			"approved_date": approved_date,
		},
	)

	merchant, amount = frappe.db.get_value(EXPENSE_RECEIPT, name, ["merchant", "amount"])
	return ToolResult(
		data={
			"name": name,
			"status": APPROVED,
			"approved_by": approved_by,
			"approved_date": str(approved_date),
		},
		summary=f"Expense receipt {name} ({merchant} {amount}) approved by {approved_by} on {approved_date}",
		docstatus_delta=f"{status} → {APPROVED}",
	)


def reject_expense_receipt(args: dict) -> ToolResult:
	"""Reject a receipt with a reason, recording who rejected it and when."""
	name = _require_receipt(args)
	status = frappe.db.get_value(EXPENSE_RECEIPT, name, "status")
	if status not in REVIEWABLE_STATUSES:
		raise ToolError(
			f"expense receipt {name} is already {status!r}. "
			f"Only {' or '.join(REVIEWABLE_STATUSES)} receipts can be rejected."
		)

	reason = as_str(args, "reason") or as_str(args, "rejection_reason")
	if not reason:
		raise ToolError(
			"reason is required to reject a receipt. A rejection with no sentence beside it "
			"is the state that generates the next three messages asking why."
		)

	rejected_by = _resolve_employee(as_str(args, "rejected_by") or as_str(args, "employee"), "rejected_by")
	rejected_date = as_date(args, "rejected_date") or today()

	frappe.db.set_value(
		EXPENSE_RECEIPT,
		name,
		{
			"status": REJECTED,
			"rejected_by": rejected_by,
			"rejected_date": rejected_date,
			"rejection_reason": reason,
		},
	)

	merchant, amount = frappe.db.get_value(EXPENSE_RECEIPT, name, ["merchant", "amount"])
	return ToolResult(
		data={
			"name": name,
			"status": REJECTED,
			"rejected_by": rejected_by,
			"rejected_date": str(rejected_date),
			"rejection_reason": reason,
		},
		summary=f"Expense receipt {name} ({merchant} {amount}) rejected by {rejected_by} "
		f"on {rejected_date}: {reason}",
		docstatus_delta=f"{status} → {REJECTED}",
	)
