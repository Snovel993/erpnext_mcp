# SPDX-License-Identifier: MIT
"""`clean_pass` — the spec contradiction, and how v0.17.1 resolves it.

THE CONTRADICTION, IN ONE PARAGRAPH. Sprint 8's rule is that a compliance
record's state is a function of its findings text: blank means Recorded, text
means Corrective Action Required. `records.py` argues for that at length and it
is right — a worker who has written "water stain, north wall, spreading" is not
offered the option of marking the walk as passed.

It breaks the moment an evidence contract REQUIRES findings text, as
MC-Cabin-01's habitability inspection does. Blank is then not a submittable
state, so the worker must type something, so every completion of that task would
open a corrective action against a cabin that is fine. The rule and the contract
cannot both be satisfied by parsing a string.

So the app asks the worker outright — "Clean pass" or "Issues found" — and sends
the answer, and the server treats THE ANSWER as authoritative rather than
re-deriving intent from the text. `fafo_ios/API_CONTRACT.md` §6 states this as a
requirement in as many words, including the case that makes string-matching
unsound: *"A worker typing the literal words 'clean pass' into a required field
must not open a corrective action."*

FOUR CLAIMS.

1. `CleanPassSatisfiesTheContract` — true satisfies a mandatory findings_text.
2. `CleanPassIsAuthoritative` — the flag beats the text, both ways round.
3. `TheRecordStillSaysWhatHappened` — an empty findings field is how records.py
   spells "nothing was wrong", so the attestation goes in notes, and the
   worker's own words survive on the assignment either way.
4. `TheOldRuleIsUntouched` — absent means nobody was asked, and the blank-means-
   clean rule that predates this still applies exactly as it did.
"""

import frappe

from erpnext_mcp.tools import dispatch

from .harness import STORE
from .test_dispatch import A_PHOTO, DispatchTestCase, WALK

#: `WALK` demands photos, a signature AND findings text. That third requirement
#: is the shape that makes the original rule unsatisfiable, and the reason the
#: flag exists — every task in this file uses it deliberately.
MANDATORY_FINDINGS = dict(WALK)


class CleanPassTestCase(DispatchTestCase):
	def setUp(self):
		super().setUp()
		self.unit = self.a_camp()

	def walked(self, **overrides):
		"""One claimed, started habitability inspection of MC-Cabin-01.

		It PRODUCES a Housing Inspection, which is the whole point: the flag only
		matters where a completion writes a compliance record whose state is
		derived from its findings.
		"""
		payload = {
			"evidence_required": dict(MANDATORY_FINDINGS),
			"creates_record": "Housing Inspection",
			"location_doctype": "Housing Unit",
			"location": self.unit,
		}
		payload.update(overrides)
		task = self.claimed(**payload)
		self.tool_data("start_farm_task", {"task": task, "worker_id": "EMP-001"})
		return task

	def inspection(self):
		rows = STORE.rows("Housing Inspection")
		self.assertTrue(rows, "no Housing Inspection was written")
		return dict(rows[-1])


# ── 1 ───────────────────────────────────────────────────────────────────────
class CleanPassSatisfiesTheContract(CleanPassTestCase):
	def test_a_mandatory_findings_field_is_met_by_the_flag_alone(self):
		"""The whole bug: without this, MC-Cabin-01 could not be completed clean."""
		task = self.walked()
		data = self.complete(task, clean_pass=True, findings_text=None)
		self.assertEqual(data["final_state"], "Completed")

	def test_without_the_flag_a_mandatory_findings_field_is_still_mandatory(self):
		task = self.walked()
		message = self.tool_error(
			"complete_farm_task",
			{
				"task": task,
				"worker_id": "EMP-001",
				"evidence_files": list(A_PHOTO),
				"signature_file": "/files/sig.png",
			},
		)
		self.assertIn("findings_text:", message)

	def test_clean_pass_false_with_nothing_written_is_refused(self):
		"""'Issues found' with no issue named opens a corrective action that
		names no fault, which is the one thing an auditor cannot act on."""
		task = self.walked()
		message = self.tool_error(
			"complete_farm_task",
			{
				"task": task,
				"worker_id": "EMP-001",
				"evidence_files": list(A_PHOTO),
				"signature_file": "/files/sig.png",
				"findings_text": "",
				"clean_pass": False,
			},
		)
		self.assertIn("clean_pass=false", message)
		self.assertIn("Write what was wrong", message)


