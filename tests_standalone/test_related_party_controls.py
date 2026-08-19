# SPDX-License-Identifier: MIT
"""Phase 4 — related-party transactions, transfer pricing, and the disclosure schedule.

THE CLASS THAT MATTERS MOST IS `AdvisoryAndEnforcedDifferOnlyInTheRefusal`. Every
other class here checks that some piece of the machinery works; that one checks
the promise the whole IPO-readiness design rests on — that a site running the
control in Advisory accumulates exactly the record it would have accumulated
running Enforced, minus the refusals. It is checked the only way that means
anything: run the same call under both modes, and compare the findings and the
alerts.

THE SECOND MOST IMPORTANT IS `TheMatchIsNeverAGuess`. A disclosure schedule that
silently guessed which vendor was the manager's LLC would be worse than one that
admitted it could not tell, because somebody would file it. So a name match is
always labelled as one, and everything that resolved to nothing comes back in
`unmatched_parties` rather than being dropped.
"""

import json

import frappe

from erpnext_mcp import compliance_rules, enforcement

from .fixtures import MAIN, MAIN_ABBR, SeededTestCase
from .harness import STORE

TODAY = "2026-07-24"
YEAR_START = "2026-01-01"

#: The vouchers below are Journal Entries rather than Purchase Invoices, and it
#: is a fact about the DOUBLE rather than about the app: a Compliance Alert's
#: `source_doctype` is a Link to DocType, and this harness models a subset of
#: ERPNext that does not include Purchase Invoice. On a site either resolves.

#: Every switch this module's tools sit behind.
ALL_ON = {
	"allow_create_related_party": 1,
	"allow_update_related_party": 1,
	"allow_create_transfer_pricing_doc": 1,
	"allow_get_transfer_pricing_doc": 1,
	"allow_list_transfer_pricing_docs": 1,
	"allow_update_transfer_pricing_doc": 1,
	"allow_flag_related_party_transaction": 1,
	"allow_get_related_party_transactions": 1,
	"allow_list_related_party_disclosures": 1,
	"allow_generate_related_party_disclosure": 1,
}

HAULER = "T. Polehn Trucking LLC"
ORCHARD_SUPPLIER = "Valley Orchard Supply"


class RelatedPartyControlsTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		frappe.local.session.user = "Administrator"
		# The gate rules are seeded from `enforcement.CONTROL_POINTS` by the same
		# seeder that installs the swept ones — `bench migrate` runs it, and a
		# test that skipped it would be testing a site no operator ever has.
		compliance_rules.seed_compliance_rules()

	# ── fixtures ────────────────────────────────────────────────────────────
	def a_supplier(self, name: str) -> str:
		if not frappe.db.exists("Supplier", name):
			STORE.seed("Supplier", [{"name": name, "supplier_name": name, "disabled": 0}])
		return name

	def a_related_party(
		self,
		party_name: str = "Tim Polehn",
		relationship: str = "Manager",
		supplier: str = "",
		**extra,
	) -> str:
		payload = {
			"company": MAIN,
			"party_name": party_name,
			"party_type": "Individual",
			"relationship_to_company": relationship,
			"effective_date": "2020-01-01",
			**extra,
		}
		if supplier:
			payload["supplier"] = self.a_supplier(supplier)
		return self.tool_data("create_related_party", payload)["name"]

	def a_gl_row(
		self,
		voucher_no: str,
		party: str,
		amount: float,
		posting_date: str = "2026-03-15",
		party_type: str = "Supplier",
		voucher_type: str = "Journal Entry",
		account: str = "",
		suffix: str = "cr",
	) -> dict:
		return {
			"name": f"GL-{voucher_no}-{suffix}",
			"company": MAIN,
			"account": account or f"Creditors - {MAIN_ABBR}",
			"posting_date": posting_date,
			"debit": 0.0,
			"credit": float(amount),
			"party": party,
			"party_type": party_type,
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"is_cancelled": 0,
			"is_opening": "No",
		}

	def seed_gl(self, *rows) -> None:
		"""The postings, AND the vouchers they belong to.

		The voucher itself has to exist because a Compliance Alert's
		`source_docname` is a Dynamic Link — an alert about a voucher the site
		does not have is refused, correctly, and on a real site the voucher is
		always there.
		"""
		rows = list(rows)
		vouchers = {}
		for row in rows:
			if row.get("voucher_type") == "Journal Entry" and row.get("voucher_no"):
				vouchers[row["voucher_no"]] = {
					"name": row["voucher_no"],
					"company": row.get("company", MAIN),
					"posting_date": row.get("posting_date"),
					"docstatus": 1,
				}
		if vouchers:
			STORE.seed("Journal Entry", list(vouchers.values()))
		STORE.seed("GL Entry", rows)

	def a_memo(self, related_party: str, **extra) -> str:
		payload = {
			"company": MAIN,
			"related_party": related_party,
			"transaction_type": "Services Received",
			"period_start": YEAR_START,
			"period_end": "2026-12-31",
			"amount": 50000,
			"market_rate_reference": "Three quotes from independent haulers, Yakima, March 2026",
			"justification": "Rate is within the range of the three independent quotes.",
			"pricing_method": "Comparable Uncontrolled Price",
			"status": "Complete",
			**extra,
		}
		return self.tool_data("create_transfer_pricing_doc", payload)["name"]

	# ── helpers ─────────────────────────────────────────────────────────────
	def rule_for(self, control_point: str) -> str:
		name = frappe.db.get_value("Compliance Rule", {"control_point": control_point}, "name")
		self.assertTrue(name, f"no Compliance Rule seeded for control point {control_point}")
		return name

	def set_mode(self, control_point: str, mode: str) -> None:
		frappe.db.set_value("Compliance Rule", self.rule_for(control_point), "enforcement_mode", mode)

	def alerts_for(self, control_point: str) -> list:
		return [row for row in STORE.rows("Compliance Alert") if row.get("alert_type") == control_point]


