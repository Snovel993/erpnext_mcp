# SPDX-License-Identifier: MIT
"""Form W-4 as a document — the template, the fill, and the employer half. v0.48.0.

SEVEN CLAIMS.

1. `TheShippedTemplate` — the IRS PDF is on disk, unmodified, and carries the
   fields the table addresses. The checksum is asserted, so a template somebody
   re-saved in a PDF editor fails here rather than producing a form nobody can
   file.
2. `TheFieldTableIsCheckedAgainstGeometry` — and this is the claim that does the
   real work. USCIS named its I-9 fields after the boxes; the IRS named its W-4
   fields `f1_12[0]`, so `test_i9_pdf`'s "every name exists" is not enough —
   every name in this table EXISTS, and a mistyped one would exist too. So the
   names are checked against WHERE THEY SIT: the two name boxes share a row, the
   filing-status ticks descend in the order the form prints them, and the three
   Employers Only boxes are the bottom band left to right.
3. `TheFieldPlan` — every collected election lands in the box the IRS gave it,
   dollars come out `2,200.00`, a zero comes out blank, and one filing status
   ticks one box of three. Asserted against `w4_pdf.plan`, which needs no PDF
   library — so the mapping is checked on a bench that cannot render.
4. `WhatIsDeliberatelyBlank` — the SSN, the exempt tick, and the worksheets.
   Each is a decision the module argues for, and a test is the only thing that
   stops a later "improvement" quietly undoing one.
5. `TheEmployerBlock` — the three boxes this release exists for, including the
   name-and-address line being truncated rather than run off the page.
6. `TheFilledPage` — real bytes, the values read back OUT of the page, and the
   XFA payload gone. That last one is not cosmetic: with it, Acrobat renders the
   XFA layout and every value below is invisible.
7. `RenderTool` — the tool renders, attaches, resolves the employer block from
   settings, refuses a second render without `overwrite`, and reports a tax year
   that does not match the shipped edition rather than refusing it.

EVERY CLASS THAT NEEDS pypdf SKIPS WITHOUT IT, the same posture
`test_i9_pdf.py` takes. `TheFieldPlan`, `WhatIsDeliberatelyBlank` and
`TheEmployerBlock` deliberately do NOT skip: the mapping is pure Python and is
the half most likely to be got wrong.
"""

import io
import unittest

import frappe

from erpnext_mcp import pdf_signing, w4_pdf

from .fixtures import MAIN
from .harness import STORE
from .test_i9 import I9TestCase

#: The tool this file is about, on top of what `test_w4.py` enables.
W4_PDF_ON = {f"allow_{name}": 1 for name in ("render_w4_pdf", "submit_w4", "get_w4", "list_w4_forms")}

needs_pypdf = unittest.skipUnless(
	w4_pdf.available(),
	"pypdf and the shipped IRS template are what fill a federal form; this bench has one "
	"or neither, which is exactly the case render_w4_pdf goes unavailable for.",
)


def a_record(**overrides) -> dict:
	"""One fully filled W-4 row, as `w4._w4_fields` reads it off the doctype.

	A DICT RATHER THAN A FIXTURE, because `w4_pdf` is a pure function and the
	whole point of testing it that way is that no database is involved.
	"""
	record = {
		"name": "W4-2026-0001",
		"employee": "HR-EMP-00001",
		"employee_name": "Maria Garcia",
		"company": MAIN,
		"tax_year": 2026,
		"status": "Active",
		"effective_date": "2026-04-02",
		"filing_status": "Married Filing Jointly",
		"multiple_jobs": 1,
		"additional_income_from_other_jobs": 0,
		"dependents_under_17_count": 2,
		"dependents_under_17_amount": 4400,
		"other_dependents_count": 1,
		"other_dependents_amount": 500,
		"total_dependents_credit": 4900,
		"other_income": 1200,
		"deductions": 3000,
		"extra_withholding_per_period": 25,
		"first_date_of_employment": "2026-04-01",
	}
	record.update(overrides)
	return record


EMPLOYEE = {
	"first_name": "Maria",
	"middle_name": "Elena",
	"last_name": "Garcia",
	"address": "1420 Orchard Road",
	"city": "Yakima",
	"state": "WA",
	"zip": "98901",
}

EMPLOYER = {
	"name": "Test Farm LLC",
	"address": "123 Orchard Rd, Yakima WA 98901",
	"ein": "12-3456789",
}


