# SPDX-License-Identifier: MIT
"""Sustainable CF/Acre, computed with every ingredient left on the plate.

    Sustainable CF/Acre = (Normalized OCF - Maintenance Capex) ÷ Productive Acres

THE OUTPUT IS ITEMIZED AND THAT IS NOT A CONVENIENCE. A single number here is
worthless, because the number's whole claim is that it has been adjusted — and an
adjusted number nobody can inspect is indistinguishable from an arranged one.
Every buyer, lender and auditor who reads this figure will test it one add-back
at a time: which items were taken out, who said they would not recur, what was
counted as maintenance, and which acres were in the denominator. So `compute`
returns each of those, and the final figure is the last key rather than the only
one.

There are three inputs and each is got wrong in a characteristic way. This module
is mostly the three refusals.

────────────────────────────────────────────────────────────────────────────
1. RAW OCF — COMPUTED FROM GL ENTRY BY THE DIRECT METHOD
────────────────────────────────────────────────────────────────────────────

The obvious source is ERPNext's built-in "Cash Flow" report, and v0.19.5 does
not use it. Two reasons, and the second is the one that decided it:

  * It is a Frappe *report*, which means running it needs the report engine, a
    filter dict shaped to whatever version the site has, and a return shape that
    has changed across the versions this app supports. `run_report` exists for a
    caller who wants exactly that; a KPI is not a caller who wants that.
  * A report's output cannot be traced back to rows. The entire argument of this
    module is that the figure has to be inspectable, and "the Cash Flow report
    said 412,000" is the least inspectable form the number could take.

So the operating section is computed here from GL Entry, which exists only for
SUBMITTED vouchers — the same source `generate_quarterly_investment_report` reads
balances from, and the same reason it is trustworthy: a draft journal entry
cannot move this number.

THE METHOD IS DIRECT AND VOUCHER-BY-VOUCHER. For each posted voucher that touched
a cash or bank account in the period, the net cash movement is the sum of its cash
rows; the activity that movement belongs to is read off its OTHER rows. Money
that moved against Income or Expense is operating; against a fixed asset is
investing; against equity or borrowing is financing. A voucher whose other side
is mixed is SPLIT proportionally rather than assigned to whichever bucket is
biggest, because a single journal entry paying an invoice and a loan instalment
together is genuinely both.

Transfers between two of the operation's own cash accounts fall out at zero
without a special case: the two cash rows net against each other inside the same
voucher.

WHAT IS UNCLASSIFIABLE IS REPORTED. A voucher with cash rows and no other rows at
all cannot be assigned, and it is counted and named in the warnings rather than
being swept into operating — which is the direction that would flatter the
figure.

────────────────────────────────────────────────────────────────────────────
2. MAINTENANCE CAPEX — ACTUAL SPEND, NEVER A PERCENTAGE
────────────────────────────────────────────────────────────────────────────

The shortcut everybody takes is to model maintenance capex as a fraction of
revenue. It destroys the only interesting thing the metric measures. An operation
that spent nothing on replacement this year did not have a cheap year — it
borrowed the year from the orchard, and under-investment IS the signal. A formula
substituting 3 % of revenue reports a well-maintained farm instead, every time,
including in the years it matters.

So this is the sum of the maintenance portion of Assets PURCHASED IN THE PERIOD,
and a period with none says zero and means it.

AN UNCLASSIFIED ASSET IS EXCLUDED AND FLAGGED. It is not evidence of maintenance
and not evidence of growth. Reading it as either would be inventing a fact; both
its count and its amount are returned so a reader can see the size of what the
figure does not yet know about.

────────────────────────────────────────────────────────────────────────────
3. PRODUCTIVE ACRES — TIME-WEIGHTED, AND NOT THE ACRES THAT ARE OWNED
────────────────────────────────────────────────────────────────────────────

Fallow ground has acreage, a cost centre and a water right and earns nothing. A
perennial in its pre-yield years is capital under construction wearing the
costume of an orchard. Both are out of the denominator, and both are COUNTED
separately in the output, because pre-yield acres are next year's denominator and
a reader who cannot see them coming cannot read the trend.

And a block that came into bearing in February was productive for part of the
period, not for all of it or none of it. Every field is weighted by the fraction
of the period it was actually productive:

    overlap_start = max(productive_from, period_start)
    overlap_end   = min(productive_through or period_end, period_end)
    weight        = inclusive_days(overlap) / inclusive_days(period)

INCLUSIVE DAYS, both ends. `period_end` is an inclusive date everywhere else in
this app — a Q1 window is 1 January to 31 March and covers ninety days, not
eighty-nine — and a denominator counting one fewer day than the numerator's
period would understate every partial year by a little, in the same direction,
for ever.

A FIELD WITH NO `productive_from_date` IS EXCLUDED AND WARNED ABOUT. This is the
most consequential refusal in the module and the easiest one to get wrong the
kind way. Assuming an undated block is productive puts acres in the denominator
that may be a three-year-old planting, which makes the KPI LOWER and looks
conservative — and quietly converts a data gap into a number somebody will act
on. Saying "eleven blocks have no productive-from date" is the answer; inventing
their dates is not.
"""

