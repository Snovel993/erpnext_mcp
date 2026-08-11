# SPDX-License-Identifier: MIT
"""The fifty-one methods the Farm Ops app calls, as whitelisted Frappe endpoints.

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

v0.45.0 published nine more, v0.46.0 three more again and v0.46.2 a thirteenth,
and they are not like the first fifteen. Every tool the original wrappers
delegate to is field work with no role check of its own; these thirteen reach
`tools/employee.py`, `tools/i9.py`, `tools/w4.py`, `tools/shifts.py` and
`tools/bucket_log.py`, and each of THOSE calls `employee.require_hr_role()` or
`kpi.require_kpi_role()` before it writes a row.

`get_employee` is the one with an EXCEPTION in its gate rather than a copy of it,
and the exception is a single record: a worker reading their OWN Employee row —
their hire date, their I-9 status, the badge in their pocket — is not reading the
personnel register, and the wizard's returning-worker path is the reason to say
so. Everybody else's record still wants the HR role. That is written out in the
wrapper's own docstring, because a gate with a hole in it is the gate somebody
has to be able to find.

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

import base64

import frappe

from .. import bucket_bridge, compat
from ..errors import ToolError
from ..tools import asset_tags, badges, bucket_log, dispatch, fieldwork, i9, shifts, signatures, signers, w4
from ..tools import files as file_tools
from ..tools import calendar as compliance_calendar
from ..tools import employee as personnel
from ..tools import housing as housing_tools
from ..tools import ml_model as ml_model_tools
from ..tools import mobile as mobile_tools
from ..tools import wallet as wallet_tools
from . import guard, shape

ALERT = "Compliance Alert"
FARM_TASK = "Farm Task"
FARM_TASK_ASSIGNMENT = "Farm Task Assignment"
EMPLOYEE = "Employee"
FARM_SHIFT = "Farm Shift"
ML_MODEL = "ML Model"
HOUSING_UNIT = "Housing Unit"
HOUSING_ASSIGNMENT = "Housing Assignment"

#: The four HR masters the wizard's Assignment step offers as dropdowns, mapped
#: to the field on each that carries a human label. `Branch` has no second
#: column at all on a stock Frappe HR — the docname IS the branch name — which
#: is why the value here may be empty and `label` falls back to the docname.
REFERENCE_MASTERS = (
	("branches", "Branch", "branch"),
	("departments", "Department", "department_name"),
	("designations", "Designation", "designation_name"),
	("employment_types", "Employment Type", "employee_type_name"),
)

#: Most rows any one reference list hands back. A dropdown longer than this is a
#: dropdown nobody scrolls, and every one of these masters is a hand-maintained
#: table on a real site — a farm with two hundred designations has a data problem
#: rather than a paging problem.
REFERENCE_LIMIT = 200

#: Most Housing Units `list_available_housing` reads. `tools/housing.REGISTER_CAP`
#: is the register's own ceiling and is read rather than restated so a phone and
#: a Desk report cannot come to disagree about how big a camp is.
HOUSING_LIST_LIMIT = housing_tools.REGISTER_CAP

#: Most bucket captures one sync call carries. `tools/bucket_log.BATCH_CAP` is
#: the tool's own limit and is read rather than restated, so a phone and a Desk
#: import cannot come to disagree about how big a batch is.
BUCKET_BATCH_CAP = bucket_log.BATCH_CAP

#: Most rows `search_employees` will hand back. The number iOS asks Frappe's REST
#: list endpoint for in `OnboardingAPI.searchEmployees`, kept exactly: the search
#: field on the wizard's Identity step shows a scrolling list a person picks one
#: name out of, and a longer answer is a bigger payload nobody reads to the end.
#: A search that hits the cap is a search that needs another letter typed into it.
EMPLOYEE_SEARCH_LIMIT = 20

#: What `search_employees` reports about each match, in the order
#: `ExistingEmployee` (`Models/OnboardingModels.swift:273`) declares them.
#: `company` is not in that struct and is emitted anyway — `guard.scoped` checks
#: the result against the caller's entities on the way out, and it needs the
#: column to check.
EMPLOYEE_SEARCH_FIELDS = (
	"name",
	"employee_name",
	"employee_number",
	"status",
	"date_of_joining",
	"employment_type",
	"company",
)


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


def _full_name(first, last, given) -> str:
	"""One `employee_name`, from whichever halves the wizard filled in.

	`OnboardingIdentity.employeePayload` already joins the two and sends all three
	keys, so this is the fallback for a client that sends only the halves rather
	than the usual path. The one-word case is NOT patched over here: a record
	carrying "Rosa" and nothing else names nobody findable on an I-9, a payroll
	register or a dispatch board, and `create_employee` refuses it with that
	sentence. Composing something plausible instead would put the refusal off
	until the person had filled in four more screens.
	"""
	whole = str(given or "").strip()
	if whole:
		return whole
	return " ".join(part for part in (str(first or "").strip(), str(last or "").strip()) if part)


def _employee_identity(name: str) -> dict:
	"""The two facts the wizard holds on to after step 1, read back off the record.

	`OnboardingAPI.CreatedEmployee` decodes `name` with `try c.decode(String.self)`
	— absent or null and the whole row throws, mid-flow, on a person who has just
	been hired — and `employee_id` as an optional it falls back to the docname for.

	`employee_id` IS `employee_number` AND IS OFTEN EMPTY. Frappe HR's Employee
	carries the docname as its identity and `employee_number` as the payroll number
	an operator may or may not keep; a site that keeps none gets null here rather
	than a docname echoed into a second key, because the app already writes that
	fallback itself and a server that guessed would hide which sites actually
	number their people.
	"""
	row = (
		frappe.db.get_value(
			EMPLOYEE,
			name,
			compat.existing_fields(EMPLOYEE, ["employee_name", "employee_number", "company", "status"]),
			as_dict=True,
		)
		or {}
	)
	return {
		"name": name,
		"employee_id": str(row.get("employee_number") or "") or None,
		"employee_name": row.get("employee_name"),
		"company": row.get("company"),
		"status": row.get("status"),
	}


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


def _model_docname(model) -> str:
	"""`model` resolved to an ML Model docname before `guard.require_scoped_doc`
	checks it, so a phone naming the `uuid` off its own cached manifest — which
	is `source_uuid`, not a docname, see `model_registry.build_model_manifest`
	— still resolves to something the scoping check can run against.

	A value that resolves to nothing is returned unchanged: `require_scoped_doc`
	then does its own `frappe.db.exists` and answers the same 404 it would for
	any other docname nobody has heard of.
	"""
	value = str(model or "").strip()
	if not value or frappe.db.exists(ML_MODEL, value):
		return value
	return str(frappe.db.get_value(ML_MODEL, {"source_uuid": value}, "name") or value)


def _previous_assignment(employee: str, allowed: list) -> dict | None:
	"""Where this person slept last season, and whether that cabin is free now.

	v0.54.0, for the wizard's "Last year: MC-Cabin-07" row. A returning picker
	who had the same cabin for three seasons is one tap instead of a scroll
	through forty units nobody remembers the numbers of, and the foreman does not
	have to ask somebody where they slept last August.

	ENDED ASSIGNMENTS ONLY, MOST RECENT FIRST. A returning worker is by definition
	somebody whose last stay finished. An open assignment means they are housed
	right now, which is a different screen and a different sentence — offering
	"last year: Cabin 7" to somebody who is currently IN Cabin 7 is an offer to
	double-book them — so it is reported as `currently_housed` and no preference
	is returned.

	AVAILABILITY IS COMPUTED FOR THE UNIT ITSELF rather than read off the list
	this is returned beside. That list is filtered — by branch, and by the default
	that drops full and condemned units — so a cabin missing from it is precisely
	the case this has to answer for, and looking it up there would report every
	full cabin as available.

	A UNIT OR AN ASSIGNMENT BELONGING TO AN ENTITY THE CALLER CANNOT REACH IS NOT
	REPORTED AT ALL. `guard.scoped` cannot do it — a Housing Unit calls its
	company `owning_entity` — so the check is here, and it is the same rule
	`assign_housing` applies by hand for the same reason.
	"""
	if not compat.doctype_exists(HOUSING_ASSIGNMENT):
		return None

	# AN OPEN ASSIGNMENT IS CHECKED FIRST, AND IT WINS OVER ANY ENDED ONE. Not
	# merely a fallback for somebody with no history: a worker who had Cabin 7
	# last season and is in Cabin 3 tonight has both, and answering with Cabin 7
	# would offer a one-tap re-assignment for somebody who already has a bed. What
	# they need is "they are already housed", which is true regardless of how many
	# finished seasons sit behind it.
	housed = frappe.db.get_all(
		HOUSING_ASSIGNMENT,
		filters={"employee": employee, "end_date": ("is", "not set")},
		fields=["name", "unit", "assigned_date"],
		order_by="assigned_date desc",
		limit_page_length=1,
	)
	if housed:
		current = dict(housed[0])
		if not _unit_is_reachable(str(current.get("unit") or ""), allowed):
			return None
		return {
			"assignment": current.get("name"),
			"unit": current.get("unit"),
			"unit_name": _unit_label(str(current.get("unit") or "")),
			"check_in_date": str(current.get("assigned_date") or "") or None,
			"check_out_date": None,
			"currently_housed": True,
			"available": False,
			"unavailable_reason": (
				"This is where they are housed now, not where they were. Ending that "
				"assignment is end_housing_assignment; nothing here re-assigns anybody."
			),
		}

	rows = frappe.db.get_all(
		HOUSING_ASSIGNMENT,
		filters={"employee": employee, "end_date": ("is", "set")},
		fields=["name", "unit", "assigned_date", "end_date", "housing_deduction_from_wages"],
		order_by="end_date desc, assigned_date desc",
		limit_page_length=1,
	)
	if not rows:
		# A first-season hire. Not an error and not a warning — just nothing to
		# put at the top of the list.
		return None

	row = dict(rows[0])
	unit = str(row.get("unit") or "")
	if not _unit_is_reachable(unit, allowed):
		return None

	capacity, occupants, condition = _unit_occupancy(unit)
	condemned = condition == "Uninhabitable"
	full = bool(capacity) and occupants >= capacity
	reason = None
	if condemned:
		reason = "Marked Uninhabitable since they left. It has to be repaired and inspected first."
	elif full:
		reason = f"All {capacity} bed(s) are taken."

	return {
		"assignment": row.get("name"),
		"unit": unit,
		"unit_name": _unit_label(unit),
		"check_in_date": str(row.get("assigned_date") or "") or None,
		"check_out_date": str(row.get("end_date") or "") or None,
		"currently_housed": False,
		"capacity": capacity or None,
		"current_occupants": occupants,
		"open_beds": max(0, capacity - occupants) if capacity else None,
		"available": not (condemned or full),
		"unavailable_reason": reason,
		"housing_deduction_from_wages": row.get("housing_deduction_from_wages") or "Unknown",
	}


def _unit_is_reachable(unit: str, allowed: list) -> bool:
	"""Does this Housing Unit belong to an entity this caller may see?

	A blank owning entity is reachable, matching `guard.scoped`'s rule: a record
	with no company is a data problem rather than another entity's secret, and
	hiding it makes it invisible instead of fixed.
	"""
	if not unit or not frappe.db.exists(HOUSING_UNIT, unit):
		return False
	owner = str(frappe.db.get_value(HOUSING_UNIT, unit, "owning_entity") or "")
	return not owner or owner in set(allowed or [])


def _unit_label(unit: str) -> str | None:
	"""What is painted on the door, rather than the docname with the parcel key on it."""
	if not unit:
		return None
	return str(frappe.db.get_value(HOUSING_UNIT, unit, "unit_name") or "") or unit


def _unit_occupancy(unit: str) -> tuple:
	"""`(capacity, occupants_today, condition)` for one unit.

	Occupancy is counted through `housing.occupancy_for`, the same overlap rule
	`assign_housing` refuses on, so "available" here and "accepted" there cannot
	come to different answers about the same cabin on the same day.
	"""
	row = frappe.db.get_value(HOUSING_UNIT, unit, ["capacity", "condition"], as_dict=True) or {}
	today = frappe.utils.today()
	occupants = housing_tools.occupancy_for(unit, today, today)
	return int(row.get("capacity") or 0), len(occupants), str(row.get("condition") or "")


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
	tasks. Its shape IS checked, one layer down where the column is written: a
	UUID as 8-4-4-4-12, either case, or the call is refused with the format in
	the message. Omitting it is not an error — it files the completion outside
	any visit, which `list_visits` counts separately and says so.
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


# ── 11a. dismiss_compliance_alert ───────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("dismiss_compliance_alert", limit=guard.WRITE_LIMIT, mutating=True)
def dismiss_compliance_alert(user: str, alert=None, reason=None) -> dict:
	"""Close one alert the SERVER marked closable, with a reason. v0.57.0.

	`API_CONTRACT.md` §8.2, and the gate is the whole of it: this refuses any
	alert whose `can_dismiss` is not set, which is every alert until somebody
	says otherwise about that one.

	THE APP'S CHECK IS UI COURTESY AND THIS IS THE BOUNDARY, which the app's own
	contract asks for in as many words. The Dismiss button appears only where
	`list_compliance_alerts` sent `can_dismiss: true`; the refusal below is what
	happens when something posts anyway.

	WHY A PHONE MAY NOT SIMPLY DISMISS. An overdue housing inspection is not a
	notification. Waving one off from a handset leaves a cabin uninspected and
	the compliance calendar quiet about it, which is why the mobile surface
	shipped with no dismiss at all — and why the alerts that genuinely are stale
	are marked one at a time, by somebody who can see the whole picture, rather
	than by whoever is holding the phone.

	THE REASON IS NOT DECORATION. It is the entire audit trail for an obligation
	nobody discharged. Empty is refused here as well as on the handset, exactly
	as `reject_task`'s is, and `tools/calendar.py` refuses a word where a
	sentence belongs.

	NOTHING ABOUT THE UNDERLYING RECORD CHANGES. The certificate is still
	expired, the cabin still uninspected. What is recorded is that somebody with
	a phone in an orchard decided it did not need doing, and who they were.
	"""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(ALERT, alert, "alert", allowed)
	if not str(reason or "").strip():
		frappe.throw(
			"A reason is required to dismiss a compliance alert. It is the only part of this "
			"record nobody can reconstruct: the alert itself the nightly sweep can rebuild from "
			"the source record, but why somebody decided an obligation did not need meeting "
			"exists nowhere else.",
			frappe.ValidationError,
		)

	result = compliance_calendar.dismiss_compliance_alert({"alert": name, "reason": str(reason).strip()})
	data = result.data
	return {
		"alert": data.get("name"),
		"dismissed": bool(data.get("dismissed")),
		"dismissed_by": data.get("dismissed_by"),
		"dismissed_on": data.get("dismissed_on"),
		"reason": data.get("dismissed_reason"),
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


# ── 17. create_employee ─────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("create_employee", mutating=True, limit=guard.WRITE_LIMIT)
def create_employee(
	user: str,
	first_name=None,
	middle_name=None,
	last_name=None,
	employee_name=None,
	company=None,
	gender=None,
	date_of_birth=None,
	date_of_joining=None,
	employment_type=None,
	designation=None,
	department=None,
	branch=None,
	personal_email=None,
	cell_number=None,
	i9_status=None,
	w4_status=None,
	jurisdiction=None,
) -> dict:
	"""The person. Step 1 of the wizard, and the record the other four steps fill in.

	v0.46.0, and the same failure v0.45.0 fixed nine of: `OnboardingAPI` reached
	for `POST /api/resource/Employee`, the Tailscale funnel publishes
	`/farmops/api/…` and nothing else, and the wizard 404'd on its FIRST step —
	so none of the nine paths that release published was ever reached from a
	phone. `MobileAPI.createEmployee` has named this path since Sprint 9.

	IT DELEGATES RATHER THAN INSERTING. `frappe.get_doc({...}).insert()` here would
	be four lines and would step around every rule `tools/employee.py` has held
	since v0.18.1: the nineteen-field allowlist that refuses `ctc` and
	`salary_structure` by name, the second-record check that keeps one person off
	the dispatch board twice, the mandatory fields read off THIS site's meta rather
	than assumed, and `require_hr_role`. Those rules stay where they are for the
	reason the dispatch rules do — a wrapper with its own copy is a second set of
	personnel rules to keep in step, and they drift.

	`status` IS NOT ACCEPTED, and the app sends it. `OnboardingIdentity.employeePayload`
	carries `"status": "Active"`, which is what `create_employee` writes anyway;
	what the argument would ALSO buy is a phone that can file somebody as Left or
	Suspended on the day they were hired. It is dropped rather than forwarded, and
	the record comes out Active because that is the tool's default.

	`user_id` IS NOT ACCEPTED EITHER. Linking an Employee to a login is what turns
	a login into a person on the dispatch board, and `link_employee_to_user` does
	it in the Desk behind a check that the account is actually enrolled. A phone
	that could set it in passing could point somebody else's task history at an
	account it names.

	THE THREE COMPLIANCE STATUSES ARE FORWARDED, NOT DEFAULTED HERE. v0.46.1: the
	wizard reached this path and got "this site's Frappe HR marks i9_status,
	w4_status, jurisdiction mandatory on Employee, and the call did not supply
	them" — which was not Frappe HR's doing at all. `compliance_fields.py` installs
	those three as Custom Fields with `reqd=True`, so the wall was erpnext_mcp's
	own, and it stood in front of `onboard_employee` and the MCP tool exactly as
	much as it stood in front of the phone.

	The obvious fix was three lines HERE — Pending, Missing, OR — and it would have
	been the wrong file. `tools/employee.py` owns the fourteen-field allowlist and
	the mandatory check, so a wrapper cannot pass a field the allowlist does not
	carry, and a wrapper that could would be a second set of hiring defaults to
	keep in step with `onboard_employee`'s. The defaults live in the tool, next to
	the check they answer; all three are on `WRITABLE` now, so what this wrapper
	adds is the ABILITY TO OVERRIDE. The wizard sends none of them today and gets
	the tool's values back in `defaults_applied`; a later build that asks the
	foreman which state the crew is working can send `jurisdiction` and have it
	honoured without a server change.
	"""
	allowed = guard.require_scope(user)

	inner = {
		"employee_name": _full_name(first_name, last_name, employee_name),
		"company": _company(user, company, allowed),
	}
	for key, value in (
		("first_name", first_name),
		# v0.51.0. Read off the licence barcode at the tailgate and dropped
		# here until now — which also emptied the I-9's Legal Middle Name, since
		# `submit_i9_section_1` fills that from `Employee.middle_name` when the
		# caller sends none.
		("middle_name", middle_name),
		("last_name", last_name),
		("gender", gender),
		("date_of_birth", date_of_birth),
		("date_of_joining", date_of_joining),
		("employment_type", employment_type),
		("designation", designation),
		("department", department),
		# v0.54.0. The Assignment step's fourth dropdown, and the one that had
		# nowhere to land: `tools/employee.WRITABLE` did not carry `branch`, so a
		# wizard that asked which camp somebody was hired to could not record the
		# answer. `list_onboarding_reference_data` is where the four choices come
		# from, and `create_employee`'s Link check refuses one that names nothing.
		("branch", branch),
		("personal_email", personal_email),
		("cell_number", cell_number),
		("i9_status", i9_status),
		("w4_status", w4_status),
		("jurisdiction", jurisdiction),
	):
		if value not in (None, ""):
			inner[key] = value

	result = personnel.create_employee(inner)
	return _employee_identity(result.data["employee"])


# ── 18. search_employees ────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("search_employees", limit=guard.READ_LIMIT)
def search_employees(user: str, query=None, company=None) -> dict:
	"""Who this entity has already hired, by name. The step before creating a second record.

	The wizard runs this before its Identity step writes anything: a picker who
	worked last season is an Employee record that already exists, and hiring them
	again as a NEW record is the mistake `create_employee` refuses at the end and
	this method prevents at the beginning. A match the foreman recognises becomes
	`reactivate_employee`; no match becomes `create_employee`.

	IT CARRIES THE HR ROLE GATE ITSELF, which no other read on this surface does.
	The rest of the file reads field work — a task board, an asset, a compliance
	alert — and a picker holding a phone is entitled to all of it. This reads the
	personnel register: every name, hire date and employment type an entity has,
	including the people who have LEFT. `tools/hr.list_employees` has no gate of
	its own because the MCP transport is an operator's console; this transport
	faces the open internet with a phone on the other end, so the same gate the
	writing methods inherit from `tools/employee.py` is applied here by hand.

	STATUS IS NOT FILTERED, on purpose. A Left or Inactive employee is exactly who
	this search is for, and `hr.list_employees` defaults to Active — which is why
	this reads the register directly rather than calling it. `ExistingEmployee`
	carries `status` and the wizard branches on it: Active means "you already have
	them", anything else means "reactivate".

	The company filter is the caller's own entities when the app does not name one,
	never the whole site — the rule `list_available_tasks` states at length — and
	`guard.scoped` checks the answer again on the way out.
	"""
	allowed = guard.require_scope(user)
	personnel.require_hr_role()
	wanted = guard.require_company(user, company, allowed)

	text = str(query or "").strip()
	if not text:
		frappe.throw(
			"query is required — a search with nothing in it would return the entity's whole "
			"personnel register.",
			frappe.ValidationError,
		)

	rows = frappe.db.get_all(
		EMPLOYEE,
		filters={
			"company": ("in", [wanted] if wanted else allowed),
			"employee_name": ("like", f"%{text}%"),
		},
		fields=compat.existing_fields(EMPLOYEE, list(EMPLOYEE_SEARCH_FIELDS)),
		order_by="employee_name asc",
		limit_page_length=EMPLOYEE_SEARCH_LIMIT,
	)

	found = guard.scoped(
		[
			{
				"name": row.get("name"),
				"employee_name": row.get("employee_name"),
				"employee_id": str(row.get("employee_number") or "") or None,
				"status": row.get("status"),
				"date_of_joining": row.get("date_of_joining"),
				"employment_type": row.get("employment_type"),
				"company": row.get("company"),
			}
			for row in rows or []
		],
		allowed,
	)
	return {
		"employees": found,
		"count": len(found),
		"company": wanted or None,
		"limit": EMPLOYEE_SEARCH_LIMIT,
	}


# ── 19. get_employee ────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("get_employee", limit=guard.READ_LIMIT)
def get_employee(user: str, employee=None, docname=None) -> dict:
	"""One person's record and how far through onboarding they already are.

	v0.46.2, and the third of `OnboardingAPI`'s Identity-step calls to be reaching
	somewhere the funnel does not publish: `getEmployeeDetail` still asks
	`GET /api/resource/Employee/<name>` (`OnboardingAPI.swift:121`), which is the
	same 404 v0.46.0 fixed for the other two. It is the step BETWEEN them — the
	foreman picks a returning picker out of `search_employees`, and the wizard
	then has to decide which of its five steps that person still needs, because a
	worker who came back for their fourth season should not be walked through an
	I-9 and a W-4 they already have on file. In tree fruit that is the COMMON
	path, not the exception.

	WHAT IT ANSWERS WITH is `EmployeeDetail` (`OnboardingModels.swift:291`) field
	for field — `name` and `employee_name` strict, the rest optional — plus
	`jurisdiction`, which the struct does not carry yet and which the W-4 step
	needs the moment a crew works across a state line. `needsI9`, `needsW4` and
	`needsBadge` are computed on the handset from those fields and are NOT
	computed here: a server that also decided which step to skip would be a second
	copy of the wizard's own rule, and this file refuses that for the dispatch
	rules at length.

	`badge_id` IS A LOOKUP, NOT A COLUMN. `link_badge_to_employee` writes a Bucket
	Log Badge Map row rather than a field on Employee, and only an ACTIVE mapping
	counts — a badge handed back at the end of last season is exactly the one step
	5 has to issue again.

	`i9_status` AND `w4_status` ARE RECONCILED BEFORE THEY GO OUT, and that is the
	whole reason this method is worth writing rather than pointing the app at a
	column. Those two are Custom Fields this app installs; `create_employee` sets
	them to Pending/Missing and NOTHING MOVES THEM AFTERWARDS — `submit_i9_section_2`
	and `submit_w4` write `I-9 Form.status` and `W-4 Form.status` on their own
	doctypes. `EmployeeDetail.satisfiedSteps` (`OnboardingModels.swift:352`)
	branches on the COLUMN, so handing it over raw would take a returning picker
	whose I-9 was verified last June through a fresh I-9 and a fresh W-4. A live
	Complete/Active record therefore fills a column that is still at its hire-time
	default, and NOTHING ELSE: `Expired` and `Requires-Update` stand, because an
	expired I-9 is precisely the case that must be re-verified. `i9_status_recorded`,
	`w4_status_recorded`, `i9`, `w4` and `reconciled` carry the unreconciled truth
	beside it — see `tools/employee.employee_detail`, which owns the rule.

	IT IS THE ONE READ ON THIS SURFACE WHOSE GATE HAS AN EXCEPTION IN IT.
	`search_employees` applies `require_hr_role` flatly, and rightly: it hands back
	the entity's whole personnel register, which is not a picker's to browse. This
	names ONE record, and a worker asking for their own — their hire date, their
	I-9 status, the badge in their pocket — is not reading the register at all. So
	the HR role is required for ANYBODY ELSE'S record and not for the caller's own,
	which is the narrowest opening that makes the sentence "workers can check their
	own onboarding" true. `_employee` resolves the caller through `Employee.user_id`
	and nothing in the body, so the exception cannot be claimed by naming somebody.

	`docname` IS ACCEPTED AS A SECOND SPELLING of `employee`, for the reason
	`reactivate_employee` accepts it: the Swift function's own parameter is called
	`docname`, and two names for one docname is cheaper than shipping a build.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)

	if person != fieldwork._employee_for(user):
		personnel.require_hr_role()

	detail = personnel.employee_detail(person)
	if not guard.scoped([detail], allowed):  # pragma: no cover - require_scoped_doc got there first
		frappe.throw(f"employee {person} was not found.", frappe.DoesNotExistError)
	return detail


