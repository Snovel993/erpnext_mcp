# SPDX-License-Identifier: MIT
"""Sustainable CF/Acre: the vocabulary the doctype, the service and the tools share.

v0.19.5, and the first release in the v0.19.x run that is not about a regulator.
The metric is

    Sustainable CF/Acre = (Normalized OCF - Maintenance Capex) ÷ Productive Acres

and the argument for it is that headline operating cash flow lies in two
directions at once. It is FLATTERED by money that came in and will not come in
again — an insurance recovery, a settlement, a gain on a tractor sale that landed
in the operating section — and it is flattered AGAIN by maintenance that was not
done. A farm running its irrigation to failure to make a year look good is
destroying the thing the year was earned with, and the headline number goes UP
while it happens.

Sustainable CF/Acre is what is left per productive acre after both corrections:
after the non-recurring items are taken back out, and after the replacement of
what wore out is funded. It is the number that says whether the operation can pay
its owners AND stay whole.

────────────────────────────────────────────────────────────────────────────
WHY THIS MODULE EXISTS RATHER THAN THE CONSTANTS LIVING WHERE THEY ARE USED
────────────────────────────────────────────────────────────────────────────

The same reason `shifts.py` and `training.py` exist. Four places have to agree
about what an approved adjustment is: the doctype controller that refuses a
second one, the service that sums them, the tool that approves one, and the
report the dashboard chart draws. A status string spelled in four files is a
status string that gets renamed in three.

────────────────────────────────────────────────────────────────────────────
ONLY `Approved` COUNTS, AND THAT IS THE WHOLE COMPLIANCE POSTURE
────────────────────────────────────────────────────────────────────────────

An AI can propose a normalization — reading the insurance claim, noticing the
settlement, spotting the working-capital swing — and proposing is genuinely
useful, because the items are scattered across a ledger nobody reads line by
line. What an AI must not do is decide. Whether a hailstorm in a region that
hails every third year is non-recurring is a judgement with a lender on the other
end of it, and `create_normalization_adjustment` therefore produces a DRAFT and
nothing else. `approve_normalization_adjustment` is a separate call, separately
switched on, that takes a signature.

That split is the same one `record_training` makes between filing a record and
signing the supervisor review, and it is the app's standing position: AI
proposes, human approves.

────────────────────────────────────────────────────────────────────────────
MAINTENANCE CAPEX IS ACTUAL SPEND, NEVER A PERCENTAGE
────────────────────────────────────────────────────────────────────────────

The common shortcut is to model maintenance capex as some fraction of revenue.
It is a shortcut that destroys the metric's only interesting signal. If an
operation spent nothing on replacement this year, the honest reading is that this
year's cash flow was borrowed from next year's orchard — and a formula that
substitutes 3 % of revenue reports a well-maintained farm instead. So maintenance
capex here is the sum of what was actually booked as maintenance on Assets
purchased in the period, and a period with none says so.

WHAT IS NOT CLASSIFIED IS EXCLUDED AND REPORTED, not guessed. An Asset with no
`capex_type` is not evidence of maintenance and is not evidence of growth; it is
a gap, and the KPI names it in `computation_warnings` rather than absorbing it
into either side.

────────────────────────────────────────────────────────────────────────────
THE DENOMINATOR IS WHAT IS PRODUCTIVE, NOT WHAT IS OWNED
────────────────────────────────────────────────────────────────────────────

Fallow ground has acreage, a cost centre and a water right, and it earns nothing.
A perennial block in its pre-yield years is capital under construction wearing
the costume of an orchard. Both are excluded, and both are COUNTED SEPARATELY in
the output — pre-yield acres are next year's denominator and a reader who cannot
see them coming cannot read the trend.

And a block that came into production in February counts for the part of the
period it was productive, not for the whole of it. See
`services/sustainable_cf_per_acre.time_weighted_acres`.
"""

from __future__ import annotations

import frappe

from . import compat

DOCTYPE = "Normalization Adjustment"

