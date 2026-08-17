# SPDX-License-Identifier: MIT
"""The curriculum, the afternoon, and the eight tools that close the training loop.

v0.19.0 built the training REGISTER and 891fc7c built the MATRIX over it, and
between them they left the two ends of the loop open. At one end a Training Type
was a name and a regime tag — nothing on it said what the course IS, so a handset
could tell a picker they were due for WPS Handler Training and could not show
them the film. At the other end every record was filed one person at a time, so a
crew leader who trained twelve people in a shed on Tuesday had twelve forms to
type, and twelve forms typed one at a time disagree about the date, the topics
and the trainer by the third one.

These eight tools are those two ends. `update_training_type` and
`get_training_curriculum` put the content on the curriculum and hand it to a
screen. The six session tools record the afternoon once and turn it into as many
training records as there were people who can be proved to have been there.

────────────────────────────────────────────────────────────────────────────
THE SESSION IS THE ACT; THE RECORDS ARE STILL THE EVIDENCE
────────────────────────────────────────────────────────────────────────────

Nothing here replaces `Employee Training Record`, and that is deliberate rather
than conservative. The compliance matrix reads per-person records; the
`training_expiring` rule watches per-person expiry; `generate_audit_packet` pulls
per-person rows. A group document that called itself the evidence would be
evidence none of those three can see into, and the operation would discover it in
a room with an inspector.

So `complete_training_session` is the one moment a session becomes records, and
it writes them THROUGH `record_training` rather than building documents of its
own. One code path files a training record on this site — the same guards, the
same §112.161 derivations, the same supersession report — and the difference
between one person and twelve is a loop.

────────────────────────────────────────────────────────────────────────────
WHY A COMPLETION CAN REFUSE AND WHY IT CAN ALSO PROCEED
────────────────────────────────────────────────────────────────────────────

The default refuses when somebody marked present has no badge scan or no
signature, and names them. That is the check worth having: a sheet where four of
twelve never signed is a sheet somebody has to go and fix while the crew is still
on site, and a tool that quietly filed eight records would have taken that Monday
away from them.

`skip_incomplete=true` files the eight anyway and names the four. Both calls are
legitimate — sometimes the four went home — and which one is right is not a
decision this app can make. What it will not do is make it silently.

────────────────────────────────────────────────────────────────────────────
THE GUARDS ARE `record_training`'s, SHARED RATHER THAN RESTATED
────────────────────────────────────────────────────────────────────────────

`employee.require_hr_role` and `employee.require_company_scope` are imported, not
copied, for the reason `tools/training.py` gives: a training session is a
personnel record, it names workers, it is read in a wage claim, and creating one
for an entity you cannot see would put a crew's qualification history on a
register you cannot read.
"""

from __future__ import annotations

import frappe

from .. import compat, training, training_sessions
from ..args import as_bool, as_date, as_int, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import badges as badge_tools
from . import employee as employee_tool
from . import signing_evidence as evidence
from . import training as training_tools

DOCTYPE = training_sessions.DOCTYPE
TYPE_DOCTYPE = training_sessions.TYPE_DOCTYPE

#: Most sessions any one listing returns. A training register is read to answer a
#: question about a season, not to be exported.
SESSION_CAP = 200

#: Most attendee rows one session holds. A hundred people in one shed is a
#: figure nobody has hit; the cap is here so a malformed batch cannot build a
#: document nothing can render.
ATTENDEE_CAP = 200

#: Most curricula `get_training_curriculum` reports at once when no name is given.
CURRICULUM_CAP = 100

#: Most recent sessions a curriculum read reports beside the content.
RECENT_SESSION_CAP = 5


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _require_type() -> None:
	compat.require_doctype(
		TYPE_DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _resolve_type(value: str) -> str:
	"""An existing Training Type docname, case- and space-insensitively.

	NO AUTO-CREATION HERE, unlike `record_training`. That tool takes free text and
	creates a curriculum because refusing it would leave an operation with the
	training and no record of it. These tools are the other way round: a session
	scheduled against a curriculum this site does not run, and a content update
	applied to a name nobody has filed against, are both far more likely to be a
	typo than a new course — and `record_training` is one call away for the case
	where it is not.
	"""
	wanted = str(value or "").strip()
	if not wanted:
		raise ToolError("training_type is required — a session is a delivery of one curriculum.")
	found = training.find_type(wanted)
	if not found:
		raise ToolError(
			f"no {TYPE_DOCTYPE} called {wanted!r} on this site. Curricula are created by "
			"record_training from free text on first use, and the ten common ones are seeded on "
			"install — so a name nothing has been filed against does not exist yet. "
			"get_training_curriculum lists them. Nothing was changed."
		)
	return found


def _resolve_session(args: dict) -> dict:
	name = (
		as_str(args, "name") or as_str(args, "session") or as_str(args, "training_session", required=True)
	).strip()
	if not frappe.db.exists(DOCTYPE, name):
		raise ToolError(
			f"no {DOCTYPE} called {name!r} on this site. list_training_sessions has the register; "
			"a docname looks like TRNS-2026-0001."
		)
	return dict(
		frappe.db.get_value(DOCTYPE, name, compat.existing_fields(DOCTYPE, training_sessions.FIELDS), as_dict=True)
		or {}
	)


def _open_session(args: dict, actor: str, what: str) -> dict:
	"""One session, proved to be one this principal may still write to."""
	row = _resolve_session(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))
	status = training_sessions.canon_status(row.get("status")) or training_sessions.STATUS_SCHEDULED
	if status not in training_sessions.OPEN_STATUSES:
		raise ToolError(
			f"{row['name']} is {status}, and {what} on it is not an edit — it is a correction to "
			+ (
				"finished evidence. The training records it produced are on twelve people's files "
				"and an attendee added now would be a thirteenth person appearing on a sheet that "
				"has already been filed. Record the extra session, or the extra person, as its "
				"own. "
				if status == training_sessions.STATUS_COMPLETED
				else "a session that did not happen. Create a new one for the session that did. "
			)
			+ "Nothing was changed."
		)
	return row


