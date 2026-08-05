# SPDX-License-Identifier: MIT
"""Federal withholding calculation engine — a PURE FUNCTION.

No database reads, no side effects, fully testable with deterministic inputs.

Implements the IRS percentage method (2020+ W-4) from Publication 15-T, plus
FICA (Social Security, Medicare, Additional Medicare) and FUTA.
"""
from __future__ import annotations

PERIODS_PER_YEAR = {
    "Annual": 1,
    "Monthly": 12,
    "Semimonthly": 24,
    "Biweekly": 26,
    "Weekly": 52,
    "Daily": 260,
}

# Standard deduction amounts by filing status (2025 — used when the W-4
# does not specify Step 4(b) deductions). These are the amounts built into
# the percentage method tables, so they are NOT subtracted again.
#
# The percentage method tables already incorporate the standard deduction.
# Step 4(b) is for ADDITIONAL deductions beyond the standard deduction.


def calculate_federal_withholding(
    gross_pay: float,
    pay_frequency: str,
    w4_data: dict,
    ytd_gross: float,
    ytd_ss_withheld: float,
    fica_config: dict,
    tax_table: list[dict],
) -> dict:
    """Calculate all federal payroll taxes for one pay period.

    Args:
        gross_pay: Gross pay for this pay period.
        pay_frequency: One of the PERIODS_PER_YEAR keys.
        w4_data: Dict with W-4 fields: filing_status, multiple_jobs,
            dependents_under_17_count, other_dependents_count,
            total_dependents_credit, other_income, deductions,
            extra_withholding_per_period, additional_income_from_other_jobs.
        ytd_gross: Year-to-date gross pay BEFORE this pay period.
        ytd_ss_withheld: Year-to-date Social Security tax already withheld
            (employee share).
        fica_config: Dict with: social_security_rate_employee,
            social_security_rate_employer, social_security_wage_base,
            medicare_rate_employee, medicare_rate_employer,
            additional_medicare_threshold, additional_medicare_rate,
            futa_rate, futa_wage_base, futa_state_credit_max.
        tax_table: List of bracket dicts for the matching filing_status and
            payroll_period, sorted by bracket_floor ascending. Each has:
            bracket_floor, bracket_ceiling (None for top), base_tax,
            marginal_rate.

    Returns:
        Dict with all tax amounts and a computation_detail breakdown.
    """
    periods = PERIODS_PER_YEAR.get(pay_frequency, 1)
    detail = {"pay_frequency": pay_frequency, "periods_per_year": periods}

    # ── Federal Income Tax (Percentage Method, 2020+ W-4) ─────────────
    fit, fit_detail = _calc_federal_income_tax(
        gross_pay, periods, w4_data, tax_table,
    )
    detail["federal_income_tax"] = fit_detail

    # ── FICA ──────────────────────────────────────────────────────────
    ss_employee, ss_employer, ss_detail = _calc_social_security(
        gross_pay, ytd_gross, ytd_ss_withheld, fica_config, periods,
    )
    detail["social_security"] = ss_detail

    med_employee, med_employer, addl_med, med_detail = _calc_medicare(
        gross_pay, ytd_gross, fica_config,
    )
    detail["medicare"] = med_detail

    futa, futa_detail = _calc_futa(gross_pay, ytd_gross, fica_config)
    detail["futa"] = futa_detail

    total_employee = fit + ss_employee + med_employee + addl_med
    total_employer = ss_employer + med_employer + futa

    effective_rate = 0.0
    if gross_pay > 0:
        effective_rate = round(total_employee / gross_pay * 100, 4)

    return {
        "federal_income_tax": round(fit, 2),
        "social_security_employee": round(ss_employee, 2),
        "social_security_employer": round(ss_employer, 2),
        "medicare_employee": round(med_employee, 2),
        "medicare_employer": round(med_employer, 2),
        "additional_medicare": round(addl_med, 2),
        "futa_employer": round(futa, 2),
        "total_employee_tax": round(total_employee, 2),
        "total_employer_tax": round(total_employer, 2),
        "effective_rate": effective_rate,
        "computation_detail": detail,
    }


