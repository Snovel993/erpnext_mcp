# SPDX-License-Identifier: MIT
"""`investigate_je_gl_link` — and the belief about ERPNext it exists to correct.

THE INCIDENT. Sprint 6 verification on 2026-07-30 ran
`update_journal_entry_party` against ACC-JV-2026-00073 — a $10 member
distribution against an Equity account — and got `gl_entries_matched: 0`. Three
explanations were live: an Equity-account quirk, a Bank Bridge JE-crafting bug,
or ordinary ERPNext behaviour.

THE ANSWER. Ordinary ERPNext behaviour, and nothing to do with Equity.
`GL Entry.voucher_detail_no` carries the child-row docname for Sales Invoice
Item and its relatives; for a Journal Entry, `JournalEntry.get_gl_entries` fills
it from the line's `reference_detail_no`, which points at a payment schedule row
on an invoice being settled and is empty on every ordinary line. A lookup keyed
on it matches nothing, for every account type, on every site.

WHY THE STANDALONE SUITE MISSED IT. The fixture seeded GL rows by hand with
`voucher_detail_no = <the line's docname>`, which is what anybody would write
and what the code believed. A double built from the same wrong belief as the
code cannot contradict it. `harness.post_journal_entry_gl` now models what
ERPNext actually writes — including `merge_similar_entries`, which collapses
lines sharing an account, party and cost center into ONE summed row — and the
first two tests below are the ones that would have caught the release.

The tool is read-only and available on drafts and cancelled entries too, since
a voucher whose rows were reversed is exactly the one somebody is trying to
understand.
"""

from .fixtures import (
	ALEX,
	BANK,
	MAIN,
	MEMBER_DISTRIBUTIONS,
	V12TestCase,
	cash,
	supplies,
)
from .harness import STORE, post_journal_entry_gl

ON = {"allow_investigate_je_gl_link": 1}


