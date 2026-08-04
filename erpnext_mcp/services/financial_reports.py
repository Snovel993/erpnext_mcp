# SPDX-License-Identifier: MIT
"""The registered windowed reports: Sustainable CF/Acre, OCF, revenue.

v0.19.6, and this file is the demonstration that the standard in
`windowed_reports.py` is a standard rather than a feature on one metric.

THREE COMPUTERS, AND THEY ARE DELIBERATELY NOT THREE OF A KIND. Each one is
registered with the same six lines and each exercises a different corner of the
machinery, which is the only honest way to claim a mechanism generalizes:

  * `sustainable_cf_per_acre` is A RATIO and is NOT bucket-additive. Its window
    is computed whole. It is the retrofit of v0.19.5 and the reason the release
    exists.
  * `ocf` is a FLOW, and is still not bucket-additive — because normalized OCF
    carries approved normalization adjustments, and `kpi.approved_in_period`
    counts an adjustment whose period falls INSIDE the window. A quarterly
    insurance recovery falls inside no monthly bucket, so a year assembled from
    twelve months would drop it silently. This is the case that proves
    "additive" is a property of the COMPUTATION and not of the units.
  * `revenue` IS bucket-additive: a sum over GL rows with no containment rule
    anywhere in it. Its window is assembled from its steps, the two paths agree,
    and the caller gets the per-month trail for free — which is what somebody
    asking "when did it fall" actually wants.

WHY OCF EXISTS SEPARATELY FROM THE KPI. `get_sustainable_cf_per_acre` already
returns raw and normalized OCF inside its payload, and it also returns every
approved adjustment, every classified asset and every productive block — which
is a great deal to send somebody who wanted one number for a covenant test. A
lender's debt service coverage calculation needs normalized OCF and nothing
else, and a KPI that can only be got with its whole apparatus attached is one
people copy the number out of by hand.

WHAT REVENUE IS AND IS NOT. It is cash-basis revenue read from GL Entry:
credits less debits on accounts whose `root_type` is Income, for submitted
vouchers only. It is NOT ERPNext's P&L revenue line and will not always agree
with it — the P&L applies period closing, dimension filters and the company's
own income account tree. The difference is a feature of where it is read from:
this figure traces back to rows, which is the same argument
`sustainable_cf_per_acre` makes for computing OCF from GL rather than off the
Cash Flow report, and it matters for the same reason. Anyone who needs the
statutory figure should run the statutory report; anyone who needs a figure they
can defend line by line wants this one. The payload says which it is.

ADDING A FOURTH IS SIX LINES AND NO NEW CONCEPTS — write a computer taking
(company, period_start, period_end), return a components dict with a value key,
declare which of its keys are sums, which are weighted denominators and which
are itemized lists, and call `register`. The boundaries, the history, the cache,
the statistics, the warnings and the MCP tool all follow. See
`docs/reporting_ttm_standard.md`.
"""

from __future__ import annotations

import frappe

from .. import compat, kpi
from . import sustainable_cf_per_acre as cf_service
from . import windowed_reports as windows

INCOME_ROOT = "Income"


# ── 1. Sustainable CF/Acre — the v0.19.5 retrofit ───────────────────────────
def sustainable_cf_per_acre(company: str, period_start: str, period_end: str) -> dict:
	"""v0.19.5's `compute`, unchanged, under the window standard.

	Passed through rather than wrapped, and that is the point of the retrofit: the
	KPI's arithmetic did not change in v0.19.6 and must not appear to have. What
	changed is that the period it is computed over is now chosen by the window
	rule instead of being typed by a caller.
	"""
	return cf_service.compute(company, period_start, period_end)


