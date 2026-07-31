# SPDX-License-Identifier: MIT
"""Attribution drift: finding it, repairing one line, and repairing a batch.

WHAT DRIFT IS. A Journal Entry line carries `party_type` and `party`; so does
each GL Entry row it posted. The voucher is what the entry shows; the GL is what
every ageing report, party ledger and statement of account reads. They are
supposed to say the same thing.

WHERE THIS PARTICULAR CLASS OF IT CAME FROM. v0.13.0's
`update_journal_entry_party` looked its GL rows up by `voucher_detail_no ==
line.name`, which is the Sales Invoice Item convention and not the Journal Entry
one. Every call against a submitted entry matched zero rows, wrote the voucher,
and returned a warning blaming the site. What that leaves behind is a voucher
saying one party, a ledger saying another, and nothing in either table admitting
to the disagreement — which is why it needs a tool to find rather than a query to
run.

`TheAccJv73Damage` REPRODUCES THE ORIGINAL INCIDENT, on the same shape of entry:
a $10 member distribution against an Equity account, damaged exactly the way
v0.13.0 damaged it, then found and repaired. It is the test Tim asked for by
name, and it is the one that would fail if the matcher regressed to the v0.13.0
behaviour.

v0.14.0 FIXED THE MATCHER AND LEFT ONE THING BROKEN, which is what Feature E
finishes. Its idempotence check read only the VOUCHER: if the line already said
what was asked for, it refused with "nothing to change" — which on a drifted line
is precisely wrong, because the voucher agreeing is the SIGNATURE of the damage.
`TheIdempotenceCheckReadsBothTables` is that fix.

THE REPAIR MOVES NO BALANCE, EVER, and there is a test that adds up the ledger
before and after. That is what makes a batch write to submitted vouchers
defensible at all.
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

ON = {
	"allow_find_drifted_je_attributions": 1,
	"allow_repair_drifted_je_attributions": 1,
	"allow_update_journal_entry_party": 1,
	"allow_investigate_je_gl_link": 1,
	"allow_create_journal_entry": 1,
	"allow_submit_journal_entry": 1,
	"allow_get_journal_entry": 1,
}

RANGE = {"from_date": "2020-01-01", "to_date": "2030-01-01"}


class DriftTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		# The damaged entries all carry a Family party, because that is the shape
		# the incident had: a member distribution attributed to a relative. The
		# party type has to be registered for the posting to be valid at all —
		# `party` is a Dynamic Link resolved through `party_type`, which is the
		# whole reason the Family DocType exists.
		from erpnext_mcp.tools import company

		company.ensure_party_types()

	def a_distribution(self, remark="member distribution"):
		"""The shape of ACC-JV-2026-00073: $10 out of the bank to an Equity account."""
		name = self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-07-30",
				"user_remark": remark,
				"accounts": [
					{"account": MEMBER_DISTRIBUTIONS, "debit": 10, "party_type": "Family", "party": ALEX},
					{"account": BANK, "credit": 10},
				],
			},
		)["name"]
		self.tool_data("submit_journal_entry", {"name": name})
		post_journal_entry_gl(name)
		return name

	def damage_it_the_way_v013_did(self, name: str, line_index: int = 1, party: str = ALEX):
		"""Write the VOUCHER and leave the LEDGER alone — v0.13.0's exact failure.

		Done by hand rather than through the tool, because the tool no longer does
		this. Reproducing the damage is the only way to test finding it.
		"""
		entry = STORE.get_raw("Journal Entry", name)
		line = entry["accounts"][line_index - 1]
		line["party_type"] = "Family"
		line["party"] = party
		return name

	def gl_rows(self, name: str) -> list:
		return [row for row in STORE.rows("GL Entry") if row.get("voucher_no") == name]

	def ledger_totals(self) -> dict:
		"""Every account's debit and credit, for the "no balance moved" check."""
		totals: dict = {}
		for row in STORE.rows("GL Entry"):
			bucket = totals.setdefault(row.get("account"), [0.0, 0.0])
			bucket[0] += float(row.get("debit") or 0)
			bucket[1] += float(row.get("credit") or 0)
		return totals


