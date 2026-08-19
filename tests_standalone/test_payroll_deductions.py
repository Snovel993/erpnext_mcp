# SPDX-License-Identifier: MIT
"""Garnishments and voluntary deductions.

TWELVE CLAIMS.

1.  `CCPAFloor` — the exempt floor is 30x the federal minimum wage a week, and
    the regulation's own multipliers for the longer periods.
2.  `OrdinaryCeiling` — the lesser of 25% of disposable earnings and the amount
    over the floor, matching the DOL's own published examples, with the binding
    rule named.
3.  `ChildSupportCeiling` — 50/55/60/65 by the two facts in 1673(b)(2), and
    orders that will not both fit are prorated.
4.  `SharedPool` — 29 CFR 870.11(b)(1): an ordinary garnishment gets what is
    LEFT of the 25% after support, not a fresh 25%.
5.  `LevyAndStudentLoan` — a tax levy is outside the CCPA entirely; a student
    loan is capped at 15% and still inside the shared pool.
6.  `PreTaxSplit` — THE CENTRAL CLAIM. A 401(k) leaves the income tax base and
    STAYS in the FICA base; a Section 125 benefit leaves both.
7.  `DisposableEarnings` — voluntary deductions do not reduce it, however
    pre-tax, so an employee cannot shrink a court order's base.
8.  `Ordering` — garnishments outrank voluntary deductions when money runs out,
    and nothing ever produces a negative net.
9.  `SlipIntegration` — the whole stack through `calculate_full_payroll`, with
    `net_pay == gross_pay - total_deductions` preserved.
10. `ActiveWindow` — status and the date range decide what is withheld, and an
    open-ended order is not dropped.
11. `DeductionTools` — the five MCP tools.
12. `EdgeCases` — no deductions changes nothing, zero pay, unknown categories.
"""

import frappe

from erpnext_mcp import payroll_deductions as pd
from erpnext_mcp.payroll_calc import calculate_full_payroll
from erpnext_mcp.payroll_integration import run_integrated_payroll, summarize_payroll_run
from erpnext_mcp.withholding import calculate_federal_withholding

from .fixtures import MAIN, V12TestCase, install_hrms
from .harness import STORE

DEDUCTION_TOOLS_ON = {
	f"allow_{name}": 1
	for name in (
		"list_payroll_deductions",
		"get_payroll_deduction",
		"list_employee_deductions",
		"create_payroll_deduction",
		"update_payroll_deduction",
	)
}

