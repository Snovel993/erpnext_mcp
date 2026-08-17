# SPDX-License-Identifier: MIT
"""What a block cost, what it returned, and where it is in its own life.

1. **AN ESTABLISHING BLOCK'S NEGATIVE MARGIN IS NOT A LOSS.**
   `TheEstablishingBlockIsNotLosingMoney`. A fourth-leaf cherry block that spent
   $180,000 and returned $4,000 has invested it, not lost it. This is the claim
   the whole feature exists for: every general ledger answers the fiscal-year
   question, and reading a perennial through one is the single most common way a
   tree fruit operation misreads its own numbers.

2. **PERENNIAL AND ANNUAL ARE DIFFERENT SHAPES.** `ThePerennialAndTheAnnual`. An
   Annual whose plant year and season year differ is refused, because several
   years of establishment cost landing against one year's revenue makes a block
   read as ruinous in one season and free in the others.

3. **THE DOUBLE COUNT IS NAMED, NOT GUESSED AT.** `TheTwoSourcesOfCost`. Ledger
   plus attribution is wrong whenever an attribution row came from the ledger.
   Rows that might have are excluded from the total AND listed, because
   including them inflates and dropping them silently understates.

4. **REVENUE IS AN ATTRIBUTION, NOT A RECOGNITION.** `TheRevenueIsAttributed`.
   The ledger records that a settlement paid; nothing in it records which ground
   grew the fruit.

5. **CUMULATIVE IS THE POINT ON A PERENNIAL.** `TheWholeLifeReading`.
"""

from .fixtures import MAIN, MAIN_ABBR, V12TestCase, cost_center, seed_masters
from .harness import STORE, frappe

BLOCK = "Yellow Camp Block 3 - MC"
BLOCK_TWO = "Yellow Camp Block 4 - MC"
CHERRY = "Cherry"

#: A Journal Entry the shared fixture already seeds. Used where a cost
#: attribution has to carry a REAL voucher reference — the doctype links to
#: Journal Entry, so an invented name fails link validation, which is the
#: framework doing its job rather than something to work around.
SEEDED_JOURNAL_ENTRY = "ACC-JV-2026-00001"


def _cost_center_docname(plain: str) -> str:
	return f"{plain} - {MAIN_ABBR}"


ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_planting_season",
		"list_planting_seasons",
		"get_planting_season",
		"get_block_cost_summary",
		"get_block_revenue_summary",
		"get_block_profitability",
		"create_parcel",
		"create_field",
		"link_field_to_cost_center",
	)
}


class BlockLifecycleTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		seed_masters()
		self.configure(enabled=1, **ALL_ON)
		self._farm()

	def _farm(self):
		self.tool_data(
			"create_parcel",
			{
				"owning_entity": MAIN,
				"parcel_name": "Mill Creek",
				"acreage": 131.43,
				"county": "Wasco",
				"state": "OR",
				"use_type": "Orchard",
			},
		)
		for name in ("Yellow Camp Block 3", "Yellow Camp Block 4"):
			self.tool_data(
				"create_field",
				{
					"parcel": "Mill Creek",
					"field_name": name,
					"acreage": 12.5,
					"crop": CHERRY,
					"variety": "Bing",
					"planting_year": 2021,
					"condition": "Good",
				},
			)

	# ── helpers ────────────────────────────────────────────────────────────
	def a_planting(self, **kw):
		payload = {
			"field": BLOCK,
			"crop": CHERRY,
			"variety": "Bing",
			"plant_year": 2021,
			"season_year": 2026,
			"acres": 12.5,
			"company": MAIN,
		}
		payload.update(kw)
		return self.tool_data("create_planting_season", payload)

	#: A cost center of this test's own rather than one of the shared fixture
	#: centers. The seeded ledger already books against "Main", and a block
	#: pointed there would inherit those rows — every ledger assertion below
	#: would then be measuring the fixture rather than the tool.
	BLOCK_CENTER = "Block 3 Orchard"

	def with_cost_center(self, block=BLOCK):
		"""Point a block at a cost center, which is what makes ledger cost visible."""
		center = _cost_center_docname(self.BLOCK_CENTER)
		STORE.seed(
			"Cost Center",
			[
				{
					"name": center,
					"cost_center_name": self.BLOCK_CENTER,
					"cost_center_number": "",
					"parent_cost_center": cost_center("Operations", MAIN_ABBR),
					"is_group": 0,
					"disabled": 0,
					"company": MAIN,
					"lft": 900,
					"rgt": 901,
				}
			],
		)
		self.tool_data(
			"link_field_to_cost_center",
			{"field": block, "cost_center": center, "company": MAIN},
		)
		return center

	def a_ledger_cost(self, amount, posting_date="2026-04-01", center=None):
		STORE.seed(
			"GL Entry",
			[
				{
					"name": f"GL-{posting_date}-{amount}",
					"company": MAIN,
					"account": f"Farm Supplies - {MAIN_ABBR}",
					"cost_center": center or _cost_center_docname(self.BLOCK_CENTER),
					"posting_date": posting_date,
					"debit": amount,
					"credit": 0,
					"is_cancelled": 0,
					"voucher_no": "JE-TEST",
				}
			],
		)

	def a_cost_entry(self, planting, amount, **kw):
		doc = frappe.new_doc("Block Cost Entry")
		doc.planting_season = planting
		doc.company = MAIN
		doc.posting_date = kw.pop("posting_date", "2026-04-01")
		doc.amount = amount
		doc.cost_category = kw.pop("cost_category", "Labor")
		doc.source = kw.pop("source", "Manual")
		doc.acres = kw.pop("acres", 12.5)
		for key, value in kw.items():
			doc.set(key, value)
		doc.insert(ignore_permissions=True)
		return doc.name

	def a_revenue_entry(self, planting, amount, **kw):
		doc = frappe.new_doc("Block Revenue Entry")
		doc.planting_season = planting
		doc.company = MAIN
		doc.posting_date = kw.pop("posting_date", "2026-07-01")
		doc.amount = amount
		doc.revenue_type = kw.pop("revenue_type", "Settlement")
		doc.source = kw.pop("source", "Manual")
		for key, value in kw.items():
			doc.set(key, value)
		doc.insert(ignore_permissions=True)
		return doc.name


# ── 1. the establishing block ───────────────────────────────────────────────
class TheEstablishingBlockIsNotLosingMoney(BlockLifecycleTestCase):
	def test_a_negative_margin_on_an_establishing_block_is_not_called_a_loss(self):
		"""The claim the whole feature exists for."""
		planting = self.a_planting(status="Establishing")["name"]
		self.a_cost_entry(planting, 180000)
		self.a_revenue_entry(planting, 4000)
		data = self.tool_data("get_block_profitability", {"planting_season": planting})
		self.assertEqual(data["margin"], -176000.0)
		self.assertFalse(data["is_meaningful_as_profit_and_loss"])
		self.assertIn("NOT A PROFIT AND LOSS", data["verdict"])
		self.assertIn("investment", data["verdict"].lower())

	def test_the_figures_are_still_all_reported(self):
		"""The numbers are real and somebody needs them; what the app will not do
		is put the word 'loss' next to them."""
		planting = self.a_planting(status="Establishing")["name"]
		self.a_cost_entry(planting, 180000)
		data = self.tool_data("get_block_profitability", {"planting_season": planting})
		self.assertEqual(data["total_cost"], 180000.0)
		self.assertEqual(data["cost_per_acre"], 14400.0)

	def test_a_productive_block_in_the_red_is_called_what_it_is(self):
		planting = self.a_planting(status="Productive")["name"]
		self.a_cost_entry(planting, 50000)
		self.a_revenue_entry(planting, 20000)
		data = self.tool_data("get_block_profitability", {"planting_season": planting})
		self.assertTrue(data["is_meaningful_as_profit_and_loss"])
		self.assertIn("more than it returned", data["verdict"])

	def test_a_productive_block_in_the_black_reports_the_margin(self):
		planting = self.a_planting(status="Productive")["name"]
		self.a_cost_entry(planting, 20000)
		self.a_revenue_entry(planting, 95000)
		data = self.tool_data("get_block_profitability", {"planting_season": planting})
		self.assertEqual(data["margin"], 75000.0)
		self.assertIn("Returned", data["verdict"])

	def test_the_cost_summary_says_establishment_cost_capitalises(self):
		planting = self.a_planting(status="Establishing")["name"]
		self.a_cost_entry(planting, 1000)
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertTrue(any("263A" in note for note in data["notes"]))

	def test_capitalised_cost_is_reported_separately(self):
		planting = self.a_planting(status="Establishing")["name"]
		self.a_cost_entry(planting, 30000, capitalized=1, cost_category="Establishment")
		self.a_cost_entry(planting, 5000, posting_date="2026-04-02")
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["attributions"]["capitalised"], 30000.0)
		self.assertEqual(data["attributions"]["expensed"], 5000.0)

	def test_leaf_year_is_computed_not_stored(self):
		"""A stored copy would be wrong for eleven months of every year."""
		planting = self.a_planting(plant_year=2021, season_year=2026)["name"]
		data = self.tool_data("get_planting_season", {"planting_season": planting})
		self.assertEqual(data["leaf_year"], 6)


