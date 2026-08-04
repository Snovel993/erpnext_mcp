# SPDX-License-Identifier: MIT
"""Report discovery and execution."""

from .fixtures import V2TestCase
from .harness import STORE


class ListReports(V2TestCase):
	"""Five fixture reports plus the two this app ships.

	v0.19.5 added `Sustainable CF Per Acre by Quarter` and v0.19.6 added
	`Sustainable CF Per Acre TTM Monthly` — both standard Script Reports in this
	app's own module, and the fixture seeds each from its shipped JSON the way a
	real `bench migrate` does. So every count here is two higher than it was and
	the app's own reports are legitimate members of the list, which is the point:
	`list_reports` is how somebody finds them.

	BOTH SHIP AND NEITHER REPLACES THE OTHER. The rolling one is the default view
	because four calendar quarters on a farm are not comparable with each other;
	the discrete one is kept because a lender's pack is laid out in their
	quarters and lining up with it is worth having.
	"""

	def test_lists_every_report_with_its_type(self):
		data = self.tool_data("list_reports")
		self.assertEqual(data["count"], 7)
		by_name = {row["name"]: row for row in data["reports"]}
		self.assertEqual(by_name["Cash Movement"]["report_type"], "Query Report")
		self.assertEqual(by_name["Open Purchase Orders"]["report_type"], "Report Builder")
		self.assertEqual(
			by_name["Sustainable CF Per Acre by Quarter"]["report_type"], "Script Report"
		)
		self.assertEqual(
			by_name["Sustainable CF Per Acre TTM Monthly"]["report_type"], "Script Report"
		)

	def test_counts_by_report_type(self):
		data = self.tool_data("list_reports")
		self.assertEqual(data["by_report_type"]["Query Report"], 2)
		self.assertEqual(data["by_report_type"]["Script Report"], 3)

	def test_filters_by_module(self):
		data = self.tool_data("list_reports", {"module": "Buying"})
		self.assertEqual([row["name"] for row in data["reports"]], ["Open Purchase Orders"])

	def test_filters_by_is_standard_in_words_or_boolean(self):
		words = self.tool_data("list_reports", {"is_standard": "Yes"})
		flag = self.tool_data("list_reports", {"is_standard": True})
		self.assertEqual(
			[row["name"] for row in words["reports"]],
			[
				"Accounts Receivable Summary",
				"Sustainable CF Per Acre TTM Monthly",
				"Sustainable CF Per Acre by Quarter",
			],
		)
		self.assertEqual(words["reports"], flag["reports"])

	def test_a_nonsense_is_standard_is_refused(self):
		message = self.tool_error("list_reports", {"is_standard": "maybe"})
		self.assertIn("must be Yes or No", message)

	def test_reports_the_ref_doctype_permissions_will_be_checked_against(self):
		data = self.tool_data("list_reports")
		by_name = {row["name"]: row for row in data["reports"]}
		self.assertEqual(by_name["Cash Movement"]["ref_doctype"], "GL Entry")
		self.assertIn("permission check", data["note"])


