# SPDX-License-Identifier: MIT
"""The pay stub PDF — v0.91.0.

EIGHT CLAIMS.

1. `StubAvailability` — the module says whether it can draw, and the tool goes
   unavailable by name on a bench without reportlab.
2. `StubIsNotAWorkingCopy` — the page carries the stub's own header and footer
   and NOT the tax forms' working-copy stamp, and the six tax forms still carry
   theirs.
3. `EarningsBalance` — the itemised lines always add up to earned gross,
   including when the balance is negative and when no rate is known at all.
4. `DeductionItemisation` — the four named taxes, the derived remainder, and the
   forward-compatible itemised list.
5. `StubPage` — the stored figures reach the page, read back out of the bytes.
6. `YearToDate` — the calendar year, this period included, labelled as calendar,
   and omitted rather than zeroed when nothing could be summed.
7. `StubTool` — the tool renders, attaches, resolves the hourly rate off the
   salary structure and recomputes nothing.
8. `StubRefusals` — the switch, the unknown run, the person not on it, the
   second render without overwrite.

HOW A PAGE IS READ BACK is `test_tax_form_pdfs.page_texts`: the sheet writes
UNCOMPRESSED content streams on purpose, so the text on a page is recoverable
with a regular expression and no PDF parser. `pay_stub_pdf` borrows that sheet,
so it inherits the property and the reader.
"""

import unittest

import frappe

from erpnext_mcp import form_pdf_renderer, pay_stub_pdf

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import STORE
from .test_payroll_register import entry, slip
from .test_tax_form_pdfs import text_of

HAVE_REPORTLAB = pay_stub_pdf.available()
NEEDS_REPORTLAB = unittest.skipUnless(
	HAVE_REPORTLAB,
	"reportlab is not installed on this bench — the pay stub tool is supposed to go quietly "
	"unavailable, which `StubAvailability` asserts without it",
)

STUB_ON = {"allow_render_pay_stub": 1, "allow_get_payroll_entry": 1}

COMPANY_INFO = {
	"name": "Example Trading Co",
	"ein": "12-3456789",
	"address": "1 Orchard Road, Hood River OR 97031",
}


def a_stub(**overrides) -> dict:
	"""One flattened slip plus its period, as the tool assembles it."""
	payload = {
		"payroll_entry": "PAY-2026-0001",
		"company": MAIN,
		"pay_period_start": "2026-06-01",
		"pay_period_end": "2026-06-14",
		"pay_frequency": "Biweekly",
		"status": "Submitted",
		"employee": "HR-EMP-00001",
		"employee_name": "Maria Garcia",
		"work_state": "OR",
		"pay_type": "Hourly",
		"hourly_rate": 20.0,
		"regular_hours": 80.0,
		"overtime_hours": 6.0,
		"total_hours": 86.0,
		"piece_units": 0.0,
		"piece_rate": 0.0,
		"earned_gross": 1780.0,
		"minimum_wage_makeup": 0.0,
		"gross_pay": 1780.0,
		"federal_withholding": 150.0,
		"state_withholding": 90.0,
		"social_security": 110.36,
		"medicare": 25.81,
		"total_deductions": 376.17,
		"net_pay": 1403.83,
		"social_security_employer": 110.36,
		"medicare_employer": 25.81,
		"futa": 10.68,
		"state_unemployment": 21.36,
		"state_employer_other": 5.0,
		"total_employer_taxes": 173.21,
	}
	payload.update(overrides)
	return payload


# ── Claim 1: availability ─────────────────────────────────────────────────


