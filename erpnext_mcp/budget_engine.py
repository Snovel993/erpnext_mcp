# SPDX-License-Identifier: MIT
"""Budget vs. actual, and the variance that comes out of it. PURE FUNCTIONS.

No database reads, no side effects, fully testable with deterministic inputs —
the same contract `payroll_gl.py` keeps: everything arrives as an argument, and
everything it returns is derivable again from the same arguments.

THE SHAPE OF A BUDGET, AS THIS MODULE SEES IT. A `budget_doc` is a plain dict
with two lists: `line_items` (one row per general ledger account, each with an
`account`, a `budgeted_amount` and a `threshold_pct`) and `kpi_targets` (one row
per Financial KPI Definition, each with a `kpi_definition`, a `target_value` and
a `threshold_pct`). Everything this module needs off a real Budget document is
read into that shape by `tools/budget.py`, which is also where the general
ledger and the KPI framework are actually read — this module never touches
either.

WHY A PERCENTAGE OF NOTHING IS `None` AND NOT ZERO. A line item with no budgeted
amount has no meaningful variance percentage — the account moved by some amount
against a budget of zero, which is not "0% over" in the way a lender or a farm
manager means that phrase. `variance_amount` still carries the honest figure;
`variance_pct` is `None` and stays `None` through `check_budget_variances`,
which treats it as nothing to check rather than as a value that happens to be
0 — the one reading that would make a genuinely unbudgeted line invisible to
variance alerting instead of the other way round.

SEVERITY IS A RATIO OF RATIOS. `check_budget_variances` compares how far a
variance has moved past its own threshold, not the raw percentage: a line whose
variance is 12% against a 10% threshold and one whose variance is 45% against a
40% threshold are both "just over", and both are Warning. Critical is reserved
for a variance at least twice its threshold, wherever that threshold was set —
so a tightly-watched KPI and a loosely-watched one escalate on the same rule.
"""

from __future__ import annotations

#: The threshold a line or target uses when its own `threshold_pct` is absent.
#: Matches the Percent field's shipped default on both child DocTypes.
DEFAULT_THRESHOLD_PCT = 10.0

STATUS_DRAFT = "Draft"
STATUS_ACTIVE = "Active"
STATUS_CLOSED = "Closed"
STATUSES = (STATUS_DRAFT, STATUS_ACTIVE, STATUS_CLOSED)

SEVERITY_WARNING = "Warning"
SEVERITY_CRITICAL = "Critical"

#: Past this multiple of its own threshold, a breach is Critical rather than
#: Warning. Two is "twice as far over the line as the line itself allows",
#: which is worth a different word than "just over".
CRITICAL_RATIO = 2.0


def _as_float(value, default: float = 0.0) -> float:
	try:
		if value is None or value == "":
			return default
		return float(value)
	except (TypeError, ValueError):
		return default


def _money(value, default: float = 0.0) -> float:
	return round(_as_float(value, default), 2)


def _variance(actual: float, planned: float) -> tuple:
	"""`(variance_amount, variance_pct)`. `variance_pct` is `None` when `planned` is ~0."""
	variance_amount = round(actual - planned, 4)
	if abs(planned) < 0.005:
		return variance_amount, None
	return variance_amount, round((variance_amount / planned) * 100, 2)


# ── computing actuals ────────────────────────────────────────────────────────


def compute_budget_actuals(budget_doc: dict, gl_balances: dict, kpi_values: dict) -> dict:
	"""Fill in `actual`/`variance` for every line item and KPI target.

	`gl_balances` is `{account: actual_amount}` and `kpi_values` is
	`{kpi_definition: actual_value}` — an account or KPI absent from either dict
	reads as "no actual figure available", not as zero, so a target the caller
	simply forgot to resolve does not show up as a 100%-under breach.

	Returns a NEW dict shaped `{"line_items": [...], "kpi_targets": [...]}`, one
	row per input row, in the input order. Nothing is mutated.
	"""
	line_items = []
	for row in budget_doc.get("line_items") or []:
		account = str(row.get("account") or "").strip()
		budgeted = _money(row.get("budgeted_amount"))
		threshold = _as_float(row.get("threshold_pct"), DEFAULT_THRESHOLD_PCT) or DEFAULT_THRESHOLD_PCT
		if account in (gl_balances or {}):
			actual = _money(gl_balances[account])
			variance_amount, variance_pct = _variance(actual, budgeted)
		else:
			actual, variance_amount, variance_pct = None, None, None
		line_items.append(
			{
				"account": account,
				"budgeted_amount": budgeted,
				"actual_amount": actual,
				"variance_amount": variance_amount,
				"variance_pct": variance_pct,
				"threshold_pct": threshold,
			}
		)

	kpi_targets = []
	for row in budget_doc.get("kpi_targets") or []:
		kpi_definition = str(row.get("kpi_definition") or "").strip()
		target = _as_float(row.get("target_value"))
		threshold = _as_float(row.get("threshold_pct"), DEFAULT_THRESHOLD_PCT) or DEFAULT_THRESHOLD_PCT
		raw_actual = (kpi_values or {}).get(kpi_definition)
		if raw_actual is None:
			actual, variance_pct = None, None
		else:
			actual = round(_as_float(raw_actual), 6)
			_amount, variance_pct = _variance(actual, target)
		kpi_targets.append(
			{
				"kpi_definition": kpi_definition,
				"target_value": round(target, 6),
				"actual_value": actual,
				"variance_pct": variance_pct,
				"threshold_pct": threshold,
			}
		)

	return {"line_items": line_items, "kpi_targets": kpi_targets}


