# SPDX-License-Identifier: MIT
"""The five mutating tools, and the switches that keep them shut.

Most of these tests are about what does NOT happen.
"""

from erpnext_mcp import registry

from .fixtures import BANK_ACCOUNT, MAIN, OTHER, SeededTestCase, cash, sales, supplies
from .harness import STORE

ALL_ON = {f"allow_{name}": 1 for name in registry.MUTATING_TOOLS}


class DefaultsAreOff(SeededTestCase):
	def test_every_mutating_tool_is_refused_out_of_the_box(self):
		for name in registry.MUTATING_TOOLS:
			with self.subTest(tool=name):
				message = self.tool_error(name, {})
				self.assertIn(f"allow_{name}", message)

	def test_a_refused_mutation_writes_nothing(self):
		before = len(STORE.rows("Journal Entry"))
		self.tool_error(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "should not happen",
				"accounts": [
					{"account": cash(), "debit": 10},
					{"account": sales(), "credit": 10},
				],
			},
		)
		self.assertEqual(len(STORE.rows("Journal Entry")), before)

	def test_a_refused_mutation_is_logged_as_blocked(self):
		self.tool_error("submit_journal_entry", {"name": "ACC-JV-2026-00002"})
		row = self.assertAudited("submit_journal_entry", status="Blocked")
		self.assertIn("allow_submit_journal_entry is off", row["result_summary"])

	def test_enabling_one_does_not_enable_the_others(self):
		self.configure(enabled=1, allow_create_journal_entry=1)
		self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "Fine",
				"accounts": [
					{"account": cash(), "debit": 10},
					{"account": sales(), "credit": 10},
				],
			},
		)
		self.assertIn(
			"allow_submit_journal_entry",
			self.tool_error("submit_journal_entry", {"name": "JE-00001"}),
		)


