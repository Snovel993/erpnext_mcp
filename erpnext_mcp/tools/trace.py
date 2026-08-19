# SPDX-License-Identifier: MIT
"""The two recall questions, as two tools over one graph.

`erpnext_mcp/traceability.py` holds the walk and argues the design. This module
is the door: it resolves whatever the caller happened to have in their hand into
a starting point, walks, and reports the breaks.

WHY THE STARTING POINT IS FIVE ARGUMENTS RATHER THAN A DOCTYPE AND A NAME. The
person asking is holding one thing, and which thing depends entirely on who
telephoned them. A buyer's QA team quotes a SHIPMENT. A packing house quotes a
BIN or a scale ticket. An accountant chasing a deduction quotes a SETTLEMENT. An
inspector standing in the orchard points at a BLOCK. Making them all say
`from_doctype="Trade Shipment"` would be asking the caller to know this app's
register names during the one hour when nobody has time to look them up.
"""

from __future__ import annotations

import frappe

from .. import compat, traceability
from ..args import as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

BUCKET = traceability.BUCKET
SCALE_TICKET = traceability.SCALE_TICKET
SETTLEMENT = traceability.SETTLEMENT
SHIPMENT = traceability.SHIPMENT
SPRAY = traceability.SPRAY
WATER_TEST = traceability.WATER_TEST
FIELD = traceability.FIELD


def _require() -> None:
	compat.require_doctype(
		BUCKET,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app. "
		"Every critical tracking event a trace walks is recorded on it.",
	)


def _hop(kind: str, doctype: str, rows: list, describe) -> dict:
	"""One stage of the chain, with its own count and its own emptiness."""
	return {
		"hop": kind,
		"doctype": doctype,
		"count": len(rows),
		"rows": [describe(row) for row in rows],
		"capped": len(rows) >= traceability.HOP_CAP,
	}


def _break(after: str, missing: str, note: str) -> dict:
	return {"after": after, "missing": missing, "note": note}


# ── describers, one per register ────────────────────────────────────────────
def _bucket(row: dict) -> dict:
	return {
		"entry": row.get("name"),
		"captured": str(row.get("timestamp") or "")[:19] or None,
		"picker": row.get("employee") or row.get("worker_badge") or None,
		"crew": row.get("crew_id") or None,
		"block": row.get("block_id") or None,
		"bin": row.get("bin_id") or None,
		"shipment_id": row.get("shipment_id") or None,
		"verdict": row.get("verdict"),
		"container": row.get("container_type"),
		"shift": row.get("shift") or None,
	}


def _spray(row: dict) -> dict:
	return {
		"spray": row.get("name"),
		"completed": str(row.get("completed_at") or "")[:19] or None,
		"blocks_reached": row.get("blocks_reached") or [],
		"applicator": row.get("applicator") or None,
		"applicator_license": row.get("applicator_license") or None,
		"rei_hours": row.get("rei_hours"),
		"phi_days": row.get("phi_days"),
		"phi_clears_on": str(row.get("phi_clears_on") or "")[:10] or None,
		"tank_mix": row.get("tank_mix") or None,
		"products": traceability_products(row),
	}


def traceability_products(row: dict) -> list:
	"""The product names and EPA numbers off the application's own snapshot."""
	import json

	raw = row.get("products_applied")
	if not raw:
		return []
	try:
		parsed = json.loads(raw) if isinstance(raw, str) else raw
	except (json.JSONDecodeError, ValueError, TypeError):
		return []
	if not isinstance(parsed, list):
		return []
	return [
		{
			"product": line.get("item_name") or line.get("item"),
			"epa_reg_number": line.get("epa_reg_number") or None,
			"rate_per_acre": line.get("rate_per_acre"),
			"rate_uom": line.get("rate_uom"),
		}
		for line in parsed
		if isinstance(line, dict)
	]


def _water(row: dict) -> dict:
	return {
		"test": row.get("name"),
		"zone": row.get("source"),
		"date": str(row.get("test_date") or "")[:10] or None,
		"coliform": row.get("coliform_result") or None,
		"ecoli": row.get("ecoli_result") or None,
		"laboratory": row.get("laboratory") or None,
		"state": row.get("workflow_state") or None,
		"findings": row.get("findings") or None,
	}


