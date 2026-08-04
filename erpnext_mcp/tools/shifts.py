# SPDX-License-Identifier: MIT
"""Forming a crew, logging what happened to it, and signing the shift off.

v0.19.3. SIX TOOLS THAT ARE ONE WORKFLOW, and the workflow has exactly one actor.
`start_shift` forms the crew, `add_worker_to_shift` and
`remove_worker_from_shift` amend it, `log_shift_event` records what the foreman
did about the conditions, and `end_shift` closes it with a signature and writes
the payroll rows. `list_shifts` and `get_shift` read it back.

────────────────────────────────────────────────────────────────────────────
WHY THE FOREMAN IS THE ONLY ACTOR
────────────────────────────────────────────────────────────────────────────

There is no `clock_in` here and there will not be one. Three reasons, argued at
length in `erpnext_mcp/shifts.py`, of which the first decides it: OAR
437-004-1131 puts the water, shade, rest-cycle and observation obligations on a
NAMED responsible person, and FSMA §112.161(b) asks that person to sign. A crew
of thirty each clocking themselves in is a shift with thirty people responsible
for the record, which is a shift with nobody responsible for it — and the
observable failure is that nobody logged the water break because everybody
assumed somebody else had.

Per-worker attendance is not lost to this. Every crew row carries its own
`joined_at` and `left_at`, `remove_worker_from_shift` sets the second rather than
deleting the row, and the close writes one Attendance record per person for
their own span.

────────────────────────────────────────────────────────────────────────────
`end_shift` REQUIRES A SIGNATURE AND THAT IS THE POINT OF `end_shift`
────────────────────────────────────────────────────────────────────────────

An unsigned close is just an UPDATE statement setting a timestamp. The signature
is what makes the close an attestation — the supervisor saying, at the moment
they say it, that this is what happened — and §112.161(b) asks for a review that
is dated and signed rather than merely recorded. So the tool refuses without one
and says so before it writes anything.

WHAT IT DOES NOT REFUSE: a shift with obligations unmet. A day where the shade
trailer broke down and the crew was sent home at eleven is a real shift with a
real gap, and a tool that would not let the gap be recorded would produce either
a false record or no record. Same posture the training tools take towards a
missing §112.161 element — state it, keep it.

────────────────────────────────────────────────────────────────────────────
THE GUARDS ARE `create_employee`'s, SHARED RATHER THAN RESTATED
────────────────────────────────────────────────────────────────────────────

`employee.require_hr_role` and `employee.require_company_scope`, imported. A
shift is a personnel record before it is a compliance record: it names who was at
work and for how long, it is read in a wage claim, and forming one for an entity
you cannot see would put a crew on a register you cannot read. Reads are scoped
the same way, so a scoped account listing "every shift" gets its own entity's.
"""

from __future__ import annotations

import frappe

from .. import compat, shifts
from ..args import as_choice, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import employee as employee_tool

DOCTYPE = shifts.DOCTYPE

#: Most shifts any one read returns. A shift register is read to answer a
#: question about a period or a crew, not to be exported.
RECORD_CAP = 500

#: Most workers one `start_shift` call will roster. Sixty is a large crew on a
#: single block; past it the caller has almost certainly passed a company roster
#: rather than the people who turned up, and forming a shift for everybody on the
#: payroll would write sixty wrong Attendance rows when it closed.
CREW_CAP = 60


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _readable_companies(actor: str) -> list:
	"""Companies this principal may see, or [] meaning unrestricted.

	Frappe's own rule: no User Permission means unrestricted, which is what every
	Desk surface already does. `api/guard.py` inverts that for the mobile methods
	and says why; this is not that surface.
	"""
	from .. import roles

	return roles.companies_for(actor) or []


def _resolve_shift(args: dict, key: str = "shift") -> dict:
	name = (as_str(args, key) or as_str(args, "name") or as_str(args, "farm_shift", required=True)).strip()
	if not frappe.db.exists(DOCTYPE, name):
		raise ToolError(
			f"no {DOCTYPE} called {name!r} on this site. list_shifts has the register; a docname "
			"looks like SHIFT-2026-0001. Nothing was changed."
		)
	return dict(
		frappe.db.get_value(DOCTYPE, name, compat.existing_fields(DOCTYPE, shifts.FIELDS), as_dict=True) or {}
	)


