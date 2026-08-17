# SPDX-License-Identifier: MIT
"""Observation → pressure → recommendation, and the judgement in the middle.

Each class is one claim about the pipeline, and the order is by how much damage
getting it wrong would do.

1. **THE BENEFICIAL OVERRIDE.** `TheBeneficialsOverrideTheThreshold`. A count
   over threshold with predators eating it does NOT generate a control — it
   generates 'hold and re-scout'. This is the difference between integrated pest
   management and pest counting, and getting it wrong means spraying a block that
   was already handling itself, killing the predators, and guaranteeing a worse
   flare in three weeks.

2. **THE COMPARISON IS A COLUMN.** `TheComparisonIsNotAnAssumption`. A Nutrient
   threshold fires BELOW its number. A hard-coded greater-than would fire on
   every healthy block and never on a deficient one — wrong in both directions
   at once, and silently.

3. **A SAMPLE TOO SMALL TO SAY GENERATES NOTHING.** `TheSampleHasToBeBigEnough`.
   Two infested leaves out of five is not twenty percent infestation.

4. **A UNIT MISMATCH STOPS THE EVALUATION.** `TheUnitsHaveToMatch`. 'Per trap'
   measured against 'percent infested' is arithmetically fine and meaningless.

5. **NO THRESHOLD IS AN ANSWER, NOT AN ERROR.** `TheMissingThresholdIsAGap`. It
   is the ordinary state of a first season, and the observations are how you find
   out what the number should be.

6. **THE SCALE.** `TheSustainabilityScore`. Including the switch from scoring
   what was offered to scoring what was chosen, which is the line that stops the
   score rewarding a farm for options it declined.
"""

from .fixtures import MAIN, V12TestCase, seed_masters
from .harness import STORE

BLOCK = "Yellow Camp Block 3 - MC"
BLOCK_TWO = "Yellow Camp Block 4 - MC"

CHERRY = "Cherry"
MOTH = "Codling Moth"
MITE = "Spider Mite"
BORON = "Boron"

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"set_pest_action_threshold",
		"list_pest_action_thresholds",
		"create_crop_observation",
		"list_crop_observations",
		"get_pest_pressure",
		"list_pest_pressures",
		"get_ipm_recommendation",
		"compute_sustainability_score",
		"create_parcel",
		"create_field",
	)
}


class CropProtectionTestCase(V12TestCase):
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
					"planting_year": 1998,
					"condition": "Good",
				},
			)

	# ── helpers ────────────────────────────────────────────────────────────
	def a_threshold(self, **kw):
		payload = {
			"crop": CHERRY,
			"threat": MOTH,
			"threat_category": "Insect",
			"action_threshold": 5,
			"sample_unit": "Per Trap",
			"company": MAIN,
			"recommended_methods": (
				"Cultural: remove alternate hosts on the headland\n"
				"Biological: release Trichogramma\n"
				"Chemical: cover spray at label rate"
			),
		}
		payload.update(kw)
		return self.tool_data("set_pest_action_threshold", payload)

	def an_observation(self, **kw):
		payload = {
			"block": BLOCK,
			"threat": MOTH,
			"threat_category": "Insect",
			"crop": CHERRY,
			"count_observed": 8,
			"sample_unit": "Per Trap",
			"sample_size": 10,
			"company": MAIN,
		}
		payload.update(kw)
		return self.tool_data("create_crop_observation", payload)

	def pressure_rows(self):
		return list(STORE.tables.get("Pest Pressure", {}).values())

	def recommendation_rows(self):
		return list(STORE.tables.get("IPM Recommendation", {}).values())


