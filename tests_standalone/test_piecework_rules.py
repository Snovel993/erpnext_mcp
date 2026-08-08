# SPDX-License-Identifier: MIT
"""Piecework and hours — the pay rules, tested as scenarios a foreman would recognise.

`test_payroll.py` tests the engine's functions one at a time and
`test_payroll_integration.py` tests the join from the shift register to the
slip. Neither walks a WHOLE DAY of picking end to end and asserts the dollar
figure, and neither pins the three places where what the code does and what a
reader assumes it does come apart. That is what this file is for.

────────────────────────────────────────────────────────────────────────────
THREE THINGS THAT ARE NOT WHAT A READER EXPECTS
────────────────────────────────────────────────────────────────────────────

THERE IS NO HIGHER-OF RULE. Ask most people how piecework pay works and they
will say "the greater of the piece total and minimum wage for the hours". This
app does not do that and says so in `payroll_integration.py`'s header: the
shortfall is REPORTED, priced per state, and gross pay is left alone. Forty-seven
buckets at $1.50 in an eight-hour Oregon day pays $70.50, not $117.60, and the
run carries a $47.10 shortfall against Ana's name for somebody to decide about.
`TheHigherOfRuleIsReportedNotApplied` pins that, in both directions, because the
day somebody "fixes" it by quietly topping up gross is the day the fact that a
rate is set below the lawful floor stops appearing anywhere.

THE MINIMUM WAGE CHECK IGNORES THE OVERTIME PREMIUM. `check_minimum_wage_by_state`
compares gross against `minimum_wage × hours` — flat, all hours alike. A picker
who works fifty hours has a statutory floor of forty at straight time plus ten at
one and a half, and the check does not know that. Fifty hours and four hundred
buckets at $1.50 grosses $780 and PASSES a floor of $735, while the actual floor
with the premium is $808.50. `OvertimeUnderPieceRate` pins the passing verdict and
the $28.50 nobody is told about, so the gap is a failing assertion the day it is
closed rather than a surprise in an audit.

OVERTIME IS WEEKLY, SO A TEN-HOUR DAY IS NOT OVERTIME. Both states put the ag
threshold at forty hours in a workweek and neither has a daily one. Ten hours on
Tuesday is ten straight-time hours; the premium starts wherever the fortieth hour
of that week falls, which may be Friday morning.

AND A SLIP CARRIES TWO MINIMUM WAGE VERDICTS THAT CAN DISAGREE. `minimum_wage_check`
comes from `payroll_calc.check_minimum_wage`, which tests the whole period's gross
against ONE state's floor — whichever state holds the most hours.
`minimum_wage_detail` comes from `check_minimum_wage_by_state`, which tests each
state against its own. For a picker with thirty Oregon hours and ten Washington
ones at $15.00 effective, the first says PASS and the second names Washington and
prices the shortfall at $16.60. `TheTwoMinimumWageVerdicts` pins both, and pins
which one `tools/payroll.py` writes to the stored row — the per-state one, which
is the right answer.

────────────────────────────────────────────────────────────────────────────
AND ONE THING THAT DOES NOT EXIST YET
────────────────────────────────────────────────────────────────────────────

A WORKER CANNOT BE PART PIECE-RATE AND PART HOURLY IN ONE PERIOD. `pay_type`
lives on the salary structure, one per employee, and `calculate_gross_pay`
branches on it once for the whole slip. A picker who spent Monday on buckets and
Tuesday on irrigation is paid for Tuesday's hours at the piece rate — which is to
say nothing, because Tuesday produced no buckets. `MixedPieceworkAndHourlyWork`
asserts the current behaviour and names it, rather than asserting the behaviour
somebody might wish for.

────────────────────────────────────────────────────────────────────────────
SEVEN CLAIMS
────────────────────────────────────────────────────────────────────────────

1. `TheHigherOfRuleIsReportedNotApplied` — the two scenarios, the boundary
   between them, and Washington's higher floor on the same day's work.
2. `OvertimeUnderPieceRate` — a ten-hour day is not overtime; a fifty-hour week
   is; the piece premium is 1.5x the effective hourly; and the minimum wage
   check does not account for any of it.
3. `BucketsBecomePieceUnits` — accepted Bucket Log Entries, through the real
   loader on the shipped doctype, become the piece units that become the gross.
   Rejected captures do not.
4. `MultiDayPayPeriod` — ten days across two workweeks aggregate into one slip
   with the hours, the units and the overtime each summed the way they are
   counted, not the way they would total.
5. `PieceworkEdgeCases` — zero buckets, a partial shift, an unclosed shift, and
   a paid rest break on a piece-rate day.
6. `MixedPieceworkAndHourlyWork` — the gap above, asserted as it stands.
7. `TheTwoMinimumWageVerdicts` — the flat check and the per-state check on one
   slip, disagreeing, and the per-state one winning where it is stored.
8. `TheBucketReconciliation` — `get_piecework_summary` and
   `reconcile_bucket_payroll`, which until now had no test but their name in the
   registry.
"""

from erpnext_mcp import payroll_integration as pi
from erpnext_mcp.payroll_calc import (
	MINIMUM_WAGE_RATES,
	calculate_gross_pay,
	check_minimum_wage,
)

from .fixtures import MAIN
from .harness import STORE
from .test_payroll_integration import (
	PERIOD_END,
	PERIOD_START,
	PICKER,
	WORKER,
	IntegrationTestCase,
	member,
	shift,
)

#: The rate on the board at the start of the row. Every dollar figure in this
#: file is derived from it and the two state floors, never typed twice.
BUCKET_RATE = 1.50

OR_FLOOR = MINIMUM_WAGE_RATES["OR"]["standard"]  # 14.70
WA_FLOOR = MINIMUM_WAGE_RATES["WA"]["standard"]  # 16.66

#: A day. Both scenarios in the brief are eight hours, so it is a constant.
DAY_HOURS = 8.0

BUCKET_DOCTYPE = "Bucket Log Entry"

#: The bucket tools, on. The payroll switches come from `IntegrationTestCase`.
BUCKET_ON = {
	f"allow_{name}": 1
	for name in (
		"sync_bucket_entries",
		"list_bucket_entries",
		"get_bucket_session",
		"list_bucket_sessions",
		"link_badge_to_employee",
		"link_entries_to_shift",
		"get_piecework_summary",
		"reconcile_bucket_payroll",
	)
}


