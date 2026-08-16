# SPDX-License-Identifier: MIT
"""Progressive discipline: the chain, and the gaps a claim would find in it.

THIS IS THE LEGALLY LOAD-BEARING MODULE OF v0.79.0. In a wrongful-termination
claim the question is almost never whether the last step was deserved; it is
whether the employer can produce a documented, escalating, acknowledged series
of steps. So every class below is one property of that series.

1. **THE CHAIN LINKS ITSELF.** `TheChainIsFoundNotTyped`. `prior_record` is
   filled in from the employee's own history, because a chain assembled by hand
   is a chain with a link missing on the day somebody is in a hurry — and the
   missing link is what the claim is about.

2. **A SKIP IS ALLOWED AND HAS TO BE EXPLAINED.** `AnUnexplainedJumpIsRefused`.
   Going straight to a final warning may be entirely right; "why was this
   person's second step everyone else's fourth" is asked every time.

3. **A REFUSAL TO SIGN IS AN OUTCOME, NOT A GAP.** `TheAcknowledgementIsEitherOr`.
   An employee may decline. What the file may not contain is silence presented
   as agreement.

4. **THE REPORT NAMES THE HOLES.** `TheGapsAreThePoint`. A report that only
   listed the steps would leave the reader to find what is missing, and what is
   missing is what decides the case.

5. **NOTHING IS DELETED AND NOTHING EXPIRES ON A SCHEDULE.** `NothingIsDestroyed`.
"""

from .fixtures import MAIN, V12TestCase
from .harness import STORE, frappe


def _days_ago(days: int) -> str:
	"""A date relative to the harness clock rather than a literal.

	The double's "today" is fixed and is not the day this file was written, so a
	hard-coded 2026-08-01 is an incident in the FUTURE — which the controller
	correctly refuses, because discipline cannot be issued before the thing it is
	about happened. Relative dates keep the tests about the chain.
	"""
	return str(frappe.utils.add_days(frappe.utils.today(), -days))


def _days_ahead(days: int) -> str:
	return str(frappe.utils.add_days(frappe.utils.today(), days))


ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_discipline_record",
		"acknowledge_discipline_record",
		"get_discipline_record",
		"list_discipline_history",
		"get_discipline_report",
		"expire_discipline_record",
		"add_task_note",
		"list_task_notes",
		"attach_audio_note",
	)
}

WORKER = "HR-EMP-00001"
MANAGER = "HR-EMP-00009"


class DisciplineTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		STORE.seed(
			"Employee",
			[
				{"name": WORKER, "employee_name": "Ana Ramos", "company": MAIN, "status": "Active"},
				{"name": MANAGER, "employee_name": "Flor Diaz", "company": MAIN, "status": "Active"},
			],
		)

	def step(self, discipline_type="Verbal Warning", **overrides):
		payload = {
			"employee": WORKER,
			"discipline_type": discipline_type,
			"incident_date": _days_ago(30),
			"incident_description": "Arrived 40 minutes late without notice on 1 August.",
			"expected_improvement": "Clock in by 06:00 on every scheduled shift for 60 days.",
			"followup_date": _days_ahead(60),
			"company": MAIN,
			"issued_by": MANAGER,
		}
		payload.update(overrides)
		return self.tool_data("create_discipline_record", payload)

	def rows(self):
		return list(STORE.tables.get("Discipline Record", {}).values())


