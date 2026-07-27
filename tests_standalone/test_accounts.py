# SPDX-License-Identifier: MIT
"""The chart-of-accounts tools.

Two things these tests are really about.

THE REFUSALS. Almost every tool in `tools/accounts.py` is mostly a list of
reasons not to do the thing. A parent that is a ledger, a root type that
disagrees with its parent, a number already in use, a cycle, an account the
current year is still posting through — each one has a test here, because a
validation that silently stops working is indistinguishable from one that was
never written.

THE DOCNAME. An Account's primary key is built from two of its own fields and is
never rebuilt afterwards, so a rename that moves one without the other leaves a
document that reports something different from what it is called. The double
models that faithfully (`harness.AccountDocument`), which is what lets these
tests assert on the actual key rather than on a field.
"""

from .fixtures import MAIN, MAIN_ABBR, OTHER, OTHER_ABBR, SeededTestCase
from .harness import META, STORE, Field, frappe

FRESH = "Fresh Orchard Co"
FRESH_ABBR = "FOC"

CURRENT_ASSETS = f"1000 - Current Assets - {MAIN_ABBR}"
CASH = f"1100 - Cash - {MAIN_ABBR}"
PAYABLES = f"2100 - Accounts Payable - {MAIN_ABBR}"
ASSET_ROOT = f"Application of Funds (Assets) - {MAIN_ABBR}"

ALL_ON = {
	"allow_create_account": 1,
	"allow_update_account": 1,
	"allow_move_account": 1,
	"allow_disable_account": 1,
	"allow_import_chart_of_accounts": 1,
}


class ChartToolsTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)


# ── create_account ──────────────────────────────────────────────────────────
class CreateAccount(ChartToolsTestCase):
	def create(self, **overrides):
		payload = {
			"company": MAIN,
			"account_number": "1150",
			"account_name": "Money Market",
			"root_type": "Asset",
			"parent_account": CURRENT_ASSETS,
		}
		payload.update(overrides)
		return payload

	def test_it_creates_a_ledger_account_named_the_way_erpnext_names_them(self):
		data = self.tool_data("create_account", self.create())
		self.assertEqual(data["name"], f"1150 - Money Market - {MAIN_ABBR}")
		self.assertEqual(data["parent_account"], CURRENT_ASSETS)
		self.assertEqual(data["root_type"], "Asset")
		self.assertFalse(data["is_group"])
		self.assertTrue(frappe.db.exists("Account", data["name"]))

	def test_the_report_type_follows_the_root_type(self):
		data = self.tool_data("create_account", self.create())
		self.assertEqual(data["report_type"], "Balance Sheet")

	def test_a_group_is_created_as_a_group(self):
		data = self.tool_data(
			"create_account",
			self.create(account_number="1160", account_name="Deposits", is_group=True),
		)
		self.assertTrue(data["is_group"])
		self.assertIn("cannot be posted to", data["next_step"])

	def test_is_group_accepts_the_string_a_model_will_send(self):
		data = self.tool_data(
			"create_account",
			self.create(account_number="1161", account_name="Deposits Two", is_group="true"),
		)
		self.assertTrue(data["is_group"])

	def test_is_group_false_as_a_string_is_false(self):
		"""`bool("false")` is True. This is the assertion that stops that reading."""
		data = self.tool_data(
			"create_account",
			self.create(account_number="1162", account_name="Deposits Three", is_group="false"),
		)
		self.assertFalse(data["is_group"])

	def test_a_ledger_parent_is_refused_with_the_reason(self):
		message = self.tool_error("create_account", self.create(parent_account=CASH))
		self.assertIn("not a group", message)
		self.assertFalse(frappe.db.exists("Account", f"1150 - Money Market - {MAIN_ABBR}"))

	def test_a_missing_parent_is_refused(self):
		message = self.tool_error("create_account", self.create(parent_account="Nowhere At All"))
		self.assertIn("no Account matching", message)

	def test_a_parent_in_another_company_is_refused(self):
		message = self.tool_error(
			"create_account", self.create(parent_account=f"1000 - Current Assets - {OTHER_ABBR}")
		)
		self.assertIn("belongs to company", message)

	def test_a_root_type_that_disagrees_with_the_parent_is_refused(self):
		message = self.tool_error("create_account", self.create(root_type="Liability"))
		self.assertIn("does not match", message)
		self.assertIn("root type", message)

	def test_an_invented_root_type_is_refused_with_the_five(self):
		message = self.tool_error("create_account", self.create(root_type="Assets"))
		self.assertIn("root_type must be one of", message)

	def test_a_number_already_in_use_is_refused_naming_the_holder(self):
		message = self.tool_error("create_account", self.create(account_number="1100"))
		self.assertIn(CASH, message)
		self.assertIn("Nothing was created", message)

	def test_the_same_number_in_another_company_is_fine(self):
		"""Account numbers are unique per company, not per site."""
		data = self.tool_data(
			"create_account",
			self.create(
				company=OTHER,
				account_number="1150",
				parent_account=f"1000 - Current Assets - {OTHER_ABBR}",
			),
		)
		self.assertEqual(data["company"], OTHER)

	def test_an_account_type_that_cannot_sit_under_this_root_is_refused(self):
		message = self.tool_error("create_account", self.create(account_type="Payable"))
		self.assertIn("belongs under root_type Liability", message)

	def test_an_account_type_this_site_does_not_have_is_refused_with_the_list(self):
		message = self.tool_error("create_account", self.create(account_type="Credit Card"))
		self.assertIn("no account_type 'Credit Card'", message)
		self.assertIn("Receivable", message)

	def test_a_valid_account_type_is_kept(self):
		data = self.tool_data("create_account", self.create(account_type="Bank"))
		self.assertEqual(data["account_type"], "Bank")

	def test_an_unknown_currency_is_refused(self):
		message = self.tool_error("create_account", self.create(account_currency="ZZZ"))
		self.assertIn("no Currency named", message)

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_account", self.create())
		self.assertIn("allow_create_account", message)

	def test_a_refusal_writes_an_audit_row_and_creates_nothing(self):
		before = len(STORE.rows("Account"))
		self.tool_error("create_account", self.create(parent_account=CASH))
		self.assertEqual(len(STORE.rows("Account")), before)
		self.assertAudited("create_account", "Error")


