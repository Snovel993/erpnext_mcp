# SPDX-License-Identifier: MIT
"""Cost centers, accounting dimensions and company defaults.

Three things these tests are really about.

THE DOCNAME, AGAIN. A Cost Center's primary key is built from its own number and
name and is never rebuilt, exactly as an Account's is, so every rename has to
move two things in the right order. `harness.CostCenterDocument` models
ERPNext's `autoname` and its root rule, which is what lets these assert on the
actual key rather than on a field.

THE DIMENSION IS A DOCTYPE. `create_accounting_dimension` can add a DocType and
a Custom Field to the site, and the double is faithful about both: inserting a
DocType makes it creatable, and inserting a Custom Field makes `frappe.get_meta`
report the field. Without that, the end-to-end case — create a dimension, create
a value, put the value on a journal entry line, read it back off the stored
document — could not be written at all, and that case is the point of the whole
feature.

THE TYPE CHECK. `set_company_defaults` refuses a Receivable field pointed at a
non-Receivable account. ERPNext would accept that save and produce party ledgers
that stop reconciling months later, so the refusal is the feature and each one
has a test.
"""

import json

from .fixtures import MAIN, MAIN_ABBR, OTHER, OTHER_ABBR, SeededTestCase, cost_center
from .harness import STORE, frappe, register_doctype

FRESH = "Fresh Orchard Co"
FRESH_ABBR = "FOC"

ROOT_CC = f"{MAIN} - {MAIN_ABBR}"
MAIN_CC = f"Main - {MAIN_ABBR}"
OPERATIONS = f"100 - Operations - {MAIN_ABBR}"
FIELD_WORK = f"110 - Field Work - {MAIN_ABBR}"
RETIRED = f"190 - Retired Depot - {MAIN_ABBR}"

CASH = f"1100 - Cash - {MAIN_ABBR}"
PAYABLES = f"2100 - Accounts Payable - {MAIN_ABBR}"
CURRENT_ASSETS = f"1000 - Current Assets - {MAIN_ABBR}"
SALES = f"4100 - Sales - {MAIN_ABBR}"

ALL_ON = {
	"allow_create_cost_center": 1,
	"allow_update_cost_center": 1,
	"allow_list_cost_centers": 1,
	"allow_create_accounting_dimension": 1,
	"allow_create_dimension_value": 1,
	"allow_set_company_defaults": 1,
	"allow_create_journal_entry": 1,
}


class DimensionToolsTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_receivable_account(self) -> str:
		"""The fixture chart has a Payable but no Receivable; add one.

		Seeded rather than created through `create_account` so these tests do not
		depend on that tool's switch being on — a company-defaults test that
		failed because a chart tool was disabled would be pointing at the wrong
		thing.
		"""
		STORE.seed(
			"Account",
			[
				{
					"name": f"1200 - Accounts Receivable - {MAIN_ABBR}",
					"account_name": "Accounts Receivable",
					"account_number": "1200",
					"parent_account": CURRENT_ASSETS,
					"is_group": 0,
					"root_type": "Asset",
					"account_type": "Receivable",
					"account_currency": "USD",
					"disabled": 0,
					"company": MAIN,
				}
			],
		)
		return f"1200 - Accounts Receivable - {MAIN_ABBR}"

	def a_fresh_company(self) -> str:
		"""A company with no chart and no cost centers, for the root paths."""
		STORE.seed(
			"Company",
			[{"name": FRESH, "abbr": FRESH_ABBR, "default_currency": "USD", "is_group": 0}],
		)
		return FRESH