# ── 1. the chain links itself ───────────────────────────────────────────────
class TheChainIsFoundNotTyped(DisciplineTestCase):
	def test_the_first_step_names_no_predecessor(self):
		data = self.step()
		self.assertIsNone(data["prior_record"])
		self.assertEqual(data["step_number"], 1)

	def test_the_second_step_links_itself_to_the_first(self):
		first = self.step("Verbal Warning")
		second = self.step("Written Warning", incident_date=_days_ago(20))
		self.assertEqual(second["prior_record"], first["name"])
		self.assertEqual(second["step_number"], 2)

	def test_the_chain_reads_in_order(self):
		self.step("Verbal Warning")
		self.step("Written Warning", incident_date=_days_ago(20))
		self.step("Final Warning", incident_date=_days_ago(10))

		data = self.tool_data("list_discipline_history", {"employee": WORKER})
		self.assertEqual(
			[entry["discipline_type"] for entry in data["steps"]],
			["Verbal Warning", "Written Warning", "Final Warning"],
		)
		self.assertEqual(data["current_level"], "Final Warning")
		self.assertEqual(data["next_step_would_be"], "Suspension")

	def test_a_step_after_a_termination_is_refused(self):
		"""There is no chain past the end of employment."""
		self.step("Verbal Warning")
		self.step("Written Warning", incident_date=_days_ago(25))
		self.step("Final Warning", incident_date=_days_ago(22))
		self.step("Termination", incident_date=_days_ago(20), supersedes_note="repeated after final warning")

		error = self.tool_error(
			"create_discipline_record",
			{
				"employee": WORKER,
				"discipline_type": "Verbal Warning",
				"incident_description": "x",
				"expected_improvement": "y",
				"followup_date": _days_ahead(90),
			},
		)
		self.assertIn("no step after the end of employment", error)

	def test_a_first_step_above_verbal_carries_a_warning(self):
		data = self.step("Final Warning", supersedes_note="safety violation — not a progressive matter")
		self.assertTrue(any("FIRST recorded step" in w for w in data["warnings"]))

	def test_a_termination_on_a_short_chain_says_so(self):
		data = self.step("Termination", supersedes_note="gross misconduct")
		self.assertTrue(any("short chain is the chain" in w for w in data["warnings"]))

	def test_an_employee_who_is_not_on_the_register_is_refused(self):
		self.assertIn(
			"no Employee called",
			self.tool_error(
				"create_discipline_record",
				{
					"employee": "NOBODY",
					"discipline_type": "Verbal Warning",
					"incident_description": "x",
					"expected_improvement": "y",
					"followup_date": _days_ahead(90),
				},
			),
		)


# ── 2. a skip has to be explained ───────────────────────────────────────────
class AnUnexplainedJumpIsRefused(DisciplineTestCase):
	def test_two_rungs_at_once_needs_a_reason(self):
		self.step("Verbal Warning")
		error = self.tool_error(
			"create_discipline_record",
			{
				"employee": WORKER,
				"discipline_type": "Suspension",
				"incident_description": "x",
				"expected_improvement": "y",
				"followup_date": _days_ahead(90),
			},
		)
		self.assertIn("rungs up", error)
		self.assertIn("supersedes_note", error)

	def test_the_reason_is_accepted_and_recorded(self):
		self.step("Verbal Warning")
		data = self.step(
			"Suspension",
			incident_date=_days_ago(20),
			supersedes_note="operated the forklift without a licence — safety, not progression",
		)
		self.assertIn("forklift", data["escalation_note"])

	def test_one_rung_needs_no_explanation(self):
		self.step("Verbal Warning")
		data = self.step("Written Warning", incident_date=_days_ago(20))
		self.assertIsNone(data["escalation_note"])

	def test_the_same_rung_twice_needs_no_explanation(self):
		"""A second written warning for a second incident is ordinary."""
		self.step("Written Warning")
		data = self.step("Written Warning", incident_date=_days_ago(20))
		self.assertIsNone(data["escalation_note"])

	def test_a_step_down_needs_no_explanation(self):
		"""Somebody being generous never has to be defended."""
		self.step("Final Warning", supersedes_note="first step, safety")
		data = self.step("Verbal Warning", incident_date=_days_ago(20))
		self.assertIsNone(data["escalation_note"])


