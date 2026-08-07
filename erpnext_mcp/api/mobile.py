# SPDX-License-Identifier: MIT
"""The twenty-four methods the Farm Ops app calls, as whitelisted Frappe endpoints.

    POST /api/method/erpnext_mcp.api.mobile.<method>
    Authorization: token <api_key>:<api_secret>
    X-FarmOps-Token: <api_key>:<api_secret>

THE SECOND HEADER IS NOT BELT-AND-BRACES FOR ITS OWN SAKE. v0.17.2: the Tailscale
`serve`/`funnel` proxy removes `Authorization`, so every call arrived as Guest and
Frappe rendered `/me` at a phone that had presented a perfectly good credential.
`api/fallback_auth.py` reads the same pair out of `X-FarmOps-Token`, or out of
`_auth` in the POST body when even that does not survive, and establishes the
identical session. Every gate below runs unchanged on whichever door was used.

One function per method, named exactly as
`FarmOpsKit/Sources/FarmOpsKit/Networking/MobileAPI.swift` names it. THERE IS NO
DISPATCHER HERE ON PURPOSE: a method exists as a function or its path 404s, so
the whole reachable surface is the `@frappe.whitelist()` lines below and an
auditor establishes that by reading them. A generic `call(tool_name, args)`
would have been fewer lines and would have published all two hundred MCP tools —
including `create_journal_entry`, `convey_parcel` and `import_chart_of_accounts`
— to anything holding a field worker's phone.

EACH WRAPPER DOES FOUR THINGS AND NO MORE:

    validate the arguments  →  delegate  →  shape for the app  →  return

`@guard.endpoint` has already run the kill switch, the role gate, the enrolment
gate and the rate limit by the time the body starts, and writes the audit row
and strips secrets on the way out whichever way the call went. The body's own
job is the part that is specific to this method: check the docnames, refuse the
companies this caller cannot reach, and never pass an argument through blind.

WHAT IS DELIBERATELY *NOT* PASSED THROUGH is as much the design as what is.
`reject_farm_task` takes `cancel=true`, which cancels a task outright instead of
returning it to the pool; a worker handing work back must not be able to delete
it, so the wrapper never forwards it. `complete_farm_task` takes `record_data`,
which writes arbitrary fields into the compliance record it produces; the phone
has no business composing that. `list_dispatched_tasks` takes `worker_id`, and
the wrapper fills it in from the authenticated caller rather than the body — an
account that can name somebody else in a request body is not scoped to anything.

THE RULES STAY IN `tools/dispatch.py`. The concurrent-claim limit, the refusal
to self-pick Dispatched work, the evidence contract and the refusal of a
completion filed by somebody who was not there are all still enforced by Sprint
8's code, because it IS Sprint 8's code. A wrapper with its own copy would be a
second set of compliance rules to keep in step, and they would drift.

────────────────────────────────────────────────────────────────────────────
THE ONBOARDING, CREW-CLOCK AND BUCKET METHODS CARRY A SECOND ROLE GATE
────────────────────────────────────────────────────────────────────────────

v0.45.0 published nine more, and they are not like the first fifteen. Every
tool the original wrappers delegate to is field work with no role check of its
own; these nine reach `tools/i9.py`, `tools/w4.py`, `tools/shifts.py` and
`tools/bucket_log.py`, and each of THOSE calls `employee.require_hr_role()` or
`kpi.require_kpi_role()` before it writes a row.

THAT GATE IS LEFT EXACTLY WHERE IT IS, and the consequence is stated here
rather than discovered in an orchard: of the roles `guard.FARM_OPS_ROLES`
admits, only **Farm Manager** is also in `employee.HR_ROLES` and
`kpi.KPI_ROLES`. A Field Worker or a Foreman holding a perfectly good grant
gets through all seven of `guard`'s gates and is then refused by the tool with
its own sentence. That is the correct refusal — an I-9 is a personnel record
and a shift is a wage record, and neither is a picker's to write — but it means
an operator enrolling somebody to run onboarding or the crew clock must enrol
them as a Farm Manager, or grant the account one of the HR roles in the Desk.

Copying the gate up here, or widening it, would be the same mistake the
paragraph above refuses for the dispatch rules: two sets of personnel rules to
keep in step.
"""

from __future__ import annotations

import frappe

from ..errors import ToolError
from ..tools import asset_tags, bucket_log, dispatch, fieldwork, i9, shifts, w4
from ..tools import mobile as mobile_tools
from . import guard, shape

FARM_TASK = "Farm Task"
FARM_TASK_ASSIGNMENT = "Farm Task Assignment"
EMPLOYEE = "Employee"
FARM_SHIFT = "Farm Shift"

#: Most bucket captures one sync call carries. `tools/bucket_log.BATCH_CAP` is
#: the tool's own limit and is read rather than restated, so a phone and a Desk
#: import cannot come to disagree about how big a batch is.
BUCKET_BATCH_CAP = bucket_log.BATCH_CAP


def _employee(user: str) -> str:
	"""The caller's Employee docname, or the refusal that says how to fix it."""
	employee = fieldwork._employee_for(user)
	if not employee:
		frappe.throw(
			f"{user} has no Employee record on this site, and a task assignment names an "
			"Employee rather than a login. Ask an operator to set `user_id` on your Employee "
			"record to this address.",
			frappe.ValidationError,
		)
	return employee


def _company(user: str, company, allowed: list) -> str:
	"""One company argument, validated, falling back to the caller's first entity.

	`guard.require_company` answers "" for "nothing was asked for", which every
	READ in this file reads as "all of mine". A write has to name exactly one, and
	the app sends it on every onboarding call — so the fallback is for the phone
	that does not, and it can only ever pick an entity this account already
	reaches, because `guard.require_scope` refused it otherwise.
	"""
	return guard.require_company(user, company, allowed) or (allowed[0] if allowed else "")