# ── 20. reactivate_employee ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("reactivate_employee", mutating=True, limit=guard.WRITE_LIMIT)
def reactivate_employee(user: str, employee=None, docname=None) -> dict:
	"""Put a returning picker back on the payroll: Active, joined today.

	The other half of `search_employees`. A worker who left in November and is
	standing in the yard in June is one Employee record with a status, not two
	records with one history between them — and the wizard's own flow takes this
	branch before it will consider creating anything.

	IT WRITES `date_of_joining` AND THAT OVERWRITES THE ORIGINAL HIRE DATE. This is
	what `OnboardingAPI.reactivateEmployee` already did through the REST path it
	could not reach, and it is deliberate rather than incidental: the I-9 opened a
	few screens later is checked against the hire date, and 8 U.S.C. §1324a's
	three-business-day clock counts from the day this person started THIS time. The
	date it replaced is not lost — `update_employee` reports every field it changed
	with its before-value, and that lands in the MCP Action Log row this call
	writes.

	TODAY IS NOT AN ARGUMENT. A rehire date is a wage fact — it decides which
	season's tenure a person is credited with — and the phone in the yard knows
	exactly one true answer to it, which is the day it is being held. A backdated
	rehire is a correction, and corrections are made in the Desk by somebody who
	can see what they are correcting.

	`docname` IS ACCEPTED AS A SECOND SPELLING of `employee`, because the Swift
	function's own parameter is called `docname` and the two names for one docname
	is the cheapest possible way to not ship a build over a key. Both are checked
	the same way — `_employee_argument` proves the record is inside this caller's
	entities, so an Employee of an entity this account cannot see reads as not
	found rather than as refused.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)

	result = personnel.update_employee({
		"employee": person,
		"status": "Active",
		"date_of_joining": frappe.utils.today(),
	})
	return {**_employee_identity(person), "changed": result.data.get("changed") or []}


# ── 21. create_i9_form ──────────────────────────────────────────────────────
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


# ── 22. submit_i9_section_1 ─────────────────────────────────────────────────
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
	i94_admission_number=None,
	foreign_passport_number=None,
	foreign_passport_country=None,
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

	THE OTHER TWO ALIEN IDENTIFIERS ARE FORWARDED UNRENAMED. v0.47.0 taught the
	tool that Section 1 takes an A-Number, an I-94 admission number, OR a foreign
	passport with the country that issued it — any one of the three answers the
	question — and this wrapper carried only the first, so a worker holding an
	I-94 and no A-Number could fill the form on a phone and be refused for a field
	the transport had dropped. `i94_admission_number`, `foreign_passport_number`
	and `foreign_passport_country` go through under the tool's own names because
	the doctype's columns and USCIS's wording agree, so there is nothing to
	rename. That a passport number without its country is refused, and that all
	three are read only for "Alien Authorized to Work", are the tool's rules.

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
		("i94_admission_number", i94_admission_number),
		("foreign_passport_number", foreign_passport_number),
		("foreign_passport_country", foreign_passport_country),
		("date_of_birth", date_of_birth),
		("email", email),
		("phone", phone),
		("section_1_signature", section_1_signature),
	):
		if value is not None:
			inner[key] = value

	result = i9.submit_i9_section_1(inner)
	return result.data


# ── 23. submit_i9_section_2 ─────────────────────────────────────────────────
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
	list_a_is_receipt=None,
	list_b_doc_type=None,
	list_b_doc_number=None,
	list_b_authority=None,
	list_b_expiry=None,
	list_b_is_receipt=None,
	list_c_doc_type=None,
	list_c_doc_number=None,
	list_c_authority=None,
	list_c_expiry=None,
	list_c_is_receipt=None,
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

	THE THREE RECEIPT FLAGS ARE NOT RENAMED and they are the reason this wrapper
	changed. v0.47.0 taught the tool 8 CFR 274a.2(b)(1)(vi) — a worker whose
	document was lost, stolen or damaged presents a receipt for the replacement
	and may lawfully work while it comes — and the transport dropped the flag, so
	every receipt examined on a phone was filed as though the document itself had
	been seen. That is a false attestation on a federal form, and the 90-day clock
	`receipt_expires_on` starts never started. `list_a_is_receipt`,
	`list_b_is_receipt` and `list_c_is_receipt` are booleans; unsent, the tool
	defaults each to false, which is the pre-v0.47.0 behaviour for a caller that
	has not grown the checkbox yet.

	A RECEIPT STILL COMPLETES THE FORM AND STILL NEEDS ITS TITLE. Neither is this
	transport's rule: the tool sets Complete because the person may work, and
	checks the title because a receipt is a receipt FOR a named document.

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
		("list_a_is_receipt", list_a_is_receipt),
		("list_b_doc_title", list_b_doc_type),
		("list_b_doc_number", list_b_doc_number),
		("list_b_doc_authority", list_b_authority),
		("list_b_doc_expiry", list_b_expiry),
		("list_b_is_receipt", list_b_is_receipt),
		("list_c_doc_title", list_c_doc_type),
		("list_c_doc_number", list_c_doc_number),
		("list_c_doc_authority", list_c_authority),
		("list_c_doc_expiry", list_c_expiry),
		("list_c_is_receipt", list_c_is_receipt),
		("document_copies_stored", document_copies_stored),
		("section_2_signature", section_2_signature),
	):
		if value is not None:
			inner[key] = value

	result = i9.submit_i9_section_2(inner)
	return result.data


# ── 24. list_i9_document_types ──────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("list_i9_document_types", limit=guard.READ_LIMIT)
def list_i9_document_types(user: str, list_category=None) -> dict:
	"""What USCIS accepts, grouped the way Section 2 asks the question.

	THE APP HAD THIS LIST HARDCODED AND THE SERVER HAS HAD THE REAL ONE SINCE
	v0.27.0. `i9_documents.py` seeds all 24 accepted documents as records, an
	operator may deactivate any of them for their own site, and none of that could
	reach a phone — so the picker a foreman scrolls in the orchard was a Swift
	array that goes stale the next time USCIS revises the list, and goes stale
	silently. This is the read that makes it a lookup, and it is the FIRST read on
	the onboarding half of this surface: everything else here writes.

	GROUPED BY LIST, because that is the shape of the choice rather than a
	convenience. Section 2 is "one from List A" OR "one from List B and one from
	List C", and a form that has to make that split itself is a form with its own
	copy of which document is in which category — which is exactly the copy that
	was wrong. `documents` carries the flat list beside it for a caller that
	wants to search across all three.

	VIEW-ONLY AND NOT SCOPED TO AN ENTITY. The federal list of acceptable
	documents is not a fact about a company, and there is nothing on one of these
	rows that belongs to a person: a title, its USCIS code, whether it carries a
	photograph. `guard.endpoint` still runs the kill switch, the role gate, the
	enrolment gate and the rate limit, which is the whole of what this read needs.
	"""
	guard.require_scope(user)
	inner = {}
	category = str(list_category or "").strip().upper()
	if category:
		if category not in ("A", "B", "C"):
			frappe.throw(
				f"list_category {list_category!r} is not an I-9 list. Pass A, B, C, or "
				"nothing at all for the whole table.",
				frappe.ValidationError,
			)
		inner["list_category"] = category

	result = i9.list_i9_document_types(inner)
	grouped = result.data.get("by_list") or {}
	return {
		"documents": result.data.get("documents") or [],
		"count": result.data.get("count") or 0,
		"list_a": grouped.get("A") or [],
		"list_b": grouped.get("B") or [],
		"list_c": grouped.get("C") or [],
		"list_category": category or None,
	}


# ── 25. reverify_i9 ─────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("reverify_i9", mutating=True, limit=guard.WRITE_LIMIT)
def reverify_i9(
	user: str,
	employee=None,
	reason=None,
	document_title=None,
	document_number=None,
	issuing_authority=None,
	document_expiry=None,
	reverification_date=None,
	rehire_date=None,
	verifier_name=None,
	verifier_title=None,
	section_3_signature=None,
	notes=None,
) -> dict:
	"""Section 3, for the returning worker whose authorization ran out.

	THIS IS THE BRANCH THE WIZARD COULD SEE AND COULD NOT TAKE. v0.46.2 gave
	`get_employee` the reconciliation that reports a returning picker's I-9 as
	`Expired` rather than as done — deliberately, because an expired I-9 is
	precisely the case §1324a wants re-examined. What the handset could then do
	about it was nothing: `create_i9_form` refuses a second form for somebody who
	has one, and the only other door was a Desk edit over the columns recording
	what was examined on the day of hire. So the wizard's own answer to its
	hardest case was "find an operator with a laptop".

	`document_expiry` IS RENAMED ON NEITHER SIDE and the argument names are the
	tool's, because there is no shipped Swift struct for this call to be
	compatible with — this endpoint precedes the screen rather than following it.
	Where the app grows one, the renaming trade `submit_i9_section_2` makes is the
	one to make again: the backend moves.

	`verifier_name` IS THE TYPED ONE, not the caller's, for the reason Section 2
	states — the person who examined the document signs their own name, and the
	account that made the call is on the MCP Action Log row regardless.

	EVERY RULE IS THE TOOL'S. That reverification needs a signed Section 2 to
	follow, that List B is not a reverification document, that a document already
	expired on the day it was examined is not evidence of continuing
	authorization, that 'Rehire' needs a rehire date — all of it is 8 CFR
	274a.2(b)(1)(vii)'s and lives in `tools/i9.py`, so none of it is restated here.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee, allowed)

	inner = {"employee": person, "reason": reason, "document_title": document_title,
	         "verifier_name": verifier_name}
	for key, value in (
		("document_number", document_number),
		("issuing_authority", issuing_authority),
		("document_expiry", document_expiry),
		("reverification_date", reverification_date),
		("rehire_date", rehire_date),
		("verifier_title", verifier_title),
		("section_3_signature", section_3_signature),
		("notes", notes),
	):
		if value is not None:
			inner[key] = value

	result = i9.reverify_i9(inner)
	return result.data


