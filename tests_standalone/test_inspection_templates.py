# SPDX-License-Identifier: MIT
"""Templated inspection sessions — v0.21.0's whole claim, one class per part.

THE CLAIM IS THAT THE SHAPE OF A VISIT IS DATA. Everything below is an attempt to
break one of the six things that has to be true for that to be worth having:

    TEMPLATES ARE RECORDS        `AuthoringATemplate`. A template is written by a
                                 tool call and is live. What it refuses — no
                                 sections, two sections with one name, a second
                                 live template with a taken name, a contract key
                                 outside the vocabulary — it refuses at authoring
                                 time, while the person who can fix it is present.

    EDITS SUPERSEDE              `Versioning`. An edit writes a NEW row and never
                                 touches the old one, which is what makes a
                                 session from April readable in November and why
                                 a session started against v1 while v2 is being
                                 authored is unaffected.

    A SESSION PINS ITS VERSION   `Versioning.test_a_session_pins_...`. The number
                                 is copied at creation and never updated.

    SUBMISSION WRITES THE        `Submitting`. Separately, at their own cadences,
    COMPLIANCE RECORDS           with the visit's shared evidence on each — and
                                 NOTHING is written if a required section is
                                 missing, because half a visit is a set of
                                 records that look complete and are not.

    THE RULE ENGINE BUNDLES      `TheRuleEngine`. Two overdue things at one cabin
    DETERMINISTICALLY            become ONE visit; one overdue thing stays one
                                 plain task. Set inclusion and two integers, no
                                 model anywhere near it.

    THE SEEDS DO NOT OVERWRITE   `TheSeededTemplates`. A second migrate creates
    AN OPERATOR'S EDIT           nothing, and a template somebody edited or
                                 deactivated is left exactly as it is. That is
                                 the difference between a seeder and a `fixtures`
                                 entry, and it is why this app has no fixtures.

`Submitting.test_two_sections_producing_one_doctype_produce_ONE_record` is the
one to read if you only read one. A Detector Test carries a smoke result AND a CO
result, both required, so two sections producing two Detector Tests for one cabin
on one day would each assert something they were never told about the other
detector. Two contradictory compliance records is the failure this whole app
exists to prevent, and it would have arrived through the feature meant to make
the evidence better.
"""

import frappe

from erpnext_mcp import records, sessions

from .fixtures import MAIN, V12TestCase, install_hrms
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_parcel",
		"create_field",
		"create_irrigation_zone",
		"create_housing_unit",
		"get_housing_unit",
		"list_inspection_templates",
		"get_inspection_template",
		"create_inspection_template",
		"update_inspection_template",
		"deactivate_inspection_template",
		"list_inspection_sessions",
		"get_inspection_session",
		"start_inspection_session",
		"submit_inspection_session",
		"propose_inspection_template_from_regulation",
		"create_farm_task",
		"generate_tasks_from_compliance_alerts",
		"refresh_compliance_alerts",
		"get_farm_task",
		"list_housing_inspections",
		"get_housing_inspection",
		"list_detector_tests",
		"get_detector_test",
		"list_water_tests",
		"generate_audit_packet",
		"list_audit_packet_types",
	)
}

TODAY = "2026-07-24"

#: One photograph, filed as a URL so no File record is needed — the same trick
#: `test_dispatch.A_PHOTO` uses.
A_PHOTO = [{"file_url": "/files/north-wall.jpg", "evidence_type": "Photo", "caption": "north wall"}]
A_SIGNATURE = [{"file_url": "/files/sig.png", "evidence_type": "Signature", "caption": "Ana"}]

#: A three-section template of the shape the spec asks for: one section producing
#: a compliance record, one producing another, one producing nothing.
THREE_SECTIONS = [
	{
		"section_name": "Habitability walk",
		"section_description": "Walk the whole cabin.",
		"produces_record_doctype": "Housing Inspection",
		"renderer_hint": "multi-photo",
		"required": True,
		"evidence_contract": {"photos": True, "signature": True, "findings_text": True},
	},
	{
		"section_name": "Detector Test",
		"produces_record_doctype": "Detector Test",
		"renderer_hint": "checklist",
		"required": True,
		"evidence_contract": {"checklist_items": ["smoke_alarm_sounds", "co_alarm_sounds"]},
	},
	{
		"section_name": "Cabin Readiness",
		"produces_record_doctype": "",
		"renderer_hint": "photo",
		"required": False,
		"evidence_contract": {"photos": True},
	},
]


class SessionTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		install_hrms()
		self.configure(enabled=1, **ALL_ON)

	# -- fixtures ------------------------------------------------------------
	def a_camp(self, unit_name="MC-Cabin-01", **overrides):
		if not STORE.rows("Parcel"):
			self.tool_data(
				"create_parcel",
				{"owning_entity": MAIN, "parcel_name": "Mill Creek", "acreage": 131.43},
			)
		payload = {
			"parcel": "Mill Creek",
			"unit_name": unit_name,
			"unit_type": "Cabin",
			"square_footage": 400,
			"capacity": 4,
			"fsma_worker_facility": True,
		}
		payload.update(overrides)
		return self.tool_data("create_housing_unit", payload)["name"]

	def a_template(self, **overrides):
		payload = {
			"template_name": "Mid-season Habitability",
			"description": "The in-season walk and detector check on an occupied cabin.",
			"applies_to_asset_type": "Housing Unit",
			"skill_required": "camp_maintenance",
			"estimated_duration_minutes": 60,
			"regulation_citations": "OAR 437-004-1120",
			"regimes": ["OR-OSHA"],
			"sections": [dict(section) for section in THREE_SECTIONS],
		}
		payload.update(overrides)
		return self.tool_data("create_inspection_template", payload)

	def a_session(self, template=None, unit=None, **overrides):
		unit = unit or self.a_camp()
		template = template or self.a_template()["name"]
		payload = {
			"template": template,
			"location": unit,
			"location_doctype": "Housing Unit",
			"worker": "HR-EMP-00002",
			"foreman": "HR-EMP-00001",
		}
		payload.update(overrides)
		return self.tool_data("start_inspection_session", payload)

	def a_full_submission(self):
		"""The two required sections of `THREE_SECTIONS`, each meeting its contract."""
		return [
			{
				"section_name": "Habitability walk",
				"evidence_file_tokens": list(A_PHOTO) + list(A_SIGNATURE),
				"notes": "",
			},
			{
				"section_name": "Detector Test",
				"checklist_values": {"smoke_alarm_sounds": True, "co_alarm_sounds": True},
				"record_data": {"smoke_detector_result": "Pass", "co_detector_result": "Pass"},
				"notes": "",
			},
		]

	def seed_the_shipped_templates(self):
		"""What `bench migrate` does. Returns the seeder's own report."""
		return sessions.seed_inspection_templates()


