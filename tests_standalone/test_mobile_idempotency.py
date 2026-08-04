# SPDX-License-Identifier: MIT
"""A completion that arrives twice is one completion — v0.20.1.

THE FAILURE THIS MODULE IS THE REGRESSION TEST FOR happened on an iPad on the
evening of 2026-08-03. A worker finished their round with the wifi off and the
offline path did exactly what it should. When the handset found signal the queue
drained, the server accepted every completion, and the acknowledgement did not
survive the trip back. The app re-sent, which is the only thing a client can do,
and got `already Completed` as a hard error — three Failed entries per task, on
work that was filed, evidenced and had already produced its compliance record.

The refusal was right as a data-integrity rule and wrong as an API contract. A
CLIENT CANNOT KNOW WHETHER ITS REQUEST LANDED, and no retry logic on the phone
changes that; the only place the question can be answered is here.

────────────────────────────────────────────────────────────────────────────
FOUR CLAIMS
────────────────────────────────────────────────────────────────────────────

1. **AN IDENTICAL RESUBMISSION SUCCEEDS AND CHANGES NOTHING.** `TheRetryPath`.
   Same worker, same evidence, same words — the completion already on record
   comes back with `x_idempotent: true`, and every countable thing about the
   site is identical afterwards. Including under a retry storm: three rapid
   resubmissions produce three successes, one compliance record, one set of
   evidence rows.

2. **A DIFFERENT SUBMISSION IS STILL A CONFLICT.** `ConflictsAreStillRefused`.
   Different findings, different evidence, a different worker. Absorbing those
   silently would be a worse bug than the one being fixed — two people cannot
   file the same completion, and a second account of the same work is not the
   first one again.

3. **THE ROWS THAT PREDATE THIS RELEASE ARE COVERED.** `TheBackfill`. They are
   the rows most likely to be sitting in a stuck queue, and a feature that only
   worked on data created after it shipped would have missed the actual
   complaint. The backfill runs twice on any real bench and is a no-op the
   second time.

4. **NOTHING ABOUT THE FIRST COMPLETION MOVED.** `TheFirstCompletionIsUnchanged`.
   The whole v0.18–v0.20.0 success path is byte-for-byte what it was; the
   idempotent answer is a new branch on a state that used to raise, not a change
   to the state that used to succeed.
"""

import frappe

from erpnext_mcp import completions
from erpnext_mcp.patches import backfill_completion_signatures

from .harness import STORE
from .test_dispatch import A_PHOTO
from .test_fieldwork import WORKER_EMPLOYEE, FieldworkTestCase

#: A second photograph, so "different evidence" is a real difference rather than
#: a reordering. Filed as a URL for the same reason A_PHOTO is: no File record.
ANOTHER_PHOTO = [{"file_url": "/files/south-wall.jpg", "evidence_type": "Photo", "caption": "south"}]

#: One visit, as an app would mint it.
VISIT = "5C1F0A64-2B3D-4E5F-8A9B-0C1D2E3F4A5B"


class IdempotencyTestCase(FieldworkTestCase):
	"""A task claimed and started, and the payload that completes it."""

	def a_started_task(self, **overrides):
		overrides.setdefault("evidence_required", {"findings_text": True})
		task = self.a_task(**overrides)
		self.worker_data("claim_task_via_mobile", {"task_name": task["name"]})
		self.worker_data("start_task_via_mobile", {"task_name": task["name"]})
		return task

	def payload(self, task, **overrides):
		body = {"task_name": task["name"], "findings_text": "", "evidence": list(A_PHOTO)}
		body.update(overrides)
		return body

	def census(self) -> dict:
		"""Every row on the site by doctype. The assertion that nothing was written.

		A COUNT AND NOT A SPOT CHECK, because the failure being guarded against
		is a duplicate somewhere nobody thought to look — a second Housing
		Inspection, a second evidence row, a second GL entry from a record's own
		side effects. Naming the doctypes to check would be naming the ones
		already thought of.
		"""
		return {doctype: len(rows) for doctype, rows in STORE.tables.items() if doctype != "MCP Action Log"}

	def assignment(self) -> dict:
		return STORE.rows("Farm Task Assignment")[0]


