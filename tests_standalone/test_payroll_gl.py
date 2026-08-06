# SPDX-License-Identifier: MIT
"""Payroll into the general ledger — v0.40.0.

THE CLAIM BEHIND THE RELEASE is that a payroll run should end up in the ledger
without anybody retyping it. v0.30.0 computed the slip, v0.35.0 gave it the
foreman's hours and v0.36.0 drew the tax forms, and a completed run still
produced no Journal Entries — so the largest number on a farm's income statement
was the one number that got keyed in twice.

EIGHT CLAIMS.

1. `TheComponentsAreTheUnit` — eleven components, six employee-side and five
   employer-side, each read off the slip under any of the names a slip might
   carry it under, and each knowing which side of the entry it lands on.

2. `TheMappingIsARecord` — no account name is hard-coded anywhere; an incomplete
   mapping is incomplete by NAME and by SIDE; an employer component with no
   amount this run is not a gap and one with an amount is.

3. `TheEntryBalances` — gross debited equals every withholding plus net
   credited, exactly, on hourly, piece-rate and cross-state slips; each employer
   component balances against itself; a slip whose own arithmetic is broken is
   caught rather than posted.

4. `LinesMerge` — two components pointed at one account and one side become one
   line, and the line still says what it was built from.

5. `ConsolidatedAndPerEmployee` — the two modes book identical totals, and the
   only difference is how many entries they are spread across.

6. `PostingWritesDraftsOnly` — every entry inserted is docstatus 0, names its
   Farm Payroll Entry and its Farm Payroll Slip in the remark, and is linked
   back onto the run.

7. `PostingTwiceIsRefused` — the idempotency rule, and the fact that it is about
   what is in the LEDGER rather than what the link table remembers.

8. `TheRefusals` — every blocker, each reported by name, all of them at once.

Plus `TheTools`, the switch posture: two reads on by default, two writes off,
and each refused by name when its switch is off.
"""

from erpnext_mcp import payroll_gl

from .fixtures import MAIN, MAIN_ABBR, OTHER, V12TestCase, install_hrms
from .harness import STORE

WORKER = "HR-EMP-00001"
PICKER = "HR-EMP-00002"
IDLE = "HR-EMP-00003"

NAMES = {WORKER: "Ana Reyes", PICKER: "Beto Cruz", IDLE: "Carla Mota"}

PERIOD_START = "2025-06-02"
PERIOD_END = "2025-06-15"

#: The accounts a payroll needs and the fixture chart does not ship. Numbered
#: the way a real chart numbers them so the docnames read like docnames.
WAGE_EXPENSE = f"5320 - Field Labor - {MAIN_ABBR}"
TAX_EXPENSE = f"5330 - Payroll Taxes - {MAIN_ABBR}"
PAYROLL_LIABILITIES = f"2200 - Payroll Liabilities - {MAIN_ABBR}"
CLEARING = f"2210 - Payroll Clearing - {MAIN_ABBR}"
FEDERAL_PAYABLE = f"2220 - Federal Tax Payable - {MAIN_ABBR}"
FICA_PAYABLE = f"2230 - FICA Payable - {MAIN_ABBR}"
STATE_PAYABLE = f"2240 - State Tax Payable - {MAIN_ABBR}"
FUTA_PAYABLE = f"2250 - FUTA Payable - {MAIN_ABBR}"
SUTA_PAYABLE = f"2260 - SUTA Payable - {MAIN_ABBR}"
STATE_EMPLOYER_PAYABLE = f"2270 - State Employer Payable - {MAIN_ABBR}"

#: The whole mapping, as `configure_payroll_accounts` takes it. The employee
#: side is the two halves of gross pay; the employer side is five expenses and
#: five liabilities, two of which share an account because employer FICA is one
#: remittance.
FULL_MAPPING = [
	{"component": "Gross Pay", "debit_account": WAGE_EXPENSE},
	{"component": "Federal Tax", "credit_account": FEDERAL_PAYABLE},
	{"component": "SS Employee", "credit_account": FICA_PAYABLE},
	{"component": "Medicare Employee", "credit_account": FICA_PAYABLE},
	{"component": "State Tax", "credit_account": STATE_PAYABLE},
	{"component": "Net Pay", "credit_account": CLEARING},
	{"component": "SS Employer", "debit_account": TAX_EXPENSE, "credit_account": FICA_PAYABLE},
	{"component": "Medicare Employer", "debit_account": TAX_EXPENSE, "credit_account": FICA_PAYABLE},
	{"component": "FUTA", "debit_account": TAX_EXPENSE, "credit_account": FUTA_PAYABLE},
	{"component": "SUTA", "debit_account": TAX_EXPENSE, "credit_account": SUTA_PAYABLE},
	{
		"component": "State Employer Other",
		"debit_account": TAX_EXPENSE,
		"credit_account": STATE_EMPLOYER_PAYABLE,
	},
]

#: The same thing as the pure functions read it, so a pure-function test does
#: not have to stand a site up.
ROWS = [
	{
		"component": row["component"],
		"debit_account": row.get("debit_account", ""),
		"credit_account": row.get("credit_account", ""),
	}
	for row in FULL_MAPPING
]

ON = {
	f"allow_{name}": 1
	for name in (
		"get_payroll_account_mapping",
		"preview_payroll_gl",
		"configure_payroll_accounts",
		"post_payroll_to_gl",
		"get_payroll_entry",
		"list_payroll_entries",
		"create_salary_structure",
		"run_payroll_for_period",
		"preview_payroll_for_period",
		"get_journal_entry",
		"create_journal_entry",
	)
}


def slip(
	employee=WORKER,
	gross=1000.0,
	federal=80.0,
	social=62.0,
	medicare=14.5,
	state=30.0,
	ss_employer=62.0,
	medicare_employer=14.5,
	futa=6.0,
	suta=12.0,
	state_other=25.0,
	**extra,
):
	"""One slip, arithmetically consistent by construction.

	Net is DERIVED rather than passed, because a fixture that let the caller set
	both net and the deductions would be a fixture that could quietly produce an
	unbalanced slip in a test about something else. The one test that wants a
	broken slip breaks it on purpose.
	"""
	deductions = round(federal + social + medicare + state, 2)
	row = {
		"employee": employee,
		"employee_name": NAMES.get(employee, employee),
		"gross_pay": gross,
		"federal_withholding": federal,
		"social_security": social,
		"medicare": medicare,
		"state_withholding": state,
		"total_deductions": deductions,
		"net_pay": round(gross - deductions, 2),
		"social_security_employer": ss_employer,
		"medicare_employer": medicare_employer,
		"futa": futa,
		"state_unemployment": suta,
		"state_employer_other": state_other,
		"pay_period_start": PERIOD_START,
		"pay_period_end": PERIOD_END,
		"company": MAIN,
	}
	row.update(extra)
	return row


