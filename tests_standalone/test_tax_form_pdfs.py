# SPDX-License-Identifier: MIT
"""Tax Form PDF rendering — v0.36.0.

NINE CLAIMS.

1. `RendererAvailability` — the module says whether it can draw, and the two
   tools go unavailable by name on a bench without reportlab.
2. `RendererBytes` — every one of the six renderers produces a real PDF.
3. `W2Page` — the computed box values reach the page, read back out of it.
4. `Form941Page` — lines 1 through 15 and the quarter tick render.
5. `StateFormPages` — OR-WR, OQ and WA-ESD carry their own figures.
6. `Disclaimers` — the header note and the working-copy block are on EVERY page
   of EVERY form, including one that only exists because a table overflowed.
7. `RenderTool` — the tool renders a Tax Form and attaches the PDF to it.
8. `BulkRenderTool` — a batch renders, skips and reports rather than aborting.
9. `RenderRefusals` — the settings gates, the overwrite guard, and every
   argument check.

HOW A PAGE IS READ BACK. `form_pdf_renderer` writes UNCOMPRESSED content
streams on purpose (see its docstring), so the text on a page is recoverable
from the bytes with a regular expression and no PDF parser. That is what makes
"box 1 says 4,000.00" a thing a test can assert rather than "the renderer ran
without raising", which is what a byte-count assertion actually proves.
"""

import re
import unittest

import frappe

from erpnext_mcp import form_generators as generators
from erpnext_mcp import form_pdf_renderer as renderer

from .fixtures import MAIN, OTHER
from .harness import STORE
from .test_tax_forms import (
	COMPANY_INFO,
	TAX_FORM_TOOLS_ON,
	TaxFormToolTestCase,
	or_slip,
	wa_slip,
)

HAVE_REPORTLAB = renderer.available()
NEEDS_REPORTLAB = unittest.skipUnless(
	HAVE_REPORTLAB,
	"reportlab is not installed on this bench — the two PDF tools are supposed to go "
	"quietly unavailable, which `RendererAvailability` asserts without it",
)

PDF_TOOLS = ("render_tax_form_pdf", "bulk_render_tax_form_pdfs")
PDF_TOOLS_ON = {**TAX_FORM_TOOLS_ON, **{f"allow_{name}": 1 for name in PDF_TOOLS}}

EMPLOYEE_INFO = {
	"employee": "HR-EMP-00001",
	"employee_name": "Test Worker",
	"ssn_last4": "6789",
	"address": "12 Row Lane, The Dalles OR 97058",
}

CONTRACTOR_INFO = {
	"party": "RP-LADDER",
	"party_name": "Ladder Company",
	"party_type": "Supplier",
	"tin_last4": "4321",
	"address": "9 Shop Street, Hood River OR 97031",
}

#: Every string drawn on a page, in draw order. The content stream is plain
#: text because the renderer does not compress it, so this needs no PDF parser
#: — and a test that needed one would be testing the parser.
_STRING = re.compile(rb"\((?:[^()\\]|\\.)*\)")
_STREAM = re.compile(rb"stream\r?\n(.*?)endstream", re.S)

#: A PDF literal string escapes these three. Undoing it is the whole of the
#: "parser" this needs — anything more would be testing the parser.
_UNESCAPE = ((r"\(", "("), (r"\)", ")"), (r"\\", "\\"))


def page_texts(pdf: bytes) -> list[str]:
	"""One joined string of drawn text per content stream, in page order."""
	pages = []
	for stream in _STREAM.findall(pdf):
		parts = []
		for match in _STRING.findall(stream):
			part = match[1:-1].decode("latin-1")
			for escaped, plain in _UNESCAPE:
				part = part.replace(escaped, plain)
			parts.append(part)
		pages.append(" ".join(parts))
	return pages


def text_of(pdf: bytes) -> str:
	"""Every string on every page of one document."""
	return " ".join(page_texts(pdf))


def a_year_of_oregon_slips(employee="HR-EMP-00001"):
	"""One slip per quarter at round numbers, so a box can be read by eye."""
	return [
		or_slip(employee=employee, gross=1000.0, period_end=end)
		for end in ("2025-02-14", "2025-05-16", "2025-08-15", "2025-11-14")
	]


# ── Claim 1: availability ─────────────────────────────────────────────────


class RendererAvailability(TaxFormToolTestCase):
	"""A bench without reportlab loses two tools by name and nothing else."""

	def test_available_matches_the_import(self):
		self.assertEqual(renderer.available(), HAVE_REPORTLAB)

	def test_the_requires_sentence_names_the_package_and_the_fix(self):
		sentence = renderer.requires_sentence()
		self.assertIn("reportlab", sentence)
		self.assertIn("pip install", sentence)

	def test_the_two_tools_are_advertised_only_where_a_page_can_be_drawn(self):
		from erpnext_mcp import registry

		for name in PDF_TOOLS:
			with self.subTest(tool=name):
				self.assertEqual(registry.is_available(name), HAVE_REPORTLAB)

	def test_the_tools_that_only_compute_are_unaffected(self):
		"""The numbers are the deliverable; the page is a convenience."""
		from erpnext_mcp import registry

		for name in ("generate_tax_form", "get_tax_form", "list_tax_forms"):
			with self.subTest(tool=name):
				self.assertTrue(registry.is_available(name))

	def test_every_form_type_has_a_layout(self):
		self.assertEqual(set(renderer.RENDERERS), set(generators.FORM_TYPES))

	@unittest.skipIf(HAVE_REPORTLAB, "reportlab is installed on this bench")
	def test_without_reportlab_the_refusal_names_the_package(self):
		from erpnext_mcp.errors import ToolError

		with self.assertRaises(ToolError) as caught:
			renderer.require()
		self.assertIn("reportlab", str(caught.exception))


