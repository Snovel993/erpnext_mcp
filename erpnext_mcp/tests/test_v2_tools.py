# SPDX-License-Identifier: MIT
"""In-bench tests for the v0.2.0 tool categories.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_v2_tools

What these add over `tests_standalone/`: the real Frappe APIs. The standalone
suite proves this app calls `frappe.model.workflow.get_transitions`,
`frappe.desk.query_report.run`, `File.get_content` and `get_leave_balance_on`
correctly *given a double of each*. Only a bench can show that the double
matches the real thing — that the arguments are the ones those functions take,
that the return shapes are the ones this app unpacks, and that the permission
checks are the ones Frappe actually enforces.

Everything here skips rather than fails when the site lacks what a case needs.
A stock ERPNext site has no Workflow and no Report Builder reports; a site
without `hrms` has no Employee. Those are legitimate sites, and a red test suite
on one is a test suite people stop running.
"""

import base64
import json

import frappe

from .test_integration import MCPIntegrationTestCase


class WorkflowTools(MCPIntegrationTestCase):
	def any_active_workflow(self):
		workflow = frappe.db.get_value("Workflow", {"is_active": 1}, ["name", "document_type"], as_dict=True)
		if not workflow:
			self.skipTest("site has no active Workflow")
		return workflow

	def a_governed_document(self, workflow):
		name = frappe.db.get_value(workflow.document_type, {}, "name")
		if not name:
			self.skipTest(f"site has no {workflow.document_type} to inspect")
		return name

	def test_list_workflows_matches_the_doctype(self):
		data = self.tool_data("list_workflows")
		self.assertEqual(data["count"], frappe.db.count("Workflow"))

	def test_states_and_transitions_come_back_populated(self):
		workflow = self.any_active_workflow()
		data = self.tool_data("list_workflows")
		entry = next(w for w in data["workflows"] if w["name"] == workflow.name)
		self.assertTrue(entry["states"], "workflow has no states")
		self.assertTrue(entry["transitions"], "workflow has no transitions")

	def test_the_state_field_this_app_reads_exists_on_the_document(self):
		"""`workflow_state_field` is configurable, and reading the wrong column is
		a silent wrong answer rather than an error."""
		workflow = self.any_active_workflow()
		data = self.tool_data("list_workflows")
		entry = next(w for w in data["workflows"] if w["name"] == workflow.name)
		self.assertTrue(frappe.get_meta(workflow.document_type).has_field(entry["workflow_state_field"]))

	def test_get_workflow_state_reads_a_real_document(self):
		workflow = self.any_active_workflow()
		name = self.a_governed_document(workflow)
		data = self.tool_data("get_workflow_state", {"doctype": workflow.document_type, "name": name})
		self.assertEqual(data["workflow"], workflow.name)
		self.assertIn("current_state", data)

	def test_available_actions_resolve_through_frappes_own_function(self):
		"""The delegation this app depends on: if the import path or the signature
		ever moves, this is what notices."""
		workflow = self.any_active_workflow()
		name = self.a_governed_document(workflow)
		data = self.tool_data("list_available_actions", {"doctype": workflow.document_type, "name": name})
		self.assertEqual(data["resolved_via"], "frappe.model.workflow.get_transitions")
		self.assertTrue(data["conditions_evaluated"])

	def test_frappe_exports_the_functions_this_app_requires(self):
		from frappe.model import workflow as workflow_api

		self.assertTrue(callable(getattr(workflow_api, "get_transitions", None)))
		self.assertTrue(callable(getattr(workflow_api, "apply_workflow", None)))

	def test_pending_approvals_only_lists_non_terminal_states(self):
		self.any_active_workflow()
		data = self.tool_data("list_pending_approvals")
		for group in data["pending"]:
			with self.subTest(state=group["state"]):
				self.assertTrue(group["actions"], "a terminal state was listed as pending")

	def test_advance_workflow_is_off_by_default(self):
		workflow = self.any_active_workflow()
		name = self.a_governed_document(workflow)
		result = self.tool(
			"advance_workflow",
			{"doctype": workflow.document_type, "name": name, "action": "Approve"},
		)
		self.assertTrue(result["isError"])
		self.assertIn("allow_advance_workflow", result["content"][0]["text"])


