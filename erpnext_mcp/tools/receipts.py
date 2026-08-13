# SPDX-License-Identifier: MIT
"""Receipt capture — scale tickets, settlement statements, and what a photo is.

v0.67.0. Sprint 2 of the gap-closure plan, and the design principle behind it is
one sentence: **the receipt is the financial atom**. A foreman photographs a piece
of paper. Which piece of paper it is decides which register it lands in, and that
decision is the only branch in an otherwise identical flow — photograph, extract,
file, review. Four kinds of paper, one capture:

    a fuel slip           → Expense Receipt   (v0.31.0, enhanced here)
    a scale ticket        → Scale Ticket      (this release)
    a packout settlement  → Settlement Statement (this release)
    a vendor invoice      → Purchase Invoice  (a later sprint)

`classify_receipt` is the branch. Everything else here is the two new registers.

────────────────────────────────────────────────────────────────────────────
WHY A SCALE TICKET IS ITS OWN DOCTYPE AND NOT A DELIVERY NOTE
────────────────────────────────────────────────────────────────────────────

ERPNext has a Delivery Note, and it is the wrong shape for this. A Delivery Note
is the *seller's* document about goods leaving on the seller's terms, priced,
against a Sales Order, in Items and UOMs the seller controls. A scale ticket is
a third party's weight record: the packer owns the scale, the packer prints the
slip, the variety is whatever the packer's clerk wrote, and there is no price on
it at all — the price arrives months later on a settlement.

Forcing that onto a Delivery Note would mean inventing an Item per variety per
grade before a foreman could photograph a slip at a tailgate, and a capture that
requires master data to exist first is a capture that does not happen. So the
ticket is stored as what it is, and the connection to priced, Item-shaped
documents is made later by the settlement — where the packer has finally said
what the fruit was worth.

────────────────────────────────────────────────────────────────────────────
THE TWO REGISTERS ARE INDEPENDENT ON PURPOSE
────────────────────────────────────────────────────────────────────────────

A Settlement Statement's `gross_delivered_weight` is what the PACKER says
arrived. The sum of the Scale Tickets matched to it is what the GROWER's own
copies say arrived. Nothing in this module reconciles them, overwrites one with
the other, or refuses a settlement whose figure disagrees.

`get_settlement_statement` reports both and names the difference, because the
difference IS the answer — it is the entire reason a grower keeps ticket stubs,
and a tool that quietly agreed them would delete the only audit this document
has.

────────────────────────────────────────────────────────────────────────────
CREATE IS ALWAYS A DRAFT; SUBMIT IS A SEPARATE TOOL AND A SEPARATE SWITCH
────────────────────────────────────────────────────────────────────────────

Both doctypes are submittable, and both `create_*` tools leave the document at
docstatus 0. That is not timidity about writes — it is that submitting a scale
ticket makes a third party's weight record immutable, and an operator who wants
a phone to be able to capture tickets does not necessarily want the same phone
able to freeze them. One switch cannot express that; two can.
"""

from __future__ import annotations

import frappe

from ..args import as_date, as_float, as_int, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

SCALE_TICKET = "Scale Ticket"
SETTLEMENT_STATEMENT = "Settlement Statement"
CUSTOMER = "Customer"
FIELD = "Field"

#: Scale Ticket statuses, and the Settlement Statement's. Both are computed by
#: their controllers from docstatus; these exist so a filter argument can be
#: refused with the list rather than silently matching nothing.
TICKET_STATUSES = ("Draft", "Submitted", "Matched", "Cancelled")
SETTLEMENT_STATUSES = ("Draft", "Submitted", "Posted", "Cancelled")

#: One unit for all the weights on one document. Mirrored from the two DocType
#: JSONs and asserted equal to them by the tests.
WEIGHT_UOMS = ("Kg", "Lb", "Ton", "Bin", "Box")

DEDUCTION_TYPES = ("Packing", "Cold Storage", "Marketing", "Commission", "Other")

#: What every Scale Ticket read returns. `ticket_image` and `notes` are included
#: because a list of tickets is something a bookkeeper scans for the ones that
#: need a photo opened, exactly as with expense receipts.
_TICKET_FIELDS = (
	"name",
	"ticket_number",
	"date",
	"customer",
	"company",
	"variety",
	"grade",
	"gross_weight",
	"tare_weight",
	"net_weight",
	"weight_uom",
	"field",
	"block",
	"truck_id",
	"driver",
	"destination",
	"status",
	"settlement",
	"ticket_image",
	"notes",
	"docstatus",
	"modified",
)

_SETTLEMENT_FIELDS = (
	"name",
	"statement_number",
	"date",
	"customer",
	"company",
	"period_start",
	"period_end",
	"gross_delivered_weight",
	"packed_weight",
	"packout_pct",
	"cull_weight",
	"cull_pct",
	"weight_uom",
	"total_gross_revenue",
	"total_deductions",
	"net_proceeds",
	"status",
	"posted_journal_entry",
	"statement_image",
	"notes",
	"docstatus",
	"modified",
)

