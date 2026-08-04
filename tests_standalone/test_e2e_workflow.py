# SPDX-License-Identifier: MIT
"""The whole workflow, built from nothing and walked end to end.

TIM'S ASK, IN ONE SENTENCE: "make sure we can build out a full workflow from end
to end via MCP." This is that test. It stands up a company, a camp, a worker and
a credential from an empty site, then walks a housing inspection from the pool
to a filed compliance record using ONLY the endpoints a phone can reach, and
asserts the things that were broken at an actual iPhone on the night of
2026-08-02.

WHAT MAKES THIS DIFFERENT FROM `test_api_mobile.TheWholeFlowWorks`. That test
proves the calls SUCCEED — the record is written, the state moves, the evidence
count is two. Every bug shipped that week passed it. They were not failures of
the happy path; they were failures of the things nobody looked at afterwards:

    v0.18.2   the claim RESPONSE had `name: null`. The claim itself worked.
    v0.18.3   the evidence UPLOAD was refused by a permission check inside a
              path the tool had never run as a non-Administrator.
    v0.18.4   the File was created, attached and counted — and unreadable by
              anybody except the worker who took it, because nothing had ever
              opened the record as somebody else.

So this file asserts THE STATE OF THE SITE AFTERWARDS, not the return values. It
opens the record. It walks the child table. It reads the File row's own
permission fields. It runs the compliance sweep again and checks the alert the
work was supposed to answer has actually gone.

THE FIXTURES ARE BUILT, NOT SEEDED, AND THAT IS DELIBERATE. Every other suite
here starts from `SeededTestCase`, which writes rows into the store directly
because it is testing something downstream. This one calls `create_company`,
`create_parcel`, `create_housing_unit`, `create_employee` and
`create_mobile_user` — the real tools, through the real registry, with the kill
switches an operator actually has to tick. A seeded fixture cannot catch an
onboarding path that has drifted, and onboarding a new entity is a thing this
system is asked to do far more often than it is asked to inspect a cabin.

WHAT IT STILL DOES NOT PROVE, stated plainly so nobody reads more into a green
run than is there: this is the in-memory double from `harness.py`, not a bench.
It proves the app's own logic, argument translation, permission decisions and
record-writing are coherent end to end. It does not prove MariaDB accepts the
insert or that Frappe's own `File.has_permission` reads `attached_to_doctype`
the way v0.18.4 assumes it does — that assumption is stated in
`inspections._link_evidence_files_to_parent` and confirmed on the site. The
FrappeTestCase suite in `erpnext_mcp/tests/` is where framework contracts live.
"""

import base64
import hashlib
import json
import zlib

import frappe

from erpnext_mcp import audit_packets, compliance_fields, roles
from erpnext_mcp.api import files as files_api
from erpnext_mcp.api import guard
from erpnext_mcp.api import mobile as mobile_api

from .fixtures import V12TestCase
from .harness import ROLES, STORE

COMPANY = "Test Farm LLC"
ABBR = "TFL"
PARCEL = "E2E Home Ranch"
UNIT = "TEST-CABIN-01"
WORKER = "e2e-worker@test.local"
WORKER_NAME = "E2E Worker"
EMPLOYEE = "EMP-E2E"

#: Every switch this walk needs, on. An operator ticks each of these by hand;
#: listing them here is also the shortest honest statement of what onboarding an
#: entity and dispatching one inspection actually requires.
SWITCHES = {
	f"allow_{name}": 1
	for name in (
		"create_company",
		"create_parcel",
		"create_housing_unit",
		"create_employee",
		"create_mobile_user",
		"revoke_mobile_user",
		"generate_mobile_login_qr",
		"create_farm_task",
		"assign_farm_task",
		"claim_farm_task",
		"complete_farm_task",
		"refresh_compliance_alerts",
		"get_compliance_calendar",
		"get_current_user_context",
		"stage_file_chunk",
		"commit_staged_file",
		"list_housing_units",
		"get_housing_unit",
		# v0.19.0. The onboarding walk now ends in the training register, so the
		# switches for it belong in the same list — which is also the shortest
		# honest statement of what onboarding an entity actually requires.
		"record_training",
		"list_trainings",
		"get_training",
		"sign_training_supervisor_review",
		"generate_audit_packet",
		# v0.19.3. The walk now ends on a SHIFT, because that is where the
		# exposure-based regimes anchor: OAR 437-004-1131 asks whether a shift
		# complied from start to finish, and a task completion cannot answer it.
		"start_shift",
		"add_worker_to_shift",
		"remove_worker_from_shift",
		"log_shift_event",
		"end_shift",
		"create_heat_exposure_event",
		"get_shift",
		"list_shifts",
		"get_heat_exposure_event",
		"get_attendance_summary",
		# v0.19.4. The conditions the shift is read against. The scheduled sweep
		# needs no switch — it is a cron rather than a tool — but the two hands on
		# it do, and `get_weather_timeline` is how the walk reads back what the
		# sweep wrote without going through the whole shift.
		"fetch_weather_now",
		"backfill_weather_for_shift",
		"list_shifts_missing_weather",
		"get_weather_timeline",
		"get_weather_settings",
		# v0.19.5. The walk now also ends on a NUMBER, because the last thing an
		# operation does with a season is say what it earned per acre — and the
		# figure is only defensible if the ingredients are on the record.
		"create_field",
		"create_asset",
		"create_normalization_adjustment",
		"approve_normalization_adjustment",
		"list_normalization_adjustments",
		"get_sustainable_cf_per_acre",
		# v0.19.6. And the number is now read over a WINDOW rather than over a
		# period somebody typed, because a single agricultural period compared
		# with another single agricultural period says the operation collapsed in
		# January and recovered in September, every year, on every farm.
		"get_windowed_report",
		"list_financial_kpi_history",
		"recompute_kpi_history",
		# v0.21.0. The walk now also ends on ONE VISIT rather than on N trips:
		# a cabin that is overdue for a habitability walk AND a detector test is
		# one afternoon's work, and the records it produces are still separate
		# because the regulators asking for them are.
		"generate_tasks_from_compliance_alerts",
		"list_inspection_templates",
		"get_inspection_template",
		"list_inspection_sessions",
		"get_inspection_session",
		"start_inspection_session",
		"submit_inspection_session",
		"list_housing_inspections",
		"list_detector_tests",
	)
}


#: The smallest real PNG: 1×1, opaque. Written out rather than pasted as a
#: base64 blob so that what it IS can be read — an evidence path tested with
#: `b"photo-bytes"` is a path that has never seen a file with a header.
def one_pixel_png() -> bytes:
	def chunk(kind: bytes, body: bytes) -> bytes:
		return len(body).to_bytes(4, "big") + kind + body + zlib.crc32(kind + body).to_bytes(4, "big")

	header = chunk(b"IHDR", (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 2, 0, 0, 0]))
	pixels = chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff"))
	return b"\x89PNG\r\n\x1a\n" + header + pixels + chunk(b"IEND", b"")


def is_true(value) -> bool:
	"""A Frappe Check, read the way Frappe means it.

	`0`, `"0"`, `""`, `None` and `False` are all "no" on the wire, and only one
	of them is falsy in Python. Getting this wrong reads every dismissed alert
	as live, which is the failure mode that would make this whole file green
	while asserting nothing.
	"""
	return str(value or "0").strip().lower() not in ("0", "", "false", "no", "none")


class EndToEndWorkflow(V12TestCase):
	"""One company, one cabin, one worker, one inspection — from an empty site."""

	# ── setup: the whole fixture, through the real tools ────────────────────
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **SWITCHES)
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		self.addCleanup(guard._BUCKETS.clear)
		guard._BUCKETS.clear()
		roles.install_roles()

		self.company = self.a_company()
		self.unit = self.a_cabin()
		self.employee = an_employee_row()
		self.enrol()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	# ── step 1: onboarding, through the tools an operator uses ──────────────
	def a_company(self) -> str:
		if frappe.db.exists("Company", COMPANY):
			return COMPANY
		return self.tool_data("create_company", {"company_name": COMPANY, "abbr": ABBR})["company"]

	def a_cabin(self) -> str:
		self.tool_data("create_parcel", {"owning_entity": COMPANY, "parcel_name": PARCEL, "acreage": 40.0})
		return self.tool_data(
			"create_housing_unit",
			{
				"parcel": PARCEL,
				"unit_name": UNIT,
				"unit_type": "Cabin",
				"capacity": 4,
				"fsma_worker_facility": True,
			},
		)["name"]

	def enrol(self) -> None:
		self.tool_data(
			"create_mobile_user",
			{
				"email": WORKER,
				"full_name": WORKER_NAME,
				"role": "Farm Manager",
				"entity_access": [COMPANY],
			},
		)

	def be_the_worker(self):
		"""Become the phone: the worker's session, on a request that looks like one."""
		self.request({}, headers={}, remote_addr="100.64.0.11")
		frappe.local.session.user = WORKER
		return WORKER

	def be_the_operator(self):
		"""Back to Administrator, for the calls a phone is not allowed to make."""
		frappe.local.session.user = "Administrator"
		return "Administrator"

	# ── the compliance rule that raises the work ────────────────────────────
	def sweep(self, alert_type="housing_inspection_overdue") -> list:
		"""Run the nightly compliance sweep and return this cabin's live alerts.

		FILTERED BY RULE, because this cabin raises more than one. A brand-new
		Housing Unit is both un-inspected and un-detector-tested, and a housing
		inspection answers exactly one of those — taking `[0]` off an unfiltered
		list would assert that recording an inspection cleared the DETECTOR
		alert, which it must not.

		`dismissed` is read through `is_true` rather than for truthiness: this
		double stores a Check as the string `"0"`, which is a perfectly truthy
		Python object and would report every dismissed alert as live.
		"""
		self.be_the_operator()
		self.tool_data("refresh_compliance_alerts", {"company": COMPANY})
		return [
			row
			for row in STORE.rows("Compliance Alert")
			if row.get("source_doctype") == "Housing Unit"
			and row.get("source_docname") == self.unit
			and (alert_type is None or row.get("alert_type") == alert_type)
			and not is_true(row.get("dismissed"))
		]

	def a_task_for_the_cabin(self, source_alert=None) -> str:
		self.be_the_operator()
		payload = {
			"task_name": f"Habitability walk — {UNIT}",
			"task_type": "Inspection",
			"evidence_required": {"photos": True, "signature": True, "findings_text": True},
			"company": COMPANY,
			"creates_record": "Housing Inspection",
			"location_doctype": "Housing Unit",
			"location": self.unit,
		}
		if source_alert:
			payload["source_alert"] = source_alert
		return self.tool_data("create_farm_task", payload)["name"]

	# ── step 2: the upload, in chunks, exactly as the phone sends it ────────
	def upload(self, body: bytes, file_name: str, upload_id: str, chunk_size=None) -> dict:
		"""Stage `body` in slices and finalise it. Returns the finalize response."""
		chunk_size = chunk_size or max(1, len(body))
		slices = [body[at : at + chunk_size] for at in range(0, len(body), chunk_size)] or [b""]
		for index, piece in enumerate(slices):
			staged = files_api.stage_file_chunk(
				upload_id=upload_id,
				file_name=file_name,
				chunk_index=index,
				chunk_count=len(slices),
				total_bytes=len(body),
				data=base64.b64encode(piece).decode("ascii"),
			)
			self.assertEqual(staged["chunk_index"], index)
			self.assertEqual(staged["chunk_count"], len(slices))
		self.assertTrue(staged["complete"], "the last chunk did not complete the upload")
		return files_api.finalize_staged_file(
			upload_id=upload_id,
			file_name=file_name,
			sha256=hashlib.sha256(body).hexdigest(),
			total_bytes=len(body),
		)

	# ── readers for the assertions ──────────────────────────────────────────
	def inspection(self, name: str) -> dict:
		return dict(STORE.get_raw("Housing Inspection", name) or {})

	def photo_rows(self, name: str) -> list:
		return list(self.inspection(name).get("photos") or [])

	def file_row(self, token: str) -> dict:
		return dict(STORE.get_raw("File", token) or {})


