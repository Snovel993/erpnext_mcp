# SPDX-License-Identifier: MIT
"""Farm Task Dispatch: the pool, the board, and the bridge from the calendar.

Sprint 7 built a compliance calendar that could tell an operation fifty-four
things were wrong. It could not tell anybody to go and fix one. This is the half
that can — and it is built compliance-native from the first field rather than
having compliance added to it afterwards, which is a distinction with three
concrete consequences:

  1. **A task cannot be created without saying what closing it requires.**
     `evidence_required` is mandatory on the doctype. There is no path to a task
     that somebody can close by saying they did it.
  2. **Completing a task WRITES THE COMPLIANCE RECORD.** Not a status change with
     a note about one — the actual Housing Inspection, Detector Test or Water
     Test, with the photographs attached to it, which then moves the register
     forward and lets the alert dismiss itself on the next sweep.
  3. **Rejection is a first-class state with a mandatory reason.** "Nobody got to
     it and dispatch never followed up" is the answer that cannot be defended.
     `reject_farm_task` turns it into "the ladder is broken and I could not reach
     the detector", which is a fact somebody can act on, on a record that stays.

DUAL MODE, BECAUSE ONE MODE IS WRONG FOR HALF THE WORK. A habitability walk is
general labour: anybody with camp maintenance skills can take it from the pool,
and making a foreman assign fifty-four of them by hand is how fifty-four of them
do not happen. Fitting a CO detector, spraying under an applicator licence, or
anything where the named holder matters is dispatched: somebody is SENT, by name,
and the record says who sent them. `dispatch_mode` is the field, and `Either`
opens both doors for the majority of work where both are fine.

THE CONCURRENT-CLAIM LIMIT IS A HOARDING LIMIT AND NOT A PRODUCTIVITY ONE. Three
at once is a morning: enough to plan a trip round the camp, few enough that
nobody can pull the whole pool onto their own name and leave a board that looks
worked. Completing one frees a slot in the same instant, so it never stands
between somebody and their next job — it only stands between them and their
fourth simultaneous one.

WHAT `complete_farm_task` REFUSES, AND WHY EACH REFUSAL EARNED ITS PLACE:

  * a submission that does not meet the task's evidence contract — the entire
    reason the contract is mandatory at creation;
  * a worker who is not the one holding the task — a completion filed by
    somebody who was not there is not chain of custody, it is a rumour;
  * a task nobody has claimed, and a task already finished.

It does NOT refuse a completion whose findings are alarming, a completion that
took four times the estimate, or a completion of work that is months overdue.
Every one of those is a fact worth recording, and a tool that refused to record
it would guarantee the record stayed empty.

AWAITING-REVIEW IS NOT A SECOND APPROVAL STEP. A completion lands there when the
compliance record it produced found something — a water stain, a dead detector, a
coliform count. The work IS done and the register IS updated; what needs a human
is the finding, and the Critical alert raised against the record is how they hear
about it. A completion that found nothing goes straight to Completed, because
routing clean work through a review queue is how a review queue stops being read.
"""

from __future__ import annotations

import json

import frappe

from .. import alerts, compat, completions, records, sessions
from ..args import as_bool, as_choice, as_int, as_limit, as_str, resolve_company
from ..erpnext_mcp.doctype.farm_task.farm_task import (
	AVAILABLE,
	AWAITING_REVIEW,
	CANCELLED,
	CLAIMED,
	COMPLETED,
	DISPATCH_DISPATCHED,
	DRAFT,
	EVIDENCE_KEYS,
	IN_PROGRESS,
	MAX_CONCURRENT_CLAIMS,
	ORIGIN_COMPLIANCE_RULE,
	ORIGIN_FIELD_REPORTED,
	ORIGIN_FOREMAN_DISPATCH,
	ORIGINS,
	REJECTED,
	SELF_PICKABLE,
	STATES,
	TERMINAL_STATES,
	evidence_contract,
	parse_json_object,
)
from ..erpnext_mcp.doctype.farm_task_assignment.farm_task_assignment import (
	concurrent_claims,
	live_assignment,
)
from ..errors import ToolError
from ..result import ToolResult
from . import inspections
from .housing import EMPLOYEE, hr_installed

FARM_TASK = "Farm Task"
FARM_TASK_ASSIGNMENT = "Farm Task Assignment"
ALERT = alerts.ALERT_DOCTYPE

BOARD_CAP = 500

#: Most tasks one `generate_tasks_from_compliance_alerts` call will create. The
#: same number the alert engine caps a single rule at, for the same reason: past
#: it, something is firing on a field that is empty everywhere rather than stale
#: on a few, and turning that into five hundred dispatchable tasks would bury the
#: dozen that matter.
GENERATE_CAP = 500

_TASK_FIELDS = (
	"name",
	"task_name",
	"task_type",
	"state",
	"urgency",
	"company",
	"location_doctype",
	"location",
	"skill_required",
	"dispatch_mode",
	"assigned_to",
	"assigned_to_name",
	"estimated_duration_minutes",
	"source_alert",
	"source_workorder",
	"evidence_required",
	"creates_record",
	"creates_record_data",
	"produced_record",
	"notes",
	"creation",
	"modified",
	"owner",
)

_ASSIGNMENT_FIELDS = (
	"name",
	"task",
	"task_name",
	"assigned_to",
	"assigned_to_name",
	"state",
	"company",
	"dispatched_by_foreman",
	"claimed_at",
	"started_at",
	"completed_at",
	"actual_duration_minutes",
	"completion_narrative",
	"findings_text",
	"witness",
	"farm_location_gps",
	"rejection_reason",
	"signature_file",
	"produced_record",
	"visit_id",
	"completion_signature",
	"creation",
	"owner",
)


