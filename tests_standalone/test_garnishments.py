# SPDX-License-Identifier: MIT
"""Garnishment compliance: the court order, its deduction, and the answer back.

SEVEN CLAIMS.

1.  `PriorityIsFederalLaw` — the rank is derived from the type on every save and
    cannot be typed in: child support 1, tax levy 2, student loan 3, creditor 4.
2.  `CreateFilesBothRecords` — one call files the order and creates the payroll
    deduction that honours it, maps the type onto the engine's category, links
    the two, and DOES NOT push the order's 1..4 into the engine's queue field.
3.  `BalanceAndSatisfaction` — the balance runs down, `add_withheld` accumulates,
    reaching zero satisfies the order and retires the deduction — and a zero
    `total_owed` is an absence rather than a paid-off debt, so a support order
    is never satisfied by arithmetic.
4.  `ListsAndReads` — filters, federal-priority ranking, and the competing
    orders that decide the shared 25% pool.
5.  `Refusals` — a duplicate case number, a re-key, a bad amount, a negative
    increment.
6.  `Warnings` — the six notes that are true and worth saying without refusing.
7.  `ResponseLetter` — the PDF renders, attaches privately, refuses a second
    render, and says the things a court is owed.

`Switches` closes each of the five tools with its own kill switch, and asserts
the OFF direction as well as the ON one.
"""

import json
import pathlib
import unittest

import frappe

from erpnext_mcp import garnishment_response_pdf
from erpnext_mcp.erpnext_mcp.doctype.farm_garnishment.farm_garnishment import (
	DEDUCTION_CATEGORY,
	DEDUCTION_STATUS,
	PRIORITY_BY_TYPE,
	STATUTORY_CEILING,
)

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import STORE
from .test_tax_form_pdfs import text_of

READS_ON = {
	"allow_list_garnishments": 1,
	"allow_get_garnishment": 1,
}

ON = {
	**READS_ON,
	"allow_create_garnishment": 1,
	"allow_update_garnishment": 1,
	"allow_render_garnishment_response": 1,
	# create_garnishment reaches `create_payroll_deduction`'s HANDLER rather than
	# dispatching it, so the deduction's own switch is deliberately NOT set here
	# — see `GarnishmentTestCase.test_one_switch_governs_both_halves`.
	"allow_get_payroll_deduction": 1,
	"allow_list_payroll_deductions": 1,
}

ANA = "HR-EMP-00001"
BEN = "HR-EMP-00002"

#: The settings JSON, found relative to this file so the test does not depend on
#: which directory `unittest discover` was started from.
SETTINGS_JSON = (
	pathlib.Path(__file__).resolve().parent.parent
	/ "erpnext_mcp"
	/ "erpnext_mcp"
	/ "doctype"
	/ "erpnext_mcp_settings"
	/ "erpnext_mcp_settings.json"
)

NEEDS_REPORTLAB = unittest.skipUnless(
	garnishment_response_pdf.available(),
	"reportlab is not installed on this bench — render_garnishment_response is supposed to go "
	"quietly unavailable, and the rest of the file covers the record without it",
)


class GarnishmentTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		STORE.seed(
			"Employee",
			[
				{
					"name": ANA,
					"employee_name": "Ana Lopez",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": BEN,
					"employee_name": "Ben Ortiz",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-03-01",
				},
			],
		)

	def a_garnishment(self, **overrides):
		args = {
			"employee": ANA,
			"company": MAIN,
			"garnishment_type": "Creditor",
			"case_number": "CV-2026-4481",
			"issuing_court_or_agency": "Yakima County Superior Court",
			"withholding_type": "Percentage of Disposable",
			"withholding_amount": 25,
			"received_date": "2026-03-02",
			"effective_date": "2026-03-16",
			"total_owed": 4000,
		}
		args.update(overrides)
		return self.tool_data("create_garnishment", args)

	def a_support_order(self, **overrides):
		args = {
			"employee": ANA,
			"company": MAIN,
			"garnishment_type": "Child Support",
			"case_number": "DR-2025-1190",
			"issuing_court_or_agency": "Washington State Support Registry",
			"withholding_type": "Fixed Amount",
			"withholding_amount": 320,
			"received_date": "2026-01-05",
			"effective_date": "2026-01-16",
		}
		args.update(overrides)
		return self.tool_data("create_garnishment", args)


