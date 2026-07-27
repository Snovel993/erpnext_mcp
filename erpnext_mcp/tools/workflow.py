# SPDX-License-Identifier: MIT
"""Workflow tools: what state is this in, who can move it, and move it.

WHY DELEGATE THE HARD PART. Frappe's `frappe.model.workflow` already knows how
to decide whether a transition is available: it evaluates the transition's
`condition` expression against the document, applies the self-approval rule, and
checks the acting user's roles. Reimplementing that here would produce a second
opinion that is right most of the time — and a workflow tool that is right most
of the time is worse than none, because "the AI said I could approve it" is not
a defence. So `list_available_actions` and `advance_workflow` call Frappe's own
functions, and only fall back to a role-only check when the site's version does
not export them. The response always says which path ran.

The fallback deliberately does NOT evaluate transition conditions — it cannot,
safely — so it reports `conditions_evaluated: false` and every condition-bearing
transition is flagged. A caller that sees that flag knows the list is a superset
of what will actually work.

WHAT COUNTS AS "PENDING". A document is pending if it sits in a state that has at
least one outgoing transition. States with no way out are terminal — Approved,
Rejected, Cancelled — and a worklist that includes them is just a list of every
document.
"""

import frappe

from .. import compat
from ..args import as_bool, as_limit, as_str
from ..errors import ToolError
from ..result import ToolResult

#: Where a Workflow stores the state, when it does not say otherwise.
DEFAULT_STATE_FIELD = "workflow_state"


# ── 16. list_workflows ──────────────────────────────────────────────────────
def list_workflows(args: dict) -> ToolResult:
	"""Every Workflow on the site with its states, transitions and roles."""
	names = frappe.db.get_all(
		"Workflow",
		fields=compat.existing_fields(
			"Workflow",
			[
				"name",
				"workflow_name",
				"document_type",
				"is_active",
				"workflow_state_field",
				"send_email_alert",
				"override_status",
			],
		),
		order_by="document_type asc",
	)

	out = []
	for header in names:
		doc = frappe.get_doc("Workflow", header["name"])
		states = _state_rows(doc)
		transitions = _transition_rows(doc)
		outgoing = {row["state"] for row in transitions}
		out.append(
			{
				**header,
				"workflow_state_field": header.get("workflow_state_field") or DEFAULT_STATE_FIELD,
				"states": states,
				"transitions": transitions,
				"terminal_states": sorted({row["state"] for row in states} - outgoing),
				"roles": sorted(
					{row["allowed"] for row in transitions if row.get("allowed")}
					| {row["allow_edit"] for row in states if row.get("allow_edit")}
				),
			}
		)

	data = {
		"workflows": out,
		"count": len(out),
		"active_count": sum(1 for row in out if row.get("is_active")),
		"note": ("terminal_states have no outgoing transition — a document there is finished, not waiting."),
	}
	return ToolResult(data, f"{len(out)} workflow(s)")


# ── 17. get_workflow_state ──────────────────────────────────────────────────
def get_workflow_state(args: dict) -> ToolResult:
	"""Where one document sits in its workflow, and where it could go next.

	Reports transitions by role rather than by user — "who could move this" — and
	leaves "can *I* move this" to `list_available_actions`, which is the question
	that needs the condition expressions evaluated.
	"""
	doctype = as_str(args, "doctype", required=True)
	name = as_str(args, "name", required=True)
	doc, workflow = _document_and_workflow(doctype, name)
	state_field = _state_field(workflow)
	current = doc.get(state_field)

	transitions = [row for row in _transition_rows(workflow) if row["state"] == current]
	state_row = next((row for row in _state_rows(workflow) if row["state"] == current), None)

	data = {
		"doctype": doctype,
		"name": name,
		"workflow": workflow.name,
		"workflow_state_field": state_field,
		"current_state": current,
		"current_state_detail": state_row,
		"docstatus": int(doc.get("docstatus") or 0),
		"docstatus_label": _docstatus_label(doc.get("docstatus")),
		"next_transitions": transitions,
		"is_terminal": not transitions,
	}
	if current is None:
		data["note"] = (
			f"{doctype} {name} has no value in {state_field!r}. Documents created "
			"before the workflow was added sit outside it until they are saved."
		)
	return ToolResult(
		data,
		f"{doctype} {name}: state {current!r}, {len(transitions)} transition(s) out",
	)


