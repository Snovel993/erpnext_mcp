# SPDX-License-Identifier: MIT
"""In-bench tests for the cap table, the event trail, the archive and the assets.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_governance

WHAT ONLY A BENCH CAN SHOW HERE. The standalone suite proves the logic against a
double. These are facts about Frappe and about the operator's ERPNext, and a
double agrees with whatever it was written to agree with:

  * **The six DocTypes this version adds actually migrate**, including the two
    child tables and the `field:asset` naming rule on Asset Cost Profile. A
    DocType JSON that Frappe refuses is a feature that does not exist, and no
    amount of standalone testing notices.
  * **The controllers' `frappe.throw`s fire.** Cap Table Entry refuses a second
    entry for the same member, Asset Cost Profile refuses an allocation that
    does not total 100. Those are the invariants that hold when somebody edits a
    record in the Desk rather than through a tool.
  * **A real File round-trips.** `attach_governance_document` hands Frappe bytes
    and gets back a private File on disk; whether the content comes back
    identical through Frappe's own storage is not something a dict can prove.
  * **ERPNext accepts the Asset and the depreciation entry.** The Asset doctype's
    own validation, and the Journal Entry's, are the two places this feature can
    fail on a real site while passing every test here otherwise.

Everything is created inside the test transaction and rolled back, except the
audit rows the base class cleans up. Tests skip rather than fail where the
operator's site has no Asset Category configured for the company — that is a
site that cannot depreciate anything, not a broken app.
"""

import base64

import frappe

from .test_integration import MCPIntegrationTestCase

GOVERNANCE_TOOLS = (
	"create_cap_table_entry",
	"update_cap_table_entry",
	"list_cap_table",
	"close_cap_table_entry",
	"record_member_event",
	"list_member_events",
	"submit_member_event",
	"attach_governance_document",
	"list_governance_documents",
	"get_governance_document_content",
	"create_asset",
	"update_asset_allocation",
	"link_asset_to_note",
	"run_depreciation_cycle",
	"depreciation_note_alignment_check",
)

#: Prefixed so nothing here can collide with a real member, document or asset on
#: the operator's site.
PREFIX = "MCPTEST"


class GovernanceIntegrationTestCase(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable(*GOVERNANCE_TOOLS)
		self.company = self.any_company()

	def a_member(self, member_id=f"{PREFIX}-01", **overrides):
		payload = {
			"company": self.company,
			"member_id": member_id,
			"legal_entity_name": f"{PREFIX} Holdings LLC",
			"entity_type": "LLC",
			"admission_date": "2020-06-15",
			"ownership_percentage": 100,
		}
		payload.update(overrides)
		return self.tool_data("create_cap_table_entry", payload)


class DocTypesMigrated(GovernanceIntegrationTestCase):
	def test_every_doctype_this_version_adds_is_on_the_site(self):
		for doctype in (
			"Cap Table Entry",
			"Member Event",
			"Governance Document",
			"Asset Cost Profile",
			"Asset Cost Center Allocation",
			"Asset Depreciation Posting",
		):
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("DocType", doctype), f"{doctype} did not migrate")

	def test_the_child_tables_really_are_child_tables(self):
		for doctype in ("Asset Cost Center Allocation", "Asset Depreciation Posting"):
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.get_value("DocType", doctype, "istable"))


