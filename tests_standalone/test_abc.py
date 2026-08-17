# SPDX-License-Identifier: MIT
"""Activity-based costing — the pool, the driver, and the refusal to estimate one.

WHAT v0.83.0 CLAIMS. That this app can say what a block cost per acre, and that
every figure it gives is one somebody can walk back to either the ledger or a
measurement. The claim would be FALSE in four specific ways, and each of them is
a class below.

It would be false if an activity whose driver quantities nobody supplied were
quietly spread across blocks by acreage — because an even spread is
indistinguishable in the output from a measured one, and the report would then be
confidently wrong in exactly the way ABC was adopted to prevent. It would be
false if a per-acre figure were a quotient with no stored numerator or
denominator, because next season nobody could say whether the block got dearer or
simply smaller. It would be false if `field=` narrowed the arithmetic as well as
the rows, because a driver share computed against one block is 100% by
construction. And it would be false if a pool's itemised trail did not add up to
the pool, because the trail is the only reason to believe the figure.

SEVEN CLASSES.

1. `AnActivityIsWhatCostsMoney` — the register, and the two duplicates it
   refuses.
2. `APoolsTrailReachesItsFigure` — ledger against manual, the AND-scope that
   does not double count, and the negative pool that is refused where a zero one
   is kept.
3. `TheEngineWillNotEstimateADriver` — the whole point of the release.
4. `TheArithmeticIsExact` — proportional shares, and the rounding residual that
   is placed rather than dropped.
5. `TheRunIsStoredWhole` — the intermediates, the append, and `dry_run`.
6. `TheReportSaysWhatItDividedBy` — the denominator changes with the grouping,
   and every row says which it used.
7. `TheWaterfallIsTheShape` — accumulation in order, and the unit count it will
   not invent.
"""

import frappe

from erpnext_mcp.tools import abc as abc_tools

from .fixtures import MAIN, MAIN_ABBR, OTHER, V12TestCase, cost_center, supplies
from .harness import STORE

YEAR = "2026"

#: Every switch this suite needs, listed rather than globbed so that turning one
#: off in a test reads as a deliberate change from the shipped posture.
ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_cost_activity",
		"get_cost_activity",
		"list_cost_activities",
		"update_cost_activity",
		"create_activity_cost_pool",
		"list_activity_cost_pools",
		"compute_abc_allocation",
		"get_abc_assignment",
		"get_abc_report",
		"get_phase_waterfall",
	)
}

FIELD_WORK = cost_center("Field Work", MAIN_ABBR)


class ABCTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(**ALL_ON)
		self.seed_blocks()

	# -- fixtures ------------------------------------------------------------
	def seed_blocks(self):
		"""Two productive blocks and one still in its pre-yield years.

		Seeded straight to the store rather than created through `create_field`,
		for the reason `test_sustainable_cf_per_acre.py` gives: these tests are
		about the DENOMINATOR, and going through the tool would drag in the parcel
		acreage rule, which is tested where it belongs.

		100 productive acres in a 60/40 split, so a share and a per-acre figure are
		different numbers and a test cannot pass by confusing them. The replant
		carries no `productive_from_date` and must stay OUT of the denominator —
		counting it would make every per-acre figure on this site 10% too cheap.
		"""
		rows = [
			{
				"name": "Home Block",
				"field_name": "Home Block",
				"parcel": "Mill Creek",
				"owning_entity": MAIN,
				"acreage": 60,
				"productive_from_date": "2025-01-01",
				"productive_through_date": None,
				"pre_yield_end_date": None,
				"condition": "Good",
			},
			{
				"name": "River Block",
				"field_name": "River Block",
				"parcel": "Mill Creek",
				"owning_entity": MAIN,
				"acreage": 40,
				"productive_from_date": "2025-01-01",
				"productive_through_date": None,
				"pre_yield_end_date": None,
				"condition": "Good",
			},
			{
				"name": "Replant",
				"field_name": "Replant",
				"parcel": "Mill Creek",
				"owning_entity": MAIN,
				"acreage": 10,
				"productive_from_date": None,
				"productive_through_date": None,
				"pre_yield_end_date": "2029-01-01",
				"condition": "Good",
			},
		]
		STORE.seed("Field", rows)
		return rows

	def gl(self, account, posting_date, debit=0, credit=0, center=FIELD_WORK, company=MAIN):
		STORE.seed(
			"GL Entry",
			[
				{
					"name": f"gl-abc-{len(STORE.rows('GL Entry'))}",
					"account": account,
					"cost_center": center,
					"posting_date": posting_date,
					"debit": debit,
					"credit": credit,
					"company": company,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "ACC-JV-2026-09999",
				}
			],
		)

	def an_activity(self, **overrides):
		payload = {
			"activity_name": "Dormant spray",
			"company": MAIN,
			"activity_type": "Pest Management",
			"phase": "Growing",
			"cost_driver": "Acres",
		}
		payload.update(overrides)
		return self.tool_data("create_cost_activity", payload)["activity"]

	def a_pool(self, activity, amount=10000, **overrides):
		payload = {"activity": activity, "company": MAIN, "fiscal_year": YEAR, "pool_amount": amount}
		payload.update(overrides)
		return self.tool_data("create_activity_cost_pool", payload)["pool"]

	def allocate(self, **overrides):
		payload = {"company": MAIN, "fiscal_year": YEAR}
		payload.update(overrides)
		return self.tool_data("compute_abc_allocation", payload)

	def line_for(self, data, cost_object, activity_name=None):
		for line in data["lines"]:
			if line["cost_object"] == cost_object and (
				activity_name is None or line["activity_name"] == activity_name
			):
				return line
		raise AssertionError(f"no line for {cost_object!r} in {[l['cost_object'] for l in data['lines']]}")


