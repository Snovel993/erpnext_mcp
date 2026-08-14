# SPDX-License-Identifier: MIT
"""Sales, settlements and receivables — the money end of the packer pipeline.

v0.70.0, Sprint 5. SEVENTEEN CLAIMS.

 1. `InvoiceFromSettlement` — every priced line becomes a line, against a shared Item per variety and grade.
 2. `TheRateAdjustment` — a stated gross amount survives; the RATE moves and says so.
 3. `DeductionsAreChargeRows` — negative Actual rows, an account that is refused rather than guessed, and the gross-only escape.
 4. `SettlementInvoiceRefusals` — draft, cancelled, twice, both-paths, wrong company, no prices.
 5. `StandaloneInvoice` — hand-written lines, and every way one can be wrong.
 6. `GettingAnInvoiceBack` — items, charges, payments, ageing, the settlement behind it.
 7. `ListingInvoices` — every filter narrows, and the totals say what they summed.
 8. `SubmittingAnInvoice` — docstatus moves, GL rows are READ BACK, twice is refused.
 9. `ReceivingPayment` — oldest-first, explicit, over-allocation, leftovers, and always a draft.
10. `Shrink` — the arithmetic, and the unexplained remainder kept apart from the cull.
11. `ShrinkByVarietyAndGrade` — packed from the lines, delivered from the tickets, and a regrade reported rather than dropped.
12. `Packout` — five groupings, and the two where a number is null rather than invented.
13. `ARAgeing` — buckets by customer, the GL cross-check, and drift.
14. `SeasonSummary` — four rollups and three gaps.
15. `SettlementToJournalEntry` — a balanced draft, the stamp, and the double-posting refusal in both directions.
16. `LateTickets` — the four checks again, and the variance that moves.
17. `SwitchesAndSchema` — twelve switches, and the two link fields.

WHAT THE MOST IMPORTANT TESTS HERE ARE ABOUT. Two things in this module could
silently produce a number nobody could argue with:

`test_a_stated_gross_amount_survives_the_invoice` is the first. ERPNext
recomputes a line's amount from qty × rate on every validate, and a settlement
line is allowed to disagree with its own multiplication. If the tool let the
amount be recomputed, the invoice would total to something the packer never
said, and the only evidence would be a rounding-sized difference nobody looks at.

`test_packout_by_field_does_not_allocate_a_pooled_settlement` is the second. A
pro-rata packout by field is a made-up number that looks exactly like a measured
one, and it would end up feeding a per-acre KPI. The claim under test is that
the tool returns the pooled weight under `unattributed` and declines to split it.
"""

import json
import pathlib

import frappe

from erpnext_mcp import compat
from erpnext_mcp.tools import sales

from .fixtures import (
	MAIN,
	MAIN_ABBR,
	MASTER_CUSTOMER,
	OTHER,
	MastersTestCase,
	cash,
	supplies,
)
from .fixtures import sales as income_account
from .harness import STORE, post_sales_invoice_gl

READ_TOOLS = (
	"get_sales_invoice",
	"list_sales_invoices",
	"get_settlement_shrink",
	"get_packout_summary",
	"get_ar_aging",
	"get_season_summary",
)
WRITE_TOOLS = (
	"create_sales_invoice",
	"create_sales_invoice_from_settlement",
	"submit_sales_invoice",
	"receive_payment",
	"post_settlement_to_gl",
	"reconcile_settlement_to_tickets",
)
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS

#: The receipt tools this module drives to build its own fixtures, plus the
#: payment submit that belongs to the purchasing module but works on any entry.
SUPPORTING_TOOLS = (
	"create_scale_ticket",
	"submit_scale_ticket",
	"create_settlement_statement",
	"submit_settlement_statement",
	"submit_payment_entry",
	"submit_journal_entry",
	"get_settlement_statement",
	"list_scale_tickets",
)

TOOLS_ON = {f"allow_{name}": 1 for name in ALL_TOOLS + SUPPORTING_TOOLS}

RECEIVABLE = f"1200 - Accounts Receivable - {MAIN_ABBR}"

OTHER_PACKER = "Blue Ridge Packing"

FIELD_ONE = "Home Block"
FIELD_TWO = "River Block"

#: One load off a truck. Honeycrisp XF, 12,200 lb net.
TICKET = {
	"ticket_number": "44718",
	"date": "2026-09-14",
	"customer": MASTER_CUSTOMER,
	"company": MAIN,
	"variety": "Honeycrisp",
	"grade": "XF",
	"gross_weight": 18400,
	"tare_weight": 6200,
	"weight_uom": "Lb",
	"field": FIELD_ONE,
}

#: The packer's statement over one period.
#:
#:   gross revenue  12400 + 4200 = 16600
#:   deductions      6240 + 1120 =  7360
#:   net proceeds                =  9240
#:
#: The SECOND line is the interesting one: 11200 × 0.40 is 4480, and the packer
#: stated 4200. That gap is what `TheRateAdjustment` is about.
SETTLEMENT = {
	"statement_number": "SS-2026-0912",
	"date": "2026-12-01",
	"customer": MASTER_CUSTOMER,
	"company": MAIN,
	"period_start": "2026-09-01",
	"period_end": "2026-11-30",
	"gross_delivered_weight": 48000,
	"packed_weight": 31200,
	"cull_weight": 9600,
	"weight_uom": "Lb",
	"line_items": [
		{
			"variety": "Honeycrisp",
			"grade": "XF",
			"packed_weight": 20000,
			"price_per_unit": 0.62,
			"price_uom": "Lb",
		},
		{
			"variety": "Honeycrisp",
			"grade": "Fancy",
			"packed_weight": 11200,
			"price_per_unit": 0.40,
			"price_uom": "Lb",
			"gross_amount": 4200.00,
		},
	],
	"deductions": [
		{"deduction_type": "Packing", "description": "Pack charge", "amount": 6240.00},
		{"deduction_type": "Cold Storage", "description": "Sep-Nov", "amount": 1120.00},
	],
}

GROSS_REVENUE = 16600.0
DEDUCTIONS = 7360.0
NET_PROCEEDS = 9240.0


class SalesTestCase(MastersTestCase):
	"""The masters site, plus a Receivable account, a second packer and two fields.

	The Receivable account is seeded HERE rather than added to the shared chart
	in `fixtures._CHART`: half a dozen other modules count the accounts on this
	site, and a fixture that grew an eleventh row under them would fail
	assertions about a tree this module does not change.

	It is seeded for MAIN only, deliberately. That is what makes
	`test_a_company_with_no_receivable_account_is_empty_not_an_error` a claim
	with something to prove rather than a branch nothing reaches.
	"""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **TOOLS_ON)
		STORE.seed(
			"Account",
			[
				{
					"name": RECEIVABLE,
					"account_name": "Accounts Receivable",
					"account_number": "1200",
					"parent_account": f"1000 - Current Assets - {MAIN_ABBR}",
					"is_group": 0,
					"root_type": "Asset",
					"account_type": "Receivable",
					"account_currency": "USD",
					"disabled": 0,
					"company": MAIN,
					"lft": 400,
					"rgt": 401,
				}
			],
		)
		STORE.seed(
			"Customer",
			[
				{
					"name": OTHER_PACKER,
					"customer_name": OTHER_PACKER,
					"customer_group": "Packers",
					"territory": "Washington",
					"customer_type": "Company",
				}
			],
		)
		STORE.seed(
			"Field",
			[
				{"name": FIELD_ONE, "field_name": FIELD_ONE, "company": MAIN},
				{"name": FIELD_TWO, "field_name": FIELD_TWO, "company": MAIN},
			],
		)

	# -- fixture builders, driven through the tools themselves ---------------
	def capture(self, **overrides):
		return self.tool_data("create_scale_ticket", {**TICKET, **overrides})

	def submitted_ticket(self, **overrides):
		name = self.capture(**overrides)["name"]
		self.tool_data("submit_scale_ticket", {"name": name})
		return name

	def settle(self, **overrides):
		return self.tool_data("create_settlement_statement", {**SETTLEMENT, **overrides})

	def submitted_settlement(self, **overrides):
		name = self.settle(**overrides)["name"]
		self.tool_data("submit_settlement_statement", {"name": name})
		return name

	def invoiced_settlement(self, **overrides):
		"""A submitted settlement with a draft invoice against it."""
		settlement = self.submitted_settlement()
		data = self.tool_data(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": settlement, "deduction_account": supplies(), **overrides},
		)
		return settlement, data

	def submitted_invoice(self, **overrides):
		_, data = self.invoiced_settlement(**overrides)
		self.tool_data("submit_sales_invoice", {"sales_invoice": data["name"]})
		return data["name"]

	def standalone_invoice(self, submit=False, **overrides):
		args = {
			"customer": MASTER_CUSTOMER,
			"company": MAIN,
			"posting_date": "2026-10-01",
			"due_date": "2026-10-31",
			"items": [{"item_code": "SURROUND-WP", "qty": 100, "rate": 5.0}],
		}
		args.update(overrides)
		data = self.tool_data("create_sales_invoice", args)
		if submit:
			self.tool_data("submit_sales_invoice", {"sales_invoice": data["name"]})
		return data["name"]