# ── shared ──────────────────────────────────────────────────────────────────
def _require(doctype: str = FARM_TASK) -> None:
	compat.require_doctype(
		doctype,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _company(args: dict) -> str | None:
	return resolve_company(as_str(args, "company"), required=False)


def task_row(task: str) -> dict:
	task = (task or "").strip()
	if not task:
		raise ToolError("task is required (a Farm Task docname such as 'FT-2026-07-00012').")
	if not frappe.db.exists(FARM_TASK, task):
		raise ToolError(
			f"no Farm Task called {task!r} on this site. list_dispatch_board has everything that is "
			"open. Nothing was changed."
		)
	return dict(
		frappe.db.get_value(FARM_TASK, task, compat.existing_fields(FARM_TASK, _TASK_FIELDS), as_dict=True)
		or {}
	)


def _describe_task(row: dict) -> dict:
	contract = evidence_contract(row.get("evidence_required"))
	out = {
		"name": row.get("name"),
		"task_name": row.get("task_name"),
		"task_type": row.get("task_type"),
		"state": row.get("state") or DRAFT,
		"urgency": row.get("urgency") or "Normal",
		"origin": row.get("origin") or ORIGIN_COMPLIANCE_RULE,
		"company": row.get("company") or None,
		"location_doctype": row.get("location_doctype") or None,
		"location": row.get("location") or None,
		"skill_required": row.get("skill_required") or None,
		"dispatch_mode": row.get("dispatch_mode") or "Either",
		"assigned_to": row.get("assigned_to") or None,
		"assigned_to_name": row.get("assigned_to_name") or row.get("assigned_to") or None,
		"estimated_duration_minutes": int(row.get("estimated_duration_minutes") or 0) or None,
		"source_alert": row.get("source_alert") or None,
		"source_workorder": row.get("source_workorder") or None,
		"evidence_required": contract,
		"evidence_required_summary": _contract_sentence(contract),
		"creates_record": row.get("creates_record") or None,
		"creates_record_data": _safe_json(row.get("creates_record_data")),
		"produced_record": row.get("produced_record") or None,
		"notes": row.get("notes") or None,
		"open": (row.get("state") or DRAFT) not in TERMINAL_STATES,
		"self_pickable": (row.get("dispatch_mode") or "Either") in SELF_PICKABLE,
	}
	if row.get("reported_by"):
		out["reported_by"] = row["reported_by"]
	if row.get("reported_at"):
		out["reported_at"] = str(row["reported_at"])
	if row.get("report_photo"):
		out["report_photo"] = row["report_photo"]
	return out


def _safe_json(raw) -> dict:
	try:
		value = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
	except Exception:
		return {}
	return value if isinstance(value, dict) else {}


def _contract_sentence(contract: dict) -> str:
	wanted = [key for key, value in sorted(contract.items()) if value]
	if not wanted:
		return "nothing (which should not be possible — evidence_required is mandatory at creation)"
	return "; ".join(f"{key}: {EVIDENCE_KEYS[key]}" for key in wanted if key in EVIDENCE_KEYS)


def _describe_assignment(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"task": row.get("task"),
		"task_name": row.get("task_name"),
		"assigned_to": row.get("assigned_to"),
		"assigned_to_name": row.get("assigned_to_name") or row.get("assigned_to"),
		"state": row.get("state"),
		"company": row.get("company") or None,
		"dispatched_by_foreman": compat.checked(row.get("dispatched_by_foreman")),
		"claimed_at": str(row.get("claimed_at") or "") or None,
		"started_at": str(row.get("started_at") or "") or None,
		"completed_at": str(row.get("completed_at") or "") or None,
		"actual_duration_minutes": int(row.get("actual_duration_minutes") or 0) or None,
		"completion_narrative": row.get("completion_narrative") or None,
		"findings_text": row.get("findings_text") or None,
		"witness": row.get("witness") or None,
		"farm_location_gps": row.get("farm_location_gps") or None,
		"rejection_reason": row.get("rejection_reason") or None,
		"signature_file": row.get("signature_file") or None,
		"produced_record": row.get("produced_record") or None,
		# v0.20.1. `visit_id` is reported so a client can PROVE THE ROUND TRIP —
		# the handset mints it, and reading it back off the record is the only
		# way the app can tell "the server has my visit" from "the server has my
		# completion and dropped the grouping". The signature is reported once at
		# the TOP LEVEL of a completion's result rather than on every assignment
		# any tool happens to describe: it is a fact about one submission, not a
		# property of the assignment worth repeating on a dispatch board.
		"visit_id": row.get("visit_id") or None,
	}


def _worker(args: dict, key: str = "worker_id", required: bool = True) -> str:
	"""A worker id, validated against the Employee register where there is one."""
	worker = as_str(args, key) or as_str(args, "assigned_to") or as_str(args, "employee")
	if not worker:
		if required:
			raise ToolError(
				f"{key} is required. A dispatch record with no name on it answers none of the "
				"questions it exists to answer."
			)
		return ""
	if hr_installed() and not frappe.db.exists(EMPLOYEE, worker):
		raise ToolError(
			f"no Employee {worker!r} on this site. A dispatch board naming somebody payroll has "
			"never heard of has already drifted from the operation it describes. list_employees "
			"has the register. Nothing was changed."
		)
	return worker


def _worker_name(worker: str, given: str = "") -> str:
	if given:
		return given
	if worker and compat.doctype_exists(EMPLOYEE):
		return str(frappe.db.get_value(EMPLOYEE, worker, "employee_name") or "") or worker
	return worker


def _assignment_row(name: str) -> dict:
	return dict(
		frappe.db.get_value(
			FARM_TASK_ASSIGNMENT,
			name,
			compat.existing_fields(FARM_TASK_ASSIGNMENT, _ASSIGNMENT_FIELDS),
			as_dict=True,
		)
		or {}
	)


def _last_completion(task: str) -> str:
	"""The most recent Completed assignment on this task, or "".

	v0.20.1, AND ONLY THE COMPLETION PATH ASKS FOR IT. A retry that names just
	the task finds no LIVE assignment, because the completion it is retrying is
	what ended the live one — so without this the second attempt is refused with
	"nobody is holding it", which is a worse answer than "already completed" and
	the same lost work. Newest first, because a task claimed, completed, reopened
	and completed again has two, and the one a client is retrying is the last.
	"""
	rows = (
		frappe.db.get_all(
			FARM_TASK_ASSIGNMENT,
			filters={"task": task, "state": COMPLETED},
			pluck="name",
			order_by="modified desc",
			limit=1,
		)
		or []
	)
	return str(rows[0]) if rows else ""


def _assignment_for(args: dict, task_name: str = "", or_completed: bool = False) -> dict:
	"""The live assignment named by `assignment`, or inferred from the task.

	`or_completed` falls back to the newest finished assignment where nothing is
	live. ONLY `complete_farm_task` passes it, and only so that a re-sent
	completion reaches the signature check instead of a refusal about an empty
	board — starting or rejecting a task that is already finished is a genuine
	mistake and still says so.
	"""
	explicit = as_str(args, "assignment") or as_str(args, "task_assignment")
	if explicit:
		if not frappe.db.exists(FARM_TASK_ASSIGNMENT, explicit):
			raise ToolError(f"no Farm Task Assignment called {explicit!r} on this site. Nothing was changed.")
		return _assignment_row(explicit)

	task = task_name or as_str(args, "task", required=True)
	name = live_assignment(task) or (_last_completion(task) if or_completed else "")
	if not name:
		raise ToolError(
			f"{task} has nobody holding it, so there is nothing to start, complete or reject. It has "
			"to be claimed with claim_farm_task or dispatched with assign_farm_task first. Nothing "
			"was changed."
		)
	return _assignment_row(name)


def _set_task_state(task: str, state: str, **fields) -> None:
	doc = frappe.get_doc(FARM_TASK, task)
	doc.state = state
	for key, value in fields.items():
		doc.set(key, value)
	doc.save(ignore_permissions=True)


# ── 1. create_farm_task ─────────────────────────────────────────────────────
def create_farm_task(args: dict) -> ToolResult:
	"""Raise one piece of work, with the evidence closing it requires stated up front."""
	_require()
	company = _company(args)

	location_doctype = as_str(args, "location_doctype")
	location = as_str(args, "location")
	if location and not location_doctype:
		raise ToolError(
			"location was given with no location_doctype, so nothing can resolve it. Pass the "
			"register it is in: 'Housing Unit', 'Field', 'Irrigation Zone' or 'Parcel'. Nothing "
			"was created."
		)
	if location_doctype and location:
		if not compat.doctype_exists(location_doctype):
			raise ToolError(
				f"this site has no {location_doctype!r} DocType, so {location!r} cannot be resolved. "
				"Nothing was created."
			)
		if not frappe.db.exists(location_doctype, location):
			raise ToolError(
				f"no {location_doctype} called {location!r} on this site. A task about a place that "
				"does not exist cannot be routed to anybody stood in it. Nothing was created."
			)

	creates_record = as_str(args, "creates_record")
	if creates_record and not compat.doctype_exists(creates_record):
		raise ToolError(
			f"this task promises to produce a {creates_record!r} and this site does not have that "
			"DocType. A task promising a record nobody can write is a promise that fails in front "
			f"of a worker stood in a cabin. Known builders: {', '.join(sorted(inspections.BUILDERS))}. "
			"Nothing was created."
		)

	source_alert = as_str(args, "source_alert")
	if source_alert:
		if not compat.doctype_exists(ALERT) or not frappe.db.exists(ALERT, source_alert):
			raise ToolError(f"no Compliance Alert called {source_alert!r} on this site. Nothing was created.")
		existing = frappe.db.get_value(FARM_TASK, {"source_alert": source_alert}, "name")
		if existing:
			raise ToolError(
				f"Farm Task {existing} already answers alert {source_alert}. One task per alert — "
				"two people sent to walk the same cabin is the thing the source link exists to "
				"prevent. Nothing was created."
			)

	worker = _worker(args, "assigned_to", required=False)
	draft = as_bool(args, "draft", False)

	doc = frappe.new_doc(FARM_TASK)
	doc.task_name = as_str(args, "task_name", required=True)
	doc.task_type = as_choice(FARM_TASK, "task_type", as_str(args, "task_type", required=True), "task_type")
	doc.urgency = as_choice(FARM_TASK, "urgency", as_str(args, "urgency") or "Normal", "urgency")
	doc.dispatch_mode = as_choice(
		FARM_TASK, "dispatch_mode", as_str(args, "dispatch_mode") or "Either", "dispatch_mode"
	)
	doc.company = company
	doc.location_doctype = location_doctype or None
	doc.location = location or None
	doc.skill_required = as_str(args, "skill_required")
	doc.estimated_duration_minutes = as_int(args, "estimated_duration_minutes") or 0
	doc.source_alert = source_alert or None
	doc.source_workorder = as_str(args, "source_workorder")
	doc.creates_record = creates_record
	doc.creates_record_data = json.dumps(
		parse_json_object(args.get("creates_record_data"), "creates_record_data")
	)
	doc.notes = as_str(args, "notes")
	doc.evidence_required = json.dumps(_evidence_argument(args))
	doc.state = DRAFT if draft else AVAILABLE
	if worker:
		doc.assigned_to = worker
		doc.assigned_to_name = _worker_name(worker, as_str(args, "assigned_to_name"))
		doc.state = DRAFT if draft else CLAIMED
	doc.insert(ignore_permissions=True)

	assignment = None
	if worker and not draft:
		assignment = _open_assignment(doc, worker, doc.assigned_to_name, dispatched=True)

	described = _describe_task(dict(doc.as_dict()))
	warnings = []
	if creates_record and creates_record not in inspections.BUILDERS:
		warnings.append(
			f"{creates_record} exists on this site but erpnext_mcp has no builder for it, so "
			"completing this task will file the evidence against the assignment and report that it "
			f"could not produce the record itself. The three it can build are: "
			f"{', '.join(sorted(inspections.BUILDERS))}."
		)
	if not location:
		warnings.append(
			"This task names no location, so it cannot be routed to whoever is already stood in the "
			"right place. Legitimate for desk work — a certificate renewal happens at a desk."
		)
	if described["dispatch_mode"] == DISPATCH_DISPATCHED and not worker:
		warnings.append(
			"Dispatch mode is Dispatched and nobody is assigned, so this task will sit in Available "
			"and no worker can claim it. Somebody has to be sent with assign_farm_task."
		)

	data = {**described, "assignment": assignment}
	if warnings:
		data["warnings"] = warnings
	return ToolResult(
		data=data,
		summary=(
			f"raised farm task {doc.name} ({doc.task_type}, {doc.urgency}, {doc.state})"
			+ (f" assigned to {doc.assigned_to_name}" if worker else "")
		),
		docstatus_delta="none → 0 (created)",
	)


def _evidence_argument(args: dict) -> dict:
	"""The evidence contract off the arguments, refused early and by name.

	The controller refuses it too — this is the second door, and it exists so the
	refusal a caller sees names the ARGUMENT rather than the field, and arrives
	before anything has been inserted.
	"""
	from ..erpnext_mcp.doctype.farm_task.farm_task import parse_evidence_required

	raw = args.get("evidence_required")
	if raw in (None, ""):
		raise ToolError(
			"evidence_required is required and this is the point of the whole doctype. Say what "
			"closing this task obliges somebody to produce, as JSON — for example "
			'{"photos": true, "signature": true, "findings_text": true}. The keys are: '
			f"{', '.join(sorted(EVIDENCE_KEYS))}. A task that requires no evidence is a task that "
			"gets closed with a tick in a box, and a tick in a box is what an auditor is trained "
			"to disbelieve. Nothing was created."
		)
	return parse_evidence_required(raw)


def _open_assignment(task_doc, worker: str, worker_name: str, dispatched: bool) -> dict:
	assignment = frappe.new_doc(FARM_TASK_ASSIGNMENT)
	assignment.task = task_doc.name
	assignment.task_name = task_doc.task_name
	assignment.company = task_doc.company
	assignment.assigned_to = worker
	assignment.assigned_to_name = worker_name
	assignment.state = CLAIMED
	assignment.dispatched_by_foreman = 1 if dispatched else 0
	assignment.claimed_at = frappe.utils.now()
	assignment.insert(ignore_permissions=True)
	return _describe_assignment(dict(assignment.as_dict()))


# ── 2. assign_farm_task ─────────────────────────────────────────────────────
def assign_farm_task(args: dict) -> ToolResult:
	"""Send one named person to one task. The foreman's half of the dual mode."""
	_require()
	row = task_row(as_str(args, "task", required=True))
	worker = _worker(args, "assigned_to")
	worker_name = _worker_name(worker, as_str(args, "assigned_to_name"))

	if row["state"] in TERMINAL_STATES:
		raise ToolError(
			f"{row['name']} is {row['state']} — the work is finished or abandoned, and reassigning it "
			"would rewrite history rather than dispatch anybody. Raise a fresh task. Nothing was "
			"changed."
		)

	held = live_assignment(row["name"])
	reassigned_from = None
	if held:
		current = dict(
			frappe.db.get_value(
				FARM_TASK_ASSIGNMENT, held, ["assigned_to", "assigned_to_name", "state"], as_dict=True
			)
			or {}
		)
		if current.get("assigned_to") == worker:
			raise ToolError(f"{row['name']} is already held by {worker_name}. Nothing was changed.")
		if not as_bool(args, "reassign", False):
			raise ToolError(
				f"{row['name']} is held by {current.get('assigned_to_name') or current.get('assigned_to')} "
				f"({held}, {current.get('state')}). Taking work off somebody who may already be stood "
				"in front of it is a decision, not a correction — pass reassign=true if you mean it. "
				"Nothing was changed."
			)
		reason = as_str(args, "reason")
		if not reason:
			raise ToolError(
				"reassigning work off somebody needs a reason, which is written onto their "
				"assignment. 'Taken off them with no explanation' is the record nobody can defend. "
				"Nothing was changed."
			)
		reassigned = frappe.get_doc(FARM_TASK_ASSIGNMENT, held)
		reassigned.state = REJECTED
		reassigned.rejection_reason = f"Reassigned to {worker_name} by dispatch: {reason}"
		reassigned.save(ignore_permissions=True)
		reassigned_from = current.get("assigned_to_name") or current.get("assigned_to")

	task_doc = frappe.get_doc(FARM_TASK, row["name"])
	task_doc.assigned_to = worker
	task_doc.assigned_to_name = worker_name
	task_doc.state = CLAIMED
	task_doc.save(ignore_permissions=True)
	assignment = _open_assignment(task_doc, worker, worker_name, dispatched=True)

	data = {
		**_describe_task(dict(task_doc.as_dict())),
		"assignment": assignment,
		"concurrent_claims": len(concurrent_claims(worker)),
		"note": (
			"Dispatched, not claimed: the assignment records that a foreman sent this person rather "
			"than that they took it from the pool. The distinction is worth keeping — work that has "
			"to be dispatched because it needs a named licence holder is a different kind of work "
			"from work anybody with the skill can pick up."
		),
	}
	if reassigned_from:
		data["reassigned_from"] = reassigned_from
	return ToolResult(
		data=data,
		summary=f"dispatched {row['name']} to {worker_name}"
		+ (f" (taken off {reassigned_from})" if reassigned_from else ""),
		docstatus_delta=f"{row['state']} → {CLAIMED}",
	)


# ── 3. claim_farm_task ──────────────────────────────────────────────────────
def claim_farm_task(args: dict) -> ToolResult:
	"""A worker takes one task from the pool. Capped at three at once, per worker."""
	_require()
	row = task_row(as_str(args, "task", required=True))
	worker = _worker(args)
	worker_name = _worker_name(worker, as_str(args, "worker_name"))

	if row["state"] != AVAILABLE:
		if row["state"] == DRAFT:
			raise ToolError(
				f"{row['name']} is still a Draft and is not in the pool. Somebody has to publish it "
				"first. Nothing was changed."
			)
		if row["state"] in TERMINAL_STATES:
			raise ToolError(
				f"{row['name']} is {row['state']}. There is nothing to claim. Nothing was changed."
			)
		held = frappe.db.get_value(FARM_TASK, row["name"], "assigned_to_name") or "somebody else"
		raise ToolError(
			f"{row['name']} is {row['state']} and held by {held}. Two people stood in front of the "
			"same work both believing it is theirs is exactly what a dispatch board exists to "
			"prevent. Nothing was changed."
		)

	if (row.get("dispatch_mode") or "Either") not in SELF_PICKABLE:
		raise ToolError(
			f"{row['name']} is dispatch_mode Dispatched: somebody has to be SENT to it by name. That "
			"is how this app marks work where the named holder matters — a licence, a safety-critical "
			"repair — and self-picking it would put the wrong person's name on a regulated record. A "
			"foreman assigns it with assign_farm_task. Nothing was changed."
		)

	holding = concurrent_claims(worker)
	if len(holding) >= MAX_CONCURRENT_CLAIMS:
		raise ToolError(
			f"{worker_name} is already holding {len(holding)} task(s): {', '.join(sorted(holding))}. "
			f"The limit is {MAX_CONCURRENT_CLAIMS} at once. This is a hoarding limit and not a "
			"productivity one — completing or rejecting one frees a slot in the same instant, and "
			"the point is that nobody can pull the whole pool onto their own name and leave a board "
			"that looks worked. Nothing was changed."
		)

	task_doc = frappe.get_doc(FARM_TASK, row["name"])
	task_doc.assigned_to = worker
	task_doc.assigned_to_name = worker_name
	task_doc.state = CLAIMED
	task_doc.save(ignore_permissions=True)
	assignment = _open_assignment(task_doc, worker, worker_name, dispatched=False)

	return ToolResult(
		data={
			**_describe_task(dict(task_doc.as_dict())),
			"assignment": assignment,
			"concurrent_claims": len(holding) + 1,
			"claims_remaining": MAX_CONCURRENT_CLAIMS - len(holding) - 1,
			"evidence_you_will_need": _contract_sentence(evidence_contract(task_doc.evidence_required)),
		},
		summary=f"{worker_name} claimed {row['name']} ({len(holding) + 1} of {MAX_CONCURRENT_CLAIMS} held)",
		docstatus_delta=f"{AVAILABLE} → {CLAIMED}",
	)


# ── 4. start_farm_task ──────────────────────────────────────────────────────
def start_farm_task(args: dict) -> ToolResult:
	"""Clock in on one claimed task. The start of the hour that gets charged to it."""
	_require()
	assignment = _assignment_for(args)
	worker = _worker(args, required=False)
	if worker and assignment.get("assigned_to") != worker:
		raise ToolError(
			f"{assignment['task']} is held by {assignment.get('assigned_to_name')}, not {worker}. "
			"Nothing was changed."
		)
	if assignment.get("state") == IN_PROGRESS:
		raise ToolError(
			f"{assignment['name']} was already started at {assignment.get('started_at')}. Starting it "
			"twice would move the clock-in forward and shorten the hour actually spent. Nothing was "
			"changed."
		)
	if assignment.get("state") != CLAIMED:
		raise ToolError(
			f"{assignment['name']} is {assignment.get('state')}, not {CLAIMED}. Nothing was changed."
		)

	doc = frappe.get_doc(FARM_TASK_ASSIGNMENT, assignment["name"])
	doc.state = IN_PROGRESS
	doc.started_at = as_str(args, "started_at") or frappe.utils.now()
	doc.save(ignore_permissions=True)
	_set_task_state(assignment["task"], IN_PROGRESS)

	task = task_row(assignment["task"])
	return ToolResult(
		data={
			"assignment": _describe_assignment(dict(doc.as_dict())),
			"task": _describe_task(task),
			"evidence_you_will_need": _contract_sentence(evidence_contract(task.get("evidence_required"))),
			"note": (
				"This is the clock-in for THIS TASK, not for the shift. A worker on the clock all "
				"morning did this particular cabin between ten and half past, and that is what an "
				"hour charged to a job has to mean."
			),
		},
		summary=f"{doc.assigned_to_name} started {assignment['task']} at {doc.started_at}",
		docstatus_delta=f"{CLAIMED} → {IN_PROGRESS}",
	)


# ── 5. complete_farm_task ───────────────────────────────────────────────────
def complete_farm_task(args: dict) -> ToolResult:
	"""Finish one task: check the evidence, file it, and write the compliance record."""
	_require()
	assignment = _assignment_for(args, or_completed=True)
	task = task_row(assignment["task"])
	worker = _worker(args)

	if assignment.get("assigned_to") != worker:
		raise ToolError(
			f"{assignment['task']} is held by "
			f"{assignment.get('assigned_to_name') or assignment.get('assigned_to')}, not {worker}. A "
			"completion filed by somebody who was not there is not a chain of custody, it is a "
			"rumour — and it is the first thing an auditor pulls on. Nothing was changed."
		)
	# v0.20.1. A COMPLETION THAT ARRIVES TWICE IS ONE COMPLETION. See
	# `_replayed` — the refusal below still stands for every finished assignment
	# whose signature does not match, which is the case where two different
	# submissions are genuinely in conflict.
	if assignment.get("state") == COMPLETED:
		replay = _replayed(assignment, task, worker, args)
		if replay is not None:
			return replay
	if assignment.get("state") not in (CLAIMED, IN_PROGRESS):
		raise ToolError(
			f"{assignment['name']} is {assignment.get('state')} and cannot be completed. Nothing was changed."
		)

	contract = evidence_contract(task.get("evidence_required"))
	evidence = inspections.normalise_evidence(args.get("evidence_files"), "evidence_files")
	signature = as_str(args, "signature_file")
	narrative = as_str(args, "completion_narrative")
	witness = as_str(args, "witness")
	findings_given = "findings_text" in args
	findings = as_str(args, "findings_text")

	clean_pass = clean_pass_flag(args)
	if clean_pass is True:
		# An explicit "I walked it and found nothing" satisfies a contract that
		# demands findings_text. See `clean_pass_flag` for why the flag has to
		# exist at all.
		findings_given = True
	elif clean_pass is False and not findings.strip():
		raise ToolError(
			"clean_pass=false says something was found, and findings_text is empty. The whole "
			"value of the flag is that it is the worker's own answer — an answer of 'issues "
			"found' with nothing beside it opens a corrective action that names no fault, which "
			"is the one thing an auditor cannot act on. Write what was wrong. Nothing was changed."
		)

	unmet = _unmet_evidence(contract, evidence, signature, findings_given, witness)
	if unmet:
		raise ToolError(
			f"{assignment['task']} cannot be completed: its evidence contract is not met.\n"
			+ "\n".join(f"  - {line}" for line in unmet)
			+ "\n\nThis is the refusal the whole doctype exists for. The contract was stated when "
			"the task was raised, precisely so that closing it could not be a tick in a box. "
			"Nothing was changed and no compliance record was written — fix the submission and "
			"call again."
		)

	now = frappe.utils.now()
	doc = frappe.get_doc(FARM_TASK_ASSIGNMENT, assignment["name"])
	doc.state = COMPLETED
	doc.completed_at = as_str(args, "completed_at") or now
	doc.completion_narrative = narrative
	doc.findings_text = findings
	doc.witness = witness
	# v0.19.1. FSMA §112.161(a)(1)(i) wants the location as well as the name, and
	# the completion is where the location is knowable — the phone is standing in
	# it. Written only when given: a blank is "nobody recorded it", and overwriting
	# a location somebody already put on the assignment with an empty string would
	# turn a recorded fact into a missing one.
	location_gps = as_str(args, "farm_location_gps")
	if location_gps:
		doc.farm_location_gps = location_gps
	doc.signature_file = signature or doc.signature_file
	doc.actual_duration_minutes = as_int(args, "actual_duration_minutes") or _elapsed(
		doc.started_at, doc.completed_at
	)
	for row in evidence:
		doc.append("evidence_files", dict(row))

	# v0.20.1. THE VISIT AND THE SIGNATURE, both written here and nowhere else.
	#
	# `visit_id` is written ONLY WHEN GIVEN, on the same argument `farm_location_gps`
	# is: a client that does not mint one leaves whatever is already on the row
	# alone, and blanking it would turn a trip somebody can see in `list_visits`
	# into five unrelated completions.
	#
	# The signature is hashed from THE PAYLOAD AS IT ARRIVED — `as_str(args,
	# "completed_at")` and not `doc.completed_at`, because the second one is the
	# server's `now()` where the client sent nothing, and `now()` differs on every
	# retry. Hashing it would make the record unmatchable by the very resubmission
	# it exists to recognise. See `erpnext_mcp/completions.py`.
	visit = as_str(args, "visit_id")
	if visit:
		doc.visit_id = visit
	doc.completion_signature = completions.signature(
		assignment["name"],
		worker,
		evidence,
		findings,
		narrative,
		as_str(args, "completed_at"),
	)

	# WHAT GOES ON THE COMPLIANCE RECORD IS NOT ALWAYS WHAT THE WORKER TYPED.
	# `records.branch_state` reads a record's state off its findings text —
	# non-empty means Corrective Action Required — so a worker who typed the
	# literal words "clean pass" into a field the contract made mandatory would
	# open a corrective action against a cabin that is fine. `clean_pass` is the
	# authoritative signal, so on a clean pass the RECORD's findings are empty
	# (which is how records.py spells "nothing was wrong") and the sentence goes
	# in the record's notes instead. The ASSIGNMENT keeps the worker's own words
	# either way — that is the evidence, and it is not this function's to edit.
	record_findings = "" if clean_pass is True else findings
	produced, record_note, record_state = _produce_record(
		task, doc, evidence, signature, record_findings, args
	)
	if produced:
		doc.produced_record = produced
	doc.save(ignore_permissions=True)

	final_state = AWAITING_REVIEW if record_state == records.CORRECTIVE_ACTION_REQUIRED else COMPLETED
	_set_task_state(assignment["task"], final_state, produced_record=produced or "")

	data = {
		"task": _describe_task(task_row(assignment["task"])),
		"assignment": _describe_assignment(dict(doc.as_dict())),
		"evidence_filed": len(evidence),
		"evidence_required": contract,
		"produced_record": produced,
		"produced_record_doctype": task.get("creates_record") or None,
		"produced_record_state": record_state,
		"final_state": final_state,
		# v0.20.1. ALWAYS PRESENT, AND FALSE HERE. A client that has to test
		# whether the key exists before reading it has two code paths where it
		# needs one, and the one it exercises least is the one that breaks — this
		# whole release is about a retry path nobody ran until an orchard did.
		"x_idempotent": False,
		"completion_signature": doc.completion_signature,
		"visit_id": doc.visit_id or None,
	}
	if record_note:
		data["record_note"] = record_note
	if final_state == AWAITING_REVIEW:
		data["review_note"] = (
			"This went to Awaiting-Review because the record it produced found something. The WORK "
			"is done and the register moved forward, so the alert that asked for it will dismiss on "
			"the next sweep; what needs a person is the finding, and a Critical alert now stands "
			"against the record until it is closed or superseded. Doing the work and finding a "
			"problem are two different facts and both are true."
		)
	else:
		data["dismissal_note"] = (
			"Nothing here touched a Compliance Alert. The record this completion wrote moved the "
			"register forward, and the alert that asked for the work auto-dismisses on the next "
			"sweep because its condition is no longer true. That is the only honest way for an "
			"alert to go away — changing the world and letting the sweep notice."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{doc.assigned_to_name} completed {assignment['task']} with {len(evidence)} evidence "
			f"file(s)" + (f", produced {task.get('creates_record')} {produced}" if produced else "")
		),
		docstatus_delta=f"{assignment.get('state')} → {final_state}",
	)