_FLOAT_FIELDS = frozenset(
	{
		"gross_weight",
		"tare_weight",
		"net_weight",
		"gross_delivered_weight",
		"packed_weight",
		"cull_weight",
		"packout_pct",
		"cull_pct",
		"total_gross_revenue",
		"total_deductions",
		"net_proceeds",
	}
)

_DATE_FIELDS = frozenset({"date", "period_start", "period_end", "modified"})


# ── helpers ───────────────────────────────────────────────────────────────


def _row_out(row: dict) -> dict:
	"""One document as JSON: dates as ISO strings, weights and money as numbers."""
	out = {}
	for key, value in dict(row).items():
		if value is None:
			out[key] = None
		elif key in _FLOAT_FIELDS:
			out[key] = float(value or 0)
		elif key == "docstatus":
			out[key] = int(value or 0)
		elif key in _DATE_FIELDS:
			out[key] = str(value)
		else:
			out[key] = value
	return out


def _require(doctype: str, args: dict, *aliases: str) -> str:
	"""The docname this call is about, or a refusal naming why."""
	name = ""
	for key in ("name", *aliases):
		name = as_str(args, key)
		if name:
			break
	if not name:
		raise ToolError(f"name (the {doctype} docname) is required.")
	if not frappe.db.exists(doctype, name):
		raise ToolError(f"no {doctype} called {name!r} on this site.")
	return name


def _customer(value: str, required: bool = False) -> str:
	"""A Customer docname from a docname or a customer_name.

	A phone knows the packer as the name on the ticket; the register knows it as
	a docname, and on a stock ERPNext site those are the same string — until
	somebody enables customer naming by series, at which point they never are
	again. Both are accepted, and neither is guessed at.
	"""
	if not value:
		if required:
			raise ToolError("customer is required — a scale ticket is a delivery TO somebody.")
		return ""
	if frappe.db.exists(CUSTOMER, value):
		return str(value)
	found = frappe.db.get_value(CUSTOMER, {"customer_name": value}, "name")
	if found:
		return str(found)
	raise ToolError(f"no Customer called {value!r} on this site. Create the packer first.")


def _weight_uom(args: dict, default: str = "Kg") -> str:
	value = as_str(args, "weight_uom") or default
	if value not in WEIGHT_UOMS:
		raise ToolError(f"weight_uom must be one of: {', '.join(WEIGHT_UOMS)}.")
	return value


def _status_filter(args: dict, allowed) -> str:
	status = as_str(args, "status")
	if status and status not in allowed:
		raise ToolError(f"status must be one of: {', '.join(allowed)}.")
	return status


def _date_range(args: dict, filters: dict, fieldname: str = "date") -> tuple:
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		if from_date > to_date:
			raise ToolError(f"from_date {from_date} is after to_date {to_date}")
		filters[fieldname] = ("between", [from_date, to_date])
	elif from_date:
		filters[fieldname] = (">=", from_date)
	elif to_date:
		filters[fieldname] = ("<=", to_date)
	return from_date, to_date


def _limit(args: dict) -> int:
	limit = as_int(args, "limit", 100) or 100
	return min(limit, 500)


def _optional_field(args: dict) -> str:
	"""The `field` argument, checked against the Field register if there is one.

	A bench without the Field doctype refuses a field argument by name rather
	than writing a dangling link — and a bench WITH it refuses a field nobody
	created, which on a farm is nearly always a typo for one that exists.
	"""
	value = as_str(args, "field")
	if not value:
		return ""
	if not frappe.db.exists("DocType", FIELD):
		raise ToolError(
			"this site has no Field doctype, so a scale ticket cannot name one. "
			"Run `bench migrate`, or omit field and record the origin in block."
		)
	if not frappe.db.exists(FIELD, value):
		raise ToolError(f"no Field called {value!r} on this site.")
	return value


# ── Scale Ticket: reads ───────────────────────────────────────────────────


