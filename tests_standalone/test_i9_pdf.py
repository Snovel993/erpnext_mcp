# SPDX-License-Identifier: MIT
"""Form I-9 as a document — the template, the fill, and the two tools. v0.47.1.

NINE CLAIMS.

1. `TheShippedTemplate` — the USCIS PDF is on disk, unmodified, and carries the
   133 named fields the field table addresses. The checksum is asserted, so a
   template somebody re-saved in a PDF editor fails here rather than producing a
   form nobody can file.
2. `TheFieldPlan` — every collected value lands in the box USCIS gave it, dates
   come out `MM/DD/YYYY`, and the citizenship attestation ticks one box of four.
   Asserted against `i9_pdf.plan`, which needs no PDF library — so the mapping
   is checked on a bench that cannot render.
3. `WhatIsDeliberatelyBlank` — both signature boxes, the SSN comb, the
   alternative-procedure tick and Supplement B's new-name boxes. Each is a
   decision the module argues for, and a test is the only thing that stops a
   later "improvement" quietly undoing one.
4. `AdditionalInformation` — the receipt deadline, the attestation timestamps
   and the reverifications that did not fit, in the box the form provides for
   prose.
5. `SupplementB` — three rows, oldest first, and the fourth reported rather than
   dropped.
6. `TheFilledPage` — real bytes, and the values read back OUT of the page. This
   is the claim `test_tax_form_pdfs.py` makes about the box values it draws, and
   it is what separates "the renderer ran" from "the form says Garcia".
7. `TheSharedFieldIsSplit` — the one USCIS field that lives on two pages. Section
   2's List A title and Supplement B's second row are separate values in the
   output, which they are not in the template.
8. `RenderTool` — the tool renders, attaches, logs, refuses a Destroyed I-9,
   refuses a second render without `overwrite`, and gates the full SSN twice.
9. `AttachSignedTool` — the signed scan is attached, made private, logged, and
   refused when it is not a scan or when one is already filed.

HOW A PAGE IS READ BACK. Unlike `form_pdf_renderer`, which writes uncompressed
content streams a regular expression can find text in, this module writes into
an AcroForm — so the values are read back the way a PDF reader reads them: off
the field objects, PER PAGE, because the page is what disambiguates the one
field name USCIS uses twice.

EVERY CLASS THAT NEEDS pypdf SKIPS WITHOUT IT, the same posture
`test_tax_form_pdfs.py` takes about reportlab. `TheFieldPlan` and
`WhatIsDeliberatelyBlank` deliberately do NOT skip: the mapping is pure Python
and is the half most likely to be got wrong.
"""

import unittest
from datetime import date, timedelta

import frappe

from erpnext_mcp import i9_pdf

from .fixtures import MAIN
from .harness import STORE
from .test_i9 import I9_TOOLS_ON, I9TestCase

#: The two tools this file is about, on top of the fifteen `test_i9.py` enables.
PDF_TOOLS_ON = dict(I9_TOOLS_ON, allow_render_i9_pdf=1, allow_attach_signed_i9=1)

#: Skip decoration for everything that needs the library AND the template.
needs_pypdf = unittest.skipUnless(
	i9_pdf.available(),
	"pypdf and the shipped USCIS template are what fill a federal form; this bench has "
	"one or neither, which is exactly the case render_i9_pdf goes unavailable for.",
)


def a_record(**overrides) -> dict:
	"""One fully filled I-9 row, as `i9._i9_fields` reads it off the doctype.

	A DICT RATHER THAN A FIXTURE, because `i9_pdf` is a pure function and the
	whole point of testing it that way is that no database is involved. The
	tool-level classes below use the real records.
	"""
	record = {
		"name": "I9-2026-0001",
		"employee": "HR-EMP-00001",
		"employee_name": "Maria Garcia",
		"company": MAIN,
		"status": "Complete",
		"hire_date": "2026-04-01",
		"legal_first_name": "Maria",
		"legal_middle_name": "Elena",
		"legal_last_name": "Garcia",
		"other_last_names": "Ramos",
		"address_street": "1420 Orchard Road",
		"address_city": "Yakima",
		"address_state": "WA",
		"address_zip": "98901",
		"date_of_birth": "1994-03-11",
		"ssn_last_four": "6789",
		"email": "maria@example.test",
		"phone": "509-555-0134",
		"citizenship_status": "Alien Authorized to Work",
		"alien_registration_number": "A123456789",
		"i94_admission_number": "12345678901",
		"foreign_passport_number": "P9988",
		"foreign_passport_country": "Mexico",
		"alien_work_authorization_expiry": "2027-06-01",
		"section_1_signed_at": "2026-04-01 07:14:00",
		"section_1_signed_ip": "10.0.0.5",
		"section_1_signature": "/private/files/sig1.png",
		"preparer_used": 0,
		"list_a_doc_title": "Employment Authorization Document (Form I-766)",
		"list_a_doc_authority": "USCIS",
		"list_a_doc_number": "SRC1234567890",
		"list_a_doc_expiry": "2027-06-01",
		"list_a_is_receipt": 0,
		"receipt_pending": 0,
		"verifier_name": "Ana Ramos",
		"verifier_title": "Farm Manager",
		"verification_date": "2026-04-02",
		"section_2_signed_at": "2026-04-02 16:02:00",
		"section_2_signed_ip": "10.0.0.9",
	}
	record.update(overrides)
	return record


#: The employer block as `tools/i9._employer_block` really returns it — three
#: keys, not two. The EIN has been in that dict since v0.47.1 and reached the
#: page for the first time in v0.48.0; it was absent from this fixture, which is
#: part of why nobody noticed it went nowhere.
EMPLOYER = {
	"name": "Test Farm LLC",
	"address": "123 Orchard Rd, Yakima WA 98901",
	"ein": "12-3456789",
}

#: A site that has not filled the EIN in. Used where the claim under test is
#: about what is NOT written.
EMPLOYER_NO_EIN = {"name": "Test Farm LLC", "address": "123 Orchard Rd, Yakima WA 98901"}


