# SPDX-License-Identifier: MIT
"""Salary Structures + Payroll Calc Engine — v0.30.0.

TEN CLAIMS.

1. `PieceRateGross` — piece-rate gross pay includes piece earnings, break pay, and OT.
2. `HourlyGrossWithOT` — hourly gross includes regular and overtime at 1.5x.
3. `BreakPayAverage` — break pay is at the average piece-rate hourly.
4. `MinimumWageCheck` — the minimum wage check passes and fails correctly, prices
   the makeup that would close the gap, and puts the overtime premium in the floor.
4b. `MixedPayTypes` — a day paid two ways, by the weighted-average method, and
   agreeing with the single-pay-type path wherever both can answer.
5. `CrossStatePayroll` — a worker with OR and WA shifts in the same period gets both states' taxes.
6. `FullPayrollCalc` — the full orchestrator integrates federal + state + FICA.
7. `SalaryStructureTools` — the CRUD tools for salary structures work.
8. `PayrollEntryTools` — the calculate/submit/get/list tools work.
9. `PreviewPayroll` — the dry-run preview returns results without creating records.
10. `EdgeCases` — zero hours, no shifts, salary pay type, deactivation.
"""

from erpnext_mcp.payroll_calc import (
	MINIMUM_WAGE_RATES,
	calculate_break_pay,
	calculate_full_payroll,
	calculate_gross_pay,
	calculate_mixed_gross_pay,
	calculate_overtime,
	check_minimum_wage,
	minimum_wage_floor,
)
from erpnext_mcp.withholding import ANNUAL_BRACKETS, PERIODS_PER_YEAR

from .fixtures import MAIN, V12TestCase, install_hrms
from .harness import STORE

PAYROLL_TOOLS_ON = {
	f"allow_{name}": 1
	for name in (
		"get_salary_structure",
		"list_salary_structures",
		"preview_payroll",
		"get_payroll_entry",
		"list_payroll_entries",
		"create_salary_structure",
		"deactivate_salary_structure",
		"calculate_payroll",
		"submit_payroll",
	)
}

W4_TOOLS_ON = {
	f"allow_{name}": 1
	for name in (
		"get_w4",
		"list_w4_forms",
		"get_fica_config",
		"get_federal_tax_table",
		"preview_federal_withholding",
		"calculate_payroll_taxes",
		"submit_w4",
		"update_fica_config",
		"import_federal_tax_table",
	)
}

STATE_TOOLS_ON = {
	f"allow_{name}": 1
	for name in (
		"get_state_tax_config",
		"list_state_tax_configs",
		"get_state_tax_table",
		"preview_state_withholding",
		"preview_total_payroll_taxes",
		"list_employees_by_work_state",
		"create_state_tax_config",
		"update_state_tax_config",
		"import_state_tax_table",
	)
}


class PayrollTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **PAYROLL_TOOLS_ON, **W4_TOOLS_ON, **STATE_TOOLS_ON)
		install_hrms()
		self._seed_fica()
		self._seed_brackets()
		self._seed_employee()

	def _seed_fica(self):
		STORE.singles["FICA Configuration"] = {
			"doctype": "FICA Configuration",
			"tax_year": "2025",
			"social_security_rate_employee": "6.2",
			"social_security_rate_employer": "6.2",
			"social_security_wage_base": "176100",
			"medicare_rate_employee": "1.45",
			"medicare_rate_employer": "1.45",
			"additional_medicare_threshold": "200000",
			"additional_medicare_rate": "0.9",
			"futa_rate": "6.0",
			"futa_wage_base": "7000",
			"futa_state_credit_max": "5.4",
		}

	def _seed_brackets(self):
		brackets = []
		for filing_status, annual in ANNUAL_BRACKETS.items():
			for period_name, periods in PERIODS_PER_YEAR.items():
				for bracket in annual:
					floor = bracket["bracket_floor"] / periods
					ceiling = bracket["bracket_ceiling"] / periods if bracket["bracket_ceiling"] else None
					base = bracket["base_tax"] / periods
					brackets.append(
						{
							"name": f"TTB-{filing_status[:3]}-{period_name[:3]}-{floor:.0f}",
							"tax_year": 2025,
							"filing_status": filing_status,
							"payroll_period": period_name,
							"bracket_floor": round(floor, 2),
							"bracket_ceiling": round(ceiling, 2) if ceiling else None,
							"base_tax": round(base, 2),
							"marginal_rate": bracket["marginal_rate"],
						}
					)
		STORE.seed("Federal Tax Table", brackets)

	def _seed_employee(self):
		STORE.seed(
			"Employee",
			[
				{
					"name": "HR-EMP-00001",
					"employee_name": "Test Worker",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": "HR-EMP-00002",
					"employee_name": "Piece Worker",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-03-01",
				},
			],
		)

	def _submit_w4(
		self,
		employee="HR-EMP-00001",
		filing_status="Single or Married Filing Separately",
		tax_year=2025,
		**kwargs,
	):
		args = {
			"employee": employee,
			"company": MAIN,
			"tax_year": tax_year,
			"filing_status": filing_status,
			**kwargs,
		}
		return self.tool_data("submit_w4", args)

	def _default_w4_data(self):
		return {
			"filing_status": "Single",
			"multiple_jobs": False,
			"additional_income_from_other_jobs": 0,
			"dependents_under_17_count": 0,
			"other_dependents_count": 0,
			"total_dependents_credit": 0,
			"other_income": 0,
			"deductions": 0,
			"extra_withholding_per_period": 0,
		}

	def _default_fica(self):
		return {
			"social_security_rate_employee": 6.2,
			"social_security_rate_employer": 6.2,
			"social_security_wage_base": 176100,
			"medicare_rate_employee": 1.45,
			"medicare_rate_employer": 1.45,
			"additional_medicare_threshold": 200000,
			"additional_medicare_rate": 0.9,
			"futa_rate": 6.0,
			"futa_wage_base": 7000,
			"futa_state_credit_max": 5.4,
		}

	def _or_config(self):
		return {
			"or_income_tax_enabled": 1,
			"or_transit_tax_rate": 0.1,
			"or_paid_leave_rate": 1.0,
			"or_paid_leave_employee_share": 60,
			"or_paid_leave_employer_share": 40,
			"or_paid_leave_small_employer": 0,
			"or_workers_comp_rate": 1.5,
		}

	def _wa_config(self):
		return {
			"wa_pfml_rate": 0.92,
			"wa_pfml_employee_share": 72.76,
			"wa_pfml_employer_share": 27.24,
			"wa_cares_rate": 0.58,
			"wa_li_rate_employee": 0.05,
			"wa_li_rate_employer": 0.15,
		}


# ── Claim 1: piece-rate gross pay ──────────────────────────────────────


class PieceRateGross(PayrollTestCase):
	"""Piece-rate gross includes piece earnings, break pay, and OT."""

	def test_basic_piece_rate(self):
		"""100 buckets at $1.50 each = $150 piece earnings."""
		result = calculate_gross_pay("Piece Rate", 1.50, 10, 0, 100, 0)
		self.assertEqual(result["piece_earnings"], 150.0)
		self.assertEqual(result["gross_pay"], 150.0)
		self.assertEqual(result["pay_type"], "Piece Rate")

	def test_piece_rate_with_breaks(self):
		"""Break pay is added at the average piece-rate hourly."""
		result = calculate_gross_pay("Piece Rate", 2.00, 10, 0, 100, 1.0)
		# 100 * 2.00 = 200 piece earnings over 9 piece-hours
		# avg rate = 200/9 = 22.22/hr, break pay = 22.22 * 1 = 22.22
		self.assertEqual(result["piece_earnings"], 200.0)
		self.assertAlmostEqual(result["break_pay"], 22.22, places=2)
		self.assertAlmostEqual(result["gross_pay"], 222.22, places=2)

	def test_piece_rate_with_overtime(self):
		"""OT is the FLSA half-time premium on the effective piece-rate hourly.

		v0.49.0 changed this figure. 29 CFR 778.111: the piece earnings ALREADY
		paid straight time for all forty-five hours, so what the five overtime ones
		are owed on top is half the regular rate, not one and a half of it.
		Releases through v0.48.2 paid $33.33 here — more than the law asks, and
		irreconcilable with the weighted-average method a mixed day needs.
		"""
		result = calculate_gross_pay("Piece Rate", 1.00, 45, 5, 200, 0)
		# 200 * 1.00 = 200 piece earnings over 45 hours
		# regular rate = 200/45 = 4.44/hr
		# OT premium = 5 * 4.44 * 0.5 = 11.11
		self.assertEqual(result["piece_earnings"], 200.0)
		self.assertAlmostEqual(result["overtime_pay"], 11.11, places=2)
		self.assertAlmostEqual(result["gross_pay"], 211.11, places=2)
		self.assertEqual(result["overtime_premium_multiplier"], 0.5)

	def test_the_piece_premium_and_the_hourly_premium_arrive_at_the_same_place(self):
		"""The two multipliers are two halves of one rule, not two rules.

		An hourly worker's regular pay covered only the regular hours, so the whole
		1.5x lands on the overtime ones. A piece-rate worker's earnings covered all
		of them, so half of it is already in hand. Same total, and this asserts it
		rather than trusting two constants to stay in step: 400 buckets at $1.50
		over fifty hours is $12.00 an hour, and fifty hours at $12.00 is the same
		money by either road.
		"""
		piece = calculate_gross_pay("Piece Rate", 1.50, 50, 10, 400, 0)
		self.assertEqual(piece["effective_hourly_rate"], 12.00)
		hourly = calculate_gross_pay("Hourly", 12.00, 50, 10, 0)
		# 40 × 12 + 10 × 12 × 1.5 = 660, and 600 of piece earnings + 10 × 12 × 0.5.
		self.assertEqual(hourly["gross_pay"], 660.00)
		self.assertEqual(piece["gross_pay"], 660.00)


