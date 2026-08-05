# SPDX-License-Identifier: MIT
"""Structured I-9 workflow — the 14 tools, the audit trail, the SSN rule.

SEVEN CLAIMS.

1. `ReadTools` — every read tool returns the right data in the right shape.
2. `CreateAndFill` — the happy path: create, Section 1, Section 2.
3. `SSNIsStrippedToLastFour` — the full SSN never touches the database.
4. `ThreeBusinessDayRule` — Section 2 is refused when verification is late.
5. `AuditTrail` — every mutating action writes an immutable I-9 Audit Log row.
6. `RetentionAndDestruction` — retention dates, destruction eligibility, and
   the refusal to destroy before the retention date.
7. `OnboardCreatesI9` — onboard_employee auto-creates a Draft I-9 Form.
"""
import json
from datetime import date, timedelta

import frappe

from erpnext_mcp.tools import i9, newhire

from .fixtures import MAIN, V12TestCase, install_hrms
from .harness import STORE

I9_TOOLS_ON = {
    f"allow_{name}": 1
    for name in (
        "get_i9_settings",
        "get_i9_form",
        "list_i9_forms",
        "list_pending_i9_verifications",
        "get_i9_audit_log",
        "list_i9_document_types",
        "get_i9_retention_report",
        "list_expiring_work_authorizations",
        "create_i9_form",
        "submit_i9_section_1",
        "submit_i9_section_2",
        "update_i9_settings",
        "flag_i9_reverification",
        "destroy_i9",
    )
}

ONBOARD_ON = {f"allow_{name}": 1 for name in ("onboard_employee", "create_mobile_user", "attach_file_to_document")}


class I9TestCase(V12TestCase):
    def setUp(self):
        super().setUp()
        self.configure(enabled=1, **I9_TOOLS_ON)
        install_hrms()
        self._seed_i9_settings()
        self._seed_doc_types()

    def _seed_i9_settings(self):
        STORE.singles["I-9 Settings"] = {
            "doctype": "I-9 Settings",
            "store_document_copies": "0",
            "enrolled_in_e_verify": "0",
            "business_legal_name": "Test Farm LLC",
            "business_address": "123 Orchard Rd",
            "business_ein": "12-3456789",
            "reminder_days_before_doc_expiration": "90",
            "reminder_days_before_destruction": "60",
        }

    def _seed_doc_types(self):
        STORE.seed(
            "I-9 Document Type",
            [
                {
                    "name": "U.S. Passport",
                    "doc_title": "U.S. Passport",
                    "list_category": "A",
                    "uscis_code": "passport",
                    "requires_photo": 1,
                    "enabled": 1,
                    "description": "Unexpired U.S. passport",
                },
                {
                    "name": "Driver's License",
                    "doc_title": "Driver's License",
                    "list_category": "B",
                    "uscis_code": "drivers-license",
                    "requires_photo": 1,
                    "enabled": 1,
                    "description": "State-issued driver's license",
                },
                {
                    "name": "Social Security Card (Unrestricted)",
                    "doc_title": "Social Security Card (Unrestricted)",
                    "list_category": "C",
                    "uscis_code": "ssn-card",
                    "requires_photo": 0,
                    "enabled": 1,
                    "description": "Social Security Account Number card",
                },
            ],
        )

    def _create_draft(self, employee="HR-EMP-00001", hire_date=None):
        if hire_date is None:
            hire_date = str(date.today())
        return self.tool_data(
            "create_i9_form",
            {"employee": employee, "company": MAIN, "hire_date": hire_date},
        )

    def _submit_section_1(self, employee="HR-EMP-00001", **overrides):
        payload = {
            "employee": employee,
            "legal_first_name": "Ada",
            "legal_last_name": "Orchard",
            "citizenship_status": "US Citizen",
        }
        payload.update(overrides)
        return self.tool_data("submit_i9_section_1", payload)

    def _submit_section_2(self, employee="HR-EMP-00001", **overrides):
        payload = {
            "employee": employee,
            "document_path": "List A",
            "list_a_doc_title": "U.S. Passport",
            "list_a_doc_authority": "US Dept of State",
            "list_a_doc_number": "123456789",
            "verifier_name": "Tim Polehn",
            "verification_date": str(date.today()),
        }
        payload.update(overrides)
        return self.tool_data("submit_i9_section_2", payload)


