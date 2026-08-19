# SPDX-License-Identifier: MIT
"""The Bank Bridge consolidation — whether a year of bank data is COMPLETE.

v0.74.0. THIRTEEN CLAIMS.

 1. `TheAnchorDoctype` — the arithmetic is computed, never accepted; one anchor per period.
 2. `ChainGaps` — a missing statement is a gap, and the gap is detected rather than declared.
 3. `TheChainRead` — period order, cumulative variance, and a mask that is refused when ambiguous.
 4. `UnreconciledWorklist` — worst first by ABSOLUTE variance, and an explained period is not hidden.
 5. `VarianceBreakdown` — three sums that are never added, and a diagnosis that names which failed.
 6. `StatementLines` — a line with no transaction is a missing movement; one transaction, one line.
 7. `VarianceReasons` — an explanation is recorded and does NOT make a period reconciled.
 8. `RebuildingTheChain` — derived values only, unless somebody explicitly asks for the other thing.
 9. `Pairing` — both sides always, roles inferred, cross-company refused, one-sided reported.
10. `ReconReport` — statement, feed and ledger side by side, never summed.
11. `Advisory` — a fee that cannot be computed is null and not zero; amendment is versioning.
12. `ThePushEndpoint` — idempotent on the period, batched, gated, and audited either way.
13. `TheMetadataEndpoint` — the aggregator identity, and an id chain that survives a re-link.

THE TWO TESTS THAT MATTER MOST HERE.

`test_a_pushed_variance_is_recomputed_and_not_believed` is the first. The entire
value of a statement anchor is that the variance is ARITHMETIC rather than an
assertion — a pipe that could push its own variance could push a zero, and a
zero variance is indistinguishable from a reconciled account. Every other
property of this feature is visible in a payload; this one is only visible if
somebody tries to lie and gets corrected.

`test_rebuilding_leaves_the_three_anchored_numbers_alone` is the second. Rebuilding
a chain from the transaction feed makes every period tie out perfectly, which
looks exactly like success and destroys the only independent record the farm has.
The default has to be the safe one, and it has to be tested from the direction of
the damage rather than from the direction of the feature.
"""

import json

import frappe

from erpnext_mcp import bank as push_api
from erpnext_mcp import registry
from erpnext_mcp.tools import anchors

from .fixtures import BANK_ACCOUNT, MAIN, MAIN_ABBR, OTHER, SeededTestCase
from .harness import STORE

READ_TOOLS = (
	"get_statement_anchor_chain",
	"list_unreconciled_anchors",
	"get_anchor_variance_breakdown",
	"list_unmatched_statement_lines",
	"get_account_pairing",
	"get_statement_recon_report",
	"get_advisory_agreement_summary",
	"list_advisory_agreements",
)
WRITE_TOOLS = (
	"set_anchor_variance_reason",
	"rebuild_anchor_chain",
	"pair_bank_accounts",
	"create_advisory_agreement",
	"update_advisory_agreement",
	"create_bank_categorization_rules",
)
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS

TOOLS_ON = {f"allow_{name}": 1 for name in ALL_TOOLS}

ANCHOR = anchors.ANCHOR
AGREEMENT = "Advisory Agreement"
BANK_TRANSACTION = "Bank Transaction"

#: A second and a third account on the same company: a managed brokerage and the
#: cash-services account its trades settle through, which is the pairing the
#: whole feature is shaped around.
BROKERAGE = "Brokerage - Example Bank"
SWEEP = "Sweep - Example Bank"
#: One on the OTHER company, so "pairing across companies is refused" has
#: something real to refuse.
FOREIGN = "Operating - Other Bank"

#: Three consecutive months on the brokerage account. The first two chain
#: cleanly; the third opens where the second closed as well, so a gap only
#: appears when a test removes a period. Period two is out by exactly the
#: quarterly advisory fee, which is the case this feature was built for.
ANCHORS = (
	# (period_start, period_end, opening, transaction_sum, closing)
	("2026-01-01", "2026-01-31", 100000.00, 5000.00, 105000.00),
	("2026-02-01", "2026-02-28", 105000.00, -2000.00, 99225.19),
	("2026-03-01", "2026-03-31", 99225.19, 1000.00, 100225.19),
)