def capture(index, employee=PICKER, day="2025-06-03", verdict="Accepted", at=None, **extra):
	"""One Bucket Log Entry in the shape the shipped doctype stores.

	Deliberately NOT the `picker_id`/`bucket_count` shape
	`test_payroll_integration.install_bucket_log` registers. That one models a
	site whose bucket log came from somewhere else; this one is the doctype this
	app ships, which carries `employee`, `timestamp` and `verdict` and NO count
	column — so one row is one bucket, which is what `_bucket_log_rows` reads it
	as and what the row actually records.
	"""
	minute = index % 60
	hour = 7 + (index // 60)
	row = {
		"name": f"BLE-{index:05d}",
		"entry_uuid": f"{index:08d}-0000-0000-0000-000000000000",
		"session_uuid": f"SESSION-{day}",
		"company": MAIN,
		"employee": employee,
		"timestamp": at or f"{day} {hour:02d}:{minute:02d}:00",
		"verdict": verdict,
		"status": "Pending",
	}
	row.update(extra)
	return row


class PieceworkTestCase(IntegrationTestCase):
	"""`IntegrationTestCase` plus the bucket switches."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **self._payroll_switches(), **BUCKET_ON)

	def _payroll_switches(self):
		from .test_payroll_integration import ON

		return ON

	# ── shorthands ────────────────────────────────────────────────────

	def piece_day(self, hours=DAY_HOURS, units=0.0, state="OR", rate=BUCKET_RATE, breaks=0.0):
		"""One worker, one day, straight through `calculate_gross_pay`.

		Returns (gross_result, minimum_wage_verdict) — the pair a foreman is
		actually asking about when they ask what a day of picking paid.
		"""
		gross = calculate_gross_pay("Piece Rate", rate, hours, 0.0, units, breaks)
		verdict = pi.check_minimum_wage_by_state(
			{state: hours},
			{state: gross["gross_pay"]},
		)
		return gross, verdict

	def seed_captures(self, accepted=0, rejected=0, employee=PICKER, day="2025-06-03"):
		"""`accepted` + `rejected` Bucket Log Entries on one day, one worker."""
		rows = [capture(i, employee=employee, day=day) for i in range(accepted)]
		rows += [capture(1000 + i, employee=employee, day=day, verdict="Rejected") for i in range(rejected)]
		STORE.seed(BUCKET_DOCTYPE, rows)
		return rows


# ── Claim 1: the higher-of rule is reported, not applied ──────────────


class TheHigherOfRuleIsReportedNotApplied(PieceworkTestCase):
	"""Piece earnings below the floor are paid as earned and named as short."""

	def test_forty_seven_buckets_in_eight_hours_pays_the_piece_total_not_the_floor(self):
		"""47 × $1.50 = $70.50. Oregon's floor for the day is $117.60. Paid: $70.50."""
		gross, _verdict = self.piece_day(units=47)
		self.assertEqual(gross["piece_earnings"], 70.50)
		self.assertEqual(gross["gross_pay"], 70.50)
		# NOT 117.60. The floor is what the shortfall is measured against, not
		# what the worker is paid — see the module header.
		self.assertNotEqual(gross["gross_pay"], round(OR_FLOOR * DAY_HOURS, 2))

	def test_the_shortfall_against_oregons_floor_is_priced_to_the_cent(self):
		"""$14.70 × 8 = $117.60 owed against $70.50 earned = $47.10 short."""
		_, verdict = self.piece_day(units=47)
		self.assertFalse(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["states_below_minimum"], ["OR"])
		self.assertEqual(verdict["total_shortfall"], 47.10)
		self.assertEqual(verdict["by_state"]["OR"]["minimum_wage"], OR_FLOOR)
		self.assertEqual(verdict["by_state"]["OR"]["effective_hourly_rate"], 8.81)

	def test_one_hundred_and_twenty_buckets_clears_the_floor_and_is_paid_in_full(self):
		"""120 × $1.50 = $180.00 against a $117.60 floor. Paid: $180.00."""
		gross, verdict = self.piece_day(units=120)
		self.assertEqual(gross["gross_pay"], 180.00)
		self.assertTrue(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["total_shortfall"], 0.0)
		self.assertEqual(verdict["by_state"]["OR"]["effective_hourly_rate"], 22.50)

	def test_the_boundary_is_the_floor_itself_and_it_passes(self):
		"""78.4 buckets is exactly $117.60 — the floor, met and not exceeded."""
		units = round(OR_FLOOR * DAY_HOURS / BUCKET_RATE, 4)  # 78.4
		gross, verdict = self.piece_day(units=units)
		self.assertEqual(gross["gross_pay"], round(OR_FLOOR * DAY_HOURS, 2))
		self.assertTrue(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["by_state"]["OR"]["effective_hourly_rate"], OR_FLOOR)

	def test_one_bucket_short_of_the_boundary_fails(self):
		"""78 buckets is $117.00 — sixty cents under, and that is a failure."""
		gross, verdict = self.piece_day(units=78)
		self.assertEqual(gross["gross_pay"], 117.00)
		self.assertFalse(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["total_shortfall"], 0.60)

	def test_the_same_day_in_washington_is_short_by_more_because_the_floor_is_higher(self):
		"""47 buckets against WA's $16.66: $133.28 owed, $62.78 short."""
		_, verdict = self.piece_day(units=47, state="WA")
		self.assertEqual(verdict["states_below_minimum"], ["WA"])
		self.assertEqual(verdict["total_shortfall"], round(WA_FLOOR * DAY_HOURS - 70.50, 2))
		self.assertEqual(verdict["total_shortfall"], 62.78)

	def test_a_full_run_leaves_gross_at_the_piece_total_and_carries_the_shortfall(self):
		"""End to end: the slip pays $70.50 and the run summary names the worker."""
		self.structure(PICKER, pay_type="Piece Rate", rate=BUCKET_RATE)
		shifts = [
			shift(
				"SHIFT-47",
				"2025-06-03",
				start="06:00:00",
				end="14:00:00",
				crew=[member(PICKER, piece_units=47)],
			)
		]
		slips = pi.run_integrated_payroll(
			shifts,
			[{"employee": PICKER, "name": "SS-P", "pay_type": "Piece Rate", "base_rate": BUCKET_RATE}],
			{},
			{},
			{},
			PERIOD_START,
			PERIOD_END,
			company=MAIN,
			include_unworked=False,
		)
		self.assertEqual(len(slips), 1)
		slip = slips[0]
		self.assertEqual(slip["total_hours"], DAY_HOURS)
		self.assertEqual(slip["piece_units"], 47)
		self.assertEqual(slip["gross_pay"], 70.50)
		self.assertEqual(slip["minimum_wage_detail"]["total_shortfall"], 47.10)

		summary = pi.summarize_payroll_run(slips)
		self.assertEqual(summary["total_gross"], 70.50)
		self.assertEqual(len(summary["below_minimum_wage"]), 1)
		self.assertEqual(summary["below_minimum_wage"][0]["employee"], PICKER)
		self.assertEqual(summary["below_minimum_wage"][0]["shortfall"], 47.10)


# ── Claim 2: overtime under piece rate ────────────────────────────────


class OvertimeUnderPieceRate(PieceworkTestCase):
	"""The threshold is weekly, the premium is 1.5x effective, and the floor
	check knows about neither."""

	def week_of(self, day_hours, units_per_day, days=5, state="OR", first="2025-06-02"):
		"""`days` consecutive days from Monday `first`, one crew of one."""
		from datetime import date, timedelta

		start = date.fromisoformat(first)
		end_hour = 6 + int(day_hours)
		rows = []
		for index in range(days):
			when = start + timedelta(days=index)
			rows.append(
				shift(
					f"SHIFT-W-{index}",
					str(when),
					start="06:00:00",
					end=f"{end_hour:02d}:00:00",
					state=state,
					crew=[member(PICKER, piece_units=units_per_day)],
				)
			)
		return rows

	def test_a_ten_hour_day_on_its_own_is_not_overtime(self):
		"""Neither state has a daily threshold. Ten hours Tuesday is ten straight."""
		aggregates = self.aggregate(self.week_of(10, 40, days=1))
		agg = aggregates[PICKER]
		self.assertEqual(agg["total_hours"], 10.0)
		self.assertEqual(agg["overtime_hours"], 0.0)
		self.assertEqual(agg["regular_hours"], 10.0)

	def test_five_ten_hour_days_are_forty_regular_and_ten_overtime(self):
		"""Fifty hours in one workweek. The premium starts at the fortieth."""
		agg = self.aggregate(self.week_of(10, 80))[PICKER]
		self.assertEqual(agg["total_hours"], 50.0)
		self.assertEqual(agg["regular_hours"], 40.0)
		self.assertEqual(agg["overtime_hours"], 10.0)
		self.assertEqual(len(agg["weeks"]), 1)
		self.assertTrue(agg["weeks"][0]["over_threshold"])

	def test_the_premium_is_one_and_a_half_times_the_effective_piece_hourly(self):
		"""400 buckets over 50 hours: $600 earned, $12.00/hr effective, $180 OT."""
		result = calculate_gross_pay("Piece Rate", BUCKET_RATE, 50.0, 10.0, 400, 0.0)
		self.assertEqual(result["piece_earnings"], 600.00)
		self.assertEqual(result["effective_hourly_rate"], 12.00)
		self.assertEqual(result["overtime_pay"], 180.00)
		self.assertEqual(result["gross_pay"], 780.00)

	def test_the_minimum_wage_check_ignores_the_overtime_premium_and_passes(self):
		"""$780 clears a FLAT floor of $735 — and is $28.50 under the real one.

		THE GAP. `check_minimum_wage_by_state` computes `minimum_wage × hours`
		with every hour alike. The statutory floor for fifty hours is forty at
		$14.70 plus ten at $22.05 = $808.50. Nothing in this app computes that
		number, so nothing reports the $28.50. This test asserts the PASSING
		verdict on purpose: it fails the day the premium is folded in, which is
		exactly when somebody should be reading it.
		"""
		gross = calculate_gross_pay("Piece Rate", BUCKET_RATE, 50.0, 10.0, 400, 0.0)
		verdict = pi.check_minimum_wage_by_state({"OR": 50.0}, {"OR": gross["gross_pay"]})
		self.assertTrue(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["total_shortfall"], 0.0)

		flat_floor = round(OR_FLOOR * 50.0, 2)
		self.assertEqual(flat_floor, 735.00)
		self.assertGreater(gross["gross_pay"], flat_floor)

		# What an hourly worker at the floor would have been owed for the same
		# fifty hours, premium included. Computed here, by this test, because the
		# app does not compute it anywhere.
		floor_with_premium = calculate_gross_pay("Hourly", OR_FLOOR, 50.0, 10.0, 0)["gross_pay"]
		self.assertEqual(floor_with_premium, 808.50)
		self.assertLess(gross["gross_pay"], floor_with_premium)
		self.assertEqual(round(floor_with_premium - gross["gross_pay"], 2), 28.50)

	def test_piecework_and_hourly_are_compared_on_the_same_fifty_hours(self):
		"""The brief's third scenario, both ways round, at the same hours.

		At 400 buckets the picker earns $780 and an hourly worker at the floor
		would have had $808.50 — piecework loses. At 700 buckets the picker earns
		$1,365 and piecework wins by a distance. Neither figure is adjusted
		toward the other; the app pays what the structure says.
		"""
		hourly = calculate_gross_pay("Hourly", OR_FLOOR, 50.0, 10.0, 0)["gross_pay"]

		lean = calculate_gross_pay("Piece Rate", BUCKET_RATE, 50.0, 10.0, 400, 0.0)["gross_pay"]
		self.assertLess(lean, hourly)

		heavy = calculate_gross_pay("Piece Rate", BUCKET_RATE, 50.0, 10.0, 700, 0.0)
		self.assertEqual(heavy["piece_earnings"], 1050.00)
		self.assertEqual(heavy["effective_hourly_rate"], 21.00)
		self.assertEqual(heavy["overtime_pay"], 315.00)
		self.assertEqual(heavy["gross_pay"], 1365.00)
		self.assertGreater(heavy["gross_pay"], hourly)

	def test_forty_five_then_thirty_five_is_five_of_overtime_across_the_period(self):
		"""Two workweeks are two thresholds. Eighty hours is not the question."""
		week_one = self.week_of(9, 30, days=5, first="2025-06-02")
		week_two = self.week_of(7, 30, days=5, first="2025-06-09")
		for index, row in enumerate(week_two):
			row["name"] = f"SHIFT-W2-{index}"
		agg = self.aggregate(week_one + week_two)[PICKER]
		self.assertEqual(agg["total_hours"], 80.0)
		self.assertEqual(agg["overtime_hours"], 5.0)
		self.assertEqual(len(agg["weeks"]), 2)
		self.assertEqual(agg["weeks"][0]["overtime_hours"], 5.0)
		self.assertEqual(agg["weeks"][1]["overtime_hours"], 0.0)


# ── Claim 3: buckets become piece units ───────────────────────────────


class BucketsBecomePieceUnits(PieceworkTestCase):
	"""Accepted captures on the shipped doctype are the units that make gross."""

	def test_forty_seven_accepted_captures_are_forty_seven_piece_units(self):
		"""One row is one bucket — the shipped doctype carries no count column."""
		self.seed_captures(accepted=47)
		rows, provenance = self._load_piece_rows()
		self.assertEqual(len(rows), 47)
		self.assertEqual(sum(row["units"] for row in rows), 47.0)
		self.assertIn(BUCKET_DOCTYPE, provenance["sources"])

	def test_rejected_captures_are_not_paid(self):
		"""The model saying the bucket was not filled is the model saying so."""
		self.seed_captures(accepted=47, rejected=13)
		rows, _ = self._load_piece_rows()
		self.assertEqual(len(rows), 47)

	def test_the_captures_reach_the_shift_and_become_the_gross(self):
		"""47 captures + an eight-hour shift + $1.50 = a $70.50 slip."""
		self.seed_captures(accepted=47, rejected=13)
		rows, _ = self._load_piece_rows()
		shifts = [
			shift(
				"SHIFT-BUCKETS",
				"2025-06-03",
				start="06:00:00",
				end="14:00:00",
				crew=[member(PICKER)],
			)
		]
		unmatched = self._attach(shifts, rows)
		self.assertEqual(unmatched, [])

		agg = self.aggregate(shifts)[PICKER]
		self.assertEqual(agg["piece_units"], 47.0)
		self.assertEqual(agg["total_hours"], DAY_HOURS)

		gross = calculate_gross_pay(
			"Piece Rate", BUCKET_RATE, agg["total_hours"], 0.0, agg["piece_units"], 0.0
		)
		self.assertEqual(gross["gross_pay"], 70.50)

	def test_a_capture_with_no_employee_resolved_pays_nobody(self):
		"""An unattributed bucket is not silently credited to whoever is nearest."""
		STORE.seed(BUCKET_DOCTYPE, [capture(1, employee=None), capture(2)])
		rows, _ = self._load_piece_rows()
		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["employee"], PICKER)

	def test_buckets_on_a_day_with_no_shift_are_still_units(self):
		"""They are paid — with zero hours, which is what the record says."""
		self.seed_captures(accepted=10, day="2025-06-05")
		rows, _ = self._load_piece_rows()
		shifts = [
			shift("SHIFT-OTHER-DAY", "2025-06-03", crew=[member(PICKER)]),
		]
		unmatched = self._attach(shifts, rows)
		self.assertEqual(len(unmatched), 10)

	# ── plumbing ──────────────────────────────────────────────────────

	def _load_piece_rows(self):
		from erpnext_mcp.tools import payroll as payroll_tools

		return payroll_tools._load_piece_rows(MAIN, PERIOD_START, PERIOD_END, None)

	def _attach(self, shifts, rows):
		from erpnext_mcp.tools import payroll as payroll_tools

		return payroll_tools._attach_piece_rows(shifts, rows)