# ── Claim 1: every priced line becomes a line ─────────────────────────────


class InvoiceFromSettlement(SalesTestCase):
	def test_one_invoice_line_per_priced_settlement_line(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(len(data["items"]), 2)
		self.assertEqual(data["items"][0]["qty"], 20000.0)
		self.assertEqual(data["items"][1]["qty"], 11200.0)

	def test_it_is_a_draft_and_says_so_both_ways(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["docstatus"], 0)
		self.assertEqual(data["docstatus_label"], "draft")
		self.assertIn("DRAFT", data["next_step"])

	def test_each_line_lands_on_a_shared_item_per_variety_and_grade(self):
		_, data = self.invoiced_settlement()
		codes = [row["item_code"] for row in data["items"]]
		self.assertEqual(codes, ["FRUIT-HONEYCRISP-XF", "FRUIT-HONEYCRISP-FANCY"])

	def test_the_shared_item_is_created_once_and_reused_by_the_next_settlement(self):
		"""Never one Item per statement — the whole point of a SHARED item."""
		self.invoiced_settlement()
		first = self.tool_data("get_sales_invoice", {"sales_invoice": "SI-00001"})
		second_settlement = self.submitted_settlement(statement_number="SS-2026-0913")
		data = self.tool_data(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": second_settlement, "deduction_account": supplies()},
		)
		self.assertEqual(
			[row["item_code"] for row in data["items"]],
			[row["item_code"] for row in first["items"]],
		)
		self.assertEqual(
			[row["item_resolved_by"] for row in data["lines_from_settlement"]],
			["existing shared item", "existing shared item"],
		)

	def test_the_created_item_is_not_a_stock_item(self):
		"""A settlement has no inventory behind it — an Item that claimed to
		would put fruit nobody counted into a warehouse balance."""
		self.invoiced_settlement()
		self.assertEqual(frappe.db.get_value("Item", "FRUIT-HONEYCRISP-XF", "is_stock_item"), 0)

	def test_a_line_with_no_variety_lands_on_the_generic_item(self):
		settlement = self.submitted_settlement(
			line_items=[{"packed_weight": 500, "price_per_unit": 1.0, "price_uom": "Lb"}],
			deductions=[],
		)
		data = self.tool_data(
			"create_sales_invoice_from_settlement", {"settlement_statement": settlement}
		)
		self.assertEqual(data["items"][0]["item_code"], "FRUIT-SALES")

	def test_the_price_uom_is_carried_rather_than_the_weight_uom_assumed(self):
		settlement = self.submitted_settlement(
			weight_uom="Lb",
			line_items=[
				{
					"variety": "Gala",
					"grade": "XF",
					"packed_weight": 400,
					"price_per_unit": 22.0,
					"price_uom": "Box",
				}
			],
			deductions=[],
		)
		data = self.tool_data(
			"create_sales_invoice_from_settlement", {"settlement_statement": settlement}
		)
		self.assertEqual(data["items"][0]["uom"], "Box")

	def test_the_dates_default_to_the_settlement_and_thirty_days_after(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["posting_date"], "2026-12-01")
		self.assertEqual(data["due_date"], "2026-12-31")

	def test_both_link_fields_are_set_and_reported(self):
		settlement, data = self.invoiced_settlement()
		self.assertTrue(data["links"]["settlement_points_at_invoice"])
		self.assertTrue(data["links"]["invoice_points_at_settlement"])
		self.assertEqual(
			frappe.db.get_value("Settlement Statement", settlement, "sales_invoice"), data["name"]
		)
		self.assertEqual(
			frappe.db.get_value("Sales Invoice", data["name"], "settlement_statement"), settlement
		)

	def test_the_receivable_and_income_accounts_come_from_the_chart(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["debit_to"], RECEIVABLE)
		self.assertEqual(data["items"][0]["income_account"], income_account())

	def test_the_remark_names_the_statement_and_its_period(self):
		_, data = self.invoiced_settlement()
		self.assertIn("SS-2026-0912", data["remarks"])
		self.assertIn("2026-09-01", data["remarks"])

	def test_the_convenience_tool_and_the_general_one_produce_the_same_invoice(self):
		"""Thin on purpose: one implementation of what a settlement line becomes."""
		settlement = self.submitted_settlement()
		direct = self.tool_data(
			"create_sales_invoice",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		other = self.submitted_settlement(statement_number="SS-2026-0913")
		wrapped = self.tool_data(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": other, "deduction_account": supplies()},
		)
		self.assertEqual(direct["grand_total"], wrapped["grand_total"])
		self.assertEqual(
			[row["item_code"] for row in direct["items"]],
			[row["item_code"] for row in wrapped["items"]],
		)


# ── Claim 2: a stated gross amount survives; the rate moves ───────────────


class TheRateAdjustment(SalesTestCase):
	def test_a_stated_gross_amount_survives_the_invoice(self):
		"""ERPNext recomputes amount = qty × rate on every validate. The packer
		stated 4200 for 11200 lb at 0.40, which multiplies to 4480. If the tool
		let the amount be recomputed the invoice would total to something the
		packer never said."""
		_, data = self.invoiced_settlement()
		self.assertEqual(data["items"][1]["amount"], 4200.0)

	def test_the_rate_moved_and_not_the_amount(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["items"][1]["rate"], 0.375)

	def test_the_stated_price_is_kept_beside_the_rate_that_was_used(self):
		_, data = self.invoiced_settlement()
		line = data["lines_from_settlement"][1]
		self.assertEqual(line["stated_price_per_unit"], 0.40)
		self.assertEqual(line["rate"], 0.375)
		self.assertTrue(line["rate_differs_from_statement"])
		self.assertIn("RATE was adjusted", line["rate_note"])

	def test_a_line_that_multiplies_out_is_not_flagged(self):
		_, data = self.invoiced_settlement()
		line = data["lines_from_settlement"][0]
		self.assertFalse(line["rate_differs_from_statement"])
		self.assertNotIn("rate_note", line)

	def test_the_invoice_totals_to_the_settlements_net_proceeds(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["grand_total"], NET_PROCEEDS)
		self.assertEqual(data["total_check"]["variance"], 0.0)
		self.assertIn("exactly", data["total_check"]["note"])

	def test_the_net_total_before_charges_is_the_gross_revenue(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["net_total"], GROSS_REVENUE)


# ── Claim 3: deductions are negative charge rows ──────────────────────────


class DeductionsAreChargeRows(SalesTestCase):
	def test_each_deduction_is_its_own_row_rather_than_one_netted_figure(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(len(data["taxes"]), 2)
		self.assertEqual(data["deductions_posted"], 2)

	def test_a_deduction_is_negative_and_of_charge_type_actual(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["taxes"][0]["charge_type"], "Actual")
		self.assertEqual(data["taxes"][0]["tax_amount"], -6240.0)
		self.assertEqual(data["taxes"][1]["tax_amount"], -1120.0)

	def test_the_row_carries_the_type_and_the_packers_own_description(self):
		_, data = self.invoiced_settlement()
		self.assertEqual(data["taxes"][0]["description"], "Packing: Pack charge")

	def test_revenue_is_gross_and_the_receivable_is_the_net(self):
		"""The whole reason deductions are charge rows and not a netted line."""
		_, data = self.invoiced_settlement()
		self.assertEqual(data["net_total"], GROSS_REVENUE)
		self.assertEqual(data["total_taxes_and_charges"], -DEDUCTIONS)
		self.assertEqual(data["grand_total"], NET_PROCEEDS)

	def test_the_deduction_account_is_refused_rather_than_guessed(self):
		settlement = self.submitted_settlement()
		message = self.tool_error(
			"create_sales_invoice_from_settlement", {"settlement_statement": settlement}
		)
		self.assertIn("deduction_account", message)
		self.assertIn("Nothing was created", message)

	def test_a_settlement_with_no_deductions_needs_no_account(self):
		settlement = self.submitted_settlement(deductions=[])
		data = self.tool_data(
			"create_sales_invoice_from_settlement", {"settlement_statement": settlement}
		)
		self.assertEqual(data["taxes"], [])
		self.assertEqual(data["grand_total"], GROSS_REVENUE)

	def test_include_deductions_false_invoices_the_gross_and_warns(self):
		settlement = self.submitted_settlement()
		data = self.tool_data(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": settlement, "include_deductions": False},
		)
		self.assertEqual(data["grand_total"], GROSS_REVENUE)
		self.assertEqual(data["taxes"], [])
		self.assertIn("NOT on this invoice", data["warning"])
		self.assertIn("total_gross_revenue", data["total_check"]["basis"])


# ── Claim 4: every way a settlement cannot be invoiced ────────────────────


class SettlementInvoiceRefusals(SalesTestCase):
	def test_a_draft_settlement_is_refused(self):
		draft = self.settle()["name"]
		message = self.tool_error(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": draft, "deduction_account": supplies()},
		)
		self.assertIn("DRAFT", message)
		self.assertIn("Nothing was created", message)

	def test_a_cancelled_settlement_is_refused(self):
		name = self.submitted_settlement()
		frappe.get_doc("Settlement Statement", name).cancel()
		message = self.tool_error(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": name, "deduction_account": supplies()},
		)
		self.assertIn("cancelled", message)

	def test_invoicing_one_settlement_twice_is_refused_by_name(self):
		settlement, data = self.invoiced_settlement()
		message = self.tool_error(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		self.assertIn("already invoiced", message)
		self.assertIn(data["name"], message)

	def test_a_settlement_already_posted_to_gl_is_refused(self):
		"""The two paths are alternatives, not a sequence."""
		settlement = self.submitted_settlement()
		self.tool_data(
			"post_settlement_to_gl",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		message = self.tool_error(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		self.assertIn("already posted", message)
		self.assertIn("book the proceeds twice", message)

	def test_passing_both_a_settlement_and_items_is_refused_rather_than_merged(self):
		settlement = self.submitted_settlement()
		message = self.tool_error(
			"create_sales_invoice",
			{
				"settlement_statement": settlement,
				"items": [{"item_code": "SURROUND-WP", "qty": 1, "rate": 1}],
			},
		)
		self.assertIn("not both", message)

	def test_a_settlement_from_another_company_is_refused(self):
		settlement = self.submitted_settlement()
		message = self.tool_error(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": settlement, "company": OTHER, "deduction_account": supplies()},
		)
		self.assertIn(OTHER, message)

	def test_a_settlement_with_no_priced_lines_is_refused(self):
		settlement = self.submitted_settlement(line_items=[], deductions=[])
		message = self.tool_error(
			"create_sales_invoice_from_settlement", {"settlement_statement": settlement}
		)
		self.assertIn("packout report, not a bill", message)

	def test_a_line_with_no_packed_weight_is_refused_rather_than_priced_at_zero(self):
		settlement = self.submitted_settlement(
			line_items=[
				{"variety": "Gala", "grade": "XF", "packed_weight": 0, "gross_amount": 500.0}
			],
			deductions=[],
		)
		message = self.tool_error(
			"create_sales_invoice_from_settlement", {"settlement_statement": settlement}
		)
		self.assertIn("packed weight", message)

	def test_an_unknown_settlement_is_refused_by_name(self):
		message = self.tool_error(
			"create_sales_invoice_from_settlement", {"settlement_statement": "NOPE"}
		)
		self.assertIn("no Settlement Statement called", message)

	def test_a_due_date_before_the_posting_date_is_refused(self):
		settlement = self.submitted_settlement(deductions=[])
		message = self.tool_error(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": settlement, "due_date": "2026-11-01"},
		)
		self.assertIn("before posting_date", message)


# ── Claim 5: hand-written lines ───────────────────────────────────────────


class StandaloneInvoice(SalesTestCase):
	def test_a_draft_with_computed_totals(self):
		name = self.standalone_invoice()
		data = self.tool_data("get_sales_invoice", {"sales_invoice": name})
		self.assertEqual(data["grand_total"], 500.0)
		self.assertEqual(data["docstatus"], 0)

	def test_it_carries_no_settlement(self):
		name = self.standalone_invoice()
		data = self.tool_data("get_sales_invoice", {"sales_invoice": name})
		self.assertIsNone(data["linked_settlement"])

	def test_items_are_required(self):
		message = self.tool_error(
			"create_sales_invoice", {"customer": MASTER_CUSTOMER, "company": MAIN}
		)
		self.assertIn("items must be a non-empty list", message)

	def test_a_zero_quantity_is_refused(self):
		message = self.tool_error(
			"create_sales_invoice",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"items": [{"item_code": "SURROUND-WP", "qty": 0, "rate": 5}],
			},
		)
		self.assertIn("qty must be positive", message)

	def test_an_unknown_item_is_refused_by_name(self):
		message = self.tool_error(
			"create_sales_invoice",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"items": [{"item_code": "NOPE", "qty": 1, "rate": 5}],
			},
		)
		self.assertIn("no Item called", message)

	def test_an_unknown_customer_is_refused_by_name(self):
		message = self.tool_error(
			"create_sales_invoice",
			{
				"customer": "Nobody Packing",
				"company": MAIN,
				"items": [{"item_code": "SURROUND-WP", "qty": 1, "rate": 5}],
			},
		)
		self.assertIn("no Customer called", message)

	def test_a_customer_can_be_named_by_customer_name_rather_than_docname(self):
		STORE.seed(
			"Customer",
			[{"name": "CUST-0099", "customer_name": "Cascade Fruit Co", "customer_group": "Packers"}],
		)
		name = self.standalone_invoice(customer="Cascade Fruit Co")
		self.assertEqual(
			self.tool_data("get_sales_invoice", {"sales_invoice": name})["customer"], "CUST-0099"
		)

	def test_a_manual_charge_row_is_carried(self):
		name = self.standalone_invoice(
			taxes=[
				{"charge_type": "Actual", "account_head": supplies(), "tax_amount": -50, "description": "Fee"}
			]
		)
		data = self.tool_data("get_sales_invoice", {"sales_invoice": name})
		self.assertEqual(data["grand_total"], 450.0)

	def test_an_actual_charge_with_no_amount_is_refused(self):
		message = self.tool_error(
			"create_sales_invoice",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"items": [{"item_code": "SURROUND-WP", "qty": 1, "rate": 5}],
				"taxes": [{"charge_type": "Actual", "account_head": supplies()}],
			},
		)
		self.assertIn("no tax_amount", message)

	def test_an_unknown_charge_type_is_refused_with_the_list(self):
		message = self.tool_error(
			"create_sales_invoice",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"items": [{"item_code": "SURROUND-WP", "qty": 1, "rate": 5}],
				"taxes": [{"charge_type": "Magic", "account_head": supplies(), "tax_amount": 1}],
			},
		)
		self.assertIn("On Net Total", message)

	def test_a_company_with_no_receivable_account_is_refused_with_what_to_pass(self):
		message = self.tool_error(
			"create_sales_invoice",
			{
				"customer": MASTER_CUSTOMER,
				"company": OTHER,
				"items": [{"item_code": "SURROUND-WP", "qty": 1, "rate": 5}],
			},
		)
		self.assertIn("debit_to", message)
		self.assertIn("Nothing was created", message)


# ── Claim 6: reading an invoice back ──────────────────────────────────────


class GettingAnInvoiceBack(SalesTestCase):
	def test_items_and_charges_come_back(self):
		_, created = self.invoiced_settlement()
		data = self.tool_data("get_sales_invoice", {"sales_invoice": created["name"]})
		self.assertEqual(len(data["items"]), 2)
		self.assertEqual(len(data["taxes"]), 2)

	def test_the_linked_settlement_comes_back_in_full(self):
		settlement, created = self.invoiced_settlement()
		data = self.tool_data("get_sales_invoice", {"sales_invoice": created["name"]})
		self.assertEqual(data["linked_settlement"]["name"], settlement)
		self.assertEqual(data["linked_settlement"]["net_proceeds"], NET_PROCEEDS)
		self.assertEqual(data["linked_settlement"]["packout_pct"], 65.0)

	def test_a_draft_invoice_is_not_aged_at_all(self):
		"""Nothing is owed until it is submitted."""
		_, created = self.invoiced_settlement()
		data = self.tool_data("get_sales_invoice", {"sales_invoice": created["name"]})
		self.assertIsNone(data["ageing"]["ageing_bucket"])

	def test_a_submitted_invoice_is_bucketed_against_the_as_of_date(self):
		name = self.submitted_invoice()
		data = self.tool_data("get_sales_invoice", {"sales_invoice": name, "as_of": "2026-12-31"})
		self.assertEqual(data["ageing"]["days_overdue"], 0)
		self.assertEqual(data["ageing"]["ageing_bucket"], "current")
		later = self.tool_data("get_sales_invoice", {"sales_invoice": name, "as_of": "2027-02-14"})
		self.assertEqual(later["ageing"]["ageing_bucket"], "31-60")

	def test_the_payments_allocated_against_it_come_back_with_amounts(self):
		name = self.submitted_invoice()
		payment = self.tool_data(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 4000,
				"paid_to": cash(),
				"posting_date": "2027-01-05",
			},
		)
		data = self.tool_data("get_sales_invoice", {"sales_invoice": name})
		self.assertEqual(len(data["payments"]), 1)
		self.assertEqual(data["payments"][0]["payment_entry"], payment["payment_entry"])
		self.assertEqual(data["payments"][0]["allocated_amount"], 4000.0)
		self.assertEqual(data["total_paid"], 4000.0)

	def test_every_docname_alias_reaches_the_same_invoice(self):
		name = self.standalone_invoice()
		for key in ("sales_invoice", "invoice", "name"):
			with self.subTest(alias=key):
				self.assertEqual(self.tool_data("get_sales_invoice", {key: name})["name"], name)

	def test_an_unknown_invoice_is_refused_by_name(self):
		self.assertIn(
			"no Sales Invoice called", self.tool_error("get_sales_invoice", {"sales_invoice": "NOPE"})
		)


# ── Claim 7: listing ──────────────────────────────────────────────────────


class ListingInvoices(SalesTestCase):
	def test_it_lists_what_was_created(self):
		self.standalone_invoice()
		self.standalone_invoice()
		self.assertEqual(self.tool_data("list_sales_invoices", {})["count"], 2)

	def test_the_customer_filter_narrows(self):
		self.standalone_invoice()
		self.standalone_invoice(customer=OTHER_PACKER)
		data = self.tool_data("list_sales_invoices", {"customer": OTHER_PACKER})
		self.assertEqual(data["count"], 1)

	def test_a_draft_inflates_the_billed_total_and_not_the_outstanding_one(self):
		self.standalone_invoice()
		data = self.tool_data("list_sales_invoices", {})
		self.assertEqual(data["total_grand"], 500.0)
		self.assertEqual(data["total_outstanding"], 0.0)

	def test_outstanding_only_excludes_the_draft(self):
		self.standalone_invoice()
		self.standalone_invoice(submit=True)
		data = self.tool_data("list_sales_invoices", {"outstanding_only": True})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["total_outstanding"], 500.0)

	def test_the_date_range_narrows_and_a_reversed_one_is_refused(self):
		self.standalone_invoice(posting_date="2026-10-01", due_date="2026-11-01")
		self.standalone_invoice(posting_date="2026-06-01", due_date="2026-07-01")
		data = self.tool_data("list_sales_invoices", {"from_date": "2026-09-01"})
		self.assertEqual(data["count"], 1)
		self.assertIn(
			"is after", self.tool_error("list_sales_invoices", {"from_date": "2026-12-01", "to_date": "2026-01-01"})
		)

	def test_the_settlement_filter_finds_the_invoice_that_billed_it(self):
		settlement, created = self.invoiced_settlement()
		self.standalone_invoice()
		data = self.tool_data("list_sales_invoices", {"settlement_statement": settlement})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["invoices"][0]["name"], created["name"])

	def test_filtering_on_a_settlement_before_the_field_exists_is_refused_with_why(self):
		"""The Custom Field is created on first use, so a site that has never
		invoiced a settlement has nothing to filter on — and says so rather than
		returning an empty list that reads as 'no such invoices'."""
		message = self.tool_error("list_sales_invoices", {"settlement_statement": "SS-ETC-0001"})
		self.assertIn("no settlement_statement field yet", message)

	def test_by_status_counts_the_drafts_as_drafts(self):
		self.standalone_invoice()
		self.standalone_invoice(submit=True)
		data = self.tool_data("list_sales_invoices", {})
		self.assertEqual(data["by_status"], {"draft": 1, "Unpaid": 1})


