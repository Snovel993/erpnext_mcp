# SPDX-License-Identifier: MIT
"""State withholding calculation engines — PURE FUNCTIONS.

No database reads, no side effects, fully testable with deterministic inputs.

v0.29.0. Oregon and Washington. The critical design decision: wage law follows
WORK LOCATION per shift, not employer HQ. An employee who picks cherries in
Hood River on Monday and in White Salmon on Tuesday owes Oregon tax on Monday's
gross and Washington programs on Tuesday's.

Oregon: income tax (ORS 316.037 brackets), Statewide Transit Tax (0.1%),
Paid Leave Oregon (ORS 657B), Workers' Compensation (employer-entered rate).

Washington: Paid Family & Medical Leave (RCW 50A), WA Cares Fund (RCW 50B),
Labor & Industries (workers' comp, employer-entered rates). NO income tax.
"""
from __future__ import annotations

from .withholding import PERIODS_PER_YEAR

SUPPORTED_STATES = ("OR", "WA")

# ── 2025 Oregon Income Tax Brackets (ORS 316.037) ─────────────────────────
# Annual brackets. The engine annualizes gross, looks up the bracket, then
# de-annualizes back to the pay period — same pattern as the federal engine.

OR_SEED_TAX_YEAR = 2025

OR_ANNUAL_BRACKETS = {
    "Single": [
        {"bracket_floor": 0, "bracket_ceiling": 4050, "base_tax": 0, "marginal_rate": 4.75},
        {"bracket_floor": 4050, "bracket_ceiling": 10200, "base_tax": 192.38, "marginal_rate": 6.75},
        {"bracket_floor": 10200, "bracket_ceiling": 125000, "base_tax": 607.50, "marginal_rate": 8.75},
        {"bracket_floor": 125000, "bracket_ceiling": None, "base_tax": 10657.50, "marginal_rate": 9.9},
    ],
    "Married Filing Jointly": [
        {"bracket_floor": 0, "bracket_ceiling": 8100, "base_tax": 0, "marginal_rate": 4.75},
        {"bracket_floor": 8100, "bracket_ceiling": 20400, "base_tax": 384.75, "marginal_rate": 6.75},
        {"bracket_floor": 20400, "bracket_ceiling": 250000, "base_tax": 1215.00, "marginal_rate": 8.75},
        {"bracket_floor": 250000, "bracket_ceiling": None, "base_tax": 21315.00, "marginal_rate": 9.9},
    ],
    "Head of Household": [
        {"bracket_floor": 0, "bracket_ceiling": 4050, "base_tax": 0, "marginal_rate": 4.75},
        {"bracket_floor": 4050, "bracket_ceiling": 10200, "base_tax": 192.38, "marginal_rate": 6.75},
        {"bracket_floor": 10200, "bracket_ceiling": 125000, "base_tax": 607.50, "marginal_rate": 8.75},
        {"bracket_floor": 125000, "bracket_ceiling": None, "base_tax": 10657.50, "marginal_rate": 9.9},
    ],
}

OR_FILING_STATUS_MAP = {
    "Single or Married Filing Separately": "Single",
    "Married Filing Jointly": "Married Filing Jointly",
    "Head of Household": "Head of Household",
}