def _replayed(assignment: dict, task: dict, worker: str, args: dict):
	"""The existing completion, where this submission IS that completion. Else None.

	v0.20.1, and the reason is in `erpnext_mcp/completions.py`: an offline queue
	drained into a connection that dropped between the server's acknowledgement
	and the handset's receipt, and every re-send came back as a hard error about
	work that was already filed and evidenced.

	RETURNING None IS NOT "NO OPINION" — it is "this is a different submission",
	and the caller's refusal then stands. Two people cannot file the same
	completion, a second account of the same work is not the first one again, and
	a task genuinely completed twice by accident is a thing somebody should be
	told about rather than have quietly absorbed. The whole value of the
	signature is that it separates those from a phone asking the same question
	twice.

	NOTHING HERE WRITES. No record is produced, no state moves, no evidence row
	is appended — that is the entire promise, and it is kept by there being no
	`save` in this function. The response is rebuilt by READING the assignment
	that already exists, so three rapid retries produce three identical answers
	and one record.
	"""
	stored = str(assignment.get("completion_signature") or "").strip()
	if not stored:
		# A Completed row with no signature at all. Pre-v0.20.1 rows are given
		# one by the backfill patch; a row that still has none after that is one
		# the backfill could not read, and guessing that an unknown submission
		# matches it would turn a genuine conflict into a silent success.
		return None
	evidence = inspections.normalise_evidence(args.get("evidence_files"), "evidence_files")
	if not completions.matches(
		stored,
		assignment["name"],
		worker,
		evidence,
		as_str(args, "findings_text"),
		as_str(args, "completion_narrative"),
		as_str(args, "completed_at"),
	):
		return None

	doc = frappe.get_doc(FARM_TASK_ASSIGNMENT, assignment["name"])
	row = _assignment_row(assignment["name"])
	produced = str(row.get("produced_record") or "") or None
	doctype = str(task.get("creates_record") or "").strip()
	record_state = None
	if produced and doctype and compat.doctype_exists(doctype):
		record_state = str(frappe.db.get_value(doctype, produced, "workflow_state") or "") or None
	filed = len(doc.get("evidence_files") or [])

	data = {
		"task": _describe_task(task_row(assignment["task"])),
		"assignment": _describe_assignment(row),
		"evidence_filed": filed,
		"evidence_required": evidence_contract(task.get("evidence_required")),
		"produced_record": produced,
		"produced_record_doctype": doctype or None,
		"produced_record_state": record_state,
		"final_state": str(task.get("state") or COMPLETED),
		"x_idempotent": True,
		"completion_signature": stored,
		"visit_id": row.get("visit_id") or None,
		"idempotent_note": (
			f"This completion was already filed, at {row.get('completed_at')}, and nothing was "
			"changed by this call. The submission matches the one on record — same worker, same "
			"evidence, same account of the work — so this is the SAME completion arriving twice "
			"rather than a second one. That is the expected shape of a mobile client whose "
			"acknowledgement was lost to a dropped connection, and it is a success: the work is "
			"recorded, the evidence is filed"
			+ (f" and {doctype} {produced} exists" if produced else "")
			+ ". A client seeing this may clear the item from its queue."
		),
	}
	return ToolResult(
		data=data,
		summary=(
			f"{row.get('assigned_to_name') or worker} had already completed {assignment['task']} "
			f"at {row.get('completed_at')}; this identical resubmission changed nothing"
		),
		docstatus_delta=f"{COMPLETED} → {COMPLETED} (no change)",
	)


