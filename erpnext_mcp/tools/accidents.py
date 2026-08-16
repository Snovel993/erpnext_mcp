# SPDX-License-Identifier: MIT
"""Accident investigation: opened at the scene, finished over days.

29 CFR 1904 is the regulation. The design problem is not the regulation.

THE DESIGN PROBLEM IS THE FIRST TEN MINUTES. This record gets opened on a phone,
in a block, by a foreman whose attention is on somebody sitting on the ground.
What they can give in that moment is: what happened, where, who was hurt, who
saw it, what we did. What they cannot give is a root cause, a recordability
determination under 1904.7, or a corrective action — those take days, and asking
for them at the scene produces exactly one outcome, which is that nobody opens
the record until the evening. The account written in the evening is worth a
fraction of the one written at the scene, and that is the whole reason this is
four calls rather than one form:

    create_accident_report          what is known now
    update_accident_investigation   what was learned since, as many times as it takes
    close_accident_investigation    the corrective action, and the date to check it
    get / list                      reading it back

MULTI-DAY IS THE DEFAULT AND NOT A FEATURE. There is no auto-close, nothing
expires, and the crew clock does not touch this: `end_shift` ends a SHIFT, and
an investigation is not a shift. A record left In Progress on Friday is In
Progress on Monday, with Friday's narrative still stamped Friday.

THE NARRATIVE IS THE INVESTIGATION. Not a description field somebody overwrites
as they learn more — that would destroy the one property a hearing cares about,
which is that Monday's account was written on Monday. Every update appends;
nothing is edited. A four-day investigation is a dozen entries by three people,
each stamped, each attributed, some of them spoken into a phone at a scene and
transcribed on the handset. See `tools/narrative.py`.

RECORDABILITY IS A PERSON'S DETERMINATION AND THIS APP DOES NOT INFER IT. It
would be easy to map severity onto the 300 log and wrong: 1904.7 turns on
medical treatment beyond first aid, first aid is a defined list, and the
consequence of getting it wrong is a citation. So `osha_recordable` defaults to
`Undetermined`, an investigation cannot close while it says so, and changing it
requires the BASIS — the sentence an inspector will ask for.

SUB-TASKS ARE HOW THE WORK GETS DONE. "Interview the witness", "pull the camera
footage", "write the root cause" are Farm Tasks with this report as their
parent, each with its own assignee and its own clock — see
`dispatch.subtasks_of`. The investigation does not close while one is open.
"""

from __future__ import annotations

import frappe

from .. import compat, timezones
from ..args import as_bool, as_date, as_int, as_limit, as_str, resolve_company
from ..erpnext_mcp.doctype.accident_report.accident_report import (
	CLOSED,
	CORRECTIVE_PENDING,
	IMMEDIATELY_REPORTABLE,
	IN_PROGRESS,
	OPEN,
	STATUSES,
	UNDETERMINED,
)
from ..errors import ToolError
from ..result import ToolResult
from . import narrative

ACCIDENT = "Accident Report"
EMPLOYEE = "Employee"
FARM_TASK = "Farm Task"

REGISTER_CAP = 200

#: The steps an investigation is usually broken into. Offered by
#: `create_accident_report` as a suggestion and raised only when asked for —
#: a server that silently created four tasks on every near miss would fill a
#: dispatch board with work nobody chose.
SUGGESTED_SUBTASKS = (
	("Interview witnesses", "Take an account from everybody who saw it, while they still remember."),
	("Photograph the scene", "Before anything is moved, tidied or repaired."),
	("Inspect the equipment", "The machine, the guard, the ladder — whatever was involved."),
	("Determine root cause", "Past the immediate cause. Why was it possible?"),
	("Write corrective actions", "What changes so it does not happen again."),
)