def file_reference(value: str, label: str) -> str:
	"""A File docname or file_url, checked to point at something real.

	A DOCNAME IS CHECKED AND A URL IS TAKEN AS GIVEN, which is exactly what
	`inspections.normalise_evidence` does and for the same two reasons. A docname
	this site has never heard of is a typo or a token from another install, and a
	signature pointing at nothing satisfies the contract and proves nothing —
	which is the one kind of missing evidence nobody discovers until an auditor
	clicks it. A URL, though, is a path: a file served from somewhere this site
	does not have a File row for is a real possibility on a bench that has been
	moved, and refusing it would leave a shift OPEN because its signature could
	not be resolved. An open shift is a worse outcome than an unresolvable path,
	because the timeline keeps growing against it.

	Both spellings arrive in practice: the iOS app gets a File docname back from
	`finalize_staged_file`, and a Desk user has a URL.
	"""
	value = str(value or "").strip()
	if not value:
		return ""
	try:
		if frappe.db.exists("File", value):
			return str(frappe.db.get_value("File", value, "file_url") or value)
	except Exception:  # pragma: no cover - a site that cannot read its own File table
		return value
	if value.startswith("/") or value.startswith("http"):
		return value
	raise ToolError(
		f"{label} points at {value!r}, which is not a File on this site and is not a path. "
		"Upload it with stage_file_chunk and commit_staged_file first — a signature pointing at "
		"nothing satisfies the contract and proves nothing, and nobody finds that out until an "
		"auditor clicks it. Nothing was changed."
	)


def _when(args: dict, key: str) -> str:
	"""A timestamp argument, defaulting to now.

	`now()` rather than `today()`: everything on a shift is answered at the
	minute — an hour between water breaks and three hours between them are
	different shifts, and only the timestamps say which this was.
	"""
	return as_str(args, key) or frappe.utils.now()


def _crew_argument(raw, label: str = "crew_employees") -> list:
	"""Whatever the caller sent as a crew, as a list of Employee docnames."""
	if raw in (None, "", []):
		return []
	if isinstance(raw, str):
		raw = [part.strip() for part in raw.split(",") if part.strip()]
	if not isinstance(raw, (list, tuple)):
		raise ToolError(f"{label} must be a list of employees. Got {type(raw).__name__}.")
	if len(raw) > CREW_CAP:
		raise ToolError(
			f"{label} names {len(raw)} people, past the {CREW_CAP} cap. Sixty is a large crew on "
			"one block; past it this is almost certainly a company roster rather than the people "
			"who turned up, and every extra name becomes a wrong Attendance row when the shift "
			"closes. Nothing was created."
		)
	out = []
	for index, entry in enumerate(raw):
		if isinstance(entry, dict):
			person = str(entry.get("employee") or entry.get("name") or "").strip()
			role = str(entry.get("role") or "Worker").strip() or "Worker"
			joined = str(entry.get("joined_at") or "").strip()
		else:
			person, role, joined = str(entry or "").strip(), "Worker", ""
		if not person:
			raise ToolError(f"{label}[{index}] names nobody.")
		out.append(
			{
				"employee": employee_tool.resolve_employee(person),
				"role": as_choice(shifts.CREW_DOCTYPE, "role", role, "role"),
				"joined_at": joined,
			}
		)
	return out


def _crew_note(described: dict) -> str:
	size = described.get("crew_size") or 0
	if not size:
		return (
			"NOBODY IS ON THIS CREW YET. A foreman opening a shift before the crew arrives is the "
			"ordinary case rather than an error — add_worker_to_shift rosters them as they turn "
			"up. But a shift closed with an empty crew writes no Attendance at all, and it is "
			"evidence about nobody."
		)
	return f"{size} on the crew, {described.get('still_on_shift')} of them still on the shift."