# ── 1 · the memo itself ─────────────────────────────────────────────────────
class TheMemoRefusesWhatWouldFailLate(RelatedPartyControlsTestCase):
	def test_a_period_that_runs_backwards_is_refused(self):
		party = self.a_related_party()
		message = self.tool_error(
			"create_transfer_pricing_doc",
			{
				"company": MAIN,
				"related_party": party,
				"transaction_type": "Services Received",
				"period_start": "2026-12-31",
				"period_end": "2026-01-01",
				"amount": 100,
			},
		)
		self.assertIn("before period_start", message)
		self.assertIn("Nothing was created", message)

	def test_a_memo_about_an_unregistered_party_is_refused(self):
		message = self.tool_error(
			"create_transfer_pricing_doc",
			{
				"company": MAIN,
				"related_party": "Somebody Nobody Registered",
				"transaction_type": "Services Received",
				"period_start": YEAR_START,
				"period_end": "2026-12-31",
				"amount": 100,
			},
		)
		self.assertIn("not a Related Party", message)
		self.assertIn("create_related_party", message)

	def test_complete_demands_the_case_and_draft_demands_nothing(self):
		party = self.a_related_party()
		# A draft with nothing in it is fine — that is what a draft is for.
		draft = self.tool_data(
			"create_transfer_pricing_doc",
			{
				"company": MAIN,
				"related_party": party,
				"transaction_type": "Services Received",
				"period_start": YEAR_START,
				"period_end": "2026-12-31",
				"amount": 1000,
			},
		)
		self.assertEqual(draft["status"], "Draft")
		self.assertIn("does not yet cover", draft["next_step"])

		message = self.tool_error(
			"update_transfer_pricing_doc",
			{"transfer_pricing_doc": draft["name"], "status": "Complete"},
		)
		self.assertIn("marked Complete", message)
		self.assertIn("Arm's-Length Justification", message)

	def test_a_negative_amount_is_refused_rather_than_absorbed(self):
		party = self.a_related_party()
		message = self.tool_error(
			"create_transfer_pricing_doc",
			{
				"company": MAIN,
				"related_party": party,
				"transaction_type": "Services Received",
				"period_start": YEAR_START,
				"period_end": "2026-12-31",
				"amount": -100,
			},
		)
		self.assertIn("negative", message)

	def test_a_review_by_the_preparer_is_not_a_review(self):
		party = self.a_related_party()
		name = self.a_memo(party, prepared_by="Administrator", reviewed_by="Administrator")
		data = self.tool_data("get_transfer_pricing_doc", {"transfer_pricing_doc": name})
		self.assertFalse(data["independently_reviewed"])
		listed = self.tool_data("list_transfer_pricing_docs", {"company": MAIN})
		self.assertIn(name, listed["not_independently_reviewed"])