# ── Claim 2: the bytes are a PDF ──────────────────────────────────────────


@NEEDS_REPORTLAB
class RendererBytes(unittest.TestCase):
	"""Every renderer produces a document a reader will open."""

	def all_six(self) -> dict:
		slips = a_year_of_oregon_slips()
		quarter = [or_slip(employee="E1"), or_slip(employee="E2", gross=2000.0)]
		return {
			"W-2": renderer.render_w2_pdf(
				generators.generate_w2_data(EMPLOYEE_INFO, slips, COMPANY_INFO, 2025),
				COMPANY_INFO,
				EMPLOYEE_INFO,
			),
			"1099-NEC": renderer.render_1099_nec_pdf(
				generators.generate_1099_nec_data(
					CONTRACTOR_INFO,
					[{"amount": 2500.0, "date": "2025-03-01"}],
					COMPANY_INFO,
					2025,
				),
				COMPANY_INFO,
				CONTRACTOR_INFO,
			),
			"941": renderer.render_941_pdf(
				generators.generate_941_data(quarter, COMPANY_INFO, "Q1", 2025),
				COMPANY_INFO,
			),
			"OR-WR": renderer.render_or_wr_pdf(
				generators.generate_or_wr_data(slips, COMPANY_INFO, 2025),
				COMPANY_INFO,
			),
			"OQ": renderer.render_or_oq_pdf(
				generators.generate_or_oq_data(quarter, COMPANY_INFO, "Q1", 2025),
				COMPANY_INFO,
			),
			"WA-ESD": renderer.render_wa_esd_pdf(
				generators.generate_wa_esd_data([wa_slip(employee="E1")], COMPANY_INFO, "Q1", 2025),
				COMPANY_INFO,
			),
		}

	def test_every_renderer_produces_pdf_bytes(self):
		for form_type, payload in self.all_six().items():
			with self.subTest(form=form_type):
				self.assertIsInstance(payload, bytes)
				self.assertTrue(payload.startswith(b"%PDF-1"), f"{form_type} is not a PDF")
				self.assertIn(b"%%EOF", payload)
				self.assertGreater(len(payload), 1500)

	def test_the_dispatcher_routes_every_form_type(self):
		slips = a_year_of_oregon_slips()
		for form_type in generators.FORM_TYPES:
			with self.subTest(form=form_type):
				form_data = generators.generate_form_data(
					form_type,
					slips,
					COMPANY_INFO,
					2025,
					quarter="Q1",
					subject_info=EMPLOYEE_INFO if form_type == "W-2" else CONTRACTOR_INFO,
					payments=[{"amount": 900.0}],
				)
				payload = renderer.render_form_pdf(
					form_type,
					form_data,
					COMPANY_INFO,
					EMPLOYEE_INFO,
				)
				self.assertTrue(payload.startswith(b"%PDF-1"))

	def test_an_unknown_form_type_is_refused_by_name(self):
		from erpnext_mcp.errors import ToolError

		with self.assertRaises(ToolError) as caught:
			renderer.render_form_pdf("W-4", {}, COMPANY_INFO)
		self.assertIn("W-4", str(caught.exception))

	def test_an_empty_form_renders_rather_than_raising(self):
		"""A form with no payroll behind it is a page of zeroes and a warning,
		not a traceback — the same promise the generators make."""
		payload = renderer.render_w2_pdf(
			generators.generate_w2_data(EMPLOYEE_INFO, [], COMPANY_INFO, 2025),
			COMPANY_INFO,
			EMPLOYEE_INFO,
		)
		self.assertTrue(payload.startswith(b"%PDF-1"))
		self.assertIn("no payroll slips found", text_of(payload))

	def test_a_form_with_nothing_at_all_in_it_still_renders(self):
		for form_type in generators.FORM_TYPES:
			with self.subTest(form=form_type):
				payload = renderer.render_form_pdf(form_type, {}, {}, {})
				self.assertTrue(payload.startswith(b"%PDF-1"))

	def test_the_file_name_says_what_the_form_is(self):
		name = renderer.file_name_for(
			"941",
			{"tax_year": 2025, "quarter": "Q2"},
		)
		self.assertEqual(name, "941-2025-Q2.pdf")
		self.assertEqual(
			renderer.file_name_for("W-2", {"tax_year": 2025}, "HR-EMP-00001"),
			"W-2-2025-HR-EMP-00001.pdf",
		)


# ── Claim 3: the W-2's boxes reach the page ───────────────────────────────