# ── 1 ───────────────────────────────────────────────────────────────────────
class AuthoringATemplate(SessionTestCase):
	def test_a_template_is_written_by_a_tool_call_and_is_live(self):
		created = self.a_template()
		self.assertEqual(created["version"], 1)
		self.assertTrue(created["active"])
		self.assertIsNone(created["superseded_by"])
		self.assertIn(created["name"], self.tool_data("list_inspection_templates", {})["live_templates"])

	def test_the_sections_persist_in_order_with_their_index_filled_in(self):
		"""THE ORDER IS REAL WORK: a close-down that tests the detectors before the
		heater is switched off tests them in a cabin that is still occupied."""
		name = self.a_template()["name"]
		detail = self.tool_data("get_inspection_template", {"name": name})
		self.assertEqual(
			[section["section_name"] for section in detail["sections"]],
			["Habitability walk", "Detector Test", "Cabin Readiness"],
		)
		self.assertEqual([section["order_index"] for section in detail["sections"]], [1, 2, 3])

	def test_each_section_keeps_its_contract_its_renderer_and_what_it_produces(self):
		name = self.a_template()["name"]
		sections = {
			section["section_name"]: section
			for section in self.tool_data("get_inspection_template", {"name": name})["sections"]
		}
		self.assertEqual(sections["Habitability walk"]["produces_record_doctype"], "Housing Inspection")
		self.assertEqual(sections["Habitability walk"]["renderer_hint"], "multi-photo")
		self.assertEqual(
			sections["Habitability walk"]["evidence_contract"],
			{"photos": True, "signature": True, "findings_text": True},
		)
		self.assertEqual(
			sections["Detector Test"]["evidence_contract"]["checklist_items"],
			["smoke_alarm_sounds", "co_alarm_sounds"],
		)
		self.assertIsNone(sections["Cabin Readiness"]["produces_record_doctype"])
		self.assertFalse(sections["Cabin Readiness"]["required"])

	def test_a_section_producing_nothing_is_a_real_answer(self):
		"""Nobody regulates a photograph of an emptied refrigerator as its own
		document, and inventing a doctype to hold it would be table sprawl."""
		created = self.a_template(
			template_name="Close-down",
			sections=[
				{
					"section_name": "Refrigerator Empty Check",
					"produces_record_doctype": "",
					"renderer_hint": "photo",
					"evidence_contract": {"photos": True},
				}
			],
		)
		self.assertEqual(created["produces"], [])

	def test_a_template_with_no_sections_is_refused(self):
		message = self.tool_error(
			"create_inspection_template",
			{"template_name": "Empty", "description": "nothing at all", "sections": []},
		)
		self.assertIn("non-empty list", message)

	def test_two_sections_sharing_a_name_are_refused(self):
		"""The name is the key a submission matches on, so one of the two could
		never be submitted — and which one would depend on iteration order."""
		message = self.tool_error(
			"create_inspection_template",
			{
				"template_name": "Doubled",
				"description": "two sections, one name",
				"sections": [
					{"section_name": "Walk", "evidence_contract": {"photos": True}},
					{"section_name": "walk", "evidence_contract": {"photos": True}},
				],
			},
		)
		self.assertIn("Two sections", message)

	def test_a_contract_key_outside_the_vocabulary_is_refused(self):
		"""`{"photo": true}` asks for nothing and looks like it asks for something."""
		message = self.tool_error(
			"create_inspection_template",
			{
				"template_name": "Typo",
				"description": "a contract with a typo in it",
				"sections": [{"section_name": "Walk", "evidence_contract": {"photo": True}}],
			},
		)
		self.assertIn("'photo'", message)
		self.assertIn("asks for nothing", message)

	def test_a_produced_doctype_this_site_does_not_have_is_refused_at_authoring_time(self):
		"""Rather than at submission time, while somebody is standing in a cabin."""
		message = self.tool_error(
			"create_inspection_template",
			{
				"template_name": "Spray Day",
				"description": "a section pointing at a doctype nobody has built yet",
				"sections": [{"section_name": "Product + Rate", "produces_record_doctype": "Spray Record"}],
			},
		)
		self.assertIn("Spray Record", message)
		self.assertIn("not a DocType on this site", message)

	def test_a_second_live_template_with_a_taken_name_is_refused(self):
		self.a_template()
		message = self.tool_error(
			"create_inspection_template",
			{
				"template_name": "Mid-season Habitability",
				"description": "somebody else's version of the same thing",
				"sections": [{"section_name": "Walk", "evidence_contract": {"photos": True}}],
			},
		)
		self.assertIn("already a live template", message)

	def test_an_unknown_renderer_hint_is_refused_by_name(self):
		message = self.tool_error(
			"create_inspection_template",
			{
				"template_name": "Odd",
				"description": "a renderer nobody has written",
				"sections": [{"section_name": "Walk", "renderer_hint": "hologram"}],
			},
		)
		self.assertIn("hologram", message)

	def test_an_unknown_regime_is_refused_rather_than_dropped(self):
		"""'OSHA' for 'OR-OSHA' would file this where no packet looks for it."""
		message = self.tool_error(
			"create_inspection_template",
			{
				"template_name": "Mistagged",
				"description": "a regime tag that is nearly right",
				"regimes": ["SQF"],
				"sections": [{"section_name": "Walk"}],
			},
		)
		self.assertIn("SQF", message)


