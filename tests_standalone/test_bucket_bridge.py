# SPDX-License-Identifier: MIT
"""BucketLog → ERPNext Piecework Bridge — v0.44.0.

`bucket_bridge.py` imports nothing from `frappe` and reads no database, the
same contract `model_registry.py` is tested under in `test_model_registry.py`:
every function takes a plain dict (or a list of them) in and returns a plain
dict (or list) out, so a `unittest.TestCase` with no setup is the honest test
for that. `SyncingEntries` is the impure half — sync, dedup, badge resolution
and session totals through the real tool and a fake site — the same way
`test_payroll_gl.py`'s `PayrollGLTestCase` exercises `tools/payroll_gl.py`.

SIX CLAIMS, PLUS THE REGISTRATION ITSELF.

1. `ValidatingEntries` — `validate_bucket_entry` finds exactly the reasons a
   capture cannot become a Bucket Log Entry, and reports none when there are
   none.
2. `ResolvingBadges` — `resolve_badge_to_employee` reads a pre-fetched map
   exactly, with no normalisation that could collide two different badges.
3. `AggregatingSessions` — `aggregate_session` counts accepted and rejected
   correctly with entries mixed and out of order, and never divides by zero.
4. `ReshapingForPayroll` — `entries_to_payroll_shape` keeps only Accepted,
   attributed entries, and what it produces is EXACTLY what
   `payroll_integration._piece_units_for` reads as bucket units — the contract
   the whole bridge exists to keep.

4b. `TheGateIsBinary` — a bucket is full or it is not: Accepted is 1, Rejected
   is 0, and pay is the Accepted count times the piece rate. A capture the model
   judged 51% full and one it judged 99% full are worth the same thing, a
   capture with no coverage at all is a whole bucket, and `coverage_percent`
   reaches neither the payroll row nor `_UNIT_KEYS`. Asserted against
   `payroll_integration`'s own tuple rather than a copy of it, because the
   failure mode is silent: pay would still come out and would be wrong by a
   fraction nobody could see without recomputing a period by hand.
5. `LinkingToAShift` — `link_entries_to_shift` sets shift/status on everything
   except an entry already Paid, and returns new dicts rather than mutating.
6. `AttachingOntoShifts` — `payroll_integration.attach_bucket_log_entries`
   matches entries onto the shift each employee worked that day and reports
   what matched nothing.
7. `SyncingEntries` — the tool layer: sync creates records, a resynced
   `entry_uuid` deduplicates rather than duplicating, an invalid entry is
   skipped without failing the batch, a badge resolves to an employee, and a
   session's totals are computed from its own entries.
8. `ToolRegistration` — the eight tools exist in `registry.TOOLS`, split five
   read / three write.
"""

import unittest

from erpnext_mcp import bucket_bridge as engine
from erpnext_mcp import payroll_integration

from .fixtures import MAIN, SeededTestCase
from .harness import STORE


def entry(**overrides):
	base = {
		"entry_uuid": "11111111-1111-1111-1111-111111111111",
		"session_uuid": "S-1",
		"company": "Highland Orchards",
		"worker_badge": "QR-0001",
		"timestamp": "2026-06-01 08:00:00",
		"verdict": "Accepted",
		"coverage_percent": 92.5,
	}
	base.update(overrides)
	return base


