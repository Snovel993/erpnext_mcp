# SPDX-License-Identifier: MIT
"""Compliance packets: the framework, and the two types that ship with it."""

from erpnext_mcp import packets, registry

from .fixtures import MAIN, OTHER, V2TestCase, cash, sales
from .harness import STORE

ALL_PACKETS = {f"allow_{name}": 1 for name in packets.names()}


class Framework(V2TestCase):
	def test_both_shipped_types_are_registered(self):
		self.assertEqual(packets.names(), ("fiscal_year_audit_packet", "reconciliation_packet"))

	def test_discovery_is_automatic(self):
		"""Adding a packet type is a file drop — no list to update. If discovery
		breaks, every type silently vanishes from the catalogue, which for a
		compliance artefact is the kind of absence nobody notices."""
		import importlib

		module = importlib.import_module("erpnext_mcp.packets")
		self.assertTrue(callable(module._discover))
		self.assertTrue(packets.PACKETS)

	def test_every_spec_declares_what_it_is_for(self):
		for name in packets.names():
			with self.subTest(packet=name):
				spec = packets.PACKETS[name]
				self.assertTrue(spec.title)
				self.assertTrue(spec.purpose)
				self.assertTrue(spec.audience)
				self.assertTrue(spec.required)

	def test_required_filters_are_all_declared_filters(self):
		for name in packets.names():
			with self.subTest(packet=name):
				spec = packets.PACKETS[name]
				self.assertTrue(set(spec.required) <= set(spec.filters))


class ListPackets(V2TestCase):
	def test_lists_the_available_types(self):
		data = self.tool_data("list_compliance_packets")
		self.assertEqual(
			sorted(row["packet_type"] for row in data["packets"]),
			["fiscal_year_audit_packet", "reconciliation_packet"],
		)

	def test_each_entry_carries_its_filter_schema(self):
		"""A client learns how to call a packet type this app's schema knows
		nothing about — that is the point of the indirection."""
		data = self.tool_data("list_compliance_packets")
		entry = next(r for r in data["packets"] if r["packet_type"] == "reconciliation_packet")
		self.assertEqual(sorted(entry["filters"]), ["account", "company", "period_end", "period_start"])
		self.assertEqual(entry["required_filters"], ["account", "period_start", "period_end"])

	def test_a_disabled_type_is_separated_from_an_unavailable_one(self):
		self.configure(enabled=1, allow_reconciliation_packet=0)
		data = self.tool_data("list_compliance_packets")
		self.assertEqual([row["packet_type"] for row in data["disabled"]], ["reconciliation_packet"])
		self.assertEqual(data["unavailable"], [])

	def test_an_unavailable_type_reports_what_it_needs(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Fiscal Year")
		data = self.tool_data("list_compliance_packets")
		self.assertEqual([row["packet_type"] for row in data["unavailable"]], ["fiscal_year_audit_packet"])
		self.assertIn("Fiscal Year", data["unavailable"][0]["requires"])


class Gating(V2TestCase):
	def test_an_unknown_packet_type_lists_the_known_ones(self):
		message = self.tool_error("generate_compliance_packet", {"packet_type": "payroll_packet"})
		self.assertIn("reconciliation_packet", message)

	def test_a_disabled_packet_type_names_its_switch(self):
		self.configure(enabled=1, allow_reconciliation_packet=0)
		message = self.tool_error(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-12-31"},
			},
		)
		self.assertIn("allow_reconciliation_packet", message)

	def test_an_unavailable_packet_type_says_it_cannot_be_switched_on(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Fiscal Year")
		message = self.tool_error(
			"generate_compliance_packet",
			{"packet_type": "fiscal_year_audit_packet", "filters": {"company": MAIN, "fiscal_year": "2026"}},
		)
		self.assertIn("not available on this site", message)

	def test_a_missing_required_filter_is_named(self):
		message = self.tool_error(
			"generate_compliance_packet",
			{"packet_type": "reconciliation_packet", "filters": {"account": cash()}},
		)
		self.assertIn("period_start", message)
		self.assertIn("period_end", message)

	def test_an_unknown_filter_is_rejected_rather_than_ignored(self):
		"""Silently generating an unscoped packet when the caller thought they had
		scoped it is the worst outcome available."""
		message = self.tool_error(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {
					"account": cash(),
					"period_start": "2026-01-01",
					"period_end": "2026-12-31",
					"cost_centre": "Main",
				},
			},
		)
		self.assertIn("does not take filter(s): cost_centre", message)

	def test_packet_type_is_required(self):
		message = self.tool_error("generate_compliance_packet", {})
		self.assertIn("list_compliance_packets", message)


