# SPDX-License-Identifier: MIT
"""The water usage report: minutes rolled up, and gallons only where they are known.

THE EVENTS ARE SEEDED RATHER THAN PERFORMED, for the reason
`test_irrigation_runtime` gives at length: this harness advances the clock one
second per call, so a run opened and closed through the tool is one second long
and every arithmetic assertion here would read zero. The pairing itself is that
module's subject; what is under test HERE is the roll-up and the pricing.

FOUR CLAIMS.

1. **THE MEASUREMENT IS `get_irrigation_runtime`'s, REUSED.** `TheSameArithmetic`.
   Two reports of the same window must not disagree about how long a gate was
   open, so the totals are checked against the older tool's on identical data.

2. **GALLONS ARE PER-VALVE AND NEVER GUESSED.** `PricingIsPerValve` and
   `UnpricedIsNamedNotDropped`. The second is the one that matters: a valve with
   no zone contributes its MINUTES to every total and no gallons, and is named —
   a volume figure that quietly dropped it would be short by an unknown amount,
   in a document filed with a water district.

3. **A RUN IS BILLED WHOLE TO THE PERIOD IT STARTED IN.** `TheGroupingIsByOpen`.
   Splitting a Saturday-night set at midnight is arithmetically tidier and
   produces a report nobody can reconcile against the valve log.

4. **THE OLD TOOL DID NOT CHANGE.** `TheOlderReportIsUntouched`. A report
   somebody has been running all season must not change its answer on an upgrade.
"""

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"register_asset",
		"update_registered_asset",
		"get_water_usage_report",
		"get_irrigation_runtime",
		"create_parcel",
		"create_field",
		"create_irrigation_zone",
		"scan_asset",
		"get_asset_status_report",
	)
}

BLOCK = "Yellow Camp Block 3 - MC"
BLOCK_TWO = "Yellow Camp Block 4 - MC"
ZONE = "YC3-Zone2 - MC"
ZONE_TWO = "YC4-Zone1 - MC"


class WaterTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		self.tool_data(
			"create_parcel",
			{
				"owning_entity": MAIN,
				"parcel_name": "Mill Creek",
				"acreage": 131.43,
				"county": "Wasco",
				"state": "OR",
				"use_type": "Orchard",
			},
		)
		for name in ("Yellow Camp Block 3", "Yellow Camp Block 4"):
			self.tool_data(
				"create_field",
				{
					"parcel": "Mill Creek",
					"field_name": name,
					"acreage": 12.5,
					"variety": "Bing",
					"planting_year": 1998,
					"condition": "Good",
				},
			)

	def a_zone(self, zone_name="YC3-Zone2", field=BLOCK, flow=60, number=2, **kw):
		payload = {
			"field": field,
			"zone_name": zone_name,
			"zone_number": number,
			"water_source": "well",
			"sprinkler_type": "drip",
			"area_sq_ft": 435600,
			"flow_rate_gpm": flow,
		}
		payload.update(kw)
		return self.tool_data("create_irrigation_zone", payload)

	def a_valve(self, name, zone=None, parent=None):
		payload = {"name": name, "asset_type": "Irrigation Valve", "company": MAIN}
		if zone:
			payload["irrigation_zone"] = zone
		if parent:
			payload["parent_asset"] = parent
		return self.tool_data("register_asset", payload)

	def event(self, asset, to_state, when):
		"""One Asset State Log row, at a timestamp this test chose."""
		rows = STORE.tables.setdefault("Asset State Log", {})
		name = f"ASL-W-{len(rows) + 1:04d}"
		rows[name] = {
			"name": name,
			"docstatus": 0,
			"asset_name": asset,
			"asset_type": "Irrigation Valve",
			"action": "open_valve" if to_state == "open" else "close_valve",
			"from_state": "closed" if to_state == "open" else "open",
			"to_state": to_state,
			"performed_by": "Administrator",
			"performed_at": when,
			"creation": when,
		}

	def run_(self, asset, start, end):
		self.event(asset, "open", start)
		self.event(asset, "closed", end)

	def report(self, **kw):
		payload = {"from_date": "2026-07-01", "to_date": "2026-07-31", "company": MAIN}
		payload.update(kw)
		return self.tool_data("get_water_usage_report", payload)


