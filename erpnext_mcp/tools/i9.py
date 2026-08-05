# SPDX-License-Identifier: MIT
"""Structured I-9 workflow: create, fill, verify, track, destroy.

v0.27.0. Replaces the opaque file attachment that `onboard_employee` used to
make with a structured record carrying Section 1, Section 2, retention dates,
and an immutable audit trail.

EVERY MUTATING ACTION WRITES AN I-9 AUDIT LOG ROW. The log is append-only —
its controller refuses updates — so the trail survives a form edit and answers
"who touched this I-9, when, and from where" without relying on Version history
that somebody with System Manager can amend.

SSN: ONLY THE LAST FOUR DIGITS ARE EVER STORED. `submit_i9_section_1` strips
to the last four before writing, and the I-9 Form controller does the same on
every save, so a full SSN that somehow arrives in a JSON payload is reduced
before it touches the database.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import frappe
from frappe.utils import getdate

from ..args import as_bool, as_date, as_int, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

I9_FORM = "I-9 Form"
I9_AUDIT_LOG = "I-9 Audit Log"
I9_SETTINGS = "I-9 Settings"
I9_DOCUMENT_TYPE = "I-9 Document Type"
EMPLOYEE = "Employee"


def _log_action(i9_form: str, employee: str, action: str, details: dict | None = None) -> None:
    """Write one immutable I-9 Audit Log row. Best effort, never fatal."""
    try:
        doc = frappe.get_doc(
            {
                "doctype": I9_AUDIT_LOG,
                "i9_form": i9_form,
                "employee": employee,
                "timestamp": frappe.utils.now(),
                "user": frappe.session.user if hasattr(frappe, "session") else "Administrator",
                "ip_address": frappe.local.request.remote_addr
                if hasattr(frappe, "local") and hasattr(frappe.local, "request") and frappe.local.request
                else "",
                "action": action,
                "details": json.dumps(details or {}, default=str),
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert()
    except Exception:
        pass


def _resolve_employee(args: dict) -> str:
    """Accept employee by docname, name, or employee_name."""
    emp = as_str(args, "employee") or as_str(args, "name") or as_str(args, "employee_name")
    if not emp:
        raise ToolError("employee is required.")
    if frappe.db.exists(EMPLOYEE, emp):
        return emp
    found = frappe.db.get_value(EMPLOYEE, {"employee_name": emp}, "name")
    if found:
        return str(found)
    raise ToolError(f"no Employee called {emp!r} on this site.")


def _i9_fields() -> list[str]:
    """The fields returned by get_i9_form."""
    return [
        "name", "employee", "employee_name", "company", "status", "hire_date",
        "legal_first_name", "legal_middle_name", "legal_last_name", "other_last_names",
        "address_street", "address_city", "address_state", "address_zip",
        "date_of_birth", "ssn_last_four", "email", "phone",
        "citizenship_status", "alien_registration_number", "alien_work_authorization_expiry",
        "section_1_signed_at", "section_1_signed_ip", "preparer_used",
        "preparer_name", "preparer_address",
        "document_path",
        "list_a_doc_title", "list_a_doc_authority", "list_a_doc_number", "list_a_doc_expiry",
        "list_b_doc_title", "list_b_doc_authority", "list_b_doc_number", "list_b_doc_expiry",
        "list_c_doc_title", "list_c_doc_authority", "list_c_doc_number", "list_c_doc_expiry",
        "document_copies_stored", "verifier_name", "verifier_title",
        "section_2_signed_at", "section_2_signed_ip", "verification_date",
        "retention_until", "destruction_eligible_date", "destroyed_at",
    ]


# ── read-only tools ────────────────────────────────────────────────────────


def get_i9_settings(args: dict) -> ToolResult:
    """Current I-9 configuration."""
    try:
        doc = frappe.get_doc(I9_SETTINGS)
    except Exception:
        return ToolResult(
            data={"note": "I-9 Settings does not exist yet. Run bench migrate."},
            summary="I-9 Settings not found",
        )
    data = {
        "store_document_copies": bool(int(doc.store_document_copies or 0)),
        "enrolled_in_e_verify": bool(int(doc.enrolled_in_e_verify or 0)),
        "business_legal_name": doc.business_legal_name or "",
        "business_address": doc.business_address or "",
        "business_ein": doc.business_ein or "",
        "reminder_days_before_doc_expiration": doc.reminder_days_before_doc_expiration or 90,
        "reminder_days_before_destruction": doc.reminder_days_before_destruction or 60,
    }
    return ToolResult(data=data, summary="I-9 settings returned")


def get_i9_form(args: dict) -> ToolResult:
    """Full I-9 record for one employee."""
    employee = _resolve_employee(args)
    name = frappe.db.get_value(I9_FORM, {"employee": employee}, "name")
    if not name:
        raise ToolError(f"no I-9 Form for employee {employee!r}.")
    row = frappe.db.get_value(I9_FORM, name, _i9_fields(), as_dict=True)
    data = {k: (str(v) if v is not None else None) for k, v in row.items()}
    _log_action(name, employee, "Viewed")
    return ToolResult(data=data, summary=f"I-9 for {employee}: {row.get('status')}")


def list_i9_forms(args: dict) -> ToolResult:
    """All I-9 forms with filtering."""
    filters = {}
    company = as_str(args, "company")
    if company:
        filters["company"] = resolve_company(company)
    status = as_str(args, "status")
    if status:
        filters["status"] = status
    limit = as_int(args, "limit", 100)
    if limit and limit > 500:
        limit = 500

    rows = frappe.db.get_all(
        I9_FORM,
        filters=filters,
        fields=["name", "employee", "employee_name", "company", "status", "hire_date",
                "retention_until", "destruction_eligible_date"],
        limit_page_length=limit,
        order_by="modified desc",
    )
    data = {"forms": [dict(r) for r in rows], "count": len(rows)}
    return ToolResult(data=data, summary=f"{len(rows)} I-9 form(s)")


def list_pending_i9_verifications(args: dict) -> ToolResult:
    """I-9 forms awaiting employer verification (Section 2)."""
    filters = {"status": ["in", ["Section 1 Complete", "Awaiting Verification"]]}
    company = as_str(args, "company")
    if company:
        filters["company"] = resolve_company(company)

    rows = frappe.db.get_all(
        I9_FORM,
        filters=filters,
        fields=["name", "employee", "employee_name", "company", "status", "hire_date"],
        order_by="hire_date asc",
    )
    for row in rows:
        if row.get("hire_date"):
            hire = getdate(row["hire_date"])
            days_since = (date.today() - hire).days
            row["days_since_hire"] = days_since
            row["overdue"] = days_since > 3

    data = {"pending": [dict(r) for r in rows], "count": len(rows)}
    return ToolResult(data=data, summary=f"{len(rows)} I-9(s) pending verification")


def get_i9_audit_log(args: dict) -> ToolResult:
    """Audit trail for one employee's I-9."""
    employee = _resolve_employee(args)
    limit = as_int(args, "limit", 100)
    if limit and limit > 500:
        limit = 500
    rows = frappe.db.get_all(
        I9_AUDIT_LOG,
        filters={"employee": employee},
        fields=["name", "i9_form", "timestamp", "user", "ip_address", "action", "details"],
        limit_page_length=limit,
        order_by="timestamp desc",
    )
    data = {"entries": [dict(r) for r in rows], "count": len(rows)}
    return ToolResult(data=data, summary=f"{len(rows)} audit log entries for {employee}")