# ── 2 · what "documented" means, in one place ───────────────────────────────
class OneDefinitionOfDocumented(RelatedPartyControlsTestCase):
	def test_a_draft_covers_nothing(self):
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party, status="Draft")
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["undocumented_count"], 1)
		self.assertEqual(data["transactions"][0]["coverage"], "draft_only")

	def test_a_complete_memo_covers_a_transaction_inside_its_period(self):
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["undocumented_count"], 0)
		self.assertTrue(data["transactions"][0]["documented"])

	def test_a_transaction_outside_the_period_is_not_covered(self):
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party, period_start="2026-01-01", period_end="2026-02-28")
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000, posting_date="2026-06-01"))
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["transactions"][0]["coverage"], "undocumented")

	def test_an_amount_far_over_the_memo_is_its_own_finding(self):
		"""Not the same gap as no memo at all, and the remedy is different work."""
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party, amount=12000)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 140000))
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		row = data["transactions"][0]
		self.assertEqual(row["coverage"], "amount_exceeds_documentation")
		# The memo it overflowed is still named — the remedy points at it.
		self.assertTrue(row["transfer_pricing_docs"])

	def test_the_tolerance_absorbs_a_season_that_did_not_land_on_the_number(self):
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party, amount=10000)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 10500))
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertTrue(data["transactions"][0]["documented"])

	def test_completing_a_draft_clears_the_finding(self):
		party = self.a_related_party(supplier=HAULER)
		memo = self.a_memo(party, status="Draft")
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		self.assertEqual(
			self.tool_data("get_related_party_transactions", {"company": MAIN})["undocumented_count"], 1
		)

		updated = self.tool_data(
			"update_transfer_pricing_doc",
			{
				"transfer_pricing_doc": memo,
				"status": "Complete",
				"justification": "Within the range of three independent quotes.",
				"market_rate_reference": "Three quotes, Yakima, March 2026",
				"pricing_method": "Comparable Uncontrolled Price",
			},
		)
		self.assertEqual(updated["status_change"], "Draft → Complete")
		self.assertEqual(
			self.tool_data("get_related_party_transactions", {"company": MAIN})["undocumented_count"], 0
		)


