# SPDX-License-Identifier: MIT
"""The curriculum, the afternoon, and the two ends of the training loop.

WHAT THIS FILE IS ABOUT. v0.19.0 built the training REGISTER and the compliance
matrix reads it, and between them they left both ends open. At one end a
`Training Type` was a name and a regime tag — nothing on it said what the course
IS, so a matrix could tell a picker their WPS card had lapsed and could not show
them the film. At the other end every record was filed one person at a time, so a
crew leader who trained twelve people in a shed had twelve forms to type, and
twelve forms typed one at a time disagree about the date, the topics and the
trainer by the third one.

EIGHT CLAIMS.

1. `TheCurriculumCarriesItsContent` — `update_training_type` writes the film, the
   materials, the minutes and the method; refuses a URL a phone cannot open and a
   delivery method this app does not know; and touches nothing already filed.

2. `TheCurriculumReadsBackForAScreen` — `get_training_curriculum` answers with
   what a handset renders, names what is missing rather than refusing, and omits
   attachments from a listing on purpose.

3. `OpeningASession` — the defaults come off the curriculum ONCE, the series is
   the year the session ran, and a session cannot be created already Completed.

4. `TheBadgeIsTheIdentification` — a scan resolves to a person through the same
   path the crew clock uses, a retired card is refused at the door, and a badge
   that disagrees with a typed name is refused rather than reconciled.

5. `TheSignatureIsASeparateAct` — a separate call, a separate timestamp, a
   `Signing Evidence` row with the session hashed BEFORE the signature was
   written, and a refusal to overwrite one already given.

6. `CompletionWritesOneRecordPerProvableAttendance` — the ready rows become
   Employee Training Records through `record_training`, the unprovable ones are
   named rather than filed, the absent ones are neither, and a second call
   duplicates nothing.

7. `TheSessionFeedsTheMatrix` — the records a session writes are ordinary
   training records, which is the whole architecture: `get_training_compliance_report`
   reads them without knowing a session existed.

8. `ReadingTheRegister` — the five filters, and the one that makes this more than
   a diary: `employee` finds the session somebody attended and did not sign, which
   produced no record and is invisible to `list_trainings`.
"""

import frappe

from erpnext_mcp import roles, training, training_sessions

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import ROLES, STORE

#: Every switch this suite needs. Listed rather than globbed so that turning one
#: off in a test is visibly a change from the on-by-default posture.
ON = {
	f"allow_{name}": 1
	for name in (
		"update_training_type",
		"get_training_curriculum",
		"create_training_session",
		"add_session_attendee",
		"sign_session_attendance",
		"complete_training_session",
		"get_training_session",
		"list_training_sessions",
		"record_training",
		"list_trainings",
		"get_training",
		"get_training_compliance_report",
		"resolve_badge",
	)
}

TRAINEE = "HR-EMP-00002"  # Ben Packhouse, Active, at MAIN
SUPERVISOR = "HR-EMP-00001"  # Ada Orchard, Active, at MAIN
#: A second trainee, seeded here rather than taken from the fixture: HR-EMP-00003
#: has status Left, and `resolve_badge` refuses a card belonging to somebody who
#: has gone — which is a refusal worth having and not the one this file is about.
SECOND = "HR-EMP-00020"
SECOND_NAME = "Marco Vega"

CURRICULUM = "Heat Illness Prevention"
TOPICS = "Heat index, water, shade, symptoms, reporting, emergency response"

BADGE_DOCTYPE = "Bucket Log Badge Map"
BEN_BADGE = "ETC-0002"
THIRD_BADGE = "ETC-0003"
RETIRED_BADGE = "ETC-0009"

SIGNATURE = "/files/ben-signature.png"


def days_out(count: int) -> str:
	return str(frappe.utils.add_days(frappe.utils.today(), count))


class TrainingSessionTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		# The double ships without an Employee register — Frappe HR is a separate
		# app, and every tool here refuses on a site that has none.
		install_hrms()
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		roles.install_roles()
		# The ten common curricula, as `install.after_migrate` seeds them. The
		# harness deliberately does not — `ensure_type` creates one from free
		# text on demand, which is the path the register's own suite exercises —
		# but every tool in THIS file resolves an existing curriculum rather than
		# creating one, so a fixture without them would be testing the refusal.
		training.seed_training_types()
		self.an_employee_at(MAIN, SECOND_NAME, SECOND)
		self._badges()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	def _badges(self):
		"""Two live cards, and one retired — which is a refusal with its own sentence."""
		STORE.seed(
			BADGE_DOCTYPE,
			[
				{"name": BEN_BADGE, "badge_id": BEN_BADGE, "company": MAIN, "employee": TRAINEE, "active": 1},
				{"name": THIRD_BADGE, "badge_id": THIRD_BADGE, "company": MAIN, "employee": SECOND, "active": 1},
				{
					"name": RETIRED_BADGE,
					"badge_id": RETIRED_BADGE,
					"company": MAIN,
					"employee": SUPERVISOR,
					"active": 0,
				},
			],
		)

	# -- helpers -------------------------------------------------------------
	def open_session(self, **overrides) -> dict:
		payload = {"training_type": CURRICULUM, "company": MAIN, "session_date": frappe.utils.today()}
		payload.update(overrides)
		return self.tool_data("create_training_session", payload)

	def a_full_session(self, **overrides) -> str:
		"""One session, two attendees scanned in and both signed. The happy path."""
		session = self.open_session(
			content_topics_covered=TOPICS, expires_date=days_out(365), **overrides
		)["name"]
		for badge in (BEN_BADGE, THIRD_BADGE):
			self.tool_data(
				"add_session_attendee",
				{"session": session, "badge_scan": badge, "scan_location": "45.5152,-122.6784"},
			)
		for person in (TRAINEE, SECOND):
			self.tool_data(
				"sign_session_attendance",
				{"session": session, "employee": person, "signature": SIGNATURE},
			)
		return session

	def raw(self, name: str) -> dict:
		return dict(STORE.get_raw(training_sessions.DOCTYPE, name) or {})

	def an_employee_at(self, company: str, name: str, docname: str) -> str:
		STORE.seed(
			"Employee",
			[
				{
					"name": docname,
					"employee_name": name,
					"status": "Active",
					"date_of_joining": "2025-01-01",
					"company": company,
				}
			],
		)
		return docname


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheCurriculumCarriesItsContent(TrainingSessionTestCase):
	def test_every_content_field_it_takes_is_written(self):
		data = self.tool_data(
			"update_training_type",
			{
				"training_type": CURRICULUM,
				"video_url": "https://example.test/heat-illness.mp4",
				"materials_description": "Handouts (EN/ES), thermometer, shade canopy.",
				"duration_minutes": 45,
				"description": "OAR 437-004-1131, annually and before the first 80 degree shift.",
				"delivery_method": "Classroom",
			},
		)
		row = dict(STORE.get_raw("Training Type", CURRICULUM) or {})

		self.assertEqual(row["video_url"], "https://example.test/heat-illness.mp4")
		self.assertEqual(row["duration_minutes"], 45)
		self.assertEqual(row["delivery_method"], "Classroom")
		self.assertIn("thermometer", row["materials_description"])
		self.assertEqual(data["delivery_method"], "Classroom")
		self.assertEqual(sorted(data["changed"]), sorted(row and data["changed"]))
		self.assertIn("video_url", data["changed"])

	def test_the_api_spelling_of_a_delivery_method_is_stored_in_the_desks(self):
		"""`field_demo` from a client, `Field Demo` in the column. One column, one spelling."""
		data = self.tool_data(
			"update_training_type", {"training_type": CURRICULUM, "delivery_method": "field_demo"}
		)
		self.assertEqual(data["delivery_method"], "Field Demo")
		self.assertEqual(dict(STORE.get_raw("Training Type", CURRICULUM))["delivery_method"], "Field Demo")

	def test_a_delivery_method_this_app_does_not_know_is_refused_by_name(self):
		error = self.tool_error(
			"update_training_type", {"training_type": CURRICULUM, "delivery_method": "webinar"}
		)
		self.assertIn("webinar", error)
		self.assertIn("Field Demo", error)
		self.assertIn("Nothing was changed", error)

	def test_a_video_url_a_phone_cannot_open_is_refused(self):
		"""A path renders on a phone as a link that goes nowhere, which looks answered."""
		error = self.tool_error(
			"update_training_type", {"training_type": CURRICULUM, "video_url": "heat-illness.mp4"}
		)
		self.assertIn("http", error)
		self.assertIn("attach_file_to_document", error)
		self.assertIsNone(dict(STORE.get_raw("Training Type", CURRICULUM)).get("video_url"))

	def test_a_curriculum_nobody_has_filed_against_is_refused_rather_than_created(self):
		"""Unlike record_training, which takes free text. See `_resolve_type`."""
		error = self.tool_error(
			"update_training_type", {"training_type": "Forklift Refresher", "duration_minutes": 30}
		)
		self.assertIn("Forklift Refresher", error)
		self.assertIn("record_training", error)

	def test_changing_a_curriculum_does_not_touch_what_is_already_filed(self):
		session = self.a_full_session()
		self.tool_data("complete_training_session", {"session": session})
		before = dict(STORE.get_raw(training_sessions.DOCTYPE, session))

		self.tool_data("update_training_type", {"training_type": CURRICULUM, "duration_minutes": 999})

		after = dict(STORE.get_raw(training_sessions.DOCTYPE, session))
		self.assertEqual(before.get("duration_minutes"), after.get("duration_minutes"))
		self.assertNotEqual(after.get("duration_minutes"), 999)

	def test_a_call_that_changes_nothing_writes_nothing_and_says_so(self):
		self.tool_data("update_training_type", {"training_type": CURRICULUM, "duration_minutes": 60})
		data = self.tool_data(
			"update_training_type", {"training_type": CURRICULUM, "duration_minutes": 60}
		)
		self.assertEqual(data["changed"], {})
		self.assertIn("nothing was written", data["note"])

	def test_regimes_are_refused_by_name_rather_than_dropped(self):
		error = self.tool_error(
			"update_training_type", {"training_type": CURRICULUM, "regimes": ["OSHA-ish"]}
		)
		self.assertIn("OSHA-ish", error)
		self.assertIn("Nothing was changed", error)


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheCurriculumReadsBackForAScreen(TrainingSessionTestCase):
	def test_one_curriculum_comes_back_in_the_shape_a_handset_renders(self):
		self.tool_data(
			"update_training_type",
			{
				"training_type": CURRICULUM,
				"video_url": "https://example.test/heat.mp4",
				"delivery_method": "video",
				"duration_minutes": 45,
				"materials_description": "Handouts.",
				"description": "OAR 437-004-1131.",
			},
		)
		data = self.tool_data("get_training_curriculum", {"training_type": CURRICULUM})

		self.assertEqual(data["training_type"], CURRICULUM)
		self.assertEqual(data["video_url"], "https://example.test/heat.mp4")
		self.assertEqual(data["duration_minutes"], 45)
		self.assertEqual(data["regimes"], ["OR-OSHA"])
		self.assertIn("OR-OSHA", data["regime_notes"])
		self.assertEqual(data["content_gaps"], [])
		self.assertIn("attachments", data)

	def test_what_a_screen_would_want_and_the_curriculum_lacks_is_named_not_refused(self):
		data = self.tool_data("get_training_curriculum", {"training_type": "OSHA 10"})
		self.assertTrue(data["content_gaps"])
		self.assertTrue(any("delivery method" in gap for gap in data["content_gaps"]))
		self.assertIn("update_training_type", data["content_note"])

	def test_a_curriculum_marked_as_video_with_nothing_to_play_is_a_named_gap(self):
		self.tool_data(
			"update_training_type", {"training_type": CURRICULUM, "delivery_method": "video"}
		)
		data = self.tool_data("get_training_curriculum", {"training_type": CURRICULUM})
		self.assertTrue(any("nothing to play" in gap for gap in data["content_gaps"]))

	def test_the_attached_pdf_is_listed_with_the_curriculum(self):
		STORE.seed(
			"File",
			[
				{
					"name": "FILE-HEAT-01",
					"file_name": "heat-illness-handout.pdf",
					"file_url": "/private/files/heat-illness-handout.pdf",
					"attached_to_doctype": "Training Type",
					"attached_to_name": CURRICULUM,
					"is_private": 1,
				}
			],
		)
		data = self.tool_data("get_training_curriculum", {"training_type": CURRICULUM})
		self.assertEqual([row["file_name"] for row in data["attachments"]], ["heat-illness-handout.pdf"])

	def test_no_name_lists_the_whole_curriculum_and_omits_attachments(self):
		data = self.tool_data("get_training_curriculum", {})
		names = [row["training_type"] for row in data["curriculum"]]
		self.assertIn(CURRICULUM, names)
		self.assertIn("WPS Handler Training", names)
		self.assertNotIn("attachments", data["curriculum"][0])
		self.assertIn("one query per curriculum", data["attachment_note"])

	def test_an_inactive_curriculum_is_out_of_the_listing_unless_asked_for(self):
		self.tool_data("update_training_type", {"training_type": "OSHA 10", "active": False})
		listed = self.tool_data("get_training_curriculum", {})
		self.assertNotIn("OSHA 10", [row["training_type"] for row in listed["curriculum"]])

		everything = self.tool_data("get_training_curriculum", {"include_inactive": True})
		self.assertIn("OSHA 10", [row["training_type"] for row in everything["curriculum"]])


