# SPDX-License-Identifier: MIT
"""breaks.py — pure-function standalone suite.

Entitlement tables for both states, overlap arithmetic, the concurrent cool-down
case, and the worker who left at eleven.
"""

from __future__ import annotations

import unittest

from erpnext_mcp.breaks import (
	crew_reconciliation,
	entitlement,
	heat_entitlement,
	next_break_due,
	overlap_minutes,
	worker_breaks,
)

# ── policies ───────────────────────────────────────────────────────────────────

OR_POLICY = {
	"policy_id": "OR-2026",
	"work_state": "OR",
	"rest_schedule": [
		{"hours_from": 2.0, "hours_to": 5.99, "periods_owed": 1, "minutes_each": 10, "paid": 1},
		{"hours_from": 6.0, "hours_to": 9.99, "periods_owed": 2, "minutes_each": 10, "paid": 1},
		{"hours_from": 10.0, "hours_to": 13.99, "periods_owed": 3, "minutes_each": 10, "paid": 1},
	],
	"meal_schedule": [
		{"hours_from": 6.0, "hours_to": 13.99, "periods_owed": 1, "minutes_each": 30, "paid": 0},
	],
	"heat_schedule": [
		{
			"heat_index_from": 90.0,
			"heat_index_to": 99.99,
			"minutes_each": 10,
			"every_hours": 2.0,
			"concurrent_with_rest": 1,
		},
		{
			"heat_index_from": 100.0,
			"heat_index_to": 200.0,
			"minutes_each": 15,
			"every_hours": 1.0,
			"concurrent_with_rest": 1,
		},
	],
	"max_hours_without_rest": None,
}

WA_POLICY = {
	"policy_id": "WA-2026",
	"work_state": "WA",
	"rest_schedule": [
		{"hours_from": 2.0, "hours_to": 5.99, "periods_owed": 1, "minutes_each": 10, "paid": 1},
		{"hours_from": 6.0, "hours_to": 9.99, "periods_owed": 2, "minutes_each": 10, "paid": 1},
		{"hours_from": 10.0, "hours_to": 13.99, "periods_owed": 3, "minutes_each": 10, "paid": 1},
	],
	"meal_schedule": [
		{"hours_from": 5.0, "hours_to": 13.99, "periods_owed": 1, "minutes_each": 30, "paid": 0},
	],
	"heat_schedule": [
		{
			"heat_index_from": 90.0,
			"heat_index_to": 99.99,
			"minutes_each": 10,
			"every_hours": 2.0,
			"concurrent_with_rest": 1,
		},
	],
	"max_hours_without_rest": 3.0,
}


# ── entitlement tables ─────────────────────────────────────────────────────────


class OregonEntitlement(unittest.TestCase):
	"""OAR 839-020-0050: entitlement for hours worked under OR-2026."""

	def test_one_hour_owes_nothing(self):
		e = entitlement(1.0, OR_POLICY)
		self.assertEqual(e["rest_periods"], 0)
		self.assertEqual(e["meal_periods"], 0)

	def test_three_hours_owes_one_rest(self):
		e = entitlement(3.0, OR_POLICY)
		self.assertEqual(e["rest_periods"], 1)
		self.assertEqual(e["rest_minutes"], 10)
		self.assertEqual(e["meal_periods"], 0)

	def test_six_hours_owes_two_rest_one_meal(self):
		e = entitlement(6.0, OR_POLICY)
		self.assertEqual(e["rest_periods"], 2)
		self.assertEqual(e["rest_minutes"], 20)
		self.assertEqual(e["meal_periods"], 1)
		self.assertEqual(e["meal_minutes"], 30)

	def test_nine_hours_owes_two_rest_one_meal(self):
		e = entitlement(9.0, OR_POLICY)
		self.assertEqual(e["rest_periods"], 2)
		self.assertEqual(e["meal_periods"], 1)

	def test_ten_hours_owes_three_rest(self):
		e = entitlement(10.0, OR_POLICY)
		self.assertEqual(e["rest_periods"], 3)
		self.assertEqual(e["rest_minutes"], 30)

	def test_boundary_at_5_99(self):
		e = entitlement(5.99, OR_POLICY)
		self.assertEqual(e["rest_periods"], 1)
		self.assertEqual(e["meal_periods"], 0)


class WashingtonEntitlement(unittest.TestCase):
	"""WAC 296-131: entitlement for hours worked under WA-2026."""

	def test_five_hours_owes_one_rest_one_meal(self):
		e = entitlement(5.0, WA_POLICY)
		self.assertEqual(e["rest_periods"], 1)
		self.assertEqual(e["meal_periods"], 1)

	def test_seven_hours_owes_two_rest_one_meal(self):
		e = entitlement(7.0, WA_POLICY)
		self.assertEqual(e["rest_periods"], 2)
		self.assertEqual(e["meal_periods"], 1)