FICA = {
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


def row(**kwargs) -> dict:
	"""One deduction row with the fields the engine reads, defaulted sanely."""
	base = {
		"name": kwargs.pop("name", "FPD-0001"),
		"status": "Active",
		"amount_type": "Fixed",
		"effective_from": "2026-01-01",
	}
	base.update(kwargs)
	return base


# ── 1. The CCPA floor ───────────────────────────────────────────────────────


class CCPAFloor(V12TestCase):
	"""30 x the federal minimum wage a week, and 29 CFR 870.10(b)'s own
	multipliers for the longer periods."""

	def test_the_weekly_floor_is_thirty_hours_of_federal_minimum_wage(self):
		self.assertEqual(pd.ccpa_exempt_floor("Weekly"), 217.50)

	def test_the_longer_periods_use_the_regulations_own_multipliers(self):
		"""NOT simple multiples of the weekly figure. Semimonthly is 65x and
		monthly is 130x, which is what 870.10(b) says rather than 2.17x and
		4.33x of a week — the difference is real money at the margin."""
		self.assertEqual(pd.ccpa_exempt_floor("Biweekly"), 435.00)
		self.assertEqual(pd.ccpa_exempt_floor("Semimonthly"), 471.25)
		self.assertEqual(pd.ccpa_exempt_floor("Monthly"), 942.50)

	def test_an_unknown_frequency_falls_back_to_the_weekly_floor(self):
		"""The most protective reading, and the one the regulation is written
		in. A frequency this app does not know must not silently exempt nothing."""
		self.assertEqual(pd.ccpa_exempt_floor("Fortnightly"), 217.50)


# ── 2. The ordinary ceiling ─────────────────────────────────────────────────


class OrdinaryCeiling(V12TestCase):
	"""The lesser of two rules, with the binding one named."""

	def test_the_dols_own_worked_examples(self):
		"""$370 a week is bound by the 25%; $250 by the floor; $200 yields
		nothing at all. These are the figures in the DOL's Fact Sheet #30, and
		a change that breaks any of them has broken the statute."""
		self.assertEqual(pd.ordinary_garnishment_ceiling(370.0, "Weekly")["ceiling"], 92.50)
		self.assertEqual(pd.ordinary_garnishment_ceiling(250.0, "Weekly")["ceiling"], 32.50)
		self.assertEqual(pd.ordinary_garnishment_ceiling(200.0, "Weekly")["ceiling"], 0.0)

	def test_it_names_which_rule_bound_the_answer(self):
		"""'Why did only $32.50 come out of a $250 cheque' is a question a
		payroll clerk is asked, and the answer is one of two sentences."""
		self.assertIn("25%", pd.ordinary_garnishment_ceiling(370.0, "Weekly")["binding_rule"])
		self.assertIn("minimum wage", pd.ordinary_garnishment_ceiling(250.0, "Weekly")["binding_rule"])

	def test_a_stricter_state_rate_wins_and_a_looser_one_does_not(self):
		"""15 U.S.C. 1677: Title III is a floor under the worker's protection
		and never a ceiling on it. A state that caps at 15% binds; a state
		claiming 50% cannot loosen the federal 25%."""
		strict = pd.ordinary_garnishment_ceiling(1000.0, "Biweekly", state_cap_rate=0.15)
		self.assertEqual(strict["ceiling"], 150.00)
		loose = pd.ordinary_garnishment_ceiling(1000.0, "Biweekly", state_cap_rate=0.50)
		self.assertEqual(loose["ceiling"], 250.00)


# ── 3. Child support ────────────────────────────────────────────────────────


class ChildSupportCeiling(V12TestCase):
	"""50/55/60/65, by the two facts 1673(b)(2) turns on."""

	def test_the_four_rates(self):
		supporting = [row(deduction_category="Child Support", supports_other_dependents=1)]
		self.assertEqual(pd.child_support_ceiling(1000.0, supporting)["rate"], 0.50)

		not_supporting = [row(deduction_category="Child Support", supports_other_dependents=0)]
		self.assertEqual(pd.child_support_ceiling(1000.0, not_supporting)["rate"], 0.60)

		arrears = [
			row(deduction_category="Child Support", supports_other_dependents=1, arrears_over_12_weeks=1)
		]
		self.assertEqual(pd.child_support_ceiling(1000.0, arrears)["rate"], 0.55)

		both = [row(deduction_category="Child Support", supports_other_dependents=0, arrears_over_12_weeks=1)]
		self.assertEqual(pd.child_support_ceiling(1000.0, both)["rate"], 0.65)

	def test_two_orders_that_will_not_both_fit_are_prorated(self):
		"""$800 of orders against a $500 ceiling. Each gets its share by ordered
		amount, the total equals the ceiling exactly, and each line SAYS it was
		prorated rather than leaving somebody to infer it from the arithmetic."""
		orders = [
			row(name="CS1", deduction_category="Child Support", amount=400),
			row(name="CS2", deduction_category="Child Support", amount=400),
		]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		amounts = [line["amount"] for line in result["lines"]]
		self.assertEqual(amounts, [250.0, 250.0])
		self.assertEqual(sum(amounts), result["child_support"]["ceiling"])
		self.assertTrue(all(line["prorated"] for line in result["lines"]))
		self.assertEqual(len(result["shortfalls"]), 2)

	def test_orders_that_fit_are_not_prorated(self):
		orders = [
			row(name="CS1", deduction_category="Child Support", amount=100),
			row(name="CS2", deduction_category="Child Support", amount=150),
		]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		self.assertEqual([line["amount"] for line in result["lines"]], [100.0, 150.0])
		self.assertFalse(any(line["prorated"] for line in result["lines"]))
		self.assertEqual(result["shortfalls"], [])


# ── 4. The shared pool ──────────────────────────────────────────────────────


class SharedPool(V12TestCase):
	"""29 CFR 870.11(b)(1). The rule most likely to be got wrong by giving each
	order its own fresh 25%."""

	def test_a_creditor_gets_what_is_left_of_the_pool_after_support(self):
		"""$1000 disposable, biweekly. The ordinary ceiling is $250. Support
		takes $150, so the creditor may have $100 — NOT $250."""
		orders = [
			row(name="CS", deduction_category="Child Support", amount=150),
			row(name="WG", deduction_category="Wage Garnishment", amount=500),
		]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		by_name = {line["deduction"]: line for line in result["lines"]}
		self.assertEqual(by_name["CS"]["amount"], 150.0)
		self.assertEqual(by_name["WG"]["amount"], 100.0)

	def test_support_over_the_ordinary_ceiling_leaves_a_creditor_nothing(self):
		"""Support may take up to 50%, and when it takes more than the ordinary
		25% the creditor's pool is empty. Zero is the rule working, not a
		failure to collect, and the shortfall says which rule refused it."""
		orders = [
			row(name="CS", deduction_category="Child Support", amount=400),
			row(name="WG", deduction_category="Wage Garnishment", amount=300),
		]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		by_name = {line["deduction"]: line for line in result["lines"]}
		self.assertEqual(by_name["CS"]["amount"], 400.0)
		self.assertEqual(by_name["WG"]["amount"], 0.0)
		self.assertEqual(by_name["WG"]["shortfall"], 300.0)
		self.assertIn("870.11(b)(1)", by_name["WG"]["shortfall_reason"])

	def test_child_support_is_taken_before_a_creditor_whatever_order_they_arrive_in(self):
		"""The creditor is listed first and filed earlier, and still yields."""
		orders = [
			row(name="WG", deduction_category="Wage Garnishment", amount=500, effective_from="2020-01-01"),
			row(name="CS", deduction_category="Child Support", amount=200, effective_from="2026-01-01"),
		]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		self.assertEqual([line["deduction"] for line in result["lines"]], ["CS", "WG"])
		by_name = {line["deduction"]: line for line in result["lines"]}
		self.assertEqual(by_name["CS"]["amount"], 200.0)
		self.assertEqual(by_name["WG"]["amount"], 50.0)


# ── 5. Levies and student loans ─────────────────────────────────────────────


class LevyAndStudentLoan(V12TestCase):
	def test_a_tax_levy_is_not_bound_by_the_ccpa(self):
		"""29 CFR 870.11(b)(2). $600 out of $1000 disposable is well past the
		$250 ordinary ceiling and is correct: a levy is outside Title III."""
		orders = [row(name="TL", deduction_category="Tax Levy", amount=600)]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		self.assertEqual(result["lines"][0]["amount"], 600.0)
		self.assertEqual(result["lines"][0]["limit_rule"], "exempt_from_ccpa")

	def test_a_levys_exempt_amount_is_left_to_the_employee(self):
		"""IRC 6334(d). With $700 exempt, a $600 levy against $1000 disposable
		can only reach $300."""
		orders = [row(name="TL", deduction_category="Tax Levy", amount=600, exempt_amount=700)]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		self.assertEqual(result["lines"][0]["amount"], 300.0)
		self.assertIn("6334(d)", result["lines"][0]["shortfall_reason"])

	def test_a_student_loan_is_capped_at_fifteen_percent(self):
		"""20 U.S.C. 1095a(a)(1)."""
		orders = [row(name="SL", deduction_category="Student Loan", amount=500)]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		self.assertEqual(result["lines"][0]["amount"], 150.0)

	def test_a_student_loan_shares_the_pool_with_a_creditor(self):
		"""$150 to the loan leaves $100 of the $250 pool for the creditor."""
		orders = [
			row(name="SL", deduction_category="Student Loan", amount=500),
			row(name="WG", deduction_category="Wage Garnishment", amount=500),
		]
		result = pd.apply_garnishments(1000.0, pd.active_deductions(orders), "Biweekly")
		by_name = {line["deduction"]: line for line in result["lines"]}
		self.assertEqual(by_name["SL"]["amount"], 150.0)
		self.assertEqual(by_name["WG"]["amount"], 100.0)


# ── 6. The pre-tax split ────────────────────────────────────────────────────


class PreTaxSplit(V12TestCase):
	"""THE CENTRAL CLAIM OF THIS RELEASE. Two reduced bases, not one."""

	def test_a_401k_leaves_the_income_tax_base_and_stays_in_the_fica_base(self):
		"""IRC 402(e)(3) defers the income tax; 3121(v)(1)(A) keeps the deferral
		in the Social Security and Medicare wage base. Collapsing these into one
		figure under-withholds FICA on every deferral."""
		rows = pd.active_deductions([row(deduction_category="Retirement 401k", amount=120)])
		result = pd.calculate_pre_tax_deductions(2000.0, rows)
		self.assertEqual(result["federal_taxable_gross"], 1880.0)
		self.assertEqual(result["fica_taxable_gross"], 2000.0)

	def test_a_section_125_benefit_leaves_both_bases(self):
		"""IRC 125(a) and 3121(a)(5)(G)."""
		for category in ("Health Insurance", "Dental Vision", "HSA", "FSA"):
			with self.subTest(category=category):
				rows = pd.active_deductions([row(deduction_category=category, amount=200)])
				result = pd.calculate_pre_tax_deductions(2000.0, rows)
				self.assertEqual(result["federal_taxable_gross"], 1800.0)
				self.assertEqual(result["fica_taxable_gross"], 1800.0)

	def test_the_two_bases_differ_by_exactly_the_deferral(self):
		"""Which is what W-2 Box 1 and Box 3 are supposed to differ by."""
		rows = pd.active_deductions(
			[
				row(name="K", deduction_category="Retirement 401k", amount=120),
				row(name="H", deduction_category="Health Insurance", amount=200),
			]
		)
		result = pd.calculate_pre_tax_deductions(2000.0, rows)
		self.assertEqual(result["federal_taxable_gross"], 1680.0)
		self.assertEqual(result["fica_taxable_gross"], 1800.0)
		self.assertEqual(result["fica_taxable_gross"] - result["federal_taxable_gross"], 120.0)

	def test_post_tax_categories_reduce_neither_base(self):
		for category in ("Life Insurance", "Union Dues"):
			with self.subTest(category=category):
				rows = pd.active_deductions([row(deduction_category=category, amount=50)])
				result = pd.calculate_pre_tax_deductions(2000.0, rows)
				self.assertEqual(result["total"], 0.0)
				self.assertEqual(result["federal_taxable_gross"], 2000.0)

	def test_the_withholding_engine_takes_the_two_bases_apart(self):
		"""The engine's own contract, not the deduction module's: Social
		Security is computed on `fica_gross` and income tax on `gross_pay`."""
		both = calculate_federal_withholding(
			1880.0,
			"Biweekly",
			{"filing_status": "Single"},
			0,
			0,
			FICA,
			[],
			fica_gross=2000.0,
		)
		self.assertEqual(both["social_security_employee"], round(2000.0 * 0.062, 2))

	def test_omitting_fica_gross_leaves_every_prior_caller_unchanged(self):
		"""The parameter is optional and defaults to `gross_pay`, so a release
		that never heard of deductions computes exactly what it always did."""
		without = calculate_federal_withholding(
			2000.0,
			"Biweekly",
			{"filing_status": "Single"},
			0,
			0,
			FICA,
			[],
		)
		explicit = calculate_federal_withholding(
			2000.0,
			"Biweekly",
			{"filing_status": "Single"},
			0,
			0,
			FICA,
			[],
			fica_gross=2000.0,
		)
		self.assertEqual(without["social_security_employee"], explicit["social_security_employee"])
		self.assertNotIn("fica_base", without["computation_detail"])


# ── 7. Disposable earnings ──────────────────────────────────────────────────


class DisposableEarnings(V12TestCase):
	def test_it_is_gross_less_the_statutory_withholding(self):
		self.assertEqual(pd.disposable_earnings(2000.0, 350.0), 1650.0)

	def test_it_never_goes_negative(self):
		self.assertEqual(pd.disposable_earnings(100.0, 500.0), 0.0)

	def test_a_401k_election_does_not_shrink_a_court_orders_base(self):
		"""29 CFR 870.10(a) says amounts required BY LAW, and an elective
		deferral is not one. If it were subtracted an employee could raise their
		own contribution rate to reduce what a support order reaches, which is
		the employee choosing how much of a court order to obey."""
		gross, statutory = 2000.0, 350.0
		bare = pd.disposable_earnings(gross, statutory)

		rows = pd.active_deductions([row(deduction_category="Retirement 401k", amount=500)])
		pre_tax = pd.calculate_pre_tax_deductions(gross, rows)
		self.assertEqual(pre_tax["total"], 500.0)
		# The deferral moved the tax base and left disposable earnings alone.
		self.assertEqual(pd.disposable_earnings(gross, statutory), bare)


# ── 8. Ordering and the floor at zero ───────────────────────────────────────


class Ordering(V12TestCase):
	def test_a_garnishment_outranks_a_voluntary_deduction(self):
		"""$260 of cash, a $250 garnishment and $100 of union dues. The dues are
		what goes short, because a court order does not yield to one."""
		orders = pd.active_deductions(
			[
				row(name="WG", deduction_category="Wage Garnishment", amount=250),
				row(name="UD", deduction_category="Union Dues", amount=100),
			]
		)
		garnishments = pd.apply_garnishments(1000.0, orders, "Biweekly")
		self.assertEqual(garnishments["total"], 250.0)

		cash = 260.0 - garnishments["total"]
		post = pd.apply_post_tax_deductions(cash, orders, 1000.0, 1000.0)
		self.assertEqual(post["lines"][0]["amount"], 10.0)
		self.assertEqual(post["lines"][0]["shortfall"], 90.0)

	def test_the_default_priority_order_is_the_legal_one(self):
		orders = pd.active_deductions(
			[
				row(name="UD", deduction_category="Union Dues", amount=10),
				row(name="WG", deduction_category="Wage Garnishment", amount=10),
				row(name="SL", deduction_category="Student Loan", amount=10),
				row(name="TL", deduction_category="Tax Levy", amount=10),
				row(name="CS", deduction_category="Child Support", amount=10),
			]
		)
		self.assertEqual(
			[r["name"] for r in orders],
			["CS", "TL", "SL", "WG", "UD"],
		)

	def test_an_explicit_priority_beats_the_categorys_but_zero_does_not(self):
		"""Zero is an empty Int field, not a request to go first. Treating it as
		first would promote every row nobody filled in ahead of a support order."""
		self.assertEqual(pd.row_priority(row(deduction_category="Union Dues")), 160)
		self.assertEqual(pd.row_priority(row(deduction_category="Union Dues", priority=0)), 160)
		self.assertEqual(pd.row_priority(row(deduction_category="Union Dues", priority=5)), 5)

	def test_a_garnishment_can_never_be_marked_pre_tax(self):
		"""Even where the row asserts it. Money taken under a court order is
		wages the worker was taxed on."""
		self.assertFalse(pd.row_is_pre_tax(row(deduction_category="Child Support", pre_tax=1)))


# ── 9. The whole stack on a slip ────────────────────────────────────────────


class SlipIntegration(V12TestCase):
	"""`calculate_full_payroll` end to end."""

	def _slip(self, deductions=None):
		return calculate_full_payroll(
			{"employee": "E1", "employee_name": "Ana"},
			[{"work_state": "OR", "hours": 80, "overtime_hours": 0, "piece_units": 0, "break_hours": 0}],
			{"name": "S1", "pay_type": "Hourly", "base_rate": 25.0},
			{
				"w4_data": {"filing_status": "Single"},
				"fica_config": FICA,
				"federal_tax_table": [],
				"pay_frequency": "Biweekly",
			},
			deductions=deductions,
		)

	def test_net_pay_is_still_gross_less_total_deductions(self):
		"""The invariant every reader of a slip relies on. Deductions are INSIDE
		`total_deductions`, so net pay stays what the worker is handed."""
		slip = self._slip(
			[
				row(name="K", deduction_category="Retirement 401k", amount=120),
				row(name="CS", deduction_category="Child Support", amount=300),
				row(name="UD", deduction_category="Union Dues", amount=40),
			]
		)
		self.assertEqual(
			round(slip["gross_pay"] - slip["total_deductions"], 2),
			slip["net_pay"],
		)

	def test_the_totals_decompose(self):
		slip = self._slip(
			[
				row(name="K", deduction_category="Retirement 401k", amount=120),
				row(name="CS", deduction_category="Child Support", amount=300),
				row(name="UD", deduction_category="Union Dues", amount=40),
			]
		)
		self.assertEqual(slip["pre_tax_deductions"], 120.0)
		self.assertEqual(slip["garnishment_total"], 300.0)
		self.assertEqual(slip["post_tax_deductions"], 340.0)
		self.assertEqual(slip["total_deduction_withholdings"], 460.0)
		self.assertEqual(
			slip["total_deductions"],
			round(slip["statutory_deductions"] + slip["total_deduction_withholdings"], 2),
		)

	def test_every_line_is_itemised_for_a_pay_stub(self):
		"""A stub prints line by line; one lump called 'Other' is not a stub."""
		slip = self._slip(
			[
				row(name="K", deduction_category="Retirement 401k", amount=120),
				row(name="CS", deduction_category="Child Support", amount=300),
			]
		)
		labels = [line["label"] for line in slip["deduction_lines"]]
		self.assertEqual(labels, ["401(k)", "Child Support"])

	def test_a_custom_label_survives_to_the_stub(self):
		slip = self._slip(
			[
				row(deduction_category="Child Support", amount=100, label="Support - Case 44821"),
			]
		)
		self.assertEqual(slip["deduction_lines"][0]["label"], "Support - Case 44821")

	def test_the_fica_base_reduction_reaches_the_actual_withholding(self):
		"""Not merely reported: Social Security on the slip is computed on the
		reduced base. A health premium reduces it and a 401(k) does not."""
		bare = self._slip()
		premium = self._slip([row(deduction_category="Health Insurance", amount=200)])
		deferral = self._slip([row(deduction_category="Retirement 401k", amount=200)])

		self.assertEqual(bare["social_security"], round(2000.0 * 0.062, 2))
		self.assertEqual(premium["social_security"], round(1800.0 * 0.062, 2))
		self.assertEqual(deferral["social_security"], bare["social_security"])

	def test_a_slip_with_no_deductions_is_unchanged(self):
		"""The whole stack collapses to zero and no figure moves — which is
		every slip on every site that has filed no deduction."""
		bare = self._slip()
		empty = self._slip([])
		for key in ("gross_pay", "total_deductions", "net_pay", "social_security", "medicare"):
			self.assertEqual(bare[key], empty[key], key)
		self.assertEqual(empty["deduction_lines"], [])
		self.assertEqual(empty["total_deduction_withholdings"], 0.0)

	def test_a_shortfall_is_reported_on_the_slip(self):
		slip = self._slip(
			[
				row(deduction_category="Wage Garnishment", amount=900),
			]
		)
		self.assertTrue(slip["deduction_shortfalls"])
		self.assertGreater(slip["deduction_shortfalls"][0]["shortfall"], 0)

	def test_the_run_summary_carries_the_deduction_totals_and_the_shortfalls(self):
		slips = run_integrated_payroll(
			[
				{
					"name": "SH1",
					"work_state": "OR",
					"start_datetime": "2026-03-02 06:00:00",
					"end_datetime": "2026-03-02 14:00:00",
					"crew": [{"employee": "E1", "employee_name": "Ana"}],
				}
			],
			{"E1": {"name": "S1", "employee": "E1", "pay_type": "Hourly", "base_rate": 25.0}},
			{},
			{},
			FICA,
			"2026-03-02",
			"2026-03-15",
			pay_frequency="Biweekly",
			deductions_by_employee={
				"E1": [row(deduction_category="Wage Garnishment", amount=900)],
			},
		)
		summary = summarize_payroll_run(slips)
		self.assertIn("total_garnishments", summary)
		self.assertTrue(summary["deduction_shortfalls"])
		self.assertEqual(summary["deduction_shortfalls"][0]["employee"], "E1")

	def test_deductions_never_leak_between_employees(self):
		"""A per-employee map, and a bug here garnishes the wrong person."""
		slips = run_integrated_payroll(
			[
				{
					"name": "SH1",
					"work_state": "OR",
					"start_datetime": "2026-03-02 06:00:00",
					"end_datetime": "2026-03-02 14:00:00",
					"crew": [{"employee": "E1"}, {"employee": "E2"}],
				},
			],
			{
				"E1": {"name": "S1", "employee": "E1", "pay_type": "Hourly", "base_rate": 25.0},
				"E2": {"name": "S2", "employee": "E2", "pay_type": "Hourly", "base_rate": 25.0},
			},
			{},
			{},
			FICA,
			"2026-03-02",
			"2026-03-15",
			pay_frequency="Biweekly",
			deductions_by_employee={
				"E1": [row(deduction_category="Child Support", amount=50)],
			},
		)
		by_employee = {slip["employee"]: slip for slip in slips}
		self.assertEqual(by_employee["E1"]["garnishment_total"], 50.0)
		self.assertEqual(by_employee["E2"]["garnishment_total"], 0.0)
		self.assertEqual(by_employee["E2"]["deduction_lines"], [])


# ── 10. What is in force ────────────────────────────────────────────────────


class ActiveWindow(V12TestCase):
	def test_only_active_status_is_withheld(self):
		self.assertTrue(pd.is_active(row(status="Active")))
		self.assertFalse(pd.is_active(row(status="Suspended")))
		self.assertFalse(pd.is_active(row(status="Completed")))

	def test_the_date_window_bounds_it(self):
		order = row(effective_from="2026-03-01", effective_to="2026-06-30")
		self.assertFalse(pd.is_active(order, "2026-02-28"))
		self.assertTrue(pd.is_active(order, "2026-03-01"))
		self.assertTrue(pd.is_active(order, "2026-06-30"))
		self.assertFalse(pd.is_active(order, "2026-07-01"))

	def test_an_open_ended_order_is_in_force_forever(self):
		"""Most support orders have no stated end. A naive `effective_to <= date`
		filter drops every one of them, silently."""
		order = row(effective_from="2026-01-01", effective_to=None)
		self.assertTrue(pd.is_active(order, "2099-01-01"))

	def test_a_suspended_order_withholds_nothing_on_a_slip(self):
		slip = calculate_full_payroll(
			{"employee": "E1"},
			[{"work_state": "OR", "hours": 80, "overtime_hours": 0}],
			{"name": "S1", "pay_type": "Hourly", "base_rate": 25.0},
			{"w4_data": {}, "fica_config": FICA, "federal_tax_table": [], "pay_frequency": "Biweekly"},
			deductions=[row(deduction_category="Child Support", amount=300, status="Suspended")],
		)
		self.assertEqual(slip["garnishment_total"], 0.0)


# ── 11. The tools ───────────────────────────────────────────────────────────


class DeductionTools(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **DEDUCTION_TOOLS_ON)
		install_hrms()
		STORE.seed(
			"Employee",
			[
				{
					"name": "HR-EMP-00001",
					"employee_name": "Ana Lopez",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-01-15",
				},
			],
		)

	def _create(self, **overrides):
		args = {
			"employee": "HR-EMP-00001",
			"company": MAIN,
			"deduction_category": "Child Support",
			"amount": 250.0,
			"effective_from": "2026-01-01",
			"reference": "CASE-44821",
		}
		args.update(overrides)
		return self.tool_data("create_payroll_deduction", args)

	def test_create_and_get(self):
		created = self._create()
		name = created["deduction"]["name"]
		self.assertEqual(created["deduction"]["deduction_category"], "Child Support")
		# The type came off the category rather than defaulting to Voluntary,
		# which would have put a support order behind the union dues.
		self.assertEqual(created["deduction"]["deduction_type"], "Garnishment")

		got = self.tool_data("get_payroll_deduction", {"deduction": name})
		self.assertEqual(got["deduction"]["reference"], "CASE-44821")

	def test_the_derived_facts_are_on_every_row(self):
		"""What the engine will make of it, not just what is stored."""
		created = self._create(
			deduction_category="Retirement 401k", amount=6, amount_type="Percentage", reference="PLAN-1"
		)
		row_out = created["deduction"]
		self.assertEqual(row_out["effective_type"], "voluntary")
		self.assertTrue(row_out["effective_pre_tax"])
		self.assertFalse(row_out["effective_fica_exempt"])
		self.assertEqual(row_out["pay_stub_label"], "401(k)")

	def test_list_and_filter(self):
		self._create()
		self._create(deduction_category="Union Dues", amount=25, reference="LOCAL-7")
		everything = self.tool_data("list_payroll_deductions", {"company": MAIN})
		self.assertEqual(everything["count"], 2)

		garnishments = self.tool_data(
			"list_payroll_deductions",
			{"company": MAIN, "deduction_type": "Garnishment"},
		)
		self.assertEqual(garnishments["count"], 1)

	def test_list_employee_deductions_is_in_processing_order(self):
		self._create(deduction_category="Union Dues", amount=25, reference="LOCAL-7")
		self._create()
		data = self.tool_data("list_employee_deductions", {"employee": "HR-EMP-00001"})
		self.assertEqual(
			[r["deduction_category"] for r in data["deductions"]],
			["Child Support", "Union Dues"],
		)

	def test_the_preview_prices_the_ccpa_ceiling(self):
		"""$400 ordered against $1000 disposable biweekly: the ceiling is $250."""
		self._create(amount=400)
		data = self.tool_data(
			"list_employee_deductions",
			{
				"employee": "HR-EMP-00001",
				"gross_pay": 1350.0,
				"statutory_withholding": 350.0,
				"pay_frequency": "Biweekly",
			},
		)
		self.assertEqual(data["preview"]["disposable_earnings"], 1000.0)
		self.assertEqual(data["preview"]["garnishment_total"], 400.0)
		self.assertNotIn("basis_warning", data["preview"])

	def test_the_preview_says_so_when_it_had_no_tax_figure(self):
		"""Treating gross as disposable OVERSTATES what a garnishment may take,
		so it is named rather than quietly assumed."""
		self._create()
		data = self.tool_data(
			"list_employee_deductions",
			{
				"employee": "HR-EMP-00001",
				"gross_pay": 1350.0,
			},
		)
		self.assertIn("basis_warning", data["preview"])

	def test_update_changes_and_echoes(self):
		created = self._create()
		name = created["deduction"]["name"]
		data = self.tool_data(
			"update_payroll_deduction",
			{
				"deduction": name,
				"amount": 300.0,
			},
		)
		self.assertEqual(data["deduction"]["amount"], 300.0)
		self.assertTrue(any(c["field"] == "amount" for c in data["changes"]))

	def test_a_deduction_is_retired_by_status_not_deleted(self):
		created = self._create()
		name = created["deduction"]["name"]
		self.tool_data("update_payroll_deduction", {"deduction": name, "status": "Completed"})
		self.assertTrue(frappe.db.exists("Farm Payroll Deduction", name))
		data = self.tool_data("list_employee_deductions", {"employee": "HR-EMP-00001"})
		self.assertEqual(data["active_count"], 0)

	def test_it_refuses_to_move_a_deduction_to_another_worker(self):
		created = self._create()
		error = self.tool_error(
			"update_payroll_deduction",
			{
				"deduction": created["deduction"]["name"],
				"employee": "HR-EMP-00002",
			},
		)
		self.assertIn("cannot be changed", error)

	def test_it_refuses_a_duplicate_active_order(self):
		"""Filing the same order twice withholds it twice, which is money
		actually taken from somebody."""
		self._create()
		error = self.tool_error(
			"create_payroll_deduction",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"deduction_category": "Child Support",
				"amount": 250.0,
				"effective_from": "2026-01-01",
				"reference": "CASE-44821",
			},
		)
		self.assertIn("already has an active", error)

	def test_a_second_order_with_a_different_reference_is_allowed(self):
		self._create()
		second = self._create(reference="CASE-99001")
		self.assertTrue(second["deduction"]["name"])

	def test_it_warns_about_a_garnishment_with_no_reference(self):
		created = self._create(reference="")
		self.assertTrue(any("reference" in note for note in created["notes"]))

	def test_it_warns_about_a_401k_marked_fica_exempt(self):
		created = self._create(
			deduction_category="Retirement 401k",
			amount=100,
			reference="PLAN-2",
			fica_exempt=True,
		)
		self.assertTrue(any("FICA" in note for note in created["notes"]))

	def test_a_mistyped_category_is_refused_at_write_time(self):
		"""'401k' is not the key — `retirement_401k` is. Caught here, it never
		reaches a pay stub as an unlabelled 'Other' line with a real amount."""
		error = self.tool_error(
			"create_payroll_deduction",
			{
				"employee": "HR-EMP-00001",
				"company": MAIN,
				"deduction_category": "401k",
				"amount": 100.0,
			},
		)
		self.assertIn("must be one of", error)
		self.assertIn("Retirement 401k", error)

	def test_a_category_written_around_the_tools_is_flagged_on_every_read(self):
		"""The tools refuse it and the Desk field is a Select, so a row like this
		was written by a patch, an import or the console — which is exactly the
		row nobody is watching. It resolves to the `other` spec silently, so the
		reads say so rather than leaving it to surface on somebody's pay stub."""
		created = self._create()
		name = created["deduction"]["name"]
		# Write around the tool the way a patch or an import would.
		STORE.tables["Farm Payroll Deduction"][name]["deduction_category"] = "401k"

		got = self.tool_data("get_payroll_deduction", {"deduction": name})
		self.assertFalse(got["deduction"]["deduction_category_recognised"])
		self.assertTrue(any("not one this app recognises" in note for note in got["notes"]))
		self.assertTrue(any("Other" in note for note in got["notes"]))

	def test_a_genuine_other_category_is_recognised(self):
		"""`Other` is a real category and must not be confused with the fallback
		an unrecognised one lands on — they resolve to the same spec."""
		created = self._create(
			deduction_category="Other", deduction_type="Voluntary", amount=15, reference="MISC-1"
		)
		self.assertTrue(created["deduction"]["deduction_category_recognised"])
		self.assertFalse(any("not one this app recognises" in note for note in created.get("notes", [])))

	def test_an_ambiguous_employee_name_is_refused(self):
		STORE.tables.setdefault("Employee", {}).update(
			{
				f"HR-EMP-0000{index}": {
					"doctype": "Employee",
					"name": f"HR-EMP-0000{index}",
					"employee_name": "Jose Garcia",
					"company": MAIN,
					"status": "Active",
					"docstatus": 0,
				}
				for index in (2, 3)
			}
		)
		error = self.tool_error(
			"create_payroll_deduction",
			{
				"employee": "Jose Garcia",
				"company": MAIN,
				"deduction_category": "Union Dues",
				"amount": 25.0,
			},
		)
		self.assertIn("matches 2 employees", error)


