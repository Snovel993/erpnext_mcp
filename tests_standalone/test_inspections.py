# SPDX-License-Identifier: MIT
"""The three compliance records a task completion produces — Sprint 8 Feature B.

THE SHAPE OF THIS FILE IS THE SHAPE OF THE CLAIM, and the claim is that a
compliance record is not a status change. Four things have to be true of every
one of them, and each gets its own class:

    THE BRANCH IS COMPUTED       findings blank gives Recorded; findings present
                                 gives Corrective Action Required. Nobody chooses
                                 it, so nobody can write "water stain, north wall"
                                 and mark the walk as passed.

    THE REGISTER MOVES           recording the work writes the date on the Housing
                                 Unit / Irrigation Zone / Field, which is the ONLY
                                 honest way an alert goes away — change the world
                                 and let the sweep notice.

    IT ONLY MOVES FORWARD        a back-dated record is filed as evidence and does
                                 NOT drag a register backwards, because that would
                                 re-raise an alert about work since done.

    FINDING SOMETHING IS NOT     a walk that found a fault still moves the
    FAILING TO DO THE WORK       inspection date forward AND raises a Critical
                                 alert of its own. Both facts are true and the
                                 tests assert both together.

THE TWO NEW ALERT RULES ARE TESTED THE SAME WAY THE FIRST NINE WERE: fires when
ripe, silent when unripe, and — the half that would be easiest to leave out —
goes quiet BY ITSELF when the work that makes it untrue is done. For these two
that is being superseded by a later clean record, which is what a real operation
produces: re-inspect the cabin, re-sample the line.

`UnreadableIsNotClean` IS THE ONE TO READ IF YOU ONLY READ ONE. A laboratory
result nobody can interpret must not be treated as a pass. Treating it as one is
how a compliance file becomes a clean record of nothing, and it is a failure that
never shows up until somebody reads the file.
"""

from erpnext_mcp import records

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_parcel",
		"create_field",
		"create_irrigation_zone",
		"create_housing_unit",
		"update_housing_unit",
		"get_housing_unit",
		"get_irrigation_zone",
		"get_field",
		"list_housing_inspections",
		"get_housing_inspection",
		"create_housing_inspection",
		"update_housing_inspection",
		"list_detector_tests",
		"get_detector_test",
		"create_detector_test",
		"update_detector_test",
		"list_water_tests",
		"get_water_test",
		"create_water_test",
		"update_water_test",
		"refresh_compliance_alerts",
		"get_compliance_calendar",
		"list_compliance_rules",
	)
}

TODAY = "2026-07-24"


class RecordTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

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

	def a_zone(self, zone_name="YC3-Zone2"):
		if not STORE.rows("Parcel"):
			self.tool_data(
				"create_parcel",
				{"owning_entity": MAIN, "parcel_name": "Mill Creek", "acreage": 131.43},
			)
		if not STORE.rows("Field"):
			self.tool_data(
				"create_field",
				{"parcel": "Mill Creek", "field_name": "Yellow Camp Block 3", "acreage": 12.5},
			)
		return self.tool_data(
			"create_field" if False else "create_irrigation_zone",
			{"field": "Yellow Camp Block 3", "zone_name": zone_name, "zone_number": 2, "water_source": "creek"},
		)["name"]

	def unit_dates(self, unit):
		row = self.tool_data("get_housing_unit", {"unit": unit})
		return (
			row["last_habitability_inspection"],
			row["smoke_detector_last_test"],
			row["co_detector_last_test"],
		)


