# SPDX-License-Identifier: MIT
"""Engine hours and the service schedule: the series, not the last number.

`asset_actions._MENU` carried a `log_hours` row marked NOT BUILT for a release,
and the note under it said what building it would take:

    one Float column for the last reading plus a log row per entry — the reading
    is only useful as a SERIES (hours since last service, hours this season), and
    a single overwritten column would answer none of those questions.

So the claims under test here are about that shape.

1. **THE LOG IS THE RECORD AND THE REGISTER IS A CACHE.** `TheSeriesIsTheRecord`.
   Every figure is computed from `Asset State Log.engine_hours`;
   `Asset Register.current_hours` follows it and nothing is computed from it.

2. **A METER ONLY COUNTS UP.** `AMeterOnlyCountsUp`. A reading below the last on
   record is a typo or a swapped instrument, and a typo accepted quietly becomes
   "this tractor ran negative hours this season", which nobody reads as a data
   error. The refusal happens BEFORE the state change, so a rejected reading does
   not leave a tractor checked in with no hours and no explanation.

3. **HOURS PER SESSION NEED BOTH ENDS.** `ASessionNeedsBothEnds`. A checkout
   nobody metered leaves the session empty rather than inventing a length from
   the last reading of any kind — which on a machine serviced in between would
   bill the workshop's test run to a worker's afternoon.

4. **UNMEASURED IS NOT OVERDUE.** `UnmeasuredIsNotOverdue`. An hours interval
   with no reading ever recorded is not due; reporting it as due would fill a
   board with unverifiable work on the day an operator first sets an interval.

5. **WHICHEVER COMES FIRST, AND THE ANSWER SAYS WHICH.** `WhicheverComesFirst`.

6. **A NIGHTLY JOB MUST NOT PRODUCE A NIGHTLY BACKLOG.** `TheSweepDoesNotDuplicate`.
"""

from .fixtures import MAIN, V12TestCase
from .harness import STORE, frappe

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"register_asset",
		"update_registered_asset",
		"log_asset_state_change",
		"list_asset_state_history",
		"get_engine_hours_summary",
		"record_service",
		"check_maintenance_due",
		"trigger_maintenance_tasks",
		"get_asset_status_report",
		"scan_asset",
		"get_farm_task",
	)
}

TRACTOR = "MC-Tractor-01"
VALVE = "MC-Valve-01"


class HoursTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_tractor(self, name=TRACTOR, asset_type="Tractor", **kw):
		return self.tool_data(
			"register_asset", {"name": name, "asset_type": asset_type, "company": MAIN, **kw}
		)

	def act(self, action, asset=TRACTOR, **kw):
		return self.tool_data("log_asset_state_change", {"asset_name": asset, "action": action, **kw})

	def summary(self, asset=TRACTOR, **kw):
		return self.tool_data("get_engine_hours_summary", {"asset_name": asset, **kw})

	def session(self, out, back, asset=TRACTOR):
		"""One full checkout at the two readings given."""
		self.act("check_out", asset=asset, engine_hours=out)
		self.act("check_in", asset=asset, engine_hours=back)


# ── 1. the series is the record ──────────────────────────────────────────────
class TheSeriesIsTheRecord(HoursTestCase):
	def test_a_reading_lands_on_the_log_row(self):
		self.a_tractor()
		data = self.act("check_out", engine_hours=1240.5)
		self.assertEqual(data["engine_hours"], 1240.5)

		row = next(r for r in STORE.rows("Asset State Log") if r["asset_name"] == TRACTOR)
		self.assertEqual(float(row["engine_hours"]), 1240.5)

	def test_the_register_caches_the_highest_reading(self):
		self.a_tractor()
		self.session(1240.5, 1247.0)
		self.assertEqual(float(STORE.get_raw("Asset Register", TRACTOR)["current_hours"]), 1247.0)

	def test_the_cache_does_not_go_backwards_on_a_stale_write(self):
		"""A scan showing fewer hours than the machine has run would send
		somebody looking for a service that is already overdue."""
		self.a_tractor()
		self.session(1240.5, 1247.0)
		from erpnext_mcp.tools import engine_hours

		engine_hours.cache_reading(TRACTOR, 900.0)
		self.assertEqual(float(STORE.get_raw("Asset Register", TRACTOR)["current_hours"]), 1247.0)

	def test_the_summary_reports_both_and_does_not_confuse_them(self):
		self.a_tractor()
		self.session(1240.5, 1247.0)
		data = self.summary()
		self.assertEqual(data["current_hours"], 1247.0)
		self.assertEqual(data["cached_current_hours"], 1247.0)

	def test_a_drifted_cache_is_visible_rather_than_authoritative(self):
		self.a_tractor()
		self.session(1240.5, 1247.0)
		STORE.get_raw("Asset Register", TRACTOR)["current_hours"] = 99999.0

		data = self.summary()
		self.assertEqual(data["current_hours"], 1247.0)
		self.assertEqual(data["cached_current_hours"], 99999.0)

	def test_zero_is_a_reading_and_not_an_absence(self):
		"""A machine straight off the lot reads 0.0, and dropping it would lose
		the one datum that makes the first session computable."""
		self.a_tractor()
		self.act("check_out", engine_hours=0)
		self.act("check_in", engine_hours=6.5)
		self.assertEqual(self.summary()["session_hours_total"], 6.5)


