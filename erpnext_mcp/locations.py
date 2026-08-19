# SPDX-License-Identifier: MIT
"""The four registers a task can be routed to, and one row shape across them.

PURE. No frappe import, no database, no side effects — the same contract
`breaks.py`, `datetimes.py` and `bucket_bridge.py` keep, and the reason this can
be imported from `api/shape.py` and `api/mobile.py` without either of them
importing the other.

────────────────────────────────────────────────────────────────────────────
WHY A FOURTH VOCABULARY EXISTS AND WHY IT IS SO SMALL
────────────────────────────────────────────────────────────────────────────

`Field`, `Irrigation Zone`, `Parcel` and `Housing Unit` are the four registers
`dispatch.create_farm_task`, `dispatch.report_field_task` and
`tasktemplates.create_task_from_template` all name in the same refusal:

    location was given with no location_doctype, so nothing can resolve it.
    Pass the register it is in: 'Housing Unit', 'Field', 'Irrigation Zone' or
    'Parcel'. Nothing was created.

The four are genuinely different records — a block has a crop and an acreage, a
zone has a flow rate, a parcel has a county and a title holder, a cabin has beds
and a smoke detector — and NOTHING HERE TRIES TO MERGE THEM. Each keeps its own
doctype, its own tool, its own describe function and its own answer. What this
module holds is the one view a LOCATION PICKER needs: a docname, the register it
came from, something a person can read, and one line of context. Four registers,
one row shape, and the full record still one call away at `get_field`,
`get_irrigation_zone`, `get_parcel` or `get_housing_unit`.

That row shape is `TaskLocationOption` on the handset — `{doctype, name, label,
detail}` with a failable initialiser, so a place without its register cannot be
constructed and cannot be sent. The plan's `{name, doctype, location_type,
acreage, parent_parcel}` is the same row with the numbers the picker sorts and
groups by. Both are produced here, from one function, so the two cannot drift.

────────────────────────────────────────────────────────────────────────────
WHAT `location_type` IS
────────────────────────────────────────────────────────────────────────────

The same string as `doctype`, sent twice. `api/shape.task` has done this since
the dispatch surface was published — the handset decodes `location_type` and the
tools write `location_doctype` — and a location list that named the register in
a third way would be a third spelling of one fact. Two is already one too many
and both are load-bearing.
"""

from __future__ import annotations

#: The four registers, in the order a picker draws them: the ground, then what
#: waters it, then the title it sits on, then the buildings. ORDER IS THE
#: PICKER'S, not alphabetical, for the same reason `farmops_api.ROUTES` is in
#: the app's order — this tuple is the first thing somebody reads to learn what
#: a location can be.
REGISTERS = ("Field", "Irrigation Zone", "Parcel", "Housing Unit")

#: Per register: which key in that tool's own described row carries the readable
#: label, the parent parcel, and the acreage.
#:
#: READ OFF THE DESCRIBED ROW RATHER THAN OFF THE DATABASE, which is what keeps
#: this module pure and is also the more honest layering: `farm._describe_field`,
#: `farm._describe_zone`, `realestate._describe_parcel` and
#: `housing._describe_unit` are each the one place that says what their register
#: reports, and a second reader going to the columns behind them would drift the
#: first time one of those functions grew a derivation. The county on a Field is
#: exactly that — derived through the parcel on every read and stored nowhere —
#: and a column reader would have missed it.
#:
#: `Irrigation Zone` REPORTS ITS PARENT AS THE PARCEL AND NOT THE FIELD, even
#: though it hangs off a block. The parcel is what every other row here is
#: grouped by, and a picker that grouped three registers by parcel and one by
#: block would put the zones somewhere nobody looked for them. The block is
#: still in the detail line, which is where a person reads it.
#:
#: A `Parcel` HAS NO PARENT AND THAT IS THE POINT — it is the top of the tree,
#: so `parent_parcel` is None and a picker draws it as a heading rather than as
#: a child of something.
REGISTER_FIELDS = {
	"Field": {"label": "field_name", "parent": "parcel", "acreage": "acreage"},
	"Irrigation Zone": {"label": "zone_name", "parent": "parcel", "acreage": "area_acres"},
	"Parcel": {"label": "parcel_name", "parent": None, "acreage": "acreage"},
	"Housing Unit": {"label": "unit_name", "parent": "parcel", "acreage": None},
}