# ── the branch ──────────────────────────────────────────────────────────────
class TheBranchIsComputedFromTheFindings(RecordTestCase):
	def test_a_clean_walk_is_recorded(self):
		unit = self.a_camp()
		data = self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": TODAY})
		self.assertEqual(data["workflow_state"], records.RECORDED)
		self.assertFalse(data["found_something"])

	def test_findings_route_it_to_corrective_action_required(self):
		unit = self.a_camp()
		data = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "findings": "water stain, north wall, spreading"},
		)
		self.assertEqual(data["workflow_state"], records.CORRECTIVE_ACTION_REQUIRED)
		self.assertTrue(data["found_something"])

	def test_the_state_cannot_be_argued_into_being_clean(self):
		"""The whole point. `workflow_state` is not an argument these tools take,
		and passing one anyway changes nothing: the controller recomputes it from
		the findings on every save, so somebody who has written 'no detector in the
		back bedroom' has no path to a record that reads as passed."""
		unit = self.a_camp()
		data = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "workflow_state": "Recorded", "findings": "no detector in the back bedroom"},
		)
		self.assertEqual(data["workflow_state"], records.CORRECTIVE_ACTION_REQUIRED)

	def test_clearing_the_findings_returns_it_to_recorded(self):
		unit = self.a_camp()
		created = self.tool_data(
			"create_housing_inspection", {"unit": unit, "inspection_date": TODAY, "findings": "loose step"}
		)
		fixed = self.tool_data("update_housing_inspection", {"record": created["name"], "findings": ""})
		self.assertEqual(fixed["workflow_state"], records.RECORDED)
		self.assertEqual(fixed["state_was"], records.CORRECTIVE_ACTION_REQUIRED)

	def test_a_draft_is_a_note_and_not_evidence(self):
		unit = self.a_camp()
		data = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "keep_as_draft": True},
		)
		self.assertEqual(data["workflow_state"], records.DRAFT)
		self.assertIsNone(self.unit_dates(unit)[0])
		self.assertIn("not evidence", data["note"])


# ── the register ────────────────────────────────────────────────────────────
class RecordingTheWorkMovesTheRegister(RecordTestCase):
	def test_a_clean_walk_moves_the_units_inspection_date(self):
		unit = self.a_camp()
		self.assertIsNone(self.unit_dates(unit)[0])
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": TODAY})
		self.assertEqual(self.unit_dates(unit)[0], TODAY)

	def test_a_walk_that_found_something_STILL_moves_the_date(self):
		"""Doing the work and finding a problem are two different facts."""
		unit = self.a_camp()
		data = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "findings": "water stain"},
		)
		self.assertEqual(self.unit_dates(unit)[0], TODAY)
		self.assertEqual(data["workflow_state"], records.CORRECTIVE_ACTION_REQUIRED)
		self.assertIn("The work itself IS recorded", data["note"])

	def test_a_back_dated_walk_never_drags_the_register_backwards(self):
		"""March's walk typed in July must not re-raise an alert cleared in June."""
		unit = self.a_camp()
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": "2026-06-01"})
		data = self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": "2026-03-02"})
		self.assertEqual(self.unit_dates(unit)[0], "2026-06-01")
		self.assertTrue(data["registers_not_moved"])
		self.assertIn("since been done", data["registers_not_moved"][0])

	def test_the_evidence_is_filed_even_when_the_register_does_not_move(self):
		unit = self.a_camp()
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": "2026-06-01"})
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": "2026-03-02"})
		self.assertEqual(self.tool_data("list_housing_inspections", {})["count"], 2)


# ── detector tests ──────────────────────────────────────────────────────────
class DetectorTests(RecordTestCase):
	def test_two_passes_move_both_dates(self):
		unit = self.a_camp()
		data = self.tool_data("create_detector_test", {"unit": unit, "test_date": TODAY})
		self.assertEqual(data["workflow_state"], records.RECORDED)
		self.assertEqual(self.unit_dates(unit)[1:], (TODAY, TODAY))

	def test_a_failure_still_writes_the_date_because_the_ignorance_is_over(self):
		"""The stale alert asks whether anybody KNOWS. A Fail answers it."""
		unit = self.a_camp()
		data = self.tool_data(
			"create_detector_test", {"unit": unit, "test_date": TODAY, "co_detector_result": "Fail"}
		)
		self.assertEqual(data["workflow_state"], records.CORRECTIVE_ACTION_REQUIRED)
		self.assertEqual(self.unit_dates(unit)[2], TODAY)
		self.assertIn("the CO detector failed its test", data["faults"])

	def test_not_present_writes_no_date_because_there_was_nothing_to_test(self):
		unit = self.a_camp()
		data = self.tool_data(
			"create_detector_test",
			{"unit": unit, "test_date": TODAY, "smoke_detector_result": "Not Present"},
		)
		self.assertIsNone(self.unit_dates(unit)[1])
		self.assertEqual(self.unit_dates(unit)[2], TODAY)
		self.assertIn("nothing there to have tested", data["not_present_note"])

	def test_a_fault_sets_replacement_needed_without_being_asked(self):
		unit = self.a_camp()
		data = self.tool_data(
			"create_detector_test", {"unit": unit, "test_date": TODAY, "smoke_detector_result": "Fail"}
		)
		self.assertEqual(data["replacement_needed"], 1)

	def test_a_replacement_raises_a_farm_task_to_go_and_fit_one(self):
		"""A checkbox with nobody dispatched against it is a finding that survives
		until next year's test rediscovers it."""
		unit = self.a_camp()
		data = self.tool_data(
			"create_detector_test", {"unit": unit, "test_date": TODAY, "co_detector_result": "Fail"}
		)
		self.assertTrue(data["replacement_task"])
		task = STORE.get_raw("Farm Task", data["replacement_task"])
		self.assertEqual(task["task_type"], "Repair")
		self.assertEqual(task["urgency"], "Critical")
		self.assertEqual(task["location"], unit)
		self.assertEqual(task["dispatch_mode"], "Dispatched")

	def test_a_clean_test_raises_no_task(self):
		unit = self.a_camp()
		data = self.tool_data("create_detector_test", {"unit": unit, "test_date": TODAY})
		self.assertNotIn("replacement_task", data.get("replacement_task") or {})
		self.assertFalse(STORE.rows("Farm Task"))