# ── 3 ───────────────────────────────────────────────────────────────────────
class OpeningASession(TrainingSessionTestCase):
	def test_the_series_is_the_year_the_session_ran(self):
		data = self.open_session(session_date="2026-03-04")
		self.assertEqual(data["name"], "TRNS-2026-0001")
		self.assertEqual(self.open_session(session_date="2026-03-05")["name"], "TRNS-2026-0002")

	def test_the_defaults_come_off_the_curriculum(self):
		self.tool_data(
			"update_training_type",
			{"training_type": CURRICULUM, "duration_minutes": 45, "delivery_method": "classroom"},
		)
		data = self.open_session()
		self.assertEqual(data["duration_minutes"], 45)
		self.assertEqual(data["delivery_method"], "Classroom")
		self.assertEqual(data["regimes"], ["OR-OSHA"])
		self.assertIn("duration_minutes", data["inherited_from_curriculum"])

	def test_a_session_that_ran_short_is_entitled_to_say_so(self):
		"""The curriculum says what was planned; the session says what happened."""
		self.tool_data("update_training_type", {"training_type": CURRICULUM, "duration_minutes": 90})
		data = self.open_session(duration_minutes=40)
		self.assertEqual(data["duration_minutes"], 40)
		self.assertNotIn("duration_minutes", data["inherited_from_curriculum"])

	def test_it_writes_nothing_to_anybodys_file(self):
		self.open_session()
		self.assertEqual(STORE.rows(training.DOCTYPE), [])

	def test_a_session_cannot_be_created_already_completed(self):
		error = self.tool_error(
			"create_training_session",
			{"training_type": CURRICULUM, "company": MAIN, "status": "Completed"},
		)
		self.assertIn("complete_training_session", error)
		self.assertIn("Nothing was created", error)

	def test_an_end_time_before_the_start_is_a_typo_and_is_refused(self):
		error = self.tool_error(
			"create_training_session",
			{"training_type": CURRICULUM, "company": MAIN, "start_time": "14:00", "end_time": "09:30"},
		)
		self.assertIn("before it began", error)

	def test_the_clock_is_normalised_so_two_spellings_of_nine_are_one(self):
		data = self.open_session(start_time="9:00", end_time="10:30")
		self.assertEqual(data["start_time"], "09:00:00")
		self.assertEqual(data["duration_from_clock"], 90)

	def test_a_trainer_from_another_entity_is_recorded_as_an_instructor_not_a_link(self):
		outsider = self.an_employee_at(OTHER, "Rosa Field", "HR-EMP-00050")
		error = self.tool_error(
			"create_training_session",
			{"training_type": CURRICULUM, "company": MAIN, "conducted_by": outsider},
		)
		self.assertIn("instructor_name", error)
		self.assertIn("Nothing was created", error)

		data = self.open_session(instructor_name="Rosa Field", provider="OSU Extension")
		self.assertEqual(data["instructor"], "Rosa Field")
		self.assertEqual(data["provider"], "OSU Extension")

	def test_an_untagged_curriculum_produces_a_session_that_says_it_is_untagged(self):
		self.tool_data("update_training_type", {"training_type": CURRICULUM, "regimes": ["Other"]})
		data = self.open_session(regimes=["WPS"])
		self.assertEqual(data["regimes"], ["WPS"])


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheBadgeIsTheIdentification(TrainingSessionTestCase):
	def test_a_scan_resolves_to_a_person_and_the_scan_itself_is_kept(self):
		session = self.open_session()["name"]
		data = self.tool_data(
			"add_session_attendee",
			{"session": session, "badge_scan": BEN_BADGE, "scan_location": "45.5152,-122.6784"},
		)
		row = data["attendee"]
		self.assertEqual(row["employee"], TRAINEE)
		self.assertEqual(row["employee_name"], "Ben Packhouse")
		self.assertEqual(row["badge_scan"], BEN_BADGE)
		self.assertEqual(row["scan_location"], "45.5152,-122.6784")
		self.assertTrue(row["scanned_at"])
		self.assertEqual(row["state"], training_sessions.ATTENDEE_INCOMPLETE)
		self.assertEqual(row["missing"], ["signature"])

	def test_a_retired_card_is_refused_at_the_door(self):
		session = self.open_session()["name"]
		error = self.tool_error(
			"add_session_attendee", {"session": session, "badge_scan": RETIRED_BADGE}
		)
		self.assertIn("retired", error)

	def test_a_badge_that_disagrees_with_the_name_beside_it_is_refused(self):
		session = self.open_session()["name"]
		error = self.tool_error(
			"add_session_attendee",
			{"session": session, "badge_scan": BEN_BADGE, "employee": SECOND},
		)
		self.assertIn("states something nobody believes", error)
		self.assertIn("Nothing was changed", error)

	def test_one_person_cannot_be_on_the_sheet_twice(self):
		session = self.open_session()["name"]
		self.tool_data("add_session_attendee", {"session": session, "badge_scan": BEN_BADGE})
		error = self.tool_error(
			"add_session_attendee", {"session": session, "badge_scan": BEN_BADGE}
		)
		self.assertIn("already on", error)
		self.assertIn("sign_session_attendance", error)

	def test_a_row_with_no_badge_is_allowed_and_says_it_produces_no_record(self):
		session = self.open_session()["name"]
		data = self.tool_data("add_session_attendee", {"session": session, "employee": TRAINEE})
		self.assertEqual(data["attendee"]["missing"], ["badge_scan", "signature"])
		self.assertIn("typed a name", data["identity_note"])

	def test_somebody_rostered_who_did_not_come_keeps_their_row(self):
		session = self.open_session()["name"]
		data = self.tool_data(
			"add_session_attendee", {"session": session, "employee": TRAINEE, "attended": False}
		)
		self.assertEqual(data["attendee"]["state"], training_sessions.ATTENDEE_ABSENT)
		self.assertEqual(data["attendance"]["absent"], 1)

	def test_an_attendee_from_another_entity_is_refused(self):
		outsider = self.an_employee_at(OTHER, "Rosa Field", "HR-EMP-00051")
		session = self.open_session()["name"]
		error = self.tool_error(
			"add_session_attendee", {"session": session, "employee": outsider}
		)
		self.assertIn("another company", error.replace("a different company", "another company"))

	def test_a_completed_session_does_not_take_a_thirteenth_person(self):
		session = self.a_full_session()
		self.tool_data("complete_training_session", {"session": session})
		error = self.tool_error(
			"add_session_attendee", {"session": session, "employee": SUPERVISOR}
		)
		self.assertIn("Completed", error)
		self.assertIn("Nothing was changed", error)


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheSignatureIsASeparateAct(TrainingSessionTestCase):
	def test_it_writes_the_signature_the_moment_and_the_evidence_row(self):
		session = self.open_session()["name"]
		self.tool_data(
			"add_session_attendee",
			{"session": session, "badge_scan": BEN_BADGE, "scan_location": "45.5152,-122.6784"},
		)
		data = self.tool_data(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)

		self.assertEqual(data["attendee"]["signature"], SIGNATURE)
		self.assertTrue(data["attendee"]["signed_at"])
		self.assertEqual(data["attendee"]["state"], training_sessions.ATTENDEE_READY)
		self.assertTrue(data["signing_evidence"]["recorded"])

		evidence = dict(STORE.get_raw("Signing Evidence", data["signing_evidence"]["evidence"]))
		self.assertEqual(evidence["signer"], TRAINEE)
		self.assertEqual(evidence["verification_method"], "Badge QR")
		self.assertEqual(evidence["signer_badge"], BEN_BADGE)
		self.assertEqual(evidence["gps_latitude"], 45.5152)
		self.assertTrue(evidence["document_hash"].startswith("sha256:"))

	def test_the_hash_covers_the_session_as_it_stood_before_the_signature(self):
		"""Editing the topics after the crew signed is visible, which is the point."""
		session = self.open_session(content_topics_covered=TOPICS)["name"]
		self.tool_data("add_session_attendee", {"session": session, "badge_scan": BEN_BADGE})
		data = self.tool_data(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)
		evidence = dict(STORE.get_raw("Signing Evidence", data["signing_evidence"]["evidence"]))
		self.assertIn("content_topics_covered", evidence["hashed_fields"])
		# The signature column itself is excluded — the hash is of the page they
		# were shown, and a hash including the signature would prove only that the
		# app had just written one.
		self.assertNotIn("signature", evidence["hashed_fields"].split(","))

	def test_signing_for_somebody_who_is_not_on_the_sheet_is_refused(self):
		session = self.open_session()["name"]
		error = self.tool_error(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)
		self.assertIn("not on", error)
		self.assertIn("add_session_attendee", error)

	def test_replacing_a_signature_is_a_decision_rather_than_a_retry(self):
		session = self.open_session()["name"]
		self.tool_data("add_session_attendee", {"session": session, "badge_scan": BEN_BADGE})
		self.tool_data(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)
		error = self.tool_error(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": "/files/second.png"},
		)
		self.assertIn("replace_signature=true", error)

		data = self.tool_data(
			"sign_session_attendance",
			{
				"session": session,
				"employee": TRAINEE,
				"signature": "/files/second.png",
				"replace_signature": True,
			},
		)
		self.assertTrue(data["replaced_signature"])
		self.assertEqual(data["attendee"]["signature"], "/files/second.png")

	def test_a_signature_without_a_badge_records_the_evidence_as_unverified(self):
		session = self.open_session()["name"]
		self.tool_data("add_session_attendee", {"session": session, "employee": TRAINEE})
		data = self.tool_data(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)
		self.assertEqual(data["signing_evidence"]["status"], "Unverified")
		self.assertEqual(data["attendee"]["state"], training_sessions.ATTENDEE_INCOMPLETE)
		self.assertEqual(data["attendee"]["missing"], ["badge_scan"])

	def test_a_scan_and_a_signature_sharing_a_minute_are_recorded_and_named(self):
		session = self.open_session()["name"]
		self.tool_data(
			"add_session_attendee",
			{"session": session, "badge_scan": BEN_BADGE, "scanned_at": "2026-07-01 09:00:00"},
		)
		data = self.tool_data(
			"sign_session_attendance",
			{
				"session": session,
				"employee": TRAINEE,
				"signature": SIGNATURE,
				"signed_at": "2026-07-01 09:00:30",
			},
		)
		self.assertIn("filled in at the end", data["timing_note"])