# ── 2. a meter only counts up ────────────────────────────────────────────────
class AMeterOnlyCountsUp(HoursTestCase):
	def test_a_lower_reading_is_refused(self):
		self.a_tractor()
		self.act("check_out", engine_hours=1240.5)
		error = self.tool_error(
			"log_asset_state_change",
			{"asset_name": TRACTOR, "action": "check_in", "engine_hours": 124.0},
		)
		self.assertIn("below the 1240.5 already on record", error)
		self.assertIn("allow_meter_reset", error)

	def test_the_refusal_happens_before_the_state_change(self):
		"""A tractor checked in with a rejected reading and no explanation is
		the worse of the two failures."""
		self.a_tractor()
		self.act("check_out", engine_hours=1240.5)
		with self.assertRaises(Exception):
			self.tool_data(
				"log_asset_state_change",
				{"asset_name": TRACTOR, "action": "check_in", "engine_hours": 124.0},
			)
		state = STORE.get_raw("Asset Register", TRACTOR)["current_state"]
		self.assertIn("checked_out", str(state))

	def test_a_tenth_low_is_tolerated(self):
		"""An hour meter re-read by somebody squinting at a dusty gauge comes
		back a tenth low, and refusing that trains people to stop entering
		readings at all."""
		self.a_tractor()
		self.act("check_out", engine_hours=1240.5)
		self.act("check_in", engine_hours=1240.4)

	def test_a_declared_reset_is_accepted_and_recorded(self):
		self.a_tractor()
		self.act("check_out", engine_hours=1240.5)
		self.act("check_in", engine_hours=12.0, allow_meter_reset=True)

		row = next(r for r in STORE.rows("Asset State Log") if r.get("engine_hours") == 12.0)
		self.assertIn("Meter reset accepted", str(row["notes"]))

	def test_a_reset_does_not_produce_a_negative_session(self):
		self.a_tractor()
		self.act("check_out", engine_hours=1240.5)
		data = self.act("check_in", engine_hours=12.0, allow_meter_reset=True)
		self.assertIsNone(data["hours_used"])

	def test_a_reading_on_a_machine_with_no_meter_is_refused_by_name(self):
		self.a_tractor(name=VALVE, asset_type="Irrigation Valve")
		error = self.tool_error(
			"log_asset_state_change",
			{"asset_name": VALVE, "action": "open_valve", "engine_hours": 12},
		)
		self.assertIn("no hour meter", error)
		self.assertIn("Tractor", error)

	def test_a_reading_that_is_not_a_number_is_refused(self):
		self.a_tractor()
		self.assertIn(
			"must be a number",
			self.tool_error(
				"log_asset_state_change",
				{"asset_name": TRACTOR, "action": "check_out", "engine_hours": "about twelve hundred"},
			),
		)