class TheRetryPath(IdempotencyTestCase):
	def test_a_completion_returns_its_signature(self):
		"""The server's own identifier for what it just accepted."""
		task = self.a_started_task()
		data = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertIn(data["final_state"], ("Completed", "Awaiting-Review"))
		self.assertFalse(data["x_idempotent"])
		self.assertTrue(data["completion_signature"].startswith("v1:"))
		self.assertEqual(self.assignment()["completion_signature"], data["completion_signature"])

	def test_the_same_submission_again_succeeds_instead_of_raising(self):
		"""THE BUG. This call used to be a 4xx the worker saw as Failed."""
		task = self.a_started_task()
		first = self.worker_data("complete_task_via_mobile", self.payload(task))
		second = self.worker_data("complete_task_via_mobile", self.payload(task))

		self.assertTrue(second["x_idempotent"])
		self.assertEqual(second["completion_signature"], first["completion_signature"])
		self.assertEqual(second["final_state"], first["final_state"])
		self.assertEqual(second["assignment"]["name"], first["assignment"]["name"])
		self.assertEqual(second["assignment"]["completed_at"], first["assignment"]["completed_at"])

	def test_the_second_call_writes_nothing_at_all(self):
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))
		before = self.census()
		stored = dict(self.assignment())

		self.worker_data("complete_task_via_mobile", self.payload(task))

		self.assertEqual(before, self.census())
		self.assertEqual(stored, self.assignment())

	def test_three_rapid_resubmissions_are_three_successes_and_one_record(self):
		"""The retry storm, as a queue that has given up on hearing back sends it.

		One compliance record, one set of evidence rows, and no doctype anywhere
		on the site grew — see `census`.
		"""
		unit = self.a_camp()
		task = self.a_started_task(
			location_doctype="Housing Unit", location=unit, creates_record="Housing Inspection"
		)
		first = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertTrue(first["produced_record"])
		before = self.census()

		for attempt in range(3):
			with self.subTest(attempt=attempt):
				again = self.worker_data("complete_task_via_mobile", self.payload(task))
				self.assertTrue(again["x_idempotent"])
				self.assertEqual(again["produced_record"], first["produced_record"])
				self.assertEqual(again["evidence_filed"], first["evidence_filed"])

		self.assertEqual(before, self.census())
		self.assertEqual(len(STORE.rows("Housing Inspection")), 1)

	def test_the_retry_works_when_the_client_names_only_the_task(self):
		"""THE OTHER HALF OF THE SAME BUG, and it fails differently.

		A completion ends the LIVE assignment, so a second call carrying only a
		task name used to be refused with "nobody is holding it" — a worse answer
		than "already completed" and the same lost work.
		"""
		task = self.a_started_task()
		first = self.worker_data("complete_task_via_mobile", self.payload(task))
		again = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertTrue(again["x_idempotent"])
		self.assertEqual(again["assignment"]["name"], first["assignment"]["name"])

	def test_the_retry_works_when_the_client_names_the_assignment(self):
		task = self.a_started_task()
		first = self.worker_data("complete_task_via_mobile", self.payload(task))
		again = self.worker_data(
			"complete_task_via_mobile",
			self.payload(task, assignment_name=first["assignment"]["name"]),
		)
		self.assertTrue(again["x_idempotent"])

	def test_the_idempotent_answer_says_the_work_is_recorded(self):
		"""A success a client cannot distinguish from a fresh one is a success a
		human reading a log cannot audit either."""
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))
		again = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertIn("already filed", again["idempotent_note"])
		self.assertIn("nothing was changed", again["idempotent_note"])

	def test_a_location_that_arrived_late_does_not_make_it_a_different_submission(self):
		"""The worker walked out of the shed between attempts and the phone got a
		fix. That is the same completion with a better location, not another one."""
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))
		again = self.worker_data(
			"complete_task_via_mobile", self.payload(task, farm_location_gps="45.5152,-122.6784")
		)
		self.assertTrue(again["x_idempotent"])

	def test_evidence_in_a_different_order_is_the_same_evidence(self):
		task = self.a_started_task(evidence_required={"photos": True, "findings_text": True})
		both = list(A_PHOTO) + list(ANOTHER_PHOTO)
		self.worker_data("complete_task_via_mobile", self.payload(task, evidence=both))
		again = self.worker_data(
			"complete_task_via_mobile", self.payload(task, evidence=list(reversed(both)))
		)
		self.assertTrue(again["x_idempotent"])


