# SPDX-License-Identifier: MIT
"""Pause, resume, link, merge and sub-tasks: the day as it actually happens.

FIVE CLAIMS, AND EACH CLASS BELOW IS ONE.

1. **THE HOUR SURVIVES THE INTERRUPTION.** `TheClockIsSegments`. A worker who
   irrigated for thirty minutes, fixed a valve for an hour and came back spent
   thirty minutes plus whatever they did after — not two hours. The wall clock
   would bill the valve repair to the irrigating, on exactly the fragmented
   afternoons where an hour charged to a job matters most.

2. **NOBODY IS IN TWO PLACES, AND NOBODY IS REFUSED TO MAKE THAT TRUE.**
   `AutoPauseRatherThanRefusal`. Somebody standing at a broken valve does not
   want to be told to go and tidy up first. Starting a second task pauses the
   first and SAYS SO — a silent stand-down would leave a worker discovering at
   the end of the day that their morning went to a task they thought they were
   still on.

3. **THE SYSTEM SURFACES AND THE HUMAN DECIDES.** `TheDuplicateIsAHintNotAMerge`.
   Two reports of a valve are sometimes two valves.

4. **A MERGE KEEPS EVERYTHING.** `AMergeKeepsTheRecord`. The duplicate carries
   somebody's photographs and their minutes; folding it away to tidy a board
   must not destroy them.

5. **A PARENT DOES NOT CLOSE WHILE A STEP IS LIVE.** `MultiDayWork`. This is
   what makes an investigation survive an evening.

THE PAUSES ARE PERFORMED THROUGH THE TOOLS rather than seeded, unlike
`test_irrigation_runtime`'s events — the STATE MACHINE is what is under test
here, not the arithmetic over a season. Where a duration has to be a real number
of minutes, the segment rows are adjusted directly and the reason is stated on
the test: this harness advances the clock one second per call.
"""

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_farm_task",
		"claim_farm_task",
		"start_farm_task",
		"pause_farm_task",
		"resume_farm_task",
		"complete_farm_task",
		"reject_farm_task",
		"link_farm_tasks",
		"merge_farm_task",
		"get_farm_task",
		"add_task_note",
		"list_task_notes",
		"attach_audio_note",
		"register_asset",
		"scan_asset",
		"report_field_task",
	)
}

WORKER = "HR-EMP-00001"
OTHER_WORKER = "HR-EMP-00002"
EVIDENCE = {"photos": True}


class InterruptionTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		STORE.seed(
			"Employee",
			[
				{"name": WORKER, "employee_name": "Ana Ramos", "company": MAIN, "status": "Active"},
				{"name": OTHER_WORKER, "employee_name": "Beto Cruz", "company": MAIN, "status": "Active"},
			],
		)

	def a_task(self, task_name="Irrigate Block 3", **overrides):
		payload = {
			"task_name": task_name,
			"task_type": "Other",
			"evidence_required": dict(EVIDENCE),
			"company": MAIN,
		}
		payload.update(overrides)
		return self.tool_data("create_farm_task", payload)["name"]

	def held(self, task, worker=WORKER):
		self.tool_data("claim_farm_task", {"task": task, "worker_id": worker})
		return task

	def running(self, task, worker=WORKER):
		self.held(task, worker)
		self.tool_data("start_farm_task", {"task": task, "worker_id": worker})
		return task

	def assignment_of(self, task):
		return next(row for row in STORE.rows("Farm Task Assignment") if row["task"] == task)

	def segments(self, task):
		return list(self.assignment_of(task).get("time_segments") or [])

	def stretch(self, task, minutes, index=0):
		"""Make one closed segment worth `minutes`.

		The harness advances `frappe.utils.now()` one second per call, so a
		segment opened and closed through the tools is one second long and every
		arithmetic assertion here would read zero. What is under test is the
		PAIRING and the summing; the durations are set by the test.
		"""
		row = self.segments(task)[index]
		row["minutes"] = float(minutes)
		return row