def page_one(record=None, employee=None, employer=None) -> dict:
	return w4_pdf.plan(
		record if record is not None else a_record(),
		EMPLOYEE if employee is None else employee,
		EMPLOYER if employer is None else employer,
	)[w4_pdf.PAGE_FORM]


def template_widgets(index: int = 0) -> dict:
	"""Every widget on one page of the TEMPLATE, by fully qualified name.

	Qualified rather than local, because the IRS's names only mean anything
	qualified: `f1_06[0]` is unique on the page and `topmostSubform[0].Page1[0].
	Step3_ReadOrder[0].f1_06[0]` is what `update_page_form_field_values` matches.
	"""
	from pypdf import PdfReader

	page = PdfReader(w4_pdf.TEMPLATE_PATH).pages[index]
	annotations = page.get("/Annots")
	annotations = annotations.get_object() if hasattr(annotations, "get_object") else annotations
	found = {}
	for reference in annotations or []:
		widget = reference.get_object()
		parts = []
		node = widget
		while node is not None:
			label = node.get("/T")
			if label:
				parts.append(str(label))
			parent = node.get("/Parent")
			node = parent.get_object() if parent is not None else None
		if parts:
			found[".".join(reversed(parts))] = [float(value) for value in widget.get("/Rect")]
	return found


def page_values(payload: bytes, index: int = 0) -> dict:
	"""Every filled field on ONE page of a rendered form, by LOCAL name.

	Local here rather than qualified — unlike the I-9 there is no name used
	twice, and the local name is what a reader of the assertion recognises.
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
		if name is not None and value not in (None, "", "/Off"):
			found[str(name)] = str(value)
	return found


# ── 1 ─────────────────────────────────────────────────────────────────────────
class TheShippedTemplate(unittest.TestCase):
	"""The government's page, byte for byte, with the names the table addresses."""

	def test_the_template_is_on_disk(self):
		import os

		self.assertTrue(
			os.path.exists(w4_pdf.TEMPLATE_PATH),
			"erpnext_mcp/templates/w4_form.pdf is what render_w4_pdf fills. Without it the "
			"tool is unavailable by design — but it ships with the app, so its absence here "
			"is a packaging failure rather than a bench without a dependency.",
		)

	def test_the_template_is_the_edition_the_field_table_was_written_against(self):
		"""The checksum, and the only test in this file that is meant to fail.

		THE IRS REVISES FORM W-4 EVERY YEAR, which makes this the most perishable
		assertion in the app — more so than the I-9's, whose edition lasts years.
		`templates/README.md` has the procedure.
		"""
		self.assertEqual(
			w4_pdf.template_sha256(),
			w4_pdf.TEMPLATE_SHA256,
			"the shipped IRS template is not the one this app's field table was written "
			"against. If the IRS published a new year's form, follow the procedure in "
			"erpnext_mcp/templates/README.md — the geometry test below says which names "
			"moved. If nothing was meant to change, the file was corrupted or re-saved by a "
			"PDF editor; restore it from git.",
		)

	def test_the_edition_string_names_the_form_and_its_control_number(self):
		self.assertIn("W-4", w4_pdf.EDITION)
		self.assertIn("1545-0074", w4_pdf.EDITION)
		self.assertIn(str(w4_pdf.TEMPLATE_TAX_YEAR), w4_pdf.EDITION)

	@needs_pypdf
	def test_it_is_five_pages_and_the_template_still_carries_its_xfa(self):
		"""The defect `fill_w4_pdf` works around, pinned down where it lives.

		A test for the removal is worth nothing if the thing being removed is not
		itself asserted: if the IRS ships a year without XFA, this fails and the
		deletion can go rather than being carried forever as a line nobody
		remembers the reason for.
		"""
		from pypdf import PdfReader

		reader = PdfReader(w4_pdf.TEMPLATE_PATH)
		self.assertEqual(len(reader.pages), 5)
		self.assertIn("/XFA", reader.trailer["/Root"]["/AcroForm"])

	@needs_pypdf
	def test_every_field_the_table_addresses_exists_in_the_template(self):
		from pypdf import PdfReader

		names = set(PdfReader(w4_pdf.TEMPLATE_PATH).get_fields() or {})
		addressed = set(page_one())
		addressed.update({field for field, _ in w4_pdf.FILING_STATUS_BOXES.values()})
		addressed.update({w4_pdf.FIELD_SSN, w4_pdf.FIELD_EXEMPT})
		self.assertTrue(addressed, "the plan addressed nothing, so this test proves nothing")
		self.assertEqual(
			addressed - names,
			set(),
			"these field names are in w4_pdf's table and not in the shipped IRS template. "
			"Either the form was revised or a name was mistyped; the values would go nowhere "
			"and the boxes would print empty.",
		)


