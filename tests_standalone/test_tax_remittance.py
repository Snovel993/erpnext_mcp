# SPDX-License-Identifier: MIT
"""Tax remittance reporting — v0.92.0.

TWELVE CLAIMS.

 1. `FederalHolidays` — the eleven, observed off weekends, and 31 December.
 2. `MonthlyDeposits` — the 15th, pushed off a weekend and off a holiday.
 3. `SemiweeklyDeposits` — both settlement days and the three-banking-day rule.
 4. `DepositFrequency` — the lookback threshold, and what an unknown one assumes.
 5. `FutaWageBase` — the $7,000 cap consumed per employee in DATE order.
 6. `Form940` — lines 3 to 17, the deposit carry, and the two farm tests.
 7. `OregonForm132` — whole hours rounded down, and the UI cap from 1 January.
 8. `MonthlyLiability` — Part 2 totals line 12, residual on a month with pay.
 9. `RemittanceSummaryTool` — federal, Oregon and Washington, by pay period.
10. `PrefillAndStateTools` — the 941 prefill, the OQ and the 132, end to end.
11. `DepositScheduleTool` — the payday, where it comes from and when it is a guess.
12. `RemittanceRefusals` — the switches, the HR gate, and every argument check.
"""
import json
from datetime import date

from erpnext_mcp.tax_remittance_calc import (
	AG_FUTA_CASH_WAGE_TEST,
	FUTA_DEPOSIT_THRESHOLD,
	LOOKBACK_THRESHOLD,
	deposit_frequency,
	federal_holidays,
	futa_taxable_by_quarter,
	generate_940_data,
	generate_or_132_data,
	is_business_day,
	lookback_period,
	monthly_due_date,
	monthly_liability,
	next_business_day,
	quarterly_return_due,
	semiweekly_due_date,
)

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import ROLES, STORE, set_roles

REMITTANCE_TOOLS = (
	"get_tax_remittance_summary",
	"get_941_prefill",
	"get_state_tax_remittance",
	"get_tax_deposit_schedule",
	"get_futa_summary",
)

REMITTANCE_TOOLS_ON = {f"allow_{name}": 1 for name in REMITTANCE_TOOLS}

#: What the Oregon engine leaves on a slip, at round numbers so a total can be
#: read by eye. Mirrors `test_tax_forms.OR_DETAIL`.
OR_DETAIL = {
	"or_income_tax": 60.0,
	"or_transit_tax": 1.0,
	"or_paid_leave_employee": 6.0,
	"or_paid_leave_employer": 4.0,
	"or_workers_comp": 15.0,
	"total_or_employee": 67.0,
	"total_or_employer": 19.0,
}

WA_DETAIL = {
	"wa_pfml_employee": 6.69,
	"wa_pfml_employer": 2.51,
	"wa_cares_employee": 5.80,
	"wa_li_employee": 0.50,
	"wa_li_employer": 1.50,
	"total_wa_employee": 12.99,
	"total_wa_employer": 4.01,
}


def slip(employee="HR-EMP-00001", gross=1000.0, state="OR", hours=80.0, **overrides):
	"""One slip with both halves of FICA on it, at the app's own rates."""
	social_security = round(gross * 0.062, 2)
	medicare = round(gross * 0.0145, 2)
	row = {
		"employee": employee,
		"employee_name": f"Worker {employee[-1]}",
		"work_state": state,
		"total_hours": hours,
		"gross_pay": gross,
		"federal_withholding": round(gross * 0.10, 2),
		"state_withholding": OR_DETAIL["or_income_tax"] if state == "OR" else 0.0,
		"social_security": social_security,
		"medicare": medicare,
		"additional_medicare": 0.0,
		"social_security_employer": social_security,
		"medicare_employer": medicare,
		"futa": round(min(gross, 7000.0) * 0.006, 2),
		"state_unemployment": round(gross * 0.02, 2),
		"state_employer_other": 0.0,
		"total_employer_taxes": 0.0,
		"state_taxes_detail": {state: dict(OR_DETAIL if state == "OR" else WA_DETAIL)},
	}
	row.update(overrides)
	return row


# ── Claim 1: the holiday table ────────────────────────────────────────────