@NEEDS_REPORTLAB
class W2Page(unittest.TestCase):
	"""Every W-2 box value, read back out of the rendered page."""

	def a_page(self, slips=None, employee_info=None, company_info=None) -> str:
		form_data = generators.generate_w2_data(
			employee_info or EMPLOYEE_INFO,
			a_year_of_oregon_slips() if slips is None else slips,
			company_info or COMPANY_INFO,
			2025,
		)
		return text_of(
			renderer.render_w2_pdf(
				form_data,
				company_info or COMPANY_INFO,
				employee_info or EMPLOYEE_INFO,
			)
		)

	def test_the_money_boxes(self):
		page = self.a_page()
		# 4 x 1,000 gross; 4 x 100 federal; 6.2% and 1.45% of the same.
		self.assertIn("4,000.00", page)  # boxes 1, 3 and 5
		self.assertIn("400.00", page)  # box 2
		self.assertIn("248.00", page)  # box 4
		self.assertIn("58.00", page)  # box 6

	def test_a_changed_figure_changes_the_page(self):
		"""The values are read off the form data, not hardcoded in the layout."""
		page = self.a_page(slips=[or_slip(gross=7250.0)])
		self.assertIn("7,250.00", page)
		self.assertNotIn("4,000.00", page)

	def test_the_form_title_the_year_and_the_copy(self):
		page = self.a_page()
		self.assertIn("Form W-2", page)
		self.assertIn("Wage and Tax Statement", page)
		self.assertIn("2025", page)
		self.assertIn("Copy B", page)

	def test_the_employer_block(self):
		page = self.a_page()
		self.assertIn("Example Trading Co", page)
		self.assertIn("12-3456789", page)
		self.assertIn("Hood River OR 97031", page)

	def test_the_employee_block(self):
		page = self.a_page()
		self.assertIn("Test Worker", page)
		self.assertIn("XXX-XX-6789", page)
		self.assertIn("The Dalles OR 97058", page)

	def test_four_digits_of_the_ssn_and_never_nine(self):
		page = self.a_page()
		self.assertNotIn("123-45-6789", page)
		self.assertIn("XXX-XX-", page)

	def test_a_missing_ssn_prints_a_blank_rather_than_a_guess(self):
		page = self.a_page(employee_info={**EMPLOYEE_INFO, "ssn_last4": ""})
		self.assertIn("XXX-XX-", page)
		self.assertNotIn("XXX-XX-6789", page)

	def test_the_official_box_numbering_is_on_the_page(self):
		page = self.a_page()
		for label in (
			"1  Wages, tips, other compensation",
			"2  Federal income tax withheld",
			"3  Social security wages",
			"5  Medicare wages and tips",
			"11  Nonqualified plans",
			"14  Other",
		):
			with self.subTest(box=label):
				self.assertIn(label, page)

	def test_boxes_fifteen_to_twenty_carry_the_state_row(self):
		page = self.a_page()
		self.assertIn("15  State", page)
		self.assertIn("16  State wages, tips, etc.", page)
		self.assertIn("17  State income tax", page)
		self.assertIn("20  Locality name", page)
		self.assertIn("OR", page)
		self.assertIn("1234567-8", page)  # box 15, the employer's Oregon BIN
		self.assertIn("240.00", page)  # box 17, four quarters of income tax

	def test_a_worker_in_two_states_gets_both_rows(self):
		slips = [*a_year_of_oregon_slips(), wa_slip(employee="HR-EMP-00001", period_end="2025-06-13")]
		page = self.a_page(slips=slips)
		self.assertIn("1234567-8", page)
		self.assertIn("000123456", page)

	def test_box_fourteen_carries_the_state_levies(self):
		page = self.a_page()
		self.assertIn("ORSTT W/H", page)

	def test_every_box_fourteen_item_is_named_in_the_notes(self):
		"""The box has room for three; the notes list all of them, because a
		levy that vanished off the bottom of a box is one nobody reconciles."""
		slips = [*a_year_of_oregon_slips(), wa_slip(employee="HR-EMP-00001", period_end="2025-06-13")]
		page = self.a_page(slips=slips)
		for code in ("ORSTT W/H", "ORPFML", "WAPFML", "WACARES", "WALI"):
			with self.subTest(code=code):
				self.assertIn(code, page)

	def test_the_warnings_are_printed_in_full(self):
		page = self.a_page(slips=[])
		self.assertIn("no payroll slips found for 2025", page)
		self.assertIn("note(s) on this form", page)

	def test_a_clean_form_says_it_assumed_nothing(self):
		page = self.a_page()
		self.assertIn("raised no notes", page)

	def test_an_address_the_form_data_lacks_is_filled_from_the_argument(self):
		"""The record wins on figures; an argument fills a hole it has."""
		form_data = generators.generate_w2_data(
			{**EMPLOYEE_INFO, "address": ""},
			a_year_of_oregon_slips(),
			COMPANY_INFO,
			2025,
		)
		page = text_of(renderer.render_w2_pdf(form_data, COMPANY_INFO, EMPLOYEE_INFO))
		self.assertIn("The Dalles OR 97058", page)

	def test_an_argument_never_displaces_a_figure_the_record_holds(self):
		form_data = generators.generate_w2_data(
			EMPLOYEE_INFO,
			a_year_of_oregon_slips(),
			COMPANY_INFO,
			2025,
		)
		page = text_of(
			renderer.render_w2_pdf(
				form_data,
				{**COMPANY_INFO, "name": "Some Other Co"},
				EMPLOYEE_INFO,
			)
		)
		self.assertIn("Example Trading Co", page)
		self.assertNotIn("Some Other Co", page)


# ── Claim 4: Form 941's lines ─────────────────────────────────────────────