# ── 2 ─────────────────────────────────────────────────────────────────────────
@needs_pypdf
class TheFieldTableIsCheckedAgainstGeometry(unittest.TestCase):
	"""Where each name SITS, because the names themselves say nothing.

	`f1_12[0]` is the employer's name and address for no reason a reader can
	verify. What can be verified is that it is the wide box on the bottom-left of
	page 1, under the words "Employers Only" — so that is what is asserted, and a
	revision that renumbered the fields fails here with the row it moved.
	"""

	@classmethod
	def setUpClass(cls):
		cls.rects = template_widgets(w4_pdf.PAGE_FORM)

	def bottom(self, field: str) -> float:
		return self.rects[field][1]

	def left(self, field: str) -> float:
		return self.rects[field][0]

	def test_step_1a_is_first_name_then_last_name_then_ssn_across_one_row(self):
		row = [w4_pdf.FIELD_FIRST_NAME, w4_pdf.FIELD_LAST_NAME, w4_pdf.FIELD_SSN]
		bottoms = {self.bottom(field) for field in row}
		self.assertEqual(len(bottoms), 1, "Step 1(a)'s three boxes are one row on the form")
		self.assertEqual([self.left(field) for field in row], sorted(self.left(f) for f in row))

	def test_the_address_lines_are_under_the_name_line_in_order(self):
		self.assertLess(self.bottom(w4_pdf.FIELD_ADDRESS), self.bottom(w4_pdf.FIELD_FIRST_NAME))
		self.assertLess(self.bottom(w4_pdf.FIELD_CITY_STATE_ZIP), self.bottom(w4_pdf.FIELD_ADDRESS))

	def test_the_filing_status_ticks_descend_in_the_order_the_form_prints_them(self):
		"""Single, then Married filing jointly, then Head of household.

		The three are separate one-state checkboxes rather than a radio group, so
		nothing but their position says which is which — and getting the order
		wrong would file every married employee as single.
		"""
		order = [
			"Single or Married Filing Separately",
			"Married Filing Jointly",
			"Head of Household",
		]
		bottoms = [self.bottom(w4_pdf.FILING_STATUS_BOXES[status][0]) for status in order]
		self.assertEqual(bottoms, sorted(bottoms, reverse=True))
		self.assertEqual(
			len({self.left(w4_pdf.FILING_STATUS_BOXES[status][0]) for status in order}),
			1,
			"the three ticks are one column on the form",
		)

	def test_each_filing_status_tick_declares_the_on_state_the_table_gives_it(self):
		from pypdf import PdfReader

		page = PdfReader(w4_pdf.TEMPLATE_PATH).pages[w4_pdf.PAGE_FORM]
		states = {}
		for reference in page["/Annots"]:
			widget = reference.get_object()
			appearance = widget.get("/AP")
			if appearance is None or widget.get("/T") is None:
				continue
			normal = appearance.get("/N")
			if normal is None:
				continue
			# THE OFF STATE IS NOT IN THE DICTIONARY on this form. A checkbox
			# whose /N declares only its on-state is perfectly ordinary — /Off
			# is the implicit default — and a filter that required /Off to be
			# present would silently match nothing and pass.
			on_states = [str(key) for key in normal.keys() if str(key) != "/Off"]
			if on_states:
				states[str(widget["/T"])] = on_states

		for status, (field, on) in w4_pdf.FILING_STATUS_BOXES.items():
			local = field.rsplit(".", 1)[-1]
			self.assertIn(
				on,
				states.get(local, []),
				f"{status} would be written as {on} and the box does not have that state — "
				f"a value outside a checkbox's own appearance dictionary renders as unticked",
			)

	def test_steps_3_and_4_descend_in_the_order_the_form_numbers_them(self):
		order = [
			w4_pdf.FIELD_DEPENDENTS_UNDER_17,  # 3(a)
			w4_pdf.FIELD_OTHER_DEPENDENTS,  # 3(b)
			w4_pdf.FIELD_DEPENDENTS_TOTAL,  # 3
			w4_pdf.FIELD_OTHER_INCOME,  # 4(a)
			w4_pdf.FIELD_DEDUCTIONS,  # 4(b)
			w4_pdf.FIELD_EXTRA_WITHHOLDING,  # 4(c)
		]
		bottoms = [self.bottom(field) for field in order]
		self.assertEqual(bottoms, sorted(bottoms, reverse=True))

	def test_the_employers_only_boxes_are_the_bottom_band_left_to_right(self):
		"""Name and address, first date of employment, EIN. The whole release."""
		row = [
			w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS,
			w4_pdf.FIELD_FIRST_DATE_OF_EMPLOYMENT,
			w4_pdf.FIELD_EMPLOYER_EIN,
		]
		lefts = [self.left(field) for field in row]
		self.assertEqual(lefts, sorted(lefts))
		for field in row:
			self.assertLess(
				self.bottom(field),
				self.bottom(w4_pdf.FIELD_EXTRA_WITHHOLDING),
				"the Employers Only row is below every box the employee fills in",
			)

	def test_step_5_has_no_signature_field_to_stamp_into(self):
		"""The module says so; this is the file saying it.

		Step 5's signature and date are printed rules between the exempt tick
		and the Employers Only row, with no widget between them. That is why
		`SIGNATURE_BOX` is measured rather than read off a rectangle, and it is
		the one piece of geometry in either PDF module that a template revision
		could move silently — so if a future year makes them fillable, this
		fails and the module should read the widget instead of the constant.
		"""
		floor = self.bottom(w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS) + self.rects[
			w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS
		][3] - self.rects[w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS][1]
		ceiling = self.bottom(w4_pdf.FIELD_EXEMPT)
		between = [
			name for name, rect in self.rects.items() if floor < rect[1] < ceiling
		]
		self.assertEqual(
			between,
			[],
			"the IRS form grew a field on the Step 5 signature line. w4_pdf must be taught "
			"never to write it — a name typed into a signature box renders as a signature "
			"and is not one.",
		)