class FederalHolidays(V12TestCase):
	"""The eleven federal holidays, as observed rather than as legislated."""

	def test_all_eleven_are_present(self):
		self.assertEqual(len(federal_holidays(2026)), 11)

	def test_the_fixed_ones_land_on_their_dates(self):
		table = federal_holidays(2025)
		self.assertEqual(table["2025-06-19"], "Juneteenth National Independence Day")
		self.assertEqual(table["2025-07-04"], "Independence Day")
		self.assertEqual(table["2025-12-25"], "Christmas Day")

	def test_the_floating_ones_are_computed(self):
		table = federal_holidays(2025)
		self.assertIn("2025-01-20", table)  # 3rd Monday of January
		self.assertIn("2025-05-26", table)  # last Monday of May
		self.assertIn("2025-11-27", table)  # 4th Thursday of November

	def test_a_saturday_holiday_is_observed_on_the_friday(self):
		"""4 July 2026 is a Saturday, so the federal holiday is Friday the 3rd."""
		table = federal_holidays(2026)
		self.assertIn("2026-07-03", table)
		self.assertNotIn("2026-07-04", table)

	def test_a_sunday_holiday_is_observed_on_the_monday(self):
		"""19 June 2027 is a Saturday; 4 July 2027 is a Sunday → Monday the 5th."""
		table = federal_holidays(2027)
		self.assertIn("2027-07-05", table)

	def test_new_years_day_can_be_observed_in_the_previous_year(self):
		"""1 January 2022 was a Saturday, so 31 December 2021 was the holiday.

		The one federal holiday that falls outside its own calendar year, and the
		one a table built strictly per-year misses — which would silently treat
		31 December as a banking day and shorten a late-December deadline.
		"""
		self.assertIn("2021-12-31", federal_holidays(2021))
		self.assertFalse(is_business_day(date(2021, 12, 31)))

	def test_the_previous_year_case_does_not_fire_when_it_should_not(self):
		"""The negative control: 1 January 2027 is a Friday, so nothing moves."""
		self.assertNotIn("2026-12-31", federal_holidays(2026))
		self.assertTrue(is_business_day(date(2026, 12, 31)))

	def test_a_weekend_is_never_a_banking_day(self):
		self.assertFalse(is_business_day(date(2025, 3, 1)))  # Saturday
		self.assertFalse(is_business_day(date(2025, 3, 2)))  # Sunday
		self.assertTrue(is_business_day(date(2025, 3, 3)))  # Monday

	def test_next_business_day_crosses_a_year_boundary(self):
		"""From 31 December 2021 the next banking day is 3 January 2022.

		The 31st is the observed holiday, the 1st is a Saturday and the 2nd a
		Sunday, so the search has to carry a table for a year it did not start in.
		"""
		self.assertEqual(next_business_day(date(2021, 12, 31)), date(2022, 1, 3))


# ── Claim 2: monthly deposits ─────────────────────────────────────────────


class MonthlyDeposits(V12TestCase):
	"""The 15th of the following month, moved for weekends and holidays."""

	def test_an_ordinary_month_is_the_fifteenth(self):
		self.assertEqual(monthly_due_date(2025, 11), date(2025, 12, 15))

	def test_december_settles_in_january(self):
		self.assertEqual(monthly_due_date(2025, 12), date(2026, 1, 15))

	def test_a_weekend_fifteenth_moves_past_the_holiday_behind_it(self):
		"""15 February 2025 is a Saturday and the Monday is Washington's Birthday.

		Two shifts in a row, which is the case a "next weekday" implementation
		gets wrong by one day and nothing complains about.
		"""
		self.assertEqual(monthly_due_date(2025, 1), date(2025, 2, 18))

	def test_a_quarterly_return_is_the_last_day_of_the_next_month(self):
		self.assertEqual(quarterly_return_due("Q1", 2025), date(2025, 4, 30))
		self.assertEqual(quarterly_return_due("Q3", 2025), date(2025, 10, 31))

	def test_the_fourth_quarter_is_due_in_the_following_year(self):
		"""31 January 2026 is a Saturday, so the deadline is Monday 2 February."""
		self.assertEqual(quarterly_return_due("Q4", 2025), date(2026, 2, 2))


# ── Claim 3: semiweekly deposits ──────────────────────────────────────────


class SemiweeklyDeposits(V12TestCase):
	"""Two settlement days, and the extra banking day a holiday buys."""

	def test_a_friday_payday_settles_the_following_wednesday(self):
		result = semiweekly_due_date(date(2025, 11, 21))
		self.assertEqual(result["due_date"], "2025-11-26")
		self.assertEqual(result["semiweekly_period_start"], "2025-11-19")
		self.assertEqual(result["semiweekly_period_end"], "2025-11-21")

	def test_a_wednesday_payday_is_in_the_same_period_as_the_friday(self):
		result = semiweekly_due_date(date(2025, 11, 19))
		self.assertEqual(result["semiweekly_period_end"], "2025-11-21")
		self.assertEqual(result["due_date"], "2025-11-26")

	def test_a_tuesday_payday_settles_the_following_friday(self):
		result = semiweekly_due_date(date(2025, 9, 9))
		self.assertEqual(result["semiweekly_period_start"], "2025-09-06")
		self.assertEqual(result["semiweekly_period_end"], "2025-09-09")
		self.assertEqual(result["due_date"], "2025-09-12")

	def test_a_saturday_payday_is_in_the_same_period_as_the_tuesday(self):
		result = semiweekly_due_date(date(2025, 9, 6))
		self.assertEqual(result["semiweekly_period_end"], "2025-09-09")
		self.assertEqual(result["due_date"], "2025-09-12")

	def test_a_holiday_in_the_window_buys_one_more_banking_day(self):
		"""Payday Tuesday 25 November 2025: Thanksgiving falls in the window.

		The deadline is the following Friday, 28 November. Thursday the 27th is
		one of the three weekdays after the period closed, so the depositor gets
		an extra banking day and the deadline becomes Monday 1 December. This is
		the three-banking-day rule and it is the one most implementations skip.
		"""
		result = semiweekly_due_date(date(2025, 11, 25))
		self.assertEqual(result["holiday_extension_days"], 1)
		self.assertEqual(result["due_date"], "2025-12-01")

	def test_a_week_with_no_holiday_gets_no_extension(self):
		"""The negative control for the rule above."""
		result = semiweekly_due_date(date(2025, 9, 9))
		self.assertEqual(result["holiday_extension_days"], 0)

	def test_memorial_day_extends_a_friday_payday(self):
		"""Friday 22 May 2026 → Wednesday 27 May, but Monday is Memorial Day."""
		result = semiweekly_due_date(date(2026, 5, 22))
		self.assertEqual(result["holiday_extension_days"], 1)
		self.assertEqual(result["due_date"], "2026-05-28")


# ── Claim 4: which schedule an employer is on ─────────────────────────────


