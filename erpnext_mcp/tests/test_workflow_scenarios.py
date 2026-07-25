# SPDX-License-Identifier: MIT
"""Workflow verification against a real Frappe Workflow on a real DocType.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_workflow_scenarios

WHY THIS SUITE EXISTS. `advance_workflow` can submit a Journal Entry — write GL
Entries, move balances — because that is what a transition into a `doc_status: 1`
state does. Before an AI client is authorised to do that, the behaviour has to be
demonstrated rather than assumed, and so do the refusals: a workflow tool that is
right about the happy path and wrong about permission denial is worse than none,
because "the AI said I could approve it" is not a defence.

The standalone suite exercises all of this against a double. A double is only
ever as good as its author's model of the framework, and this suite exists
because that model was wrong in a way that mattered:

    Frappe's `get_transitions` does NOT enforce self-approval. It filters on
    role and condition only. `allow_self_approval` is checked inside
    `apply_workflow`, which throws at execution time.

That means `list_available_actions` would have advertised an action the user
could not take, and `dry_run` would have promised `would_succeed: true` for a
transition destined to throw — the exact promise dry_run exists to make. The app
now applies the rule itself (`workflow._self_approval_permitted`) and the double
was corrected to match. These tests are what keeps both honest.

WHAT IT BUILDS. A custom submittable DocType, four Workflow States, three
Workflow Actions, two Roles, two Users and one Workflow — then walks documents
through it. `custom=1` means no files are written to the app; creating a DocType
is DDL and therefore not transactional, so setUpClass commits and tearDownClass
cleans up in dependency order. Everything is prefixed `MCP Probe` / `MCP
Workflow Probe` so a crashed run leaves something obviously disposable.

The suite skips itself if the DocType cannot be created — a site with an
unusually locked-down configuration should not show a wall of red.
"""

import frappe

from .test_integration import MCPIntegrationTestCase

PROBE_DOCTYPE = "MCP Workflow Probe"
PROBE_WORKFLOW = "MCP Probe Approval"
SECOND_WORKFLOW = "MCP Probe Approval Alternate"

SUBMITTER_ROLE = "MCP Probe Submitter"
APPROVER_ROLE = "MCP Probe Approver"
SUBMITTER = "mcp-probe-submitter@example.test"
APPROVER = "mcp-probe-approver@example.test"

STATES = ("Draft", "Pending Approval", "Approved", "Rejected")
ACTIONS = ("Submit for Approval", "Approve", "Reject")


def _ensure(doctype: str, name: str, payload: dict) -> None:
	if frappe.db.exists(doctype, name):
		return
	frappe.get_doc({"doctype": doctype, **payload}).insert(ignore_permissions=True)


def _drop(doctype: str, name: str) -> None:
	try:
		if frappe.db.exists(doctype, name):
			frappe.delete_doc(doctype, name, force=True, ignore_permissions=True, delete_permanently=True)
	except Exception:
		pass


