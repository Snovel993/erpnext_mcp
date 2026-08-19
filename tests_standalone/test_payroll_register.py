# SPDX-License-Identifier: MIT
"""The payroll register — v0.91.0.

SEVEN CLAIMS.

1. `RegisterColumns` — every column the register promises is present, per
   employee, and reads the figure the slip actually stored.
2. `RegisterTotals` — the totals row is the sum of the rows above it, and the
   employer block is summed separately from them.
3. `RegisterCostTotals` — the two cost totals are different numbers, and the
   difference between them is exactly the employees' withholding.
4. `RegisterAggregation` — an employee with slips on two runs in the window is
   ONE row with both periods in it, not two rows.
5. `RegisterWindow` — the window is on `pay_period_end`, a run is counted whole,
   and `pay_period` reads one named run whatever its status.
6. `RegisterStatuses` — Draft and Cancelled are out by default, `include_drafts`
   puts Draft back, and the result always says which were counted.
7. `RegisterRefusals` — the switch, the missing window, the reversed dates, the
   run belonging to another company, the entry cap.

WHY THE SLIPS ARE SEEDED RATHER THAN CALCULATED. This is a READ over stored
Farm Payroll Slips and nothing else — `test_payroll.py` is where the engine that
produced them is tested. Seeding the entries directly is what lets a column be
asserted against a number chosen by hand, so "gross is 1,000.00" is a claim
about the register and not a claim about the withholding tables.
"""

import unittest

from erpnext_mcp.tools import payroll

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import STORE

REGISTER_ON = {"allow_get_payroll_register": 1}


def slip(
	employee="HR-EMP-00001",
	name=None,
	gross=1000.0,
	federal=100.0,
	state=60.0,
	other=0.0,
	hours=80.0,
	units=0.0,
	**overrides,
):
	"""One Farm Payroll Slip child row at round numbers, so a column reads by eye.

	`other` is a deduction that is NOT one of the four named taxes — a
	garnishment, say — and it reaches the record the only way one can: inside
	`total_deductions`. That is what makes `other_deductions` a real derivation
	here rather than a column that is always zero.
	"""
	social_security = round(gross * 0.062, 2)
	medicare = round(gross * 0.0145, 2)
	total_deductions = round(federal + state + social_security + medicare + other, 2)
	row = {
		"employee": employee,
		"employee_name": name or f"Worker {employee[-1]}",
		"pay_type": "Hourly",
		"work_state": "OR",
		"total_hours": hours,
		"regular_hours": hours,
		"overtime_hours": 0.0,
		"piece_units": units,
		"piece_rate": 0.0,
		"gross_pay": gross,
		"earned_gross": gross,
		"minimum_wage_makeup": 0.0,
		"federal_withholding": federal,
		"state_withholding": state,
		"social_security": social_security,
		"medicare": medicare,
		"total_deductions": total_deductions,
		"net_pay": round(gross - total_deductions, 2),
		"social_security_employer": social_security,
		"medicare_employer": medicare,
		"futa": round(gross * 0.006, 2),
		"state_unemployment": round(gross * 0.02, 2),
		"state_employer_other": 5.0,
		"total_employer_taxes": round(social_security + medicare + gross * 0.006 + gross * 0.02 + 5.0, 2),
	}
	row.update(overrides)
	return row


def entry(name, start, end, slips, company=MAIN, status="Submitted"):
	"""One Farm Payroll Entry with its slips, totalled the way the tools do."""
	return {
		"name": name,
		"company": company,
		"pay_period_start": start,
		"pay_period_end": end,
		"pay_frequency": "Biweekly",
		"status": status,
		"total_gross": round(sum(row["gross_pay"] for row in slips), 2),
		"total_deductions": round(sum(row["total_deductions"] for row in slips), 2),
		"total_net": round(sum(row["net_pay"] for row in slips), 2),
		"employee_count": len(slips),
		"slips": slips,
	}


class RegisterTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **REGISTER_ON)

	def seed(self, *entries):
		STORE.seed("Farm Payroll Entry", list(entries))

	def two_runs(self):
		"""Two biweekly runs in June, two people on each."""
		self.seed(
			entry(
				"PAY-2026-0001",
				"2026-06-01",
				"2026-06-14",
				[
					slip("HR-EMP-00001", name="Maria Garcia"),
					slip("HR-EMP-00002", name="Ana Ruiz", gross=2000.0, federal=200.0, state=120.0),
				],
			),
			entry(
				"PAY-2026-0002",
				"2026-06-15",
				"2026-06-28",
				[
					slip("HR-EMP-00001", name="Maria Garcia", gross=1500.0, other=50.0),
				],
			),
		)

	def register(self, **arguments):
		return self.tool_data("get_payroll_register", {"company": MAIN, **arguments})


# ── Claim 1: the columns ──────────────────────────────────────────────────


class RegisterColumns(RegisterTestCase):
	"""Every promised column is present and carries what the slip stored."""

	def setUp(self):
		super().setUp()
		self.seed(
			entry(
				"PAY-2026-0001",
				"2026-06-01",
				"2026-06-14",
				[
					slip("HR-EMP-00001", name="Maria Garcia", other=75.0, units=42.0),
				],
			)
		)
		self.row = self.register(date_from="2026-06-01", date_to="2026-06-30")["employees"][0]

	def test_the_row_carries_every_column_the_tool_promises(self):
		for column in (
			"employee_id",
			"employee_name",
			"gross_pay",
			"federal_tax",
			"state_tax",
			"ss_employee",
			"medicare_employee",
			"other_deductions",
			"net_pay",
			"hours_worked",
			"piece_units",
		):
			with self.subTest(column=column):
				self.assertIn(column, self.row)

	def test_the_named_taxes_are_the_slip_columns_they_come_from(self):
		self.assertEqual(self.row["gross_pay"], 1000.0)
		self.assertEqual(self.row["federal_tax"], 100.0)
		self.assertEqual(self.row["state_tax"], 60.0)
		self.assertEqual(self.row["ss_employee"], 62.0)
		self.assertEqual(self.row["medicare_employee"], 14.5)

	def test_hours_and_units_come_across_as_worked(self):
		self.assertEqual(self.row["hours_worked"], 80.0)
		self.assertEqual(self.row["piece_units"], 42.0)

	def test_other_deductions_is_what_is_left_after_the_four_named_taxes(self):
		"""THE CLAIM THIS TOOL RESTS ON. Nothing on the slip names a garnishment,
		so a column that READ a field would report zero for one — which is how a
		register comes to disagree with the cheque that was actually written."""
		self.assertEqual(self.row["other_deductions"], 75.0)
		self.assertEqual(
			self.row["total_deductions"],
			self.row["federal_tax"]
			+ self.row["state_tax"]
			+ self.row["ss_employee"]
			+ self.row["medicare_employee"]
			+ self.row["other_deductions"],
		)

	def test_net_is_gross_less_total_deductions(self):
		self.assertEqual(
			round(self.row["gross_pay"] - self.row["total_deductions"], 2),
			self.row["net_pay"],
		)

	def test_the_derivation_is_stated_in_the_result_rather_than_left_to_be_guessed(self):
		data = self.register(date_from="2026-06-01", date_to="2026-06-30")
		self.assertIn("total_deductions", data["other_deductions_rule"])


# ── Claim 2: the totals ───────────────────────────────────────────────────


