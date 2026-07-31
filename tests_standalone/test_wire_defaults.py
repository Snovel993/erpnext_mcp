# SPDX-License-Identifier: MIT
"""`bulk_wire_default_accounts` — company setup that finds the accounts itself.

WHAT THESE TESTS ARE ABOUT.

IT PROPOSES; `set_company_defaults`' RULES DISPOSE. The search is allowed to
suggest any leaf account, and the same type checks that guard a hand-written
default decide whether it is used. So the test that matters most is
`test_a_1310_that_is_not_a_receivable_is_not_used`: the number is right, the
account exists, and it is the wrong KIND of account — which is precisely the
mistake a numbers-only tool would make and then nobody would notice until a
quarter's ageing report stopped reconciling.

IT DOES NOT GUESS AND IT DOES NOT SULK. A field nothing matched is reported,
loudly, with what was looked for — and every other field is still wired. Both
halves are tested, because a tool that filled the gap with something plausible
and a tool that refused the whole call for one missing account are two different
ways of being useless.

THE FIXTURE IS ITS OWN. A dedicated company with a real "Standard with Numbers"
chart, rather than the shared one, because the point of the tool is what it does
with a chart's shape and each test wants a different shape.
"""

from .fixtures import MAIN, SeededTestCase
from .harness import STORE

ON = {"allow_bulk_wire_default_accounts": 1, "allow_set_company_defaults": 1}

WIRED = "Numbered Farm Co"
ABBR = "NFC"

#: (account_name, number, root_type, account_type, is_group, parent_name)
#: ERPNext's own "Standard with Numbers" shape, trimmed to what this tool looks
#: for. 1110 is a GROUP, as it is on the real template — which is what makes the
#: descent-into-the-sub-ledger path a real case rather than a hypothetical.
STANDARD_CHART = [
	("Application of Funds (Assets)", "", "Asset", "", 1, None),
	("Current Assets", "1100", "Asset", "", 1, "Application of Funds (Assets)"),
	("Bank Accounts", "1110", "Asset", "Bank", 1, "Current Assets"),
	("Operating Checking", "1111", "Asset", "Bank", 0, "Bank Accounts"),
	("Cash In Hand", "1140", "Asset", "Cash", 0, "Current Assets"),
	("Debtors", "1310", "Asset", "Receivable", 0, "Current Assets"),
	("Source of Funds (Liabilities)", "", "Liability", "", 1, None),
	("Creditors", "2110", "Liability", "Payable", 0, "Source of Funds (Liabilities)"),
	("Income", "", "Income", "", 1, None),
	("Sales", "4100", "Income", "Income Account", 0, "Income"),
	("Expenses", "", "Expense", "", 1, None),
	("Cost of Goods Sold", "5111", "Expense", "Cost of Goods Sold", 0, "Expenses"),
	("Office Rent", "5100", "Expense", "Expense Account", 0, "Expenses"),
	("Round Off", "5212", "Expense", "Round Off", 0, "Expenses"),
	("Write Off", "5218", "Expense", "", 0, "Expenses"),
]

EXPECTED = {
	"default_receivable_account": f"1310 - Debtors - {ABBR}",
	"default_payable_account": f"2110 - Creditors - {ABBR}",
	"default_cash_account": f"1140 - Cash In Hand - {ABBR}",
	"default_bank_account": f"1111 - Operating Checking - {ABBR}",
	"default_income_account": f"4100 - Sales - {ABBR}",
	"default_expense_account": f"5100 - Office Rent - {ABBR}",
	"cost_of_goods_sold_account": f"5111 - Cost of Goods Sold - {ABBR}",
	"round_off_account": f"5212 - Round Off - {ABBR}",
	"write_off_account": f"5218 - Write Off - {ABBR}",
	"round_off_cost_center": f"{WIRED} - {ABBR}",
}


def docname(account_name: str, chart=None) -> str:
	for name, number, *_rest in chart or STANDARD_CHART:
		if name == account_name:
			stem = f"{number} - {name}" if number else name
			return f"{stem} - {ABBR}"
	raise KeyError(account_name)


class WireTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)

	def seed_company(self, chart=None, cost_centers=True, chart_template="Standard with Numbers"):
		STORE.seed(
			"Company",
			[
				{
					"name": WIRED,
					"company_name": WIRED,
					"abbr": ABBR,
					"default_currency": "USD",
					"country": "United States",
					"chart_of_accounts": chart_template,
				}
			],
		)
		rows = []
		for index, (name, number, root_type, account_type, is_group, parent) in enumerate(
			chart or STANDARD_CHART, start=1
		):
			rows.append(
				{
					"name": docname(name, chart),
					"account_name": name,
					"account_number": number,
					"parent_account": docname(parent, chart) if parent else "",
					"is_group": is_group,
					"root_type": root_type,
					"account_type": account_type,
					"account_currency": "USD",
					"disabled": 0,
					"company": WIRED,
					"lft": index * 2,
					"rgt": index * 2 + 1,
				}
			)
		STORE.seed("Account", rows)
		if cost_centers:
			STORE.seed(
				"Cost Center",
				[
					{
						"name": f"{WIRED} - {ABBR}",
						"cost_center_name": WIRED,
						"cost_center_number": "",
						"parent_cost_center": "",
						"is_group": 0,
						"disabled": 0,
						"company": WIRED,
					}
				],
			)
		return WIRED

	def wire(self, **overrides):
		payload = {"company": WIRED}
		payload.update(overrides)
		return self.tool_data("bulk_wire_default_accounts", payload)

	def company_row(self):
		return STORE.get_raw("Company", WIRED)


class HappyPath(WireTestCase):
	def test_a_fresh_numbered_company_gets_every_default(self):
		self.seed_company()
		data = self.wire()
		self.assertEqual(data["unresolved"], {})
		self.assertEqual(data["defaults_now"], EXPECTED)
		row = self.company_row()
		for field, account in EXPECTED.items():
			with self.subTest(field=field):
				self.assertEqual(row[field], account)

	def test_it_reports_what_each_pick_was_picked_by(self):
		"""So a reader can tell 'the number said so' from 'it was the only Bank
		account on the chart'."""
		self.seed_company()
		picked = self.wire()["picked_by"]
		self.assertEqual(picked["default_receivable_account"], "account number 1310")
		self.assertEqual(picked["default_payable_account"], "account number 2110")
		self.assertEqual(picked["round_off_cost_center"], "the first leaf cost center")

	def test_a_number_that_names_a_group_descends_to_the_sub_ledger(self):
		"""1110 is 'Bank Accounts' on the real template — a group, which no
		document can post to. The default belongs on the account under it."""
		self.seed_company()
		data = self.wire()
		self.assertEqual(data["defaults_now"]["default_bank_account"], docname("Operating Checking"))
		self.assertEqual(data["picked_by"]["default_bank_account"], "a sub-ledger under 1110")

	def test_a_re_run_changes_nothing_and_says_so(self):
		self.seed_company()
		self.wire()
		data = self.wire()
		self.assertEqual(data["changed"], {})
		self.assertEqual(len(data["unchanged"]), len(EXPECTED))
		self.assertTrue(data["idempotent"])

	def test_it_reports_the_before_and_after_of_everything_it_moved(self):
		self.seed_company()
		data = self.wire()
		before, after = data["changed"]["default_income_account"]
		self.assertEqual(before, "")
		self.assertEqual(after, docname("Sales"))

	def test_it_touches_no_other_company(self):
		self.seed_company()
		before = dict(STORE.get_raw("Company", MAIN))
		self.wire()
		self.assertEqual(STORE.get_raw("Company", MAIN), before)


