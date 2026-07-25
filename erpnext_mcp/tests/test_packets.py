# SPDX-License-Identifier: MIT
"""In-bench validation of the compliance packets.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_packets

The standalone suite checks the packets' logic against a fixture whose ledger is
known. This checks the two things only a real site can: that the shape survives
contact with a real chart of accounts, and — the interesting one — that the
packets' own integrity flags agree with the site they are describing. A
`fiscal_year_audit_packet` that raises TRIAL_BALANCE_IMBALANCE on a healthy
production ledger is a bug in the packet; one that stays quiet on a ledger the
site's own reports say is fine is the packet working.
"""

import frappe

from erpnext_mcp import packets

from .test_integration import MCPIntegrationTestCase


class PacketFramework(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable("list_compliance_packets", "generate_compliance_packet")

	def test_both_shipped_types_are_discovered_on_a_real_site(self):
		self.assertIn("reconciliation_packet", packets.names())
		self.assertIn("fiscal_year_audit_packet", packets.names())

	def test_every_packet_type_has_a_migrated_switch(self):
		meta = frappe.get_meta("ERPNext MCP Settings")
		missing = [name for name in packets.names() if not meta.has_field(f"allow_{name}")]
		self.assertEqual(missing, [], f"packet types with no switch on this site: {missing}")

	def test_listing_reports_the_filter_schema(self):
		data = self.tool_data("list_compliance_packets")
		names = (
			{row["packet_type"] for row in data["packets"]}
			| {row["packet_type"] for row in data["disabled"]}
			| {row["packet_type"] for row in data["unavailable"]}
		)
		self.assertEqual(names, set(packets.names()))

	def test_a_disabled_packet_type_is_refused_by_name(self):
		self.doc.allow_reconciliation_packet = 0
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype="ERPNext MCP Settings")
		result = self.tool(
			"generate_compliance_packet",
			{"packet_type": "reconciliation_packet", "filters": {}},
		)
		self.assertTrue(result["isError"])
		self.assertIn("allow_reconciliation_packet", result["content"][0]["text"])


class ReconciliationPacketOnThisSite(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable("generate_compliance_packet", "reconciliation_packet")

	def an_account_with_activity(self):
		row = frappe.db.get_value(
			"GL Entry",
			{"is_cancelled": 0},
			["account", "posting_date"],
			as_dict=True,
			order_by="posting_date desc",
		)
		if not row:
			self.skipTest("site has no GL Entry to reconcile")
		return row

	def packet(self, account, start, end):
		return self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {"account": account, "period_start": start, "period_end": end},
			},
		)

	def test_it_produces_the_documented_shape(self):
		row = self.an_account_with_activity()
		end = str(row.posting_date)
		start = frappe.utils.add_days(frappe.utils.getdate(end), -60).isoformat()
		data = self.packet(row.account, start, end)
		for key in (
			"account",
			"period",
			"opening_balance",
			"closing_balance",
			"movement_summary",
			"journal_entries",
			"unposted_drafts",
			"cancelled_entries",
			"arithmetic_check",
			"external_sources",
			"flags",
			"flag_summary",
			"generated_at",
			"generated_by",
			"mcp_action_log_id",
		):
			with self.subTest(key=key):
				self.assertIn(key, data)

	def test_the_arithmetic_closes_on_a_real_ledger(self):
		"""opening + net == closing, from two independent aggregates over the
		site's own GL. If this fails on a healthy site, the packet is wrong."""
		row = self.an_account_with_activity()
		end = str(row.posting_date)
		start = frappe.utils.add_days(frappe.utils.getdate(end), -60).isoformat()
		data = self.packet(row.account, start, end)
		self.assertTrue(
			data["arithmetic_check"]["reconciles"],
			f"opening + net != closing for {row.account}: {data['arithmetic_check']}",
		)

	def test_the_closing_balance_matches_the_read_tool(self):
		"""Two routes to the same number, and they must agree."""
		row = self.an_account_with_activity()
		end = str(row.posting_date)
		start = frappe.utils.add_days(frappe.utils.getdate(end), -60).isoformat()
		packet = self.packet(row.account, start, end)
		self.enable("get_account_balance")
		balance = self.tool_data("get_account_balance", {"account": row.account, "as_of": end})
		self.assertEqual(packet["closing_balance"]["amount"], balance["balance"])

	def test_provenance_names_this_call(self):
		row = self.an_account_with_activity()
		end = str(row.posting_date)
		data = self.packet(row.account, end, end)
		self.assertEqual(data["site"], frappe.local.site)
		self.assertTrue(data["mcp_action_log_id"])
		self.assertTrue(frappe.db.exists("MCP Action Log", data["mcp_action_log_id"]))
		self.assertEqual(
			frappe.db.get_value("MCP Action Log", data["mcp_action_log_id"], "tool_name"),
			"generate_compliance_packet",
		)

	def test_generating_a_packet_writes_nothing_but_the_audit_row(self):
		row = self.an_account_with_activity()
		end = str(row.posting_date)
		before = frappe.db.count("Journal Entry")
		self.packet(row.account, end, end)
		self.assertEqual(frappe.db.count("Journal Entry"), before)


