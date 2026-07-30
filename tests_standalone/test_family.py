# SPDX-License-Identifier: MIT
"""The family register, and the four tools that reach it.

WHY IT EXISTS AT ALL, which is the thing worth remembering. ERPNext resolves a
posting's counterparty as a Dynamic Link THROUGH its party type: `party_type` is
a Link to DocType, so `Family` only works because this app ships a Family
DocType, and `party` only works if the person is a record in it. v0.12.1 shipped
the DocType to stop `bench migrate` dying; v0.12.2 ships the way in, because
until now the only one was `/app/family` in the Desk.

Four things these tests are really about.

THE NAME IS THE DOCNAME AND CANNOT CHANGE. Every journal entry that ever named
somebody points at it, so a rename would orphan those postings. `active=false`
is how somebody is retired, and the tool says how many postings would have been
orphaned — which is the argument for the flag existing.

NO TAX ID LIVES HERE. A transfer below the IRS annual gift exclusion is not
compensation for services: no W-9, no 1099. Where a relative ALSO holds a role
worth disclosing, `related_party` points at the register that keeps four digits
and never more — and `get_family_member` is tested for never returning more.

THE POSTING COUNT IS READ FROM THE LEDGER, NOT KEPT. A second copy would drift
from what actually happened, and the whole point of the count is that it cannot.

THE END-TO-END LOOP CLOSES. Create a member with the tool, post a journal entry
naming them, and read the count back. That is the thing v0.12.0 claimed, v0.12.1
made possible, and this release makes reachable without the Desk.
"""

from .fixtures import ALEX, MAIN, V12TestCase, cash, supplies
from .harness import STORE

ALL_ON = {
	"allow_create_family_member": 1,
	"allow_update_family_member": 1,
	"allow_list_family_members": 1,
	"allow_get_family_member": 1,
	"allow_create_related_party": 1,
	"allow_create_journal_entry": 1,
}


class FamilyTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_member(self, family_name="Marguerite Bramwell", **overrides):
		payload = {"family_name": family_name, "relationship": "Parent"}
		payload.update(overrides)
		return self.tool_data("create_family_member", payload)

	def a_related_party(self, party_name="Marguerite Bramwell"):
		return self.tool_data(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": party_name,
				"party_type": "Individual",
				"relationship_to_company": "Member",
				"effective_date": "2020-01-01",
			},
		)


# ── create_family_member ────────────────────────────────────────────────────
class CreateFamilyMember(FamilyTestCase):
	def test_it_adds_somebody_the_name_being_the_docname(self):
		data = self.a_member()
		self.assertEqual(data["name"], "Marguerite Bramwell")
		self.assertEqual(data["family_member_name"], "Marguerite Bramwell")
		self.assertTrue(STORE.get_raw("Family", "Marguerite Bramwell"))

	def test_it_records_the_relationship(self):
		self.assertEqual(self.a_member(relationship="Sibling")["relationship"], "Sibling")

	def test_it_defaults_to_active(self):
		self.assertTrue(self.a_member()["active"])

	def test_active_false_is_honoured(self):
		self.assertFalse(self.a_member(active=False)["active"])

	def test_the_result_says_how_to_use_it(self):
		data = self.a_member()
		self.assertEqual(data["party_type"], "Family")
		self.assertIn("party_type='Family'", data["next_step"])
		self.assertIn("Marguerite Bramwell", data["next_step"])

	def test_it_says_the_register_holds_no_tax_id_and_why(self):
		notes = " ".join(self.a_member()["notes_on_use"])
		self.assertIn("gift", notes)
		self.assertIn("W-9", notes)
		self.assertIn("Contact or a Supplier", notes)

	def test_a_member_with_no_relationship_is_warned_about_not_refused(self):
		data = self.tool_data("create_family_member", {"family_name": "Unlabelled Cousin"})
		self.assertIsNone(data["relationship"])
		self.assertTrue(any("first question" in note for note in data["notes_on_use"]))

	def test_a_duplicate_name_is_refused(self):
		self.a_member()
		error = self.tool_error("create_family_member", {"family_name": "Marguerite Bramwell"})
		self.assertIn("already on the family register", error)
		self.assertIn("Nothing was created", error)

	def test_the_fixture_member_counts_as_a_duplicate(self):
		"""ALEX is seeded because the v12 GL rows name him — proof the fixture and
		the tool are looking at the same register."""
		self.assertIn("already on the family register", self.tool_error(
			"create_family_member", {"family_name": ALEX}
		))

	def test_a_missing_name_is_refused(self):
		self.assertIn("family_name is required", self.tool_error("create_family_member", {}))

	def test_an_unknown_relationship_is_refused_with_the_list(self):
		error = self.tool_error(
			"create_family_member", {"family_name": "Somebody", "relationship": "Second Cousin Once Removed"}
		)
		self.assertIn("Grandchild", error)

	def test_a_related_party_that_does_not_exist_is_refused(self):
		error = self.tool_error(
			"create_family_member",
			{"family_name": "Somebody", "related_party": "RP-nope"},
		)
		self.assertIn("create_related_party", error)
		self.assertIn("Nothing was created", error)

	def test_a_related_party_that_does_exist_is_linked(self):
		party = self.a_related_party()["name"]
		data = self.a_member(related_party=party)
		self.assertEqual(data["related_party"], party)
		self.assertTrue(data["has_related_party"])

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1)
		self.assertIn(
			"switched off", self.tool_error("create_family_member", {"family_name": "Somebody"})
		)

	def test_it_is_audited(self):
		self.a_member()
		self.assertAudited("create_family_member", "Success")


