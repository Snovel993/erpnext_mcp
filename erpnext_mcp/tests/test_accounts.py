# SPDX-License-Identifier: MIT
"""In-bench tests for the chart-of-accounts tools.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_accounts

WHAT ONLY A BENCH CAN SHOW HERE, and why this module exists at all.

The standalone suite proves the app's logic against a double of Account. But
Account is the doctype where a double is most likely to be wrong, because almost
everything interesting about it is behaviour rather than data:

  * **`autoname`.** The docname is `"<number> - <name> - <abbr>"`, built by
    ERPNext and never rebuilt. The app predicts that string during a dry run.
    Only a real insert can prove the prediction matches.
  * **`update_account_number`.** The app delegates its whole rename path to a
    function in ERPNext, whose signature and return value have moved between
    versions. A double agrees with itself; a bench agrees with the operator's
    actual install.
  * **"Root cannot be edited."** The app refuses roots up front, citing a rule
    that lives in `Account.validate_root_details`. If that rule ever relaxed,
    the app would be refusing something legal for no reason — and nothing but a
    real save would tell us.
  * **The nested set.** Reparenting through `doc.save()` relies on NestedSet's
    `on_update` fixing `lft`/`rgt`. The double does not maintain them at all.

Everything is created inside the test transaction and rolled back, except the
audit rows the base class cleans up. Nothing here touches the operator's own
chart: each case builds its own subtree under a group it created itself.
"""

import frappe

from erpnext_mcp import charts

from .test_integration import MCPIntegrationTestCase

CHART_TOOLS = (
	"create_account",
	"update_account",
	"move_account",
	"disable_account",
	"import_chart_of_accounts",
	"propose_clean_chart",
)

#: Numbers used only by this module, high enough not to collide with any real
#: chart. Every account created here is under one of these.
PREFIX = "99"


class ChartTestCase(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable(*CHART_TOOLS)
		self.company = self.any_company()
		self.abbr = frappe.db.get_value("Company", self.company, "abbr")
		self.root = self.a_group_account()

	def a_group_account(self):
		"""A group under the company's Asset root, created for this test.

		Discovered-then-extended rather than created from scratch: making a
		Company builds a whole chart of accounts and takes seconds, and the point
		of this app is that it works against whatever chart is already there.
		"""
		asset_root = frappe.db.get_value(
			"Account",
			{"company": self.company, "root_type": "Asset", "parent_account": ["in", ["", None]]},
			"name",
		)
		if not asset_root:
			self.skipTest(f"{self.company} has no Asset root account")
		doc = frappe.get_doc(
			{
				"doctype": "Account",
				"company": self.company,
				"account_number": f"{PREFIX}00",
				"account_name": "MCP Test Group",
				"parent_account": asset_root,
				"is_group": 1,
				"root_type": "Asset",
			}
		).insert(ignore_permissions=True)
		return doc.name

	def child(self, number, name, **fields):
		payload = {
			"company": self.company,
			"account_number": f"{PREFIX}{number}",
			"account_name": name,
			"root_type": "Asset",
			"parent_account": self.root,
		}
		payload.update(fields)
		return self.tool_data("create_account", payload)


class AutonameAgreesWithErpnext(ChartTestCase):
	"""The prediction the whole dry-run plan rests on."""

	def test_a_created_account_gets_the_docname_this_app_predicted(self):
		predicted = charts.account_docname(f"{PREFIX}10", "Predicted", self.abbr)
		data = self.child("10", "Predicted")
		self.assertEqual(data["name"], predicted)
		self.assertTrue(frappe.db.exists("Account", predicted))

	def test_the_dry_run_plan_predicts_what_the_real_run_creates(self):
		tree = [
			{
				"account_number": f"{PREFIX}20",
				"account_name": "Planned Group",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": self.root,
				"children": [{"account_number": f"{PREFIX}21", "account_name": "Planned Leaf"}],
			}
		]
		plan = self.tool_data("import_chart_of_accounts", {"company": self.company, "accounts_json": tree})
		self.assertTrue(plan["dry_run"])
		predicted = [row["docname"] for row in plan["accounts"]]

		real = self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": tree, "dry_run": False},
		)
		self.assertEqual([row["docname"] for row in real["accounts"]], predicted)
		for docname in predicted:
			self.assertTrue(frappe.db.exists("Account", docname), docname)

	def test_the_app_and_erpnext_build_the_same_name(self):
		"""Straight against ERPNext's own helper where the version exports it."""
		try:
			from erpnext.accounts.doctype.account.account import get_account_autoname
		except ImportError:
			self.skipTest("this ERPNext does not export get_account_autoname")
		self.assertEqual(
			charts.account_docname("1234", "Some Account", self.abbr),
			get_account_autoname("1234", "Some Account", self.company),
		)