# ── 1. the beneficial override ──────────────────────────────────────────────
class TheBeneficialsOverrideTheThreshold(CropProtectionTestCase):
	def setUp(self):
		super().setUp()
		self.a_threshold(threat=MITE, sample_unit="Per Leaf", action_threshold=5, beneficial_ratio_min=0.5)

	def test_predators_at_the_ratio_recommend_holding_rather_than_spraying(self):
		"""The block is already handling itself. The spray that 'fixes' it kills
		the predators and guarantees a worse flare in three weeks."""
		data = self.an_observation(
			threat=MITE,
			sample_unit="Per Leaf",
			count_observed=10,
			beneficials_observed=8,
			beneficial_name="Typhlodromus pyri",
		)
		self.assertTrue(data["evaluation"]["action_exceeded"])
		self.assertTrue(data["evaluation"]["beneficials_holding"])
		methods = [row["control_method"] for row in data["ipm_recommendation_detail"]["actions"]]
		self.assertEqual(methods, ["No Action"])

	def test_the_hold_replaces_the_control_options_rather_than_joining_them(self):
		"""Presenting 'hold and re-scout' alongside 'cover spray' is presenting no
		recommendation at all."""
		data = self.an_observation(
			threat=MITE, sample_unit="Per Leaf", count_observed=10, beneficials_observed=8
		)
		self.assertEqual(data["ipm_recommendation_detail"]["chemical_actions"], 0)

	def test_too_few_predators_still_recommends_a_control(self):
		data = self.an_observation(
			threat=MITE, sample_unit="Per Leaf", count_observed=10, beneficials_observed=1
		)
		self.assertFalse(data["evaluation"]["beneficials_holding"])
		methods = [row["control_method"] for row in data["ipm_recommendation_detail"]["actions"]]
		self.assertIn("Chemical", methods)

	def test_the_rationale_says_why_it_held(self):
		data = self.an_observation(
			threat=MITE, sample_unit="Per Leaf", count_observed=10, beneficials_observed=8
		)
		self.assertIn("beneficials are present", data["evaluation"]["note"].lower())

	def test_a_held_recommendation_is_routine_not_urgent(self):
		data = self.an_observation(
			threat=MITE, sample_unit="Per Leaf", count_observed=40, beneficials_observed=30
		)
		self.assertEqual(data["ipm_recommendation_detail"]["urgency"], "Routine")

	def test_holding_scores_full_marks(self):
		"""It is the hardest call in the discipline and the one a programme most
		often gets wrong by acting."""
		data = self.an_observation(
			threat=MITE, sample_unit="Per Leaf", count_observed=10, beneficials_observed=8
		)
		self.assertEqual(data["ipm_recommendation_detail"]["sustainability_score"], 100.0)