def _attendee_index(doc, employee: str) -> int:
	for index, row in enumerate(doc.get("attendees") or []):
		if str(row.get("employee") or "") == employee:
			return index
	return -1


def _described(row: dict) -> dict:
	return training_sessions.describe(row, training_sessions.attendees_of(str(row.get("name") or "")))


# ── 1. update_training_type ─────────────────────────────────────────────────
def update_training_type(args: dict) -> ToolResult:
	"""Put the content on a curriculum: the film, the materials, the length, the method.

	THE FIELD THAT MAKES THE REST WORTH HAVING IS `video_url`. A compliance matrix
	that can tell a picker they are due for WPS Handler Training and cannot show
	them the training is a matrix that generates a task somebody else has to do.
	This is the record that closes it, and `get_training_curriculum` is what a
	handset reads it through.

	IT DOES NOT TOUCH THE RECORDS ALREADY FILED. A curriculum's length, method and
	materials describe the COURSE; every session and every training record carries
	its own copy of what actually happened, taken at the time. Correcting a
	curriculum in August does not retroactively make July's forty-minute session
	ninety minutes long, and that is the property the whole two-record design
	exists for.
	"""
	_require_type()
	actor = employee_tool.require_hr_role()
	name = _resolve_type(as_str(args, "training_type", required=True))

	before = training_sessions.type_row(name)
	doc = frappe.get_doc(TYPE_DOCTYPE, name)
	changed: dict = {}

	def stage(fieldname: str, value) -> None:
		if value is None:
			return
		if not compat.has_field(TYPE_DOCTYPE, fieldname):
			return
		current = doc.get(fieldname)
		if str(current or "") == str(value or ""):
			return
		changed[fieldname] = {"from": current if current not in ("",) else None, "to": value or None}
		doc.set(fieldname, value)

	stage("video_url", _url(args))
	stage("materials_description", args.get("materials_description"))
	stage("description", args.get("description"))

	minutes = as_int(args, "duration_minutes")
	if minutes is not None:
		if minutes < 0:
			raise ToolError("duration_minutes cannot be negative. Nothing was changed.")
		stage("duration_minutes", minutes)

	method = args.get("delivery_method")
	if method not in (None, ""):
		canonical = training_sessions.canon_delivery(method)
		if not canonical:
			raise ToolError(
				f"delivery_method {method!r} is not one this app knows. "
				f"{training_sessions.delivery_note()} Nothing was changed."
			)
		stage("delivery_method", canonical)

	active = as_bool(args, "active", None)
	if active is not None:
		stage("active", 1 if active else 0)

	retention = as_int(args, "retention_years")
	if retention is not None:
		if retention < 0:
			raise ToolError("retention_years cannot be negative. Nothing was changed.")
		stage("retention_years", retention)

	regimes_before = training.type_regimes(name)
	regimes_after = regimes_before
	if args.get("regimes") not in (None, ""):
		try:
			regimes_after = training.require(args.get("regimes"))
		except ValueError as exc:
			raise ToolError(f"{exc} Nothing was changed.") from None
		if set(regimes_after) != set(regimes_before):
			training.set_rows(doc, "regimes", regimes_after)
			changed["regimes"] = {"from": regimes_before, "to": regimes_after}

	if not changed:
		return ToolResult(
			data={
				**training_sessions.describe_type({**before, "name": name}),
				"actor": actor,
				"changed": {},
				"note": (
					"Nothing was passed that differs from what the curriculum already says, so "
					"nothing was written. The record is reported as it stands."
				),
			},
			summary=f"{name} unchanged — nothing passed differed from what it already held",
		)

	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	after = training_sessions.type_row(name)
	described = training_sessions.describe_type(after)
	data = {
		**described,
		"actor": actor,
		"changed": changed,
		"note": (
			f"{len(changed)} field(s) changed on the curriculum. Training records and sessions "
			"already filed against it are untouched — each carries its own copy of what actually "
			"happened, taken on the day."
		),
	}
	if "delivery_method" in changed and described.get("delivery_method") == "Video" and not described.get(
		"video_url"
	):
		data["content_note"] = (
			"This curriculum is now marked as delivered by video and carries no video_url, so a "
			"handset asking for it has a method and nothing to play. Set video_url, or attach the "
			"film with attach_file_to_document."
		)
	if not described.get("regimes"):
		data["regime_note"] = (
			"This curriculum carries no regimes, so every session of it starts untagged and every "
			"record it produces appears in no audit packet. Set them once here rather than on each "
			"session."
		)
	return ToolResult(
		data=data,
		summary=f"updated {name}: {', '.join(sorted(changed))}",
		docstatus_delta="0 → 0 (amended)",
	)


def _url(args: dict) -> str | None:
	"""`video_url`, refused where it is plainly not a link a handset can open."""
	raw = args.get("video_url")
	if raw is None:
		return None
	text = str(raw).strip()
	if not text:
		return ""
	if not text.lower().startswith(("http://", "https://")):
		raise ToolError(
			f"video_url must be a URL a handset can open — {text!r} has no http:// or https:// "
			"scheme. A path or a filename here would render on a phone as a link that goes "
			"nowhere, which is worse than an empty column because it looks answered. To point at "
			"a film this operation owns, attach it with attach_file_to_document instead. Nothing "
			"was changed."
		)
	return text