# ── 26. submit_w4 ───────────────────────────────────────────────────────────
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


# ── 27. link_badge_to_employee ──────────────────────────────────────────────
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


# ── 27b. resolve_badge ──────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("resolve_badge", limit=guard.READ_LIMIT)
def resolve_badge(user: str, badge_id=None, company=None, shift=None) -> dict:
	"""Whose badge is this — the call between a scan and a name. v0.50.0.

	THE ONE READ THE SCANNING SIDE NEVER HAD. `add_worker_to_shift` takes an
	Employee docname and a camera produces a badge string, so the crew clock
	could scan a whole crew and had no way to turn any of it into the argument
	the roster call wants. The bucket loop had the same gap in a quieter form: it
	could show a foreman the code it read and never the picker's name, so a
	mis-scan looked exactly like a good one until the data reached a Desk.

	IT IS A PII LOOKUP KEYED ON A STRING ANYBODY HOLDING A CARD CAN PRODUCE, and
	that is why it is on `READ_LIMIT` rather than being cheap: sixty an hour is
	a crew clocking in and is not a register being enumerated. It answers only
	within the caller's own entities — `_company` is the same scope check every
	other method here runs — so a badge belonging to another entity on the site
	reads as "no such badge" rather than confirming it exists somewhere.

	IT REFUSES RATHER THAN ANSWERING EMPTY. Unknown, retired, and belonging to
	somebody who has left are three different sentences, because they are three
	different situations with three different fixes and the phone shows whichever
	one it got.

	`shift` IS OPTIONAL AND IS THE SECOND HALF OF THE QUESTION. Given one, the
	answer carries `on_shift` — whether this person is clocked in right now —
	which is what a bin trailer's scanner needs before it accepts a bucket.
	"""
	allowed = guard.require_scope(user)
	inner = {"badge_id": str(badge_id or "").strip(), "company": _company(user, company, allowed)}
	if shift:
		inner["shift"] = guard.require_scoped_doc(FARM_SHIFT, shift, "shift", allowed)
	result = badges.resolve_badge(inner)
	return result.data


