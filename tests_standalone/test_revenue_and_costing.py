# SPDX-License-Identifier: MIT
"""IPO readiness Phases 2 and 3 — revenue recognition, and cost accounting.

WHAT THESE TWO PHASES CLAIM, AND WHAT WOULD MAKE THE CLAIMS FALSE.

Phase 2 claims that this app can answer an auditor's actual question about
revenue — "show me one sale all the way through" — and that recognition follows
the PERFORMANCE OBLIGATION rather than the cash or the schedule. It would be
false if `trace_contract_to_cash` quietly returned the hops it happened to find,
because the missing hop is the answer; and false if revenue could be recognised
against an obligation nobody had marked satisfied without the system saying so.

Phase 3 claims that a carrying value is never an assertion nobody can check, and
that a variance report never reports a number it cannot measure. It would be
false if `current_value` could be typed, and false if a standard with no cost
center produced a variance against an actual of zero — which reads as "100% under
budget" and is indistinguishable from a real finding.

SIX CLASSES.

1. `TheContractIsTheUnitOfAccount` — obligations, allocation, and the two sums
   that may not exceed the transaction price.
2. `RecognitionFollowsTheObligation` — the control, in both modes, and the draft
   entry it produces.
3. `TheTraceReportsItsOwnBreaks` — the chain, and the hop that is missing.
4. `ACarryingValueIsAlwaysEvidenced` — `current_value` is derived, never typed.
5. `AStandardIsDated` — the effective-range selection and the overlap refusal.
6. `TheVarianceKnowsWhatItCannotMeasure` — unmeasurable is not zero, and a rate
   without a quantity is not a budget.
"""

import frappe

from erpnext_mcp import enforcement
from erpnext_mcp.tools import costing

from .fixtures import (
	MAIN,
	MAIN_ABBR,
	MASTER_CUSTOMER,
	V12TestCase,
	cash,
	sales,
	seed_masters,
)
from .harness import STORE

TODAY = "2026-08-16"

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"create_revenue_contract",
		"get_revenue_contract",
		"list_revenue_contracts",
		"update_revenue_contract",
		"link_settlement_to_contract",
		"recognize_revenue_milestone",
		"trace_contract_to_cash",
		"create_biological_asset",
		"get_biological_asset",
		"list_biological_assets",
		"update_biological_asset",
		"record_biological_asset_valuation",
		"create_standard_cost",
		"get_standard_cost",
		"list_standard_costs",
		"update_standard_cost",
		"get_cost_variance_report",
		"get_absorption_cost_report",
		"list_control_points",
	)
}


class RevenueTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		seed_masters()
		self.configure(**ALL_ON)
		from erpnext_mcp import compliance_rules

		compliance_rules.seed_compliance_rules()

	def enforce(self, control_point: str) -> None:
		rows = frappe.db.get_all("Compliance Rule", filters={"control_point": control_point}, pluck="name")
		self.assertTrue(rows, f"no seeded rule for {control_point!r}")
		frappe.db.set_value("Compliance Rule", rows[0], "enforcement_mode", "Enforced")

	def a_contract(self, **overrides):
		payload = {
			"contract_name": "Southgate 2026 Gala pool",
			"company": MAIN,
			"customer": MASTER_CUSTOMER,
			"contract_date": "2026-06-01",
			"start_date": "2026-06-01",
			"end_date": "2026-12-31",
			"total_value": 300000,
			"revenue_account": sales(MAIN_ABBR),
			"receivable_account": cash(MAIN_ABBR),
			"obligations": [
				{"obligation": "Deliver 4,000 bins of Gala", "allocated_amount": 240000},
				{"obligation": "Store through March", "allocated_amount": 60000},
			],
			"schedule": [
				{"basis": "Milestone", "obligation": "Deliver 4,000 bins of Gala", "amount": 240000},
				{"basis": "Time", "due_date": "2026-11-30", "amount": 60000},
			],
		}
		payload.update(overrides)
		return self.tool_data("create_revenue_contract", payload)


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheContractIsTheUnitOfAccount(RevenueTestCase):
	def test_a_contract_carries_its_obligations_and_schedule(self):
		data = self.a_contract()["contract"]
		self.assertEqual(data["obligation_count"], 2)
		self.assertEqual(len(data["schedule"]), 2)
		self.assertEqual(data["allocated_amount"], 300000)
		self.assertEqual(data["unrecognized_amount"], 300000)

	def test_obligations_may_not_allocate_past_the_transaction_price(self):
		"""If the parts sum past the whole, some cannot be earned — and which
		ones would depend on the order somebody recognised them in."""
		error = self.tool_error(
			"create_revenue_contract",
			{
				"contract_name": "Overallocated",
				"company": MAIN,
				"customer": MASTER_CUSTOMER,
				"total_value": 100,
				"obligations": [
					{"obligation": "A", "allocated_amount": 90},
					{"obligation": "B", "allocated_amount": 30},
				],
			},
		)
		self.assertIn("more than the contract", error)

	def test_a_schedule_may_not_total_past_the_transaction_price(self):
		error = self.tool_error(
			"create_revenue_contract",
			{
				"contract_name": "Overscheduled",
				"company": MAIN,
				"customer": MASTER_CUSTOMER,
				"total_value": 100,
				"obligations": [{"obligation": "A", "allocated_amount": 100}],
				"schedule": [
					{"basis": "Milestone", "obligation": "A", "amount": 80},
					{"basis": "Milestone", "obligation": "A", "amount": 60},
				],
			},
		)
		self.assertIn("more than the contract", error)

	def test_allocating_less_than_the_price_is_allowed_and_reported(self):
		"""That is a contract somebody is still writing, not an error."""
		data = self.a_contract(
			obligations=[{"obligation": "Deliver 4,000 bins of Gala", "allocated_amount": 240000}],
			schedule=[{"basis": "Milestone", "obligation": "Deliver 4,000 bins of Gala", "amount": 240000}],
		)["contract"]
		self.assertEqual(data["unallocated_amount"], 60000)
		self.assertIn("still writing", data["allocation_note"])

	def test_a_milestone_row_naming_no_obligation_is_refused(self):
		"""Nothing could ever make it ripe."""
		error = self.tool_error(
			"create_revenue_contract",
			{
				"contract_name": "Unripe",
				"company": MAIN,
				"customer": MASTER_CUSTOMER,
				"total_value": 100,
				"schedule": [{"basis": "Milestone", "amount": 100}],
			},
		)
		self.assertIn("names no obligation", error)

	def test_a_time_row_with_no_due_date_is_refused(self):
		error = self.tool_error(
			"create_revenue_contract",
			{
				"contract_name": "Undated",
				"company": MAIN,
				"customer": MASTER_CUSTOMER,
				"total_value": 100,
				"schedule": [{"basis": "Time", "amount": 100}],
			},
		)
		self.assertIn("no due_date", error)

	def test_a_milestone_naming_an_obligation_not_on_the_contract_is_refused(self):
		error = self.tool_error(
			"create_revenue_contract",
			{
				"contract_name": "Mismatched",
				"company": MAIN,
				"customer": MASTER_CUSTOMER,
				"total_value": 100,
				"obligations": [{"obligation": "A", "allocated_amount": 100}],
				"schedule": [{"basis": "Milestone", "obligation": "B", "amount": 100}],
			},
		)
		self.assertIn("not on this contract", error)

	def test_a_contract_with_no_customer_on_this_site_is_refused(self):
		error = self.tool_error(
			"create_revenue_contract",
			{"contract_name": "Ghost", "company": MAIN, "customer": "Nobody Ltd", "total_value": 10},
		)
		self.assertIn("no Customer", error)

	def test_satisfaction_survives_a_rewrite_of_the_obligation_list(self):
		"""Losing the record that control of four thousand bins transferred on 12
		September, because somebody reworded a DIFFERENT line, would silently
		un-recognise revenue."""
		created = self.a_contract()["contract"]
		self.tool_data(
			"update_revenue_contract",
			{
				"contract": created["name"],
				"obligations": [
					{
						"obligation": "Deliver 4,000 bins of Gala",
						"allocated_amount": 240000,
						"satisfied": True,
						"satisfied_on": "2026-09-12",
						"evidence": "SCALE-0031",
					},
					{"obligation": "Store through March", "allocated_amount": 60000},
				],
			},
		)
		after = self.tool_data(
			"update_revenue_contract",
			{
				"contract": created["name"],
				"obligations": [
					{"obligation": "Deliver 4,000 bins of Gala", "allocated_amount": 240000},
					{"obligation": "Store through March until April", "allocated_amount": 60000},
				],
			},
		)["contract"]
		delivered = next(row for row in after["obligations"] if row["obligation"].startswith("Deliver"))
		self.assertTrue(delivered["satisfied"])
		self.assertEqual(delivered["satisfied_on"], "2026-09-12")
		self.assertEqual(delivered["evidence"], "SCALE-0031")