def calculate_oregon_withholding(
    gross_pay: float,
    pay_frequency: str,
    filing_status: str,
    state_config: dict,
    state_tax_table: list[dict],
) -> dict:
    """Calculate all Oregon payroll taxes for one pay period.

    Args:
        gross_pay: Gross pay for this pay period.
        pay_frequency: One of the PERIODS_PER_YEAR keys.
        filing_status: The tax-table filing status (Single, Married Filing
            Jointly, Head of Household).
        state_config: Dict with OR-specific config fields.
        state_tax_table: List of bracket dicts for the matching filing_status,
            sorted by bracket_floor ascending.

    Returns:
        Dict with all OR tax amounts and a computation_detail breakdown.
    """
    periods = PERIODS_PER_YEAR.get(pay_frequency, 1)
    detail = {"state": "OR", "pay_frequency": pay_frequency, "periods_per_year": periods}

    # ── Oregon Income Tax ─────────────────────────────────────────────
    or_income_tax = 0.0
    income_enabled = bool(int(state_config.get("or_income_tax_enabled", 1)))
    if income_enabled and state_tax_table:
        or_income_tax, bracket_detail = _calc_state_income_tax(
            gross_pay, periods, state_tax_table,
        )
        detail["income_tax"] = bracket_detail
    else:
        detail["income_tax"] = {"note": "disabled or no brackets"}

    # ── Statewide Transit Tax (0.1% of gross, no cap) ─────────────────
    transit_rate = float(state_config.get("or_transit_tax_rate", 0.1)) / 100.0
    or_transit_tax = round(gross_pay * transit_rate, 2)
    detail["transit_tax"] = {
        "rate": transit_rate * 100,
        "tax": or_transit_tax,
    }

    # ── Paid Leave Oregon (ORS 657B) ──────────────────────────────────
    total_rate = float(state_config.get("or_paid_leave_rate", 1.0)) / 100.0
    ee_share_pct = float(state_config.get("or_paid_leave_employee_share", 60.0)) / 100.0
    er_share_pct = float(state_config.get("or_paid_leave_employer_share", 40.0)) / 100.0
    small_employer = bool(int(state_config.get("or_paid_leave_small_employer", 0)))

    total_paid_leave = gross_pay * total_rate
    or_paid_leave_employee = round(total_paid_leave * ee_share_pct, 2)
    if small_employer:
        or_paid_leave_employer = 0.0
    else:
        or_paid_leave_employer = round(total_paid_leave * er_share_pct, 2)

    detail["paid_leave"] = {
        "total_rate": total_rate * 100,
        "employee_share_pct": ee_share_pct * 100,
        "employer_share_pct": er_share_pct * 100,
        "small_employer": small_employer,
        "employee_tax": or_paid_leave_employee,
        "employer_tax": or_paid_leave_employer,
    }

    # ── Workers' Compensation (employer-entered rate) ─────────────────
    wc_rate = float(state_config.get("or_workers_comp_rate", 0)) / 100.0
    or_workers_comp = round(gross_pay * wc_rate, 2)
    detail["workers_comp"] = {
        "rate": wc_rate * 100,
        "tax": or_workers_comp,
    }

    total_employee = round(or_income_tax + or_transit_tax + or_paid_leave_employee, 2)
    total_employer = round(or_paid_leave_employer + or_workers_comp, 2)

    return {
        "state": "OR",
        "or_income_tax": round(or_income_tax, 2),
        "or_transit_tax": or_transit_tax,
        "or_paid_leave_employee": or_paid_leave_employee,
        "or_paid_leave_employer": or_paid_leave_employer,
        "or_workers_comp": or_workers_comp,
        "total_or_employee": total_employee,
        "total_or_employer": total_employer,
        "computation_detail": detail,
    }


def calculate_washington_withholding(
    gross_pay: float,
    state_config: dict,
) -> dict:
    """Calculate all Washington payroll taxes for one pay period.

    Washington has NO income tax — only flat-rate programs.

    Args:
        gross_pay: Gross pay for this pay period.
        state_config: Dict with WA-specific config fields.

    Returns:
        Dict with all WA tax amounts and a computation_detail breakdown.
    """
    detail = {"state": "WA"}

    # ── Paid Family & Medical Leave (RCW 50A) ─────────────────────────
    pfml_rate = float(state_config.get("wa_pfml_rate", 0.92)) / 100.0
    pfml_ee_share = float(state_config.get("wa_pfml_employee_share", 72.76)) / 100.0
    pfml_er_share = float(state_config.get("wa_pfml_employer_share", 27.24)) / 100.0

    total_pfml = gross_pay * pfml_rate
    wa_pfml_employee = round(total_pfml * pfml_ee_share, 2)
    wa_pfml_employer = round(total_pfml * pfml_er_share, 2)

    detail["pfml"] = {
        "rate": pfml_rate * 100,
        "employee_share_pct": pfml_ee_share * 100,
        "employer_share_pct": pfml_er_share * 100,
        "employee_tax": wa_pfml_employee,
        "employer_tax": wa_pfml_employer,
    }

    # ── WA Cares Fund (RCW 50B) ───────────────────────────────────────
    cares_rate = float(state_config.get("wa_cares_rate", 0.58)) / 100.0
    wa_cares_employee = round(gross_pay * cares_rate, 2)
    detail["cares"] = {
        "rate": cares_rate * 100,
        "employee_only": True,
        "tax": wa_cares_employee,
    }

    # ── Labor & Industries (workers' comp) ────────────────────────────
    li_ee_rate = float(state_config.get("wa_li_rate_employee", 0)) / 100.0
    li_er_rate = float(state_config.get("wa_li_rate_employer", 0)) / 100.0
    wa_li_employee = round(gross_pay * li_ee_rate, 2)
    wa_li_employer = round(gross_pay * li_er_rate, 2)
    detail["labor_and_industries"] = {
        "employee_rate": li_ee_rate * 100,
        "employer_rate": li_er_rate * 100,
        "employee_tax": wa_li_employee,
        "employer_tax": wa_li_employer,
    }

    total_employee = round(wa_pfml_employee + wa_cares_employee + wa_li_employee, 2)
    total_employer = round(wa_pfml_employer + wa_li_employer, 2)

    return {
        "state": "WA",
        "wa_pfml_employee": wa_pfml_employee,
        "wa_pfml_employer": wa_pfml_employer,
        "wa_cares_employee": wa_cares_employee,
        "wa_li_employee": wa_li_employee,
        "wa_li_employer": wa_li_employer,
        "total_wa_employee": total_employee,
        "total_wa_employer": total_employer,
        "computation_detail": detail,
    }