class StubAvailability(V12TestCase):
	"""A bench without reportlab loses one tool by name and nothing else."""

	def test_available_matches_the_import(self):
		self.assertEqual(pay_stub_pdf.available(), HAVE_REPORTLAB)

	def test_it_reports_the_same_availability_as_the_sheet_it_borrows(self):
		self.assertEqual(pay_stub_pdf.available(), form_pdf_renderer.available())

	def test_the_requires_sentence_names_the_package_and_the_fix(self):
		sentence = pay_stub_pdf.requires_sentence()
		self.assertIn("reportlab", sentence)
		self.assertIn("pip install", sentence)

	def test_the_tool_is_advertised_only_where_a_page_can_be_drawn(self):
		from erpnext_mcp import registry

		self.assertEqual(registry.is_available("render_pay_stub"), HAVE_REPORTLAB)

	def test_the_payroll_reads_are_unaffected_by_the_library(self):
		"""The figures are the deliverable; the page is a convenience."""
		from erpnext_mcp import registry

		for name in ("get_payroll_entry", "get_payroll_register", "list_payroll_entries"):
			with self.subTest(tool=name):
				self.assertTrue(registry.is_available(name))


# ── Claim 2: it is not a working copy ─────────────────────────────────────


@NEEDS_REPORTLAB
class StubIsNotAWorkingCopy(unittest.TestCase):
	"""The one thing this page does differently from every other in the app."""

	def setUp(self):
		self.page = text_of(pay_stub_pdf.render_pay_stub(a_stub(), COMPANY_INFO))

	def test_the_working_copy_stamp_is_nowhere_on_it(self):
		"""A pay stub is not a copy of a filing held somewhere else — it is the
		statement itself, and stamping it a draft would be a false statement
		about the one page on this site that IS the record it looks like."""
		self.assertNotIn("WORKING COPY", self.page)
		self.assertNotIn("NOT AN OFFICIAL FORM", self.page)
		self.assertNotIn("verify before filing", self.page)

	def test_it_carries_its_own_header_and_footer_instead(self):
		self.assertIn("Statement of earnings and deductions", self.page)
		self.assertIn("raise it with your employer", self.page)

	def test_the_six_tax_forms_still_carry_theirs(self):
		"""The sheet's furniture became an argument; the defaults did not move."""
		from erpnext_mcp import form_generators as generators

		from .test_tax_forms import COMPANY_INFO as TAX_COMPANY
		from .test_tax_forms import or_slip

		form_data = generators.generate_941_data(
			[or_slip(employee="E1")], TAX_COMPANY, "Q1", 2025,
		)
		page = text_of(form_pdf_renderer.render_941_pdf(form_data, TAX_COMPANY))
		self.assertIn("WORKING COPY", page)
		self.assertIn("verify before filing", page)

	def test_no_social_security_number_reaches_the_page(self):
		"""Neither statute asks for one, and a wage statement gets left in a
		truck. Asserted on a stub that was handed one on purpose.

		The digits are chosen not to occur in the fixture's EIN, which is a
		number the page IS meant to carry — an assertion that failed on the
		employer's own tax ID would prove nothing about the employee's."""
		page = text_of(pay_stub_pdf.render_pay_stub(
			a_stub(ssn="987-65-4321", ssn_last4="4321"), COMPANY_INFO,
		))
		self.assertNotIn("987-65-4321", page)
		self.assertNotIn("4321", page)


# ── Claim 3: the earnings always balance ──────────────────────────────────