def _ticket(row: dict) -> dict:
	return {
		"ticket": row.get("name"),
		"ticket_number": row.get("ticket_number"),
		"date": str(row.get("date") or "")[:10] or None,
		"customer": row.get("customer") or None,
		"origin_field": row.get("field") or None,
		"origin_block": row.get("block") or None,
		"variety": row.get("variety"),
		"grade": row.get("grade"),
		"net_weight": row.get("net_weight"),
		"weight_uom": row.get("weight_uom"),
		"truck": row.get("truck_id") or None,
		"destination": row.get("destination") or None,
		"settlement": row.get("settlement") or None,
		"status": row.get("status"),
	}


def _settlement(row: dict) -> dict:
	return {
		"settlement": row.get("name"),
		"statement_number": row.get("statement_number"),
		"date": str(row.get("date") or "")[:10] or None,
		"customer": row.get("customer") or None,
		"status": row.get("status"),
		"packed_weight": row.get("packed_weight"),
		"net_proceeds": row.get("net_proceeds"),
		"sales_invoice": row.get("sales_invoice") or None,
	}


def _shipment(row: dict) -> dict:
	return {
		"shipment": row.get("name"),
		"status": row.get("status"),
		"ship_date": str(row.get("ship_date") or "")[:10] or None,
		"customer": row.get("customer") or None,
		"customer_name": row.get("customer_name") or None,
		"commodity": row.get("commodity") or None,
		"destination": row.get("destination_name") or None,
		"destination_tier": row.get("destination_tier") or None,
		"destination_country": row.get("destination_country") or None,
		"destination_state": row.get("destination_state") or None,
		"carrier": row.get("carrier") or None,
		"tracking_number": row.get("tracking_number") or None,
		"sales_invoice": row.get("sales_invoice") or None,
		"departed_on": str(row.get("departed_on") or "")[:19] or None,
		"delivered_on": str(row.get("delivered_on") or "")[:19] or None,
	}


def _invoice(row: dict) -> dict:
	return {
		"invoice": row.get("name"),
		"posting_date": str(row.get("posting_date") or "")[:10] or None,
		"customer": row.get("customer") or None,
		"grand_total": row.get("grand_total"),
		"status": row.get("status"),
		"submitted": int(row.get("docstatus") or 0) == 1,
	}


def _field(row: dict) -> dict:
	return {
		"block": row.get("name"),
		"field_name": row.get("field_name"),
		"parcel": row.get("parcel") or None,
		"crop": row.get("crop") or None,
		"variety": row.get("variety") or None,
		"acreage": row.get("acreage"),
		"last_spray_date": str(row.get("last_spray_date") or "")[:10] or None,
		"water_test_last_date": str(row.get("water_test_last_date") or "")[:10] or None,
		"food_safety_zone": compat.checked(row.get("food_safety_zone")),
		"wildlife_intrusion_last_report": str(row.get("wildlife_intrusion_last_report") or "")[:10] or None,
	}


