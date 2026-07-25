# SPDX-License-Identifier: MIT
"""HR tools — and, first, that they are absent without the hrms app."""

from erpnext_mcp import registry

from .fixtures import LEAVE_BALANCES, HRTestCase, V2TestCase
from .harness import STORE

HR_TOOL_NAMES = ("list_employees", "get_attendance_summary", "get_leave_balance")


class WithoutHRMS(V2TestCase):
	def test_the_tools_are_not_advertised(self):
		self.configure(**{f"allow_{name}": 1 for name in registry.TOOLS}, enabled=1)
		body, _ = self.call("tools/list")
		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertFalse(names & set(HR_TOOL_NAMES))

	def test_calling_one_explains_what_is_missing(self):
		for name in HR_TOOL_NAMES:
			with self.subTest(tool=name):
				message = self.tool_error(name, {})
				self.assertIn("hrms", message)

	def test_the_refusal_is_audited_as_blocked(self):
		self.tool_error("list_employees")
		row = self.assertAudited("list_employees", status="Blocked")
		self.assertIn("unavailable", row["result_summary"])


class ListEmployees(HRTestCase):
	def test_active_employees_by_default(self):
		data = self.tool_data("list_employees")
		self.assertEqual(data["count"], 2)
		self.assertNotIn("HR-EMP-00003", [row["name"] for row in data["employees"]])

	def test_status_can_be_widened(self):
		data = self.tool_data("list_employees", {"status": ""})
		self.assertEqual(data["count"], 3)

	def test_filters_by_department_and_designation(self):
		data = self.tool_data("list_employees", {"department": "Operations"})
		self.assertEqual(data["count"], 2)
		data = self.tool_data("list_employees", {"designation": "Supervisor"})
		self.assertEqual([row["name"] for row in data["employees"]], ["HR-EMP-00001"])

	def test_counts_by_department(self):
		data = self.tool_data("list_employees", {"status": ""})
		self.assertEqual(data["by_department"], {"Operations": 2, "Administration": 1})

	def test_explains_which_identifier_the_other_tools_want(self):
		data = self.tool_data("list_employees")
		self.assertIn("employee_number", data["note"])
		self.assertIn("HR-EMP", data["note"])


class AttendanceSummary(HRTestCase):
	def test_aggregates_per_employee(self):
		data = self.tool_data("get_attendance_summary", {"from_date": "2026-06-01", "to_date": "2026-06-30"})
		by_employee = {row["employee"]: row for row in data["employees"]}
		self.assertEqual(by_employee["HR-EMP-00001"]["counts"]["Present"], 3)
		self.assertEqual(by_employee["HR-EMP-00001"]["counts"]["On Leave"], 1)
		self.assertEqual(by_employee["HR-EMP-00002"]["counts"]["Absent"], 1)
		self.assertEqual(by_employee["HR-EMP-00002"]["counts"]["Half Day"], 1)

	def test_every_employee_gets_every_status_key(self):
		"""A missing key is ambiguous between zero and untracked."""
		data = self.tool_data("get_attendance_summary", {"from_date": "2026-06-01", "to_date": "2026-06-30"})
		for row in data["employees"]:
			with self.subTest(employee=row["employee"]):
				self.assertEqual(sorted(row["counts"]), data["statuses"])

	def test_drafts_are_not_counted(self):
		"""A draft Attendance row is not evidence anybody turned up."""
		data = self.tool_data("get_attendance_summary", {"from_date": "2026-06-01", "to_date": "2026-06-30"})
		by_employee = {row["employee"]: row for row in data["employees"]}
		self.assertEqual(by_employee["HR-EMP-00002"]["total_marked"], 4)
		self.assertEqual(data["records_counted"], 8)

	def test_totals_across_the_site(self):
		data = self.tool_data("get_attendance_summary", {"from_date": "2026-06-01", "to_date": "2026-06-30"})
		self.assertEqual(data["totals"]["Present"], 5)
		self.assertEqual(data["totals"]["On Leave"], 1)

	def test_scopes_to_one_employee_by_number(self):
		data = self.tool_data(
			"get_attendance_summary",
			{"from_date": "2026-06-01", "to_date": "2026-06-30", "employee": "E-101"},
		)
		self.assertEqual([row["employee"] for row in data["employees"]], ["HR-EMP-00002"])

	def test_scopes_to_a_department(self):
		data = self.tool_data(
			"get_attendance_summary",
			{
				"from_date": "2026-06-01",
				"to_date": "2026-06-30",
				"department": "Administration",
			},
		)
		self.assertEqual(data["employee_count"], 0)

	def test_a_narrow_range_excludes_records(self):
		data = self.tool_data("get_attendance_summary", {"from_date": "2026-06-01", "to_date": "2026-06-03"})
		self.assertEqual(data["records_counted"], 3)

	def test_an_inverted_range_is_refused(self):
		message = self.tool_error(
			"get_attendance_summary", {"from_date": "2026-06-30", "to_date": "2026-06-01"}
		)
		self.assertIn("is after", message)

	def test_an_unknown_employee_points_at_list_employees(self):
		message = self.tool_error(
			"get_attendance_summary",
			{"from_date": "2026-06-01", "to_date": "2026-06-30", "employee": "E-999"},
		)
		self.assertIn("list_employees", message)