# ── 2 ───────────────────────────────────────────────────────────────────────
class Versioning(SessionTestCase):
	def test_an_edit_writes_a_new_version_and_supersedes_the_old_one(self):
		first = self.a_template()["name"]
		second = self.tool_data(
			"update_inspection_template",
			{"name": first, "estimated_duration_minutes": 90},
		)
		self.assertEqual(second["version"], 2)
		self.assertEqual(second["supersedes"], first)
		self.assertNotEqual(second["name"], first)

		old = self.tool_data("get_inspection_template", {"name": first})
		self.assertEqual(old["superseded_by"], second["name"])
		self.assertFalse(old["active"])

	def test_the_old_version_keeps_its_sections_exactly_as_they_were(self):
		"""The whole reason versioning is by copy rather than in place."""
		first = self.a_template()["name"]
		self.tool_data(
			"update_inspection_template",
			{"name": first, "sections": [{"section_name": "Walk only", "evidence_contract": {}}]},
		)
		old = self.tool_data("get_inspection_template", {"name": first})
		self.assertEqual(len(old["sections"]), 3)
		self.assertEqual(old["sections"][0]["section_name"], "Habitability walk")

	def test_an_argument_left_out_means_unchanged_rather_than_cleared(self):
		first = self.a_template()["name"]
		second = self.tool_data("update_inspection_template", {"name": first, "description": "reworded"})
		self.assertEqual(second["description"], "reworded")
		self.assertEqual(second["skill_required"], "camp_maintenance")
		self.assertEqual(len(second["sections"]), 3)
		self.assertEqual(second["regimes"], ["OR-OSHA"])

	def test_the_template_name_resolves_to_whichever_version_is_live(self):
		first = self.a_template()["name"]
		second = self.tool_data("update_inspection_template", {"name": first, "description": "v2"})["name"]
		found = self.tool_data("get_inspection_template", {"name": "Mid-season Habitability"})
		self.assertEqual(found["name"], second)

	def test_superseding_a_superseded_version_is_refused_and_says_which_one_to_edit(self):
		first = self.a_template()["name"]
		second = self.tool_data("update_inspection_template", {"name": first, "description": "v2"})["name"]
		message = self.tool_error("update_inspection_template", {"name": first, "description": "v3"})
		self.assertIn(second, message)
		self.assertIn("Nothing was written", message)

	def test_a_session_pins_the_version_that_was_live_when_it_started(self):
		first = self.a_template()["name"]
		session = self.a_session(template=first)
		self.assertEqual(session["template_version"], 1)
		self.assertEqual(session["template"], first)

		self.tool_data("update_inspection_template", {"name": first, "description": "v2"})

		# The session is unchanged: the row it points at was never touched, which
		# is the answer to "what happens to a session started against v1 while v2
		# is being authored".
		after = self.tool_data("get_inspection_session", {"name": session["name"]})
		self.assertEqual(after["template"], first)
		self.assertEqual(after["template_version"], 1)
		self.assertEqual(len(after["template_detail"]["sections"]), 3)

	def test_a_session_pinned_to_v1_still_submits_against_v1s_sections(self):
		first = self.a_template()["name"]
		session = self.a_session(template=first)["name"]
		self.tool_data(
			"update_inspection_template",
			{"name": first, "sections": [{"section_name": "Something else", "evidence_contract": {}}]},
		)
		submitted = self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)
		self.assertEqual(submitted["state"], "Submitted")
		self.assertEqual(len(submitted["produced"]), 2)

	def test_a_section_from_a_later_version_is_not_a_section_this_worker_saw(self):
		first = self.a_template()["name"]
		session = self.a_session(template=first)["name"]
		self.tool_data(
			"update_inspection_template",
			{
				"name": first,
				"sections": [
					*[dict(section) for section in THREE_SECTIONS],
					{"section_name": "Propane check", "evidence_contract": {"photos": True}},
				],
			},
		)
		message = self.tool_error(
			"submit_inspection_session",
			{
				"name": session,
				"section_submissions": [
					*self.a_full_submission(),
					{"section_name": "Propane check", "evidence_file_tokens": list(A_PHOTO)},
				],
			},
		)
		self.assertIn("Propane check", message)
		self.assertIn("was worked from", message)


# ── 3 ───────────────────────────────────────────────────────────────────────
class Deactivating(SessionTestCase):
	def test_it_sets_active_off_and_records_the_reason(self):
		name = self.a_template()["name"]
		result = self.tool_data(
			"deactivate_inspection_template",
			{"name": name, "reason": "the camp moved to the county water main"},
		)
		self.assertFalse(result["active"])
		self.assertIn("county water main", result["description"])

	def test_no_new_session_starts_from_a_deactivated_template(self):
		name = self.a_template()["name"]
		unit = self.a_camp()
		self.tool_data("deactivate_inspection_template", {"name": name, "reason": "superseded by the county"})
		message = self.tool_error(
			"start_inspection_session",
			{"template": name, "location": unit, "location_doctype": "Housing Unit"},
		)
		self.assertIn("not active", message)

	def test_the_sessions_already_worked_from_it_stay_readable(self):
		"""That is what deactivating is FOR, and the reason there is no delete."""
		name = self.a_template()["name"]
		session = self.a_session(template=name)["name"]
		result = self.tool_data(
			"deactivate_inspection_template", {"name": name, "reason": "no longer how it is done"}
		)
		self.assertEqual(result["sessions_already_worked"], 1)
		still = self.tool_data("get_inspection_session", {"name": session})
		self.assertEqual(still["template"], name)
		self.assertEqual(len(still["template_detail"]["sections"]), 3)

	def test_a_reason_that_says_nothing_is_refused(self):
		name = self.a_template()["name"]
		message = self.tool_error("deactivate_inspection_template", {"name": name, "reason": "old"})
		self.assertIn("must say something", message)

	def test_deactivating_one_frees_the_name_for_a_replacement(self):
		name = self.a_template()["name"]
		self.tool_data("deactivate_inspection_template", {"name": name, "reason": "starting again"})
		replacement = self.a_template()
		self.assertEqual(replacement["version"], 1)
		self.assertNotEqual(replacement["name"], name)


