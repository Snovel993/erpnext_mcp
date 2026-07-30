# SPDX-License-Identifier: MIT
"""The related-party register.

THE TEST THAT MATTERS MOST IS THE ONE ABOUT NINE DIGITS. A field that quietly
stores whatever it is handed is a field that will one day hold a full SSN, and
the difference between a site holding four digits and a site holding nine is the
difference between an inconvenience and a notifiable breach. There is a test that
a nine-digit value is REFUSED rather than truncated, one that the refusal names
the four digits to send instead, one that the controller refuses it too (the Desk
form is another door into the same field), and one that a linked Supplier's full
`tax_id` never reaches a tool result.

A PERSON IS NOT ONE ROW. Somebody who is both Manager and Member of an LLC is two
entries under two instruments, so the docname carries the relationship. The tests
assert both entries can exist, that the same role twice cannot, and that a bare
name matching two capacities is refused with both docnames rather than resolved
to whichever came first.

NOTHING IS DELETED WHEN A RELATIONSHIP ENDS. `end_date` is set and the row stays,
because the transactions it explains are still in the ledger and a prior year's
disclosure schedule still needs to know who was who.
"""

from .fixtures import COOPER, MAIN, MAIN_ABBR, MEMBER_ONE, OTHER, V11TestCase

ALL_ON = {
	"allow_create_related_party": 1,
	"allow_update_related_party": 1,
	"allow_list_related_parties": 1,
	"allow_get_related_party": 1,
	"allow_create_cap_table_entry": 1,
	"allow_attach_governance_document": 1,
	"allow_create_parcel": 1,
	"allow_create_lease": 1,
}


class PartyTestCase(V11TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_party(self, party_name="Tim Polehn", relationship="Manager", **overrides):
		payload = {
			"company": MAIN,
			"party_name": party_name,
			"party_type": "Individual",
			"relationship_to_company": relationship,
			"effective_date": "2020-06-15",
		}
		payload.update(overrides)
		return self.tool_data("create_related_party", payload)


# ── create_related_party ────────────────────────────────────────────────────
class CreateRelatedParty(PartyTestCase):
	def test_the_docname_carries_the_relationship_as_well_as_the_company(self):
		data = self.a_party()
		self.assertEqual(data["name"], f"Tim Polehn - Manager - {MAIN_ABBR}")
		self.assertEqual(data["relationship_to_company"], "Manager")
		self.assertTrue(data["current"])
		self.assertTrue(data["disclosable"])

	def test_one_person_may_hold_two_roles(self):
		"""A member-manager is the ordinary case in an LLC, and one row with one
		Select cannot hold it."""
		manager = self.a_party(relationship="Manager")
		member = self.a_party(relationship="Member")
		self.assertNotEqual(manager["name"], member["name"])
		data = self.tool_data("list_related_parties", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["distinct_people"], 1)

	def test_the_same_role_twice_is_refused_naming_the_existing_entry(self):
		self.a_party()
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Tim Polehn",
				"party_type": "Individual",
				"relationship_to_company": "Manager",
				"effective_date": "2021-01-01",
			},
		)
		self.assertIn(f"Tim Polehn - Manager - {MAIN_ABBR}", message)
		self.assertIn("second role for the same person is a second entry", message)
		self.assertIn("Nothing was created", message)

	def test_an_effective_date_is_required(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Nobody",
				"party_type": "Individual",
				"relationship_to_company": "Other",
			},
		)
		self.assertIn("effective_date is required", message)

	def test_an_end_date_before_the_start_is_refused(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Backwards",
				"party_type": "Individual",
				"relationship_to_company": "Other",
				"effective_date": "2020-01-01",
				"end_date": "2019-01-01",
			},
		)
		self.assertIn("before effective_date", message)

	def test_an_unknown_party_type_is_refused_with_the_list_off_the_doctype(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Nobody",
				"party_type": "Robot",
				"relationship_to_company": "Other",
				"effective_date": "2020-01-01",
			},
		)
		self.assertIn("party_type must be one of", message)
		self.assertIn("Family Member", message)

	def test_an_unknown_relationship_is_refused(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Nobody",
				"party_type": "Individual",
				"relationship_to_company": "Confidant",
				"effective_date": "2020-01-01",
			},
		)
		self.assertIn("relationship_to_company must be one of", message)
		self.assertIn("Trustee", message)

	def test_a_party_with_no_tax_id_is_warned_that_no_1099_can_be_issued(self):
		data = self.a_party()
		self.assertIn("signed W-9", data["warning"])

	def test_it_suggests_filing_the_instrument_that_establishes_the_relationship(self):
		data = self.a_party()
		self.assertIn("attach_governance_document", data["next_step"])

	def test_a_governing_document_from_another_company_is_refused(self):
		archive = self.tool_data(
			"attach_governance_document",
			{"company": MAIN, "title": "OML Operating Agreement 2020-06-15", "category": "Operating Agreement"},
		)
		message = self.tool_error(
			"create_related_party",
			{
				"company": OTHER,
				"party_name": "Somebody",
				"party_type": "Individual",
				"relationship_to_company": "Member",
				"effective_date": "2020-01-01",
				"governing_document": archive["name"],
			},
		)
		self.assertIn("belongs to", message)

	def test_it_links_to_the_member_register_without_copying_the_name(self):
		"""The cap table already carries the legal name, so the link leaks nothing
		new — it just stops the two drifting apart."""
		member = self.tool_data(
			"create_cap_table_entry",
			{
				"company": MAIN,
				"member_id": MEMBER_ONE,
				"legal_entity_name": "Tim Polehn",
				"entity_type": "Individual",
				"admission_date": "2020-06-15",
				"ownership_percentage": 50,
			},
		)
		data = self.a_party(relationship="Member", cap_table_entry=member["name"])
		self.assertEqual(data["cap_table_entry"], member["name"])

	def test_a_supplier_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Ghost Vendor",
				"party_type": "LLC",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"supplier": "No Such Vendor",
			},
		)
		self.assertIn("no Supplier named", message)