# ── 1. the walk ─────────────────────────────────────────────────────────────
class TheWorkflowWalksEndToEnd(EndToEndWorkflow):
	"""Pool → claim → start → upload → complete → Housing Inspection → alert clear.

	One test, on purpose. Splitting it would either re-walk the whole flow per
	assertion (slow, and every failure looks like six) or share state between
	tests through the class (which unittest does not order or isolate). The
	assertions carry their own messages so a failure names its own step.
	"""

	def test_the_whole_pipeline(self):
		# ── the rule raises the work ────────────────────────────────────────
		raised = self.sweep()
		self.assertTrue(
			raised,
			f"{UNIT} has never been inspected and `housing_inspection_overdue` did not raise. "
			"Nothing downstream in this test has anything to answer.",
		)
		alert = raised[0]["name"]

		task = self.a_task_for_the_cabin(source_alert=alert)

		# ── the pool ────────────────────────────────────────────────────────
		self.be_the_worker()
		context = mobile_api.get_current_user_context()
		self.assertEqual(context["user"], WORKER)
		self.assertEqual(context["default_company"], COMPANY)

		pool = mobile_api.list_available_tasks()
		self.assertIn(
			task,
			{row["name"] for row in pool["tasks"]},
			"the inspection is Available and scoped to this worker's only entity, and it is not "
			"in the pool the phone was shown",
		)

		# ── claim: the v0.18.2 regression check ─────────────────────────────
		claimed = mobile_api.claim_task(task=task)
		self.assertEqual(
			claimed["name"],
			task,
			"claim_task answered with no `name`. iOS decodes it with `try c.decode(String.self)` "
			"(FarmTask.swift:105) and throws — this is v0.18.2, which shipped.",
		)
		self.assertIsInstance(claimed["name"], str)
		self.assertTrue(claimed["assignment"], "a claim with no assignment docname is not a claim")
		self.assertTrue(claimed["claimed_at"])
		assignment = claimed["assignment"]

		# ── start ───────────────────────────────────────────────────────────
		started = mobile_api.start_task(task=task, task_assignment=assignment)
		self.assertEqual(started["state"], "In-Progress")
		self.assertTrue(started["started_at"], "duration is counted from started_at and it is unset")
		self.assertEqual(frappe.db.get_value("Farm Task", task, "state"), "In-Progress")

		mine = mobile_api.list_my_tasks()
		self.assertEqual({row["name"] for row in mine["tasks"]}, {task})

		# ── the evidence, in more than one chunk ────────────────────────────
		png = one_pixel_png()
		photo = self.upload(png, "TEST-CABIN-01.png", "e2e-photo", chunk_size=16)
		signature = self.upload(png, "TEST-CABIN-01-sig.png", "e2e-signature")

		self.assertTrue(photo["file_token"], "finalize produced no File handle")
		self.assertTrue(photo["sha256_verified"], "the hash the phone sent was not checked")
		self.assertTrue(photo["is_private"], "evidence must never be committed public")
		self.assertEqual(photo["total_bytes"], len(png))

		# ── complete ────────────────────────────────────────────────────────
		done = mobile_api.complete_task_via_mobile(
			task=task,
			task_assignment=assignment,
			findings_text="Test finding",
			completion_narrative="Walked the cabin end to end.",
			actual_duration_minutes=18,
			witness="Foreman",
			latitude=45.6721,
			longitude=-121.1787,
			evidence_files=[
				{
					"file_token": photo["file_token"],
					"file_name": "TEST-CABIN-01.png",
					"sha256": hashlib.sha256(png).hexdigest(),
					"kind": "photo",
				},
				{
					"file_token": signature["file_token"],
					"file_name": "TEST-CABIN-01-sig.png",
					"sha256": hashlib.sha256(png).hexdigest(),
					"kind": "signature",
				},
			],
		)

		produced = done["created_record_name"]
		self.assertEqual(done["created_record_doctype"], "Housing Inspection")
		self.assertTrue(produced, "the completion produced no compliance record")
		self.assertEqual(done["evidence_filed"], 2)

		# Findings were written, so the record branches to Corrective Action
		# Required and the task waits for a person. That is the designed
		# behaviour, not an incidental one — assert it rather than avoiding it
		# by filing a clean pass.
		self.assertTrue(done["corrective_action_opened"])
		self.assertEqual(frappe.db.get_value("Farm Task", task, "state"), "Awaiting-Review")

		# ── the assertions that are the whole point ─────────────────────────
		record = self.inspection(produced)
		self.assertTrue(record, f"{produced} was reported created and is not on the site")
		self.assertEqual(record["unit"], self.unit)
		self.assertEqual(record["source_task"], task)
		self.assertEqual(record["findings"], "Test finding")

		rows = self.photo_rows(produced)
		self.assertEqual(len(rows), 2, f"the inspection's `photos` child table holds {len(rows)} rows, not 2")
		filed = {row.get("file") for row in rows}
		self.assertIn(photo["file_token"], filed, "the photograph is not on the record it evidences")
		self.assertIn(signature["file_token"], filed, "the signature is not on the record it attests")

		# v0.18.4: the File must cascade its read permission off the parent, or
		# only the worker who took it can ever open it.
		for token in (photo["file_token"], signature["file_token"]):
			row = self.file_row(token)
			with self.subTest(file=token):
				self.assertEqual(
					row.get("attached_to_doctype"),
					"Housing Inspection",
					"the File is not attached to the record, so Frappe has nothing to cascade "
					"read permission from and an auditor opening the inspection is refused — "
					"this is v0.18.4, which shipped",
				)
				self.assertEqual(row.get("attached_to_name"), produced)
				self.assertTrue(row.get("is_private"), "evidence went public")

		# ── the alert the work answered clears itself ───────────────────────
		still_open = {row["name"] for row in self.sweep()}
		self.assertNotIn(
			alert,
			still_open,
			"the inspection was recorded and `housing_inspection_overdue` still fires. An alert "
			"only ever goes away because the condition stopped being true — if it is still true "
			"the record did not move the register, which means the loop is open.",
		)
		self.assertTrue(
			is_true(frappe.db.get_value("Compliance Alert", alert, "dismissed")),
			"the alert was neither answered nor dismissed",
		)
		self.assertTrue(
			is_true(frappe.db.get_value("Compliance Alert", alert, "auto_dismissed")),
			"the alert was dismissed by hand rather than by the condition ceasing to be true. "
			"That distinction is the entire architecture — see inspections.py's docstring.",
		)


# ── 2. the pieces the walk depends on, asserted where they can be isolated ──
class ThePipelineRefusesWhatItShould(EndToEndWorkflow):
	"""The walk proves the path works. These prove it is not just permissive."""

	def setUp(self):
		super().setUp()
		self.task = self.a_task_for_the_cabin()

	def test_a_completion_without_the_signature_the_contract_demands_is_refused(self):
		"""The refusal the whole evidence contract exists for, on the real path."""
		self.be_the_worker()
		claimed = mobile_api.claim_task(task=self.task)
		mobile_api.start_task(task=self.task, task_assignment=claimed["assignment"])
		photo = self.upload(one_pixel_png(), "only-photo.png", "e2e-only-photo")

		with self.assertRaises(frappe.ValidationError) as caught:
			mobile_api.complete_task_via_mobile(
				task=self.task,
				task_assignment=claimed["assignment"],
				findings_text="Test finding",
				evidence_files=[{"file_token": photo["file_token"], "kind": "photo"}],
			)
		self.assertIn("signature", str(caught.exception))
		self.assertEqual(
			STORE.rows("Housing Inspection"),
			[],
			"a refused completion wrote a compliance record anyway",
		)

	def test_the_evidence_upload_runs_as_the_worker_and_not_as_an_operator(self):
		"""v0.18.3. The staging path was written for the MCP System User and had
		never been run by a field account, which is the only account that uses
		it. `_assert_owner` binds the session to the upload — that only means
		anything if the session IS the worker."""
		self.be_the_worker()
		finalized = self.upload(one_pixel_png(), "owned.png", "e2e-owned")
		self.assertEqual(
			STORE.get_raw("File", finalized["file_token"]).get("owner"),
			WORKER,
			"the evidence File is not owned by the worker who uploaded it",
		)

	def test_one_workers_staging_session_cannot_be_finalised_by_another(self):
		"""Ownership is the separation between two phones, and it is free only
		while the call actually runs as the caller."""
		self.be_the_worker()
		files_api.stage_file_chunk(
			upload_id="e2e-theirs",
			file_name="theirs.png",
			chunk_index=0,
			chunk_count=1,
			total_bytes=4,
			data=base64.b64encode(b"abcd").decode("ascii"),
		)

		self.tool_data(
			"create_mobile_user",
			{
				"email": "e2e-other@test.local",
				"full_name": "Somebody Else",
				"role": "Field Worker",
				"entity_access": [COMPANY],
			},
		)
		self.request({}, headers={}, remote_addr="100.64.0.12")
		frappe.local.session.user = "e2e-other@test.local"
		with self.assertRaises(Exception):
			files_api.finalize_staged_file(
				upload_id="e2e-theirs",
				file_name="theirs.png",
				sha256=hashlib.sha256(b"abcd").hexdigest(),
				total_bytes=4,
			)

	def test_a_worker_cannot_reach_another_entitys_cabin_through_any_of_this(self):
		"""The scoping the whole mobile surface rests on, checked on the built
		fixture rather than the seeded one."""
		self.tool_data("create_company", {"company_name": "Other E2E LLC", "abbr": "OEL"})
		outsider_task = self.tool_data(
			"create_farm_task",
			{
				"task_name": "Not this worker's",
				"task_type": "Inspection",
				"evidence_required": {"photos": True},
				"company": "Other E2E LLC",
			},
		)["name"]

		self.be_the_worker()
		pool = {row["name"] for row in mobile_api.list_available_tasks()["tasks"]}
		self.assertNotIn(outsider_task, pool)
		with self.assertRaises(frappe.ValidationError):
			mobile_api.get_task(task=outsider_task)
		with self.assertRaises(frappe.ValidationError):
			mobile_api.claim_task(task=outsider_task)


