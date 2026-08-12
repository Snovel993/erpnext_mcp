# SPDX-License-Identifier: MIT
"""The wage floor, configurable and visible before anybody posts. v0.63.0.

`payroll_calc` has PAID the floor since v0.49.0 — gross is the greater of what
the work earned and what the hours were owed — and two things about it were out
of reach:

  * `calculate_full_payroll` has declared `min_wage_rates` since that release and
    NOTHING EVER SUPPLIED ONE, so every run on every install used the table
    compiled into the module and an Oregon rate change was a release.
  * It has declared `min_wage_regions` for just as long and nothing ever supplied
    one either, so Oregon's Portland metro rate — the HIGHEST of the three, and
    the one an orchard inside the urban growth boundary is on — was unreachable
    from any tool in this app.

And a third thing was computed and hard to see: `calculate_payroll` stored the
topped-up gross without the two columns that say it had been topped up, and
answered with totals that never mentioned the floor.

FIVE CLAIMS.

1. `TheRateComesOffTheSite` — a State Tax Configuration's own minimum wage wins,
   a zero does not, and a site that has configured nothing pays exactly what it
   paid before this release.

2. `TheRegionIsReachable` — a salary structure names one of the three Oregon
   rates and the floor moves with it, including across a state that has no such
   region.

3. `ThePreviewSaysIt` — `preview_payroll` carries the block a person reads before
   posting, and says it on the summary line rather than only in a nested key.

4. `TheEntrySaysIt` — `calculate_payroll` writes `earned_gross` and
   `minimum_wage_makeup` onto the slips it stores, and reports the run's floor
   picture on the draft somebody is about to submit.

5. `TheCrossCheckReadsTheSameTable` — the independent per-state check runs
   against the configured rates rather than the shipped ones. A cross-check
   reading a different table from the thing it checks is not a cross-check.
"""

from erpnext_mcp.payroll_calc import (
	MINIMUM_WAGE_RATES,
	normalise_min_wage_region,
	state_min_wage_rates,
)
from erpnext_mcp.tools import payroll as payroll_tools

from .fixtures import MAIN
from .harness import STORE
from .test_payroll_integration import (
	PERIOD_END,
	PERIOD_START,
	PICKER,
	WORKER,
	IntegrationTestCase,
	member,
	shift,
)

#: An Oregon floor this site sets for itself, well clear of the shipped $14.70 so
#: a test cannot pass by accident on the default.
OR_LOCAL = 18.00


class MinimumWageTestCase(IntegrationTestCase):
	def set_min_wage(self, state="OR", **rates):
		"""Put this site's own floor on the State Tax Configuration for `state`."""
		for row in STORE.rows("State Tax Configuration"):
			if row.get("state") == state:
				row.update(rates)
				return row
		raise AssertionError(f"no State Tax Configuration seeded for {state}")

	def structure(self, employee, pay_type="Hourly", rate=20.0, company=MAIN, **extra):
		return self.tool_data(
			"create_salary_structure",
			{
				"employee": employee,
				"company": company,
				"pay_type": pay_type,
				"base_rate": rate,
				"effective_from": "2025-01-01",
				**extra,
			},
		)

	def one_short_day(self, employee=WORKER, state="OR"):
		"""Eight hours that earn almost nothing, so the floor is what pays them."""
		self.seed_shifts(
			shift("S1", "2025-06-02", start="06:00:00", end="14:00:00", state=state,
			      crew=[member(employee)]),
		)

	def summary_of(self, tool, args):
		"""The one-line summary a tool hands back, called straight rather than
		through the dispatcher.

		The summary is the tool's own sentence and lands in MCP Action Log; the
		transport does not carry it in `content`. Every gate between here and there
		is asserted in `test_protocol` and `test_settings`, so calling the handler
		is the shortest honest way to check WHAT IT SAYS.
		"""
		return getattr(payroll_tools, tool)(args).summary

	def preview_one(self, employee=WORKER, **extra):
		return self.tool_data(
			"preview_payroll",
			{
				"employee": employee,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"pay_frequency": "Biweekly",
				"company": MAIN,
				**extra,
			},
		)