class ConflictsAreStillRefused(IdempotencyTestCase):
	def test_a_different_account_of_the_work_is_refused(self):
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))
		message = self.worker_error(
			"complete_task_via_mobile", self.payload(task, findings_text="water stain, north wall")
		)
		self.assertIn("Completed", message)
		self.assertIn("Nothing was changed", message)

	def test_different_evidence_is_refused(self):
		task = self.a_started_task(evidence_required={"photos": True, "findings_text": True})
		self.worker_data("complete_task_via_mobile", self.payload(task))
		message = self.worker_error(
			"complete_task_via_mobile", self.payload(task, evidence=list(ANOTHER_PHOTO))
		)
		self.assertIn("Completed", message)

	def test_a_different_narrative_is_refused(self):
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task, completion_narrative="walked it"))
		message = self.worker_error(
			"complete_task_via_mobile", self.payload(task, completion_narrative="walked it twice")
		)
		self.assertIn("Completed", message)

	def test_a_different_worker_is_refused_by_the_chain_of_custody_rule(self):
		"""A completion carries the completing worker's identity as EVIDENCE. The
		refusal that catches this predates v0.20.1 and is deliberately left in
		front of the signature check — 'held by somebody else' is a more useful
		sentence than 'that is not the submission on record'."""
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))

		other = self.enrol(email="ben@example.test", name="Ben Ortiz")
		result = self.as_worker("complete_task_via_mobile", self.payload(task), credential=other)
		self.assertTrue(result.get("isError"))
		message = result["content"][0]["text"]
		self.assertIn("Ana Ramos", message)
		self.assertIn("rumour", message)

	def test_a_conflicting_resubmission_writes_nothing(self):
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))
		before, stored = self.census(), dict(self.assignment())
		self.worker_error("complete_task_via_mobile", self.payload(task, findings_text="something"))
		self.assertEqual(before, self.census())
		self.assertEqual(stored, self.assignment())

	def test_a_completed_row_with_no_signature_at_all_still_refuses(self):
		"""Guessing that an unknown submission matches an unsigned row would turn
		a genuine conflict into a silent success."""
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))
		frappe.db.set_value("Farm Task Assignment", self.assignment()["name"], "completion_signature", "")
		message = self.worker_error("complete_task_via_mobile", self.payload(task))
		self.assertIn("cannot be completed", message)


class TheBackfill(IdempotencyTestCase):
	"""Rows completed before v0.20.1 — the ones most likely to be re-sent."""

	def a_pre_v0201_completion(self, **overrides):
		"""Complete a task, then strip the signature the way v0.19.7 left it."""
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task, **overrides))
		frappe.db.set_value("Farm Task Assignment", self.assignment()["name"], "completion_signature", "")
		return task

	def test_the_backfill_signs_an_unsigned_completed_row(self):
		self.a_pre_v0201_completion()
		report = backfill_completion_signatures.backfill_completion_signatures()
		self.assertEqual(report["signed"], 1)
		self.assertTrue(self.assignment()["completion_signature"].startswith("v1b:"))

	def test_a_backfilled_row_recognises_a_matching_resubmission(self):
		"""The whole reason the backfill exists."""
		task = self.a_pre_v0201_completion()
		backfill_completion_signatures.execute()
		again = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertTrue(again["x_idempotent"])

	def test_a_backfilled_row_still_refuses_a_different_submission(self):
		task = self.a_pre_v0201_completion()
		backfill_completion_signatures.execute()
		message = self.worker_error(
			"complete_task_via_mobile", self.payload(task, findings_text="dead detector")
		)
		self.assertIn("Completed", message)

	def test_a_backfilled_row_matches_whatever_clock_out_time_arrives(self):
		"""Nothing on a legacy row says whether the client or the server chose
		`completed_at`, so a backfilled signature does not hash it. Guessing
		would create false conflicts on exactly the oldest rows."""
		task = self.a_pre_v0201_completion()
		backfill_completion_signatures.execute()
		again = self.worker_data(
			"complete_task_via_mobile", self.payload(task, completed_at="2026-08-03 17:45:00")
		)
		self.assertTrue(again["x_idempotent"])

	def test_running_the_backfill_twice_signs_nothing_the_second_time(self):
		self.a_pre_v0201_completion()
		first = backfill_completion_signatures.backfill_completion_signatures()
		signature = self.assignment()["completion_signature"]
		second = backfill_completion_signatures.backfill_completion_signatures()

		self.assertEqual(first["signed"], 1)
		self.assertEqual(second["signed"], 0)
		self.assertEqual(second["already_signed"], 1)
		self.assertEqual(self.assignment()["completion_signature"], signature)

	def test_it_never_rewrites_a_signature_a_completion_wrote(self):
		task = self.a_started_task()
		data = self.worker_data("complete_task_via_mobile", self.payload(task))
		backfill_completion_signatures.execute()
		self.assertEqual(self.assignment()["completion_signature"], data["completion_signature"])

	def test_it_leaves_unfinished_assignments_alone(self):
		self.a_started_task()
		report = backfill_completion_signatures.backfill_completion_signatures()
		self.assertEqual(report["scanned"], 0)
		self.assertEqual(report["signed"], 0)

	def test_a_silent_run_prints_nothing(self):
		"""A migration that prints on every migrate is one nobody reads by the
		third release."""
		self.assertEqual(backfill_completion_signatures.report_lines({"signed": 0, "scanned": 0}), [])

	def test_the_report_names_what_it_did(self):
		lines = backfill_completion_signatures.report_lines({"signed": 4, "scanned": 9})
		self.assertEqual(len(lines), 1)
		self.assertIn("4 completed Farm Task Assignment", lines[0])