#: The Asset columns v0.19.5 grafts on, declared in `compliance_fields.py` with
#: the rest of the columns this app puts on somebody else's doctype. Named here
#: because the service, the `create_asset` guard and the backfill all read them
#: and a fieldname spelled in three files is a fieldname that gets renamed in two.
ASSET_DOCTYPE = "Asset"
CAPEX_TYPE_FIELD = "capex_type"
MAINTENANCE_PORTION_FIELD = "maintenance_portion"
GROWTH_PORTION_FIELD = "growth_portion"
CAPEX_JUSTIFICATION_FIELD = "capex_justification"

CAPEX_MAINTENANCE = "Maintenance"
CAPEX_GROWTH = "Growth"
CAPEX_MIXED = "Mixed"
CAPEX_TYPES = (CAPEX_MAINTENANCE, CAPEX_GROWTH, CAPEX_MIXED)

#: How far a Mixed asset's two portions may miss its gross purchase amount before
#: the split is refused. A cent, because the split is entered by a person from an
#: invoice and a third-of-a-dollar rounding is not a disagreement — but a dollar
#: is, and a dollar is how a transposed figure looks.
MIXED_TOLERANCE = 0.01

#: The Field columns v0.19.5 adds, in `field.json` — Field is this app's OWN
#: doctype, so they are declared fields rather than Custom Fields. (v0.19.5
#: checked: no `PlantingSeason` junction exists in this app or is reachable from
#: it, so there was nothing already carrying these dates to reuse.)
FIELD_DOCTYPE = "Field"
PRODUCTIVE_FROM_FIELD = "productive_from_date"
PRODUCTIVE_THROUGH_FIELD = "productive_through_date"
PRE_YIELD_END_FIELD = "pre_yield_end_date"

#: The Field condition that means "out of production this season". Read off the
#: doctype's own Select rather than restated as a rule, because a fallow block
#: still has acreage and a water right and only the condition says it is idle.
FALLOW_CONDITION = "Fallow"


# ── the capex split ─────────────────────────────────────────────────────────
def capex_installed() -> bool:
	"""Is `Asset.capex_type` actually on this site yet?

	The `create_asset` gate is conditional on this, and deliberately so. The
	column arrives on the `bench migrate` that installs v0.19.5, and a tool that
	refused every asset for want of a field the site does not have would take
	asset creation down for the length of an upgrade — on a site where the
	operator may not yet have read why.
	"""
	try:
		return compat.has_field(ASSET_DOCTYPE, CAPEX_TYPE_FIELD)
	except Exception:
		return False