class RefusesToGuess(WireTestCase):
	def test_a_1310_that_is_not_a_receivable_is_not_used(self):
		"""The mistake a numbers-only tool makes. ERPNext keys party ledgers off
		`account_type`, so a default_receivable_account pointed at a plain Asset
		posts fine and stops ageing correctly a quarter later."""
		chart = [
			row if row[0] != "Debtors" else ("Marketable Securities", "1310", "Asset", "", 0, "Current Assets")
			for row in STANDARD_CHART
		]
		self.seed_company(chart=chart)
		data = self.wire()
		self.assertNotIn("default_receivable_account", data["defaults_now"])
		reason = data["unresolved"]["default_receivable_account"]
		self.assertIn("account number 1310", reason)
		self.assertIn("account_type Receivable", reason)
		self.assertIn("create_account", reason)
		self.assertIsNone(self.company_row().get("default_receivable_account"))

	def test_a_missing_income_account_is_reported_with_what_was_looked_for(self):
		"""'if 4100 does not exist' — and neither does any other income leaf."""
		chart = [row for row in STANDARD_CHART if row[0] not in ("Sales", "Income")]
		self.seed_company(chart=chart)
		data = self.wire()
		reason = data["unresolved"]["default_income_account"]
		self.assertIn("account number 4100", reason)
		self.assertIn("the first Income leaf", reason)
		self.assertIn("no Income leaf account", reason)

	def test_the_rest_are_still_wired_when_one_is_missing(self):
		"""A company with nine of ten defaults is better off than one with none."""
		chart = [row for row in STANDARD_CHART if row[0] not in ("Sales", "Income")]
		self.seed_company(chart=chart)
		data = self.wire()
		self.assertEqual(list(data["unresolved"]), ["default_income_account"])
		self.assertEqual(len(data["defaults_now"]), len(EXPECTED) - 1)
		self.assertEqual(self.company_row()["default_payable_account"], docname("Creditors"))

	def test_the_summary_leads_with_what_is_still_missing(self):
		chart = [row for row in STANDARD_CHART if row[0] not in ("Sales", "Income")]
		self.seed_company(chart=chart)
		result = self.tool("bulk_wire_default_accounts", {"company": WIRED})
		import json

		payload = json.loads(result["content"][0]["text"])
		self.assertIn("default_income_account", payload["next_step"])
		self.assertIn("get_chart_of_accounts", payload["next_step"])

	def test_strict_refuses_the_whole_call_and_writes_nothing(self):
		chart = [row for row in STANDARD_CHART if row[0] not in ("Sales", "Income")]
		self.seed_company(chart=chart)
		message = self.tool_error(
			"bulk_wire_default_accounts", {"company": WIRED, "strict": True}
		)
		self.assertIn("strict=true and 1 field(s) could not be resolved", message)
		self.assertIn("default_income_account", message)
		self.assertIsNone(self.company_row().get("default_payable_account"))

	def test_a_disabled_account_is_never_picked(self):
		chart = list(STANDARD_CHART)
		self.seed_company(chart=chart)
		STORE.get_raw("Account", docname("Cash In Hand"))["disabled"] = 1
		data = self.wire()
		self.assertIn("default_cash_account", data["unresolved"])

	def test_a_company_with_no_leaf_cost_center_says_so(self):
		self.seed_company(cost_centers=False)
		reason = self.wire()["unresolved"]["round_off_cost_center"]
		self.assertIn("no leaf cost center", reason)
		self.assertIn("create_cost_center", reason)