class LeaveBalance(HRTestCase):
	def test_returns_a_balance_per_allocated_type(self):
		data = self.tool_data("get_leave_balance", {"employee": "HR-EMP-00001"})
		self.assertEqual(
			sorted(row["leave_type"] for row in data["balances"]),
			["Annual Leave", "Sick Leave"],
		)
		self.assertEqual(data["total_balance"], 20.5)

	def test_it_delegates_rather_than_subtracting_itself(self):
		"""Carry-forward and expiry live in HR's function; a subtraction here
		would be confidently wrong on any site with a policy."""
		data = self.tool_data("get_leave_balance", {"employee": "HR-EMP-00001"})
		self.assertEqual(data["computed_via"], "HR get_leave_balance_on")

	def test_only_types_with_a_current_allocation_are_included(self):
		"""Unpaid Leave was allocated for 2025 only — a zero row for it is noise."""
		data = self.tool_data("get_leave_balance", {"employee": "HR-EMP-00001"})
		self.assertNotIn("Unpaid Leave", [row["leave_type"] for row in data["balances"]])

	def test_a_specific_leave_type_can_be_asked_for(self):
		data = self.tool_data("get_leave_balance", {"employee": "HR-EMP-00001", "leave_type": "Sick Leave"})
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["balances"][0]["balance"], 8.0)

	def test_an_unknown_leave_type_lists_the_allocated_ones(self):
		message = self.tool_error(
			"get_leave_balance", {"employee": "HR-EMP-00001", "leave_type": "Sabbatical"}
		)
		self.assertIn("Annual Leave", message)

	def test_defaults_as_of_to_today(self):
		data = self.tool_data("get_leave_balance", {"employee": "HR-EMP-00001"})
		self.assertEqual(data["as_of"], "2026-07-24")

	def test_resolves_an_employee_by_user_id(self):
		data = self.tool_data("get_leave_balance", {"employee": "approver@example.test"})
		self.assertEqual(data["employee"], "HR-EMP-00001")

	def test_one_broken_leave_type_does_not_lose_the_others(self):
		LEAVE_BALANCES["Sick Leave"] = RuntimeError("misconfigured leave type")
		try:
			data = self.tool_data("get_leave_balance", {"employee": "HR-EMP-00001"})
		finally:
			LEAVE_BALANCES["Sick Leave"] = 8.0
		self.assertEqual([row["leave_type"] for row in data["balances"]], ["Annual Leave"])
		self.assertEqual(data["failed"][0]["leave_type"], "Sick Leave")

	def test_an_employee_with_no_allocations_returns_an_empty_list(self):
		data = self.tool_data("get_leave_balance", {"employee": "HR-EMP-00002"})
		self.assertEqual(data["count"], 0)
		self.assertEqual(data["total_balance"], 0)

	def test_it_refuses_rather_than_guessing_without_the_hr_api(self):
		import sys

		module_path = "hrms.hr.doctype.leave_application.leave_application"
		saved = sys.modules.pop(module_path)
		try:
			message = self.tool_error("get_leave_balance", {"employee": "HR-EMP-00001"})
		finally:
			sys.modules[module_path] = saved
		self.assertIn("does not export get_leave_balance_on", message)
		self.assertIn("run_report", message)


class HRIsAudited(HRTestCase):
	def test_reading_personal_data_leaves_a_row(self):
		self.tool_data("list_employees")
		self.assertAudited("list_employees", status="Success")

	def test_the_ambient_hrms_check_does_not_change_the_other_tools(self):
		"""Installing hrms must not disturb the accounting catalogue."""
		data = self.tool_data("get_company_topology")
		self.assertEqual(data["count"], 2)
		self.assertIn("hrms", STORE.installed_apps)