# ── 1. Priority is what the law says, not what anybody typed ────────────────


class PriorityIsFederalLaw(GarnishmentTestCase):
	def test_each_type_gets_its_statutory_rank(self):
		expected = {"Child Support": 1, "Tax Levy": 2, "Student Loan": 3, "Creditor": 4}
		self.assertEqual(PRIORITY_BY_TYPE, expected)
		for index, (kind, rank) in enumerate(expected.items()):
			with self.subTest(garnishment_type=kind):
				data = self.a_garnishment(
					garnishment_type=kind,
					case_number=f"CASE-{index}",
					withholding_type="Fixed Amount",
					withholding_amount=100,
					total_owed=1000,
				)
				self.assertEqual(data["garnishment"]["priority"], rank)
				self.assertEqual(data["garnishment"]["federal_priority"], rank)

	def test_the_four_tables_cover_every_type_the_doctype_offers(self):
		"""A fifth Select option added without a line in each table is the whole
		bug this locks down: `PRIORITY_BY_TYPE.get(kind, 0)` would rank the new
		type FIRST, ahead of child support, and take its share of the pool."""
		schema = json.loads(
			(
				pathlib.Path(__file__).resolve().parent.parent
				/ "erpnext_mcp"
				/ "erpnext_mcp"
				/ "doctype"
				/ "farm_garnishment"
				/ "farm_garnishment.json"
			).read_text()
		)
		selects = {
			field["fieldname"]: set((field.get("options") or "").split("\n"))
			for field in schema["fields"]
			if field["fieldtype"] == "Select"
		}
		self.assertEqual(set(PRIORITY_BY_TYPE), selects["garnishment_type"])
		self.assertEqual(set(DEDUCTION_CATEGORY), selects["garnishment_type"])
		self.assertEqual(set(STATUTORY_CEILING), selects["garnishment_type"])
		self.assertEqual(set(DEDUCTION_STATUS), selects["status"])

	def test_an_unranked_type_sorts_behind_the_known_ones_not_ahead(self):
		"""The negative control for the table above, exercised through the
		controller so it is the real fallback and not a re-implementation."""
		doc = frappe.new_doc("Farm Garnishment")
		doc.garnishment_type = "Some Future Regime"
		doc._derive_priority()
		self.assertGreater(doc.priority, max(PRIORITY_BY_TYPE.values()))

	def test_a_supplied_priority_is_ignored_rather_than_honoured(self):
		"""It is law, not input. A caller that sent one is overruled silently
		because there is nothing to negotiate about."""
		data = self.a_garnishment(garnishment_type="Creditor", priority=1)
		self.assertEqual(data["garnishment"]["priority"], 4)

	def test_changing_the_type_re_derives_the_rank(self):
		created = self.a_garnishment(garnishment_type="Creditor")
		name = created["garnishment"]["name"]
		updated = self.tool_data(
			"update_garnishment",
			{"garnishment": name, "garnishment_type": "Tax Levy"},
		)
		self.assertEqual(updated["garnishment"]["priority"], 2)

	def test_child_support_outranks_a_creditor_judgment(self):
		"""The whole point of the field: 29 CFR 870.11(b)(1) gives the ordinary
		garnishment only what is left of the 25% pool after support."""
		self.a_garnishment()
		self.a_support_order()
		listed = self.tool_data("list_garnishments", {"company": MAIN, "employee": ANA})
		self.assertEqual(
			[row["garnishment_type"] for row in listed["garnishments"]],
			["Child Support", "Creditor"],
		)


# ── 2. One call, two records ────────────────────────────────────────────────


