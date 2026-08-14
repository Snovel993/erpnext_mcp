# SPDX-License-Identifier: MIT
"""The cap table, the member event trail and the governance archive.

Four things these tests are really about.

THE LEDGER STAYS ANONYMOUS. The whole design is that a posting is tagged
`Member-01` and exactly one doctype says who that is. So the tests assert on
what reaches the Journal Entry — the dimension value, never the legal name — as
well as on what the cap table returns.

THE ENTRY IS BUILT, NOT ACCEPTED. `record_member_event` decides which account is
debited and which is credited from the event type, and tags each line with the
right member. A transfer is the case that proves it: two lines, same account,
different member tags, money never leaving the company.

REFUSALS ARE THE FEATURE. An equity account that could be one of two, a
narrative too short to mean anything, a member id that is not yet a dimension
value, a second document claiming to be the same operating agreement — each is a
refusal with a remedy in it, and each has a test.

TWO SWITCHES, NOT ONE. `submit_member_event` posts to the general ledger, and it
honours `allow_submit_journal_entry` as well as its own. A second door into the
same room with a different lock would make the operator's decision meaningless,
so the test that proves the second lock holds is the important one in this file.
"""

import base64
import json

from .fixtures import (
	MAIN,
	MAIN_ABBR,
	MEMBER_CAPITAL,
	MEMBER_DISTRIBUTIONS,
	MEMBER_ONE,
	MEMBER_THREE,
	MEMBER_TWO,
	OTHER,
	V7TestCase,
	cash,
	install_hrms,
)
from .harness import ROLES, STORE, frappe, set_roles

BANK = f"1110 - Bank Checking - {MAIN_ABBR}"

ALL_ON = {
	"allow_create_cap_table_entry": 1,
	"allow_update_cap_table_entry": 1,
	"allow_list_cap_table": 1,
	"allow_close_cap_table_entry": 1,
	"allow_record_member_event": 1,
	"allow_list_member_events": 1,
	"allow_submit_member_event": 1,
	"allow_attach_governance_document": 1,
	"allow_list_governance_documents": 1,
	"allow_get_governance_document_content": 1,
}