class EarningsBalance(unittest.TestCase):
	"""The lines add up to earned gross. Every time, including the awkward times."""

	def total(self, stub) -> float:
		return round(sum(line["amount"] for line in pay_stub_pdf.earnings_lines(stub)), 2)

	def test_the_lines_sum_to_earned_gross(self):
		stub = a_stub()
		self.assertEqual(self.total(stub), stub["earned_gross"])

	def test_hourly_work_itemises_regular_and_overtime_at_the_multiplier(self):
		lines = pay_stub_pdf.earnings_lines(a_stub())
		self.assertEqual(lines[0]["label"], "Regular hours")
		self.assertEqual(lines[0]["amount"], 1600.0)
		self.assertIn("1.5x", lines[1]["label"])
		self.assertEqual(lines[1]["amount"], 180.0)

	def test_break_pay_and_the_premium_land_on_a_named_balancing_line(self):
		"""The engine computes gross in one pass; a page that printed three
		lines not adding up to the gross beneath them is what starts a claim."""
		stub = a_stub(earned_gross=1850.0)
		lines = pay_stub_pdf.earnings_lines(stub)
		self.assertEqual(lines[-1]["label"], "Other earnings (break pay, overtime premium)")
		self.assertEqual(lines[-1]["amount"], 70.0)
		self.assertEqual(self.total(stub), 1850.0)

	def test_a_negative_balance_is_drawn_rather_than_clamped_to_zero(self):
		"""It means the rate this page was given is not the rate the slip was
		computed at. A line reading -84.00 is a page somebody queries; a zero is
		a page that looks right and is not."""
		stub = a_stub(earned_gross=1700.0)
		lines = pay_stub_pdf.earnings_lines(stub)
		self.assertEqual(lines[-1]["amount"], -80.0)
		self.assertEqual(self.total(stub), 1700.0)

	def test_piece_work_itemises_units_at_the_piece_rate(self):
		stub = a_stub(
			pay_type="Piece Rate", hourly_rate=0.0, regular_hours=0.0, overtime_hours=0.0,
			piece_units=400.0, piece_rate=3.0, earned_gross=1200.0, gross_pay=1200.0,
		)
		lines = pay_stub_pdf.earnings_lines(stub)
		self.assertEqual(lines[0]["label"], "Piece work")
		self.assertEqual(lines[0]["amount"], 1200.0)
		self.assertEqual(self.total(stub), 1200.0)

	def test_with_no_rate_at_all_everything_lands_on_one_unnamed_earnings_line(self):
		"""A line that cannot be priced from the record is better absent than
		priced from a guess."""
		stub = a_stub(hourly_rate=0.0)
		lines = pay_stub_pdf.earnings_lines(stub)
		self.assertEqual(len(lines), 1)
		self.assertEqual(lines[0]["label"], "Earnings")
		self.assertEqual(lines[0]["amount"], 1780.0)

	def test_an_exactly_itemised_stub_grows_no_balancing_line(self):
		stub = a_stub(earned_gross=1780.0)
		self.assertEqual(len(pay_stub_pdf.earnings_lines(stub)), 2)


# ── Claim 4: the deductions ───────────────────────────────────────────────


class DeductionItemisation(unittest.TestCase):
	"""The four named taxes, then whatever else the total says is there."""

	def test_the_four_named_taxes_come_first_and_in_order(self):
		labels = [line["label"] for line in pay_stub_pdf.deduction_lines(a_stub())]
		self.assertEqual(
			labels[:4],
			["Federal income tax", "State income tax", "Social Security", "Medicare"],
		)

	def test_the_remainder_of_total_deductions_is_shown_rather_than_dropped(self):
		"""THE FORWARD-COMPATIBILITY CLAIM. A garnishment written by a later
		release lands inside total_deductions and appears here the day it lands,
		with no change to this module."""
		lines = pay_stub_pdf.deduction_lines(a_stub(total_deductions=451.17))
		self.assertEqual(lines[-1]["label"], "Other deductions")
		self.assertEqual(lines[-1]["amount"], 75.0)

	def test_the_lines_sum_to_total_deductions(self):
		stub = a_stub(total_deductions=451.17)
		self.assertAlmostEqual(
			sum(line["amount"] for line in pay_stub_pdf.deduction_lines(stub)),
			stub["total_deductions"],
			places=2,
		)

	def test_an_itemised_list_is_printed_by_name_instead_of_as_a_lump(self):
		lines = pay_stub_pdf.deduction_lines(a_stub(
			total_deductions=451.17,
			deduction_lines=[
				{"label": "Child Support", "amount": 50.0},
				{"label": "401(k)", "amount": 25.0},
			],
		))
		self.assertEqual([line["label"] for line in lines[4:]], ["Child Support", "401(k)"])
		self.assertNotIn("Other deductions", [line["label"] for line in lines])

	def test_a_zero_tax_is_left_off_rather_than_printed_as_zero(self):
		labels = [line["label"] for line in pay_stub_pdf.deduction_lines(
			a_stub(state_withholding=0.0, total_deductions=286.17),
		)]
		self.assertNotIn("State income tax", labels)

	def test_a_stub_with_no_deductions_itemises_nothing(self):
		self.assertEqual(
			pay_stub_pdf.deduction_lines(a_stub(
				federal_withholding=0.0, state_withholding=0.0,
				social_security=0.0, medicare=0.0, total_deductions=0.0,
			)),
			[],
		)