class CreateFilesBothRecords(GarnishmentTestCase):
	def test_the_order_creates_the_deduction_and_links_it(self):
		data = self.a_garnishment()
		deduction = data["payroll_deduction"]
		self.assertTrue(deduction)
		self.assertEqual(data["garnishment"]["payroll_deduction"], deduction)

		row = self.tool_data("get_payroll_deduction", {"deduction": deduction})["deduction"]
		self.assertEqual(row["employee"], ANA)
		self.assertEqual(row["deduction_type"], "Garnishment")
		self.assertEqual(row["reference"], "CV-2026-4481")
		self.assertEqual(row["status"], "Active")

	def test_the_type_maps_onto_the_engines_category(self):
		for kind, category in DEDUCTION_CATEGORY.items():
			with self.subTest(garnishment_type=kind):
				data = self.a_garnishment(
					garnishment_type=kind,
					case_number=f"MAP-{kind}",
					withholding_type="Fixed Amount",
					withholding_amount=75,
					total_owed=900,
				)
				row = self.tool_data(
					"get_payroll_deduction",
					{"deduction": data["payroll_deduction"]},
				)["deduction"]
				self.assertEqual(row["deduction_category"], category)

	def test_the_federal_rank_is_not_pushed_into_the_engines_queue(self):
		"""THE BUG THIS PREVENTS. The order ranks creditor 4 and the engine
		queues creditor at 40; pushing 4 across would sort it ahead of a support
		order at 10 and invert the precedence the rank exists to record."""
		creditor = self.a_garnishment(garnishment_type="Creditor")
		support = self.a_support_order()

		creditor_row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": creditor["payroll_deduction"]},
		)["deduction"]
		support_row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": support["payroll_deduction"]},
		)["deduction"]

		# Nothing was written to the deduction's own priority column. Compared as
		# an integer because an unset Int arrives as the string its JSON default
		# carries, and "did anybody write a rank here" is the question.
		self.assertEqual(int(creditor_row["priority"] or 0), 0)
		# ...so the engine's effective order is the category's, and support wins.
		self.assertLess(support_row["effective_priority"], creditor_row["effective_priority"])

	def test_a_percentage_order_is_measured_against_disposable_earnings(self):
		"""Not gross and not net. 29 CFR 870.10 is what every order is written
		against, and the engine spells that 'Net After Tax'."""
		data = self.a_garnishment(withholding_type="Percentage of Disposable", withholding_amount=25)
		row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": data["payroll_deduction"]},
		)["deduction"]
		self.assertEqual(row["amount_type"], "Percentage")
		self.assertEqual(row["basis"], "Net After Tax")

	def test_the_child_support_ceiling_facts_reach_the_deduction(self):
		"""The two facts in 1673(b)(2) are properties of the ORDER and are read
		by the engine off the deduction, so they have to make the crossing."""
		data = self.a_support_order(
			supports_other_dependents=False,
			arrears_over_12_weeks=True,
		)
		row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": data["payroll_deduction"]},
		)["deduction"]
		self.assertFalse(row["supports_other_dependents"])
		self.assertTrue(row["arrears_over_12_weeks"])

	def test_one_switch_governs_both_halves(self):
		"""`allow_create_payroll_deduction` is OFF in this fixture and the
		deduction is still created, because filing the order and instructing
		payroll to honour it are one act under one switch. An order that
		withheld nothing would be a garnishment the payroll run cannot see."""
		self.assertEqual(
			str(STORE.singles["ERPNext MCP Settings"].get("allow_create_payroll_deduction")),
			"0",
		)
		data = self.a_garnishment()
		self.assertTrue(data["payroll_deduction"])

	def test_a_second_worker_is_scoped_to_their_own_orders(self):
		self.a_garnishment(employee=ANA)
		self.a_garnishment(employee=BEN, case_number="CV-2026-9002")
		for employee, case in ((ANA, "CV-2026-4481"), (BEN, "CV-2026-9002")):
			with self.subTest(employee=employee):
				listed = self.tool_data("list_garnishments", {"employee": employee})
				self.assertEqual([r["case_number"] for r in listed["garnishments"]], [case])


# ── 3. The balance, and the one status change arithmetic may make ───────────