# ── 3. the same walk, filed as a clean pass ─────────────────────────────────
class ACleanPassClosesTheLoopToo(EndToEndWorkflow):
	"""The other half of the branch. A cabin that is FINE has to be recordable,
	and `clean_pass` is the flag that makes an empty findings field an answer
	rather than an omission — see `CompletionSubmission.swift:36-45`."""

	def test_a_clean_walk_records_and_completes_rather_than_opening_an_action(self):
		alert = self.sweep()[0]["name"]
		task = self.a_task_for_the_cabin(source_alert=alert)

		self.be_the_worker()
		claimed = mobile_api.claim_task(task=task)
		mobile_api.start_task(task=task, task_assignment=claimed["assignment"])
		png = one_pixel_png()
		photo = self.upload(png, "clean.png", "e2e-clean-photo")
		signature = self.upload(png, "clean-sig.png", "e2e-clean-sig")

		done = mobile_api.complete_task_via_mobile(
			task=task,
			task_assignment=claimed["assignment"],
			findings_text="",
			clean_pass=True,
			completion_narrative="Nothing wrong.",
			evidence_files=[
				{"file_token": photo["file_token"], "kind": "photo"},
				{"file_token": signature["file_token"], "kind": "signature"},
			],
		)
		produced = done["created_record_name"]
		self.assertTrue(produced)
		self.assertFalse(
			done["corrective_action_opened"],
			"a clean pass opened a corrective action against a cabin that is fine",
		)
		self.assertEqual(frappe.db.get_value("Farm Task", task, "state"), "Completed")
		self.assertEqual(self.inspection(produced)["workflow_state"], "Recorded")
		self.assertNotIn(alert, {row["name"] for row in self.sweep()})

		# The evidence still cascades — a clean pass is not a lesser record.
		for token in (photo["file_token"], signature["file_token"]):
			with self.subTest(file=token):
				self.assertEqual(self.file_row(token).get("attached_to_name"), produced)


# ── helpers ─────────────────────────────────────────────────────────────────
def an_employee_row() -> str:
	"""The worker's Employee record.

	Seeded rather than built through `create_employee`: the HR tool requires a
	date of joining, a company default holiday list and a naming series this
	double does not carry, and an Employee is not what this file is testing.
	What it IS testing needs the record to exist and to name `user_id`, because
	`api/mobile._employee` refuses every call without it — which is itself a
	failure mode worth stating, so the refusal has its own test below.
	"""
	STORE.seed(
		"Employee",
		[
			{
				"name": EMPLOYEE,
				"employee_name": WORKER_NAME,
				"user_id": WORKER,
				"company": COMPANY,
				"status": "Active",
			}
		],
	)
	return EMPLOYEE


class TheOnboardingGapsSayWhatToDo(EndToEndWorkflow):
	"""Every refusal on the way in has to name its own fix, because the person
	reading it is an operator standing next to a worker holding a phone."""

	def test_a_worker_with_no_employee_record_is_told_exactly_what_to_set(self):
		STORE.tables["Employee"].pop(EMPLOYEE, None)
		self.be_the_worker()
		task = None
		try:
			task = self.a_task_for_the_cabin()
		finally:
			self.be_the_worker()
		with self.assertRaises(frappe.ValidationError) as caught:
			mobile_api.reject_task(task=task, reason="cannot reach it")
		message = str(caught.exception)
		self.assertIn("user_id", message)
		self.assertIn("Employee", message)

	def test_the_grant_is_what_opens_the_door_and_revoking_it_shuts_it(self):
		self.be_the_worker()
		self.assertEqual(mobile_api.get_current_user_context()["user"], WORKER)

		self.be_the_operator()
		self.tool_data("revoke_mobile_user", {"email": WORKER, "reason": "end of season"})

		self.be_the_worker()
		with self.assertRaises(frappe.PermissionError):
			mobile_api.get_current_user_context()

	def test_the_login_qr_the_worker_scans_carries_this_entity_and_no_secret_twice(self):
		self.be_the_operator()
		payload = self.tool_data("generate_mobile_login_qr", {"user": WORKER})["payload"]
		self.assertEqual(payload["type"], "farm_ops_login")
		self.assertTrue(payload["url"].startswith("https://"))
		# The credential is in the QR by design; what must not happen is it also
		# landing in the audit row that records the QR being made.
		rows = [
			row for row in STORE.rows("MCP Action Log") if row.get("tool_name") == "generate_mobile_login_qr"
		]
		self.assertTrue(rows)
		blob = json.dumps(rows[-1], default=str)
		self.assertNotIn(payload["api_secret"], blob)


# ── 5. v0.19.0: onboarding ends in the training register ────────────────────
class OnboardingReachesTheAuditPacket(EndToEndWorkflow):
	"""onboard → record_training → the training is in the packet for its regime.

	THE SEAM THIS COVERS. `test_training.py` proves each tool in isolation against
	a seeded HR fixture; `test_audit_packets.py` proves the packet assembles. What
	neither proves is that a person hired through the real onboarding path — a
	company built by `create_company`, an Employee whose company came from that
	call rather than from a fixture literal — produces a training record the
	packet generator can actually find. That join is the whole point of tagging by
	regime, and it is exactly the kind of seam v0.18.2 through v0.18.4 were each
	found at by somebody holding a phone rather than by CI.

	It is ONE test, walked forward, for the same reason `test_the_whole_pipeline`
	is: splitting it would either re-walk the flow per assertion or share state
	between tests, and both are worse than a long test whose failure line names
	the step.
	"""

	def test_a_new_hires_training_reaches_the_packet_for_its_regime(self):
		self.be_the_operator()

		# 1. The person. Through `create_employee`, so this covers the tool an
		#    operator actually uses rather than a seeded row.
		supervisor = self.tool_data(
			"create_employee",
			{"employee_name": "Sam Foreman", "company": COMPANY, "date_of_joining": "2026-01-05"},
		)["employee"]

		# 2. The training. Tagged WPS and GAP: one afternoon, two audits.
		completed = str(frappe.utils.add_days(frappe.utils.today(), -3))
		training_record = self.tool_data(
			"record_training",
			{
				"employee": self.employee,
				"training_type": "WPS Handler Training",
				"completed_date": completed,
				"completed_time": "08:15",
				"regimes": ["WPS", "GAP"],
				"content_topics_covered": "Label reading, PPE, REI, decontamination, heat",
				"provider": "OSU Extension",
				"expires_date": str(frappe.utils.add_days(frappe.utils.today(), 362)),
				"person_performed_signature": "/files/worker-signature.png",
			},
		)
		self.assertEqual(training_record["company"], COMPANY)
		self.assertEqual(training_record["regimes"], ["GAP", "WPS"])
		self.assertEqual(training_record["status"], "Active")

		# 3. The §112.161(b) review, which is the element a GAP-only operation
		#    lacks and FDA cites even where the training itself was fine.
		self.assertFalse(training_record["supervisor_reviewed"])
		signed = self.tool_data(
			"sign_training_supervisor_review",
			{"name": training_record["name"], "supervisor": supervisor},
		)
		self.assertTrue(signed["supervisor_reviewed"])
		self.assertEqual(signed["supervisor_reviewed_by"], supervisor)

		# 4. The register answers by regime, which is how a packet is assembled.
		listed = self.tool_data("list_trainings", {"company": COMPANY, "regime": "WPS"})
		self.assertEqual([row["name"] for row in listed["records"]], [training_record["name"]])
		self.assertEqual(listed["without_supervisor_review"], [])

		# 5. THE JOIN. The same record, found by the packet generator for BOTH
		#    audits it was tagged for — and absent from the one it was not.
		period = {
			"company": COMPANY,
			"period_start": str(frappe.utils.add_days(frappe.utils.today(), -30)),
			"period_end": frappe.utils.today(),
			"dry_run": True,
		}
		for audit_type in ("GAP", "EPA"):
			with self.subTest(audit_type=audit_type):
				packet = self.tool_data("generate_audit_packet", {**period, "audit_type": audit_type})
				self.assertEqual(packet["section_counts"]["training"], 1, packet["section_counts"])

		# A record tagged WPS and GAP is not organic-handling evidence, and a
		# packet that pulled it into an NOP-shaped section would be the quiet
		# wrong-evidence bug this whole tagging scheme exists to prevent.
		narrowed = self.tool_data("generate_audit_packet", {**period, "audit_type": "GAP", "regime": "NOP"})
		self.assertEqual(narrowed["section_counts"]["training"], 0)
		self.assertEqual(narrowed["training_regime"], "NOP")

		# 6. And the calendar has nothing to say about it, because it does not
		#    expire for another year. An alert here would be the chronological
		#    failure mode the whole rule engine is written against.
		self.tool_data("refresh_compliance_alerts", {"company": COMPANY})
		live = [
			row
			for row in STORE.rows("Compliance Alert")
			if row.get("alert_type") == "training_expiring" and not is_true(row.get("dismissed"))
		]
		self.assertEqual(live, [])


