# SPDX-License-Identifier: MIT
"""End-to-end tests against a real bench: migration, encryption, ERPNext validation.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_integration

ONE THING TO KNOW ABOUT ISOLATION. Frappe wraps each test in a transaction and
rolls it back, which is why these tests can create documents freely. The audit
log deliberately breaks that for one case: when a tool fails, `audit.record` is
called with `commit=True` so the failure row outlives the rolled-back
transaction — that is the point of it. Those rows therefore survive the test, so
`setUp` snapshots the log and `tearDown` deletes whatever appeared. A test that
exercises a failure path needs no extra bookkeeping; one that bypasses this base
class does.
"""

import json

import frappe

try:  # Frappe v16 renamed the base classes.
	from frappe.tests import IntegrationTestCase as BaseTestCase
except ImportError:  # Frappe v14 / v15
	from frappe.tests.utils import FrappeTestCase as BaseTestCase

from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request

from erpnext_mcp import audit, mcp, registry, settings

TOKEN = "erpnext-mcp-integration-test-token-0123456789abcdef"


class MCPIntegrationTestCase(BaseTestCase):
	"""Shared setup: a live, loopback-only server with a known token."""

	def setUp(self):
		super().setUp()
		self._logs_before = self._log_names()
		self.doc = frappe.get_single(settings.SETTINGS_DOCTYPE)
		self.doc.enabled = 1
		self.doc.allowed_cidrs = "127.0.0.1/32,::1/128"
		self.doc.auth_token = TOKEN
		self.doc.require_user_context = 0
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.local.request = None
		# Committed audit rows escape the test transaction by design; clean up.
		for name in set(self._log_names()) - set(self._logs_before):
			frappe.delete_doc(audit.LOG_DOCTYPE, name, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDown()

	def _log_names(self):
		return frappe.db.get_all(audit.LOG_DOCTYPE, pluck="name")

	# -- request plumbing -----------------------------------------------------
	def post(self, payload, token=TOKEN, remote_addr="127.0.0.1", headers=None):
		"""Build a real WSGI request and run the whitelisted endpoint on it."""
		all_headers = {"Content-Type": "application/json"}
		if token:
			all_headers["Authorization"] = f"Bearer {token}"
		all_headers.update(headers or {})
		builder = EnvironBuilder(
			method="POST",
			path="/api/method/erpnext_mcp.mcp.handle",
			data=json.dumps(payload),
			headers=all_headers,
			environ_base={"REMOTE_ADDR": remote_addr},
		)
		frappe.local.request = Request(builder.get_environ())
		response = mcp.handle()
		body = response.get_data(as_text=True)
		return (json.loads(body) if body.strip() else None), response.status_code

	def rpc(self, method, params=None, **kwargs):
		message = {"jsonrpc": "2.0", "id": 1, "method": method}
		if params is not None:
			message["params"] = params
		return self.post(message, **kwargs)

	def tool(self, name, arguments=None, **kwargs):
		body, status = self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)
		self.assertEqual(status, 200, body)
		return body["result"]

	def tool_data(self, name, arguments=None, **kwargs):
		result = self.tool(name, arguments, **kwargs)
		self.assertFalse(result["isError"], result["content"][0]["text"])
		return json.loads(result["content"][0]["text"])

	def enable(self, *tool_names):
		for name in tool_names:
			self.doc.set(f"allow_{name}", 1)
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)

	# -- site-shape helpers ---------------------------------------------------
	def any_company(self):
		company = frappe.db.get_value("Company", {}, "name")
		if not company:
			self.skipTest("site has no Company — nothing to read a ledger from")
		return company

	def leaf_accounts(self, company, count=2):
		"""Two postable accounts on this site, or skip.

		Deliberately discovered rather than created: creating a Company builds an
		entire chart of accounts, and the point of this app is that it works
		against whatever chart the operator already has.
		"""
		accounts = frappe.db.get_all(
			"Account",
			filters={"company": company, "is_group": 0, "disabled": 0, "freeze_account": ""},
			pluck="name",
			limit=count,
			order_by="name asc",
		)
		if len(accounts) < count:
			self.skipTest(f"site has fewer than {count} postable accounts for {company}")
		return accounts

	def open_posting_date(self, company):
		"""A date inside a fiscal year, or skip — ERPNext rejects anything else."""
		years = frappe.db.get_all(
			"Fiscal Year",
			filters={"disabled": 0},
			fields=["name", "year_start_date", "year_end_date"],
			order_by="year_start_date desc",
		)
		today = frappe.utils.getdate()
		for year in years:
			if year.year_start_date <= today <= year.year_end_date:
				return today.isoformat()
		if years:
			return frappe.utils.getdate(years[0].year_end_date).isoformat()
		self.skipTest("site has no Fiscal Year")