class GovernanceTestCase(V7TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_member(self, member_id=MEMBER_ONE, **overrides):
		payload = {
			"company": MAIN,
			"member_id": member_id,
			"legal_entity_name": "The Example Family Trust",
			"entity_type": "Trust",
			"admission_date": "2020-06-15",
			"ownership_percentage": 100,
		}
		payload.update(overrides)
		return self.tool_data("create_cap_table_entry", payload)

	def journal_entry(self, name):
		return frappe.get_doc("Journal Entry", name)


# ── create_cap_table_entry ──────────────────────────────────────────────────
class CreateCapTableEntry(GovernanceTestCase):
	def test_it_registers_a_member_under_a_readable_docname(self):
		data = self.a_member()
		self.assertEqual(data["name"], f"{MEMBER_ONE} - {MAIN_ABBR}")
		self.assertEqual(data["member_id"], MEMBER_ONE)
		self.assertEqual(data["legal_entity_name"], "The Example Family Trust")
		self.assertEqual(data["entity_type"], "Trust")
		self.assertFalse(data["retired"])
		self.assertTrue(frappe.db.exists("Cap Table Entry", data["name"]))

	def test_it_records_which_dimension_master_the_id_belongs_to(self):
		data = self.a_member()
		self.assertEqual(data["member_dimension_doctype"], "Member")
		self.assertIn("Member dimension", data["note"])

	def test_an_id_that_is_not_a_dimension_value_yet_is_refused_with_the_remedy(self):
		message = self.tool_error(
			"create_cap_table_entry",
			{
				"company": MAIN,
				"member_id": "Member-09",
				"legal_entity_name": "Nobody",
				"entity_type": "Individual",
				"admission_date": "2026-01-01",
			},
		)
		self.assertIn("create_dimension_value", message)
		self.assertIn("Nothing was created", message)
		self.assertFalse(frappe.db.exists("Cap Table Entry", f"Member-09 - {MAIN_ABBR}"))

	def test_a_site_with_no_member_dimension_is_allowed_and_told_so(self):
		STORE.tables["Accounting Dimension"] = {}
		data = self.a_member()
		self.assertIsNone(data["member_dimension_doctype"])
		self.assertIn("no 'Member' accounting dimension", data["note"])

	def test_the_same_member_twice_is_refused_naming_the_existing_entry(self):
		self.a_member()
		message = self.tool_error(
			"create_cap_table_entry",
			{
				"company": MAIN,
				"member_id": MEMBER_ONE,
				"legal_entity_name": "Someone Else",
				"entity_type": "Individual",
				"admission_date": "2021-01-01",
			},
		)
		self.assertIn(f"{MEMBER_ONE} - {MAIN_ABBR}", message)
		self.assertIn("update_cap_table_entry", message)

	def test_an_impossible_percentage_is_refused(self):
		message = self.tool_error(
			"create_cap_table_entry",
			{
				"company": MAIN,
				"member_id": MEMBER_ONE,
				"legal_entity_name": "The Trust",
				"entity_type": "Trust",
				"admission_date": "2020-06-15",
				"ownership_percentage": 140,
			},
		)
		self.assertIn("between 0 and 100", message)

	def test_an_unknown_entity_type_is_refused_with_the_list(self):
		message = self.tool_error(
			"create_cap_table_entry",
			{
				"company": MAIN,
				"member_id": MEMBER_ONE,
				"legal_entity_name": "The Trust",
				"entity_type": "Sole Trader",
				"admission_date": "2020-06-15",
			},
		)
		self.assertIn("Individual", message)
		self.assertIn("Trust", message)

	def test_a_member_cannot_be_created_already_retired(self):
		message = self.tool_error(
			"create_cap_table_entry",
			{
				"company": MAIN,
				"member_id": MEMBER_ONE,
				"legal_entity_name": "The Trust",
				"entity_type": "Trust",
				"admission_date": "2020-06-15",
				"retired": True,
			},
		)
		self.assertIn("close_cap_table_entry", message)

	def test_ownership_that_does_not_total_a_hundred_warns_rather_than_refusing(self):
		data = self.a_member(ownership_percentage=60)
		self.assertEqual(data["active_ownership_total"], 60.0)
		self.assertIn("60", data["warning"])

	def test_a_member_cost_center_is_optional_and_resolved_when_given(self):
		data = self.a_member(member_cost_center="110")
		self.assertEqual(data["member_cost_center"], f"110 - Field Work - {MAIN_ABBR}")


# ── update_cap_table_entry ──────────────────────────────────────────────────
class UpdateCapTableEntry(GovernanceTestCase):
	def setUp(self):
		super().setUp()
		self.a_member()

	def test_it_changes_the_legal_name(self):
		data = self.tool_data(
			"update_cap_table_entry",
			{"member": MEMBER_ONE, "legal_entity_name": "The Example Family Trust (2026 Restatement)"},
		)
		self.assertEqual(data["legal_entity_name"], "The Example Family Trust (2026 Restatement)")
		self.assertIn("legal_entity_name", data["changes"])

	def test_the_member_can_be_named_by_its_docname_too(self):
		data = self.tool_data(
			"update_cap_table_entry",
			{"member": f"{MEMBER_ONE} - {MAIN_ABBR}", "ownership_percentage": 50},
		)
		self.assertEqual(data["ownership_percentage"], 50.0)

	def test_it_cannot_retire_a_member(self):
		message = self.tool_error("update_cap_table_entry", {"member": MEMBER_ONE, "retired": True})
		self.assertIn("close_cap_table_entry", message)
		self.assertIn("Nothing was changed", message)

	def test_it_cannot_re_key_a_member(self):
		message = self.tool_error("update_cap_table_entry", {"member": MEMBER_ONE, "member_id": "Member-99"})
		self.assertIn("every posting", message)

	def test_nothing_to_change_is_refused_rather_than_reported_as_success(self):
		message = self.tool_error("update_cap_table_entry", {"member": MEMBER_ONE})
		self.assertIn("nothing to change", message)

	def test_an_unregistered_member_is_refused_with_the_known_ones(self):
		message = self.tool_error("update_cap_table_entry", {"member": "Member-42", "legal_entity_name": "X"})
		self.assertIn(MEMBER_ONE, message)


# ── list_cap_table ──────────────────────────────────────────────────────────
class ListCapTable(GovernanceTestCase):
	def setUp(self):
		super().setUp()
		self.a_member(MEMBER_ONE, ownership_percentage=60)
		self.a_member(
			MEMBER_TWO, legal_entity_name="A. Example", entity_type="Individual", ownership_percentage=40
		)

	def test_it_lists_every_member_with_the_ownership_total(self):
		data = self.tool_data("list_cap_table", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["active_ownership_total"], 100.0)
		self.assertTrue(data["ownership_balances"])
		self.assertEqual([member["member_id"] for member in data["members"]], [MEMBER_ONE, MEMBER_TWO])

	def test_a_retired_member_is_still_listed_by_default(self):
		self.tool_data(
			"close_cap_table_entry",
			{
				"member": MEMBER_TWO,
				"withdrawal_date": "2026-03-31",
				"notes": "Bought out, see resolution 2026-03.",
			},
		)
		data = self.tool_data("list_cap_table", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["retired_count"], 1)
		self.assertEqual(data["active_count"], 1)

	def test_retired_members_can_be_left_out_when_asked(self):
		self.tool_data(
			"close_cap_table_entry",
			{
				"member": MEMBER_TWO,
				"withdrawal_date": "2026-03-31",
				"notes": "Bought out, see resolution 2026-03.",
			},
		)
		data = self.tool_data("list_cap_table", {"company": MAIN, "include_retired": False})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["retired_count"], 1)

	def test_it_warns_when_active_ownership_does_not_total_a_hundred(self):
		self.tool_data(
			"close_cap_table_entry",
			{
				"member": MEMBER_TWO,
				"withdrawal_date": "2026-03-31",
				"notes": "Bought out, see resolution 2026-03.",
			},
		)
		data = self.tool_data("list_cap_table", {"company": MAIN})
		self.assertFalse(data["ownership_balances"])
		self.assertIn("60", data["warning"])

	def test_another_company_has_its_own_empty_register(self):
		data = self.tool_data("list_cap_table", {"company": OTHER})
		self.assertEqual(data["count"], 0)


