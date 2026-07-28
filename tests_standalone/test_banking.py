# SPDX-License-Identifier: MIT
"""The record that maps a real bank account onto an account on the chart.

Three things these tests are really about.

THE ROOT-TYPE AND ACCOUNT-TYPE CHECKS. A Bank Account pointed at an Income
account is not caught by ERPNext, and produces a reconciliation screen where
every deposit increases revenue. An Asset account without `account_type = Bank`
saves fine and then cannot be reconciled at all, because ERPNext's own
reconciliation tool selects on that flag. Both are refusals here, and both have
tests, because a validation that silently stops working is indistinguishable
from one that was never written.

TWO DOCTYPES, ONE CALL, ONE TRANSACTION. ERPNext splits the institution from the
account at it. A caller naming a bank this site has never seen means to open the
account, not to be told to create a Bank first — so the Bank is created, and a
failure anywhere after that has to leave neither.

THE DOCNAME. ERPNext names a Bank Account `"<account_name> - <bank>"`, and that
string is what somebody copies into a bank feed's configuration. The double
reproduces the rule (`harness.BankAccountDocument`), so these tests assert on the
key a caller actually gets.
"""

from .fixtures import (
	BANK_ACCOUNT,
	BANK_SAVINGS,
	CREDIT_CARD,
	EQUIPMENT,
	MAIN,
	MAIN_ABBR,
	OTHER,
	OTHER_ABBR,
	V8TestCase,
)
from .harness import frappe

BANK_CHECKING = f"1110 - Bank Checking - {MAIN_ABBR}"
CURRENT_ASSETS = f"1000 - Current Assets - {MAIN_ABBR}"
SALES = f"4100 - Sales - {MAIN_ABBR}"

ALL_ON = {"allow_create_bank_account": 1}


