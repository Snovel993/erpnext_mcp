# SPDX-License-Identifier: MIT
"""Container-Agnostic Fill Pipeline — v0.68.0.

SIX CLAIMS.

1. `SettingAThreshold` — `update_fill_threshold` creates version 1, a second
   call bumps to version 2 and writes a Fill Threshold Change Log row with the
   old and new bounds, omitting `upper_bound_pct` clears an existing one (a
   full definition, not a patch), and the bound and role checks refuse by name.

2. `ReadingAThreshold` — `get_fill_thresholds` answers `configured: false` for
   a container type nobody has set one for, and the current row otherwise.

3. `TheDetermination` — `get_fill_determination` computes a fill percentage
   from `mask_area_px`/`container_area_px` when both are present, falls back
   to `coverage_percent` otherwise, applies the right threshold, and answers
   `Unknown` when neither a percentage nor a threshold is available. The
   session variant returns one determination per entry plus a tally.

4. `TheChangeLog` — `list_fill_threshold_changes` lists every change with its
   acknowledgment count.

5. `Acknowledging` — `acknowledge_threshold_update` records a checker's
   sign-off, is idempotent, and refuses an unknown employee or a container
   type with no threshold on record.

6. `PendingAcknowledgments` — `list_pending_threshold_acknowledgments` reports
   every Active Employee designated Checker who has not signed off on the
   CURRENT version, and shrinks as they do.
"""

import frappe

from erpnext_mcp import bucket_bridge

from .fixtures import MAIN, SeededTestCase
from .harness import ROLES, STORE, set_roles

#: A snapshot of Administrator's default roles, taken at import time before any
#: test mutates the (global, never auto-reset) ROLES map. Includes System
#: Manager, which is enough to pass `require_foreman_role` — the same pattern
#: `test_kpi_framework.py`/`test_windowed_reports.py` use for the same reason.
SHIPPED_ROLES = list(ROLES["Administrator"])

ON = {
	f"allow_{name}": 1
	for name in (
		"get_fill_determination",
		"get_fill_thresholds",
		"update_fill_threshold",
		"list_fill_threshold_changes",
		"acknowledge_threshold_update",
		"list_pending_threshold_acknowledgments",
	)
}


class FillPipelineTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		set_roles("Administrator", SHIPPED_ROLES)
		self.configure(enabled=1, **ON)

	# ── fixtures ──────────────────────────────────────────────────────────
	def a_checker(self, name="HR-EMP-CHECK1", employee_name="Chuck Checker", status="Active"):
		if not STORE.get_raw("Designation", "Checker"):
			STORE.seed("Designation", [{"name": "Checker", "designation_name": "Checker"}])
		STORE.seed(
			"Employee",
			[
				{
					"name": name,
					"employee_name": employee_name,
					"designation": "Checker",
					"status": status,
					"company": MAIN,
					"date_of_joining": "2026-01-01",
				}
			],
		)
		return name

	def an_entry(self, name="BLE-TEST-0001", **overrides):
		payload = {
			"doctype": "Bucket Log Entry",
			"name": name,
			"entry_uuid": name,
			"company": MAIN,
			"verdict": "Accepted",
			"timestamp": "2026-08-01 10:00:00",
			"status": "Pending",
			"container_type": "cherry_bucket",
		}
		payload.update(overrides)
		STORE.seed("Bucket Log Entry", [payload])
		return name

	def a_session_of_entries(self, session_uuid="SESSION-TEST-1"):
		STORE.seed(
			"Bucket Log Session",
			[
				{
					"doctype": "Bucket Log Session",
					"name": session_uuid,
					"session_uuid": session_uuid,
					"company": MAIN,
					"status": "Open",
				}
			],
		)
		self.an_entry(
			"BLE-TEST-S1", entry_uuid="BLE-TEST-S1", session_uuid=session_uuid,
			mask_area_px=45000.0, container_area_px=48000.0,
		)
		self.an_entry(
			"BLE-TEST-S2", entry_uuid="BLE-TEST-S2", session_uuid=session_uuid,
			mask_area_px=30000.0, container_area_px=48000.0,
		)
		return session_uuid