# ── close_cap_table_entry ───────────────────────────────────────────────────
class CloseCapTableEntry(GovernanceTestCase):
	def setUp(self):
		super().setUp()
		self.a_member()

	def test_it_retires_and_writes_the_exit_into_the_trail(self):
		data = self.tool_data(
			"close_cap_table_entry",
			{
				"member": MEMBER_ONE,
				"withdrawal_date": "2026-06-30",
				"notes": "Interest bought out under the 2026 buy-sell agreement.",
			},
		)
		self.assertTrue(data["retired"])
		self.assertEqual(data["withdrawal_date"], "2026-06-30")
		event = frappe.get_doc("Member Event", data["member_event"])
		self.assertEqual(event.event_type, "Withdrawal")
		self.assertEqual(float(event.amount or 0), 0.0)
		self.assertIn("buy-sell", event.narrative)

	def test_it_moves_no_money(self):
		before = len(STORE.rows("Journal Entry"))
		self.tool_data(
			"close_cap_table_entry",
			{
				"member": MEMBER_ONE,
				"withdrawal_date": "2026-06-30",
				"notes": "Bought out under the agreement.",
			},
		)
		self.assertEqual(len(STORE.rows("Journal Entry")), before)

	def test_retiring_twice_is_refused(self):
		self.tool_data(
			"close_cap_table_entry",
			{
				"member": MEMBER_ONE,
				"withdrawal_date": "2026-06-30",
				"notes": "Bought out under the agreement.",
			},
		)
		message = self.tool_error(
			"close_cap_table_entry",
			{"member": MEMBER_ONE, "withdrawal_date": "2026-07-31", "notes": "Bought out again somehow."},
		)
		self.assertIn("already retired", message)

	def test_an_exit_before_the_admission_is_refused(self):
		message = self.tool_error(
			"close_cap_table_entry",
			{"member": MEMBER_ONE, "withdrawal_date": "2019-01-01", "notes": "Impossible ordering."},
		)
		self.assertIn("before this member's admission date", message)

	def test_a_placeholder_reason_is_refused(self):
		message = self.tool_error(
			"close_cap_table_entry",
			{"member": MEMBER_ONE, "withdrawal_date": "2026-06-30", "notes": "n/a"},
		)
		self.assertIn("placeholder", message)