def a_reverification(**overrides) -> dict:
	row = {
		"reverification_date": "2027-05-01",
		"reason": "Work Authorization Expired",
		"rehire_date": None,
		"document_title": "Employment Authorization Document (Form I-766)",
		"issuing_authority": "USCIS",
		"document_number": "SRC0001",
		"document_expiry": "2029-05-01",
		"verifier_name": "Ana Ramos",
		"verifier_title": "Farm Manager",
		"signed_at": "2027-05-01 09:00:00",
		"notes": "",
	}
	row.update(overrides)
	return row


def page_values(payload: bytes, index: int) -> dict:
	"""Every filled field on ONE page of a rendered form, by field name.

	PER PAGE ON PURPOSE, and it is the assertion `TheSharedFieldIsSplit` needs:
	`pypdf.get_fields` collapses the document to one entry per name, which is the
	exact view that cannot tell Section 2's `Document Title 1` from Supplement
	B's. Walking the page's own annotations and resolving each widget's value —
	its own, or the field's that lends it one — is how a reader sees the page.
	"""
	import io

	from pypdf import PdfReader

	reader = PdfReader(io.BytesIO(payload))
	found = {}
	for reference in reader.pages[index].get("/Annots") or []:
		widget = reference.get_object()
		name = widget.get("/T")
		value = widget.get("/V")
		parent = widget.get("/Parent")
		if parent is not None:
			parent = parent.get_object()
			if name is None:
				name = parent.get("/T")
			if value is None:
				value = parent.get("/V")
		if name is not None and value not in (None, ""):
			found[str(name)] = str(value)
	return found


# ── 1 ─────────────────────────────────────────────────────────────────────────
class TheShippedTemplate(unittest.TestCase):
	"""The government's page, byte for byte, with the names the table addresses."""

	def test_the_template_is_on_disk(self):
		import os

		self.assertTrue(
			os.path.exists(i9_pdf.TEMPLATE_PATH),
			"erpnext_mcp/templates/i9_form.pdf is what render_i9_pdf fills. Without it the "
			"tool is unavailable by design — but it ships with the app, so its absence here "
			"is a packaging failure rather than a bench without a dependency.",
		)

	def test_the_template_is_the_edition_the_field_table_was_written_against(self):
		"""The checksum, and the only test in this file that is meant to fail.

		A NEW USCIS EDITION FAILS HERE FIRST, which is the point: the field names
		are the whole interface, they are not stable across editions, and a
		template swapped in place would otherwise be discovered by a form coming
		out of a printer with empty boxes. `templates/README.md` has the
		three-step procedure for a revision.
		"""
		self.assertEqual(
			i9_pdf.template_sha256(),
			i9_pdf.TEMPLATE_SHA256,
			"the shipped USCIS template is not the one this app's field table was written "
			"against. If USCIS published a new edition, follow the procedure in "
			"erpnext_mcp/templates/README.md — the field-name tests below say which names "
			"moved. If nothing was meant to change, the file was corrupted or re-saved by a "
			"PDF editor; restore it from git.",
		)

	def test_the_edition_string_names_the_form_and_its_expiry(self):
		self.assertIn("1615-0047", i9_pdf.EDITION)
		self.assertIn("05/31/2027", i9_pdf.EDITION)

	@needs_pypdf
	def test_it_is_four_pages_and_carries_no_xfa(self):
		"""XFA would make the AcroForm values invisible in Acrobat.

		A LiveCycle form carries its real layout in an XML payload and viewers
		that understand it ignore the AcroForm entirely — so a fill like this one
		would produce a file whose fields hold the right values and whose pages
		render blank. This edition has none, and a future one that did would need
		a different approach rather than a bug report.
		"""
		from pypdf import PdfReader

		reader = PdfReader(i9_pdf.TEMPLATE_PATH)
		self.assertEqual(len(reader.pages), 4)
		self.assertNotIn("/XFA", reader.trailer["/Root"]["/AcroForm"])

	@needs_pypdf
	def test_every_field_the_table_addresses_exists_in_the_template(self):
		"""The mapping, checked against the file rather than against itself.

		This is the test that turns a USCIS revision from a silent blank box into
		a named failure. Every field name in `plan()` — for a record that fills
		every branch of it — has to be a field the template actually has.
		"""
		from pypdf import PdfReader

		names = set(PdfReader(i9_pdf.TEMPLATE_PATH).get_fields() or {})
		record = a_record(preparer_used=1, preparer_name="Luis Ortega", preparer_address="22 Main St")
		record["citizenship_status"] = "Alien Authorized to Work"
		planned = i9_pdf.plan(record, EMPLOYER, [a_reverification()] * 3, full_ssn="123456789")

		addressed = {field for values in planned.values() for field in values}
		# The one name that is NOT in the template on purpose: it does not exist
		# until `_split_shared_title` creates it. See the module docstring.
		addressed.discard(i9_pdf.SUPPLEMENT_B_TITLE_FIELD)
		self.assertTrue(addressed, "the plan addressed nothing, so this test proves nothing")
		self.assertEqual(
			addressed - names,
			set(),
			"these field names are in i9_pdf's table and not in the shipped USCIS template. "
			"Either the template was revised or a name was mistyped; the values would go "
			"nowhere and the boxes would print empty.",
		)

	@needs_pypdf
	def test_form_i9_has_no_employer_identification_number_box(self):
		"""v0.48.0's premise, pinned to the file rather than to a comment.

		`_employer_lines` writes the EIN into Additional Information BECAUSE the
		form has nowhere else for it — the Section 2 employer block is a name, a
		title, a signature, a date, a business name and a business address. If a
		later edition grows an EIN box this fails, and the right response is a
		field-table entry plus deleting the prose line, not a bug report.
		"""
		from pypdf import PdfReader

		names = [str(name).lower() for name in (PdfReader(i9_pdf.TEMPLATE_PATH).get_fields() or {})]
		suspects = [
			name for name in names
			if "ein" in name.replace("_", " ").split() or "identification number" in name
		]
		self.assertEqual(
			suspects,
			[],
			"the shipped template has a field that looks like an employer EIN box. Map it in "
			"i9_pdf._section_2 and drop the EIN line from _employer_lines — a number in a "
			"labelled box beats the same number in a prose box.",
		)

	@needs_pypdf
	def test_the_shared_field_really_is_shared_in_the_template(self):
		"""`_split_shared_title` exists because of this, so this is asserted.

		A test for the surgery is worth nothing if the defect it works around is
		not itself pinned down: if USCIS fixes the duplicate name in a later
		edition, this fails and the split can be deleted rather than carried
		forever as a thing nobody remembers the reason for.
		"""
		from pypdf import PdfReader

		reader = PdfReader(i9_pdf.TEMPLATE_PATH)
		pages = [
			index
			for index, page in enumerate(reader.pages)
			for reference in (page.get("/Annots") or [])
			if str(
				(reference.get_object().get("/T") or "")
				or (
					reference.get_object().get("/Parent").get_object().get("/T")
					if reference.get_object().get("/Parent") is not None
					else ""
				)
			)
			== i9_pdf.SHARED_TITLE_FIELD
		]
		self.assertEqual(
			sorted(pages),
			[i9_pdf.PAGE_FORM, i9_pdf.PAGE_SUPPLEMENT_B],
			f"{i9_pdf.SHARED_TITLE_FIELD!r} is supposed to be one field with a widget on "
			f"page 1 and another on page 4 — that is the whole reason _split_shared_title "
			f"exists. If USCIS has fixed it, delete the split and this test.",
		)