# ── update_account ──────────────────────────────────────────────────────────
class UpdateAccount(ChartToolsTestCase):
	def test_renaming_moves_the_docname_with_the_field(self):
		"""The quirk the whole module is built around: the docname encodes the
		name, and nothing rebuilds it on a later save."""
		data = self.tool_data("update_account", {"name": CASH, "new_account_name": "Cash on Hand"})
		self.assertEqual(data["name"], f"1100 - Cash on Hand - {MAIN_ABBR}")
		self.assertEqual(data["account_name"], "Cash on Hand")
		self.assertTrue(data["renamed"])
		self.assertFalse(frappe.db.exists("Account", CASH))

	def test_it_delegates_to_erpnexts_own_rename_helper(self):
		data = self.tool_data("update_account", {"name": CASH, "new_account_name": "Petty Cash"})
		self.assertEqual(data["rename_method"], "erpnext update_account_number")

	def test_renumbering_moves_the_docname_too(self):
		data = self.tool_data("update_account", {"name": CASH, "new_account_number": "1105"})
		self.assertEqual(data["name"], f"1105 - Cash - {MAIN_ABBR}")
		self.assertEqual(data["account_number"], "1105")

	def test_a_rename_repoints_the_children(self):
		data = self.tool_data(
			"update_account", {"name": CURRENT_ASSETS, "new_account_name": "Current Assets & Cash"}
		)
		self.assertEqual(frappe.db.get_value("Account", CASH, "parent_account"), data["name"])

	def test_renumbering_onto_a_used_number_is_refused(self):
		message = self.tool_error("update_account", {"name": CASH, "new_account_number": "1110"})
		self.assertIn("already used by", message)
		self.assertTrue(frappe.db.exists("Account", CASH))

	def test_changing_the_account_type_works_and_is_reported(self):
		data = self.tool_data("update_account", {"name": CASH, "new_account_type": "Bank"})
		self.assertEqual(data["account_type"], "Bank")
		self.assertEqual(data["changes"]["account_type"], ["Cash", "Bank"])
		self.assertFalse(data["renamed"])

	def test_an_account_type_wrong_for_the_root_is_refused(self):
		message = self.tool_error("update_account", {"name": CASH, "new_account_type": "Income Account"})
		self.assertIn("belongs under root_type Income", message)

	def test_crossing_the_payable_boundary_with_gl_entries_is_refused(self):
		"""ERPNext keys party balances off Receivable/Payable, so flipping it on
		an account with history silently unreconciles the ledger."""
		message = self.tool_error("update_account", {"name": CASH, "new_account_type": "Receivable"})
		self.assertIn("Receivable/Payable boundary", message)
		self.assertIn("GL Entry", message)

	def test_crossing_the_payable_boundary_is_allowed_with_no_history(self):
		self.tool_data(
			"create_account",
			{
				"company": MAIN,
				"account_number": "1210",
				"account_name": "Other Receivable",
				"root_type": "Asset",
				"parent_account": CURRENT_ASSETS,
			},
		)
		data = self.tool_data(
			"update_account",
			{"name": f"1210 - Other Receivable - {MAIN_ABBR}", "new_account_type": "Receivable"},
		)
		self.assertEqual(data["account_type"], "Receivable")

	def test_it_can_re_enable_a_disabled_account(self):
		self.tool_data("update_account", {"name": CASH, "disabled": True})
		data = self.tool_data("update_account", {"name": CASH, "disabled": False})
		self.assertFalse(data["disabled"])

	def test_it_cannot_reparent(self):
		"""The schema blocks the argument; this proves the tool would refuse it
		even if a client sent it anyway."""
		self.assertNotIn(
			"new_parent_account",
			self.registry_schema("update_account")["properties"],
		)

	def test_a_root_cannot_be_re_typed(self):
		message = self.tool_error("update_account", {"name": ASSET_ROOT, "new_account_type": "Bank"})
		self.assertIn("root account", message)
		self.assertIn("Root cannot be edited", message)

	def test_a_root_can_still_be_renamed(self):
		data = self.tool_data("update_account", {"name": ASSET_ROOT, "new_account_name": "Assets"})
		self.assertEqual(data["name"], f"Assets - {MAIN_ABBR}")

	def test_asking_for_nothing_is_refused(self):
		message = self.tool_error("update_account", {"name": CASH})
		self.assertIn("nothing to change", message)

	def test_asking_for_the_value_it_already_has_is_refused(self):
		message = self.tool_error("update_account", {"name": CASH, "new_account_type": "Cash"})
		self.assertIn("already has those values", message)

	def registry_schema(self, tool):
		from erpnext_mcp import registry

		return registry.TOOLS[tool]["inputSchema"]


class RenameFallback(ChartToolsTestCase):
	"""The path for an ERPNext too old to have `update_account_number`."""

	def setUp(self):
		super().setUp()
		import sys
		import types

		path = "erpnext.accounts.doctype.account.account"
		self.saved = sys.modules[path]
		sys.modules[path] = types.ModuleType(path)
		self.addCleanup(sys.modules.__setitem__, path, self.saved)

	def test_it_still_moves_both_the_field_and_the_docname(self):
		data = self.tool_data("update_account", {"name": CASH, "new_account_name": "Till"})
		self.assertEqual(data["name"], f"1100 - Till - {MAIN_ABBR}")
		self.assertEqual(data["account_name"], "Till")
		self.assertIn("legacy ERPNext", data["rename_method"])

	def test_the_fallback_also_repoints_children(self):
		data = self.tool_data("update_account", {"name": CURRENT_ASSETS, "new_account_number": "1001"})
		self.assertEqual(frappe.db.get_value("Account", CASH, "parent_account"), data["name"])


