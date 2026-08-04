# SPDX-License-Identifier: MIT
"""Turning what the tools return into what `FarmOpsKit` decodes.

THE TRANSLATION LIVES HERE AND NOT IN EITHER SIDE, and that is the whole point
of the file. The MCP tools answer an operator's question ("describe this task
for an audit record"); the app asks a worker's ("what am I holding, and what
does it still need"). Both shapes are right for their reader, so v0.17.1 adds a
translator rather than bending one reader's payload towards the other.

WHAT THE APP DECODES IS THE SPEC. `fafo_ios/API_CONTRACT.md` and the structs in
`FarmOpsKit/Sources/FarmOpsKit/Models/` are the contract, and where Wave A's
payload and Wave B's decoder disagreed the BACKEND moved — every gap below is
closed by emitting the field the app already expects, because the alternative is
shipping a new build to every phone in the valley to rename a key.

THE APP IS FORGIVING AND THIS STILL MATTERS. `API_CONTRACT.md` §"Client
tolerance" says booleans may be `1`/`"1"`/`true`, numbers may be strings, lists
may be bare or wrapped, and unknown enum values degrade to "unknown" rather than
failing the row. So a mismatch here is usually a blank field on a screen rather
than a crash — which is worse, not better: an inspection whose "Why this task
exists" card is silently missing looks like an inspection nobody could justify.

THE FIVE GAPS THIS FILE CLOSES, all of them found by reading the Swift:

  * `location_type`. The doctype field is `location_doctype`; the app decodes
    `location_type`. BOTH are emitted — the app gets its key, and an MCP client
    reading the same payload keeps the name the doctype uses.
  * `latitude` / `longitude`. No Farm Task carries coordinates. `Field` and
    `Irrigation Zone` carry a boundary centroid, so a task located on one gets a
    real pin; a Housing Unit has none and the keys are OMITTED rather than
    zeroed. `0, 0` is a real place in the Gulf of Guinea and a map that puts a
    cabin there is worse than a map with no pin.
  * `source_alert_explanation`. The app renders it verbatim under "Why this task
    exists" and HIDES THE CARD if it is absent — its contract says an inspection
    with no stated reason is worse than none. Only the server knows which rule
    fired, so it comes from the Compliance Alert's own message.
  * `assignment` / `claimed_at` / `started_at` on the TASK. They live on the
    Farm Task Assignment; the app's `FarmTask` carries them because a phone
    renders one card, not a join.
  * `companies` as objects, `default_company`, `roles`, `skills` on the user
    context. `get_current_user_context` answers with `entity_access` (strings),
    `preferred_company` and `mobile_roles`; the app decodes `companies`
    (`{name, abbr}`), `default_company`, `roles` and `skills`.
"""

from __future__ import annotations

import frappe

from .. import compat
from .. import roles as role_lib

ALERT = "Compliance Alert"
FARM_TASK = "Farm Task"
EMPLOYEE = "Employee"

#: Where a site might keep a worker's skills. None is created by this app — see
#: `tools/fieldwork.py`, which argues at length against inventing a register.
#: The app's `UserContext.skills` is a display field, so an empty list is a
#: truthful answer and a guessed one would not be.
_SKILL_FIELDS = ("farm_skills", "skills", "custom_farm_skills")

#: Doctypes that can answer "where is this, in degrees". A boundary centroid is
#: not the cabin door, but it is the right block, which is what a worker driving
#: to it needs.
_CENTROID = ("boundary_centroid_lat", "boundary_centroid_lon")

#: Compliance Alert severity → the app's `TaskUrgency`. The app has four levels
#: and the alert register has three, so Warning maps to High: an overdue
#: habitability inspection is not a "normal" item on a phone, and rounding it
#: down would sort it below routine work.
_SEVERITY_TO_URGENCY = {"Critical": "Critical", "Warning": "High", "Info": "Normal"}