# ── Claim 5: the page ─────────────────────────────────────────────────────


@NEEDS_REPORTLAB
class StubPage(unittest.TestCase):
	"""The stored figures reach the page, read back out of the bytes."""

	def setUp(self):
		self.pdf = pay_stub_pdf.render_pay_stub(a_stub(), COMPANY_INFO)
		self.page = text_of(self.pdf)

	def test_it_is_a_pdf_a_reader_will_open(self):
		self.assertTrue(self.pdf.startswith(b"%PDF-1"))
		self.assertIn(b"%%EOF", self.pdf)
		self.assertGreater(len(self.pdf), 1500)

	def test_the_person_and_the_period_are_on_it(self):
		self.assertIn("Maria Garcia", self.page)
		self.assertIn("HR-EMP-00001", self.page)
		self.assertIn("2026-06-01", self.page)
		self.assertIn("2026-06-14", self.page)

	def test_the_employer_block_is_on_it(self):
		self.assertIn("Example Trading Co", self.page)
		self.assertIn("Hood River OR 97031", self.page)
		self.assertIn("12-3456789", self.page)

	def test_gross_deductions_and_net_are_on_it(self):
		self.assertIn("1,780.00", self.page)
		self.assertIn("376.17", self.page)
		self.assertIn("1,403.83", self.page)
		self.assertIn("NET PAY", self.page)

	def test_the_hours_and_the_rate_are_on_it(self):
		self.assertIn("80.00", self.page)
		self.assertIn("20.00", self.page)
		self.assertIn("30.00", self.page)  # the overtime rate, 20 x 1.5

	def test_the_employer_section_is_off_by_default(self):
		"""Some workers read any figure on a stub as something taken off them."""
		self.assertNotIn("Employer contributions", self.page)
		self.assertNotIn("173.21", self.page)

	def test_asking_for_it_draws_it_with_the_sentence_that_prevents_the_misreading(self):
		page = text_of(pay_stub_pdf.render_pay_stub(
			a_stub(), COMPANY_INFO, show_employer_contributions=True,
		))
		self.assertIn("Employer contributions", page)
		self.assertIn("NOT DEDUCTED FROM YOUR PAY", page)
		self.assertIn("173.21", page)

	def test_minimum_wage_makeup_is_shown_as_an_addition_and_said_to_be_one(self):
		"""Anything other than zero here is a rate set below what the hours are
		worth. The worker was paid lawfully and the page must not read as though
		something was taken."""
		page = text_of(pay_stub_pdf.render_pay_stub(
			a_stub(earned_gross=1700.0, minimum_wage_makeup=80.0), COMPANY_INFO,
		))
		self.assertIn("Minimum wage adjustment", page)
		self.assertIn("Nothing was deducted", page)

	def test_the_file_name_sorts_by_run_then_person_then_period(self):
		self.assertEqual(
			pay_stub_pdf.file_name_for(a_stub()),
			"Pay-Stub-PAY-2026-0001-HR-EMP-00001-2026-06-14.pdf",
		)


# ── Claim 6: year to date ─────────────────────────────────────────────────