# ── move_account ────────────────────────────────────────────────────────────
class MoveAccount(ChartToolsTestCase):
	def setUp(self):
		super().setUp()
		self.tool_data(
			"create_account",
			{
				"company": MAIN,
				"account_number": "1500",
				"account_name": "Other Current Assets",
				"root_type": "Asset",
				"parent_account": CURRENT_ASSETS,
				"is_group": True,
			},
		)
		self.other_current = f"1500 - Other Current Assets - {MAIN_ABBR}"

	def test_it_reparents_and_leaves_everything_else_alone(self):
		data = self.tool_data("move_account", {"name": CASH, "new_parent_account": self.other_current})
		self.assertEqual(data["name"], CASH)
		self.assertEqual(data["parent_account"], self.other_current)
		self.assertEqual(data["previous_parent_account"], CURRENT_ASSETS)
		self.assertEqual(data["account_type"], "Cash")

	def test_it_says_what_reparenting_actually_does_to_reports(self):
		data = self.tool_data("move_account", {"name": CASH, "new_parent_account": self.other_current})
		self.assertIn("does not move a single GL Entry", data["note"])
		self.assertEqual(data["gl_entries_on_this_account"], 3)

	def test_a_ledger_target_is_refused(self):
		message = self.tool_error("move_account", {"name": CASH, "new_parent_account": PAYABLES})
		self.assertIn("not a group", message)

	def test_a_different_root_type_is_refused(self):
		message = self.tool_error(
			"move_account",
			{"name": CASH, "new_parent_account": f"Source of Funds (Liabilities) - {MAIN_ABBR}"},
		)
		self.assertIn("does not match", message)

	def test_moving_a_group_under_its_own_child_is_refused_as_a_cycle(self):
		message = self.tool_error(
			"move_account", {"name": CURRENT_ASSETS, "new_parent_account": self.other_current}
		)
		self.assertIn("cycle", message)
		self.assertEqual(frappe.db.get_value("Account", CURRENT_ASSETS, "parent_account"), ASSET_ROOT)

	def test_moving_somewhere_it_already_is_is_refused(self):
		message = self.tool_error("move_account", {"name": CASH, "new_parent_account": CURRENT_ASSETS})
		self.assertIn("already under", message)

	def test_a_root_cannot_be_moved(self):
		message = self.tool_error("move_account", {"name": ASSET_ROOT, "new_parent_account": CURRENT_ASSETS})
		self.assertIn("root account", message)

	def test_a_target_in_another_company_is_refused(self):
		message = self.tool_error(
			"move_account",
			{"name": CASH, "new_parent_account": f"1000 - Current Assets - {OTHER_ABBR}"},
		)
		self.assertIn("belongs to company", message)

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("move_account", {"name": CASH, "new_parent_account": self.other_current})
		self.assertIn("allow_move_account", message)


# ── disable_account ─────────────────────────────────────────────────────────
class DisableAccount(ChartToolsTestCase):
	def test_an_account_with_entries_this_fiscal_year_is_refused(self):
		"""The fixture's Cash posts through 2026, and today is in 2026."""
		message = self.tool_error("disable_account", {"name": CASH, "reason": "tidying up"})
		self.assertIn("fiscal year 2026", message)
		self.assertIn("GL Entry", message)
		self.assertFalse(frappe.db.get_value("Account", CASH, "disabled"))

	def test_a_quiet_account_is_disabled(self):
		data = self.tool_data("disable_account", {"name": PAYABLES, "reason": "never used on this site"})
		self.assertTrue(data["disabled"])
		self.assertEqual(data["gl_entries_in_window"], 0)
		self.assertIn("fiscal year 2026", data["checked_window"])
		self.assertTrue(frappe.db.get_value("Account", PAYABLES, "disabled"))

	def test_nothing_is_deleted(self):
		data = self.tool_data("disable_account", {"name": PAYABLES, "reason": "unused"})
		self.assertIn("Nothing was deleted", data["note"])
		self.assertTrue(frappe.db.exists("Account", PAYABLES))

	def test_the_reason_lands_on_the_document(self):
		self.tool_data("disable_account", {"name": PAYABLES, "reason": "closed the supplier"})
		self.assertTrue(any("closed the supplier" in comment.get("text", "") for comment in STORE.comments))

	def test_a_placeholder_reason_is_refused(self):
		message = self.tool_error("disable_account", {"name": PAYABLES, "reason": "x"})
		self.assertIn("real explanation", message)

	def test_disabling_twice_is_refused(self):
		self.tool_data("disable_account", {"name": PAYABLES, "reason": "unused"})
		message = self.tool_error("disable_account", {"name": PAYABLES, "reason": "unused again"})
		self.assertIn("already disabled", message)

	def test_a_root_cannot_be_disabled(self):
		message = self.tool_error("disable_account", {"name": ASSET_ROOT, "reason": "not wanted"})
		self.assertIn("root account", message)

	def test_a_group_reports_the_children_it_did_not_touch(self):
		"""The group itself has no postings, so it can be hidden — but its
		children stay postable, and saying so is the point."""
		data = self.tool_data(
			"disable_account", {"name": CURRENT_ASSETS, "reason": "restructuring the chart"}
		)
		self.assertEqual(data["child_accounts"], 3)
		self.assertIn("are NOT disabled", data["note"])
		self.assertFalse(frappe.db.get_value("Account", CASH, "disabled"))

	def test_entries_outside_the_current_year_do_not_block_it(self):
		"""Prior-year history is exactly what a soft delete is for."""
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "GLE-OLD-1",
					"account": PAYABLES,
					"posting_date": "2025-06-01",
					"debit": 0,
					"credit": 10,
					"company": MAIN,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "ACC-JV-2025-00009",
				}
			],
		)
		data = self.tool_data("disable_account", {"name": PAYABLES, "reason": "supplier gone"})
		self.assertEqual(data["gl_entries_in_window"], 0)
		self.assertEqual(data["gl_entries_all_time"], 1)

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("disable_account", {"name": PAYABLES, "reason": "unused"})
		self.assertIn("allow_disable_account", message)