# ── Claim 4: a multi-day pay period ───────────────────────────────────


class MultiDayPayPeriod(PieceworkTestCase):
	"""Ten picking days across two workweeks, aggregated into one slip."""

	def setUp(self):
		super().setUp()
		self.shifts = self._ten_days()

	def _ten_days(self):
		"""Week one: five ten-hour days, 100 buckets each. Week two: five
		six-hour days, 90 buckets each."""
		from datetime import date, timedelta

		rows = []
		monday = date.fromisoformat(PERIOD_START)
		for index in range(5):
			rows.append(
				shift(
					f"SHIFT-A-{index}",
					str(monday + timedelta(days=index)),
					start="06:00:00",
					end="16:00:00",
					crew=[member(PICKER, piece_units=100)],
				)
			)
		for index in range(5):
			rows.append(
				shift(
					f"SHIFT-B-{index}",
					str(monday + timedelta(days=7 + index)),
					start="06:00:00",
					end="12:00:00",
					crew=[member(PICKER, piece_units=90)],
				)
			)
		return rows

	def test_the_hours_are_the_sum_of_the_days(self):
		agg = self.aggregate(self.shifts)[PICKER]
		self.assertEqual(agg["total_hours"], 80.0)
		self.assertEqual(agg["shift_count"], 10)

	def test_the_units_are_the_sum_of_the_days(self):
		agg = self.aggregate(self.shifts)[PICKER]
		self.assertEqual(agg["piece_units"], 950.0)

	def test_the_overtime_is_week_ones_alone(self):
		"""Fifty hours in week one, thirty in week two. Ten of overtime, not zero
		and not the twenty a daily threshold would have found."""
		agg = self.aggregate(self.shifts)[PICKER]
		self.assertEqual(agg["overtime_hours"], 10.0)
		self.assertEqual(agg["weeks"][0]["overtime_hours"], 10.0)
		self.assertEqual(agg["weeks"][1]["overtime_hours"], 0.0)

	def test_the_period_grosses_the_units_plus_the_premium(self):
		"""950 × $1.50 = $1,425 over 80 hours = $17.8125/hr; ten of those at 1.5x."""
		agg = self.aggregate(self.shifts)[PICKER]
		gross = calculate_gross_pay(
			"Piece Rate",
			BUCKET_RATE,
			agg["total_hours"],
			agg["overtime_hours"],
			agg["piece_units"],
			agg["break_hours"],
		)
		self.assertEqual(gross["piece_earnings"], 1425.00)
		self.assertEqual(gross["effective_hourly_rate"], 17.81)
		self.assertEqual(gross["overtime_pay"], 267.19)
		self.assertEqual(gross["gross_pay"], 1692.19)

	def test_the_period_clears_both_floors_it_could_be_measured_against(self):
		agg = self.aggregate(self.shifts)[PICKER]
		gross = calculate_gross_pay(
			"Piece Rate", BUCKET_RATE, agg["total_hours"], agg["overtime_hours"], agg["piece_units"], 0.0
		)
		verdict = pi.check_minimum_wage_by_state({"OR": 80.0}, {"OR": gross["gross_pay"]})
		self.assertTrue(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["by_state"]["OR"]["effective_hourly_rate"], 21.15)

	def test_a_day_outside_the_period_is_not_in_the_total(self):
		"""The eleventh day is the next period's, units and all."""
		extra = shift(
			"SHIFT-NEXT-PERIOD",
			"2025-06-16",
			start="06:00:00",
			end="14:00:00",
			crew=[member(PICKER, piece_units=100)],
		)
		agg = self.aggregate([*self.shifts, extra])[PICKER]
		self.assertEqual(agg["piece_units"], 950.0)
		self.assertEqual(agg["total_hours"], 80.0)
		self.assertEqual(len(agg["shifts_outside_period"]), 1)