class RealValidationRefusals(ChartTestCase):
	def test_a_ledger_parent_is_refused_before_erpnext_sees_it(self):
		leaf = self.child("30", "A Leaf")
		result = self.tool(
			"create_account",
			{
				"company": self.company,
				"account_number": f"{PREFIX}31",
				"account_name": "Under A Leaf",
				"root_type": "Asset",
				"parent_account": leaf["name"],
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("not a group", result["content"][0]["text"])

	def test_erpnext_still_refuses_it_if_this_app_ever_stopped(self):
		"""The app's check is a better error message, not the safety net. If this
		ever passes, ERPNext changed and the app's refusal is now the only one."""
		leaf = self.child("32", "Another Leaf")
		with self.assertRaises(Exception):
			frappe.get_doc(
				{
					"doctype": "Account",
					"company": self.company,
					"account_number": f"{PREFIX}33",
					"account_name": "Illegal Child",
					"parent_account": leaf["name"],
					"root_type": "Asset",
				}
			).insert(ignore_permissions=True)

	def test_a_duplicate_number_is_refused_with_the_holder(self):
		self.child("34", "First")
		result = self.tool(
			"create_account",
			{
				"company": self.company,
				"account_number": f"{PREFIX}34",
				"account_name": "Second",
				"root_type": "Asset",
				"parent_account": self.root,
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("already used by", result["content"][0]["text"])

	def test_the_account_type_options_this_app_validates_against_are_the_sites_own(self):
		"""`charts.site_account_types()` reads the live Select. If it came back
		empty the app would wave every account_type through."""
		supported = charts.site_account_types()
		self.assertIn("Bank", supported)
		self.assertIn("Payable", supported)

	def test_every_account_type_in_the_shipped_template_resolves_to_a_real_one(self):
		"""The substitution table has to produce something this ERPNext accepts,
		or a proposal is a plan that cannot be run. Inheritance is resolved down
		the tree here rather than one level up: an `account_type` checked against
		an empty `root_type` would be silently discarded, and the assertion below
		would then be checking nothing."""
		supported = charts.site_account_types()
		checked = 0

		def visit(nodes, root_type):
			nonlocal checked
			for node in nodes:
				inherited = node.get("root_type") or root_type
				wanted = node.get("account_type") or ""
				if wanted:
					usable, note = charts.resolve_account_type(wanted, inherited, supported)
					checked += 1
					self.assertTrue(
						usable or "no equivalent here" in note,
						f"{node['account_number']} {node['account_name']}: {wanted} → {note!r}",
					)
					if usable:
						self.assertIn(usable, supported)
				visit(node.get("children") or [], inherited)

		visit(charts.get("us_llc_farm").tree, "")
		self.assertGreater(checked, 50, "the template lost its account types")


class RenameAgainstRealErpnext(ChartTestCase):
	"""The delegation this module's docstring is mostly about."""

	def test_erpnext_still_exports_the_helper_this_app_delegates_to(self):
		from erpnext.accounts.doctype.account.account import update_account_number

		self.assertTrue(callable(update_account_number))

	def test_renaming_moves_the_docname_and_the_field_together(self):
		created = self.child("40", "Before")
		data = self.tool_data("update_account", {"name": created["name"], "new_account_name": "After"})
		self.assertEqual(data["rename_method"], "erpnext update_account_number")
		self.assertEqual(data["name"], charts.account_docname(f"{PREFIX}40", "After", self.abbr))
		self.assertFalse(frappe.db.exists("Account", created["name"]))
		self.assertEqual(frappe.db.get_value("Account", data["name"], "account_name"), "After")

	def test_renumbering_moves_the_docname_and_the_field_together(self):
		created = self.child("41", "Renumber Me")
		data = self.tool_data(
			"update_account", {"name": created["name"], "new_account_number": f"{PREFIX}42"}
		)
		self.assertEqual(data["name"], charts.account_docname(f"{PREFIX}42", "Renumber Me", self.abbr))
		self.assertEqual(frappe.db.get_value("Account", data["name"], "account_number"), f"{PREFIX}42")

	def test_renaming_a_group_repoints_its_children_in_the_database(self):
		"""The failure a naive rename produces: a group whose children still
		point at a docname that no longer exists."""
		child = self.child("43", "Child Of Renamed")
		renamed = self.tool_data(
			"update_account", {"name": self.root, "new_account_name": "MCP Test Group Renamed"}
		)
		self.assertEqual(frappe.db.get_value("Account", child["name"], "parent_account"), renamed["name"])

	def test_the_document_and_its_name_never_disagree(self):
		"""The invariant both halves of the rename exist to preserve."""
		created = self.child("44", "Consistent")
		data = self.tool_data(
			"update_account",
			{
				"name": created["name"],
				"new_account_name": "Still Consistent",
				"new_account_number": f"{PREFIX}45",
			},
		)
		row = frappe.db.get_value("Account", data["name"], ["account_name", "account_number"], as_dict=True)
		self.assertEqual(
			data["name"],
			charts.account_docname(row.account_number, row.account_name, self.abbr),
		)


class RootsAgainstRealErpnext(ChartTestCase):
	def a_root(self):
		name = frappe.db.get_value(
			"Account", {"company": self.company, "parent_account": ["in", ["", None]]}, "name"
		)
		if not name:
			self.skipTest(f"{self.company} has no root account")
		return name

	def test_erpnext_really_does_refuse_to_save_a_root(self):
		"""The rule the app cites in three separate refusals. If ERPNext ever
		relaxed it, those refusals would be gratuitous."""
		doc = frappe.get_doc("Account", self.a_root())
		doc.account_type = "Bank"
		with self.assertRaises(Exception):
			doc.save(ignore_permissions=True)

	def test_the_app_refuses_first_with_a_better_message(self):
		result = self.tool("disable_account", {"name": self.a_root(), "reason": "tidying the chart"})
		self.assertTrue(result["isError"])
		self.assertIn("Root cannot be edited", result["content"][0]["text"])

	def test_a_root_cannot_be_moved(self):
		result = self.tool("move_account", {"name": self.a_root(), "new_parent_account": self.root})
		self.assertTrue(result["isError"])
		self.assertIn("root account", result["content"][0]["text"])


class MoveAgainstTheNestedSet(ChartTestCase):
	def test_reparenting_keeps_the_nested_set_consistent(self):
		"""`doc.save()` is what triggers NestedSet's rebuild. The standalone
		double does not maintain lft/rgt at all, so this is the only place the
		tree stays a tree."""
		target = self.tool_data(
			"create_account",
			{
				"company": self.company,
				"account_number": f"{PREFIX}50",
				"account_name": "Move Target",
				"root_type": "Asset",
				"parent_account": self.root,
				"is_group": True,
			},
		)
		moving = self.child("51", "Moving Account")

		self.tool_data("move_account", {"name": moving["name"], "new_parent_account": target["name"]})
		child = frappe.db.get_value("Account", moving["name"], ["lft", "rgt"], as_dict=True)
		parent = frappe.db.get_value("Account", target["name"], ["lft", "rgt"], as_dict=True)
		self.assertLess(parent.lft, child.lft)
		self.assertGreater(parent.rgt, child.rgt)

	def test_a_cycle_is_refused_before_the_nested_set_is_asked_to_build_one(self):
		inner = self.tool_data(
			"create_account",
			{
				"company": self.company,
				"account_number": f"{PREFIX}52",
				"account_name": "Inner Group",
				"root_type": "Asset",
				"parent_account": self.root,
				"is_group": True,
			},
		)
		result = self.tool("move_account", {"name": self.root, "new_parent_account": inner["name"]})
		self.assertTrue(result["isError"])
		self.assertIn("cycle", result["content"][0]["text"])


class DisableAgainstTheLedger(ChartTestCase):
	def test_it_reads_this_sites_real_fiscal_year(self):
		created = self.child("60", "Quiet Account")
		data = self.tool_data(
			"disable_account", {"name": created["name"], "reason": "created only for this test"}
		)
		self.assertTrue(data["disabled"])
		current = frappe.db.get_value(
			"Fiscal Year",
			{
				"year_start_date": ["<=", frappe.utils.nowdate()],
				"year_end_date": [">=", frappe.utils.nowdate()],
			},
			"name",
		)
		if current:
			self.assertIn(str(current), data["checked_window"])

	def test_an_account_with_this_years_postings_is_refused(self):
		"""Built rather than discovered, so the case runs on any site."""
		account = self.child("61", "Busy Account", account_type="Cash")
		counterpart = self.child("62", "Counterpart Account", account_type="Cash")
		entry = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"company": self.company,
				"posting_date": frappe.utils.nowdate(),
				"user_remark": "erpnext_mcp disable_account test",
				"accounts": [
					{"account": account["name"], "debit_in_account_currency": 10},
					{"account": counterpart["name"], "credit_in_account_currency": 10},
				],
			}
		)
		try:
			entry.insert(ignore_permissions=True)
			entry.submit()
		except Exception as exc:
			self.skipTest(f"site would not accept a test Journal Entry: {exc}")

		result = self.tool("disable_account", {"name": account["name"], "reason": "no longer used"})
		self.assertTrue(result["isError"])
		self.assertIn("GL Entry", result["content"][0]["text"])
		self.assertFalse(frappe.db.get_value("Account", account["name"], "disabled"))

	def test_nothing_is_deleted_by_a_disable(self):
		created = self.child("63", "Retiring Account")
		self.tool_data("disable_account", {"name": created["name"], "reason": "retiring it"})
		self.assertTrue(frappe.db.exists("Account", created["name"]))
		self.assertTrue(frappe.db.get_value("Account", created["name"], "disabled"))


class ImportAgainstRealErpnext(ChartTestCase):
	def subtree(self, top):
		return [
			{
				"account_number": f"{PREFIX}{top}",
				"account_name": f"Imported Group {top}",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": self.root,
				"children": [
					{
						"account_number": f"{PREFIX}{top + 1}",
						"account_name": f"Imported Leaf {top + 1}",
						"account_type": "Cash",
					},
					{
						"account_number": f"{PREFIX}{top + 2}",
						"account_name": f"Imported Leaf {top + 2}",
					},
				],
			}
		]

	def test_a_dry_run_writes_nothing_at_all(self):
		before = frappe.db.count("Account", {"company": self.company})
		self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": self.subtree(70)},
		)
		self.assertEqual(frappe.db.count("Account", {"company": self.company}), before)

	def test_a_real_run_builds_the_tree_erpnext_accepts(self):
		data = self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": self.subtree(70), "dry_run": False},
		)
		self.assertEqual(data["counts"]["created"], 3)
		group = next(row for row in data["accounts"] if row["is_group"])
		for row in data["accounts"]:
			if row is group:
				continue
			self.assertEqual(
				frappe.db.get_value("Account", row["docname"], "parent_account"), group["docname"]
			)

	def test_a_second_run_skips_rather_than_duplicating(self):
		self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": self.subtree(74), "dry_run": False},
		)
		plan = self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": self.subtree(74)},
		)
		self.assertEqual({row["action"] for row in plan["accounts"]}, {"skip"})

	def test_a_number_already_held_by_another_account_is_caught_before_writing(self):
		tree = self.subtree(78)
		tree[0]["children"][1]["account_number"] = f"{PREFIX}00"  # the group created in setUp
		before = frappe.db.count("Account", {"company": self.company})
		result = self.tool(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": tree, "dry_run": False},
		)
		self.assertTrue(result["isError"])
		self.assertEqual(frappe.db.count("Account", {"company": self.company}), before)

	def test_a_failure_part_way_through_the_inserts_leaves_nothing_behind(self):
		"""Atomicity against a real transaction rather than a modelled one.

		Two unnumbered siblings with the same name get past the plan — its
		uniqueness check is on `account_number`, and neither has one — and then
		collide on the docname at insert, by which point the group and the first
		leaf are already written. Pathological input, and exactly the case the
		all-or-nothing guarantee exists for: what comes back is a site with no
		half-built subtree in it."""
		tree = [
			{
				"account_number": f"{PREFIX}90",
				"account_name": "Atomic Group",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": self.root,
				"children": [
					{"account_name": "Duplicate Leaf"},
					{"account_name": "Duplicate Leaf"},
				],
			}
		]
		before = frappe.db.count("Account", {"company": self.company})
		result = self.tool(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": tree, "dry_run": False},
		)
		self.assertTrue(result["isError"], result)
		self.assertEqual(frappe.db.count("Account", {"company": self.company}), before)
		self.assertFalse(
			frappe.db.exists("Account", charts.account_docname(f"{PREFIX}90", "Atomic Group", self.abbr))
		)

	def test_descriptions_land_somewhere_a_human_will_see_them(self):
		tree = [
			{
				"account_number": f"{PREFIX}82",
				"account_name": "Documented Account",
				"root_type": "Asset",
				"is_group": True,
				"parent_account": self.root,
				"children": [
					{
						"account_number": f"{PREFIX}83",
						"account_name": "Explained Account",
						"description": "This account exists only to prove descriptions survive.",
					}
				],
			}
		]
		data = self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": tree, "dry_run": False},
		)
		self.assertTrue(data["descriptions_written"])
		docname = charts.account_docname(f"{PREFIX}83", "Explained Account", self.abbr)
		if "description field" in data["descriptions_written"]:
			self.assertIn(
				"prove descriptions survive",
				frappe.db.get_value("Account", docname, "description") or "",
			)
		else:
			comments = frappe.db.get_all(
				"Comment",
				filters={"reference_doctype": "Account", "reference_name": docname},
				pluck="content",
			)
			self.assertTrue(any("prove descriptions survive" in (c or "") for c in comments))