@NEEDS_REPORTLAB
class YearToDate(unittest.TestCase):
	"""The calendar year, said to be the calendar year, or absent."""

	YTD = {
		"year": "2026",
		"periods": 12,
		"gross_pay": 21360.0,
		"federal_withholding": 1800.0,
		"state_withholding": 1080.0,
		"social_security": 1324.32,
		"medicare": 309.72,
		"total_deductions": 4514.04,
		"net_pay": 16845.96,
	}

	def test_the_section_is_labelled_calendar_rather_than_just_ytd(self):
		"""This site's fiscal year may close after harvest. A stub whose YTD
		withholding cannot be reconciled against the W-2 is worse than none."""
		page = text_of(pay_stub_pdf.render_pay_stub(a_stub(ytd=self.YTD), COMPANY_INFO))
		self.assertIn("Year to date (calendar 2026)", page)
		self.assertIn("Withholding years are calendar years", page)

	def test_every_ytd_column_reaches_the_page(self):
		page = text_of(pay_stub_pdf.render_pay_stub(a_stub(ytd=self.YTD), COMPANY_INFO))
		for figure in ("21,360.00", "1,800.00", "1,324.32", "16,845.96"):
			with self.subTest(figure=figure):
				self.assertIn(figure, page)

	def test_it_is_omitted_entirely_rather_than_drawn_as_zeros(self):
		"""A column of 0.00 next to 'Year to date' reads as a year in which
		nothing was withheld, which is a different claim from 'not computed'."""
		page = text_of(pay_stub_pdf.render_pay_stub(a_stub(), COMPANY_INFO))
		self.assertNotIn("Year to date", page)


# ── Claim 7: the tool ─────────────────────────────────────────────────────


class StubToolTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **STUB_ON)
		STORE.seed("Farm Salary Structure", [{
			"name": "SAL-0001",
			"employee": "HR-EMP-00001",
			"employee_name": "Maria Garcia",
			"company": MAIN,
			"pay_type": "Hourly",
			"base_rate": 20.0,
			"hourly_rate": 0.0,
			"is_active": 1,
			"effective_from": "2026-01-01",
		}])

	def a_run(self, name="PAY-2026-0002", start="2026-06-15", end="2026-06-28", **kwargs):
		STORE.seed("Farm Payroll Entry", [entry(name, start, end, [
			slip("HR-EMP-00001", name="Maria Garcia", gross=1600.0,
			     salary_structure="SAL-0001", regular_hours=80.0, hours=80.0),
			slip("HR-EMP-00002", name="Ana Ruiz", gross=2000.0),
		], **kwargs)])
		return name

	def attached(self, run: str, index: int = 0) -> bytes:
		rows = frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": "Farm Payroll Entry", "attached_to_name": run},
			fields=["name"],
		)
		self.assertTrue(rows, f"no File attached to {run}")
		return STORE.file_contents[rows[index]["name"]]


@NEEDS_REPORTLAB
class StubTool(StubToolTestCase):
	"""It renders, attaches, and recomputes nothing."""

	def test_it_attaches_a_private_pdf_to_the_payroll_entry(self):
		run = self.a_run()
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
		})
		self.assertEqual(data["payroll_entry"], run)
		self.assertEqual(data["employee"], "HR-EMP-00001")
		self.assertTrue(data["file_url"])
		self.assertTrue(data["attachment"]["is_private"])
		self.assertTrue(self.attached(run).startswith(b"%PDF-1"))

	def test_it_attaches_to_the_record_and_not_to_a_field(self):
		"""A run carries one stub per employee and the doctype has one document;
		a field would hold whichever was rendered last and lose the rest."""
		run = self.a_run()
		self.tool_data("render_pay_stub", {"payroll_entry": run, "employee": "HR-EMP-00001"})
		self.tool_data("render_pay_stub", {"payroll_entry": run, "employee": "HR-EMP-00002"})
		rows = frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": "Farm Payroll Entry", "attached_to_name": run},
			fields=["name", "file_name"],
		)
		self.assertEqual(len(rows), 2)
		self.assertEqual(len({row["file_name"] for row in rows}), 2)

	def test_the_hourly_rate_comes_off_the_salary_structure(self):
		"""The slip stores hours and not a wage, so the rate has to be resolved
		at render time — and it is the only thing that is."""
		run = self.a_run()
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
		})
		self.assertEqual(data["hourly_rate"], 20.0)
		self.assertIn("20.00", text_of(self.attached(run)))

	def test_a_slip_with_no_structure_still_renders(self):
		"""Everything lands on the balancing line. A stub that refused over a
		missing rate would leave the worker with no statement at all."""
		run = self.a_run()
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00002",
		})
		self.assertIsNone(data["hourly_rate"])
		self.assertIn("2,000.00", text_of(self.attached(run, index=0)))

	def test_the_employee_can_be_named_by_the_name_on_the_slip(self):
		"""The person asking for a stub is holding a piece of paper with a name
		on it rather than a docname."""
		run = self.a_run()
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "Maria Garcia",
		})
		self.assertEqual(data["employee"], "HR-EMP-00001")

	def test_rendering_moves_no_status_and_changes_no_figure(self):
		run = self.a_run(status="Calculated")
		before = frappe.db.get_value("Farm Payroll Entry", run, "total_gross")
		self.tool_data("render_pay_stub", {"payroll_entry": run, "employee": "HR-EMP-00001"})
		self.assertEqual(frappe.db.get_value("Farm Payroll Entry", run, "status"), "Calculated")
		self.assertEqual(frappe.db.get_value("Farm Payroll Entry", run, "total_gross"), before)

	def test_the_result_says_the_ytd_year_is_the_calendar_year(self):
		run = self.a_run()
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
		})
		self.assertIn("CALENDAR", data["note"])

	def test_the_employer_section_is_a_choice_on_this_surface(self):
		run = self.a_run()
		self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
			"show_employer_contributions": True,
		})
		self.assertIn("Employer contributions", text_of(self.attached(run)))


