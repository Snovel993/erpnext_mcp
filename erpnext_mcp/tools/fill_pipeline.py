# SPDX-License-Identifier: MIT
"""Container-Agnostic Fill Pipeline — v0.68.0. Six tools, two of them write.

WHAT THIS CONNECTS. `bucket_log.py`'s `sync_bucket_entries` has taken a
segmentation model's `coverage_percent` since v0.44.0 and, as of this release,
its raw `mask_area_px` / `container_area_px` too — the pixel counts the model
actually measured. This module is where that number meets a THRESHOLD a
foreman set, for a container type that can be anything a device and a foreman
have agreed to call one: `get_fill_determination` explains the arithmetic for
one capture, `get_fill_thresholds`/`update_fill_threshold` are the band a
foreman controls, and the last three tools are the loop that makes a threshold
CHANGE something a checker in the field is known to have seen.

NOT PAY. `bucket_bridge.py`'s binary gate — Accepted is one bucket, Rejected is
none, `coverage_percent` never scales it — is unchanged by this release. A fill
determination is quality-control information a foreman or checker reads to
decide whether to trust or override the on-device verdict; nothing in this
module writes to `verdict` or to anything payroll reads.

CONTAINER-AGNOSTIC MEANS THE CODE DOES NOT NAME CONTAINER TYPES. `cherry_bucket`
and `pear_bin` are examples, not a closed vocabulary — `container_type` is a
free-text key on both Bucket Log Entry and Container Fill Threshold, and
whether a type can overfill is a property of whether its threshold ROW carries
an `upper_bound_pct`, never a hardcoded name check. A cherry bucket has no
upper bound because no caller has ever set one for it, not because this module
special-cases the string `"cherry_bucket"`.

THE ARITHMETIC IS NOT HERE. `bucket_bridge.fill_determination` is pure and does
the fill-percentage/threshold comparison; this module is the only place that
reads or writes a Container Fill Threshold, Fill Threshold Change Log or
Fill Threshold Acknowledgment document.

────────────────────────────────────────────────────────────────────────────
A THRESHOLD UPDATE IS A FULL DEFINITION, NOT A PATCH
────────────────────────────────────────────────────────────────────────────

`update_fill_threshold` sets `lower_bound_pct` and `upper_bound_pct` to exactly
what the call carries — omitting `upper_bound_pct` CLEARS it, every time. That
is what "cherry bucket: only lower_bound_pct" means in practice: nobody ever
sends an upper bound for one, so every update leaves it null. A caller changing
only the lower bound on a container type that HAS an upper bound must resend
both.

────────────────────────────────────────────────────────────────────────────
WHO MAY WRITE
────────────────────────────────────────────────────────────────────────────

`update_fill_threshold` moves the band a checker's phone enforces in the field,
so it gates through `require_foreman_role` — Foreman or above, explicitly NOT
Checker, because there is no "Checker" role in this app (see `roles.py`'s six)
and gating on an ordinary personnel role would let anybody holding it move the
number a checker is being asked to trust. `acknowledge_threshold_update` is not
role-gated beyond naming a real, active Employee — a checker acknowledging what
they were shown is exactly the low-privilege write this loop exists to allow.

WHO A "CHECKER" IS. This app has no Checker role or flag on Employee — the
population `list_pending_threshold_acknowledgments` checks against is every
Active Employee whose `designation` is `Checker`, the same Link-to-Designation
field `Position Wage Default` already reads. A site names that designation once
in the Desk; nothing here creates it.
"""

from __future__ import annotations

import frappe

from .. import bucket_bridge, compat, roles, security
from ..args import as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult

THRESHOLD_DOCTYPE = "Container Fill Threshold"
CHANGE_LOG_DOCTYPE = "Fill Threshold Change Log"
ACKNOWLEDGMENT_DOCTYPE = "Fill Threshold Acknowledgment"
ENTRY_DOCTYPE = "Bucket Log Entry"
SESSION_DOCTYPE = "Bucket Log Session"

_MIGRATE_HINT = "It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app."

#: "Foreman or higher" — the same list `dispatch.py`'s Critical-urgency gate
#: uses, for the same reason: a threshold a checker enforces in the field is a
#: dispatch-weight judgement, not an ordinary personnel action. `Checker` is
#: deliberately absent — there is no such role, so this excludes it by
#: construction rather than by name.
FOREMAN_ROLES = ("System Manager", "Farm Manager", "Foreman")