# ── create_cost_center ──────────────────────────────────────────────────────
class CreateCostCenter(DimensionToolsTestCase):
	def create(self, **overrides):
		payload = {
			"company": MAIN,
			"cost_center_name": "Harvest",
			"cost_center_number": "120",
			"parent_cost_center": OPERATIONS,
		}
		payload.update(overrides)
		return payload

	def test_it_creates_a_leaf_named_the_way_erpnext_names_them(self):
		data = self.tool_data("create_cost_center", self.create())
		self.assertEqual(data["name"], f"120 - Harvest - {MAIN_ABBR}")
		self.assertEqual(data["parent_cost_center"], OPERATIONS)
		self.assertFalse(data["is_group"])
		self.assertTrue(frappe.db.exists("Cost Center", data["name"]))

	def test_the_number_is_optional_unlike_an_account_number(self):
		data = self.tool_data("create_cost_center", self.create(cost_center_number=""))
		self.assertEqual(data["name"], f"Harvest - {MAIN_ABBR}")
		self.assertEqual(data["cost_center_number"], "")

	def test_a_group_is_created_as_a_group(self):
		data = self.tool_data(
			"create_cost_center",
			self.create(cost_center_name="Post-Harvest", cost_center_number="130", is_group=True),
		)
		self.assertTrue(data["is_group"])
		self.assertIn("cannot be posted to", data["next_step"])

	def test_is_group_accepts_the_string_a_model_will_send(self):
		data = self.tool_data(
			"create_cost_center",
			self.create(cost_center_name="Packing", cost_center_number="140", is_group="true"),
		)
		self.assertTrue(data["is_group"])

	def test_the_parent_can_be_named_by_its_number(self):
		data = self.tool_data("create_cost_center", self.create(parent_cost_center="100"))
		self.assertEqual(data["parent_cost_center"], OPERATIONS)

	def test_a_leaf_parent_is_refused_with_the_reason(self):
		message = self.tool_error("create_cost_center", self.create(parent_cost_center=FIELD_WORK))
		self.assertIn("not a group", message)
		self.assertFalse(frappe.db.exists("Cost Center", f"120 - Harvest - {MAIN_ABBR}"))

	def test_a_parent_in_another_company_is_refused(self):
		message = self.tool_error(
			"create_cost_center",
			self.create(parent_cost_center=f"100 - Operations - {OTHER_ABBR}"),
		)
		self.assertIn("belongs to company", message)

	def test_a_number_already_in_use_is_refused_naming_the_holder(self):
		message = self.tool_error("create_cost_center", self.create(cost_center_number="110"))
		self.assertIn(FIELD_WORK, message)
		self.assertIn("Nothing was created", message)

	def test_an_existing_docname_is_refused(self):
		message = self.tool_error(
			"create_cost_center",
			self.create(cost_center_name="Field Work", cost_center_number="110"),
		)
		self.assertIn("already", message)

	def test_omitting_the_parent_is_refused_and_names_the_root(self):
		message = self.tool_error("create_cost_center", self.create(parent_cost_center=""))
		self.assertIn("parent_cost_center is required", message)
		self.assertIn(ROOT_CC, message)

	def test_a_company_with_no_cost_centers_can_be_given_its_root(self):
		company = self.a_fresh_company()
		data = self.tool_data(
			"create_cost_center",
			{"company": company, "cost_center_name": company, "is_group": True},
		)
		self.assertEqual(data["name"], f"{FRESH} - {FRESH_ABBR}")
		self.assertEqual(data["parent_cost_center"], "")

	def test_a_root_has_to_be_named_after_its_company(self):
		company = self.a_fresh_company()
		message = self.tool_error(
			"create_cost_center",
			{"company": company, "cost_center_name": "Everything", "is_group": True},
		)
		self.assertIn(FRESH, message)
		self.assertIn("named exactly after its company", message)

	def test_the_tool_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_cost_center", self.create())
		self.assertIn("allow_create_cost_center", message)

	def test_a_creation_is_audited(self):
		self.tool_data("create_cost_center", self.create())
		row = self.assertAudited("create_cost_center", "Success")
		self.assertIn("120 - Harvest", row["result_summary"])