@NEEDS_REPORTLAB
class StubYearToDate(StubToolTestCase):
	"""What the tool sums for the YTD block, and from which runs."""

	def two_runs(self):
		STORE.seed("Farm Payroll Entry", [
			entry("PAY-2026-0001", "2026-06-01", "2026-06-14", [
				slip("HR-EMP-00001", name="Maria Garcia", gross=1000.0,
				     salary_structure="SAL-0001"),
			]),
			entry("PAY-2026-0002", "2026-06-15", "2026-06-28", [
				slip("HR-EMP-00001", name="Maria Garcia", gross=1600.0,
				     salary_structure="SAL-0001"),
			]),
		])

	def test_it_sums_the_prior_runs_and_includes_this_one(self):
		"""'Year to date' on a stub means through today's cheque, not one period
		behind it."""
		self.two_runs()
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": "PAY-2026-0002", "employee": "HR-EMP-00001",
		})
		self.assertEqual(data["ytd"]["periods"], 2)
		self.assertEqual(data["ytd"]["gross_pay"], 2600.0)
		self.assertEqual(data["ytd"]["year"], "2026")
		self.assertTrue(data["ytd"]["includes_this_period"])

	def test_a_prior_calendar_year_is_not_summed_into_it(self):
		self.two_runs()
		STORE.seed("Farm Payroll Entry", [
			entry("PAY-2025-0026", "2025-12-15", "2025-12-28", [
				slip("HR-EMP-00001", name="Maria Garcia", gross=9000.0,
				     salary_structure="SAL-0001"),
			]),
		])
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": "PAY-2026-0002", "employee": "HR-EMP-00001",
		})
		self.assertEqual(data["ytd"]["gross_pay"], 2600.0)

	def test_a_later_run_is_not_summed_into_it(self):
		self.two_runs()
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": "PAY-2026-0001", "employee": "HR-EMP-00001",
		})
		self.assertEqual(data["ytd"]["periods"], 1)
		self.assertEqual(data["ytd"]["gross_pay"], 1000.0)

	def test_a_cancelled_run_is_not_summed_into_it(self):
		"""A Draft has not been paid and a Cancelled one was not — the rule
		`_load_ytd` applies for the same reason."""
		self.two_runs()
		STORE.seed("Farm Payroll Entry", [
			entry("PAY-2026-0003", "2026-06-15", "2026-06-28", [
				slip("HR-EMP-00001", name="Maria Garcia", gross=7000.0,
				     salary_structure="SAL-0001"),
			], status="Cancelled"),
		])
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": "PAY-2026-0002", "employee": "HR-EMP-00001",
		})
		self.assertEqual(data["ytd"]["gross_pay"], 2600.0)

	def test_a_draft_run_reports_that_it_is_not_in_its_own_year_to_date(self):
		"""Rendering a stub for a run that has not been paid is legitimate — it
		is what a review looks at — and the page must not claim the figure it is
		showing has been counted."""
		STORE.seed("Farm Payroll Entry", [
			entry("PAY-2026-0001", "2026-06-01", "2026-06-14", [
				slip("HR-EMP-00001", name="Maria Garcia", gross=1000.0,
				     salary_structure="SAL-0001"),
			]),
			entry("PAY-2026-0002", "2026-06-15", "2026-06-28", [
				slip("HR-EMP-00001", name="Maria Garcia", gross=1600.0,
				     salary_structure="SAL-0001"),
			], status="Draft"),
		])
		data = self.tool_data("render_pay_stub", {
			"payroll_entry": "PAY-2026-0002", "employee": "HR-EMP-00001",
		})
		self.assertFalse(data["ytd"]["includes_this_period"])
		self.assertEqual(data["ytd"]["gross_pay"], 1000.0)


