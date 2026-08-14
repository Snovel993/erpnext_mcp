# SPDX-License-Identifier: MIT
"""The Farm Ops mobile HTTP surface — v0.17.1.

These are the tests for a transport that has NO transport gates. `mcp.handle`
runs `security.authorize()` — master switch, shared token, CIDR allowlist —
before it looks a tool up; a `@frappe.whitelist()` method reached directly runs
none of it. So every check that used to be somebody else's job is now this
package's, and every one of them is asserted here BY ITS ABSENCE FIRST: the test
that a gate works is a test that the call fails without it.

SEVEN CLAIMS.

1. **THE SURFACE IS ELEVEN METHODS AND CANNOT BE MORE.** `TheSurfaceIsClosed`.
   There is no dispatcher, so `create_journal_entry` and `convey_parcel` are not
   reachable at any path — asserted by enumerating what the modules actually
   export rather than by trusting the docstring.

2. **A LOGIN IS NOT ENOUGH.** `TheGatesRefuseWhatTheyShould`. Guest, a user with
   no field role, and an enrolled account whose grant was revoked are all
   refused with the SAME message, so a caller cannot learn which gate it failed.

3. **THE ADMIN IS NOT EXEMPT — THE ADMIN IS THE POINT.**
   `AnAdminIsNotEnrolled`. Administrator holds every role on the site, so the
   role gate alone would let the operator's own account drive the field API.
   The grant check is what stops it, and Tim's own account cannot call these
   until he grants himself mobile access on purpose.

4. **ENTITY SCOPING IS ENFORCED HERE, NOT INHERITED.** `TheScopingIsThisApps`.
   The tools read through `frappe.db.get_all`, which does not consult User
   Permissions, so a wrapper that trusted the framework would return the
   holding company's task board to an operating company's picker.

5. **THE SWITCHES AND LIMITS ARE REAL.** `TheKillSwitchStops` and
   `TheRateLimitTrips` drive them until they refuse, and check the refusals are
   503 and 429 rather than a 401 that would sign forty phones out.

6. **EVERY CALL LEAVES A ROW AND NO CALL LEAVES A SECRET.**
   `EveryCallIsAudited`, `NoSecretReachesThePhone`.

7. **THE PAYLOAD IS WHAT THE APP DECODES.** `TheAppCanDecodeThis` re-implements
   `LoginQRParser` and `FarmTask`'s decoder in Python and runs the server's real
   output through them. v0.17.0 shipped a QR that failed at `type` and eleven
   endpoints that 404'd; a test that only checked the server against itself is
   exactly what did not catch either.
"""

import base64
import hashlib
import inspect
import json
from typing import ClassVar
from unittest import mock

import frappe

from erpnext_mcp import audit, compat, roles, settings
from erpnext_mcp.api import files as files_api
from erpnext_mcp.api import guard
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.tools import mobile as mobile_tools

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import ROLES, STORE, set_roles
from .test_dispatch import WALK

WORKER = "ana@example.test"
WORKER_EMPLOYEE = "EMP-ANA"
OUTSIDER = "ben@example.test"
OUTSIDER_EMPLOYEE = "EMP-BEN"

ON = {
	f"allow_{name}": 1
	for name in (
		"create_mobile_user",
		"revoke_mobile_user",
		"generate_mobile_login_qr",
		"get_current_user_context",
		"create_parcel",
		"create_housing_unit",
		"create_farm_task",
		"assign_farm_task",
		"claim_farm_task",
		"refresh_compliance_alerts",
		"get_compliance_calendar",
	)
}