# ── 3. a session needs both ends ─────────────────────────────────────────────
class ASessionNeedsBothEnds(HoursTestCase):
	def test_a_full_session_records_its_hours(self):
		self.a_tractor()
		data = self.act("check_out", engine_hours=1240.0)
		self.assertIsNone(data["hours_used"])
		data = self.act("check_in", engine_hours=1247.5)
		self.assertEqual(data["hours_used"], 7.5)

	def test_an_unmetered_checkout_leaves_the_session_empty(self):
		"""Not invented from the last reading of any kind: on a machine serviced
		in between, that would bill the workshop's test run to a worker."""
		self.a_tractor()
		self.act("check_out")
		data = self.act("check_in", engine_hours=1247.5)
		self.assertIsNone(data["hours_used"])

	def test_a_machine_still_out_is_an_open_session(self):
		self.a_tractor()
		self.act("check_out", engine_hours=1240.0)
		data = self.summary()
		self.assertTrue(data["checked_out_now"])
		self.assertTrue(data["open_session"]["open"])
		self.assertIsNone(data["open_session"]["hours"])

	def test_an_open_session_is_not_measured_against_the_current_reading(self):
		"""Nobody has read the meter since it left the yard, so there is no
		second number to subtract."""
		self.a_tractor()
		self.session(1000.0, 1010.0)
		self.act("check_out", engine_hours=1010.0)
		self.assertEqual(self.summary()["session_hours_total"], 10.0)

	def test_two_sessions_add_up(self):
		self.a_tractor()
		self.session(1000.0, 1006.0)
		self.session(1006.0, 1011.5)
		data = self.summary()
		self.assertEqual(data["session_count"], 2)
		self.assertEqual(data["session_hours_total"], 11.5)


# ── 4. the schedule ──────────────────────────────────────────────────────────
class UnmeasuredIsNotOverdue(HoursTestCase):
	def test_an_hours_interval_with_no_reading_is_not_due(self):
		self.a_tractor(service_interval_hours=250)
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertFalse(data["due"])
		self.assertTrue(any("unmeasured is not overdue" in r for r in data["reasons"]))

	def test_an_asset_with_no_interval_is_not_on_a_schedule_at_all(self):
		self.a_tractor()
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertFalse(data["scheduled"])
		self.assertIn("not on a service schedule", data["message"])

	def test_a_measured_machine_past_its_interval_is_due(self):
		self.a_tractor(service_interval_hours=250)
		self.session(0.0, 260.0)
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertTrue(data["due"])
		self.assertEqual(data["due_on"], "hours")
		self.assertEqual(data["overdue_by_hours"], 10.0)

	def test_the_interval_counts_from_the_last_service_and_not_from_zero(self):
		self.a_tractor(service_interval_hours=250)
		self.session(0.0, 260.0)
		self.tool_data("record_service", {"asset_name": TRACTOR})
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertFalse(data["due"])
		self.assertEqual(data["hours_since_service"], 0.0)

	def test_record_service_defaults_the_reading_to_the_series(self):
		self.a_tractor(service_interval_hours=250)
		self.session(0.0, 260.0)
		data = self.tool_data("record_service", {"asset_name": TRACTOR})
		self.assertEqual(data["service_hours"], 260.0)


class WhicheverComesFirst(HoursTestCase):
	def test_the_calendar_alone_is_a_complete_schedule(self):
		"""A fire extinguisher has no hour meter and a real interval."""
		self.a_tractor(
			name="MC-Ext-01",
			asset_type="General",
			service_interval_days=365,
			last_service_date="2024-01-01",
		)
		data = self.tool_data("check_maintenance_due", {"asset_name": "MC-Ext-01"})
		self.assertTrue(data["due"])
		self.assertEqual(data["due_on"], "days")

	def test_the_headline_names_the_interval_that_bit(self):
		"""Telling somebody 'due in 265 days' because the calendar was checked
		second is how a schedule stops being believed."""
		self.a_tractor(service_interval_hours=250, service_interval_days=365, last_service_date="2026-08-01")
		self.session(0.0, 300.0)
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertEqual(data["due_on"], "hours")
		self.assertIn("OVERDUE", data["message"])

	def test_when_both_intervals_have_passed_hours_is_the_headline(self):
		"""Not because ten hours over is 'more' than five days over — the two
		cannot be compared — but because a machine carrying an hours interval is
		one whose wear somebody chose to measure in hours. Both figures are
		reported either way."""
		self.a_tractor(service_interval_hours=250, service_interval_days=30, last_service_date="2020-01-01")
		self.session(0.0, 300.0)
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertEqual(data["due_on"], "hours")
		self.assertEqual(data["overdue_by_hours"], 50.0)
		self.assertGreater(data["overdue_by_days"], 0)

	def test_a_calendar_schedule_with_no_service_ever_anchors_on_registration(self):
		self.a_tractor(service_interval_days=30)
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertIsNotNone(data["days_since_service"])
		self.assertEqual(data["last_service_date"], None)

	def test_a_machine_approaching_its_interval_is_due_soon_not_due(self):
		self.a_tractor(service_interval_hours=250)
		self.session(0.0, 240.0)
		data = self.tool_data("check_maintenance_due", {"asset_name": TRACTOR})
		self.assertFalse(data["due"])
		self.assertTrue(data["due_soon"])
		self.assertIn("Service due in", data["message"])

	def test_the_farm_wide_sweep_reports_only_scheduled_assets(self):
		self.a_tractor(service_interval_hours=250)
		self.a_tractor(name="MC-Tractor-02")
		self.session(0.0, 300.0)
		data = self.tool_data("check_maintenance_due", {"company": MAIN})
		self.assertEqual(data["scheduled_count"], 1)
		self.assertEqual(data["due_count"], 1)