class TheHotShiftReachesTheOSHAPacket(EndToEndWorkflow):
	"""Onboard, train, form a crew, work the day, document the heat, close it.

	THE JOIN THIS EXISTS FOR. `OnboardingReachesTheAuditPacket` proves a training
	record written through the tools is found by the packet generator for the
	regimes it was tagged. This proves the v0.19.3 half: that a SHIFT formed by a
	foreman, with events logged against it and a heat record filed on top,
	produces both a payroll row an HR report can count AND a section an Oregon
	OSHA inspector is handed — from one afternoon, through the real tools, with
	the kill switches an operator ticks.

	Every seam here is one that only exists between releases. The training the
	worker got in step 2 is what `create_heat_exposure_event` checks the crew
	against in step 6, and it checks it AS OF THE DAY OF THE SHIFT. The crew rows
	written in step 3 are what the Attendance bridge spans in step 7. The heat
	record filed in step 6 is what the OSHA packet pulls in step 9. None of those
	is visible from inside the module that owns either end.

	It is ONE test, walked forward, for the same reason the other end-to-end walks
	here are: splitting it would either re-walk the flow per assertion or share
	state between tests, and both are worse than a long test whose failure line
	names the step.
	"""

	def setUp(self):
		super().setUp()
		# The Attendance bridge writes `Attendance.farm_shift`, a Custom Field this
		# app installs on every migrate. Running the real installer rather than
		# registering the column by hand is the whole point of an end-to-end walk:
		# a fixture that hand-wrote the schema would prove the bridge works against
		# a site that does not exist.
		compliance_fields.install_compliance_fields(respect_switch=False)
		# `get_attendance_summary` is gated on the hrms app rather than on the
		# Attendance doctype, and step 8 is the assertion that farm_hr counts a
		# shift-formed day exactly as it counts a hand-entered one. The app is
		# declared and NOTHING ELSE is seeded: every Employee and every Attendance
		# row in this walk is written by the tools under test.
		if "hrms" not in STORE.installed_apps:
			STORE.installed_apps.append("hrms")

	def test_a_crew_shift_produces_payroll_rows_and_an_osha_section(self):
		self.be_the_operator()
		today = frappe.utils.today()

		def at(hour: int, minute: int = 0) -> str:
			return f"{today} {hour:02d}:{minute:02d}:00"

		# 1. The foreman. The responsible party -1131 names, and the person
		#    §112.161(b) asks to sign — through `create_employee`, so the tool an
		#    operator actually uses is the tool this covers.
		foreman = self.tool_data(
			"create_employee",
			{"employee_name": "Hot Day Foreman", "company": COMPANY, "date_of_joining": "2026-01-05"},
		)["employee"]

		# 2. Heat illness prevention training for the worker who will be on the
		#    crew. -1131 requires it annually AND before work at a site where the
		#    heat index will reach 80 °F, so this is the record step 6 checks.
		training_record = self.tool_data(
			"record_training",
			{
				"employee": self.employee,
				"training_type": "Heat Illness Prevention",
				"completed_date": str(frappe.utils.add_days(today, -20)),
				"expires_date": str(frappe.utils.add_days(today, 345)),
				"regimes": ["OR-OSHA"],
				"content_topics_covered": (
					"Heat index, water, shade, symptoms, reporting, emergency response"
				),
				"person_performed_signature": "/files/worker-signature.png",
			},
		)
		self.assertEqual(training_record["regimes"], ["OR-OSHA"])

		# 3. The shift. The foreman forms the crew; nobody clocks themselves in.
		shift = self.tool_data(
			"start_shift",
			{
				"foreman": foreman,
				"location": "Block 7 North",
				"shift_type": "Harvest",
				"farm_location_gps": "45.52,-122.68",
				"start_datetime": at(6),
				"crew_employees": [self.employee],
			},
		)
		self.assertEqual(shift["company"], COMPANY)
		self.assertEqual(shift["status"], "Active")
		self.assertEqual(shift["crew_size"], 1)
		# Rostered at the beginning means present from the beginning.
		self.assertEqual(shift["crew"][0]["joined_at"], at(6))

		# 4. A late arrival, who joins when somebody says so rather than at the
		#    shift's start — and leaves before the end. This is the person the
		#    Attendance bridge has to get right.
		late = self.tool_data(
			"create_employee",
			{"employee_name": "Late Picker", "company": COMPANY, "date_of_joining": "2026-06-01"},
		)["employee"]
		# Trained too, and the first draft of this test found out the hard way why
		# that line has to be here: `create_heat_exposure_event` REFUSED the
		# record at step 6 because one worker on the crew had no current heat
		# training, naming them. That refusal is the point of the check — the same
		# packet carries this record and the register, and a packet that
		# contradicts itself is worse than one with a gap — and it is exactly the
		# kind of seam that only shows up when the whole walk is run.
		self.tool_data(
			"record_training",
			{
				"employee": late,
				"training_type": "Heat Illness Prevention",
				"completed_date": str(frappe.utils.add_days(today, -5)),
				"expires_date": str(frappe.utils.add_days(today, 360)),
				"regimes": ["OR-OSHA"],
				"content_topics_covered": (
					"Heat index, water, shade, symptoms, reporting, emergency response"
				),
			},
		)
		self.tool_data("add_worker_to_shift", {"shift": shift["name"], "employee": late, "joined_at": at(9)})
		self.tool_data(
			"remove_worker_from_shift",
			{"shift": shift["name"], "employee": late, "left_at": at(12)},
		)

		# 5. The timeline. THE EVIDENCE, as against the claim: Oregon's rule does
		#    not ask whether water was available in principle.
		for hour, kind in ((9, "Water Break"), (11, "Shade Break"), (13, "Rest Cycle")):
			self.tool_data(
				"log_shift_event",
				{
					"shift": shift["name"],
					"event_type": kind,
					"event_datetime": at(hour),
					"description": f"{kind} called for the whole crew.",
				},
			)
		self.tool_data(
			"log_shift_event",
			{
				"shift": shift["name"],
				"event_type": "Supervisor Observation",
				"event_datetime": at(14),
				"producer_record_doctype": "Employee Training Record",
				"producer_record_name": training_record["name"],
			},
		)

		# 6. The heat record, with `training_verified=1` — which is only accepted
		#    because step 2 actually happened, and is checked AS OF THE DAY OF THE
		#    SHIFT rather than as of today.
		heat = self.tool_data(
			"create_heat_exposure_event",
			{
				"farm_shift": shift["name"],
				"max_temp_f": 96,
				"max_heat_index_f": 101,
				"threshold_crossed_at": at(10, 40),
				"water_provided": True,
				"shade_provided": True,
				"mandatory_rest_taken": True,
				"heat_illness_signs_observed": False,
				"worker_reported_symptoms": False,
				"emergency_response_activated": False,
				"training_verified": True,
				"supervisor_signature_file_token": "/files/foreman-signature.png",
			},
		)
		self.assertTrue(heat["submitted"])
		self.assertTrue(heat["training_verified"])
		self.assertEqual(heat["obligation_gaps"], [])
		self.assertEqual(heat["regulation_citation"], "OAR 437-004-1131")
		self.assertIn(WORKER_NAME, heat["crew_training"]["with_current_training"])
		self.assertEqual(heat["shift_timeline_events"], 4)

		# 7. The close, and the payroll rows it writes. THE SPANS ARE PER PERSON:
		#    the late picker worked three hours of a nine-hour shift, and a row
		#    claiming nine would be wrong in the employer's favour.
		closed = self.tool_data(
			"end_shift",
			{
				"shift": shift["name"],
				"end_datetime": at(15),
				"supervisor_signature_file_token": "/files/foreman-signature.png",
				"foreman_notes": "Hot from eleven. Crew held up.",
			},
		)
		self.assertEqual(closed["status"], "Closed")
		self.assertTrue(closed["supervisor_review_on"])
		self.assertEqual(closed["attendance_created"], 2)

		attendance = {row["employee"]: row for row in STORE.rows("Attendance") if row.get("farm_shift")}
		self.assertEqual(set(attendance), {self.employee, late})
		self.assertEqual(str(attendance[self.employee]["in_time"]), at(6))
		self.assertEqual(str(attendance[self.employee]["out_time"]), at(15))
		self.assertEqual(attendance[self.employee]["working_hours"], 9.0)
		self.assertEqual(str(attendance[late]["in_time"]), at(9))
		self.assertEqual(str(attendance[late]["out_time"]), at(12))
		self.assertEqual(attendance[late]["working_hours"], 3.0)
		for row in attendance.values():
			self.assertEqual(int(row["docstatus"]), 1, "a draft row is not a fact about attendance")
			self.assertEqual(row["farm_shift"], shift["name"])

		# 8. And farm_hr counts the shift-formed day exactly as it counts a
		#    hand-entered one, which is the whole reason the bridge exists.
		summary = self.tool_data("get_attendance_summary", {"from_date": today, "to_date": today})
		by_employee = {row["employee"]: row for row in summary["employees"]}
		self.assertEqual(by_employee[self.employee]["counts"].get("Present"), 1)
		self.assertEqual(by_employee[late]["counts"].get("Present"), 1)

		# 9. THE JOIN. An Oregon OSHA inspector asking about this season is handed
		#    a packet whose heat section carries this record — and the section is
		#    NOT on the packets for the schemes that never ask about -1131.
		period = {
			"company": COMPANY,
			"period_start": str(frappe.utils.add_days(today, -30)),
			"period_end": today,
			"dry_run": True,
		}
		osha = self.tool_data("generate_audit_packet", {**period, "audit_type": "OSHA"})
		self.assertEqual(osha["section_counts"]["heat_exposure"], 1, osha["section_counts"])
		# The training that made step 6 legal is in the same packet, under the
		# OR-OSHA tag it was filed with — two records for one crew, from one day.
		self.assertEqual(osha["section_counts"]["training"], 2)
		# And the heat section discloses nothing, because this shift met every
		# obligation. The other sections disclose plenty — an empty SOP library, an
		# unrecorded I-9 — which is what a disclosure list is for.
		self.assertEqual([entry for entry in osha["disclosures"] if entry["section"] == "heat_exposure"], [])

		# The section itself, built the way the renderer sees it. The tool's dry
		# run returns counts rather than rows, and a count of one would be equally
		# true of the wrong record.
		section = audit_packets.build(
			audit_packets.get("OSHA"), COMPANY, period["period_start"], period["period_end"]
		)
		heat_section = next(entry for entry in section["sections"] if entry["key"] == "heat_exposure")
		self.assertEqual(heat_section["rows"][0]["record"], heat["name"])
		self.assertEqual(heat_section["rows"][0]["shift"], shift["name"])
		self.assertTrue(heat_section["rows"][0]["training"])
		self.assertNotIn("with_unmet_obligations", heat_section)

		# A GAP auditor does not audit Oregon's heat rule, and a packet that
		# handed them a heat register would invite a question nobody wanted to
		# answer — exactly as a DOL packet containing a GlobalGAP certificate would.
		gap = self.tool_data("generate_audit_packet", {**period, "audit_type": "GAP"})
		self.assertNotIn("heat_exposure", gap["section_counts"])

		# 10. And the evidence chain reads end to end from either direction.
		read_back = self.tool_data("get_heat_exposure_event", {"name": heat["name"]})
		self.assertEqual(read_back["shift"]["name"], shift["name"])
		self.assertEqual(read_back["shift"]["compliance_event_count"], 4)
		self.assertEqual(read_back["obligation_gaps"], [])
		from_the_shift = self.tool_data("get_shift", {"name": shift["name"]})
		self.assertEqual(from_the_shift["heat_exposure_event"]["name"], heat["name"])
		# The row of the person who left early still says they were here, which is
		# what a wage claim turns on.
		left_row = next(row for row in from_the_shift["crew"] if row["employee"] == late)
		self.assertTrue(left_row["left_early"])
		self.assertEqual(left_row["present_until"], at(12))