# ── trace_backward ──────────────────────────────────────────────────────────
def trace_backward(args: dict) -> ToolResult:
	"""Everything that happened to one lot, from the shipment back to the block."""
	_require()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	start = as_date(args, "date_from") or ""
	end = as_date(args, "date_to") or ""
	cap = min(as_limit(args), traceability.HOP_CAP)

	anchor, seeds, notes = _backward_seeds(args, company)

	entries = traceability.buckets_for(
		company=company,
		blocks=seeds.get("blocks"),
		bins=seeds.get("bins"),
		shipments=seeds.get("shipments"),
		start=start or seeds.get("start", ""),
		end=end or seeds.get("end", ""),
	)[:cap]
	summary = traceability.summarise_buckets(entries)

	breaks = []
	if not entries:
		breaks.append(
			_break(
				anchor["kind"],
				"bucket captures",
				"No Bucket Log Entry matches this lot. Either nothing was captured against it, "
				"or the id on the buckets is written differently from the id on the record you "
				"started from — list_bucket_entries shows what the site actually stored. "
				"WITHOUT THIS HOP THERE IS NO TRACE: the bucket is where the block, the crew "
				"and the picker are recorded, and nothing downstream carries them.",
			)
		)
	for column, count in summary["unlinked_counts"].items():
		if count:
			breaks.append(
				_break(
					"bucket captures",
					column,
					f"{count} of {summary['bucket_count']} captures have no {column}. Those "
					"buckets are in this lot and cannot be traced any further in that "
					"direction — the link was never written, so nothing here can recover it.",
				)
			)

	blocks = summary["blocks"]
	fields = traceability.fields_named(blocks)
	unresolved_blocks = [block for block in blocks if block not in fields]
	if unresolved_blocks:
		breaks.append(
			_break(
				"blocks",
				"Field records",
				f"{len(unresolved_blocks)} block id(s) on these buckets match no Field on this "
				f"site: {', '.join(unresolved_blocks[:10])}"
				+ (" …" if len(unresolved_blocks) > 10 else "")
				+ ". The spray and water history below covers only the blocks that resolved, "
				"so it is INCOMPLETE for this lot. `block_id` is free text on the capture.",
			)
		)

	# The harvest bound. An application made after the fruit came off did not
	# reach it, and naming it in a residue answer sends somebody to investigate a
	# tank that was never on that crop.
	picked_by = summary["last_captured"] or end or ""
	sprays = traceability.sprays_on(list(fields), before=picked_by)
	waters = traceability.water_tests_for(list(fields), before=picked_by)
	if fields and not sprays:
		breaks.append(
			_break(
				"blocks",
				"spray applications",
				"No Spray Application on record reached these blocks on or before the last "
				"capture. For an untreated block that is the right answer; for a treated one it "
				"means the pass was never filed, and a residue question about this lot has no "
				"answer in this system.",
			)
		)
	if fields and not waters:
		breaks.append(
			_break(
				"blocks",
				"water tests",
				"No Water Test reaches these blocks. The join runs Irrigation Zone → Field, so "
				"a block with no zone on file produces nothing here — which is a gap in the "
				"register rather than a clean water history.",
			)
		)

	tickets = traceability.scale_tickets_for(
		list(fields) or blocks, company, start or summary["first_captured"] or "", end or ""
	)
	settlements = traceability.settlements_for(tickets)
	shipments, unresolved_shipments = traceability.shipments_for(
		summary["shipment_ids"], [row.get("sales_invoice") for row in settlements]
	)
	invoices = traceability.invoices_for(settlements, shipments)
	if unresolved_shipments:
		breaks.append(
			_break(
				"shipment ids",
				"Trade Shipment records",
				f"{len(unresolved_shipments)} shipment id(s) on these captures match no Trade "
				f"Shipment: {', '.join(unresolved_shipments[:10])}. `shipment_id` is free text "
				"on the capture and the register has its own names — an id pointing at nothing "
				"is a data fault, and the chain looks complete without being so.",
			)
		)

	chain = [
		_hop("buckets", BUCKET, entries, _bucket),
		_hop("blocks", FIELD, list(fields.values()), _field),
		_hop("sprays", SPRAY, sprays, _spray),
		_hop("water_tests", WATER_TEST, waters, _water),
		_hop("scale_tickets", SCALE_TICKET, tickets, _ticket),
		_hop("settlements", SETTLEMENT, settlements, _settlement),
		_hop("shipments", SHIPMENT, shipments, _shipment),
		_hop("invoices", "Sales Invoice", invoices, _invoice),
	]
	present = [entry["hop"] for entry in chain if entry["count"]]

	return ToolResult(
		data={
			"direction": "backward",
			"question": "What is in this lot, and what happened to it?",
			"anchor": anchor,
			"company": company or None,
			"window": {"from": start or summary["first_captured"], "to": end or summary["last_captured"]},
			"chain": chain,
			"hops_with_rows": present,
			"hops_empty": [entry["hop"] for entry in chain if not entry["count"]],
			**{key: summary[key] for key in ("bucket_count", "blocks", "bins", "crews", "pickers", "shifts")},
			"harvest_days": summary["harvest_days"],
			"rejected_captures": summary["rejected"],
			"unlinked_counts": summary["unlinked_counts"],
			"unresolved_block_ids": unresolved_blocks,
			"unresolved_shipment_ids": unresolved_shipments,
			"customers": _clean_customers(tickets, settlements, shipments, invoices),
			"breaks": breaks,
			"notes": notes,
			"note": (
				"A BREAK IS THE POINT OF THIS READ, not a failure of it. `unlinked_counts` says "
				"how many captures in this lot carry no block, no crew, no bin or no shipment — "
				"which is the number that turns 'our traceability is fine' into a fact, and a "
				"trace that quietly returned the hops it did find would bury it."
			),
		},
		summary=(
			f"traced {anchor['kind']} {anchor['id']} back: {summary['bucket_count']} capture(s), "
			f"{len(summary['blocks'])} block(s), {len(sprays)} spray(s), {len(tickets)} scale "
			f"ticket(s), {len(shipments)} shipment(s)"
			+ (f"; {len(breaks)} break(s) named" if breaks else "; no breaks")
		),
	)


