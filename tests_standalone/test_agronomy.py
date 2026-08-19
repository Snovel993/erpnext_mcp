# SPDX-License-Identifier: MIT
"""Crops, markets and agricultural units: the master data everything else assumed.

Five things these tests are really about.

BLANK IS NOT ZERO, AND THE PHI IS WHERE THAT MATTERS MOST. A crop with
`default_phi_days` of 0 has genuinely no pre-harvest interval; a crop with none
recorded is one nobody has checked. There are tests proving the two are reported
apart at every level — the register, the single read and the create — because a
gate that conflates them clears fruit a label would hold.

THE CROP IS PART OF THE CONVERSION KEY, AND THAT IS THE WHOLE POINT. A bin of
cherries is 800 lb and a bin of apples is 900. `ConversionResolution` proves the
crop-specific row wins, that the generic row is a labelled fallback rather than a
silent one, and — the case that matters most — that asking for a crop with no row
and no generic fallback is REFUSED with the other crops named, rather than
answered with somebody else's fruit.

TWO WINDOW RULES THAT LOOK ALIKE AND ARE OPPOSITES. Half a harvest window is
refused, because a season with no end is a season every reader has to guess at.
A window that WRAPS the year is accepted, because November to February is a real
harvest and the obvious `start <= end` check would be a rule about integers
wearing the costume of a rule about farming. Both have tests, adjacent, so
removing one to "fix" the other is visibly wrong.

A CONTRADICTION IS REFUSED; A JUDGEMENT IS REPORTED. `maturity_years` on an
annual is refused — an annual has no non-bearing years, and the two facts cannot
both be true. Every recorded variety sitting in one pollination group is only
REPORTED, because the pollinizer may be in a neighbouring block or simply
unrecorded. The tests state which is which, because getting that boundary wrong
in either direction is how a register becomes either unusable or useless.

NEITHER A CROP NOR A MARKET IS COMPANY-SCOPED. There is a test that `create_crop`
and `create_market` do not take a company at all, and it is there because every
other register in this app does. A species is a species; a market is a place in
the world, and two growers shipping into one market are shipping into ONE market.
"""

from .fixtures import V12TestCase
from .harness import STORE

ALL_ON = {
	"allow_list_crops": 1,
	"allow_get_crop": 1,
	"allow_create_crop": 1,
	"allow_update_crop": 1,
	"allow_list_markets": 1,
	"allow_get_market": 1,
	"allow_create_market": 1,
	"allow_update_market": 1,
	"allow_list_ag_uom_contexts": 1,
	"allow_get_uom_conversions": 1,
}

#: The units the conversion tests convert between. Seeded here rather than
#: relying on the installer, because these tests are about the RESOLUTION logic
#: and a fixture that quietly depended on the seed order would fail for a reason
#: that had nothing to do with what it was checking.
AG_UOMS = ("Bin", "Lug", "Bucket", "Bushel", "Pound", "Ton", "Gallon", "Fluid Ounce", "Acre", "Square Foot")


class AgronomyTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		STORE.seed("UOM", [{"name": name, "enabled": 1} for name in AG_UOMS])

	# -- builders ------------------------------------------------------------
	def a_crop(self, crop_name="Sweet Cherry", **overrides):
		payload = {
			"crop_name": crop_name,
			"crop_type": "Stone Fruit",
			"scientific_name": "Prunus avium",
			"growth_cycle": "Perennial",
			"days_to_harvest": 60,
			"harvest_window_start": "June",
			"harvest_window_end": "August",
			"default_phi_days": 3,
			"varieties": [
				{
					"variety_name": "Bing",
					"rootstock": "Mazzard",
					"pollination_group": "S3S4",
					"expected_yield_per_acre": 4.5,
					"maturity_years": 5,
				},
				{
					"variety_name": "Rainier",
					"rootstock": "Mazzard",
					"pollination_group": "S1S4",
					"expected_yield_per_acre": 4.0,
					"maturity_years": 5,
				},
			],
		}
		payload.update(overrides)
		return self.tool_data("create_crop", payload)

	def a_market(self, market_name="Pacific Northwest Fresh Cherry", **overrides):
		payload = {
			"market_name": market_name,
			"market_type": "Fresh",
			"region": "Pacific Northwest",
			"shipping_point": "Washington and Oregon Cherries",
			"grade_standards": [
				{
					"grade_name": "Washington Extra Fancy",
					"min_size_mm": 26.5,
					"max_defect_pct": 5,
					"pack_style": "15 lb clamshell",
					"premium_pct": 25,
				},
				{
					"grade_name": "Orchard Run",
					"min_size_mm": 22.0,
					"max_defect_pct": 15,
					"pack_style": "20 lb bulk",
					"premium_pct": -30,
				},
			],
		}
		payload.update(overrides)
		return self.tool_data("create_market", payload)

	def a_conversion(self, **overrides):
		"""One conversion straight into the store.

		Written directly rather than through a tool because there is no
		create_uom_conversion tool in this release — the register is seeded on
		install and edited in the Desk, and these tests are about how it is READ.
		"""
		row = {
			"doctype": "Agricultural UOM Conversion",
			"from_uom": "Bin",
			"to_uom": "Pound",
			"crop": "",
			"factor": 800.0,
			"basis": "Nominal",
			"source": "Trade rule of thumb",
			"is_active": 1,
		}
		row.update(overrides)
		crop = row.get("crop") or ""
		row["name"] = f"{row['from_uom']} to {row['to_uom']}" + (f" - {crop}" if crop else "")
		STORE.seed("Agricultural UOM Conversion", [row])
		return row

	def a_context(self, context_name="Harvest", applies_to="Count", uoms=None, **overrides):
		row = {
			"doctype": "Agricultural UOM Context",
			"name": context_name,
			"context_name": context_name,
			"applies_to": applies_to,
			"is_active": 1,
			"description": "",
			"uoms": uoms
			if uoms is not None
			else [
				{"uom": "Bin", "is_default": 1, "notes": ""},
				{"uom": "Lug", "is_default": 0, "notes": ""},
			],
		}
		row.update(overrides)
		STORE.seed("Agricultural UOM Context", [row])
		return row


# ── the crop register ───────────────────────────────────────────────────────
class CreatingACrop(AgronomyTestCase):
	def test_the_docname_is_the_crop_name(self):
		self.assertEqual(self.a_crop()["name"], "Sweet Cherry")

	def test_the_varieties_are_stored_in_order(self):
		created = self.a_crop()
		self.assertEqual([row["variety_name"] for row in created["varieties"]], ["Bing", "Rainier"])

	def test_a_second_crop_of_the_same_name_is_refused(self):
		self.a_crop()
		message = self.tool_error("create_crop", {"crop_name": "Sweet Cherry", "crop_type": "Stone Fruit"})
		self.assertIn("already registered", message)

	def test_the_refusal_leaves_the_first_crop_alone(self):
		self.a_crop()
		self.tool_error("create_crop", {"crop_name": "Sweet Cherry", "crop_type": "Stone Fruit"})
		self.assertEqual(len(STORE.rows("Crop")), 1)

	def test_a_crop_type_outside_the_list_is_refused_with_the_list(self):
		message = self.tool_error("create_crop", {"crop_name": "Hops", "crop_type": "Vine"})
		self.assertIn("Tree Fruit", message)

	def test_the_growth_cycle_defaults_to_perennial(self):
		created = self.tool_data("create_crop", {"crop_name": "Plum", "crop_type": "Stone Fruit"})
		self.assertEqual(created["growth_cycle"], "Perennial")

	def test_an_annual_crop_is_accepted_because_a_farm_is_not_only_trees(self):
		created = self.tool_data(
			"create_crop", {"crop_name": "Sweet Corn", "crop_type": "Vegetable", "growth_cycle": "Annual"}
		)
		self.assertEqual(created["growth_cycle"], "Annual")

	def test_an_unknown_variety_key_is_refused_rather_than_dropped(self):
		"""A key silently dropped is a fact somebody thinks they recorded."""
		message = self.tool_error(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"varieties": [{"variety_name": "Italian", "rootsock": "Myrobalan"}],
			},
		)
		self.assertIn("rootsock", message)

	def test_nothing_is_written_when_a_variety_row_is_refused(self):
		self.tool_error(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"varieties": [{"variety_name": "Italian"}, {"nonsense": 1}],
			},
		)
		self.assertEqual(STORE.rows("Crop"), [])

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_crop", {"crop_name": "Plum", "crop_type": "Stone Fruit"})
		self.assertIn("allow_create_crop", message)

	def test_it_is_audited(self):
		self.a_crop()
		self.assertAudited("create_crop", "Success")