# ── 1. start_shift ──────────────────────────────────────────────────────────
def start_shift(args: dict) -> ToolResult:
	"""Form a crew at a place, and start the exposure period compliance is read against."""
	_require()
	compat.require_doctype("Employee", "It comes with the Frappe HR (hrms) app.")
	actor = employee_tool.require_hr_role()

	foreman = employee_tool.resolve_employee(as_str(args, "foreman", required=True))
	row = frappe.db.get_value("Employee", foreman, ["employee_name", "company", "status"], as_dict=True) or {}
	company = resolve_company(as_str(args, "company") or str(row.get("company") or ""), required=True)
	employee_tool.require_company_scope(actor, company)
	if row.get("company") and str(row["company"]) != company:
		raise ToolError(
			f"{foreman} ({row.get('employee_name')}) is employed by {row['company']}, and this "
			f"call names {company}. A shift belongs to the entity whose crew worked it — filing "
			"it against another one puts the evidence in a packet that will be handed to an "
			"inspector asking about a different company. Nothing was created."
		)

	crew = _crew_argument(args.get("crew_employees") or args.get("crew"))
	start = _when(args, "start_datetime")

	# THE CREW IS CHECKED AGAINST THE COMPANY BEFORE ANYTHING IS WRITTEN, because a
	# shift half-created and then refused on its ninth crew member leaves an open
	# shift nobody meant to open — and an open shift is what the v0.19.4 weather
	# sweep walks and what `list_shifts` reports as work in progress.
	for entry in crew:
		theirs = str(frappe.db.get_value("Employee", entry["employee"], "company") or "")
		if theirs and theirs != company:
			raise ToolError(
				f"{entry['employee']} is employed by {theirs} and this shift belongs to {company}. "
				"A crew list crossing entities produces Attendance rows on a payroll register "
				"that did not employ the person. Nothing was created."
			)

	doc = frappe.new_doc(DOCTYPE)
	doc.foreman = foreman
	doc.foreman_name = row.get("employee_name") or foreman
	doc.company = company
	doc.location = as_str(args, "location")
	doc.farm_location_gps = as_str(args, "farm_location_gps")
	doc.shift_type = as_choice(DOCTYPE, "shift_type", as_str(args, "shift_type") or "General", "shift_type")
	doc.start_datetime = start
	for entry in crew:
		doc.append(
			"crew",
			{
				"employee": entry["employee"],
				"role": entry["role"],
				# DEFAULTS TO THE SHIFT'S OWN START rather than to now. Everybody the
				# foreman rostered at the beginning was there at the beginning, and
				# stamping them with the moment the API call landed would shave
				# minutes off every one of their days.
				"joined_at": entry["joined_at"] or start,
			},
		)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = shifts.describe(dict(doc.as_dict()), with_children=True)
	data = {
		**described,
		"actor": actor,
		"crew_note": _crew_note(described),
		"note": (
			f"Shift {doc.name} is OPEN. Everything logged against it from now until end_shift is "
			"evidence about this exposure period — log_shift_event records the water breaks, "
			"shade breaks, rest cycles and observations OAR 437-004-1131 asks about, and "
			"end_shift closes it with the supervisor's signature."
		),
		"next_step": (
			"log_shift_event as things happen. Logging at the time is the whole value: a timeline "
			"written from memory in the evening is what an investigator discounts."
		),
	}
	if not doc.farm_location_gps:
		data["weather_note"] = (
			"No farm_location_gps on this shift, so it will get NO weather timeline when v0.19.4 "
			"wires the fetch — the service asks Open-Meteo what the conditions are at a place, and "
			"there is no place here. A point-in-time temperature is a data point and a timeline is "
			"a defence; set the coordinates while the shift is still open."
		)
	return ToolResult(
		data=data,
		summary=(
			f"started {doc.shift_type} shift {doc.name} at {doc.location or 'an unnamed location'} "
			f"under {described['foreman_name']} with {described['crew_size']} on the crew"
		),
		docstatus_delta="none → 0 (created)",
	)