def _backward_seeds(args: dict, company: str) -> tuple:
	"""Whatever the caller had in their hand, turned into anchors to walk from."""
	notes: list = []

	shipment = as_str(args, "shipment")
	if shipment:
		return (
			{"kind": "shipment", "id": shipment},
			{"shipments": [shipment]},
			notes,
		)

	container = as_str(args, "bin")
	if container:
		return {"kind": "bin", "id": container}, {"bins": [container]}, notes

	entry = as_str(args, "bucket_entry")
	if entry:
		row = _one(BUCKET, entry, "bucket_entry", "list_bucket_entries")
		seeds = {}
		for key, column in (("bins", "bin_id"), ("shipments", "shipment_id"), ("blocks", "block_id")):
			if row.get(column):
				seeds[key] = [row[column]]
		if not seeds:
			raise ToolError(
				f"Bucket Log Entry {entry} carries no block, bin or shipment id, so there is "
				"nothing to trace from it. The capture exists and its traceability columns were "
				"never filled in — which is the finding, and list_bucket_entries shows the rest."
			)
		notes.append(
			f"Started from one capture and widened to everything sharing its {', '.join(sorted(seeds))}."
		)
		return {"kind": "bucket entry", "id": entry}, seeds, notes

	ticket = as_str(args, "scale_ticket")
	if ticket:
		row = _one(SCALE_TICKET, ticket, "scale_ticket", "list_scale_tickets")
		blocks = [value for value in (row.get("field"), row.get("block")) if value]
		if not blocks:
			raise ToolError(
				f"Scale Ticket {ticket} names no origin field or block, so the fruit on it "
				"cannot be traced back to ground. Nothing was read."
			)
		day = str(row.get("date") or "")[:10]
		notes.append(
			"A scale ticket names the ground and the day, not the buckets. This widened to "
			f"every capture on {', '.join(blocks)}"
			+ (f" on {day}" if day else "")
			+ " — which may include fruit that went out on another truck."
		)
		return (
			{"kind": "scale ticket", "id": ticket},
			{"blocks": blocks, "start": day, "end": day},
			notes,
		)

	statement = as_str(args, "settlement")
	if statement:
		tickets = [
			dict(row)
			for row in frappe.db.get_all(
				SCALE_TICKET,
				filters={"settlement": statement},
				fields=compat.existing_fields(SCALE_TICKET, ("name", "field", "block", "date")),
				limit=traceability.HOP_CAP,
			)
			or []
		]
		if not tickets:
			raise ToolError(
				f"no Scale Ticket is linked to Settlement Statement {statement!r}, so there is "
				"no route from it back to the ground. A settlement reaches the orchard through "
				"its tickets and through nothing else. Nothing was read."
			)
		blocks = traceability.distinct(
			[row.get("field") for row in tickets] + [row.get("block") for row in tickets]
		)
		days = sorted(str(row.get("date") or "")[:10] for row in tickets if row.get("date"))
		notes.append(f"Reached the ground through {len(tickets)} scale ticket(s) on this settlement.")
		return (
			{"kind": "settlement", "id": statement},
			{
				"blocks": blocks,
				"start": days[0] if days else "",
				"end": days[-1] if days else "",
			},
			notes,
		)

	raise ToolError(
		"name what you are holding: shipment, bin, scale_ticket, settlement or bucket_entry. "
		"Which one depends on who telephoned — a buyer quotes a shipment, a packing house a bin "
		"or a ticket, an accountant a settlement. To go the other way, from a block or a spray "
		"to the lots that carry it, use trace_forward. Nothing was read."
	)