# ── update_cost_center ──────────────────────────────────────────────────────
class UpdateCostCenter(DimensionToolsTestCase):
	def test_a_rename_moves_the_docname_with_the_field(self):
		data = self.tool_data(
			"update_cost_center",
			{"name": FIELD_WORK, "new_cost_center_name": "Orchard Work"},
		)
		self.assertEqual(data["name"], f"110 - Orchard Work - {MAIN_ABBR}")
		self.assertTrue(data["renamed"])
		self.assertFalse(frappe.db.exists("Cost Center", FIELD_WORK))
		self.assertEqual(
			frappe.db.get_value("Cost Center", data["name"], "cost_center_name"),
			"Orchard Work",
		)

	def test_a_renumber_moves_the_docname_too(self):
		data = self.tool_data(
			"update_cost_center",
			{"name": FIELD_WORK, "new_cost_center_number": "115"},
		)
		self.assertEqual(data["name"], f"115 - Field Work - {MAIN_ABBR}")
		self.assertEqual(data["cost_center_number"], "115")

	def test_renaming_a_group_repoints_its_children(self):
		data = self.tool_data(
			"update_cost_center",
			{"name": OPERATIONS, "new_cost_center_name": "Field Operations"},
		)
		self.assertEqual(
			frappe.db.get_value("Cost Center", FIELD_WORK, "parent_cost_center"),
			data["name"],
		)

	def test_disabling_reports_what_survives(self):
		data = self.tool_data("update_cost_center", {"name": FIELD_WORK, "disabled": True})
		self.assertTrue(data["disabled"])
		self.assertIn("Nothing was deleted", data["warning"])

	def test_disabling_a_group_says_the_children_are_still_live(self):
		data = self.tool_data("update_cost_center", {"name": OPERATIONS, "disabled": True})
		self.assertIn("child cost center", data["warning"])

	def test_re_enabling_works(self):
		data = self.tool_data("update_cost_center", {"name": RETIRED, "disabled": False})
		self.assertFalse(data["disabled"])

	def test_nothing_to_change_is_refused(self):
		message = self.tool_error("update_cost_center", {"name": FIELD_WORK})
		self.assertIn("nothing to change", message)

	def test_the_same_value_again_is_refused(self):
		message = self.tool_error(
			"update_cost_center",
			{"name": FIELD_WORK, "new_cost_center_name": "Field Work"},
		)
		self.assertIn("already has those values", message)

	def test_a_number_already_in_use_is_refused(self):
		message = self.tool_error(
			"update_cost_center",
			{"name": FIELD_WORK, "new_cost_center_number": "190"},
		)
		self.assertIn(RETIRED, message)
		self.assertIn("Nothing was renamed", message)

	def test_the_root_cannot_be_renamed(self):
		message = self.tool_error(
			"update_cost_center",
			{"name": ROOT_CC, "new_cost_center_name": "Head Office"},
		)
		self.assertIn("root cost center", message)
		self.assertTrue(frappe.db.exists("Cost Center", ROOT_CC))

	def test_it_refuses_to_reparent(self):
		message = self.tool_error(
			"update_cost_center",
			{"name": FIELD_WORK, "parent_cost_center": MAIN_CC},
		)
		self.assertIn("cannot reparent", message)

	def test_the_tool_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("update_cost_center", {"name": FIELD_WORK, "disabled": True})
		self.assertIn("allow_update_cost_center", message)