class BalanceAndSatisfaction(GarnishmentTestCase):
	def test_the_remaining_balance_is_computed_not_entered(self):
		created = self.a_garnishment(total_owed=4000)
		self.assertEqual(created["garnishment"]["remaining_balance"], 4000)

		name = created["garnishment"]["name"]
		data = self.tool_data("update_garnishment", {"garnishment": name, "add_withheld": 250})
		self.assertEqual(data["garnishment"]["total_withheld"], 250)
		self.assertEqual(data["garnishment"]["remaining_balance"], 3750)

	def test_add_withheld_accumulates_across_runs(self):
		name = self.a_garnishment(total_owed=1000)["garnishment"]["name"]
		for _ in range(3):
			self.tool_data("update_garnishment", {"garnishment": name, "add_withheld": 100})
		data = self.tool_data("get_garnishment", {"garnishment": name})["garnishment"]
		self.assertEqual(data["total_withheld"], 300)
		self.assertEqual(data["remaining_balance"], 700)

	def test_reaching_zero_satisfies_the_order_and_retires_the_deduction(self):
		created = self.a_garnishment(total_owed=500)
		name = created["garnishment"]["name"]
		deduction = created["payroll_deduction"]

		data = self.tool_data("update_garnishment", {"garnishment": name, "add_withheld": 500})
		self.assertEqual(data["garnishment"]["status"], "Satisfied")
		self.assertEqual(data["garnishment"]["remaining_balance"], 0)
		self.assertTrue(data["garnishment"]["satisfied_on"])
		self.assertIn("satisfied_note", data)

		# THE HALF WITH MONEY IN IT: the withholding actually stops.
		row = self.tool_data("get_payroll_deduction", {"deduction": deduction})["deduction"]
		self.assertEqual(row["status"], "Completed")

	def test_overpayment_floors_the_balance_rather_than_going_negative(self):
		name = self.a_garnishment(total_owed=500)["garnishment"]["name"]
		data = self.tool_data("update_garnishment", {"garnishment": name, "add_withheld": 640})
		self.assertEqual(data["garnishment"]["remaining_balance"], 0)
		self.assertEqual(data["garnishment"]["total_withheld"], 640)
		self.assertEqual(data["garnishment"]["status"], "Satisfied")

	def test_a_zero_total_owed_is_an_absence_and_never_satisfies(self):
		"""THE NEGATIVE CONTROL FOR THE CLAIM ABOVE. Every Currency field is 0
		on a row nobody filled in, and every child support order is one. Reading
		that as 'paid off' would mark a support order Satisfied on the day it was
		filed and stop the withholding — the exact failure this module exists to
		prevent."""
		created = self.a_support_order()
		name = created["garnishment"]["name"]
		self.assertFalse(created["garnishment"]["has_balance"])

		data = self.tool_data("update_garnishment", {"garnishment": name, "add_withheld": 320})
		self.assertEqual(data["garnishment"]["status"], "Active")
		self.assertEqual(data["garnishment"]["total_withheld"], 320)

		row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": created["payroll_deduction"]},
		)["deduction"]
		self.assertEqual(row["status"], "Active")

	def test_terminating_an_order_also_stops_the_deduction(self):
		created = self.a_support_order()
		name = created["garnishment"]["name"]
		self.tool_data("update_garnishment", {"garnishment": name, "status": "Terminated"})
		row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": created["payroll_deduction"]},
		)["deduction"]
		self.assertEqual(row["status"], "Completed")

	def test_restoring_an_order_brings_the_withholding_back(self):
		"""A Terminated entered by mistake and corrected has to resume, or the
		order is live on the file and dead in payroll."""
		created = self.a_support_order()
		name = created["garnishment"]["name"]
		self.tool_data("update_garnishment", {"garnishment": name, "status": "Terminated"})
		self.tool_data("update_garnishment", {"garnishment": name, "status": "Active"})
		row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": created["payroll_deduction"]},
		)["deduction"]
		self.assertEqual(row["status"], "Active")

	def test_a_court_ordered_stay_is_not_undone_by_the_orders_own_status(self):
		"""Suspended is a fact about the deduction the order's status does not
		carry, so an Active order does not lift a stay a court imposed."""
		created = self.a_support_order()
		name = created["garnishment"]["name"]
		deduction = created["payroll_deduction"]
		frappe.db.set_value("Farm Payroll Deduction", deduction, "status", "Suspended")

		self.tool_data("update_garnishment", {"garnishment": name, "notes": "clerk touched it"})
		row = self.tool_data("get_payroll_deduction", {"deduction": deduction})["deduction"]
		self.assertEqual(row["status"], "Suspended")

	def test_changing_the_amount_carries_onto_the_deduction(self):
		"""An order that says $200 and a run that takes $150 is not a
		discrepancy anybody notices — both figures look deliberate."""
		created = self.a_support_order()
		name = created["garnishment"]["name"]
		data = self.tool_data(
			"update_garnishment",
			{"garnishment": name, "withholding_amount": 410},
		)
		self.assertTrue(data["deduction_changes"])
		row = self.tool_data(
			"get_payroll_deduction",
			{"deduction": created["payroll_deduction"]},
		)["deduction"]
		self.assertEqual(row["amount"], 410)


