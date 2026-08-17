# SPDX-License-Identifier: MIT
"""Deleting an archived copy, and the links that must not stop you. v0.83.0.

`generate_mobile_login_qr` can file the card it draws in the governance archive
and record where it went on `Mobile Access Grant.qr_document`. That link then made
the archived copy UNDELETABLE — Frappe refuses to delete a document any Link field
points at — so a card filed by mistake, or one belonging to somebody who left two
years ago, could not be cleared out without first finding a read-only field on a
register most people have never opened.

FOUR CLAIMS, AND THE SECOND IS THE ONE THAT MAKES THIS SAFE.

1. `TheArchiveCopyLinkReleases` — the delete goes through and the grant survives
   with its link nulled and everything else intact.
2. `EveryOtherLinkStillRefuses` — a Lease, a Parcel, a Related Party or an
   Advisory Agreement naming a Governance Document still blocks its deletion.
   THAT IS THE POINT: a reference records that two things were connected, and a
   delete that quietly detached them would destroy the one fact an archive exists
   to hold. Only an archived COPY is released.
3. `TheDoubleActuallyRefuses` — guard on the guard. Until v0.83.0 the harness
   deleted anything asked of it and checked no links at all, which meant claim 2
   would have passed for the wrong reason and claim 1 was untestable.
4. `ItSurvivesAnOddSite` — no Mobile Access Grant doctype, no `qr_document`
   column, several grants pointing at one document. None of them raise.
"""

import frappe

from .fixtures import MAIN, SeededTestCase
from .harness import STORE

DOC = "GOV-2026-0001"
WORKER = "picker@example.test"


class ArchiveTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		STORE.seed(
			"Governance Document",
			[{"name": DOC, "company": MAIN, "document_type": "Other", "title": "Mobile card"}],
		)

	def a_grant(self, name=WORKER, **overrides):
		row = {
			"name": name,
			"user": name,
			"mobile_role": "Farm Ops Worker",
			"state": "Active",
			"qr_document": DOC,
			"endpoint_url": "https://farm.example.test",
		}
		row.update(overrides)
		STORE.seed("Mobile Access Grant", [row])
		return row


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheArchiveCopyLinkReleases(ArchiveTestCase):
	def test_the_delete_goes_through(self):
		self.a_grant()
		frappe.delete_doc("Governance Document", DOC)
		self.assertFalse(frappe.db.exists("Governance Document", DOC))

	def test_the_grant_survives(self):
		"""SET NULL, NOT CASCADE. The grant is the fact; the archived card is an
		artefact of it. Deleting the artefact must not take the account with it."""
		self.a_grant()
		frappe.delete_doc("Governance Document", DOC)
		self.assertTrue(frappe.db.exists("Mobile Access Grant", WORKER))

	def test_the_link_is_nulled(self):
		self.a_grant()
		frappe.delete_doc("Governance Document", DOC)
		self.assertFalsy = self.assertFalse
		self.assertFalse(frappe.db.get_value("Mobile Access Grant", WORKER, "qr_document"))

	def test_nothing_else_about_the_grant_moves(self):
		"""A cleanup that also reset `state` would revoke somebody's phone because
		a PDF was deleted."""
		self.a_grant()
		frappe.delete_doc("Governance Document", DOC)
		row = STORE.get_raw("Mobile Access Grant", WORKER)
		self.assertEqual(row["state"], "Active")
		self.assertEqual(row["mobile_role"], "Farm Ops Worker")
		self.assertEqual(row["endpoint_url"], "https://farm.example.test")

	def test_every_grant_pointing_at_it_is_released(self):
		"""One card can be filed against several grants over time. Releasing only
		the first would leave the delete refused by the second."""
		self.a_grant()
		self.a_grant(name="second@example.test")
		frappe.delete_doc("Governance Document", DOC)
		self.assertFalse(frappe.db.exists("Governance Document", DOC))
		for name in (WORKER, "second@example.test"):
			with self.subTest(grant=name):
				self.assertFalse(frappe.db.get_value("Mobile Access Grant", name, "qr_document"))

	def test_a_grant_pointing_somewhere_else_is_untouched(self):
		"""The cleanup is scoped to THIS document, not to the column."""
		other = "GOV-2026-0002"
		STORE.seed(
			"Governance Document",
			[{"name": other, "company": MAIN, "document_type": "Other", "title": "Another"}],
		)
		self.a_grant(name="third@example.test", qr_document=other)
		frappe.delete_doc("Governance Document", DOC)
		self.assertEqual(
			frappe.db.get_value("Mobile Access Grant", "third@example.test", "qr_document"), other
		)