class TheSignatureItself(IdempotencyTestCase):
	"""`erpnext_mcp/completions.py` on its own, without a site around it."""

	def test_evidence_order_does_not_change_the_hash(self):
		one = completions.signature("FTA-1", "EMP-ANA", ["File-A", "File-B"], "", "", "")
		other = completions.signature("FTA-1", "EMP-ANA", ["File-B", "File-A"], "", "", "")
		self.assertEqual(one, other)

	def test_a_different_worker_changes_the_hash(self):
		one = completions.signature("FTA-1", "EMP-ANA", [], "", "", "")
		other = completions.signature("FTA-1", "EMP-BEN", [], "", "", "")
		self.assertNotEqual(one, other)

	def test_surrounding_whitespace_on_the_notes_does_not(self):
		"""A client that re-serialised its own text between attempts is retrying,
		not filing something new. Interior whitespace is content and is hashed."""
		one = completions.signature("FTA-1", "EMP-ANA", [], "water stain", "", "")
		other = completions.signature("FTA-1", "EMP-ANA", [], "  water stain\n", "", "")
		self.assertEqual(one, other)

	def test_interior_text_is_still_content(self):
		one = completions.signature("FTA-1", "EMP-ANA", [], "water stain", "", "")
		other = completions.signature("FTA-1", "EMP-ANA", [], "water  stain", "", "")
		self.assertNotEqual(one, other)

	def test_the_two_schemes_are_never_confused_for_each_other(self):
		live = completions.signature("FTA-1", "EMP-ANA", [], "", "", "")
		legacy = completions.backfill_signature("FTA-1", "EMP-ANA", [], "", "")
		self.assertNotEqual(live, legacy)
		self.assertTrue(live.startswith("v1:"))
		self.assertTrue(legacy.startswith("v1b:"))

	def test_an_empty_or_unreadable_stored_signature_matches_nothing(self):
		for stored in ("", None, "   ", "v9:deadbeef", "nonsense"):
			with self.subTest(stored=stored):
				self.assertFalse(completions.matches(stored, "FTA-1", "EMP-ANA", [], "", "", ""))

	def test_a_component_cannot_be_smuggled_across_the_separator(self):
		"""Two different submissions must not hash the same. The separator is
		ASCII 31 precisely because nothing a caller can send contains it."""
		one = completions.signature("FTA-1", "EMP-ANA", [], "a", "b", "")
		other = completions.signature("FTA-1", "EMP-ANA", [], "a\x1fb", "", "")
		self.assertNotEqual(one, other)

	def test_evidence_rows_are_read_by_file_then_by_url(self):
		self.assertEqual(
			completions.evidence_references([{"file": "File-A"}, {"file_url": "/files/b.jpg"}]),
			["/files/b.jpg", "File-A"],
		)

	def test_a_row_naming_neither_is_dropped_rather_than_hashed_as_empty(self):
		self.assertEqual(completions.evidence_references([{"caption": "north wall"}, "File-A"]), ["File-A"])