class TheHarvestWindow(AgronomyTestCase):
	"""Two rules that look alike and are opposites. Kept adjacent on purpose."""

	def test_half_a_window_is_refused(self):
		message = self.tool_error(
			"create_crop",
			{"crop_name": "Plum", "crop_type": "Stone Fruit", "harvest_window_start": "July"},
		)
		self.assertIn("Half a window", message)

	def test_the_refusal_names_which_end_is_missing(self):
		message = self.tool_error(
			"create_crop",
			{"crop_name": "Plum", "crop_type": "Stone Fruit", "harvest_window_end": "July"},
		)
		self.assertIn("has a end and no start", message)

	def test_a_window_that_wraps_the_year_is_accepted(self):
		"""November to February is a real harvest. A `start <= end` check would
		refuse the southern hemisphere and the greenhouse both."""
		created = self.tool_data(
			"create_crop",
			{
				"crop_name": "Citrus",
				"crop_type": "Tree Fruit",
				"harvest_window_start": "November",
				"harvest_window_end": "February",
			},
		)
		self.assertEqual(created["harvest_months"], ["November", "December", "January", "February"])

	def test_a_normal_window_counts_its_months_inclusively(self):
		self.assertEqual(self.a_crop()["harvest_months"], ["June", "July", "August"])

	def test_no_window_at_all_is_allowed_and_warned_about(self):
		created = self.tool_data(
			"create_crop", {"crop_name": "Plum", "crop_type": "Stone Fruit", "default_phi_days": 7}
		)
		self.assertEqual(created["harvest_months"], [])
		self.assertTrue(any("harvest window" in warning for warning in created["warnings"]))


class MaturityYearsAreAContradictionOnAnAnnual(AgronomyTestCase):
	def test_years_to_maturity_on_an_annual_is_refused(self):
		message = self.tool_error(
			"create_crop",
			{
				"crop_name": "Sweet Corn",
				"crop_type": "Vegetable",
				"growth_cycle": "Annual",
				"varieties": [{"variety_name": "Bodacious", "maturity_years": 3}],
			},
		)
		self.assertIn("no non-bearing years", message)

	def test_the_same_row_is_fine_on_a_perennial(self):
		created = self.tool_data(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"varieties": [{"variety_name": "Italian", "maturity_years": 3}],
			},
		)
		self.assertEqual(created["varieties"][0]["maturity_years"], 3)

	def test_an_annual_with_no_maturity_years_is_untouched(self):
		created = self.tool_data(
			"create_crop",
			{
				"crop_name": "Sweet Corn",
				"crop_type": "Vegetable",
				"growth_cycle": "Annual",
				"varieties": [{"variety_name": "Bodacious"}],
			},
		)
		self.assertEqual(created["varieties"][0]["variety_name"], "Bodacious")


class DuplicateChildRows(AgronomyTestCase):
	def test_two_varieties_of_one_name_are_refused(self):
		message = self.tool_error(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"varieties": [{"variety_name": "Italian"}, {"variety_name": "italian"}],
			},
		)
		self.assertIn("Two rows about one tree", message)

	def test_two_water_rows_for_one_stage_are_refused(self):
		message = self.tool_error(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"water_requirements": [
					{"growth_stage": "Bloom", "crop_coefficient_kc": 0.5},
					{"growth_stage": "Bloom", "crop_coefficient_kc": 0.6},
				],
			},
		)
		self.assertIn("row order", message)

	def test_a_crop_coefficient_out_of_range_is_refused_as_a_decimal_point(self):
		message = self.tool_error(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"water_requirements": [{"growth_stage": "Bloom", "crop_coefficient_kc": 7.5}],
			},
		)
		self.assertIn("decimal point", message)