class Overrides(WireTestCase):
	def test_an_override_pins_a_different_account(self):
		chart = STANDARD_CHART + [
			("Payroll Checking", "1112", "Asset", "Bank", 0, "Bank Accounts"),
		]
		self.seed_company(chart=chart)
		data = self.wire(overrides={"default_bank_account": "1112"})
		self.assertEqual(data["defaults_now"]["default_bank_account"], docname("Payroll Checking", chart))
		self.assertEqual(data["picked_by"]["default_bank_account"], "override")

	def test_an_override_naming_a_group_is_a_hard_refusal(self):
		"""An explicit instruction that cannot be honoured is a different thing
		from a search that came up empty, strict or not."""
		self.seed_company()
		message = self.tool_error(
			"bulk_wire_default_accounts",
			{"company": WIRED, "overrides": {"default_bank_account": "1110"}},
		)
		self.assertIn("group account", message)
		self.assertIsNone(self.company_row().get("default_payable_account"))

	def test_an_override_naming_an_account_that_does_not_exist_is_refused(self):
		self.seed_company()
		self.assertIn(
			"9999",
			self.tool_error(
				"bulk_wire_default_accounts",
				{"company": WIRED, "overrides": {"default_cash_account": "9999"}},
			),
		)

	def test_an_override_for_a_field_this_tool_does_not_wire_is_refused_by_name(self):
		self.seed_company()
		message = self.tool_error(
			"bulk_wire_default_accounts",
			{"company": WIRED, "overrides": {"capital_work_in_progress_account": "1500"}},
		)
		self.assertIn("capital_work_in_progress_account", message)
		self.assertIn("set_company_defaults", message)

	def test_overrides_must_be_an_object(self):
		self.seed_company()
		self.assertIn(
			"must be an object",
			self.tool_error(
				"bulk_wire_default_accounts", {"company": WIRED, "overrides": "1310"}
			),
		)


class Strategies(WireTestCase):
	#: A chart somebody wrote by hand: right account types, numbers that mean
	#: nothing to ERPNext's template.
	CUSTOM_CHART = [
		("Assets", "", "Asset", "", 1, None),
		("Operating Bank", "A-10", "Asset", "Bank", 0, "Assets"),
		("Petty Cash", "A-20", "Asset", "Cash", 0, "Assets"),
		("Customer Balances", "A-30", "Asset", "Receivable", 0, "Assets"),
		("Liabilities", "", "Liability", "", 1, None),
		("Vendor Balances", "L-10", "Liability", "Payable", 0, "Liabilities"),
		("Revenue", "", "Income", "", 1, None),
		("Fruit Sales", "R-10", "Income", "Income Account", 0, "Revenue"),
		("Costs", "", "Expense", "", 1, None),
		("General Expense", "E-10", "Expense", "Expense Account", 0, "Costs"),
		("Rounding", "E-90", "Expense", "Round Off", 0, "Costs"),
	]

	def test_a_custom_chart_falls_back_to_account_type_matching(self):
		self.seed_company(chart=self.CUSTOM_CHART, chart_template="Farm Chart")
		data = self.wire()
		self.assertEqual(
			data["defaults_now"]["default_receivable_account"],
			docname("Customer Balances", self.CUSTOM_CHART),
		)
		self.assertEqual(
			data["picked_by"]["default_receivable_account"], "account_type Receivable"
		)

	def test_account_type_strategy_skips_the_numbers_entirely(self):
		self.seed_company()
		data = self.wire(strategy="account_type")
		self.assertEqual(data["strategy_used"], "account_type")
		self.assertEqual(data["picked_by"]["default_receivable_account"], "account_type Receivable")

	def test_auto_reads_the_companys_own_chart_template(self):
		self.seed_company()
		self.assertEqual(self.wire(strategy="auto")["strategy_used"], "standard_with_numbers")

	def test_auto_picks_account_type_for_an_unnumbered_template(self):
		self.seed_company(chart=self.CUSTOM_CHART, chart_template="Farm Chart")
		self.assertEqual(self.wire(strategy="auto")["strategy_used"], "account_type")

	def test_an_account_named_write_off_is_found_without_the_number(self):
		"""An account literally called 'Write Off' is evidence, not a guess."""
		chart = self.CUSTOM_CHART + [("Write Off", "E-95", "Expense", "", 0, "Costs")]
		self.seed_company(chart=chart, chart_template="Farm Chart")
		data = self.wire()
		self.assertEqual(data["defaults_now"]["write_off_account"], docname("Write Off", chart))
		self.assertIn("named like", data["picked_by"]["write_off_account"])

	def test_an_unknown_strategy_is_refused_with_the_known_ones(self):
		self.seed_company()
		message = self.tool_error(
			"bulk_wire_default_accounts", {"company": WIRED, "strategy": "vibes"}
		)
		self.assertIn("unknown strategy 'vibes'", message)
		self.assertIn("standard_with_numbers", message)
		self.assertIn("account_type", message)