class MobileAPITestCase(V12TestCase):
	"""A site with one enrolled worker, and a way to call the API as them."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **ON)
		# `ROLES` is a module-level dict the whole suite shares, and these tests
		# rewrite Administrator's own role set to prove the grant gate holds
		# against an account that holds everything. Restoring it is not tidiness:
		# leaving it rewritten silently broke twelve workflow tests two files
		# away, which is the worst kind of failure to debug.
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		guard._BUCKETS.clear()
		roles.install_roles()
		STORE.seed(
			"Employee",
			[
				{
					"name": WORKER_EMPLOYEE,
					"employee_name": "Ana Ramos",
					"user_id": WORKER,
					"company": MAIN,
					"status": "Active",
				},
				{
					"name": OUTSIDER_EMPLOYEE,
					"employee_name": "Ben Ortiz",
					"user_id": OUTSIDER,
					"company": OTHER,
					"status": "Active",
				},
			],
		)
		self.enrol()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	# ── enrolment and impersonation ─────────────────────────────────────────
	def enrol(self, email=WORKER, name="Ana Ramos", role="Field Worker", entities=None):
		return self.tool_data(
			"create_mobile_user",
			{
				"email": email,
				"full_name": name,
				"role": role,
				"entity_access": entities or [MAIN],
			},
		)

	def be(self, user=WORKER, remote_addr="100.64.0.7"):
		"""Become one user, on a request that looks like a phone's.

		Sets the session directly rather than through an api-key header: Frappe
		has already authenticated by the time a whitelisted method runs, and what
		reaches this code is a session, not a header. Driving it any other way
		would be testing the double's own auth reproduction rather than the gates.
		"""
		self.request({}, headers={}, remote_addr=remote_addr)
		frappe.local.session.user = user
		return user

	# ── the site's furniture ────────────────────────────────────────────────
	def a_camp(self, unit_name="MC-Cabin-01"):
		if not STORE.rows("Parcel"):
			self.tool_data(
				"create_parcel", {"owning_entity": MAIN, "parcel_name": "Mill Creek", "acreage": 131.43}
			)
		self.unit = self.tool_data(
			"create_housing_unit",
			{
				"parcel": "Mill Creek",
				"unit_name": unit_name,
				"unit_type": "Cabin",
				"capacity": 4,
				"fsma_worker_facility": True,
			},
		)["name"]
		return self.unit

	def a_task(self, **overrides):
		payload = {
			"task_name": "Habitability walk — MC-Cabin-01",
			"task_type": "Inspection",
			"evidence_required": dict(WALK),
			"skill_required": "camp_maintenance",
			"company": MAIN,
		}
		payload.update(overrides)
		return self.tool_data("create_farm_task", payload)["name"]

	def audit_rows(self, method=None):
		rows = [
			row for row in STORE.rows("MCP Action Log") if str(row.get("tool_name", "")).startswith("mobile:")
		]
		if method:
			rows = [row for row in rows if row["tool_name"] == f"mobile:{method}"]
		return rows


# ── 1. the surface is closed ────────────────────────────────────────────────
class TheSurfaceIsClosed(MobileAPITestCase):
	#: Exactly what `MobileAPI.swift` names, and nothing else.
	MOBILE: ClassVar[set[str]] = {
		"get_current_user_context",
		"list_my_tasks",
		"list_available_tasks",
		"get_task",
		"claim_task",
		"start_task",
		"complete_task_via_mobile",
		"reject_task",
		"report_field_task",
		"list_compliance_alerts",
		"scan_asset",
		"get_asset_detail",
		"log_asset_state_change",
		"get_available_actions",
		"report_asset_issue",
		# v0.46.0 — the Identity step the wizard 404'd on.
		"create_employee",
		"search_employees",
		"reactivate_employee",
		# v0.46.2 — the returning worker's branch, between the search and the rehire.
		"get_employee",
		# v0.45.0 — onboarding, the bucket sync and the crew clock.
		"create_i9_form",
		"submit_i9_section_1",
		"submit_i9_section_2",
		# v0.47.0 — the I-9's document lookup, replacing a hardcoded Swift array,
		# and Section 3, which is the branch a returning worker's expired I-9 takes.
		"list_i9_document_types",
		"reverify_i9",
		# v0.47.1 — the I-9 read the wizard never had, the federal form filled from
		# the record, and the signed sheet photographed back onto it.
		"get_i9_form",
		"generate_i9_pdf",
		"upload_signed_i9",
		# v0.48.0 — the authorized signer roster the Section 2 screen reads
		# before it offers a name, and the three calls that maintain it.
		"list_authorized_signers",
		"add_authorized_signer",
		"update_authorized_signer",
		"remove_authorized_signer",
		"submit_w4",
		# v0.48.0 — the W-4 as a federal form rather than a doctype.
		"generate_w4_pdf",
		"link_badge_to_employee",
		# v0.50.0 — the read between a scan and a name. `add_worker_to_shift`
		# takes an Employee docname and a camera produces a badge string, so
		# until this existed the crew clock could scan a crew and roster none of
		# it, and the capture loop could show a foreman a code but never a name.
		"resolve_badge",
		"generate_employee_badge_qr",
		"set_employee_photo",
		"sync_bucket_entries",
		"start_shift",
		"add_worker_to_shift",
		"end_shift",
		# v0.48.3 — the second half of an onboarding upload. Without it the
		# wizard's six photographs and signatures went to Frappe's own
		# `/api/method/upload_file`, which this app's auth hook does not look at,
		# and every one of them was lost against a 200 and a login page.
		"attach_onboarding_document",
		# v0.57.0 — the compliance tab stops being a noticeboard.
		# `MobileAPI.swift` names both of these already: `dismissComplianceAlert`
		# is the gated close, and `submitFormSignature` is the pad's own call,
		# which is `collect_signature`'s write under the argument names
		# `API_CONTRACT.md` §14.2 posts. The app has never named
		# `collect_signature`, which is why that one stays below.
		"dismiss_compliance_alert",
		"submit_form_signature",
		# v0.62.0 — the seven `MobileAPI.swift` names and this surface answered
		# with a 404. Three are the methods below under the name and the argument
		# spellings the app actually posts; four had no method here at all. Every
		# one of the seven is named by a constant in `MobileAPI.swift` today,
		# which is what puts them in THIS set rather than in the pending one.
		"list_org_reference_data",
		"list_housing_units",
		"create_housing_assignment",
		"set_employee_org_fields",
		"set_employee_contact_fields",
		"list_attachments",
		"get_attachment_content",
	}
	FILES: ClassVar[set[str]] = {"stage_file_chunk", "finalize_staged_file"}

	#: v0.52.0. `get_active_model` / `get_model_file_chunk` ship server-side
	#: ahead of the client that will call them — `ModelDownloadService.swift`'s
	#: own header still says "ERPNext's MCP server is only reachable by Claude
	#: tooling, not by the iOS app at runtime", which is exactly the
	#: assumption this route removes, and the Swift-side cutover from querying
	#: Volume Vision directly to calling this route is separate, tracked work.
	#: Listed by name, not folded into MOBILE, so this file does not claim
	#: `MobileAPI.swift` names them until it actually does.
	#:
	#: v0.53.0 adds `get_employee_badge_pass` on the same footing and for the
	#: same reason: the server builds the `.pkpass` and the Google save link, and
	#: the handset side — a share sheet that writes the bytes to a temporary file
	#: with the `com.apple.pkpass` type on it so AirDrop opens it in Wallet — is
	#: separate, tracked work. Listing it here rather than in `MOBILE` keeps this
	#: file from claiming `MobileAPI.swift` names a method it does not.
	#: v0.54.0 adds the hiring wizard's Assignment and Housing steps on the same
	#: footing again. The handset already scans a licence and matches a name; the
	#: four Assignment dropdowns are still a Swift array compiled into the app,
	#: and there is no Housing step at all. These three are the server half, and
	#: `MobileAPI.swift` names none of them yet — so they are listed HERE rather
	#: than in `MOBILE`, and `test_ios_contract` transcribes no mirror for them,
	#: because a mirror of a Codable that does not exist would invent the
	#: contract instead of copying it. They move up to `MOBILE` in the release
	#: that lands the Swift side.
	#: v0.55.0 adds `collect_signature` on the same footing. The server side is
	#: complete — the alert finds the empty box, the task routes it, and this
	#: method files the capture — and the handset side is a signature pad opened
	#: over a task's `subject_docname`, which `MobileAPI.swift` does not name
	#: yet. Listed here rather than in `MOBILE` so this file keeps claiming only
	#: what the Swift actually calls.
	#: v0.63.0 adds `get_document_preview` and `seal_signed_document` on the same
	#: footing, and the first of the two is unusual in that the CONTRACT asked for
	#: it before the client could call it: `API_CONTRACT.md` §17.5 names the
	#: presentation step as a server-side gap and says "the fix is one route".
	#: This is that route, and `MobileAPI.swift` does not name it yet — the
	#: handset side is a viewer on the `.reviewing` step, which is separate,
	#: tracked work. `seal_signed_document` may never need naming at all: the
	#: ordinary flow gets its seal from `submit_form_signature`, and this method
	#: exists for a form signed before v0.63.0 or one signed in the Desk.
	#: v0.65.0 adds `universal_scan` on the same footing. The server side is the
	#: whole of it — one string in, whichever register holds it out — and the
	#: handset side is a scanner screen that stops asking the worker what they
	#: are about to scan, which `MobileAPI.swift` does not name yet. Listed here
	#: rather than in `MOBILE` so this file keeps claiming only what the Swift
	#: actually calls.
	#: v0.67.0 adds the four receipt-capture methods on the same footing. This is
	#: Sprint 2's SERVER side: the two new registers exist, the classifier that
	#: decides which one a photograph belongs in exists, and the capture screen
	#: that would call them is the iOS half of the same sprint. Listed here
	#: rather than in `MOBILE` so this file keeps claiming only what
	#: `MobileAPI.swift` actually names — and note that `create_expense_receipt`
	#: is among them even though the app has photographed receipts since v0.31.0:
	#: it reached `submit_expense_receipt` through the MCP surface, and this is
	#: the first time the flow has a route of its own.
	#: Sprint 3 (v0.68.0) adds the six compliance-alert-rectification methods on
	#: the same footing. The server side is the whole of it — every alert now
	#: names a `rectification` object and there is a route behind every one of
	#: them — and the handset side is the button `ComplianceAlertDetailView.swift`
	#: does not draw yet, which is separate, tracked work per the sprint's own
	#: scope: server-side sidecar first, the phone reads it and draws a button
	#: after. Listed here rather than in `MOBILE` so this file keeps claiming
	#: only what `MobileAPI.swift` actually names.
	PENDING_IOS_INTEGRATION: ClassVar[set[str]] = {
		"universal_scan",
		"classify_receipt",
		"create_expense_receipt",
		"create_scale_ticket",
		"list_scale_tickets",
		"collect_signature",
		"get_document_preview",
		"seal_signed_document",
		"get_active_model",
		"get_model_file_chunk",
		"get_employee_badge_pass",
		"list_onboarding_reference_data",
		"list_available_housing",
		"assign_housing",
		"log_shift_break",
		"end_shift_break",
		"get_break_policy",
		"clock_out_worker",
		"get_shift_production",
		"get_shift",
		"renew_certification",
		"record_training",
		"sign_training_supervisor_review",
		"update_regulatory_filing",
		"advance_policy_review",
		"rectify_alert",
	}

	def _whitelisted(self, module):
		return {
			name
			for name in dir(module)
			if not name.startswith("_") and getattr(getattr(module, name), "farm_ops_method", None)
		}

	def test_the_mobile_module_publishes_exactly_the_ten_the_app_calls(self):
		self.assertEqual(self._whitelisted(mobile_api) - self.PENDING_IOS_INTEGRATION, self.MOBILE)

	def test_the_files_module_publishes_exactly_the_two_the_app_calls(self):
		self.assertEqual(self._whitelisted(files_api), self.FILES)

	def test_no_admin_tool_is_reachable_at_any_path_in_this_package(self):
		"""The one that matters. A generic dispatcher would have published these."""
		for dangerous in (
			"create_journal_entry",
			"submit_journal_entry",
			"convey_parcel",
			"import_chart_of_accounts",
			"create_mobile_user",
			"generate_mobile_login_qr",
			"revoke_api_token",
			"run_report",
		):
			self.assertFalse(hasattr(mobile_api, dangerous), dangerous)
			self.assertFalse(hasattr(files_api, dangerous), dangerous)

	def test_there_is_no_generic_dispatcher_taking_a_tool_name(self):
		for module in (mobile_api, files_api):
			for suspect in ("call", "invoke", "dispatch_tool", "handle", "run"):
				attribute = getattr(module, suspect, None)
				self.assertFalse(
					callable(attribute) and getattr(attribute, "farm_ops_method", None),
					f"{module.__name__}.{suspect} is published",
				)


# ── 2. the gates ────────────────────────────────────────────────────────────
class TheGatesRefuseWhatTheyShould(MobileAPITestCase):
	REFUSAL = "enrolled Farm Ops credential"

	def test_guest_is_refused_before_anything_is_read(self):
		self.be("Guest")
		with self.assertRaises(frappe.PermissionError) as caught:
			mobile_api.get_current_user_context()
		self.assertIn(self.REFUSAL, str(caught.exception))

	def test_a_login_with_no_field_role_is_refused(self):
		"""A Family Member and an Advisor are real logins on this site."""
		STORE.seed("User", [{"name": "aunt@example.test", "enabled": 1, "full_name": "Aunt"}])
		set_roles("aunt@example.test", ["Family Member", "Advisor"])
		self.be("aunt@example.test")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.list_my_tasks()

	def test_a_field_role_with_no_grant_is_refused(self):
		"""Holding the role is not being enrolled. The grant is the enrolment."""
		STORE.seed("User", [{"name": "casual@example.test", "enabled": 1, "full_name": "Casual"}])
		set_roles("casual@example.test", ["Field Worker"])
		self.be("casual@example.test")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.list_my_tasks()

	def test_a_revoked_grant_closes_the_door_on_the_very_next_call(self):
		self.be()
		self.assertTrue(mobile_api.get_current_user_context()["user"])

		frappe.local.session.user = "Administrator"
		self.tool_data("revoke_mobile_user", {"email": WORKER, "reason": "left at the end of harvest"})
		self.assertEqual(frappe.db.get_value("Mobile Access Grant", WORKER, "state"), "Revoked")

		self.be()
		with self.assertRaises(frappe.PermissionError):
			mobile_api.get_current_user_context()

	def test_every_refusal_reads_the_same_so_nothing_is_learned_from_probing(self):
		"""Telling a caller which gate it failed hands it a free oracle."""
		messages = set()
		for user, roles_held in (
			("Guest", []),
			("noroles@example.test", []),
			("norole2@example.test", ["Family Member"]),
			("nogrant@example.test", ["Foreman"]),
		):
			if user != "Guest":
				STORE.seed("User", [{"name": user, "enabled": 1, "full_name": user}])
				set_roles(user, roles_held)
			self.be(user)
			with self.assertRaises(frappe.PermissionError) as caught:
				mobile_api.list_my_tasks()
			messages.add(str(caught.exception))
		self.assertEqual(len(messages), 1, messages)


class ThePersonnelRegisterIsNotAPickersToRead(MobileAPITestCase):
	"""v0.46.0. `search_employees` is the only READ on this surface with a role
	gate of its own, and the reason is worth a test rather than a comment: every
	other read is field work a picker is entitled to, and this one is the entity's
	whole personnel register — names, hire dates, employment types, and the people
	who have left. The writing methods inherit the same gate from
	`tools/employee.py`; this one applies it by hand, so it is the one that can
	quietly stop applying."""

	def test_a_field_worker_with_a_perfectly_good_grant_is_still_refused(self):
		self.be()
		with self.assertRaises(Exception) as caught:
			mobile_api.search_employees(query="Ramos")
		self.assertIn("personnel register", str(caught.exception))

	def test_and_a_farm_manager_is_not(self):
		"""The role an operator actually enrols an onboarding phone as."""
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		self.be()
		self.assertIn("employees", mobile_api.search_employees(query="Ramos"))

	def test_a_field_worker_cannot_create_or_reactivate_one_either(self):
		self.be()
		for call in (
			lambda: mobile_api.create_employee(first_name="Elena", last_name="Marquez", company=MAIN),
			lambda: mobile_api.reactivate_employee(employee="EMP-ANA"),
		):
			with self.assertRaises(Exception) as caught:
				call()
			self.assertIn("personnel register", str(caught.exception))

	def test_a_field_worker_cannot_read_somebody_elses_record(self):
		"""v0.46.2. `get_employee` is the one read here whose gate has a hole in
		it, so this is the half of the hole that must stay shut."""
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-COLLEAGUE",
					"employee_name": "Rosa Aguilar",
					"company": MAIN,
					"status": "Active",
				}
			],
		)
		self.be()
		with self.assertRaises(Exception) as caught:
			mobile_api.get_employee(employee="EMP-COLLEAGUE")
		self.assertIn("personnel register", str(caught.exception))

	def test_and_can_read_their_own_without_the_hr_role(self):
		"""The other half, and the reason the exception is there: a picker checking
		what their own onboarding still needs is not browsing the register."""
		self.be()
		row = mobile_api.get_employee(employee=WORKER_EMPLOYEE)
		self.assertEqual(row["name"], WORKER_EMPLOYEE)
		self.assertEqual(row["employee_name"], "Ana Ramos")

	def test_the_self_exception_cannot_be_claimed_by_naming_somebody(self):
		"""The caller's own record is resolved through `Employee.user_id` and never
		from the body, so there is nothing in a request that can assert it.

		Ben is enrolled into MAIN here, so entity scoping is NOT what refuses him —
		he can reach Ana's company perfectly well. The only thing between him and
		her record is the HR role he does not hold."""
		self.enrol(email=OUTSIDER, name="Ben Ortiz", entities=[MAIN])
		self.be(OUTSIDER)
		with self.assertRaises(Exception) as caught:
			mobile_api.get_employee(employee=WORKER_EMPLOYEE)
		self.assertIn("personnel register", str(caught.exception))

	def test_a_farm_manager_reads_anybody_in_their_own_entities(self):
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-COLLEAGUE",
					"employee_name": "Rosa Aguilar",
					"company": MAIN,
					"status": "Active",
				}
			],
		)
		self.be()
		self.assertEqual(mobile_api.get_employee(employee="EMP-COLLEAGUE")["name"], "EMP-COLLEAGUE")

	def test_but_not_outside_them_even_holding_every_hr_role(self):
		"""Entity scoping is not the role gate and does not bend to it."""
		set_roles(WORKER, ["Field Worker", "Farm Manager", "HR Manager"])
		self.be()
		with self.assertRaises(Exception) as caught:
			mobile_api.get_employee(employee=OUTSIDER_EMPLOYEE)
		self.assertIn("not found", str(caught.exception).lower())


class AnAdminIsNotEnrolled(MobileAPITestCase):
	def test_administrator_holds_every_role_and_still_cannot_call_the_field_api(self):
		"""The reason the grant check exists. Tim's own account is not exempt."""
		set_roles("Administrator", ["System Manager", "Farm Manager", "Foreman", "Field Worker"])
		self.be("Administrator")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.list_available_tasks()

	def test_and_can_once_somebody_deliberately_enrols_it(self):
		"""The gate is a decision, not a wall: enrolling on purpose opens it."""
		self.enrol(email="tim@example.test", name="Tim Polehn", role="Farm Manager")
		set_roles("tim@example.test", ["System Manager", "Farm Manager"])
		self.be("tim@example.test")
		self.assertEqual(mobile_api.get_current_user_context()["user"], "tim@example.test")


# ── 3. entity scoping ───────────────────────────────────────────────────────
class TheScopingIsThisApps(MobileAPITestCase):
	def setUp(self):
		super().setUp()
		self.a_camp()
		self.mine = self.a_task()
		self.theirs = self.a_task(task_name="Highland walk", company=OTHER)

	def test_a_company_the_caller_cannot_reach_is_refused_not_quietly_emptied(self):
		self.be()
		with self.assertRaises(frappe.PermissionError) as caught:
			mobile_api.list_my_tasks(company=OTHER)
		self.assertIn(OTHER, str(caught.exception))

	def test_the_pool_never_carries_another_entitys_work(self):
		self.be()
		names = {row["name"] for row in mobile_api.list_available_tasks()["tasks"]}
		self.assertIn(self.mine, names)
		self.assertNotIn(self.theirs, names)

	def test_a_task_in_another_entity_is_not_found_rather_than_forbidden(self):
		"""Both refusals read the same, so docnames cannot be mapped by probing."""
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.get_task(task=self.theirs)

	def test_another_entitys_task_cannot_be_claimed(self):
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.claim_task(task=self.theirs)

	def test_another_entitys_task_cannot_be_completed(self):
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.complete_task_via_mobile(task=self.theirs, clean_pass=True)

	def test_an_account_with_no_entity_access_is_refused_rather_than_shown_everything(self):
		"""Frappe's rule is that no User Permission means UNRESTRICTED. On an
		endpoint reachable from the internet that default is exactly backwards."""
		for row in list(STORE.rows("User Permission")):
			if row.get("user") == WORKER:
				frappe.delete_doc("User Permission", row["name"], force=True, ignore_permissions=True)
		self.be()
		with self.assertRaises(frappe.PermissionError) as caught:
			mobile_api.list_my_tasks()
		self.assertIn("no entity access", str(caught.exception))

	def test_the_scoped_filter_drops_a_row_the_tool_layer_let_through(self):
		"""The belt to the braces, driven directly: a row that escapes the query
		filter through some future code path still must not leave the building."""
		rows = [{"name": "FT-1", "company": MAIN}, {"name": "FT-2", "company": OTHER}, {"name": "FT-3"}]
		kept = {row["name"] for row in guard.scoped(rows, [MAIN])}
		self.assertEqual(kept, {"FT-1", "FT-3"})