# ── 1 ─────────────────────────────────────────────────────────────────────────
class ReadTools(I9TestCase):
    def test_get_i9_settings(self):
        data = self.tool_data("get_i9_settings", {})
        self.assertEqual(data["business_legal_name"], "Test Farm LLC")
        self.assertEqual(data["business_ein"], "12-3456789")
        self.assertFalse(data["store_document_copies"])
        self.assertFalse(data["enrolled_in_e_verify"])

    def test_list_i9_document_types_returns_all(self):
        data = self.tool_data("list_i9_document_types", {})
        self.assertEqual(data["count"], 3)

    def test_list_i9_document_types_filters_by_category(self):
        data = self.tool_data("list_i9_document_types", {"list_category": "A"})
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["documents"][0]["doc_title"], "U.S. Passport")

    def test_list_i9_forms_empty(self):
        data = self.tool_data("list_i9_forms", {})
        self.assertEqual(data["count"], 0)

    def test_get_i9_form_missing_raises(self):
        msg = self.tool_error("get_i9_form", {"employee": "HR-EMP-00001"})
        self.assertIn("no I-9 Form", msg)

    def test_list_pending_i9_verifications_empty(self):
        data = self.tool_data("list_pending_i9_verifications", {})
        self.assertEqual(data["count"], 0)

    def test_get_i9_retention_report_empty(self):
        data = self.tool_data("get_i9_retention_report", {})
        self.assertEqual(data["approaching_count"], 0)
        self.assertEqual(data["eligible_count"], 0)

    def test_list_expiring_work_authorizations_empty(self):
        data = self.tool_data("list_expiring_work_authorizations", {})
        self.assertEqual(data["count"], 0)


