# SPDX-License-Identifier: MIT
"""Payroll wages split across cost centers — v0.101.0, item 18.

THE CLAIM BEHIND THE RELEASE is that a farm should be able to ask its P&L what
one block's labour cost. v0.40.0 put payroll into the ledger and put ONE cost
center on every line of it, so the largest cost a block carries was the one the
ledger knew least about: "wages, somewhere on the farm, $41,300".

The record to answer it from already existed and nothing read it. A Farm Task
names its block through the `location_doctype`/`location` pair, a `Field` names
its Cost Center (`link_field_to_cost_center`, v0.53.0), and a Farm Task
Assignment records the minutes (`actual_duration_minutes`, the segment sum since
v0.79.0). This release walks that chain at posting time.

SIX CLAIMS.

1. `TheSplitIsProportionalToTheHours` — minutes per block become shares; paid
   time the tasks do not place becomes a share of its own on the blanket cost
   center rather than inflating the blocks that ARE placed.

2. `TheSplitIsExactToTheCent` — largest remainder, so a third of $1,000 three
   times is $1,000 and not $999.99, on positive and negative amounts alike.

3. `OnlyTheExpenseSideSplits` — the debits carry blocks and the credits carry
   the blanket cost center, because a withholding liability is not a block's
   cost. The entry balances either way.

4. `TheTwoModesBookTheSameBlocks` — consolidated and per-employee agree to the
   cent, per cost center, INCLUDING where an employer tax has capped out for one
   worker and not another.

5. `TheChainIsTaskToBlockToCostCenter` — end to end on a site: a task on a
   block with a postable Cost Center splits, and each of the five ways that
   chain can be broken falls back rather than failing.

6. `NothingElseAboutThePostingChanged` — same totals, same balance, same entry
   count, same idempotency; and a site with no task data posts what it always
   posted.
"""

from erpnext_mcp import payroll_gl

from .fixtures import MAIN, MAIN_ABBR, OTHER, V12TestCase, cost_center, install_hrms
from .harness import STORE
from .test_payroll_gl import (
	FULL_MAPPING,
	NAMES,
	ON,
	PERIOD_END,
	PERIOD_START,
	PICKER,
	ROWS,
	TAX_EXPENSE,
	WAGE_EXPENSE,
	WORKER,
	slip,
)

#: Two leaf cost centers of the fixture's own `Operations` group, one per block.
#: `Field Work` ships in the fixture and is the blanket — the one the mapping
#: carries and the one unattributed time falls to — so the three are distinct
#: and a line landing on the wrong one is visible rather than plausible.
BLANKET = cost_center("Field Work")
BLOCK_7_CC = f"120 - Block 7 - {MAIN_ABBR}"
BLOCK_9_CC = f"130 - Block 9 - {MAIN_ABBR}"
GROUP_CC = cost_center("Operations")
DISABLED_CC = cost_center("Retired Depot")
OTHER_COMPANY_CC = cost_center("Field Work", "OTHER")

BLOCK_7 = "BLOCK-7-NORTH"
BLOCK_9 = "BLOCK-9-SOUTH"
BLOCK_NO_CC = "BLOCK-UNLINKED"
BLOCK_GROUP = "BLOCK-ON-A-HEADING"
BLOCK_DISABLED = "BLOCK-RETIRED"

RUN = "PAY-2025-00001"


def shares(*pairs):
	"""`(cost center, share)` pairs as `split_amount` takes them."""
	return [{"cost_center": center, "share": share} for center, share in pairs]


# ── Claim 1: the proportions ──────────────────────────────────────────────