# ── 3. acknowledgement is either/or ─────────────────────────────────────────
class TheAcknowledgementIsEitherOr(DisciplineTestCase):
	def test_a_signature_records_it(self):
		record = self.step()["name"]
		data = self.tool_data(
			"acknowledge_discipline_record",
			{"record": record, "employee_signature": "/files/sig.png"},
		)
		self.assertTrue(data["employee_acknowledged"])
		self.assertEqual(data["outcome"], "acknowledged")

	def test_a_refusal_records_it_too(self):
		"""An employee is entitled to decline."""
		record = self.step()["name"]
		data = self.tool_data(
			"acknowledge_discipline_record",
			{"record": record, "declined_to_sign": True, "witnesses": "Flor Diaz"},
		)
		self.assertTrue(data["employee_declined_to_sign"])
		self.assertEqual(data["outcome"], "declined_to_sign")

	def test_a_refusal_with_no_witness_is_refused(self):
		"""Otherwise the file says the employer says they refused."""
		record = self.step()["name"]
		self.assertIn(
			"needs a witness",
			self.tool_error("acknowledge_discipline_record", {"record": record, "declined_to_sign": True}),
		)

	def test_an_acknowledgement_with_neither_is_refused(self):
		"""Silence presented as agreement is the one thing this may not contain."""
		record = self.step()["name"]
		self.assertIn(
			"silence presented as",
			self.tool_error("acknowledge_discipline_record", {"record": record}),
		)

	def test_the_doctype_refuses_a_contradiction_too(self):
		record = self.step()["name"]
		doc = STORE.get_raw("Discipline Record", record)
		doc["employee_acknowledged"] = 1
		doc["employee_declined_to_sign"] = 1
		import frappe

		with self.assertRaises(Exception):
			frappe.get_doc("Discipline Record", record).save()

	def test_the_employees_own_account_is_recorded(self):
		"""A chain that shows the employee was heard is materially stronger."""
		record = self.step()["name"]
		data = self.tool_data(
			"acknowledge_discipline_record",
			{
				"record": record,
				"employee_signature": "/files/sig.png",
				"employee_statement": "My ride broke down and I called the office.",
			},
		)
		self.assertIn("ride broke down", data["employee_statement"])


# ── 4. the gaps are the point ───────────────────────────────────────────────
class TheGapsAreThePoint(DisciplineTestCase):
	def test_an_unacknowledged_step_is_named(self):
		self.step()
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		gaps = [gap["gap"] for gap in data["gaps"]]
		self.assertIn("not_acknowledged", gaps)

	def test_the_gap_explains_why_it_matters(self):
		"""'Unacknowledged' means nothing to somebody who has not run a hearing."""
		self.step()
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		gap = next(gap for gap in data["gaps"] if gap["gap"] == "not_acknowledged")
		self.assertIn("note in a file rather than a step in a chain", gap["detail"])

	def test_a_missing_manager_signature_is_named(self):
		self.step()
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		self.assertIn("unsigned_by_manager", [gap["gap"] for gap in data["gaps"]])

	def test_a_missed_follow_up_is_named(self):
		# Issued in the past too: the controller refuses a follow-up dated before
		# the step was issued, which is right and is not what this test is about.
		self.step(issued_on=_days_ago(30), followup_date=_days_ago(5))
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		gap = next(gap for gap in data["gaps"] if gap["gap"] == "followup_missed")
		self.assertIn("process was theatre", gap["detail"])

	def test_a_witnessed_refusal_is_not_a_gap(self):
		record = self.step()["name"]
		self.tool_data(
			"acknowledge_discipline_record",
			{
				"record": record,
				"declined_to_sign": True,
				"witnesses": "Flor Diaz",
				"manager_signature": "/files/mgr.png",
			},
		)
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		self.assertNotIn("not_acknowledged", [gap["gap"] for gap in data["gaps"]])
		self.assertNotIn("refusal_unwitnessed", [gap["gap"] for gap in data["gaps"]])

	def test_a_complete_chain_reports_no_gaps(self):
		record = self.step(followup_date=_days_ahead(120))["name"]
		self.tool_data(
			"acknowledge_discipline_record",
			{
				"record": record,
				"employee_signature": "/files/sig.png",
				"manager_signature": "/files/mgr.png",
			},
		)
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		self.assertEqual(data["gaps"], [])
		self.assertIn("No gaps found", data["assessment"])

	def test_the_report_says_it_is_not_legal_advice(self):
		self.step()
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		self.assertIn("not legal advice", data["note"])
		self.assertIn("does not say whether any step was warranted", data["note"])

	def test_the_report_carries_the_timeline_and_the_narratives(self):
		self.step(narrative="Met in the shop office at 07:10. He acknowledged being late.")
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		self.assertEqual(len(data["timeline"]), 1)
		self.assertTrue(data["narratives"])

	def test_the_gaps_are_fixable_now_and_the_report_says_so(self):
		self.step()
		data = self.tool_data("get_discipline_report", {"employee": WORKER})
		self.assertIn("FIXABLE NOW AND NOT LATER", data["assessment"])