# ── Claim 2: hourly gross with OT ─────────────────────────────────────


class HourlyGrossWithOT(PayrollTestCase):
	"""Hourly gross includes regular pay and overtime at 1.5x."""

	def test_basic_hourly(self):
		"""40 hours at $20/hr = $800."""
		result = calculate_gross_pay("Hourly", 20.0, 40, 0, 0)
		self.assertEqual(result["gross_pay"], 800.0)
		self.assertEqual(result["regular_pay"], 800.0)
		self.assertEqual(result["overtime_pay"], 0.0)

	def test_hourly_with_overtime(self):
		"""45 hours at $20/hr = 40*20 + 5*20*1.5 = 800 + 150 = 950."""
		result = calculate_gross_pay("Hourly", 20.0, 45, 5, 0)
		self.assertEqual(result["regular_pay"], 800.0)
		self.assertEqual(result["overtime_pay"], 150.0)
		self.assertEqual(result["gross_pay"], 950.0)

	def test_all_overtime(self):
		"""10 hours, all OT, at $15/hr."""
		result = calculate_gross_pay("Hourly", 15.0, 10, 10, 0)
		self.assertEqual(result["regular_pay"], 0.0)
		self.assertEqual(result["overtime_pay"], 225.0)
		self.assertEqual(result["gross_pay"], 225.0)


# ── Claim 3: break pay at average piece-rate ───────────────────────────


class BreakPayAverage(PayrollTestCase):
	"""Break pay is at the average piece-rate hourly for both states."""

	def test_break_pay_calculation(self):
		"""Direct calculation of break pay."""
		# 8 piece-hours, earned $160, so avg = $20/hr
		# 0.5 hr break => $10 break pay
		bp = calculate_break_pay(160.0, 8.0, 0.5)
		self.assertEqual(bp, 10.0)

	def test_no_break_hours(self):
		"""Zero break hours means zero break pay."""
		bp = calculate_break_pay(200.0, 10.0, 0)
		self.assertEqual(bp, 0.0)

	def test_no_piece_hours(self):
		"""Zero piece hours means zero break pay (avoid division by zero)."""
		bp = calculate_break_pay(0.0, 0.0, 1.0)
		self.assertEqual(bp, 0.0)


# ── Claim 4: minimum wage check ───────────────────────────────────────