# ── overlap arithmetic ────────────────────────────────────────────────────────


class OverlapMinutesArithmetic(unittest.TestCase):
	"""overlap_minutes(event, segment_start, segment_end)."""

	def test_full_overlap(self):
		ev = {"event_datetime": "2026-07-14 09:40:00", "duration_minutes": 10}
		self.assertAlmostEqual(
			overlap_minutes(ev, "2026-07-14 06:00:00", "2026-07-14 15:00:00"),
			10.0,
		)

	def test_partial_overlap_worker_left_during_break(self):
		ev = {"event_datetime": "2026-07-14 10:50:00", "duration_minutes": 10}
		self.assertAlmostEqual(
			overlap_minutes(ev, "2026-07-14 06:00:00", "2026-07-14 10:55:00"),
			5.0,
		)

	def test_no_overlap_worker_left_before_break(self):
		ev = {"event_datetime": "2026-07-14 13:00:00", "duration_minutes": 10}
		self.assertAlmostEqual(
			overlap_minutes(ev, "2026-07-14 06:00:00", "2026-07-14 11:00:00"),
			0.0,
		)

	def test_worker_joined_during_break(self):
		ev = {"event_datetime": "2026-07-14 09:00:00", "duration_minutes": 10}
		self.assertAlmostEqual(
			overlap_minutes(ev, "2026-07-14 09:05:00", "2026-07-14 15:00:00"),
			5.0,
		)

	def test_ended_at_overrides_scheduled_duration(self):
		ev = {
			"event_datetime": "2026-07-14 09:40:00",
			"duration_minutes": 10,
			"ended_at": "2026-07-14 09:55:00",
		}
		self.assertAlmostEqual(
			overlap_minutes(ev, "2026-07-14 06:00:00", "2026-07-14 15:00:00"),
			15.0,
		)

	def test_zero_duration_returns_zero(self):
		ev = {"event_datetime": "2026-07-14 09:40:00", "duration_minutes": 0}
		self.assertAlmostEqual(
			overlap_minutes(ev, "2026-07-14 06:00:00", "2026-07-14 15:00:00"),
			0.0,
		)


# ── the concurrent cool-down case ────────────────────────────────────────────


class HeatEntitlementAndConcurrency(unittest.TestCase):
	"""Cool-down absorbed by concurrent rest under OR's heat schedule."""

	def test_heat_index_95_owes_cool_downs(self):
		timeline = [{"heat_index_f": 95.0}]
		h = heat_entitlement(8.0, timeline, OR_POLICY)
		self.assertGreater(h["cool_down_periods"], 0)
		self.assertTrue(h["concurrent_with_rest"])

	def test_heat_index_below_90_owes_nothing(self):
		timeline = [{"heat_index_f": 85.0}]
		h = heat_entitlement(8.0, timeline, OR_POLICY)
		self.assertEqual(h["cool_down_periods"], 0)

	def test_no_weather_timeline_owes_nothing(self):
		h = heat_entitlement(8.0, [], OR_POLICY)
		self.assertEqual(h["cool_down_periods"], 0)

	def test_extreme_heat_uses_higher_band(self):
		timeline = [{"heat_index_f": 105.0}]
		h = heat_entitlement(4.0, timeline, OR_POLICY)
		self.assertEqual(h["cool_down_minutes"], 4 * 15)
		self.assertTrue(h["concurrent_with_rest"])


# ── worker_breaks: the worker who left at eleven ──────────────────────────────