#: The Employee.designation value `list_pending_threshold_acknowledgments`
#: filters on. See the module docstring's "WHO A CHECKER IS".
CHECKER_DESIGNATION = "Checker"

_THRESHOLD_FIELDS = (
	"name",
	"company",
	"container_type",
	"lower_bound_pct",
	"upper_bound_pct",
	"version",
	"last_updated_by",
	"last_updated_at",
)

_ENTRY_FIELDS = (
	"name",
	"entry_uuid",
	"session_uuid",
	"company",
	"container_type",
	"mask_area_px",
	"container_area_px",
	"coverage_percent",
	"verdict",
)


def _require(doctype: str) -> None:
	compat.require_doctype(doctype, _MIGRATE_HINT)


def require_foreman_role() -> str:
	"""The principal this call is attributed to, once it has proved it may move
	a fill threshold. Same identity resolution as `kpi.require_kpi_role` and
	`employee.require_hr_role` — whichever of the request's authenticated
	caller and the session user is present — against `FOREMAN_ROLES`."""
	actor = security.caller_identity() or str(getattr(frappe.session, "user", "") or "")
	if not actor or actor == "Guest":
		raise ToolError(
			"this call has no identity to attribute a threshold change to. update_fill_threshold "
			"moves the band a checker's phone enforces in the field, so who moved it is part of "
			"the record. Nothing was changed."
		)
	held = set(frappe.get_roles(actor) or []) or set(roles.all_roles_of(actor) or [])
	if not held & set(FOREMAN_ROLES):
		raise ToolError(
			f"{actor} may not change a fill threshold: it holds none of {', '.join(FOREMAN_ROLES)}. "
			"A checker acknowledges a threshold with acknowledge_threshold_update; only a foreman "
			"or above sets one. Nothing was changed."
		)
	return actor


def _describe_threshold(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"company": row.get("company"),
		"container_type": row.get("container_type"),
		"lower_bound_pct": row.get("lower_bound_pct"),
		"upper_bound_pct": row.get("upper_bound_pct"),
		"version": row.get("version"),
		"last_updated_by": row.get("last_updated_by") or None,
		"last_updated_at": str(row.get("last_updated_at") or "") or None,
	}


def _current_threshold(company: str, container_type: str) -> dict | None:
	row = frappe.db.get_value(
		THRESHOLD_DOCTYPE,
		{"company": company, "container_type": container_type},
		list(_THRESHOLD_FIELDS),
		as_dict=True,
	)
	return dict(row) if row else None


def _resolve_entry(entry: str) -> dict:
	name = (
		entry
		if frappe.db.exists(ENTRY_DOCTYPE, entry)
		else frappe.db.get_value(ENTRY_DOCTYPE, {"entry_uuid": entry}, "name")
	)
	if not name:
		raise ToolError(f"no Bucket Log Entry matching {entry!r} on this site.")
	row = frappe.db.get_value(ENTRY_DOCTYPE, name, list(_ENTRY_FIELDS), as_dict=True)
	return dict(row)


def _resolve_session_uuid(session: str) -> str:
	name = (
		session
		if frappe.db.exists(SESSION_DOCTYPE, session)
		else frappe.db.get_value(SESSION_DOCTYPE, {"session_uuid": session}, "name")
	)
	if not name:
		raise ToolError(f"no Bucket Log Session matching {session!r} on this site.")
	return frappe.db.get_value(SESSION_DOCTYPE, name, "session_uuid")


def _determination_for(entry_row: dict) -> dict:
	threshold = None
	container_type = entry_row.get("container_type")
	if container_type:
		threshold = _current_threshold(entry_row.get("company"), container_type)
	return bucket_bridge.fill_determination(entry_row, threshold)


# ── 1. get_fill_determination ────────────────────────────────────────────