# ── 2. get_training_curriculum ──────────────────────────────────────────────
def get_training_curriculum(args: dict) -> ToolResult:
	"""What a course IS, in the shape a handset renders: film, materials, regimes, files.

	THE READ THE MATRIX NEEDED AND DID NOT HAVE. `get_training_compliance_report`
	can tell a foreman that six pickers are missing WPS; until this existed the
	next step was somebody driving to an office for a DVD. One name in, and the
	answer carries the link to play, the materials to lay out, the minutes to
	book and the attachments to hand round.

	NO NAME GIVEN LISTS THE WHOLE CURRICULUM, which is the other question a
	screen asks — "what training does this operation run" — and it is one call
	rather than a listing plus one read per row.
	"""
	_require_type()
	wanted = as_str(args, "training_type") or as_str(args, "name")
	include_inactive = as_bool(args, "include_inactive", False)

	if wanted:
		name = _resolve_type(wanted)
		row = training_sessions.type_row(name)
		described = training_sessions.describe_type(row)
		described["recent_sessions"] = _recent_sessions(name)
		described["regime_notes"] = {
			regime: training.REGIME_NOTES[regime]
			for regime in described["regimes"]
			if regime in training.REGIME_NOTES
		}
		described["retention_note"] = training.retention_note(described["regimes"])
		described["content_gaps"] = _content_gaps(described)
		if described["content_gaps"]:
			described["content_note"] = (
				f"{len(described['content_gaps'])} thing(s) a screen would want to show are not on "
				"this curriculum. update_training_type puts them there once, and every future "
				"session of the course inherits them."
			)
		if not described["active"]:
			described["active_note"] = (
				"This curriculum is not active, so it is not held against anybody in the "
				"compliance matrix. Records already filed against it are untouched — last "
				"season's evidence is still evidence about last season."
			)
		return ToolResult(
			data=described,
			summary=(
				f"{name} — {described['delivery_method'] or 'delivery method unstated'}, "
				f"{described['duration_minutes'] or '?'} min, "
				f"{len(described['attachments'])} attachment(s), "
				f"{', '.join(described['regimes']) or 'untagged'}"
			),
		)

	filters: dict = {}
	if not include_inactive and compat.has_field(TYPE_DOCTYPE, "active"):
		filters["active"] = 1
	rows = (
		frappe.db.get_all(
			TYPE_DOCTYPE,
			filters=filters,
			fields=compat.existing_fields(TYPE_DOCTYPE, training_sessions.TYPE_FIELDS),
			order_by="name asc",
			limit=CURRICULUM_CAP + 1,
		)
		or []
	)
	truncated = len(rows) > CURRICULUM_CAP
	described = [
		training_sessions.describe_type(dict(row), with_attachments=False) for row in rows[:CURRICULUM_CAP]
	]
	for entry in described:
		entry["content_gaps"] = _content_gaps(entry)

	incomplete = [entry["training_type"] for entry in described if entry["content_gaps"]]
	data = {
		"count": len(described),
		"include_inactive": include_inactive,
		"curriculum": described,
		"truncated": truncated,
		"delivery_methods": list(training_sessions.DELIVERY_METHODS),
		"delivery_notes": training_sessions.DELIVERY_NOTES,
		"without_content": incomplete,
		"note": (
			f"{len(incomplete)} of {len(described)} curricula have nothing for a screen to show. "
			"A compliance matrix that names a gap and cannot deliver the training generates a task "
			"for somebody else; update_training_type is what closes it."
			if incomplete
			else "Every curriculum listed has content a handset can render."
		),
		"attachment_note": (
			"Attachments are omitted from the listing — one query per curriculum to count PDFs is "
			"a hundred round trips for a screen that shows names. Name a training_type to get them."
		),
	}
	if truncated:
		data["truncation_note"] = (
			f"More than {CURRICULUM_CAP} curricula on this site and this is the first "
			f"{CURRICULUM_CAP} by name."
		)
	return ToolResult(
		data=data,
		summary=f"{len(described)} curriculum(s); {len(incomplete)} with nothing to show",
	)


def _content_gaps(described: dict) -> list:
	"""What a screen would want and this curriculum does not have."""
	gaps = []
	if not described.get("description"):
		gaps.append("no description — a course name is not an explanation of what is covered")
	if not described.get("delivery_method"):
		gaps.append("no delivery method, so a handset cannot tell whether to open a player or a document")
	if described.get("delivery_method") == "Video" and not described.get("video_url"):
		gaps.append("marked as video and carries no video_url, so there is nothing to play")
	if not described.get("duration_minutes"):
		gaps.append("no duration, so a session of it cannot be booked against a shift")
	if not described.get("materials_description"):
		gaps.append("no materials list — whoever sets the shed up an hour beforehand is guessing")
	return gaps


def _recent_sessions(training_type: str) -> list:
	if not training_sessions.available():
		return []
	return [
		{
			"name": row.get("name"),
			"session_date": str(row.get("session_date") or "") or None,
			"status": row.get("status"),
			"location": row.get("location") or None,
			"records_created": int(row.get("records_created") or 0),
		}
		for row in training_sessions.rows({"training_type": training_type}, limit=RECENT_SESSION_CAP)
	]