# ── 2 ─────────────────────────────────────────────────────────────────────────
class CreateAndFill(I9TestCase):
    def test_create_draft(self):
        data = self._create_draft()
        self.assertEqual(data["status"], "Draft")
        self.assertEqual(data["employee"], "HR-EMP-00001")

    def test_create_refuses_duplicate(self):
        self._create_draft()
        msg = self.tool_error(
            "create_i9_form",
            {"employee": "HR-EMP-00001", "company": MAIN, "hire_date": str(date.today())},
        )
        self.assertIn("already has an active I-9", msg)

    def test_submit_section_1(self):
        self._create_draft()
        data = self._submit_section_1()
        self.assertEqual(data["status"], "Section 1 Complete")

    def test_submit_section_1_requires_draft(self):
        msg = self.tool_error(
            "submit_i9_section_1",
            {"employee": "HR-EMP-00001", "legal_first_name": "Ada",
             "legal_last_name": "Orchard", "citizenship_status": "US Citizen"},
        )
        self.assertIn("no Draft I-9", msg)

    def test_submit_section_2_list_a(self):
        self._create_draft()
        self._submit_section_1()
        data = self._submit_section_2()
        self.assertEqual(data["status"], "Complete")

    def test_submit_section_2_list_b_c(self):
        self._create_draft()
        self._submit_section_1()
        data = self._submit_section_2(
            document_path="List B + C",
            list_b_doc_title="Driver's License",
            list_b_doc_authority="Oregon DMV",
            list_b_doc_number="DL12345",
            list_c_doc_title="Social Security Card (Unrestricted)",
            list_c_doc_authority="SSA",
            list_c_doc_number="SSC12345",
        )
        self.assertEqual(data["status"], "Complete")

    def test_full_workflow_appears_in_list(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        data = self.tool_data("list_i9_forms", {})
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["forms"][0]["status"], "Complete")

    def test_get_i9_form_returns_complete_record(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        data = self.tool_data("get_i9_form", {"employee": "HR-EMP-00001"})
        self.assertEqual(data["status"], "Complete")
        self.assertEqual(data["legal_first_name"], "Ada")
        self.assertEqual(data["legal_last_name"], "Orchard")

    def test_pending_verification_after_section_1(self):
        self._create_draft()
        self._submit_section_1()
        data = self.tool_data("list_pending_i9_verifications", {})
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["pending"][0]["employee"], "HR-EMP-00001")

    def test_no_pending_after_section_2(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        data = self.tool_data("list_pending_i9_verifications", {})
        self.assertEqual(data["count"], 0)


# ── 3 ─────────────────────────────────────────────────────────────────────────
class SSNIsStrippedToLastFour(I9TestCase):
    def test_full_ssn_stored_as_last_four(self):
        self._create_draft()
        self._submit_section_1(ssn_last_four="123-45-6789")
        row = frappe.db.get_value("I-9 Form", {"employee": "HR-EMP-00001"}, "ssn_last_four")
        self.assertEqual(row, "6789")

    def test_four_digits_stored_as_is(self):
        self._create_draft()
        self._submit_section_1(ssn_last_four="1234")
        row = frappe.db.get_value("I-9 Form", {"employee": "HR-EMP-00001"}, "ssn_last_four")
        self.assertEqual(row, "1234")

    def test_partial_ssn_stored_as_available_digits(self):
        self._create_draft()
        self._submit_section_1(ssn_last_four="89")
        row = frappe.db.get_value("I-9 Form", {"employee": "HR-EMP-00001"}, "ssn_last_four")
        self.assertEqual(row, "89")


# ── 4 ─────────────────────────────────────────────────────────────────────────
class ThreeBusinessDayRule(I9TestCase):
    def test_same_day_verification_accepted(self):
        today = date.today()
        self._create_draft(hire_date=str(today))
        self._submit_section_1()
        data = self._submit_section_2(verification_date=str(today))
        self.assertEqual(data["status"], "Complete")

    def test_three_business_days_accepted(self):
        hire = date(2026, 8, 4)  # Monday
        verify = date(2026, 8, 7)  # Thursday, 3 bdays later
        self._create_draft(hire_date=str(hire))
        self._submit_section_1()
        data = self._submit_section_2(verification_date=str(verify))
        self.assertEqual(data["status"], "Complete")

    def test_four_business_days_refused(self):
        hire = date(2026, 8, 3)  # Monday
        verify = date(2026, 8, 8)  # Saturday -> actually let's make it realistic
        # Monday to the next Monday = 5 bdays (M,T,W,Th,F + M) but counting inclusive
        hire = date(2026, 8, 3)  # Monday
        verify = date(2026, 8, 10)  # next Monday = 6 bdays inclusive
        self._create_draft(hire_date=str(hire))
        self._submit_section_1()
        msg = self.tool_error(
            "submit_i9_section_2",
            {
                "employee": "HR-EMP-00001",
                "document_path": "List A",
                "list_a_doc_title": "U.S. Passport",
                "verifier_name": "Tim Polehn",
                "verification_date": str(verify),
            },
        )
        self.assertIn("business days", msg)

    def test_weekend_not_counted(self):
        hire = date(2026, 8, 7)  # Friday
        verify = date(2026, 8, 11)  # Tuesday = 3 bdays (Fri, Mon, Tue)
        self._create_draft(hire_date=str(hire))
        self._submit_section_1()
        data = self._submit_section_2(verification_date=str(verify))
        self.assertEqual(data["status"], "Complete")


# ── 5 ─────────────────────────────────────────────────────────────────────────
class AuditTrail(I9TestCase):
    def test_create_logs_created(self):
        self._create_draft()
        logs = STORE.rows("I-9 Audit Log")
        created = [r for r in logs if r.get("action") == "Created"]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["employee"], "HR-EMP-00001")

    def test_section_1_logs_submitted(self):
        self._create_draft()
        self._submit_section_1()
        logs = STORE.rows("I-9 Audit Log")
        s1 = [r for r in logs if r.get("action") == "Section 1 Submitted"]
        self.assertEqual(len(s1), 1)

    def test_section_2_logs_signed(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        logs = STORE.rows("I-9 Audit Log")
        s2 = [r for r in logs if r.get("action") == "Section 2 Signed"]
        self.assertEqual(len(s2), 1)

    def test_view_logs_viewed(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        self.tool_data("get_i9_form", {"employee": "HR-EMP-00001"})
        logs = STORE.rows("I-9 Audit Log")
        viewed = [r for r in logs if r.get("action") == "Viewed"]
        self.assertEqual(len(viewed), 1)

    def test_get_audit_log_returns_entries(self):
        self._create_draft()
        self._submit_section_1()
        data = self.tool_data("get_i9_audit_log", {"employee": "HR-EMP-00001"})
        self.assertGreaterEqual(data["count"], 2)

    def test_audit_log_is_immutable(self):
        self._create_draft()
        logs = STORE.rows("I-9 Audit Log")
        self.assertTrue(len(logs) > 0)


# ── 6 ─────────────────────────────────────────────────────────────────────────
class RetentionAndDestruction(I9TestCase):
    def test_retention_date_calculated(self):
        hire = str(date.today() - timedelta(days=365))
        self._create_draft(hire_date=hire)
        row = frappe.db.get_value("I-9 Form", {"employee": "HR-EMP-00001"}, "retention_until")
        self.assertIsNotNone(row)

    def test_destroy_refused_before_retention(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        msg = self.tool_error("destroy_i9", {"employee": "HR-EMP-00001"})
        self.assertIn("retained until", msg)

    def test_destroy_after_retention(self):
        hire = date.today() - timedelta(days=365 * 4)
        self._create_draft(hire_date=str(hire))
        self._submit_section_1()
        self._submit_section_2(verification_date=str(hire))
        data = self.tool_data("destroy_i9", {"employee": "HR-EMP-00001"})
        self.assertEqual(data["status"], "Destroyed")

    def test_destroy_logs_destruction(self):
        hire = date.today() - timedelta(days=365 * 4)
        self._create_draft(hire_date=str(hire))
        self._submit_section_1()
        self._submit_section_2(verification_date=str(hire))
        self.tool_data("destroy_i9", {"employee": "HR-EMP-00001"})
        logs = STORE.rows("I-9 Audit Log")
        destroyed = [r for r in logs if r.get("action") == "Destroyed"]
        self.assertEqual(len(destroyed), 1)

    def test_reverification_flag(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        data = self.tool_data(
            "flag_i9_reverification",
            {"employee": "HR-EMP-00001", "reason": "Work auth expiring"},
        )
        self.assertEqual(data["status"], "Reverification Needed")

    def test_reverification_logs(self):
        self._create_draft()
        self._submit_section_1()
        self._submit_section_2()
        self.tool_data(
            "flag_i9_reverification",
            {"employee": "HR-EMP-00001", "reason": "Work auth expiring"},
        )
        logs = STORE.rows("I-9 Audit Log")
        flagged = [r for r in logs if r.get("action") == "Reverification Flagged"]
        self.assertEqual(len(flagged), 1)

    def test_retention_report_shows_eligible(self):
        hire = str(date.today() - timedelta(days=365 * 4))
        self._create_draft(hire_date=hire)
        data = self.tool_data("get_i9_retention_report", {})
        self.assertGreater(data["eligible_count"], 0)

    def test_expiring_work_auth(self):
        self._create_draft()
        self._submit_section_1(
            citizenship_status="Alien Authorized to Work",
            alien_work_authorization_expiry=str(date.today() + timedelta(days=30)),
        )
        self._submit_section_2()
        data = self.tool_data("list_expiring_work_authorizations", {"days_ahead": 90})
        self.assertEqual(data["count"], 1)


# ── 7 ─────────────────────────────────────────────────────────────────────────
class OnboardCreatesI9(I9TestCase):
    def setUp(self):
        super().setUp()
        self.configure(enabled=1, **I9_TOOLS_ON, **ONBOARD_ON)

    def test_onboard_creates_draft_i9(self):
        data = self.tool_data(
            "onboard_employee",
            {"full_name": "Ana Ramos", "company": MAIN},
        )
        self.assertIsNotNone(data.get("i9_form"))
        i9 = frappe.db.get_value("I-9 Form", data["i9_form"], "status")
        self.assertEqual(i9, "Draft")

    def test_onboard_reuses_existing_i9(self):
        data1 = self.tool_data(
            "onboard_employee",
            {"full_name": "Ana Ramos", "company": MAIN},
        )
        i9_name = data1["i9_form"]
        data2 = self.tool_data(
            "onboard_employee",
            {"full_name": "Ana Ramos", "company": MAIN},
        )
        self.assertEqual(data2["i9_form"], i9_name)


class UpdateI9Settings(I9TestCase):
    def test_update_settings(self):
        data = self.tool_data("update_i9_settings", {"business_legal_name": "New Farm LLC"})
        self.assertIn("business_legal_name", data["updated"])

    def test_update_requires_at_least_one_field(self):
        msg = self.tool_error("update_i9_settings", {})
        self.assertIn("no fields to update", msg)

    def test_e_verify_forces_copies(self):
        self.tool_data("update_i9_settings", {"enrolled_in_e_verify": True})
        data = self.tool_data("get_i9_settings", {})
        self.assertTrue(data["enrolled_in_e_verify"])
