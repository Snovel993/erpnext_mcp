# SPDX-License-Identifier: MIT
"""Budget + Variance Alerts — v0.42.0. The pure engine, tested as a pure engine.

`budget_engine.py` imports nothing from `frappe` and reads no database, so this
suite needs neither the stub `STORE` nor a fixture company: every function here
takes a plain dict in and returns a plain dict out, and a `unittest.TestCase`
with no setup is the honest test for that. The impure half — reading GL Entry
and the KPI cache, writing a Budget document's computed fields — lives in
`tools/budget.py` and is exercised through the tool layer elsewhere; this file
is about the arithmetic alone.

FOUR CLAIMS, ONE CLASS EACH.

1. `ComputingActuals` — `compute_budget_actuals` reads a matched account or KPI
   into a real figure, leaves an unmatched one `None` rather than zero, and
   treats a zero-budgeted line as "no percentage" rather than "0% variance".
2. `CheckingVariances` — `check_budget_variances` finds exactly the rows whose
   variance has crossed their OWN threshold, skips a row with no percentage to
   check, and reports the direction and the ratio a breach crossed by.
3. `SeverityEscalates` — the ratio-of-ratio rule at its two boundaries: just
   under twice a threshold is Warning, at or past twice it is Critical, and the
   boundary itself is inclusive on both ends of the definitions it uses.
4. `RefreshBudgetEndToEnd` — the combined operation on a small budget with a
   mix of clean and breaching rows, checked against what the two halves would
   have produced separately.
"""

import unittest

from erpnext_mcp import budget_engine as engine


def line(account="5300 - Field Labor", budgeted=10000.0, threshold=10.0):
	return {"account": account, "budgeted_amount": budgeted, "threshold_pct": threshold}


def kpi_target(kpi_definition="cost_per_bin", target=25.0, threshold=10.0):
	return {"kpi_definition": kpi_definition, "target_value": target, "threshold_pct": threshold}


class ComputingActuals(unittest.TestCase):
	def test_a_matched_account_gets_a_real_variance(self):
		budget_doc = {"line_items": [line(budgeted=10000.0, threshold=10.0)], "kpi_targets": []}
		result = engine.compute_budget_actuals(budget_doc, {"5300 - Field Labor": 11000.0}, {})
		row = result["line_items"][0]
		self.assertEqual(row["actual_amount"], 11000.0)
		self.assertEqual(row["variance_amount"], 1000.0)
		self.assertEqual(row["variance_pct"], 10.0)
		self.assertEqual(row["threshold_pct"], 10.0)

	def test_an_account_absent_from_gl_balances_is_none_not_zero(self):
		"""A line whose account was never resolved is a gap in the read, not a
		claim that nothing moved. `gl_balances` omits the key entirely for that
		case — see `tools/budget.py` — and this must not read that as $0 actual,
		which would report every unresolved line as "100% under budget"."""
		budget_doc = {"line_items": [line(account="9999 - Unmapped", budgeted=5000.0)], "kpi_targets": []}
		result = engine.compute_budget_actuals(budget_doc, {}, {})
		row = result["line_items"][0]
		self.assertIsNone(row["actual_amount"])
		self.assertIsNone(row["variance_amount"])
		self.assertIsNone(row["variance_pct"])

	def test_zero_budgeted_amount_has_no_percentage(self):
		"""A line with nothing budgeted has a variance AMOUNT — the account still
		moved by something — but no variance PERCENTAGE, because a percentage of
		zero is undefined rather than infinite or zero."""
		budget_doc = {"line_items": [line(budgeted=0.0)], "kpi_targets": []}
		result = engine.compute_budget_actuals(budget_doc, {"5300 - Field Labor": 500.0}, {})
		row = result["line_items"][0]
		self.assertEqual(row["actual_amount"], 500.0)
		self.assertEqual(row["variance_amount"], 500.0)
		self.assertIsNone(row["variance_pct"])

	def test_threshold_pct_falls_back_to_the_default_when_absent(self):
		budget_doc = {
			"line_items": [{"account": "5300 - Field Labor", "budgeted_amount": 1000.0}],
			"kpi_targets": [],
		}
		result = engine.compute_budget_actuals(budget_doc, {"5300 - Field Labor": 1000.0}, {})
		self.assertEqual(result["line_items"][0]["threshold_pct"], engine.DEFAULT_THRESHOLD_PCT)

	def test_a_kpi_target_reads_the_same_way_as_a_line_item(self):
		budget_doc = {"line_items": [], "kpi_targets": [kpi_target(target=25.0, threshold=10.0)]}
		result = engine.compute_budget_actuals(budget_doc, {}, {"cost_per_bin": 30.0})
		row = result["kpi_targets"][0]
		self.assertEqual(row["target_value"], 25.0)
		self.assertEqual(row["actual_value"], 30.0)
		self.assertEqual(row["variance_pct"], 20.0)

	def test_a_kpi_with_no_cached_value_is_none(self):
		"""`kpi_values.get(...)` returning `None` — a KPI with no cached figure —
		must not become a variance of "-100%"; it is nothing to report yet."""
		budget_doc = {"line_items": [], "kpi_targets": [kpi_target()]}
		result = engine.compute_budget_actuals(budget_doc, {}, {"cost_per_bin": None})
		row = result["kpi_targets"][0]
		self.assertIsNone(row["actual_value"])
		self.assertIsNone(row["variance_pct"])

	def test_row_order_and_count_are_preserved(self):
		budget_doc = {
			"line_items": [line(account="A"), line(account="B"), line(account="C")],
			"kpi_targets": [],
		}
		result = engine.compute_budget_actuals(budget_doc, {"A": 1, "B": 2, "C": 3}, {})
		self.assertEqual([row["account"] for row in result["line_items"]], ["A", "B", "C"])

	def test_nothing_on_the_input_is_mutated(self):
		budget_doc = {"line_items": [line()], "kpi_targets": []}
		gl_balances = {"5300 - Field Labor": 11000.0}
		before = dict(budget_doc["line_items"][0])
		engine.compute_budget_actuals(budget_doc, gl_balances, {})
		self.assertEqual(budget_doc["line_items"][0], before)