# ── checking variances ───────────────────────────────────────────────────────


def _severity_for(ratio: float) -> str:
	return SEVERITY_CRITICAL if ratio >= CRITICAL_RATIO else SEVERITY_WARNING


def check_budget_variances(budget_result: dict, threshold_default: float = DEFAULT_THRESHOLD_PCT) -> list:
	"""Every line item and KPI target whose variance has crossed its own threshold.

	Takes the shape `compute_budget_actuals` returns. A row with a `None`
	`variance_pct` — no actual figure yet, or an unbudgeted line — is not a
	breach; it is a row nothing can be said about yet, which is a different
	claim from "inside every line".

	Ordered worst-first (Critical before Warning), which is the order a variance
	report and an alert message both want to read in.
	"""
	threshold_default = threshold_default or DEFAULT_THRESHOLD_PCT
	breaches = []

	for row in budget_result.get("line_items") or []:
		pct = row.get("variance_pct")
		if pct is None:
			continue
		threshold = _as_float(row.get("threshold_pct"), threshold_default) or threshold_default
		ratio = abs(pct) / threshold
		if ratio < 1.0:
			continue
		breaches.append(
			{
				"kind": "line_item",
				"identifier": row.get("account"),
				"budgeted_amount": row.get("budgeted_amount"),
				"actual_amount": row.get("actual_amount"),
				"variance_amount": row.get("variance_amount"),
				"variance_pct": pct,
				"threshold_pct": threshold,
				"ratio": round(ratio, 2),
				"direction": "over" if pct > 0 else "under",
				"severity": _severity_for(ratio),
			}
		)

	for row in budget_result.get("kpi_targets") or []:
		pct = row.get("variance_pct")
		if pct is None:
			continue
		threshold = _as_float(row.get("threshold_pct"), threshold_default) or threshold_default
		ratio = abs(pct) / threshold
		if ratio < 1.0:
			continue
		breaches.append(
			{
				"kind": "kpi_target",
				"identifier": row.get("kpi_definition"),
				"target_value": row.get("target_value"),
				"actual_value": row.get("actual_value"),
				"variance_pct": pct,
				"threshold_pct": threshold,
				"ratio": round(ratio, 2),
				"direction": "over" if pct > 0 else "under",
				"severity": _severity_for(ratio),
			}
		)

	breaches.sort(key=lambda breach: (breach["severity"] != SEVERITY_CRITICAL, -breach["ratio"]))
	return breaches


def breach_message(breach: dict) -> str:
	"""One sentence for one breach — what a Compliance Alert message is built from."""
	if breach["kind"] == "line_item":
		return (
			f"{breach['identifier']} is {abs(breach['variance_pct']):.1f}% {breach['direction']} "
			f"budget ({breach['actual_amount']:.2f} vs {breach['budgeted_amount']:.2f})"
		)
	return (
		f"{breach['identifier']} is {abs(breach['variance_pct']):.1f}% {breach['direction']} target "
		f"({breach['actual_value']:.4g} vs {breach['target_value']:.4g})"
	)


# ── the combined operation ───────────────────────────────────────────────────


def refresh_budget(
	budget_doc: dict,
	gl_balances: dict,
	kpi_values: dict,
	threshold_default: float = DEFAULT_THRESHOLD_PCT,
) -> dict:
	"""`compute_budget_actuals` followed by `check_budget_variances`, as one call.

	The one entry point `tools/budget.py`'s `refresh_budget` and the overnight
	sweep both call, so the figures a caller sees and the figures written back
	onto the Budget document come out of one computation and cannot drift.
	"""
	result = compute_budget_actuals(budget_doc, gl_balances, kpi_values)
	breaches = check_budget_variances(result, threshold_default=threshold_default)
	return {
		"line_items": result["line_items"],
		"kpi_targets": result["kpi_targets"],
		"breaches": breaches,
		"breach_count": len(breaches),
		"critical_count": len([b for b in breaches if b["severity"] == SEVERITY_CRITICAL]),
		"warning_count": len([b for b in breaches if b["severity"] == SEVERITY_WARNING]),
	}
