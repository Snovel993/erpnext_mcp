# SPDX-License-Identifier: MIT
"""The five organisational masters, and the register `create_employee` refuses against.

THE GAP THESE CLOSE. `create_employee` has always checked `designation`,
`department`, `branch` and `employment_type` against this site's own records and
refused a value naming none — correctly, and with the site's own choices listed.
There was no tool anywhere that could ADD one, so a caller reading that refusal
had an answer it could not act on.

FIVE CLAIMS.

1. `CreatingThem` — all five insert, all five are idempotent by NAME rather than
   by docname, and the result reports the docname Frappe actually chose.

2. `TheDocnameIsNotTheName` — the one that matters and the one a double can
   hide. `Department` is named through a controller that appends the company
   abbreviation, so the string somebody typed is not the key. Both spellings
   resolve, on every tool, and the fixture serial-names Department precisely so
   this is a real test rather than a coincidence.

3. `Renaming` — `new_name` moves the docname AND repoints every Employee that
   named it, and is refused where the target already exists, because merging two
   registers is a decision about people rather than a spelling fix.

4. `WhatItRefusesToWrite` — Employee Grade's pay columns, by name, on both the
   create and the update. One value there reaches a whole band of people.

5. `ReportsTo` — the supervisor column two of this app's own surfaces already
   read and nothing could write, with the loop refusal that a self-referential
   Link needs and no other field here does.
"""

import frappe

from erpnext_mcp import roles

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import ROLES, STORE

ON = {
	f"allow_{name}": 1
	for name in (
		"create_designation",
		"list_designations",
		"update_designation",
		"create_department",
		"list_departments",
		"update_department",
		"create_branch",
		"list_branches",
		"update_branch",
		"create_employment_type",
		"list_employment_types",
		"update_employment_type",
		"create_employee_grade",
		"list_employee_grades",
		"update_employee_grade",
		"create_employee",
		"update_employee",
		"get_employee",
		"list_employees",
	)
}


class OrgTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		roles.install_roles()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	def strip_role(self):
		"""Take every HR role off the principal, leaving it able to read only."""
		ROLES["Administrator"] = ["Guest"]


class CreatingThem(OrgTestCase):
	def test_designation_inserts_and_is_offered_to_the_hire(self):
		data = self.tool_data("create_designation", {"designation_name": "Tractor Driver"})
		self.assertEqual(data["name"], "Tractor Driver")
		self.assertEqual(data["designation_name"], "Tractor Driver")
		self.assertEqual(data["active_employees"], 0)
		# The point of the whole module: the value `create_employee` refused a
		# minute ago is now one it accepts.
		hire = self.tool_data(
			"create_employee",
			{"employee_name": "Ana Ramos", "company": MAIN, "designation": "Tractor Driver"},
		)
		self.assertEqual(frappe.db.get_value("Employee", hire["employee"], "designation"), "Tractor Driver")

	def test_branch_and_employment_type_and_grade_all_insert(self):
		self.assertEqual(self.tool_data("create_branch", {"branch": "Mill Creek"})["name"], "Mill Creek")
		self.assertEqual(
			self.tool_data("create_employment_type", {"employment_type_name": "H-2A"})["name"], "H-2A"
		)
		# Prompt-named: the docname is EXACTLY what was passed, with no column
		# behind it. A serial name here would mean the tool never took the prompt
		# path it takes on a bench.
		self.assertEqual(
			self.tool_data("create_employee_grade", {"employee_grade_name": "Lead"})["name"], "Lead"
		)

	def test_a_second_create_with_the_same_name_is_refused(self):
		self.tool_data("create_designation", {"designation_name": "Checker"})
		error = self.tool_error("create_designation", {"designation_name": "Checker"})
		self.assertIn("already a Designation called 'Checker'", error)
		self.assertIn("Nothing was created", error)
		self.assertEqual(len([row for row in STORE.rows("Designation") if row["name"] == "Checker"]), 1)

	def test_creating_needs_an_hr_role(self):
		self.strip_role()
		error = self.tool_error("create_designation", {"designation_name": "Checker"})
		self.assertIn("may not change the personnel register", error)
		self.assertFalse(frappe.db.exists("Designation", "Checker"))

	def test_a_department_is_scoped_to_a_company(self):
		data = self.tool_data("create_department", {"department_name": "Harvest", "company": MAIN})
		self.assertEqual(data["company"], MAIN)
		# The same name at another entity is an ordinary shape, not a duplicate.
		other = self.tool_data("create_department", {"department_name": "Harvest", "company": OTHER})
		self.assertNotEqual(other["name"], data["name"])
		error = self.tool_error("create_department", {"department_name": "Harvest", "company": MAIN})
		self.assertIn("already a Department called 'Harvest'", error)