# ── 4. What the reads answer ────────────────────────────────────────────────


class ListsAndReads(GarnishmentTestCase):
	def test_filters(self):
		self.a_garnishment()
		self.a_support_order()
		self.a_garnishment(employee=BEN, case_number="CV-2026-9002")

		self.assertEqual(self.tool_data("list_garnishments", {"company": MAIN})["count"], 3)
		self.assertEqual(
			self.tool_data("list_garnishments", {"garnishment_type": "Child Support"})["count"],
			1,
		)
		self.assertEqual(self.tool_data("list_garnishments", {"employee": BEN})["count"], 1)
		self.assertEqual(
			self.tool_data("list_garnishments", {"case_number": "CV-2026"})["count"],
			2,
		)
		self.assertEqual(self.tool_data("list_garnishments", {"status": "Satisfied"})["count"], 0)

	def test_the_company_filter_scopes_the_file(self):
		self.a_garnishment()
		self.assertEqual(self.tool_data("list_garnishments", {"company": MAIN})["count"], 1)
		self.assertEqual(self.tool_data("list_garnishments", {"company": OTHER})["count"], 0)

	def test_competing_orders_are_named_because_they_share_one_pool(self):
		self.a_garnishment()
		self.a_support_order()
		listed = self.tool_data("list_garnishments", {"company": MAIN})
		self.assertEqual(listed["employees_with_competing_orders"], [ANA])

		got = self.tool_data("get_garnishment", {"garnishment": "CV-2026-4481"})
		self.assertEqual(
			[row["garnishment_type"] for row in got["competing_orders"]],
			["Child Support"],
		)

	def test_a_case_number_resolves_and_an_ambiguous_one_is_refused(self):
		self.a_garnishment(employee=ANA)
		self.a_garnishment(employee=BEN)
		message = self.tool_error("get_garnishment", {"garnishment": "CV-2026-4481"})
		self.assertIn("2 orders", message)
		self.assertIn("Pass the docname", message)

	def test_the_unremitted_balance_counts_only_orders_that_have_one(self):
		self.a_garnishment(total_owed=4000)
		self.a_support_order()
		listed = self.tool_data("list_garnishments", {"company": MAIN})
		self.assertEqual(listed["unremitted_balance"], 4000)

	def test_the_derived_facts_are_on_every_row(self):
		data = self.tool_data(
			"get_garnishment",
			{"garnishment": self.a_garnishment()["garnishment"]["name"]},
		)["garnishment"]
		self.assertEqual(data["deduction_category"], "Wage Garnishment")
		self.assertEqual(data["statutory_ceiling_percentage"], 25.0)
		self.assertTrue(data["has_balance"])

	def test_a_tax_levy_reports_no_ceiling_because_the_ccpa_does_not_reach_it(self):
		created = self.a_garnishment(
			garnishment_type="Tax Levy",
			case_number="LEVY-668",
			withholding_type="Fixed Amount",
			withholding_amount=300,
		)
		got = self.tool_data(
			"get_garnishment",
			{"garnishment": created["garnishment"]["name"]},
		)["garnishment"]
		self.assertIsNone(got["statutory_ceiling_percentage"])


# ── 5. What is refused ──────────────────────────────────────────────────────


