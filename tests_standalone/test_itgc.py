# SPDX-License-Identifier: MIT
"""Phase 5 — IT general controls: access, change, and whether the backups restore.

THREE CLASSES CARRY THE ARGUMENT.

`TheAccessReportIsComputedAndStoredNowhere` checks the claim the tool makes about
itself. A permissions snapshot in a table is wrong the moment somebody adds a
role, so the report must leave nothing behind — and the test asserts on the
absence, which is the only way that property stays true.

`AnApproverIsNotThePersonWhoMadeTheChange` checks the one rule that makes a change
log a control rather than a diary. It is checked at both layers, because a row
written from the Desk has to obey it too.

`NotTestedIsNotAFailureAndAGreenJobIsNotAVerification` checks the distinction the
backup doctype exists to hold. A farm with a year of successful backups and no
restore has no evidence of anything, and the control has to say so without
calling the backups failed.
"""

import frappe

from erpnext_mcp import compliance_rules

from .fixtures import MAIN, SeededTestCase, set_roles
from .harness import STORE

ALL_ON = {
	"allow_generate_access_control_report": 1,
	"allow_create_change_management_log": 1,
	"allow_get_change_management_log": 1,
	"allow_list_change_management_logs": 1,
	"allow_get_change_management_report": 1,
	"allow_create_backup_record": 1,
	"allow_get_backup_record": 1,
	"allow_list_backup_records": 1,
	"allow_record_backup_test": 1,
}

BOOKKEEPER = "bookkeeper@example.test"
MANAGER = "manager@example.test"


class ITGCTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		frappe.local.session.user = "Administrator"
		compliance_rules.seed_compliance_rules()
		# Both are referenced as Link values on every change log row below, and a
		# Link to a user the site does not have is refused — correctly.
		self.a_user(BOOKKEEPER, ["Accounts Manager"], full_name="Bea Bookkeeper")
		self.a_user(MANAGER, ["System Manager"], full_name="Mo Manager")

	def a_user(self, email: str, roles=(), enabled: int = 1, full_name: str = "", last_login: str = "") -> str:
		"""A login with its roles as Has Role rows — which is where the report reads them.

		`set_roles` alone would not do: it feeds `frappe.get_roles`, while
		`roles.all_roles_of` queries the Has Role table exactly as it does on a
		site. Seeding the User's own `roles` child list is what the harness
		flattens into that table.
		"""
		row = {
			"name": email,
			"email": email,
			"enabled": enabled,
			"full_name": full_name or email,
			"roles": [{"role": role} for role in roles],
		}
		if last_login:
			row["last_login"] = last_login
		STORE.seed("User", [row])
		set_roles(email, list(roles))
		return email

	def a_change(self, **extra) -> dict:
		payload = {
			"company": MAIN,
			"change_type": "Permission",
			"title": "Granted Accounts Manager to the new bookkeeper",
			"description": "Added the Accounts Manager role so month-end can be closed while the controller is away.",
			"changed_by": BOOKKEEPER,
			"approved_by": MANAGER,
			**extra,
		}
		return self.tool_data("create_change_management_log", payload)

	def a_backup(self, **extra) -> dict:
		payload = {
			"company": MAIN,
			"backup_type": "Full",
			"status": "Success",
			"started_at": "2026-07-20 02:00:00",
			"completed_at": "2026-07-20 02:41:00",
			"location": "s3://orchard-backups/nightly",
			"offsite": True,
			**extra,
		}
		return self.tool_data("create_backup_record", payload)

	def rule_for(self, control_point: str) -> str:
		name = frappe.db.get_value("Compliance Rule", {"control_point": control_point}, "name")
		self.assertTrue(name, f"no Compliance Rule seeded for control point {control_point}")
		return name

	def set_mode(self, control_point: str, mode: str) -> None:
		frappe.db.set_value("Compliance Rule", self.rule_for(control_point), "enforcement_mode", mode)