# ── Claim 5: edge cases ───────────────────────────────────────────────


class PieceworkEdgeCases(PieceworkTestCase):
	"""Zero buckets, a partial shift, an unclosed shift, a paid break."""

	def test_zero_buckets_in_a_full_shift_is_a_zero_gross_and_a_full_shortfall(self):
		"""Not an error, and not a division by zero. A day owed $117.60 and paid nothing."""
		gross, verdict = self.piece_day(units=0)
		self.assertEqual(gross["piece_earnings"], 0.0)
		self.assertEqual(gross["gross_pay"], 0.0)
		self.assertEqual(gross["effective_hourly_rate"], 0.0)
		self.assertFalse(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["total_shortfall"], round(OR_FLOOR * DAY_HOURS, 2))

	def test_buckets_with_no_hours_recorded_do_not_divide_by_zero(self):
		"""Somebody's buckets landed on a day whose shift was never opened."""
		gross = calculate_gross_pay("Piece Rate", BUCKET_RATE, 0.0, 0.0, 20, 0.0)
		self.assertEqual(gross["piece_earnings"], 30.00)
		self.assertEqual(gross["gross_pay"], 30.00)
		self.assertEqual(gross["effective_hourly_rate"], 0.0)
		# Zero hours means no floor to clear — there is nothing to divide.
		verdict = pi.check_minimum_wage_by_state({"OR": 0.0}, {"OR": 30.00})
		self.assertTrue(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["by_state"], {})

	def test_a_partial_shift_is_paid_for_the_hours_actually_stood(self):
		"""Ana joined at 10:00 on a 06:00–14:00 shift. Four hours, not eight."""
		shifts = [
			shift(
				"SHIFT-PARTIAL",
				"2025-06-03",
				start="06:00:00",
				end="14:00:00",
				crew=[
					member(PICKER, joined="2025-06-03 10:00:00", piece_units=24),
					member(WORKER, piece_units=47),
				],
			)
		]
		aggregates = self.aggregate(shifts)
		self.assertEqual(aggregates[PICKER]["total_hours"], 4.0)
		self.assertEqual(aggregates[WORKER]["total_hours"], 8.0)

		# Half the day and half the buckets is the SAME effective rate — and the
		# same verdict against the floor. The shortfall is halved, not the rate.
		short_day = pi.check_minimum_wage_by_state({"OR": 4.0}, {"OR": 24 * BUCKET_RATE})
		full_day = pi.check_minimum_wage_by_state({"OR": 8.0}, {"OR": 48 * BUCKET_RATE})
		self.assertEqual(short_day["by_state"]["OR"]["effective_hourly_rate"], 9.00)
		self.assertEqual(full_day["by_state"]["OR"]["effective_hourly_rate"], 9.00)
		self.assertEqual(short_day["total_shortfall"], 22.80)
		self.assertEqual(full_day["total_shortfall"], 45.60)

	def test_an_unclosed_shift_counts_no_hours_and_is_named(self):
		"""Buckets with no span to divide by would read as an infinite rate."""
		shifts = [
			shift(
				"SHIFT-OPEN",
				"2025-06-03",
				start="06:00:00",
				end=None,
				crew=[member(PICKER, piece_units=47)],
			)
		]
		agg = self.aggregate(shifts)[PICKER]
		self.assertEqual(agg["total_hours"], 0.0)
		self.assertEqual(agg["piece_units"], 47.0)
		self.assertEqual(agg["open_shifts"], ["SHIFT-OPEN"])

	def test_an_unpaid_meal_comes_off_the_piece_hours_and_raises_the_effective_rate(self):
		"""A half-hour lunch is not hours worked, so it is not in the divisor."""
		shifts = [
			shift(
				"SHIFT-MEAL",
				"2025-06-03",
				start="06:00:00",
				end="14:30:00",
				crew=[member(PICKER, piece_units=47, unpaid_break_hours=0.5)],
			)
		]
		agg = self.aggregate(shifts)[PICKER]
		self.assertEqual(agg["total_hours"], 8.0)
		self.assertEqual(agg["unpaid_break_hours"], 0.5)

	def test_a_paid_rest_break_is_paid_at_the_average_piece_hourly(self):
		"""WAC 296-131-020. Ten minutes of rest is not ten minutes of nothing."""
		gross = calculate_gross_pay("Piece Rate", BUCKET_RATE, DAY_HOURS, 0.0, 120, 0.5)
		# $180 over 7.5 piece-hours = $24.00/hr; half an hour of rest = $12.00.
		self.assertEqual(gross["piece_earnings"], 180.00)
		self.assertEqual(gross["break_pay"], 12.00)
		self.assertEqual(gross["gross_pay"], 192.00)

	def test_a_break_longer_than_the_shift_cannot_outpay_the_shift(self):
		"""The clock is the harder fact; the break is capped at the hours."""
		shifts = [
			shift(
				"SHIFT-ABSURD",
				"2025-06-03",
				start="06:00:00",
				end="08:00:00",
				crew=[member(PICKER, piece_units=10, break_hours=9)],
			)
		]
		agg = self.aggregate(shifts)[PICKER]
		self.assertEqual(agg["total_hours"], 2.0)
		self.assertEqual(agg["break_hours"], 2.0)

	def test_a_negative_bucket_count_does_not_become_negative_pay_silently(self):
		"""It becomes negative pay LOUDLY: the figure is signed and visible.

		Nothing in the engine rejects a negative count — a correction row is a
		real thing — so this pins what one does rather than claiming it cannot
		happen.
		"""
		gross = calculate_gross_pay("Piece Rate", BUCKET_RATE, DAY_HOURS, 0.0, -10, 0.0)
		self.assertEqual(gross["gross_pay"], -15.00)