# ── 1. the same arithmetic as the older tool ─────────────────────────────────
class TheSameArithmetic(WaterTestCase):
	def test_one_run_is_the_same_minutes_both_ways(self):
		self.a_valve("MC-Valve-05")
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 09:30:00")

		older = self.tool_data(
			"get_irrigation_runtime",
			{"asset": "MC-Valve-05", "from_date": "2026-07-01", "to_date": "2026-07-31"},
		)
		newer = self.report(asset="MC-Valve-05")
		self.assertEqual(older["runtime_minutes"], newer["runtime_minutes"])
		self.assertEqual(newer["runtime_hours"], 3.5)

	def test_a_still_open_run_stays_out_of_the_period_totals(self):
		"""`runtime_minutes` must not change between two identical calls."""
		self.a_valve("MC-Valve-05")
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")

		data = self.report(asset="MC-Valve-05")
		self.assertEqual(data["runtime_minutes"], 0.0)
		self.assertGreater(data["open_run_minutes"], 0)
		self.assertEqual(data["valves_open_now"], ["MC-Valve-05"])

	def test_a_close_written_by_the_cascade_still_ends_a_run(self):
		self.a_valve("MC-Valve-05")
		self.event("MC-Valve-05", "open", "2026-07-10 06:00:00")
		rows = STORE.tables["Asset State Log"]
		self.event("MC-Valve-05", "closed", "2026-07-10 07:00:00")
		list(rows.values())[-1]["cascaded_from"] = "MC-Main-01"

		data = self.report(asset="MC-Valve-05")
		self.assertEqual(data["runtime_minutes"], 60.0)
		self.assertEqual(data["cascaded_closes"], 1)

	def test_a_farm_with_no_valve_is_refused_by_name(self):
		error = self.tool_error("get_water_usage_report", {"company": MAIN})
		self.assertIn("no valve matched", error)


# ── 2. gallons are per-valve ─────────────────────────────────────────────────
class PricingIsPerValve(WaterTestCase):
	def test_a_zone_linked_valve_is_priced_at_its_own_rate(self):
		self.a_zone(flow=60)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")

		data = self.report()
		self.assertEqual(data["runtime_minutes"], 60.0)
		self.assertEqual(data["gallons"], 3600.0)

	def test_two_zones_at_different_rates_are_priced_separately(self):
		"""One flow rate applied to a whole farm is the shortcut this avoids."""
		self.a_zone(flow=60)
		self.a_zone(zone_name="YC4-Zone1", field=BLOCK_TWO, flow=20, number=1)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.a_valve("MC-Valve-06", zone=ZONE_TWO)
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-06", "2026-07-10 06:00:00", "2026-07-10 07:00:00")

		data = self.report()
		self.assertEqual(data["gallons"], 4800.0)

	def test_an_explicit_rate_overrides_every_zone(self):
		self.a_zone(flow=60)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")

		data = self.report(flow_rate_gpm=100)
		self.assertEqual(data["gallons"], 6000.0)
		self.assertIn("flow_rate_gpm argument", data["flow_rate_source"])

	def test_a_zero_rate_is_refused_rather_than_producing_no_water(self):
		self.a_valve("MC-Valve-05")
		self.assertIn(
			"greater than zero",
			self.tool_error(
				"get_water_usage_report",
				{"company": MAIN, "flow_rate_gpm": 0, "from_date": "2026-07-01", "to_date": "2026-07-31"},
			),
		)

	def test_acre_feet_are_reported_because_a_water_right_is_measured_in_them(self):
		self.a_zone(flow=60)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.run_("MC-Valve-05", "2026-07-10 00:00:00", "2026-07-10 12:00:00")

		data = self.report()
		self.assertGreater(data["acre_feet"], 0)
		self.assertGreater(data["acre_inches"], 0)