# ── 6 ───────────────────────────────────────────────────────────────────────
class CompletionWritesOneRecordPerProvableAttendance(TrainingSessionTestCase):
	def test_every_ready_row_becomes_its_own_training_record(self):
		session = self.a_full_session()
		data = self.tool_data("complete_training_session", {"session": session})

		self.assertEqual(data["filed_count"], 2)
		self.assertEqual(data["status"], training_sessions.STATUS_COMPLETED)
		self.assertEqual(data["records_created"], 2)
		self.assertTrue(data["completed_at"])

		filed = {row["employee"]: row["training_record"] for row in data["records_filed"]}
		self.assertEqual(sorted(filed), sorted([TRAINEE, SECOND]))
		for person, record in filed.items():
			row = dict(STORE.get_raw(training.DOCTYPE, record))
			self.assertEqual(row["employee"], person)
			self.assertEqual(row["training_type"], CURRICULUM)
			self.assertEqual(row["company"], MAIN)
			self.assertEqual(row["regimes"], "OR-OSHA")
			self.assertEqual(str(row["completed_date"]), frappe.utils.today())
			self.assertEqual(row["person_performed_signature"], SIGNATURE)
			self.assertTrue(row["expires_date"])

	def test_the_attendee_row_names_the_record_it_produced(self):
		"""The trail an auditor walks: from the register back to the afternoon."""
		session = self.a_full_session()
		self.tool_data("complete_training_session", {"session": session})
		sheet = self.tool_data("get_training_session", {"session": session})
		for row in sheet["attendee_rows"]:
			self.assertTrue(row["training_record"])
			self.assertEqual(row["state"], training_sessions.ATTENDEE_RECORDED)

	def test_somebody_marked_present_who_cannot_be_proved_there_refuses_the_whole_call(self):
		session = self.a_full_session()
		self.tool_data("add_session_attendee", {"session": session, "employee": SUPERVISOR})
		error = self.tool_error("complete_training_session", {"session": session})

		self.assertIn("Ada Orchard", error)
		self.assertIn("skip_incomplete=true", error)
		self.assertIn("Nothing was changed", error)
		self.assertEqual(STORE.rows(training.DOCTYPE), [])

	def test_skip_incomplete_files_the_ready_rows_and_names_the_rest(self):
		session = self.a_full_session()
		self.tool_data("add_session_attendee", {"session": session, "employee": SUPERVISOR})
		data = self.tool_data(
			"complete_training_session", {"session": session, "skip_incomplete": True}
		)
		self.assertEqual(data["filed_count"], 2)
		self.assertEqual(
			[row["employee"] for row in data["skipped_incomplete"]], [SUPERVISOR]
		)
		self.assertIn("were expected", data["skip_note"])

	def test_somebody_who_did_not_come_neither_blocks_nor_is_filed(self):
		session = self.a_full_session()
		self.tool_data(
			"add_session_attendee", {"session": session, "employee": SUPERVISOR, "attended": False}
		)
		data = self.tool_data("complete_training_session", {"session": session})
		self.assertEqual(data["filed_count"], 2)
		self.assertEqual(data["absent"], [SUPERVISOR])

	def test_a_second_call_files_nothing_twice(self):
		session = self.a_full_session()
		self.tool_data("complete_training_session", {"session": session})
		error = self.tool_error("complete_training_session", {"session": session})
		self.assertIn("no attendee is ready", error)
		self.assertEqual(len(STORE.rows(training.DOCTYPE)), 2)

	def test_a_session_with_no_topics_cannot_be_completed(self):
		session = self.open_session()["name"]
		self.tool_data("add_session_attendee", {"session": session, "badge_scan": BEN_BADGE})
		self.tool_data(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)
		error = self.tool_error("complete_training_session", {"session": session})
		self.assertIn("content_topics_covered is empty", error)

		data = self.tool_data(
			"complete_training_session", {"session": session, "content_topics_covered": TOPICS}
		)
		self.assertEqual(data["filed_count"], 1)

	def test_a_session_with_no_regimes_cannot_be_completed(self):
		"""An untagged record appears in no packet, which is a silent way to lose evidence.

		The untagged curriculum is seeded rather than made through a tool, because
		nothing this app offers will produce one: `ensure_type` falls back to
		`Internal` and `update_training_type` refuses an empty list. It is the
		shape a site reaches by somebody clearing the tags in the Desk, which is
		exactly the case worth refusing at the one moment it matters.
		"""
		STORE.seed(
			"Training Type",
			[{"name": "Ladder Safety", "training_type_name": "Ladder Safety", "active": 1, "regimes": []}],
		)
		session = self.open_session(training_type="Ladder Safety", content_topics_covered=TOPICS)["name"]
		self.tool_data("add_session_attendee", {"session": session, "badge_scan": BEN_BADGE})
		self.tool_data(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)
		error = self.tool_error("complete_training_session", {"session": session})
		self.assertIn("no audit packet", error)

	def test_a_session_with_nobody_ready_cannot_be_completed(self):
		session = self.open_session(content_topics_covered=TOPICS)["name"]
		self.tool_data("add_session_attendee", {"session": session, "employee": TRAINEE})
		error = self.tool_error("complete_training_session", {"session": session})
		self.assertIn("no attendee is ready", error)

	def test_a_cancelled_session_cannot_be_completed(self):
		session = self.a_full_session()
		frappe.db.set_value(training_sessions.DOCTYPE, session, "status", "Cancelled")
		error = self.tool_error("complete_training_session", {"session": session})
		self.assertIn("did not happen", error)
		self.assertEqual(STORE.rows(training.DOCTYPE), [])

	def test_a_session_with_no_expiry_says_the_records_will_never_be_renewed(self):
		session = self.open_session(content_topics_covered=TOPICS)["name"]
		self.tool_data("add_session_attendee", {"session": session, "badge_scan": BEN_BADGE})
		self.tool_data(
			"sign_session_attendance",
			{"session": session, "employee": TRAINEE, "signature": SIGNATURE},
		)
		data = self.tool_data("complete_training_session", {"session": session})
		self.assertIn("WPS", data["expiry_note"])


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheSessionFeedsTheMatrix(TrainingSessionTestCase):
	def test_the_records_a_session_writes_are_ordinary_training_records(self):
		"""The whole architecture: the matrix reads them without knowing a session existed."""
		session = self.a_full_session()
		self.tool_data("complete_training_session", {"session": session})

		matrix = self.tool_data("get_training_compliance_report", {"company": MAIN})
		standing = {row["employee"]: row["requirements"][CURRICULUM]["status"] for row in matrix["matrix"]}
		self.assertEqual(standing[TRAINEE], "current")
		self.assertEqual(standing[SECOND], "current")
		self.assertEqual(standing[SUPERVISOR], "missing")

	def test_the_register_reads_them_back_with_the_session_named_in_the_notes(self):
		session = self.a_full_session()
		self.tool_data("complete_training_session", {"session": session})
		register = self.tool_data("list_trainings", {"company": MAIN})
		self.assertEqual(register["count"], 2)
		for row in register["records"]:
			self.assertTrue(row["trainee_signed"])
			self.assertFalse(row["supervisor_reviewed"])