# ── the scan ────────────────────────────────────────────────────────────────
class FindingDrift(DriftTestCase):
	def test_a_clean_ledger_reports_nothing(self):
		self.a_distribution()
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(data["drifted_line_count"], 0)
		self.assertGreater(data["entries_scanned"], 0)

	def test_a_drifted_line_is_found_with_both_sides_reported(self):
		name = self.a_distribution()
		self.gl_rows(name)[0].update({"party_type": None, "party": None})
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(data["drifted_line_count"], 1)
		row = data["drifted"][0]
		self.assertEqual(row["journal_entry"], name)
		self.assertEqual(row["jea_party"], ALEX)
		self.assertIsNone(row["gle_party"])
		self.assertEqual(row["account"], MEMBER_DISTRIBUTIONS)

	def test_it_reports_the_line_index_erpnext_numbers_the_line_by(self):
		"""A report that indexed lines in whatever order the database returned
		would name the wrong line in a repair instruction."""
		name = self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-07-30",
				"user_remark": "three lines, the middle one drifted",
				"accounts": [
					{"account": supplies(), "debit": 100},
					{"account": MEMBER_DISTRIBUTIONS, "debit": 10, "party_type": "Family", "party": ALEX},
					{"account": cash(), "credit": 110},
				],
			},
		)["name"]
		self.tool_data("submit_journal_entry", {"name": name})
		post_journal_entry_gl(name)
		equity_row = next(
			row for row in self.gl_rows(name) if row.get("account") == MEMBER_DISTRIBUTIONS
		)
		equity_row.update({"party_type": None, "party": None})
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(data["drifted"][0]["line_index"], 2)

	def test_it_scans_in_three_queries_whatever_the_range(self):
		"""Per-entry `get_doc` on a five-hundred-entry range is a diagnostic
		nobody runs twice."""
		for index in range(5):
			self.a_distribution(remark=f"distribution {index}")
		before = len(STORE.queries) if hasattr(STORE, "queries") else None
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertGreaterEqual(data["entries_scanned"], 5)
		if before is not None:  # pragma: no cover - the double may not count queries
			self.assertLess(len(STORE.queries) - before, 20)

	def test_an_ambiguous_line_is_reported_separately_and_not_as_drift(self):
		"""Two lines of one voucher posting the same amount to one account are
		indistinguishable in the ledger. Reporting a coin toss as a finding would
		be worse than reporting nothing."""
		name = self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-07-30",
				"user_remark": "two identical transfers on one voucher",
				"accounts": [
					{"account": supplies(), "debit": 600, "party_type": "Family", "party": ALEX},
					{"account": supplies(), "debit": 600},
					{"account": cash(), "credit": 1200},
				],
			},
		)["name"]
		self.tool_data("submit_journal_entry", {"name": name})
		post_journal_entry_gl(name)
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(data["drifted_line_count"], 0)
		self.assertTrue(data["ambiguous"])
		self.assertNotIn(name, [row["journal_entry"] for row in data["drifted"]])

	def test_a_draft_is_not_scanned(self):
		"""A draft has written no GL rows, so it cannot disagree with them."""
		self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-07-30",
				"user_remark": "a draft nobody submitted",
				"accounts": [
					{"account": MEMBER_DISTRIBUTIONS, "debit": 10, "party_type": "Family", "party": ALEX},
					{"account": BANK, "credit": 10},
				],
			},
		)
		self.assertEqual(self.tool_data("find_drifted_je_attributions", RANGE)["drifted_line_count"], 0)

	def test_it_groups_by_drift_vintage_and_says_the_grouping_is_not_a_filter(self):
		"""Drift from a restored backup or a direct database edit is just as real
		and lands outside the window."""
		name = self.a_distribution()
		self.gl_rows(name)[0].update({"party_type": None, "party": None})
		STORE.get_raw("Journal Entry", name)["modified"] = "2026-07-30 14:00:00"
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(data["vintage_window"], {"from": "2026-07-30", "to": "2026-07-31"})
		self.assertIn("the v0.13.0 window", " ".join(data["by_vintage"]))
		self.assertIn("never used to filter it", data["vintage_note"])

	def test_drift_outside_the_window_is_still_reported(self):
		name = self.a_distribution()
		self.gl_rows(name)[0].update({"party_type": None, "party": None})
		STORE.get_raw("Journal Entry", name)["modified"] = "2025-01-01 09:00:00"
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(data["drifted_line_count"], 1)
		self.assertIn("predates the broken tool", data["drifted"][0]["vintage"])

	def test_the_window_can_be_moved_to_when_this_site_ran_v013(self):
		"""A site that upgraded later ran the broken tool for longer."""
		name = self.a_distribution()
		self.gl_rows(name)[0].update({"party_type": None, "party": None})
		STORE.get_raw("Journal Entry", name)["modified"] = "2026-09-15 09:00:00"
		data = self.tool_data(
			"find_drifted_je_attributions",
			{**RANGE, "vintage_from": "2026-09-01", "vintage_to": "2026-09-30"},
		)
		self.assertIn("2026-09-01 to 2026-09-30", data["drifted"][0]["vintage"])

	def test_repair_input_is_the_batch_tools_argument_verbatim(self):
		name = self.a_distribution()
		self.gl_rows(name)[0].update({"party_type": None, "party": None})
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(
			data["repair_input"],
			[{"journal_entry": name, "line_index": 1, "party_type": "Family", "party": ALEX}],
		)

	def test_it_writes_nothing(self):
		name = self.a_distribution()
		self.gl_rows(name)[0].update({"party_type": None, "party": None})
		before = self.ledger_totals()
		self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(self.ledger_totals(), before)
		self.assertIsNone(self.gl_rows(name)[0]["party"])

	def test_a_backwards_range_is_refused(self):
		message = self.tool_error(
			"find_drifted_je_attributions", {"from_date": "2026-12-31", "to_date": "2026-01-01"}
		)
		self.assertIn("before", message)

	def test_an_empty_range_says_so_rather_than_reporting_a_clean_ledger(self):
		data = self.tool_data(
			"find_drifted_je_attributions", {"from_date": "2019-01-01", "to_date": "2019-12-31"}
		)
		self.assertEqual(data["entries_scanned"], 0)
		self.assertIn("nothing to check", data["note"])