class CheckingVariances(unittest.TestCase):
	def test_a_variance_inside_its_threshold_is_not_a_breach(self):
		result = {
			"line_items": [
				{
					"account": "A",
					"budgeted_amount": 1000.0,
					"actual_amount": 1050.0,
					"variance_amount": 50.0,
					"variance_pct": 5.0,
					"threshold_pct": 10.0,
				}
			],
			"kpi_targets": [],
		}
		self.assertEqual(engine.check_budget_variances(result), [])

	def test_a_variance_at_exactly_its_threshold_is_a_breach(self):
		"""The boundary is inclusive: a ratio of exactly 1.0 counts, because a
		variance sitting exactly on the line somebody drew is exactly what the
		threshold exists to catch."""
		result = {
			"line_items": [
				{
					"account": "A",
					"budgeted_amount": 1000.0,
					"actual_amount": 1100.0,
					"variance_amount": 100.0,
					"variance_pct": 10.0,
					"threshold_pct": 10.0,
				}
			],
			"kpi_targets": [],
		}
		breaches = engine.check_budget_variances(result)
		self.assertEqual(len(breaches), 1)
		self.assertEqual(breaches[0]["ratio"], 1.0)
		self.assertEqual(breaches[0]["severity"], engine.SEVERITY_WARNING)

	def test_a_row_with_no_percentage_is_never_a_breach(self):
		result = {
			"line_items": [
				{
					"account": "A",
					"budgeted_amount": 0.0,
					"actual_amount": 500.0,
					"variance_amount": 500.0,
					"variance_pct": None,
					"threshold_pct": 10.0,
				}
			],
			"kpi_targets": [],
		}
		self.assertEqual(engine.check_budget_variances(result), [])

	def test_direction_is_over_when_positive_and_under_when_negative(self):
		result = {
			"line_items": [
				{
					"account": "OVER",
					"budgeted_amount": 1000.0,
					"actual_amount": 1300.0,
					"variance_amount": 300.0,
					"variance_pct": 30.0,
					"threshold_pct": 10.0,
				},
				{
					"account": "UNDER",
					"budgeted_amount": 1000.0,
					"actual_amount": 700.0,
					"variance_amount": -300.0,
					"variance_pct": -30.0,
					"threshold_pct": 10.0,
				},
			],
			"kpi_targets": [],
		}
		breaches = {b["identifier"]: b for b in engine.check_budget_variances(result)}
		self.assertEqual(breaches["OVER"]["direction"], "over")
		self.assertEqual(breaches["UNDER"]["direction"], "under")

	def test_kpi_targets_are_checked_the_same_way_as_line_items(self):
		result = {
			"line_items": [],
			"kpi_targets": [
				{
					"kpi_definition": "cost_per_bin",
					"target_value": 25.0,
					"actual_value": 40.0,
					"variance_pct": 60.0,
					"threshold_pct": 10.0,
				}
			],
		}
		breaches = engine.check_budget_variances(result)
		self.assertEqual(len(breaches), 1)
		self.assertEqual(breaches[0]["kind"], "kpi_target")
		self.assertEqual(breaches[0]["identifier"], "cost_per_bin")

	def test_threshold_default_is_used_when_a_row_has_none(self):
		result = {
			"line_items": [
				{
					"account": "A",
					"budgeted_amount": 1000.0,
					"actual_amount": 1250.0,
					"variance_amount": 250.0,
					"variance_pct": 25.0,
					"threshold_pct": None,
				}
			],
			"kpi_targets": [],
		}
		# threshold_default of 20 puts a 25% variance just over the line.
		breaches = engine.check_budget_variances(result, threshold_default=20.0)
		self.assertEqual(len(breaches), 1)
		self.assertEqual(breaches[0]["threshold_pct"], 20.0)

	def test_breaches_are_sorted_worst_first(self):
		result = {
			"line_items": [
				{
					"account": "MILD",
					"budgeted_amount": 1000.0,
					"actual_amount": 1110.0,
					"variance_amount": 110.0,
					"variance_pct": 11.0,
					"threshold_pct": 10.0,
				},
				{
					"account": "SEVERE",
					"budgeted_amount": 1000.0,
					"actual_amount": 1500.0,
					"variance_amount": 500.0,
					"variance_pct": 50.0,
					"threshold_pct": 10.0,
				},
			],
			"kpi_targets": [],
		}
		breaches = engine.check_budget_variances(result)
		self.assertEqual([b["identifier"] for b in breaches], ["SEVERE", "MILD"])
		self.assertEqual(breaches[0]["severity"], engine.SEVERITY_CRITICAL)
		self.assertEqual(breaches[1]["severity"], engine.SEVERITY_WARNING)