class MinimumWageCheck(PayrollTestCase):
	"""Minimum wage check passes and fails correctly."""

	def test_passes_oregon_standard(self):
		"""$16/hr passes Oregon's $14.70 standard."""
		result = check_minimum_wage(160.0, 10.0, "OR")
		self.assertTrue(result["meets_minimum_wage"])
		self.assertEqual(result["effective_hourly_rate"], 16.0)
		self.assertEqual(result["minimum_wage"], 14.70)

	def test_fails_oregon_standard(self):
		"""$12/hr fails Oregon's $14.70 standard."""
		result = check_minimum_wage(120.0, 10.0, "OR")
		self.assertFalse(result["meets_minimum_wage"])
		self.assertEqual(result["effective_hourly_rate"], 12.0)

	def test_passes_washington(self):
		"""$18/hr passes Washington's $16.66."""
		result = check_minimum_wage(180.0, 10.0, "WA")
		self.assertTrue(result["meets_minimum_wage"])
		self.assertEqual(result["minimum_wage"], 16.66)

	def test_fails_washington(self):
		"""$15/hr fails Washington's $16.66."""
		result = check_minimum_wage(150.0, 10.0, "WA")
		self.assertFalse(result["meets_minimum_wage"])

	def test_portland_metro(self):
		"""Portland metro has a higher minimum wage."""
		result = check_minimum_wage(155.0, 10.0, "OR", region="portland_metro")
		self.assertFalse(result["meets_minimum_wage"])
		self.assertEqual(result["minimum_wage"], 15.95)

	def test_zero_hours(self):
		"""Zero hours means the check passes (no work done)."""
		result = check_minimum_wage(0.0, 0.0, "OR")
		self.assertTrue(result["meets_minimum_wage"])

	# ── v0.49.0: the floor is a figure, and it knows about overtime ────

	def test_the_check_prices_the_makeup_it_would_take_to_reach_the_floor(self):
		"""$120 over ten Oregon hours is $27 short of $147, and it says so."""
		result = check_minimum_wage(120.0, 10.0, "OR")
		self.assertEqual(result["minimum_wage_floor"], 147.00)
		self.assertEqual(result["minimum_wage_makeup"], 27.00)

	def test_pay_that_clears_the_floor_needs_no_makeup(self):
		result = check_minimum_wage(160.0, 10.0, "OR")
		self.assertEqual(result["minimum_wage_makeup"], 0.0)
		self.assertTrue(result["meets_minimum_wage"])

	def test_the_floor_itself_is_met_and_not_a_cent_is_added(self):
		"""Exactly $147.00 for ten Oregon hours. `>=` is the whole assertion."""
		result = check_minimum_wage(147.00, 10.0, "OR")
		self.assertTrue(result["meets_minimum_wage"])
		self.assertEqual(result["minimum_wage_makeup"], 0.0)

	def test_the_floor_carries_the_overtime_premium(self):
		"""Fifty Oregon hours is $808.50, not $735.

		THE GAP v0.48.2 PINNED. Forty at $14.70 is $588 and ten at $22.05 is
		$220.50. A check that multiplied fifty by $14.70 would pass a slip that is
		$73.50 short of what the law asks for the same hours.
		"""
		floor = minimum_wage_floor(50.0, 10.0, MINIMUM_WAGE_RATES["OR"]["standard"])
		self.assertEqual(floor, 808.50)
		self.assertNotEqual(floor, round(50.0 * MINIMUM_WAGE_RATES["OR"]["standard"], 2))

		result = check_minimum_wage(600.0, 50.0, "OR", overtime_hours=10.0)
		self.assertFalse(result["meets_minimum_wage"])
		self.assertEqual(result["minimum_wage_floor"], 808.50)
		self.assertEqual(result["minimum_wage_makeup"], 208.50)

	def test_without_overtime_the_floor_is_the_flat_product(self):
		"""Forty hours is forty hours. The premium only exists past the threshold."""
		self.assertEqual(
			minimum_wage_floor(40.0, 0.0, MINIMUM_WAGE_RATES["OR"]["standard"]),
			588.00,
		)

	def test_overtime_hours_cannot_exceed_the_hours_they_came_out_of(self):
		"""A bad input does not inflate the floor past the hours worked."""
		self.assertEqual(
			minimum_wage_floor(8.0, 40.0, 10.0),
			minimum_wage_floor(8.0, 8.0, 10.0),
		)

	def test_a_state_with_no_floor_on_file_has_no_floor(self):
		"""Idaho is not in the table, so nothing is owed under this rule and
		nothing is invented. The alternative is topping somebody's pay up to a
		number this app made up."""
		result = check_minimum_wage(10.0, 8.0, "ID")
		self.assertTrue(result["meets_minimum_wage"])
		self.assertEqual(result["minimum_wage"], 0.0)
		self.assertEqual(result["minimum_wage_makeup"], 0.0)


# ── Claim 4b: mixed pay types in one period ───────────────────────────


