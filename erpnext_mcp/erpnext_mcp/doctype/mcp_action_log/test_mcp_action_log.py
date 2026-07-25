# SPDX-License-Identifier: MIT
"""Doctype tests for MCP Action Log — append-only, enforced by the framework."""

import frappe

try:  # Frappe v16 renamed the base classes.
	from frappe.tests import IntegrationTestCase as BaseTestCase
except ImportError:  # Frappe v14 / v15
	from frappe.tests.utils import FrappeTestCase as BaseTestCase

from erpnext_mcp import audit


class TestMCPActionLog(BaseTestCase):
	def row(self, **overrides):
		payload = {
			"doctype": audit.LOG_DOCTYPE,
			"timestamp": frappe.utils.now(),
			"tool_name": "get_company_topology",
			"caller_ip": "127.0.0.1",
			"arguments_json": "{}",
			"result_status": "Success",
			"result_summary": "1 company/companies",
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert(ignore_permissions=True)
		return doc

	def test_a_row_inserts(self):
		doc = self.row()
		self.assertTrue(frappe.db.exists(audit.LOG_DOCTYPE, doc.name))

	def test_a_row_cannot_be_updated(self):
		doc = self.row()
		doc.result_summary = "rewritten history"
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_the_refused_update_did_not_land(self):
		doc = self.row(result_summary="original")
		reloaded = frappe.get_doc(audit.LOG_DOCTYPE, doc.name)
		reloaded.result_summary = "tampered"
		with self.assertRaises(frappe.ValidationError):
			reloaded.save(ignore_permissions=True)
		self.assertEqual(
			frappe.db.get_value(audit.LOG_DOCTYPE, doc.name, "result_summary"), "original"
		)

	def test_a_row_can_still_be_deleted_so_the_log_can_be_pruned(self):
		"""Deletion is the intended escape valve, and Frappe records it in
		Deleted Document, so a pruned row still leaves a trace."""
		doc = self.row()
		frappe.delete_doc(audit.LOG_DOCTYPE, doc.name, force=True, ignore_permissions=True)
		self.assertFalse(frappe.db.exists(audit.LOG_DOCTYPE, doc.name))

	def test_result_status_is_constrained_to_the_four_outcomes(self):
		options = frappe.get_meta(audit.LOG_DOCTYPE).get_field("result_status").options
		self.assertEqual(
			options.split("\n"), ["Success", "Error", "Blocked", "Unauthorized"]
		)

	def test_the_doctype_does_not_write_version_rows(self):
		doc = self.row()
		self.assertEqual(
			frappe.db.count("Version", {"ref_doctype": audit.LOG_DOCTYPE, "docname": doc.name}),
			0,
		)

	def test_audit_record_writes_through_the_helper(self):
		name = audit.record(
			"search_accounts",
			{"query": "cash"},
			audit.STATUS_SUCCESS,
			summary="search 'cash': 1 of 1 match(es)",
			caller_ip="127.0.0.1",
		)
		self.assertIsNotNone(name)
		self.assertEqual(
			frappe.db.get_value(audit.LOG_DOCTYPE, name, "result_status"), "Success"
		)

	def test_audit_record_redacts_secret_looking_arguments(self):
		name = audit.record(
			"hypothetical", {"api_key": "sk-live-abc"}, audit.STATUS_SUCCESS
		)
		self.assertIn(
			"***redacted***",
			frappe.db.get_value(audit.LOG_DOCTYPE, name, "arguments_json"),
		)