# ── Claim 8: submitting ───────────────────────────────────────────────────


class SubmittingAnInvoice(SalesTestCase):
	def test_submit_moves_the_docstatus_and_opens_the_receivable(self):
		_, created = self.invoiced_settlement()
		data = self.tool_data("submit_sales_invoice", {"sales_invoice": created["name"]})
		self.assertEqual(data["docstatus"], 1)
		self.assertEqual(data["status"], "Unpaid")
		self.assertEqual(data["outstanding_amount"], NET_PROCEEDS)

	def test_the_gl_rows_are_read_back_rather_than_computed(self):
		"""The tool reports what the ledger says. Nothing here posts."""
		_, created = self.invoiced_settlement()
		first = self.tool_data("submit_sales_invoice", {"sales_invoice": created["name"]})
		self.assertEqual(first["gl_entries"], [])
		self.assertEqual(first["gl_entries_created"], 0)

		post_sales_invoice_gl(created["name"])
		read = self.tool_data("get_sales_invoice", {"sales_invoice": created["name"]})
		self.assertEqual(read["grand_total"], NET_PROCEEDS)

	def test_the_posted_ledger_debits_the_receivable_and_credits_income(self):
		_, created = self.invoiced_settlement()
		self.tool_data("submit_sales_invoice", {"sales_invoice": created["name"]})
		rows = post_sales_invoice_gl(created["name"])
		debits = {row["account"]: row["debit"] for row in rows if row["debit"]}
		credits = {row["account"]: row["credit"] for row in rows if row["credit"]}
		self.assertEqual(credits[income_account()], GROSS_REVENUE)
		self.assertEqual(debits[supplies()], DEDUCTIONS)
		self.assertEqual(debits[RECEIVABLE], NET_PROCEEDS)

	def test_submitting_twice_is_refused(self):
		_, created = self.invoiced_settlement()
		self.tool_data("submit_sales_invoice", {"sales_invoice": created["name"]})
		self.assertIn(
			"already submitted",
			self.tool_error("submit_sales_invoice", {"sales_invoice": created["name"]}),
		)

	def test_submitting_a_cancelled_invoice_is_refused(self):
		name = self.standalone_invoice(submit=True)
		frappe.get_doc("Sales Invoice", name).cancel()
		self.assertIn("cancelled", self.tool_error("submit_sales_invoice", {"sales_invoice": name}))

	def test_submitting_an_unknown_invoice_is_refused_by_name(self):
		self.assertIn(
			"no Sales Invoice called", self.tool_error("submit_sales_invoice", {"sales_invoice": "NOPE"})
		)

	def test_the_settlement_is_still_reachable_from_the_submitted_invoice(self):
		settlement, created = self.invoiced_settlement()
		data = self.tool_data("submit_sales_invoice", {"sales_invoice": created["name"]})
		self.assertEqual(data["settlement_statement"], settlement)


