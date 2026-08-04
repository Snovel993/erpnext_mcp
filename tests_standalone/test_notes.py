# SPDX-License-Identifier: MIT
"""Notes payable: what the company owes, on what terms, and against what.

Four things these tests are really about.

THE SPLIT IS THE WHOLE JOB. A loan payment leaving a bank account is one number
whose two halves land in completely different places — one reduces a liability,
one is an expense of the period. Booked as a single line against the liability,
the year's interest expense reads as nil and the balance sheet says the note was
paid down by more than it was. So `record_loan_payment` refuses a split that does
not add up, refuses interest with nowhere to put it, and the arithmetic has tests
rather than the existence of a journal entry.

NOTHING POSTS. The payment produces a DRAFT, like everything else in this app.
The consequence is that the note's outstanding figure and the liability account
disagree by every unposted payment, which is a real property of the design and is
therefore asserted rather than papered over.

CLOSING WRITES NO JOURNAL ENTRY, DELIBERATELY. Relieving a written-off balance is
a posting with tax consequences; rolling one into a refinance moves a balance
between two liabilities. Both belong to somebody who meant them. The tests check
that the response says which entry is still owed, because an omission nobody
notices is worse than a refusal.

THE TENOR CHECK IS BORROWED, NOT REBUILT. `create_note_payable(related_asset=…)`
delegates to `link_asset_to_note`, so a note and an asset that disagree about
when they end are refused by the same code that refuses it from the other
direction — and the note and the link are one transaction.
"""

from .fixtures import (
	ASSET_CATEGORY,
	BANK,
	BANK_ACCOUNT,
	INTEREST_EXPENSE,
	MAIN,
	MAIN_ABBR,
	NOTES_PAYABLE,
	OTHER,
	V8TestCase,
	cost_center,
)
from .harness import frappe

CASH = f"1100 - Cash - {MAIN_ABBR}"
PAYABLES = f"2100 - Accounts Payable - {MAIN_ABBR}"
SUPPLIES = f"5100 - Office Supplies - {MAIN_ABBR}"
LIABILITIES_ROOT = f"Source of Funds (Liabilities) - {MAIN_ABBR}"

SORTER = "Umpqua Bank - GP Graders Automatic Defect Sorter"
SORTER_DOC = f"{SORTER} - {MAIN_ABBR}"

ALL_ON = {
	"allow_create_note_payable": 1,
	"allow_record_loan_payment": 1,
	"allow_list_notes_payable": 1,
	"allow_close_note_payable": 1,
	"allow_create_asset": 1,
	"allow_link_asset_to_note": 1,
	"allow_depreciation_note_alignment_check": 1,
}