_FIELDS = (
	"name",
	"status",
	"severity",
	"occurred_at",
	"reported_at",
	"company",
	"reported_by",
	"reported_by_name",
	"investigation_lead",
	"incident_description",
	"location_doctype",
	"location",
	"location_description",
	"asset",
	"injured_person",
	"injured_person_name",
	"injury_type",
	"body_part",
	"medical_treatment",
	"treatment_facility",
	"days_away_from_work",
	"days_restricted_duty",
	"immediate_actions",
	"osha_recordable",
	"osha_determination_basis",
	"osha_determined_by",
	"osha_determined_on",
	"osha_form_301_filed",
	"reportable_to_osha_within_24h",
	"root_cause",
	"contributing_factors",
	"corrective_actions",
	"corrective_actions_completed_on",
	"followup_date",
	"closed_on",
	"closed_by",
	"closure_summary",
	"creation",
	"owner",
)


def _require() -> None:
	compat.require_doctype(
		ACCIDENT,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _report_row(name: str) -> dict:
	if not frappe.db.exists(ACCIDENT, name):
		raise ToolError(
			f"no {ACCIDENT} called {name!r} on this site. list_accident_reports has the register."
		)
	return dict(
		frappe.db.get_value(ACCIDENT, name, compat.existing_fields(ACCIDENT, _FIELDS), as_dict=True) or {}
	)


def _witnesses(name: str) -> list:
	try:
		doc = frappe.get_doc(ACCIDENT, name)
	except Exception:  # pragma: no cover
		return []
	out = []
	for row in doc.get("witnesses") or []:
		row = dict(row)
		out.append(
			{
				"witness_name": row.get("witness_name"),
				"employee": row.get("employee") or None,
				"role": row.get("role") or "Witness",
				"contact": row.get("contact") or None,
				"statement_taken": bool(frappe.utils.cint(row.get("statement_taken"))),
				"statement_taken_on": str(row.get("statement_taken_on") or "") or None,
			}
		)
	return out


def _describe(row: dict) -> dict:
	occurred = str(row.get("occurred_at") or "")
	reported = str(row.get("reported_at") or "")
	lag = None
	if occurred and reported:
		try:
			lag = round(float(frappe.utils.time_diff_in_seconds(reported, occurred)) / 3600.0, 2)
		except Exception:  # pragma: no cover - an unparseable stored timestamp
			lag = None
	return {
		"name": row.get("name"),
		"status": row.get("status") or OPEN,
		"severity": row.get("severity") or None,
		"occurred_at": occurred or None,
		"reported_at": reported or None,
		"reporting_lag_hours": lag,
		"company": row.get("company") or None,
		"reported_by": row.get("reported_by") or None,
		"reported_by_name": row.get("reported_by_name") or row.get("reported_by") or None,
		"investigation_lead": row.get("investigation_lead") or None,
		"incident_description": row.get("incident_description") or None,
		"location_doctype": row.get("location_doctype") or None,
		"location": row.get("location") or None,
		"location_description": row.get("location_description") or None,
		"asset": row.get("asset") or None,
		"injured_person": row.get("injured_person") or None,
		"injured_person_name": row.get("injured_person_name") or row.get("injured_person") or None,
		"injury_type": row.get("injury_type") or None,
		"body_part": row.get("body_part") or None,
		"medical_treatment": row.get("medical_treatment") or None,
		"treatment_facility": row.get("treatment_facility") or None,
		"days_away_from_work": int(row.get("days_away_from_work") or 0),
		"days_restricted_duty": int(row.get("days_restricted_duty") or 0),
		"immediate_actions": row.get("immediate_actions") or None,
		"osha_recordable": row.get("osha_recordable") or UNDETERMINED,
		"osha_determination_basis": row.get("osha_determination_basis") or None,
		"osha_determined_by": row.get("osha_determined_by") or None,
		"osha_determined_on": str(row.get("osha_determined_on") or "") or None,
		"osha_form_301_filed": bool(frappe.utils.cint(row.get("osha_form_301_filed"))),
		"immediately_reportable": bool(frappe.utils.cint(row.get("reportable_to_osha_within_24h"))),
		"root_cause": row.get("root_cause") or None,
		"contributing_factors": row.get("contributing_factors") or None,
		"corrective_actions": row.get("corrective_actions") or None,
		"corrective_actions_completed_on": str(row.get("corrective_actions_completed_on") or "") or None,
		"followup_date": str(row.get("followup_date") or "") or None,
		"closed_on": str(row.get("closed_on") or "") or None,
		"closed_by": row.get("closed_by") or None,
		"closure_summary": row.get("closure_summary") or None,
		"created_at": str(row.get("creation") or "") or None,
	}


def _outstanding(name: str, row: dict) -> list[dict]:
	"""What still has to happen before this can be closed. Named, not counted.

	READ BY THE CLOSE CALL AND BY EVERY GET, which is the point: an investigation
	that tells you on day three what it is waiting for is one somebody finishes.
	One that tells you only when you try to close it is one that sits Open until
	an inspector asks.
	"""
	from . import dispatch

	out = []
	if str(row.get("osha_recordable") or UNDETERMINED) == UNDETERMINED:
		out.append(
			{
				"item": "osha_determination",
				"detail": (
					"Recordability is still Undetermined. Under 29 CFR 1904.7 this turns on medical "
					"treatment beyond first aid, and the determination — with its basis — is what an "
					"inspector asks to see."
				),
			}
		)
	if not str(row.get("root_cause") or "").strip():
		out.append(
			{
				"item": "root_cause",
				"detail": "No root cause recorded. 'Operator error' is a stopping point, not a cause.",
			}
		)
	if not str(row.get("corrective_actions") or "").strip():
		out.append(
			{
				"item": "corrective_actions",
				"detail": (
					"No corrective actions recorded. An investigation that identified a cause and "
					"changed nothing is a filing exercise."
				),
			}
		)
	if not row.get("followup_date"):
		out.append(
			{
				"item": "followup_date",
				"detail": "No follow-up date set. A fix nobody verified is a fix that gets undone.",
			}
		)
	for witness in _witnesses(name):
		if not witness["statement_taken"]:
			out.append(
				{
					"item": "witness_statement",
					"detail": (
						f"No statement taken from {witness['witness_name']}. 'We knew they saw it and "
						"never asked' is the finding that survives every other one."
					),
				}
			)
	for child in dispatch.open_subtasks(name):
		out.append(
			{
				"item": "open_subtask",
				"detail": f"{child['name']} ({child['task_name']}) is still {child['state']}.",
			}
		)
	return out


# ── create_accident_report ──────────────────────────────────────────────────
def create_accident_report(args: dict) -> ToolResult:
	"""Open an incident record at the scene, with whatever is known now.

	ASKS FOR FIVE THINGS AND NO MORE: when, what, who was hurt, who saw it, what
	was done. Everything else on the doctype is filled in over the following
	days through `update_accident_investigation`. A create call that demanded a
	root cause would be a create call nobody makes until the evening.

	WITNESSES ARE ROWS, NOT A STRING, and that is the one piece of structure
	worth insisting on at a scene: a witness is somebody an investigator has to
	go back to, and 'we still have not interviewed Miguel' is the most useful
	thing a half-finished investigation knows. A comma-separated list cannot say
	it.
	"""
	_require()
	company = resolve_company(as_str(args, "company"))

	occurred = as_str(args, "occurred_at") or as_str(args, "occurred")
	if not occurred:
		raise ToolError(
			"occurred_at is required. Every clock this record is measured against runs from it — "
			"the gap to reporting, OSHA's 8-hour and 24-hour notification windows under 1904.39, "
			"and the days-away count. Nothing was created."
		)
	description = as_str(args, "incident_description") or as_str(args, "description")
	if not description:
		raise ToolError(
			"incident_description is required. Everything else here can be filled in over the "
			"following days; the account of what happened is the one part that is worth less every "
			"hour it is not written. Nothing was created."
		)

	doc = frappe.new_doc(ACCIDENT)
	doc.status = OPEN
	doc.severity = as_str(args, "severity") or "First Aid"
	doc.occurred_at = occurred
	doc.reported_at = as_str(args, "reported_at") or frappe.utils.now()
	doc.company = company or None
	doc.reported_by = as_str(args, "reported_by") or (
		frappe.session.user if hasattr(frappe, "session") else None
	)
	doc.reported_by_name = as_str(args, "reported_by_name") or doc.reported_by
	doc.investigation_lead = as_str(args, "investigation_lead") or None
	doc.incident_description = description
	doc.location_doctype = as_str(args, "location_doctype") or None
	doc.location = as_str(args, "location") or None
	doc.location_description = as_str(args, "location_description")
	doc.asset = as_str(args, "asset") or None
	doc.injured_person = as_str(args, "injured_person") or None
	doc.injured_person_name = as_str(args, "injured_person_name") or None
	doc.injury_type = as_str(args, "injury_type") or None
	doc.body_part = as_str(args, "body_part")
	doc.medical_treatment = as_str(args, "medical_treatment") or "None"
	doc.treatment_facility = as_str(args, "treatment_facility")
	doc.days_away_from_work = as_int(args, "days_away_from_work") or 0
	doc.days_restricted_duty = as_int(args, "days_restricted_duty") or 0
	doc.immediate_actions = as_str(args, "immediate_actions")
	doc.osha_recordable = UNDETERMINED

	if doc.injured_person and compat.doctype_exists(EMPLOYEE):
		if not frappe.db.exists(EMPLOYEE, doc.injured_person):
			raise ToolError(f"no Employee called {doc.injured_person!r} on this site. Nothing was created.")
		doc.injured_person_name = doc.injured_person_name or str(
			frappe.db.get_value(EMPLOYEE, doc.injured_person, "employee_name") or ""
		)

	if str(doc.severity) in IMMEDIATELY_REPORTABLE:
		doc.reportable_to_osha_within_24h = 1

	for witness in _witness_argument(args):
		doc.append("witnesses", witness)
	doc.insert(ignore_permissions=True)

	account = as_str(args, "narrative")
	if account:
		narrative.append_note(
			ACCIDENT,
			doc.name,
			"investigation_notes",
			{
				"note_type": "Note",
				"author": doc.reported_by,
				"author_name": doc.reported_by_name,
				"written_at": frappe.utils.now(),
				"source_type": narrative.SOURCE_TYPED,
				"source_language": as_str(args, "source_language") or None,
				"narrative": account,
			},
		)

	described = _describe(dict(doc.as_dict()))
	urgent = []
	# READ THROUGH `cint`. A Frappe Check arrives as the STRING "0" from a
	# default and `bool("0")` is True — the trap `settings.py` documents for the
	# tool switches. Read naively, every first-aid case would come back carrying
	# a fatality's telephone obligation, which is the fastest way to teach a
	# foreman to ignore the banner.
	if frappe.utils.cint(doc.reportable_to_osha_within_24h):
		hours = 8 if str(doc.severity) == "Fatality" else 24
		urgent.append(
			f"29 CFR 1904.39: a {doc.severity} must be reported to OSHA BY TELEPHONE within "
			f"{hours} hours of the employer learning of it — 1-800-321-OSHA, or the local area "
			"office. This record does not make that call and cannot. The clock runs from when the "
			"employer learned, not from this timestamp."
		)

	return ToolResult(
		data={
			**described,
			"witnesses": _witnesses(doc.name),
			"outstanding": _outstanding(doc.name, dict(doc.as_dict())),
			"urgent_obligations": urgent,
			"suggested_subtasks": [{"task_name": title, "why": why} for title, why in SUGGESTED_SUBTASKS],
			"message_key": "accident.created",
			"note": (
				"Opened with what is known now. Root cause, recordability and corrective actions "
				"are filled in over the following days with update_accident_investigation — this "
				"record does not expect a single session and nothing closes it automatically. "
				"Raise the steps as sub-tasks with create_farm_task(parent_task=this report) so "
				"each has its own owner and clock."
			),
			**timezones.Renderer(args).block(),
		},
		summary=(
			f"accident report {doc.name} opened: {doc.severity}"
			+ (f", {described['injured_person_name']}" if described["injured_person_name"] else "")
			+ (f" — {len(urgent)} immediate obligation(s)" if urgent else "")
		),
		docstatus_delta="none → 0 (created)",
	)


def _witness_argument(args: dict) -> list[dict]:
	raw = args.get("witnesses")
	if raw in (None, ""):
		return []
	if isinstance(raw, str):
		# A comma-separated string is accepted and split, because a handset that
		# has only a text box should not be refused — but each name becomes a ROW,
		# so the outstanding-statement flag works either way.
		raw = [{"witness_name": part.strip()} for part in raw.split(",") if part.strip()]
	if not isinstance(raw, list):
		raise ToolError(
			'witnesses must be a list like [{"witness_name": "Ana Ramos", "employee": "HR-EMP-001"}] '
			"or a comma-separated string of names. Nothing was created."
		)
	out = []
	for entry in raw:
		if isinstance(entry, str):
			entry = {"witness_name": entry}
		if not isinstance(entry, dict):
			raise ToolError(f"each witness must be a name or an object, got {type(entry).__name__}.")
		name = str(entry.get("witness_name") or entry.get("name") or "").strip()
		if not name:
			continue
		out.append(
			{
				"witness_name": name,
				"employee": str(entry.get("employee") or "").strip() or None,
				"role": str(entry.get("role") or "Witness").strip(),
				"contact": str(entry.get("contact") or "").strip() or None,
				"statement_taken": 1 if entry.get("statement_taken") else 0,
				"statement_taken_on": entry.get("statement_taken_on") or None,
			}
		)
	return out


# ── update_accident_investigation ───────────────────────────────────────────
def update_accident_investigation(args: dict) -> ToolResult:
	"""Add what was learned. Called as many times as the investigation takes.

	THE NARRATIVE APPENDS AND THE COLUMNS OVERWRITE, and the split is deliberate.
	`root_cause` is a conclusion and there is one of it — a revised conclusion
	replaces the earlier one, and the earlier one survives in the narrative where
	it was written. The narrative never overwrites, because "what did we think on
	Tuesday" is a question a hearing asks.

	CHANGING RECORDABILITY REQUIRES THE BASIS. Moving `osha_recordable` off
	Undetermined without saying why would put an unexplained determination on the
	300 log, which is the one an inspector asks about.
	"""
	_require()
	name = as_str(args, "report", required=True) or as_str(args, "name")
	row = _report_row(name)
	if row.get("status") == CLOSED and not as_bool(args, "reopen", False):
		raise ToolError(
			f"{name} was closed on {row.get('closed_on')}. Adding findings to a closed "
			"investigation without saying so would change a record somebody signed off. Send "
			"reopen=true with a reason in the narrative if new information has arrived. Nothing "
			"was changed."
		)

	doc = frappe.get_doc(ACCIDENT, name)
	changed = {}

	def _stage(field: str, value):
		if value in (None, ""):
			return
		before = doc.get(field)
		if str(before or "") == str(value):
			return
		changed[field] = [before or None, value]
		doc.set(field, value)

	for field in (
		"root_cause",
		"contributing_factors",
		"corrective_actions",
		"immediate_actions",
		"investigation_lead",
		"treatment_facility",
		"injury_type",
		"body_part",
		"location_description",
		"closure_summary",
	):
		_stage(field, as_str(args, field))
	for field in ("medical_treatment", "severity"):
		_stage(field, as_str(args, field))
	for field in ("followup_date", "corrective_actions_completed_on"):
		_stage(field, as_date(args, field))
	for field in ("days_away_from_work", "days_restricted_duty"):
		if args.get(field) is not None:
			_stage(field, as_int(args, field))

	recordable = as_str(args, "osha_recordable")
	if recordable:
		if recordable not in (UNDETERMINED, "Yes", "No"):
			raise ToolError(
				f"osha_recordable must be Undetermined, Yes or No, not {recordable!r}. Nothing was changed."
			)
		basis = as_str(args, "osha_determination_basis")
		if recordable != UNDETERMINED and not (basis or doc.osha_determination_basis):
			raise ToolError(
				"osha_determination_basis is required when the recordability is decided. "
				"'Medical treatment beyond first aid — sutures — under 1904.7(b)(5)' is a "
				"determination an inspector can check; 'No' on its own is one they will ask about. "
				"Nothing was changed."
			)
		_stage("osha_recordable", recordable)
		if basis:
			_stage("osha_determination_basis", basis)
		doc.osha_determined_by = as_str(args, "osha_determined_by") or (
			frappe.session.user if hasattr(frappe, "session") else None
		)
		doc.osha_determined_on = frappe.utils.now()

	if as_bool(args, "osha_form_301_filed", None) is not None:
		doc.osha_form_301_filed = 1 if as_bool(args, "osha_form_301_filed", False) else 0

	for witness in _witness_argument(args):
		existing = [str(row.get("witness_name")) for row in doc.get("witnesses") or []]
		if witness["witness_name"] not in existing:
			doc.append("witnesses", witness)
			changed.setdefault("witnesses_added", []).append(witness["witness_name"])

	statement_from = as_str(args, "statement_taken_from")
	if statement_from:
		found = False
		for witness_row in doc.get("witnesses") or []:
			if str(witness_row.get("witness_name")) == statement_from:
				witness_row.set("statement_taken", 1) if hasattr(witness_row, "set") else witness_row.update(
					{"statement_taken": 1}
				)
				(
					witness_row.set("statement_taken_on", frappe.utils.now())
					if hasattr(witness_row, "set")
					else witness_row.update({"statement_taken_on": frappe.utils.now()})
				)
				found = True
		if not found:
			raise ToolError(
				f"{statement_from!r} is not a witness on this report. Add them with `witnesses` "
				"first. Nothing was changed."
			)
		changed["statement_taken_from"] = statement_from

	# READ BEFORE THE STATUS BRANCH BELOW USES IT. A narrative entry IS
	# investigative work — somebody took an account, or wrote down what they
	# found — so an update carrying only a narrative moves an Open report to In
	# Progress exactly as one carrying a root cause does.
	account = as_str(args, "narrative") or as_str(args, "findings")

	status = as_str(args, "status")
	if status:
		if status not in STATUSES:
			raise ToolError(f"status must be one of {', '.join(STATUSES)}, not {status!r}.")
		if status == CLOSED:
			raise ToolError(
				"an investigation is closed with close_accident_investigation, not by setting a "
				"status. Closing checks that the corrective actions, the follow-up date and the "
				"recordability determination are actually there. Nothing was changed."
			)
		_stage("status", status)
	elif doc.status == OPEN and (changed or account):
		# Somebody added findings, so this is being worked. Moved rather than left
		# Open, because a board where everything reads Open cannot show what is
		# being investigated and what was filed and forgotten.
		_stage("status", IN_PROGRESS)

	if as_bool(args, "reopen", False) and doc.status == CLOSED:
		_stage("status", CORRECTIVE_PENDING)
		doc.closed_on = None
		doc.closed_by = None

	if not changed and not account:
		raise ToolError(
			"nothing to update. Pass a finding, a root cause, a corrective action, a witness "
			"statement, or a narrative entry. Nothing was changed."
		)

	doc.save(ignore_permissions=True)

	if account:
		narrative.append_note(
			ACCIDENT,
			name,
			"investigation_notes",
			{
				"note_type": as_str(args, "note_type") or "Finding",
				"author": as_str(args, "author")
				or (frappe.session.user if hasattr(frappe, "session") else None),
				"author_name": as_str(args, "author_name") or None,
				"written_at": frappe.utils.now(),
				"source_type": narrative.SOURCE_TYPED,
				"source_language": as_str(args, "source_language") or None,
				"narrative": account,
			},
		)

	fresh = _report_row(name)
	outstanding = _outstanding(name, fresh)
	return ToolResult(
		data={
			**_describe(fresh),
			"changed": {key: value for key, value in changed.items()},
			"narrative_appended": bool(account),
			"witnesses": _witnesses(name),
			"outstanding": outstanding,
			"ready_to_close": not outstanding,
			"note": (
				"Findings are appended, conclusions are replaced. The earlier conclusion survives "
				"in the narrative, stamped when it was written — which is what makes this a record "
				"of an investigation rather than of its ending."
			),
		},
		summary=(
			f"{name} updated ({len(changed)} field(s)"
			+ (", narrative appended" if account else "")
			+ f"), {len(outstanding)} item(s) outstanding"
		),
		docstatus_delta="0 → 0 (updated)",
	)


# ── close_accident_investigation ────────────────────────────────────────────
def close_accident_investigation(args: dict) -> ToolResult:
	"""Close it — with the corrective action, the determination and a date to check.

	THE THREE CHECKS ARE THE CLOSE. An investigation closed with no corrective
	action, no recordability determination and no follow-up is one that will not
	survive being read, and closing is the moment that becomes permanent. Each
	refusal names what is missing and why, because "cannot close" is useless.
	"""
	_require()
	name = as_str(args, "report", required=True) or as_str(args, "name")
	row = _report_row(name)
	if row.get("status") == CLOSED:
		raise ToolError(f"{name} was already closed on {row.get('closed_on')}. Nothing was changed.")

	doc = frappe.get_doc(ACCIDENT, name)
	for field in ("corrective_actions", "closure_summary", "root_cause"):
		value = as_str(args, field)
		if value:
			doc.set(field, value)
	followup = as_date(args, "followup_date")
	if followup:
		doc.followup_date = followup
	completed = as_date(args, "corrective_actions_completed_on")
	if completed:
		doc.corrective_actions_completed_on = completed

	recordable = as_str(args, "osha_recordable")
	if recordable:
		basis = as_str(args, "osha_determination_basis")
		if recordable != UNDETERMINED and not (basis or doc.osha_determination_basis):
			raise ToolError(
				"osha_determination_basis is required when the recordability is decided. Nothing was changed."
			)
		doc.osha_recordable = recordable
		if basis:
			doc.osha_determination_basis = basis
		doc.osha_determined_by = as_str(args, "osha_determined_by") or (
			frappe.session.user if hasattr(frappe, "session") else None
		)
		doc.osha_determined_on = frappe.utils.now()

	# THE OUTSTANDING LIST IS CHECKED BEFORE THE CONTROLLER GETS IT, so the
	# refusal names everything at once rather than one thing per attempt. A close
	# that fails four times in a row, each naming one more missing field, is how
	# somebody learns to stop closing things.
	blocking = _outstanding(name, dict(doc.as_dict()))
	if blocking:
		lines = "; ".join(f"{item['item']} — {item['detail']}" for item in blocking)
		raise ToolError(
			f"{name} cannot be closed yet. {len(blocking)} thing(s) outstanding: {lines} Nothing was changed."
		)

	doc.status = CLOSED
	doc.closed_on = frappe.utils.now()
	doc.closed_by = as_str(args, "closed_by") or (frappe.session.user if hasattr(frappe, "session") else None)
	doc.save(ignore_permissions=True)

	narrative.append_note(
		ACCIDENT,
		name,
		"investigation_notes",
		{
			"note_type": "Corrective Action",
			"author": doc.closed_by,
			"author_name": as_str(args, "closed_by_name") or None,
			"written_at": frappe.utils.now(),
			"source_type": narrative.SOURCE_TYPED,
			"narrative": (
				f"Investigation closed. Corrective actions: {doc.corrective_actions} "
				f"Follow-up set for {doc.followup_date}."
			),
		},
	)

	return ToolResult(
		data={
			**_describe(_report_row(name)),
			"note": (
				f"Closed. The follow-up on {doc.followup_date} is what makes the corrective action "
				"real — an investigation nobody went back to is one where the guard is off again "
				"by August. Reopen with update_accident_investigation(reopen=true) if new "
				"information arrives."
			),
			"message_key": "accident.closed",
		},
		summary=f"{name} closed by {doc.closed_by}, follow-up {doc.followup_date}",
		docstatus_delta=f"{row.get('status')} → {CLOSED}",
	)


# ── get_accident_report ─────────────────────────────────────────────────────
def get_accident_report(args: dict) -> ToolResult:
	"""One investigation in full: the record, the narrative, the steps, the gaps."""
	_require()
	name = as_str(args, "report", required=True) or as_str(args, "name")
	row = _report_row(name)
	described = _describe(row)

	from . import dispatch

	clock = timezones.Renderer(args)
	clock.add(described, "occurred_at", "reported_at", "closed_on", "osha_determined_on")

	notes = narrative.describe_notes(ACCIDENT, name, "investigation_notes")
	for note in notes:
		clock.add(note, "written_at")

	# WHICH DAYS WERE WORKED. An investigation's narrative grouped by date is the
	# activity log a multi-day record owes its reader — "nothing happened between
	# the 3rd and the 11th" is a finding about the investigation.
	by_day: dict = {}
	for note in notes:
		day = str(note.get("written_at") or "")[:10] or "(undated)"
		by_day.setdefault(day, []).append(note)

	return ToolResult(
		data={
			**described,
			"witnesses": _witnesses(name),
			"witness_count": len(_witnesses(name)),
			"notes": notes,
			"note_count": len(notes),
			"activity_by_day": {
				day: {
					"entry_count": len(entries),
					"authors": sorted({entry["author_name"] for entry in entries if entry["author_name"]}),
				}
				for day, entries in sorted(by_day.items())
			},
			"days_active": len(by_day),
			**dispatch.subtask_summary(name),
			"outstanding": _outstanding(name, row),
			"ready_to_close": not _outstanding(name, row),
			**clock.block(),
		},
		summary=(
			f"{name}: {described['severity']}, {described['status']}, "
			f"{len(notes)} narrative entrie(s) over {len(by_day)} day(s)"
		),
	)


# ── list_accident_reports ───────────────────────────────────────────────────
def list_accident_reports(args: dict) -> ToolResult:
	"""The register, filterable by status, severity, recordability and date."""
	_require()
	company = resolve_company(as_str(args, "company"))
	filters: dict = {}
	if company:
		filters["company"] = company

	status = as_str(args, "status")
	if status:
		if status not in STATUSES:
			raise ToolError(f"status must be one of {', '.join(STATUSES)}, not {status!r}.")
		filters["status"] = status
	elif as_bool(args, "open_only", False):
		filters["status"] = ("!=", CLOSED)

	severity = as_str(args, "severity")
	if severity:
		filters["severity"] = severity
	recordable = as_str(args, "osha_recordable")
	if recordable:
		filters["osha_recordable"] = recordable

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["occurred_at"] = ("between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"])
	elif from_date:
		filters["occurred_at"] = (">=", f"{from_date} 00:00:00")
	elif to_date:
		filters["occurred_at"] = ("<=", f"{to_date} 23:59:59")

	rows = (
		frappe.db.get_all(
			ACCIDENT,
			filters=filters,
			fields=compat.existing_fields(ACCIDENT, _FIELDS),
			order_by="occurred_at desc",
			limit=min(as_limit(args), REGISTER_CAP),
		)
		or []
	)
	reports = [_describe(dict(row)) for row in rows]

	clock = timezones.Renderer(args)
	for report in reports:
		clock.add(report, "occurred_at", "reported_at", "closed_on")

	open_reports = [report for report in reports if report["status"] != CLOSED]
	undetermined = [report for report in reports if report["osha_recordable"] == UNDETERMINED]
	recordables = [report for report in reports if report["osha_recordable"] == "Yes"]

	return ToolResult(
		data={
			"company": company,
			"report_count": len(reports),
			"reports": reports,
			"open_count": len(open_reports),
			"open": [report["name"] for report in open_reports],
			"osha_recordable_count": len(recordables),
			# NAMED RATHER THAN COUNTED. Every one of these is a decision nobody
			# has made, and the 300 log is missing an entry or carrying one it
			# should not until somebody does.
			"undetermined_recordability": [report["name"] for report in undetermined],
			"undetermined_count": len(undetermined),
			"days_away_total": sum(report["days_away_from_work"] for report in reports),
			**clock.block(),
		},
		summary=(
			f"{len(reports)} accident report(s), {len(open_reports)} open, "
			f"{len(recordables)} OSHA-recordable"
			+ (f", {len(undetermined)} undetermined" if undetermined else "")
		),
	)