# ── water tests ─────────────────────────────────────────────────────────────
class WaterTests(RecordTestCase):
	def test_a_clean_sample_moves_the_zone_AND_the_block(self):
		"""The rule reads the block; the sample came from the zone. Both, or the
		calendar disagrees with the laboratory."""
		zone = self.a_zone()
		self.tool_data(
			"create_water_test",
			{"source": zone, "test_date": TODAY, "coliform_result": "Absent", "ecoli_result": "<1"},
		)
		self.assertEqual(self.tool_data("get_irrigation_zone", {"zone": zone})["water_test_last_date"], TODAY)
		self.assertEqual(
			self.tool_data("get_field", {"field": "Yellow Camp Block 3"})["water_test_last_date"], TODAY
		)

	def test_a_presence_result_is_a_detection(self):
		zone = self.a_zone()
		data = self.tool_data(
			"create_water_test", {"source": zone, "test_date": TODAY, "coliform_result": "Present"}
		)
		self.assertEqual(data["contamination_detected"], 1)
		self.assertEqual(data["workflow_state"], records.CORRECTIVE_ACTION_REQUIRED)

	def test_a_count_above_zero_is_a_detection(self):
		zone = self.a_zone()
		data = self.tool_data(
			"create_water_test", {"source": zone, "test_date": TODAY, "coliform_result": "23 MPN/100mL"}
		)
		self.assertEqual(data["contamination_detected"], 1)

	def test_generic_ecoli_over_the_fsma_criterion_is_named_as_such(self):
		zone = self.a_zone()
		data = self.tool_data(
			"create_water_test",
			{"source": zone, "test_date": TODAY, "coliform_result": "Absent", "ecoli_result": "230"},
		)
		self.assertEqual(data["ecoli_action_level_cfu_per_100ml"], records.ECOLI_ACTION_LEVEL_CFU)
		self.assertTrue(any("112.44(b)" in concern for concern in data["concerns"]))

	def test_a_draft_sample_waits_for_the_laboratory_and_writes_nothing(self):
		"""A sample is taken on Monday and answered on Thursday."""
		zone = self.a_zone()
		data = self.tool_data(
			"create_water_test", {"source": zone, "test_date": TODAY, "keep_as_draft": True}
		)
		self.assertEqual(data["workflow_state"], records.DRAFT)
		self.assertIsNone(self.tool_data("get_irrigation_zone", {"zone": zone})["water_test_last_date"])

	def test_filing_the_answer_publishes_the_same_record_rather_than_a_second_one(self):
		zone = self.a_zone()
		draft = self.tool_data(
			"create_water_test", {"source": zone, "test_date": TODAY, "keep_as_draft": True}
		)
		published = self.tool_data(
			"update_water_test",
			{
				"record": draft["name"],
				"keep_as_draft": False,
				"coliform_result": "Absent",
				"ecoli_result": "<1",
				"laboratory": "Columbia Analytical",
				"lab_reported_on": "2026-07-27",
			},
		)
		self.assertEqual(published["name"], draft["name"])
		self.assertEqual(published["workflow_state"], records.RECORDED)
		self.assertEqual(self.tool_data("list_water_tests", {})["count"], 1)
		self.assertEqual(self.tool_data("get_irrigation_zone", {"zone": zone})["water_test_last_date"], TODAY)

	def test_a_missing_lab_report_is_named_because_it_is_the_irreplaceable_part(self):
		zone = self.a_zone()
		data = self.tool_data("create_water_test", {"source": zone, "test_date": TODAY, "coliform_result": "Absent"})
		self.assertIn("transcription", data["missing_lab_report"])