from __future__ import annotations

import frappe

from .. import compat, kpi

CASH_ACCOUNT_TYPES = ("Cash", "Bank")

OPERATING = "operating"
INVESTING = "investing"
FINANCING = "financing"

#: How a counterpart account decides which activity a cash movement belongs to.
#:
#: STANDARD DIRECT-METHOD CLASSIFICATION, written out rather than inferred,
#: because the two rows a reader will argue about are the two that are genuinely
#: arguable. A trade payable is OPERATING — paying a supplier is the operation
#: paying for what it consumed, and calling it financing because it is a
#: liability would move every invoice out of operating cash flow. A note payable
#: is FINANCING — the money came from a lender, not from selling fruit. ERPNext
#: types both as Liability and only `account_type` tells them apart.
#:
#: `account_type` wins over `root_type` where both apply, which is why the fixed
#: asset entries are listed first: a tractor is an Asset by root and an investing
#: outflow by any reading.
ACCOUNT_TYPE_ACTIVITY = {
	"Fixed Asset": INVESTING,
	"Capital Work in Progress": INVESTING,
	"Accumulated Depreciation": INVESTING,
	"Depreciation": OPERATING,
	"Payable": OPERATING,
	"Receivable": OPERATING,
	"Stock": OPERATING,
	"Tax": OPERATING,
	"Chargeable": OPERATING,
	"Payroll Payable": OPERATING,
	"Expenses Included In Valuation": OPERATING,
	"Stock Received But Not Billed": OPERATING,
}

ROOT_TYPE_ACTIVITY = {
	"Income": OPERATING,
	"Expense": OPERATING,
	"Equity": FINANCING,
	# A liability that is not a trade payable is borrowing until something says
	# otherwise, and a bare Asset is working capital until something says
	# otherwise. Both fall through to these after `account_type` has had its say.
	"Liability": FINANCING,
	"Asset": OPERATING,
}


# ── dates ───────────────────────────────────────────────────────────────────
def inclusive_days(start: str, end: str) -> int:
	"""Days from `start` to `end` counting BOTH ends. Zero when they cross.

	Inclusive because `period_end` is an inclusive date everywhere else in this
	app: a Q1 window is 1 January to 31 March and covers ninety days. An exclusive
	count would make every partial-year weight slightly too small, always in the
	same direction, which is the shape of error nobody notices and everybody
	inherits.
	"""
	try:
		first = frappe.utils.getdate(start)
		last = frappe.utils.getdate(end)
	except Exception:
		return 0
	return max(0, (last - first).days + 1)


def _overlap_days(from_date: str, through_date: str, period_start: str, period_end: str) -> int:
	start = max(str(from_date), str(period_start))
	end = min(str(through_date or period_end), str(period_end))
	if end < start:
		return 0
	return inclusive_days(start, end)


# ── 1. raw operating cash flow ──────────────────────────────────────────────
def _cash_accounts(company: str) -> dict:
	"""Every ledger account this company treats as cash, by docname.

	Cash AND Bank. A site that keeps its operating account as `Bank` and its till
	as `Cash` — which is every site — would otherwise have half its cash movement
	classified as a counterpart to the other half.
	"""
	rows = frappe.db.get_all(
		"Account",
		filters={"company": company, "account_type": ("in", list(CASH_ACCOUNT_TYPES)), "is_group": 0},
		fields=compat.existing_fields("Account", ("name", "account_name", "account_type", "root_type")),
		limit=500,
	)
	return {row["name"]: row for row in rows}


