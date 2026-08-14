# SPDX-License-Identifier: MIT
"""Stock that moves because the work happened, not because somebody remembered.

v0.69.0. Three hooks, one module, and one sentence behind all of them: the
quantity in the shed is a consequence of what the crew did this morning, and an
operation that has to be TOLD about it twice — once by the person who did the
work and once by whoever keys the stock entry — has a shed count that is wrong
by lunchtime on the first busy day.

WHAT THE THREE HOOKS ARE:

  1. **A spray task's tank mix draws itself down.** The chemicals and quantities
     are already on the task, because that is what the applicator was sent to
     put on the block. Completing the task issues them.
  2. **A submitted Purchase Receipt puts what arrived where it arrived.** The
     lines and the warehouse are already on the receipt.
  3. **Any task can name what it consumed.** `materials_used` on the completion
     is the general version of (1) — twine, bin liners, a filter — for the work
     whose consumption is not a tank mix somebody planned in advance.

STOCK NEVER BLOCKS THE COMPLETION, AND THAT IS THE LOAD-BEARING RULE. A worker
stood in a block with a signature, two photographs and a finished spray is
holding a COMPLIANCE record. If the shed count says there were only four litres
of a product they just put five litres of on the ground, the count is wrong and
the spray is right — and an app that refused the completion over it would have
destroyed the record that matters to keep the number that does not. So every
failure in here is caught, named, and returned as a warning ON a completion that
succeeded. `issue_materials` cannot raise; the only refusals are at PARSE time,
before anything is written, where a malformed argument is a client bug and
nothing has happened yet.

ONE STOCK ENTRY PER LINE, WHICH IS NOT THE ERPNext HOUSE STYLE. A hand-keyed
Material Issue carries every line of one job. These are written per item on
purpose: the failure that actually happens is per item — one chemical short in
one warehouse — and a single multi-line entry fails whole. Five chemicals with
one short becomes four issued and one warned about, rather than five silently
un-issued behind a single error. The source linkage makes the set findable
again: every entry from one task names that task.

NOT ONE LINE OF STOCK ENTRY CONSTRUCTION LIVES IN THIS FILE. Every write goes
through `tools/stock_inventory.create_stock_entry` and `submit_stock_entry` —
the same two functions an operator's own call reaches, with the same UOM
conversion, the same warehouse resolution, the same refusal of an entry type
this app will not approximate, and the same `source_doctype`/`source_name`
linkage that `get_stock_entry` reads back. A second builder in here would be a
second set of those decisions to keep in step, and the first one to drift would
be the automatic path nobody watches. What this module owns is WHEN a movement
happens and WHAT IT MEANS when one cannot: the two questions the tool layer
cannot answer, because it has no idea a worker is standing in a block.

THE SWITCHES ARE NOT CONSULTED, AND THAT IS DELIBERATE. `allow_create_stock_entry`
gates the TOOL — whether an MCP caller may write stock directly. This is not an
MCP caller; it is the app recording a consequence of work an operator has already
enabled somebody to file. Gating a drawdown on the write-tool switch would mean
an operation that lets its crew close spray tasks silently keeps a chemical count
that has not moved since March.

THE REI AND PHI WINDOWS ARE COMPUTED HERE AND STAMPED ON THE TASK. `Item.rei_hours`
and `Item.phi_days` are label facts about a product; the WINDOW is a fact about a
block, and it exists the moment the spray finishes. `rei_expires_at` and
`phi_clears_on` on the Farm Task are what the two compliance rules read — see
`compliance_rules.declarative_seed_specs`. The longest interval in the tank wins,
because a mix is under the strictest product in it, and the item that set it is
recorded beside the date so nobody has to reconstruct which one.
"""

from __future__ import annotations

import json

import frappe

from . import compat

STOCK_ENTRY = "Stock Entry"
ITEM = "Item"
WAREHOUSE = "Warehouse"

