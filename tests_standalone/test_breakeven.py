# SPDX-License-Identifier: MIT
"""The breakeven calculator — v0.87.0.

WHAT THIS MODULE CLAIMS, AND WHAT WOULD MAKE THE CLAIM FALSE.

It claims that a farm can ask "what price do I need to break even?" and get an
answer that is arithmetically right, honest about what it guessed, and STABLE —
the same question asked two ways gets the same number.

It would be false if:

  * the packout slider moved the breakeven in the right direction by the wrong
    amount, which is the failure mode that looks exactly like an answer;
  * `compute_breakeven(packout_pct=60)` and the -25% packout row of
    `get_breakeven_sensitivity` disagreed, because then the tool contradicts
    itself and a grower has no way to know which half to believe;
  * an account classified by a guess were reported the same way as one somebody
    decided about;
  * a breakeven QUANTITY were reported where the contribution margin is
    negative, since the arithmetic limit there reads as a hard target rather
    than as an impossibility;
  * a market quotation per 20-lb carton were silently compared against a
    breakeven per 40-lb box.

Every class below is one of those.

THE FIXTURE IS AN ORCHARD, AND ITS NUMBERS ARE CHOSEN TO BE CHECKABLE BY HAND.
Sixty thousand fixed, forty thousand of picking, thirty thousand of packing, ten
thousand boxes at eighty percent. Every expected figure in this file can be
derived on paper from those four numbers, which is the only way to tell an
arithmetic bug from a fixture that drifted.
"""

import frappe

from erpnext_mcp.tools import breakeven

from .fixtures import MAIN, MAIN_ABBR, OTHER, SeededTestCase, cost_center
from .harness import STORE

#: The standalone harness freezes the clock at 2026-07-24. Every date in this
#: module therefore sits behind it — a quotation dated tomorrow is refused by the
#: controller on purpose, and a fixture that drifted past the frozen clock would
#: fail as though the refusal were broken.
ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_breakeven_analysis",
		"compute_breakeven",
		"get_breakeven_analysis",
		"list_breakeven_analyses",
		"get_breakeven_sensitivity",
	)
}

#: (account_name, number, account_type, amount posted in 2026)
#:
#: Deliberately a chart nobody has classified: every one of these is sorted by
#: the HEURISTIC unless a test says otherwise, which is what makes the
#: "it says what it guessed" class real rather than hypothetical.
ORCHARD = [
	("Picking Labor", "5200", "", 30000),
	("Hauling", "5210", "", 10000),
	("Packing Charges", "5300", "", 20000),
	("Cartons", "5310", "", 10000),
	("Orchard Rent", "5400", "", 50000),
	("Crop Insurance", "5410", "", 10000),
	("Income Tax", "5900", "Tax", 5000),
]

#: What the fixture adds up to, and the identities every expected number below
#: is derived from. Fixed 60,000 · harvest-basis 40,000 · sellable-basis 30,000
#: · excluded 5,000.
FIXED = 60000.0
HARVEST = 40000.0
SELLABLE = 30000.0

#: 10,000 units at 80% is 8,000 sellable, which makes both rates come out whole:
#: 4.00 per harvested unit and 3.75 per sellable one.
CROP = 10000.0
PACKOUT = 80.0
PRICE = 20.0
CULL = 1.0


def account(name: str, abbr: str = MAIN_ABBR) -> str:
	for account_name, number, *_rest in ORCHARD:
		if account_name == name:
			return f"{number} - {account_name} - {abbr}"
	raise KeyError(name)


def seed_orchard(company: str = MAIN, abbr: str = MAIN_ABBR, posting_date: str = "2026-06-30") -> None:
	"""Seven expense accounts and one posting into each. Additive to `seed_site`."""
	accounts = []
	entries = []
	for index, (account_name, number, account_type, amount) in enumerate(ORCHARD, start=100):
		docname = f"{number} - {account_name} - {abbr}"
		accounts.append(
			{
				"name": docname,
				"account_name": account_name,
				"account_number": number,
				"parent_account": f"Expenses - {abbr}",
				"is_group": 0,
				"root_type": "Expense",
				"account_type": account_type,
				"account_currency": "USD",
				"disabled": 0,
				"company": company,
				"lft": index * 2,
				"rgt": index * 2 + 1,
			}
		)
		entries.append(
			{
				"name": f"GL-BE-{abbr}-{number}",
				"account": docname,
				"posting_date": posting_date,
				"debit": amount,
				"credit": 0,
				"company": company,
				"is_cancelled": 0,
				"cost_center": cost_center("Field Work", abbr),
				"voucher_type": "Journal Entry",
				"voucher_no": "ACC-JV-2026-09000",
			}
		)
	STORE.seed("Account", accounts)
	STORE.seed("GL Entry", entries)


class BreakevenTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		seed_orchard()
		self.configure(**ALL_ON)

	def an_analysis(self, **overrides):
		payload = {
			"company": MAIN,
			"fiscal_year": "2026",
			"crop_type": "Gala",
			"analysis_name": "Southgate Gala",
			"expected_harvest_units": CROP,
			"packout_pct": PACKOUT,
			"expected_price": PRICE,
			"cull_credit_per_unit": CULL,
			"unit_label": "Box",
			# An explicit window rather than the fiscal year's, so the base
			# fixture's own February expense posting stays out of the arithmetic
			# and every figure below is derivable from the four orchard numbers.
			"from_date": "2026-06-01",
			"to_date": "2026-07-31",
		}
		payload.update(overrides)
		return self.tool_data("create_breakeven_analysis", payload)["analysis"]

	def a_computed_analysis(self, create=None, **compute):
		name = self.an_analysis(**(create or {}))["name"]
		return self.tool_data("compute_breakeven", {"name": name, **compute})


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheArithmeticIsRight(SeededTestCase):
	"""`compute_model` on its own, where every number is checkable on paper.

	Pure arithmetic, no site, no document — which is the point of the function
	having that shape. The cases here are the ones a document-shaped model makes
	awkward to reach: a negative contribution margin, a hundred-percent packout,
	a zero price.
	"""

	def model(self, **overrides):
		payload = {
			"fixed": FIXED,
			# 40,000 over 10,000 harvested, 30,000 over 8,000 sellable.
			"rate_harvest": 4.0,
			"rate_sellable": 3.75,
			"harvest_units": CROP,
			"packout_pct": PACKOUT,
			"price": PRICE,
			"cull_credit": CULL,
		}
		payload.update(overrides)
		return breakeven.compute_model(**payload)

	def test_the_variable_cost_per_box_carries_the_culls_picking(self):
		"""4.00/0.8 + 3.75. At 80% packout every box paid to pick 1.25 boxes."""
		self.assertAlmostEqual(self.model()["variable_cost_per_sellable_unit"], 8.75, places=4)

	def test_the_cull_credit_is_per_sellable_unit_not_per_cull(self):
		"""1.00 x 0.2 / 0.8 = 0.25. Each packed box brings a quarter of a cull's juice money."""
		self.assertAlmostEqual(self.model()["cull_credit_per_sellable_unit"], 0.25, places=4)

	def test_the_contribution_margin_is_price_plus_culls_less_variable(self):
		"""20 + 0.25 - 8.75."""
		self.assertAlmostEqual(self.model()["contribution_margin_per_unit"], 11.50, places=4)
		self.assertAlmostEqual(self.model()["contribution_margin_ratio"], 57.5, places=4)

	def test_the_breakeven_is_fixed_over_contribution(self):
		"""60,000 / 11.50, and the same point expressed as fruit on the tree."""
		model = self.model()
		self.assertAlmostEqual(model["breakeven_units"], 5217.3913, places=3)
		self.assertAlmostEqual(model["breakeven_harvest_units"], 6521.7391, places=3)

	def test_the_breakeven_price_covers_every_cost_at_the_expected_crop(self):
		"""(60,000 + 70,000 - 2,000) / 8,000 = 16.00 — the answer the tool exists for."""
		self.assertAlmostEqual(self.model()["breakeven_price"], 16.00, places=4)

	def test_the_projected_profit_and_the_safety_margin_agree_with_it(self):
		model = self.model()
		self.assertAlmostEqual(model["projected_profit"], 32000.0, places=2)
		self.assertAlmostEqual(model["margin_of_safety_pct"], 34.7826, places=3)

	def test_selling_at_the_breakeven_price_makes_exactly_nothing(self):
		"""The definition, asserted rather than assumed. Nothing else here checks
		the breakeven price against the profit formula it is supposed to invert."""
		at_breakeven = self.model(price=self.model()["breakeven_price"])
		self.assertAlmostEqual(at_breakeven["projected_profit"], 0.0, places=2)

	def test_packing_out_at_the_breakeven_packout_also_makes_exactly_nothing(self):
		"""59.0164% — and the same identity has to hold on the other axis."""
		required = self.model()["breakeven_packout_pct"]
		self.assertAlmostEqual(required, 59.0164, places=3)
		self.assertAlmostEqual(self.model(packout_pct=required)["projected_profit"], 0.0, places=1)

	def test_a_lighter_packout_raises_the_breakeven_price(self):
		"""THE WHOLE POINT OF THE TWO PILES. At 60% the picking bill lands on
		6,000 boxes instead of 8,000 while the packing bill shrinks with them, so
		the breakeven price rises to 19.75 — not to the 21.00 a single-pile model
		would report, and not to the 16.00 it would report if nothing moved."""
		lighter = self.model(packout_pct=60)
		self.assertAlmostEqual(lighter["breakeven_price"], 19.75, places=4)
		self.assertAlmostEqual(lighter["total_variable_harvest_cost"], 40000.0, places=2)
		self.assertAlmostEqual(lighter["total_variable_sellable_cost"], 22500.0, places=2)

	def test_the_harvest_pile_is_untouched_by_the_packout_and_the_packing_pile_is_not(self):
		"""Stated as its own assertion because it is the invariant the whole
		module rests on, and it is invisible in any single breakeven figure."""
		for packout in (100, 80, 60, 40):
			with self.subTest(packout=packout):
				model = self.model(packout_pct=packout)
				self.assertAlmostEqual(model["total_variable_harvest_cost"], 40000.0, places=2)
				self.assertAlmostEqual(
					model["total_variable_sellable_cost"], 3.75 * CROP * packout / 100.0, places=2
				)

	def test_a_negative_contribution_margin_has_no_breakeven_quantity_at_all(self):
		"""There is no such number. Reporting the arithmetic limit would render
		as a hard target rather than as the impossibility it is."""
		model = self.model(price=5.0)
		self.assertLess(model["contribution_margin_per_unit"], 0)
		self.assertIsNone(model["breakeven_units"])
		self.assertIsNone(model["breakeven_harvest_units"])
		self.assertIsNone(model["breakeven_revenue"])
		self.assertIsNone(model["margin_of_safety_pct"])
		self.assertIn("NO VOLUME EVER BREAKS EVEN", model["impossible"])

	def test_the_breakeven_price_still_exists_when_no_volume_does(self):
		"""And it is the number that matters there: how far the price has to come
		up before selling more helps at all."""
		self.assertAlmostEqual(self.model(price=5.0)["breakeven_price"], 16.00, places=4)

	def test_a_perfect_packout_makes_the_cull_credit_vanish_rather_than_divide_by_zero(self):
		model = self.model(packout_pct=100)
		self.assertEqual(model["cull_credit_per_sellable_unit"], 0.0)
		self.assertEqual(model["cull_credit_total"], 0.0)
		self.assertAlmostEqual(model["variable_cost_per_sellable_unit"], 7.75, places=4)

	def test_no_crop_is_answered_rather_than_divided_by(self):
		"""Every per-unit figure is a division by one of these two, and an
		infinity renders downstream as a large confident number."""
		for overrides in ({"harvest_units": 0}, {"packout_pct": 0}):
			with self.subTest(**overrides):
				model = self.model(**overrides)
				self.assertIsNone(model["breakeven_units"])
				self.assertEqual(model["projected_profit"], -FIXED)
				self.assertIn("no per-unit figure exists", model["impossible"])

	def test_a_zero_price_reports_no_ratio_rather_than_dividing_by_it(self):
		self.assertIsNone(self.model(price=0)["contribution_margin_ratio"])