# ── 2. the comparison is a column ───────────────────────────────────────────
class TheComparisonIsNotAnAssumption(CropProtectionTestCase):
	def test_a_nutrient_threshold_fires_below_its_number(self):
		"""Tissue nitrogen under 2.2 percent is the finding, not over it."""
		self.a_threshold(
			threat=BORON,
			threat_category="Nutrient",
			action_threshold=2.2,
			comparison="Less Than",
			sample_unit="Percent Dry Weight",
			recommended_methods="Cultural: foliar boron at petal fall",
		)
		low = self.an_observation(
			threat=BORON,
			threat_category="Nutrient",
			count_observed=1.8,
			sample_unit="Percent Dry Weight",
		)
		self.assertTrue(low["evaluation"]["action_exceeded"])

	def test_a_healthy_nutrient_reading_does_not_fire(self):
		"""The half a hard-coded greater-than would get wrong on every block."""
		self.a_threshold(
			threat=BORON,
			threat_category="Nutrient",
			action_threshold=2.2,
			comparison="Less Than",
			sample_unit="Percent Dry Weight",
		)
		healthy = self.an_observation(
			threat=BORON,
			threat_category="Nutrient",
			count_observed=3.1,
			sample_unit="Percent Dry Weight",
		)
		self.assertFalse(healthy["evaluation"]["action_exceeded"])
		self.assertEqual(self.recommendation_rows(), [])

	def test_a_falling_nutrient_reading_is_a_rising_pressure(self):
		"""Rising means DETERIORATING, not numerically larger. A manager scanning
		the board is asking which blocks are getting worse."""
		self.a_threshold(
			threat=BORON,
			threat_category="Nutrient",
			action_threshold=2.2,
			comparison="Less Than",
			sample_unit="Percent Dry Weight",
		)
		common = {
			"threat": BORON,
			"threat_category": "Nutrient",
			"sample_unit": "Percent Dry Weight",
		}
		self.an_observation(count_observed=3.0, observed_on="2026-05-01", **common)
		second = self.an_observation(count_observed=2.4, observed_on="2026-05-08", **common)
		self.assertEqual(second["pest_pressure_detail"]["trend"], "Rising")

	def test_a_rising_insect_count_is_also_a_rising_pressure(self):
		self.a_threshold()
		self.an_observation(count_observed=2, observed_on="2026-05-01")
		second = self.an_observation(count_observed=4, observed_on="2026-05-08")
		self.assertEqual(second["pest_pressure_detail"]["trend"], "Rising")

	def test_a_warning_on_the_wrong_side_is_refused_going_up(self):
		error = self.tool_error(
			"set_pest_action_threshold",
			{
				"crop": CHERRY,
				"threat": MOTH,
				"threat_category": "Insect",
				"action_threshold": 5,
				"warning_threshold": 9,
			},
		)
		self.assertIn("arrive FIRST", error)

	def test_a_warning_on_the_wrong_side_is_refused_coming_down(self):
		error = self.tool_error(
			"set_pest_action_threshold",
			{
				"crop": CHERRY,
				"threat": BORON,
				"threat_category": "Nutrient",
				"action_threshold": 2.2,
				"comparison": "Less Than",
				"warning_threshold": 1.5,
			},
		)
		self.assertIn("has to sit above", error)

	def test_the_warning_number_moves_the_pressure_to_watch_without_generating(self):
		"""A week's notice rather than an alarm on the morning it is too late."""
		self.a_threshold(warning_threshold=3)
		data = self.an_observation(count_observed=4)
		self.assertTrue(data["evaluation"]["warning_exceeded"])
		self.assertFalse(data["evaluation"]["action_exceeded"])
		self.assertEqual(data["pest_pressure_detail"]["status"], "Watch")
		self.assertEqual(self.recommendation_rows(), [])

	def test_a_seventh_threat_category_is_refused(self):
		error = self.tool_error(
			"set_pest_action_threshold",
			{
				"crop": CHERRY,
				"threat": "Gremlins",
				"threat_category": "Mythical",
				"action_threshold": 1,
			},
		)
		self.assertIn("must be one of", error)

	def test_all_six_categories_are_accepted(self):
		for index, category in enumerate(("Insect", "Disease", "Weed", "Vertebrate", "Abiotic", "Nutrient")):
			data = self.a_threshold(threat=f"Threat {index}", threat_category=category)
			self.assertEqual(data["threat_category"], category)


