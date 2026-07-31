# SPDX-License-Identifier: MIT
"""In-bench tests for the land, lease, party and document features of v0.11.0.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_realestate

WHAT ONLY A BENCH CAN SHOW HERE. The standalone suite proves the logic against a
double, and a double agrees with whatever it was written to agree with. These are
facts about Frappe and about the operator's own ERPNext:

  * **The three DocTypes this version adds actually migrate**, and Frappe can
    import each one's controller. A DocType JSON the framework refuses is a
    feature that does not exist, and no amount of standalone testing notices —
    which is exactly how v0.7.0 shipped two child tables with no `.py` and broke
    `bench migrate` on a live site.
  * **The controllers' `frappe.throw`s fire in the Desk too.** The four-digit tax
    id rule is the one that matters: the tool refuses nine digits, and so must
    the doctype, because the form is a second door into the same field.
  * **The generated documents survive Frappe's own File storage.** A PDF handed
    to `frappe.get_doc({"doctype": "File", ...})` and read back has been through
    the real filesystem and the real `get_content`; whether the bytes come back
    identical is not something a dict can prove.
  * **`Lease` and `Parcel` do not collide with anything the operator has.** A
    DocType name is global across apps on a site, so the migration test is also
    the collision test.

Everything is created inside the test transaction and rolled back, except the
audit rows the base class cleans up. Tests skip rather than fail where the site
has no Company — that is a site with no ledger, not a broken app.
"""

import frappe

from .test_integration import MCPIntegrationTestCase

V11_TOOLS = (
	"create_parcel",
	"update_parcel",
	"list_parcels",
	"get_parcel",
	"link_parcel_to_asset",
	"create_lease",
	"update_lease",
	"list_leases",
	"get_lease",
	"create_related_party",
	"update_related_party",
	"list_related_parties",
	"get_related_party",
	"generate_quarterly_investment_report",
	"generate_1099_prefill",
)

#: Every DocType v0.11.0 adds. None is a child table — this release deliberately
#: added no table sprawl, which is the design instruction it was built under.
V11_DOCTYPES = ("Parcel", "Lease", "Related Party")

#: Prefixed so nothing here can collide with real land, a real lease or a real
#: person on the operator's site.
PREFIX = "MCPTEST"


class RealEstateIntegrationTestCase(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable(*V11_TOOLS)
		self.company = self.any_company()

	def a_parcel(self, parcel_name=f"{PREFIX} Red Camp", **overrides):
		payload = {
			"owning_entity": self.company,
			"parcel_name": parcel_name,
			"parcel_id": f"{PREFIX}-1N-13E-8-1200",
			"county": "Wasco",
			"state": "OR",
			"acreage": 37.49,
			"use_type": "Orchard",
		}
		payload.update(overrides)
		return self.tool_data("create_parcel", payload)

	def a_lease(self, lease_name=f"{PREFIX} Ground Lease", **overrides):
		payload = {
			"owning_entity": self.company,
			"lease_name": lease_name,
			"direction": "Outbound",
			"lessor": f"{PREFIX} Holdings LLC",
			"lessee": f"{PREFIX} Orchards",
			"effective_date": "2025-01-01",
			"expiration_date": "2027-12-31",
			"rent_amount": 1500,
			"rent_frequency": "Monthly",
		}
		payload.update(overrides)
		return self.tool_data("create_lease", payload)

	def a_party(self, party_name=f"{PREFIX} Tim", relationship="Manager", **overrides):
		payload = {
			"company": self.company,
			"party_name": party_name,
			"party_type": "Individual",
			"relationship_to_company": relationship,
			"effective_date": "2020-06-15",
		}
		payload.update(overrides)
		return self.tool_data("create_related_party", payload)


class DocTypesMigrated(RealEstateIntegrationTestCase):
	def test_every_doctype_this_version_adds_is_on_the_site(self):
		"""Also the name-collision test: a DocType name is global across apps."""
		for doctype in V11_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.db.exists("DocType", doctype), f"{doctype} did not migrate")

	def test_frappe_can_import_every_doctypes_module(self):
		"""A row can exist for a doctype whose Python module cannot be imported.

		That gap is what broke `bench migrate` in v0.7.0, and `load_doctype_module`
		is the frame at the top of that traceback.
		"""
		from frappe.modules.utils import load_doctype_module

		for doctype in V11_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertIsNotNone(load_doctype_module(doctype), f"{doctype} has no module")
				controller = frappe.get_controller(doctype)
				self.assertEqual(
					controller.__name__,
					doctype.replace(" ", ""),
					f"{doctype} fell back to a base class, so its validation does not run",
				)

	def test_they_belong_to_this_app(self):
		for doctype in V11_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertEqual(frappe.db.get_value("DocType", doctype, "module"), "ERPNext MCP")

	def test_none_of_them_is_a_child_table(self):
		"""v0.11.0 added no table sprawl, and the instruction it was built under
		says so explicitly."""
		for doctype in V11_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertFalse(frappe.db.get_value("DocType", doctype, "istable"))

	def test_the_governance_archive_took_the_new_categories(self):
		options = frappe.get_meta("Governance Document").get_field("category").options
		self.assertIn("Tax Filing", options)
		self.assertIn("Lease", options)