class ValidatingEntries(unittest.TestCase):
	def test_a_complete_entry_is_valid(self):
		self.assertEqual(engine.validate_bucket_entry(entry()), [])

	def test_entry_uuid_is_required(self):
		errors = engine.validate_bucket_entry(entry(entry_uuid=""))
		self.assertTrue(any("entry_uuid" in e for e in errors))

	def test_company_is_required(self):
		errors = engine.validate_bucket_entry(entry(company=""))
		self.assertTrue(any("company" in e for e in errors))

	def test_timestamp_is_required(self):
		errors = engine.validate_bucket_entry(entry(timestamp=""))
		self.assertTrue(any("timestamp is required" in e for e in errors))

	def test_an_unparseable_timestamp_is_refused(self):
		errors = engine.validate_bucket_entry(entry(timestamp="not-a-date"))
		self.assertTrue(any("timestamp" in e for e in errors))

	def test_verdict_is_required(self):
		errors = engine.validate_bucket_entry(entry(verdict=""))
		self.assertTrue(any("verdict is required" in e for e in errors))

	def test_verdict_must_be_a_known_value(self):
		errors = engine.validate_bucket_entry(entry(verdict="Maybe"))
		self.assertTrue(any("verdict" in e for e in errors))

	def test_a_badge_alone_is_enough_to_identify_the_worker(self):
		self.assertEqual(engine.validate_bucket_entry(entry(worker_badge="QR-1", employee="")), [])

	def test_an_already_resolved_employee_alone_is_enough(self):
		self.assertEqual(
			engine.validate_bucket_entry(entry(worker_badge="", employee="HR-EMP-00001")),
			[],
		)

	def test_neither_badge_nor_employee_is_refused(self):
		errors = engine.validate_bucket_entry(entry(worker_badge="", employee=""))
		self.assertTrue(any("worker_badge or employee" in e for e in errors))

	def test_coverage_percent_in_range_is_valid(self):
		self.assertEqual(engine.validate_bucket_entry(entry(coverage_percent=0.0)), [])
		self.assertEqual(engine.validate_bucket_entry(entry(coverage_percent=100.0)), [])

	def test_coverage_percent_out_of_range_is_refused(self):
		errors = engine.validate_bucket_entry(entry(coverage_percent=150))
		self.assertTrue(any("coverage_percent" in e for e in errors))

	def test_coverage_percent_absent_is_not_refused(self):
		self.assertEqual(engine.validate_bucket_entry(entry(coverage_percent="")), [])

	def test_an_unrecognised_status_is_refused(self):
		errors = engine.validate_bucket_entry(entry(status="Retired"))
		self.assertTrue(any("status" in e for e in errors))

	def test_every_missing_required_field_is_reported_at_once(self):
		errors = engine.validate_bucket_entry({})
		for expected in ("entry_uuid", "company", "timestamp", "verdict"):
			with self.subTest(field=expected):
				self.assertTrue(any(expected in e for e in errors))

	def test_an_empty_dict_does_not_raise(self):
		self.assertIsInstance(engine.validate_bucket_entry({}), list)

	def test_none_does_not_raise(self):
		self.assertIsInstance(engine.validate_bucket_entry(None), list)


class ResolvingBadges(unittest.TestCase):
	def test_a_mapped_badge_resolves(self):
		self.assertEqual(engine.resolve_badge_to_employee("QR-1", {"QR-1": "HR-EMP-00001"}), "HR-EMP-00001")

	def test_an_unmapped_badge_resolves_to_none(self):
		self.assertIsNone(engine.resolve_badge_to_employee("QR-9", {"QR-1": "HR-EMP-00001"}))

	def test_an_empty_badge_resolves_to_none(self):
		self.assertIsNone(engine.resolve_badge_to_employee("", {"QR-1": "HR-EMP-00001"}))

	def test_an_empty_map_resolves_to_none(self):
		self.assertIsNone(engine.resolve_badge_to_employee("QR-1", {}))

	def test_none_map_does_not_raise(self):
		self.assertIsNone(engine.resolve_badge_to_employee("QR-1", None))

	def test_matching_is_exact_not_case_insensitive(self):
		self.assertIsNone(engine.resolve_badge_to_employee("qr-1", {"QR-1": "HR-EMP-00001"}))


class AggregatingSessions(unittest.TestCase):
	def test_mixed_accepted_and_rejected_are_counted_separately(self):
		entries = [
			entry(verdict="Accepted", timestamp="2026-06-01 08:00:00"),
			entry(verdict="Accepted", timestamp="2026-06-01 08:05:00"),
			entry(verdict="Rejected", timestamp="2026-06-01 08:10:00"),
		]
		totals = engine.aggregate_session(entries)
		self.assertEqual(totals["total_accepted"], 2)
		self.assertEqual(totals["total_rejected"], 1)
		self.assertEqual(totals["total_entries"], 3)
		self.assertAlmostEqual(totals["acceptance_rate"], 2 / 3, places=4)

	def test_an_empty_session_has_a_zero_rate_not_a_division_error(self):
		totals = engine.aggregate_session([])
		self.assertEqual(totals["acceptance_rate"], 0.0)
		self.assertEqual(totals["total_entries"], 0)

	def test_started_and_ended_are_the_earliest_and_latest_timestamp_regardless_of_order(self):
		entries = [
			entry(timestamp="2026-06-01 08:10:00"),
			entry(timestamp="2026-06-01 08:00:00"),
			entry(timestamp="2026-06-01 08:05:00"),
		]
		totals = engine.aggregate_session(entries)
		self.assertEqual(totals["started_at"], "2026-06-01 08:00:00")
		self.assertEqual(totals["ended_at"], "2026-06-01 08:10:00")

	def test_duration_is_computed_from_the_span(self):
		entries = [entry(timestamp="2026-06-01 08:00:00"), entry(timestamp="2026-06-01 08:30:00")]
		totals = engine.aggregate_session(entries)
		self.assertEqual(totals["duration_minutes"], 30.0)

	def test_a_single_entry_has_zero_duration(self):
		totals = engine.aggregate_session([entry(timestamp="2026-06-01 08:00:00")])
		self.assertEqual(totals["duration_minutes"], 0.0)

	def test_entries_with_no_timestamp_do_not_raise(self):
		totals = engine.aggregate_session([{"verdict": "Accepted"}])
		self.assertEqual(totals["total_accepted"], 1)
		self.assertIsNone(totals["started_at"])

	def test_an_unrecognised_verdict_counts_toward_neither(self):
		totals = engine.aggregate_session([entry(verdict="Whatever")])
		self.assertEqual(totals["total_accepted"], 0)
		self.assertEqual(totals["total_rejected"], 0)
		self.assertEqual(totals["total_entries"], 1)


