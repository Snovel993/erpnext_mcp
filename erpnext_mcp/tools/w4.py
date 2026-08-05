# SPDX-License-Identifier: MIT
"""W-4 / federal withholding tools.

v0.28.0. Structured W-4 processing and the withholding engine that reads it.

The W-4 superseding pattern: a new W-4 for the same employee + tax_year sets
the old one to Superseded with a superseded_by link. Only one Active W-4 per
employee per tax_year.
"""
from __future__ import annotations

import frappe
from frappe.utils import now

from ..args import as_int, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from ..withholding import (
    FILING_STATUS_MAP,
    PERIODS_PER_YEAR,
    SEED_TAX_YEAR,
    calculate_federal_withholding,
)

W4_FORM = "W-4 Form"
FEDERAL_TAX_TABLE = "Federal Tax Table"
FICA_CONFIG = "FICA Configuration"
EMPLOYEE = "Employee"


def _resolve_employee(args: dict) -> str:
    emp = as_str(args, "employee") or as_str(args, "name") or as_str(args, "employee_name")
    if not emp:
        raise ToolError("employee is required.")
    if frappe.db.exists(EMPLOYEE, emp):
        return emp
    found = frappe.db.get_value(EMPLOYEE, {"employee_name": emp}, "name")
    if found:
        return str(found)
    raise ToolError(f"no Employee called {emp!r} on this site.")


def _w4_fields() -> list[str]:
    return [
        "name", "employee", "employee_name", "company", "tax_year",
        "status", "effective_date", "superseded_by",
        "filing_status", "multiple_jobs",
        "additional_income_from_other_jobs",
        "dependents_under_17_count", "dependents_under_17_amount",
        "other_dependents_count", "other_dependents_amount",
        "total_dependents_credit",
        "other_income", "deductions", "extra_withholding_per_period",
        "signed_at", "signed_ip",
    ]


# ── read-only tools ──────────────────────────────────────────────────────


def get_w4(args: dict) -> ToolResult:
    """Current active W-4 for an employee."""
    employee = _resolve_employee(args)
    tax_year = as_int(args, "tax_year")

    filters = {"employee": employee, "status": "Active"}
    if tax_year:
        filters["tax_year"] = tax_year

    name = frappe.db.get_value(
        W4_FORM, filters, "name", order_by="tax_year desc, effective_date desc",
    )
    if not name:
        raise ToolError(f"no active W-4 Form for employee {employee!r}.")

    row = frappe.db.get_value(W4_FORM, name, _w4_fields(), as_dict=True)
    data = {k: (str(v) if v is not None else None) for k, v in row.items()}
    return ToolResult(data=data, summary=f"W-4 for {employee}: {row.get('filing_status')}, tax year {row.get('tax_year')}")


def list_w4_forms(args: dict) -> ToolResult:
    """All W-4 forms with filtering."""
    filters = {}
    company = as_str(args, "company")
    if company:
        filters["company"] = resolve_company(company)
    status = as_str(args, "status")
    if status:
        filters["status"] = status
    tax_year = as_int(args, "tax_year")
    if tax_year:
        filters["tax_year"] = tax_year
    limit = as_int(args, "limit", 100)
    if limit and limit > 500:
        limit = 500

    rows = frappe.db.get_all(
        W4_FORM,
        filters=filters,
        fields=["name", "employee", "employee_name", "company", "tax_year",
                "status", "effective_date", "filing_status"],
        limit_page_length=limit,
        order_by="modified desc",
    )
    data = {"forms": [dict(r) for r in rows], "count": len(rows)}
    return ToolResult(data=data, summary=f"{len(rows)} W-4 form(s)")


def get_fica_config(args: dict) -> ToolResult:
    """Current FICA rates."""
    try:
        doc = frappe.get_doc(FICA_CONFIG)
    except Exception:
        return ToolResult(
            data={"note": "FICA Configuration does not exist yet. Run bench migrate."},
            summary="FICA Configuration not found",
        )
    data = {
        "tax_year": int(doc.tax_year or 2025),
        "social_security_rate_employee": float(doc.social_security_rate_employee or 6.2),
        "social_security_rate_employer": float(doc.social_security_rate_employer or 6.2),
        "social_security_wage_base": float(doc.social_security_wage_base or 176100),
        "medicare_rate_employee": float(doc.medicare_rate_employee or 1.45),
        "medicare_rate_employer": float(doc.medicare_rate_employer or 1.45),
        "additional_medicare_threshold": float(doc.additional_medicare_threshold or 200000),
        "additional_medicare_rate": float(doc.additional_medicare_rate or 0.9),
        "futa_rate": float(doc.futa_rate or 6.0),
        "futa_wage_base": float(doc.futa_wage_base or 7000),
        "futa_state_credit_max": float(doc.futa_state_credit_max or 5.4),
    }
    return ToolResult(data=data, summary=f"FICA config for tax year {data['tax_year']}")


