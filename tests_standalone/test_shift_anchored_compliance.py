# SPDX-License-Identifier: MIT
"""v0.64.0 — the crew envelope, and the join between a task and the shift it ran on.

THREE CLAIMS, AND EACH CLASS IN THIS FILE IS ONE OF THEM.

1. **THE CREW IS NOT ONE PERSON.** `TheCrewEnvelope`. The shift already carried
   `joined_at` and `left_at` per row and nothing read them as an exposure
   question. `get_shift_crew_timeline` computes every figure against the
   worker's OWN span, and the test that matters is the picker who arrived after
   the shift crossed its heat threshold: the shift's first crossing and theirs
   are different timestamps, the crew break called before they turned up is not
   care given to them, and the peak they stood in is not the peak the foreman
   stood in.

2. **A TASK KNOWS WHICH SHIFT IT RAN ON, AND ITS EVIDENCE GOES THERE.**
   `TheTaskShiftJoin`. A completion carries a point in time; a shift carries the
   period an exposure regime asks about. The link is settable at creation,
   dispatch, clock-in and completion, inferred at clock-in from an unambiguous
   open shift, and refused across companies — and the completion appends ONE
   event to the shift's own timeline carrying the signature and the weather AS
   IT STOOD AT OR BEFORE the work finishing.

3. **THE CALENDAR LOOKS AGAIN AT THE MOMENT THE WORLD CHANGED.**
   `TheNarrowedSweep`. `refresh_compliance_alerts` grew an `alert_types`
   allowlist with the same raised-nothing-dismissed-nothing promise its `regime`
   filter has, and `complete_farm_task` calls it for the rule that raised the
   task plus every rule that reads the register the completion wrote to. It is
   the SWEEP, called sooner — nothing here dismisses an alert by hand, which is
   the invariant `test_dispatch.py` guards from the other side.
"""

import frappe

from erpnext_mcp import shifts
from erpnext_mcp.services import weather

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import STORE

ON = {
	f"allow_{name}": 1
	for name in (
		"start_shift",
		"add_worker_to_shift",
		"remove_worker_from_shift",
		"log_shift_event",
		"log_shift_break",
		"end_shift",
		"list_shifts",
		"get_shift",
		"get_shift_crew_timeline",
		"get_weather_timeline",
		"create_farm_task",
		"assign_farm_task",
		"claim_farm_task",
		"start_farm_task",
		"complete_farm_task",
		"list_dispatch_board",
		"get_farm_task",
		"refresh_compliance_alerts",
	)
}

FOREMAN = "HR-EMP-00001"  # Ada Orchard, at MAIN — on from the start
WORKER = "HR-EMP-00002"  # Ben Packhouse, at MAIN — on from the start
LATE = "HR-EMP-00010"  # the picker who turns up after the crossing

SIGNATURE = "/files/ada-shift-signature.png"
GPS = "45.52,-122.68"

WALK = {"photos": True, "signature": True, "findings_text": True}
A_PHOTO = [{"file_url": "/files/north-wall.jpg", "evidence_type": "Photo", "caption": "north wall"}]


def at(hour: int, minute: int = 0) -> str:
	return f"{frappe.utils.today()} {hour:02d}:{minute:02d}:00"


class ShiftAnchoredTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		STORE.seed(
			"Employee",
			[
				{
					"name": LATE,
					"employee_name": "Ana Late",
					"status": "Active",
					"date_of_joining": "2026-06-01",
					"company": MAIN,
				}
			],
		)

	# -- helpers -------------------------------------------------------------
	def start(self, **overrides):
		payload = {
			"foreman": FOREMAN,
			"location": "Block 7 North",
			"shift_type": "Harvest",
			"farm_location_gps": GPS,
			"start_datetime": at(6),
			# THE FOREMAN IS ROSTERED EXPLICITLY. `start_shift` names them on the
			# shift without putting them on the crew — being answerable for a
			# record is not the same as being on it — and this suite's whole
			# subject is comparing one crew row's exposure against another's.
			"crew_employees": [FOREMAN, WORKER],
		}
		payload.update(overrides)
		return self.tool_data("start_shift", payload)["name"]

	def close(self, shift, **overrides):
		payload = {
			"shift": shift,
			"end_datetime": at(15),
			"supervisor_signature_file_token": SIGNATURE,
		}
		payload.update(overrides)
		return self.tool_data("end_shift", payload)

	def raw(self, name: str) -> dict:
		return dict(STORE.get_raw(shifts.DOCTYPE, name) or {})

	def events(self, name: str, event_type=None) -> list:
		rows = list(self.raw(name).get("compliance_events") or [])
		if event_type:
			rows = [row for row in rows if row.get("event_type") == event_type]
		return rows

	def reading(self, hour, temp=78.0, humidity=40.0, minute=0):
		return {
			"reading_datetime": at(hour, minute),
			"temp_f": temp,
			"heat_index_f": weather.heat_index_f(temp, humidity),
			"humidity_pct": humidity,
			"wind_speed_mph": 3.0,
			"wind_direction_deg": 180,
			"precipitation_mm": 0.0,
			"source": weather.SOURCE_CURRENT,
			"fetched_at": frappe.utils.now(),
		}

	def append(self, shift: str, *readings):
		return weather.append_readings(shift, list(readings))

	def a_hot_morning(self, events=(), remove=None, close=True):
		"""A shift that is cool at 07:00 and past the threshold from 10:00.

		The foreman and one worker are on from six. Ana joins at 11:30, which is
		AFTER the shift's first crossing — the whole point of the fixture.

		IT CLOSES BY DEFAULT, and that is not incidental. An OPEN shift's envelope
		runs to `now`, correctly: nobody's exposure period extends past the present
		moment. The harness's clock sits at breakfast time, so a fixture that
		stayed open would put every reading in this story in the future and every
		envelope would be empty — which would be the tool telling the truth about a
		day that had not happened yet. A closed shift is also the shape an
		inspector actually reads.
		"""
		shift = self.start()
		self.append(
			shift,
			self.reading(7, temp=64.0),
			self.reading(8, temp=70.0),
			self.reading(9, temp=76.0),
			self.reading(10, temp=88.0),
			self.reading(11, temp=93.0),
			self.reading(12, temp=97.0),
		)
		self.tool_data(
			"add_worker_to_shift", {"shift": shift, "employee": LATE, "joined_at": at(11, 30)}
		)
		for tool, payload in events:
			self.tool_data(tool, {"shift": shift, **payload})
		if remove:
			self.tool_data("remove_worker_from_shift", {"shift": shift, **remove})
		if close:
			self.close(shift)
		return shift

	def timeline(self, shift, **overrides):
		payload = {"shift": shift}
		payload.update(overrides)
		return self.tool_data("get_shift_crew_timeline", payload)

	def envelope(self, data, employee):
		return next(row for row in data["crew"] if row["employee"] == employee)


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheCrewEnvelope(ShiftAnchoredTestCase):
	"""Every figure against the worker's own span, never the shift's."""

	def test_each_crew_row_reports_its_own_span_and_hours(self):
		shift = self.a_hot_morning()
		data = self.timeline(shift)
		self.assertEqual(data["crew_size"], 3)

		ada = self.envelope(data, FOREMAN)
		self.assertEqual(ada["joined_at"], at(6))
		self.assertIsNone(ada["left_at"])

		ana = self.envelope(data, LATE)
		self.assertEqual(ana["joined_at"], at(11, 30))
		# Five and a half hours fewer on the clock than the foreman, and the
		# arithmetic is the wage record rather than a decoration on it.
		self.assertLess(ana["hours_present"], ada["hours_present"])

	def test_the_late_arrival_did_not_stand_in_the_peak_the_foreman_did(self):
		"""THE TEST THIS TOOL EXISTS FOR.

		The shift reached 88 °F at ten. Ana was not there. A heat record scoped to
		the crew says she was, and that record is read in an investigation.
		"""
		data = self.timeline(self.a_hot_morning())
		ada = self.envelope(data, FOREMAN)
		ana = self.envelope(data, LATE)

		self.assertEqual(ada["exposure"]["readings_in_span"], 6)
		self.assertEqual(ada["exposure"]["peak_temp_f"], 97.0)
		# Only the noon reading falls inside a span that began at 11:30.
		self.assertEqual(ana["exposure"]["readings_in_span"], 1)
		self.assertEqual(ana["exposure"]["peak_temp_f"], 97.0)
		self.assertEqual(ana["exposure"]["first_crossing_in_span"], at(12))
		self.assertEqual(ada["exposure"]["first_crossing_in_span"], at(10))

	def test_the_shifts_first_crossing_and_a_workers_own_are_different_facts(self):
		data = self.timeline(self.a_hot_morning())
		self.assertEqual(data["shift_first_crossing"], at(10))
		self.assertTrue(self.envelope(data, FOREMAN)["exposure"]["present_at_shift_first_crossing"])
		self.assertFalse(self.envelope(data, LATE)["exposure"]["present_at_shift_first_crossing"])
		self.assertEqual(data["arrived_after_the_first_crossing"], [LATE])
		self.assertIn("obligations run from the crossing", data["exposure_note"])

	def test_one_reading_above_the_threshold_brackets_zero_minutes_not_a_guess(self):
		"""A moment is not a duration, and nothing here interpolates one."""
		data = self.timeline(self.a_hot_morning())
		self.assertEqual(self.envelope(data, LATE)["exposure"]["minutes_bracketed_by_crossings"], 0.0)
		# Ten o'clock to noon, bracketed rather than summed.
		self.assertEqual(
			self.envelope(data, FOREMAN)["exposure"]["minutes_bracketed_by_crossings"], 120.0
		)

	def test_a_crew_break_called_before_somebody_arrived_is_not_care_given_to_them(self):
		"""Counting it would flatter the operation where an investigator checks."""
		shift = self.a_hot_morning(
			events=[
				(
					"log_shift_event",
					{
						"event_type": "Water Break",
						"event_datetime": at(9),
						"logged_by": FOREMAN,
						"description": "water called for the crew",
					},
				)
			]
		)
		data = self.timeline(shift)
		self.assertEqual(self.envelope(data, FOREMAN)["care_events_in_span"], 1)
		self.assertEqual(self.envelope(data, LATE)["care_events_in_span"], 0)

	def test_an_individual_event_naming_somebody_counts_whatever_the_clock_says(self):
		"""An observation ABOUT a person is about them, not about a window."""
		shift = self.a_hot_morning(
			events=[
				(
					"log_shift_break",
					{
						"break_kind": "Cool-Down",
						"started_at": at(7),
						"duration_minutes": 10,
						"applies_to": "Individual",
						"employee": LATE,
						"description": "cool-down given to Ana",
					},
				)
			]
		)
		data = self.timeline(shift)
		self.assertEqual(self.envelope(data, LATE)["care_events_in_span"], 1)
		# And it is NOT counted for anybody else, which is the other half.
		self.assertEqual(self.envelope(data, WORKER)["care_events_in_span"], 0)

	def test_the_sample_gap_is_reported_so_the_bracket_can_be_judged(self):
		data = self.timeline(self.a_hot_morning())
		self.assertEqual(data["sample_gap_minutes"], 60.0)

	def test_a_shift_with_no_timeline_reports_nulls_and_says_nobody_measured(self):
		data = self.timeline(self.start())
		ben = self.envelope(data, WORKER)
		self.assertIsNone(ben["exposure"]["peak_temp_f"])
		self.assertEqual(ben["exposure"]["readings_in_span"], 0)
		self.assertIsNone(data["shift_first_crossing"])
		self.assertIn("nobody measured", data["weather_note"])

	def test_it_narrows_to_one_person_and_refuses_somebody_never_rostered(self):
		shift = self.a_hot_morning()
		one = self.timeline(shift, employee=LATE)
		self.assertEqual([row["employee"] for row in one["crew"]], [LATE])

		message = self.tool_error("get_shift_crew_timeline", {"shift": shift, "employee": "HR-EMP-00099"})
		self.assertIn("is not on", message)
		self.assertIn("Nothing was changed", message)

	def test_a_worker_who_left_keeps_their_row_and_their_own_ceiling(self):
		"""remove_worker_from_shift sets left_at; the envelope stops there."""
		shift = self.a_hot_morning(remove={"employee": WORKER, "left_at": at(9, 30)})
		ben = self.envelope(self.timeline(shift), WORKER)
		self.assertEqual(ben["left_at"], at(9, 30))
		self.assertTrue(ben["left_early"])
		# Seven, eight and nine — and not the 88 °F at ten, which he missed.
		self.assertEqual(ben["exposure"]["readings_in_span"], 3)
		self.assertEqual(ben["exposure"]["peak_temp_f"], 76.0)
		self.assertIsNone(ben["exposure"]["first_crossing_in_span"])
		self.assertNotIn(WORKER, self.timeline(shift)["exposed_to_the_heat_threshold"])

	def test_breaks_are_null_without_a_policy_rather_than_zero(self):
		"""Nobody wrote a policy is not the same as nobody was owed a break."""
		data = self.timeline(self.a_hot_morning())
		self.assertIsNone(self.envelope(data, FOREMAN)["breaks"])
		self.assertEqual(data["short_of_their_break_entitlement"], [])