class Migration(MCPIntegrationTestCase):
	"""The DocType JSON actually became tables with the right shape."""

	def test_both_doctypes_are_installed(self):
		self.assertTrue(frappe.db.exists("DocType", settings.SETTINGS_DOCTYPE))
		self.assertTrue(frappe.db.exists("DocType", audit.LOG_DOCTYPE))

	def test_the_settings_doctype_is_a_single(self):
		self.assertTrue(frappe.get_meta(settings.SETTINGS_DOCTYPE).issingle)

	def test_every_registry_tool_has_a_migrated_switch(self):
		meta = frappe.get_meta(settings.SETTINGS_DOCTYPE)
		for name in registry.TOOLS:
			with self.subTest(tool=name):
				field = meta.get_field(f"allow_{name}")
				self.assertIsNotNone(field, f"allow_{name} did not migrate")
				self.assertEqual(field.fieldtype, "Check")

	def test_install_seeded_the_declared_defaults(self):
		"""A Single stores a row per set field, so without seeding the read-tool
		switches would read as unset on a fresh install."""
		fresh = frappe.new_doc(settings.SETTINGS_DOCTYPE)
		self.assertEqual(int(fresh.enabled or 0), 0)
		for name in registry.READ_TOOLS:
			with self.subTest(tool=name):
				self.assertEqual(int(fresh.get(f"allow_{name}") or 0), 1)
		for name in registry.MUTATING_TOOLS:
			with self.subTest(tool=name):
				self.assertEqual(int(fresh.get(f"allow_{name}") or 0), 0)

	def test_the_log_has_the_columns_the_app_writes(self):
		meta = frappe.get_meta(audit.LOG_DOCTYPE)
		for fieldname in (
			"timestamp",
			"tool_name",
			"arguments_json",
			"result_status",
			"result_summary",
			"caller_ip",
			"docstatus_delta",
		):
			with self.subTest(field=fieldname):
				self.assertTrue(meta.has_field(fieldname))


class TokenStorage(MCPIntegrationTestCase):
	def test_the_token_round_trips_through_frappes_encryption(self):
		"""The standalone suite fakes the auth table; this proves the real one."""
		self.assertEqual(settings.auth_token(), TOKEN)

	def test_the_ciphertext_is_not_the_plaintext(self):
		stored = frappe.db.get_value(
			"Singles",
			{"doctype": settings.SETTINGS_DOCTYPE, "field": "auth_token"},
			"value",
		)
		self.assertNotEqual(stored, TOKEN)

	def test_generate_token_replaces_the_working_token(self):
		result = frappe.get_single(settings.SETTINGS_DOCTYPE).generate_token()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		self.assertEqual(settings.auth_token(), result["token"])
		_, status = self.rpc("ping", token=TOKEN)
		self.assertEqual(status, 401)
		_, status = self.rpc("ping", token=result["token"])
		self.assertEqual(status, 200)