# ── 2 ───────────────────────────────────────────────────────────────────────
class RecognitionFollowsTheObligation(RevenueTestCase):
	def satisfy(self, contract: str) -> None:
		self.tool_data(
			"update_revenue_contract",
			{
				"contract": contract,
				"obligations": [
					{
						"obligation": "Deliver 4,000 bins of Gala",
						"allocated_amount": 240000,
						"satisfied": True,
						"satisfied_on": "2026-09-12",
					},
					{"obligation": "Store through March", "allocated_amount": 60000},
				],
			},
		)

	def test_recognising_a_satisfied_obligation_is_clean_and_writes_a_draft(self):
		created = self.a_contract()["contract"]
		self.satisfy(created["name"])
		data = self.tool_data(
			"recognize_revenue_milestone",
			{"contract": created["name"], "tranche": 1, "posting_date": "2026-09-12"},
		)
		self.assertEqual(data["control"]["finding_count"], 0)
		self.assertTrue(data["journal_entry"])
		entry = STORE.tables["Journal Entry"][data["journal_entry"]]
		self.assertEqual(int(entry["docstatus"]), 0)
		self.assertEqual(data["contract"]["recognized_amount"], 240000)
		self.assertEqual(data["contract"]["unrecognized_amount"], 60000)

	def test_recognising_an_unsatisfied_obligation_is_advisory_by_default(self):
		created = self.a_contract()["contract"]
		data = self.tool_data(
			"recognize_revenue_milestone",
			{"contract": created["name"], "tranche": 1, "posting_date": "2026-09-12"},
		)
		self.assertTrue(data["journal_entry"], "advisory mode did not let the recognition through")
		self.assertEqual(data["control"]["finding_count"], 1)
		self.assertIn("not marked satisfied", data["control"]["findings"][0]["message"])

	def test_enforced_refuses_it_and_writes_no_entry(self):
		created = self.a_contract()["contract"]
		self.enforce("revenue_recognition")
		before = len(STORE.rows("Journal Entry"))
		error = self.tool_error(
			"recognize_revenue_milestone",
			{"contract": created["name"], "tranche": 1, "posting_date": "2026-09-12"},
		)
		self.assertIn("REFUSED", error)
		self.assertIn("control transfers", error)
		self.assertEqual(len(STORE.rows("Journal Entry")), before)

	def test_a_time_tranche_recognised_early_is_a_finding(self):
		"""Recognising next quarter's storage fee this quarter is the same error
		with a different trigger."""
		created = self.a_contract()["contract"]
		data = self.tool_data(
			"recognize_revenue_milestone",
			{"contract": created["name"], "tranche": 2, "posting_date": "2026-09-12"},
		)
		self.assertEqual(data["control"]["finding_count"], 1)
		self.assertIn("not due until 2026-11-30", data["control"]["findings"][0]["message"])

	def test_a_time_tranche_on_its_due_date_is_clean(self):
		created = self.a_contract()["contract"]
		data = self.tool_data(
			"recognize_revenue_milestone",
			{"contract": created["name"], "tranche": 2, "posting_date": "2026-11-30"},
		)
		self.assertEqual(data["control"]["finding_count"], 0)

	def test_recognising_a_tranche_twice_is_refused(self):
		created = self.a_contract()["contract"]
		self.satisfy(created["name"])
		self.tool_data("recognize_revenue_milestone", {"contract": created["name"], "tranche": 1})
		error = self.tool_error("recognize_revenue_milestone", {"contract": created["name"], "tranche": 1})
		self.assertIn("already recognised", error)

	def test_recognition_refuses_to_guess_an_account(self):
		"""An account picked by an algorithm is a misstatement somebody finds at
		year end."""
		created = self.a_contract(revenue_account="", receivable_account="")["contract"]
		error = self.tool_error("recognize_revenue_milestone", {"contract": created["name"], "tranche": 1})
		self.assertIn("will not guess", error)

	def test_the_schedule_cannot_be_replaced_once_a_tranche_is_recognised(self):
		"""The journal entries would point at tranches that no longer say what
		they said when the entries were written."""
		created = self.a_contract()["contract"]
		self.satisfy(created["name"])
		self.tool_data("recognize_revenue_milestone", {"contract": created["name"], "tranche": 1})
		error = self.tool_error(
			"update_revenue_contract",
			{
				"contract": created["name"],
				"schedule": [{"basis": "Time", "due_date": "2026-12-31", "amount": 10}],
			},
		)
		self.assertIn("already been recognised", error)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheTraceReportsItsOwnBreaks(RevenueTestCase):
	def test_a_bare_contract_traces_to_one_hop_and_names_what_is_missing(self):
		created = self.a_contract()["contract"]
		data = self.tool_data("trace_contract_to_cash", {"contract": created["name"]})
		self.assertEqual(data["hops_present"], ["contract"])
		self.assertFalse(data["complete"])
		self.assertIn("settlement", data["hops_missing"])
		self.assertIn("payment", data["hops_missing"])

	def test_a_break_carries_the_sentence_that_explains_it(self):
		"""`hops_missing` says which stage; `breaks` says what its absence means.
		A trace that returned only the hops it found would bury the answer."""
		created = self.a_contract()["contract"]
		data = self.tool_data("trace_contract_to_cash", {"contract": created["name"]})
		self.assertTrue(data["breaks"])
		self.assertIn("link_settlement_to_contract", " ".join(row["note"] for row in data["breaks"]))

	def test_a_recognised_tranche_puts_its_entry_in_the_chain_once_posted(self):
		created = self.a_contract()["contract"]
		self.tool_data("recognize_revenue_milestone", {"contract": created["name"], "tranche": 1})
		data = self.tool_data("trace_contract_to_cash", {"contract": created["name"]})
		self.assertEqual(data["recognized_amount"], 240000)

	def test_the_note_says_a_break_is_the_point_of_the_read(self):
		created = self.a_contract()["contract"]
		data = self.tool_data("trace_contract_to_cash", {"contract": created["name"]})
		self.assertIn("A BREAK IS THE POINT OF THIS READ", data["note"])

	def a_settlement(self, name="SETT-0001", customer=MASTER_CUSTOMER, company=MAIN):
		STORE.seed(
			"Settlement Statement",
			[
				{
					"name": name,
					"statement_number": name,
					"date": "2026-12-01",
					"customer": customer,
					"company": company,
					"status": "Posted",
					"net_proceeds": 285000,
					"total_gross_revenue": 300000,
					"docstatus": 1,
				}
			],
		)
		return name

	def test_linking_a_settlement_adds_the_hop(self):
		created = self.a_contract()["contract"]
		settlement = self.a_settlement()
		self.tool_data("link_settlement_to_contract", {"settlement": settlement, "contract": created["name"]})
		data = self.tool_data("trace_contract_to_cash", {"contract": created["name"]})
		self.assertIn("settlement", data["hops_present"])

	def test_a_settlement_from_another_customer_is_refused(self):
		"""It would corrupt every trace and every revenue figure reading through it."""
		created = self.a_contract()["contract"]
		STORE.seed(
			"Customer",
			[{"name": "Другой Buyer", "customer_name": "Другой Buyer", "disabled": 0, "accounts": []}],
		)
		settlement = self.a_settlement(name="SETT-0002", customer="Другой Buyer")
		error = self.tool_error(
			"link_settlement_to_contract", {"settlement": settlement, "contract": created["name"]}
		)
		self.assertIn("corrupts every trace", error)

	def test_force_links_a_mismatched_settlement_and_says_it_did(self):
		created = self.a_contract()["contract"]
		STORE.seed(
			"Customer",
			[{"name": "Другой Buyer", "customer_name": "Другой Buyer", "disabled": 0, "accounts": []}],
		)
		settlement = self.a_settlement(name="SETT-0003", customer="Другой Buyer")
		data = self.tool_data(
			"link_settlement_to_contract",
			{"settlement": settlement, "contract": created["name"], "force": True},
		)
		self.assertTrue(data["warnings"])

	def test_a_settlement_already_linked_elsewhere_is_refused(self):
		"""Counted against two contracts it would double the revenue."""
		first = self.a_contract()["contract"]
		second = self.a_contract(contract_name="Second pool")["contract"]
		settlement = self.a_settlement()
		self.tool_data("link_settlement_to_contract", {"settlement": settlement, "contract": first["name"]})
		error = self.tool_error(
			"link_settlement_to_contract", {"settlement": settlement, "contract": second["name"]}
		)
		self.assertIn("double-count", error)