# ── Claim 1: the components ───────────────────────────────────────────────


class TheComponentsAreTheUnit(V12TestCase):
	"""Eleven components, each knowing its side and none knowing an account."""

	def test_there_are_eleven_six_employee_and_five_employer(self):
		self.assertEqual(len(payroll_gl.COMPONENTS), 11)
		self.assertEqual(len(payroll_gl.CORE_COMPONENTS), 6)
		self.assertEqual(len(payroll_gl.EMPLOYER_COMPONENTS), 5)

	def test_no_component_carries_an_account_name(self):
		"""The whole point of the mapping being a record."""
		for definition in payroll_gl.COMPONENTS:
			with self.subTest(component=definition["component"]):
				self.assertNotIn("account", definition)
				self.assertNotIn("debit_account", definition)

	def test_gross_pay_is_a_debit_and_every_withholding_is_a_credit(self):
		self.assertEqual(payroll_gl.COMPONENT_INDEX["Gross Pay"]["sides"], ("debit",))
		for component in ("Federal Tax", "SS Employee", "Medicare Employee", "State Tax", "Net Pay"):
			with self.subTest(component=component):
				self.assertEqual(payroll_gl.COMPONENT_INDEX[component]["sides"], ("credit",))

	def test_every_employer_component_takes_both_sides(self):
		"""An expense AND a liability, because it is money owed on top of gross."""
		for component in payroll_gl.EMPLOYER_COMPONENTS:
			with self.subTest(component=component):
				self.assertEqual(
					payroll_gl.COMPONENT_INDEX[component]["sides"], ("debit", "credit"),
				)

	def test_amounts_come_off_the_slip_under_the_names_a_slip_uses(self):
		amounts = payroll_gl.component_amounts(slip())
		self.assertEqual(amounts["Gross Pay"], 1000.0)
		self.assertEqual(amounts["Federal Tax"], 80.0)
		self.assertEqual(amounts["SS Employee"], 62.0)
		self.assertEqual(amounts["Medicare Employee"], 14.5)
		self.assertEqual(amounts["State Tax"], 30.0)
		self.assertEqual(amounts["Net Pay"], 813.5)

	def test_an_alias_key_is_read_where_the_primary_is_absent(self):
		"""`payroll_calc` says social_security; a caller may say the long form."""
		row = {"gross_pay": 100, "social_security_employee": 6.2, "futa_employer": 0.6}
		amounts = payroll_gl.component_amounts(row)
		self.assertEqual(amounts["SS Employee"], 6.2)
		self.assertEqual(amounts["FUTA"], 0.6)

	def test_a_missing_key_is_zero_rather_than_an_error(self):
		"""A Washington worker genuinely has no state income tax."""
		amounts = payroll_gl.component_amounts({"gross_pay": 100})
		self.assertEqual(amounts["State Tax"], 0.0)
		self.assertEqual(amounts["SUTA"], 0.0)

	def test_a_slip_of_zeros_is_empty_and_one_with_anything_is_not(self):
		self.assertTrue(payroll_gl.slip_is_empty(payroll_gl.component_amounts({})))
		self.assertFalse(payroll_gl.slip_is_empty(payroll_gl.component_amounts(slip())))

	def test_a_pre_v0_40_slip_is_recognised_as_carrying_no_employer_figures(self):
		"""Four zeros and four absences are different facts about a payroll."""
		old = {k: v for k, v in slip().items() if k not in (
			"social_security_employer", "medicare_employer", "futa",
			"state_unemployment", "state_employer_other",
		)}
		self.assertFalse(payroll_gl.employer_taxes_recorded(old))
		self.assertTrue(payroll_gl.employer_taxes_recorded(slip()))


# ── Claim 2: the mapping ──────────────────────────────────────────────────


class TheMappingIsARecord(V12TestCase):
	"""Which account a component posts to is configuration, never code."""

	def test_a_full_mapping_is_complete(self):
		verdict = payroll_gl.validate_mapping(ROWS)
		self.assertTrue(verdict["complete"], verdict["missing"])

	def test_an_empty_mapping_names_all_six_required_components(self):
		verdict = payroll_gl.validate_mapping([])
		self.assertFalse(verdict["complete"])
		named = {row["component"] for row in verdict["missing"]}
		self.assertEqual(named, set(payroll_gl.CORE_COMPONENTS))

	def test_a_gap_is_reported_by_component_and_by_side(self):
		rows = [row for row in ROWS if row["component"] != "Net Pay"]
		verdict = payroll_gl.validate_mapping(rows)
		self.assertFalse(verdict["complete"])
		gap = next(row for row in verdict["missing"] if row["component"] == "Net Pay")
		self.assertEqual(gap["side"], "credit")
		self.assertEqual(gap["account_field"], "credit_account")

	def test_an_employer_component_with_one_side_missing_is_a_gap(self):
		rows = [dict(row) for row in ROWS]
		for row in rows:
			if row["component"] == "FUTA":
				row["credit_account"] = ""
		amounts = payroll_gl.component_amounts(slip())
		verdict = payroll_gl.validate_mapping(rows, amounts)
		self.assertFalse(verdict["complete"])
		self.assertEqual([r["component"] for r in verdict["missing"]], ["FUTA"])

	def test_an_employer_component_with_no_amount_this_run_is_not_a_gap(self):
		"""A farm with no SUTA liability should not have to invent an account."""
		rows = [row for row in ROWS if row["component"] != "SUTA"]
		amounts = payroll_gl.component_amounts(slip(suta=0.0))
		self.assertTrue(payroll_gl.validate_mapping(rows, amounts)["complete"])

	def test_the_same_component_with_an_amount_is_a_gap(self):
		rows = [row for row in ROWS if row["component"] != "SUTA"]
		amounts = payroll_gl.component_amounts(slip(suta=12.0))
		verdict = payroll_gl.validate_mapping(rows, amounts)
		self.assertFalse(verdict["complete"])
		self.assertEqual({r["component"] for r in verdict["missing"]}, {"SUTA"})

	def test_an_employee_component_is_required_even_at_zero(self):
		"""The six are the two sides of gross pay whatever the amounts are."""
		rows = [row for row in ROWS if row["component"] != "State Tax"]
		amounts = payroll_gl.component_amounts(slip(state=0.0))
		self.assertFalse(payroll_gl.validate_mapping(rows, amounts)["complete"])

	def test_dropping_the_employer_half_drops_its_requirements_too(self):
		rows = [row for row in ROWS if row["component"] in payroll_gl.CORE_COMPONENTS]
		amounts = payroll_gl.component_amounts(slip())
		self.assertTrue(
			payroll_gl.validate_mapping(rows, amounts, include_employer=False)["complete"]
		)
		self.assertFalse(payroll_gl.validate_mapping(rows, amounts)["complete"])

	def test_a_component_name_this_app_never_heard_of_is_named(self):
		rows = [*ROWS, {"component": "Gross Payy", "debit_account": WAGE_EXPENSE}]
		self.assertEqual(payroll_gl.unrecognised_components(rows), ["Gross Payy"])

	def test_a_mapping_keyed_by_component_reads_the_same(self):
		keyed = {row["component"]: row for row in ROWS}
		self.assertEqual(
			payroll_gl.mapping_index(keyed), payroll_gl.mapping_index(ROWS),
		)


