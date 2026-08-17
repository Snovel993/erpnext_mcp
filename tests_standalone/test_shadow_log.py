# SPDX-License-Identifier: MIT
"""The RACI feed: what happened, frozen, addressed up the chain of command.

FIVE CLAIMS, AND EVERY CLASS IN THIS FILE IS ONE OF THEM.

1. **THE CHAIN IS `reports_to`, AND IT IS ALLOWED TO BE SHORT.** `TheChainIsWalked`.
   Level 1 is the direct supervisor, 2 is theirs, 3 is the one above that — a
   DISTANCE, not a job title. A crew whose chain is two deep produces two copies
   and not three with the third addressed to whoever happened to be around, and a
   cycle in `reports_to` (which is a data-entry error somebody makes, not a
   hypothetical) stops the walk rather than blowing the stack inside a bucket
   sync.

2. **THE SNAPSHOT IS FROZEN.** `TheSnapshotIsFrozen`. This is the entire value of
   the row: a supervisor who acknowledged 412 buckets acknowledged 412, and a
   later recount to 380 is a second fact rather than a silent rewrite of the
   first. The controller refuses a change after insert; the hash makes "frozen"
   checkable rather than merely promised.

3. **PROPAGATION CANNOT FAIL ITS CALLER.** `TheFeedNeverBreaksTheWork`. A bucket
   sync, a shift close, an alert and a completion are somebody's real work being
   filed, and none of them may fail because a supervisory copy could not be
   written. What could not be done is REPORTED on a result that succeeded.

4. **THE FOUR EVENTS PROPAGATE, AND IDEMPOTENTLY.** `TheFourEventsFire`. A
   handset that timed out and resent a batch is the ordinary case, so a resend
   must not put a second copy of one morning in front of the same foreman — and
   the first snapshot stands rather than being refreshed.

5. **ACKNOWLEDGEMENT IS ONE-WAY AND RETRY-SAFE.** `TheAcknowledgementIsOneWay`.
   "I saw this" is a statement about the past; clearing the tick would leave no
   trace of either the seeing or the later dispute. A second call changes nothing
   and says so, because a phone that lost its response must not have to choose
   between retrying and being correct.
"""

import json
from typing import ClassVar

import frappe

from erpnext_mcp.tools import shadow_log

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import META, STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"list_shadow_log_entries",
		"get_shadow_log_entry",
		"acknowledge_shadow_log",
	)
}

PICKER = "HR-EMP-00001"
FOREMAN = "HR-EMP-00002"
MANAGER = "HR-EMP-00003"
OWNER = "HR-EMP-00004"
#: Nobody above them, in the OTHER company. What `_top_of_house` finds for an
#: event that is about the operation rather than about a person.
OTHER_OWNER = "HR-EMP-00005"
#: A pair who report to each other. `reports_to` is operator-maintained and this
#: is a mistake somebody makes, not a hypothetical.
LOOP_A = "HR-EMP-00006"
LOOP_B = "HR-EMP-00007"


class ShadowLogTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		STORE.seed(
			"Employee",
			[
				{
					"name": PICKER,
					"employee_name": "Ana Ramos",
					"company": MAIN,
					"status": "Active",
					"reports_to": FOREMAN,
				},
				{
					"name": FOREMAN,
					"employee_name": "Luis Ortega",
					"company": MAIN,
					"status": "Active",
					"reports_to": MANAGER,
				},
				{
					"name": MANAGER,
					"employee_name": "Dana Cole",
					"company": MAIN,
					"status": "Active",
					"reports_to": OWNER,
				},
				{
					"name": OWNER,
					"employee_name": "Tom Whitlock",
					"company": MAIN,
					"status": "Active",
				},
				{
					"name": OTHER_OWNER,
					"employee_name": "Rae Lindqvist",
					"company": OTHER,
					"status": "Active",
				},
				{
					"name": LOOP_A,
					"employee_name": "Kim Sandoval",
					"company": MAIN,
					"status": "Active",
					"reports_to": LOOP_B,
				},
				{
					"name": LOOP_B,
					"employee_name": "Pat Nakamura",
					"company": MAIN,
					"status": "Active",
					"reports_to": LOOP_A,
				},
			],
		)

	def raise_one(self, **overrides):
		"""One propagation with the fixture's defaults, for tests about the rest."""
		payload = {
			"event_type": shadow_log.EVENT_BUCKET_SESSION,
			"source_doctype": "Bucket Log Session",
			"source_name": "BLS-0001",
			"subject_employee": PICKER,
			"company": MAIN,
			"occurred_at": "2026-08-16 14:00:00",
			"summary": "Ana Ramos synced a picking session: 412 accepted, 3 rejected.",
			"snapshot": {"total_accepted": 412, "total_rejected": 3, "employee": PICKER},
		}
		payload.update(overrides)
		return shadow_log.propagate(**payload)


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheChainIsWalked(ShadowLogTestCase):
	def test_three_levels_come_back_in_order(self):
		chain = shadow_log.raci_chain(PICKER)["chain"]
		self.assertEqual(
			[(entry["level"], entry["employee"]) for entry in chain],
			[(1, FOREMAN), (2, MANAGER), (3, OWNER)],
		)

	def test_the_level_names_say_what_the_number_means(self):
		chain = shadow_log.raci_chain(PICKER)["chain"]
		self.assertEqual(
			[entry["level_name"] for entry in chain],
			["direct supervisor", "manager", "owner"],
		)

	def test_a_short_chain_produces_fewer_copies_and_not_an_invented_one(self):
		"""Inventing a recipient would put somebody's name against work they were
		never told about."""
		result = shadow_log.raci_chain(MANAGER)
		self.assertEqual([entry["employee"] for entry in result["chain"]], [OWNER])
		self.assertTrue(any("short chain" in note for note in result["notes"]))

	def test_somebody_at_the_top_has_no_chain_at_all_and_it_is_reported(self):
		result = shadow_log.raci_chain(OWNER)
		self.assertEqual(result["chain"], [])
		self.assertTrue(any("no reports_to" in note for note in result["notes"]))

	def test_a_cycle_stops_the_walk_and_says_so(self):
		"""`reports_to` is operator-maintained. Two people reporting to each other
		is a mistake somebody makes, and a recursion that blew the stack inside a
		bucket sync would take a morning's picking with it."""
		result = shadow_log.raci_chain(LOOP_A)
		self.assertEqual([entry["employee"] for entry in result["chain"]], [LOOP_B])
		self.assertTrue(any("already appears in this chain" in note for note in result["notes"]))

	def test_no_subject_means_no_chain_rather_than_an_error(self):
		result = shadow_log.raci_chain("")
		self.assertEqual(result["chain"], [])
		self.assertTrue(result["notes"])

	def test_a_site_with_no_reports_to_column_says_so_by_name(self):
		"""A note naming the missing column is something an operator can act on;
		an empty feed is not. `reports_to` is standard on ERPNext's Employee, so
		this is a site running Frappe HR trimmed or a doctype somebody replaced —
		rare, and silent in exactly the way that wastes an afternoon."""
		meta = META["Employee"]
		kept, kept_index = list(meta.fields), dict(meta._by_name)
		meta.fields = [field for field in kept if field["fieldname"] != "reports_to"]
		meta._by_name = {name: field for name, field in kept_index.items() if name != "reports_to"}
		try:
			result = shadow_log.raci_chain(PICKER)
			self.assertEqual(result["chain"], [])
			self.assertTrue(any("no reports_to column" in note for note in result["notes"]))
		finally:
			meta.fields, meta._by_name = kept, kept_index

	def test_the_walk_stops_at_three_even_if_the_chain_is_longer(self):
		"""A fourth level on a farm this size is the same person twice, and the
		controller refuses one anyway."""
		STORE.seed(
			"Employee",
			[{"name": "HR-EMP-00008", "employee_name": "Deep", "company": MAIN, "status": "Active", "reports_to": PICKER}],
		)
		chain = shadow_log.raci_chain("HR-EMP-00008")["chain"]
		self.assertEqual(len(chain), shadow_log.MAX_LEVEL)


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheSnapshotIsFrozen(ShadowLogTestCase):
	def test_a_copy_lands_for_every_level(self):
		report = self.raise_one()
		self.assertEqual(
			[(row["level"], row["recipient"]) for row in report["raised"]],
			[(1, FOREMAN), (2, MANAGER), (3, OWNER)],
		)

	def test_the_snapshot_holds_the_values_as_they_were(self):
		report = self.raise_one()
		data = self.tool_data("get_shadow_log_entry", {"name": report["raised"][0]["name"]})
		self.assertEqual(data["snapshot"]["total_accepted"], 412)

	def test_the_snapshot_cannot_be_rewritten_after_insert(self):
		"""THE CLAIM THE WHOLE DOCTYPE EXISTS FOR. If a recount could rewrite the
		copy, the supervisor's acknowledgement would migrate onto values they
		never saw."""
		report = self.raise_one()
		doc = frappe.get_doc("Shadow Log Entry", report["raised"][0]["name"])
		doc.data_snapshot = json.dumps({"total_accepted": 380})
		with self.assertRaises(Exception) as caught:
			doc.save(ignore_permissions=True)
		self.assertIn("FROZEN", str(caught.exception))

	def test_the_timestamp_cannot_be_moved_after_the_fact(self):
		report = self.raise_one()
		doc = frappe.get_doc("Shadow Log Entry", report["raised"][0]["name"])
		doc.occurred_at = "2026-08-17 09:00:00"
		with self.assertRaises(Exception):
			doc.save(ignore_permissions=True)

	def test_the_hash_makes_frozen_checkable_rather_than_merely_promised(self):
		report = self.raise_one()
		data = self.tool_data("get_shadow_log_entry", {"name": report["raised"][0]["name"]})
		self.assertTrue(data["snapshot_intact"])
		self.assertNotIn("integrity_warning", data)

	def test_a_row_altered_round_the_controller_is_shown_and_flagged(self):
		"""Hiding it would hide the evidence of the alteration."""
		report = self.raise_one()
		name = report["raised"][0]["name"]
		STORE.get_raw("Shadow Log Entry", name)["data_snapshot"] = json.dumps({"total_accepted": 1})
		data = self.tool_data("get_shadow_log_entry", {"name": name})
		self.assertFalse(data["snapshot_intact"])
		self.assertIn("went round the doctype's controller", data["integrity_warning"])
		self.assertEqual(data["snapshot"]["total_accepted"], 1)

	def test_a_snapshot_that_is_not_an_object_is_refused(self):
		"""Every reader of this feed indexes it by field name."""
		doc = frappe.new_doc("Shadow Log Entry")
		doc.shadow_key = "x::y::z::w"
		doc.event_type = shadow_log.EVENT_SHIFT_CLOSED
		doc.shadow_level = 1
		doc.recipient_employee = FOREMAN
		doc.source_doctype = "Farm Shift"
		doc.source_name = "SHIFT-1"
		doc.occurred_at = "2026-08-16 14:00:00"
		doc.summary = "x"
		doc.data_snapshot = json.dumps([1, 2, 3])
		with self.assertRaises(Exception) as caught:
			doc.insert(ignore_permissions=True)
		self.assertIn("JSON OBJECT", str(caught.exception))

	def test_a_level_outside_one_to_three_is_refused(self):
		doc = frappe.new_doc("Shadow Log Entry")
		doc.shadow_key = "x::y::z::v"
		doc.event_type = shadow_log.EVENT_SHIFT_CLOSED
		doc.shadow_level = 7
		doc.recipient_employee = FOREMAN
		doc.source_doctype = "Farm Shift"
		doc.source_name = "SHIFT-1"
		doc.occurred_at = "2026-08-16 14:00:00"
		doc.summary = "x"
		doc.data_snapshot = json.dumps({"a": 1})
		with self.assertRaises(Exception) as caught:
			doc.insert(ignore_permissions=True)
		self.assertIn("DISTANCE", str(caught.exception))

	def test_the_copy_survives_the_source_being_deleted(self):
		"""THE CASE THIS FEED EXISTS FOR. `source_doctype`/`source_name` are Data
		and not Links precisely so a delete cannot cascade into the backup."""
		STORE.seed("Bucket Log Session", [{"name": "BLS-0001", "company": MAIN, "session_uuid": "u"}])
		report = self.raise_one()
		del STORE.tables["Bucket Log Session"]["BLS-0001"]
		data = self.tool_data("get_shadow_log_entry", {"name": report["raised"][0]["name"]})
		self.assertFalse(data["source_still_exists"])
		self.assertIn("ONLY RECORD OF IT", data["source_note"])
		self.assertEqual(data["snapshot"]["total_accepted"], 412)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheFeedNeverBreaksTheWork(ShadowLogTestCase):
	def test_a_missing_doctype_is_reported_and_never_raised(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Shadow Log Entry")
		report = self.raise_one()
		self.assertEqual(report["raised"], [])
		self.assertTrue(any("bench migrate" in note for note in report["skipped"]))

	def test_the_feed_switch_stops_it_writing_and_says_so(self):
		self.configure(enabled=1, shadow_log_feed_enabled=0, **ALL_ON)
		report = self.raise_one()
		self.assertEqual(report["raised"], [])
		self.assertTrue(any("switched off" in note for note in report["skipped"]))

	def test_the_switch_ships_on_because_the_feed_is_the_feature(self):
		self.assertTrue(shadow_log.feed_enabled())

	def test_an_event_type_this_feed_does_not_carry_is_reported_not_raised(self):
		report = self.raise_one(event_type="Something Else")
		self.assertEqual(report["raised"], [])
		self.assertTrue(report["skipped"])

	def test_a_subject_nobody_reports_to_writes_nothing_and_explains(self):
		report = self.raise_one(subject_employee=OWNER)
		self.assertEqual(report["raised"], [])
		self.assertTrue(any("no reports_to" in note for note in report["notes"]))

	def test_an_event_about_the_operation_goes_to_the_top_of_the_house(self):
		"""A stale water test or an uninspected cabin is about the OPERATION.
		Without this the compliance half of the feed would raise nothing at all."""
		report = self.raise_one(
			event_type=shadow_log.EVENT_ALERT_RAISED,
			source_doctype="Compliance Alert",
			source_name="ALERT-1",
			subject_employee="",
			company=OTHER,
		)
		self.assertEqual([row["recipient"] for row in report["raised"]], [OTHER_OWNER])
		self.assertEqual(report["raised"][0]["level"], shadow_log.MAX_LEVEL)

	def test_that_fallback_is_level_three_only(self):
		"""Filing it as a level 1 would put a water test in a foreman's
		direct-report feed beside the crew events, where it does not belong."""
		report = self.raise_one(
			event_type=shadow_log.EVENT_ALERT_RAISED,
			source_doctype="Compliance Alert",
			source_name="ALERT-2",
			subject_employee="",
			company=OTHER,
		)
		self.assertEqual({row["level"] for row in report["raised"]}, {shadow_log.MAX_LEVEL})

	def test_a_company_with_nobody_at_the_top_writes_nothing_and_explains(self):
		report = self.raise_one(subject_employee="", company="Nowhere Ltd")
		self.assertEqual(report["raised"], [])
		self.assertTrue(any("nobody to address a copy to" in note for note in report["notes"]))


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheFourEventsFire(ShadowLogTestCase):
	def test_the_four_named_events_are_the_closed_set(self):
		self.assertEqual(
			set(shadow_log.EVENTS),
			{
				"Bucket Session Synced",
				"Shift Closed",
				"Compliance Alert Raised",
				"Farm Task Completed",
			},
		)

	def test_a_resend_does_not_put_a_second_copy_in_front_of_anybody(self):
		"""A handset that timed out and resent a batch is the ORDINARY case here
		— it is the case the sync's whole duplicate handling exists for."""
		first = self.raise_one()
		second = self.raise_one()
		self.assertEqual(len(first["raised"]), 3)
		self.assertEqual(second["raised"], [])
		self.assertEqual(len(second["existing"]), 3)
		self.assertEqual(len(STORE.rows("Shadow Log Entry")), 3)

	def test_the_first_snapshot_stands_and_is_not_refreshed(self):
		"""If re-raising overwrote the snapshot, an acknowledgement would migrate
		onto values nobody saw."""
		self.raise_one()
		self.raise_one(snapshot={"total_accepted": 380, "total_rejected": 35})
		rows = STORE.rows("Shadow Log Entry")
		for row in rows:
			self.assertEqual(json.loads(row["data_snapshot"])["total_accepted"], 412)

	def test_the_key_carries_no_timestamp(self):
		"""A key carrying the moment would make every retry a new row and would
		discard the acknowledgement somebody already gave."""
		key = shadow_log.shadow_key("Shift Closed", "Farm Shift", "SHIFT-1", FOREMAN)
		self.assertEqual(key, "Shift Closed::Farm Shift::SHIFT-1::" + FOREMAN)

	def test_a_pathologically_long_source_name_still_fits_a_docname(self):
		key = shadow_log.shadow_key("Shift Closed", "Farm Shift", "S" * 400, FOREMAN)
		self.assertLessEqual(len(key), 140)
		self.assertTrue(key.startswith("Shift Closed::"))

	def test_one_persons_feed_is_readable(self):
		self.raise_one()
		data = self.tool_data("list_shadow_log_entries", {"employee": MANAGER})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["entries"][0]["shadow_level"], 2)

	def test_the_feed_filters_by_level(self):
		self.raise_one()
		data = self.tool_data("list_shadow_log_entries", {"level": 1})
		self.assertEqual([row["recipient_employee"] for row in data["entries"]], [FOREMAN])

	def test_the_feed_filters_by_acknowledgement(self):
		report = self.raise_one()
		self.tool_data("acknowledge_shadow_log", {"name": report["raised"][0]["name"]})
		unread = self.tool_data("list_shadow_log_entries", {"acknowledged": False})
		self.assertEqual(unread["count"], 2)
		read = self.tool_data("list_shadow_log_entries", {"acknowledged": True})
		self.assertEqual(read["count"], 1)

	def test_an_empty_feed_says_it_is_a_real_answer(self):
		data = self.tool_data("list_shadow_log_entries", {"employee": PICKER})
		self.assertEqual(data["count"], 0)
		self.assertIn("EMPTY FEED IS A REAL ANSWER", data["empty_note"])

	def test_an_event_type_that_is_not_carried_is_refused_with_the_list(self):
		error = self.tool_error("list_shadow_log_entries", {"event_type": "Nope"})
		self.assertIn("Shift Closed", error)

	def test_an_unknown_entry_is_refused_by_name(self):
		error = self.tool_error("get_shadow_log_entry", {"name": "SHADOW-NOPE"})
		self.assertIn("SHADOW-NOPE", error)


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheAcknowledgementIsOneWay(ShadowLogTestCase):
	def test_acknowledging_records_who_and_when(self):
		report = self.raise_one()
		data = self.tool_data("acknowledge_shadow_log", {"name": report["raised"][0]["name"]})
		self.assertTrue(data["acknowledged"])
		self.assertTrue(data["acknowledged_at"])
		self.assertEqual(data["acknowledged_by"], "Administrator")
		self.assertFalse(data["x_idempotent"])

	def test_it_says_what_was_acknowledged_was_the_snapshot(self):
		report = self.raise_one()
		data = self.tool_data("acknowledge_shadow_log", {"name": report["raised"][0]["name"]})
		self.assertIn("not the source record's current values", data["note"])

	def test_a_second_call_changes_nothing_and_says_so(self):
		"""A phone that lost its response must not have to choose between
		retrying and being correct."""
		report = self.raise_one()
		name = report["raised"][0]["name"]
		first = self.tool_data("acknowledge_shadow_log", {"name": name})
		second = self.tool_data("acknowledge_shadow_log", {"name": name})
		self.assertTrue(second["x_idempotent"])
		self.assertEqual(second["acknowledged_at"], first["acknowledged_at"])

	def test_an_acknowledgement_cannot_be_withdrawn(self):
		"""'I saw this' is a statement about the past. Clearing the tick would
		leave no trace of either the seeing or the later dispute."""
		report = self.raise_one()
		name = report["raised"][0]["name"]
		self.tool_data("acknowledge_shadow_log", {"name": name})
		doc = frappe.get_doc("Shadow Log Entry", name)
		doc.acknowledged = 0
		with self.assertRaises(Exception) as caught:
			doc.save(ignore_permissions=True)
		self.assertIn("cannot be withdrawn", str(caught.exception).lower())

	def test_a_note_is_optional_because_an_acknowledgement_is_not_a_review(self):
		report = self.raise_one()
		plain = self.tool_data("acknowledge_shadow_log", {"name": report["raised"][0]["name"]})
		self.assertIsNone(plain["acknowledged_note"])
		annotated = self.tool_data(
			"acknowledge_shadow_log",
			{"name": report["raised"][1]["name"], "note": "Spoke to Ana about the rejects."},
		)
		self.assertEqual(annotated["acknowledged_note"], "Spoke to Ana about the rejects.")

	def test_acknowledging_writes_nothing_to_the_source(self):
		STORE.seed(
			"Bucket Log Session",
			[{"name": "BLS-0001", "company": MAIN, "session_uuid": "u", "total_accepted": 412}],
		)
		report = self.raise_one()
		before = dict(STORE.get_raw("Bucket Log Session", "BLS-0001"))
		self.tool_data("acknowledge_shadow_log", {"name": report["raised"][0]["name"]})
		self.assertEqual(dict(STORE.get_raw("Bucket Log Session", "BLS-0001")), before)