def _calc_federal_income_tax(
    gross_pay: float,
    periods: int,
    w4_data: dict,
    tax_table: list[dict],
) -> tuple[float, dict]:
    """The percentage method for a 2020+ W-4."""
    detail: dict = {}

    # Step 1: Annualize gross pay
    annual_gross = gross_pay * periods
    detail["annual_gross"] = round(annual_gross, 2)

    # Step 2: Subtract Step 4(b) deductions
    deductions = float(w4_data.get("deductions") or 0)
    adjusted = max(annual_gross - deductions, 0)
    detail["step_4b_deductions"] = round(deductions, 2)
    detail["adjusted_annual_income"] = round(adjusted, 2)

    # Step 2 multiple-jobs: add other-jobs income
    other_jobs = float(w4_data.get("additional_income_from_other_jobs") or 0)
    if other_jobs > 0:
        adjusted += other_jobs
        detail["step_2_other_jobs_income"] = round(other_jobs, 2)
        detail["adjusted_with_other_jobs"] = round(adjusted, 2)

    # Step 4(a): Add other income
    other_income = float(w4_data.get("other_income") or 0)
    if other_income > 0:
        adjusted += other_income
        detail["step_4a_other_income"] = round(other_income, 2)
        detail["adjusted_with_other_income"] = round(adjusted, 2)

    # Step 3: Look up tentative withholding from tax table
    tentative, bracket_detail = _apply_tax_table(adjusted, tax_table)
    detail["tentative_annual_tax"] = round(tentative, 2)
    detail["bracket_applied"] = bracket_detail

    # Step 4: Subtract dependents credit
    dependents_credit = float(w4_data.get("total_dependents_credit") or 0)
    after_credits = max(tentative - dependents_credit, 0)
    detail["dependents_credit"] = round(dependents_credit, 2)
    detail["after_credits"] = round(after_credits, 2)

    # Convert back to per-period
    per_period = after_credits / periods
    detail["per_period_before_extra"] = round(per_period, 2)

    # Step 5: Add Step 4(c) extra withholding
    extra = float(w4_data.get("extra_withholding_per_period") or 0)
    per_period += extra
    detail["extra_withholding"] = round(extra, 2)

    result = max(per_period, 0)
    detail["final_per_period"] = round(result, 2)

    return result, detail


def _apply_tax_table(annual_income: float, brackets: list[dict]) -> tuple[float, dict]:
    """Walk the brackets, return the tax and the bracket that applied."""
    if not brackets:
        return 0.0, {"error": "no brackets available"}

    sorted_brackets = sorted(brackets, key=lambda b: float(b.get("bracket_floor", 0)))

    applied = sorted_brackets[0]
    for bracket in sorted_brackets:
        floor = float(bracket.get("bracket_floor", 0))
        if annual_income >= floor:
            applied = bracket
        else:
            break

    floor = float(applied.get("bracket_floor", 0))
    base = float(applied.get("base_tax", 0))
    rate = float(applied.get("marginal_rate", 0)) / 100.0
    ceiling = applied.get("bracket_ceiling")

    taxable_in_bracket = annual_income - floor
    if ceiling is not None and ceiling:
        taxable_in_bracket = min(taxable_in_bracket, float(ceiling) - floor)
    taxable_in_bracket = max(taxable_in_bracket, 0)

    tax = base + taxable_in_bracket * rate

    return tax, {
        "bracket_floor": floor,
        "bracket_ceiling": ceiling,
        "base_tax": base,
        "marginal_rate": rate * 100,
        "taxable_in_bracket": round(taxable_in_bracket, 2),
        "computed_tax": round(tax, 2),
    }