class CapTableContract(GovernanceIntegrationTestCase):
	def test_the_docname_is_the_member_id_and_the_company_abbreviation(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		data = self.a_member()
		self.assertEqual(data["name"], f"{PREFIX}-01 - {abbr}")

	def test_the_controller_refuses_a_second_entry_for_the_same_member(self):
		"""Not through the tool — through the Desk path, which is the one a human
		takes and the one the tool's own check cannot cover."""
		self.a_member()
		duplicate = frappe.get_doc(
			{
				"doctype": "Cap Table Entry",
				"member_id": f"{PREFIX}-01",
				"company": self.company,
				"legal_entity_name": "Someone Else",
				"entity_type": "Individual",
				"admission_date": "2021-01-01",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			duplicate.insert()

	def test_retiring_writes_both_the_flag_and_the_event(self):
		member = self.a_member()
		data = self.tool_data(
			"close_cap_table_entry",
			{
				"member": member["name"],
				"withdrawal_date": "2026-06-30",
				"notes": "Interest bought out under the buy-sell agreement.",
			},
		)
		self.assertTrue(frappe.db.get_value("Cap Table Entry", member["name"], "retired"))
		self.assertEqual(
			frappe.db.get_value("Member Event", data["member_event"], "event_type"), "Withdrawal"
		)


class GovernanceArchiveContract(GovernanceIntegrationTestCase):
	CONTENT = b"%PDF-1.4 " + (b"operating agreement " * 64)

	def test_a_real_file_round_trips_through_frappes_own_storage(self):
		filed = self.tool_data(
			"attach_governance_document",
			{
				"company": self.company,
				"category": "Operating Agreement",
				"title": f"{PREFIX} Operating Agreement 2020-06-15",
				"effective_date": "2020-06-15",
				"file_name": f"{PREFIX}-operating-agreement.pdf",
				"file_content": base64.b64encode(self.CONTENT).decode("ascii"),
			},
		)
		self.assertTrue(filed["attachment"]["is_private"])
		read = self.tool_data("get_governance_document_content", {"name": filed["name"]})
		self.assertEqual(base64.b64decode(read["content"]["content_base64"]), self.CONTENT)

	def test_the_amendment_chain_is_written_in_both_directions(self):
		original = self.tool_data(
			"attach_governance_document",
			{
				"company": self.company,
				"category": "Operating Agreement",
				"title": f"{PREFIX} Operating Agreement 2020",
			},
		)
		amendment = self.tool_data(
			"attach_governance_document",
			{
				"company": self.company,
				"category": "Amendment",
				"title": f"{PREFIX} First Amendment 2026",
				"supersedes": original["name"],
			},
		)
		self.assertEqual(
			frappe.db.get_value("Governance Document", original["name"], "superseded_by"),
			amendment["name"],
		)

	def test_the_controller_refuses_a_circular_chain(self):
		first = self.tool_data(
			"attach_governance_document",
			{"company": self.company, "category": "Other", "title": f"{PREFIX} A"},
		)
		second = self.tool_data(
			"attach_governance_document",
			{
				"company": self.company,
				"category": "Other",
				"title": f"{PREFIX} B",
				"supersedes": first["name"],
			},
		)
		doc = frappe.get_doc("Governance Document", first["name"])
		doc.supersedes = second["name"]
		with self.assertRaises(frappe.ValidationError):
			doc.save()


class AssetContract(GovernanceIntegrationTestCase):
	"""Whether ERPNext accepts what create_asset builds, on this operator's site."""

	def setUp(self):
		super().setUp()
		self.category = self._asset_category_with_accounts()
		self.cost_centers = self._leaf_cost_centers(2)

	def _asset_category_with_accounts(self):
		"""An Asset Category configured for this company, or skip.

		Discovered rather than created, for the reason `leaf_accounts` is: the
		point of this app is that it works against the chart the operator already
		has, and a category invented here would prove nothing about theirs.
		"""
		for name in frappe.db.get_all("Asset Category", pluck="name"):
			for row in frappe.get_doc("Asset Category", name).get("accounts") or []:
				if row.get("company") != self.company:
					continue
				if row.get("depreciation_expense_account") and row.get("accumulated_depreciation_account"):
					return name
		self.skipTest(
			f"{self.company} has no Asset Category with a depreciation expense and an "
			"accumulated depreciation account — a site that cannot depreciate anything"
		)

	def _leaf_cost_centers(self, count):
		rows = frappe.db.get_all(
			"Cost Center",
			filters={"company": self.company, "is_group": 0, "disabled": 0},
			pluck="name",
			limit=count,
		)
		if len(rows) < count:
			self.skipTest(f"{self.company} has fewer than {count} postable cost centers")
		return rows

	def _location(self):
		"""ERPNext requires a Location on some versions and not others."""
		if not frappe.db.exists("DocType", "Location"):
			return ""
		existing = frappe.db.get_all("Location", pluck="name", limit=1)
		if existing:
			return existing[0]
		return frappe.get_doc({"doctype": "Location", "location_name": f"{PREFIX} Yard"}).insert().name

	def an_asset(self, **overrides):
		payload = {
			"company": self.company,
			"asset_name": f"{PREFIX} Tractor",
			"item_code": f"{PREFIX}-TRACTOR",
			"asset_category": self.category,
			"purchase_date": "2026-01-01",
			"purchase_amount": 12000,
			"useful_life_months": 12,
			"cost_center_allocation": [
				{"cost_center": self.cost_centers[0], "percentage": 40},
				{"cost_center": self.cost_centers[1], "percentage": 60},
			],
		}
		location = self._location()
		if location:
			payload["location"] = location
		payload.update(overrides)
		return self.tool_data("create_asset", payload)

	def test_erpnext_accepts_the_asset_and_leaves_its_own_depreciation_off(self):
		data = self.an_asset()
		asset = frappe.get_doc("Asset", data["asset"])
		self.assertEqual(int(asset.get("calculate_depreciation") or 0), 0)
		self.assertEqual(int(asset.docstatus or 0), 0)
		self.assertTrue(frappe.db.exists("Asset Cost Profile", data["profile"]))

	def test_the_profile_names_itself_after_the_asset(self):
		data = self.an_asset()
		self.assertEqual(data["profile"], data["asset"])

	def test_the_controller_refuses_an_allocation_that_does_not_total_a_hundred(self):
		data = self.an_asset()
		profile = frappe.get_doc("Asset Cost Profile", data["profile"])
		profile.cost_center_allocation[0].percentage = 10
		with self.assertRaises(frappe.ValidationError):
			profile.save()

	def test_erpnext_accepts_the_depreciation_entry_it_produces(self):
		data = self.an_asset()
		run = self.tool_data(
			"run_depreciation_cycle",
			{"company": self.company, "period_end": "2026-02-28", "asset": data["asset"], "dry_run": False},
		)
		self.assertEqual(run["period_count"], 2)
		entry = frappe.get_doc("Journal Entry", run["journal_entries"][0])
		self.assertEqual(int(entry.docstatus or 0), 0)
		self.assertEqual(len(entry.accounts), 3)
		self.assertAlmostEqual(float(entry.total_debit), float(entry.total_credit), places=2)

	def test_a_second_run_writes_nothing(self):
		data = self.an_asset()
		arguments = {
			"company": self.company,
			"period_end": "2026-02-28",
			"asset": data["asset"],
			"dry_run": False,
		}
		self.tool_data("run_depreciation_cycle", arguments)
		again = self.tool_data("run_depreciation_cycle", arguments)
		self.assertEqual(again["period_count"], 0)