def _activity_of(account: dict) -> str:
	"""Which cash flow section a counterpart account puts a movement in."""
	by_type = ACCOUNT_TYPE_ACTIVITY.get(str(account.get("account_type") or "").strip())
	if by_type:
		return by_type
	return ROOT_TYPE_ACTIVITY.get(str(account.get("root_type") or "").strip(), OPERATING)


def raw_operating_cash_flow(company: str, period_start: str, period_end: str) -> dict:
	"""Cash that moved through the operating section, voucher by voucher.

	Returns the figure, the counts behind it and anything that could not be
	classified. See the module docstring for the method and for why it is not
	ERPNext's Cash Flow report.
	"""
	out = {
		"value": 0.0,
		"source": "ERPNext GL Entry, operating section (direct method)",
		"computation_note": (
			"Sum of net cash and bank movement on every SUBMITTED voucher posted in the "
			"period, apportioned to operating / investing / financing by the accounts on the "
			"other side of each voucher. Income and Expense counterparts are operating; fixed "
			"asset counterparts are investing; equity and non-trade liabilities are financing. "
			"A voucher whose other side spans more than one section is split in proportion to "
			"the amounts, because a journal entry that pays an invoice and a loan instalment "
			"together really is both. Transfers between two of this company's own cash "
			"accounts net to zero inside the voucher and need no special case. GL Entry exists "
			"only for submitted vouchers, so a draft journal entry cannot move this number."
		),
		"cash_accounts": [],
		"voucher_count": 0,
		"investing_cash_flow": 0.0,
		"financing_cash_flow": 0.0,
		"unclassified_voucher_count": 0,
		"unclassified_cash_movement": 0.0,
	}
	if not compat.doctype_exists("GL Entry"):
		out["computation_note"] = "this site has no GL Entry doctype, so no cash flow could be read."
		return out

	cash = _cash_accounts(company)
	out["cash_accounts"] = sorted(cash)
	if not cash:
		out["computation_note"] = (
			f"{company} has no ledger account typed Cash or Bank, so there is no cash movement "
			"to read. Set account_type on the operating account and the figure computes itself."
		)
		return out

	rows = frappe.db.get_all(
		"GL Entry",
		filters={
			"company": company,
			"posting_date": ("between", [period_start, period_end]),
			"is_cancelled": 0,
		},
		fields=compat.existing_fields(
			"GL Entry", ("name", "account", "debit", "credit", "voucher_type", "voucher_no", "posting_date")
		),
		limit=100000,
	)

	vouchers: dict = {}
	for row in rows or []:
		key = (row.get("voucher_type") or "", row.get("voucher_no") or row.get("name") or "")
		vouchers.setdefault(key, []).append(row)

	operating = investing = financing = 0.0
	touched = 0
	for lines in vouchers.values():
		cash_delta = 0.0
		others: list = []
		for line in lines:
			amount = float(line.get("debit") or 0) - float(line.get("credit") or 0)
			if line.get("account") in cash:
				cash_delta += amount
			else:
				others.append((line.get("account"), abs(amount)))
		if not any(line.get("account") in cash for line in lines):
			continue
		touched += 1
		if round(cash_delta, 2) == 0:
			# A pure transfer between this company's own accounts, or a voucher
			# whose cash rows cancel. Nothing moved in or out.
			continue
		weights = _activity_weights(others)
		if not weights:
			out["unclassified_voucher_count"] += 1
			out["unclassified_cash_movement"] = round(out["unclassified_cash_movement"] + cash_delta, 2)
			continue
		operating += cash_delta * weights.get(OPERATING, 0.0)
		investing += cash_delta * weights.get(INVESTING, 0.0)
		financing += cash_delta * weights.get(FINANCING, 0.0)

	out["value"] = round(operating, 2)
	out["investing_cash_flow"] = round(investing, 2)
	out["financing_cash_flow"] = round(financing, 2)
	out["voucher_count"] = touched
	return out


def _activity_weights(others: list) -> dict:
	"""What fraction of a voucher's non-cash side sits in each section.

	Proportional to the absolute amounts rather than winner-takes-all: a journal
	entry that settles a supplier invoice and a loan instalment out of one payment
	is genuinely both, and assigning the whole movement to whichever line happened
	to be larger would put a loan repayment into operating cash flow on the
	strength of a rounding.
	"""
	buckets: dict = {}
	total = 0.0
	for account_name, amount in others:
		if not amount:
			continue
		account = (
			frappe.db.get_value("Account", account_name, ["name", "account_type", "root_type"], as_dict=True)
			or {}
		)
		activity = _activity_of(account)
		buckets[activity] = buckets.get(activity, 0.0) + amount
		total += amount
	if not total:
		return {}
	return {activity: amount / total for activity, amount in buckets.items()}