# ── 4. input validation ─────────────────────────────────────────────────────
class NothingIsPassedThroughBlind(MobileAPITestCase):
	def setUp(self):
		super().setUp()
		self.a_camp()
		self.task = self.a_task()

	def test_a_docname_that_does_not_exist_is_refused_before_delegation(self):
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.get_task(task="FT-does-not-exist")

	def test_a_missing_docname_is_refused_by_name(self):
		self.be()
		with self.assertRaises(frappe.ValidationError):
			mobile_api.get_task(task="")

	def test_an_assignment_from_another_task_cannot_be_smuggled_in(self):
		"""The one argument that could otherwise move work between records."""
		other = self.a_task(task_name="Second walk")
		self.be()
		mobile_api.claim_task(task=self.task)
		mine = frappe.db.get_value("Farm Task Assignment", {"task": self.task}, "name")
		with self.assertRaises(frappe.ValidationError) as caught:
			mobile_api.start_task(task=other, task_assignment=mine)
		self.assertIn("does not belong to", str(caught.exception))

	def test_a_rejection_always_hands_the_task_back_and_never_cancels_it(self):
		"""`reject_farm_task` takes cancel=true, which would delete the work."""
		self.be()
		mobile_api.claim_task(task=self.task)
		mobile_api.reject_task(task=self.task, reason="the ladder is broken")
		self.assertEqual(frappe.db.get_value("Farm Task", self.task, "state"), "Available")

	def test_the_cancel_argument_is_not_even_in_the_wrappers_signature(self):
		"""Frappe drops a body key a whitelisted method does not declare, so an
		argument that is absent from the signature is one no phone can send."""
		import inspect

		accepted = set(inspect.signature(mobile_api.reject_task).parameters)
		self.assertEqual(accepted, {"user", "task", "task_assignment", "reason"})
		for wrapper, forbidden in (
			(mobile_api.complete_task_via_mobile, ("record_data", "worker_id", "signature_file")),
			(mobile_api.list_my_tasks, ("worker_id", "user_id")),
			(files_api.finalize_staged_file, ("attach_to_doctype", "attach_to_name", "is_private")),
			(files_api.stage_file_chunk, ("attach_to_doctype", "governance_document")),
		):
			accepted = set(inspect.signature(wrapper).parameters)
			for name in forbidden:
				self.assertNotIn(name, accepted, f"{wrapper.__name__} accepts {name}")

	def test_a_rejection_with_no_reason_is_refused(self):
		self.be()
		mobile_api.claim_task(task=self.task)
		with self.assertRaises(frappe.ValidationError):
			mobile_api.reject_task(task=self.task, reason="   ")

	def test_an_evidence_file_type_that_is_not_evidence_is_refused(self):
		self.be()
		for name in ("payload.html", "logo.svg", "run.sh", "note.php"):
			with self.assertRaises(frappe.ValidationError, msg=name):
				files_api.stage_file_chunk(
					upload_id="u1", file_name=name, chunk_index=0, chunk_count=1, total_bytes=3, data="YWJj"
				)

	def test_a_filename_cannot_carry_a_path(self):
		self.be()
		result = files_api.stage_file_chunk(
			upload_id="u2",
			file_name="../../../etc/passwd/north-wall.jpg",
			chunk_index=0,
			chunk_count=1,
			total_bytes=3,
			data="YWJj",
		)
		self.assertEqual(result["file_name"], "north-wall.jpg")


# ── 5. the switches and the limits ──────────────────────────────────────────
class TheKillSwitchStops(MobileAPITestCase):
	def test_the_settings_field_stops_every_method(self):
		self.configure(enabled=1, farm_ops_mobile_enabled=0, **ON)
		self.be()
		for call in (
			mobile_api.get_current_user_context,
			mobile_api.list_my_tasks,
			mobile_api.list_available_tasks,
			mobile_api.list_compliance_alerts,
		):
			with self.assertRaises(guard.MobileDisabled):
				call()

	def test_site_config_can_stop_it_without_the_desk(self):
		frappe.conf["farm_ops_mobile_enabled"] = 0
		self.be()
		with self.assertRaises(guard.MobileDisabled):
			mobile_api.list_my_tasks()

	def test_it_answers_503_and_not_401_so_no_phone_is_signed_out(self):
		"""FarmOpsKit treats 401 as 'credential dead, sign out and re-scan'. A
		kill switch that answered 401 would lose every queued completion."""
		self.assertEqual(guard.MobileDisabled.http_status_code, 503)
		self.assertEqual(guard.RateLimited.http_status_code, 429)

	def test_it_is_a_separate_switch_from_the_mcp_master_one(self):
		"""Stopping the AI and stopping the phones are different decisions."""
		self.configure(enabled=0, farm_ops_mobile_enabled=1, **ON)
		self.be()
		self.assertTrue(mobile_api.get_current_user_context()["user"])

	def test_it_ships_on(self):
		self.assertTrue(settings.farm_ops_mobile_enabled())


class TheRateLimitTrips(MobileAPITestCase):
	def test_a_read_survives_a_pull_to_refresh_and_stops_at_the_limit(self):
		self.be()
		for _ in range(guard.READ_LIMIT):
			mobile_api.get_current_user_context()
		with self.assertRaises(guard.RateLimited):
			mobile_api.get_current_user_context()

	def test_a_state_change_gets_a_much_tighter_limit(self):
		self.a_camp()
		tasks = [self.a_task(task_name=f"walk {index}") for index in range(guard.WRITE_LIMIT + 1)]
		self.be()
		for name in tasks[:-1]:
			try:
				mobile_api.claim_task(task=name)
			except guard.RateLimited:
				raise
			except Exception:
				# The concurrent-claim limit refuses long before the rate limit
				# does. That refusal is Sprint 8's and is tested there; what
				# matters here is that it still COUNTS against the window.
				pass
		with self.assertRaises(guard.RateLimited):
			mobile_api.claim_task(task=tasks[-1])

	def test_the_limit_is_per_user_not_per_site(self):
		"""One worker burning their allowance must not lock the crew out."""
		self.enrol(email=OUTSIDER, name="Ben Ortiz", entities=[MAIN])
		self.be()
		for _ in range(guard.READ_LIMIT):
			mobile_api.get_current_user_context()
		self.be(OUTSIDER)
		self.assertEqual(mobile_api.get_current_user_context()["user"], OUTSIDER)


# ── 6. the audit trail and the secrets ──────────────────────────────────────
class EveryCallIsAudited(MobileAPITestCase):
	def test_a_successful_call_writes_one_row_naming_the_caller_and_the_ip(self):
		self.be(remote_addr="100.64.0.7")
		mobile_api.get_current_user_context()
		rows = self.audit_rows("get_current_user_context")
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["result_status"], audit.STATUS_SUCCESS)
		self.assertIn(WORKER, rows[0]["result_summary"])
		self.assertEqual(rows[0]["caller_ip"], "100.64.0.7")

	def test_a_refused_call_is_logged_too_and_says_it_was_a_permission_failure(self):
		STORE.seed("User", [{"name": "casual@example.test", "enabled": 1, "full_name": "Casual"}])
		set_roles("casual@example.test", ["Field Worker"])
		self.be("casual@example.test")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.list_my_tasks()
		rows = self.audit_rows("list_my_tasks")
		self.assertEqual(rows[0]["result_status"], audit.STATUS_UNAUTHORIZED)
		self.assertIn("permission_error", rows[0]["result_summary"])

	def test_a_rate_limited_call_is_logged_as_blocked(self):
		self.be()
		for _ in range(guard.READ_LIMIT + 1):
			try:
				mobile_api.get_current_user_context()
			except guard.RateLimited:
				break
		blocked = [row for row in self.audit_rows() if row["result_status"] == audit.STATUS_BLOCKED]
		self.assertTrue(blocked)
		self.assertIn("rate_limited", blocked[-1]["result_summary"])

	def test_a_kill_switched_call_is_logged_as_blocked(self):
		self.configure(enabled=1, farm_ops_mobile_enabled=0, **ON)
		self.be()
		with self.assertRaises(guard.MobileDisabled):
			mobile_api.list_my_tasks()
		rows = self.audit_rows("list_my_tasks")
		self.assertEqual(rows[0]["result_status"], audit.STATUS_BLOCKED)
		self.assertIn("disabled", rows[0]["result_summary"])

	def test_the_rows_go_to_the_action_log_and_not_into_the_compliance_register(self):
		"""Forty phones polling would bury a real auditor's Audit Event list."""
		before = len(STORE.rows("Audit Event"))
		self.be()
		mobile_api.get_current_user_context()
		mobile_api.list_my_tasks()
		self.assertEqual(len(STORE.rows("Audit Event")), before)
		self.assertEqual(len(self.audit_rows()), 2)

	def test_the_arguments_are_recorded_so_a_completion_can_be_reconstructed(self):
		self.a_camp()
		task = self.a_task()
		self.be()

		try:
			mobile_api.complete_task_via_mobile(task=task, clean_pass=True, latitude=45.67, longitude=-121.17)
		except Exception:
			pass
		row = self.audit_rows("complete_task_via_mobile")[0]
		recorded = json.loads(row["arguments_json"])
		self.assertEqual(recorded["task"], task)
		self.assertEqual(recorded["latitude"], 45.67)


class NoSecretReachesThePhone(MobileAPITestCase):
	def test_a_credential_shaped_key_is_stripped_at_any_depth(self):
		payload = {
			"user": WORKER,
			"api_key": "live",
			"api_secret": "live",
			"auth_header": "token a:b",
			"grant": {"api_key": "live", "state": "Active", "nested": [{"password": "x", "ok": 1}]},
		}
		cleaned = guard.strip_secrets(payload)
		self.assertNotIn("api_key", json.dumps(cleaned))
		self.assertNotIn("api_secret", json.dumps(cleaned))
		self.assertNotIn("auth_header", cleaned)
		self.assertEqual(cleaned["grant"]["state"], "Active")
		self.assertEqual(cleaned["grant"]["nested"][0]["ok"], 1)

	def test_the_file_handle_the_app_needs_survives_the_strip(self):
		"""`file_token` trips the substring rule and carries no secret."""
		self.assertEqual(guard.strip_secrets({"file_token": "abc"}), {"file_token": "abc"})

	def test_the_user_context_a_phone_receives_carries_no_credential(self):
		self.be()
		text = json.dumps(mobile_api.get_current_user_context(), default=str)
		for hint in ("api_secret", "api_key", "auth_header"):
			self.assertNotIn(hint, text)


# ── 7. the app can actually decode what the server sends ────────────────────
def parse_login_qr(raw: str, allow_insecure_http: bool = False) -> dict:
	"""`FarmOpsKit.LoginQRParser.parse`, re-implemented line for line.

	NOT a paraphrase of what the Swift is believed to do — a transcription of
	`FarmOpsKit/Sources/FarmOpsKit/Auth/ServerCredentials.swift`, in the same
	order, with the same refusals. v0.17.0 shipped a payload the real parser
	rejected at its first check while every server-side test passed, because
	every server-side test asked the server whether it agreed with itself.

	Raises ValueError carrying the parser's own error case name.
	"""
	try:
		payload = json.loads(raw)
	except (ValueError, TypeError):
		raise ValueError("notJSON") from None
	if not isinstance(payload, dict):
		raise ValueError("notJSON")

	if payload.get("type") != "farm_ops_login":
		raise ValueError(f"wrongType({payload.get('type') or ''})")
	if int(payload.get("v") or 1) > 1:
		raise ValueError(f"unsupportedVersion({payload.get('v')})")

	missing = [
		label
		for key, label in (
			("url", "server address"),
			("user", "user"),
			("api_key", "API key"),
			("api_secret", "API secret"),
		)
		if not str(payload.get(key) or "")
	]
	if missing:
		raise ValueError(f"missingFields({', '.join(missing)})")

	trimmed = str(payload["url"]).strip(" /")
	if "://" not in trimmed or not trimmed.split("://", 1)[1]:
		raise ValueError(f"badURL({payload['url']})")
	if trimmed.split("://", 1)[0].lower() != "https" and not allow_insecure_http:
		raise ValueError("insecureURL")

	return {
		"baseURL": trimmed,
		"user": payload["user"],
		"apiKey": payload["api_key"],
		"apiSecret": payload["api_secret"],
		"authorizationHeader": f"token {payload['api_key']}:{payload['api_secret']}",
	}