# ── 2 ─────────────────────────────────────────────────────────────────────────
class TheFieldPlan(unittest.TestCase):
	"""Which box each value lands in. NO PDF LIBRARY IS INVOLVED."""

	def setUp(self):
		self.plan = i9_pdf.plan(a_record(), EMPLOYER)
		self.page = self.plan[i9_pdf.PAGE_FORM]

	def test_section_1_identity(self):
		self.assertEqual(self.page["Last Name (Family Name)"], "Garcia")
		self.assertEqual(self.page["First Name Given Name"], "Maria")
		self.assertEqual(self.page["Employee Other Last Names Used (if any)"], "Ramos")
		self.assertEqual(self.page["City or Town"], "Yakima")
		self.assertEqual(self.page["State"], "WA")
		self.assertEqual(self.page["ZIP Code"], "98901")
		self.assertEqual(self.page["Employees E-mail Address"], "maria@example.test")

	def test_the_middle_name_becomes_the_middle_initial_the_box_asks_for(self):
		self.assertEqual(self.page["Employee Middle Initial (if any)"], "E")

	def test_dates_come_out_in_the_format_the_boxes_are_labelled(self):
		"""`mmddyyyy`, from the ISO strings the doctype stores."""
		self.assertEqual(self.page["Date of Birth mmddyyyy"], "03/11/1994")
		self.assertEqual(self.page["FirstDayEmployed mmddyyyy"], "04/01/2026")
		self.assertEqual(self.page["S2 Todays Date mmddyyyy"], "04/02/2026")
		self.assertEqual(self.page["Today's Date mmddyyy"], "04/01/2026")

	def test_a_date_it_cannot_parse_is_left_blank_rather_than_printed_through(self):
		"""A half-converted date in a date box reads as a date somebody chose."""
		page = i9_pdf.plan(a_record(date_of_birth="not a date"), EMPLOYER)[i9_pdf.PAGE_FORM]
		self.assertNotIn("Date of Birth mmddyyyy", page)

	def test_one_citizenship_box_is_ticked_and_only_one(self):
		ticked = [box for box in i9_pdf.CITIZENSHIP_BOXES.values() if box in self.page]
		self.assertEqual(ticked, ["CB_4"])
		self.assertEqual(self.page["CB_4"], "/On")

	def test_a_status_the_form_does_not_offer_ticks_nothing(self):
		"""An incomplete form somebody can see is incomplete beats a guess."""
		page = i9_pdf.plan(a_record(citizenship_status=""), EMPLOYER)[i9_pdf.PAGE_FORM]
		self.assertEqual([box for box in i9_pdf.CITIZENSHIP_BOXES.values() if box in page], [])

	def test_all_three_alien_identifiers_reach_their_own_boxes(self):
		self.assertEqual(self.page["USCIS ANumber"], "A123456789")
		self.assertEqual(self.page["Form I94 Admission Number"], "12345678901")
		self.assertEqual(
			self.page["Foreign Passport Number and Country of IssuanceRow1"], "P9988 / Mexico"
		)
		self.assertEqual(self.page["Exp Date mmddyyyy"], "06/01/2027")

	def test_a_permanent_residents_a_number_goes_in_the_other_box(self):
		"""One column here, two boxes on the form, and the status decides which."""
		page = i9_pdf.plan(
			a_record(citizenship_status="Lawful Permanent Resident"), EMPLOYER
		)[i9_pdf.PAGE_FORM]
		self.assertEqual(page["3 A lawful permanent resident Enter USCIS or ANumber"], "A123456789")
		self.assertNotIn("USCIS ANumber", page)
		self.assertEqual(page["CB_3"], "/On")

	def test_section_2_documents_and_the_employer_block(self):
		self.assertEqual(
			self.page["Document Title 1"], "Employment Authorization Document (Form I-766)"
		)
		self.assertEqual(self.page["Issuing Authority 1"], "USCIS")
		self.assertEqual(self.page["Document Number 0 (if any)"], "SRC1234567890")
		self.assertEqual(self.page["Expiration Date if any"], "06/01/2027")
		self.assertEqual(
			self.page["Last Name First Name and Title of Employer or Authorized Representative"],
			"Ana Ramos, Farm Manager",
		)
		self.assertEqual(self.page["Employers Business or Org Name"], "Test Farm LLC")
		self.assertEqual(
			self.page["Employers Business or Org Address"], "123 Orchard Rd, Yakima WA 98901"
		)

	def test_list_b_and_list_c_fill_their_own_columns(self):
		page = i9_pdf.plan(
			a_record(
				list_a_doc_title="",
				list_b_doc_title="Driver's License",
				list_b_doc_authority="WA DOL",
				list_b_doc_number="DL-1",
				list_c_doc_title="Social Security Card (Unrestricted)",
				list_c_doc_authority="SSA",
				list_c_doc_number="C-1",
			),
			EMPLOYER,
		)[i9_pdf.PAGE_FORM]
		self.assertEqual(page["List B Document 1 Title"], "Driver's License")
		self.assertEqual(page["List B Document Number 1"], "DL-1")
		self.assertEqual(page["List C Document Title 1"], "Social Security Card (Unrestricted)")
		self.assertEqual(page["List C Document Number 1"], "C-1")
		self.assertNotIn("Document Title 1", page)

	def test_an_unknown_state_is_left_blank_rather_than_set_outside_the_dropdown(self):
		"""A `/Ch` field set outside its own option list renders empty anyway."""
		page = i9_pdf.plan(a_record(address_state="Washington"), EMPLOYER)[i9_pdf.PAGE_FORM]
		self.assertNotIn("State", page)

	def test_the_preparer_supplement_is_empty_unless_a_preparer_was_used(self):
		self.assertEqual(self.plan[i9_pdf.PAGE_SUPPLEMENT_A], {})

	def test_the_preparer_supplement_splits_one_name_into_two_boxes(self):
		page = i9_pdf.plan(
			a_record(preparer_used=1, preparer_name="Luis Ortega", preparer_address="22 Main St"),
			EMPLOYER,
		)[i9_pdf.PAGE_SUPPLEMENT_A]
		self.assertEqual(page["Preparer or Translator First Name (Given Name) 0"], "Luis")
		self.assertEqual(page["Preparer or Translator Last Name (Family Name) 0"], "Ortega")
		self.assertEqual(page["Preparer or Translator Address (Street Number and Name) 0"], "22 Main St")
		self.assertEqual(page["Last Name Family Name from Section 1"], "Garcia")

	def test_the_file_name_leads_with_the_docname(self):
		self.assertEqual(i9_pdf.file_name_for(a_record()), "I-9-I9-2026-0001-Garcia-Maria.pdf")