# ── update_family_member ────────────────────────────────────────────────────
class UpdateFamilyMember(FamilyTestCase):
	def setUp(self):
		super().setUp()
		self.a_member()

	def test_it_changes_the_relationship(self):
		data = self.tool_data(
			"update_family_member", {"family_name": "Marguerite Bramwell", "relationship": "Grandparent"}
		)
		self.assertEqual(data["changed"]["relationship"], ["Parent", "Grandparent"])

	def test_it_links_a_related_party(self):
		party = self.a_related_party()["name"]
		data = self.tool_data(
			"update_family_member", {"family_name": "Marguerite Bramwell", "related_party": party}
		)
		self.assertTrue(data["has_related_party"])

	def test_it_retires_somebody_without_deleting_them(self):
		before = len(STORE.rows("Family"))
		data = self.tool_data(
			"update_family_member", {"family_name": "Marguerite Bramwell", "active": False}
		)
		self.assertFalse(data["active"])
		self.assertEqual(len(STORE.rows("Family")), before)

	def test_retiring_somebody_says_how_many_postings_would_have_been_orphaned(self):
		"""Which is the argument for the flag existing rather than a delete."""
		data = self.tool_data(
			"update_family_member", {"family_name": "Marguerite Bramwell", "active": False}
		)
		self.assertTrue(any("orphan" in warning for warning in data["warnings"]))

	def test_renaming_is_refused_because_postings_point_at_the_docname(self):
		error = self.tool_error(
			"update_family_member",
			{"family_name": "Marguerite Bramwell", "new_family_name": "Marguerite B"},
		)
		self.assertIn("IS the docname", error)
		self.assertIn("orphan", error)

	def test_a_related_party_that_does_not_exist_is_refused(self):
		self.assertIn(
			"no Related Party",
			self.tool_error(
				"update_family_member",
				{"family_name": "Marguerite Bramwell", "related_party": "RP-nope"},
			),
		)

	def test_a_call_that_changes_nothing_is_refused_with_the_options(self):
		error = self.tool_error("update_family_member", {"family_name": "Marguerite Bramwell"})
		self.assertIn("nothing to change", error)
		self.assertIn("relationship", error)

	def test_an_unknown_member_is_refused_with_the_register_named(self):
		self.assertIn(
			"list_family_members",
			self.tool_error("update_family_member", {"family_name": "Nobody", "active": False}),
		)

	def test_the_switch_is_off_by_default(self):
		self.configure(enabled=1)
		self.assertIn(
			"switched off",
			self.tool_error(
				"update_family_member", {"family_name": "Marguerite Bramwell", "active": False}
			),
		)