# ── 18. list_pending_approvals ──────────────────────────────────────────────
def list_pending_approvals(args: dict) -> ToolResult:
	"""Documents parked in a state something still has to be done to.

	With `user`, narrows to states that user's roles can actually act on — which
	is the worklist question people mean when they ask "what is waiting on me".
	"""
	user = as_str(args, "user")
	only_workflow = as_str(args, "workflow")
	limit = as_limit(args)

	if user and not frappe.db.exists("User", user):
		raise ToolError(f"no User named {user!r}")
	user_roles = set(frappe.get_roles(user)) if user else set()

	filters = {"is_active": 1}
	if only_workflow:
		filters["name"] = only_workflow
	workflows = frappe.db.get_all("Workflow", filters=filters, pluck="name")
	if only_workflow and not workflows:
		raise ToolError(f"no active Workflow named {only_workflow!r}")

	groups, total = [], 0
	for workflow_name in workflows:
		workflow = frappe.get_doc("Workflow", workflow_name)
		doctype = workflow.document_type
		if not compat.doctype_exists(doctype):
			continue
		state_field = _state_field(workflow)
		if not compat.has_field(doctype, state_field):
			continue

		by_state = {}
		for row in _transition_rows(workflow):
			by_state.setdefault(row["state"], []).append(row)

		for state, transitions in sorted(by_state.items()):
			allowed_roles = sorted({t["allowed"] for t in transitions if t.get("allowed")})
			if user and not (user_roles & set(allowed_roles)):
				continue
			docs = frappe.db.get_all(
				doctype,
				filters={state_field: state, "docstatus": ("<", 2)},
				fields=compat.existing_fields(doctype, ["name", "owner", "modified", "docstatus"]),
				order_by="modified asc",
				limit=limit,
			)
			if not docs:
				continue
			total += len(docs)
			groups.append(
				{
					"workflow": workflow_name,
					"doctype": doctype,
					"state": state,
					"actions": sorted({t["action"] for t in transitions}),
					"allowed_roles": allowed_roles,
					"count": len(docs),
					"truncated": len(docs) == limit,
					"documents": docs,
				}
			)

	data = {
		"pending": groups,
		"group_count": len(groups),
		"document_count": total,
		"user": user or None,
		"user_roles": sorted(user_roles) if user else None,
		"limit_per_state": limit,
		"note": (
			"Only states with an outgoing transition are listed; terminal states "
			"are finished, not pending. Counts are per state and capped at "
			"limit_per_state."
		),
	}
	scope = f" for {user}" if user else ""
	return ToolResult(data, f"{total} document(s) pending across {len(groups)} state(s){scope}")


# ── 19. list_available_actions ──────────────────────────────────────────────
def list_available_actions(args: dict) -> ToolResult:
	"""What the *current* user can do to this document right now.

	"Current user" is whoever the MCP System User is configured to be — see
	docs/security.md — not the human at the other end of the chat. That is the
	honest answer, because it is the user the transition would actually run as.
	"""
	doctype = as_str(args, "doctype", required=True)
	name = as_str(args, "name", required=True)
	doc, workflow = _document_and_workflow(doctype, name)
	current = doc.get(_state_field(workflow))

	transitions, via, conditions_evaluated, withheld = _available_transitions(doc, workflow, current)
	data = {
		"doctype": doctype,
		"name": name,
		"workflow": workflow.name,
		"user": frappe.session.user,
		"user_roles": sorted(frappe.get_roles(frappe.session.user) or []),
		"current_state": current,
		"available_actions": [row.get("action") for row in transitions],
		"transitions": transitions,
		"withheld": withheld,
		"resolved_via": via,
		"conditions_evaluated": conditions_evaluated,
	}
	if withheld:
		data["withheld_note"] = (
			"These transitions match your roles and their conditions, but "
			"apply_workflow would refuse them. Frappe checks self-approval at "
			"execution time; this app checks it up front so the answer is honest."
		)
	if not conditions_evaluated:
		data["warning"] = (
			"This site's Frappe does not export get_transitions, so transition "
			"conditions were NOT evaluated and this list is a superset: an action "
			"here may still be refused. Transitions carrying a condition are "
			"flagged with has_condition."
		)
	return ToolResult(
		data,
		f"{doctype} {name}: {len(transitions)} action(s) available to "
		f"{frappe.session.user} from state {current!r}",
	)