#: What a clean pass is recorded AS, on the record that inherits an empty
#: findings field. Blank findings and a blank notes field would be a record that
#: cannot tell "walked, nothing wrong" from "nobody filled this in".
CLEAN_PASS_NOTE = "No findings reported by inspector."


def clean_pass_flag(args: dict):
	"""The worker's own answer to "was this clean", as True, False, or None.

	v0.17.1. THREE STATES, AND THE THIRD IS THE POINT. None means nobody was
	asked, and the original rule applies: blank findings is a clean pass, text in
	findings opens a corrective action. That rule is right and it stays.

	It BREAKS when the evidence contract requires findings_text — as MC-Cabin-01's
	habitability inspection does — because blank is then not a submittable state,
	so a worker must type something, so every completion would open a corrective
	action against a cabin that is fine. The app therefore asks outright ("Clean
	pass" / "Issues found") and sends the answer, and Wave A treats that answer as
	AUTHORITATIVE rather than re-deriving intent by parsing the text. A worker who
	writes the words "clean pass" into a mandatory field must not trip a
	corrective action, and no amount of string-matching on findings_text is a
	sound way to avoid it.

	Accepts what Frappe and an HTTP form actually deliver: a real bool, 1/0, and
	the strings "true"/"false"/"1"/"0" that survive a JSON body reaching a
	whitelisted method as form data.
	"""
	if "clean_pass" not in args:
		return None
	raw = args.get("clean_pass")
	if raw is None or raw == "":
		return None
	if isinstance(raw, bool):
		return raw
	if isinstance(raw, (int, float)):
		return bool(raw)
	text = str(raw).strip().lower()
	if text in ("1", "true", "yes", "y"):
		return True
	if text in ("0", "false", "no", "n"):
		return False
	raise ToolError(
		f"clean_pass must be true or false, got {raw!r}. It is the worker's own answer to "
		"whether the walk was clean, and a value nobody can read is not an answer."
	)


def _unmet_evidence(
	contract: dict, evidence: list, signature: str, findings_given: bool, witness: str
) -> list:
	"""Which of the task's evidence requirements this submission does not meet."""
	unmet = []
	types = {str(row.get("evidence_type") or "") for row in evidence}

	if contract.get("photos") and not ({"Photo", "Video"} & types):
		unmet.append(
			"photos: the task requires at least one photograph, and none was filed. Pass "
			"evidence_files as a list of File docnames from commit_staged_file, or objects with "
			'{"file": "...", "evidence_type": "Photo"}.'
		)
	if contract.get("signature") and not (signature or "Signature" in types):
		unmet.append(
			"signature: the task requires a signature capture and none was filed. Pass "
			"signature_file, or an evidence_files entry typed Signature. A signature is what turns "
			"a row somebody typed into an attestation somebody made."
		)
	if contract.get("findings_text") and not findings_given:
		unmet.append(
			"findings_text: the task requires an explicit statement of what was found. PASS AN "
			'EMPTY STRING — findings_text: "" — to record that there was nothing wrong. A clean '
			"inspection is a positive statement and that is how you make it; leaving the argument "
			"out entirely records that nobody was asked."
		)
	if contract.get("witness") and not witness:
		unmet.append(
			"witness: the task requires somebody else who was there. This is work where one "
			"person's word is not the standard, which is why the contract asked."
		)
	return unmet


def _elapsed(started, completed):
	if not (started and completed):
		return 0
	try:
		return max(0, round(frappe.utils.time_diff_in_seconds(completed, started) / 60.0))
	except Exception:
		return 0


def _produce_record(task: dict, assignment_doc, evidence: list, signature: str, findings: str, args: dict):
	"""Build the compliance record this task promised. Returns (name, note, state).

	A task with no `creates_record` produces nothing and says nothing — most work
	is just work. A task naming a doctype this app has no builder for completes
	anyway, with the evidence filed against the assignment and a note saying what
	could not be produced: refusing the completion would strand a worker who has
	genuinely done the job in front of a tool that will not accept it.
	"""
	doctype = str(task.get("creates_record") or "").strip()
	if not doctype:
		return None, None, None
	builder = inspections.BUILDERS.get(doctype)
	if builder is None:
		return (
			None,
			(
				f"This task promised a {doctype}, which erpnext_mcp has no builder for, so the "
				"evidence is filed against the assignment and no record was written. The three it "
				f"can build are: {', '.join(sorted(inspections.BUILDERS))}. The completion itself "
				"stands — refusing it would strand somebody who has done the job."
			),
			None,
		)
	if not compat.doctype_exists(doctype):
		return (
			None,
			f"This site no longer has the {doctype} DocType, so no record could be written. Run "
			"`bench migrate`. The completion and its evidence stand.",
			None,
		)

	payload = dict(_safe_json(task.get("creates_record_data")))
	payload.update(parse_json_object(args.get("record_data"), "record_data"))
	if str(task.get("location_doctype") or "") == _subject_doctype(doctype):
		# Only where the task's location is the SAME KIND of thing the record is
		# about. A `water_test_stale` alert points at a block; a Water Test is
		# about the irrigation zone that waters it, and one block can have
		# several. Filling the zone in from the block would file the sample
		# against something that is not a water source — so the worker names the
		# zone in `record_data`, and `build_water_test` refuses if they did not.
		payload.setdefault(_subject_field(doctype), task.get("location"))
	payload["source_task"] = task.get("name")
	payload["findings"] = payload.get("findings") or findings

	if clean_pass_flag(args) is True:
		# The record's findings are empty on purpose — see `complete_farm_task`.
		# The attestation goes in notes, along with whatever the worker actually
		# wrote, so the record says "walked, nothing wrong" rather than being
		# indistinguishable from one nobody filled in.
		payload["findings"] = ""
		typed = as_str(args, "findings_text").strip()
		payload["notes"] = "\n".join(
			part
			for part in (
				str(payload.get("notes") or "").strip(),
				CLEAN_PASS_NOTE,
				f"Inspector's note: {typed}" if typed else "",
			)
			if part
		)
	payload.setdefault(_person_field(doctype), assignment_doc.assigned_to)
	payload.setdefault(f"{_person_field(doctype)}_name", assignment_doc.assigned_to_name)
	if doctype == inspections.HOUSING_INSPECTION and signature:
		payload.setdefault("signature", signature)

	record = builder(payload, evidence)
	return record.name, None, str(record.workflow_state or "")


def _subject_field(doctype: str) -> str:
	spec = inspections.SPECS.get(doctype)
	return spec.subject_field if spec else "unit"


def _subject_doctype(doctype: str) -> str:
	spec = inspections.SPECS.get(doctype)
	return spec.subject_doctype if spec else ""


def _person_field(doctype: str) -> str:
	spec = inspections.SPECS.get(doctype)
	return spec.person_field if spec else "inspector"


# ── 6. reject_farm_task ─────────────────────────────────────────────────────
def reject_farm_task(args: dict) -> ToolResult:
	"""Hand one task back, with a reason. Rejection is a first-class state."""
	_require()
	assignment = _assignment_for(args)
	worker = _worker(args, required=False)
	if worker and assignment.get("assigned_to") != worker:
		raise ToolError(
			f"{assignment['task']} is held by {assignment.get('assigned_to_name')}, not {worker}. "
			"Nothing was changed."
		)
	if assignment.get("state") not in (CLAIMED, IN_PROGRESS):
		raise ToolError(
			f"{assignment['name']} is {assignment.get('state')} and cannot be rejected. Nothing was changed."
		)

	reason = as_str(args, "reason") or as_str(args, "rejection_reason")
	if not reason:
		raise ToolError(
			"reason is required. This is the most useful sentence in the whole doctype: it is what "
			"turns 'nobody got to it and dispatch never followed up' — the answer nobody can defend "
			"— into 'the ladder is broken and I could not reach the detector', which is a fact "
			"somebody can act on. Nothing was changed."
		)

	doc = frappe.get_doc(FARM_TASK_ASSIGNMENT, assignment["name"])
	doc.state = REJECTED
	doc.rejection_reason = reason
	doc.completed_at = frappe.utils.now()
	doc.save(ignore_permissions=True)

	# Back to the pool, with the name cleared. The rejected assignment stays: it
	# is the record that somebody WAS sent, looked, and could not do it, which is
	# a considerably better compliance answer than an absence.
	back_to = AVAILABLE if not as_bool(args, "cancel", False) else CANCELLED
	_set_task_state(assignment["task"], back_to, assigned_to="", assigned_to_name="")

	return ToolResult(
		data={
			"task": _describe_task(task_row(assignment["task"])),
			"assignment": _describe_assignment(dict(doc.as_dict())),
			"returned_to_state": back_to,
			"note": (
				"The rejected assignment stays on the record. It is the proof that somebody was "
				"sent, went, and could not do it — which answers an auditor's question in a way "
				"that an absence never does. The task is back "
				+ ("in the pool." if back_to == AVAILABLE else "and cancelled.")
			),
		},
		summary=f"{doc.assigned_to_name} rejected {assignment['task']}: {reason[:80]}",
		docstatus_delta=f"{assignment.get('state')} → {REJECTED}",
	)