def _employee_argument(employee, allowed: list, label: str = "employee") -> str:
	"""An Employee docname from the body, proved to exist inside the caller's entities.

	NOT the caller's own Employee — that is `_employee`, and the two are different
	on purpose. Onboarding and the crew clock are things somebody does TO another
	person's record, so the docname has to come from the body; what makes that
	safe is that it is checked against `Employee.company` the same way a task
	docname is, and an Employee of an entity this account cannot see reads as not
	found rather than as refused.
	"""
	return guard.require_scoped_doc(EMPLOYEE, employee, label, allowed)


def _assignment(task: str, assignment, allowed: list) -> str:
	"""An optional task_assignment argument, validated and proved to fit the task.

	The app usually sends one and the tools can find it without help. When it IS
	sent it is checked BOTH ways — that it exists within the caller's entities,
	and that it actually belongs to the task named alongside it — because an
	assignment docname from another task is the one argument here that could
	otherwise move work between records.
	"""
	value = str(assignment or "").strip()
	if not value:
		return ""
	value = guard.require_scoped_doc(FARM_TASK_ASSIGNMENT, value, "task_assignment", allowed)
	if str(frappe.db.get_value(FARM_TASK_ASSIGNMENT, value, "task") or "") != task:
		frappe.throw(f"task_assignment {value} does not belong to {task}.", frappe.ValidationError)
	return value


def _evidence(raw) -> list:
	"""The app's evidence list, translated to what `complete_farm_task` takes.

	The phone knows `{"file_token", "file_name", "sha256", "kind"}` because that
	is what `finalize_staged_file` handed it; the tool wants
	`{"file", "evidence_type"}`. `file_token` IS the File docname — see
	`api/files.finalize_staged_file`, which is the only thing that mints one — so
	this is a rename rather than a lookup, and `normalise_evidence` still refuses
	any docname that is not a real File on this site.

	The sha256 the app carries was already verified against the assembled bytes
	at finalize, and that verification is what the audit row for the upload
	records. Farm Task Evidence has no hash column to put it in, so it is not
	silently dropped into `caption` — see RELEASES/v0.17.1.md, which names the
	column as the follow-up if the hash should live on the record itself.
	"""
	if raw in (None, ""):
		return []
	if isinstance(raw, (str, dict)):
		raw = [raw]
	if not isinstance(raw, list):
		raise ToolError(
			'evidence_files must be a list of objects like {"file_token": "...", "kind": "photo"}.'
		)
	kinds = {"photo": "Photo", "signature": "Signature", "video": "Video", "document": "Document"}
	out = []
	for index, entry in enumerate(raw):
		if isinstance(entry, str):
			out.append({"file": entry, "evidence_type": "Photo"})
			continue
		if not isinstance(entry, dict):
			raise ToolError(f"evidence_files[{index}] must be an object.")
		token = str(entry.get("file_token") or entry.get("file") or "").strip()
		url = str(entry.get("file_url") or "").strip()
		if not (token or url):
			raise ToolError(f"evidence_files[{index}] names neither a file_token nor a file_url.")
		kind = str(entry.get("kind") or entry.get("evidence_type") or "photo").strip().lower()
		row = {"evidence_type": kinds.get(kind, "Photo")}
		if token:
			row["file"] = token
		if url:
			row["file_url"] = url
		if entry.get("file_name"):
			row["caption"] = str(entry["file_name"])[:140]
		out.append(row)
	return out


def _location(given, latitude, longitude) -> str:
	"""Where the work was done, as one string, from whichever half the app sent.

	v0.19.1. A TYPED PLACE NAME BEATS A COORDINATE. The handset's fix is the
	usual answer, but a worker who wrote "MC-Cabin-01" did so in a shed where
	the fix was absent or wrong, and overwriting that with whatever the GPS
	eventually settled on outside would replace a fact with a guess.

	A pair that will not parse as numbers is DROPPED rather than raised on. The
	field is optional and additive; failing a completion — with its photographs,
	its signature and its compliance record — over a malformed coordinate would
	trade the whole record for its least important field. The latitude and
	longitude as sent are in the audit row regardless, which is where a
	malformed pair is worth looking at anyway.
	"""
	typed = str(given or "").strip()
	if typed:
		return typed[:140]
	if latitude in (None, "") or longitude in (None, ""):
		return ""
	try:
		return f"{float(latitude):.7f},{float(longitude):.7f}"
	except (TypeError, ValueError):
		return ""


def _bucket_entries(raw, company: str) -> list:
	"""The handset's capture queue, translated to what `sync_bucket_entries` takes.

	THE COMPANY IS STAMPED ON EVERY ENTRY FROM THE CALL, NEVER READ OFF ONE. The
	tool resolves `company` per entry, which is right for a Desk import that may
	legitimately carry two entities in one file. On this transport it would be a
	hole: one batch could write Bucket Log Entry rows against an entity the caller
	cannot see, and a picking record is a payroll record. So the wrapper takes ONE
	company argument, checks it against the caller's scope once, and overwrites
	whatever each entry claimed.

	`employee` IS NOT ACCEPTED ON AN ENTRY, for the same reason `list_my_tasks`
	fills `worker_id` from the session. The badge is what attributes a bucket, the
	Bucket Log Badge Map is what resolves it, and `link_badge_to_employee` is the
	deliberate act that populates that register — a phone that could name the
	picker directly would be able to move somebody else's piece-rate onto its own
	badge without ever touching the map an operator reads.

	The rest is a rename. `FarmOpsKit/Capture/BucketEntry.swift` encodes `id`,
	`session_id`, `badge_id` and `accepted`; the doctype's columns are
	`entry_uuid`, `session_uuid`, `worker_badge` and `verdict`. Both spellings are
	accepted so a Desk-shaped payload and the handset's own both work, and
	`accepted` is translated to the Select's two options rather than passed
	through — the app has a boolean and the register has words.
	"""
	if raw in (None, ""):
		raise ToolError('entries is required — a sync with nothing in it is not a sync.')
	if isinstance(raw, dict):
		raw = [raw]
	if not isinstance(raw, list):
		raise ToolError('entries must be a list of objects like {"entry_uuid": "...", "verdict": "Accepted"}.')
	if not raw:
		raise ToolError("entries is required — a sync with nothing in it is not a sync.")
	if len(raw) > BUCKET_BATCH_CAP:
		raise ToolError(
			f"{len(raw)} captures is more than one sync call accepts ({BUCKET_BATCH_CAP}). Send the "
			"queue in slices. Nothing was changed."
		)

	out = []
	for index, entry in enumerate(raw):
		if not isinstance(entry, dict):
			raise ToolError(f"entries[{index}] must be an object.")
		verdict = str(entry.get("verdict") or "").strip()
		if not verdict and entry.get("accepted") is not None:
			verdict = "Accepted" if entry.get("accepted") in (True, 1, "1", "true", "True") else "Rejected"
		row = {
			"company": company,
			"entry_uuid": str(entry.get("entry_uuid") or entry.get("id") or "").strip(),
			"session_uuid": str(entry.get("session_uuid") or entry.get("session_id") or "").strip(),
			"worker_badge": str(entry.get("worker_badge") or entry.get("badge_id") or "").strip(),
			"timestamp": entry.get("timestamp"),
			"verdict": verdict,
		}
		for key in ("coverage_percent", "gps_lat", "gps_lon"):
			if entry.get(key) not in (None, ""):
				row[key] = entry[key]
		for key in ("model_uuid", "h3_cell", "device_id"):
			value = str(entry.get(key) or "").strip()
			if value:
				row[key] = value
		out.append(row)
	return out


