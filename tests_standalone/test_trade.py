# SPDX-License-Identifier: MIT
"""Sales orders, purchase orders and receivables ageing."""

from .fixtures import MAIN, OTHER, V2TestCase
from .harness import STORE


class SalesOrders(V2TestCase):
	def test_lists_headers_newest_first(self):
		data = self.tool_data("list_sales_orders")
		self.assertEqual(
			[row["name"] for row in data["orders"]],
			["SAL-ORD-2026-00001", "SAL-ORD-2026-00002"],
		)

	def test_carries_the_delivery_progress(self):
		data = self.tool_data("list_sales_orders", {"status": "To Deliver and Bill"})
		self.assertEqual(data["orders"][0]["per_delivered"], 0)
		self.assertEqual(data["orders"][0]["delivery_date"], "2026-06-19")

	def test_filters_by_customer(self):
		data = self.tool_data("list_sales_orders", {"customer": "Southgate Markets"})
		self.assertEqual([row["name"] for row in data["orders"]], ["SAL-ORD-2026-00002"])

	def test_filters_by_date_range(self):
		data = self.tool_data("list_sales_orders", {"from_date": "2026-06-01", "to_date": "2026-06-30"})
		self.assertEqual(data["count"], 1)

	def test_totals_the_value_of_what_it_returned(self):
		data = self.tool_data("list_sales_orders")
		self.assertEqual(data["total_value"], 15600.0)
		self.assertIn("partial figure when truncated", data["note"])

	def test_counts_by_status(self):
		data = self.tool_data("list_sales_orders")
		self.assertEqual(data["by_status"], {"To Deliver and Bill": 1, "Completed": 1})

	def test_labels_the_docstatus(self):
		data = self.tool_data("list_sales_orders")
		self.assertEqual(data["orders"][0]["docstatus_label"], "submitted")

	def test_an_inverted_date_range_is_refused(self):
		message = self.tool_error("list_sales_orders", {"from_date": "2026-12-31", "to_date": "2026-01-01"})
		self.assertIn("is after", message)

	def test_a_company_filter_that_matches_nothing_is_empty_not_an_error(self):
		data = self.tool_data("list_sales_orders", {"company": OTHER})
		self.assertEqual(data["count"], 0)


class PurchaseOrders(V2TestCase):
	def test_lists_headers_with_receipt_progress(self):
		data = self.tool_data("list_purchase_orders")
		self.assertEqual(data["count"], 3)
		by_name = {row["name"]: row for row in data["orders"]}
		self.assertEqual(by_name["PUR-ORD-2026-00002"]["per_received"], 100)

	def test_filters_by_supplier(self):
		data = self.tool_data("list_purchase_orders", {"supplier": "Second Supplier LLC"})
		self.assertEqual([row["name"] for row in data["orders"]], ["PUR-ORD-2026-00002"])

	def test_uses_schedule_date_as_the_promise_field(self):
		"""A Purchase Order promises a schedule_date, not a delivery_date — the
		mirrored implementation has to swap the noun, not just the doctype."""
		data = self.tool_data("list_purchase_orders", {"status": "Completed"})
		self.assertEqual(data["orders"][0]["schedule_date"], "2026-05-30")
		self.assertNotIn("delivery_date", data["orders"][0])

	def test_reports_its_own_doctype(self):
		data = self.tool_data("list_purchase_orders")
		self.assertEqual(data["doctype"], "Purchase Order")

	def test_the_supplier_filter_key_is_echoed_back(self):
		data = self.tool_data("list_purchase_orders", {"supplier": "Example Supplies Inc"})
		self.assertEqual(data["filters"]["supplier"], "Example Supplies Inc")

	def test_truncation_is_reported(self):
		data = self.tool_data("list_purchase_orders", {"limit": 2})
		self.assertEqual(data["count"], 2)
		self.assertTrue(data["truncated"])


