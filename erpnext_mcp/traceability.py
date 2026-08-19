# SPDX-License-Identifier: MIT
"""The mock recall, answered in one call instead of an afternoon.

WHAT WAS ALREADY HERE AND WHY IT WAS NOT AN ANSWER. Every critical tracking event
the FSMA Food Traceability Rule asks for has been recorded for some time.
`Bucket Log Entry` carries `crew_id`, `block_id`, `bin_id` and `shipment_id`, and
`compliance_fields.py` states the intent of each in the field definition itself —
"the block is where the lot came from, and it is the join to the spray record,
which is how a residue question becomes an answerable question", and "a buyer's
mock recall is timed, and an operation that cannot answer in four hours fails the
audit".

The data threaded. Nothing walked it. Answering "which blocks are in this lot"
meant filtering bucket entries by hand, collecting the block ids, opening the
spray register, filtering that by block and by date, and writing the result on
paper — which is a four-hour answer to a four-hour question, done by the one
person who knows where everything is, on the day a buyer calls.

────────────────────────────────────────────────────────────────────────────
THE TWO QUESTIONS, AND WHY THEY ARE NOT THE SAME QUESTION BACKWARDS
────────────────────────────────────────────────────────────────────────────

    BACKWARD — "show me everything that happened to lot X".
    Asked when the product is already suspect: a customer complaint, a residue
    detection, a positive test at the packing house. It starts at a SHIPMENT, a
    BIN or a SETTLEMENT and ends at the blocks, the pickers, the crews, the days
    — and then at what those blocks had been given, because a residue question
    is a question about the spray register.

    FORWARD — "which lots contain product from block Y".
    Asked when the SOURCE is suspect: a spray applied at the wrong rate, a water
    test that came back positive, a flooded block. It starts at a block, a spray
    application or a water test and ends at the bins, the shipments, the
    settlements and the CUSTOMERS — the people who have to be telephoned.

They share a middle (blocks ↔ harvest events) and differ at both ends, which is
why they are two functions over one graph rather than one function with a flag.
The forward trace also carries a bound the backward trace has no use for: see
THE DATE BOUND below.

────────────────────────────────────────────────────────────────────────────
THE DATE BOUND, WHICH IS THE WHOLE VALUE OF A FORWARD TRACE
────────────────────────────────────────────────────────────────────────────

A forward trace from a spray application must name the lots harvested AFTER that
application, not every lot that block ever produced. A recall that names three
seasons of fruit because one tank went out in April is a recall nobody can act
on, and an operation that issues it once is an operation whose next recall is not
believed.

So `trace_forward` from a spray or a water test anchors on that record's own date
and takes what came after it. From a bare block it takes everything, and SAYS SO
— an unbounded trace is a legitimate question ("everything this block ever
produced") and must not be silently confused with a bounded one.

────────────────────────────────────────────────────────────────────────────
EVERY BREAK IS NAMED. THIS IS THE POINT OF THE READ, NOT A FAILURE OF IT
────────────────────────────────────────────────────────────────────────────

`trace_contract_to_cash` established the idiom and the argument holds harder
here: a chain that quietly returned the hops it found would bury the single most
useful sentence a traceability read can produce, which is "the bins are recorded
and no shipment id was ever written on them, so this lot cannot be traced to a
customer."

Three kinds of break are distinguished, because they need three different
answers:

  * A HOP WITH NO ROWS — the register is there and the period is empty.
  * A HOP THAT IS NOT LINKED — the rows exist and the joining column is blank.
    `unlinked_counts` says how many, per column, which is what turns "our
    traceability is fine" into a number.
  * A REFERENCE THAT RESOLVES TO NOTHING — `shipment_id` is free text on the
    bucket entry and `Trade Shipment` is a register with its own names. A
    shipment id matching no Trade Shipment is reported as an unresolved
    reference rather than dropped, because that is a real data fault and the
    silent version of it is how a chain looks complete and is not.

────────────────────────────────────────────────────────────────────────────
WHAT THIS MODULE DOES NOT DO
────────────────────────────────────────────────────────────────────────────

It writes nothing. It computes no verdicts about whether an operation is
compliant. And it does not invent a link where the site did not record one:
`bin_id` is free text, two bins called "17" in two seasons are two different
bins, and this walks the ids the site actually stored, within the window it was
asked about, rather than guessing which "17" was meant.
"""

