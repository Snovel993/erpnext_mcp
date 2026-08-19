# SPDX-License-Identifier: MIT
"""Tax Form Generators — v0.34.0.

TWELVE CLAIMS.

 1. `PeriodHelpers` — quarters, years and due dates resolve correctly, Q4 into January.
 2. `W2Boxes` — every W-2 box computes, the SS wage base caps box 3, and box 14 carries the state levies.
 3. `W2StateBoxes` — boxes 15 to 17 are per state, and only income tax lands in 17.
 4. `NEC1099` — box 1 totals the payments, box 4 the backup withholding, and the threshold decides reportability.
 5. `Form941` — the quarter aggregates across employees, and lines 1 through 15 add up.
 6. `Form941WageBase` — the Social Security base is consumed per employee, not against the company total.
 7. `OregonQuarterly` — OQ carries Paid Leave, the Transit Tax and per-month employee counts.
 8. `OregonAnnual` — OR-WR buckets the year by quarter and reconciles against what was filed.
 9. `WashingtonQuarterly` — WA ESD carries hours, PFML, WA Cares and the UI wage base.
10. `TaxFormTools` — the generate/list/get tools work end to end against seeded payroll.
11. `TaxFormLifecycle` — Draft to Generated to Filed, and regeneration reports what moved.
12. `TaxFormRefusals` — the settings gates, the duplicate guard, and every argument check.
"""

import json

import frappe

from erpnext_mcp.form_generators import (
	FORM_TYPES,
	generate_941_data,
	generate_1099_nec_data,
	generate_or_oq_data,
	generate_or_wr_data,
	generate_w2_data,
	generate_wa_esd_data,
	quarter_due_date,
	quarter_of_date,
	quarter_period,
	year_period,
)

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import STORE

TAX_FORM_TOOLS_ON = {
	f"allow_{name}": 1
	for name in (
		"list_tax_forms",
		"get_tax_form",
		"generate_tax_form",
		"regenerate_tax_form",
		"mark_tax_form_filed",
	)
}

#: What the Oregon engine returns on a slip, at the rates in `_or_config`.
OR_DETAIL = {
	"or_income_tax": 60.0,
	"or_transit_tax": 1.0,
	"or_paid_leave_employee": 6.0,
	"or_paid_leave_employer": 4.0,
	"or_workers_comp": 15.0,
	"total_or_employee": 67.0,
	"total_or_employer": 19.0,
}

#: What the Washington engine returns on a slip.
WA_DETAIL = {
	"wa_pfml_employee": 6.69,
	"wa_pfml_employer": 2.51,
	"wa_cares_employee": 5.80,
	"wa_li_employee": 0.50,
	"wa_li_employer": 1.50,
	"total_wa_employee": 12.99,
	"total_wa_employer": 4.01,
}


def or_slip(employee="E1", gross=1000.0, period_end="2025-02-14", **overrides):
	"""One Oregon slip at round numbers, so a box value can be read by eye."""
	slip = {
		"employee": employee,
		"employee_name": f"Worker {employee}",
		"work_state": "OR",
		"gross_pay": gross,
		"federal_withholding": 100.0,
		"state_withholding": 67.0,
		"social_security": round(gross * 0.062, 2),
		"medicare": round(gross * 0.0145, 2),
		"total_hours": 80.0,
		"period_start": "2025-02-01",
		"period_end": period_end,
		"state_taxes_detail": {"OR": dict(OR_DETAIL)},
	}
	slip.update(overrides)
	return slip


def wa_slip(employee="E1", gross=1000.0, period_end="2025-02-14", **overrides):
	slip = {
		"employee": employee,
		"employee_name": f"Worker {employee}",
		"work_state": "WA",
		"gross_pay": gross,
		"federal_withholding": 100.0,
		"state_withholding": 12.99,
		"social_security": round(gross * 0.062, 2),
		"medicare": round(gross * 0.0145, 2),
		"total_hours": 80.0,
		"period_start": "2025-02-01",
		"period_end": period_end,
		"state_taxes_detail": {"WA": dict(WA_DETAIL)},
	}
	slip.update(overrides)
	return slip


COMPANY_INFO = {
	"name": "Example Trading Co",
	"ein": "12-3456789",
	"address": "1 Orchard Road, Hood River OR 97031",
	"state_ids": {"OR": "1234567-8", "WA": "000123456"},
}


# ── Claim 1: period helpers ───────────────────────────────────────────────


class PeriodHelpers(V12TestCase):
	"""Quarters, years and due dates."""

	def test_quarter_periods(self):
		self.assertEqual(quarter_period("Q1", 2025), ("2025-01-01", "2025-03-31"))
		self.assertEqual(quarter_period("Q2", 2025), ("2025-04-01", "2025-06-30"))
		self.assertEqual(quarter_period("Q3", 2025), ("2025-07-01", "2025-09-30"))
		self.assertEqual(quarter_period("Q4", 2025), ("2025-10-01", "2025-12-31"))

	def test_q1_end_moves_in_a_leap_year(self):
		"""February's last day is computed, not assumed."""
		self.assertEqual(quarter_period("Q1", 2024), ("2024-01-01", "2024-03-31"))
		self.assertEqual(quarter_period("Q2", 2024)[0], "2024-04-01")

	def test_year_period(self):
		self.assertEqual(year_period(2025), ("2025-01-01", "2025-12-31"))

	def test_due_dates(self):
		self.assertEqual(quarter_due_date("Q1", 2025), "2025-04-30")
		self.assertEqual(quarter_due_date("Q2", 2025), "2025-07-31")
		self.assertEqual(quarter_due_date("Q3", 2025), "2025-10-31")

	def test_q4_is_due_in_the_following_january(self):
		"""The one due date a hand-written table gets wrong."""
		self.assertEqual(quarter_due_date("Q4", 2025), "2026-01-31")

	def test_quarter_of_date(self):
		self.assertEqual(quarter_of_date("2025-01-15"), "Q1")
		self.assertEqual(quarter_of_date("2025-06-30"), "Q2")
		self.assertEqual(quarter_of_date("2025-12-31"), "Q4")

	def test_quarter_of_an_unreadable_date_is_none(self):
		self.assertIsNone(quarter_of_date(""))
		self.assertIsNone(quarter_of_date(None))
		self.assertIsNone(quarter_of_date("not-a-date"))

	def test_a_bad_quarter_is_refused_by_name(self):
		with self.assertRaises(ValueError) as caught:
			quarter_period("Q5", 2025)
		self.assertIn("Q1", str(caught.exception))

	def test_every_form_type_declares_its_period_and_scope(self):
		for name, spec in FORM_TYPES.items():
			with self.subTest(form=name):
				self.assertIn(spec["period"], ("year", "quarter"))
				self.assertIn(spec["scope"], ("employee", "company"))


# ── Claim 2: W-2 boxes ────────────────────────────────────────────────────