# ── the four-digits rule ────────────────────────────────────────────────────
class TaxIdentity(PartyTestCase):
	def test_four_digits_are_stored(self):
		data = self.a_party(tax_id_type="SSN", tax_id_last4="6789")
		self.assertEqual(data["tax_id_type"], "SSN")
		self.assertEqual(data["tax_id_last4"], "6789")
		self.assertNotIn("warning", data)

	def test_nine_digits_are_refused_and_the_refusal_names_the_four_to_send(self):
		"""Not truncated, not masked, not accepted with a warning. Refused."""
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Careless",
				"party_type": "Individual",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"tax_id_type": "SSN",
				"tax_id_last4": "123456789",
			},
		)
		self.assertIn("nine digits", message)
		self.assertIn("'6789'", message)
		self.assertIn("signed W-9", message)
		self.assertIn("Nothing was created", message)

	def test_a_hyphenated_full_ssn_is_caught_too(self):
		"""`123-45-6789` is nine digits with punctuation, which is the shape a
		person actually pastes."""
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Careless",
				"party_type": "Individual",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"tax_id_type": "SSN",
				"tax_id_last4": "123-45-6789",
			},
		)
		self.assertIn("nine digits", message)

	def test_the_wrong_number_of_digits_is_refused(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Careless",
				"party_type": "Individual",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"tax_id_type": "EIN",
				"tax_id_last4": "12345",
			},
		)
		self.assertIn("exactly four digits", message)

	def test_letters_are_refused(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Careless",
				"party_type": "Individual",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"tax_id_type": "EIN",
				"tax_id_last4": "abcd",
			},
		)
		self.assertIn("must be four digits", message)

	def test_a_type_with_no_digits_is_refused(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Careless",
				"party_type": "Individual",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"tax_id_type": "SSN",
			},
		)
		self.assertIn("leave tax_id_type as None", message)

	def test_digits_with_no_type_are_refused(self):
		message = self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Careless",
				"party_type": "Individual",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"tax_id_last4": "6789",
			},
		)
		self.assertIn("Say which kind of number it is", message)

	def test_the_controller_refuses_nine_digits_too(self):
		"""The Desk form is another door into the same field, and a rule only one
		door enforces is a rule the other door gets around."""
		import frappe

		doc = frappe.new_doc("Related Party")
		doc.party_name = "Direct"
		doc.company = MAIN
		doc.party_type = "Individual"
		doc.relationship_to_company = "Vendor"
		doc.effective_date = "2020-01-01"
		doc.tax_id_type = "SSN"
		doc.tax_id_last4 = "123456789"
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("nine digits", str(caught.exception))

	def test_a_linked_suppliers_full_tax_id_never_reaches_a_result(self):
		import frappe

		frappe.db.set_value("Supplier", COOPER, "tax_id", "93-1234567")
		self.a_party("Cooper Family Orchards", relationship="Vendor", party_type="Partnership", supplier=COOPER)
		data = self.tool_data("get_related_party", {"party": f"Cooper Family Orchards - Vendor - {MAIN_ABBR}"})
		self.assertEqual(data["supplier_detail"]["tax_id"], "on file")
		self.assertNotIn("93-1234567", str(data))


