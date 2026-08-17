# SPDX-License-Identifier: MIT
"""The spray program: what is in the tank, what went on the ground, in what wind.

WHAT WAS ALREADY HERE AND WHY IT WAS NOT ENOUGH. `tools/spray_rei.py` (v0.78.0)
answers one question extremely well — is this block closed to entry right now —
and `record_spray_application` exists to open that window. It was never a record
of the SPRAY. It takes a flat materials list with quantities, it has nowhere to
put a rate per acre, nowhere to put the wind, nowhere to put a licence number,
and it cannot describe a machine running two sets of tips. A farm asked for its
spray records by a state inspector cannot produce them from it.

So this module is the record, and Spray REI stays the answer. The relationship
is one direction only: an application OPENS restricted-entry windows, and every
question about restricted entry is still asked of `get_active_rei`, which is one
indexed query on a block. Nothing here re-answers it.

────────────────────────────────────────────────────────────────────────────
THE ONE DELIBERATE DIFFERENCE FROM record_spray_application
────────────────────────────────────────────────────────────────────────────

`record_spray_application` REFUSES to write anything when no restricted-entry
interval can be computed. That is right for that tool: its entire purpose is the
window, and a window of zero hours reads on every screen as "this block is
clear".

`create_spray_application` DOES NOT REFUSE. A tank of foliar nitrogen has no
label interval, restricts nobody, and is still a spray application that a farm
has to be able to record — the same pass, the same machine, the same wind, the
same acres. It records the application, creates zero Spray REI records, and says
so in `rei_records_created` and in the summary.

That difference is the single most important line in this module to get right in
both directions. Refusing here would push a real pass onto a clipboard; opening
a zero-hour window there would put somebody in a treated row.

────────────────────────────────────────────────────────────────────────────
MULTI-PRODUCT TANKS, AND THE DUAL FLIP
────────────────────────────────────────────────────────────────────────────

A tank mix is several products each at its OWN rate per acre. Not one rate for
the tank: a cover spray is two or three answers to two or three different
problems, and only a per-product rate can be checked against a label.

A dual flip machine runs one tank through two sets of tips that get flipped
during the pass — fungicide through the upper tower and insecticide through the
lower, or a band herbicide flipped in at the headland. Which product is on which
set is part of the RECIPE (it lives on the tank mix product line); which set was
actually running on a given block is part of the EVENT (it lives on the
application's block row). Those are different facts and a farm that recorded only
the first would have no way to say which block got which product when the flip
happened at a block edge, which is where it usually happens.

────────────────────────────────────────────────────────────────────────────
WEATHER IS RECORDED AND NOT ENFORCED
────────────────────────────────────────────────────────────────────────────

See `SprayApplication._weather_advisories`. Wind outside the label window earns
an advisory on the record, not a refusal. The tank went out three hours ago; a
refusal does not prevent the spray, only the record of it.
"""

from __future__ import annotations

import json

import frappe

from .. import compat, stock_bridge, timezones
from ..args import (
	as_bool,
	as_choice,
	as_date,
	as_float,
	as_int,
	as_limit,
	as_str,
	resolve_company,
)
from ..errors import ToolError
from ..result import ToolResult
from . import spray_rei

NOZZLE_CONFIG = "Spray Nozzle Config"
TANK_MIX = "Spray Tank Mix"
APPLICATION = "Spray Application"
SPRAY_REI = spray_rei.SPRAY_REI
ITEM = "Item"
FARM_TASK = "Farm Task"

APPLIED = "Applied"
PLANNED = "Planned"
CANCELLED = "Cancelled"

#: Block resolution is `spray_rei`'s, not a second copy. A block named on an
#: application and the same block named on a restriction have to resolve to the
#: same docname in the same register, or the application would open a window on a
#: block nobody can then ask about. Reusing the private helper is deliberate: a
#: parallel implementation here is exactly the drift this avoids.
_resolve_block = spray_rei._resolve_block

#: Most product lines one tank carries. A "mix" longer than this is a data entry
#: problem, and the cap is the same order as `stock_bridge.MATERIALS_CAP`.
PRODUCT_CAP = 25

#: Most blocks one application covers, matching `spray_rei.BLOCK_CAP` so an
#: application cannot describe more blocks than it can restrict.
BLOCK_CAP = spray_rei.BLOCK_CAP

#: Most rows any list here returns.
LIST_CAP = 200

_APPLICATION_FIELDS = (
	"name",
	"status",
	"company",
	"tank_mix",
	"applicator",
	"applicator_license",
	"sprayer",
	"source_task",
	"started_at",
	"completed_at",
	"ground_speed_mph",
	"tanks_used",
	"total_acres",
	"products_applied",
	"dual_nozzle",
	"nozzle_set_a",
	"nozzle_set_b",
	"set_a_purpose",
	"set_b_purpose",
	"flip_performed",
	"flip_at",
	"gallons_per_acre",
	"wind_speed_mph",
	"wind_direction",
	"temperature_f",
	"humidity_pct",
	"sky_conditions",
	"weather_source",
	"weather_recorded_at",
	"weather_advisories",
	"rei_hours",
	"rei_source_item",
	"rei_expires_at",
	"phi_days",
	"phi_source_item",
	"phi_clears_on",
	"rei_records_created",
	"cancelled_on",
	"cancellation_reason",
	"notes",
)

_NOZZLE_FIELDS = (
	"name",
	"nozzle_name",
	"company",
	"nozzle_type",
	"manufacturer",
	"orifice_color",
	"disabled",
	"flow_rate_gpm",
	"rated_pressure_psi",
	"droplet_class",
	"pattern",
	"spacing_inches",
	"nozzles_active",
	"boom_width_ft",
	"notes",
)