class ABlankPhiIsNotAPhiOfZero(AgronomyTestCase):
	def test_an_unrecorded_phi_reads_as_null(self):
		created = self.tool_data("create_crop", {"crop_name": "Plum", "crop_type": "Stone Fruit"})
		self.assertIsNone(created["default_phi_days"])

	def test_a_recorded_zero_reads_as_zero(self):
		created = self.tool_data(
			"create_crop", {"crop_name": "Plum", "crop_type": "Stone Fruit", "default_phi_days": 0}
		)
		self.assertEqual(created["default_phi_days"], 0)

	def test_only_the_unrecorded_one_is_listed_as_a_gap(self):
		self.tool_data("create_crop", {"crop_name": "Plum", "crop_type": "Stone Fruit"})
		self.tool_data(
			"create_crop", {"crop_name": "Quince", "crop_type": "Tree Fruit", "default_phi_days": 0}
		)
		listed = self.tool_data("list_crops")
		self.assertEqual(listed["without_phi_recorded"], ["Plum"])

	def test_every_tool_that_reports_a_phi_carries_the_caveat(self):
		"""The binding interval is on the label, not on the crop."""
		self.a_crop()
		for payload in (
			self.tool_data("get_crop", {"crop": "Sweet Cherry"}),
			self.tool_data("update_crop", {"crop": "Sweet Cherry", "default_phi_days": 5}),
		):
			self.assertIn("printed on the label", payload["phi_caveat"])


class ReadingTheCropRegister(AgronomyTestCase):
	def test_it_counts_the_varieties_without_loading_each_crop(self):
		self.a_crop()
		listed = self.tool_data("list_crops")
		self.assertEqual(listed["variety_count"], 2)
		self.assertEqual(listed["crops"][0]["varieties"], ["Bing", "Rainier"])

	def test_it_groups_by_crop_type(self):
		self.a_crop()
		self.tool_data("create_crop", {"crop_name": "Apple", "crop_type": "Tree Fruit"})
		listed = self.tool_data("list_crops")
		self.assertEqual(listed["by_crop_type"], {"Stone Fruit": 1, "Tree Fruit": 1})

	def test_the_crop_type_filter_narrows_it(self):
		self.a_crop()
		self.tool_data("create_crop", {"crop_name": "Apple", "crop_type": "Tree Fruit"})
		listed = self.tool_data("list_crops", {"crop_type": "Tree Fruit"})
		self.assertEqual([crop["crop_name"] for crop in listed["crops"]], ["Apple"])

	def test_a_crop_with_no_varieties_is_reported_as_a_gap(self):
		self.tool_data("create_crop", {"crop_name": "Plum", "crop_type": "Stone Fruit"})
		self.assertEqual(self.tool_data("list_crops")["without_varieties"], ["Plum"])


class ReadingOneCrop(AgronomyTestCase):
	def test_a_bare_name_resolves_case_insensitively(self):
		self.a_crop()
		self.assertEqual(self.tool_data("get_crop", {"crop": "sweet cherry"})["name"], "Sweet Cherry")

	def test_an_unknown_crop_is_refused_with_what_the_site_has(self):
		self.a_crop()
		message = self.tool_error("get_crop", {"crop": "Durian"})
		self.assertIn("Sweet Cherry", message)

	def test_it_reports_the_pollination_groups_actually_planted(self):
		self.a_crop()
		self.assertEqual(
			self.tool_data("get_crop", {"crop": "Sweet Cherry"})["pollination_groups"], ["S1S4", "S3S4"]
		)

	def test_one_pollination_group_across_every_variety_is_reported_not_refused(self):
		"""The pollinizer may be in a neighbouring block or simply unrecorded —
		refusing this would refuse a real orchard."""
		self.tool_data(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"varieties": [
					{"variety_name": "Italian", "pollination_group": "Group 1"},
					{"variety_name": "Stanley", "pollination_group": "Group 1"},
				],
			},
		)
		notes = self.tool_data("get_crop", {"crop": "Plum"})["agronomy_notes"]
		self.assertTrue(any("will not set fruit for each other" in note for note in notes))

	def test_the_markets_that_buy_it_come_back_with_it(self):
		self.a_crop()
		self.a_market(primary_commodity="Sweet Cherry")
		markets = self.tool_data("get_crop", {"crop": "Sweet Cherry"})["markets"]
		self.assertEqual([market["name"] for market in markets], ["Pacific Northwest Fresh Cherry"])

	def test_the_water_stages_come_back_in_order(self):
		self.tool_data(
			"create_crop",
			{
				"crop_name": "Plum",
				"crop_type": "Stone Fruit",
				"water_requirements": [
					{"growth_stage": "Dormant", "crop_coefficient_kc": 0.2},
					{"growth_stage": "Bloom", "crop_coefficient_kc": 0.55},
				],
			},
		)
		payload = self.tool_data("get_crop", {"crop": "Plum"})
		self.assertEqual(payload["water_stages_recorded"], ["Dormant", "Bloom"])