# ── update_related_party ────────────────────────────────────────────────────
class UpdateRelatedParty(PartyTestCase):
	def test_it_ends_a_relationship_without_deleting_the_row(self):
		import frappe

		self.a_party()
		data = self.tool_data(
			"update_related_party",
			{"party": f"Tim Polehn - Manager - {MAIN_ABBR}", "end_date": "2026-01-31"},
		)
		self.assertEqual(data["end_date"], "2026-01-31")
		self.assertFalse(data["current"])
		self.assertTrue(frappe.db.exists("Related Party", f"Tim Polehn - Manager - {MAIN_ABBR}"))
		self.assertIn("never deleted", data["note"])

	def test_renaming_is_refused_because_the_docname_is_built_from_the_key(self):
		self.a_party()
		message = self.tool_error(
			"update_related_party",
			{"party": f"Tim Polehn - Manager - {MAIN_ABBR}", "party_name": "T. Polehn"},
		)
		self.assertIn("cannot be changed", message)
		self.assertIn("create_related_party", message)

	def test_changing_the_role_is_refused_as_a_new_relationship(self):
		self.a_party()
		message = self.tool_error(
			"update_related_party",
			{"party": f"Tim Polehn - Manager - {MAIN_ABBR}", "relationship_to_company": "Member"},
		)
		self.assertIn("a change of role is a new relationship", message)

	def test_changing_the_company_is_refused(self):
		self.a_party()
		message = self.tool_error(
			"update_related_party",
			{"party": f"Tim Polehn - Manager - {MAIN_ABBR}", "company": OTHER},
		)
		self.assertIn("cannot be changed", message)

	def test_an_end_date_before_the_start_is_refused(self):
		self.a_party()
		message = self.tool_error(
			"update_related_party",
			{"party": f"Tim Polehn - Manager - {MAIN_ABBR}", "end_date": "2019-01-01"},
		)
		self.assertIn("before effective_date", message)
		self.assertIn("Nothing was changed", message)

	def test_an_empty_end_date_clears_it(self):
		self.a_party(end_date="2026-01-31")
		data = self.tool_data(
			"update_related_party", {"party": f"Tim Polehn - Manager - {MAIN_ABBR}", "end_date": ""}
		)
		self.assertIsNone(data["end_date"])
		self.assertTrue(data["current"])

	def test_a_no_op_update_is_refused(self):
		self.a_party()
		message = self.tool_error(
			"update_related_party",
			{"party": f"Tim Polehn - Manager - {MAIN_ABBR}", "party_type": "Individual"},
		)
		self.assertIn("nothing to change", message)

	def test_a_tax_id_can_be_added_later(self):
		self.a_party()
		data = self.tool_data(
			"update_related_party",
			{
				"party": f"Tim Polehn - Manager - {MAIN_ABBR}",
				"tax_id_type": "SSN",
				"tax_id_last4": "6789",
			},
		)
		self.assertEqual(data["tax_id_last4"], "6789")
		self.assertEqual(data["changes"]["tax_id_type"], ["None", "SSN"])

	def test_a_nine_digit_update_is_refused_the_same_way(self):
		self.a_party()
		message = self.tool_error(
			"update_related_party",
			{
				"party": f"Tim Polehn - Manager - {MAIN_ABBR}",
				"tax_id_type": "SSN",
				"tax_id_last4": "123456789",
			},
		)
		self.assertIn("nine digits", message)
		self.assertIn("Nothing was changed", message)