class RegisterTotals(RegisterTestCase):
	"""The totals row sums the rows above it; the employer block is its own."""

	def setUp(self):
		super().setUp()
		self.two_runs()
		self.data = self.register(date_from="2026-06-01", date_to="2026-06-30")

	def test_every_employee_column_totals_the_rows_above_it(self):
		for column in (
			"gross_pay",
			"federal_tax",
			"state_tax",
			"ss_employee",
			"medicare_employee",
			"other_deductions",
			"total_deductions",
			"net_pay",
			"hours_worked",
			"piece_units",
		):
			with self.subTest(column=column):
				self.assertAlmostEqual(
					self.data["totals"][column],
					round(sum(row[column] for row in self.data["employees"]), 2),
					places=2,
				)

	def test_the_totals_row_counts_the_people_and_the_periods(self):
		self.assertEqual(self.data["totals"]["employees"], 2)
		self.assertEqual(self.data["totals"]["periods"], 3)

	def test_the_employer_block_is_separate_from_the_employee_totals(self):
		"""None of it is deducted from anybody, so none of it is in a net."""
		costs = self.data["employer_costs"]
		for column in ("ss_employer", "medicare_employer", "futa", "suta", "state_employer_other"):
			with self.subTest(column=column):
				self.assertIn(column, costs)
				self.assertNotIn(column, self.data["totals"])
		self.assertAlmostEqual(
			costs["total_employer_taxes"],
			round(sum(value for key, value in costs.items() if key != "total_employer_taxes"), 2),
			places=2,
		)

	def test_the_employer_share_of_fica_matches_the_employees_on_this_fixture(self):
		"""6.2 and 1.45 either side, which is what makes a transposed column
		here visible rather than plausible."""
		self.assertAlmostEqual(
			self.data["employer_costs"]["ss_employer"],
			self.data["totals"]["ss_employee"],
			places=2,
		)
		self.assertAlmostEqual(
			self.data["employer_costs"]["medicare_employer"],
			self.data["totals"]["medicare_employee"],
			places=2,
		)

	def test_the_runs_it_added_up_are_named_so_a_disagreement_can_be_traced(self):
		names = [row["name"] for row in self.data["payroll_entries"]]
		self.assertEqual(names, ["PAY-2026-0001", "PAY-2026-0002"])
		self.assertEqual(self.data["payroll_entry_count"], 2)
		self.assertEqual([row["slips"] for row in self.data["payroll_entries"]], [2, 1])


# ── Claim 3: the two cost totals ──────────────────────────────────────────


class RegisterCostTotals(RegisterTestCase):
	"""Two different questions, two different numbers, and the gap named."""

	def setUp(self):
		super().setUp()
		self.two_runs()
		self.data = self.register(date_from="2026-06-01", date_to="2026-06-30")

	def test_grand_total_labor_cost_is_net_plus_every_employer_tax(self):
		self.assertAlmostEqual(
			self.data["grand_total_labor_cost"],
			round(
				self.data["totals"]["net_pay"] + self.data["employer_costs"]["total_employer_taxes"],
				2,
			),
			places=2,
		)

	def test_total_cost_of_employment_is_gross_plus_every_employer_tax(self):
		"""The money that actually leaves the farm: the withheld tax is the
		employer's to remit, so it leaves too — it is just not in anybody's net."""
		self.assertAlmostEqual(
			self.data["total_cost_of_employment"],
			round(
				self.data["totals"]["gross_pay"] + self.data["employer_costs"]["total_employer_taxes"],
				2,
			),
			places=2,
		)

	def test_the_two_differ_by_exactly_the_employees_withholding(self):
		"""Reported beside them, so the arithmetic can be checked on the face of
		the result rather than taken on trust."""
		self.assertAlmostEqual(
			self.data["total_cost_of_employment"] - self.data["grand_total_labor_cost"],
			self.data["total_employee_withholding"],
			places=2,
		)
		self.assertAlmostEqual(
			self.data["total_employee_withholding"],
			round(self.data["totals"]["gross_pay"] - self.data["totals"]["net_pay"], 2),
			places=2,
		)

	def test_the_larger_of_the_two_is_the_gross_based_one(self):
		"""Stated as its own claim because getting these the wrong way round
		would understate the cost of the crew by every dollar withheld."""
		self.assertGreater(
			self.data["total_cost_of_employment"],
			self.data["grand_total_labor_cost"],
		)


# ── Claim 4: one row per person ───────────────────────────────────────────


class RegisterAggregation(RegisterTestCase):
	"""Two runs, one person, one row — with both periods in it."""

	def setUp(self):
		super().setUp()
		self.two_runs()
		self.data = self.register(date_from="2026-06-01", date_to="2026-06-30")

	def test_a_person_on_two_runs_is_one_row(self):
		ids = [row["employee_id"] for row in self.data["employees"]]
		self.assertEqual(sorted(ids), ["HR-EMP-00001", "HR-EMP-00002"])
		self.assertEqual(len(ids), len(set(ids)))

	def test_that_row_sums_both_periods(self):
		maria = next(r for r in self.data["employees"] if r["employee_id"] == "HR-EMP-00001")
		self.assertEqual(maria["periods"], 2)
		self.assertEqual(maria["gross_pay"], 2500.0)
		self.assertEqual(maria["hours_worked"], 160.0)

	def test_a_garnishment_on_only_one_of_the_two_still_lands_in_other_deductions(self):
		maria = next(r for r in self.data["employees"] if r["employee_id"] == "HR-EMP-00001")
		self.assertEqual(maria["other_deductions"], 50.0)

	def test_rows_are_ordered_by_name_so_the_sheet_reads_the_same_way_twice(self):
		names = [row["employee_name"] for row in self.data["employees"]]
		self.assertEqual(names, sorted(names))