def get_federal_tax_table(args: dict) -> ToolResult:
    """Withholding brackets for a filing status and payroll period."""
    tax_year = as_int(args, "tax_year")
    if not tax_year:
        raise ToolError("tax_year is required.")
    filing_status = as_str(args, "filing_status", required=True)
    payroll_period = as_str(args, "payroll_period", required=True)

    rows = frappe.db.get_all(
        FEDERAL_TAX_TABLE,
        filters={
            "tax_year": tax_year,
            "filing_status": filing_status,
            "payroll_period": payroll_period,
        },
        fields=["bracket_floor", "bracket_ceiling", "base_tax", "marginal_rate"],
        order_by="bracket_floor asc",
    )
    data = {"brackets": [dict(r) for r in rows], "count": len(rows),
            "tax_year": tax_year, "filing_status": filing_status, "payroll_period": payroll_period}
    return ToolResult(data=data, summary=f"{len(rows)} bracket(s) for {filing_status}, {payroll_period}, {tax_year}")


def preview_federal_withholding(args: dict) -> ToolResult:
    """Dry-run withholding calculation showing exactly what would be withheld."""
    employee = _resolve_employee(args)
    gross_pay = as_float(args, "gross_pay", required=True)
    pay_frequency = as_str(args, "pay_frequency", required=True)
    if pay_frequency not in PERIODS_PER_YEAR:
        raise ToolError(f"pay_frequency must be one of: {', '.join(PERIODS_PER_YEAR)}.")

    w4_data, tax_year = _load_w4_data(employee, as_int(args, "tax_year"))
    fica = _load_fica_config()
    tax_table = _load_tax_table(tax_year, w4_data["filing_status"], pay_frequency)

    ytd_gross = as_float(args, "ytd_gross", 0.0)
    ytd_ss = as_float(args, "ytd_ss_withheld", 0.0)

    result = calculate_federal_withholding(
        gross_pay, pay_frequency, w4_data, ytd_gross, ytd_ss, fica, tax_table,
    )
    result["employee"] = employee
    result["tax_year"] = tax_year
    result["gross_pay"] = gross_pay
    result["pay_frequency"] = pay_frequency

    return ToolResult(
        data=result,
        summary=f"Withholding preview for {employee}: ${result['total_employee_tax']} employee, "
                f"${result['total_employer_tax']} employer on ${gross_pay} {pay_frequency}",
    )


def list_employees_missing_w4(args: dict) -> ToolResult:
    """Employees with no active W-4."""
    company = as_str(args, "company")
    emp_filters = {"status": "Active"}
    if company:
        emp_filters["company"] = resolve_company(company)

    employees = frappe.db.get_all(
        EMPLOYEE,
        filters=emp_filters,
        fields=["name", "employee_name", "company", "date_of_joining"],
    )

    missing = []
    for emp in employees:
        has_w4 = frappe.db.exists(W4_FORM, {"employee": emp["name"], "status": "Active"})
        if not has_w4:
            missing.append(dict(emp))

    data = {"employees": missing, "count": len(missing)}
    return ToolResult(data=data, summary=f"{len(missing)} active employee(s) without a W-4")


def calculate_payroll_taxes(args: dict) -> ToolResult:
    """Run the full calc engine and return the breakdown (read-only calc)."""
    employee = _resolve_employee(args)
    gross_pay = as_float(args, "gross_pay", required=True)
    pay_frequency = as_str(args, "pay_frequency", required=True)
    if pay_frequency not in PERIODS_PER_YEAR:
        raise ToolError(f"pay_frequency must be one of: {', '.join(PERIODS_PER_YEAR)}.")

    w4_data, tax_year = _load_w4_data(employee, as_int(args, "tax_year"))
    fica = _load_fica_config()
    tax_table = _load_tax_table(tax_year, w4_data["filing_status"], pay_frequency)

    ytd_gross = as_float(args, "ytd_gross", 0.0)
    ytd_ss = as_float(args, "ytd_ss_withheld", 0.0)

    result = calculate_federal_withholding(
        gross_pay, pay_frequency, w4_data, ytd_gross, ytd_ss, fica, tax_table,
    )
    result["employee"] = employee
    result["tax_year"] = tax_year

    return ToolResult(
        data=result,
        summary=f"Payroll taxes for {employee}: ${result['total_employee_tax']} employee, "
                f"${result['total_employer_tax']} employer",
    )