# ── 4 ───────────────────────────────────────────────────────────────────────
class StartingASession(SessionTestCase):
	def test_it_writes_no_compliance_record_and_moves_no_register(self):
		before = len(STORE.rows("Housing Inspection"))
		unit = self.a_camp()
		self.a_session(unit=unit)
		self.assertEqual(len(STORE.rows("Housing Inspection")), before)
		self.assertIsNone(frappe.db.get_value("Housing Unit", unit, "last_habitability_inspection"))

	def test_a_cabin_template_cannot_be_started_against_a_block(self):
		self.a_camp()
		self.tool_data(
			"create_field",
			{"parcel": "Mill Creek", "field_name": "Yellow Camp Block 3", "acreage": 12.5},
		)
		name = self.a_template()["name"]
		message = self.tool_error(
			"start_inspection_session",
			{
				"template": name,
				"location": "Yellow Camp Block 3",
				"location_doctype": "Field",
			},
		)
		self.assertIn("applies to Housing Unit", message)

	def test_a_location_that_is_not_on_the_register_is_refused(self):
		name = self.a_template()["name"]
		message = self.tool_error(
			"start_inspection_session",
			{"template": name, "location": "MC-Cabin-99", "location_doctype": "Housing Unit"},
		)
		self.assertIn("MC-Cabin-99", message)

	def test_the_location_doctype_is_inferred_from_the_template(self):
		unit = self.a_camp()
		name = self.a_template()["name"]
		session = self.tool_data("start_inspection_session", {"template": name, "location": unit})
		self.assertEqual(session["location_doctype"], "Housing Unit")

	def test_the_handsets_visit_id_is_carried(self):
		unit = self.a_camp()
		session = self.a_session(unit=unit, visit_id="5C1F0A64-2B3D-4E5F-8A9B-0C1D2E3F4A5B")
		self.assertEqual(session["visit_id"], "5C1F0A64-2B3D-4E5F-8A9B-0C1D2E3F4A5B")

	def test_a_visit_id_no_handset_mints_is_refused_here_too(self):
		"""A session and the plain completions filed on the same walk are read as
		ONE trip, and they are read as one by matching this column exactly. A
		session admitted with a garbled identifier does not join the trip it was
		part of — it becomes a visit of its own, and `list_visits` reports both as
		if that were the day's work."""
		unit = self.a_camp()
		name = self.a_template()["name"]
		message = self.tool_error(
			"start_inspection_session",
			{"template": name, "location": unit, "visit_id": "north-block-am"},
		)
		self.assertIn("north-block-am", message)
		self.assertIn("8-4-4-4-12", message)
		self.assertEqual(STORE.rows("Inspection Session"), [])

	def test_a_farm_task_gains_the_link_back(self):
		"""The task stays the dispatch atom; the session is the form behind it."""
		unit = self.a_camp()
		task = self.tool_data(
			"create_farm_task",
			{
				"task_name": "Mid-season walk",
				"task_type": "Inspection",
				"evidence_required": {"photos": True},
				"location_doctype": "Housing Unit",
				"location": unit,
			},
		)["name"]
		session = self.a_session(unit=unit, farm_task=task)
		self.assertEqual(session["farm_task"], task)
		self.assertEqual(frappe.db.get_value("Farm Task", task, "inspection_session"), session["name"])