class NoteTestCase(V8TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def payload(self, **overrides):
		values = {
			"borrower": MAIN,
			"note_name": SORTER,
			"lender": "Umpqua Bank",
			"principal_original": 120000,
			"origination_date": "2026-01-01",
			"maturity_date": "2027-01-01",
			"interest_rate": 6.5,
			"payment_frequency": "Monthly",
			"linked_gl_account": NOTES_PAYABLE,
			"interest_expense_account": INTEREST_EXPENSE,
		}
		values.update(overrides)
		return {key: value for key, value in values.items() if value is not None}

	def create(self, **overrides):
		return self.tool_data("create_note_payable", self.payload(**overrides))

	def make_asset(self, **overrides):
		values = {
			"company": MAIN,
			"asset_name": "Defect Sorter",
			"item_code": "SORTER-1",
			"asset_category": ASSET_CATEGORY,
			"purchase_date": "2026-01-01",
			"purchase_amount": 120000,
			"useful_life_months": 12,
			"cost_center_allocation": [{"cost_center": cost_center("Main"), "percentage": 100}],
		}
		values.update(overrides)
		return self.tool_data("create_asset", values)


# ── create_note_payable ─────────────────────────────────────────────────────
class CreateNotePayable(NoteTestCase):
	def test_it_registers_the_note_and_names_it_after_itself(self):
		data = self.create()
		self.assertEqual(data["name"], SORTER_DOC)
		self.assertEqual(data["lender"], "Umpqua Bank")
		self.assertEqual(data["status"], "Active")
		self.assertTrue(frappe.db.exists("Note Payable", SORTER_DOC))

	def test_the_outstanding_balance_defaults_to_the_original_principal(self):
		data = self.create()
		self.assertEqual(data["principal_original"], 120000.0)
		self.assertEqual(data["principal_outstanding"], 120000.0)

	def test_a_note_taken_on_mid_term_keeps_the_balance_it_was_given(self):
		data = self.create(principal_outstanding=64500)
		self.assertEqual(data["principal_original"], 120000.0)
		self.assertEqual(data["principal_outstanding"], 64500.0)

	def test_the_tenor_is_reported(self):
		self.assertEqual(self.create()["tenor_months"], 12)

	def test_a_note_with_no_maturity_has_no_tenor(self):
		self.assertIsNone(self.create(maturity_date=None)["tenor_months"])

	def test_the_defaults_are_the_common_case(self):
		data = self.create()
		self.assertEqual(data["interest_type"], "Fixed")
		self.assertEqual(data["payment_frequency"], "Monthly")

	def test_it_says_the_balance_here_is_not_the_ledgers(self):
		data = self.create()
		self.assertIn("convenience figure", data["note"])
		self.assertIn(NOTES_PAYABLE, data["note"])

	def test_a_note_with_no_gl_account_says_so_loudly(self):
		data = self.create(linked_gl_account=None, interest_expense_account=None)
		self.assertIn("nothing on this record ties back to the ledger", data["note"])

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_note_payable", self.payload())
		self.assertIn("allow_create_note_payable", message)
		self.assertFalse(frappe.db.exists("Note Payable", SORTER_DOC))


class CreateNoteRefusals(NoteTestCase):
	def test_the_same_note_twice_for_one_borrower_is_refused(self):
		self.create()
		message = self.tool_error("create_note_payable", self.payload())
		self.assertIn("already has a note called", message)
		self.assertEqual(frappe.db.count("Note Payable", {"note_name": SORTER}), 1)

	def test_the_same_name_for_another_borrower_is_fine(self):
		self.create()
		data = self.create(borrower=OTHER, linked_gl_account=None, interest_expense_account=None)
		self.assertEqual(data["borrower"], OTHER)

	def test_a_principal_of_nothing_is_refused(self):
		message = self.tool_error("create_note_payable", self.payload(principal_original=0))
		self.assertIn("A note for nothing is not a note", message)

	def test_a_negative_outstanding_balance_is_refused(self):
		message = self.tool_error("create_note_payable", self.payload(principal_outstanding=-1))
		self.assertIn("cannot be negative", message)

	def test_maturity_before_origination_is_refused(self):
		message = self.tool_error("create_note_payable", self.payload(maturity_date="2025-06-01"))
		self.assertIn("before origination_date", message)

	def test_zero_interest_with_a_rate_is_a_contradiction(self):
		message = self.tool_error("create_note_payable", self.payload(interest_type="Zero"))
		self.assertIn("One of the two is wrong", message)

	def test_a_zero_interest_family_note_is_perfectly_fine(self):
		data = self.create(interest_type="Zero", interest_rate=0, lender="Ed Martin")
		self.assertEqual(data["interest_type"], "Zero")
		self.assertEqual(data["interest_rate"], 0.0)

	def test_an_invented_interest_type_is_refused_with_the_options(self):
		message = self.tool_error("create_note_payable", self.payload(interest_type="Floating"))
		self.assertIn("Fixed", message)
		self.assertIn("Variable", message)

	def test_a_rate_over_a_hundred_is_refused(self):
		message = self.tool_error("create_note_payable", self.payload(interest_rate=150))
		self.assertIn("between 0 and 100", message)

	def test_a_liability_account_that_is_a_payable_is_refused(self):
		"""A note's principal booked to a Payable-typed account shows up as a
		supplier balance that never ages out."""
		message = self.tool_error("create_note_payable", self.payload(linked_gl_account=PAYABLES))
		self.assertIn("account_type 'Payable'", message)
		self.assertIn("never ages out", message)

	def test_a_gl_account_that_is_not_a_liability_is_refused(self):
		message = self.tool_error("create_note_payable", self.payload(linked_gl_account=CASH))
		self.assertIn("has to be Liability", message)

	def test_an_interest_account_that_is_not_an_expense_is_refused(self):
		message = self.tool_error("create_note_payable", self.payload(interest_expense_account=NOTES_PAYABLE))
		self.assertIn("has to be Expense", message)

	def test_it_cannot_be_created_already_closed(self):
		message = self.tool_error("create_note_payable", self.payload(status="Paid Off"))
		self.assertIn("cannot be created already closed", message)
		self.assertIn("close_note_payable", message)


class NoteSecuredOnAnAsset(NoteTestCase):
	def test_a_matching_tenor_links_the_asset_profile_back(self):
		asset = self.make_asset()
		data = self.create(related_asset=asset["asset"])
		self.assertEqual(data["related_asset"], asset["asset"])
		self.assertEqual(data["asset_link"]["linked_note"], SORTER_DOC)
		self.assertEqual(data["asset_link"]["linked_note_doctype"], "Note Payable")
		self.assertEqual(data["asset_link"]["note_tenor_months"], 12)
		self.assertTrue(data["asset_link"]["tenor_enforced"])

	def test_the_alignment_check_can_then_see_the_pair(self):
		asset = self.make_asset()
		self.create(related_asset=asset["asset"])
		# `as_of` on a month boundary: remaining months are counted in whole
		# months from either end, so mid-month the two sides legitimately differ
		# by one and the check says so. That is the existing tool's arithmetic,
		# not something the link changed.
		report = self.tool_data("depreciation_note_alignment_check", {"company": MAIN, "as_of": "2026-07-01"})
		self.assertEqual(report["checked"], 1)
		self.assertEqual(report["diverged_count"], 0)
		self.assertEqual(report["assets"][0]["linked_note"], SORTER_DOC)
		self.assertEqual(report["assets"][0]["linked_note_doctype"], "Note Payable")

	def test_a_tenor_that_disagrees_with_the_asset_is_refused(self):
		asset = self.make_asset()
		message = self.tool_error(
			"create_note_payable", self.payload(related_asset=asset["asset"], maturity_date="2027-07-01")
		)
		self.assertIn("divergence", message)

	def test_a_refused_link_leaves_no_note_behind(self):
		"""The note and the link are one transaction."""
		asset = self.make_asset()
		self.tool_error(
			"create_note_payable", self.payload(related_asset=asset["asset"], maturity_date="2027-07-01")
		)
		self.assertFalse(frappe.db.exists("Note Payable", SORTER_DOC))

	def test_a_deliberate_divergence_can_be_accepted(self):
		asset = self.make_asset()
		data = self.create(
			related_asset=asset["asset"], maturity_date="2027-07-01", enforce_asset_tenor=False
		)
		self.assertEqual(data["asset_link"]["delta_months"], -6)
		self.assertFalse(data["asset_link"]["tenor_enforced"])

	def test_an_asset_this_site_does_not_have_is_refused(self):
		message = self.tool_error("create_note_payable", self.payload(related_asset="Nonesuch"))
		self.assertIn("no Asset named or called", message)


# ── record_loan_payment ─────────────────────────────────────────────────────
class RecordLoanPayment(NoteTestCase):
	def setUp(self):
		super().setUp()
		self.create()

	def pay(self, **overrides):
		values = {
			"note": SORTER_DOC,
			"payment_date": "2026-02-01",
			"total_amount": 10650,
			"principal_split": 10000,
			"interest_split": 650,
			"offset_bank_account": BANK_ACCOUNT,
		}
		values.update(overrides)
		return self.tool_data("record_loan_payment", values)

	def test_the_entry_splits_principal_from_interest(self):
		data = self.pay()
		lines = {line["account"]: line for line in data["lines"]}
		self.assertEqual(lines[NOTES_PAYABLE]["debit"], 10000.0)
		self.assertEqual(lines[INTEREST_EXPENSE]["debit"], 650.0)
		self.assertEqual(lines[BANK]["credit"], 10650.0)

	def test_the_balance_comes_down_by_the_principal_only(self):
		data = self.pay()
		self.assertEqual(data["principal_outstanding_before"], 120000.0)
		self.assertEqual(data["principal_outstanding_after"], 110000.0)
		self.assertEqual(
			float(frappe.db.get_value("Note Payable", SORTER_DOC, "principal_outstanding")), 110000.0
		)

	def test_the_payment_lands_in_the_notes_history(self):
		data = self.pay()
		events = frappe.get_doc("Note Payable", SORTER_DOC).get("payment_events")
		self.assertEqual(len(events), 1)
		self.assertEqual(events[0]["event_type"], "Payment")
		self.assertEqual(events[0]["journal_entry"], data["journal_entry"])
		self.assertEqual(float(events[0]["principal_outstanding_after"]), 110000.0)

	def test_the_journal_entry_is_only_ever_a_draft(self):
		data = self.pay()
		entry = frappe.get_doc("Journal Entry", data["journal_entry"])
		self.assertEqual(int(entry.get("docstatus") or 0), 0)
		self.assertIn("submit_journal_entry", data["next_step"])

	def test_it_admits_the_record_and_the_ledger_now_disagree(self):
		data = self.pay()
		self.assertIn("disagree by 10000.0", data["note_text"])

	def test_interest_is_derived_when_only_the_principal_is_given(self):
		data = self.pay(interest_split=None)
		self.assertEqual(data["interest_split"], 650.0)

	def test_principal_is_derived_when_only_the_interest_is_given(self):
		data = self.pay(principal_split=None)
		self.assertEqual(data["principal_split"], 10000.0)

	def test_a_payment_that_is_all_principal_is_allowed_when_said_explicitly(self):
		data = self.pay(total_amount=10000, principal_split=10000, interest_split=0)
		self.assertEqual(data["interest_split"], 0.0)
		self.assertEqual(len(data["lines"]), 2)

	def test_a_bank_account_record_is_carried_onto_the_line(self):
		data = self.pay()
		self.assertEqual(data["accounts_used"]["offset_bank_account"], BANK_ACCOUNT)
		entry = frappe.get_doc("Journal Entry", data["journal_entry"])
		credit = next(line for line in entry.get("accounts") if line.get("credit"))
		self.assertEqual(credit["bank_account"], BANK_ACCOUNT)

	def test_a_plain_gl_account_works_too(self):
		data = self.pay(offset_bank_account=CASH)
		self.assertIsNone(data["accounts_used"]["offset_bank_account"])
		self.assertEqual(data["accounts_used"]["offset_account"], CASH)

	def test_several_payments_accumulate(self):
		self.pay()
		data = self.pay(payment_date="2026-03-01")
		self.assertEqual(data["principal_outstanding_after"], 100000.0)
		self.assertEqual(data["payment_count"], 2)

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error(
			"record_loan_payment",
			{
				"note": SORTER_DOC,
				"payment_date": "2026-02-01",
				"total_amount": 10650,
				"principal_split": 10000,
				"offset_bank_account": BANK_ACCOUNT,
			},
		)
		self.assertIn("allow_record_loan_payment", message)


class LoanPaymentRefusals(NoteTestCase):
	def setUp(self):
		super().setUp()
		self.create()

	def refuse(self, **overrides):
		values = {
			"note": SORTER_DOC,
			"payment_date": "2026-02-01",
			"total_amount": 10650,
			"principal_split": 10000,
			"interest_split": 650,
			"offset_bank_account": BANK_ACCOUNT,
		}
		values.update(overrides)
		return self.tool_error(
			"record_loan_payment", {key: value for key, value in values.items() if value is not None}
		)

	def test_a_split_that_does_not_add_up_is_refused(self):
		message = self.refuse(interest_split=1000)
		self.assertIn("not total_amount", message)
		self.assertEqual(frappe.db.count("Journal Entry", {"posting_date": "2026-02-01"}), 0)

	def test_no_split_at_all_is_refused_with_the_reason_it_matters(self):
		message = self.refuse(principal_split=None, interest_split=None)
		self.assertIn("understates the year's interest expense", message)

	def test_more_principal_than_is_owed_is_refused(self):
		message = self.refuse(total_amount=200000, principal_split=200000, interest_split=0)
		self.assertIn("more than the 120000.0 still outstanding", message)

	def test_a_payment_before_the_note_existed_is_refused(self):
		message = self.refuse(payment_date="2025-12-01")
		self.assertIn("before the note was originated", message)

	def test_interest_with_nowhere_to_put_it_is_refused(self):
		frappe.db.set_value("Note Payable", SORTER_DOC, "interest_expense_account", "")
		message = self.refuse()
		self.assertIn("no expense account to debit", message)

	def test_a_note_that_has_been_closed_takes_nothing_further(self):
		self.tool_data(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Written Off",
				"disposition_date": "2026-06-01",
				"narrative": "Forgiven under the 2026 family settlement",
			},
		)
		message = self.refuse(payment_date="2026-07-01")
		self.assertIn("Written Off", message)
		self.assertIn("nothing further is recorded", message)

	def test_an_unknown_note_is_refused_with_the_ones_that_exist(self):
		message = self.tool_error(
			"record_loan_payment",
			{
				"note": "Nonesuch",
				"payment_date": "2026-02-01",
				"total_amount": 100,
				"principal_split": 100,
				"offset_bank_account": BANK_ACCOUNT,
			},
		)
		self.assertIn("no Note Payable called 'Nonesuch'", message)
		self.assertIn(SORTER, message)

	def test_an_offset_account_that_is_an_expense_is_refused(self):
		message = self.refuse(offset_bank_account=SUPPLIES)
		self.assertIn("has to be Asset or Liability", message)