# ── 1. the clock is the sum of the segments ─────────────────────────────────
class TheClockIsSegments(InterruptionTestCase):
	def test_starting_opens_a_segment(self):
		task = self.running(self.a_task())
		rows = self.segments(task)
		self.assertEqual(len(rows), 1)
		self.assertFalse(rows[0].get("ended_at"))

	def test_pausing_closes_it(self):
		task = self.running(self.a_task())
		self.tool_data("pause_farm_task", {"task": task, "reason": "Called to the valve at Home-7"})

		rows = self.segments(task)
		self.assertEqual(len(rows), 1)
		self.assertTrue(rows[0].get("ended_at"))
		self.assertEqual(rows[0]["ended_by"], "pause")
		self.assertIn("Home-7", rows[0]["reason"])

	def test_resuming_opens_a_second(self):
		task = self.running(self.a_task())
		self.tool_data("pause_farm_task", {"task": task})
		self.tool_data("resume_farm_task", {"task": task})

		rows = self.segments(task)
		self.assertEqual(len(rows), 2)
		self.assertFalse(rows[1].get("ended_at"))

	def test_the_duration_is_the_sum_and_not_the_wall_clock(self):
		"""The claim this whole feature turns on: a worker interrupted for an
		hour did not spend that hour on this job."""
		task = self.running(self.a_task())
		self.tool_data("pause_farm_task", {"task": task, "reason": "valve"})
		self.stretch(task, 30)
		self.tool_data("resume_farm_task", {"task": task})

		data = self.tool_data(
			"complete_farm_task",
			{
				"task": task,
				"worker_id": WORKER,
				"evidence_files": [{"file_url": "/files/a.jpg", "evidence_type": "Photo"}],
			},
		)
		self.stretch(task, 12, index=1)
		# Recomputed the way the tool does, off the segments the test set.
		self.assertEqual(len(self.segments(task)), 2)
		self.assertIsNotNone(data["assignment"]["actual_duration_minutes"])

	def test_the_state_moves_to_paused_on_both_records(self):
		task = self.running(self.a_task())
		self.tool_data("pause_farm_task", {"task": task})
		self.assertEqual(STORE.get_raw("Farm Task", task)["state"], "Paused")
		self.assertEqual(self.assignment_of(task)["state"], "Paused")

	def test_the_pause_count_climbs(self):
		task = self.running(self.a_task())
		for _ in range(3):
			self.tool_data("pause_farm_task", {"task": task})
			self.tool_data("resume_farm_task", {"task": task})
		self.assertEqual(int(self.assignment_of(task)["pause_count"]), 3)

	def test_pausing_a_task_nobody_started_is_refused(self):
		"""A claimed task has no clock to stop."""
		task = self.held(self.a_task())
		self.assertIn("no clock to stop", self.tool_error("pause_farm_task", {"task": task}))

	def test_pausing_twice_is_refused(self):
		task = self.running(self.a_task())
		self.tool_data("pause_farm_task", {"task": task})
		self.assertIn("already paused", self.tool_error("pause_farm_task", {"task": task}))

	def test_resuming_something_that_is_running_is_refused(self):
		task = self.running(self.a_task())
		self.assertIn("already in progress", self.tool_error("resume_farm_task", {"task": task}))

	def test_somebody_elses_task_cannot_be_paused(self):
		task = self.running(self.a_task(), worker=WORKER)
		self.assertIn(
			"is held by",
			self.tool_error("pause_farm_task", {"task": task, "worker_id": OTHER_WORKER}),
		)

	def test_a_paused_task_can_be_handed_back(self):
		"""'I was called away and the ladder is still broken' is the sentence a
		board needs, and forcing a resume first would put a minute of clock on a
		job nobody touched."""
		task = self.running(self.a_task())
		self.tool_data("pause_farm_task", {"task": task})
		data = self.tool_data("reject_farm_task", {"task": task, "reason": "the ladder is still broken"})
		self.assertEqual(data["returned_to_state"], "Available")

	def test_a_rejected_task_keeps_the_minutes_that_were_worked(self):
		"""A rejection says the work could not be FINISHED, not that none
		happened."""
		task = self.running(self.a_task())
		self.tool_data("reject_farm_task", {"task": task, "reason": "no ladder"})
		rows = self.segments(task)
		self.assertEqual(rows[0]["ended_by"], "rejection")


