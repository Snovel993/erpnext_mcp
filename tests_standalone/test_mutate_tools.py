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
		"""Nothing mutates on a fresh install — and the refusal says which kind.

		A tool whose site prerequisite is missing is refused for THAT reason
		rather than for the switch, because `dispatch` checks availability first
		and the two need different words: "your operator turned this off" is a
		request to go and ask them, while "this site has no shapely" is a request
		to stop trying. Both are refusals and neither writes anything, which is
		what this test is really about.
		"""
		for name in registry.MUTATING_TOOLS:
			with self.subTest(tool=name):
				message = self.tool_error(name, {})
				if registry.is_available(name):
					self.assertIn(f"allow_{name}", message)
				else:
					self.assertIn("not available on this site", message)
					self.assertIn("not something an operator can switch on", message)

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

	def test_payment_doctype_is_named_as_the_wrong_field(self):
		"""`payment_doctype` is the name the field has almost everywhere else in
		Frappe, so it is the one a model reaches for first; on Bank Transaction
		Payments it is `payment_document`. Being told which costs one round trip.
		Guessing would make this the only tool in the app that reads a key it was
		not given."""
		message = self.tool_error(
			"reconcile_bank_transaction",
			{
				"name": "BT-2026-0001",
				"payment_entries": [
					{
						"payment_doctype": "Payment Entry",
						"payment_entry": "PE-0002",
						"allocated_amount": 400,
					}
				],
			},
		)
		self.assertIn("the field is called payment_document", message)
		self.assertIn("'Payment Entry'", message)
		self.assertIn("Nothing was changed", message)
		self.assertEqual(STORE.get_raw("Bank Transaction", "BT-2026-0001")["payment_entries"], [])

	def test_a_journal_entry_round_trips_from_creation_to_allocation(self):
		"""The whole path an operator takes off a bank statement: draft the entry,
		post it, allocate it against the transaction that paid for it."""
		self.configure(enabled=1, **ALL_ON)
		created = self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-01-15",
				"user_remark": "Customer deposit per bank statement",
				"accounts": [
					{"account": cash(), "debit": 600},
					{"account": sales(), "credit": 600},
				],
			},
		)
		self.tool_data("submit_journal_entry", {"name": created["name"]})
		data = self.tool_data(
			"reconcile_bank_transaction",
			{
				"name": "BT-2026-0001",
				"payment_entries": [
					{
						"payment_document": "Journal Entry",
						"payment_entry": created["name"],
						"allocated_amount": 600,
					}
				],
			},
		)
		self.assertEqual(data["allocated_now"], 600)
		row = STORE.get_raw("Bank Transaction", "BT-2026-0001")["payment_entries"][0]
		self.assertEqual(row["payment_document"], "Journal Entry")
		self.assertEqual(row["payment_entry"], created["name"])
		self.assertEqual(row["allocated_amount"], 600)

	def test_erpnexts_own_method_gets_the_allocation_and_sets_the_clearance_date(self):
		"""Clearance dates and the allocated/unallocated split are ERPNext's to
		write; this asserts what it is handed and that the result is read back."""
		from erpnext_mcp.tools import mutate

		original = mutate.frappe.get_doc

		def patched(*args, **kwargs):
			doc = original(*args, **kwargs)
			if args and args[0] == "Bank Transaction":

				def add_payment_entries(vouchers):
					allocated = sum(v["allocated_amount"] for v in vouchers)
					for voucher in vouchers:
						doc.append("payment_entries", voucher)
					doc.allocated_amount = allocated
					doc.unallocated_amount = 1000 - allocated
					doc.clearance_date = "2026-01-15"
					doc.status = "Reconciled"
					doc.save()

				doc.add_payment_entries = add_payment_entries
			return doc

		mutate.frappe.get_doc = patched
		try:
			data = self.tool_data("reconcile_bank_transaction", self.voucher(amount=400))
		finally:
			mutate.frappe.get_doc = original
		self.assertEqual(data["allocated_total"], 400)
		self.assertEqual(data["unallocated_amount"], 600)
		self.assertEqual(data["status"], "Reconciled")
		self.assertEqual(STORE.get_raw("Bank Transaction", "BT-2026-0001")["clearance_date"], "2026-01-15")