class ConsolidationTestCase(SeededTestCase):
	"""The fixture site, three bank accounts and a quarter of anchored periods."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **TOOLS_ON)
		STORE.seed(
			"Bank Account",
			[
				{
					"name": BROKERAGE,
					"account_name": "Brokerage",
					"bank": "Example Bank",
					"company": MAIN,
					"account": f"1110 - Bank Checking - {MAIN_ABBR}",
					"is_company_account": 1,
				},
				{
					"name": SWEEP,
					"account_name": "Sweep",
					"bank": "Example Bank",
					"company": MAIN,
					"is_company_account": 1,
				},
				{
					"name": FOREIGN,
					"account_name": "Operating",
					"bank": "Example Bank",
					"company": OTHER,
					"is_company_account": 1,
				},
			],
		)

	# -- fixture builders ----------------------------------------------------
	def anchor(self, **overrides):
		"""One Statement Anchor, straight through the doctype."""
		payload = {
			"doctype": ANCHOR,
			"bank_account": BROKERAGE,
			"period_start": "2026-01-01",
			"period_end": "2026-01-31",
			"anchored_opening": 100000.00,
			"transaction_sum": 5000.00,
			"anchored_closing": 105000.00,
		}
		payload.update(overrides)
		return frappe.get_doc(payload).insert()

	def quarter(self, bank_account=BROKERAGE):
		"""Three consecutive anchored months. Returns their docnames in order."""
		out = []
		for start, end, opening, moved, closing in ANCHORS:
			out.append(
				self.anchor(
					bank_account=bank_account,
					period_start=start,
					period_end=end,
					anchored_opening=opening,
					transaction_sum=moved,
					anchored_closing=closing,
				).name
			)
		return out

	def transactions(self, rows, bank_account=BROKERAGE):
		"""Bank Transactions on the account, as `(name, date, signed amount)`."""
		STORE.seed(
			BANK_TRANSACTION,
			[
				{
					"name": name,
					"date": date,
					"bank_account": bank_account,
					"company": MAIN,
					"description": description,
					"status": "Unreconciled",
					"deposit": amount if amount > 0 else 0,
					"withdrawal": -amount if amount < 0 else 0,
					"allocated_amount": 0,
					"unallocated_amount": abs(amount),
					"currency": "USD",
					"docstatus": 1,
					"payment_entries": [],
				}
				for name, date, description, amount in rows
			],
		)

	def raw(self, doctype, name):
		return STORE.get_raw(doctype, name)


# ── 1. the anchor doctype ───────────────────────────────────────────────────
class TheAnchorDoctype(ConsolidationTestCase):
	def test_the_three_derived_numbers_are_computed_from_the_two_inputs(self):
		doc = self.anchor(anchored_opening=1000, transaction_sum=-250, anchored_closing=750)
		self.assertEqual(doc.computed_closing, 750.0)
		self.assertEqual(doc.variance, 0.0)
		self.assertEqual(doc.reconciled, 1)

	def test_a_pushed_variance_is_recomputed_and_not_believed(self):
		"""THE test. A variance that can be asserted is worth nothing at all."""
		doc = self.anchor(
			anchored_opening=1000,
			transaction_sum=-250,
			anchored_closing=500,
			variance=0,
			computed_closing=500,
			reconciled=1,
		)
		self.assertEqual(doc.computed_closing, 750.0)
		self.assertEqual(doc.variance, -250.0)
		self.assertEqual(doc.reconciled, 0)

	def test_positive_is_money_in_on_both_sides_of_the_arithmetic(self):
		"""A sign flip would make every variance twice the transaction sum."""
		doc = self.anchor(anchored_opening=0, transaction_sum=500, anchored_closing=500)
		self.assertEqual(doc.computed_closing, 500.0)
		self.assertEqual(doc.variance, 0.0)

	def test_a_variance_inside_the_tolerance_still_reconciles(self):
		doc = self.anchor(anchored_opening=1000, transaction_sum=0, anchored_closing=1000.01)
		self.assertEqual(doc.variance, 0.01)
		self.assertEqual(doc.reconciled, 1)

	def test_a_cent_over_the_tolerance_does_not(self):
		doc = self.anchor(anchored_opening=1000, transaction_sum=0, anchored_closing=1000.02)
		self.assertEqual(doc.reconciled, 0)

	def test_the_company_comes_from_the_bank_account_and_is_never_typed(self):
		doc = self.anchor(company=OTHER)
		self.assertEqual(doc.company, MAIN)

	def test_two_anchors_for_one_period_are_refused(self):
		self.anchor()
		with self.assertRaises(frappe.ValidationError) as caught:
			self.anchor()
		self.assertIn("already has an anchor", str(caught.exception))

	def test_the_same_period_on_another_account_is_fine(self):
		self.anchor()
		other = self.anchor(bank_account=BANK_ACCOUNT)
		self.assertTrue(other.name)
		self.assertEqual(frappe.db.count(ANCHOR), 2)

	def test_a_period_that_ends_before_it_starts_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.anchor(period_start="2026-02-01", period_end="2026-01-31")
		self.assertIn("before Period Start", str(caught.exception))


# ── 2. chain gaps ───────────────────────────────────────────────────────────
class ChainGaps(ConsolidationTestCase):
	def test_a_clean_chain_has_no_gaps(self):
		names = self.quarter()
		self.assertEqual([self.raw(ANCHOR, name)["chain_gap_from_prior"] for name in names], [0, 0, 0])

	def test_a_period_that_does_not_follow_the_one_before_it_is_flagged(self):
		self.anchor(
			period_start="2026-01-01", period_end="2026-01-31", anchored_closing=105000, transaction_sum=5000
		)
		later = self.anchor(
			period_start="2026-02-01",
			period_end="2026-02-28",
			anchored_opening=99000,
			transaction_sum=0,
			anchored_closing=99000,
		)
		self.assertEqual(later.chain_gap_from_prior, 1)

	def test_the_flag_is_computed_and_a_pushed_value_is_discarded(self):
		"""A pipe that has not got the missing statement cannot report its absence."""
		doc = self.anchor(chain_gap_from_prior=1)
		self.assertEqual(doc.chain_gap_from_prior, 0)

	def test_the_first_period_on_an_account_is_never_a_gap(self):
		doc = self.anchor(anchored_opening=100000)
		self.assertEqual(doc.chain_gap_from_prior, 0)


# ── 3. reading the chain ────────────────────────────────────────────────────
class TheChainRead(ConsolidationTestCase):
	def chain(self, **args):
		return self.tool_data("get_statement_anchor_chain", {"bank_account": BROKERAGE, **args})

	def test_the_chain_comes_back_in_period_order(self):
		self.quarter()
		data = self.chain()
		self.assertEqual(
			[row["period_start"] for row in data["anchors"]], ["2026-01-01", "2026-02-01", "2026-03-01"]
		)

	def test_the_cumulative_variance_is_what_a_reader_checks_first(self):
		self.quarter()
		data = self.chain()
		self.assertEqual([row["variance"] for row in data["anchors"]], [0.0, -3774.81, 0.0])
		self.assertEqual([row["cumulative_variance"] for row in data["anchors"]], [0.0, -3774.81, -3774.81])
		self.assertEqual(data["cumulative_variance"], -3774.81)

	def test_the_counts_separate_reconciled_from_explained(self):
		names = self.quarter()
		data = self.chain()
		self.assertEqual(data["unreconciled_count"], 1)
		self.assertEqual(data["unexplained_count"], 1)
		self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "variance_reason": "advisory fee"})
		self.assertEqual(self.chain()["unexplained_count"], 0)
		self.assertEqual(self.chain()["unreconciled_count"], 1)

	def test_a_date_range_narrows_the_chain(self):
		self.quarter()
		data = self.chain(from_date="2026-02-01", to_date="2026-02-28")
		self.assertEqual(data["count"], 1)

	def test_an_account_can_be_found_by_its_mask(self):
		anchors.ensure_pairing_fields()
		frappe.db.set_value("Bank Account", BROKERAGE, anchors.PLAID_MASK_FIELD, "6030")
		self.quarter()
		data = self.tool_data("get_statement_anchor_chain", {"plaid_account_mask": "6030"})
		self.assertEqual(data["bank_account"], BROKERAGE)

	def test_an_ambiguous_mask_is_refused_by_name_rather_than_answered(self):
		"""A reconciliation answer for the wrong account looks exactly like a
		right one, which is why this cannot pick whichever sorted first."""
		anchors.ensure_pairing_fields()
		for account in (BROKERAGE, SWEEP):
			frappe.db.set_value("Bank Account", account, anchors.PLAID_MASK_FIELD, "6030")
		error = self.tool_error("get_statement_anchor_chain", {"plaid_account_mask": "6030"})
		self.assertIn(BROKERAGE, error)
		self.assertIn(SWEEP, error)

	def test_naming_neither_an_account_nor_a_company_is_refused(self):
		self.assertIn("not a reconciliation question", self.tool_error("get_statement_anchor_chain", {}))

	def test_a_chain_gap_is_warned_about_rather_than_buried(self):
		self.anchor(anchored_closing=105000)
		self.anchor(
			period_start="2026-03-01",
			period_end="2026-03-31",
			anchored_opening=99000,
			transaction_sum=0,
			anchored_closing=99000,
		)
		data = self.chain()
		self.assertEqual(data["chain_gap_count"], 1)
		self.assertIn("missing statement", data["warning"].lower())


# ── 4. the unreconciled worklist ────────────────────────────────────────────
class UnreconciledWorklist(ConsolidationTestCase):
	def worklist(self, **args):
		return self.tool_data("list_unreconciled_anchors", {"company": MAIN, **args})

	def test_only_the_periods_that_do_not_tie_out_are_listed(self):
		self.quarter()
		data = self.worklist()
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["anchors"][0]["period_start"], "2026-02-01")

	def test_the_worst_variance_comes_first_by_absolute_size(self):
		"""Not by the signed one: the largest overstatement and the largest
		understatement would land at opposite ends of the list."""
		self.anchor(anchored_opening=0, transaction_sum=0, anchored_closing=-9000)
		self.anchor(
			period_start="2026-02-01",
			period_end="2026-02-28",
			anchored_opening=0,
			transaction_sum=0,
			anchored_closing=500,
		)
		self.assertEqual([row["variance"] for row in self.worklist()["anchors"]], [-9000.0, 500.0])

	def test_a_tolerance_re_judges_every_period_rather_than_reading_the_flag(self):
		self.quarter()
		self.assertEqual(self.worklist(tolerance=5000)["count"], 0)
		self.assertEqual(self.worklist(tolerance=100)["count"], 1)

	def test_an_explained_period_is_still_listed_because_it_is_a_recorded_fact(self):
		names = self.quarter()
		self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "variance_reason": "advisory fee"})
		data = self.worklist()
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["unexplained_count"], 0)

	def test_the_explained_ones_can_be_dropped_when_asked(self):
		names = self.quarter()
		self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "variance_reason": "advisory fee"})
		self.assertEqual(self.worklist(include_explained=False)["count"], 0)

	def test_the_totals_are_per_account_as_well_as_overall(self):
		self.quarter()
		self.anchor(
			bank_account=BANK_ACCOUNT,
			anchored_opening=0,
			transaction_sum=0,
			anchored_closing=25,
		)
		data = self.worklist()
		self.assertEqual(data["total_variance"], round(-3774.81 + 25, 2))
		self.assertEqual(data["by_account"][BROKERAGE]["periods"], 1)
		self.assertEqual(data["by_account"][BANK_ACCOUNT]["total_variance"], 25.0)


# ── 5. the variance breakdown ───────────────────────────────────────────────
class VarianceBreakdown(ConsolidationTestCase):
	def breakdown(self, **args):
		return self.tool_data("get_anchor_variance_breakdown", args)

	def test_the_three_sums_are_reported_separately(self):
		names = self.quarter()
		self.transactions(
			[
				("BT-BRK-1", "2026-02-05", "DIVIDEND", 1000.00),
				("BT-BRK-2", "2026-02-20", "WITHDRAWAL", -3000.00),
			]
		)
		data = self.breakdown(anchor=names[1])
		self.assertEqual(data["anchored_transaction_sum"], -2000.0)
		self.assertEqual(data["ledger_transaction_sum"], -2000.0)
		self.assertEqual(data["anchor"]["variance"], -3774.81)
		self.assertEqual(data["feed_vs_statement_gap"], 0.0)

	def test_a_feed_that_disagrees_with_the_statement_is_diagnosed_as_such(self):
		names = self.quarter()
		self.transactions([("BT-BRK-1", "2026-02-05", "DIVIDEND", 1000.00)])
		data = self.breakdown(anchor=names[1])
		self.assertEqual(data["feed_vs_statement_gap"], -3000.0)
		self.assertIn("FEED problem", data["diagnosis"])

	def test_money_out_that_is_missing_is_diagnosed_as_a_deduction(self):
		names = self.quarter()
		self.transactions(
			[
				("BT-BRK-1", "2026-02-05", "DIVIDEND", 1000.00),
				("BT-BRK-2", "2026-02-20", "WITHDRAWAL", -3000.00),
			]
		)
		data = self.breakdown(anchor=names[1])
		self.assertIn("went OUT is missing", data["diagnosis"])

	def test_a_period_that_ties_out_says_so(self):
		names = self.quarter()
		self.transactions([("BT-BRK-1", "2026-01-05", "DEPOSIT", 5000.00)])
		self.assertIn("ties out", self.breakdown(anchor=names[0])["diagnosis"])

	def test_an_anchor_can_be_named_by_account_and_period(self):
		self.quarter()
		data = self.breakdown(bank_account=BROKERAGE, period_start="2026-02-01", period_end="2026-02-28")
		self.assertEqual(data["anchor"]["variance"], -3774.81)

	def test_a_period_with_no_transactions_at_all_is_warned_about(self):
		names = self.quarter()
		self.assertIn("No Bank Transactions at all", self.breakdown(anchor=names[0])["warning"])

	def test_an_unknown_anchor_is_refused_by_name(self):
		self.assertIn(
			"no Statement Anchor named",
			self.tool_error("get_anchor_variance_breakdown", {"anchor": "SA-99999"}),
		)


# ── 6. statement lines ──────────────────────────────────────────────────────
class StatementLines(ConsolidationTestCase):
	def anchor_with_lines(self, lines, **overrides):
		doc = self.anchor(**overrides)
		for line in lines:
			doc.append("statement_lines", line)
		doc.save()
		return doc

	def test_a_line_with_no_transaction_behind_it_is_the_missing_movement(self):
		self.anchor_with_lines(
			[
				{"line_date": "2026-01-05", "description": "DEPOSIT", "amount": 5000.00},
				{"line_date": "2026-01-20", "description": "ADVISORY FEE", "amount": -250.00},
			]
		)
		self.transactions([("BT-BRK-1", "2026-01-05", "DEPOSIT", 5000.00)])
		data = self.tool_data("list_unmatched_statement_lines", {"bank_account": BROKERAGE})
		self.assertEqual(data["matching"], 1)
		self.assertEqual(data["unmatched_lines"][0]["description"], "ADVISORY FEE")
		self.assertEqual(data["total_unmatched_amount"], -250.0)

	def test_one_transaction_cannot_satisfy_two_identical_lines(self):
		"""Two identical fuel purchases in one week is the ordinary case, and a
		matcher that reused one transaction would report the account complete
		while a movement was genuinely missing."""
		self.anchor_with_lines(
			[
				{"line_date": "2026-01-05", "description": "FUEL", "amount": -184.62},
				{"line_date": "2026-01-06", "description": "FUEL", "amount": -184.62},
			]
		)
		self.transactions([("BT-BRK-1", "2026-01-05", "FUEL", -184.62)])
		data = self.tool_data("list_unmatched_statement_lines", {"bank_account": BROKERAGE})
		self.assertEqual(data["matching"], 1)

	def test_a_transaction_with_no_statement_line_is_the_other_failure(self):
		self.anchor_with_lines([{"line_date": "2026-01-05", "description": "DEPOSIT", "amount": 5000.00}])
		self.transactions(
			[
				("BT-BRK-1", "2026-01-05", "DEPOSIT", 5000.00),
				("BT-BRK-2", "2026-01-09", "MYSTERY", -40.00),
			]
		)
		data = self.tool_data("list_unmatched_statement_lines", {"bank_account": BROKERAGE})
		self.assertEqual(data["matching"], 0)
		self.assertEqual(data["transactions_without_a_statement_line_count"], 1)

	def test_a_day_of_posting_drift_still_matches(self):
		self.anchor_with_lines([{"line_date": "2026-01-05", "description": "DEPOSIT", "amount": 5000.00}])
		self.transactions([("BT-BRK-1", "2026-01-06", "DEPOSIT", 5000.00)])
		self.assertEqual(
			self.tool_data("list_unmatched_statement_lines", {"bank_account": BROKERAGE})["matching"], 0
		)

	def test_no_lines_on_file_is_not_reported_as_a_clean_result(self):
		"""'Nothing is missing' and 'we have nothing to check against' are
		opposite answers, and an empty list means the first one."""
		self.quarter()
		data = self.tool_data("list_unmatched_statement_lines", {"bank_account": BROKERAGE})
		self.assertEqual(data["anchors_without_lines"], 3)
		self.assertIn("NOT a clean result", data["warning"])

	def test_listing_the_lines_writes_nothing(self):
		doc = self.anchor_with_lines(
			[{"line_date": "2026-01-05", "description": "DEPOSIT", "amount": 5000.00}]
		)
		self.transactions([("BT-BRK-1", "2026-01-05", "DEPOSIT", 5000.00)])
		self.tool_data("list_unmatched_statement_lines", {"bank_account": BROKERAGE})
		row = self.raw(ANCHOR, doc.name)
		self.assertFalse(row["statement_lines"][0].get("matched_bank_transaction"))


# ── 7. variance reasons ─────────────────────────────────────────────────────
class VarianceReasons(ConsolidationTestCase):
	def test_an_explanation_is_recorded_against_the_period(self):
		names = self.quarter()
		data = self.tool_data(
			"set_anchor_variance_reason",
			{"anchor": names[1], "variance_reason": "Quarterly advisory fee 3774.81"},
		)
		self.assertEqual(data["variance_reason"], "Quarterly advisory fee 3774.81")
		self.assertEqual(self.raw(ANCHOR, names[1])["variance_reason"], "Quarterly advisory fee 3774.81")

	def test_an_explanation_does_not_make_a_period_reconciled(self):
		"""`reconciled` is arithmetic. A sentence beside it is a human judgement,
		and neither overwrites the other."""
		names = self.quarter()
		self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "variance_reason": "fee"})
		self.assertEqual(self.raw(ANCHOR, names[1])["reconciled"], 0)

	def test_replacing_an_explanation_says_what_it_replaced(self):
		names = self.quarter()
		self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "variance_reason": "first"})
		data = self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "variance_reason": "second"})
		self.assertIn("first", data["warning"])

	def test_an_explanation_can_be_cleared(self):
		names = self.quarter()
		self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "variance_reason": "fee"})
		self.tool_data("set_anchor_variance_reason", {"anchor": names[1], "clear": True})
		self.assertFalse(self.raw(ANCHOR, names[1])["variance_reason"])

	def test_setting_and_clearing_at_once_is_a_contradiction(self):
		names = self.quarter()
		self.assertIn(
			"contradiction",
			self.tool_error(
				"set_anchor_variance_reason",
				{"anchor": names[1], "variance_reason": "fee", "clear": True},
			),
		)

	def test_an_empty_reason_is_refused(self):
		names = self.quarter()
		self.assertIn(
			"variance_reason is required",
			self.tool_error("set_anchor_variance_reason", {"anchor": names[1]}),
		)


# ── 8. rebuilding the chain ─────────────────────────────────────────────────
class RebuildingTheChain(ConsolidationTestCase):
	def test_rebuilding_leaves_the_three_anchored_numbers_alone(self):
		"""THE other test. Rebuilding from the feed makes every period tie out
		perfectly, which looks like success and destroys the independent record."""
		names = self.quarter()
		self.transactions([("BT-BRK-1", "2026-02-05", "DIVIDEND", 1000.00)])
		self.tool_data("rebuild_anchor_chain", {"bank_account": BROKERAGE})
		row = self.raw(ANCHOR, names[1])
		self.assertEqual(row["transaction_sum"], -2000.0)
		self.assertEqual(row["anchored_opening"], 105000.0)
		self.assertEqual(row["anchored_closing"], 99225.19)
		self.assertEqual(row["variance"], -3774.81)

	def test_a_stale_gap_flag_from_an_out_of_order_insert_is_corrected(self):
		"""The reason this tool exists: a chain built one anchor at a time gets
		the gap flags wrong whenever a statement arrives late."""
		later = self.anchor(
			period_start="2026-02-01",
			period_end="2026-02-28",
			anchored_opening=105000.00,
			transaction_sum=-2000.00,
			anchored_closing=99225.19,
		)
		self.assertEqual(later.chain_gap_from_prior, 0)
		self.anchor(
			period_start="2026-01-01",
			period_end="2026-01-31",
			anchored_opening=100000.00,
			transaction_sum=5000.00,
			anchored_closing=90000.00,
		)
		self.assertEqual(self.raw(ANCHOR, later.name)["chain_gap_from_prior"], 0)

		self.tool_data("rebuild_anchor_chain", {"bank_account": BROKERAGE})
		self.assertEqual(self.raw(ANCHOR, later.name)["chain_gap_from_prior"], 1)

	def test_recomputing_from_the_feed_is_opt_in_and_warns_loudly(self):
		names = self.quarter()
		self.transactions([("BT-BRK-1", "2026-02-05", "DIVIDEND", 1000.00)])
		data = self.tool_data(
			"rebuild_anchor_chain", {"bank_account": BROKERAGE, "recompute_transaction_sum": True}
		)
		self.assertIn("the record of it did", data["warning"])
		row = self.raw(ANCHOR, names[1])
		self.assertEqual(row["transaction_sum"], 1000.0)
		self.assertEqual(row["anchored_closing"], 99225.19)

	def test_a_dry_run_writes_nothing(self):
		later = self.anchor(
			period_start="2026-02-01",
			period_end="2026-02-28",
			anchored_opening=105000.00,
			transaction_sum=-2000.00,
			anchored_closing=99225.19,
		)
		self.anchor(anchored_closing=90000.00)
		data = self.tool_data("rebuild_anchor_chain", {"bank_account": BROKERAGE, "dry_run": True})
		self.assertIn("NOTHING was written", data["warning_dry_run"])
		self.assertEqual(self.raw(ANCHOR, later.name)["chain_gap_from_prior"], 0)

	def test_rebuilding_writes_the_line_matches_down(self):
		doc = self.anchor()
		doc.append(
			"statement_lines", {"line_date": "2026-01-05", "description": "DEPOSIT", "amount": 5000.00}
		)
		doc.save()
		self.transactions([("BT-BRK-1", "2026-01-05", "DEPOSIT", 5000.00)])
		data = self.tool_data("rebuild_anchor_chain", {"bank_account": BROKERAGE})
		self.assertEqual(data["statement_lines_matched"], 1)
		self.assertEqual(
			self.raw(ANCHOR, doc.name)["statement_lines"][0]["matched_bank_transaction"], "BT-BRK-1"
		)

	def test_an_account_with_no_anchors_is_refused_by_name(self):
		self.assertIn(
			"no Statement Anchors in that range",
			self.tool_error("rebuild_anchor_chain", {"bank_account": SWEEP}),
		)

	def test_rebuilding_posts_nothing(self):
		self.quarter()
		before = len(STORE.rows("GL Entry")), len(STORE.rows("Journal Entry"))
		self.tool_data("rebuild_anchor_chain", {"bank_account": BROKERAGE})
		self.assertEqual((len(STORE.rows("GL Entry")), len(STORE.rows("Journal Entry"))), before)


# ── 9. account pairing ──────────────────────────────────────────────────────
class Pairing(ConsolidationTestCase):
	def pair(self, **args):
		return self.tool_data(
			"pair_bank_accounts",
			{"bank_account": BROKERAGE, "paired_bank_account": SWEEP, **args},
		)

	def test_pairing_writes_both_sides(self):
		self.pair()
		self.assertEqual(frappe.db.get_value("Bank Account", BROKERAGE, anchors.PAIRED_FIELD), SWEEP)
		self.assertEqual(frappe.db.get_value("Bank Account", SWEEP, anchors.PAIRED_FIELD), BROKERAGE)

	def test_naming_one_role_names_the_other(self):
		"""The pair has exactly two roles, and a pairing where only one side says
		what it is cannot answer 'which of these is the brokerage' from the other."""
		self.pair(pairing_type="Brokerage")
		self.assertEqual(
			frappe.db.get_value("Bank Account", SWEEP, anchors.PAIRING_TYPE_FIELD), "Cash Services"
		)

	def test_both_sides_claiming_one_role_is_refused(self):
		self.assertIn(
			"the pair has two roles",
			self.tool_error(
				"pair_bank_accounts",
				{
					"bank_account": BROKERAGE,
					"paired_bank_account": SWEEP,
					"pairing_type": "Brokerage",
					"paired_pairing_type": "Brokerage",
				},
			),
		)

	def test_pairing_across_companies_is_refused(self):
		self.assertIn(
			"another's reconciliation",
			self.tool_error(
				"pair_bank_accounts", {"bank_account": BROKERAGE, "paired_bank_account": FOREIGN}
			),
		)

	def test_an_account_cannot_be_its_own_companion(self):
		self.assertIn(
			"its own companion",
			self.tool_error(
				"pair_bank_accounts", {"bank_account": BROKERAGE, "paired_bank_account": BROKERAGE}
			),
		)

	def test_repointing_an_existing_pairing_is_refused_without_replace(self):
		self.pair()
		self.assertIn(
			"replace=true",
			self.tool_error(
				"pair_bank_accounts",
				{"bank_account": BROKERAGE, "paired_bank_account": BANK_ACCOUNT},
			),
		)

	def test_replace_names_the_account_left_with_no_companion(self):
		self.pair()
		data = self.tool_data(
			"pair_bank_accounts",
			{"bank_account": BROKERAGE, "paired_bank_account": BANK_ACCOUNT, "replace": True},
		)
		self.assertEqual(data["orphaned"][0]["bank_account"], SWEEP)
		self.assertFalse(frappe.db.get_value("Bank Account", SWEEP, anchors.PAIRED_FIELD))

	def test_the_topology_read_reports_a_one_sided_pairing(self):
		anchors.ensure_pairing_fields()
		frappe.db.set_value("Bank Account", BROKERAGE, anchors.PAIRED_FIELD, SWEEP)
		data = self.tool_data("get_account_pairing", {"company": MAIN})
		self.assertEqual(len(data["one_sided_pairings"]), 1)
		self.assertIn("does not point back", data["one_sided_pairings"][0]["why"])

	def test_the_topology_read_counts_anchored_periods_per_account(self):
		self.quarter()
		data = self.tool_data("get_account_pairing", {"company": MAIN})
		by_name = {row["name"]: row for row in data["accounts"]}
		self.assertEqual(by_name[BROKERAGE]["anchored_periods"], 3)
		self.assertEqual(by_name[SWEEP]["anchored_periods"], 0)

	def test_the_topology_read_is_scoped_to_one_company(self):
		names = {row["name"] for row in self.tool_data("get_account_pairing", {"company": MAIN})["accounts"]}
		self.assertNotIn(FOREIGN, names)

	def test_pairing_changes_no_transaction_and_no_anchor(self):
		self.quarter()
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.pair()
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		for doctype in (BANK_TRANSACTION, ANCHOR):
			self.assertEqual(before.get(doctype), after.get(doctype))


# ── 10. the recon report ────────────────────────────────────────────────────
class ReconReport(ConsolidationTestCase):
	def report(self, **args):
		return self.tool_data("get_statement_recon_report", {"bank_account": BROKERAGE, **args})

	def test_the_three_measurements_are_reported_and_never_summed(self):
		self.quarter()
		self.transactions([("BT-BRK-1", "2026-01-10", "DEPOSIT", 5000.00)])
		data = self.report()
		self.assertEqual(data["statement_total"], 4000.0)
		self.assertEqual(data["feed_total"], 5000.0)
		self.assertEqual(data["statement_vs_feed_total"], -1000.0)
		self.assertNotIn("grand_total", data)

	def test_a_statement_the_feed_does_not_match_is_warned_about(self):
		self.quarter()
		data = self.report()
		self.assertEqual(data["periods_where_statement_and_feed_disagree"], 3)
		self.assertIn("do not add up to", data["warning"])

	def test_the_ledger_movement_comes_from_the_gl_account_the_bank_account_names(self):
		self.quarter()
		data = self.report()
		self.assertEqual(data["periods"][0]["gl_account"], f"1110 - Bank Checking - {MAIN_ABBR}")

	def test_an_account_with_no_gl_account_reports_null_rather_than_zero(self):
		"""A bank account registered for a feed before anybody decided where it
		posts is ordinary, and a movement of zero would claim the ledger
		disagrees with the feed by the whole month."""
		self.anchor(bank_account=SWEEP)
		data = self.report(bank_account=SWEEP)
		self.assertIsNone(data["periods"][0]["gl_account"])
		self.assertIsNone(data["periods"][0]["feed_vs_ledger"])

	def test_the_category_breakdown_names_what_is_uncategorised(self):
		self.quarter()
		self.transactions([("BT-BRK-1", "2026-01-10", "DEPOSIT", 5000.00)])
		data = self.report()
		self.assertIn("(uncategorised)", data["by_category"])
		self.assertEqual(data["by_category"]["(uncategorised)"]["count"], 1)

	def test_a_range_with_no_anchors_is_refused_rather_than_answered_empty(self):
		self.assertIn(
			"no Statement Anchors in that range",
			self.tool_error("get_statement_recon_report", {"bank_account": SWEEP}),
		)


# ── 11. advisory agreements ─────────────────────────────────────────────────
class Advisory(ConsolidationTestCase):
	def agreement(self, **overrides):
		payload = {
			"company": MAIN,
			"agreement_name": "OML Managed Account",
			"bank_account": BROKERAGE,
			"client_entity": "Example Trading Co",
			"advisor_entity": "Example Advisors LLC",
			"fee_type": "Percent of AUM",
			"fee_percent_of_aum": 1.0,
			"billing_frequency": "Quarterly",
			"effective_date": "2026-01-01",
		}
		payload.update(overrides)
		return self.tool_data("create_advisory_agreement", payload)

	def test_an_agreement_comes_back_whole(self):
		data = self.agreement()
		self.assertEqual(data["agreement"]["fee_percent_of_aum"], 1.0)
		self.assertEqual(data["agreement"]["status"], "Active")
		self.assertEqual(data["annual_fee_at_1m"], 10000.0)

	def test_a_percent_fee_with_no_percentage_is_refused(self):
		"""It would compute to zero, which looks exactly like an account managed
		for free."""
		self.assertIn(
			"Fee Percent of AUM is required",
			self.tool_error(
				"create_advisory_agreement",
				{
					"company": MAIN,
					"agreement_name": "x",
					"effective_date": "2026-01-01",
					"fee_type": "Percent of AUM",
				},
			),
		)

	def test_a_hybrid_missing_half_of_itself_is_refused(self):
		self.assertIn(
			"Fee Flat Annual is required",
			self.tool_error(
				"create_advisory_agreement",
				{
					"company": MAIN,
					"agreement_name": "x",
					"effective_date": "2026-01-01",
					"fee_type": "Hybrid",
					"fee_percent_of_aum": 1.0,
				},
			),
		)

	def test_a_second_active_agreement_on_one_account_is_refused(self):
		self.agreement()
		self.assertIn(
			"already has an Active advisory agreement",
			self.tool_error(
				"create_advisory_agreement",
				{
					"company": MAIN,
					"agreement_name": "Another",
					"bank_account": BROKERAGE,
					"effective_date": "2026-06-01",
					"fee_percent_of_aum": 1.5,
				},
			),
		)

	def test_an_account_from_another_company_is_refused(self):
		self.assertIn(
			f"belongs to {OTHER}",
			self.tool_error(
				"create_advisory_agreement",
				{
					"company": MAIN,
					"agreement_name": "x",
					"bank_account": FOREIGN,
					"effective_date": "2026-01-01",
					"fee_percent_of_aum": 1.0,
				},
			),
		)

	def test_the_fee_is_computed_against_the_anchored_portfolio_value(self):
		self.agreement()
		self.anchor(portfolio_closing_value=2_000_000.00)
		data = self.tool_data(
			"get_advisory_agreement_summary", {"agreement": "OML Managed Account", "company": MAIN}
		)
		self.assertEqual(data["assets_under_management"], 2_000_000.0)
		self.assertIn("portfolio_closing_value", data["aum_source"])
		self.assertEqual(data["computed_annual_fee"], 20000.0)
		self.assertEqual(data["computed_fee_per_billing_period"], 5000.0)

	def test_a_fee_that_cannot_be_computed_is_null_and_not_zero(self):
		"""A fee of zero and a fee nobody can compute are opposite findings."""
		self.agreement()
		data = self.tool_data(
			"get_advisory_agreement_summary", {"agreement": "OML Managed Account", "company": MAIN}
		)
		self.assertIsNone(data["assets_under_management"])
		self.assertIsNone(data["computed_annual_fee"])
		self.assertIn("bank balance is deliberately NOT used", data["warning"])

	def test_a_supplied_aum_says_that_it_was_supplied(self):
		self.agreement()
		data = self.tool_data(
			"get_advisory_agreement_summary",
			{"agreement": "OML Managed Account", "company": MAIN, "assets_under_management": 500000},
		)
		self.assertEqual(data["computed_annual_fee"], 5000.0)
		self.assertEqual(data["aum_source"], "supplied by the caller")

	def test_amending_creates_a_version_and_supersedes_the_old_one(self):
		created = self.agreement()
		data = self.tool_data(
			"update_advisory_agreement",
			{
				"agreement": created["name"],
				"fee_percent_of_aum": 0.85,
				"amendment_reason": "Renegotiated at renewal",
			},
		)
		self.assertNotEqual(data["name"], created["name"])
		self.assertEqual(data["agreement"]["amended_from"], created["name"])
		self.assertEqual(data["agreement"]["fee_percent_of_aum"], 0.85)
		self.assertEqual(frappe.db.get_value(AGREEMENT, created["name"], "status"), "Superseded")

	def test_an_amendment_without_a_reason_is_refused(self):
		created = self.agreement()
		self.assertIn(
			"amendment_reason is required",
			self.tool_error(
				"update_advisory_agreement", {"agreement": created["name"], "fee_percent_of_aum": 2.0}
			),
		)

	def test_a_failed_amendment_leaves_the_original_active(self):
		"""The prior agreement is superseded before the new one is inserted, so a
		refusal has to put it back."""
		created = self.agreement()
		self.tool_error(
			"update_advisory_agreement",
			{
				"agreement": created["name"],
				"fee_type": "Flat Annual",
				"fee_percent_of_aum": 0,
				"amendment_reason": "should fail: no flat amount",
			},
		)
		self.assertEqual(frappe.db.get_value(AGREEMENT, created["name"], "status"), "Active")

	def test_the_history_walks_the_whole_chain(self):
		created = self.agreement()
		self.tool_data(
			"update_advisory_agreement",
			{"agreement": created["name"], "fee_percent_of_aum": 0.85, "amendment_reason": "renewal"},
		)
		data = self.tool_data(
			"get_advisory_agreement_summary", {"agreement": "OML Managed Account", "company": MAIN}
		)
		self.assertEqual(len(data["amendment_history"]), 2)
		self.assertEqual(data["amendment_count"], 1)
		self.assertEqual(data["amendment_history"][0]["fee_percent_of_aum"], 1.0)

	def test_correcting_a_description_in_place_creates_no_version(self):
		created = self.agreement()
		data = self.tool_data(
			"update_advisory_agreement",
			{"agreement": created["name"], "client_entity": "Example Trading Co.", "in_place": True},
		)
		self.assertEqual(data["name"], created["name"])
		self.assertEqual(frappe.db.count(AGREEMENT), 1)

	def test_changing_a_term_in_place_is_refused(self):
		created = self.agreement()
		self.assertIn(
			"those are TERMS",
			self.tool_error(
				"update_advisory_agreement",
				{"agreement": created["name"], "fee_percent_of_aum": 2.0, "in_place": True},
			),
		)

	def test_terminating_needs_a_date_rather_than_defaulting_to_today(self):
		created = self.agreement()
		self.assertIn(
			"termination_date is required",
			self.tool_error("update_advisory_agreement", {"agreement": created["name"], "terminate": True}),
		)
		data = self.tool_data(
			"update_advisory_agreement",
			{
				"agreement": created["name"],
				"terminate": True,
				"termination_date": "2026-03-31",
			},
		)
		self.assertEqual(data["agreement"]["status"], "Terminated")
		self.assertEqual(frappe.db.count(AGREEMENT), 1)

	def test_a_superseded_agreement_cannot_be_amended_again(self):
		created = self.agreement()
		self.tool_data(
			"update_advisory_agreement",
			{"agreement": created["name"], "fee_percent_of_aum": 0.85, "amendment_reason": "renewal"},
		)
		self.assertIn(
			"already been superseded",
			self.tool_error(
				"update_advisory_agreement",
				{"agreement": created["name"], "fee_percent_of_aum": 0.5, "amendment_reason": "again"},
			),
		)

	def test_the_register_names_the_managed_accounts_with_no_agreement(self):
		anchors.ensure_pairing_fields()
		frappe.db.set_value("Bank Account", BROKERAGE, anchors.PLAID_TYPE_FIELD, "investment")
		data = self.tool_data("list_advisory_agreements", {"company": MAIN})
		self.assertEqual(data["accounts_without_an_agreement"], [BROKERAGE])
		self.agreement()
		self.assertEqual(
			self.tool_data("list_advisory_agreements", {"company": MAIN})["accounts_without_an_agreement"],
			[],
		)

	def test_creating_an_agreement_posts_nothing(self):
		before = len(STORE.rows("GL Entry")), len(STORE.rows("Journal Entry"))
		self.agreement()
		self.assertEqual((len(STORE.rows("GL Entry")), len(STORE.rows("Journal Entry"))), before)


# ── 12. the push endpoint ───────────────────────────────────────────────────
class ThePushEndpoint(ConsolidationTestCase):
	def push(self, **payload):
		return push_api.push_statement_anchor(**payload)

	def january(self, **overrides):
		payload = {
			"bank_account": BROKERAGE,
			"period_start": "2026-01-01",
			"period_end": "2026-01-31",
			"anchored_opening": 100000.00,
			"transaction_sum": 5000.00,
			"anchored_closing": 105000.00,
			"parser_version": "2.4.1",
		}
		payload.update(overrides)
		return payload

	def test_a_push_creates_the_anchor_and_computes_its_arithmetic(self):
		result = self.push(**self.january(anchored_closing=104000.00))
		self.assertEqual(result["created_count"], 1)
		anchor = result["created"][0]
		self.assertEqual(anchor["computed_closing"], 105000.0)
		self.assertEqual(anchor["variance"], -1000.0)
		self.assertFalse(anchor["reconciled"])

	def test_pushing_the_same_period_twice_updates_rather_than_duplicates(self):
		"""A pipe retries, a parse is re-run, an operator syncs by hand. Two
		anchors for one October is two answers to whether October tied out."""
		self.push(**self.january())
		result = self.push(**self.january(anchored_closing=104000.00))
		self.assertEqual(result["created_count"], 0)
		self.assertEqual(result["updated_count"], 1)
		self.assertEqual(frappe.db.count(ANCHOR), 1)
		self.assertEqual(result["updated"][0]["variance"], -1000.0)

	def test_a_batch_pushes_a_whole_history_in_one_call(self):
		rows = [
			{
				"bank_account": BROKERAGE,
				"period_start": start,
				"period_end": end,
				"anchored_opening": opening,
				"transaction_sum": moved,
				"anchored_closing": closing,
			}
			for start, end, opening, moved, closing in ANCHORS
		]
		result = self.push(anchors_batch=rows)
		self.assertEqual(result["created_count"], 3)
		self.assertEqual(frappe.db.count(ANCHOR), 3)

	def test_one_bad_row_does_not_lose_the_others(self):
		rows = [
			{
				"bank_account": BROKERAGE,
				"period_start": "2026-01-01",
				"period_end": "2026-01-31",
				"anchored_opening": 1,
				"anchored_closing": 1,
			},
			{"bank_account": "No Such Account", "period_start": "2026-02-01", "period_end": "2026-02-28"},
		]
		result = self.push(anchors_batch=rows)
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(result["failed_count"], 1)
		self.assertIn("No Such Account", result["failed"][0]["error"])

	def test_a_batch_can_arrive_as_json_text(self):
		payload = json.dumps(
			[
				{
					"bank_account": BROKERAGE,
					"period_start": "2026-01-01",
					"period_end": "2026-01-31",
					"anchored_opening": 1,
					"anchored_closing": 1,
				}
			]
		)
		self.assertEqual(self.push(anchors_batch=payload)["created_count"], 1)

	def test_statement_lines_are_replaced_rather_than_appended(self):
		"""A re-parse produces the same lines again, and appending would double
		a month of them — after which the unmatched count is quietly wrong."""
		lines = [{"line_date": "2026-01-05", "description": "DEPOSIT", "amount": 5000.00}]
		first = self.push(**self.january(statement_lines=lines))
		self.push(**self.january(statement_lines=lines))
		row = self.raw(ANCHOR, first["created"][0]["name"])
		self.assertEqual(len(row["statement_lines"]), 1)

	def test_a_push_that_says_nothing_about_lines_leaves_them_alone(self):
		lines = [{"line_date": "2026-01-05", "description": "DEPOSIT", "amount": 5000.00}]
		first = self.push(**self.january(statement_lines=lines))
		self.push(**self.january(anchored_closing=104000.00))
		row = self.raw(ANCHOR, first["created"][0]["name"])
		self.assertEqual(len(row["statement_lines"]), 1)

	def test_a_pushed_explanation_lands_on_an_anchor_that_has_none(self):
		"""Which is what makes a one-time migration of a pipe's own tags work."""
		result = self.push(**self.january(variance_reason="advisory fee, not in the feed"))
		self.assertEqual(result["created"][0]["variance_reason"], "advisory fee, not in the feed")

	def test_a_later_push_never_overwrites_a_human_explanation(self):
		"""A sync running every night would otherwise erase the sentence
		somebody wrote, on the next run, silently."""
		created = self.push(**self.january(anchored_closing=104000.00))
		self.tool_data(
			"set_anchor_variance_reason",
			{"anchor": created["created"][0]["name"], "variance_reason": "advisory fee, not in the feed"},
		)
		self.push(**self.january(anchored_closing=104000.00, variance_reason="unexplained"))
		self.assertEqual(
			self.raw(ANCHOR, created["created"][0]["name"])["variance_reason"],
			"advisory fee, not in the feed",
		)

	def test_an_unknown_bank_account_is_refused_rather_than_created(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.push(**self.january(bank_account="Nobody's Account"))
		self.assertIn("no Bank Account named", str(caught.exception))
		self.assertEqual(frappe.db.count("Bank Account"), 4)

	def test_a_push_with_no_period_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.push(bank_account=BROKERAGE, anchored_opening=1)
		self.assertIn("an anchor IS a period", str(caught.exception))

	def test_guest_never_gets_past_the_first_line(self):
		frappe.local.session.user = "Guest"
		try:
			with self.assertRaises(frappe.PermissionError):
				self.push(**self.january())
		finally:
			frappe.local.session.user = "Administrator"
		self.assertEqual(frappe.db.count(ANCHOR), 0)

	def test_a_credential_without_write_permission_is_refused(self):
		STORE.denied_permissions.add((ANCHOR, "write"))
		with self.assertRaises(frappe.PermissionError):
			self.push(**self.january())
		self.assertEqual(frappe.db.count(ANCHOR), 0)

	def test_every_push_writes_an_audit_row(self):
		self.push(**self.january())
		self.assertAudited("push:push_statement_anchor", "Success")

	def test_a_refused_push_writes_one_too(self):
		"""An operator asking 'which credential pushed the October anchor' must
		not get silence for the calls that failed."""
		with self.assertRaises(frappe.ValidationError):
			self.push(**self.january(bank_account="Nobody's Account"))
		self.assertAudited("push:push_statement_anchor", "Error")

	def test_pairing_metadata_is_pushed_onto_the_bank_account(self):
		result = push_api.push_account_pairing(
			bank_account=BROKERAGE,
			plaid_account_id="acc_9f21",
			plaid_account_mask="9401",
			plaid_account_type="investment",
			plaid_account_subtype="brokerage",
			sync_enabled=1,
		)
		self.assertEqual(result["updated_count"], 1)
		account = result["updated"][0]["account"]
		self.assertEqual(account["plaid_account_mask"], "9401")
		self.assertTrue(account["sync_enabled"])

	def test_only_the_keys_actually_sent_are_written(self):
		"""A pipe that knows the mask and not the subtype must not blank a
		subtype somebody typed."""
		push_api.push_account_pairing(bank_account=BROKERAGE, plaid_account_subtype="brokerage")
		push_api.push_account_pairing(bank_account=BROKERAGE, plaid_account_mask="9401")
		self.assertEqual(
			frappe.db.get_value("Bank Account", BROKERAGE, anchors.PLAID_SUBTYPE_FIELD), "brokerage"
		)

	def test_a_pushed_pairing_writes_both_sides(self):
		push_api.push_account_pairing(
			bank_account=BROKERAGE, paired_bank_account=SWEEP, pairing_type="Brokerage"
		)
		self.assertEqual(frappe.db.get_value("Bank Account", SWEEP, anchors.PAIRED_FIELD), BROKERAGE)

	def test_an_unknown_plaid_account_type_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			push_api.push_account_pairing(bank_account=BROKERAGE, plaid_account_type="chequing")
		self.assertIn("plaid_account_type must be one of", str(caught.exception))


# ── 13. the metadata endpoint and the id chain ──────────────────────────────
class TheMetadataEndpoint(ConsolidationTestCase):
	"""What a sync says about an account, and what happens when the bank re-links.

	THE TEST THAT MATTERS MOST HERE is
	`test_a_new_aggregator_id_does_not_erase_the_one_it_replaced`. Overwriting the
	id is correct — the new one is live — and it is also the failure: a year of
	stored feed rows, the aggregator's own support logs and the pipe's history all
	name the dead id, and nothing else connects them to this account once it is
	gone. The overwrite looks like a successful sync from every direction, which
	is why the chain has to be asserted rather than trusted.
	"""

	def push(self, **payload):
		return push_api.push_account_metadata(**payload)

	def stored(self, fieldname, bank_account=BROKERAGE):
		return frappe.db.get_value("Bank Account", bank_account, fieldname)

	def history(self, bank_account=BROKERAGE):
		return anchors.id_history(self.stored(anchors.PLAID_ID_HISTORY_FIELD, bank_account))

	# -- the metadata itself -------------------------------------------------
	def test_a_push_writes_every_metadata_field_onto_the_bank_account(self):
		"""The gap this release closes: a push that reported success and left
		the columns null."""
		result = self.push(
			bank_account=BROKERAGE,
			plaid_account_id="jN7xBz83JaHPyQ5k1oVJSVeeraMO63tRE55ek",
			plaid_account_mask="6030",
			plaid_account_type="depository",
			plaid_account_subtype="checking",
			sync_enabled=1,
		)
		self.assertEqual(result["updated_count"], 1)
		self.assertEqual(self.stored(anchors.PLAID_ID_FIELD), "jN7xBz83JaHPyQ5k1oVJSVeeraMO63tRE55ek")
		self.assertEqual(self.stored(anchors.PLAID_MASK_FIELD), "6030")
		self.assertEqual(self.stored(anchors.PLAID_TYPE_FIELD), "depository")
		self.assertEqual(self.stored(anchors.PLAID_SUBTYPE_FIELD), "checking")
		self.assertEqual(self.stored(anchors.SYNC_FIELD), 1)

	def test_the_reply_echoes_the_account_as_it_now_reads(self):
		"""The pipe verifies its push against this echo rather than the status
		line, so an echo that did not reflect the write would defeat the check."""
		account = self.push(bank_account=BROKERAGE, plaid_account_mask="6030")["updated"][0]["account"]
		self.assertEqual(account["plaid_account_mask"], "6030")
		self.assertEqual(account["plaid_account_id_history"], [])

	def test_only_the_keys_actually_sent_are_written(self):
		self.push(bank_account=BROKERAGE, plaid_account_subtype="brokerage")
		self.push(bank_account=BROKERAGE, plaid_account_mask="9401")
		self.assertEqual(self.stored(anchors.PLAID_SUBTYPE_FIELD), "brokerage")

	def test_sync_enabled_off_is_a_value_and_not_an_absence(self):
		"""'This account is no longer pulled' is precisely the fact that must
		survive the trip."""
		self.push(bank_account=BROKERAGE, sync_enabled=1)
		self.push(bank_account=BROKERAGE, sync_enabled=0)
		self.assertEqual(self.stored(anchors.SYNC_FIELD), 0)

	def test_an_account_can_be_found_by_its_mask(self):
		self.push(bank_account=BROKERAGE, plaid_account_mask="6030")
		self.push(bank_account="6030", plaid_account_subtype="checking")
		self.assertEqual(self.stored(anchors.PLAID_SUBTYPE_FIELD), "checking")

	def test_a_mask_two_accounts_share_is_refused_rather_than_guessed(self):
		self.push(bank_account=BROKERAGE, plaid_account_mask="6030")
		self.push(bank_account=SWEEP, plaid_account_mask="6030")
		with self.assertRaises(frappe.ValidationError) as caught:
			self.push(bank_account="6030", plaid_account_subtype="checking")
		self.assertIn("Name one of them", str(caught.exception))

	# -- the id chain across reconnections -----------------------------------
	def test_a_new_aggregator_id_does_not_erase_the_one_it_replaced(self):
		old = "ZE4ZoOpA5bUKEkPd5Lb8hDMg4b7e84Ubx7Jeb"
		new = "jN7xBz83JaHPyQ5k1oVJSVeeraMO63tRE55ek"
		self.push(bank_account=BROKERAGE, plaid_account_id=old)
		result = self.push(bank_account=BROKERAGE, plaid_account_id=new)
		self.assertEqual(self.stored(anchors.PLAID_ID_FIELD), new)
		self.assertEqual(self.history(), [old])
		self.assertEqual(result["repointed"], [{"bank_account": BROKERAGE, "was": old, "now": new}])

	def test_the_first_id_ever_pushed_supersedes_nothing(self):
		result = self.push(bank_account=BROKERAGE, plaid_account_id="acc_first")
		self.assertEqual(self.history(), [])
		self.assertEqual(result["repointed_count"], 0)

	def test_pushing_the_same_id_every_night_appends_nothing(self):
		"""A sync runs whether or not anything changed; a history that grew on
		every run would be a log of syncs rather than a chain of identities."""
		for _ in range(3):
			self.push(bank_account=BROKERAGE, plaid_account_id="acc_stable", plaid_account_mask="6030")
		self.assertEqual(self.history(), [])

	def test_several_reconnections_chain_oldest_first(self):
		for identifier in ("acc_one", "acc_two", "acc_three", "acc_four"):
			self.push(bank_account=BROKERAGE, plaid_account_id=identifier)
		self.assertEqual(self.stored(anchors.PLAID_ID_FIELD), "acc_four")
		self.assertEqual(self.history(), ["acc_one", "acc_two", "acc_three"])

	def test_an_id_that_becomes_current_again_leaves_the_history(self):
		"""A re-link can land back on a connection the account already had. An
		id that is both current and superseded reads as two accounts to anything
		matching on it."""
		for identifier in ("acc_one", "acc_two", "acc_one"):
			self.push(bank_account=BROKERAGE, plaid_account_id=identifier)
		self.assertEqual(self.stored(anchors.PLAID_ID_FIELD), "acc_one")
		self.assertEqual(self.history(), ["acc_two"])

	def test_the_history_is_capped_and_drops_the_oldest(self):
		ids = [f"acc_{index}" for index in range(anchors.MAX_ID_HISTORY + 5)]
		for identifier in ids:
			self.push(bank_account=BROKERAGE, plaid_account_id=identifier)
		history = self.history()
		self.assertEqual(len(history), anchors.MAX_ID_HISTORY)
		self.assertEqual(history[-1], ids[-2])
		self.assertNotIn(ids[0], history)

	def test_a_pairing_push_keeps_the_chain_too(self):
		"""One implementation under both endpoints — a pipe that repoints
		through push_account_pairing must not lose what the other would keep."""
		push_api.push_account_pairing(bank_account=BROKERAGE, plaid_account_id="acc_one")
		push_api.push_account_pairing(bank_account=BROKERAGE, plaid_account_id="acc_two")
		self.assertEqual(self.history(), ["acc_one"])

	def test_a_hand_typed_id_in_the_history_column_is_not_parsed_away(self):
		"""Somebody who typed a dead id into the field answered the same
		question this column asks."""
		frappe.db.set_value(
			"Bank Account",
			BROKERAGE,
			{anchors.PLAID_ID_HISTORY_FIELD: "acc_legacy", anchors.PLAID_ID_FIELD: "acc_one"},
		)
		self.push(bank_account=BROKERAGE, plaid_account_id="acc_two")
		self.assertEqual(self.history(), ["acc_legacy", "acc_one"])

	def test_the_chain_is_readable_through_get_account_pairing(self):
		self.push(bank_account=BROKERAGE, plaid_account_id="acc_one")
		self.push(bank_account=BROKERAGE, plaid_account_id="acc_two")
		data = self.tool_data("get_account_pairing", {"bank_account": BROKERAGE})
		self.assertEqual(data["accounts"][0]["plaid_account_id_history"], ["acc_one"])

	# -- a chain the pipe kept, pushed explicitly ----------------------------
	def test_a_pushed_history_is_recorded_for_ids_this_site_never_saw(self):
		"""The re-links that happened before this site was told about the
		account, or between syncs — the only place those ids can come from."""
		push_api.push_account_pairing(
			bank_account=BROKERAGE,
			plaid_account_id="acc_three",
			plaid_account_id_history=json.dumps(["acc_one", "acc_two"]),
		)
		self.assertEqual(self.history(), ["acc_one", "acc_two"])
		self.assertEqual(self.stored(anchors.PLAID_ID_FIELD), "acc_three")

	def test_a_pushed_history_is_merged_with_what_this_site_observed(self):
		"""Neither source is trusted to be complete: a pipe sending a short
		chain must not truncate ids this site watched being retired."""
		self.push(bank_account=BROKERAGE, plaid_account_id="acc_two")
		push_api.push_account_pairing(
			bank_account=BROKERAGE,
			plaid_account_id="acc_four",
			plaid_account_id_history=json.dumps(["acc_one"]),
		)
		# acc_one from the pipe, acc_two observed here, acc_three never existed.
		self.assertEqual(self.history(), ["acc_one", "acc_two"])

	def test_a_pushed_history_does_not_truncate_a_longer_stored_chain(self):
		for identifier in ("acc_one", "acc_two", "acc_three"):
			self.push(bank_account=BROKERAGE, plaid_account_id=identifier)
		push_api.push_account_pairing(
			bank_account=BROKERAGE, plaid_account_id_history=json.dumps(["acc_one"])
		)
		self.assertEqual(self.history(), ["acc_one", "acc_two"])

	def test_the_metadata_endpoint_takes_a_pushed_history_too(self):
		"""Declared on both, because Frappe drops a kwarg a whitelisted method
		does not name and answers 200 anyway."""
		self.push(
			bank_account=BROKERAGE,
			plaid_account_id="acc_three",
			plaid_account_id_history=json.dumps(["acc_one", "acc_two"]),
		)
		self.assertEqual(self.history(), ["acc_one", "acc_two"])

	def test_a_pushed_history_can_arrive_as_a_list_rather_than_json_text(self):
		push_api.push_account_pairing(bank_account=BROKERAGE, plaid_account_id_history=["acc_one", "acc_two"])
		self.assertEqual(self.history(), ["acc_one", "acc_two"])

	def test_re_pushing_the_same_history_appends_nothing(self):
		"""A sync sends its chain every night; a column that grew each time
		would be a log of syncs rather than a chain of identities."""
		for _ in range(3):
			push_api.push_account_pairing(
				bank_account=BROKERAGE,
				plaid_account_id="acc_three",
				plaid_account_id_history=json.dumps(["acc_one", "acc_two"]),
			)
		self.assertEqual(self.history(), ["acc_one", "acc_two"])

	def test_a_pushed_history_naming_the_live_id_does_not_keep_it_as_superseded(self):
		"""An id that is both current and superseded reads as two accounts to
		anything matching on it."""
		push_api.push_account_pairing(
			bank_account=BROKERAGE,
			plaid_account_id="acc_two",
			plaid_account_id_history=json.dumps(["acc_one", "acc_two"]),
		)
		self.assertEqual(self.history(), ["acc_one"])

	def test_a_pushed_history_alone_is_not_reported_as_a_repointing(self):
		"""Backfilling a chain is not a re-link, and a sync log that called it
		one would put a reconnection on the day somebody ran a backfill."""
		result = push_api.push_account_pairing(
			bank_account=BROKERAGE, plaid_account_id_history=json.dumps(["acc_one"])
		)
		self.assertEqual(result["repointed_count"], 0)
		self.assertEqual(self.history(), ["acc_one"])

	def test_a_pushed_history_and_a_repointing_in_one_call_keep_both(self):
		self.push(bank_account=BROKERAGE, plaid_account_id="acc_two")
		result = push_api.push_account_pairing(
			bank_account=BROKERAGE,
			plaid_account_id="acc_three",
			plaid_account_id_history=json.dumps(["acc_one"]),
		)
		self.assertEqual(self.history(), ["acc_one", "acc_two"])
		self.assertEqual(
			result["repointed"], [{"bank_account": BROKERAGE, "was": "acc_two", "now": "acc_three"}]
		)

	# -- what it refuses -----------------------------------------------------
	def test_a_pairing_key_is_refused_by_name_rather_than_dropped(self):
		"""A key that returns 200 and writes nothing is the failure this whole
		module is shaped to avoid."""
		with self.assertRaises(frappe.ValidationError) as caught:
			self.push(bank_account=BROKERAGE, paired_bank_account=SWEEP)
		self.assertIn("push_account_pairing", str(caught.exception))
		self.assertIsNone(self.stored(anchors.PAIRED_FIELD))

	def test_a_pairing_key_inside_a_batch_row_is_refused_too(self):
		result = self.push(
			accounts=[
				{"bank_account": BROKERAGE, "plaid_account_id": "acc_one"},
				{"bank_account": SWEEP, "paired_bank_account": BROKERAGE},
			]
		)
		self.assertEqual(result["updated_count"], 1)
		self.assertEqual(result["failed_count"], 1)
		self.assertIn("push_account_pairing", result["failed"][0]["error"])
		self.assertIsNone(self.stored(anchors.PAIRED_FIELD, SWEEP))

	def test_an_unknown_bank_account_is_refused_rather_than_created(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.push(bank_account="Nobody's Account", plaid_account_id="acc_one")
		self.assertIn("no Bank Account named", str(caught.exception))
		self.assertEqual(frappe.db.count("Bank Account"), 4)

	def test_an_unknown_plaid_account_type_is_refused(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.push(bank_account=BROKERAGE, plaid_account_type="chequing")
		self.assertIn("plaid_account_type must be one of", str(caught.exception))

	def test_guest_never_gets_past_the_first_line(self):
		frappe.local.session.user = "Guest"
		try:
			with self.assertRaises(frappe.PermissionError):
				self.push(bank_account=BROKERAGE, plaid_account_id="acc_one")
		finally:
			frappe.local.session.user = "Administrator"
		self.assertIsNone(self.stored(anchors.PLAID_ID_FIELD))

	def test_a_credential_without_write_permission_is_refused(self):
		STORE.denied_permissions.add(("Bank Account", "write"))
		with self.assertRaises(frappe.PermissionError):
			self.push(bank_account=BROKERAGE, plaid_account_id="acc_one")
		self.assertIsNone(self.stored(anchors.PLAID_ID_FIELD))

	# -- batches and the audit trail -----------------------------------------
	def test_a_batch_pushes_a_whole_sync_and_one_bad_row_does_not_lose_the_rest(self):
		result = self.push(
			accounts=[
				{"bank_account": BROKERAGE, "plaid_account_id": "acc_one", "sync_enabled": 1},
				{"bank_account": SWEEP, "plaid_account_id": "acc_two", "sync_enabled": 0},
				{"bank_account": "No Such Account", "plaid_account_id": "acc_three"},
			]
		)
		self.assertEqual(result["updated_count"], 2)
		self.assertEqual(result["failed_count"], 1)
		self.assertIn("No Such Account", result["failed"][0]["error"])
		self.assertEqual(self.stored(anchors.PLAID_ID_FIELD, SWEEP), "acc_two")

	def test_a_batch_can_arrive_as_json_text(self):
		payload = json.dumps([{"bank_account": BROKERAGE, "plaid_account_mask": "6030"}])
		self.assertEqual(self.push(accounts=payload)["updated_count"], 1)

	def test_every_push_writes_an_audit_row(self):
		self.push(bank_account=BROKERAGE, plaid_account_id="acc_one")
		self.assertAudited("push:push_account_metadata", "Success")

	def test_a_refused_push_writes_one_too(self):
		with self.assertRaises(frappe.ValidationError):
			self.push(bank_account="Nobody's Account", plaid_account_id="acc_one")
		self.assertAudited("push:push_account_metadata", "Error")


# ── the switches ────────────────────────────────────────────────────────────
class Switches(ConsolidationTestCase):
	def test_every_new_tool_refuses_by_the_name_of_its_own_switch(self):
		for name in ALL_TOOLS:
			with self.subTest(tool=name):
				self.configure(enabled=1, **{f"allow_{name}": 0})
				message = self.tool_error(name, {})
				self.assertIn(f"allow_{name}", message)
				self.assertIn("switched off", message)

	def test_every_new_tool_is_in_the_catalogue_with_the_right_polarity(self):
		for name in READ_TOOLS:
			with self.subTest(tool=name):
				self.assertIn(name, registry.READ_TOOLS)
		for name in WRITE_TOOLS:
			with self.subTest(tool=name):
				self.assertIn(name, registry.MUTATING_TOOLS)

	def test_the_read_tools_ship_on_and_the_write_tools_ship_off(self):
		defaults = self.configure()
		for name in READ_TOOLS:
			with self.subTest(tool=name):
				self.assertEqual(defaults[f"allow_{name}"], "1")
		for name in WRITE_TOOLS:
			with self.subTest(tool=name):
				self.assertEqual(defaults[f"allow_{name}"], "0")