class TheAppCanDecodeThis(MobileAPITestCase):
	def a_qr(self, **overrides):
		payload = dict(self.tool_data("generate_mobile_login_qr", {"user": WORKER, **overrides})["payload"])
		return json.dumps(payload, separators=(",", ":"), sort_keys=True)

	def test_a_real_login_qr_passes_the_real_parser(self):
		"""The v0.17.0 bug, asserted as fixed at the point it actually broke."""
		credentials = parse_login_qr(self.a_qr())
		self.assertEqual(credentials["user"], WORKER)
		self.assertEqual(credentials["baseURL"], "https://umbrel.tail4a2b.ts.net")
		self.assertTrue(credentials["apiKey"])
		self.assertTrue(credentials["apiSecret"])
		self.assertEqual(
			credentials["authorizationHeader"],
			f"token {credentials['apiKey']}:{credentials['apiSecret']}",
		)

	def test_the_type_is_the_constant_the_app_checks_for_by_name(self):
		payload = json.loads(self.a_qr())
		self.assertEqual(payload["type"], "farm_ops_login")
		self.assertEqual(payload["type"], mobile_tools.LOGIN_QR_TYPE)

	def test_the_parser_this_test_uses_is_strict_enough_to_have_caught_v0_17_0(self):
		"""A transcription that accepted the old payload would prove nothing."""
		old = json.loads(self.a_qr())
		old.pop("type")
		with self.assertRaises(ValueError) as caught:
			parse_login_qr(json.dumps(old))
		self.assertIn("wrongType", str(caught.exception))

	def test_a_farmcore_onboarding_code_is_still_refused_by_name(self):
		"""The two apps must never cross-sign."""
		with self.assertRaises(ValueError) as caught:
			parse_login_qr(json.dumps({"type": "farm_app_nostr_link", "v": 1, "url": "https://x.test"}))
		self.assertIn("farm_app_nostr_link", str(caught.exception))

	def test_a_plain_http_endpoint_is_still_refused_at_both_ends(self):
		self.configure(enabled=1, public_url="http://umbrel.local", **ON)
		message = self.tool_error("generate_mobile_login_qr", {"user": WORKER})
		self.assertIn("not HTTPS", message)

	def test_the_task_payload_carries_every_key_the_ios_model_decodes(self):
		"""`FarmTask`'s CodingKeys, checked against what the server emits."""
		self.a_camp()
		task = self.a_task()
		self.be()
		row = mobile_api.get_task(task=task)
		for key in (
			"name",
			"task_name",
			"task_type",
			"state",
			"urgency",
			"dispatch_mode",
			"estimated_duration_minutes",
			"skill_required",
			"notes",
			"company",
			"location",
			"location_type",
			"evidence_required",
			"creates_record",
			"source_alert",
			"assignment",
			"assigned_to",
			"claimed_at",
			"started_at",
		):
			self.assertIn(key, row, f"FarmTask decodes {key} and the server does not emit it")

	def test_location_type_is_emitted_alongside_the_doctypes_own_spelling(self):
		unit = self.a_camp()
		task = self.a_task(location_doctype="Housing Unit", location=unit)
		self.be()
		row = mobile_api.get_task(task=task)
		self.assertEqual(row["location_type"], "Housing Unit")
		self.assertEqual(row["location_doctype"], "Housing Unit")

	def test_coordinates_are_omitted_rather_than_zeroed_when_the_site_has_none(self):
		"""0,0 is a real place in the Gulf of Guinea."""
		unit = self.a_camp()
		task = self.a_task(location_doctype="Housing Unit", location=unit)
		self.be()
		row = mobile_api.get_task(task=task)
		self.assertNotIn("latitude", row)
		self.assertNotIn("longitude", row)

	def test_the_user_context_carries_every_key_the_ios_model_decodes(self):
		self.be()
		context = mobile_api.get_current_user_context()
		for key in ("user", "full_name", "employee", "roles", "companies", "default_company", "skills"):
			self.assertIn(key, context, f"UserContext decodes {key}")
		self.assertEqual(context["default_company"], MAIN)
		self.assertEqual([entry["name"] for entry in context["companies"]], [MAIN])
		self.assertIn("abbr", context["companies"][0])
		self.assertIn("Field Worker", context["roles"])

	def test_a_task_raised_by_an_alert_explains_itself_verbatim(self):
		"""The app hides the 'Why this task exists' card without this, and an
		inspection with no stated reason is worse than none."""
		self.a_camp()
		self.tool_data("refresh_compliance_alerts", {"company": MAIN})
		alert = next(iter(STORE.rows("Compliance Alert")), None)
		if alert is None:
			self.skipTest("this fixture raised no compliance alert")
		task = self.a_task(source_alert=alert["name"])
		self.be()
		row = mobile_api.get_task(task=task)
		self.assertEqual(row["source_alert"], alert["name"])
		self.assertEqual(row["source_alert_explanation"], alert["alert_message"])


# ── 8. the whole flow, end to end, through the transport the app uses ───────
class TheWholeFlowWorks(MobileAPITestCase):
	"""Scan to compliance record, over the eleven whitelisted methods only.

	This is the test v0.17.0 did not have. Every capability it shipped worked
	when driven through `mcp.handle`, and every one of them 404'd for the app,
	because nothing anywhere exercised the transport the app actually speaks.
	"""

	def setUp(self):
		super().setUp()
		self.unit = self.a_camp()
		self.task = self.a_task(
			creates_record="Housing Inspection",
			location_doctype="Housing Unit",
			location=self.unit,
		)

	def upload(self, kind, name):
		"""One photograph, in slices, exactly as the app sends it."""
		payload = base64.b64encode(f"{kind}-bytes".encode()).decode()
		digest = hashlib.sha256(base64.b64decode(payload)).hexdigest()
		upload_id = f"{kind}-0001"
		files_api.stage_file_chunk(
			upload_id=upload_id,
			file_name=name,
			chunk_index=0,
			chunk_count=1,
			total_bytes=len(base64.b64decode(payload)),
			data=payload,
		)
		return files_api.finalize_staged_file(
			upload_id=upload_id,
			file_name=name,
			sha256=digest,
			total_bytes=len(base64.b64decode(payload)),
		)

	def test_scan_to_compliance_record_over_the_mobile_api_alone(self):
		self.be()

		context = mobile_api.get_current_user_context()
		self.assertEqual(context["user"], WORKER)
		self.assertEqual(context["default_company"], MAIN)

		pool = mobile_api.list_available_tasks()["tasks"]
		self.assertIn(self.task, {row["name"] for row in pool})

		claimed = mobile_api.claim_task(task=self.task)
		self.assertTrue(claimed["assignment"])
		self.assertTrue(claimed["claimed_at"])

		started = mobile_api.start_task(task=self.task)
		self.assertTrue(started["started_at"])

		mine = mobile_api.list_my_tasks()["tasks"]
		self.assertEqual({row["name"] for row in mine}, {self.task})

		photo = self.upload("photo", "FT_photo.jpg")
		signature = self.upload("signature", "FT_signature.png")
		self.assertTrue(photo["file_token"])
		self.assertTrue(photo["sha256_verified"])

		done = mobile_api.complete_task_via_mobile(
			task=self.task,
			task_assignment=claimed["assignment"],
			findings_text="",
			clean_pass=True,
			completion_narrative="walked it",
			actual_duration_minutes=22,
			latitude=45.6721,
			longitude=-121.1787,
			evidence_files=[
				{"file_token": photo["file_token"], "file_name": "FT_photo.jpg", "kind": "photo"},
				{
					"file_token": signature["file_token"],
					"file_name": "FT_signature.png",
					"kind": "signature",
				},
			],
		)
		self.assertEqual(done["created_record_doctype"], "Housing Inspection")
		self.assertTrue(done["created_record_name"])
		self.assertFalse(done["corrective_action_opened"])
		self.assertEqual(done["evidence_filed"], 2)
		self.assertEqual(frappe.db.get_value("Farm Task", self.task, "state"), "Completed")

	def test_a_mismatched_hash_is_refused_and_the_pieces_are_kept(self):
		"""The app's contract asks for this by name: an audit trail that records
		evidence hashes it never checked is recording a claim, not a fact."""
		self.be()
		payload = base64.b64encode(b"north-wall").decode()
		files_api.stage_file_chunk(
			upload_id="u9",
			file_name="north-wall.jpg",
			chunk_index=0,
			chunk_count=1,
			total_bytes=10,
			data=payload,
		)
		with self.assertRaises(frappe.ValidationError) as caught:
			files_api.finalize_staged_file(
				upload_id="u9", file_name="north-wall.jpg", sha256="0" * 64, total_bytes=10
			)
		self.assertIn("not the bytes that were sent", str(caught.exception))

	def test_one_workers_upload_cannot_be_finalised_by_another(self):
		self.enrol(email=OUTSIDER, name="Ben Ortiz", entities=[MAIN])
		self.be()
		files_api.stage_file_chunk(
			upload_id="shared-id",
			file_name="mine.jpg",
			chunk_index=0,
			chunk_count=1,
			total_bytes=3,
			data=base64.b64encode(b"abc").decode(),
		)
		self.be(OUTSIDER)
		with self.assertRaises(frappe.ValidationError) as caught:
			files_api.finalize_staged_file(
				upload_id="shared-id",
				file_name="mine.jpg",
				sha256=hashlib.sha256(b"abc").hexdigest(),
				total_bytes=3,
			)
		self.assertIn("belongs to", str(caught.exception))

	def test_the_evidence_file_is_private(self):
		self.be()
		handle = self.upload("photo", "north-wall.jpg")
		self.assertTrue(frappe.db.get_value("File", handle["file_token"], "is_private"))

	def test_an_upload_cannot_be_attached_to_an_arbitrary_document(self):
		"""`commit_staged_file` takes an attach target. This never forwards one."""
		self.be()
		handle = self.upload("photo", "north-wall.jpg")
		row = frappe.db.get_value(
			"File", handle["file_token"], ["attached_to_doctype", "attached_to_name"], as_dict=True
		)
		self.assertFalse(row.get("attached_to_doctype"))
		self.assertFalse(row.get("attached_to_name"))


# ── 9. the two things a hostile body could otherwise reach ──────────────────
class TheHandsetsFixReachesTheRecord(MobileAPITestCase):
	"""v0.19.1. The shipped app has been sending `latitude`/`longitude` since
	v0.18 and they went only to the audit row, because Farm Task Assignment had
	no column to put them in. It has one now, so they land on the record — which
	is the location half of §112.161(a)(1)(i) arriving without an app release."""

	def _complete(self, **kwargs):
		self.a_camp()
		task = self.a_task(evidence_required={"findings_text": True})
		self.be()
		mobile_api.claim_task(task=task)
		mobile_api.start_task(task=task)
		mobile_api.complete_task_via_mobile(task=task, findings_text="", **kwargs)
		return STORE.rows("Farm Task Assignment")[0]

	def test_a_coordinate_pair_becomes_the_location(self):
		row = self._complete(latitude=45.6721, longitude=-121.1787)
		self.assertEqual(row["farm_location_gps"], "45.6721000,-121.1787000")

	def test_an_explicit_place_name_beats_the_handsets_fix(self):
		"""A worker who typed a name did so where the fix was absent or wrong.
		Overwriting it with whatever the GPS settled on outside would replace a
		fact with a guess."""
		row = self._complete(farm_location_gps="MC-Cabin-01", latitude=45.6721, longitude=-121.1787)
		self.assertEqual(row["farm_location_gps"], "MC-Cabin-01")

	def test_a_malformed_pair_is_dropped_rather_than_failing_the_completion(self):
		"""THE ONE THAT MATTERS. The completion carries photographs, a signature
		and a compliance record; refusing all of it over an unparseable coordinate
		would trade the record for its least important field. The pair as sent is
		still in the audit row, which is where a bad one is worth looking at."""
		row = self._complete(latitude="not-a-number", longitude="also-not")
		self.assertFalse(row.get("farm_location_gps"))
		recorded = json.loads(self.audit_rows("complete_task_via_mobile")[0]["arguments_json"])
		self.assertEqual(recorded["latitude"], "not-a-number")

	def test_a_completion_carrying_no_location_at_all_still_lands(self):
		row = self._complete()
		self.assertFalse(row.get("farm_location_gps"))
		self.assertEqual(row["state"], "Completed")