# ── 1 ─────────────────────────────────────────────────────────────────────
class SettingAThreshold(FillPipelineTestCase):
	def test_the_first_call_creates_version_one(self):
		data = self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		self.assertEqual(data["version"], 1)
		self.assertEqual(data["lower_bound_pct"], 85.0)
		self.assertIsNone(data["upper_bound_pct"])
		self.assertEqual(data["last_updated_by"], "Administrator")

	def test_a_second_call_bumps_the_version_and_logs_the_change(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "pear_bin", "company": MAIN, "lower_bound_pct": 80.0, "upper_bound_pct": 110.0},
		)
		data = self.tool_data(
			"update_fill_threshold",
			{"container_type": "pear_bin", "company": MAIN, "lower_bound_pct": 82.0, "upper_bound_pct": 115.0},
		)
		self.assertEqual(data["version"], 2)
		changes = self.tool_data("list_fill_threshold_changes", {"container_type": "pear_bin"})["changes"]
		self.assertEqual(len(changes), 2)
		newest = changes[0]
		self.assertEqual(newest["version"], 2)
		self.assertEqual(newest["old_lower_bound_pct"], 80.0)
		self.assertEqual(newest["new_lower_bound_pct"], 82.0)
		self.assertEqual(newest["old_upper_bound_pct"], 110.0)
		self.assertEqual(newest["new_upper_bound_pct"], 115.0)

	def test_omitting_upper_bound_clears_an_existing_one(self):
		"""A full definition, not a patch — this is what makes a cherry bucket
		stay unable to overfill: nobody ever sends an upper bound for one."""
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "pear_bin", "company": MAIN, "lower_bound_pct": 80.0, "upper_bound_pct": 110.0},
		)
		data = self.tool_data(
			"update_fill_threshold",
			{"container_type": "pear_bin", "company": MAIN, "lower_bound_pct": 80.0},
		)
		self.assertIsNone(data["upper_bound_pct"])

	def test_lower_bound_is_required(self):
		message = self.tool_error(
			"update_fill_threshold", {"container_type": "cherry_bucket", "company": MAIN}
		)
		self.assertIn("lower_bound_pct is required", message)

	def test_an_upper_bound_not_greater_than_the_lower_is_refused(self):
		message = self.tool_error(
			"update_fill_threshold",
			{"container_type": "pear_bin", "company": MAIN, "lower_bound_pct": 80.0, "upper_bound_pct": 80.0},
		)
		self.assertIn("must be greater than", message)

	def test_a_negative_lower_bound_is_refused(self):
		message = self.tool_error(
			"update_fill_threshold", {"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": -1}
		)
		self.assertIn("must not be negative", message)

	def test_a_caller_without_the_foreman_role_is_refused(self):
		set_roles("Administrator", ["Sales User"])
		message = self.tool_error(
			"update_fill_threshold", {"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0}
		)
		self.assertIn("Foreman", message)

	def test_the_tool_is_behind_its_own_switch(self):
		self.configure(enabled=1, **{**ON, "allow_update_fill_threshold": 0})
		message = self.tool_error(
			"update_fill_threshold", {"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0}
		)
		self.assertIn("is switched off on this site", message)


# ── 2 ─────────────────────────────────────────────────────────────────────
class ReadingAThreshold(FillPipelineTestCase):
	def test_an_unconfigured_container_type_answers_configured_false(self):
		data = self.tool_data("get_fill_thresholds", {"container_type": "pear_bin", "company": MAIN})
		self.assertFalse(data["configured"])
		self.assertIsNone(data["lower_bound_pct"])

	def test_the_current_threshold_is_returned(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		data = self.tool_data("get_fill_thresholds", {"container_type": "cherry_bucket", "company": MAIN})
		self.assertTrue(data["configured"])
		self.assertEqual(data["lower_bound_pct"], 85.0)
		self.assertEqual(data["version"], 1)


# ── 3 ─────────────────────────────────────────────────────────────────────
class TheDetermination(FillPipelineTestCase):
	def test_it_computes_from_pixel_areas_and_passes_within_the_band(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		self.an_entry(mask_area_px=45000.0, container_area_px=48000.0)  # 93.75%
		data = self.tool_data("get_fill_determination", {"entry": "BLE-TEST-0001"})
		self.assertEqual(data["computed_fill_percentage"], 93.75)
		self.assertEqual(data["result"], "Pass")
		self.assertIn("÷", data["math_explanation"])

	def test_underfill_and_overfill_are_reported(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "pear_bin", "company": MAIN, "lower_bound_pct": 80.0, "upper_bound_pct": 110.0},
		)
		self.an_entry(
			"BLE-UNDER", entry_uuid="BLE-UNDER", container_type="pear_bin",
			mask_area_px=30000.0, container_area_px=48000.0,  # 62.5%
		)
		self.an_entry(
			"BLE-OVER", entry_uuid="BLE-OVER", container_type="pear_bin",
			mask_area_px=58000.0, container_area_px=48000.0,  # 120.83%
		)
		under = self.tool_data("get_fill_determination", {"entry": "BLE-UNDER"})
		over = self.tool_data("get_fill_determination", {"entry": "BLE-OVER"})
		self.assertEqual(under["result"], "Underfill")
		self.assertEqual(over["result"], "Overfill")

	def test_a_cherry_bucket_cannot_overfill(self):
		"""No upper bound was ever set, so nothing above the lower bound fails —
		not because the code special-cases 'cherry_bucket', but because nobody
		called update_fill_threshold with an upper_bound_pct for it."""
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		self.an_entry(mask_area_px=96000.0, container_area_px=48000.0)  # 200%
		data = self.tool_data("get_fill_determination", {"entry": "BLE-TEST-0001"})
		self.assertEqual(data["result"], "Pass")

	def test_it_falls_back_to_coverage_percent_when_no_areas_were_sent(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		self.an_entry(coverage_percent=90.0)
		data = self.tool_data("get_fill_determination", {"entry": "BLE-TEST-0001"})
		self.assertIsNone(data["computed_fill_percentage"])
		self.assertEqual(data["fill_percentage"], 90.0)
		self.assertEqual(data["result"], "Pass")
		self.assertIsNone(data["math_explanation"])

	def test_no_percentage_at_all_is_unknown_not_a_refusal(self):
		self.an_entry(container_type="", coverage_percent=None)
		data = self.tool_data("get_fill_determination", {"entry": "BLE-TEST-0001"})
		self.assertEqual(data["result"], "Unknown")

	def test_a_percentage_with_no_threshold_set_yet_is_unknown(self):
		self.an_entry(coverage_percent=90.0)  # cherry_bucket, no threshold created
		data = self.tool_data("get_fill_determination", {"entry": "BLE-TEST-0001"})
		self.assertEqual(data["result"], "Unknown")
		self.assertIn("no Container Fill Threshold is set", data["explanation"])

	def test_entry_or_session_is_required(self):
		self.assertIn("entry or session is required", self.tool_error("get_fill_determination", {}))

	def test_a_session_returns_one_determination_per_entry_and_a_tally(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		session_uuid = self.a_session_of_entries()
		data = self.tool_data("get_fill_determination", {"session": session_uuid})
		self.assertEqual(data["count"], 2)
		results = sorted(item["result"] for item in data["determinations"])
		self.assertEqual(results, ["Pass", "Underfill"])
		self.assertEqual(data["summary_by_result"]["Pass"], 1)
		self.assertEqual(data["summary_by_result"]["Underfill"], 1)


# ── 4 ─────────────────────────────────────────────────────────────────────
class TheChangeLog(FillPipelineTestCase):
	def test_it_lists_every_change_with_its_acknowledgment_count(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		checker = self.a_checker()
		self.tool_data(
			"acknowledge_threshold_update",
			{"employee": checker, "container_type": "cherry_bucket", "company": MAIN},
		)
		changes = self.tool_data("list_fill_threshold_changes", {"container_type": "cherry_bucket"})["changes"]
		self.assertEqual(len(changes), 1)
		self.assertEqual(changes[0]["acknowledged_count"], 1)

	def test_it_narrows_by_container_type_and_company(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "pear_bin", "company": MAIN, "lower_bound_pct": 80.0, "upper_bound_pct": 110.0},
		)
		changes = self.tool_data("list_fill_threshold_changes", {"container_type": "pear_bin"})["changes"]
		self.assertEqual([c["container_type"] for c in changes], ["pear_bin"])


# ── 5 ─────────────────────────────────────────────────────────────────────
class Acknowledging(FillPipelineTestCase):
	def test_a_checker_acknowledges_the_current_version(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		checker = self.a_checker()
		data = self.tool_data(
			"acknowledge_threshold_update",
			{"employee": checker, "container_type": "cherry_bucket", "company": MAIN},
		)
		self.assertEqual(data["version"], 1)
		self.assertFalse(data["already_acknowledged"])

	def test_acknowledging_twice_is_idempotent(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		checker = self.a_checker()
		self.tool_data(
			"acknowledge_threshold_update",
			{"employee": checker, "container_type": "cherry_bucket", "company": MAIN},
		)
		second = self.tool_data(
			"acknowledge_threshold_update",
			{"employee": checker, "container_type": "cherry_bucket", "company": MAIN},
		)
		self.assertTrue(second["already_acknowledged"])
		changes = self.tool_data("list_fill_threshold_changes", {"container_type": "cherry_bucket"})["changes"]
		self.assertEqual(changes[0]["acknowledged_count"], 1)

	def test_an_unknown_employee_is_refused(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		message = self.tool_error(
			"acknowledge_threshold_update",
			{"employee": "HR-EMP-NOBODY", "container_type": "cherry_bucket", "company": MAIN},
		)
		self.assertIn("no Employee", message)

	def test_a_container_type_with_no_threshold_ever_set_is_refused(self):
		checker = self.a_checker()
		message = self.tool_error(
			"acknowledge_threshold_update",
			{"employee": checker, "container_type": "cherry_bucket", "company": MAIN},
		)
		self.assertIn("nothing to acknowledge", message)


# ── 6 ─────────────────────────────────────────────────────────────────────
class PendingAcknowledgments(FillPipelineTestCase):
	def test_an_unset_container_type_answers_configured_false(self):
		data = self.tool_data(
			"list_pending_threshold_acknowledgments", {"container_type": "cherry_bucket", "company": MAIN}
		)
		self.assertFalse(data["configured"])
		self.assertEqual(data["pending"], [])

	def test_every_active_checker_is_pending_until_they_acknowledge(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		checker = self.a_checker("HR-EMP-CHECK1", "Chuck Checker")
		self.a_checker("HR-EMP-CHECK2", "Dana Checker")
		data = self.tool_data(
			"list_pending_threshold_acknowledgments", {"container_type": "cherry_bucket", "company": MAIN}
		)
		self.assertEqual(data["checkers_total"], 2)
		self.assertEqual(data["acknowledged_count"], 0)
		self.assertEqual({row["name"] for row in data["pending"]}, {"HR-EMP-CHECK1", "HR-EMP-CHECK2"})

		self.tool_data(
			"acknowledge_threshold_update",
			{"employee": checker, "container_type": "cherry_bucket", "company": MAIN},
		)
		data = self.tool_data(
			"list_pending_threshold_acknowledgments", {"container_type": "cherry_bucket", "company": MAIN}
		)
		self.assertEqual(data["acknowledged_count"], 1)
		self.assertEqual([row["name"] for row in data["pending"]], ["HR-EMP-CHECK2"])

	def test_a_left_checker_is_not_counted(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		self.a_checker("HR-EMP-CHECK1", "Chuck Checker", status="Left")
		data = self.tool_data(
			"list_pending_threshold_acknowledgments", {"container_type": "cherry_bucket", "company": MAIN}
		)
		self.assertEqual(data["checkers_total"], 0)

	def test_a_new_version_makes_everybody_pending_again(self):
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 85.0},
		)
		checker = self.a_checker()
		self.tool_data(
			"acknowledge_threshold_update",
			{"employee": checker, "container_type": "cherry_bucket", "company": MAIN},
		)
		self.tool_data(
			"update_fill_threshold",
			{"container_type": "cherry_bucket", "company": MAIN, "lower_bound_pct": 90.0},
		)
		data = self.tool_data(
			"list_pending_threshold_acknowledgments", {"container_type": "cherry_bucket", "company": MAIN}
		)
		self.assertEqual(data["current_version"], 2)
		self.assertEqual(data["acknowledged_count"], 0)
		self.assertEqual([row["name"] for row in data["pending"]], [checker])


# ── the pure engine ──────────────────────────────────────────────────────
class TheFillDeterminationEngine(SeededTestCase):
	"""bucket_bridge.fill_determination, direct — no fixtures, no tool layer."""

	def test_no_data_at_all_is_unknown(self):
		result = bucket_bridge.fill_determination({}, None)
		self.assertEqual(result["result"], "Unknown")
		self.assertIsNone(result["fill_percentage"])

	def test_a_zero_container_area_does_not_divide_by_zero(self):
		result = bucket_bridge.fill_determination(
			{"mask_area_px": 100.0, "container_area_px": 0.0}, None
		)
		self.assertIsNone(result["computed_fill_percentage"])
		self.assertEqual(result["result"], "Unknown")

	def test_the_computed_percentage_wins_over_the_stored_one(self):
		result = bucket_bridge.fill_determination(
			{"mask_area_px": 45000.0, "container_area_px": 48000.0, "coverage_percent": 50.0},
			{"lower_bound_pct": 0.0, "upper_bound_pct": None, "container_type": "cherry_bucket", "version": 1},
		)
		self.assertEqual(result["computed_fill_percentage"], 93.75)
		self.assertEqual(result["fill_percentage"], 93.75)
		self.assertEqual(result["stored_coverage_percent"], 50.0)