# ── 3 ─────────────────────────────────────────────────────────────────────────
class TheFieldPlan(unittest.TestCase):
	"""Every collected election in the box the IRS gave it. No PDF library."""

	def setUp(self):
		self.plan = page_one()

	def test_step_1_identity(self):
		self.assertEqual(self.plan[w4_pdf.FIELD_FIRST_NAME], "Maria E")
		self.assertEqual(self.plan[w4_pdf.FIELD_LAST_NAME], "Garcia")
		self.assertEqual(self.plan[w4_pdf.FIELD_ADDRESS], "1420 Orchard Road")
		self.assertEqual(self.plan[w4_pdf.FIELD_CITY_STATE_ZIP], "Yakima, WA 98901")

	def test_the_middle_name_becomes_the_middle_initial_the_box_asks_for(self):
		self.assertEqual(page_one(employee=dict(EMPLOYEE, middle_name=""))[w4_pdf.FIELD_FIRST_NAME], "Maria")

	def test_one_filing_status_box_is_ticked_and_only_one(self):
		ticked = [
			field for field, _ in w4_pdf.FILING_STATUS_BOXES.values() if field in self.plan
		]
		self.assertEqual(ticked, [w4_pdf.FILING_STATUS_BOXES["Married Filing Jointly"][0]])
		self.assertEqual(self.plan[ticked[0]], "/2")

	def test_a_filing_status_the_form_does_not_offer_ticks_nothing(self):
		plan = page_one(a_record(filing_status="Married Filing Jointly with Extra Steps"))
		for field, _ in w4_pdf.FILING_STATUS_BOXES.values():
			self.assertNotIn(field, plan)

	def test_the_two_jobs_tick_is_set_only_where_the_employee_set_it(self):
		self.assertEqual(self.plan[w4_pdf.FIELD_MULTIPLE_JOBS], w4_pdf.MULTIPLE_JOBS_ON)
		self.assertNotIn(w4_pdf.FIELD_MULTIPLE_JOBS, page_one(a_record(multiple_jobs=0)))

	def test_the_dependents_credits_are_the_computed_amounts_not_the_counts(self):
		"""The form's boxes are dollars — 'Multiply the number … by $2,200'."""
		self.assertEqual(self.plan[w4_pdf.FIELD_DEPENDENTS_UNDER_17], "4,400.00")
		self.assertEqual(self.plan[w4_pdf.FIELD_OTHER_DEPENDENTS], "500.00")
		self.assertEqual(self.plan[w4_pdf.FIELD_DEPENDENTS_TOTAL], "4,900.00")

	def test_the_step_4_adjustments_reach_their_own_boxes(self):
		self.assertEqual(self.plan[w4_pdf.FIELD_OTHER_INCOME], "1,200.00")
		self.assertEqual(self.plan[w4_pdf.FIELD_DEDUCTIONS], "3,000.00")

	def test_a_zero_is_left_blank_rather_than_printed_as_a_deliberate_nothing(self):
		plan = page_one(a_record(other_income=0, deductions=0, total_dependents_credit=0))
		for field in (w4_pdf.FIELD_OTHER_INCOME, w4_pdf.FIELD_DEDUCTIONS, w4_pdf.FIELD_DEPENDENTS_TOTAL):
			self.assertNotIn(field, plan)

	def test_the_multiple_jobs_worksheet_result_is_added_to_step_4c(self):
		"""The form's own instruction: 'enter the result in Step 4(c) below'.

		ADDED, not substituted. An employee with a second job AND an extra $25 a
		period asked for both, and printing either alone understates what they
		told the employer to withhold.
		"""
		plan = page_one(a_record(additional_income_from_other_jobs=100, extra_withholding_per_period=25))
		self.assertEqual(plan[w4_pdf.FIELD_EXTRA_WITHHOLDING], "125.00")

	def test_step_4c_alone_still_reaches_the_box(self):
		self.assertEqual(self.plan[w4_pdf.FIELD_EXTRA_WITHHOLDING], "25.00")

	def test_the_file_name_leads_with_the_docname_and_carries_the_year(self):
		name = w4_pdf.file_name_for(a_record(), EMPLOYEE)
		self.assertEqual(name, "W-4-W4-2026-0001-2026-Garcia-Maria.pdf")