def _crew(raw, allowed: list) -> list:
	"""The crew a foreman rostered when opening a shift, each name checked here.

	`shifts._crew_argument` accepts docnames, employee ids and names and resolves
	them, and `start_shift` then refuses any crew member employed by another
	entity. Both still run. What this adds is the check the mobile surface always
	adds and the tool layer deliberately does not: a name that resolves to an
	Employee of an entity THIS CALLER cannot reach reads as not found, so a phone
	cannot enumerate the holding company's payroll by watching which names roster.

	A bare string, a comma-joined string and a list of `{"employee", "role"}`
	objects all arrive in practice — the handset sends the last of those — so all
	three are normalised here rather than argued about downstream.
	"""
	if raw in (None, "", []):
		return []
	if isinstance(raw, str):
		raw = [part.strip() for part in raw.split(",") if part.strip()]
	if isinstance(raw, dict):
		raw = [raw]
	if not isinstance(raw, list):
		raise ToolError('crew_employees must be a list of employees, or of objects like {"employee": "..."}.')

	out = []
	for index, entry in enumerate(raw):
		if isinstance(entry, dict):
			person = entry.get("employee") or entry.get("name")
			role = str(entry.get("role") or "").strip()
			joined = str(entry.get("joined_at") or "").strip()
		else:
			person, role, joined = entry, "", ""
		if not str(person or "").strip():
			raise ToolError(f"crew_employees[{index}] names nobody.")
		row = {"employee": _employee_argument(person, allowed, f"crew_employees[{index}]")}
		if role:
			row["role"] = role
		if joined:
			row["joined_at"] = joined
		out.append(row)
	return out


# ── 1. get_current_user_context ─────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("get_current_user_context", limit=guard.READ_LIMIT)
def get_current_user_context(user: str) -> dict:
	"""Who is calling, what they may do, and which entity the app opens on.

	Doubles as credential validation: the app calls it immediately after a scan
	and on every manual refresh, and treats a 401 as "this credential is dead,
	sign out and re-scan". Frappe answers the 401 itself when the token is bad,
	so this only ever runs for a credential that already checked out.
	"""
	guard.require_scope(user)
	result = mobile_tools.get_current_user_context({})
	return shape.user_context(result.data, user)


# ── 2. list_my_tasks ────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("list_my_tasks", limit=guard.READ_LIMIT)
def list_my_tasks(user: str, company=None) -> dict:
	"""What this worker is holding right now: claimed and in progress."""
	allowed = guard.require_scope(user)
	wanted = guard.require_company(user, company, allowed)

	result = fieldwork.list_my_tasks({"company": wanted} if wanted else {})
	rows = []
	for entry in result.data.get("assignments") or []:
		detail = entry.get("task_detail")
		if detail:
			rows.append(shape.task(detail, entry))
	rows = guard.scoped(rows, allowed)
	return {"tasks": rows, "count": len(rows), "company": wanted or None}


# ── 3. list_available_tasks ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("list_available_tasks", limit=guard.READ_LIMIT)
def list_available_tasks(user: str, company=None) -> dict:
	"""The pool this worker could take from.

	COMPANY IS ADVISORY AND THE SERVER FILTERS ANYWAY. The app's own contract
	asks for this in as many words — "a client that sends nothing must not
	receive everything" — and `list_available_tasks` in the tool layer reads
	through `frappe.db.get_all`, which does NOT consult User Permissions. So with
	no company argument the pool is fetched once per accessible entity rather
	than once unfiltered, and `guard.scoped` checks the result again on the way
	out.
	"""
	allowed = guard.require_scope(user)
	wanted = guard.require_company(user, company, allowed)

	rows, may_claim, remaining = [], True, None
	for entity in [wanted] if wanted else allowed:
		result = fieldwork.list_available_for_me({"company": entity})
		rows.extend(shape.tasks(result.data.get("tasks") or []))
		if result.data.get("may_claim") is False:
			may_claim = False
		claims = result.data.get("claims_remaining")
		if claims is not None:
			remaining = claims if remaining is None else min(remaining, claims)

	seen, unique = set(), []
	for row in guard.scoped(rows, allowed):
		if row.get("name") in seen:
			continue
		seen.add(row.get("name"))
		unique.append(row)
	return {
		"tasks": unique,
		"count": len(unique),
		"company": wanted or None,
		"may_claim": may_claim,
		"claims_remaining": remaining,
	}