class Determinism(WireTestCase):
	def test_two_candidates_of_the_same_type_pick_the_same_one_every_time(self):
		"""Otherwise 'idempotent' would be a lie the second time somebody called
		this, and the company's default would wander between runs."""
		chart = STANDARD_CHART + [
			("Second Checking", "1112", "Asset", "Bank", 0, "Bank Accounts"),
		]
		self.seed_company(chart=chart)
		first = self.wire(strategy="account_type")["defaults_now"]["default_bank_account"]
		second = self.wire(strategy="account_type")["defaults_now"]["default_bank_account"]
		self.assertEqual(first, second)
		self.assertEqual(first, docname("Operating Checking", chart))


class DryRun(WireTestCase):
	def test_it_reports_every_pick_and_writes_nothing(self):
		self.seed_company()
		data = self.wire(dry_run=True)
		self.assertTrue(data["dry_run"])
		self.assertEqual(data["defaults_now"], EXPECTED)
		self.assertEqual(len(data["changed"]), len(EXPECTED))
		self.assertIn("Nothing was written", data["note"])
		self.assertIsNone(self.company_row().get("default_receivable_account"))

	def test_a_dry_run_can_be_followed_by_the_real_thing(self):
		self.seed_company()
		self.wire(dry_run=True)
		self.wire()
		self.assertEqual(self.company_row()["default_receivable_account"], docname("Debtors"))

	def test_a_dry_run_still_reports_the_unresolved(self):
		chart = [row for row in STANDARD_CHART if row[0] not in ("Sales", "Income")]
		self.seed_company(chart=chart)
		self.assertIn("default_income_account", self.wire(dry_run=True)["unresolved"])


class SwitchAndShape(WireTestCase):
	def test_it_is_off_out_of_the_box(self):
		self.seed_company()
		self.configure(enabled=1)
		self.assertIn(
			"allow_bulk_wire_default_accounts",
			self.tool_error("bulk_wire_default_accounts", {"company": WIRED}),
		)

	def test_it_is_declared_mutating(self):
		from erpnext_mcp import registry

		self.assertIn("bulk_wire_default_accounts", registry.MUTATING_TOOLS)

	def test_it_returns_the_same_shape_as_set_company_defaults(self):
		"""So a caller can read either result the same way."""
		self.seed_company()
		wired = self.wire()
		manual = self.tool_data(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_cash_account": "1100"}},
		)
		for key in ("company", "changed", "unchanged", "defaults_now", "idempotent", "note"):
			with self.subTest(key=key):
				self.assertIn(key, wired)
				self.assertIn(key, manual)

	def test_a_company_that_does_not_exist_is_refused(self):
		self.assertIn(
			"Nowhere",
			self.tool_error("bulk_wire_default_accounts", {"company": "Nowhere Ltd"}),
		)

	def test_a_field_this_erpnext_lacks_is_reported_rather_than_skipped(self):
		"""A supported default an older ERPNext does not have is neither an error
		nor a silence: the caller is told their version has no such field, which is
		a different problem from 'no account matched'."""
		from erpnext_mcp.tools import dimensions

		from .harness import META

		self.seed_company()
		remaining = [f for f in META["Company"].fields if f.fieldname != "write_off_account"]
		META["Company"].fields = remaining
		# The double keeps a lookup beside the list; both have to move together.
		META["Company"]._by_name = {f.fieldname: f for f in remaining}
		data = self.wire()
		self.assertIn("write_off_account", data["fields_absent_on_this_erpnext"])
		self.assertNotIn("write_off_account", data["defaults_now"])
		self.assertIn("write_off_account", dimensions.WIRED_COMPANY_DEFAULTS)
