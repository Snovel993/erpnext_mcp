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
import json
from typing import ClassVar

import frappe

from erpnext_mcp import audit, roles, settings
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
	}
	FILES: ClassVar[set[str]] = {"stage_file_chunk", "finalize_staged_file"}

	def _whitelisted(self, module):
		return {
			name
			for name in dir(module)
			if not name.startswith("_") and getattr(getattr(module, name), "farm_ops_method", None)
		}

	def test_the_mobile_module_publishes_exactly_the_ten_the_app_calls(self):
		self.assertEqual(self._whitelisted(mobile_api), self.MOBILE)

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