# ── 4 ───────────────────────────────────────────────────────────────────────
class ACarryingValueIsAlwaysEvidenced(RevenueTestCase):
	def a_block(self, **overrides):
		payload = {
			"asset_name": "Home Block Gala",
			"company": MAIN,
			"asset_type": "Bearer",
			"status": "Mature",
		}
		payload.update(overrides)
		return self.tool_data("create_biological_asset", payload)

	def test_an_unvalued_asset_reports_null_rather_than_zero(self):
		"""'Not yet valued' and 'valued at nothing' are different statements
		about a growing crop, and this app will not collapse them."""
		data = self.a_block()["asset"]
		self.assertIsNone(data["current_value"])
		self.assertIn("different statements", data["valuation_note"])

	def test_an_opening_value_becomes_the_first_history_row(self):
		data = self.a_block(current_value=450000, basis="establishment cost", valuation_date="2026-01-01")[
			"asset"
		]
		self.assertEqual(data["current_value"], 450000)
		self.assertEqual(data["valuation_count"], 1)
		self.assertEqual(data["valuations"][0]["basis"], "establishment cost")

	def test_current_value_cannot_be_typed(self):
		"""The whole point of the doctype: a figure no valuation supports is
		exactly the unevidenced assertion it exists to prevent."""
		created = self.a_block()["asset"]
		error = self.tool_error(
			"update_biological_asset", {"asset": created["name"], "current_value": 999999}
		)
		self.assertIn("cannot be set here", error)
		self.assertIn("record_biological_asset_valuation", error)

	def test_a_valuation_appends_and_the_newest_wins(self):
		created = self.a_block(current_value=100, basis="opening", valuation_date="2026-01-01")["asset"]
		self.tool_data(
			"record_biological_asset_valuation",
			{"asset": created["name"], "value": 175, "basis": "appraisal", "valuation_date": "2026-06-30"},
		)
		data = self.tool_data("get_biological_asset", {"asset": created["name"]})
		self.assertEqual(data["current_value"], 175)
		self.assertEqual(data["valuation_count"], 2)
		self.assertEqual(data["movement"]["change"], 75)

	def test_a_valuation_demands_a_basis(self):
		"""A fair value with no stated basis is somebody's opinion formatted as
		a figure."""
		created = self.a_block()["asset"]
		error = self.tool_error("record_biological_asset_valuation", {"asset": created["name"], "value": 100})
		self.assertIn("basis", error)

	def test_a_future_dated_valuation_is_refused(self):
		created = self.a_block()["asset"]
		error = self.tool_error(
			"record_biological_asset_valuation",
			{"asset": created["name"], "value": 100, "basis": "guess", "valuation_date": "2099-01-01"},
		)
		self.assertIn("future", error)

	def test_the_valuation_says_no_journal_entry_was_written(self):
		"""IAS 41.26 puts the movement in profit or loss — and which accounts is
		a decision about this operation's chart, not one an app may guess."""
		created = self.a_block(current_value=100, basis="opening")["asset"]
		data = self.tool_data(
			"record_biological_asset_valuation",
			{"asset": created["name"], "value": 175, "basis": "appraisal"},
		)
		self.assertEqual(data["change"], 75)
		self.assertIn("has NOT posted it", data["gain_or_loss_note"])

	def test_unvalued_assets_are_excluded_from_the_register_total(self):
		"""A carrying value that silently included un-valued crops would
		understate the balance sheet AND look like a measurement."""
		self.a_block(current_value=1000, basis="opening")
		self.a_block(asset_name="Nursery lot 7", asset_type="Consumable")
		data = self.tool_data("list_biological_assets", {"company": MAIN})
		self.assertEqual(data["total_carrying_value"], 1000)
		self.assertEqual(data["valued_count"], 1)
		self.assertEqual(data["unvalued_count"], 1)

	def test_a_consumable_asset_says_it_is_never_depreciated(self):
		created = self.a_block(asset_name="Nursery lot 7", asset_type="Consumable")["asset"]
		data = self.tool_data("get_biological_asset", {"asset": created["name"]})
		self.assertIn("never depreciated", data["type_note"])

	def test_an_unknown_asset_type_is_refused_with_the_distinction(self):
		error = self.tool_error(
			"create_biological_asset",
			{"asset_name": "Confused", "company": MAIN, "asset_type": "Perennial"},
		)
		self.assertIn("Bearer", error)
		self.assertIn("Consumable", error)