class W2Boxes(V12TestCase):
	"""Every W-2 box computes from the year's slips."""

	def setUp(self):
		super().setUp()
		self.employee = {"employee": "E1", "employee_name": "Test Worker", "ssn_last4": "1234"}

	def test_wages_and_withholding(self):
		"""Twenty-six biweekly slips of $1,000 make a $26,000 W-2."""
		slips = [or_slip(gross=1000.0) for _ in range(26)]
		w2 = generate_w2_data(self.employee, slips, COMPANY_INFO, 2025)
		self.assertEqual(w2["box1_wages"], 26000.0)
		self.assertEqual(w2["box2_federal_income_tax_withheld"], 2600.0)
		self.assertEqual(w2["box3_social_security_wages"], 26000.0)
		self.assertEqual(w2["box4_social_security_tax_withheld"], 1612.0)
		self.assertEqual(w2["box5_medicare_wages"], 26000.0)
		self.assertEqual(w2["box6_medicare_tax_withheld"], 377.0)
		self.assertEqual(w2["slip_count"], 26)

	def test_medicare_wages_are_not_capped(self):
		"""Box 5 has no ceiling — Medicare applies to every dollar."""
		slips = [or_slip(gross=100000.0, social_security=6200.0, medicare=1450.0) for _ in range(3)]
		w2 = generate_w2_data(self.employee, slips, COMPANY_INFO, 2025)
		self.assertEqual(w2["box1_wages"], 300000.0)
		self.assertEqual(w2["box5_medicare_wages"], 300000.0)

	def test_social_security_wages_are_capped_at_the_wage_base(self):
		"""Box 3 stops at the base; box 1 and box 5 do not."""
		slips = [or_slip(gross=100000.0, social_security=6200.0, medicare=1450.0) for _ in range(3)]
		w2 = generate_w2_data(self.employee, slips, COMPANY_INFO, 2025)
		self.assertEqual(w2["box3_social_security_wages"], 176100.0)
		self.assertTrue(any("wage base" in w for w in w2["warnings"]))

	def test_the_wage_base_is_overridable(self):
		slips = [or_slip(gross=100000.0)]
		info = {**COMPANY_INFO, "ss_wage_base": 50000.0}
		w2 = generate_w2_data(self.employee, slips, info, 2025)
		self.assertEqual(w2["box3_social_security_wages"], 50000.0)

	def test_overtime_is_already_in_the_gross(self):
		"""A W-2 has no overtime box. OT is wages, and lands in box 1."""
		regular = or_slip(gross=800.0)
		with_ot = or_slip(gross=950.0)
		w2 = generate_w2_data(self.employee, [regular, with_ot], COMPANY_INFO, 2025)
		self.assertEqual(w2["box1_wages"], 1750.0)

	def test_box14_carries_the_state_levies_that_are_not_income_tax(self):
		"""ORSTT and ORPFML belong in box 14, not box 17."""
		slips = [or_slip(gross=1000.0) for _ in range(4)]
		w2 = generate_w2_data(self.employee, slips, COMPANY_INFO, 2025)
		codes = {item["code"]: item["amount"] for item in w2["box14_other"]}
		self.assertEqual(codes["ORSTT W/H"], 4.0)
		self.assertEqual(codes["ORPFML"], 24.0)

	def test_washington_box14_codes(self):
		slips = [wa_slip(gross=1000.0) for _ in range(2)]
		w2 = generate_w2_data(self.employee, slips, COMPANY_INFO, 2025)
		codes = {item["code"]: item["amount"] for item in w2["box14_other"]}
		self.assertEqual(codes["WAPFML"], 13.38)
		self.assertEqual(codes["WACARES"], 11.60)
		self.assertEqual(codes["WALI"], 1.0)

	def test_a_zero_levy_does_not_get_a_box14_line(self):
		"""An empty line on box 14 is a question somebody has to answer."""
		slip = or_slip(state_taxes_detail={"OR": {**OR_DETAIL, "or_transit_tax": 0.0}})
		w2 = generate_w2_data(self.employee, [slip], COMPANY_INFO, 2025)
		self.assertNotIn("ORSTT W/H", {i["code"] for i in w2["box14_other"]})

	def test_the_employer_and_employee_blocks(self):
		w2 = generate_w2_data(self.employee, [or_slip()], COMPANY_INFO, 2025)
		self.assertEqual(w2["employer"]["ein"], "12-3456789")
		self.assertEqual(w2["employee"]["ssn_display"], "XXX-XX-1234")
		self.assertEqual(w2["period_start"], "2025-01-01")
		self.assertEqual(w2["period_end"], "2025-12-31")

	def test_a_missing_ssn_prints_a_blank_rather_than_a_guess(self):
		w2 = generate_w2_data({"employee": "E1", "employee_name": "X"}, [or_slip()], COMPANY_INFO, 2025)
		self.assertIn("no SSN on file", w2["employee"]["ssn_display"])

	def test_no_slips_gives_a_zero_form_and_says_so(self):
		w2 = generate_w2_data(self.employee, [], COMPANY_INFO, 2025)
		self.assertEqual(w2["box1_wages"], 0.0)
		self.assertEqual(w2["state_boxes"], [])
		self.assertTrue(any("no payroll slips" in w for w in w2["warnings"]))

	def test_withholding_that_disagrees_with_the_statutory_rate_is_flagged(self):
		"""6.2% of box 3 against box 4 — the reconciliation a reviewer does first."""
		slip = or_slip(gross=10000.0, social_security=100.0)
		w2 = generate_w2_data(self.employee, [slip], COMPANY_INFO, 2025)
		self.assertTrue(any("box 4" in w for w in w2["warnings"]))

	def test_a_correct_form_carries_no_arithmetic_warning(self):
		slips = [or_slip(gross=1000.0) for _ in range(10)]
		w2 = generate_w2_data(self.employee, slips, COMPANY_INFO, 2025)
		self.assertFalse([w for w in w2["warnings"] if "box 4" in w or "box 6" in w])


# ── Claim 3: W-2 state boxes ──────────────────────────────────────────────


class W2StateBoxes(V12TestCase):
	"""Boxes 15 to 17, one row per state."""

	def setUp(self):
		super().setUp()
		self.employee = {"employee": "E1", "employee_name": "Test Worker", "ssn_last4": "1234"}

	def test_one_state_gives_one_row(self):
		w2 = generate_w2_data(self.employee, [or_slip(gross=1000.0)], COMPANY_INFO, 2025)
		self.assertEqual(len(w2["state_boxes"]), 1)
		row = w2["state_boxes"][0]
		self.assertEqual(row["box15_state"], "OR")
		self.assertEqual(row["box15_employer_state_id"], "1234567-8")
		self.assertEqual(row["box16_state_wages"], 1000.0)
		self.assertEqual(row["box17_state_income_tax"], 60.0)

	def test_washington_has_wages_and_no_income_tax(self):
		"""No income tax in the state means box 17 is zero, not blank-with-a-number."""
		w2 = generate_w2_data(self.employee, [wa_slip(gross=1000.0)], COMPANY_INFO, 2025)
		row = w2["state_boxes"][0]
		self.assertEqual(row["box15_state"], "WA")
		self.assertEqual(row["box16_state_wages"], 1000.0)
		self.assertEqual(row["box17_state_income_tax"], 0.0)

	def test_a_worker_in_both_states_gets_two_rows(self):
		slips = [or_slip(gross=1000.0), wa_slip(gross=600.0)]
		w2 = generate_w2_data(self.employee, slips, COMPANY_INFO, 2025)
		by_state = {r["box15_state"]: r for r in w2["state_boxes"]}
		self.assertEqual(set(by_state), {"OR", "WA"})
		self.assertEqual(by_state["OR"]["box16_state_wages"], 1000.0)
		self.assertEqual(by_state["WA"]["box16_state_wages"], 600.0)
		self.assertEqual(by_state["OR"]["box17_state_income_tax"], 60.0)

	def test_an_explicit_allocation_is_used_over_the_work_state(self):
		"""A cross-state pay period splits gross; the slip says how."""
		slip = or_slip(
			gross=1000.0,
			state_wages={"OR": 700.0, "WA": 300.0},
			state_taxes_detail={"OR": dict(OR_DETAIL), "WA": dict(WA_DETAIL)},
		)
		w2 = generate_w2_data(self.employee, [slip], COMPANY_INFO, 2025)
		by_state = {r["box15_state"]: r["box16_state_wages"] for r in w2["state_boxes"]}
		self.assertEqual(by_state["OR"], 700.0)
		self.assertEqual(by_state["WA"], 300.0)

	def test_a_cross_state_slip_with_no_allocation_is_flagged(self):
		"""Two engines ran and nothing said how the gross divided."""
		slip = or_slip(
			gross=1000.0,
			state_taxes_detail={"OR": dict(OR_DETAIL), "WA": dict(WA_DETAIL)},
		)
		w2 = generate_w2_data(self.employee, [slip], COMPANY_INFO, 2025)
		self.assertTrue(any("state_wages" in w for w in w2["warnings"]))

	def test_a_single_state_slip_raises_no_allocation_warning(self):
		"""There is nothing to get wrong, so there is nothing to warn about."""
		w2 = generate_w2_data(self.employee, [or_slip()], COMPANY_INFO, 2025)
		self.assertFalse([w for w in w2["warnings"] if "state_wages" in w])

	def test_a_missing_employer_state_id_is_flagged(self):
		info = {**COMPANY_INFO, "state_ids": {}}
		w2 = generate_w2_data(self.employee, [or_slip()], info, 2025)
		self.assertTrue(any("employer state ID" in w for w in w2["warnings"]))