class TheWeatherTimelineDocumentsTheShiftItself(EndToEndWorkflow):
	"""v0.19.4. The half of the evidence nobody could produce before.

	`TheHotShiftReachesTheOSHAPacket` walks the same afternoon with the maxima
	TYPED IN — 96 °F and a 101 °F heat index, because until this release that was
	the only way they could get onto the record, and "about ninety-five" written
	from memory in the evening is what an investigator discounts.

	This walks it with nobody typing a temperature at all. The scheduled sweep
	documents the open shift, a reading over the threshold logs its own
	compliance event, the foreman files the heat record and its maxima compute
	off the timeline, and the whole thing lands in the packet an Oregon OSHA
	inspector is handed.

	THE ASSERTION THAT MATTERS IS THE ONE ABOUT WHAT IS NOT WRITTEN. The sweep
	sees 82 °F and does NOT create a Heat Exposure Event. That record says which
	crew was exposed, what water was provided, whether the rest cycle was taken
	and whether anybody showed signs, and it carries a signature — five
	judgements by the person who was standing there. A machine filing one from a
	temperature would be producing an attestation with nobody behind it, in the
	one place where having nobody behind it is the whole failure. The sweep
	surfaces; the foreman decides.

	The far end is faked, because the alternative is a test that fails when a
	free public API is slow. Everything on THIS side of the HTTP call is real:
	the settings single, the cache, the threshold arithmetic, the child-table
	append, the controller that computes the maxima, and the packet builder.
	"""

	def setUp(self):
		super().setUp()
		compliance_fields.install_compliance_fields(respect_switch=False)
		if "hrms" not in STORE.installed_apps:
			STORE.installed_apps.append("hrms")

		from erpnext_mcp.services import weather

		from .test_weather import FakeOpenMeteo

		weather.reset_cache()
		self.addCleanup(weather.reset_cache)
		self.api = FakeOpenMeteo()
		self._install_fake_open_meteo()

	def _install_fake_open_meteo(self):
		"""A `requests` module the fake answers, restored afterwards.

		`services/weather._get_json` imports `requests` inside the function, so a
		bench somehow missing it loses weather and nothing else — which means the
		import has to be satisfied at call time. Installing a module exercises the
		real import statement rather than replacing the function that runs it.
		"""
		import sys
		import types as _types

		module = _types.ModuleType("requests")
		module.get = self.api.get
		previous = sys.modules.get("requests")
		sys.modules["requests"] = module

		def restore():
			if previous is None:
				sys.modules.pop("requests", None)
			else:
				sys.modules["requests"] = previous

		self.addCleanup(restore)

	def test_the_sweep_documents_the_shift_and_the_foreman_files_the_record(self):
		from erpnext_mcp.services import weather

		self.be_the_operator()
		today = frappe.utils.today()

		def at(hour: int, minute: int = 0) -> str:
			return f"{today} {hour:02d}:{minute:02d}:00"

		# 1. The foreman, and the crew member's heat training. Same two steps as
		#    the typed-maxima walk, because `create_heat_exposure_event` still
		#    checks the register as of the day of the shift and a claim it
		#    contradicts is still refused.
		foreman = self.tool_data(
			"create_employee",
			{"employee_name": "Sweep Foreman", "company": COMPANY, "date_of_joining": "2026-01-05"},
		)["employee"]
		self.tool_data(
			"record_training",
			{
				"employee": self.employee,
				"training_type": "Heat Illness Prevention",
				"completed_date": str(frappe.utils.add_days(today, -20)),
				"expires_date": str(frappe.utils.add_days(today, 345)),
				"regimes": ["OR-OSHA"],
				"content_topics_covered": (
					"Heat index, water, shade, symptoms, reporting, emergency response"
				),
				"person_performed_signature": "/files/worker-signature.png",
			},
		)

		# 2. The shift. `farm_location_gps` is the weather anchor, and a shift
		#    without it gets no timeline however hot the day is.
		shift = self.tool_data(
			"start_shift",
			{
				"foreman": foreman,
				"location": "Block 7 North",
				"shift_type": "Harvest",
				"farm_location_gps": "45.52,-122.68",
				"start_datetime": at(6),
				"crew_employees": [self.employee],
			},
		)
		self.assertEqual(shift["weather_reading_count"], 0)

		# 3. THE SCHEDULED SWEEP, called the way the cron calls it: bare, with no
		#    arguments and no operator in the loop. 82 °F at 30 % humidity — over
		#    the shipped 80 °F air-temperature threshold and under the heat-index
		#    one, which is the ordinary Oregon August morning and the case a rule
		#    keyed only on heat index would miss.
		self.api.set_current(temp=82.0, humidity=30.0, when=at(10, 45))
		self.assertEqual(weather.sweep_open_shifts(), 1)

		timeline = self.tool_data("get_weather_timeline", {"shift": shift["name"]})
		self.assertEqual(timeline["count"], 1)
		self.assertEqual(timeline["readings"][0]["temp_f"], 82.0)
		self.assertEqual(timeline["readings"][0]["source"], weather.SOURCE_CURRENT)
		self.assertEqual(timeline["first_crossing"], at(10, 45))

		# 4. The crossing logged ITSELF, because it is arithmetic over a stored
		#    reading and needs nobody's judgement. `logged_by` is empty: nobody
		#    logged it, and naming the foreman would put their identity against an
		#    observation they did not make.
		with_events = self.tool_data("get_shift", {"name": shift["name"]})
		crossings = [
			row for row in with_events["compliance_events"] if row["event_type"] == weather.THRESHOLD_EVENT
		]
		self.assertEqual(len(crossings), 1, with_events["compliance_events"])
		self.assertIsNone(crossings[0]["logged_by"])
		self.assertEqual(crossings[0]["temp_f"], 82.0)

		# 5. AND NO HEAT EXPOSURE EVENT EXISTS. This is the line the release
		#    draws, asserted rather than described.
		self.assertEqual(STORE.rows("Heat Exposure Event"), [])

		# 6. The foreman's own timeline, logged as things happened. The snapshot
		#    on each row is filled from the reading current at its instant, which
		#    is why an auditor reading the event does not have to reconstruct
		#    which of the day's readings was in force.
		self.tool_data(
			"log_shift_event",
			{
				"shift": shift["name"],
				"event_type": "Water Break",
				"event_datetime": at(11),
				"description": "Cooler refilled, whole crew drank.",
			},
		)
		water = next(
			row
			for row in self.tool_data("get_shift", {"name": shift["name"]})["compliance_events"]
			if row["event_type"] == "Water Break"
		)
		self.assertEqual(water["temp_f"], 82.0)

		# 7. THE FOREMAN DECIDES IT IS A RECORD, and files it with no maxima at
		#    all — the fields that used to be typed from memory. They compute off
		#    the timeline, and `threshold_crossed_at` is the EARLIEST crossing
		#    because that is where -1131's obligations start running from.
		heat = self.tool_data(
			"create_heat_exposure_event",
			{
				"farm_shift": shift["name"],
				"water_provided": True,
				"shade_provided": True,
				"mandatory_rest_taken": True,
				"heat_illness_signs_observed": False,
				"worker_reported_symptoms": False,
				"emergency_response_activated": False,
				"training_verified": True,
				"supervisor_signature_file_token": "/files/foreman-signature.png",
			},
		)
		self.assertEqual(heat["max_temp_f"], 82.0)
		self.assertEqual(heat["max_heat_index_f"], weather.heat_index_f(82.0, 30.0))
		self.assertEqual(str(heat["threshold_crossed_at"]), at(10, 45))
		self.assertTrue(heat["submitted"])
		self.assertEqual(heat["obligation_gaps"], [])

		# 8. The close. The shift stops being swept the moment it has an end time,
		#    which is the same fact `status` is derived from.
		closed = self.tool_data(
			"end_shift",
			{
				"shift": shift["name"],
				"end_datetime": at(15),
				"supervisor_signature_file_token": "/files/foreman-signature.png",
			},
		)
		self.assertEqual(closed["status"], "Closed")
		self.assertEqual(closed["attendance_created"], 1)
		self.assertEqual(weather.sweep_open_shifts(), 0)

		# 9. And the packet an Oregon OSHA inspector is handed carries the record
		#    whose numbers nobody typed.
		period = {
			"company": COMPANY,
			"period_start": str(frappe.utils.add_days(today, -30)),
			"period_end": today,
			"dry_run": True,
		}
		osha = self.tool_data("generate_audit_packet", {**period, "audit_type": "OSHA"})
		self.assertEqual(osha["section_counts"]["heat_exposure"], 1, osha["section_counts"])
		section = audit_packets.build(
			audit_packets.get("OSHA"), COMPANY, period["period_start"], period["period_end"]
		)
		heat_section = next(entry for entry in section["sections"] if entry["key"] == "heat_exposure")
		self.assertEqual(heat_section["rows"][0]["record"], heat["name"])
		self.assertEqual(heat_section["rows"][0]["shift"], shift["name"])

	def test_a_shift_that_ran_before_the_service_was_on_is_backfilled(self):
		"""THE OTHER HALF, and the one every site has on the day it upgrades: a
		season of closed shifts with an empty weather table. A shift with no
		timeline is not one that was compliant or non-compliant — it is one
		nobody can say anything about, and Open-Meteo still knows what the
		weather was that day.
		"""
		from erpnext_mcp.services import weather

		self.be_the_operator()
		today = frappe.utils.today()

		def at(hour: int, minute: int = 0) -> str:
			return f"{today} {hour:02d}:{minute:02d}:00"

		foreman = self.tool_data(
			"create_employee",
			{"employee_name": "Backfill Foreman", "company": COMPANY, "date_of_joining": "2026-01-05"},
		)["employee"]
		shift = self.tool_data(
			"start_shift",
			{
				"foreman": foreman,
				"location": "Block 3",
				"shift_type": "Harvest",
				"farm_location_gps": "45.52,-122.68",
				"start_datetime": at(6),
				"crew_employees": [self.employee],
			},
		)
		self.tool_data(
			"end_shift",
			{
				"shift": shift["name"],
				"end_datetime": at(15),
				"supervisor_signature_file_token": "/files/foreman-signature.png",
			},
		)

		# The worklist finds it, and says why: a nine-hour shift with no readings
		# is thinner than one reading per hour by nine.
		worklist = self.tool_data("list_shifts_missing_weather", {"company": COMPANY})
		entry = next(row for row in worklist["shifts"] if row["name"] == shift["name"])
		self.assertEqual(entry["weather_reading_count"], 0)
		self.assertEqual(entry["readings_expected"], 9)

		self.api.set_archive(hours=24, temp=91.0, humidity=45.0, start_hour=0, day=today)
		report = self.tool_data("backfill_weather_for_shift", {"shift": shift["name"]})
		# Ten readings — 06:00 through 15:00 inclusive — and fourteen dropped for
		# falling outside the shift's own period. The archive answers by whole
		# days, and a morning shift must not acquire a timeline running to
		# midnight.
		self.assertEqual(report["added"], 10)
		self.assertEqual(report["outside_the_shift_period"], 14)
		self.assertEqual(report["source"], weather.SOURCE_ARCHIVE)

		# NO COMPLIANCE EVENT, however hot the archive says it was. A Threshold
		# Crossed row on a closed and signed shift would be an observation nobody
		# made, sitting beside water breaks somebody did.
		self.assertEqual(report["readings_at_or_above_the_heat_threshold"], 10)
		read_back = self.tool_data("get_shift", {"name": shift["name"]})
		self.assertEqual(
			[row for row in read_back["compliance_events"] if row["event_type"] == weather.THRESHOLD_EVENT],
			[],
		)

		# Running it again adds nothing. A reading is immutable evidence and is
		# only ever appended.
		again = self.tool_data("backfill_weather_for_shift", {"shift": shift["name"]})
		self.assertEqual(again["added"], 0)
		self.assertEqual(again["skipped_as_duplicate"], 10)

		# And the worklist no longer names it.
		after = self.tool_data("list_shifts_missing_weather", {"company": COMPANY})
		self.assertNotIn(shift["name"], [row["name"] for row in after["shifts"]])