# ── mutating tools ───────────────────────────────────────────────────────


def submit_w4(args: dict) -> ToolResult:
    """Create a W-4 Form, superseding any prior active W-4 for same employee+tax_year."""
    employee = _resolve_employee(args)
    company = resolve_company(as_str(args, "company"), required=True)
    tax_year = as_int(args, "tax_year")
    if not tax_year:
        raise ToolError("tax_year is required.")
    filing_status = as_str(args, "filing_status", required=True)

    valid_statuses = ["Single or Married Filing Separately", "Married Filing Jointly", "Head of Household"]
    if filing_status not in valid_statuses:
        raise ToolError(f"filing_status must be one of: {', '.join(valid_statuses)}.")

    multiple_jobs = bool(args.get("multiple_jobs"))
    dependents_under_17 = as_int(args, "dependents_under_17_count", 0) or 0
    other_dependents = as_int(args, "other_dependents_count", 0) or 0
    other_income = as_float(args, "other_income", 0.0)
    deductions = as_float(args, "deductions", 0.0)
    extra_withholding = as_float(args, "extra_withholding_per_period", 0.0)
    additional_other_jobs = as_float(args, "additional_income_from_other_jobs", 0.0)

    # Supersede any existing active W-4 for this employee + tax_year
    existing = frappe.db.get_all(
        W4_FORM,
        filters={"employee": employee, "tax_year": tax_year, "status": "Active"},
        fields=["name"],
    )

    doc = frappe.get_doc({
        "doctype": W4_FORM,
        "employee": employee,
        "company": company,
        "tax_year": tax_year,
        "status": "Active",
        "effective_date": frappe.utils.today(),
        "filing_status": filing_status,
        "multiple_jobs": 1 if multiple_jobs else 0,
        "additional_income_from_other_jobs": additional_other_jobs,
        "dependents_under_17_count": dependents_under_17,
        "other_dependents_count": other_dependents,
        "other_income": other_income,
        "deductions": deductions,
        "extra_withholding_per_period": extra_withholding,
        "signed_at": now(),
        "signed_ip": (
            frappe.local.request.remote_addr
            if hasattr(frappe, "local") and hasattr(frappe.local, "request") and frappe.local.request
            else ""
        ),
    })
    doc.flags.ignore_permissions = True
    doc.insert()

    # Mark old ones as superseded
    for old in existing:
        frappe.db.set_value(W4_FORM, old["name"], {
            "status": "Superseded",
            "superseded_by": doc.name,
        })

    emp_name = frappe.db.get_value(EMPLOYEE, employee, "employee_name") or employee
    return ToolResult(
        data={
            "name": doc.name,
            "employee": employee,
            "employee_name": emp_name,
            "tax_year": tax_year,
            "filing_status": filing_status,
            "status": "Active",
            "superseded": [old["name"] for old in existing],
        },
        summary=f"W-4 submitted for {emp_name}: {filing_status}, tax year {tax_year}"
                + (f", superseded {len(existing)} prior W-4(s)" if existing else ""),
    )


def update_fica_config(args: dict) -> ToolResult:
    """Update FICA rates for a new tax year."""
    try:
        doc = frappe.get_doc(FICA_CONFIG)
    except Exception:
        raise ToolError("FICA Configuration does not exist yet. Run bench migrate.")

    fields = [
        "tax_year", "social_security_rate_employee", "social_security_rate_employer",
        "social_security_wage_base", "medicare_rate_employee", "medicare_rate_employer",
        "additional_medicare_threshold", "additional_medicare_rate",
        "futa_rate", "futa_wage_base", "futa_state_credit_max",
    ]
    updated = []
    for field in fields:
        val = args.get(field)
        if val is not None:
            if field == "tax_year":
                setattr(doc, field, int(val))
            else:
                setattr(doc, field, float(val))
            updated.append(field)

    if not updated:
        raise ToolError("no fields to update. Pass at least one FICA field.")

    doc.flags.ignore_permissions = True
    doc.save()

    return ToolResult(
        data={"updated_fields": updated, "tax_year": int(doc.tax_year or 0)},
        summary=f"FICA config updated: {', '.join(updated)}",
    )