# ── Claim 4: 1099-NEC ─────────────────────────────────────────────────────


class NEC1099(V12TestCase):
	"""Box 1, box 4, and the reporting threshold."""

	def setUp(self):
		super().setUp()
		self.contractor = {
			"party": "RP-0001",
			"party_name": "Pruning Crew LLC",
			"party_type": "Supplier",
			"tin_last4": "4321",
		}

	def test_box1_totals_the_payments(self):
		payments = [{"amount": 1500.0}, {"amount": 2500.0}, {"amount": 1000.0}]
		form = generate_1099_nec_data(self.contractor, payments, COMPANY_INFO, 2025)
		self.assertEqual(form["box1_nonemployee_compensation"], 5000.0)
		self.assertEqual(form["payment_count"], 3)

	def test_box4_carries_backup_withholding(self):
		payments = [{"amount": 1000.0, "federal_withholding": 240.0}]
		form = generate_1099_nec_data(self.contractor, payments, COMPANY_INFO, 2025)
		self.assertEqual(form["box4_federal_income_tax_withheld"], 240.0)

	def test_over_the_threshold_is_reportable(self):
		form = generate_1099_nec_data(self.contractor, [{"amount": 600.0}], COMPANY_INFO, 2025)
		self.assertTrue(form["reportable"])
		self.assertEqual(form["reporting_threshold"], 600.0)

	def test_under_the_threshold_is_computed_and_not_reportable(self):
		"""The figures still come out — an employer deciding not to file wants the total."""
		form = generate_1099_nec_data(self.contractor, [{"amount": 599.99}], COMPANY_INFO, 2025)
		self.assertFalse(form["reportable"])
		self.assertEqual(form["box1_nonemployee_compensation"], 599.99)
		self.assertTrue(any("threshold" in w for w in form["warnings"]))

	def test_backup_withholding_makes_a_small_payment_reportable(self):
		"""Tax was withheld, so the IRS is owed a form whatever the total."""
		payments = [{"amount": 100.0, "federal_withholding": 24.0}]
		form = generate_1099_nec_data(self.contractor, payments, COMPANY_INFO, 2025)
		self.assertTrue(form["reportable"])

	def test_state_boxes_are_per_state(self):
		payments = [
			{"amount": 1000.0, "state": "OR", "state_withholding": 50.0},
			{"amount": 500.0, "state": "WA"},
		]
		form = generate_1099_nec_data(self.contractor, payments, COMPANY_INFO, 2025)
		by_state = {r["box6_state"]: r for r in form["state_boxes"]}
		self.assertEqual(by_state["OR"]["box7_state_income"], 1000.0)
		self.assertEqual(by_state["OR"]["box5_state_tax_withheld"], 50.0)
		self.assertEqual(by_state["WA"]["box7_state_income"], 500.0)

	def test_the_tin_is_four_digits_and_never_nine(self):
		form = generate_1099_nec_data(self.contractor, [{"amount": 1000.0}], COMPANY_INFO, 2025)
		self.assertEqual(form["recipient"]["tin_display"], "XXX-XX-4321")

	def test_no_tin_says_get_the_w9(self):
		form = generate_1099_nec_data({"party": "RP-2"}, [{"amount": 1000.0}], COMPANY_INFO, 2025)
		self.assertIn("W-9", form["recipient"]["tin_display"])
		self.assertTrue(any("W-9" in w for w in form["warnings"]))

	def test_the_threshold_is_overridable(self):
		info = {**COMPANY_INFO, "nec_threshold": 2000.0}
		form = generate_1099_nec_data(self.contractor, [{"amount": 1000.0}], info, 2025)
		self.assertFalse(form["reportable"])
		self.assertEqual(form["reporting_threshold"], 2000.0)


# ── Claim 5: Form 941 ─────────────────────────────────────────────────────