# ── 3. the sample has to be big enough ──────────────────────────────────────
class TheSampleHasToBeBigEnough(CropProtectionTestCase):
	def setUp(self):
		super().setUp()
		self.a_threshold(min_sample_size=25)

	def test_a_short_sample_over_threshold_generates_nothing(self):
		"""Acting on a sample too small to say is how a programme loses a crew's
		confidence."""
		data = self.an_observation(count_observed=9, sample_size=4)
		self.assertTrue(data["evaluation"]["action_exceeded"])
		self.assertTrue(data["evaluation"]["sample_below_minimum"])
		self.assertEqual(self.recommendation_rows(), [])

	def test_the_observation_is_still_recorded_in_full(self):
		"""A scout who saw something worth writing down should never be arguing
		with a form."""
		data = self.an_observation(count_observed=9, sample_size=4)
		self.assertEqual(data["count_observed"], 9.0)
		self.assertEqual(data["sample_size"], 4)

	def test_the_pressure_still_moves(self):
		data = self.an_observation(count_observed=9, sample_size=4)
		self.assertEqual(data["pest_pressure_detail"]["latest_value"], 9.0)

	def test_no_sample_size_at_all_counts_as_below_the_minimum(self):
		data = self.an_observation(count_observed=9, sample_size=None)
		self.assertTrue(data["evaluation"]["sample_below_minimum"])

	def test_a_full_sample_generates_normally(self):
		data = self.an_observation(count_observed=9, sample_size=40)
		self.assertFalse(data["evaluation"]["sample_below_minimum"])
		self.assertEqual(len(self.recommendation_rows()), 1)

	def test_a_negative_count_is_refused(self):
		error = self.tool_error(
			"create_crop_observation",
			{
				"block": BLOCK,
				"threat": MOTH,
				"threat_category": "Insect",
				"count_observed": -3,
			},
		)
		self.assertIn("cannot be negative", error)

	def test_a_count_of_zero_is_a_real_observation(self):
		"""It is how a block is shown to have been walked and found clean."""
		data = self.an_observation(count_observed=0, sample_size=40)
		self.assertEqual(data["count_observed"], 0.0)
		self.assertFalse(data["threshold_exceeded"])

	def test_a_future_dated_observation_is_refused(self):
		error = self.tool_error(
			"create_crop_observation",
			{
				"block": BLOCK,
				"threat": MOTH,
				"threat_category": "Insect",
				"count_observed": 3,
				"observed_on": "2099-01-01",
			},
		)
		self.assertIn("in the future", error)


# ── 4. units ────────────────────────────────────────────────────────────────
class TheUnitsHaveToMatch(CropProtectionTestCase):
	def test_a_mismatched_unit_stops_the_evaluation(self):
		"""'Two per trap' against 'two percent infested' produces a number that
		is arithmetically fine and means nothing."""
		self.a_threshold(sample_unit="Per Trap", action_threshold=5)
		data = self.an_observation(count_observed=40, sample_unit="Percent Infested")
		self.assertFalse(data["evaluation"]["action_exceeded"])
		self.assertIn("not comparable", data["evaluation"]["note"])
		self.assertEqual(self.recommendation_rows(), [])

	def test_a_matching_unit_evaluates(self):
		self.a_threshold(sample_unit="Per Trap", action_threshold=5)
		data = self.an_observation(count_observed=8, sample_unit="Per Trap")
		self.assertTrue(data["evaluation"]["action_exceeded"])

	def test_the_observation_inherits_the_threshold_unit_when_it_states_none(self):
		self.a_threshold(sample_unit="Per Trap", action_threshold=5)
		data = self.an_observation(count_observed=8, sample_unit=None)
		self.assertEqual(data["sample_unit"], "Per Trap")
		self.assertTrue(data["evaluation"]["action_exceeded"])