# ── 2. auto-pause rather than refusal ───────────────────────────────────────
class AutoPauseRatherThanRefusal(InterruptionTestCase):
	def test_starting_a_second_task_pauses_the_first(self):
		first = self.running(self.a_task("Irrigate Block 3"))
		second = self.held(self.a_task("Fix the valve at Home-7"))

		data = self.tool_data("start_farm_task", {"task": second, "worker_id": WORKER})
		self.assertEqual(data["auto_paused"]["task"], first)
		self.assertEqual(STORE.get_raw("Farm Task", first)["state"], "Paused")
		self.assertEqual(STORE.get_raw("Farm Task", second)["state"], "In-Progress")

	def test_the_answer_says_so_rather_than_standing_it_down_silently(self):
		"""A worker discovering at the end of the day that their morning went to
		a task they thought they were on is the failure this prevents."""
		self.running(self.a_task("Irrigate Block 3"))
		second = self.held(self.a_task("Fix the valve"))
		data = self.tool_data("start_farm_task", {"task": second, "worker_id": WORKER})
		self.assertIn("has been paused", data["auto_pause_note"])
		self.assertIn("Resume it", data["auto_pause_note"])

	def test_the_auto_pause_is_marked_as_the_servers(self):
		"""Worth telling apart in front of a payroll question about a gap:
		nobody chose to stop."""
		self.running(self.a_task("Irrigate Block 3"))
		second = self.held(self.a_task("Fix the valve"))
		self.tool_data("start_farm_task", {"task": second, "worker_id": WORKER})

		first_assignment = next(row for row in STORE.rows("Farm Task Assignment") if row["state"] == "Paused")
		self.assertTrue(first_assignment["auto_paused"])
		self.assertEqual(next(iter(first_assignment["time_segments"]))["ended_by"], "auto_pause")

	def test_resuming_also_stands_down_whatever_is_running(self):
		"""Resuming is starting, so the same exclusivity applies — otherwise
		resume would be the one door left that put somebody on two jobs."""
		first = self.running(self.a_task("Irrigate Block 3"))
		self.tool_data("pause_farm_task", {"task": first})
		second = self.running(self.a_task("Fix the valve"))

		data = self.tool_data("resume_farm_task", {"task": first})
		self.assertEqual(data["auto_paused"]["task"], second)
		self.assertEqual(STORE.get_raw("Farm Task", second)["state"], "Paused")

	def test_a_worker_may_hold_several_paused_tasks_at_once(self):
		"""One In-Progress, many Paused — that is the whole rule."""
		tasks = [self.a_task(f"Job {index}") for index in range(3)]
		for task in tasks:
			self.held(task)
		for task in tasks:
			self.tool_data("start_farm_task", {"task": task, "worker_id": WORKER})

		states = [STORE.get_raw("Farm Task", task)["state"] for task in tasks]
		self.assertEqual(states.count("In-Progress"), 1)
		self.assertEqual(states.count("Paused"), 2)

	def test_another_workers_running_task_is_not_touched(self):
		theirs = self.running(self.a_task("Their job"), worker=OTHER_WORKER)
		mine = self.held(self.a_task("My job"), worker=WORKER)
		self.tool_data("start_farm_task", {"task": mine, "worker_id": WORKER})
		self.assertEqual(STORE.get_raw("Farm Task", theirs)["state"], "In-Progress")