class Form941(V12TestCase):
	"""Lines 1 through 15, aggregated across every employee in the quarter."""

	def test_the_quarter_aggregates_across_employees(self):
		slips = [
			or_slip(employee="E1", gross=1000.0),
			or_slip(employee="E2", gross=2000.0, social_security=124.0, medicare=29.0),
			or_slip(employee="E1", gross=1000.0, period_end="2025-03-14"),
		]
		form = generate_941_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line1_number_of_employees"], 2)
		self.assertEqual(form["line2_wages_tips_other_compensation"], 4000.0)
		self.assertEqual(form["line3_federal_income_tax_withheld"], 300.0)
		self.assertEqual(form["slip_count"], 3)

	def test_line_5a_is_social_security_at_the_combined_rate(self):
		"""12.4%, because line 5 is the employee and employer halves together."""
		form = generate_941_data([or_slip(gross=10000.0, social_security=620.0)], COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line5a_taxable_social_security_wages"], 10000.0)
		self.assertEqual(form["line5a_tax"], 1240.0)

	def test_line_5c_is_medicare_at_the_combined_rate(self):
		form = generate_941_data([or_slip(gross=10000.0, medicare=145.0)], COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line5c_taxable_medicare_wages"], 10000.0)
		self.assertEqual(form["line5c_tax"], 290.0)

	def test_line_5e_is_the_sum_of_5a_through_5d(self):
		form = generate_941_data(
			[or_slip(gross=10000.0, social_security=620.0, medicare=145.0)], COMPANY_INFO, "Q1", 2025
		)
		self.assertEqual(
			form["line5e_total_social_security_and_medicare"],
			round(form["line5a_tax"] + form["line5b_tax"] + form["line5c_tax"] + form["line5d_tax"], 2),
		)

	def test_line_6_is_withholding_plus_5e(self):
		slips = [or_slip(gross=10000.0, federal_withholding=1000.0, social_security=620.0, medicare=145.0)]
		form = generate_941_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line6_total_taxes_before_adjustments"], 1000.0 + 1530.0)

	def test_line_7_is_the_fractions_of_cents_difference(self):
		"""What was actually withheld and matched, against the form's own rates."""
		slips = [or_slip(gross=10000.0, social_security=620.0, medicare=145.0)]
		form = generate_941_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line7_fractions_of_cents_adjustment"], 0.0)

		off_by_a_cent = [or_slip(gross=10000.0, social_security=620.01, medicare=145.0)]
		form = generate_941_data(off_by_a_cent, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line7_fractions_of_cents_adjustment"], 0.02)

	def test_line_5d_needs_the_surcharge_stored_apart(self):
		slips = [or_slip(gross=250000.0, additional_medicare=450.0, social_security=15500.0, medicare=4075.0)]
		form = generate_941_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line5d_tax"], 450.0)
		self.assertEqual(form["line5d_wages_subject_to_additional_medicare"], 50000.0)

	def test_without_it_line_5d_is_zero_and_says_why(self):
		form = generate_941_data([or_slip(gross=10000.0)], COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line5d_tax"], 0.0)
		self.assertTrue(any("additional_medicare" in w for w in form["warnings"]))

	def test_deposits_reduce_the_balance_due(self):
		slips = [or_slip(gross=10000.0, federal_withholding=1000.0, social_security=620.0, medicare=145.0)]
		info = {**COMPANY_INFO, "deposits": 2530.0}
		form = generate_941_data(slips, info, "Q1", 2025)
		self.assertEqual(form["line13_total_deposits"], 2530.0)
		self.assertEqual(form["line14_balance_due"], 0.0)
		self.assertEqual(form["line15_overpayment"], 0.0)

	def test_an_overdeposit_shows_as_an_overpayment_not_a_negative_balance(self):
		slips = [or_slip(gross=10000.0, federal_withholding=1000.0, social_security=620.0, medicare=145.0)]
		info = {**COMPANY_INFO, "deposits": 3000.0}
		form = generate_941_data(slips, info, "Q1", 2025)
		self.assertEqual(form["line14_balance_due"], 0.0)
		self.assertEqual(form["line15_overpayment"], 470.0)

	def test_a_credit_reduces_line_12(self):
		slips = [or_slip(gross=10000.0, federal_withholding=1000.0, social_security=620.0, medicare=145.0)]
		info = {**COMPANY_INFO, "small_business_payroll_tax_credit": 500.0}
		form = generate_941_data(slips, info, "Q1", 2025)
		self.assertEqual(form["line11_small_business_payroll_tax_credit"], 500.0)
		self.assertEqual(
			form["line12_total_taxes_after_credits"], form["line10_total_taxes_after_adjustments"] - 500.0
		)

	def test_the_due_date_is_on_the_form(self):
		form = generate_941_data([or_slip()], COMPANY_INFO, "Q4", 2025)
		self.assertEqual(form["due_date"], "2026-01-31")

	def test_no_wages_ticks_line_4(self):
		form = generate_941_data([], COMPANY_INFO, "Q1", 2025)
		self.assertTrue(form["line4_no_wages_subject_to_ss_medicare"])
		self.assertEqual(form["line2_wages_tips_other_compensation"], 0.0)

	def test_a_missing_ein_is_flagged(self):
		info = {**COMPANY_INFO, "ein": ""}
		form = generate_941_data([or_slip()], info, "Q1", 2025)
		self.assertTrue(any("EIN" in w for w in form["warnings"]))


# ── Claim 6: the Social Security wage base on a 941 ───────────────────────


class Form941WageBase(V12TestCase):
	"""The base is an annual per-employee cap, and is consumed that way."""

	def test_the_cap_is_per_employee_not_per_company(self):
		"""Two employees at $100,000 each are $200,000 of SS wages, not $176,100."""
		slips = [
			or_slip(employee="E1", gross=100000.0, social_security=6200.0, medicare=1450.0),
			or_slip(employee="E2", gross=100000.0, social_security=6200.0, medicare=1450.0),
		]
		form = generate_941_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line5a_taxable_social_security_wages"], 200000.0)

	def test_one_employee_over_the_base_is_capped(self):
		slips = [or_slip(employee="E1", gross=200000.0, social_security=10918.2, medicare=2900.0)]
		form = generate_941_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["line5a_taxable_social_security_wages"], 176100.0)
		self.assertEqual(form["line2_wages_tips_other_compensation"], 200000.0)
		self.assertTrue(any("wage base" in w for w in form["warnings"]))

	def test_year_to_date_wages_consume_the_base(self):
		"""Q3 for somebody who already passed the base has zero taxable SS wages."""
		slips = [or_slip(employee="E1", gross=20000.0, social_security=0.0, medicare=290.0)]
		info = {**COMPANY_INFO, "ytd_wages_by_employee": {"E1": 176100.0}}
		form = generate_941_data(slips, info, "Q3", 2025)
		self.assertEqual(form["line5a_taxable_social_security_wages"], 0.0)
		self.assertEqual(form["line5c_taxable_medicare_wages"], 20000.0)

	def test_partial_headroom_is_used_exactly(self):
		slips = [or_slip(employee="E1", gross=20000.0, social_security=620.0, medicare=290.0)]
		info = {**COMPANY_INFO, "ytd_wages_by_employee": {"E1": 166100.0}}
		form = generate_941_data(slips, info, "Q3", 2025)
		self.assertEqual(form["line5a_taxable_social_security_wages"], 10000.0)

	def test_no_ytd_says_so(self):
		form = generate_941_data([or_slip(gross=1000.0)], COMPANY_INFO, "Q2", 2025)
		self.assertTrue(any("year-to-date" in w for w in form["warnings"]))


# ── Claim 7: Oregon Form OQ ───────────────────────────────────────────────


class OregonQuarterly(V12TestCase):
	"""Paid Leave, the Transit Tax, UI and the per-month employee counts."""

	def test_the_four_programs(self):
		slips = [or_slip(gross=1000.0) for _ in range(3)]
		form = generate_or_oq_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["subject_wages"], 3000.0)
		self.assertEqual(form["state_withholding"], 180.0)
		self.assertEqual(form["statewide_transit_tax"], 3.0)
		self.assertEqual(form["paid_leave_employee"], 18.0)
		self.assertEqual(form["paid_leave_employer"], 12.0)
		self.assertEqual(form["paid_leave_total"], 30.0)

	def test_ui_tax_is_the_assigned_rate_on_subject_wages(self):
		slips = [or_slip(gross=10000.0)]
		info = {**COMPANY_INFO, "ui_rate": 2.4}
		form = generate_or_oq_data(slips, info, "Q1", 2025)
		self.assertEqual(form["ui_rate"], 2.4)
		self.assertEqual(form["ui_tax"], 240.0)

	def test_the_ui_wage_base_caps_per_employee(self):
		slips = [
			or_slip(employee="E1", gross=60000.0),
			or_slip(employee="E2", gross=10000.0),
		]
		info = {**COMPANY_INFO, "ui_rate": 2.4, "or_ui_wage_base": 54300.0}
		form = generate_or_oq_data(slips, info, "Q1", 2025)
		self.assertEqual(form["ui_subject_wages"], 64300.0)
		self.assertTrue(any("UI wage base" in w for w in form["warnings"]))

	def test_no_ui_rate_is_zero_and_says_why(self):
		form = generate_or_oq_data([or_slip()], COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["ui_tax"], 0.0)
		self.assertTrue(any("unemployment-insurance rate" in w for w in form["warnings"]))

	def test_monthly_employee_counts(self):
		"""OQ asks how many people were paid in each of the quarter's three months."""
		slips = [
			or_slip(employee="E1", period_end="2025-01-15"),
			or_slip(employee="E2", period_end="2025-01-31"),
			or_slip(employee="E1", period_end="2025-02-15"),
			or_slip(employee="E1", period_end="2025-03-15"),
			or_slip(employee="E2", period_end="2025-03-31"),
			or_slip(employee="E3", period_end="2025-03-31"),
		]
		form = generate_or_oq_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["monthly_employee_counts"], {"January": 2, "February": 1, "March": 3})

	def test_a_person_paid_twice_in_a_month_counts_once(self):
		slips = [
			or_slip(employee="E1", period_end="2025-01-15"),
			or_slip(employee="E1", period_end="2025-01-31"),
		]
		form = generate_or_oq_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["monthly_employee_counts"]["January"], 1)

	def test_washington_slips_are_not_on_an_oregon_return(self):
		slips = [or_slip(gross=1000.0), wa_slip(gross=5000.0)]
		form = generate_or_oq_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["subject_wages"], 1000.0)
		self.assertEqual(form["slip_count"], 1)

	def test_total_due_adds_the_programs(self):
		slips = [or_slip(gross=1000.0)]
		info = {**COMPANY_INFO, "ui_rate": 2.4}
		form = generate_or_oq_data(slips, info, "Q1", 2025)
		self.assertEqual(form["total_due"], round(60.0 + 1.0 + 6.0 + 4.0 + 24.0, 2))

	def test_the_bin_and_the_due_date(self):
		form = generate_or_oq_data([or_slip()], COMPANY_INFO, "Q2", 2025)
		self.assertEqual(form["oregon_bin"], "1234567-8")
		self.assertEqual(form["due_date"], "2025-07-31")

	def test_hours_are_carried(self):
		slips = [or_slip(gross=1000.0, total_hours=80.0) for _ in range(3)]
		form = generate_or_oq_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["total_hours"], 240.0)