# ── 1 · access ──────────────────────────────────────────────────────────────
class TheAccessReportIsComputedAndStoredNowhere(ITGCTestCase):
	def test_it_writes_no_record_of_itself(self):
		before = len(STORE.rows("Change Management Log"))
		self.tool_data("generate_access_control_report", {"company": MAIN})
		self.assertEqual(len(STORE.rows("Change Management Log")), before)

	def test_it_says_it_stored_nothing(self):
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		self.assertIn("stored nowhere", data["nothing_was_stored"])

	def test_it_lists_users_with_their_roles(self):
		self.a_user(BOOKKEEPER, ["Accounts Manager"], full_name="Bea Bookkeeper")
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		row = next(entry for entry in data["users"] if entry["user"] == BOOKKEEPER)
		self.assertIn("Accounts Manager", row["roles"])
		self.assertTrue(row["privileged"])

	def test_privileged_users_sort_first(self):
		self.a_user("nobody@example.test", [])
		self.a_user(BOOKKEEPER, ["Accounts Manager"])
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		self.assertTrue(data["users"][0]["privileged"])

	def test_a_disabled_login_is_excluded_unless_asked_for(self):
		self.a_user("gone@example.test", ["Accounts Manager"], enabled=0)
		default = self.tool_data("generate_access_control_report", {"company": MAIN})
		self.assertNotIn("gone@example.test", {row["user"] for row in default["users"]})
		included = self.tool_data(
			"generate_access_control_report", {"company": MAIN, "include_disabled": True}
		)
		self.assertIn("gone@example.test", {row["user"] for row in included["users"]})

	def test_a_user_with_no_roles_is_flagged(self):
		self.a_user("nobody@example.test", [])
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		row = next(entry for entry in data["users"] if entry["user"] == "nobody@example.test")
		self.assertIn("no roles", row["flags"])

	def test_privileged_only_narrows_the_report(self):
		self.a_user("nobody@example.test", [])
		self.a_user(BOOKKEEPER, ["Accounts Manager"])
		data = self.tool_data("generate_access_control_report", {"company": MAIN, "privileged_only": True})
		self.assertTrue(all(row["privileged"] for row in data["users"]))

	def test_a_never_reviewed_site_reports_the_review_as_overdue(self):
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		self.assertTrue(data["review_overdue"])
		self.assertIsNone(data["last_access_review"])
		self.assertEqual(data["control"]["finding_count"], 1)

	def test_recording_the_review_clears_it(self):
		self.a_change(change_type="Permission", title="Quarterly access review")
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		self.assertFalse(data["review_overdue"])
		self.assertEqual(data["control"]["finding_count"], 0)

	def test_a_missing_last_login_column_is_reported_rather_than_read_as_never(self):
		"""ABSENT is not the same sentence as 'nobody has logged in'."""
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		if not data["last_login_available"]:
			self.assertIn("ABSENT rather than clear", data["last_login_note"])

	def test_the_read_never_refuses_under_enforcement(self):
		self.set_mode("access_review", "Enforced")
		data = self.tool_data("generate_access_control_report", {"company": MAIN})
		self.assertTrue(data["control"]["enforced"])
		self.assertEqual(data["control"]["action"], "reported")


# ── 2 · change management ───────────────────────────────────────────────────
class AnApproverIsNotThePersonWhoMadeTheChange(ITGCTestCase):
	def test_a_self_approval_is_refused_by_the_tool(self):
		message = self.tool_error(
			"create_change_management_log",
			{
				"company": MAIN,
				"change_type": "Permission",
				"title": "Gave myself the keys",
				"description": "Added System Manager to my own account.",
				"changed_by": BOOKKEEPER,
				"approved_by": BOOKKEEPER,
			},
		)
		self.assertIn("not an approval", message)
		self.assertIn("Not Required", message)
		self.assertIn("Nothing was created", message)

	def test_a_self_approval_is_refused_by_the_controller_too(self):
		"""A row written in the Desk has to obey the same rule."""
		doc = frappe.new_doc("Change Management Log")
		doc.company = MAIN
		doc.change_type = "Permission"
		doc.title = "Gave myself the keys"
		doc.description = "Added System Manager to my own account."
		doc.changed_by = BOOKKEEPER
		doc.approved_by = BOOKKEEPER
		doc.change_date = "2026-07-20 09:00:00"
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("approved", str(caught.exception).lower())

	def test_a_documented_exception_is_accepted(self):
		data = self.a_change(
			approved_by="",
			approval_status="Not Required",
			notes="One-person finance function; the owner reviews the access report each quarter.",
		)
		self.assertEqual(data["approval_status"], "Not Required")
		self.assertEqual(data["control"]["action"], "none")

	def test_an_unapproved_permission_change_is_reported_and_allowed(self):
		data = self.a_change(approved_by="", approval_status="Pending")
		self.assertEqual(data["control"]["action"], "reported")
		self.assertEqual(data["control"]["finding_count"], 1)
		self.assertTrue(frappe.db.exists("Change Management Log", data["name"]))

	def test_enforced_refuses_and_writes_nothing(self):
		self.set_mode("change_approval", "Enforced")
		before = len(STORE.rows("Change Management Log"))
		message = self.tool_error(
			"create_change_management_log",
			{
				"company": MAIN,
				"change_type": "Permission",
				"title": "Granted a role",
				"description": "Added Accounts User.",
				"changed_by": BOOKKEEPER,
			},
		)
		self.assertIn("REFUSED", message)
		self.assertEqual(len(STORE.rows("Change Management Log")), before)

	def test_a_change_type_that_needs_no_approval_is_not_gated(self):
		data = self.a_change(change_type="Data Correction", approved_by="", approval_status="Pending")
		self.assertEqual(data["control"]["finding_count"], 0)

	def test_an_approval_dated_before_its_change_is_refused(self):
		message = self.tool_error(
			"create_change_management_log",
			{
				"company": MAIN,
				"change_type": "Permission",
				"title": "Granted a role",
				"description": "Added Accounts User.",
				"changed_by": BOOKKEEPER,
				"approved_by": MANAGER,
				"change_date": "2026-07-20 09:00:00",
				"approved_on": "2026-07-01 09:00:00",
			},
		)
		self.assertIn("before the change itself", message)