class ReshapingForPayroll(unittest.TestCase):
	def test_only_accepted_entries_survive(self):
		entries = [
			entry(entry_uuid="a", verdict="Accepted", employee="HR-EMP-00001"),
			entry(entry_uuid="b", verdict="Rejected", employee="HR-EMP-00001"),
		]
		rows = engine.entries_to_payroll_shape(entries)
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["employee"], "HR-EMP-00001")

	def test_an_entry_with_no_resolved_employee_is_dropped(self):
		rows = engine.entries_to_payroll_shape([entry(verdict="Accepted", employee="")])
		self.assertEqual(rows, [])

	def test_one_row_per_accepted_entry_no_unit_count_key(self):
		entries = [
			entry(entry_uuid="a", verdict="Accepted", employee="HR-EMP-00001"),
			entry(entry_uuid="b", verdict="Accepted", employee="HR-EMP-00001"),
		]
		rows = engine.entries_to_payroll_shape(entries)
		self.assertEqual(len(rows), 2)
		for row in rows:
			self.assertNotIn("piece_units", row)
			self.assertNotIn("units", row)

	def test_an_empty_list_produces_an_empty_list(self):
		self.assertEqual(engine.entries_to_payroll_shape([]), [])

	def test_matches_what_piece_units_for_expects(self):
		"""THE CONTRACT THE WHOLE BRIDGE EXISTS TO KEEP: what this produces,
		attached to a shift as `bucket_logs`, is read by
		`payroll_integration._piece_units_for` as one unit per accepted entry —
		no change to that module was needed."""
		entries = [
			entry(entry_uuid="a", verdict="Accepted", employee="HR-EMP-00001"),
			entry(entry_uuid="b", verdict="Accepted", employee="HR-EMP-00001"),
			entry(entry_uuid="c", verdict="Rejected", employee="HR-EMP-00001"),
			entry(entry_uuid="d", verdict="Accepted", employee="HR-EMP-00002"),
		]
		shift = {"name": "SHIFT-1", "bucket_logs": engine.entries_to_payroll_shape(entries)}
		self.assertEqual(payroll_integration._piece_units_for(shift, "HR-EMP-00001"), 2.0)
		self.assertEqual(payroll_integration._piece_units_for(shift, "HR-EMP-00002"), 1.0)
		self.assertEqual(payroll_integration._piece_units_for(shift, "HR-EMP-00003"), 0.0)