# ── Claim 5: the window ───────────────────────────────────────────────────


class RegisterWindow(RegisterTestCase):
	"""On `pay_period_end`, whole runs only, and one run by name."""

	def setUp(self):
		super().setUp()
		self.two_runs()

	def test_a_run_that_ended_outside_the_window_is_not_counted_at_all(self):
		"""Even though its days overlap: splitting a run would produce
		withholding totals that reconcile against no deposit ever made."""
		data = self.register(date_from="2026-06-01", date_to="2026-06-20")
		self.assertEqual([row["name"] for row in data["payroll_entries"]], ["PAY-2026-0001"])
		self.assertEqual(data["totals"]["gross_pay"], 3000.0)

	def test_the_window_rule_is_stated_in_the_result(self):
		data = self.register(date_from="2026-06-01", date_to="2026-06-20")
		self.assertIn("pay_period_end", data["window_rule"])

	def test_pay_period_names_one_run_and_takes_its_dates_from_it(self):
		data = self.register(pay_period="PAY-2026-0002")
		self.assertEqual(data["pay_period"], "PAY-2026-0002")
		self.assertEqual(data["date_from"], "2026-06-15")
		self.assertEqual(data["date_to"], "2026-06-28")
		self.assertEqual(data["payroll_entry_count"], 1)
		self.assertEqual(data["totals"]["gross_pay"], 1500.0)

	def test_the_date_aliases_all_reach_the_same_window(self):
		by_alias = self.register(from_date="2026-06-01", to_date="2026-06-20")
		by_name = self.register(date_from="2026-06-01", date_to="2026-06-20")
		self.assertEqual(by_alias["totals"], by_name["totals"])

	def test_a_run_in_another_company_is_not_in_this_company_register(self):
		self.seed(
			entry(
				"PAY-2026-0090",
				"2026-06-01",
				"2026-06-14",
				[
					slip("HR-EMP-00009", name="Someone Else", company=None),
				],
				company=OTHER,
			)
		)
		data = self.register(date_from="2026-06-01", date_to="2026-06-30")
		self.assertNotIn(
			"HR-EMP-00009",
			[row["employee_id"] for row in data["employees"]],
		)


# ── Claim 6: which statuses count ─────────────────────────────────────────


class RegisterStatuses(RegisterTestCase):
	"""A Draft has not been paid and a Cancelled one was not."""

	def setUp(self):
		super().setUp()
		self.seed(
			entry("PAY-2026-0001", "2026-06-01", "2026-06-14", [slip()], status="Submitted"),
			entry("PAY-2026-0002", "2026-06-15", "2026-06-28", [slip(gross=500.0)], status="Draft"),
			entry("PAY-2026-0003", "2026-06-15", "2026-06-28", [slip(gross=900.0)], status="Cancelled"),
		)

	def test_draft_and_cancelled_are_out_by_default(self):
		data = self.register(date_from="2026-06-01", date_to="2026-06-30")
		self.assertEqual([row["name"] for row in data["payroll_entries"]], ["PAY-2026-0001"])
		self.assertEqual(data["totals"]["gross_pay"], 1000.0)

	def test_include_drafts_puts_draft_back_and_leaves_cancelled_out(self):
		data = self.register(date_from="2026-06-01", date_to="2026-06-30", include_drafts=True)
		names = [row["name"] for row in data["payroll_entries"]]
		self.assertIn("PAY-2026-0002", names)
		self.assertNotIn("PAY-2026-0003", names)
		self.assertEqual(data["totals"]["gross_pay"], 1500.0)

	def test_the_result_always_says_which_statuses_it_counted(self):
		default = self.register(date_from="2026-06-01", date_to="2026-06-30")
		self.assertEqual(default["statuses_counted"], list(payroll.REGISTER_STATUSES))
		with_drafts = self.register(
			date_from="2026-06-01",
			date_to="2026-06-30",
			include_drafts=True,
		)
		self.assertIn("Draft", with_drafts["statuses_counted"])

	def test_a_run_named_explicitly_is_read_whatever_its_status(self):
		"""The caller asked for that run, not for a window. Refusing to show
		them a Draft they named by docname answers a question nobody asked."""
		data = self.register(pay_period="PAY-2026-0003")
		self.assertEqual(data["payroll_entry_count"], 1)
		self.assertEqual(data["totals"]["gross_pay"], 900.0)
		self.assertEqual(data["statuses_counted"], ["Cancelled"])