# ── Claim 8: Oregon Form OR-WR ────────────────────────────────────────────


class OregonAnnual(V12TestCase):
	"""The year bucketed by quarter, and reconciled against what was filed."""

	def _year_of_slips(self):
		return [
			or_slip(period_end="2025-02-14"),
			or_slip(period_end="2025-03-14"),
			or_slip(period_end="2025-05-16"),
			or_slip(period_end="2025-08-15"),
			or_slip(period_end="2025-11-14"),
		]

	def test_the_annual_total(self):
		form = generate_or_wr_data(self._year_of_slips(), COMPANY_INFO, 2025)
		self.assertEqual(form["annual"]["or_wages"], 5000.0)
		self.assertEqual(form["annual"]["or_income_tax"], 300.0)
		self.assertEqual(form["annual"]["or_transit_tax"], 5.0)
		self.assertEqual(form["annual"]["or_paid_leave_employee"], 30.0)

	def test_the_quarterly_buckets(self):
		form = generate_or_wr_data(self._year_of_slips(), COMPANY_INFO, 2025)
		self.assertEqual(form["by_quarter"]["Q1"]["or_wages"], 2000.0)
		self.assertEqual(form["by_quarter"]["Q2"]["or_wages"], 1000.0)
		self.assertEqual(form["by_quarter"]["Q3"]["or_wages"], 1000.0)
		self.assertEqual(form["by_quarter"]["Q4"]["or_wages"], 1000.0)

	def test_the_quarters_add_up_to_the_year(self):
		form = generate_or_wr_data(self._year_of_slips(), COMPANY_INFO, 2025)
		self.assertEqual(form["quarters_sum"]["or_wages"], form["annual"]["or_wages"])
		self.assertEqual(form["quarters_sum"]["or_income_tax"], form["annual"]["or_income_tax"])

	def test_a_matching_reconciliation(self):
		reported = {
			"Q1": {"or_income_tax": 120.0, "or_transit_tax": 2.0, "or_paid_leave_employee": 12.0},
			"Q2": {"or_income_tax": 60.0, "or_transit_tax": 1.0, "or_paid_leave_employee": 6.0},
			"Q3": {"or_income_tax": 60.0, "or_transit_tax": 1.0, "or_paid_leave_employee": 6.0},
			"Q4": {"or_income_tax": 60.0, "or_transit_tax": 1.0, "or_paid_leave_employee": 6.0},
		}
		info = {**COMPANY_INFO, "oq_reported": reported}
		form = generate_or_wr_data(self._year_of_slips(), info, 2025)
		self.assertTrue(form["reconciles"])
		self.assertEqual(form["reconciliation"]["or_income_tax"]["difference"], 0.0)

	def test_a_difference_is_named_and_measured(self):
		"""An OR-WR filed on an unexplained difference is an assessment letter."""
		reported = {"Q1": {"or_income_tax": 100.0}}
		info = {**COMPANY_INFO, "oq_reported": reported}
		form = generate_or_wr_data(self._year_of_slips(), info, 2025)
		self.assertFalse(form["reconciles"])
		self.assertEqual(form["reconciliation"]["or_income_tax"]["annual_total"], 300.0)
		self.assertEqual(form["reconciliation"]["or_income_tax"]["quarterly_filed"], 100.0)
		self.assertEqual(form["reconciliation"]["or_income_tax"]["difference"], 200.0)

	def test_with_nothing_filed_there_is_nothing_to_reconcile(self):
		form = generate_or_wr_data(self._year_of_slips(), COMPANY_INFO, 2025)
		self.assertIsNone(form["reconciles"])
		self.assertTrue(any("oq_reported" in w for w in form["warnings"]))

	def test_an_undated_slip_is_counted_annually_and_flagged(self):
		slips = [*self._year_of_slips(), or_slip(period_end="")]
		form = generate_or_wr_data(slips, COMPANY_INFO, 2025)
		self.assertEqual(form["annual"]["or_wages"], 6000.0)
		self.assertEqual(form["quarters_sum"]["or_wages"], 5000.0)
		self.assertTrue(any("no readable period_end" in w for w in form["warnings"]))

	def test_the_due_date_is_the_following_january(self):
		form = generate_or_wr_data([], COMPANY_INFO, 2025)
		self.assertEqual(form["due_date"], "2026-01-31")

	def test_employee_count(self):
		slips = [or_slip(employee="E1"), or_slip(employee="E2"), or_slip(employee="E1")]
		form = generate_or_wr_data(slips, COMPANY_INFO, 2025)
		self.assertEqual(form["employee_count"], 2)


# ── Claim 9: Washington ESD ───────────────────────────────────────────────