class MixedPayTypes(PayrollTestCase):
	"""A day paid two ways, by the weighted-average method of 29 CFR 778.115."""

	def test_a_picking_morning_and_an_irrigation_afternoon_are_both_paid(self):
		"""Six hours of buckets at $1.50 and two of irrigation at $16.00."""
		result = calculate_mixed_gross_pay(
			[
				{"pay_type": "Piece Rate", "rate": 1.50, "hours": 6.0, "piece_units": 90},
				{"pay_type": "Hourly", "rate": 16.00, "hours": 2.0},
			]
		)
		self.assertEqual(result["piece_earnings"], 135.00)
		self.assertEqual(result["hourly_earnings"], 32.00)
		self.assertEqual(result["gross_pay"], 167.00)
		self.assertEqual(result["pay_type"], "Mixed")

	def test_the_regular_rate_is_the_whole_lot_over_all_the_hours(self):
		"""$167 over eight hours is $20.88 — not the piece rate and not the
		hourly one. 29 CFR 778.115: one regular rate for the workweek."""
		result = calculate_mixed_gross_pay(
			[
				{"pay_type": "Piece Rate", "rate": 1.50, "hours": 6.0, "piece_units": 90},
				{"pay_type": "Hourly", "rate": 16.00, "hours": 2.0},
			]
		)
		self.assertEqual(result["effective_hourly_rate"], 20.88)

	def test_the_overtime_premium_is_half_the_blended_rate(self):
		"""Forty-five hours of it: five past the threshold at half of $20.88."""
		segments = [
			{"pay_type": "Piece Rate", "rate": 1.50, "hours": 30.0, "piece_units": 450},
			{"pay_type": "Hourly", "rate": 16.00, "hours": 15.0},
		]
		result = calculate_mixed_gross_pay(segments, overtime_hours=5.0)
		# 675 + 240 = 915 straight over 45 hours = $20.3333/hr.
		self.assertEqual(result["straight_time_pay"], 915.00)
		self.assertEqual(result["effective_hourly_rate"], 20.33)
		self.assertEqual(result["overtime_pay"], 50.83)  # 5 × 20.3333 × 0.5
		self.assertEqual(result["gross_pay"], 965.83)

	def test_one_pay_type_through_the_mixed_path_matches_the_single_path(self):
		"""The mixed method is a GENERALISATION, not a second opinion.

		Both branches on one input, hourly and piece rate, with overtime in each.
		If these ever diverge, a worker's pay depends on which code path their
		shift happened to take, which is the bug this asserts cannot exist.
		"""
		hourly_single = calculate_gross_pay("Hourly", 20.0, 45, 5, 0)
		hourly_mixed = calculate_mixed_gross_pay(
			[{"pay_type": "Hourly", "rate": 20.0, "hours": 45.0}],
			overtime_hours=5.0,
		)
		self.assertEqual(hourly_mixed["gross_pay"], hourly_single["gross_pay"])

		piece_single = calculate_gross_pay("Piece Rate", 1.50, 50, 10, 400, 0)
		piece_mixed = calculate_mixed_gross_pay(
			[{"pay_type": "Piece Rate", "rate": 1.50, "hours": 50.0, "piece_units": 400}],
			overtime_hours=10.0,
		)
		self.assertEqual(piece_mixed["gross_pay"], piece_single["gross_pay"])
		self.assertEqual(
			piece_mixed["effective_hourly_rate"],
			piece_single["effective_hourly_rate"],
		)

	def test_a_paid_rest_break_inside_a_piece_segment_is_still_paid(self):
		"""WAC 296-131-020 does not stop applying because the day was mixed."""
		result = calculate_mixed_gross_pay(
			[
				{
					"pay_type": "Piece Rate",
					"rate": 1.50,
					"hours": 8.0,
					"piece_units": 120,
					"break_hours": 0.5,
				},
			]
		)
		self.assertEqual(result["break_pay"], 12.00)
		self.assertEqual(result["gross_pay"], 192.00)

	def test_an_hourly_segment_with_no_rate_earns_nothing_and_is_not_paid_per_bucket(self):
		"""The loud failure. Zero is a number somebody asks about; $1.50 an hour
		for irrigation would look like a decision."""
		result = calculate_mixed_gross_pay(
			[
				{"pay_type": "Piece Rate", "rate": 1.50, "hours": 6.0, "piece_units": 90},
				{"pay_type": "Hourly", "rate": 0.0, "hours": 2.0},
			]
		)
		self.assertEqual(result["hourly_earnings"], 0.0)
		self.assertEqual(result["gross_pay"], 135.00)

	def test_no_segments_at_all_is_zero_and_not_a_division_by_zero(self):
		result = calculate_mixed_gross_pay([], overtime_hours=0.0)
		self.assertEqual(result["gross_pay"], 0.0)
		self.assertEqual(result["effective_hourly_rate"], 0.0)


# ── Claim 5: cross-state payroll ──────────────────────────────────────