# ── Claim 9: receiving payment ────────────────────────────────────────────


class ReceivingPayment(SalesTestCase):
	def two_open_invoices(self):
		"""Two submitted invoices, the older one due first."""
		old = self.standalone_invoice(
			posting_date="2026-06-01", due_date="2026-07-01", submit=True
		)
		new = self.standalone_invoice(
			posting_date="2026-10-01", due_date="2026-11-01", submit=True
		)
		return old, new

	def test_an_unallocated_payment_walks_the_invoices_oldest_first(self):
		old, new = self.two_open_invoices()
		data = self.tool_data(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 600, "paid_to": cash()},
		)
		self.assertEqual(data["allocation_method"], "oldest first")
		allocated = [row["sales_invoice"] for row in data["allocated_invoices"]]
		self.assertEqual(allocated, [old, new])
		self.assertEqual(data["allocated_invoices"][0]["allocated_amount"], 500.0)
		self.assertEqual(data["allocated_invoices"][1]["allocated_amount"], 100.0)

	def test_money_over_everything_outstanding_is_left_on_account(self):
		self.two_open_invoices()
		data = self.tool_data(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 1500, "paid_to": cash()},
		)
		self.assertEqual(data["allocated_total"], 1000.0)
		self.assertEqual(data["unallocated_amount"], 500.0)

	def test_an_explicit_allocation_names_the_invoice(self):
		_old, new = self.two_open_invoices()
		data = self.tool_data(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 300,
				"paid_to": cash(),
				"invoices": [{"sales_invoice": new, "allocated_amount": 300}],
			},
		)
		self.assertEqual(data["allocation_method"], "explicit")
		self.assertEqual(data["allocated_invoices"][0]["sales_invoice"], new)

	def test_an_explicit_allocation_with_no_amount_takes_the_whole_outstanding(self):
		_, new = self.two_open_invoices()
		data = self.tool_data(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 500,
				"paid_to": cash(),
				"invoices": [{"sales_invoice": new}],
			},
		)
		self.assertEqual(data["allocated_total"], 500.0)

	def test_allocating_more_than_an_invoice_owes_is_refused(self):
		_, new = self.two_open_invoices()
		message = self.tool_error(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 900,
				"paid_to": cash(),
				"invoices": [{"sales_invoice": new, "allocated_amount": 900}],
			},
		)
		self.assertIn("exceeds", message)

	def test_allocating_more_than_was_paid_is_refused(self):
		old, new = self.two_open_invoices()
		message = self.tool_error(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 100,
				"paid_to": cash(),
				"invoices": [
					{"sales_invoice": old, "allocated_amount": 500},
					{"sales_invoice": new, "allocated_amount": 500},
				],
			},
		)
		self.assertIn("paid_amount", message)

	def test_another_customers_invoice_is_refused(self):
		_, new = self.two_open_invoices()
		message = self.tool_error(
			"receive_payment",
			{
				"customer": OTHER_PACKER,
				"company": MAIN,
				"paid_amount": 100,
				"paid_to": cash(),
				"invoices": [{"sales_invoice": new, "allocated_amount": 100}],
			},
		)
		self.assertIn("billed to", message)

	def test_a_draft_invoice_cannot_be_paid(self):
		draft = self.standalone_invoice()
		message = self.tool_error(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 100,
				"paid_to": cash(),
				"invoices": [{"sales_invoice": draft, "allocated_amount": 100}],
			},
		)
		self.assertIn("not submitted", message)

	def test_the_same_invoice_twice_in_one_payment_is_refused(self):
		_, new = self.two_open_invoices()
		message = self.tool_error(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 400,
				"paid_to": cash(),
				"invoices": [
					{"sales_invoice": new, "allocated_amount": 200},
					{"sales_invoice": new, "allocated_amount": 200},
				],
			},
		)
		self.assertIn("appears twice", message)

	def test_a_zero_payment_is_refused(self):
		message = self.tool_error(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 0, "paid_to": cash()},
		)
		self.assertIn("must be positive", message)

	def test_it_is_a_draft_and_has_moved_nothing(self):
		old, _ = self.two_open_invoices()
		self.tool_data(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 500, "paid_to": cash()},
		)
		self.assertEqual(
			self.tool_data("get_sales_invoice", {"sales_invoice": old})["outstanding_amount"], 500.0
		)

	def test_submitting_it_clears_the_invoice(self):
		old, _ = self.two_open_invoices()
		payment = self.tool_data(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 500, "paid_to": cash()},
		)
		self.tool_data("submit_payment_entry", {"name": payment["payment_entry"]})
		data = self.tool_data("get_sales_invoice", {"sales_invoice": old})
		self.assertEqual(data["outstanding_amount"], 0.0)
		self.assertEqual(data["status"], "Paid")

	def test_it_is_receive_and_customer_and_never_pay(self):
		self.two_open_invoices()
		payment = self.tool_data(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 100, "paid_to": cash()},
		)
		row = frappe.db.get_value(
			"Payment Entry", payment["payment_entry"], ["payment_type", "party_type"], as_dict=True
		)
		self.assertEqual(row["payment_type"], "Receive")
		self.assertEqual(row["party_type"], "Customer")

	def test_the_receivable_is_credited_and_the_bank_debited(self):
		self.two_open_invoices()
		payment = self.tool_data(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 100, "paid_to": cash()},
		)
		self.assertEqual(payment["paid_from"], RECEIVABLE)
		self.assertEqual(payment["paid_to"], cash())

	def test_a_company_with_no_bank_default_is_refused_with_what_to_pass(self):
		self.two_open_invoices()
		message = self.tool_error(
			"receive_payment", {"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 100}
		)
		self.assertIn("paid_to", message)

	def test_a_cheque_number_is_carried_and_dated(self):
		self.two_open_invoices()
		payment = self.tool_data(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": 100,
				"paid_to": cash(),
				"reference_no": "CHK-8841",
				"mode_of_payment": "Cheque",
			},
		)
		self.assertEqual(payment["reference_no"], "CHK-8841")
		self.assertEqual(payment["mode_of_payment"], "Cheque")