class TheBodyCannotNameTheCaller(MobileAPITestCase):
	"""Frappe binds body keys that match a method's signature, and `user` is in
	every one of these signatures because the guard injects it."""

	def setUp(self):
		super().setUp()
		self.enrol(email=OUTSIDER, name="Ben Ortiz", entities=[MAIN])

	def test_a_user_key_in_the_body_is_dropped_not_honoured(self):
		self.be(WORKER)
		context = mobile_api.get_current_user_context(user=OUTSIDER)
		self.assertEqual(context["user"], WORKER)

	def test_and_it_does_not_crash_the_call_either(self):
		"""The collision would raise rather than escalate, so the bug this
		prevents is loud. It should also simply not happen."""
		self.be(WORKER)
		for call in (mobile_api.list_my_tasks, mobile_api.list_available_tasks):
			self.assertIsInstance(call(user="attacker@example.test"), dict)

	def test_the_audit_row_names_the_authenticated_caller_not_the_claimed_one(self):
		self.be(WORKER)
		mobile_api.get_current_user_context(user=OUTSIDER)
		summary = self.audit_rows("get_current_user_context")[0]["result_summary"]
		self.assertIn(WORKER, summary)
		self.assertNotIn(OUTSIDER, summary)


class RefusalsAreMeteredToo(MobileAPITestCase):
	def test_an_ungranted_account_cannot_grow_the_audit_log_without_bound(self):
		"""Every refusal writes a row. A caller holding a valid token but no
		grant is never allowed to do anything — and is exactly the one worth
		metering, or the log is a free write primitive."""
		STORE.seed("User", [{"name": "prober@example.test", "enabled": 1, "full_name": "Prober"}])
		set_roles("prober@example.test", ["Field Worker"])
		self.be("prober@example.test")

		refused = 0
		for _ in range(guard.READ_LIMIT * 2):
			try:
				mobile_api.list_my_tasks()
			except guard.RateLimited:
				break
			except frappe.PermissionError:
				refused += 1
		else:
			self.fail("the refusal path was never rate limited")

		self.assertLessEqual(refused, guard.READ_LIMIT)
		self.assertLessEqual(len(self.audit_rows("list_my_tasks")), guard.READ_LIMIT + 1)

	def test_a_guest_is_metered_by_the_address_it_arrives_from(self):
		"""Not by the name "Guest", which every anonymous caller shares — one
		bucket for all of them would let a caller who has hit the limit go quiet
		by suppressing the next unrelated one's audit rows."""
		self.be("Guest", remote_addr="203.0.113.9")
		for _ in range(guard.READ_LIMIT):
			with self.assertRaises(frappe.PermissionError):
				mobile_api.list_my_tasks()
		with self.assertRaises(guard.RateLimited):
			mobile_api.list_my_tasks()

		# A different address is a different bucket, and is still refused on its
		# own merits rather than on somebody else's spending.
		self.be("Guest", remote_addr="198.51.100.4")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.list_my_tasks()


# ── 8. the hiring wizard's Assignment and Housing steps ─────────────────────
class TheWizardsDropdownsComeFromTheSite(MobileAPITestCase):
	"""v0.54.0. `list_onboarding_reference_data`.

	The four masters an operator maintains in the Desk, read by the phone instead
	of compiled into it. The failure this prevents is the one
	`list_i9_document_types` prevented for the I-9's document picker: a Swift
	array that goes stale silently, and a wizard whose Assignment step fails at
	the END of a hire with "not a Designation on this site" and no way to find
	out what is.
	"""

	def setUp(self):
		super().setUp()
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		STORE.seed("Branch", [{"name": "Mill Creek Camp", "branch": "Mill Creek Camp"}])
		STORE.seed("Designation", [{"name": "Picker", "designation_name": "Picker"}])
		STORE.seed(
			"Employment Type",
			[
				{"name": "Seasonal", "employee_type_name": "Seasonal"},
				{"name": "Permanent", "employee_type_name": "Permanent"},
				{"name": "Contract", "employee_type_name": "Contract"},
			],
		)
		STORE.seed(
			"Department",
			[
				{"name": "Harvest", "department_name": "Harvest", "company": MAIN, "is_group": 0},
				{"name": "All Departments", "department_name": "All", "company": MAIN, "is_group": 1},
				{"name": "Packing", "department_name": "Packing", "company": OTHER, "is_group": 0},
			],
		)

	def test_all_four_masters_come_back_in_one_call(self):
		"""One call, because they are read together once when the step opens and
		four round trips over a tailgate LTE connection is four chances to
		half-populate a form."""
		self.be()
		answer = mobile_api.list_onboarding_reference_data()
		for key in ("branches", "departments", "designations", "employment_types"):
			with self.subTest(key=key):
				self.assertTrue(answer[key], key)
		self.assertEqual(answer["counts"]["employment_types"], 3)
		self.assertEqual(answer["masters_absent"], [])

	def test_the_three_employment_types_the_wizard_offers_are_the_sites_own(self):
		self.be()
		names = {row["name"] for row in mobile_api.list_onboarding_reference_data()["employment_types"]}
		self.assertEqual(names, {"Seasonal", "Permanent", "Contract"})

	def test_a_branch_with_no_second_column_still_has_a_label(self):
		"""Frappe HR's Branch is a docname and nothing else on some sites, and a
		dropdown row with a null label is a blank line somebody has to pick."""
		STORE.seed("Branch", [{"name": "Bare Camp"}])
		self.be()
		labels = {
			row["name"]: row["label"] for row in mobile_api.list_onboarding_reference_data()["branches"]
		}
		self.assertEqual(labels["Bare Camp"], "Bare Camp")
		self.assertEqual(labels["Mill Creek Camp"], "Mill Creek Camp")

	def test_another_entitys_department_is_not_offered(self):
		"""Department is the only one of the four that carries a company, and the
		scoping rule the rest of this surface follows applies to it too."""
		self.be()
		names = {row["name"] for row in mobile_api.list_onboarding_reference_data()["departments"]}
		self.assertIn("Harvest", names)
		self.assertNotIn("Packing", names)

	def test_a_group_department_is_not_offered(self):
		"""`is_group` marks a node in the tree rather than somewhere a person is
		assigned, and an Employee pointed at one double-counts in every report."""
		self.be()
		names = {row["name"] for row in mobile_api.list_onboarding_reference_data()["departments"]}
		self.assertNotIn("All Departments", names)

	def test_a_master_this_site_does_not_have_is_empty_and_named_not_an_error(self):
		"""A site without Frappe HR has none of the four, and the honest answer is
		a wizard that offers no choices for that field rather than a hire that
		cannot start. `create_employee` agrees: `_clean` does not check a Link
		whose target doctype is absent."""
		from .harness import INSTALLED_DOCTYPES

		# Not seeded away — removed from the site's installed set, which is what
		# "this bench has no hrms" actually looks like to `compat.doctype_exists`.
		INSTALLED_DOCTYPES.discard("Branch")
		self.addCleanup(INSTALLED_DOCTYPES.add, "Branch")

		self.be()
		answer = mobile_api.list_onboarding_reference_data()
		self.assertEqual(answer["branches"], [])
		self.assertIn("Branch", answer["masters_absent"])
		self.assertTrue(answer["designations"])

	def test_a_field_worker_may_read_it_because_nothing_on_it_is_about_a_person(self):
		"""Deliberately unlike `search_employees`. A job title and a camp name are
		not a personnel record, and gating them would mean the wizard's own
		dropdowns needed a role the rest of the surface does not."""
		set_roles(WORKER, ["Field Worker"])
		self.be()
		self.assertTrue(mobile_api.list_onboarding_reference_data()["designations"])

	def test_it_refuses_an_entity_this_phone_cannot_reach(self):
		self.be()
		with self.assertRaises(frappe.PermissionError):
			mobile_api.list_onboarding_reference_data(company=OTHER)


class TheCampHasARead(MobileAPITestCase):
	"""v0.54.0. `list_available_housing` — beds and bodies, and no names."""

	def setUp(self):
		super().setUp()
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		self.a_camp("MC-Cabin-01")

	def _units(self, **kwargs):
		return {row["name"]: row for row in mobile_api.list_available_housing(**kwargs)["units"]}

	def test_an_empty_cabin_reports_its_capacity_and_no_occupants(self):
		self.be()
		unit = self._units()[self.unit]
		self.assertEqual(unit["capacity"], 4)
		self.assertEqual(unit["current_occupants"], 0)
		self.assertEqual(unit["open_beds"], 4)
		self.assertEqual(unit["status"], "Available")
		self.assertTrue(unit["assignable"])

	def test_it_counts_who_is_in_a_cabin_and_never_names_them(self):
		"""`list_housing_units` returns an `occupants` list of employee names.
		Who sleeps in which cabin is a personnel fact, and it has no business on
		a picker's phone merely because the vacancy count does."""
		self.be()
		mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
		)

		unit = self._units()[self.unit]
		self.assertEqual(unit["current_occupants"], 1)
		self.assertEqual(unit["open_beds"], 3)
		self.assertNotIn("occupants", unit)
		self.assertNotIn("Ana Ramos", json.dumps(unit))

	def test_a_full_cabin_is_dropped_by_default_and_kept_with_a_reason_on_request(self):
		self.be()
		for index in range(4):
			STORE.seed(
				"Employee",
				[
					{
						"name": f"EMP-FILL-{index}",
						"employee_name": f"Filler {index}",
						"company": MAIN,
						"status": "Active",
					}
				],
			)
			mobile_api.assign_housing(
				employee=f"EMP-FILL-{index}", housing_unit=self.unit, check_in_date="2026-08-01"
			)

		self.assertNotIn(self.unit, self._units())

		unit = self._units(include_full=True)[self.unit]
		self.assertEqual(unit["status"], "Full")
		self.assertFalse(unit["assignable"])
		self.assertIn("4 bed(s) are taken", unit["unassignable_reason"])

	def test_a_shower_block_is_never_offered_at_all(self):
		"""`create_housing_assignment` refuses one by name, and a dropdown that
		offers it is a dropdown whose next screen is a refusal."""
		self.tool_data(
			"create_housing_unit",
			{"parcel": "Mill Creek", "unit_name": "MC-Bath", "unit_type": "Toilet-Shower"},
		)
		self.be()
		self.assertNotIn("MC-Bath - MC", self._units())

	def test_a_condemned_cabin_is_hidden_by_default_and_says_why_when_shown(self):
		"""A foreman who cannot find the cabin they expected needs to be told it
		is condemned, not shown a shorter list."""
		frappe.db.set_value("Housing Unit", self.unit, "condition", "Uninhabitable")
		self.be()
		self.assertNotIn(self.unit, self._units())

		unit = self._units(include_full=True)[self.unit]
		self.assertEqual(unit["status"], "Uninhabitable")
		self.assertFalse(unit["assignable"])
		self.assertIn("Uninhabitable", unit["unassignable_reason"])

	def test_a_cabin_nobody_has_measured_is_not_reported_as_full(self):
		"""Capacity zero means unmeasured, not "no beds" — a camp whose
		capacities were never entered would otherwise come back with every bed
		taken and nothing saying why."""
		self.tool_data(
			"create_housing_unit",
			{"parcel": "Mill Creek", "unit_name": "MC-Cabin-09", "unit_type": "Cabin"},
		)
		self.be()
		unit = self._units()["MC-Cabin-09 - MC"]
		self.assertIsNone(unit["capacity"])
		self.assertIsNone(unit["open_beds"])
		self.assertEqual(unit["status"], "Available")
		self.assertTrue(unit["assignable"])

	def test_it_refuses_an_entity_this_phone_cannot_reach(self):
		self.be()
		with self.assertRaises(frappe.PermissionError):
			mobile_api.list_available_housing(company=OTHER)