class DepositFrequency(V12TestCase):
	"""The lookback test, its boundary, and what an unknown total assumes."""

	def test_above_the_threshold_is_semiweekly(self):
		self.assertEqual(deposit_frequency(50000.01)["schedule"], "Semiweekly")

	def test_exactly_the_threshold_is_monthly(self):
		"""The rule is MORE than $50,000, so the boundary itself is monthly."""
		self.assertEqual(deposit_frequency(LOOKBACK_THRESHOLD)["schedule"], "Monthly")

	def test_below_the_threshold_is_monthly(self):
		self.assertEqual(deposit_frequency(12000.0)["schedule"], "Monthly")

	def test_an_unknown_total_assumes_the_new_employer_default(self):
		result = deposit_frequency(None)
		self.assertEqual(result["schedule"], "Monthly")
		self.assertTrue(result["assumed"])
		self.assertIn("semiweekly", result["basis"])

	def test_a_known_total_is_not_flagged_as_assumed(self):
		self.assertFalse(deposit_frequency(1000.0)["assumed"])

	def test_the_lookback_window_ends_in_the_middle_of_the_prior_year(self):
		"""The trap in the name: 2026's schedule is decided by mid-2025 data."""
		window = lookback_period(2026)
		self.assertEqual(window["start"], "2024-07-01")
		self.assertEqual(window["end"], "2025-06-30")
		self.assertEqual(window["quarters"][0], "Q3 2024")


# ── Claim 5: the FUTA wage base ───────────────────────────────────────────


class FutaWageBase(V12TestCase):
	"""$7,000 per employee per year, consumed in the order it was earned."""

	def _quarterly(self, gross_per_quarter, employees=("E1",)):
		slips = [
			{"employee": employee, "gross_pay": gross_per_quarter,
			 "period_start": f"2025-{month:02d}-01", "period_end": f"2025-{month:02d}-28"}
			for employee in employees
			for month in (2, 5, 8, 11)
		]
		return futa_taxable_by_quarter(slips, 7000.0)

	def test_the_cap_is_consumed_in_date_order_not_split_evenly(self):
		"""$3,000 a quarter reaches $7,000 in Q3, so Q4 is taxable on nothing.

		An implementation that annualises and divides by four reports 1,750 in
		every quarter, which is wrong in all four and wrong on Part 5 of the 940.
		"""
		taxable, excess, capped = self._quarterly(3000.0)
		self.assertEqual(taxable, {"Q1": 3000.0, "Q2": 3000.0, "Q3": 1000.0, "Q4": 0.0})
		self.assertEqual(excess, {"Q1": 0.0, "Q2": 0.0, "Q3": 2000.0, "Q4": 3000.0})
		self.assertEqual(capped, {"E1"})

	def test_an_employee_under_the_cap_is_taxable_all_year(self):
		taxable, excess, capped = self._quarterly(1000.0)
		self.assertEqual(taxable, {"Q1": 1000.0, "Q2": 1000.0, "Q3": 1000.0, "Q4": 1000.0})
		self.assertEqual(sum(excess.values()), 0.0)
		self.assertEqual(capped, set())

	def test_the_cap_is_per_employee_not_per_company(self):
		"""Two employees at $3,000 a quarter consume two separate bases."""
		taxable, _excess, capped = self._quarterly(3000.0, employees=("E1", "E2"))
		self.assertEqual(taxable["Q1"], 6000.0)
		self.assertEqual(sum(taxable.values()), 14000.0)
		self.assertEqual(capped, {"E1", "E2"})

	def test_prior_wages_consume_the_base_before_the_first_slip(self):
		slips = [{"employee": "E1", "gross_pay": 3000.0,
		          "period_start": "2025-08-01", "period_end": "2025-08-31"}]
		taxable, excess, _capped = futa_taxable_by_quarter(slips, 7000.0, {"E1": 6000.0})
		self.assertEqual(taxable["Q3"], 1000.0)
		self.assertEqual(excess["Q3"], 2000.0)


# ── Claim 6: Form 940 ─────────────────────────────────────────────────────