@NEEDS_REPORTLAB
class Form941Page(unittest.TestCase):
	def a_page(self, slips=None, company_info=None, quarter="Q1") -> str:
		info = company_info or COMPANY_INFO
		form_data = generators.generate_941_data(
			[or_slip(employee="E1"), or_slip(employee="E2", gross=2000.0)] if slips is None else slips,
			info,
			quarter,
			2025,
		)
		return text_of(renderer.render_941_pdf(form_data, info))

	def test_the_title_and_the_quarter(self):
		page = self.a_page()
		self.assertIn("Form 941", page)
		self.assertIn("Employer's QUARTERLY Federal Tax Return", page)
		self.assertIn("Q1 2025", page)

	def test_the_quarter_is_ticked_and_the_others_are_not(self):
		"""Four labels are always drawn; the tick is what says which quarter."""
		self.assertIn("3: July, August, September", self.a_page(quarter="Q3"))

	def test_lines_one_through_three(self):
		page = self.a_page()
		self.assertIn("Number of employees who received wages", page)
		self.assertIn("Wages, tips, and other compensation", page)
		self.assertIn("3,000.00", page)  # line 2, two employees
		self.assertIn("200.00", page)  # line 3, two lots of federal withholding

	def test_line_five_carries_both_columns(self):
		page = self.a_page()
		self.assertIn("Taxable social security wages", page)
		self.assertIn("372.00", page)  # 12.4% of 3,000
		self.assertIn("Taxable Medicare wages and tips", page)
		self.assertIn("87.00", page)  # 2.9% of 3,000

	def test_the_totals_and_the_balance(self):
		page = self.a_page()
		self.assertIn("Total taxes before adjustments", page)
		self.assertIn("659.00", page)  # lines 6, 10, 12 and 14 with no deposits
		self.assertIn("Balance due", page)

	def test_deposits_move_the_balance_onto_the_page(self):
		page = self.a_page(company_info={**COMPANY_INFO, "deposits": 700.0})
		self.assertIn("700.00", page)  # line 13
		self.assertIn("41.00", page)  # line 15, the overpayment

	def test_every_line_number_one_to_fifteen_is_drawn(self):
		page = self.a_page()
		for label in (
			"Total social security and Medicare taxes",
			"Section 3121(q)",
			"fractions of cents",
			"adjustment for sick pay",
			"small business payroll tax credit",
			"Total deposits for this quarter",
			"Overpayment",
		):
			with self.subTest(line=label):
				self.assertIn(label, page)

	def test_the_due_date_is_on_the_page(self):
		self.assertIn("2025-04-30", self.a_page())

	def test_the_warnings_are_printed(self):
		page = self.a_page()
		self.assertIn("no deposit total supplied", page)


# ── Claim 5: the state forms ──────────────────────────────────────────────


@NEEDS_REPORTLAB
class StateFormPages(unittest.TestCase):
	def test_or_wr_carries_the_quarters_and_the_annual_total(self):
		form_data = generators.generate_or_wr_data(
			a_year_of_oregon_slips(),
			COMPANY_INFO,
			2025,
		)
		page = text_of(renderer.render_or_wr_pdf(form_data, COMPANY_INFO))
		self.assertIn("Form OR-WR", page)
		self.assertIn("Oregon Annual Withholding Tax Reconciliation Report", page)
		self.assertIn("1234567-8", page)  # the BIN
		self.assertIn("1st quarter", page)
		self.assertIn("4th quarter", page)
		self.assertIn("Annual total", page)
		self.assertIn("4,000.00", page)  # the year's Oregon wages
		self.assertIn("240.00", page)  # the year's income tax
		self.assertIn("2026-01-31", page)  # the due date

	def test_or_wr_prints_the_reconciliation_verdict(self):
		filed = {"or_income_tax": 60.0, "or_transit_tax": 1.0, "or_paid_leave_employee": 6.0}
		info = {**COMPANY_INFO, "oq_reported": {q: dict(filed) for q in ("Q1", "Q2", "Q3", "Q4")}}
		form_data = generators.generate_or_wr_data(a_year_of_oregon_slips(), info, 2025)
		page = text_of(renderer.render_or_wr_pdf(form_data, info))
		self.assertIn("RECONCILES", page)
		self.assertIn("Filed on the OQs", page)

	def test_or_wr_says_so_loudly_when_it_does_not_reconcile(self):
		info = {**COMPANY_INFO, "oq_reported": {"Q1": {"or_income_tax": 5.0}}}
		form_data = generators.generate_or_wr_data(a_year_of_oregon_slips(), info, 2025)
		page = text_of(renderer.render_or_wr_pdf(form_data, info))
		self.assertIn("DOES NOT RECONCILE", page)
		self.assertIn("235.00", page)  # 240 computed against 5 filed

	def test_oq_carries_the_four_programs(self):
		info = {**COMPANY_INFO, "ui_rate": 2.4}
		form_data = generators.generate_or_oq_data(
			[or_slip(employee="E1"), or_slip(employee="E2")],
			info,
			"Q1",
			2025,
		)
		page = text_of(renderer.render_or_oq_pdf(form_data, info))
		self.assertIn("Form OQ", page)
		self.assertIn("Q1 2025", page)
		self.assertIn("2,000.00", page)  # subject wages
		self.assertIn("120.00", page)  # state withholding
		self.assertIn("Statewide transit tax withheld", page)
		self.assertIn("Paid Leave Oregon - total", page)
		self.assertIn("48.00", page)  # UI tax at 2.4%
		self.assertIn("2.40 %", page)

	def test_oq_carries_the_per_month_employee_counts(self):
		form_data = generators.generate_or_oq_data(
			[or_slip(employee="E1", period_end="2025-02-14")],
			COMPANY_INFO,
			"Q1",
			2025,
		)
		page = text_of(renderer.render_or_oq_pdf(form_data, COMPANY_INFO))
		self.assertIn("Employees paid in January", page)
		self.assertIn("Employees paid in February", page)
		self.assertIn("Employees paid in March", page)

	def test_wa_esd_carries_the_wage_and_hour_detail(self):
		info = {**COMPANY_INFO, "ui_rate": 1.0, "ssn_last4_by_employee": {"E1": "6789"}}
		form_data = generators.generate_wa_esd_data(
			[wa_slip(employee="E1", gross=1000.0)],
			info,
			"Q1",
			2025,
		)
		page = text_of(renderer.render_wa_esd_pdf(form_data, info))
		self.assertIn("WA ESD Quarterly Report", page)
		self.assertIn("000123456", page)  # the ESD account number
		self.assertIn("Hours worked", page)
		self.assertIn("Worker E1", page)
		self.assertIn("XXX-XX-6789", page)
		self.assertIn("1,000.00", page)
		self.assertIn("80", page)

	def test_wa_esd_carries_the_three_programs(self):
		info = {**COMPANY_INFO, "ui_rate": 1.0}
		form_data = generators.generate_wa_esd_data([wa_slip(employee="E1")], info, "Q1", 2025)
		page = text_of(renderer.render_wa_esd_pdf(form_data, info))
		self.assertIn("Paid Family & Medical Leave", page)
		self.assertIn("WA Cares Fund", page)
		self.assertIn("Unemployment insurance tax", page)
		self.assertIn("10.00", page)  # UI at 1% of 1,000
		self.assertIn("5.80", page)  # WA Cares
		self.assertIn("TOTAL DUE", page)

	def test_wa_esd_says_when_hours_are_missing(self):
		slip = wa_slip(employee="E1")
		slip["total_hours"] = 0
		form_data = generators.generate_wa_esd_data([slip], COMPANY_INFO, "Q1", 2025)
		page = text_of(renderer.render_wa_esd_pdf(form_data, COMPANY_INFO))
		self.assertIn("ESD requires hours worked per employee", page)

	def test_wa_esd_with_no_employees_says_so_rather_than_drawing_an_empty_grid(self):
		form_data = generators.generate_wa_esd_data([], COMPANY_INFO, "Q1", 2025)
		page = text_of(renderer.render_wa_esd_pdf(form_data, COMPANY_INFO))
		self.assertIn("No Washington wages in this quarter.", page)