# ── 1 ───────────────────────────────────────────────────────────────────────
class AnActivityIsWhatCostsMoney(ABCTestCase):
	def test_an_activity_carries_its_driver_and_its_phase(self):
		data = self.an_activity()
		self.assertEqual(data["cost_driver"], "Acres")
		self.assertEqual(data["phase"], "Growing")
		self.assertTrue(data["driver_is_derivable"])

	def test_a_measured_driver_says_it_will_not_be_estimated(self):
		"""The sentence a caller needs BEFORE they run an allocation and find a
		whole pool sitting in `unassigned_amount`."""
		data = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")
		self.assertFalse(data["driver_is_derivable"])
		self.assertIn("MEASUREMENT", data["driver_note"])
		self.assertIn("indistinguishable", data["driver_note"])

	def test_an_activity_with_no_ledger_scope_says_its_pool_can_only_be_manual(self):
		data = self.an_activity()
		self.assertIn("Manual", data["pool_note"])

	def test_a_second_activity_of_the_same_name_is_refused(self):
		"""Every report groups by activity name; two rows with one name split one
		activity's cost across two lines that each look like a whole."""
		self.an_activity()
		message = self.tool_error(
			"create_cost_activity",
			{"activity_name": "Dormant spray", "company": MAIN},
		)
		self.assertIn("already called", message)

	def test_the_same_name_under_another_company_is_allowed(self):
		self.an_activity()
		other = self.an_activity(company=OTHER)
		self.assertEqual(other["company"], OTHER)

	def test_an_account_listed_twice_is_refused(self):
		"""A repeated account doubles that slice of the pool and the total still
		looks plausible, which is what makes it dangerous."""
		self.an_activity(accounts=[supplies(MAIN_ABBR)])
		row = STORE.get_raw("Cost Activity", f"Dormant spray - {MAIN_ABBR}")
		row["accounts"].append(dict(row["accounts"][0]))
		doc = frappe.get_doc("Cost Activity", f"Dormant spray - {MAIN_ABBR}")
		with self.assertRaises(Exception) as caught:
			doc.save()
		self.assertIn("twice", str(caught.exception))

	def test_an_unknown_phase_is_refused_with_what_the_phase_decides(self):
		message = self.tool_error(
			"create_cost_activity",
			{"activity_name": "Mystery", "company": MAIN, "phase": "Winter"},
		)
		self.assertIn("waterfall", message)

	def test_an_unknown_driver_is_refused(self):
		message = self.tool_error(
			"create_cost_activity",
			{"activity_name": "Mystery", "company": MAIN, "cost_driver": "Vibes"},
		)
		self.assertIn("cost_driver must be one of", message)

	def test_the_register_names_the_drivers_nobody_can_derive(self):
		self.an_activity()
		self.an_activity(activity_name="Hand thinning", cost_driver="Hours")
		data = self.tool_data("list_cost_activities", {"company": MAIN})
		self.assertEqual(data["activity_count"], 2)
		self.assertEqual(data["by_phase"]["Growing"], 2)
		self.assertEqual(len(data["drivers_needing_measurement"]), 1)
		self.assertIn("UNALLOCATED", data["note"])

	def test_a_retired_activity_is_out_of_the_register_by_default(self):
		self.an_activity()
		self.tool_data("update_cost_activity", {"activity": "Dormant spray", "company": MAIN, "disabled": True})
		self.assertEqual(self.tool_data("list_cost_activities", {"company": MAIN})["activity_count"], 0)
		self.assertEqual(
			self.tool_data("list_cost_activities", {"company": MAIN, "include_disabled": True})["activity_count"],
			1,
		)

	def test_changing_the_phase_says_stored_runs_are_not_restated(self):
		self.an_activity()
		data = self.tool_data(
			"update_cost_activity", {"activity": "Dormant spray", "company": MAIN, "phase": "Harvest"}
		)
		self.assertIn("goes on saying what it said", data["stored_runs_note"])

	def test_an_update_with_nothing_to_change_is_refused(self):
		self.an_activity()
		message = self.tool_error("update_cost_activity", {"activity": "Dormant spray", "company": MAIN})
		self.assertIn("nothing to update", message)

	def test_get_carries_the_pools_built_for_it(self):
		activity = self.an_activity()["name"]
		self.a_pool(activity)
		data = self.tool_data("get_cost_activity", {"activity": activity})
		self.assertEqual(data["pool_count"], 1)