class TheDocnameIsNotTheName(OrgTestCase):
	"""The claim a double can hide. See the module docstring."""

	def test_a_department_resolves_by_either_spelling(self):
		created = self.tool_data("create_department", {"department_name": "Harvest", "company": MAIN})
		docname = created["name"]
		# The fixture serial-names Department for the same reason Frappe's own
		# controller renames it: the docname is not the string somebody typed.
		self.assertNotEqual(docname, "Harvest")
		self.assertEqual(created["department_name"], "Harvest")

		by_docname = self.tool_data("update_department", {"department": docname, "is_group": True})
		self.assertEqual(by_docname["name"], docname)
		by_typed_name = self.tool_data("update_department", {"department": "Harvest", "is_group": False})
		self.assertEqual(by_typed_name["name"], docname)

	def test_two_departments_with_one_name_are_reported_rather_than_guessed(self):
		self.tool_data("create_department", {"department_name": "Harvest", "company": MAIN})
		self.tool_data("create_department", {"department_name": "Harvest", "company": OTHER})
		error = self.tool_error("update_department", {"department": "Harvest", "is_group": True})
		self.assertIn("2 Department records are called 'Harvest'", error)
		self.assertIn("Nothing was changed", error)

	def test_a_name_that_matches_nothing_says_what_was_searched(self):
		error = self.tool_error("update_branch", {"branch": "Nowhere", "new_name": "Somewhere"})
		self.assertIn("no Branch matching 'Nowhere'", error)
		self.assertIn("list_branches", error)


class ListingThem(OrgTestCase):
	def test_the_headcount_is_what_makes_the_read_worth_anything(self):
		self.tool_data("create_designation", {"designation_name": "Tractor Driver"})
		self.tool_data(
			"create_employee",
			{"employee_name": "Ana Ramos", "company": MAIN, "designation": "Tractor Driver"},
		)
		data = self.tool_data("list_designations")
		rows = {row["name"]: row for row in data["designations"]}
		self.assertEqual(rows["Tractor Driver"]["active_employees"], 1)
		# Seeded by the fixture and held by nobody — the row that is safe to retire.
		self.assertIn("Bookkeeper", data["unused"])
		self.assertNotIn("Tractor Driver", data["unused"])

	def test_in_use_only_drops_the_rows_nobody_holds(self):
		self.tool_data("create_designation", {"designation_name": "Tractor Driver"})
		data = self.tool_data("list_designations", {"in_use_only": True})
		self.assertNotIn("Tractor Driver", [row["name"] for row in data["designations"]])
		self.assertTrue(all(row["active_employees"] for row in data["designations"]))

	def test_departments_can_be_listed_for_one_company(self):
		self.tool_data("create_department", {"department_name": "Harvest", "company": OTHER})
		data = self.tool_data("list_departments", {"company": MAIN})
		self.assertEqual(data["company"], MAIN)
		self.assertNotIn("Harvest", [row["department_name"] for row in data["departments"]])

	def test_reading_needs_no_hr_role(self):
		"""A hiring form has to offer the list it is about to refuse against."""
		self.strip_role()
		self.assertTrue(self.tool_data("list_designations")["count"])


class Renaming(OrgTestCase):
	def test_renaming_carries_the_people_already_on_it(self):
		self.tool_data("create_branch", {"branch": "Mill Creak"})
		hire = self.tool_data(
			"create_employee", {"employee_name": "Ana Ramos", "company": MAIN, "branch": "Mill Creak"}
		)
		data = self.tool_data("update_branch", {"branch": "Mill Creak", "new_name": "Mill Creek"})

		self.assertEqual(data["name"], "Mill Creek")
		self.assertEqual(data["renamed_from"], "Mill Creak")
		self.assertFalse(frappe.db.exists("Branch", "Mill Creak"))
		# THE POINT. Nobody edited the Employee, and the Employee moved.
		self.assertEqual(frappe.db.get_value("Employee", hire["employee"], "branch"), "Mill Creek")

	def test_renaming_moves_the_name_column_too(self):
		"""A `field:`-named doctype whose column disagreed with its key is half a rename."""
		self.tool_data("create_designation", {"designation_name": "Trakter Driver"})
		self.tool_data("update_designation", {"designation": "Trakter Driver", "new_name": "Tractor Driver"})
		self.assertEqual(
			frappe.db.get_value("Designation", "Tractor Driver", "designation_name"), "Tractor Driver"
		)

	def test_renaming_onto_an_existing_name_is_refused(self):
		self.tool_data("create_branch", {"branch": "Mill Creek"})
		self.tool_data("create_branch", {"branch": "Home Ranch"})
		error = self.tool_error("update_branch", {"branch": "Home Ranch", "new_name": "Mill Creek"})
		self.assertIn("would MERGE", error)
		self.assertTrue(frappe.db.exists("Branch", "Home Ranch"))
		self.assertTrue(frappe.db.exists("Branch", "Mill Creek"))

	def test_an_update_with_nothing_to_change_says_so(self):
		self.tool_data("create_branch", {"branch": "Mill Creek"})
		error = self.tool_error("update_branch", {"branch": "Mill Creek"})
		self.assertIn("nothing to change", error)
		self.assertIn("new_name", error)

	def test_a_designation_description_is_writable_and_reported(self):
		self.tool_data("create_designation", {"designation_name": "Checker", "description": "Grades fruit"})
		data = self.tool_data("update_designation", {"designation": "Checker", "description": "Grades bins"})
		self.assertEqual(
			data["changed"], [{"field": "description", "from": "Grades fruit", "to": "Grades bins"}]
		)
		# A value already equal to what was asked for is not reported as a write.
		again = self.tool_data("update_designation", {"designation": "Checker", "description": "Grades bins"})
		self.assertEqual(again["changed"], [])
		self.assertEqual(again["unchanged"], ["description"])


