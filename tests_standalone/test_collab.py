# SPDX-License-Identifier: MIT
"""Comments and ToDos."""

from erpnext_mcp import registry

from .fixtures import APPROVER, ATTACHED_JE, BUYER, V2TestCase
from .harness import STORE


class ListComments(V2TestCase):
	def test_returns_the_thread_oldest_first(self):
		data = self.tool_data("list_comments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertEqual([row["name"] for row in data["comments"]], ["comment-1", "comment-2"])

	def test_does_not_leak_another_documents_thread(self):
		data = self.tool_data("list_comments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertNotIn("comment-3", [row["name"] for row in data["comments"]])

	def test_normalises_the_author(self):
		data = self.tool_data("list_comments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertEqual(data["comments"][0]["author"], "Avi Approver")
		self.assertEqual(data["comments"][1]["author"], "Administrator")

	def test_counts_by_comment_type(self):
		"""Framework chatter and human remarks share a table; the split is the
		first thing a reader needs."""
		data = self.tool_data("list_comments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertEqual(data["by_comment_type"], {"Comment": 1, "Info": 1})

	def test_filters_to_human_remarks(self):
		data = self.tool_data(
			"list_comments",
			{"doctype": "Journal Entry", "name": ATTACHED_JE, "comment_type": "Comment"},
		)
		self.assertEqual(data["count"], 1)
		self.assertEqual(data["comments"][0]["comment_type"], "Comment")

	def test_no_permission_on_the_document_means_no_thread(self):
		STORE.denied_permissions.add(("Journal Entry", ATTACHED_JE))
		message = self.tool_error("list_comments", {"doctype": "Journal Entry", "name": ATTACHED_JE})
		self.assertIn("not permitted to read", message)

	def test_an_unknown_document_is_a_clean_error(self):
		message = self.tool_error("list_comments", {"doctype": "Journal Entry", "name": "ACC-JV-NOPE"})
		self.assertIn("no Journal Entry named", message)


class ListAssignedTodos(V2TestCase):
	def test_open_only_by_default(self):
		data = self.tool_data("list_assigned_todos")
		self.assertEqual(
			sorted(row["name"] for row in data["todos"]),
			["todo-open-future", "todo-open-overdue"],
		)
		self.assertEqual(data["status"], "Open")

	def test_filters_by_assignee(self):
		data = self.tool_data("list_assigned_todos", {"user": BUYER, "status": ""})
		self.assertEqual([row["name"] for row in data["todos"]], ["todo-closed"])

	def test_normalises_the_assignee_field(self):
		"""`owner` is the creator; `allocated_to` is the assignee. Confusing them
		is the classic ToDo bug."""
		data = self.tool_data("list_assigned_todos", {"user": APPROVER})
		self.assertEqual(data["assignee_field"], "allocated_to")
		for row in data["todos"]:
			self.assertEqual(row["assigned_to"], APPROVER)
			self.assertEqual(row["owner"], "Administrator")

	def test_flags_overdue_items(self):
		data = self.tool_data("list_assigned_todos", {"user": APPROVER})
		by_name = {row["name"]: row for row in data["todos"]}
		self.assertTrue(by_name["todo-open-overdue"]["overdue"])
		self.assertFalse(by_name["todo-open-future"]["overdue"])
		self.assertEqual(data["overdue_count"], 1)

	def test_an_unknown_user_is_refused(self):
		message = self.tool_error("list_assigned_todos", {"user": "ghost@example.test"})
		self.assertIn("no User named", message)

	def test_a_nonsense_status_lists_the_valid_ones(self):
		message = self.tool_error("list_assigned_todos", {"status": "Pending"})
		self.assertIn("Open, Closed, Cancelled", message)


class CreateTodo(V2TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_create_todo=1)

	def test_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error("create_todo", {"subject": "Do the thing", "owner": APPROVER})
		self.assertIn("allow_create_todo", message)

	def test_creates_a_todo_assigned_to_the_named_user(self):
		data = self.tool_data("create_todo", {"subject": "Chase Westbrook", "owner": APPROVER})
		stored = STORE.get_raw("ToDo", data["name"])
		self.assertEqual(stored["allocated_to"], APPROVER)
		self.assertEqual(stored["status"], "Open")
		self.assertEqual(data["assigned_to"], APPROVER)

	def test_owner_means_assignee_not_creator(self):
		"""The argument is named for what a caller means by it; the response says
		where the value actually went."""
		data = self.tool_data("create_todo", {"subject": "x task", "owner": APPROVER})
		self.assertEqual(data["assignee_field"], "allocated_to")
		self.assertEqual(data["assigned_by"], "Administrator")

	def test_the_subject_is_folded_into_the_description(self):
		data = self.tool_data(
			"create_todo",
			{"subject": "Chase Westbrook", "owner": APPROVER, "description": "90+ days"},
		)
		stored = STORE.get_raw("ToDo", data["name"])
		self.assertIn("Chase Westbrook", stored["description"])
		self.assertIn("90+ days", stored["description"])
		self.assertIn("no subject field", data["subject_handling"])

	def test_a_subject_alone_becomes_the_description(self):
		data = self.tool_data("create_todo", {"subject": "Just this", "owner": APPROVER})
		self.assertEqual(STORE.get_raw("ToDo", data["name"])["description"], "Just this")

	def test_priority_and_due_date_are_carried(self):
		data = self.tool_data(
			"create_todo",
			{
				"subject": "Urgent",
				"owner": APPROVER,
				"priority": "High",
				"date": "2026-8-3",
			},
		)
		stored = STORE.get_raw("ToDo", data["name"])
		self.assertEqual(stored["priority"], "High")
		self.assertEqual(stored["date"], "2026-08-03")

	def test_a_reference_document_is_linked(self):
		data = self.tool_data(
			"create_todo",
			{
				"subject": "Review this entry",
				"owner": APPROVER,
				"reference_doctype": "Journal Entry",
				"reference_name": ATTACHED_JE,
			},
		)
		stored = STORE.get_raw("ToDo", data["name"])
		self.assertEqual(stored["reference_type"], "Journal Entry")
		self.assertEqual(stored["reference_name"], ATTACHED_JE)

	def test_half_a_reference_is_refused(self):
		message = self.tool_error(
			"create_todo",
			{"subject": "x", "owner": APPROVER, "reference_doctype": "Journal Entry"},
		)
		self.assertIn("go together", message)

	def test_a_reference_to_a_missing_document_is_refused(self):
		message = self.tool_error(
			"create_todo",
			{
				"subject": "x",
				"owner": APPROVER,
				"reference_doctype": "Journal Entry",
				"reference_name": "ACC-JV-NOPE",
			},
		)
		self.assertIn("no Journal Entry named", message)

	def test_an_unknown_assignee_explains_what_owner_means(self):
		message = self.tool_error("create_todo", {"subject": "x", "owner": "ghost@example.test"})
		self.assertIn("no User named", message)
		self.assertIn("assigned to", message)

	def test_a_disabled_assignee_is_refused(self):
		message = self.tool_error("create_todo", {"subject": "x", "owner": "retired@example.test"})
		self.assertIn("disabled and cannot be assigned", message)

	def test_a_bad_priority_lists_the_valid_ones(self):
		message = self.tool_error("create_todo", {"subject": "x", "owner": APPROVER, "priority": "Urgent"})
		self.assertIn("Low, Medium, High", message)

	def test_a_bad_date_says_what_to_send(self):
		message = self.tool_error("create_todo", {"subject": "x", "owner": APPROVER, "date": "next tuesday"})
		self.assertIn("YYYY-MM-DD", message)

	def test_nothing_is_created_when_validation_fails(self):
		before = len(STORE.rows("ToDo"))
		self.tool_error("create_todo", {"subject": "x", "owner": "ghost@example.test"})
		self.assertEqual(len(STORE.rows("ToDo")), before)

	def test_it_is_audited_as_a_mutation(self):
		self.tool_data("create_todo", {"subject": "Audited task", "owner": APPROVER})
		row = self.assertAudited("create_todo", status="Success")
		self.assertEqual(row["docstatus_delta"], "none → 0 (draft)")
		self.assertIn("Audited task", row["result_summary"])

	def test_it_is_registered_as_a_mutating_tool(self):
		self.assertIn("create_todo", registry.MUTATING_TOOLS)