# ── 5. a missing threshold ──────────────────────────────────────────────────
class TheMissingThresholdIsAGap(CropProtectionTestCase):
	def test_an_observation_with_no_threshold_is_recorded(self):
		"""It is the ordinary state of a first season, and refusing it would mean
		a farm cannot record what it saw until it has written its thresholds —
		which is backwards, because the observations are how you find out what
		the thresholds should be."""
		data = self.an_observation()
		self.assertEqual(data["count_observed"], 8.0)
		self.assertIsNone(data["threshold"])
		self.assertIn("No action threshold on file", data["evaluation_note"])

	def test_the_pressure_is_still_tracked(self):
		data = self.an_observation()
		self.assertEqual(data["pest_pressure_detail"]["latest_value"], 8.0)
		self.assertEqual(data["pest_pressure_detail"]["status"], "Monitoring")

	def test_the_list_names_the_unevaluated_observations(self):
		self.an_observation()
		data = self.tool_data("list_crop_observations", {"company": MAIN})
		self.assertEqual(len(data["observations_with_no_threshold_on_file"]), 1)

	def test_a_threshold_with_no_methods_is_flagged_on_the_list(self):
		self.a_threshold(recommended_methods=None)
		data = self.tool_data("list_pest_action_thresholds", {"company": MAIN})
		self.assertEqual(len(data["thresholds_without_recommended_methods"]), 1)

	def test_a_stage_specific_threshold_beats_the_season_long_fallback(self):
		self.a_threshold(action_threshold=5)
		self.a_threshold(crop_stage="Petal Fall", action_threshold=2)
		data = self.an_observation(count_observed=3, crop_stage="Petal Fall")
		self.assertEqual(data["evaluation"]["threshold_value"], 2.0)
		self.assertTrue(data["evaluation"]["action_exceeded"])

	def test_the_fallback_is_used_where_no_stage_matches(self):
		self.a_threshold(action_threshold=5)
		self.a_threshold(crop_stage="Petal Fall", action_threshold=2)
		data = self.an_observation(count_observed=3, crop_stage="First Cover")
		self.assertEqual(data["evaluation"]["threshold_value"], 5.0)
		self.assertFalse(data["evaluation"]["action_exceeded"])

	def test_matching_is_case_insensitive(self):
		"""They are typed by a person on a phone at the end of a row."""
		self.a_threshold(action_threshold=5)
		data = self.an_observation(threat="codling moth", crop="cherry", count_observed=8)
		self.assertTrue(data["evaluation"]["action_exceeded"])

	def test_revising_a_threshold_retires_the_old_row(self):
		"""Observations already evaluated point at the row that evaluated them,
		and an edit would silently rewrite what June's scouting was measured
		against."""
		first = self.a_threshold(action_threshold=5)
		second = self.a_threshold(action_threshold=3)
		self.assertEqual(second["replaced"], first["name"])
		live = self.tool_data("list_pest_action_thresholds", {"company": MAIN})
		self.assertEqual(live["count"], 1)
		self.assertEqual(live["thresholds"][0]["action_threshold"], 3.0)


# ── 6. pressure and the board ───────────────────────────────────────────────
class ThePressureIsTracked(CropProtectionTestCase):
	def setUp(self):
		super().setUp()
		self.a_threshold(action_threshold=5)

	def test_one_row_per_block_threat_and_season(self):
		self.an_observation(block=BLOCK)
		self.an_observation(block=BLOCK)
		self.an_observation(block=BLOCK_TWO)
		self.assertEqual(len(self.pressure_rows()), 2)

	def test_the_peak_is_not_the_latest(self):
		"""A pest answered in June and quiet in August still peaked in June, and
		next year's programme is planned off the peak."""
		self.an_observation(count_observed=40, observed_on="2026-06-01")
		later = self.an_observation(count_observed=2, observed_on="2026-07-01")
		self.assertEqual(later["pest_pressure_detail"]["peak_value"], 40.0)
		self.assertEqual(later["pest_pressure_detail"]["latest_value"], 2.0)

	def test_crossings_are_counted(self):
		self.an_observation(count_observed=8, observed_on="2026-06-01")
		self.an_observation(count_observed=9, observed_on="2026-06-08")
		latest = self.an_observation(count_observed=1, observed_on="2026-06-15")
		self.assertEqual(latest["pest_pressure_detail"]["threshold_exceeded_count"], 2)

	def test_falling_back_under_threshold_is_controlled_not_monitoring(self):
		"""Something was done, or the pest moved on, and a season review is read
		off the distinction."""
		self.an_observation(count_observed=8, observed_on="2026-06-01")
		later = self.an_observation(count_observed=1, observed_on="2026-06-15")
		self.assertEqual(later["pest_pressure_detail"]["status"], "Controlled")

	def test_a_block_never_over_threshold_stays_monitoring(self):
		data = self.an_observation(count_observed=1)
		self.assertEqual(data["pest_pressure_detail"]["status"], "Monitoring")

	def test_the_board_puts_action_first(self):
		"""Ordered by the status ladder rather than the raw numbers, which are in
		different units across threats and cannot be ranked against each other."""
		self.an_observation(block=BLOCK_TWO, count_observed=1)
		self.an_observation(block=BLOCK, count_observed=9)
		board = self.tool_data("list_pest_pressures", {"company": MAIN})
		self.assertEqual(board["pest_pressures"][0]["status"], "Action")
		self.assertEqual(board["pest_pressures"][0]["block"], BLOCK)
		self.assertEqual(board["needing_action"], [board["pest_pressures"][0]["name"]])

	def test_get_carries_the_scouting_history(self):
		self.an_observation(count_observed=3, observed_on="2026-06-01")
		latest = self.an_observation(count_observed=9, observed_on="2026-06-08")
		data = self.tool_data("get_pest_pressure", {"pest_pressure": latest["pest_pressure_detail"]["name"]})
		self.assertEqual(data["observation_count"], 2)
		self.assertEqual(len(data["observations"]), 2)
		self.assertEqual(len(data["recommendations"]), 1)

	def test_a_repeat_crossing_on_a_rising_trend_is_escalated(self):
		self.an_observation(count_observed=6, observed_on="2026-06-01")
		second = self.an_observation(count_observed=9, observed_on="2026-06-08")
		self.assertEqual(second["ipm_recommendation_detail"]["urgency"], "Elevated")

	def test_an_unknown_pressure_is_refused_by_name(self):
		error = self.tool_error("get_pest_pressure", {"pest_pressure": "NOPE"})
		self.assertIn("no Pest Pressure", error)