class CrossStatePayroll(PayrollTestCase):
	"""A worker with OR and WA shifts in the same period."""

	def test_cross_state_allocation(self):
		"""Hours split between OR and WA allocate gross proportionally."""
		shifts = [
			{"work_state": "OR", "hours": 20, "overtime_hours": 0, "piece_units": 0, "break_hours": 0},
			{"work_state": "WA", "hours": 20, "overtime_hours": 0, "piece_units": 0, "break_hours": 0},
		]
		result = calculate_full_payroll(
			{"employee": "HR-EMP-00001", "employee_name": "Test"},
			shifts,
			{"pay_type": "Hourly", "base_rate": 20.0, "name": "SS-001"},
			{
				"w4_data": self._default_w4_data(),
				"fica_config": self._default_fica(),
				"federal_tax_table": ANNUAL_BRACKETS["Single"],
				"pay_frequency": "Biweekly",
				"ytd_gross": 0,
				"ytd_ss_withheld": 0,
				"state_configs": {"OR": self._or_config(), "WA": self._wa_config()},
				"state_tax_tables": {},
			},
		)
		self.assertEqual(result["gross_pay"], 800.0)
		self.assertIn("OR", result["state_taxes_detail"])
		self.assertIn("WA", result["state_taxes_detail"])
		self.assertGreater(result["state_withholding"], 0)

	def test_single_state_no_split(self):
		"""All shifts in one state — no allocation needed."""
		shifts = [
			{"work_state": "OR", "hours": 40, "overtime_hours": 0, "piece_units": 0, "break_hours": 0},
		]
		result = calculate_full_payroll(
			{"employee": "HR-EMP-00001", "employee_name": "Test"},
			shifts,
			{"pay_type": "Hourly", "base_rate": 20.0, "name": "SS-001"},
			{
				"w4_data": self._default_w4_data(),
				"fica_config": self._default_fica(),
				"federal_tax_table": ANNUAL_BRACKETS["Single"],
				"pay_frequency": "Biweekly",
				"ytd_gross": 0,
				"ytd_ss_withheld": 0,
				"state_configs": {"OR": self._or_config()},
				"state_tax_tables": {},
			},
		)
		self.assertEqual(result["work_state"], "OR")
		self.assertEqual(len(result["state_taxes_detail"]), 1)


# ── Claim 6: full payroll calc ────────────────────────────────────────


class FullPayrollCalc(PayrollTestCase):
	"""The full orchestrator integrates federal + state + FICA."""

	def test_hourly_full_stack(self):
		"""An hourly worker's full payroll has federal, state, and FICA."""
		shifts = [
			{"work_state": "OR", "hours": 40, "overtime_hours": 0, "piece_units": 0, "break_hours": 0},
		]
		result = calculate_full_payroll(
			{"employee": "HR-EMP-00001", "employee_name": "Test Worker"},
			shifts,
			{"pay_type": "Hourly", "base_rate": 25.0, "name": "SS-001"},
			{
				"w4_data": self._default_w4_data(),
				"fica_config": self._default_fica(),
				"federal_tax_table": ANNUAL_BRACKETS["Single"],
				"pay_frequency": "Biweekly",
				"ytd_gross": 0,
				"ytd_ss_withheld": 0,
				"state_configs": {"OR": self._or_config()},
				"state_tax_tables": {},
			},
		)
		self.assertEqual(result["gross_pay"], 1000.0)
		self.assertGreater(result["federal_withholding"], 0)
		self.assertGreater(result["social_security"], 0)
		self.assertGreater(result["medicare"], 0)
		self.assertGreater(result["total_deductions"], 0)
		self.assertGreater(result["net_pay"], 0)
		self.assertLess(result["net_pay"], result["gross_pay"])
		self.assertAlmostEqual(
			result["net_pay"],
			result["gross_pay"] - result["total_deductions"],
			places=2,
		)

	def test_piece_rate_full_stack(self):
		"""A piece-rate worker gets the same tax treatment."""
		shifts = [
			{"work_state": "WA", "hours": 40, "overtime_hours": 0, "piece_units": 500, "break_hours": 0.5},
		]
		result = calculate_full_payroll(
			{"employee": "HR-EMP-00002", "employee_name": "Piece Worker"},
			shifts,
			{"pay_type": "Piece Rate", "base_rate": 2.0, "name": "SS-002"},
			{
				"w4_data": self._default_w4_data(),
				"fica_config": self._default_fica(),
				"federal_tax_table": ANNUAL_BRACKETS["Single"],
				"pay_frequency": "Biweekly",
				"ytd_gross": 0,
				"ytd_ss_withheld": 0,
				"state_configs": {"WA": self._wa_config()},
				"state_tax_tables": {},
			},
		)
		self.assertGreater(result["gross_pay"], 1000.0)
		self.assertEqual(result["pay_type"], "Piece Rate")
		self.assertTrue(result["minimum_wage_check"])
		self.assertGreater(result["net_pay"], 0)