class ABranchResolvesToItsGround(MobileAPITestCase):
	"""v0.54.0. `Parcel.branch`, and the join it exists to make.

	An Employee carries a Branch and a Housing Unit stands on a Parcel. Before
	this column there was no join between them at all, so a wizard that had just
	asked which camp somebody was hired to could not then show that camp's
	cabins — the iOS side would have had to fetch every parcel, fetch every unit,
	and work the mapping out itself.

	The resolution happens server-side through one function that both
	`list_onboarding_reference_data` and `list_available_housing` call, so the
	mapping the wizard was SHOWN and the mapping the housing list FILTERS ON
	cannot come apart.
	"""

	def setUp(self):
		super().setUp()
		# `update_parcel` is how a branch is put on a parcel, and it is a mutating
		# tool an operator switches on by hand like every other one.
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **ON, allow_update_parcel=1)
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		STORE.seed(
			"Branch",
			[
				{"name": "Mill Creek Camp", "branch": "Mill Creek Camp"},
				{"name": "Grande Camp", "branch": "Grande Camp"},
				{"name": "Packhouse", "branch": "Packhouse"},
			],
		)
		# Mill Creek Camp grew across a fence line: two parcels, one camp. Grande
		# Camp is the ordinary single-parcel case. Packhouse is a real operating
		# unit with no ground tagged to it at all, which is its own answer.
		for parcel_name in ("Mill Creek", "Mill Creek North", "Grande Ronde"):
			self.tool_data(
				"create_parcel",
				{"owning_entity": MAIN, "parcel_name": parcel_name, "acreage": 40.0},
			)
		for parcel_name, branch in (
			("Mill Creek", "Mill Creek Camp"),
			("Mill Creek North", "Mill Creek Camp"),
			("Grande Ronde", "Grande Camp"),
		):
			self.tool_data("update_parcel", {"parcel": parcel_name, "branch": branch})

		self.cabins = {}
		for parcel_name, unit_name in (
			("Mill Creek", "MC-Cabin-01"),
			("Mill Creek North", "MCN-Cabin-01"),
			("Grande Ronde", "GR-Cabin-01"),
		):
			self.cabins[unit_name] = self.tool_data(
				"create_housing_unit",
				{"parcel": parcel_name, "unit_name": unit_name, "unit_type": "Cabin", "capacity": 4},
			)["name"]

	def _names(self, **kwargs):
		return {row["name"] for row in mobile_api.list_available_housing(**kwargs)["units"]}

	# ── the reference read ──────────────────────────────────────────────────
	def test_every_branch_row_carries_the_parcels_it_holds(self):
		self.be()
		rows = {row["name"]: row for row in mobile_api.list_onboarding_reference_data()["branches"]}
		self.assertEqual(rows["Mill Creek Camp"]["parcels"], ["Mill Creek - ETC", "Mill Creek North - ETC"])
		self.assertEqual(rows["Grande Camp"]["parcels"], ["Grande Ronde - ETC"])
		self.assertEqual(rows["Packhouse"]["parcels"], [])

	def test_the_scalar_parcel_is_set_only_when_there_is_exactly_one(self):
		"""Null for none AND for several. A scalar that silently picked the first
		of two parcels would send half a camp's cabins missing, and a client
		reading only this field has to fall back to asking the server."""
		self.be()
		rows = {row["name"]: row for row in mobile_api.list_onboarding_reference_data()["branches"]}
		self.assertEqual(rows["Grande Camp"]["parcel"], "Grande Ronde - ETC")
		self.assertIsNone(rows["Mill Creek Camp"]["parcel"])
		self.assertEqual(rows["Mill Creek Camp"]["parcel_count"], 2)
		self.assertIsNone(rows["Packhouse"]["parcel"])

	def test_a_branch_with_no_ground_is_listed_and_called_out_rather_than_hidden(self):
		"""It is a real operating unit somebody may legitimately hire into. What
		it is not is a camp with housing."""
		self.be()
		answer = mobile_api.list_onboarding_reference_data()
		self.assertIn("Packhouse", {row["name"] for row in answer["branches"]})
		self.assertEqual(answer["branches_without_parcels"], ["Packhouse"])

	# ── the housing read ────────────────────────────────────────────────────
	def test_passing_a_branch_returns_that_camps_cabins_and_no_others(self):
		"""The whole point: the phone passes the branch it just hired somebody
		into and does no parcel lookup of its own."""
		self.be()
		self.assertEqual(self._names(branch="Grande Camp"), {self.cabins["GR-Cabin-01"]})

	def test_a_camp_spanning_two_parcels_returns_both(self):
		"""A filter that took only the first parcel would hide half the beds on
		exactly the operations big enough to have the problem."""
		self.be()
		self.assertEqual(
			self._names(branch="Mill Creek Camp"),
			{self.cabins["MC-Cabin-01"], self.cabins["MCN-Cabin-01"]},
		)

	def test_the_parcels_searched_are_echoed_back(self):
		"""A foreman looking at an unexpectedly short list needs to see which
		ground was searched."""
		self.be()
		answer = mobile_api.list_available_housing(branch="Mill Creek Camp")
		self.assertTrue(answer["branch_filter_applied"])
		self.assertEqual(answer["branch_parcels"], ["Mill Creek - ETC", "Mill Creek North - ETC"])
		self.assertIsNone(answer["branch_note"])

	def test_a_branch_that_names_nothing_is_refused_rather_than_answered_empty(self):
		"""A typo resolves to no parcels, and "no parcels" and "no beds" produce
		the same empty list — so the mistake has to be caught while it can still
		be told apart from an answer."""
		self.be()
		with self.assertRaises(frappe.DoesNotExistError) as caught:
			mobile_api.list_available_housing(branch="Mil Creek Camp")
		self.assertIn("list_onboarding_reference_data", str(caught.exception))

	def test_a_real_branch_with_no_ground_lists_everything_and_says_why(self):
		"""Never a silent empty list. An empty camp reads on a phone as "no
		room", which is the one wrong answer this endpoint can give."""
		self.be()
		answer = mobile_api.list_available_housing(branch="Packhouse")
		self.assertFalse(answer["branch_filter_applied"])
		self.assertEqual(answer["branch_parcels"], [])
		self.assertIn("No parcel is tagged with branch Packhouse", answer["branch_note"])
		self.assertIn("update_parcel", answer["branch_note"])
		self.assertEqual(len(answer["units"]), 3)

	def test_a_site_that_has_not_migrated_the_column_says_so_and_lists_everything(self):
		"""The other way this can fail, and it is a different sentence: the
		column is not there rather than nothing being tagged with it."""
		real = compat.has_field

		# Wraps rather than replaces: every other field answers truthfully, so
		# `existing_fields` still builds a real column list and only the one
		# column this site would be missing is missing.
		def unmigrated(doctype, field):
			return False if (doctype == "Parcel" and field == "branch") else real(doctype, field)

		with mock.patch.object(compat, "has_field", unmigrated):
			self.be()
			answer = mobile_api.list_available_housing(branch="Mill Creek Camp")
		self.assertFalse(answer["branch_filter_applied"])
		self.assertIn("no branch column", answer["branch_note"])
		self.assertIn("migrate", answer["branch_note"])
		self.assertEqual(len(answer["units"]), 3)

	def test_a_branch_and_a_parcel_together_intersect(self):
		"""Which is what somebody asking for one parcel of a two-parcel camp
		means."""
		self.be()
		self.assertEqual(
			self._names(branch="Mill Creek Camp", parcel="Mill Creek"),
			{self.cabins["MC-Cabin-01"]},
		)

	def test_the_branch_the_wizard_was_shown_is_the_one_the_housing_read_filters_on(self):
		"""The two halves asserted together. If these ever disagree the wizard
		shows a camp and then shows none of its cabins."""
		self.be()
		for row in mobile_api.list_onboarding_reference_data()["branches"]:
			if not row["parcels"]:
				continue
			with self.subTest(branch=row["name"]):
				answer = mobile_api.list_available_housing(branch=row["name"])
				self.assertEqual(answer["branch_parcels"], row["parcels"])

	# ── the mapping itself ──────────────────────────────────────────────────
	def test_a_parcel_cannot_be_tagged_with_a_branch_that_does_not_exist(self):
		"""The refusal is at the record, so nothing downstream has to defend
		against a branch nobody can resolve."""
		message = self.tool_error("update_parcel", {"parcel": "Grande Ronde", "branch": "Nowhere"})
		self.assertIn("Nowhere", message)
		self.assertIn("Branch", message)

	def test_clearing_a_branch_unassigns_the_ground(self):
		"""How a camp folded into another one is recorded."""
		self.tool_data("update_parcel", {"parcel": "Grande Ronde", "branch": ""})
		self.be()
		answer = mobile_api.list_available_housing(branch="Grande Camp")
		self.assertFalse(answer["branch_filter_applied"])
		self.assertIn("No parcel is tagged", answer["branch_note"])

	def test_another_entitys_ground_is_not_in_a_branchs_parcels(self):
		"""The scoping rule applied to the JOIN rather than only to the rows —
		a branch with parcels under a company this caller cannot see reports
		only the ones they can."""
		self.tool_data(
			"create_parcel",
			{"owning_entity": OTHER, "parcel_name": "Far Side", "acreage": 10.0},
		)
		self.tool_data("update_parcel", {"parcel": "Far Side", "branch": "Grande Camp"})
		self.be()
		rows = {row["name"]: row for row in mobile_api.list_onboarding_reference_data()["branches"]}
		self.assertEqual(rows["Grande Camp"]["parcels"], ["Grande Ronde - ETC"])


class TheCampHasAWrite(MobileAPITestCase):
	"""v0.54.0. `assign_housing` — the wizard's Housing step."""

	def setUp(self):
		super().setUp()
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		self.a_camp("MC-Cabin-01")

	def test_it_puts_one_person_in_one_cabin_from_one_date(self):
		self.be()
		answer = mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
		)
		self.assertTrue(answer["assignment"])
		self.assertEqual(answer["employee"], WORKER_EMPLOYEE)
		self.assertEqual(answer["unit"], self.unit)
		self.assertEqual(answer["check_in_date"], "2026-08-01")
		self.assertEqual(answer["status"], "Current")
		self.assertEqual(answer["current_occupants"], 1)
		self.assertEqual(answer["open_beds"], 3)
		self.assertEqual(answer["company"], MAIN)

	def test_a_second_person_into_a_four_bunk_cabin_is_the_ordinary_case(self):
		"""The tool refuses an overlap without `allow_multi_occupancy` and this
		method passes it under capacity on the caller's behalf — a shared cabin
		is what a cabin IS, and a wizard that refused the second picker into a
		four-bunk unit would be unusable in July."""
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-LUZ",
					"employee_name": "Luz Herrera",
					"company": MAIN,
					"status": "Active",
				}
			],
		)
		self.be()
		mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
		)
		answer = mobile_api.assign_housing(
			employee="EMP-LUZ", housing_unit=self.unit, check_in_date="2026-08-02"
		)
		self.assertEqual(answer["current_occupants"], 2)

	def test_it_refuses_to_overfill_a_cabin_where_the_tool_only_warns(self):
		"""The difference is deliberate. A warning is right on a console where an
		operator can weigh it; on a phone nothing displays it, the foreman has
		walked away, and a bed that does not exist becomes somebody sleeping in a
		truck."""
		self.be()
		for index in range(4):
			STORE.seed(
				"Employee",
				[
					{
						"name": f"EMP-FILL-{index}",
						"employee_name": f"Filler {index}",
						"company": MAIN,
						"status": "Active",
					}
				],
			)
			mobile_api.assign_housing(
				employee=f"EMP-FILL-{index}", housing_unit=self.unit, check_in_date="2026-08-01"
			)

		with self.assertRaises(Exception) as caught:
			mobile_api.assign_housing(
				employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
			)
		message = str(caught.exception)
		self.assertIn("holds 4", message)
		self.assertIn("list_available_housing", message)

		# Asserted on the PERSON rather than on a row count. `guard._record`
		# rolls back on every refusal — deliberately, so a failed call leaves
		# nothing behind but its audit row — and in this suite the four fills
		# above share that uncommitted transaction, so counting rows here would
		# be measuring the rollback rather than the refusal.
		self.assertFalse(
			[row for row in STORE.rows("Housing Assignment") if row.get("employee") == WORKER_EMPLOYEE]
		)

	def test_the_phone_cannot_send_the_flag_that_turns_the_capacity_check_off(self):
		"""`allow_multi_occupancy` is the one argument that would undo the check
		above, so it is not in the signature at all — Frappe's own argument
		filter drops a body key that matches nothing."""
		self.be()
		with self.assertRaises(TypeError):
			mobile_api.assign_housing(
				employee=WORKER_EMPLOYEE,
				housing_unit=self.unit,
				check_in_date="2026-08-01",
				allow_multi_occupancy=True,
			)

	def test_a_field_worker_may_not_write_one(self):
		"""A Housing Assignment names a person, a building and the dates between
		them. It is the audit trail defending a Section 119 exclusion and the
		answer to an ORS 653 wage claim, which is a personnel record."""
		set_roles(WORKER, ["Field Worker"])
		self.be()
		with self.assertRaises(Exception) as caught:
			mobile_api.assign_housing(
				employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
			)
		self.assertIn("personnel register", str(caught.exception))

	def test_a_check_in_date_is_required(self):
		self.be()
		with self.assertRaises(frappe.ValidationError) as caught:
			mobile_api.assign_housing(employee=WORKER_EMPLOYEE, housing_unit=self.unit)
		self.assertIn("check_in_date", str(caught.exception))

	def test_an_employee_of_another_entity_is_not_found_rather_than_refused(self):
		"""The same wording a docname that does not exist gets, so a caller
		cannot map the site's employees by watching which error comes back."""
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.assign_housing(
				employee=OUTSIDER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
			)

	def test_a_cabin_belonging_to_another_entity_is_not_found_either(self):
		"""A Housing Unit calls its company `owning_entity`, so
		`require_scoped_doc` reads a field that is not there and the check is
		made by hand. This is the test that it is made at all."""
		frappe.db.set_value("Housing Unit", self.unit, "owning_entity", OTHER)
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.assign_housing(
				employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
			)

	def test_it_carries_the_section_119_note_and_the_unrecorded_deduction_warning(self):
		"""Whether a housing charge came out of wages is the question ORS 653
		constrains, and Unknown is the answer that cannot be defended."""
		self.be()
		answer = mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
		)
		self.assertEqual(answer["housing_deduction_from_wages"], "Unknown")
		self.assertIn("Section 119", answer["section_119_note"])
		self.assertTrue(any("ORS 653" in warning for warning in answer["warnings"]))

	def test_the_deduction_answer_is_forwarded_when_the_wizard_asks_it(self):
		self.be()
		answer = mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE,
			housing_unit=self.unit,
			check_in_date="2026-08-01",
			housing_deduction_from_wages="No",
			deposit_paid=150,
		)
		self.assertEqual(answer["housing_deduction_from_wages"], "No")
		self.assertEqual(answer["deposit_paid"], 150.0)

	def test_every_call_leaves_an_audit_row(self):
		self.be()
		mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-08-01"
		)
		self.assertEqual(len(self.audit_rows("assign_housing")), 1)