# ── Claim 1 ─────────────────────────────────────────────────────────────────
class TheRateComesOffTheSite(MinimumWageTestCase):
	def test_a_configured_rate_wins_over_the_shipped_one(self):
		configs = {"OR": {"minimum_wage": OR_LOCAL}}
		rates = state_min_wage_rates(configs)
		self.assertEqual(rates["OR"]["standard"], OR_LOCAL)
		# The regions it did NOT set keep the shipped values rather than vanishing.
		self.assertEqual(rates["OR"]["portland_metro"], 15.95)
		self.assertEqual(rates["WA"]["standard"], 16.66)

	def test_a_zero_is_not_set_here_rather_than_a_floor_of_zero(self):
		"""Currency fields default to 0 on every row of every doctype. Treating one
		as an override would let a site that filled in nothing but its SUTA rate
		drop the floor to nothing for its whole payroll — and the makeup that
		catches an underpaid piece rate would compute against it and find nothing
		wrong."""
		rates = state_min_wage_rates({"OR": {"minimum_wage": 0, "minimum_wage_non_urban": 0.0}})
		self.assertEqual(rates["OR"], MINIMUM_WAGE_RATES["OR"])

	def test_the_shipped_table_is_not_mutated(self):
		before = dict(MINIMUM_WAGE_RATES["OR"])
		state_min_wage_rates({"OR": {"minimum_wage": OR_LOCAL}})
		self.assertEqual(MINIMUM_WAGE_RATES["OR"], before)

	def test_prose_in_the_column_does_not_take_the_floor_down(self):
		rates = state_min_wage_rates({"OR": {"minimum_wage": "not a number"}})
		self.assertEqual(rates["OR"]["standard"], MINIMUM_WAGE_RATES["OR"]["standard"])

	def test_a_site_that_configures_nothing_pays_what_it_paid_before(self):
		"""The columns are new and empty on every site upgrading to v0.63.0. The
		release must not change what a single slip pays until somebody types a
		number into one."""
		self.structure(WORKER, rate=20.0)
		self.one_short_day()
		before = self.preview_one()
		self.assertEqual(before["minimum_wage"]["rates"]["OR"]["standard"], 14.70)
		self.assertEqual(before["minimum_wage"]["configured_states"], [])

	def test_the_configured_rate_reaches_a_real_preview(self):
		"""The end of the wire: a row in the Desk changes what a slip pays."""
		self.structure(WORKER, rate=15.0)
		self.one_short_day()
		self.assertEqual(self.preview_one()["minimum_wage"]["makeup"], 0.0)

		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		after = self.preview_one()
		self.assertEqual(after["minimum_wage"]["rates"]["OR"]["standard"], OR_LOCAL)
		self.assertEqual(after["minimum_wage"]["configured_states"], ["OR"])
		# Eight hours at $15 earns $120; eight at $18 is owed $144.
		self.assertAlmostEqual(after["minimum_wage"]["makeup"], 24.00, places=2)
		self.assertAlmostEqual(after["gross_pay"], 144.00, places=2)

	def test_a_topped_up_slip_is_compliant_and_still_says_the_rate_is_wrong(self):
		"""Two different facts. Conflating them would either report every
		underpriced bucket as a violation or hide it entirely."""
		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		self.structure(WORKER, rate=15.0)
		self.one_short_day()
		block = self.preview_one()["minimum_wage"]
		self.assertTrue(block["compliant"])
		self.assertGreater(block["makeup"], 0)
		self.assertIn("THE RATE IS THE PROBLEM", block["note"])


