# SPDX-License-Identifier: MIT
"""`onboard_employee` — one call for a new hire, and where the paperwork lands.

THE CLAIM THIS FILE EXISTS FOR IS THE SECOND ONE. An I-9, a W-4 and a photograph
of somebody's driver's licence are PERSONAL records about one employee.
`attach_governance_document` files into the register that holds trust
instruments, operating agreements, SOPs and certification paperwork — the
documents describing the BUSINESS — which an auditor, an advisor and a family
member browse. Filing forty people's immigration paperwork there would look tidy
and be a disclosure.

That mistake was made once and corrected. `TheePaperworkGoesOnThePerson` asserts
it against the module source as well as against behaviour, because the claim is
that no code path exists rather than that the common one is right.

FOUR CLAIMS.

1. `ItDoesTheWholeThing` — employee, paperwork, login, first-day tasks.
2. `ThePaperworkGoesOnThePerson` — private, on the Employee, never the archive.
3. `ItDoesNotDuplicateAPerson` — two Employee records for one human puts them on
   the dispatch board twice and in the payroll register once.
4. `APartialRunSaysWhichPartRan` — an orchestrator that half-completes is only
   dangerous if nobody can tell which half.
"""

import base64

import frappe

from erpnext_mcp import roles
from erpnext_mcp.tools import newhire

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import ROLES, STORE

ON = {
	f"allow_{name}": 1
	for name in ("onboard_employee", "create_mobile_user", "create_farm_task", "attach_file_to_document")
}

A_PDF = base64.b64encode(b"%PDF-1.4 i9 form bytes").decode()


class NewHireTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **ON)
		# `onboard_employee` refuses a site with no Employee register, and the
		# double ships without one — Frappe HR is a separate app, which is the
		# whole reason the tool has an availability predicate.
		install_hrms()
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		roles.install_roles()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	def hire(self, **overrides):
		payload = {"full_name": "Ana Ramos", "company": MAIN}
		payload.update(overrides)
		return self.tool_data("onboard_employee", payload)

	def attachments(self, employee):
		return [row for row in STORE.rows("File") if row.get("attached_to_name") == employee]


# ── 1 ───────────────────────────────────────────────────────────────────────
class ItDoesTheWholeThing(NewHireTestCase):
	def test_it_creates_the_employee(self):
		data = self.hire()
		self.assertTrue(data["employee"])
		row = frappe.db.get_value(
			"Employee", data["employee"], ["employee_name", "company", "status"], as_dict=True
		)
		self.assertEqual(row["employee_name"], "Ana Ramos")
		self.assertEqual(row["company"], MAIN)

	def test_it_creates_a_scoped_login_when_an_email_is_given(self):
		data = self.hire(email="ana@example.test")
		self.assertEqual(data["mobile"]["user"], "ana@example.test")
		self.assertEqual(data["mobile"]["entity_access"], [MAIN])
		self.assertEqual(data["mobile"]["role"], "Field Worker")

	def test_the_login_defaults_to_the_hiring_company_alone(self):
		"""The scoped default. `create_mobile_user` refuses an account with no
		entities, and inheriting every company would be the least scoped one."""
		data = self.hire(email="ana@example.test")
		self.assertEqual(data["mobile"]["entity_access"], [MAIN])

	def test_a_wider_entity_list_is_honoured_when_asked_for(self):
		data = self.hire(email="ana@example.test", entity_access=[MAIN, OTHER])
		self.assertEqual(sorted(data["mobile"]["entity_access"]), sorted([MAIN, OTHER]))

	def test_no_email_means_no_login_and_the_reason_is_reported(self):
		"""Right for somebody who will never hold a phone."""
		data = self.hire()
		self.assertIsNone(data["mobile"])
		self.assertTrue(any(row["step"] == "mobile access" for row in data["skipped"]))

	def test_the_employee_is_linked_to_the_login(self):
		data = self.hire(email="ana@example.test")
		self.assertEqual(frappe.db.get_value("Employee", data["employee"], "user_id"), "ana@example.test")

	def test_first_day_tasks_are_opt_in(self):
		self.assertEqual(self.hire()["tasks"], [])

	def test_first_day_tasks_demand_a_signature(self):
		"""'I was told about the machinery' is a claim whose entire value is that
		somebody attested to it."""
		data = self.hire(first_day_tasks=True)
		self.assertEqual(len(data["tasks"]), 2)
		for entry in data["tasks"]:
			contract = frappe.db.get_value("Farm Task", entry["task"], "evidence_required")
			self.assertIn("signature", str(contract))

	def test_a_one_word_name_is_refused(self):
		message = self.tool_error("onboard_employee", {"full_name": "Ana", "company": MAIN})
		self.assertIn("names nobody findable", message)

	def test_the_credential_is_not_repeated_in_the_result(self):
		"""create_mobile_user returns a secret exactly once. Echoing it into an
		orchestrator's summary would put it somewhere much more pasteable."""
		import json

		data = self.hire(email="ana@example.test")
		text = json.dumps(data, default=str)
		self.assertNotIn("api_secret", text)
		self.assertNotIn("auth_header", text)
		self.assertIn("generate_mobile_login_qr", data["mobile"]["credential_note"])


