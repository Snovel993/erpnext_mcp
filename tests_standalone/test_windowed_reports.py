# SPDX-License-Identifier: MIT
"""The window standard: the boundaries, the merge, the history, the cache.

THE CLAIM BEHIND THE RELEASE is that a financial figure quoted over one
agricultural period is not merely imprecise — it is confidently wrong in a
direction that changes with the month somebody asked. Q3 is harvest and Q1 is
pruning, so a quarter-on-quarter comparison says the farm collapsed in January
and recovered in September, every year, on every farm. So every financial report
in this app now defaults to a trailing twelve months, and the tests here are
mostly about the arithmetic that has to be exactly right for that to be worth
anything.

EIGHT CLAIMS.

1. `TheBoundaryIsOneRule` — the last COMPLETED step on or before the reporting
   moment, for all five steps, plus the inclusive-at-both-ends window start and
   the month-length clamp that stops a monthly series growing a thirteenth
   bucket in a long February.

2. `QuartersFollowTheFiscalYear` — a July-year operation steps its quarters and
   its years on its own calendar, says so in `fiscal_year_start_month`, and
   warns that it has done so.

3. `TheMerge` — sums add, weighted denominators re-weight over the longer window
   rather than being summed or averaged, lists concatenate and de-duplicate by
   docname. Plus the case that decides the whole module's shape: an adjustment
   spanning a quarter falls inside no monthly bucket, so a computer whose
   figures are counted by period containment is NOT bucket-additive and its
   window is computed whole.

4. `TheHistory` — a five-year Monthly lookback wants sixty entries; a short
   ledger gets a short series and a warning naming the count; the live
   computation cap truncates rather than hanging and says which tool fills the
   rest.

5. `TheStatistics` — mean, median, min, max and standard deviation from a known
   series; the two deltas; and every one of them None rather than zero where it
   cannot be computed.

6. `TheCache` — a hit returns without recomputing, a miss computes and writes,
   an approved adjustment invalidates the windows it changed, and
   `recompute_kpi_history(force=true)` clears and rebuilds.

7. `TheRetrofit` — the TTM shape by default, v0.19.5's exact payload for a
   v0.19.5 call, and the deprecation sentence that is the only thing added to
   it.

8. `TheGuards` — the role gate, the company scope, the kill switch, and the one
   promise a read tool that warms a cache has to make: it writes the cache and
   NOTHING else.
"""

import datetime
import json

import frappe

from erpnext_mcp import kpi
from erpnext_mcp.services import financial_reports  # noqa: F401  (registers the computers)
from erpnext_mcp.services import windowed_reports as windows

from .fixtures import MAIN, OTHER, V12TestCase, cash, sales
from .harness import ROLES, STORE, set_roles

#: The shipped role set, captured at import so `setUp` can put it back. `ROLES`
#: is a module global in the harness and nothing resets it between tests, so a
#: guard test in ANY earlier module leaves its narrowed role set behind — and
#: every tool in this file is behind a role gate. Restoring it here makes this
#: module independent of what ran before it, which is the property a suite this
#: size needs more than it needs tidiness.
SHIPPED_ROLES = list(ROLES["Administrator"])

ON = {
	f"allow_{name}": 1
	for name in (
		"get_windowed_report",
		"list_financial_kpi_history",
		"recompute_kpi_history",
		"get_sustainable_cf_per_acre",
		"create_normalization_adjustment",
		"approve_normalization_adjustment",
	)
}

SIGNATURE = "/files/accountant-signature.png"

WHY = (
	"Hail on 2026-04-11 destroyed the frost fans on blocks 3 and 4; the replacement was a "
	"single insured event and the last hail loss on this ground was 2011."
)

#: The reporting moment every test in this file reads from, so a boundary
#: assertion is a statement about the rule rather than about the day the suite
#: happened to run.
AS_OF = "2026-08-03"


class WindowTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		set_roles("Administrator", SHIPPED_ROLES)
		self.configure(enabled=1, **ON)

	# ── building blocks ─────────────────────────────────────────────────────
	def a_field(self, name, acreage, productive_from="2020-01-01", productive_through=None, **overrides):
		row = {
			"name": name,
			"field_name": name,
			"parcel": "Mill Creek",
			"owning_entity": overrides.pop("company", MAIN),
			"acreage": acreage,
			"productive_from_date": productive_from,
			"productive_through_date": productive_through,
			"pre_yield_end_date": None,
			"condition": "Good",
		}
		row.update(overrides)
		STORE.seed("Field", [row])
		return row

	def a_sale(self, posting_date, amount, company=MAIN):
		"""One cash sale: cash in, income out, one voucher, double entry intact."""
		voucher = f"ACC-JV-SALE-{posting_date}-{int(amount)}"
		STORE.seed(
			"GL Entry",
			[
				{
					"name": f"GL-CASH-{voucher}",
					"account": cash(),
					"posting_date": posting_date,
					"debit": amount,
					"credit": 0,
					"company": company,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": voucher,
					"is_opening": "No",
				},
				{
					"name": f"GL-SALES-{voucher}",
					"account": sales(),
					"posting_date": posting_date,
					"debit": 0,
					"credit": amount,
					"company": company,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": voucher,
					"is_opening": "No",
				},
			],
		)

	def an_approved_adjustment(self, start="2026-01-01", end="2026-03-31", amount=20000, **overrides):
		payload = {
			"company": MAIN,
			"fiscal_year": "2026",
			"period_start": start,
			"period_end": end,
			"amount": amount,
			"direction": "Add-back to OCF",
			"category": "Weather-Event-Loss",
			"justification": WHY,
		}
		payload.update(overrides)
		draft = self.tool_data("create_normalization_adjustment", payload)
		return self.tool_data(
			"approve_normalization_adjustment",
			{"name": draft["name"], "approver_signature_file_token": SIGNATURE},
		)

	def windowed(self, report_name="sustainable_cf_per_acre", **overrides):
		payload = {"report_name": report_name, "company": MAIN, "as_of": AS_OF}
		payload.update(overrides)
		return self.tool_data("get_windowed_report", payload)


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheBoundaryIsOneRule(WindowTestCase):
	"""period_end is the last COMPLETED step on or before the reporting moment."""

	def test_monthly_read_on_the_third_of_august_ends_on_the_thirty_first_of_july(self):
		end = windows.last_completed_boundary(AS_OF, windows.STEP_MONTHLY)
		self.assertEqual(end.isoformat(), "2026-07-31")
		self.assertEqual(windows.window_start(end, 12).isoformat(), "2025-08-01")

	def test_daily_ends_yesterday_because_today_has_not_happened_yet(self):
		"""The ledger gets postings all afternoon. A daily figure that changes
		four times between morning and evening is one nobody can put in an email."""
		end = windows.last_completed_boundary(AS_OF, windows.STEP_DAILY)
		self.assertEqual(end.isoformat(), "2026-08-02")
		self.assertEqual(windows.window_start(end, 12).isoformat(), "2025-08-03")

	def test_quarterly_ends_at_the_last_completed_quarter(self):
		end = windows.last_completed_boundary(AS_OF, windows.STEP_QUARTERLY)
		self.assertEqual(end.isoformat(), "2026-06-30")
		self.assertEqual(windows.window_start(end, 12).isoformat(), "2025-07-01")

	def test_weekly_ends_on_the_last_sunday_and_a_sunday_is_its_own_boundary(self):
		self.assertEqual(
			windows.last_completed_boundary(AS_OF, windows.STEP_WEEKLY).isoformat(), "2026-08-02"
		)
		self.assertEqual(
			windows.last_completed_boundary("2026-08-02", windows.STEP_WEEKLY).isoformat(),
			"2026-08-02",
		)

	def test_yearly_ends_at_the_last_completed_year(self):
		self.assertEqual(
			windows.last_completed_boundary(AS_OF, windows.STEP_YEARLY).isoformat(), "2025-12-31"
		)

	def test_a_month_end_read_on_its_own_last_day_is_already_complete(self):
		"""31 July read on 31 July ends on 31 July, not on 30 June. The rule is
		'the last completed boundary <= as_of', and a month whose last day is
		today has completed by the time anybody reads it."""
		self.assertEqual(
			windows.last_completed_boundary("2026-07-31", windows.STEP_MONTHLY).isoformat(),
			"2026-07-31",
		)

	def test_the_window_is_inclusive_at_both_ends(self):
		"""2025-08-01 to 2026-07-31 is twelve months. 2025-07-31 to 2026-07-31 is
		twelve months AND A DAY, and the error would be in the same direction for
		ever — which is the shape nobody notices and everybody inherits."""
		start = windows.window_start("2026-07-31", 12)
		self.assertEqual(start.isoformat(), "2025-08-01")
		self.assertEqual(
			windows.add_months(start, 12) - datetime.timedelta(days=1), datetime.date(2026, 7, 31)
		)

	def test_month_arithmetic_clamps_rather_than_rolling(self):
		"""31 January plus a month is 28 February. Rolling into March is how a
		monthly series grows a thirteenth bucket in a year with a long February."""
		self.assertEqual(windows.add_months("2026-01-31", 1).isoformat(), "2026-02-28")
		self.assertEqual(windows.add_months("2024-01-31", 1).isoformat(), "2024-02-29")
		self.assertEqual(windows.add_months("2026-03-31", -1).isoformat(), "2026-02-28")

	def test_the_point_in_time_block_is_the_step_that_just_finished(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed()
		self.assertEqual(data["point_in_time"]["period_start"], "2026-07-01")
		self.assertEqual(data["point_in_time"]["period_end"], "2026-07-31")
		self.assertEqual(data["window"]["period_start"], "2025-08-01")
		self.assertEqual(data["window"]["period_end"], "2026-07-31")

	def test_ttm_is_the_default_and_the_ttm_key_is_the_window(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed()
		self.assertEqual(data["window_type"], "TTM")
		self.assertEqual(data["window_months"], 12)
		self.assertEqual(data["computation_step"], "Monthly")
		self.assertEqual(data["ttm"], data["window"])

	def test_a_non_ttm_window_carries_no_ttm_key_rather_than_a_mislabelled_one(self):
		"""A YTD block under a key called `ttm` is the kind of thing somebody
		quotes."""
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed(window_type="YTD")
		self.assertNotIn("ttm", data)
		self.assertEqual(data["window"]["period_end"], AS_OF)
		self.assertEqual(data["window"]["period_start"], "2026-01-01")

	def test_a_snapshot_window_is_the_period_alone_with_no_history(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed(window_type="Snapshot")
		self.assertIsNone(data["window"])
		self.assertIsNone(data["historical_averages"])
		self.assertEqual(data["point_in_time"]["period_end"], "2026-07-31")

	def test_an_unknown_step_or_window_type_is_refused_by_name(self):
		message = self.tool_error(
			"get_windowed_report",
			{"report_name": "revenue", "company": MAIN, "computation_step": "Fortnightly"},
		)
		self.assertIn("Fortnightly", message)
		self.assertIn("Monthly", message)
		message = self.tool_error(
			"get_windowed_report",
			{"report_name": "revenue", "company": MAIN, "window_type": "LTM"},
		)
		self.assertIn("LTM", message)
		self.assertIn("TTM", message)

	def test_an_unregistered_report_is_refused_with_the_ones_there_are(self):
		message = self.tool_error("get_windowed_report", {"report_name": "ebitda_per_acre", "company": MAIN})
		self.assertIn("ebitda_per_acre", message)
		self.assertIn("sustainable_cf_per_acre", message)


# ── 2 ───────────────────────────────────────────────────────────────────────
class QuartersFollowTheFiscalYear(WindowTestCase):
	"""A month is a month everywhere. A quarter is not."""

	def a_july_fiscal_year(self):
		STORE.seed(
			"Fiscal Year",
			[
				{
					"name": "2026-27",
					"year_start_date": "2026-07-01",
					"year_end_date": "2027-06-30",
				}
			],
		)

	def test_a_calendar_year_anchors_quarters_on_march_june_september_december(self):
		self.assertEqual(windows.quarter_end_months(1), [3, 6, 9, 12])

	def test_a_february_year_anchors_them_on_its_own_quarters(self):
		"""February to April, May to July, August to October, November to January."""
		self.assertEqual(windows.quarter_end_months(2), [1, 4, 7, 10])
		self.assertEqual(
			windows.last_completed_boundary(AS_OF, windows.STEP_QUARTERLY, 2).isoformat(),
			"2026-07-31",
		)

	def test_a_july_year_ends_its_year_in_june_not_december(self):
		self.assertEqual(
			windows.last_completed_boundary(AS_OF, windows.STEP_YEARLY, 7).isoformat(), "2026-06-30"
		)

	def test_the_anchor_is_reported_rather_than_left_to_be_inferred(self):
		self.a_july_fiscal_year()
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed(computation_step="Quarterly")
		self.assertEqual(data["fiscal_year_start_month"], 7)

	def test_a_non_calendar_year_says_out_loud_that_its_quarters_are_fiscal(self):
		self.a_july_fiscal_year()
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed(computation_step="Quarterly")
		self.assertTrue(
			any("FISCAL rather than calendar" in line for line in data["computation_warnings"]),
			data["computation_warnings"],
		)

	def test_a_site_with_no_fiscal_year_falls_back_to_january_without_failing(self):
		STORE.tables["Fiscal Year"].clear()
		self.assertEqual(windows.fiscal_year_start_month(MAIN), 1)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheMerge(WindowTestCase):
	"""Sums add, denominators re-weight, lists de-duplicate — and the case that
	says which computers may be merged at all."""

	def a_bucket(self, start, end, days, **components):
		return {"period_start": start, "period_end": end, "days": days, "components": components}

	def test_sum_components_add_across_the_window(self):
		entry = windows.register(
			"_test_sum", lambda *a: {}, sum_keys=("raw_ocf.value",), value_key="raw_ocf.value"
		)
		buckets = [
			self.a_bucket("2026-01-01", "2026-01-31", 31, raw_ocf={"value": 100.0}),
			self.a_bucket("2026-02-01", "2026-02-28", 28, raw_ocf={"value": 250.0}),
			self.a_bucket("2026-03-01", "2026-03-31", 31, raw_ocf={"value": -50.0}),
		]
		merged = windows.aggregate_components(buckets, entry, 90)
		self.assertEqual(merged["raw_ocf"]["value"], 300.0)

	def test_a_time_weighted_denominator_reweights_and_is_not_summed(self):
		"""A block productive for six of twelve months contributed a FULL month's
		weight to each of six buckets. Summing them would give six farms; the
		window's answer is half of one."""
		entry = windows.register(
			"_test_weighted", lambda *a: {}, weighted_keys=("productive_acres.time_weighted",)
		)
		buckets = [
			self.a_bucket(
				f"2026-{m:02d}-01", f"2026-{m:02d}-28", 30, productive_acres={"time_weighted": 100.0}
			)
			for m in range(1, 7)
		] + [
			self.a_bucket(f"2026-{m:02d}-01", f"2026-{m:02d}-28", 30, productive_acres={"time_weighted": 0.0})
			for m in range(7, 13)
		]
		merged = windows.aggregate_components(buckets, entry, 360)
		self.assertEqual(merged["productive_acres"]["time_weighted"], 50.0)

	def test_list_components_concatenate_and_dedupe_by_docname(self):
		entry = windows.register("_test_lists", lambda *a: {}, list_keys=("maintenance_capex.itemized",))
		buckets = [
			self.a_bucket(
				"2026-01-01",
				"2026-01-31",
				31,
				maintenance_capex={"itemized": [{"asset": "ACC-ASSET-001", "maintenance_portion": 500}]},
			),
			self.a_bucket(
				"2026-02-01",
				"2026-02-28",
				28,
				maintenance_capex={
					"itemized": [
						{"asset": "ACC-ASSET-001", "maintenance_portion": 500},
						{"asset": "ACC-ASSET-002", "maintenance_portion": 900},
					]
				},
			),
		]
		merged = windows.aggregate_components(buckets, entry, 59)
		names = [row["asset"] for row in merged["maintenance_capex"]["itemized"]]
		self.assertEqual(names, ["ACC-ASSET-001", "ACC-ASSET-002"])

	def test_revenue_is_additive_and_hands_back_its_per_step_trail(self):
		"""The one registered computer that may be assembled from its buckets:
		a sum over GL rows with no containment rule anywhere in it."""
		self.a_sale("2025-11-15", 4000)
		self.a_sale("2026-05-20", 6000)
		data = self.windowed("revenue")
		self.assertTrue(windows.COMPUTERS["revenue"]["bucket_additive"])
		self.assertEqual(len(data["window"]["buckets"]), 12)
		months = {bucket["period_end"]: bucket["value"] for bucket in data["window"]["buckets"]}
		self.assertEqual(months["2025-11-30"], 4000.0)
		self.assertEqual(months["2026-05-31"], 6000.0)

	def test_a_computer_counted_by_period_containment_is_not_additive(self):
		"""THE CASE THAT DECIDES THE MODULE'S SHAPE. `kpi.approved_in_period`
		counts an adjustment whose period falls INSIDE the window — deliberately,
		so a quarterly insurance recovery is not counted in a quarter and again
		in the year. A Q1 adjustment therefore falls inside NO monthly bucket, so
		a year assembled from twelve months would drop it silently: the figure
		would simply be lower, with nothing anywhere saying why."""
		self.a_field("Block 1 - MC", 100.0)
		self.an_approved_adjustment(start="2026-01-01", end="2026-03-31", amount=36000)

		# The quarter-long adjustment is in no month's bucket...
		for month_start, month_end in (("2026-01-01", "2026-01-31"), ("2026-02-01", "2026-02-28")):
			self.assertEqual(kpi.approved_in_period(MAIN, month_start, month_end), [])
		# ...and is in the twelve-month window, which is why the window is
		# computed whole and this computer is not registered as additive.
		self.assertEqual(len(kpi.approved_in_period(MAIN, "2025-08-01", "2026-07-31")), 1)
		self.assertFalse(windows.COMPUTERS["sustainable_cf_per_acre"]["bucket_additive"])

		data = self.windowed()
		self.assertEqual(data["window"]["components"]["normalization_adjustments_total_addback"], 36000.0)

	def test_the_ttm_components_carry_every_ingredient_the_period_did(self):
		"""Component itemization is not lost to the window — it is the whole
		reason v0.19.5 exists and the window inherits the obligation."""
		self.a_field("Block 1 - MC", 100.0)
		self.an_approved_adjustment(amount=12000)
		components = self.windowed()["window"]["components"]
		self.assertEqual(len(components["normalization_adjustments"]), 1)
		self.assertIn("justification", components["normalization_adjustments"][0])
		self.assertIn("itemized", components["maintenance_capex"])
		self.assertIn("itemized", components["productive_acres"])
		self.assertGreater(components["productive_acres"]["time_weighted"], 0)

	def test_the_ratio_is_recomputed_at_the_window_and_not_averaged(self):
		"""The average of twelve monthly ratios is not the ratio of the twelve
		month totals, and only the second is the number anybody means."""
		self.a_field("Block 1 - MC", 100.0)
		window = self.windowed()["window"]
		components = window["components"]
		expected = round(
			(components["normalized_ocf"] - components["maintenance_capex"]["total"])
			/ components["productive_acres"]["time_weighted"],
			2,
		)
		self.assertEqual(window["value"], expected)


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheHistory(WindowTestCase):
	def test_a_five_year_monthly_lookback_asks_for_sixty_entries(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed()
		self.assertEqual(data["historical_averages"]["requested_entries"], 60)
		self.assertEqual(data["historical_averages"]["lookback_years"], 5)

	def test_steps_per_year_is_the_series_lap_and_not_a_month_count(self):
		self.assertEqual(windows.STEPS_PER_YEAR["Monthly"], 12)
		self.assertEqual(windows.STEPS_PER_YEAR["Quarterly"], 4)
		self.assertEqual(windows.STEPS_PER_YEAR["Yearly"], 1)

	def test_a_short_ledger_gets_a_short_series_and_is_told_the_count(self):
		STORE.tables["GL Entry"].clear()
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2026-04-10", 5000)
		data = self.windowed()
		averages = data["historical_averages"]
		self.assertLess(averages["prior_ttm_count"], 60)
		self.assertTrue(
			any("does not go back that far" in line for line in data["computation_warnings"])
			or any("only" in line and "month(s) of ledger" in line for line in data["computation_warnings"]),
			data["computation_warnings"],
		)

	def test_a_partial_window_is_labelled_partial_and_never_annualized(self):
		"""Four months of ledger is four months of ledger. Annualizing it would
		invent eight months of a season that has not happened."""
		STORE.tables["GL Entry"].clear()
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2026-04-10", 5000)
		data = self.windowed()
		partial = [line for line in data["computation_warnings"] if "PARTIAL" in line]
		self.assertTrue(partial, data["computation_warnings"])
		self.assertIn("not annualized", partial[0])
		self.assertEqual(data["ledger_starts"], "2026-04-10")
		self.assertEqual(data["ledger_months_available"], 4)

	def test_an_empty_ledger_says_so_rather_than_reporting_a_business_that_earned_nothing(self):
		STORE.tables["GL Entry"].clear()
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed()
		self.assertIsNone(data["ledger_starts"])
		self.assertTrue(
			any("no submitted GL postings" in line for line in data["computation_warnings"]),
			data["computation_warnings"],
		)

	def test_the_live_cap_truncates_the_series_and_names_the_tool_that_fills_it(self):
		"""A read that runs for four minutes is a read somebody kills and then
		distrusts. A short series that says it is short is one they can act on."""
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2015-01-05", 1000)
		report = windows.run("sustainable_cf_per_acre", MAIN, as_of=AS_OF, live_computation_cap=3)
		self.assertEqual(report["historical_averages"]["computed_live"], 3)
		self.assertLess(report["historical_averages"]["prior_ttm_count"], 60)
		self.assertTrue(
			any("recompute_kpi_history" in line for line in report["computation_warnings"]),
			report["computation_warnings"],
		)

	def test_history_can_be_switched_off_entirely(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed(include_historical_averages=False)
		self.assertIsNone(data["historical_averages"])
		self.assertIsNotNone(data["window"])

	def test_a_lookback_past_the_ceiling_is_refused_rather_than_silently_clipped(self):
		message = self.tool_error(
			"get_windowed_report",
			{"report_name": "revenue", "company": MAIN, "historical_lookback_years": 40},
		)
		self.assertIn("10", message)


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheStatistics(WindowTestCase):
	def a_series(self, values):
		return [{"as_of": f"2026-{index:02d}-01", "value": value} for index, value in enumerate(values, 1)]

	def test_mean_median_min_max_and_stddev_from_a_known_series(self):
		summary = windows.summarise(self.a_series([100.0, 200.0, 300.0, 400.0]), 500.0, 12)
		self.assertEqual(summary["prior_ttm_mean"], 250.0)
		self.assertEqual(summary["prior_ttm_median"], 250.0)
		self.assertEqual(summary["prior_ttm_min"], 100.0)
		self.assertEqual(summary["prior_ttm_max"], 400.0)
		self.assertEqual(summary["prior_ttm_stddev"], 129.1)
		self.assertEqual(summary["prior_ttm_count"], 4)

	def test_current_vs_mean_is_a_percentage_of_the_mean(self):
		summary = windows.summarise(self.a_series([100.0, 200.0, 300.0]), 400.0, 12)
		self.assertEqual(summary["prior_ttm_mean"], 200.0)
		self.assertEqual(summary["current_vs_mean_pct_delta"], 100.0)

	def test_the_prior_year_comparator_is_one_lap_of_the_series_not_a_date(self):
		"""`series` is newest first, so on a Monthly step the prior year is index
		eleven — the twelfth entry back. Twelve months back from `as_of` can land
		inside a bucket; the entry a reader means is the same point last year."""
		series = self.a_series([float(100 + index) for index in range(24)])
		summary = windows.summarise(series, 200.0, 12)
		self.assertEqual(summary["prior_year_value"], series[11]["value"])
		self.assertEqual(summary["prior_year_value"], 111.0)

	def test_a_series_shorter_than_a_year_has_no_year_on_year_delta(self):
		summary = windows.summarise(self.a_series([100.0, 110.0]), 120.0, 12)
		self.assertIsNone(summary["current_vs_prior_year_pct_delta"])
		self.assertIsNone(summary["prior_year_value"])

	def test_every_statistic_is_none_rather_than_zero_where_it_cannot_be_computed(self):
		"""A standard deviation of zero means a perfectly steady business; a
		standard deviation of None means one snapshot. Reporting the first for
		the second is how a volatility figure becomes a covenant nobody can hold
		to."""
		empty = windows.summarise([], 100.0, 12)
		for key in (
			"prior_ttm_mean",
			"prior_ttm_median",
			"prior_ttm_min",
			"prior_ttm_max",
			"prior_ttm_stddev",
			"current_vs_mean_pct_delta",
			"current_vs_prior_year_pct_delta",
		):
			self.assertIsNone(empty[key], key)
		self.assertEqual(empty["prior_ttm_count"], 0)

		one = windows.summarise(self.a_series([100.0]), 100.0, 12)
		self.assertEqual(one["prior_ttm_mean"], 100.0)
		self.assertIsNone(one["prior_ttm_stddev"])

	def test_a_to_date_window_compares_the_same_span_and_not_a_rolling_year(self):
		"""THE QUIETEST WAY TO PRODUCE CONFIDENT NONSENSE. "Are we ahead of last
		year?" means the first eight months of this year against the first eight
		months of last year. A prior series built out of trailing-twelve-month
		windows would answer a different question with the same number of decimal
		places, and nobody would notice: both series are plausible and neither is
		labelled."""
		self.assertEqual(
			windows.to_date_prior(AS_OF, "YTD", 1),
			(datetime.date(2025, 1, 1), datetime.date(2025, 8, 3)),
		)
		self.assertEqual(
			windows.to_date_prior(AS_OF, "QTD", 1),
			(datetime.date(2026, 4, 1), datetime.date(2026, 5, 4)),
		)
		self.assertEqual(
			windows.to_date_prior(AS_OF, "MTD", 1),
			(datetime.date(2026, 7, 1), datetime.date(2026, 7, 3)),
		)

	def test_a_to_date_span_is_clamped_to_the_prior_periods_own_last_day(self):
		"""31 March has no counterpart in February, and 28 February is the honest
		answer rather than 3 March."""
		self.assertEqual(
			windows.to_date_prior("2026-03-31", "MTD", 1),
			(datetime.date(2026, 2, 1), datetime.date(2026, 2, 28)),
		)

	def test_a_to_date_series_laps_by_its_own_period(self):
		"""A YTD figure with twelve monthly entries under it would be twelve
		rolling years sitting beneath a year-to-date one."""
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2020-01-05", 1000)
		data = self.windowed(window_type="YTD", historical_lookback_years=3)
		averages = data["historical_averages"]
		self.assertEqual(averages["series_step"], "YTD")
		self.assertEqual(averages["requested_entries"], 3)
		for entry in averages["prior_ttm_series"]:
			self.assertTrue(entry["period_start"].endswith("-01-01"), entry)

	def test_a_ttm_series_laps_by_the_computation_step(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.windowed(computation_step="Quarterly", historical_lookback_years=2)
		self.assertEqual(data["historical_averages"]["series_step"], "Quarterly")
		self.assertEqual(data["historical_averages"]["requested_entries"], 8)

	def test_entries_with_no_value_are_left_out_of_the_averages(self):
		series = self.a_series([100.0, 200.0])
		series.append({"as_of": "2026-03-01", "value": None})
		summary = windows.summarise(series, 300.0, 12)
		self.assertEqual(summary["prior_ttm_count"], 2)
		self.assertEqual(summary["prior_ttm_mean"], 150.0)


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheCache(WindowTestCase):
	def test_a_miss_computes_and_writes_and_a_hit_returns_without_recomputing(self):
		self.a_field("Block 1 - MC", 100.0)
		calls = {"count": 0}
		real = windows.COMPUTERS["revenue"]["computer"]

		def counting(company, start, end):
			calls["count"] += 1
			return real(company, start, end)

		windows.COMPUTERS["revenue"]["computer"] = counting
		try:
			first = windows.run("revenue", MAIN, as_of=AS_OF)
			cold = calls["count"]
			self.assertGreater(cold, 0)
			self.assertGreater(len(STORE.rows(windows.DOCTYPE)), 0)

			calls["count"] = 0
			second = windows.run("revenue", MAIN, as_of=AS_OF)
			self.assertEqual(second["window"]["value"], first["window"]["value"])
			self.assertLess(calls["count"], cold)
		finally:
			windows.COMPUTERS["revenue"]["computer"] = real

	def test_a_cached_row_carries_the_components_not_only_the_number(self):
		"""A cached figure with no ingredients is one an auditor cannot test, and
		the historical figures are exactly the ones nobody can recompute from
		memory."""
		self.a_field("Block 1 - MC", 100.0)
		self.windowed()
		row = STORE.rows(windows.DOCTYPE)[0]
		components = json.loads(row["components_json"])
		self.assertIn("productive_acres", components)
		self.assertIn("maintenance_capex", components)
		self.assertTrue(row["source_version"])

	def test_the_cache_refuses_two_snapshots_of_one_reporting_moment(self):
		"""Two rows for one moment are two answers about one window, and the one
		a chart draws is whichever sorted first."""
		self.a_field("Block 1 - MC", 100.0)
		self.windowed()
		row = STORE.rows(windows.DOCTYPE)[0]
		duplicate = frappe.new_doc(windows.DOCTYPE)
		for field in windows.DOCTYPE and (
			"kpi_key",
			"company",
			"computation_step",
			"window_type",
			"window_months",
			"as_of",
			"period_start",
			"period_end",
		):
			setattr(duplicate, field, row[field])
		duplicate.value = 1.0
		duplicate.computed_at = frappe.utils.now()
		with self.assertRaises(Exception) as caught:
			duplicate.insert(ignore_permissions=True)
		self.assertIn("two answers about one window", str(caught.exception))

	def test_the_cache_refuses_a_window_that_runs_backwards(self):
		doc = frappe.new_doc(windows.DOCTYPE)
		doc.kpi_key = "revenue"
		doc.company = MAIN
		doc.computation_step = "Monthly"
		doc.window_type = "TTM"
		doc.window_months = 12
		doc.as_of = "2026-07-31"
		doc.period_start = "2026-07-31"
		doc.period_end = "2025-08-01"
		doc.computed_at = frappe.utils.now()
		with self.assertRaises(Exception) as caught:
			doc.insert(ignore_permissions=True)
		self.assertIn("negative time", str(caught.exception))

	def test_approving_an_adjustment_invalidates_the_windows_it_changed(self):
		"""A retroactive approval genuinely changes what every window containing
		it was worth. A cache that kept serving the old figure would be the most
		expensive kind of wrong: confidently precise, itemized, and stale."""
		self.a_field("Block 1 - MC", 100.0)
		self.windowed()
		before = len(STORE.rows(windows.DOCTYPE))
		self.assertGreater(before, 0)

		result = self.an_approved_adjustment(start="2026-01-01", end="2026-03-31")
		self.assertGreater(result["cache_invalidation"]["deleted"], 0)
		self.assertIn("INVALIDATED", result["cache_note"])
		self.assertLess(len(STORE.rows(windows.DOCTYPE)), before)

	def test_the_rebuilt_window_carries_the_new_adjustment(self):
		self.a_field("Block 1 - MC", 100.0)
		self.windowed()
		self.an_approved_adjustment(start="2026-01-01", end="2026-03-31", amount=48000)
		components = self.windowed()["window"]["components"]
		self.assertEqual(components["normalization_adjustments_total_addback"], 48000.0)

	def test_recompute_is_idempotent_and_force_clears_and_rebuilds(self):
		self.a_field("Block 1 - MC", 100.0)
		first = self.tool_data(
			"recompute_kpi_history", {"kpi_key": "revenue", "company": MAIN, "back_years": 1}
		)
		self.assertGreater(first["snapshots_written"], 0)
		self.assertEqual(first["snapshots_cleared"], 0)
		self.assertIn("second run finds nothing to do", first["idempotent_note"])

		again = self.tool_data(
			"recompute_kpi_history", {"kpi_key": "revenue", "company": MAIN, "back_years": 1}
		)
		self.assertEqual(again["snapshots_written"], 0)

		forced = self.tool_data(
			"recompute_kpi_history",
			{"kpi_key": "revenue", "company": MAIN, "back_years": 1, "force": True},
		)
		self.assertGreater(forced["snapshots_cleared"], 0)
		self.assertGreater(forced["snapshots_written"], 0)
		self.assertIn("DELETED and rebuilt", forced["force_note"])

	def test_recompute_refuses_a_kpi_no_computer_produces(self):
		message = self.tool_error("recompute_kpi_history", {"kpi_key": "roic", "company": MAIN})
		self.assertIn("roic", message)
		self.assertIn("sustainable_cf_per_acre", message)

	def test_the_sweep_with_no_cache_builds_the_lookback_and_the_second_run_does_nothing(self):
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2026-04-10", 5000)
		first = windows.recompute_kpi_history_incremental()
		self.assertGreater(first, 0)
		self.assertEqual(windows.recompute_kpi_history_incremental(), 0)

	def test_the_sweep_never_raises_on_a_mixed_batch(self):
		"""It runs on somebody's scheduler beside their real work, and it is the
		only job here whose cost scales with the size of their books."""
		self.a_field("Block 1 - MC", 100.0)
		windows.register("_test_broken", lambda *a: 1 / 0, kpi_key="_test_broken")
		try:
			self.assertIsInstance(windows.recompute_kpi_history_incremental(), int)
		finally:
			windows.COMPUTERS.pop("_test_broken", None)

	def test_the_sweep_does_nothing_with_its_switch_off(self):
		self.configure(enabled=1, enable_kpi_history_sweep=0, **ON)
		self.a_field("Block 1 - MC", 100.0)
		self.assertEqual(windows.recompute_kpi_history_incremental(), 0)

	def test_the_cache_reader_reports_a_gap_rather_than_drawing_over_it(self):
		self.a_field("Block 1 - MC", 100.0)
		self.windowed()
		data = self.tool_data("list_financial_kpi_history", {"company": MAIN})
		self.assertGreater(data["count"], 0)
		self.assertIn("NOT a period in which the business earned nothing", data["note"])
		self.assertEqual(len(data["series"]), data["count"])

	def test_the_cache_reader_says_when_nothing_is_cached(self):
		data = self.tool_data("list_financial_kpi_history", {"company": MAIN})
		self.assertEqual(data["count"], 0)
		self.assertIn("ordinary state of a site", data["empty_note"])

	def test_the_cache_reader_filters_by_kpi_and_by_date(self):
		self.a_field("Block 1 - MC", 100.0)
		self.windowed()
		self.windowed("revenue")
		only = self.tool_data("list_financial_kpi_history", {"company": MAIN, "kpi_key": "revenue"})
		self.assertTrue(only["count"])
		self.assertEqual({row["kpi_key"] for row in only["records"]}, {"revenue"})
		bounded = self.tool_data(
			"list_financial_kpi_history",
			{"company": MAIN, "from_date": "2026-07-01", "to_date": "2026-07-31"},
		)
		self.assertTrue(all(row["as_of"] <= "2026-07-31" for row in bounded["records"]))


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheRetrofit(WindowTestCase):
	def test_the_kpi_defaults_to_the_windowed_shape(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.tool_data("get_sustainable_cf_per_acre", {"company": MAIN, "as_of": AS_OF})
		self.assertEqual(data["window_type"], "TTM")
		self.assertEqual(data["ttm"]["period_start"], "2025-08-01")
		self.assertEqual(data["ttm"]["period_end"], "2026-07-31")
		self.assertIn("historical_averages", data)
		self.assertIn("point_in_time", data)

	def test_the_v0195_signature_still_returns_the_v0195_payload(self):
		"""This figure is quoted in packs that were sent before the window
		existed. A release that changed what an unchanged call returned would
		silently alter a number somebody had already sent to a bank."""
		self.a_field("Block 1 - MC", 100.0)
		data = self.tool_data(
			"get_sustainable_cf_per_acre",
			{"company": MAIN, "period_start": "2026-01-01", "period_end": "2026-03-31"},
		)
		self.assertEqual(data["period_start"], "2026-01-01")
		self.assertEqual(data["period_end"], "2026-03-31")
		self.assertIn("sustainable_cf_per_acre", data)
		self.assertIn("raw_ocf", data)
		self.assertNotIn("ttm", data)
		self.assertNotIn("historical_averages", data)

	def test_the_old_shape_carries_the_deprecation_sentence_and_nothing_else_new(self):
		self.a_field("Block 1 - MC", 100.0)
		data = self.tool_data(
			"get_sustainable_cf_per_acre",
			{"company": MAIN, "period_start": "2026-01-01", "period_end": "2026-03-31"},
		)
		self.assertIn("DEPRECATED CALL SHAPE", data["computation_warnings"][0])
		self.assertEqual(data["call_shape"], "v0.19.5 point-in-time (deprecated)")

	def test_half_the_old_signature_is_refused_rather_than_guessed_at(self):
		message = self.tool_error(
			"get_sustainable_cf_per_acre", {"company": MAIN, "period_start": "2026-01-01"}
		)
		self.assertIn("go TOGETHER", message)

	def test_the_service_arithmetic_is_unchanged_by_the_retrofit(self):
		"""The KPI's arithmetic did not change in v0.19.6 and must not appear to
		have: the same period through the new machinery gives the old answer."""
		self.a_field("Block 1 - MC", 100.0)
		old = self.tool_data(
			"get_sustainable_cf_per_acre",
			{"company": MAIN, "period_start": "2025-08-01", "period_end": "2026-07-31"},
		)
		new = self.windowed()
		self.assertEqual(new["window"]["value"], old["sustainable_cf_per_acre"])

	def test_ocf_is_reachable_without_the_whole_kpi_apparatus(self):
		"""A lender's coverage test needs normalized OCF and nothing else, and a
		figure that can only be got with an acreage denominator attached is one
		people copy out by hand."""
		data = self.windowed("ocf")
		components = data["window"]["components"]
		self.assertIn("normalized_ocf", components)
		self.assertIn("raw_operating_cash_flow", components)
		self.assertNotIn("productive_acres", components)
		self.assertEqual(data["window"]["value"], components["normalized_ocf"])

	def test_normalized_ocf_is_the_headline_and_raw_is_beside_it(self):
		"""A wrapper whose headline was the unadjusted figure would put the
		flattered number back at the top of the payload."""
		self.an_approved_adjustment(amount=15000)
		components = self.windowed("ocf")["window"]["components"]
		self.assertEqual(
			components["normalized_ocf"],
			round(components["raw_operating_cash_flow"] + 15000, 2),
		)

	def test_revenue_is_credits_less_debits_and_says_it_is_not_the_pl_line(self):
		self.a_sale("2026-05-20", 7500)
		data = self.windowed("revenue")
		components = data["window"]["components"]
		self.assertIn("NOT ERPNext's P&L revenue line", components["basis"])
		self.assertGreaterEqual(components["total"], 7500.0)


# ── 8 ───────────────────────────────────────────────────────────────────────
class TheGuards(WindowTestCase):
	def test_a_principal_without_one_of_the_three_roles_is_refused_by_name(self):
		set_roles("Administrator", ["HR User"])
		for tool, arguments in (
			("get_windowed_report", {"report_name": "revenue", "company": MAIN}),
			("list_financial_kpi_history", {"company": MAIN}),
			("recompute_kpi_history", {"kpi_key": "revenue", "company": MAIN}),
		):
			with self.subTest(tool=tool):
				message = self.tool_error(tool, arguments)
				self.assertIn("Accounts Manager", message)
				self.assertIn("Farm Manager", message)

	def test_an_entity_scoped_principal_cannot_read_another_entitys_window(self):
		STORE.seed(
			"User Permission",
			[{"name": "UP-KPI-1", "user": "Administrator", "allow": "Company", "for_value": MAIN}],
		)
		message = self.tool_error("get_windowed_report", {"report_name": "revenue", "company": OTHER})
		self.assertIn(OTHER, message)

	def test_a_recompute_is_scoped_to_the_companies_the_caller_may_see(self):
		STORE.seed(
			"User Permission",
			[{"name": "UP-KPI-2", "user": "Administrator", "allow": "Company", "for_value": MAIN}],
		)
		data = self.tool_data("recompute_kpi_history", {"kpi_key": "revenue", "back_years": 1})
		self.assertEqual(data["companies"], [MAIN])

	def test_every_tool_is_refused_with_its_switch_off(self):
		for tool, arguments in (
			("get_windowed_report", {"report_name": "revenue", "company": MAIN}),
			("list_financial_kpi_history", {"company": MAIN}),
			("recompute_kpi_history", {"kpi_key": "revenue", "company": MAIN}),
		):
			with self.subTest(tool=tool):
				self.configure(enabled=1, **{**ON, f"allow_{tool}": 0})
				message = self.tool_error(tool, arguments)
				self.assertIn(f"allow_{tool}", message)
				self.assertIn("switched off", message)

	def test_recompute_ships_off_and_the_two_reads_ship_on(self):
		"""The read/write posture of the release, read off the shipped DocType
		rather than restated here."""
		meta = frappe.get_meta("ERPNext MCP Settings")
		defaults = {field.fieldname: field.default for field in meta.fields}
		self.assertEqual(defaults["allow_get_windowed_report"], "1")
		self.assertEqual(defaults["allow_list_financial_kpi_history"], "1")
		self.assertEqual(defaults["allow_recompute_kpi_history"], "0")
		self.assertEqual(defaults["enable_kpi_history_sweep"], "1")

	def test_a_windowed_read_writes_the_cache_and_nothing_else(self):
		"""THE ONE PROMISE A READ TOOL THAT WARMS A CACHE HAS TO MAKE. It is
		annotated read-only and it does write — to Financial KPI History, which
		is derived state the overnight sweep would have written anyway. Nothing
		in anybody's LEDGER may move: no Account, no GL Entry, no Journal Entry,
		no Asset, no Field, no adjustment."""
		self.a_field("Block 1 - MC", 100.0)
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.windowed()
		self.windowed("revenue")
		self.tool_data("list_financial_kpi_history", {"company": MAIN})
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		for doctype in (windows.DOCTYPE, "MCP Action Log"):
			before.pop(doctype, None)
			after.pop(doctype, None)
		self.assertEqual(before, after)

	def test_every_call_writes_an_action_log_row(self):
		self.a_field("Block 1 - MC", 100.0)
		self.windowed("revenue")
		self.tool_data("list_financial_kpi_history", {"company": MAIN})
		self.tool_data("recompute_kpi_history", {"kpi_key": "revenue", "company": MAIN, "back_years": 1})
		for tool in ("get_windowed_report", "list_financial_kpi_history", "recompute_kpi_history"):
			self.assertAudited(tool)

	def test_the_master_switch_takes_them_all_with_it(self):
		"""`enabled=0` makes the ENDPOINT behave as if it does not exist.

		Not a tool error — a 404 with `not found`, before any tool name is looked
		at. Asserted at that level rather than through `tool_error`, because the
		difference is the point: a per-tool switch refuses a tool that exists, and
		the master switch refuses to admit the endpoint is there at all.
		"""
		self.configure(enabled=0, **ON)
		for tool, arguments in (
			("get_windowed_report", {"report_name": "revenue", "company": MAIN}),
			("list_financial_kpi_history", {"company": MAIN}),
			("recompute_kpi_history", {"kpi_key": "revenue", "company": MAIN}),
		):
			with self.subTest(tool=tool):
				body, status = self.call("tools/call", {"name": tool, "arguments": arguments})
				self.assertEqual(status, 404, body)
				self.assertIn("not found", body["error"]["message"])

	def test_one_companys_window_contains_nothing_of_anothers(self):
		self.a_field("Block 1 - MC", 100.0)
		self.a_field("Block 9 - OT", 500.0, company=OTHER)
		components = self.windowed()["window"]["components"]
		names = [row["field"] for row in components["productive_acres"]["itemized"]]
		self.assertEqual(names, ["Block 1 - MC"])


# ── 9 ───────────────────────────────────────────────────────────────────────
class TheChart(WindowTestCase):
	"""The rolling line, the dashed rule, and the two reports that stay."""

	def report(self, **filters):
		from erpnext_mcp.erpnext_mcp.report.sustainable_cf_per_acre_ttm_monthly import (
			sustainable_cf_per_acre_ttm_monthly as ttm,
		)

		payload = {"company": MAIN, "as_of": AS_OF}
		payload.update(filters)
		return ttm.execute(payload)

	def test_it_draws_a_rolling_point_per_month_over_two_years(self):
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2020-01-05", 1000)
		_columns, rows, _message, chart = self.report()
		self.assertLessEqual(len(rows), 24)
		self.assertGreater(len(rows), 1)
		self.assertEqual(len(chart["data"]["labels"]), len(rows))
		self.assertEqual(len(chart["data"]["datasets"]), 1)

	def test_every_point_is_a_twelve_month_window_and_not_a_month(self):
		"""The whole retrofit in one assertion: consecutive points differ by the
		month that entered and the month that left, not by a whole season."""
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2020-01-05", 1000)
		_columns, rows, _message, _chart = self.report()
		for row in rows:
			start = windows.as_date(row["period_start"])
			end = windows.as_date(row["period_end"])
			self.assertEqual(windows.add_months(end, -12), start - datetime.timedelta(days=1))

	def test_the_line_reads_left_to_right_oldest_first(self):
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2020-01-05", 1000)
		_columns, rows, _message, _chart = self.report()
		self.assertEqual([row["month"] for row in rows], sorted(row["month"] for row in rows))
		self.assertEqual(rows[-1]["period_end"], "2026-07-31")

	def test_the_mean_is_a_dashed_reference_rule_and_not_a_second_line(self):
		"""A second solid line invites the reader to compare its SHAPE with the
		first, which is meaningless — it has none. frappe-charts draws a yMarker
		as a dashed labelled rule, which is what a reference level is."""
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2020-01-05", 1000)
		_columns, _rows, _message, chart = self.report()
		self.assertEqual(len(chart["yMarkers"]), 1)
		self.assertIn("mean", chart["yMarkers"][0]["label"].lower())
		self.assertEqual(len(chart["data"]["datasets"]), 1)

	def test_the_components_are_columns_because_a_tooltip_cannot_hold_four(self):
		self.a_field("Block 1 - MC", 100.0)
		columns, rows, _message, _chart = self.report()
		names = {column["fieldname"] for column in columns}
		self.assertLessEqual(
			{"normalized_ocf", "maintenance_capex", "productive_acres", "unclassified_assets"}, names
		)
		self.assertIsNotNone(rows[-1]["normalized_ocf"])
		self.assertIsNotNone(rows[-1]["productive_acres"])

	def test_the_warnings_are_rendered_above_the_line_not_summarised(self):
		"""A chart with a partial window in it looks exactly like a chart with a
		full one, and the difference is the whole claim."""
		STORE.tables["GL Entry"].clear()
		self.a_field("Block 1 - MC", 100.0)
		self.a_sale("2026-04-10", 5000)
		_columns, _rows, message, _chart = self.report()
		self.assertIn("PARTIAL", message)

	def test_a_multi_company_site_with_no_filter_is_a_page_not_an_exception(self):
		columns, rows, _message, chart = self.report(company=None)
		self.assertTrue(columns)
		self.assertEqual(rows, [])
		self.assertEqual(chart["data"]["labels"], [])

	def test_both_charts_ship_and_the_rolling_one_is_first(self):
		from erpnext_mcp import dashboard

		names = [spec["chart_name"] for spec in dashboard.KPI_CHARTS]
		self.assertEqual(names[0], dashboard.KPI_TTM_CHART_NAME)
		self.assertIn(dashboard.KPI_CHART_NAME, names)

	def test_the_quarterly_chart_keeps_its_docname_so_dashboards_survive(self):
		"""A Dashboard Chart's docname is what a Dashboard and anybody's saved
		link point at. Renaming the v0.19.5 record to demote it would silently
		empty the dashboard of every site that installed v0.19.5."""
		from erpnext_mcp import dashboard

		self.assertEqual(dashboard.KPI_CHART_NAME, "Sustainable CF/Acre by Quarter")

	def test_only_the_rolling_chart_supplies_its_own_chart_config(self):
		"""The dashed rule can only reach a Dashboard Chart through the report's
		own chart config, which is the one mechanical difference between them."""
		from erpnext_mcp import dashboard

		by_name = {spec["chart_name"]: spec for spec in dashboard.KPI_CHARTS}
		self.assertEqual(by_name[dashboard.KPI_TTM_CHART_NAME]["use_report_chart"], 1)
		self.assertEqual(by_name[dashboard.KPI_CHART_NAME]["use_report_chart"], 0)

	def test_a_missing_report_takes_down_only_its_own_chart(self):
		from erpnext_mcp import dashboard

		STORE.tables["Report"].pop("Sustainable CF Per Acre by Quarter", None)
		report = dashboard.install_kpi_charts()
		failed = [row["name"] for row in report["failed"]]
		self.assertEqual(failed, [dashboard.KPI_CHART_NAME])
		self.assertIn(dashboard.KPI_TTM_CHART_NAME, report["created_charts"] + report["existing_charts"])