# ── 2. perennial and annual ─────────────────────────────────────────────────
class ThePerennialAndTheAnnual(BlockLifecycleTestCase):
	def test_an_annual_spanning_years_is_refused(self):
		"""Several years of establishment cost against one year's revenue makes a
		block read as ruinous in one season and free in the others."""
		error = self.tool_error(
			"create_planting_season",
			{
				"field": BLOCK,
				"crop": "Onion",
				"lifecycle": "Annual",
				"plant_year": 2024,
				"season_year": 2026,
			},
		)
		self.assertIn("An annual IS its planting", error)

	def test_an_annual_with_matching_years_is_accepted(self):
		data = self.a_planting(crop="Onion", lifecycle="Annual", plant_year=2026, season_year=2026)
		self.assertEqual(data["lifecycle"], "Annual")

	def test_an_annual_defaults_its_season_year_to_the_plant_year(self):
		data = self.a_planting(crop="Onion", lifecycle="Annual", plant_year=2026, season_year=None)
		self.assertEqual(data["season_year"], 2026)

	def test_a_perennial_may_span_years(self):
		data = self.a_planting(plant_year=2021, season_year=2026)
		self.assertEqual(data["lifecycle"], "Perennial")
		self.assertEqual(data["plant_year"], 2021)
		self.assertEqual(data["season_year"], 2026)

	def test_a_season_before_the_plant_year_is_refused(self):
		error = self.tool_error(
			"create_planting_season",
			{"field": BLOCK, "crop": CHERRY, "plant_year": 2021, "season_year": 2019},
		)
		self.assertIn("before it went in the ground", error)

	def test_two_plantings_of_one_crop_in_one_season_are_refused(self):
		"""Every cost would have two places to land and no rule for choosing."""
		self.a_planting()
		error = self.tool_error(
			"create_planting_season",
			{
				"field": BLOCK,
				"crop": CHERRY,
				"plant_year": 2021,
				"season_year": 2026,
				"company": MAIN,
			},
		)
		self.assertIn("two places to land", error)

	def test_a_second_variety_on_the_same_ground_needs_its_own_block_name(self):
		self.a_planting()
		data = self.a_planting(block_name="North Half", variety="Rainier")
		self.assertEqual(data["block_name"], "North Half")

	def test_trees_per_acre_comes_from_the_spacing_when_both_are_known(self):
		"""The DESIGN density, which stays true when six trees die."""
		data = self.a_planting(spacing_in_row_ft=4, spacing_between_rows_ft=12)
		self.assertEqual(data["trees_per_acre"], 907.5)

	def test_trees_per_acre_falls_back_to_the_count_over_acres(self):
		data = self.a_planting(trees_planted=10000, acres=10)
		self.assertEqual(data["trees_per_acre"], 1000.0)

	def test_a_removed_planting_needs_a_removal_date(self):
		error = self.tool_error(
			"create_planting_season",
			{
				"field": BLOCK,
				"crop": CHERRY,
				"plant_year": 2021,
				"status": "Removed",
				"company": MAIN,
			},
		)
		self.assertIn("date it came out", error)

	def test_an_implausible_plant_year_is_refused(self):
		error = self.tool_error(
			"create_planting_season", {"field": BLOCK, "crop": CHERRY, "plant_year": 2190}
		)
		self.assertIn("not a plausible year", error)

	def test_an_unknown_field_is_refused(self):
		error = self.tool_error(
			"create_planting_season", {"field": "Nowhere", "crop": CHERRY, "plant_year": 2021}
		)
		self.assertIn("no Field called", error)

	def test_the_list_reports_establishing_acres(self):
		"""The acreage consuming money and returning none, which no report built
		on a fiscal year will show."""
		self.a_planting(status="Establishing")
		self.a_planting(field=BLOCK_TWO, status="Productive", season_year=2026)
		data = self.tool_data("list_planting_seasons", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["establishing_acres"], 12.5)

	def test_a_planting_with_no_cost_center_is_named(self):
		self.a_planting()
		data = self.tool_data("list_planting_seasons", {"company": MAIN})
		self.assertEqual(len(data["without_cost_center"]), 1)