# ── 4. get_task ─────────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("get_task", limit=guard.READ_LIMIT)
def get_task(user: str, task=None) -> dict:
	"""One task in full: the job, the contract, and why it exists."""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(FARM_TASK, task, "task", allowed)

	result = fieldwork.get_task_with_evidence_contract({"task": name})
	data = result.data
	out = shape.task(data.get("task") or {}, data.get("live_assignment") or {})
	out["is_mine"] = data.get("is_mine")
	out["evidence_contract"] = data.get("evidence_contract")
	out["evidence_outstanding"] = data.get("evidence_outstanding")
	out["evidence_complete"] = data.get("evidence_complete")
	return out


# ── 5. claim_task ───────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("claim_task", limit=guard.WRITE_LIMIT, mutating=True)
def claim_task(user: str, task=None) -> dict:
	"""Take one task from the pool.

	Never queued offline by the app, and it must not be: two workers offline
	would both believe they own the same cabin, and the concurrent-claim limit
	cannot be enforced from a phone.
	"""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(FARM_TASK, task, "task", allowed)

	result = fieldwork.claim_task_via_mobile({"task": name})
	# v0.18.2: dispatch.claim_farm_task spreads task fields at the TOP LEVEL of
	# data (see dispatch.py `_describe_task(dict(task_doc.as_dict()))` inside
	# `data={**_describe_task(...), "assignment": ..., ...}`), not nested under
	# a "task" key like start_farm_task does. shape.task(data.get("task") or {})
	# passed an empty dict and emitted {"name": null, ...}, which crashed the
	# iOS Codable decoder with "Bad value at 'name'". Extract the task fields
	# out of the flat response instead of asking for a "task" wrapper that
	# isn't there.
	task_row = {
		key: value
		for key, value in result.data.items()
		if key
		not in (
			"assignment",
			"concurrent_claims",
			"claims_remaining",
			"evidence_you_will_need",
			"me",
			"next",
		)
	}
	return shape.task(task_row, result.data.get("assignment") or {})


# ── 6. start_task ───────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("start_task", limit=guard.WRITE_LIMIT, mutating=True)
def start_task(user: str, task=None, task_assignment=None) -> dict:
	"""Clock in on one claimed task. `started_at` is what duration counts from."""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(FARM_TASK, task, "task", allowed)
	assignment = _assignment(name, task_assignment, allowed)

	inner = {"task": name}
	if assignment:
		inner["assignment"] = assignment
	result = fieldwork.start_task_via_mobile(inner)
	return shape.task(result.data.get("task") or {}, result.data.get("assignment") or {})


# ── 7. complete_task_via_mobile ─────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("complete_task_via_mobile", limit=guard.COMPLETE_LIMIT, mutating=True)
def complete_task_via_mobile(
	user: str,
	task=None,
	task_assignment=None,
	findings_text=None,
	completion_narrative=None,
	completed_at=None,
	actual_duration_minutes=None,
	clean_pass=None,
	witness=None,
	latitude=None,
	longitude=None,
	farm_location_gps=None,
	evidence_files=None,
	visit_id=None,
) -> dict:
	"""Finish one task: file the evidence, write the compliance record.

	THE ARGUMENTS ARE LISTED RATHER THAN FORWARDED. Frappe hands a whitelisted
	method whatever keys the body carried, so `**kwargs` here would pass the
	phone's JSON straight into `complete_farm_task` — including `record_data`,
	which writes arbitrary fields into the compliance record, and `worker_id`,
	which names whose completion it is. Naming every accepted argument is what
	makes those two unreachable.

	`findings_text` is passed THROUGH THE PRESENCE TEST, not through truthiness.
	An empty string is a positive statement — "I looked and nothing was wrong" —
	and the tool layer distinguishes it from the argument being absent, which
	records that nobody was asked.

	`latitude`/`longitude` NOW REACH THE RECORD. v0.19.1 added
	`farm_location_gps` to Farm Task Assignment, so the pair the shipped app has
	been sending since v0.18 stops being audit-row-only and becomes the location
	half of FSMA §112.161(a)(1)(i). An explicit `farm_location_gps` wins — a
	worker who typed "MC-Cabin-01" in a shed with no fix said something the
	handset could not — and the coordinates are formatted only as a fallback.
	Both still land in the audit row either way, unchanged.

	v0.20.1. THIS CALL IS SAFE TO SEND TWICE, which it was not before. A queued
	completion that reached the server and whose acknowledgement did not reach
	the handset used to come back as a hard error about work that was already
	filed — three Failed entries per task in a sync queue, on an iPad, over an
	evening's real work. `complete_farm_task` now recognises an identical
	resubmission by its signature and answers with the completion already on
	record and `x_idempotent: true`. IT IS STILL A REFUSAL when the second
	submission is a different one: a different worker, different evidence or a
	different account of the work is a conflict, and absorbing it silently would
	be a worse bug than the one being fixed.

	`visit_id` IS THE HANDSET'S, forwarded as sent. It groups the completions of
	one trip so `list_visits` can report the trip rather than five unrelated
	tasks, and nothing on this side validates its shape — see the tool wrapper.
	"""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(FARM_TASK, task, "task", allowed)
	assignment = _assignment(name, task_assignment, allowed)

	inner = {"task": name}
	if assignment:
		inner["assignment"] = assignment
	if findings_text is not None:
		inner["findings_text"] = findings_text
	if clean_pass is not None:
		inner["clean_pass"] = clean_pass
	for key, value in (
		("completion_narrative", completion_narrative),
		("witness", witness),
		("completed_at", completed_at),
		("actual_duration_minutes", actual_duration_minutes),
		("visit_id", visit_id),
	):
		if value is not None:
			inner[key] = value
	location = _location(farm_location_gps, latitude, longitude)
	if location:
		inner["farm_location_gps"] = location
	evidence = _evidence(evidence_files)
	if evidence:
		inner["evidence_files"] = evidence

	result = fieldwork.complete_task_via_mobile(inner)
	return shape.completion(result.data)