class TheAssignmentStepCanRecordWhereSomebodyWorks(MobileAPITestCase):
	"""v0.54.0. `create_employee` on the phone, and the field that had nowhere
	to land.

	`tools/employee.WRITABLE` carried designation, department and employment type
	and not `branch`, so the wizard's Assignment step could ask which camp
	somebody reports to and could not record the answer. The wrapper forwards it
	like the other three and adds no rule of its own — the allowlist and the Link
	check stay in `tools/employee.py`, which is why this asserts through the
	published endpoint rather than around it.
	"""

	def setUp(self):
		super().setUp()
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		STORE.seed("Branch", [{"name": "Mill Creek Camp", "branch": "Mill Creek Camp"}])
		STORE.seed("Designation", [{"name": "Picker", "designation_name": "Picker"}])
		STORE.seed("Employment Type", [{"name": "Seasonal", "employee_type_name": "Seasonal"}])
		STORE.seed(
			"Department",
			[{"name": "Harvest", "department_name": "Harvest", "company": MAIN, "is_group": 0}],
		)

	def test_the_four_assignment_dropdowns_all_reach_the_record(self):
		self.be()
		created = mobile_api.create_employee(
			first_name="Elena",
			last_name="Marquez",
			company=MAIN,
			designation="Picker",
			department="Harvest",
			employment_type="Seasonal",
			branch="Mill Creek Camp",
		)
		row = frappe.db.get_value(
			"Employee",
			created["name"],
			["designation", "department", "employment_type", "branch"],
			as_dict=True,
		)
		self.assertEqual(row["designation"], "Picker")
		self.assertEqual(row["department"], "Harvest")
		self.assertEqual(row["employment_type"], "Seasonal")
		self.assertEqual(row["branch"], "Mill Creek Camp")

	def test_a_branch_that_names_nothing_is_refused_by_the_tool_not_the_wrapper(self):
		"""The refusal lists what this site has, which is the whole reason the
		wizard reads its dropdowns from `list_onboarding_reference_data` rather
		than from an array compiled into the app."""
		self.be()
		with self.assertRaises(Exception) as caught:
			mobile_api.create_employee(
				first_name="Elena", last_name="Marquez", company=MAIN, branch="Nowhere Camp"
			)
		message = str(caught.exception)
		self.assertIn("Nowhere Camp", message)
		self.assertIn("Mill Creek Camp", message)

	def test_the_branch_the_wizard_offers_is_one_the_reference_read_returned(self):
		"""The two halves of the same step, asserted together: a value that came
		out of the dropdown read is a value the create accepts. If these ever
		disagree the wizard fails on its last screen."""
		self.be()
		offered = mobile_api.list_onboarding_reference_data()["branches"][0]["name"]
		created = mobile_api.create_employee(
			first_name="Elena", last_name="Marquez", company=MAIN, branch=offered
		)
		self.assertEqual(frappe.db.get_value("Employee", created["name"], "branch"), offered)


class TheReturningWorkersCabin(MobileAPITestCase):
	"""v0.54.0. `list_available_housing(employee=…)` and `previous_assignment`.

	A picker who worked last season had a cabin, and usually wants it again. The
	wizard shows "Last year: MC-Cabin-07" at the top of the list so a returning
	worker is one tap rather than a scroll through forty units nobody remembers
	the numbers of.

	IT IS THE ONE ARGUMENT ON THIS ENDPOINT THAT NAMES A PERSON, which is why it
	carries a gate the rest of the method does not.
	"""

	def setUp(self):
		super().setUp()
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		self.a_camp("MC-Cabin-01")
		self.second = self.tool_data(
			"create_housing_unit",
			{"parcel": "Mill Creek", "unit_name": "MC-Cabin-07", "unit_type": "Cabin", "capacity": 2},
		)["name"]

	def _previous(self, **kwargs):
		return mobile_api.list_available_housing(employee=WORKER_EMPLOYEE, **kwargs)["previous_assignment"]

	def _stay(self, unit, start, end, employee=WORKER_EMPLOYEE):
		"""One finished stay, written the way a season that ended looks."""
		self.be()
		created = mobile_api.assign_housing(employee=employee, housing_unit=unit, check_in_date=start)
		frappe.db.set_value("Housing Assignment", created["assignment"], "end_date", end)
		frappe.db.set_value("Housing Assignment", created["assignment"], "status", "Ended")
		return created["assignment"]

	# ── what it returns ─────────────────────────────────────────────────────
	def test_it_surfaces_last_seasons_cabin_with_both_dates(self):
		self._stay(self.second, "2025-06-01", "2025-10-15")
		self.be()
		previous = self._previous()
		self.assertEqual(previous["unit"], self.second)
		self.assertEqual(previous["unit_name"], "MC-Cabin-07")
		self.assertEqual(previous["check_in_date"], "2025-06-01")
		self.assertEqual(previous["check_out_date"], "2025-10-15")
		self.assertTrue(previous["available"])
		self.assertIsNone(previous["unavailable_reason"])

	def test_the_unit_name_is_what_is_painted_on_the_door(self):
		"""Not the docname, which carries the parcel key on the end of it. A row
		reading "MC-Cabin-07 - MC" is a row a foreman has to parse."""
		self._stay(self.second, "2025-06-01", "2025-10-15")
		self.be()
		self.assertEqual(self._previous()["unit_name"], "MC-Cabin-07")
		self.assertIn(" - ", self._previous()["unit"])

	def test_the_most_recent_ended_stay_wins(self):
		"""Three seasons in two cabins: the answer is the one they left last."""
		self._stay(self.unit, "2023-06-01", "2023-10-01")
		self._stay(self.second, "2024-06-01", "2024-10-01")
		self._stay(self.unit, "2025-06-01", "2025-10-01")
		self.be()
		self.assertEqual(self._previous()["unit"], self.unit)
		self.assertEqual(self._previous()["check_out_date"], "2025-10-01")

	def test_a_first_season_hire_has_no_previous_assignment_and_that_is_not_an_error(self):
		self.be()
		self.assertIsNone(self._previous())

	def test_it_is_absent_unless_an_employee_is_named(self):
		"""The vacancy read is unchanged for every caller that does not ask."""
		self.be()
		self.assertIsNone(mobile_api.list_available_housing()["previous_assignment"])

	# ── whether the cabin can actually be had ───────────────────────────────
	def test_a_cabin_that_filled_up_since_is_reported_unavailable_with_the_reason(self):
		"""The point of the field. Offering a one-tap re-assignment into a full
		cabin is an offer whose next screen is a refusal."""
		self._stay(self.second, "2025-06-01", "2025-10-15")
		for index in range(2):
			STORE.seed(
				"Employee",
				[
					{
						"name": f"EMP-NEW-{index}",
						"employee_name": f"New {index}",
						"company": MAIN,
						"status": "Active",
					}
				],
			)
			self.be()
			mobile_api.assign_housing(
				employee=f"EMP-NEW-{index}",
				housing_unit=self.second,
				check_in_date=frappe.utils.today(),
			)

		self.be()
		previous = self._previous()
		self.assertEqual(previous["unit"], self.second)
		self.assertFalse(previous["available"])
		self.assertIn("2 bed(s) are taken", previous["unavailable_reason"])
		self.assertEqual(previous["open_beds"], 0)

	def test_a_cabin_condemned_since_they_left_says_so(self):
		self._stay(self.second, "2025-06-01", "2025-10-15")
		frappe.db.set_value("Housing Unit", self.second, "condition", "Uninhabitable")
		self.be()
		previous = self._previous()
		self.assertFalse(previous["available"])
		self.assertIn("Uninhabitable", previous["unavailable_reason"])

	def test_availability_is_computed_even_when_the_list_filtered_that_cabin_out(self):
		"""The field is computed for the unit itself rather than looked up in the
		list beside it — that list drops full and condemned units by default, so
		a lookup there would report every full cabin as available."""
		self._stay(self.second, "2025-06-01", "2025-10-15")
		frappe.db.set_value("Housing Unit", self.second, "condition", "Uninhabitable")
		self.be()
		answer = mobile_api.list_available_housing(employee=WORKER_EMPLOYEE)
		self.assertNotIn(self.second, {row["name"] for row in answer["units"]})
		self.assertEqual(answer["previous_assignment"]["unit"], self.second)
		self.assertFalse(answer["previous_assignment"]["available"])

	def test_somebody_still_housed_is_told_so_rather_than_offered_their_own_bed(self):
		"""An open assignment means they are housed RIGHT NOW. Offering "last
		year: Cabin 7" to somebody currently in Cabin 7 is an offer to
		double-book them."""
		self.be()
		mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE, housing_unit=self.second, check_in_date="2026-06-01"
		)
		previous = self._previous()
		self.assertTrue(previous["currently_housed"])
		self.assertFalse(previous["available"])
		self.assertIsNone(previous["check_out_date"])
		self.assertIn("where they are housed now", previous["unavailable_reason"])

	def test_an_open_stay_wins_over_a_finished_one_however_many_seasons_deep(self):
		"""Somebody who had MC-Cabin-07 last season and is in MC-Cabin-01 tonight
		has BOTH. Answering with last year's cabin would offer a one-tap
		re-assignment to a person who already has a bed, so the open row wins —
		"they are already housed" is true regardless of the history behind it."""
		self._stay(self.second, "2025-06-01", "2025-10-15")
		self.be()
		mobile_api.assign_housing(
			employee=WORKER_EMPLOYEE, housing_unit=self.unit, check_in_date="2026-06-01"
		)

		previous = self._previous()
		self.assertTrue(previous["currently_housed"])
		self.assertEqual(previous["unit"], self.unit)
		self.assertFalse(previous["available"])

	# ── the gate ────────────────────────────────────────────────────────────
	def test_a_field_worker_may_read_vacancies_and_may_not_name_a_person(self):
		"""The split this endpoint is built on, asserted in one test: the same
		caller, the same method, one argument apart."""
		self._stay(self.second, "2025-06-01", "2025-10-15")
		set_roles(WORKER, ["Field Worker"])
		self.be()

		self.assertTrue(mobile_api.list_available_housing()["units"])
		with self.assertRaises(Exception) as caught:
			mobile_api.list_available_housing(employee=WORKER_EMPLOYEE)
		self.assertIn("personnel register", str(caught.exception))

	def test_an_employee_of_another_entity_is_not_found(self):
		self.be()
		with self.assertRaises(frappe.DoesNotExistError):
			mobile_api.list_available_housing(employee=OUTSIDER_EMPLOYEE)

	def test_a_cabin_of_an_entity_this_phone_cannot_reach_is_not_reported(self):
		"""`guard.scoped` cannot do this one — a Housing Unit calls its company
		`owning_entity` — so the check is made by hand and this is the test that
		it is made at all."""
		self._stay(self.second, "2025-06-01", "2025-10-15")
		frappe.db.set_value("Housing Unit", self.second, "owning_entity", OTHER)
		self.be()
		self.assertIsNone(self._previous())

	def test_naming_a_person_still_leaves_one_audit_row_like_any_other_call(self):
		self._stay(self.second, "2025-06-01", "2025-10-15")
		self.be()
		before = len(self.audit_rows("list_available_housing"))
		mobile_api.list_available_housing(employee=WORKER_EMPLOYEE)
		self.assertEqual(len(self.audit_rows("list_available_housing")), before + 1)

	# ── it composes with the branch filter ──────────────────────────────────
	def test_it_comes_back_alongside_a_branch_filtered_list(self):
		"""The two the wizard uses together: the camp's cabins, and the one this
		person had last year."""
		STORE.seed("Branch", [{"name": "Mill Creek Camp", "branch": "Mill Creek Camp"}])
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **ON, allow_update_parcel=1)
		self.tool_data("update_parcel", {"parcel": "Mill Creek", "branch": "Mill Creek Camp"})
		self._stay(self.second, "2025-06-01", "2025-10-15")

		self.be()
		answer = mobile_api.list_available_housing(branch="Mill Creek Camp", employee=WORKER_EMPLOYEE)
		self.assertTrue(answer["branch_filter_applied"])
		self.assertEqual(answer["previous_assignment"]["unit"], self.second)
		self.assertIn(self.second, {row["name"] for row in answer["units"]})