#: Which key in a described row carries the owning entity, in the order tried.
#:
#: ALL FOUR REGISTERS CALL IT `owning_entity` AND THE REST OF THIS SITE CALLS IT
#: `company`, which is a real trap and not a tidy-up waiting to happen:
#: `guard.require_scoped_doc` reads `company`, finds nothing on any of these
#: four, skips its own check and passes every docname. `api/mobile._scoped_location`
#: exists because of exactly that, and `_attachment_parent` made the same
#: hand-made check for `Housing Unit` before it. `company` is tried second so a
#: caller handing this a row from somewhere else — a task, a template — still
#: gets a scoped answer rather than an unscoped one.
COMPANY_KEYS = ("owning_entity", "company")


def _text(value) -> str:
	return str(value or "").strip()


def _number(value):
	"""A stored acreage, preserved as given. None only when there is no figure.

	NOT `float(value or 0)`, WHICH WOULD INVENT A MEASUREMENT. A register that
	carries no acreage column at all — `Housing Unit`, measured in beds — must
	report nothing rather than nought, because a picker sorting by size would
	otherwise file every cabin on the farm as zero acres of ground.

	IT CANNOT TELL "UNMEASURED" FROM "MEASURED AT ZERO" AND DOES NOT PRETEND TO.
	That distinction is lost upstream: `farm._describe_field` already collapses a
	missing acreage to `0.0`, and `list_fields` reports the gap separately, by
	docname, in `without_acreage`. Re-deriving it here from a zero would be a
	second opinion about a value this function did not measure — so what is given
	is what comes back, and a caller who needs the gap asks the register that
	knows it.
	"""
	if value in (None, ""):
		return None
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	return round(number, 3)


def detail_line(doctype: str, row: dict) -> str:
	"""The second line of a picker row: what tells two places of one kind apart.

	Composed rather than templated per register, because what distinguishes two
	blocks (the parcel and the crop) is not what distinguishes two cabins (the
	parcel and how many beds), and a single format string would have produced
	"Home Ranch · None · None" for half the farm. Empty when there is nothing to
	say, and the caller then shows the docname alone rather than a bullet with
	nothing round it.
	"""
	parent = _text(row.get((REGISTER_FIELDS.get(doctype) or {}).get("parent") or ""))
	parts = [parent] if parent else []
	if doctype == "Field":
		for key in ("crop", "variety"):
			if _text(row.get(key)):
				parts.append(_text(row.get(key)))
				break
		acres = _number(row.get("acreage"))
		if acres:
			parts.append(f"{acres:g} ac")
	elif doctype == "Irrigation Zone":
		if _text(row.get("field")):
			parts.append(_text(row["field"]))
		if _text(row.get("water_source")):
			parts.append(_text(row["water_source"]))
	elif doctype == "Parcel":
		if _text(row.get("county")):
			parts.append(f"{_text(row['county'])} County")
		acres = _number(row.get("acreage"))
		if acres:
			parts.append(f"{acres:g} ac")
	elif doctype == "Housing Unit":
		if _text(row.get("unit_type")):
			parts.append(_text(row["unit_type"]))
		if row.get("capacity"):
			parts.append(f"sleeps {int(row['capacity'])}")
	return " · ".join(part for part in parts if part)


def option(doctype: str, row: dict) -> dict:
	"""One register row as a location the picker can offer.

	`label` FALLS BACK TO THE DOCNAME AND NEVER TO None. The handset's
	`TaskLocationOption` does the same substitution on its side; doing it here as
	well means the two agree about what a row is called even where the register's
	own name column is empty, which happens on rows imported from the other farm
	system before it had one.
	"""
	spec = REGISTER_FIELDS.get(doctype) or {}
	name = _text(row.get("name"))
	label = _text(row.get(spec.get("label") or "")) or name
	entity = ""
	for key in COMPANY_KEYS:
		entity = _text(row.get(key))
		if entity:
			break
	return {
		"name": name,
		"doctype": doctype,
		# The same string as `doctype`. See the module docstring: `shape.task`
		# has sent both since the dispatch surface was published and the app
		# decodes this one.
		"location_type": doctype,
		"label": label,
		"detail": detail_line(doctype, row) or None,
		"parent_parcel": _text(row.get(spec.get("parent") or "")) or None,
		"acreage": _number(row.get(spec.get("acreage") or "")) if spec.get("acreage") else None,
		"company": entity or None,
	}


def sort_key(entry: dict) -> tuple:
	"""Register order first, then label. The order a person reads a picker in.

	NOT alphabetical across the whole list, which would interleave a cabin
	between two blocks and make the four sections the picker draws impossible to
	find. `REGISTERS.index` is the section order; the label sorts inside it,
	case-folded so `ridge top` and `Ridge Top` are neighbours.
	"""
	doctype = str(entry.get("doctype") or "")
	rank = REGISTERS.index(doctype) if doctype in REGISTERS else len(REGISTERS)
	return (rank, str(entry.get("label") or entry.get("name") or "").casefold())
