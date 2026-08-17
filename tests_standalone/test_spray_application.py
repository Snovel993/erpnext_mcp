# SPDX-License-Identifier: MIT
"""The spray program: the tank, the flip, the wind, and the record of the pass.

Each class below is one claim, and the first two are the ones that would matter
in a hearing.

1. **A TANK THAT RESTRICTS NOBODY IS STILL A SPRAY.** `TheTankWithNoInterval`.
   This is the deliberate difference from `record_spray_application`, which
   refuses in that case. A tank of foliar nitrogen has no label interval, closes
   no block, and is a real pass over real acres — refusing it would push the
   record onto a clipboard. It records, creates ZERO Spray REI rows, and says so.

2. **THE LONGEST INTERVAL IN THE TANK STILL WINS, AND PER BLOCK.**
   `TheWindowsItOpens`. One REI per block, from that block's OWN completion time
   where the pass was long enough for them to differ — a block sprayed at eight
   does not stay shut because the last block finished at two.

3. **MULTI-PRODUCT TANKS CARRY PER-PRODUCT RATES.** `TheMultiProductTank`. Not
   one rate for the tank: a cover spray is several answers to several problems
   and only a per-product rate can be checked against a label.

4. **A DUAL MIX WITH NOTHING ON ONE SIDE IS REFUSED.** `TheDualFlipNozzle`. The
   flag's whole purpose is that an application will ask which set was running
   where, and a mix that cannot answer is a single-set mix ticked by accident.

5. **WEATHER IS RECORDED, NEVER ENFORCED.** `TheWeatherIsAdvisory`. Including
   the non-obvious direction: wind BELOW the label window is an inversion, not
   calm safety, and earns an advisory of its own.

6. **THE MIX IS COPIED, NOT LINKED.** `TheMixIsCopiedOntoTheApplication`. A
   recipe re-rated in July did not change what went out in May.
"""

from erpnext_mcp import compliance_fields

from .fixtures import MAIN, SPRAY, STORES, V12TestCase, seed_masters, seed_stock
from .harness import STORE

#: A 24-hour product, so "longest wins" has two intervals to choose between.
SULFUR = "SULFUR-90"

#: A foliar nutrient with no interval at all. What makes "no window" a real
#: answer rather than an absent computation.
NUTRIENT = "FOLIAR-N"

BLOCK = "Yellow Camp Block 3 - MC"
BLOCK_TWO = "Yellow Camp Block 4 - MC"

UPPER = "Air Blast Upper"
LOWER = "Air Blast Lower"
MIX = "Petal Fall Cover 1"

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_spray_nozzle_config",
		"list_spray_nozzle_configs",
		"create_spray_tank_mix",
		"create_spray_application",
		"list_spray_applications",
		"get_spray_application",
		"get_active_rei",
		"list_active_reis",
		"create_parcel",
		"create_field",
		"register_asset",
	)
}


class SprayTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		seed_masters()
		seed_stock()
		self.configure(enabled=1, **ALL_ON)
		self._install_item_intervals()
		self._farm()

	def _install_item_intervals(self):
		"""The REI columns, through the real installer — not written by hand.

		The whole mechanism turns on `compat.has_field` seeing the column, and a
		fixture that added it directly would prove the window works on a site
		configured in a way this app never produces.
		"""
		compliance_fields.install_compliance_fields()
		STORE.seed("UOM", [{"name": "Gal", "enabled": 1}, {"name": "Lb", "enabled": 1}])
		STORE.seed(
			"Item",
			[
				{
					"name": SULFUR,
					"item_code": SULFUR,
					"item_name": "Micronised Sulfur 90",
					"stock_uom": "Lb",
					"is_stock_item": 1,
					"disabled": 0,
					"item_defaults": [{"company": MAIN, "default_warehouse": STORES}],
					"reorder_levels": [],
					"rei_hours": 24,
					"phi_days": 1,
				},
				{
					"name": NUTRIENT,
					"item_code": NUTRIENT,
					"item_name": "Foliar Nitrogen",
					"stock_uom": "Gal",
					"is_stock_item": 1,
					"disabled": 0,
					"item_defaults": [{"company": MAIN, "default_warehouse": STORES}],
					"reorder_levels": [],
					"rei_hours": 0,
					"phi_days": 0,
				},
			],
		)
		spray = STORE.get_raw("Item", SPRAY)
		spray["rei_hours"] = 4
		spray["phi_days"] = 14

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
					"variety": "Bing",
					"planting_year": 1998,
					"condition": "Good",
				},
			)

	# ── helpers ────────────────────────────────────────────────────────────
	def a_nozzle(self, name=UPPER, **kw):
		payload = {
			"nozzle_name": name,
			"flow_rate_gpm": 0.4,
			"nozzle_type": "Hollow Cone",
			"pattern": "Canopy Upper",
			"droplet_class": "Coarse",
			"spacing_inches": 20,
			"rated_pressure_psi": 100,
			"company": MAIN,
		}
		payload.update(kw)
		return self.tool_data("create_spray_nozzle_config", payload)

	def a_mix(self, name=MIX, products=None, **kw):
		payload = {
			"mix_name": name,
			"products": products
			if products is not None
			else [{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"}],
			"tank_size_gal": 500,
			"carrier_gpa": 100,
			"company": MAIN,
		}
		payload.update(kw)
		return self.tool_data("create_spray_tank_mix", payload)

	def an_application(self, blocks=(BLOCK,), **kw):
		payload = {"blocks": list(blocks), "company": MAIN}
		payload.setdefault("products", [{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb"}])
		payload.update(kw)
		return self.tool_data("create_spray_application", payload)

	def rei_rows(self):
		return list(STORE.tables.get("Spray REI", {}).values())


# ── 1. the difference from record_spray_application ─────────────────────────
class TheTankWithNoInterval(SprayTestCase):
	def test_a_foliar_nutrient_is_recorded_and_restricts_nobody(self):
		"""`record_spray_application` refuses this case because its whole purpose
		is the window. This tool's purpose is the RECORD of the pass, and a tank
		of foliar nitrogen over twelve acres is a real pass."""
		data = self.an_application(products=[{"item_code": NUTRIENT, "rate_per_acre": 2, "rate_uom": "Gal"}])
		self.assertEqual(data["rei_hours"], 0)
		self.assertEqual(data["rei_records_created"], 0)
		self.assertEqual(self.rei_rows(), [])
		self.assertTrue(any("no restricted-entry window" in note for note in data["notes_for_caller"]))

	def test_the_application_itself_is_on_the_record(self):
		"""The point of not refusing: the acres, the block and the weather are
		kept even though nothing was restricted."""
		data = self.an_application(
			blocks=[{"block": BLOCK, "acres": 12.5}],
			products=[{"item_code": NUTRIENT, "rate_per_acre": 2}],
			wind_speed_mph=6,
		)
		self.assertEqual(data["total_acres"], 12.5)
		self.assertEqual(data["weather"]["wind_speed_mph"], 6)
		self.assertEqual(len(data["blocks"]), 1)

	def test_a_planned_application_opens_nothing_even_with_a_real_interval(self):
		"""Nothing has been put on the ground, and a restriction on a block
		nobody has sprayed keeps a crew out of somewhere they may work."""
		data = self.an_application(status="Planned")
		self.assertEqual(data["rei_records_created"], 0)
		self.assertEqual(self.rei_rows(), [])
		self.assertTrue(any("Planned" in note for note in data["notes_for_caller"]))

	def test_a_stated_interval_of_zero_is_still_refused(self):
		"""Omitting it means 'restricts nobody'; stating zero is a caller who
		meant something and got it wrong."""
		error = self.tool_error(
			"create_spray_application",
			{"blocks": [BLOCK], "products": [{"item_code": SPRAY, "rate_per_acre": 5}], "rei_hours": 0},
		)
		self.assertIn("greater than zero", error)

	def test_a_pass_over_nowhere_is_refused(self):
		error = self.tool_error(
			"create_spray_application", {"products": [{"item_code": SPRAY, "rate_per_acre": 5}]}
		)
		self.assertIn("blocks is required", error)

	def test_an_empty_tank_is_refused(self):
		error = self.tool_error("create_spray_application", {"blocks": [BLOCK]})
		self.assertIn("nothing is in the tank", error)


# ── 2. the windows it opens ─────────────────────────────────────────────────
class TheWindowsItOpens(SprayTestCase):
	def test_the_longest_interval_in_the_tank_wins(self):
		data = self.an_application(
			products=[
				{"item_code": SPRAY, "rate_per_acre": 5},
				{"item_code": SULFUR, "rate_per_acre": 10},
			]
		)
		self.assertEqual(data["rei_hours"], 24)
		self.assertEqual(data["rei_source_item"], SULFUR)

	def test_one_window_per_block(self):
		"""Two blocks clear together and each is asked about separately."""
		data = self.an_application(blocks=(BLOCK, BLOCK_TWO))
		self.assertEqual(data["rei_records_created"], 2)
		self.assertEqual({row["block"] for row in self.rei_rows()}, {BLOCK, BLOCK_TWO})

	def test_each_block_row_links_back_to_the_window_it_opened(self):
		"""The application and the restriction are one chain rather than two
		registers somebody has to join by hand."""
		data = self.an_application(blocks=(BLOCK, BLOCK_TWO))
		for row in data["blocks"]:
			self.assertTrue(row["rei_record"], f"{row['block']} has no REI record linked")

	def test_a_block_that_finished_early_clears_early(self):
		"""A block sprayed at eight in the morning does not stay shut because the
		last block of the pass finished at two."""
		data = self.an_application(
			blocks=[
				{"block": BLOCK, "completed_at": "2026-05-04 08:00:00"},
				{"block": BLOCK_TWO, "completed_at": "2026-05-04 14:00:00"},
			],
			completed_at="2026-05-04 14:00:00",
		)
		self.assertEqual(data["rei_records_created"], 2)
		expiries = {row["block"]: str(row["expires_at"]) for row in self.rei_rows()}
		self.assertLess(expiries[BLOCK], expiries[BLOCK_TWO])

	def test_get_active_rei_answers_for_the_window_this_opened(self):
		"""The integration that matters: the existing REI reads see what this
		wrote, because it writes the same records they have always read."""
		self.an_application(blocks=(BLOCK,))
		data = self.tool_data("get_active_rei", {"block": BLOCK, "company": MAIN})
		self.assertTrue(data["restricted"])

	def test_the_stored_tank_is_per_block_acres_not_the_whole_pass(self):
		"""The question asked after somebody feels ill is what went onto the
		ground they were standing on."""
		self.an_application(blocks=[{"block": BLOCK, "acres": 10}])
		data = self.tool_data("get_active_rei", {"block": BLOCK, "company": MAIN})
		line = data["active_reis"][0]["products"][0]
		self.assertEqual(line["qty"], 50.0)

	def test_a_stated_interval_overrides_every_label(self):
		data = self.an_application(rei_hours=48)
		self.assertEqual(data["rei_hours"], 48)
		self.assertEqual(float(self.rei_rows()[0]["rei_hours"]), 48.0)

	def test_a_completion_before_the_start_is_refused(self):
		error = self.tool_error(
			"create_spray_application",
			{
				"blocks": [BLOCK],
				"products": [{"item_code": SPRAY, "rate_per_acre": 5}],
				"started_at": "2026-05-04 14:00:00",
				"completed_at": "2026-05-04 08:00:00",
			},
		)
		self.assertIn("before started_at", error)


# ── 3. several products, each at its own rate ───────────────────────────────
class TheMultiProductTank(SprayTestCase):
	def test_each_product_keeps_its_own_rate(self):
		data = self.a_mix(
			products=[
				{"item_code": SPRAY, "rate_per_acre": 5, "rate_uom": "Lb", "target": "mildew"},
				{"item_code": SULFUR, "rate_per_acre": 10, "rate_uom": "Lb", "target": "mites"},
			]
		)
		rates = {line["item"]: line["rate_per_acre"] for line in data["products"]}
		self.assertEqual(rates, {SPRAY: 5.0, SULFUR: 10.0})

	def test_the_mix_rolls_up_the_strictest_label(self):
		data = self.a_mix(
			products=[
				{"item_code": SPRAY, "rate_per_acre": 5},
				{"item_code": SULFUR, "rate_per_acre": 10},
			]
		)
		self.assertEqual(data["rei_hours"], 24)
		self.assertEqual(data["rei_source_item"], SULFUR)
		self.assertEqual(data["phi_days"], 14)
		self.assertEqual(data["phi_source_item"], SPRAY)

	def test_label_numbers_are_copied_onto_the_line_not_fetched(self):
		"""A mix read years later has to say what the label said THEN.

		The order is the whole test: file the pass, THEN correct the Item's
		interval, then re-read. A record that resolved its labels through a live
		link would come back saying 99 — which is what the label says now, and a
		different claim from what went out.
		"""
		mix = self.a_mix()
		self.assertEqual(mix["products"][0]["rei_hours"], 4.0)
		filed = self.an_application(tank_mix=MIX, products=None)

		STORE.get_raw("Item", SPRAY)["rei_hours"] = 99

		again = self.tool_data("get_spray_application", {"application": filed["name"]})
		self.assertEqual(again["products_applied"][0]["rei_hours"], 4.0)
		self.assertEqual(again["rei_hours"], 4.0)

	def test_acres_per_tank_is_computed(self):
		data = self.a_mix(tank_size_gal=500, carrier_gpa=100)
		self.assertEqual(data["acres_per_tank"], 5.0)

	def test_one_product_twice_is_refused(self):
		"""Two lines is two rates for one product, and nothing reading the mix
		could say which applied."""
		error = self.tool_error(
			"create_spray_tank_mix",
			{
				"mix_name": "Doubled",
				"products": [
					{"item_code": SPRAY, "rate_per_acre": 5},
					{"item_code": SPRAY, "rate_per_acre": 8},
				],
			},
		)
		self.assertIn("listed twice", error)

	def test_a_rate_of_zero_is_refused(self):
		error = self.tool_error(
			"create_spray_tank_mix",
			{"mix_name": "Zeroed", "products": [{"item_code": SPRAY, "rate_per_acre": 0}]},
		)
		self.assertIn("greater than zero", error)

	def test_the_total_applied_is_rate_times_acres(self):
		data = self.an_application(
			blocks=[{"block": BLOCK, "acres": 10}, {"block": BLOCK_TWO, "acres": 2.5}],
			products=[{"item_code": SPRAY, "rate_per_acre": 5}],
		)
		self.assertEqual(data["total_acres"], 12.5)
		self.assertEqual(data["products_applied"][0]["total_applied"], 62.5)


# ── 4. the dual flip nozzle ─────────────────────────────────────────────────
class TheDualFlipNozzle(SprayTestCase):
	def setUp(self):
		super().setUp()
		self.a_nozzle(UPPER)
		self.a_nozzle(LOWER, pattern="Canopy Lower")

	def test_a_dual_mix_splits_products_across_two_sets(self):
		data = self.a_mix(
			products=[
				{"item_code": SPRAY, "rate_per_acre": 5, "nozzle_set": "A"},
				{"item_code": SULFUR, "rate_per_acre": 10, "nozzle_set": "B"},
			],
			dual_nozzle=True,
			nozzle_set_a=UPPER,
			nozzle_set_b=LOWER,
			set_a_purpose="fungicide, upper canopy",
			set_b_purpose="miticide, lower canopy",
		)
		self.assertTrue(data["dual_nozzle"])
		sets = {line["item"]: line["nozzle_set"] for line in data["products"]}
		self.assertEqual(sets, {SPRAY: "A", SULFUR: "B"})

	def test_a_dual_mix_with_nothing_on_set_b_is_refused(self):
		"""It is a single-set mix somebody ticked by accident, and the cheapest
		place to find that out is here."""
		error = self.tool_error(
			"create_spray_tank_mix",
			{
				"mix_name": "Half Dual",
				"products": [{"item_code": SPRAY, "rate_per_acre": 5, "nozzle_set": "A"}],
				"dual_nozzle": True,
				"nozzle_set_a": UPPER,
				"nozzle_set_b": LOWER,
			},
		)
		self.assertIn("no product is assigned to set B", error)

	def test_a_dual_mix_naming_one_set_twice_is_refused(self):
		error = self.tool_error(
			"create_spray_tank_mix",
			{
				"mix_name": "Same Twice",
				"products": [
					{"item_code": SPRAY, "rate_per_acre": 5, "nozzle_set": "A"},
					{"item_code": SULFUR, "rate_per_acre": 10, "nozzle_set": "B"},
				],
				"dual_nozzle": True,
				"nozzle_set_a": UPPER,
				"nozzle_set_b": UPPER,
			},
		)
		self.assertIn("one set rather than two", error)

	def test_a_dual_mix_with_an_unnamed_set_is_refused(self):
		error = self.tool_error(
			"create_spray_tank_mix",
			{
				"mix_name": "Unnamed Set",
				"products": [
					{"item_code": SPRAY, "rate_per_acre": 5, "nozzle_set": "A"},
					{"item_code": SULFUR, "rate_per_acre": 10, "nozzle_set": "B"},
				],
				"dual_nozzle": True,
				"nozzle_set_a": UPPER,
			},
		)
		self.assertIn("names both nozzle sets", error)

	def test_which_set_ran_is_recorded_per_block(self):
		"""The flip usually happens at a block edge, and an application that says
		'we flipped somewhere' cannot say which block got which product."""
		data = self.an_application(
			blocks=[
				{"block": BLOCK, "nozzle_set_used": "A"},
				{"block": BLOCK_TWO, "nozzle_set_used": "A then B"},
			],
			nozzle_set_a=UPPER,
			nozzle_set_b=LOWER,
			dual_nozzle=True,
			flip_performed=True,
		)
		used = {row["block"]: row["nozzle_set_used"] for row in data["blocks"]}
		self.assertEqual(used, {BLOCK: "A", BLOCK_TWO: "A then B"})
		self.assertTrue(data["flip_performed"])

	def test_a_dual_pass_with_no_flip_is_called_out(self):
		"""Only one set's products reached the ground, so the record of what was
		applied overstates it."""
		data = self.an_application(
			nozzle_set_a=UPPER, nozzle_set_b=LOWER, dual_nozzle=True, flip_performed=False
		)
		self.assertTrue(any("flip_performed" in note for note in data["notes_for_caller"]))

	def test_an_unknown_nozzle_set_is_refused(self):
		error = self.tool_error(
			"create_spray_application",
			{
				"blocks": [BLOCK],
				"products": [{"item_code": SPRAY, "rate_per_acre": 5}],
				"nozzle_set_a": "No Such Tip",
			},
		)
		self.assertIn("no Spray Nozzle Config", error)

	def test_gallons_per_acre_is_computed_from_flow_spacing_and_speed(self):
		"""The calibration figure an inspector recomputes rather than reads.
		0.4 GPM through 20-inch spacing at 3 mph is 0.4 * 5940 / (3 * 20)."""
		data = self.an_application(nozzle_set_a=UPPER, ground_speed_mph=3)
		self.assertAlmostEqual(data["gallons_per_acre"], 39.6, places=1)

	def test_gallons_per_acre_is_blank_without_a_ground_speed(self):
		"""A wrong GPA is worse than none, because the wrong one gets filed."""
		data = self.an_application(nozzle_set_a=UPPER)
		self.assertIsNone(data["gallons_per_acre"])


# ── 5. weather ──────────────────────────────────────────────────────────────
class TheWeatherIsAdvisory(SprayTestCase):
	def test_high_wind_is_recorded_not_refused(self):
		"""The tank went out three hours ago. A refusal does not prevent the
		spray, only the record of it."""
		data = self.an_application(wind_speed_mph=14)
		self.assertTrue(data["name"])
		self.assertTrue(any("drift" in line for line in data["weather_advisories"]))

	def test_low_wind_earns_an_inversion_advisory(self):
		"""The non-obvious direction. Still air is not the safe end."""
		data = self.an_application(wind_speed_mph=1)
		self.assertTrue(any("inversion" in line for line in data["weather_advisories"]))

	def test_wind_inside_the_window_earns_no_wind_advisory(self):
		data = self.an_application(wind_speed_mph=6, weather_source="Observed")
		self.assertFalse(any("mph" in line for line in data["weather_advisories"]))

	def test_no_wind_recorded_at_all_is_itself_an_advisory(self):
		"""It is the most asked-for line on a state record and it cannot be
		reconstructed afterwards."""
		data = self.an_application()
		self.assertTrue(any("No wind speed recorded" in line for line in data["weather_advisories"]))

	def test_a_dead_calm_reading_is_not_the_same_as_no_reading(self):
		"""Zero is a real and important observation; None is a gap in a
		compliance record. Collapsing both to 0 would hide the difference on
		exactly the field where it matters most."""
		calm = self.an_application(wind_speed_mph=0)
		self.assertEqual(calm["weather"]["wind_speed_mph"], 0)
		missing = self.an_application(blocks=(BLOCK_TWO,))
		self.assertIsNone(missing["weather"]["wind_speed_mph"])

	def test_high_temperature_earns_an_advisory(self):
		data = self.an_application(wind_speed_mph=6, temperature_f=95)
		self.assertTrue(any("volatilisation" in line for line in data["weather_advisories"]))

	def test_a_planned_application_earns_no_weather_advisory(self):
		"""An advisory about the weather at a moment that has not happened is
		noise on a work order."""
		data = self.an_application(status="Planned")
		self.assertEqual(data["weather_advisories"], [])

	def test_the_list_names_the_passes_with_no_wind(self):
		self.an_application(blocks=(BLOCK,))
		self.an_application(blocks=(BLOCK_TWO,), wind_speed_mph=6)
		data = self.tool_data("list_spray_applications", {"company": MAIN})
		self.assertEqual(len(data["applications_without_wind_recorded"]), 1)

	def test_a_missing_applicator_licence_is_called_out(self):
		data = self.an_application(wind_speed_mph=6)
		self.assertTrue(any("licence" in note for note in data["notes_for_caller"]))


# ── 6. the mix is copied, not linked ────────────────────────────────────────
class TheMixIsCopiedOntoTheApplication(SprayTestCase):
	def test_the_application_carries_the_mix_as_it_stood(self):
		self.a_mix()
		data = self.an_application(tank_mix=MIX, products=None)
		self.assertEqual(data["products_applied"][0]["item"], SPRAY)
		self.assertEqual(data["products_applied"][0]["rate_per_acre"], 5.0)

	def test_a_departure_from_the_recipe_is_reported_not_refused(self):
		"""A crew that halved a rate applied something other than the recipe, and
		the record has to be of what went out."""
		self.a_mix()
		data = self.an_application(tank_mix=MIX, products=[{"item_code": SPRAY, "rate_per_acre": 2.5}])
		self.assertEqual(data["products_applied"][0]["rate_per_acre"], 2.5)
		self.assertTrue(any("differs from tank mix" in note for note in data["notes_for_caller"]))

	def test_a_mix_matching_what_was_applied_raises_no_difference_note(self):
		self.a_mix()
		data = self.an_application(tank_mix=MIX, products=[{"item_code": SPRAY, "rate_per_acre": 5}])
		self.assertFalse(any("differs from tank mix" in note for note in data["notes_for_caller"]))

	def test_an_unknown_mix_is_refused(self):
		error = self.tool_error("create_spray_application", {"blocks": [BLOCK], "tank_mix": "No Such Mix"})
		self.assertIn("no Spray Tank Mix", error)

	def test_a_duplicate_mix_name_is_refused(self):
		self.a_mix()
		error = self.tool_error(
			"create_spray_tank_mix",
			{"mix_name": MIX, "products": [{"item_code": SPRAY, "rate_per_acre": 5}]},
		)
		self.assertIn("already exists", error)


# ── 7. the reads ────────────────────────────────────────────────────────────
class TheReads(SprayTestCase):
	def test_get_reports_which_blocks_are_still_restricted(self):
		data = self.an_application(blocks=(BLOCK, BLOCK_TWO))
		detail = self.tool_data("get_spray_application", {"application": data["name"]})
		self.assertEqual(sorted(detail["blocks_restricted_now"]), sorted([BLOCK, BLOCK_TWO]))

	def test_a_nutrient_pass_shows_no_block_restricted(self):
		data = self.an_application(products=[{"item_code": NUTRIENT, "rate_per_acre": 2}])
		detail = self.tool_data("get_spray_application", {"application": data["name"]})
		self.assertEqual(detail["blocks_restricted_now"], [])

	def test_the_list_totals_the_acres(self):
		self.an_application(blocks=[{"block": BLOCK, "acres": 10}])
		self.an_application(blocks=[{"block": BLOCK_TWO, "acres": 2.5}])
		data = self.tool_data("list_spray_applications", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["total_acres"], 12.5)

	def test_the_list_can_be_filtered_to_one_block(self):
		self.an_application(blocks=(BLOCK,))
		self.an_application(blocks=(BLOCK_TWO,))
		data = self.tool_data("list_spray_applications", {"company": MAIN, "block": BLOCK})
		self.assertEqual(data["count"], 1)

	def test_an_unknown_application_is_refused_by_name(self):
		error = self.tool_error("get_spray_application", {"application": "NOPE"})
		self.assertIn("no Spray Application", error)

	def test_nozzle_configs_list_and_hide_disabled_ones(self):
		self.a_nozzle(UPPER)
		self.a_nozzle(LOWER, pattern="Canopy Lower", disabled=True)
		visible = self.tool_data("list_spray_nozzle_configs", {"company": MAIN})
		self.assertEqual(visible["count"], 1)
		everything = self.tool_data("list_spray_nozzle_configs", {"company": MAIN, "include_disabled": True})
		self.assertEqual(everything["count"], 2)

	def test_a_nozzle_with_no_flow_is_refused(self):
		error = self.tool_error("create_spray_nozzle_config", {"nozzle_name": "Dead Tip", "flow_rate_gpm": 0})
		self.assertIn("greater than zero", error)

	def test_a_nozzle_with_no_droplet_class_is_warned_about(self):
		"""It is the drift control on most labels."""
		data = self.tool_data(
			"create_spray_nozzle_config",
			{"nozzle_name": "Plain Tip", "flow_rate_gpm": 0.3, "company": MAIN},
		)
		self.assertTrue(any("droplet class" in warning for warning in data["warnings"]))