def list_i9_document_types(args: dict) -> ToolResult:
    """Accepted documents by list category."""
    filters = {"enabled": 1}
    cat = as_str(args, "list_category")
    if cat:
        filters["list_category"] = cat
    rows = frappe.db.get_all(
        I9_DOCUMENT_TYPE,
        filters=filters,
        fields=["doc_title", "list_category", "uscis_code", "description", "requires_photo"],
        order_by="list_category asc, doc_title asc",
    )
    data = {"documents": [dict(r) for r in rows], "count": len(rows)}
    return ToolResult(data=data, summary=f"{len(rows)} I-9 document type(s)")


def get_i9_retention_report(args: dict) -> ToolResult:
    """I-9 forms approaching or past their retention date."""
    company = as_str(args, "company")
    filters = {"status": ["not in", ["Destroyed"]]}
    if company:
        filters["company"] = resolve_company(company)

    rows = frappe.db.get_all(
        I9_FORM,
        filters=filters,
        fields=["name", "employee", "employee_name", "company", "status", "hire_date",
                "retention_until", "destruction_eligible_date"],
        order_by="retention_until asc",
    )
    today = date.today()
    approaching = []
    eligible = []
    for row in rows:
        r = dict(row)
        if r.get("retention_until"):
            ret = getdate(r["retention_until"])
            r["days_until_retention"] = (ret - today).days
            if r["days_until_retention"] <= 0:
                eligible.append(r)
            elif r["days_until_retention"] <= 90:
                approaching.append(r)

    data = {
        "approaching_retention": approaching,
        "eligible_for_destruction": eligible,
        "approaching_count": len(approaching),
        "eligible_count": len(eligible),
    }
    return ToolResult(
        data=data,
        summary=f"{len(approaching)} approaching retention, {len(eligible)} eligible for destruction",
    )