class RunQueryReport(V2TestCase):
	def test_runs_a_script_report_and_returns_rows(self):
		data = self.tool_data("run_report", {"name": "Accounts Receivable Summary"})
		self.assertEqual(data["row_count"], 2)
		self.assertEqual(data["executed_via"], "frappe.desk.query_report.run")
		self.assertEqual(data["row_format"], "objects")

	def test_passes_filters_through(self):
		data = self.tool_data(
			"run_report",
			{
				"name": "Accounts Receivable Summary",
				"filters": {"customer": "Southgate Markets"},
			},
		)
		self.assertEqual(data["row_count"], 1)
		self.assertEqual(data["rows"][0]["customer"], "Southgate Markets")
		self.assertEqual(data["filters_applied"], {"customer": "Southgate Markets"})

	def test_accepts_filters_as_a_json_string(self):
		"""A model will send a JSON string about as often as an object."""
		data = self.tool_data(
			"run_report",
			{
				"name": "Accounts Receivable Summary",
				"filters": '{"customer": "Southgate Markets"}',
			},
		)
		self.assertEqual(data["row_count"], 1)

	def test_a_non_object_filter_says_what_to_send(self):
		message = self.tool_error(
			"run_report", {"name": "Accounts Receivable Summary", "filters": "customer=x"}
		)
		self.assertIn("filters must be an object", message)

	def test_normalises_old_style_column_strings(self):
		"""Query Reports may hand back "Label:Fieldtype/Options:Width" — a model
		should not have to parse that."""
		data = self.tool_data("run_report", {"name": "Cash Movement"})
		columns = data["columns_normalised"]
		self.assertEqual(columns[0]["label"], "Date")
		self.assertEqual(columns[0]["fieldtype"], "Date")
		self.assertEqual(columns[0]["width"], 100)
		self.assertEqual(columns[1]["fieldtype"], "Currency")
		self.assertEqual(columns[1]["options"], "USD")

	def test_normalises_dict_columns_too(self):
		data = self.tool_data("run_report", {"name": "Accounts Receivable Summary"})
		first = data["columns_normalised"][0]
		self.assertEqual(first["fieldname"], "customer")
		self.assertEqual(first["options"], "Customer")

	def test_reports_array_rows_as_such(self):
		data = self.tool_data("run_report", {"name": "Cash Movement"})
		self.assertIn("arrays", data["row_format"])

	def test_carries_the_reports_own_message_through(self):
		data = self.tool_data("run_report", {"name": "Accounts Receivable Summary"})
		self.assertEqual(data["message"], "Aged as of report date")

	def test_truncates_and_says_so(self):
		data = self.tool_data("run_report", {"name": "Accounts Receivable Summary", "limit": 1})
		self.assertEqual(data["row_count"], 1)
		self.assertEqual(data["total_rows"], 2)
		self.assertTrue(data["truncated"])

	def test_a_disabled_report_is_refused(self):
		message = self.tool_error("run_report", {"name": "Retired Report"})
		self.assertIn("is disabled", message)

	def test_an_unknown_report_points_at_list_reports(self):
		message = self.tool_error("run_report", {"name": "Imaginary Report"})
		self.assertIn("list_reports", message)

	def test_an_unsupported_report_type_says_which_are_supported(self):
		message = self.tool_error("run_report", {"name": "Exotic Report"})
		self.assertIn("Custom Report", message)
		self.assertIn("Report Builder", message)

	def test_an_unknown_run_as_user_is_refused(self):
		message = self.tool_error("run_report", {"name": "Cash Movement", "user": "ghost@example.test"})
		self.assertIn("no User named", message)

	def test_a_permission_failure_names_the_doctype(self):
		"""Reports are the one read path where Frappe permissions apply, so the
		refusal has to explain itself in those terms."""
		STORE.denied_permissions.add(("GL Entry", "report"))
		message = self.tool_error("run_report", {"name": "Cash Movement"})
		self.assertIn("not permitted to run Report", message)
		self.assertIn("GL Entry", message)


class RunReportBuilder(V2TestCase):
	def test_materialises_a_saved_list_view(self):
		data = self.tool_data("run_report", {"name": "Open Purchase Orders"})
		self.assertEqual(data["executed_via"], "frappe.desk.reportview.get")
		self.assertEqual(data["report_type"], "Report Builder")

	def test_applies_the_saved_filters(self):
		"""The saved config filters to status=Draft, which is one order."""
		data = self.tool_data("run_report", {"name": "Open Purchase Orders"})
		self.assertEqual(data["row_count"], 1)
		self.assertEqual(data["rows"][0][0], "PUR-ORD-2026-00003")

	def test_uses_the_saved_columns_with_name_forced_in(self):
		data = self.tool_data("run_report", {"name": "Open Purchase Orders"})
		self.assertEqual(
			[column["fieldname"] for column in data["columns"]],
			["name", "supplier", "grand_total"],
		)

	def test_reports_the_saved_filters_back(self):
		data = self.tool_data("run_report", {"name": "Open Purchase Orders"})
		self.assertEqual(data["saved_filters"], [["Purchase Order", "status", "=", "Draft"]])

	def test_falls_back_to_get_list_when_reportview_is_unavailable(self):
		import sys

		reportview = sys.modules["frappe.desk.reportview"]
		saved = reportview.get

		def boom(*args, **kwargs):
			raise RuntimeError("reportview moved in this version")

		reportview.get = boom
		try:
			data = self.tool_data("run_report", {"name": "Open Purchase Orders"})
		finally:
			reportview.get = saved
		self.assertIn("frappe.get_list", data["executed_via"])
		self.assertEqual(data["row_count"], 1)
		self.assertEqual(data["rows"][0]["name"], "PUR-ORD-2026-00003")

	def test_a_permission_failure_is_not_swallowed_by_the_fallback(self):
		"""The fallback exists for a missing API, not for a refusal — falling
		back on PermissionError would route around the check."""
		STORE.denied_permissions.add(("Purchase Order", "read"))
		message = self.tool_error("run_report", {"name": "Open Purchase Orders"})
		self.assertIn("not permitted to run Report", message)

	def test_a_builder_report_on_a_missing_doctype_is_refused(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Purchase Order")
		message = self.tool_error("run_report", {"name": "Open Purchase Orders"})
		self.assertIn("not installed on this site", message)


class ReportsAreAudited(V2TestCase):
	def test_a_run_is_logged_with_the_report_name(self):
		self.tool_data("run_report", {"name": "Cash Movement"})
		row = self.assertAudited("run_report", status="Success")
		self.assertIn("Cash Movement", row["result_summary"])

	def test_a_refused_run_is_logged(self):
		self.tool_error("run_report", {"name": "Retired Report"})
		self.assertAudited("run_report", status="Error")