# ── Claim 10: shrink ──────────────────────────────────────────────────────


class Shrink(SalesTestCase):
	def shrink(self, **overrides):
		name = self.submitted_settlement(**overrides)
		return self.tool_data("get_settlement_shrink", {"settlement_statement": name})

	def test_shrink_is_delivered_minus_packed(self):
		data = self.shrink()
		self.assertEqual(data["shrink_weight"], 16800.0)

	def test_packout_and_shrink_are_complements(self):
		data = self.shrink()
		self.assertEqual(data["packout_pct"], 65.0)
		self.assertEqual(data["shrink_pct"], 35.0)
		self.assertEqual(round(data["packout_pct"] + data["shrink_pct"], 2), 100.0)

	def test_the_unexplained_remainder_is_kept_apart_from_the_cull(self):
		"""A cull percentage is what a grower renegotiates a contract over; an
		unexplained percentage is what a grower asks a question about."""
		data = self.shrink()
		self.assertEqual(data["cull_weight"], 9600.0)
		self.assertEqual(data["unexplained_weight"], 7200.0)
		self.assertEqual(data["cull_pct"], 20.0)
		self.assertEqual(data["unexplained_pct"], 15.0)

	def test_the_ticket_reconciliation_comes_with_it(self):
		ticket = self.submitted_ticket()
		name = self.submitted_settlement(scale_tickets=[ticket])
		data = self.tool_data("get_settlement_shrink", {"settlement_statement": name})
		self.assertEqual(data["ticket_reconciliation"]["matched_ticket_net_weight"], 12200.0)
		self.assertEqual(data["ticket_reconciliation"]["variance"], 35800.0)

	def test_no_delivered_weight_makes_every_percentage_zero_and_says_so(self):
		data = self.shrink(gross_delivered_weight=0, packed_weight=0, cull_weight=0)
		self.assertEqual(data["packout_pct"], 0.0)
		self.assertIn("by convention rather than by measurement", data["warning"])

	def test_every_docname_alias_reaches_the_same_settlement(self):
		name = self.submitted_settlement()
		for key in ("settlement_statement", "settlement", "statement", "name"):
			with self.subTest(alias=key):
				self.assertEqual(
					self.tool_data("get_settlement_shrink", {key: name})["settlement_statement"], name
				)


# ── Claim 11: shrink per variety and grade ────────────────────────────────


class ShrinkByVarietyAndGrade(SalesTestCase):
	def test_packed_comes_from_the_lines_and_delivered_from_the_tickets(self):
		ticket = self.submitted_ticket()
		name = self.submitted_settlement(scale_tickets=[ticket])
		data = self.tool_data("get_settlement_shrink", {"settlement_statement": name})
		groups = {(row["variety"], row["grade"]): row for row in data["by_variety_grade"]}
		xf = groups[("Honeycrisp", "XF")]
		self.assertEqual(xf["packed_weight"], 20000.0)
		self.assertEqual(xf["delivered_weight"], 12200.0)
		self.assertEqual(xf["ticket_count"], 1)

	def test_a_grade_priced_but_never_delivered_under_that_name_is_reported(self):
		"""A packer regrading a load produces exactly this, and dropping the row
		would hide the one thing worth noticing about it."""
		ticket = self.submitted_ticket()
		name = self.submitted_settlement(scale_tickets=[ticket])
		data = self.tool_data("get_settlement_shrink", {"settlement_statement": name})
		groups = {(row["variety"], row["grade"]): row for row in data["by_variety_grade"]}
		fancy = groups[("Honeycrisp", "Fancy")]
		self.assertFalse(fancy["comparable"])
		self.assertIsNone(fancy["packout_pct"])
		self.assertIn("settlement lines", fancy["note"])

	def test_a_ticket_in_another_unit_is_excluded_rather_than_converted(self):
		bins = self.submitted_ticket(
			ticket_number="44719", weight_uom="Bin", gross_weight=40, tare_weight=0
		)
		name = self.submitted_settlement(scale_tickets=[bins])
		data = self.tool_data("get_settlement_shrink", {"settlement_statement": name})
		excluded = [
			row for row in data["by_variety_grade"] if row.get("tickets_in_other_units_excluded")
		]
		self.assertEqual(excluded[0]["tickets_in_other_units_excluded"], 1)


# ── Claim 12: packout across settlements ──────────────────────────────────