# ── Claim 3: the entry balances ───────────────────────────────────────────


class TheEntryBalances(V12TestCase):
	"""Double entry, checked before anything is written rather than after."""

	def entry(self, row=None, **kwargs):
		return payroll_gl.build_journal_entry(row or slip(), ROWS, **kwargs)

	def test_gross_is_debited_once(self):
		entry = self.entry()
		debits = [line for line in entry["accounts"] if line.get("debit")]
		wage = [line for line in debits if line["account"] == WAGE_EXPENSE]
		self.assertEqual(len(wage), 1)
		self.assertEqual(wage[0]["debit"], 1000.0)

	def test_the_credits_are_every_withholding_plus_net(self):
		entry = self.entry()
		credited = {}
		for line in entry["accounts"]:
			if line.get("credit"):
				credited[line["account"]] = credited.get(line["account"], 0) + line["credit"]
		self.assertEqual(credited[FEDERAL_PAYABLE], 80.0)
		self.assertEqual(credited[STATE_PAYABLE], 30.0)
		self.assertEqual(credited[CLEARING], 813.5)

	def test_it_balances(self):
		entry = self.entry()
		self.assertTrue(entry["balanced"], entry["difference"])
		self.assertEqual(entry["total_debit"], entry["total_credit"])

	def test_it_balances_on_a_slip_with_awkward_cents(self):
		"""The rounding case: figures that do not divide cleanly."""
		entry = self.entry(slip(gross=1337.77, federal=131.31, social=82.94, medicare=19.40, state=41.42))
		self.assertTrue(entry["balanced"], entry["difference"])

	def test_each_employer_component_balances_against_itself(self):
		"""An expense and a liability of the same amount, from one component."""
		entry = self.entry(slip(gross=0, federal=0, social=0, medicare=0, state=0,
		                        ss_employer=0, medicare_employer=0, futa=0, suta=40.0,
		                        state_other=0))
		self.assertTrue(entry["balanced"])
		self.assertEqual(entry["total_debit"], 40.0)
		accounts = {line["account"] for line in entry["accounts"]}
		self.assertEqual(accounts, {TAX_EXPENSE, SUTA_PAYABLE})

	def test_a_zero_component_produces_no_line(self):
		"""ERPNext refuses a row with a zero on both sides, and so does this."""
		entry = self.entry(slip(state=0.0))
		self.assertNotIn(STATE_PAYABLE, {line["account"] for line in entry["accounts"]})

	def test_a_slip_whose_own_arithmetic_is_broken_does_not_balance(self):
		"""Gross that is not deductions plus net is a data problem, not a posting."""
		broken = slip()
		broken["net_pay"] = broken["net_pay"] + 100
		entry = payroll_gl.build_journal_entry(broken, ROWS)
		self.assertFalse(entry["balanced"])
		self.assertEqual(entry["difference"], -100.0)

	def test_the_employer_half_can_be_left_off_and_it_still_balances(self):
		entry = self.entry(include_employer=False)
		self.assertTrue(entry["balanced"])
		self.assertNotIn(TAX_EXPENSE, {line["account"] for line in entry["accounts"]})

	def test_the_remark_names_the_run_the_worker_and_the_slip(self):
		entry = payroll_gl.build_journal_entry(
			slip(), ROWS, payroll_entry="PAY-2025-0001", slip_name="abc123",
		)
		self.assertIn("PAY-2025-0001", entry["user_remark"])
		self.assertIn("Ana Reyes", entry["user_remark"])
		self.assertIn("Farm Payroll Slip abc123", entry["user_remark"])
		self.assertIn(PERIOD_START, entry["user_remark"])

	def test_a_zero_gross_slip_is_warned_about_and_not_refused(self):
		entry = payroll_gl.build_journal_entry(
			slip(gross=0, federal=0, social=0, medicare=0, state=0), ROWS,
		)
		self.assertTrue(any("zero gross pay" in w for w in entry["warnings"]))

	def test_a_pre_v0_40_slip_says_its_employer_taxes_are_not_posted(self):
		old = {k: v for k, v in slip().items() if k not in (
			"social_security_employer", "medicare_employer", "futa",
			"state_unemployment", "state_employer_other",
		)}
		entry = payroll_gl.build_journal_entry(old, ROWS)
		self.assertTrue(entry["balanced"])
		self.assertTrue(any("before v0.40.0" in w for w in entry["warnings"]))

	def test_a_cost_center_lands_on_every_line(self):
		entry = self.entry(cost_center="Main - ETC")
		self.assertTrue(all(line["cost_center"] == "Main - ETC" for line in entry["accounts"]))


# ── Claim 4: lines merge ──────────────────────────────────────────────────


