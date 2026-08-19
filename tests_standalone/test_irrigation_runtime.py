# SPDX-License-Identifier: MIT
"""Tests for v0.76.0 — irrigation runtime from the valve state log.

THE EVENTS ARE SEEDED RATHER THAN PERFORMED. `log_asset_state_change` stamps
`performed_at` from `frappe.utils.now()`, which this harness advances one second
per call — so a run opened and closed through the tool is one second long and
every arithmetic assertion below would read zero. What is under test here is the
PAIRING: which open goes with which close, what happens at the edges of the
window, and what a run that never ended is worth. That needs timestamps chosen
by the test, so the log rows go in directly and the tool reads them exactly as it
would read a season's worth off a real site.

`test_asset_state.py` covers the other half — that a real toggle writes the row
this file assumes.
"""

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	"allow_register_asset": 1,
	"allow_get_irrigation_runtime": 1,
	"allow_log_asset_state_change": 1,
	"allow_get_available_actions": 1,
	"allow_create_irrigation_zone": 1,
}


class RuntimeTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_valve(self, name="MC-Valve-05", asset_type="Irrigation Valve", **kw):
		payload = {"name": name, "asset_type": asset_type, "company": MAIN, **kw}
		return self.tool_data("register_asset", payload)

	def event(self, asset, to_state, when, action=None, cascaded_from=None):
		"""One Asset State Log row, at a timestamp this test chose."""
		rows = STORE.tables.setdefault("Asset State Log", {})
		name = f"ASL-{len(rows) + 1:04d}"
		rows[name] = {
			"name": name,
			"docstatus": 0,
			"asset_name": asset,
			"asset_type": "Irrigation Valve",
			"action": action or ("open_valve" if to_state == "open" else "close_valve"),
			"from_state": "closed" if to_state == "open" else "open",
			"to_state": to_state,
			"performed_by": "Administrator",
			"performed_at": when,
			"cascaded_from": cascaded_from,
			"creation": when,
		}

	def runtime(self, asset, **kw):
		return self.tool_data("get_irrigation_runtime", {"asset": asset, **kw})


# ── one valve, one run ────────────────────────────────────────────────────────
class OneValve(RuntimeTestCase):
	def test_an_open_and_a_close_are_one_run(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 09:30:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 1)
		self.assertEqual(data["runtime_minutes"], 210.0)
		self.assertEqual(data["runtime_hours"], 3.5)

	def test_two_runs_add_up(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 07:00:00")
		self.event("MC-Valve-05", "open", "2026-07-12 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-12 08:00:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 2)
		self.assertEqual(data["runtime_minutes"], 180.0)

	def test_a_valve_that_never_ran_reports_zero_rather_than_refusing(self):
		self.a_valve()
		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 0)
		self.assertEqual(data["runtime_minutes"], 0)
		self.assertEqual(data["valve_count"], 1)

	def test_a_second_open_on_an_open_valve_does_not_start_a_second_run(self):
		"""A worker re-scanning a gate they already opened is one run, not two —
		and counting it twice would double every minute between the two rows."""
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "open", "2026-07-10 06:30:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 08:00:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 1)
		self.assertEqual(data["runtime_minutes"], 120.0)

	def test_a_close_with_no_open_before_it_is_not_a_run(self):
		self.a_valve()
		self.event("MC-Valve-05", "closed", "2026-07-10 09:00:00")
		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 0)
		self.assertEqual(data["runtime_minutes"], 0)

	def test_events_are_paired_on_performed_at_not_on_filing_order(self):
		"""A queued completion filed hours after the fact has a `creation` that
		disagrees with its `performed_at`, and pairing on the wrong one is a
		negative run."""
		self.a_valve()
		rows = STORE.tables.setdefault("Asset State Log", {})
		self.event("MC-Valve-05", "closed", "2026-07-10 09:00:00")
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		# The open was filed second, so its creation is later than the close's.
		rows["ASL-0002"]["creation"] = "2026-07-10 12:00:00"

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 1)
		self.assertEqual(data["runtime_minutes"], 180.0)