# ── list_notes_payable ──────────────────────────────────────────────────────
class ListNotesPayable(NoteTestCase):
	def setUp(self):
		super().setUp()
		self.create()
		self.create(
			note_name="Ed Martin note",
			lender="Estate of Ed Martin",
			principal_original=250000,
			principal_outstanding=180000,
			origination_date="2003-05-01",
			maturity_date=None,
			interest_type="Zero",
			interest_rate=0,
			payment_frequency="Balloon",
			interest_expense_account=None,
		)

	def test_it_lists_both_with_their_balances(self):
		data = self.tool_data("list_notes_payable", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["active_count"], 2)
		self.assertEqual(data["total_original_principal_active"], 370000.0)
		self.assertEqual(data["total_outstanding_active"], 300000.0)

	def test_the_next_payment_is_projected_from_the_frequency(self):
		data = self.tool_data("list_notes_payable", {"company": MAIN})
		sorter = next(note for note in data["notes"] if note["note_name"] == SORTER)
		self.assertEqual(sorter["next_payment_date"], "2026-02-01")

	def test_the_projection_follows_the_last_payment_recorded(self):
		self.tool_data(
			"record_loan_payment",
			{
				"note": SORTER_DOC,
				"payment_date": "2026-03-01",
				"total_amount": 10650,
				"principal_split": 10000,
				"interest_split": 650,
				"offset_bank_account": BANK_ACCOUNT,
			},
		)
		data = self.tool_data("list_notes_payable", {"company": MAIN})
		sorter = next(note for note in data["notes"] if note["note_name"] == SORTER)
		self.assertEqual(sorter["last_payment_date"], "2026-03-01")
		self.assertEqual(sorter["next_payment_date"], "2026-04-01")
		self.assertEqual(sorter["payment_count"], 1)

	def test_a_balloon_note_projects_nothing_but_its_maturity(self):
		data = self.tool_data("list_notes_payable", {"company": MAIN})
		martin = next(note for note in data["notes"] if note["note_name"] == "Ed Martin note")
		self.assertIsNone(martin["next_payment_date"])

	def test_the_projection_never_runs_past_maturity(self):
		self.tool_data(
			"record_loan_payment",
			{
				"note": SORTER_DOC,
				"payment_date": "2026-12-15",
				"total_amount": 10650,
				"principal_split": 10000,
				"interest_split": 650,
				"offset_bank_account": BANK_ACCOUNT,
			},
		)
		data = self.tool_data("list_notes_payable", {"company": MAIN})
		sorter = next(note for note in data["notes"] if note["note_name"] == SORTER)
		self.assertEqual(sorter["next_payment_date"], "2027-01-01")

	def test_it_can_be_filtered_by_status(self):
		data = self.tool_data("list_notes_payable", {"company": MAIN, "status": "Paid Off"})
		self.assertEqual(data["count"], 0)

	def test_closed_notes_can_be_left_out(self):
		self.tool_data(
			"close_note_payable",
			{
				"note": "Ed Martin note",
				"company": MAIN,
				"disposition": "Written Off",
				"disposition_date": "2026-06-30",
				"narrative": "Waived under the 2026 family settlement deed",
			},
		)
		with_closed = self.tool_data("list_notes_payable", {"company": MAIN})
		self.assertEqual(with_closed["count"], 2)
		self.assertEqual(with_closed["active_count"], 1)
		without = self.tool_data("list_notes_payable", {"company": MAIN, "include_closed": False})
		self.assertEqual(without["count"], 1)

	def test_it_warns_that_the_balances_are_not_the_ledgers(self):
		data = self.tool_data("list_notes_payable", {"company": MAIN})
		self.assertIn("not the balance of the linked GL account", data["note"])
		self.assertIn("get_account_balance", data["note"])

	def test_it_is_a_read_tool_and_is_on_by_default(self):
		from erpnext_mcp import registry

		self.configure(enabled=1)
		self.assertFalse(registry.TOOLS["list_notes_payable"]["mutating"])
		self.assertEqual(self.tool_data("list_notes_payable", {"company": MAIN})["count"], 2)