# ── 27c. generate_employee_badge_qr ─────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("generate_employee_badge_qr", mutating=True, limit=guard.WRITE_LIMIT)
def generate_employee_badge_qr(user: str, employee=None, docname=None, company=None, regenerate=None, notes=None) -> dict:
	"""Issue (or reprint) this hire's badge and hand back the QR to show them.

	THE TOOL HAS EXISTED SINCE v0.50.0 AND THE PHONE COULD NOT REACH IT. That is
	the whole of what this wrapper is. `generate_employee_badge_qr` mints a
	readable `CF-0001`, writes the register row a bucket scan resolves through
	and draws the code — and the only surface it was published on was the MCP
	tool registry, which the handset does not speak. So the wizard's badge step
	could map a card somebody had already printed elsewhere and could not
	produce one, on a hire day, in a yard, for a worker standing there waiting to
	be told their number.

	`badge_id` IS NOT ACCEPTED, and that is the one thing taken away here. The
	tool lets a caller name the identifier — a Desk operator adopting a card from
	the old `farm_app` uuid stock — and letting a handset do it would put the
	uniqueness of a payroll key in the hands of whatever a foreman typed. The
	phone's job is to ask for a badge; minting is the server's.

	`regenerate` IS ACCEPTED, because the lost-card path is a field problem and
	not a Desk one. Without it the call is IDEMPOTENT: somebody who already holds
	a live badge gets that badge's QR back rather than a second identifier, which
	is what makes the wizard's button safe to press twice on a bad connection.
	With it the old card is retired in the same call — a replacement that leaves
	its predecessor resolving is how a found badge keeps earning.

	THE HR ROLE AND THE ENTITY SCOPE ARE THE TOOL'S OWN. It calls
	`require_hr_role` and `require_company_scope` itself, and it refuses a
	worker who is not Active by name.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)

	inner = {"employee": person, "company": _company(user, company, allowed)}
	if regenerate is not None:
		inner["regenerate"] = regenerate
	if notes:
		inner["notes"] = str(notes).strip()

	result = badges.generate_employee_badge_qr(inner)
	data = result.data or {}
	return {
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		"company": data.get("company"),
		"badge_id": data.get("badge_id"),
		"created": data.get("created"),
		"reused": data.get("reused"),
		"retired_badges": data.get("retired_badges") or [],
		"designation": data.get("designation"),
		# What a card needs to be drawn on the handset: the code, the face (or
		# the initials that stand in for one) and the entity's mark.
		"png_base64": data.get("png_base64"),
		"png_bytes": data.get("png_bytes"),
		"photo_url": data.get("photo_url"),
		"photo_placeholder": data.get("photo_placeholder"),
		"company_logo_url": data.get("company_logo_url"),
	}


# ── 27d. get_employee_badge_pass ────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("get_employee_badge_pass", mutating=True, limit=guard.WRITE_LIMIT)
def get_employee_badge_pass(user: str, employee=None, docname=None, company=None, platform=None,
                            regenerate=None) -> dict:
	"""The badge as a file the foreman AirDrops into the worker's own wallet.

	THE DELIVERY THIS SURFACE WAS MISSING. `generate_employee_badge_qr` hands
	back a PNG, and a PNG has to be printed, laminated and carried — which is a
	trip back to an office in the middle of a hire day, and a card that goes
	through a wash cycle in August. Every worker in the orchard already has a
	phone with a wallet on it. This returns a `.pkpass` the foreman shares
	straight off the handset: it opens into Apple Wallet on the worker's device
	with nothing installed there, and the Android half is a save link in the same
	answer.

	THE BYTES ARE IN THE RESULT AND THAT IS WHY THIS ROUTE EXISTS. The tool
	attaches the pass to the Employee as a private File and hands back a
	`file_url`, which is right for a Desk operator and useless to a handset — the
	app authenticates against THIS door with `X-FarmOps-Token`, not with a Frappe
	session, so a private file URL is a login page to it. `include_base64` is
	therefore set here and is not a body key: a phone that could turn it off
	would be a phone that cannot share what it just asked for.

	`badge_id` IS NOT ACCEPTED, for the reason it is not accepted on
	`generate_employee_badge_qr`: minting a payroll key is the server's job, not
	whatever a foreman typed. `regenerate` IS, because the lost-card path happens
	in a field — and without it the call is IDEMPOTENT, so the wizard's button is
	safe to press twice on a bad connection.

	`attach` IS NOT ACCEPTED EITHER. The pass is always filed against the
	Employee, so a reissue has a record and a Desk operator can hand the same
	file to somebody who lost their phone rather than reissuing a badge for it.

	AN UNSIGNED PASS COMES BACK AS UNSIGNED. On a site with no Apple certificate
	the file is complete and correct and `apple.signed` is false with the reason
	in it — the app should say so rather than sharing a file Wallet will refuse.
	Every other refusal is the tool's: an inactive worker, an entity this account
	cannot reach, an employee of another company.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)

	inner = {
		"employee": person,
		"company": _company(user, company, allowed),
		# See the docstring: the handset cannot fetch a private File, so the
		# bytes travel in the answer. Not a body key.
		"include_base64": True,
	}
	if platform:
		inner["platform"] = platform
	if regenerate is not None:
		inner["regenerate"] = regenerate

	result = wallet_tools.generate_employee_badge_pass(inner)
	data = result.data or {}
	apple = data.get("apple") or {}
	google = data.get("google") or {}
	return {
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		"company": data.get("company"),
		"badge_id": data.get("badge_id"),
		"created": data.get("created"),
		"reused": data.get("reused"),
		"retired_badges": data.get("retired_badges") or [],
		"platform": data.get("platform"),
		"warnings": data.get("warnings") or [],
		# The Apple half, flattened to what a share sheet needs: the bytes, what
		# to call the file, and the UTI that makes iOS open it in Wallet rather
		# than in Files. `pass_json` is deliberately NOT forwarded — it is a
		# debugging read for the Desk and the app has no use for a second copy of
		# what is already inside the archive it was handed.
		"apple": {
			"pkpass_base64": apple.get("pkpass_base64"),
			"file_name": apple.get("file_name"),
			"content_type": apple.get("content_type"),
			"bytes": apple.get("bytes"),
			"sha256": apple.get("sha256"),
			"signed": apple.get("signed"),
			"configured": apple.get("configured"),
			"warnings": apple.get("warnings") or [],
		}
		if data.get("apple")
		else None,
		"google": {
			"save_url": google.get("save_url"),
			"signed": google.get("signed"),
			"configured": google.get("configured"),
			"warnings": google.get("warnings") or [],
		}
		if data.get("google")
		else None,
	}


# ── 27e. set_employee_photo ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("set_employee_photo", mutating=True, limit=guard.WRITE_LIMIT)
def set_employee_photo(user: str, employee=None, docname=None, file_token=None) -> dict:
	"""File a headshot against the Employee and make it the photo on the record.

	THE BADGE CARD READS `Employee.image` AND NOTHING WAS EVER WRITING IT.
	`attach_onboarding_document` files evidence — the bytes land as a private
	File pointing at the Employee and the Employee points nowhere — which is
	right for a List B photograph and leaves a badge printing initials. This is
	the same staged upload followed by the field update that closes the loop.

	IT TAKES A `file_token`, NOT BYTES, exactly like every other upload on this
	surface: `stage_file_chunk` then `finalize_staged_file`, and this call names
	what that produced. One upload path, and it is the one that authenticates.

	THE HR ROLE IS REQUIRED WITH NO EXCEPTION, the same posture
	`attach_onboarding_document` takes and for the same reason: an account that
	could set its own photograph is an account that could put somebody else's
	face on its own badge.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)
	personnel.require_hr_role()

	if not str(file_token or "").strip():
		frappe.throw(
			"file_token is required — upload the photograph with stage_file_chunk and "
			"finalize_staged_file first, then send the token that returns.",
			frappe.ValidationError,
		)

	result = personnel.set_employee_photo({"employee": person, "file_token": file_token})
	data = result.data or {}
	return {
		"employee": data.get("employee"),
		"photo_url": data.get("photo_url"),
		"file_token": data.get("file_token"),
		"file_name": data.get("file_name"),
		"image_set": data.get("image_set"),
		"replaced": data.get("replaced"),
		"already_attached": data.get("already_attached"),
	}


# ── 28. sync_bucket_entries ─────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("sync_bucket_entries", mutating=True, limit=guard.UPLOAD_LIMIT)
def sync_bucket_entries(user: str, entries=None, company=None, shift=None) -> dict:
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

	THE BADGE POLICY IS `strict` HERE AND IS NOT A BODY KEY. v0.50.0. The tool
	defaults to `lenient` — file the capture, resolve the badge later — and that
	is right for a Desk import of a morning taken before anybody mapped the
	cards. It is wrong for a phone. Badges are minted by this app now
	(`generate_employee_badge_qr` writes the register at the moment the card is
	printed), so a handset scanning a string this site never issued has scanned a
	barcode on a soda can, a Wi-Fi join code or an operator's login QR — and
	filing that produces a piecework row nobody will ever claim, which is worse
	than the refusal the picker's foreman can act on while still standing there.
	A body key that could relax it would hand that decision to the handset.

	`shift` IS OPTIONAL AND IS THE OTHER HALF OF IT. Given the shift the crew
	clock has open, every capture is checked against its roster: a badge that
	resolves to somebody who is not clocked in is refused with their name in the
	sentence. Omitted, the badge still has to resolve to an employed person.
	"""
	allowed = guard.require_scope(user)
	wanted = _company(user, company, allowed)
	inner = {
		"entries": _bucket_entries(entries, wanted),
		"badge_policy": bucket_bridge.BADGE_POLICY_STRICT,
	}
	if shift:
		inner["shift"] = guard.require_scoped_doc(FARM_SHIFT, shift, "shift", allowed)
	result = bucket_log.sync_bucket_entries(inner)
	return result.data


# ── 29. start_shift ─────────────────────────────────────────────────────────
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


# ── 30. add_worker_to_shift ─────────────────────────────────────────────────
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


# ── 31. end_shift ───────────────────────────────────────────────────────────
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


# ── 32. get_i9_form ─────────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("get_i9_form", limit=guard.READ_LIMIT)
def get_i9_form(user: str, employee=None, docname=None) -> dict:
	"""The whole I-9 back, for the screen that shows what was collected.

	THE ONLY WAY A HANDSET COULD READ AN I-9 BEFORE THIS WAS TO HAVE JUST WRITTEN
	ONE. `create_i9_form`, `submit_i9_section_1`, `submit_i9_section_2` and
	`reverify_i9` each hand back the record they changed, and `get_employee`
	reports a one-word status — so a foreman who opened the wizard on somebody
	already verified could be told `Verified` and nothing else. Which documents?
	Examined by whom? Is there a receipt still owed? All on the server, none of it
	reachable, and every one of those is a question an audit asks first.

	THE SSN IS THE LAST FOUR AND NOTHING ELSE, which is not this wrapper's doing:
	`i9._i9_fields` does not list `ssn_full` and argues at length why it never
	will. The encrypted nine digits are read in exactly one place in this app —
	`render_i9_pdf`, behind two switches — and not here.

	THE HR ROLE IS REQUIRED FOR ANYBODY ELSE'S RECORD AND NOT FOR THE CALLER'S
	OWN, the same exception `get_employee` carries and for the same reason: a
	worker reading their own I-9 is reading their own immigration paperwork, not
	the personnel file. `_employee` resolves the caller through `Employee.user_id`
	and nothing in the body, so the exception cannot be claimed by naming
	somebody else.

	READING IS LOGGED. `i9.get_i9_form` writes a `Viewed` row to the I-9 Audit
	Log on every call, which is the whole point of that log: who looked at this
	person's immigration status, when, and from where.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)

	if person != fieldwork._employee_for(user):
		personnel.require_hr_role()

	result = i9.get_i9_form({"employee": person})
	return result.data


# ── 33. generate_i9_pdf ─────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("generate_i9_pdf", mutating=True, limit=guard.WRITE_LIMIT)
def generate_i9_pdf(user: str, employee=None, docname=None, overwrite=None,
                    additional_information=None) -> dict:
	"""Fill the federal form from the record and hand the phone a URL for it.

	THE END OF THE ONBOARDING FLOW, and the step that was missing from it. The
	wizard collects Section 1 and Section 2 in an orchard and then has nothing to
	show for it: what an employer has to be able to produce for an inspection
	under 8 U.S.C. §1324a(b)(3) is Form I-9, and until v0.47.1 the only artefact
	this app made was a doctype. `i9.render_i9_pdf` writes the collected values
	into the USCIS fillable PDF this app ships and attaches it privately to the
	record; this hands back `file_url`, which is what the app opens, prints and
	hands to the two people who have to sign it.

	`include_full_ssn` IS NOT ACCEPTED HERE and is not a rename away — it is
	absent. It would print somebody's nine-digit Social Security number onto a
	page a phone in a packing shed could then mail anywhere, and it needs a
	decision about the site's own retention policy that belongs to an operator
	with the Desk in front of them rather than to whoever is holding the handset.
	The rendered page leaves the box empty and the employee writes the number on
	it, which is how the paper form has always worked.

	`overwrite` IS FORWARDED, because the wizard's realistic second call is the
	one after a correction — a misspelled name, a document number typed wrong —
	and refusing it would leave the phone holding a stale PDF with no way to ask
	for a fresh one. The File that was there stays attached to the record either
	way, so nothing is lost by re-rendering.

	EVERY REFUSAL IS THE TOOL'S: that a Destroyed I-9 is not re-rendered, that
	the site needs pypdf and the shipped template, that a second render without
	`overwrite` is refused. None of it is restated here.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)
	personnel.require_hr_role()

	inner = {"employee": person}
	if overwrite is not None:
		inner["overwrite"] = overwrite
	if additional_information is not None:
		inner["additional_information"] = additional_information

	result = i9.render_i9_pdf(inner)
	data = result.data
	return {
		"name": data.get("name"),
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		"status": data.get("status"),
		"file_url": data.get("file_url"),
		"file_name": data.get("file_name"),
		"bytes": data.get("bytes"),
		"edition": data.get("edition"),
		"incomplete": data.get("incomplete") or [],
		"reverifications_not_on_page": data.get("reverifications_not_on_page") or 0,
		"replaced": data.get("replaced"),
		"note": data.get("note"),
	}