class TheSplitIsProportionalToTheHours(V12TestCase):
	"""Minutes per block in, shares out — and the shortfall is its own share."""

	def test_two_blocks_split_by_their_minutes(self):
		computed = payroll_gl.cost_center_shares(
			[
				{"cost_center": BLOCK_7_CC, "minutes": 120, "block": BLOCK_7},
				{"cost_center": BLOCK_9_CC, "minutes": 60, "block": BLOCK_9},
			],
			paid_minutes=180,
			fallback_cost_center=BLANKET,
		)
		self.assertEqual(
			[(row["cost_center"], row["share"]) for row in computed["shares"]],
			[(BLOCK_7_CC, 0.666667), (BLOCK_9_CC, 0.333333)],
		)
		self.assertEqual(computed["unattributed_minutes"], 0.0)

	def test_several_tasks_on_one_block_are_one_share(self):
		computed = payroll_gl.cost_center_shares(
			[
				{"cost_center": BLOCK_7_CC, "minutes": 60, "block": BLOCK_7},
				{"cost_center": BLOCK_7_CC, "minutes": 60, "block": BLOCK_7},
				{"cost_center": BLOCK_9_CC, "minutes": 120, "block": BLOCK_9},
			],
			paid_minutes=240,
		)
		self.assertEqual(len(computed["shares"]), 2)
		seven = computed["shares"][0]
		self.assertEqual(seven["minutes"], 120.0)
		self.assertEqual(seven["task_count"], 2)

	def test_paid_time_the_tasks_do_not_place_is_its_own_share(self):
		"""Two hours on Block 7 out of an eight-hour day is a quarter, not all."""
		computed = payroll_gl.cost_center_shares(
			[{"cost_center": BLOCK_7_CC, "minutes": 120, "block": BLOCK_7}],
			paid_minutes=480,
			fallback_cost_center=BLANKET,
		)
		self.assertEqual(computed["unattributed_minutes"], 360.0)
		self.assertEqual(
			{row["cost_center"]: row["share"] for row in computed["shares"]},
			{BLANKET: 0.75, BLOCK_7_CC: 0.25},
		)
		self.assertEqual(computed["coverage"], 0.25)

	def test_a_slip_with_no_hours_is_split_by_the_attributed_time_alone(self):
		"""Nothing to measure against is not the same as a shortfall of everything."""
		computed = payroll_gl.cost_center_shares(
			[
				{"cost_center": BLOCK_7_CC, "minutes": 120, "block": BLOCK_7},
				{"cost_center": BLOCK_9_CC, "minutes": 120, "block": BLOCK_9},
			],
			paid_minutes=0,
			fallback_cost_center=BLANKET,
		)
		self.assertEqual(computed["unattributed_minutes"], 0.0)
		self.assertIsNone(computed["coverage"])
		self.assertEqual([row["share"] for row in computed["shares"]], [0.5, 0.5])

	def test_more_task_time_than_paid_time_produces_no_negative_share(self):
		computed = payroll_gl.cost_center_shares(
			[{"cost_center": BLOCK_7_CC, "minutes": 600, "block": BLOCK_7}],
			paid_minutes=480,
			fallback_cost_center=BLANKET,
		)
		self.assertEqual(computed["unattributed_minutes"], 0.0)
		self.assertEqual([row["cost_center"] for row in computed["shares"]], [BLOCK_7_CC])
		self.assertEqual(computed["shares"][0]["share"], 1.0)

	def test_time_on_a_block_with_no_cost_center_is_unattributed_not_dropped(self):
		"""Dropping it would hand its share to the blocks that ARE placed."""
		computed = payroll_gl.cost_center_shares(
			[
				{"cost_center": BLOCK_7_CC, "minutes": 120, "block": BLOCK_7},
				{"cost_center": "", "minutes": 120, "block": BLOCK_NO_CC},
			],
			paid_minutes=240,
			fallback_cost_center=BLANKET,
		)
		self.assertEqual(computed["unplaced_minutes"], 120.0)
		self.assertEqual(
			{row["cost_center"]: row["share"] for row in computed["shares"]},
			{BLOCK_7_CC: 0.5, BLANKET: 0.5},
		)

	def test_the_blanket_merges_where_it_is_also_a_blocks_cost_center(self):
		"""One cost center is one share, or the split could hand it two cents."""
		computed = payroll_gl.cost_center_shares(
			[{"cost_center": BLANKET, "minutes": 120, "block": BLOCK_7}],
			paid_minutes=480,
			fallback_cost_center=BLANKET,
		)
		self.assertEqual(len(computed["shares"]), 1)
		self.assertEqual(computed["shares"][0]["minutes"], 480.0)
		self.assertEqual(computed["shares"][0]["share"], 1.0)

	def test_no_minutes_anywhere_produces_no_shares_at_all(self):
		computed = payroll_gl.cost_center_shares([], paid_minutes=0, fallback_cost_center=BLANKET)
		self.assertEqual(computed["shares"], [])

	def test_shares_are_ordered_largest_first_and_ties_by_name(self):
		computed = payroll_gl.cost_center_shares(
			[
				{"cost_center": BLOCK_9_CC, "minutes": 60, "block": BLOCK_9},
				{"cost_center": BLOCK_7_CC, "minutes": 60, "block": BLOCK_7},
				{"cost_center": BLANKET, "minutes": 300, "block": "B"},
			],
			paid_minutes=420,
		)
		self.assertEqual(
			[row["cost_center"] for row in computed["shares"]],
			[BLANKET, BLOCK_7_CC, BLOCK_9_CC],
		)