# ── 3 ─────────────────────────────────────────────────────────────────────────
class WhatIsDeliberatelyBlank(unittest.TestCase):
	"""Four decisions the module argues for, pinned so nothing quietly undoes one."""

	def setUp(self):
		self.plan = i9_pdf.plan(a_record(), EMPLOYER, [a_reverification()])

	def test_neither_signature_box_is_ever_written(self):
		"""8 CFR 274a.2(h). A name typed into a `/Tx` field is not a signature."""
		page = self.plan[i9_pdf.PAGE_FORM]
		self.assertNotIn("Signature of Employee", page)
		self.assertNotIn("Signature of Employer or AR", page)
		self.assertNotIn("Signature of Emp Rep 0", self.plan[i9_pdf.PAGE_SUPPLEMENT_B])

	def test_the_ssn_comb_is_empty_unless_nine_digits_were_handed_in(self):
		self.assertNotIn("US Social Security Number", self.plan[i9_pdf.PAGE_FORM])

	def test_the_last_four_alone_never_reach_the_comb(self):
		"""Five empty cells then four digits reads as an SSN beginning 0000."""
		page = i9_pdf.plan(a_record(), EMPLOYER, full_ssn="6789")[i9_pdf.PAGE_FORM]
		self.assertNotIn("US Social Security Number", page)

	def test_nine_digits_are_written_without_their_punctuation(self):
		page = i9_pdf.plan(a_record(), EMPLOYER, full_ssn="123-45-6789")[i9_pdf.PAGE_FORM]
		self.assertEqual(page["US Social Security Number"], "123456789")

	def test_the_alternative_procedure_tick_is_never_set(self):
		"""Nothing in this app records whether DHS's remote procedure was used."""
		for values in self.plan.values():
			for field in values:
				self.assertFalse(field.startswith("CB_Alt"), f"{field} was ticked by nobody")

	def test_supplement_b_leaves_the_new_name_boxes_empty(self):
		"""They are for a legal name change, and the child table has no new name."""
		page = self.plan[i9_pdf.PAGE_SUPPLEMENT_B]
		for field in ("Last Name 0", "First Name 0", "Middle Initial 0"):
			self.assertNotIn(field, page)