# ── 20. advance_workflow (MUTATING) ─────────────────────────────────────────
def advance_workflow(args: dict) -> ToolResult:
	"""Take a workflow action on a document, or say what one would do.

	Runs `frappe.model.workflow.apply_workflow`, which is the same code path the
	Desk button uses — so the state change, any `update_field` the state sets, the
	docstatus change and the resulting submit or cancel all happen exactly as they
	would for a human. There is no fallback: if this site's Frappe does not export
	`apply_workflow`, the tool refuses rather than hand-rolling a state write.

	WITH `dry_run=True` nothing is executed. The tool resolves the transition,
	reads the target state's `doc_status` and reports what would happen — most
	importantly whether the document would be SUBMITTED, which is the fact that
	makes this tool dangerous and which is invisible from the action's name. A
	client is expected to dry-run, show a human, and only then execute.

	A dry run never raises for an unavailable action. "It would be refused, and
	here is why" is the answer to the question, not a failure to answer it — so
	that case comes back as data with `would_succeed: false`. Everything else
	(unknown document, no workflow, two active workflows) still raises, because
	those mean the question itself was malformed.
	"""
	doctype = as_str(args, "doctype", required=True)
	name = as_str(args, "name", required=True)
	action = as_str(args, "action", required=True)
	dry_run = bool(as_bool(args, "dry_run", False))
	doc, workflow = _document_and_workflow(doctype, name)
	state_field = _state_field(workflow)
	before_state = doc.get(state_field)
	before_docstatus = int(doc.get("docstatus") or 0)

	transitions, _via, conditions_evaluated, withheld = _available_transitions(doc, workflow, before_state)
	available = [row.get("action") for row in transitions]
	refusal = None
	if action not in available:
		blocked = next((row for row in withheld if row["action"] == action), None)
		refusal = (
			f"{action!r} is not available to {frappe.session.user} on {doctype} "
			f"{name} in state {before_state!r}. Available: "
			f"{', '.join(available) or '<none>'}."
			+ (f" {action!r} was withheld because {blocked['reason']}." if blocked else "")
			+ (
				""
				if conditions_evaluated
				else " (Transition conditions could not be evaluated on this site.)"
			)
		)

	if dry_run:
		return _dry_run_result(
			doctype=doctype,
			name=name,
			workflow=workflow,
			action=action,
			before_state=before_state,
			before_docstatus=before_docstatus,
			available=available,
			refusal=refusal,
			conditions_evaluated=conditions_evaluated,
		)

	apply_workflow = _workflow_attr("apply_workflow")
	if apply_workflow is None:
		raise ToolError(
			"this site's Frappe does not export frappe.model.workflow."
			"apply_workflow, so a workflow action cannot be taken safely. "
			"Use the Desk."
		)
	if refusal:
		raise ToolError(refusal)

	updated = apply_workflow(doc, action)
	after_state = (updated or doc).get(state_field)
	after_docstatus = int((updated or doc).get("docstatus") or 0)

	data = {
		"doctype": doctype,
		"name": name,
		"workflow": workflow.name,
		"action": action,
		"user": frappe.session.user,
		"state_before": before_state,
		"state_after": after_state,
		"docstatus_before": before_docstatus,
		"docstatus_after": after_docstatus,
		"docstatus_label": _docstatus_label(after_docstatus),
	}
	delta = (
		f"{before_docstatus} → {after_docstatus} ({_docstatus_label(after_docstatus)})"
		if before_docstatus != after_docstatus
		else ""
	)
	return ToolResult(
		data,
		f"{action} on {doctype} {name}: {before_state!r} → {after_state!r} as {frappe.session.user}",
		docstatus_delta=delta,
	)