class UpdatingACrop(AgronomyTestCase):
	def test_renaming_is_refused_because_the_docname_is_the_key(self):
		self.a_crop()
		message = self.tool_error("update_crop", {"crop": "Sweet Cherry", "crop_name": "Cherry"})
		self.assertIn("cannot be changed", message)

	def test_a_change_is_echoed_as_before_and_after(self):
		self.a_crop()
		changed = self.tool_data("update_crop", {"crop": "Sweet Cherry", "default_phi_days": 7})
		self.assertEqual(changed["changed"]["default_phi_days"], [3, 7])

	def test_the_variety_table_is_replaced_wholesale_rather_than_merged(self):
		self.a_crop()
		changed = self.tool_data(
			"update_crop", {"crop": "Sweet Cherry", "varieties": [{"variety_name": "Skeena"}]}
		)
		self.assertEqual([row["variety_name"] for row in changed["varieties"]], ["Skeena"])

	def test_an_empty_list_is_how_a_caller_clears_the_table(self):
		self.a_crop()
		changed = self.tool_data("update_crop", {"crop": "Sweet Cherry", "varieties": []})
		self.assertEqual(changed["varieties"], [])

	def test_omitting_the_table_leaves_it_alone(self):
		self.a_crop()
		changed = self.tool_data("update_crop", {"crop": "Sweet Cherry", "days_to_harvest": 65})
		self.assertEqual(len(changed["varieties"]), 2)

	def test_changing_nothing_is_refused_with_the_list_of_what_can_change(self):
		self.a_crop()
		message = self.tool_error("update_crop", {"crop": "Sweet Cherry"})
		self.assertIn("nothing to change", message)

	def test_the_switch_is_off_by_default(self):
		self.a_crop()
		self.configure(enabled=1, allow_get_crop=1)
		message = self.tool_error("update_crop", {"crop": "Sweet Cherry", "days_to_harvest": 65})
		self.assertIn("allow_update_crop", message)


# ── the market register ─────────────────────────────────────────────────────
class CreatingAMarket(AgronomyTestCase):
	def test_the_docname_is_the_market_name(self):
		self.assertEqual(self.a_market()["name"], "Pacific Northwest Fresh Cherry")

	def test_a_second_market_of_the_same_name_is_refused(self):
		self.a_market()
		message = self.tool_error(
			"create_market", {"market_name": "Pacific Northwest Fresh Cherry", "market_type": "Fresh"}
		)
		self.assertIn("two answers to what its grades are", message)

	def test_it_takes_no_company_because_a_market_is_a_place_in_the_world(self):
		"""Every other register in this app is company-scoped. This one is not,
		and the schema is where that is enforced."""
		from erpnext_mcp import registry

		for tool in ("create_market", "list_markets", "create_crop", "list_crops"):
			properties = registry.TOOLS[tool]["inputSchema"]["properties"]
			self.assertNotIn("company", properties, tool)
			self.assertNotIn("owning_entity", properties, tool)

	def test_a_defect_tolerance_over_a_hundred_is_refused(self):
		message = self.tool_error(
			"create_market",
			{
				"market_name": "Juice",
				"market_type": "Processing",
				"grade_standards": [{"grade_name": "Any", "max_defect_pct": 150}],
			},
		)
		self.assertIn("every tolerance comparison pass", message)

	def test_a_negative_premium_is_normal_and_accepted(self):
		"""Juice against fresh. A column refusing these would make every
		operation invent a base grade nothing falls under."""
		created = self.a_market()
		self.assertEqual(
			[grade["premium_pct"] for grade in created["grade_standards"]],
			[25.0, -30.0],
		)

	def test_a_premium_below_minus_one_hundred_is_refused_as_a_sign_error(self):
		message = self.tool_error(
			"create_market",
			{
				"market_name": "Juice",
				"market_type": "Processing",
				"grade_standards": [{"grade_name": "Any", "premium_pct": -140}],
			},
		)
		self.assertIn("sign error", message)

	def test_two_grades_of_one_name_are_refused(self):
		message = self.tool_error(
			"create_market",
			{
				"market_name": "Juice",
				"market_type": "Processing",
				"grade_standards": [{"grade_name": "No. 1"}, {"grade_name": "no. 1"}],
			},
		)
		self.assertIn("Two prices for one grade", message)

	def test_a_market_with_no_grades_is_allowed_and_warned_about(self):
		created = self.tool_data("create_market", {"market_name": "Spot", "market_type": "Fresh"})
		self.assertTrue(any("no packout assumption" in warning for warning in created["warnings"]))

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_market", {"market_name": "Spot", "market_type": "Fresh"})
		self.assertIn("allow_create_market", message)