from __future__ import annotations

import frappe

from . import compat

BUCKET = "Bucket Log Entry"
SCALE_TICKET = "Scale Ticket"
SETTLEMENT = "Settlement Statement"
SHIPMENT = "Trade Shipment"
SPRAY = "Spray Application"
SPRAY_BLOCK = "Spray Application Block"
WATER_TEST = "Water Test"
IRRIGATION_ZONE = "Irrigation Zone"
FIELD = "Field"
INVOICE = "Sales Invoice"

#: Hard cap on any one hop. A recall answer is meant to be READ, and a bin id
#: reused across three seasons can pull eleven thousand bucket entries. A cap
#: that bites is stated in the answer, never silent — the same rule the audit
#: packet applies for the same reason.
HOP_CAP = 2000

#: What the four traceability columns are called, in the order a lot is built.
#: Used to report which link is missing rather than that "the chain is broken".
CTE_COLUMNS = ("block_id", "crew_id", "bin_id", "shipment_id")


def _rows(doctype: str, filters, fields, order_by: str = "", limit: int = HOP_CAP) -> list:
	"""One hop's rows, or nothing at all when the doctype is not on this site."""
	if not compat.doctype_exists(doctype):
		return []
	return [
		dict(row)
		for row in frappe.db.get_all(
			doctype,
			filters=filters,
			fields=compat.existing_fields(doctype, fields),
			order_by=order_by or "creation asc",
			limit=limit,
		)
		or []
	]


def distinct(values) -> list:
	"""Distinct, order-preserving, blanks dropped."""
	out: list = []
	for value in values:
		text = str(value).strip() if value not in (None, "") else ""
		if text and text not in out:
			out.append(text)
	return out


def _day(value) -> str | None:
	return str(value or "")[:10] or None


def _window(start: str = "", end: str = "") -> dict:
	"""A `timestamp` filter fragment, with the end bound carrying a time.

	`Bucket Log Entry.timestamp` is a Datetime and a period bound is a Date, so a
	bucket captured at 11:30 on the last day sorts after a bare date bound and
	falls out of the answer. On a recall that is a lot nobody was told about.
	"""
	if start and end:
		return {"timestamp": ("between", [f"{start} 00:00:00", f"{end} 23:59:59"])}
	if start:
		return {"timestamp": (">=", f"{start} 00:00:00")}
	if end:
		return {"timestamp": ("<=", f"{end} 23:59:59")}
	return {}


# ── the middle of the graph, shared by both directions ──────────────────────
def buckets_for(
	company: str = "",
	blocks: list | None = None,
	bins: list | None = None,
	shipments: list | None = None,
	start: str = "",
	end: str = "",
) -> list:
	"""Bucket Log Entries matching any ONE of the given anchors.

	ANY, not all. A backward trace from a shipment knows the shipment id and
	nothing else; a forward trace from a block knows the block. Requiring every
	anchor would answer nothing, and combining them with OR in one query would
	need raw SQL this app does not write — so each anchor is its own query and
	the results are merged on docname.
	"""
	base = {}
	if company:
		base["company"] = company
	base.update(_window(start, end))

	found: dict = {}
	for column, values in (
		("block_id", blocks or []),
		("bin_id", bins or []),
		("shipment_id", shipments or []),
	):
		wanted = distinct(values)
		if not wanted:
			continue
		for row in _rows(
			BUCKET,
			{**base, column: ("in", wanted)},
			(
				"name",
				"timestamp",
				"company",
				"employee",
				"worker_badge",
				"crew_id",
				"block_id",
				"bin_id",
				"shipment_id",
				"verdict",
				"container_type",
				"shift",
				"session_uuid",
			),
			order_by="timestamp asc",
		):
			found[str(row["name"])] = row
	return list(found.values())