# ── 8. reject_task ──────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("reject_task", limit=guard.WRITE_LIMIT, mutating=True)
def reject_task(user: str, task=None, task_assignment=None, reason=None) -> dict:
	"""Hand one task back to the pool, with a reason that goes on the record.

	`cancel` IS NOT ACCEPTED. `reject_farm_task` takes it, and it cancels the
	task outright instead of returning it to the pool — a worker saying "I could
	not do this" must not be able to make the work disappear, so the argument
	stops here and the task always goes back.
	"""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(FARM_TASK, task, "task", allowed)
	assignment = _assignment(name, task_assignment, allowed)
	if not str(reason or "").strip():
		frappe.throw(
			"A reason is required to hand a task back. 'The ladder is broken and I could not "
			"reach the detector' is a fact somebody can act on; an empty rejection is not.",
			frappe.ValidationError,
		)

	inner = {"task": name, "reason": str(reason).strip(), "worker_id": _employee(user), "cancel": False}
	if assignment:
		inner["assignment"] = assignment
	result = dispatch.reject_farm_task(inner)
	return {
		"task": (result.data.get("task") or {}).get("name"),
		"returned_to_state": result.data.get("returned_to_state"),
		"reason": str(reason).strip(),
	}


# ── 10. report_field_task ───────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("report_field_task", limit=guard.WRITE_LIMIT, mutating=True)
def report_field_task(
	user: str,
	location_doctype=None,
	location=None,
	task_type=None,
	skill_required=None,
	urgency=None,
	description=None,
	photo_file_token=None,
	asset=None,
) -> dict:
	"""A worker in the field flags a problem on the spot.

	THE FIELD REPORT IS THE WORK ORDER. The worker taps, snaps a photo,
	describes the problem, and the task is in the pool — one act, not a
	two-step process with a separate ticket doctype between them.

	`reported_by` IS FILLED FROM THE AUTHENTICATED CALLER, not from the
	body. An account that can name somebody else in a request body is not
	scoped to anything — the same principle as `list_my_tasks` filling
	`worker_id` from the session.

	`urgency` is CAPPED: field workers may choose Normal or High. Critical
	is restricted to Foreman and Farm Manager roles, and the tool enforces
	that — the wrapper passes the value through because the tool layer
	already has the role check.
	"""
	allowed = guard.require_scope(user)
	employee = _employee(user)

	inner = {"reported_by": employee}
	if location_doctype:
		inner["location_doctype"] = str(location_doctype).strip()
	if location:
		inner["location"] = str(location).strip()
	if task_type:
		inner["task_type"] = str(task_type).strip()
	if skill_required:
		inner["skill_required"] = str(skill_required).strip()
	if urgency:
		inner["urgency"] = str(urgency).strip()
	if description:
		inner["description"] = str(description).strip()
	if photo_file_token:
		inner["photo_file_token"] = str(photo_file_token).strip()
	if asset:
		inner["asset"] = str(asset).strip()

	company = guard.require_company(user, inner.get("company"), allowed) if inner.get("company") else ""
	if not company and allowed:
		inner["company"] = allowed[0]
	elif company:
		inner["company"] = company

	result = dispatch.report_field_task(inner)
	data = result.data
	return shape.task(data, None)


# ── 11. list_compliance_alerts ──────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("list_compliance_alerts", limit=guard.READ_LIMIT)
def list_compliance_alerts(user: str, company=None) -> dict:
	"""Open compliance alerts for the entities this caller may reach. View-only.

	THE ROLE GATE IS NOT THE APP'S. `UserContext.canViewCompliance` hides the
	Compliance tab from a picker, and the app's own contract says in as many
	words that this is UI courtesy rather than the security boundary. The gate
	that matters is `guard.endpoint` plus the entity scoping below, both of which
	run whatever the app decided to draw.
	"""
	allowed = guard.require_scope(user)
	wanted = guard.require_company(user, company, allowed)

	inner = {"company": wanted} if wanted else {}
	result = fieldwork.list_compliance_calendar_for_me(inner)
	rows = guard.scoped(shape.alerts(result.data.get("alerts") or []), allowed)
	return {
		"alerts": rows,
		"count": len(rows),
		"company": wanted or None,
		"critical": len([row for row in rows if row.get("severity") == "Critical"]),
		"overdue": len([row for row in rows if row.get("overdue")]),
	}


# ── 12. scan_asset ────────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("scan_asset", mutating=True, limit=guard.WRITE_LIMIT)
def scan_asset(user: str, asset_name=None, gps_lat=None, gps_lon=None) -> dict:
	"""Record a scan event on an asset tag. Returns asset detail + open tasks."""
	guard.require_scope(user)
	asset_name = str(asset_name or "").strip()
	if not asset_name:
		frappe.throw("asset_name is required.", frappe.ValidationError)

	result = asset_tags.scan_asset({
		"asset_name": asset_name,
		"scanned_by": user,
		"gps_lat": gps_lat,
		"gps_lon": gps_lon,
	})
	return result.data


# ── 13. get_asset_detail ──────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("get_asset_detail", limit=guard.READ_LIMIT)
def get_asset_detail(user: str, asset_name=None) -> dict:
	"""Asset detail screen data: current state, open tasks, history."""
	guard.require_scope(user)
	asset_name = str(asset_name or "").strip()
	if not asset_name:
		frappe.throw("asset_name is required.", frappe.ValidationError)

	result = asset_tags.get_asset_detail({"asset_name": asset_name})
	return result.data


# ── 14. log_asset_state_change ────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("log_asset_state_change", mutating=True, limit=guard.WRITE_LIMIT)
def log_asset_state_change(
	user: str,
	asset_name=None,
	action=None,
	notes=None,
	photo_file_token=None,
	gps_lat=None,
	gps_lon=None,
) -> dict:
	"""Record a state-change action on an asset. Validates the transition."""
	guard.require_scope(user)
	asset_name = str(asset_name or "").strip()
	if not asset_name:
		frappe.throw("asset_name is required.", frappe.ValidationError)
	action_str = str(action or "").strip()
	if not action_str:
		frappe.throw("action is required.", frappe.ValidationError)

	result = asset_tags.log_asset_state_change({
		"asset_name": asset_name,
		"action": action_str,
		"performed_by": user,
		"notes": str(notes or "").strip() or None,
		"photo_file_token": str(photo_file_token or "").strip() or None,
		"gps_lat": gps_lat,
		"gps_lon": gps_lon,
	})
	return result.data