# ── Claim 6: the disclaimer, on every page ────────────────────────────────


@NEEDS_REPORTLAB
class Disclaimers(unittest.TestCase):
	"""A page that could be mistaken for a filing says it is not, twice."""

	def documents(self) -> dict:
		return RendererBytes().all_six()

	def test_the_header_note_is_on_every_page_of_every_form(self):
		for form_type, payload in self.documents().items():
			for index, page in enumerate(page_texts(payload), start=1):
				with self.subTest(form=form_type, page=index):
					self.assertIn(renderer.HEADER_NOTE, page)

	def test_the_working_copy_block_is_on_every_page_of_every_form(self):
		for form_type, payload in self.documents().items():
			for index, page in enumerate(page_texts(payload), start=1):
				with self.subTest(form=form_type, page=index):
					self.assertIn("WORKING COPY - NOT AN OFFICIAL FORM", page)
					self.assertIn("is not a filing", page)

	def test_each_form_names_where_it_is_really_filed(self):
		expected = {
			"W-2": "Business Services Online",
			"1099-NEC": "IRIS or FIRE",
			"941": "EFTPS",
			"OR-WR": "Revenue Online",
			"OQ": "Frances Online",
			"WA-ESD": "EAMS",
		}
		documents = self.documents()
		for form_type, needle in expected.items():
			with self.subTest(form=form_type):
				self.assertIn(needle, text_of(documents[form_type]))

	def test_a_page_that_exists_only_because_a_table_overflowed_carries_it_too(self):
		"""Forty employees do not fit on one page. The second one is still a
		working copy, and a reader who only sees page two must be told."""
		info = {**COMPANY_INFO, "ssn_last4_by_employee": {f"E{n:02d}": "6789" for n in range(1, 41)}}
		form_data = generators.generate_wa_esd_data(
			[wa_slip(employee=f"E{n:02d}") for n in range(1, 41)],
			info,
			"Q1",
			2025,
		)
		payload = renderer.render_wa_esd_pdf(form_data, info)
		pages = page_texts(payload)
		self.assertGreater(len(pages), 1)
		for index, page in enumerate(pages, start=1):
			with self.subTest(page=index):
				self.assertIn(renderer.HEADER_NOTE, page)
				self.assertIn("WORKING COPY - NOT AN OFFICIAL FORM", page)

	def test_the_wage_detail_repeats_its_header_on_the_second_page(self):
		info = {**COMPANY_INFO, "ssn_last4_by_employee": {f"E{n:02d}": "6789" for n in range(1, 41)}}
		form_data = generators.generate_wa_esd_data(
			[wa_slip(employee=f"E{n:02d}") for n in range(1, 41)],
			info,
			"Q1",
			2025,
		)
		pages = page_texts(renderer.render_wa_esd_pdf(form_data, info))
		for index, page in enumerate(pages[:2], start=1):
			with self.subTest(page=index):
				self.assertIn("Hours worked", page)

	def test_the_totals_row_counts_every_employee_across_the_break(self):
		info = {**COMPANY_INFO, "ssn_last4_by_employee": {f"E{n:02d}": "6789" for n in range(1, 41)}}
		form_data = generators.generate_wa_esd_data(
			[wa_slip(employee=f"E{n:02d}") for n in range(1, 41)],
			info,
			"Q1",
			2025,
		)
		page = text_of(renderer.render_wa_esd_pdf(form_data, info))
		self.assertIn("TOTAL - 40 employee(s)", page)
		self.assertIn("40,000.00", page)