class Gates(MCPIntegrationTestCase):
	def test_the_endpoint_answers_over_a_real_wsgi_request(self):
		body, status = self.rpc("ping")
		self.assertEqual(status, 200)
		self.assertEqual(body["result"], {})

	def test_a_wrong_token_is_401(self):
		_, status = self.rpc("ping", token="not-the-token")
		self.assertEqual(status, 401)

	def test_an_off_site_address_is_403(self):
		_, status = self.rpc("ping", remote_addr="203.0.113.7")
		self.assertEqual(status, 403)

	def test_the_master_switch_produces_a_404(self):
		self.doc.enabled = 0
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		_, status = self.rpc("ping")
		self.assertEqual(status, 404)

	def test_initialize_negotiates_and_names_the_app_version(self):
		from erpnext_mcp import __version__

		body, _ = self.rpc("initialize", {"protocolVersion": "2024-11-05"})
		self.assertEqual(body["result"]["protocolVersion"], "2024-11-05")
		self.assertEqual(body["result"]["serverInfo"]["version"], __version__)

	def test_the_default_catalogue_is_read_only(self):
		body, _ = self.rpc("tools/list")
		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertEqual(names, set(registry.READ_TOOLS))
		self.assertFalse(names & set(registry.MUTATING_TOOLS))


class Permissions(MCPIntegrationTestCase):
	def test_a_non_system_manager_cannot_read_the_settings(self):
		"""The permission rows, enforced by Frappe rather than by this app."""
		user = self._plain_user()
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				frappe.get_doc(settings.SETTINGS_DOCTYPE).check_permission("read")
		finally:
			frappe.set_user("Administrator")

	def test_a_non_system_manager_cannot_read_the_audit_log(self):
		user = self._plain_user()
		frappe.set_user(user)
		try:
			self.assertFalse(
				frappe.has_permission(audit.LOG_DOCTYPE, "read", user=user)
			)
		finally:
			frappe.set_user("Administrator")

	def test_nobody_has_write_permission_on_the_audit_log(self):
		self.assertFalse(
			frappe.db.get_value(
				"DocPerm", {"parent": audit.LOG_DOCTYPE, "write": 1}, "name"
			)
		)

	def test_selftest_requires_system_manager(self):
		user = self._plain_user()
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError):
				mcp.selftest()
		finally:
			frappe.set_user("Administrator")

	def _plain_user(self):
		email = "erpnext-mcp-plain-user@example.test"
		if not frappe.db.exists("User", email):
			user = frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "Plain",
					"send_welcome_email": 0,
					"roles": [],
				}
			)
			user.insert(ignore_permissions=True)
		return email


class ReadToolsAgainstTheRealSite(MCPIntegrationTestCase):
	def test_topology_describes_this_site(self):
		self.any_company()
		data = self.tool_data("get_company_topology")
		self.assertGreaterEqual(data["count"], 1)
		self.assertEqual(data["site"], frappe.local.site)
		self.assertIn("Bank Transaction", data["optional_doctypes"])

	def test_topology_reports_root_types_for_a_real_chart(self):
		company = self.any_company()
		data = self.tool_data("get_company_topology")
		entry = next(c for c in data["companies"] if c["name"] == company)
		if not entry["root_accounts"]:
			self.skipTest(f"{company} has no chart of accounts")
		self.assertTrue(set(entry["root_types"]) <= {
			"Asset",
			"Liability",
			"Income",
			"Expense",
			"Equity",
		})

	def test_a_balance_matches_a_direct_ledger_sum(self):
		"""Compared against the query ERPNext's own General Ledger report uses, so
		a mismatch means this tool is wrong rather than merely different."""
		company = self.any_company()
		account = self.leaf_accounts(company, 1)[0]
		as_of = frappe.utils.today()
		data = self.tool_data(
			"get_account_balance", {"account": account, "company": company, "as_of": as_of}
		)
		expected = frappe.db.get_all(
			"GL Entry",
			filters={"account": account, "posting_date": ("<=", as_of), "is_cancelled": 0},
			fields=["sum(debit) as debit", "sum(credit) as credit"],
		)[0]
		self.assertEqual(
			data["balance"],
			round(float(expected.debit or 0) - float(expected.credit or 0), 2),
		)

	def test_search_accounts_resolves_a_real_docname(self):
		company = self.any_company()
		account = self.leaf_accounts(company, 1)[0]
		account_name = frappe.db.get_value("Account", account, "account_name")
		data = self.tool_data("search_accounts", {"query": account_name, "company": company})
		self.assertIn(account, [row["name"] for row in data["matches"]])

	def test_chart_of_accounts_returns_a_tree(self):
		company = self.any_company()
		data = self.tool_data("get_chart_of_accounts", {"company": company})
		if not data["flat_count"]:
			self.skipTest(f"{company} has no chart of accounts")
		self.assertTrue(data["accounts"])
		self.assertIn("children", data["accounts"][0])

	def test_fiscal_years_come_back(self):
		if not frappe.db.count("Fiscal Year"):
			self.skipTest("site has no Fiscal Year")
		data = self.tool_data("list_fiscal_years")
		self.assertGreaterEqual(data["count"], 1)

	def test_bank_statement_degrades_on_a_site_without_the_doctype(self):
		if frappe.db.exists("DocType", "Bank Statement"):
			self.skipTest("this site has the Bank Statement doctype")
		result = self.tool("get_bank_statement", {"name": "anything"})
		self.assertTrue(result["isError"])
		self.assertIn("not installed on this site", result["content"][0]["text"])