def list_scale_tickets(args: dict) -> ToolResult:
	"""Scale tickets by customer, company, status and delivery date range."""
	filters = {}

	company = as_str(args, "company")
	if company:
		filters["company"] = resolve_company(company)

	customer = as_str(args, "customer")
	if customer:
		filters["customer"] = _customer(customer)

	status = _status_filter(args, TICKET_STATUSES)
	if status:
		filters["status"] = status

	variety = as_str(args, "variety")
	if variety:
		filters["variety"] = variety

	field = as_str(args, "field")
	if field:
		filters["field"] = _optional_field(args)

	settlement = as_str(args, "settlement")
	if settlement:
		filters["settlement"] = settlement

	# `unmatched=true` is the question this register exists to answer: which
	# delivered loads has nobody paid for yet. Expressed as its own argument
	# rather than as `settlement=""`, because an empty string in a filter reads
	# as "no filter" everywhere else in this catalogue.
	if args.get("unmatched") in (True, "true", "True", 1, "1"):
		filters["settlement"] = ("in", ("", None))

	from_date, to_date = _date_range(args, filters)
	limit = _limit(args)

	rows = frappe.db.get_all(
		SCALE_TICKET,
		filters=filters,
		fields=list(_TICKET_FIELDS),
		limit_page_length=limit,
		order_by="date desc, creation desc",
	)
	tickets = [_row_out(row) for row in rows]

	by_status = {}
	for ticket in tickets:
		key = ticket.get("status") or "<none>"
		by_status[key] = by_status.get(key, 0) + 1

	data = {
		"scale_tickets": tickets,
		"count": len(tickets),
		"limit": limit,
		"truncated": len(tickets) == limit,
		"total_net_weight": round(sum(t["net_weight"] for t in tickets), 3),
		"by_status": by_status,
		"by_weight_uom": _by_uom(tickets),
		"filters": {
			"customer": filters.get("customer") or None,
			"company": filters.get("company") or None,
			"status": status or None,
			"from_date": from_date,
			"to_date": to_date,
		},
		"note": (
			"total_net_weight sums net_weight across the rows returned, which is a "
			"partial figure when truncated is true — and a MEANINGLESS one when "
			"by_weight_uom names more than one unit, because kilos and bins do not add."
		),
	}
	return ToolResult(
		data=data,
		summary=f"{len(tickets)} scale ticket(s), {round(sum(t['net_weight'] for t in tickets), 3)} net",
	)


def _by_uom(rows: list) -> dict:
	"""How many rows in each weight unit. The guard on a summed total."""
	counts = {}
	for row in rows:
		key = row.get("weight_uom") or "<none>"
		counts[key] = counts.get(key, 0) + 1
	return counts


def get_scale_ticket(args: dict) -> ToolResult:
	"""One scale ticket in full, with the settlement that claimed it if any."""
	name = _require(SCALE_TICKET, args, "scale_ticket", "ticket")
	doc = frappe.get_doc(SCALE_TICKET, name)

	data = _row_out({field: doc.get(field) for field in _TICKET_FIELDS})
	data["weight_check"] = _weight_check(doc)

	if doc.get("settlement"):
		row = (
			frappe.db.get_value(
				SETTLEMENT_STATEMENT,
				doc.get("settlement"),
				["name", "statement_number", "date", "status", "net_proceeds"],
				as_dict=True,
			)
			or {}
		)
		data["settlement_detail"] = _row_out(row) if row else None

	return ToolResult(
		data=data,
		summary=f"Scale ticket {name} ({doc.get('ticket_number')}): "
		f"{doc.get('net_weight')} {doc.get('weight_uom')} of {doc.get('variety') or 'fruit'} "
		f"to {doc.get('customer')} on {doc.get('date')} — {doc.get('status')}",
	)


def _weight_check(doc) -> dict:
	"""Gross, tare and the subtraction, restated so a reader can see the sum.

	The whole disagreement a scale ticket exists to settle is arithmetic, and a
	tool that returned only the answer would be asking to be trusted about it.
	"""
	gross = float(doc.get("gross_weight") or 0)
	tare = float(doc.get("tare_weight") or 0)
	return {
		"gross_weight": gross,
		"tare_weight": tare,
		"net_weight": float(doc.get("net_weight") or 0),
		"weight_uom": doc.get("weight_uom"),
		"computed_as": f"{gross} - {tare} = {round(gross - tare, 3)}",
	}


# ── Scale Ticket: writes ──────────────────────────────────────────────────