# ── 2 ───────────────────────────────────────────────────────────────────────
class CleanPassIsAuthoritative(CleanPassTestCase):
	def test_the_literal_words_clean_pass_do_not_open_a_corrective_action(self):
		"""The exact case `API_CONTRACT.md` names. String-matching would fail it."""
		task = self.walked()
		data = self.complete(task, findings_text="clean pass", clean_pass=True)
		self.assertEqual(data["produced_record_state"], "Recorded")
		self.assertEqual(data["final_state"], "Completed")

	def test_a_real_finding_still_opens_one(self):
		task = self.walked()
		data = self.complete(task, findings_text="water stain, north wall, spreading", clean_pass=False)
		self.assertEqual(data["produced_record_state"], "Corrective Action Required")
		self.assertEqual(data["final_state"], "Awaiting-Review")

	def test_the_flag_is_read_from_every_form_an_http_body_delivers(self):
		"""A JSON body reaching a whitelisted method can arrive as form data, so
		the flag turns up as 1, "1", "true" and True — all the same answer."""
		for raw in (True, 1, "1", "true", "yes"):
			self.assertIs(dispatch.clean_pass_flag({"clean_pass": raw}), True, raw)
		for raw in (False, 0, "0", "false", "no"):
			self.assertIs(dispatch.clean_pass_flag({"clean_pass": raw}), False, raw)

	def test_a_value_nobody_can_read_is_refused_rather_than_guessed(self):
		with self.assertRaises(Exception) as caught:
			dispatch.clean_pass_flag({"clean_pass": "maybe"})
		self.assertIn("must be true or false", str(caught.exception))


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheRecordStillSaysWhatHappened(CleanPassTestCase):
	def test_the_records_findings_are_empty_because_that_is_how_clean_is_spelled(self):
		"""`records.branch_state` reads state off this field. Putting the
		attestation IN it would flip the cabin to Corrective Action Required."""
		task = self.walked()
		self.complete(task, findings_text="clean pass", clean_pass=True)
		record = self.inspection()
		self.assertEqual(str(record.get("findings") or ""), "")
		self.assertEqual(record["workflow_state"], "Recorded")

	def test_the_attestation_is_written_where_it_does_not_change_the_state(self):
		"""A blank findings field and a blank notes field would be a record that
		cannot tell 'walked, nothing wrong' from 'nobody filled this in'."""
		task = self.walked()
		self.complete(task, findings_text=None, clean_pass=True)
		self.assertIn(dispatch.CLEAN_PASS_NOTE, self.inspection()["notes"])
		self.assertEqual(dispatch.CLEAN_PASS_NOTE, "No findings reported by inspector.")

	def test_the_workers_own_words_survive_on_the_assignment(self):
		"""The record's findings are the server's judgement; the assignment's are
		the evidence, and editing evidence is not this function's to do."""
		task = self.walked()
		data = self.complete(task, findings_text="looked fine to me", clean_pass=True)
		self.assertEqual(data["assignment"]["findings_text"], "looked fine to me")
		self.assertIn("Inspector's note: looked fine to me", self.inspection()["notes"])

	def test_the_unit_register_moves_forward_on_a_clean_pass(self):
		"""The whole point of doing the work: the alert goes away because the
		condition stopped being true, not because anybody dismissed it."""
		task = self.walked()
		self.complete(task, clean_pass=True, findings_text=None)
		self.assertTrue(
			frappe.db.get_value("Housing Unit", self.unit, "last_habitability_inspection"),
			"a clean inspection did not move the register",
		)


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheOldRuleIsUntouched(CleanPassTestCase):
	def test_absent_means_nobody_was_asked_and_blank_findings_are_still_clean(self):
		task = self.walked()
		data = self.complete(task, findings_text="")
		self.assertEqual(data["produced_record_state"], "Recorded")

	def test_absent_and_findings_written_still_opens_a_corrective_action(self):
		task = self.walked()
		data = self.complete(task, findings_text="screen torn, east window")
		self.assertEqual(data["produced_record_state"], "Corrective Action Required")

	def test_absent_is_a_third_state_and_not_a_synonym_for_false(self):
		self.assertIsNone(dispatch.clean_pass_flag({}))
		self.assertIsNone(dispatch.clean_pass_flag({"clean_pass": None}))
		self.assertIsNone(dispatch.clean_pass_flag({"clean_pass": ""}))
