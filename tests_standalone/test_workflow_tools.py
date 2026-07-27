# SPDX-License-Identifier: MIT
"""Workflow tools, over a synthetic Purchase Order approval workflow."""

import sys

from erpnext_mcp import registry

from .fixtures import APPROVER, BUYER, WORKFLOW_NAME, V2TestCase
from .harness import STORE, frappe

PO_PENDING = "PUR-ORD-2026-00001"
PO_APPROVED = "PUR-ORD-2026-00002"
PO_DRAFT = "PUR-ORD-2026-00003"
PO_PENDING_ACTION = "Approve"


class ListWorkflows(V2TestCase):
	def test_lists_definitions_with_states_and_transitions(self):
		data = self.tool_data("list_workflows")
		self.assertEqual(data["count"], 2)
		self.assertEqual(data["active_count"], 1)
		workflow = next(w for w in data["workflows"] if w["name"] == WORKFLOW_NAME)
		self.assertEqual(workflow["document_type"], "Purchase Order")
		self.assertEqual(len(workflow["states"]), 4)
		self.assertEqual(len(workflow["transitions"]), 3)

	def test_reports_the_state_field(self):
		data = self.tool_data("list_workflows")
		workflow = next(w for w in data["workflows"] if w["name"] == WORKFLOW_NAME)
		self.assertEqual(workflow["workflow_state_field"], "workflow_state")

	def test_identifies_terminal_states(self):
		"""A state nothing leads out of is finished, not waiting — that is the
		distinction the pending-approvals worklist is built on."""
		data = self.tool_data("list_workflows")
		workflow = next(w for w in data["workflows"] if w["name"] == WORKFLOW_NAME)
		self.assertEqual(workflow["terminal_states"], ["Approved", "Rejected"])

	def test_collects_every_role_involved(self):
		data = self.tool_data("list_workflows")
		workflow = next(w for w in data["workflows"] if w["name"] == WORKFLOW_NAME)
		self.assertEqual(workflow["roles"], ["Purchase Manager", "Purchase User"])

	def test_flags_transitions_that_carry_a_condition(self):
		data = self.tool_data("list_workflows")
		workflow = next(w for w in data["workflows"] if w["name"] == WORKFLOW_NAME)
		reject = next(t for t in workflow["transitions"] if t["action"] == "Reject")
		self.assertTrue(reject["has_condition"])
		self.assertEqual(reject["condition"], "doc.grand_total > 0")
		approve = next(t for t in workflow["transitions"] if t["action"] == "Approve")
		self.assertFalse(approve["has_condition"])

	def test_reports_the_self_approval_rule(self):
		data = self.tool_data("list_workflows")
		workflow = next(w for w in data["workflows"] if w["name"] == WORKFLOW_NAME)
		approve = next(t for t in workflow["transitions"] if t["action"] == "Approve")
		self.assertFalse(approve["allow_self_approval"])