class UnreadableIsNotClean(RecordTestCase):
	"""A result nobody can interpret is not evidence that the water is safe."""

	def test_a_result_nobody_can_read_routes_to_corrective_action(self):
		zone = self.a_zone()
		data = self.tool_data(
			"create_water_test",
			{"source": zone, "test_date": TODAY, "coliform_result": "see attached report"},
		)
		self.assertEqual(data["workflow_state"], records.CORRECTIVE_ACTION_REQUIRED)
		self.assertTrue(any("could not be read" in concern for concern in data["concerns"]))

	def test_it_is_not_reported_as_contamination_detected(self):
		"""Unreadable and dirty are different findings and both need somebody."""
		zone = self.a_zone()
		data = self.tool_data(
			"create_water_test",
			{"source": zone, "test_date": TODAY, "coliform_result": "see attached report"},
		)
		self.assertEqual(data["contamination_detected"], 0)

	def test_the_parser_reads_words_first_and_numbers_second(self):
		for text, expected in (
			("Absent", False),
			("absent", False),
			("<1", False),
			("0", False),
			("Not Detected", False),
			("Present", True),
			("POSITIVE", True),
			("23 MPN/100mL", True),
			("12", True),
			("see attached", None),
			("", None),
		):
			with self.subTest(result=text):
				self.assertIs(records.result_is_detection(text), expected)

	def test_a_blank_ecoli_count_is_not_a_count_of_zero(self):
		"""Pretending a presence result is 0 CFU would report it as comfortably
		under the limit."""
		self.assertIsNone(records.ecoli_over_action_level("Present"))
		self.assertIs(records.ecoli_over_action_level("230"), True)
		self.assertIs(records.ecoli_over_action_level("12"), False)


# ── the refusals ────────────────────────────────────────────────────────────
class WhatTheseRefuse(RecordTestCase):
	def test_a_future_inspection_is_refused(self):
		unit = self.a_camp()
		message = self.tool_error(
			"create_housing_inspection", {"unit": unit, "inspection_date": "2030-01-01"}
		)
		self.assertIn("Nobody has walked it yet", message)

	def test_a_unit_that_does_not_exist_is_refused(self):
		message = self.tool_error("create_housing_inspection", {"unit": "MC-Cabin-99 - MC"})
		self.assertIn("no Housing Unit", message)
		self.assertIn("Nothing was created", message)

	def test_closing_a_finding_without_saying_what_was_done_is_refused(self):
		unit = self.a_camp()
		created = self.tool_data(
			"create_housing_inspection", {"unit": unit, "inspection_date": TODAY, "findings": "loose step"}
		)
		message = self.tool_error(
			"update_housing_inspection",
			{"record": created["name"], "corrective_action_closed": TODAY},
		)
		self.assertIn("closure note saying what was actually done", message)

	def test_a_closure_before_the_inspection_is_refused(self):
		unit = self.a_camp()
		created = self.tool_data(
			"create_housing_inspection", {"unit": unit, "inspection_date": TODAY, "findings": "loose step"}
		)
		message = self.tool_error(
			"update_housing_inspection",
			{
				"record": created["name"],
				"corrective_action_closed": "2026-01-01",
				"closure_note": "replaced the tread",
			},
		)
		self.assertIn("Nothing was fixed before it was found", message)

	def test_a_water_sample_from_a_zone_that_does_not_exist_is_refused(self):
		message = self.tool_error("create_water_test", {"source": "Nowhere-Zone1 - MC"})
		self.assertIn("no Irrigation Zone", message)

	def test_evidence_pointing_at_a_file_that_does_not_exist_is_refused(self):
		"""The worst kind of missing evidence: it satisfies a contract, files a
		row, and proves nothing until an auditor clicks it."""
		unit = self.a_camp()
		message = self.tool_error(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "photos": ["not-a-real-file"]},
		)
		self.assertIn("not on this site", message)
		self.assertIn("Nothing was written", message)

	def test_a_file_url_needs_no_file_record(self):
		unit = self.a_camp()
		data = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "photos": ["/files/north-wall.jpg"]},
		)
		self.assertEqual(data["evidence_count"], 1)
		self.assertEqual(data["evidence"][0]["file_url"], "/files/north-wall.jpg")

	def test_an_update_that_changes_nothing_names_what_it_would_take(self):
		unit = self.a_camp()
		created = self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": TODAY})
		message = self.tool_error("update_housing_inspection", {"record": created["name"]})
		self.assertIn("nothing to change", message)
		self.assertIn("findings", message)


