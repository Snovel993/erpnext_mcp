# SPDX-License-Identifier: MIT
"""Structured I-9 workflow: create, fill, verify, reverify, track, destroy.

v0.27.0. Replaces the opaque file attachment that `onboard_employee` used to
make with a structured record carrying Section 1, Section 2, retention dates,
and an immutable audit trail.

EVERY MUTATING ACTION WRITES AN I-9 AUDIT LOG ROW. The log is append-only —
its controller refuses updates — so the trail survives a form edit and answers
"who touched this I-9, when, and from where" without relying on Version history
that somebody with System Manager can amend.

SSN: THE LAST FOUR DIGITS ARE ALWAYS WHAT THIS APP READS. `submit_i9_section_1`
strips to the last four before writing them, and the I-9 Form controller does the
same on every save, so a full SSN that arrives in a JSON payload is reduced
before it touches the `ssn_last_four` column. v0.47.0 added a SECOND column,
`ssn_full`, and it is off by default and stays off unless an operator switches
`store_full_ssn` on in I-9 Settings: E-Verify submits nine digits and cannot be
run from four, so a site that runs E-Verify needs somewhere to keep them and a
site that does not should not have them. It is a Frappe Password field, which
means Frappe writes it to the encrypted `__Auth` table rather than to a column;
NO TOOL IN THIS APP READS IT BACK, `get_i9_form` does not return it, and the
controller blanks it on every save while the switch is off.

────────────────────────────────────────────────────────────────────────────
v0.47.0: THE THREE FEDERAL GAPS
────────────────────────────────────────────────────────────────────────────

SECTION 1 ASKS FOR ONE OF THREE IDENTIFIERS, not for the A-number alone. An
Alien Authorized to Work gives a USCIS/A-Number, OR a Form I-94 admission
number, OR a foreign passport number WITH the country that issued it. Only the
first was storable, so the other two arrived at a form that had nowhere to put
them and were dropped. `submit_i9_section_1` now takes all three and refuses a
status of Alien Authorized to Work that carries none of them — that refusal is
Section 1's own rule, and a form filed without an answer to it is not filed.
A foreign passport number without a country is refused for the same reason: it
identifies nobody.

SECTION 2 CHECKS THE TITLE AGAINST THE LIST IT CLAIMS TO BE FROM. `i9_documents.py`
has seeded all 24 USCIS-accepted documents since v0.27.0 and Section 2 accepted
free text, so nothing stopped a List B document being recorded in the List A slot
— which is a form that says one document proved both identity and work
authorization when it proved neither. The check runs off the I-9 Document Type
table and only where that table has enabled rows for the category, so a site
that has deactivated a document gets its own answer and a site mid-migrate is
not locked out of filing an I-9.

A RECEIPT IS TEMPORARILY ACCEPTABLE AND THE FORM IS STILL COMPLETE. 8 CFR
274a.2(b)(1)(vi) lets an employee who has lost a document present a receipt for
the replacement and work while it is coming, for 90 days from the hire date.
The status therefore stays Complete — the person may work — and `receipt_pending`
with `receipt_expires_on` carries what is still owed. `list_pending_i9_verifications`
reports them, and `reverify_i9` is where the real document lands.

SECTION 3 IS A CHILD TABLE, NOT A SECOND SET OF COLUMNS. `reverify_i9` appends
an `I-9 Reverification` row and never touches Section 2's — what was examined on
the day of hire is the record §1324a asks the employer to have kept, and a
seasonal worker on a renewing authorization is reverified once a season for as
long as they keep coming back. Reverifying an expiring authorization moves
`alien_work_authorization_expiry` forward to the new document's date, so
`list_expiring_work_authorizations` follows the document currently in force
rather than the one it replaced — and moves `Employee.i9_status` off `Expired`,
which is the ONLY write this app makes to that column and the only one it should.
`_clear_expired_i9_column` carries that argument in full.
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
I9_REVERIFICATION = "I-9 Reverification"
EMPLOYEE = "Employee"

#: How long a receipt for a lost, stolen or damaged document stands in for the
#: document itself. Read off the I-9 Form controller rather than restated, so the
#: tool and the doctype cannot come to disagree about a statutory deadline.
RECEIPT_VALID_DAYS = 90

#: The statuses a Section 3 entry may be written against. A Draft or a form whose
#: Section 2 was never signed has nothing to reverify — there is no first
#: verification for a second one to follow.
REVERIFIABLE_STATUSES = ("Complete", "Reverification Needed", "Expired")

#: What `reverify_i9` will accept as a reason, mirroring the child doctype's own
#: Select. Kept here as well because the refusal has to name them, and a refusal
#: that says "invalid reason" without saying which ones are valid is a support
#: ticket.
REVERIFICATION_REASONS = ("Work Authorization Expired", "Rehire", "Receipt Replaced", "Name Change")

#: The three identifiers Section 1 will take from an Alien Authorized to Work,
#: any ONE of which answers the question. USCIS calls them exactly this.
ALIEN_IDENTIFIERS = ("alien_registration_number", "i94_admission_number", "foreign_passport_number")


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
    """The fields returned by get_i9_form.

    `ssn_full` IS NOT IN THIS LIST AND MUST NOT BE ADDED TO IT. It is a Frappe
    Password field, so reading it needs `get_decrypted_password` and would not
    come back through `get_value` anyway — but the reason it is absent is the
    policy rather than the mechanism: nothing in this app reads the full number
    back, and the day something needs to (an E-Verify submission) it should say
    so at its own call site rather than inherit it from a general-purpose read.
    """
    return [
        "name", "employee", "employee_name", "company", "status", "hire_date",
        "legal_first_name", "legal_middle_name", "legal_last_name", "other_last_names",
        "address_street", "address_city", "address_state", "address_zip",
        "date_of_birth", "ssn_last_four", "email", "phone",
        "citizenship_status", "alien_registration_number", "i94_admission_number",
        "foreign_passport_number", "foreign_passport_country",
        "alien_work_authorization_expiry",
        "section_1_signed_at", "section_1_signed_ip", "preparer_used",
        "preparer_name", "preparer_address",
        "document_path",
        "list_a_doc_title", "list_a_doc_authority", "list_a_doc_number", "list_a_doc_expiry",
        "list_a_is_receipt",
        "list_b_doc_title", "list_b_doc_authority", "list_b_doc_number", "list_b_doc_expiry",
        "list_b_is_receipt",
        "list_c_doc_title", "list_c_doc_authority", "list_c_doc_number", "list_c_doc_expiry",
        "list_c_is_receipt",
        "receipt_pending", "receipt_expires_on",
        "document_copies_stored", "verifier_name", "verifier_title",
        "section_2_signed_at", "section_2_signed_ip", "verification_date",
        "retention_until", "destruction_eligible_date", "destroyed_at",
    ]


#: What one Section 3 row reports. The child rows come back on every
#: `get_i9_form` because a reverification history nobody can read is a history
#: that gets collected twice.
REVERIFICATION_FIELDS = (
    "reverification_date", "reason", "rehire_date",
    "document_title", "issuing_authority", "document_number", "document_expiry",
    "verifier_name", "verifier_title", "signed_at", "signed_ip", "notes",
)


def _document_titles(category: str) -> list[str]:
    """The enabled I-9 Document Type titles for one list category.

    An EMPTY LIST MEANS "do not check", and that is the deliberate reading rather
    than "nothing is acceptable". The table is seeded by `i9_documents.py` on
    every migrate, so it is empty in exactly two situations: a site between
    installing this version and running `bench migrate`, and a site where an
    operator has deactivated every document in a category. Refusing every I-9 on
    a site mid-migrate would make an upgrade a compliance outage, and this app's
    standing promise is that installing it cannot break a site.
    """
    try:
        rows = frappe.db.get_all(
            I9_DOCUMENT_TYPE,
            filters={"enabled": 1, "list_category": category},
            fields=["doc_title"],
        )
    except Exception:
        return []
    return [str(r["doc_title"]) for r in rows if r.get("doc_title")]


def _check_document_title(title: str, category: str, label: str) -> str:
    """The title, as the I-9 Document Type table spells it, or the refusal.

    MATCHED CASE-INSENSITIVELY AND RETURNED IN THE TABLE'S OWN SPELLING, because
    a phone that sent "u.s. passport" meant the U.S. Passport and storing the
    lowercase version would put a title on a federal form that does not match the
    list it claims to be from. The refusal names the category's whole accepted
    list: an operator reading "not a List A document" with no list to compare
    against has to go and find one.
    """
    accepted = _document_titles(category)
    if not accepted:
        return title
    for known in accepted:
        if known.casefold() == title.casefold():
            return known
    raise ToolError(
        f"{label} {title!r} is not a List {category} document on this site. "
        f"List {category} accepts: {', '.join(sorted(accepted))}. "
        "list_i9_document_types has the whole table, including which documents an "
        "operator has deactivated here."
    )


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
        "store_full_ssn": bool(int(doc.get("store_full_ssn") or 0)),
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
    data["reverifications"] = _reverification_history(name)
    data["reverification_count"] = len(data["reverifications"])
    _log_action(name, employee, "Viewed")
    return ToolResult(data=data, summary=f"I-9 for {employee}: {row.get('status')}")


def _reverification_history(i9_name: str) -> list[dict]:
    """Every Section 3 entry on one form, oldest first.

    Read off the child table directly rather than through `frappe.get_doc`,
    because this runs inside a read tool and loading the parent to reach its
    children would pull the Section 1 columns — including the encrypted SSN
    field — into memory for a caller who asked for a reverification history.
    """
    try:
        rows = frappe.db.get_all(
            I9_REVERIFICATION,
            filters={"parent": i9_name, "parenttype": I9_FORM},
            fields=["name", *REVERIFICATION_FIELDS],
            order_by="idx asc",
        )
    except Exception:
        return []
    return [{k: (str(v) if v is not None else None) for k, v in dict(r).items()} for r in rows]


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
                "receipt_pending", "receipt_expires_on",
                "retention_until", "destruction_eligible_date"],
        limit_page_length=limit,
        order_by="modified desc",
    )
    data = {"forms": [dict(r) for r in rows], "count": len(rows)}
    return ToolResult(data=data, summary=f"{len(rows)} I-9 form(s)")


def list_pending_i9_verifications(args: dict) -> ToolResult:
    """I-9 forms awaiting employer verification (Section 2), and the receipts running out.

    TWO KINDS OF OUTSTANDING WORK, REPORTED SEPARATELY BECAUSE THEY ARE NOT THE
    SAME OBLIGATION. `pending` is a Section 2 that has not been signed at all,
    and the deadline is three business days from the hire date. `receipts_outstanding`
    is a Section 2 that WAS signed against a receipt for a lost or stolen document
    — the form is Complete, the person may work, and the actual document is owed
    within 90 days. Merging them into one list would put a worker who is lawfully
    employed in the same bucket as one whose paperwork was never done.
    """
    company = as_str(args, "company")
    scoped = {"company": resolve_company(company)} if company else {}

    filters = {"status": ["in", ["Section 1 Complete", "Awaiting Verification"]], **scoped}
    rows = frappe.db.get_all(
        I9_FORM,
        filters=filters,
        fields=["name", "employee", "employee_name", "company", "status", "hire_date"],
        order_by="hire_date asc",
    )
    today = date.today()
    for row in rows:
        if row.get("hire_date"):
            hire = getdate(row["hire_date"])
            days_since = (today - hire).days
            row["days_since_hire"] = days_since
            row["overdue"] = days_since > 3

    receipts = frappe.db.get_all(
        I9_FORM,
        filters={"receipt_pending": 1, "status": ["not in", ["Destroyed"]], **scoped},
        fields=["name", "employee", "employee_name", "company", "status", "hire_date",
                "receipt_expires_on", "list_a_is_receipt", "list_b_is_receipt", "list_c_is_receipt"],
        order_by="receipt_expires_on asc",
    )
    for row in receipts:
        row["receipt_lists"] = [
            category
            for category, flag in (("A", "list_a_is_receipt"), ("B", "list_b_is_receipt"),
                                   ("C", "list_c_is_receipt"))
            if int(row.get(flag) or 0)
        ]
        if row.get("receipt_expires_on"):
            expires = getdate(row["receipt_expires_on"])
            row["days_until_receipt_expiry"] = (expires - today).days
            row["overdue"] = expires < today

    data = {
        "pending": [dict(r) for r in rows],
        "count": len(rows),
        "receipts_outstanding": [dict(r) for r in receipts],
        "receipts_count": len(receipts),
    }
    summary = f"{len(rows)} I-9(s) pending verification"
    if receipts:
        summary += f", {len(receipts)} receipt(s) outstanding"
    return ToolResult(data=data, summary=summary)


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
    """Accepted documents by list category.

    THE GROUPED SHAPE IS THE ONE A FORM ACTUALLY NEEDS, and it is returned
    alongside the flat list rather than instead of it. Section 2 is not a free
    choice among 24 documents: it is "one from List A" or "one from List B AND
    one from List C", and a caller drawing that form has to split the list on
    exactly that line before it can draw anything. Every caller doing the split
    itself is every caller having its own copy of which category is which.
    """
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
    documents = [dict(r) for r in rows]
    data = {
        "documents": documents,
        "count": len(documents),
        "by_list": {
            category: [d for d in documents if d.get("list_category") == category]
            for category in ("A", "B", "C")
        },
    }
    return ToolResult(data=data, summary=f"{len(documents)} I-9 document type(s)")


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


def _store_full_ssn_enabled() -> bool:
    """Whether this site has asked to keep nine digits. Absent means no."""
    try:
        return bool(int(frappe.db.get_single_value(I9_SETTINGS, "store_full_ssn") or 0))
    except Exception:
        return False


def submit_i9_section_1(args: dict) -> ToolResult:
    """Fill Section 1 of an I-9 Form (employee information).

    SECTION 1 ASKS AN ALIEN AUTHORIZED TO WORK FOR ONE OF THREE IDENTIFIERS and
    this refuses a form that answers with none of them: a USCIS/A-Number, a Form
    I-94 admission number, or a foreign passport number with its country of
    issuance. That is the form's own rule rather than this app's, and a Section 1
    filed without an answer to it is not a filed Section 1 — which is why it is a
    refusal here and not a warning somewhere an operator reads later.

    A FOREIGN PASSPORT NUMBER WITHOUT A COUNTRY IS REFUSED. Passport numbering is
    per-issuer; the number alone identifies nobody, and storing half the pair
    would leave a form that looks answered and is not.

    THE FULL SSN IS WRITTEN ONLY WHERE THE SITE ASKED FOR IT. `ssn` takes the
    whole number; `ssn_last_four` takes either. Both are stripped to four digits
    for `ssn_last_four` whatever arrives, and the nine-digit form reaches the
    encrypted `ssn_full` column only where `store_full_ssn` is on in I-9
    Settings — see this module's header for why that is a switch rather than a
    default. A full number sent to a site with the switch off is not an error and
    is not stored: the caller got what it asked for, which is an I-9 with the
    last four on it.
    """
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

    ssn = as_str(args, "ssn") or as_str(args, "ssn_last_four")
    if ssn:
        digits = "".join(c for c in ssn if c.isdigit())
        doc.ssn_last_four = digits[-4:] if len(digits) >= 4 else digits
        if len(digits) == 9 and _store_full_ssn_enabled():
            doc.ssn_full = digits

    if doc.citizenship_status in ("Lawful Permanent Resident", "Alien Authorized to Work"):
        doc.alien_registration_number = as_str(args, "alien_registration_number")
    if doc.citizenship_status == "Alien Authorized to Work":
        doc.i94_admission_number = as_str(args, "i94_admission_number")
        doc.foreign_passport_number = as_str(args, "foreign_passport_number")
        doc.foreign_passport_country = as_str(args, "foreign_passport_country")
        doc.alien_work_authorization_expiry = as_date(args, "alien_work_authorization_expiry")

        if doc.foreign_passport_number and not doc.foreign_passport_country:
            raise ToolError(
                "foreign_passport_number was given without foreign_passport_country. "
                "Passport numbers are issued per country and a number on its own identifies "
                "nobody — Section 1 asks for the pair."
            )
        if not any(doc.get(field) for field in ALIEN_IDENTIFIERS):
            raise ToolError(
                "citizenship_status 'Alien Authorized to Work' needs ONE of "
                "alien_registration_number (USCIS/A-Number), i94_admission_number "
                "(Form I-94/I-94A), or foreign_passport_number with "
                "foreign_passport_country. Section 1 of Form I-9 asks for one of the "
                "three and this form answered with none."
            )

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

    _log_action(doc.name, employee, "Section 1 Submitted", {
        "citizenship_status": doc.citizenship_status,
        # WHICH identifier, never the identifier itself. The audit log answers
        # "was this form answered and how", and an A-number copied into a JSON
        # blob on a second doctype is one more place a personal identifier lives.
        "identifiers": [f for f in ALIEN_IDENTIFIERS if doc.get(f)],
        "full_ssn_stored": bool(doc.get("ssn_full")),
    })

    return ToolResult(
        data={"name": doc.name, "employee": employee, "status": doc.status},
        summary=f"Section 1 submitted for {employee}",
        docstatus_delta="Draft → Section 1 Complete",
    )


def submit_i9_section_2(args: dict) -> ToolResult:
    """Fill Section 2 of an I-9 Form (employer verification).

    THE DOCUMENT TITLES ARE CHECKED AGAINST THE LIST THEY CLAIM TO BE FROM. All
    24 USCIS-accepted documents are seeded by `i9_documents.py` and any of them
    may be recorded; what is refused is a title in the wrong slot — a driver's
    license in the List A slot is a form asserting that one document proved both
    identity and employment authorization, and it proved neither. A site whose
    I-9 Document Type table is empty is not checked at all; `_document_titles`
    sets out why that is the safe direction.

    A RECEIPT IS ACCEPTED AND THE FORM STILL COMPLETES. Under 8 CFR
    274a.2(b)(1)(vi) an employee whose document was lost, stolen or damaged may
    present a receipt for the replacement and work while it comes. So
    `list_a_is_receipt` and its two siblings do NOT hold the form open: the
    status goes to Complete, because the person may lawfully work, and
    `receipt_pending` with `receipt_expires_on` — hire date plus 90 days,
    computed by the controller — carries what is still owed.
    `list_pending_i9_verifications` reports them and `reverify_i9` closes them.

    THE TITLE IS STILL CHECKED WHEN IT IS A RECEIPT, because a receipt is a
    receipt FOR a named document and the document it replaces is what has to be
    on the list. A receipt whose slot is empty of a title is a form recording
    that something was examined without saying what.
    """
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
        doc.list_a_doc_title = _check_document_title(
            as_str(args, "list_a_doc_title", required=True), "A", "list_a_doc_title"
        )
        doc.list_a_doc_authority = as_str(args, "list_a_doc_authority")
        doc.list_a_doc_number = as_str(args, "list_a_doc_number")
        doc.list_a_doc_expiry = as_date(args, "list_a_doc_expiry")
        doc.list_a_is_receipt = as_bool(args, "list_a_is_receipt", False)
        doc.list_b_is_receipt = 0
        doc.list_c_is_receipt = 0
    else:
        doc.list_b_doc_title = _check_document_title(
            as_str(args, "list_b_doc_title", required=True), "B", "list_b_doc_title"
        )
        doc.list_b_doc_authority = as_str(args, "list_b_doc_authority")
        doc.list_b_doc_number = as_str(args, "list_b_doc_number")
        doc.list_b_doc_expiry = as_date(args, "list_b_doc_expiry")
        doc.list_b_is_receipt = as_bool(args, "list_b_is_receipt", False)
        doc.list_c_doc_title = _check_document_title(
            as_str(args, "list_c_doc_title", required=True), "C", "list_c_doc_title"
        )
        doc.list_c_doc_authority = as_str(args, "list_c_doc_authority")
        doc.list_c_doc_number = as_str(args, "list_c_doc_number")
        doc.list_c_doc_expiry = as_date(args, "list_c_doc_expiry")
        doc.list_c_is_receipt = as_bool(args, "list_c_is_receipt", False)
        doc.list_a_is_receipt = 0

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

    receipt_lists = [
        category
        for category, flag in (("A", "list_a_is_receipt"), ("B", "list_b_is_receipt"),
                               ("C", "list_c_is_receipt"))
        if int(doc.get(flag) or 0)
    ]

    _log_action(doc.name, employee, "Section 2 Signed", {
        "document_path": doc.document_path,
        "verifier_name": doc.verifier_name,
        "verification_date": str(verification_date),
        "receipt_lists": receipt_lists,
    })
    if receipt_lists:
        _log_action(doc.name, employee, "Receipt Accepted", {
            "receipt_lists": receipt_lists,
            "receipt_expires_on": str(doc.receipt_expires_on or ""),
        })

    data = {
        "name": doc.name,
        "employee": employee,
        "status": doc.status,
        "receipt_pending": bool(int(doc.receipt_pending or 0)),
        "receipt_expires_on": str(doc.receipt_expires_on) if doc.receipt_expires_on else None,
    }
    summary = f"Section 2 completed for {employee} by {doc.verifier_name}"
    if receipt_lists:
        summary += (
            f" against a List {'/'.join(receipt_lists)} receipt — the document itself is "
            f"owed by {doc.receipt_expires_on}"
        )
    return ToolResult(
        data=data,
        summary=summary,
        docstatus_delta="Section 1 Complete → Complete",
    )


def update_i9_settings(args: dict) -> ToolResult:
    """Update I-9 site settings."""
    try:
        doc = frappe.get_doc(I9_SETTINGS)
    except Exception:
        raise ToolError("I-9 Settings does not exist. Run bench migrate.")

    changed = []
    for field in ("store_document_copies", "enrolled_in_e_verify", "store_full_ssn"):
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
    """Move an I-9 to Reverification Needed when work auth expires.

    THIS RAISES THE FLAG; `reverify_i9` LOWERS IT. Until v0.47.0 nothing lowered
    it — an I-9 could be marked as needing re-examination and there was no call
    that recorded the re-examination having happened, which left an operator with
    a Desk edit over Section 2's own columns and the day-of-hire record gone.
    """
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


def reverify_i9(args: dict) -> ToolResult:
    """Record a Section 3 entry — Form I-9's Supplement B, Reverification and Rehire.

    THIS IS THE CALL A RETURNING WORKER'S EXPIRED I-9 NEEDS, and it is why it
    exists: `flag_i9_reverification` could say an I-9 needed re-examining and
    nothing in this app could then record that it had been. So an expiring
    authorization went one of two ways on a real site — a second I-9 created
    beside the first, which `create_i9_form` refuses outright, or the Section 2
    columns edited in the Desk, which overwrites what was examined on the day of
    hire. The record §1324a asks an employer to have kept is BOTH: the original
    verification and every reverification since.

    SO IT APPENDS AND NEVER OVERWRITES. Each call adds one `I-9 Reverification`
    row to the parent's table. A seasonal picker on a renewing EAD accumulates
    one a season, in order, and the row from four seasons ago still says what was
    examined four seasons ago.

    IT MOVES TWO COLUMNS AND NO OTHERS. `alien_work_authorization_expiry` goes to
    the new document's date where it carries one — that column is what
    `list_expiring_work_authorizations` reads, and leaving it on the document just
    replaced would go on reporting a renewed authorization as expiring. And
    `Employee.i9_status` moves off `Expired`, which is the only write this app
    makes to that column and the only one it should; `_clear_expired_i9_column`
    is where that argument is set out.

    A REVERIFICATION AGAINST AN ALREADY-EXPIRED DOCUMENT IS REFUSED. Recording
    one would produce a form asserting the employer examined evidence of
    continuing authorization on a day when the document showed the opposite.
    A reverification with NO expiry date is accepted — an unexpiring document is
    a real answer, and the alternative is refusing a lawful reverification for
    lacking a date that does not exist.

    WHICH DOCUMENTS ARE ACCEPTED: List A or List C. Reverification establishes
    continuing employment authorization; List B establishes identity, which does
    not expire and is not re-examined. The title is checked against whichever of
    the two lists it is on, and a List B title is refused with that sentence.

    'Receipt Replaced' IS A REVERIFICATION REASON rather than its own tool. What
    the employer does when the real document turns up is exactly what they do
    when an authorization is renewed — examine it, record it, sign it — and the
    only difference is that this one also clears `receipt_pending`, which it does
    by clearing the receipt flags the controller computes that from.
    """
    employee = _resolve_employee(args)
    i9_name = frappe.db.get_value(
        I9_FORM,
        {"employee": employee, "status": ["in", list(REVERIFIABLE_STATUSES)]},
        "name",
    )
    if not i9_name:
        raise ToolError(
            f"no I-9 Form for {employee!r} in a state that can be reverified. "
            f"Section 3 records a SECOND examination and needs a first one to follow: "
            f"the form must be in {', '.join(REVERIFIABLE_STATUSES)}. A Draft or a form "
            "whose Section 2 was never signed is completed with submit_i9_section_2, not "
            "reverified."
        )

    doc = frappe.get_doc(I9_FORM, i9_name)

    reason = as_str(args, "reason", required=True)
    if reason not in REVERIFICATION_REASONS:
        raise ToolError(
            f"reason {reason!r} is not one this form records. Accepted: "
            f"{', '.join(REVERIFICATION_REASONS)}."
        )

    title, category = _reverification_document(as_str(args, "document_title", required=True))

    reverification_date = as_date(args, "reverification_date") or str(date.today())
    document_expiry = as_date(args, "document_expiry")
    if document_expiry and getdate(document_expiry) < getdate(reverification_date):
        raise ToolError(
            f"document_expiry {document_expiry} is before reverification_date "
            f"{reverification_date}. A reverification records that the employer examined "
            "evidence of CONTINUING work authorization; a document that had already "
            "expired on the day it was examined is not that."
        )

    rehire_date = as_date(args, "rehire_date")
    if reason == "Rehire" and not rehire_date:
        raise ToolError("reason 'Rehire' needs rehire_date — Supplement B asks for it by name.")

    old_expiry = doc.alien_work_authorization_expiry
    row = doc.append("reverifications", {
        "reverification_date": reverification_date,
        "reason": reason,
        "rehire_date": rehire_date,
        "document_title": title,
        "issuing_authority": as_str(args, "issuing_authority"),
        "document_number": as_str(args, "document_number"),
        "document_expiry": document_expiry,
        "verifier_name": as_str(args, "verifier_name", required=True),
        "verifier_title": as_str(args, "verifier_title"),
        "section_3_signature": as_str(args, "section_3_signature"),
        "notes": as_str(args, "notes"),
        "signed_at": frappe.utils.now(),
        "signed_ip": (
            frappe.local.request.remote_addr
            if hasattr(frappe, "local") and hasattr(frappe.local, "request") and frappe.local.request
            else ""
        ),
    })

    if document_expiry:
        doc.alien_work_authorization_expiry = document_expiry

    receipt_closed = False
    if reason == "Receipt Replaced":
        if not int(doc.receipt_pending or 0):
            raise ToolError(
                f"the I-9 for {employee!r} has no receipt outstanding, so there is nothing "
                "for a replacement to replace. Record a renewed authorization with reason "
                "'Work Authorization Expired'."
            )
        doc.list_a_is_receipt = 0
        doc.list_b_is_receipt = 0
        doc.list_c_is_receipt = 0
        receipt_closed = True

    old_status = doc.status
    doc.status = "Complete"
    doc.flags.ignore_permissions = True
    doc.save()

    column = _clear_expired_i9_column(employee)

    _log_action(doc.name, employee, "Section 3 Reverified", {
        "reason": reason,
        "document_title": title,
        "list_category": category,
        "document_expiry": str(document_expiry or ""),
        "previous_expiry": str(old_expiry or ""),
        "previous_status": old_status,
        "verifier_name": row.verifier_name,
        "receipt_closed": receipt_closed,
        "employee_i9_status": column,
        "entry": len(doc.reverifications),
    })

    return ToolResult(
        data={
            "name": doc.name,
            "employee": employee,
            "status": doc.status,
            "reason": reason,
            "document_title": title,
            "list_category": category or None,
            "document_expiry": str(document_expiry) if document_expiry else None,
            "work_authorization_expiry": (
                str(doc.alien_work_authorization_expiry)
                if doc.alien_work_authorization_expiry
                else None
            ),
            "receipt_pending": bool(int(doc.receipt_pending or 0)),
            "reverification_count": len(doc.reverifications),
            "employee_i9_status": column,
        },
        summary=(
            f"I-9 for {employee} reverified ({reason}) against {title} "
            f"by {row.verifier_name}"
        ),
        docstatus_delta=f"{old_status} → Complete",
    )


def _clear_expired_i9_column(employee: str) -> str | None:
    """Move `Employee.i9_status` off Expired, and off NOTHING else.

    THE ONE PLACE THIS APP WRITES THAT COLUMN, and the narrowness is the whole
    argument for doing it at all. `i9_status` is a Custom Field
    `compliance_fields.py` installs; v0.46.2 established that no I-9 tool writes
    it and that `employee_detail` reconciles a stale Pending against a live
    record on the way out — while `Expired` is left ALONE, because an expired
    I-9 is somebody's deliberate statement and a Complete form from an earlier
    season is exactly the wrong thing to trust against it.

    A reverification is the one event that answers that statement. Leaving the
    column on Expired afterwards would have `get_employee` go on reporting the
    worker as needing an I-9 — the wizard would route them to `create_i9_form`,
    which refuses because they have one, and the `i9_expired` alert would go on
    firing about an authorization that was renewed this morning. So the deliberate
    statement is answered by an equally deliberate action, and by nothing else:
    a column reading anything OTHER than Expired is not touched, which leaves
    `employee_detail`'s reconciliation the only thing that moves a Pending.

    BEST EFFORT AND NEVER FATAL. The Section 3 row is filed and the audit row is
    written whatever happens here; a site that never ran `install_compliance_fields`
    has no such column, and losing a convenience column must not lose a federal
    record. Returns the value written, or None where nothing was.
    """
    try:
        from .. import compat

        if not compat.has_field(EMPLOYEE, "i9_status"):
            return None
        current = str(frappe.db.get_value(EMPLOYEE, employee, "i9_status") or "").strip()
        if current != "Expired":
            return None
        # The site's own Select options are the arbiter, exactly as they are in
        # `employee.employee_detail`: an operator who edited the field gets their
        # value or none, never one invented from this module's idea of the options.
        from ..args import select_options

        options = select_options(EMPLOYEE, "i9_status")
        if options and "Verified" not in options:
            return None
        frappe.db.set_value(EMPLOYEE, employee, "i9_status", "Verified")
        return "Verified"
    except Exception:
        return None


def _reverification_document(title: str) -> tuple[str, str]:
    """The document title as the table spells it, and which list it is on.

    THREE CASES, AND TELLING THEM APART IS THE WHOLE JOB. A List B title is
    refused with its own sentence, because "not a reverification document" and
    "not a document" are different things to read at four in the morning in a
    packing shed. A List A or List C title comes back in the table's own
    spelling. A table with no enabled rows checks nothing and returns the title
    as given — the same reading `_document_titles` takes and for the same reason.
    """
    try:
        rows = frappe.db.get_all(
            I9_DOCUMENT_TYPE,
            filters={"enabled": 1},
            fields=["doc_title", "list_category"],
        )
    except Exception:
        rows = []
    if not rows:
        return title, ""

    for row in rows:
        if str(row.get("doc_title") or "").casefold() == title.casefold():
            category = str(row.get("list_category") or "")
            if category == "B":
                raise ToolError(
                    f"{title!r} is a List B document. List B establishes IDENTITY, which "
                    "does not expire and is not re-examined — a reverification records a "
                    "List A or List C document establishing continuing employment "
                    "authorization."
                )
            return str(row.get("doc_title")), category

    accepted = sorted(
        str(r["doc_title"])
        for r in rows
        if r.get("doc_title") and str(r.get("list_category") or "") in ("A", "C")
    )
    raise ToolError(
        f"document_title {title!r} is not an accepted I-9 document on this site. "
        f"A reverification records a List A or List C document: {', '.join(accepted)}. "
        "list_i9_document_types has the whole table."
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