# ── 2. maintenance capex ────────────────────────────────────────────────────
def maintenance_capex(company: str, period_start: str, period_end: str) -> dict:
	"""What was actually spent replacing productive capacity in the period.

	Actual spend, itemized per Asset. See the module docstring on why this is
	never a percentage of revenue.
	"""
	out = {
		"total": 0.0,
		"itemized": [],
		"unclassified_asset_count": 0,
		"unclassified_asset_amount": 0.0,
		"growth_capex_excluded": 0.0,
		"source": "ERPNext Asset, purchase_date in the period, capex_type Maintenance or Mixed",
	}
	if not compat.doctype_exists(kpi.ASSET_DOCTYPE):
		out["note"] = (
			"this site has no Asset doctype, so no maintenance capex could be read. Zero here "
			"is the absence of a fixed-asset register rather than a period of no replacement "
			"spending, and the two are very different readings of the same number."
		)
		return out
	if not kpi.capex_installed():
		out["note"] = (
			f"this site's Asset has no {kpi.CAPEX_TYPE_FIELD} column yet — it arrives with the "
			"`bench migrate` that installs v0.19.5. Until it does, no purchase can be told from "
			"any other, so maintenance capex reads zero and the KPI above it is overstated by "
			"however much replacement spending happened in the period."
		)
		return out

	rows = frappe.db.get_all(
		kpi.ASSET_DOCTYPE,
		filters={
			"company": company,
			"purchase_date": ("between", [period_start, period_end]),
			"docstatus": ("!=", 2),
		},
		fields=compat.existing_fields(
			kpi.ASSET_DOCTYPE,
			(
				"name",
				"asset_name",
				"purchase_date",
				"gross_purchase_amount",
				kpi.CAPEX_TYPE_FIELD,
				kpi.MAINTENANCE_PORTION_FIELD,
				kpi.GROWTH_PORTION_FIELD,
			),
		),
		order_by="purchase_date asc, name asc",
		limit=5000,
	)

	total = 0.0
	for row in rows or []:
		capex_type = str(row.get(kpi.CAPEX_TYPE_FIELD) or "").strip()
		gross = round(float(row.get("gross_purchase_amount") or 0), 2)
		if not capex_type:
			out["unclassified_asset_count"] += 1
			out["unclassified_asset_amount"] = round(out["unclassified_asset_amount"] + gross, 2)
			continue
		if capex_type == kpi.CAPEX_GROWTH:
			out["growth_capex_excluded"] = round(out["growth_capex_excluded"] + gross, 2)
			continue
		portion = kpi.maintenance_portion_of(row)
		if not portion:
			continue
		total += portion
		out["itemized"].append(
			{
				"asset": row.get("name"),
				"asset_name": row.get("asset_name"),
				"purchase_date": str(row.get("purchase_date") or "") or None,
				"gross_purchase_amount": gross,
				"capex_type": capex_type,
				"maintenance_portion": portion,
			}
		)
	out["total"] = round(total, 2)
	return out