# ── Claim 7: salary structure CRUD tools ──────────────────────────────


class SalaryStructureTools(PayrollTestCase):
	"""The CRUD tools for salary structures work."""

	def test_create_and_get(self):
		"""Create a salary structure and retrieve it."""
		data = self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-06-01",
			},
		)
		self.assertEqual(data["pay_type"], "Hourly")
		self.assertEqual(data["base_rate"], 20.0)

		got = self.tool_data("get_salary_structure", {"employee": "HR-EMP-00001"})
		self.assertEqual(got["pay_type"], "Hourly")

	def test_list_structures(self):
		"""List returns created structures."""
		self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-06-01",
			},
		)
		data = self.tool_data("list_salary_structures", {"company": MAIN})
		self.assertGreaterEqual(data["count"], 1)

	def test_deactivate(self):
		"""Deactivation sets is_active to 0."""
		created = self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-06-01",
			},
		)
		data = self.tool_data("deactivate_salary_structure", {"name": created["name"]})
		self.assertEqual(data["is_active"], 0)

		error = self.tool_error("get_salary_structure", {"employee": "HR-EMP-00001"})
		self.assertIn("no active", error)

	def test_invalid_pay_type(self):
		"""Rejects invalid pay type."""
		error = self.tool_error(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Commission",
				"base_rate": 20.0,
			},
		)
		self.assertIn("Piece Rate", error)

	def test_zero_base_rate(self):
		"""Rejects zero base rate."""
		error = self.tool_error(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 0,
			},
		)
		self.assertIn("positive", error)


# ── Claim 8: payroll entry tools ──────────────────────────────────────


class PayrollEntryTools(PayrollTestCase):
	"""The calculate/submit/get/list tools work."""

	def _setup_for_payroll(self):
		"""Seed structures and W-4s for payroll calculation."""
		self._submit_w4("HR-EMP-00001")
		self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-01-01",
			},
		)

	def test_calculate_payroll(self):
		"""Calculate creates a payroll entry with slips."""
		self._setup_for_payroll()
		data = self.tool_data(
			"calculate_payroll",
			{
				"company": MAIN,
				"pay_period_start": "2025-06-01",
				"pay_period_end": "2025-06-14",
				"pay_frequency": "Biweekly",
			},
		)
		self.assertEqual(data["status"], "Calculated")
		self.assertGreaterEqual(data["employee_count"], 1)

	def test_submit_payroll(self):
		"""Submit moves from Calculated to Submitted."""
		self._setup_for_payroll()
		calc = self.tool_data(
			"calculate_payroll",
			{
				"company": MAIN,
				"pay_period_start": "2025-06-01",
				"pay_period_end": "2025-06-14",
				"pay_frequency": "Biweekly",
			},
		)
		data = self.tool_data("submit_payroll", {"name": calc["name"]})
		self.assertEqual(data["status"], "Submitted")

	def test_submit_draft_fails(self):
		"""Cannot submit a Draft entry (only Calculated)."""
		# Create a raw Draft entry directly
		STORE.seed(
			"Farm Payroll Entry",
			[
				{
					"name": "PAY-2025-0099",
					"company": MAIN,
					"pay_period_start": "2025-06-01",
					"pay_period_end": "2025-06-14",
					"pay_frequency": "Biweekly",
					"status": "Draft",
					"total_gross": 0,
					"total_deductions": 0,
					"total_net": 0,
					"employee_count": 0,
				}
			],
		)
		error = self.tool_error("submit_payroll", {"name": "PAY-2025-0099"})
		self.assertIn("Draft", error)

	def test_get_payroll_entry(self):
		"""Get returns entry with slips."""
		self._setup_for_payroll()
		calc = self.tool_data(
			"calculate_payroll",
			{
				"company": MAIN,
				"pay_period_start": "2025-07-01",
				"pay_period_end": "2025-07-14",
				"pay_frequency": "Biweekly",
			},
		)
		data = self.tool_data("get_payroll_entry", {"name": calc["name"]})
		self.assertEqual(data["name"], calc["name"])
		self.assertIsInstance(data["slips"], list)

	def test_list_payroll_entries(self):
		"""List returns created entries."""
		self._setup_for_payroll()
		self.tool_data(
			"calculate_payroll",
			{
				"company": MAIN,
				"pay_period_start": "2025-08-01",
				"pay_period_end": "2025-08-14",
				"pay_frequency": "Biweekly",
			},
		)
		data = self.tool_data("list_payroll_entries", {"company": MAIN})
		self.assertGreaterEqual(data["count"], 1)