class ProposeAgainstThisSite(ChartTestCase):
	def test_it_creates_nothing(self):
		before = frappe.db.count("Account", {"company": self.company})
		self.tool_data("propose_clean_chart", {"company": self.company})
		self.assertEqual(frappe.db.count("Account", {"company": self.company}), before)

	def test_it_reports_this_companys_real_roots(self):
		data = self.tool_data("propose_clean_chart", {"company": self.company})
		reported = {row["name"] for row in data["existing_root_accounts"]}
		actual = set(
			frappe.db.get_all(
				"Account",
				filters={"company": self.company, "parent_account": ["in", ["", None]]},
				pluck="name",
			)
		)
		self.assertEqual(reported, actual)

	def test_the_proposal_survives_the_importers_dry_run_on_this_site(self):
		"""The end-to-end promise: what propose returns is what import accepts."""
		proposal = self.tool_data("propose_clean_chart", {"company": self.company})
		plan = self.tool_data(
			"import_chart_of_accounts",
			{"company": self.company, "accounts_json": proposal["accounts"]},
		)
		self.assertTrue(plan["dry_run"])
		self.assertEqual(plan["total_accounts"], proposal["total_accounts"])


class ChartSwitchesMigrated(MCPIntegrationTestCase):
	def test_every_chart_tool_has_a_switch_on_the_real_doctype(self):
		meta = frappe.get_meta("ERPNext MCP Settings")
		for name in CHART_TOOLS:
			self.assertTrue(meta.has_field(f"allow_{name}"), f"allow_{name} did not migrate")

	def test_the_write_ones_default_off_and_the_planner_defaults_on(self):
		meta = frappe.get_meta("ERPNext MCP Settings")
		for name in CHART_TOOLS:
			expected = "1" if name == "propose_clean_chart" else "0"
			self.assertEqual(meta.get_field(f"allow_{name}").default, expected, name)

	def test_the_section_migrated(self):
		self.assertTrue(frappe.get_meta("ERPNext MCP Settings").has_field("chart_tools_section"))

	def test_a_disabled_chart_tool_is_not_advertised(self):
		from erpnext_mcp import registry

		body, _status = self.rpc("tools/list")
		advertised = {tool["name"] for tool in body["result"]["tools"]}
		self.assertNotIn("create_account", advertised)
		self.assertIn("create_account", registry.TOOLS)