class ReadingOneMarket(AgronomyTestCase):
	def test_the_grade_ladder_is_sorted_by_what_it_pays_not_by_row_order(self):
		self.a_market()
		payload = self.tool_data("get_market", {"market": "Pacific Northwest Fresh Cherry"})
		self.assertEqual(payload["top_grade"], "Washington Extra Fancy")

	def test_the_premium_spread_is_the_distance_across_the_ladder(self):
		self.a_market()
		payload = self.tool_data("get_market", {"market": "Pacific Northwest Fresh Cherry"})
		self.assertEqual(payload["premium_spread_pct"], 55.0)

	def test_one_grade_has_no_spread_rather_than_a_spread_of_zero(self):
		self.tool_data(
			"create_market",
			{
				"market_name": "Spot",
				"market_type": "Fresh",
				"grade_standards": [{"grade_name": "All", "premium_pct": 0}],
			},
		)
		self.assertIsNone(self.tool_data("get_market", {"market": "Spot"})["premium_spread_pct"])

	def test_an_active_market_with_no_grades_is_named_in_the_planning_notes(self):
		self.tool_data("create_market", {"market_name": "Spot", "market_type": "Fresh"})
		notes = self.tool_data("get_market", {"market": "Spot"})["planning_notes"]
		self.assertTrue(any("number somebody typed" in note for note in notes))

	def test_a_grade_ladder_with_no_sizes_cannot_be_applied_and_says_so(self):
		self.tool_data(
			"create_market",
			{
				"market_name": "Spot",
				"market_type": "Fresh",
				"grade_standards": [{"grade_name": "All", "premium_pct": 0}],
			},
		)
		notes = self.tool_data("get_market", {"market": "Spot"})["planning_notes"]
		self.assertTrue(any("measured size distribution" in note for note in notes))


class ReadingTheMarketRegister(AgronomyTestCase):
	def test_an_active_market_with_no_grades_is_the_headline_gap(self):
		self.a_market()
		self.tool_data("create_market", {"market_name": "Spot", "market_type": "Fresh"})
		listed = self.tool_data("list_markets")
		self.assertEqual(listed["active_without_grade_standards"], ["Spot"])

	def test_a_retired_market_is_not_reported_as_a_gap(self):
		"""Only markets somebody might plan against are worth chasing."""
		self.tool_data("create_market", {"market_name": "Spot", "market_type": "Fresh", "is_active": False})
		self.assertEqual(self.tool_data("list_markets")["active_without_grade_standards"], [])

	def test_the_market_type_filter_narrows_it(self):
		self.a_market()
		self.tool_data("create_market", {"market_name": "WA Processing", "market_type": "Processing"})
		listed = self.tool_data("list_markets", {"market_type": "Processing"})
		self.assertEqual([market["market_name"] for market in listed["markets"]], ["WA Processing"])

	def test_a_market_with_no_shipping_point_cannot_be_joined_to_usda(self):
		self.tool_data("create_market", {"market_name": "Spot", "market_type": "Fresh"})
		self.assertIn("Spot", self.tool_data("list_markets")["without_usda_shipping_point"])