class AccountCurrencyAmounts(SeededTestCase):
	"""The v0.8.0 bug: a line with only `debit` posts as zero.

	ERPNext derives `debit` FROM `debit_in_account_currency` on every validate,
	so a Journal Entry built with `debit` alone is written to the database with
	its amounts zeroed and is refused on submit — a draft that cannot be posted
	and does not say why. Four auto-generated opening-balance entries did exactly
	that on a live site. `harness.JournalEntryDocument` models the derivation, so
	these tests fail against the unfixed code rather than passing against it.
	"""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def entry(self, **overrides):
		payload = {
			"company": MAIN,
			"posting_date": "2026-03-01",
			"user_remark": "Cash sale",
			"accounts": [
				{"account": cash(), "debit": 100},
				{"account": sales(), "credit": 100},
			],
		}
		payload.update(overrides)
		return self.tool_data("create_journal_entry", payload)

	def lines(self, name):
		return STORE.get_raw("Journal Entry", name)["accounts"]

	def test_every_line_carries_both_amount_columns(self):
		lines = self.lines(self.entry()["name"])
		self.assertEqual(lines[0]["debit_in_account_currency"], 100)
		self.assertEqual(lines[0]["credit_in_account_currency"], 0)
		self.assertEqual(lines[1]["credit_in_account_currency"], 100)
		self.assertEqual(lines[1]["debit_in_account_currency"], 0)

	def test_the_amounts_survive_the_insert(self):
		"""The bug's first symptom: a draft that reads 0.00 the moment it is saved."""
		lines = self.lines(self.entry()["name"])
		self.assertEqual([line["debit"] for line in lines], [100, 0])
		self.assertEqual([line["credit"] for line in lines], [0, 100])

	def test_the_entry_can_actually_be_submitted(self):
		"""The bug's second symptom, and the one that cost a day."""
		name = self.entry()["name"]
		data = self.tool_data("submit_journal_entry", {"name": name})
		self.assertEqual(data["docstatus"], 1)
		self.assertEqual(data["total_debit"], 100)
		self.assertEqual(data["total_credit"], 100)

	def test_an_amount_given_only_in_account_currency_is_understood(self):
		data = self.entry(
			accounts=[
				{"account": cash(), "debit_in_account_currency": 100},
				{"account": sales(), "credit_in_account_currency": 100},
			]
		)
		self.assertEqual(data["total_debit"], 100)
		self.assertEqual([line["debit"] for line in self.lines(data["name"])], [100, 0])

	def test_a_rate_of_one_copies_rather_than_divides(self):
		"""No arithmetic may put a fraction of a cent between two columns of the
		same number, so the common case copies. Asserted on the line this app
		builds rather than the stored row, because ERPNext rounds to the field's
		precision afterwards and would hide the difference."""
		from erpnext_mcp.tools import mutate

		lines = mutate.validated_journal_lines(
			[{"account": cash(), "debit": 33.335}, {"account": sales(), "credit": 33.335}], MAIN
		)
		self.assertEqual(lines[0]["debit_in_account_currency"], 33.335)
		self.assertEqual(lines[1]["credit_in_account_currency"], 33.335)

	def test_an_explicit_exchange_rate_divides_into_the_account_currency(self):
		STORE.tables["Account"][cash()]["account_currency"] = "CAD"
		data = self.entry(
			accounts=[
				{"account": cash(), "debit": 200, "exchange_rate": 2},
				{"account": sales(), "credit": 200},
			]
		)
		self.assertEqual(self.lines(data["name"])[0]["debit_in_account_currency"], 100)

	def test_a_supplied_account_currency_amount_is_not_recomputed(self):
		"""The caller knows what the invoice said and this does not."""
		STORE.tables["Account"][cash()]["account_currency"] = "CAD"
		data = self.entry(
			accounts=[
				{
					"account": cash(),
					"debit": 275,
					"debit_in_account_currency": 137.5,
					"exchange_rate": 2,
				},
				{"account": sales(), "credit": 275},
			]
		)
		line = self.lines(data["name"])[0]
		self.assertEqual(line["debit_in_account_currency"], 137.5)
		self.assertEqual(line["debit"], 275)

	def test_two_amounts_that_disagree_are_refused_rather_than_one_chosen(self):
		"""ERPNext posts the account-currency figure times the rate. An entry that
		balances on `debit` and not on that is one that balances here and not on
		the site."""
		STORE.tables["Account"][cash()]["account_currency"] = "CAD"
		message = self.tool_error(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "Cash sale",
				"accounts": [
					{
						"account": cash(),
						"debit": 200,
						"debit_in_account_currency": 137.5,
						"exchange_rate": 2,
					},
					{"account": sales(), "credit": 200},
				],
			},
		)
		self.assertIn("comes to 275.0", message)
		self.assertIn("cannot both be right", message)

	def test_a_foreign_account_with_no_rate_is_refused(self):
		STORE.tables["Account"][cash()]["account_currency"] = "CAD"
		message = self.tool_error(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "Cash sale",
				"accounts": [
					{"account": cash(), "debit": 100},
					{"account": sales(), "credit": 100},
				],
			},
		)
		self.assertIn("held in CAD", message)
		self.assertIn("exchange_rate", message)
		self.assertIn("Nothing was created", message)

	def test_a_zero_or_negative_rate_is_refused(self):
		message = self.tool_error(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "Cash sale",
				"accounts": [
					{"account": cash(), "debit": 100, "exchange_rate": -1},
					{"account": sales(), "credit": 100},
				],
			},
		)
		self.assertIn("must be positive", message)