# ── 7. list_available_tasks ─────────────────────────────────────────────────
def list_available_tasks(args: dict) -> ToolResult:
	"""The pool: what a worker could pick up right now, and whether they may."""
	_require()
	company = _company(args)
	limit = min(as_limit(args), BOARD_CAP)

	filters = {"state": AVAILABLE, "dispatch_mode": ("in", list(SELF_PICKABLE))}
	if company:
		filters["company"] = company
	for key, fieldname in (("location", "location"), ("skill", "skill_required"), ("task_type", "task_type")):
		value = as_str(args, key) or as_str(args, fieldname)
		if value:
			filters[fieldname] = value
	urgency = as_str(args, "urgency")
	if urgency:
		filters["urgency"] = as_choice(FARM_TASK, "urgency", urgency, "urgency")

	rows = frappe.db.get_all(
		FARM_TASK,
		filters=filters,
		fields=compat.existing_fields(FARM_TASK, _TASK_FIELDS),
		order_by="modified desc",
		limit=limit,
	)
	tasks = _by_urgency([_describe_task(dict(row)) for row in rows or []])

	worker = as_str(args, "worker_id")
	data = {
		"company": company,
		"count": len(tasks),
		"limit": limit,
		"truncated": len(tasks) >= limit,
		"tasks": tasks,
		"filters": {
			"location": as_str(args, "location") or None,
			"skill": as_str(args, "skill") or as_str(args, "skill_required") or None,
			"task_type": as_str(args, "task_type") or None,
			"urgency": urgency or None,
		},
		"note": (
			"Only Self-pick and Either tasks are here. Dispatched work is deliberately absent from "
			"the pool: somebody has to be SENT to it by name, because that is how this app marks "
			"work where the named holder matters."
		),
	}
	if worker:
		held = concurrent_claims(worker)
		data["worker_id"] = worker
		data["concurrent_claims"] = len(held)
		data["claims_remaining"] = max(0, MAX_CONCURRENT_CLAIMS - len(held))
		data["may_claim"] = len(held) < MAX_CONCURRENT_CLAIMS
		if not data["may_claim"]:
			data["claim_note"] = (
				f"{worker} is holding {len(held)} task(s), which is the limit of "
				f"{MAX_CONCURRENT_CLAIMS}. Completing or rejecting one frees a slot immediately."
			)
	return ToolResult(
		data=data,
		summary=f"{len(tasks)} task(s) in the pool"
		+ (f"; {worker} may claim {data.get('claims_remaining')}" if worker else ""),
	)


def _by_urgency(tasks: list) -> list:
	order = {"Critical": 0, "High": 1, "Normal": 2, "Low": 3}
	return sorted(tasks, key=lambda task: (order.get(task["urgency"], 9), str(task["name"])))


# ── 8. list_dispatched_tasks ────────────────────────────────────────────────
def list_dispatched_tasks(args: dict) -> ToolResult:
	"""Everything one worker is holding or has finished, with their assignments."""
	_require()
	worker = _worker(args, required=True)
	limit = min(as_limit(args), BOARD_CAP)

	filters = {"assigned_to": worker}
	state = as_str(args, "state")
	if state:
		filters["state"] = as_choice(FARM_TASK_ASSIGNMENT, "state", state, "state")
	elif not as_bool(args, "include_finished", False):
		filters["state"] = ("in", [CLAIMED, IN_PROGRESS])
	company = _company(args)
	if company:
		filters["company"] = company

	rows = frappe.db.get_all(
		FARM_TASK_ASSIGNMENT,
		filters=filters,
		fields=compat.existing_fields(FARM_TASK_ASSIGNMENT, _ASSIGNMENT_FIELDS),
		order_by="creation desc",
		limit=limit,
	)
	assignments = [_describe_assignment(dict(row)) for row in rows or []]
	task_names = sorted({entry["task"] for entry in assignments if entry.get("task")})
	tasks = {}
	if task_names:
		for row in (
			frappe.db.get_all(
				FARM_TASK,
				filters={"name": ("in", task_names)},
				fields=compat.existing_fields(FARM_TASK, _TASK_FIELDS),
				limit=BOARD_CAP,
			)
			or []
		):
			tasks[row["name"]] = _describe_task(dict(row))
	for entry in assignments:
		entry["task_detail"] = tasks.get(entry.get("task"))

	held = [entry for entry in assignments if entry["state"] in (CLAIMED, IN_PROGRESS)]
	return ToolResult(
		data={
			"worker_id": worker,
			"count": len(assignments),
			"holding_now": len(held),
			"claims_remaining": max(0, MAX_CONCURRENT_CLAIMS - len(concurrent_claims(worker))),
			"limit": limit,
			"truncated": len(assignments) >= limit,
			"assignments": assignments,
		},
		summary=f"{worker} has {len(held)} task(s) in hand, {len(assignments)} shown",
	)


# ── 9. list_dispatch_board ──────────────────────────────────────────────────
def list_dispatch_board(args: dict) -> ToolResult:
	"""The Kanban as JSON: every task grouped by state, worst urgency first."""
	_require()
	company = _company(args)
	limit = min(as_limit(args), BOARD_CAP)

	filters = {}
	if company:
		filters["company"] = company
	state_filter = as_str(args, "state_filter") or as_str(args, "state")
	if state_filter:
		wanted = [part.strip() for part in state_filter.split(",") if part.strip()]
		unknown = [part for part in wanted if part not in STATES]
		if unknown:
			raise ToolError(
				f"state_filter names {', '.join(repr(part) for part in unknown)}, which is not a Farm "
				f"Task state. The eight are: {', '.join(STATES)}."
			)
		filters["state"] = ("in", wanted)
	elif not as_bool(args, "include_closed", False):
		filters["state"] = ("in", [state for state in STATES if state not in TERMINAL_STATES])
	for key in ("task_type", "urgency", "assigned_to", "skill_required"):
		value = as_str(args, key)
		if value:
			filters[key] = value

	rows = frappe.db.get_all(
		FARM_TASK,
		filters=filters,
		fields=compat.existing_fields(FARM_TASK, _TASK_FIELDS),
		order_by="modified desc",
		limit=limit,
	)
	tasks = _by_urgency([_describe_task(dict(row)) for row in rows or []])

	columns = {state: [] for state in STATES}
	for task in tasks:
		columns.setdefault(task["state"], []).append(task)
	from_alerts = [task for task in tasks if task["source_alert"]]
	unassigned = [task["name"] for task in tasks if task["state"] == AVAILABLE]
	critical = [task["name"] for task in tasks if task["urgency"] == "Critical" and task["open"]]

	return ToolResult(
		data={
			"company": company,
			"count": len(tasks),
			"limit": limit,
			"truncated": len(tasks) >= limit,
			"by_state": {state: len(entries) for state, entries in columns.items()},
			"columns": {state: entries for state, entries in columns.items() if entries},
			"in_the_pool": unassigned,
			"open_critical": critical,
			"generated_from_alerts": len(from_alerts),
			"kanban_route": "/app/farm-task/view/kanban/Farm Task Dispatch",
			"note": (
				f"{len(from_alerts)} of {len(tasks)} task(s) on this board came from a compliance "
				"alert. That fraction is the honest measure of whether the calendar is driving "
				"work or being read and ignored."
			),
		},
		summary=(
			f"{len(tasks)} task(s) on the board: {len(unassigned)} in the pool, {len(critical)} open Critical"
		),
	)


# ── 10. get_farm_task ───────────────────────────────────────────────────────
def get_farm_task(args: dict) -> ToolResult:
	"""One task in full, with every assignment it has ever had and their evidence."""
	_require()
	row = task_row(as_str(args, "task", required=True))
	described = _describe_task(row)

	history = [
		_describe_assignment(dict(entry))
		for entry in frappe.db.get_all(
			FARM_TASK_ASSIGNMENT,
			filters={"task": row["name"]},
			fields=compat.existing_fields(FARM_TASK_ASSIGNMENT, _ASSIGNMENT_FIELDS),
			order_by="creation desc",
			limit=100,
		)
		or []
	]
	for entry in history:
		entry["evidence"] = _assignment_evidence(entry["name"])

	described["assignments"] = history
	described["assignment_count"] = len(history)
	described["rejections"] = [entry for entry in history if entry["state"] == REJECTED]
	described["live_assignment"] = next(
		(entry for entry in history if entry["state"] in (CLAIMED, IN_PROGRESS)), None
	)

	if row.get("source_alert") and compat.doctype_exists(ALERT):
		alert = frappe.db.get_value(
			ALERT,
			row["source_alert"],
			["alert_type", "severity", "dismissed", "auto_dismissed", "alert_message", "due_date"],
			as_dict=True,
		)
		described["alert"] = dict(alert) if alert else None
		if alert and compat.checked(alert.get("auto_dismissed")):
			described["loop_closed"] = (
				"The alert this task came from has auto-dismissed, which means the sweep found its "
				"condition no longer true. The work was done and the calendar noticed by itself."
			)
	return ToolResult(
		data=described,
		summary=(
			f"{row['name']} — {described['task_name']} ({described['state']}, {len(history)} assignment(s))"
		),
	)


def _assignment_evidence(assignment: str) -> list:
	if not compat.doctype_exists(inspections.EVIDENCE_CHILD):
		return []
	rows = frappe.db.get_all(
		inspections.EVIDENCE_CHILD,
		filters={"parent": assignment, "parenttype": FARM_TASK_ASSIGNMENT},
		fields=["evidence_type", "file", "file_url", "caption", "captured_on", "idx"],
		order_by="idx asc",
		limit=inspections.EVIDENCE_CAP * 2,
	)
	return [
		{
			"evidence_type": row.get("evidence_type"),
			"file": row.get("file"),
			"file_url": row.get("file_url"),
			"caption": row.get("caption"),
			"captured_on": str(row.get("captured_on") or "") or None,
		}
		for row in rows or []
	]