def _calc_social_security(
    gross_pay: float,
    ytd_gross: float,
    ytd_ss_withheld: float,
    config: dict,
    periods: int,
) -> tuple[float, float, dict]:
    """SS tax: 6.2% on gross up to the wage base, checking YTD."""
    wage_base = float(config.get("social_security_wage_base", 176100))
    ee_rate = float(config.get("social_security_rate_employee", 6.2)) / 100.0
    er_rate = float(config.get("social_security_rate_employer", 6.2)) / 100.0

    remaining_base = max(wage_base - ytd_gross, 0)
    taxable = min(gross_pay, remaining_base)

    ee = taxable * ee_rate
    er = taxable * er_rate

    return ee, er, {
        "wage_base": wage_base,
        "ytd_gross": ytd_gross,
        "remaining_base": round(remaining_base, 2),
        "taxable_this_period": round(taxable, 2),
        "employee_rate": ee_rate * 100,
        "employer_rate": er_rate * 100,
        "employee_tax": round(ee, 2),
        "employer_tax": round(er, 2),
    }


def _calc_medicare(
    gross_pay: float,
    ytd_gross: float,
    config: dict,
) -> tuple[float, float, float, dict]:
    """Medicare: 1.45% on all gross + additional 0.9% over $200k YTD threshold."""
    ee_rate = float(config.get("medicare_rate_employee", 1.45)) / 100.0
    er_rate = float(config.get("medicare_rate_employer", 1.45)) / 100.0
    addl_threshold = float(config.get("additional_medicare_threshold", 200000))
    addl_rate = float(config.get("additional_medicare_rate", 0.9)) / 100.0

    ee = gross_pay * ee_rate
    er = gross_pay * er_rate

    # Additional Medicare: employee-only, on gross over $200k YTD
    addl = 0.0
    new_ytd = ytd_gross + gross_pay
    if new_ytd > addl_threshold:
        addl_base = new_ytd - max(ytd_gross, addl_threshold)
        addl_base = max(addl_base, 0)
        addl = addl_base * addl_rate

    return ee, er, addl, {
        "employee_rate": ee_rate * 100,
        "employer_rate": er_rate * 100,
        "employee_tax": round(ee, 2),
        "employer_tax": round(er, 2),
        "additional_medicare_threshold": addl_threshold,
        "additional_medicare_rate": addl_rate * 100,
        "ytd_gross_before": ytd_gross,
        "ytd_gross_after": new_ytd,
        "additional_medicare_base": round(new_ytd - max(ytd_gross, addl_threshold), 2) if new_ytd > addl_threshold else 0,
        "additional_medicare_tax": round(addl, 2),
    }


def _calc_futa(
    gross_pay: float,
    ytd_gross: float,
    config: dict,
) -> tuple[float, dict]:
    """FUTA: employer-only, 6.0% on first $7k per employee minus state credit."""
    futa_rate = float(config.get("futa_rate", 6.0)) / 100.0
    futa_wage_base = float(config.get("futa_wage_base", 7000))
    state_credit = float(config.get("futa_state_credit_max", 5.4)) / 100.0
    effective_rate = max(futa_rate - state_credit, 0)

    remaining = max(futa_wage_base - ytd_gross, 0)
    taxable = min(gross_pay, remaining)

    tax = taxable * effective_rate

    return tax, {
        "futa_rate": futa_rate * 100,
        "state_credit": state_credit * 100,
        "effective_rate": effective_rate * 100,
        "wage_base": futa_wage_base,
        "ytd_gross": ytd_gross,
        "remaining_base": round(remaining, 2),
        "taxable_this_period": round(taxable, 2),
        "futa_tax": round(tax, 2),
    }


# ── 2025 Federal Tax Brackets (IRS Publication 15-T, Percentage Method) ───
# Annual brackets for the three filing statuses. These are the tentative
# withholding amounts from Table 2 of Pub 15-T.
#
# NOTE: Using 2025 values. The IRS publishes updated tables annually.
# When 2026 tables are published, add a second year to this seed.

SEED_TAX_YEAR = 2025