# ── 4 ─────────────────────────────────────────────────────────────────────────
class AdditionalInformation(unittest.TestCase):
	"""The one box on the form that takes prose, and what this app puts in it."""

	def note(self, record=None, reverifications=None, notes=None, employer=EMPLOYER) -> str:
		planned = i9_pdf.plan(record or a_record(), employer, reverifications or [], notes=notes)
		return planned[i9_pdf.PAGE_FORM].get("Additional Information", "")

	def test_the_attestations_are_recorded_as_what_they_are(self):
		body = self.note()
		self.assertIn("Section 1 attested electronically 04/01/2026 07:14 from 10.0.0.5", body)
		self.assertIn("Section 2 attested electronically 04/02/2026 16:02", body)
		self.assertIn("signature capture retained on the record", body)

	def test_a_receipt_names_the_list_and_the_deadline(self):
		"""M-274 §4.3: the receipt goes in the document boxes, the fact goes here."""
		body = self.note(
			a_record(list_a_is_receipt=1, receipt_pending=1, receipt_expires_on="2026-06-30")
		)
		self.assertIn("RECEIPT under 8 CFR 274a.2(b)(1)(vi)", body)
		self.assertIn("List A", body)
		self.assertIn("must present the document by 06/30/2026", body)

	def test_the_receipt_is_not_written_into_the_document_title(self):
		"""A prefix there would say it in the wrong place and overflow the box."""
		page = i9_pdf.plan(a_record(list_a_is_receipt=1), EMPLOYER)[i9_pdf.PAGE_FORM]
		self.assertEqual(
			page["Document Title 1"], "Employment Authorization Document (Form I-766)"
		)

	def test_a_callers_own_lines_are_carried(self):
		self.assertIn("Rehired for the 2026 cherry harvest.", self.note(notes=["Rehired for the 2026 cherry harvest."]))

	def test_the_lines_are_separated_by_newlines_not_run_together(self):
		"""The box is the form's one multiline field; a viewer wraps a paragraph
		but does not invent the breaks between four separate statements."""
		self.assertIn("\n", self.note())

	def test_it_is_capped_rather_than_allowed_to_become_a_smudge(self):
		body = self.note(notes=["x" * 5000])
		self.assertLessEqual(len(body), i9_pdf.ADDITIONAL_INFORMATION_LIMIT)

	def test_nothing_is_written_when_there_is_nothing_to_say(self):
		bare = a_record(
			section_1_signed_at=None, section_2_signed_at=None, receipt_pending=0, list_a_is_receipt=0
		)
		self.assertEqual(self.note(bare, employer=EMPLOYER_NO_EIN), "")

	# v0.48.0. The EIN, and the reason it is here rather than in a box of its own.
	def test_the_employer_ein_is_written_and_is_labelled(self):
		self.assertIn("Employer EIN: 12-3456789.", self.note())

	def test_a_site_that_has_not_filled_the_ein_in_writes_no_line_about_it(self):
		self.assertNotIn("EIN", self.note(employer=EMPLOYER_NO_EIN))

	def test_the_ein_never_reaches_the_address_box(self):
		"""The box it would have been easiest to append to, and must not be."""
		page = i9_pdf.plan(a_record(), EMPLOYER)[i9_pdf.PAGE_FORM]
		self.assertEqual(page["Employers Business or Org Address"], EMPLOYER["address"])
		self.assertNotIn("12-3456789", page["Employers Business or Org Name"])


# ── 5 ─────────────────────────────────────────────────────────────────────────
class SupplementB(unittest.TestCase):
	"""Three rows, oldest first, and the fourth reported rather than dropped."""

	def rows(self, count: int) -> dict:
		entries = [
			a_reverification(reverification_date=f"20{27 + n}-05-01", document_number=f"SRC{n}")
			for n in range(count)
		]
		return i9_pdf.plan(a_record(), EMPLOYER, entries)

	def test_the_header_repeats_the_name_because_the_page_detaches(self):
		page = self.rows(1)[i9_pdf.PAGE_SUPPLEMENT_B]
		self.assertEqual(page["Last Name Family Name from Section 1-2"], "Garcia")
		self.assertEqual(page["First Name Given Name from Section 1-2"], "Maria")
		self.assertEqual(page["Middle initial if any from Section 1-2"], "E")

	def test_each_row_fills_its_own_boxes(self):
		page = self.rows(3)[i9_pdf.PAGE_SUPPLEMENT_B]
		self.assertEqual(page["Document Number 0"], "SRC0")
		self.assertEqual(page["Document Number 1"], "SRC1")
		self.assertEqual(page["Document Number 2"], "SRC2")
		self.assertEqual(page["Todays Date 0"], "05/01/2027")
		self.assertEqual(page["Todays Date 2"], "05/01/2029")
		self.assertEqual(page["Name of Emp or Auth Rep 0"], "Ana Ramos, Farm Manager")

	def test_row_ones_title_goes_to_the_field_that_was_split_out(self):
		page = self.rows(3)[i9_pdf.PAGE_SUPPLEMENT_B]
		self.assertIn(i9_pdf.SUPPLEMENT_B_TITLE_FIELD, page)
		self.assertNotIn(i9_pdf.SHARED_TITLE_FIELD, page)

	def test_the_reason_and_the_attestation_go_in_the_rows_own_note(self):
		page = self.rows(1)[i9_pdf.PAGE_SUPPLEMENT_B]
		self.assertIn("Reason: Work Authorization Expired.", page["Addtl Info 0"])
		self.assertIn("Issuing authority: USCIS.", page["Addtl Info 0"])
		self.assertIn("Attested electronically 05/01/2027 09:00", page["Addtl Info 0"])

	def test_a_rehire_date_reaches_its_box(self):
		page = i9_pdf.plan(
			a_record(), EMPLOYER, [a_reverification(reason="Rehire", rehire_date="2027-04-15")]
		)[i9_pdf.PAGE_SUPPLEMENT_B]
		self.assertEqual(page["Date of Rehire 0"], "04/15/2027")

	def test_the_fourth_season_is_named_in_additional_information_not_dropped(self):
		"""A silent truncation would look complete and be missing a season."""
		planned = self.rows(5)
		self.assertNotIn("Document Number 3", planned[i9_pdf.PAGE_SUPPLEMENT_B])
		body = planned[i9_pdf.PAGE_FORM]["Additional Information"]
		self.assertIn("Supplement B has 3 rows and this employee has 5 reverification(s)", body)
		self.assertIn("05/01/2030", body)
		self.assertIn("05/01/2031", body)