# ── 11. generate_tasks_from_compliance_alerts ───────────────────────────────
#: What each alert type becomes when it stops being a warning and starts being
#: work. Every entry is a judgement about the SHAPE of the job, and the two that
#: matter most are `dispatch` and `evidence`:
#:
#:   * SELF-PICK for general labour. Fifty-four habitability walks that a foreman
#:     has to assign by hand are fifty-four walks that do not happen.
#:   * DISPATCHED wherever the named holder matters — a licence renewal, an I-9,
#:     anything an agency will later ask "who did this" about.
#:
#: An alert type absent from this table is reported by name rather than turned
#: into a generic task. A task with a made-up evidence contract is worse than no
#: task: it produces a compliance record nobody can rely on.
ALERT_TASK_MAP = {
	"housing_inspection_overdue": {
		"task_type": "Inspection",
		"creates_record": "Housing Inspection",
		"skill": "camp_maintenance",
		"dispatch": "Self-pick",
		"minutes": 45,
		"evidence": {"photos": True, "signature": True, "findings_text": True},
		"what": "Walk the cabin and record a habitability inspection",
	},
	"housing_detector_test_stale": {
		"task_type": "Test",
		"creates_record": "Detector Test",
		"skill": "camp_maintenance",
		"dispatch": "Self-pick",
		"minutes": 20,
		"evidence": {"photos": True, "findings_text": True},
		"what": "Test the smoke and CO detectors and record the result",
	},
	"water_test_stale": {
		"task_type": "Water-Sampling",
		"creates_record": "Water Test",
		"skill": "water_sampling",
		"dispatch": "Self-pick",
		"minutes": 60,
		"evidence": {"photos": True, "findings_text": True},
		"what": "Take an agricultural water sample and send it to the laboratory",
	},
	"certification_expiring": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "compliance_admin",
		"dispatch": "Dispatched",
		"minutes": 90,
		"evidence": {"findings_text": True},
		"what": "Renew the certificate before it lapses",
	},
	"policy_review_overdue": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "compliance_admin",
		"dispatch": "Dispatched",
		"minutes": 120,
		"evidence": {"findings_text": True},
		"what": "Review the procedure and record what changed",
	},
	"i9_expired": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "hr_admin",
		"dispatch": "Dispatched",
		"minutes": 30,
		"evidence": {"findings_text": True, "signature": True},
		"what": "Re-verify employment authorisation",
	},
	"flc_license_expiring": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "hr_admin",
		"dispatch": "Dispatched",
		"minutes": 60,
		"evidence": {"findings_text": True},
		"what": "Renew the farm labor contractor licence",
	},
	"filing_response_due": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "compliance_admin",
		"dispatch": "Dispatched",
		"minutes": 45,
		"evidence": {"findings_text": True},
		"what": "Chase the agency for a response before the deadline",
	},
	"audit_action_overdue": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "compliance_admin",
		"dispatch": "Dispatched",
		"minutes": 120,
		"evidence": {"findings_text": True, "photos": True},
		"what": "Close the corrective action the auditor raised",
	},
	"housing_corrective_action_open": {
		"task_type": "Repair",
		"creates_record": "",
		"skill": "camp_maintenance",
		"dispatch": "Dispatched",
		"minutes": 90,
		"evidence": {"photos": True, "findings_text": True},
		"what": "Fix what the inspection found, then record a fresh one",
	},
	"water_test_contamination": {
		"task_type": "Water-Sampling",
		"creates_record": "Water Test",
		"skill": "water_sampling",
		"dispatch": "Dispatched",
		"minutes": 90,
		"evidence": {"photos": True, "findings_text": True},
		"what": "Treat or switch the source, then re-sample",
	},
	# v0.19.0. `creates_record` is deliberately EMPTY even though there is an
	# Employee Training Record doctype now. Completing this task is arranging and
	# delivering a retraining — a trainer, a crew, a language, a room — and the
	# record it produces has to name the topics actually covered and carry the
	# trainee's own signature. `complete_farm_task` has no builder that can invent
	# either, and a task that auto-filed a training record with no topics and no
	# signature would produce exactly the document an auditor disallows. So the
	# task closes with findings text, and `record_training` files the evidence.
	"training_expiring": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "hr_admin",
		"dispatch": "Dispatched",
		"minutes": 120,
		"evidence": {"findings_text": True, "signature": True},
		"what": "Arrange and deliver the retraining, then file it with record_training",
	},
	# v0.19.3. Fifteen minutes, and it is a real fifteen minutes: the work is a
	# supervisor reading a record they were not present for and putting their name
	# to it. `creates_record` is empty for the same reason the training entry's is
	# — the record already exists, and what is missing is a signature on it, which
	# `sign_training_supervisor_review` writes and no completion builder can
	# invent. The evidence contract asks for the signature itself rather than a
	# findings note, because a review with no signature is the gap this task was
	# raised to close.
	"supervisor_review_lapsed": {
		"task_type": "Compliance-Audit",
		"creates_record": "",
		"skill": "hr_admin",
		"dispatch": "Dispatched",
		"minutes": 15,
		"evidence": {"signature": True},
		"what": (
			"Read the record and sign the §112.161(b) supervisor review with sign_training_supervisor_review"
		),
	},
}


def _recipe_for(alert_type: str) -> dict | None:
	"""The shape of work one alert type becomes: the table first, then the RECORD.

	v0.22.5, AND IT IS THE HALF OF THE CCF'S PRODUCER STORY THAT WAS MISSING. Since
	v0.22.0 every Compliance Rule has carried `producer_farm_task_type`,
	`producer_skill_required` and `evidence_contract` — seeded FROM this table, so
	the two could not disagree — but nothing read them back. A rule authored after
	the framework shipped therefore had a producer recipe on its record and landed
	in `skipped_unmapped` anyway, which is a framework that lets you define a rule
	and not the work it asks for.

	`ALERT_TASK_MAP` STAYS FIRST, and that ordering is the backward-compatibility
	guarantee: the thirteen shipped rules produce exactly the tasks they produced
	in v0.22.1, out of the same reviewed table, whatever a site has since edited
	onto their records. The record is consulted only where the table has nothing to
	say — which is precisely the case the table cannot cover, because the rule did
	not exist when it was written.

	Returns None where the record has no producer definition either, and None
	still means "reported by name, not turned into a generic task".
	"""
	recipe = ALERT_TASK_MAP.get(alert_type)
	if recipe is not None:
		return recipe

	try:
		from .. import compliance_rules

		name = compliance_rules.resolve(alert_type)
		row = compliance_rules.rule_row(name) if name else {}
	except Exception:  # pragma: no cover - a site mid-migrate
		return None
	if not row:
		return None

	task_type = str(row.get("producer_farm_task_type") or "").strip()
	evidence = compliance_rules._quietly(
		compliance_rules.parse_contract, row.get("evidence_contract_json"), {}
	)
	if not (task_type and evidence):
		# A TASK WITH NO EVIDENCE CONTRACT IS A TASK SOMEBODY CLOSES WITH A TICK,
		# and a tick in a box is what an auditor is trained to disbelieve. Both
		# halves are required before an alert becomes work, exactly as they are for
		# every entry in the table above.
		return None

	extra = compliance_rules._quietly(compliance_rules.as_object, row.get("extra_parameters_json"), {})
	expression = str(row.get("producer_assigned_to_expression") or "").strip()
	return {
		"task_type": task_type,
		"creates_record": "",
		"skill": str(row.get("producer_skill_required") or "").strip(),
		# ROUTED BY NAME WHERE THERE IS A NAME TO ROUTE TO. `Dispatched` is this
		# app's existing word for "somebody has to be SENT to this by name" — the
		# same mode a licence renewal and an I-9 re-verification take — and a
		# fourth dispatch_mode meaning the same thing would be a second vocabulary
		# for one idea, on the enum the iOS build switches on.
		"dispatch": DISPATCH_DISPATCHED if expression else "Self-pick",
		"minutes": int(extra.get("producer_task_minutes") or 0),
		"evidence": dict(evidence),
		"what": str(extra.get("producer_task_what") or "").strip() or str(row.get("title") or alert_type),
		"assigned_to_expression": expression,
	}


def _assignee_from_expression(recipe: dict, row: dict, notes: list) -> str:
	"""The Employee a rule's `producer_assigned_to_expression` names, or "".

	NEVER RAISES AND NEVER GUESSES. An expression that will not run, or that names
	somebody payroll has never heard of, leaves the task on its skill routing and
	says so on the report — because the alternative is a task assigned to a string
	nobody holds, which is a task that is on nobody's list AND out of the pool.
	"""
	expression = str(recipe.get("assigned_to_expression") or "").strip()
	if not expression:
		return ""
	doctype = str(row.get("source_doctype") or "")
	docname = str(row.get("source_docname") or "")
	if not (doctype and docname):
		return ""
	try:
		source = dict(frappe.get_doc(doctype, docname).as_dict())
	except Exception:
		notes.append(f"{doctype} {docname} could not be read, so the assignee expression was not evaluated.")
		return ""
	try:
		from ..alerts import sandbox

		answer = sandbox.evaluate(
			expression, {"row": source, "alert": dict(row), "today": frappe.utils.today()}
		)
	except Exception as exc:
		notes.append(f"the assignee expression {expression!r} did not run ({exc}); the task routes by skill.")
		return ""
	employee = str(answer or "").strip()
	if not employee:
		notes.append(
			f"the assignee expression {expression!r} produced nothing on {doctype} {docname} — the "
			"column it reads is empty on that record — so the task routes by skill."
		)
		return ""
	if compat.doctype_exists("Employee") and not frappe.db.exists("Employee", employee):
		notes.append(
			f"the assignee expression produced {employee!r}, which is not an Employee on this site. "
			"The task routes by skill rather than to a name nobody holds."
		)
		return ""
	return employee


#: Alert severity to task urgency. Deliberately NOT the identity mapping: a
#: Critical alert is a statement that something has stopped being lawful, and a
#: board where every item is Critical is a board nobody reads. High is the top of
#: the working scale; Critical on a task is reserved for the handful raised
#: directly by a failed detector or a contaminated sample.
SEVERITY_URGENCY = {
	alerts.SEVERITY_CRITICAL: "High",
	alerts.SEVERITY_WARNING: "Normal",
	alerts.SEVERITY_INFO: "Low",
}