def summarise_buckets(entries: list) -> dict:
	"""What a set of bucket captures says about blocks, people, days and links."""
	unlinked = {column: 0 for column in CTE_COLUMNS}
	for row in entries:
		for column in CTE_COLUMNS:
			if not str(row.get(column) or "").strip():
				unlinked[column] += 1
	days = sorted({day for day in (_day(row.get("timestamp")) for row in entries) if day})
	return {
		"bucket_count": len(entries),
		"blocks": distinct(row.get("block_id") for row in entries),
		"bins": distinct(row.get("bin_id") for row in entries),
		"shipment_ids": distinct(row.get("shipment_id") for row in entries),
		"crews": distinct(row.get("crew_id") for row in entries),
		"pickers": distinct(row.get("employee") or row.get("worker_badge") for row in entries),
		"shifts": distinct(row.get("shift") for row in entries),
		"first_captured": days[0] if days else None,
		"last_captured": days[-1] if days else None,
		"harvest_days": days,
		"rejected": len([row for row in entries if str(row.get("verdict") or "") == "Rejected"]),
		# THE NUMBER THAT TURNS "our traceability is fine" INTO A FACT. Per
		# column, because the four are written by different acts and a farm that
		# is perfect on blocks and blank on shipments has one problem, not four.
		"unlinked_counts": unlinked,
	}


# ── the input side: what a block was given ──────────────────────────────────
def sprays_on(blocks: list, before: str = "", after: str = "") -> list:
	"""Spray Applications that reached any of these blocks.

	Read through `Spray Application Block`, the child table that records which
	blocks one pass actually covered — `parent` asked for BY NAME, since
	`compat.existing_fields` drops framework columns and a batched child read
	that loses it files every row under one empty key.

	`before` is the harvest date on a BACKWARD trace: an application made after
	the fruit was picked did not reach it, and naming it in a residue answer
	sends somebody to investigate a tank that was never on that crop.
	"""
	wanted = distinct(blocks)
	if not wanted or not compat.doctype_exists(SPRAY_BLOCK):
		return []
	parents: dict = {}
	for row in (
		frappe.db.get_all(
			SPRAY_BLOCK,
			filters={"block": ("in", wanted)},
			fields=["parent", "block", "acres"],
			limit=HOP_CAP,
		)
		or []
	):
		entry = dict(row)
		parents.setdefault(str(entry.get("parent") or ""), []).append(entry.get("block"))
	if not parents:
		return []

	filters = {"name": ("in", sorted(name for name in parents if name))}
	if before and after:
		filters["completed_at"] = ("between", [f"{after} 00:00:00", f"{before} 23:59:59"])
	elif before:
		filters["completed_at"] = ("<=", f"{before} 23:59:59")
	elif after:
		filters["completed_at"] = (">=", f"{after} 00:00:00")

	out = []
	for row in _rows(
		SPRAY,
		filters,
		(
			"name",
			"status",
			"completed_at",
			"applicator",
			"applicator_license",
			"rei_hours",
			"phi_days",
			"phi_clears_on",
			"products_applied",
			"tank_mix",
		),
		order_by="completed_at asc",
	):
		if str(row.get("status") or "Applied") != "Applied":
			# Planned and Cancelled passes put nothing on the ground. Naming one
			# in a recall answer would report fruit as treated with a tank that
			# never left the shed.
			continue
		row["blocks_reached"] = distinct(parents.get(str(row["name"]), []))
		out.append(row)
	return out


def water_tests_for(blocks: list, before: str = "", after: str = "") -> list:
	"""Water tests on the irrigation zones that serve these blocks.

	The join is Zone → Field, so a block with no zone on file produces no water
	tests and that is reported as an unlinked hop rather than as a clean result.
	"""
	wanted = distinct(blocks)
	if not wanted or not compat.doctype_exists(IRRIGATION_ZONE):
		return []
	zones = distinct(
		row.get("name")
		for row in _rows(IRRIGATION_ZONE, {"field": ("in", wanted)}, ("name", "zone_name", "field"))
	)
	if not zones:
		return []
	filters = {"source": ("in", zones)}
	if before and after:
		filters["test_date"] = ("between", [after, before])
	elif before:
		filters["test_date"] = ("<=", before)
	elif after:
		filters["test_date"] = (">=", after)
	return _rows(
		WATER_TEST,
		filters,
		(
			"name",
			"source",
			"test_date",
			"coliform_result",
			"ecoli_result",
			"laboratory",
			"workflow_state",
			"findings",
		),
		order_by="test_date asc",
	)