# ── 15. get_available_actions ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("get_available_actions", limit=guard.READ_LIMIT)
def get_available_actions(user: str, asset_name=None) -> dict:
	"""What state-change actions can be performed on this asset right now."""
	guard.require_scope(user)
	asset_name = str(asset_name or "").strip()
	if not asset_name:
		frappe.throw("asset_name is required.", frappe.ValidationError)

	result = asset_tags.get_available_actions({"asset_name": asset_name})
	return result.data


# ── 16. report_asset_issue ──────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("report_asset_issue", mutating=True, limit=guard.WRITE_LIMIT)
def report_asset_issue(
	user: str,
	asset_name=None,
	description=None,
	urgency=None,
	photo_file_token=None,
	task_type=None,
	skill_required=None,
	gps_lat=None,
	gps_lon=None,
) -> dict:
	"""Report a problem on a specific asset. Convenience wrapper that auto-fills
	location and skill from the asset, then creates a Farm Task."""
	allowed = guard.require_scope(user)
	employee = _employee(user)

	asset_name = str(asset_name or "").strip()
	if not asset_name:
		frappe.throw("asset_name is required.", frappe.ValidationError)

	inner = {
		"asset_name": asset_name,
		"reported_by": employee,
	}
	if description:
		inner["description"] = str(description).strip()
	if urgency:
		inner["urgency"] = str(urgency).strip()
	if photo_file_token:
		inner["photo_file_token"] = str(photo_file_token).strip()
	if task_type:
		inner["task_type"] = str(task_type).strip()
	if skill_required:
		inner["skill_required"] = str(skill_required).strip()
	if gps_lat is not None:
		inner["gps_lat"] = gps_lat
	if gps_lon is not None:
		inner["gps_lon"] = gps_lon

	company = guard.require_company(user, None, allowed) if allowed else ""
	if not company and allowed:
		inner["company"] = allowed[0]
	elif company:
		inner["company"] = company

	result = asset_tags.report_asset_issue(inner)
	data = result.data
	return shape.task(data, None)


# ── 17. create_i9_form ──────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("create_i9_form", mutating=True, limit=guard.WRITE_LIMIT)
def create_i9_form(user: str, employee=None, company=None, hire_date=None) -> dict:
	"""Open a Draft I-9 for somebody who has just been hired.

	The first of the five onboarding methods, and the only one that creates the
	record the other four fill in. `OnboardingAPI.createI9Form` sends today as the
	hire date because the app's flow runs on the person's first morning; it is NOT
	defaulted here when absent, because the three-business-day clock Section 2 is
	checked against counts from it and a guessed hire date would silently move a
	statutory deadline.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee, allowed)
	result = i9.create_i9_form({
		"employee": person,
		"company": _company(user, company, allowed),
		"hire_date": hire_date,
	})
	return result.data


# ── 18. submit_i9_section_1 ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("submit_i9_section_1", mutating=True, limit=guard.WRITE_LIMIT)
def submit_i9_section_1(
	user: str,
	employee=None,
	citizenship_status=None,
	ssn_last_four=None,
	address_street=None,
	address_city=None,
	address_state=None,
	address_zip=None,
	alien_registration_number=None,
	work_authorization_expiry=None,
	legal_first_name=None,
	legal_last_name=None,
	legal_middle_name=None,
	date_of_birth=None,
	email=None,
	phone=None,
	section_1_signature=None,
) -> dict:
	"""The employee's own half of the I-9: who they are and how they may work.

	THE LEGAL NAMES FALL BACK TO THE EMPLOYEE RECORD, which is what makes this
	callable from the shipped app at all. `submit_i9_section_1` requires
	`legal_first_name` and `legal_last_name`; `OnboardingI9Section1.apiParams`
	sends neither, because step 1 of the app's own flow already created the
	Employee with them and asking a person to type their name twice on a phone in
	a packing shed is how a form gets abandoned. Sent explicitly they win — a
	legal name and a payroll name genuinely differ for some people, and the I-9
	wants the legal one.

	`work_authorization_expiry` IS RENAMED, not forwarded. The doctype's column is
	`alien_work_authorization_expiry` and the app's key is the shorter one; a
	rename here is one line, and the alternative is a build shipped to every phone
	in the valley. The tool reads it only for "Alien Authorized to Work", so a
	value sent alongside any other citizenship status is dropped there rather than
	here — that is the tool's rule about its own field.

	`preparer_used` and the three preparer fields ARE NOT ACCEPTED. A preparer or
	translator signs their own attestation on paper, and a phone that could set
	`preparer_used` without carrying that signature would record an attestation
	nobody made. An operator files those in the Desk.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee, allowed)
	row = (
		frappe.db.get_value(EMPLOYEE, person, ["first_name", "middle_name", "last_name"], as_dict=True) or {}
	)

	inner = {
		"employee": person,
		"citizenship_status": citizenship_status,
		"legal_first_name": legal_first_name or row.get("first_name"),
		"legal_last_name": legal_last_name or row.get("last_name"),
		"legal_middle_name": legal_middle_name or row.get("middle_name"),
		"alien_work_authorization_expiry": work_authorization_expiry,
	}
	for key, value in (
		("ssn_last_four", ssn_last_four),
		("address_street", address_street),
		("address_city", address_city),
		("address_state", address_state),
		("address_zip", address_zip),
		("alien_registration_number", alien_registration_number),
		("date_of_birth", date_of_birth),
		("email", email),
		("phone", phone),
		("section_1_signature", section_1_signature),
	):
		if value is not None:
			inner[key] = value

	result = i9.submit_i9_section_1(inner)
	return result.data


