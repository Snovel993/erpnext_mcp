# SPDX-License-Identifier: MIT
"""Seed the I-9 Document Type lookup table with USCIS-accepted documents.

v0.27.0. Pre-seeds the current List A, B and C documents from the USCIS I-9
Acceptable Documents list. An operator can add or deactivate rows but
should not need to.

SEEDED, NOT FIXTURED. `test_hooks.py` forbids the word `fixtures` by name.
`seed_i9_document_types` checks by `doc_title` before it writes, so a document
somebody edited or deactivated stays that way across every future migrate.
"""
from __future__ import annotations

import frappe

DOCTYPE = "I-9 Document Type"

DOCUMENTS = [
    # ── List A: documents that establish both identity and employment authorization
    {"doc_title": "U.S. Passport", "list_category": "A", "uscis_code": "passport", "requires_photo": 1,
     "description": "Unexpired U.S. passport"},
    {"doc_title": "U.S. Passport Card", "list_category": "A", "uscis_code": "passport-card", "requires_photo": 1,
     "description": "Unexpired U.S. passport card"},
    {"doc_title": "Permanent Resident Card (Form I-551)", "list_category": "A", "uscis_code": "i-551",
     "requires_photo": 1,
     "description": "Unexpired Permanent Resident Card or Alien Registration Receipt Card"},
    {"doc_title": "Foreign Passport with I-551 Stamp", "list_category": "A", "uscis_code": "foreign-passport-551",
     "requires_photo": 1,
     "description": "Foreign passport with temporary I-551 stamp or printed notation on MRIV"},
    {"doc_title": "Employment Authorization Document (Form I-766)", "list_category": "A", "uscis_code": "i-766",
     "requires_photo": 1,
     "description": "Unexpired Employment Authorization Document with photograph"},
    {"doc_title": "Foreign Passport with Form I-94 (H-1B)", "list_category": "A",
     "uscis_code": "foreign-passport-94",
     "requires_photo": 1,
     "description": "Foreign passport with Form I-94/I-94A and H-1B nonimmigrant status"},
    {"doc_title": "Passport from FSM or RMI with I-94", "list_category": "A", "uscis_code": "fsm-rmi",
     "requires_photo": 1,
     "description": "Passport from Federated States of Micronesia or Republic of the Marshall Islands with Form I-94/I-94A"},
    # ── List B: documents that establish identity
    {"doc_title": "Driver's License", "list_category": "B", "uscis_code": "drivers-license", "requires_photo": 1,
     "description": "State-issued driver's license or ID card with photograph"},
    {"doc_title": "State ID Card", "list_category": "B", "uscis_code": "state-id", "requires_photo": 1,
     "description": "State-issued identification card with photograph"},
    {"doc_title": "School ID Card", "list_category": "B", "uscis_code": "school-id", "requires_photo": 1,
     "description": "School identification card with photograph"},
    {"doc_title": "Voter Registration Card", "list_category": "B", "uscis_code": "voter-reg", "requires_photo": 0,
     "description": "Voter registration card"},
    {"doc_title": "U.S. Military Card", "list_category": "B", "uscis_code": "military-card", "requires_photo": 1,
     "description": "U.S. military card or draft record"},
    {"doc_title": "Military Dependent ID Card", "list_category": "B", "uscis_code": "mil-dependent",
     "requires_photo": 1,
     "description": "Military dependent's identification card"},
    {"doc_title": "U.S. Coast Guard Merchant Mariner Document", "list_category": "B", "uscis_code": "uscg-mmd",
     "requires_photo": 1,
     "description": "U.S. Coast Guard Merchant Mariner Document"},
    {"doc_title": "Native American Tribal Document", "list_category": "B", "uscis_code": "tribal-doc",
     "requires_photo": 0,
     "description": "Native American tribal document"},
    {"doc_title": "Canadian Driver's License", "list_category": "B", "uscis_code": "canadian-dl",
     "requires_photo": 1,
     "description": "Driver's license issued by a Canadian government authority"},
    # ── List C: documents that establish employment authorization
    {"doc_title": "Social Security Card (Unrestricted)", "list_category": "C", "uscis_code": "ssn-card",
     "requires_photo": 0,
     "description": "Social Security Account Number card without employment restrictions"},
    {"doc_title": "Certification of Birth Abroad (FS-545)", "list_category": "C", "uscis_code": "fs-545",
     "requires_photo": 0,
     "description": "Certification of Birth Abroad issued by the Department of State"},
    {"doc_title": "Certification of Report of Birth (DS-1350)", "list_category": "C", "uscis_code": "ds-1350",
     "requires_photo": 0,
     "description": "Certification of Report of Birth issued by the Department of State"},
    {"doc_title": "U.S. Birth Certificate", "list_category": "C", "uscis_code": "birth-cert", "requires_photo": 0,
     "description": "Original or certified copy of a birth certificate issued by a U.S. state, county, or territory"},
    {"doc_title": "Native American Tribal Document (List C)", "list_category": "C", "uscis_code": "tribal-doc-c",
     "requires_photo": 0,
     "description": "Native American tribal document for employment authorization"},
    {"doc_title": "U.S. Citizen ID Card (Form I-197)", "list_category": "C", "uscis_code": "i-197",
     "requires_photo": 0,
     "description": "U.S. Citizen Identification Card"},
    {"doc_title": "ID Card for Resident Citizen (Form I-179)", "list_category": "C", "uscis_code": "i-179",
     "requires_photo": 0,
     "description": "Identification Card for Use of Resident Citizen in the United States"},
    {"doc_title": "Employment Authorization Document (List C)", "list_category": "C", "uscis_code": "ead-c",
     "requires_photo": 0,
     "description": "Employment authorization document issued by DHS"},
]


def seed_i9_document_types() -> dict:
    """Seed I-9 Document Type rows. Idempotent: checks by doc_title before inserting."""
    if not frappe.db.exists("DocType", DOCTYPE):
        return {"created": [], "existing": [], "failed": [],
                "note": "I-9 Document Type doctype does not exist yet. Run bench migrate."}

    created = []
    existing = []
    failed = []

    for spec in DOCUMENTS:
        try:
            if frappe.db.exists(DOCTYPE, spec["doc_title"]):
                existing.append(spec["doc_title"])
                continue

            doc = frappe.get_doc({"doctype": DOCTYPE, "enabled": 1, **spec})
            doc.flags.ignore_permissions = True
            doc.insert()
            created.append(spec["doc_title"])
        except Exception as exc:
            failed.append({"name": spec["doc_title"], "reason": f"{type(exc).__name__}: {exc}"})

    return {"created": created, "existing": existing, "failed": failed}