# ── 3. productive acres ─────────────────────────────────────────────────────
def productive_acres(company: str, period_start: str, period_end: str) -> dict:
	"""The denominator: acres that were actually earning, weighted by time in service."""
	out = {
		"start_of_period": 0.0,
		"end_of_period": 0.0,
		"time_weighted": 0.0,
		"field_count_productive": 0,
		"field_count_pre_yield": 0,
		"field_count_fallow": 0,
		"field_count_retired": 0,
		"field_count_undated": 0,
		"pre_yield_acres": 0.0,
		"fallow_acres": 0.0,
		"undated_acres": 0.0,
		"undated_fields": [],
		"itemized": [],
		"period_days": inclusive_days(period_start, period_end),
		"source": "erpnext_mcp Field, owning_entity scoped, weighted by days productive in the period",
	}
	if not compat.doctype_exists(kpi.FIELD_DOCTYPE):
		out["note"] = "this site has no Field doctype, so there is no acreage to divide by."
		return out

	rows = frappe.db.get_all(
		kpi.FIELD_DOCTYPE,
		filters={"owning_entity": company},
		fields=compat.existing_fields(
			kpi.FIELD_DOCTYPE,
			(
				"name",
				"field_name",
				"acreage",
				"condition",
				kpi.PRODUCTIVE_FROM_FIELD,
				kpi.PRODUCTIVE_THROUGH_FIELD,
				kpi.PRE_YIELD_END_FIELD,
			),
		),
		order_by="name asc",
		limit=5000,
	)

	period_days = out["period_days"] or 1
	weighted = 0.0
	for row in rows or []:
		acres = round(float(row.get("acreage") or 0), 2)
		if str(row.get("condition") or "") == kpi.FALLOW_CONDITION:
			out["field_count_fallow"] += 1
			out["fallow_acres"] = round(out["fallow_acres"] + acres, 2)
			continue

		productive_from = str(row.get(kpi.PRODUCTIVE_FROM_FIELD) or "")
		pre_yield_end = str(row.get(kpi.PRE_YIELD_END_FIELD) or "")
		if not productive_from:
			# No date at all. NOT assumed productive — see the module docstring.
			# A block that at least carries a pre-yield end date is coming into
			# bearing and is counted as such; one with neither is a gap.
			if pre_yield_end:
				out["field_count_pre_yield"] += 1
				out["pre_yield_acres"] = round(out["pre_yield_acres"] + acres, 2)
			else:
				out["field_count_undated"] += 1
				out["undated_acres"] = round(out["undated_acres"] + acres, 2)
				out["undated_fields"].append(row.get("name"))
			continue

		productive_through = str(row.get(kpi.PRODUCTIVE_THROUGH_FIELD) or "")
		if productive_from > str(period_end):
			out["field_count_pre_yield"] += 1
			out["pre_yield_acres"] = round(out["pre_yield_acres"] + acres, 2)
			continue
		if productive_through and productive_through < str(period_start):
			out["field_count_retired"] += 1
			continue

		days = _overlap_days(productive_from, productive_through, period_start, period_end)
		if not days:
			out["field_count_retired"] += 1
			continue

		share = round(acres * days / period_days, 4)
		weighted += share
		out["field_count_productive"] += 1
		if productive_from <= str(period_start) and (
			not productive_through or productive_through >= str(period_start)
		):
			out["start_of_period"] = round(out["start_of_period"] + acres, 2)
		if productive_from <= str(period_end) and (
			not productive_through or productive_through >= str(period_end)
		):
			out["end_of_period"] = round(out["end_of_period"] + acres, 2)
		out["itemized"].append(
			{
				"field": row.get("name"),
				"field_name": row.get("field_name"),
				"acreage": acres,
				"productive_from_date": productive_from,
				"productive_through_date": productive_through or None,
				"days_productive_in_period": days,
				"time_weighted_acres": share,
			}
		)

	out["time_weighted"] = round(weighted, 4)
	return out


# ── the KPI ─────────────────────────────────────────────────────────────────
def compute(company: str, period_start: str, period_end: str) -> dict:
	"""Sustainable CF/Acre for one company over one period, with every ingredient.

	The final figure is the LAST key in the payload rather than the only one, and
	that ordering is the point: a number whose whole claim is that it has been
	adjusted, presented without the adjustments, is indistinguishable from a
	number somebody arranged.

	`sustainable_cf_per_acre` is None — not zero — where the denominator is zero.
	Zero would be a per-acre figure for an operation with no productive acres,
	which is a division nobody performed and an answer nobody should read.
	"""
	period_start = str(period_start)
	period_end = str(period_end)

	adjustments = [kpi.describe(row) for row in kpi.approved_in_period(company, period_start, period_end)]
	totals = kpi.signed_total(adjustments)

	ocf = raw_operating_cash_flow(company, period_start, period_end)
	normalized_ocf = round(float(ocf["value"]) + totals["total_addback"] - totals["total_subtract"], 2)

	capex = maintenance_capex(company, period_start, period_end)
	acres = productive_acres(company, period_start, period_end)

	sustainable = normalized_ocf - capex["total"]
	denominator = float(acres["time_weighted"] or 0)
	per_acre = round(sustainable / denominator, 2) if denominator else None

	return {
		"company": company,
		"period_start": period_start,
		"period_end": period_end,
		"period_days": acres["period_days"],
		"raw_ocf": ocf,
		"normalization_adjustments": adjustments,
		"normalization_adjustments_total_addback": totals["total_addback"],
		"normalization_adjustments_total_subtract": totals["total_subtract"],
		"normalized_ocf": normalized_ocf,
		"maintenance_capex": capex,
		"productive_acres": acres,
		"sustainable_cash_flow": round(sustainable, 2),
		"sustainable_cf_per_acre": per_acre,
		"formula": "(normalized_ocf - maintenance_capex.total) / productive_acres.time_weighted",
		"computation_warnings": _warnings(ocf, adjustments, capex, acres, per_acre),
	}