# ── 2. operating cash flow, raw and normalized ──────────────────────────────
def operating_cash_flow(company: str, period_start: str, period_end: str) -> dict:
	"""Raw and normalized OCF for one period, with the adjustments itemized.

	The same two numbers `sustainable_cf_per_acre` computes and the same
	adjustments, without the capex split or the acreage denominator. Somebody
	testing a debt service covenant needs exactly this and nothing else.

	NORMALIZED OCF IS THE `value`, not raw. The whole argument of v0.19.5 is that
	headline OCF is flattered in two directions, and a wrapper whose headline
	figure was the unadjusted one would put the flattered number back at the top
	of the payload. Raw is a key beside it, so the size of the correction is
	always visible — an adjustment that moves OCF by forty per cent is a fact
	about the year and about the judgement, and both belong in front of a reader.
	"""
	adjustments = [kpi.describe(row) for row in kpi.approved_in_period(company, period_start, period_end)]
	totals = kpi.signed_total(adjustments)
	ocf = cf_service.raw_operating_cash_flow(company, period_start, period_end)
	normalized = round(float(ocf["value"]) + totals["total_addback"] - totals["total_subtract"], 2)

	warnings = []
	if ocf["unclassified_voucher_count"]:
		warnings.append(
			f"{ocf['unclassified_voucher_count']} voucher(s) moved "
			f"{ocf['unclassified_cash_movement']} of cash with nothing on the other side to "
			"classify it by, so that movement is in no section at all — not in operating, and "
			"not against it. It is usually an opening balance or a hand-built single-line entry."
		)
	if not adjustments:
		warnings.append(
			"No approved normalization adjustments apply to this window, so normalized OCF is raw "
			"OCF. That is a legitimate answer and worth being sure of rather than assuming: a "
			"settlement or an insurance recovery nobody wrote up is a flattered figure that looks "
			"clean. Drafts and pending proposals do not count; only Approved does."
		)

	return {
		"company": company,
		"period_start": period_start,
		"period_end": period_end,
		"raw_ocf": ocf,
		"raw_operating_cash_flow": ocf["value"],
		"investing_cash_flow": ocf["investing_cash_flow"],
		"financing_cash_flow": ocf["financing_cash_flow"],
		"normalization_adjustments": adjustments,
		"normalization_adjustments_total_addback": totals["total_addback"],
		"normalization_adjustments_total_subtract": totals["total_subtract"],
		"normalization_adjustments_net": totals["net"],
		"normalized_ocf": normalized,
		"formula": "raw_ocf.value + normalization add-backs - normalization subtractions",
		"source": "ERPNext GL Entry, operating section (direct method), plus approved adjustments",
		"value": normalized,
		"computation_warnings": warnings,
	}


# ── 3. revenue ──────────────────────────────────────────────────────────────
def revenue(company: str, period_start: str, period_end: str) -> dict:
	"""Income booked in the period, from GL Entry, by account.

	CREDITS LESS DEBITS, because Income is a credit-balance root and a revenue
	figure that came out negative on every well-kept set of books would be read as
	a loss by everybody who saw it. Refunds, credit notes and reversals are debits
	on the same accounts and correctly reduce it.

	SUBMITTED VOUCHERS ONLY. GL Entry does not exist for drafts, so a draft sales
	invoice cannot move this number — which is the same guarantee the OCF figure
	carries and for the same reason.

	Itemized per account, because "revenue fell" and "the cherry account fell
	while everything else held" are different facts and only the second is
	actionable.
	"""
	out = {
		"company": company,
		"period_start": period_start,
		"period_end": period_end,
		"total": 0.0,
		"value": 0.0,
		"by_account": [],
		"account_count": 0,
		"entry_count": 0,
		"source": "ERPNext GL Entry, accounts with root_type Income, submitted vouchers only",
		"basis": (
			"Cash-and-accrual as the ledger has it, NOT ERPNext's P&L revenue line. The P&L "
			"applies period closing and the company's own income account tree; this is the raw "
			"credit-less-debit movement on Income accounts, and it is computed this way so every "
			"figure traces back to rows somebody can open."
		),
		"computation_warnings": [],
	}
	if not compat.doctype_exists("GL Entry"):
		out["computation_warnings"].append(
			"this site has no GL Entry doctype, so no revenue could be read. Zero here is the "
			"absence of a ledger rather than a period with no sales."
		)
		return out

	accounts = frappe.db.get_all(
		"Account",
		filters={"company": company, "root_type": INCOME_ROOT, "is_group": 0},
		fields=compat.existing_fields("Account", ("name", "account_name", "account_number")),
		limit=2000,
	)
	if not accounts:
		out["computation_warnings"].append(
			f"{company} has no ledger account with root_type {INCOME_ROOT}, so there is nowhere for "
			"revenue to have been booked. That is a chart-of-accounts gap rather than a trading "
			"result — get_chart_of_accounts shows what the company does have."
		)
		return out

	by_name = {row["name"]: row for row in accounts}
	rows = frappe.db.get_all(
		"GL Entry",
		filters={
			"company": company,
			"account": ("in", list(by_name)),
			"posting_date": ("between", [period_start, period_end]),
			"is_cancelled": 0,
		},
		fields=compat.existing_fields("GL Entry", ("name", "account", "debit", "credit")),
		limit=100000,
	)

	totals: dict = {}
	for row in rows or []:
		amount = float(row.get("credit") or 0) - float(row.get("debit") or 0)
		totals[row.get("account")] = totals.get(row.get("account"), 0.0) + amount
	out["entry_count"] = len(rows or [])

	itemized = []
	grand = 0.0
	for account, amount in sorted(totals.items(), key=lambda pair: -abs(pair[1])):
		grand += amount
		itemized.append(
			{
				"name": account,
				"account": account,
				"account_name": (by_name.get(account) or {}).get("account_name"),
				"account_number": (by_name.get(account) or {}).get("account_number"),
				"amount": round(amount, 2),
			}
		)
	out["by_account"] = itemized
	out["account_count"] = len(itemized)
	out["total"] = round(grand, 2)
	out["value"] = out["total"]

	if not itemized:
		out["computation_warnings"].append(
			f"No income was posted to any of {company}'s {len(accounts)} income account(s) in this "
			"window. GL Entry exists only for SUBMITTED vouchers, so the first thing to check is "
			"whether the invoices for the period are still drafts."
		)
	return out