# ── 5 ───────────────────────────────────────────────────────────────────────
class Submitting(SessionTestCase):
	def test_every_producing_section_writes_its_own_compliance_record(self):
		unit = self.a_camp()
		session = self.a_session(unit=unit)["name"]
		result = self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)
		self.assertEqual(result["state"], "Submitted")
		self.assertEqual(
			sorted(entry["doctype"] for entry in result["produced"]),
			["Detector Test", "Housing Inspection"],
		)
		self.assertEqual(len(STORE.rows("Housing Inspection")), 1)
		self.assertEqual(len(STORE.rows("Detector Test")), 1)

	def test_the_registers_move_exactly_as_they_would_from_separate_visits(self):
		"""The records are separate, so the alerts they dismiss are separate."""
		unit = self.a_camp()
		session = self.a_session(unit=unit)["name"]
		self.tool_data(
			"submit_inspection_session",
			{
				"name": session,
				"section_submissions": self.a_full_submission(),
				"record_date": TODAY,
			},
		)
		row = frappe.db.get_value(
			"Housing Unit",
			unit,
			["last_habitability_inspection", "smoke_detector_last_test", "co_detector_last_test"],
			as_dict=True,
		)
		self.assertEqual(str(row["last_habitability_inspection"]), TODAY)
		self.assertEqual(str(row["smoke_detector_last_test"]), TODAY)
		self.assertEqual(str(row["co_detector_last_test"]), TODAY)

	def test_the_produced_record_link_is_written_back_onto_every_submission(self):
		session = self.a_session()["name"]
		self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)
		detail = self.tool_data("get_inspection_session", {"name": session})
		links = {
			entry["section_name"]: entry["produced_record_link"] for entry in detail["section_submissions"]
		}
		self.assertTrue(links["Habitability walk"])
		self.assertTrue(links["Detector Test"])
		self.assertNotEqual(links["Habitability walk"], links["Detector Test"])

	def test_a_missing_required_section_is_refused_and_nothing_is_written(self):
		"""Half a visit is a set of records that LOOK complete and are not."""
		session = self.a_session()["name"]
		message = self.tool_error(
			"submit_inspection_session",
			{"name": session, "section_submissions": [self.a_full_submission()[0]]},
		)
		self.assertIn("Detector Test", message)
		self.assertIn("Nothing was written", message)
		self.assertFalse(STORE.rows("Housing Inspection"))
		self.assertEqual(frappe.db.get_value(sessions.SESSION_DOCTYPE, session, "state"), "In Progress")

	def test_a_required_section_marked_skipped_is_still_a_missing_section(self):
		session = self.a_session()["name"]
		submission = self.a_full_submission()
		submission[1]["skipped"] = True
		message = self.tool_error(
			"submit_inspection_session", {"name": session, "section_submissions": submission}
		)
		self.assertIn("a required one cannot be", message)

	def test_a_section_short_of_its_contract_is_refused_naming_what_is_missing(self):
		session = self.a_session()["name"]
		submission = self.a_full_submission()
		submission[0]["evidence_file_tokens"] = list(A_PHOTO)  # no signature
		message = self.tool_error(
			"submit_inspection_session", {"name": session, "section_submissions": submission}
		)
		self.assertIn("Habitability walk", message)
		self.assertIn("signature", message)
		self.assertFalse(STORE.rows("Housing Inspection"))

	def test_an_empty_findings_string_is_a_positive_statement_and_satisfies_the_contract(self):
		"""Leaving it out records that nobody was asked. The two are different."""
		session = self.a_session()["name"]
		submission = self.a_full_submission()
		submission[0].pop("notes")
		message = self.tool_error(
			"submit_inspection_session", {"name": session, "section_submissions": submission}
		)
		self.assertIn("what they actually saw", message)

	def test_an_optional_section_may_be_skipped_and_produces_nothing(self):
		session = self.a_session()["name"]
		result = self.tool_data(
			"submit_inspection_session",
			{
				"name": session,
				"section_submissions": [
					*self.a_full_submission(),
					{"section_name": "Cabin Readiness", "skipped": True},
				],
			},
		)
		self.assertEqual(result["skipped_sections"], ["Cabin Readiness"])
		submissions = {entry["section_name"]: entry for entry in result["section_submissions"]}
		self.assertTrue(submissions["Cabin Readiness"]["skipped"])
		self.assertIsNone(submissions["Cabin Readiness"]["produced_record_link"])

	def test_two_sections_producing_one_doctype_produce_ONE_record(self):
		"""THE ONE TO READ. A Detector Test carries a smoke result AND a CO result,
		both required fields — so two sections filing two Detector Tests for one
		cabin on one day would each assert something they were never told about
		the other detector. Two contradictory compliance records is the failure
		this whole app exists to prevent."""
		unit = self.a_camp()
		template = self.a_template(
			template_name="Split detectors",
			sections=[
				{
					"section_name": "Smoke Detector Test",
					"produces_record_doctype": "Detector Test",
					"evidence_contract": {"checklist_items": ["smoke_alarm_sounds"]},
				},
				{
					"section_name": "CO Detector Test",
					"produces_record_doctype": "Detector Test",
					"evidence_contract": {"checklist_items": ["co_alarm_sounds"]},
				},
			],
		)["name"]
		session = self.a_session(template=template, unit=unit)["name"]
		result = self.tool_data(
			"submit_inspection_session",
			{
				"name": session,
				"section_submissions": [
					{
						"section_name": "Smoke Detector Test",
						"checklist_values": {"smoke_alarm_sounds": True},
						"record_data": {"smoke_detector_result": "Pass"},
					},
					{
						"section_name": "CO Detector Test",
						"checklist_values": {"co_alarm_sounds": True},
						"record_data": {"co_detector_result": "Fail"},
					},
				],
			},
		)
		self.assertEqual(len(result["produced"]), 1)
		self.assertEqual(len(STORE.rows("Detector Test")), 1)

		record = result["produced"][0]
		self.assertEqual(sorted(record["sections"]), ["CO Detector Test", "Smoke Detector Test"])
		row = frappe.db.get_value(
			"Detector Test",
			record["record"],
			["smoke_detector_result", "co_detector_result"],
			as_dict=True,
		)
		self.assertEqual(row["smoke_detector_result"], "Pass")
		self.assertEqual(row["co_detector_result"], "Fail")

		# Both section submissions point at the one record — the trail from either
		# side is intact.
		links = {
			entry["section_name"]: entry["produced_record_link"] for entry in result["section_submissions"]
		}
		self.assertEqual(links["Smoke Detector Test"], links["CO Detector Test"])

	def test_one_file_filed_against_two_sections_reaches_both_records(self):
		"""The shared tray, and the v0.18.4 cascade behind it. Photographing a
		cabin twice because the paperwork wanted two records is what this
		replaces."""
		STORE.seed(
			"File",
			[
				{
					"name": "file-cabin-wall",
					"file_name": "north-wall.jpg",
					"file_url": "/private/files/north-wall.jpg",
					"is_private": 1,
				}
			],
		)
		session = self.a_session()["name"]
		result = self.tool_data(
			"submit_inspection_session",
			{
				"name": session,
				"section_submissions": [
					{
						"section_name": "Habitability walk",
						"evidence_file_tokens": ["file-cabin-wall", *A_SIGNATURE],
						"notes": "",
					},
					{
						"section_name": "Detector Test",
						"evidence_file_tokens": ["file-cabin-wall"],
						"checklist_values": {"smoke_alarm_sounds": True, "co_alarm_sounds": True},
						"notes": "",
					},
				],
			},
		)
		produced = {entry["doctype"]: entry["record"] for entry in result["produced"]}
		walk = self.tool_data("get_housing_inspection", {"record": produced["Housing Inspection"]})
		test = self.tool_data("get_detector_test", {"record": produced["Detector Test"]})
		self.assertIn("file-cabin-wall", [row["file"] for row in walk["evidence"]])
		self.assertIn("file-cabin-wall", [row["file"] for row in test["evidence"]])

	def test_the_shared_tray_records_which_section_each_file_answered(self):
		session = self.a_session()["name"]
		self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)
		detail = self.tool_data("get_inspection_session", {"name": session})
		self.assertEqual({row["section_name"] for row in detail["evidence"]}, {"Habitability walk"})

	def test_a_finding_still_files_the_record_and_raises_its_own_alert(self):
		"""Doing the work and finding a problem are two different facts."""
		unit = self.a_camp()
		session = self.a_session(unit=unit)["name"]
		submission = self.a_full_submission()
		submission[0]["notes"] = "water stain, north wall, spreading"
		result = self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": submission, "record_date": TODAY},
		)
		walk = next(entry for entry in result["produced"] if entry["doctype"] == "Housing Inspection")
		self.assertTrue(walk["found_something"])
		self.assertEqual(walk["state"], records.CORRECTIVE_ACTION_REQUIRED)
		self.assertIn("findings_note", result)
		# The register STILL moved: the walk happened.
		self.assertEqual(
			str(frappe.db.get_value("Housing Unit", unit, "last_habitability_inspection")), TODAY
		)

	def test_a_second_submission_of_one_afternoon_is_refused(self):
		session = self.a_session()["name"]
		self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)
		message = self.tool_error(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)
		self.assertIn("already submitted", message)
		self.assertEqual(len(STORE.rows("Housing Inspection")), 1)

	def test_a_record_whose_subject_the_session_cannot_supply_must_be_named(self):
		"""A session happens at a cabin; a Water Test is about a water source, and
		one cabin can draw from several."""
		template = self.a_template(
			template_name="Cabin with water",
			sections=[
				{
					"section_name": "Water Supply Test",
					"produces_record_doctype": "Water Test",
					"evidence_contract": {},
				}
			],
		)["name"]
		session = self.a_session(template=template)["name"]
		message = self.tool_error(
			"submit_inspection_session",
			{"name": session, "section_submissions": [{"section_name": "Water Supply Test"}]},
		)
		self.assertIn("Irrigation Zone", message)
		self.assertIn("will not guess", message)

	def test_naming_the_subject_in_record_data_lets_the_record_be_written(self):
		unit = self.a_camp()
		self.tool_data(
			"create_field",
			{"parcel": "Mill Creek", "field_name": "Yellow Camp Block 3", "acreage": 12.5},
		)
		zone = self.tool_data(
			"create_irrigation_zone",
			{
				"field": "Yellow Camp Block 3",
				"zone_name": "YC3-Zone2",
				"zone_number": 2,
				"water_source": "creek",
			},
		)["name"]
		template = self.a_template(
			template_name="Cabin with water",
			sections=[
				{
					"section_name": "Water Supply Test",
					"produces_record_doctype": "Water Test",
					"evidence_contract": {},
				}
			],
		)["name"]
		session = self.a_session(template=template, unit=unit)["name"]
		result = self.tool_data(
			"submit_inspection_session",
			{
				"name": session,
				"section_submissions": [
					{"section_name": "Water Supply Test", "record_data": {"source": zone}}
				],
			},
		)
		self.assertEqual(result["produced"][0]["doctype"], "Water Test")
		self.assertEqual(result["produced"][0]["subject"], zone)


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheRuleEngine(SessionTestCase):
	"""Set inclusion and two integers. No model anywhere near the trigger path."""

	def a_camp_with_alerts(self, units=1):
		for index in range(1, units + 1):
			self.a_camp(f"MC-Cabin-{index:02d}")
		return self.tool_data("refresh_compliance_alerts", {"today": TODAY})

	def test_submitting_the_session_clears_both_alerts_it_answered(self):
		"""v0.64.1. The other door that writes compliance registers.

		A session is the multi-section version of one afternoon: one walk round a
		cabin filing a Housing Inspection and a Detector Test at their own
		cadences. v0.64.0 gave the narrowed sweep to `complete_farm_task` and to
		nothing else — so the same work, done through a session rather than
		through two tasks, left both alerts standing until the hourly pass. One
		farm, one afternoon, two answers depending on which screen it was filed
		from.
		"""
		self.a_camp_with_alerts(units=1)
		open_alerts = {
			str(row["alert_type"]): str(row["name"])
			for row in STORE.rows("Compliance Alert")
			if not int(row.get("dismissed") or 0)
		}
		self.assertEqual(sorted(open_alerts), ["housing_detector_test_stale", "housing_inspection_overdue"])

		session = self.a_session(unit="MC-Cabin-01 - MC")["name"]
		submitted = self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)
		self.assertEqual(len(submitted["produced"]), 2)

		evaluation = submitted["compliance_evaluation"]
		self.assertEqual(
			evaluation["rules_asked"], ["housing_detector_test_stale", "housing_inspection_overdue"]
		)
		self.assertEqual(sorted(evaluation["auto_dismissed"]), sorted(open_alerts.values()))
		for name in open_alerts.values():
			self.assertTrue(int(STORE.get_raw("Compliance Alert", name)["auto_dismissed"]))

	def test_a_session_at_one_cabin_leaves_the_next_cabins_alerts_alone(self):
		"""The sweep is narrowed by RULE and decided per RECORD. A walk at Cabin 1
		re-runs the habitability rule, which then looks at every unit and finds
		Cabin 2 still uninspected — so the row that should go, goes, and the row
		that should stay, stays."""
		self.a_camp_with_alerts(units=2)
		open_alerts = {
			str(row["source_docname"]): str(row["name"])
			for row in STORE.rows("Compliance Alert")
			if row["alert_type"] == "housing_inspection_overdue"
		}
		self.assertEqual(len(open_alerts), 2)

		session = self.a_session(unit="MC-Cabin-01 - MC")["name"]
		submitted = self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission()},
		)

		walked = open_alerts["MC-Cabin-01 - MC"]
		untouched = open_alerts["MC-Cabin-02 - MC"]
		self.assertIn(walked, submitted["compliance_evaluation"]["auto_dismissed"])
		self.assertNotIn(untouched, submitted["compliance_evaluation"]["auto_dismissed"])
		self.assertFalse(int(STORE.get_raw("Compliance Alert", untouched)["dismissed"]))

	def test_two_alerts_at_one_place_become_one_task_carrying_a_session(self):
		self.seed_the_shipped_templates()
		self.a_camp_with_alerts(units=1)
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})

		self.assertEqual(report["session_count"], 1)
		self.assertEqual(report["alerts_bundled_into_sessions"], 2)
		self.assertEqual(report["created_count"], 0)
		self.assertEqual(len(STORE.rows("Farm Task")), 1)

		entry = report["sessions"][0]
		self.assertEqual(entry["template_name"], "Mid-season Habitability")
		self.assertEqual(sorted(entry["covers"]), ["Detector Test", "Housing Inspection"])
		self.assertEqual(
			frappe.db.get_value("Farm Task", entry["task"], "inspection_session"), entry["session"]
		)

	def test_one_alert_at_a_place_is_still_one_plain_task(self):
		"""No match is a first-class answer and the common one."""
		self.seed_the_shipped_templates()
		self.a_camp_with_alerts(units=1)
		report = self.tool_data(
			"generate_tasks_from_compliance_alerts",
			{"company": MAIN, "alert_types": ["housing_inspection_overdue"]},
		)
		self.assertEqual(report["session_count"], 0)
		self.assertEqual(report["created_count"], 1)
		task = report["created"][0]["task"]
		self.assertIsNone(frappe.db.get_value("Farm Task", task, "inspection_session"))

	def test_with_no_templates_on_the_site_nothing_bundles(self):
		"""The pre-v0.21.0 behaviour, unchanged, on a site nobody has seeded."""
		self.a_camp_with_alerts(units=1)
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})
		self.assertEqual(report["session_count"], 0)
		self.assertEqual(report["created_count"], 2)

	def test_the_tightest_fitting_template_wins(self):
		"""Mid-season covers exactly what is due; Pre-season covers it and more."""
		self.seed_the_shipped_templates()
		self.a_camp_with_alerts(units=1)
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})
		self.assertEqual(report["sessions"][0]["template_name"], "Mid-season Habitability")
		self.assertEqual(report["sessions"][0]["extra_sections"], [])

	def test_running_it_twice_does_not_send_two_people_to_one_cabin(self):
		"""Idempotent by a different mechanism: the session records every alert it
		answers, and the second sweep reads them back as whole docnames."""
		self.seed_the_shipped_templates()
		self.a_camp_with_alerts(units=2)
		self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})
		again = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})
		self.assertEqual(again["session_count"], 0)
		self.assertEqual(again["created_count"], 0)
		self.assertEqual(len(again["skipped_already_answered"]), 4)
		self.assertEqual(len(STORE.rows("Farm Task")), 2)
		self.assertEqual(len(STORE.rows(sessions.SESSION_DOCTYPE)), 2)

	def test_a_dry_run_writes_no_session(self):
		self.seed_the_shipped_templates()
		self.a_camp_with_alerts(units=1)
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN, "dry_run": True})
		self.assertEqual(report["session_count"], 1)
		self.assertIsNone(report["sessions"][0]["session"])
		self.assertFalse(STORE.rows(sessions.SESSION_DOCTYPE))
		self.assertFalse(STORE.rows("Farm Task"))

	def test_the_task_is_the_dispatch_atom_and_the_session_is_the_form_behind_it(self):
		self.seed_the_shipped_templates()
		self.a_camp_with_alerts(units=1)
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})
		task = self.tool_data("get_farm_task", {"task": report["sessions"][0]["task"]})
		self.assertEqual(task["state"], "Available")
		self.assertEqual(task["dispatch_mode"], "Self-pick")
		self.assertEqual(task["skill_required"], "camp_maintenance")
		self.assertEqual(task["location"], "MC-Cabin-01 - MC")
		self.assertTrue(task["evidence_required"])

	def test_a_template_labelled_rather_than_registered_is_never_matched(self):
		"""A "Cabin" template is almost certainly about a Housing Unit and the
		engine still will not assume it. An automatic bundling of somebody's
		compliance work is exactly the place not to guess."""
		self.a_template(
			template_name="Labelled cabin visit",
			applies_to_asset_type="Cabin",
			sections=[
				{"section_name": "Walk", "produces_record_doctype": "Housing Inspection"},
				{"section_name": "Detectors", "produces_record_doctype": "Detector Test"},
			],
		)
		self.a_camp_with_alerts(units=1)
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})
		self.assertEqual(report["session_count"], 0)
		self.assertEqual(report["created_count"], 2)

	def test_a_template_that_covers_only_half_the_pending_alerts_does_not_match(self):
		"""Superset or nothing. A visit that silently answers three of four overdue
		things leaves the fourth answered by nothing."""
		self.a_template(
			template_name="Walk only",
			sections=[{"section_name": "Walk", "produces_record_doctype": "Housing Inspection"}],
		)
		self.a_camp_with_alerts(units=1)
		report = self.tool_data("generate_tasks_from_compliance_alerts", {"company": MAIN})
		self.assertEqual(report["session_count"], 0)
		self.assertEqual(report["created_count"], 2)


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheSeededTemplates(SessionTestCase):
	def test_install_creates_the_four(self):
		report = self.seed_the_shipped_templates()
		self.assertEqual(
			sorted(report["created"]),
			[
				"Mid-season Habitability",
				"Post-harvest Cabin Close-down",
				"Pre-season Cabin Opening",
				"Spray Day Inspection",
			],
		)
		self.assertFalse(report["failed"])

	def test_a_second_migrate_creates_nothing(self):
		self.seed_the_shipped_templates()
		again = self.seed_the_shipped_templates()
		self.assertFalse(again["created"])
		self.assertEqual(len(again["present"]), 4)
		self.assertEqual(len(STORE.rows(sessions.TEMPLATE_DOCTYPE)), 4)

	def test_an_operator_edit_survives_the_next_migrate(self):
		"""THE DIFFERENCE BETWEEN A SEEDER AND A `fixtures` ENTRY. An operator who
		added a section to their close-down would get it silently removed by a
		fixture, and the first anybody would know is a winter with no propane
		check."""
		self.seed_the_shipped_templates()
		close_down = self.tool_data("get_inspection_template", {"name": "Post-harvest Cabin Close-down"})
		edited = self.tool_data(
			"update_inspection_template",
			{
				"name": close_down["name"],
				"sections": [
					*[
						{
							"section_name": section["section_name"],
							"produces_record_doctype": section["produces_record_doctype"] or "",
							"renderer_hint": section["renderer_hint"],
							"required": section["required"],
							"evidence_contract": section["evidence_contract"],
						}
						for section in close_down["sections"]
					],
					{
						"section_name": "Chimney swept",
						"produces_record_doctype": "",
						"evidence_contract": {"photos": True},
					},
				],
			},
		)
		self.seed_the_shipped_templates()

		live = self.tool_data("get_inspection_template", {"name": "Post-harvest Cabin Close-down"})
		self.assertEqual(live["name"], edited["name"])
		self.assertEqual(live["version"], 2)
		self.assertIn("Chimney swept", [section["section_name"] for section in live["sections"]])
		# And no version 1 was seeded back beside it, which would have put two
		# live templates with one name on the site.
		self.assertEqual(
			len(
				[
					row
					for row in STORE.rows(sessions.TEMPLATE_DOCTYPE)
					if row["template_name"] == "Post-harvest Cabin Close-down"
				]
			),
			2,
		)

	def test_a_deactivated_seeded_template_is_not_re_enabled_every_migrate(self):
		self.seed_the_shipped_templates()
		spray = self.tool_data("get_inspection_template", {"name": "Spray Day Inspection"})
		self.tool_data(
			"deactivate_inspection_template",
			{"name": spray["name"], "reason": "this operation contracts its spraying out"},
		)
		self.seed_the_shipped_templates()
		self.assertFalse(self.tool_data("get_inspection_template", {"name": spray["name"]})["active"])

	def test_the_seeded_templates_carry_their_citations_and_regimes(self):
		"""A template is a compliance artefact rather than a convenience: it states,
		before anybody works it, which rule the afternoon answers."""
		self.seed_the_shipped_templates()
		opening = self.tool_data("get_inspection_template", {"name": "Pre-season Cabin Opening"})
		self.assertIn("OAR 437-004-1120", opening["regulation_citations"])
		self.assertEqual(opening["regimes"], ["FSMA", "OR-OSHA"])

	def test_the_spray_day_product_section_produces_nothing_until_the_doctype_exists(self):
		"""There is no Spray Record doctype yet, and a section pointing at one that
		does not exist would refuse every submission."""
		self.seed_the_shipped_templates()
		spray = self.tool_data("get_inspection_template", {"name": "Spray Day Inspection"})
		product = next(
			section for section in spray["sections"] if section["section_name"] == "Product + Rate Recording"
		)
		self.assertIsNone(product["produces_record_doctype"])
		self.assertEqual(spray["produces"], [])