# ── 34. upload_signed_i9 ────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("upload_signed_i9", mutating=True, limit=guard.UPLOAD_LIMIT)
def upload_signed_i9(user: str, employee=None, docname=None, file_token=None, overwrite=None) -> dict:
	"""File the photographed or scanned signed copy against the I-9 record.

	THE OTHER HALF OF `generate_i9_pdf`, and the half that is the federal record.
	The rendered page is printed, the employee signs Section 1 and the verifier
	signs Section 2 — with a pen, because 8 CFR 274a.2(h) has requirements a name
	typed into a PDF does not meet — and the phone photographs the signed sheet.
	That photograph is what an inspection is shown.

	IT TAKES A `file_token`, NOT BYTES. The photograph goes up through
	`stage_file_chunk` / `finalize_staged_file` exactly like the evidence on a
	completed task and the signature on a closed shift: 512 KB at a time, hashed
	at capture and verified on assembly, resumable over a thin field link. This
	call names the File that upload produced and attaches it. A second upload
	path taking a base64 body would have its own size limit and its own way of
	failing halfway up a hill.

	`upload_id` IS NOT AN ARGUMENT AND `finalize_staged_file` IS NOT CALLED FOR
	YOU. The app already finalises its own uploads and already holds the token;
	doing it again here would mean this endpoint owning a staging session it did
	not open, and a partial upload would fail inside a call that says it is
	filing an I-9.

	THE HR ROLE IS REQUIRED WITH NO EXCEPTION — not even for the caller's own
	record. `get_i9_form` lets a worker read their own I-9 because reading it
	harms nobody; this WRITES the document the employer will be inspected on, and
	an account that could file its own signed I-9 could file one nobody signed.

	Every other refusal is the tool's: a Destroyed I-9, a file that is not a scan,
	a second signed copy without `overwrite`. The File is made private on the way
	in whatever it was.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)
	personnel.require_hr_role()

	if not str(file_token or "").strip():
		frappe.throw(
			"file_token is required — upload the signed copy with stage_file_chunk and "
			"finalize_staged_file first, then send the token that returns.",
			frappe.ValidationError,
		)

	inner = {"employee": person, "file_token": file_token}
	if overwrite is not None:
		inner["overwrite"] = overwrite

	result = i9.attach_signed_i9(inner)
	data = result.data
	return {
		"name": data.get("name"),
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		"status": data.get("status"),
		"signed_pdf": data.get("signed_pdf"),
		"file_token": data.get("file_docname"),
		"replaced": data.get("replaced"),
	}


# ── 35. list_authorized_signers ─────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("list_authorized_signers", limit=guard.READ_LIMIT)
def list_authorized_signers(user: str, include_inactive=None, form_type=None) -> dict:
	"""Who this employer has authorised to sign an I-9 or a W-4.

	THE READ THE SECTION 2 SCREEN NEEDS BEFORE IT OFFERS A NAME. v0.48.0 made
	the verifier a roster lookup rather than a free-text box, and a wizard that
	could not read the roster would have to discover its own account's
	authorisation by submitting a form and being refused — in an orchard, having
	just examined somebody's documents.

	`configured` IS THE FIELD THAT MATTERS TO THE APP. False means the site has
	no roster and the old free-text box is still correct; true means the name is
	the server's to supply and the field should be prefilled and read-only
	unless the foreman is filing on somebody else's behalf.

	THE HR ROLE IS REQUIRED. The roster names the people who can attest to a
	federal form for this business, which is not a field worker's read.
	"""
	guard.require_scope(user)
	personnel.require_hr_role()

	inner = {}
	if include_inactive is not None:
		inner["include_inactive"] = include_inactive
	if form_type is not None:
		inner["form_type"] = form_type

	result = signers.list_authorized_signers(inner)
	return result.data


# ── 36. add_authorized_signer ───────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("add_authorized_signer", mutating=True, limit=guard.WRITE_LIMIT)
def add_authorized_signer(user: str, signer_user=None, full_name=None, title=None,
                          can_sign_i9=None, can_sign_w4=None) -> dict:
	"""Put one account on the roster from the app.

	`signer_user` RATHER THAN `user`, and the rename is not cosmetic.
	`guard.endpoint` injects the AUTHENTICATED caller into `user` and
	`routes.accepted_arguments` drops any `user` a body carries, precisely so an
	account cannot name somebody else in a request. The person being authorised
	is a different argument and has to have a different name, or it would be
	dropped on the way in and this endpoint would silently authorise the caller.

	`active` IS NOT ACCEPTED. Adding somebody inactive is a configuration state
	with no meaning on a phone — the app adds a signer in order to let them sign.
	`update_authorized_signer` and `remove_authorized_signer` are what change it
	afterwards.

	EVERY REFUSAL IS THE TOOL'S: an account that is not on the site, a second row
	for one account, a full name that can be found nowhere. So is the warning
	that this row was the first and has just turned enforcement on for the whole
	site — which the app should show, because the next foreman to file a Section
	2 is the one it affects.
	"""
	guard.require_scope(user)
	personnel.require_hr_role()

	inner = {"user": signer_user}
	for key, value in (
		("full_name", full_name),
		("title", title),
		("can_sign_i9", can_sign_i9),
		("can_sign_w4", can_sign_w4),
	):
		if value is not None:
			inner[key] = value

	result = signers.add_authorized_signer(inner)
	return result.data


# ── 37. update_authorized_signer ────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("update_authorized_signer", mutating=True, limit=guard.WRITE_LIMIT)
def update_authorized_signer(user: str, signer_user=None, full_name=None, title=None,
                             can_sign_i9=None, can_sign_w4=None, active=None) -> dict:
	"""Change one signer's printed name, title, or what they may sign.

	`signer_user` for the same reason as above. `active` IS accepted here, and
	it is the only way back from `remove_authorized_signer` — a roster with
	nobody active refuses every Section 2 on the site, so the call that undoes
	that has to be reachable from wherever the call that caused it was made.
	"""
	guard.require_scope(user)
	personnel.require_hr_role()

	inner = {"user": signer_user}
	for key, value in (
		("full_name", full_name),
		("title", title),
		("can_sign_i9", can_sign_i9),
		("can_sign_w4", can_sign_w4),
		("active", active),
	):
		if value is not None:
			inner[key] = value

	result = signers.update_authorized_signer(inner)
	return result.data


# ── 38. remove_authorized_signer ────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("remove_authorized_signer", mutating=True, limit=guard.WRITE_LIMIT)
def remove_authorized_signer(user: str, signer_user=None) -> dict:
	"""Deactivate one signer. The row is kept — see `tools/signers.py`.

	NOTHING IS DELETED HERE OR ANYWHERE. A form signed last season was signed by
	whoever was authorised last season, and the tool clears a flag rather than
	dropping a row so that stays answerable. The result carries the warning when
	this call left the site with no active signers at all, which is a state that
	refuses every subsequent Section 2 — the app should surface it rather than
	let the next foreman find out in a field.
	"""
	guard.require_scope(user)
	personnel.require_hr_role()

	result = signers.remove_authorized_signer({"user": signer_user})
	return result.data


# ── 39. generate_w4_pdf ─────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("generate_w4_pdf", mutating=True, limit=guard.WRITE_LIMIT)
def generate_w4_pdf(user: str, employee=None, docname=None, tax_year=None, overwrite=None) -> dict:
	"""Fill the federal W-4 from the record and hand the phone a URL for it.

	THE OTHER HALF OF `generate_i9_pdf`, and the last artefact the onboarding
	flow was missing. The wizard has collected withholding elections since
	v0.45.0 and had nothing to show for them: what an employer keeps for an
	employee's withholding is Form W-4, and until v0.48.0 the only thing this app
	produced was a doctype. `w4.render_w4_pdf` writes the collected values into
	the IRS fillable PDF this app ships and attaches it privately to the record;
	this hands back `file_url`, which is what the app opens, prints and hands to
	the employee to sign.

	THE EMPLOYER BLOCK NEEDS NOTHING FROM THE PHONE. Step 5's employer name,
	address, EIN and first date of employment are resolved on the server from
	I-9 Settings, the Company and `Employee.date_of_joining` — so a foreman in an
	orchard is not typing an EIN into a handset, which is the failure mode that
	would put a wrong one on a federal form.

	`overwrite` IS FORWARDED, because the wizard's realistic second call is the
	one after a correction — a filing status picked wrong, a dependent count off
	by one — and refusing it would leave the phone holding a stale PDF with no
	way to ask for a fresh one. The File that was there stays attached either way.

	THE HR ROLE IS REQUIRED. A W-4 names a person's filing status, their
	dependents and their other income; it is a payroll record and not a picker's
	to render.

	EVERY REFUSAL IS THE TOOL'S: that the site needs pypdf and the shipped
	template, that a second render without `overwrite` is refused, that there is
	no active W-4 for this person. None of it is restated here.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)
	personnel.require_hr_role()

	inner = {"employee": person}
	if tax_year is not None:
		inner["tax_year"] = tax_year
	if overwrite is not None:
		inner["overwrite"] = overwrite

	result = w4.render_w4_pdf(inner)
	data = result.data
	return {
		"name": data.get("name"),
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		"tax_year": data.get("tax_year"),
		"status": data.get("status"),
		"file_url": data.get("file_url"),
		"file_name": data.get("file_name"),
		"bytes": data.get("bytes"),
		"edition": data.get("edition"),
		"template_tax_year_matches": data.get("template_tax_year_matches"),
		"incomplete": data.get("incomplete") or [],
		"replaced": data.get("replaced"),
		"note": data.get("note"),
	}


# ── 40. attach_onboarding_document ──────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("attach_onboarding_document", mutating=True, limit=guard.UPLOAD_LIMIT)
def attach_onboarding_document(user: str, employee=None, docname=None, file_token=None, document_kind=None) -> dict:
	"""File an uploaded photograph or signature against the Employee record.

	v0.48.3. THE CALL WHOSE ABSENCE LOST EVERY PIECE OF ONBOARDING EVIDENCE.
	The wizard collects six files — the Section 1 signature, the List A or List
	B/C document photographs, the Section 3 signature and the photographed W-4 —
	and this surface published no way to file any of them. So the app sent them
	to Frappe's own `/api/method/upload_file`, which is not one of this app's
	paths, which means `fallback_auth._is_mobile_path` never looked at the
	`X-FarmOps-Token` header, which means the funnel-stripped request arrived as
	Guest and Frappe answered 200 with the Desk login page. The app checked the
	status and not the body, so the wizard advanced and the I-9 read Complete
	with nothing behind it. That is the failure this endpoint ends, and the
	§1324a(b)(3) inspection is the reason it mattered.

	IT TAKES A `file_token`, NOT BYTES — the docname `finalize_staged_file` hands
	back, exactly like `upload_signed_i9` and `complete_task_via_mobile`. The
	photograph goes up in 512 KB slices, hashed at capture and verified on
	assembly, and this call names what that produced. There is now ONE upload
	path from this app and it is the one that authenticates.

	WHY NOT JUST LET `finalize_staged_file` ATTACH. Because it deliberately
	refuses an attachment target: `api/files.py` argues it at length, and the
	argument is that a field worker who could name the parent could hang a file
	off a Journal Entry or somebody else's lease. This endpoint names ONE parent
	doctype, in code, and proves the docname is an Employee inside the caller's
	own entities before it goes near the File.

	THE HR ROLE IS REQUIRED WITH NO EXCEPTION, not even for the caller's own
	record. These are the photographs an employer is inspected on, and an account
	that could file its own would be an account that could file anything as its
	own identity documents.
	"""
	allowed = guard.require_scope(user)
	person = _employee_argument(employee or docname, allowed)
	personnel.require_hr_role()

	if not str(file_token or "").strip():
		frappe.throw(
			"file_token is required — upload the photograph with stage_file_chunk and "
			"finalize_staged_file first, then send the token that returns.",
			frappe.ValidationError,
		)

	inner = {"employee": person, "file_token": file_token}
	if document_kind is not None:
		inner["document_kind"] = document_kind

	result = personnel.attach_employee_document(inner)
	data = result.data
	return {
		"employee": data.get("employee"),
		"document_kind": data.get("document_kind"),
		"file_token": data.get("file_token"),
		"file_url": data.get("file_url"),
		"file_name": data.get("file_name"),
		"is_private": data.get("is_private"),
		"already_attached": data.get("already_attached"),
	}