# ── 3 · the match is never a guess ──────────────────────────────────────────
class TheMatchIsNeverAGuess(RelatedPartyControlsTestCase):
	def test_a_supplier_link_match_is_labelled_as_one(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["transactions"][0]["match"], "supplier_link")

	def test_a_name_match_is_labelled_differently_from_a_link_match(self):
		"""The whole point: a caller can tell which conclusions were inferred."""
		self.a_related_party(party_name=HAULER, relationship="Vendor")
		self.a_related_party(party_name="Tim Polehn", relationship="Manager")
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data(
			"get_related_party_transactions", {"company": MAIN, "include_arms_length_vendors": True}
		)
		self.assertEqual(data["transactions"][0]["match"], "name")

	def test_a_counterparty_nobody_registered_is_reported_not_dropped(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(
			self.a_gl_row("PINV-01", HAULER, 4000),
			self.a_gl_row("PINV-02", ORCHARD_SUPPLIER, 9000),
		)
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["count"], 1)
		unmatched = {row["party"]: row for row in data["unmatched_parties"]}
		self.assertIn(ORCHARD_SUPPLIER, unmatched)
		self.assertEqual(unmatched[ORCHARD_SUPPLIER]["total"], 9000.0)

	def test_the_schedule_says_what_it_cannot_see(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-02", ORCHARD_SUPPLIER, 9000))
		data = self.tool_data("generate_related_party_disclosure", {"company": MAIN})
		self.assertIn("nobody registered", data["what_it_cannot_see"])
		self.assertTrue(data["unmatched_parties"])

	def test_one_voucher_with_several_lines_is_one_transaction(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(
			self.a_gl_row("PINV-01", HAULER, 1000, suffix="a"),
			self.a_gl_row("PINV-01", HAULER, 3000, suffix="b"),
		)
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["transactions"][0]["amount"], 4000.0)
		self.assertEqual(data["transactions"][0]["posting_count"], 2)

	def test_an_opening_posting_is_skipped_and_counted(self):
		self.a_related_party(supplier=HAULER)
		opening = self.a_gl_row("PINV-00", HAULER, 500)
		opening["is_opening"] = "Yes"
		self.seed_gl(opening, self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["opening_postings_skipped"], 1)


# ── 4 · the gate ────────────────────────────────────────────────────────────
class TheGateReportsAndRefuses(RelatedPartyControlsTestCase):
	def test_an_undocumented_voucher_is_reported_and_allowed_through(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data(
			"flag_related_party_transaction",
			{"company": MAIN, "voucher_type": "Journal Entry", "voucher_no": "PINV-01"},
		)
		self.assertEqual(data["finding_count"], 1)
		self.assertEqual(data["control"]["action"], "reported")
		self.assertIn("ALLOWED THROUGH", data["control"]["advisory_note"])

	def test_a_documented_voucher_finds_nothing(self):
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data(
			"flag_related_party_transaction",
			{"company": MAIN, "voucher_type": "Journal Entry", "voucher_no": "PINV-01"},
		)
		self.assertEqual(data["finding_count"], 0)
		self.assertEqual(data["documented_count"], 1)
		self.assertEqual(data["control"]["action"], "none")

	def test_enforced_refuses_and_names_what_would_fix_it(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		self.set_mode("related_party_transfer_pricing", "Enforced")
		message = self.tool_error(
			"flag_related_party_transaction",
			{"company": MAIN, "voucher_type": "Journal Entry", "voucher_no": "PINV-01"},
		)
		self.assertIn("REFUSED", message)
		self.assertIn("create_transfer_pricing_doc", message)
		self.assertIn("back to Advisory", message)

	def test_a_proposed_transaction_can_be_evaluated_before_it_exists(self):
		party = self.a_related_party(supplier=HAULER)
		data = self.tool_data(
			"flag_related_party_transaction",
			{"company": MAIN, "related_party": party, "amount": 4000, "posting_date": "2026-03-15"},
		)
		self.assertEqual(data["evaluated"][0]["match"], "stated")
		self.assertEqual(data["finding_count"], 1)

	def test_a_voucher_with_no_related_party_is_refused_as_nothing_to_evaluate(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-02", ORCHARD_SUPPLIER, 9000))
		message = self.tool_error(
			"flag_related_party_transaction",
			{"company": MAIN, "voucher_type": "Journal Entry", "voucher_no": "PINV-02"},
		)
		self.assertIn("nothing related-party about it", message)

	def test_naming_neither_a_voucher_nor_a_party_is_refused(self):
		message = self.tool_error("flag_related_party_transaction", {"company": MAIN})
		self.assertIn("Name the dealing", message)


# ── 5 · the promise the whole design rests on ───────────────────────────────
class AdvisoryAndEnforcedDifferOnlyInTheRefusal(RelatedPartyControlsTestCase):
	"""A season in Advisory must accumulate what a season in Enforced would have."""

	def _run(self, mode: str) -> dict:
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		self.set_mode("related_party_transfer_pricing", mode)
		payload = {"company": MAIN, "voucher_type": "Journal Entry", "voucher_no": "PINV-01"}
		if mode == "Enforced":
			message = self.tool_error("flag_related_party_transaction", payload)
			findings = [message]
		else:
			data = self.tool_data("flag_related_party_transaction", payload)
			findings = [json.dumps(data["control"]["findings"], sort_keys=True)]
		return {"findings": findings, "alerts": self.alerts_for("related_party_transfer_pricing")}

	# The alert comparison below is what would catch a control that reported
	# under one mode and stayed silent under the other.
	def test_the_finding_is_reached_under_both_modes(self):
		advisory = self._run("Advisory")
		self.assertTrue(advisory["alerts"], "advisory filed no alert")
		self.assertIn("PINV-01", advisory["alerts"][0]["alert_message"])

		self.setUp()
		enforced = self._run("Enforced")
		self.assertIn("PINV-01", enforced["findings"][0])

	def test_the_advisory_alert_says_the_work_was_allowed_through(self):
		advisory = self._run("Advisory")
		self.assertIn("advisory — the work was allowed through", advisory["alerts"][0]["alert_message"])

	def test_the_same_call_twice_does_not_write_two_alerts(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		payload = {"company": MAIN, "voucher_type": "Journal Entry", "voucher_no": "PINV-01"}
		self.tool_data("flag_related_party_transaction", payload)
		self.tool_data("flag_related_party_transaction", payload)
		self.assertEqual(len(self.alerts_for("related_party_transfer_pricing")), 1)

	def test_the_control_ships_advisory(self):
		"""Nothing in this module may arrive enforcing on an upgrade."""
		mode = frappe.db.get_value(
			"Compliance Rule", self.rule_for("related_party_transfer_pricing"), "enforcement_mode"
		)
		self.assertEqual(mode, enforcement.ADVISORY)


# ── 6 · the register and the schedule ───────────────────────────────────────
class TheDisclosureRegister(RelatedPartyControlsTestCase):
	def test_a_relationship_with_no_paper_behind_it_is_a_gap(self):
		self.a_related_party()
		data = self.tool_data("list_related_party_disclosures", {"company": MAIN})
		row = data["disclosures"][0]
		self.assertIn("no governing document establishes this relationship", row["gaps"])

	def test_an_arms_length_vendor_is_not_a_disclosure(self):
		self.a_related_party(party_name="Ordinary Vendor Co", relationship="Vendor")
		data = self.tool_data("list_related_party_disclosures", {"company": MAIN})
		self.assertEqual(data["count"], 0)

	def test_undocumented_transactions_show_as_a_gap_with_their_total(self):
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data("list_related_party_disclosures", {"company": MAIN})
		row = data["disclosures"][0]
		self.assertEqual(row["undocumented_total"], 4000.0)
		self.assertTrue(any("no transfer pricing documentation" in gap for gap in row["gaps"]))

	def test_an_ended_relationship_is_still_listed(self):
		"""The transactions it explains are still in the ledger."""
		self.a_related_party(party_name="Retired Member", relationship="Member", end_date="2024-06-30")
		data = self.tool_data("list_related_party_disclosures", {"company": MAIN})
		self.assertEqual(data["count"], 1)
		self.assertFalse(data["disclosures"][0]["current"])


class TheSchedule(RelatedPartyControlsTestCase):
	def test_it_foots_and_splits_documented_from_undocumented(self):
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party, period_start=YEAR_START, period_end="2026-03-31", amount=5000)
		self.seed_gl(
			self.a_gl_row("PINV-01", HAULER, 4000, posting_date="2026-03-15"),
			self.a_gl_row("PINV-02", HAULER, 6000, posting_date="2026-07-15"),
		)
		data = self.tool_data("generate_related_party_disclosure", {"company": MAIN})
		self.assertEqual(data["total"], 10000.0)
		self.assertEqual(data["documented_total"], 4000.0)
		self.assertEqual(data["undocumented_total"], 6000.0)
		self.assertEqual(data["coverage_pct"], 40.0)

	def test_it_names_registered_parties_with_no_transactions(self):
		self.a_related_party(party_name="Quiet Trustee", relationship="Trustee")
		data = self.tool_data("generate_related_party_disclosure", {"company": MAIN})
		self.assertTrue(data["registered_without_transactions"])

	def test_it_carries_the_pricing_methods_relied_on(self):
		party = self.a_related_party(supplier=HAULER)
		self.a_memo(party)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		data = self.tool_data("generate_related_party_disclosure", {"company": MAIN})
		self.assertEqual(data["schedule"][0]["pricing_methods"], ["Comparable Uncontrolled Price"])

	def test_it_files_nothing_and_says_so(self):
		self.a_related_party(supplier=HAULER)
		data = self.tool_data("generate_related_party_disclosure", {"company": MAIN})
		self.assertIn("nothing here is filed anywhere", data["what_this_is"])

	def test_a_window_the_wrong_way_round_is_refused(self):
		message = self.tool_error(
			"generate_related_party_disclosure",
			{"company": MAIN, "from_date": "2026-12-31", "to_date": "2026-01-01"},
		)
		self.assertIn("is after", message)


# ── 7 · the switches ────────────────────────────────────────────────────────
class EveryToolIsBehindItsOwnSwitch(RelatedPartyControlsTestCase):
	def test_a_disabled_tool_is_invisible(self):
		self.configure(enabled=1, **{**ALL_ON, "allow_create_transfer_pricing_doc": 0})
		body, status = self.call("tools/list")
		self.assertEqual(status, 200)
		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertNotIn("create_transfer_pricing_doc", names)
		self.assertIn("list_transfer_pricing_docs", names)

	def test_the_read_tools_never_refuse_even_under_enforcement(self):
		"""A read that raised would make enforcement unobservable before switching on."""
		self.a_related_party(supplier=HAULER)
		self.seed_gl(self.a_gl_row("PINV-01", HAULER, 4000))
		self.set_mode("related_party_transfer_pricing", "Enforced")
		data = self.tool_data("get_related_party_transactions", {"company": MAIN})
		self.assertEqual(data["undocumented_count"], 1)
		self.assertTrue(data["control"]["enforced"])