class TheFirstCompletionIsUnchanged(IdempotencyTestCase):
	"""v0.20.1 is additive. The success path is the one v0.18 shipped."""

	def test_a_fresh_completion_still_produces_its_compliance_record(self):
		unit = self.a_camp()
		task = self.a_started_task(
			location_doctype="Housing Unit", location=unit, creates_record="Housing Inspection"
		)
		data = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertTrue(frappe.db.exists("Housing Inspection", data["produced_record"]))
		self.assertFalse(data["x_idempotent"])

	def test_findings_still_route_a_completion_to_awaiting_review(self):
		unit = self.a_camp()
		task = self.a_started_task(
			location_doctype="Housing Unit", location=unit, creates_record="Housing Inspection"
		)
		data = self.worker_data(
			"complete_task_via_mobile", self.payload(task, findings_text="water stain, north wall")
		)
		self.assertEqual(data["final_state"], "Awaiting-Review")

	def test_an_unclaimed_task_is_still_refused(self):
		task = self.a_task(evidence_required={"findings_text": True})
		message = self.worker_error("complete_task_via_mobile", self.payload(task))
		self.assertIn("nobody holding it", message)

	def test_a_rejected_assignment_is_still_refused(self):
		"""The fallback to a finished assignment is for COMPLETED rows only. A
		rejection is not a completion arriving twice."""
		task = self.a_started_task()
		frappe.db.set_value("Farm Task Assignment", self.assignment()["name"], "state", "Rejected")
		message = self.worker_error("complete_task_via_mobile", self.payload(task))
		self.assertIn("nobody holding it", message)

	def test_starting_a_completed_task_is_still_refused(self):
		"""`or_completed` is passed by the completion path and by nothing else."""
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task))
		message = self.worker_error("start_task_via_mobile", {"task_name": task["name"]})
		self.assertIn("nobody holding it", message)

	def test_the_worker_still_comes_from_the_request(self):
		task = self.a_started_task()
		data = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertEqual(data["assignment"]["assigned_to"], WORKER_EMPLOYEE)


class TheVisitIdRoundTrips(IdempotencyTestCase):
	"""Item 2 — the column the trip rollup is built on."""

	def test_a_visit_id_is_persisted_and_returned(self):
		task = self.a_started_task()
		data = self.worker_data("complete_task_via_mobile", self.payload(task, visit_id=VISIT))
		self.assertEqual(data["visit_id"], VISIT)
		self.assertEqual(data["assignment"]["visit_id"], VISIT)
		self.assertEqual(self.assignment()["visit_id"], VISIT)

	def test_a_completion_without_one_leaves_the_column_blank(self):
		task = self.a_started_task()
		data = self.worker_data("complete_task_via_mobile", self.payload(task))
		self.assertIsNone(data["visit_id"])
		self.assertFalse(self.assignment().get("visit_id"))

	def test_its_shape_is_not_validated_in_v0201(self):
		"""The app mints it. A server with opinions about somebody else's
		identifier format would refuse a completion over the one field on it that
		carries no evidence."""
		task = self.a_started_task()
		data = self.worker_data("complete_task_via_mobile", self.payload(task, visit_id="north-block-am"))
		self.assertEqual(data["visit_id"], "north-block-am")

	def test_it_is_not_part_of_what_makes_a_resubmission_identical(self):
		"""It groups completions; it does not identify one. A retry from a client
		that has since re-minted its visit is still that same completion."""
		task = self.a_started_task()
		self.worker_data("complete_task_via_mobile", self.payload(task, visit_id=VISIT))
		again = self.worker_data("complete_task_via_mobile", self.payload(task, visit_id="a-different-one"))
		self.assertTrue(again["x_idempotent"])
		self.assertEqual(self.assignment()["visit_id"], VISIT)


if __name__ == "__main__":
	import unittest

	unittest.main()
