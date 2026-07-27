# SPDX-License-Identifier: MIT
"""In-bench tests for the cost centre and accounting-dimension tools.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_dimensions

WHAT ONLY A BENCH CAN SHOW HERE, and why this module exists at all.

The standalone suite proves the logic against a double. These three facts are
not facts about this app's logic at all — they are facts about the operator's
ERPNext, and a double agrees with whatever it was written to agree with:

  * **Cost Center's `autoname`.** `dimensions.cost_center_docname` predicts
    `"<number> - <name> - <abbr>"` and does NOT import ERPNext's own
    `get_autoname_with_number` — deliberately, because that helper's signature
    moved between majors. The prediction is what every uniqueness check and
    every rename in the module rests on, so a real insert has to confirm it.
  * **The root rule.** The module refuses a rootless cost center named anything
    other than its company, citing `CostCenter.validate_mandatory`. If that rule
    ever relaxed, the app would be refusing something legal for no reason.
  * **Creating a DocType and a Custom Field for real.** `create_accounting_dimension`
    can add a custom DocType (`custom: 1`, named `field:dimension_value`, with a
    `naming_rule` this app sets only where the version has the field) and Link
    fields on accounting doctypes. Whether Frappe accepts that payload, and
    whether `frappe.get_meta` reports the field afterwards, is a framework
    contract — and the whole feature is worthless if either half is wrong.

Everything is created inside the test transaction and rolled back, except the
audit rows the base class cleans up. Nothing here renames or disables a cost
center the operator already had: every case builds its own subtree under a group
it created itself.
"""

import frappe

from erpnext_mcp.tools import dimensions

from .test_integration import MCPIntegrationTestCase

DIMENSION_TOOLS = (
	"create_cost_center",
	"update_cost_center",
	"list_cost_centers",
	"create_accounting_dimension",
	"create_dimension_value",
	"set_company_defaults",
	"create_journal_entry",
)

#: Numbers and names used only by this module, prefixed so they cannot collide
#: with anything on a real site.
PREFIX = "99"
MASTER = "MCP Test Dimension Master"
LABEL = "MCP Test Axis"