# ── 8. the season becomes a number ──────────────────────────────────────────
class TheSeasonReachesAPerAcreFigure(EndToEndWorkflow):
	"""Company → fiscal year → block with productive dates → a classified purchase
	→ a normalization proposed and approved → Sustainable CF/Acre, itemized.

	v0.19.5, AND IT IS THE FIRST WALK IN THIS FILE THAT ENDS SOMEWHERE OTHER THAN
	A COMPLIANCE RECORD. Everything above it answers somebody with a citation. This
	answers the question an owner, a lender and a buyer ask about the same season:
	what did the ground actually earn, after the non-recurring items are taken back
	out and after replacing what wore out is paid for?

	ONE TEST, on purpose, for the same reason `TheWorkflowWalksEndToEnd` is one
	test: the claim is that the pieces connect, and a claim about connection cannot
	be made by six tests that each re-walk the flow.

	The assertion that matters most is the last one. The figure is checked, and so
	is every ingredient — because the whole argument of the release is that a
	normalized number nobody can inspect is indistinguishable from an arranged one,
	and a test that asserted only the figure would be asserting the half that is
	worth least.
	"""

	def test_the_walk_ends_in_a_defensible_figure(self):
		self.be_the_operator()

		# The year the books close in, which the site already has: a fiscal year
		# names itself, so there cannot be a second 2026 and the walk uses the
		# one that is there. A normalization is refused outside a fiscal year for
		# the same reason ERPNext refuses a posting outside one — it is defended
		# INSIDE a closed set of books — and the refusal is proved further down by
		# the period check rather than by making a duplicate year here.
		self.assertTrue(frappe.db.exists("Fiscal Year", "2026"))
		outside = self.tool_error(
			"create_normalization_adjustment",
			{
				"company": COMPANY,
				"fiscal_year": "2026",
				"period_start": "2025-12-01",
				"period_end": "2026-03-31",
				"amount": 1,
				"direction": "Add-back to OCF",
				"category": "Other",
				"justification": "x" * 60,
			},
		)
		self.assertIn("straddling two", outside)

		# The block, with the dates that put it in the denominator. Productive
		# from the first of the year, so a Q1 window weights it in full — and
		# `pre_yield_end_date` is on the record beside it, which is what makes the
		# transition auditable rather than inferred from a planting year.
		block = self.tool_data(
			"create_field",
			{
				"parcel": PARCEL,
				"field_name": "E2E Block 1",
				"acreage": 40.0,
				"variety": "Bing",
				"planting_year": 2019,
				"productive_from_date": "2026-01-01",
				"pre_yield_end_date": "2025-12-31",
			},
		)
		self.assertEqual(block["productive_from_date"], "2026-01-01")

		# The season's cash, by the direct method: a sale in and a cost out,
		# against Income and Expense, which is what makes both operating.
		abbr = ABBR
		STORE.seed(
			"Account",
			[
				{
					"name": f"1100 - Cash - {abbr}",
					"account_name": "Cash",
					"account_number": "1100",
					"root_type": "Asset",
					"account_type": "Cash",
					"is_group": 0,
					"company": COMPANY,
				},
				{
					"name": f"4100 - Sales - {abbr}",
					"account_name": "Sales",
					"account_number": "4100",
					"root_type": "Income",
					"account_type": "",
					"is_group": 0,
					"company": COMPANY,
				},
				{
					"name": f"5100 - Supplies - {abbr}",
					"account_name": "Supplies",
					"account_number": "5100",
					"root_type": "Expense",
					"account_type": "",
					"is_group": 0,
					"company": COMPANY,
				},
			],
		)
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "E2E-GL-1",
					"account": f"1100 - Cash - {abbr}",
					"posting_date": "2026-02-01",
					"debit": 140000,
					"credit": 0,
					"company": COMPANY,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "E2E-JV-1",
				},
				{
					"name": "E2E-GL-2",
					"account": f"4100 - Sales - {abbr}",
					"posting_date": "2026-02-01",
					"debit": 0,
					"credit": 140000,
					"company": COMPANY,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "E2E-JV-1",
				},
				{
					"name": "E2E-GL-3",
					"account": f"5100 - Supplies - {abbr}",
					"posting_date": "2026-02-15",
					"debit": 40000,
					"credit": 0,
					"company": COMPANY,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "E2E-JV-2",
				},
				{
					"name": "E2E-GL-4",
					"account": f"1100 - Cash - {abbr}",
					"posting_date": "2026-02-15",
					"debit": 0,
					"credit": 40000,
					"company": COMPANY,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "E2E-JV-2",
				},
			],
		)

		# The replacement pump, classified AT THE MOMENT OF PURCHASE by the person
		# who knows the old one failed. Seeded rather than raised through
		# `create_asset` because ERPNext's Asset needs an Asset Category carrying
		# three accounts, and that scaffolding is exercised in test_assets.py —
		# what this walk is about is the classification travelling into the figure.
		compliance_fields.install_compliance_fields(respect_switch=False)
		STORE.seed(
			"Asset",
			[
				{
					"name": "E2E-PUMP",
					"asset_name": "Irrigation pump — replacement",
					"company": COMPANY,
					"purchase_date": "2026-02-10",
					"gross_purchase_amount": 30000,
					"docstatus": 1,
					"capex_type": "Maintenance",
					"maintenance_portion": 30000,
					"growth_portion": 0,
				}
			],
		)

		# The hailstorm. Proposed as a DRAFT — and the walk proves it does not
		# count in that state, which is the whole compliance posture of the
		# release rather than a detail of it.
		why = (
			"Hail on 2026-04-11 destroyed the frost fans on blocks 3 and 4; the replacement "
			"was a single insured event and the last hail loss on this ground was 2011."
		)
		draft = self.tool_data(
			"create_normalization_adjustment",
			{
				"company": COMPANY,
				"fiscal_year": "2026",
				"period_start": "2026-01-01",
				"period_end": "2026-03-31",
				"amount": 20000,
				"direction": "Add-back to OCF",
				"category": "Weather-Event-Loss",
				"justification": why,
			},
		)
		self.assertEqual(draft["status"], "Draft")

		before = self.tool_data(
			"get_sustainable_cf_per_acre",
			{"company": COMPANY, "period_start": "2026-01-01", "period_end": "2026-03-31"},
		)
		self.assertEqual(before["normalization_adjustments"], [])
		self.assertEqual(before["normalized_ocf"], 100000.0)

		# The accountant signs. THIS is the act that moves the number, and it
		# cannot happen without a signature.
		unsigned = self.tool_error("approve_normalization_adjustment", {"name": draft["name"]})
		self.assertIn("approver_signature_file_token is required", unsigned)

		# A file URL rather than a staged upload, and that is the realistic path
		# here: the staging pipeline is the PHONE's, gated on an enrolled Farm Ops
		# credential, and the person signing off a normalization is an accountant
		# at a desk. `shifts.file_reference` accepts either spelling for exactly
		# this reason — a docname is checked and a URL is taken as given.
		approved = self.tool_data(
			"approve_normalization_adjustment",
			{
				"name": draft["name"],
				"approver_signature_file_token": "/files/e2e-approval-signature.png",
			},
		)
		self.assertEqual(approved["status"], "Approved")
		self.assertTrue(approved["approved_on"])

		# And the figure. (100k raw + 20k add-back - 30k maintenance capex) ÷ 40
		# acres = 2,250 per acre.
		data = self.tool_data(
			"get_sustainable_cf_per_acre",
			{"company": COMPANY, "period_start": "2026-01-01", "period_end": "2026-03-31"},
		)
		self.assertEqual(data["raw_ocf"]["value"], 100000.0)
		self.assertEqual(data["normalized_ocf"], 120000.0)
		self.assertEqual(data["maintenance_capex"]["total"], 30000.0)
		self.assertEqual(data["productive_acres"]["time_weighted"], 40.0)
		self.assertEqual(data["sustainable_cf_per_acre"], 2250.0)

		# EVERY INGREDIENT IS ON THE PLATE, which is the claim the release makes
		# and the half a test asserting only the figure would leave unasserted.
		adjustment = data["normalization_adjustments"][0]
		self.assertEqual(adjustment["name"], draft["name"])
		self.assertEqual(adjustment["justification"], why)
		self.assertEqual(adjustment["signed_effect_on_ocf"], 20000.0)
		self.assertTrue(adjustment["has_approver_signature"])

		capex = data["maintenance_capex"]["itemized"][0]
		self.assertEqual(capex["asset"], "E2E-PUMP")
		self.assertEqual(capex["capex_type"], "Maintenance")
		self.assertEqual(capex["maintenance_portion"], 30000.0)
		self.assertEqual(capex["purchase_date"], "2026-02-10")

		acres = data["productive_acres"]["itemized"][0]
		self.assertEqual(acres["field"], block["name"])
		self.assertEqual(acres["days_productive_in_period"], 90)
		self.assertEqual(acres["time_weighted_acres"], 40.0)

		# The register agrees with the figure about what counted.
		register = self.tool_data("list_normalization_adjustments", {"company": COMPANY})
		self.assertEqual(register["counted_in_the_kpi"], [draft["name"]])
		self.assertEqual(register["awaiting_a_decision"], [])