class CreateJournalEntry(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def entry(self, **overrides):
		payload = {
			"company": MAIN,
			"posting_date": "2026-03-01",
			"user_remark": "Reclassify supplies",
			"accounts": [
				{"account": supplies(), "debit": 100},
				{"account": cash(), "credit": 100},
			],
		}
		payload.update(overrides)
		return payload

	def test_creates_a_draft(self):
		data = self.tool_data("create_journal_entry", self.entry())
		self.assertEqual(data["docstatus"], 0)
		self.assertEqual(data["docstatus_label"], "draft")
		self.assertEqual(data["total_debit"], 100)
		self.assertEqual(data["total_credit"], 100)
		stored = STORE.get_raw("Journal Entry", data["name"])
		self.assertEqual(stored["docstatus"], 0)

	def test_says_the_draft_affects_no_balance(self):
		"""The model has to understand that it has not posted anything."""
		data = self.tool_data("create_journal_entry", self.entry())
		self.assertIn("affects no balance", data["next_step"])

	def test_unbalanced_entry_creates_nothing(self):
		before = len(STORE.rows("Journal Entry"))
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": supplies(), "debit": 100},
					{"account": cash(), "credit": 90},
				]
			),
		)
		self.assertIn("do not equal credits", message)
		self.assertIn("difference 10.0", message)
		self.assertIn("Nothing was created", message)
		self.assertEqual(len(STORE.rows("Journal Entry")), before)

	def test_rounding_within_half_a_cent_is_accepted(self):
		data = self.tool_data(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": supplies(), "debit": 33.33},
					{"account": supplies(), "debit": 33.33},
					{"account": supplies(), "debit": 33.34},
					{"account": cash(), "credit": 100},
				]
			),
		)
		self.assertEqual(data["line_count"], 4)

	def test_a_single_line_is_refused(self):
		message = self.tool_error(
			"create_journal_entry", self.entry(accounts=[{"account": cash(), "debit": 10}])
		)
		self.assertIn("at least two lines", message)

	def test_a_line_with_both_debit_and_credit_is_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": cash(), "debit": 10, "credit": 10},
					{"account": sales(), "credit": 10},
				]
			),
		)
		self.assertIn("one or the other", message)

	def test_a_line_with_neither_is_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(accounts=[{"account": cash()}, {"account": sales()}]),
		)
		self.assertIn("neither a debit nor a credit", message)

	def test_a_negative_amount_is_refused_rather_than_silently_flipped(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": cash(), "debit": -10},
					{"account": sales(), "credit": -10},
				]
			),
		)
		self.assertIn("negative amount", message)

	def test_posting_to_a_group_account_is_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": "Current Assets", "debit": 10},
					{"account": sales(), "credit": 10},
				]
			),
		)
		self.assertIn("group account", message)

	def test_an_unsupported_line_field_is_named(self):
		"""A model that sent `amount` needs to be told the field is debit or
		credit, not handed a zero-value entry."""
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": cash(), "amount": 10},
					{"account": sales(), "credit": 10},
				]
			),
		)
		self.assertIn("unsupported field(s): amount", message)
		self.assertIn("debit", message)

	def test_accounts_must_be_a_list(self):
		message = self.tool_error("create_journal_entry", self.entry(accounts="cash 100"))
		self.assertIn("non-empty list", message)

	def test_user_remark_is_required(self):
		payload = self.entry()
		del payload["user_remark"]
		self.assertIn("user_remark is required", self.tool_error("create_journal_entry", payload))

	def test_company_is_required_on_a_multi_company_site(self):
		payload = self.entry()
		del payload["company"]
		self.assertIn("company is required", self.tool_error("create_journal_entry", payload))

	def test_an_account_from_another_company_is_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			self.entry(
				company=OTHER,
				accounts=[
					{"account": cash(), "debit": 10},
					{"account": sales("SEL"), "credit": 10},
				],
			),
		)
		self.assertIn("belongs to company", message)

	def test_optional_line_fields_are_carried_through(self):
		data = self.tool_data(
			"create_journal_entry",
			self.entry(
				accounts=[
					{"account": supplies(), "debit": 100, "cost_center": "Main - ETC"},
					{"account": cash(), "credit": 100, "user_remark": "petty cash"},
				]
			),
		)
		stored = STORE.get_raw("Journal Entry", data["name"])
		self.assertEqual(stored["accounts"][0]["cost_center"], "Main - ETC")
		self.assertEqual(stored["accounts"][1]["user_remark"], "petty cash")

	def test_the_audit_row_records_the_docstatus_delta(self):
		self.tool_data("create_journal_entry", self.entry())
		row = self.assertAudited("create_journal_entry", status="Success")
		self.assertEqual(row["docstatus_delta"], "none → 0 (draft)")