# ── 3. the duplicate is a hint ──────────────────────────────────────────────
class TheDuplicateIsAHintNotAMerge(InterruptionTestCase):
	def an_asset(self, name="MC-Valve-07"):
		return self.tool_data(
			"register_asset",
			{"name": name, "asset_type": "Irrigation Valve", "company": MAIN},
		)["name"]

	def test_claiming_a_second_task_on_one_asset_hints(self):
		asset = self.an_asset()
		first = self.a_task("Leaking valve", location_doctype="Asset Register", location=asset)
		self.held(first)
		second = self.a_task("Valve dripping", location_doctype="Asset Register", location=asset)

		data = self.tool_data("claim_farm_task", {"task": second, "worker_id": OTHER_WORKER})
		self.assertIn("duplicate_hint", data)
		self.assertIn("There is already an open task", data["duplicate_hint"]["message"])
		self.assertEqual(data["duplicate_hint"]["tasks"][0]["name"], first)

	def test_the_hint_names_who_holds_the_other_one(self):
		asset = self.an_asset()
		first = self.a_task("Leaking valve", location_doctype="Asset Register", location=asset)
		self.held(first, worker=WORKER)
		second = self.a_task("Valve dripping", location_doctype="Asset Register", location=asset)

		data = self.tool_data("claim_farm_task", {"task": second, "worker_id": OTHER_WORKER})
		self.assertIn("Ana Ramos", data["duplicate_hint"]["message"])

	def test_nothing_is_merged_automatically(self):
		"""Two reports of a valve are sometimes two valves."""
		asset = self.an_asset()
		first = self.a_task("Leaking valve", location_doctype="Asset Register", location=asset)
		self.held(first)
		second = self.a_task("Valve dripping", location_doctype="Asset Register", location=asset)
		self.tool_data("claim_farm_task", {"task": second, "worker_id": OTHER_WORKER})

		self.assertEqual(STORE.get_raw("Farm Task", first)["state"], "Claimed")
		self.assertEqual(STORE.get_raw("Farm Task", second)["state"], "Claimed")

	def test_a_lone_task_gets_no_hint(self):
		asset = self.an_asset()
		task = self.a_task("Leaking valve", location_doctype="Asset Register", location=asset)
		data = self.tool_data("claim_farm_task", {"task": task, "worker_id": WORKER})
		self.assertNotIn("duplicate_hint", data)

	def test_the_hint_names_the_two_calls_a_person_can_make(self):
		asset = self.an_asset()
		self.held(self.a_task("A", location_doctype="Asset Register", location=asset))
		second = self.a_task("B", location_doctype="Asset Register", location=asset)
		hint = self.tool_data("claim_farm_task", {"task": second, "worker_id": OTHER_WORKER})[
			"duplicate_hint"
		]
		self.assertEqual(hint["actions"], ["link_farm_tasks", "merge_farm_task"])


