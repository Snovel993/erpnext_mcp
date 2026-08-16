# SPDX-License-Identifier: MIT
"""Progressive discipline: the chain that is the defence.

IN A WRONGFUL-TERMINATION CLAIM THE DOCUMENTATION IS THE CASE. The question a
tribunal asks is almost never whether the final step was deserved. It is whether
the employer can produce a documented, escalating, acknowledged series of steps
that told this person what was wrong, what had to change, by when, and what
would happen if it did not — and then whether somebody actually went back and
looked. An employer who can produce that chain generally wins. An employer with
a termination memo and nothing before it generally does not, however true the
memo is.

So the shape of this module follows the shape of the defence:

  * EVERY STEP AFTER THE FIRST NAMES THE ONE BEFORE IT. `prior_record` is filled
    in by this app off the employee's own active history, not by whoever is
    typing. A chain assembled by hand is a chain with a link missing on the day
    somebody is in a hurry, and the missing link is what the claim is about.
  * A SKIP HAS TO BE EXPLAINED. Going from a verbal warning straight to a final
    one may be entirely correct — a safety violation is not a progressive matter
    — but "why was this person's second step everyone else's fourth" is asked
    every time, and the answer is worth more written on the day than
    reconstructed two years later.
  * EVERY STEP STATES AN EXPECTATION AND A DATE. Enforced by the doctype rather
    than here, because a record without them is indefensible however it was
    created.
  * A REFUSAL TO SIGN IS AN OUTCOME, NOT A GAP. An employee may decline. What
    the file may not contain is silence presented as agreement — so
    `acknowledge_discipline_record` takes either a signature or an explicit
    refusal with a witness, and the doctype refuses the third possibility.

WHAT THIS MODULE DOES NOT DO. It does not decide whether discipline is
warranted, it does not compute the next step, and it does not expire anything on
a schedule. The look-back window that discounts an old warning is a policy
decision that differs by employer and by state; `expire_discipline_record` is
the call somebody makes when their policy says so, and its absence is not this
app quietly counting a three-year-old verbal warning toward a termination.

THE HISTORY IS THE PRODUCT. `get_discipline_report` is what an HR manager hands
a lawyer: the chain in order, what each step asked for, whether it was
acknowledged, whether anybody followed up, and — stated plainly — where the gaps
are. A report that only listed the steps would leave the reader to find the
holes, and the holes are the thing that decides the case.
"""

from __future__ import annotations

import frappe

from .. import compat, timezones
from ..args import as_bool, as_date, as_limit, as_str, resolve_company
from ..erpnext_mcp.doctype.discipline_record.discipline_record import (
	ACTIVE,
	DISCIPLINE_TYPES,
	EXPIRED,
	RESCINDED,
	STATUSES,
	TERMINATION,
	severity,
)
from ..errors import ToolError
from ..result import ToolResult
from . import narrative

DISCIPLINE = "Discipline Record"
EMPLOYEE = "Employee"

#: Most steps one chain carries. An employee with more than this is not a
#: discipline question any more, and a list nobody can read is not a defence.
CHAIN_CAP = 100

#: Most rows one register listing returns.
REGISTER_CAP = 200

_FIELDS = (
	"name",
	"employee",
	"employee_name",
	"discipline_type",
	"status",
	"company",
	"incident_date",
	"issued_on",
	"issued_by",
	"issued_by_name",
	"prior_record",
	"step_number",
	"supersedes_note",
	"incident_description",
	"policy_violated",
	"witnesses",
	"expected_improvement",
	"followup_date",
	"consequence_of_no_improvement",
	"suspension_start",
	"suspension_end",
	"employee_acknowledged",
	"employee_signature",
	"employee_acknowledged_on",
	"employee_declined_to_sign",
	"employee_statement",
	"manager_signature",
	"manager_signed_on",
	"sealed_pdf",
	"creation",
	"owner",
)