class SubmitJournalEntry(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def test_submits_a_draft(self):
		data = self.tool_data("submit_journal_entry", {"name": "ACC-JV-2026-00002"})
		self.assertEqual(data["docstatus"], 1)
		self.assertEqual(STORE.get_raw("Journal Entry", "ACC-JV-2026-00002")["docstatus"], 1)

	def test_records_the_delta(self):
		self.tool_data("submit_journal_entry", {"name": "ACC-JV-2026-00002"})
		row = self.assertAudited("submit_journal_entry", status="Success")
		self.assertEqual(row["docstatus_delta"], "0 → 1 (submitted)")

	def test_an_already_submitted_entry_is_refused(self):
		message = self.tool_error("submit_journal_entry", {"name": "ACC-JV-2026-00001"})
		self.assertIn("already submitted", message)

	def test_a_cancelled_entry_is_refused(self):
		STORE.get_raw("Journal Entry", "ACC-JV-2026-00002")["docstatus"] = 2
		message = self.tool_error("submit_journal_entry", {"name": "ACC-JV-2026-00002"})
		self.assertIn("cancelled and cannot be submitted", message)

	def test_an_unknown_entry_is_refused(self):
		message = self.tool_error("submit_journal_entry", {"name": "ACC-JV-9999-1"})
		self.assertIn("no Journal Entry named", message)

	def test_it_cannot_be_asked_to_create_anything(self):
		"""The schema is a name and nothing else — that is the whole safety
		property of splitting create from submit."""
		schema = registry.TOOLS["submit_journal_entry"]["inputSchema"]
		self.assertEqual(list(schema["properties"]), ["name"])
		self.assertFalse(schema["additionalProperties"])


class CancelJournalEntry(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def test_cancels_a_submitted_entry(self):
		data = self.tool_data(
			"cancel_journal_entry",
			{"name": "ACC-JV-2026-00001", "reason": "Duplicate of the February entry"},
		)
		self.assertEqual(data["docstatus"], 2)
		self.assertEqual(STORE.get_raw("Journal Entry", "ACC-JV-2026-00001")["docstatus"], 2)

	def test_the_reason_lands_on_the_document_and_in_the_log(self):
		reason = "Posted to the wrong period"
		self.tool_data("cancel_journal_entry", {"name": "ACC-JV-2026-00001", "reason": reason})
		comments = [c for c in STORE.comments if c.get("name") == "ACC-JV-2026-00001"]
		self.assertTrue(any(reason in c["text"] for c in comments))
		self.assertIn(reason, self.assertAudited("cancel_journal_entry")["result_summary"])

	def test_a_reason_is_mandatory(self):
		message = self.tool_error("cancel_journal_entry", {"name": "ACC-JV-2026-00001"})
		self.assertIn("reason is required", message)

	def test_a_placeholder_reason_is_refused(self):
		message = self.tool_error("cancel_journal_entry", {"name": "ACC-JV-2026-00001", "reason": "x"})
		self.assertIn("real explanation", message)

	def test_a_draft_is_refused_with_advice(self):
		message = self.tool_error(
			"cancel_journal_entry", {"name": "ACC-JV-2026-00002", "reason": "Not wanted"}
		)
		self.assertIn("is a draft", message)
		self.assertIn("delete it in ERPNext", message)

	def test_it_is_annotated_as_destructive(self):
		self.assertTrue(registry.TOOLS["cancel_journal_entry"]["annotations"]["destructiveHint"])

	def test_nothing_is_deleted(self):
		data = self.tool_data(
			"cancel_journal_entry", {"name": "ACC-JV-2026-00001", "reason": "Wrong account"}
		)
		self.assertIn("nothing was deleted", data["note"].lower())
		self.assertIsNotNone(STORE.get_raw("Journal Entry", "ACC-JV-2026-00001"))


class CreateBankTransaction(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def test_a_positive_amount_becomes_a_deposit(self):
		data = self.tool_data(
			"create_bank_transaction",
			{
				"bank_account": BANK_ACCOUNT,
				"date": "2026-03-01",
				"amount": 500,
				"description": "Refund received",
			},
		)
		stored = STORE.get_raw("Bank Transaction", data["name"])
		self.assertEqual(stored["deposit"], 500)
		self.assertEqual(stored["withdrawal"], 0)

	def test_a_negative_amount_becomes_a_withdrawal(self):
		data = self.tool_data(
			"create_bank_transaction",
			{
				"bank_account": BANK_ACCOUNT,
				"date": "2026-03-01",
				"amount": -75.25,
				"description": "Bank fee",
			},
		)
		stored = STORE.get_raw("Bank Transaction", data["name"])
		self.assertEqual(stored["deposit"], 0)
		self.assertEqual(stored["withdrawal"], 75.25)

	def test_it_lands_as_a_draft(self):
		data = self.tool_data(
			"create_bank_transaction",
			{
				"bank_account": BANK_ACCOUNT,
				"date": "2026-03-01",
				"amount": 10,
				"description": "Interest",
			},
		)
		self.assertEqual(data["docstatus"], 0)
		self.assertIn("not reconcilable", data["next_step"])

	def test_company_and_currency_come_from_the_bank_account(self):
		data = self.tool_data(
			"create_bank_transaction",
			{
				"bank_account": "Operating",
				"date": "2026-03-01",
				"amount": 10,
				"description": "Interest",
			},
		)
		stored = STORE.get_raw("Bank Transaction", data["name"])
		self.assertEqual(stored["company"], MAIN)
		self.assertEqual(stored["currency"], "USD")

	def test_a_zero_amount_is_refused(self):
		message = self.tool_error(
			"create_bank_transaction",
			{
				"bank_account": BANK_ACCOUNT,
				"date": "2026-03-01",
				"amount": 0,
				"description": "Nothing",
			},
		)
		self.assertIn("non-zero", message)

	def test_a_non_numeric_amount_says_so(self):
		message = self.tool_error(
			"create_bank_transaction",
			{
				"bank_account": BANK_ACCOUNT,
				"date": "2026-03-01",
				"amount": "five hundred",
				"description": "Nope",
			},
		)
		self.assertIn("amount must be a number", message)


class ReconcileBankTransaction(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def voucher(self, amount=400, name="PE-0002"):
		return {
			"name": "BT-2026-0001",
			"payment_entries": [
				{
					"payment_document": "Payment Entry",
					"payment_entry": name,
					"allocated_amount": amount,
				}
			],
		}

	def test_attaches_a_voucher(self):
		data = self.tool_data("reconcile_bank_transaction", self.voucher())
		self.assertEqual(data["allocated_now"], 400)
		self.assertEqual(len(data["payment_entries"]), 1)
		stored = STORE.get_raw("Bank Transaction", "BT-2026-0001")
		self.assertEqual(stored["payment_entries"][0]["payment_entry"], "PE-0002")

	def test_over_allocation_is_refused_and_changes_nothing(self):
		message = self.tool_error("reconcile_bank_transaction", self.voucher(amount=1500))
		self.assertIn("would exceed", message)
		self.assertIn("Nothing was changed", message)
		self.assertEqual(STORE.get_raw("Bank Transaction", "BT-2026-0001")["payment_entries"], [])

	def test_allocation_against_an_already_reconciled_transaction_is_refused(self):
		message = self.tool_error(
			"reconcile_bank_transaction",
			{
				"name": "BT-2026-0002",
				"payment_entries": [
					{
						"payment_document": "Payment Entry",
						"payment_entry": "PE-0002",
						"allocated_amount": 1,
					}
				],
			},
		)
		self.assertIn("remaining 0.0", message)

	def test_a_voucher_that_does_not_exist_is_refused(self):
		message = self.tool_error("reconcile_bank_transaction", self.voucher(name="PE-9999"))
		self.assertIn("no Payment Entry named", message)

	def test_an_unknown_voucher_doctype_is_refused(self):
		message = self.tool_error(
			"reconcile_bank_transaction",
			{
				"name": "BT-2026-0001",
				"payment_entries": [
					{
						"payment_document": "Invented Doctype",
						"payment_entry": "X-1",
						"allocated_amount": 1,
					}
				],
			},
		)
		self.assertIn("no such DocType", message)

	def test_a_non_positive_allocation_is_refused(self):
		message = self.tool_error("reconcile_bank_transaction", self.voucher(amount=-5))
		self.assertIn("must be positive", message)

	def test_incomplete_voucher_fields_are_named(self):
		message = self.tool_error(
			"reconcile_bank_transaction",
			{"name": "BT-2026-0001", "payment_entries": [{"allocated_amount": 5}]},
		)
		self.assertIn("payment_document", message)
		self.assertIn("payment_entry", message)

	def test_it_delegates_to_erpnext_when_the_method_exists(self):
		"""On a real site ERPNext owns clearance dates, allocation arithmetic and
		status; reimplementing those is how a transaction ends up looking
		reconciled without being it."""
		from erpnext_mcp.tools import mutate

		called = {}

		original = mutate.frappe.get_doc

		def patched(*args, **kwargs):
			doc = original(*args, **kwargs)
			if args and args[0] == "Bank Transaction":

				def add_payment_entries(vouchers):
					called["vouchers"] = vouchers
					doc.status = "Reconciled"
					doc.save()

				doc.add_payment_entries = add_payment_entries
			return doc

		mutate.frappe.get_doc = patched
		try:
			data = self.tool_data("reconcile_bank_transaction", self.voucher())
		finally:
			mutate.frappe.get_doc = original
		self.assertEqual(called["vouchers"][0]["payment_entry"], "PE-0002")
		self.assertEqual(data["applied_via"], "ERPNext add_payment_entries")

	def test_the_fallback_path_is_labelled(self):
		data = self.tool_data("reconcile_bank_transaction", self.voucher())
		self.assertIn("legacy ERPNext", data["applied_via"])