# ── list_cost_centers ───────────────────────────────────────────────────────
class ListCostCenters(DimensionToolsTestCase):
	def test_it_nests_the_tree_under_the_company_root(self):
		data = self.tool_data("list_cost_centers", {"company": MAIN})
		self.assertEqual(len(data["cost_centers"]), 1)
		root = data["cost_centers"][0]
		self.assertEqual(root["name"], ROOT_CC)
		names = sorted(child["name"] for child in root["children"])
		self.assertEqual(names, [OPERATIONS, MAIN_CC])
		operations = next(c for c in root["children"] if c["name"] == OPERATIONS)
		self.assertEqual([c["name"] for c in operations["children"]], [FIELD_WORK])

	def test_disabled_cost_centers_are_left_out_and_counted(self):
		data = self.tool_data("list_cost_centers", {"company": MAIN})
		self.assertEqual(data["flat_count"], 4)
		self.assertEqual(data["disabled_count_excluded"], 1)
		self.assertIn("include_disabled=true", data["note"])

	def test_include_disabled_brings_them_back(self):
		data = self.tool_data("list_cost_centers", {"company": MAIN, "include_disabled": True})
		self.assertEqual(data["flat_count"], 5)
		flat = json.dumps(data["cost_centers"])
		self.assertIn(RETIRED, flat)

	def test_it_reports_the_company_default_cost_center(self):
		data = self.tool_data("list_cost_centers", {"company": MAIN})
		self.assertEqual(data["default_cost_center"], MAIN_CC)

	def test_it_is_scoped_to_one_company(self):
		data = self.tool_data("list_cost_centers", {"company": OTHER})
		self.assertEqual(data["cost_centers"][0]["name"], f"{OTHER} - {OTHER_ABBR}")

	def test_it_is_on_by_default(self):
		self.configure(enabled=1)
		data = self.tool_data("list_cost_centers", {"company": MAIN})
		self.assertTrue(data["cost_centers"])

	def test_it_writes_nothing(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("list_cost_centers", {"company": MAIN})
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		after.pop("MCP Action Log", None)
		before.pop("MCP Action Log", None)
		self.assertEqual(before, after)


# ── create_accounting_dimension ─────────────────────────────────────────────
class CreateAccountingDimension(DimensionToolsTestCase):
	def member(self, **overrides):
		payload = {
			"dimension_name": "Member",
			"create_master_if_missing": True,
			"document_types": ["Journal Entry"],
		}
		payload.update(overrides)
		return payload

	def test_it_generates_the_master_doctype_when_asked(self):
		data = self.tool_data("create_accounting_dimension", self.member())
		self.assertTrue(data["master_doctype_created"])
		self.assertEqual(data["master_doctype"], "Member")
		self.assertTrue(frappe.db.exists("DocType", "Member"))

	def test_the_fieldname_is_the_scrubbed_label(self):
		data = self.tool_data("create_accounting_dimension", self.member(dimension_name="BBCH Stage"))
		self.assertEqual(data["fieldname"], "bbch_stage")

	def test_journal_entry_is_redirected_to_the_line(self):
		data = self.tool_data("create_accounting_dimension", self.member())
		self.assertEqual(data["document_types_applied"], ["Journal Entry Account"])
		self.assertEqual(data["redirected"], {"Journal Entry": ["Journal Entry Account"]})

	def test_the_custom_field_really_lands_on_the_doctype(self):
		self.tool_data("create_accounting_dimension", self.member())
		self.assertTrue(frappe.get_meta("Journal Entry Account").has_field("member"))
		field = frappe.get_meta("Journal Entry Account").get_field("member")
		self.assertEqual(field["fieldtype"], "Link")
		self.assertEqual(field["options"], "Member")

	def test_the_default_document_types_are_the_four_a_posting_goes_through(self):
		# The fixture site has no Purchase Invoice — see ERPNEXT_SCHEMA — so the
		# default set needs it registered first. Which is the point of the next
		# test down.
		register_doctype("Purchase Invoice", [{"fieldname": "supplier", "fieldtype": "Data"}])
		data = self.tool_data(
			"create_accounting_dimension",
			{"dimension_name": "Member", "create_master_if_missing": True},
		)
		self.assertEqual(
			data["document_types_requested"],
			["Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry"],
		)
		self.assertIn("Journal Entry Account", data["document_types_applied"])
		self.assertNotIn("Journal Entry", data["document_types_applied"])
		for doctype in ("Sales Invoice", "Purchase Invoice", "Payment Entry"):
			self.assertTrue(frappe.get_meta(doctype).has_field("member"), doctype)

	def test_a_default_document_type_this_site_lacks_is_refused_not_skipped(self):
		message = self.tool_error(
			"create_accounting_dimension",
			{"dimension_name": "Member", "create_master_if_missing": True},
		)
		self.assertIn("Purchase Invoice", message)
		self.assertNotIn("Member", STORE.tables.get("DocType", {}))

	def test_a_missing_master_without_the_flag_is_refused_and_explains_why(self):
		message = self.tool_error(
			"create_accounting_dimension",
			{"dimension_name": "Member", "document_types": ["Journal Entry"]},
		)
		self.assertIn("create_master_if_missing", message)
		self.assertIn("pointer at a DocType", message)
		self.assertNotIn("Member", STORE.tables.get("DocType", {}))

	def test_an_existing_master_is_wired_up_without_being_recreated(self):
		register_doctype("Crop Block", [{"fieldname": "block_name", "fieldtype": "Data"}])
		data = self.tool_data(
			"create_accounting_dimension",
			{
				"dimension_name": "Block",
				"master_doctype": "Crop Block",
				"document_types": ["Journal Entry"],
			},
		)
		self.assertFalse(data["master_doctype_created"])
		self.assertEqual(data["master_doctype"], "Crop Block")
		self.assertEqual(data["fieldname"], "block")

	def test_a_second_dimension_on_the_same_doctype_is_refused(self):
		self.tool_data("create_accounting_dimension", self.member())
		message = self.tool_error(
			"create_accounting_dimension",
			{
				"dimension_name": "Owner",
				"master_doctype": "Member",
				"document_types": ["Journal Entry"],
			},
		)
		self.assertIn("already points at", message)

	def test_a_duplicate_label_is_refused(self):
		self.tool_data("create_accounting_dimension", self.member())
		register_doctype("Member Two", [{"fieldname": "value", "fieldtype": "Data"}])
		message = self.tool_error(
			"create_accounting_dimension",
			{
				"dimension_name": "Member",
				"master_doctype": "Member Two",
				"document_types": ["Journal Entry"],
			},
		)
		self.assertIn("already exists", message)

	def test_a_single_doctype_cannot_back_a_dimension(self):
		message = self.tool_error(
			"create_accounting_dimension",
			{
				"dimension_name": "Settings Axis",
				"master_doctype": "ERPNext MCP Settings",
				"document_types": ["Journal Entry"],
			},
		)
		self.assertIn("Single DocType", message)

	def test_a_child_table_cannot_back_a_dimension(self):
		message = self.tool_error(
			"create_accounting_dimension",
			{
				"dimension_name": "Line Axis",
				"master_doctype": "Journal Entry Account",
				"document_types": ["Journal Entry"],
			},
		)
		self.assertIn("core or", message)

	def test_a_core_doctype_cannot_back_a_dimension(self):
		message = self.tool_error(
			"create_accounting_dimension",
			{
				"dimension_name": "Company Axis",
				"master_doctype": "Company",
				"document_types": ["Journal Entry"],
			},
		)
		self.assertIn("core or", message)

	def test_a_fieldname_already_taken_by_a_real_field_is_refused(self):
		"""`account` is a Data field on Journal Entry Account; a dimension cannot have it."""
		message = self.tool_error(
			"create_accounting_dimension",
			{
				"dimension_name": "Account",
				"master_doctype": "Crop Block",
				"create_master_if_missing": True,
				"document_types": ["Journal Entry"],
			},
		)
		self.assertIn("already has a field named 'account'", message)
		self.assertNotIn("Crop Block", STORE.tables.get("DocType", {}))

	def test_an_unknown_document_type_is_refused_before_anything_is_written(self):
		message = self.tool_error(
			"create_accounting_dimension",
			self.member(document_types=["Journal Entry", "Nonexistent Doctype"]),
		)
		self.assertIn("Nonexistent Doctype", message)
		self.assertNotIn("Member", STORE.tables.get("DocType", {}))

	def test_the_tool_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_accounting_dimension", self.member())
		self.assertIn("allow_create_accounting_dimension", message)


# ── create_dimension_value ──────────────────────────────────────────────────
class CreateDimensionValue(DimensionToolsTestCase):
	def setUp(self):
		super().setUp()
		self.tool_data(
			"create_accounting_dimension",
			{
				"dimension_name": "Member",
				"create_master_if_missing": True,
				"document_types": ["Journal Entry"],
			},
		)

	def test_the_value_becomes_the_docname(self):
		data = self.tool_data(
			"create_dimension_value",
			{"dimension_name": "Member", "value_name": "Member-01"},
		)
		self.assertEqual(data["name"], "Member-01")
		self.assertEqual(data["master_doctype"], "Member")
		self.assertEqual(data["named_by"], "field:dimension_value")
		self.assertTrue(frappe.db.exists("Member", "Member-01"))

	def test_extra_fields_are_written_verbatim(self):
		data = self.tool_data(
			"create_dimension_value",
			{
				"dimension_name": "Member",
				"value_name": "Member-00",
				"extra_fields": {"description": "Retired", "disabled": 1},
			},
		)
		self.assertEqual(data["extra_fields"]["description"], "Retired")
		self.assertEqual(frappe.db.get_value("Member", "Member-00", "disabled"), 1)

	def test_the_dimension_can_be_found_by_its_master_doctype(self):
		data = self.tool_data(
			"create_dimension_value",
			{"dimension_name": "Member", "value_name": "Member-02"},
		)
		self.assertEqual(data["dimension"], "Member")

	def test_a_duplicate_value_is_refused(self):
		self.tool_data("create_dimension_value", {"dimension_name": "Member", "value_name": "Member-01"})
		message = self.tool_error(
			"create_dimension_value",
			{"dimension_name": "Member", "value_name": "Member-01"},
		)
		self.assertIn("already exists", message)

	def test_an_unknown_dimension_is_refused_with_the_known_ones(self):
		message = self.tool_error(
			"create_dimension_value",
			{"dimension_name": "Block", "value_name": "B-1"},
		)
		self.assertIn("Known dimensions: Member", message)

	def test_an_unknown_extra_field_is_refused_by_name(self):
		message = self.tool_error(
			"create_dimension_value",
			{
				"dimension_name": "Member",
				"value_name": "Member-03",
				"extra_fields": {"nickname": "Ted"},
			},
		)
		self.assertIn("'nickname'", message)
		self.assertFalse(frappe.db.exists("Member", "Member-03"))

	def test_the_tool_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error(
			"create_dimension_value",
			{"dimension_name": "Member", "value_name": "Member-04"},
		)
		self.assertIn("allow_create_dimension_value", message)


# ── set_company_defaults ────────────────────────────────────────────────────
class SetCompanyDefaults(DimensionToolsTestCase):
	def test_it_sets_the_fields_it_was_given(self):
		receivable = self.a_receivable_account()
		data = self.tool_data(
			"set_company_defaults",
			{
				"company": MAIN,
				"defaults": {
					"default_receivable_account": receivable,
					"default_payable_account": PAYABLES,
				},
			},
		)
		self.assertEqual(sorted(data["changed"]), ["default_payable_account", "default_receivable_account"])
		self.assertEqual(frappe.db.get_value("Company", MAIN, "default_receivable_account"), receivable)

	def test_an_account_can_be_named_by_its_number(self):
		self.a_receivable_account()
		data = self.tool_data(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_receivable_account": "1200"}},
		)
		self.assertEqual(
			data["changed"]["default_receivable_account"][1],
			f"1200 - Accounts Receivable - {MAIN_ABBR}",
		)

	def test_a_second_identical_call_changes_nothing(self):
		payload = {"company": MAIN, "defaults": {"default_cash_account": CASH}}
		self.tool_data("set_company_defaults", payload)
		data = self.tool_data("set_company_defaults", payload)
		self.assertEqual(data["changed"], {})
		self.assertEqual(data["unchanged"], ["default_cash_account"])

	def test_the_wrong_account_type_is_refused_with_the_reason(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_receivable_account": CASH}},
		)
		self.assertIn("account_type", message)
		self.assertIn("Receivable", message)
		self.assertIsNone(frappe.db.get_value("Company", MAIN, "default_receivable_account"))

	def test_the_wrong_root_type_is_refused(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_income_account": CASH}},
		)
		self.assertIn("Income", message)

	def test_a_group_account_is_refused(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_cash_account": CURRENT_ASSETS}},
		)
		self.assertIn("group account", message)

	def test_a_disabled_account_is_refused(self):
		frappe.db.set_value("Account", CASH, "disabled", 1)
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_cash_account": CASH}},
		)
		self.assertIn("disabled", message)

	def test_an_account_from_another_company_is_refused(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_cash_account": f"1100 - Cash - {OTHER_ABBR}"}},
		)
		self.assertIn("belongs to company", message)

	def test_an_unsupported_key_is_refused_by_name(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_biscuit_account": CASH}},
		)
		self.assertIn("default_biscuit_account", message)
		self.assertIn("Supported keys", message)

	def test_a_field_this_erpnext_does_not_have_is_refused(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_deferred_expense_account": CASH}},
		)
		self.assertIn("has no field", message)

	def test_one_bad_value_writes_none_of_the_batch(self):
		message = self.tool_error(
			"set_company_defaults",
			{
				"company": MAIN,
				"defaults": {"default_cash_account": CASH, "default_payable_account": SALES},
			},
		)
		self.assertIn("Nothing was changed", message)
		self.assertIsNone(frappe.db.get_value("Company", MAIN, "default_cash_account"))

	def test_round_off_cost_center_takes_a_cost_center(self):
		data = self.tool_data(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"round_off_cost_center": "Main"}},
		)
		self.assertEqual(data["changed"]["round_off_cost_center"][1], MAIN_CC)

	def test_a_group_cost_center_is_refused(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"round_off_cost_center": OPERATIONS}},
		)
		self.assertIn("group cost center", message)

	def test_an_empty_string_clears_a_default(self):
		self.tool_data("set_company_defaults", {"company": MAIN, "defaults": {"default_cash_account": CASH}})
		data = self.tool_data(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_cash_account": ""}},
		)
		self.assertEqual(data["changed"]["default_cash_account"], [CASH, ""])

	def test_an_empty_defaults_object_is_refused(self):
		message = self.tool_error("set_company_defaults", {"company": MAIN, "defaults": {}})
		self.assertIn("non-empty object", message)

	def test_the_tool_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"default_cash_account": CASH}},
		)
		self.assertIn("allow_set_company_defaults", message)