# ── 6 ─────────────────────────────────────────────────────────────────────────
@needs_pypdf
class TheFilledPage(unittest.TestCase):
	"""Real bytes, and the values read back out of the page a printer gets."""

	@classmethod
	def setUpClass(cls):
		cls.payload = i9_pdf.fill_i9_pdf(
			a_record(preparer_used=1, preparer_name="Luis Ortega", preparer_address="22 Main St"),
			EMPLOYER,
			[a_reverification()],
			full_ssn="123456789",
		)

	def test_it_is_a_pdf_and_it_is_the_whole_form(self):
		import io

		from pypdf import PdfReader

		self.assertTrue(self.payload.startswith(b"%PDF"))
		self.assertEqual(len(PdfReader(io.BytesIO(self.payload)).pages), 4)

	def test_the_template_on_disk_was_not_touched(self):
		"""The government's file stays the government's file."""
		self.assertEqual(i9_pdf.template_sha256(), i9_pdf.TEMPLATE_SHA256)

	def test_section_1_reads_back_off_page_one(self):
		page = page_values(self.payload, i9_pdf.PAGE_FORM)
		self.assertEqual(page["Last Name (Family Name)"], "Garcia")
		self.assertEqual(page["Date of Birth mmddyyyy"], "03/11/1994")
		self.assertEqual(page["US Social Security Number"], "123456789")
		self.assertEqual(page["State"], "WA")
		self.assertEqual(page["CB_4"], "/On")
		self.assertNotIn("CB_1", page)

	def test_the_supplements_read_back_off_their_own_pages(self):
		self.assertEqual(
			page_values(self.payload, i9_pdf.PAGE_SUPPLEMENT_A)[
				"Preparer or Translator Last Name (Family Name) 0"
			],
			"Ortega",
		)
		self.assertEqual(
			page_values(self.payload, i9_pdf.PAGE_SUPPLEMENT_B)["Document Number 0"], "SRC0001"
		)

	def test_the_page_of_acceptable_documents_is_left_alone(self):
		"""Page 2 is USCIS's instructions and has nothing to fill."""
		self.assertEqual(page_values(self.payload, i9_pdf.PAGE_LISTS), {})

	def test_need_appearances_is_set_so_every_viewer_renders_the_values(self):
		import io

		from pypdf import PdfReader

		acroform = PdfReader(io.BytesIO(self.payload)).trailer["/Root"]["/AcroForm"]
		self.assertTrue(acroform.get("/NeedAppearances"))


# ── 7 ─────────────────────────────────────────────────────────────────────────
@needs_pypdf
class TheSharedFieldIsSplit(unittest.TestCase):
	"""The USCIS defect, and the one structural edit this module makes.

	`Document Title 1` is ONE field with a widget in Section 2's List A block and
	another in Supplement B's second row. Without the split, whichever is written
	second wins in both boxes — a hire-day document title overwritten by a
	reverification made two seasons later, on a form that looks perfectly
	plausible.
	"""

	@classmethod
	def setUpClass(cls):
		cls.payload = i9_pdf.fill_i9_pdf(
			a_record(list_a_doc_title="U.S. Passport", list_a_doc_authority="US Dept of State"),
			EMPLOYER,
			[
				a_reverification(document_title="ROW ZERO"),
				a_reverification(document_title="ROW ONE"),
				a_reverification(document_title="ROW TWO"),
			],
		)

	def test_section_2_keeps_the_document_examined_on_the_day_of_hire(self):
		self.assertEqual(
			page_values(self.payload, i9_pdf.PAGE_FORM)[i9_pdf.SHARED_TITLE_FIELD], "U.S. Passport"
		)

	def test_supplement_b_carries_three_different_titles(self):
		page = page_values(self.payload, i9_pdf.PAGE_SUPPLEMENT_B)
		self.assertEqual(page["Document Title 0"], "ROW ZERO")
		self.assertEqual(page[i9_pdf.SUPPLEMENT_B_TITLE_FIELD], "ROW ONE")
		self.assertEqual(page["Document Title 2"], "ROW TWO")

	def test_the_split_field_is_a_field_of_its_own_in_the_output(self):
		import io

		from pypdf import PdfReader

		fields = PdfReader(io.BytesIO(self.payload)).get_fields() or {}
		self.assertIn(i9_pdf.SUPPLEMENT_B_TITLE_FIELD, fields)
		self.assertEqual(fields[i9_pdf.SUPPLEMENT_B_TITLE_FIELD].get("/V"), "ROW ONE")