class Form940(V12TestCase):
	"""The annual FUTA return, and the two tests that decide it applies."""

	def _year(self, gross=3000.0, employees=("E1",), **info):
		slips = [
			{"employee": employee, "gross_pay": gross,
			 "period_start": f"2025-{month:02d}-01", "period_end": f"2025-{month:02d}-28"}
			for employee in employees
			for month in (2, 5, 8, 11)
		]
		return generate_940_data(slips, {"name": MAIN, "ein": "93-1234567", **info}, 2025)

	def test_the_lines_add_up(self):
		form = self._year()
		self.assertEqual(form["line3_total_payments"], 12000.0)
		self.assertEqual(form["line5_payments_over_wage_base"], 5000.0)
		self.assertEqual(form["line7_total_taxable_futa_wages"], 7000.0)
		self.assertEqual(form["line8_futa_tax_before_adjustments"], 42.0)
		self.assertEqual(form["line12_total_futa_tax"], 42.0)

	def test_the_effective_rate_is_six_tenths_of_a_percent(self):
		"""6.0% gross less the 5.4% state credit."""
		self.assertEqual(self._year()["effective_rate"], 0.6)

	def test_part_five_totals_the_annual_tax(self):
		form = self._year()
		self.assertEqual(form["line16_quarterly_liabilities"],
		                 {"Q1": 18.0, "Q2": 18.0, "Q3": 6.0, "Q4": 0.0})
		self.assertEqual(form["line17_total_liability"], form["line12_total_futa_tax"])

	def test_undeposited_futa_carries_between_quarters(self):
		"""Under $500 nothing is deposited and the liability rolls forward."""
		plan = self._year()["quarterly_deposits"]
		self.assertEqual([row["accumulated"] for row in plan], [18.0, 36.0, 42.0, 42.0])
		self.assertFalse(any(row["deposit_required"] for row in plan))

	def test_a_large_enough_liability_is_deposited_and_resets_the_carry(self):
		form = self._year(gross=7000.0, employees=tuple(f"E{n}" for n in range(1, 40)))
		plan = form["quarterly_deposits"]
		self.assertTrue(plan[0]["deposit_required"])
		self.assertGreater(plan[0]["deposit_amount"], FUTA_DEPOSIT_THRESHOLD)
		self.assertEqual(plan[1]["carried_in"], 0.0)

	def test_a_balance_is_due_when_nothing_was_deposited(self):
		form = self._year()
		self.assertEqual(form["line14_balance_due"], 42.0)
		self.assertEqual(form["line15_overpayment"], 0.0)

	def test_an_overpayment_is_reported_separately(self):
		form = self._year(deposits=100.0)
		self.assertEqual(form["line14_balance_due"], 0.0)
		self.assertEqual(form["line15_overpayment"], 58.0)

	def test_a_small_farm_meets_neither_coverage_test(self):
		coverage = self._year()["agricultural_coverage"]
		self.assertFalse(coverage["cash_wage_test_met"])
		self.assertFalse(coverage["weeks_test_met"])
		self.assertFalse(coverage["liable"])

	def test_a_quarter_over_twenty_thousand_meets_the_cash_wage_test(self):
		coverage = self._year(gross=21000.0)["agricultural_coverage"]
		self.assertTrue(coverage["cash_wage_test_met"])
		self.assertTrue(coverage["liable"])
		self.assertGreaterEqual(coverage["highest_quarter_cash_wages"], AG_FUTA_CASH_WAGE_TEST)

	def test_neither_test_being_met_is_said_in_words(self):
		"""An employer under both owes nothing at all — not a reduced amount."""
		warnings = " ".join(self._year()["warnings"])
		self.assertIn("NEITHER AGRICULTURAL FUTA TEST IS MET", warnings)
		self.assertIn("files no Form 940", warnings)

	def test_the_weeks_test_says_it_is_derived_rather_than_measured(self):
		coverage = self._year()["agricultural_coverage"]
		self.assertIn("DERIVED", coverage["weeks_test_source"])
		self.assertIn("EXACT", coverage["cash_wage_test_source"])

	def test_ten_workers_across_the_year_meets_the_weeks_test(self):
		"""Twelve monthly periods with ten workers is well past twenty weeks."""
		slips = [
			{"employee": f"E{n}", "gross_pay": 500.0,
			 "period_start": f"2025-{month:02d}-01", "period_end": f"2025-{month:02d}-28"}
			for n in range(1, 11)
			for month in range(1, 13)
		]
		coverage = generate_940_data(slips, {"name": MAIN}, 2025)["agricultural_coverage"]
		self.assertGreaterEqual(coverage["weeks_with_ten_or_more"], 20)
		self.assertTrue(coverage["weeks_test_met"])

	def test_an_empty_year_is_all_zeroes_and_says_so(self):
		form = generate_940_data([], {"name": MAIN}, 2025)
		self.assertEqual(form["line12_total_futa_tax"], 0.0)
		self.assertIn("no payroll slips found", " ".join(form["warnings"]))


# ── Claim 7: Oregon Form 132 ──────────────────────────────────────────────


class OregonForm132(V12TestCase):
	"""The per-employee detail an OQ is not a filing without."""

	def _detail(self, slips, **info):
		return generate_or_132_data(slips, {"name": MAIN, **info}, "Q1", 2025)

	def test_hours_are_rounded_down_to_whole_hours(self):
		"""Oregon's instruction. Rounding to nearest reports an unworked hour."""
		rows = self._detail([slip(hours=39.9, gross=1000.0)])["employees"]
		self.assertEqual(rows[0]["hours_worked"], 39)

	def test_a_row_per_employee_not_per_slip(self):
		slips = [slip(gross=1000.0), slip(gross=1000.0), slip(employee="HR-EMP-00002")]
		detail = self._detail(slips)
		self.assertEqual(detail["employee_count"], 2)
		self.assertEqual(detail["total_wages"], 3000.0)

	def test_the_ui_cap_is_consumed_from_january_not_from_the_quarter(self):
		"""An employee who reached the base in spring has no Q3 subject wages.

		Applying the cap to the quarter alone is the bug that overstates a later
		quarter for a crew that worked the spring.
		"""
		detail = self._detail(
			[slip(gross=10000.0)],
			or_ui_wage_base=54300.0,
			ytd_wages_by_employee={"HR-EMP-00001": 50000.0},
		)
		row = detail["employees"][0]
		self.assertEqual(row["ui_subject_wages"], 4300.0)
		self.assertEqual(row["excess_wages"], 5700.0)

	def test_without_prior_wages_it_says_the_figure_is_a_ceiling(self):
		detail = self._detail([slip(gross=1000.0)], or_ui_wage_base=54300.0)
		self.assertIn("OVERSTATES subject wages", " ".join(detail["warnings"]))

	def test_a_missing_ssn_is_named_because_oregon_matches_on_it(self):
		detail = self._detail([slip(gross=1000.0)], or_ui_wage_base=54300.0)
		self.assertIn("no SSN recorded", " ".join(detail["warnings"]))

	def test_an_ssn_that_is_present_raises_no_warning(self):
		detail = self._detail(
			[slip(gross=1000.0)],
			or_ui_wage_base=54300.0,
			ssn_last4_by_employee={"HR-EMP-00001": "6789"},
		)
		self.assertNotIn("no SSN recorded", " ".join(detail["warnings"]))
		self.assertEqual(detail["employees"][0]["ssn_last4"], "6789")