# ── record_member_event ─────────────────────────────────────────────────────
class RecordMemberEvent(GovernanceTestCase):
	def setUp(self):
		super().setUp()
		self.a_member(MEMBER_ONE, ownership_percentage=60)
		self.a_member(
			MEMBER_TWO, legal_entity_name="A. Example", entity_type="Individual", ownership_percentage=40
		)

	def contribution(self, **overrides):
		payload = {
			"company": MAIN,
			"event_type": "Contribution",
			"effective_date": "2026-04-01",
			"amount": 25000,
			"member": MEMBER_ONE,
			"narrative": "Capital call under section 4.2 of the operating agreement.",
		}
		payload.update(overrides)
		return payload

	def test_a_contribution_debits_the_bank_and_credits_member_capital(self):
		data = self.tool_data("record_member_event", self.contribution())
		self.assertTrue(data["journal_entry_created"])
		entry = self.journal_entry(data["offset_je"])
		self.assertEqual(int(entry.docstatus or 0), 0)
		debits = {line["account"]: line["debit"] for line in entry.accounts if line.get("debit")}
		credits = {line["account"]: line["credit"] for line in entry.accounts if line.get("credit")}
		self.assertEqual(debits, {BANK: 25000.0})
		self.assertEqual(credits, {MEMBER_CAPITAL: 25000.0})

	def test_every_line_carries_the_member_dimension_and_no_legal_name(self):
		data = self.tool_data("record_member_event", self.contribution())
		entry = self.journal_entry(data["offset_je"])
		self.assertEqual([line.get("member") for line in entry.accounts], [MEMBER_ONE, MEMBER_ONE])
		self.assertNotIn("Example Family Trust", json.dumps(dict(entry), default=str))

	def test_a_distribution_uses_the_distributions_account_the_other_way_round(self):
		data = self.tool_data(
			"record_member_event",
			self.contribution(
				event_type="Distribution", amount=4000, narrative="Quarterly distribution, Q1 2026."
			),
		)
		entry = self.journal_entry(data["offset_je"])
		debits = {line["account"]: line["debit"] for line in entry.accounts if line.get("debit")}
		credits = {line["account"]: line["credit"] for line in entry.accounts if line.get("credit")}
		self.assertEqual(debits, {MEMBER_DISTRIBUTIONS: 4000.0})
		self.assertEqual(credits, {BANK: 4000.0})

	def test_a_transfer_moves_between_two_member_tags_on_one_account(self):
		data = self.tool_data(
			"record_member_event",
			self.contribution(
				event_type="Transfer",
				amount=10000,
				counterparty_member=MEMBER_TWO,
				narrative="Transfer of interest under the 2026 assignment deed.",
			),
		)
		entry = self.journal_entry(data["offset_je"])
		self.assertEqual({line["account"] for line in entry.accounts}, {MEMBER_CAPITAL})
		by_member = {
			line["member"]: (line.get("debit") or 0, line.get("credit") or 0) for line in entry.accounts
		}
		self.assertEqual(by_member[MEMBER_ONE], (10000.0, 0))
		self.assertEqual(by_member[MEMBER_TWO], (0, 10000.0))

	def test_a_transfer_without_a_counterparty_is_refused(self):
		message = self.tool_error(
			"record_member_event",
			self.contribution(
				event_type="Transfer", amount=10000, narrative="Transfer with nobody to transfer to."
			),
		)
		self.assertIn("counterparty_member is required", message)

	def test_an_admission_records_the_event_and_posts_nothing(self):
		before = len(STORE.rows("Journal Entry"))
		data = self.tool_data(
			"record_member_event",
			self.contribution(
				event_type="Admission",
				amount=0,
				member=MEMBER_TWO,
				narrative="Admitted as a member under the 2026 amendment; no capital contributed.",
			),
		)
		self.assertFalse(data["journal_entry_created"])
		self.assertIsNone(data["offset_je"])
		self.assertEqual(len(STORE.rows("Journal Entry")), before)

	def test_a_reallocation_of_percentages_alone_books_nothing(self):
		"""Moving percentages between two members without moving capital is a real
		event with nothing to post — the one posting type allowed a zero amount."""
		before = len(STORE.rows("Journal Entry"))
		data = self.tool_data(
			"record_member_event",
			self.contribution(
				event_type="Reallocation",
				amount=0,
				counterparty_member=MEMBER_TWO,
				narrative="Percentages restated under the 2026 amendment; no capital moved.",
			),
		)
		self.assertFalse(data["journal_entry_created"])
		self.assertEqual(len(STORE.rows("Journal Entry")), before)

	def test_an_existing_journal_entry_can_be_linked_instead(self):
		data = self.tool_data("record_member_event", self.contribution(offset_je="ACC-JV-2026-00001"))
		self.assertFalse(data["journal_entry_created"])
		self.assertEqual(data["offset_je"], "ACC-JV-2026-00001")

	def test_a_journal_entry_from_another_company_is_refused(self):
		STORE.tables["Journal Entry"]["ACC-JV-2026-00001"]["company"] = OTHER
		message = self.tool_error("record_member_event", self.contribution(offset_je="ACC-JV-2026-00001"))
		self.assertIn(OTHER, message)

	def test_a_narrative_too_short_to_mean_anything_is_refused(self):
		message = self.tool_error("record_member_event", self.contribution(narrative="cash"))
		self.assertIn("real explanation", message)

	def test_a_negative_amount_is_refused_with_the_reason(self):
		message = self.tool_error("record_member_event", self.contribution(amount=-100))
		self.assertIn("own event_type", message)

	def test_a_posting_event_with_no_amount_is_refused(self):
		message = self.tool_error("record_member_event", self.contribution(amount=0))
		self.assertIn("positive amount", message)

	def test_an_ambiguous_capital_account_is_refused_with_the_candidates(self):
		STORE.seed(
			"Account",
			[
				{
					"name": f"3150 - Partner Capital - {MAIN_ABBR}",
					"account_name": "Partner Capital",
					"account_number": "3150",
					"parent_account": f"Equity - {MAIN_ABBR}",
					"is_group": 0,
					"root_type": "Equity",
					"disabled": 0,
					"company": MAIN,
				}
			],
		)
		message = self.tool_error("record_member_event", self.contribution())
		self.assertIn("Partner Capital", message)
		self.assertIn(MEMBER_CAPITAL, message)
		self.assertIn("capital_account", message)

	def test_a_named_capital_account_settles_the_ambiguity(self):
		data = self.tool_data("record_member_event", self.contribution(capital_account="3100"))
		self.assertEqual(data["accounts_used"]["capital_account"], MEMBER_CAPITAL)
		self.assertEqual(data["accounts_used"]["resolved_by"], "argument")

	def test_no_equity_account_at_all_is_refused_with_what_the_company_has(self):
		del STORE.tables["Account"][MEMBER_CAPITAL]
		del STORE.tables["Account"][MEMBER_DISTRIBUTIONS]
		message = self.tool_error("record_member_event", self.contribution())
		self.assertIn("create_account", message)
		self.assertIn("Nothing was created", message)

	def test_a_named_counter_account_is_used_instead_of_the_company_default(self):
		data = self.tool_data("record_member_event", self.contribution(counter_account="1100"))
		self.assertEqual(data["accounts_used"]["counter_account"], cash())

	def test_no_member_dimension_refuses_the_posting_with_the_remedy(self):
		STORE.tables["Accounting Dimension"] = {}
		message = self.tool_error("record_member_event", self.contribution())
		self.assertIn("create_accounting_dimension", message)
		self.assertIn("Nothing was created", message)

	def test_a_member_of_another_company_cannot_be_the_counterparty(self):
		message = self.tool_error(
			"record_member_event",
			self.contribution(event_type="Transfer", counterparty_member="Member-77", amount=1),
		)
		self.assertIn("no Cap Table Entry", message)

	def test_the_event_records_the_narrative_and_links_the_entry(self):
		data = self.tool_data("record_member_event", self.contribution())
		event = frappe.get_doc("Member Event", data["name"])
		self.assertEqual(event.member, f"{MEMBER_ONE} - {MAIN_ABBR}")
		self.assertEqual(event.offset_je, data["offset_je"])
		self.assertIn("section 4.2", event.narrative)