def list_expiring_work_authorizations(args: dict) -> ToolResult:
    """Employees whose work authorization expires within N days."""
    company = as_str(args, "company")
    days_ahead = as_int(args, "days_ahead", 90) or 90
    filters = {
        "status": ["not in", ["Destroyed", "Expired"]],
        "citizenship_status": ["in", ["Alien Authorized to Work", "Lawful Permanent Resident"]],
        "alien_work_authorization_expiry": ["is", "set"],
    }
    if company:
        filters["company"] = resolve_company(company)

    rows = frappe.db.get_all(
        I9_FORM,
        filters=filters,
        fields=["name", "employee", "employee_name", "company",
                "citizenship_status", "alien_work_authorization_expiry"],
        order_by="alien_work_authorization_expiry asc",
    )
    today = date.today()
    cutoff = today + timedelta(days=days_ahead)
    expiring = []
    for row in rows:
        r = dict(row)
        exp = getdate(r["alien_work_authorization_expiry"])
        if exp <= cutoff:
            r["days_until_expiry"] = (exp - today).days
            expiring.append(r)

    data = {"expiring": expiring, "count": len(expiring), "days_ahead": days_ahead}
    return ToolResult(data=data, summary=f"{len(expiring)} work authorization(s) expiring within {days_ahead} days")


# ── mutating tools ─────────────────────────────────────────────────────────