# ── trace_forward ───────────────────────────────────────────────────────────
def trace_forward(args: dict) -> ToolResult:
	"""Which lots carry product from this block, spray or water source — and who has them."""
	_require()
	company = resolve_company(as_str(args, "company"), required=False) or ""
	end = as_date(args, "date_to") or ""
	cap = min(as_limit(args), traceability.HOP_CAP)

	anchor, blocks, after, notes = _forward_seeds(args)
	after = as_date(args, "date_from") or after

	if not after:
		notes.append(
			"UNBOUNDED. No date to start from, so this is everything these blocks have ever "
			"produced rather than everything produced since an event. That is a legitimate "
			"question and a different one — pass date_from, or start from the spray or the "
			"water test itself, to bound it."
		)

	entries = traceability.buckets_for(company=company, blocks=blocks, start=after, end=end)[:cap]
	summary = traceability.summarise_buckets(entries)

	breaks = []
	if not entries:
		breaks.append(
			_break(
				"blocks",
				"bucket captures",
				"No Bucket Log Entry names these blocks in this window. Either nothing was "
				"picked from them, or the block id on the captures is written differently from "
				"the Field docname — `block_id` is free text, and a farm that writes 'YC3' "
				"where the register says 'Yellow Camp Block 3 - MC' has a chain this cannot "
				"join. list_bucket_entries shows what was actually stored.",
			)
		)
	if summary["unlinked_counts"]["bin_id"]:
		breaks.append(
			_break(
				"bucket captures",
				"bin_id",
				f"{summary['unlinked_counts']['bin_id']} capture(s) carry no bin. Fruit from "
				"these blocks went somewhere and this system cannot say into which lot — on a "
				"recall those are the buckets that force the scope wider than it should be.",
			)
		)
	if summary["unlinked_counts"]["shipment_id"]:
		breaks.append(
			_break(
				"bucket captures",
				"shipment_id",
				f"{summary['unlinked_counts']['shipment_id']} capture(s) carry no shipment id. "
				"THIS IS THE HOP THAT REACHES A CUSTOMER — a lot with no shipment reference "
				"cannot be traced to anybody to telephone, and the scale tickets below are the "
				"only remaining route.",
			)
		)

	tickets = traceability.scale_tickets_for(blocks, company, after, end)
	settlements = traceability.settlements_for(tickets)
	shipments, unresolved = traceability.shipments_for(
		summary["shipment_ids"], [row.get("sales_invoice") for row in settlements]
	)
	invoices = traceability.invoices_for(settlements, shipments)
	if unresolved:
		breaks.append(
			_break(
				"shipment ids",
				"Trade Shipment records",
				f"{len(unresolved)} shipment id(s) match no Trade Shipment: "
				f"{', '.join(unresolved[:10])}. The fruit left; the register cannot say to whom.",
			)
		)

	customers = _clean_customers(tickets, settlements, shipments, invoices)
	if not customers:
		breaks.append(
			_break(
				"downstream",
				"customers",
				"NOBODY TO TELEPHONE. This trace reaches no customer through any route — no "
				"shipment, no settlement and no scale ticket names one. If this fruit was sold, "
				"the record of who bought it is not in this system, and a recall on these "
				"blocks cannot be executed from it.",
			)
		)

	chain = [
		_hop("buckets", BUCKET, entries, _bucket),
		_hop("scale_tickets", SCALE_TICKET, tickets, _ticket),
		_hop("settlements", SETTLEMENT, settlements, _settlement),
		_hop("shipments", SHIPMENT, shipments, _shipment),
		_hop("invoices", "Sales Invoice", invoices, _invoice),
	]

	return ToolResult(
		data={
			"direction": "forward",
			"question": "Which lots carry product from here, and who has them?",
			"anchor": anchor,
			"company": company or None,
			"blocks_traced": blocks,
			"bounded_from": after or None,
			"window": {"from": after or None, "to": end or None},
			"chain": chain,
			"hops_with_rows": [entry["hop"] for entry in chain if entry["count"]],
			"hops_empty": [entry["hop"] for entry in chain if not entry["count"]],
			"bucket_count": summary["bucket_count"],
			"bins": summary["bins"],
			"shipment_ids": summary["shipment_ids"],
			"harvest_days": summary["harvest_days"],
			"pickers": summary["pickers"],
			"crews": summary["crews"],
			"unlinked_counts": summary["unlinked_counts"],
			"unresolved_shipment_ids": unresolved,
			# THE FIELD A RECALL IS ACTUALLY RUN FROM. Everything above is
			# evidence; this is the list of people who have to be telephoned.
			"customers_to_notify": customers,
			"breaks": breaks,
			"notes": notes,
			"note": (
				"`customers_to_notify` is what a recall is executed from and `breaks` is what "
				"makes it trustworthy: a capture with no bin or no shipment id is fruit this "
				"system cannot place, and the honest scope of the recall is wider than the list."
			),
		},
		summary=(
			f"traced {anchor['kind']} {anchor['id']} forward"
			+ (f" from {after}" if after else " (unbounded)")
			+ f": {summary['bucket_count']} capture(s), {len(summary['bins'])} bin(s), "
			f"{len(shipments)} shipment(s), {len(customers)} customer(s) to notify"
			+ (f"; {len(breaks)} break(s) named" if breaks else "; no breaks")
		),
	)