# ── 2 ───────────────────────────────────────────────────────────────────────
class EveryLineSaysWhoClassifiedIt(BreakevenTestCase):
	"""Account, then override, then guess — and the source is on the row."""

	def lines(self, data):
		return {line["account_name"]: line for line in data["cost_lines"]}

	def test_the_heuristic_sorts_an_orchard_chart_nobody_has_classified(self):
		lines = self.lines(self.a_computed_analysis())
		self.assertEqual(lines["Picking Labor"]["cost_behavior"], "Variable")
		self.assertEqual(lines["Picking Labor"]["volume_basis"], "Harvested")
		self.assertEqual(lines["Cartons"]["cost_behavior"], "Variable")
		self.assertEqual(lines["Cartons"]["volume_basis"], "Sellable")
		self.assertEqual(lines["Orchard Rent"]["cost_behavior"], "Fixed")
		self.assertEqual(lines["Crop Insurance"]["cost_behavior"], "Fixed")

	def test_every_guessed_line_is_labelled_as_a_guess_and_counted(self):
		"""A breakeven resting on guesses is a different object from one resting
		on decisions, and the only place that difference is visible is here."""
		data = self.a_computed_analysis()
		guessed = [line for line in data["cost_lines"] if line["classification_source"] == "Heuristic"]
		self.assertEqual(len(guessed), 6)
		self.assertEqual(data["guessed_classification_count"], 6)
		for line in guessed:
			self.assertTrue(line["basis_note"].startswith("GUESSED:"))
		self.assertIn("CLASSIFIED BY GUESS", data["classification_note"])
		self.assertIn("classified BY GUESS", data["computation_warnings"])

	def test_income_tax_is_excluded_by_rule_and_is_not_a_guess(self):
		"""At breakeven there is no pre-tax income to tax. A model carrying the
		tax line would demand the farm cover a liability it does not have."""
		line = self.lines(self.a_computed_analysis())["Income Tax"]
		self.assertEqual(line["cost_behavior"], "Excluded")
		self.assertEqual(line["classification_source"], "Account")
		self.assertIn("BY RULE", line["basis_note"])

	def test_an_excluded_account_is_in_neither_pile_and_is_still_reported(self):
		"""An exclusion nobody can see is how a breakeven quietly gets easier."""
		data = self.a_computed_analysis()
		line = self.lines(data)["Income Tax"]
		self.assertEqual(line["fixed_amount"], 0)
		self.assertEqual(line["variable_amount"], 0)
		self.assertEqual(data["total_excluded_cost"], 5000.0)

	def test_a_classification_on_the_account_beats_the_heuristic(self):
		frappe.db.set_value("Account", account("Orchard Rent"), breakeven.BEHAVIOR_FIELD, "Variable")
		frappe.db.set_value("Account", account("Orchard Rent"), breakeven.BASIS_FIELD, "Sellable")
		line = self.lines(self.a_computed_analysis())["Orchard Rent"]
		self.assertEqual(line["cost_behavior"], "Variable")
		self.assertEqual(line["volume_basis"], "Sellable")
		self.assertEqual(line["classification_source"], "Account")

	def test_an_override_beats_the_account_and_writes_nothing_to_it(self):
		frappe.db.set_value("Account", account("Orchard Rent"), breakeven.BEHAVIOR_FIELD, "Variable")
		data = self.a_computed_analysis(
			cost_overrides=[
				{
					"account": account("Orchard Rent"),
					"cost_behavior": "Fixed",
					"reason": "the lease is signed for the season",
				}
			]
		)
		line = self.lines(data)["Orchard Rent"]
		self.assertEqual(line["cost_behavior"], "Fixed")
		self.assertEqual(line["classification_source"], "Override")
		self.assertIn("the lease is signed", line["basis_note"])
		# The Account is untouched: an override is for this analysis only.
		self.assertEqual(
			frappe.db.get_value("Account", account("Orchard Rent"), breakeven.BEHAVIOR_FIELD),
			"Variable",
		)

	def test_a_mixed_account_splits_and_the_two_parts_sum_back_to_the_amount(self):
		data = self.a_computed_analysis(
			cost_overrides=[
				{
					"account": account("Hauling"),
					"cost_behavior": "Mixed",
					"variable_pct": 70,
					"volume_basis": "Harvested",
				}
			]
		)
		line = self.lines(data)["Hauling"]
		self.assertAlmostEqual(line["variable_amount"], 7000.0, places=2)
		self.assertAlmostEqual(line["fixed_amount"], 3000.0, places=2)
		self.assertAlmostEqual(line["fixed_amount"] + line["variable_amount"], line["amount"], places=2)

	def test_an_override_naming_an_account_this_company_does_not_have_is_refused(self):
		"""REFUSED RATHER THAN IGNORED. A dropped override leaves the account
		being guessed at while the caller believes they classified it, and the
		result looks identical to one where it took."""
		name = self.an_analysis()["name"]
		message = self.tool_error(
			"compute_breakeven",
			{
				"name": name,
				"cost_overrides": [{"account": "9999 - Imaginary - ETC", "cost_behavior": "Fixed"}],
			},
		)
		self.assertIn("not expense accounts", message)
		self.assertIn("9999 - Imaginary - ETC", message)

	def test_two_overrides_for_one_account_are_refused(self):
		name = self.an_analysis()["name"]
		message = self.tool_error(
			"compute_breakeven",
			{
				"name": name,
				"cost_overrides": [
					{"account": account("Cartons"), "cost_behavior": "Fixed"},
					{"account": account("Cartons"), "cost_behavior": "Variable"},
				],
			},
		)
		self.assertIn("twice", message)

	def test_a_malformed_override_list_is_refused_before_anything_is_written(self):
		name = self.an_analysis()["name"]
		self.assertIn(
			"must be a list", self.tool_error("compute_breakeven", {"name": name, "cost_overrides": {}})
		)
		self.assertEqual(frappe.db.get_value("Breakeven Analysis", name, "status"), "Draft")

	def test_an_account_with_no_money_in_the_window_is_not_a_line(self):
		"""A hundred zero rows in front of the eight that matter is how a usable
		form becomes an unreadable one."""
		names = {line["account_name"] for line in self.a_computed_analysis()["cost_lines"]}
		self.assertNotIn("Office Supplies", names)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheStoredResultIsTheLedgersOwnAnswer(BreakevenTestCase):
	def test_the_piles_add_up_to_what_was_posted(self):
		data = self.a_computed_analysis()
		self.assertAlmostEqual(data["total_fixed_cost"], FIXED, places=2)
		self.assertAlmostEqual(data["total_variable_harvest_cost"], HARVEST, places=2)
		self.assertAlmostEqual(data["total_variable_sellable_cost"], SELLABLE, places=2)
		self.assertAlmostEqual(data["total_excluded_cost"], 5000.0, places=2)

	def test_the_headline_numbers_are_the_ones_the_arithmetic_class_derived(self):
		data = self.a_computed_analysis()
		self.assertAlmostEqual(data["contribution_margin_per_unit"], 11.50, places=4)
		self.assertAlmostEqual(data["breakeven_price"], 16.00, places=4)
		self.assertAlmostEqual(data["breakeven_units"], 5217.3913, places=3)
		self.assertAlmostEqual(data["projected_profit"], 32000.0, places=2)

	def test_the_rates_are_stored_with_the_baseline_they_were_derived_at(self):
		data = self.a_computed_analysis()
		self.assertAlmostEqual(data["rate_harvest"], 4.0, places=4)
		self.assertAlmostEqual(data["rate_sellable"], 3.75, places=4)
		self.assertEqual(data["baseline_harvest_units"], CROP)
		self.assertEqual(data["baseline_packout_pct"], PACKOUT)

	def test_computing_twice_with_the_same_inputs_gives_the_same_answer(self):
		"""Idempotence is not decoration here: the rates are derived from stored
		totals and a run that re-based them silently would make the second call
		disagree with the first."""
		name = self.an_analysis()["name"]
		first = self.tool_data("compute_breakeven", {"name": name})
		second = self.tool_data("compute_breakeven", {"name": name})
		for key in ("breakeven_price", "breakeven_units", "contribution_margin_per_unit", "rate_sellable"):
			self.assertAlmostEqual(first[key], second[key], places=6, msg=key)

	def test_a_cancelled_posting_is_not_a_cost(self):
		STORE.tables["GL Entry"][f"GL-BE-{MAIN_ABBR}-5400"]["is_cancelled"] = 1
		self.assertAlmostEqual(self.a_computed_analysis()["total_fixed_cost"], 10000.0, places=2)

	def test_a_credit_reduces_the_account_rather_than_adding_to_it(self):
		"""Expense is a debit-balance root: a refund is a credit and correctly
		reduces the cost. The other way round comes out negative on every
		well-kept set of books."""
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "GL-BE-REFUND",
					"account": account("Cartons"),
					"posting_date": "2026-07-01",
					"debit": 0,
					"credit": 4000,
					"company": MAIN,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "ACC-JV-2026-09001",
				}
			],
		)
		self.assertAlmostEqual(self.a_computed_analysis()["total_variable_sellable_cost"], 26000.0, places=2)

	def test_a_posting_outside_the_window_is_not_in_it(self):
		STORE.tables["GL Entry"][f"GL-BE-{MAIN_ABBR}-5400"]["posting_date"] = "2026-01-15"
		self.assertAlmostEqual(self.a_computed_analysis()["total_fixed_cost"], 10000.0, places=2)

	def test_the_window_defaults_from_the_fiscal_year(self):
		analysis = self.an_analysis(from_date=None, to_date=None)
		self.assertEqual(analysis["from_date"], "2026-01-01")
		self.assertEqual(analysis["to_date"], "2026-12-31")

	def test_another_companys_ledger_is_not_read(self):
		seed_orchard(OTHER, "SEL")
		self.assertAlmostEqual(self.a_computed_analysis()["total_fixed_cost"], FIXED, places=2)

	def test_a_cost_center_narrows_the_pull(self):
		"""Right for a farm that also runs a cattle operation through one set of
		books, and the reason the filter exists at all."""
		STORE.tables["GL Entry"][f"GL-BE-{MAIN_ABBR}-5400"]["cost_center"] = cost_center("Main")
		data = self.a_computed_analysis(create={"cost_center": cost_center("Field Work")})
		self.assertAlmostEqual(data["total_fixed_cost"], 10000.0, places=2)

	def test_an_empty_window_says_so_rather_than_reporting_a_free_season(self):
		STORE.tables["GL Entry"].clear()
		data = self.a_computed_analysis()
		self.assertEqual(data["cost_line_count"], 0)
		self.assertIn("almost always an empty window rather than a free season", data["computation_warnings"])

	def test_creation_does_not_compute(self):
		"""The two tools have separate switches, and a create that computed would
		hand an operator a tool they had not enabled."""
		created = self.tool_data(
			"create_breakeven_analysis",
			{
				"company": MAIN,
				"fiscal_year": "2026",
				"crop_type": "Fuji",
				"expected_harvest_units": 100,
			},
		)
		self.assertEqual(created["analysis"]["status"], "Draft")
		self.assertEqual(created["analysis"]["total_fixed_cost"], 0)
		self.assertIn("compute_breakeven", created["next_step"])
		self.assertIn("separate switches", created["next_step"])