def generate_tasks_from_compliance_alerts(args: dict) -> ToolResult:
	"""Turn every open compliance alert into a dispatchable task. Idempotent.

	THE BRIDGE, AND THE POINT OF THE WHOLE RELEASE. Sprint 7 could say fifty-four
	things were wrong; nothing could send anybody to fix one. This walks the open
	alerts, maps each to the shape of work it actually is, and raises a Farm Task
	carrying the evidence its completion has to produce.

	IDEMPOTENT BY CONSTRUCTION. A task carries `source_alert`, so a second run
	finds the task the first one raised and skips the alert. Re-running after
	fixing half the camp raises tasks only for the half still outstanding, which
	is the property that makes this safe to run whenever somebody wonders.

	DRY RUN DEFAULTS FALSE, unlike this app's other bulk write, and the asymmetry
	is deliberate. `dismiss_alert_bulk` defaults to a dry run because a
	mis-typed filter there HIDES non-compliance and leaves an operation reading
	as clean while nothing was fixed. The failure mode here is the opposite and
	far cheaper: too many tasks on a board, each of which is idempotent, none of
	which changes an operational record. Gating the useful direction behind a
	second call would be safety theatre paid for by the person trying to get work
	dispatched.
	"""
	_require()
	compat.require_doctype(
		ALERT, "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app."
	)
	company = _company(args)
	dry_run = as_bool(args, "dry_run", False)
	limit = min(as_int(args, "limit") or GENERATE_CAP, GENERATE_CAP)

	wanted = _alert_types(args)
	filters = {"dismissed": 0}
	if company:
		filters["company"] = company
	if wanted:
		filters["alert_type"] = ("in", sorted(wanted))

	rows = frappe.db.get_all(
		ALERT,
		filters=filters,
		fields=[
			"name",
			"alert_type",
			"severity",
			"category",
			"company",
			"source_doctype",
			"source_docname",
			"alert_message",
			"due_date",
		],
		order_by="due_date asc",
		limit=limit + 1,
	)
	rows = [dict(row) for row in rows or []]
	over_cap = len(rows) > limit
	rows = rows[:limit]

	answered = _already_answered([row["name"] for row in rows])
	report = {
		"company": company,
		"dry_run": dry_run,
		"alerts_considered": len(rows),
		"created": [],
		"sessions": [],
		"skipped_already_answered": [],
		"skipped_unmapped": [],
		"failed": [],
	}

	# v0.21.0. Before anything is raised one-alert-at-a-time, look for the places
	# where SEVERAL different things are overdue at once and one template covers
	# all of them. Those become one visit rather than N trips. Everything this
	# does not bundle falls through to the unchanged per-alert path below.
	bundled = _bundle_into_sessions(rows, answered, report, dry_run)

	for row in rows:
		alert_type = str(row.get("alert_type") or "")
		if row["name"] in bundled:
			continue
		if row["name"] in answered:
			report["skipped_already_answered"].append(
				{"alert": row["name"], "alert_type": alert_type, "task": answered[row["name"]]}
			)
			continue
		recipe = _recipe_for(alert_type)
		if recipe is None:
			report["skipped_unmapped"].append(
				{
					"alert": row["name"],
					"alert_type": alert_type,
					"reason": (
						f"no task recipe for {alert_type!r}. A generic task with a made-up evidence "
						"contract is worse than no task: it produces a compliance record nobody can "
						"rely on. Fill in the producer fields on the Compliance Rule with "
						"update_compliance_rule — producer_farm_task_type and evidence_contract at "
						"minimum — add it to ALERT_TASK_MAP, or raise the task by hand with "
						"create_farm_task."
					),
				}
			)
			continue
		try:
			report["created"].append(_task_from_alert(row, recipe, dry_run))
		except Exception as exc:
			report["failed"].append(
				{"alert": row["name"], "alert_type": alert_type, "error": f"{type(exc).__name__}: {exc}"}
			)

	by_type = {}
	for entry in report["created"]:
		by_type[entry["alert_type"]] = by_type.get(entry["alert_type"], 0) + 1
	report["created_count"] = len(report["created"])
	report["session_count"] = len(report["sessions"])
	report["alerts_bundled_into_sessions"] = sum(len(entry["alerts"]) for entry in report["sessions"])
	report["by_alert_type"] = dict(sorted(by_type.items()))
	report["kanban_route"] = "/app/farm-task/view/kanban/Farm Task Dispatch"
	report["note"] = (
		("DRY RUN — nothing was written. " if dry_run else "")
		+ f"{len(report['created'])} alert(s) became dispatchable work, "
		f"{len(report['skipped_already_answered'])} already had a task, "
		f"{len(report['skipped_unmapped'])} have no recipe. Re-running is safe: a task carries the "
		"alert that produced it, so this finds what it raised last time and skips it."
	)
	if report["sessions"]:
		report["session_note"] = (
			f"{report['alerts_bundled_into_sessions']} alert(s) at "
			f"{len(report['sessions'])} place(s) became ONE templated visit each instead of one "
			"task apiece. A worker walks into a cabin once and does everything it needs; the "
			"compliance records still come out separately, at their own cadences, because those "
			"are different regulators asking on different schedules. The bundling is deterministic "
			"— a template matched when its sections produce a superset of the records the pending "
			"alerts asked for — and it is idempotent, because the session records which alerts it "
			"answers."
		)
	if over_cap:
		report["capped"] = (
			f"More than {limit} open alerts matched. The first {limit} were considered; run again "
			"to take the next batch. An operation with this many open alerts should look at whether "
			"a rule is firing on a field that is empty everywhere rather than stale on a few."
		)
	return ToolResult(
		data=report,
		summary=(
			("dry run: would raise " if dry_run else "raised ")
			+ f"{len(report['created'])} farm task(s) from {len(rows)} open alert(s); "
			f"{len(report['skipped_already_answered'])} already answered"
		),
		docstatus_delta="" if dry_run or not report["created"] else "none → 0 (created)",
	)


def _bundle_into_sessions(rows: list, answered: dict, report: dict, dry_run: bool) -> set:
	"""One templated visit per place where a template covers everything overdue.

	THE RULE, IN FULL, BECAUSE AN AUDITOR WILL ASK WHY THREE THINGS BECAME ONE JOB:

	  1. Alerts are grouped by the PLACE they point at — `(source_doctype,
	     source_docname)` — and only where the source doctype is a register
	     somebody can be sent to. An alert about a certificate is not about a
	     place and is never bundled.
	  2. A place qualifies only with **two or more alerts of DIFFERENT types**.
	     One alert is one task; that path is unchanged and is the common case.
	  3. Each alert type is translated into the compliance record answering it
	     would produce, through `ALERT_TASK_MAP` — the same table the per-alert
	     path uses, so the two cannot disagree about what a habitability alert
	     asks for. An alert type whose recipe produces NO record (a licence
	     renewal, a policy review) cannot be covered by a section and takes the
	     place out of the running: bundling it would produce a visit that silently
	     answers three of four overdue things.
	  4. `sessions.match_template` picks the template whose sections produce a
	     SUPERSET of those records — tightest fit first, ties broken by docname so
	     the choice is the same on every run and every site.
	  5. No match is a first-class answer and leaves every alert at that place on
	     the ordinary per-alert path.

	IDEMPOTENT, and by a different mechanism from the per-alert path. A task
	carries one `source_alert`; a session answers several, so it stores them all
	and `sessions.alerts_answered_by_open_sessions` reads them back — whole
	docnames split on newlines, never a substring match. A second sweep finds the
	session and raises nothing. Once the session is SUBMITTED its records have
	moved the registers, the alerts dismiss themselves on the next sweep, and none
	of this is reached at all.

	NEVER RAISES PAST ONE PLACE. A template that could not be matched or a session
	that could not be created lands in `failed` for that location and the alerts
	fall through to the per-alert path, which is strictly better than losing them.
	"""
	if not compat.doctype_exists(sessions.SESSION_DOCTYPE):
		return set()

	places = {}
	for row in rows:
		doctype = str(row.get("source_doctype") or "")
		docname = str(row.get("source_docname") or "")
		if not (docname and doctype in sessions.MATCHABLE_ASSET_TYPES):
			continue
		places.setdefault((doctype, docname), []).append(row)

	already = sessions.alerts_answered_by_open_sessions()
	bundled = set()
	for (doctype, docname), alerts_here in sorted(places.items()):
		if len({str(row.get("alert_type") or "") for row in alerts_here}) < 2:
			continue
		if any(row["name"] in already or row["name"] in answered for row in alerts_here):
			# Something at this place is already answered — by a session from a
			# previous sweep, or by a plain task somebody raised. Bundling the
			# rest would produce a second job overlapping the first.
			for row in alerts_here:
				entry = already.get(row["name"])
				if entry:
					bundled.add(row["name"])
					report["skipped_already_answered"].append(
						{
							"alert": row["name"],
							"alert_type": row.get("alert_type"),
							"task": entry.get("task"),
							"session": entry.get("session"),
						}
					)
			continue

		wanted = set()
		coverable = True
		for row in alerts_here:
			recipe = _recipe_for(str(row.get("alert_type") or ""))
			produced = str((recipe or {}).get("creates_record") or "").strip()
			if not produced:
				coverable = False
				break
			wanted.add(produced)
		if not (coverable and wanted):
			continue

		match = sessions.match_template(doctype, wanted)
		if not match:
			continue

		entry = {
			"location": docname,
			"location_doctype": doctype,
			"template": match["template"],
			"template_name": match["template_name"],
			"template_version": match["version"],
			"covers": match["covers"],
			"extra_sections": match["extra_sections"],
			"alerts": [row["name"] for row in alerts_here],
			"alert_types": sorted({str(row.get("alert_type") or "") for row in alerts_here}),
			"session": None,
			"task": None,
		}
		if not dry_run:
			try:
				entry.update(_session_from_alerts(match, doctype, docname, alerts_here))
			except Exception as exc:
				report["failed"].append(
					{
						"location": docname,
						"template": match["template"],
						"error": f"{type(exc).__name__}: {exc}",
					}
				)
				continue
		report["sessions"].append(entry)
		bundled.update(row["name"] for row in alerts_here)
	return bundled


def _session_from_alerts(match: dict, doctype: str, docname: str, alerts_here: list) -> dict:
	"""One Farm Task carrying one Inspection Session, for a place with several alerts.

	THE FARM TASK IS STILL THE DISPATCH ATOM. It is the card on the Kanban board,
	the thing a worker claims, the thing that counts against the concurrent-claim
	limit. The session is the sectioned form behind that one card and never a
	second kind of card beside it — a board with two kinds of item on it is a
	board somebody has to be taught to read.

	`source_alert` carries the FIRST alert only, because the column holds one and
	the per-alert idempotency check reads it. The session carries all of them, and
	that is what `_bundle_into_sessions` checks on the next sweep.
	"""
	severity_order = {alerts.SEVERITY_CRITICAL: 0, alerts.SEVERITY_WARNING: 1, alerts.SEVERITY_INFO: 2}
	lead = sorted(
		alerts_here,
		key=lambda row: (severity_order.get(str(row.get("severity") or ""), 3), str(row["name"])),
	)[0]
	company = next((row.get("company") for row in alerts_here if row.get("company")), None)

	session = frappe.new_doc(sessions.SESSION_DOCTYPE)
	session.template = match["template"]
	session.location = docname
	session.location_doctype = doctype
	session.company = company
	session.state = sessions.STATE_DRAFT
	session.source_alerts = "\n".join(sorted(row["name"] for row in alerts_here))
	session.notes = "\n".join(
		f"{row.get('alert_type')}: {row.get('alert_message') or ''}".strip() for row in alerts_here
	)[:1000]
	session.insert(ignore_permissions=True)

	task = frappe.new_doc(FARM_TASK)
	task.task_name = f"{match['template_name']} — {docname}"[:140]
	task.task_type = "Inspection"
	task.state = AVAILABLE
	task.urgency = SEVERITY_URGENCY.get(str(lead.get("severity") or ""), "Normal")
	task.dispatch_mode = "Self-pick"
	task.company = company
	task.location_doctype = doctype
	task.location = docname
	task.skill_required = match["skill_required"] or None
	task.estimated_duration_minutes = match["estimated_duration_minutes"] or 0
	task.source_alert = lead["name"]
	task.inspection_session = session.name
	# The contract on the CARD is the floor; the real one is per section on the
	# template, checked by submit_inspection_session. Stating something here
	# rather than nothing keeps the task doctype's promise — there is no path to
	# a task somebody can close by saying they did it.
	task.evidence_required = json.dumps({"photos": True, "findings_text": True})
	task.notes = (
		f"One visit covering {len(alerts_here)} overdue item(s) at {docname}: "
		+ ", ".join(sorted({str(row.get("alert_type") or "") for row in alerts_here}))
		+ f". Worked from the template {match['template_name']} v{match['version']}, which has "
		f"{match['section_count']} section(s) and produces {', '.join(match['produces'])}. "
		"Submit it with submit_inspection_session — the compliance records come out separately, "
		"at their own cadences, from the one walk."
	)
	task.insert(ignore_permissions=True)
	frappe.db.set_value(sessions.SESSION_DOCTYPE, session.name, "farm_task", task.name)
	return {"session": session.name, "task": task.name, "lead_alert": lead["name"]}


def _alert_types(args: dict) -> set:
	raw = args.get("alert_types")
	if raw in (None, "", []):
		return set()
	if isinstance(raw, str):
		raw = [part.strip() for part in raw.split(",") if part.strip()]
	if not isinstance(raw, list):
		raise ToolError("alert_types must be a list of rule names, or a comma-separated string.")
	wanted = {str(entry).strip() for entry in raw if str(entry).strip()}
	unknown = sorted(wanted - set(alerts.names()))
	if unknown:
		raise ToolError(
			f"alert_types names {', '.join(repr(name) for name in unknown)}, which are not rules on "
			f"this site. list_compliance_rules has them. The mapped ones are: "
			f"{', '.join(sorted(ALERT_TASK_MAP))}."
		)
	return wanted