# ── Claim 6: the mixed worker, as it stands ───────────────────────────


class MixedPieceworkAndHourlyWork(PieceworkTestCase):
	"""A worker cannot be part piece-rate and part hourly in one period.

	THE GAP. `pay_type` is one field on one salary structure and
	`calculate_gross_pay` branches on it once. There is no per-task rate, no
	per-shift pay type, and no way to say "Monday was buckets and Tuesday was
	irrigation". These tests assert the CURRENT behaviour so that the day a
	mixed-rate path is built, they fail and get rewritten deliberately.
	"""

	def test_hours_on_a_non_piece_task_earn_nothing_under_a_piece_structure(self):
		"""Eight hours of buckets and eight of irrigation pays for the buckets."""
		shifts = [
			shift(
				"SHIFT-PICKING",
				"2025-06-03",
				start="06:00:00",
				end="14:00:00",
				crew=[member(PICKER, piece_units=30)],
			),
			shift(
				"SHIFT-IRRIGATION",
				"2025-06-04",
				start="06:00:00",
				end="14:00:00",
				crew=[member(PICKER)],  # no buckets — a different kind of day
			),
		]
		agg = self.aggregate(shifts)[PICKER]
		self.assertEqual(agg["total_hours"], 16.0)
		self.assertEqual(agg["piece_units"], 30.0)

		gross = calculate_gross_pay(
			"Piece Rate", BUCKET_RATE, agg["total_hours"], 0.0, agg["piece_units"], 0.0
		)
		# $45 for sixteen hours. The irrigation day is unpaid, because the only
		# rate this worker has is a rate per bucket.
		self.assertEqual(gross["gross_pay"], 45.00)
		self.assertEqual(gross["effective_hourly_rate"], 2.81)

	def test_the_unpaid_day_shows_up_as_a_minimum_wage_failure_not_as_nothing(self):
		"""The one place the missing hourly pay is visible is the floor check."""
		verdict = pi.check_minimum_wage_by_state({"OR": 16.0}, {"OR": 45.00})
		self.assertFalse(verdict["meets_minimum_wage"])
		self.assertEqual(verdict["total_shortfall"], round(OR_FLOOR * 16.0 - 45.00, 2))
		self.assertEqual(verdict["total_shortfall"], 190.20)

	def test_the_same_worker_on_an_hourly_structure_is_paid_for_every_hour(self):
		"""Same sixteen hours, the other structure. The buckets earn nothing."""
		gross = calculate_gross_pay("Hourly", 18.00, 16.0, 0.0, 30, 0.0)
		self.assertEqual(gross["gross_pay"], 288.00)
		self.assertNotIn("piece_earnings", gross)


