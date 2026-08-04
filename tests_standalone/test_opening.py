# SPDX-License-Identifier: MIT
"""Opening balances: the amounts that were true before day one.

Three things these tests are really about.

THE PLUG IS COMPUTED, NOT SUPPLIED. Every historical fact brought onto a set of
books has a real side and an equity side, and a caller who works the equity side
out for itself gets it wrong by a few cents on the third event — after which the
ledger never balances again. So the tool takes the real side only, and the tests
assert on the arithmetic rather than on the fact that a journal entry appeared.

THE FLAGS. `is_opening` and the `Opening Entry` voucher type are what keep these
amounts out of the period's activity in every report that separates the two.
Nothing warns you when they are missing; the P&L simply reads as though the
company earned its opening equity in January. Both are set conditionally on the
site's own meta, so both are tested against a double that carries ERPNext's real
option list.

THE EQUITY ACCOUNT IS FOUND, NOT GUESSED. 3300 by number, then a leaf Equity
account named after opening balances, and anything other than exactly one match
is a refusal with the candidates listed. Posting a plug to whichever equity
account sorted first is an error that surfaces months later as an equity
statement nobody can explain, so the ambiguous and the missing cases both have
tests.
"""

from .fixtures import (
	EQUIPMENT,
	MAIN,
	MAIN_ABBR,
	MEMBER_CAPITAL,
	MEMBER_ONE,
	OPENING_EQUITY,
	OTHER,
	V8TestCase,
	cost_center,
)
from .harness import STORE, frappe

CASH = f"1100 - Cash - {MAIN_ABBR}"
CURRENT_ASSETS = f"1000 - Current Assets - {MAIN_ABBR}"
FIELD_WORK = f"110 - Field Work - {MAIN_ABBR}"
NOTES_PAYABLE = f"2310 - Notes Payable - {MAIN_ABBR}"

ALL_ON = {"allow_set_opening_balance": 1}