class Packout(SalesTestCase):
	def test_the_overall_figures_come_from_the_headers_and_are_exact(self):
		self.submitted_settlement()
		data = self.tool_data("get_packout_summary", {"company": MAIN})
		self.assertEqual(data["summary"]["total_delivered"], 48000.0)
		self.assertEqual(data["summary"]["total_packed"], 31200.0)
		self.assertEqual(data["summary"]["overall_packout_pct"], 65.0)

	def test_a_draft_settlement_is_not_counted(self):
		"""A draft statement is a document somebody is still typing."""
		self.settle()
		data = self.tool_data("get_packout_summary", {"company": MAIN})
		self.assertEqual(data["summary"]["settlement_count"], 0)

	def test_grouping_by_customer_is_exact_on_all_three_weights(self):
		self.submitted_settlement()
		data = self.tool_data("get_packout_summary", {"company": MAIN, "group_by": "customer"})
		group = data["groups"][0]
		self.assertEqual(group["group_key"], MASTER_CUSTOMER)
		self.assertEqual(group["delivered"], 48000.0)
		self.assertEqual(group["packed"], 31200.0)
		self.assertEqual(group["culled"], 9600.0)
		self.assertIsNone(data["unattributed"])

	def test_grouping_by_month_keys_on_the_statement_date(self):
		self.submitted_settlement()
		data = self.tool_data("get_packout_summary", {"company": MAIN, "group_by": "month"})
		self.assertEqual(data["groups"][0]["group_key"], "2026-12")

	def test_grouping_by_grade_takes_packed_from_the_priced_lines(self):
		self.submitted_settlement()
		data = self.tool_data("get_packout_summary", {"company": MAIN, "group_by": "grade"})
		groups = {row["group_key"]: row for row in data["groups"]}
		self.assertEqual(groups["XF"]["packed"], 20000.0)
		self.assertEqual(groups["Fancy"]["packed"], 11200.0)

	def test_the_cull_is_null_per_variety_and_reported_whole_instead(self):
		"""A packer states ONE cull weight per statement. Splitting it across
		varieties would be inventing the split."""
		self.submitted_settlement()
		data = self.tool_data("get_packout_summary", {"company": MAIN, "group_by": "variety"})
		self.assertIsNone(data["groups"][0]["culled"])
		self.assertEqual(data["unattributed"]["culled_weight"], 9600.0)

	def test_grouping_by_variety_takes_delivered_from_the_tickets(self):
		ticket = self.submitted_ticket()
		self.submitted_settlement(scale_tickets=[ticket])
		data = self.tool_data("get_packout_summary", {"company": MAIN, "group_by": "variety"})
		self.assertEqual(data["groups"][0]["delivered"], 12200.0)

	def test_packout_by_field_attributes_a_settlement_owned_by_one_field(self):
		ticket = self.submitted_ticket(field=FIELD_ONE)
		self.submitted_settlement(scale_tickets=[ticket])
		data = self.tool_data("get_packout_summary", {"company": MAIN, "group_by": "field"})
		groups = {row["group_key"]: row for row in data["groups"]}
		self.assertEqual(groups[FIELD_ONE]["delivered"], 12200.0)
		self.assertEqual(groups[FIELD_ONE]["packed"], 31200.0)
		self.assertEqual(data["unattributed"]["packed_weight"], 0.0)

	def test_packout_by_field_does_not_allocate_a_pooled_settlement(self):
		"""THE MOST IMPORTANT ASSERTION IN THIS CLASS. A pro-rata packout by
		field is a made-up number that looks exactly like a measured one, and it
		would end up feeding a per-acre KPI."""
		home = self.submitted_ticket(ticket_number="1", field=FIELD_ONE)
		river = self.submitted_ticket(ticket_number="2", field=FIELD_TWO)
		self.submitted_settlement(scale_tickets=[home, river])
		data = self.tool_data("get_packout_summary", {"company": MAIN, "group_by": "field"})
		groups = {row["group_key"]: row for row in data["groups"]}
		self.assertEqual(groups[FIELD_ONE]["delivered"], 12200.0)
		self.assertIsNone(groups[FIELD_ONE]["packout_pct"])
		self.assertEqual(data["unattributed"]["packed_weight"], 31200.0)
		self.assertEqual(data["unattributed"]["settlement_count"], 1)
		self.assertIn("pro-rata", data["unattributed"]["note"])

	def test_an_unknown_group_by_is_refused_with_the_list(self):
		message = self.tool_error("get_packout_summary", {"company": MAIN, "group_by": "colour"})
		self.assertIn("variety", message)
		self.assertIn("month", message)

	def test_mixed_weight_units_make_every_total_meaningless_and_it_says_so(self):
		self.submitted_settlement()
		self.submitted_settlement(statement_number="SS-2026-0913", weight_uom="Bin")
		data = self.tool_data("get_packout_summary", {"company": MAIN})
		self.assertEqual(len(data["by_weight_uom"]), 2)
		self.assertIn("not comparable", data["warning"])

	def test_the_customer_filter_narrows(self):
		self.submitted_settlement()
		self.submitted_settlement(statement_number="SS-2026-0913", customer=OTHER_PACKER)
		data = self.tool_data(
			"get_packout_summary", {"company": MAIN, "customer": OTHER_PACKER, "group_by": "customer"}
		)
		self.assertEqual(data["summary"]["settlement_count"], 1)

	def test_the_basis_sentence_is_always_present_and_names_the_grouping(self):
		self.submitted_settlement()
		for group_by in sales.PACKOUT_GROUP_BY:
			with self.subTest(group_by=group_by):
				data = self.tool_data(
					"get_packout_summary", {"company": MAIN, "group_by": group_by}
				)
				self.assertTrue(data["basis"])


# ── Claim 13: AR ageing ───────────────────────────────────────────────────


class ARAgeing(SalesTestCase):
	def aged(self, **overrides):
		args = {"company": MAIN, "as_of": "2027-01-15"}
		args.update(overrides)
		return self.tool_data("get_ar_aging", args)

	def three_invoices(self):
		"""Due 2027-02-01 (current), 2027-01-01 (0-30), 2026-11-01 (61-90)."""
		for posting, due in (
			("2026-12-01", "2027-02-01"),
			("2026-12-01", "2027-01-01"),
			("2026-10-01", "2026-11-01"),
		):
			name = self.standalone_invoice(posting_date=posting, due_date=due, submit=True)
			post_sales_invoice_gl(name)

	def test_invoices_are_bucketed_by_days_overdue(self):
		self.three_invoices()
		buckets = self.aged()["customers"][0]["buckets"]
		self.assertEqual(buckets["current"]["count"], 1)
		self.assertEqual(buckets["0-30"]["count"], 1)
		self.assertEqual(buckets["61-90"]["count"], 1)

	def test_it_groups_by_customer_rather_than_listing_invoices_flat(self):
		self.three_invoices()
		data = self.aged()
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["customers"][0]["customer"], MASTER_CUSTOMER)
		self.assertEqual(data["customers"][0]["total_outstanding"], 1500.0)
		self.assertEqual(data["invoice_count"], 3)

	def test_a_draft_invoice_is_not_aged(self):
		self.three_invoices()
		self.standalone_invoice()
		self.assertEqual(self.aged()["invoice_count"], 3)

	def test_a_paid_invoice_drops_off_entirely(self):
		self.three_invoices()
		payment = self.tool_data(
			"receive_payment",
			{"customer": MASTER_CUSTOMER, "company": MAIN, "paid_amount": 500, "paid_to": cash()},
		)
		self.tool_data("submit_payment_entry", {"name": payment["payment_entry"]})
		self.assertEqual(self.aged()["invoice_count"], 2)

	def test_the_gl_balance_matches_the_open_invoices_when_nothing_odd_happened(self):
		self.three_invoices()
		row = self.aged()["customers"][0]
		self.assertEqual(row["gl_balance"], 1500.0)
		self.assertNotIn("drift", row)

	def test_a_settlement_posted_straight_to_gl_shows_up_as_drift(self):
		"""The named consequence of choosing the journal-entry path: the
		receivable exists as a balance and not as a document anybody can age."""
		self.three_invoices()
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "GL-SETTLEMENT-1",
					"account": RECEIVABLE,
					"posting_date": "2026-12-01",
					"debit": 9240.0,
					"credit": 0.0,
					"company": MAIN,
					"is_cancelled": 0,
					"voucher_type": "Journal Entry",
					"voucher_no": "JE-SETTLEMENT-1",
					"voucher_detail_no": "",
					"party_type": "Customer",
					"party": MASTER_CUSTOMER,
					"cost_center": None,
					"is_opening": "No",
					"docstatus": 1,
				}
			],
		)
		row = self.aged()["customers"][0]
		self.assertEqual(row["drift"], 9240.0)
		self.assertIn("post_settlement_to_gl", row["drift_note"])

	def test_the_bucket_totals_add_up_to_the_grand_total(self):
		self.three_invoices()
		data = self.aged()
		self.assertEqual(
			round(sum(b["outstanding"] for b in data["buckets"].values()), 2),
			data["total_outstanding"],
		)

	def test_an_invoice_with_no_due_date_is_unknown_rather_than_current(self):
		name = self.standalone_invoice(posting_date="2026-10-01", due_date=None, submit=True)
		post_sales_invoice_gl(name)
		buckets = self.aged()["customers"][0]["buckets"]
		self.assertEqual(buckets["unknown"]["count"], 1)
		self.assertEqual(buckets["current"]["count"], 0)

	def test_a_company_with_no_receivable_account_is_empty_not_an_error(self):
		data = self.tool_data("get_ar_aging", {"company": OTHER})
		self.assertEqual(data["customers"], [])
		self.assertIn("no Account typed Receivable", data["note"])

	def test_the_customer_filter_narrows(self):
		self.three_invoices()
		other = self.standalone_invoice(
			customer=OTHER_PACKER, posting_date="2026-12-01", due_date="2027-01-01", submit=True
		)
		post_sales_invoice_gl(other)
		self.assertEqual(self.aged()["count"], 2)
		self.assertEqual(self.aged(customer=OTHER_PACKER)["count"], 1)