def _already_answered(alert_names: list) -> dict:
	"""alert docname → the Farm Task that already answers it."""
	if not alert_names:
		return {}
	rows = frappe.db.get_all(
		FARM_TASK,
		filters={"source_alert": ("in", sorted(alert_names))},
		fields=["name", "source_alert"],
		limit=GENERATE_CAP * 2,
	)
	return {str(row["source_alert"]): str(row["name"]) for row in rows or []}


def _task_from_alert(row: dict, recipe: dict, dry_run: bool) -> dict:
	subject = str(row.get("source_docname") or "")
	# v0.22.5. Resolved BEFORE the dry-run return, so a dry run reports the person
	# the task would land on. A dry run that could not answer "who gets this" would
	# be a dry run of the half of the decision nobody is worried about.
	routing_notes: list = []
	assignee = _assignee_from_expression(recipe, row, routing_notes)
	entry = {
		"alert": row["name"],
		"alert_type": row["alert_type"],
		"severity": row.get("severity"),
		"task_name": f"{recipe['what']} — {subject}" if subject else recipe["what"],
		"task_type": recipe["task_type"],
		"urgency": SEVERITY_URGENCY.get(str(row.get("severity") or ""), "Normal"),
		"dispatch_mode": recipe["dispatch"] if assignee else _pool_dispatch(recipe),
		# EITHER/OR, ENFORCED HERE AS WELL AS AT AUTHORING TIME. The controller
		# refuses a rule carrying both, and this is the second door: a task with a
		# named holder AND a skill would show up in the pool listing beside the
		# person already holding it.
		"skill_required": "" if assignee else recipe["skill"],
		"assigned_to": assignee or None,
		"creates_record": recipe["creates_record"] or None,
		"location_doctype": str(row.get("source_doctype") or "") or None,
		"location": subject or None,
		"evidence_required": dict(recipe["evidence"]),
		"task": None,
	}
	if routing_notes:
		entry["routing_notes"] = routing_notes
	if dry_run:
		return entry

	doc = frappe.new_doc(FARM_TASK)
	doc.task_name = entry["task_name"][:140]
	doc.task_type = recipe["task_type"]
	doc.state = AVAILABLE
	doc.urgency = entry["urgency"]
	doc.dispatch_mode = entry["dispatch_mode"]
	doc.company = row.get("company") or None
	doc.skill_required = entry["skill_required"]
	doc.estimated_duration_minutes = int(recipe.get("minutes") or 0)
	doc.source_alert = row["name"]
	doc.evidence_required = json.dumps(recipe["evidence"])
	doc.notes = str(row.get("alert_message") or "")
	if assignee:
		doc.assigned_to = assignee
		doc.assigned_to_name = _worker_name(assignee, "")
		# CLAIMED rather than Available, exactly as `create_farm_task` does when
		# somebody is sent by name: the task is already held, so nobody else can
		# pick it up and it appears on that person's own list rather than in a pool
		# they would have to go looking in.
		doc.state = CLAIMED

	# A location only where the register the alert points at is one a worker can
	# be sent to. An alert about a Certification points at a certificate, which
	# is not a place — filling `location` with it would put a certificate on a
	# map and make the pool's location filter useless.
	if subject and str(row.get("source_doctype") or "") in _DISPATCHABLE_LOCATIONS:
		doc.location_doctype = row["source_doctype"]
		doc.location = subject
	if recipe["creates_record"] and compat.doctype_exists(recipe["creates_record"]):
		spec = inspections.SPECS.get(recipe["creates_record"])
		doc.creates_record = recipe["creates_record"]
		if spec and subject and spec.subject_doctype == str(row.get("source_doctype") or ""):
			doc.creates_record_data = json.dumps({spec.subject_field: subject})
		elif spec:
			# The alert points at one kind of thing and the record is about
			# another — a block against an irrigation zone. Nothing is prefilled
			# and the worker is told what they have to name.
			doc.notes = (
				str(doc.notes or "")
				+ f"\n\nWhen completing this, name the {spec.subject_doctype} the sample came from "
				f'in record_data, e.g. {{"{spec.subject_field}": "<{spec.subject_doctype} docname>"}}. '
				f"One {row.get('source_doctype')} can have several, so this app will not guess."
			).strip()
	doc.insert(ignore_permissions=True)
	if assignee:
		entry["assignment"] = _open_assignment(doc, assignee, doc.assigned_to_name, dispatched=True)
	entry["task"] = doc.name
	entry["location"] = doc.location
	entry["creates_record"] = doc.creates_record or None
	return entry


def _pool_dispatch(recipe: dict) -> str:
	"""What a task falls back to when its named assignee could not be resolved.

	NEVER `Dispatched` WITH NOBODY ON IT. That combination is a task that sits in
	Available which no worker is allowed to claim — visible, urgent, and
	unreachable, which is the worst of the three ways for dispatch to fail.
	"""
	mode = str(recipe.get("dispatch") or "Either")
	if mode == DISPATCH_DISPATCHED and recipe.get("assigned_to_expression"):
		return "Self-pick" if recipe.get("skill") else "Either"
	return mode


#: The registers an alert's source record can be a PLACE in. An alert about a
#: certificate points at a certificate, and a certificate is not somewhere
#: anybody can be sent.
_DISPATCHABLE_LOCATIONS = ("Housing Unit", "Field", "Irrigation Zone", "Parcel")


# ── 12. report_field_task ───────────────────────────────────────────────────
#: v0.23.0. Field-Initiated Tasks: a worker in the field becomes a compliance
#: sensor. The field report IS the work order — no separate "Issue" or "Ticket"
#: doctype. Photo-taking IS ticket-creation IS dispatch entry, all one act.

FIELD_REPORT_LIMIT = 5
FIELD_REPORT_WINDOW_SECONDS = 3600
FIELD_REPORT_PENALTY_HOURS = 24

#: Urgency values a field worker may choose. Critical is restricted to
#: foreman/manager roles to prevent alarm inflation — every field worker
#: believing their problem is Critical is how Critical stops meaning anything.
_WORKER_URGENCY = ("Normal", "High")

#: Roles that may use Critical urgency on a field report.
_CRITICAL_ROLES = frozenset({"Foreman", "Farm Manager"})


def _field_report_count(worker: str) -> int:
	"""Reports this worker has filed in the current window."""
	cutoff = frappe.utils.add_to_date(frappe.utils.now(), hours=-1, as_string=True, as_datetime=True)
	return frappe.db.count(
		FARM_TASK,
		filters={
			"origin": ORIGIN_FIELD_REPORTED,
			"reported_by": worker,
			"reported_at": (">=", cutoff),
		},
	)


def _is_penalty_active(worker: str) -> bool:
	"""Whether a foreman dismissed one of this worker's reports in the last 24h."""
	cutoff = frappe.utils.add_to_date(
		frappe.utils.now(), hours=-FIELD_REPORT_PENALTY_HOURS, as_string=True, as_datetime=True
	)
	return bool(
		frappe.db.exists(
			FARM_TASK,
			{
				"origin": ORIGIN_FIELD_REPORTED,
				"reported_by": worker,
				"state": CANCELLED,
				"modified": (">=", cutoff),
			},
		)
	)


def report_field_task(args: dict) -> ToolResult:
	"""A worker in the field flags a problem on the spot.

	THE FIELD REPORT IS THE WORK ORDER. No separate doctype, no two-step
	process, no gap between seeing and dispatching. The worker taps, snaps,
	describes, and the task is in the pool in one act.

	ANTI-SPAM: 5 per worker per hour. A foreman dismissing a report with
	reason 'not a real issue' counts against the reporter's limit for the
	next 24h. Photo required — a report without evidence is a rumour.

	URGENCY IS CAPPED. Workers may choose Normal or High. Critical is
	reserved for foreman/manager roles, because every worker believing their
	problem is Critical is how Critical stops meaning anything on a board.
	"""
	_require()
	company = _company(args)

	worker = _worker(args, "reported_by", required=True)
	worker_name = _worker_name(worker)

	# ── anti-spam ──────────────────────────────────────────────────────────
	if _is_penalty_active(worker):
		raise ToolError(
			f"{worker_name} ({worker}) had a field report dismissed as 'not a real issue' in the "
			f"last {FIELD_REPORT_PENALTY_HOURS} hours, which counts against their rate limit. "
			"Nothing was created."
		)
	count = _field_report_count(worker)
	if count >= FIELD_REPORT_LIMIT:
		raise ToolError(
			f"{worker_name} ({worker}) has already filed {count} field reports in the last hour. "
			f"The limit is {FIELD_REPORT_LIMIT}. Nothing was created."
		)

	# ── photo required ─────────────────────────────────────────────────────
	photo = as_str(args, "photo_file_token")
	if not photo:
		raise ToolError(
			"photo_file_token is required. A field report without a photograph is a rumour — the "
			"photo is what turns 'there is a problem' into evidence somebody can act on. Upload "
			"the photo first with stage_file_chunk / finalize_staged_file, then pass the file "
			"token here. Nothing was created."
		)
	if not frappe.db.exists("File", photo):
		raise ToolError(
			f"no File {photo!r} on this site. Upload the photo first with stage_file_chunk / "
			"finalize_staged_file, then pass the file token here. Nothing was created."
		)

	# ── urgency ────────────────────────────────────────────────────────────
	urgency = as_str(args, "urgency") or "Normal"
	if urgency == "Critical":
		caller_roles = set(frappe.get_roles(frappe.session.user or ""))
		if not (caller_roles & _CRITICAL_ROLES):
			raise ToolError(
				"Critical urgency on a field report is restricted to Foreman and Farm Manager "
				"roles. A field worker may choose Normal or High. Nothing was created."
			)
	if urgency not in ("Low", "Normal", "High", "Critical"):
		raise ToolError(
			f"urgency {urgency!r} is not one of: Low, Normal, High, Critical. Nothing was created."
		)

	# ── location ───────────────────────────────────────────────────────────
	location_doctype = as_str(args, "location_doctype")
	location = as_str(args, "location")
	if location and not location_doctype:
		raise ToolError(
			"location was given with no location_doctype. Pass the register it is in: "
			"'Housing Unit', 'Field', 'Irrigation Zone' or 'Parcel'. Nothing was created."
		)
	if location_doctype and location:
		if not compat.doctype_exists(location_doctype):
			raise ToolError(f"this site has no {location_doctype!r} DocType. Nothing was created.")
		if not frappe.db.exists(location_doctype, location):
			raise ToolError(f"no {location_doctype} called {location!r} on this site. Nothing was created.")

	# ── build the task ─────────────────────────────────────────────────────
	task_type = as_str(args, "task_type") or "Repair"
	skill_required = as_str(args, "skill_required")
	description = as_str(args, "description")
	task_name = description[:80] if description else f"Field report by {worker_name}"

	doc = frappe.new_doc(FARM_TASK)
	doc.task_name = task_name
	doc.task_type = as_choice(FARM_TASK, "task_type", task_type, "task_type")
	doc.urgency = urgency
	doc.origin = ORIGIN_FIELD_REPORTED
	doc.company = company
	doc.location_doctype = location_doctype or None
	doc.location = location or None
	doc.skill_required = skill_required
	doc.dispatch_mode = "Either"
	doc.state = AVAILABLE
	doc.notes = description
	doc.evidence_required = json.dumps({"photos": True, "findings_text": True})
	doc.reported_by = worker
	doc.reported_at = frappe.utils.now()
	doc.report_photo = photo
	doc.insert(ignore_permissions=True)

	described = _describe_task(dict(doc.as_dict()))
	return ToolResult(
		data={
			**described,
			"reported_by": worker,
			"reported_by_name": worker_name,
			"reported_at": str(doc.reported_at),
			"report_photo": photo,
		},
		summary=(
			f"field report {doc.name} ({doc.task_type}, {urgency}) "
			f"reported by {worker_name} at {doc.location or 'unspecified location'}"
		),
		docstatus_delta="none → 0 (created)",
	)