# ── 5. the job that raises the work ──────────────────────────────────────────
class TheSweepDoesNotDuplicate(HoursTestCase):
	def _due_tractor(self):
		self.a_tractor(service_interval_hours=250)
		self.session(0.0, 300.0)

	def test_it_is_a_dry_run_by_default(self):
		"""A tool whose first accidental call fills a dispatch board is one an
		operator switches off rather than tunes."""
		self._due_tractor()
		data = self.tool_data("trigger_maintenance_tasks", {"company": MAIN})
		self.assertTrue(data["dry_run"])
		self.assertEqual(data["created_count"], 0)
		self.assertEqual(len(data["would_create"]), 1)
		self.assertEqual(STORE.tables.get("Farm Task", {}), {})

	def test_a_live_run_raises_one_task(self):
		self._due_tractor()
		data = self.tool_data("trigger_maintenance_tasks", {"company": MAIN, "dry_run": False})
		self.assertEqual(data["created_count"], 1)

		task = STORE.get_raw("Farm Task", data["created"][0]["task"])
		self.assertEqual(task["location"], TRACTOR)
		self.assertEqual(task["location_doctype"], "Asset Register")

	def test_running_twice_does_not_raise_twice(self):
		"""A job that re-raised nightly would produce the exact backlog that
		teaches a crew to ignore the board."""
		self._due_tractor()
		self.tool_data("trigger_maintenance_tasks", {"company": MAIN, "dry_run": False})
		second = self.tool_data("trigger_maintenance_tasks", {"company": MAIN, "dry_run": False})
		self.assertEqual(second["created_count"], 0)
		self.assertEqual(second["skipped_count"], 1)

	def test_a_task_raised_by_hand_suppresses_the_automatic_one(self):
		self._due_tractor()
		self.configure(enabled=1, allow_create_farm_task=1, **ALL_ON)
		self.tool_data(
			"create_farm_task",
			{
				"task_name": "Service the tractor",
				"task_type": "Repair",
				"evidence_required": {"photos": True},
				"company": MAIN,
				"location_doctype": "Asset Register",
				"location": TRACTOR,
			},
		)
		data = self.tool_data("trigger_maintenance_tasks", {"company": MAIN, "dry_run": False})
		self.assertEqual(data["created_count"], 0)
		self.assertEqual(data["skipped_count"], 1)

	def test_the_raised_task_asks_for_evidence(self):
		"""A tick in a box is what an auditor is trained to disbelieve."""
		self._due_tractor()
		data = self.tool_data("trigger_maintenance_tasks", {"company": MAIN, "dry_run": False})
		task = STORE.get_raw("Farm Task", data["created"][0]["task"])
		self.assertIn("photos", str(task["evidence_required"]))

	def test_the_scheduler_entry_takes_no_arguments_and_never_raises(self):
		import inspect

		from erpnext_mcp.tools import maintenance

		from .harness import INSTALLED_DOCTYPES

		self.assertEqual(list(inspect.signature(maintenance.sweep_due_maintenance).parameters), [])
		INSTALLED_DOCTYPES.discard("Asset Register")
		try:
			self.assertEqual(maintenance.sweep_due_maintenance()["created_count"], 0)
		finally:
			INSTALLED_DOCTYPES.add("Asset Register")