# ── 19. submit_i9_section_2 ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("submit_i9_section_2", mutating=True, limit=guard.WRITE_LIMIT)
def submit_i9_section_2(
	user: str,
	employee=None,
	document_path=None,
	verifier_name=None,
	verifier_title=None,
	verification_date=None,
	list_a_doc_type=None,
	list_a_doc_number=None,
	list_a_authority=None,
	list_a_expiry=None,
	list_b_doc_type=None,
	list_b_doc_number=None,
	list_b_authority=None,
	list_b_expiry=None,
	list_c_doc_type=None,
	list_c_doc_number=None,
	list_c_authority=None,
	list_c_expiry=None,
	document_copies_stored=None,
	section_2_signature=None,
) -> dict:
	"""The employer's half: what documents were examined, by whom, on what day.

	THREE KEYS PER DOCUMENT ARE RENAMED. `OnboardingI9Section2.apiParams` sends
	`list_a_doc_type`, `list_a_authority` and `list_a_expiry`; the doctype's
	columns are `list_a_doc_title`, `list_a_doc_authority` and `list_a_doc_expiry`.
	Same for B and C. Renaming here rather than in Swift is the same trade
	`api/shape.py` makes and states: the backend moves, because the alternative is
	a new build on every phone.

	BOTH DOCUMENT PATHS ARE FORWARDED WHOLE and the tool picks. It requires
	`list_a_doc_title` on the List A path and both `list_b_doc_title` and
	`list_c_doc_title` on the other, refuses a `document_path` that is neither,
	and refuses a verification more than three business days after the hire date —
	all of which is 8 U.S.C. §1324a's rule rather than this transport's, so none
	of it is restated here.

	`verifier_name` IS THE TYPED ONE, not the caller's. The person examining the
	documents signs their own name to the attestation, and the account that made
	the HTTP call is recorded regardless — every mobile call writes an MCP Action
	Log row naming it.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee, allowed)

	inner = {
		"employee": person,
		"document_path": document_path,
		"verifier_name": verifier_name,
		"verifier_title": verifier_title,
		"verification_date": verification_date,
	}
	for key, value in (
		("list_a_doc_title", list_a_doc_type),
		("list_a_doc_number", list_a_doc_number),
		("list_a_doc_authority", list_a_authority),
		("list_a_doc_expiry", list_a_expiry),
		("list_b_doc_title", list_b_doc_type),
		("list_b_doc_number", list_b_doc_number),
		("list_b_doc_authority", list_b_authority),
		("list_b_doc_expiry", list_b_expiry),
		("list_c_doc_title", list_c_doc_type),
		("list_c_doc_number", list_c_doc_number),
		("list_c_doc_authority", list_c_authority),
		("list_c_doc_expiry", list_c_expiry),
		("document_copies_stored", document_copies_stored),
		("section_2_signature", section_2_signature),
	):
		if value is not None:
			inner[key] = value

	result = i9.submit_i9_section_2(inner)
	return result.data


# ── 20. submit_w4 ───────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("submit_w4", mutating=True, limit=guard.WRITE_LIMIT)
def submit_w4(
	user: str,
	employee=None,
	company=None,
	tax_year=None,
	filing_status=None,
	multiple_jobs=None,
	dependents_under_17=None,
	other_dependents=None,
	other_income=None,
	deductions=None,
	extra_withholding=None,
	additional_income_from_other_jobs=None,
) -> dict:
	"""Federal withholding, as the worker filled it in. Supersedes their last one.

	THREE MORE RENAMES, and they run the same way as Section 2's.
	`OnboardingW4.apiParams` sends `dependents_under_17`, `other_dependents` and
	`extra_withholding`; the doctype counts and periods, so its columns are
	`dependents_under_17_count`, `other_dependents_count` and
	`extra_withholding_per_period`.

	`status` IS NOT ACCEPTED AND NEITHER IS `effective_date`. `submit_w4` always
	writes an Active W-4 dated today and marks the previous one Superseded with a
	pointer to its replacement — that chain is what answers "which W-4 was in
	force on the day this cheque was cut", and a phone that could set either field
	could break it.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee, allowed)

	inner = {
		"employee": person,
		"company": _company(user, company, allowed),
		"tax_year": tax_year,
		"filing_status": filing_status,
	}
	for key, value in (
		("multiple_jobs", multiple_jobs),
		("dependents_under_17_count", dependents_under_17),
		("other_dependents_count", other_dependents),
		("other_income", other_income),
		("deductions", deductions),
		("extra_withholding_per_period", extra_withholding),
		("additional_income_from_other_jobs", additional_income_from_other_jobs),
	):
		if value is not None:
			inner[key] = value

	result = w4.submit_w4(inner)
	return result.data