class BulkSubmitJournalEntries(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def draft(self, amount=10):
		return self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": f"Batch entry {amount}",
				"accounts": [
					{"account": cash(), "debit": amount},
					{"account": sales(), "credit": amount},
				],
			},
		)["name"]

	def test_it_posts_every_draft_in_the_batch(self):
		names = [self.draft(10), self.draft(20), self.draft(30)]
		data = self.tool_data("bulk_submit_journal_entries", {"names": names})
		self.assertEqual(data["total"], 3)
		self.assertEqual(data["submitted"], 3)
		self.assertEqual(data["failed"], 0)
		self.assertEqual(data["submitted_names"], names)
		for name in names:
			self.assertEqual(STORE.get_raw("Journal Entry", name)["docstatus"], 1)

	def test_an_already_submitted_entry_is_skipped_not_failed(self):
		"""A half-finished batch has to be safe to retry whole."""
		names = [self.draft(10), self.draft(20)]
		self.tool_data("submit_journal_entry", {"name": names[0]})
		data = self.tool_data("bulk_submit_journal_entries", {"names": names})
		self.assertEqual(data["submitted"], 1)
		self.assertEqual(data["skipped"], 1)
		self.assertEqual(data["failed"], 0)
		skipped = next(row for row in data["results"] if row["name"] == names[0])
		self.assertTrue(skipped["ok"])
		self.assertEqual(skipped["skipped"], "already_submitted")

	def test_one_bad_entry_does_not_abort_the_batch(self):
		names = [self.draft(10), "ACC-JV-NOPE", self.draft(20)]
		data = self.tool_data("bulk_submit_journal_entries", {"names": names})
		self.assertEqual(data["submitted"], 2)
		self.assertEqual(data["failed"], 1)
		self.assertEqual(data["failed_names"], ["ACC-JV-NOPE"])
		self.assertIn("no Journal Entry named", data["results"][1]["error"])
		self.assertEqual(STORE.get_raw("Journal Entry", names[2])["docstatus"], 1)

	def test_the_entries_that_posted_stay_posted(self):
		"""The whole point of a transaction per document."""
		good = self.draft(10)
		data = self.tool_data("bulk_submit_journal_entries", {"names": [good, "ACC-JV-NOPE"]})
		self.assertEqual(data["failed"], 1)
		self.assertEqual(STORE.get_raw("Journal Entry", good)["docstatus"], 1)

	def test_a_cancelled_entry_is_a_failure_not_a_skip(self):
		name = self.draft(10)
		self.tool_data("submit_journal_entry", {"name": name})
		self.tool_data("cancel_journal_entry", {"name": name, "reason": "keyed twice"})
		data = self.tool_data("bulk_submit_journal_entries", {"names": [name]})
		self.assertEqual(data["failed"], 1)
		self.assertIn("cancelled", data["results"][0]["error"])

	def test_it_refuses_when_submit_journal_entry_is_off(self):
		"""That switch is where posting is allowed or not; this does not go round it."""
		name = self.draft(10)
		self.configure(enabled=1, allow_bulk_submit_journal_entries=1)
		message = self.tool_error("bulk_submit_journal_entries", {"names": [name]})
		self.assertIn("allow_submit_journal_entry", message)
		self.assertIn("Nothing was submitted", message)
		self.assertEqual(STORE.get_raw("Journal Entry", name)["docstatus"], 0)

	def test_it_refuses_when_its_own_switch_is_off(self):
		name = self.draft(10)
		self.configure(enabled=1, allow_create_journal_entry=1, allow_submit_journal_entry=1)
		message = self.tool_error("bulk_submit_journal_entries", {"names": [name]})
		self.assertIn("allow_bulk_submit_journal_entries", message)

	def test_an_empty_list_is_refused(self):
		message = self.tool_error("bulk_submit_journal_entries", {"names": []})
		self.assertIn("non-empty list", message)

	def test_a_repeated_name_is_submitted_once(self):
		name = self.draft(10)
		data = self.tool_data("bulk_submit_journal_entries", {"names": [name, name]})
		self.assertEqual(data["total"], 1)
		self.assertEqual(data["submitted"], 1)

	def test_a_batch_larger_than_the_limit_is_refused_before_anything_posts(self):
		message = self.tool_error(
			"bulk_submit_journal_entries", {"names": [f"JE-{n}" for n in range(501)]}
		)
		self.assertIn("the limit is 500", message)
		self.assertIn("Nothing was submitted", message)

	def test_the_next_step_names_what_to_do_about_failures(self):
		data = self.tool_data("bulk_submit_journal_entries", {"names": [self.draft(10), "JE-NOPE"]})
		self.assertIn("call this again with just those names", data["next_step"])

	def test_a_clean_batch_says_nothing_is_outstanding(self):
		data = self.tool_data("bulk_submit_journal_entries", {"names": [self.draft(10)]})
		self.assertIn("Nothing is outstanding", data["next_step"])