class UnpricedIsNamedNotDropped(WaterTestCase):
	def test_an_unmapped_valve_contributes_minutes_and_no_gallons(self):
		self.a_zone(flow=60)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.a_valve("MC-Valve-99")
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-99", "2026-07-10 06:00:00", "2026-07-10 07:00:00")

		data = self.report()
		self.assertEqual(data["runtime_minutes"], 120.0)
		self.assertEqual(data["gallons"], 3600.0)

	def test_the_unpriced_valve_is_named(self):
		"""A total that quietly dropped it would be short by an unknown amount,
		in a document filed with a water district."""
		self.a_valve("MC-Valve-99")
		self.run_("MC-Valve-99", "2026-07-10 06:00:00", "2026-07-10 07:00:00")

		data = self.report()
		self.assertEqual(data["unpriced_valves"], ["MC-Valve-99"])
		self.assertIn("update_registered_asset", data["unpriced_note"])

	def test_priced_and_unpriced_minutes_are_reported_apart(self):
		self.a_zone(flow=60)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.a_valve("MC-Valve-99")
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-99", "2026-07-10 06:00:00", "2026-07-10 07:30:00")

		data = self.report()
		self.assertEqual(data["priced_minutes"], 60.0)
		self.assertEqual(data["unpriced_minutes"], 90.0)

	def test_a_farm_with_nothing_mapped_reports_minutes_and_no_volume(self):
		self.a_valve("MC-Valve-99")
		self.run_("MC-Valve-99", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		data = self.report()
		self.assertIsNone(data["gallons"])
		self.assertEqual(data["runtime_minutes"], 60.0)

	def test_a_zone_with_no_flow_rate_set_leaves_its_valve_unpriced(self):
		self.a_zone(flow=0)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")

		data = self.report()
		self.assertEqual(data["unpriced_valves"], ["MC-Valve-05"])


# ── 3. the grouping ──────────────────────────────────────────────────────────
class TheGroupingIsByOpen(WaterTestCase):
	def setUp(self):
		super().setUp()
		self.a_zone(flow=60)
		self.a_zone(zone_name="YC4-Zone1", field=BLOCK_TWO, flow=60, number=1)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.a_valve("MC-Valve-06", zone=ZONE_TWO)

	def test_by_zone_is_the_default(self):
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-06", "2026-07-10 06:00:00", "2026-07-10 08:00:00")

		data = self.report()
		self.assertEqual(data["group_by"], "zone")
		groups = {entry["group"]: entry["runtime_minutes"] for entry in data["groups"]}
		self.assertEqual(groups, {ZONE: 60.0, ZONE_TWO: 120.0})

	def test_by_block_rolls_the_zones_up_to_the_ground_they_water(self):
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-06", "2026-07-10 06:00:00", "2026-07-10 08:00:00")

		groups = {e["group"]: e["runtime_minutes"] for e in self.report(group_by="block")["groups"]}
		self.assertEqual(groups, {BLOCK: 60.0, BLOCK_TWO: 120.0})

	def test_by_month_buckets_on_the_open(self):
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-05", "2026-07-20 06:00:00", "2026-07-20 07:00:00")

		data = self.report(group_by="month")
		self.assertEqual(len(data["groups"]), 1)
		self.assertEqual(data["groups"][0]["group"], "2026-07")
		self.assertEqual(data["groups"][0]["runtime_minutes"], 120.0)

	def test_a_run_that_crosses_midnight_is_billed_whole_to_the_day_it_started(self):
		"""Splitting it is arithmetically tidier and produces a report nobody can
		reconcile against the valve log, where the run is one row with one start."""
		self.run_("MC-Valve-05", "2026-07-10 22:00:00", "2026-07-11 02:00:00")

		data = self.report(group_by="day")
		self.assertEqual(len(data["groups"]), 1)
		self.assertEqual(data["groups"][0]["group"], "2026-07-10")
		self.assertEqual(data["groups"][0]["runtime_minutes"], 240.0)

	def test_by_week_uses_iso_weeks(self):
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		data = self.report(group_by="week")
		self.assertTrue(data["groups"][0]["group"].startswith("2026-W"))

	def test_by_valve_is_offered_too(self):
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-06", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		groups = {e["group"] for e in self.report(group_by="valve")["groups"]}
		self.assertEqual(groups, {"MC-Valve-05", "MC-Valve-06"})

	def test_an_unknown_grouping_is_refused_with_the_list(self):
		error = self.tool_error(
			"get_water_usage_report",
			{"company": MAIN, "group_by": "fortnight", "from_date": "2026-07-01", "to_date": "2026-07-31"},
		)
		self.assertIn("group_by must be one of", error)

	def test_a_field_filter_narrows_through_the_zone(self):
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-06", "2026-07-10 06:00:00", "2026-07-10 08:00:00")

		data = self.report(field=BLOCK)
		self.assertEqual(data["valve_count"], 1)
		self.assertEqual(data["runtime_minutes"], 60.0)

	def test_a_field_nothing_is_linked_to_names_the_link_to_set(self):
		self.a_valve("MC-Valve-99")
		error = self.tool_error(
			"get_water_usage_report",
			{
				"company": MAIN,
				"field": BLOCK,
				"asset": "MC-Valve-99",
				"from_date": "2026-07-01",
				"to_date": "2026-07-31",
			},
		)
		self.assertIn("irrigation_zone", error)

	def test_inches_applied_divides_by_the_zone_acreage_once(self):
		"""A zone that ran forty times in July has not grown forty times bigger."""
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")
		self.run_("MC-Valve-05", "2026-07-11 06:00:00", "2026-07-11 07:00:00")

		entry = next(e for e in self.report()["groups"] if e["group"] == ZONE)
		self.assertEqual(entry["acres"], 10.0)
		self.assertEqual(entry["gallons"], 7200.0)


# ── 4. the older report did not move ─────────────────────────────────────────
class TheOlderReportIsUntouched(WaterTestCase):
	def test_it_still_refuses_to_price_without_being_told_how(self):
		"""A report somebody has been running all season must not change its
		answer on an upgrade — even though the valve now names a zone."""
		self.a_zone(flow=60)
		self.a_valve("MC-Valve-05", zone=ZONE)
		self.run_("MC-Valve-05", "2026-07-10 06:00:00", "2026-07-10 07:00:00")

		data = self.tool_data(
			"get_irrigation_runtime",
			{"asset": "MC-Valve-05", "from_date": "2026-07-01", "to_date": "2026-07-31"},
		)
		self.assertIsNone(data["flow_rate_gpm"])
		self.assertIn("NOT PRICED", data["flow_rate_source"])
		self.assertNotIn("gallons", data)


# ── 5. what a valve scan shows ───────────────────────────────────────────────
class TheValveScanCarriesRuntime(WaterTestCase):
	def test_a_scan_reports_today_this_week_and_this_season(self):
		self.a_valve("MC-Valve-05")
		data = self.tool_data("scan_asset", {"asset_name": "MC-Valve-05"})
		runtime = data["status"]["runtime"]
		for key in ("today_minutes", "week_minutes", "season_minutes"):
			self.assertIn(key, runtime)

	def test_a_valve_under_a_shut_main_is_told_about_the_main(self):
		"""A lateral reading `open` under a closed main is a valve with no water
		in it, and a worker sent to find out why nothing is running needs telling
		about the handle three hundred yards uphill."""
		self.a_valve("MC-Main-01")
		self.a_valve("MC-Valve-05", parent="MC-Main-01")

		data = self.tool_data("scan_asset", {"asset_name": "MC-Valve-05"})
		self.assertTrue(data["status"]["parent_valve"]["parent_blocking"])
		self.assertIn("no water can reach here", data["status"]["parent_valve"]["parent_note"])

	def test_an_open_main_is_not_reported_as_blocking(self):
		self.a_valve("MC-Main-01")
		self.a_valve("MC-Valve-05", parent="MC-Main-01")
		self.configure(enabled=1, allow_log_asset_state_change=1, **ALL_ON)
		self.tool_data("log_asset_state_change", {"asset_name": "MC-Main-01", "action": "open_valve"})

		data = self.tool_data("scan_asset", {"asset_name": "MC-Valve-05"})
		self.assertFalse(data["status"]["parent_valve"]["parent_blocking"])

	def test_a_tractor_scan_carries_no_runtime_block(self):
		self.tool_data("register_asset", {"name": "MC-Tractor-01", "asset_type": "Tractor", "company": MAIN})
		data = self.tool_data("scan_asset", {"asset_name": "MC-Tractor-01"})
		self.assertEqual(data["status"]["runtime"], {})