# ── the alerts ──────────────────────────────────────────────────────────────
class DoingTheWorkDismissesTheAlert(RecordTestCase):
	"""The only honest way an alert goes away: change the world, let the sweep see."""

	def sweep(self, **kwargs):
		return self.tool_data("refresh_compliance_alerts", kwargs)

	def test_a_walk_auto_dismisses_housing_inspection_overdue(self):
		unit = self.a_camp()
		first = self.sweep(today=TODAY)
		self.assertIn(
			"housing_inspection_overdue", {entry["alert_type"] for entry in first["alerts"]}
		)
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": TODAY})
		second = self.sweep(today=TODAY)
		dismissed = [
			entry
			for entry in second["alerts"]
			if entry["alert_type"] == "housing_inspection_overdue" and entry["outcome"] == "auto_dismissed"
		]
		self.assertEqual(len(dismissed), 1, second["alerts"])

	def test_a_detector_test_auto_dismisses_the_stale_detector_alert(self):
		unit = self.a_camp()
		self.sweep(today=TODAY)
		self.tool_data("create_detector_test", {"unit": unit, "test_date": TODAY})
		second = self.sweep(today=TODAY)
		self.assertTrue(
			[
				entry
				for entry in second["alerts"]
				if entry["alert_type"] == "housing_detector_test_stale"
				and entry["outcome"] == "auto_dismissed"
			]
		)

	def test_a_detector_recorded_not_present_keeps_the_stale_alert_alive(self):
		"""Nothing was tested, so nothing is known, and the calendar should say so."""
		unit = self.a_camp()
		self.sweep(today=TODAY)
		self.tool_data(
			"create_detector_test",
			{"unit": unit, "test_date": TODAY, "co_detector_result": "Not Present"},
		)
		second = self.sweep(today=TODAY)
		self.assertFalse(
			[
				entry
				for entry in second["alerts"]
				if entry["alert_type"] == "housing_detector_test_stale"
				and entry["outcome"] == "auto_dismissed"
			]
		)