# ── list_family_members ─────────────────────────────────────────────────────
class ListFamilyMembers(FamilyTestCase):
	def setUp(self):
		super().setUp()
		self.a_member("Marguerite Bramwell", relationship="Parent")
		self.a_member("Rowan Bramwell", relationship="Sibling")

	def test_it_lists_everybody_including_the_seeded_member(self):
		data = self.tool_data("list_family_members")
		names = [member["name"] for member in data["members"]]
		self.assertIn("Marguerite Bramwell", names)
		self.assertIn(ALEX, names)
		self.assertEqual(data["member_count"], len(names))

	def test_it_counts_by_relationship(self):
		counts = self.tool_data("list_family_members")["by_relationship"]
		self.assertEqual(counts["Parent"], 1)
		self.assertEqual(counts["Sibling"], 2)

	def test_it_names_who_has_a_related_party_and_who_does_not(self):
		data = self.tool_data("list_family_members")
		self.assertEqual(data["with_related_party"], [])
		self.assertIn("Marguerite Bramwell", data["without_related_party"])

	def test_linking_one_moves_them_between_the_two_lists(self):
		party = self.a_related_party()["name"]
		self.tool_data(
			"update_family_member", {"family_name": "Marguerite Bramwell", "related_party": party}
		)
		data = self.tool_data("list_family_members")
		self.assertEqual(data["with_related_party"], ["Marguerite Bramwell"])
		self.assertNotIn("Marguerite Bramwell", data["without_related_party"])

	def test_it_says_a_missing_related_party_is_usually_fine(self):
		"""It is a gap for a member, a lessor or a trustee — not for a relative
		who only receives transfers. A list that read as forty problems would be
		a list nobody acts on."""
		note = self.tool_data("list_family_members")["note"]
		self.assertIn("not a gap for most", note)
		self.assertIn("trustee", note)

	def test_it_filters_by_active(self):
		self.tool_data("update_family_member", {"family_name": "Rowan Bramwell", "active": False})
		data = self.tool_data("list_family_members", {"active": True})
		self.assertNotIn("Rowan Bramwell", [member["name"] for member in data["members"]])

	def test_it_filters_by_relationship(self):
		data = self.tool_data("list_family_members", {"relationship": "Parent"})
		self.assertEqual([member["name"] for member in data["members"]], ["Marguerite Bramwell"])

	def test_it_names_who_has_no_relationship_recorded(self):
		self.tool_data("create_family_member", {"family_name": "Unlabelled Cousin"})
		self.assertIn("Unlabelled Cousin", self.tool_data("list_family_members")["without_relationship"])

	def test_it_is_read_only(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("list_family_members")
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		before.pop("MCP Action Log", None)
		after.pop("MCP Action Log", None)
		self.assertEqual(before, after)

	def test_it_is_on_by_default(self):
		self.configure(enabled=1)
		self.tool_data("list_family_members")


# ── get_family_member ───────────────────────────────────────────────────────
class GetFamilyMember(FamilyTestCase):
	def test_it_counts_the_postings_that_name_them(self):
		"""Read from the ledger rather than kept here, so the count cannot drift
		from what actually happened. ALEX has two seeded Family postings."""
		data = self.tool_data("get_family_member", {"family_name": ALEX})
		self.assertEqual(data["posting_count"], 2)
		self.assertEqual(data["net_amount"], 7500.0)
		self.assertEqual(data["first_posting"], "2025-05-10")
		self.assertEqual(data["last_posting"], "2025-10-10")

	def test_it_names_the_companies_the_postings_are_on(self):
		self.assertEqual(self.tool_data("get_family_member", {"family_name": ALEX})["companies"], [MAIN])

	def test_somebody_with_no_postings_reports_zero_rather_than_nothing(self):
		self.a_member()
		data = self.tool_data("get_family_member", {"family_name": "Marguerite Bramwell"})
		self.assertEqual(data["posting_count"], 0)
		self.assertIsNone(data["first_posting"])

	def test_it_returns_the_related_party_detail(self):
		party = self.a_related_party()["name"]
		self.a_member(related_party=party)
		detail = self.tool_data("get_family_member", {"family_name": "Marguerite Bramwell"})[
			"related_party_detail"
		]
		self.assertEqual(detail["party_name"], "Marguerite Bramwell")
		self.assertEqual(detail["relationship_to_company"], "Member")

	def test_it_never_returns_more_than_four_digits_of_a_taxpayer_id(self):
		"""The same rule get_related_party keeps, kept here too because this is a
		second door onto the same field."""
		self.tool_data(
			"create_related_party",
			{
				"company": MAIN,
				"party_name": "Marguerite Bramwell",
				"party_type": "Individual",
				"relationship_to_company": "Member",
				"effective_date": "2020-01-01",
				"tax_id_type": "SSN",
				"tax_id_last4": "6789",
			},
		)
		party = STORE.rows("Related Party")[-1]["name"]
		self.a_member(related_party=party)
		data = self.tool_data("get_family_member", {"family_name": "Marguerite Bramwell"})
		self.assertEqual(data["related_party_detail"]["tin_last4"], "6789")
		self.assertNotIn("123456789", str(data))

	def test_no_related_party_is_reported_as_usually_fine(self):
		self.a_member()
		notes = self.tool_data("get_family_member", {"family_name": "Marguerite Bramwell"})[
			"compliance_notes"
		]
		self.assertTrue(any("only receives transfers" in note for note in notes))

	def test_a_retired_member_with_postings_says_the_record_stays_on_purpose(self):
		self.tool_data("update_family_member", {"family_name": ALEX, "active": False})
		notes = self.tool_data("get_family_member", {"family_name": ALEX})["compliance_notes"]
		self.assertTrue(any("postings keep resolving" in note for note in notes))

	def test_an_unknown_member_is_refused_with_the_register_named(self):
		self.assertIn(
			"list_family_members", self.tool_error("get_family_member", {"family_name": "Nobody"})
		)


# ── the loop this release closes ────────────────────────────────────────────
class EndToEnd(FamilyTestCase):
	"""Create a member with the tool, post to them, read the count back.

	v0.12.0 claimed a Family posting worked and could not deliver it; v0.12.1
	made it possible but only from the Desk; this is the release where the whole
	thing happens over MCP.
	"""

	def test_a_member_created_here_can_be_posted_to(self):
		self.a_member("Rowan Bramwell", relationship="Sibling")
		result = self.tool(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "quarterly family transfer",
				"accounts": [
					{
						"account": supplies(),
						"debit": 1200,
						"party_type": "Family",
						"party": "Rowan Bramwell",
					},
					{"account": cash(), "credit": 1200},
				],
			},
		)
		self.assertFalse(result.get("isError"), result["content"][0]["text"])

	def test_posting_to_somebody_not_on_the_register_is_still_refused(self):
		"""The Dynamic Link doing its job. Adding the tool did not loosen it."""
		result = self.tool(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "transfer",
				"accounts": [
					{
						"account": supplies(),
						"debit": 1200,
						"party_type": "Family",
						"party": "Never Added",
					},
					{"account": cash(), "credit": 1200},
				],
			},
		)
		self.assertTrue(result.get("isError"))

	def test_the_1099_prefill_still_excludes_family_postings(self):
		"""Adding a way to create members must not change what the pre-fill does
		with them. Family stays excluded and stays counted."""
		self.configure(enabled=1, **ALL_ON, allow_generate_1099_prefill=1)
		data = self.tool_data(
			"generate_1099_prefill", {"company": MAIN, "tax_year": 2025, "dry_run": True}
		)
		self.assertEqual(data["excluded"]["family_party_postings"], 2)
		self.assertNotIn(ALEX, [row["recipient"] for row in data["recipients"]])