class LinesMerge(V12TestCase):
	"""Two components on one account and one side become one line."""

	def test_employee_fica_is_one_credit_line_not_two(self):
		entry = payroll_gl.build_journal_entry(slip(), ROWS)
		fica = [
			line for line in entry["accounts"]
			if line["account"] == FICA_PAYABLE and line.get("credit")
		]
		self.assertEqual(len(fica), 1)
		# 62.00 employee SS + 14.50 employee Medicare + 62.00 employer SS
		# + 14.50 employer Medicare, all one remittance and one account.
		self.assertEqual(fica[0]["credit"], 153.0)

	def test_the_merged_line_still_says_what_it_was_built_from(self):
		entry = payroll_gl.build_journal_entry(slip(), ROWS)
		fica = next(
			line for line in entry["accounts"]
			if line["account"] == FICA_PAYABLE and line.get("credit")
		)
		self.assertIn("Social Security withheld", fica["user_remark"])
		self.assertIn("Medicare", fica["user_remark"])

	def test_the_component_breakdown_survives_the_merge(self):
		entry = payroll_gl.build_journal_entry(slip(), ROWS)
		by_component = {row["component"]: row for row in entry["components"]}
		self.assertEqual(by_component["SS Employee"]["amount"], 62.0)
		self.assertEqual(by_component["Medicare Employee"]["amount"], 14.5)
		self.assertEqual(by_component["SS Employee"]["credit_account"], FICA_PAYABLE)

	def test_the_four_employer_expenses_are_one_debit_line(self):
		entry = payroll_gl.build_journal_entry(slip(), ROWS)
		expense = [
			line for line in entry["accounts"]
			if line["account"] == TAX_EXPENSE and line.get("debit")
		]
		self.assertEqual(len(expense), 1)
		self.assertEqual(expense[0]["debit"], 62.0 + 14.5 + 6.0 + 12.0 + 25.0)

	def test_a_component_with_no_account_is_reported_rather_than_dropped(self):
		rows = [row for row in ROWS if row["component"] != "SUTA"]
		_lines, _breakdown, unmapped = payroll_gl.journal_lines(
			payroll_gl.component_amounts(slip()), rows,
		)
		self.assertEqual([row["component"] for row in unmapped], ["SUTA"])


# ── Claim 5: the two modes ────────────────────────────────────────────────


class ConsolidatedAndPerEmployee(V12TestCase):
	"""Same totals, different number of entries. Nothing else differs."""

	def slips(self):
		return [slip(WORKER), slip(PICKER, gross=2000.0, federal=190.0, social=124.0,
		                            medicare=29.0, state=61.0)]

	def test_consolidated_is_one_entry_for_the_whole_run(self):
		plan = payroll_gl.build_payroll_journal_entries(self.slips(), ROWS, company=MAIN)
		self.assertEqual(plan["mode"], "consolidated")
		self.assertEqual(plan["entry_count"], 1)
		self.assertEqual(plan["journal_entries"][0]["employee_count"], 2)

	def test_per_employee_is_one_each(self):
		plan = payroll_gl.build_payroll_journal_entries(
			self.slips(), ROWS, company=MAIN, mode="per_employee",
		)
		self.assertEqual(plan["entry_count"], 2)
		self.assertEqual(
			[entry["employee"] for entry in plan["journal_entries"]], [WORKER, PICKER],
		)

	def test_the_two_modes_book_the_same_money(self):
		one = payroll_gl.build_payroll_journal_entries(self.slips(), ROWS, company=MAIN)
		many = payroll_gl.build_payroll_journal_entries(
			self.slips(), ROWS, company=MAIN, mode="per_employee",
		)
		self.assertEqual(one["total_debit"], many["total_debit"])
		self.assertEqual(one["total_credit"], many["total_credit"])
		self.assertEqual(one["totals"], many["totals"])

	def test_both_modes_balance(self):
		for mode in ("consolidated", "per_employee"):
			with self.subTest(mode=mode):
				plan = payroll_gl.build_payroll_journal_entries(
					self.slips(), ROWS, company=MAIN, mode=mode,
				)
				self.assertTrue(plan["balanced"], plan["unbalanced"])

	def test_a_slip_of_zeros_is_skipped_and_named_in_both_modes(self):
		rows = [*self.slips(), slip(IDLE, gross=0, federal=0, social=0, medicare=0,
		                            state=0, ss_employer=0, medicare_employer=0,
		                            futa=0, suta=0, state_other=0)]
		for mode in ("consolidated", "per_employee"):
			with self.subTest(mode=mode):
				plan = payroll_gl.build_payroll_journal_entries(
					rows, ROWS, company=MAIN, mode=mode,
				)
				self.assertEqual([row["employee"] for row in plan["skipped"]], [IDLE])

	def test_a_run_of_nothing_but_zeros_produces_no_entry_at_all(self):
		rows = [slip(IDLE, gross=0, federal=0, social=0, medicare=0, state=0,
		             ss_employer=0, medicare_employer=0, futa=0, suta=0, state_other=0)]
		plan = payroll_gl.build_payroll_journal_entries(rows, ROWS, company=MAIN)
		self.assertEqual(plan["entry_count"], 0)

	def test_an_unknown_mode_falls_back_to_consolidated_in_the_pure_function(self):
		plan = payroll_gl.build_payroll_journal_entries(
			self.slips(), ROWS, company=MAIN, mode="whatever",
		)
		self.assertEqual(plan["mode"], "consolidated")

	def test_one_warning_is_reported_once_however_many_slips_produced_it(self):
		old = []
		for employee in (WORKER, PICKER):
			row = slip(employee)
			for key in ("social_security_employer", "medicare_employer", "futa",
			            "state_unemployment", "state_employer_other"):
				row.pop(key)
			old.append(row)
		plan = payroll_gl.build_payroll_journal_entries(old, ROWS, company=MAIN, mode="per_employee")
		employer = [w for w in plan["warnings"] if "before v0.40.0" in w]
		self.assertEqual(len(employer), 2, "one per worker, named — but not duplicated verbatim")
		self.assertEqual(len(set(employer)), 2)


# ── The site: fixtures ────────────────────────────────────────────────────