def get_fill_determination(args: dict) -> ToolResult:
	"""Read-only. The fill determination for one Bucket Log Entry, or for every
	entry in one Bucket Log Session: segmentation mask area, container boundary
	area, the fill percentage computed from them (falling back to the stored
	coverage_percent when areas were never sent), which Container Fill
	Threshold was applied, and the pass/fail result — with the arithmetic
	spelled out in `math_explanation`/`explanation` rather than left for a
	caller to re-derive. Pass `entry` (docname or entry_uuid) for one capture,
	or `session` (docname or session_uuid) for every capture in it."""
	_require(ENTRY_DOCTYPE)
	entry = as_str(args, "entry")
	session = as_str(args, "session")
	if not entry and not session:
		raise ToolError("entry or session is required.")

	if entry:
		row = _resolve_entry(entry)
		determination = _determination_for(row)
		return ToolResult(
			data=determination,
			summary=(
				f"{row.get('entry_uuid') or row.get('name')}: {determination['result']}"
				+ (
					f" ({determination['fill_percentage']:g}%)"
					if determination.get("fill_percentage") is not None
					else ""
				)
			),
		)

	session_uuid = _resolve_session_uuid(session)
	rows = frappe.db.get_all(
		ENTRY_DOCTYPE,
		filters={"session_uuid": session_uuid},
		fields=list(_ENTRY_FIELDS),
		order_by="timestamp asc",
		limit_page_length=0,
	)
	determinations = [_determination_for(dict(row)) for row in rows]
	counts: dict = {}
	for item in determinations:
		counts[item["result"]] = counts.get(item["result"], 0) + 1
	data = {
		"session": session_uuid,
		"count": len(determinations),
		"determinations": determinations,
		"summary_by_result": counts,
	}
	summary = f"{session_uuid}: {len(determinations)} determination(s) — " + ", ".join(
		f"{count} {result}" for result, count in sorted(counts.items())
	)
	return ToolResult(data=data, summary=summary)


# ── 2. get_fill_thresholds ───────────────────────────────────────────────


def get_fill_thresholds(args: dict) -> ToolResult:
	"""Read-only. The current fill threshold for one container type at one
	company: lower_bound_pct, upper_bound_pct (null where the container type
	cannot overfill), version, and who last changed it. A container type with
	no threshold set yet is answered with `configured: false` rather than
	refused — a fresh install has none, and that is a fact worth returning, not
	an error."""
	_require(THRESHOLD_DOCTYPE)
	container_type = as_str(args, "container_type", required=True)
	company = resolve_company(as_str(args, "company"), required=True)
	row = _current_threshold(company, container_type)
	if not row:
		data = {
			"configured": False,
			"company": company,
			"container_type": container_type,
			"lower_bound_pct": None,
			"upper_bound_pct": None,
			"version": None,
			"last_updated_by": None,
			"last_updated_at": None,
		}
		return ToolResult(
			data=data,
			summary=f"no fill threshold set for {container_type!r} at {company} yet",
		)
	data = {"configured": True, **_describe_threshold(row)}
	upper = row.get("upper_bound_pct")
	band = f"{row.get('lower_bound_pct'):g}%–{upper:g}%" if upper not in (None, "") else f"≥{row.get('lower_bound_pct'):g}%"
	return ToolResult(data=data, summary=f"{container_type} at {company}: {band} (v{row.get('version')})")


# ── 3. update_fill_threshold ─────────────────────────────────────────────