#: ERPNext's two purposes this module writes, and never a third. A Material
#: Transfer moves stock between warehouses and is somebody's deliberate decision
#: about where things live; a Manufacture entry consumes to produce. Neither is
#: a consequence of a task being finished, so neither is written automatically.
MATERIAL_ISSUE = "Material Issue"
MATERIAL_RECEIPT = "Material Receipt"

#: Most material lines one completion or one receipt will move. Past this
#: something is looping, and forty stock entries written from one tap is a
#: quantity of writes nobody asked for.
MATERIALS_CAP = 40


class MaterialsError(ValueError):
	"""A materials list that cannot be read. Raised at PARSE time only.

	Deliberately not a `ToolError`: this module is imported by the doctype layer
	as well as by tools, and the caller that has a worker in front of it decides
	whether a bad list is a refusal (an argument somebody just passed) or a
	warning (a blob that has been sitting on a task since it was raised).
	"""


# ── reading a materials list ────────────────────────────────────────────────
def parse_materials(raw, label: str = "materials_used") -> list[dict]:
	"""`[{item_code, qty, uom?, warehouse?}]`, validated, or a MaterialsError.

	REFUSED HERE AND NOWHERE LATER, and the reason is the module docstring's
	rule read the other way round: because a stock failure must never fail a
	completion, a materials list that is MALFORMED has to be caught before the
	completion starts — otherwise the only two options left are to swallow a
	client's typo silently or to break the promise. A refusal at parse time
	happens when nothing has been written and the fix is one keystroke.

	Accepts a JSON string as well as a list, because this parses two things: an
	argument that arrived as JSON over the wire and a Long Text column that
	stores the same shape on the task.
	"""
	if raw in (None, ""):
		return []
	if isinstance(raw, str):
		try:
			raw = json.loads(raw)
		except Exception as exc:
			raise MaterialsError(f"{label} is not valid JSON: {exc}") from None
	if isinstance(raw, dict):
		raw = [raw]
	if not isinstance(raw, list):
		raise MaterialsError(
			f'{label} must be a list of objects like [{{"item_code": "SPRAY-01", "qty": 5, '
			f'"uom": "Litre"}}], got {type(raw).__name__}.'
		)
	if len(raw) > MATERIALS_CAP:
		raise MaterialsError(
			f"{label} has {len(raw)} lines, which is more than the {MATERIALS_CAP} one job moves. "
			"Split it, or check the client is not repeating a line."
		)
	out = []
	for index, entry in enumerate(raw):
		if not isinstance(entry, dict):
			raise MaterialsError(
				f'{label}[{index}] must be an object like {{"item_code": "SPRAY-01", "qty": 5}}, '
				f"got {type(entry).__name__}."
			)
		item_code = str(entry.get("item_code") or entry.get("item") or "").strip()
		if not item_code:
			raise MaterialsError(f"{label}[{index}] names no item_code.")
		try:
			qty = float(entry.get("qty"))
		except (TypeError, ValueError):
			raise MaterialsError(
				f"{label}[{index}].qty must be a number, got {entry.get('qty')!r}."
			) from None
		if qty <= 0:
			raise MaterialsError(
				f"{label}[{index}].qty must be positive, got {qty}. A line that moved nothing is "
				"a line nobody meant to write."
			)
		line = {"item_code": item_code, "qty": qty}
		uom = str(entry.get("uom") or "").strip()
		if uom:
			line["uom"] = uom
		warehouse = str(entry.get("warehouse") or "").strip()
		if warehouse:
			line["warehouse"] = warehouse
		out.append(line)
	return out