# ── 5 ───────────────────────────────────────────────────────────────────────
class AStandardIsDated(RevenueTestCase):
	def a_standard(self, **overrides):
		payload = {
			"subject": "HARVEST-GALA",
			"company": MAIN,
			"cost_basis": "Per Bin",
			"standard_rate": 24.0,
			"effective_from": "2026-01-01",
			"effective_to": "2026-12-31",
			"cost_category": "Labor",
		}
		payload.update(overrides)
		return self.tool_data("create_standard_cost", payload)

	def test_two_standards_for_one_subject_and_basis_may_not_overlap(self):
		"""Two standards covering one date would make 'what should this have
		cost' depend on which row was read first."""
		self.a_standard()
		error = self.tool_error(
			"create_standard_cost",
			{
				"subject": "HARVEST-GALA",
				"company": MAIN,
				"cost_basis": "Per Bin",
				"standard_rate": 27.0,
				"effective_from": "2026-06-01",
			},
		)
		self.assertIn("overlaps", error)

	def test_a_different_basis_for_one_subject_may_share_a_range(self):
		"""Per-acre and per-bin for one activity are two standards, and both may
		be live."""
		self.a_standard()
		data = self.a_standard(cost_basis="Per Acre", standard_rate=1800.0)
		self.assertEqual(data["standard"]["cost_basis"], "Per Acre")

	def test_closing_a_standard_makes_room_for_the_next_season(self):
		first = self.a_standard(effective_to=None)["standard"]
		self.tool_data("update_standard_cost", {"standard": first["name"], "effective_to": "2026-12-31"})
		data = self.a_standard(effective_from="2027-01-01", effective_to=None, standard_rate=27.0)
		self.assertEqual(data["standard"]["standard_rate"], 27.0)

	def test_the_standard_covering_a_date_is_the_one_selected(self):
		self.a_standard()
		self.tool_data(
			"create_standard_cost",
			{
				"subject": "HARVEST-GALA",
				"company": MAIN,
				"cost_basis": "Per Bin",
				"standard_rate": 27.0,
				"effective_from": "2027-01-01",
			},
		)
		data = self.tool_data(
			"get_standard_cost", {"subject": "HARVEST-GALA", "on_date": "2026-08-01", "cost_basis": "Per Bin"}
		)
		self.assertEqual(data["standard_rate"], 24.0)
		later = self.tool_data(
			"get_standard_cost", {"subject": "HARVEST-GALA", "on_date": "2027-08-01", "cost_basis": "Per Bin"}
		)
		self.assertEqual(later["standard_rate"], 27.0)

	def test_a_date_no_standard_covered_is_refused_rather_than_approximated(self):
		"""A variance against the wrong season's standard is a confident wrong
		number, which is worse than no variance."""
		self.a_standard()
		error = self.tool_error(
			"get_standard_cost", {"subject": "HARVEST-GALA", "on_date": "2025-08-01", "cost_basis": "Per Bin"}
		)
		self.assertIn("covered 2025-08-01", error)

	def test_the_pure_selector_prefers_the_latest_covering_row(self):
		rows = [
			{"name": "A", "effective_from": "2026-01-01", "effective_to": "2026-12-31"},
			{"name": "B", "effective_from": "2026-06-01", "effective_to": "2026-12-31"},
		]
		self.assertEqual(costing.select_effective(rows, "2026-08-01")["name"], "B")
		self.assertEqual(costing.select_effective(rows, "2026-03-01")["name"], "A")
		self.assertEqual(costing.select_effective(rows, "2025-01-01"), {})

	def test_an_open_ended_standard_covers_everything_after_its_start(self):
		rows = [{"name": "A", "effective_from": "2026-01-01", "effective_to": None}]
		self.assertEqual(costing.select_effective(rows, "2099-01-01")["name"], "A")

	def test_a_standard_with_no_cost_center_or_account_says_it_is_unmeasurable(self):
		data = self.a_standard()
		self.assertIn("UNMEASURABLE", data["actuals_note"])