# ── 12. Edges ───────────────────────────────────────────────────────────────


class EdgeCases(V12TestCase):
	def test_zero_pay_withholds_nothing_and_does_not_divide_by_zero(self):
		orders = pd.active_deductions([row(deduction_category="Child Support", amount=300)])
		result = pd.apply_garnishments(0.0, orders, "Biweekly")
		self.assertEqual(result["total"], 0.0)
		self.assertEqual(result["lines"][0]["shortfall"], 300.0)

	def test_an_unknown_category_falls_back_to_other_rather_than_raising(self):
		"""The caller is a payroll run and the value came off a Select. A
		category this app does not know is not worth refusing a company's
		payroll over."""
		spec = pd.category_spec("Interplanetary Levy")
		self.assertEqual(spec["label"], "Other")
		self.assertEqual(pd.row_type(row(deduction_category="Interplanetary Levy")), "voluntary")

	def test_an_other_category_can_still_be_typed_a_garnishment(self):
		"""And is then bounded by the ordinary ceiling, not left unbounded."""
		orders = pd.active_deductions(
			[
				row(deduction_category="Other", deduction_type="Garnishment", amount=900),
			]
		)
		result = pd.apply_garnishments(1000.0, orders, "Biweekly")
		self.assertEqual(result["lines"][0]["amount"], 250.0)
		self.assertEqual(result["lines"][0]["limit_rule"], "ordinary")

	def test_a_percentage_is_of_the_basis_it_names(self):
		gross_based = row(
			deduction_category="Retirement 401k", amount_type="Percentage", amount=6, basis="Gross Pay"
		)
		self.assertEqual(pd.scheduled_amount(gross_based, 2000.0, 1500.0)["requested"], 120.0)

		net_based = row(
			deduction_category="Union Dues", amount_type="Percentage", amount=2, basis="Net After Tax"
		)
		self.assertEqual(pd.scheduled_amount(net_based, 2000.0, 1500.0)["requested"], 30.0)

	def test_a_percentage_defaults_to_the_basis_its_kind_is_written_against(self):
		"""Court orders are written against disposable earnings; a deferral rate
		is written against gross."""
		garnishment = row(deduction_category="Wage Garnishment", amount_type="Percentage", amount=10)
		self.assertEqual(pd.scheduled_amount(garnishment, 2000.0, 1000.0)["requested"], 100.0)

		election = row(deduction_category="Retirement 401k", amount_type="Percentage", amount=10)
		self.assertEqual(pd.scheduled_amount(election, 2000.0, 1000.0)["requested"], 200.0)

	def test_max_per_period_caps_a_percentage(self):
		capped = row(
			deduction_category="Retirement 401k",
			amount_type="Percentage",
			amount=10,
			basis="Gross Pay",
			max_per_period=150,
		)
		schedule = pd.scheduled_amount(capped, 2000.0, 2000.0)
		self.assertEqual(schedule["requested"], 150.0)
		self.assertEqual(schedule["capped_by"], "max_per_period")

	def test_a_zero_cap_is_no_cap_rather_than_a_cap_of_nothing(self):
		"""Currency fields default to 0 on every row, so a zero is a field
		nobody filled in."""
		uncapped = row(deduction_category="Union Dues", amount=40, max_per_period=0)
		self.assertEqual(pd.scheduled_amount(uncapped, 2000.0, 2000.0)["requested"], 40.0)

	def test_pre_tax_elections_cannot_drive_the_wage_base_negative(self):
		rows = pd.active_deductions([row(deduction_category="Health Insurance", amount=5000)])
		result = pd.calculate_pre_tax_deductions(2000.0, rows)
		self.assertEqual(result["federal_taxable_gross"], 0.0)
		self.assertEqual(result["total"], 2000.0)
		self.assertTrue(result["shortfalls"])

	def test_the_category_table_is_internally_consistent(self):
		"""Every category has the four keys the engine switches on, and the
		derived lists cannot drift from the table they come from."""
		for name, spec in pd.CATEGORY_SPECS.items():
			with self.subTest(category=name):
				for key in ("label", "deduction_type", "priority", "pre_tax", "fica_exempt", "limit"):
					self.assertIn(key, spec)
				self.assertIn(spec["deduction_type"], pd.DEDUCTION_TYPES)
				# Only a pre-tax deduction can be FICA-exempt.
				if spec["fica_exempt"]:
					self.assertTrue(spec["pre_tax"], f"{name} is FICA-exempt but not pre-tax")
		self.assertEqual(
			set(pd.GARNISHMENT_CATEGORIES) | set(pd.VOLUNTARY_CATEGORIES),
			set(pd.CATEGORIES),
		)
		self.assertEqual(set(pd.GARNISHMENT_CATEGORIES) & set(pd.VOLUNTARY_CATEGORIES), set())