# ── list_member_events ──────────────────────────────────────────────────────
class ListMemberEvents(GovernanceTestCase):
	def setUp(self):
		super().setUp()
		self.a_member(MEMBER_ONE, ownership_percentage=60)
		self.a_member(
			MEMBER_TWO, legal_entity_name="A. Example", entity_type="Individual", ownership_percentage=40
		)
		self.tool_data(
			"record_member_event",
			{
				"company": MAIN,
				"event_type": "Contribution",
				"effective_date": "2026-01-05",
				"amount": 10000,
				"member": MEMBER_ONE,
				"narrative": "Opening capital under the operating agreement.",
			},
		)
		self.tool_data(
			"record_member_event",
			{
				"company": MAIN,
				"event_type": "Distribution",
				"effective_date": "2026-05-05",
				"amount": 2500,
				"member": MEMBER_TWO,
				"narrative": "Quarterly distribution, Q2 2026, per resolution.",
			},
		)

	def test_it_returns_the_trail_newest_first_with_totals(self):
		data = self.tool_data("list_member_events", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["events"][0]["event_type"], "Distribution")
		self.assertEqual(data["totals_by_event_type"], {"Distribution": 2500.0, "Contribution": 10000.0})

	def test_it_resolves_the_legal_name_from_the_register(self):
		data = self.tool_data("list_member_events", {"company": MAIN, "member": MEMBER_ONE})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["events"][0]["member_id"], MEMBER_ONE)
		self.assertEqual(data["events"][0]["legal_entity_name"], "The Example Family Trust")

	def test_it_filters_by_event_type_and_by_date(self):
		data = self.tool_data("list_member_events", {"company": MAIN, "event_type": "Contribution"})
		self.assertEqual(data["count"], 1)
		data = self.tool_data(
			"list_member_events", {"company": MAIN, "from_date": "2026-03-01", "to_date": "2026-12-31"}
		)
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["events"][0]["event_type"], "Distribution")

	def test_a_superseded_event_can_be_left_out(self):
		events = self.tool_data("list_member_events", {"company": MAIN})["events"]
		frappe.db.set_value("Member Event", events[0]["name"], "superseded_by", events[1]["name"])
		data = self.tool_data("list_member_events", {"company": MAIN, "include_superseded": False})
		self.assertEqual(data["count"], 1)


# ── submit_member_event ─────────────────────────────────────────────────────
class SubmitMemberEvent(GovernanceTestCase):
	def setUp(self):
		super().setUp()
		self.a_member()
		self.event = self.tool_data(
			"record_member_event",
			{
				"company": MAIN,
				"event_type": "Contribution",
				"effective_date": "2026-04-01",
				"amount": 25000,
				"member": MEMBER_ONE,
				"narrative": "Capital call under section 4.2 of the operating agreement.",
			},
		)

	def test_it_refuses_while_submit_journal_entry_is_off(self):
		message = self.tool_error("submit_member_event", {"name": self.event["name"]})
		self.assertIn("allow_submit_journal_entry", message)
		self.assertIn("Nothing was changed", message)
		self.assertEqual(int(self.journal_entry(self.event["offset_je"]).docstatus or 0), 0)

	def test_it_posts_the_entry_once_both_switches_are_on(self):
		self.configure(enabled=1, allow_submit_journal_entry=1, **ALL_ON)
		data = self.tool_data("submit_member_event", {"name": self.event["name"]})
		self.assertEqual(data["journal_entry"]["docstatus"], 1)
		self.assertEqual(int(self.journal_entry(self.event["offset_je"]).docstatus or 0), 1)

	def test_an_event_with_nothing_to_post_is_refused(self):
		self.configure(enabled=1, allow_submit_journal_entry=1, **ALL_ON)
		admission = self.tool_data(
			"record_member_event",
			{
				"company": MAIN,
				"event_type": "Admission",
				"effective_date": "2026-04-01",
				"amount": 0,
				"member": MEMBER_ONE,
				"narrative": "Recorded for completeness; no capital moved on admission.",
			},
		)
		message = self.tool_error("submit_member_event", {"name": admission["name"]})
		self.assertIn("no Journal Entry to post", message)