class OpeningBalanceTestCase(V8TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def payload(self, entries=None, **overrides):
		values = {
			"company": MAIN,
			"posting_date": "2026-01-01",
			"user_remark": "Equipment transferred from PFI on dissolution",
			"entries": entries
			if entries is not None
			else [{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 52650}],
		}
		values.update(overrides)
		return values

	def entry(self, name):
		return frappe.get_doc("Journal Entry", name)


class TheEquityPlug(OpeningBalanceTestCase):
	def test_a_single_debit_is_balanced_by_a_credit_to_opening_equity(self):
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertEqual(data["opening_equity_account"], OPENING_EQUITY)
		self.assertEqual(data["opening_equity_side"], "credit")
		self.assertEqual(data["opening_equity_amount"], 52650.0)
		self.assertEqual(data["total_debit"], data["total_credit"])
		self.assertEqual(data["line_count"], 2)

	def test_a_single_credit_is_balanced_by_a_debit(self):
		data = self.tool_data(
			"set_opening_balance",
			self.payload([{"account": NOTES_PAYABLE, "dr_or_cr": "cr", "amount": 1080000}]),
		)
		self.assertEqual(data["opening_equity_side"], "debit")
		self.assertEqual(data["opening_equity_amount"], 1080000.0)

	def test_the_plug_is_the_difference_across_several_entries(self):
		data = self.tool_data(
			"set_opening_balance",
			self.payload(
				[
					{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 52650},
					{"account": CASH, "dr_or_cr": "dr", "amount": 1700000},
					{"account": NOTES_PAYABLE, "dr_or_cr": "cr", "amount": 200000},
				]
			),
		)
		self.assertEqual(data["entered_debit"], 1752650.0)
		self.assertEqual(data["entered_credit"], 200000.0)
		self.assertEqual(data["balancing_difference"], 1552650.0)
		self.assertEqual(data["opening_equity_amount"], 1552650.0)
		self.assertEqual(data["line_count"], 4)

	def test_entries_that_already_balance_get_no_plug_at_all(self):
		data = self.tool_data(
			"set_opening_balance",
			self.payload(
				[
					{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 52650},
					{"account": NOTES_PAYABLE, "dr_or_cr": "cr", "amount": 52650},
				]
			),
		)
		self.assertIsNone(data["opening_equity_side"])
		self.assertIsNone(data["opening_equity_amount"])
		self.assertEqual(data["line_count"], 2)
		self.assertIn("no offsetting line was written", data["note"])

	def test_one_entry_that_balances_against_nothing_is_refused(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload([{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 0.001}]),
		)
		self.assertIn("balances against nothing", message)

	def test_the_journal_entry_actually_carries_the_plug(self):
		data = self.tool_data("set_opening_balance", self.payload())
		lines = self.entry(data["name"]).get("accounts")
		plug = [line for line in lines if line["account"] == OPENING_EQUITY]
		self.assertEqual(len(plug), 1)
		self.assertEqual(float(plug[0]["credit"]), 52650.0)


class FindingTheEquityAccount(OpeningBalanceTestCase):
	def test_it_finds_3300_by_number(self):
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertEqual(data["opening_equity_resolved_by"], "account_number 3300")

	def test_it_falls_back_to_a_name_match(self):
		frappe.db.set_value("Account", OPENING_EQUITY, "account_number", "3999")
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertEqual(data["opening_equity_resolved_by"], "account name match")

	def test_an_explicit_account_wins(self):
		data = self.tool_data("set_opening_balance", self.payload(opening_equity_account=MEMBER_CAPITAL))
		self.assertEqual(data["opening_equity_account"], MEMBER_CAPITAL)
		self.assertEqual(data["opening_equity_resolved_by"], "argument")

	def test_an_explicit_account_that_is_not_equity_is_refused(self):
		message = self.tool_error("set_opening_balance", self.payload(opening_equity_account=CASH))
		self.assertIn("equity by definition", message)

	def test_no_candidate_at_all_is_refused_with_the_account_to_create(self):
		frappe.db.set_value("Account", OPENING_EQUITY, "account_number", "3999")
		frappe.db.set_value("Account", OPENING_EQUITY, "account_name", "Retained Earnings")
		message = self.tool_error("set_opening_balance", self.payload())
		self.assertIn("could not find an Opening Balance Equity account", message)
		self.assertIn("create_account", message)
		self.assertIn(MEMBER_CAPITAL, message)

	def test_two_candidates_are_refused_rather_than_one_chosen(self):
		STORE.seed(
			"Account",
			[
				{
					"name": f"3350 - Opening Equity - {MAIN_ABBR}",
					"account_name": "Opening Equity",
					"account_number": "3350",
					"parent_account": f"Equity - {MAIN_ABBR}",
					"is_group": 0,
					"root_type": "Equity",
					"disabled": 0,
					"company": MAIN,
				}
			],
		)
		frappe.db.set_value("Account", OPENING_EQUITY, "account_number", "3999")
		message = self.tool_error("set_opening_balance", self.payload())
		self.assertIn("2 leaf Equity accounts could be", message)
		self.assertIn("opening_equity_account", message)


class OpeningEntryFlags(OpeningBalanceTestCase):
	def test_it_flags_the_entry_as_opening(self):
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertEqual(data["flags_set"]["is_opening"], "Yes")
		self.assertEqual(self.entry(data["name"]).get("is_opening"), "Yes")

	def test_it_uses_the_opening_entry_voucher_type_where_the_site_offers_it(self):
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertEqual(data["flags_set"]["voucher_type"], "Opening Entry")
		self.assertEqual(self.entry(data["name"]).get("voucher_type"), "Opening Entry")

	def test_a_site_without_that_option_gets_an_ordinary_voucher_type(self):
		from .harness import META

		META["Journal Entry"].get_field("voucher_type")["options"] = "Journal Entry\nBank Entry"
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertNotIn("voucher_type", data["flags_set"])
		self.assertEqual(data["flags_set"]["is_opening"], "Yes")

	def test_it_only_ever_produces_a_draft(self):
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertEqual(data["docstatus"], 0)
		self.assertEqual(int(self.entry(data["name"]).get("docstatus") or 0), 0)
		self.assertIn("submit_journal_entry", data["next_step"])


class EntryValidation(OpeningBalanceTestCase):
	def test_the_direction_words_a_model_will_send_are_all_accepted(self):
		for word in ("dr", "debit", "d", "DR", "Debit"):
			with self.subTest(word=word):
				data = self.tool_data(
					"set_opening_balance",
					self.payload([{"account": EQUIPMENT, "dr_or_cr": word, "amount": 100}]),
				)
				self.assertEqual(data["opening_equity_side"], "credit")

	def test_an_unreadable_direction_is_refused(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload([{"account": EQUIPMENT, "dr_or_cr": "positive", "amount": 100}]),
		)
		self.assertIn("dr_or_cr must say which side", message)

	def test_a_negative_amount_is_refused_rather_than_read_as_a_direction(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload([{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": -100}]),
		)
		self.assertIn("must be positive", message)
		self.assertIn("direction is dr_or_cr", message)

	def test_an_unsupported_field_is_refused_by_name(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload([{"account": EQUIPMENT, "debit": 100, "dr_or_cr": "dr", "amount": 100}]),
		)
		self.assertIn("unsupported field(s): debit", message)

	def test_a_group_account_is_refused(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload([{"account": CURRENT_ASSETS, "dr_or_cr": "dr", "amount": 100}]),
		)
		self.assertIn("entries[1]", message)
		self.assertIn("group account", message)

	def test_a_disabled_account_is_refused(self):
		frappe.db.set_value("Account", EQUIPMENT, "disabled", 1)
		message = self.tool_error("set_opening_balance", self.payload())
		self.assertIn("is disabled", message)

	def test_an_account_in_another_company_is_refused(self):
		message = self.tool_error("set_opening_balance", self.payload(company=OTHER))
		self.assertIn("belongs to company", message)

	def test_a_cost_center_is_carried_onto_the_line(self):
		data = self.tool_data(
			"set_opening_balance",
			self.payload(
				[{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 100, "cost_center": FIELD_WORK}]
			),
		)
		self.assertEqual(data["entries"][0]["cost_center"], FIELD_WORK)
		lines = self.entry(data["name"]).get("accounts")
		self.assertEqual(lines[0]["cost_center"], FIELD_WORK)

	def test_a_group_cost_center_is_refused(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload(
				[
					{
						"account": EQUIPMENT,
						"dr_or_cr": "dr",
						"amount": 100,
						"cost_center": cost_center("Operations"),
					}
				]
			),
		)
		self.assertIn("group cost center", message)

	def test_a_disabled_cost_center_is_refused(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload(
				[
					{
						"account": EQUIPMENT,
						"dr_or_cr": "dr",
						"amount": 100,
						"cost_center": cost_center("Retired Depot"),
					}
				]
			),
		)
		self.assertIn("disabled cost center", message)

	def test_a_dimension_is_written_onto_the_line(self):
		data = self.tool_data(
			"set_opening_balance",
			self.payload(
				[
					{
						"account": MEMBER_CAPITAL,
						"dr_or_cr": "cr",
						"amount": 100,
						"dimensions": {"member": MEMBER_ONE},
					}
				]
			),
		)
		lines = self.entry(data["name"]).get("accounts")
		self.assertEqual(lines[0]["member"], MEMBER_ONE)

	def test_a_dimension_value_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"set_opening_balance",
			self.payload(
				[
					{
						"account": MEMBER_CAPITAL,
						"dr_or_cr": "cr",
						"amount": 100,
						"dimensions": {"member": "Member-99"},
					}
				]
			),
		)
		self.assertIn("not a Member on this site", message)
		self.assertEqual(frappe.db.count("Journal Entry", {"user_remark": ("like", "%PFI%")}), 0)

	def test_a_per_line_narrative_becomes_the_line_remark(self):
		data = self.tool_data(
			"set_opening_balance",
			self.payload(
				[
					{
						"account": EQUIPMENT,
						"dr_or_cr": "dr",
						"amount": 100,
						"narrative": "Two forklifts and a sprayer",
					}
				]
			),
		)
		lines = self.entry(data["name"]).get("accounts")
		self.assertEqual(lines[0]["user_remark"], "Two forklifts and a sprayer")

	def test_an_empty_entry_list_is_refused_with_an_example(self):
		message = self.tool_error("set_opening_balance", self.payload([]))
		self.assertIn("non-empty list", message)
		self.assertIn("do not include it", message)


class TheRemark(OpeningBalanceTestCase):
	def test_it_is_mandatory(self):
		message = self.tool_error("set_opening_balance", self.payload(user_remark=None))
		self.assertIn("user_remark is required", message)

	def test_a_placeholder_is_refused(self):
		message = self.tool_error("set_opening_balance", self.payload(user_remark="opening"))
		self.assertIn("real explanation", message)

	def test_it_lands_on_the_journal_entry(self):
		data = self.tool_data("set_opening_balance", self.payload())
		self.assertEqual(
			self.entry(data["name"]).get("user_remark"),
			"Equipment transferred from PFI on dissolution",
		)


class SwitchAndAudit(OpeningBalanceTestCase):
	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("set_opening_balance", self.payload())
		self.assertIn("allow_set_opening_balance", message)
		self.assertEqual(frappe.db.count("Journal Entry", {"posting_date": "2026-01-01"}), 0)

	def test_a_refusal_writes_an_audit_row_and_nothing_else(self):
		before = frappe.db.count("Journal Entry")
		self.tool_error("set_opening_balance", self.payload(opening_equity_account=CASH))
		self.assertEqual(frappe.db.count("Journal Entry"), before)
		self.assertAudited("set_opening_balance", status="Error")


class TheOpeningEntryCanActuallyBePosted(OpeningBalanceTestCase):
	"""v0.8.0's opening balances could be created and not submitted.

	The lines went in with `debit` and no `debit_in_account_currency`; ERPNext
	derives the first from the second, so the draft was stored with its amounts
	zeroed and refused on submit with *"Row 1: Both Debit and Credit values
	cannot be zero"*. Four of them did that on a live site, and the workaround
	was keying every line by hand through `create_journal_entry`.
	"""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_submit_journal_entry=1, **ALL_ON)

	def test_the_draft_keeps_the_amounts_it_was_given(self):
		data = self.tool_data("set_opening_balance", self.payload())
		lines = self.entry(data["name"]).get("accounts")
		self.assertEqual(float(lines[0]["debit"]), 52650.0)
		self.assertEqual(float(lines[0]["debit_in_account_currency"]), 52650.0)
		self.assertEqual(float(lines[1]["credit_in_account_currency"]), 52650.0)

	def test_the_computed_equity_plug_carries_them_too(self):
		"""The plug is built by this app, not by the caller, so it is the line
		most likely to be the one nobody filled in."""
		data = self.tool_data("set_opening_balance", self.payload())
		plug = next(
			line for line in self.entry(data["name"]).get("accounts") if line["account"] == OPENING_EQUITY
		)
		self.assertEqual(float(plug["credit_in_account_currency"]), 52650.0)

	def test_it_submits(self):
		data = self.tool_data("set_opening_balance", self.payload())
		posted = self.tool_data("submit_journal_entry", {"name": data["name"]})
		self.assertEqual(posted["docstatus"], 1)
		self.assertEqual(posted["total_debit"], 52650.0)
		self.assertEqual(posted["total_credit"], 52650.0)

	def test_a_multi_line_opening_balance_submits(self):
		data = self.tool_data(
			"set_opening_balance",
			self.payload(
				[
					{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 52650},
					{"account": CASH, "dr_or_cr": "dr", "amount": 1700000},
					{"account": NOTES_PAYABLE, "dr_or_cr": "cr", "amount": 200000},
				]
			),
		)
		posted = self.tool_data("submit_journal_entry", {"name": data["name"]})
		self.assertEqual(posted["docstatus"], 1)
		self.assertEqual(posted["total_debit"], posted["total_credit"])
		self.assertEqual(posted["total_debit"], 1752650.0)


class PostOpeningBalanceJournalEntry(OpeningBalanceTestCase):
	"""The other shape of the job: a whole trial balance, both sides given."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_post_opening_balance_journal_entry=1, **ALL_ON)

	def payload(self, lines=None, **overrides):
		values = {
			"company": MAIN,
			"posting_date": "2026-01-01",
			"user_remark": "Trial balance at 2025-12-31 per the prior system",
			"lines": lines
			if lines is not None
			else [
				{"account": EQUIPMENT, "side": "debit", "amount": 52650},
				{"account": NOTES_PAYABLE, "side": "credit", "amount": 52650},
			],
		}
		values.update(overrides)
		return values

	def unbalanced(self):
		return [
			{"account": EQUIPMENT, "side": "debit", "amount": 52650},
			{"account": CASH, "side": "debit", "amount": 1700000},
			{"account": NOTES_PAYABLE, "side": "credit", "amount": 200000},
		]

	# -- the happy paths -----------------------------------------------------
	def test_balanced_lines_are_written_as_given(self):
		data = self.tool_data("post_opening_balance_journal_entry", self.payload())
		self.assertEqual(data["docstatus"], 0)
		self.assertEqual(data["line_count"], 2)
		self.assertIsNone(data["offset_account"])
		self.assertEqual(data["total_debit"], data["total_credit"])
		self.assertIn("already balanced", data["note"])

	def test_the_difference_goes_to_the_offset_account(self):
		data = self.tool_data(
			"post_opening_balance_journal_entry",
			self.payload(self.unbalanced(), offset_account=OPENING_EQUITY),
		)
		self.assertEqual(data["offset_account"], OPENING_EQUITY)
		self.assertEqual(data["offset_side"], "credit")
		self.assertEqual(data["offset_amount"], 1552650.0)
		self.assertEqual(data["line_count"], 4)
		self.assertEqual(data["total_debit"], data["total_credit"])

	def test_a_credit_heavy_entry_is_offset_on_the_debit_side(self):
		data = self.tool_data(
			"post_opening_balance_journal_entry",
			self.payload(
				[{"account": NOTES_PAYABLE, "side": "credit", "amount": 1080000}],
				offset_account=OPENING_EQUITY,
			),
		)
		self.assertEqual(data["offset_side"], "debit")
		self.assertEqual(data["offset_amount"], 1080000.0)

	def test_the_journal_entry_carries_every_line(self):
		data = self.tool_data(
			"post_opening_balance_journal_entry",
			self.payload(self.unbalanced(), offset_account=OPENING_EQUITY),
		)
		lines = self.entry(data["name"]).get("accounts")
		self.assertEqual(
			[line["account"] for line in lines],
			[EQUIPMENT, CASH, NOTES_PAYABLE, OPENING_EQUITY],
		)
		self.assertEqual(float(lines[3]["credit"]), 1552650.0)
		self.assertEqual(float(lines[3]["credit_in_account_currency"]), 1552650.0)

	def test_every_line_carries_both_amount_columns(self):
		"""The bug this release exists to close, on the tool built after it."""
		data = self.tool_data("post_opening_balance_journal_entry", self.payload())
		for line in self.entry(data["name"]).get("accounts"):
			with self.subTest(account=line["account"]):
				self.assertEqual(float(line["debit"]), float(line["debit_in_account_currency"]))
				self.assertEqual(float(line["credit"]), float(line["credit_in_account_currency"]))

	def test_a_cost_center_and_a_dimension_are_carried_onto_the_line(self):
		data = self.tool_data(
			"post_opening_balance_journal_entry",
			self.payload(
				[
					{
						"account": EQUIPMENT,
						"side": "debit",
						"amount": 52650,
						"cost_center": cost_center("Field Work"),
						"dimensions": {"member": MEMBER_ONE},
						"narrative": "Tractor and sprayer",
					},
					{"account": NOTES_PAYABLE, "side": "credit", "amount": 52650},
				]
			),
		)
		line = self.entry(data["name"]).get("accounts")[0]
		self.assertEqual(line["cost_center"], cost_center("Field Work"))
		self.assertEqual(line["member"], MEMBER_ONE)
		self.assertEqual(line["user_remark"], "Tractor and sprayer")

	# -- the flags -----------------------------------------------------------
	def test_it_defaults_to_the_opening_entry_voucher_type(self):
		data = self.tool_data("post_opening_balance_journal_entry", self.payload())
		self.assertEqual(data["flags_set"]["voucher_type"], "Opening Entry")
		self.assertEqual(data["flags_set"]["is_opening"], "Yes")
		self.assertEqual(self.entry(data["name"]).get("voucher_type"), "Opening Entry")

	def test_a_caller_may_name_another_voucher_type(self):
		data = self.tool_data(
			"post_opening_balance_journal_entry", self.payload(voucher_type="Journal Entry")
		)
		self.assertEqual(self.entry(data["name"]).get("voucher_type"), "Journal Entry")

	def test_a_voucher_type_this_site_does_not_have_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry", self.payload(voucher_type="Invented Entry")
		)
		self.assertIn("not one this site's Journal Entry offers", message)
		self.assertIn("Opening Entry", message)

	def test_a_site_without_the_option_gets_an_ordinary_voucher_type(self):
		from .harness import META

		META["Journal Entry"].get_field("voucher_type")["options"] = "Journal Entry\nBank Entry"
		data = self.tool_data("post_opening_balance_journal_entry", self.payload())
		self.assertNotIn("voucher_type", data["flags_set"])
		self.assertIn("no 'Opening Entry' voucher type", data["note"])

	# -- submitting ----------------------------------------------------------
	def test_it_leaves_a_draft_by_default(self):
		data = self.tool_data("post_opening_balance_journal_entry", self.payload())
		self.assertFalse(data["submitted"])
		self.assertEqual(int(self.entry(data["name"]).get("docstatus") or 0), 0)
		self.assertIn("submit_journal_entry", data["next_step"])

	def test_submit_true_posts_it(self):
		self.configure(
			enabled=1,
			allow_post_opening_balance_journal_entry=1,
			allow_submit_journal_entry=1,
			**ALL_ON,
		)
		data = self.tool_data("post_opening_balance_journal_entry", self.payload(submit=True))
		self.assertTrue(data["submitted"])
		self.assertEqual(data["docstatus"], 1)
		self.assertEqual(int(self.entry(data["name"]).get("docstatus") or 0), 1)
		self.assertIn("has moved balances", data["next_step"])

	def test_submit_true_is_refused_when_posting_is_switched_off(self):
		"""And refused BEFORE anything is written, so a site with posting disabled
		does not end up with a draft it did not ask for."""
		before = frappe.db.count("Journal Entry")
		message = self.tool_error("post_opening_balance_journal_entry", self.payload(submit=True))
		self.assertIn("allow_submit_journal_entry", message)
		self.assertIn("Nothing was created", message)
		self.assertEqual(frappe.db.count("Journal Entry"), before)

	# -- the refusals --------------------------------------------------------
	def test_an_unbalanced_entry_with_no_offset_account_is_refused(self):
		before = frappe.db.count("Journal Entry")
		message = self.tool_error("post_opening_balance_journal_entry", self.payload(self.unbalanced()))
		self.assertIn("out by 1552650.0", message)
		self.assertIn("offset_account", message)
		self.assertIn("3300", message)
		self.assertEqual(frappe.db.count("Journal Entry"), before)

	def test_a_group_offset_account_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload(self.unbalanced(), offset_account=CURRENT_ASSETS),
		)
		self.assertIn("offset_account", message)
		self.assertIn("group account", message)

	def test_an_offset_account_in_another_company_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload(self.unbalanced(), offset_account="1100 - Cash - SEL"),
		)
		self.assertIn("belongs to company", message)

	def test_a_non_equity_offset_account_is_allowed(self):
		"""Unlike set_opening_balance, which computes the plug and so knows it is
		equity by definition. Here the caller named the account on purpose."""
		data = self.tool_data(
			"post_opening_balance_journal_entry",
			self.payload(self.unbalanced(), offset_account=MEMBER_CAPITAL),
		)
		self.assertEqual(data["offset_account"], MEMBER_CAPITAL)

	def test_one_line_that_balances_against_nothing_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload([{"account": EQUIPMENT, "side": "debit", "amount": 0.001}]),
		)
		self.assertIn("balances against nothing", message)

	def test_the_other_tools_direction_field_is_named(self):
		"""A model that has just used set_opening_balance will send dr_or_cr."""
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload([{"account": EQUIPMENT, "dr_or_cr": "dr", "amount": 100}]),
		)
		self.assertIn("unsupported field(s): dr_or_cr", message)
		self.assertIn("`side`", message)

	def test_the_direction_words_a_model_will_send_are_all_accepted(self):
		for word in ("dr", "debit", "DEBIT", "cr", "credit"):
			with self.subTest(word=word):
				data = self.tool_data(
					"post_opening_balance_journal_entry",
					self.payload(
						[{"account": EQUIPMENT, "side": word, "amount": 100}],
						offset_account=OPENING_EQUITY,
					),
				)
				self.assertEqual(data["line_count"], 2)

	def test_an_unreadable_direction_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload([{"account": EQUIPMENT, "side": "positive", "amount": 100}]),
		)
		self.assertIn("lines[1].side must say which side", message)

	def test_a_negative_amount_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload([{"account": EQUIPMENT, "side": "debit", "amount": -100}]),
		)
		self.assertIn("lines[1].amount must be positive", message)

	def test_a_group_account_on_a_line_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload([{"account": CURRENT_ASSETS, "side": "debit", "amount": 100}]),
		)
		self.assertIn("lines[1]", message)
		self.assertIn("group account", message)

	def test_a_disabled_account_is_refused(self):
		frappe.db.set_value("Account", EQUIPMENT, "disabled", 1)
		message = self.tool_error("post_opening_balance_journal_entry", self.payload())
		self.assertIn("is disabled", message)

	def test_a_dimension_value_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"post_opening_balance_journal_entry",
			self.payload(
				[
					{
						"account": EQUIPMENT,
						"side": "debit",
						"amount": 100,
						"dimensions": {"member": "Member-99"},
					},
					{"account": NOTES_PAYABLE, "side": "credit", "amount": 100},
				]
			),
		)
		self.assertIn("Member-99", message)

	def test_an_empty_line_list_is_refused(self):
		message = self.tool_error("post_opening_balance_journal_entry", self.payload([]))
		self.assertIn("non-empty list", message)

	def test_a_placeholder_remark_is_refused(self):
		message = self.tool_error("post_opening_balance_journal_entry", self.payload(user_remark="opening"))
		self.assertIn("real explanation", message)

	def test_the_remark_lands_on_the_entry(self):
		data = self.tool_data("post_opening_balance_journal_entry", self.payload())
		self.assertEqual(
			self.entry(data["name"]).get("user_remark"),
			"Trial balance at 2025-12-31 per the prior system",
		)

	# -- switch and audit ----------------------------------------------------
	def test_it_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("post_opening_balance_journal_entry", self.payload())
		self.assertIn("allow_post_opening_balance_journal_entry", message)
		self.assertEqual(frappe.db.count("Journal Entry", {"posting_date": "2026-01-01"}), 0)

	def test_the_audit_row_records_the_docstatus_delta(self):
		self.tool_data("post_opening_balance_journal_entry", self.payload())
		row = self.assertAudited("post_opening_balance_journal_entry", status="Success")
		self.assertEqual(row["docstatus_delta"], "none → 0 (draft)")