class InvestigateJeGlLink(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_create_journal_entry=1, allow_submit_journal_entry=1, **ON)

	# -- entries -------------------------------------------------------------
	def a_distribution(self, submit=True):
		"""The shape of ACC-JV-2026-00073: $10 out of the bank to an Equity account."""
		name = self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-07-30",
				"user_remark": "member distribution",
				"accounts": [
					{"account": MEMBER_DISTRIBUTIONS, "debit": 10},
					{"account": BANK, "credit": 10},
				],
			},
		)["name"]
		if submit:
			self.tool_data("submit_journal_entry", {"name": name})
			post_journal_entry_gl(name)
		return name

	def a_twin_entry(self):
		"""Two lines to one account for one amount — indistinguishable in the GL."""
		name = self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-07-30",
				"user_remark": "two identical transfers on one voucher",
				"accounts": [
					{"account": supplies(), "debit": 600},
					{"account": supplies(), "debit": 600},
					{"account": cash(), "credit": 1200},
				],
			},
		)["name"]
		self.tool_data("submit_journal_entry", {"name": name})
		post_journal_entry_gl(name)
		return name

	# -- the finding ---------------------------------------------------------
	def test_a_journal_entrys_gl_rows_carry_no_voucher_detail_no(self):
		"""The fact the whole feature turns on, asserted where a reader will find it."""
		name = self.a_distribution()
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual(data["summary"]["voucher_detail_no_populated"], 0)
		for line in data["lines"]:
			for row in line["gl_entries"]:
				self.assertIsNone(row["voucher_detail_no"])

	def test_it_says_the_zero_is_erpnext_behaviour_and_not_an_equity_quirk(self):
		name = self.a_distribution()
		finding = self.tool_data("investigate_je_gl_link", {"journal_entry": name})["finding"]
		self.assertIn("NOT ONE of the GL rows carries a voucher_detail_no", finding)
		self.assertIn("reference_detail_no", finding)
		self.assertIn("not a defect and not an account-type", finding)

	def test_the_equity_line_matches_its_gl_row_on_account_and_amount(self):
		"""The point of the fix: the row v0.13.0 could not find is found."""
		name = self.a_distribution()
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		equity = data["lines"][0]
		self.assertEqual(equity["account"], MEMBER_DISTRIBUTIONS)
		self.assertEqual(equity["root_type"], "Equity")
		self.assertEqual(equity["gl_entries_matched"], 1)
		self.assertEqual(equity["matched_by"], "account and amount")
		self.assertFalse(equity["match_is_exact"])
		self.assertEqual(equity["gl_entries"][0]["debit"], 10)

	# -- the per-line report -------------------------------------------------
	def test_every_line_is_reported_with_its_account_facts(self):
		name = self.a_distribution()
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual([line["line_index"] for line in data["lines"]], [1, 2])
		self.assertEqual(data["lines"][1]["account"], BANK)
		self.assertEqual(data["lines"][1]["root_type"], "Asset")
		self.assertEqual(data["lines"][1]["account_type"], "Bank")
		self.assertEqual(data["lines"][1]["credit"], 10)
		self.assertTrue(data["lines"][0]["line_name"])

	def test_the_summary_counts_lines_rows_and_matches(self):
		name = self.a_distribution()
		summary = self.tool_data("investigate_je_gl_link", {"journal_entry": name})["summary"]
		self.assertEqual(summary["journal_entry_lines"], 2)
		self.assertEqual(summary["gl_entry_rows"], 2)
		self.assertEqual(summary["matched_pairs"], 2)
		self.assertEqual(summary["unmatched_journal_entry_lines"], [])
		self.assertEqual(summary["unmatched_gl_entry_rows"], 0)

	def test_it_flags_a_line_whose_party_disagrees_with_the_ledger(self):
		"""Exactly the state v0.13.0's tool left behind: voucher updated, ledger not."""
		name = self.a_distribution()
		line = STORE.get_raw("Journal Entry", name)["accounts"][0]
		line["party_type"] = "Family"
		line["party"] = ALEX
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual(data["summary"]["lines_whose_party_disagrees_with_the_ledger"], [1])
		self.assertTrue(data["lines"][0]["gl_entries"][0]["party_disagrees_with_line"])
		self.assertEqual(data["lines"][0]["party"], ALEX)
		self.assertIsNone(data["lines"][0]["gl_entries"][0]["party"])

	def test_a_line_that_agrees_is_not_flagged(self):
		name = self.a_distribution()
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual(data["summary"]["lines_whose_party_disagrees_with_the_ledger"], [])
		self.assertFalse(data["lines"][0]["gl_entries"][0]["party_disagrees_with_line"])

	# -- merged and ambiguous ------------------------------------------------
	def test_merged_rows_leave_both_lines_unmatched_and_the_row_unexplained(self):
		name = self.a_twin_entry()
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual(data["summary"]["unmatched_journal_entry_lines"], [1, 2])
		self.assertEqual(data["summary"]["unmatched_gl_entry_rows"], 1)
		self.assertEqual(data["unmatched_gl_entries"][0]["debit"], 1200)
		self.assertIn("coin toss", data["lines"][0]["blocker"])

	def test_the_finding_names_the_merge_when_lines_go_unmatched(self):
		name = self.a_twin_entry()
		finding = self.tool_data("investigate_je_gl_link", {"journal_entry": name})["finding"]
		self.assertIn("[1, 2] could not be matched", finding)
		self.assertIn("merges lines that share an account", finding)
		self.assertIn("not explained by any single line", finding)

	def test_the_third_line_of_the_twin_entry_still_matches_cleanly(self):
		"""A voucher being partly ambiguous does not make all of it unreadable."""
		name = self.a_twin_entry()
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual(data["lines"][2]["gl_entries_matched"], 1)
		self.assertIsNone(data["lines"][2]["blocker"])

	# -- the states an entry can be in ---------------------------------------
	def test_a_draft_is_reported_as_a_draft_rather_than_as_a_problem(self):
		name = self.a_distribution(submit=False)
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual(data["docstatus_label"], "draft")
		self.assertEqual(data["summary"]["gl_entry_rows"], 0)
		self.assertIn("posts no GL Entry rows at all", data["finding"])
		self.assertIn("nothing wrong", data["finding"])

	def test_a_cancelled_entry_is_readable_and_says_so(self):
		name = self.a_distribution()
		STORE.get_raw("Journal Entry", name)["docstatus"] = 2
		for row in STORE.rows("GL Entry"):
			if row.get("voucher_no") == name:
				row["is_cancelled"] = 1
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual(data["docstatus_label"], "cancelled")
		self.assertIn("CANCELLED", data["finding"])
		self.assertEqual(data["summary"]["gl_entry_rows"], 0)

	def test_a_submitted_entry_with_no_gl_rows_at_all_is_called_unusual(self):
		name = self.a_distribution(submit=False)
		self.tool_data("submit_journal_entry", {"name": name})
		data = self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertIn("NO live GL Entry rows", data["finding"])
		self.assertIn("unusual", data["finding"])

	def test_cancelled_gl_rows_are_excluded_from_the_counts(self):
		name = self.a_distribution()
		for row in STORE.rows("GL Entry"):
			if row.get("voucher_no") == name and row.get("account") == BANK:
				row["is_cancelled"] = 1
		summary = self.tool_data("investigate_je_gl_link", {"journal_entry": name})["summary"]
		self.assertEqual(summary["gl_entry_rows"], 1)

	# -- refusals and the switch ---------------------------------------------
	def test_an_unknown_entry_is_refused_by_name(self):
		message = self.tool_error("investigate_je_gl_link", {"journal_entry": "ACC-JV-NOPE"})
		self.assertIn("no Journal Entry named", message)
		self.assertIn("ACC-JV-NOPE", message)

	def test_the_journal_entry_argument_is_required(self):
		self.assertIn("journal_entry is required", self.tool_error("investigate_je_gl_link", {}))

	def test_it_is_a_read_tool_and_ships_switched_on(self):
		from erpnext_mcp import registry

		self.assertIn("investigate_je_gl_link", registry.READ_TOOLS)
		name = self.a_distribution()
		# `configure()` with no overrides writes the shipped defaults, so what
		# follows is the out-of-the-box posture rather than a value the test chose.
		self.configure(enabled=1)
		self.tool_data("investigate_je_gl_link", {"journal_entry": name})

	def test_an_operator_can_switch_it_off_and_it_vanishes_from_the_catalogue(self):
		self.configure(enabled=1, allow_investigate_je_gl_link=0)
		body, _status = self.call("tools/list")
		self.assertNotIn("investigate_je_gl_link", [tool["name"] for tool in body["result"]["tools"]])
		self.assertIn(
			"allow_investigate_je_gl_link",
			self.tool_error("investigate_je_gl_link", {"journal_entry": "x"}),
		)

	def test_it_writes_nothing(self):
		name = self.a_distribution()
		before = len(STORE.rows("GL Entry")), len(STORE.rows("Journal Entry"))
		self.tool_data("investigate_je_gl_link", {"journal_entry": name})
		self.assertEqual((len(STORE.rows("GL Entry")), len(STORE.rows("Journal Entry"))), before)