# ── Claim 8: Form 941 Part 2 ──────────────────────────────────────────────


class MonthlyLiability(V12TestCase):
	"""Part 2 has to total line 12 to the cent or the return is rejected."""

	def test_liability_lands_in_the_month_the_period_ended(self):
		result = monthly_liability([slip(gross=1000.0, period_end="2025-02-14")], "Q1", 2025)
		self.assertEqual(result["as_withheld"]["February"], 253.0)
		self.assertEqual(result["as_withheld"]["January"], 0.0)

	def test_the_residual_is_applied_so_the_total_matches_line_twelve(self):
		rows = [slip(gross=1000.0, period_end="2025-02-14")]
		result = monthly_liability(rows, "Q1", 2025, total_tax=253.5)
		self.assertTrue(result["reconciles"])
		self.assertEqual(result["reconciled_total"], 253.5)
		self.assertEqual(result["residual_applied_to_last_month"], 0.5)

	def test_the_residual_goes_to_the_last_month_that_had_pay(self):
		"""Not simply the last month of the quarter.

		A quarter whose payroll stopped in February would otherwise report a
		March liability in a month nobody was paid — a figure an agency can ask
		about and the employer cannot explain.
		"""
		rows = [slip(gross=1000.0, period_end="2025-02-14")]
		result = monthly_liability(rows, "Q1", 2025, total_tax=253.5)
		self.assertEqual(result["residual_month"], "February")
		self.assertEqual(result["reconciled"]["March"], 0.0)

	def test_without_a_total_nothing_is_moved(self):
		rows = [slip(gross=1000.0, period_end="2025-02-14")]
		result = monthly_liability(rows, "Q1", 2025)
		self.assertEqual(result["as_withheld"], result["reconciled"])
		self.assertTrue(result["reconciles"])


# ── The tool-level base ───────────────────────────────────────────────────


class RemittanceToolTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **REMITTANCE_TOOLS_ON)
		install_hrms()
		# `ROLES` is module state on the harness and `set_roles` does not undo
		# itself, so a test that drops Administrator to Accounts User to prove
		# the HR gate would otherwise leave every later test unable to read
		# anything. Restored here rather than in each test that moves it.
		original = list(ROLES.get("Administrator") or [])
		self.addCleanup(set_roles, "Administrator", original)
		STORE.seed("Employee", [
			{"name": "HR-EMP-00001", "employee_name": "Test Worker", "company": MAIN,
			 "status": "Active", "date_of_joining": "2025-01-15"},
			{"name": "HR-EMP-00002", "employee_name": "Second Worker", "company": MAIN,
			 "status": "Active", "date_of_joining": "2025-01-15"},
		])
		STORE.seed("State Tax Configuration", [
			{"name": "STC-OR-2025", "company": MAIN, "state": "OR", "tax_year": 2025,
			 "status": "Active", "employer_account_number": "1234567-8"},
			{"name": "STC-WA-2025", "company": MAIN, "state": "WA", "tax_year": 2025,
			 "status": "Active", "employer_account_number": "000123456"},
		])

	def seed_payroll(self, name, start, end, slips, status="Submitted", company=MAIN, postings=None):
		rows = []
		for row in slips:
			row = dict(row)
			row["state_taxes_detail"] = json.dumps(row.get("state_taxes_detail") or {})
			rows.append(row)
		entry = {
			"name": name,
			"company": company,
			"pay_period_start": start,
			"pay_period_end": end,
			"pay_frequency": "Biweekly",
			"status": status,
			"total_gross": sum(s.get("gross_pay", 0) for s in slips),
			"total_deductions": 0,
			"total_net": 0,
			"employee_count": len({s.get("employee") for s in slips}),
			"slips": rows,
		}
		if postings:
			entry["gl_postings"] = [
				{"journal_entry": f"JE-{name}", "posting_date": d, "total_debit": 0}
				for d in postings
			]
		STORE.seed("Farm Payroll Entry", [entry])

	def seed_a_quarter(self, company=MAIN):
		"""Two Oregon pay periods inside Q1 2025."""
		self.seed_payroll("PAY-0001", "2025-02-01", "2025-02-14", [slip(gross=1000.0)], company=company)
		self.seed_payroll("PAY-0002", "2025-02-15", "2025-02-28", [slip(gross=1000.0)], company=company)


# ── Claim 9: the summary tool ─────────────────────────────────────────────