def create_i9_form(args: dict) -> ToolResult:
    """Create a Draft I-9 Form for an employee."""
    employee = _resolve_employee(args)
    company = resolve_company(as_str(args, "company"), required=True)
    hire_date = as_date(args, "hire_date", required=True)

    existing = frappe.db.get_value(I9_FORM, {"employee": employee, "status": ["!=", "Destroyed"]}, "name")
    if existing:
        raise ToolError(
            f"employee {employee!r} already has an active I-9 Form ({existing}). "
            "Destroy the existing one before creating a new one, or use the existing form."
        )

    doc = frappe.get_doc(
        {
            "doctype": I9_FORM,
            "employee": employee,
            "company": company,
            "hire_date": hire_date,
            "status": "Draft",
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert()

    _log_action(doc.name, employee, "Created", {"hire_date": str(hire_date)})

    return ToolResult(
        data={"name": doc.name, "employee": employee, "status": "Draft"},
        summary=f"created Draft I-9 for {employee}",
        docstatus_delta="none → Draft",
    )


def _business_days_between(start: date, end: date) -> int:
    """Count business days between two dates (inclusive of both)."""
    if end < start:
        start, end = end, start
    count = 0
    current = start
    while current <= end:
        if current.weekday() < 5:
            count += 1
        current += timedelta(days=1)
    return count


def submit_i9_section_1(args: dict) -> ToolResult:
    """Fill Section 1 of an I-9 Form (employee information)."""
    employee = _resolve_employee(args)
    i9_name = frappe.db.get_value(I9_FORM, {"employee": employee, "status": "Draft"}, "name")
    if not i9_name:
        raise ToolError(
            f"no Draft I-9 Form for employee {employee!r}. Create one first with create_i9_form."
        )

    doc = frappe.get_doc(I9_FORM, i9_name)

    doc.legal_first_name = as_str(args, "legal_first_name", required=True)
    doc.legal_last_name = as_str(args, "legal_last_name", required=True)
    doc.legal_middle_name = as_str(args, "legal_middle_name")
    doc.other_last_names = as_str(args, "other_last_names")
    doc.address_street = as_str(args, "address_street")
    doc.address_city = as_str(args, "address_city")
    doc.address_state = as_str(args, "address_state")
    doc.address_zip = as_str(args, "address_zip")
    doc.date_of_birth = as_date(args, "date_of_birth")
    doc.email = as_str(args, "email")
    doc.phone = as_str(args, "phone")
    doc.citizenship_status = as_str(args, "citizenship_status", required=True)

    ssn = as_str(args, "ssn_last_four")
    if ssn:
        digits = "".join(c for c in ssn if c.isdigit())
        doc.ssn_last_four = digits[-4:] if len(digits) >= 4 else digits

    if doc.citizenship_status in ("Lawful Permanent Resident", "Alien Authorized to Work"):
        doc.alien_registration_number = as_str(args, "alien_registration_number")
    if doc.citizenship_status == "Alien Authorized to Work":
        doc.alien_work_authorization_expiry = as_date(args, "alien_work_authorization_expiry")

    sig = as_str(args, "section_1_signature")
    if sig:
        doc.section_1_signature = sig
    doc.section_1_signed_at = frappe.utils.now()
    doc.section_1_signed_ip = (
        frappe.local.request.remote_addr
        if hasattr(frappe, "local") and hasattr(frappe.local, "request") and frappe.local.request
        else ""
    )

    doc.preparer_used = as_bool(args, "preparer_used", False)
    if doc.preparer_used:
        doc.preparer_name = as_str(args, "preparer_name")
        doc.preparer_address = as_str(args, "preparer_address")
        doc.preparer_signature = as_str(args, "preparer_signature")

    doc.status = "Section 1 Complete"
    doc.flags.ignore_permissions = True
    doc.save()

    _log_action(doc.name, employee, "Section 1 Submitted", {"citizenship_status": doc.citizenship_status})

    return ToolResult(
        data={"name": doc.name, "employee": employee, "status": doc.status},
        summary=f"Section 1 submitted for {employee}",
        docstatus_delta="Draft → Section 1 Complete",
    )


def submit_i9_section_2(args: dict) -> ToolResult:
    """Fill Section 2 of an I-9 Form (employer verification)."""
    employee = _resolve_employee(args)
    i9_name = frappe.db.get_value(
        I9_FORM,
        {"employee": employee, "status": ["in", ["Section 1 Complete", "Awaiting Verification"]]},
        "name",
    )
    if not i9_name:
        raise ToolError(
            f"no I-9 Form in 'Section 1 Complete' or 'Awaiting Verification' status for {employee!r}. "
            "Section 1 must be completed first."
        )

    doc = frappe.get_doc(I9_FORM, i9_name)

    verification_date = as_date(args, "verification_date", required=True)
    hire = getdate(doc.hire_date)
    ver = getdate(verification_date)
    bdays = _business_days_between(hire, ver)
    if bdays > 4:
        raise ToolError(
            f"verification_date {verification_date} is {bdays - 1} business days after hire_date "
            f"{doc.hire_date}. Section 2 must be completed within 3 business days of the hire date."
        )

    doc.document_path = as_str(args, "document_path", required=True)
    if doc.document_path not in ("List A", "List B + C"):
        raise ToolError("document_path must be 'List A' or 'List B + C'.")

    if doc.document_path == "List A":
        doc.list_a_doc_title = as_str(args, "list_a_doc_title", required=True)
        doc.list_a_doc_authority = as_str(args, "list_a_doc_authority")
        doc.list_a_doc_number = as_str(args, "list_a_doc_number")
        doc.list_a_doc_expiry = as_date(args, "list_a_doc_expiry")
    else:
        doc.list_b_doc_title = as_str(args, "list_b_doc_title", required=True)
        doc.list_b_doc_authority = as_str(args, "list_b_doc_authority")
        doc.list_b_doc_number = as_str(args, "list_b_doc_number")
        doc.list_b_doc_expiry = as_date(args, "list_b_doc_expiry")
        doc.list_c_doc_title = as_str(args, "list_c_doc_title", required=True)
        doc.list_c_doc_authority = as_str(args, "list_c_doc_authority")
        doc.list_c_doc_number = as_str(args, "list_c_doc_number")
        doc.list_c_doc_expiry = as_date(args, "list_c_doc_expiry")

    doc.document_copies_stored = as_bool(args, "document_copies_stored", False)
    doc.verifier_name = as_str(args, "verifier_name", required=True)
    doc.verifier_title = as_str(args, "verifier_title")
    doc.verification_date = verification_date

    sig = as_str(args, "section_2_signature")
    if sig:
        doc.section_2_signature = sig
    doc.section_2_signed_at = frappe.utils.now()
    doc.section_2_signed_ip = (
        frappe.local.request.remote_addr
        if hasattr(frappe, "local") and hasattr(frappe.local, "request") and frappe.local.request
        else ""
    )

    doc.status = "Complete"
    doc.flags.ignore_permissions = True
    doc.save()

    _log_action(doc.name, employee, "Section 2 Signed", {
        "document_path": doc.document_path,
        "verifier_name": doc.verifier_name,
        "verification_date": str(verification_date),
    })

    return ToolResult(
        data={"name": doc.name, "employee": employee, "status": doc.status},
        summary=f"Section 2 completed for {employee} by {doc.verifier_name}",
        docstatus_delta="Section 1 Complete → Complete",
    )


def update_i9_settings(args: dict) -> ToolResult:
    """Update I-9 site settings."""
    try:
        doc = frappe.get_doc(I9_SETTINGS)
    except Exception:
        raise ToolError("I-9 Settings does not exist. Run bench migrate.")

    changed = []
    for field in ("store_document_copies", "enrolled_in_e_verify"):
        val = as_bool(args, field, None)
        if val is not None:
            setattr(doc, field, val)
            changed.append(field)

    for field in ("business_legal_name", "business_address", "business_ein"):
        val = as_str(args, field)
        if val:
            setattr(doc, field, val)
            changed.append(field)

    for field in ("reminder_days_before_doc_expiration", "reminder_days_before_destruction"):
        val = as_int(args, field)
        if val is not None:
            setattr(doc, field, val)
            changed.append(field)

    if not changed:
        raise ToolError("no fields to update. Pass at least one I-9 Settings field.")

    doc.flags.ignore_permissions = True
    doc.save()

    return ToolResult(
        data={"updated": changed},
        summary=f"I-9 settings updated: {', '.join(changed)}",
    )


def flag_i9_reverification(args: dict) -> ToolResult:
    """Move an I-9 to Reverification Needed when work auth expires."""
    employee = _resolve_employee(args)
    reason = as_str(args, "reason", required=True)
    i9_name = frappe.db.get_value(
        I9_FORM,
        {"employee": employee, "status": ["in", ["Complete", "Reverification Needed"]]},
        "name",
    )
    if not i9_name:
        raise ToolError(f"no Complete I-9 Form for {employee!r} to flag for reverification.")

    doc = frappe.get_doc(I9_FORM, i9_name)
    old_status = doc.status
    doc.status = "Reverification Needed"
    doc.flags.ignore_permissions = True
    doc.save()

    _log_action(doc.name, employee, "Reverification Flagged", {
        "reason": reason,
        "previous_status": old_status,
    })

    return ToolResult(
        data={"name": doc.name, "employee": employee, "status": doc.status, "reason": reason},
        summary=f"I-9 for {employee} flagged for reverification: {reason}",
        docstatus_delta=f"{old_status} → Reverification Needed",
    )


def destroy_i9(args: dict) -> ToolResult:
    """Mark an I-9 as Destroyed after retention period has passed."""
    employee = _resolve_employee(args)
    i9_name = frappe.db.get_value(
        I9_FORM,
        {"employee": employee, "status": ["!=", "Destroyed"]},
        "name",
    )
    if not i9_name:
        raise ToolError(f"no active I-9 Form for {employee!r} to destroy.")

    doc = frappe.get_doc(I9_FORM, i9_name)

    if doc.retention_until:
        ret = getdate(doc.retention_until)
        if date.today() < ret:
            raise ToolError(
                f"this I-9 must be retained until {doc.retention_until}. "
                f"It cannot be destroyed until that date has passed."
            )

    cert = as_str(args, "destruction_certificate")
    old_status = doc.status
    doc.status = "Destroyed"
    doc.destroyed_at = frappe.utils.now()
    if cert:
        doc.destruction_certificate = cert
    doc.flags.ignore_permissions = True
    doc.save()

    _log_action(doc.name, employee, "Destroyed", {
        "previous_status": old_status,
        "has_certificate": bool(cert),
    })

    return ToolResult(
        data={"name": doc.name, "employee": employee, "status": "Destroyed",
              "destroyed_at": str(doc.destroyed_at)},
        summary=f"I-9 for {employee} destroyed",
        docstatus_delta=f"{old_status} → Destroyed",
    )