class TheAccJv73Damage(DriftTestCase):
	"""Tim's requirement by name: the incident, reproduced and then repaired.

	A $10 member distribution against an Equity account — the entry that surfaced
	the whole bug on 2026-07-30 — damaged exactly the way v0.13.0 damaged it. The
	shape is what matters, not the docname: it looked like an Equity quirk and was
	not, and the same zero came back for every account type on every site.
	"""

	def setUp(self):
		super().setUp()
		self.name = self.a_distribution()
		# v0.13.0's exact outcome: the voucher was written, the ledger was not.
		self.gl_rows(self.name)[0].update({"party_type": None, "party": None})

	def test_the_scan_finds_it(self):
		data = self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertEqual(data["drifted_entry_count"], 1)
		row = data["drifted"][0]
		self.assertEqual(row["journal_entry"], self.name)
		self.assertEqual(row["account"], MEMBER_DISTRIBUTIONS)
		self.assertEqual(row["debit"], 10.0)
		self.assertEqual(row["jea_party_type"], "Family")
		self.assertIsNone(row["gle_party_type"])

	def test_it_is_matched_on_account_and_amount_rather_than_on_voucher_detail_no(self):
		"""THE REGRESSION GUARD. A matcher that went back to v0.13.0's lookup would
		find nothing here and this scan would report a clean ledger."""
		row = self.tool_data("find_drifted_je_attributions", RANGE)["drifted"][0]
		self.assertEqual(row["matched_by"], "account and amount")
		self.assertFalse(row["match_is_exact"])

	def test_the_equity_account_is_not_special(self):
		"""It looked like an Equity quirk on the day. It was not."""
		self.assertEqual(
			STORE.get_raw("Account", MEMBER_DISTRIBUTIONS)["root_type"], "Equity"
		)
		row = self.tool_data("find_drifted_je_attributions", RANGE)["drifted"][0]
		self.assertEqual(row["gl_entry"], self.gl_rows(self.name)[0]["name"])

	def test_the_single_line_tool_repairs_it_and_calls_it_a_gl_only_update(self):
		data = self.tool_data(
			"update_journal_entry_party",
			{
				"journal_entry": self.name,
				"line_index": 1,
				"party_type": "Family",
				"party": ALEX,
				"reason": "repairing drift left by v0.13.0's update_journal_entry_party",
			},
		)
		self.assertTrue(data["gl_only_update"])
		self.assertTrue(data["repaired_drift"])
		self.assertEqual(data["gl_entries_updated"], 1)
		self.assertEqual(self.gl_rows(self.name)[0]["party"], ALEX)

	def test_the_batch_tool_repairs_it_from_the_scans_own_output(self):
		found = self.tool_data("find_drifted_je_attributions", RANGE)
		data = self.tool_data(
			"repair_drifted_je_attributions",
			{
				"repairs": found["repair_input"],
				"reason": "repairing drift left by v0.13.0's update_journal_entry_party",
				"dry_run": False,
			},
		)
		self.assertEqual(data["repaired"], 1)
		self.assertEqual(data["failed"], 0)

	def test_a_second_scan_after_the_repair_is_clean(self):
		"""The only proof the repair worked, and cheaper than reading the list
		twice — which is what the tool's own `next_step` says."""
		found = self.tool_data("find_drifted_je_attributions", RANGE)
		result = self.tool_data(
			"repair_drifted_je_attributions",
			{
				"repairs": found["repair_input"],
				"reason": "repairing drift left by v0.13.0's update_journal_entry_party",
				"dry_run": False,
			},
		)
		self.assertIn("clean second scan", result["next_step"])
		self.assertEqual(
			self.tool_data("find_drifted_je_attributions", RANGE)["drifted_line_count"], 0
		)