def import_federal_tax_table(args: dict) -> ToolResult:
    """Bulk import withholding brackets for a new tax year."""
    tax_year = as_int(args, "tax_year")
    if not tax_year:
        raise ToolError("tax_year is required.")
    brackets = args.get("brackets")
    if not brackets:
        raise ToolError("brackets is required — a list of bracket objects.")

    created = 0
    for bracket in brackets:
        if not isinstance(bracket, dict):
            continue
        doc = frappe.get_doc({
            "doctype": FEDERAL_TAX_TABLE,
            "tax_year": tax_year,
            "filing_status": bracket.get("filing_status", ""),
            "payroll_period": bracket.get("payroll_period", ""),
            "bracket_floor": float(bracket.get("bracket_floor", 0)),
            "bracket_ceiling": float(bracket["bracket_ceiling"]) if bracket.get("bracket_ceiling") else None,
            "base_tax": float(bracket.get("base_tax", 0)),
            "marginal_rate": float(bracket.get("marginal_rate", 0)),
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        created += 1

    return ToolResult(
        data={"tax_year": tax_year, "created": created},
        summary=f"Imported {created} bracket(s) for tax year {tax_year}",
    )


# ── internal helpers ─────────────────────────────────────────────────────


def _load_w4_data(employee: str, tax_year: int | None = None) -> tuple[dict, int]:
    """Load the active W-4 for an employee and return (w4_data, tax_year)."""
    filters = {"employee": employee, "status": "Active"}
    if tax_year:
        filters["tax_year"] = tax_year

    name = frappe.db.get_value(
        W4_FORM, filters, "name", order_by="tax_year desc, effective_date desc",
    )
    if not name:
        raise ToolError(f"no active W-4 for employee {employee!r}. Submit one first.")

    row = frappe.db.get_value(W4_FORM, name, _w4_fields(), as_dict=True)
    filing_status = row.get("filing_status", "Single or Married Filing Separately")
    table_status = FILING_STATUS_MAP.get(filing_status, "Single")

    w4_data = {
        "filing_status": table_status,
        "multiple_jobs": bool(int(row.get("multiple_jobs") or 0)),
        "additional_income_from_other_jobs": float(row.get("additional_income_from_other_jobs") or 0),
        "dependents_under_17_count": int(row.get("dependents_under_17_count") or 0),
        "other_dependents_count": int(row.get("other_dependents_count") or 0),
        "total_dependents_credit": float(row.get("total_dependents_credit") or 0),
        "other_income": float(row.get("other_income") or 0),
        "deductions": float(row.get("deductions") or 0),
        "extra_withholding_per_period": float(row.get("extra_withholding_per_period") or 0),
    }
    return w4_data, int(row.get("tax_year") or SEED_TAX_YEAR)


def _load_fica_config() -> dict:
    """Load FICA configuration as a plain dict."""
    try:
        doc = frappe.get_doc(FICA_CONFIG)
    except Exception:
        raise ToolError("FICA Configuration does not exist. Run bench migrate.")

    return {
        "social_security_rate_employee": float(doc.social_security_rate_employee or 6.2),
        "social_security_rate_employer": float(doc.social_security_rate_employer or 6.2),
        "social_security_wage_base": float(doc.social_security_wage_base or 176100),
        "medicare_rate_employee": float(doc.medicare_rate_employee or 1.45),
        "medicare_rate_employer": float(doc.medicare_rate_employer or 1.45),
        "additional_medicare_threshold": float(doc.additional_medicare_threshold or 200000),
        "additional_medicare_rate": float(doc.additional_medicare_rate or 0.9),
        "futa_rate": float(doc.futa_rate or 6.0),
        "futa_wage_base": float(doc.futa_wage_base or 7000),
        "futa_state_credit_max": float(doc.futa_state_credit_max or 5.4),
    }


def _load_tax_table(tax_year: int, filing_status: str, payroll_period: str) -> list[dict]:
    """Load tax brackets from the database."""
    rows = frappe.db.get_all(
        FEDERAL_TAX_TABLE,
        filters={
            "tax_year": tax_year,
            "filing_status": filing_status,
            "payroll_period": payroll_period,
        },
        fields=["bracket_floor", "bracket_ceiling", "base_tax", "marginal_rate"],
        order_by="bracket_floor asc",
    )
    if not rows:
        raise ToolError(
            f"no Federal Tax Table brackets for {filing_status}, {payroll_period}, {tax_year}. "
            "Import brackets with import_federal_tax_table or run bench migrate to seed 2025 defaults."
        )
    return [dict(r) for r in rows]


def as_float(args: dict, key: str, default: float = 0.0, required: bool = False) -> float:
    """Coerce to float, with a clear error message."""
    val = args.get(key)
    if val is None or val == "":
        if required:
            raise ToolError(f"{key} is required.")
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        raise ToolError(f"{key} must be a number, got {val!r}.")