class MutationsThroughRealERPNext(MCPIntegrationTestCase):
	"""The point of these: ERPNext's own validation runs, not ours."""

	def test_a_created_journal_entry_is_a_real_draft(self):
		company = self.any_company()
		debit, credit = self.leaf_accounts(company, 2)
		self.enable("create_journal_entry")
		data = self.tool_data(
			"create_journal_entry",
			{
				"company": company,
				"posting_date": self.open_posting_date(company),
				"user_remark": "erpnext_mcp integration test",
				"accounts": [
					{"account": debit, "debit": 1},
					{"account": credit, "credit": 1},
				],
			},
		)
		doc = frappe.get_doc("Journal Entry", data["name"])
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(len(doc.accounts), 2)
		self.assertEqual(doc.total_debit, 1)
		self.assertEqual(
			frappe.db.count(
				"GL Entry", {"voucher_type": "Journal Entry", "voucher_no": doc.name}
			),
			0,
			"a draft must not have written GL Entries",
		)

	def test_an_unbalanced_entry_creates_nothing_in_the_database(self):
		company = self.any_company()
		debit, credit = self.leaf_accounts(company, 2)
		self.enable("create_journal_entry")
		before = frappe.db.count("Journal Entry")
		result = self.tool(
			"create_journal_entry",
			{
				"company": company,
				"posting_date": self.open_posting_date(company),
				"user_remark": "unbalanced",
				"accounts": [
					{"account": debit, "debit": 100},
					{"account": credit, "credit": 99},
				],
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("do not equal credits", result["content"][0]["text"])
		self.assertEqual(frappe.db.count("Journal Entry"), before)

	def test_submitting_writes_gl_entries_and_cancelling_reverses_them(self):
		company = self.any_company()
		debit, credit = self.leaf_accounts(company, 2)
		self.enable("create_journal_entry", "submit_journal_entry", "cancel_journal_entry")
		created = self.tool_data(
			"create_journal_entry",
			{
				"company": company,
				"posting_date": self.open_posting_date(company),
				"user_remark": "erpnext_mcp submit test",
				"accounts": [
					{"account": debit, "debit": 1},
					{"account": credit, "credit": 1},
				],
			},
		)
		submitted = self.tool_data("submit_journal_entry", {"name": created["name"]})
		self.assertEqual(submitted["docstatus"], 1)
		self.assertGreater(submitted["gl_entries_created"], 0)

		cancelled = self.tool_data(
			"cancel_journal_entry",
			{"name": created["name"], "reason": "erpnext_mcp integration test cleanup"},
		)
		self.assertEqual(cancelled["docstatus"], 2)
		self.assertEqual(frappe.get_doc("Journal Entry", created["name"]).docstatus, 2)

	def test_the_cancellation_reason_lands_on_the_document(self):
		company = self.any_company()
		debit, credit = self.leaf_accounts(company, 2)
		self.enable("create_journal_entry", "submit_journal_entry", "cancel_journal_entry")
		created = self.tool_data(
			"create_journal_entry",
			{
				"company": company,
				"posting_date": self.open_posting_date(company),
				"user_remark": "erpnext_mcp comment test",
				"accounts": [
					{"account": debit, "debit": 1},
					{"account": credit, "credit": 1},
				],
			},
		)
		self.tool_data("submit_journal_entry", {"name": created["name"]})
		reason = "erpnext_mcp cancellation reason marker"
		self.tool_data("cancel_journal_entry", {"name": created["name"], "reason": reason})
		comments = frappe.db.get_all(
			"Comment",
			filters={"reference_doctype": "Journal Entry", "reference_name": created["name"]},
			pluck="content",
		)
		self.assertTrue(any(reason in (c or "") for c in comments))

	def test_a_disabled_mutating_tool_is_refused_even_with_a_valid_token(self):
		company = self.any_company()
		debit, credit = self.leaf_accounts(company, 2)
		result = self.tool(
			"create_journal_entry",
			{
				"company": company,
				"posting_date": self.open_posting_date(company),
				"user_remark": "should be blocked",
				"accounts": [
					{"account": debit, "debit": 1},
					{"account": credit, "credit": 1},
				],
			},
		)
		self.assertTrue(result["isError"])
		self.assertIn("allow_create_journal_entry", result["content"][0]["text"])

	def test_mutations_are_attributed_to_the_configured_mcp_user(self):
		company = self.any_company()
		debit, credit = self.leaf_accounts(company, 2)
		email = "erpnext-mcp-system-user@example.test"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "MCP",
					"send_welcome_email": 0,
					"roles": [{"role": "Accounts Manager"}, {"role": "Accounts User"}],
				}
			).insert(ignore_permissions=True)
		self.doc.require_user_context = 1
		self.doc.mcp_system_user = email
		self.doc.allow_create_journal_entry = 1
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)

		data = self.tool_data(
			"create_journal_entry",
			{
				"company": company,
				"posting_date": self.open_posting_date(company),
				"user_remark": "erpnext_mcp attribution test",
				"accounts": [
					{"account": debit, "debit": 1},
					{"account": credit, "credit": 1},
				],
			},
		)
		self.assertEqual(frappe.db.get_value("Journal Entry", data["name"], "owner"), email)