class WhatItRefusesToWrite(OrgTestCase):
	def test_a_grades_pay_columns_are_refused_by_name_on_create(self):
		error = self.tool_error(
			"create_employee_grade", {"employee_grade_name": "Lead", "default_base_pay": 25}
		)
		# The schema is closed, so the dispatcher refuses it before the handler
		# does — either refusal is a refusal, and nothing was written.
		self.assertFalse(frappe.db.exists("Employee Grade", "Lead"))
		self.assertTrue(error)

	def test_a_grades_pay_columns_are_refused_by_name_on_update(self):
		self.tool_data("create_employee_grade", {"employee_grade_name": "Lead"})
		error = self.tool_error(
			"update_employee_grade", {"employee_grade": "Lead", "default_salary_structure": "SS-01"}
		)
		self.assertTrue(error)
		self.assertIsNone(frappe.db.get_value("Employee Grade", "Lead", "default_salary_structure"))

	def test_the_handler_refuses_a_pay_column_even_off_the_schema(self):
		"""The dispatcher's schema is one door. This is the other one.

		`additionalProperties: false` is advertised and is not enforced by
		anything this app controls, so the refusal has to exist in the handler
		too or it is a promise made only to well-behaved callers.
		"""
		from erpnext_mcp.errors import ToolError
		from erpnext_mcp.tools import org

		with self.assertRaises(ToolError) as caught:
			org.create_employee_grade({"employee_grade_name": "Lead", "default_base_pay": 25})
		self.assertIn("entire BAND of people is paid", str(caught.exception))


class ReportsTo(OrgTestCase):
	def supervisor(self, name="Ada Orchard"):
		return frappe.db.get_value("Employee", {"employee_name": name}, "name")

	def test_a_hire_can_name_their_supervisor(self):
		boss = self.supervisor()
		hire = self.tool_data(
			"create_employee", {"employee_name": "Ana Ramos", "company": MAIN, "reports_to": boss}
		)
		self.assertEqual(frappe.db.get_value("Employee", hire["employee"], "reports_to"), boss)

	def test_update_writes_it_and_resolves_the_supervisor_four_ways(self):
		hire = self.tool_data("create_employee", {"employee_name": "Ana Ramos", "company": MAIN})
		# By employee_name rather than by docname: a badge scan gives a name.
		data = self.tool_data("update_employee", {"name": hire["employee"], "reports_to": "Ada Orchard"})
		self.assertEqual(
			[entry["field"] for entry in data["changed"]],
			["reports_to"],
		)
		self.assertEqual(frappe.db.get_value("Employee", hire["employee"], "reports_to"), self.supervisor())

	def test_reporting_to_yourself_is_refused(self):
		hire = self.tool_data("create_employee", {"employee_name": "Ana Ramos", "company": MAIN})
		error = self.tool_error("update_employee", {"name": hire["employee"], "reports_to": hire["employee"]})
		self.assertIn("cannot report to themselves", error)
		self.assertFalse(frappe.db.get_value("Employee", hire["employee"], "reports_to"))

	def test_a_loop_further_up_the_chain_is_refused_with_the_path(self):
		one = self.tool_data("create_employee", {"employee_name": "Ana Ramos", "company": MAIN})["employee"]
		two = self.tool_data("create_employee", {"employee_name": "Beto Cruz", "company": MAIN})["employee"]
		three = self.tool_data("create_employee", {"employee_name": "Cleo Diaz", "company": MAIN})["employee"]
		self.tool_data("update_employee", {"name": two, "reports_to": one})
		self.tool_data("update_employee", {"name": three, "reports_to": two})
		# one → three → two → one would close the loop.
		error = self.tool_error("update_employee", {"name": one, "reports_to": three})
		self.assertIn("would close a loop", error)
		self.assertIn(one, error)
		self.assertFalse(frappe.db.get_value("Employee", one, "reports_to"))

	def test_an_unknown_supervisor_is_refused_by_name(self):
		hire = self.tool_data("create_employee", {"employee_name": "Ana Ramos", "company": MAIN})
		error = self.tool_error("update_employee", {"name": hire["employee"], "reports_to": "Nobody At All"})
		self.assertIn("no Employee matching 'Nobody At All'", error)