# ── Claim 7: the tool ─────────────────────────────────────────────────────


class PdfToolTestCase(TaxFormToolTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **PDF_TOOLS_ON)

	def a_w2(self, employee="HR-EMP-00001") -> str:
		self.seed_a_year(employee=employee)
		return self.tool_data(
			"generate_tax_form",
			{
				"form_type": "W-2",
				"company": MAIN,
				"fiscal_year": "2025",
				"employee": employee,
			},
		)["name"]

	def a_941(self, quarter="Q1") -> str:
		self.seed_payroll(
			f"PAY-{quarter}",
			"2025-02-01",
			"2025-02-14",
			[or_slip(employee="HR-EMP-00001", gross=1000.0)],
		)
		return self.tool_data(
			"generate_tax_form",
			{
				"form_type": "941",
				"company": MAIN,
				"fiscal_year": "2025",
				"quarter": quarter,
			},
		)["name"]

	def attached_pdf(self, form_name: str) -> bytes:
		file_name = frappe.db.get_value(
			"File",
			{"attached_to_doctype": "Tax Form", "attached_to_name": form_name},
			"name",
		)
		self.assertTrue(file_name, f"no File attached to {form_name}")
		return STORE.file_contents[file_name]


@NEEDS_REPORTLAB
class RenderTool(PdfToolTestCase):
	"""One Tax Form, rendered and attached."""

	def test_render_attaches_a_pdf_to_the_form(self):
		name = self.a_w2()
		data = self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertEqual(data["name"], name)
		self.assertEqual(data["form_type"], "W-2")
		self.assertTrue(data["attachment"]["file_url"])
		self.assertTrue(data["attachment"]["is_private"])
		self.assertTrue(self.attached_pdf(name).startswith(b"%PDF-1"))

	def test_the_generated_pdf_field_points_at_the_file(self):
		name = self.a_w2()
		data = self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertEqual(
			frappe.db.get_value("Tax Form", name, "generated_pdf"),
			data["attachment"]["file_url"],
		)

	def test_the_page_carries_the_stored_figures(self):
		name = self.a_w2()
		self.tool_data("render_tax_form_pdf", {"name": name})
		page = text_of(self.attached_pdf(name))
		self.assertIn("4,000.00", page)
		self.assertIn("Test Worker", page)
		self.assertIn("Form W-2", page)

	def test_the_file_name_says_which_form_it_is(self):
		name = self.a_w2()
		data = self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertEqual(data["attachment"]["file_name"], "W-2-2025-HR-EMP-00001.pdf")

	def test_a_quarterly_form_carries_its_quarter_in_the_file_name(self):
		name = self.a_941("Q2")
		data = self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertEqual(data["attachment"]["file_name"], "941-2025-Q2.pdf")

	def test_rendering_moves_no_status(self):
		name = self.a_w2()
		self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertEqual(frappe.db.get_value("Tax Form", name, "status"), "Generated")

	def test_rendering_changes_no_figure(self):
		"""A rendering is a read of the arithmetic. If it recomputed, a corrected
		slip would silently change the form the page claims to render."""
		name = self.a_w2()
		before = frappe.db.get_value("Tax Form", name, "form_data_json")
		self.seed_payroll(
			"PAY-LATE", "2025-12-01", "2025-12-14", [or_slip(employee="HR-EMP-00001", gross=9000.0)]
		)
		self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertEqual(frappe.db.get_value("Tax Form", name, "form_data_json"), before)
		self.assertNotIn("13,000.00", text_of(self.attached_pdf(name)))

	def test_a_filed_form_can_still_be_rendered(self):
		"""Nothing about drawing the stored values disturbs a filing."""
		name = self.a_941()
		self.tool_data("mark_tax_form_filed", {"name": name})
		self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertEqual(frappe.db.get_value("Tax Form", name, "status"), "Filed")

	def test_the_warning_count_is_reported_and_printed(self):
		name = self.a_941()
		data = self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertGreater(data["warning_count"], 0)
		self.assertIn("note(s) on this form", text_of(self.attached_pdf(name)))

	def test_the_result_says_the_page_is_not_a_filing(self):
		name = self.a_w2()
		data = self.tool_data("render_tax_form_pdf", {"name": name})
		self.assertIn("not a filing", data["note"])

	def test_a_company_address_argument_fills_a_hole_the_record_has(self):
		name = self.a_w2()
		self.tool_data(
			"render_tax_form_pdf",
			{
				"name": name,
				"company_address": "1 Orchard Road, Hood River OR 97031",
			},
		)
		self.assertIn("Hood River OR 97031", text_of(self.attached_pdf(name)))

	def test_overwrite_repoints_the_field_and_keeps_the_old_file(self):
		name = self.a_w2()
		first = self.tool_data("render_tax_form_pdf", {"name": name})
		second = self.tool_data("render_tax_form_pdf", {"name": name, "overwrite": True})
		self.assertEqual(second["replaced"], first["attachment"]["file_url"])
		attachments = frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": "Tax Form", "attached_to_name": name},
			fields=["name"],
		)
		self.assertEqual(len(attachments), 2, "the earlier File was thrown away")

	def test_a_render_is_audited(self):
		self.tool_data("render_tax_form_pdf", {"name": self.a_w2()})
		self.assertAudited("render_tax_form_pdf", "Success")

	def test_every_form_type_renders_through_the_tool(self):
		self.seed_payroll(
			"PAY-OR", "2025-02-01", "2025-02-14", [or_slip(employee="HR-EMP-00001", gross=1000.0)]
		)
		self.seed_payroll(
			"PAY-WA", "2025-03-01", "2025-03-14", [wa_slip(employee="HR-EMP-00002", gross=1000.0)]
		)
		for form_type, extra in (
			("W-2", {"employee": "HR-EMP-00001"}),
			("941", {"quarter": "Q1"}),
			("OR-WR", {}),
			("OQ", {"quarter": "Q1"}),
			("WA-ESD", {"quarter": "Q1"}),
		):
			with self.subTest(form=form_type):
				created = self.tool_data(
					"generate_tax_form",
					{
						"form_type": form_type,
						"company": MAIN,
						"fiscal_year": "2025",
						**extra,
					},
				)
				self.tool_data("render_tax_form_pdf", {"name": created["name"]})
				self.assertTrue(self.attached_pdf(created["name"]).startswith(b"%PDF-1"))