# ── 41. get_active_model ────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("get_active_model", limit=guard.READ_LIMIT)
def get_active_model(user: str, company=None, piecework_activity=None) -> dict:
	"""Which ML model is deployed for one piecework activity, and its manifest.

	v0.52.0. THE MODEL BINARY IS NOT IN THIS ANSWER — `get_model_file_chunk` is
	the second call, made only when the manifest's `uuid` no longer matches
	whatever this app already has cached on disk. `manifest.metadata.downloadable`
	says whether that second call has anything to read yet; when it does not,
	the model is registered but `attach_model_file` has not run on this site.
	"""
	allowed = guard.require_scope(user)
	wanted = guard.require_company(user, company, allowed) or (allowed[0] if allowed else "")
	activity = str(piecework_activity or "").strip()
	if not activity:
		frappe.throw("piecework_activity is required.", frappe.ValidationError)

	result = ml_model_tools.get_active_model({"company": wanted, "piecework_activity": activity})
	return result.data


# ── 42. get_model_file_chunk ────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("get_model_file_chunk", limit=guard.UPLOAD_LIMIT)
def get_model_file_chunk(user: str, model=None, chunk_index=None, chunk_bytes=None) -> dict:
	"""One base64 slice of an ML model's binary, in the same shape FarmOpsKit
	already streams uploads in.

	v0.52.0, AND THE WHOLE POINT OF THIS RELEASE: an iOS app reads the model
	back from HERE, through the credential it already holds, rather than
	opening a second connection to Volume Vision with a second credential —
	see `tools/ml_model.py`'s module docstring.

	`model` NAMES AN ML Model RECORD, SCOPED THE SAME WAY A TASK IS. A phone's
	own cache is keyed on `uuid` from get_active_model's manifest, which is
	`source_uuid` rather than a docname when this model came from Volume
	Vision — `_model_docname` resolves that spelling before
	`guard.require_scoped_doc` refuses one that belongs to an entity this
	caller cannot reach as not found, same as any other docname argument here.
	"""
	allowed = guard.require_scope(user)
	name = guard.require_scoped_doc(ML_MODEL, _model_docname(model), "model", allowed)

	result = ml_model_tools.get_model_file_chunk(
		{"model": name, "chunk_index": chunk_index, "chunk_bytes": chunk_bytes}
	)
	return result.data


# ── 43. list_onboarding_reference_data ──────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("list_onboarding_reference_data", limit=guard.READ_LIMIT)
def list_onboarding_reference_data(user: str, company=None) -> dict:
	"""The four dropdowns on the wizard's Assignment step, in one call.

	v0.54.0, and the same failure `list_i9_document_types` fixed for the I-9's
	document picker: a Swift array of employment types compiled into the app is a
	copy of a table an operator maintains in the Desk, and it goes stale silently.
	`create_employee` checks every one of these against THIS site's records and
	refuses a value that names nothing — so a hardcoded list is not merely stale,
	it is a wizard whose Assignment step fails at the end of a hire with "not a
	Designation on this site" and no way to find out what is.

	ONE CALL FOR FOUR LISTS, on purpose. They are read together, once, when the
	step opens, and four round trips over a tailgate LTE connection is four
	chances to half-populate a form. `list_i9_document_types` groups its answer
	for the same reason.

	A MASTER THIS SITE DOES NOT HAVE COMES BACK EMPTY AND IS NAMED IN
	`masters_absent`, never omitted and never an error. Branch, Department,
	Designation and Employment Type all ship with Frappe HR; a site without hrms
	has none of them, and the honest answer there is a wizard that offers no
	choices for that field rather than a hire that cannot start. `create_employee`
	agrees — `_clean` does not check a Link whose target doctype is absent.

	DEPARTMENTS ARE SCOPED TO THE CALLER'S ENTITIES, and the other three are not,
	because Department is the only one of the four that carries a company on a
	stock Frappe HR. Group departments are dropped: `is_group` marks a node in the
	tree rather than somewhere a person is assigned, and an Employee pointed at
	one is a report that double-counts them.

	EVERY BRANCH ROW CARRIES ITS PARCELS, and that is what makes the Housing step
	reachable from the Assignment step. An Employee carries a Branch and a Housing
	Unit stands on a Parcel; `Parcel.branch` is the only column joining the two,
	and without it a wizard that has just asked which camp somebody works at
	cannot then show that camp's cabins.

	`parcels` IS A LIST AND `parcel` IS THE SINGLE ONE WHEN THERE IS EXACTLY ONE.
	A camp is a place rather than a deed — one that grew across a fence line is
	two parcels — so the list is the real answer and the scalar is the
	convenience for the ordinary case. `parcel` is null when a branch maps to
	none OR to several, and a client that reads only the scalar must treat null as
	"ask the server", which is what passing `branch` to `list_available_housing`
	does. That endpoint resolves the same mapping through the same function, so
	the phone never has to do the lookup itself and the two can never disagree.

	A BRANCH WITH NO PARCELS IS REPORTED, NOT HIDDEN — empty `parcels`, and the
	branch still in the list. It is a real operating unit somebody may legitimately
	hire into; what it is not is a camp with housing, and `list_available_housing`
	says so in its own words rather than returning an empty list that reads as a
	full camp.

	VIEW-ONLY AND NOT A PERSONNEL READ. There is nothing on these rows about a
	person — a job title, a camp name, an employment class. `guard.endpoint` has
	run the kill switch, the role gate, the enrolment gate and the rate limit,
	which is the whole of what this needs; `search_employees`, which really does
	read the register, carries the HR role gate and this deliberately does not.
	"""
	allowed = guard.require_scope(user)
	wanted = guard.require_company(user, company, allowed)
	entities = [wanted] if wanted else allowed

	out: dict = {"company": wanted or None}
	absent = []
	for key, doctype, label_field in REFERENCE_MASTERS:
		if not compat.doctype_exists(doctype):
			out[key] = []
			absent.append(doctype)
			continue

		filters = {}
		if doctype == "Department":
			if compat.has_field(doctype, "company"):
				filters["company"] = ("in", entities)
			if compat.has_field(doctype, "is_group"):
				filters["is_group"] = 0

		fields = compat.existing_fields(doctype, ["name", label_field, "company"])
		rows = frappe.db.get_all(
			doctype,
			filters=filters or None,
			fields=fields,
			order_by="name asc",
			limit_page_length=REFERENCE_LIMIT,
		)
		listed = [
			{
				"name": row.get("name"),
				"label": str(row.get(label_field) or "").strip() or row.get("name"),
				"company": row.get("company") or None,
			}
			for row in rows or []
		]
		# The belt to the braces on the one master that has a company at all. It
		# runs on all four because `scoped` keeps a row with no company, so a
		# Designation is untouched and a Department that slipped the filter is not.
		out[key] = guard.scoped(listed, allowed)

	# The ground each branch holds, in ONE query for every row rather than one per
	# row. Scoped to the entities this caller may reach, so a branch that also has
	# parcels under a company they cannot see reports only the ones they can — the
	# same rule every other read on this surface follows, applied to the join
	# rather than only to the rows.
	mapping = housing_tools.branch_parcel_map(
		[row["name"] for row in out["branches"]], wanted or entities
	)
	for row in out["branches"]:
		parcels = mapping.get(row["name"], [])
		row["parcels"] = parcels
		# Null for none AND for several. A scalar that silently picked the first
		# of two parcels would send half a camp's cabins missing, and a client
		# reading only this field has to fall back to asking the server — which
		# is what passing `branch` to `list_available_housing` does.
		row["parcel"] = parcels[0] if len(parcels) == 1 else None
		row["parcel_count"] = len(parcels)

	out["counts"] = {key: len(out[key]) for key, _doctype, _label in REFERENCE_MASTERS}
	out["masters_absent"] = absent
	out["branches_without_parcels"] = [
		row["name"] for row in out["branches"] if not row["parcels"]
	]
	return out


# ── 44. list_available_housing ──────────────────────────────────────────────
@frappe.whitelist(methods=["POST", "GET"])
@guard.endpoint("list_available_housing", limit=guard.READ_LIMIT)
def list_available_housing(
	user: str, company=None, parcel=None, branch=None, include_full=None, employee=None
) -> dict:
	"""Which cabins have a bed free tonight, and how full each one already is.

	v0.54.0. The wizard's Housing step, and the read `assign_housing` exists to be
	the write for. Beds and bodies per unit, so a foreman standing at a tailgate
	can put somebody somewhere without walking the camp or opening the Desk.

	IT COUNTS OCCUPANTS AND DOES NOT NAME THEM. `list_housing_units` returns an
	`occupants` list of employee names — who sleeps in which cabin, which is a
	personnel fact and is exactly the sort of thing that has no business on a
	picker's phone merely because the vacancy count does. The count is what fills
	a dropdown; the names are read in the Desk, or through
	`get_employee_housing_history` on the MCP console. That split is the reason
	this read carries no HR role gate where `search_employees` does.

	`employee` IS THE ONE ARGUMENT THAT CROSSES THAT LINE, AND IT CARRIES THE GATE
	WITH IT. Passing it returns `previous_assignment` — where this person slept
	last season, so the wizard can offer "Last year: MC-Cabin-07" at the top of
	the list and a returning picker is one tap instead of a scroll through forty
	cabins nobody remembers the numbers of.

	That is exactly the fact the paragraph above keeps off this endpoint: a named
	person, a named cabin, and the dates between them. So `personnel.require_hr_role()`
	runs WHEN AND ONLY WHEN `employee` is passed. Without it, this method would be
	an unauthenticated-by-role way to walk the housing register one employee
	docname at a time — the same register `search_employees` guards and
	`assign_housing` guards, reachable by anybody holding a picker's phone. The
	vacancy read stays open to a Field Worker because it still names nobody; the
	onboarding phone that uses this is enrolled as a Farm Manager already, which is
	stated in this module's header and is what makes the gate free rather than
	restrictive.

	IT READS ONLY *ENDED* ASSIGNMENTS, most recent first. A returning worker is by
	definition somebody whose last stay finished; an open assignment means they are
	housed RIGHT NOW, and offering "last year: Cabin 7" to somebody currently in
	Cabin 7 is an offer to double-book them. `currently_housed` says so instead, so
	the wizard can show that rather than a stale preference.

	`previous_assignment.available` IS COMPUTED FOR THE UNIT ITSELF, not read off
	the list above. The list is filtered — by branch, and by the default that drops
	full and condemned units — so a cabin that is missing from it is exactly the
	case this field has to answer for, and looking the answer up in a list that
	dropped it would report every full cabin as available.

	NON-RESIDENTIAL UNITS ARE NOT IN THE ANSWER AT ALL. A shower block and a shop
	are Housing Units with a capacity of zero, `create_housing_assignment` refuses
	an assignment into either by name, and a dropdown offering them is a dropdown
	whose next screen is a refusal. Uninhabitable units are LISTED and marked
	`assignable: false` with the reason — a foreman who cannot find the cabin they
	expected needs to be told it is condemned, not shown a shorter list.

	`include_full=true` KEEPS THE UNITS WITH NO BED LEFT, marked the same way. The
	default drops them, because the question this answers is "where can somebody
	sleep"; the flag is for the screen that shows the whole camp.

	`branch` RESOLVES TO ITS PARCELS SERVER-SIDE, so the phone passes the camp it
	just hired somebody into and gets that camp's cabins back. A Housing Unit
	stands on a Parcel and carries no Branch of its own — a person REPORTS to a
	branch, a cabin STANDS ON ground somebody owns — and `Parcel.branch` (v0.54.0)
	is the column joining the two. It is resolved through
	`housing.parcels_for_branch`, which is the same function
	`list_onboarding_reference_data` fills its `parcels` field from, so the
	mapping the wizard was shown and the mapping this filters on cannot disagree.

	A BRANCH MAY HOLD SEVERAL PARCELS and every one of them is included. A camp
	that grew across a fence line is two parcels, and a filter that took only the
	first would hide half the beds on exactly the operations big enough to have
	the problem.

	THE THREE WAYS THIS CAN FAIL ARE THREE DIFFERENT ANSWERS, and none of them is
	a silent empty list — an empty camp reads on a phone as "no room", which is
	the one wrong answer here:

	  * the branch names no Branch record → REFUSED, naming it. A typo resolves to
	    no parcels and would otherwise look exactly like a full camp.
	  * the branch is real but no parcel carries it → the whole list, with
	    `branch_filter_applied: false` and `branch_note` saying that no ground is
	    tagged with this branch and that `update_parcel(branch=…)` is the fix.
	  * this site has no `Parcel.branch` column yet (not migrated) → the same,
	    with `branch_note` naming the migration.

	`parcel` IS STILL ACCEPTED and is narrower than `branch`. Passing both filters
	to the intersection, which is what somebody asking for one parcel of a
	two-parcel camp means.
	"""
	allowed = guard.require_scope(user)
	wanted = guard.require_company(user, company, allowed)
	compat.require_doctype(
		HOUSING_UNIT,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)

	# The gate rides with the argument. See the docstring: everything else this
	# method returns is a building and a bed count, and this one thing is a named
	# person's housing history.
	previous = None
	if str(employee or "").strip():
		personnel.require_hr_role()
		previous = _previous_assignment(
			guard.require_scoped_doc(EMPLOYEE, employee, "employee", allowed), allowed
		)

	branch_wanted = str(branch or "").strip()
	branch_parcels: list = []
	branch_note = None
	if branch_wanted:
		# A branch that names nothing is refused BEFORE the register is read. It
		# resolves to no parcels, and "no parcels" and "no beds" produce the same
		# empty list from here on — so the mistake has to be caught while it can
		# still be told apart from an answer.
		if compat.doctype_exists("Branch") and not frappe.db.exists("Branch", branch_wanted):
			frappe.throw(
				f"branch {branch_wanted} is not one on this site. "
				"list_onboarding_reference_data has the branches, each with the parcels it "
				"holds. Nothing was read.",
				frappe.DoesNotExistError,
			)
		if not compat.has_field("Parcel", "branch"):
			branch_note = (
				"This site's Parcel doctype has no branch column, so a branch cannot be "
				"resolved to the ground it holds. Run `bench --site <site> migrate` after "
				"upgrading to v0.54.0. Every unit is listed rather than none."
			)
		else:
			branch_parcels = housing_tools.parcels_for_branch(branch_wanted, wanted or allowed)
			if not branch_parcels:
				branch_note = (
					f"No parcel is tagged with branch {branch_wanted}, so there is no ground "
					"to look for housing on. Set it with update_parcel(parcel=..., "
					f"branch='{branch_wanted}'). Every unit is listed rather than none."
				)

	inner = {"limit": HOUSING_LIST_LIMIT}
	if wanted:
		inner["company"] = wanted
	if str(parcel or "").strip():
		inner["parcel"] = str(parcel).strip()

	result = housing_tools.list_housing_units(inner)

	branch_applied = bool(branch_parcels)
	permitted_parcels = set(branch_parcels)

	show_full = str(include_full or "").strip().lower() in ("1", "true", "yes")
	units = []
	for unit in result.data.get("units") or []:
		if not unit.get("residential"):
			continue
		if branch_applied and unit.get("parcel") not in permitted_parcels:
			continue

		capacity = int(unit.get("capacity") or 0)
		occupants = int(unit.get("currently_assigned") or 0)
		# A unit nobody has given a capacity is NOT reported as full. Zero here
		# means unmeasured, which `lawful_occupancy` produces for a cabin with no
		# floor area on file, and a camp whose capacities were never entered would
		# otherwise come back with every bed taken and no way to tell why.
		open_beds = max(0, capacity - occupants) if capacity else None
		condemned = unit.get("condition") == "Uninhabitable"
		full = bool(capacity) and occupants >= capacity

		if not show_full and (full or condemned):
			continue

		reason = None
		if condemned:
			reason = "Marked Uninhabitable. It has to be repaired and inspected before anybody is put in it."
		elif full:
			reason = f"All {capacity} bed(s) are taken."

		units.append(
			{
				"name": unit.get("name"),
				"unit_name": unit.get("unit_name"),
				"unit_type": unit.get("unit_type"),
				"parcel": unit.get("parcel"),
				"company": unit.get("owning_entity"),
				"capacity": capacity or None,
				"current_occupants": occupants,
				"open_beds": open_beds,
				"status": "Uninhabitable" if condemned else ("Full" if full else "Available"),
				"condition": unit.get("condition"),
				"assignable": not (condemned or full),
				"unassignable_reason": reason,
				"max_occupants_per_or_law": unit.get("max_occupants_per_or_law"),
				"capacity_over_lawful_occupancy": unit.get("capacity_over_lawful_occupancy"),
				"inspection_overdue": unit.get("inspection_overdue"),
				"gps": unit.get("gps"),
			}
		)

	units = guard.scoped(units, allowed)
	return {
		"units": units,
		"count": len(units),
		"assignable_count": sum(1 for unit in units if unit["assignable"]),
		"open_beds": sum(unit["open_beds"] or 0 for unit in units),
		"previous_assignment": previous,
		"company": wanted or None,
		"parcel": str(parcel or "").strip() or None,
		"branch": branch_wanted or None,
		"branch_filter_applied": branch_applied,
		# The ground the branch resolved to, echoed back. A foreman looking at an
		# unexpectedly short list needs to see which parcels were searched, and a
		# client that wants to cache the mapping gets it here rather than making a
		# second call for it.
		"branch_parcels": branch_parcels,
		"branch_note": branch_note,
		"include_full": show_full,
	}