# ── 8 ───────────────────────────────────────────────────────────────────────
class TheAuditPacket(SessionTestCase):
	def a_submitted_session(self):
		unit = self.a_camp()
		session = self.a_session(unit=unit)["name"]
		self.tool_data(
			"submit_inspection_session",
			{"name": session, "section_submissions": self.a_full_submission(), "record_date": TODAY},
		)
		return session

	def packet(self, audit_type="OSHA"):
		data = self.tool_data(
			"generate_audit_packet",
			{
				"audit_type": audit_type,
				"company": MAIN,
				"period_start": "2026-01-01",
				"period_end": "2026-12-31",
				"allow_open_actions": True,
			},
		)
		return next(section for section in data["packet"]["sections"] if section["key"] == "sessions")

	def test_the_packet_says_which_visit_produced_which_records(self):
		session = self.a_submitted_session()
		section = self.packet()
		self.assertEqual(section["row_count"], 1)
		row = section["rows"][0]
		self.assertEqual(row["session"], session)
		self.assertEqual(row["template"], "Mid-season Habitability")
		self.assertEqual(row["template_version"], 1)
		self.assertEqual(row["record_count"], 2)
		self.assertEqual(row["location"], "MC-Cabin-01 - MC")

	def test_it_names_the_worker_and_the_foreman(self):
		"""'Ana Ramos, foreman Miguel Torres' is the sentence an auditor wants."""
		self.a_submitted_session()
		row = self.packet()["rows"][0]
		self.assertTrue(row["worker"])
		self.assertTrue(row["foreman"])
		self.assertNotEqual(row["worker"], row["foreman"])

	def test_a_started_but_unsubmitted_visit_is_counted_rather_than_listed(self):
		"""An abandoned visit is not evidence of anything, and putting it in a
		packet beside real ones with nothing to distinguish them would be worse
		than leaving it out silently."""
		self.a_session()
		section = self.packet()
		self.assertEqual(section["row_count"], 0)
		self.assertEqual(section["open_sessions_in_period"], 1)
		self.assertIn("never submitted", section["problem_note"])

	def test_the_records_themselves_are_still_in_their_own_sections(self):
		"""This section adds no record to the packet — it adds the sentence
		joining the ones already there."""
		self.a_submitted_session()
		data = self.tool_data(
			"generate_audit_packet",
			{
				"audit_type": "OSHA",
				"company": MAIN,
				"period_start": "2026-01-01",
				"period_end": "2026-12-31",
				"allow_open_actions": True,
			},
		)
		housing = next(section for section in data["packet"]["sections"] if section["key"] == "housing")
		self.assertEqual(housing["rows"][0]["last_habitability_inspection"], TODAY)


