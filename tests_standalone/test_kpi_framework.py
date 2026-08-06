# SPDX-License-Identifier: MIT
"""The Financial KPI Framework — KPIs as data. v0.39.0.

THE CLAIM BEHIND THE RELEASE is that a KPI is a QUESTION about a ledger, and a
question is a record rather than a function. v0.19.6 made the window standard
generalize across three SHIPPED reports; this makes the KPI itself editable, so
an operation can add the ratio its own lender asks about without a release.

The tests here are mostly about the two things that have to be exactly right for
that to be worth having: the sandbox has to refuse everything that is not
arithmetic, and a definition has to be refused at SAVE time for anything that
would otherwise be discovered as a quiet null at two in the morning.

EIGHT CLAIMS.

1. `TheSandboxIsArithmeticAndNothingElse` — `import`, attribute access,
   subscripts, lambdas, comprehensions, calls to anything but the four named
   functions, string constants and runaway exponents are each refused BY NAME,
   at check time. Arithmetic evaluates. A division by zero is a NULL with a
   warning naming the empty denominator, never a zero.

2. `TheInputsResolveToQueries` — the four sources, and the distinction the whole
   `gl` source turns on: a balance is a POSITION at the window's end and a
   movement is what crossed it, and a current ratio built from the second is not
   a current ratio.

3. `ADefinitionIsRefusedBeforeItLies` — a duplicate kpi_id, a key that is not a
   key, a Built-in naming a computer this site has not got, an expression
   reading an undefined variable, thresholds whose critical line is on the wrong
   side of its warning line, and a KPI that reads itself.

4. `ComputingGoesThroughTheWindowStandard` — a defined KPI and
   `get_windowed_report` produce the same figure for the same window, because
   they go through the same function. One broken definition does not empty a
   dashboard.

5. `TheThresholdVerdict` — Critical beats Warning, both beat OK, and a KPI with
   no thresholds says `No thresholds` rather than `OK` — because "nobody drew a
   line" and "inside the line" are different statements.

6. `TheAlertIsOnTheOneCalendar` — a breach raises a Compliance Alert under
   `Finance`, it reads the CACHE and never computes, a KPI with no cached value
   raises nothing AND DISMISSES NOTHING, and the alert clears by itself when the
   value comes back inside.

7. `TheCacheAndTheOvernightJob` — the refresh is incremental, `force` clears and
   rebuilds, the scheduled entry point never raises and takes no arguments, and
   the kill switch is the one the 02:00 job already has.

8. `TheGuards` — the role gate, the company scope, the seeder's idempotence, and
   the one promise a read tool that warms a cache has to make: it writes the
   cache and NOTHING else.
"""

import json

import frappe

from erpnext_mcp import compliance_rules
from erpnext_mcp.alerts import rules as shipped_rules
from erpnext_mcp.services import (
	financial_reports,  # noqa: F401  (registers the computers)
	kpi_engine,
)
from erpnext_mcp.services import windowed_reports as windows

from .fixtures import MAIN, OTHER, V12TestCase, cash, sales
from .harness import ROLES, STORE, set_roles

#: The shipped role set, captured at import so `setUp` can put it back — the
#: same reason `test_windowed_reports.py` does it, and it matters more here
#: because every tool in this file is behind a role gate and a guard test in ANY
#: earlier module leaves its narrowed set behind.
SHIPPED_ROLES = list(ROLES["Administrator"])

ON = {
	f"allow_{name}": 1
	for name in (
		"create_financial_kpi_definition",
		"update_financial_kpi_definition",
		"list_financial_kpi_definitions",
		"get_financial_kpi_definition",
		"compute_kpi",
		"compute_all_kpis",
		"refresh_kpi_cache",
		"get_windowed_report",
		"list_financial_kpi_history",
		"recompute_kpi_history",
	)
}

#: The reporting moment every test reads from, so a boundary assertion is a
#: statement about the rule rather than about the day the suite happened to run.
AS_OF = "2026-08-03"


class KPIFrameworkTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		set_roles("Administrator", SHIPPED_ROLES)
		self.configure(enabled=1, **ON)

	# ── building blocks ─────────────────────────────────────────────────────
	def a_definition(self, **overrides):
		payload = {
			"kpi_id": "test_kpi",
			"title": "A Test KPI",
			"formula_type": "Built-in",
			"builtin_function": "revenue",
		}
		payload.update(overrides)
		return self.tool_data("create_financial_kpi_definition", payload)

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

	def a_cached_snapshot(self, kpi_id, value, as_of="2026-07-31", company=MAIN, **overrides):
		options = {"computation_step": "Monthly", "window_type": "TTM", "window_months": 12}
		options.update(overrides)
		return windows.cache_write(
			kpi_id,
			company,
			options["computation_step"],
			options["window_type"],
			options["window_months"],
			as_of,
			{
				"period_start": "2025-08-01",
				"period_end": as_of,
				"value": value,
				"components": {},
				"computation_warnings": [],
			},
		)


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheSandboxIsArithmeticAndNothingElse(KPIFrameworkTestCase):
	"""The one place in the finance side where text somebody typed becomes behaviour.

	It is not `exec`, it is not `eval`, and it never touches the real builtins:
	the text is parsed to an AST, every node is checked against an allowlist of
	arithmetic, and the surviving tree is walked by an evaluator that can reach
	the resolved variables and four bound functions and nothing else.
	"""

	def test_arithmetic_evaluates(self):
		outcome = kpi_engine.evaluate("(a - b) / c * 100", {"a": 10, "b": 4, "c": 2})
		self.assertEqual(outcome["value"], 300.0)
		self.assertEqual(outcome["warnings"], [])

	def test_the_four_named_functions_are_callable(self):
		self.assertEqual(
			kpi_engine.evaluate("round(min(a, b) + abs(c), 2)", {"a": 3, "b": 9, "c": -2})["value"], 5.0
		)
		self.assertEqual(kpi_engine.evaluate("max(a, b)", {"a": 3, "b": 9})["value"], 9.0)

	def test_a_conditional_is_allowed_because_a_ratio_can_have_a_floor(self):
		self.assertEqual(kpi_engine.evaluate("a / b if b > 0 else 0", {"a": 9, "b": 3})["value"], 3.0)
		self.assertEqual(kpi_engine.evaluate("a / b if b > 0 else 0", {"a": 9, "b": 0})["value"], 0.0)

	def test_import_is_refused_by_name(self):
		with self.assertRaises(kpi_engine.ExpressionError) as caught:
			kpi_engine.check_expression("__import__('os').system('rm -rf /')", ["a"])
		self.assertIn("cannot be called here", str(caught.exception))

	def test_attribute_access_is_refused_with_the_reason(self):
		"""`x.__class__.__bases__[0].__subclasses__()` needs no imports at all,
		which is why this refusal is by NODE TYPE rather than by name."""
		with self.assertRaises(kpi_engine.ExpressionError) as caught:
			kpi_engine.check_expression("a.__class__", ["a"])
		message = str(caught.exception)
		self.assertIn("Attribute is not allowed", message)
		self.assertIn("__subclasses__", message)

	def test_the_shapes_that_are_not_arithmetic_are_each_refused(self):
		for expression, expected in (
			("a[0]", "Subscript"),
			("[a, b]", "List"),
			("{a: b}", "Dict"),
			("(a for a in [1])", None),
			("[x * 2 for x in [1]]", None),
			("lambda: a", "Lambda"),
			("'a string'", "str constant"),
		):
			with self.subTest(expression=expression):
				with self.assertRaises(kpi_engine.ExpressionError) as caught:
					kpi_engine.check_expression(expression, ["a", "b"])
				if expected:
					self.assertIn(expected, str(caught.exception))

	def test_an_underscore_name_is_refused_whatever_it_turns_out_to_mean(self):
		with self.assertRaises(kpi_engine.ExpressionError) as caught:
			kpi_engine.check_expression("_secret + 1", ["_secret"])
		self.assertIn("starts with an underscore", str(caught.exception))

	def test_a_runaway_exponent_is_refused_at_evaluation(self):
		"""`9 ** 9 ** 9` is nine characters and holds a worker until somebody
		restarts it. No financial ratio needs a power above a small integer."""
		outcome = kpi_engine.evaluate("a ** b", {"a": 9, "b": 9999})
		self.assertIsNone(outcome["value"])
		self.assertIn("exponent", outcome["warnings"][0])

	def test_a_division_by_zero_is_a_null_naming_the_empty_denominator(self):
		"""NOT A ZERO. A farm with no acres in production has no cash flow per
		acre, and a zero there would be read as a result rather than as an
		absence."""
		outcome = kpi_engine.evaluate("revenue / acres", {"revenue": 100000, "acres": 0})
		self.assertIsNone(outcome["value"])
		self.assertIn("divided by zero", outcome["warnings"][0])
		self.assertIn("acres", outcome["warnings"][0])

	def test_an_unresolved_input_produces_a_null_rather_than_being_treated_as_zero(self):
		outcome = kpi_engine.evaluate("a + b", {"a": 5, "b": None})
		self.assertIsNone(outcome["value"])
		self.assertIn("'b' resolved to nothing", outcome["warnings"][0])

	def test_an_infinity_is_reported_rather_than_returned(self):
		"""An infinity on a dashboard is read as a very large result rather than
		as a broken formula."""
		outcome = kpi_engine.evaluate("a ** b", {"a": 1e300, "b": 2})
		self.assertIsNone(outcome["value"])
		self.assertTrue(outcome["warnings"])

	def test_an_undefined_variable_is_refused_at_check_time(self):
		with self.assertRaises(kpi_engine.ExpressionError) as caught:
			kpi_engine.check_expression("a / missing", ["a"])
		self.assertIn("'missing'", str(caught.exception))

	def test_an_unused_input_is_reported_and_not_refused(self):
		"""An unused input is usually half of a rename, and refusing the save
		would mean nobody could fix the other half."""
		checked = kpi_engine.check_expression("a + b", ["a", "b", "leftover"])
		self.assertEqual(checked["unused"], ["leftover"])
		self.assertEqual(checked["names"], ["a", "b"])


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheInputsResolveToQueries(KPIFrameworkTestCase):
	def income_over(self, start="2026-01-01", end="2026-12-31", company=MAIN):
		"""Income movement across a window, as the `gl` source resolves it.

		A DELTA RATHER THAN AN ABSOLUTE, in every test that uses it: the shared
		fixture seeds a ledger of its own, and a test asserting a round number
		would be asserting the fixture rather than the query.
		"""
		specs = kpi_engine.parse_inputs({"income": {"source": "gl", "root_type": "Income"}})
		return kpi_engine.resolve_inputs(specs, company, start, end)

	def test_a_gl_input_sums_movement_across_the_window(self):
		baseline = self.income_over()["values"]["income"]
		self.a_sale("2026-03-15", 40000)
		self.a_sale("2026-06-15", 60000)
		resolved = self.income_over()
		self.assertEqual(resolved["values"]["income"] - baseline, 100000.0)
		self.assertEqual(resolved["resolved"][0]["read_as"], "credits less debits")
		self.assertEqual(resolved["resolved"][0]["basis"], "movement across the window")

	def test_income_is_read_as_credits_less_debits_without_anybody_saying_so(self):
		"""Revenue read the other way comes out negative on every well-kept set
		of books, and a figure that is negative everywhere is one everybody
		misreads as a loss."""
		self.a_sale("2026-03-15", 40000)
		self.assertGreater(self.income_over()["values"]["income"], 0)

	def test_a_balance_is_a_position_at_the_window_end_and_a_movement_is_not(self):
		"""THE DISTINCTION THE WHOLE `gl` SOURCE TURNS ON. A current ratio built
		from twelve months of movement in a cash account is not a current ratio;
		it is a cash flow with a ratio's name on it."""
		movement = kpi_engine.parse_inputs(
			{"c": {"source": "gl", "accounts": [cash()], "sign": "debit_minus_credit"}}
		)
		balance = kpi_engine.parse_inputs(
			{"c": {"source": "gl", "accounts": [cash()], "sign": "debit_minus_credit", "balance": True}}
		)

		def read(specs):
			return kpi_engine.resolve_inputs(specs, MAIN, "2026-01-01", "2026-12-31")["values"]["c"]

		across_before, position_before = read(movement), read(balance)
		self.a_sale("2025-03-15", 30000)
		self.a_sale("2026-03-15", 40000)
		# The 2025 sale is BEFORE the window: it moves the position and not the
		# movement, which is the entire distinction.
		self.assertEqual(read(movement) - across_before, 40000.0)
		self.assertEqual(read(balance) - position_before, 70000.0)

	def test_a_constant_is_a_number_with_a_name(self):
		specs = kpi_engine.parse_inputs({"sqft": {"source": "constant", "value": 43560}})
		self.assertEqual(
			kpi_engine.resolve_inputs(specs, MAIN, "2026-01-01", "2026-12-31")["values"]["sqft"], 43560.0
		)

	def test_a_bare_number_is_accepted_as_the_short_form_of_a_constant(self):
		specs = kpi_engine.parse_inputs({"sqft": 43560})
		self.assertEqual(specs["sqft"], {"source": "constant", "value": 43560.0})

	def test_a_report_input_reads_a_component_off_a_shipped_computer(self):
		specs = kpi_engine.parse_inputs(
			{"sales": {"source": "report", "report_name": "revenue", "path": "total"}}
		)

		def read():
			return kpi_engine.resolve_inputs(specs, MAIN, "2026-01-01", "2026-12-31")["values"]["sales"]

		before = read()
		self.a_sale("2026-03-15", 40000)
		self.assertEqual(read() - before, 40000.0)

	def test_a_report_input_naming_an_unregistered_computer_is_refused_at_parse(self):
		with self.assertRaises(kpi_engine.ExpressionError) as caught:
			kpi_engine.parse_inputs({"x": {"source": "report", "report_name": "not_a_report"}})
		self.assertIn("produces nothing, for ever, quietly", str(caught.exception))

	def test_a_gl_input_that_narrows_nothing_is_refused(self):
		"""An unnarrowed query sums the entire chart of accounts to approximately
		zero, which is a number nobody would question."""
		with self.assertRaises(kpi_engine.ExpressionError) as caught:
			kpi_engine.parse_inputs({"x": {"source": "gl"}})
		self.assertIn("narrows nothing", str(caught.exception))

	def test_a_gl_input_matching_no_account_is_a_null_and_not_a_zero(self):
		specs = kpi_engine.parse_inputs({"x": {"source": "gl", "accounts": ["No Such Account - ETC"]}})
		resolved = kpi_engine.resolve_inputs(specs, MAIN, "2026-01-01", "2026-12-31")
		self.assertIsNone(resolved["values"]["x"])
		self.assertIn("chart-of-accounts gap", resolved["warnings"][0])

	def test_a_kpi_input_reads_another_definition(self):
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue", builtin_function="revenue")
		self.a_definition(
			kpi_id="revenue_in_thousands",
			title="Revenue in Thousands",
			formula_type="Expression",
			expression="base / divisor",
			expression_inputs={
				"base": {"source": "kpi", "kpi_id": "gross_revenue"},
				"divisor": {"source": "constant", "value": 1000},
			},
		)
		row = kpi_engine.definition_row("revenue_in_thousands")
		point = kpi_engine.compute_point(row, MAIN, "2026-01-01", "2026-12-31")
		direct = kpi_engine.compute_point(
			kpi_engine.definition_row("gross_revenue"), MAIN, "2026-01-01", "2026-12-31"
		)
		self.assertEqual(point["value"], round(direct["total"] / 1000, 6))

	def test_a_kpi_cycle_produces_a_null_and_names_the_chain(self):
		"""Caught on the first pass rather than by a worker running out of stack
		at two in the morning."""
		specs = kpi_engine.parse_inputs({"me": {"source": "kpi", "kpi_id": "looping"}})
		resolved = kpi_engine.resolve_inputs(specs, MAIN, "2026-01-01", "2026-12-31", _seen=("looping",))
		self.assertIsNone(resolved["values"]["me"])
		self.assertIn("already being computed in this chain", resolved["warnings"][0])