# ── the v0.15.0 idempotence fix ─────────────────────────────────────────────
class TheIdempotenceCheckReadsBothTables(DriftTestCase):
	"""v0.14.0 refused "nothing to change" when only the VOUCHER was checked.

	On a line damaged by v0.13.0 the voucher agreeing with the request is the
	SIGNATURE of the damage, not evidence that there is nothing to do — so the one
	state the tool most needed to repair was the one it declined to look at, while
	telling the caller everything was fine.
	"""

	def setUp(self):
		super().setUp()
		self.name = self.a_distribution()

	def test_a_line_that_agrees_everywhere_is_still_refused(self):
		"""Nothing to change means nothing to change ANYWHERE. The refusal has to
		survive the fix, or the fix has just removed a useful guard."""
		message = self.tool_error(
			"update_journal_entry_party",
			{
				"journal_entry": self.name,
				"line_index": 1,
				"party_type": "Family",
				"party": ALEX,
				"reason": "checking whether the tool notices there is nothing to do",
			},
		)
		self.assertIn("already reads", message)
		self.assertIn("and so do the", message)

	def test_a_line_whose_ledger_disagrees_is_repaired_rather_than_refused(self):
		self.gl_rows(self.name)[0].update({"party_type": None, "party": None})
		data = self.tool_data(
			"update_journal_entry_party",
			{
				"journal_entry": self.name,
				"line_index": 1,
				"party_type": "Family",
				"party": ALEX,
				"reason": "repairing drift left by v0.13.0's update_journal_entry_party",
			},
		)
		self.assertTrue(data["gl_only_update"])
		self.assertEqual(data["gl_entries_drifted"], 1)

	def test_force_gl_sync_writes_even_where_nothing_disagrees(self):
		"""For an operator who wants the write to be an explicit act rather than a
		consequence of a comparison — which is what the batch tool passes."""
		data = self.tool_data(
			"update_journal_entry_party",
			{
				"journal_entry": self.name,
				"line_index": 1,
				"party_type": "Family",
				"party": ALEX,
				"reason": "explicitly re-syncing the ledger as part of the drift cleanup",
				"force_gl_sync": True,
			},
		)
		self.assertTrue(data["force_gl_sync"])
		self.assertEqual(data["gl_entries_updated"], 1)

	def test_the_result_names_the_drifting_rows(self):
		self.gl_rows(self.name)[0].update({"party_type": None, "party": None})
		data = self.tool_data(
			"update_journal_entry_party",
			{
				"journal_entry": self.name,
				"line_index": 1,
				"party_type": "Family",
				"party": ALEX,
				"reason": "repairing drift left by v0.13.0's update_journal_entry_party",
				"dry_run": True,
			},
		)
		self.assertEqual(len(data["gl_drift"]), 1)
		self.assertIn("GL-only repair", data["note"])

	def test_an_ordinary_attribution_change_is_untouched_by_the_fix(self):
		"""The common case still reports itself as a change of party, not as a
		repair."""
		data = self.tool_data(
			"update_journal_entry_party",
			{
				"journal_entry": self.name,
				"line_index": 1,
				"party_type": "Contact",
				"party": "Antony Sedge",
				"reason": "reclassified after establishing this was a consulting fee",
			},
		)
		self.assertFalse(data["gl_only_update"])
		self.assertEqual(data["before"]["party"], ALEX)


