# SPDX-License-Identifier: MIT
"""MCP Action Log: what gets written, and what cannot erase it."""

import contextlib
import json

from erpnext_mcp import audit, registry
from erpnext_mcp.errors import ToolError

from .fixtures import MAIN, SeededTestCase, cash, sales
from .harness import STORE, frappe

ALL_ON = {f"allow_{name}": 1 for name in registry.MUTATING_TOOLS}


@contextlib.contextmanager
def patched_handler(tool_name, handler):
	"""Swap one tool's implementation for the duration of a test.

	Patching an existing tool rather than registering a new one is deliberate: a
	tool with no `allow_` field on the settings doctype is refused before
	dispatch, so a made-up tool could never reach the code these tests are
	about.
	"""
	original = registry.TOOLS[tool_name]["handler"]
	registry.TOOLS[tool_name]["handler"] = handler
	try:
		yield
	finally:
		registry.TOOLS[tool_name]["handler"] = original


class EveryCallIsLogged(SeededTestCase):
	def test_a_read_call_is_logged(self):
		"""Reads are logged because the question after the fact is usually "what
		did it see", not "what did it change"."""
		self.tool_data("search_accounts", {"query": "cash"})
		row = self.assertAudited("search_accounts", status="Success")
		self.assertEqual(row["caller_ip"], "127.0.0.1")
		self.assertIn("match", row["result_summary"])

	def test_the_row_records_the_arguments(self):
		self.tool_data("get_account_balance", {"account": cash(), "company": MAIN})
		row = self.assertAudited("get_account_balance")
		self.assertEqual(json.loads(row["arguments_json"])["account"], cash())

	def test_every_read_tool_writes_exactly_one_row(self):
		before = len(self.audit_rows())
		self.tool_data("get_company_topology")
		self.tool_data("list_fiscal_years")
		self.assertEqual(len(self.audit_rows()), before + 2)

	def test_a_tool_error_is_logged_as_error_with_the_message(self):
		self.tool_error("get_journal_entry", {"name": "ACC-JV-9999-1"})
		row = self.assertAudited("get_journal_entry", status="Error")
		self.assertIn("no Journal Entry named", row["result_summary"])

	def test_an_unknown_tool_is_logged(self):
		self.tool_error("rm_rf_slash")
		self.assertAudited("rm_rf_slash", status="Error")

	def test_the_caller_ip_is_the_one_the_gate_used(self):
		"""A log row and a gate decision must never disagree about who called."""
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8")
		self.tool_data(
			"list_fiscal_years",
			headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.42"},
			remote_addr="127.0.0.1",
		)
		self.assertEqual(self.assertAudited("list_fiscal_years")["caller_ip"], "10.0.0.42")


class SurvivingFailure(SeededTestCase):
	def test_a_failed_tool_leaves_no_partial_document_but_keeps_the_log_row(self):
		"""The case the whole rollback dance exists for: a tool that wrote
		something and then failed must lose the write and keep the record."""

		def half_write(args):
			frappe.get_doc(
				{"doctype": "Payment Entry", "posting_date": "2026-05-01", "paid_amount": 1}
			).insert()
			raise ToolError("changed my mind")

		before = len(STORE.rows("Payment Entry"))
		with patched_handler("search_accounts", half_write):
			message = self.tool_error("search_accounts", {"query": "x"})

		self.assertEqual(message, "changed my mind")
		self.assertEqual(len(STORE.rows("Payment Entry")), before, "partial write survived")
		self.assertAudited("search_accounts", status="Error")

	def test_the_failure_row_is_committed_so_it_outlives_the_request(self):
		def boom(args):
			raise ToolError("nope")

		before = STORE.committed
		with patched_handler("search_accounts", boom):
			self.tool_error("search_accounts", {"query": "x"})
		self.assertGreater(STORE.committed, before)

	def test_an_unexpected_exception_is_reported_by_type_and_logged(self):
		def boom(args):
			return 1 / 0

		with patched_handler("search_accounts", boom):
			message = self.tool_error("search_accounts", {"query": "x"})
		self.assertIn("ZeroDivisionError", message)
		self.assertAudited("search_accounts", status="Error")
		self.assertTrue(
			any("tool search_accounts failed" in (e["title"] or "") for e in STORE.errors),
			"no Error Log entry for an unexpected exception",
		)

	def test_a_broken_audit_write_does_not_break_the_tool(self):
		"""An audit failure must not turn a working call into a 500 — and must not
		hide the fact that it failed either."""
		original = audit.frappe.get_doc

		def refuse(*args, **kwargs):
			if args and isinstance(args[0], dict) and args[0].get("doctype") == audit.LOG_DOCTYPE:
				raise RuntimeError("log table is on fire")
			return original(*args, **kwargs)

		audit.frappe.get_doc = refuse
		try:
			data = self.tool_data("list_fiscal_years")
		finally:
			audit.frappe.get_doc = original
		self.assertEqual(data["count"], 2)
		self.assertEqual(self.audit_rows(), [])
		self.assertTrue(any("audit log write failed" in (e["title"] or "") for e in STORE.errors))