def split_for(capex_type: str, gross: float, maintenance, growth) -> dict:
	"""The two portions a capex type implies, or the reason the split is refused.

	Returns `{"maintenance": float, "growth": float, "error": str}` — `error` empty
	when the split holds. A dict rather than a raise because two callers need it
	and they refuse differently: `create_asset` raises a `ToolError` naming the
	argument, and the backfill records the row as skipped and carries on.

	MAINTENANCE AND GROWTH ARE BOTH DEFAULTED FROM THE TOTAL for the two pure
	types, so a caller says `capex_type="Maintenance"` and nothing else and the
	numbers the KPI reads are correct. Only Mixed requires both, and only Mixed
	is checked against the total — the pure cases cannot disagree with it because
	they are derived from it.
	"""
	gross = float(gross or 0)
	if capex_type == CAPEX_MAINTENANCE:
		return {"maintenance": round(gross, 2), "growth": 0.0, "error": ""}
	if capex_type == CAPEX_GROWTH:
		return {"maintenance": 0.0, "growth": round(gross, 2), "error": ""}
	if capex_type != CAPEX_MIXED:
		return {
			"maintenance": 0.0,
			"growth": 0.0,
			"error": f"capex_type must be one of {', '.join(CAPEX_TYPES)}; got {capex_type!r}.",
		}

	if maintenance in (None, "") or growth in (None, ""):
		return {
			"maintenance": 0.0,
			"growth": 0.0,
			"error": (
				"a Mixed purchase needs BOTH maintenance_portion and growth_portion. Mixed is "
				"the case where one invoice is partly replacing what wore out and partly buying "
				"capacity that was never there — a bigger tractor replacing a smaller one is the "
				"old machine's worth as maintenance and the difference as growth — and the whole "
				"reason to record it as Mixed rather than picking a side is that the two halves "
				"behave differently: the maintenance half recurs and the growth half does not."
			),
		}
	try:
		maintenance = float(maintenance)
		growth = float(growth)
	except (TypeError, ValueError):
		return {"maintenance": 0.0, "growth": 0.0, "error": "the portions must be numbers."}
	if maintenance < 0 or growth < 0:
		return {"maintenance": 0.0, "growth": 0.0, "error": "neither portion can be negative."}

	drift = abs((maintenance + growth) - gross)
	if drift > MIXED_TOLERANCE:
		return {
			"maintenance": 0.0,
			"growth": 0.0,
			"error": (
				f"the portions total {round(maintenance + growth, 2)} and the purchase was "
				f"{round(gross, 2)} — out by {round(drift, 2)}. A split that does not add up to "
				"the invoice is not a split, it is two numbers: whichever way the difference "
				"falls, some of the money is classified as neither maintenance nor growth and "
				f"drops out of every figure computed from it. The tolerance is "
				f"{MIXED_TOLERANCE:.2f} — a rounded cent is not a disagreement and a dollar is, "
				"because a dollar is what a transposed figure looks like."
			),
		}
	return {"maintenance": round(maintenance, 2), "growth": round(growth, 2), "error": ""}


def maintenance_portion_of(row: dict) -> float:
	"""What of one Asset row counts as maintenance capex.

	The stored portion where there is one, and the gross amount where the type is
	Maintenance and nobody filled the portion in — which is what a backfilled row
	looks like, and what a site that classified in the Desk without touching the
	portion fields looks like. Growth contributes nothing and an unclassified row
	contributes nothing; the second is counted and reported separately rather than
	being read as either.
	"""
	capex_type = str(row.get(CAPEX_TYPE_FIELD) or "").strip()
	gross = float(row.get("gross_purchase_amount") or 0)
	if capex_type == CAPEX_MAINTENANCE:
		stored = row.get(MAINTENANCE_PORTION_FIELD)
		return round(float(stored) if stored not in (None, "") else gross, 2)
	if capex_type == CAPEX_MIXED:
		return round(float(row.get(MAINTENANCE_PORTION_FIELD) or 0), 2)
	return 0.0


# ── the statuses ────────────────────────────────────────────────────────────
STATUS_DRAFT = "Draft"
STATUS_PENDING = "Pending Approval"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"
STATUS_SUPERSEDED = "Superseded"

STATUSES = (STATUS_DRAFT, STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_SUPERSEDED)

#: The one status the KPI reads. Everything else is a proposal, a refusal or a
#: correction's leftovers.
COUNTING_STATUS = STATUS_APPROVED

DIRECTION_ADD = "Add-back to OCF"
DIRECTION_SUBTRACT = "Subtract from OCF"
DIRECTIONS = (DIRECTION_ADD, DIRECTION_SUBTRACT)

CATEGORIES = (
	"Insurance-Proceeds",
	"Litigation-Settlement",
	"Weather-Event-Loss",
	"Asset-Sale-Gain",
	"Working-Capital-Timing",
	"Discontinued-Operation",
	"Restructuring",
	"Other",
)

#: The floor under the justification, in characters. NOT A QUALITY BAR — no
#: character count can be one. It is a floor under "one-time" and "per Tim",
#: which are what gets typed when the field is merely required, and both of which
#: an auditor reads as an admission that nobody thought about it.
MIN_JUSTIFICATION = 40

FIELDS = (
	"name",
	"company",
	"fiscal_year",
	"period_start",
	"period_end",
	"amount",
	"direction",
	"category",
	"justification",
	"supporting_document",
	"status",
	"approved_by",
	"approved_on",
	"approver_signature",
	"rejection_reason",
	"superseded_by",
	"creation",
	"owner",
)