class FiscalYearPacketOnThisSite(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.enable("generate_compliance_packet", "fiscal_year_audit_packet")

	def a_year(self):
		company = self.any_company()
		year = frappe.db.get_value("Fiscal Year", {"disabled": 0}, "name", order_by="year_start_date desc")
		if not year:
			self.skipTest("site has no Fiscal Year")
		return company, year

	def packet(self):
		company, year = self.a_year()
		return self.tool_data(
			"generate_compliance_packet",
			{
				"packet_type": "fiscal_year_audit_packet",
				"filters": {"company": company, "fiscal_year": year},
			},
		)

	def test_it_produces_the_documented_shape(self):
		data = self.packet()
		for key in (
			"company",
			"fiscal_year",
			"date_range",
			"trial_balance",
			"trial_balance_totals",
			"income_statement",
			"balance_sheet",
			"accounting_identity",
			"top_20_entries_by_amount",
			"intercompany_activity",
			"document_counts",
			"flags",
			"flag_summary",
		):
			with self.subTest(key=key):
				self.assertIn(key, data)

	def test_debits_equal_credits_on_a_real_ledger(self):
		"""Double entry, checked against the site's own GL. A failure here is a
		finding about the site, not about this app — but it should not happen on a
		ledger ERPNext maintained."""
		data = self.packet()
		self.assertEqual(
			data["trial_balance_totals"]["difference"],
			0,
			"cumulative debits != credits — see TRIAL_BALANCE_IMBALANCE",
		)

	def test_every_trial_balance_row_states_its_basis(self):
		data = self.packet()
		for root, rows in data["trial_balance"]["by_root_type"].items():
			for row in rows:
				with self.subTest(account=row["account"]):
					expected = "fiscal_year" if root in ("Income", "Expense") else "cumulative"
					self.assertEqual(row["basis"], expected)

	def test_the_top_entries_are_ranked(self):
		data = self.packet()
		amounts = [row["amount"] for row in data["top_20_entries_by_amount"]]
		self.assertEqual(amounts, sorted(amounts, reverse=True))
		self.assertLessEqual(len(amounts), 20)

	def test_document_counts_are_none_only_for_absent_doctypes(self):
		data = self.packet()
		for key, value in data["document_counts"].items():
			if key == "note":
				continue
			with self.subTest(count=key):
				self.assertTrue(value is None or isinstance(value, int))

	def test_an_unknown_fiscal_year_is_refused(self):
		company = self.any_company()
		result = self.tool(
			"generate_compliance_packet",
			{
				"packet_type": "fiscal_year_audit_packet",
				"filters": {"company": company, "fiscal_year": "not-a-year"},
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("no Fiscal Year named", result["content"][0]["text"])