# ── 3. create_training_session ──────────────────────────────────────────────
def create_training_session(args: dict) -> ToolResult:
	"""Open one group training event. Nobody is on it yet and nothing is filed.

	A SESSION WRITES NOTHING TO ANYBODY'S FILE, and that is the point of it being
	a separate document from the records it will produce. It can be created a
	week early, it can be cancelled, it can sit half-filled while the crew is
	still arriving — none of which should put a training record on somebody's
	compliance matrix. `complete_training_session` is the only call that does.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	curriculum = _resolve_type(as_str(args, "training_type", required=True))
	company = resolve_company(as_str(args, "company"), required=True)
	employee_tool.require_company_scope(actor, company)

	status = training_sessions.STATUS_SCHEDULED
	if args.get("status") not in (None, ""):
		status = training_sessions.canon_status(args.get("status"))
		if not status:
			raise ToolError(
				f"status {args['status']!r} is not one of "
				f"{', '.join(training_sessions.STATUSES)}. Nothing was created."
			)
		if status == training_sessions.STATUS_COMPLETED:
			raise ToolError(
				"a session cannot be created as Completed. Completion is what writes the training "
				"records, and a document that arrived already finished would claim an afternoon "
				"that produced no evidence — complete_training_session is the call that does both "
				"at once. Nothing was created."
			)

	doc = frappe.new_doc(DOCTYPE)
	doc.training_type = curriculum
	doc.company = company
	doc.status = status
	doc.session_date = as_date(args, "session_date") or frappe.utils.today()
	doc.start_time = as_str(args, "start_time")
	doc.end_time = as_str(args, "end_time")
	doc.location = as_str(args, "location")
	doc.instructor_name = as_str(args, "instructor_name")
	doc.provider = as_str(args, "provider")
	doc.notes = as_str(args, "notes")
	doc.expires_date = as_date(args, "expires_date")

	conductor = as_str(args, "conducted_by") or as_str(args, "instructor")
	if conductor:
		person = employee_tool.resolve_employee(conductor)
		employer = frappe.db.get_value("Employee", person, "company")
		if employer and str(employer) != company:
			raise ToolError(
				f"{person} is employed by {employer} and this session belongs to {company}. "
				"Somebody from another entity may genuinely have run the training — record them "
				"as `instructor_name` and `provider` instead, which is the pair that exists for a "
				"trainer who is not on this payroll. Nothing was created."
			)
		doc.conducted_by = person

	source = as_str(args, "training_source")
	if source:
		meta = compat.field_meta(DOCTYPE, "training_source")
		options = [option for option in str(getattr(meta, "options", "") or "").split("\n") if option]
		match = next((option for option in options if option.lower() == source.lower()), "")
		if not match:
			raise ToolError(
				f"training_source {source!r} is not one of {', '.join(options)}. Nothing was created."
			)
		doc.training_source = match

	method = args.get("delivery_method")
	if method not in (None, ""):
		canonical = training_sessions.canon_delivery(method)
		if not canonical:
			raise ToolError(
				f"delivery_method {method!r} is not one this app knows. "
				f"{training_sessions.delivery_note()} Nothing was created."
			)
		doc.delivery_method = canonical

	minutes = as_int(args, "duration_minutes")
	if minutes is not None:
		if minutes < 0:
			raise ToolError("duration_minutes cannot be negative. Nothing was created.")
		doc.duration_minutes = minutes

	if args.get("regimes") not in (None, ""):
		try:
			training.set_rows(doc, "regimes", training.require(args.get("regimes")))
		except ValueError as exc:
			raise ToolError(f"{exc} Nothing was created.") from None

	topics = training.topics(args.get("content_topics_covered"))
	if topics:
		doc.content_topics_covered = ", ".join(topics)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = _described(dict(doc.as_dict()))
	curriculum_row = training_sessions.type_row(curriculum)
	data = {
		**described,
		"actor": actor,
		"curriculum": training_sessions.describe_type(curriculum_row),
		"inherited_from_curriculum": [
			field
			for field in ("duration_minutes", "delivery_method", "regimes")
			if args.get(field) in (None, "") and described.get(field)
		],
		"next_step": (
			"add_session_attendee, once per person, taking the badge scanned at the door. "
			"sign_session_attendance takes their signature at the end. "
			"complete_training_session turns the ready rows into training records."
		),
		"note": (
			"Nothing is on anybody's file yet. A session writes no training record until it is "
			"completed, which is what makes it safe to open one a week early or to cancel it."
		),
	}
	if not described["regimes"]:
		data["regime_note"] = (
			f"{curriculum} carries no regimes and none were passed, so this session is untagged "
			"and the records it produces would appear in no audit packet. "
			"complete_training_session refuses until it is tagged — set them here or, better, "
			"once on the curriculum with update_training_type."
		)
	if not described["content_topics_covered"]:
		data["topics_note"] = (
			"content_topics_covered is empty. It can be filled in when the session is completed, "
			"and it has to be by then: it is what makes a regime tag defensible rather than "
			"optimistic."
		)
	if not described["expires_date"]:
		data["expiry_note"] = (
			"No expires_date, so every record this session produces will be one-time training the "
			"compliance calendar never asks to be renewed. Right for a new-hire orientation; WRONG "
			"for WPS (12 months), Oregon heat illness (12 months) and annual GAP hygiene."
		)
	return ToolResult(
		data=data,
		summary=(
			f"opened {doc.name} — {curriculum} on {doc.session_date}"
			+ (f" at {described['location']}" if described["location"] else "")
			+ f" ({status})"
		),
		docstatus_delta="none → 0 (created)",
	)


# ── 4. add_session_attendee ─────────────────────────────────────────────────
def add_session_attendee(args: dict) -> ToolResult:
	"""Put one person on the sheet, identified by the badge scanned at the door.

	THE SCAN IS THE IDENTIFICATION AND THE EMPLOYEE LINK IS ITS RESULT, which is
	why `badge_scan` alone is enough and why both may be given. `resolve_badge` is
	the same call the crew clock makes, so a retired card, a card belonging to
	somebody who has left and a QR that is not a badge at all are each refused by
	their own sentence BEFORE a name reaches a sign-in sheet. Passing both is
	checked against each other and refused if they disagree — a badge that
	resolves to somebody else than the name typed beside it is the one situation
	where a sign-in sheet would otherwise record a lie.

	`scan_location` IS NOT REQUIRED and its absence is not a refusal. A metal
	packing shed is where GPS goes to die, and a session refused for want of
	coordinates is a training that happened and was not recorded.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	row = _open_session(args, actor, "adding an attendee")
	company = str(row.get("company") or "")

	badge = as_str(args, "badge_scan") or as_str(args, "badge_id") or as_str(args, "badge")
	named = as_str(args, "employee")
	if not (badge or named):
		raise ToolError(
			"add_session_attendee needs either a badge_scan or an employee. The badge is the "
			"better answer — it is an identity check a machine made at the door, and it is what "
			"complete_training_session requires before it will write a training record. Nothing "
			"was changed."
		)

	scanned_employee = ""
	badge_card: dict = {}
	if badge:
		badge_card = badge_tools.resolve_badge({"badge_id": badge, "company": company}).data
		scanned_employee = str(badge_card.get("employee") or "")

	person = employee_tool.resolve_employee(named) if named else scanned_employee
	if named and scanned_employee and person != scanned_employee:
		raise ToolError(
			f"badge {badge!r} belongs to {scanned_employee} "
			f"({badge_card.get('employee_name')}) and this call also names {person}. A sign-in "
			"sheet that recorded the badge of one person against the name of another would be the "
			"one document in this app that states something nobody believes. Scan the right card, "
			"or pass only one of the two. Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	if len(doc.get("attendees") or []) >= ATTENDEE_CAP:
		raise ToolError(
			f"{row['name']} already holds {ATTENDEE_CAP} attendees, which is this app's ceiling "
			"for one session. A training with more people than that was more than one session — "
			"record it as more than one. Nothing was changed."
		)
	if _attendee_index(doc, person) >= 0:
		raise ToolError(
			f"{person} is already on {row['name']}'s attendee list. Two rows for one person "
			"produce two training records of one afternoon, which is how a compliance report "
			"comes to disagree with the register. sign_session_attendance is the call for adding "
			"their signature. Nothing was changed."
		)

	entry = doc.append(
		"attendees",
		{
			"employee": person,
			"attended": 1 if as_bool(args, "attended", True) else 0,
			"badge_scan": badge,
			"scan_location": as_str(args, "scan_location") or as_str(args, "gps"),
			"scanned_at": as_str(args, "scanned_at") or (frappe.utils.now() if badge else None),
			"notes": as_str(args, "notes"),
		},
	)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	described = _described(dict(doc.as_dict()))
	mine = next((item for item in described["attendee_rows"] if item["employee"] == person), {})
	data = {
		"session": row["name"],
		"actor": actor,
		"attendee": mine,
		"attendance": described["attendance"],
		"badge_holder": badge_card.get("employee_name") if badge_card else None,
		"idx": entry.idx,
		"next_step": (
			"sign_session_attendance, when they sign at the end. Without a signature this row "
			"produces no training record."
			if not mine.get("signed")
			else "Nothing outstanding on this row."
		),
	}
	if not badge:
		data["identity_note"] = (
			"No badge was scanned, so this row records that somebody typed a name. "
			"complete_training_session will not write a training record from it — pass "
			"`badge_scan` from the door, or scan them later and add the row then."
		)
	if not mine.get("scan_location"):
		data["location_note"] = (
			"No coordinates came with the scan. FSMA §112.161(a)(1)(i) asks an activity record "
			"where it happened, and the session's `location` answers it in words; this row simply "
			"does not corroborate it. Not a refusal — a shed is where GPS goes to die."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{mine.get('employee_name') or person} added to {row['name']}"
			+ (f" by badge {badge}" if badge else " by name, with no badge scan")
		),
		docstatus_delta="0 → 0 (amended)",
	)