ANNUAL_BRACKETS = {
    "Single": [
        {"bracket_floor": 0, "bracket_ceiling": 6000, "base_tax": 0, "marginal_rate": 0},
        {"bracket_floor": 6000, "bracket_ceiling": 17600, "base_tax": 0, "marginal_rate": 10},
        {"bracket_floor": 17600, "bracket_ceiling": 53150, "base_tax": 1160, "marginal_rate": 12},
        {"bracket_floor": 53150, "bracket_ceiling": 106525, "base_tax": 5426, "marginal_rate": 22},
        {"bracket_floor": 106525, "bracket_ceiling": 197950, "base_tax": 17168.50, "marginal_rate": 24},
        {"bracket_floor": 197950, "bracket_ceiling": 252500, "base_tax": 39110.50, "marginal_rate": 32},
        {"bracket_floor": 252500, "bracket_ceiling": 631900, "base_tax": 56576.50, "marginal_rate": 35},
        {"bracket_floor": 631900, "bracket_ceiling": None, "base_tax": 189365.50, "marginal_rate": 37},
    ],
    "Married Filing Jointly": [
        {"bracket_floor": 0, "bracket_ceiling": 16500, "base_tax": 0, "marginal_rate": 0},
        {"bracket_floor": 16500, "bracket_ceiling": 39700, "base_tax": 0, "marginal_rate": 10},
        {"bracket_floor": 39700, "bracket_ceiling": 110800, "base_tax": 2320, "marginal_rate": 12},
        {"bracket_floor": 110800, "bracket_ceiling": 217550, "base_tax": 10852, "marginal_rate": 22},
        {"bracket_floor": 217550, "bracket_ceiling": 400400, "base_tax": 34337, "marginal_rate": 24},
        {"bracket_floor": 400400, "bracket_ceiling": 509500, "base_tax": 78221, "marginal_rate": 32},
        {"bracket_floor": 509500, "bracket_ceiling": 768200, "base_tax": 113133, "marginal_rate": 35},
        {"bracket_floor": 768200, "bracket_ceiling": None, "base_tax": 203678, "marginal_rate": 37},
    ],
    "Head of Household": [
        {"bracket_floor": 0, "bracket_ceiling": 13000, "base_tax": 0, "marginal_rate": 0},
        {"bracket_floor": 13000, "bracket_ceiling": 26500, "base_tax": 0, "marginal_rate": 10},
        {"bracket_floor": 26500, "bracket_ceiling": 68400, "base_tax": 1350, "marginal_rate": 12},
        {"bracket_floor": 68400, "bracket_ceiling": 109500, "base_tax": 6378, "marginal_rate": 22},
        {"bracket_floor": 109500, "bracket_ceiling": 200950, "base_tax": 15420, "marginal_rate": 24},
        {"bracket_floor": 200950, "bracket_ceiling": 252500, "base_tax": 37388, "marginal_rate": 32},
        {"bracket_floor": 252500, "bracket_ceiling": 631900, "base_tax": 53884, "marginal_rate": 35},
        {"bracket_floor": 631900, "bracket_ceiling": None, "base_tax": 186674, "marginal_rate": 37},
    ],
}

# Mapping from W-4 filing status to tax table filing status
FILING_STATUS_MAP = {
    "Single or Married Filing Separately": "Single",
    "Married Filing Jointly": "Married Filing Jointly",
    "Head of Household": "Head of Household",
}


def seed_brackets() -> list[dict]:
    """Return the full set of seed brackets for all periods, ready for insertion."""
    rows = []
    for filing_status, annual in ANNUAL_BRACKETS.items():
        for period_name, periods in PERIODS_PER_YEAR.items():
            for bracket in annual:
                floor = bracket["bracket_floor"] / periods
                ceiling = bracket["bracket_ceiling"] / periods if bracket["bracket_ceiling"] else None
                base = bracket["base_tax"] / periods
                rows.append({
                    "tax_year": SEED_TAX_YEAR,
                    "filing_status": filing_status,
                    "payroll_period": period_name,
                    "bracket_floor": round(floor, 2),
                    "bracket_ceiling": round(ceiling, 2) if ceiling else None,
                    "base_tax": round(base, 2),
                    "marginal_rate": bracket["marginal_rate"],
                })
    return rows