# ── Claim 2: the arithmetic ───────────────────────────────────────────────


class TheSplitIsExactToTheCent(V12TestCase):
	"""Largest remainder. A journal entry a cent out does not post."""

	def test_a_third_three_times_is_the_whole_amount(self):
		pieces = payroll_gl.split_amount(1000.00, shares(("A", 1 / 3), ("B", 1 / 3), ("C", 1 / 3)))
		self.assertEqual(round(sum(row["amount"] for row in pieces), 2), 1000.00)
		self.assertEqual(sorted(row["amount"] for row in pieces), [333.33, 333.33, 333.34])

	def test_the_spare_cent_goes_to_the_largest_remainder(self):
		pieces = payroll_gl.split_amount(100.00, shares(("A", 0.5), ("B", 0.3), ("C", 0.2)))
		self.assertEqual(
			{row["cost_center"]: row["amount"] for row in pieces}, {"A": 50.0, "B": 30.0, "C": 20.0}
		)

	def test_a_negative_amount_splits_the_same_way_with_its_sign_back(self):
		pieces = payroll_gl.split_amount(-1000.00, shares(("A", 1 / 3), ("B", 1 / 3), ("C", 1 / 3)))
		self.assertEqual(round(sum(row["amount"] for row in pieces), 2), -1000.00)
		self.assertTrue(all(row["amount"] < 0 for row in pieces))

	def test_it_is_exact_over_a_wide_range_of_amounts_and_share_counts(self):
		"""The property, checked rather than asserted about one example."""
		for count in range(2, 8):
			weights = shares(*[(f"CC{i}", (i + 1)) for i in range(count)])
			for cents in range(9_995, 10_015):
				amount = round(cents / 100.0, 2)
				pieces = payroll_gl.split_amount(amount, weights)
				self.assertEqual(
					round(sum(row["amount"] for row in pieces), 2),
					amount,
					f"{count} shares of {amount}",
				)

	def test_unnormalised_shares_are_normalised_rather_than_refused(self):
		pieces = payroll_gl.split_amount(90.00, shares(("A", 2.0), ("B", 1.0)))
		self.assertEqual({row["cost_center"]: row["amount"] for row in pieces}, {"A": 60.0, "B": 30.0})

	def test_no_shares_splits_nothing(self):
		self.assertEqual(payroll_gl.split_amount(100.0, []), [])
		self.assertEqual(payroll_gl.split_amount(100.0, shares(("A", 0.0))), [])

	def test_a_share_too_small_to_earn_a_cent_produces_no_line(self):
		pieces = payroll_gl.split_amount(1.00, shares(("A", 0.9999), ("B", 0.0001)))
		self.assertEqual([row["cost_center"] for row in pieces], ["A"])
		self.assertEqual(round(sum(row["amount"] for row in pieces), 2), 1.00)


# ── Claim 3: which side splits ────────────────────────────────────────────