class TheGateIsBinary(unittest.TestCase):
	"""A bucket is full or it is not. There is no partial credit anywhere.

	THE RULE, WRITTEN OUT ONCE. The phone runs the model, reaches a verdict, and
	the only thing that crosses to this app is which way it went:

	    Accepted → 1 bucket        Rejected → 0 buckets
	    piecework pay = Accepted bucket count × piece rate

	`coverage_percent` is the model's own record of WHY the gate went that way.
	It is evidence, it is worth keeping for anybody auditing a model version,
	and it buys nothing. These tests are what stop it — or a future column named
	`units` — quietly becoming a multiplier, because the failure would be
	invisible: pay would still come out, and it would be wrong by a fraction
	nobody could see without recomputing a period by hand.

	They are written against `payroll_integration._UNIT_KEYS` ITSELF rather than
	against a copy of it, so a key added on that side and a row grown on this
	side cannot drift into agreement without failing here first.
	"""

	def _accepted(self, coverage, **overrides):
		return entry(verdict="Accepted", employee="HR-EMP-00001", coverage_percent=coverage, **overrides)

	def test_a_barely_full_bucket_and_a_brimming_one_are_worth_the_same(self):
		"""THE ASSERTION TIM'S RULE LIVES OR DIES ON. 51% and 99% are both one
		bucket, because both are Accepted and the gate already ran."""
		shift = {
			"bucket_logs": engine.entries_to_payroll_shape(
				[self._accepted(51.0, entry_uuid="a"), self._accepted(99.0, entry_uuid="b")]
			)
		}
		self.assertEqual(payroll_integration._piece_units_for(shift, "HR-EMP-00001"), 2.0)

	def test_a_rejected_bucket_is_worth_nothing_however_full_the_model_thought_it_was(self):
		shift = {
			"bucket_logs": engine.entries_to_payroll_shape(
				[entry(verdict="Rejected", employee="HR-EMP-00001", coverage_percent=99.9)]
			)
		}
		self.assertEqual(payroll_integration._piece_units_for(shift, "HR-EMP-00001"), 0.0)

	def test_a_capture_with_no_coverage_at_all_is_still_a_whole_bucket(self):
		"""Coverage is not required and its absence costs nothing — which is the
		same statement as "it is not an input to pay", made from the other side."""
		shift = {
			"bucket_logs": engine.entries_to_payroll_shape(
				[entry(verdict="Accepted", employee="HR-EMP-00001", coverage_percent=None)]
			)
		}
		self.assertEqual(payroll_integration._piece_units_for(shift, "HR-EMP-00001"), 1.0)

	def test_units_are_always_a_whole_number_of_buckets(self):
		"""Ten accepted captures at ten different coverages are ten buckets, not
		some sum of tenths."""
		coverages = [50.5, 61.0, 72.25, 80.0, 88.8, 91.0, 94.2, 96.5, 99.0, 100.0]
		rows = engine.entries_to_payroll_shape(
			[self._accepted(value, entry_uuid=f"e{index}") for index, value in enumerate(coverages)]
		)
		units = payroll_integration._piece_units_for({"bucket_logs": rows}, "HR-EMP-00001")
		self.assertEqual(units, 10.0)
		self.assertEqual(units, float(int(units)), "a fractional bucket means partial credit crept in")

	def test_coverage_percent_never_reaches_the_payroll_row(self):
		rows = engine.entries_to_payroll_shape([self._accepted(94.2)])
		self.assertNotIn("coverage_percent", rows[0])

	def test_the_payroll_row_carries_no_count_column_of_any_spelling(self):
		"""Checked against payroll_integration's OWN tuple. A key added there
		that this row happens to emit would turn one bucket into whatever that
		column held, and nothing else in either module would notice."""
		rows = engine.entries_to_payroll_shape([self._accepted(94.2)])
		self.assertEqual(set(rows[0]), {"employee", "entry_uuid"})
		for key in payroll_integration._UNIT_KEYS:
			with self.subTest(unit_key=key):
				self.assertNotIn(key, rows[0])

	def test_coverage_is_not_a_unit_column_on_the_payroll_side_either(self):
		"""The other direction: nobody may add coverage to `_UNIT_KEYS` and have
		the production loader in `tools/payroll.py` start multiplying by it."""
		self.assertNotIn("coverage_percent", payroll_integration._UNIT_KEYS)
		self.assertNotIn("coverage", payroll_integration._UNIT_KEYS)

	def test_a_row_that_did_carry_a_count_would_be_read_as_that_count(self):
		"""Not a test of this app's behaviour — a test of WHY the two above
		matter. `_row_units` honours a count column where one exists, so the
		whole binary guarantee rests on the payroll row not having one."""
		self.assertEqual(payroll_integration._row_units({"employee": "x"}), 1.0)
		self.assertEqual(payroll_integration._row_units({"employee": "x", "units": 0.51}), 0.51)