# ── 4 ───────────────────────────────────────────────────────────────────────
class ThePackoutSliderMovesTheAnswer(BreakevenTestCase):
	"""The feature the module exists for, and the one that is easy to get
	directionally right and numerically wrong."""

	def test_sliding_the_packout_down_raises_the_breakeven_price(self):
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		lighter = self.tool_data("compute_breakeven", {"name": name, "packout_pct": 60})
		self.assertAlmostEqual(lighter["breakeven_price"], 19.75, places=4)
		self.assertEqual(lighter["packout_pct"], 60.0)

	def test_the_picking_bill_does_not_fall_with_the_packout(self):
		"""The asymmetry, at the tool level rather than only in the arithmetic."""
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		lighter = self.tool_data("compute_breakeven", {"name": name, "packout_pct": 60})
		self.assertAlmostEqual(lighter["total_variable_harvest_cost"], HARVEST, places=2)
		self.assertAlmostEqual(lighter["total_variable_sellable_cost"], 22500.0, places=2)

	def test_the_slider_and_the_sensitivity_table_agree_exactly(self):
		"""TWO TOOLS ANSWERING ONE QUESTION WITH TWO NUMBERS would be worse than
		either of them being slightly wrong. -25% of 80 is 60."""
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		table = self.tool_data(
			"get_breakeven_sensitivity", {"name": name, "variable": "packout", "range": [-25]}
		)
		row = table["scenarios"][0]
		slid = self.tool_data("compute_breakeven", {"name": name, "packout_pct": 60})
		self.assertAlmostEqual(row["scenario_value"], 60.0, places=6)
		self.assertAlmostEqual(row["breakeven_price"], slid["breakeven_price"], places=6)
		self.assertAlmostEqual(row["breakeven_units"], slid["breakeven_units"], places=6)
		self.assertAlmostEqual(
			row["contribution_margin_per_unit"], slid["contribution_margin_per_unit"], places=6
		)

	def test_the_baseline_is_not_moved_by_the_slider(self):
		"""The mechanism behind the agreement above. A run that re-based silently
		would make sliding to 62% mean 'the same money over fewer cartons'
		instead of 'the same money per carton over fewer cartons'."""
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		slid = self.tool_data("compute_breakeven", {"name": name, "packout_pct": 60})
		self.assertEqual(slid["baseline_packout_pct"], PACKOUT)
		self.assertAlmostEqual(slid["rate_sellable"], 3.75, places=6)

	def test_rebasing_is_explicit_and_says_what_it_did(self):
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		rebased = self.tool_data("compute_breakeven", {"name": name, "packout_pct": 60, "rebase_costs": True})
		self.assertEqual(rebased["baseline_packout_pct"], 60.0)
		self.assertAlmostEqual(rebased["rate_sellable"], 5.0, places=6)
		self.assertAlmostEqual(rebased["breakeven_price"], 21.00, places=4)
		self.assertIn("REBASED", rebased["computation_warnings"])

	def test_the_breakeven_packout_is_the_same_question_read_backwards(self):
		"""'We need 59% out of this block' is a target a packhouse can be given."""
		self.assertAlmostEqual(self.a_computed_analysis()["breakeven_packout_pct"], 59.0164, places=3)

	def test_a_packout_of_zero_or_over_a_hundred_is_refused(self):
		name = self.an_analysis()["name"]
		self.assertIn(
			"greater than zero", self.tool_error("compute_breakeven", {"name": name, "packout_pct": 0})
		)
		self.assertIn(
			"cannot pack out than came off the trees",
			self.tool_error("compute_breakeven", {"name": name, "packout_pct": 120}),
		)


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheSensitivityTableAnswersAndStoresNothing(BreakevenTestCase):
	def computed(self):
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		return name

	def test_it_refuses_an_analysis_that_has_never_been_computed(self):
		"""A table over a fixed cost of zero is a page of the same wrong number."""
		name = self.an_analysis()["name"]
		self.assertIn("never been computed", self.tool_error("get_breakeven_sensitivity", {"name": name}))

	def test_it_writes_no_scenario_rows(self):
		name = self.computed()
		before = len(self.tool_data("get_breakeven_analysis", {"name": name})["scenarios"])
		self.tool_data("get_breakeven_sensitivity", {"name": name, "variable": "price", "range": [-30, 30]})
		after = self.tool_data("get_breakeven_analysis", {"name": name})["scenarios"]
		self.assertEqual(len(after), before)

	def test_compute_stores_the_standard_band_across_every_variable(self):
		"""Four points on five variables. The register's contents depend on who
		ran a computation, not on who was browsing."""
		data = self.tool_data("get_breakeven_analysis", {"name": self.computed()})
		self.assertEqual(len(data["scenarios"]), 20)
		self.assertEqual(
			sorted({row["variable"] for row in data["scenarios"]}),
			["Fixed Cost", "Packout", "Price", "Variable Cost", "Yield"],
		)

	def test_a_bigger_crop_costs_more_to_pick_and_more_to_pack(self):
		"""Both rates apply to more units. A table that scaled only one of them
		is the commonest way a home-made sensitivity comes out wrong."""
		table = self.tool_data(
			"get_breakeven_sensitivity", {"name": self.computed(), "variable": "yield", "range": [20]}
		)
		row = table["scenarios"][0]
		self.assertAlmostEqual(row["scenario_value"], 12000.0, places=4)
		# Fixed 60,000 unmoved + both variable piles at 1.2x (48,000 + 36,000)
		# less 2,400 of culls, over 9,600 boxes. A bigger crop is CHEAPER per box
		# because the fixed cost is the only thing that did not grow with it —
		# which is the whole reason a grower asks this question.
		self.assertAlmostEqual(row["breakeven_price"], (60000.0 + 84000.0 - 2400.0) / 9600.0, places=4)
		self.assertLess(row["breakeven_price"], 16.00)

	def test_the_derivative_is_computed_rather_than_left_to_be_eyeballed(self):
		table = self.tool_data("get_breakeven_sensitivity", {"name": self.computed(), "variable": "price"})
		self.assertIn("the breakeven price moves", table["sensitivity"])

	def test_moving_the_price_does_not_move_the_breakeven_price(self):
		"""Worth an assertion because it looks wrong and is right: the price a
		crop needs is a function of its costs and its volume, not of what
		somebody hoped to get. It is the breakeven UNITS that move."""
		table = self.tool_data(
			"get_breakeven_sensitivity", {"name": self.computed(), "variable": "price", "range": [-20, 20]}
		)
		prices = {row["breakeven_price"] for row in table["scenarios"]}
		self.assertEqual(len(prices), 1)
		self.assertNotEqual(
			table["scenarios"][0]["breakeven_units"], table["scenarios"][1]["breakeven_units"]
		)

	def test_a_scenario_where_nothing_breaks_even_is_named_rather_than_blanked(self):
		table = self.tool_data(
			"get_breakeven_sensitivity", {"name": self.computed(), "variable": "price", "range": [-60]}
		)
		self.assertIsNone(table["scenarios"][0]["breakeven_units"])
		self.assertEqual(table["scenarios"][0]["verdict"], "loses money at any volume")
		self.assertEqual(table["no_volume_breaks_even_at"], [-60.0])
		self.assertIn("null by design", table["note"])

	def test_the_packout_scenario_is_clamped_at_a_hundred(self):
		"""More fruit cannot pack out than came off the trees."""
		table = self.tool_data(
			"get_breakeven_sensitivity", {"name": self.computed(), "variable": "packout", "range": [50]}
		)
		self.assertEqual(table["scenarios"][0]["scenario_value"], 100.0)

	def test_an_unknown_variable_is_refused_with_the_list(self):
		message = self.tool_error(
			"get_breakeven_sensitivity", {"name": self.computed(), "variable": "weather"}
		)
		self.assertIn("packout", message)

	def test_a_malformed_range_is_refused(self):
		name = self.computed()
		self.assertIn(
			"not a number",
			self.tool_error("get_breakeven_sensitivity", {"name": name, "variable": "price", "range": ["x"]}),
		)
		self.assertIn(
			"more than the 50",
			self.tool_error(
				"get_breakeven_sensitivity",
				{"name": name, "variable": "price", "range": list(range(60))},
			),
		)