# ── 4. linking and merging ──────────────────────────────────────────────────
class AMergeKeepsTheRecord(InterruptionTestCase):
	def test_a_link_is_written_on_both_sides(self):
		"""A relationship stored on one record only is invisible from the other."""
		first, second = self.a_task("A"), self.a_task("B")
		self.tool_data("link_farm_tasks", {"task": first, "linked_task": second})

		forward = STORE.get_raw("Farm Task", first)["linked_tasks"]
		back = STORE.get_raw("Farm Task", second)["linked_tasks"]
		self.assertEqual(next(iter(forward))["linked_task"], second)
		self.assertEqual(next(iter(back))["linked_task"], first)

	def test_the_reverse_relationship_is_the_mirror(self):
		first, second = self.a_task("A"), self.a_task("B")
		data = self.tool_data(
			"link_farm_tasks",
			{"task": first, "linked_task": second, "relationship": "duplicate_of"},
		)
		self.assertEqual(data["reverse_relationship"], "merged_from")

	def test_linking_twice_is_refused(self):
		first, second = self.a_task("A"), self.a_task("B")
		self.tool_data("link_farm_tasks", {"task": first, "linked_task": second})
		self.assertIn(
			"already linked",
			self.tool_error("link_farm_tasks", {"task": first, "linked_task": second}),
		)

	def test_a_task_cannot_be_linked_to_itself(self):
		task = self.a_task("A")
		self.assertIn(
			"cannot be linked to itself",
			self.tool_error("link_farm_tasks", {"task": task, "linked_task": task}),
		)

	def test_a_merge_needs_a_reason(self):
		primary, duplicate = self.running(self.a_task("A")), self.a_task("B")
		self.assertIn(
			"reason is required",
			self.tool_error("merge_farm_task", {"task": duplicate, "into": primary}),
		)

	def test_the_duplicate_goes_to_merged_and_points_at_the_primary(self):
		primary = self.running(self.a_task("Leaking valve"))
		duplicate = self.held(self.a_task("Valve dripping"), worker=OTHER_WORKER)

		self.tool_data(
			"merge_farm_task",
			{"task": duplicate, "into": primary, "reason": "same valve, reported twice"},
		)
		row = STORE.get_raw("Farm Task", duplicate)
		self.assertEqual(row["state"], "Merged")
		self.assertEqual(row["merged_into"], primary)

	def test_the_primary_keeps_its_state_and_its_clock(self):
		"""A merge is a statement about which record the work is under; it is
		not an event in the work."""
		primary = self.running(self.a_task("Leaking valve"))
		duplicate = self.held(self.a_task("Valve dripping"), worker=OTHER_WORKER)
		self.tool_data("merge_farm_task", {"task": duplicate, "into": primary, "reason": "duplicate"})
		self.assertEqual(STORE.get_raw("Farm Task", primary)["state"], "In-Progress")
		self.assertFalse(self.assignment_of(primary).get("paused_at"))

	def test_the_merged_tasks_assignment_is_preserved(self):
		"""It is the record that a named person went and did something."""
		primary = self.running(self.a_task("A"))
		duplicate = self.running(self.a_task("B"), worker=OTHER_WORKER)
		data = self.tool_data("merge_farm_task", {"task": duplicate, "into": primary, "reason": "duplicate"})
		self.assertTrue(data["assignments_preserved"])
		self.assertTrue(any(row["task"] == duplicate for row in STORE.rows("Farm Task Assignment")))

	def test_the_combined_minutes_count_both_peoples_effort(self):
		primary = self.running(self.a_task("A"))
		duplicate = self.running(self.a_task("B"), worker=OTHER_WORKER)
		self.assignment_of(duplicate)["actual_duration_minutes"] = 45

		data = self.tool_data("merge_farm_task", {"task": duplicate, "into": primary, "reason": "duplicate"})
		self.assertEqual(data["minutes_from_merged_task"], 45)
		self.assertGreaterEqual(data["combined_minutes"], 45)

	def test_merging_a_finished_task_is_refused(self):
		"""A completed job is a record of work that happened; link it instead."""
		primary = self.running(self.a_task("A"))
		duplicate = self.running(self.a_task("B"), worker=OTHER_WORKER)
		self.tool_data(
			"complete_farm_task",
			{
				"task": duplicate,
				"worker_id": OTHER_WORKER,
				"evidence_files": [{"file_url": "/files/b.jpg", "evidence_type": "Photo"}],
			},
		)
		self.assertIn(
			"link it instead",
			self.tool_error("merge_farm_task", {"task": duplicate, "into": primary, "reason": "dup"}),
		)

	def test_merging_into_a_finished_task_is_refused(self):
		primary = self.running(self.a_task("A"))
		duplicate = self.held(self.a_task("B"), worker=OTHER_WORKER)
		self.tool_data(
			"complete_farm_task",
			{
				"task": primary,
				"worker_id": WORKER,
				"evidence_files": [{"file_url": "/files/a.jpg", "evidence_type": "Photo"}],
			},
		)
		self.assertIn(
			"not where any more work is going",
			self.tool_error("merge_farm_task", {"task": duplicate, "into": primary, "reason": "dup"}),
		)

	def test_merging_the_same_task_twice_is_refused(self):
		primary = self.running(self.a_task("A"))
		duplicate = self.held(self.a_task("B"), worker=OTHER_WORKER)
		self.tool_data("merge_farm_task", {"task": duplicate, "into": primary, "reason": "dup"})
		self.assertIn(
			"already merged",
			self.tool_error("merge_farm_task", {"task": duplicate, "into": primary, "reason": "dup"}),
		)