def _warnings(ocf: dict, adjustments: list, capex: dict, acres: dict, per_acre) -> list:
	"""Everything a reader has to know before quoting the number.

	Written as sentences rather than codes because the audience is a person
	defending the figure across a table, and "3 assets purchased in the period have
	no capex_type" is something they can act on where `UNCLASSIFIED_ASSETS` is not.
	"""
	warnings = []

	if per_acre is None:
		warnings.append(
			"There are no time-weighted productive acres in this period, so the KPI is None "
			"rather than zero — a division nobody performed is not an answer, and a zero here "
			"would be read as one. Check that this company's Fields carry a "
			f"{kpi.PRODUCTIVE_FROM_FIELD}: acres with no date are excluded on purpose."
		)

	if acres["field_count_undated"]:
		warnings.append(
			f"{acres['field_count_undated']} field(s) totalling {acres['undated_acres']} acre(s) "
			f"have no {kpi.PRODUCTIVE_FROM_FIELD} and are EXCLUDED from the denominator: "
			+ ", ".join(str(name) for name in acres["undated_fields"][:10])
			+ ("…" if len(acres["undated_fields"]) > 10 else "")
			+ ". They are not assumed productive, because assuming it would put acres in the "
			"denominator that may be a three-year-old planting — which makes the figure LOOK "
			"conservative while quietly turning a data gap into a number somebody acts on. Fill "
			"the dates in with update_field."
		)

	if acres["field_count_pre_yield"]:
		warnings.append(
			f"{acres['field_count_pre_yield']} field(s) totalling {acres['pre_yield_acres']} "
			"acre(s) are in their pre-yield years and are out of the denominator. They are next "
			"year's denominator, and a per-acre figure that improves the season they come into "
			"bearing has improved for that reason and not for an operational one."
		)

	if capex["unclassified_asset_count"]:
		warnings.append(
			f"{capex['unclassified_asset_count']} asset(s) purchased in this period, worth "
			f"{capex['unclassified_asset_amount']}, have no {kpi.CAPEX_TYPE_FIELD} and are "
			"excluded from maintenance capex — see maintenance_capex.unclassified_*. Excluded "
			"rather than assumed, in either direction: an unclassified purchase is not evidence "
			"of maintenance and not evidence of growth. Until they are classified this KPI is "
			f"overstated by up to {capex['unclassified_asset_amount']} divided by the acres "
			"below. backfill_asset_capex_type classifies history in bulk."
		)

	if not capex["total"] and not capex["unclassified_asset_count"]:
		warnings.append(
			"No maintenance capex at all was recorded in this period. That is either a genuine "
			"gap year between replacements or a period whose spending was never classified, and "
			"the two read identically here. If it is the first, the figure is right and the "
			"trend is the thing to watch: sustained zero maintenance capex is deferred "
			"maintenance, and deferred maintenance is this year's cash flow borrowed from the "
			"orchard."
		)

	if ocf["unclassified_voucher_count"]:
		warnings.append(
			f"{ocf['unclassified_voucher_count']} voucher(s) moved "
			f"{ocf['unclassified_cash_movement']} of cash with nothing on the other side to "
			"classify it by, so that movement is in no section at all. It is usually an opening "
			"balance or a hand-built entry with a single line."
		)

	if not adjustments:
		warnings.append(
			"No approved normalization adjustments apply to this period, so normalized OCF is "
			"raw OCF. That is a legitimate answer — most quarters have nothing non-recurring in "
			"them — but it is worth being sure rather than assuming: an insurance recovery or a "
			"settlement that nobody wrote up is a flattered figure that looks clean. Drafts and "
			"pending proposals do NOT count; only Approved does."
		)

	return warnings