# ── dry run ─────────────────────────────────────────────────────────────────
def _dry_run_result(
	*,
	doctype,
	name,
	workflow,
	action,
	before_state,
	before_docstatus,
	available,
	refusal,
	conditions_evaluated,
) -> ToolResult:
	"""What the transition would do, without doing any of it."""
	transition = next(
		(
			row
			for row in _transition_rows(workflow)
			if row["state"] == before_state and row["action"] == action
		),
		None,
	)
	next_state = transition["next_state"] if transition else None
	target = next((row for row in _state_rows(workflow) if row.get("state") == next_state), {})
	after_docstatus = int(target.get("doc_status") or 0) if transition else before_docstatus

	effects = []
	if transition:
		effects.append(f"workflow_state: {before_state!r} → {next_state!r}")
		if target.get("update_field"):
			effects.append(f"sets {target['update_field']} = {target.get('update_value')!r}")
		if after_docstatus == 1 and before_docstatus == 0:
			effects.append(
				"SUBMITS the document (target state has doc_status 1) — for a "
				"Journal Entry this writes GL Entries and moves balances"
			)
		elif after_docstatus == 2 and before_docstatus == 1:
			effects.append(
				"CANCELS the document (target state has doc_status 2) — this writes "
				"reversing entries for a submitted accounting document"
			)
		elif after_docstatus == before_docstatus:
			effects.append(f"leaves docstatus at {before_docstatus}")

	data = {
		"dry_run": True,
		"executed": False,
		"doctype": doctype,
		"name": name,
		"workflow": workflow.name,
		"action": action,
		"user": frappe.session.user,
		"current_state": before_state,
		"current_docstatus": before_docstatus,
		"would_succeed": refusal is None,
		"would_move_to": next_state,
		"would_set_docstatus": after_docstatus,
		"would_submit": bool(transition) and after_docstatus == 1 and before_docstatus != 1,
		"would_cancel": bool(transition) and after_docstatus == 2 and before_docstatus != 2,
		"effects": effects,
		"available_actions": available,
		"conditions_evaluated": conditions_evaluated,
		"transition": transition,
		"refusal_reason": refusal,
		"next_step": (
			"Call again with dry_run=false to execute."
			if refusal is None
			else "This action would be refused. Nothing to execute."
		),
	}
	verdict = "would succeed" if refusal is None else "would be REFUSED"
	return ToolResult(
		data,
		f"dry run: {action} on {doctype} {name} {verdict} "
		f"({before_state!r} → {next_state!r}"
		f"{', submits the document' if data['would_submit'] else ''}"
		f"{', cancels the document' if data['would_cancel'] else ''})",
	)


# ── shared ──────────────────────────────────────────────────────────────────
def _document_and_workflow(doctype: str, name: str):
	"""The document and the active Workflow governing it, or a clear ToolError."""
	if not compat.doctype_exists(doctype):
		raise ToolError(f"no DocType named {doctype!r} on this site")
	if not frappe.db.exists(doctype, name):
		raise ToolError(f"no {doctype} named {name!r}")
	active = sorted(
		frappe.db.get_all("Workflow", filters={"document_type": doctype, "is_active": 1}, pluck="name")
	)
	if not active:
		governed = sorted(frappe.db.get_all("Workflow", filters={"is_active": 1}, pluck="document_type"))
		raise ToolError(
			f"no active Workflow governs {doctype}. Workflows are active for: "
			f"{', '.join(governed) or '<none>'}."
		)
	if len(active) > 1:
		# Frappe deactivates the others when a Workflow is saved active, so this
		# state only arises from a direct database edit or a half-finished
		# fixture import. There is no defined answer to "which workflow governs
		# this document", and picking one silently could advance the wrong one —
		# on a submitting transition that is unrecoverable. Stop instead.
		raise ToolError(
			f"{len(active)} Workflows are active for {doctype} at once: "
			f"{', '.join(active)}. Frappe deactivates the others when you save one "
			"active, so this site has been edited around the doctype. Which one "
			"governs a document is undefined — deactivate all but one before using "
			"the workflow tools. list_workflows shows them all."
		)
	return frappe.get_doc(doctype, name), frappe.get_doc("Workflow", active[0])