def update_fill_threshold(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Set the fill-percentage band for one container
	type at one company. Requires Foreman or above — see the module docstring.

	FULL DEFINITION, NOT A PATCH: omitting upper_bound_pct clears any existing
	one. Pass only lower_bound_pct for a container type that cannot overfill
	(a cherry bucket); pass both for one that can (a pear bin).

	Bumps version, records who/when/old→new on a new Fill Threshold Change Log
	row, and leaves every checker's prior acknowledgment attached to the
	version they acknowledged — list_pending_threshold_acknowledgments will
	report them all pending against the new one until acknowledge_threshold_update
	is called again."""
	_require(THRESHOLD_DOCTYPE)
	actor = require_foreman_role()
	company = resolve_company(as_str(args, "company"), required=True)
	container_type = as_str(args, "container_type", required=True)

	lower_raw = args.get("lower_bound_pct")
	if lower_raw in (None, ""):
		raise ToolError("lower_bound_pct is required. Nothing was changed.")
	try:
		lower = float(lower_raw)
	except (TypeError, ValueError):
		raise ToolError(f"lower_bound_pct must be a number, got {lower_raw!r}. Nothing was changed.") from None
	if lower < 0:
		raise ToolError(f"lower_bound_pct must not be negative, got {lower!r}. Nothing was changed.")

	upper_raw = args.get("upper_bound_pct")
	upper = None
	if upper_raw not in (None, ""):
		try:
			upper = float(upper_raw)
		except (TypeError, ValueError):
			raise ToolError(f"upper_bound_pct must be a number, got {upper_raw!r}. Nothing was changed.") from None
		if upper <= lower:
			raise ToolError(
				f"upper_bound_pct ({upper!r}) must be greater than lower_bound_pct ({lower!r}). "
				"Nothing was changed."
			)

	existing = frappe.db.get_value(
		THRESHOLD_DOCTYPE,
		{"company": company, "container_type": container_type},
		["name", "lower_bound_pct", "upper_bound_pct", "version"],
		as_dict=True,
	)
	old_lower = existing.get("lower_bound_pct") if existing else None
	old_upper = existing.get("upper_bound_pct") if existing else None
	new_version = (int(existing.get("version") or 0) + 1) if existing else 1
	stamp = frappe.utils.now()

	if existing:
		doc = frappe.get_doc(THRESHOLD_DOCTYPE, existing["name"])
	else:
		doc = frappe.new_doc(THRESHOLD_DOCTYPE)
		doc.company = company
		doc.container_type = container_type
	doc.lower_bound_pct = lower
	doc.upper_bound_pct = upper
	doc.version = new_version
	doc.last_updated_by = actor
	doc.last_updated_at = stamp
	if existing:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)

	log = frappe.new_doc(CHANGE_LOG_DOCTYPE)
	log.company = company
	log.container_type = container_type
	log.version = new_version
	log.changed_by = actor
	log.changed_at = stamp
	log.old_lower_bound_pct = old_lower
	log.new_lower_bound_pct = lower
	log.old_upper_bound_pct = old_upper
	log.new_upper_bound_pct = upper
	log.reason = as_str(args, "reason") or None
	log.insert(ignore_permissions=True)

	return ToolResult(
		data={**_describe_threshold(doc.as_dict()), "change_log": log.name},
		summary=(
			f"{container_type} at {company}: lower {old_lower!r} → {lower!r}"
			+ (f", upper {old_upper!r} → {upper!r}" if upper != old_upper else "")
			+ f" (v{new_version}, by {actor})"
		),
		docstatus_delta=("none → 0 (created)" if not existing else f"v{existing.get('version')} → v{new_version}"),
	)


# ── 4. list_fill_threshold_changes ───────────────────────────────────────


def list_fill_threshold_changes(args: dict) -> ToolResult:
	"""Read-only. The audit log of every fill-threshold change: who, when,
	old value, new value, and how many checkers have acknowledged it so far."""
	_require(CHANGE_LOG_DOCTYPE)
	filters: dict = {}
	container_type = as_str(args, "container_type")
	if container_type:
		filters["container_type"] = container_type
	company = as_str(args, "company")
	if company:
		filters["company"] = resolve_company(company)

	rows = frappe.db.get_all(
		CHANGE_LOG_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"company",
			"container_type",
			"version",
			"changed_by",
			"changed_at",
			"old_lower_bound_pct",
			"new_lower_bound_pct",
			"old_upper_bound_pct",
			"new_upper_bound_pct",
			"reason",
		],
		order_by="changed_at desc",
		limit_page_length=0,
	)
	ack_rows = frappe.db.get_all(
		ACKNOWLEDGMENT_DOCTYPE,
		filters={"parenttype": CHANGE_LOG_DOCTYPE, "parent": ("in", [row["name"] for row in rows] or [""])},
		fields=["parent"],
		limit_page_length=0,
	)
	ack_counts: dict = {}
	for row in ack_rows:
		ack_counts[row["parent"]] = ack_counts.get(row["parent"], 0) + 1

	changes = [{**row, "acknowledged_count": ack_counts.get(row["name"], 0)} for row in rows]
	return ToolResult(
		data={"changes": changes, "count": len(changes)},
		summary=f"{len(changes)} fill threshold change(s)",
	)


# ── 5. acknowledge_threshold_update ──────────────────────────────────────


def acknowledge_threshold_update(args: dict) -> ToolResult:
	"""MUTATING (default OFF). A checker acknowledges they have seen and
	understood the CURRENT fill threshold for one container type — records
	their Employee, a timestamp, and the threshold version acknowledged.
	Idempotent: acknowledging a version already acknowledged by the same
	employee writes nothing a second time."""
	_require(CHANGE_LOG_DOCTYPE)
	employee = as_str(args, "employee", required=True)
	if not frappe.db.exists("Employee", employee):
		raise ToolError(f"no Employee {employee!r} on this site. Nothing was changed.")
	company = resolve_company(as_str(args, "company"), required=True)
	container_type = as_str(args, "container_type", required=True)

	latest = frappe.db.get_all(
		CHANGE_LOG_DOCTYPE,
		filters={"company": company, "container_type": container_type},
		fields=["name", "version"],
		order_by="version desc",
		limit_page_length=1,
	)
	if not latest:
		raise ToolError(
			f"no fill threshold has ever been set for {container_type!r} at {company} — there is "
			"nothing to acknowledge. Nothing was changed."
		)
	log_name, version = latest[0]["name"], latest[0]["version"]
	doc = frappe.get_doc(CHANGE_LOG_DOCTYPE, log_name)
	if any(row.get("employee") == employee for row in doc.get("acknowledgments") or []):
		return ToolResult(
			data={
				"employee": employee,
				"container_type": container_type,
				"company": company,
				"version": version,
				"already_acknowledged": True,
			},
			summary=f"{employee} had already acknowledged {container_type} v{version} — no change",
		)

	employee_name = frappe.db.get_value("Employee", employee, "employee_name")
	stamp = frappe.utils.now()
	doc.append(
		"acknowledgments",
		{"employee": employee, "employee_name": employee_name, "acknowledged_at": stamp},
	)
	doc.save(ignore_permissions=True)

	return ToolResult(
		data={
			"employee": employee,
			"employee_name": employee_name,
			"container_type": container_type,
			"company": company,
			"version": version,
			"acknowledged_at": stamp,
			"already_acknowledged": False,
		},
		summary=f"{employee_name or employee} acknowledged {container_type} v{version}",
		docstatus_delta=f"unacknowledged → acknowledged (v{version})",
	)


# ── 6. list_pending_threshold_acknowledgments ────────────────────────────


def list_pending_threshold_acknowledgments(args: dict) -> ToolResult:
	"""Read-only. Which active Checkers have not yet acknowledged the CURRENT
	fill threshold for one container type at one company. The population is
	every Active Employee whose designation is Checker — see the module
	docstring's "WHO A CHECKER IS"."""
	_require(CHANGE_LOG_DOCTYPE)
	company = resolve_company(as_str(args, "company"), required=True)
	container_type = as_str(args, "container_type", required=True)

	latest = frappe.db.get_all(
		CHANGE_LOG_DOCTYPE,
		filters={"company": company, "container_type": container_type},
		fields=["name", "version"],
		order_by="version desc",
		limit_page_length=1,
	)
	if not latest:
		return ToolResult(
			data={
				"configured": False,
				"company": company,
				"container_type": container_type,
				"current_version": None,
				"checkers_total": 0,
				"acknowledged_count": 0,
				"pending": [],
			},
			summary=f"no fill threshold change is on record for {container_type!r} at {company}",
		)
	log_name, version = latest[0]["name"], latest[0]["version"]

	acknowledged = {
		row["employee"]
		for row in frappe.db.get_all(
			ACKNOWLEDGMENT_DOCTYPE,
			filters={"parenttype": CHANGE_LOG_DOCTYPE, "parent": log_name},
			fields=["employee"],
			limit_page_length=0,
		)
	}
	checkers = frappe.db.get_all(
		"Employee",
		filters={"designation": CHECKER_DESIGNATION, "status": "Active", "company": company},
		fields=["name", "employee_name"],
		order_by="employee_name asc",
		limit_page_length=0,
	)
	pending = [row for row in checkers if row["name"] not in acknowledged]

	data = {
		"configured": True,
		"company": company,
		"container_type": container_type,
		"current_version": version,
		"checkers_total": len(checkers),
		"acknowledged_count": len(checkers) - len(pending),
		"pending": pending,
	}
	return ToolResult(
		data=data,
		summary=(
			f"{container_type} v{version} at {company}: {len(pending)}/{len(checkers)} checker(s) "
			"still pending"
		),
	)