class TheCorrectiveActionRules(RecordTestCase):
	"""v0.16.0's two new rules: they fire on KNOWLEDGE rather than ignorance."""

	def sweep(self, **kwargs):
		return self.tool_data("refresh_compliance_alerts", kwargs)

	def alerts_of(self, report, alert_type):
		return [entry for entry in report["alerts"] if entry["alert_type"] == alert_type]

	def test_a_finding_raises_a_critical_alert(self):
		unit = self.a_camp()
		self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "findings": "no detector in the back bedroom"},
		)
		raised = self.alerts_of(self.sweep(today=TODAY), "housing_corrective_action_open")
		self.assertEqual(len(raised), 1)
		self.assertEqual(raised[0]["severity"], "Critical")

	def test_a_clean_walk_raises_nothing(self):
		unit = self.a_camp()
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": TODAY})
		self.assertFalse(self.alerts_of(self.sweep(today=TODAY), "housing_corrective_action_open"))

	def test_a_draft_finding_raises_nothing_because_a_draft_is_not_evidence(self):
		unit = self.a_camp()
		self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "findings": "loose step", "keep_as_draft": True},
		)
		self.assertFalse(self.alerts_of(self.sweep(today=TODAY), "housing_corrective_action_open"))

	def test_a_later_clean_walk_supersedes_the_finding_with_nobody_ticking_anything(self):
		"""The exit that happens in practice, and the reason the rule is worth having."""
		unit = self.a_camp()
		self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": "2026-07-01", "findings": "water stain"},
		)
		self.sweep(today=TODAY)
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": "2026-07-20"})
		after = self.alerts_of(self.sweep(today=TODAY), "housing_corrective_action_open")
		self.assertEqual([entry["outcome"] for entry in after], ["auto_dismissed"])

	def test_closing_it_by_hand_also_silences_it(self):
		unit = self.a_camp()
		created = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": "2026-07-01", "findings": "water stain"},
		)
		self.sweep(today=TODAY)
		self.tool_data(
			"update_housing_inspection",
			{
				"record": created["name"],
				"corrective_action_closed": "2026-07-20",
				"closure_note": "re-flashed the window and repainted",
			},
		)
		after = self.alerts_of(self.sweep(today=TODAY), "housing_corrective_action_open")
		self.assertEqual([entry["outcome"] for entry in after], ["auto_dismissed"])

	def test_a_failed_detector_raises_the_same_rule(self):
		"""One rule over both camp records: it is one walk round the camp."""
		unit = self.a_camp()
		self.tool_data(
			"create_detector_test", {"unit": unit, "test_date": TODAY, "co_detector_result": "Fail"}
		)
		raised = self.alerts_of(self.sweep(today=TODAY), "housing_corrective_action_open")
		self.assertEqual(len(raised), 1)
		self.assertIn("Detector Test", raised[0]["source"])

	def test_contaminated_water_raises_its_own_critical_rule(self):
		zone = self.a_zone()
		self.tool_data(
			"create_water_test", {"source": zone, "test_date": TODAY, "coliform_result": "Present"}
		)
		raised = self.alerts_of(self.sweep(today=TODAY), "water_test_contamination")
		self.assertEqual(len(raised), 1)
		self.assertEqual(raised[0]["severity"], "Critical")

	def test_a_later_clean_sample_supersedes_the_contamination(self):
		zone = self.a_zone()
		self.tool_data(
			"create_water_test", {"source": zone, "test_date": "2026-07-01", "coliform_result": "Present"}
		)
		self.sweep(today=TODAY)
		self.tool_data(
			"create_water_test", {"source": zone, "test_date": "2026-07-20", "coliform_result": "Absent"}
		)
		after = self.alerts_of(self.sweep(today=TODAY), "water_test_contamination")
		self.assertEqual([entry["outcome"] for entry in after], ["auto_dismissed"])

	def test_the_sweep_stays_idempotent_with_the_new_rules(self):
		unit = self.a_camp()
		self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "findings": "water stain"},
		)
		self.sweep(today=TODAY)
		before = len(STORE.rows("Compliance Alert"))
		self.sweep(today=TODAY)
		self.sweep(today=TODAY)
		self.assertEqual(len(STORE.rows("Compliance Alert")), before)

	def test_both_new_rules_carry_a_kairotic_gate(self):
		rules = {rule["alert_type"]: rule for rule in self.tool_data("list_compliance_rules", {})["rules"]}
		for key in ("housing_corrective_action_open", "water_test_contamination"):
			with self.subTest(rule=key):
				self.assertIn("supersede", rules[key]["kairotic_gate"].lower())


class TheRecordReads(RecordTestCase):
	def test_the_list_names_the_open_findings_first(self):
		unit = self.a_camp()
		self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": "2026-07-01"})
		bad = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": "2026-07-02", "findings": "loose step"},
		)
		data = self.tool_data("list_housing_inspections", {})
		self.assertEqual(data["open_corrective_actions"], [bad["name"]])
		self.assertIn("judged on closing findings", data["note"])

	def test_get_shows_the_units_whole_history_and_what_superseded_this_one(self):
		unit = self.a_camp()
		bad = self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": "2026-07-01", "findings": "water stain"},
		)
		good = self.tool_data("create_housing_inspection", {"unit": unit, "inspection_date": "2026-07-20"})
		data = self.tool_data("get_housing_inspection", {"record": bad["name"]})
		self.assertEqual(len(data["subject_history"]), 2)
		self.assertEqual(data["superseded_by"], good["name"])

	def test_drafts_are_counted_and_explained(self):
		unit = self.a_camp()
		self.tool_data(
			"create_housing_inspection",
			{"unit": unit, "inspection_date": TODAY, "keep_as_draft": True},
		)
		data = self.tool_data("list_housing_inspections", {})
		self.assertEqual(len(data["drafts"]), 1)
		self.assertIn("dismisses no alert", data["draft_note"])