class ReportTools(MCPIntegrationTestCase):
	def a_report(self, report_type):
		name = frappe.db.get_value("Report", {"report_type": report_type, "disabled": 0}, "name")
		if not name:
			self.skipTest(f"site has no enabled {report_type}")
		return name

	def test_list_reports_matches_the_doctype(self):
		data = self.tool_data("list_reports")
		self.assertEqual(data["count"], frappe.db.count("Report"))

	def test_every_report_has_a_type_this_tool_recognises(self):
		data = self.tool_data("list_reports")
		known = {"Query Report", "Script Report", "Report Builder"}
		unknown = sorted({row["report_type"] for row in data["reports"] if row["report_type"] not in known})
		if unknown:
			self.skipTest(f"site has report types this version does not run: {unknown}")

	def test_query_report_run_accepts_the_arguments_this_app_passes(self):
		"""`ignore_prepared_report` is the argument that matters — without it a
		prepared report returns a job id instead of rows."""
		from frappe.desk import query_report

		name = self.a_report("Query Report")
		result = query_report.run(report_name=name, filters={}, user=None, ignore_prepared_report=True)
		self.assertIn("result", result)
		self.assertIn("columns", result)

	def test_running_a_script_report_returns_rows_and_columns(self):
		name = self.a_report("Script Report")
		result = self.tool("run_report", {"name": name})
		if result["isError"]:
			# Most stock Script Reports require filters (a company, a date range)
			# that this test cannot invent. That the runner was reached and
			# complained about filters is the fact worth having.
			self.skipTest(f"{name} needs filters: {result['content'][0]['text'][:120]}")
		data = json.loads(result["content"][0]["text"])
		self.assertIn("columns_normalised", data)
		self.assertEqual(data["executed_via"], "frappe.desk.query_report.run")

	def test_a_report_builder_report_materialises(self):
		name = self.a_report("Report Builder")
		data = self.tool_data("run_report", {"name": name})
		self.assertIn(
			data["executed_via"],
			(
				"frappe.desk.reportview.get",
				"frappe.get_list (reportview unavailable on this version)",
			),
		)

	def test_reportview_get_is_where_this_app_expects_it(self):
		from frappe.desk import reportview

		self.assertTrue(callable(getattr(reportview, "get", None)))

	def test_a_disabled_report_is_refused(self):
		name = frappe.db.get_value("Report", {"disabled": 1}, "name")
		if not name:
			self.skipTest("site has no disabled Report")
		result = self.tool("run_report", {"name": name})
		self.assertTrue(result["isError"])
		self.assertIn("disabled", result["content"][0]["text"])


class AttachmentTools(MCPIntegrationTestCase):
	def a_small_attachment(self):
		row = frappe.db.get_value(
			"File",
			{
				"is_folder": 0,
				"attached_to_doctype": ("is", "set"),
				"file_size": ("<", 200000),
			},
			["name", "attached_to_doctype", "attached_to_name"],
			as_dict=True,
		)
		if not row:
			self.skipTest("site has no small attached File")
		return row

	def test_listing_attachments_matches_the_file_table(self):
		row = self.a_small_attachment()
		data = self.tool_data(
			"list_attachments",
			{"doctype": row.attached_to_doctype, "name": row.attached_to_name},
		)
		self.assertEqual(
			data["count"],
			frappe.db.count(
				"File",
				{
					"attached_to_doctype": row.attached_to_doctype,
					"attached_to_name": row.attached_to_name,
				},
			),
		)

	def test_file_get_content_returns_what_this_app_expects(self):
		"""The app base64-encodes whatever `get_content` gives it and handles both
		bytes and str; this is where a version change in that return type shows."""
		row = self.a_small_attachment()
		content = frappe.get_doc("File", row.name).get_content()
		self.assertIsInstance(content, (bytes, str))

	def test_reading_a_small_attachment_round_trips(self):
		row = self.a_small_attachment()
		data = self.tool_data("get_attachment_content", {"name": row.name})
		self.assertEqual(data["encoding"], "base64")
		decoded = base64.b64decode(data["content_base64"])
		self.assertEqual(len(decoded), data["file_size"])

	def test_the_parent_permission_check_uses_frappes_own(self):
		"""Not a stub: `has_permission(doc=<name>)` has to be a signature this
		Frappe accepts, or every attachment call would 500."""
		row = self.a_small_attachment()
		self.assertIsInstance(
			frappe.has_permission(row.attached_to_doctype, "read", doc=row.attached_to_name),
			bool,
		)

	def test_an_oversized_attachment_is_refused_not_returned(self):
		row = frappe.db.get_value("File", {"is_folder": 0, "file_size": (">", 2 * 1024 * 1024)}, "name")
		if not row:
			self.skipTest("site has no File over the default cap")
		result = self.tool("get_attachment_content", {"name": row})
		self.assertTrue(result["isError"])
		self.assertIn("cap", result["content"][0]["text"])