class DimensionTestCase(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable(*DIMENSION_TOOLS)
		self.company = self.any_company()
		self.abbr = frappe.db.get_value("Company", self.company, "abbr")

	def a_group_cost_center(self):
		"""A group under the company's root cost center, created for this test."""
		root = frappe.db.get_value(
			"Cost Center",
			{"company": self.company, "parent_cost_center": ["in", ["", None]]},
			"name",
		)
		if not root:
			self.skipTest(f"{self.company} has no root Cost Center")
		doc = frappe.get_doc(
			{
				"doctype": "Cost Center",
				"company": self.company,
				"cost_center_number": f"{PREFIX}00",
				"cost_center_name": "MCP Test Group",
				"parent_cost_center": root,
				"is_group": 1,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def child(self, number, name, **fields):
		payload = {
			"company": self.company,
			"cost_center_number": f"{PREFIX}{number}",
			"cost_center_name": name,
			"parent_cost_center": self.parent,
		}
		payload.update(fields)
		return self.tool_data("create_cost_center", payload)


class CostCenterAutonameAgreesWithErpnext(DimensionTestCase):
	"""The prediction every uniqueness check and rename in the module rests on."""

	def setUp(self):
		super().setUp()
		self.parent = self.a_group_cost_center()

	def test_a_created_cost_center_gets_the_docname_this_app_predicted(self):
		predicted = dimensions.cost_center_docname(f"{PREFIX}10", "Predicted", self.abbr)
		data = self.child("10", "Predicted")
		self.assertEqual(data["name"], predicted)
		self.assertTrue(frappe.db.exists("Cost Center", predicted))

	def test_an_unnumbered_cost_center_is_named_without_a_number(self):
		predicted = dimensions.cost_center_docname("", "Unnumbered", self.abbr)
		data = self.child("", "Unnumbered", cost_center_number="")
		self.assertEqual(data["name"], predicted)
		self.assertNotIn(PREFIX, data["name"])

	def test_a_rename_moves_the_docname_and_the_field_together(self):
		created = self.child("20", "Before")
		data = self.tool_data(
			"update_cost_center",
			{"name": created["name"], "new_cost_center_name": "After"},
		)
		self.assertEqual(data["name"], dimensions.cost_center_docname(f"{PREFIX}20", "After", self.abbr))
		self.assertFalse(frappe.db.exists("Cost Center", created["name"]))
		self.assertEqual(frappe.db.get_value("Cost Center", data["name"], "cost_center_name"), "After")

	def test_a_renamed_group_keeps_its_children(self):
		self.child("30", "Kept")
		renamed = self.tool_data(
			"update_cost_center",
			{"name": self.parent, "new_cost_center_name": "MCP Test Group Renamed"},
		)
		child = dimensions.cost_center_docname(f"{PREFIX}30", "Kept", self.abbr)
		self.assertEqual(frappe.db.get_value("Cost Center", child, "parent_cost_center"), renamed["name"])

	def test_erpnext_still_refuses_a_root_that_is_not_named_after_its_company(self):
		"""The rule `_validated_cost_center_parent` cites. If it relaxed, the app
		would be refusing something legal."""
		doc = frappe.get_doc(
			{
				"doctype": "Cost Center",
				"company": self.company,
				"cost_center_name": "MCP Test Rogue Root",
				"is_group": 1,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_a_leaf_parent_is_refused_before_anything_is_written(self):
		leaf = self.child("40", "Leaf")
		message = self.tool(
			"create_cost_center",
			{
				"company": self.company,
				"cost_center_number": f"{PREFIX}41",
				"cost_center_name": "Under A Leaf",
				"parent_cost_center": leaf["name"],
			},
		)["content"][0]["text"]
		self.assertIn("not a group", message)
		self.assertFalse(
			frappe.db.exists(
				"Cost Center", dimensions.cost_center_docname(f"{PREFIX}41", "Under A Leaf", self.abbr)
			)
		)


class CostCenterTreeAgainstTheRealSite(DimensionTestCase):
	def test_the_tree_comes_back_rooted_at_the_company(self):
		data = self.tool_data("list_cost_centers", {"company": self.company})
		self.assertTrue(data["cost_centers"], "no cost centers for this company")
		self.assertGreaterEqual(data["flat_count"], 1)
		for node in data["cost_centers"]:
			self.assertIn("children", node)

	def test_a_created_cost_center_appears_in_the_tree(self):
		self.parent = self.a_group_cost_center()
		created = self.child("50", "Visible")
		data = self.tool_data("list_cost_centers", {"company": self.company})
		found = _find(data["cost_centers"], created["name"])
		self.assertIsNotNone(found, f"{created['name']} missing from the tree")


class AccountingDimensionsThroughRealFrappe(DimensionTestCase):
	"""The framework contract: a generated DocType, and Custom Fields that stick."""

	def setUp(self):
		super().setUp()
		if not frappe.db.exists("DocType", "Accounting Dimension"):
			self.skipTest("this ERPNext has no Accounting Dimension doctype")
		if frappe.db.exists("DocType", MASTER):
			self.skipTest(f"{MASTER} already exists on this site")

	def create(self, **overrides):
		payload = {
			"dimension_name": LABEL,
			"master_doctype": MASTER,
			"create_master_if_missing": True,
			"document_types": ["Journal Entry"],
		}
		payload.update(overrides)
		return self.tool_data("create_accounting_dimension", payload)

	def test_the_generated_master_is_a_real_creatable_doctype(self):
		data = self.create()
		self.assertTrue(data["master_doctype_created"])
		self.assertTrue(frappe.db.exists("DocType", MASTER))
		self.assertEqual(frappe.db.get_value("DocType", MASTER, "custom"), 1)
		record = frappe.get_doc({"doctype": MASTER, "dimension_value": "MCP-01"})
		record.insert(ignore_permissions=True)
		self.assertEqual(record.name, "MCP-01")

	def test_the_custom_field_is_visible_to_get_meta_immediately(self):
		data = self.create()
		fieldname = data["fieldname"]
		self.assertEqual(data["document_types_applied"], ["Journal Entry Account"])
		self.assertTrue(frappe.get_meta("Journal Entry Account").has_field(fieldname))
		field = frappe.get_meta("Journal Entry Account").get_field(fieldname)
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, MASTER)

	def test_the_accounting_dimension_record_points_at_the_master(self):
		data = self.create()
		self.assertEqual(frappe.db.get_value("Accounting Dimension", data["name"], "document_type"), MASTER)

	def test_a_value_created_through_the_tool_is_named_after_itself(self):
		self.create()
		data = self.tool_data("create_dimension_value", {"dimension_name": LABEL, "value_name": "MCP-02"})
		self.assertEqual(data["name"], "MCP-02")
		self.assertEqual(data["named_by"], "field:dimension_value")
		self.assertTrue(frappe.db.exists(MASTER, "MCP-02"))

	def test_a_dimension_really_persists_on_a_journal_entry_line(self):
		"""The end-to-end case, and the reason the whole feature exists."""
		dimension = self.create()
		fieldname = dimension["fieldname"]
		self.tool_data("create_dimension_value", {"dimension_name": LABEL, "value_name": "MCP-03"})

		accounts = self.leaf_accounts(self.company, 2)
		created = self.tool_data(
			"create_journal_entry",
			{
				"company": self.company,
				"posting_date": self.open_posting_date(self.company),
				"user_remark": "MCP dimension round trip",
				"accounts": [
					{"account": accounts[0], "debit": 1, "dimensions": {fieldname: "MCP-03"}},
					{"account": accounts[1], "credit": 1},
				],
			},
		)
		self.assertEqual(created["dimension_fields_set"], [fieldname])

		doc = frappe.get_doc("Journal Entry", created["name"])
		self.assertEqual(doc.accounts[0].get(fieldname), "MCP-03")
		# And it is a real column, not just an attribute on the in-memory doc.
		self.assertEqual(
			frappe.db.get_value("Journal Entry Account", doc.accounts[0].name, fieldname), "MCP-03"
		)

	def test_a_value_that_is_not_a_record_is_refused_before_the_draft_exists(self):
		dimension = self.create()
		accounts = self.leaf_accounts(self.company, 2)
		result = self.tool(
			"create_journal_entry",
			{
				"company": self.company,
				"posting_date": self.open_posting_date(self.company),
				"user_remark": "MCP bad dimension value",
				"accounts": [
					{
						"account": accounts[0],
						"debit": 1,
						"dimensions": {dimension["fieldname"]: "MCP-NOPE"},
					},
					{"account": accounts[1], "credit": 1},
				],
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("MCP-NOPE", result["content"][0]["text"])
		self.assertFalse(frappe.db.exists("Journal Entry", {"user_remark": "MCP bad dimension value"}))

	def test_a_dimension_field_that_does_not_exist_is_refused(self):
		accounts = self.leaf_accounts(self.company, 2)
		result = self.tool(
			"create_journal_entry",
			{
				"company": self.company,
				"posting_date": self.open_posting_date(self.company),
				"user_remark": "MCP unknown dimension",
				"accounts": [
					{
						"account": accounts[0],
						"debit": 1,
						"dimensions": {"mcp_field_that_does_not_exist": "x"},
					},
					{"account": accounts[1], "credit": 1},
				],
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("create_accounting_dimension", result["content"][0]["text"])

	def test_a_missing_master_without_the_flag_creates_nothing(self):
		result = self.tool(
			"create_accounting_dimension",
			{
				"dimension_name": LABEL,
				"master_doctype": MASTER,
				"document_types": ["Journal Entry"],
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("create_master_if_missing", result["content"][0]["text"])
		self.assertFalse(frappe.db.exists("DocType", MASTER))


class CompanyDefaultsThroughRealErpnext(DimensionTestCase):
	def test_setting_a_default_to_what_it_already_is_writes_nothing(self):
		"""Idempotency against a real Company, whose save is not a cheap write."""
		current = frappe.db.get_value("Company", self.company, "default_receivable_account")
		if not current:
			self.skipTest(f"{self.company} has no default_receivable_account to re-set")
		data = self.tool_data(
			"set_company_defaults",
			{"company": self.company, "defaults": {"default_receivable_account": current}},
		)
		self.assertEqual(data["changed"], {})
		self.assertEqual(data["unchanged"], ["default_receivable_account"])

	def test_the_type_check_refuses_a_real_wrong_account(self):
		wrong = frappe.db.get_value(
			"Account",
			{"company": self.company, "is_group": 0, "root_type": "Expense", "disabled": 0},
			"name",
		)
		if not wrong:
			self.skipTest(f"{self.company} has no leaf Expense account")
		result = self.tool(
			"set_company_defaults",
			{"company": self.company, "defaults": {"default_receivable_account": wrong}},
		)
		self.assertTrue(result["isError"])
		self.assertIn("Nothing was changed", result["content"][0]["text"])

	def test_every_supported_default_is_a_field_this_erpnext_has(self):
		"""Not a requirement — but a key nobody's ERPNext has is a typo, not tolerance."""
		meta = frappe.get_meta("Company")
		present = [key for key in dimensions.SUPPORTED_COMPANY_DEFAULTS if meta.has_field(key)]
		self.assertTrue(
			len(present) >= len(dimensions.SUPPORTED_COMPANY_DEFAULTS) - 2,
			f"only {len(present)} of {len(dimensions.SUPPORTED_COMPANY_DEFAULTS)} "
			f"supported defaults exist on this Company: {present}",
		)


def _find(nodes, name):
	for node in nodes:
		if node.get("name") == name:
			return node
		found = _find(node.get("children") or [], name)
		if found:
			return found
	return None