class TheYearReachesARollingFigure(EndToEndWorkflow):
	"""Company → block → a classified purchase → an approved adjustment → three
	months of ledger → the SAME figure over a trailing twelve months, with its
	own history under it.

	v0.19.6, AND IT IS THE SAME WALK AS `TheSeasonReachesAPerAcreFigure` ENDING
	ONE STEP FURTHER ON. That one proves a period produces a defensible number.
	This proves the number can be read without knowing what season it is —
	which is the difference between a figure an owner can act on and a figure
	that has to be explained every time it is quoted.

	ONE TEST, on purpose, for the reason every walk in this file is one test: the
	claim is that the pieces connect, and a claim about connection cannot be made
	by six tests that each re-walk the flow.

	THE ASSERTIONS THAT MATTER ARE THE LAST THREE. The TTM window's ingredients
	are checked, not only its value — the release inherits v0.19.5's obligation
	that a normalized figure nobody can inspect is indistinguishable from an
	arranged one, and a window makes that obligation harder rather than lighter.
	Then the partial-history warning, because a three-month ledger under a
	twelve-month window is exactly the case where a silently-smaller number would
	be believed. Then the cache, because a figure that is not reproducible from
	what was stored is a figure nobody can defend six months later.
	"""

	def test_the_year_ends_in_a_figure_that_can_be_read_without_a_calendar(self):
		self.be_the_operator()

		block = self.tool_data(
			"create_field",
			{
				"parcel": PARCEL,
				"field_name": "E2E TTM Block",
				"acreage": 40.0,
				"variety": "Bing",
				"planting_year": 2019,
				"productive_from_date": "2020-01-01",
				"pre_yield_end_date": "2019-12-31",
			},
		)

		abbr = ABBR
		STORE.seed(
			"Account",
			[
				{
					"name": f"1100 - Cash - {abbr}",
					"account_name": "Cash",
					"account_number": "1100",
					"root_type": "Asset",
					"account_type": "Cash",
					"is_group": 0,
					"company": COMPANY,
				},
				{
					"name": f"4100 - Sales - {abbr}",
					"account_name": "Sales",
					"account_number": "4100",
					"root_type": "Income",
					"account_type": "",
					"is_group": 0,
					"company": COMPANY,
				},
			],
		)

		# THREE MONTHS OF LEDGER INSIDE A TWELVE-MONTH WINDOW. Deliberately short:
		# the interesting case is not a full year, it is the site that has just
		# started keeping books, because that is where a figure quoted as "TTM"
		# without a caveat is a figure somebody acts on.
		months = {"2026-05-15": 90000, "2026-06-15": 120000, "2026-07-15": 150000}
		rows = []
		for index, (posting_date, amount) in enumerate(sorted(months.items()), start=1):
			voucher = f"E2E-TTM-JV-{index}"
			rows.extend(
				[
					{
						"name": f"E2E-TTM-GL-{index}-cash",
						"account": f"1100 - Cash - {abbr}",
						"posting_date": posting_date,
						"debit": amount,
						"credit": 0,
						"company": COMPANY,
						"is_cancelled": 0,
						"voucher_type": "Journal Entry",
						"voucher_no": voucher,
					},
					{
						"name": f"E2E-TTM-GL-{index}-sales",
						"account": f"4100 - Sales - {abbr}",
						"posting_date": posting_date,
						"debit": 0,
						"credit": amount,
						"company": COMPANY,
						"is_cancelled": 0,
						"voucher_type": "Journal Entry",
						"voucher_no": voucher,
					},
				]
			)
		STORE.seed("GL Entry", rows)

		# The replacement pump, classified at the moment of purchase. The capex
		# columns are grafted on the way `bench migrate` does — seeded rather than
		# raised through `create_asset` because ERPNext's Asset needs an Asset
		# Category carrying three accounts, and that scaffolding is exercised in
		# test_assets.py. What this walk is about is the classification travelling
		# into a TWELVE-MONTH figure rather than a quarterly one.
		compliance_fields.install_compliance_fields(respect_switch=False)
		STORE.seed(
			"Asset",
			[
				{
					"name": "E2E-TTM-PUMP",
					"asset_name": "Well 2 booster pump (replacement)",
					"company": COMPANY,
					"purchase_date": "2026-06-10",
					"gross_purchase_amount": 30000,
					"docstatus": 1,
					"capex_type": "Maintenance",
					"maintenance_portion": 30000,
					"growth_portion": 0,
				}
			],
		)

		# The judgement, proposed and then signed. It spans a QUARTER, which is
		# the case that decides how the window is computed: a quarter-long
		# adjustment falls inside no monthly bucket, so a year assembled from
		# twelve months would drop it and nothing would say so.
		why = (
			"Hail on 2026-04-11 destroyed the frost fans on the north block; the replacement was "
			"a single insured event and the last hail loss on this ground was 2011."
		)
		draft = self.tool_data(
			"create_normalization_adjustment",
			{
				"company": COMPANY,
				"fiscal_year": "2026",
				"period_start": "2026-04-01",
				"period_end": "2026-06-30",
				"amount": 24000,
				"direction": "Add-back to OCF",
				"category": "Weather-Event-Loss",
				"justification": why,
			},
		)
		self.tool_data(
			"approve_normalization_adjustment",
			{
				"name": draft["name"],
				"approver_signature_file_token": "/files/e2e-ttm-signature.png",
			},
		)

		# ── the window ────────────────────────────────────────────────────
		data = self.tool_data(
			"get_windowed_report",
			{
				"report_name": "sustainable_cf_per_acre",
				"company": COMPANY,
				"as_of": "2026-08-03",
			},
		)
		self.assertEqual(data["window_type"], "TTM")
		self.assertEqual(data["computation_step"], "Monthly")
		# Read on 3 August, the window ends at the last COMPLETED month.
		self.assertEqual(data["ttm"]["period_start"], "2025-08-01")
		self.assertEqual(data["ttm"]["period_end"], "2026-07-31")
		self.assertEqual(data["point_in_time"]["period_start"], "2026-07-01")
		self.assertEqual(data["point_in_time"]["period_end"], "2026-07-31")

		# (360,000 raw + 24,000 add-back − 30,000 maintenance capex) ÷ 40 acres
		# productive for the whole window = 8,850 per acre.
		components = data["ttm"]["components"]
		self.assertEqual(components["raw_ocf"]["value"], 360000.0)
		self.assertEqual(components["normalization_adjustments_total_addback"], 24000.0)
		self.assertEqual(components["normalized_ocf"], 384000.0)
		self.assertEqual(components["maintenance_capex"]["total"], 30000.0)
		self.assertEqual(components["productive_acres"]["time_weighted"], 40.0)
		self.assertEqual(data["ttm"]["value"], 8850.0)

		# EVERY INGREDIENT SURVIVES THE WINDOW, which is v0.19.5's obligation and
		# is harder rather than lighter across twelve months than across one
		# quarter — the adjustment that spans a quarter is in here exactly once.
		adjustment = components["normalization_adjustments"][0]
		self.assertEqual(adjustment["name"], draft["name"])
		self.assertEqual(adjustment["justification"], why)
		self.assertTrue(adjustment["has_approver_signature"])
		self.assertEqual(len(components["normalization_adjustments"]), 1)

		capex = components["maintenance_capex"]["itemized"][0]
		self.assertEqual(capex["asset"], "E2E-TTM-PUMP")
		self.assertEqual(capex["maintenance_portion"], 30000.0)

		acres = components["productive_acres"]["itemized"][0]
		self.assertEqual(acres["field"], block["name"])
		self.assertEqual(acres["time_weighted_acres"], 40.0)

		# ── the caveat, which is the point of a three-month ledger ────────
		partial = [line for line in data["computation_warnings"] if "PARTIAL" in line]
		self.assertTrue(partial, data["computation_warnings"])
		self.assertIn("not annualized", partial[0])
		self.assertEqual(data["ledger_starts"], "2026-05-15")
		self.assertEqual(data["ledger_months_available"], 3)

		# The history block exists and says how thin it is rather than averaging
		# three months into a norm.
		averages = data["historical_averages"]
		self.assertEqual(averages["requested_entries"], 60)
		self.assertLess(averages["prior_ttm_count"], 60)
		self.assertIsNone(averages["current_vs_prior_year_pct_delta"])

		# ── the cache, which is what makes the figure reproducible later ──
		cached = self.tool_data(
			"list_financial_kpi_history",
			{"company": COMPANY, "kpi_key": "sustainable_cf_per_acre"},
		)
		self.assertGreater(cached["count"], 0)
		snapshot = next(row for row in cached["records"] if row["as_of"] == "2026-07-31")
		self.assertEqual(snapshot["value"], 8850.0)
		self.assertEqual(snapshot["period_start"], "2025-08-01")
		self.assertEqual(snapshot["period_end"], "2026-07-31")

		# And the old call shape still answers, exactly as it did, because this
		# figure is quoted in packs that were sent before the window existed.
		legacy = self.tool_data(
			"get_sustainable_cf_per_acre",
			{"company": COMPANY, "period_start": "2025-08-01", "period_end": "2026-07-31"},
		)
		self.assertEqual(legacy["sustainable_cf_per_acre"], 8850.0)
		self.assertIn("DEPRECATED CALL SHAPE", legacy["computation_warnings"][0])