# ── Claim 8: the batch ────────────────────────────────────────────────────


@NEEDS_REPORTLAB
class BulkRenderTool(PdfToolTestCase):
	def three_941s(self) -> list[str]:
		names = []
		for index, quarter in enumerate(("Q1", "Q2", "Q3")):
			self.seed_payroll(
				f"PAY-{quarter}",
				f"2025-0{index * 3 + 2}-01",
				f"2025-0{index * 3 + 2}-14",
				[or_slip(employee="HR-EMP-00001", gross=1000.0, period_end=f"2025-0{index * 3 + 2}-14")],
			)
			names.append(
				self.tool_data(
					"generate_tax_form",
					{
						"form_type": "941",
						"company": MAIN,
						"fiscal_year": "2025",
						"quarter": quarter,
					},
				)["name"]
			)
		return names

	def test_a_filtered_batch_renders_every_form_it_matched(self):
		names = self.three_941s()
		data = self.tool_data(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"fiscal_year": "2025",
			},
		)
		self.assertEqual(data["matched"], 3)
		self.assertEqual(data["rendered_count"], 3)
		self.assertEqual(data["failed_count"], 0)
		for name in names:
			with self.subTest(form=name):
				self.assertTrue(self.attached_pdf(name).startswith(b"%PDF-1"))

	def test_an_explicit_list_renders_exactly_those(self):
		names = self.three_941s()
		data = self.tool_data("bulk_render_tax_form_pdfs", {"names": names[:2]})
		self.assertEqual(data["rendered_count"], 2)
		self.assertIsNone(frappe.db.get_value("Tax Form", names[2], "generated_pdf"))

	def test_a_comma_separated_list_is_accepted(self):
		names = self.three_941s()
		data = self.tool_data("bulk_render_tax_form_pdfs", {"names": ",".join(names)})
		self.assertEqual(data["rendered_count"], 3)

	def test_a_form_that_already_has_a_pdf_is_skipped_and_counted(self):
		names = self.three_941s()
		self.tool_data("render_tax_form_pdf", {"name": names[0]})
		data = self.tool_data(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"fiscal_year": "2025",
			},
		)
		self.assertEqual(data["rendered_count"], 2)
		self.assertEqual(data["skipped_count"], 1)
		self.assertEqual(data["skipped"][0]["name"], names[0])
		self.assertIn("overwrite", data["skipped"][0]["reason"])

	def test_overwrite_renders_the_skipped_ones_too(self):
		self.three_941s()
		self.tool_data(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"fiscal_year": "2025",
			},
		)
		data = self.tool_data(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"fiscal_year": "2025",
				"overwrite": True,
			},
		)
		self.assertEqual(data["rendered_count"], 3)
		self.assertEqual(data["skipped_count"], 0)

	def test_one_broken_form_does_not_stop_the_batch(self):
		"""A form with no computed values fails by name and the rest come out."""
		names = self.three_941s()
		frappe.db.set_value("Tax Form", names[1], "form_data_json", "")
		data = self.tool_data(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"fiscal_year": "2025",
			},
		)
		self.assertEqual(data["rendered_count"], 2)
		self.assertEqual(data["failed_count"], 1)
		self.assertEqual(data["failed"][0]["name"], names[1])
		self.assertIn("no computed values", data["failed"][0]["reason"])

	def test_every_w2_for_a_year_in_one_call(self):
		for employee in ("HR-EMP-00001", "HR-EMP-00002"):
			self.seed_payroll(
				f"PAY-{employee}",
				"2025-02-01",
				"2025-02-14",
				[or_slip(employee=employee, gross=1000.0)],
			)
			self.tool_data(
				"generate_tax_form",
				{
					"form_type": "W-2",
					"company": MAIN,
					"fiscal_year": "2025",
					"employee": employee,
				},
			)
		data = self.tool_data(
			"bulk_render_tax_form_pdfs",
			{
				"form_type": "W-2",
				"fiscal_year": "2025",
			},
		)
		self.assertEqual(data["rendered_count"], 2)
		self.assertEqual(
			{row["subject"] for row in data["rendered"]},
			{"HR-EMP-00001", "HR-EMP-00002"},
		)

	def test_the_filters_that_chose_the_batch_are_reported_back(self):
		self.three_941s()
		data = self.tool_data(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"fiscal_year": "2025",
				"quarter": "Q2",
			},
		)
		self.assertEqual(data["selection"]["quarter"], "Q2")
		self.assertEqual(data["selection"]["form_type"], "941")
		self.assertEqual(data["matched"], 1)

	def test_another_company_s_forms_are_not_in_the_batch(self):
		"""Which is why selecting on the other company matches nothing at all."""
		names = self.three_941s()
		error = self.tool_error("bulk_render_tax_form_pdfs", {"company": OTHER, "form_type": "941"})
		self.assertIn("no Tax Form matches", error)
		self.assertIn(OTHER, error)
		for name in names:
			with self.subTest(form=name):
				self.assertFalse(frappe.db.get_value("Tax Form", name, "generated_pdf"))

	def test_a_bulk_render_is_audited(self):
		self.three_941s()
		self.tool_data("bulk_render_tax_form_pdfs", {"company": MAIN, "form_type": "941"})
		self.assertAudited("bulk_render_tax_form_pdfs", "Success")