class OnlyTheExpenseSideSplits(V12TestCase):
	"""A block carries the wage. It does not carry the payroll liability."""

	def setUp(self):
		super().setUp()
		self.allocation = payroll_gl.cost_center_shares(
			[
				{"cost_center": BLOCK_7_CC, "minutes": 180, "block": BLOCK_7},
				{"cost_center": BLOCK_9_CC, "minutes": 60, "block": BLOCK_9},
			],
			paid_minutes=240,
			fallback_cost_center=BLANKET,
		)["shares"]
		self.entry = payroll_gl.build_journal_entry(
			slip(),
			ROWS,
			company=MAIN,
			cost_center=BLANKET,
			allocation=self.allocation,
		)

	def test_the_expense_components_are_exactly_the_ones_with_a_debit_side(self):
		self.assertEqual(
			set(payroll_gl.EXPENSE_COMPONENTS),
			{row["component"] for row in payroll_gl.COMPONENTS if "debit" in row["sides"]},
		)
		self.assertIn("Gross Pay", payroll_gl.EXPENSE_COMPONENTS)
		self.assertNotIn("Net Pay", payroll_gl.EXPENSE_COMPONENTS)
		self.assertNotIn("Federal Tax", payroll_gl.EXPENSE_COMPONENTS)

	def test_the_wage_expense_is_one_line_per_block(self):
		wage = [line for line in self.entry["accounts"] if line["account"] == WAGE_EXPENSE]
		self.assertEqual(
			{line["cost_center"]: line["debit"] for line in wage},
			{BLOCK_7_CC: 750.00, BLOCK_9_CC: 250.00},
		)

	def test_the_wage_lines_add_back_to_gross(self):
		wage = [line for line in self.entry["accounts"] if line["account"] == WAGE_EXPENSE]
		self.assertEqual(round(sum(line["debit"] for line in wage), 2), 1000.00)

	def test_every_credit_keeps_the_blanket_cost_center(self):
		credits = [line for line in self.entry["accounts"] if "credit" in line]
		self.assertTrue(credits)
		self.assertEqual({line["cost_center"] for line in credits}, {BLANKET})

	def test_the_employer_tax_expense_splits_and_its_liability_does_not(self):
		expense = [
			line for line in self.entry["accounts"] if line["account"] == TAX_EXPENSE and "debit" in line
		]
		self.assertEqual({line["cost_center"] for line in expense}, {BLOCK_7_CC, BLOCK_9_CC})
		payable = [
			line for line in self.entry["accounts"] if line["account"] == TAX_EXPENSE and "credit" in line
		]
		self.assertEqual(payable, [])

	def test_it_still_balances(self):
		self.assertTrue(self.entry["balanced"], self.entry["difference"])
		self.assertEqual(self.entry["total_debit"], self.entry["total_credit"])

	def test_the_split_is_reported_beside_the_lines(self):
		reported = {row["cost_center"]: row["amount"] for row in self.entry["cost_center_split"]}
		# Every component is split on its OWN largest remainder, so the totals
		# land within a cent per component of three quarters and one quarter
		# rather than on it exactly. What has to be exact is the sum: the debits
		# of a payroll entry are gross plus the employer taxes, and if the split
		# lost a cent of that the entry would not balance.
		self.assertEqual(round(sum(reported.values()), 2), self.entry["total_debit"])
		self.assertAlmostEqual(reported[BLOCK_7_CC], 0.75 * 1119.5, delta=0.05)
		self.assertAlmostEqual(reported[BLOCK_9_CC], 0.25 * 1119.5, delta=0.05)

	def test_the_component_breakdown_says_where_gross_went(self):
		gross = next(row for row in self.entry["components"] if row["component"] == "Gross Pay")
		self.assertEqual(
			{row["cost_center"]: row["amount"] for row in gross["cost_centers"]},
			{BLOCK_7_CC: 750.00, BLOCK_9_CC: 250.00},
		)
		net = next(row for row in self.entry["components"] if row["component"] == "Net Pay")
		self.assertNotIn("cost_centers", net)

	def test_no_allocation_is_the_entry_this_module_always_built(self):
		"""The negative control. Without shares, nothing about the entry moves."""
		plain = payroll_gl.build_journal_entry(slip(), ROWS, company=MAIN, cost_center=BLANKET)
		self.assertEqual({line["cost_center"] for line in plain["accounts"]}, {BLANKET})
		self.assertEqual(plain["total_debit"], self.entry["total_debit"])
		self.assertEqual(plain["cost_center_split"], [])
		self.assertLess(len(plain["accounts"]), len(self.entry["accounts"]))

	def test_dropping_the_employer_half_drops_its_split_too(self):
		entry = payroll_gl.build_journal_entry(
			slip(),
			ROWS,
			company=MAIN,
			cost_center=BLANKET,
			allocation=self.allocation,
			include_employer=False,
		)
		self.assertTrue(entry["balanced"])
		self.assertEqual(
			round(sum(row["amount"] for row in entry["cost_center_split"]), 2),
			1000.00,
		)


# ── Claim 4: the two modes ────────────────────────────────────────────────