class WashingtonQuarterly(V12TestCase):
	"""Hours, PFML, WA Cares and the UI taxable wage base."""

	def test_per_employee_wage_and_hour_detail(self):
		slips = [
			wa_slip(employee="E1", gross=1000.0, total_hours=80.0),
			wa_slip(employee="E1", gross=1200.0, total_hours=90.0),
			wa_slip(employee="E2", gross=800.0, total_hours=60.0),
		]
		info = {**COMPANY_INFO, "ssn_last4_by_employee": {"E1": "1111", "E2": "2222"}}
		form = generate_wa_esd_data(slips, info, "Q1", 2025)
		rows = {r["employee"]: r for r in form["employees"]}
		self.assertEqual(rows["E1"]["wages"], 2200.0)
		self.assertEqual(rows["E1"]["hours"], 170)
		self.assertEqual(rows["E2"]["wages"], 800.0)
		self.assertEqual(form["employee_count"], 2)
		self.assertEqual(form["total_wages"], 3000.0)
		self.assertEqual(form["total_hours"], 230)

	def test_hours_are_whole_and_rounded_down(self):
		"""Rounding an hour up manufactures benefit eligibility."""
		slips = [wa_slip(employee="E1", gross=1000.0, total_hours=79.9)]
		form = generate_wa_esd_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["employees"][0]["hours"], 79)

	def test_pfml_and_wa_cares(self):
		slips = [wa_slip(gross=1000.0) for _ in range(2)]
		form = generate_wa_esd_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["pfml_employee_premium"], 13.38)
		self.assertEqual(form["pfml_employer_premium"], 5.02)
		self.assertEqual(form["pfml_total_premium"], 18.40)
		self.assertEqual(form["wa_cares_employee_premium"], 11.60)

	def test_labor_and_industries_is_carried_separately(self):
		slips = [wa_slip(gross=1000.0)]
		form = generate_wa_esd_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["labor_and_industries_employee"], 0.50)
		self.assertEqual(form["labor_and_industries_employer"], 1.50)

	def test_the_ui_wage_base_produces_excess_wages(self):
		slips = [wa_slip(employee="E1", gross=80000.0, total_hours=500.0)]
		form = generate_wa_esd_data(slips, COMPANY_INFO, "Q1", 2025)
		row = form["employees"][0]
		self.assertEqual(row["wages"], 80000.0)
		self.assertEqual(row["taxable_wages"], 72800.0)
		self.assertEqual(row["excess_wages"], 7200.0)
		self.assertEqual(form["total_excess_wages"], 7200.0)

	def test_year_to_date_wages_consume_the_base(self):
		slips = [wa_slip(employee="E1", gross=10000.0, total_hours=500.0)]
		info = {**COMPANY_INFO, "ytd_wages_by_employee": {"E1": 72800.0}}
		form = generate_wa_esd_data(slips, info, "Q1", 2025)
		self.assertEqual(form["total_taxable_wages"], 0.0)
		self.assertEqual(form["total_excess_wages"], 10000.0)

	def test_ui_tax_is_on_taxable_wages_only(self):
		slips = [wa_slip(employee="E1", gross=80000.0, total_hours=500.0)]
		info = {**COMPANY_INFO, "ui_rate": 1.0}
		form = generate_wa_esd_data(slips, info, "Q1", 2025)
		self.assertEqual(form["ui_tax"], 728.0)

	def test_wages_with_no_hours_is_a_rejected_report(self):
		slips = [wa_slip(employee="E1", gross=1000.0, total_hours=0.0)]
		form = generate_wa_esd_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertTrue(any("hours worked per employee" in w for w in form["warnings"]))

	def test_a_missing_ssn_is_flagged_by_count(self):
		slips = [wa_slip(employee="E1"), wa_slip(employee="E2")]
		info = {**COMPANY_INFO, "ssn_last4_by_employee": {"E1": "1111"}}
		form = generate_wa_esd_data(slips, info, "Q1", 2025)
		self.assertTrue(any("no Social Security number" in w for w in form["warnings"]))
		rows = {r["employee"]: r for r in form["employees"]}
		self.assertEqual(rows["E1"]["ssn_display"], "XXX-XX-1111")
		self.assertIn("no SSN on file", rows["E2"]["ssn_display"])

	def test_oregon_slips_are_not_on_a_washington_return(self):
		slips = [wa_slip(gross=1000.0), or_slip(gross=9000.0)]
		form = generate_wa_esd_data(slips, COMPANY_INFO, "Q1", 2025)
		self.assertEqual(form["total_wages"], 1000.0)

	def test_total_due_adds_ui_pfml_and_cares(self):
		slips = [wa_slip(employee="E1", gross=1000.0)]
		info = {**COMPANY_INFO, "ui_rate": 1.0}
		form = generate_wa_esd_data(slips, info, "Q1", 2025)
		self.assertEqual(form["total_due"], round(10.0 + 6.69 + 2.51 + 5.80, 2))

	def test_the_esd_account_number_and_due_date(self):
		form = generate_wa_esd_data([wa_slip()], COMPANY_INFO, "Q3", 2025)
		self.assertEqual(form["esd_account_number"], "000123456")
		self.assertEqual(form["due_date"], "2025-10-31")


# ── The tool layer ────────────────────────────────────────────────────────


class TaxFormToolTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **TAX_FORM_TOOLS_ON)
		install_hrms()
		self._seed_employees()
		self._seed_state_configs()

	def _seed_employees(self):
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
					"employee_name": "Second Worker",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-01-15",
				},
			],
		)

	def _seed_state_configs(self):
		STORE.seed(
			"State Tax Configuration",
			[
				{
					"name": "STC-OR-2025",
					"company": MAIN,
					"state": "OR",
					"tax_year": 2025,
					"status": "Active",
					"employer_account_number": "1234567-8",
				},
				{
					"name": "STC-WA-2025",
					"company": MAIN,
					"state": "WA",
					"tax_year": 2025,
					"status": "Active",
					"employer_account_number": "000123456",
				},
			],
		)

	def seed_payroll(self, name, period_start, period_end, slips, status="Submitted", company=MAIN):
		"""One Farm Payroll Entry with its slips, as `calculate_payroll` leaves it."""
		rows = []
		for slip in slips:
			row = dict(slip)
			row["state_taxes_detail"] = json.dumps(slip.get("state_taxes_detail") or {})
			row.pop("period_start", None)
			row.pop("period_end", None)
			rows.append(row)
		STORE.seed(
			"Farm Payroll Entry",
			[
				{
					"name": name,
					"company": company,
					"pay_period_start": period_start,
					"pay_period_end": period_end,
					"pay_frequency": "Biweekly",
					"status": status,
					"total_gross": sum(s.get("gross_pay", 0) for s in slips),
					"total_deductions": 0,
					"total_net": 0,
					"employee_count": len({s.get("employee") for s in slips}),
					"slips": rows,
				}
			],
		)

	def seed_a_year(self, employee="HR-EMP-00001"):
		"""Four Oregon pay periods, one in each quarter."""
		for index, (start, end) in enumerate(
			(
				("2025-02-01", "2025-02-14"),
				("2025-05-01", "2025-05-16"),
				("2025-08-01", "2025-08-15"),
				("2025-11-01", "2025-11-14"),
			)
		):
			self.seed_payroll(
				f"PAY-2025-{index:04d}",
				start,
				end,
				[or_slip(employee=employee, gross=1000.0)],
			)


# ── Claim 10: the generate/list/get tools ─────────────────────────────────