# ── tasks ───────────────────────────────────────────────────────────────────
def task(row: dict, assignment: dict | None = None) -> dict:
	"""One Farm Task in the shape `FarmOpsKit.FarmTask` decodes."""
	row = dict(row or {})
	live = dict(assignment or {})
	out = {
		"name": row.get("name"),
		"task_name": row.get("task_name") or row.get("name"),
		"task_type": row.get("task_type") or "Other",
		"state": row.get("state") or "Draft",
		"urgency": row.get("urgency") or "Normal",
		"dispatch_mode": row.get("dispatch_mode") or "Either",
		"estimated_duration_minutes": row.get("estimated_duration_minutes"),
		"skill_required": row.get("skill_required"),
		"notes": row.get("notes"),
		"company": row.get("company"),
		"location": row.get("location"),
		# Both spellings, deliberately. See the module docstring.
		"location_type": row.get("location_doctype") or row.get("location_type"),
		"location_doctype": row.get("location_doctype"),
		"evidence_required": row.get("evidence_required") or {},
		"creates_record": row.get("creates_record"),
		"produced_record": row.get("produced_record"),
		"source_alert": row.get("source_alert"),
		"assignment": live.get("name") or row.get("assignment"),
		"assigned_to": live.get("assigned_to") or row.get("assigned_to"),
		"assigned_to_name": live.get("assigned_to_name") or row.get("assigned_to_name"),
		"claimed_at": live.get("claimed_at"),
		"started_at": live.get("started_at"),
	}

	explanation = alert_explanation(row.get("source_alert"))
	if explanation:
		out["source_alert_explanation"] = explanation

	latitude, longitude = coordinates(row.get("location_doctype"), row.get("location"))
	if latitude is not None and longitude is not None:
		out["latitude"] = latitude
		out["longitude"] = longitude
	return out


def tasks(rows: list, assignments: dict | None = None) -> list:
	"""A list of tasks, each carrying its live assignment where there is one."""
	by_task = assignments or {}
	return [task(row, by_task.get(str(row.get("name")))) for row in rows or [] if isinstance(row, dict)]


def coordinates(location_doctype, location) -> tuple:
	"""Degrees for a task's location, or (None, None) where the site has none.

	Never raises and never guesses. A doctype without a centroid, a document that
	has been deleted, or a boundary nobody has drawn all give the same answer,
	and the caller omits the keys rather than sending a placeholder.
	"""
	doctype = str(location_doctype or "").strip()
	name = str(location or "").strip()
	if not doctype or not name:
		return None, None
	try:
		if not compat.has_field(doctype, _CENTROID[0]):
			return None, None
		row = frappe.db.get_value(doctype, name, list(_CENTROID), as_dict=True) or {}
	except Exception:  # pragma: no cover - a doctype this site cannot read
		return None, None
	try:
		latitude = float(row.get(_CENTROID[0]))
		longitude = float(row.get(_CENTROID[1]))
	except (TypeError, ValueError):
		return None, None
	if latitude == 0 and longitude == 0:
		# Null Island. A boundary that centroids to exactly zero is an empty
		# field, not a farm in the Atlantic.
		return None, None
	return latitude, longitude


def alert_explanation(alert) -> str:
	"""Why this task exists, in the words the rule that raised it used.

	The app renders this VERBATIM and hides its card without it, so this returns
	"" rather than a composed sentence when there is no alert behind the task —
	most work is just work, and "this task exists because somebody made it" is
	not an explanation worth a card.
	"""
	name = str(alert or "").strip()
	if not name or not compat.doctype_exists(ALERT):
		return ""
	try:
		return str(frappe.db.get_value(ALERT, name, "alert_message") or "").strip()
	except Exception:  # pragma: no cover
		return ""


# ── the compliance calendar ─────────────────────────────────────────────────
def alert(row: dict) -> dict:
	"""One calendar row in the shape `ComplianceAlertSummary` decodes."""
	row = dict(row or {})
	return {
		"name": row.get("name"),
		"title": row.get("title") or row.get("alert_type") or row.get("name"),
		"explanation": row.get("message") or row.get("alert_message"),
		"due_date": row.get("due_date"),
		"urgency": _SEVERITY_TO_URGENCY.get(str(row.get("severity") or ""), "Normal"),
		"severity": row.get("severity"),
		"company": row.get("company"),
		"regulation": row.get("framework"),
		"linked_task": linked_task(row.get("name")),
		"overdue": row.get("overdue"),
		"days_until_due": row.get("days_until_due"),
	}


def alerts(rows: list) -> list:
	return [alert(row) for row in rows or [] if isinstance(row, dict)]