# ── the edges of the window ───────────────────────────────────────────────────
class RunsThatCrossTheWindow(RuntimeTestCase):
	def test_a_run_that_started_before_the_window_counts_from_the_window(self):
		"""Water opened on 28 June and closed on 2 July is July irrigation. A
		query reading only July's rows sees a close with no open and drops it."""
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-06-28 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-01 06:00:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 1)
		self.assertEqual(data["runtime_minutes"], 360.0)
		self.assertTrue(data["runs"][0]["open_at_window_start"])

	def test_junes_minutes_are_not_billed_to_july(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-06-28 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-01 06:00:00")

		june = self.runtime("MC-Valve-05", from_date="2026-06-01", to_date="2026-06-30")
		july = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		# June's window ends at 23:59:59 on the 30th, so the two windows partition
		# the run to the second rather than double-counting it.
		self.assertAlmostEqual(june["runtime_minutes"] + july["runtime_minutes"], 4320.0, delta=1.0)

	def test_a_run_that_ended_after_the_window_is_finished_not_still_running(self):
		"""June's report is written in August. Water on at midnight on the 30th
		and shut on 1 July ran for those June hours — calling it "still running"
		would move them out of the total somebody copies into a water report."""
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-06-28 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-01 06:00:00")

		june = self.runtime("MC-Valve-05", from_date="2026-06-01", to_date="2026-06-30")
		self.assertEqual(june["open_run_minutes"], 0)
		self.assertEqual(june["valves_open_now"], [])
		self.assertEqual(june["run_count"], 1)
		self.assertAlmostEqual(june["runtime_minutes"], 3960.0, delta=1.0)

	def test_a_clipped_run_reports_both_the_boundary_and_its_real_close(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-06-28 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-01 06:00:00")

		run = self.runtime("MC-Valve-05", from_date="2026-06-01", to_date="2026-06-30")["runs"][0]
		self.assertTrue(run["closed_after_window"])
		self.assertFalse(run["still_open"])
		self.assertEqual(run["closed_at"], "2026-06-30 23:59:59")
		self.assertEqual(run["actual_closed_at"], "2026-07-01 06:00:00")

	def test_a_close_before_the_window_does_not_open_it(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-06-28 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-06-28 07:00:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["run_count"], 0)
		self.assertEqual(data["runtime_minutes"], 0)


# ── a run that has not ended ──────────────────────────────────────────────────
class WaterStillRunning(RuntimeTestCase):
	def test_an_unfinished_run_is_reported_apart_from_the_finished_ones(self):
		"""A total that mixed the two would change between two identical calls."""
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 07:00:00")
		self.event("MC-Valve-05", "open", "2026-07-12 06:00:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-15")
		self.assertEqual(data["runtime_minutes"], 60.0)
		self.assertEqual(data["run_count"], 1)
		self.assertGreater(data["open_run_minutes"], 0)
		self.assertEqual(data["valves_open_now"], ["MC-Valve-05"])
		self.assertEqual(
			data["total_minutes_including_open"],
			round(data["runtime_minutes"] + data["open_run_minutes"], 1),
		)

	def test_an_unfinished_run_stops_at_the_end_of_a_closed_window(self):
		"""A window that ended last week must not keep accruing minutes."""
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-10", to_date="2026-07-10")
		# 06:00:00 to 23:59:59 on the same day, and not a second more.
		self.assertAlmostEqual(data["open_run_minutes"], 1080.0, delta=1.0)

	def test_the_open_run_appears_in_runs_with_no_closing_time(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-12 06:00:00")
		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-15")

		self.assertEqual(len(data["runs"]), 1)
		self.assertTrue(data["runs"][0]["still_open"])
		self.assertIsNone(data["runs"][0]["closed_at"])


# ── the tree ──────────────────────────────────────────────────────────────────
class RuntimeForAZone(RuntimeTestCase):
	def a_zone(self):
		self.a_valve(name="MC-Zone-3", asset_type="Irrigation Zone")
		self.a_valve(name="MC-Valve-A", location="MC-Zone-3")
		self.a_valve(name="MC-Valve-B", location="MC-Zone-3")

	def test_a_zone_sums_the_valves_below_it(self):
		self.a_zone()
		self.event("MC-Valve-A", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-A", "closed", "2026-07-10 07:00:00")
		self.event("MC-Valve-B", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-B", "closed", "2026-07-10 08:00:00")

		data = self.runtime("MC-Zone-3", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["valve_count"], 2)
		self.assertEqual(data["runtime_minutes"], 180.0)
		self.assertEqual(data["run_count"], 2)

	def test_each_valve_is_reported_on_its_own_as_well_as_in_the_total(self):
		self.a_zone()
		self.event("MC-Valve-A", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-A", "closed", "2026-07-10 07:00:00")

		data = self.runtime("MC-Zone-3", from_date="2026-07-01", to_date="2026-07-31")
		per_valve = {entry["asset_name"]: entry["runtime_minutes"] for entry in data["valves"]}
		self.assertEqual(per_valve["MC-Valve-A"], 60.0)
		self.assertEqual(per_valve["MC-Valve-B"], 0)

	def test_the_zone_node_itself_is_not_counted_as_a_valve(self):
		"""An Irrigation Zone has its own state machine — active, winterized —
		and none of it is water moving through a gate."""
		self.a_zone()
		data = self.runtime("MC-Zone-3", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(
			sorted(entry["asset_name"] for entry in data["valves"]),
			["MC-Valve-A", "MC-Valve-B"],
		)

	def test_a_block_reaches_valves_two_levels_down(self):
		self.a_valve(name="MC-Block-A", asset_type="Block")
		self.a_valve(name="MC-Zone-1", asset_type="Irrigation Zone", location="MC-Block-A")
		self.a_valve(name="MC-Valve-C", location="MC-Zone-1")
		self.event("MC-Valve-C", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-C", "closed", "2026-07-10 07:00:00")

		data = self.runtime("MC-Block-A", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["valve_count"], 1)
		self.assertEqual(data["runtime_minutes"], 60.0)

	def test_an_asset_with_no_valve_under_it_is_refused_with_what_it_has(self):
		self.a_valve(name="MC-Shed-01", asset_type="Cold Storage")
		error = self.tool_error("get_irrigation_runtime", {"asset": "MC-Shed-01"})
		self.assertIn("no valve", error)
		self.assertIn("Irrigation Valve", error)

	def test_a_cold_storage_open_for_the_season_is_not_irrigation(self):
		"""Three shipped types reach a state called `open`. Counting anything
		that reaches it would report a packing shed's season as runtime."""
		self.a_valve(name="MC-Zone-9", asset_type="Irrigation Zone")
		self.a_valve(name="MC-Cold-01", asset_type="Cold Storage", location="MC-Zone-9")
		self.event("MC-Cold-01", "open", "2026-07-01 06:00:00")
		self.event("MC-Cold-01", "closed", "2026-07-20 06:00:00")

		error = self.tool_error("get_irrigation_runtime", {"asset": "MC-Zone-9"})
		self.assertIn("no valve", error)

	def test_a_retired_valve_is_left_out(self):
		self.configure(enabled=1, allow_retire_asset=1, **ALL_ON)
		self.a_zone()
		self.tool_data("retire_asset", {"asset_name": "MC-Valve-B"})
		data = self.runtime("MC-Zone-3", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual([entry["asset_name"] for entry in data["valves"]], ["MC-Valve-A"])


# ── cascaded closes ───────────────────────────────────────────────────────────
class ClosesNobodyPerformed(RuntimeTestCase):
	def test_a_cascaded_close_ends_a_run_like_any_other(self):
		"""The water did stop. The row was written by the main valve above it."""
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 07:00:00", cascaded_from="MC-Main-01")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["runtime_minutes"], 60.0)
		self.assertEqual(data["cascaded_closes"], 1)
		self.assertTrue(data["runs"][0]["closed_by_cascade"])
		self.assertEqual(data["runs"][0]["cascaded_from"], "MC-Main-01")

	def test_a_hand_close_is_not_counted_as_cascaded(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 07:00:00")

		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["cascaded_closes"], 0)
		self.assertFalse(data["runs"][0]["closed_by_cascade"])

	def test_the_cascade_and_the_measurement_agree_end_to_end(self):
		"""Shutting a main writes closes the runtime tool then reads. The two
		halves are tested apart everywhere else; this is the seam."""
		self.a_valve(name="MC-Main-05")
		self.a_valve(name="MC-Drop-05", location="MC-Main-05")
		self.event("MC-Main-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Drop-05", "open", "2026-07-10 06:00:00")
		STORE.tables["Asset Register"]["MC-Main-05"]["current_state"] = '{"state": "open"}'
		STORE.tables["Asset Register"]["MC-Drop-05"]["current_state"] = '{"state": "open"}'

		self.tool_data("log_asset_state_change", {"asset_name": "MC-Main-05", "action": "close_valve"})
		data = self.runtime("MC-Main-05", from_date="2026-07-01", to_date="2026-07-31")

		self.assertEqual(data["valve_count"], 2)
		self.assertEqual(data["run_count"], 2)
		self.assertEqual(data["cascaded_closes"], 1)
		self.assertEqual(data["valves_open_now"], [])


# ── water ─────────────────────────────────────────────────────────────────────
class MinutesIntoGallons(RuntimeTestCase):
	def a_measured_valve(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 07:00:00")

	def test_an_explicit_rate_prices_the_runtime(self):
		self.a_measured_valve()
		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31", flow_rate_gpm=250)
		self.assertEqual(data["gallons"], 15000.0)
		self.assertIn("argument", data["flow_rate_source"])

	def test_with_no_rate_there_are_no_gallons_and_it_says_why(self):
		self.a_measured_valve()
		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertNotIn("gallons", data)
		self.assertIsNone(data["flow_rate_gpm"])
		self.assertIn("NOT PRICED", data["flow_rate_source"])

	def test_a_zone_record_supplies_the_rate_and_the_acreage(self):
		self.a_measured_valve()
		STORE.tables.setdefault("Irrigation Zone", {})["ZONE-3"] = {
			"name": "ZONE-3",
			"docstatus": 0,
			"zone_name": "Zone 3",
			"flow_rate_gpm": 250,
			"area_acres": 10,
			"owning_entity": MAIN,
		}
		data = self.runtime(
			"MC-Valve-05",
			from_date="2026-07-01",
			to_date="2026-07-31",
			irrigation_zone="ZONE-3",
		)
		self.assertEqual(data["gallons"], 15000.0)
		self.assertEqual(data["gallons_per_acre"], 1500.0)
		self.assertEqual(data["irrigation_zone"], "ZONE-3")
		self.assertIn("ZONE-3", data["flow_rate_source"])

	def test_an_unknown_zone_is_refused_rather_than_silently_unpriced(self):
		self.a_measured_valve()
		error = self.tool_error(
			"get_irrigation_runtime",
			{"asset": "MC-Valve-05", "irrigation_zone": "ZONE-NOPE"},
		)
		self.assertIn("ZONE-NOPE", error)

	def test_a_zone_with_no_rate_on_it_returns_minutes_and_says_so(self):
		self.a_measured_valve()
		STORE.tables.setdefault("Irrigation Zone", {})["ZONE-4"] = {
			"name": "ZONE-4",
			"docstatus": 0,
			"zone_name": "Zone 4",
			"flow_rate_gpm": 0,
			"owning_entity": MAIN,
		}
		data = self.runtime(
			"MC-Valve-05",
			from_date="2026-07-01",
			to_date="2026-07-31",
			irrigation_zone="ZONE-4",
		)
		self.assertNotIn("gallons", data)
		self.assertIn("NOT PRICED", data["flow_rate_source"])
		self.assertEqual(data["runtime_minutes"], 60.0)

	def test_only_finished_runs_are_priced(self):
		"""Gallons come off `runtime_minutes`, which is the total that does not
		move between two identical calls."""
		self.a_measured_valve()
		self.event("MC-Valve-05", "open", "2026-07-12 06:00:00")
		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-15", flow_rate_gpm=250)
		self.assertEqual(data["gallons"], 15000.0)
		self.assertGreater(data["open_run_minutes"], 0)


# ── the window itself ─────────────────────────────────────────────────────────
class TheWindow(RuntimeTestCase):
	def test_the_default_window_is_the_last_thirty_days(self):
		self.a_valve()
		data = self.runtime("MC-Valve-05")
		self.assertEqual(data["from"], "2026-06-24 00:00:00")
		self.assertEqual(data["to"], "2026-07-24 23:59:59")

	def test_the_window_includes_the_whole_of_its_last_day(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 22:00:00")
		data = self.runtime("MC-Valve-05", from_date="2026-07-10", to_date="2026-07-10")
		self.assertEqual(data["runtime_minutes"], 960.0)

	def test_a_backwards_window_is_refused(self):
		self.a_valve()
		error = self.tool_error(
			"get_irrigation_runtime",
			{"asset": "MC-Valve-05", "from_date": "2026-07-31", "to_date": "2026-07-01"},
		)
		self.assertIn("after", error)

	def test_the_tool_is_read_only_and_writes_nothing(self):
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		before = dict(STORE.tables["Asset Register"]["MC-Valve-05"])
		self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(STORE.tables["Asset Register"]["MC-Valve-05"], before)
		self.assertEqual(len(STORE.tables["Asset State Log"]), 1)


# ── the switch ────────────────────────────────────────────────────────────────
class TheSwitch(RuntimeTestCase):
	def test_it_is_on_by_default_because_it_is_a_read(self):
		self.configure(enabled=1, allow_register_asset=1)
		self.a_valve()
		self.configure(enabled=1)
		data = self.tool_data("get_irrigation_runtime", {"asset": "MC-Valve-05"})
		self.assertEqual(data["runtime_minutes"], 0)


# ── which six o'clock ─────────────────────────────────────────────────────────
class TheRuntimeSaysWhichClock(RuntimeTestCase):
	"""v0.77.0. A runtime report is read against a wall clock."""

	def setUp(self):
		super().setUp()
		STORE.singles["System Settings"] = {"time_zone": "America/Los_Angeles"}
		self.a_valve()
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		self.event("MC-Valve-05", "closed", "2026-07-10 09:30:00")

	def test_the_window_and_every_run_carry_an_offset(self):
		data = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		self.assertEqual(data["timezone"], "America/Los_Angeles")
		self.assertEqual(data["from_local"], "2026-07-01T00:00:00.000-07:00")
		self.assertEqual(data["runs"][0]["opened_at_local"], "2026-07-10T06:00:00.000-07:00")
		self.assertEqual(data["runs"][0]["closed_at_local"], "2026-07-10T09:30:00.000-07:00")

	def test_the_stored_spelling_is_untouched_beside_it(self):
		"""`FrappeDate.parse` on the handset reads the naive form and would fail
		the whole row on a shape it has not seen."""
		run = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")["runs"][0]
		self.assertEqual(run["opened_at"], "2026-07-10 06:00:00")

	def test_asking_in_another_zone_moves_the_clock_and_not_the_minutes(self):
		"""A duration is the difference between two instants. Rendering them
		elsewhere must not change how long the water ran."""
		pacific = self.runtime("MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31")
		denver = self.runtime(
			"MC-Valve-05", from_date="2026-07-01", to_date="2026-07-31", timezone="America/Denver"
		)
		self.assertEqual(pacific["runtime_minutes"], denver["runtime_minutes"])
		self.assertEqual(denver["runs"][0]["opened_at_local"], "2026-07-10T07:00:00.000-06:00")

	def test_the_day_measured_is_the_farms_day_whatever_zone_is_asked_for(self):
		"""A `to_date` of the 10th means that day as the farm lived it. Rendering
		in Denver moves the wall clock on each timestamp; it must not move which
		day was measured."""
		here = self.runtime("MC-Valve-05", from_date="2026-07-10", to_date="2026-07-10")
		there = self.runtime(
			"MC-Valve-05", from_date="2026-07-10", to_date="2026-07-10", timezone="Asia/Tokyo"
		)
		self.assertEqual(here["runtime_minutes"], there["runtime_minutes"])
		self.assertEqual(here["from"], there["from"])

	def test_an_unknown_zone_is_refused(self):
		error = self.tool_error(
			"get_irrigation_runtime", {"asset": "MC-Valve-05", "timezone": "America/Yakima"}
		)
		self.assertIn("America/Yakima", error)