def _forward_seeds(args: dict) -> tuple:
	"""The blocks to walk from, and the date after which harvest is in scope."""
	notes: list = []

	spray = as_str(args, "spray_application")
	if spray:
		row = _one(SPRAY, spray, "spray_application", "list_spray_applications")
		blocks = [
			str(dict(child).get("block"))
			for child in frappe.db.get_all(
				traceability.SPRAY_BLOCK,
				filters={"parent": spray},
				fields=["parent", "block"],
				limit=traceability.HOP_CAP,
			)
			or []
			if dict(child).get("block")
		]
		if not blocks:
			raise ToolError(
				f"Spray Application {spray} records no blocks, so nothing can be traced forward "
				"from it. Nothing was read."
			)
		day = str(row.get("completed_at") or row.get("started_at") or "")[:10]
		notes.append(
			"Bounded at the application. Fruit picked BEFORE this pass did not carry it, and a "
			"recall that named three seasons because one tank went out in April is a recall "
			"nobody can act on."
		)
		if row.get("phi_clears_on"):
			notes.append(
				f"The pre-harvest interval on this application cleared on {str(row['phi_clears_on'])[:10]}. "
				"Captures before that date are fruit picked inside the interval, which is a "
				"different and more serious finding than fruit merely treated."
			)
		return {"kind": "spray application", "id": spray}, blocks, day, notes

	test = as_str(args, "water_test")
	if test:
		row = _one(WATER_TEST, test, "water_test", "list_water_tests")
		zone = row.get("source")
		if not zone:
			raise ToolError(
				f"Water Test {test} names no irrigation zone, so it reaches no ground. Nothing was read."
			)
		blocks = traceability.distinct(
			dict(entry).get("field")
			for entry in frappe.db.get_all(
				traceability.IRRIGATION_ZONE,
				filters={"name": zone},
				fields=compat.existing_fields(traceability.IRRIGATION_ZONE, ("name", "field")),
				limit=1,
			)
			or []
		)
		if not blocks:
			raise ToolError(
				f"Irrigation Zone {zone!r} is not linked to a Field, so a water result on it "
				"cannot be carried to a block. That link is the whole route from water to "
				"fruit. Nothing was read."
			)
		notes.append(
			"Bounded at the sample. Fruit irrigated before this result was drawn is not what "
			"this test says anything about."
		)
		return (
			{"kind": "water test", "id": test},
			blocks,
			str(row.get("test_date") or "")[:10],
			notes,
		)

	block = as_str(args, "block")
	if block:
		return {"kind": "block", "id": block}, [block], "", notes

	raise ToolError(
		"name what is suspect: block, spray_application or water_test. A block traces everything "
		"it produced; a spray or a water test traces only what was picked after it, which is "
		"almost always the question actually being asked. To go the other way, from a shipment "
		"or a bin back to the ground, use trace_backward. Nothing was read."
	)


def _one(doctype: str, name: str, label: str, finder: str) -> dict:
	if not compat.doctype_exists(doctype):
		raise ToolError(f"{doctype} is not installed on this site. Nothing was read.")
	if not frappe.db.exists(doctype, name):
		raise ToolError(
			f"no {doctype} called {name!r} on this site. {finder} has the register. "
			f"Nothing was read. ({label})"
		)
	return dict(frappe.get_doc(doctype, name).as_dict())


def _clean_customers(tickets: list, settlements: list, shipments: list, invoices: list) -> list:
	"""Everybody who took delivery, by every route, with the route named.

	Four registers can name a customer and they can disagree — a ticket says who
	the truck went to, a shipment says who it was shipped to, an invoice says who
	was billed. A recall telephones all of them, so this merges rather than
	picking one, and says which register produced each name.
	"""
	found: dict = {}
	for rows, route, key in (
		(shipments, "shipment", "customer"),
		(settlements, "settlement", "customer"),
		(tickets, "scale ticket", "customer"),
		(invoices, "sales invoice", "customer"),
	):
		for row in rows:
			customer = str(row.get(key) or "").strip()
			if not customer:
				continue
			entry = found.setdefault(customer, {"customer": customer, "reached_via": [], "records": []})
			if route not in entry["reached_via"]:
				entry["reached_via"].append(route)
			if len(entry["records"]) < 25:
				entry["records"].append(row.get("name"))
	return [found[key] for key in sorted(found)]