class UpdatingAMarket(AgronomyTestCase):
	def test_renaming_is_refused(self):
		self.a_market()
		message = self.tool_error(
			"update_market", {"market": "Pacific Northwest Fresh Cherry", "market_name": "PNW"}
		)
		self.assertIn("cannot be changed", message)

	def test_retiring_a_market_keeps_it(self):
		self.a_market()
		changed = self.tool_data(
			"update_market", {"market": "Pacific Northwest Fresh Cherry", "is_active": False}
		)
		self.assertFalse(changed["is_active"])
		self.assertEqual(len(STORE.rows("Market")), 1)

	def test_the_grade_ladder_is_replaced_wholesale(self):
		self.a_market()
		changed = self.tool_data(
			"update_market",
			{
				"market": "Pacific Northwest Fresh Cherry",
				"grade_standards": [{"grade_name": "Single", "premium_pct": 0}],
			},
		)
		self.assertEqual([row["grade_name"] for row in changed["grade_standards"]], ["Single"])


# ── unit contexts ───────────────────────────────────────────────────────────
class UnitContexts(AgronomyTestCase):
	def test_it_reports_the_default_unit_for_each_context(self):
		self.a_context()
		listed = self.tool_data("list_ag_uom_contexts")
		self.assertEqual(listed["contexts"][0]["default_uom"], "Bin")

	def test_it_reports_what_each_unit_measures(self):
		self.a_context()
		units = self.tool_data("list_ag_uom_contexts")["contexts"][0]["units"]
		self.assertEqual({unit["uom"]: unit["measures"] for unit in units}, {"Bin": "Count", "Lug": "Count"})

	def test_harvest_and_scale_ticket_stay_apart(self):
		"""A bin is a container and a pound is a weight. One list accepting
		either is a list that lets a counted delivery be added to a weighed one."""
		self.a_context()
		self.a_context(
			"Scale Ticket",
			applies_to="Weight",
			uoms=[{"uom": "Pound", "is_default": 1}, {"uom": "Ton", "is_default": 0}],
		)
		listed = self.tool_data("list_ag_uom_contexts")
		self.assertEqual(
			{context["context_name"]: context["valid_uoms"] for context in listed["contexts"]},
			{"Harvest": ["Bin", "Lug"], "Scale Ticket": ["Pound", "Ton"]},
		)

	def test_the_active_filter_narrows_it(self):
		self.a_context()
		self.a_context("Retired", is_active=0)
		listed = self.tool_data("list_ag_uom_contexts", {"is_active": True})
		self.assertEqual([context["context_name"] for context in listed["contexts"]], ["Harvest"])