# ── 1b ──────────────────────────────────────────────────────────────────────
class TheBreakColumnsWereNeverFetched(ShiftAnchoredTestCase):
	"""Two bugs v0.64.0 found by trying to read a break through the envelope.

	Neither had a test and neither announced itself. `log_shift_break` passed the
	whole args dict to `as_float` as if it were a number, so every call raised and
	quoted the request payload back as the offending value. And `EVENT_FIELDS`
	never fetched the six break columns, so the rows that reached
	`breaks.worker_breaks` had no `applies_to` (an Individual break counted for
	the whole crew), no `duration_minutes` (every break was zero minutes long) and
	no `break_kind` (so `describe_event_row`'s break branch was unreachable).

	The pair is worth one class because the second hid the first: with the fields
	unfetched, nothing downstream could tell a broken break from an absent one.
	"""

	def test_a_break_with_a_duration_is_accepted_rather_than_refused(self):
		shift = self.start()
		data = self.tool_data(
			"log_shift_break",
			{
				"shift": shift,
				"break_kind": "Paid Rest",
				"started_at": at(9),
				"duration_minutes": 10,
			},
		)
		self.assertEqual(data["logged"]["duration_minutes"], 10.0)

	def test_a_non_numeric_duration_is_still_refused_and_names_only_the_field(self):
		"""The refusal must name the argument, not read back the whole payload."""
		shift = self.start()
		message = self.tool_error(
			"log_shift_break",
			{
				"shift": shift,
				"break_kind": "Paid Rest",
				"started_at": at(9),
				"duration_minutes": "ten",
			},
		)
		self.assertIn("duration_minutes must be a number", message)
		self.assertIn("'ten'", message)
		self.assertNotIn("break_kind", message)

	def test_a_break_row_reads_back_its_kind_scope_and_duration(self):
		shift = self.a_hot_morning(
			events=[
				(
					"log_shift_break",
					{
						"break_kind": "Paid Rest",
						"started_at": at(9),
						"duration_minutes": 10,
						"applies_to": "Individual",
						"employee": WORKER,
					},
				)
			]
		)
		row = self.events(shift, "Rest Period")[0]
		self.assertEqual(row["break_kind"], "Paid Rest")
		self.assertEqual(row["applies_to"], "Individual")
		self.assertEqual(row["employee"], WORKER)
		# The read path, not just the stored row: `get_shift` describes events
		# through `describe_event_row`, whose break branch was unreachable.
		described = self.tool_data("get_shift", {"name": shift})
		break_rows = [e for e in described["compliance_events"] if e.get("break_kind")]
		self.assertEqual(len(break_rows), 1)
		self.assertEqual(break_rows[0]["duration_minutes"], 10.0)
		self.assertEqual(break_rows[0]["applies_to"], "Individual")

	def test_one_persons_break_is_not_the_whole_crews_break(self):
		"""`applies_to` absent defaulted to Crew, which credits everybody."""
		shift = self.a_hot_morning(
			events=[
				(
					"log_shift_break",
					{
						"break_kind": "Paid Rest",
						"started_at": at(9),
						"duration_minutes": 10,
						"applies_to": "Individual",
						"employee": WORKER,
					},
				)
			]
		)
		data = self.timeline(shift)
		self.assertEqual(self.envelope(data, WORKER)["care_events_in_span"], 1)
		self.assertEqual(self.envelope(data, FOREMAN)["care_events_in_span"], 0)


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheTaskShiftJoin(ShiftAnchoredTestCase):
	"""A completion carries a point in time. The shift carries the period."""

	def a_task(self, **overrides):
		payload = {
			"task_name": "Spray block 7",
			"task_type": "Spray",
			"evidence_required": dict(WALK),
		}
		payload.update(overrides)
		return self.tool_data("create_farm_task", payload)

	def complete(self, task, worker=WORKER, **overrides):
		payload = {
			"task": task,
			"worker_id": worker,
			"evidence_files": list(A_PHOTO),
			"signature_file": "/files/sig.png",
			"findings_text": "",
			"completion_narrative": "sprayed it",
		}
		payload.update(overrides)
		return self.tool_data("complete_farm_task", payload)

	def test_a_task_can_be_raised_against_a_shift_and_the_assignment_inherits_it(self):
		shift = self.start()
		task = self.a_task(farm_shift=shift, assigned_to=WORKER)
		self.assertEqual(task["farm_shift"], shift)
		self.assertEqual(task["assignment"]["farm_shift"], shift)

	def test_a_shift_at_another_company_is_refused_by_name(self):
		shift = self.start()
		frappe.db.set_value(shifts.DOCTYPE, shift, "company", OTHER)
		message = self.tool_error(
			"create_farm_task",
			{
				"task_name": "Spray block 7",
				"task_type": "Spray",
				"evidence_required": dict(WALK),
				"company": MAIN,
				"farm_shift": shift,
			},
		)
		self.assertIn("is a shift at", message)
		self.assertIn("Nothing was changed", message)

	def test_a_shift_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"create_farm_task",
			{
				"task_name": "Spray block 7",
				"task_type": "Spray",
				"evidence_required": dict(WALK),
				"farm_shift": "SHIFT-2026-9999",
			},
		)
		self.assertIn("no Farm Shift called", message)

	def test_the_clock_in_infers_the_one_open_shift_the_worker_is_rostered_on(self):
		"""Nobody types a shift docname into a phone."""
		shift = self.start()
		task = self.a_task()
		self.tool_data("claim_farm_task", {"task": task["name"], "worker_id": WORKER})
		started = self.tool_data("start_farm_task", {"task": task["name"], "worker_id": WORKER})
		self.assertEqual(started["assignment"]["farm_shift"], shift)
		self.assertIn("anchored to shift", started["shift_note"])

	def test_two_open_shifts_naming_the_same_person_infer_nothing(self):
		"""Guessing would put the evidence on a crew that was somewhere else.

		THE SECOND CREW ROW IS WRITTEN ON THE DOCUMENT RATHER THAN THROUGH
		`start_shift`, and the reason is worth stating: the tools now REFUSE to
		roster somebody onto a second open shift, because two open shifts become
		two overlapping Attendance days when both close. That guard does not make
		this state impossible — a crew row added in the Desk, or data migrated in
		from whatever the operation used before, produces it without going near a
		tool — and a shift-inference that guessed as soon as the tools stopped
		producing the ambiguity would be wrong in exactly the case it was written
		for. So the state is built the way a site actually arrives at it.
		"""
		self.start()
		second = self.start(location="Block 9 South", start_datetime=at(7), crew_employees=[])
		doc = frappe.get_doc(shifts.DOCTYPE, second)
		doc.append("crew", {"employee": WORKER, "joined_at": at(7)})
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		task = self.a_task()
		self.tool_data("claim_farm_task", {"task": task["name"], "worker_id": WORKER})
		started = self.tool_data("start_farm_task", {"task": task["name"], "worker_id": WORKER})
		self.assertIsNone(started["assignment"]["farm_shift"])
		self.assertIn("anchored to NO SHIFT", started["shift_note"])

	def test_completing_puts_one_event_on_the_shifts_own_timeline(self):
		shift = self.a_hot_morning()
		task = self.a_task(farm_shift=shift)
		self.tool_data("claim_farm_task", {"task": task["name"], "worker_id": WORKER})
		done = self.complete(task["name"], completed_at=at(11, 52))

		self.assertTrue(done["shift_evidence"]["event_logged"])
		self.assertEqual(done["shift_evidence"]["farm_shift"], shift)

		rows = self.events(shift, "Task Completed")
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["producer_record_doctype"], "Farm Task Assignment")
		self.assertEqual(rows[0]["producer_record_name"], done["assignment"]["name"])
		# The signature is the attested half of the completion, so it is the file
		# worth carrying onto a record somebody else reads.
		self.assertEqual(rows[0]["evidence_file"], "/files/sig.png")
		self.assertIn("Spray block 7", rows[0]["description"])

	def test_the_weather_snapshot_is_the_reading_at_or_before_never_the_next_one(self):
		"""11:52 sits between the 11:00 and 12:00 readings. Reaching forward would
		stamp the record with a measurement that did not exist yet."""
		shift = self.a_hot_morning()
		task = self.a_task(farm_shift=shift)
		self.tool_data("claim_farm_task", {"task": task["name"], "worker_id": WORKER})
		self.complete(task["name"], completed_at=at(11, 52))
		row = self.events(shift, "Task Completed")[0]
		self.assertEqual(row["weather_snapshot_temp_f"], 93.0)

	def test_an_identical_resubmission_does_not_append_a_second_event(self):
		"""One completion is one entry. Two would be one afternoon's work twice."""
		shift = self.a_hot_morning()
		task = self.a_task(farm_shift=shift)
		self.tool_data("claim_farm_task", {"task": task["name"], "worker_id": WORKER})
		payload = {
			"task": task["name"],
			"worker_id": WORKER,
			"evidence_files": list(A_PHOTO),
			"signature_file": "/files/sig.png",
			"findings_text": "",
			"completion_narrative": "sprayed it",
			"completed_at": at(11, 52),
		}
		self.tool_data("complete_farm_task", dict(payload))
		replay = self.tool_data("complete_farm_task", dict(payload))

		self.assertTrue(replay["x_idempotent"])
		self.assertEqual(len(self.events(shift, "Task Completed")), 1)
		# The key is read back rather than re-written, so a client keeps one path.
		self.assertTrue(replay["shift_evidence"]["event_logged"])
		self.assertTrue(replay["shift_evidence"]["replayed"])
		self.assertIsNone(replay["compliance_evaluation"])

	def test_an_unanchored_completion_says_so_rather_than_going_quiet(self):
		task = self.a_task()
		self.tool_data("claim_farm_task", {"task": task["name"], "worker_id": WORKER})
		done = self.complete(task["name"])
		self.assertIsNone(done["shift_evidence"])
		self.assertIn("anchored to NO SHIFT", done["shift_note"])

	def test_the_board_narrows_to_one_shift_and_counts_what_is_anchored_to_none(self):
		shift = self.start()
		self.a_task(farm_shift=shift)
		self.a_task(task_name="Desk work")

		whole = self.tool_data("list_dispatch_board", {})
		self.assertEqual(len(whole["not_anchored_to_a_shift"]), 1)
		self.assertEqual(list(whole["by_shift"]), [shift])
		self.assertIn("name no shift", whole["shift_note"])

		narrowed = self.tool_data("list_dispatch_board", {"farm_shift": shift})
		self.assertEqual(narrowed["count"], 1)
		self.assertEqual(narrowed["farm_shift"], shift)
		self.assertEqual(narrowed["not_anchored_to_a_shift"], [])

	def test_the_shift_reads_back_the_work_still_open_on_its_crew(self):
		shift = self.start()
		open_task = self.a_task(farm_shift=shift)
		other = self.a_task(task_name="Second job", farm_shift=shift)
		self.tool_data("claim_farm_task", {"task": other["name"], "worker_id": WORKER})
		self.complete(other["name"])

		data = self.tool_data("get_shift", {"name": shift})
		self.assertEqual(data["farm_tasks"]["total"], 2)
		self.assertEqual(data["farm_tasks"]["completed"], 1)
		self.assertEqual([row["name"] for row in data["farm_tasks"]["open"]], [open_task["name"]])


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheNarrowedSweep(ShiftAnchoredTestCase):
	"""`alert_types` raises nothing and dismisses nothing outside its allowlist."""

	def test_a_named_rule_runs_and_every_other_is_skipped_untouched(self):
		from erpnext_mcp.alerts import base as alerts_base

		report = alerts_base.refresh_compliance_alerts(
			company=MAIN, alert_types=["housing_inspection_overdue"]
		)
		self.assertEqual(report["alert_types"], ["housing_inspection_overdue"])
		skipped = {entry["alert_type"] for entry in report["rules_skipped"]}
		self.assertNotIn("housing_inspection_overdue", skipped)
		self.assertTrue(skipped)
		for entry in report["rules_skipped"]:
			self.assertIn("DISMISSED NOTHING", entry["reason"])
		# A skipped rule did not run, and the report must not say it did.
		self.assertFalse(skipped & set(report["rules_run"]))

	def test_a_rule_name_this_site_does_not_have_is_reported_not_raised(self):
		"""A rule renamed since a task was raised must not fail a filed completion."""
		from erpnext_mcp.alerts import base as alerts_base

		report = alerts_base.refresh_compliance_alerts(company=MAIN, alert_types=["no_such_rule"])
		self.assertEqual(report["alert_types_not_on_this_site"], ["no_such_rule"])
		self.assertEqual(report["created"], 0)
		self.assertEqual(report["auto_dismissed"], 0)