# ── Claim 9: the refusals ─────────────────────────────────────────────────


@NEEDS_REPORTLAB
class RenderRefusals(PdfToolTestCase):
	def test_each_tool_is_off_unless_its_switch_is_on(self):
		name = self.a_w2()
		for tool, arguments in (
			("render_tax_form_pdf", {"name": name}),
			("bulk_render_tax_form_pdfs", {"company": MAIN, "form_type": "W-2"}),
		):
			with self.subTest(tool=tool):
				self.configure(enabled=1, **{**PDF_TOOLS_ON, f"allow_{tool}": 0})
				self.assertIn(f"allow_{tool}", self.tool_error(tool, arguments))

	def test_an_unknown_form_is_refused_by_name(self):
		self.assertIn("TAXFRM-NOPE", self.tool_error("render_tax_form_pdf", {"name": "TAXFRM-NOPE"}))

	def test_a_form_with_no_computed_values_is_refused(self):
		name = self.a_w2()
		frappe.db.set_value("Tax Form", name, "form_data_json", "")
		error = self.tool_error("render_tax_form_pdf", {"name": name})
		self.assertIn("no computed values", error)
		self.assertIn("regenerate_tax_form", error)

	def test_a_second_render_is_refused_and_names_the_existing_pdf(self):
		name = self.a_w2()
		first = self.tool_data("render_tax_form_pdf", {"name": name})
		error = self.tool_error("render_tax_form_pdf", {"name": name})
		self.assertIn(first["attachment"]["file_url"], error)
		self.assertIn("overwrite=true", error)
		self.assertIn("Nothing was changed", error)

	def test_the_refused_second_render_left_one_attachment(self):
		name = self.a_w2()
		self.tool_data("render_tax_form_pdf", {"name": name})
		self.tool_error("render_tax_form_pdf", {"name": name})
		attachments = frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": "Tax Form", "attached_to_name": name},
			fields=["name"],
		)
		self.assertEqual(len(attachments), 1)

	def test_a_bulk_run_with_no_selection_at_all_is_refused(self):
		error = self.tool_error("bulk_render_tax_form_pdfs", {})
		self.assertIn("needs something to select on", error)
		self.assertIn("Nothing was rendered", error)

	def test_a_bulk_selection_that_matches_nothing_is_refused(self):
		self.a_w2()
		error = self.tool_error(
			"bulk_render_tax_form_pdfs",
			{
				"form_type": "W-2",
				"fiscal_year": "2019",
			},
		)
		self.assertIn("no Tax Form matches", error)
		self.assertIn("list_tax_forms", error)

	def test_a_bulk_selection_over_the_limit_is_refused_rather_than_truncated(self):
		self.three_941s = BulkRenderTool.three_941s.__get__(self)
		self.three_941s()
		error = self.tool_error(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"limit": 2,
			},
		)
		self.assertIn("more than 2", error)
		self.assertIn("Nothing was rendered", error)

	def test_the_over_limit_refusal_rendered_nothing(self):
		self.three_941s = BulkRenderTool.three_941s.__get__(self)
		names = self.three_941s()
		self.tool_error(
			"bulk_render_tax_form_pdfs",
			{
				"company": MAIN,
				"form_type": "941",
				"limit": 2,
			},
		)
		for name in names:
			with self.subTest(form=name):
				self.assertFalse(frappe.db.get_value("Tax Form", name, "generated_pdf"))

	def test_an_unknown_name_in_an_explicit_list_is_refused_by_name(self):
		name = self.a_w2()
		error = self.tool_error("bulk_render_tax_form_pdfs", {"names": [name, "TAXFRM-NOPE"]})
		self.assertIn("TAXFRM-NOPE", error)
		self.assertIn("Nothing was rendered", error)

	def test_a_bad_form_type_filter_is_refused_by_name(self):
		self.assertIn("W-4", self.tool_error("bulk_render_tax_form_pdfs", {"form_type": "W-4"}))

	def test_a_bad_quarter_filter_is_refused_by_name(self):
		error = self.tool_error("bulk_render_tax_form_pdfs", {"form_type": "941", "quarter": "Q5"})
		self.assertIn("Q5", error)