# ── 6: the switches ship the way this app's whole surface does ──────────────
class TheSwitchesAreRight(ShadowLogTestCase):
	def test_the_two_reads_ship_on_and_the_write_ships_off(self):
		self.configure(enabled=1)
		self.assertFalse(self.tool("list_shadow_log_entries", {}).get("isError"))
		self.assertFalse(self.tool("get_shadow_log_entry", {"name": "x"}).get("isError") is False)
		result = self.tool("acknowledge_shadow_log", {"name": "x"})
		self.assertTrue(result.get("isError"))
		self.assertIn("switched off", result["content"][0]["text"].lower())


# ── 7: the four call sites, exercised through the real tools ────────────────
class TheCallSitesActuallyFire(ShadowLogTestCase):
	"""The claim the other six classes cannot make on their own.

	Everything above tests `propagate` and the doctype. This tests that a real
	bucket sync, a real shift close and a real task completion CALL it — which is
	the half a refactor breaks silently, because every one of those tools goes on
	succeeding with the copies quietly no longer written.
	"""

	SYNC_ON: ClassVar[dict] = {
		f"allow_{name}": 1
		for name in (
			"sync_bucket_entries",
			"start_shift",
			"end_shift",
			"list_shadow_log_entries",
			"get_shadow_log_entry",
			"acknowledge_shadow_log",
		)
	}

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **self.SYNC_ON)

	def _entry(self, **overrides):
		base = {
			"entry_uuid": "11111111-1111-1111-1111-111111111111",
			"session_uuid": "S-1",
			"company": MAIN,
			"employee": PICKER,
			"timestamp": "2026-08-16 09:15:00",
			"verdict": "Accepted",
		}
		base.update(overrides)
		return base

	def test_a_bucket_sync_raises_a_copy_for_every_level(self):
		result = self.tool_data("sync_bucket_entries", {"entries": [self._entry()]})
		self.assertEqual(result["created_count"], 1)
		self.assertIn("shadow_log", result)
		raised = [row for report in result["shadow_log"] for row in report["raised"]]
		self.assertEqual([row["level"] for row in raised], [1, 2, 3])
		self.assertEqual({row["recipient"] for row in raised}, {FOREMAN, MANAGER, OWNER})

	def test_the_sync_snapshot_carries_the_session_totals(self):
		self.tool_data("sync_bucket_entries", {"entries": [self._entry()]})
		feed = self.tool_data("list_shadow_log_entries", {"employee": FOREMAN})
		data = self.tool_data("get_shadow_log_entry", {"name": feed["entries"][0]["name"]})
		self.assertEqual(data["snapshot"]["total_accepted"], 1)
		self.assertEqual(data["event_type"], "Bucket Session Synced")

	def test_a_resynced_batch_does_not_duplicate_the_copies(self):
		"""A phone that timed out and resent is the ordinary case on this route —
		it is why `duplicate_count` exists — and it must not put a second copy of
		one morning in front of the same foreman."""
		self.tool_data("sync_bucket_entries", {"entries": [self._entry()]})
		second = self.tool_data("sync_bucket_entries", {"entries": [self._entry()]})
		self.assertEqual(second["duplicate_count"], 1)
		self.assertEqual(len(STORE.rows("Shadow Log Entry")), 3)

	def test_a_sync_still_succeeds_when_the_feed_cannot_write(self):
		"""THE CONTRACT. The captures are the picker's piece-rate record and no
		supervisory convenience gets to veto them."""
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Shadow Log Entry")
		result = self.tool_data("sync_bucket_entries", {"entries": [self._entry()]})
		self.assertEqual(result["created_count"], 1)
		self.assertTrue(result["shadow_log"][0]["skipped"])

	def test_a_sync_with_the_feed_switched_off_writes_no_copies(self):
		self.configure(enabled=1, shadow_log_feed_enabled=0, **self.SYNC_ON)
		result = self.tool_data("sync_bucket_entries", {"entries": [self._entry()]})
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(STORE.rows("Shadow Log Entry"), [])

	def test_closing_a_shift_raises_a_copy_up_the_foremans_chain(self):
		"""The SUBJECT is the foreman, not the crew: a shift is the record they
		signed. A copy per crew member would put one afternoon in front of a
		manager fourteen times."""
		STORE.seed(
			"Farm Shift",
			[
				{
					"name": "SHIFT-1",
					"foreman": FOREMAN,
					"foreman_name": "Luis Ortega",
					"company": MAIN,
					"status": "Open",
					"start_datetime": "2026-08-16 06:00:00",
					"crew": [{"employee": PICKER, "joined_at": "2026-08-16 06:00:00"}],
				}
			],
		)
		result = self.tool_data(
			"end_shift",
			{
				"shift": "SHIFT-1",
				"end_datetime": "2026-08-16 15:00:00",
				"supervisor_signature_file_token": "/files/shift-signature.png",
			},
		)
		self.assertIn("shadow_log", result)
		self.assertEqual(
			[row["recipient"] for row in result["shadow_log"]["raised"]],
			[MANAGER, OWNER],
		)
		self.assertEqual(
			{row["level"] for row in result["shadow_log"]["raised"]},
			{1, 2},
		)

	def test_the_shift_copy_names_the_event_and_freezes_the_close(self):
		STORE.seed(
			"Farm Shift",
			[
				{
					"name": "SHIFT-1",
					"foreman": FOREMAN,
					"foreman_name": "Luis Ortega",
					"company": MAIN,
					"status": "Open",
					"start_datetime": "2026-08-16 06:00:00",
					"crew": [{"employee": PICKER, "joined_at": "2026-08-16 06:00:00"}],
				}
			],
		)
		self.tool_data(
			"end_shift",
			{
				"shift": "SHIFT-1",
				"end_datetime": "2026-08-16 15:00:00",
				"supervisor_signature_file_token": "/files/shift-signature.png",
			},
		)
		feed = self.tool_data("list_shadow_log_entries", {"employee": MANAGER})
		self.assertEqual(feed["entries"][0]["event_type"], "Shift Closed")
		data = self.tool_data("get_shadow_log_entry", {"name": feed["entries"][0]["name"]})
		self.assertEqual(data["snapshot"]["_shift_hours"], 9.0)
		self.assertTrue(data["snapshot_intact"])