# ── 7. the recommendation and its score ─────────────────────────────────────
class TheSustainabilityScore(CropProtectionTestCase):
	def setUp(self):
		super().setUp()
		self.a_threshold(action_threshold=5)

	def test_options_are_ordered_least_chemical_first(self):
		"""The first row is what a person reads, and a list that opened with the
		spray would make the ladder decorative."""
		data = self.an_observation(count_observed=9)
		methods = [row["control_method"] for row in data["ipm_recommendation_detail"]["actions"]]
		self.assertEqual(methods, ["Cultural", "Biological", "Chemical"])

	def test_the_proposed_set_is_scored_before_anything_is_accepted(self):
		"""(1.00 + 0.90 + 0.20) / 3 = 0.70."""
		data = self.an_observation(count_observed=9)
		self.assertEqual(data["ipm_recommendation_detail"]["sustainability_score"], 70.0)

	def test_accepting_the_spray_alone_drops_the_score_to_chemical(self):
		"""The switch that stops the scale rewarding a farm for options it turned
		down: once anything is accepted the score is of what was CHOSEN."""
		data = self.an_observation(count_observed=9)
		name = data["ipm_recommendation_detail"]["name"]
		row = STORE.get_raw("IPM Recommendation", name)
		for action in row["actions"]:
			if action["control_method"] == "Chemical":
				action["status"] = "Accepted"
		scored = self.tool_data("compute_sustainability_score", {"recommendation": name})
		self.assertEqual(scored["score"], 70.0)

		# Re-saving is what re-scores it, the same as any Desk edit would.
		import frappe as _frappe

		doc = _frappe.get_doc("IPM Recommendation", name)
		doc.save(ignore_permissions=True)
		rescored = self.tool_data("compute_sustainability_score", {"recommendation": name})
		self.assertEqual(rescored["score"], 20.0)

	def test_a_rejected_option_is_never_scored(self):
		"""Counting it would let a farm bank credit for every option it turned
		down."""
		data = self.an_observation(count_observed=9)
		name = data["ipm_recommendation_detail"]["name"]
		row = STORE.get_raw("IPM Recommendation", name)
		for action in row["actions"]:
			if action["control_method"] in ("Cultural", "Biological"):
				action["status"] = "Rejected"
		import frappe as _frappe

		doc = _frappe.get_doc("IPM Recommendation", name)
		doc.save(ignore_permissions=True)
		rescored = self.tool_data("compute_sustainability_score", {"recommendation": name})
		self.assertEqual(rescored["score"], 20.0)

	def test_a_rejected_option_is_still_counted_on_the_record(self):
		"""A farm that declined the biological option four times running has a
		pattern worth seeing, and a count that dropped it hides exactly that."""
		data = self.an_observation(count_observed=9)
		self.assertEqual(data["ipm_recommendation_detail"]["biological_actions"], 1)

	def test_a_bare_method_list_is_scored_without_writing_anything(self):
		data = self.tool_data("compute_sustainability_score", {"methods": ["Cultural", "Biological"]})
		self.assertEqual(data["score"], 95.0)
		self.assertEqual(data["grade"], "A")
		self.assertEqual(self.recommendation_rows(), [])

	def test_chemical_is_not_zero(self):
		"""A correctly timed threshold-driven spray IS integrated pest management,
		and scoring it zero would tell a farm its best chemical decision was
		worth the same as its worst."""
		data = self.tool_data("compute_sustainability_score", {"methods": ["Chemical"]})
		self.assertEqual(data["score"], 20.0)

	def test_an_unclassified_action_scores_below_chemical(self):
		"""It cannot be shown to have been anything other than a spray, and a
		scale giving it the benefit of the doubt would reward not filling the
		field in."""
		unclassified = self.tool_data("compute_sustainability_score", {"methods": ["Unclassified"]})
		chemical = self.tool_data("compute_sustainability_score", {"methods": ["Chemical"]})
		self.assertGreater(unclassified["score"], chemical["score"])
		self.assertLess(unclassified["score"], 40)

	def test_an_unrecognised_method_is_scored_as_unclassified_and_named(self):
		data = self.tool_data("compute_sustainability_score", {"methods": ["Wishful Thinking"]})
		self.assertEqual(data["counts"]["Unclassified"], 1)
		self.assertEqual(data["unrecognised_methods_scored_as_unclassified"], ["Wishful Thinking"])

	def test_an_empty_set_scores_zero_rather_than_a_hundred(self):
		"""A recommendation with no options is not a good outcome, and scoring it
		full marks for having nothing chemical in it would be the most obviously
		wrong answer available."""
		data = self.tool_data("compute_sustainability_score", {"methods": []})
		self.assertEqual(data["score"], 0.0)
		self.assertEqual(data["grade"], "F")

	def test_a_portfolio_with_nothing_in_scope_scores_null_not_zero(self):
		"""Zero would read as a bad programme rather than an empty one."""
		data = self.tool_data("compute_sustainability_score", {"company": MAIN})
		self.assertEqual(data["count"], 0)
		self.assertIsNone(data["score"])

	def test_a_portfolio_is_one_vote_per_decision(self):
		self.an_observation(block=BLOCK, count_observed=9)
		self.an_observation(block=BLOCK_TWO, count_observed=9)
		data = self.tool_data("compute_sustainability_score", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["score"], 70.0)
		self.assertIn("one vote per decision", data["method"])

	def test_the_recommendation_carries_its_trigger(self):
		"""The whole chain — what was seen, what it was measured against, what was
		recommended — is readable from one link."""
		data = self.an_observation(count_observed=9)
		name = data["ipm_recommendation_detail"]["name"]
		detail = self.tool_data("get_ipm_recommendation", {"recommendation": name})
		self.assertEqual(detail["triggering_observation"]["name"], data["name"])
		self.assertEqual(detail["observed_value"], 9.0)
		self.assertEqual(detail["threshold_value"], 5.0)

	def test_the_response_says_what_the_score_is_not(self):
		data = self.an_observation(count_observed=9)
		detail = self.tool_data(
			"get_ipm_recommendation", {"recommendation": data["ipm_recommendation_detail"]["name"]}
		)
		self.assertIn("not a certification", detail["scale"]["note"])

	def test_a_threshold_with_no_methods_still_generates_something_actionable(self):
		"""An alarm with nothing to do about it is what this avoids being."""
		self.a_threshold(action_threshold=5, recommended_methods=None)
		data = self.an_observation(count_observed=9)
		actions = data["ipm_recommendation_detail"]["actions"]
		self.assertEqual(len(actions), 1)
		self.assertIn("no recommended methods are set", actions[0]["action"])