# ── list_related_parties ────────────────────────────────────────────────────
class ListRelatedParties(PartyTestCase):
	def a_register(self):
		self.a_party("Tim Polehn", "Manager")
		self.a_party("Tim Polehn", "Member")
		self.a_party("Donella Polehn", "Member")
		self.a_party("Molly Polehn", "Member", end_date="2025-03-20")
		self.a_party("Antoine Tissot", "Other", party_type="Individual")

	def test_it_counts_relationships_and_people_separately(self):
		self.a_register()
		data = self.tool_data("list_related_parties", {"company": MAIN})
		self.assertEqual(data["count"], 5)
		self.assertEqual(data["distinct_people"], 4)

	def test_ended_relationships_are_listed_by_default(self):
		self.a_register()
		data = self.tool_data("list_related_parties", {"company": MAIN})
		self.assertEqual(data["ended_count"], 1)
		self.assertEqual(data["current_count"], 4)

	def test_current_only_hides_them(self):
		self.a_register()
		data = self.tool_data("list_related_parties", {"company": MAIN, "current_only": True})
		self.assertEqual(data["count"], 4)
		self.assertTrue(all(party["current"] for party in data["parties"]))

	def test_it_buckets_by_relationship_and_party_type(self):
		self.a_register()
		data = self.tool_data("list_related_parties", {"company": MAIN})
		self.assertEqual(data["by_relationship"]["Member"], 3)
		self.assertEqual(data["by_party_type"]["Individual"], 5)

	def test_it_names_the_relationships_with_no_paper_behind_them(self):
		self.a_register()
		data = self.tool_data("list_related_parties", {"company": MAIN})
		self.assertEqual(len(data["without_governing_document"]), 5)
		self.assertIn("examiner asks for", data["warning"])

	def test_filtering_by_relationship(self):
		self.a_register()
		data = self.tool_data(
			"list_related_parties", {"company": MAIN, "relationship_to_company": "Manager"}
		)
		self.assertEqual([party["party_name"] for party in data["parties"]], ["Tim Polehn"])

	def test_an_empty_register_answers_rather_than_refusing(self):
		data = self.tool_data("list_related_parties", {"company": MAIN})
		self.assertEqual(data["count"], 0)
		self.assertEqual(data["distinct_people"], 0)

	def test_a_limit_that_hides_part_of_the_register_warns(self):
		self.a_register()
		data = self.tool_data("list_related_parties", {"company": MAIN, "limit": 2})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["total_in_register"], 5)
		self.assertIn("Raise `limit`", data["warning"])