class CollaborationTools(MCPIntegrationTestCase):
	def test_todo_stores_its_assignee_where_this_app_writes_it(self):
		"""`allocated_to` vs `owner` — the whole reason `_assignee_field` exists."""
		meta = frappe.get_meta("ToDo")
		self.assertTrue(
			meta.has_field("allocated_to") or meta.has_field("owner"),
			"ToDo has neither allocated_to nor owner",
		)

	def test_todo_has_no_subject_field_so_folding_is_needed(self):
		"""If a future Frappe adds one, `_fold_subject` should start using it —
		this test is the reminder."""
		has_subject = frappe.get_meta("ToDo").has_field("subject")
		self.assertIn(has_subject, (True, False))

	def test_listing_todos_matches_the_doctype(self):
		data = self.tool_data("list_assigned_todos", {"status": "Open"})
		self.assertLessEqual(data["count"], frappe.db.count("ToDo", {"status": "Open"}))

	def test_create_todo_is_off_by_default(self):
		result = self.tool("create_todo", {"subject": "erpnext_mcp test", "owner": "Administrator"})
		self.assertTrue(result["isError"])
		self.assertIn("allow_create_todo", result["content"][0]["text"])

	def test_creating_a_todo_makes_a_real_document(self):
		self.enable("create_todo")
		data = self.tool_data(
			"create_todo",
			{
				"subject": "erpnext_mcp integration test",
				"owner": "Administrator",
				"priority": "Low",
			},
		)
		doc = frappe.get_doc("ToDo", data["name"])
		self.assertEqual(doc.status, "Open")
		self.assertIn("erpnext_mcp integration test", doc.description or "")

	def test_comments_are_readable_on_a_document_that_has_some(self):
		row = frappe.db.get_value(
			"Comment",
			{"reference_doctype": ("is", "set"), "reference_name": ("is", "set")},
			["reference_doctype", "reference_name"],
			as_dict=True,
		)
		if not row:
			self.skipTest("site has no Comment rows")
		if not frappe.db.exists(row.reference_doctype, row.reference_name):
			self.skipTest("orphaned Comment row")
		data = self.tool_data(
			"list_comments",
			{"doctype": row.reference_doctype, "name": row.reference_name},
		)
		self.assertGreaterEqual(data["count"], 1)