class Refusals(GarnishmentTestCase):
	def test_the_same_case_number_against_the_same_worker_is_refused(self):
		self.a_garnishment()
		message = self.tool_error(
			"create_garnishment",
			{
				"employee": ANA,
				"company": MAIN,
				"garnishment_type": "Creditor",
				"case_number": "CV-2026-4481",
				"withholding_amount": 25,
			},
		)
		self.assertIn("already has an active garnishment", message)
		self.assertIn("withhold it twice", message)
		self.assertEqual(self.tool_data("list_garnishments", {})["count"], 1)

	def test_the_same_case_number_against_a_different_worker_is_allowed(self):
		"""A multi-defendant judgment is one case number and two orders, and
		refusing the second would be refusing a real one."""
		self.a_garnishment(employee=ANA)
		self.a_garnishment(employee=BEN)
		self.assertEqual(self.tool_data("list_garnishments", {})["count"], 2)

	def test_an_order_cannot_be_moved_to_another_worker(self):
		name = self.a_garnishment()["garnishment"]["name"]
		message = self.tool_error(
			"update_garnishment",
			{"garnishment": name, "employee": BEN},
		)
		self.assertIn("cannot be changed", message)
		self.assertEqual(
			self.tool_data("get_garnishment", {"garnishment": name})["garnishment"]["employee"],
			ANA,
		)

	def test_a_withholding_of_nothing_is_refused(self):
		message = self.tool_error(
			"create_garnishment",
			{
				"employee": ANA,
				"company": MAIN,
				"garnishment_type": "Creditor",
				"case_number": "ZERO-1",
				"withholding_amount": 0,
			},
		)
		self.assertIn("greater than zero", message)

	def test_a_percentage_over_one_hundred_is_refused(self):
		message = self.tool_error(
			"create_garnishment",
			{
				"employee": ANA,
				"company": MAIN,
				"garnishment_type": "Creditor",
				"case_number": "PCT-1",
				"withholding_type": "Percentage of Disposable",
				"withholding_amount": 140,
			},
		)
		self.assertIn("cannot exceed 100%", message)

	def test_a_ceiling_over_one_hundred_is_refused(self):
		message = self.tool_error(
			"create_garnishment",
			{
				"employee": ANA,
				"company": MAIN,
				"garnishment_type": "Creditor",
				"case_number": "CEIL-1",
				"withholding_amount": 50,
				"max_disposable_earnings_percentage": 120,
			},
		)
		self.assertIn("cannot exceed 100", message)

	def test_a_negative_increment_is_refused(self):
		name = self.a_garnishment()["garnishment"]["name"]
		message = self.tool_error(
			"update_garnishment",
			{"garnishment": name, "add_withheld": -50},
		)
		self.assertIn("cannot be negative", message)

	def test_an_update_that_changes_nothing_says_so(self):
		name = self.a_garnishment()["garnishment"]["name"]
		message = self.tool_error("update_garnishment", {"garnishment": name})
		self.assertIn("nothing to change", message)

	def test_an_ambiguous_employee_name_is_refused(self):
		STORE.seed(
			"Employee",
			[
				{
					"name": "HR-EMP-00003",
					"employee_name": "Ana Lopez Reyes",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-06-01",
				}
			],
		)
		message = self.tool_error(
			"create_garnishment",
			{
				"employee": "Ana Lopez",
				"company": MAIN,
				"garnishment_type": "Creditor",
				"case_number": "AMB-1",
				"withholding_amount": 50,
			},
		)
		self.assertIn("matches 2 employees", message)


# ── 6. What is warned about rather than refused ─────────────────────────────


class Warnings(GarnishmentTestCase):
	def notes_of(self, data) -> str:
		return " ".join(data.get("notes") or [])

	def test_a_ceiling_above_the_statutory_maximum_is_flagged(self):
		"""An order may set a ceiling LOWER than the statute and cannot raise
		one, so a higher figure is somebody misreading the paper — but it saves,
		because refusing would block a real order over a transcription."""
		data = self.a_garnishment(max_disposable_earnings_percentage=60)
		self.assertIn("above the 25% federal maximum", self.notes_of(data))

	def test_a_percentage_ceiling_on_a_tax_levy_is_flagged(self):
		data = self.a_garnishment(
			garnishment_type="Tax Levy",
			case_number="LEVY-1",
			withholding_type="Fixed Amount",
			withholding_amount=200,
			max_disposable_earnings_percentage=25,
		)
		self.assertIn("outside the CCPA entirely", self.notes_of(data))

	def test_a_creditor_order_with_no_balance_is_flagged(self):
		data = self.a_garnishment(total_owed=0)
		self.assertIn("never become", self.notes_of(data))

	def test_a_support_order_with_a_balance_is_flagged_the_other_way(self):
		data = self.a_support_order(total_owed=2500)
		self.assertIn("STOP the withholding", self.notes_of(data))

	def test_withholding_dated_before_service_is_flagged(self):
		data = self.a_garnishment(received_date="2026-03-02", effective_date="2026-02-01")
		self.assertIn("before the order was served", self.notes_of(data))

	def test_a_missing_issuer_is_flagged_because_the_letter_needs_an_addressee(self):
		data = self.a_garnishment(issuing_court_or_agency="")
		self.assertIn("nobody to address", self.notes_of(data))

	def test_the_shared_pool_is_explained_when_a_second_order_arrives(self):
		self.a_support_order()
		data = self.a_garnishment()
		self.assertIn("shared_pool_note", data)
		self.assertIn("870.11(b)(1)", data["shared_pool_note"])