# ── 2 ───────────────────────────────────────────────────────────────────────
class APoolsTrailReachesItsFigure(ABCTestCase):
	def test_a_ledger_pool_is_itemised_by_account_and_adds_up(self):
		self.gl(supplies(MAIN_ABBR), "2026-03-01", debit=6000)
		self.gl(supplies(MAIN_ABBR), "2026-04-01", debit=4000)
		activity = self.an_activity(cost_center=FIELD_WORK)["name"]
		data = self.tool_data(
			"create_activity_cost_pool", {"activity": activity, "company": MAIN, "fiscal_year": YEAR}
		)
		pool = data["pool"]
		self.assertEqual(pool["amount_source"], "Ledger")
		self.assertEqual(pool["pool_amount"], 10000)
		self.assertEqual(sum(source["amount"] for source in pool["sources"]), 10000)
		self.assertEqual(pool["sources"][0]["entry_count"], 2)
		self.assertIn("walked back to the books", data["amount_source_note"])

	def test_the_window_comes_from_the_fiscal_year_and_bounds_the_read(self):
		"""A cost booked outside the year is not this year's pool."""
		self.gl(supplies(MAIN_ABBR), "2026-06-01", debit=5000)
		self.gl(supplies(MAIN_ABBR), "2025-06-01", debit=99999)
		activity = self.an_activity(cost_center=FIELD_WORK)["name"]
		pool = self.tool_data(
			"create_activity_cost_pool", {"activity": activity, "company": MAIN, "fiscal_year": YEAR}
		)["pool"]
		self.assertEqual(pool["pool_amount"], 5000)
		self.assertEqual(pool["period_start"], "2026-01-01")
		self.assertEqual(pool["period_end"], "2026-12-31")

	def test_the_scope_is_an_and_so_one_entry_is_never_counted_twice(self):
		"""Cost center AND account narrow the same rows; the trail then splits
		THAT set. Totalling each filter independently is how a plausible pool ends
		up with evidence that quietly disagrees with it."""
		self.gl(supplies(MAIN_ABBR), "2026-03-01", debit=7000)
		activity = self.an_activity(cost_center=FIELD_WORK, accounts=[supplies(MAIN_ABBR)])["name"]
		pool = self.tool_data(
			"create_activity_cost_pool", {"activity": activity, "company": MAIN, "fiscal_year": YEAR}
		)["pool"]
		self.assertEqual(pool["pool_amount"], 7000)
		self.assertEqual(len(pool["sources"]), 1)

	def test_a_manual_pool_is_labelled_as_a_different_kind_of_evidence(self):
		activity = self.an_activity()["name"]
		data = self.tool_data(
			"create_activity_cost_pool",
			{"activity": activity, "company": MAIN, "fiscal_year": YEAR, "pool_amount": 12345},
		)
		self.assertEqual(data["pool"]["amount_source"], "Manual")
		self.assertIn("no ledger trail", data["amount_source_note"])

	def test_an_activity_with_no_scope_and_no_amount_is_refused(self):
		activity = self.an_activity()["name"]
		message = self.tool_error(
			"create_activity_cost_pool", {"activity": activity, "company": MAIN, "fiscal_year": YEAR}
		)
		self.assertIn("neither a cost center nor an account", message)
		self.assertIn("Nothing was created", message)

	def test_a_negative_pool_is_refused_because_allocating_it_credits_every_block(self):
		self.gl(supplies(MAIN_ABBR), "2026-03-01", credit=4000)
		activity = self.an_activity(cost_center=FIELD_WORK)["name"]
		message = self.tool_error(
			"create_activity_cost_pool", {"activity": activity, "company": MAIN, "fiscal_year": YEAR}
		)
		self.assertIn("negative", message)
		self.assertIn("credit", message.lower())

	def test_a_zero_pool_is_stored_because_zero_is_an_answer(self):
		"""'This activity cost nothing' and 'nobody has computed this activity'
		are different statements and only one is worth acting on."""
		activity = self.an_activity(cost_center=FIELD_WORK)["name"]
		data = self.tool_data(
			"create_activity_cost_pool", {"activity": activity, "company": MAIN, "fiscal_year": YEAR}
		)
		self.assertEqual(data["pool"]["pool_amount"], 0)
		self.assertIn("different statements", data["zero_note"])

	def test_a_second_pool_for_one_activity_and_year_is_refused(self):
		activity = self.an_activity()["name"]
		self.a_pool(activity)
		message = self.tool_error(
			"create_activity_cost_pool",
			{"activity": activity, "company": MAIN, "fiscal_year": YEAR, "pool_amount": 500},
		)
		self.assertIn("already covers", message)

	def test_a_trail_that_does_not_reach_the_figure_is_refused_at_the_controller(self):
		self.gl(supplies(MAIN_ABBR), "2026-03-01", debit=6000)
		activity = self.an_activity(cost_center=FIELD_WORK)["name"]
		pool = self.tool_data(
			"create_activity_cost_pool", {"activity": activity, "company": MAIN, "fiscal_year": YEAR}
		)["pool"]
		doc = frappe.get_doc("Activity Cost Pool", pool["name"])
		doc.pool_amount = 9000
		with self.assertRaises(Exception) as caught:
			doc.save()
		self.assertIn("add up to", str(caught.exception))

	def test_a_direct_assignment_activity_must_name_its_block(self):
		activity = self.an_activity(activity_name="Replant labour", cost_driver="Direct Assignment")["name"]
		message = self.tool_error(
			"create_activity_cost_pool",
			{"activity": activity, "company": MAIN, "fiscal_year": YEAR, "pool_amount": 800},
		)
		self.assertIn("cost_object", message)

	def test_a_cost_object_that_is_not_a_block_is_refused(self):
		activity = self.an_activity(activity_name="Replant labour", cost_driver="Direct Assignment")["name"]
		message = self.tool_error(
			"create_activity_cost_pool",
			{
				"activity": activity,
				"company": MAIN,
				"fiscal_year": YEAR,
				"pool_amount": 800,
				"cost_object": "Nowhere",
			},
		)
		self.assertIn("no Field named", message)

	def test_the_register_separates_the_typed_money_from_the_read_money(self):
		self.gl(supplies(MAIN_ABBR), "2026-03-01", debit=6000)
		ledger = self.an_activity(cost_center=FIELD_WORK)["name"]
		self.tool_data("create_activity_cost_pool", {"activity": ledger, "company": MAIN, "fiscal_year": YEAR})
		manual = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(manual, amount=4000)

		data = self.tool_data("list_activity_cost_pools", {"company": MAIN, "fiscal_year": YEAR})
		self.assertEqual(data["total_pool_amount"], 10000)
		self.assertEqual(data["ledger_amount"], 6000)
		self.assertEqual(data["manual_amount"], 4000)
		self.assertIn("typed rather than read", data["note"])

	def test_an_unknown_fiscal_year_names_the_ones_that_exist(self):
		activity = self.an_activity()["name"]
		message = self.tool_error(
			"create_activity_cost_pool",
			{"activity": activity, "company": MAIN, "fiscal_year": "1066", "pool_amount": 1},
		)
		self.assertIn("Known fiscal years", message)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheEngineWillNotEstimateADriver(ABCTestCase):
	"""The claim the whole release rests on.

	An even spread is indistinguishable in the output from a measured one. So an
	activity whose driver quantities nobody supplied reaches NO block, its money
	is reported as its own figure, and the report says which measurement would
	fix it.
	"""

	def test_an_acres_driver_is_derived_from_the_productive_blocks(self):
		activity = self.an_activity()["name"]
		self.a_pool(activity, amount=10000)
		data = self.allocate()
		self.assertEqual(data["productive_acres"], 100.0)
		self.assertEqual(self.line_for(data, "Home Block")["assigned_amount"], 6000)
		self.assertEqual(self.line_for(data, "River Block")["assigned_amount"], 4000)
		self.assertIn("derived", self.line_for(data, "Home Block")["driver_source"])

	def test_the_pre_yield_block_is_not_in_the_denominator(self):
		"""Counting it would make every per-acre figure on this site 10% cheap."""
		activity = self.an_activity()["name"]
		self.a_pool(activity, amount=10000)
		data = self.allocate()
		self.assertEqual(data["productive_acres"], 100.0)
		self.assertNotIn("Replant", [line["cost_object"] for line in data["lines"]])

	def test_a_measured_driver_with_no_measurement_reaches_no_block(self):
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(activity, amount=8000)
		data = self.allocate()
		self.assertEqual(data["total_assigned"], 0)
		self.assertEqual(data["unassigned_amount"], 8000)
		self.assertEqual(data["unallocated"][0]["pool_amount"], 8000)
		self.assertIn("MEASUREMENT", data["unallocated"][0]["reason"])
		self.assertIn("indistinguishable", data["unassigned_note"])

	def test_its_money_is_not_quietly_spread_across_the_blocks(self):
		"""The failure this release exists to prevent, asserted directly."""
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(activity, amount=8000)
		data = self.allocate()
		self.assertEqual([line["assigned_amount"] for line in data["lines"]], [0.0])
		self.assertEqual(data["lines"][0]["cost_object_type"], "Company")

	def test_supplied_quantities_allocate_it_and_are_labelled_as_supplied(self):
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(activity, amount=10000)
		data = self.allocate(
			driver_quantities=[
				{"activity": "Hand thinning", "cost_object": "Home Block", "quantity": 90},
				{"activity": "Hand thinning", "cost_object": "River Block", "quantity": 10},
			]
		)
		self.assertEqual(data["unassigned_amount"], 0)
		home = self.line_for(data, "Home Block")
		self.assertEqual(home["assigned_amount"], 9000)
		self.assertEqual(home["driver_quantity"], 90.0)
		self.assertEqual(home["driver_share"], 90.0)
		self.assertIn("supplied by the caller", home["driver_source"])

	def test_a_measured_driver_and_acreage_give_different_per_acre_figures(self):
		"""The reason ABC is worth the trouble: 90% of the thinning on 60% of the
		acres is not the same block cost as an even spread would report."""
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(activity, amount=10000)
		data = self.allocate(
			driver_quantities=[
				{"activity": "Hand thinning", "cost_object": "Home Block", "quantity": 90},
				{"activity": "Hand thinning", "cost_object": "River Block", "quantity": 10},
			]
		)
		self.assertEqual(self.line_for(data, "Home Block")["cost_per_acre"], 150.0)
		self.assertEqual(self.line_for(data, "River Block")["cost_per_acre"], 25.0)

	def test_a_supplied_quantity_overrides_a_derived_acreage_and_says_so(self):
		activity = self.an_activity()["name"]
		self.a_pool(activity, amount=10000)
		data = self.allocate(
			driver_quantities=[{"activity": "Dormant spray", "cost_object": "Home Block", "quantity": 5}]
		)
		self.assertEqual(self.line_for(data, "Home Block")["assigned_amount"], 10000)
		self.assertIn("overriding", self.line_for(data, "Home Block")["driver_source"])

	def test_a_direct_assignment_reaches_its_own_block_in_full(self):
		activity = self.an_activity(activity_name="Replant labour", cost_driver="Direct Assignment")["name"]
		self.a_pool(activity, amount=2500, cost_object="Home Block")
		data = self.allocate()
		self.assertEqual(len(data["lines"]), 1)
		self.assertEqual(self.line_for(data, "Home Block")["assigned_amount"], 2500)
		self.assertIn("direct assignment", self.line_for(data, "Home Block")["driver_source"])

	def test_the_identity_holds_and_is_stated(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		self.a_pool(
			self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"], amount=8000
		)
		data = self.allocate()
		self.assertEqual(data["total_pool_amount"], 18000)
		self.assertEqual(data["total_assigned"] + data["unassigned_amount"], 18000)
		self.assertIn("total_assigned", data["identity"])

	def test_a_negative_supplied_quantity_is_refused(self):
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(activity, amount=1000)
		message = self.tool_error(
			"compute_abc_allocation",
			{
				"company": MAIN,
				"fiscal_year": YEAR,
				"driver_quantities": [
					{"activity": "Hand thinning", "cost_object": "Home Block", "quantity": -5}
				],
			},
		)
		self.assertIn("negative", message)

	def test_a_draft_pool_is_skipped_and_said_to_be_skipped(self):
		"""A draft pool is not a zero pool."""
		self.a_pool(self.an_activity()["name"], amount=10000)
		self.a_pool(
			self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"],
			amount=8000,
			status="Draft",
		)
		data = self.allocate()
		self.assertEqual(data["total_pool_amount"], 10000)
		self.assertEqual(len(data["skipped_draft_pools"]), 1)
		self.assertIn("not a zero pool", data["draft_note"])

	def test_an_allocation_with_no_pools_at_all_is_refused(self):
		self.an_activity()
		message = self.tool_error("compute_abc_allocation", {"company": MAIN, "fiscal_year": YEAR})
		self.assertIn("no Activity Cost Pool", message)

	def test_an_allocation_where_every_pool_is_draft_is_refused_by_name(self):
		self.a_pool(self.an_activity()["name"], amount=10000, status="Draft")
		message = self.tool_error("compute_abc_allocation", {"company": MAIN, "fiscal_year": YEAR})
		self.assertIn("Draft", message)
		self.assertIn("not a zero pool", message)


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheArithmeticIsExact(ABCTestCase):
	def test_the_rounding_residual_is_placed_so_the_lines_reach_the_pool(self):
		"""100.00 across three equal consumers is 33.33 three times, which is
		99.99. A run whose lines do not add up to its pools is a run whose totals
		disagree with themselves."""
		STORE.seed(
			"Field",
			[
				{
					"name": "Third Block",
					"field_name": "Third Block",
					"parcel": "Mill Creek",
					"owning_entity": MAIN,
					"acreage": 10,
					"productive_from_date": "2025-01-01",
					"condition": "Good",
				}
			],
		)
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(activity, amount=100)
		data = self.allocate(
			driver_quantities=[
				{"activity": "Hand thinning", "cost_object": block, "quantity": 1}
				for block in ("Home Block", "River Block", "Third Block")
			]
		)
		amounts = [line["assigned_amount"] for line in data["lines"]]
		self.assertEqual(round(sum(amounts), 2), 100.00)
		self.assertEqual(sorted(amounts), [33.33, 33.33, 33.34])
		self.assertEqual(data["rounding_residual_placed"], 0.01)
		self.assertEqual(data["unassigned_amount"], 0)

	def test_shares_within_one_activity_sum_to_one_hundred(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		data = self.allocate()
		self.assertEqual(round(sum(line["driver_share"] for line in data["lines"]), 4), 100.0)

	def test_cost_per_acre_is_the_assignment_over_that_blocks_own_acres(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		data = self.allocate()
		home = self.line_for(data, "Home Block")
		self.assertEqual(home["productive_acres"], 60.0)
		self.assertEqual(home["cost_per_acre"], round(home["assigned_amount"] / 60.0, 4))

	def test_no_productive_acres_gives_null_rather_than_zero(self):
		"""Zero is a per-acre figure for a division nobody performed."""
		for row in STORE.rows("Field"):
			row["productive_from_date"] = None
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"]
		self.a_pool(activity, amount=1000)
		data = self.allocate(
			driver_quantities=[{"activity": "Hand thinning", "cost_object": "Home Block", "quantity": 3}]
		)
		self.assertIsNone(data["cost_per_acre"])
		self.assertIsNone(self.line_for(data, "Home Block")["cost_per_acre"])
		self.assertIn("null rather than zero", data["acreage_note"])

	def test_an_acres_activity_with_nothing_productive_reaches_no_block(self):
		for row in STORE.rows("Field"):
			row["productive_from_date"] = None
		self.a_pool(self.an_activity()["name"], amount=10000)
		data = self.allocate()
		self.assertEqual(data["unassigned_amount"], 10000)
		self.assertIn("pre-yield", data["unallocated"][0]["reason"])

	def test_time_weighting_halves_a_block_that_came_into_bearing_mid_year(self):
		STORE.get_raw("Field", "River Block")["productive_from_date"] = "2026-07-02"
		self.a_pool(self.an_activity()["name"], amount=10000)
		data = self.allocate()
		# 40 acres for 183 of 365 days ≈ 20.05 weighted acres against Home's 60.
		river = self.line_for(data, "River Block")
		self.assertLess(river["driver_quantity"], 21)
		self.assertGreater(river["driver_quantity"], 19)
		self.assertGreater(self.line_for(data, "Home Block")["assigned_amount"], river["assigned_amount"])


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheRunIsStoredWhole(ABCTestCase):
	def test_a_dry_run_computes_everything_and_writes_nothing(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		data = self.allocate(dry_run=True)
		self.assertTrue(data["dry_run"])
		self.assertIsNone(data["assignment"])
		self.assertEqual(data["total_assigned"], 10000)
		self.assertEqual(STORE.rows("ABC Cost Assignment"), [])

	def test_a_run_stores_every_intermediate_on_every_line(self):
		"""A per-acre cost is a quotient of two numbers that both moved during the
		year. Keeping only the quotient is how nobody can say afterwards whether
		the block got dearer or simply smaller."""
		self.a_pool(self.an_activity()["name"], amount=10000)
		self.allocate()
		data = self.tool_data("get_abc_assignment", {"company": MAIN, "fiscal_year": YEAR})
		line = next(row for row in data["lines"] if row["cost_object"] == "Home Block")
		for key in ("driver_quantity", "driver_share", "pool_amount", "assigned_amount", "productive_acres"):
			self.assertIsNotNone(line[key], key)
		self.assertEqual(line["phase"], "Growing")
		self.assertEqual(line["cost_driver"], "Acres")
		self.assertEqual(data["total_pool_amount"], 10000)

	def test_a_rerun_appends_rather_than_replacing(self):
		"""The history of what this operation believed its costs were is itself a
		record."""
		self.a_pool(self.an_activity()["name"], amount=10000)
		first = self.allocate()["assignment"]
		second = self.allocate()["assignment"]
		self.assertNotEqual(first, second)
		self.assertEqual(len(STORE.rows("ABC Cost Assignment")), 2)

	def test_the_newest_run_is_the_one_a_read_gets_and_an_older_one_is_reachable(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		first = self.allocate()["assignment"]
		second = self.allocate()["assignment"]
		self.assertEqual(
			self.tool_data("get_abc_assignment", {"company": MAIN, "fiscal_year": YEAR})["name"], second
		)
		self.assertEqual(
			self.tool_data("get_abc_assignment", {"company": MAIN, "assignment": first})["name"], first
		)

	def test_a_consumed_pool_is_marked_allocated_and_stays_re_allocatable(self):
		"""The flag records that a run happened. Locking the pool would freeze an
		error in place."""
		pool = self.a_pool(self.an_activity()["name"], amount=10000)
		self.allocate()
		self.assertEqual(STORE.get_raw("Activity Cost Pool", pool["name"])["status"], "Allocated")
		self.assertEqual(self.allocate()["total_pool_amount"], 10000)

	def test_an_unallocated_activity_is_kept_on_the_run_rather_than_only_in_prose(self):
		activity = self.an_activity(activity_name="Hand thinning", cost_driver="Hours", phase="Harvest")["name"]
		self.a_pool(activity, amount=8000)
		self.allocate()
		data = self.tool_data("get_abc_assignment", {"company": MAIN, "fiscal_year": YEAR})
		self.assertEqual(data["lines"], [])
		self.assertEqual(data["unallocated"][0]["phase"], "Harvest")
		self.assertEqual(data["unallocated"][0]["pool_amount"], 8000)

	def test_reading_a_year_with_no_run_says_which_tool_produces_one(self):
		message = self.tool_error("get_abc_report", {"company": MAIN, "fiscal_year": YEAR})
		self.assertIn("compute_abc_allocation", message)
		self.assertIn("never computes", message)


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheReportSaysWhatItDividedBy(ABCTestCase):
	def a_run(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		self.a_pool(
			self.an_activity(activity_name="Pick and haul", cost_driver="Bins", phase="Harvest")["name"],
			amount=20000,
		)
		return self.allocate(
			driver_quantities=[
				{"activity": "Pick and haul", "cost_object": "Home Block", "quantity": 800},
				{"activity": "Pick and haul", "cost_object": "River Block", "quantity": 200},
			]
		)

	def test_grouped_by_field_each_block_is_divided_by_its_own_acres(self):
		self.a_run()
		data = self.tool_data("get_abc_report", {"company": MAIN, "fiscal_year": YEAR, "group_by": "field"})
		home = next(row for row in data["groups"] if row["key"] == "Home Block")
		self.assertEqual(home["acres"], 60.0)
		self.assertIn("this block's own", home["acres_basis"])
		# 6,000 of the spray plus 16,000 of the picking, over 60 acres.
		self.assertEqual(home["assigned_amount"], 22000)
		self.assertEqual(home["cost_per_acre"], round(22000 / 60, 4))

	def test_a_blocks_acreage_is_not_counted_once_per_activity(self):
		"""Summing it per line would divide by the acreage several times over and
		make a heavily worked block look cheap."""
		self.a_run()
		data = self.tool_data("get_abc_report", {"company": MAIN, "fiscal_year": YEAR, "group_by": "field"})
		home = next(row for row in data["groups"] if row["key"] == "Home Block")
		self.assertEqual(home["line_count"], 2)
		self.assertEqual(home["acres"], 60.0)

	def test_grouped_by_activity_the_denominator_is_the_whole_operation(self):
		self.a_run()
		data = self.tool_data("get_abc_report", {"company": MAIN, "fiscal_year": YEAR, "group_by": "activity"})
		spray = next(row for row in data["groups"] if row["label"] == "Dormant spray")
		self.assertEqual(spray["acres"], 100.0)
		self.assertIn("whole operation", spray["acres_basis"])
		self.assertEqual(spray["cost_per_acre"], 100.0)

	def test_the_two_denominators_are_named_so_nobody_assumes_the_wrong_one(self):
		self.a_run()
		data = self.tool_data("get_abc_report", {"company": MAIN, "fiscal_year": YEAR})
		self.assertIn("ratio of one block to the farm", data["denominator_note"])

	def test_grouped_by_phase_every_phase_appears_even_the_empty_ones(self):
		"""An unmapped phase and a free one look identical in a total and are not
		the same finding."""
		self.a_run()
		data = self.tool_data("get_abc_report", {"company": MAIN, "fiscal_year": YEAR, "group_by": "phase"})
		self.assertEqual([row["key"] for row in data["groups"]], list(abc_tools.PHASES))
		packing = next(row for row in data["groups"] if row["key"] == "Packing")
		self.assertEqual(packing["assigned_amount"], 0.0)
		self.assertIn("different from this", packing["note"])

	def test_an_unknown_group_by_is_refused(self):
		self.a_run()
		message = self.tool_error(
			"get_abc_report", {"company": MAIN, "fiscal_year": YEAR, "group_by": "supplier"}
		)
		self.assertIn("group_by must be one of", message)

	def test_the_report_says_how_much_reached_no_block_at_all(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		self.a_pool(
			self.an_activity(activity_name="Hand thinning", cost_driver="Hours")["name"], amount=5000
		)
		self.allocate()
		data = self.tool_data("get_abc_report", {"company": MAIN, "fiscal_year": YEAR})
		self.assertEqual(data["unassigned_amount"], 5000)
		self.assertIn("understated", data["unassigned_note"])

	def test_the_report_names_the_run_it_read(self):
		run = self.a_run()["assignment"]
		data = self.tool_data("get_abc_report", {"company": MAIN, "fiscal_year": YEAR})
		self.assertEqual(data["assignment"], run)


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheWaterfallIsTheShape(ABCTestCase):
	def a_pipeline(self):
		for name, phase, amount in (
			("Dormant spray", "Growing", 30000),
			("Pick and haul", "Harvest", 20000),
			("Cold storage", "Post-Harvest", 10000),
			("Pack line", "Packing", 15000),
		):
			activity = self.an_activity(activity_name=name, phase=phase, cost_driver="Acres")["name"]
			self.a_pool(activity, amount=amount)
		return self.allocate()

	def test_cost_accumulates_in_order(self):
		self.a_pipeline()
		data = self.tool_data("get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR})
		stages = {row["phase"]: row for row in data["phases"]}
		self.assertEqual([row["phase"] for row in data["phases"]], list(abc_tools.PHASES))
		self.assertEqual(stages["Growing"]["cumulative_cost"], 30000)
		self.assertEqual(stages["Harvest"]["cumulative_cost"], 50000)
		self.assertEqual(stages["Post-Harvest"]["cumulative_cost"], 60000)
		self.assertEqual(stages["Packing"]["cumulative_cost"], 75000)
		self.assertEqual(stages["Sales"]["cumulative_cost"], 75000)
		self.assertEqual(data["total_cost"], 75000)

	def test_cumulative_per_acre_is_reported_at_every_stage(self):
		self.a_pipeline()
		data = self.tool_data("get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR})
		stages = {row["phase"]: row for row in data["phases"]}
		self.assertEqual(stages["Growing"]["cumulative_per_acre"], 300.0)
		self.assertEqual(stages["Packing"]["cumulative_per_acre"], 750.0)

	def test_it_will_not_invent_a_unit_count(self):
		self.a_pipeline()
		data = self.tool_data("get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR})
		self.assertIsNone(data["total_per_unit"])
		self.assertIsNone(data["phases"][0]["cumulative_per_unit"])
		self.assertIn("WILL NOT PICK A DENOMINATOR", data["unit_note"])

	def test_units_turn_the_waterfall_into_cost_per_bin(self):
		self.a_pipeline()
		data = self.tool_data(
			"get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR, "units": 1500, "uom": "bin"}
		)
		stages = {row["phase"]: row for row in data["phases"]}
		self.assertEqual(stages["Growing"]["cumulative_per_unit"], 20.0)
		self.assertEqual(stages["Packing"]["cumulative_per_unit"], 50.0)
		self.assertEqual(data["total_per_unit"], 50.0)

	def test_a_unit_count_of_zero_is_refused_rather_than_dividing(self):
		self.a_pipeline()
		message = self.tool_error(
			"get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR, "units": 0}
		)
		self.assertIn("greater than zero", message)

	def test_a_phase_nothing_is_mapped_to_is_reported_at_zero_with_a_note(self):
		self.a_pipeline()
		data = self.tool_data("get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR})
		sales = next(row for row in data["phases"] if row["phase"] == "Sales")
		self.assertEqual(sales["cost_added"], 0.0)
		self.assertIn("different from this phase costing nothing", sales["note"])

	def test_unallocated_money_is_broken_out_by_the_phase_it_belongs_to(self):
		"""So a reader can see WHICH stage is under-measured, not only that
		something is."""
		self.a_pipeline()
		thinning = self.an_activity(activity_name="Hand thinning", cost_driver="Hours", phase="Growing")["name"]
		self.a_pool(thinning, amount=9000)
		self.allocate()
		data = self.tool_data("get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR})
		growing = next(row for row in data["phases"] if row["phase"] == "Growing")
		self.assertEqual(growing["unallocated_amount"], 9000)
		self.assertIn("understated", growing["unallocated_note"])
		self.assertEqual(data["unallocated_by_phase"]["Growing"], 9000)

	def test_one_blocks_own_waterfall_is_that_blocks_share(self):
		self.a_pipeline()
		data = self.tool_data(
			"get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR, "field": "Home Block"}
		)
		self.assertEqual(data["total_cost"], 45000)
		self.assertEqual(data["productive_acres"], 60.0)
		self.assertEqual(data["total_per_acre"], 750.0)
		self.assertIn("it does not re-run it", data["field_note"])

	def test_the_read_says_the_shape_is_the_answer(self):
		self.a_pipeline()
		data = self.tool_data("get_phase_waterfall", {"company": MAIN, "fiscal_year": YEAR})
		self.assertIn("The total is available from any ledger", data["reading_it"])


# ── the filter ──────────────────────────────────────────────────────────────
class TheFilterNarrowsRowsAndNeverTheArithmetic(ABCTestCase):
	"""A driver share computed against one block is 100% by construction."""

	def test_filtering_to_one_block_leaves_its_share_exactly_as_it_was(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		whole = self.allocate()
		narrowed = self.allocate(field="Home Block")
		self.assertEqual(
			self.line_for(whole, "Home Block")["driver_share"],
			self.line_for(narrowed, "Home Block")["driver_share"],
		)
		self.assertEqual(self.line_for(narrowed, "Home Block")["assigned_amount"], 6000)
		self.assertEqual(len(narrowed["lines"]), 1)
		self.assertIn("100% by construction", narrowed["filter_note"])

	def test_the_stored_run_holds_every_line_even_when_the_answer_showed_one(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		narrowed = self.allocate(field="Home Block")
		stored = self.tool_data("get_abc_assignment", {"company": MAIN, "assignment": narrowed["assignment"]})
		self.assertEqual(len(stored["lines"]), 2)

	def test_an_unknown_block_is_refused_rather_than_silently_matching_nothing(self):
		self.a_pool(self.an_activity()["name"], amount=10000)
		message = self.tool_error(
			"compute_abc_allocation", {"company": MAIN, "fiscal_year": YEAR, "field": "Nowhere"}
		)
		self.assertIn("no Field named", message)


# ── the switches ────────────────────────────────────────────────────────────
class TheSwitchesShipTheWayTheyShould(ABCTestCase):
	def test_the_four_writes_are_off_out_of_the_box(self):
		self.configure()
		for name in (
			"create_cost_activity",
			"update_cost_activity",
			"create_activity_cost_pool",
			"compute_abc_allocation",
		):
			with self.subTest(tool=name):
				message = self.tool_error(name, {"company": MAIN})
				self.assertIn(f"allow_{name}", message)

	def test_the_six_reads_are_on_out_of_the_box(self):
		self.configure()
		data = self.tool_data("list_cost_activities", {"company": MAIN})
		self.assertEqual(data["activity_count"], 0)