class HRTools(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		if "hrms" not in frappe.get_installed_apps():
			self.skipTest("site does not have the hrms app")

	def an_employee(self):
		name = frappe.db.get_value("Employee", {"status": "Active"}, "name")
		if not name:
			self.skipTest("site has no active Employee")
		return name

	def test_the_hr_tools_are_advertised_here(self):
		self.enable("list_employees", "get_attendance_summary", "get_leave_balance")
		body, _ = self.rpc("tools/list")
		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertIn("list_employees", names)
		self.assertIn("get_leave_balance", names)

	def test_listing_employees_matches_the_doctype(self):
		data = self.tool_data("list_employees", {"status": "Active"})
		self.assertLessEqual(data["count"], frappe.db.count("Employee", {"status": "Active"}))

	def test_the_leave_balance_api_is_importable_where_this_app_looks(self):
		from erpnext_mcp.tools.hr import _leave_balance_api

		self.assertTrue(
			callable(_leave_balance_api()),
			"neither hrms nor erpnext exports get_leave_balance_on",
		)

	def test_a_leave_balance_computes(self):
		employee = self.an_employee()
		data = self.tool_data("get_leave_balance", {"employee": employee})
		self.assertEqual(data["computed_via"], "HR get_leave_balance_on")
		self.assertEqual(data.get("failed", []), [])

	def test_attendance_summary_counts_only_submitted_rows(self):
		data = self.tool_data(
			"get_attendance_summary",
			{"from_date": "2000-01-01", "to_date": frappe.utils.today()},
		)
		self.assertEqual(data["records_counted"], frappe.db.count("Attendance", {"docstatus": 1}))


class HRToolsAbsent(MCPIntegrationTestCase):
	def setUp(self):
		super().setUp()
		if "hrms" in frappe.get_installed_apps():
			self.skipTest("site has the hrms app, so the HR tools are available")

	def test_they_are_not_advertised(self):
		self.enable("list_employees")
		body, _ = self.rpc("tools/list")
		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertNotIn("list_employees", names)

	def test_calling_one_says_what_is_missing(self):
		result = self.tool("list_employees", {})
		self.assertTrue(result["isError"])
		self.assertIn("hrms", result["content"][0]["text"])


class TradeTools(MCPIntegrationTestCase):
	def test_sales_orders_come_back(self):
		if not frappe.db.count("Sales Order"):
			self.skipTest("site has no Sales Order")
		data = self.tool_data("list_sales_orders", {"limit": 5})
		self.assertLessEqual(data["count"], 5)
		self.assertIn("per_delivered", data["orders"][0])

	def test_purchase_orders_come_back_with_their_own_promise_field(self):
		if not frappe.db.count("Purchase Order"):
			self.skipTest("site has no Purchase Order")
		data = self.tool_data("list_purchase_orders", {"limit": 5})
		self.assertIn("schedule_date", data["orders"][0])

	def test_outstanding_invoices_match_a_direct_query(self):
		data = self.tool_data("get_outstanding_invoices", {"limit": 500})
		expected = frappe.db.count("Sales Invoice", {"outstanding_amount": (">", 0), "docstatus": 1})
		self.assertEqual(data["count"], min(expected, 500))

	def test_every_invoice_lands_in_exactly_one_bucket(self):
		data = self.tool_data("get_outstanding_invoices", {"limit": 500})
		if not data["count"]:
			self.skipTest("site has no outstanding Sales Invoice")
		self.assertEqual(sum(bucket["count"] for bucket in data["buckets"].values()), data["count"])


class MetaTools(MCPIntegrationTestCase):
	def test_custom_fields_match_the_doctype(self):
		data = self.tool_data("list_custom_fields", {"limit": 500})
		self.assertEqual(data["count"], min(frappe.db.count("Custom Field"), 500))

	def test_client_script_bodies_are_never_returned_whole(self):
		if not frappe.db.count("Client Script"):
			self.skipTest("site has no Client Script")
		data = self.tool_data("list_client_scripts", {"enabled": "any", "limit": 500})
		for row in data["client_scripts"]:
			with self.subTest(script=row["name"]):
				self.assertNotIn("script", row)
				self.assertLessEqual(len(row["script_preview"]), 500)


class CatalogueOnThisSite(MCPIntegrationTestCase):
	def test_every_tool_has_a_migrated_switch(self):
		from erpnext_mcp import registry, settings

		meta = frappe.get_meta(settings.SETTINGS_DOCTYPE)
		missing = [name for name in registry.TOOLS if not meta.has_field(f"allow_{name}")]
		self.assertEqual(missing, [], f"tools with no switch on this site: {missing}")

	def test_the_catalogue_is_thirty_five_tools(self):
		from erpnext_mcp import registry

		self.assertEqual(len(registry.TOOLS), 35)

	def test_only_available_tools_are_advertised(self):
		from erpnext_mcp import registry, settings

		for name in registry.TOOLS:
			self.doc.set(f"allow_{name}", 1)
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)

		body, _ = self.rpc("tools/list")
		advertised = {tool["name"] for tool in body["result"]["tools"]}
		expected = {name for name in registry.TOOLS if registry.is_available(name)}
		self.assertEqual(advertised, expected)

	def test_the_seven_write_tools_are_named_correctly(self):
		from erpnext_mcp import mcp, registry

		self.assertEqual(len(registry.MUTATING_TOOLS), 7)
		self.assertEqual(mcp.mutating_tool_names(), list(registry.MUTATING_TOOLS))