# ── 10. v0.21.0: one walk, three sections, the records still separate ───────
class OneVisitReachesTheOSHAPacketAsOneVisit(EndToEndWorkflow):
	"""The whole v0.21.0 claim, walked from an empty site to an audit packet.

	THE JOIN THIS EXISTS FOR. `test_inspection_templates` proves each part in
	isolation: the seeder writes four templates, the matcher bundles by set
	inclusion, the submitter writes the compliance records, the packet grows a
	section. What none of those proves is that the CHAIN holds — that a cabin
	which is genuinely overdue for two different things raises two genuinely
	different alerts, that the rule engine picks the template covering both
	rather than one of the four, that the records it writes actually move the
	registers those two alerts read, that the alerts then go away by themselves,
	and that an auditor asking "which visit produced this Housing Inspection"
	gets an answer from the packet rather than from somebody's memory.

	Every one of those is a seam between two subsystems, and every bug this
	project has shipped has lived in a seam.

	WHY IT ASSERTS **TWO** COMPLIANCE RECORDS FROM **THREE** SECTIONS, and why
	that is the correct number rather than a shortfall: a Detector Test carries
	a smoke result AND a CO result, both required fields. Testing them as two
	sections is right for the worker — they walk to one detector and then to the
	other — but filing two Detector Test records for one cabin on one day would
	mean each of them asserting something it was never told about the other
	detector. Two contradictory compliance records is precisely the failure this
	app exists to prevent, so the two sections produce ONE record between them
	and both submissions link it. The unit's smoke AND CO dates both move, which
	is what the alert was actually asking about.
	"""

	def test_one_afternoon_produces_the_separate_records_and_says_it_was_one_afternoon(self):
		from erpnext_mcp import sessions as session_records

		self.be_the_operator()

		# ── 1. The templates arrive on migrate, as data. Nobody shipped code. ──
		seeded = session_records.seed_inspection_templates()
		self.assertIn("Mid-season Habitability", seeded["created"])
		self.assertFalse(seeded["failed"])

		catalogue = self.tool_data("list_inspection_templates", {"applies_to_asset_type": "Housing Unit"})
		self.assertEqual(len(catalogue["live_templates"]), 3)

		# ── 2. A brand-new cabin is overdue for two DIFFERENT things. ─────────
		live = self.sweep(alert_type=None)
		self.assertEqual(
			sorted({row["alert_type"] for row in live}),
			["housing_detector_test_stale", "housing_inspection_overdue"],
		)

		# ── 3. The rule engine bundles them into ONE trip, deterministically. ─
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": COMPANY})
		self.assertEqual(report["session_count"], 1)
		self.assertEqual(report["alerts_bundled_into_sessions"], 2)
		self.assertEqual(report["created_count"], 0, "two alerts at one cabin should not be two tasks")

		bundle = report["sessions"][0]
		self.assertEqual(bundle["template_name"], "Mid-season Habitability")
		self.assertEqual(bundle["covers"], ["Detector Test", "Housing Inspection"])
		self.assertEqual(bundle["extra_sections"], [], "the tightest-fitting template should win")
		self.assertEqual(bundle["location"], self.unit)

		# One card on the board, not two — and the session is the form behind it.
		self.assertEqual(len(STORE.rows("Farm Task")), 1)
		self.assertEqual(
			frappe.db.get_value("Farm Task", bundle["task"], "inspection_session"), bundle["session"]
		)

		# ── 4. The evidence goes up in chunks, as the phone sends it. ─────────
		self.be_the_worker()
		photo = self.upload(one_pixel_png(), "north-wall.png", "e2e-session-photo")["file_token"]
		signature = self.upload(one_pixel_png(), "signature.png", "e2e-session-sig")["file_token"]
		self.be_the_operator()

		# ── 5. One submission. One signature. Three sections. ─────────────────
		# THE SESSION THE RULE ENGINE ALREADY RAISED is the one that gets filed —
		# not a second one started beside it. It was created before anybody had
		# claimed the task, so it names nobody; the handset filing it does.
		session = self.tool_data("get_inspection_session", {"name": bundle["session"]})
		self.assertEqual(session["template_version"], 1)
		self.assertEqual(session["state"], "Draft")
		self.assertIsNone(session["worker"])
		self.assertEqual(sorted(session["source_alerts"]), sorted(bundle["alerts"]))

		today = frappe.utils.today()
		submitted = self.tool_data(
			"submit_inspection_session",
			{
				"name": session["name"],
				"record_date": today,
				"worker": self.employee,
				"visit_id": "E2E-VISIT-01",
				"section_submissions": [
					{
						"section_name": "Habitability walk",
						"evidence_file_tokens": [photo],
						"signature_file": signature,
						"notes": "",
					},
					{
						"section_name": "Smoke Detector Test",
						"checklist_values": {"smoke_alarm_sounds": True},
						"record_data": {"smoke_detector_result": "Pass"},
						"signature_file": signature,
						"notes": "",
					},
					{
						"section_name": "CO Detector Test",
						"checklist_values": {"co_alarm_sounds": True},
						"record_data": {"co_detector_result": "Pass"},
						"signature_file": signature,
						"notes": "",
					},
				],
			},
		)
		self.assertEqual(submitted["state"], "Submitted")
		self.assertEqual(len(submitted["section_submissions"]), 3)

		# ── 6. TWO compliance records from three sections. See the docstring. ─
		produced = {entry["doctype"]: entry["record"] for entry in submitted["produced"]}
		self.assertEqual(sorted(produced), ["Detector Test", "Housing Inspection"])
		self.assertEqual(len(STORE.rows("Housing Inspection")), 1)
		self.assertEqual(len(STORE.rows("Detector Test")), 1)

		detector = dict(STORE.get_raw("Detector Test", produced["Detector Test"]) or {})
		self.assertEqual(detector["smoke_detector_result"], "Pass")
		self.assertEqual(detector["co_detector_result"], "Pass")

		# Both detector sections point at the one record: the trail from either
		# side of the walk is intact.
		links = {row["section_name"]: row["produced_record_link"] for row in submitted["section_submissions"]}
		self.assertEqual(links["Smoke Detector Test"], links["CO Detector Test"])
		self.assertNotEqual(links["Habitability walk"], links["Smoke Detector Test"])

		# ── 7. The SAME photograph is on both records. That is the tray. ──────
		walk_photos = {row.get("file") for row in self.photo_rows(produced["Housing Inspection"])}
		detector_photos = {row.get("file") for row in (detector.get("photos") or [])}
		self.assertIn(photo, walk_photos)
		self.assertIn(
			signature, walk_photos | {self.inspection(produced["Housing Inspection"]).get("signature")}
		)
		self.assertIn(signature, detector_photos)

		# ── 8. The registers moved — all three dates, from one visit. ─────────
		unit = frappe.db.get_value(
			"Housing Unit",
			self.unit,
			["last_habitability_inspection", "smoke_detector_last_test", "co_detector_last_test"],
			as_dict=True,
		)
		self.assertEqual(str(unit["last_habitability_inspection"]), today)
		self.assertEqual(str(unit["smoke_detector_last_test"]), today)
		self.assertEqual(str(unit["co_detector_last_test"]), today)

		# ── 9. So both alerts go away BY THEMSELVES on the next sweep. ────────
		self.assertEqual(self.sweep(alert_type=None), [])

		# ── 10. And the auditor's question is answerable from the packet. ─────
		built = self.tool_data(
			"generate_audit_packet",
			{
				"audit_type": "OSHA",
				"company": COMPANY,
				"period_start": str(frappe.utils.add_days(today, -30)),
				"period_end": today,
				"regime": "OR-OSHA",
				"allow_open_actions": True,
			},
		)
		packet = built["packet"]
		visits = next(section for section in packet["sections"] if section["key"] == "sessions")
		self.assertEqual(visits["row_count"], 1)

		row = visits["rows"][0]
		self.assertEqual(row["session"], session["name"])
		self.assertEqual(row["template"], "Mid-season Habitability")
		self.assertEqual(row["template_version"], 1)
		self.assertEqual(row["location"], self.unit)
		self.assertEqual(row["record_count"], 2)
		self.assertEqual(row["date"], today)
		# Distinct FILES, not evidence rows: one signature filed against three
		# sections is one photograph, and reporting three would overstate the
		# evidence in a document somebody signs.
		self.assertEqual(row["evidence_files"], 2)

		# The records themselves are STILL in the section that reads their own
		# register. This section adds no record to the packet; it adds the
		# sentence joining the ones already in it.
		housing = next(section for section in packet["sections"] if section["key"] == "housing")
		self.assertEqual(housing["rows"][0]["last_habitability_inspection"], today)

		# ── 11. And the read tools answer the same question the other way. ────
		listed = self.tool_data("list_inspection_sessions", {"company": COMPANY})
		self.assertEqual(listed["count"], 1)
		self.assertEqual(listed["compliance_records_produced"], 2)
		self.assertEqual(listed["sessions"][0]["visit_id"], "E2E-VISIT-01")