def linked_task(alert_name) -> str | None:
	"""The Farm Task raised from this alert, so the calendar can say "task raised".

	Newest first: an alert that was answered, rejected and re-raised has several,
	and the one a worker cares about is the live one.
	"""
	name = str(alert_name or "").strip()
	if not name or not compat.doctype_exists(FARM_TASK):
		return None
	try:
		rows = frappe.db.get_all(
			FARM_TASK,
			filters={"source_alert": name},
			pluck="name",
			order_by="creation desc",
			limit=1,
		)
	except Exception:  # pragma: no cover
		return None
	return rows[0] if rows else None


# ── the user context ────────────────────────────────────────────────────────
def user_context(data: dict, user: str) -> dict:
	"""`get_current_user_context` in the shape `UserContext` decodes.

	`roles` is EVERY role the account holds, not just this app's six. The app
	intersects it with {Compliance Officer, Foreman, Farm Manager, System
	Manager} to decide whether to draw the Compliance tab — and its own contract
	is explicit that this is UI courtesy and not the security boundary, which is
	why `list_compliance_alerts` gates independently. It is the caller's own
	role list either way.
	"""
	data = dict(data or {})
	companies = [name for name in (data.get("entity_access") or []) if name]
	return {
		"user": data.get("user") or user,
		"full_name": data.get("full_name") or user,
		"employee": data.get("employee"),
		"roles": data.get("all_roles") or role_lib.all_roles_of(user),
		"mobile_roles": data.get("mobile_roles") or [],
		"companies": [{"name": name, "abbr": company_abbr(name)} for name in companies],
		"default_company": data.get("preferred_company") or (companies[0] if companies else None),
		"skills": skills_of(data.get("employee")),
		"enabled": data.get("enabled"),
		"grant_state": data.get("grant_state"),
		# The credential's review date is NOT sent. `UserContext` does not decode
		# it, `guard.strip_secrets` would take half of it anyway on the name, and
		# a phone that cannot act on a fact is a phone that should not be told it
		# — the operator reads it in `list_mobile_users`, which is where the
		# person who can re-issue a token is looking.
	}


def company_abbr(name) -> str | None:
	try:
		return str(frappe.db.get_value("Company", name, "abbr") or "") or None
	except Exception:  # pragma: no cover
		return None


def skills_of(employee) -> list:
	"""What this site records about a worker's skills. Usually nothing.

	Returns [] rather than inventing a register or reading a job title — see
	`tools/fieldwork._skill_for`, which makes the argument. The app shows the
	list and filters nothing by it, so an empty one costs a line on a settings
	screen and a guessed one would hide work.
	"""
	name = str(employee or "").strip()
	if not name or not compat.doctype_exists(EMPLOYEE):
		return []
	field = compat.first_field(EMPLOYEE, *_SKILL_FIELDS)
	if not field:
		return []
	try:
		raw = str(frappe.db.get_value(EMPLOYEE, name, field) or "")
	except Exception:  # pragma: no cover
		return []
	return [part.strip() for part in raw.replace("\n", ",").split(",") if part.strip()]


# ── completion ──────────────────────────────────────────────────────────────
def completion(data: dict) -> dict:
	"""`complete_farm_task`'s result in the shape `CompletionResult` decodes.

	`created_record_name` is what closes the loop on the phone — the worker reads
	"Housing Inspection HI-2026-00042 created · compliance alert cleared" instead
	of "Done", and the app's contract asks for it by name for exactly that reason.

	`dismissed_alert` IS NOT A CLAIM THAT AN ALERT WAS DISMISSED. Nothing in this
	app dismisses an alert directly; the record moves the register forward and the
	next sweep notices the condition is no longer true — `complete_farm_task`
	argues that this is the only honest way for an alert to go away. What this
	field names is the alert the work ANSWERED, which is what the phone means when
	it says "cleared", and it is only populated when the completion actually
	produced the record that answers it.
	"""
	data = dict(data or {})
	task_row = dict(data.get("task") or {})
	produced = data.get("produced_record")
	answered = task_row.get("source_alert") if produced else None
	return {
		"task": task_row.get("name"),
		"created_record_doctype": data.get("produced_record_doctype"),
		"created_record_name": produced,
		"dismissed_alert": answered,
		"corrective_action_opened": data.get("produced_record_state") == "Corrective Action Required",
		"final_state": data.get("final_state"),
		"evidence_filed": data.get("evidence_filed"),
		"record_note": data.get("record_note"),
	}