class WorkflowState(V2TestCase):
	def test_reports_the_current_state_and_the_way_out(self):
		data = self.tool_data("get_workflow_state", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertEqual(data["current_state"], "Pending Approval")
		self.assertEqual(sorted(t["action"] for t in data["next_transitions"]), ["Approve", "Reject"])
		self.assertFalse(data["is_terminal"])

	def test_a_terminal_state_says_so(self):
		data = self.tool_data("get_workflow_state", {"doctype": "Purchase Order", "name": PO_APPROVED})
		self.assertEqual(data["current_state"], "Approved")
		self.assertTrue(data["is_terminal"])
		self.assertEqual(data["next_transitions"], [])

	def test_includes_the_state_definition(self):
		data = self.tool_data("get_workflow_state", {"doctype": "Purchase Order", "name": PO_APPROVED})
		self.assertEqual(data["current_state_detail"]["doc_status"], 1)

	def test_a_doctype_with_no_workflow_lists_the_ones_that_exist(self):
		message = self.tool_error(
			"get_workflow_state", {"doctype": "Journal Entry", "name": "ACC-JV-2026-00001"}
		)
		self.assertIn("no active Workflow governs Journal Entry", message)
		self.assertIn("Purchase Order", message)

	def test_an_inactive_workflow_does_not_count(self):
		"""There is an inactive Sales Order workflow in the fixture; it must not
		be treated as governing anything."""
		message = self.tool_error(
			"get_workflow_state", {"doctype": "Sales Order", "name": "SAL-ORD-2026-00001"}
		)
		self.assertIn("no active Workflow governs Sales Order", message)

	def test_an_unknown_document_is_a_clean_error(self):
		message = self.tool_error("get_workflow_state", {"doctype": "Purchase Order", "name": "PUR-ORD-NOPE"})
		self.assertIn("no Purchase Order named", message)

	def test_an_unknown_doctype_is_a_clean_error(self):
		message = self.tool_error("get_workflow_state", {"doctype": "Invented Doctype", "name": "X"})
		self.assertIn("no DocType named", message)

	def test_a_document_outside_the_workflow_is_explained(self):
		STORE.get_raw("Purchase Order", PO_DRAFT)["workflow_state"] = None
		data = self.tool_data("get_workflow_state", {"doctype": "Purchase Order", "name": PO_DRAFT})
		self.assertIsNone(data["current_state"])
		self.assertIn("created before the workflow was added", data["note"])


class PendingApprovals(V2TestCase):
	def test_groups_documents_by_state_with_the_roles_that_can_act(self):
		data = self.tool_data("list_pending_approvals")
		states = {group["state"]: group for group in data["pending"]}
		self.assertEqual(sorted(states), ["Draft", "Pending Approval"])
		self.assertEqual(states["Pending Approval"]["allowed_roles"], ["Purchase Manager"])
		self.assertEqual(sorted(states["Pending Approval"]["actions"]), ["Approve", "Reject"])

	def test_terminal_states_are_never_listed(self):
		"""PUR-ORD-2026-00002 is Approved. A worklist that includes it is just a
		list of every document."""
		data = self.tool_data("list_pending_approvals")
		listed = {doc["name"] for group in data["pending"] for doc in group["documents"]}
		self.assertNotIn(PO_APPROVED, listed)
		self.assertIn(PO_PENDING, listed)

	def test_user_filter_narrows_to_states_their_roles_can_act_on(self):
		data = self.tool_data("list_pending_approvals", {"user": BUYER})
		self.assertEqual([group["state"] for group in data["pending"]], ["Draft"])
		self.assertEqual(data["user_roles"], ["Purchase User"])

	def test_an_approver_sees_the_approval_queue(self):
		data = self.tool_data("list_pending_approvals", {"user": APPROVER})
		self.assertEqual(
			sorted(group["state"] for group in data["pending"]),
			["Draft", "Pending Approval"],
		)

	def test_a_user_with_no_relevant_role_sees_nothing(self):
		data = self.tool_data("list_pending_approvals", {"user": "retired@example.test"})
		self.assertEqual(data["pending"], [])
		self.assertEqual(data["document_count"], 0)

	def test_can_be_restricted_to_one_workflow(self):
		data = self.tool_data("list_pending_approvals", {"workflow": WORKFLOW_NAME})
		self.assertTrue(all(g["workflow"] == WORKFLOW_NAME for g in data["pending"]))

	def test_an_unknown_workflow_is_refused(self):
		message = self.tool_error("list_pending_approvals", {"workflow": "Nope"})
		self.assertIn("no active Workflow named", message)

	def test_an_unknown_user_is_refused(self):
		message = self.tool_error("list_pending_approvals", {"user": "ghost@example.test"})
		self.assertIn("no User named", message)

	def test_the_per_state_limit_is_reported(self):
		data = self.tool_data("list_pending_approvals", {"limit": 1})
		self.assertEqual(data["limit_per_state"], 1)
		for group in data["pending"]:
			self.assertLessEqual(group["count"], 1)


class AvailableActions(V2TestCase):
	def act_as(self, user):
		"""Point the MCP System User at somebody.

		`frappe.set_user` alone would not do it: the endpoint sets the session
		user from settings on every request, which is the behaviour that makes
		"the acting user" a configuration decision rather than a caller's claim.
		"""
		self.configure(enabled=1, require_user_context=1, mcp_system_user=user)

	def test_resolves_through_frappes_own_get_transitions(self):
		data = self.tool_data("list_available_actions", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertEqual(data["resolved_via"], "frappe.model.workflow.get_transitions")
		self.assertTrue(data["conditions_evaluated"])

	def test_an_approver_can_approve_someone_elses_order(self):
		data = self.tool_data("list_available_actions", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertEqual(sorted(data["available_actions"]), ["Approve", "Reject"])

	def test_the_self_approval_rule_removes_the_action(self):
		"""The approver owns this order, and Approve has allow_self_approval off.

		Isolated deliberately: the acting user keeps the Purchase Manager role, so
		the only thing removing Approve is the self-approval rule — the exact case
		a role-only check gets wrong, and the reason these tools delegate. Reject
		allows self-approval and survives.
		"""
		self.act_as(APPROVER)
		STORE.get_raw("Purchase Order", PO_PENDING)["owner"] = APPROVER
		data = self.tool_data("list_available_actions", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertEqual(data["user"], APPROVER)
		self.assertNotIn("Approve", data["available_actions"])
		self.assertIn("Reject", data["available_actions"])

	def test_a_false_condition_removes_the_action(self):
		STORE.get_raw("Purchase Order", PO_PENDING)["grand_total"] = 0
		data = self.tool_data("list_available_actions", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertNotIn("Reject", data["available_actions"])
		self.assertIn("Approve", data["available_actions"])

	def test_a_raising_condition_is_reported_not_swallowed(self):
		STORE.get_raw("Workflow", WORKFLOW_NAME)["transitions"][2]["condition"] = "1/0"
		message = self.tool_error("list_available_actions", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertIn("evaluating this workflow's transitions failed", message)

	def test_falls_back_to_a_role_check_and_says_so(self):
		"""On a Frappe without get_transitions the answer is a superset, and the
		caller has to be told that rather than left to assume."""
		workflow_module = sys.modules["frappe.model.workflow"]
		saved = workflow_module.get_transitions
		del workflow_module.get_transitions
		try:
			data = self.tool_data("list_available_actions", {"doctype": "Purchase Order", "name": PO_PENDING})
		finally:
			workflow_module.get_transitions = saved
		self.assertEqual(data["resolved_via"], "role check only (this app)")
		self.assertFalse(data["conditions_evaluated"])
		self.assertIn("superset", data["warning"])

	def test_reports_which_user_the_answer_is_about(self):
		data = self.tool_data("list_available_actions", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertEqual(data["user"], "Administrator")
		self.assertIn("Purchase Manager", data["user_roles"])


class AdvanceWorkflow(V2TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_advance_workflow=1)

	def act_as(self, user):
		self.configure(
			enabled=1,
			allow_advance_workflow=1,
			require_user_context=1,
			mcp_system_user=user,
		)

	def test_is_off_by_default(self):
		self.configure(enabled=1)
		message = self.tool_error(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
		)
		self.assertIn("allow_advance_workflow", message)

	def test_moves_the_document_to_the_next_state(self):
		data = self.tool_data(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
		)
		self.assertEqual(data["state_before"], "Pending Approval")
		self.assertEqual(data["state_after"], "Approved")
		self.assertEqual(STORE.get_raw("Purchase Order", PO_PENDING)["workflow_state"], "Approved")

	def test_the_target_states_docstatus_is_applied(self):
		"""Approved is doc_status 1, so approving submits the order — which is
		exactly what the Desk button does, and why this is a mutating tool."""
		data = self.tool_data(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
		)
		self.assertEqual(data["docstatus_before"], 0)
		self.assertEqual(data["docstatus_after"], 1)

	def test_the_docstatus_change_is_audited(self):
		self.tool_data(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
		)
		row = self.assertAudited("advance_workflow", status="Success")
		self.assertEqual(row["docstatus_delta"], "0 → 1 (submitted)")
		self.assertIn("Pending Approval", row["result_summary"])

	def test_an_unavailable_action_is_refused_with_the_alternatives(self):
		message = self.tool_error(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Delete Everything"},
		)
		self.assertIn("is not available", message)
		self.assertIn("Approve", message)

	def test_a_self_approval_attempt_is_refused_and_changes_nothing(self):
		self.act_as(APPROVER)
		STORE.get_raw("Purchase Order", PO_PENDING)["owner"] = APPROVER
		message = self.tool_error(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
		)
		self.assertIn("not available to", message)
		self.assertEqual(STORE.get_raw("Purchase Order", PO_PENDING)["workflow_state"], "Pending Approval")

	def test_it_refuses_rather_than_hand_rolling_a_state_write(self):
		workflow_module = sys.modules["frappe.model.workflow"]
		saved = workflow_module.apply_workflow
		del workflow_module.apply_workflow
		try:
			message = self.tool_error(
				"advance_workflow",
				{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
			)
		finally:
			workflow_module.apply_workflow = saved
		self.assertIn("does not export", message)
		self.assertEqual(STORE.get_raw("Purchase Order", PO_PENDING)["workflow_state"], "Pending Approval")

	def test_it_is_registered_as_a_mutating_tool(self):
		self.assertIn("advance_workflow", registry.MUTATING_TOOLS)
		self.assertFalse(registry.TOOLS["advance_workflow"]["annotations"]["readOnlyHint"])


class DryRun(V2TestCase):
	"""`dry_run=True` answers "what would this do" without doing any of it.

	The point is the pattern: dry-run, show a human, then execute. So the dry run
	has to surface the fact that makes this tool dangerous — that a transition
	into a doc_status 1 state SUBMITS the document — and it has to answer rather
	than raise when the action would be refused, because "it would be refused" is
	the answer to the question.
	"""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, allow_advance_workflow=1)

	def dry(self, action=PO_PENDING_ACTION, name=PO_PENDING, **kwargs):
		return self.tool_data(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": name, "action": action, "dry_run": True},
			**kwargs,
		)

	def test_it_changes_nothing(self):
		before = dict(STORE.get_raw("Purchase Order", PO_PENDING))
		self.dry()
		self.assertEqual(STORE.get_raw("Purchase Order", PO_PENDING), before)

	def test_it_says_it_did_not_execute(self):
		data = self.dry()
		self.assertTrue(data["dry_run"])
		self.assertFalse(data["executed"])

	def test_it_reports_the_target_state(self):
		data = self.dry()
		self.assertEqual(data["current_state"], "Pending Approval")
		self.assertEqual(data["would_move_to"], "Approved")
		self.assertTrue(data["would_succeed"])

	def test_it_warns_that_the_transition_submits_the_document(self):
		"""The fact that makes this tool dangerous, and the one invisible from the
		action's name: Approved is doc_status 1."""
		data = self.dry()
		self.assertTrue(data["would_submit"])
		self.assertEqual(data["would_set_docstatus"], 1)
		self.assertTrue(any("SUBMITS the document" in effect for effect in data["effects"]))

	def test_a_non_submitting_transition_says_so(self):
		data = self.dry(action="Submit for Approval", name=PO_DRAFT)
		self.assertFalse(data["would_submit"])
		self.assertEqual(data["would_set_docstatus"], 0)
		self.assertTrue(any("leaves docstatus at 0" in effect for effect in data["effects"]))

	def test_an_unavailable_action_is_answered_not_raised(self):
		"""'It would be refused, and here is why' is the answer to the question,
		not a failure to answer it."""
		data = self.dry(action="Delete Everything")
		self.assertFalse(data["would_succeed"])
		self.assertIn("is not available", data["refusal_reason"])
		self.assertIn("Nothing to execute", data["next_step"])

	def test_a_self_approval_refusal_shows_up_in_the_dry_run(self):
		self.configure(
			enabled=1,
			allow_advance_workflow=1,
			require_user_context=1,
			mcp_system_user=APPROVER,
		)
		STORE.get_raw("Purchase Order", PO_PENDING)["owner"] = APPROVER
		data = self.dry()
		self.assertFalse(data["would_succeed"])
		self.assertEqual(data["available_actions"], ["Reject"])

	def test_it_lists_what_is_available_instead(self):
		data = self.dry(action="Delete Everything")
		self.assertEqual(sorted(data["available_actions"]), ["Approve", "Reject"])

	def test_it_points_at_how_to_execute(self):
		data = self.dry()
		self.assertIn("dry_run=false", data["next_step"])

	def test_the_audit_row_records_no_docstatus_change(self):
		self.dry()
		row = self.assertAudited("advance_workflow", status="Success")
		self.assertEqual(row["docstatus_delta"], "")
		self.assertIn("dry run", row["result_summary"])

	def test_a_dry_run_still_needs_the_switch(self):
		"""It reads only, but it is the same tool — an operator who has not
		enabled workflow writes has not enabled reasoning about them either."""
		self.configure(enabled=1, allow_advance_workflow=0)
		message = self.tool_error(
			"advance_workflow",
			{
				"doctype": "Purchase Order",
				"name": PO_PENDING,
				"action": "Approve",
				"dry_run": True,
			},
		)
		self.assertIn("allow_advance_workflow", message)

	def test_a_malformed_question_still_raises(self):
		"""Unknown document is not "it would be refused" — the question itself
		does not parse."""
		message = self.tool_error(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": "PUR-ORD-NOPE", "action": "Approve", "dry_run": True},
		)
		self.assertIn("no Purchase Order named", message)

	def test_dry_run_accepts_the_string_a_model_will_send(self):
		data = self.tool_data(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve", "dry_run": "true"},
		)
		self.assertTrue(data["dry_run"])

	def test_an_unreadable_dry_run_is_refused_rather_than_executed(self):
		"""v0.5.0. This used to map everything it did not recognise to False,
		which turned a mistyped dry_run into a live workflow transition — and a
		transition can submit or cancel the document."""
		before = frappe.db.get_value("Purchase Order", PO_PENDING, "workflow_state")
		message = self.tool_error(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve", "dry_run": "sure"},
		)
		self.assertIn("must be true or false", message)
		self.assertEqual(frappe.db.get_value("Purchase Order", PO_PENDING, "workflow_state"), before)

	def test_omitting_dry_run_executes(self):
		data = self.tool_data(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
		)
		self.assertNotIn("dry_run", data)
		self.assertEqual(data["state_after"], "Approved")


class MultipleActiveWorkflows(V2TestCase):
	"""Two active workflows on one DocType — a state Frappe prevents, and a
	direct database edit can still produce."""

	def activate_a_second(self):
		STORE.get_raw("Workflow", "Inactive Example")["document_type"] = "Purchase Order"
		STORE.get_raw("Workflow", "Inactive Example")["is_active"] = 1

	def test_the_tools_refuse_rather_than_pick_one(self):
		"""Which workflow governs a document is undefined here, and guessing on a
		submitting transition is unrecoverable."""
		self.activate_a_second()
		message = self.tool_error("get_workflow_state", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertIn("2 Workflows are active", message)
		self.assertIn("Inactive Example", message)
		self.assertIn(WORKFLOW_NAME, message)

	def test_the_refusal_says_how_to_fix_it(self):
		self.activate_a_second()
		message = self.tool_error("get_workflow_state", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertIn("deactivate all but one", message)

	def test_advancing_is_refused_too(self):
		self.activate_a_second()
		self.configure(enabled=1, allow_advance_workflow=1)
		message = self.tool_error(
			"advance_workflow",
			{"doctype": "Purchase Order", "name": PO_PENDING, "action": "Approve"},
		)
		self.assertIn("2 Workflows are active", message)
		self.assertEqual(STORE.get_raw("Purchase Order", PO_PENDING)["workflow_state"], "Pending Approval")

	def test_list_workflows_still_shows_both_for_diagnosis(self):
		self.activate_a_second()
		data = self.tool_data("list_workflows")
		self.assertEqual(data["active_count"], 2)

	def test_one_active_workflow_is_unaffected(self):
		data = self.tool_data("get_workflow_state", {"doctype": "Purchase Order", "name": PO_PENDING})
		self.assertEqual(data["workflow"], WORKFLOW_NAME)