# ── 3. the two sources of cost ──────────────────────────────────────────────
class TheTwoSourcesOfCost(BlockLifecycleTestCase):
	def setUp(self):
		super().setUp()
		self.with_cost_center()

	def test_ledger_cost_reaches_the_block_through_its_cost_center(self):
		planting = self.a_planting()["name"]
		self.a_ledger_cost(25000)
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertTrue(data["ledger"]["measurable"])
		self.assertEqual(data["ledger"]["amount"], 25000.0)
		self.assertEqual(data["total_cost"], 25000.0)

	def test_a_standalone_attribution_is_added_to_the_ledger(self):
		"""Owner labour and in-kind trades have no ledger entry at all."""
		planting = self.a_planting()["name"]
		self.a_ledger_cost(25000)
		self.a_cost_entry(planting, 4000)
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["attributions"]["standalone_total"], 4000.0)
		self.assertEqual(data["total_cost"], 29000.0)

	def test_a_swept_row_is_excluded_from_the_total(self):
		"""The ledger is counted directly, so counting the sweep too would double."""
		planting = self.a_planting()["name"]
		self.a_ledger_cost(25000)
		self.a_cost_entry(planting, 25000, source="GL Sweep", gl_entry="GL-2026-04-01-25000")
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["attributions"]["definitely_already_in_ledger"], 25000.0)
		self.assertEqual(data["total_cost"], 25000.0)

	def test_a_row_with_a_voucher_reference_is_excluded_and_named(self):
		"""Including it inflates and dropping it silently understates. A farm told
		which rows are in question settles it in a minute."""
		planting = self.a_planting()["name"]
		self.a_ledger_cost(25000)
		self.a_cost_entry(planting, 900, journal_entry=SEEDED_JOURNAL_ENTRY)
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["attributions"]["probably_already_in_ledger"], 900.0)
		self.assertEqual(data["total_cost"], 25000.0)
		self.assertTrue(any("probably already inside" in note for note in data["notes"]))

	def test_a_block_with_no_cost_center_says_why_the_ledger_is_silent(self):
		planting = self.a_planting(field=BLOCK_TWO)["name"]
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertFalse(data["ledger"]["measurable"])
		self.assertIn("no cost center", data["ledger"]["note"])

	def test_credits_reduce_the_ledger_figure_rather_than_appearing_elsewhere(self):
		planting = self.a_planting()["name"]
		self.a_ledger_cost(25000)
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "GL-CREDIT",
					"company": MAIN,
					"account": f"Farm Supplies - {MAIN_ABBR}",
					"cost_center": _cost_center_docname(self.BLOCK_CENTER),
					"posting_date": "2026-04-15",
					"debit": 0,
					"credit": 5000,
					"is_cancelled": 0,
				}
			],
		)
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["ledger"]["amount"], 20000.0)

	def test_costs_are_grouped_by_category(self):
		planting = self.a_planting()["name"]
		self.a_cost_entry(planting, 4000, cost_category="Labor")
		self.a_cost_entry(planting, 1200, cost_category="Chemicals", posting_date="2026-04-02")
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["attributions"]["by_cost_category"]["Labor"], 4000.0)
		self.assertEqual(data["attributions"]["by_cost_category"]["Chemicals"], 1200.0)

	def test_a_cost_of_zero_is_refused(self):
		planting = self.a_planting()["name"]
		with self.assertRaises(Exception) as caught:
			self.a_cost_entry(planting, 0)
		self.assertIn("cannot be zero", str(caught.exception))

	def test_a_negative_cost_is_allowed_because_rebates_are_real(self):
		planting = self.a_planting()["name"]
		self.a_cost_entry(planting, 5000)
		self.a_cost_entry(planting, -800, posting_date="2026-04-10", cost_category="Chemicals")
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["attributions"]["standalone_total"], 4200.0)

	def test_the_window_defaults_to_the_planting_season_year(self):
		planting = self.a_planting(season_year=2026)["name"]
		self.a_cost_entry(planting, 1000, posting_date="2026-04-01")
		self.a_cost_entry(planting, 9999, posting_date="2025-04-01")
		data = self.tool_data("get_block_cost_summary", {"planting_season": planting})
		self.assertEqual(data["window"], "calendar 2026")
		self.assertEqual(data["attributions"]["standalone_total"], 1000.0)