# ── the point of all of it: a dimension on a journal entry line ─────────────
class DimensionsOnAJournalEntry(DimensionToolsTestCase):
	def setUp(self):
		super().setUp()
		self.tool_data(
			"create_accounting_dimension",
			{
				"dimension_name": "Member",
				"create_master_if_missing": True,
				"document_types": ["Journal Entry"],
			},
		)
		self.tool_data("create_dimension_value", {"dimension_name": "Member", "value_name": "Member-01"})

	def entry(self, **overrides):
		payload = {
			"company": MAIN,
			"posting_date": "2026-07-01",
			"user_remark": "Member distribution",
			"accounts": [
				{"account": CASH, "credit": 100, "dimensions": {"member": "Member-01"}},
				{"account": SALES, "debit": 100},
			],
		}
		payload.update(overrides)
		return payload

	def test_the_dimension_persists_on_the_line(self):
		data = self.tool_data("create_journal_entry", self.entry())
		self.assertEqual(data["dimension_fields_set"], ["member"])
		stored = STORE.get_raw("Journal Entry", data["name"])
		self.assertEqual(stored["accounts"][0]["member"], "Member-01")
		self.assertIsNone(stored["accounts"][1].get("member"))

	def test_it_reads_back_through_get_journal_entry(self):
		created = self.tool_data("create_journal_entry", self.entry())
		self.configure(enabled=1, **{**ALL_ON, "allow_get_journal_entry": 1})
		data = self.tool_data("get_journal_entry", {"name": created["name"]})
		self.assertTrue(data["accounts"])

	def test_a_dimension_field_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": CASH, "credit": 100, "dimensions": {"bbch_stage": "BBCH-8"}},
					{"account": SALES, "debit": 100},
				]
			),
		)
		self.assertIn("bbch_stage", message)
		self.assertIn("create_accounting_dimension", message)

	def test_a_value_that_is_not_a_record_is_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": CASH, "credit": 100, "dimensions": {"member": "Member-99"}},
					{"account": SALES, "debit": 100},
				]
			),
		)
		self.assertIn("Member-99", message)
		self.assertIn("create_dimension_value", message)

	def test_dimensions_has_to_be_an_object(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": CASH, "credit": 100, "dimensions": "Member-01"},
					{"account": SALES, "debit": 100},
				]
			),
		)
		self.assertIn("must be an object", message)

	def test_an_unknown_top_level_line_key_is_still_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": CASH, "credit": 100, "member": "Member-01"},
					{"account": SALES, "debit": 100},
				]
			),
		)
		self.assertIn("unsupported field(s): member", message)
		self.assertIn("`dimensions` object", message)

	def test_an_entry_with_no_dimensions_reports_none(self):
		data = self.tool_data(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": CASH, "credit": 100},
					{"account": SALES, "debit": 100},
				]
			),
		)
		self.assertEqual(data["dimension_fields_set"], [])