def _require() -> None:
	compat.require_doctype(
		DISCIPLINE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _employee_argument(args: dict, verb: str) -> tuple[str, str]:
	"""`(employee, employee_name)`, checked against the register where there is one.

	CHECKED BUT NOT REQUIRED TO EXIST AS A LINK, for the reason the doctype
	stores a Data field: Frappe HR is not a dependency, and a site without an
	Employee register still has people and still disciplines them. Where the
	register IS present the id has to be in it — a discipline record against a
	name nobody can resolve is a record that cannot be served on anybody.
	"""
	employee = as_str(args, "employee", required=True)
	name = as_str(args, "employee_name")
	if compat.doctype_exists(EMPLOYEE):
		if not frappe.db.exists(EMPLOYEE, employee):
			raise ToolError(
				f"no Employee called {employee!r} on this site. A discipline record names a person "
				f"who can be served with it. Nothing was {verb}."
			)
		name = name or str(frappe.db.get_value(EMPLOYEE, employee, "employee_name") or "")
	return employee, name or employee


def chain_for(employee: str, include_inactive: bool = False, limit: int = CHAIN_CAP) -> list[dict]:
	"""Every step for one person, oldest first. The chain itself.

	OLDEST FIRST, because a chain is read as an escalation and reversing it turns
	a progression into a list. `include_inactive` brings in Expired and Rescinded
	steps — off by default because those are the ones a policy says no longer
	count, and on for the report, where "there was a warning in 2024 and it aged
	out" is itself something a reader needs.
	"""
	if not compat.doctype_exists(DISCIPLINE):
		return []
	filters: dict = {"employee": employee}
	if not include_inactive:
		filters["status"] = ACTIVE
	try:
		rows = (
			frappe.db.get_all(
				DISCIPLINE,
				filters=filters,
				fields=compat.existing_fields(DISCIPLINE, _FIELDS),
				order_by="incident_date asc, creation asc",
				limit=limit,
			)
			or []
		)
	except Exception:  # pragma: no cover - a site shaping these columns differently
		return []
	return [dict(row) for row in rows]


def _describe(row: dict, today: str = "") -> dict:
	today = today or str(frappe.utils.today())
	followup = str(row.get("followup_date") or "") or None
	overdue = False
	days = None
	if followup:
		try:
			days = int(frappe.utils.date_diff(followup, today))
			overdue = days < 0
		except Exception:  # pragma: no cover - an unparseable stored date
			days = None
	acknowledged = bool(frappe.utils.cint(row.get("employee_acknowledged")))
	declined = bool(frappe.utils.cint(row.get("employee_declined_to_sign")))
	return {
		"name": row.get("name"),
		"employee": row.get("employee"),
		"employee_name": row.get("employee_name") or row.get("employee"),
		"discipline_type": row.get("discipline_type"),
		"severity": severity(str(row.get("discipline_type") or "")),
		"status": row.get("status") or ACTIVE,
		"company": row.get("company") or None,
		"incident_date": str(row.get("incident_date") or "") or None,
		"issued_on": str(row.get("issued_on") or "") or None,
		"issued_by": row.get("issued_by") or None,
		"issued_by_name": row.get("issued_by_name") or row.get("issued_by") or None,
		"prior_record": row.get("prior_record") or None,
		"step_number": int(row.get("step_number") or 0) or None,
		"escalation_note": row.get("supersedes_note") or None,
		"incident_description": row.get("incident_description") or None,
		"policy_violated": row.get("policy_violated") or None,
		"witnesses": row.get("witnesses") or None,
		"expected_improvement": row.get("expected_improvement") or None,
		"followup_date": followup,
		"days_until_followup": days,
		"followup_overdue": overdue,
		"consequence_of_no_improvement": row.get("consequence_of_no_improvement") or None,
		"suspension_start": str(row.get("suspension_start") or "") or None,
		"suspension_end": str(row.get("suspension_end") or "") or None,
		"employee_acknowledged": acknowledged,
		"employee_declined_to_sign": declined,
		"acknowledged_on": str(row.get("employee_acknowledged_on") or "") or None,
		"employee_statement": row.get("employee_statement") or None,
		"has_employee_signature": bool(row.get("employee_signature")),
		"has_manager_signature": bool(row.get("manager_signature")),
		"sealed_pdf": row.get("sealed_pdf") or None,
		"created_at": str(row.get("creation") or "") or None,
	}


def _gaps(steps: list[dict], today: str) -> list[dict]:
	"""Where this chain is weak, stated plainly. The half a report is FOR.

	A report that only listed the steps would leave the reader to find the holes,
	and the holes are what decides the case. Every entry names the record, what
	is missing, and why it matters — because "unacknowledged" means nothing to
	somebody who has not run a hearing.
	"""
	out = []
	for step in steps:
		if step["status"] != ACTIVE:
			continue
		if not (step["employee_acknowledged"] or step["employee_declined_to_sign"]):
			out.append(
				{
					"record": step["name"],
					"gap": "not_acknowledged",
					"detail": (
						f"{step['discipline_type']} of {step['incident_date']} has no acknowledgement "
						"and no note that the employee declined to sign. An undelivered warning is a "
						"note in a file rather than a step in a chain, and this is the first thing a "
						"claim looks for."
					),
				}
			)
		elif step["employee_declined_to_sign"] and not step["witnesses"]:
			out.append(
				{
					"record": step["name"],
					"gap": "refusal_unwitnessed",
					"detail": (
						f"{step['discipline_type']} of {step['incident_date']} records that the "
						"employee declined to sign, with no witness named. A refusal nobody witnessed "
						"is the employer's word for it."
					),
				}
			)
		if not step["has_manager_signature"]:
			out.append(
				{
					"record": step["name"],
					"gap": "unsigned_by_manager",
					"detail": (
						f"{step['discipline_type']} of {step['incident_date']} carries no manager "
						"signature. Who issued it is part of whether it was issued."
					),
				}
			)
		if step["followup_overdue"]:
			out.append(
				{
					"record": step["name"],
					"gap": "followup_missed",
					"detail": (
						f"The follow-up on {step['discipline_type']} was due {step['followup_date']}, "
						f"{abs(step['days_until_followup'] or 0)} day(s) ago. An unreviewed warning is "
						"what a claim points at to show the process was theatre."
					),
				}
			)
		if step["step_number"] and step["step_number"] > 1 and not step["prior_record"]:
			out.append(
				{
					"record": step["name"],
					"gap": "chain_broken",
					"detail": (
						f"{step['discipline_type']} is step {step['step_number']} and names no prior "
						"record. The chain is the defence and this is a link missing from it."
					),
				}
			)
	return out


# ── create_discipline_record ────────────────────────────────────────────────
def create_discipline_record(args: dict) -> ToolResult:
	"""File one step of progressive discipline, linked to the one before it.

	THE PRIOR RECORD IS FOUND, NOT ASKED FOR. Whoever is typing this has an
	employee in front of them and a thing that just happened; asking them which
	docname the last warning was is asking them to go and look, which is how a
	chain acquires a missing link. `prior_record` is the newest ACTIVE step for
	this person, and `step_number` follows from it.

	A SKIP IS ALLOWED AND HAS TO BE EXPLAINED. Jumping the queue may be entirely
	right — a safety violation is not a progressive matter — so this refuses the
	jump only until somebody says why, and then records the reason on the record
	where a reader will find it.

	A STEP AFTER A TERMINATION IS REFUSED OUTRIGHT. There is no chain past the
	end of employment, and a record filed after one is either about somebody who
	was rehired — in which case the new chain starts clean — or a mistake.
	"""
	_require()
	company = resolve_company(as_str(args, "company"))
	employee, employee_name = _employee_argument(args, "created")

	discipline_type = as_str(args, "discipline_type", required=True)
	if discipline_type not in DISCIPLINE_TYPES:
		raise ToolError(
			f"discipline_type must be one of {', '.join(DISCIPLINE_TYPES)}, not "
			f"{discipline_type!r}. The order is the escalation. Nothing was created."
		)

	history = chain_for(employee)
	prior = history[-1] if history else None
	if prior and str(prior.get("discipline_type")) == TERMINATION:
		raise ToolError(
			f"{employee_name} was terminated on {prior.get('issued_on')} ({prior.get('name')}). "
			"There is no step after the end of employment. If they were rehired, rescind or expire "
			"the old chain first — a new chain starts clean. Nothing was created."
		)

	step_number = (int(prior.get("step_number") or len(history)) + 1) if prior else 1
	escalation_note = as_str(args, "supersedes_note") or as_str(args, "escalation_reason")

	# THE SKIP CHECK. More than one rung at a time needs a sentence; the same
	# rung twice does not (a second written warning for a second incident is
	# ordinary), and a step DOWN does not either (a lesser step after a serious
	# one is somebody being generous, which nobody ever has to defend).
	if prior:
		jump = severity(discipline_type) - severity(str(prior.get("discipline_type") or ""))
		if jump > 1 and not escalation_note:
			raise ToolError(
				f"{employee_name}'s last step was a {prior.get('discipline_type')} and this is a "
				f"{discipline_type} — {jump} rungs up. That may be entirely right; a safety "
				"violation is not a progressive matter. But 'why was this person's step everyone "
				"else's fourth' is asked every time, and the answer is worth more written today "
				"than reconstructed in two years. Send supersedes_note with the reason. Nothing "
				"was created."
			)

	doc = frappe.new_doc(DISCIPLINE)
	doc.employee = employee
	doc.employee_name = employee_name
	doc.discipline_type = discipline_type
	doc.status = ACTIVE
	doc.company = company or None
	doc.incident_date = as_date(args, "incident_date") or str(frappe.utils.today())
	doc.issued_on = as_date(args, "issued_on") or str(frappe.utils.today())
	doc.issued_by = as_str(args, "issued_by") or (frappe.session.user if hasattr(frappe, "session") else None)
	doc.issued_by_name = as_str(args, "issued_by_name") or doc.issued_by
	doc.prior_record = prior.get("name") if prior else None
	doc.step_number = step_number
	doc.supersedes_note = escalation_note or None
	doc.incident_description = as_str(args, "incident_description") or as_str(args, "description")
	doc.policy_violated = as_str(args, "policy_violated")
	doc.witnesses = as_str(args, "witnesses")
	doc.expected_improvement = as_str(args, "expected_improvement")
	doc.followup_date = as_date(args, "followup_date")
	doc.consequence_of_no_improvement = as_str(args, "consequence_of_no_improvement")
	doc.suspension_start = as_date(args, "suspension_start")
	doc.suspension_end = as_date(args, "suspension_end")
	doc.employee_statement = as_str(args, "employee_statement")
	doc.insert(ignore_permissions=True)

	# The manager's own account of the conversation, where they gave one at the
	# same time. Appended rather than written into a column so it carries an
	# author and a timestamp like every other narrative entry.
	account = as_str(args, "narrative") or as_str(args, "conversation_notes")
	if account:
		narrative.append_note(
			DISCIPLINE,
			doc.name,
			"discipline_notes",
			{
				"note_type": "Conversation",
				"author": doc.issued_by,
				"author_name": doc.issued_by_name,
				"written_at": frappe.utils.now(),
				"source_type": narrative.SOURCE_TYPED,
				"source_language": as_str(args, "source_language") or None,
				"narrative": account,
			},
		)

	described = _describe(dict(doc.as_dict()))
	warnings = []
	if not prior and severity(discipline_type) > 1:
		warnings.append(
			f"This is the FIRST recorded step for {employee_name} and it is a {discipline_type} "
			"rather than a verbal warning. Where earlier conversations happened and were not "
			"written down, that gap is what a claim will find — record them now if they can be "
			"honestly dated, and say in supersedes_note why this level was right if they cannot."
		)
	if discipline_type == TERMINATION and len(history) < 2:
		warnings.append(
			f"This is a termination with {len(history)} prior step(s) on file. A termination at the "
			"end of an unbroken documented chain is defensible; the same termination with a short "
			"chain is the chain."
		)

	return ToolResult(
		data={
			**described,
			"chain_length": len(history) + 1,
			"prior_records": [step["name"] for step in history],
			"warnings": warnings or None,
			"message_key": "discipline.created",
		},
		summary=(
			f"{discipline_type} recorded for {employee_name} (step {step_number}"
			+ (f", following {prior['name']}" if prior else ", first on file")
			+ ")"
		),
		docstatus_delta="none → 0 (created)",
	)


# ── acknowledge_discipline_record ───────────────────────────────────────────
def acknowledge_discipline_record(args: dict) -> ToolResult:
	"""Record that the employee was told — or that they declined to sign.

	TWO OUTCOMES, BOTH RECORDED, AND NO THIRD. An employee may sign, or may
	refuse. What the file may not contain is silence presented as agreement, so
	this refuses an acknowledgement with neither a signature nor an explicit
	refusal, and refuses a refusal with no witness — a declining nobody saw is
	the employer's word for it.
	"""
	_require()
	name = as_str(args, "record", required=True) or as_str(args, "name")
	if not frappe.db.exists(DISCIPLINE, name):
		raise ToolError(f"no {DISCIPLINE} called {name!r} on this site. Nothing was changed.")

	doc = frappe.get_doc(DISCIPLINE, name)
	declined = bool(as_bool(args, "declined_to_sign", False))
	signature = as_str(args, "employee_signature") or as_str(args, "signature_file")
	witnesses = as_str(args, "witnesses")

	if declined:
		if not (witnesses or doc.witnesses):
			raise ToolError(
				"a refusal to sign needs a witness named. An employee is entitled to decline, and "
				"the record of that is only worth having if somebody else saw it — otherwise the "
				"file says the employer says they refused. Send witnesses. Nothing was changed."
			)
		doc.employee_declined_to_sign = 1
		doc.employee_acknowledged = 0
	else:
		if not signature:
			raise ToolError(
				"employee_signature is required, or declined_to_sign=true with a witness. Those "
				"are the two outcomes; an acknowledgement with neither is silence presented as "
				"agreement, which is the one thing this record may not contain. Nothing was "
				"changed."
			)
		doc.employee_signature = signature
		doc.employee_acknowledged = 1
		doc.employee_declined_to_sign = 0

	doc.employee_acknowledged_on = as_str(args, "acknowledged_on") or frappe.utils.now()
	if witnesses:
		doc.witnesses = witnesses
	statement = as_str(args, "employee_statement")
	if statement:
		doc.employee_statement = statement
	manager_signature = as_str(args, "manager_signature")
	if manager_signature:
		doc.manager_signature = manager_signature
		doc.manager_signed_on = frappe.utils.now()
	doc.save(ignore_permissions=True)

	return ToolResult(
		data={
			**_describe(dict(doc.as_dict())),
			"outcome": "declined_to_sign" if declined else "acknowledged",
			"message_key": ("discipline.declined_to_sign" if declined else "discipline.acknowledged"),
		},
		summary=(
			f"{doc.employee_name} "
			+ ("declined to sign" if declined else "acknowledged")
			+ f" {doc.discipline_type} {doc.name}"
		),
		docstatus_delta="0 → 0 (updated)",
	)


# ── get_discipline_record ───────────────────────────────────────────────────
def get_discipline_record(args: dict) -> ToolResult:
	"""One step in full, with its narrative and the step before it."""
	_require()
	name = as_str(args, "record", required=True) or as_str(args, "name")
	if not frappe.db.exists(DISCIPLINE, name):
		raise ToolError(f"no {DISCIPLINE} called {name!r} on this site.")
	row = dict(
		frappe.db.get_value(DISCIPLINE, name, compat.existing_fields(DISCIPLINE, _FIELDS), as_dict=True) or {}
	)
	described = _describe(row)
	clock = timezones.Renderer(args)
	clock.add(described, "acknowledged_on")

	notes = narrative.describe_notes(DISCIPLINE, name, "discipline_notes")
	for note in notes:
		clock.add(note, "written_at")

	prior = None
	if row.get("prior_record"):
		prior_row = frappe.db.get_value(
			DISCIPLINE, row["prior_record"], compat.existing_fields(DISCIPLINE, _FIELDS), as_dict=True
		)
		prior = _describe(dict(prior_row)) if prior_row else None

	return ToolResult(
		data={
			**described,
			"prior": prior,
			"notes": notes,
			"note_count": len(notes),
			**clock.block(),
		},
		summary=f"{described['discipline_type']} for {described['employee_name']} on {described['incident_date']}",
	)


# ── list_discipline_history ─────────────────────────────────────────────────
def list_discipline_history(args: dict) -> ToolResult:
	"""One employee's whole chain, in order."""
	_require()
	employee, employee_name = _employee_argument(args, "read")
	include_inactive = bool(as_bool(args, "include_inactive", True))
	today = str(frappe.utils.today())

	steps = [_describe(row, today) for row in chain_for(employee, include_inactive, as_limit(args))]
	clock = timezones.Renderer(args)
	for step in steps:
		clock.add(step, "acknowledged_on")

	active = [step for step in steps if step["status"] == ACTIVE]
	current = active[-1] if active else None

	return ToolResult(
		data={
			"employee": employee,
			"employee_name": employee_name,
			"step_count": len(steps),
			"active_step_count": len(active),
			"steps": steps,
			"current_level": current["discipline_type"] if current else None,
			"current_severity": current["severity"] if current else 0,
			"next_step_would_be": (
				DISCIPLINE_TYPES[min(current["severity"], len(DISCIPLINE_TYPES) - 1)]
				if current and current["severity"] < len(DISCIPLINE_TYPES)
				else (DISCIPLINE_TYPES[0] if not current else None)
			),
			"terminated": bool(current and current["discipline_type"] == TERMINATION),
			"overdue_followups": [step["name"] for step in active if step["followup_overdue"]],
			**clock.block(),
		},
		summary=(
			f"{employee_name}: {len(steps)} discipline record(s)"
			+ (f", currently at {current['discipline_type']}" if current else ", none active")
		),
	)


# ── get_discipline_report ───────────────────────────────────────────────────
def get_discipline_report(args: dict) -> ToolResult:
	"""The chain as a document somebody hands a lawyer — including its gaps.

	THE GAPS ARE THE POINT, and this is the same argument
	`export_insurance_schedule` makes about missing serial numbers: a report that
	only listed what is there leaves the reader to find what is not, and what is
	not there is what decides the case. Every unacknowledged step, every missed
	follow-up, every broken link is itemised with a sentence saying why it
	matters — so it can be fixed before a hearing rather than discovered during
	one.
	"""
	_require()
	employee, employee_name = _employee_argument(args, "read")
	today = str(frappe.utils.today())
	rows = chain_for(employee, include_inactive=True, limit=CHAIN_CAP)
	steps = [_describe(row, today) for row in rows]

	clock = timezones.Renderer(args)
	narratives = {}
	for step in steps:
		clock.add(step, "acknowledged_on")
		entries = narrative.describe_notes(DISCIPLINE, step["name"], "discipline_notes")
		for entry in entries:
			clock.add(entry, "written_at")
		if entries:
			narratives[step["name"]] = entries

	gaps = _gaps(steps, today)
	active = [step for step in steps if step["status"] == ACTIVE]
	acknowledged = [
		step for step in active if step["employee_acknowledged"] or step["employee_declined_to_sign"]
	]

	timeline = [
		{
			"date": step["incident_date"],
			"issued": step["issued_on"],
			"step": step["step_number"],
			"type": step["discipline_type"],
			"status": step["status"],
			"acknowledged": step["employee_acknowledged"] or step["employee_declined_to_sign"],
			"record": step["name"],
			"incident": step["incident_description"],
			"expected": step["expected_improvement"],
			"followup": step["followup_date"],
			"followup_met": not step["followup_overdue"],
		}
		for step in steps
	]

	return ToolResult(
		data={
			"employee": employee,
			"employee_name": employee_name,
			"generated_at": frappe.utils.now(),
			"step_count": len(steps),
			"active_step_count": len(active),
			"timeline": timeline,
			"steps": steps,
			"narratives": narratives,
			"acknowledgement_rate": (round(len(acknowledged) / len(active), 2) if active else None),
			"gap_count": len(gaps),
			"gaps": gaps,
			"chain_intact": not any(gap["gap"] == "chain_broken" for gap in gaps),
			"terminated": bool(active and active[-1]["discipline_type"] == TERMINATION),
			"assessment": (
				"No gaps found. Every active step is acknowledged or records a witnessed refusal, "
				"carries a manager signature, names its predecessor, and had its follow-up met."
				if not gaps
				else (
					f"{len(gaps)} gap(s) in this chain. Each is listed above with what is missing "
					"and why it matters. THESE ARE FIXABLE NOW AND NOT LATER: an acknowledgement "
					"obtained today for a warning issued in March is worth something; the same "
					"signature obtained after a claim is filed is worth very little."
				)
			),
			"note": (
				"This is a record of what is on file. It is not legal advice and it does not say "
				"whether any step was warranted — only whether the documentation of it is complete."
			),
			**clock.block(),
		},
		summary=(
			f"{employee_name}: {len(steps)} step(s), {len(gaps)} documentation gap(s)"
			+ (", chain intact" if not gaps else "")
		),
	)


# ── expire_discipline_record ────────────────────────────────────────────────
def expire_discipline_record(args: dict) -> ToolResult:
	"""Age a step out of the chain, or withdraw one on review.

	NOTHING EXPIRES ON A SCHEDULE IN THIS APP. The look-back window that
	discounts an old warning is a policy decision that differs by employer and by
	state, and a server quietly ageing steps out would be making that decision
	for somebody. This is the call an operator makes when their policy says so,
	and the record is kept — a rescinded warning is itself something an employee
	may point to.
	"""
	_require()
	name = as_str(args, "record", required=True) or as_str(args, "name")
	if not frappe.db.exists(DISCIPLINE, name):
		raise ToolError(f"no {DISCIPLINE} called {name!r} on this site. Nothing was changed.")

	status = as_str(args, "status") or EXPIRED
	if status not in (EXPIRED, RESCINDED):
		raise ToolError(
			f"status must be {EXPIRED} or {RESCINDED}, not {status!r}. A step is never deleted and "
			f"never returns to {ACTIVE} — the statuses are {', '.join(STATUSES)}. Nothing was changed."
		)
	reason = as_str(args, "reason", required=True)

	doc = frappe.get_doc(DISCIPLINE, name)
	if doc.status != ACTIVE:
		raise ToolError(f"{name} is already {doc.status}. Nothing was changed.")
	before = doc.status
	doc.status = status
	doc.save(ignore_permissions=True)

	narrative.append_note(
		DISCIPLINE,
		name,
		"discipline_notes",
		{
			"note_type": "Note",
			"author": as_str(args, "author") or (frappe.session.user if hasattr(frappe, "session") else None),
			"author_name": as_str(args, "author_name") or None,
			"written_at": frappe.utils.now(),
			"source_type": narrative.SOURCE_TYPED,
			"narrative": f"Status changed {before} → {status}: {reason}",
		},
	)

	return ToolResult(
		data={
			**_describe(dict(doc.as_dict())),
			"from_status": before,
			"reason": reason,
			"note": (
				"The record is kept. A step that aged out or was withdrawn is itself part of the "
				"history — an employee may point to a rescinded warning, and a chain that had one "
				"deleted cannot explain the gap where it was."
			),
		},
		summary=f"{name}: {before} → {status} ({reason})",
		docstatus_delta="0 → 0 (updated)",
	)