def _state_field(workflow) -> str:
	return workflow.get("workflow_state_field") or DEFAULT_STATE_FIELD


def _state_rows(workflow) -> list[dict]:
	fields = (
		"state",
		"doc_status",
		"allow_edit",
		"update_field",
		"update_value",
		"is_optional_state",
		"message",
	)
	return [
		{key: row.get(key) for key in fields if row.get(key) is not None}
		for row in (workflow.get("states") or [])
	]


def _transition_rows(workflow) -> list[dict]:
	out = []
	for row in workflow.get("transitions") or []:
		condition = row.get("condition")
		out.append(
			{
				"state": row.get("state"),
				"action": row.get("action"),
				"next_state": row.get("next_state"),
				"allowed": row.get("allowed"),
				"allow_self_approval": bool(row.get("allow_self_approval")),
				# The expression itself is site configuration, not a secret, and a
				# model that can see it can explain why an action is unavailable.
				"has_condition": bool(condition),
				"condition": condition or None,
			}
		)
	return out


def _available_transitions(doc, workflow, current_state):
	"""(transitions, how_they_were_resolved, whether_conditions_were_evaluated).

	Withheld transitions are dropped here and reported separately by the caller.
	"""
	rows, via, conditions_evaluated = _resolved_transitions(doc, workflow, current_state)
	allowed, withheld = [], []
	for row in rows:
		if _self_approval_permitted(doc, row):
			allowed.append(row)
		else:
			withheld.append(
				{
					"action": row.get("action"),
					"next_state": row.get("next_state"),
					"reason": (
						"self-approval is not permitted on this transition and you are "
						f"the document's owner ({doc.get('owner')})"
					),
				}
			)
	return allowed, via, conditions_evaluated, withheld


def _resolved_transitions(doc, workflow, current_state):
	"""Frappe's own answer where available, a role check where it is not."""
	get_transitions = _workflow_attr("get_transitions")
	if get_transitions is not None:
		try:
			rows = get_transitions(doc) or []
			return (
				[dict(row) for row in rows],
				"frappe.model.workflow.get_transitions",
				True,
			)
		except Exception as exc:
			# A condition expression that raises is a site bug, not ours; report
			# it rather than silently falling back to a laxer answer.
			raise ToolError(
				f"evaluating this workflow's transitions failed: {type(exc).__name__}: {exc}"
			) from exc

	roles = set(frappe.get_roles(frappe.session.user) or [])
	rows = [
		row
		for row in _transition_rows(workflow)
		if row["state"] == current_state and (not row["allowed"] or row["allowed"] in roles)
	]
	return rows, "role check only (this app)", False


def _self_approval_permitted(doc, transition) -> bool:
	"""Frappe's self-approval rule, applied here because it is applied late there.

	`frappe.model.workflow.get_transitions` does NOT check self-approval — it
	filters on role and condition only. The rule is enforced inside
	`apply_workflow`, which throws "Self approval is not allowed" at execution
	time. Verified against a real Workflow in
	`erpnext_mcp/tests/test_workflow_scenarios.py`.

	That is fine for the Desk, which finds out when the user clicks. It is not
	fine here: `list_available_actions` would advertise an action the user cannot
	take, and — worse — `dry_run` would report `would_succeed: true` for a
	transition destined to throw, which is precisely the promise dry_run exists
	to make. So the same rule is applied up front, in the same form Frappe
	applies it:

	    user is Administrator, OR the transition allows self-approval,
	    OR the user is not the document's owner.
	"""
	if frappe.session.user == "Administrator":
		return True
	if transition.get("allow_self_approval"):
		return True
	return frappe.session.user != doc.get("owner")


def _workflow_attr(name: str):
	"""`frappe.model.workflow.<name>`, or None where the version lacks it."""
	try:
		from frappe.model import workflow as workflow_api
	except Exception:
		return None
	return getattr(workflow_api, name, None)


def _docstatus_label(docstatus) -> str:
	return {0: "draft", 1: "submitted", 2: "cancelled"}.get(int(docstatus or 0), "unknown")