# ── naming ──────────────────────────────────────────────────────────────────
def next_in_series(year: str) -> str:
	"""`NADJ-YYYY-0001`, counted from what is already on the site.

	Delegated to `shifts.next_in_series`, which is the same function this app
	already uses for two other series and is generic in everything but its name.
	Reimplementing it here would be a second place for the "the year is the
	RECORD's year, not this year" rule to be got wrong, and that rule matters more
	here than it does on a shift: a Q4 adjustment written up in January is about
	the year that ended, and a series keyed off `today()` would file it under the
	following year for ever.
	"""
	from . import shifts

	return shifts.next_in_series(DOCTYPE, "NADJ", year)


# ── reading ─────────────────────────────────────────────────────────────────
def rows(filters: dict | None = None, limit: int = 500) -> list[dict]:
	"""Adjustments matching `filters`, newest period first."""
	if not compat.doctype_exists(DOCTYPE):
		return []
	return frappe.db.get_all(
		DOCTYPE,
		filters=filters or {},
		fields=compat.existing_fields(DOCTYPE, FIELDS),
		order_by="period_start desc, name desc",
		limit=limit,
	)


def approved_in_period(company: str, period_start: str, period_end: str, limit: int = 500) -> list[dict]:
	"""Every approved adjustment whose period falls INSIDE the window.

	Inside, not overlapping, and the difference is the whole reason this is one
	function rather than a filter written at three call sites. An adjustment
	spanning a quarter is about that quarter; letting it overlap into the
	neighbouring one would count a single insurance recovery in two quarters and
	in the year, and the year would then disagree with the sum of its quarters by
	the amount of the double count. A period that straddles the window is a period
	somebody has to split before it can be defended anyway.
	"""
	return rows(
		{
			"company": company,
			"status": COUNTING_STATUS,
			"period_start": (">=", period_start),
			"period_end": ("<=", period_end),
		},
		limit=limit,
	)


def describe(row: dict) -> dict:
	"""One adjustment as the KPI and the register both report it."""
	amount = float(row.get("amount") or 0)
	direction = str(row.get("direction") or "")
	return {
		"name": row.get("name"),
		"company": row.get("company"),
		"fiscal_year": row.get("fiscal_year"),
		"period_start": str(row.get("period_start") or "") or None,
		"period_end": str(row.get("period_end") or "") or None,
		"amount": round(amount, 2),
		"direction": direction,
		# The number the arithmetic actually uses, so a reader never has to apply
		# the sign themselves and never has to wonder whether somebody did.
		"signed_effect_on_ocf": round(amount if direction == DIRECTION_ADD else -amount, 2),
		"category": row.get("category"),
		"justification": row.get("justification"),
		"status": row.get("status") or STATUS_DRAFT,
		"approved_by": row.get("approved_by") or None,
		"approved_on": str(row.get("approved_on") or "") or None,
		"has_approver_signature": bool(str(row.get("approver_signature") or "").strip()),
		"has_supporting_document": bool(str(row.get("supporting_document") or "").strip()),
		"rejection_reason": row.get("rejection_reason") or None,
		"superseded_by": row.get("superseded_by") or None,
	}


def signed_total(described: list[dict]) -> dict:
	"""Add-backs, subtractions and the net, from a list of `describe` outputs.

	Returned as three numbers rather than one, because the net alone is the thing
	this whole doctype exists to stop anybody reporting: a net of zero can be no
	adjustments at all or a two-hundred-thousand-dollar add-back against a
	two-hundred-thousand-dollar subtraction, and those are not the same set of
	books.
	"""
	addback = sum(entry["amount"] for entry in described if entry["direction"] == DIRECTION_ADD)
	subtract = sum(entry["amount"] for entry in described if entry["direction"] == DIRECTION_SUBTRACT)
	return {
		"total_addback": round(addback, 2),
		"total_subtract": round(subtract, 2),
		"net": round(addback - subtract, 2),
	}