# ── the output side: where the fruit went ───────────────────────────────────
def scale_tickets_for(blocks: list, company: str = "", start: str = "", end: str = "") -> list:
	"""Scale Tickets whose origin is one of these blocks.

	TWO COLUMNS, BOTH READ. `Scale Ticket.field` is a Link to Field and `block`
	is free text somebody wrote on the ticket. A site that fills one and not the
	other is normal, and reading only the Link would lose every hand-entered
	ticket — which on a recall is a truck nobody accounted for.
	"""
	wanted = distinct(blocks)
	if not wanted:
		return []
	base = {}
	if company:
		base["company"] = company
	if start and end:
		base["date"] = ("between", [start, end])
	elif start:
		base["date"] = (">=", start)
	elif end:
		base["date"] = ("<=", end)

	found: dict = {}
	for column in ("field", "block"):
		if not compat.has_field(SCALE_TICKET, column):
			continue
		for row in _rows(
			SCALE_TICKET,
			{**base, column: ("in", wanted)},
			(
				"name",
				"ticket_number",
				"date",
				"customer",
				"company",
				"field",
				"block",
				"variety",
				"grade",
				"net_weight",
				"weight_uom",
				"status",
				"settlement",
				"truck_id",
				"destination",
			),
			order_by="date asc",
		):
			found[str(row["name"])] = row
	return list(found.values())


def settlements_for(tickets: list) -> list:
	"""The settlement statements those tickets were paid on."""
	wanted = distinct(row.get("settlement") for row in tickets)
	if not wanted:
		return []
	return _rows(
		SETTLEMENT,
		{"name": ("in", wanted)},
		(
			"name",
			"statement_number",
			"date",
			"customer",
			"company",
			"status",
			"sales_invoice",
			"revenue_contract",
			"net_proceeds",
			"packed_weight",
		),
		order_by="date asc",
	)


def shipments_for(shipment_ids: list, invoices: list | None = None) -> tuple:
	"""`(rows, unresolved)` — Trade Shipments, and the ids that matched nothing.

	`Bucket Log Entry.shipment_id` is free text and `Trade Shipment` is a
	register with its own naming series, so the two can disagree. An id that
	resolves to no shipment is RETURNED as unresolved rather than dropped: a lot
	whose shipment reference points at nothing is a real data fault, and the
	silent version of it is how a chain looks complete and is not.
	"""
	wanted = distinct(shipment_ids)
	rows: dict = {}
	if wanted:
		for row in _rows(
			SHIPMENT,
			{"name": ("in", wanted)},
			_SHIPMENT_FIELDS,
			order_by="ship_date asc",
		):
			rows[str(row["name"])] = row
	for invoice in distinct(invoices or []):
		for row in _rows(SHIPMENT, {"sales_invoice": invoice}, _SHIPMENT_FIELDS):
			rows[str(row["name"])] = row
	unresolved = [value for value in wanted if value not in rows]
	return list(rows.values()), unresolved


_SHIPMENT_FIELDS = (
	"name",
	"status",
	"ship_date",
	"customer",
	"customer_name",
	"company",
	"commodity",
	"destination_tier",
	"destination_name",
	"destination_country",
	"destination_state",
	"carrier",
	"tracking_number",
	"sales_invoice",
	"sales_order",
	"departed_on",
	"delivered_on",
)


def invoices_for(settlements: list, shipments: list) -> list:
	"""Sales Invoices reachable from either downstream register."""
	wanted = distinct(
		[row.get("sales_invoice") for row in settlements] + [row.get("sales_invoice") for row in shipments]
	)
	if not wanted:
		return []
	return _rows(
		INVOICE,
		{"name": ("in", wanted)},
		("name", "posting_date", "customer", "company", "grand_total", "status", "docstatus"),
		order_by="posting_date asc",
	)


def fields_named(blocks: list) -> dict:
	"""`{docname: row}` for the block ids that are Field docnames.

	A `block_id` need not be one. It is free text on the bucket entry, a farm may
	write "YC3" where the register says "Yellow Camp Block 3 - MC", and this
	resolves what it can rather than refusing the whole trace over a naming
	convention nobody agreed. What it cannot resolve is reported.
	"""
	wanted = distinct(blocks)
	if not wanted:
		return {}
	return {
		str(row["name"]): row
		for row in _rows(
			FIELD,
			{"name": ("in", wanted)},
			(
				"name",
				"field_name",
				"parcel",
				"owning_entity",
				"crop",
				"variety",
				"acreage",
				"last_spray_date",
				"water_test_last_date",
				"food_safety_zone",
				"wildlife_intrusion_last_report",
			),
		)
	}