# ── 6 ───────────────────────────────────────────────────────────────────────
class AnEditedInputGoesStale(BreakevenTestCase):
	def test_changing_a_price_in_the_desk_marks_the_results_stale(self):
		"""A breakeven that changed underneath the person reading it, keeping the
		same computed_on, is worse than one that says it is out of date."""
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		doc = frappe.get_doc("Breakeven Analysis", name)
		doc.expected_price = 25
		doc.save()
		self.assertEqual(frappe.db.get_value("Breakeven Analysis", name, "status"), "Stale")

	def test_the_stale_record_keeps_its_old_numbers_and_says_which_input_moved(self):
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		doc = frappe.get_doc("Breakeven Analysis", name)
		doc.packout_pct = 55
		doc.save()
		data = self.tool_data("get_breakeven_analysis", {"name": name})
		self.assertAlmostEqual(data["breakeven_price"], 16.00, places=4)
		self.assertIn("packout_pct changed", data["computation_warnings"])
		self.assertIn("STALE", data["status_note"])

	def test_reclassifying_a_cost_line_by_hand_also_goes_stale(self):
		"""The edit somebody makes between reading a breakeven and disbelieving it."""
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		doc = frappe.get_doc("Breakeven Analysis", name)
		doc.cost_lines[0]["cost_behavior"] = "Excluded"
		doc.save()
		self.assertEqual(frappe.db.get_value("Breakeven Analysis", name, "status"), "Stale")

	def test_touching_a_note_does_not(self):
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		doc = frappe.get_doc("Breakeven Analysis", name)
		doc.notes = "checked against the packhouse's own figure"
		doc.save()
		self.assertEqual(frappe.db.get_value("Breakeven Analysis", name, "status"), "Computed")

	def test_recomputing_brings_it_forward(self):
		name = self.an_analysis()["name"]
		self.tool_data("compute_breakeven", {"name": name})
		doc = frappe.get_doc("Breakeven Analysis", name)
		doc.expected_price = 25
		doc.save()
		data = self.tool_data("compute_breakeven", {"name": name})
		self.assertEqual(data["status"], "Computed")
		self.assertAlmostEqual(data["contribution_margin_per_unit"], 16.50, places=4)


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheRegisterNamesWhatIsNotAnAnswer(BreakevenTestCase):
	def test_a_draft_and_a_stale_row_are_reported_separately(self):
		"""Both carry a full set of numeric columns that read like a live result."""
		draft = self.an_analysis(analysis_name="Never run")["name"]
		stale = self.an_analysis(analysis_name="Went stale")["name"]
		self.tool_data("compute_breakeven", {"name": stale})
		doc = frappe.get_doc("Breakeven Analysis", stale)
		doc.expected_price = 30
		doc.save()

		data = self.tool_data("list_breakeven_analyses", {"company": MAIN})
		self.assertIn(draft, data["never_computed"])
		self.assertIn(stale, data["stale"])
		self.assertIn("never computed", data["status_note"])

	def test_a_row_where_no_volume_breaks_even_is_named(self):
		name = self.an_analysis(expected_price=5)["name"]
		self.tool_data("compute_breakeven", {"name": name})
		data = self.tool_data("list_breakeven_analyses", {"company": MAIN})
		self.assertIn(name, data["no_volume_breaks_even"])
		self.assertIn("there is no such quantity", data["note"])

	def test_it_filters_by_crop_across_seasons(self):
		"""One crop across seasons is the comparison worth having."""
		self.an_analysis(analysis_name="Gala 2026")
		self.an_analysis(analysis_name="Fuji 2026", crop_type="Fuji")
		data = self.tool_data("list_breakeven_analyses", {"company": MAIN, "crop_type": "Gala"})
		self.assertEqual(data["analysis_count"], 1)

	def test_a_get_by_analysis_name_that_matches_several_refuses_rather_than_picking(self):
		self.an_analysis(analysis_name="Southgate Gala", fiscal_year="2026")
		self.an_analysis(analysis_name="Southgate Gala", fiscal_year="2025")
		message = self.tool_error("get_breakeven_analysis", {"name": "Southgate Gala"})
		self.assertIn("matches 2 analyses", message)