class SeverityEscalates(unittest.TestCase):
	"""Warning at 1x-2x a row's own threshold, Critical at 2x or past it."""

	def _breach_at(self, variance_pct: float, threshold_pct: float = 10.0):
		result = {
			"line_items": [
				{
					"account": "A",
					"budgeted_amount": 1000.0,
					"actual_amount": 1000.0 * (1 + variance_pct / 100),
					"variance_amount": 1000.0 * (variance_pct / 100),
					"variance_pct": variance_pct,
					"threshold_pct": threshold_pct,
				}
			],
			"kpi_targets": [],
		}
		breaches = engine.check_budget_variances(result)
		self.assertEqual(len(breaches), 1, f"expected exactly one breach at {variance_pct}%")
		return breaches[0]

	def test_just_over_the_threshold_is_warning(self):
		self.assertEqual(self._breach_at(11.0, threshold_pct=10.0)["severity"], engine.SEVERITY_WARNING)

	def test_just_under_twice_the_threshold_is_still_warning(self):
		self.assertEqual(self._breach_at(19.9, threshold_pct=10.0)["severity"], engine.SEVERITY_WARNING)

	def test_exactly_twice_the_threshold_is_critical(self):
		self.assertEqual(self._breach_at(20.0, threshold_pct=10.0)["severity"], engine.SEVERITY_CRITICAL)

	def test_well_past_twice_the_threshold_is_critical(self):
		self.assertEqual(self._breach_at(45.0, threshold_pct=10.0)["severity"], engine.SEVERITY_CRITICAL)

	def test_escalation_is_relative_to_each_rows_own_threshold(self):
		"""A tightly-watched row (5%) and a loosely-watched one (40%) both
		escalate to Critical at their OWN double, not at a shared number."""
		tight = self._breach_at(10.0, threshold_pct=5.0)
		loose = self._breach_at(80.0, threshold_pct=40.0)
		self.assertEqual(tight["severity"], engine.SEVERITY_CRITICAL)
		self.assertEqual(loose["severity"], engine.SEVERITY_CRITICAL)

	def test_negative_variance_escalates_on_its_magnitude(self):
		self.assertEqual(self._breach_at(-25.0, threshold_pct=10.0)["severity"], engine.SEVERITY_CRITICAL)
		self.assertEqual(self._breach_at(-15.0, threshold_pct=10.0)["severity"], engine.SEVERITY_WARNING)