# ── 6. what a scan shows ─────────────────────────────────────────────────────
class TheScanCarriesTheHours(HoursTestCase):
	def test_a_scan_of_a_tractor_reports_its_meter_and_its_schedule(self):
		self.a_tractor(service_interval_hours=250)
		self.session(0.0, 300.0)
		data = self.tool_data("scan_asset", {"asset_name": TRACTOR})

		self.assertEqual(data["status"]["current_hours"], 300.0)
		self.assertTrue(data["status"]["maintenance_due"])
		self.assertIn("OVERDUE", data["status"]["maintenance_message"])

	def test_the_overdue_machine_raises_a_warning_on_the_card(self):
		self.a_tractor(service_interval_hours=250)
		self.session(0.0, 300.0)
		data = self.tool_data("scan_asset", {"asset_name": TRACTOR})
		self.assertTrue(data["needs_attention"])
		self.assertEqual(data["warnings"][0]["kind"], "maintenance")

	def test_a_valve_scan_carries_no_hours_and_does_not_fail(self):
		"""A scan is not the place for a refusal about the kind of thing that
		was scanned."""
		self.a_tractor(name=VALVE, asset_type="Irrigation Valve")
		data = self.tool_data("scan_asset", {"asset_name": VALVE})
		self.assertIsNone(data["status"]["current_hours"])
		self.assertFalse(data["status"]["engine_hours"])

	def test_the_desk_report_records_no_scan(self):
		"""`scan_asset` stamps `last_scan_at` because somebody was standing
		there; a dispatcher at a desk was not."""
		self.a_tractor()
		self.tool_data("get_asset_status_report", {"asset_name": TRACTOR})
		self.assertFalse(STORE.get_raw("Asset Register", TRACTOR).get("last_scan_at"))

	def test_the_flat_keys_a_shipped_handset_decodes_are_untouched(self):
		"""The status block lands under one key precisely so it cannot overwrite
		the surface every field device already reads."""
		self.a_tractor()
		data = self.tool_data("scan_asset", {"asset_name": TRACTOR})
		self.assertEqual(data["state"], "in_service")
		self.assertIsInstance(data["open_tasks"], list)
		self.assertTrue(any(row["action"] == "check_out" for row in data["action_menu"]))

	def test_the_recent_activity_carries_the_readings(self):
		self.a_tractor()
		self.session(1000.0, 1007.0)
		activity = self.tool_data("scan_asset", {"asset_name": TRACTOR})["status"]["recent_activity"]
		self.assertEqual(activity[0]["engine_hours"], 1007.0)
		self.assertEqual(activity[0]["hours_used"], 7.0)


# ── 7. this season ───────────────────────────────────────────────────────────
class ThisSeason(HoursTestCase):
	def event(self, action, when, hours, asset=TRACTOR):
		"""One metered log row at a timestamp this test chose.

		Seeded rather than performed, for the reason `test_irrigation_runtime`
		gives: the harness advances the clock one second per call, so every event
		recorded through the tool lands today and a season boundary would never
		be crossed.
		"""
		rows = STORE.tables.setdefault("Asset State Log", {})
		name = f"ASL-H-{len(rows) + 1:04d}"
		rows[name] = {
			"name": name,
			"docstatus": 0,
			"asset_name": asset,
			"asset_type": "Tractor",
			"action": action,
			"from_state": "in_service" if action == "check_out" else "checked_out",
			"to_state": "checked_out" if action == "check_out" else "in_service",
			"performed_by": "Administrator",
			"performed_at": when,
			"engine_hours": hours,
			"creation": when,
		}

	def test_last_years_hours_are_not_this_seasons(self):
		self.a_tractor()
		self.event("check_out", "2025-06-01 06:00:00", 1000.0)
		self.event("check_in", "2025-06-01 18:00:00", 1010.0)
		self.event("check_out", f"{frappe.utils.today()} 06:00:00", 1010.0)
		self.event("check_in", f"{frappe.utils.today()} 12:00:00", 1016.0)

		data = self.summary()
		self.assertEqual(data["session_hours_total"], 16.0)
		self.assertEqual(data["hours_this_season"], 6.0)

	def test_the_season_start_can_be_stated(self):
		self.a_tractor()
		self.event("check_out", "2025-06-01 06:00:00", 1000.0)
		self.event("check_in", "2025-06-01 18:00:00", 1010.0)

		data = self.summary(season_start="2025-01-01")
		self.assertEqual(data["season_start"], "2025-01-01")
		self.assertEqual(data["hours_this_season"], 10.0)

	def test_the_default_says_which_date_it_used(self):
		self.a_tractor()
		self.assertEqual(self.summary()["season_start"], f"{str(frappe.utils.today())[:4]}-01-01")

	def test_a_machine_with_no_meter_says_so_rather_than_reporting_zero(self):
		self.a_tractor(name=VALVE, asset_type="Irrigation Valve")
		data = self.summary(asset=VALVE)
		self.assertFalse(data["metered"])
		self.assertIn("no hour meter", data["note"])