def materials_from_record(raw, label: str) -> tuple[list, list]:
	"""(materials, warnings) — the forgiving reader, for a stored blob.

	`parse_materials` refuses; this one reports. The difference is who typed the
	value: an ARGUMENT arrived a moment ago from somebody who can fix it, and a
	COLUMN was written when the task was raised, possibly weeks ago, possibly by
	an import. Refusing a completion because a tank mix somebody keyed in March
	will not parse would strand the worker rather than the mistake.
	"""
	try:
		return parse_materials(raw, label), []
	except MaterialsError as exc:
		return [], [
			f"{exc} Nothing was drawn down for it, and the completion is unaffected — fix the "
			"column and issue the stock by hand."
		]


# ── the two writes ──────────────────────────────────────────────────────────
def issue_materials(
	materials: list,
	company: str,
	source_doctype: str,
	source_name: str,
	posting_datetime: str = "",
) -> dict:
	"""One Material Issue per line. NEVER RAISES; failures come back as warnings.

	Returns `{"stock_entries": [...], "warnings": [...], "requested": n, "moved": n}`
	— always all four keys, so a caller reads one shape and branches on the
	numbers rather than on which keys happen to be present.
	"""
	return _move(materials, company, source_doctype, source_name, posting_datetime, MATERIAL_ISSUE)


def receive_materials(
	materials: list,
	company: str,
	source_doctype: str,
	source_name: str,
	posting_datetime: str = "",
) -> dict:
	"""One Material Receipt per line. Same shape, same promise as `issue_materials`."""
	return _move(materials, company, source_doctype, source_name, posting_datetime, MATERIAL_RECEIPT)


def _move(
	materials: list,
	company: str,
	source_doctype: str,
	source_name: str,
	posting_datetime: str,
	purpose: str,
) -> dict:
	out = {"stock_entries": [], "warnings": [], "requested": len(materials or []), "moved": 0}
	if not materials:
		return out
	if not compat.doctype_exists(STOCK_ENTRY):
		out["warnings"].append(
			"this site has no Stock Entry DocType, so nothing was drawn down. ERPNext's Stock "
			"module supplies it; until it is there the materials are recorded on the record "
			"itself and nowhere else."
		)
		return out
	for line in materials:
		try:
			entry = _write_entry(line, company, source_doctype, source_name, posting_datetime, purpose)
		except Exception as exc:  # every failure here is a warning, by design
			out["warnings"].append(
				f"{purpose} for {line.get('qty')} of {line.get('item_code')} was not written: "
				f"{type(exc).__name__}: {exc}. The record this came from is unaffected — move the "
				"stock by hand, or fix the count and re-issue."
			)
			continue
		out["stock_entries"].append(entry)
		out["moved"] += 1
	return out


def _write_entry(
	line: dict,
	company: str,
	source_doctype: str,
	source_name: str,
	posting_datetime: str,
	purpose: str,
) -> dict:
	"""One submitted Stock Entry for one line. Raises; `_move` is what catches.

	CREATED AND THEN SUBMITTED, THROUGH THE TWO SHIPPED TOOLS, in the order they
	were built to be called in. A draft moves nothing, and a drawdown that left
	forty drafts for somebody to submit would be a worse count than no drawdown
	at all — it looks done. Submission is also where ERPNext refuses an issue
	that would take a bin negative, which is the refusal this whole module is
	arranged to turn into a warning rather than a lost completion.
	"""
	from .tools import stock_inventory

	item_code = line["item_code"]
	# THE ONE THING RESOLVED HERE RATHER THAN THERE. `create_stock_entry` requires
	# a warehouse per line and is right to: a caller naming none has not decided
	# where the stock is. An automatic drawdown HAS no caller to ask, so the
	# item's own default for the company stands in — and where there is not one
	# either, the failure says which of the two to fix.
	warehouse = str(line.get("warehouse") or "").strip() or default_warehouse(item_code, company)
	if not warehouse:
		raise ValueError(
			f"no warehouse for {item_code}: the line named none and the item has no default "
			f"warehouse for {company or 'this company'}. Set one with update_item, or name the "
			"warehouse on the line."
		)

	entry = {"item_code": item_code, "qty": line["qty"], "warehouse": warehouse}
	if line.get("uom"):
		entry["uom"] = line["uom"]
	created = stock_inventory.create_stock_entry(
		{
			"entry_type": purpose,
			"company": company,
			"posting_date": str(posting_datetime or "").split(" ")[0] or None,
			"items": [entry],
			"source_doctype": source_doctype,
			"source_name": source_name,
		}
	).data
	stock_inventory.submit_stock_entry({"name": created["name"]})
	return {
		"name": created["name"],
		"purpose": purpose,
		"item_code": item_code,
		"qty": float(line["qty"]),
		"uom": (created.get("items") or [{}])[0].get("uom") or line.get("uom") or stock_uom(item_code),
		"warehouse": warehouse,
		# WHERE THE LINKAGE ACTUALLY LANDED, reported rather than assumed:
		# `Stock Entry.purchase_receipt_no` for an inbound delivery, the remarks
		# marker for a Farm Task, since ERPNext has no column for one. A caller
		# that reported provenance it did not store would be lying in the one
		# place this app cannot afford to.
		"source": created.get("source"),
	}