# ── 45. assign_housing ──────────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("assign_housing", mutating=True, limit=guard.WRITE_LIMIT)
def assign_housing(
	user: str,
	employee=None,
	housing_unit=None,
	check_in_date=None,
	end_date=None,
	deposit_paid=None,
	housing_deduction_from_wages=None,
	notes=None,
) -> dict:
	"""Put one new hire in one cabin from one date. The wizard's Housing step.

	v0.54.0. IT DELEGATES to `create_housing_assignment`, so the overlap rule, the
	refusal of a shower block, the refusal of a condemned unit, the deposit
	arithmetic and the Section 119 note are all the code an operator gets on the
	MCP console — the reason `create_employee` delegates to `tools/employee.py`.

	IT CARRIES THE HR ROLE GATE. A Housing Assignment names a person, a building
	and the dates between them; it is the audit trail defending an IRS Section 119
	exclusion and the record a wage claim about an ORS 653 housing deduction is
	answered from. That is a personnel record, and the same gate `search_employees`
	applies by hand applies here for the same reason — a picker holding a perfectly
	good Farm Ops grant is refused, and the operator enrolling an onboarding phone
	enrols it as a Farm Manager.

	IT REFUSES TO OVERFILL A CABIN WHERE THE TOOL ONLY WARNS, and that difference
	is deliberate rather than an oversight in one of them. `create_housing_assignment`
	reports "now holds 5 against a recorded capacity of 4" in `warnings` and writes
	the row, which is right on a console where an operator can see the warning,
	weigh it and mean it — a barracks really does take a fifth bunk some seasons.
	It is wrong on a phone: nothing on the Housing step displays a warning, the
	foreman has already walked away, and a bed that does not exist becomes somebody
	sleeping in a truck. So the count is taken BEFORE the write and the refusal
	names the unit, its capacity and who is already in it.

	`allow_multi_occupancy` IS NOT FORWARDED, and the wizard cannot send it. Under
	capacity this method passes it as true on the caller's behalf — a four-bunk
	cabin with one person in it is the ordinary case and the tool refuses a second
	assignment without the flag — and AT capacity the check above has already
	refused. Letting a phone send the flag itself would hand it the one argument
	that turns the capacity refusal off.
	"""
	allowed = guard.require_scope(user)
	personnel.require_hr_role()
	compat.require_doctype(
		"Housing Assignment",
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)

	person = guard.require_scoped_doc(EMPLOYEE, employee, "employee", allowed)
	unit = guard.require_docname(HOUSING_UNIT, housing_unit, "housing_unit")
	start = str(check_in_date or "").strip()
	if not start:
		frappe.throw(
			"check_in_date is required — an assignment with no start date is not a record.",
			frappe.ValidationError,
		)
	finish = str(end_date or "").strip()

	# The unit is scoped by its OWNING ENTITY, which is what a Housing Unit calls
	# its company. `require_scoped_doc` reads a field named `company` and there is
	# not one, so the check is made here rather than skipped — a cabin belonging
	# to an entity this caller cannot reach is not found, the same refusal as a
	# docname that does not exist.
	unit_row = frappe.db.get_value(HOUSING_UNIT, unit, ["owning_entity", "capacity"], as_dict=True) or {}
	owner = str(unit_row.get("owning_entity") or "")
	if owner and owner not in set(allowed):
		frappe.throw(f"housing_unit {unit} was not found.", frappe.DoesNotExistError)

	capacity = int(unit_row.get("capacity") or 0)
	occupied = housing_tools.occupancy_for(unit, start, finish or None)
	if capacity and len(occupied) >= capacity:
		frappe.throw(
			f"{unit} holds {capacity} and already has {len(occupied)} assigned over these dates. "
			"Pick another unit, or end an assignment that has actually ended — "
			"list_available_housing shows what has a bed free. Nothing was created.",
			frappe.ValidationError,
		)

	inner = {
		"unit": unit,
		"employee": person,
		"assigned_date": start,
		# Under capacity by the check above, so a shared cabin is the ordinary
		# case rather than the exception this flag was written for. See the
		# docstring: the phone cannot send it, and this is not it being forwarded.
		"allow_multi_occupancy": True,
	}
	for key, value in (
		("end_date", finish),
		("deposit_paid", deposit_paid),
		("housing_deduction_from_wages", housing_deduction_from_wages),
		("notes", notes),
	):
		if value not in (None, ""):
			inner[key] = value

	data = housing_tools.create_housing_assignment(inner).data
	return {
		"assignment": data.get("name"),
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		"unit": data.get("unit"),
		"parcel": data.get("parcel"),
		"company": owner or None,
		"check_in_date": data.get("assigned_date"),
		"end_date": data.get("end_date"),
		"status": data.get("status"),
		"unit_capacity": data.get("unit_capacity"),
		"current_occupants": data.get("occupants_after"),
		"open_beds": max(0, capacity - int(data.get("occupants_after") or 0)) if capacity else None,
		"housing_deduction_from_wages": data.get("housing_deduction_from_wages"),
		"deposit_paid": data.get("deposit_paid"),
		"warnings": data.get("warnings") or [],
		"section_119_note": data.get("section_119_note"),
	}


# ── 46. collect_signature ───────────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("collect_signature", mutating=True, limit=guard.UPLOAD_LIMIT)
def collect_signature(
	user: str,
	doctype=None,
	docname=None,
	field=None,
	signature_base64=None,
	file_token=None,
	row=None,
	task=None,
	overwrite=None,
) -> dict:
	"""Attach a signature capture to the I-9 or W-4 box a task said was missing.

	THE CALL THE HIRING BOARD'S SIGNATURE TASKS EXIST FOR. A Farm Task raised by
	`i9_section_2_unsigned` carries `subject_doctype` and `subject_docname` —
	which form needs signing — and the app opens a signature pad over them. This
	is what the pad calls when the finger lifts.

	IT TAKES BYTES, WHICH `upload_signed_i9` DOES NOT, and the two are answering
	different questions rather than disagreeing. That one files a PHOTOGRAPH OF A
	SIGNED PAGE: megabytes, taken on a camera, and it goes up through
	`stage_file_chunk` / `finalize_staged_file` because a link that drops halfway
	through eight megabytes has to be resumable. This one carries what a finger
	drew on the glass: a few kilobytes of monochrome PNG, complete in one
	gesture, and chunking it would be three round trips to move less data than
	the JSON around it — three more places for a signature to be lost while the
	person who drew it walks back to the block. `tools/signatures.py` holds the
	512 KB ceiling that keeps the two apart, and something over it is told which
	door to use. `file_token` is still accepted for a caller that has one.

	THE HR ROLE IS REQUIRED WITH NO EXCEPTION, exactly as `upload_signed_i9`
	requires it and for the same reason: this writes the document the employer is
	inspected on. A worker may READ their own I-9 through `get_i9_form` because
	reading it harms nobody; an account that could put a signature on its own
	Section 2 could attest that somebody examined documents when nobody did.

	EVERY OTHER REFUSAL IS THE TOOL'S and is not copied here — the closed list of
	signable boxes, the authorized-signer roster on the two EMPLOYER boxes, the
	refusal to overwrite an attestation, the destroyed I-9. A second copy of
	those would be a second set of federal-form rules to keep in step.

	`worker_id` IS NOT AN ARGUMENT. Which task gets closed is worked out from the
	form and the alert type, and closing it goes through `complete_farm_task`,
	which refuses a completion from an account that is not holding the task. An
	account that could name somebody else here would be closing another person's
	task in their name.
	"""
	guard.require_scope(user)
	personnel.require_hr_role()

	if not str(doctype or "").strip():
		frappe.throw(
			"doctype is required — 'I-9 Form' or 'W-4 Form'. The task you opened this from "
			"carries it in subject_doctype.",
			frappe.ValidationError,
		)
	if not str(docname or "").strip():
		frappe.throw(
			"docname is required — the form being signed. The task carries it in "
			"subject_docname.",
			frappe.ValidationError,
		)
	if not (str(signature_base64 or "").strip() or str(file_token or "").strip()):
		frappe.throw(
			"the signature image is required: signature_base64 for a capture taken on the "
			"pad, or file_token for one already uploaded.",
			frappe.ValidationError,
		)

	inner = {"doctype": doctype, "name": docname}
	for key, value in (
		("field", field),
		("signature_base64", signature_base64),
		("file_token", file_token),
		("row", row),
		("task", task),
		("overwrite", overwrite),
	):
		if value not in (None, ""):
			inner[key] = value

	data = signatures.collect_form_signature(inner).data
	return {
		"doctype": data.get("doctype"),
		"name": data.get("name"),
		"field": data.get("field"),
		"label": data.get("label"),
		"row": data.get("row"),
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		"signature": data.get("signature"),
		"signed_at": data.get("signed_at"),
		"replaced": data.get("replaced"),
		# BOTH REPORTED, NEITHER FATAL. The app shows a completed signature and,
		# where the task did not close, why — usually because somebody else is
		# holding it, which is a thing the person at the pad needs to be told
		# rather than a failure of what they just did.
		"task": (data.get("task") or {}).get("task"),
		"task_completed": bool((data.get("task") or {}).get("completed")),
		"task_note": (data.get("task") or {}).get("note"),
		"pdf_regenerated": bool((data.get("pdf") or {}).get("regenerated")),
	}