class TheChangeReportSaysWhatIsSelfAttested(ITGCTestCase):
	def test_it_splits_rows_this_app_wrote_from_rows_somebody_typed(self):
		self.a_change()
		self.a_change(title="Second change", source="MCP Tool")
		data = self.tool_data("get_change_management_report", {"company": MAIN})
		self.assertEqual(data["self_attestation"]["written_by_this_app"], 1)
		self.assertEqual(data["self_attestation"]["written_by_hand"], 1)
		self.assertIn("first question worth asking", data["self_attestation"]["why_this_matters"])

	def test_it_counts_approval_and_names_what_is_outstanding(self):
		self.a_change()
		outstanding = self.a_change(title="Unapproved", approved_by="", approval_status="Pending")
		data = self.tool_data("get_change_management_report", {"company": MAIN})
		self.assertEqual(data["approval"]["expected"], 2)
		self.assertEqual(data["approval"]["approved"], 1)
		self.assertIn(outstanding["name"], data["approval"]["outstanding_rows"])
		self.assertEqual(data["approval"]["rate"], 50.0)

	def test_a_high_risk_untested_change_is_named(self):
		change = self.a_change(risk_level="High")
		data = self.tool_data("get_change_management_report", {"company": MAIN})
		self.assertIn(change["name"], data["high_risk_untested"])

	def test_a_change_with_no_rollback_plan_is_named(self):
		change = self.a_change()
		data = self.tool_data("get_change_management_report", {"company": MAIN})
		self.assertIn(change["name"], data["no_rollback_plan"])

	def test_the_report_never_refuses_under_enforcement(self):
		self.a_change(approved_by="", approval_status="Pending")
		self.set_mode("change_approval", "Enforced")
		data = self.tool_data("get_change_management_report", {"company": MAIN})
		self.assertTrue(data["control"]["enforced"])
		self.assertEqual(data["control"]["action"], "reported")

	def test_a_window_the_wrong_way_round_is_refused(self):
		message = self.tool_error(
			"get_change_management_report",
			{"company": MAIN, "from_date": "2026-12-31", "to_date": "2026-01-01"},
		)
		self.assertIn("is after", message)