# ── 5. nothing is destroyed ─────────────────────────────────────────────────
class NothingIsDestroyed(DisciplineTestCase):
	def test_expiring_keeps_the_row(self):
		record = self.step()["name"]
		self.tool_data(
			"expire_discipline_record",
			{"record": record, "status": "Expired", "reason": "twelve-month look-back under our policy"},
		)
		self.assertEqual(STORE.get_raw("Discipline Record", record)["status"], "Expired")

	def test_expiring_needs_a_reason(self):
		record = self.step()["name"]
		self.assertIn(
			"reason",
			self.tool_error("expire_discipline_record", {"record": record, "status": "Expired"}),
		)

	def test_an_expired_step_drops_out_of_the_active_chain(self):
		first = self.step("Verbal Warning")["name"]
		self.tool_data(
			"expire_discipline_record", {"record": first, "status": "Expired", "reason": "aged out"}
		)
		second = self.step("Written Warning", incident_date=_days_ago(10))
		self.assertIsNone(second["prior_record"])

	def test_the_expired_step_is_still_in_the_history(self):
		"""A chain that had one deleted cannot explain the gap where it was."""
		record = self.step()["name"]
		self.tool_data(
			"expire_discipline_record", {"record": record, "status": "Expired", "reason": "aged out"}
		)
		data = self.tool_data("list_discipline_history", {"employee": WORKER})
		self.assertEqual(data["step_count"], 1)
		self.assertEqual(data["active_step_count"], 0)

	def test_the_status_change_is_written_into_the_narrative(self):
		record = self.step()["name"]
		self.tool_data(
			"expire_discipline_record",
			{"record": record, "status": "Rescinded", "reason": "withdrawn on review"},
		)
		notes = self.tool_data("list_task_notes", {"doctype": "Discipline Record", "name": record})["notes"]
		self.assertTrue(any("Rescinded" in note["narrative"] for note in notes))

	def test_expiring_twice_is_refused(self):
		record = self.step()["name"]
		self.tool_data(
			"expire_discipline_record", {"record": record, "status": "Expired", "reason": "aged out"}
		)
		self.assertIn(
			"already Expired",
			self.tool_error(
				"expire_discipline_record",
				{"record": record, "status": "Rescinded", "reason": "again"},
			),
		)

	def test_a_record_cannot_be_returned_to_active(self):
		record = self.step()["name"]
		self.assertIn(
			"never returns to Active",
			self.tool_error(
				"expire_discipline_record",
				{"record": record, "status": "Active", "reason": "changed my mind"},
			),
		)


# ── 6. what the doctype itself refuses ──────────────────────────────────────
class TheRecordRefusesToBeIndefensible(DisciplineTestCase):
	def test_no_expected_improvement_is_refused(self):
		error = self.tool_error(
			"create_discipline_record",
			{
				"employee": WORKER,
				"discipline_type": "Verbal Warning",
				"incident_description": "late",
				"followup_date": _days_ahead(90),
			},
		)
		self.assertIn("expected improvement is required", error.lower())

	def test_no_follow_up_date_is_refused(self):
		error = self.tool_error(
			"create_discipline_record",
			{
				"employee": WORKER,
				"discipline_type": "Verbal Warning",
				"incident_description": "late",
				"expected_improvement": "be on time",
			},
		)
		self.assertIn("follow-up date is required", error.lower())

	def test_discipline_dated_before_the_incident_is_refused(self):
		self.assertIn(
			"before the incident",
			self.tool_error(
				"create_discipline_record",
				{
					"employee": WORKER,
					"discipline_type": "Verbal Warning",
					"incident_date": _days_ago(5),
					"issued_on": _days_ago(20),
					"incident_description": "late",
					"expected_improvement": "be on time",
					"followup_date": _days_ahead(90),
				},
			),
		)

	def test_an_unknown_discipline_type_is_refused_with_the_list(self):
		error = self.tool_error(
			"create_discipline_record",
			{
				"employee": WORKER,
				"discipline_type": "Stern Look",
				"incident_description": "late",
				"expected_improvement": "be on time",
				"followup_date": _days_ahead(90),
			},
		)
		self.assertIn("Verbal Warning", error)
		self.assertIn("Termination", error)