def calculate_state_withholding(
    gross_pay: float,
    pay_frequency: str,
    state: str,
    filing_status: str,
    state_config: dict,
    state_tax_table: list[dict] | None = None,
) -> dict:
    """Route to the correct state engine. The dispatch function.

    Args:
        gross_pay: Gross pay for this pay period.
        pay_frequency: One of the PERIODS_PER_YEAR keys.
        state: "OR" or "WA".
        filing_status: Tax-table filing status.
        state_config: State-specific config dict.
        state_tax_table: Income tax brackets (OR only; ignored for WA).

    Returns:
        State withholding result dict.
    """
    if state == "OR":
        return calculate_oregon_withholding(
            gross_pay, pay_frequency, filing_status,
            state_config, state_tax_table or [],
        )
    elif state == "WA":
        return calculate_washington_withholding(gross_pay, state_config)
    else:
        return {
            "state": state,
            "error": f"unsupported state: {state}",
            "total_employee": 0.0,
            "total_employer": 0.0,
        }


def calculate_all_payroll_taxes(
    gross_pay: float,
    pay_frequency: str,
    work_state: str,
    filing_status: str,
    w4_data: dict,
    ytd_gross: float,
    ytd_ss_withheld: float,
    fica_config: dict,
    federal_tax_table: list[dict],
    state_config: dict,
    state_tax_table: list[dict] | None = None,
) -> dict:
    """Combined federal + state payroll tax calculation.

    Calls the federal engine (from v0.28.0) and the appropriate state engine,
    returns a unified breakdown.
    """
    from .withholding import calculate_federal_withholding

    federal = calculate_federal_withholding(
        gross_pay, pay_frequency, w4_data, ytd_gross, ytd_ss_withheld,
        fica_config, federal_tax_table,
    )

    state = calculate_state_withholding(
        gross_pay, pay_frequency, work_state, filing_status,
        state_config, state_tax_table,
    )

    state_ee_key = f"total_{work_state.lower()}_employee"
    state_er_key = f"total_{work_state.lower()}_employer"
    state_employee = state.get(state_ee_key, 0.0)
    state_employer = state.get(state_er_key, 0.0)

    grand_total_employee = round(federal["total_employee_tax"] + state_employee, 2)
    grand_total_employer = round(federal["total_employer_tax"] + state_employer, 2)

    return {
        "federal": federal,
        "state": state,
        "work_state": work_state,
        "grand_total_employee": grand_total_employee,
        "grand_total_employer": grand_total_employer,
        "grand_total_all": round(grand_total_employee + grand_total_employer, 2),
    }


# ── Internal helpers ───────────────────────────────────────────────────────

def _calc_state_income_tax(
    gross_pay: float,
    periods: int,
    tax_table: list[dict],
) -> tuple[float, dict]:
    """Annualize, walk brackets, de-annualize — same method as federal."""
    annual_gross = gross_pay * periods
    detail = {"annual_gross": round(annual_gross, 2)}

    if not tax_table:
        return 0.0, {**detail, "error": "no brackets available"}

    sorted_brackets = sorted(tax_table, key=lambda b: float(b.get("bracket_floor", 0)))

    applied = sorted_brackets[0]
    for bracket in sorted_brackets:
        floor = float(bracket.get("bracket_floor", 0))
        if annual_gross >= floor:
            applied = bracket
        else:
            break

    floor = float(applied.get("bracket_floor", 0))
    base = float(applied.get("base_tax", 0))
    rate = float(applied.get("marginal_rate", 0)) / 100.0
    ceiling = applied.get("bracket_ceiling")

    taxable_in_bracket = annual_gross - floor
    if ceiling is not None and ceiling:
        taxable_in_bracket = min(taxable_in_bracket, float(ceiling) - floor)
    taxable_in_bracket = max(taxable_in_bracket, 0)

    annual_tax = base + taxable_in_bracket * rate
    per_period = annual_tax / periods

    detail["bracket_applied"] = {
        "bracket_floor": floor,
        "bracket_ceiling": ceiling,
        "base_tax": base,
        "marginal_rate": rate * 100,
        "taxable_in_bracket": round(taxable_in_bracket, 2),
        "annual_tax": round(annual_tax, 2),
    }
    detail["per_period"] = round(per_period, 2)

    return max(per_period, 0), detail


def seed_or_brackets() -> list[dict]:
    """Return Oregon bracket rows for all periods, ready for insertion."""
    rows = []
    for filing_status, annual in OR_ANNUAL_BRACKETS.items():
        for period_name, periods in PERIODS_PER_YEAR.items():
            for bracket in annual:
                floor = bracket["bracket_floor"] / periods
                ceiling = bracket["bracket_ceiling"] / periods if bracket["bracket_ceiling"] else None
                base = bracket["base_tax"] / periods
                rows.append({
                    "state": "OR",
                    "tax_year": OR_SEED_TAX_YEAR,
                    "filing_status": filing_status,
                    "bracket_floor": round(floor, 2),
                    "bracket_ceiling": round(ceiling, 2) if ceiling else None,
                    "base_tax": round(base, 2),
                    "marginal_rate": bracket["marginal_rate"],
                })
    return rows