# ── 4. revenue is an attribution ────────────────────────────────────────────
class TheRevenueIsAttributed(BlockLifecycleTestCase):
	def test_the_response_says_it_is_an_attribution_not_a_recognition(self):
		planting = self.a_planting()["name"]
		self.a_revenue_entry(planting, 84000)
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertTrue(any("ATTRIBUTION" in note for note in data["notes"]))

	def test_revenue_totals_and_divides_by_acres(self):
		planting = self.a_planting(acres=10)["name"]
		self.a_revenue_entry(planting, 90000)
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertEqual(data["total_revenue"], 90000.0)
		self.assertEqual(data["revenue_per_acre"], 9000.0)

	def test_the_allocation_basis_is_reported(self):
		"""A farm reading its per-block returns three years later needs to know
		whether the split was on weight or was direct."""
		planting = self.a_planting()["name"]
		self.a_revenue_entry(
			planting, 40000, allocation_basis="Weight Share", allocation_pct=47.5, pool_total=84210
		)
		self.a_revenue_entry(
			planting, 2000, posting_date="2026-07-15", allocation_basis="Direct", revenue_type="Fresh Sales"
		)
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertEqual(data["by_allocation_basis"]["Weight Share"], 40000.0)
		self.assertEqual(data["by_allocation_basis"]["Direct"], 2000.0)

	def test_mixed_quantity_units_are_called_out(self):
		"""The price per unit derived from a sum of unlike things means nothing."""
		planting = self.a_planting()["name"]
		self.a_revenue_entry(planting, 40000, quantity=500, quantity_uom="Bin")
		self.a_revenue_entry(planting, 2000, posting_date="2026-07-15", quantity=1200, quantity_uom="Lb")
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertEqual(sorted(data["quantity_uoms"]), ["Bin", "Lb"])
		self.assertTrue(any("more than one unit" in note for note in data["notes"]))

	def test_a_single_unit_gives_a_price_per_unit(self):
		planting = self.a_planting()["name"]
		self.a_revenue_entry(planting, 40000, quantity=500, quantity_uom="Bin")
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertEqual(data["price_per_unit"], 80.0)

	def test_a_negative_settlement_is_allowed(self):
		"""A block whose fruit did not pack out can owe the packer more than the
		fruit returned, and the statement comes with a number in brackets."""
		planting = self.a_planting()["name"]
		self.a_revenue_entry(planting, -3200, quantity=40, quantity_uom="Bin")
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertEqual(data["total_revenue"], -3200.0)
		self.assertEqual(data["price_per_unit"], -80.0)

	def test_a_negative_quantity_is_refused(self):
		"""It would flip the sign of the price and make a bad settlement look
		like a good one."""
		planting = self.a_planting()["name"]
		with self.assertRaises(Exception) as caught:
			self.a_revenue_entry(planting, 4000, quantity=-10)
		self.assertIn("Quantity cannot be negative", str(caught.exception))

	def test_no_revenue_at_all_says_what_to_do_about_it(self):
		planting = self.a_planting()["name"]
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertEqual(data["total_revenue"], 0.0)
		self.assertTrue(any("has to be written" in note for note in data["notes"]))

	def test_an_unattributed_scale_ticket_is_flagged(self):
		"""A delivery whose return has not been attributed back to the ground
		that grew it."""
		planting = self.a_planting()["name"]
		STORE.seed(
			"Scale Ticket",
			[
				{
					"name": "ST-0001",
					"ticket_number": "0001",
					"company": MAIN,
					"date": "2026-08-05",
					"field": BLOCK,
					"variety": "Bing",
					"net_weight": 18400,
					"weight_uom": "Lb",
				}
			],
		)
		data = self.tool_data("get_block_revenue_summary", {"planting_season": planting})
		self.assertEqual(len(data["scale_tickets"]), 1)
		self.assertFalse(data["scale_tickets"][0]["attributed"])
		self.assertTrue(any("not been attributed" in note for note in data["notes"]))