class ReconciliationPacket(V2TestCase):
	def packet(self, **overrides):
		filters = {
			"account": cash(),
			"period_start": "2026-01-01",
			"period_end": "2026-06-30",
			"company": MAIN,
		}
		filters.update(overrides)
		return self.tool_data(
			"generate_compliance_packet",
			{"packet_type": "reconciliation_packet", "filters": filters},
		)

	def test_identifies_the_account(self):
		data = self.packet()
		self.assertEqual(data["account"]["name"], cash())
		self.assertEqual(data["account"]["number"], "1100")
		self.assertEqual(data["account"]["root_type"], "Asset")
		self.assertEqual(data["account"]["currency"], "USD")

	def test_opening_is_the_day_before_the_period(self):
		data = self.packet()
		self.assertEqual(data["opening_balance"]["as_of"], "2025-12-31")
		self.assertEqual(data["opening_balance"]["amount"], 0)

	def test_closing_and_movement_agree(self):
		data = self.packet()
		self.assertEqual(data["closing_balance"]["amount"], 750)
		self.assertEqual(data["movement_summary"]["total_debits"], 1000)
		self.assertEqual(data["movement_summary"]["total_credits"], 250)
		self.assertEqual(data["movement_summary"]["net_change"], 750)

	def test_the_arithmetic_check_closes(self):
		"""opening + net == closing, from two independent aggregates."""
		data = self.packet()
		self.assertTrue(data["arithmetic_check"]["reconciles"])
		self.assertEqual(data["arithmetic_check"]["difference"], 0)

	def test_submitted_entries_carry_this_accounts_share(self):
		data = self.packet()
		entry = next(e for e in data["journal_entries"] if e["name"] == "ACC-JV-2026-00001")
		self.assertEqual(entry["this_account_debit"], 1000)
		self.assertEqual(entry["this_account_credit"], 0)
		self.assertEqual(entry["this_account_net"], 1000)

	def test_drafts_are_listed_separately_and_flagged(self):
		"""ACC-JV-2026-00002 is a draft crediting cash 250. It is not in the
		closing balance, and an account that reconciles until it is submitted is
		not reconciled."""
		data = self.packet()
		self.assertEqual([e["name"] for e in data["unposted_drafts"]], ["ACC-JV-2026-00002"])
		flag = next(f for f in data["flags"] if f["code"] == "UNPOSTED_DRAFTS")
		self.assertEqual(flag["severity"], "WARN")
		self.assertEqual(flag["detail"]["net_if_submitted"], -250)

	def test_cancelled_entries_are_surfaced(self):
		"""A cancelled JE leaves no live GL row, so a balance query cannot see it
		— which is exactly why the packet has to."""
		STORE.get_raw("Journal Entry", "ACC-JV-2025-00009")["docstatus"] = 2
		STORE.get_raw("Journal Entry", "ACC-JV-2025-00009")["posting_date"] = "2026-03-01"
		data = self.packet()
		self.assertEqual([e["name"] for e in data["cancelled_entries"]], ["ACC-JV-2025-00009"])
		flag = next(f for f in data["flags"] if f["code"] == "CANCELLED_ENTRIES")
		self.assertEqual(flag["severity"], "WARN")

	def test_an_account_with_no_activity_says_so(self):
		data = self.packet(account="2100 - Accounts Payable - ETC")
		codes = [f["code"] for f in data["flags"]]
		self.assertIn("NO_ACTIVITY", codes)

	def test_a_large_entry_is_pointed_at_not_concluded_about(self):
		data = self.packet()
		flag = next(f for f in data["flags"] if f["code"] == "LARGE_ENTRY")
		self.assertEqual(flag["severity"], "INFO")
		self.assertIn("materiality is a judgement", flag["description"].lower())

	def test_an_income_account_reports_its_natural_direction(self):
		data = self.packet(account=sales())
		self.assertEqual(data["account"]["root_type"], "Income")
		self.assertEqual(data["closing_balance"]["amount"], -1000)

	def test_an_inverted_period_is_refused(self):
		message = self.tool_error(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {
					"account": cash(),
					"period_start": "2026-12-31",
					"period_end": "2026-01-01",
				},
			},
		)
		self.assertIn("is after", message)

	def test_an_ambiguous_account_is_refused_rather_than_guessed(self):
		message = self.tool_error(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": "Cash", "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		self.assertIn("matches 2 accounts", message)

	def test_the_bank_bridge_slot_is_present_and_empty(self):
		"""v0.4 fills this. Shipping the key empty rather than absent means a
		consumer written today does not change when it fills up."""
		data = self.packet()
		self.assertEqual(data["external_sources"], [])


class Provenance(V2TestCase):
	def test_a_packet_says_how_it_was_made(self):
		data = self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		self.assertEqual(data["generated_by"], "Administrator")
		self.assertEqual(data["site"], "test.localhost")
		self.assertEqual(data["generator"], "erpnext_mcp")
		self.assertTrue(data["generated_at"])
		self.assertTrue(data["generator_version"])

	def test_it_carries_the_audit_row_for_its_own_call(self):
		"""The most useful piece of provenance, and the one the handler cannot
		know — dispatch stamps it once the row exists."""
		data = self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		row = self.assertAudited("generate_compliance_packet", status="Success")
		self.assertEqual(data["mcp_action_log_id"], row["name"])

	def test_the_stamp_does_not_touch_other_tools(self):
		data = self.tool_data("get_company_topology")
		self.assertNotIn("mcp_action_log_id", data)

	def test_the_flag_summary_says_whether_it_is_signable(self):
		data = self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		self.assertIn("signable", data["flag_summary"])
		self.assertTrue(data["flag_summary"]["signable"], "no ERROR flags expected here")

	def test_an_error_flag_makes_a_packet_unsignable(self):
		"""Forge an unbalanced Journal Entry — ERPNext cannot produce one, so its
		presence means something wrote around the doctype."""
		STORE.get_raw("Journal Entry", "ACC-JV-2026-00001")["total_credit"] = 900
		data = self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		self.assertFalse(data["flag_summary"]["signable"])
		self.assertEqual(data["flag_summary"]["worst"], "ERROR")
		self.assertIn("UNBALANCED_JOURNAL_ENTRY", [f["code"] for f in data["flags"]])

	def test_flags_are_ordered_worst_first(self):
		STORE.get_raw("Journal Entry", "ACC-JV-2026-00001")["total_credit"] = 900
		data = self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		self.assertEqual(data["flags"][0]["severity"], "ERROR")

	def test_the_summary_carries_the_worst_flag_into_the_audit_log(self):
		STORE.get_raw("Journal Entry", "ACC-JV-2026-00001")["total_credit"] = 900
		self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		row = self.assertAudited("generate_compliance_packet")
		self.assertIn("worst flag: ERROR", row["result_summary"])


class FiscalYearAuditPacket(V2TestCase):
	def packet(self, **overrides):
		filters = {"company": MAIN, "fiscal_year": "2026"}
		filters.update(overrides)
		return self.tool_data(
			"generate_compliance_packet",
			{"packet_type": "fiscal_year_audit_packet", "filters": filters},
		)

	def test_reports_the_year_and_its_range(self):
		data = self.packet()
		self.assertEqual(data["fiscal_year"], "2026")
		self.assertEqual(data["date_range"]["start"], "2026-01-01")
		self.assertEqual(data["date_range"]["end"], "2026-12-31")

	def test_the_trial_balance_is_grouped_by_root_type(self):
		data = self.packet()
		self.assertEqual(
			sorted(data["trial_balance"]["by_root_type"]),
			["Asset", "Expense", "Income", "Liability"],
		)

	def test_every_row_states_its_basis(self):
		"""Balance-sheet accounts are cumulative, P&L accounts are within the year.
		Mixing the two silently is how a trial balance stops balancing."""
		data = self.packet()
		for rows in data["trial_balance"]["by_root_type"].values():
			for row in rows:
				with self.subTest(account=row["account"]):
					expected = "fiscal_year" if row["root_type"] in ("Income", "Expense") else "cumulative"
					self.assertEqual(row["basis"], expected)

	def test_debits_equal_credits_cumulatively(self):
		data = self.packet()
		self.assertEqual(data["trial_balance_totals"]["difference"], 0)
		self.assertNotIn("TRIAL_BALANCE_IMBALANCE", [f["code"] for f in data["flags"]])

	def test_the_income_statement_uses_natural_signs(self):
		data = self.packet()
		self.assertEqual(data["income_statement"]["revenue"], 1500)
		self.assertEqual(data["income_statement"]["expenses"], 250)
		self.assertEqual(data["income_statement"]["net_income"], 1250)

	def test_the_accounting_identity_holds_on_a_sound_ledger(self):
		data = self.packet()
		self.assertTrue(data["accounting_identity"]["holds"])
		self.assertEqual(data["accounting_identity"]["difference"], 0)

	def test_an_imbalanced_ledger_is_an_error_not_a_note(self):
		STORE.seed(
			"GL Entry",
			[
				{
					"name": "GL-orphan",
					"account": cash(),
					"posting_date": "2026-04-01",
					"debit": 500,
					"credit": 0,
					"company": MAIN,
					"is_cancelled": 0,
				}
			],
		)
		data = self.packet()
		codes = [f["code"] for f in data["flags"]]
		self.assertIn("TRIAL_BALANCE_IMBALANCE", codes)
		self.assertFalse(data["flag_summary"]["signable"])

	def test_document_counts_report_null_for_missing_doctypes(self):
		data = self.packet()
		self.assertIsNone(data["document_counts"]["purchase_invoices"])
		self.assertIn("not installed", data["document_counts"]["note"])

	def test_drafts_at_year_end_are_flagged(self):
		data = self.packet()
		flag = next(f for f in data["flags"] if f["code"] == "DRAFT_ENTRIES_AT_YEAR_END")
		self.assertEqual(flag["severity"], "WARN")

	def test_top_entries_are_ranked_by_amount(self):
		data = self.packet()
		amounts = [row["amount"] for row in data["top_20_entries_by_amount"]]
		self.assertEqual(amounts, sorted(amounts, reverse=True))

	def test_intercompany_lines_are_found_by_resolving_each_account(self):
		"""A Journal Entry declares one company; nothing stops a line pointing at
		another company's account, and that is invisible until consolidation."""
		STORE.get_raw("Journal Entry", "ACC-JV-2026-00001")["accounts"][1]["account"] = sales("SEL")
		data = self.packet()
		self.assertEqual(
			[row["journal_entry"] for row in data["intercompany_activity"]],
			["ACC-JV-2026-00001"],
		)
		self.assertIn("INTERCOMPANY_ACTIVITY", [f["code"] for f in data["flags"]])

	def test_an_unknown_fiscal_year_lists_the_known_ones(self):
		message = self.tool_error(
			"generate_compliance_packet",
			{"packet_type": "fiscal_year_audit_packet", "filters": {"company": MAIN, "fiscal_year": "1999"}},
		)
		self.assertIn("2026", message)

	def test_a_year_not_linked_to_the_company_is_flagged(self):
		data = self.packet(company=OTHER, fiscal_year="2026")
		self.assertIn("FISCAL_YEAR_NOT_LINKED", [f["code"] for f in data["flags"]])

	def test_a_company_with_no_activity_says_so(self):
		data = self.packet(company=OTHER)
		self.assertIn("NO_ACTIVITY", [f["code"] for f in data["flags"]])


class PacketsDoNotWrite(V2TestCase):
	def test_generating_a_packet_changes_nothing(self):
		before = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": cash(), "period_start": "2026-01-01", "period_end": "2026-06-30"},
			},
		)
		self.tool_data(
			"generate_compliance_packet",
			{"packet_type": "fiscal_year_audit_packet", "filters": {"company": MAIN, "fiscal_year": "2026"}},
		)
		after = {doctype: len(rows) for doctype, rows in STORE.tables.items()}
		after.pop("MCP Action Log", None)
		before.pop("MCP Action Log", None)
		self.assertEqual(before, after)

	def test_both_tools_are_registered_read_only(self):
		for name in ("list_compliance_packets", "generate_compliance_packet"):
			with self.subTest(tool=name):
				self.assertIn(name, registry.READ_TOOLS)
				self.assertTrue(registry.TOOLS[name]["annotations"]["readOnlyHint"])