# ── 6 ───────────────────────────────────────────────────────────────────────
class TheVarianceKnowsWhatItCannotMeasure(RevenueTestCase):
	def a_standard(self, **overrides):
		payload = {
			"subject": "HARVEST-GALA",
			"company": MAIN,
			"cost_basis": "Per Bin",
			"standard_rate": 24.0,
			"effective_from": "2026-01-01",
			"cost_category": "Labor",
			"variance_tolerance_pct": 10,
		}
		payload.update(overrides)
		return self.tool_data("create_standard_cost", payload)

	def test_a_supplied_actual_and_quantity_gives_a_variance(self):
		self.a_standard()
		data = self.tool_data(
			"get_cost_variance_report",
			{
				"company": MAIN,
				"on_date": "2026-08-01",
				"actuals": [{"subject": "HARVEST-GALA", "actual_amount": 105600, "quantity": 4000}],
			},
		)
		row = data["variances"][0]
		self.assertEqual(row["expected_amount"], 96000)
		self.assertEqual(row["actual_amount"], 105600)
		self.assertEqual(row["variance_amount"], 9600)
		self.assertEqual(row["variance_pct"], 10.0)
		self.assertEqual(row["direction"], "over")
		self.assertTrue(row["significant"])

	def test_a_variance_inside_the_tolerance_is_not_significant(self):
		self.a_standard()
		data = self.tool_data(
			"get_cost_variance_report",
			{
				"company": MAIN,
				"on_date": "2026-08-01",
				"actuals": [{"subject": "HARVEST-GALA", "actual_amount": 98000, "quantity": 4000}],
			},
		)
		self.assertFalse(data["variances"][0]["significant"])
		self.assertEqual(data["significant_count"], 0)

	def test_the_tolerance_is_per_standard(self):
		"""A fuel standard and a labor standard do not deserve the same band, and
		a single tolerance that fits neither is how a report becomes noise."""
		self.a_standard(variance_tolerance_pct=25)
		data = self.tool_data(
			"get_cost_variance_report",
			{
				"company": MAIN,
				"on_date": "2026-08-01",
				"actuals": [{"subject": "HARVEST-GALA", "actual_amount": 105600, "quantity": 4000}],
			},
		)
		self.assertFalse(data["variances"][0]["significant"])

	def test_a_standard_with_no_quantity_reports_why_it_cannot_compare(self):
		"""A standard is a RATE, and rate times nothing is not a budget."""
		self.a_standard()
		data = self.tool_data(
			"get_cost_variance_report",
			{
				"company": MAIN,
				"on_date": "2026-08-01",
				"actuals": [{"subject": "HARVEST-GALA", "actual_amount": 105600}],
			},
		)
		row = data["variances"][0]
		self.assertIsNone(row["expected_amount"])
		self.assertIsNone(row["variance_pct"])
		self.assertIn("rate times nothing is not a budget", row["note"])

	def test_an_unmeasurable_standard_is_listed_rather_than_reported_as_zero(self):
		"""A variance against an actual of zero reports it as 100% under budget
		and is indistinguishable from a real finding."""
		self.a_standard()
		data = self.tool_data("get_cost_variance_report", {"company": MAIN, "on_date": "2026-08-01"})
		row = data["variances"][0]
		self.assertFalse(row["measurable"])
		self.assertIsNone(row["actual_amount"])
		self.assertIn(row["standard"], data["unmeasurable"])
		self.assertIn("UNMEASURABLE IS NOT ZERO", data["note"])

	def test_a_significant_variance_is_an_advisory_finding_by_default(self):
		self.a_standard()
		data = self.tool_data(
			"get_cost_variance_report",
			{
				"company": MAIN,
				"on_date": "2026-08-01",
				"actuals": [{"subject": "HARVEST-GALA", "actual_amount": 200000, "quantity": 4000}],
			},
		)
		self.assertEqual(data["control"]["mode"], enforcement.ADVISORY)
		self.assertEqual(data["control"]["finding_count"], 1)

	def test_enforced_refuses_the_report(self):
		self.a_standard()
		self.enforce("cost_variance")
		error = self.tool_error(
			"get_cost_variance_report",
			{
				"company": MAIN,
				"on_date": "2026-08-01",
				"actuals": [{"subject": "HARVEST-GALA", "actual_amount": 200000, "quantity": 4000}],
			},
		)
		self.assertIn("REFUSED", error)

	def test_a_report_with_no_standards_at_all_is_refused_with_the_reason(self):
		error = self.tool_error("get_cost_variance_report", {"company": MAIN})
		self.assertIn("needs a standard to compare against", error)

	def test_absorption_off_is_direct_cost_only(self):
		data = self.tool_data(
			"get_absorption_cost_report",
			{"company": MAIN, "direct_accounts": [cash(MAIN_ABBR)], "quantity": 100},
		)
		self.assertFalse(data["absorption"])
		self.assertIsNone(data["indirect_cost"])
		self.assertIn("direct cost only", data["absorption_note"])

	def test_absorption_on_folds_the_overhead_pool_into_the_same_base(self):
		data = self.tool_data(
			"get_absorption_cost_report",
			{
				"company": MAIN,
				"absorption": True,
				"direct_accounts": [cash(MAIN_ABBR)],
				"overhead_accounts": [sales(MAIN_ABBR)],
				"quantity": 100,
			},
		)
		self.assertTrue(data["absorption"])
		self.assertIsNotNone(data["indirect_cost"])
		self.assertIn("two readings of ONE ledger", data["absorption_note"])

	def test_absorption_will_not_invent_a_denominator(self):
		"""Picking an allocation base is the step that turns a costing report
		into fiction."""
		data = self.tool_data(
			"get_absorption_cost_report", {"company": MAIN, "direct_accounts": [cash(MAIN_ABBR)]}
		)
		self.assertIsNone(data["cost_per_unit"])
		self.assertIn("WILL NOT PICK AN ALLOCATION BASE", data["quantity_note"])

	def test_a_report_with_no_pools_says_there_is_nothing_to_total(self):
		data = self.tool_data("get_absorption_cost_report", {"company": MAIN})
		self.assertIn("nothing to total", data["note"])