# ── Claim 2 ─────────────────────────────────────────────────────────────────
class TheRegionIsReachable(MinimumWageTestCase):
	def test_the_select_maps_onto_the_rate_table_key(self):
		self.assertEqual(normalise_min_wage_region("Portland Metro"), "portland_metro")
		self.assertEqual(normalise_min_wage_region("Non-Urban"), "non_urban")
		self.assertEqual(normalise_min_wage_region("Standard"), "standard")

	def test_an_unreadable_region_falls_back_rather_than_raising(self):
		"""The caller is a payroll run and the value came off somebody's structure.
		A region this app does not recognise is worth one lawful-but-wrong floor —
		the standard rate, which everybody in the state is owed at minimum — and is
		not worth refusing to pay a whole company over."""
		self.assertEqual(normalise_min_wage_region("Bend"), "standard")
		self.assertEqual(normalise_min_wage_region(None), "standard")

	def test_creating_a_structure_records_the_region(self):
		created = self.structure(WORKER, min_wage_region="Portland Metro")
		self.assertEqual(created["min_wage_region"], "Portland Metro")

	def test_a_region_nobody_defines_is_refused_at_the_keyboard(self):
		"""Unlike the payroll path — where an unreadable value must not hold up a
		company's pay — this is somebody typing it, once, in front of the answer."""
		error = self.tool_error(
			"create_salary_structure",
			{
				"employee": WORKER,
				"company": MAIN,
				"pay_type": "Hourly",
				"base_rate": 20.0,
				"effective_from": "2025-01-01",
				"min_wage_region": "Hood River",
			},
		)
		self.assertIn("Standard, Non-Urban or Portland Metro", error)

	def test_the_portland_metro_floor_is_the_one_that_applies(self):
		"""$15.95 against the standard $14.70 — the rate this app has shipped since
		v0.30.0 and could not reach."""
		self.structure(WORKER, rate=15.00, min_wage_region="Portland Metro")
		self.one_short_day()
		block = self.preview_one()["minimum_wage"]
		self.assertEqual(block["region"], "portland_metro")
		# Eight hours at $15 earns $120; eight at $15.95 is owed $127.60.
		self.assertAlmostEqual(block["makeup"], 7.60, places=2)

	def test_the_same_worker_on_standard_needs_no_makeup(self):
		self.structure(WORKER, rate=15.00)
		self.one_short_day()
		self.assertEqual(self.preview_one()["minimum_wage"]["makeup"], 0.0)

	def test_a_region_the_state_does_not_define_falls_back_to_its_standard(self):
		"""Washington sets one rate. A Portland-metro worker who spent a week over
		the river is owed Washington's $16.66, not nothing and not Oregon's."""
		self.structure(PICKER, rate=15.00, min_wage_region="Portland Metro")
		self.one_short_day(PICKER, state="WA")
		block = self.preview_one(PICKER)["minimum_wage"]
		# Eight hours at $15 earns $120; eight at $16.66 is owed $133.28.
		self.assertAlmostEqual(block["makeup"], 13.28, places=2)
		self.assertEqual(block["by_state"]["WA"]["minimum_wage"], 16.66)


# ── Claim 3 ─────────────────────────────────────────────────────────────────
class ThePreviewSaysIt(MinimumWageTestCase):
	def test_the_summary_line_names_the_makeup(self):
		"""The whole reason a preview exists is to be READ before anybody posts,
		and the figure has been in a nested key nobody opens since v0.49.0."""
		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		self.structure(WORKER, rate=15.0)
		self.one_short_day()
		summary = self.summary_of(
			"preview_payroll",
			{
				"employee": WORKER,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"company": MAIN,
			},
		)
		self.assertIn("minimum wage makeup", summary)
		self.assertIn("did not clear the floor", summary)

	def test_a_compliant_slip_gets_no_sentence_and_no_summary_clause(self):
		"""A note on every compliant line of every run is noise."""
		self.structure(WORKER, rate=25.0)
		self.one_short_day()
		data = self.preview_one()
		self.assertEqual(data["minimum_wage"]["note"], "")
		self.assertEqual(data["minimum_wage"]["makeup"], 0.0)

	def test_a_salary_structure_is_not_topped_up_and_says_so(self):
		"""Whether a salaried employee is exempt is a fact about their job this app
		does not hold. Raising an exempt supervisor's pay because a sixty-hour
		harvest week divided their salary below the floor would be inventing an
		obligation."""
		self.structure(WORKER, pay_type="Salary", rate=100.0)
		self.one_short_day()
		block = self.preview_one()["minimum_wage"]
		self.assertFalse(block["applies"])
		self.assertEqual(block["makeup"], 0.0)
		self.assertIn("not topped up here", block["note"])