# ── 3 ───────────────────────────────────────────────────────────────────────
class ADefinitionIsRefusedBeforeItLies(KPIFrameworkTestCase):
	"""The failure every check here prevents is the quiet one: a definition that
	is accepted, saved, computed nightly, drawn on a dashboard, and wrong in a
	way nothing announces."""

	def test_a_duplicate_kpi_id_is_refused_because_it_is_the_cache_key(self):
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		message = self.tool_error(
			"create_financial_kpi_definition", {"kpi_id": "gross_revenue", "title": "Another one"}
		)
		self.assertIn("already defined", message)
		self.assertIn("CACHE KEY", message)

	def test_a_kpi_id_that_is_not_a_key_is_normalized_or_refused(self):
		message = self.tool_error(
			"create_financial_kpi_definition",
			{"kpi_id": "Gross Revenue!", "title": "x", "builtin_function": "revenue"},
		)
		self.assertIn("lower-case letters, digits and underscores", message)

	def test_a_builtin_with_no_computer_named_at_all_is_refused(self):
		"""`Built-in` is the default formula_type, so the minimal create call is
		three arguments rather than two — and the refusal names the computers
		this site has rather than leaving somebody to guess."""
		message = self.tool_error(
			"create_financial_kpi_definition", {"kpi_id": "nameless", "title": "Nameless"}
		)
		self.assertIn("has to name a builtin_function", message)
		self.assertIn("revenue", message)

	def test_a_builtin_naming_a_computer_this_site_has_not_got_is_refused(self):
		message = self.tool_error(
			"create_financial_kpi_definition",
			{"kpi_id": "phantom", "title": "Phantom", "builtin_function": "no_such_computer"},
		)
		self.assertIn("not a built-in computer", message)
		self.assertIn("produces nothing, for ever", message)

	def test_an_expression_reading_an_undefined_variable_is_refused(self):
		message = self.tool_error(
			"create_financial_kpi_definition",
			{
				"kpi_id": "broken",
				"title": "Broken",
				"formula_type": "Expression",
				"expression": "a / b",
				"expression_inputs": {"a": {"source": "constant", "value": 1}},
			},
		)
		self.assertIn("'b'", message)

	def test_a_critical_floor_above_its_warning_floor_is_refused(self):
		"""That pair can never be read the way it is written: every value past
		critical is also past warning, so the dashboard reports the lesser of the
		two on the more serious breach."""
		message = self.tool_error(
			"create_financial_kpi_definition",
			{
				"kpi_id": "crossed",
				"title": "Crossed",
				"builtin_function": "revenue",
				"threshold_warning_low": 100,
				"threshold_critical_low": 500,
			},
		)
		self.assertIn("critical floor", message)

	def test_a_critical_ceiling_below_its_warning_ceiling_is_refused(self):
		message = self.tool_error(
			"create_financial_kpi_definition",
			{
				"kpi_id": "crossed_high",
				"title": "Crossed High",
				"builtin_function": "revenue",
				"threshold_warning_high": 500,
				"threshold_critical_high": 100,
			},
		)
		self.assertIn("critical ceiling", message)

	def test_overlapping_low_and_high_thresholds_are_refused(self):
		"""Every possible value would breach one of them, so the KPI would be
		permanently in alert."""
		message = self.tool_error(
			"create_financial_kpi_definition",
			{
				"kpi_id": "always_bad",
				"title": "Always Bad",
				"builtin_function": "revenue",
				"threshold_warning_low": 900,
				"threshold_warning_high": 100,
			},
		)
		self.assertIn("permanently in alert", message)

	def test_a_kpi_that_reads_itself_is_refused(self):
		message = self.tool_error(
			"create_financial_kpi_definition",
			{
				"kpi_id": "ouroboros",
				"title": "Ouroboros",
				"formula_type": "Expression",
				"expression": "me + 1",
				"expression_inputs": {"me": {"source": "kpi", "kpi_id": "ouroboros"}},
			},
		)
		self.assertIn("reads itself", message)

	def test_a_pair_that_reads_each_other_is_refused(self):
		"""Built the only way a mutual pair CAN be built — A first, then B
		reading A, then A edited to read B. Neither could be computed first, so
		neither would have a value."""
		self.a_definition(kpi_id="alpha", title="Alpha", builtin_function="revenue")
		self.a_definition(
			kpi_id="beta",
			title="Beta",
			formula_type="Expression",
			expression="base * 2",
			expression_inputs={"base": {"source": "kpi", "kpi_id": "alpha"}},
		)
		message = self.tool_error(
			"update_financial_kpi_definition",
			{
				"kpi_id": "alpha",
				"formula_type": "Expression",
				"expression": "other + 1",
				"expression_inputs": {"other": {"source": "kpi", "kpi_id": "beta"}},
			},
		)
		self.assertIn("Neither can be computed first", message)

	def test_a_kpi_id_cannot_be_renamed(self):
		"""Renaming orphans the whole cached series: the old line stays in the
		table under a name nothing reads, and the chart starts again."""
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		message = self.tool_error(
			"update_financial_kpi_definition", {"kpi_id": "gross_revenue", "new_kpi_id": "gr"}
		)
		self.assertIn("cannot be changed", message)
		self.assertIn("orphans the whole series", message)

	def test_a_bad_window_is_refused(self):
		for field, value in (
			("default_window_type", "Fortnightly"),
			("default_computation_step", "Hourly"),
		):
			with self.subTest(field=field):
				message = self.tool_error(
					"create_financial_kpi_definition",
					{"kpi_id": f"bad_{field}", "title": "x", "builtin_function": "revenue", field: value},
				)
				self.assertIn("must be one of", message)

	def test_an_omitted_threshold_is_not_a_threshold_of_zero(self):
		"""On a cash-flow KPI those are two very different operations."""
		self.a_definition(kpi_id="unthresholded", title="Unthresholded")
		row = kpi_engine.definition_row("unthresholded")
		self.assertIsNone(kpi_engine.thresholds_of(row)["threshold_warning_low"])
		self.assertFalse(kpi_engine.thresholds_of(row)["has_any"])