class AuditTrail(MCPIntegrationTestCase):
	def test_a_successful_read_is_logged(self):
		self.any_company()
		self.tool_data("get_company_topology")
		row = frappe.db.get_all(
			audit.LOG_DOCTYPE,
			filters={"tool_name": "get_company_topology"},
			fields=["result_status", "caller_ip", "arguments_json", "result_summary"],
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(row, "no audit row was written")
		self.assertEqual(row[0].result_status, "Success")
		self.assertEqual(row[0].caller_ip, "127.0.0.1")

	def test_a_rejected_call_is_logged_as_unauthorized(self):
		self.rpc("ping", token="wrong")
		row = frappe.db.get_all(
			audit.LOG_DOCTYPE,
			filters={"tool_name": "<transport>"},
			fields=["result_status", "result_summary"],
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(row)
		self.assertEqual(row[0].result_status, "Unauthorized")
		self.assertIn("bearer token", row[0].result_summary)

	def test_an_audit_row_cannot_be_edited(self):
		self.any_company()
		self.tool_data("list_fiscal_years")
		name = frappe.db.get_all(
			audit.LOG_DOCTYPE,
			filters={"tool_name": "list_fiscal_years"},
			pluck="name",
			order_by="creation desc",
			limit=1,
		)[0]
		doc = frappe.get_doc(audit.LOG_DOCTYPE, name)
		doc.result_summary = "tampered"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_a_failed_tool_still_leaves_a_row(self):
		result = self.tool("get_journal_entry", {"name": "ACC-JV-DOES-NOT-EXIST-0000"})
		self.assertTrue(result["isError"])
		row = frappe.db.get_all(
			audit.LOG_DOCTYPE,
			filters={"tool_name": "get_journal_entry", "result_status": "Error"},
			pluck="name",
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(row, "a failed tool wrote no audit row")