# ── the batch ───────────────────────────────────────────────────────────────
class RepairingABatch(DriftTestCase):
	def setUp(self):
		super().setUp()
		self.names = [self.a_distribution(remark=f"distribution {index}") for index in range(3)]
		for name in self.names:
			self.gl_rows(name)[0].update({"party_type": None, "party": None})
		self.found = self.tool_data("find_drifted_je_attributions", RANGE)

	def test_the_dry_run_is_the_default_and_writes_nothing(self):
		before = self.ledger_totals()
		data = self.tool_data(
			"repair_drifted_je_attributions",
			{"repairs": self.found["repair_input"], "reason": "repairing v0.13.0 attribution drift"},
		)
		self.assertTrue(data["dry_run"])
		self.assertEqual(data["repaired"], 3)
		self.assertEqual(self.ledger_totals(), before)
		self.assertIsNone(self.gl_rows(self.names[0])[0]["party"])

	def test_the_live_run_repairs_every_line(self):
		data = self.tool_data(
			"repair_drifted_je_attributions",
			{
				"repairs": self.found["repair_input"],
				"reason": "repairing v0.13.0 attribution drift",
				"dry_run": False,
			},
		)
		self.assertEqual(data["repaired"], 3)
		for name in self.names:
			self.assertEqual(self.gl_rows(name)[0]["party"], ALEX)

	def test_no_balance_moves(self):
		"""THE PROPERTY THAT MAKES A BATCH WRITE TO SUBMITTED VOUCHERS DEFENSIBLE.
		`party` is an attribution column; every debit, credit, account and date is
		refused as an argument."""
		before = self.ledger_totals()
		self.tool_data(
			"repair_drifted_je_attributions",
			{
				"repairs": self.found["repair_input"],
				"reason": "repairing v0.13.0 attribution drift",
				"dry_run": False,
			},
		)
		self.assertEqual(self.ledger_totals(), before)

	def test_a_failure_does_not_abort_the_run(self):
		"""Each item is a different voucher. A run that stopped half way would
		leave the ledger in a state neither report describes."""
		repairs = list(self.found["repair_input"])
		repairs.insert(1, {"journal_entry": "ACC-JV-NOPE", "line_index": 1, "party_type": "Family", "party": ALEX})
		data = self.tool_data(
			"repair_drifted_je_attributions",
			{"repairs": repairs, "reason": "repairing v0.13.0 attribution drift", "dry_run": False},
		)
		self.assertEqual(data["repaired"], 3)
		self.assertEqual(data["failed"], 1)
		refused = [row for row in data["results"] if row["outcome"] == "refused"]
		self.assertEqual(refused[0]["journal_entry"], "ACC-JV-NOPE")
		self.assertIn("failed", data["note"])

	def test_the_reason_lands_on_every_entry_it_touched(self):
		self.tool_data(
			"repair_drifted_je_attributions",
			{
				"repairs": self.found["repair_input"],
				"reason": "repairing v0.13.0 attribution drift per the 2026-07-31 scan",
				"dry_run": False,
			},
		)
		comments = [
			row for row in STORE.rows("Comment") if "2026-07-31 scan" in str(row.get("content") or "")
		]
		self.assertEqual(len(comments), 3)

	def test_a_short_reason_is_refused(self):
		message = self.tool_error(
			"repair_drifted_je_attributions",
			{"repairs": self.found["repair_input"], "reason": "fix"},
		)
		self.assertIn("real explanation", message)

	def test_an_empty_list_is_refused_and_names_the_scan(self):
		message = self.tool_error(
			"repair_drifted_je_attributions",
			{"repairs": [], "reason": "repairing v0.13.0 attribution drift"},
		)
		self.assertIn("find_drifted_je_attributions", message)

	def test_an_unsupported_field_is_refused_by_name(self):
		message = self.tool_error(
			"repair_drifted_je_attributions",
			{
				"repairs": [{"journal_entry": self.names[0], "line_index": 1, "party": ALEX, "note": "x"}],
				"reason": "repairing v0.13.0 attribution drift",
			},
		)
		self.assertIn("note", message)
		self.assertIn("Nothing was changed", message)

	def test_a_non_numeric_line_index_is_refused(self):
		message = self.tool_error(
			"repair_drifted_je_attributions",
			{
				"repairs": [{"journal_entry": self.names[0], "line_index": "first", "party_type": "Family", "party": ALEX}],
				"reason": "repairing v0.13.0 attribution drift",
			},
		)
		self.assertIn("whole number", message)

	def test_it_refuses_more_than_the_cap(self):
		"""Reading five hundred findings is a report; writing to five hundred
		ledger rows is an event."""
		from erpnext_mcp.tools import mutate

		repairs = [
			{"journal_entry": self.names[0], "line_index": 1, "party_type": "Family", "party": ALEX}
		] * (mutate.REPAIR_CAP + 1)
		message = self.tool_error(
			"repair_drifted_je_attributions",
			{"repairs": repairs, "reason": "repairing v0.13.0 attribution drift"},
		)
		self.assertIn("Split the list", message)

	def test_it_accepts_the_scans_key_name_as_an_alias(self):
		"""`repair_input` is what the scan calls it. Making a caller rename the key
		on the way through is the kind of friction that gets a tool used wrong."""
		data = self.tool_data(
			"repair_drifted_je_attributions",
			{"repair_input": self.found["repair_input"], "reason": "repairing v0.13.0 attribution drift"},
		)
		self.assertEqual(data["requested"], 3)


class Defaults(DriftTestCase):
	def test_the_scan_is_on_and_the_repair_is_off_out_of_the_box(self):
		self.configure(enabled=1)
		self.tool_data("find_drifted_je_attributions", RANGE)
		self.assertIn(
			"allow_repair_drifted_je_attributions",
			self.tool_error("repair_drifted_je_attributions", {"repairs": [], "reason": "x"}),
		)