# ── the part that multiplies into settlements ───────────────────────────────
class ConversionResolution(AgronomyTestCase):
	def test_a_direct_row_is_read_as_recorded(self):
		self.a_conversion()
		payload = self.tool_data("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound"})
		self.assertEqual(payload["factor"], 800.0)
		self.assertEqual(payload["path"], ["Bin", "Pound"])

	def test_the_reading_spells_the_direction_out(self):
		"""Getting the direction backwards is not a visible error — it is a
		settlement out by a factor of 640,000 that still looks like a number."""
		self.a_conversion()
		payload = self.tool_data("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound"})
		self.assertEqual(payload["reading"], "1 Bin = 800.0 Pound")

	def test_a_row_recorded_the_other_way_round_is_inverted(self):
		self.a_conversion()
		payload = self.tool_data("get_uom_conversions", {"from_uom": "Pound", "to_uom": "Bin"})
		self.assertEqual(payload["factor"], round(1 / 800.0, 6))
		self.assertTrue(any("inverted" in note for note in payload["notes"]))

	def test_the_crop_specific_row_beats_the_generic_one(self):
		self.a_conversion(factor=800.0)
		self.a_conversion(crop="Apple", factor=900.0)
		self.a_crop("Apple", crop_type="Tree Fruit", varieties=[])
		payload = self.tool_data(
			"get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound", "crop": "Apple"}
		)
		self.assertEqual(payload["factor"], 900.0)
		self.assertTrue(payload["crop_specific"])

	def test_falling_back_to_the_generic_row_is_labelled_not_silent(self):
		self.a_conversion(factor=800.0)
		self.a_crop("Pear", crop_type="Tree Fruit", varieties=[])
		payload = self.tool_data(
			"get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound", "crop": "Pear"}
		)
		self.assertFalse(payload["crop_specific"])
		self.assertTrue(any("GENERIC factor" in note for note in payload["notes"]))

	def test_a_crop_with_no_row_and_no_generic_fallback_is_refused_with_the_others_named(self):
		"""The one that matters. Answering with somebody else's fruit is how a
		settlement goes wrong by a factor nobody traces."""
		self.a_conversion(crop="Apple", factor=900.0)
		self.a_crop("Apple", crop_type="Tree Fruit", varieties=[])
		self.a_crop("Peach", crop_type="Stone Fruit", varieties=[])
		message = self.tool_error(
			"get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound", "crop": "Peach"}
		)
		self.assertIn("Apple", message)
		self.assertIn("Record the factor for Peach", message)

	def test_a_superseded_row_is_kept_and_not_consulted(self):
		self.a_conversion(factor=750.0, is_active=0)
		message = self.tool_error("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound"})
		self.assertIn("no ACTIVE conversion", message)
		self.assertEqual(len(STORE.rows("Agricultural UOM Conversion")), 1)

	def test_it_chains_through_one_intermediate_unit(self):
		self.a_conversion(from_uom="Bin", to_uom="Pound", factor=800.0)
		self.a_conversion(from_uom="Ton", to_uom="Pound", factor=2000.0, basis="Exact")
		payload = self.tool_data("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Ton"})
		self.assertEqual(payload["path"], ["Bin", "Pound", "Ton"])
		self.assertEqual(payload["factor"], 0.4)

	def test_a_chain_reports_the_weaker_of_its_two_bases(self):
		"""An exact hop composed with a nominal one is nominal. Reporting the
		chain as Exact would be the most misleading thing this could say."""
		self.a_conversion(from_uom="Bin", to_uom="Pound", factor=800.0, basis="Nominal")
		self.a_conversion(from_uom="Ton", to_uom="Pound", factor=2000.0, basis="Exact")
		payload = self.tool_data("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Ton"})
		self.assertEqual(payload["basis"], "Nominal")

	def test_two_hops_are_not_attempted(self):
		"""Past one hop it is multiplying three nominal figures together, and
		the compounding error exceeds the answer's worth."""
		self.a_conversion(from_uom="Bucket", to_uom="Lug", factor=1.5)
		self.a_conversion(from_uom="Lug", to_uom="Pound", factor=18.0)
		self.a_conversion(from_uom="Ton", to_uom="Pound", factor=2000.0, basis="Exact")
		message = self.tool_error("get_uom_conversions", {"from_uom": "Bucket", "to_uom": "Ton"})
		self.assertIn("through any single intermediate unit", message)

	def test_converting_a_unit_to_itself_is_refused(self):
		message = self.tool_error("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Bin"})
		self.assertIn("is not a conversion", message)

	def test_every_row_for_the_pair_comes_back_so_the_caller_can_check(self):
		self.a_conversion(factor=800.0)
		self.a_conversion(crop="Apple", factor=900.0)
		self.a_crop("Apple", crop_type="Tree Fruit", varieties=[])
		payload = self.tool_data("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound"})
		self.assertEqual(sorted(row["factor"] for row in payload["rows_for_this_pair"]), [800.0, 900.0])

	def test_a_nominal_factor_says_what_it_is_not_good_enough_for(self):
		self.a_conversion()
		payload = self.tool_data("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound"})
		self.assertTrue(any("settle a dispute" in note for note in payload["notes"]))

	def test_the_switch_is_on_by_default_because_it_is_a_read(self):
		self.configure(enabled=1)
		self.a_conversion()
		self.tool_data("get_uom_conversions", {"from_uom": "Bin", "to_uom": "Pound"})