# ── shared ──────────────────────────────────────────────────────────────────
def _require(doctype: str) -> None:
	compat.require_doctype(
		doctype,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _now() -> str:
	return str(frappe.utils.now())


def _number(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


def _checked(value) -> bool:
	return compat.checked(value)


# ── create_spray_nozzle_config ──────────────────────────────────────────────
def create_spray_nozzle_config(args: dict) -> ToolResult:
	"""Register one nozzle set as it is plumbed on a machine."""
	_require(NOZZLE_CONFIG)
	company = resolve_company(as_str(args, "company"))
	nozzle_name = as_str(args, "nozzle_name", required=True)

	if frappe.db.exists(NOZZLE_CONFIG, nozzle_name):
		raise ToolError(
			f"a nozzle configuration called {nozzle_name!r} already exists. The name is the "
			"docname, and every application that points at it means this one. Name the new set "
			"for what distinguishes it — the tip size, the tower position. Nothing was created."
		)

	flow = as_float(args.get("flow_rate_gpm"), "flow_rate_gpm")
	if flow <= 0:
		raise ToolError(
			"flow_rate_gpm must be greater than zero, and it is gallons per minute PER NOZZLE off "
			"the manufacturer's chart — not the boom total. A set that flows nothing makes every "
			"rate computed through it a division by zero. Nothing was created."
		)

	doc = frappe.new_doc(NOZZLE_CONFIG)
	doc.nozzle_name = nozzle_name
	doc.company = company or None
	doc.flow_rate_gpm = flow
	doc.manufacturer = as_str(args, "manufacturer") or None
	doc.orifice_color = as_str(args, "orifice_color") or None
	doc.rated_pressure_psi = as_float(args.get("rated_pressure_psi"), "rated_pressure_psi")
	doc.spacing_inches = as_float(args.get("spacing_inches"), "spacing_inches")
	doc.boom_width_ft = as_float(args.get("boom_width_ft"), "boom_width_ft")
	doc.nozzles_active = as_int(args, "nozzles_active") or 0
	doc.notes = as_str(args, "notes") or None
	for key in ("nozzle_type", "pattern", "droplet_class"):
		value = as_str(args, key)
		if value:
			doc.set(key, as_choice(NOZZLE_CONFIG, key, value, key))
	if as_bool(args, "disabled"):
		doc.disabled = 1
	doc.insert(ignore_permissions=True)

	warnings = []
	if not doc.spacing_inches:
		warnings.append(
			"No nozzle spacing recorded, so gallons per acre cannot be computed for applications "
			"through this set. An air-blast tower legitimately has none; a boom does not."
		)
	if not doc.droplet_class:
		warnings.append(
			"No droplet class recorded. It is the drift control on most labels — a label reading "
			"'apply as Coarse or coarser' is an instruction about this field."
		)
	if not doc.rated_pressure_psi:
		warnings.append(
			"No rated pressure recorded. A flow rate without the pressure it was read at cannot "
			"be checked against the manufacturer's chart."
		)

	return ToolResult(
		data={**_describe_nozzle(dict(doc.as_dict())), "warnings": warnings},
		summary=f"registered nozzle set {doc.name} at {flow:g} GPM/nozzle",
		docstatus_delta="none → 0 (created)",
	)


def _describe_nozzle(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"nozzle_name": row.get("nozzle_name"),
		"company": row.get("company") or None,
		"nozzle_type": row.get("nozzle_type") or None,
		"manufacturer": row.get("manufacturer") or None,
		"orifice_color": row.get("orifice_color") or None,
		"disabled": _checked(row.get("disabled")),
		"flow_rate_gpm": round(_number(row.get("flow_rate_gpm")), 3),
		"rated_pressure_psi": round(_number(row.get("rated_pressure_psi")), 1) or None,
		"droplet_class": row.get("droplet_class") or None,
		"pattern": row.get("pattern") or None,
		"spacing_inches": round(_number(row.get("spacing_inches")), 2) or None,
		"nozzles_active": int(row.get("nozzles_active") or 0) or None,
		"boom_width_ft": round(_number(row.get("boom_width_ft")), 2) or None,
		"notes": row.get("notes") or None,
	}


# ── list_spray_nozzle_configs ───────────────────────────────────────────────
def list_spray_nozzle_configs(args: dict) -> ToolResult:
	"""Every nozzle set on file."""
	_require(NOZZLE_CONFIG)
	company = resolve_company(as_str(args, "company"))

	filters: dict = {}
	if company:
		filters["company"] = company
	for key in ("nozzle_type", "pattern", "droplet_class"):
		value = as_str(args, key)
		if value:
			filters[key] = value
	if not as_bool(args, "include_disabled", False):
		filters["disabled"] = 0

	rows = frappe.db.get_all(
		NOZZLE_CONFIG,
		filters=filters,
		fields=compat.existing_fields(NOZZLE_CONFIG, _NOZZLE_FIELDS),
		order_by="nozzle_name asc",
		limit=min(as_limit(args), LIST_CAP),
	)
	configs = [_describe_nozzle(dict(row)) for row in rows or []]
	return ToolResult(
		data={"count": len(configs), "nozzle_configs": configs},
		summary=f"{len(configs)} nozzle configuration(s)",
	)


# ── create_spray_tank_mix ───────────────────────────────────────────────────
def _mix_products(args: dict, key: str = "products") -> list[dict]:
	"""The product lines off the argument, with each Item's label copied in.

	THE LABEL NUMBERS ARE READ HERE AND STORED, not fetched at read time. A mix
	written in April and read in a hearing in November has to say what the label
	said in April; a live join says what it says now, which is a different claim.
	"""
	raw = args.get(key)
	if raw in (None, ""):
		raise ToolError(
			f"{key} is required — a tank mix is what is in the tank. Send a list like "
			'[{"item_code": "SULFUR-90", "rate_per_acre": 5, "rate_uom": "Lb", '
			'"nozzle_set": "A"}]. Nothing was created.'
		)
	if isinstance(raw, dict):
		raw = [raw]
	if not isinstance(raw, list):
		raise ToolError(f"{key} must be a list of objects, got {type(raw).__name__}. Nothing was created.")
	if len(raw) > PRODUCT_CAP:
		raise ToolError(
			f"{key} has {len(raw)} lines, more than the {PRODUCT_CAP} one tank carries. Nothing was created."
		)

	out = []
	seen = set()
	for index, entry in enumerate(raw):
		if not isinstance(entry, dict):
			raise ToolError(f"{key}[{index}] must be an object. Nothing was created.")
		item_code = str(entry.get("item_code") or entry.get("item") or "").strip()
		if not item_code:
			raise ToolError(f"{key}[{index}] names no item_code. Nothing was created.")
		if not frappe.db.exists(ITEM, item_code):
			raise ToolError(f"no Item called {item_code!r} on this site. Nothing was created.")
		if item_code in seen:
			raise ToolError(
				f"{item_code!r} is listed twice. One product gets one rate in one tank — two lines "
				"is two rates, and nothing reading the mix could say which applied. Nothing was "
				"created."
			)
		seen.add(item_code)

		try:
			rate = float(entry.get("rate_per_acre"))
		except (TypeError, ValueError):
			raise ToolError(
				f"{key}[{index}].rate_per_acre must be a number, got "
				f"{entry.get('rate_per_acre')!r}. Nothing was created."
			) from None
		if rate <= 0:
			raise ToolError(
				f"{key}[{index}].rate_per_acre must be greater than zero. A product in the tank at "
				"no rate is either not in the tank or is a rate nobody entered, and the two need "
				"different corrections. Nothing was created."
			)

		nozzle_set = str(entry.get("nozzle_set") or "Both").strip().title()
		if nozzle_set in ("A", "B"):
			pass
		elif nozzle_set.lower() == "both":
			nozzle_set = "Both"
		else:
			raise ToolError(
				f"{key}[{index}].nozzle_set must be 'A', 'B' or 'Both', got "
				f"{entry.get('nozzle_set')!r}. Nothing was created."
			)

		rei_hours, phi_days = stock_bridge.item_intervals(item_code)
		item_name = epa = ""
		try:
			item_name = str(frappe.db.get_value(ITEM, item_code, "item_name") or "")
		except Exception:  # pragma: no cover - an Item register shaped differently
			item_name = ""
		if compat.has_field(ITEM, "epa_registration_number"):
			try:
				epa = str(frappe.db.get_value(ITEM, item_code, "epa_registration_number") or "")
			except Exception:  # pragma: no cover
				epa = ""

		out.append(
			{
				"item": item_code,
				"item_name": item_name or item_code,
				"rate_per_acre": rate,
				"rate_uom": str(entry.get("rate_uom") or "").strip() or None,
				"nozzle_set": nozzle_set,
				"rei_hours": rei_hours,
				"phi_days": phi_days,
				"epa_reg_number": str(entry.get("epa_reg_number") or epa or "").strip() or None,
				"target": str(entry.get("target") or "").strip() or None,
			}
		)
	return out


def create_spray_tank_mix(args: dict) -> ToolResult:
	"""Write a tank mix: several products, each at its own rate per acre."""
	_require(TANK_MIX)
	company = resolve_company(as_str(args, "company"))
	mix_name = as_str(args, "mix_name", required=True)

	if frappe.db.exists(TANK_MIX, mix_name):
		raise ToolError(
			f"a tank mix called {mix_name!r} already exists. The name is the docname and every "
			"application filed against it means this one. Nothing was created."
		)

	products = _mix_products(args)
	dual = bool(as_bool(args, "dual_nozzle", False))
	set_a = as_str(args, "nozzle_set_a")
	set_b = as_str(args, "nozzle_set_b")

	for label, value in (("nozzle_set_a", set_a), ("nozzle_set_b", set_b)):
		if value and not frappe.db.exists(NOZZLE_CONFIG, value):
			raise ToolError(
				f"no Spray Nozzle Config called {value!r} on this site — {label} has to name one. "
				"list_spray_nozzle_configs has the register. Nothing was created."
			)

	# The dual-set rules are the controller's and are checked there too. They are
	# ALSO checked here so the refusal is a sentence naming the argument to fix
	# rather than a framework validation error surfacing from doc.insert().
	if dual:
		sets = {line["nozzle_set"] for line in products}
		if "A" not in sets or "B" not in sets:
			missing = "B" if "B" not in sets else "A"
			raise ToolError(
				f"dual_nozzle is set but no product is assigned to set {missing}. A dual mix is "
				"products split across two sets that get flipped mid-pass; if everything runs "
				"through whichever set is on, leave dual_nozzle off — that is a single-set mix. "
				"Nothing was created."
			)
		if not set_a or not set_b:
			raise ToolError(
				"a dual mix names both nozzle sets — nozzle_set_a and nozzle_set_b. A flip to an "
				"unnamed set is not a record of anything. Nothing was created."
			)
		if set_a == set_b:
			raise ToolError(
				f"nozzle_set_a and nozzle_set_b are both {set_a!r}, which is one set rather than "
				"two. Nothing gets flipped. Nothing was created."
			)

	doc = frappe.new_doc(TANK_MIX)
	doc.mix_name = mix_name
	doc.company = company or None
	doc.crop = as_str(args, "crop") or None
	doc.target_pest = as_str(args, "target_pest") or None
	doc.season_year = as_int(args, "season_year") or 0
	doc.tank_size_gal = as_float(args.get("tank_size_gal"), "tank_size_gal")
	doc.carrier_gpa = as_float(args.get("carrier_gpa"), "carrier_gpa")
	doc.dual_nozzle = 1 if dual else 0
	doc.nozzle_set_a = set_a or None
	doc.nozzle_set_b = set_b or None
	doc.set_a_purpose = as_str(args, "set_a_purpose") or None
	doc.set_b_purpose = as_str(args, "set_b_purpose") or None
	doc.notes = as_str(args, "notes") or None
	status = as_str(args, "status")
	doc.status = as_choice(TANK_MIX, "status", status, "status") if status else "Draft"
	for line in products:
		doc.append("products", line)
	doc.insert(ignore_permissions=True)

	described = _describe_mix(doc)
	warnings = []
	if not described["rei_hours"]:
		warnings.append(
			"Nothing in this mix has an REI on its Item, so an application of it opens no "
			"restricted-entry window. For a foliar nutrient that is correct. For a pesticide it "
			"means the label interval has not been entered — set rei_hours on the product's own "
			"Item record, or run install_compliance_fields if this site has no such column."
		)
	if not described["acres_per_tank"]:
		warnings.append(
			"Tank size or carrier rate is missing, so acres per tank cannot be computed — that is "
			"the number the person filling the tank actually works with."
		)
	return ToolResult(
		data={**described, "warnings": warnings},
		summary=(
			f"tank mix {doc.name}: {len(products)} product(s), "
			f"REI {described['rei_hours']:g}h" + (", dual set" if dual else "")
		),
		docstatus_delta="none → 0 (created)",
	)


def _describe_mix(doc) -> dict:
	row = dict(doc.as_dict()) if hasattr(doc, "as_dict") else dict(doc)
	products = []
	for line in row.get("products") or []:
		line = dict(line)
		products.append(
			{
				"item": line.get("item"),
				"item_name": line.get("item_name") or line.get("item"),
				"rate_per_acre": round(_number(line.get("rate_per_acre")), 4),
				"rate_uom": line.get("rate_uom") or None,
				"nozzle_set": line.get("nozzle_set") or "Both",
				"rei_hours": round(_number(line.get("rei_hours")), 2),
				"phi_days": round(_number(line.get("phi_days")), 2),
				"epa_reg_number": line.get("epa_reg_number") or None,
				"target": line.get("target") or None,
			}
		)
	return {
		"name": row.get("name"),
		"mix_name": row.get("mix_name"),
		"status": row.get("status"),
		"company": row.get("company") or None,
		"crop": row.get("crop") or None,
		"target_pest": row.get("target_pest") or None,
		"season_year": int(row.get("season_year") or 0) or None,
		"tank_size_gal": round(_number(row.get("tank_size_gal")), 2) or None,
		"carrier_gpa": round(_number(row.get("carrier_gpa")), 2) or None,
		"acres_per_tank": round(_number(row.get("acres_per_tank")), 3),
		"dual_nozzle": _checked(row.get("dual_nozzle")),
		"nozzle_set_a": row.get("nozzle_set_a") or None,
		"nozzle_set_b": row.get("nozzle_set_b") or None,
		"set_a_purpose": row.get("set_a_purpose") or None,
		"set_b_purpose": row.get("set_b_purpose") or None,
		"rei_hours": round(_number(row.get("rei_hours")), 2),
		"rei_source_item": row.get("rei_source_item") or None,
		"phi_days": round(_number(row.get("phi_days")), 2),
		"phi_source_item": row.get("phi_source_item") or None,
		"product_count": len(products),
		"products": products,
		"notes": row.get("notes") or None,
	}


# ── create_spray_application ────────────────────────────────────────────────
def _application_blocks(args: dict) -> list[dict]:
	"""The block rows, resolved against the same registers Spray REI uses.

	Accepts three shapes because three callers are plausible and all three mean
	the same thing: a bare list of names off a handset, a list of objects with
	acres from a desk, and a single name from somebody spraying one block.
	"""
	raw = args.get("blocks")
	if raw in (None, ""):
		raw = [args.get("block")] if args.get("block") else []
	if isinstance(raw, (str, dict)):
		raw = [raw]
	if not isinstance(raw, list):
		raise ToolError(
			'blocks must be a list — either of names ["Home-7", "Home-8"] or of objects '
			'[{"block": "Home-7", "acres": 4.2, "nozzle_set_used": "A then B"}]. '
			"Nothing was recorded."
		)
	entries = [entry for entry in raw if entry not in (None, "")]
	if not entries:
		raise ToolError(
			"blocks is required. A pass recorded against nowhere restricts nobody and costs "
			"nothing to any block. Nothing was recorded."
		)
	if len(entries) > BLOCK_CAP:
		raise ToolError(
			f"blocks has {len(entries)} entries, more than the {BLOCK_CAP} one application covers. "
			"Nothing was recorded."
		)

	default_doctype = as_str(args, "block_doctype")
	out = []
	seen = set()
	for entry in entries:
		if isinstance(entry, dict):
			name = str(entry.get("block") or entry.get("name") or "").strip()
			doctype_hint = str(entry.get("block_doctype") or default_doctype or "").strip()
			acres = entry.get("acres")
			nozzle_used = str(entry.get("nozzle_set_used") or "Both").strip()
			started = str(entry.get("started_at") or "").strip()
			completed = str(entry.get("completed_at") or "").strip()
			tanks = entry.get("tanks_used")
			notes = str(entry.get("notes") or "").strip()
		else:
			name, doctype_hint = str(entry).strip(), default_doctype
			acres = None
			nozzle_used = "Both"
			started = completed = notes = ""
			tanks = None

		docname, doctype = _resolve_block(name, doctype_hint, "recorded")
		if (doctype, docname) in seen:
			continue
		seen.add((doctype, docname))

		if nozzle_used not in ("Both", "A", "B", "A then B", "B then A"):
			raise ToolError(
				f"nozzle_set_used on block {docname!r} must be one of: Both, A, B, 'A then B', "
				f"'B then A'. Got {nozzle_used!r}. Nothing was recorded."
			)
		acres_value = _number(acres)
		if acres_value < 0:
			raise ToolError(f"acres on block {docname!r} cannot be negative. Nothing was recorded.")

		out.append(
			{
				"block_doctype": doctype,
				"block": docname,
				"acres": acres_value,
				"nozzle_set_used": nozzle_used,
				"started_at": started or None,
				"completed_at": completed or None,
				"tanks_used": _number(tanks) or 0,
				"notes": notes or None,
			}
		)
	return out


def _products_for_application(args: dict, mix: dict | None) -> list[dict]:
	"""The tank as applied — the mix's own lines, or an explicit override.

	An explicit `products` list WINS OVER THE MIX and does not have to match it.
	That is not laxness: a crew that halved a rate because the block is young, or
	dropped a product they ran out of, has applied something different from the
	recipe, and the record has to be of what went out. The difference is reported
	rather than refused.
	"""
	if args.get("products") not in (None, ""):
		return _mix_products(args)
	if mix:
		return [
			{
				"item": line.get("item"),
				"item_name": line.get("item_name") or line.get("item"),
				"rate_per_acre": _number(line.get("rate_per_acre")),
				"rate_uom": line.get("rate_uom") or None,
				"nozzle_set": line.get("nozzle_set") or "Both",
				"rei_hours": _number(line.get("rei_hours")),
				"phi_days": _number(line.get("phi_days")),
				"epa_reg_number": line.get("epa_reg_number") or None,
				"target": line.get("target") or None,
			}
			for line in mix.get("products") or []
		]
	return []


def create_spray_application(args: dict) -> ToolResult:
	"""File a spray: what went out, where, when, in what weather — and open its REIs."""
	_require(APPLICATION)
	company = resolve_company(as_str(args, "company"))

	status = as_str(args, "status") or APPLIED
	status = as_choice(APPLICATION, "status", status, "status")

	mix_name = as_str(args, "tank_mix")
	mix = None
	if mix_name:
		if not frappe.db.exists(TANK_MIX, mix_name):
			raise ToolError(f"no Spray Tank Mix called {mix_name!r} on this site. Nothing was recorded.")
		mix = _describe_mix(frappe.get_doc(TANK_MIX, mix_name))
		if not company and mix.get("company"):
			company = mix["company"]

	blocks = _application_blocks(args)
	products = _products_for_application(args, mix)
	if not products:
		raise ToolError(
			"nothing is in the tank. Pass tank_mix to apply a recipe on file, or products for a "
			"tank mixed at the machine. Nothing was recorded."
		)

	completed_at = as_str(args, "completed_at") or _now()
	started_at = as_str(args, "started_at") or completed_at
	if str(completed_at) < str(started_at):
		raise ToolError(
			f"completed_at ({completed_at}) is before started_at ({started_at}). Every "
			"restricted-entry window runs from the completion, so this would open a window that "
			"closed before the spray began. Nothing was recorded."
		)

	sprayer = as_str(args, "sprayer")
	if sprayer and not frappe.db.exists(spray_rei.ASSET_REGISTER, sprayer):
		raise ToolError(f"no Asset Register record called {sprayer!r}. Nothing was recorded.")
	source_task = as_str(args, "source_task")
	if source_task and (not compat.doctype_exists(FARM_TASK) or not frappe.db.exists(FARM_TASK, source_task)):
		raise ToolError(f"no Farm Task called {source_task!r} on this site. Nothing was recorded.")

	set_a = as_str(args, "nozzle_set_a") or (mix or {}).get("nozzle_set_a") or ""
	set_b = as_str(args, "nozzle_set_b") or (mix or {}).get("nozzle_set_b") or ""
	for label, value in (("nozzle_set_a", set_a), ("nozzle_set_b", set_b)):
		if value and not frappe.db.exists(NOZZLE_CONFIG, value):
			raise ToolError(f"no Spray Nozzle Config called {value!r} — {label}. Nothing was recorded.")
	dual = as_bool(args, "dual_nozzle")
	if dual is None:
		dual = bool((mix or {}).get("dual_nozzle"))

	# ── the intervals ───────────────────────────────────────────────────────
	# `spray_windows` is called rather than reimplemented: the longest-in-the-tank
	# rule has one home, and a second copy here would be a second answer to "how
	# long is this block shut".
	materials = [{"item_code": line["item"], "qty": 1} for line in products]
	windows = stock_bridge.spray_windows(materials, completed_at)
	phi_days = _number(windows.get("phi_days"))
	phi_source = str(windows.get("phi_source_item") or "")
	phi_clears_on = windows.get("phi_clears_on") or None

	stated = args.get("rei_hours")
	if stated is not None:
		# A STATED INTERVAL OVERRIDES EVERY LABEL IN THE TANK. Two cases the
		# register cannot cover: a product this site has not entered yet, and a
		# state or certifier interval longer than the federal label. It does NOT
		# override the PHI, which is a separate label fact with its own argument.
		rei_hours = _number(stated)
		if rei_hours <= 0:
			raise ToolError(
				"rei_hours must be greater than zero when stated. To record a spray that restricts "
				"nobody — a foliar nutrient — omit it entirely and no window is opened. Nothing "
				"was recorded."
			)
		rei_source = as_str(args, "rei_source_item") or products[0]["item"]
	else:
		rei_hours = _number(windows.get("rei_hours"))
		rei_source = str(windows.get("rei_source_item") or "")

	doc = frappe.new_doc(APPLICATION)
	doc.status = status
	doc.company = company or None
	doc.tank_mix = mix_name or None
	doc.applicator = (
		as_str(args, "applicator") or (frappe.session.user if hasattr(frappe, "session") else "") or None
	)
	doc.applicator_license = as_str(args, "applicator_license") or None
	doc.sprayer = sprayer or None
	doc.source_task = source_task or None
	doc.started_at = started_at
	doc.completed_at = completed_at
	doc.ground_speed_mph = as_float(args.get("ground_speed_mph"), "ground_speed_mph")
	doc.tanks_used = as_float(args.get("tanks_used"), "tanks_used")
	doc.dual_nozzle = 1 if dual else 0
	doc.nozzle_set_a = set_a or None
	doc.nozzle_set_b = set_b or None
	doc.set_a_purpose = as_str(args, "set_a_purpose") or (mix or {}).get("set_a_purpose") or None
	doc.set_b_purpose = as_str(args, "set_b_purpose") or (mix or {}).get("set_b_purpose") or None
	doc.flip_performed = 1 if as_bool(args, "flip_performed", False) else 0
	doc.flip_at = as_str(args, "flip_at") or None
	doc.wind_speed_mph = args.get("wind_speed_mph")
	doc.wind_direction = as_str(args, "wind_direction") or None
	doc.temperature_f = args.get("temperature_f")
	doc.humidity_pct = args.get("humidity_pct")
	doc.sky_conditions = as_str(args, "sky_conditions") or None
	weather_source = as_str(args, "weather_source")
	if weather_source:
		doc.weather_source = as_choice(APPLICATION, "weather_source", weather_source, "weather_source")
	doc.weather_recorded_at = as_str(args, "weather_recorded_at") or None
	doc.rei_hours = rei_hours
	doc.rei_source_item = rei_source or None
	doc.phi_days = phi_days
	doc.phi_source_item = phi_source or None
	doc.phi_clears_on = phi_clears_on
	doc.notes = as_str(args, "notes") or None

	total_acres = round(sum(line["acres"] for line in blocks), 3)
	for line in blocks:
		doc.append(
			"blocks",
			{
				"block_doctype": line["block_doctype"],
				"block": line["block"],
				"acres": line["acres"],
				"nozzle_set_used": line["nozzle_set_used"],
				"started_at": line["started_at"],
				"completed_at": line["completed_at"],
				"tanks_used": line["tanks_used"],
				"notes": line["notes"],
			},
		)
	doc.products_applied = json.dumps(_applied_snapshot(products, total_acres))
	doc.insert(ignore_permissions=True)

	# ── the restricted-entry windows ────────────────────────────────────────
	reis, rei_errors, latest_expiry = _open_reis(doc, products, rei_hours, rei_source, company)
	if reis or rei_errors:
		doc.rei_records_created = len(reis)
		doc.rei_expires_at = latest_expiry or None
		for row in doc.blocks:
			for created in reis:
				if created["block"] == row.block and created["block_doctype"] == row.block_doctype:
					row.rei_record = created["name"]
		doc.save(ignore_permissions=True)

	described = _describe_application(dict(doc.as_dict()), include_blocks=True)
	notes = _application_notes(doc, mix, products, rei_hours, status, rei_errors)

	clock = timezones.Renderer(args)
	clock.add(described, "started_at", "completed_at", "flip_at", "rei_expires_at", "weather_recorded_at")

	return ToolResult(
		data={**described, "notes_for_caller": notes, "timezone": clock.block()},
		summary=(
			f"spray {doc.name}: {len(products)} product(s) over {len(blocks)} block(s), "
			f"{total_acres:g} ac, {len(reis)} REI window(s)"
		),
		docstatus_delta="none → 0 (created)",
	)


def _applied_snapshot(products: list[dict], total_acres: float) -> list[dict]:
	"""The mix as applied, with the total of each product this pass put out."""
	snapshot = []
	for line in products:
		entry = dict(line)
		entry["total_applied"] = (
			round(_number(line.get("rate_per_acre")) * total_acres, 4) if total_acres else None
		)
		snapshot.append(entry)
	return snapshot


def _open_reis(doc, products: list[dict], rei_hours: float, rei_source: str, company: str):
	"""One Spray REI per block, or none at all — and never a zero-hour window.

	NOTHING IS CREATED WHEN THE TANK RESTRICTS NOBODY. See the module docstring:
	a foliar nutrient has no label interval and closes no block, and an
	application of it is still a real record. A window of zero hours would read
	on every screen as "this block is clear", which is the one wrong answer.

	A PLANNED APPLICATION OPENS NOTHING either. Nothing has been put on the
	ground, and a restriction on a block nobody has sprayed keeps a crew out of
	somewhere they may work.

	FAILURES ARE ITEMISED, NEVER RAISED. The application is already on the
	record; one block whose window will not save must not lose the record of the
	whole pass — but it must also not be silent, because that block is then
	unrestricted and somebody has to know.
	"""
	created: list[dict] = []
	errors: list[dict] = []
	if str(doc.status) != APPLIED or rei_hours <= 0:
		return created, errors, ""
	if not compat.doctype_exists(SPRAY_REI):
		errors.append(
			{
				"block": "*",
				"reason": "the Spray REI DocType is not installed on this site, so no "
				"restricted-entry window could be opened. Run `bench migrate`.",
			}
		)
		return created, errors, ""

	product_name = ""
	for line in products:
		if line["item"] == rei_source:
			product_name = line.get("item_name") or ""
			break

	latest = ""
	for row in doc.blocks:
		# The block's OWN completion where the pass was long enough for the blocks
		# to differ. A block sprayed at eight in the morning does not stay shut
		# because the last block of the pass finished at two.
		anchor = str(row.completed_at or doc.completed_at)
		expires = str(frappe.utils.add_to_date(anchor, hours=rei_hours))
		try:
			rei = frappe.new_doc(SPRAY_REI)
			rei.status = spray_rei.ACTIVE
			rei.block_doctype = row.block_doctype
			rei.block = row.block
			rei.company = company or None
			rei.sprayer = doc.sprayer or None
			rei.applicator = doc.applicator or None
			rei.source_task = doc.source_task or None
			rei.product = rei_source or None
			rei.product_name = product_name or rei_source or None
			rei.rei_hours = rei_hours
			rei.all_products = json.dumps(_rei_products(products, _number(row.acres)))
			rei.started_at = anchor
			rei.expires_at = expires
			rei.notes = f"Opened by Spray Application {doc.name}."
			rei.insert(ignore_permissions=True)
		except Exception as exc:  # pragma: no cover - reported, never raised
			errors.append({"block": row.block, "reason": f"{type(exc).__name__}: {exc}"})
			continue
		created.append(
			{
				"name": rei.name,
				"block": row.block,
				"block_doctype": row.block_doctype,
				"expires_at": expires,
			}
		)
		latest = max(latest, expires)
	return created, errors, latest


def _rei_products(products: list[dict], acres: float) -> list[dict]:
	"""The tank in `spray_rei`'s own shape, so `get_active_rei` renders it.

	`qty` is the rate times THIS BLOCK's acres, which is a more honest number
	than the tank total: the question asked after somebody feels ill is what
	went onto the ground they were standing on.
	"""
	out = []
	for line in products:
		entry = {
			"item_code": line["item"],
			"item_name": line.get("item_name"),
			"qty": round(_number(line.get("rate_per_acre")) * acres, 4) if acres else None,
			"rei_hours": _number(line.get("rei_hours")),
			"phi_days": _number(line.get("phi_days")),
		}
		if line.get("rate_uom"):
			entry["uom"] = line["rate_uom"]
		out.append(entry)
	return out


def _application_notes(doc, mix, products, rei_hours, status, rei_errors) -> list[str]:
	"""What the caller should know, in the order it matters."""
	notes = []
	for failure in rei_errors:
		notes.append(
			f"NO RESTRICTED-ENTRY WINDOW WAS OPENED ON {failure['block']}: {failure['reason']} "
			"That block is not showing as restricted and a crew could be sent into it."
		)
	if status == PLANNED:
		notes.append(
			"Status is Planned, so no restricted-entry window was opened — nothing has been put "
			"on the ground yet. File it again as Applied, or update it, once the pass is made."
		)
	elif rei_hours <= 0:
		notes.append(
			"No product in this tank has an REI on its Item, so this application opened no "
			"restricted-entry window and no block is closed by it. For a foliar nutrient that is "
			"correct. For a pesticide it means a label interval has not been entered — set "
			"rei_hours on the product's Item, or pass rei_hours here for a state interval."
		)
	if doc.weather_advisories:
		notes.extend(str(doc.weather_advisories).split("\n"))
	if mix and _mix_differs(mix.get("products") or [], products):
		notes.append(
			f"What was applied differs from tank mix {mix['mix_name']!r} as it is written. The "
			"record is of what went out, which is correct — but if the recipe has changed for "
			"good, update the mix so the next pass starts from the right numbers."
		)
	if doc.dual_nozzle and not doc.flip_performed:
		notes.append(
			"This is a dual-set application and flip_performed is not set. If the flip did not "
			"happen, only one set's products reached the ground and the record above overstates "
			"what was applied."
		)
	if not doc.applicator_license:
		notes.append(
			"No applicator licence recorded. It is one of the first things asked for in a state "
			"pesticide record inspection."
		)
	return notes


def _mix_differs(mix_products: list[dict], applied: list[dict]) -> bool:
	"""Whether the tank as applied departs from the recipe, on item or on rate."""
	recipe = {line.get("item"): round(_number(line.get("rate_per_acre")), 4) for line in mix_products}
	actual = {line.get("item"): round(_number(line.get("rate_per_acre")), 4) for line in applied}
	return recipe != actual


def _describe_application(row: dict, include_blocks: bool = False) -> dict:
	out = {
		"name": row.get("name"),
		"status": row.get("status"),
		"company": row.get("company") or None,
		"tank_mix": row.get("tank_mix") or None,
		"applicator": row.get("applicator") or None,
		"applicator_license": row.get("applicator_license") or None,
		"sprayer": row.get("sprayer") or None,
		"source_task": row.get("source_task") or None,
		"started_at": str(row.get("started_at") or "") or None,
		"completed_at": str(row.get("completed_at") or "") or None,
		"ground_speed_mph": round(_number(row.get("ground_speed_mph")), 2) or None,
		"tanks_used": round(_number(row.get("tanks_used")), 2) or None,
		"total_acres": round(_number(row.get("total_acres")), 3),
		"gallons_per_acre": round(_number(row.get("gallons_per_acre")), 3) or None,
		"dual_nozzle": _checked(row.get("dual_nozzle")),
		"nozzle_set_a": row.get("nozzle_set_a") or None,
		"nozzle_set_b": row.get("nozzle_set_b") or None,
		"set_a_purpose": row.get("set_a_purpose") or None,
		"set_b_purpose": row.get("set_b_purpose") or None,
		"flip_performed": _checked(row.get("flip_performed")),
		"flip_at": str(row.get("flip_at") or "") or None,
		"weather": {
			"wind_speed_mph": _optional(row.get("wind_speed_mph")),
			"wind_direction": row.get("wind_direction") or None,
			"temperature_f": _optional(row.get("temperature_f")),
			"humidity_pct": _optional(row.get("humidity_pct")),
			"sky_conditions": row.get("sky_conditions") or None,
			"source": row.get("weather_source") or None,
			"recorded_at": str(row.get("weather_recorded_at") or "") or None,
		},
		"weather_advisories": [
			line for line in str(row.get("weather_advisories") or "").split("\n") if line.strip()
		],
		"rei_hours": round(_number(row.get("rei_hours")), 2),
		"rei_source_item": row.get("rei_source_item") or None,
		"rei_expires_at": str(row.get("rei_expires_at") or "") or None,
		"rei_records_created": int(row.get("rei_records_created") or 0),
		"phi_days": round(_number(row.get("phi_days")), 2),
		"phi_source_item": row.get("phi_source_item") or None,
		"phi_clears_on": str(row.get("phi_clears_on") or "") or None,
		"cancelled_on": str(row.get("cancelled_on") or "") or None,
		"cancellation_reason": row.get("cancellation_reason") or None,
		"notes": row.get("notes") or None,
	}
	products = row.get("products_applied")
	if products:
		try:
			parsed = json.loads(products) if isinstance(products, str) else products
			out["products_applied"] = parsed if isinstance(parsed, list) else []
		except (json.JSONDecodeError, ValueError, TypeError):
			out["products_applied"] = []
	else:
		out["products_applied"] = []
	if include_blocks:
		out["blocks"] = [
			{
				"block": dict(line).get("block"),
				"block_doctype": dict(line).get("block_doctype"),
				"acres": round(_number(dict(line).get("acres")), 3),
				"nozzle_set_used": dict(line).get("nozzle_set_used") or "Both",
				"started_at": str(dict(line).get("started_at") or "") or None,
				"completed_at": str(dict(line).get("completed_at") or "") or None,
				"tanks_used": round(_number(dict(line).get("tanks_used")), 2) or None,
				"rei_record": dict(line).get("rei_record") or None,
				"notes": dict(line).get("notes") or None,
			}
			for line in row.get("blocks") or []
		]
	return out


def _optional(value):
	"""A number, or None where the field was genuinely never filled in.

	Weather readings need this and totals do not: a wind speed of 0.0 is a real
	and important observation (dead calm is an inversion), while an unrecorded
	wind speed is a gap in a compliance record. Collapsing both to 0 would make
	the two indistinguishable on exactly the field where the difference matters
	most.
	"""
	if value in (None, ""):
		return None
	return round(_number(value), 2)


# ── list_spray_applications ─────────────────────────────────────────────────
def list_spray_applications(args: dict) -> ToolResult:
	"""Spray applications over a window, newest first."""
	_require(APPLICATION)
	company = resolve_company(as_str(args, "company"))

	filters: dict = {}
	if company:
		filters["company"] = company
	for key in ("tank_mix", "sprayer", "applicator", "source_task"):
		value = as_str(args, key)
		if value:
			filters[key] = value
	status = as_str(args, "status")
	if status:
		filters["status"] = as_choice(APPLICATION, "status", status, "status")

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["completed_at"] = ("between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"])
	elif from_date:
		filters["completed_at"] = (">=", f"{from_date} 00:00:00")
	elif to_date:
		filters["completed_at"] = ("<=", f"{to_date} 23:59:59")

	rows = frappe.db.get_all(
		APPLICATION,
		filters=filters,
		fields=compat.existing_fields(APPLICATION, _APPLICATION_FIELDS),
		order_by="completed_at desc",
		limit=min(as_limit(args), LIST_CAP),
	)
	applications = [_describe_application(dict(row)) for row in rows or []]

	# Filtering by block is done AFTER the query rather than in it, because the
	# block lives on a child table and a join would either need raw SQL — which
	# this app does not write — or a second query per row. The cap keeps the
	# post-filter honest, and `truncated` says when it bit.
	block = as_str(args, "block")
	if block:
		names = [app["name"] for app in applications]
		matching = set()
		if names:
			rows = frappe.db.get_all(
				"Spray Application Block",
				filters={"parent": ("in", names), "block": block},
				fields=["parent"],
				limit=LIST_CAP * 5,
			)
			matching = {str(dict(row).get("parent")) for row in rows or []}
		applications = [app for app in applications if app["name"] in matching]

	clock = timezones.Renderer(args)
	for app in applications:
		clock.add(app, "started_at", "completed_at", "rei_expires_at")

	total_acres = round(sum(app["total_acres"] for app in applications), 3)
	missing_weather = [app["name"] for app in applications if app["weather"]["wind_speed_mph"] is None]
	return ToolResult(
		data={
			"count": len(applications),
			"total_acres": total_acres,
			"applications": applications,
			"applications_without_wind_recorded": missing_weather,
			"truncated": len(applications) >= min(as_limit(args), LIST_CAP),
			"timezone": clock.block(),
		},
		summary=f"{len(applications)} spray application(s) over {total_acres:g} ac",
	)


# ── get_spray_application ───────────────────────────────────────────────────
def get_spray_application(args: dict) -> ToolResult:
	"""One application in full, with the restrictions it is still holding open."""
	_require(APPLICATION)
	name = as_str(args, "application", required=True)
	if not frappe.db.exists(APPLICATION, name):
		raise ToolError(
			f"no Spray Application called {name!r} on this site. list_spray_applications has the register."
		)
	doc = frappe.get_doc(APPLICATION, name)
	described = _describe_application(dict(doc.as_dict()), include_blocks=True)

	# THE LIVE RESTRICTIONS ARE READ THROUGH `spray_rei`, not recomputed. Whether
	# a block is closed right now is that module's question and it sweeps expired
	# windows before answering, so this stays correct on a bench whose scheduler
	# has stopped.
	blocks = [row["block"] for row in described.get("blocks") or []]
	live = spray_rei.active_for_blocks(blocks, described.get("company") or "")
	by_block: dict = {}
	for window in live:
		by_block.setdefault(str(window.get("block")), []).append(window)
	for row in described.get("blocks") or []:
		windows = by_block.get(str(row["block"]), [])
		row["restricted_now"] = bool(windows)
		row["active_restrictions"] = windows

	mix = None
	if described.get("tank_mix") and frappe.db.exists(TANK_MIX, described["tank_mix"]):
		mix = _describe_mix(frappe.get_doc(TANK_MIX, described["tank_mix"]))

	clock = timezones.Renderer(args)
	clock.add(described, "started_at", "completed_at", "flip_at", "rei_expires_at", "weather_recorded_at")

	restricted = [row["block"] for row in described.get("blocks") or [] if row.get("restricted_now")]
	return ToolResult(
		data={
			**described,
			"tank_mix_detail": mix,
			"blocks_restricted_now": restricted,
			"timezone": clock.block(),
		},
		summary=(
			f"{name}: {len(described.get('blocks') or [])} block(s), "
			f"{described['total_acres']:g} ac, {len(restricted)} still restricted"
		),
	)