class TaxFormTools(TaxFormToolTestCase):
	"""Generate, list and get, end to end against seeded payroll."""

	def test_generate_a_w2(self):
		self.seed_a_year()
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		self.assertEqual(data["status"], "Generated")
		self.assertEqual(data["slip_count"], 4)
		self.assertEqual(data["form_data"]["box1_wages"], 4000.0)
		self.assertEqual(data["period_start"], "2025-01-01")
		self.assertEqual(data["period_end"], "2025-12-31")

	def test_the_employer_state_id_comes_off_the_state_tax_configuration(self):
		self.seed_a_year()
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		self.assertEqual(data["form_data"]["state_boxes"][0]["box15_employer_state_id"], "1234567-8")

	def test_a_state_ids_argument_overrides_the_configuration(self):
		self.seed_a_year()
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
				"state_ids": {"OR": "9999999-9"},
			},
		)
		self.assertEqual(data["form_data"]["state_boxes"][0]["box15_employer_state_id"], "9999999-9")

	def test_generate_a_941(self):
		self.seed_payroll(
			"PAY-Q1-A",
			"2025-02-01",
			"2025-02-14",
			[
				or_slip(employee="HR-EMP-00001", gross=1000.0),
				or_slip(employee="HR-EMP-00002", gross=2000.0),
			],
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.assertEqual(data["form_data"]["line1_number_of_employees"], 2)
		self.assertEqual(data["form_data"]["line2_wages_tips_other_compensation"], 3000.0)
		self.assertEqual(data["quarter"], "Q1")

	def test_generate_an_oregon_oq(self):
		self.seed_payroll(
			"PAY-Q1-A", "2025-02-01", "2025-02-14", [or_slip(employee="HR-EMP-00001", gross=1000.0)]
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "OQ",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
				"ui_rate": 2.4,
			},
		)
		self.assertEqual(data["form_data"]["subject_wages"], 1000.0)
		self.assertEqual(data["form_data"]["ui_tax"], 24.0)
		self.assertEqual(data["form_data"]["oregon_bin"], "1234567-8")

	def test_generate_a_wa_esd(self):
		self.seed_payroll(
			"PAY-Q1-A",
			"2025-02-01",
			"2025-02-14",
			[wa_slip(employee="HR-EMP-00001", gross=1000.0, total_hours=80.0)],
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "WA-ESD",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.assertEqual(data["form_data"]["total_wages"], 1000.0)
		self.assertEqual(data["form_data"]["total_hours"], 80)
		self.assertEqual(data["form_data"]["esd_account_number"], "000123456")

	def test_generate_an_or_wr(self):
		self.seed_a_year()
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "OR-WR",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.assertEqual(data["form_data"]["annual"]["or_wages"], 4000.0)
		self.assertEqual(data["form_data"]["by_quarter"]["Q2"]["or_wages"], 1000.0)

	def test_only_the_named_employee_lands_on_a_w2(self):
		self.seed_payroll(
			"PAY-A",
			"2025-02-01",
			"2025-02-14",
			[
				or_slip(employee="HR-EMP-00001", gross=1000.0),
				or_slip(employee="HR-EMP-00002", gross=9000.0),
			],
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		self.assertEqual(data["form_data"]["box1_wages"], 1000.0)

	def test_a_draft_payroll_is_not_counted(self):
		"""A draft payroll has not been paid."""
		self.seed_payroll(
			"PAY-DRAFT",
			"2025-02-01",
			"2025-02-14",
			[or_slip(employee="HR-EMP-00001", gross=5000.0)],
			status="Draft",
		)
		self.seed_payroll(
			"PAY-REAL", "2025-03-01", "2025-03-14", [or_slip(employee="HR-EMP-00001", gross=1000.0)]
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		self.assertEqual(data["form_data"]["box1_wages"], 1000.0)

	def test_a_cancelled_payroll_is_not_counted(self):
		self.seed_payroll(
			"PAY-X",
			"2025-02-01",
			"2025-02-14",
			[or_slip(employee="HR-EMP-00001", gross=5000.0)],
			status="Cancelled",
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		self.assertEqual(data["form_data"]["box1_wages"], 0.0)

	def test_a_payroll_outside_the_period_is_not_counted(self):
		self.seed_payroll(
			"PAY-Q1", "2025-02-01", "2025-02-14", [or_slip(employee="HR-EMP-00001", gross=1000.0)]
		)
		self.seed_payroll(
			"PAY-Q2", "2025-05-01", "2025-05-16", [or_slip(employee="HR-EMP-00001", gross=7000.0)]
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.assertEqual(data["form_data"]["line2_wages_tips_other_compensation"], 1000.0)

	def test_another_company_s_payroll_is_not_counted(self):
		self.seed_payroll(
			"PAY-OTHER",
			"2025-02-01",
			"2025-02-14",
			[or_slip(employee="HR-EMP-00001", gross=9000.0)],
			company=OTHER,
		)
		self.seed_payroll(
			"PAY-MAIN", "2025-02-15", "2025-02-28", [or_slip(employee="HR-EMP-00001", gross=1000.0)]
		)
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.assertEqual(data["form_data"]["line2_wages_tips_other_compensation"], 1000.0)

	def test_get_reads_back_every_computed_value(self):
		self.seed_a_year()
		created = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		data = self.tool_data("get_tax_form", {"name": created["name"]})
		self.assertEqual(data["form_type"], "W-2")
		self.assertEqual(data["status"], "Generated")
		self.assertEqual(data["employee"], "HR-EMP-00001")
		self.assertEqual(data["form_data"]["box1_wages"], 4000.0)
		self.assertEqual(data["form_data"]["box2_federal_income_tax_withheld"], 400.0)

	def test_list_and_filter(self):
		self.seed_a_year()
		self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q2",
			},
		)

		self.assertEqual(self.tool_data("list_tax_forms", {})["count"], 3)
		self.assertEqual(self.tool_data("list_tax_forms", {"form_type": "941"})["count"], 2)
		self.assertEqual(self.tool_data("list_tax_forms", {"quarter": "Q1"})["count"], 1)
		self.assertEqual(
			self.tool_data("list_tax_forms", {"employee": "HR-EMP-00001"})["count"],
			1,
		)
		self.assertEqual(self.tool_data("list_tax_forms", {"fiscal_year": "2024"})["count"], 0)

	def test_list_counts_by_status(self):
		self.seed_a_year()
		created = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "OR-WR",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.tool_data("mark_tax_form_filed", {"name": created["name"]})
		data = self.tool_data("list_tax_forms", {})
		self.assertEqual(data["by_status"], {"Filed": 1})

	def test_a_generated_form_is_audited(self):
		self.seed_a_year()
		self.tool_data(
			"generate_tax_form",
			{
				"form_type": "OR-WR",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.assertAudited("generate_tax_form", "Success")


# ── Claim 11: the lifecycle ───────────────────────────────────────────────


class TaxFormLifecycle(TaxFormToolTestCase):
	"""Generated to Filed, and what regeneration reports."""

	def _a_941(self, gross=1000.0):
		self.seed_payroll(
			"PAY-Q1", "2025-02-01", "2025-02-14", [or_slip(employee="HR-EMP-00001", gross=gross)]
		)
		return self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)

	def test_a_new_form_is_generated_not_draft(self):
		self.assertEqual(self._a_941()["status"], "Generated")

	def test_mark_filed(self):
		created = self._a_941()
		data = self.tool_data(
			"mark_tax_form_filed",
			{
				"name": created["name"],
				"filed_date": "2025-04-25",
				"confirmation_number": "EFTPS-99887766",
			},
		)
		self.assertEqual(data["status"], "Filed")
		self.assertEqual(data["filed_date"], "2025-04-25")
		self.assertEqual(data["confirmation_number"], "EFTPS-99887766")

		read_back = self.tool_data("get_tax_form", {"name": created["name"]})
		self.assertEqual(read_back["status"], "Filed")
		self.assertEqual(read_back["confirmation_number"], "EFTPS-99887766")

	def test_filed_date_defaults_to_today(self):
		created = self._a_941()
		data = self.tool_data("mark_tax_form_filed", {"name": created["name"]})
		self.assertTrue(data["filed_date"])

	def test_filing_twice_is_refused(self):
		"""It would overwrite the record of the filing that actually happened."""
		created = self._a_941()
		self.tool_data("mark_tax_form_filed", {"name": created["name"]})
		error = self.tool_error("mark_tax_form_filed", {"name": created["name"]})
		self.assertIn("already Filed", error)

	def test_regeneration_reports_what_moved(self):
		created = self._a_941(gross=1000.0)
		self.assertEqual(created["form_data"]["line2_wages_tips_other_compensation"], 1000.0)

		# A correction lands: the same period, more wages.
		self.seed_payroll(
			"PAY-Q1-FIX", "2025-03-01", "2025-03-14", [or_slip(employee="HR-EMP-00001", gross=500.0)]
		)

		data = self.tool_data("regenerate_tax_form", {"name": created["name"]})
		self.assertTrue(data["changed"])
		moved = data["changes"]["line2_wages_tips_other_compensation"]
		self.assertEqual(moved["was"], 1000.0)
		self.assertEqual(moved["now"], 1500.0)
		self.assertEqual(moved["delta"], 500.0)

	def test_regeneration_with_no_change_says_so(self):
		created = self._a_941()
		data = self.tool_data("regenerate_tax_form", {"name": created["name"]})
		self.assertFalse(data["changed"])
		self.assertEqual(data["changes"], {})

	def test_regeneration_rewrites_the_stored_values(self):
		created = self._a_941(gross=1000.0)
		self.seed_payroll(
			"PAY-Q1-FIX", "2025-03-01", "2025-03-14", [or_slip(employee="HR-EMP-00001", gross=500.0)]
		)
		self.tool_data("regenerate_tax_form", {"name": created["name"]})
		read_back = self.tool_data("get_tax_form", {"name": created["name"]})
		self.assertEqual(read_back["form_data"]["line2_wages_tips_other_compensation"], 1500.0)

	def test_a_filed_form_will_not_regenerate_by_accident(self):
		"""It would replace the record of what was actually sent."""
		created = self._a_941()
		self.tool_data("mark_tax_form_filed", {"name": created["name"]})
		error = self.tool_error("regenerate_tax_form", {"name": created["name"]})
		self.assertIn("Filed", error)
		self.assertIn("allow_filed", error)

	def test_allow_filed_lets_it_through_and_keeps_the_status(self):
		created = self._a_941()
		self.tool_data("mark_tax_form_filed", {"name": created["name"]})
		self.tool_data("regenerate_tax_form", {"name": created["name"], "allow_filed": True})
		read_back = self.tool_data("get_tax_form", {"name": created["name"]})
		self.assertEqual(read_back["status"], "Filed")

	def test_an_amended_form_is_refused(self):
		created = self._a_941()
		frappe.db.set_value("Tax Form", created["name"], "status", "Amended")
		error = self.tool_error("regenerate_tax_form", {"name": created["name"]})
		self.assertIn("Amended", error)

	def test_an_amended_form_does_not_block_a_replacement(self):
		"""Superseding one form and generating its correction is the whole point."""
		created = self._a_941()
		frappe.db.set_value("Tax Form", created["name"], "status", "Amended")
		replacement = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.assertNotEqual(replacement["name"], created["name"])

	def test_get_returns_the_values_as_generated_not_as_recomputed(self):
		"""A filed form is a statement about a date, not a live query."""
		created = self._a_941(gross=1000.0)
		self.seed_payroll(
			"PAY-Q1-LATE", "2025-03-01", "2025-03-14", [or_slip(employee="HR-EMP-00001", gross=9000.0)]
		)
		read_back = self.tool_data("get_tax_form", {"name": created["name"]})
		self.assertEqual(read_back["form_data"]["line2_wages_tips_other_compensation"], 1000.0)


# ── Claim 12: refusals ────────────────────────────────────────────────────


class TaxFormRefusals(TaxFormToolTestCase):
	"""The settings gates and every argument check."""

	def test_each_mutating_tool_is_off_by_default(self):
		self.configure(enabled=1)
		for tool in ("generate_tax_form", "regenerate_tax_form", "mark_tax_form_filed"):
			with self.subTest(tool=tool):
				error = self.tool_error(tool, {"name": "TAXFRM-2025-0001"})
				self.assertIn("switched off", error.lower())

	def test_each_read_tool_is_on_by_default(self):
		self.configure(enabled=1)
		self.assertEqual(self.tool_data("list_tax_forms", {})["count"], 0)

	def test_a_read_tool_can_be_switched_off(self):
		self.configure(enabled=1, allow_list_tax_forms=0)
		error = self.tool_error("list_tax_forms", {})
		self.assertIn("switched off", error.lower())

	def test_get_can_be_switched_off(self):
		self.configure(enabled=1, allow_get_tax_form=0)
		error = self.tool_error("get_tax_form", {"name": "TAXFRM-2025-0001"})
		self.assertIn("switched off", error.lower())

	def test_an_unknown_form_type_is_refused_by_name(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "W-4",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.assertIn("form_type must be one of", error)
		self.assertIn("WA-ESD", error)

	def test_a_quarterly_form_needs_a_quarter(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.assertIn("quarter is required", error)

	def test_an_annual_form_refuses_a_quarter(self):
		"""Silently ignoring it would produce a year's figures under a quarter's label."""
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "OR-WR",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.assertIn("annual form", error)

	def test_a_bad_quarter_is_refused(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q5",
			},
		)
		self.assertIn("quarter must be one of", error)

	def test_a_missing_year_is_refused(self):
		error = self.tool_error("generate_tax_form", {"form_type": "OR-WR", "company": MAIN})
		self.assertIn("fiscal_year is required", error)

	def test_a_bad_year_is_refused(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "OR-WR",
				"company": MAIN,
				"fiscal_year": "twenty-five",
			},
		)
		self.assertIn("four-digit year", error)

	def test_a_w2_needs_an_employee(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.assertIn("employee is required", error)

	def test_an_unknown_employee_is_refused(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "Nobody At All",
			},
		)
		self.assertIn("no Employee", error)

	def test_a_1099_needs_a_related_party(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "1099-NEC",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.assertIn("related_party is required", error)

	def test_an_unknown_related_party_is_refused(self):
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "1099-NEC",
				"company": MAIN,
				"fiscal_year": "2025",
				"related_party": "RP-NOPE",
			},
		)
		self.assertIn("no Related Party", error)

	def test_a_duplicate_form_is_refused_and_points_at_the_original(self):
		self.seed_a_year()
		created = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "OR-WR",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "OR-WR",
				"company": MAIN,
				"fiscal_year": "2025",
			},
		)
		self.assertIn(created["name"], error)
		self.assertIn("regenerate_tax_form", error)

	def test_two_employees_get_their_own_w2s(self):
		"""The duplicate guard is per recipient, not per company and year."""
		self.seed_payroll(
			"PAY-A",
			"2025-02-01",
			"2025-02-14",
			[
				or_slip(employee="HR-EMP-00001", gross=1000.0),
				or_slip(employee="HR-EMP-00002", gross=2000.0),
			],
		)
		first = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00001",
			},
		)
		second = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": "HR-EMP-00002",
			},
		)
		self.assertNotEqual(first["name"], second["name"])
		self.assertEqual(second["form_data"]["box1_wages"], 2000.0)

	def test_each_quarter_gets_its_own_941(self):
		self.seed_a_year()
		for quarter in ("Q1", "Q2", "Q3", "Q4"):
			with self.subTest(quarter=quarter):
				self.tool_data(
					"generate_tax_form",
					{
						"form_type": "941",
						"company": MAIN,
						"fiscal_year": "2025",
						"quarter": quarter,
					},
				)
		self.assertEqual(self.tool_data("list_tax_forms", {"form_type": "941"})["count"], 4)

	def test_a_nonexistent_form_is_refused_cleanly(self):
		error = self.tool_error("get_tax_form", {"name": "TAXFRM-2025-9999"})
		self.assertIn("no Tax Form", error)

	def test_get_needs_a_name(self):
		error = self.tool_error("get_tax_form", {})
		self.assertIn("name", error)

	def test_a_bad_numeric_argument_is_refused_by_name(self):
		self.seed_a_year()
		error = self.tool_error(
			"generate_tax_form",
			{
				"form_type": "OQ",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
				"ui_rate": "two point four",
			},
		)
		self.assertIn("ui_rate must be a number", error)

	def test_generating_with_no_payroll_at_all_still_produces_a_form(self):
		"""A quarter with nobody on it is a return that has to be filed anyway."""
		data = self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": "Q1",
			},
		)
		self.assertEqual(data["slip_count"], 0)
		self.assertEqual(data["form_data"]["line2_wages_tips_other_compensation"], 0.0)
		self.assertTrue(data["form_data"]["line4_no_wages_subject_to_ss_medicare"])