# ── 4 ───────────────────────────────────────────────────────────────────────
class ComputingGoesThroughTheWindowStandard(KPIFrameworkTestCase):
	def test_a_built_in_definition_agrees_with_get_windowed_report(self):
		"""NOT TIDINESS. A framework whose new KPIs get a second, simpler window
		implementation is one where the new KPIs are quietly wrong at the fiscal
		year boundary, and nobody finds out until a lender does."""
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue", builtin_function="revenue")

		through_framework = self.tool_data(
			"compute_kpi", {"kpi_id": "gross_revenue", "company": MAIN, "as_of": AS_OF}
		)
		through_report = self.tool_data(
			"get_windowed_report", {"report_name": "revenue", "company": MAIN, "as_of": AS_OF}
		)
		self.assertEqual(through_framework["window"]["value"], through_report["window"]["value"])
		self.assertEqual(
			through_framework["window"]["period_start"], through_report["window"]["period_start"]
		)
		self.assertEqual(through_framework["window"]["period_end"], through_report["window"]["period_end"])

	def test_the_window_comes_from_the_definition_by_default(self):
		"""Which is what keeps a dashboard, its alerts and its cache agreeing
		without anybody passing anything."""
		self.a_definition(
			kpi_id="quarterly_revenue",
			title="Quarterly Revenue",
			default_window_type="Custom",
			default_window_months=3,
		)
		data = self.tool_data("compute_kpi", {"kpi_id": "quarterly_revenue", "company": MAIN, "as_of": AS_OF})
		self.assertEqual(data["window_type"], "Custom")
		self.assertEqual(data["window_months"], 3)

	def test_a_caller_may_override_the_definitions_window(self):
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		data = self.tool_data(
			"compute_kpi",
			{"kpi_id": "gross_revenue", "company": MAIN, "as_of": AS_OF, "window_months": 6},
		)
		self.assertEqual(data["window_months"], 6)

	def test_an_expression_kpi_carries_every_input_with_what_it_matched(self):
		"""AN EXPRESSION KPI IS AS DEFENSIBLE AS A BUILT-IN ONE, and this is why:
		a reader sees not just that the figure was 40000 but that it came from
		one income account, read as credits less debits, across the window."""
		self.a_sale("2026-03-15", 40000)
		self.a_definition(
			kpi_id="income_per_thousand",
			title="Income per Thousand",
			formula_type="Expression",
			expression="income / divisor",
			expression_inputs={
				"income": {"source": "gl", "root_type": "Income"},
				"divisor": {"source": "constant", "value": 1000},
			},
		)
		data = self.tool_data(
			"compute_kpi", {"kpi_id": "income_per_thousand", "company": MAIN, "as_of": AS_OF}
		)
		inputs = {entry["variable"]: entry for entry in data["window"]["components"]["inputs"]}
		self.assertEqual(inputs["income"]["read_as"], "credits less debits")
		self.assertEqual(inputs["divisor"]["value"], 1000.0)
		self.assertEqual(data["window"]["components"]["expression"], "income / divisor")

	def test_compute_all_kpis_returns_the_dashboard_in_order(self):
		self.a_definition(kpi_id="second", title="Second", display_order=20)
		self.a_definition(kpi_id="first", title="First", display_order=10)
		data = self.tool_data("compute_all_kpis", {"company": MAIN, "as_of": AS_OF})
		self.assertEqual([item["definition"]["kpi_id"] for item in data["kpis"]], ["first", "second"])

	def test_one_broken_definition_does_not_empty_the_dashboard(self):
		"""The same promise the compliance sweep makes about one rule that throws."""
		self.a_definition(kpi_id="healthy", title="Healthy")
		# Broken past the tool's own validation: the record is edited directly,
		# which is what a site looks like after a release removes a computer.
		self.a_definition(kpi_id="doomed", title="Doomed")
		frappe.db.set_value(
			kpi_engine.DOCTYPE, kpi_engine.definition_row("doomed")["name"], "builtin_function", "gone"
		)
		data = self.tool_data("compute_all_kpis", {"company": MAIN, "as_of": AS_OF})
		by_id = {item["definition"]["kpi_id"]: item for item in data["kpis"]}
		self.assertEqual(len(by_id), 2)
		self.assertIsNone(by_id["doomed"]["value"])
		self.assertTrue(by_id["doomed"]["computation_warnings"])

	def test_a_disabled_definition_is_not_computed(self):
		self.a_definition(kpi_id="switched_off", title="Switched Off", enabled=False)
		data = self.tool_data("compute_all_kpis", {"company": MAIN, "as_of": AS_OF})
		self.assertEqual(data["kpi_count"], 0)

	def test_a_definition_scoped_to_one_company_is_refused_on_another(self):
		self.a_definition(kpi_id="scoped", title="Scoped", company=OTHER)
		message = self.tool_error("compute_kpi", {"kpi_id": "scoped", "company": MAIN})
		self.assertIn("is defined for", message)

	def test_a_definition_with_no_company_applies_everywhere(self):
		self.a_definition(kpi_id="everywhere", title="Everywhere")
		for company in (MAIN, OTHER):
			with self.subTest(company=company):
				data = self.tool_data("compute_kpi", {"kpi_id": "everywhere", "company": company})
				self.assertEqual(data["company"], company)


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheThresholdVerdict(KPIFrameworkTestCase):
	def a_row(self, **thresholds):
		row = {
			"threshold_warning_low": None,
			"threshold_critical_low": None,
			"threshold_warning_high": None,
			"threshold_critical_high": None,
		}
		row.update(thresholds)
		return row

	def test_critical_beats_warning(self):
		row = self.a_row(threshold_warning_low=500, threshold_critical_low=100)
		self.assertEqual(kpi_engine.threshold_status(row, 50)["status"], "Critical")
		self.assertEqual(kpi_engine.threshold_status(row, 300)["status"], "Warning")
		self.assertEqual(kpi_engine.threshold_status(row, 900)["status"], "OK")

	def test_the_high_direction_is_a_real_one(self):
		"""A debt-to-equity ratio, a days-sales-outstanding and a cost per bin
		are all KPIs whose bad news is a big number."""
		row = self.a_row(threshold_warning_high=2.0, threshold_critical_high=3.0)
		self.assertEqual(kpi_engine.threshold_status(row, 3.5)["status"], "Critical")
		self.assertEqual(kpi_engine.threshold_status(row, 2.5)["status"], "Warning")
		self.assertEqual(kpi_engine.threshold_status(row, 1.0)["status"], "OK")

	def test_the_boundary_is_inclusive_in_both_directions(self):
		row = self.a_row(threshold_warning_low=500, threshold_warning_high=900)
		self.assertTrue(kpi_engine.threshold_status(row, 500)["breached"])
		self.assertTrue(kpi_engine.threshold_status(row, 900)["breached"])
		self.assertFalse(kpi_engine.threshold_status(row, 700)["breached"])

	def test_no_thresholds_is_not_ok(self):
		"""'Nobody drew a line' and 'inside the line' are different statements,
		and a dashboard showing them the same green would be claiming something
		nobody checked."""
		verdict = kpi_engine.threshold_status(self.a_row(), 42)
		self.assertEqual(verdict["status"], "No thresholds")
		self.assertFalse(verdict["breached"])

	def test_a_null_value_is_unknown_rather_than_a_pass(self):
		verdict = kpi_engine.threshold_status(self.a_row(threshold_warning_low=500), None)
		self.assertEqual(verdict["status"], "Unknown")
		self.assertFalse(verdict["breached"])
		self.assertIn("not a pass", verdict["message"])

	def test_compute_all_kpis_names_the_kpis_nothing_is_watching(self):
		"""An empty `breached` list is not a healthy operation."""
		self.a_definition(kpi_id="unwatched", title="Unwatched")
		data = self.tool_data("compute_all_kpis", {"company": MAIN, "as_of": AS_OF})
		self.assertEqual(data["breached"], [])
		self.assertIn("unwatched", data["unwatched_note"])


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheAlertIsOnTheOneCalendar(KPIFrameworkTestCase):
	"""A covenant about to be breached is exactly as much a Monday-morning
	problem as a cabin with a dead carbon monoxide detector, and an operation
	with two alerting systems reads neither."""

	RULE = "financial_kpi_threshold_breach"

	def scan(self, company=""):
		return list(shipped_rules.RULES[self.RULE].scan({"today": AS_OF, "company": company}) or [])

	def test_the_rule_ships_and_seeds_as_a_record(self):
		self.assertIn(self.RULE, shipped_rules.RULES)
		self.assertIn(self.RULE, [spec["rule_id"] for spec in compliance_rules.seed_specs()])

	def test_it_lands_under_the_finance_category(self):
		self.assertEqual(shipped_rules.RULES[self.RULE].category, "Finance")
		self.assertIn("Finance", compliance_rules.CATEGORIES)

	def test_a_cached_value_past_the_critical_line_raises_critical(self):
		self.a_definition(
			kpi_id="cf_per_acre",
			title="Cash Flow per Acre",
			threshold_warning_low=500,
			threshold_critical_low=100,
		)
		self.a_cached_snapshot("cf_per_acre", 50.0)
		observations = self.scan()
		self.assertEqual(len(observations), 1)
		self.assertEqual(observations[0].severity, "Critical")
		self.assertEqual(observations[0].source_doctype, kpi_engine.DOCTYPE)
		self.assertIn("critical threshold", observations[0].message)

	def test_a_cached_value_inside_the_lines_raises_nothing(self):
		self.a_definition(kpi_id="cf_per_acre", title="Cash Flow per Acre", threshold_warning_low=500)
		self.a_cached_snapshot("cf_per_acre", 900.0)
		self.assertEqual(self.scan(), [])

	def test_a_kpi_with_no_cached_value_raises_nothing_and_dismisses_nothing(self):
		"""The same reading an absent record gets everywhere else here: a
		definition created this morning is not evidence of anything yet."""
		self.a_definition(kpi_id="cf_per_acre", title="Cash Flow per Acre", threshold_warning_low=500)
		self.assertEqual(self.scan(), [])

	def test_a_kpi_with_no_thresholds_is_not_this_rules_business(self):
		self.a_definition(kpi_id="unwatched", title="Unwatched")
		self.a_cached_snapshot("unwatched", -99999.0)
		self.assertEqual(self.scan(), [])

	def test_it_reads_the_cache_and_never_computes(self):
		"""The alert sweep runs hourly beside somebody's real work; a scan that
		recomputed every KPI for every company would put minutes of GL arithmetic
		on that path, every hour, for a figure that moves once a month."""
		self.a_definition(kpi_id="cf_per_acre", title="Cash Flow per Acre", threshold_warning_low=500)
		self.a_cached_snapshot("cf_per_acre", 50.0)
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.scan()
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.assertEqual(before, after)

	def test_the_cached_value_is_the_one_the_dashboard_shows(self):
		"""Which is what guarantees an alert and a dashboard can never disagree
		about the number."""
		self.a_definition(kpi_id="cf_per_acre", title="Cash Flow per Acre", threshold_critical_low=100)
		self.a_cached_snapshot("cf_per_acre", 42.0)
		self.assertIn("42.0", self.scan()[0].message)

	def test_a_definition_scoped_to_one_company_only_fires_there(self):
		self.a_definition(kpi_id="scoped", title="Scoped", company=OTHER, threshold_critical_low=100)
		self.a_cached_snapshot("scoped", 50.0, company=MAIN)
		self.assertEqual(self.scan(), [])

	def test_one_observation_per_company(self):
		"""A KPI that applies to every company is genuinely several conditions,
		and merging them would mean one entity's breach dismissing another's."""
		self.a_definition(kpi_id="everywhere", title="Everywhere", threshold_critical_low=100)
		self.a_cached_snapshot("everywhere", 50.0, company=MAIN)
		self.a_cached_snapshot("everywhere", 10.0, company=OTHER)
		self.assertEqual(sorted(item.company for item in self.scan()), sorted([MAIN, OTHER]))

	def test_it_carries_no_due_date(self):
		"""Every other rule in that file has a date by which something must be
		done. A KPI past its threshold has no such date, because the remedy is a
		season of trading rather than a form — and a fabricated one would sort
		this above genuine deadlines."""
		self.a_definition(kpi_id="cf_per_acre", title="Cash Flow per Acre", threshold_critical_low=100)
		self.a_cached_snapshot("cf_per_acre", 50.0)
		self.assertEqual(self.scan()[0].due_date, "")

	def test_it_reads_the_definitions_own_window(self):
		"""A KPI declared as a quarterly snapshot and one declared as a monthly
		TTM cache under different keys, and matching on kpi_key alone would
		compare a value against a threshold drawn for a different window."""
		self.a_definition(
			kpi_id="quarterly",
			title="Quarterly",
			default_computation_step="Quarterly",
			threshold_critical_low=100,
		)
		self.a_cached_snapshot("quarterly", 50.0, computation_step="Monthly")
		self.assertEqual(self.scan(), [])
		self.a_cached_snapshot("quarterly", 50.0, computation_step="Quarterly")
		self.assertEqual(len(self.scan()), 1)


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheCacheAndTheOvernightJob(KPIFrameworkTestCase):
	def test_the_refresh_writes_snapshots_for_a_defined_kpi(self):
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		data = self.tool_data(
			"refresh_kpi_cache", {"kpi_id": "gross_revenue", "company": MAIN, "back_years": 1}
		)
		self.assertGreater(data["written"], 0)
		cached = [row for row in STORE.rows("Financial KPI History") if row["kpi_key"] == "gross_revenue"]
		self.assertTrue(cached)

	def test_a_second_run_finds_nothing_to_do(self):
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		self.tool_data("refresh_kpi_cache", {"kpi_id": "gross_revenue", "company": MAIN, "back_years": 1})
		again = self.tool_data(
			"refresh_kpi_cache", {"kpi_id": "gross_revenue", "company": MAIN, "back_years": 1}
		)
		self.assertEqual(again["written"], 0)

	def test_force_clears_and_rebuilds(self):
		"""What a changed formula needs: an incremental fill leaves the old rows
		in place, and a series holding two definitions of one KPI is a line with
		an unmarked join in it."""
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		first = self.tool_data(
			"refresh_kpi_cache", {"kpi_id": "gross_revenue", "company": MAIN, "back_years": 1}
		)
		forced = self.tool_data(
			"refresh_kpi_cache",
			{"kpi_id": "gross_revenue", "company": MAIN, "back_years": 1, "force": True},
		)
		self.assertGreater(forced["cleared"], 0)
		self.assertEqual(forced["written"], first["written"])

	def test_recompute_kpi_history_delegates_for_a_defined_kpi(self):
		"""A caller who already knows that tool does not have to learn this one."""
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		data = self.tool_data(
			"recompute_kpi_history", {"kpi_key": "gross_revenue", "company": MAIN, "back_years": 1}
		)
		self.assertTrue(data["defined_kpi"])
		self.assertGreater(data["written"], 0)

	def test_recompute_kpi_history_still_refuses_a_name_that_is_neither(self):
		message = self.tool_error("recompute_kpi_history", {"kpi_key": "not_a_thing"})
		self.assertIn("no registered report and no KPI definition", message)

	def test_the_scheduled_entry_point_takes_no_arguments_and_never_raises(self):
		"""It runs on somebody's scheduler beside their real work. A KPI
		framework that took the scheduler down would be worse than one that
		missed a night."""
		import inspect

		signature = inspect.signature(kpi_engine.refresh_all_kpi_caches)
		self.assertEqual(list(signature.parameters), [])
		self.assertIsInstance(kpi_engine.refresh_all_kpi_caches(), int)

	def test_the_overnight_job_honours_the_kill_switch_the_two_oclock_job_has(self):
		"""ONE SWITCH FOR BOTH JOBS. They cache the same doctype for the same
		reason, and a second checkbox called something almost identical is how a
		setting stops being read."""
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		self.configure(enabled=1, enable_kpi_history_sweep=0, **ON)
		self.assertEqual(kpi_engine.refresh_all_kpi_caches(), 0)

	def test_list_financial_kpi_history_names_the_definition_behind_a_series(self):
		"""0.42 is a catastrophe as a current ratio, a fine margin as a
		percentage and a rounding error as dollars, and before the framework the
		only place the unit lived was a Python constant."""
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue", unit="Currency")
		self.a_cached_snapshot("gross_revenue", 40000.0)
		data = self.tool_data("list_financial_kpi_history", {"kpi_key": "gross_revenue"})
		self.assertEqual(data["definitions"]["gross_revenue"]["title"], "Gross Revenue")
		self.assertEqual(data["definitions"]["gross_revenue"]["unit"], "Currency")