# ── 7. The answer back to the court ─────────────────────────────────────────


@NEEDS_REPORTLAB
class ResponseLetter(GarnishmentTestCase):
	def render(self, name, **overrides):
		args = {"garnishment": name, "company_address": "1 Orchard Rd\nZillah WA"}
		args.update(overrides)
		return self.tool_data("render_garnishment_response", args)

	def attached(self, data) -> bytes:
		return STORE.file_contents[data["file"]]

	def text(self, data) -> str:
		"""Every string drawn on the page, not the raw stream.

		reportlab splits a line across Tj operators, so a phrase that is plainly
		on the page is frequently not a contiguous run of bytes in the file.
		`text_of` is what the tax form tests read a page with.
		"""
		return text_of(self.attached(data))

	def test_the_letter_renders_and_attaches_privately(self):
		name = self.a_garnishment()["garnishment"]["name"]
		data = self.render(name)
		self.assertTrue(data["file_url"])
		self.assertTrue(data["attachment"]["is_private"])
		self.assertEqual(data["attachment"]["sha256"], data["attachment"]["sha256"])
		self.assertTrue(data["file_name"].startswith("garnishment-response-"))
		self.assertTrue(self.attached(data).startswith(b"%PDF"))

	def test_the_result_name_is_the_order_not_the_file(self):
		"""`describe_attachment` carries its own `name`, and spreading it whole
		would silently rename the record the result is about."""
		name = self.a_garnishment()["garnishment"]["name"]
		data = self.render(name)
		self.assertEqual(data["name"], name)
		self.assertNotEqual(data["attachment"]["name"], name)

	def test_the_page_says_what_a_court_is_owed(self):
		name = self.a_garnishment()["garnishment"]["name"]
		body = self.text(self.render(name))
		for phrase in (
			"Employer Response to Withholding Order",
			"CV-2026-4481",
			"Yakima County Superior Court",
			"Ana Lopez",
			"2026-03-16",
		):
			with self.subTest(phrase=phrase):
				self.assertIn(phrase, body)

	def test_the_letter_names_the_person_not_the_docname(self):
		"""A court matches this letter to its own defendant by the NAME on the
		order. `employee_name` carries `fetch_from`, which is a Desk convenience
		that does not run on every insert path, so `create_garnishment` writes it
		— this is the assertion that catches it going back to a fetch."""
		name = self.a_garnishment()["garnishment"]["name"]
		body = self.text(self.render(name))
		self.assertIn("Ana Lopez", body)
		self.assertNotIn("HR-EMP-00001", body)

	def test_a_worker_with_no_name_on_file_is_marked_rather_than_passed_off(self):
		"""The negative control. Where there really is no name the docname is
		printed AND labelled, because a bare docname in a name column reads as a
		name and would be posted to a court as one."""
		STORE.seed(
			"Employee",
			[
				{
					"name": "HR-EMP-00009",
					"employee_name": "",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": "2025-02-01",
				}
			],
		)
		created = self.a_garnishment(employee="HR-EMP-00009", case_number="NONAME-1")
		body = self.text(self.render(created["garnishment"]["name"]))
		self.assertIn("name not recorded", body)

	def test_it_does_not_pretend_to_be_the_courts_own_form(self):
		name = self.a_garnishment()["garnishment"]["name"]
		body = self.text(self.render(name))
		self.assertIn("does not replace any answer", body)
		self.assertNotIn("NOT AN OFFICIAL FORM", body)

	def test_the_ceiling_sentence_matches_the_type(self):
		creditor = self.a_garnishment()["garnishment"]["name"]
		self.assertIn("1673(a)", self.text(self.render(creditor)))

		support = self.a_support_order()["garnishment"]["name"]
		self.assertIn("1673(b)(2)", self.text(self.render(support)))

	def test_the_competing_orders_and_the_shared_pool_rule_are_printed(self):
		self.a_support_order()
		name = self.a_garnishment()["garnishment"]["name"]
		body = self.text(self.render(name))
		self.assertIn("DR-2025-1190", body)
		self.assertIn("870.11(b)(1)", body)

	def test_no_other_orders_is_stated_rather_than_omitted(self):
		"""A section that vanished would leave a reader unable to tell a clean
		answer from an omission, and a court asks for the statement."""
		name = self.a_garnishment()["garnishment"]["name"]
		self.assertIn("no other active withholding order", self.text(self.render(name)))

	def test_a_second_render_refuses_unless_overwrite_is_passed(self):
		name = self.a_garnishment()["garnishment"]["name"]
		self.render(name)
		message = self.tool_error("render_garnishment_response", {"garnishment": name})
		self.assertIn("already has a response letter", message)

		again = self.render(name, overwrite=True)
		self.assertTrue(again["replaced"])

	def test_a_missing_addressee_is_reported_rather_than_silently_blank(self):
		name = self.a_garnishment(issuing_court_or_agency="")["garnishment"]["name"]
		data = self.render(name)
		self.assertIn("addressee_note", data)