# ── close_note_payable ──────────────────────────────────────────────────────
class CloseNotePayable(NoteTestCase):
	def setUp(self):
		super().setUp()
		self.create()

	def close(self, **overrides):
		values = {
			"note": SORTER_DOC,
			"disposition": "Written Off",
			"disposition_date": "2026-06-30",
			"narrative": "Waived under the 2026 family settlement deed",
		}
		values.update(overrides)
		return self.tool_data("close_note_payable", values)

	def test_it_sets_the_status_and_records_the_event(self):
		data = self.close()
		self.assertEqual(data["status"], "Written Off")
		self.assertEqual(data["principal_outstanding"], 0.0)
		self.assertEqual(data["principal_outstanding_at_close"], 120000.0)
		self.assertEqual(data["events"][-1]["event_type"], "Written Off")
		self.assertEqual(data["events"][-1]["narrative"], "Waived under the 2026 family settlement deed")

	def test_it_writes_no_journal_entry_and_says_which_one_is_still_owed(self):
		before = frappe.db.count("Journal Entry")
		data = self.close()
		self.assertEqual(frappe.db.count("Journal Entry"), before)
		self.assertIsNone(data["journal_entry"])
		self.assertIn("NO journal entry was written", data["note"])
		self.assertIn("usually taxable", data["note"])
		self.assertIn(NOTES_PAYABLE, data["note"])

	def test_paid_off_with_a_balance_still_showing_is_refused(self):
		message = self.tool_error(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Paid Off",
				"disposition_date": "2027-01-01",
				"narrative": "Final payment cleared on the maturity date",
			},
		)
		self.assertIn("still shows 120000.0 outstanding", message)
		self.assertIn("record_loan_payment", message)
		self.assertIn("zero_remaining_balance", message)

	def test_a_stale_balance_can_be_written_down_deliberately(self):
		data = self.close(
			disposition="Paid Off",
			disposition_date="2027-01-01",
			narrative="Paid off in 2019; this record was created from the file afterwards",
			zero_remaining_balance=True,
		)
		self.assertTrue(data["balance_zeroed_without_payment"])
		self.assertEqual(data["events"][0]["event_type"], "Adjustment")
		self.assertEqual(data["events"][0]["principal_component"], 120000.0)

	def test_paid_off_after_the_balance_reaches_zero_is_clean(self):
		self.tool_data(
			"record_loan_payment",
			{
				"note": SORTER_DOC,
				"payment_date": "2026-06-01",
				"total_amount": 120000,
				"principal_split": 120000,
				"interest_split": 0,
				"offset_bank_account": BANK_ACCOUNT,
			},
		)
		data = self.close(
			disposition="Paid Off", disposition_date="2026-06-01", narrative="Cleared in full on 1 June"
		)
		self.assertEqual(data["status"], "Paid Off")
		self.assertFalse(data["balance_zeroed_without_payment"])
		self.assertIn("probably nothing left to post", data["note"])

	def test_a_refinance_can_name_its_successor(self):
		self.create(note_name="Umpqua Bank - Sorter refinance 2027", principal_original=64000)
		successor = f"Umpqua Bank - Sorter refinance 2027 - {MAIN_ABBR}"
		data = self.close(
			disposition="Refinanced",
			disposition_date="2027-01-01",
			narrative="Rolled into the 2027 facility at 5.9%",
			superseded_by=successor,
		)
		self.assertEqual(data["superseded_by"], successor)
		self.assertIn(successor, data["next_step"])

	def test_a_refinance_without_a_successor_says_to_add_one(self):
		data = self.close(
			disposition="Refinanced",
			disposition_date="2027-01-01",
			narrative="Rolled into the 2027 facility at 5.9%",
		)
		self.assertIn("create_note_payable", data["next_step"])
		self.assertIn("belongs to the successor note", data["note"])

	def test_a_successor_on_anything_but_a_refinance_is_refused(self):
		self.create(note_name="Another note", principal_original=1000)
		message = self.tool_error(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Written Off",
				"disposition_date": "2026-06-30",
				"narrative": "Waived under the 2026 family settlement deed",
				"superseded_by": f"Another note - {MAIN_ABBR}",
			},
		)
		self.assertIn("only makes sense for a Refinanced disposition", message)

	def test_closing_twice_is_refused(self):
		self.close()
		message = self.tool_error(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Paid Off",
				"disposition_date": "2026-07-01",
				"narrative": "Trying to close it again for some reason",
			},
		)
		self.assertIn("already Written Off", message)

	def test_an_invented_disposition_is_refused_with_the_three(self):
		message = self.tool_error(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Forgotten",
				"disposition_date": "2026-07-01",
				"narrative": "Nobody can find the paperwork any more",
			},
		)
		self.assertIn("Paid Off, Refinanced, Written Off", message)
		self.assertIn("Superseded", message)

	def test_a_placeholder_narrative_is_refused(self):
		message = self.tool_error(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Written Off",
				"disposition_date": "2026-06-30",
				"narrative": "done",
			},
		)
		self.assertIn("real explanation", message)

	def test_a_disposition_before_origination_is_refused(self):
		message = self.tool_error(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Written Off",
				"disposition_date": "2001-01-01",
				"narrative": "Waived under the 2026 family settlement deed",
			},
		)
		self.assertIn("before the note was originated", message)

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error(
			"close_note_payable",
			{
				"note": SORTER_DOC,
				"disposition": "Written Off",
				"disposition_date": "2026-06-30",
				"narrative": "Waived under the 2026 family settlement deed",
			},
		)
		self.assertIn("allow_close_note_payable", message)
		self.assertEqual(frappe.db.get_value("Note Payable", SORTER_DOC, "status"), "Active")