# ── 4 ─────────────────────────────────────────────────────────────────────────
class WhatIsDeliberatelyBlank(unittest.TestCase):
	"""Three decisions the module argues for, each pinned so it cannot be undone."""

	def test_the_ssn_box_is_never_written(self):
		"""A W-4 is completed by the EMPLOYEE and the number is theirs to write."""
		self.assertNotIn(w4_pdf.FIELD_SSN, page_one())

	def test_no_ssn_reaches_the_page_even_when_the_record_carries_one(self):
		"""There is no column for it and there must be no accident either."""
		plan = page_one(a_record(ssn_last_four="0142", ssn_full="555110142"))
		printed = "".join(str(value) for value in plan.values())
		self.assertNotIn("555110142", printed)
		self.assertNotIn("0142", printed)

	def test_the_exempt_from_withholding_tick_is_never_set(self):
		"""It claims exemption for a whole year under penalty of perjury."""
		self.assertNotIn(w4_pdf.FIELD_EXEMPT, page_one())
		self.assertNotIn(w4_pdf.FIELD_EXEMPT, page_one(a_record(exempt=1)))

	def test_only_page_one_is_written(self):
		"""Pages 3 and 4 are worksheets the IRS tells the employee to keep."""
		self.assertEqual(list(w4_pdf.plan(a_record(), EMPLOYEE, EMPLOYER)), [w4_pdf.PAGE_FORM])


# ── 5 ─────────────────────────────────────────────────────────────────────────
class TheEmployerBlock(unittest.TestCase):
	"""The three boxes v0.48.0 exists for."""

	def test_the_name_and_address_share_one_box(self):
		self.assertEqual(
			page_one()[w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS],
			"Test Farm LLC, 123 Orchard Rd, Yakima WA 98901",
		)

	def test_the_first_date_of_employment_is_the_format_the_box_is_labelled(self):
		self.assertEqual(page_one()[w4_pdf.FIELD_FIRST_DATE_OF_EMPLOYMENT], "04/01/2026")

	def test_a_date_it_cannot_parse_is_left_blank_rather_than_printed_through(self):
		plan = page_one(a_record(first_date_of_employment="whenever"))
		self.assertNotIn(w4_pdf.FIELD_FIRST_DATE_OF_EMPLOYMENT, plan)

	def test_the_ein_reaches_its_own_box_on_this_form(self):
		"""Unlike Form I-9, which has no EIN box at all. See `i9_pdf`."""
		self.assertEqual(page_one()[w4_pdf.FIELD_EMPLOYER_EIN], "12-3456789")

	def test_a_site_that_has_filled_nothing_in_gets_three_empty_boxes(self):
		plan = page_one(employer={"name": "", "address": "", "ein": ""})
		for field in (
			w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS,
			w4_pdf.FIELD_EMPLOYER_EIN,
		):
			self.assertNotIn(field, plan)

	def test_an_overlong_block_is_cut_rather_than_run_off_the_page(self):
		employer = dict(EMPLOYER, address="Suite 400, " + "The Very Long Orchard Road " * 8)
		block = page_one(employer=employer)[w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS]
		self.assertLessEqual(len(block), w4_pdf.EMPLOYER_BLOCK_LIMIT)
		self.assertTrue(block.endswith("…"))
		self.assertTrue(w4_pdf.employer_block_overflows(employer))

	def test_a_block_that_fits_is_not_reported_as_truncated(self):
		self.assertFalse(w4_pdf.employer_block_overflows(EMPLOYER))