# ── 5. sign_session_attendance ──────────────────────────────────────────────
def sign_session_attendance(args: dict) -> ToolResult:
	"""Take one attendee's signature, and file the evidence row that says how.

	A SEPARATE CALL FROM `add_session_attendee`, DELIBERATELY, and for the reason
	`sign_training_supervisor_review` is separate from `record_training`: the
	badge is scanned when somebody walks in and the signature is given when the
	session ends, and a single call that took both would make it trivial to
	record them at the same instant. Thirty scans and thirty signatures sharing
	one timestamp is the shape of a sheet an inspector reads as having been
	assembled afterwards.

	IT WRITES A `Signing Evidence` ROW where that register is on the site: who
	signed, how they were identified, where the handset was, and the hash of the
	session as it stood when they signed it. An operation that later edits the
	topics list can be shown to have done so after the crew signed — which is the
	question a sign-in sheet has never been able to answer.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	row = _open_session(args, actor, "signing")

	badge = as_str(args, "badge_scan") or as_str(args, "badge_id") or as_str(args, "badge")
	named = as_str(args, "employee")
	if not (badge or named):
		raise ToolError(
			"sign_session_attendance needs an employee or the badge that identifies them. "
			"Nothing was changed."
		)
	person = (
		employee_tool.resolve_employee(named)
		if named
		else str(badge_tools.resolve_badge({"badge_id": badge, "company": str(row.get("company") or "")}).data.get("employee") or "")
	)

	signature = as_str(args, "signature") or as_str(args, "signature_file", required=True)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	index = _attendee_index(doc, person)
	if index < 0:
		raise ToolError(
			f"{person} is not on {row['name']}'s attendee list, so there is nothing to sign. "
			"add_session_attendee puts them there — with the badge scanned at the door, which is "
			"what makes the signature evidence of a particular person rather than of a mark. "
			"Nothing was changed."
		)
	entry = doc.attendees[index]
	existing = str(entry.get("signature") or "")
	if existing and not as_bool(args, "replace_signature", False):
		when = str(entry.get("signed_at") or "") or "an unrecorded date"
		raise ToolError(
			f"{person} already signed {row['name']} on {when}. Replacing a signature that is "
			"already on a compliance record is a decision rather than a retry — pass "
			"replace_signature=true to say so. Nothing was changed."
		)

	# The hash is taken BEFORE the signature is written, which is the only moment
	# that answers the question it exists to answer — see
	# `signing_evidence.document_fingerprint`. It covers the session as the signer
	# was shown it: the curriculum, the date, the topics, the other attendees.
	fingerprint = evidence.document_fingerprint(doc, exclude=("signature", "signed_at", "signing_evidence"))

	signed_at = as_str(args, "signed_at") or frappe.utils.now()
	entry.signature = signature
	entry.signed_at = signed_at
	if not entry.get("attended"):
		entry.attended = 1
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	written = evidence.record(
		document_type=DOCTYPE,
		document_name=doc.name,
		signature_role="Employee",
		signed_at=signed_at,
		company=str(row.get("company") or ""),
		signer=person,
		signer_name=str(entry.get("employee_name") or person),
		signer_badge=str(entry.get("badge_scan") or ""),
		verification_method="Badge QR" if entry.get("badge_scan") else "",
		signature_field=f"attendees[{entry.idx}].signature",
		signature_image=signature,
		document_hash=fingerprint["hash"],
		hashed_fields=",".join(fingerprint["fields"]),
		device_id=as_str(args, "device_id"),
		gps_latitude=_coordinate(args, entry, 0),
		gps_longitude=_coordinate(args, entry, 1),
	)
	if written.get("evidence") and compat.has_field(
		training_sessions.ATTENDEE_DOCTYPE, "signing_evidence"
	):
		frappe.db.set_value(
			training_sessions.ATTENDEE_DOCTYPE, entry.name, "signing_evidence", written["evidence"]
		)

	described = _described(dict(frappe.get_doc(DOCTYPE, doc.name).as_dict()))
	mine = next((item for item in described["attendee_rows"] if item["employee"] == person), {})
	data = {
		"session": doc.name,
		"actor": actor,
		"attendee": mine,
		"attendance": described["attendance"],
		"replaced_signature": bool(existing),
		"signing_evidence": written,
		"note": (
			"§112.161(a)(4) is answered for this person: the record of the activity is signed by "
			"the person who performed it. The supervisor review §112.161(b) asks for is a separate "
			"act at a later moment — sign_training_supervisor_review, on the record this session "
			"produces."
		),
	}
	if mine.get("state") == training_sessions.ATTENDEE_READY:
		data["next_step"] = (
			"This row is ready. complete_training_session turns it and every other ready row into "
			"a training record."
		)
	elif mine.get("missing"):
		data["next_step"] = (
			"Still outstanding on this row: " + ", ".join(mine["missing"]) + ". Without it the row "
			"produces no training record."
		)
	same_moment = str(entry.get("scanned_at") or "")[:16] == str(signed_at)[:16]
	if same_moment and entry.get("scanned_at"):
		data["timing_note"] = (
			"The badge scan and the signature share a minute. A short toolbox talk genuinely is "
			"one moment and this is recorded as given — but where a session ran an hour, a sheet "
			"whose scans and signatures all share a timestamp is the shape an inspector reads as "
			"having been filled in at the end."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{mine.get('employee_name') or person} signed {doc.name} at {signed_at}"
			+ (
				f" (evidence {written['evidence']})"
				if written.get("evidence")
				else " — no evidence row was written"
			)
		),
		docstatus_delta="0 → 0 (amended)",
	)


def _coordinate(args: dict, entry, index: int):
	"""One half of a lat/long, from the call or from the badge scan's own fix.

	The signature's coordinates where the caller sent them, and the SCAN's
	otherwise — a phone that had a fix at the door and none an hour later is
	ordinary, and the door fix is a true statement about where this person was
	for this session. None where neither has one; a zero would be a point in the
	Gulf of Guinea.
	"""
	explicit = args.get("gps_latitude" if index == 0 else "gps_longitude")
	if explicit not in (None, ""):
		try:
			return float(explicit)
		except (TypeError, ValueError):
			return None
	pieces = str(entry.get("scan_location") or "").split(",")
	if len(pieces) != 2:
		return None
	try:
		return float(pieces[index].strip())
	except (TypeError, ValueError):
		return None


# ── 6. complete_training_session ────────────────────────────────────────────
def complete_training_session(args: dict) -> ToolResult:
	"""Turn every provable attendance into its own training record. One call, one afternoon.

	THIS IS THE MOMENT THE SESSION BECOMES EVIDENCE, and it is the only one. Each
	ready row is filed THROUGH `record_training` — the same guards, the same
	§112.161 derivations, the same supersession report a single record gets — so
	the difference between one person and twelve is a loop rather than a second
	implementation of what a training record means.

	A ROW THAT FAILS DOES NOT TAKE THE OTHERS WITH IT. Eleven records filed and
	one refused by name is a better answer than a refusal that leaves twelve
	people with nothing, and the one refusal is reported with the reason so
	somebody can fix it and call again — a second call files only what is still
	outstanding, because a row that already produced a record is `recorded` and
	is skipped.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_session(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))
	status = training_sessions.canon_status(row.get("status")) or training_sessions.STATUS_SCHEDULED
	if status == training_sessions.STATUS_CANCELLED:
		raise ToolError(
			f"{row['name']} is Cancelled — it is a session that did not happen, and completing it "
			"would put a training record on somebody's file for an afternoon nobody attended. "
			"Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, row["name"])
	# Topics may be supplied at completion, which is the moment somebody actually
	# knows what was covered. They are still REQUIRED — see `completion_blockers`.
	topics = training.topics(args.get("content_topics_covered"))
	if topics:
		doc.content_topics_covered = ", ".join(topics)
	if args.get("regimes") not in (None, ""):
		try:
			training.set_rows(doc, "regimes", training.require(args.get("regimes")))
		except ValueError as exc:
			raise ToolError(f"{exc} Nothing was changed.") from None
	expires = as_date(args, "expires_date")
	if expires:
		doc.expires_date = expires
	if topics or expires or args.get("regimes") not in (None, ""):
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		doc = frappe.get_doc(DOCTYPE, doc.name)

	described = _described(dict(doc.as_dict()))
	blockers = training_sessions.completion_blockers(described)
	if blockers:
		raise ToolError(
			f"{doc.name} cannot be completed yet: "
			+ " ".join(f"({index + 1}) {blocker}" for index, blocker in enumerate(blockers))
			+ " Nothing was changed."
		)

	skip = as_bool(args, "skip_incomplete", False)
	incomplete = [entry for entry in described["attendee_rows"] if entry["state"] == training_sessions.ATTENDEE_INCOMPLETE]
	if incomplete and not skip:
		raise ToolError(
			f"{len(incomplete)} of {described['attendance']['attendees']} attendee(s) on "
			f"{doc.name} were marked present and cannot be proved to have been there: "
			+ "; ".join(
				f"{entry['employee_name'] or entry['employee']} (no {', no '.join(entry['missing'])})"
				for entry in incomplete
			)
			+ ". A sheet where somebody never signed is a sheet worth fixing while the crew is "
			"still on site, which is why this refuses rather than quietly filing the rest. Scan "
			"or sign them, untick `attended` for anybody who did not come, or pass "
			"skip_incomplete=true to file the ready rows and leave these named. Nothing was "
			"changed."
		)

	ready = [entry for entry in described["attendee_rows"] if entry["state"] == training_sessions.ATTENDEE_READY]
	regimes = described["regimes"]
	filed: list = []
	failed: list = []
	for entry in ready:
		payload = {
			"employee": entry["employee"],
			"company": described["company"],
			"training_type": described["training_type"],
			"regimes": regimes,
			"content_topics_covered": described["content_topics_covered"],
			"completed_date": described["session_date"],
			"person_performed_signature": entry["signature"],
		}
		for key, value in (
			("completed_time", described["start_time"]),
			("expires_date", described["expires_date"]),
			("training_source", described["training_source"]),
			("provider", described["provider"] or described["instructor_name"]),
			("notes", f"Recorded from training session {doc.name}."),
		):
			if value:
				payload[key] = value
		try:
			result = training_tools.record_training(payload)
		except ToolError as exc:
			failed.append({"employee": entry["employee"], "employee_name": entry["employee_name"], "reason": str(exc)})
			continue
		filed.append(
			{
				"employee": entry["employee"],
				"employee_name": entry["employee_name"],
				"training_record": result.data.get("name"),
				"fsma_112_161_gaps": result.data.get("fsma_112_161_gaps") or [],
				"supersedes": [item["name"] for item in result.data.get("supersedes") or []],
			}
		)

	# Written back one row at a time rather than through the parent: `record_training`
	# has just inserted documents, and re-saving the session would re-run every
	# attendee check against a document that has changed underneath this call.
	for item in filed:
		index = _attendee_index(doc, item["employee"])
		if index >= 0:
			frappe.db.set_value(
				training_sessions.ATTENDEE_DOCTYPE,
				doc.attendees[index].name,
				"training_record",
				item["training_record"],
			)

	completed_at = as_str(args, "completed_at") or frappe.utils.now()
	closing = frappe.get_doc(DOCTYPE, doc.name)
	if not failed:
		closing.status = training_sessions.STATUS_COMPLETED
		closing.completed_at = completed_at
		closing.completed_by = frappe.session.user
	closing.records_created = int(closing.get("records_created") or 0) + len(filed)
	closing.flags.ignore_permissions = True
	closing.save(ignore_permissions=True)

	final = _described(dict(frappe.get_doc(DOCTYPE, doc.name).as_dict()))
	data = {
		**final,
		"actor": actor,
		"records_filed": filed,
		"filed_count": len(filed),
		"failed": failed,
		"skipped_incomplete": [
			{
				"employee": entry["employee"],
				"employee_name": entry["employee_name"],
				"missing": entry["missing"],
			}
			for entry in incomplete
		],
		"absent": [
			entry["employee"]
			for entry in final["attendee_rows"]
			if entry["state"] == training_sessions.ATTENDEE_ABSENT
		],
		"retention_note": training.retention_note(regimes),
		"packets_that_will_pull_them": regimes,
		"note": (
			f"{len(filed)} training record(s) written from one afternoon, each on its own person's "
			f"file and each tagged {', '.join(regimes)}. The compliance matrix reads them "
			"individually — the session is the act, the records are the evidence, and the attendee "
			"rows link the two so an auditor can walk from a register back to the sheet."
		),
		"next_step": (
			"sign_training_supervisor_review on each record — §112.161(b) asks for a review by a "
			"supervisor within a reasonable time AFTER the record is made, which is a sequence "
			"rather than a form field, and it is the element a GAP-only operation most often lacks."
		),
	}
	if failed:
		data["failure_note"] = (
			f"{len(failed)} attendee(s) could not be filed and the session is still "
			f"{final['status']} because of it. The {len(filed)} that succeeded ARE filed and a "
			"second call will not duplicate them — a row that produced a record is skipped. Fix "
			"what is named and call again."
		)
	if incomplete:
		data["skip_note"] = (
			f"{len(incomplete)} attendee(s) were marked present without the evidence to prove it "
			"and were skipped at the caller's instruction. Their rows are kept — deleting them "
			"would lose the fact that they were expected, which is the fact a supervisor needs on "
			"Monday."
		)
	if not final["expires_date"]:
		data["expiry_note"] = (
			"These records carry no expiry, so the compliance calendar will never ask for them to "
			"be renewed. Right for a new-hire orientation; WRONG for WPS, Oregon heat illness and "
			"annual GAP hygiene — those lapse at twelve months and a record that cannot lapse is a "
			"gap nobody is warned about."
		)
	return ToolResult(
		data=data,
		summary=(
			f"completed {doc.name} — {len(filed)} training record(s) filed from "
			f"{final['attendance']['attendees']} attendee(s)"
			+ (f", {len(failed)} failed" if failed else "")
			+ (f", {len(incomplete)} skipped" if incomplete else "")
		),
		docstatus_delta="0 → 0 (amended)",
	)


# ── 7. get_training_session ─────────────────────────────────────────────────
def get_training_session(args: dict) -> ToolResult:
	"""One session in full: the curriculum, the sheet, and what is still outstanding."""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_session(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	described = _described(row)
	described["curriculum"] = training_sessions.describe_type(
		training_sessions.type_row(str(row.get("training_type") or ""))
	)
	described["retention_note"] = training.retention_note(described["regimes"])
	described["regime_notes"] = {
		regime: training.REGIME_NOTES[regime]
		for regime in described["regimes"]
		if regime in training.REGIME_NOTES
	}
	described["completion_blockers"] = (
		training_sessions.completion_blockers(described)
		if described["status"] in training_sessions.OPEN_STATUSES
		else []
	)
	described["actor"] = actor

	attendance = described["attendance"]
	if described["status"] == training_sessions.STATUS_COMPLETED:
		described["note"] = (
			f"Completed on {described['completed_at']}: {attendance['recorded']} training "
			"record(s) were written and each is on its own person's file. The attendee rows name "
			"them, which is the trail an auditor walks from a register back to the afternoon."
		)
	elif described["completion_blockers"]:
		described["note"] = (
			f"{len(described['completion_blockers'])} thing(s) stand between this session and a "
			"completion, listed in completion_blockers. None of them is an attendee who has not "
			"signed — that person simply produces no record."
		)
	else:
		described["note"] = (
			f"{attendance['ready']} of {attendance['attendees']} attendee(s) can be proved to have "
			"been here. complete_training_session writes a training record for each."
		)
	return ToolResult(
		data=described,
		summary=(
			f"{row['name']} — {described['training_type']} on {described['session_date']} "
			f"({described['status']}); {attendance['attendees']} attendee(s), "
			f"{attendance['ready']} ready, {attendance['recorded']} recorded, "
			f"{attendance['incomplete']} incomplete"
		),
	)


# ── 8. list_training_sessions ───────────────────────────────────────────────
def list_training_sessions(args: dict) -> ToolResult:
	"""The session register, filtered the five ways a foreman or an auditor asks.

	`employee` IS THE FILTER THAT MAKES THIS MORE THAN A DIARY. "Which sessions
	was Ana at" is answered off the attendee rows rather than off the training
	register, which means it includes the session she attended and did not sign —
	the one that produced no record and is therefore invisible to
	`list_trainings`. That gap is exactly the thing somebody is looking for when
	they ask.
	"""
	_require()
	actor = employee_tool.require_hr_role()
	limit = min(as_limit(args), SESSION_CAP)

	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	else:
		from .. import roles

		allowed = roles.companies_for(actor) or []
		if allowed:
			filters["company"] = ("in", allowed)

	curriculum = as_str(args, "training_type")
	if curriculum:
		filters["training_type"] = _resolve_type(curriculum)

	status = as_str(args, "status")
	if status:
		canonical = training_sessions.canon_status(status)
		if not canonical:
			raise ToolError(
				f"status {status!r} is not one of {', '.join(training_sessions.STATUSES)}."
			)
		filters["status"] = canonical

	conductor = as_str(args, "conducted_by")
	if conductor:
		filters["conducted_by"] = employee_tool.resolve_employee(conductor)

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["session_date"] = ("between", [from_date, to_date])
	elif from_date:
		filters["session_date"] = (">=", from_date)
	elif to_date:
		filters["session_date"] = ("<=", to_date)

	found = training_sessions.rows(filters, limit=max(limit * 4, limit))
	attendees = training_sessions.attendees_for_parents([entry.get("name") for entry in found])
	described = [
		training_sessions.describe(entry, attendees.get(str(entry.get("name") or ""), []))
		for entry in found
	]

	person = as_str(args, "employee")
	if person:
		wanted = employee_tool.resolve_employee(person)
		described = [
			entry
			for entry in described
			if any(item["employee"] == wanted for item in entry["attendee_rows"])
		]

	regime = as_str(args, "regime")
	if regime:
		target = training.canon(regime)
		if not target:
			raise ToolError(f"regime {regime!r} is not one this app knows. {training.vocabulary_note()}")
		described = [entry for entry in described if target in entry["regimes"]]

	truncated = len(described) > limit
	described = described[:limit]

	# The listing does not carry every attendee row — forty sessions of twelve is
	# five hundred rows to answer "what happened in June" — but it DOES carry the
	# counts, which is what a caller filters on before opening one.
	for entry in described:
		entry.pop("attendee_rows", None)

	incomplete = [
		entry["name"]
		for entry in described
		if entry["status"] in training_sessions.OPEN_STATUSES and entry["attendance"]["incomplete"]
	]
	data = {
		"company": company,
		"count": len(described),
		"limit": limit,
		"truncated": truncated,
		"sessions": described,
		"by_status": _by_status(described),
		"awaiting_completion": [
			entry["name"] for entry in described if entry["status"] in training_sessions.OPEN_STATUSES
		],
		"with_unproved_attendance": incomplete,
		"note": (
			f"{len(incomplete)} open session(s) hold somebody marked present without a badge scan "
			"or a signature. Those rows produce no training record, so the person is trained and "
			"the compliance matrix does not know it — which is the gap that gets found by an "
			"inspector rather than by a report."
			if incomplete
			else "Nothing open in this selection has an attendee whose presence cannot be proved."
		),
		"attendee_note": (
			"Attendee rows are omitted from a listing and the counts are not. get_training_session "
			"has the sheet."
		),
	}
	if truncated:
		data["truncation_note"] = (
			f"More than {limit} session(s) matched and this is the first {limit}. Narrow by "
			"company, curriculum or period before relying on the counts above."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} training session(s)"
			+ (f" of {filters['training_type']}" if curriculum else "")
			+ f"; {len(incomplete)} with attendance that cannot be proved"
		),
	)


def _by_status(described: list) -> dict:
	counted = {status: 0 for status in training_sessions.STATUSES}
	for entry in described:
		counted[entry["status"]] = counted.get(entry["status"], 0) + 1
	return counted