# ── the tool-level fixtures ──────────────────────────────────────────────────
class I9PdfToolTestCase(I9TestCase):
	"""A complete I-9 on the fixture site, and both PDF tools switched on."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **PDF_TOOLS_ON)

	def a_complete_i9(self, employee="HR-EMP-00001") -> str:
		self._create_draft(employee=employee)
		self._submit_section_1(
			employee=employee,
			address_street="1420 Orchard Road",
			address_city="Yakima",
			address_state="WA",
			address_zip="98901",
			date_of_birth="1994-03-11",
			ssn_last_four="6789",
		)
		self._submit_section_2(employee=employee)
		return str(frappe.db.get_value("I-9 Form", {"employee": employee}, "name"))

	def a_scan(self, file_name="signed-i9.pdf", private=0) -> str:
		"""One File on the site, as an upload would have left it."""
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": file_name,
				"file_url": f"/private/files/{file_name}",
				"is_private": private,
				"content": b"%PDF-1.4 signed",
			}
		).insert()
		return doc.name

	def audit_actions(self, i9_name: str) -> list:
		return [
			row["action"]
			for row in STORE.rows("I-9 Audit Log")
			if row.get("i9_form") == i9_name
		]


# ── 8 ─────────────────────────────────────────────────────────────────────────
@needs_pypdf
class RenderTool(I9PdfToolTestCase):
	def test_it_renders_attaches_and_reports(self):
		name = self.a_complete_i9()
		data = self.tool_data("render_i9_pdf", {"i9_form": name})

		self.assertEqual(data["name"], name)
		self.assertGreater(data["bytes"], 100_000)
		self.assertTrue(data["file_name"].startswith("I-9-"))
		self.assertEqual(data["edition"], i9_pdf.EDITION)
		self.assertFalse(data["full_ssn_printed"])
		self.assertIsNone(data["replaced"])

		stored = frappe.db.get_value("I-9 Form", name, ["generated_pdf", "generated_pdf_on"], as_dict=True)
		self.assertEqual(stored["generated_pdf"], data["file_url"])
		self.assertTrue(stored["generated_pdf_on"])

	def test_the_attached_file_is_private_and_belongs_to_the_form(self):
		name = self.a_complete_i9()
		self.tool_data("render_i9_pdf", {"i9_form": name})
		attachment = next(
			row for row in STORE.rows("File") if row.get("attached_to_name") == name
		)
		self.assertEqual(int(attachment["is_private"]), 1)
		self.assertEqual(attachment["attached_to_doctype"], "I-9 Form")
		self.assertEqual(attachment["attached_to_field"], "generated_pdf")

	def test_the_employer_block_comes_off_i9_settings(self):
		name = self.a_complete_i9()
		data = self.tool_data("render_i9_pdf", {"i9_form": name})
		self.assertEqual(data["employer"]["name"], "Test Farm LLC")
		self.assertEqual(data["employer"]["address"], "123 Orchard Rd")

	def test_it_resolves_the_form_by_employee_as_well_as_by_docname(self):
		name = self.a_complete_i9()
		data = self.tool_data("render_i9_pdf", {"employee": "HR-EMP-00001"})
		self.assertEqual(data["name"], name)

	def test_it_logs_a_printed_row(self):
		name = self.a_complete_i9()
		self.tool_data("render_i9_pdf", {"i9_form": name})
		self.assertIn("Printed", self.audit_actions(name))

	def test_a_second_render_is_refused_without_overwrite(self):
		"""That field probably holds the copy somebody already had signed."""
		name = self.a_complete_i9()
		self.tool_data("render_i9_pdf", {"i9_form": name})
		error = self.tool_error("render_i9_pdf", {"i9_form": name})
		self.assertIn("already has a rendered PDF", error)
		self.assertIn("overwrite=true", error)

	def test_overwrite_repoints_the_field_and_leaves_the_old_file_attached(self):
		name = self.a_complete_i9()
		first = self.tool_data("render_i9_pdf", {"i9_form": name})
		second = self.tool_data("render_i9_pdf", {"i9_form": name, "overwrite": True})
		self.assertEqual(second["replaced"], first["file_url"])
		attached = [row for row in STORE.rows("File") if row.get("attached_to_name") == name]
		self.assertEqual(len(attached), 2)

	def test_a_destroyed_i9_is_refused(self):
		name = self.a_complete_i9()
		frappe.db.set_value("I-9 Form", name, "status", "Destroyed")
		error = self.tool_error("render_i9_pdf", {"i9_form": name})
		self.assertIn("destroyed", error)

	def test_a_draft_renders_and_says_which_boxes_are_empty(self):
		"""Handing a new hire a page with their own details on it is the point."""
		self._create_draft()
		name = str(frappe.db.get_value("I-9 Form", {"employee": "HR-EMP-00001"}, "name"))
		data = self.tool_data("render_i9_pdf", {"i9_form": name})
		self.assertIn("Section 1: citizenship attestation", data["incomplete"])
		self.assertIn("Section 2: no List A or List B+C document recorded", data["incomplete"])

	def test_a_complete_i9_reports_nothing_missing(self):
		name = self.a_complete_i9()
		self.assertEqual(self.tool_data("render_i9_pdf", {"i9_form": name})["incomplete"], [])

	def test_a_form_that_does_not_exist_is_named_in_the_refusal(self):
		error = self.tool_error("render_i9_pdf", {"i9_form": "I9-NOPE-0001"})
		self.assertIn("I9-NOPE-0001", error)

	def test_the_full_ssn_needs_the_site_switch_as_well_as_the_argument(self):
		"""Two gates, and this is the one an operator controls."""
		name = self.a_complete_i9()
		error = self.tool_error("render_i9_pdf", {"i9_form": name, "include_full_ssn": True})
		self.assertIn("store_full_ssn is off", error)
		self.assertIsNone(frappe.db.get_value("I-9 Form", name, "generated_pdf"))

	def test_the_full_ssn_is_refused_when_the_switch_is_on_and_nothing_is_stored(self):
		frappe.db.set_value("I-9 Settings", "I-9 Settings", "store_full_ssn", 1)
		name = self.a_complete_i9()
		error = self.tool_error("render_i9_pdf", {"i9_form": name, "include_full_ssn": True})
		self.assertIn("no stored full Social Security number", error)

	def test_without_the_argument_the_comb_is_simply_empty(self):
		frappe.db.set_value("I-9 Settings", "I-9 Settings", "store_full_ssn", 1)
		name = self.a_complete_i9()
		data = self.tool_data("render_i9_pdf", {"i9_form": name})
		self.assertFalse(data["full_ssn_printed"])

	def test_additional_information_accepts_a_string_or_a_list(self):
		name = self.a_complete_i9()
		self.tool_data(
			"render_i9_pdf", {"i9_form": name, "additional_information": "One line."}
		)
		self.tool_data(
			"render_i9_pdf",
			{"i9_form": name, "additional_information": ["Two.", "Lines."], "overwrite": True},
		)

	def test_a_long_reverification_history_is_reported_not_dropped(self):
		name = self.a_complete_i9()
		doc = frappe.get_doc("I-9 Form", name)
		for n in range(5):
			doc.append(
				"reverifications",
				{
					"reverification_date": str(date.today() - timedelta(days=30 * (5 - n))),
					"reason": "Work Authorization Expired",
					"document_title": "U.S. Passport",
					"verifier_name": "Ana Ramos",
				},
			)
		doc.flags.ignore_permissions = True
		doc.save()

		data = self.tool_data("render_i9_pdf", {"i9_form": name})
		self.assertEqual(data["reverifications"], 5)
		self.assertEqual(data["reverifications_not_on_page"], 2)


# ── 9 ─────────────────────────────────────────────────────────────────────────
class AttachSignedTool(I9PdfToolTestCase):
	"""The copy §1324a asks the employer to have kept. NEEDS NO PDF LIBRARY."""

	def test_it_attaches_the_scan_and_points_the_field_at_it(self):
		name = self.a_complete_i9()
		token = self.a_scan()
		data = self.tool_data("attach_signed_i9", {"i9_form": name, "file_token": token})

		self.assertEqual(data["name"], name)
		self.assertEqual(data["file_docname"], token)
		stored = frappe.db.get_value("I-9 Form", name, ["signed_pdf", "signed_pdf_on"], as_dict=True)
		self.assertEqual(stored["signed_pdf"], data["signed_pdf"])
		self.assertTrue(stored["signed_pdf_on"])

	def test_the_file_is_made_private_and_attached_to_the_form(self):
		"""A signed I-9 names a person, their DOB and their immigration status."""
		name = self.a_complete_i9()
		token = self.a_scan(private=0)
		self.tool_data("attach_signed_i9", {"i9_form": name, "file_token": token})

		row = frappe.db.get_value(
			"File", token, ["is_private", "attached_to_doctype", "attached_to_name", "attached_to_field"],
			as_dict=True,
		)
		self.assertEqual(int(row["is_private"]), 1)
		self.assertEqual(row["attached_to_doctype"], "I-9 Form")
		self.assertEqual(row["attached_to_name"], name)
		self.assertEqual(row["attached_to_field"], "signed_pdf")

	def test_the_stored_url_is_the_files_own_after_the_save(self):
		"""Not the one the caller named — the one that resolves afterwards.

		On a real bench, making a public File private MOVES the bytes from
		`public/files` to `private/files` and Frappe's File controller rewrites
		`file_url` as it goes. That is why the attach goes through the Document
		and re-reads the URL from it rather than flipping a column with
		`db.set_value`: a form pointing at the pre-move URL would hold a link that
		says private and resolves public, or does not resolve at all.
		"""
		name = self.a_complete_i9()
		token = self.a_scan(private=0)
		data = self.tool_data("attach_signed_i9", {"i9_form": name, "file_token": token})
		self.assertEqual(data["signed_pdf"], frappe.db.get_value("File", token, "file_url"))

	def test_it_takes_a_file_url_as_well_as_a_docname(self):
		"""A Desk Attach field holds the URL; `finalize_staged_file` returns the
		docname. Both spellings reach the same File."""
		name = self.a_complete_i9()
		token = self.a_scan(file_name="scan-by-url.pdf")
		url = frappe.db.get_value("File", token, "file_url")
		data = self.tool_data("attach_signed_i9", {"i9_form": name, "file_url": url})
		self.assertEqual(data["name"], name)
		self.assertEqual(data["file_docname"], token)

	def test_it_logs_a_signed_copy_filed_row(self):
		name = self.a_complete_i9()
		self.tool_data("attach_signed_i9", {"i9_form": name, "file_token": self.a_scan()})
		self.assertIn("Signed Copy Filed", self.audit_actions(name))

	def test_a_photograph_of_a_signed_sheet_is_accepted(self):
		"""A phone in an orchard photographs the page; it does not scan it."""
		name = self.a_complete_i9()
		token = self.a_scan(file_name="signed-i9.jpg")
		self.tool_data("attach_signed_i9", {"i9_form": name, "file_token": token})

	def test_something_that_is_not_a_scan_is_refused(self):
		name = self.a_complete_i9()
		token = self.a_scan(file_name="notes.txt")
		error = self.tool_error("attach_signed_i9", {"i9_form": name, "file_token": token})
		self.assertIn(".pdf", error)
		self.assertIsNone(frappe.db.get_value("I-9 Form", name, "signed_pdf"))

	def test_a_file_that_is_not_on_the_site_is_refused_by_name(self):
		name = self.a_complete_i9()
		error = self.tool_error("attach_signed_i9", {"i9_form": name, "file_token": "nope"})
		self.assertIn("no File called", error)
		self.assertIn("finalize_staged_file", error)

	def test_naming_no_file_at_all_is_refused_with_the_upload_it_needs(self):
		name = self.a_complete_i9()
		error = self.tool_error("attach_signed_i9", {"i9_form": name})
		self.assertIn("needs the file to attach", error)

	def test_a_second_signed_copy_is_refused_without_overwrite(self):
		"""The one write on this doctype that could not be undone from the record."""
		name = self.a_complete_i9()
		self.tool_data("attach_signed_i9", {"i9_form": name, "file_token": self.a_scan("first.pdf")})
		error = self.tool_error(
			"attach_signed_i9", {"i9_form": name, "file_token": self.a_scan("second.pdf")}
		)
		self.assertIn("already has a signed copy", error)

	def test_overwrite_replaces_it_and_says_what_it_replaced(self):
		name = self.a_complete_i9()
		first = self.tool_data(
			"attach_signed_i9", {"i9_form": name, "file_token": self.a_scan("first.pdf")}
		)
		second = self.tool_data(
			"attach_signed_i9",
			{"i9_form": name, "file_token": self.a_scan("second.pdf"), "overwrite": True},
		)
		self.assertEqual(second["replaced"], first["signed_pdf"])

	def test_a_destroyed_i9_takes_no_signed_copy(self):
		name = self.a_complete_i9()
		frappe.db.set_value("I-9 Form", name, "status", "Destroyed")
		error = self.tool_error(
			"attach_signed_i9", {"i9_form": name, "file_token": self.a_scan()}
		)
		self.assertIn("destroyed", error)

	def test_get_i9_form_reports_both_halves(self):
		"""A reader who cannot see whether a signed copy was filed cannot tell a
		complete I-9 file from an incomplete one."""
		name = self.a_complete_i9()
		before = self.tool_data("get_i9_form", {"employee": "HR-EMP-00001"})
		self.assertIsNone(before["signed_pdf"])

		self.tool_data("attach_signed_i9", {"i9_form": name, "file_token": self.a_scan()})
		after = self.tool_data("get_i9_form", {"employee": "HR-EMP-00001"})
		self.assertTrue(after["signed_pdf"])
		self.assertTrue(after["signed_pdf_on"])


if __name__ == "__main__":
	unittest.main()