class Redaction(SeededTestCase):
	def test_secret_looking_keys_are_masked(self):
		"""No current tool takes a secret. A future one might, and this log is
		read-only forever — the wrong moment to discover it."""
		row = audit.record(
			"hypothetical",
			{"account": "1100", "api_key": "sk-live-1234", "password": "hunter2"},
			audit.STATUS_SUCCESS,
		)
		stored = json.loads(STORE.get_raw(audit.LOG_DOCTYPE, row)["arguments_json"])
		self.assertEqual(stored["account"], "1100")
		self.assertEqual(stored["api_key"], "***redacted***")
		self.assertEqual(stored["password"], "***redacted***")

	def test_one_oversized_value_is_elided_rather_than_crowding_the_row(self):
		"""Truncating the serialised payload would throw away every key that sorts
		after the big one — which for attach_file_to_document is the parent
		document itself. Eliding the value keeps its length and keeps the rest."""
		row = audit.record(
			"hypothetical", {"blob": "x" * 20000, "name": "ACC-JV-2026-02329"}, audit.STATUS_SUCCESS
		)
		stored = json.loads(STORE.get_raw(audit.LOG_DOCTYPE, row)["arguments_json"])
		self.assertEqual(stored["blob"], "<20000 characters elided>")
		self.assertEqual(stored["name"], "ACC-JV-2026-02329")

	def test_a_payload_of_many_values_is_still_truncated_and_says_so(self):
		row = audit.record("hypothetical", {f"k{i}": "x" * 400 for i in range(50)}, audit.STATUS_SUCCESS)
		stored = STORE.get_raw(audit.LOG_DOCTYPE, row)["arguments_json"]
		self.assertLessEqual(len(stored), 8000)
		self.assertTrue(stored.endswith("…[truncated]"))

	def test_an_oversized_summary_is_truncated(self):
		row = audit.record("hypothetical", {}, audit.STATUS_SUCCESS, summary="y" * 9000)
		self.assertLessEqual(len(STORE.get_raw(audit.LOG_DOCTYPE, row)["result_summary"]), 2000)

	def test_unserialisable_arguments_do_not_break_the_write(self):
		row = audit.record("hypothetical", {"thing": object()}, audit.STATUS_SUCCESS)
		self.assertIsNotNone(row)


class Immutability(SeededTestCase):
	def test_a_written_row_cannot_be_edited(self):
		self.tool_data("list_fiscal_years")
		name = self.assertAudited("list_fiscal_years")["name"]
		doc = frappe.get_doc(audit.LOG_DOCTYPE, name)
		doc.result_summary = "nothing to see here"
		with self.assertRaises(Exception) as caught:
			doc.save()
		self.assertIn("immutable", str(caught.exception))

	def test_the_stored_row_is_unchanged_after_a_refused_edit(self):
		self.tool_data("list_fiscal_years")
		row = self.assertAudited("list_fiscal_years")
		original = row["result_summary"]
		doc = frappe.get_doc(audit.LOG_DOCTYPE, row["name"])
		doc.result_summary = "tampered"
		with self.assertRaises(Exception):
			doc.save()
		self.assertEqual(STORE.get_raw(audit.LOG_DOCTYPE, row["name"])["result_summary"], original)

	def test_the_doctype_grants_no_write_permission(self):
		from .harness import _load_app_doctype

		permissions = _load_app_doctype("mcp_action_log")["permissions"]
		self.assertEqual([p["role"] for p in permissions], ["System Manager"])
		for permission in permissions:
			self.assertFalse(permission.get("write"))
			self.assertFalse(permission.get("create"))
			self.assertTrue(permission.get("read"))

	def test_the_doctype_does_not_track_changes(self):
		"""Version rows on an append-only log double every write for nothing."""
		from .harness import _load_app_doctype

		self.assertFalse(_load_app_doctype("mcp_action_log").get("track_changes"))


class MutationAudit(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def test_the_full_create_submit_cancel_sequence_is_traceable(self):
		created = self.tool_data(
			"create_journal_entry",
			{
				"company": MAIN,
				"posting_date": "2026-03-01",
				"user_remark": "Accrual",
				"accounts": [
					{"account": cash(), "debit": 20},
					{"account": sales(), "credit": 20},
				],
			},
		)
		self.tool_data("submit_journal_entry", {"name": created["name"]})
		self.tool_data("cancel_journal_entry", {"name": created["name"], "reason": "Reversed next month"})
		deltas = [row["docstatus_delta"] for row in self.audit_rows() if row["docstatus_delta"]]
		self.assertEqual(deltas, ["none → 0 (draft)", "0 → 1 (submitted)", "1 → 2 (cancelled)"])

	def test_a_read_call_records_no_docstatus_delta(self):
		self.tool_data("list_fiscal_years")
		self.assertEqual(self.assertAudited("list_fiscal_years")["docstatus_delta"], "")