class TheTwoModesBookTheSameBlocks(V12TestCase):
	"""Consolidated is the sum of the per-employee entries, per cost center."""

	def setUp(self):
		super().setUp()
		# Two workers whose blocks barely overlap, and whose EMPLOYER taxes are
		# not in the same ratio as their gross: Beto has exhausted the FUTA wage
		# base and Ana has not. Blending the run into one set of proportions —
		# the cheap way to do this — would book Ana's FUTA onto Beto's block.
		self.slips = [
			slip(WORKER, gross=1000.0, futa=6.0, total_hours=10.0),
			slip(PICKER, gross=1000.0, futa=0.0, total_hours=10.0),
		]
		self.allocations = {
			WORKER: payroll_gl.cost_center_shares(
				[{"cost_center": BLOCK_7_CC, "minutes": 600, "block": BLOCK_7}],
				paid_minutes=600,
			)["shares"],
			PICKER: payroll_gl.cost_center_shares(
				[{"cost_center": BLOCK_9_CC, "minutes": 600, "block": BLOCK_9}],
				paid_minutes=600,
			)["shares"],
		}

	def plan(self, mode):
		return payroll_gl.build_payroll_journal_entries(
			self.slips,
			ROWS,
			company=MAIN,
			cost_center=BLANKET,
			mode=mode,
			allocations=self.allocations,
		)

	def test_both_modes_book_the_same_money_to_the_same_cost_centers(self):
		consolidated = self.plan("consolidated")["cost_center_split"]
		per_employee = self.plan("per_employee")["cost_center_split"]
		self.assertEqual(consolidated, per_employee)

	def test_the_capped_employer_tax_stays_on_its_own_workers_block(self):
		"""The negative control for blending: only Block 7 carries the FUTA."""
		entry = self.plan("consolidated")["journal_entries"][0]
		split = {row["cost_center"]: row["amount"] for row in entry["cost_center_split"]}
		self.assertEqual(round(split[BLOCK_7_CC] - split[BLOCK_9_CC], 2), 6.00)

	def test_both_modes_still_balance(self):
		for mode in ("consolidated", "per_employee"):
			plan = self.plan(mode)
			self.assertTrue(plan["balanced"], mode)
			self.assertEqual(plan["total_debit"], plan["total_credit"], mode)

	def test_the_split_totals_add_back_to_the_expense_side(self):
		plan = self.plan("consolidated")
		expense = sum(plan["totals"][name] for name in payroll_gl.EXPENSE_COMPONENTS)
		self.assertEqual(round(sum(row["amount"] for row in plan["cost_center_split"]), 2), round(expense, 2))

	def test_an_employee_with_no_allocation_falls_to_the_blanket(self):
		plan = payroll_gl.build_payroll_journal_entries(
			self.slips,
			ROWS,
			company=MAIN,
			cost_center=BLANKET,
			mode="consolidated",
			allocations={WORKER: self.allocations[WORKER]},
		)
		split = {row["cost_center"]: row["amount"] for row in plan["cost_center_split"]}
		self.assertIn(BLANKET, split)
		self.assertNotIn(BLOCK_9_CC, split)

	def test_a_run_with_no_allocations_at_all_reports_no_split(self):
		plan = payroll_gl.build_payroll_journal_entries(
			self.slips, ROWS, company=MAIN, cost_center=BLANKET, mode="consolidated"
		)
		self.assertFalse(plan["split_by_cost_center"])
		self.assertEqual(plan["cost_center_split"], [])


# ── The site fixture ──────────────────────────────────────────────────────