class BankAccountTestCase(V8TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def payload(self, **overrides):
		values = {
			"company": MAIN,
			"account_name": "Savings",
			"bank_name": "Wells Fargo",
			"account": BANK_SAVINGS,
			"account_no": "3158",
		}
		values.update(overrides)
		return values


class CreateBankAccount(BankAccountTestCase):
	def test_it_creates_both_documents_and_names_them_the_way_erpnext_does(self):
		data = self.tool_data("create_bank_account", self.payload())
		self.assertEqual(data["name"], "Savings - Wells Fargo")
		self.assertEqual(data["bank"], "Wells Fargo")
		self.assertTrue(data["bank_created"])
		self.assertTrue(frappe.db.exists("Bank", "Wells Fargo"))
		self.assertTrue(frappe.db.exists("Bank Account", "Savings - Wells Fargo"))

	def test_an_institution_this_site_already_has_is_reused(self):
		data = self.tool_data("create_bank_account", self.payload(bank_name="Example Bank"))
		self.assertFalse(data["bank_created"])
		self.assertEqual(frappe.db.count("Bank", {"bank_name": "Example Bank"}), 1)

	def test_the_gl_account_is_stored_and_explained(self):
		data = self.tool_data("create_bank_account", self.payload())
		self.assertEqual(data["account"], BANK_SAVINGS)
		self.assertIn("holds no balance", data["note"])
		self.assertIn(BANK_SAVINGS, data["note"])

	def test_it_defaults_to_a_company_account(self):
		data = self.tool_data("create_bank_account", self.payload())
		self.assertTrue(data["is_company_account"])
		self.assertFalse(data["disabled"])

	def test_account_no_lands_in_erpnexts_own_column(self):
		data = self.tool_data("create_bank_account", self.payload(account_no="••3158"))
		self.assertEqual(data["bank_account_no"], "••3158")

	def test_bank_account_no_is_the_same_field_under_the_other_name(self):
		data = self.tool_data(
			"create_bank_account", self.payload(account_no=None, bank_account_no="90012345")
		)
		self.assertEqual(data["bank_account_no"], "90012345")

	def test_two_different_account_numbers_are_refused_rather_than_one_picked(self):
		message = self.tool_error(
			"create_bank_account", self.payload(account_no="3158", bank_account_no="6030")
		)
		self.assertIn("both set and differ", message)
		self.assertFalse(frappe.db.exists("Bank Account", "Savings - Wells Fargo"))

	def test_the_iban_is_kept(self):
		data = self.tool_data("create_bank_account", self.payload(iban="GB33BUKB20201555555555"))
		self.assertEqual(data["iban"], "GB33BUKB20201555555555")

	def test_it_can_be_created_already_disabled(self):
		data = self.tool_data("create_bank_account", self.payload(disabled=True))
		self.assertTrue(data["disabled"])

	def test_it_says_to_wire_the_feed_before_the_first_sync(self):
		data = self.tool_data("create_bank_account", self.payload())
		self.assertIn("before its first sync", data["next_step"])

	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_bank_account", self.payload())
		self.assertIn("allow_create_bank_account", message)
		self.assertFalse(frappe.db.exists("Bank Account", "Savings - Wells Fargo"))


class BankAccountRefusals(BankAccountTestCase):
	def test_a_company_account_without_a_gl_account_is_refused(self):
		message = self.tool_error("create_bank_account", self.payload(account=None))
		self.assertIn("account is required for a company bank account", message)

	def test_an_income_account_is_refused_by_root_type(self):
		message = self.tool_error("create_bank_account", self.payload(account=SALES))
		self.assertIn("Income account", message)
		self.assertIn("a credit card is a Liability", message)

	def test_an_asset_account_that_is_not_a_bank_account_is_refused_by_type(self):
		"""1710 Equipment is an Asset, and a Fixed Asset. ERPNext's reconciliation
		selects on account_type, so this would save and never reconcile."""
		message = self.tool_error("create_bank_account", self.payload(account=EQUIPMENT))
		self.assertIn("account_type is Fixed Asset", message)
		self.assertIn("update_account", message)

	def test_a_credit_card_liability_is_accepted(self):
		data = self.tool_data(
			"create_bank_account",
			self.payload(account=CREDIT_CARD, account_name="Purchasing Card", bank_name="Umpqua Bank"),
		)
		self.assertEqual(data["account"], CREDIT_CARD)

	def test_a_group_account_is_refused(self):
		message = self.tool_error("create_bank_account", self.payload(account=CURRENT_ASSETS))
		self.assertIn("group account", message)

	def test_a_disabled_account_is_refused(self):
		frappe.db.set_value("Account", BANK_SAVINGS, "disabled", 1)
		message = self.tool_error("create_bank_account", self.payload())
		self.assertIn("is disabled", message)

	def test_an_account_in_another_company_is_refused(self):
		message = self.tool_error(
			"create_bank_account", self.payload(company=OTHER, account=BANK_SAVINGS)
		)
		self.assertIn("belongs to company", message)

	def test_a_duplicate_account_name_in_the_same_company_is_refused(self):
		message = self.tool_error("create_bank_account", self.payload(account_name="Operating"))
		self.assertIn("already has a Bank Account called 'Operating'", message)
		self.assertIn("mask is the usual distinguisher", message)

	def test_the_same_account_name_in_another_company_is_fine(self):
		data = self.tool_data(
			"create_bank_account",
			{
				"company": OTHER,
				"account_name": "Operating",
				"bank_name": "Example Bank",
				"account": f"1110 - Bank Checking - {OTHER_ABBR}",
			},
		)
		self.assertEqual(data["company"], OTHER)

	def test_a_bank_name_frappe_would_refuse_as_a_docname_is_refused_here(self):
		message = self.tool_error("create_bank_account", self.payload(bank_name="Wells <Fargo>"))
		self.assertIn("validate_name", message)
		self.assertFalse(frappe.db.exists("Bank Account", "Savings - Wells <Fargo>"))

	def test_a_bank_name_starting_with_new_is_refused(self):
		message = self.tool_error("create_bank_account", self.payload(bank_name="New Bank Ltd"))
		self.assertIn("reserves for unsaved documents", message)

	def test_a_party_on_a_company_account_is_a_contradiction(self):
		message = self.tool_error(
			"create_bank_account", self.payload(party_type="Supplier", party="Anyone")
		)
		self.assertIn("says the opposite", message)

	def test_a_party_without_its_type_is_refused(self):
		message = self.tool_error(
			"create_bank_account", self.payload(is_company_account=False, party="Anyone")
		)
		self.assertIn("party and party_type go together", message)

	def test_a_party_doctype_this_site_does_not_have_is_refused(self):
		message = self.tool_error(
			"create_bank_account",
			self.payload(is_company_account=False, party_type="Nonesuch", party="Anyone"),
		)
		self.assertIn("no DocType named 'Nonesuch'", message)

	def test_a_third_party_account_needs_no_gl_account(self):
		data = self.tool_data(
			"create_bank_account",
			self.payload(account=None, is_company_account=False, account_name="Their Account"),
		)
		self.assertFalse(data["is_company_account"])
		self.assertIsNone(data["account"])
		self.assertIn("cannot be reconciled to the ledger", data["note"])

	def test_a_failure_leaves_no_orphan_bank_behind(self):
		"""Both documents are one transaction. A Bank created for an account that
		was then refused is an institution nobody opened an account at."""
		self.tool_error("create_bank_account", self.payload(bank_name="Columbia River Bank", account=SALES))
		self.assertFalse(frappe.db.exists("Bank", "Columbia River Bank"))


class SharedGLAccount(BankAccountTestCase):
	def test_a_second_bank_account_on_the_same_gl_account_warns_rather_than_refuses(self):
		"""Legitimate for a sweep arrangement, a mistake everywhere else — and not
		something this app gets to decide."""
		data = self.tool_data(
			"create_bank_account", self.payload(account_name="Operating Mirror", account=BANK_CHECKING)
		)
		self.assertIn("warning", data)
		self.assertIn(BANK_ACCOUNT, data["warning"])
		self.assertTrue(frappe.db.exists("Bank Account", "Operating Mirror - Wells Fargo"))

	def test_the_first_bank_account_on_an_account_gets_no_warning(self):
		data = self.tool_data("create_bank_account", self.payload())
		self.assertNotIn("warning", data)