class WorkflowScenario(MCPIntegrationTestCase):
	"""Builds the probe workflow once for the class, tears it down after."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.built = False
		try:
			cls._build()
			frappe.db.commit()
			cls.built = True
		except Exception as exc:  # pragma: no cover - site-dependent
			frappe.db.rollback()
			cls._teardown()
			frappe.db.commit()
			raise cls.skipException(f"cannot build the probe workflow on this site: {exc}") from exc

	@classmethod
	def tearDownClass(cls):
		if getattr(cls, "built", False):
			cls._teardown()
			frappe.db.commit()
		super().tearDownClass()

	# -- fixture ---------------------------------------------------------
	@classmethod
	def _build(cls):
		cls._teardown()  # idempotent: a crashed earlier run leaves debris

		for state in STATES:
			_ensure("Workflow State", state, {"workflow_state_name": state})
		for action in ACTIONS:
			_ensure("Workflow Action Master", action, {"workflow_action_name": action})
		for role in (SUBMITTER_ROLE, APPROVER_ROLE):
			_ensure("Role", role, {"role_name": role, "desk_access": 1})
		for email, roles in ((SUBMITTER, [SUBMITTER_ROLE]), (APPROVER, [APPROVER_ROLE, SUBMITTER_ROLE])):
			if not frappe.db.exists("User", email):
				frappe.get_doc(
					{
						"doctype": "User",
						"email": email,
						"first_name": email.split("@")[0],
						"send_welcome_email": 0,
						"roles": [{"role": role} for role in roles],
					}
				).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "DocType",
				"name": PROBE_DOCTYPE,
				"module": "ERPNext MCP",
				"custom": 1,
				"is_submittable": 1,
				"autoname": "hash",
				"fields": [
					{"fieldname": "title", "label": "Title", "fieldtype": "Data", "reqd": 1},
					{"fieldname": "amount", "label": "Amount", "fieldtype": "Currency"},
					{
						# The lever for "a submit hook raises": this field is only
						# mandatory once docstatus is 1, so the document inserts
						# happily and refuses to submit while it is empty. Stock
						# Frappe validation, no Server Script needed — so the test
						# runs on every site rather than only where server scripts
						# are enabled.
						"fieldname": "approval_note",
						"label": "Approval Note",
						"fieldtype": "Data",
						"mandatory_depends_on": "eval:doc.docstatus==1",
					},
				],
				"permissions": [
					{"role": "System Manager", "read": 1, "write": 1, "create": 1, "submit": 1, "cancel": 1},
					{"role": SUBMITTER_ROLE, "read": 1, "write": 1, "create": 1, "submit": 1},
					{"role": APPROVER_ROLE, "read": 1, "write": 1, "submit": 1, "cancel": 1},
				],
			}
		).insert(ignore_permissions=True)

		frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": PROBE_WORKFLOW,
				"document_type": PROBE_DOCTYPE,
				"is_active": 1,
				"workflow_state_field": "workflow_state",
				"send_email_alert": 0,
				"states": [
					{"state": "Draft", "doc_status": "0", "allow_edit": SUBMITTER_ROLE},
					{"state": "Pending Approval", "doc_status": "0", "allow_edit": APPROVER_ROLE},
					{"state": "Approved", "doc_status": "1", "allow_edit": APPROVER_ROLE},
					{"state": "Rejected", "doc_status": "0", "allow_edit": APPROVER_ROLE},
				],
				"transitions": [
					{
						"state": "Draft",
						"action": "Submit for Approval",
						"next_state": "Pending Approval",
						"allowed": SUBMITTER_ROLE,
						"allow_self_approval": 1,
					},
					{
						"state": "Pending Approval",
						"action": "Approve",
						"next_state": "Approved",
						"allowed": APPROVER_ROLE,
						# The rule this suite exists to pin down.
						"allow_self_approval": 0,
					},
					{
						"state": "Pending Approval",
						"action": "Reject",
						"next_state": "Rejected",
						"allowed": APPROVER_ROLE,
						"allow_self_approval": 1,
						"condition": "doc.amount > 0",
					},
				],
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _teardown(cls):
		for workflow in (PROBE_WORKFLOW, SECOND_WORKFLOW):
			_drop("Workflow", workflow)
		try:
			frappe.db.delete(PROBE_DOCTYPE)
		except Exception:
			pass
		_drop("Custom Field", f"{PROBE_DOCTYPE}-workflow_state")
		_drop("DocType", PROBE_DOCTYPE)
		for email in (SUBMITTER, APPROVER):
			_drop("User", email)
		for role in (SUBMITTER_ROLE, APPROVER_ROLE):
			_drop("Role", role)

	# -- helpers ---------------------------------------------------------
	def setUp(self):
		super().setUp()
		self.enable("advance_workflow")

	def act_as(self, user):
		"""Point the MCP System User at somebody.

		Administrator is useless for permission tests: `frappe.get_roles` returns
		every role on the site for it, and `has_approval_access` exempts it from
		self-approval outright.
		"""
		self.doc.require_user_context = 1
		self.doc.mcp_system_user = user
		self.doc.allow_advance_workflow = 1
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype="ERPNext MCP Settings")

	def probe(self, *, owner=None, state="Draft", amount=100, approval_note=None):
		doc = frappe.get_doc(
			{
				"doctype": PROBE_DOCTYPE,
				"title": "probe",
				"amount": amount,
				"approval_note": approval_note,
				"workflow_state": state,
			}
		)
		if owner:
			doc.owner = owner
		doc.insert(ignore_permissions=True)
		if owner and doc.owner != owner:
			frappe.db.set_value(PROBE_DOCTYPE, doc.name, "owner", owner, update_modified=False)
			doc.reload()
		return doc

	def reread(self, name):
		return frappe.get_doc(PROBE_DOCTYPE, name)

	def advance(self, name, action, **arguments):
		return self.tool(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": name, "action": action, **arguments},
		)

	def last_log(self, tool_name="advance_workflow"):
		rows = frappe.db.get_all(
			"MCP Action Log",
			filters={"tool_name": tool_name},
			fields=["result_status", "result_summary", "docstatus_delta"],
			order_by="creation desc",
			limit=1,
		)
		self.assertTrue(rows, f"no audit row for {tool_name}")
		return rows[0]


class HappyPath(WorkflowScenario):
	def test_the_workflow_built_and_is_active(self):
		self.assertTrue(frappe.db.get_value("Workflow", PROBE_WORKFLOW, "is_active"))
		self.assertEqual(frappe.db.get_value("Workflow", PROBE_WORKFLOW, "document_type"), PROBE_DOCTYPE)

	def test_frappe_created_the_state_field_for_us(self):
		"""The Workflow controller adds `workflow_state` as a Custom Field on the
		governed doctype. This app reads whatever `workflow_state_field` names."""
		self.assertTrue(frappe.get_meta(PROBE_DOCTYPE).has_field("workflow_state"))

	def test_list_workflows_sees_it(self):
		data = self.tool_data("list_workflows")
		entry = next(w for w in data["workflows"] if w["name"] == PROBE_WORKFLOW)
		self.assertEqual(entry["document_type"], PROBE_DOCTYPE)
		self.assertEqual(len(entry["states"]), 4)
		self.assertEqual(len(entry["transitions"]), 3)
		self.assertEqual(sorted(entry["terminal_states"]), ["Approved", "Rejected"])

	def test_a_document_walks_draft_to_pending_to_approved(self):
		doc = self.probe(approval_note="ok to pay")

		first = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Submit for Approval"},
		)
		self.assertEqual(first["state_before"], "Draft")
		self.assertEqual(first["state_after"], "Pending Approval")
		self.assertEqual(first["docstatus_after"], 0)
		self.assertEqual(self.reread(doc.name).workflow_state, "Pending Approval")

		second = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve"},
		)
		self.assertEqual(second["state_after"], "Approved")
		self.assertEqual(second["docstatus_after"], 1)

		final = self.reread(doc.name)
		self.assertEqual(final.workflow_state, "Approved")
		self.assertEqual(final.docstatus, 1)

	def test_the_submitting_transition_is_audited_as_a_docstatus_change(self):
		doc = self.probe(state="Pending Approval", approval_note="ok")
		self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve"},
		)
		row = self.last_log()
		self.assertEqual(row.result_status, "Success")
		self.assertEqual(row.docstatus_delta, "0 → 1 (submitted)")

	def test_get_workflow_state_tracks_the_document(self):
		doc = self.probe(state="Pending Approval")
		data = self.tool_data("get_workflow_state", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertEqual(data["current_state"], "Pending Approval")
		self.assertEqual(sorted(t["action"] for t in data["next_transitions"]), ["Approve", "Reject"])
		self.assertFalse(data["is_terminal"])

	def test_pending_approvals_finds_it(self):
		doc = self.probe(state="Pending Approval")
		data = self.tool_data("list_pending_approvals", {"workflow": PROBE_WORKFLOW})
		listed = {row["name"] for group in data["pending"] for row in group["documents"]}
		self.assertIn(doc.name, listed)


class TerminalState(WorkflowScenario):
	def test_a_terminal_document_has_nowhere_to_go(self):
		doc = self.probe(state="Approved", approval_note="done")
		data = self.tool_data("get_workflow_state", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertTrue(data["is_terminal"])
		self.assertEqual(data["next_transitions"], [])

	def test_advancing_from_a_terminal_state_is_refused(self):
		doc = self.probe(state="Approved", approval_note="done")
		result = self.advance(doc.name, "Approve")
		self.assertTrue(result["isError"])
		self.assertIn("not available", result["content"][0]["text"])
		self.assertEqual(self.reread(doc.name).workflow_state, "Approved")

	def test_the_refusal_lists_nothing_available(self):
		doc = self.probe(state="Rejected")
		result = self.advance(doc.name, "Approve")
		self.assertIn("Available: <none>", result["content"][0]["text"])


class PermissionDenial(WorkflowScenario):
	def test_a_user_without_the_role_cannot_advance(self):
		"""The submitter has no Approver role, so Approve is not theirs to take."""
		doc = self.probe(state="Pending Approval", owner=APPROVER)
		self.act_as(SUBMITTER)
		result = self.advance(doc.name, "Approve")
		self.assertTrue(result["isError"])
		self.assertIn("not available to", result["content"][0]["text"])

	def test_nothing_changed(self):
		doc = self.probe(state="Pending Approval", owner=APPROVER)
		self.act_as(SUBMITTER)
		self.advance(doc.name, "Approve")
		fresh = self.reread(doc.name)
		self.assertEqual(fresh.workflow_state, "Pending Approval")
		self.assertEqual(fresh.docstatus, 0)

	def test_the_refusal_is_audited_as_blocked_or_error(self):
		"""A refused transition must leave a record. `Blocked` is a switch or a
		site prerequisite; a permission refusal from inside the tool is `Error`.
		Either way the attempt is on the log."""
		doc = self.probe(state="Pending Approval", owner=APPROVER)
		self.act_as(SUBMITTER)
		self.advance(doc.name, "Approve")
		row = self.last_log()
		self.assertIn(row.result_status, ("Blocked", "Error"))
		self.assertEqual(row.docstatus_delta, "")

	def test_a_disabled_switch_is_audited_as_blocked(self):
		doc = self.probe(state="Pending Approval")
		self.doc.allow_advance_workflow = 0
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype="ERPNext MCP Settings")
		result = self.advance(doc.name, "Approve")
		self.assertTrue(result["isError"])
		self.assertIn("allow_advance_workflow", result["content"][0]["text"])
		self.assertEqual(self.last_log().result_status, "Blocked")

	def test_available_actions_reflects_the_acting_user(self):
		doc = self.probe(state="Pending Approval", owner=APPROVER)
		self.act_as(SUBMITTER)
		data = self.tool_data("list_available_actions", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertEqual(data["user"], SUBMITTER)
		self.assertEqual(data["available_actions"], [])


class SelfApproval(WorkflowScenario):
	"""The rule Frappe enforces late, and this app enforces early.

	`get_transitions` filters on role and condition only — self-approval is
	checked inside `apply_workflow`. Without the app's own check,
	`list_available_actions` would advertise Approve to the document's own owner
	and `dry_run` would promise it would work.
	"""

	def test_frappe_itself_does_not_filter_self_approval(self):
		"""Pinning the framework behaviour this app compensates for. If a future
		Frappe starts filtering in get_transitions, this fails and the compensation
		can be removed."""
		from frappe.model.workflow import get_transitions

		doc = self.probe(state="Pending Approval", owner=APPROVER)
		frappe.set_user(APPROVER)
		try:
			actions = {row.get("action") for row in get_transitions(self.reread(doc.name))}
		finally:
			frappe.set_user("Administrator")
		self.assertIn(
			"Approve",
			actions,
			"Frappe's get_transitions now filters self-approval — "
			"workflow._self_approval_permitted can be simplified",
		)

	def test_the_owner_is_not_offered_the_action(self):
		doc = self.probe(state="Pending Approval", owner=APPROVER)
		self.act_as(APPROVER)
		data = self.tool_data("list_available_actions", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertNotIn("Approve", data["available_actions"])
		self.assertIn("Approve", [row["action"] for row in data["withheld"]])
		self.assertIn("self-approval", data["withheld"][0]["reason"])

	def test_a_transition_that_allows_self_approval_survives(self):
		"""Reject sets allow_self_approval=1, so the same user keeps it."""
		doc = self.probe(state="Pending Approval", owner=APPROVER, amount=100)
		self.act_as(APPROVER)
		data = self.tool_data("list_available_actions", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertIn("Reject", data["available_actions"])

	def test_advancing_is_refused_cleanly(self):
		doc = self.probe(state="Pending Approval", owner=APPROVER)
		self.act_as(APPROVER)
		result = self.advance(doc.name, "Approve")
		self.assertTrue(result["isError"])
		self.assertIn("self-approval", result["content"][0]["text"])
		fresh = self.reread(doc.name)
		self.assertEqual(fresh.workflow_state, "Pending Approval")
		self.assertEqual(fresh.docstatus, 0)

	def test_the_dry_run_does_not_promise_it_would_work(self):
		"""The defect this whole suite found: without the app's own check, dry_run
		reported would_succeed=true for a transition that throws."""
		doc = self.probe(state="Pending Approval", owner=APPROVER)
		self.act_as(APPROVER)
		data = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve", "dry_run": True},
		)
		self.assertFalse(data["would_succeed"])
		self.assertIn("self-approval", data["refusal_reason"])

	def test_a_different_user_can_approve_the_same_document(self):
		doc = self.probe(state="Pending Approval", owner=SUBMITTER, approval_note="ok")
		self.act_as(APPROVER)
		data = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve"},
		)
		self.assertEqual(data["state_after"], "Approved")


class ConditionFailure(WorkflowScenario):
	"""Reject carries `doc.amount > 0`."""

	def test_a_false_condition_removes_the_action(self):
		doc = self.probe(state="Pending Approval", amount=0)
		data = self.tool_data("list_available_actions", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertNotIn("Reject", data["available_actions"])
		self.assertIn("Approve", data["available_actions"])
		self.assertTrue(data["conditions_evaluated"])

	def test_a_true_condition_keeps_it(self):
		doc = self.probe(state="Pending Approval", amount=250)
		data = self.tool_data("list_available_actions", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertIn("Reject", data["available_actions"])

	def test_advancing_on_a_false_condition_is_refused_with_the_alternatives(self):
		doc = self.probe(state="Pending Approval", amount=0)
		result = self.advance(doc.name, "Reject")
		self.assertTrue(result["isError"])
		message = result["content"][0]["text"]
		self.assertIn("not available", message)
		self.assertIn("Approve", message)
		self.assertEqual(self.reread(doc.name).workflow_state, "Pending Approval")

	def test_the_condition_is_visible_in_the_workflow_listing(self):
		"""So a model can explain *why* an action is unavailable rather than just
		reporting that it is."""
		data = self.tool_data("list_workflows")
		entry = next(w for w in data["workflows"] if w["name"] == PROBE_WORKFLOW)
		reject = next(t for t in entry["transitions"] if t["action"] == "Reject")
		self.assertTrue(reject["has_condition"])
		self.assertEqual(reject["condition"], "doc.amount > 0")


class SubmitHookRaises(WorkflowScenario):
	"""A transition sets docstatus=1 and the document refuses to submit.

	`approval_note` is mandatory only when `docstatus == 1`, so a probe with it
	empty inserts happily and cannot be submitted. That is stock Frappe
	validation on the real submit path — the same place a doctype's own
	`on_submit` or an accounting integrity check would raise.

	What must NOT happen: the workflow state advancing to Approved while the
	document sits at docstatus 0. `apply_workflow` writes the state and submits in
	one transaction, and `registry.dispatch` rolls back on any exception, so the
	document has to come out exactly as it went in.
	"""

	def test_the_transition_fails(self):
		doc = self.probe(state="Pending Approval", approval_note=None)
		result = self.advance(doc.name, "Approve")
		self.assertTrue(result["isError"])

	def test_no_orphaned_workflow_state(self):
		doc = self.probe(state="Pending Approval", approval_note=None)
		self.advance(doc.name, "Approve")
		fresh = self.reread(doc.name)
		self.assertEqual(
			fresh.workflow_state,
			"Pending Approval",
			"the workflow state advanced even though the submit failed",
		)
		self.assertEqual(fresh.docstatus, 0)

	def test_the_failure_is_audited(self):
		doc = self.probe(state="Pending Approval", approval_note=None)
		self.advance(doc.name, "Approve")
		row = self.last_log()
		self.assertEqual(row.result_status, "Error")
		self.assertEqual(row.docstatus_delta, "")

	def test_the_dry_run_could_not_have_predicted_it(self):
		"""Worth stating plainly: dry_run resolves the transition, it does not
		run the document's validation. It answers "is this action available",
		not "will the resulting save succeed"."""
		doc = self.probe(state="Pending Approval", approval_note=None)
		data = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve", "dry_run": True},
		)
		self.assertTrue(data["would_succeed"])
		self.assertTrue(data["would_submit"])

	def test_filling_the_field_lets_it_through(self):
		doc = self.probe(state="Pending Approval", approval_note=None)
		self.advance(doc.name, "Approve")
		frappe.db.set_value(PROBE_DOCTYPE, doc.name, "approval_note", "now supplied")
		data = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve"},
		)
		self.assertEqual(data["docstatus_after"], 1)


class DryRunAgainstRealFrappe(WorkflowScenario):
	def test_it_changes_nothing(self):
		doc = self.probe(state="Pending Approval", approval_note="ok")
		before = self.reread(doc.name)
		self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve", "dry_run": True},
		)
		after = self.reread(doc.name)
		self.assertEqual(after.workflow_state, before.workflow_state)
		self.assertEqual(after.docstatus, before.docstatus)
		self.assertEqual(after.modified, before.modified)

	def test_it_reads_the_target_states_docstatus_from_the_real_workflow(self):
		doc = self.probe(state="Pending Approval", approval_note="ok")
		data = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve", "dry_run": True},
		)
		self.assertEqual(data["would_move_to"], "Approved")
		self.assertTrue(data["would_submit"])
		self.assertEqual(data["would_set_docstatus"], 1)

	def test_a_non_submitting_transition_is_reported_as_such(self):
		doc = self.probe(state="Draft")
		data = self.tool_data(
			"advance_workflow",
			{
				"doctype": PROBE_DOCTYPE,
				"name": doc.name,
				"action": "Submit for Approval",
				"dry_run": True,
			},
		)
		self.assertFalse(data["would_submit"])
		self.assertEqual(data["would_set_docstatus"], 0)

	def test_dry_run_then_execute_is_the_intended_pattern(self):
		doc = self.probe(state="Pending Approval", approval_note="ok")
		preview = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve", "dry_run": True},
		)
		self.assertTrue(preview["would_succeed"])
		executed = self.tool_data(
			"advance_workflow",
			{"doctype": PROBE_DOCTYPE, "name": doc.name, "action": "Approve"},
		)
		self.assertEqual(executed["state_after"], preview["would_move_to"])
		self.assertEqual(executed["docstatus_after"], preview["would_set_docstatus"])


class MultipleWorkflows(WorkflowScenario):
	"""Two Workflows on one DocType.

	Frappe deactivates the others when you save one active — that is the
	behaviour worth pinning, because it is what makes a single-active assumption
	safe. The tools additionally refuse if two are somehow active at once, which
	only a direct database edit can produce.
	"""

	def second_workflow(self, is_active=1):
		doc = frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": SECOND_WORKFLOW,
				"document_type": PROBE_DOCTYPE,
				"is_active": is_active,
				"workflow_state_field": "workflow_state",
				"send_email_alert": 0,
				"states": [
					{"state": "Draft", "doc_status": "0", "allow_edit": SUBMITTER_ROLE},
					{"state": "Approved", "doc_status": "1", "allow_edit": APPROVER_ROLE},
				],
				"transitions": [
					{
						"state": "Draft",
						"action": "Approve",
						"next_state": "Approved",
						"allowed": APPROVER_ROLE,
						"allow_self_approval": 1,
					}
				],
			}
		)
		doc.insert(ignore_permissions=True)
		return doc

	def tearDown(self):
		_drop("Workflow", SECOND_WORKFLOW)
		frappe.db.set_value("Workflow", PROBE_WORKFLOW, "is_active", 1)
		super().tearDown()

	def test_frappe_deactivates_the_previous_one(self):
		self.second_workflow(is_active=1)
		self.assertEqual(frappe.db.get_value("Workflow", PROBE_WORKFLOW, "is_active"), 0)
		self.assertEqual(frappe.db.get_value("Workflow", SECOND_WORKFLOW, "is_active"), 1)

	def test_the_tools_follow_the_active_one(self):
		self.second_workflow(is_active=1)
		doc = self.probe(state="Draft")
		data = self.tool_data("get_workflow_state", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertEqual(data["workflow"], SECOND_WORKFLOW)

	def test_an_inactive_second_workflow_is_ignored(self):
		self.second_workflow(is_active=0)
		doc = self.probe(state="Pending Approval")
		data = self.tool_data("get_workflow_state", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertEqual(data["workflow"], PROBE_WORKFLOW)

	def test_two_active_at_once_is_refused_rather_than_guessed(self):
		"""Only a direct database edit gets here, and guessing on a submitting
		transition would be unrecoverable."""
		self.second_workflow(is_active=0)
		frappe.db.set_value("Workflow", SECOND_WORKFLOW, "is_active", 1)
		frappe.db.set_value("Workflow", PROBE_WORKFLOW, "is_active", 1)
		frappe.clear_cache()
		doc = self.probe(state="Pending Approval")
		result = self.tool("get_workflow_state", {"doctype": PROBE_DOCTYPE, "name": doc.name})
		self.assertTrue(result["isError"])
		self.assertIn("2 Workflows are active", result["content"][0]["text"])