class RemittanceSummaryTool(RemittanceToolTestCase):
	"""Every authority for a period, and the pay periods it came from."""

	def test_the_federal_deposit_is_both_halves_of_fica(self):
		"""$1,000 gross: $100 withheld, $62 + $62 SS, $14.50 + $14.50 Medicare."""
		self.seed_payroll("PAY-0001", "2025-02-01", "2025-02-14", [slip(gross=1000.0)])
		data = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["federal"]["deposit_liability"], 253.0)
		self.assertEqual(data["federal"]["social_security_employer"], 62.0)

	def test_each_pay_period_is_reported_on_its_own_row(self):
		self.seed_a_quarter()
		data = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["payroll_entry_count"], 2)
		self.assertEqual([row["federal_deposit"] for row in data["by_period"]], [253.0, 253.0])
		self.assertEqual(data["by_period"][0]["quarter"], "Q1")

	def test_the_oregon_components_are_totalled_by_programme(self):
		self.seed_payroll("PAY-0001", "2025-02-01", "2025-02-14", [slip(gross=1000.0)])
		components = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["oregon"]["components"]
		self.assertEqual(components["or_income_tax"]["amount"], 60.0)
		self.assertEqual(components["or_transit_tax"]["amount"], 1.0)
		self.assertEqual(components["state_unemployment"]["amount"], 20.0)

	def test_washington_is_totalled_separately(self):
		self.seed_payroll("PAY-WA", "2025-02-01", "2025-02-14", [slip(gross=1000.0, state="WA")])
		data = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["washington"]["components"]["wa_cares_employee"]["amount"], 5.80)
		self.assertEqual(data["oregon"]["total"], 0.0)

	def test_a_draft_payroll_is_not_counted(self):
		self.seed_payroll("PAY-DRAFT", "2025-02-01", "2025-02-14", [slip(gross=9000.0)], status="Draft")
		data = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["gross_pay"], 0.0)
		self.assertIn("no Calculated or Submitted payroll", " ".join(data["warnings"]))

	def test_another_companys_payroll_is_not_counted(self):
		self.seed_payroll("PAY-OTHER", "2025-02-01", "2025-02-14",
		                  [slip(gross=5000.0)], company=OTHER)
		self.seed_payroll("PAY-MAIN", "2025-02-15", "2025-02-28", [slip(gross=1000.0)])
		data = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["gross_pay"], 1000.0)

	def test_a_quarter_narrows_the_window(self):
		self.seed_payroll("PAY-Q1", "2025-02-01", "2025-02-14", [slip(gross=1000.0)])
		self.seed_payroll("PAY-Q2", "2025-05-01", "2025-05-14", [slip(gross=4000.0)])
		q1 = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		year = self.tool_data("get_tax_remittance_summary", {"company": MAIN, "fiscal_year": "2025"})
		self.assertEqual(q1["gross_pay"], 1000.0)
		self.assertEqual(year["gross_pay"], 5000.0)

	def test_the_grand_total_adds_the_three_jurisdictions(self):
		self.seed_payroll("PAY-0001", "2025-02-01", "2025-02-14", [slip(gross=1000.0)])
		data = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		expected = round(
			data["federal"]["deposit_liability"] + data["federal"]["futa"]
			+ data["oregon"]["total"] + data["washington"]["total"], 2,
		)
		self.assertEqual(data["grand_total_remittance"], expected)

	def test_a_slip_with_no_employer_half_is_mirrored_and_said_so(self):
		bare = slip(gross=1000.0)
		bare["social_security_employer"] = 0.0
		bare["medicare_employer"] = 0.0
		self.seed_payroll("PAY-OLD", "2025-02-01", "2025-02-14", [bare])
		data = self.tool_data("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["federal"]["deposit_liability"], 253.0)
		self.assertIn("MIRRORED", " ".join(data["warnings"]))

	def test_the_call_is_audited(self):
		self.seed_a_quarter()
		self.tool_data("get_tax_remittance_summary", {"company": MAIN, "fiscal_year": "2025"})
		self.assertAudited("get_tax_remittance_summary", "Success")


# ── Claim 10: the 941 prefill and the state reports ───────────────────────


class PrefillAndStateTools(RemittanceToolTestCase):
	"""The federal return, Oregon's two forms and Washington's one."""

	def test_the_941_lines_come_through(self):
		self.seed_a_quarter()
		form = self.tool_data("get_941_prefill", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["form_941"]
		self.assertEqual(form["line2_wages_tips_other_compensation"], 2000.0)
		self.assertEqual(form["line3_federal_income_tax_withheld"], 200.0)
		self.assertEqual(form["line1_number_of_employees"], 1)

	def test_the_943_warning_is_first_because_this_is_a_farm(self):
		"""Buried at position six it is a warning nobody reads."""
		self.seed_a_quarter()
		warnings = self.tool_data("get_941_prefill", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["warnings"]
		self.assertIn("FORM 943, NOT FORM 941", warnings[0])
		self.assertIn("ANNUAL", warnings[0])

	def test_part_two_reconciles_to_line_twelve(self):
		self.seed_a_quarter()
		data = self.tool_data("get_941_prefill", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		part2 = data["part2_monthly_liability"]
		self.assertTrue(part2["reconciles"])
		self.assertEqual(part2["reconciled_total"], data["form_941"]["line12_total_taxes_after_credits"])

	def test_the_prefill_records_nothing(self):
		"""It is recomputed on every call; generate_tax_form is what stores."""
		self.seed_a_quarter()
		self.tool_data("get_941_prefill", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(STORE.rows("Tax Form"), [])

	def test_the_oregon_report_carries_both_forms(self):
		self.seed_a_quarter()
		report = self.tool_data("get_state_tax_remittance", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["reports"]["OR"]
		self.assertEqual(report["forms"], ["OQ", "Form 132"])
		self.assertEqual(report["oq"]["subject_wages"], 2000.0)
		self.assertEqual(report["form_132"]["employee_count"], 1)
		self.assertEqual(report["form_132"]["total_hours"], 160)

	def test_the_oregon_bin_reaches_both_forms(self):
		self.seed_a_quarter()
		report = self.tool_data("get_state_tax_remittance", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["reports"]["OR"]
		self.assertEqual(report["form_132"]["oregon_bin"], "1234567-8")

	def test_the_oq_and_the_132_are_reconciled_against_each_other(self):
		"""Oregon rejects a filing where the two disagree, so this says when."""
		self.seed_a_quarter()
		data = self.tool_data("get_state_tax_remittance", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertNotIn("Oregon reconciles the two", " ".join(data["warnings"]))

	def test_washington_is_reported_when_asked_for(self):
		self.seed_payroll("PAY-WA", "2025-02-01", "2025-02-14", [slip(gross=1000.0, state="WA")])
		data = self.tool_data("get_state_tax_remittance", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1", "state": "WA",
		})
		self.assertEqual(data["states"], ["WA"])
		self.assertNotIn("OR", data["reports"])

	def test_both_states_are_reported_by_default(self):
		self.seed_a_quarter()
		data = self.tool_data("get_state_tax_remittance", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["states"], ["OR", "WA"])

	def test_both_states_share_the_federal_due_date(self):
		self.seed_a_quarter()
		data = self.tool_data("get_state_tax_remittance", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["due_date"], "2025-04-30")
		self.assertEqual(data["reports"]["OR"]["due_date"], "2025-04-30")

	def test_the_futa_summary_walks_the_year(self):
		for index, month in enumerate((2, 5, 8, 11)):
			self.seed_payroll(f"PAY-{index}", f"2025-{month:02d}-01", f"2025-{month:02d}-28",
			                  [slip(gross=3000.0)])
		form = self.tool_data("get_futa_summary", {
			"company": MAIN, "fiscal_year": "2025",
		})["form_940"]
		self.assertEqual(form["line7_total_taxable_futa_wages"], 7000.0)
		self.assertEqual(form["line16_quarterly_liabilities"]["Q4"], 0.0)


# ── Claim 11: the deposit schedule ────────────────────────────────────────


class DepositScheduleTool(RemittanceToolTestCase):
	"""When each deposit is due, and how much the payday behind it is worth."""

	def test_a_small_employer_is_a_monthly_depositor(self):
		self.seed_a_quarter()
		data = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["deposit_schedule"], "Monthly")
		self.assertEqual(data["federal_deposits"][0]["due_date"], "2025-03-17")

	def test_the_schedule_can_be_supplied_directly(self):
		self.seed_a_quarter()
		data = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1", "schedule": "Semiweekly",
		})
		self.assertEqual(data["deposit_schedule"], "Semiweekly")
		self.assertIn("supplied as Semiweekly", data["schedule_basis"])

	def test_a_supplied_lookback_total_decides_the_schedule(self):
		self.seed_a_quarter()
		data = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "lookback_total": 60000,
		})
		self.assertEqual(data["deposit_schedule"], "Semiweekly")

	def test_a_thin_lookback_is_flagged_as_a_floor(self):
		"""A quarter this app never ran reads as zero, not as unknown."""
		self.seed_a_quarter()
		data = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025",
		})
		self.assertIn("FLOOR", " ".join(data["warnings"]))

	def test_the_period_end_is_used_when_there_is_no_payday(self):
		self.seed_a_quarter()
		row = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["federal_deposits"][0]
		self.assertTrue(row["payday_is_assumed"])
		self.assertEqual(row["payday"], "2025-02-14")
		self.assertIn("THE PAY PERIOD END", row["payday_basis"])

	def test_the_offset_moves_the_payday(self):
		self.seed_a_quarter()
		row = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1", "payday_offset_days": 5,
		})["federal_deposits"][0]
		self.assertEqual(row["payday"], "2025-02-19")

	def test_a_posted_run_uses_its_real_ledger_date(self):
		"""A run that reached the ledger has a recorded date; prefer it."""
		self.seed_payroll("PAY-POSTED", "2025-02-01", "2025-02-14", [slip(gross=1000.0)],
		                  postings=["2025-02-20"])
		row = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["federal_deposits"][0]
		self.assertFalse(row["payday_is_assumed"])
		self.assertEqual(row["payday"], "2025-02-20")
		self.assertIn("GL posting date", row["payday_basis"])

	def test_an_assumed_payday_says_the_dates_are_early(self):
		self.seed_a_quarter()
		warnings = " ".join(self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["warnings"])
		self.assertIn("EARLY", warnings)

	def test_the_next_day_rule_is_raised_on_a_large_deposit(self):
		self.seed_payroll("PAY-BIG", "2025-02-01", "2025-02-14", [slip(gross=500000.0)])
		data = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertTrue(data["federal_deposits"][0]["next_day_rule"])
		self.assertIn("next-day", " ".join(data["warnings"]))

	def test_the_state_deadlines_cover_all_three_jurisdictions(self):
		self.seed_a_quarter()
		rows = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})["state_deadlines"]
		self.assertEqual({row["jurisdiction"] for row in rows}, {"Federal", "OR", "WA"})
		self.assertTrue(all(row["due_date"] == "2025-04-30" for row in rows))

	def test_a_whole_year_lists_every_quarters_deadline(self):
		self.seed_a_quarter()
		rows = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025",
		})["state_deadlines"]
		self.assertEqual(len(rows), 12)

	def test_the_monthly_rollup_totals_the_deposits(self):
		self.seed_a_quarter()
		data = self.tool_data("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertEqual(data["monthly_rollup"][0]["month"], "2025-02")
		self.assertEqual(data["monthly_rollup"][0]["liability"], data["federal_deposit_total"])


# ── Claim 12: the refusals ────────────────────────────────────────────────


class RemittanceRefusals(RemittanceToolTestCase):
	"""The switches, the HR gate, and every argument check."""

	def test_every_tool_ships_on(self):
		"""These are reads and the brief ships them enabled."""
		self.configure(enabled=1)
		for tool in REMITTANCE_TOOLS:
			with self.subTest(tool=tool):
				args = {"company": MAIN, "fiscal_year": "2025"}
				if tool in ("get_941_prefill", "get_state_tax_remittance"):
					args["quarter"] = "Q1"
				self.assertNotIn("switched off", str(self.tool_data(tool, args)).lower())

	def test_each_tool_can_be_switched_off_individually(self):
		for tool in REMITTANCE_TOOLS:
			with self.subTest(tool=tool):
				self.configure(enabled=1, **{**REMITTANCE_TOOLS_ON, f"allow_{tool}": 0})
				error = self.tool_error(tool, {"company": MAIN, "fiscal_year": "2025"})
				self.assertIn("switched off", error.lower())

	def test_the_hr_role_is_required(self):
		"""These name what everybody on the farm was paid."""
		set_roles("Administrator", ["Accounts User"])
		for tool in REMITTANCE_TOOLS:
			with self.subTest(tool=tool):
				error = self.tool_error(tool, {"company": MAIN, "fiscal_year": "2025"})
				self.assertIn("HR Manager", error)

	def test_a_farm_manager_may_read_them(self):
		set_roles("Administrator", ["Farm Manager"])
		self.seed_a_quarter()
		data = self.tool_data("get_tax_remittance_summary", {"company": MAIN, "fiscal_year": "2025"})
		self.assertEqual(data["gross_pay"], 2000.0)

	def test_a_missing_year_is_refused_by_name(self):
		error = self.tool_error("get_tax_remittance_summary", {"company": MAIN})
		self.assertIn("fiscal_year is required", error)

	def test_a_year_outside_the_range_is_refused(self):
		error = self.tool_error("get_tax_remittance_summary", {"company": MAIN, "fiscal_year": "1899"})
		self.assertIn("four-digit calendar year", error)

	def test_a_bad_quarter_is_refused(self):
		error = self.tool_error("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q5",
		})
		self.assertIn("quarter must be one of", error)

	def test_the_quarter_is_taken_as_a_number_as_well_as_a_string(self):
		"""v0.92.2. The iOS picker posts the integer; a model writes 'Q1'."""
		self.seed_a_quarter()
		for sent in ("Q1", "q1", "1", 1):
			with self.subTest(quarter=sent):
				data = self.tool_data("get_tax_remittance_summary", {
					"company": MAIN, "fiscal_year": "2025", "quarter": sent,
				})
				self.assertEqual(data["quarter"], "Q1")
				self.assertEqual(data["period_start"], "2025-01-01")
				self.assertEqual(data["period_end"], "2025-03-31")
				self.assertEqual(data["gross_pay"], 2000.0)

	def test_a_number_satisfies_the_tools_that_require_a_quarter(self):
		"""The refusal it used to draw was raised where a picker cannot correct it."""
		self.seed_a_quarter()
		for tool in ("get_941_prefill", "get_state_tax_remittance"):
			with self.subTest(tool=tool):
				data = self.tool_data(tool, {"company": MAIN, "fiscal_year": "2025", "quarter": 1})
				self.assertEqual(data["quarter"], "Q1")

	def test_a_number_outside_one_to_four_is_still_refused(self):
		"""NORMALISING IS NOT ACCEPTING. 0, 5 and 13 are wrong in either spelling."""
		for sent in (0, 5, 13, "Q0", "5"):
			with self.subTest(quarter=sent):
				error = self.tool_error("get_tax_remittance_summary", {
					"company": MAIN, "fiscal_year": "2025", "quarter": sent,
				})
				self.assertIn("quarter must be one of", error)
				self.assertIn("number 1 to 4", error)

	def test_a_quarter_that_is_neither_is_quoted_back_unchanged(self):
		"""A guess would be worse than the refusal: 2026-Q2 is another tool's format."""
		error = self.tool_error("get_tax_remittance_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "2026-Q2",
		})
		self.assertIn("2026-Q2", error)

	def test_the_annual_futa_tool_refuses_a_numbered_quarter_too(self):
		"""The normaliser runs BEFORE the annual check, so 1 is caught like 'Q1'."""
		error = self.tool_error("get_futa_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": 1,
		})
		self.assertIn("ANNUAL return", error)

	def test_a_quarterly_tool_needs_a_quarter(self):
		for tool in ("get_941_prefill", "get_state_tax_remittance"):
			with self.subTest(tool=tool):
				error = self.tool_error(tool, {"company": MAIN, "fiscal_year": "2025"})
				self.assertIn("quarter is required", error)

	def test_the_annual_futa_tool_refuses_a_quarter(self):
		"""Silently ignoring it would label a year's figures as a quarter's."""
		error = self.tool_error("get_futa_summary", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1",
		})
		self.assertIn("ANNUAL return", error)

	def test_an_unsupported_state_is_refused_by_name(self):
		error = self.tool_error("get_state_tax_remittance", {
			"company": MAIN, "fiscal_year": "2025", "quarter": "Q1", "state": "CA",
		})
		self.assertIn("state must be one of", error)

	def test_a_negative_payday_offset_is_refused(self):
		error = self.tool_error("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "payday_offset_days": -3,
		})
		self.assertIn("between 0 and", error)

	def test_a_payday_offset_that_is_really_a_date_is_refused(self):
		error = self.tool_error("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "payday_offset_days": 20250214,
		})
		self.assertIn("number of DAYS", error)

	def test_an_unknown_deposit_schedule_is_refused(self):
		error = self.tool_error("get_tax_deposit_schedule", {
			"company": MAIN, "fiscal_year": "2025", "schedule": "Fortnightly",
		})
		self.assertIn("Monthly", error)