class CostCenterSplitTestCase(V12TestCase):
	"""A payroll run, two blocks, and the dispatch record that joins them."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		self._seed_accounts()
		self._seed_cost_centers()
		self._seed_employees()
		self._seed_blocks()
		self.tool_data(
			"configure_payroll_accounts",
			{"company": MAIN, "components": FULL_MAPPING, "cost_center": BLANKET},
		)

	# -- seeds ---------------------------------------------------------------

	def _seed_accounts(self):
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
		rows = []
		counter = 900
		for number, name, root, is_group in chart:
			counter += 2
			rows.append(
				{
					"name": f"{number} - {name} - {MAIN_ABBR}",
					"account_name": name,
					"account_number": number,
					"is_group": is_group,
					"root_type": root,
					"account_currency": "USD",
					"disabled": 0,
					"company": MAIN,
					"lft": counter,
					"rgt": counter + 1,
				}
			)
		STORE.seed("Account", rows)

	def _seed_cost_centers(self):
		"""Two more leaves under the fixture's own Operations group."""
		existing = list(STORE.rows("Cost Center"))
		counter = max(int(row.get("rgt") or 0) for row in existing) + 1
		for number, name in (("120", "Block 7"), ("130", "Block 9")):
			counter += 2
			existing.append(
				{
					"name": f"{number} - {name} - {MAIN_ABBR}",
					"cost_center_name": name,
					"cost_center_number": number,
					"parent_cost_center": GROUP_CC,
					"is_group": 0,
					"disabled": 0,
					"company": MAIN,
					"lft": counter,
					"rgt": counter + 1,
				}
			)
		STORE.seed("Cost Center", existing)

	def _seed_employees(self):
		STORE.seed(
			"Employee",
			[
				{
					"name": name,
					"employee_name": NAMES[name],
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-01-15",
				}
				for name in NAMES
			],
		)

	def _seed_blocks(self):
		"""Five blocks: two postable, and one of each way the chain can break."""
		STORE.seed(
			"Field",
			[
				{
					"name": BLOCK_7,
					"field_name": "Block 7 North",
					"owning_entity": MAIN,
					"cost_center": BLOCK_7_CC,
				},
				{
					"name": BLOCK_9,
					"field_name": "Block 9 South",
					"owning_entity": MAIN,
					"cost_center": BLOCK_9_CC,
				},
				{"name": BLOCK_NO_CC, "field_name": "Unlinked", "owning_entity": MAIN, "cost_center": ""},
				{
					"name": BLOCK_GROUP,
					"field_name": "On a heading",
					"owning_entity": MAIN,
					"cost_center": GROUP_CC,
				},
				{
					"name": BLOCK_DISABLED,
					"field_name": "Retired",
					"owning_entity": MAIN,
					"cost_center": DISABLED_CC,
				},
			],
		)

	def seed_tasks(self, rows):
		"""`(employee, block_or_None, minutes)` triples as tasks and assignments.

		`block_or_None` may also be a `(doctype, docname)` pair, which is how a
		task raised against a parcel or an irrigation zone is written — real work
		on real ground that is simply not a block.
		"""
		tasks, assignments = [], []
		for index, (employee, where, minutes) in enumerate(rows, start=1):
			task = f"TASK-{index:04d}"
			if isinstance(where, tuple):
				location_doctype, location = where
			else:
				location_doctype, location = ("Field", where) if where else ("", "")
			tasks.append(
				{
					"name": task,
					"task_name": f"Job {index}",
					"company": MAIN,
					"state": "Completed",
					"location_doctype": location_doctype,
					"location": location,
				}
			)
			assignments.append(
				{
					"name": f"ASSIGN-{index:04d}",
					"task": task,
					"assigned_to": employee,
					"company": MAIN,
					"state": "Completed",
					"started_at": f"{PERIOD_START} 07:00:00",
					"completed_at": f"{PERIOD_START} 08:00:00",
					"actual_duration_minutes": minutes,
				}
			)
		STORE.seed("Farm Task", tasks)
		STORE.seed("Farm Task Assignment", assignments)

	def seed_run(self, slips=None, status="Calculated", name=RUN):
		rows = slips if slips is not None else [slip(WORKER, total_hours=8.0)]
		child = []
		for index, row in enumerate(rows, start=1):
			entry = {
				key: value
				for key, value in row.items()
				if key not in ("pay_period_start", "pay_period_end", "company")
			}
			entry["name"] = f"{name}-slip-{index}"
			entry["parent"] = name
			entry["parenttype"] = "Farm Payroll Entry"
			entry["parentfield"] = "slips"
			entry["minimum_wage_check"] = 1
			child.append(entry)

		STORE.seed(
			"Farm Payroll Entry",
			[
				{
					"name": name,
					"company": MAIN,
					"pay_period_start": PERIOD_START,
					"pay_period_end": PERIOD_END,
					"pay_frequency": "Biweekly",
					"status": status,
					"total_gross": round(sum(row["gross_pay"] for row in rows), 2),
					"total_deductions": round(sum(row["total_deductions"] for row in rows), 2),
					"total_net": round(sum(row["net_pay"] for row in rows), 2),
					"employee_count": len(rows),
					"gl_status": "Not Posted",
					"slips": child,
					"gl_postings": [],
				}
			],
		)
		return name

	# -- shorthands ----------------------------------------------------------

	def preview(self, **extra):
		return self.tool_data("preview_payroll_gl", {"payroll_entry": RUN, **extra})

	def post(self, **extra):
		return self.tool_data("post_payroll_to_gl", {"payroll_entry": RUN, **extra})

	def journal_lines(self, journal_entry):
		return STORE.tables["Journal Entry"][journal_entry]["accounts"]

	def wage_lines(self, journal_entry):
		return [row for row in self.journal_lines(journal_entry) if row["account"] == WAGE_EXPENSE]


# ── Claim 5: the chain ────────────────────────────────────────────────────