# ── 47. submit_form_signature ───────────────────────────────────────────────
@frappe.whitelist(methods=["POST"])
@guard.endpoint("submit_form_signature", mutating=True, limit=guard.UPLOAD_LIMIT)
def submit_form_signature(
	user: str,
	doctype=None,
	docname=None,
	signature_field=None,
	signature_image=None,
	signer_role=None,
	printed_name=None,
	employee=None,
	task=None,
	task_assignment=None,
	row=None,
	include_pdf=None,
) -> dict:
	"""The signature pad's own call, in the shape `API_CONTRACT.md` §14.2 posts.

	THE SAME WRITE AS `collect_signature`, WITH THE APP'S OWN ARGUMENT NAMES AND
	THE APP'S OWN ANSWER. v0.55.0 published this work as `collect_signature`,
	which takes `field` and `signature_base64`; the client was written against
	§14.2, which sends `signature_field` and `signature_image` — and since
	`farmops_api/routes.bind` reduces a body to the keys the signature declares,
	a pad posting the contract's spelling at the old method lost the field name
	and the image on the way in and was told the signature was missing. Both
	methods stay: an older handset keeps the route it knows, and neither
	signature grows a second spelling of an argument.

	WHAT IS NEW BESIDES THE NAMES IS THE ANSWER, and it is what makes the
	compliance calendar a place work can be finished rather than read:

	  * `form_status` — what the form says now, so the screen can report "the
	    form now reads Complete" instead of "done";
	  * `dismissed_alert` — the alert this signature ANSWERED. Not a claim that
	    anything was dismissed: nothing here dismisses an alert, the sweep does
	    that by looking at the record again and finding the box filled. What the
	    phone needs is the name of the row it should take off the tab it was
	    tapped from, and only the server knows which row that is;
	  * `already_signed` — see below. It is the difference between a retry and a
	    second signature.

	IT IS IDEMPOTENT, WHICH §14.4 ASKS FOR BY NAME. A submission whose answer
	never made it back over a marginal link gets retried, and a worker who has
	already signed being shown an error is a worker who signs again — so a box
	that already carries an attestation answers success with
	`already_signed: true` and NOTHING IS OVERWRITTEN. Replacing an attestation
	somebody made under penalty of perjury is a deliberate act with an
	`overwrite` flag on it, and that flag is not reachable from here.

	`task` TRAVELS AND CLOSES THE WORK IN THE SAME TRANSACTION. The alert the
	phone tapped carries the Farm Task the sweep raised, and routing around the
	task list must not route around closing the task. `task_assignment` is
	accepted for the reason §6's is — one task can carry several assignments and
	closing it without naming the unit of work leaves the wrong one open — and is
	forwarded to the same completion path, which still refuses a completion filed
	by somebody who was not holding the work. That refusal is REPORTED rather
	than fatal: the signature is the compliance artefact and the task is
	bookkeeping about it.

	`signed_on`, `image_format`, `gps_lat` AND `gps_lon` ARE DROPPED, and by the
	documented mechanism rather than by accident — `routes.bind` keeps only the
	keys this signature declares, exactly as it drops `pdf_source`. The
	timestamp is the interesting one: §14.2 stamps it when the pad opened, and
	the column beside the image is the 8 CFR § 274a.2(h) record of when the
	attestation was made. A handset that could set it could backdate it, so the
	server stamps its own and answers with what it wrote — every key here is
	optional to the client, which reads the server's word for the record.

	`include_pdf` HANDS BACK THE PAGE THAT WAS JUST SIGNED, AND DEFAULTS ON.
	v0.57.1. The signed form is the artefact the whole flow exists to produce and
	the person who drew the signature could not see it: `file_url` names a
	private File, and this app authenticates to THIS door with `X-FarmOps-Token`
	rather than to Frappe, so a private URL is a login page to it — the same
	reason `get_employee_badge_pass` puts its `.pkpass` in the answer. So the
	PDF travels as base64 beside the URL, and `render_i9_pdf` stamps the capture
	into the page content and flattens it, which means what comes back is the
	page WITH the signature on it rather than the blank-box copy.

	IT RENDERS ONE WHERE NONE EXISTED, which `collect_form_signature` will not do
	on its own — see `_redraw`, which argues at length that drawing a federal
	form nobody asked for is this app deciding something that is not its to
	decide. A caller passing this HAS asked, by name, in the same call. Turning
	it off is supported for a client that only wants the write.

	NOT FATAL, EVER. The renderers need `pypdf` and the blank federal form on
	disk; a site missing either gets `pdf.available: false` with the reason in
	`pdf.note` and a signature that is on the record regardless. A page is worth
	less than the attestation it depicts, and a call that threw away the second
	to avoid reporting the loss of the first would have the trade backwards.

	THE HR ROLE IS REQUIRED WITH NO EXCEPTION, as on `collect_signature`: this
	writes the document the employer is inspected on. Every other refusal is the
	tool's — the closed list of signable boxes, the authorized-signer roster on
	the two employer boxes, the destroyed I-9 — and is not copied here.
	"""
	guard.require_scope(user)
	personnel.require_hr_role()

	if not str(doctype or "").strip():
		frappe.throw(
			"doctype is required — the form the signature goes on, e.g. 'I-9 Form'. The alert or "
			"task you opened the pad from carries it in signature_request.doctype.",
			frappe.ValidationError,
		)
	if not str(docname or "").strip():
		frappe.throw(
			"docname is required — the record being signed. It is signature_request.docname on "
			"the alert or task the pad was opened from.",
			frappe.ValidationError,
		)
	if not str(signature_image or "").strip():
		frappe.throw(
			"signature_image is required: the capture as bare base64, no data: preamble.",
			frappe.ValidationError,
		)

	wants_pdf = _as_flag(include_pdf, default=True)
	inner = {
		"doctype": doctype,
		"name": docname,
		"signature_base64": signature_image,
		"render_pdf": wants_pdf,
	}
	for key, value in (("field", signature_field), ("row", row), ("task", task)):
		if value not in (None, ""):
			inner[key] = value

	try:
		data = signatures.collect_form_signature(inner).data
	except signatures.AlreadySignedError:
		return _already_signed(doctype, docname, signature_field, task, wants_pdf)

	closed = data.get("task") or {}
	return {
		"doctype": data.get("doctype"),
		"docname": data.get("name"),
		# `field` RATHER THAN `signature_field` ON THE WAY BACK, because that is
		# what §14.3 answers with. The request and the response spell it
		# differently in the contract and both spellings are the app's.
		"field": data.get("field"),
		"form_status": _form_status(data.get("doctype"), data.get("name")),
		"file_url": data.get("signature"),
		"task": closed.get("task"),
		"task_state": _task_state(closed.get("task")),
		"task_completed": bool(closed.get("completed")),
		"task_note": closed.get("note"),
		"signed_on": data.get("signed_at"),
		"already_signed": False,
		"dismissed_alert": _alert_answered(data.get("doctype"), data.get("field"), data.get("name")),
		"employee": data.get("employee"),
		"employee_name": data.get("employee_name"),
		# See the docstring. The page carries the capture stamped in, and the
		# bytes travel because a private File is a login page to this caller.
		"pdf": _signed_pdf(data.get("pdf") or {}) if wants_pdf else None,
	}


def _signed_pdf(redrawn: dict) -> dict:
	"""The rendered page as something a handset can open, or why it has none.

	ALWAYS A DICT AND NEVER A RAISE. `_redraw` has already swallowed whatever the
	renderer did and reported it in `note`; this reads the File it named and can
	fail on its own — a File row written in a transaction that has not committed,
	a site whose private files directory moved. Both end the same way: the
	signature is on the record, and the phone is told there is no page rather
	than shown a failure for a write that succeeded.

	`available` IS THE KEY TO BRANCH ON, not the presence of `base64`. A page
	that rendered and could not be read back is a different problem from a site
	with no `pypdf`, and both are `available: false` with the reason in `note`.
	"""
	out = {
		"available": False,
		"regenerated": bool(redrawn.get("regenerated")),
		"file_url": redrawn.get("file_url"),
		"file_name": redrawn.get("file_name"),
		"content_type": "application/pdf",
		"base64": None,
		"bytes": None,
		"replaced": redrawn.get("replaced"),
		"note": redrawn.get("note"),
	}
	url = str(redrawn.get("file_url") or "").strip()
	if not url:
		return out
	out["file_name"] = out["file_name"] or url.rsplit("/", 1)[-1] or None
	try:
		docname = str(frappe.db.get_value("File", {"file_url": url}, "name") or "")
		content = file_tools.read_file_bytes(docname) if docname else b""
	except Exception as exc:  # pragma: no cover - see the docstring
		out["note"] = f"the page was rendered at {url} and could not be read back ({exc})."
		return out
	if not content:
		out["note"] = f"the page at {url} read back empty."
		return out
	out.update(
		{
			"available": True,
			"base64": base64.b64encode(content).decode("ascii"),
			"bytes": len(content),
		}
	)
	return out


def _as_flag(value, default: bool) -> bool:
	"""One optional boolean off a JSON body, tolerating the four ways it arrives.

	A phone sends `true`; a form post sends `"true"`; an older client sends `1`;
	and an absent key means the default rather than false. `args.as_bool` reads
	from a dict and these are already bound parameters, so the same tolerance is
	spelled out here rather than round-tripped through one.
	"""
	if value is None or value == "":
		return default
	if isinstance(value, bool):
		return value
	return str(value).strip().lower() in ("1", "true", "yes", "on")


def _already_signed(doctype, docname, field, task, wants_pdf: bool = False) -> dict:
	"""The §14.3 answer for a box that was already signed. A SUCCESS, not a miss.

	Answers with what is on the record rather than with what this call would have
	written, because nothing was written. The task is reported in whatever state
	it is actually in and is NOT closed from here: if the first attempt landed it
	closed the task then, and if somebody signed this box in the Desk instead
	then the task is theirs to close from the account holding it.

	THE PDF IS READ AND NOT DRAWN, for exactly that reason. A retry whose first
	attempt landed wants the same page back — the worker is standing there and
	the point of the answer is to show them what they signed — but rendering one
	here would make the idempotent path write, which is the one thing this
	branch exists not to do. So it hands back whatever page is already on the
	record, and a form nobody has rendered reports no page rather than growing
	one on a retry.
	"""
	resolved = str(doctype or "").strip()
	name = str(docname or "").strip()
	return {
		"doctype": resolved,
		"docname": name,
		"field": str(field or "").strip() or None,
		"form_status": _form_status(resolved, name),
		"task": str(task or "").strip() or None,
		"task_state": _task_state(task),
		"task_completed": None,
		"already_signed": True,
		"dismissed_alert": _alert_answered(resolved, field, name),
		"pdf": _existing_pdf(resolved, name) if wants_pdf else None,
		"note": (
			"This box already carried a signature and nothing was changed. An attestation is "
			"replaced deliberately or not at all."
		),
	}


def _existing_pdf(doctype, docname) -> dict:
	"""The page already on the record, read back. Never renders and never raises."""
	handler = signatures.FORM_HANDLERS.get(str(doctype or "").strip()) or {}
	field = handler.get("pdf_field")
	if not (field and docname):
		return _signed_pdf({})
	try:
		url = str(frappe.db.get_value(doctype, docname, field) or "").strip()
	except Exception:  # pragma: no cover - a site whose column is not migrated
		url = ""
	if not url:
		return _signed_pdf(
			{
				"note": (
					f"no page has been rendered for this form. {handler.get('renderer')} draws "
					f"one, with the signature stamped in."
				)
			}
		)
	return _signed_pdf({"regenerated": False, "file_url": url})


def _form_status(doctype, docname) -> str | None:
	"""What the form says about itself now. None where the doctype has no status."""
	name = str(docname or "").strip()
	resolved = str(doctype or "").strip()
	if not (name and resolved) or not compat.has_field(resolved, "status"):
		return None
	try:
		return str(frappe.db.get_value(resolved, name, "status") or "") or None
	except Exception:  # pragma: no cover - a record deleted between write and read
		return None


def _task_state(task) -> str | None:
	name = str(task or "").strip()
	if not name or not compat.doctype_exists(FARM_TASK):
		return None
	try:
		return str(frappe.db.get_value(FARM_TASK, name, "state") or "") or None
	except Exception:  # pragma: no cover
		return None


def _alert_answered(doctype, field, docname) -> str | None:
	"""The alert a filled box makes untrue, by key. Never raises — see the tool."""
	box = signatures.BOXES_BY_KEY.get(f"{str(doctype or '').strip()}.{str(field or '').strip()}")
	if box is None:
		# The field was resolved by the tool from a doctype with one box, or this
		# is the already-signed path where the client may have named none.
		candidates = [
			entry for entry in signatures.SIGNATURE_BOXES if entry.doctype == str(doctype or "").strip()
		]
		if len(candidates) != 1:
			return None
		box = candidates[0]
	return signatures.alert_answered_by(box, str(docname or "").strip()) or None