# ── Claim 14: the season ──────────────────────────────────────────────────


class SeasonSummary(SalesTestCase):
	def season(self, **overrides):
		args = {"company": MAIN, "from_date": "2026-01-01", "to_date": "2026-12-31"}
		args.update(overrides)
		return self.tool_data("get_season_summary", args)

	def test_deliveries_roll_up_by_variety(self):
		self.submitted_ticket()
		data = self.season()
		self.assertEqual(data["deliveries"]["ticket_count"], 1)
		self.assertEqual(data["deliveries"]["total_net_weight"], 12200.0)
		self.assertEqual(data["deliveries"]["by_variety"][0]["variety"], "Honeycrisp")

	def test_the_settlement_rollup_is_a_weighted_packout(self):
		"""Not the mean of each statement's own percentage — a two-bin statement
		would count as much as a two-hundred-bin one."""
		self.submitted_settlement()
		self.submitted_settlement(
			statement_number="SS-2026-0913",
			gross_delivered_weight=2000,
			packed_weight=200,
			cull_weight=0,
		)
		data = self.season()
		self.assertEqual(data["settlements"]["count"], 2)
		self.assertEqual(data["settlements"]["total_delivered_weight"], 50000.0)
		self.assertEqual(data["settlements"]["avg_packout_pct"], 62.8)

	def test_the_money_rolls_up_from_the_settlements(self):
		self.submitted_settlement()
		data = self.season()
		self.assertEqual(data["settlements"]["total_gross_revenue"], GROSS_REVENUE)
		self.assertEqual(data["settlements"]["total_deductions"], DEDUCTIONS)
		self.assertEqual(data["settlements"]["total_net_proceeds"], NET_PROCEEDS)

	def test_a_ticket_no_settlement_claimed_is_a_gap(self):
		self.submitted_ticket()
		data = self.season()
		self.assertEqual(data["unmatched_tickets"]["count"], 1)
		self.assertEqual(data["unmatched_tickets"]["total_weight"], 12200.0)
		self.assertEqual(data["pipeline_health"], "has_gaps")
		self.assertIn("nobody has been paid for", data["gaps"][0])

	def test_unsettled_deliveries_is_the_same_list_under_the_other_name(self):
		self.submitted_ticket()
		data = self.season()
		self.assertEqual(data["unsettled_deliveries"], data["unmatched_tickets"])

	def test_a_draft_ticket_counts_as_a_delivery_and_not_as_a_gap(self):
		"""A draft ticket is not yet evidence of anything."""
		self.capture()
		data = self.season()
		self.assertEqual(data["deliveries"]["ticket_count"], 1)
		self.assertEqual(data["unmatched_tickets"]["count"], 0)

	def test_a_settlement_nobody_invoiced_is_a_gap(self):
		self.submitted_settlement()
		data = self.season()
		self.assertEqual(data["uninvoiced_settlements"]["count"], 1)
		self.assertEqual(data["uninvoiced_settlements"]["total_net_proceeds"], NET_PROCEEDS)

	def test_invoicing_the_settlement_closes_that_gap(self):
		self.invoiced_settlement()
		data = self.season()
		self.assertEqual(data["uninvoiced_settlements"]["count"], 0)

	def test_posting_it_to_gl_closes_the_same_gap(self):
		settlement = self.submitted_settlement()
		self.tool_data(
			"post_settlement_to_gl",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		self.assertEqual(self.season()["uninvoiced_settlements"]["count"], 0)

	def test_a_billed_and_uncollected_invoice_is_a_gap(self):
		self.standalone_invoice(posting_date="2026-10-01", due_date="2026-11-01", submit=True)
		data = self.season()
		self.assertEqual(data["invoicing"]["invoice_count"], 1)
		self.assertEqual(data["invoicing"]["total_outstanding"], 500.0)
		self.assertIn("billed and not collected", " ".join(data["gaps"]))

	def test_a_clean_season_reads_complete(self):
		ticket = self.submitted_ticket()
		settlement = self.submitted_settlement(scale_tickets=[ticket])
		invoice = self.tool_data(
			"create_sales_invoice_from_settlement",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		self.tool_data("submit_sales_invoice", {"sales_invoice": invoice["name"]})
		payment = self.tool_data(
			"receive_payment",
			{
				"customer": MASTER_CUSTOMER,
				"company": MAIN,
				"paid_amount": NET_PROCEEDS,
				"paid_to": cash(),
			},
		)
		self.tool_data("submit_payment_entry", {"name": payment["payment_entry"]})
		data = self.season()
		self.assertEqual(data["gaps"], [])
		self.assertEqual(data["pipeline_health"], "complete")

	def test_the_dates_are_required_and_a_reversed_range_is_refused(self):
		self.assertIn("from_date is required", self.tool_error("get_season_summary", {"company": MAIN}))
		self.assertIn(
			"is after",
			self.tool_error(
				"get_season_summary",
				{"company": MAIN, "from_date": "2026-12-31", "to_date": "2026-01-01"},
			),
		)

	def test_a_delivery_outside_the_window_is_outside_the_window(self):
		self.submitted_ticket()
		data = self.season(from_date="2026-01-01", to_date="2026-06-30")
		self.assertEqual(data["deliveries"]["ticket_count"], 0)


# ── Claim 15: the journal-entry path ──────────────────────────────────────


class SettlementToJournalEntry(SalesTestCase):
	def post(self, **overrides):
		settlement = overrides.pop("settlement", None) or self.submitted_settlement()
		args = {"settlement_statement": settlement, "deduction_account": supplies()}
		args.update(overrides)
		return settlement, self.tool_data("post_settlement_to_gl", args)

	def test_the_entry_balances_and_is_a_draft(self):
		_, data = self.post()
		self.assertEqual(data["debit_total"], GROSS_REVENUE)
		self.assertEqual(data["credit_total"], GROSS_REVENUE)
		self.assertEqual(data["docstatus"], 0)
		self.assertEqual(data["line_count"], 3)

	def test_the_receivable_carries_the_net_and_the_packer_as_party(self):
		_settlement, data = self.post()
		entry = self.tool_data("get_journal_entry", {"name": data["journal_entry"]})
		by_account = {row["account"]: row for row in entry["accounts"]}
		self.assertEqual(by_account[RECEIVABLE]["debit"], NET_PROCEEDS)
		self.assertEqual(by_account[RECEIVABLE]["party"], MASTER_CUSTOMER)
		self.assertEqual(by_account[supplies()]["debit"], DEDUCTIONS)
		self.assertEqual(by_account[income_account()]["credit"], GROSS_REVENUE)

	def test_it_stamps_the_settlement_and_flips_it_to_posted(self):
		settlement, data = self.post()
		row = frappe.db.get_value(
			"Settlement Statement", settlement, ["posted_journal_entry", "status"], as_dict=True
		)
		self.assertEqual(row["posted_journal_entry"], data["journal_entry"])
		self.assertEqual(row["status"], "Posted")
		self.assertTrue(data["settlement_linked"])

	def test_posting_the_same_settlement_twice_is_refused(self):
		settlement, _ = self.post()
		message = self.tool_error(
			"post_settlement_to_gl",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		self.assertIn("already posted", message)

	def test_a_settlement_already_invoiced_is_refused_here(self):
		"""The refusal runs in both directions, which is what makes the pair of
		paths alternatives rather than a way round each other."""
		settlement, _ = self.invoiced_settlement()
		message = self.tool_error(
			"post_settlement_to_gl",
			{"settlement_statement": settlement, "deduction_account": supplies()},
		)
		self.assertIn("already invoiced", message)

	def test_a_draft_settlement_is_refused(self):
		draft = self.settle()["name"]
		message = self.tool_error(
			"post_settlement_to_gl",
			{"settlement_statement": draft, "deduction_account": supplies()},
		)
		self.assertIn("DRAFT", message)

	def test_the_deduction_account_is_required_when_there_are_deductions(self):
		settlement = self.submitted_settlement()
		message = self.tool_error("post_settlement_to_gl", {"settlement_statement": settlement})
		self.assertIn("deduction_account", message)

	def test_a_settlement_with_no_deductions_posts_two_lines(self):
		settlement = self.submitted_settlement(deductions=[])
		data = self.tool_data("post_settlement_to_gl", {"settlement_statement": settlement})
		self.assertEqual(data["line_count"], 2)
		self.assertEqual(data["debit_total"], GROSS_REVENUE)

	def test_a_settlement_with_no_revenue_is_refused(self):
		settlement = self.submitted_settlement(line_items=[], deductions=[])
		message = self.tool_error("post_settlement_to_gl", {"settlement_statement": settlement})
		self.assertIn("nothing to post", message)

	def test_the_posting_date_defaults_to_the_settlements_own(self):
		_, data = self.post()
		self.assertEqual(data["posting_date"], "2026-12-01")

	def test_the_remark_names_the_statement_and_the_arithmetic(self):
		_, data = self.post()
		self.assertIn("SS-2026-0912", data["user_remark"])
		self.assertIn("9240.0 net proceeds", data["user_remark"])

	def test_it_stays_a_draft_until_the_separate_submit_tool_runs(self):
		_, data = self.post()
		self.assertEqual(
			frappe.db.get_value("Journal Entry", data["journal_entry"], "docstatus"), 0
		)
		self.tool_data("submit_journal_entry", {"name": data["journal_entry"]})
		self.assertEqual(
			frappe.db.get_value("Journal Entry", data["journal_entry"], "docstatus"), 1
		)


# ── Claim 16: late tickets ────────────────────────────────────────────────


class LateTickets(SalesTestCase):
	def test_a_late_ticket_is_matched_and_the_variance_moves(self):
		settlement = self.submitted_settlement()
		ticket = self.submitted_ticket()
		data = self.tool_data(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [ticket]},
		)
		self.assertEqual(data["matched_count"], 1)
		self.assertEqual(data["variance_change"], -12200.0)
		self.assertEqual(data["updated_reconciliation"]["variance"], 35800.0)

	def test_the_ticket_leaves_the_unmatched_list(self):
		settlement = self.submitted_settlement()
		ticket = self.submitted_ticket()
		self.tool_data(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [ticket]},
		)
		self.assertEqual(
			self.tool_data("list_scale_tickets", {"unmatched": True})["count"], 0
		)

	def test_the_settlements_own_numbers_are_untouched(self):
		settlement = self.submitted_settlement()
		before = self.tool_data("get_settlement_statement", {"name": settlement})
		self.tool_data(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [self.submitted_ticket()]},
		)
		after = self.tool_data("get_settlement_statement", {"name": settlement})
		for field in ("gross_delivered_weight", "packed_weight", "net_proceeds", "packout_pct"):
			with self.subTest(field=field):
				self.assertEqual(before[field], after[field])

	def test_a_draft_ticket_is_refused(self):
		settlement = self.submitted_settlement()
		draft = self.capture()["name"]
		message = self.tool_error(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [draft]},
		)
		self.assertIn("not submitted", message)

	def test_a_ticket_already_matched_elsewhere_is_refused(self):
		ticket = self.submitted_ticket()
		self.submitted_settlement(scale_tickets=[ticket])
		second = self.submitted_settlement(statement_number="SS-2026-0913")
		message = self.tool_error(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": second, "scale_tickets": [ticket]},
		)
		self.assertIn("already matched", message)

	def test_another_packers_ticket_is_refused(self):
		settlement = self.submitted_settlement()
		theirs = self.submitted_ticket(ticket_number="9", customer=OTHER_PACKER)
		message = self.tool_error(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [theirs]},
		)
		self.assertIn("not to", message)

	def test_nothing_is_matched_when_one_ticket_in_the_list_is_bad(self):
		"""All the checks run BEFORE anything is written, so a settlement is
		never left with half its tickets claimed."""
		settlement = self.submitted_settlement()
		good = self.submitted_ticket(ticket_number="1")
		draft = self.capture(ticket_number="2")["name"]
		self.tool_error(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [good, draft]},
		)
		self.assertEqual(frappe.db.get_value("Scale Ticket", good, "settlement"), None)

	def test_an_empty_ticket_list_is_refused_with_where_to_look(self):
		settlement = self.submitted_settlement()
		message = self.tool_error(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": []},
		)
		self.assertIn("unmatched: true", message)

	def test_a_cancelled_settlement_claims_nothing(self):
		settlement = self.submitted_settlement()
		frappe.get_doc("Settlement Statement", settlement).cancel()
		message = self.tool_error(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [self.submitted_ticket()]},
		)
		self.assertIn("cancelled", message)

	def test_a_ticket_in_another_unit_is_matched_and_still_excluded(self):
		settlement = self.submitted_settlement()
		bins = self.submitted_ticket(
			ticket_number="7", weight_uom="Bin", gross_weight=40, tare_weight=0
		)
		data = self.tool_data(
			"reconcile_settlement_to_tickets",
			{"settlement_statement": settlement, "scale_tickets": [bins]},
		)
		self.assertEqual(data["matched_count"], 1)
		self.assertEqual(data["variance_change"], 0.0)
		self.assertEqual(
			data["updated_reconciliation"]["tickets_in_other_units_excluded"], 1
		)