# ── 8 ───────────────────────────────────────────────────────────────────────
class TheGuards(KPIFrameworkTestCase):
	def test_every_tool_is_behind_the_kpi_role_gate(self):
		"""A KPI definition decides what number a lender is shown and what
		threshold raises an alert about it, which is the same class of authority
		as approving an add-back to operating cash flow."""
		set_roles("Administrator", ["Sales User"])
		for name, arguments in (
			("list_financial_kpi_definitions", {}),
			("create_financial_kpi_definition", {"kpi_id": "x", "title": "x"}),
			("compute_all_kpis", {"company": MAIN}),
		):
			with self.subTest(tool=name):
				message = self.tool_error(name, arguments)
				self.assertIn("Accounts Manager", message)

	def test_every_tool_is_behind_its_own_switch(self):
		"""The switch is not a security boundary for a read — anyone with the
		bearer token could read the same data through Frappe's own API. It is a
		SURFACE control, and every tool in this release has one."""
		off = {key: 0 for key in ON}
		self.configure(enabled=1, **off)
		for name, arguments in (
			("list_financial_kpi_definitions", {}),
			("get_financial_kpi_definition", {"kpi_id": "x"}),
			("compute_kpi", {"kpi_id": "x", "company": MAIN}),
			("compute_all_kpis", {"company": MAIN}),
			("create_financial_kpi_definition", {"kpi_id": "x", "title": "x"}),
			("update_financial_kpi_definition", {"kpi_id": "x"}),
			("refresh_kpi_cache", {}),
		):
			with self.subTest(tool=name):
				self.assertIn("is switched off on this site", self.tool_error(name, arguments).lower())

	def test_a_read_that_warms_a_cache_writes_the_cache_and_nothing_else(self):
		"""The one promise a read tool that writes anything has to make."""
		self.a_sale("2026-03-15", 40000)
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("compute_kpi", {"kpi_id": "gross_revenue", "company": MAIN, "as_of": AS_OF})
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		for doctype in ("MCP Action Log", "Financial KPI History"):
			before.pop(doctype, None)
			after.pop(doctype, None)
		self.assertEqual(before, after)

	def test_the_seeder_writes_sustainable_cf_per_acre_and_only_that(self):
		"""A seeded KPI is a claim that this app knows what an operation should
		watch, and it can only honestly make that claim about a metric it also
		ships the computer for."""
		report = kpi_engine.seed_kpi_definitions()
		self.assertEqual(report["created"], ["sustainable_cf_per_acre"])
		self.assertEqual(report["failed"], [])

	def test_the_seeded_definition_adopts_the_series_the_cache_already_uses(self):
		"""Every Financial KPI History row written since v0.19.6 carries
		`kpi_key = "sustainable_cf_per_acre"`, so the seeded definition adopts
		that series rather than starting a second one beside it."""
		kpi_engine.seed_kpi_definitions()
		row = kpi_engine.definition_row("sustainable_cf_per_acre")
		self.assertEqual(row["builtin_function"], "sustainable_cf_per_acre")
		self.assertEqual(row["kpi_id"], windows.COMPUTERS["sustainable_cf_per_acre"]["kpi_key"])

	def test_the_seeder_is_idempotent_and_leaves_an_operator_edit_alone(self):
		"""The difference between a seeder and a Frappe fixture, and the reason
		this app has never shipped one."""
		kpi_engine.seed_kpi_definitions()
		self.tool_data(
			"update_financial_kpi_definition",
			{"kpi_id": "sustainable_cf_per_acre", "threshold_warning_low": 750, "enabled": False},
		)
		again = kpi_engine.seed_kpi_definitions()
		self.assertEqual(again["created"], [])
		self.assertEqual(again["present"], ["sustainable_cf_per_acre"])
		row = kpi_engine.definition_row("sustainable_cf_per_acre")
		self.assertEqual(float(row["threshold_warning_low"]), 750.0)
		self.assertFalse(int(row["enabled"]))

	def test_the_seeder_sets_no_thresholds(self):
		"""A defensible floor under cash flow per acre is a number about one
		operation's own cost structure and debt service, and a seeded one would
		be a line somebody had not drawn being enforced on a calendar."""
		kpi_engine.seed_kpi_definitions()
		row = kpi_engine.definition_row("sustainable_cf_per_acre")
		self.assertFalse(kpi_engine.thresholds_of(row)["has_any"])

	def test_an_update_that_moves_the_arithmetic_says_so_with_the_cached_count(self):
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue", builtin_function="revenue")
		self.a_cached_snapshot("gross_revenue", 40000.0)
		data = self.tool_data(
			"update_financial_kpi_definition", {"kpi_id": "gross_revenue", "builtin_function": "ocf"}
		)
		self.assertIn("THE ARITHMETIC OF THIS KPI CHANGED", data["arithmetic_note"])
		self.assertIn("cached snapshot", data["arithmetic_note"])

	def test_an_update_carries_the_previous_values_for_the_audit_row(self):
		"""An auditor asking 'who changed this and what did it say before' reads
		the answer off this app rather than off a git history."""
		self.a_definition(kpi_id="gross_revenue", title="Gross Revenue")
		data = self.tool_data(
			"update_financial_kpi_definition", {"kpi_id": "gross_revenue", "title": "Total Revenue"}
		)
		self.assertEqual(data["previous"]["title"], "Gross Revenue")
		self.assertEqual(data["changed"]["title"]["now"], "Total Revenue")
		self.assertAudited("update_financial_kpi_definition")

	def test_expression_inputs_are_stored_as_validated_json(self):
		"""A dict from a model and a JSON string from somebody pasting out of the
		Desk must not be admitted on different terms."""
		self.a_definition(
			kpi_id="from_a_string",
			title="From a String",
			formula_type="Expression",
			expression="a + b",
			expression_inputs=json.dumps(
				{"a": {"source": "constant", "value": 1}, "b": {"source": "constant", "value": 2}}
			),
		)
		row = kpi_engine.definition_row("from_a_string")
		self.assertEqual(json.loads(row["expression_inputs"])["a"]["value"], 1.0)