# ── 8 ───────────────────────────────────────────────────────────────────────
class TheMarketOverlayIsNeverLoadBearing(BreakevenTestCase):
	"""Every figure is complete without it, and a failure to overlay must never
	take the breakeven down with it."""

	def a_quote(self, **overrides):
		payload = {
			"doctype": "USDA Price Quote",
			"commodity": "APPLES",
			"variety": "GALA",
			"market": "YAKIMA VALLEY WASHINGTON",
			"package": "40 lb cartons tray pack",
			"report_date": "2026-07-20",
			"low_price": 18.0,
			"high_price": 26.0,
			"mostly_low": 20.0,
			"mostly_high": 24.0,
			"source": "USDA AMS Market News",
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert(ignore_permissions=True)
		return doc

	def test_no_commodity_means_no_overlay_and_says_so(self):
		overlay = self.a_computed_analysis()["market_overlay"]
		self.assertIsNone(overlay["market_price"])
		self.assertIn("usda_commodity is not set", overlay["verdict"])
		self.assertIn("does not depend on it", overlay["verdict"])

	def test_a_commodity_with_no_quote_on_file_says_that_rather_than_zero(self):
		overlay = self.a_computed_analysis(create={"usda_commodity": "APPLES"})["market_overlay"]
		self.assertIsNone(overlay["market_price"])
		self.assertIn("no quotation for APPLES is on file", overlay["verdict"])

	def test_the_mostly_band_is_preferred_over_the_full_range(self):
		"""AMS publishes it because the low and the high of a district's day
		include the distressed load and the one specialty buyer."""
		self.a_quote()
		overlay = self.a_computed_analysis(create={"usda_commodity": "APPLES", "usda_variety": "GALA"})[
			"market_overlay"
		]
		self.assertEqual(overlay["market_price"], 22.0)

	def test_the_range_midpoint_is_used_where_no_mostly_band_was_published(self):
		self.a_quote(mostly_low=None, mostly_high=None)
		overlay = self.a_computed_analysis(create={"usda_commodity": "APPLES", "usda_variety": "GALA"})[
			"market_overlay"
		]
		self.assertEqual(overlay["market_price"], 22.0)

	def test_the_spread_is_the_market_against_this_crops_breakeven(self):
		self.a_quote()
		overlay = self.a_computed_analysis(create={"usda_commodity": "APPLES", "usda_variety": "GALA"})[
			"market_overlay"
		]
		self.assertAlmostEqual(overlay["spread_vs_breakeven"], 6.0, places=4)
		self.assertIn("ABOVE", overlay["verdict"])

	def test_the_two_packages_are_reported_and_never_converted(self):
		"""A breakeven per 40-lb box against a price per 20-lb carton is out by a
		factor of two and looks entirely plausible."""
		self.a_quote(package="20 lb cartons")
		overlay = self.a_computed_analysis(create={"usda_commodity": "APPLES", "usda_variety": "GALA"})[
			"market_overlay"
		]
		self.assertEqual(overlay["market_package"], "20 lb cartons")
		self.assertIn("NOT CONVERTED", overlay["verdict"])

	def test_a_widened_match_is_labelled_as_one(self):
		"""A grower who asked about Galas in Yakima and got the commodity-wide
		apple quotation is better served than one who got nothing — PROVIDED the
		answer says which of the two it is."""
		self.a_quote(variety="", market="")
		data = self.a_computed_analysis(
			create={"usda_commodity": "APPLES", "usda_variety": "GALA", "usda_market": "WENATCHEE"}
		)
		self.assertIn("commodity only", data["market_overlay"]["verdict"])
		self.assertIn("had to widen its search", data["computation_warnings"])

	def test_the_newest_report_date_wins(self):
		self.a_quote(
			report_date="2026-06-01", mostly_low=10.0, mostly_high=12.0, low_price=9.0, high_price=13.0
		)
		self.a_quote(report_date="2026-07-20")
		overlay = self.a_computed_analysis(create={"usda_commodity": "APPLES", "usda_variety": "GALA"})[
			"market_overlay"
		]
		self.assertEqual(overlay["quote_date"], "2026-07-20")

	def test_a_price_somebody_was_actually_quoted_is_stored_and_labelled(self):
		"""A broker's bid in hand is a better number than any district average,
		and it must not be mistakable for one."""
		name = self.an_analysis(usda_commodity="APPLES")["name"]
		data = self.tool_data(
			"compute_breakeven",
			{
				"name": name,
				"market_price": 18.5,
				"market_price_date": "2026-07-22",
				"market_package": "40 lb cartons",
				"market_price_source": "Broker Quote",
			},
		)
		self.assertEqual(data["market_overlay"]["market_price"], 18.5)
		self.assertAlmostEqual(data["market_overlay"]["spread_vs_breakeven"], 2.5, places=4)
		self.assertIn("Broker Quote", data["market_overlay"]["verdict"])

	def test_asking_for_a_fetch_with_no_report_slug_is_reported_not_guessed(self):
		data = self.a_computed_analysis(create={"usda_commodity": "APPLES"}, refresh_usda_prices=True)
		self.assertIn("no usda_report_slug was given", data["computation_warnings"])

	def test_a_fetch_with_no_api_key_leaves_the_register_alone_and_says_why(self):
		data = self.a_computed_analysis(
			create={"usda_commodity": "APPLES"}, refresh_usda_prices=True, usda_report_slug="FVWAPPL"
		)
		self.assertIn("no USDA MARS API key is configured", data["computation_warnings"])
		self.assertEqual(frappe.db.count("USDA Price Quote"), 0)


# ── 9 ───────────────────────────────────────────────────────────────────────
class AQuotationRefusesWhatCannotBeAPrice(SeededTestCase):
	def a_quote(self, **overrides):
		payload = {
			"doctype": "USDA Price Quote",
			"commodity": "CHERRIES",
			"report_date": "2026-07-20",
			"low_price": 30.0,
			"high_price": 40.0,
			"source": "USDA AMS Market News",
		}
		payload.update(overrides)
		return frappe.get_doc(payload)

	def test_a_high_below_its_low_is_refused(self):
		"""A transposition survives every downstream check: the midpoint of a
		reversed band is still a plausible-looking price."""
		with self.assertRaises(Exception) as caught:
			self.a_quote(low_price=40.0, high_price=30.0).insert()
		self.assertIn("transposed", str(caught.exception))

	def test_a_mostly_band_outside_its_range_is_refused(self):
		with self.assertRaises(Exception) as caught:
			self.a_quote(mostly_low=10.0, mostly_high=35.0).insert()
		self.assertIn("sits INSIDE the range", str(caught.exception))

	def test_a_negative_price_is_refused(self):
		with self.assertRaises(Exception) as caught:
			self.a_quote(low_price=-5.0).insert()
		self.assertIn("never negative", str(caught.exception))

	def test_a_report_dated_tomorrow_is_refused(self):
		"""It would win every 'most recent' lookup in this app permanently."""
		with self.assertRaises(Exception) as caught:
			self.a_quote(report_date="2099-01-01").insert()
		self.assertIn("in the future", str(caught.exception))

	def test_the_identity_is_the_docname_so_a_refetch_updates_rather_than_copies(self):
		first = self.a_quote()
		first.insert()
		self.assertIn("CHERRIES", first.name)
		self.assertIn("2026-07-20", first.name)


# ── 10 ──────────────────────────────────────────────────────────────────────
class TheSwitchesAreTheOnesThisAppPromises(SeededTestCase):
	def test_the_two_writes_are_off_out_of_the_box(self):
		self.configure()
		for tool in ("create_breakeven_analysis", "compute_breakeven"):
			with self.subTest(tool=tool):
				self.assertIn("is switched off", self.tool_error(tool, {"name": "x", "crop_type": "y"}))

	def test_the_three_reads_are_on_out_of_the_box(self):
		self.configure()
		data = self.tool_data("list_breakeven_analyses", {"company": MAIN})
		self.assertEqual(data["analysis_count"], 0)