# ── the item facts the two writes and the two rules need ────────────────────
def default_warehouse(item_code: str, company: str) -> str:
	"""The item's own default warehouse for this company, or "". Never raises."""
	try:
		doc = frappe.get_doc(ITEM, item_code)
	except Exception:
		return ""
	for row in doc.get("item_defaults") or []:
		row_company = row.get("company") if isinstance(row, dict) else getattr(row, "company", "")
		if company and str(row_company or "") != company:
			continue
		found = (
			row.get("default_warehouse") if isinstance(row, dict) else getattr(row, "default_warehouse", "")
		)
		if found:
			return str(found)
	return ""


def stock_uom(item_code: str) -> str:
	"""The item's stock UOM, or "". Never raises."""
	try:
		return str(frappe.db.get_value(ITEM, item_code, "stock_uom") or "")
	except Exception:
		return ""


def item_intervals(item_code: str) -> tuple[int, int]:
	"""`(rei_hours, phi_days)` off the Item, both 0 where absent. Never raises.

	ZERO IS "NOT A RESTRICTED PRODUCT", NOT "NO DATA", and the two are worth
	distinguishing out loud even though both answer 0 here: an item with no REI
	column at all is a site that has not run `install_compliance_fields`, and the
	callers report that separately — see `spray_windows`. What this function
	promises is only that it never turns a missing column into an exception in
	the middle of a completion.
	"""
	rei = phi = 0
	for fieldname, target in (("rei_hours", "rei"), ("phi_days", "phi")):
		if not compat.has_field(ITEM, fieldname):
			continue
		try:
			value = frappe.db.get_value(ITEM, item_code, fieldname)
		except Exception:
			continue
		try:
			number = int(float(value or 0))
		except (TypeError, ValueError):
			continue
		if number <= 0:
			continue
		if target == "rei":
			rei = number
		else:
			phi = number
	return rei, phi