# ── 21. link_badge_to_employee ──────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("link_badge_to_employee", mutating=True, limit=guard.WRITE_LIMIT)
def link_badge_to_employee(user: str, badge_id=None, employee=None, company=None, notes=None) -> dict:
	"""Point a scanned QR badge at the person holding it. The last onboarding step.

	`active` IS NOT ACCEPTED, which is the one thing this wrapper takes away.
	`link_badge_to_employee` uses it to DEACTIVATE a mapping, and a deactivated
	badge stops resolving on every bucket that scans it from that moment on —
	which is a decision about somebody's piece-rate pay, made in the Desk by
	somebody who can see the register. The wrapper always maps a badge live, which
	is the only thing the onboarding flow means by scanning one.

	Repointing a badge already mapped to somebody else IS allowed, because a lost
	card reissued to the next picker is the ordinary case rather than an attack —
	the tool records the previous holder on the row it returns, and the audit row
	names the account that did it.

	The backfill is the tool's and it matters here: a badge mapped after a morning
	of picking claims the buckets already synced against it that had nobody
	attached. A badge is scanned before the map exists more often than after.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee, allowed)
	badge = str(badge_id or "").strip()
	if not badge:
		frappe.throw("badge_id is required.", frappe.ValidationError)

	inner = {
		"badge_id": badge,
		"employee": person,
		"company": _company(user, company, allowed),
		"active": True,
	}
	if notes:
		inner["notes"] = str(notes).strip()

	result = bucket_log.link_badge_to_employee(inner)
	return result.data


# ── 22. sync_bucket_entries ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("sync_bucket_entries", mutating=True, limit=guard.UPLOAD_LIMIT)
def sync_bucket_entries(user: str, entries=None, company=None) -> dict:
	"""A handset's capture queue, filed as Bucket Log Entry rows.

	`UPLOAD_LIMIT` RATHER THAN `WRITE_LIMIT`, and for the reason that limit exists.
	A picker works through a morning with no signal and the queue drains in a
	burst when the phone finds the yard's wifi; ten calls a minute would refuse
	most of it, and a refused sync is a morning of somebody's piece-rate sitting
	on a device that might not come back. The batch cap and the tool's own
	deduplication are what bound this instead — resending a batch the site already
	has creates nothing, so a client that retries because it never saw the answer
	is a no-op rather than a double payment.

	ONE COMPANY FOR THE WHOLE BATCH, checked once — see `_bucket_entries`, which
	also says why an entry may not name its own picker.
	"""
	allowed = guard.require_scope(user)
	wanted = _company(user, company, allowed)
	result = bucket_log.sync_bucket_entries({
		"entries": _bucket_entries(entries, wanted),
	})
	return result.data


# ── 23. start_shift ─────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("start_shift", mutating=True, limit=guard.WRITE_LIMIT)
def start_shift(
	user: str,
	company=None,
	location=None,
	farm_location_gps=None,
	shift_type=None,
	start_datetime=None,
	crew_employees=None,
	latitude=None,
	longitude=None,
) -> dict:
	"""Open a shift: a crew, a place, and the exposure period compliance is read against.

	`foreman` IS NOT ACCEPTED AND IS FILLED FROM THE CALLER. This is the strongest
	version of the rule `report_field_task` and `list_my_tasks` already follow, and
	here it is more than scoping hygiene: OAR 437-004-1131 puts the water, shade
	and rest obligations on a NAMED responsible person and FSMA §112.161(b) asks
	that person to sign the close. The phone in the hand at the start of the shift
	is that person. A body key naming somebody else would put another human's name
	against obligations they did not know they had.

	`farm_location_gps` TAKES A TYPED PLACE OVER A FIX, exactly as a completion
	does — `_location` is shared rather than re-argued. It matters more here: a
	shift with no coordinates gets no weather timeline at all, and a heat-illness
	defence built on a point-in-time temperature is not a defence.

	The crew may be empty and that is not an error. A foreman opening a shift
	before the pickers arrive is the ordinary case; `add_worker_to_shift` rosters
	them as they turn up, and the tool's own answer says so.
	"""
	allowed = guard.require_scope(user)
	foreman = _employee(user)

	inner = {
		"foreman": foreman,
		"company": _company(user, company, allowed),
		"crew_employees": _crew(crew_employees, allowed),
	}
	for key, value in (
		("location", location),
		("shift_type", shift_type),
		("start_datetime", start_datetime),
	):
		if value is not None:
			inner[key] = value
	gps = _location(farm_location_gps, latitude, longitude)
	if gps:
		inner["farm_location_gps"] = gps

	result = shifts.start_shift(inner)
	return result.data


# ── 24. add_worker_to_shift ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("add_worker_to_shift", mutating=True, limit=guard.WRITE_LIMIT)
def add_worker_to_shift(
	user: str, shift=None, employee=None, role=None, joined_at=None, notes=None
) -> dict:
	"""Roster a late arrival onto a shift that is already running.

	`joined_at` DEFAULTS TO NOW IN THE TOOL and is forwarded when sent, because a
	phone that queued the clock-in offline knows a truer time than the moment its
	sync landed. It is the start of that person's own Attendance span when the
	shift closes, so a value half an hour late is half an hour of somebody's day.

	A shift that is already closed, a second row for somebody already on the crew,
	and a worker employed by another entity are all refused by the tool with their
	own sentences — the second of those is the one that would otherwise become two
	Attendance days for one person.
	"""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(FARM_SHIFT, shift, "shift", allowed)
	person = _employee_argument(employee, allowed)

	inner = {"shift": name, "employee": person}
	for key, value in (("role", role), ("joined_at", joined_at), ("notes", notes)):
		if value is not None:
			inner[key] = value

	result = shifts.add_worker_to_shift(inner)
	return result.data


# ── 25. end_shift ───────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("end_shift", mutating=True, limit=guard.WRITE_LIMIT)
def end_shift(
	user: str,
	shift=None,
	end_datetime=None,
	supervisor_signature_file_token=None,
	reviewed_on=None,
	foreman_notes=None,
) -> dict:
	"""Close a shift with the supervisor's signature, and write the crew's payroll rows.

	THE SIGNATURE IS A FILE TOKEN AND THE TOOL WILL NOT CLOSE WITHOUT ONE. It is
	the docname `finalize_staged_file` handed back after the phone uploaded the
	drawn signature in chunks — the same token `complete_task_via_mobile` carries
	for its evidence, resolved the same way. An unsigned close is an UPDATE
	setting a timestamp; §112.161(b) asks for a review that is dated AND signed.

	The close is what writes one Attendance record per crew member, each spanning
	that person's own joined_at to their own left_at. It happens once: the tool
	refuses a shift that is already closed rather than writing a second set.
	"""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(FARM_SHIFT, shift, "shift", allowed)

	inner = {"shift": name}
	for key, value in (
		("end_datetime", end_datetime),
		("supervisor_signature_file_token", supervisor_signature_file_token),
		("reviewed_on", reviewed_on),
		("foreman_notes", foreman_notes),
	):
		if value is not None:
			inner[key] = value

	result = shifts.end_shift(inner)
	return result.data