# ── 8 ───────────────────────────────────────────────────────────────────────
class ReadingTheRegister(TrainingSessionTestCase):
	def test_the_sheet_reports_what_stands_between_it_and_a_completion(self):
		session = self.open_session()["name"]
		self.tool_data("add_session_attendee", {"session": session, "employee": TRAINEE})
		data = self.tool_data("get_training_session", {"session": session})

		self.assertEqual(data["attendance"]["attendees"], 1)
		self.assertEqual(data["attendance"]["incomplete"], 1)
		self.assertEqual(data["attendance"]["without_badge_scan"], [TRAINEE])
		self.assertTrue(data["completion_blockers"])
		self.assertEqual(data["curriculum"]["training_type"], CURRICULUM)

	def test_a_listing_carries_the_counts_and_not_the_rows(self):
		self.a_full_session()
		data = self.tool_data("list_training_sessions", {"company": MAIN})
		self.assertEqual(data["count"], 1)
		self.assertNotIn("attendee_rows", data["sessions"][0])
		self.assertEqual(data["sessions"][0]["attendance"]["ready"], 2)

	def test_the_employee_filter_finds_the_session_that_produced_no_record(self):
		"""The gap somebody asking the question is actually looking for."""
		session = self.open_session()["name"]
		self.tool_data("add_session_attendee", {"session": session, "badge_scan": BEN_BADGE})

		found = self.tool_data("list_training_sessions", {"company": MAIN, "employee": TRAINEE})
		self.assertEqual([row["name"] for row in found["sessions"]], [session])
		self.assertEqual(found["with_unproved_attendance"], [session])
		self.assertIn("found by an inspector", found["note"])

		# And the training register knows nothing about it, which is the point.
		self.assertEqual(self.tool_data("list_trainings", {"company": MAIN})["count"], 0)

	def test_the_status_filter_takes_the_clients_spelling(self):
		session = self.open_session()["name"]
		frappe.db.set_value(training_sessions.DOCTYPE, session, "status", "In Progress")
		data = self.tool_data("list_training_sessions", {"company": MAIN, "status": "in_progress"})
		self.assertEqual([row["name"] for row in data["sessions"]], [session])

	def test_the_regime_filter_matches_by_tag_and_never_by_substring(self):
		self.open_session(regimes=["GlobalGAP"])
		gap = self.tool_data("list_training_sessions", {"company": MAIN, "regime": "GAP"})
		self.assertEqual(gap["count"], 0)
		globalgap = self.tool_data("list_training_sessions", {"company": MAIN, "regime": "GlobalGAP"})
		self.assertEqual(globalgap["count"], 1)

	def test_the_period_filter_is_on_the_day_it_ran(self):
		self.open_session(session_date=days_out(-30))
		recent = self.open_session(session_date=days_out(-2))["name"]
		data = self.tool_data(
			"list_training_sessions", {"company": MAIN, "from_date": days_out(-7)}
		)
		self.assertEqual([row["name"] for row in data["sessions"]], [recent])