def spray_windows(materials: list, completed_at: str) -> dict:
	"""The REI and PHI windows one tank mix opens, from the moment it finished.

	THE LONGEST INTERVAL IN THE TANK WINS, and it is not an average or a sum. A
	mix is under the strictest product in it: a four-hour REI product and a
	twenty-four-hour one together restrict the block for twenty-four hours, and
	the block does not become half-enterable at hour twelve. The item that set
	each window is returned beside it so nobody has to reopen five labels to find
	out which one they are waiting on.

	Returns `{}` where nothing in the mix restricts anything — which is the
	ordinary case for a fertiliser or a foliar nutrient, and is different from a
	site that cannot answer. Never raises.
	"""
	anchor = str(completed_at or frappe.utils.now())
	rei_hours = phi_days = 0
	rei_item = phi_item = ""
	unreadable = not compat.has_field(ITEM, "rei_hours") and not compat.has_field(ITEM, "phi_days")
	for line in materials or []:
		item_code = line.get("item_code")
		if not item_code:
			continue
		hours, days = item_intervals(str(item_code))
		if hours > rei_hours:
			rei_hours, rei_item = hours, str(item_code)
		if days > phi_days:
			phi_days, phi_item = days, str(item_code)

	out: dict = {}
	if unreadable:
		out["note"] = (
			"This site's Item register has no rei_hours or phi_days column, so no restricted-entry "
			"or pre-harvest window could be computed for this application. Run "
			"install_compliance_fields and the next spray stamps them."
		)
		return out
	if rei_hours:
		out["rei_hours"] = rei_hours
		out["rei_source_item"] = rei_item
		out["rei_expires_at"] = _add_hours(anchor, rei_hours)
	if phi_days:
		out["phi_days"] = phi_days
		out["phi_source_item"] = phi_item
		out["phi_clears_on"] = _add_days(anchor.split(" ")[0], phi_days)
	if out:
		out["spray_completed_at"] = anchor
	return out


def _add_hours(stamp: str, hours: int) -> str:
	try:
		return str(frappe.utils.add_to_date(stamp, hours=hours))
	except Exception:  # pragma: no cover - a stamp frappe cannot parse
		return stamp


def _add_days(date: str, days: int) -> str:
	try:
		return str(frappe.utils.add_days(date, days))
	except Exception:  # pragma: no cover
		return date


# ── does this site's Purchase Receipt already move the stock itself? ─────────
def receipt_already_posted(purchase_receipt: str) -> bool:
	"""Whether submitting this receipt ALREADY wrote stock ledger entries.

	THIS IS THE GUARD THAT KEEPS HOOK 2 FROM DOUBLE-COUNTING, and it is worth
	the paragraph. On a site with ERPNext's Stock module, submitting a Purchase
	Receipt posts its own Stock Ledger Entries — that is what submission MEANS
	for that doctype, and `submit_purchase_receipt` has said so in its docstring
	since v0.68.0. Writing a Material Receipt on top of it would put every
	delivery into the warehouse twice, which is a worse inventory than none.

	So the mirror entry is written only where the receipt did NOT post: a site
	without the stock ledger, a receipt whose items are non-stock, an install
	where Purchase Receipt is a record of what arrived rather than a stock
	document. The check is on the LEDGER rather than on a version number,
	because the ledger is the fact and the version is a proxy for it.

	Answers True on anything it cannot determine. Erring towards "already
	posted" errs towards writing nothing, and a receipt somebody has to enter by
	hand is recoverable in a way a doubled stock balance is not.
	"""
	if not compat.doctype_exists("Stock Ledger Entry"):
		return False
	try:
		return bool(
			frappe.db.exists(
				"Stock Ledger Entry",
				{"voucher_type": "Purchase Receipt", "voucher_no": purchase_receipt},
			)
		)
	except Exception:  # pragma: no cover - a site whose ledger cannot be read
		return True


def receipt_lines(doc) -> list[dict]:
	"""A Purchase Receipt's item rows as materials lines. Never raises."""
	out = []
	for row in doc.get("items") or []:
		item_code = row.get("item_code") if isinstance(row, dict) else getattr(row, "item_code", "")
		if not item_code:
			continue
		raw_qty = row.get("qty") if isinstance(row, dict) else getattr(row, "qty", 0)
		try:
			qty = float(raw_qty or 0)
		except (TypeError, ValueError):
			continue
		if qty <= 0:
			continue
		line = {"item_code": str(item_code), "qty": qty}
		warehouse = row.get("warehouse") if isinstance(row, dict) else getattr(row, "warehouse", "")
		if warehouse:
			line["warehouse"] = str(warehouse)
		uom = row.get("uom") if isinstance(row, dict) else getattr(row, "uom", "")
		if uom:
			line["uom"] = str(uom)
		out.append(line)
	return out[:MATERIALS_CAP]