# ── 6 ─────────────────────────────────────────────────────────────────────────
@needs_pypdf
class TheFilledPage(unittest.TestCase):
	"""Real bytes, and the values read back OUT of the page."""

	@classmethod
	def setUpClass(cls):
		cls.payload = w4_pdf.fill_w4_pdf(a_record(), EMPLOYEE, EMPLOYER)

	def test_it_is_a_pdf_and_it_is_the_whole_form(self):
		import io

		from pypdf import PdfReader

		self.assertTrue(self.payload.startswith(b"%PDF"))
		self.assertEqual(len(PdfReader(io.BytesIO(self.payload)).pages), 5)

	def test_the_template_on_disk_was_not_touched(self):
		self.assertEqual(w4_pdf.template_sha256(), w4_pdf.TEMPLATE_SHA256)

	def test_the_xfa_payload_is_gone_from_the_copy(self):
		"""Without this, Acrobat renders the XFA layout and prints a blank form.

		The single most consequential line in the module: every value below
		would be in the file and invisible in the reader an accountant opens it
		in.
		"""
		import io

		from pypdf import PdfReader

		reader = PdfReader(io.BytesIO(self.payload))
		self.assertNotIn("/XFA", reader.trailer["/Root"]["/AcroForm"])

	def test_the_employees_half_reads_back_off_page_one(self):
		values = page_values(self.payload)
		self.assertEqual(values["f1_02[0]"], "Garcia")
		self.assertEqual(values["f1_04[0]"], "Yakima, WA 98901")
		self.assertEqual(values["f1_08[0]"], "4,900.00")

	def test_the_employers_half_reads_back_off_page_one(self):
		values = page_values(self.payload)
		self.assertEqual(values["f1_12[0]"], "Test Farm LLC, 123 Orchard Rd, Yakima WA 98901")
		self.assertEqual(values["f1_13[0]"], "04/01/2026")
		self.assertEqual(values["f1_14[0]"], "12-3456789")

	def test_the_ticked_box_is_the_one_the_employee_chose(self):
		values = page_values(self.payload)
		self.assertEqual(values["c1_1[1]"], "/2")
		self.assertNotIn("c1_1[0]", values)
		self.assertNotIn("c1_1[2]", values)
		self.assertNotIn("c1_3[0]", values)

	def test_the_worksheet_pages_are_left_alone(self):
		for index in (w4_pdf.PAGE_MULTIPLE_JOBS, w4_pdf.PAGE_DEDUCTIONS):
			self.assertEqual(page_values(self.payload, index), {})

	def test_need_appearances_is_set_so_every_viewer_renders_the_values(self):
		import io

		from pypdf import PdfReader

		reader = PdfReader(io.BytesIO(self.payload))
		self.assertTrue(reader.trailer["/Root"]["/AcroForm"].get("/NeedAppearances"))