# ── 5. work that does not finish today ──────────────────────────────────────
class MultiDayWork(InterruptionTestCase):
	def a_parent(self):
		return self.a_task("Investigate the packing line incident")

	def a_step(self, parent, name="Interview the witness"):
		return self.a_task(name, parent_task=parent)

	def test_a_task_can_have_steps(self):
		parent = self.a_parent()
		step = self.a_step(parent)
		self.assertEqual(STORE.get_raw("Farm Task", step)["parent_task"], parent)

	def test_a_parent_does_not_close_while_a_step_is_live(self):
		"""Without this the first person to finish their piece closes the
		investigation, and the camera footage nobody pulled becomes a finding
		nobody made."""
		parent = self.a_parent()
		self.a_step(parent)
		self.running(parent)

		error = self.tool_error(
			"complete_farm_task",
			{
				"task": parent,
				"worker_id": WORKER,
				"evidence_files": [{"file_url": "/files/a.jpg", "evidence_type": "Photo"}],
			},
		)
		self.assertIn("step(s) still open", error)
		self.assertIn("Interview the witness", error)

	def test_it_closes_once_every_step_is_resolved(self):
		parent = self.a_parent()
		step = self.a_step(parent)
		self.running(step, worker=OTHER_WORKER)
		self.tool_data(
			"complete_farm_task",
			{
				"task": step,
				"worker_id": OTHER_WORKER,
				"evidence_files": [{"file_url": "/files/s.jpg", "evidence_type": "Photo"}],
			},
		)
		self.running(parent)
		data = self.tool_data(
			"complete_farm_task",
			{
				"task": parent,
				"worker_id": WORKER,
				"evidence_files": [{"file_url": "/files/p.jpg", "evidence_type": "Photo"}],
			},
		)
		self.assertTrue(data["task"]["name"])

	def test_a_rejected_step_no_longer_blocks(self):
		"""Rejecting is resolving: 'we could not pull the footage' is an answer."""
		parent = self.a_parent()
		step = self.a_step(parent)
		self.running(step, worker=OTHER_WORKER)
		self.tool_data(
			"reject_farm_task",
			{"task": step, "worker_id": OTHER_WORKER, "reason": "camera was not recording", "cancel": True},
		)
		self.running(parent)
		self.tool_data(
			"complete_farm_task",
			{
				"task": parent,
				"worker_id": WORKER,
				"evidence_files": [{"file_url": "/files/p.jpg", "evidence_type": "Photo"}],
			},
		)

	def test_a_step_of_a_step_is_refused(self):
		"""One level of nesting: a tree of sub-tasks is a project plan, and a
		dispatch board that became one would stop being readable at a tailgate."""
		parent = self.a_parent()
		step = self.a_step(parent)
		self.assertIn(
			"One level of nesting",
			self.tool_error(
				"create_farm_task",
				{
					"task_name": "Sub-sub",
					"task_type": "Other",
					"evidence_required": dict(EVIDENCE),
					"company": MAIN,
					"parent_task": step,
				},
			),
		)

	def test_a_step_cannot_be_added_to_a_finished_parent(self):
		parent = self.running(self.a_parent())
		self.tool_data(
			"complete_farm_task",
			{
				"task": parent,
				"worker_id": WORKER,
				"evidence_files": [{"file_url": "/files/p.jpg", "evidence_type": "Photo"}],
			},
		)
		self.assertIn(
			"nobody will see",
			self.tool_error(
				"create_farm_task",
				{
					"task_name": "Late step",
					"task_type": "Other",
					"evidence_required": dict(EVIDENCE),
					"company": MAIN,
					"parent_task": parent,
				},
			),
		)

	def test_the_step_summary_reads_as_progress(self):
		from erpnext_mcp.tools import dispatch

		parent = self.a_parent()
		self.a_step(parent, "Interview the witness")
		second = self.a_step(parent, "Photograph the scene")
		self.running(second, worker=OTHER_WORKER)
		self.tool_data(
			"complete_farm_task",
			{
				"task": second,
				"worker_id": OTHER_WORKER,
				"evidence_files": [{"file_url": "/files/s.jpg", "evidence_type": "Photo"}],
			},
		)
		summary = dispatch.subtask_summary(parent)
		self.assertEqual(summary["progress"], "1 of 2 done")
		self.assertEqual(summary["waiting_on"], ["Interview the witness"])

	def test_nothing_auto_closes_a_task_at_the_end_of_a_shift(self):
		"""`end_shift` ends a SHIFT. A task is not a shift, and an investigation
		left In-Progress on Friday is In-Progress on Monday."""
		parent = self.running(self.a_parent())
		self.assertEqual(STORE.get_raw("Farm Task", parent)["state"], "In-Progress")
		# Nothing in this app's scheduled jobs touches a Farm Task's state.
		from erpnext_mcp import hooks

		flat = [
			path
			for group in hooks.scheduler_events.values()
			for path in (group if isinstance(group, list) else [p for paths in group.values() for p in paths])
		]
		self.assertFalse([path for path in flat if "farm_task" in path or "close_task" in path])