# ── Claim 17: the switches, and the schema behind them ────────────────────


class SwitchesAndSchema(SalesTestCase):
	def test_every_mutating_tool_is_off_by_default(self):
		self.configure(enabled=1)
		for name in WRITE_TOOLS:
			with self.subTest(tool=name):
				self.assertIn("switched off", self.tool_error(name, {}))

	def test_every_read_tool_is_on_by_default(self):
		"""Called with no arguments each either answers or refuses on its own
		terms — what none of them may do is refuse because of a switch."""
		self.configure(enabled=1)
		for name in READ_TOOLS:
			with self.subTest(tool=name):
				result = self.tool(name, {})
				self.assertNotIn("switched off", result["content"][0]["text"])

	def test_each_switch_names_itself_when_it_is_the_one_that_is_off(self):
		for name in ALL_TOOLS:
			with self.subTest(tool=name):
				self.configure(enabled=1, **{**TOOLS_ON, f"allow_{name}": 0})
				self.assertIn(f"allow_{name}", self.tool_error(name, {}))

	def test_every_new_tool_has_a_switch_in_the_settings_doctype(self):
		payload = json.loads(
			(
				pathlib.Path(__file__).resolve().parents[1]
				/ "erpnext_mcp"
				/ "erpnext_mcp"
				/ "doctype"
				/ "erpnext_mcp_settings"
				/ "erpnext_mcp_settings.json"
			).read_text()
		)
		fieldnames = {field["fieldname"] for field in payload["fields"]}
		for name in ALL_TOOLS:
			with self.subTest(tool=name):
				self.assertIn(f"allow_{name}", fieldnames)

	def test_the_settlement_statement_ships_the_sales_invoice_link(self):
		payload = json.loads(
			(
				pathlib.Path(__file__).resolve().parents[1]
				/ "erpnext_mcp"
				/ "erpnext_mcp"
				/ "doctype"
				/ "settlement_statement"
				/ "settlement_statement.json"
			).read_text()
		)
		field = next(f for f in payload["fields"] if f["fieldname"] == "sales_invoice")
		self.assertEqual(field["fieldtype"], "Link")
		self.assertEqual(field["options"], "Sales Invoice")
		self.assertEqual(field["read_only"], 1)
		self.assertIn("sales_invoice", payload["field_order"])

	def test_the_sales_invoice_link_field_does_not_exist_until_it_is_needed(self):
		"""It is a Custom Field on somebody else's doctype, created on first use
		— so the 'this site has not got it yet' branch is a real state."""
		self.assertFalse(compat.has_field("Sales Invoice", "settlement_statement"))

	def test_the_first_settlement_invoice_creates_it_idempotently(self):
		self.invoiced_settlement()
		self.assertTrue(compat.has_field("Sales Invoice", "settlement_statement"))
		self.assertTrue(sales.ensure_settlement_link_field())
		rows = frappe.db.get_all(
			"Custom Field",
			filters={"dt": "Sales Invoice", "fieldname": "settlement_statement"},
			pluck="name",
		)
		self.assertEqual(len(rows), 1)

	def test_a_read_tool_writes_nothing(self):
		self.submitted_settlement()
		self.standalone_invoice(submit=True)
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		for name, arguments in {
			"list_sales_invoices": {},
			"get_packout_summary": {"company": MAIN},
			"get_ar_aging": {"company": MAIN},
			"get_season_summary": {
				"company": MAIN,
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
			},
		}.items():
			self.tool_data(name, arguments)
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		after.pop("MCP Action Log", None)
		before.pop("MCP Action Log", None)
		self.assertEqual(before, after)

	def test_the_ageing_buckets_agree_with_the_other_two_ageing_reports(self):
		"""Three modules, three copies of four lines of arithmetic. A reader who
		knows one ageing report in this app knows all three."""
		from erpnext_mcp.tools import purchasing, trade

		self.assertEqual(sales.AGEING_BUCKETS, purchasing.AGEING_BUCKETS)
		self.assertEqual(sales.AGEING_BUCKETS, trade.AGEING_BUCKETS)
		for days, label in ((-1, "current"), (0, "current"), (1, "0-30"), (45, "31-60"), (400, "90+")):
			with self.subTest(days=days):
				self.assertEqual(sales._bucket(days), label)
				self.assertEqual(sales._bucket(days), purchasing._bucket(days))
		self.assertEqual(sales._bucket(None), "unknown")