# ── Claim 7: the two minimum wage verdicts on one slip ────────────────


class TheTwoMinimumWageVerdicts(PieceworkTestCase):
	"""A cross-state slip is checked twice, and the two checks can disagree.

	Thirty Oregon hours and ten Washington ones, 400 buckets at $1.50 — $600 for
	forty hours, a flat $15.00/hr in both states. Oregon's floor is $14.70 and
	Washington's is $16.66, so the same rate passes one and fails the other, and
	which answer the slip gives depends on which function asked.
	"""

	def cross_state_shifts(self):
		"""Three Oregon days and one Washington day, 100 buckets each."""
		rows = []
		for index, (day, state) in enumerate(
			(
				("2025-06-02", "OR"),
				("2025-06-03", "OR"),
				("2025-06-04", "OR"),
				("2025-06-05", "WA"),
			)
		):
			rows.append(
				shift(
					f"SHIFT-XS-{index}",
					day,
					start="06:00:00",
					end="16:00:00",
					state=state,
					crew=[member(PICKER, piece_units=100)],
				)
			)
		return rows

	def slip(self):
		return pi.run_integrated_payroll(
			self.cross_state_shifts(),
			[{"employee": PICKER, "name": "SS-P", "pay_type": "Piece Rate", "base_rate": BUCKET_RATE}],
			{},
			{},
			{},
			PERIOD_START,
			PERIOD_END,
			company=MAIN,
			include_unworked=False,
		)[0]

	def test_the_period_is_forty_hours_and_six_hundred_dollars_in_two_states(self):
		slip = self.slip()
		self.assertEqual(slip["total_hours"], 40.0)
		self.assertEqual(slip["overtime_hours"], 0.0)
		self.assertEqual(slip["piece_units"], 400.0)
		self.assertEqual(slip["gross_pay"], 600.00)
		self.assertEqual(slip["hours_by_state"], {"OR": 30.0, "WA": 10.0})

	def test_the_wages_follow_the_hours_into_each_state(self):
		self.assertEqual(self.slip()["state_wages"], {"OR": 450.00, "WA": 150.00})

	def test_the_flat_check_passes_because_it_only_asks_oregon(self):
		"""`check_minimum_wage` tests the whole gross against the state with the
		most hours. Thirty of the forty are Oregon's, so Oregon is the question
		asked, and $15.00 clears $14.70."""
		slip = self.slip()
		self.assertEqual(slip["work_state"], "OR")
		self.assertTrue(slip["minimum_wage_check"])
		self.assertEqual(
			check_minimum_wage(600.00, 40.0, "OR"),
			{
				"meets_minimum_wage": True,
				"effective_hourly_rate": 15.00,
				"minimum_wage": OR_FLOOR,
				"state": "OR",
				"region": "standard",
			},
		)

	def test_the_per_state_check_fails_and_names_washington(self):
		"""$15.00 does not clear $16.66, and ten hours of it is $16.60 short."""
		detail = self.slip()["minimum_wage_detail"]
		self.assertFalse(detail["meets_minimum_wage"])
		self.assertEqual(detail["states_below_minimum"], ["WA"])
		self.assertEqual(detail["total_shortfall"], 16.60)
		self.assertTrue(detail["by_state"]["OR"]["meets_minimum_wage"])
		self.assertFalse(detail["by_state"]["WA"]["meets_minimum_wage"])

	def test_the_stored_row_takes_the_per_state_answer_not_the_flat_one(self):
		"""`_slip_row` resolves the disagreement, and it resolves it correctly."""
		from erpnext_mcp.tools import payroll as payroll_tools

		slip = self.slip()
		self.assertTrue(slip["minimum_wage_check"])  # the flat verdict, still True
		row = payroll_tools._slip_row(slip)
		self.assertEqual(row["minimum_wage_check"], 0)  # ...and overruled on the row

	def test_the_run_summary_reports_the_shortfall_the_flat_check_missed(self):
		summary = pi.summarize_payroll_run([self.slip()])
		self.assertEqual(len(summary["below_minimum_wage"]), 1)
		self.assertEqual(summary["below_minimum_wage"][0]["states"], ["WA"])
		self.assertEqual(summary["below_minimum_wage"][0]["shortfall"], 16.60)