class DeleteDraftJournalEntry(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def payload(self, name="ACC-JV-2026-00002", **overrides):
		values = {"name": name, "reason": "duplicate of ACC-JV-2026-00001, keyed twice"}
		values.update(overrides)
		return values

	def test_it_deletes_the_draft(self):
		data = self.tool_data("delete_draft_journal_entry", self.payload())
		self.assertEqual(data["deleted"]["name"], "ACC-JV-2026-00002")
		self.assertIsNone(STORE.get_raw("Journal Entry", "ACC-JV-2026-00002"))

	def test_it_reports_what_it_deleted_because_nothing_else_will(self):
		data = self.tool_data("delete_draft_journal_entry", self.payload())
		deleted = data["deleted"]
		self.assertEqual(deleted["company"], MAIN)
		self.assertEqual(deleted["posting_date"], "2026-02-10")
		self.assertEqual(deleted["line_count"], 2)
		self.assertEqual(deleted["total_debit"], 250)
		self.assertEqual([row["account"] for row in deleted["accounts"]], [supplies(), cash()])

	def test_the_audit_row_carries_the_reason_and_the_entry(self):
		self.tool_data("delete_draft_journal_entry", self.payload())
		row = self.assertAudited("delete_draft_journal_entry", status="Success")
		self.assertIn("deleted draft Journal Entry ACC-JV-2026-00002", row["result_summary"])
		self.assertIn("keyed twice", row["result_summary"])
		self.assertEqual(row["docstatus_delta"], "0 (draft) → deleted")

	def test_a_submitted_entry_is_refused_and_pointed_at_cancel(self):
		message = self.tool_error("delete_draft_journal_entry", self.payload("ACC-JV-2026-00001"))
		self.assertIn("cancel_journal_entry", message)
		self.assertIn("Nothing was deleted", message)
		self.assertIsNotNone(STORE.get_raw("Journal Entry", "ACC-JV-2026-00001"))

	def test_a_cancelled_entry_is_refused(self):
		STORE.tables["Journal Entry"]["ACC-JV-2026-00002"]["docstatus"] = 2
		message = self.tool_error("delete_draft_journal_entry", self.payload())
		self.assertIn("audit trail with a hole in it", message)
		self.assertIsNotNone(STORE.get_raw("Journal Entry", "ACC-JV-2026-00002"))

	def test_an_entry_that_does_not_exist_is_refused(self):
		message = self.tool_error("delete_draft_journal_entry", self.payload("ACC-JV-NOPE"))
		self.assertIn("no Journal Entry named", message)

	def test_a_placeholder_reason_is_refused(self):
		message = self.tool_error("delete_draft_journal_entry", self.payload(reason="x"))
		self.assertIn("real explanation", message)
		self.assertIsNotNone(STORE.get_raw("Journal Entry", "ACC-JV-2026-00002"))

	def test_a_missing_reason_is_refused(self):
		message = self.tool_error("delete_draft_journal_entry", {"name": "ACC-JV-2026-00002"})
		self.assertIn("reason is required", message)

	def test_the_switch_keeps_it_shut(self):
		self.configure(enabled=1)
		message = self.tool_error("delete_draft_journal_entry", self.payload())
		self.assertIn("allow_delete_draft_journal_entry", message)
		self.assertIsNotNone(STORE.get_raw("Journal Entry", "ACC-JV-2026-00002"))