# ── the governance archive ──────────────────────────────────────────────────
AGREEMENT = b"%PDF-1.4 operating agreement, executed 2020-06-15"


class GovernanceDocuments(GovernanceTestCase):
	def attach(self, **overrides):
		payload = {
			"company": MAIN,
			"category": "Operating Agreement",
			"title": "Example Operating Agreement 2020-06-15",
			"effective_date": "2020-06-15",
			"execution_date": "2020-06-15",
			"file_name": "operating-agreement-2020.pdf",
			"file_content": base64.b64encode(AGREEMENT).decode("ascii"),
			"parties": "The Example Family Trust; A. Example",
		}
		payload.update(overrides)
		return self.tool_data("attach_governance_document", payload)

	def test_it_files_the_document_with_its_content_attached_privately(self):
		data = self.attach()
		self.assertEqual(data["category"], "Operating Agreement")
		self.assertTrue(data["attachment"]["is_private"])
		self.assertEqual(data["attachment"]["file_size"], len(AGREEMENT))
		attached = frappe.db.get_all(
			"File",
			filters={"attached_to_doctype": "Governance Document", "attached_to_name": data["name"]},
			fields=["name", "is_private"],
		)
		self.assertEqual(len(attached), 1)

	def test_content_comes_back_byte_for_byte(self):
		filed = self.attach()
		data = self.tool_data("get_governance_document_content", {"name": filed["name"]})
		self.assertEqual(base64.b64decode(data["content"]["content_base64"]), AGREEMENT)
		self.assertEqual(data["title"], "Example Operating Agreement 2020-06-15")
		self.assertTrue(data["operative"])

	def test_a_url_can_be_recorded_instead_of_uploading(self):
		data = self.attach(file_content=None, file_name=None, file_url="https://example.test/oa.pdf")
		self.assertEqual(data["attached_file"], "https://example.test/oa.pdf")
		self.assertIsNone(data["attachment"])

	def test_content_and_a_url_together_are_refused(self):
		message = self.tool_error(
			"attach_governance_document",
			{
				"company": MAIN,
				"category": "Other",
				"title": "Both at once",
				"file_content": base64.b64encode(b"x").decode("ascii"),
				"file_name": "x.txt",
				"file_url": "https://example.test/x",
			},
		)
		self.assertIn("not both", message)

	def test_content_without_a_filename_is_refused(self):
		message = self.tool_error(
			"attach_governance_document",
			{
				"company": MAIN,
				"category": "Other",
				"title": "Nameless",
				"file_content": base64.b64encode(b"x").decode("ascii"),
			},
		)
		self.assertIn("file_name is required", message)

	def test_content_that_is_not_base64_is_refused(self):
		message = self.tool_error(
			"attach_governance_document",
			{
				"company": MAIN,
				"category": "Other",
				"title": "Broken",
				"file_name": "x.txt",
				"file_content": "not base64 at all!!",
			},
		)
		self.assertIn("valid base64", message)

	def test_the_same_document_filed_twice_is_refused(self):
		self.attach()
		message = self.tool_error(
			"attach_governance_document",
			{
				"company": MAIN,
				"category": "Operating Agreement",
				"title": "Example Operating Agreement 2020-06-15",
			},
		)
		self.assertIn("already has a Operating Agreement", message)
		self.assertIn("supersedes", message)

	def test_an_amendment_chains_in_both_directions(self):
		original = self.attach()
		amendment = self.attach(
			category="Amendment",
			title="First Amendment 2026-01-10",
			effective_date="2026-01-10",
			supersedes=original["name"],
			file_name="amendment-2026.pdf",
		)
		self.assertEqual(amendment["supersedes"], original["name"])
		self.assertEqual(
			frappe.db.get_value("Governance Document", original["name"], "superseded_by"),
			amendment["name"],
		)

	def test_superseding_an_already_superseded_document_is_refused(self):
		original = self.attach()
		self.attach(
			category="Amendment",
			title="First Amendment 2026-01-10",
			supersedes=original["name"],
			file_name="amendment-2026.pdf",
		)
		message = self.tool_error(
			"attach_governance_document",
			{
				"company": MAIN,
				"category": "Amendment",
				"title": "Competing Amendment",
				"supersedes": original["name"],
			},
		)
		self.assertIn("already been superseded", message)

	def test_the_list_separates_operative_from_history(self):
		original = self.attach()
		self.attach(
			category="Amendment",
			title="First Amendment 2026-01-10",
			supersedes=original["name"],
			file_name="amendment-2026.pdf",
		)
		data = self.tool_data("list_governance_documents", {"company": MAIN})
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["operative_count"], 1)
		operative = [document for document in data["documents"] if document["operative"]]
		self.assertEqual(operative[0]["title"], "First Amendment 2026-01-10")

	def test_the_list_can_be_narrowed_to_one_category(self):
		self.attach()
		self.attach(category="Board Resolution", title="Resolution 2026-03", file_name="res.pdf")
		data = self.tool_data("list_governance_documents", {"company": MAIN, "category": "Board Resolution"})
		self.assertEqual(data["count"], 1)

	def test_a_document_with_no_attachment_reports_that_rather_than_failing(self):
		filed = self.attach(file_content=None, file_name=None)
		data = self.tool_data("get_governance_document_content", {"name": filed["name"]})
		self.assertIsNone(data["content"])
		self.assertIn("no attached file", data["note"])

	def test_an_unknown_document_is_refused_by_name(self):
		message = self.tool_error("get_governance_document_content", {"name": "GOV-nope"})
		self.assertIn("list_governance_documents", message)

	def test_wrapped_base64_is_accepted(self):
		"""MIME-style base64 arrives wrapped at 76 columns; the whitespace is not
		part of the document."""
		wrapped = "\n".join(
			base64.b64encode(AGREEMENT).decode("ascii")[index : index + 16]
			for index in range(0, len(base64.b64encode(AGREEMENT)), 16)
		)
		filed = self.attach(file_content=wrapped)
		data = self.tool_data("get_governance_document_content", {"name": filed["name"]})
		self.assertEqual(base64.b64decode(data["content"]["content_base64"]), AGREEMENT)