# ── 14. universal_scan ──────────────────────────────────────────────────────
class TheScannerScreenHasOneCall(MobileAPITestCase):
	"""v0.65.0. The route behind "point the camera at it and see what it is".

	The cascade itself is `test_universal_scan.py`'s subject. What is asserted
	HERE is only what this transport adds: the gates, the entity scoping, the
	audit row, and the argument spellings — because this door's own filter keeps
	only the keys the signature declares, and a scan posted under a name the
	signature does not carry arrives empty.
	"""

	VALVE = "MC-Valve-05"

	def setUp(self):
		super().setUp()
		self.configure(
			enabled=1,
			public_url="https://umbrel.tail4a2b.ts.net",
			**ON,
			allow_register_asset=1,
			allow_universal_scan=1,
		)

	def a_valve(self, name=VALVE, company=MAIN):
		return self.tool_data(
			"register_asset", {"name": name, "asset_type": "Irrigation Valve", "company": company}
		)["name"]

	def test_a_tag_resolves_and_the_scan_is_recorded_against_the_caller(self):
		self.a_valve()
		self.be()
		answer = mobile_api.universal_scan(content=self.VALVE)
		self.assertEqual(answer["entity_type"], "asset")
		self.assertEqual(answer["entity_name"], self.VALVE)
		self.assertTrue(answer["scan_recorded"])
		self.assertEqual(frappe.db.get_value("Asset Register", self.VALVE, "last_scan_by"), WORKER)

	def test_a_stranger_comes_back_as_unknown_rather_than_as_an_error(self):
		self.be()
		answer = mobile_api.universal_scan(content="0123456789012")
		self.assertEqual(answer["entity_type"], "unknown")
		self.assertEqual(answer["available_actions"], ["create_task"])
		self.assertFalse(answer["scan_recorded"])

	def test_every_spelling_of_the_scan_argument_reaches_the_tool(self):
		"""`bind` drops what a signature does not name, so a handset posting
		`code` at a method declaring only `content` would be told the field is
		required while holding a perfectly good scan."""
		self.a_valve()
		for key in ("content", "scan", "raw", "code"):
			with self.subTest(argument=key):
				self.be()
				answer = mobile_api.universal_scan(**{key: self.VALVE})
				self.assertEqual(answer["entity_name"], self.VALVE)

	def test_an_empty_scan_is_refused_before_anything_is_read(self):
		self.be()
		with self.assertRaises(frappe.ValidationError) as caught:
			mobile_api.universal_scan(content="   ")
		self.assertIn("content is required", str(caught.exception))

	def test_guest_is_refused_like_every_other_method_here(self):
		self.be("Guest")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.universal_scan(content=self.VALVE)

	def test_a_tag_belonging_to_another_entity_is_not_scanned(self):
		"""The scoping that matters on a scan: a phone cannot stamp, or read,
		the register of a farm this account was never given."""
		self.a_valve(name="SEL-Valve-01", company=OTHER)
		self.be()
		with self.assertRaises(frappe.ValidationError) as caught:
			mobile_api.universal_scan(content="SEL-Valve-01")
		self.assertIn(OTHER, str(caught.exception))
		self.assertFalse(frappe.db.get_value("Asset Register", "SEL-Valve-01", "last_scan_at"))

	def test_the_company_argument_may_narrow_and_may_not_widen(self):
		self.a_valve()
		self.be()
		self.assertEqual(mobile_api.universal_scan(content=self.VALVE, company=MAIN)["entity_type"], "asset")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.universal_scan(content=self.VALVE, company=OTHER)

	# ── the audit row ───────────────────────────────────────────────────────
	def test_one_audit_row_lands_for_the_scan(self):
		self.a_valve()
		self.be()
		before = len(self.audit_rows("universal_scan"))
		mobile_api.universal_scan(content=self.VALVE)
		self.assertEqual(len(self.audit_rows("universal_scan")), before + 1)

	def test_a_login_payload_scanned_by_mistake_is_refused_and_redacted(self):
		"""The one scan that must not be echoed — and the audit row is where it
		would have been kept. `guard.redact_payloads` is what stops that, and this
		is the test that both halves hold on this route."""
		payload = '{"url":"https://x","api_key":"k-abc","api_secret":"s3cr3t-nobody-should-see"}'
		self.be()
		with self.assertRaises(frappe.ValidationError) as caught:
			mobile_api.universal_scan(content=payload)
		self.assertIn("credential document", str(caught.exception))
		self.assertNotIn("s3cr3t-nobody-should-see", str(caught.exception))
		row = self.audit_rows("universal_scan")[-1]
		self.assertNotIn("s3cr3t-nobody-should-see", json.dumps(row))


# ── receipt capture (v0.67.0) ───────────────────────────────────────────────
class ReceiptCaptureFromAPhone(MobileAPITestCase):
	"""The four routes Sprint 2 publishes, and the three arguments they refuse.

	The interesting assertions here are not that the endpoints work — the tools
	are tested in `test_receipts.py` and `test_expenses.py`. They are that this
	transport does not hand a phone anything the tool would otherwise accept:
	`submitted_by` comes from the authenticated account, the company comes from
	the caller's scope, and there is no way to submit a scale ticket at all.
	"""

	PACKER = "Blue Ridge Packing"

	def setUp(self):
		super().setUp()
		STORE.seed(
			"Customer",
			[{"name": self.PACKER, "customer_name": self.PACKER, "customer_group": "Packers"}],
		)
		self.be()

	def ticket(self, **overrides):
		payload = {
			"ticket_number": "44718",
			"date": "2026-09-14",
			"customer": self.PACKER,
			"gross_weight": 18400,
			"tare_weight": 6200,
			"weight_uom": "Lb",
		}
		payload.update(overrides)
		return mobile_api.create_scale_ticket(**payload)

	# ── classify_receipt ────────────────────────────────────────────────────
	def test_the_classifier_answers_and_shows_its_working(self):
		data = mobile_api.classify_receipt(text="SCALE TICKET 44718 GROSS WT 18400 TARE WT 6200")
		self.assertEqual(data["receipt_type"], "scale_ticket")
		self.assertIn("scale ticket", data["matched_signals"])

	def test_the_classifier_still_needs_an_enrolled_credential(self):
		self.be("Guest")
		with self.assertRaises(frappe.PermissionError):
			mobile_api.classify_receipt(text="anything")

	# ── create_scale_ticket ─────────────────────────────────────────────────
	def test_a_ticket_captured_from_a_phone_arrives_as_a_draft(self):
		data = self.ticket()
		self.assertEqual(data["status"], "Draft")
		self.assertEqual(data["docstatus"], 0)
		self.assertEqual(data["net_weight"], 12200.0)

	def test_the_company_comes_from_the_callers_scope_when_none_is_sent(self):
		self.assertEqual(self.ticket()["company"], MAIN)

	def test_a_company_this_account_cannot_reach_is_refused_not_quietly_swapped(self):
		with self.assertRaises(frappe.PermissionError):
			self.ticket(company=OTHER)

	def test_there_is_no_way_to_submit_a_ticket_from_a_phone(self):
		"""Submitting freezes a third party's weight record. The tool exists for
		somebody who can see the settlement it will be checked against."""
		self.assertFalse(hasattr(mobile_api, "submit_scale_ticket"))

	def test_a_phone_cannot_assert_a_net_weight(self):
		"""Not by being refused — by the argument not existing. `bind` keeps only
		the keys a signature declares, so a body carrying one is dropped."""
		self.assertNotIn("net_weight", inspect.signature(mobile_api.create_scale_ticket).parameters)

	# ── list_scale_tickets ──────────────────────────────────────────────────
	def test_the_back_button_list_shows_what_this_crew_just_filed(self):
		self.ticket()
		self.ticket(ticket_number="44719")
		data = mobile_api.list_scale_tickets()
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["total_net_weight"], 24400.0)

	def test_the_list_is_scoped_to_the_callers_entities(self):
		"""A ticket filed by MAIN's crew is not another entity's to read, and the
		filter runs twice — once in the tool, once on the way out."""
		self.ticket()
		self.enrol(email=OUTSIDER, name="Ben Ortiz", entities=[OTHER])
		self.be(OUTSIDER)
		self.assertEqual(mobile_api.list_scale_tickets()["count"], 0)

	def test_the_per_unit_count_survives_the_trip_to_the_phone(self):
		self.ticket()
		self.ticket(ticket_number="44719", weight_uom="Bin", gross_weight=40, tare_weight=0)
		self.assertEqual(mobile_api.list_scale_tickets()["by_weight_uom"], {"Lb": 1, "Bin": 1})

	# ── create_expense_receipt ──────────────────────────────────────────────
	def receipt(self, **overrides):
		payload = {
			"merchant": "Valley Co-op Fuel",
			"amount": 184.62,
			"receipt_date": "2026-06-14",
			"category": "Fuel",
		}
		payload.update(overrides)
		return mobile_api.create_expense_receipt(**payload)

	def test_a_receipt_captured_from_a_phone_is_filed_against_the_caller(self):
		self.assertEqual(self.receipt()["submitted_by"], WORKER_EMPLOYEE)

	def test_a_phone_cannot_file_an_expense_against_somebody_else(self):
		"""A reimbursement claim with the wrong person's signature on it."""
		for argument in ("submitted_by", "employee"):
			with self.subTest(argument=argument):
				self.assertNotIn(argument, inspect.signature(mobile_api.create_expense_receipt).parameters)

	def test_an_offline_queue_can_post_a_draft(self):
		self.assertEqual(self.receipt(status="Draft")["status"], "Draft")

	def test_a_phone_cannot_post_an_already_approved_receipt(self):
		"""Refused by the tool's own `CREATABLE_STATUSES`, not by this signature —
		which is the right place for it, because the MCP surface needs the same
		refusal, and `guard` turns it into the ValidationError a phone reads."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self.receipt(status="Approved")
		self.assertIn("Approval and rejection are separate tools", str(caught.exception))

	def test_approval_is_not_reachable_from_a_phone_at_all(self):
		for method in ("approve_expense_receipt", "reject_expense_receipt"):
			with self.subTest(method=method):
				self.assertFalse(hasattr(mobile_api, method))

	def test_the_supplier_and_item_links_are_forwarded(self):
		STORE.seed(
			"Supplier",
			[{"name": "Valley Co-operative", "supplier_name": "Valley Co-operative"}],
		)
		STORE.seed("Item", [{"name": "HOSE-050", "item_name": "Hydraulic hose 1/2in"}])
		data = self.receipt(
			supplier="Valley Co-operative",
			items=[{"description": "HYD HOSE 1/2", "item": "HOSE-050", "quantity": 1, "unit_price": 31.25}],
		)
		self.assertEqual(data["supplier"], "Valley Co-operative")
		self.assertEqual(data["items"][0]["item"], "HOSE-050")

	def test_a_json_encoded_items_array_is_accepted(self):
		"""`URLSession` posting JSON and a multipart retry do not agree about
		nested arrays, and the intent is unambiguous either way."""
		data = self.receipt(items=json.dumps([{"description": "Nozzle", "quantity": 2, "unit_price": 4.5}]))
		self.assertEqual(data["items"][0]["line_total"], 9.0)

	def test_a_malformed_items_body_is_refused_rather_than_500ing(self):
		with self.assertRaises(frappe.ValidationError):
			self.receipt(items="not json at all")

	def test_settlements_are_not_reachable_from_a_phone(self):
		"""A settlement is a multi-page document that arrives at an office, not a
		thing anybody photographs at a tailgate."""
		for method in ("create_settlement_statement", "submit_settlement_statement"):
			with self.subTest(method=method):
				self.assertFalse(hasattr(mobile_api, method))