class RefreshBudgetEndToEnd(unittest.TestCase):
	def setUp(self):
		self.budget_doc = {
			"line_items": [
				line(account="CLEAN", budgeted=10000.0, threshold=10.0),
				line(account="WARNING", budgeted=10000.0, threshold=10.0),
				line(account="CRITICAL", budgeted=10000.0, threshold=10.0),
				line(account="UNRESOLVED", budgeted=5000.0, threshold=10.0),
			],
			"kpi_targets": [kpi_target(kpi_definition="cost_per_bin", target=25.0, threshold=10.0)],
		}
		self.gl_balances = {
			"CLEAN": 10200.0,  # 2% over — inside threshold
			"WARNING": 11500.0,  # 15% over — 1.5x threshold, Warning
			"CRITICAL": 13000.0,  # 30% over — 3x threshold, Critical
			# UNRESOLVED intentionally absent
		}
		self.kpi_values = {"cost_per_bin": 30.0}  # 20% over — 2x threshold, Critical

	def test_the_combined_result_matches_the_two_halves_run_separately(self):
		combined = engine.refresh_budget(self.budget_doc, self.gl_balances, self.kpi_values)
		actuals = engine.compute_budget_actuals(self.budget_doc, self.gl_balances, self.kpi_values)
		expected_breaches = engine.check_budget_variances(actuals)

		self.assertEqual(combined["line_items"], actuals["line_items"])
		self.assertEqual(combined["kpi_targets"], actuals["kpi_targets"])
		self.assertEqual(combined["breaches"], expected_breaches)

	def test_breach_counts(self):
		result = engine.refresh_budget(self.budget_doc, self.gl_balances, self.kpi_values)
		self.assertEqual(result["breach_count"], 3)
		self.assertEqual(result["critical_count"], 2)  # CRITICAL account + cost_per_bin KPI
		self.assertEqual(result["warning_count"], 1)  # WARNING account

	def test_the_clean_and_unresolved_rows_never_breach(self):
		result = engine.refresh_budget(self.budget_doc, self.gl_balances, self.kpi_values)
		identifiers = {b["identifier"] for b in result["breaches"]}
		self.assertNotIn("CLEAN", identifiers)
		self.assertNotIn("UNRESOLVED", identifiers)

	def test_breach_message_reads_the_right_direction_and_figures(self):
		result = engine.refresh_budget(self.budget_doc, self.gl_balances, self.kpi_values)
		by_id = {b["identifier"]: b for b in result["breaches"]}
		message = engine.breach_message(by_id["CRITICAL"])
		self.assertIn("CRITICAL", message)
		self.assertIn("over budget", message)
		self.assertIn("13000.00", message)
		self.assertIn("10000.00", message)

	def test_an_empty_budget_refreshes_to_no_breaches(self):
		result = engine.refresh_budget({"line_items": [], "kpi_targets": []}, {}, {})
		self.assertEqual(result["breaches"], [])
		self.assertEqual(result["breach_count"], 0)
		self.assertEqual(result["line_items"], [])
		self.assertEqual(result["kpi_targets"], [])


if __name__ == "__main__":
	unittest.main()