# ── get_related_party ───────────────────────────────────────────────────────
class GetRelatedParty(PartyTestCase):
	def test_it_lists_the_persons_other_roles(self):
		self.a_party("Tim Polehn", "Manager")
		self.a_party("Tim Polehn", "Member")
		data = self.tool_data("get_related_party", {"party": f"Tim Polehn - Manager - {MAIN_ABBR}"})
		self.assertEqual(data["other_roles"], [f"Tim Polehn - Member - {MAIN_ABBR}"])

	def test_a_bare_name_in_two_capacities_is_refused_with_both_docnames(self):
		"""Resolving to whichever came first would answer a question nobody asked."""
		self.a_party("Tim Polehn", "Manager")
		self.a_party("Tim Polehn", "Member")
		message = self.tool_error("get_related_party", {"party": "Tim Polehn"})
		self.assertIn("registered in 2 capacities", message)
		self.assertIn(f"Tim Polehn - Manager - {MAIN_ABBR}", message)
		self.assertIn(f"Tim Polehn - Member - {MAIN_ABBR}", message)

	def test_a_bare_name_held_once_resolves(self):
		self.a_party("Donella Polehn", "Member")
		data = self.tool_data("get_related_party", {"party": "Donella Polehn"})
		self.assertEqual(data["relationship_to_company"], "Member")

	def test_it_finds_the_parcels_and_leases_pointing_at_the_party(self):
		party = self.a_party("Highland Ltd Liability Co.", "Other", party_type="LLC")
		self.tool_data(
			"create_parcel",
			{"owning_entity": MAIN, "parcel_name": "Red Camp", "title_holder": party["name"]},
		)
		self.tool_data(
			"create_lease",
			{
				"owning_entity": MAIN,
				"lease_name": "Ground lease",
				"direction": "Inbound",
				"lessor": "Highland Ltd Liability Co.",
				"lessee": MAIN,
				"effective_date": "2025-01-01",
				"counterparty": party["name"],
			},
		)
		data = self.tool_data("get_related_party", {"party": party["name"]})
		self.assertEqual(data["parcels_titled"], [f"Red Camp - {MAIN_ABBR}"])
		self.assertEqual(data["leases_as_counterparty"], [f"Ground lease - {MAIN_ABBR}"])

	def test_an_unknown_party_is_refused_with_the_tool_that_lists_them(self):
		message = self.tool_error("get_related_party", {"party": "Nobody"})
		self.assertIn("no Related Party called", message)
		self.assertIn("list_related_parties", message)


# ── switches ────────────────────────────────────────────────────────────────
class Switches(PartyTestCase):
	def test_the_mutating_tools_ship_off(self):
		self.configure(enabled=1)
		for tool, arguments in (
			(
				"create_related_party",
				{
					"company": MAIN,
					"party_name": "X",
					"party_type": "Individual",
					"relationship_to_company": "Other",
					"effective_date": "2020-01-01",
				},
			),
			("update_related_party", {"party": "X", "address": "Y"}),
		):
			with self.subTest(tool=tool):
				message = self.tool_error(tool, arguments)
				self.assertIn(f"allow_{tool}", message)
				self.assertIn("switched off", message)

	def test_the_read_tools_ship_on(self):
		self.configure(enabled=1)
		self.assertEqual(self.tool_data("list_related_parties", {"company": MAIN})["count"], 0)

	def test_the_tools_disappear_when_the_doctype_is_missing(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Related Party")
		message = self.tool_error("list_related_parties", {"company": MAIN})
		self.assertIn("not available on this site", message)
		self.assertIn("Related Party DocType", message)

	def test_creation_and_refusal_are_both_audited(self):
		self.a_party()
		self.assertAudited("create_related_party", "Success")
		self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Tim Polehn",
				"party_type": "Individual",
				"relationship_to_company": "Manager",
				"effective_date": "2020-06-15",
			},
		)
		self.assertAudited("create_related_party", "Error")

	def test_a_refused_creation_leaves_nothing_behind(self):
		import frappe

		self.tool_error(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Careless",
				"party_type": "Individual",
				"relationship_to_company": "Vendor",
				"effective_date": "2020-01-01",
				"tax_id_type": "SSN",
				"tax_id_last4": "123456789",
			},
		)
		self.assertFalse(frappe.db.exists("Related Party", f"Careless - Vendor - {MAIN_ABBR}"))