# ── Claim 4 ─────────────────────────────────────────────────────────────────
class TheEntrySaysIt(MinimumWageTestCase):
	def calculate(self):
		return self.tool_data(
			"calculate_payroll",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"pay_frequency": "Biweekly",
			},
		)

	def test_the_stored_slip_carries_both_halves_of_gross(self):
		"""The two columns the slip doctype has had since v0.49.0 and this path
		never wrote. A stored row that says only the topped-up total cannot answer
		'how much of this was makeup'."""
		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		self.structure(WORKER, rate=15.0)
		self.one_short_day()
		entry = self.calculate()
		row = next(
			slip
			for slip in self.tool_data("get_payroll_entry", {"name": entry["name"]})["slips"]
			if slip["employee"] == WORKER
		)
		self.assertAlmostEqual(row["minimum_wage_makeup"], 24.00, places=2)
		self.assertAlmostEqual(row["earned_gross"], 120.00, places=2)
		self.assertAlmostEqual(row["gross_pay"], 144.00, places=2)

	def test_the_draft_reports_the_runs_floor_picture(self):
		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		self.structure(WORKER, rate=15.0)
		self.one_short_day()
		block = self.calculate()["minimum_wage"]
		self.assertAlmostEqual(block["total_makeup"], 24.00, places=2)
		self.assertEqual([row["employee"] for row in block["topped_up"]], [WORKER])
		self.assertEqual(block["below_floor"], [])
		self.assertIn("PAID LAWFULLY", block["note"])

	def test_the_summary_line_names_it_before_anybody_submits(self):
		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		self.structure(WORKER, rate=15.0)
		self.one_short_day()
		summary = self.summary_of(
			"calculate_payroll",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
			},
		)
		self.assertIn("minimum wage makeup", summary)

	def test_a_clean_run_says_nothing_about_the_floor(self):
		self.structure(WORKER, rate=25.0)
		self.one_short_day()
		block = self.calculate()["minimum_wage"]
		self.assertEqual(block["total_makeup"], 0.0)
		self.assertEqual(block["topped_up"], [])
		self.assertEqual(block["note"], "")

	def test_the_region_travels_into_the_whole_company_run(self):
		self.structure(WORKER, rate=15.00, min_wage_region="Portland Metro")
		self.one_short_day()
		block = self.calculate()["minimum_wage"]
		self.assertEqual(block["topped_up"][0]["region"], "portland_metro")
		self.assertAlmostEqual(block["total_makeup"], 7.60, places=2)


# ── Claim 5 ─────────────────────────────────────────────────────────────────
class TheCrossCheckReadsTheSameTable(MinimumWageTestCase):
	def period_preview(self):
		return self.tool_data(
			"preview_payroll_for_period",
			{
				"company": MAIN,
				"pay_period_start": PERIOD_START,
				"pay_period_end": PERIOD_END,
				"verbose": 1,
			},
		)

	def test_the_independent_check_uses_the_configured_floor(self):
		"""Without this the cross-check would test against the SHIPPED rates while
		the engine paid the site's own — so a farm that raised its floor would see
		every topped-up slip flagged as below the minimum."""
		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		self.structure(WORKER, rate=15.0)
		self.one_short_day()
		slip = next(
			row for row in self.period_preview()["slips"] if row["employee"] == WORKER
		)
		detail = slip["minimum_wage_detail"]
		self.assertTrue(detail["meets_minimum_wage"], detail)
		self.assertEqual(detail["by_state"]["OR"]["minimum_wage"], OR_LOCAL)

	def test_the_period_run_reports_where_the_floor_came_from(self):
		self.set_min_wage("OR", minimum_wage=OR_LOCAL)
		self.structure(WORKER, rate=20.0)
		self.one_short_day()
		data = self.period_preview()
		self.assertEqual(data["minimum_wage_rates"]["OR"]["standard"], OR_LOCAL)
		self.assertEqual(data["minimum_wage_states_configured"], ["OR"])

	def test_the_region_reaches_the_period_run_too(self):
		self.structure(WORKER, rate=15.00, min_wage_region="Portland Metro")
		self.one_short_day()
		slip = next(
			row for row in self.period_preview()["slips"] if row["employee"] == WORKER
		)
		self.assertEqual(slip["minimum_wage_detail"]["by_state"]["OR"]["minimum_wage"], 15.95)