# ── 3 · backup and recovery ─────────────────────────────────────────────────
class NotTestedIsNotAFailureAndAGreenJobIsNotAVerification(ITGCTestCase):
	def test_a_successful_backup_is_not_verified(self):
		backup = self.a_backup()
		self.assertEqual(backup["test_restore_result"], "Not Tested")
		self.assertFalse(backup["verified"])
		self.assertIn("is a belief", backup["next_step"])

	def test_a_year_of_green_jobs_still_fails_the_verification_control(self):
		for day in ("2026-07-18", "2026-07-19", "2026-07-20"):
			self.a_backup(started_at=f"{day} 02:00:00", completed_at=f"{day} 02:40:00")
		data = self.tool_data("list_backup_records", {"company": MAIN})
		self.assertEqual(data["verified_count"], 0)
		self.assertEqual(data["control"]["finding_count"], 1)
		self.assertIn("green job log is not a verification", data["control"]["findings"][0]["remedy"])

	def test_a_passing_restore_clears_the_control(self):
		backup = self.a_backup()
		self.tool_data(
			"record_backup_test",
			{
				"backup_record": backup["name"],
				"test_restore_result": "Pass",
				"test_restore_on": "2026-07-20",
				"test_restore_notes": "Restored to staging and reconciled the trial balance.",
			},
		)
		data = self.tool_data("list_backup_records", {"company": MAIN})
		self.assertEqual(data["verified_count"], 1)
		self.assertEqual(data["control"]["finding_count"], 0)

	def test_a_partial_restore_does_not_verify(self):
		backup = self.a_backup()
		data = self.tool_data(
			"record_backup_test",
			{"backup_record": backup["name"], "test_restore_result": "Partial", "test_restore_on": "2026-07-20"},
		)
		self.assertFalse(data["verified"])
		self.assertIn("not a verification", data["note"])

	def test_a_failed_restore_is_the_most_valuable_row(self):
		backup = self.a_backup()
		data = self.tool_data(
			"record_backup_test",
			{"backup_record": backup["name"], "test_restore_result": "Fail", "test_restore_on": "2026-07-20"},
		)
		self.assertFalse(data["verified"])
		self.assertIn("most valuable row", data["note"])

	def test_not_tested_is_refused_as_a_test_result(self):
		backup = self.a_backup()
		message = self.tool_error(
			"record_backup_test", {"backup_record": backup["name"], "test_restore_result": "Not Tested"}
		)
		self.assertIn("is not a test", message)

	def test_a_future_dated_restore_is_refused(self):
		backup = self.a_backup()
		message = self.tool_error(
			"record_backup_test",
			{"backup_record": backup["name"], "test_restore_result": "Pass", "test_restore_on": "2099-01-01"},
		)
		self.assertIn("in the future", message)

	def test_an_undated_verification_is_refused_by_the_controller(self):
		doc = frappe.new_doc("Backup Record")
		doc.company = MAIN
		doc.backup_type = "Full"
		doc.status = "Success"
		doc.started_at = "2026-07-20 02:00:00"
		doc.completed_at = "2026-07-20 02:40:00"
		doc.location = "s3://orchard-backups/nightly"
		doc.test_restore_result = "Pass"
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("undated", str(caught.exception).lower() + " undated")

	def test_a_restore_slower_than_the_objective_is_reported(self):
		backup = self.a_backup(rto_hours=4)
		self.tool_data(
			"record_backup_test",
			{
				"backup_record": backup["name"],
				"test_restore_result": "Pass",
				"test_restore_on": "2026-07-20",
				"restore_duration_minutes": 540,
			},
		)
		data = self.tool_data("get_backup_record", {"backup_record": backup["name"]})
		self.assertFalse(data["met_rto"])
		self.assertIn("the number the system actually did", data["rto_note"])

	def test_a_completion_before_its_start_is_refused(self):
		message = self.tool_error(
			"create_backup_record",
			{
				"company": MAIN,
				"backup_type": "Full",
				"status": "Success",
				"started_at": "2026-07-20 02:00:00",
				"completed_at": "2026-07-19 02:00:00",
				"location": "s3://orchard-backups/nightly",
			},
		)
		self.assertIn("before Started At", message)

	def test_the_window_is_asked_of_the_company_not_of_the_page(self):
		"""A filtered list must not report the control as clear on a partial view."""
		verified = self.a_backup()
		self.tool_data(
			"record_backup_test",
			{"backup_record": verified["name"], "test_restore_result": "Pass", "test_restore_on": "2026-07-20"},
		)
		self.a_backup(status="Failed", completed_at="2026-07-20 02:05:00")
		data = self.tool_data("list_backup_records", {"company": MAIN, "status": "Failed"})
		self.assertEqual(data["control"]["finding_count"], 0)


class TheBackupControlShipsAdvisory(ITGCTestCase):
	def test_all_three_itgc_controls_ship_advisory(self):
		for control_point in ("access_review", "change_approval", "backup_verification"):
			with self.subTest(control_point=control_point):
				mode = frappe.db.get_value("Compliance Rule", self.rule_for(control_point), "enforcement_mode")
				self.assertEqual(mode, "Advisory")

	def test_every_itgc_control_has_a_seeded_rule_that_is_enabled(self):
		for control_point in ("access_review", "change_approval", "backup_verification"):
			with self.subTest(control_point=control_point):
				self.assertTrue(
					frappe.db.get_value("Compliance Rule", self.rule_for(control_point), "enabled")
				)