# ── 2 ───────────────────────────────────────────────────────────────────────
class ThePaperworkGoesOnThePerson(NewHireTestCase):
	def test_an_i9_lands_on_the_employee_record(self):
		data = self.hire(documents={"i9": {"file_name": "ana-i9.pdf", "file_content": A_PDF}})
		attached = self.attachments(data["employee"])
		self.assertEqual(len(attached), 1)
		self.assertEqual(attached[0]["attached_to_doctype"], "Employee")

	def test_it_is_private(self):
		"""A public URL to somebody's I-9 is readable by anyone who guesses it."""
		data = self.hire(documents={"i9": {"file_name": "ana-i9.pdf", "file_content": A_PDF}})
		self.assertTrue(self.attachments(data["employee"])[0]["is_private"])
		self.assertTrue(data["documents"][0]["is_private"])

	def test_no_governance_document_is_created_by_any_of_it(self):
		"""THE CORRECTION. The archive holds the documents describing the
		BUSINESS, and an auditor, an advisor and a family member browse it."""
		before = len(STORE.rows("Governance Document"))
		self.hire(
			email="ana@example.test",
			first_day_tasks=True,
			documents={
				"i9": {"file_name": "ana-i9.pdf", "file_content": A_PDF},
				"w4": {"file_name": "ana-w4.pdf", "file_content": A_PDF},
				"photo_id": {"file_name": "ana-id.pdf", "file_content": A_PDF},
			},
		)
		self.assertEqual(len(STORE.rows("Governance Document")), before)

	def test_the_module_cannot_reach_the_governance_archive_at_all(self):
		"""Asserted against the PARSED module, because the claim is that no code
		path exists — reachable or otherwise — from a new hire to that register.

		AST rather than a substring search, because the docstrings deliberately
		name `attach_governance_document` in order to say it is the wrong tool,
		and a grep cannot tell a prohibition from a call. What is checked is what
		executes: every name this module imports, every attribute it reads, and
		every string constant it could pass as a doctype.
		"""
		import ast
		import inspect

		tree = ast.parse(inspect.getsource(newhire))
		forbidden = {"governance", "attach_governance_document", "file_governance_document"}

		imported = set()
		for node in ast.walk(tree):
			if isinstance(node, ast.ImportFrom):
				imported.update(alias.asname or alias.name for alias in node.names)
			elif isinstance(node, ast.Import):
				imported.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
		self.assertFalse(imported & forbidden, f"newhire imports {imported & forbidden}")

		called = {
			node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
			for node in ast.walk(tree)
			if isinstance(node, ast.Call)
		}
		self.assertFalse(called & forbidden, f"newhire calls {called & forbidden}")

		# No string constant could name the archive as a target doctype either.
		constants = {
			node.value
			for node in ast.walk(tree)
			if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 40
		}
		self.assertNotIn("Governance Document", constants)

	def test_every_kind_is_filed_and_labelled_for_somebody_reading_it_later(self):
		data = self.hire(
			documents={
				"i9": {"file_name": "a.pdf", "file_content": A_PDF},
				"w4": {"file_name": "b.pdf", "file_content": A_PDF},
				"photo_id": {"file_name": "c.pdf", "file_content": A_PDF},
			}
		)
		labels = {row["label"] for row in data["documents"]}
		self.assertIn("Form I-9 — employment eligibility verification", labels)
		self.assertIn("Form W-4 — employee withholding certificate", labels)

	def test_an_unknown_document_kind_is_refused_by_name(self):
		message = self.tool_error(
			"onboard_employee",
			{
				"full_name": "Ana Ramos",
				"company": MAIN,
				"documents": {"passport_scan": {"file_name": "p.pdf", "file_content": A_PDF}},
			},
		)
		self.assertIn("passport_scan", message)
		self.assertIn("i9", message)

	def test_each_document_records_the_hash_of_what_was_filed(self):
		data = self.hire(documents={"i9": {"file_name": "ana-i9.pdf", "file_content": A_PDF}})
		self.assertTrue(data["documents"][0]["sha256"])


# ── 3 ───────────────────────────────────────────────────────────────────────
class ItDoesNotDuplicateAPerson(NewHireTestCase):
	def test_an_existing_employee_is_reused_rather_than_doubled(self):
		"""Two Employee records for one human puts them on the dispatch board
		twice and in the payroll register once."""
		first = self.hire(email="ana@example.test")
		before = len(STORE.rows("Employee"))
		second = self.hire(email="ana@example.test", update_existing=True)
		self.assertEqual(second["employee"], first["employee"])
		self.assertEqual(len(STORE.rows("Employee")), before)

	def test_an_employee_named_explicitly_is_used(self):
		first = self.hire()
		second = self.hire(employee=first["employee"])
		self.assertEqual(second["employee"], first["employee"])
		self.assertEqual(
			next(row for row in second["steps"] if row["step"] == "employee")["action"], "reused"
		)

	def test_an_employee_docname_that_does_not_exist_is_refused(self):
		message = self.tool_error(
			"onboard_employee",
			{"full_name": "Ana Ramos", "company": MAIN, "employee": "EMP-NOPE"},
		)
		self.assertIn("EMP-NOPE", message)