# ── 5. the whole-life reading ───────────────────────────────────────────────
class TheWholeLifeReading(BlockLifecycleTestCase):
	def test_one_season_of_a_perennial_says_to_ask_for_the_whole_life(self):
		planting = self.a_planting(status="Productive")["name"]
		self.a_cost_entry(planting, 1000)
		data = self.tool_data("get_block_profitability", {"planting_season": planting})
		self.assertFalse(data["cumulative"])
		self.assertTrue(any("cumulative=true" in note for note in data["notes"]))

	def test_cumulative_sums_every_season_of_the_planting(self):
		"""A block that takes years to pay back cannot be judged on one of them,
		and a general ledger can only ever show you one of them."""
		establishing = self.a_planting(season_year=2023, status="Establishing")["name"]
		bearing = self.a_planting(season_year=2026, status="Productive")["name"]
		self.a_cost_entry(establishing, 60000, posting_date="2023-04-01")
		self.a_cost_entry(bearing, 20000, posting_date="2026-04-01")
		self.a_revenue_entry(bearing, 95000, posting_date="2026-07-01")

		data = self.tool_data("get_block_profitability", {"planting_season": bearing, "cumulative": True})
		self.assertTrue(data["cumulative"])
		self.assertEqual(sorted(data["seasons_included"]), sorted([establishing, bearing]))
		self.assertEqual(data["total_cost"], 80000.0)
		self.assertEqual(data["total_revenue"], 95000.0)
		self.assertEqual(data["margin"], 15000.0)

	def test_the_per_season_breakdown_is_returned(self):
		establishing = self.a_planting(season_year=2023, status="Establishing")["name"]
		bearing = self.a_planting(season_year=2026, status="Productive")["name"]
		self.a_cost_entry(establishing, 60000, posting_date="2023-04-01")
		self.a_cost_entry(bearing, 20000, posting_date="2026-04-01")
		data = self.tool_data("get_block_profitability", {"planting_season": bearing, "cumulative": True})
		by_season = {row["planting_season"]: row["cost"] for row in data["per_season"]}
		self.assertEqual(by_season[establishing], 60000.0)
		self.assertEqual(by_season[bearing], 20000.0)

	def test_a_planting_resolves_from_a_readable_description(self):
		"""A model will say 'the 2021 Gala block' as often as it will say a hash."""
		self.a_planting(variety="Gala", plant_year=2021, season_year=2026)
		data = self.tool_data("get_planting_season", {"planting_season": "Gala", "company": MAIN})
		self.assertEqual(data["variety"], "Gala")

	def test_an_ambiguous_description_is_refused_with_the_candidates(self):
		"""A field worked as two plantings is the ordinary case this exists for,
		so guessing is not available."""
		self.a_planting(block_name="North Half", variety="Bing")
		self.a_planting(block_name="South Half", variety="Bing")
		error = self.tool_error("get_planting_season", {"planting_season": "Bing", "company": MAIN})
		self.assertIn("matches 2 plantings", error)

	def test_an_unknown_planting_is_refused(self):
		error = self.tool_error("get_planting_season", {"planting_season": "Nothing Like This"})
		self.assertIn("no Planting Season matching", error)

	def test_a_perennial_with_no_planned_end_is_flagged(self):
		"""A farm that has never written it down replants by surprise."""
		planting = self.a_planting()["name"]
		data = self.tool_data("get_planting_season", {"planting_season": planting})
		self.assertTrue(any("No planned end" in note for note in data["lifecycle_notes"]))

	def test_a_block_past_its_productive_from_date_and_still_establishing_is_flagged(self):
		planting = self.a_planting(status="Establishing", productive_from="2025-06-01")["name"]
		data = self.tool_data("get_planting_season", {"planting_season": planting})
		self.assertTrue(any("still capitalising" in note for note in data["lifecycle_notes"]))

	def test_other_plantings_on_the_field_are_listed(self):
		first = self.a_planting(block_name="North Half")["name"]
		self.a_planting(block_name="South Half")
		data = self.tool_data("get_planting_season", {"planting_season": first})
		self.assertEqual(len(data["other_plantings_on_this_field"]), 1)