class TheChainIsTaskToBlockToCostCenter(CostCenterSplitTestCase):
	"""Farm Task → Field → Cost Center, and every way it can be broken."""

	def test_the_wage_lands_on_the_blocks_the_tasks_name(self):
		self.seed_tasks([(WORKER, BLOCK_7, 360), (WORKER, BLOCK_9, 120)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		self.assertEqual(
			{row["cost_center"]: row["debit"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLOCK_7_CC: 750.00, BLOCK_9_CC: 250.00},
		)

	def test_the_preview_says_the_same_thing_and_writes_nothing(self):
		self.seed_tasks([(WORKER, BLOCK_7, 360), (WORKER, BLOCK_9, 120)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		before = len(STORE.rows("Journal Entry"))
		previewed = self.preview()["cost_center_split"]
		self.assertEqual(len(STORE.rows("Journal Entry")), before)
		self.assertEqual(previewed, self.post()["cost_center_split"])

	def test_the_minutes_behind_every_share_are_reported(self):
		self.seed_tasks([(WORKER, BLOCK_7, 360), (WORKER, BLOCK_9, 120)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		report = self.preview()["cost_center_allocation"]
		self.assertTrue(report["available"])
		self.assertEqual(report["task_count"], 2)
		row = next(entry for entry in report["employees"] if entry["employee"] == WORKER)
		self.assertEqual(row["paid_minutes"], 480.0)
		self.assertEqual(row["attributed_minutes"], 480.0)
		self.assertEqual(
			{share["cost_center"]: share["minutes"] for share in row["shares"]},
			{BLOCK_7_CC: 360.0, BLOCK_9_CC: 120.0},
		)

	def test_paid_time_no_task_placed_falls_to_the_mapping_cost_center(self):
		"""Two hours dispatched out of eight paid is a quarter of the wage."""
		self.seed_tasks([(WORKER, BLOCK_7, 120)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		self.assertEqual(
			{row["cost_center"]: row["debit"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET: 750.00, BLOCK_7_CC: 250.00},
		)

	def test_a_task_on_something_that_is_not_a_block_does_not_split(self):
		self.seed_tasks([(WORKER, ("Parcel", "PARCEL-1"), 480)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		self.assertEqual(
			{row["cost_center"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET},
		)
		self.assertFalse(data["cost_center_allocation"]["available"])

	def test_a_block_with_no_cost_center_is_named_rather_than_guessed_at(self):
		self.seed_tasks([(WORKER, BLOCK_7, 240), (WORKER, BLOCK_NO_CC, 240)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		report = data["cost_center_allocation"]
		self.assertEqual(
			[row["block"] for row in report["blocks_without_cost_center"]],
			[BLOCK_NO_CC],
		)
		self.assertEqual(
			{row["cost_center"]: row["debit"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET: 500.00, BLOCK_7_CC: 500.00},
		)

	def test_a_block_pointed_at_a_group_cost_center_is_refused_not_posted(self):
		"""ERPNext would reject the whole entry. Falling back posts the payroll."""
		self.seed_tasks([(WORKER, BLOCK_GROUP, 480)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		refused = data["cost_center_allocation"]["blocks_without_cost_center"]
		self.assertEqual([row["block"] for row in refused], [BLOCK_GROUP])
		self.assertIn("leaf", refused[0]["why"])
		self.assertEqual(
			{row["cost_center"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET},
		)

	def test_a_block_pointed_at_a_disabled_cost_center_is_refused_too(self):
		self.seed_tasks([(WORKER, BLOCK_DISABLED, 480)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		self.assertEqual(
			[row["block"] for row in data["cost_center_allocation"]["blocks_without_cost_center"]],
			[BLOCK_DISABLED],
		)

	def test_a_task_outside_the_pay_period_is_not_counted(self):
		self.seed_tasks([(WORKER, BLOCK_7, 480)])
		STORE.seed(
			"Farm Task Assignment",
			[
				dict(row, started_at="2025-01-04 07:00:00", completed_at="2025-01-04 08:00:00")
				for row in STORE.rows("Farm Task Assignment")
			],
		)
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		self.assertEqual(data["cost_center_allocation"]["assignment_count"], 0)
		self.assertEqual(
			{row["cost_center"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET},
		)

	def test_another_workers_task_does_not_move_this_workers_wage(self):
		self.seed_tasks([(PICKER, BLOCK_7, 480)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		data = self.post()
		self.assertEqual(
			{row["cost_center"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET},
		)

	def test_an_assignment_with_no_duration_falls_back_to_the_wall_clock(self):
		self.seed_tasks([(WORKER, BLOCK_7, 0)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		report = self.preview()["cost_center_allocation"]
		row = next(entry for entry in report["employees"] if entry["employee"] == WORKER)
		self.assertEqual(row["attributed_minutes"], 60.0)

	def test_a_run_where_only_one_worker_was_dispatched_splits_only_theirs(self):
		self.seed_tasks([(WORKER, BLOCK_7, 480)])
		self.seed_run([slip(WORKER, total_hours=8.0), slip(PICKER, gross=2000.0, total_hours=8.0)])
		data = self.post()
		lines = {row["cost_center"]: row["debit"] for row in self.wage_lines(data["journal_entries"][0])}
		self.assertEqual(lines, {BLANKET: 2000.00, BLOCK_7_CC: 1000.00})

	def test_the_audit_summary_names_the_cost_centers_the_wage_landed_on(self):
		"""What an operator scanning MCP Action Log a month later reads."""
		self.seed_tasks([(WORKER, BLOCK_7, 360), (WORKER, BLOCK_9, 120)])
		self.seed_run([slip(WORKER, total_hours=8.0)])
		self.post()
		summary = self.assertAudited("post_payroll_to_gl")["result_summary"]
		self.assertIn("expense split across 2 cost center(s)", summary)
		self.assertIn(BLOCK_7_CC, summary)

	def test_an_unsplit_posting_says_nothing_about_cost_centers(self):
		self.seed_run([slip(WORKER, total_hours=8.0)])
		self.post()
		self.assertNotIn("expense split", self.assertAudited("post_payroll_to_gl")["result_summary"])


# ── Claim 6: everything else is unchanged ─────────────────────────────────


class NothingElseAboutThePostingChanged(CostCenterSplitTestCase):
	"""The dimension moved. The money, the balance and the refusals did not."""

	def setUp(self):
		super().setUp()
		self.seed_tasks([(WORKER, BLOCK_7, 360), (WORKER, BLOCK_9, 120)])
		self.seed_run([slip(WORKER, total_hours=8.0)])

	def test_the_totals_are_identical_split_and_unsplit(self):
		split = self.preview()
		plain = self.preview(split_by_cost_center=False)
		self.assertEqual(split["total_debit"], plain["total_debit"])
		self.assertEqual(split["total_credit"], plain["total_credit"])
		self.assertEqual(split["totals"], plain["totals"])

	def test_turning_the_split_off_posts_what_this_tool_always_posted(self):
		"""The negative control for the whole release."""
		data = self.post(split_by_cost_center=False)
		self.assertFalse(data["split_by_cost_center"])
		self.assertEqual(data["cost_center_split"], [])
		self.assertEqual(
			{row["cost_center"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET},
		)

	def test_the_split_is_on_by_default(self):
		self.assertTrue(self.preview()["split_by_cost_center"])

	def test_the_entry_balances_on_the_site_too(self):
		data = self.post()
		rows = self.journal_lines(data["journal_entries"][0])
		debit = round(sum(float(row.get("debit") or 0) for row in rows), 2)
		credit = round(sum(float(row.get("credit") or 0) for row in rows), 2)
		self.assertEqual(debit, credit)
		self.assertEqual(debit, data["total_debit"])

	def test_it_is_still_one_consolidated_entry(self):
		self.assertEqual(self.post()["entry_count"], 1)

	def test_everything_it_creates_is_still_a_draft(self):
		data = self.post()
		for name in data["journal_entries"]:
			self.assertEqual(int(STORE.tables["Journal Entry"][name].get("docstatus") or 0), 0)

	def test_posting_twice_is_still_refused(self):
		self.post()
		self.assertIn("already", self.tool_error("post_payroll_to_gl", {"payroll_entry": RUN}).lower())

	def test_a_run_whose_blocks_name_no_cost_center_reports_why(self):
		STORE.seed("Field", [dict(row, cost_center="") for row in STORE.rows("Field")])
		report = self.preview()["cost_center_allocation"]
		self.assertFalse(report["available"])
		self.assertIn("nothing to split by", report["reason"])

	def test_a_cost_center_on_another_companys_tree_is_not_used(self):
		STORE.seed(
			"Field",
			[dict(row, cost_center=OTHER_COMPANY_CC) for row in STORE.rows("Field")],
		)
		data = self.post()
		self.assertEqual(
			{row["cost_center"] for row in self.wage_lines(data["journal_entries"][0])},
			{BLANKET},
		)
		self.assertTrue(data["cost_center_allocation"]["blocks_without_cost_center"])
		self.assertNotEqual(OTHER, MAIN)