# ── cost center resolution ──────────────────────────────────────────────────
class CostCenterResolution(DimensionToolsTestCase):
	def test_a_docname_a_number_and_a_name_all_reach_the_same_row(self):
		for spelling in (FIELD_WORK, "110", "Field Work"):
			with self.subTest(spelling=spelling):
				data = self.tool_data(
					"set_company_defaults",
					{"company": MAIN, "defaults": {"round_off_cost_center": spelling}},
				)
				self.assertEqual(data["defaults_now"]["round_off_cost_center"], FIELD_WORK)

	def test_an_ambiguous_name_is_refused_with_the_candidates(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": "", "defaults": {"round_off_cost_center": "Main"}},
		)
		# Two companies on this site, so "Main" alone cannot be resolved — and the
		# company argument is what disambiguates it.
		self.assertIn("multi-company site", message)

	def test_an_unknown_cost_center_points_at_the_listing_tool(self):
		message = self.tool_error(
			"set_company_defaults",
			{"company": MAIN, "defaults": {"round_off_cost_center": "Nowhere"}},
		)
		self.assertIn("list_cost_centers", message)

	def test_the_fixture_helper_agrees_with_the_stored_docnames(self):
		self.assertEqual(cost_center("Field Work"), FIELD_WORK)
		self.assertEqual(cost_center(MAIN), ROOT_CC)