# ── the switches ────────────────────────────────────────────────────────────
class GovernanceSwitches(V7TestCase):
	def test_every_governance_tool_is_off_until_it_is_turned_on(self):
		self.configure(enabled=1)
		for tool in (
			"create_cap_table_entry",
			"update_cap_table_entry",
			"close_cap_table_entry",
			"record_member_event",
			"submit_member_event",
			"attach_governance_document",
		):
			with self.subTest(tool=tool):
				message = self.tool_error(tool, {})
				self.assertIn(f"allow_{tool}", message)

	def test_the_read_tools_are_on_out_of_the_box(self):
		self.configure(enabled=1)
		data = self.tool_data("list_cap_table", {"company": MAIN})
		self.assertEqual(data["count"], 0)

	def test_a_failed_governance_write_still_leaves_an_audit_row(self):
		self.configure(enabled=1, allow_create_cap_table_entry=1)
		self.tool_error(
			"create_cap_table_entry",
			{
				"company": MAIN,
				"member_id": MEMBER_THREE,
				"legal_entity_name": "Third",
				"entity_type": "Nonsense",
				"admission_date": "2026-01-01",
			},
		)
		row = self.assertAudited("create_cap_table_entry", status="Error")
		self.assertIn("entity_type", row["result_summary"])


# ── v0.68.0: create_owner_draw ────────────────────────────────────────────────