# ── Claim 9: preview payroll ──────────────────────────────────────────


class PreviewPayroll(PayrollTestCase):
	"""The dry-run preview returns results without creating records."""

	def test_preview_returns_calc(self):
		"""Preview returns gross, deductions, and net."""
		self._submit_w4("HR-EMP-00001")
		self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 25.0,
				"effective_from": "2025-01-01",
			},
		)

		data = self.tool_data(
			"preview_payroll",
			{
				"employee": "HR-EMP-00001",
				"pay_period_start": "2025-06-01",
				"pay_period_end": "2025-06-14",
				"pay_frequency": "Biweekly",
			},
		)
		self.assertIn("gross_pay", data)
		self.assertIn("net_pay", data)
		self.assertIn("federal_withholding", data)

	def test_preview_no_structure_fails(self):
		"""Preview fails if no salary structure exists."""
		error = self.tool_error(
			"preview_payroll",
			{
				"employee": "HR-EMP-00002",
				"pay_period_start": "2025-06-01",
				"pay_period_end": "2025-06-14",
			},
		)
		self.assertIn("no active salary structure", error)


# ── Claim 10: edge cases ─────────────────────────────────────────────


class EdgeCases(PayrollTestCase):
	"""Zero hours, no shifts, salary pay type, deactivation."""

	def test_salary_pay_type(self):
		"""Salary pay type uses base_rate as the periodic amount."""
		result = calculate_gross_pay("Salary", 5000.0, 80, 0, 0)
		self.assertEqual(result["gross_pay"], 5000.0)
		self.assertEqual(result["pay_type"], "Salary")
		self.assertEqual(result["overtime_hours"], 0.0)

	def test_zero_hours_hourly(self):
		"""Zero hours means zero gross for hourly."""
		result = calculate_gross_pay("Hourly", 20.0, 0, 0, 0)
		self.assertEqual(result["gross_pay"], 0.0)

	def test_no_shifts_full_payroll(self):
		"""Full payroll with no shifts still computes from salary structure."""
		result = calculate_full_payroll(
			{"employee": "HR-EMP-00001", "employee_name": "Test"},
			[],
			{"pay_type": "Hourly", "base_rate": 20.0, "name": "SS-001"},
			{
				"w4_data": self._default_w4_data(),
				"fica_config": self._default_fica(),
				"federal_tax_table": ANNUAL_BRACKETS["Single"],
				"pay_frequency": "Biweekly",
				"ytd_gross": 0,
				"ytd_ss_withheld": 0,
				"state_configs": {},
				"state_tax_tables": {},
			},
		)
		self.assertEqual(result["gross_pay"], 0.0)
		self.assertEqual(result["total_hours"], 0.0)
		self.assertEqual(result["net_pay"], 0.0)

	def test_overtime_calc_zero_hours(self):
		"""Zero OT hours means zero OT pay."""
		ot = calculate_overtime(0, 0, 20.0)
		self.assertEqual(ot, 0.0)

	def test_deactivate_by_employee(self):
		"""Deactivate by employee name instead of docname."""
		self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-01-01",
			},
		)
		data = self.tool_data(
			"deactivate_salary_structure",
			{
				"employee": "HR-EMP-00001",
			},
		)
		self.assertEqual(data["is_active"], 0)

	def test_no_w4_uses_defaults(self):
		"""Preview payroll still works when employee has no W-4 — uses defaults."""
		self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00002",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-01-01",
			},
		)
		data = self.tool_data(
			"preview_payroll",
			{
				"employee": "HR-EMP-00002",
				"pay_period_start": "2025-06-01",
				"pay_period_end": "2025-06-14",
				"pay_frequency": "Biweekly",
			},
		)
		self.assertIn("gross_pay", data)

	def test_nonexistent_payroll_entry(self):
		"""Getting a nonexistent entry fails cleanly."""
		error = self.tool_error("get_payroll_entry", {"name": "PAY-NOPE-0001"})
		self.assertIn("no Farm Payroll Entry", error)

	def test_missing_pay_frequency(self):
		"""Invalid pay frequency is rejected."""
		self._submit_w4("HR-EMP-00001")
		self.tool_data(
			"create_salary_structure",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-01-01",
			},
		)
		error = self.tool_error(
			"calculate_payroll",
			{
				"company": MAIN,
				"pay_period_start": "2025-06-01",
				"pay_period_end": "2025-06-14",
				"pay_frequency": "Quarterly",
			},
		)
		self.assertIn("pay_frequency", error)