# ── registration ────────────────────────────────────────────────────────────
#
# Six lines each, and they are the whole of putting a computation under the
# standard. See `windowed_reports.register` for what the key lists mean.
def _kpi_available() -> bool:
	try:
		return bool(compat.doctype_exists(kpi.DOCTYPE))
	except Exception:  # pragma: no cover
		return False


windows.register(
	"sustainable_cf_per_acre",
	sustainable_cf_per_acre,
	kpi_key="sustainable_cf_per_acre",
	label="Sustainable CF/Acre",
	value_key="sustainable_cf_per_acre",
	sum_keys=(
		"raw_ocf.value",
		"normalized_ocf",
		"normalization_adjustments_total_addback",
		"normalization_adjustments_total_subtract",
		"maintenance_capex.total",
		"sustainable_cash_flow",
	),
	weighted_keys=("productive_acres.time_weighted",),
	list_keys=("normalization_adjustments", "maintenance_capex.itemized", "productive_acres.itemized"),
	# NOT additive, twice over: it is a ratio, and its adjustments are counted by
	# period containment. See the module docstring.
	bucket_additive=False,
	unit="currency per acre",
	available=_kpi_available,
)

windows.register(
	"ocf",
	operating_cash_flow,
	kpi_key="ocf",
	label="Operating Cash Flow (normalized)",
	value_key="normalized_ocf",
	sum_keys=(
		"raw_operating_cash_flow",
		"normalized_ocf",
		"normalization_adjustments_total_addback",
		"normalization_adjustments_total_subtract",
		"investing_cash_flow",
		"financing_cash_flow",
	),
	list_keys=("normalization_adjustments",),
	bucket_additive=False,
	unit="currency",
)

windows.register(
	"revenue",
	revenue,
	kpi_key="revenue",
	label="Revenue",
	value_key="total",
	sum_keys=("total", "entry_count"),
	list_keys=("by_account",),
	# The one that IS additive. Its window is assembled from its steps and the
	# per-step trail rides along in `window.buckets`.
	bucket_additive=True,
	unit="currency",
)


# ── the demonstration wrappers ──────────────────────────────────────────────
def get_ocf_windowed(company: str, as_of=None, **kwargs) -> dict:
	"""Normalized operating cash flow, TTM by default, with its own history."""
	return windows.run("ocf", company, as_of=as_of, **kwargs)


def get_revenue_windowed(company: str, as_of=None, **kwargs) -> dict:
	"""Revenue, TTM by default, with the per-step trail and its own history."""
	return windows.run("revenue", company, as_of=as_of, **kwargs)


def get_sustainable_cf_per_acre_windowed(company: str, as_of=None, **kwargs) -> dict:
	"""The v0.19.5 KPI, TTM by default. What `get_sustainable_cf_per_acre` now returns."""
	return windows.run("sustainable_cf_per_acre", company, as_of=as_of, **kwargs)