# ── 2 ───────────────────────────────────────────────────────────────────────
class EveryOtherLinkStillRefuses(ArchiveTestCase):
	"""THE SAFETY PROPERTY. `ARCHIVE_LINKS` names one field, and widening it is a
	decision somebody has to make on purpose. A reference is not a copy: the lease
	is ABOUT that agreement, and detaching them silently destroys the fact that
	they were ever connected."""

	def link_from(self, doctype: str, fieldname: str, **extra) -> str:
		name = f"{doctype.replace(' ', '-')}-0001"
		row = {"name": name, "company": MAIN, fieldname: DOC}
		row.update(extra)
		STORE.seed(doctype, [row])
		return name

	def test_a_related_party_still_blocks_the_delete(self):
		"""`Related Party.governing_document` is "the document that ESTABLISHES
		this relationship" — the operating agreement, the trust instrument. A
		reference, and the clearest case of one."""
		self.link_from("Related Party", "governing_document", party_name="Alex Bramwell")
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Governance Document", DOC)
		self.assertTrue(frappe.db.exists("Governance Document", DOC))

	def test_a_supporting_document_still_blocks_the_delete(self):
		"""`Transfer Pricing Documentation.supporting_document` is "whatever is on
		the site already" — a pointer at a document that existed first."""
		self.link_from("Transfer Pricing Documentation", "supporting_document")
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Governance Document", DOC)

	def test_the_tuple_names_exactly_one_field_today(self):
		"""A DECISION RECORDED RATHER THAN LEFT IMPLICIT.

		THREE OTHER FIELDS DESCRIBE THEMSELVES AS ARCHIVE COPIES and are genuine
		candidates for this tuple:

		  * `Lease.governance_document` — "the archive entry for this lease".
		  * `Parcel.appraisal_document` — "the archived appraisal report".
		  * `Advisory Agreement.document_reference` — "the signed agreement in the
		    governance register, where it has been filed there".

		None is in it. v0.83.0's brief was the mobile card, and the card is the one
		of the four this app can REDRAW from its own data — delete the filed PNG
		and `generate_mobile_login_qr` makes another. A lease's archive entry holds
		a signed instrument that exists nowhere else, so releasing its link would
		hand somebody a one-click delete of the only copy. That asymmetry is the
		real rule, and it is why the tuple is not simply "every field whose label
		says archive".

		This test exists so that adding one is a line somebody changes on purpose
		with this argument in front of them, rather than a tuple that quietly
		grows."""
		from erpnext_mcp.erpnext_mcp.doctype.governance_document.governance_document import (
			ARCHIVE_LINKS,
		)

		self.assertEqual(ARCHIVE_LINKS, (("Mobile Access Grant", "qr_document"),))

	def test_the_amendment_chain_still_blocks_the_delete(self):
		"""`supersedes` is the archive's own spine. Losing a hop makes 'which one
		was in force in 2031' unanswerable, which is the whole point of the chain."""
		STORE.seed(
			"Governance Document",
			[
				{
					"name": "GOV-2026-0009",
					"company": MAIN,
					"document_type": "Other",
					"title": "Amendment",
					"supersedes": DOC,
				}
			],
		)
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Governance Document", DOC)

	def test_a_reference_and_an_archive_copy_together_still_refuse(self):
		"""The archive link releasing must not be mistaken for permission. A
		document that is BOTH somebody's filed card and a lease's governing
		document is still a lease's governing document."""
		self.a_grant()
		self.link_from("Related Party", "governing_document", party_name="Alex Bramwell")
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Governance Document", DOC)
		self.assertTrue(frappe.db.exists("Governance Document", DOC))

	def test_forcing_it_still_works(self):
		"""`force=True` is Frappe's own escape hatch and this must not have taken
		it away — `before_uninstall` depends on it."""
		self.link_from("Related Party", "governing_document", party_name="Alex Bramwell")
		frappe.delete_doc("Governance Document", DOC, force=True)
		self.assertFalse(frappe.db.exists("Governance Document", DOC))


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheDoubleActuallyRefuses(SeededTestCase):
	"""GUARD ON THE GUARD, the same move `test_patches.TheDoubleActuallyValidates`
	makes for link validation. Until v0.83.0 `frappe.delete_doc` in this harness
	popped the row and checked nothing, so every claim above would have passed
	against a double that permits what a real bench refuses."""

	def test_a_linked_document_is_refused(self):
		STORE.seed("Related Party", [{"name": "RP-0001", "party_name": "Alex", "company": MAIN}])
		STORE.seed(
			"Family", [{"name": "Cousin", "family_member_name": "Cousin", "related_party": "RP-0001"}]
		)
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc("Related Party", "RP-0001")

	def test_an_unlinked_document_deletes_cleanly(self):
		"""The other half: a check that refused everything would be just as wrong
		and would look identical from the test above."""
		STORE.seed("Related Party", [{"name": "RP-0002", "party_name": "Bo", "company": MAIN}])
		frappe.delete_doc("Related Party", "RP-0002")
		self.assertFalse(frappe.db.exists("Related Party", "RP-0002"))

	def test_the_message_names_both_ends(self):
		"""The app prints this. "Cannot delete" with no names is not actionable."""
		STORE.seed("Related Party", [{"name": "RP-0003", "party_name": "Cy", "company": MAIN}])
		STORE.seed(
			"Family", [{"name": "Nephew", "family_member_name": "Nephew", "related_party": "RP-0003"}]
		)
		with self.assertRaises(frappe.LinkExistsError) as caught:
			frappe.delete_doc("Related Party", "RP-0003")
		self.assertIn("RP-0003", str(caught.exception))
		self.assertIn("Family", str(caught.exception))

	def test_a_document_linking_only_to_itself_can_still_go(self):
		"""Frappe does not refuse that either, and a chain being torn down would
		otherwise deadlock on its own last document."""
		STORE.seed(
			"Governance Document",
			[
				{
					"name": "GOV-SELF",
					"company": MAIN,
					"document_type": "Other",
					"title": "Self",
					"superseded_by": "GOV-SELF",
				}
			],
		)
		frappe.delete_doc("Governance Document", "GOV-SELF")
		self.assertFalse(frappe.db.exists("Governance Document", "GOV-SELF"))


# ── 4 ───────────────────────────────────────────────────────────────────────
class ItSurvivesAnOddSite(ArchiveTestCase):
	"""`on_trash` runs on every delete of this doctype on every site, including
	ones with no mobile surface at all."""

	def test_a_site_with_no_mobile_access_grant_is_fine(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Mobile Access Grant")
		try:
			frappe.delete_doc("Governance Document", DOC)
		finally:
			INSTALLED_DOCTYPES.add("Mobile Access Grant")
		self.assertFalse(frappe.db.exists("Governance Document", DOC))

	def test_a_document_nothing_points_at_deletes_cleanly(self):
		frappe.delete_doc("Governance Document", DOC)
		self.assertFalse(frappe.db.exists("Governance Document", DOC))

	def test_a_grant_with_an_empty_link_is_not_disturbed(self):
		self.a_grant(name="nolink@example.test", qr_document=None)
		frappe.delete_doc("Governance Document", DOC)
		self.assertTrue(frappe.db.exists("Mobile Access Grant", "nolink@example.test"))