class LinkingToAShift(unittest.TestCase):
	def test_shift_and_status_are_set(self):
		updated = engine.link_entries_to_shift([entry(entry_uuid="a")], "SHIFT-1")
		self.assertEqual(updated[0]["shift"], "SHIFT-1")
		self.assertEqual(updated[0]["status"], engine.STATUS_LINKED)

	def test_an_already_paid_entry_is_left_untouched(self):
		paid = entry(entry_uuid="a", status=engine.STATUS_PAID, shift="SHIFT-OLD")
		updated = engine.link_entries_to_shift([paid], "SHIFT-NEW")
		self.assertEqual(updated[0]["status"], engine.STATUS_PAID)
		self.assertEqual(updated[0]["shift"], "SHIFT-OLD")

	def test_the_input_dicts_are_not_mutated(self):
		original = entry(entry_uuid="a")
		engine.link_entries_to_shift([original], "SHIFT-1")
		self.assertNotIn("shift", original)

	def test_an_empty_list_produces_an_empty_list(self):
		self.assertEqual(engine.link_entries_to_shift([], "SHIFT-1"), [])


class AttachingOntoShifts(unittest.TestCase):
	def test_an_entry_matches_the_shift_its_employee_worked_that_day(self):
		shifts = [
			{
				"name": "SHIFT-1",
				"start_datetime": "2026-06-01 06:00:00",
				"crew": [{"employee": "HR-EMP-00001"}],
			}
		]
		entries = [
			entry(
				entry_uuid="a", verdict="Accepted", employee="HR-EMP-00001", timestamp="2026-06-01 09:00:00"
			)
		]
		updated, unmatched = payroll_integration.attach_bucket_log_entries(shifts, entries)
		self.assertEqual(unmatched, [])
		self.assertEqual(len(updated[0]["bucket_logs"]), 1)
		self.assertEqual(updated[0]["bucket_logs"][0]["employee"], "HR-EMP-00001")

	def test_an_entry_on_a_day_with_no_matching_shift_is_returned_unmatched(self):
		shifts = [
			{
				"name": "SHIFT-1",
				"start_datetime": "2026-06-01 06:00:00",
				"crew": [{"employee": "HR-EMP-00001"}],
			}
		]
		entries = [
			entry(
				entry_uuid="a", verdict="Accepted", employee="HR-EMP-00001", timestamp="2026-06-02 09:00:00"
			)
		]
		updated, unmatched = payroll_integration.attach_bucket_log_entries(shifts, entries)
		self.assertNotIn("bucket_logs", updated[0])
		self.assertEqual(len(unmatched), 1)

	def test_the_input_shifts_are_not_mutated(self):
		shifts = [
			{
				"name": "SHIFT-1",
				"start_datetime": "2026-06-01 06:00:00",
				"crew": [{"employee": "HR-EMP-00001"}],
			}
		]
		entries = [
			entry(
				entry_uuid="a", verdict="Accepted", employee="HR-EMP-00001", timestamp="2026-06-01 09:00:00"
			)
		]
		payroll_integration.attach_bucket_log_entries(shifts, entries)
		self.assertNotIn("bucket_logs", shifts[0])

	def test_no_entries_leaves_shifts_unmatched_and_untouched(self):
		shifts = [
			{
				"name": "SHIFT-1",
				"start_datetime": "2026-06-01 06:00:00",
				"crew": [{"employee": "HR-EMP-00001"}],
			}
		]
		updated, unmatched = payroll_integration.attach_bucket_log_entries(shifts, [])
		self.assertEqual(unmatched, [])
		self.assertNotIn("bucket_logs", updated[0])


# ── The tool layer: sync, dedup, badge resolution, session totals ─────────

ENTRY_DOCTYPE = "Bucket Log Entry"
EMP = "HR-EMP-00001"
EMP_NAME = "Ana Reyes"
BADGE = "QR-0001"

_ON = {
	f"allow_{name}": 1 for name in ("sync_bucket_entries", "link_badge_to_employee", "link_entries_to_shift")
}


def _entry(**overrides):
	return entry(company=MAIN, **overrides)


class BucketLogToolTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **_ON)
		STORE.seed(
			"Employee",
			[
				{
					"name": EMP,
					"employee_name": EMP_NAME,
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-01-15",
				},
			],
		)