class OutstandingInvoices(V2TestCase):
	def test_only_submitted_invoices_with_money_owed(self):
		data = self.tool_data("get_outstanding_invoices")
		names = [row["name"] for row in data["invoices"]]
		self.assertNotIn("ACC-SINV-2026-00007", names)  # settled
		self.assertNotIn("ACC-SINV-2026-00008", names)  # cancelled
		self.assertEqual(data["count"], 6)

	def test_totals_the_outstanding(self):
		data = self.tool_data("get_outstanding_invoices")
		self.assertEqual(data["total_outstanding"], 14100.0)

	def test_a_future_due_date_is_current_not_zero_to_thirty(self):
		"""Folding not-yet-due invoices into 0-30 overstates the exposure, which
		is the whole reason for the extra bucket."""
		data = self.tool_data("get_outstanding_invoices")
		by_name = {row["name"]: row for row in data["invoices"]}
		self.assertEqual(by_name["ACC-SINV-2026-00001"]["ageing_bucket"], "current")
		self.assertLess(by_name["ACC-SINV-2026-00001"]["days_overdue"], 0)

	def test_each_bucket_gets_its_invoice(self):
		data = self.tool_data("get_outstanding_invoices")
		by_name = {row["name"]: row["ageing_bucket"] for row in data["invoices"]}
		self.assertEqual(by_name["ACC-SINV-2026-00002"], "0-30")
		self.assertEqual(by_name["ACC-SINV-2026-00003"], "31-60")
		self.assertEqual(by_name["ACC-SINV-2026-00004"], "61-90")
		self.assertEqual(by_name["ACC-SINV-2026-00005"], "90+")

	def test_an_invoice_with_no_due_date_is_unknown_not_current(self):
		"""An invoice nobody put terms on is a real problem, and hiding it in
		'current' is how it stays one."""
		data = self.tool_data("get_outstanding_invoices")
		by_name = {row["name"]: row["ageing_bucket"] for row in data["invoices"]}
		self.assertEqual(by_name["ACC-SINV-2026-00006"], "unknown")
		self.assertIsNone(
			next(r for r in data["invoices"] if r["name"] == "ACC-SINV-2026-00006")["days_overdue"]
		)

	def test_bucket_totals_add_up(self):
		data = self.tool_data("get_outstanding_invoices")
		self.assertEqual(data["buckets"]["90+"], {"count": 1, "outstanding": 5000.0})
		self.assertEqual(data["buckets"]["0-30"], {"count": 1, "outstanding": 500.0})
		self.assertEqual(
			round(sum(b["outstanding"] for b in data["buckets"].values()), 2),
			data["total_outstanding"],
		)

	def test_the_as_of_date_moves_the_buckets(self):
		"""ACC-SINV-2026-00001 is due 2026-08-30: not yet due at the default
		as_of, 31 days overdue at 2026-09-30."""
		data = self.tool_data("get_outstanding_invoices", {"as_of": "2026-09-30"})
		by_name = {row["name"]: row["ageing_bucket"] for row in data["invoices"]}
		self.assertEqual(by_name["ACC-SINV-2026-00001"], "31-60")
		self.assertEqual(by_name["ACC-SINV-2026-00002"], "61-90")
		self.assertEqual(by_name["ACC-SINV-2026-00006"], "unknown")

	def test_filters_by_customer(self):
		data = self.tool_data("get_outstanding_invoices", {"customer": "Westbrook Cafe"})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["total_outstanding"], 5600.0)

	def test_the_bucket_definition_is_stated(self):
		data = self.tool_data("get_outstanding_invoices")
		self.assertIn("as_of - due_date", data["bucket_definition"])
		self.assertIn("not yet due", data["bucket_definition"])

	def test_defaults_as_of_to_today(self):
		data = self.tool_data("get_outstanding_invoices")
		self.assertEqual(data["as_of"], "2026-07-24")


class TradeToolsDoNotWrite(V2TestCase):
	def test_no_trade_tool_changes_a_row(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data("list_sales_orders")
		self.tool_data("list_purchase_orders")
		self.tool_data("get_outstanding_invoices")
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		after.pop("MCP Action Log", None)
		before.pop("MCP Action Log", None)
		self.assertEqual(before, after)

	def test_they_are_gated_on_erpnext_being_installed(self):
		STORE.installed_apps.remove("erpnext")
		message = self.tool_error("list_sales_orders")
		self.assertIn("not available on this site", message)
		self.assertIn("ERPNext", message)

	def test_a_company_filter_resolves_an_abbreviation(self):
		data = self.tool_data("list_sales_orders", {"company": "ETC"})
		self.assertEqual(data["filters"]["company"], MAIN)