# ── Claim 7: the refusals ─────────────────────────────────────────────────


class RegisterRefusals(RegisterTestCase):
	"""Every way of asking wrongly, and what each is told."""

	def test_the_switch_refuses_with_the_field_to_tick(self):
		self.configure(enabled=1, allow_get_payroll_register=0)
		message = self.tool_error(
			"get_payroll_register",
			{
				"company": MAIN,
				"date_from": "2026-06-01",
				"date_to": "2026-06-30",
			},
		)
		self.assertIn("allow_get_payroll_register", message)

	def test_no_window_at_all_is_refused_rather_than_read_as_everything(self):
		message = self.tool_error("get_payroll_register", {"company": MAIN})
		self.assertIn("pay_period", message)
		self.assertIn("date_from", message)

	def test_one_date_without_the_other_is_refused(self):
		message = self.tool_error(
			"get_payroll_register",
			{
				"company": MAIN,
				"date_from": "2026-06-01",
			},
		)
		self.assertIn("date_to", message)

	def test_reversed_dates_are_refused_rather_than_read_as_an_empty_period(self):
		message = self.tool_error(
			"get_payroll_register",
			{
				"company": MAIN,
				"date_from": "2026-06-30",
				"date_to": "2026-06-01",
			},
		)
		self.assertIn("after", message)

	def test_an_unknown_pay_period_names_the_tool_that_lists_them(self):
		message = self.tool_error(
			"get_payroll_register",
			{
				"company": MAIN,
				"pay_period": "PAY-NOPE",
			},
		)
		self.assertIn("list_payroll_entries", message)

	def test_a_run_belonging_to_another_company_is_refused_by_name(self):
		"""Not silently returned as an empty register — the caller named a
		docname that exists, and 'no rows' would read as 'nobody was paid'."""
		self.seed(entry("PAY-2026-0090", "2026-06-01", "2026-06-14", [slip()], company=OTHER))
		message = self.tool_error(
			"get_payroll_register",
			{
				"company": MAIN,
				"pay_period": "PAY-2026-0090",
			},
		)
		self.assertIn(OTHER, message)

	def test_a_window_over_the_entry_cap_is_refused_and_not_truncated(self):
		"""A register that quietly stopped short would look like it had covered
		the period, and its totals would be wrong in the direction nobody
		checks."""
		self.seed(
			*[
				entry(f"PAY-2026-{index:04d}", "2026-01-01", "2026-01-14", [slip()])
				for index in range(1, payroll.REGISTER_ENTRY_CAP + 2)
			]
		)
		message = self.tool_error(
			"get_payroll_register",
			{
				"company": MAIN,
				"date_from": "2026-01-01",
				"date_to": "2026-01-31",
			},
		)
		self.assertIn(str(payroll.REGISTER_ENTRY_CAP), message)
		self.assertIn("Narrow the dates", message)

	def test_an_empty_period_is_an_empty_register_and_not_an_error(self):
		"""Nobody paid in a window is a fact, and a tool that raised over it
		would make 'no payroll in January' impossible to state."""
		data = self.register(date_from="2030-01-01", date_to="2030-01-31")
		self.assertEqual(data["employees"], [])
		self.assertEqual(data["totals"]["gross_pay"], 0)
		self.assertEqual(data["grand_total_labor_cost"], 0)


if __name__ == "__main__":
	unittest.main()