# ── propose_clean_chart ─────────────────────────────────────────────────────
class ProposeCleanChart(SeededTestCase):
	def test_the_default_template_is_the_farm_llc(self):
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		self.assertEqual(data["template"], "us_llc_farm")
		self.assertEqual(data["entity_type"], "LLC")

	def test_it_returns_the_shape_the_importer_takes(self):
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		roots = {node["account_number"] for node in data["accounts"]}
		self.assertEqual(roots, {"1000", "2000", "3000", "4000", "5000", "6000", "7000"})
		for node in data["accounts"]:
			self.assertIn("root_type", node)
			self.assertTrue(node["is_group"])

	def test_the_counts_add_up(self):
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		self.assertEqual(data["total_accounts"], data["group_accounts"] + data["ledger_accounts"])
		flat = []

		def collect(nodes):
			for node in nodes:
				flat.append(node)
				collect(node.get("children") or [])

		collect(data["accounts"])
		self.assertEqual(len(flat), data["total_accounts"])

	def test_the_live_wage_account_is_named_and_explained(self):
		"""2120 exists to be a moment-to-moment balance. The description is the
		only thing stopping the next person booking a month-end accrual into it,
		so it is part of the deliverable, not commentary."""
		node = self.find(self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"], "2120")
		self.assertEqual(node["account_name"], "Current Pay Period - Due to Employees")
		self.assertEqual(node["account_type"], "Payable")
		self.assertIn("NOT A PERIOD-END ACCRUAL", node["description"])
		self.assertIn("continuously", node["description"])
		self.assertIn("flushes to zero", node["description"])

	def test_an_account_type_this_site_lacks_is_swapped_and_reported(self):
		"""2160 asks for 'Credit Card', which ERPNext v15 does not offer."""
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		node = self.find(data["accounts"], "2160")
		self.assertEqual(node["account_type"], "Payable")
		swap = next(row for row in data["account_type_adjustments"] if row["account_number"] == "2160")
		self.assertEqual(swap["requested"], "Credit Card")
		self.assertEqual(swap["used"], "Payable")

	def test_the_proposal_is_importable_as_returned(self):
		"""Everything it proposes has to survive the importer's own validation —
		otherwise the review step produces a plan that cannot be run."""
		from erpnext_mcp import charts

		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		charts.validate_tree(data["accounts"])

	def test_the_optional_list_is_present_and_empty_for_this_template(self):
		"""The mechanism still works — the shipped template just does not use it.
		Every line in `us_llc_farm` earns its place, which is the point of a
		compact chart; a template that ships fifty maybes is back to being long."""
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		self.assertEqual(data["optional_accounts"], [])

	def test_an_optional_account_is_reported_when_a_template_has_one(self):
		"""The path the shipped template leaves untested."""
		from erpnext_mcp import charts

		template = charts.get("us_llc_farm")
		leaf = template.tree[0]["children"][0]["children"][0]
		leaf["optional"] = True
		self.addCleanup(leaf.pop, "optional", None)
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		self.assertEqual(
			data["optional_accounts"],
			[{"account_number": leaf["account_number"], "account_name": leaf["account_name"]}],
		)

	#: The trading segment, as a filter an operator can actually type into a
	#: report. Inclusive on both ends.
	TRADING_RANGES = ((1800, 1849), (3500, 3500), (4200, 4249), (7300, 7339))

	#: Accounts outside those ranges that legitimately carry trading vocabulary.
	#: Kept as an explicit map so a new exemption has to be argued for in a diff
	#: rather than appearing because somebody widened a regex.
	TRADING_WORD_EXEMPT = (("1130", "Bank Bridge working account for paired transactions"),)

	def test_the_trading_segment_is_reportable_as_one_range_set(self):
		"""The whole reason the investment book has its own numbers: filter a
		report to these ranges and you have the trading business, exclude them and
		you have the farm. A farm account that wandered into 18xx, 42xx or the
		7300s would quietly break that, and nothing else would notice."""
		flat = self.flatten(self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"])
		trading = {n for n in flat if any(low <= int(n) <= high for low, high in self.TRADING_RANGES)}
		self.assertEqual(
			trading,
			{
				"1800",
				"1810",
				"1815",
				"1820",
				"1830",
				"1840",
				"3500",
				"4200",
				"4210",
				"4220",
				"4230",
				"4240",
				"7300",
				"7310",
				"7320",
				"7330",
			},
		)

	def test_nothing_outside_the_ranges_reads_as_trading(self):
		"""The half that rots. Ranges stay right on their own; what drifts is
		somebody adding `6950 Brokerage Fees` two years from now and the filter
		silently starting to miss things."""
		flat = self.flatten(self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"])
		trading = {n for n in flat if any(low <= int(n) <= high for low, high in self.TRADING_RANGES)}
		for number, name in flat.items():
			if number in trading or number in dict(self.TRADING_WORD_EXEMPT):
				continue
			lowered = name.lower()
			for word in ("securities", "brokerage", "custodian", "options", "capital gain", "capital loss"):
				self.assertNotIn(
					word,
					lowered,
					f"{number} {name} reads as trading but sits outside the segment ranges",
				)

	def test_the_exempt_accounts_are_the_ones_we_think_they_are(self):
		"""An exemption that outlives the account it was written for is a hole in
		the check above."""
		flat = self.flatten(self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"])
		exempt = set(dict(self.TRADING_WORD_EXEMPT))
		self.assertEqual(exempt & set(flat), exempt)
		self.assertEqual(flat["1130"], "Cash Clearing - Brokerage")

	def test_the_clearing_account_says_it_is_not_part_of_the_segment(self):
		"""1130 is the one account a reader could reasonably mistake for a
		trading account, so it has to say out loud that it is not one."""
		node = self.find(self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"], "1130")
		self.assertEqual(node["account_type"], "Current Asset")
		self.assertIn("NOT part of the", node["description"])
		self.assertIn("should sit", node["description"])

	def test_the_loss_accounts_are_split_for_tax_treatment(self):
		"""7300 and 7310 exist apart because loss harvesting has to separate them
		anyway; a combined account just moves the work downstream."""
		accounts = self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"]
		equity_losses = self.find(accounts, "7300")
		options_losses = self.find(accounts, "7310")
		self.assertEqual(equity_losses["account_name"], "Realized Capital Losses")
		self.assertEqual(options_losses["account_name"], "Options Losses")
		self.assertIn("7310", equity_losses["description"])
		self.assertIn("1256", options_losses["description"])
		# The open-options account points at the right one of the two.
		self.assertIn("7310, not 7300", self.find(accounts, "1840")["description"])

	def test_the_brokerage_cash_group_ships_empty(self):
		"""One child per linked brokerage account, and which accounts exist is a
		property of the install rather than of the template. Shipping defaults
		here would mean every operator deleting somebody else's account numbers."""
		node = self.find(self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"], "1830")
		self.assertTrue(node["is_group"])
		self.assertEqual(node.get("children"), [])
		self.assertNotIn("account_type", node)
		self.assertIn("ONE CHILD PER LINKED BROKERAGE", node["description"])
		self.assertIn("create_account", node["description"])

	def test_the_operator_is_told_the_empty_group_needs_filling(self):
		"""An empty group that nobody knows to fill is just a hole in the chart."""
		notes = " ".join(self.tool_data("propose_clean_chart", {"company": MAIN})["notes"])
		self.assertIn("1830", notes)
		self.assertIn("EMPTY GROUP", notes)

	def test_the_trading_costs_are_inside_the_segment_not_with_the_farm(self):
		"""Filtering the segment has to show what the book costs to run, not only
		what it earned — otherwise the answer flatters itself."""
		accounts = self.tool_data("propose_clean_chart", {"company": MAIN})["accounts"]
		for number in ("7320", "7330"):
			node = self.find(accounts, number)
			self.assertIsNotNone(node, number)
			self.assertEqual(node["account_type"], "Expense Account")
		self.assertIn("7320", self.find(accounts, "6400")["description"])

	def flatten(self, nodes, out=None):
		out = {} if out is None else out
		for node in nodes:
			out[node["account_number"]] = node["account_name"]
			self.flatten(node.get("children") or [], out)
		return out

	def test_the_tax_accounts_land_on_the_right_side(self):
		"""Three things people mix up: the accrued obligation, the expense, and
		the money withheld from somebody else."""
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		flat = {}

		def collect(nodes, root_type=""):
			for node in nodes:
				flat[node["account_number"]] = (
					node["account_name"],
					node.get("root_type") or root_type,
					node,
				)
				collect(node.get("children") or [], node.get("root_type") or root_type)

		collect(data["accounts"])
		self.assertEqual(flat["2170"][1], "Liability")
		self.assertEqual(flat["6650"][1], "Expense")
		self.assertEqual(flat["1420"][1], "Asset")
		self.assertEqual(flat["6150"][1], "Expense")
		self.assertEqual(flat["2141"][1], "Liability")
		# The prepaid amortises into the expense, and both say so.
		self.assertIn("6650", flat["1420"][2]["description"])
		# Employer share is not the withheld share, and the account says which.
		self.assertIn("2140", flat["6150"][2]["description"])
		self.assertIn("6150", flat["2140"][2]["description"])

	def test_it_warns_that_it_adds_roots_rather_than_replacing_them(self):
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		self.assertTrue(data["existing_root_accounts"])
		self.assertIn("adds a second set of roots", data["warning"])

	def test_the_warning_leads_with_the_collision_that_would_block_the_import(self):
		"""Near-certain on any company built from a bundled ERPNext chart, and it
		is the one that stops the import dead rather than merely making a mess."""
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		self.assertTrue(data["account_numbers_already_in_use"])
		self.assertIn("unique per company", data["warning"])
		self.assertIn("update_account", data["warning"])
		self.assertLess(
			data["warning"].index("unique per company"),
			data["warning"].index("adds a second set of roots"),
		)

	def test_a_company_with_no_collisions_gets_no_warning(self):
		STORE.seed(
			"Company",
			[{"name": "Blank Co", "abbr": "BCO", "default_currency": "USD", "is_group": 0}],
		)
		data = self.tool_data("propose_clean_chart", {"company": "Blank Co"})
		self.assertEqual(data["warning"], "")

	def test_it_flags_numbers_already_in_use(self):
		"""The fixture's 1100 Cash collides with the template's 1100 group."""
		data = self.tool_data("propose_clean_chart", {"company": MAIN})
		numbers = {row["account_number"] for row in data["account_numbers_already_in_use"]}
		self.assertIn("1100", numbers)

	def test_an_unknown_template_is_refused_with_the_list(self):
		message = self.tool_error("propose_clean_chart", {"company": MAIN, "template": "uk_ltd"})
		self.assertIn("us_llc_farm", message)

	def test_it_writes_nothing(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("propose_clean_chart", {"company": MAIN})
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		after.pop("MCP Action Log", None)
		before.pop("MCP Action Log", None)
		self.assertEqual(before, after)

	def test_it_is_a_read_tool_and_on_by_default(self):
		from erpnext_mcp import registry

		self.assertIn("propose_clean_chart", registry.READ_TOOLS)
		self.assertTrue(self.tool_data("propose_clean_chart", {"company": MAIN})["read_only"])

	def find(self, nodes, number):
		for node in nodes:
			if node.get("account_number") == number:
				return node
			found = self.find(node.get("children") or [], number)
			if found:
				return found
		return None


# ── import_chart_of_accounts ────────────────────────────────────────────────
SMALL = [
	{
		"account_number": "9000",
		"account_name": "Test Root",
		"root_type": "Expense",
		"is_group": True,
		"children": [
			{
				"account_number": "9100",
				"account_name": "Test Group",
				"is_group": True,
				"children": [
					{
						"account_number": "9110",
						"account_name": "Test Leaf",
						"account_type": "Expense Account",
					},
					{"account_number": "9120", "account_name": "Second Leaf"},
				],
			}
		],
	}
]


class ImportDryRun(ChartToolsTestCase):
	def test_dry_run_is_the_default(self):
		"""The load-bearing default. An accidental call must not rearrange a
		live chart of accounts."""
		data = self.tool_data("import_chart_of_accounts", {"company": MAIN, "accounts_json": SMALL})
		self.assertTrue(data["dry_run"])
		self.assertFalse(frappe.db.exists("Account", f"9110 - Test Leaf - {MAIN_ABBR}"))

	def test_the_plan_is_ordered_parents_first(self):
		data = self.tool_data("import_chart_of_accounts", {"company": MAIN, "accounts_json": SMALL})
		numbers = [row["account_number"] for row in data["accounts"]]
		self.assertEqual(numbers, ["9000", "9100", "9110", "9120"])
		self.assertEqual([row["depth"] for row in data["accounts"]], [0, 1, 2, 2])

	def test_the_plan_predicts_the_docnames(self):
		data = self.tool_data("import_chart_of_accounts", {"company": MAIN, "accounts_json": SMALL})
		leaf = next(row for row in data["accounts"] if row["account_number"] == "9110")
		self.assertEqual(leaf["docname"], f"9110 - Test Leaf - {MAIN_ABBR}")
		self.assertEqual(leaf["parent_account"], f"9100 - Test Group - {MAIN_ABBR}")

	def test_the_root_type_is_inherited_down_the_tree(self):
		data = self.tool_data("import_chart_of_accounts", {"company": MAIN, "accounts_json": SMALL})
		self.assertEqual({row["root_type"] for row in data["accounts"]}, {"Expense"})

	def test_a_json_string_is_accepted(self):
		import json

		data = self.tool_data(
			"import_chart_of_accounts",
			{"company": MAIN, "accounts_json": json.dumps(SMALL)},
		)
		self.assertEqual(data["total_accounts"], 4)

	def test_the_whole_propose_response_can_be_passed_straight_back(self):
		proposal = self.tool_data("propose_clean_chart", {"company": OTHER})
		data = self.tool_data("import_chart_of_accounts", {"company": OTHER, "accounts_json": proposal})
		self.assertEqual(data["total_accounts"], proposal["total_accounts"])

	def test_it_says_what_to_do_next(self):
		data = self.tool_data("import_chart_of_accounts", {"company": MAIN, "accounts_json": SMALL})
		self.assertIn("dry_run=false", data["next_step"])


class ImportValidation(ChartToolsTestCase):
	def plan(self, tree, **kwargs):
		return self.tool_error("import_chart_of_accounts", {"company": MAIN, "accounts_json": tree, **kwargs})

	def test_a_root_without_a_root_type_is_refused(self):
		message = self.plan([{"account_number": "9000", "account_name": "X", "is_group": True}])
		self.assertIn("needs root_type", message)

	def test_a_root_that_is_not_a_group_is_refused(self):
		message = self.plan([{"account_number": "9000", "account_name": "X", "root_type": "Asset"}])
		self.assertIn("must be a group", message)

	def test_children_under_a_leaf_are_refused(self):
		message = self.plan(
			[
				{
					"account_number": "9000",
					"account_name": "X",
					"root_type": "Asset",
					"is_group": True,
					"children": [
						{
							"account_number": "9100",
							"account_name": "Y",
							"children": [{"account_number": "9110", "account_name": "Z"}],
						}
					],
				}
			]
		)
		self.assertIn("is_group is not set", message)

	def test_a_duplicate_number_inside_the_chart_is_refused(self):
		message = self.plan(
			[
				{
					"account_number": "9000",
					"account_name": "X",
					"root_type": "Asset",
					"is_group": True,
					"children": [
						{"account_number": "9000", "account_name": "Y"},
					],
				}
			]
		)
		self.assertIn("appears twice", message)

	def test_a_subtree_that_switches_root_type_is_refused(self):
		message = self.plan(
			[
				{
					"account_number": "9000",
					"account_name": "X",
					"root_type": "Asset",
					"is_group": True,
					"children": [
						{"account_number": "9100", "account_name": "Y", "root_type": "Income"},
					],
				}
			]
		)
		self.assertIn("share one root type", message)

	def test_an_unknown_node_field_is_rejected_by_name(self):
		message = self.plan(
			[
				{
					"account_number": "9000",
					"account_name": "X",
					"root_type": "Asset",
					"is_group": True,
					"type": "Bank",
				}
			]
		)
		self.assertIn("unsupported field(s): type", message)

	def test_bad_json_says_so(self):
		message = self.plan("{not json")
		self.assertIn("not valid JSON", message)

	def test_an_empty_chart_is_refused(self):
		message = self.plan([])
		self.assertIn("non-empty list", message)

	def test_an_oversized_chart_is_refused_rather_than_half_run(self):
		from erpnext_mcp import charts

		children = [
			{"account_number": f"9{index:03d}", "account_name": f"Leaf {index}"}
			for index in range(charts.MAX_ACCOUNTS + 5)
		]
		message = self.plan(
			[
				{
					"account_number": "8000",
					"account_name": "Big",
					"root_type": "Expense",
					"is_group": True,
					"children": children,
				}
			]
		)
		self.assertIn("per-import limit", message)


class ImportConflicts(ChartToolsTestCase):
	def graft(self, children):
		return [
			{
				"account_number": "1000",
				"account_name": "Current Assets",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": ASSET_ROOT,
				"children": children,
			}
		]

	def test_an_identical_existing_account_is_skipped_so_reruns_are_safe(self):
		data = self.tool_data(
			"import_chart_of_accounts",
			{
				"company": MAIN,
				"accounts_json": self.graft([{"account_number": "1100", "account_name": "Cash"}]),
			},
		)
		actions = {row["account_number"]: row["action"] for row in data["accounts"]}
		self.assertEqual(actions["1000"], "skip")
		self.assertEqual(actions["1100"], "skip")

	def test_a_number_held_by_a_different_name_is_an_error_not_a_skip(self):
		"""Silently skipping here is how a reviewed chart ends up meaning
		something else."""
		data = self.tool_data(
			"import_chart_of_accounts",
			{
				"company": MAIN,
				"accounts_json": self.graft([{"account_number": "1100", "account_name": "Petty Cash"}]),
			},
		)
		row = next(r for r in data["accounts"] if r["account_number"] == "1100")
		self.assertEqual(row["action"], "error")
		self.assertIn("already used by", row["note"])

	def test_a_group_ledger_mismatch_is_an_error(self):
		data = self.tool_data(
			"import_chart_of_accounts",
			{
				"company": MAIN,
				"accounts_json": self.graft(
					[{"account_number": "1100", "account_name": "Cash", "is_group": True, "children": []}]
				),
			},
		)
		row = next(r for r in data["accounts"] if r["account_number"] == "1100")
		self.assertEqual(row["action"], "error")
		self.assertIn("ledger account", row["note"])

	def test_the_plan_separates_the_causes_from_the_fallout(self):
		"""One bad group takes its whole subtree with it. A caller staring at
		seventy error rows needs to know which five of them to fix."""
		tree = [
			{
				"account_number": "9000",
				"account_name": "X",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": CASH,
				"children": [
					{
						"account_number": "9100",
						"account_name": "Y",
						"is_group": True,
						"children": [
							{"account_number": "9110", "account_name": "Z"},
						],
					},
				],
			}
		]
		data = self.tool_data("import_chart_of_accounts", {"company": MAIN, "accounts_json": tree})
		self.assertEqual(data["counts"]["error"], 3)
		self.assertEqual(len(data["blocking_problems"]), 1)
		self.assertEqual(data["blocking_problems"][0]["account_number"], "9000")
		self.assertIn("blocking 3 of 3", data["next_step"])

	def test_children_of_a_broken_node_are_not_planned_as_creatable(self):
		tree = [
			{
				"account_number": "9000",
				"account_name": "X",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": CASH,
				"children": [{"account_number": "9100", "account_name": "Y"}],
			}
		]
		data = self.tool_data("import_chart_of_accounts", {"company": MAIN, "accounts_json": tree})
		self.assertEqual([row["action"] for row in data["accounts"]], ["error", "error"])
		self.assertIn("cannot be created", data["accounts"][1]["note"])

	def test_a_real_run_refuses_while_any_row_is_an_error(self):
		message = self.tool_error(
			"import_chart_of_accounts",
			{
				"company": MAIN,
				"accounts_json": self.graft([{"account_number": "1100", "account_name": "Petty Cash"}]),
				"dry_run": False,
			},
		)
		self.assertIn("block 1 account(s) from being created", message)
		self.assertIn("Nothing was created", message)


class ImportExecute(ChartToolsTestCase):
	def run_import(self, tree=None, company=MAIN):
		return self.tool_data(
			"import_chart_of_accounts",
			{"company": company, "accounts_json": tree or SMALL, "dry_run": False},
		)

	def test_it_creates_the_whole_tree(self):
		data = self.run_import()
		self.assertEqual(data["counts"]["created"], 4)
		self.assertTrue(frappe.db.exists("Account", f"9110 - Test Leaf - {MAIN_ABBR}"))

	def test_children_hang_off_the_real_parent_docnames(self):
		self.run_import()
		self.assertEqual(
			frappe.db.get_value("Account", f"9110 - Test Leaf - {MAIN_ABBR}", "parent_account"),
			f"9100 - Test Group - {MAIN_ABBR}",
		)

	def test_the_new_root_is_a_root(self):
		self.run_import()
		self.assertEqual(
			frappe.db.get_value("Account", f"9000 - Test Root - {MAIN_ABBR}", "parent_account"), None
		)

	def test_running_it_twice_creates_nothing_the_second_time(self):
		self.run_import()
		message = self.tool_error(
			"import_chart_of_accounts",
			{"company": MAIN, "accounts_json": SMALL, "dry_run": False},
		)
		self.assertIn("already exists", message)
		self.assertEqual(frappe.db.count("Account", {"account_number": "9110"}), 1)

	def test_a_subtree_can_be_grafted_onto_an_existing_parent(self):
		tree = [
			{
				"account_number": "1600",
				"account_name": "Deposits",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": CURRENT_ASSETS,
				"children": [{"account_number": "1610", "account_name": "Utility Deposits"}],
			}
		]
		self.run_import(tree)
		self.assertEqual(
			frappe.db.get_value("Account", f"1600 - Deposits - {MAIN_ABBR}", "parent_account"),
			CURRENT_ASSETS,
		)

	def test_an_unknown_currency_is_caught_before_anything_is_written(self):
		tree = [
			{
				"account_number": "9000",
				"account_name": "Test Root",
				"root_type": "Expense",
				"is_group": True,
				"children": [{"account_number": "9200", "account_name": "Boom", "account_currency": "ZZZ"}],
			}
		]
		message = self.tool_error(
			"import_chart_of_accounts", {"company": MAIN, "accounts_json": tree, "dry_run": False}
		)
		self.assertIn("no Currency named 'ZZZ'", message)
		self.assertFalse(frappe.db.exists("Account", f"9000 - Test Root - {MAIN_ABBR}"))

	def test_a_failure_part_way_rolls_the_whole_import_back(self):
		"""A half-built chart has orphaned groups in it; there is no useful
		partial state to keep. Driven by making the third insert blow up, which
		is the shape of any validation this app did not anticipate."""
		real_new_doc = frappe.new_doc
		calls = []

		def exploding_new_doc(doctype):
			calls.append(doctype)
			if len(calls) == 3:
				raise RuntimeError("a Frappe validation this app did not anticipate")
			return real_new_doc(doctype)

		frappe.new_doc = exploding_new_doc
		self.addCleanup(setattr, frappe, "new_doc", real_new_doc)

		before = len(STORE.rows("Account"))
		self.tool_error(
			"import_chart_of_accounts", {"company": MAIN, "accounts_json": SMALL, "dry_run": False}
		)
		self.assertEqual(len(STORE.rows("Account")), before)
		self.assertFalse(frappe.db.exists("Account", f"9000 - Test Root - {MAIN_ABBR}"))
		self.assertFalse(frappe.db.exists("Account", f"9100 - Test Group - {MAIN_ABBR}"))
		self.assertAudited("import_chart_of_accounts", "Error")

	def test_the_whole_farm_template_imports_into_a_fresh_company(self):
		"""End to end, and the closest this suite gets to the real job: propose a
		chart for a company with nothing in it, then run it for real."""
		self.fresh_company()
		proposal = self.tool_data("propose_clean_chart", {"company": FRESH})
		self.assertEqual(proposal["existing_root_accounts"], [])
		self.assertEqual(proposal["account_numbers_already_in_use"], [])

		data = self.tool_data(
			"import_chart_of_accounts",
			{"company": FRESH, "accounts_json": proposal["accounts"], "dry_run": False},
		)
		self.assertEqual(data["counts"]["created"], proposal["total_accounts"])
		self.assertEqual(frappe.db.count("Account", {"company": FRESH}), proposal["total_accounts"])
		self.assertTrue(
			frappe.db.exists("Account", f"2120 - Current Pay Period - Due to Employees - {FRESH_ABBR}")
		)

	def test_the_imported_tree_is_connected_all_the_way_down(self):
		"""Seven roots and nothing orphaned — the failure a flat import produces
		looks fine account by account and is useless as a chart."""
		self.fresh_company()
		proposal = self.tool_data("propose_clean_chart", {"company": FRESH})
		self.tool_data(
			"import_chart_of_accounts",
			{"company": FRESH, "accounts_json": proposal["accounts"], "dry_run": False},
		)
		rows = frappe.db.get_all(
			"Account",
			filters={"company": FRESH},
			fields=["name", "parent_account", "is_group"],
			limit=500,
		)
		by_name = {row["name"]: row for row in rows}
		roots = [row for row in rows if not row["parent_account"]]
		self.assertEqual(len(roots), 7)
		for row in rows:
			if row["parent_account"]:
				self.assertIn(row["parent_account"], by_name, f"{row['name']} is orphaned")
				self.assertTrue(by_name[row["parent_account"]]["is_group"])

	def test_an_empty_group_imports_and_can_then_be_filled(self):
		"""1830 ships empty and gets a child per linked brokerage account. Both
		halves have to work: ERPNext has to accept a childless group, and
		create_account has to accept it as a parent afterwards."""
		self.fresh_company()
		proposal = self.tool_data("propose_clean_chart", {"company": FRESH})
		self.tool_data(
			"import_chart_of_accounts",
			{"company": FRESH, "accounts_json": proposal["accounts"], "dry_run": False},
		)
		group = f"1830 - Brokerage Cash & Money Market - {FRESH_ABBR}"
		self.assertTrue(frappe.db.get_value("Account", group, "is_group"))
		self.assertEqual(frappe.db.count("Account", {"parent_account": group}), 0)

		child = self.tool_data(
			"create_account",
			{
				"company": FRESH,
				"account_number": "1831",
				"account_name": "Brokerage Cash - 3158",
				"root_type": "Asset",
				"parent_account": group,
				"account_type": "Bank",
			},
		)
		self.assertEqual(child["parent_account"], group)
		self.assertEqual(child["account_type"], "Bank")
		self.assertEqual(frappe.db.count("Account", {"parent_account": group}), 1)

	def test_descriptions_land_as_comments_when_the_doctype_has_no_field(self):
		self.fresh_company()
		proposal = self.tool_data("propose_clean_chart", {"company": FRESH})
		data = self.tool_data(
			"import_chart_of_accounts",
			{"company": FRESH, "accounts_json": proposal["accounts"], "dry_run": False},
		)
		self.assertTrue(data["descriptions_written"]["comment"])
		self.assertTrue(
			any("NOT A PERIOD-END ACCRUAL" in comment.get("text", "") for comment in STORE.comments)
		)

	def fresh_company(self):
		"""A company with no chart at all — what mom's ERPNext looks like before
		anybody touches it, minus even the bundled defaults."""
		STORE.seed(
			"Company",
			[
				{
					"name": FRESH,
					"abbr": FRESH_ABBR,
					"default_currency": "USD",
					"country": "United States",
					"is_group": 0,
				}
			],
		)

	def test_descriptions_use_a_description_field_where_a_site_has_one(self):
		META["Account"].fields.append(Field(fieldname="description", fieldtype="Small Text"))
		META["Account"]._by_name["description"] = META["Account"].fields[-1]
		self.addCleanup(META["Account"]._by_name.pop, "description", None)
		self.addCleanup(META["Account"].fields.pop)
		tree = [
			{
				"account_number": "9000",
				"account_name": "Test Root",
				"root_type": "Expense",
				"is_group": True,
				"children": [{"account_number": "9100", "account_name": "Noted", "description": "read me"}],
			}
		]
		data = self.run_import(tree)
		self.assertEqual(data["descriptions_written"], {"description field": 1})
		self.assertEqual(
			frappe.db.get_value("Account", f"9100 - Noted - {MAIN_ABBR}", "description"), "read me"
		)

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("import_chart_of_accounts", {"company": MAIN, "accounts_json": SMALL})
		self.assertIn("allow_import_chart_of_accounts", message)

	def test_dry_run_garbage_is_refused_rather_than_read_as_false(self):
		"""`dry_run="maybe"` becoming a live run is the failure the strict
		coercion exists to prevent."""
		message = self.tool_error(
			"import_chart_of_accounts",
			{"company": MAIN, "accounts_json": SMALL, "dry_run": "maybe"},
		)
		self.assertIn("must be true or false", message)
		self.assertFalse(frappe.db.exists("Account", f"9110 - Test Leaf - {MAIN_ABBR}"))


# ── the template itself ─────────────────────────────────────────────────────
class TemplateData(SeededTestCase):
	def template(self):
		from erpnext_mcp import charts

		return charts.get("us_llc_farm")

	def test_it_is_pure_data_with_no_live_lookup(self):
		"""A template that needed a site to describe itself could not be
		reviewed before it ran."""
		described = self.template().describe()
		self.assertEqual(described["total_accounts"], 81)
		self.assertEqual(described["group_accounts"], 17)
		self.assertEqual(described["ledger_accounts"], 64)

	def test_it_stays_shallow(self):
		"""Compact is a property of the shape, not only the count. Two levels of
		grouping is the limit this chart sets itself; the moment a third appears
		somebody has started building the sprawling version again."""
		from erpnext_mcp import charts

		deepest = max(depth for _node, _parent, depth in charts.walk(self.template().tree))
		self.assertLessEqual(deepest, 3)

	#: Deliberately carry no account_type. ERPNext offers nothing that fits a
	#: securities or open-options position — the nearest, Stock, means trading
	#: inventory and would pull them into the Stock module's valuation.
	UNTYPED_BY_DESIGN = ("1810", "1815", "1820", "1840")

	def test_every_number_is_unique_and_every_leaf_is_typed(self):
		from erpnext_mcp import charts

		numbers, untyped = set(), []
		for node, _parent, _depth in charts.walk(self.template().tree):
			number = node["account_number"]
			self.assertNotIn(number, numbers, f"{number} appears twice")
			numbers.add(number)
			if not node.get("is_group") and not node.get("account_type"):
				untyped.append(number)
		self.assertEqual(tuple(untyped), self.UNTYPED_BY_DESIGN)

	def test_the_five_root_types_are_all_represented(self):
		roots = {node["root_type"] for node in self.template().tree}
		self.assertEqual(roots, {"Asset", "Liability", "Equity", "Income", "Expense"})

	def test_it_passes_the_importers_own_validation(self):
		from erpnext_mcp import charts

		charts.validate_tree(self.template().tree)

	def test_the_registry_finds_it_by_discovery(self):
		from erpnext_mcp import charts

		self.assertIn("us_llc_farm", charts.names())