class PayrollGLTestCase(V12TestCase):
	"""A site with a payroll run on it and somewhere to post it to."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		self._seed_accounts()
		self._seed_employees()

	def _seed_accounts(self):
		rows = []
		chart = [
			("5320", "Field Labor", "Expense", 0),
			("5330", "Payroll Taxes", "Expense", 0),
			("2200", "Payroll Liabilities", "Liability", 1),
			("2210", "Payroll Clearing", "Liability", 0),
			("2220", "Federal Tax Payable", "Liability", 0),
			("2230", "FICA Payable", "Liability", 0),
			("2240", "State Tax Payable", "Liability", 0),
			("2250", "FUTA Payable", "Liability", 0),
			("2260", "SUTA Payable", "Liability", 0),
			("2270", "State Employer Payable", "Liability", 0),
		]
		counter = 900
		for number, name, root, is_group in chart:
			counter += 2
			rows.append({
				"name": f"{number} - {name} - {MAIN_ABBR}",
				"account_name": name,
				"account_number": number,
				"is_group": is_group,
				"root_type": root,
				"account_type": "",
				"account_currency": "USD",
				"disabled": 0,
				"company": MAIN,
				"lft": counter,
				"rgt": counter + 1,
			})
		STORE.seed("Account", rows)

	def _seed_employees(self):
		STORE.seed("Employee", [
			{"name": name, "employee_name": NAMES[name], "company": MAIN,
			 "status": "Active", "date_of_joining": "2025-01-15"}
			for name in (WORKER, PICKER, IDLE)
		])

	def configure_mapping(self, components=None, **extra):
		return self.tool_data("configure_payroll_accounts", {
			"company": MAIN,
			"components": components if components is not None else FULL_MAPPING,
			**extra,
		})

	def seed_run(self, slips=None, status="Calculated", name="PAY-2025-00001"):
		"""A Farm Payroll Entry as `run_payroll_for_period` would have left it."""
		rows = slips if slips is not None else [slip(WORKER), slip(PICKER, gross=2000.0)]
		child = []
		for index, row in enumerate(rows, start=1):
			entry = {k: v for k, v in row.items() if k not in (
				"pay_period_start", "pay_period_end", "company",
			)}
			entry["name"] = f"{name}-slip-{index}"
			entry["parent"] = name
			entry["parenttype"] = "Farm Payroll Entry"
			entry["parentfield"] = "slips"
			entry["minimum_wage_check"] = 1
			child.append(entry)

		STORE.seed("Farm Payroll Entry", [{
			"name": name,
			"company": MAIN,
			"pay_period_start": PERIOD_START,
			"pay_period_end": PERIOD_END,
			"pay_frequency": "Biweekly",
			"status": status,
			"total_gross": round(sum(r["gross_pay"] for r in rows), 2),
			"total_deductions": round(sum(r["total_deductions"] for r in rows), 2),
			"total_net": round(sum(r["net_pay"] for r in rows), 2),
			"employee_count": len(rows),
			"gl_status": "Not Posted",
			"slips": child,
			"gl_postings": [],
		}])
		return name

    # -- shorthands ------------------------------------------------------

	def preview(self, **extra):
		return self.tool_data("preview_payroll_gl", {"payroll_entry": "PAY-2025-00001", **extra})

	def post(self, **extra):
		return self.tool_data("post_payroll_to_gl", {"payroll_entry": "PAY-2025-00001", **extra})

	def post_error(self, **extra):
		return self.tool_error("post_payroll_to_gl", {"payroll_entry": "PAY-2025-00001", **extra})


# ── Claim 6: posting writes drafts only ───────────────────────────────────


class PostingWritesDraftsOnly(PayrollGLTestCase):
	def setUp(self):
		super().setUp()
		self.configure_mapping()
		self.seed_run()

	def test_it_creates_journal_entries_and_names_them(self):
		data = self.post()
		self.assertEqual(data["entry_count"], 1)
		self.assertEqual(len(data["journal_entries"]), 1)
		self.assertTrue(frappe_exists(data["journal_entries"][0]))

	def test_every_entry_it_creates_is_a_draft(self):
		data = self.post(mode="per_employee")
		self.assertEqual(data["entry_count"], 2)
		for name in data["journal_entries"]:
			with self.subTest(entry=name):
				row = STORE.tables["Journal Entry"][name]
				self.assertEqual(int(row["docstatus"]), 0)

	def test_the_entry_balances_on_the_site_too(self):
		data = self.post()
		name = data["journal_entries"][0]
		rows = [
			row for row in STORE.rows("Journal Entry Account")
			if row.get("parent") == name
		] or STORE.tables["Journal Entry"][name].get("accounts") or []
		debit = round(sum(float(row.get("debit") or 0) for row in rows), 2)
		credit = round(sum(float(row.get("credit") or 0) for row in rows), 2)
		self.assertEqual(debit, credit)
		self.assertEqual(debit, data["total_debit"])

	def test_the_remark_names_the_payroll_run(self):
		data = self.post()
		row = STORE.tables["Journal Entry"][data["journal_entries"][0]]
		self.assertIn("PAY-2025-00001", row["user_remark"])

	def test_a_per_employee_remark_names_the_slip_it_came_from(self):
		data = self.post(mode="per_employee")
		remarks = [
			STORE.tables["Journal Entry"][name]["user_remark"]
			for name in data["journal_entries"]
		]
		self.assertTrue(any("PAY-2025-00001-slip-1" in text for text in remarks))

	def test_the_entries_are_linked_back_onto_the_run(self):
		data = self.post()
		run = STORE.tables["Farm Payroll Entry"]["PAY-2025-00001"]
		linked = [row["journal_entry"] for row in run["gl_postings"]]
		self.assertEqual(linked, data["journal_entries"])

	def test_the_run_records_that_it_has_drafts_and_never_that_it_is_submitted(self):
		self.post()
		run = STORE.tables["Farm Payroll Entry"]["PAY-2025-00001"]
		self.assertEqual(run["gl_status"], "Draft Entries Created")

	def test_the_result_says_the_drafts_affect_no_balance(self):
		data = self.post()
		self.assertIn("DRAFTS", data["next_step"])
		self.assertIn("no balance", data["next_step"])

	def test_the_posting_date_defaults_to_the_period_end_and_can_be_moved(self):
		self.assertEqual(self.preview()["posting_date"], PERIOD_END)
		data = self.post(posting_date="2025-06-30")
		row = STORE.tables["Journal Entry"][data["journal_entries"][0]]
		self.assertEqual(str(row["posting_date"]), "2025-06-30")

	def test_the_preview_and_the_posting_produce_the_same_entry(self):
		"""The property that makes a preview worth having."""
		before = self.preview()
		after = self.post()
		self.assertEqual(before["total_debit"], after["total_debit"])
		self.assertEqual(before["total_credit"], after["total_credit"])
		self.assertEqual(before["entry_count"], after["entry_count"])

	def test_the_preview_writes_nothing(self):
		before = len(STORE.tables.get("Journal Entry", {}))
		self.preview()
		self.assertEqual(len(STORE.tables.get("Journal Entry", {})), before)
		self.assertIsNone(self.preview()["created"])


# ── Claim 7: posting twice ────────────────────────────────────────────────


class PostingTwiceIsRefused(PayrollGLTestCase):
	def setUp(self):
		super().setUp()
		self.configure_mapping()
		self.seed_run()

	def test_a_second_posting_is_refused_and_names_the_entries(self):
		first = self.post()
		error = self.post_error()
		self.assertIn("already has", error)
		self.assertIn(first["journal_entries"][0], error)

	def test_the_refusal_says_what_would_happen(self):
		self.post()
		error = self.post_error()
		self.assertIn("double the wage expense", error)

	def test_nothing_is_written_by_the_refused_second_posting(self):
		self.post()
		before = len(STORE.tables["Journal Entry"])
		self.post_error()
		self.assertEqual(len(STORE.tables["Journal Entry"]), before)

	def test_the_preview_reports_it_as_a_blocker_rather_than_raising(self):
		self.post()
		data = self.preview()
		self.assertFalse(data["would_post"])
		self.assertIn("already_posted", [row["blocker"] for row in data["blockers"]])

	def test_a_cancelled_entry_is_not_a_reason_to_refuse(self):
		"""The rule is about what is in the LEDGER, not what the table remembers."""
		data = self.post()
		STORE.tables["Journal Entry"][data["journal_entries"][0]]["docstatus"] = 2
		STORE.commit()
		again = self.post()
		self.assertEqual(again["entry_count"], 1)
		self.assertNotEqual(again["journal_entries"], data["journal_entries"])

	def test_a_deleted_entry_is_not_a_reason_to_refuse(self):
		data = self.post()
		del STORE.tables["Journal Entry"][data["journal_entries"][0]]
		STORE.commit()
		self.assertEqual(self.post()["entry_count"], 1)

	def test_a_superseded_posting_is_reported_rather_than_forgotten(self):
		data = self.post()
		STORE.tables["Journal Entry"][data["journal_entries"][0]]["docstatus"] = 2
		STORE.commit()
		self.assertEqual(len(self.preview()["superseded_postings"]), 1)


# ── Claim 8: the refusals ─────────────────────────────────────────────────


class TheRefusals(PayrollGLTestCase):
	def test_a_company_with_no_mapping_cannot_post(self):
		self.seed_run()
		error = self.post_error()
		self.assertIn("no payroll account mapping", error)
		self.assertIn("configure_payroll_accounts", error)

	def test_an_incomplete_mapping_names_the_missing_components(self):
		self.configure_mapping([row for row in FULL_MAPPING if row["component"] != "Net Pay"])
		self.seed_run()
		error = self.post_error()
		self.assertIn("Net Pay", error)
		self.assertIn("cannot produce an entry that balances", error)

	def test_an_inactive_mapping_cannot_post(self):
		self.configure_mapping()
		self.tool_data("configure_payroll_accounts", {"company": MAIN, "components": [], "is_active": 0}) \
			if False else self.set_mapping_inactive()
		self.seed_run()
		error = self.post_error()
		self.assertIn("inactive", error)

	def set_mapping_inactive(self):
		STORE.tables["Farm Payroll Account Mapping"][MAIN]["is_active"] = 0
		STORE.commit()

	def test_a_draft_payroll_run_cannot_post(self):
		self.configure_mapping()
		self.seed_run(status="Draft")
		error = self.post_error()
		self.assertIn("'Draft'", error)
		self.assertIn("no computed slips", error)

	def test_a_cancelled_payroll_run_cannot_post(self):
		self.configure_mapping()
		self.seed_run(status="Cancelled")
		self.assertIn("Cancelled", self.post_error())

	def test_a_run_of_zeros_has_nothing_to_post(self):
		self.configure_mapping()
		self.seed_run([slip(IDLE, gross=0, federal=0, social=0, medicare=0, state=0,
		                    ss_employer=0, medicare_employer=0, futa=0, suta=0,
		                    state_other=0)])
		self.assertIn("nothing to book", self.post_error())

	def test_an_unbalanced_slip_is_refused_rather_than_posted(self):
		self.configure_mapping()
		broken = slip(WORKER)
		broken["net_pay"] = broken["net_pay"] + 100
		self.seed_run([broken])
		error = self.post_error()
		self.assertIn("do not balance", error)

	def test_every_blocker_is_reported_at_once(self):
		"""A caller who fixes one refusal should not meet the next one alone."""
		self.seed_run(status="Draft")
		data = self.preview()
		blockers = {row["blocker"] for row in data["blockers"]}
		self.assertIn("payroll_status", blockers)
		self.assertIn("no_mapping", blockers)

	def test_a_refusal_writes_nothing(self):
		self.seed_run()
		before = len(STORE.tables.get("Journal Entry", {}))
		self.post_error()
		self.assertEqual(len(STORE.tables.get("Journal Entry", {})), before)
		run = STORE.tables["Farm Payroll Entry"]["PAY-2025-00001"]
		self.assertEqual(run.get("gl_status"), "Not Posted")

	def test_an_unknown_payroll_entry_says_so(self):
		self.assertIn(
			"no Farm Payroll Entry",
			self.tool_error("post_payroll_to_gl", {"payroll_entry": "PAY-nope"}),
		)


# ── Configuring the mapping ───────────────────────────────────────────────


class ConfiguringTheMapping(PayrollGLTestCase):
	def test_it_creates_the_mapping_and_reports_it_complete(self):
		data = self.configure_mapping()
		self.assertTrue(data["created"])
		self.assertTrue(data["mapping"]["complete"])
		self.assertEqual(len(data["components"]), 11)

	def test_rows_merge_rather_than_replacing(self):
		self.configure_mapping(FULL_MAPPING[:2])
		data = self.configure_mapping(FULL_MAPPING[2:])
		self.assertEqual(len(data["components"]), 11)
		self.assertFalse(data["created"])

	def test_replace_discards_what_is_not_in_the_call(self):
		self.configure_mapping()
		data = self.configure_mapping(FULL_MAPPING[:1], replace=1)
		self.assertEqual(len(data["components"]), 1)
		self.assertFalse(data["mapping"]["complete"])

	def test_a_changed_account_is_reported_with_what_it_was(self):
		self.configure_mapping()
		data = self.configure_mapping([{"component": "Gross Pay", "debit_account": TAX_EXPENSE}])
		change = next(row for row in data["changes"] if row["component"] == "Gross Pay")
		self.assertEqual(change["change"], "changed")
		self.assertEqual(change["was"]["debit_account"], WAGE_EXPENSE)

	def test_rows_are_stored_in_component_order_however_they_arrive(self):
		self.configure_mapping(list(reversed(FULL_MAPPING)))
		data = self.tool_data("get_payroll_account_mapping", {"company": MAIN})
		self.assertEqual(
			[row["component"] for row in data["components"]],
			[name for name in payroll_gl.COMPONENT_NAMES],
		)

	def test_an_account_resolves_by_number(self):
		self.configure_mapping([{"component": "Gross Pay", "debit_account": "5320"}])
		data = self.tool_data("get_payroll_account_mapping", {"company": MAIN})
		row = next(r for r in data["components"] if r["component"] == "Gross Pay")
		self.assertEqual(row["debit_account"], WAGE_EXPENSE)

	def test_a_group_account_is_refused_when_the_mapping_is_written(self):
		error = self.tool_error("configure_payroll_accounts", {
			"company": MAIN,
			"components": [{"component": "Net Pay", "credit_account": PAYROLL_LIABILITIES}],
		})
		self.assertIn("group account", error)
		self.assertIn("can never post", error)

	def test_a_component_name_that_does_not_exist_lists_the_ones_that_do(self):
		error = self.tool_error("configure_payroll_accounts", {
			"company": MAIN,
			"components": [{"component": "Wages", "debit_account": WAGE_EXPENSE}],
		})
		self.assertIn("not a payroll component", error)
		self.assertIn("Gross Pay", error)

	def test_a_credit_on_gross_pay_is_refused_by_name(self):
		"""Gross pay has no credit side; a caller who sent one meant something else."""
		error = self.tool_error("configure_payroll_accounts", {
			"company": MAIN,
			"components": [{"component": "Gross Pay", "credit_account": CLEARING}],
		})
		self.assertIn("no credit side", error)

	def test_a_single_account_on_a_two_sided_component_is_refused(self):
		error = self.tool_error("configure_payroll_accounts", {
			"company": MAIN,
			"components": [{"component": "FUTA", "account": FUTA_PAYABLE}],
		})
		self.assertIn("BOTH sides", error)

	def test_a_single_account_on_a_one_sided_component_is_accepted(self):
		data = self.configure_mapping([{"component": "Net Pay", "account": CLEARING}])
		row = next(r for r in data["components"] if r["component"] == "Net Pay")
		self.assertEqual(row["credit_account"], CLEARING)

	def test_one_component_twice_in_one_call_is_refused(self):
		error = self.tool_error("configure_payroll_accounts", {
			"company": MAIN,
			"components": [
				{"component": "Net Pay", "credit_account": CLEARING},
				{"component": "Net Pay", "credit_account": FICA_PAYABLE},
			],
		})
		self.assertIn("appears twice", error)

	def test_a_mapping_keyed_by_component_is_accepted(self):
		data = self.configure_mapping({"Gross Pay": {"debit_account": WAGE_EXPENSE}})
		self.assertEqual(len(data["components"]), 1)

	def test_the_default_posting_mode_is_consolidated_and_can_be_changed(self):
		data = self.configure_mapping()
		self.assertEqual(data["default_posting_mode"], "Consolidated")
		data = self.configure_mapping(default_posting_mode="Per Employee")
		self.assertEqual(data["default_posting_mode"], "Per Employee")

	def test_the_mapping_default_decides_the_mode_when_the_call_does_not(self):
		self.configure_mapping(default_posting_mode="Per Employee")
		self.seed_run()
		self.assertEqual(self.preview()["mode"], "per_employee")
		self.assertEqual(self.preview(mode="consolidated")["mode"], "consolidated")

	def test_a_cost_center_on_the_mapping_reaches_every_line(self):
		self.configure_mapping(cost_center="Main")
		self.seed_run()
		data = self.preview()
		lines = data["journal_entries"][0]["accounts"]
		self.assertTrue(all(line.get("cost_center") for line in lines))

	def test_an_unknown_mode_is_refused_by_name(self):
		error = self.tool_error("configure_payroll_accounts", {
			"company": MAIN, "components": FULL_MAPPING, "default_posting_mode": "weekly",
		})
		self.assertIn("consolidated", error)


# ── Reading the mapping ───────────────────────────────────────────────────


class ReadingTheMapping(PayrollGLTestCase):
	def test_an_unconfigured_company_says_payroll_cannot_be_posted(self):
		data = self.tool_data("get_payroll_account_mapping", {"company": MAIN})
		self.assertFalse(data["configured"])
		self.assertIn("configure_payroll_accounts", data["next_step"])

	def test_it_lists_every_component_and_what_each_is_for(self):
		data = self.tool_data("get_payroll_account_mapping", {"company": MAIN})
		self.assertEqual(len(data["catalogue"]), 11)
		gross = next(row for row in data["catalogue"] if row["component"] == "Gross Pay")
		self.assertEqual(gross["sides"], ["debit"])
		self.assertTrue(gross["required"])

	def test_an_incomplete_mapping_says_which_components_are_missing(self):
		self.configure_mapping(FULL_MAPPING[:3])
		data = self.tool_data("get_payroll_account_mapping", {"company": MAIN})
		self.assertFalse(data["mapping"]["complete"])
		self.assertIn("Net Pay", data["next_step"])

	def test_a_complete_mapping_says_so(self):
		self.configure_mapping()
		data = self.tool_data("get_payroll_account_mapping", {"company": MAIN})
		self.assertTrue(data["mapping"]["complete"])

	def test_a_mapping_belongs_to_one_company(self):
		self.configure_mapping()
		other = self.tool_data("get_payroll_account_mapping", {"company": OTHER})
		self.assertFalse(other["configured"])


# ── End to end: the shift register to the ledger ──────────────────────────


class EndToEnd(PayrollGLTestCase):
	"""Hours on the register, a payroll run, and a journal entry that balances.

	The one test here that goes through the real payroll engine rather than a
	seeded run — which is what proves the employer taxes v0.40.0 started storing
	actually reach a slip, and from there the ledger.
	"""

	def setUp(self):
		super().setUp()
		self._seed_fica()
		self._seed_state_configs()
		self.configure_mapping()

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

	def _seed_state_configs(self):
		STORE.seed("State Tax Configuration", [{
			"name": "STC-OR-2025", "company": MAIN, "state": "OR",
			"tax_year": 2025, "status": "Active",
			"or_income_tax_enabled": 0,
			"or_transit_tax_rate": 0.1,
			"or_paid_leave_rate": 1.0,
			"or_paid_leave_employee_share": 60,
			"or_paid_leave_employer_share": 40,
			"or_paid_leave_small_employer": 0,
			"or_workers_comp_rate": 1.5,
			"suta_rate": 2.4,
			"suta_wage_base": 54300,
		}])

	def _seed_shifts(self):
		rows = []
		for index, day in enumerate(("2025-06-02", "2025-06-03", "2025-06-04"), start=1):
			rows.append({
				"name": f"SHIFT-{index:03d}",
				"company": MAIN,
				"shift_type": "Harvest",
				"work_state": "OR",
				"start_datetime": f"{day} 06:00:00",
				"end_datetime": f"{day} 14:00:00",
				"status": "Closed",
				"cancelled": 0,
				"crew": [{
					"employee": WORKER,
					"employee_name": NAMES[WORKER],
					"joined_at": None,
					"left_at": None,
				}],
			})
		STORE.seed("Farm Shift", rows)

	def test_hours_become_a_slip_with_employer_taxes_and_then_a_balanced_entry(self):
		self._seed_shifts()
		self.tool_data("create_salary_structure", {
			"employee": WORKER, "company": MAIN, "pay_type": "Hourly",
			"base_rate": 20.0, "effective_from": "2025-01-01",
		})
		run = self.tool_data("run_payroll_for_period", {
			"company": MAIN,
			"pay_period_start": PERIOD_START,
			"pay_period_end": PERIOD_END,
			"employee": WORKER,
		})
		self.assertEqual(run["status"], "Calculated")

		entry = self.tool_data("get_payroll_entry", {"name": run["name"]})
		worker = next(row for row in entry["slips"] if row["employee"] == WORKER)
		self.assertGreater(worker["gross_pay"], 0)
		# 24 hours at $20 is $480; the employer's 6.2% is $29.76 and it is
		# recorded rather than recomputed at posting time.
		self.assertEqual(worker["social_security_employer"], round(worker["gross_pay"] * 0.062, 2))
		self.assertEqual(worker["medicare_employer"], round(worker["gross_pay"] * 0.0145, 2))
		self.assertGreater(worker["state_unemployment"], 0)
		self.assertGreater(worker["state_employer_other"], 0)

		data = self.tool_data("post_payroll_to_gl", {"payroll_entry": run["name"]})
		self.assertEqual(data["entry_count"], 1)
		self.assertEqual(data["total_debit"], data["total_credit"])
		# Gross plus every employer tax is what the wages actually cost.
		self.assertEqual(
			data["totals"]["Gross Pay"] + worker["total_employer_taxes"],
			data["total_debit"],
		)

	def test_a_worker_with_no_suta_rate_configured_pays_none(self):
		"""The rate defaults to zero, so no site gains a charge on upgrading."""
		STORE.tables["State Tax Configuration"]["STC-OR-2025"]["suta_rate"] = 0
		STORE.commit()
		self._seed_shifts()
		self.tool_data("create_salary_structure", {
			"employee": WORKER, "company": MAIN, "pay_type": "Hourly",
			"base_rate": 20.0, "effective_from": "2025-01-01",
		})
		run = self.tool_data("run_payroll_for_period", {
			"company": MAIN, "pay_period_start": PERIOD_START,
			"pay_period_end": PERIOD_END, "employee": WORKER,
		})
		entry = self.tool_data("get_payroll_entry", {"name": run["name"]})
		worker = next(row for row in entry["slips"] if row["employee"] == WORKER)
		self.assertEqual(worker["state_unemployment"], 0.0)


# ── The tools ─────────────────────────────────────────────────────────────


class TheTools(PayrollGLTestCase):
	"""Two reads on by default, two writes off, each refused by name."""

	def test_the_reads_are_on_by_default_and_the_writes_are_not(self):
		from erpnext_mcp import registry

		fields = {field["fieldname"]: field for field in self.settings_meta()["fields"]}
		self.assertEqual(fields["allow_get_payroll_account_mapping"]["default"], "1")
		self.assertEqual(fields["allow_preview_payroll_gl"]["default"], "1")
		self.assertEqual(fields["allow_configure_payroll_accounts"]["default"], "0")
		self.assertEqual(fields["allow_post_payroll_to_gl"]["default"], "0")
		self.assertIn("get_payroll_account_mapping", registry.READ_TOOLS)
		self.assertIn("preview_payroll_gl", registry.READ_TOOLS)
		self.assertIn("configure_payroll_accounts", registry.MUTATING_TOOLS)
		self.assertIn("post_payroll_to_gl", registry.MUTATING_TOOLS)

	def settings_meta(self):
		import json
		import pathlib

		path = (
			pathlib.Path(__file__).resolve().parents[1]
			/ "erpnext_mcp" / "erpnext_mcp" / "doctype"
			/ "erpnext_mcp_settings" / "erpnext_mcp_settings.json"
		)
		return json.loads(path.read_text())

	def test_each_tool_is_refused_by_name_when_its_switch_is_off(self):
		self.seed_run()
		for tool in (
			"get_payroll_account_mapping",
			"preview_payroll_gl",
			"configure_payroll_accounts",
			"post_payroll_to_gl",
		):
			with self.subTest(tool=tool):
				self.configure(enabled=1, **{**ON, f"allow_{tool}": 0})
				error = self.tool_error(tool, {
					"company": MAIN,
					"payroll_entry": "PAY-2025-00001",
					"components": FULL_MAPPING,
				})
				self.assertIn(tool, error)

	def test_the_kill_switch_stops_all_four(self):
		self.configure(enabled=0, **ON)
		for tool in (
			"get_payroll_account_mapping",
			"preview_payroll_gl",
			"configure_payroll_accounts",
			"post_payroll_to_gl",
		):
			with self.subTest(tool=tool):
				_body, status = self.call("tools/call", {
					"name": tool,
					"arguments": {"company": MAIN, "payroll_entry": "PAY-2025-00001"},
				})
				self.assertEqual(status, 404)

	def test_posting_is_audited(self):
		self.configure_mapping()
		self.seed_run()
		self.post()
		self.assertAudited("post_payroll_to_gl", "Success")

	def test_a_refused_posting_is_audited_too(self):
		self.seed_run()
		self.post_error()
		self.assertAudited("post_payroll_to_gl")


def frappe_exists(name: str) -> bool:
	return name in STORE.tables.get("Journal Entry", {})