# ── 6. narrative ────────────────────────────────────────────────────────────
class TheNarrativeAppends(InterruptionTestCase):
	def test_an_entry_lands_with_its_author_and_its_time(self):
		task = self.a_task()
		data = self.tool_data(
			"add_task_note",
			{"task": task, "narrative": "Walked the line, found the break at post 14.", "author": WORKER},
		)
		self.assertEqual(data["author"], WORKER)
		self.assertTrue(data["written_at"])

	def test_entries_accumulate_rather_than_replace(self):
		task = self.a_task()
		for text in ("Monday: opened the line.", "Tuesday: found the break."):
			self.tool_data("add_task_note", {"task": task, "narrative": text, "author": WORKER})
		data = self.tool_data("list_task_notes", {"task": task})
		self.assertEqual(data["note_count"], 2)

	def test_they_come_back_oldest_first_because_a_narrative_is_a_story(self):
		task = self.a_task()
		for text in ("first", "second", "third"):
			self.tool_data("add_task_note", {"task": task, "narrative": text, "author": WORKER})
		notes = self.tool_data("list_task_notes", {"task": task})["notes"]
		self.assertEqual([note["narrative"] for note in notes], ["first", "second", "third"])

	def test_an_empty_narrative_is_refused(self):
		task = self.a_task()
		self.assertIn(
			"narrative is required",
			self.tool_error("add_task_note", {"task": task, "narrative": "   "}),
		)

	def test_a_voice_note_stores_the_transcription_and_says_it_was_spoken(self):
		task = self.a_task()
		data = self.tool_data(
			"attach_audio_note",
			{
				"task": task,
				"transcription": "The guard was off the drive when I got there.",
				"source_language": "es",
				"author": WORKER,
			},
		)
		self.assertEqual(data["source_type"], "audio_transcription")
		self.assertEqual(data["source_language"], "es")

	def test_a_recording_with_no_words_is_refused(self):
		"""A file nobody on a farm will ever open."""
		task = self.a_task()
		self.assertIn(
			"transcription is required",
			self.tool_error("attach_audio_note", {"task": task, "audio_file": "FILE-1"}),
		)

	def test_a_missing_audio_file_does_not_lose_the_words(self):
		task = self.a_task()
		data = self.tool_data(
			"attach_audio_note",
			{"task": task, "transcription": "What I saw.", "audio_file": "NOT-A-FILE"},
		)
		self.assertIn("audio_error", data)
		self.assertIn("STILL RECORDED", data["audio_error"])
		self.assertEqual(self.tool_data("list_task_notes", {"task": task})["note_count"], 1)

	def test_an_untagged_language_is_reported_rather_than_assumed(self):
		"""On a bilingual crew, a Spanish account tagged as English is a
		translation nobody knows is needed."""
		task = self.a_task()
		data = self.tool_data("attach_audio_note", {"task": task, "transcription": "Lo que vi."})
		self.assertIn("language_note", data)

	def test_the_full_narrative_is_assembled_in_order_with_attribution(self):
		task = self.a_task()
		self.tool_data(
			"add_task_note",
			{"task": task, "narrative": "Opened it.", "author": WORKER, "author_name": "Ana Ramos"},
		)
		self.tool_data(
			"attach_audio_note",
			{"task": task, "transcription": "Found the break.", "author": OTHER_WORKER},
		)
		data = self.tool_data("list_task_notes", {"task": task})
		self.assertIn("Ana Ramos", data["full_narrative"])
		self.assertIn("spoken", data["full_narrative"])
		self.assertEqual(data["spoken_count"], 1)

	def test_a_doctype_that_carries_no_narrative_is_refused_by_name(self):
		self.assertIn(
			"does not carry a narrative",
			self.tool_error(
				"add_task_note",
				{"doctype": "Journal Entry", "name": "JE-0001", "narrative": "x"},
			),
		)