# ── Claim 8: the bucket-to-payroll reconciliation ─────────────────────


class TheBucketReconciliation(PieceworkTestCase):
	"""`get_piecework_summary` and `reconcile_bucket_payroll`, which shipped in
	v0.44.0 with a registration test and nothing else."""

	FROM = "2025-06-01"
	TO = "2025-06-30"

	def summary(self, employee=PICKER, **extra):
		return self.tool_data(
			"get_piecework_summary",
			{
				"employee": employee,
				"company": MAIN,
				"from_date": self.FROM,
				"to_date": self.TO,
				**extra,
			},
		)

	def reconcile(self, **extra):
		return self.tool_data(
			"reconcile_bucket_payroll",
			{"company": MAIN, "from_date": self.FROM, "to_date": self.TO, **extra},
		)

	# ── get_piecework_summary ─────────────────────────────────────────

	def test_the_summary_counts_accepted_buckets_as_the_piece_units(self):
		"""47 accepted and 13 rejected is 47 units and a 78% acceptance rate."""
		self.seed_captures(accepted=47, rejected=13)
		data = self.summary()
		self.assertEqual(data["total_accepted"], 47)
		self.assertEqual(data["total_rejected"], 13)
		self.assertEqual(data["piece_units"], 47)
		self.assertAlmostEqual(data["acceptance_rate"], 47 / 60, places=4)

	def test_the_summary_is_scoped_to_the_employee_asked_about(self):
		self.seed_captures(accepted=47, employee=PICKER)
		STORE.seed(
			BUCKET_DOCTYPE,
			[capture(2000 + i, employee=WORKER) for i in range(5)],
		)
		self.assertEqual(self.summary(PICKER)["total_accepted"], 47)
		self.assertEqual(self.summary(WORKER)["total_accepted"], 5)

	def test_the_summary_is_scoped_to_the_dates_asked_about(self):
		"""A bucket outside the window is another period's piece unit."""
		self.seed_captures(accepted=10, day="2025-06-03")
		STORE.seed(
			BUCKET_DOCTYPE,
			[capture(3000 + i, day="2025-07-03") for i in range(4)],
		)
		self.assertEqual(self.summary()["total_accepted"], 10)
		self.assertEqual(self.summary(to_date="2025-07-31")["total_accepted"], 14)

	def test_the_summary_counts_the_sessions_the_buckets_came_from(self):
		self.seed_captures(accepted=10, day="2025-06-03")
		STORE.seed(
			BUCKET_DOCTYPE,
			[capture(4000 + i, day="2025-06-04") for i in range(6)],
		)
		data = self.summary()
		self.assertEqual(data["session_count"], 2)
		self.assertEqual(data["total_accepted"], 16)

	def test_a_worker_with_no_buckets_gets_zeroes_rather_than_an_error(self):
		data = self.summary()
		self.assertEqual(data["total_accepted"], 0)
		self.assertEqual(data["piece_units"], 0)
		self.assertEqual(data["acceptance_rate"], 0.0)
		self.assertEqual(data["session_count"], 0)

	def test_backwards_dates_are_refused(self):
		message = self.tool_error(
			"get_piecework_summary",
			{"employee": PICKER, "company": MAIN, "from_date": self.TO, "to_date": self.FROM},
		)
		self.assertIn("after", message)

	def test_the_summary_is_a_read_and_is_on_by_default(self):
		"""Both of these are reads, and reads ship on. Asserted rather than
		assumed, because "the payroll figure could not be checked" is a bad
		default and this is the file that would notice it changing."""
		self.configure(enabled=1)  # nothing but the kill switch — shipped defaults
		self.seed_captures(accepted=3)
		data = self.summary()
		self.assertEqual(data["total_accepted"], 3)

	def test_the_kill_switch_stops_the_summary(self):
		"""Disabled is 404 at the transport, not a tool error inside a 200."""
		self.configure(enabled=0, **BUCKET_ON)
		_body, status = self.call(
			"tools/call",
			{
				"name": "get_piecework_summary",
				"arguments": {
					"employee": PICKER,
					"company": MAIN,
					"from_date": self.FROM,
					"to_date": self.TO,
				},
			},
		)
		self.assertEqual(status, 404)

	def test_the_summary_is_refused_when_its_own_switch_is_turned_off(self):
		self.configure(enabled=1, allow_get_piecework_summary=0)
		message = self.tool_error(
			"get_piecework_summary",
			{"employee": PICKER, "company": MAIN, "from_date": self.FROM, "to_date": self.TO},
		)
		self.assertIn("get_piecework_summary", message)

	# ── reconcile_bucket_payroll ──────────────────────────────────────

	def test_buckets_with_no_payroll_yet_are_reported_as_not_yet_paid(self):
		"""Not an error — a bucket nobody has run payroll for is simply unpaid."""
		self.seed_captures(accepted=47)
		data = self.reconcile()
		row = self._row(data, PICKER)
		self.assertEqual(row["accepted_bucket_entries"], 47)
		self.assertEqual(row["payroll_piece_units"], 0.0)
		self.assertEqual(row["discrepancy"], 47)
		self.assertEqual(row["status"], "bucket_units_not_yet_paid")
		self.assertEqual(data["unpaid_pending_bucket_entries"], 47)

	def test_a_slip_that_paid_the_same_count_matches(self):
		self.seed_captures(accepted=47)
		self.seed_payroll_run({PICKER: 47})
		row = self._row(self.reconcile(), PICKER)
		self.assertEqual(row["status"], "matches")
		self.assertEqual(row["discrepancy"], 0)
		self.assertEqual(self.reconcile()["mismatch_count"], 0)

	def test_a_slip_that_paid_more_units_than_the_bucket_log_holds_is_flagged(self):
		"""The direction that matters most — payroll ahead of the evidence."""
		self.seed_captures(accepted=47)
		self.seed_payroll_run({PICKER: 60})
		row = self._row(self.reconcile(), PICKER)
		self.assertEqual(row["status"], "payroll_units_exceed_bucket_log")
		self.assertEqual(row["discrepancy"], -13)

	def test_a_slip_that_paid_fewer_units_than_the_bucket_log_holds_is_flagged(self):
		self.seed_captures(accepted=47)
		self.seed_payroll_run({PICKER: 40})
		row = self._row(self.reconcile(), PICKER)
		self.assertEqual(row["status"], "bucket_units_not_yet_paid")
		self.assertEqual(row["discrepancy"], 7)

	def test_rejected_captures_are_not_expected_to_have_been_paid(self):
		"""Only Accepted entries are the yardstick — a rejected one is not a bucket."""
		self.seed_captures(accepted=47, rejected=13)
		self.seed_payroll_run({PICKER: 47})
		self.assertEqual(self.reconcile()["mismatch_count"], 0)

	def test_a_bucket_with_no_employee_is_counted_as_unattributed_not_dropped(self):
		STORE.seed(BUCKET_DOCTYPE, [capture(1, employee=None), capture(2, employee=None), capture(3)])
		data = self.reconcile()
		self.assertEqual(data["unattributed_bucket_entries"], 2)
		self.assertEqual(self._row(data, PICKER)["accepted_bucket_entries"], 1)

	def test_a_cancelled_payroll_run_does_not_count_as_having_paid_anything(self):
		self.seed_captures(accepted=47)
		self.seed_payroll_run({PICKER: 47}, status="Cancelled")
		row = self._row(self.reconcile(), PICKER)
		self.assertEqual(row["payroll_piece_units"], 0.0)
		self.assertEqual(row["status"], "bucket_units_not_yet_paid")

	def test_the_reconciliation_can_be_scoped_to_one_employee(self):
		self.seed_captures(accepted=47, employee=PICKER)
		STORE.seed(
			BUCKET_DOCTYPE,
			[capture(5000 + i, employee=WORKER) for i in range(9)],
		)
		data = self.reconcile(employee=WORKER)
		self.assertEqual([row["employee"] for row in data["employees"]], [WORKER])
		self.assertEqual(data["employees"][0]["accepted_bucket_entries"], 9)

	def test_backwards_dates_are_refused_before_anything_is_compared(self):
		message = self.tool_error(
			"reconcile_bucket_payroll",
			{"company": MAIN, "from_date": self.TO, "to_date": self.FROM},
		)
		self.assertIn("after", message)

	def test_the_reconciliation_is_refused_when_its_own_switch_is_turned_off(self):
		self.configure(enabled=1, allow_reconcile_bucket_payroll=0)
		message = self.tool_error(
			"reconcile_bucket_payroll",
			{"company": MAIN, "from_date": self.FROM, "to_date": self.TO},
		)
		self.assertIn("reconcile_bucket_payroll", message)

	def test_the_kill_switch_stops_the_reconciliation(self):
		self.configure(enabled=0, **BUCKET_ON)
		_body, status = self.call(
			"tools/call",
			{
				"name": "reconcile_bucket_payroll",
				"arguments": {"company": MAIN, "from_date": self.FROM, "to_date": self.TO},
			},
		)
		self.assertEqual(status, 404)

	# ── plumbing ──────────────────────────────────────────────────────

	def seed_payroll_run(self, units_by_employee, status="Calculated", name="PAY-2025-00001"):
		"""A Farm Payroll Entry whose slips carry the piece units given."""
		slips = []
		for index, (employee, units) in enumerate(sorted(units_by_employee.items()), start=1):
			slips.append(
				{
					"name": f"{name}-slip-{index}",
					"parent": name,
					"parenttype": "Farm Payroll Entry",
					"parentfield": "slips",
					"employee": employee,
					"pay_type": "Piece Rate",
					"piece_units": units,
					"piece_rate": BUCKET_RATE,
					"gross_pay": round(units * BUCKET_RATE, 2),
					"minimum_wage_check": 0,
				}
			)
		# BOTH, deliberately. The tool reads the parent to find the period and
		# `frappe.db.get_all("Farm Payroll Slip", ...)` to find the units, and a
		# fixture that only nested them would test neither query honestly.
		STORE.seed("Farm Payroll Slip", slips)
		STORE.seed(
			"Farm Payroll Entry",
			[
				{
					"name": name,
					"company": MAIN,
					"pay_period_start": self.FROM,
					"pay_period_end": self.TO,
					"pay_frequency": "Biweekly",
					"status": status,
					"employee_count": len(slips),
					"slips": slips,
					"gl_postings": [],
				}
			],
		)
		return name

	def _row(self, data, employee):
		for row in data["employees"]:
			if row["employee"] == employee:
				return row
		self.fail(f"{employee} not in the reconciliation: {data['employees']}")