class TheWorkerWhoLeftEarly(unittest.TestCase):
	"""A worker who leaves at 11:00 on a shift that ends at 15:00."""

	def _events(self):
		return [
			{
				"break_kind": "Paid Rest",
				"event_datetime": "2026-07-14 09:40:00",
				"duration_minutes": 10,
				"applies_to": "Crew",
			},
			{
				"break_kind": "Paid Rest",
				"event_datetime": "2026-07-14 13:30:00",
				"duration_minutes": 10,
				"applies_to": "Crew",
			},
			{
				"break_kind": "Unpaid Meal",
				"event_datetime": "2026-07-14 12:00:00",
				"duration_minutes": 30,
				"applies_to": "Crew",
			},
		]

	def test_full_shift_worker_gets_two_rest(self):
		seg = {
			"employee": "ANA",
			"joined_at": "2026-07-14 06:00:00",
			"left_at": "2026-07-14 15:00:00",
		}
		wb = worker_breaks(seg, self._events(), OR_POLICY)
		self.assertEqual(wb["rest_taken"], 2)
		self.assertEqual(wb["meal_taken"], 1)
		self.assertEqual(wb["shortfall_minutes"], 0)

	def test_early_leaver_misses_second_rest_and_meal(self):
		seg = {
			"employee": "EARLY",
			"joined_at": "2026-07-14 06:00:00",
			"left_at": "2026-07-14 11:00:00",
		}
		wb = worker_breaks(seg, self._events(), OR_POLICY)
		self.assertEqual(wb["rest_taken"], 1)
		self.assertEqual(wb["meal_taken"], 0)
		self.assertAlmostEqual(wb["paid_break_hours"], 10.0 / 60, places=3)

	def test_early_leaver_entitlement_is_for_five_hours(self):
		seg = {
			"employee": "EARLY",
			"joined_at": "2026-07-14 06:00:00",
			"left_at": "2026-07-14 11:00:00",
		}
		wb = worker_breaks(seg, self._events(), OR_POLICY)
		self.assertEqual(wb["rest_owed"], 1)
		self.assertEqual(wb["meal_owed"], 0)
		self.assertEqual(wb["shortfall_minutes"], 0)

	def test_individual_break_only_counts_for_named_employee(self):
		events = [
			{
				"break_kind": "Paid Rest",
				"event_datetime": "2026-07-14 09:40:00",
				"duration_minutes": 10,
				"applies_to": "Individual",
				"employee": "ANA",
			},
		]
		seg_ana = {
			"employee": "ANA",
			"joined_at": "2026-07-14 06:00:00",
			"left_at": "2026-07-14 15:00:00",
		}
		seg_luis = {
			"employee": "LUIS",
			"joined_at": "2026-07-14 06:00:00",
			"left_at": "2026-07-14 15:00:00",
		}
		wb_ana = worker_breaks(seg_ana, events, OR_POLICY)
		wb_luis = worker_breaks(seg_luis, events, OR_POLICY)
		self.assertEqual(wb_ana["rest_taken"], 1)
		self.assertEqual(wb_luis["rest_taken"], 0)


# ── crew_reconciliation ──────────────────────────────────────────────────────


class CrewReconciliation(unittest.TestCase):
	def test_one_worker_short(self):
		shift = {"start_datetime": "2026-07-14 06:00:00", "end_datetime": "2026-07-14 15:00:00"}
		crew = [
			{"employee": "ANA", "employee_name": "Ana Ruiz"},
			{"employee": "LUIS", "employee_name": "Luis Mora"},
		]
		events = [
			{
				"break_kind": "Paid Rest",
				"event_datetime": "2026-07-14 09:40:00",
				"duration_minutes": 10,
				"applies_to": "Crew",
			},
			{
				"break_kind": "Paid Rest",
				"event_datetime": "2026-07-14 13:30:00",
				"duration_minutes": 10,
				"applies_to": "Individual",
				"employee": "ANA",
			},
			{
				"break_kind": "Unpaid Meal",
				"event_datetime": "2026-07-14 12:00:00",
				"duration_minutes": 30,
				"applies_to": "Crew",
			},
		]
		recon = crew_reconciliation(shift, crew, events, OR_POLICY)
		short = recon["workers_short"]
		names = [w["employee"] for w in short]
		self.assertIn("LUIS", names)
		self.assertNotIn("ANA", names)


# ── next_break_due ───────────────────────────────────────────────────────────


class NextBreakDue(unittest.TestCase):
	def test_wa_max_hours_without_rest_fires(self):
		nbd = next_break_due(
			now="2026-07-14 09:30:00",
			segment_start="2026-07-14 06:00:00",
			events=[],
			policy=WA_POLICY,
		)
		self.assertEqual(nbd["due"], "Paid Rest")
		self.assertIn(nbd["urgency"], ("overdue", "imminent"))

	def test_or_no_max_hours_does_not_fire_on_time_alone(self):
		nbd = next_break_due(
			now="2026-07-14 09:30:00",
			segment_start="2026-07-14 06:00:00",
			events=[],
			policy=OR_POLICY,
		)
		self.assertNotEqual(nbd.get("urgency"), "overdue")

	def test_heat_cool_down_due(self):
		nbd = next_break_due(
			now="2026-07-14 14:00:00",
			segment_start="2026-07-14 06:00:00",
			events=[],
			policy=OR_POLICY,
			heat_index=95.0,
		)
		self.assertEqual(nbd["due"], "Cool-Down")


if __name__ == "__main__":
	unittest.main()