# ── the kill switches ───────────────────────────────────────────────────────


class Switches(GarnishmentTestCase):
	TOOLS = (
		("list_garnishments", {}),
		("get_garnishment", {"garnishment": "GARN-0001"}),
		(
			"create_garnishment",
			{
				"employee": ANA,
				"company": MAIN,
				"garnishment_type": "Creditor",
				"case_number": "SW-1",
				"withholding_amount": 50,
			},
		),
		("update_garnishment", {"garnishment": "GARN-0001", "notes": "x"}),
		("render_garnishment_response", {"garnishment": "GARN-0001"}),
	)

	def test_each_tool_is_refused_when_its_own_switch_is_off(self):
		for tool, args in self.TOOLS:
			with self.subTest(tool=tool):
				self.configure(enabled=1, **{**ON, f"allow_{tool}": 0})
				message = self.tool_error(tool, args)
				self.assertIn(tool, message)

	def test_the_writes_ship_off_and_the_reads_ship_on(self):
		"""The invariant this app holds across every tool: a read is a surface
		control an operator may narrow, a write is a decision they must take."""
		defaults = json.loads(SETTINGS_JSON.read_text())
		by_name = {field["fieldname"]: field for field in defaults["fields"]}
		for tool in ("list_garnishments", "get_garnishment"):
			self.assertEqual(by_name[f"allow_{tool}"]["default"], "1", tool)
		for tool in ("create_garnishment", "update_garnishment", "render_garnishment_response"):
			self.assertEqual(by_name[f"allow_{tool}"]["default"], "0", tool)

	def test_every_call_leaves_an_audit_row(self):
		self.a_garnishment()
		self.assertAudited("create_garnishment")

	def test_every_field_the_writers_read_is_advertised(self):
		"""A schema that omits an argument its handler honours is a field no
		caller can discover: `additionalProperties` is advertised and never
		enforced, so the omission is silent in both directions."""
		from erpnext_mcp import registry
		from erpnext_mcp.tools import garnishments

		create = registry.TOOLS["create_garnishment"]["inputSchema"]["properties"]
		update = registry.TOOLS["update_garnishment"]["inputSchema"]["properties"]
		for field in garnishments._WRITABLE:
			with self.subTest(field=field):
				self.assertIn(field, create)
				self.assertIn(field, update)
		self.assertIn("add_withheld", update)

	def test_an_order_already_part_collected_does_not_restart_its_balance(self):
		"""The reason `total_withheld` is writable on create. An order served
		months ago and only now being filed would otherwise make the worker pay
		the debt twice."""
		data = self.a_garnishment(total_owed=4000, total_withheld=1500)
		self.assertEqual(data["garnishment"]["remaining_balance"], 2500)