# ── Claim 8: the refusals ─────────────────────────────────────────────────


class StubRefusals(StubToolTestCase):
	"""Every way of asking wrongly, and what each is told."""

	def test_the_switch_ships_off_and_refuses_with_the_field_to_tick(self):
		"""A mutating tool, and it writes a File naming somebody's wages."""
		run = self.a_run()
		self.configure(enabled=1, allow_render_pay_stub=0)
		message = self.tool_error("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
		})
		self.assertIn("allow_render_pay_stub", message)

	@NEEDS_REPORTLAB
	def test_an_unknown_run_is_refused_by_name(self):
		message = self.tool_error("render_pay_stub", {
			"payroll_entry": "PAY-NOPE", "employee": "HR-EMP-00001",
		})
		self.assertIn("PAY-NOPE", message)

	@NEEDS_REPORTLAB
	def test_a_person_not_on_the_run_is_told_who_is(self):
		"""'Not on this run' and 'spelled differently' are the two things it can
		be, and only one of them is the caller's mistake."""
		run = self.a_run()
		message = self.tool_error("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00099",
		})
		self.assertIn("HR-EMP-00001", message)
		self.assertIn("Ana Ruiz", message)
		self.assertIn("Nothing was rendered", message)

	@NEEDS_REPORTLAB
	def test_no_employee_at_all_is_refused_rather_than_rendering_somebody(self):
		run = self.a_run()
		message = self.tool_error("render_pay_stub", {"payroll_entry": run})
		self.assertIn("employee is required", message)

	@NEEDS_REPORTLAB
	def test_a_second_render_is_refused_and_names_what_is_already_there(self):
		run = self.a_run()
		first = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
		})
		message = self.tool_error("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
		})
		self.assertIn(first["file_url"], message)
		self.assertIn("overwrite=true", message)
		self.assertIn("Nothing was changed", message)

	@NEEDS_REPORTLAB
	def test_overwrite_renders_again_and_keeps_the_file_that_was_there(self):
		"""A stub somebody was handed is a statement that was made, and deleting
		it would not unmake it."""
		run = self.a_run()
		first = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001",
		})
		second = self.tool_data("render_pay_stub", {
			"payroll_entry": run, "employee": "HR-EMP-00001", "overwrite": True,
		})
		self.assertEqual(second["replaced"], first["file_url"])
		rows = frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": "Farm Payroll Entry", "attached_to_name": run},
			fields=["name"],
		)
		self.assertEqual(len(rows), 2)

	@NEEDS_REPORTLAB
	def test_a_stub_for_another_employee_on_the_same_run_is_not_a_second_render(self):
		"""The refusal is per stub, not per run — otherwise the second person on
		a crew could never be given one."""
		run = self.a_run()
		self.tool_data("render_pay_stub", {"payroll_entry": run, "employee": "HR-EMP-00001"})
		self.tool_data("render_pay_stub", {"payroll_entry": run, "employee": "HR-EMP-00002"})


if __name__ == "__main__":
	unittest.main()