class ParcelContract(RealEstateIntegrationTestCase):
	def test_a_parcel_round_trips_through_the_framework(self):
		data = self.a_parcel()
		stored = frappe.get_doc("Parcel", data["name"])
		self.assertEqual(stored.parcel_name, f"{PREFIX} Red Camp")
		self.assertEqual(stored.owning_entity, self.company)
		self.assertAlmostEqual(float(stored.acreage), 37.49, places=2)

	def test_the_docname_carries_the_company_abbreviation(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		self.assertEqual(self.a_parcel()["name"], f"{PREFIX} Red Camp - {abbr}")

	def test_the_controller_refuses_a_duplicate_from_the_desk(self):
		"""The tool refuses it too; this is the other door into the same rule."""
		self.a_parcel()
		duplicate = frappe.new_doc("Parcel")
		duplicate.parcel_name = f"{PREFIX} Red Camp"
		duplicate.owning_entity = self.company
		with self.assertRaises(frappe.ValidationError):
			duplicate.insert(ignore_permissions=True)

	def test_the_controller_refuses_negative_acreage(self):
		doc = frappe.new_doc("Parcel")
		doc.parcel_name = f"{PREFIX} Negative"
		doc.owning_entity = self.company
		doc.acreage = -1
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_only_the_two_intended_roles_can_read_it(self):
		roles = [row.role for row in frappe.get_meta("Parcel").permissions]
		self.assertEqual(sorted(roles), ["Accounts Manager", "System Manager"])

	def test_parcels_are_version_tracked(self):
		"""An appraised value that changed with no record of who changed it is a
		number nobody can defend."""
		for doctype in V11_DOCTYPES:
			with self.subTest(doctype=doctype):
				self.assertTrue(frappe.get_meta(doctype).track_changes)


class LeaseContract(RealEstateIntegrationTestCase):
	def test_a_lease_round_trips_and_annualises_its_rent(self):
		data = self.a_lease()
		self.assertEqual(data["annualised_rent"], 18000.0)
		self.assertEqual(frappe.get_doc("Lease", data["name"]).direction, "Outbound")

	def test_the_controller_refuses_a_terminated_lease_with_no_date(self):
		doc = frappe.new_doc("Lease")
		doc.lease_name = f"{PREFIX} Ended"
		doc.owning_entity = self.company
		doc.direction = "Outbound"
		doc.lessor = "A"
		doc.lessee = "B"
		doc.effective_date = "2025-01-01"
		doc.status = "Terminated"
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_the_controller_refuses_one_party_on_both_sides(self):
		doc = frappe.new_doc("Lease")
		doc.lease_name = f"{PREFIX} Self"
		doc.owning_entity = self.company
		doc.direction = "Outbound"
		doc.lessor = "Same"
		doc.lessee = "same"
		doc.effective_date = "2025-01-01"
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_a_lease_over_a_parcel_is_found_from_the_parcel(self):
		parcel = self.a_parcel()
		self.a_lease(parcel=parcel["name"])
		data = self.tool_data("get_parcel", {"parcel": parcel["name"]})
		self.assertEqual(len(data["leases"]), 1)

	def test_an_attached_lease_document_round_trips_through_file_storage(self):
		"""Frappe's real storage, not a dict pretending to be one."""
		import base64

		payload = b"%PDF-1.4 executed lease\n%%EOF"
		data = self.a_lease(
			f"{PREFIX} With document",
			file_content=base64.b64encode(payload).decode(),
			file_name=f"{PREFIX}-lease.pdf",
		)
		attachment = frappe.get_doc("File", data["attachment"]["name"])
		self.assertEqual(attachment.get_content(), payload)
		self.assertTrue(attachment.is_private)


class RelatedPartyContract(RealEstateIntegrationTestCase):
	def test_a_party_round_trips_with_the_relationship_in_the_docname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr")
		data = self.a_party()
		self.assertEqual(data["name"], f"{PREFIX} Tim - Manager - {abbr}")
		self.assertEqual(frappe.get_doc("Related Party", data["name"]).party_type, "Individual")

	def test_one_person_can_hold_two_roles_on_a_real_site(self):
		manager = self.a_party(relationship="Manager")
		member = self.a_party(relationship="Member")
		self.assertNotEqual(manager["name"], member["name"])
		self.assertTrue(frappe.db.exists("Related Party", manager["name"]))
		self.assertTrue(frappe.db.exists("Related Party", member["name"]))

	def test_the_controller_refuses_a_full_tax_id(self):
		"""The tool refuses nine digits. So must the doctype: the Desk form is a
		second door into the same field, and a rule only one door enforces is a
		rule the other door gets around."""
		doc = frappe.new_doc("Related Party")
		doc.party_name = f"{PREFIX} Careless"
		doc.company = self.company
		doc.party_type = "Individual"
		doc.relationship_to_company = "Vendor"
		doc.effective_date = "2020-01-01"
		doc.tax_id_type = "SSN"
		doc.tax_id_last4 = "123456789"
		with self.assertRaises(frappe.ValidationError) as caught:
			doc.insert(ignore_permissions=True)
		self.assertIn("nine digits", str(caught.exception))

	def test_the_field_is_declared_four_characters_long(self):
		"""Belt to the controller's braces: the column itself cannot hold nine."""
		field = frappe.get_meta("Related Party").get_field("tax_id_last4")
		self.assertEqual(int(field.length or 0), 4)

	def test_the_controller_refuses_the_same_role_twice(self):
		self.a_party()
		duplicate = frappe.new_doc("Related Party")
		duplicate.party_name = f"{PREFIX} Tim"
		duplicate.company = self.company
		duplicate.party_type = "Individual"
		duplicate.relationship_to_company = "Manager"
		duplicate.effective_date = "2021-01-01"
		with self.assertRaises(frappe.ValidationError):
			duplicate.insert(ignore_permissions=True)


class GeneratedDocuments(RealEstateIntegrationTestCase):
	def test_the_report_refuses_a_quarter_that_has_not_closed(self):
		"""No fixture needed: the refusal is the behaviour, and a site with no
		portfolio still gets the right answer to the wrong question."""
		quarter = f"{frappe.utils.getdate().year + 1}-Q4"
		result = self.tool(
			"generate_quarterly_investment_report", {"company": self.company, "quarter": quarter}
		)
		self.assertTrue(result["isError"])
		self.assertIn("quarter not yet closed", result["content"][0]["text"])

	def test_the_prefill_refuses_a_year_that_has_not_ended(self):
		year = frappe.utils.getdate().year
		result = self.tool("generate_1099_prefill", {"company": self.company, "tax_year": year})
		self.assertTrue(result["isError"])
		self.assertIn("has not ended", result["content"][0]["text"])

	def test_a_generated_pdf_survives_frappe_file_storage(self):
		"""The renderer's bytes, through the real File doctype and back."""
		from erpnext_mcp.render.pdf import PdfDocument

		document = PdfDocument(title=f"{PREFIX} round trip", footer="f")
		document.title_block("A REPORT", self.company)
		document.paragraph("Body.")
		payload = document.render()

		attachment = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{PREFIX}-round-trip.pdf",
				"is_private": 1,
				"content": payload,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(frappe.get_doc("File", attachment.name).get_content(), payload)

	def test_a_generated_workbook_survives_frappe_file_storage(self):
		from erpnext_mcp.render.xlsx import Sheet, XlsxWorkbook

		payload = XlsxWorkbook(Sheet(title="s", headers=["a"], rows=[["b"]])).render()
		attachment = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{PREFIX}-round-trip.xlsx",
				"is_private": 1,
				"content": payload,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(frappe.get_doc("File", attachment.name).get_content(), payload)

	def test_the_writable_roots_are_inside_this_site(self):
		"""`output_path` is confined to the site's own file storage, and on a real
		bench those paths have to resolve to somewhere that exists."""
		import os

		from erpnext_mcp.tools import artifacts

		roots = artifacts.allowed_roots()
		self.assertTrue(roots)
		for root in roots:
			with self.subTest(root=root):
				self.assertTrue(os.path.isabs(root))
				self.assertIn(frappe.local.site, root)

	def test_a_path_outside_the_site_is_refused_on_a_real_bench(self):
		from erpnext_mcp.errors import ToolError
		from erpnext_mcp.tools import artifacts

		with self.assertRaises(ToolError):
			artifacts.resolve_output_path("/etc/erpnext-mcp.pdf", "x.pdf")
		with self.assertRaises(ToolError):
			artifacts.resolve_output_path("../../../etc/erpnext-mcp.pdf", "x.pdf")


class CatalogueContract(RealEstateIntegrationTestCase):
	def test_every_new_tool_is_advertised_once_enabled(self):
		body, status = self.rpc("tools/list")
		self.assertEqual(status, 200)
		advertised = {tool["name"] for tool in body["result"]["tools"]}
		for name in V11_TOOLS:
			with self.subTest(tool=name):
				self.assertIn(name, advertised)

	def test_every_new_switch_exists_on_the_settings_form(self):
		meta = frappe.get_meta("ERPNext MCP Settings")
		for name in V11_TOOLS:
			with self.subTest(tool=name):
				self.assertTrue(meta.has_field(f"allow_{name}"))


# ── v0.13.0 schema facts only a bench can confirm ───────────────────────────
class ConveyanceSchemaMigrated(RealEstateIntegrationTestCase):
	"""The child table v0.13.0 adds, and the Parcel field that holds it.

	A conveyance destroys one record and creates another, so the surviving
	parcel's `conveyance_events` table is the ONLY record that the ground ever
	moved. A DocType JSON Frappe refuses, or a child table whose controller
	cannot be imported, is that record not existing — and it is the exact shape
	of the failure that broke `bench migrate` in v0.7.0.
	"""

	def test_the_conveyance_event_doctype_migrated(self):
		self.assertTrue(frappe.db.exists("DocType", "Parcel Conveyance Event"))

	def test_frappe_can_import_its_controller(self):
		from frappe.modules.utils import load_doctype_module

		self.assertIsNotNone(load_doctype_module("Parcel Conveyance Event"))
		self.assertEqual(frappe.get_controller("Parcel Conveyance Event").__name__, "ParcelConveyanceEvent")

	def test_it_is_a_child_table_and_belongs_to_this_app(self):
		self.assertTrue(frappe.db.get_value("DocType", "Parcel Conveyance Event", "istable"))
		self.assertEqual(
			frappe.db.get_value("DocType", "Parcel Conveyance Event", "module"), "ERPNext MCP"
		)

	def test_the_parcel_carries_the_table(self):
		field = frappe.get_meta("Parcel").get_field("conveyance_events")
		self.assertIsNotNone(field, "Parcel has no conveyance_events table")
		self.assertEqual(field.fieldtype, "Table")
		self.assertEqual(field.options, "Parcel Conveyance Event")

	def test_the_parcel_carries_its_own_short_key(self):
		"""The key every Field, Irrigation Zone and Housing Unit docname is
		suffixed with, and the one a conveyance has to preserve."""
		self.assertIsNotNone(frappe.get_meta("Parcel").get_field("abbr"))


class FamilyRegisterSchemaMigrated(RealEstateIntegrationTestCase):
	"""Son, Daughter and `related_to` — added without a data migration.

	The whole migration story is that there isn't one: Child stays in the option
	list so existing records remain valid, and `related_to` arrives empty on every
	record that predates it. Both of those are facts about the DocType JSON, which
	only a real `bench migrate` proves.
	"""

	def test_son_and_daughter_joined_the_relationship_options(self):
		options = frappe.get_meta("Family").get_field("relationship").options
		self.assertIn("Son", options)
		self.assertIn("Daughter", options)

	def test_child_is_still_there_so_existing_records_stay_valid(self):
		self.assertIn("Child", frappe.get_meta("Family").get_field("relationship").options)

	def test_related_to_is_a_data_field_not_a_link(self):
		"""Deliberately Data: the answer is a Family record OR a Related Party
		record OR somebody in neither register, and no Frappe Link points at two
		doctypes. The tools resolve it on read."""
		field = frappe.get_meta("Family").get_field("related_to")
		self.assertIsNotNone(field, "Family has no related_to field")
		self.assertEqual(field.fieldtype, "Data")

	def test_it_is_not_mandatory(self):
		"""Records seeded before v0.13.0 carry no value, and a required field would
		make every one of them unsaveable."""
		self.assertFalse(frappe.get_meta("Family").get_field("related_to").reqd)