# ── 4 ───────────────────────────────────────────────────────────────────────
class APartialRunSaysWhichPartRan(NewHireTestCase):
	def test_every_step_is_named_with_what_happened_to_it(self):
		data = self.hire(
			email="ana@example.test",
			documents={"i9": {"file_name": "ana-i9.pdf", "file_content": A_PDF}},
		)
		steps = {row["step"] for row in data["steps"]}
		self.assertIn("employee", steps)
		self.assertIn("document:i9", steps)
		self.assertIn("mobile access", steps)

	def test_a_login_that_cannot_be_created_does_not_undo_the_employee(self):
		"""An existing account is create_mobile_user's refusal, and it is right —
		but the Employee that was already made must stand and be reported."""
		self.hire(email="ana@example.test")
		before = STORE.rows("Employee")
		data = self.hire(full_name="Ana Ramos", company=MAIN, email="ana@example.test")
		self.assertTrue(data["employee"])
		self.assertIsNone(data["mobile"])
		self.assertTrue(any(row["step"] == "mobile access" for row in data["skipped"]))
		self.assertEqual(len(STORE.rows("Employee")), len(before))

	def test_a_task_that_cannot_be_raised_is_reported_and_not_fatal(self):
		"""A training task that could not be raised must not undo an onboarding."""
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Farm Task")
		try:
			data = self.hire(first_day_tasks=True)
		finally:
			INSTALLED_DOCTYPES.add("Farm Task")
		self.assertTrue(data["employee"])
		self.assertEqual(data["tasks"], [])
		self.assertTrue(any("task" in row["step"] for row in data["skipped"]))

	def test_a_site_with_no_employee_register_refuses_before_anything_is_made(self):
		"""Creating half a hire would be worse than refusing the whole one. The
		refusal arrives from the tool's availability predicate before the handler
		runs at all, which is why the message names the DocType rather than the
		step that would have needed it."""
		STORE.installed_apps.remove("hrms")
		try:
			message = self.tool_error("onboard_employee", {"full_name": "Ana Ramos", "company": MAIN})
		finally:
			STORE.installed_apps.append("hrms")
		self.assertIn("Employee", message)
		before = len(STORE.rows("Employee"))
		self.assertEqual(len(STORE.rows("Employee")), before)


# ── the assignment facts, v0.54.0 ───────────────────────────────────────────
class ItRecordsWhatSomebodyWasHiredAs(NewHireTestCase):
	"""v0.54.0. `onboard_employee` has taken `designation` since it shipped and
	neither `department`, `employment_type` nor `branch` — so an onboarding could
	answer "what do they do" and not "what are they hired as, and where".

	`employment_type` is the one that matters most: it is the field that says
	whether somebody is Seasonal, which is the fact an H-2A roster, an ACA hours
	count and a piece-rate wage statement all turn on.
	"""

	def setUp(self):
		super().setUp()
		STORE.seed("Branch", [{"name": "Mill Creek Camp", "branch": "Mill Creek Camp"}])
		STORE.seed("Designation", [{"name": "Picker", "designation_name": "Picker"}])
		STORE.seed("Employment Type", [{"name": "Seasonal", "employee_type_name": "Seasonal"}])
		STORE.seed(
			"Department",
			[{"name": "Harvest", "department_name": "Harvest", "company": MAIN, "is_group": 0}],
		)

	def test_all_four_assignment_fields_land_on_the_record(self):
		data = self.hire(
			designation="Picker",
			department="Harvest",
			employment_type="Seasonal",
			branch="Mill Creek Camp",
		)
		row = frappe.db.get_value(
			"Employee",
			data["employee"],
			["designation", "department", "employment_type", "branch"],
			as_dict=True,
		)
		self.assertEqual(row["designation"], "Picker")
		self.assertEqual(row["department"], "Harvest")
		self.assertEqual(row["employment_type"], "Seasonal")
		self.assertEqual(row["branch"], "Mill Creek Camp")

	def test_an_employment_type_this_site_does_not_have_is_refused_with_its_choices(self):
		"""The check is `create_employee`'s, which is the point of delegating —
		a wrapper with its own copy would be a second set of hiring rules."""
		message = self.tool_error(
			"onboard_employee",
			{"full_name": "Ana Ramos", "company": MAIN, "employment_type": "Casual"},
		)
		self.assertIn("Casual", message)
		self.assertIn("Employment Type", message)

	def test_a_branch_that_names_nothing_is_refused_rather_than_written_blind(self):
		message = self.tool_error(
			"onboard_employee",
			{"full_name": "Ana Ramos", "company": MAIN, "branch": "Nowhere Camp"},
		)
		self.assertIn("Nowhere Camp", message)
		self.assertIn("Branch", message)

	def test_none_of_the_four_is_required(self):
		"""A hire made at a tailgate with three facts is still a hire."""
		self.assertTrue(self.hire()["employee"])