# ── 2. add_worker_to_shift ──────────────────────────────────────────────────
def add_worker_to_shift(args: dict) -> ToolResult:
	"""Roster somebody onto a shift already running — a late arrival, or a transfer."""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_shift(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))
	if not shifts.is_open(row):
		raise ToolError(
			f"{row['name']} ended at {row.get('end_datetime')}. Nobody joins a shift that is over "
			"— and the Attendance rows for this shift have already been written, so a crew row "
			"added now would be a person with no payroll day. Start a new shift. Nothing was "
			"changed."
		)

	person = employee_tool.resolve_employee(as_str(args, "employee", required=True))
	theirs = frappe.db.get_value("Employee", person, ["employee_name", "company"], as_dict=True) or {}
	if theirs.get("company") and str(theirs["company"]) != str(row.get("company") or ""):
		raise ToolError(
			f"{person} ({theirs.get('employee_name')}) is employed by {theirs['company']} and this "
			f"shift belongs to {row.get('company')}. Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	for entry in doc.crew or []:
		if str(entry.get("employee")) == person:
			raise ToolError(
				f"{theirs.get('employee_name') or person} is already on this crew, joined at "
				f"{entry.get('joined_at')}"
				+ (f" and left at {entry.get('left_at')}" if entry.get("left_at") else "")
				+ ". Two rows for one person become two Attendance days when the shift closes. "
				"Somebody who left and came back is one row spanning both — clear their left_at "
				"instead. Nothing was changed."
			)

	# DEFAULTS TO NOW, WHICH IS THE OPPOSITE OF `start_shift`'s DEFAULT AND IS
	# RIGHT FOR THE SAME REASON. A worker rostered at the beginning was there at
	# the beginning; a worker added mid-shift arrived when somebody said so.
	joined = _when(args, "joined_at")
	doc.append(
		"crew",
		{
			"employee": person,
			"employee_name": theirs.get("employee_name") or person,
			"role": as_choice(shifts.CREW_DOCTYPE, "role", as_str(args, "role") or "Worker", "role"),
			"joined_at": joined,
			"notes": as_str(args, "notes"),
		},
	)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = shifts.describe(dict(doc.as_dict()), with_children=True)
	late = shifts.hours_between(str(row.get("start_datetime") or ""), joined)
	data = {
		**described,
		"actor": actor,
		"added": {
			"employee": person,
			"employee_name": theirs.get("employee_name") or person,
			"joined_at": joined,
			"hours_after_shift_start": late,
		},
		"note": (
			f"{theirs.get('employee_name') or person} is on the crew from {joined}. Their "
			"Attendance record will span from there rather than from the shift's start — the "
			"shift is the crew envelope and the row is the person's own day."
		),
	}
	if late is not None and late >= 1:
		data["acclimatization_note"] = (
			f"They joined {late} hour(s) into the shift. If the shift has already crossed the "
			f"{shifts.HEAT_THRESHOLD_F:.0f} °F heat index, somebody arriving mid-shift has had "
			"none of the morning's water breaks and none of the acclimatization the crew has "
			"— OAR 437-004-1131(g) is about exactly this person."
		)
	return ToolResult(
		data=data,
		summary=(
			f"added {theirs.get('employee_name') or person} to {row['name']} at {joined} "
			f"({described['crew_size']} on the crew)"
		),
		docstatus_delta="0 → 0 (amended)",
	)


# ── 3. remove_worker_from_shift ─────────────────────────────────────────────
def remove_worker_from_shift(args: dict) -> ToolResult:
	"""End one worker's time on a shift that continues without them.

	IT SETS `left_at`; IT DOES NOT DELETE THE ROW. Deleting would destroy the only
	record that this person was on the shift at all — which is the record a wage
	claim turns on, and the record that says who was exposed on a hot afternoon
	before they were sent home. The name of the tool is the operational verb and
	the storage is the compliance one, and the two are allowed to differ.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_shift(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	person = employee_tool.resolve_employee(as_str(args, "employee", required=True))
	doc = frappe.get_doc(DOCTYPE, row["name"])
	target = None
	for entry in doc.crew or []:
		if str(entry.get("employee")) == person:
			target = entry
			break
	if target is None:
		raise ToolError(
			f"{person} is not on the crew of {row['name']}. get_shift lists who is. Nothing was changed."
		)
	if target.get("left_at") and not str(args.get("left_at") or "").strip():
		raise ToolError(
			f"{target.get('employee_name') or person} already left {row['name']} at "
			f"{target['left_at']}. Pass left_at explicitly to correct that time; calling this "
			"twice with no time would silently move their departure to now and lengthen a day "
			"that has already ended. Nothing was changed."
		)

	left = _when(args, "left_at")
	target.left_at = left
	if as_str(args, "notes"):
		target.notes = as_str(args, "notes")
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = shifts.describe(dict(doc.as_dict()), with_children=True)
	hours = shifts.hours_between(str(target.get("joined_at") or ""), left)
	return ToolResult(
		data={
			**described,
			"actor": actor,
			"removed": {
				"employee": person,
				"employee_name": target.get("employee_name") or person,
				"joined_at": str(target.get("joined_at") or "") or None,
				"left_at": left,
				"hours_present": hours,
			},
			"note": (
				f"{target.get('employee_name') or person} is off the shift from {left}. THE ROW IS "
				"STILL THERE — it is the only record they were on this shift at all, which is what "
				"a wage claim turns on and what says who was exposed before they were sent home. "
				f"Their Attendance record will span {target.get('joined_at')} to {left}"
				+ (f", {hours} hour(s)." if hours is not None else ".")
			),
			"shift_note": (
				f"The shift continues with {described['still_on_shift']} still on it."
				if described["still_on_shift"]
				else "NOBODY IS LEFT ON THIS SHIFT. If the crew has gone home, end_shift closes it "
				"— an open shift with an empty crew keeps appearing as work in progress and keeps "
				"being fetched for weather it does not need."
			),
		},
		summary=(
			f"{target.get('employee_name') or person} left {row['name']} at {left}"
			+ (f" after {hours} hour(s)" if hours is not None else "")
		),
		docstatus_delta="0 → 0 (amended)",
	)


# ── 4. log_shift_event ──────────────────────────────────────────────────────
def log_shift_event(args: dict) -> ToolResult:
	"""Record one thing the foreman did about the conditions, at the moment it happened.

	THE TIMELINE IS THE EVIDENCE. Oregon's heat rule does not ask whether water
	was available in principle; it asks what happened during the shift, and four
	water breaks with timestamps answer that in a way an annual policy document
	never can. Logged at the time, because a timeline written from memory in the
	evening is what an investigator discounts.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_shift(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	event_type = as_choice(
		shifts.EVENT_DOCTYPE, "event_type", as_str(args, "event_type", required=True), "event_type"
	)
	when = _when(args, "event_datetime")
	logged_by = as_str(args, "logged_by")
	if logged_by:
		logged_by = employee_tool.resolve_employee(logged_by)
	else:
		# The foreman is the DEFAULT and not the assumption — a lead worker calling
		# a break at the far end of the block is the ordinary case, and `logged_by`
		# names them where somebody says so.
		logged_by = row.get("foreman")

	evidence = file_reference(
		as_str(args, "evidence_file_token") or as_str(args, "evidence_file"), "evidence_file_token"
	)

	producer_doctype = as_str(args, "producer_record_doctype")
	producer_name = as_str(args, "producer_record_name")
	if producer_name and not producer_doctype:
		raise ToolError(
			f"producer_record_name is {producer_name!r} and producer_record_doctype is empty. A "
			"docname with no doctype cannot be followed to anything — a packet builder reading "
			"this row would have a string and nowhere to look it up. Nothing was written."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	doc.append(
		"compliance_events",
		{
			"event_type": event_type,
			"event_datetime": when,
			"logged_by": logged_by or None,
			"description": as_str(args, "description"),
			"producer_record_doctype": producer_doctype or None,
			"producer_record_name": producer_name or None,
			"weather_snapshot_temp_f": args.get("weather_snapshot_temp_f"),
			"weather_snapshot_heat_index_f": args.get("weather_snapshot_heat_index_f"),
			"evidence_file": evidence or None,
		},
	)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = shifts.describe(dict(doc.as_dict()), with_children=True)
	same_type = [entry for entry in described["compliance_events"] if entry["event_type"] == event_type]
	data = {
		**described,
		"actor": actor,
		"logged": {
			"event_type": event_type,
			"event_datetime": when,
			"logged_by": logged_by or None,
			"producer_record": f"{producer_doctype} {producer_name}".strip() or None,
			"evidence_attached": bool(evidence),
		},
		"events_of_this_type": len(same_type),
		"note": (
			f"{len(described['compliance_events'])} event(s) on this shift's timeline, "
			f"{len(same_type)} of them {event_type}. The timeline is the evidence — 'water was "
			"available' is a policy and four timestamped breaks are a record."
		),
	}
	if not shifts.is_open(row):
		data["closed_shift_note"] = (
			f"{row['name']} was closed at {row.get('end_datetime')} and this event was added "
			"afterwards. Recorded rather than refused: an event remembered after the close is "
			"better on the record than off it. But it is dated by its own timestamp and the close "
			"is dated by its, and an inspector reading the two will see the order they happened in."
		)
	elif str(when) < str(row.get("start_datetime") or ""):
		data["timing_note"] = (
			f"This event is timestamped {when} and the shift started at {row.get('start_datetime')}"
			" — before the shift began. Kept as given, because a clock five minutes out is not a "
			"false record and refusing would mean the break goes unlogged rather than logged "
			"approximately. Worth correcting if it was not a clock."
		)
	return ToolResult(
		data=data,
		summary=f"logged {event_type} on {row['name']} at {when}",
		docstatus_delta="0 → 0 (amended)",
	)


# ── 5. end_shift ────────────────────────────────────────────────────────────
def end_shift(args: dict) -> ToolResult:
	"""Close a shift with the supervisor's signature, and write the crew's payroll rows.

	THE SIGNATURE IS REQUIRED AND IT IS WHY THIS IS A TOOL. An unsigned close is
	an UPDATE setting a timestamp; the signature is what makes it the attestation
	FSMA §112.161(b) asks for — a review that is dated and signed rather than
	merely recorded.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_shift(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))
	if not shifts.is_open(row):
		raise ToolError(
			f"{row['name']} was already closed at {row.get('end_datetime')} by a review signed on "
			f"{row.get('supervisor_review_on') or 'an unrecorded date'}. Closing it again would "
			"write a second set of Attendance rows for the same day. Nothing was changed."
		)

	signature = file_reference(
		as_str(args, "supervisor_signature_file_token") or as_str(args, "supervisor_signature"),
		"supervisor_signature_file_token",
	)
	if not signature:
		raise ToolError(
			"supervisor_signature_file_token is required to close a shift. FSMA §112.161(b) asks "
			"for a review that is dated AND SIGNED by a supervisor or responsible party, and an "
			"unsigned close is an update statement setting a timestamp — there is nobody's name "
			"against what this shift claims happened. Upload the signature with stage_file_chunk "
			"and commit_staged_file, then pass its File docname. THE SHIFT IS STILL OPEN and "
			"nothing was changed."
		)

	end = _when(args, "end_datetime")
	if str(end) < str(row.get("start_datetime") or ""):
		raise ToolError(
			f"this call ends the shift at {end} and it started at {row.get('start_datetime')} — it "
			"would have finished before it began, and every crew member's Attendance row would "
			"carry a negative span. Nothing was changed."
		)

	crew_before = shifts.crew_of(row["name"])
	late = [entry for entry in crew_before if entry.get("left_at") and str(entry["left_at"]) > str(end)]
	if late:
		names = ", ".join(str(entry.get("employee_name") or entry.get("employee")) for entry in late)
		raise ToolError(
			f"{names} is recorded as leaving after {end}, which is when this call ends the shift. "
			"Nobody is on a shift that is over. Correct the departure time with "
			"remove_worker_from_shift, or end the shift later. Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	doc.end_datetime = end
	doc.supervisor_review_signature = signature
	doc.supervisor_review_on = as_str(args, "reviewed_on") or frappe.utils.now()
	if as_str(args, "foreman_notes"):
		doc.foreman_notes = as_str(args, "foreman_notes")
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	closed = dict(doc.as_dict())
	described = shifts.describe(closed, with_children=True)
	# THE BRIDGE RUNS AFTER THE CLOSE IS WRITTEN AND CANNOT UNDO IT. See
	# `shifts.bridge_to_attendance`: the signature is the compliance act and the
	# payroll row is the convenience, so a site with no Frappe HR, an archived
	# employee, or a day somebody already keyed in by hand all produce a reported
	# skip rather than a shift that will not close.
	bridge = shifts.bridge_to_attendance(closed, shifts.crew_of(row["name"]))

	hours = shifts.hours_between(str(row.get("start_datetime") or ""), end)
	data = {
		**described,
		"actor": actor,
		"shift_hours": hours,
		"attendance": bridge,
		"attendance_created": len(bridge.get("created") or []),
		"note": (
			f"{row['name']} is CLOSED, reviewed and signed. "
			f"{len(bridge.get('created') or [])} Attendance record(s) were written, one per crew "
			"member, each spanning that person's own joined_at to their own left_at — not the "
			"shift's span, because a worker who arrived late and left early did not work the "
			"whole day and a row claiming they did is wrong in the employer's favour."
		),
		"next_step": (
			"create_heat_exposure_event documents OAR 437-004-1131 for this shift where the day "
			"was hot. It is the record an inspector asks for by shift, and there is at most one "
			"per shift."
		),
	}
	if not described["compliance_events"]:
		data["timeline_note"] = (
			"NOTHING WAS LOGGED ON THIS SHIFT'S TIMELINE. That is recorded rather than refused — a "
			"shift where nothing needed logging is a real shift — but on a hot day it is the "
			"absence an inspector reads as 'no water breaks were called', because there is no "
			"other reading available. log_shift_event is the record; it cannot be added "
			"convincingly afterwards."
		)
	return ToolResult(
		data=data,
		summary=(
			f"closed {row['name']} at {end}"
			+ (f" after {hours} hour(s)" if hours is not None else "")
			+ f"; {len(bridge.get('created') or [])} Attendance record(s) written"
		),
		docstatus_delta="0 → 0 (closed)",
	)


# ── 6. list_shifts ──────────────────────────────────────────────────────────
def list_shifts(args: dict) -> ToolResult:
	"""The shift register, filtered the four ways a question about a period asks."""
	_require()
	actor = employee_tool.require_hr_role()
	limit = min(as_limit(args), RECORD_CAP)

	filters = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	else:
		allowed = _readable_companies(actor)
		if allowed:
			filters["company"] = ("in", allowed)

	foreman = as_str(args, "foreman")
	if foreman:
		filters["foreman"] = employee_tool.resolve_employee(foreman)

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["start_datetime"] = ("between", [f"{from_date} 00:00:00", f"{to_date} 23:59:59"])
	elif from_date:
		filters["start_datetime"] = (">=", f"{from_date} 00:00:00")
	elif to_date:
		filters["start_datetime"] = ("<=", f"{to_date} 23:59:59")

	shift_type = as_str(args, "shift_type")
	if shift_type:
		filters["shift_type"] = as_choice(DOCTYPE, "shift_type", shift_type, "shift_type")

	found = shifts.rows(filters, limit=max(limit * 2, limit))
	described = [shifts.describe(row) for row in found]

	status = as_str(args, "status")
	if status:
		wanted = {
			option.lower(): option
			for option in (shifts.STATUS_ACTIVE, shifts.STATUS_CLOSED, shifts.STATUS_CANCELLED)
		}.get(status.strip().lower())
		if not wanted:
			raise ToolError(
				f"status {status!r} is not one of Active, Closed, Cancelled. Note this is COMPUTED "
				"from whether the shift has an end time rather than read off the stored column — a "
				"record saved in March holds March's answer, and an open shift is what the weather "
				"sweep walks."
			)
		described = [entry for entry in described if entry["status"] == wanted]

	if as_str(args, "employee"):
		# WALKED IN PYTHON, because the crew is a child table and a join would
		# return one shift row per crew row. Somebody asking "which shifts was Ana
		# on" wants shifts, not crew rows.
		person = employee_tool.resolve_employee(as_str(args, "employee"))
		described = [
			entry
			for entry in described
			if any(str(crew.get("employee")) == person for crew in shifts.crew_of(entry["name"]))
		]

	truncated = len(described) > limit
	described = described[:limit]
	open_now = [entry["name"] for entry in described if entry["open"]]
	unsigned = [
		entry["name"] for entry in described if not entry["open"] and not entry["supervisor_reviewed"]
	]

	data = {
		"company": company,
		"count": len(described),
		"limit": limit,
		"truncated": truncated,
		"shifts": described,
		"open": open_now,
		"closed_without_a_signature": unsigned,
		"note": (
			f"{len(open_now)} shift(s) still open."
			if open_now
			else "Nothing in this selection is still open."
		),
	}
	if unsigned:
		data["signature_note"] = (
			f"{len(unsigned)} closed shift(s) carry no supervisor signature. end_shift cannot "
			"produce one, so these were closed in the Desk or by an import — and FSMA §112.161(b) "
			"asks for a review that is dated and signed. A signature added now is dated now."
		)
	if truncated:
		data["truncation_note"] = (
			f"More than {limit} shift(s) matched and this is the first {limit}. Narrow by company, "
			"foreman or period before relying on the counts above."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} shift(s)"
			+ (f" for {company}" if company else "")
			+ f"; {len(open_now)} open, {len(unsigned)} closed unsigned"
		),
	)


# ── 7. get_shift ────────────────────────────────────────────────────────────
def get_shift(args: dict) -> ToolResult:
	"""One shift in full: the crew and their spans, the timeline, the weather, the heat record."""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_shift(args, "name")
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	described = shifts.describe(row, with_children=True)
	heat = shifts.heat_rows({"farm_shift": row["name"]}, limit=2)

	data = {
		**described,
		"heat_exposure_event": (shifts.describe_heat_event(heat[0]) if heat else None),
		"crew_note": _crew_note(described),
	}
	if not described["weather_timeline"]:
		data["weather_note"] = (
			"NO WEATHER TIMELINE. Nothing populates it in v0.19.3 — the table ships so its shape "
			"is fixed and the compliance-event snapshots have somewhere to come from, and v0.19.4 "
			"wires the Open-Meteo fetch for open shifts plus archive backfill for closed ones. "
			"Until then a heat record's maxima are entered by hand."
		)
	if described["open"]:
		data["open_note"] = (
			"This shift is still open. Its compliance events are still being written and its "
			"Attendance rows do not exist yet — end_shift produces both."
		)
	elif not described["supervisor_reviewed"]:
		data["signature_note"] = (
			"This shift is closed and carries no supervisor signature, so it was not closed by "
			"end_shift. FSMA §112.161(b) asks for a review dated and signed by a supervisor, and "
			"there is nobody's name against what this shift says happened."
		)
	if not heat and described["shift_type"] in ("Harvest", "Prune", "Spray", "Irrigation"):
		data["heat_note"] = (
			"No Heat Exposure Event documents this shift. That is correct for a shift that never "
			f"reached a {shifts.HEAT_THRESHOLD_F:.0f} °F heat index and a gap for one that did — "
			"create_heat_exposure_event files it, and there is at most one per shift."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{row['name']} — {described['shift_type']} at "
			f"{described['location'] or 'an unnamed location'} under {described['foreman_name']}, "
			f"{described['status']}, {described['crew_size']} on the crew, "
			f"{described['compliance_event_count']} event(s) logged"
		),
	)