class SyncingEntries(BucketLogToolTestCase):
	def test_a_valid_entry_is_created(self):
		result = self.tool_data("sync_bucket_entries", {"entries": [_entry()]})
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(result["duplicate_count"], 0)
		self.assertEqual(len(STORE.rows(ENTRY_DOCTYPE)), 1)

	def test_resyncing_the_same_entry_uuid_deduplicates_rather_than_duplicating(self):
		self.tool_data("sync_bucket_entries", {"entries": [_entry()]})
		second = self.tool_data("sync_bucket_entries", {"entries": [_entry()]})
		self.assertEqual(second["created_count"], 0)
		self.assertEqual(second["duplicate_count"], 1)
		self.assertEqual(len(STORE.rows(ENTRY_DOCTYPE)), 1)

	def test_an_invalid_entry_is_skipped_without_failing_the_batch(self):
		good = _entry()
		bad = _entry(entry_uuid="22222222-2222-2222-2222-222222222222", verdict="Maybe")
		result = self.tool_data("sync_bucket_entries", {"entries": [good, bad]})
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(result["invalid_count"], 1)
		self.assertEqual(result["invalid"][0]["entry_uuid"], bad["entry_uuid"])

	def test_worker_badge_resolves_to_employee_via_the_badge_map(self):
		self.tool_data("link_badge_to_employee", {"badge_id": BADGE, "employee": EMP, "company": MAIN})
		result = self.tool_data("sync_bucket_entries", {"entries": [_entry()]})
		self.assertEqual(result["created"][0]["employee"], EMP)

	def test_an_unresolved_badge_leaves_employee_empty_rather_than_refusing_the_entry(self):
		result = self.tool_data("sync_bucket_entries", {"entries": [_entry()]})
		self.assertEqual(result["created_count"], 1)
		self.assertIsNone(result["created"][0]["employee"])

	def test_session_totals_are_computed_from_the_synced_entries(self):
		entries = [
			_entry(
				entry_uuid="a1111111-1111-1111-1111-111111111111",
				verdict="Accepted",
				timestamp="2026-06-01 08:00:00",
			),
			_entry(
				entry_uuid="a2222222-2222-2222-2222-222222222222",
				verdict="Accepted",
				timestamp="2026-06-01 08:05:00",
			),
			_entry(
				entry_uuid="a3333333-3333-3333-3333-333333333333",
				verdict="Rejected",
				timestamp="2026-06-01 08:10:00",
			),
		]
		self.tool_data("sync_bucket_entries", {"entries": entries})
		session = self.tool_data("get_bucket_session", {"session": "S-1"})
		self.assertEqual(session["total_accepted"], 2)
		self.assertEqual(session["total_rejected"], 1)
		self.assertAlmostEqual(session["acceptance_rate"], 2 / 3, places=3)

	def test_a_second_batch_into_the_same_session_updates_its_totals(self):
		self.tool_data(
			"sync_bucket_entries",
			{
				"entries": [
					_entry(
						entry_uuid="b1111111-1111-1111-1111-111111111111", timestamp="2026-06-01 08:00:00"
					),
				]
			},
		)
		self.tool_data(
			"sync_bucket_entries",
			{
				"entries": [
					_entry(
						entry_uuid="b2222222-2222-2222-2222-222222222222", timestamp="2026-06-01 08:05:00"
					),
				]
			},
		)
		session = self.tool_data("get_bucket_session", {"session": "S-1"})
		self.assertEqual(session["total_accepted"], 2)

	def test_the_switch_off_refuses_the_call(self):
		self.configure(enabled=1)
		self.tool_error("sync_bucket_entries", {"entries": [_entry()]})


class ToolRegistration(unittest.TestCase):
	def setUp(self):
		from erpnext_mcp import registry

		self.registry = registry

	def test_all_eight_tools_are_registered(self):
		for name in (
			"sync_bucket_entries",
			"list_bucket_entries",
			"get_bucket_session",
			"list_bucket_sessions",
			"link_badge_to_employee",
			"link_entries_to_shift",
			"get_piecework_summary",
			"reconcile_bucket_payroll",
		):
			with self.subTest(tool=name):
				self.assertIn(name, self.registry.TOOLS)

	def test_five_are_read_and_three_are_write(self):
		reads = {
			"list_bucket_entries",
			"get_bucket_session",
			"list_bucket_sessions",
			"get_piecework_summary",
			"reconcile_bucket_payroll",
		}
		writes = {"sync_bucket_entries", "link_badge_to_employee", "link_entries_to_shift"}
		for name in reads:
			with self.subTest(tool=name):
				self.assertFalse(self.registry.TOOLS[name]["mutating"])
		for name in writes:
			with self.subTest(tool=name):
				self.assertTrue(self.registry.TOOLS[name]["mutating"])


if __name__ == "__main__":
	unittest.main()