def create_scale_ticket(args: dict) -> ToolResult:
	"""Capture one scale ticket as a draft."""
	ticket_number = as_str(args, "ticket_number", required=True)
	date = as_date(args, "date", required=True)
	company = resolve_company(as_str(args, "company"), required=True)
	customer = _customer(as_str(args, "customer"), required=True)
	weight_uom = _weight_uom(args)

	gross = _weight(args, "gross_weight")
	tare = _weight(args, "tare_weight")
	if gross and tare > gross:
		raise ToolError(
			f"tare_weight {tare} is greater than gross_weight {gross}, which would make the "
			f"net weight negative. That is two numbers off different tickets, or a gross and "
			f"a tare in different units. Nothing was created."
		)

	doc = frappe.get_doc(
		{
			"doctype": SCALE_TICKET,
			"ticket_number": ticket_number,
			"date": date,
			"customer": customer,
			"company": company,
			"variety": as_str(args, "variety") or None,
			"grade": as_str(args, "grade") or None,
			"gross_weight": gross,
			"tare_weight": tare,
			"weight_uom": weight_uom,
			"field": _optional_field(args) or None,
			"block": as_str(args, "block") or None,
			"truck_id": as_str(args, "truck_id") or None,
			"driver": as_str(args, "driver") or None,
			"destination": as_str(args, "destination") or None,
			"ticket_image": as_str(args, "ticket_image") or None,
			"notes": as_str(args, "notes") or None,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()

	data = _row_out({field: doc.get(field) for field in _TICKET_FIELDS})
	return ToolResult(
		data=data,
		summary=f"Scale ticket {doc.name} captured as a draft: ticket {ticket_number}, "
		f"{doc.get('net_weight')} {weight_uom} net to {customer} on {date}",
		docstatus_delta="none → draft",
	)


def _weight(args: dict, key: str) -> float:
	"""One weight argument, absent reading as zero and negative refused."""
	raw = args.get(key)
	if raw in (None, ""):
		return 0.0
	value = as_float(raw, key)
	if value < 0:
		raise ToolError(f"{key} cannot be negative — got {value}. Nothing was created.")
	return value


def submit_scale_ticket(args: dict) -> ToolResult:
	"""Submit a draft scale ticket, which makes it immutable."""
	name = _require(SCALE_TICKET, args, "scale_ticket", "ticket")
	doc = frappe.get_doc(SCALE_TICKET, name)

	if int(doc.get("docstatus") or 0) == 1:
		raise ToolError(f"scale ticket {name} is already submitted. Nothing was changed.")
	if int(doc.get("docstatus") or 0) == 2:
		raise ToolError(
			f"scale ticket {name} is cancelled. A cancelled ticket is amended into a new "
			f"one rather than resubmitted. Nothing was changed."
		)

	doc.flags.ignore_permissions = True
	doc.submit()

	return ToolResult(
		data={
			"name": name,
			"ticket_number": doc.get("ticket_number"),
			"status": doc.get("status"),
			"docstatus": int(doc.get("docstatus") or 0),
			"net_weight": float(doc.get("net_weight") or 0),
			"weight_uom": doc.get("weight_uom"),
			"customer": doc.get("customer"),
			"date": str(doc.get("date")),
		},
		summary=f"Scale ticket {name} submitted: {doc.get('net_weight')} "
		f"{doc.get('weight_uom')} net to {doc.get('customer')}",
		docstatus_delta="draft → submitted",
	)


# ── Settlement Statement: reads ───────────────────────────────────────────


def list_settlement_statements(args: dict) -> ToolResult:
	"""Settlement statements by customer, company, status and statement date."""
	filters = {}

	company = as_str(args, "company")
	if company:
		filters["company"] = resolve_company(company)

	customer = as_str(args, "customer")
	if customer:
		filters["customer"] = _customer(customer)

	status = _status_filter(args, SETTLEMENT_STATUSES)
	if status:
		filters["status"] = status

	from_date, to_date = _date_range(args, filters)
	limit = _limit(args)

	rows = frappe.db.get_all(
		SETTLEMENT_STATEMENT,
		filters=filters,
		fields=list(_SETTLEMENT_FIELDS),
		limit_page_length=limit,
		order_by="date desc, creation desc",
	)
	statements = [_row_out(row) for row in rows]

	by_status = {}
	for statement in statements:
		key = statement.get("status") or "<none>"
		by_status[key] = by_status.get(key, 0) + 1

	data = {
		"settlement_statements": statements,
		"count": len(statements),
		"limit": limit,
		"truncated": len(statements) == limit,
		"total_net_proceeds": round(sum(s["net_proceeds"] for s in statements), 2),
		"total_deductions": round(sum(s["total_deductions"] for s in statements), 2),
		"by_status": by_status,
		"filters": {
			"customer": filters.get("customer") or None,
			"company": filters.get("company") or None,
			"status": status or None,
			"from_date": from_date,
			"to_date": to_date,
		},
	}
	return ToolResult(
		data=data,
		summary=f"{len(statements)} settlement statement(s), "
		f"{round(sum(s['net_proceeds'] for s in statements), 2)} net proceeds",
	)


def get_settlement_statement(args: dict) -> ToolResult:
	"""One settlement in full: lines, deductions, and the tickets it claims."""
	name = _require(SETTLEMENT_STATEMENT, args, "settlement_statement", "settlement", "statement")
	doc = frappe.get_doc(SETTLEMENT_STATEMENT, name)

	data = _row_out({field: doc.get(field) for field in _SETTLEMENT_FIELDS})
	data["line_items"] = _lines_out(doc)
	data["deductions"] = _deductions_out(doc)
	data["matched_scale_tickets"] = _matched_tickets(name)
	data["delivery_reconciliation"] = _reconciliation(doc, data["matched_scale_tickets"])

	return ToolResult(
		data=data,
		summary=f"Settlement {name} ({doc.get('statement_number')}) from {doc.get('customer')}: "
		f"{doc.get('packout_pct')}% packout, {doc.get('net_proceeds')} net — {doc.get('status')}",
	)


def _lines_out(doc) -> list[dict]:
	lines = []
	for row in doc.get("line_items") or []:
		get = row.get if isinstance(row, dict) else (lambda k, d=None, r=row: getattr(r, k, d))
		lines.append(
			{
				"variety": get("variety"),
				"grade": get("grade"),
				"packed_weight": float(get("packed_weight") or 0),
				"price_per_unit": float(get("price_per_unit") or 0),
				"price_uom": get("price_uom"),
				"gross_amount": float(get("gross_amount") or 0),
			}
		)
	return lines


def _deductions_out(doc) -> list[dict]:
	rows = []
	for row in doc.get("deductions") or []:
		get = row.get if isinstance(row, dict) else (lambda k, d=None, r=row: getattr(r, k, d))
		rows.append(
			{
				"deduction_type": get("deduction_type"),
				"description": get("description"),
				"amount": float(get("amount") or 0),
			}
		)
	return rows


def _matched_tickets(settlement: str) -> list[dict]:
	"""The Scale Tickets pointing at this settlement, newest first."""
	rows = frappe.db.get_all(
		SCALE_TICKET,
		filters={"settlement": settlement},
		fields=[
			"name",
			"ticket_number",
			"date",
			"variety",
			"grade",
			"net_weight",
			"weight_uom",
			"status",
		],
		order_by="date desc",
		limit_page_length=500,
	)
	return [_row_out(row) for row in rows]


def _reconciliation(doc, tickets: list) -> dict:
	"""The packer's delivered weight against the grower's own tickets.

	REPORTED, NEVER ENFORCED. The two figures come from two parties and the
	difference between them is the finding — a settlement that pays for less
	fruit than the tickets say was delivered is the single thing this pair of
	registers exists to make visible. A tool that refused the settlement, or
	overwrote one figure with the other, would delete it.

	Tickets in a unit other than the statement's are counted and excluded rather
	than converted: there is no conversion from bins to kilos that this app
	knows, and a fabricated one would put a fabricated variance on the answer.
	"""
	statement_uom = doc.get("weight_uom")
	comparable = [t for t in tickets if t.get("weight_uom") == statement_uom]
	mismatched = len(tickets) - len(comparable)

	ticket_total = round(sum(float(t.get("net_weight") or 0) for t in comparable), 3)
	packer_total = float(doc.get("gross_delivered_weight") or 0)

	return {
		"packer_gross_delivered_weight": packer_total,
		"matched_ticket_net_weight": ticket_total,
		"weight_uom": statement_uom,
		"variance": round(packer_total - ticket_total, 3),
		"matched_ticket_count": len(tickets),
		"tickets_in_other_units_excluded": mismatched,
		"note": (
			"variance is the PACKER's delivered weight less the sum of the grower's own "
			"matched Scale Tickets. Neither figure is derived from the other and neither "
			"is corrected — a non-zero variance is a finding to take up with the packer, "
			"not an error in this record. Zero matched tickets makes the variance equal "
			"to the packer's figure and means nothing at all."
		),
	}


# ── Settlement Statement: writes ──────────────────────────────────────────


def create_settlement_statement(args: dict) -> ToolResult:
	"""Capture one settlement statement as a draft, with its lines and deductions."""
	statement_number = as_str(args, "statement_number", required=True)
	date = as_date(args, "date", required=True)
	company = resolve_company(as_str(args, "company"), required=True)
	customer = _customer(as_str(args, "customer"), required=True)
	weight_uom = _weight_uom(args)

	period_start = as_date(args, "period_start")
	period_end = as_date(args, "period_end")
	if period_start and period_end and period_start > period_end:
		raise ToolError(f"period_start {period_start} is after period_end {period_end}. Nothing was created.")

	doc = frappe.get_doc(
		{
			"doctype": SETTLEMENT_STATEMENT,
			"statement_number": statement_number,
			"date": date,
			"customer": customer,
			"company": company,
			"period_start": period_start,
			"period_end": period_end,
			"gross_delivered_weight": _weight(args, "gross_delivered_weight"),
			"packed_weight": _weight(args, "packed_weight"),
			"cull_weight": _weight(args, "cull_weight"),
			"weight_uom": weight_uom,
			"statement_image": as_str(args, "statement_image") or None,
			"notes": as_str(args, "notes") or None,
		}
	)
	tickets = _tickets_to_match(args, company=company, customer=customer)

	for row in _read_lines(args):
		doc.append("line_items", row)
	for row in _read_deductions(args):
		doc.append("deductions", row)

	doc.flags.ignore_permissions = True
	doc.insert()

	matched = _match_tickets(doc.name, tickets)

	data = _row_out({field: doc.get(field) for field in _SETTLEMENT_FIELDS})
	data["line_items"] = _lines_out(doc)
	data["deductions"] = _deductions_out(doc)
	data["matched_scale_tickets"] = _matched_tickets(doc.name)
	data["delivery_reconciliation"] = _reconciliation(doc, data["matched_scale_tickets"])

	return ToolResult(
		data=data,
		summary=f"Settlement {doc.name} captured as a draft: {statement_number} from "
		f"{customer}, {doc.get('packout_pct')}% packout, {doc.get('net_proceeds')} net proceeds"
		+ (f", {matched} scale ticket(s) matched" if matched else ""),
		docstatus_delta="none → draft",
	)


def _tickets_to_match(args: dict, *, company: str, customer: str) -> list[str]:
	"""The `scale_tickets` argument, checked before anything is written.

	CHECKED BEFORE THE INSERT, all of it, so a settlement is never created with
	half its tickets claimed. Every refusal here says "nothing was created",
	which is only true because none of it runs after `doc.insert()`.

	Four things are refused, and each is a real way of losing a load:

	  * A ticket that is still a DRAFT. A draft is a record somebody is still
	    editing — its weights can change after the settlement is checked against
	    them, which makes the check meaningless.
	  * A ticket already claimed by a DIFFERENT settlement. Two statements
	    paying for one load is the exact overpayment this register exists to
	    surface, and silently re-pointing the ticket would hide it.
	  * A ticket belonging to another COMPANY. Two entities delivering to one
	    packer under one bench is the ordinary case here.
	  * A ticket from a different CUSTOMER. A packer does not settle another
	    packer's deliveries.
	"""
	raw = args.get("scale_tickets") or args.get("tickets")
	if raw in (None, "", []):
		return []
	if not isinstance(raw, list):
		raise ToolError("scale_tickets must be a list of Scale Ticket docnames.")

	names = []
	for index, value in enumerate(raw, start=1):
		name = str(value or "").strip()
		if not name:
			raise ToolError(f"scale_tickets[{index}] is empty. Nothing was created.")
		row = frappe.db.get_value(
			SCALE_TICKET, name, ["name", "docstatus", "company", "customer", "settlement"], as_dict=True
		)
		if not row:
			raise ToolError(f"no Scale Ticket called {name!r} on this site. Nothing was created.")
		if int(row.get("docstatus") or 0) != 1:
			raise ToolError(
				f"scale ticket {name} is not submitted, so it cannot be matched to a settlement. "
				f"A draft ticket's weights can still change after the settlement is checked "
				f"against them, which makes the check meaningless. Nothing was created."
			)
		if row.get("settlement"):
			raise ToolError(
				f"scale ticket {name} is already matched to settlement {row['settlement']}. "
				f"Two statements paying for one load is the overpayment this register exists to "
				f"surface — it is not resolved by re-pointing the ticket. Nothing was created."
			)
		if str(row.get("company") or "") != company:
			raise ToolError(
				f"scale ticket {name} belongs to {row.get('company')!r}, not to {company!r}. "
				f"Nothing was created."
			)
		if str(row.get("customer") or "") != customer:
			raise ToolError(
				f"scale ticket {name} was delivered to {row.get('customer')!r}, not to "
				f"{customer!r}. A packer does not settle another packer's deliveries. "
				f"Nothing was created."
			)
		names.append(name)
	return names


def _match_tickets(settlement: str, names: list) -> int:
	"""Point each checked ticket at the settlement. Status follows from the link."""
	for name in names:
		frappe.db.set_value(
			SCALE_TICKET, name, {"settlement": settlement, "status": "Matched"}, update_modified=False
		)
	return len(names)


def _read_lines(args: dict) -> list[dict]:
	"""The `line_items` argument, validated into rows the child table will accept.

	A missing `gross_amount` is filled from packed weight times price; one that
	was given is kept. Same rule as an Expense Receipt's line totals and the same
	reason — a packer who applied a promotion to a line is telling the truth and
	the multiplication is not.
	"""
	raw = args.get("line_items") or args.get("lines")
	if raw in (None, "", []):
		return []
	if not isinstance(raw, list):
		raise ToolError(
			"line_items must be a list of objects with variety, grade, packed_weight, "
			"price_per_unit, price_uom and gross_amount."
		)

	rows = []
	for index, item in enumerate(raw, start=1):
		if not isinstance(item, dict):
			raise ToolError(f"line_items[{index}] must be an object, got {type(item).__name__}.")
		packed_weight = as_float(item.get("packed_weight"), f"line_items[{index}].packed_weight")
		price = as_float(item.get("price_per_unit"), f"line_items[{index}].price_per_unit")
		gross_amount = as_float(item.get("gross_amount"), f"line_items[{index}].gross_amount")
		if not gross_amount and packed_weight and price:
			gross_amount = round(packed_weight * price, 2)
		rows.append(
			{
				"variety": as_str(item, "variety") or None,
				"grade": as_str(item, "grade") or None,
				"packed_weight": packed_weight,
				"price_per_unit": price,
				"price_uom": as_str(item, "price_uom") or None,
				"gross_amount": gross_amount,
			}
		)
	return rows


def _read_deductions(args: dict) -> list[dict]:
	"""The `deductions` argument, with every type checked against the Select."""
	raw = args.get("deductions")
	if raw in (None, "", []):
		return []
	if not isinstance(raw, list):
		raise ToolError("deductions must be a list of objects with deduction_type, description and amount.")

	rows = []
	for index, item in enumerate(raw, start=1):
		if not isinstance(item, dict):
			raise ToolError(f"deductions[{index}] must be an object, got {type(item).__name__}.")
		deduction_type = as_str(item, "deduction_type") or "Other"
		if deduction_type not in DEDUCTION_TYPES:
			raise ToolError(
				f"deductions[{index}].deduction_type must be one of: {', '.join(DEDUCTION_TYPES)}."
			)
		amount = as_float(item.get("amount"), f"deductions[{index}].amount")
		if amount < 0:
			raise ToolError(
				f"deductions[{index}].amount cannot be negative — got {amount}. A deduction is "
				f"already a subtraction, so a negative one would ADD to the proceeds. If the "
				f"packer credited something back, that is a line item, not a deduction."
			)
		rows.append(
			{
				"deduction_type": deduction_type,
				"description": as_str(item, "description") or None,
				"amount": amount,
			}
		)
	return rows


def submit_settlement_statement(args: dict) -> ToolResult:
	"""Submit a draft settlement statement, which makes it immutable."""
	name = _require(SETTLEMENT_STATEMENT, args, "settlement_statement", "settlement", "statement")
	doc = frappe.get_doc(SETTLEMENT_STATEMENT, name)

	if int(doc.get("docstatus") or 0) == 1:
		raise ToolError(f"settlement statement {name} is already submitted. Nothing was changed.")
	if int(doc.get("docstatus") or 0) == 2:
		raise ToolError(
			f"settlement statement {name} is cancelled. A cancelled statement is amended into "
			f"a new one rather than resubmitted. Nothing was changed."
		)

	doc.flags.ignore_permissions = True
	doc.submit()

	return ToolResult(
		data={
			"name": name,
			"statement_number": doc.get("statement_number"),
			"status": doc.get("status"),
			"docstatus": int(doc.get("docstatus") or 0),
			"customer": doc.get("customer"),
			"total_gross_revenue": float(doc.get("total_gross_revenue") or 0),
			"total_deductions": float(doc.get("total_deductions") or 0),
			"net_proceeds": float(doc.get("net_proceeds") or 0),
			"packout_pct": float(doc.get("packout_pct") or 0),
		},
		summary=f"Settlement {name} submitted: {doc.get('net_proceeds')} net proceeds from "
		f"{doc.get('customer')}",
		docstatus_delta="draft → submitted",
	)


# ── classify_receipt ──────────────────────────────────────────────────────
#
# WHY THIS IS RULES AND NOT A MODEL. The classifier runs at the moment a foreman
# is standing in front of the thing they photographed, and it is allowed to be
# wrong — the app shows its answer as a pre-selected tab the person can change.
# What it is NOT allowed to be is unaccountable: "why did it file my ticket as an
# expense" has to have an answer, and `matched_signals` is that answer. A
# keyword table is auditable by reading it, ships without weights to host, and
# runs on a bench with no Python beyond what Frappe already has.
#
# IT NEVER RETURNS 1.0. A keyword rule is never certain, and a confidence of 1.0
# is an instruction to the client to stop asking. The ceiling is 0.95.

#: `(keyword, weight)` per receipt type. Weight 2 is a phrase that appears on
#: essentially nothing else; weight 1 is a word that leans. Everything is matched
#: case-insensitively against merchant, description and raw text together.
CLASSIFIER_SIGNALS = {
	"scale_ticket": (
		("scale ticket", 2),
		("weight ticket", 2),
		("weighmaster", 2),
		("gross wt", 2),
		("tare wt", 2),
		("net wt", 2),
		("tare weight", 2),
		("gross weight", 2),
		("net weight", 2),
		("bin count", 2),
		("load ticket", 2),
		("delivery ticket", 2),
		("certified scale", 2),
		("tare", 1),
		("gross", 1),
		("bins", 1),
		("truck", 1),
		("orchard", 1),
		("block", 1),
		("lot", 1),
	),
	"settlement": (
		("settlement", 2),
		("packout", 2),
		("pack out", 2),
		("grower statement", 2),
		("grower return", 2),
		("pool return", 2),
		("packing charge", 2),
		("cold storage", 2),
		("marketing fee", 2),
		("commission", 2),
		("cull", 2),
		("net proceeds", 2),
		("deduction", 1),
		("pool", 1),
		("fob", 1),
		("packed", 1),
		("season", 1),
	),
	"bill": (
		("invoice", 2),
		("invoice no", 2),
		("bill to", 2),
		("remit to", 2),
		("amount due", 2),
		("net 30", 2),
		("purchase order", 2),
		("statement of account", 2),
		("terms", 1),
		("due date", 1),
		("account number", 1),
		("balance forward", 1),
	),
	"expense": (
		("thank you for shopping", 2),
		("change due", 2),
		("cash tendered", 2),
		("card ending", 2),
		("pump", 2),
		("gallons", 2),
		("unleaded", 2),
		("diesel", 2),
		("subtotal", 1),
		("sales tax", 1),
		("cashier", 1),
		("visa", 1),
		("mastercard", 1),
		("debit", 1),
		("receipt", 1),
		("hardware", 1),
		("auto parts", 1),
		("co-op", 1),
	),
}

#: Ties are broken in this order, and the order is an argument. A settlement
#: statement quotes weights, so it will always pick up scale-ticket words; a
#: scale ticket almost never uses the word "packout". The more specific document
#: therefore wins a tie. `expense` is last because it is also the fallback, and a
#: fallback that could win a tie would swallow the two registers this release
#: exists to fill.
CLASSIFIER_PRECEDENCE = ("settlement", "scale_ticket", "bill", "expense")

#: What a type maps to on the other side of the branch, so a client does not have
#: to keep its own table.
CLASSIFIER_TARGETS = {
	"scale_ticket": "Scale Ticket (create_scale_ticket)",
	"settlement": "Settlement Statement (create_settlement_statement)",
	"bill": "Purchase Invoice — NOT YET IMPLEMENTED, capture as an expense for now",
	"expense": "Expense Receipt (submit_expense_receipt)",
}

#: How much evidence counts as enough. Below this, confidence is scaled down in
#: proportion — one weak keyword out of one is not 100% certainty, it is one
#: weak keyword.
CLASSIFIER_SATURATION = 4.0

#: A keyword rule is never certain.
CLASSIFIER_CEILING = 0.95


def classify_receipt(args: dict) -> ToolResult:
	"""Which register a photographed document belongs in, and how sure that is."""
	merchant = as_str(args, "merchant")
	description = as_str(args, "description")
	text = as_str(args, "text") or as_str(args, "ocr_raw_text")
	amount = args.get("amount")

	haystack = " ".join(part for part in (merchant, description, text) if part).lower()
	if not haystack.strip():
		raise ToolError(
			"classify_receipt needs something to read: pass merchant, description or text "
			"(the raw OCR output). Classifying an empty string would return a guess wearing "
			"a confidence."
		)

	scores = {}
	matched = {}
	for receipt_type, signals in CLASSIFIER_SIGNALS.items():
		hits = [(keyword, weight) for keyword, weight in signals if keyword in haystack]
		scores[receipt_type] = sum(weight for _, weight in hits)
		matched[receipt_type] = [keyword for keyword, _ in hits]

	total = sum(scores.values())
	if not total:
		return ToolResult(
			data={
				"receipt_type": "expense",
				"confidence": 0.0,
				"default_applied": True,
				"matched_signals": [],
				"scores": scores,
				"alternatives": [],
				"suggested_tool": CLASSIFIER_TARGETS["expense"],
				"amount": float(amount) if amount not in (None, "") else None,
				"note": (
					"nothing in the text matched any rule, so this is the FALLBACK rather than "
					"a classification — expense is where an unrecognised slip does least harm, "
					"because it is the register a person reviews anyway. Confidence is 0."
				),
			},
			summary="no signal matched — falling back to expense with confidence 0",
		)

	winner = min(
		CLASSIFIER_PRECEDENCE,
		key=lambda kind: (-scores[kind], CLASSIFIER_PRECEDENCE.index(kind)),
	)
	share = scores[winner] / total
	saturation = min(1.0, scores[winner] / CLASSIFIER_SATURATION)
	confidence = round(min(CLASSIFIER_CEILING, share * saturation), 2)

	alternatives = [
		{"receipt_type": kind, "score": scores[kind], "matched_signals": matched[kind]}
		for kind in CLASSIFIER_PRECEDENCE
		if kind != winner and scores[kind]
	]

	data = {
		"receipt_type": winner,
		"confidence": confidence,
		"default_applied": False,
		"matched_signals": matched[winner],
		"scores": scores,
		"alternatives": alternatives,
		"suggested_tool": CLASSIFIER_TARGETS[winner],
		"amount": float(amount) if amount not in (None, "") else None,
		"note": (
			"confidence is the winner's share of all matched evidence, scaled down when there "
			f"was little evidence to share (full weight at a score of {int(CLASSIFIER_SATURATION)}), "
			f"and capped at {CLASSIFIER_CEILING} because a keyword rule is never certain. "
			"matched_signals is the whole reason for the answer — show it rather than the number "
			"when somebody asks why."
		),
	}
	return ToolResult(
		data=data,
		summary=f"{winner} (confidence {confidence}) on "
		f"{len(matched[winner])} signal(s): {', '.join(matched[winner][:5]) or '<none>'}",
	)