# ── 9 ───────────────────────────────────────────────────────────────────────
class TheSurfaceThatWasDeclaredFirst(SessionTestCase):
	"""v0.21.0 reserved the AI proposer and left it refusing. v0.37.0 filled it.

	The rails are taken apart one at a time in `test_ai_proposals.py`; what is
	asserted here is what THIS file is responsible for — that a form a model
	drafted is not a form any handset can fetch, which is the templates-as-data
	claim's other half. A template is live the moment it is written, so the only
	thing standing between a plausible draft and a worker's screen is that a
	proposal lands inactive.
	"""

	def test_a_drafted_template_is_not_a_live_template(self):
		data = self.tool_data(
			"propose_inspection_template_from_regulation",
			{
				"template_name": "Shade and Water Check",
				"description": "The hot-afternoon walk.",
				"regulation_section": "OAR 437-004-1131",
				"sections": [{"section_name": "Shade", "evidence_contract": {"photos": True}}],
			},
		)
		self.assertFalse(data["active"])
		self.assertEqual(data["authored_by"], "AI-proposed")
		self.assertNotIn(data["name"], self.tool_data("list_inspection_templates", {})["live_templates"])

	def test_a_draft_with_no_source_writes_nothing(self):
		before = len(STORE.rows(sessions.TEMPLATE_DOCTYPE))
		message = self.tool_error(
			"propose_inspection_template_from_regulation",
			{
				"template_name": "Shade and Water Check",
				"description": "The hot-afternoon walk.",
				"sections": [{"section_name": "Shade", "evidence_contract": {"photos": True}}],
			},
		)
		self.assertIn("where it was read from", message)
		self.assertEqual(len(STORE.rows(sessions.TEMPLATE_DOCTYPE)), before)