# ── 7 ─────────────────────────────────────────────────────────────────────────
@needs_pypdf
class RenderTool(I9TestCase):
	"""The tool: render, attach, resolve the employer block, refuse the second one.

	BUILT ON `I9TestCase` because the employer block comes off I-9 Settings —
	which is the point of `_employer_for`, and would be untested against a
	fixture that seeded the W-4 alone.
	"""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **dict(self._switches(), **W4_PDF_ON))
		STORE.seed(
			"W-4 Form",
			[
				{
					"name": "W4-2026-0001",
					"employee": "HR-EMP-00001",
					"employee_name": "Ada Orchard",
					"company": MAIN,
					"tax_year": 2026,
					"status": "Active",
					"effective_date": "2026-04-02",
					"filing_status": "Married Filing Jointly",
					"multiple_jobs": 0,
					"dependents_under_17_count": 1,
					"dependents_under_17_amount": 2200,
					"other_dependents_count": 0,
					"other_dependents_amount": 0,
					"total_dependents_credit": 2200,
					"other_income": 0,
					"deductions": 0,
					"extra_withholding_per_period": 0,
					"additional_income_from_other_jobs": 0,
				}
			],
		)

	def _switches(self):
		from .test_i9 import I9_TOOLS_ON

		return I9_TOOLS_ON

	def render(self, **overrides):
		payload = {"w4_form": "W4-2026-0001"}
		payload.update(overrides)
		return self.tool_data("render_w4_pdf", payload)

	def test_it_renders_attaches_and_reports(self):
		data = self.render()
		self.assertEqual(data["name"], "W4-2026-0001")
		self.assertGreater(data["bytes"], 1000)
		self.assertTrue(data["file_url"])
		self.assertEqual(data["edition"], w4_pdf.EDITION)
		self.assertIn("NO signature field", data["note"])

	def test_the_attached_file_is_private_and_belongs_to_the_form(self):
		self.render()
		files = STORE.rows("File")
		self.assertEqual(len(files), 1)
		self.assertEqual(files[0]["attached_to_doctype"], "W-4 Form")
		self.assertEqual(files[0]["attached_to_name"], "W4-2026-0001")
		self.assertTrue(int(files[0]["is_private"]))

	def test_the_employer_block_comes_off_i9_settings(self):
		"""One place per fact: the name on the EIN is configured once."""
		data = self.render()
		self.assertEqual(data["employer"]["name"], "Test Farm LLC")
		self.assertEqual(data["employer"]["ein"], "12-3456789")
		self.assertFalse(data["employer_block_truncated"])

	def test_the_first_date_of_employment_comes_off_the_employee_record(self):
		frappe.db.set_value("Employee", "HR-EMP-00001", "date_of_joining", "2026-04-01")
		data = self.render()
		self.assertEqual(data["first_date_of_employment"], "2026-04-01")
		self.assertNotIn("Employers Only: first date of employment", data["incomplete"])

	def test_a_missing_hire_date_is_reported_rather_than_refused(self):
		frappe.db.set_value("Employee", "HR-EMP-00001", "date_of_joining", None)
		data = self.render()
		self.assertIn("Employers Only: first date of employment", data["incomplete"])

	def test_a_second_render_is_refused_unless_overwrite_is_passed(self):
		self.render()
		message = self.tool_error("render_w4_pdf", {"w4_form": "W4-2026-0001"})
		self.assertIn("already has a rendered PDF", message)
		self.assertIn("Nothing was changed", message)

	def test_overwrite_repoints_the_field_and_says_what_it_replaced(self):
		first = self.render()
		second = self.render(overwrite=True)
		self.assertEqual(second["replaced"], first["file_url"])
		self.assertEqual(len(STORE.rows("File")), 2)

	def test_it_resolves_by_employee_when_no_docname_is_given(self):
		data = self.tool_data("render_w4_pdf", {"employee": "HR-EMP-00001"})
		self.assertEqual(data["name"], "W4-2026-0001")

	def test_an_employee_with_no_w4_is_refused_by_name(self):
		STORE.tables["W-4 Form"] = {}
		message = self.tool_error("render_w4_pdf", {"employee": "HR-EMP-00001"})
		self.assertIn("no active W-4 Form", message)

	def test_a_tax_year_the_template_does_not_print_is_reported_not_refused(self):
		"""A 2025 election on the 2026 page is a readable record; none is not."""
		frappe.db.set_value("W-4 Form", "W4-2026-0001", "tax_year", 2025)
		data = self.render()
		self.assertFalse(data["template_tax_year_matches"])
		self.assertEqual(data["template_tax_year"], w4_pdf.TEMPLATE_TAX_YEAR)

	def test_rendering_moves_no_status(self):
		self.render()
		self.assertEqual(frappe.db.get_value("W-4 Form", "W4-2026-0001", "status"), "Active")