class CreateOwnerDraw(V7TestCase):
	"""Independent of the cap table on purpose — no member, no dimension, just a
	role and an equity account. `V7TestCase` gives it `MEMBER_DISTRIBUTIONS`,
	whose name ("Member Distributions") already matches the same keyword table
	`record_member_event` uses, and `MAIN`'s default bank account."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_create_owner_draw=1)
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		ROLES.clear()

	def tearDown(self):
		ROLES.clear()
		ROLES.update(self._roles_before)
		super().tearDown()

	def draw_args(self, **overrides):
		payload = {
			"company": MAIN,
			"amount": 5000,
			"date": "2026-07-01",
			"narrative": "Quarterly distribution approved by the members.",
		}
		payload.update(overrides)
		return payload

	def journal_entry(self, name):
		return frappe.get_doc("Journal Entry", name)

	def test_refused_with_no_role_at_all(self):
		error = self.tool_error("create_owner_draw", self.draw_args())
		self.assertIn("may not record an owner draw", error)
		self.assertIn("Member Manager", error)

	def test_a_role_that_is_not_member_manager_is_refused(self):
		set_roles("Administrator", ["Accounts User"])
		error = self.tool_error("create_owner_draw", self.draw_args())
		self.assertIn("may not record an owner draw", error)

	def test_member_manager_role_is_accepted(self):
		set_roles("Administrator", ["Member Manager"])
		data = self.tool_data("create_owner_draw", self.draw_args())
		self.assertTrue(data["name"])
		self.assertEqual(data["recorded_by"], "Administrator")

	def test_system_manager_is_accepted_too(self):
		set_roles("Administrator", ["System Manager"])
		data = self.tool_data("create_owner_draw", self.draw_args())
		self.assertTrue(data["name"])

	def test_debits_the_matched_distributions_account_and_credits_the_bank(self):
		set_roles("Administrator", ["Member Manager"])
		data = self.tool_data("create_owner_draw", self.draw_args(amount=1200))
		doc = self.journal_entry(data["name"])
		lines = {row.account: (row.debit, row.credit) for row in doc.accounts}
		self.assertEqual(lines[MEMBER_DISTRIBUTIONS], (1200.0, 0.0))
		self.assertEqual(lines[BANK], (0.0, 1200.0))

	def test_it_is_always_a_draft(self):
		set_roles("Administrator", ["Member Manager"])
		data = self.tool_data("create_owner_draw", self.draw_args())
		self.assertEqual(data["docstatus"], 0)
		doc = self.journal_entry(data["name"])
		self.assertEqual(int(doc.docstatus), 0)

	def test_amount_must_be_positive(self):
		set_roles("Administrator", ["Member Manager"])
		error = self.tool_error("create_owner_draw", self.draw_args(amount=-100))
		self.assertIn("positive", error)

	def test_amount_is_required(self):
		set_roles("Administrator", ["Member Manager"])
		payload = self.draw_args()
		payload.pop("amount")
		error = self.tool_error("create_owner_draw", payload)
		self.assertIn("amount is required", error)

	def test_narrative_too_short_is_refused(self):
		set_roles("Administrator", ["Member Manager"])
		error = self.tool_error("create_owner_draw", self.draw_args(narrative="why"))
		self.assertIn("narrative must be a real explanation", error)

	def test_date_is_required(self):
		set_roles("Administrator", ["Member Manager"])
		payload = self.draw_args()
		payload.pop("date")
		error = self.tool_error("create_owner_draw", payload)
		self.assertIn("date", error)

	def test_effective_date_is_accepted_as_an_alias(self):
		set_roles("Administrator", ["Member Manager"])
		payload = self.draw_args()
		payload["effective_date"] = payload.pop("date")
		data = self.tool_data("create_owner_draw", payload)
		self.assertTrue(data["name"])

	def test_explicit_draw_account_overrides_the_keyword_match(self):
		set_roles("Administrator", ["Member Manager"])
		data = self.tool_data("create_owner_draw", self.draw_args(draw_account=MEMBER_CAPITAL))
		self.assertEqual(data["draw_account"], MEMBER_CAPITAL)
		self.assertEqual(data["draw_account_resolved_by"], "argument")

	def test_switched_off_by_default(self):
		self.configure(enabled=1, allow_create_owner_draw=0)
		set_roles("Administrator", ["Member Manager"])
		error = self.tool_error("create_owner_draw", self.draw_args())
		self.assertIn("switched off", error)


class CreateOwnerDrawFromReceipt(V7TestCase):
	def setUp(self):
		super().setUp()
		self.configure(
			enabled=1,
			allow_create_owner_draw=1,
			allow_submit_expense_receipt=1,
		)
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		ROLES.clear()
		set_roles("Administrator", ["Member Manager"])
		install_hrms()

	def tearDown(self):
		ROLES.clear()
		ROLES.update(self._roles_before)
		super().tearDown()

	def owner_draw_receipt(self, **overrides):
		payload = {
			"merchant": "Owner Draw",
			"amount": 2500,
			"receipt_date": "2026-07-01",
			"category": "Owner Draw",
			"company": MAIN,
			"submitted_by": "HR-EMP-00001",
		}
		payload.update(overrides)
		return self.tool_data("submit_expense_receipt", payload)["name"]

	def test_links_the_receipt_to_the_journal_entry(self):
		name = self.owner_draw_receipt()
		data = self.tool_data(
			"create_owner_draw",
			{
				"company": MAIN,
				"amount": 2500,
				"date": "2026-07-01",
				"narrative": "Owner draw captured from a photographed slip.",
				"receipt": name,
			},
		)
		receipt = self.tool_data("get_expense_receipt", {"name": name})
		self.assertEqual(receipt["linked_doctype"], "Journal Entry")
		self.assertEqual(receipt["linked_document"], data["name"])

	def test_a_non_owner_draw_receipt_is_refused(self):
		name = self.owner_draw_receipt(category="Fuel")
		error = self.tool_error(
			"create_owner_draw",
			{
				"company": MAIN,
				"amount": 2500,
				"date": "2026-07-01",
				"narrative": "This should not work.",
				"receipt": name,
			},
		)
		self.assertIn("not", error)
		self.assertIn("Owner Draw", error)

	def test_an_already_linked_receipt_is_refused(self):
		name = self.owner_draw_receipt()
		args = {
			"company": MAIN,
			"amount": 2500,
			"date": "2026-07-01",
			"narrative": "First draw against this receipt.",
			"receipt": name,
		}
		self.tool_data("create_owner_draw", args)
		error = self.tool_error("create_owner_draw", {**args, "narrative": "Second attempt."})
		self.assertIn("already linked", error)