# ── 8 ─────────────────────────────────────────────────────────────────────────
def a_capture(width=700, height=200) -> bytes:
	"""What `SignatureCanvas.renderPNG` produces: opaque, white paper, black ink."""
	from PIL import Image, ImageDraw

	image = Image.new("RGB", (width, height), (255, 255, 255))
	ImageDraw.Draw(image).line(
		[(150, 130), (200, 70), (250, 130), (300, 60), (360, 120), (420, 80), (470, 110)],
		fill=(0, 0, 0), width=5, joint="curve")
	buffer = io.BytesIO()
	image.save(buffer, format="PNG")
	return buffer.getvalue()


@unittest.skipUnless(pdf_signing.available() and w4_pdf.available(),
                     "needs Pillow, reportlab, pypdf and the shipped template")
class TheSignatureIsStampedIntoStep5(unittest.TestCase):
	"""v0.51.0. "This form is not valid unless you sign it" is on the page.

	The app held the employee's captured signature and the rendered W-4 did not
	show it, so what came out was an invalid W-4 with all the right numbers on
	it. The signature now goes into the page content and the form is flattened.
	"""

	def rendered(self, **kwargs):
		from pypdf import PdfReader

		payload = w4_pdf.fill_w4_pdf(a_record(), EMPLOYEE, EMPLOYER, **kwargs)
		return payload, PdfReader(io.BytesIO(payload))

	def test_a_signed_w4_has_no_editable_field_left(self):
		_payload, reader = self.rendered(signature=a_capture())
		self.assertIsNone(reader.get_fields())
		self.assertNotIn("/AcroForm", reader.trailer["/Root"])

	def test_flattening_keeps_the_values(self):
		"""The failure that would matter most: a flatten that loses the
		appearance streams produces a beautifully blank federal form."""
		_payload, reader = self.rendered(signature=a_capture())
		text = reader.pages[w4_pdf.PAGE_FORM].extract_text()
		self.assertIn("Maria", text)
		self.assertIn("Garcia", text)
		self.assertIn("Yakima", text)

	def test_the_signature_reaches_the_page_content(self):
		_payload, reader = self.rendered(signature=a_capture())
		page = reader.pages[w4_pdf.PAGE_FORM]
		images = [key for key, value in (page["/Resources"].get("/XObject") or {}).items()
		          if value.get_object().get("/Subtype") == "/Image"]
		self.assertTrue(images, "the signature did not reach the page")

	def test_an_unsigned_w4_is_still_the_page_somebody_prints_and_signs(self):
		_payload, reader = self.rendered()
		self.assertTrue(reader.get_fields())
		self.assertIn("/AcroForm", reader.trailer["/Root"])

	def test_an_unreadable_capture_costs_the_signature_and_not_the_form(self):
		payload, reader = self.rendered(signature=b"not a png")
		self.assertTrue(payload.startswith(b"%PDF"))
		# Nothing stamped, so nothing flattened: still a form somebody can sign.
		self.assertTrue(reader.get_fields())

	def test_the_measured_box_still_matches_the_shipped_page(self):
		"""THE ONE HARDCODED RECTANGLE IN EITHER PDF MODULE, re-derived here from
		the landmarks its comment names — so a template revision that moves the
		Step 5 rule fails this instead of stamping a signature into empty space.
		"""
		from pypdf import PdfReader

		captions = []

		def visit(text, cm, tm, font, size):
			body = (text or "").strip()
			if body:
				captions.append((round(tm[4], 1), round(tm[5], 1), body))

		PdfReader(w4_pdf.TEMPLATE_PATH).pages[w4_pdf.PAGE_FORM].extract_text(visitor_text=visit)
		# "signature", not "Employee" — the form's own TITLE is "Employee's
		# Withholding Certificate" and it is at the top of the page.
		caption = [c for c in captions if "signature" in c[2].lower()]
		self.assertTrue(caption, "the Step 5 caption moved or was reworded")
		caption_x, caption_y, _ = caption[0]

		x0, y0, x1, y1 = w4_pdf.SIGNATURE_BOX
		self.assertGreater(x1, x0)
		self.assertGreater(y1, y0)
		# Sits above the caption it belongs to...
		self.assertGreaterEqual(y0, caption_y)
		# ...starts roughly where that caption starts...
		self.assertAlmostEqual(x0, caption_x, delta=6.0)
		# ...and clears the Employers Only row underneath it.
		employers = template_widgets()[w4_pdf.FIELD_EMPLOYER_NAME_ADDRESS]
		self.assertGreater(y0, employers[3])
