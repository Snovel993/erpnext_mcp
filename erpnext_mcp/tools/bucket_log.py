# SPDX-License-Identifier: MIT
"""BucketLog → ERPNext Piecework Bridge — v0.44.0. Eight tools, four of them write.

WHAT THIS CLOSES. `payroll_integration.py` has known how to read a `bucket_logs`
row off a shift since v0.35.0 — `_piece_units_for` checks for the key by name,
`_UNIT_KEYS`/`_WORKER_KEYS` already anticipate its shape — and `tools/payroll.py`
has speculatively queried a doctype called `"Bucket Log Entry"` since the same
release. What was missing was the doctype itself, and everything that gets a
capture from an iPhone in an orchard into it: the sync endpoint, the badge →
Employee register a scan resolves through, and the read tools a foreman or a
payroll run asks afterward.

THE ARITHMETIC IS NOT HERE. Validation, badge resolution, session totals and
the `bucket_logs` reshape all live in `erpnext_mcp/bucket_bridge.py`, which is
pure and reads no database — the same split every other engine in this app
keeps. This module is the only place that reads or writes a Bucket Log Entry,
Bucket Log Session or Bucket Log Badge Map document.

────────────────────────────────────────────────────────────────────────────
SYNC IS IDEMPOTENT AND PARTIAL FAILURE DOES NOT LOSE THE BATCH
────────────────────────────────────────────────────────────────────────────

`sync_bucket_entries` deduplicates by `entry_uuid` — a device that never heard
back for a batch it already delivered resends it and gets the same result, not
a duplicate row. An entry that fails `bucket_bridge.validate_bucket_entry` is
reported and skipped rather than failing the call: fifty buckets synced with
one malformed row should keep the other forty-nine, the same posture
`register_model` does not take for ONE record but a batch endpoint has to,
because the alternative is a device retrying the whole batch forever against
the one row it cannot fix by resending.

v0.59.2 EXTENDS THAT TO THE WRITE ITSELF, which is where the promise was
leaking. Only `UniqueValidationError` was caught at `doc.insert()`, so an entry
this app's own checks approved and the DATABASE refused — an ISO 8601 timestamp
a `DATETIME` column will not take was the case that surfaced it — left the loop
and came back as a 500 with no per-entry detail, and the device retrying the
whole batch forever is exactly what happened. Every other insert failure now
lands in `invalid[]` alongside the validation ones, behind a savepoint so
skipping one capture does not take the transaction with it.

────────────────────────────────────────────────────────────────────────────
WHO MAY WRITE
────────────────────────────────────────────────────────────────────────────

`sync_bucket_entries` and `link_entries_to_shift` move data that lands on a
payroll slip, so they gate through `kpi_tools.require_kpi_role` — the same
System Manager / Accounts Manager / Farm Manager list `ml_model.py` and
`budget.py` use, for the same reason: which captures become paid units is the
same class of judgement. `link_badge_to_employee` is identity administration —
whose badge this is — and gates through `employee.require_hr_role` instead,
the list `link_employee_to_user` already uses for the same kind of fact.
"""

from __future__ import annotations

import json

import frappe

from .. import bucket_bridge, compat
from ..args import as_bool, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import employee as employee_tool
from . import kpi as kpi_tools

ENTRY_DOCTYPE = "Bucket Log Entry"
SESSION_DOCTYPE = "Bucket Log Session"
BADGE_DOCTYPE = "Bucket Log Badge Map"

#: Entries one sync call accepts. Past this a device is resending a season, not
#: a batch — split it. See ml_model.py's RECORD_CAP for the same reasoning.
BATCH_CAP = 500

#: Rows one list call returns.
RECORD_CAP = 500


def _require(doctype: str = ENTRY_DOCTYPE) -> None:
	compat.require_doctype(
		doctype,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _num(value, default: float = 0.0) -> float:
	try:
		if value is None or value == "":
			return default
		return float(value)
	except (TypeError, ValueError):
		return default


def _as_json_arg(args: dict, key: str, expected_type: type):
	"""`args[key]` as `expected_type` (list or dict) — a native value or a JSON
	string, since a model context sends either. `None` when the key is absent."""
	if key not in args or args[key] is None:
		return None
	raw = args[key]
	if isinstance(raw, expected_type):
		return raw
	if isinstance(raw, str):
		if raw.strip() == "":
			return expected_type()
		try:
			parsed = json.loads(raw)
		except (TypeError, ValueError):
			raise ToolError(f"{key} must be valid JSON. Nothing was changed.") from None
		if not isinstance(parsed, expected_type):
			raise ToolError(
				f"{key} must be a JSON {'array' if expected_type is list else 'object'}. Nothing was changed."
			)
		return parsed
	raise ToolError(
		f"{key} must be a JSON {'array' if expected_type is list else 'object'}. Nothing was changed."
	)


def _badge_map(company: str) -> dict:
	"""`{badge_id: employee}` for every active mapping in one company."""
	filters = {"active": 1}
	if company:
		filters["company"] = company
	rows = frappe.db.get_all(BADGE_DOCTYPE, filters=filters, fields=["badge_id", "employee"]) or []
	return {row["badge_id"]: row["employee"] for row in rows if row.get("badge_id") and row.get("employee")}


def _badge_row(badge_id: str, company: str, cache: dict) -> dict:
	"""One badge's register row, cached for the batch.

	Read DIRECTLY rather than out of `_badge_map`, which holds only active rows,
	because "no badge with that ID" and "a badge that was retired" are two
	different sentences to a foreman and only this read can tell them apart. A
	row belonging to another entity is reported as absent — see `resolve_badge`
	for why that is the right answer rather than "wrong company".
	"""
	key = (badge_id, company)
	if key not in cache:
		row = (
			frappe.db.get_value(
				BADGE_DOCTYPE, badge_id, ["badge_id", "company", "employee", "active"], as_dict=True
			)
			or {}
		)
		if row and company and str(row.get("company") or "") != company:
			row = {}
		cache[key] = dict(row)
	return cache[key]


def _employee_status(employee: str, cache: dict) -> str:
	"""One Employee's `status` column, cached for the batch."""
	if employee not in cache:
		cache[employee] = str(frappe.db.get_value("Employee", employee, "status") or "")
	return cache[employee]


def _shift_roster(shift: str) -> set:
	"""Who is currently clocked in on one Farm Shift.

	Somebody with a `left_at` is NOT on it: a picker who clocked out at noon and
	whose badge is scanned at three is either a mis-scan or a card somebody else
	is holding, and both are exactly what the strict policy is for.
	"""
	if not compat.doctype_exists("Farm Shift"):
		raise ToolError(
			"shift validation was asked for and this site has no Farm Shift doctype. Run "
			"`bench migrate`, or sync without a shift. Nothing was changed."
		)
	if not frappe.db.exists("Farm Shift", shift):
		raise ToolError(f"no Farm Shift {shift!r} on this site. Nothing was changed.")
	doc = frappe.get_doc("Farm Shift", shift)
	if doc.get("end_datetime"):
		raise ToolError(
			f"shift {shift} closed at {doc.get('end_datetime')}. Captures cannot be validated "
			"against a shift that is over — its Attendance rows are already written. Sync "
			"without a shift and link them with link_entries_to_shift. Nothing was changed."
		)
	return {
		str(entry.get("employee"))
		for entry in (doc.get("crew") or [])
		if entry.get("employee") and not entry.get("left_at")
	}


def _admission_errors(badge: str, employee: str, company: str, context: dict) -> list:
	"""Why this capture may not be attributed, under the strict badge policy.

	`context` carries the batch's caches and the shift being validated against;
	the RULE itself is `bucket_bridge.badge_admission_errors`, which is pure and
	reads no database. This function's whole job is turning a badge string into
	the facts that rule needs.
	"""
	shift = context.get("shift") or ""
	roster = context.get("roster") or set()

	if badge:
		row = _badge_row(badge, company, context["badges"])
		resolved = str(row.get("employee") or "")
		return bucket_bridge.badge_admission_errors(
			badge,
			{
				"known": bool(row),
				"active": bool(row.get("active")),
				"employee": resolved,
				"employee_status": (_employee_status(resolved, context["statuses"]) if resolved else ""),
				"shift": shift,
				"on_shift": (resolved in roster) if shift else None,
			},
		)

	# No badge, so the entry named its picker directly — a Desk import, since
	# the phone transport refuses an `employee` key outright. The badge
	# sentences do not apply; the two facts about the person still do.
	errors = []
	status = _employee_status(employee, context["statuses"]) or "Active"
	if status != "Active":
		errors.append(
			f"{employee} has employment status {status} — a capture cannot be paid to somebody "
			"who is not employed"
		)
	if shift and employee not in roster:
		errors.append(
			f"{employee} is not clocked in on shift {shift} — add them with add_worker_to_shift "
			"before their buckets can be attributed to it"
		)
	return errors


def _describe_entry(doc) -> dict:
	return {
		"name": doc.name,
		"entry_uuid": doc.entry_uuid,
		"session_uuid": doc.get("session_uuid") or None,
		"company": doc.company,
		"worker_badge": doc.get("worker_badge") or None,
		"employee": doc.get("employee") or None,
		"timestamp": str(doc.timestamp) if doc.get("timestamp") else None,
		"verdict": doc.verdict,
		"coverage_percent": doc.get("coverage_percent"),
		"model_uuid": doc.get("model_uuid") or None,
		"container_type": doc.get("container_type") or None,
		"mask_area_px": doc.get("mask_area_px"),
		"container_area_px": doc.get("container_area_px"),
		"gps_lat": doc.get("gps_lat"),
		"gps_lon": doc.get("gps_lon"),
		"h3_cell": doc.get("h3_cell") or None,
		"device_id": doc.get("device_id") or None,
		"shift": doc.get("shift") or None,
		"status": doc.status,
		"synced_at": str(doc.get("synced_at") or "") or None,
	}


def _sync_session(session_uuid: str) -> None:
	"""Recompute one session's totals from its OWN current entries and upsert it.

	Reads every entry in the session rather than only the ones this sync batch
	just wrote, because a session's true totals are whatever is on the site now
	— a second batch adding three more captures to a session synced yesterday
	should leave the session record agreeing with all six, not just the three.
	"""
	rows = frappe.db.get_all(
		ENTRY_DOCTYPE,
		filters={"session_uuid": session_uuid},
		fields=["verdict", "timestamp", "company", "worker_badge", "employee", "device_id"],
		order_by="timestamp asc",
		limit_page_length=0,
	)
	if not rows:
		return
	totals = bucket_bridge.aggregate_session(rows)
	first = rows[0]

	name = frappe.db.get_value(SESSION_DOCTYPE, {"session_uuid": session_uuid}, "name")
	if name:
		doc = frappe.get_doc(SESSION_DOCTYPE, name)
	else:
		doc = frappe.new_doc(SESSION_DOCTYPE)
		doc.session_uuid = session_uuid
		doc.company = first.get("company")
		doc.worker_badge = first.get("worker_badge")
		doc.device_id = first.get("device_id")

	# A badge resolved after this session's first entries synced (a later
	# link_badge_to_employee) should reach the session too, not only new
	# entries — but never overwrite an employee this record already has.
	if not doc.get("employee"):
		for row in rows:
			if row.get("employee"):
				doc.employee = row["employee"]
				break

	doc.started_at = totals["started_at"]
	doc.ended_at = totals["ended_at"]
	doc.total_accepted = totals["total_accepted"]
	doc.total_rejected = totals["total_rejected"]
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)


#: Class names a unique-index collision arrives as. MATCHED BY NAME, not by
#: `except frappe.exceptions.UniqueValidationError`, because the batch loop below
#: has to catch the whole family of insert failures in ONE clause in order to
#: keep going, and `isinstance` against a class this Frappe may not define is a
#: second failure inside the handler. `DuplicateEntryError` is what older Frappe
#: raised for the same collision, and both are worth recognising on a site this
#: app does not pin a Frappe version for.
_DUPLICATE_ERROR_NAMES = ("UniqueValidationError", "DuplicateEntryError")


def _is_duplicate(exc: BaseException) -> bool:
	"""Whether `exc` is a unique-index collision, whatever this Frappe calls it.

	The class hierarchy is walked rather than the exception's own type only, so a
	subclass a future Frappe introduces is still read as the duplicate it is.
	"""
	return any(cls.__name__ in _DUPLICATE_ERROR_NAMES for cls in type(exc).__mro__)


#: Reused for every entry in a batch rather than made unique per entry. MariaDB
#: replaces a savepoint of the same name, and there is never more than one of
#: these outstanding — each is set immediately before one insert and released or
#: rolled back to before the next.
_ENTRY_SAVEPOINT = "erpnext_mcp_bucket_entry"


def _savepoint():
	"""A named savepoint before one entry's insert, where this Frappe has them.

	SKIPPING AN ENTRY IS ONLY A SKIP IF THE TRANSACTION SURVIVES IT. `invalid`
	promises the caller that the other entries in the batch were filed, and a
	failed insert can leave the transaction in a state where every subsequent
	one fails too — which would turn one refused capture into a batch that
	reports thirty successes and commits none. Same tool and same reason as
	`dashboard._savepoint`; returns None where the database layer has no
	savepoints, which is the in-memory double and is harmless there because
	nothing it raises is a transaction a driver has poisoned.
	"""
	try:
		frappe.db.savepoint(_ENTRY_SAVEPOINT)
		return _ENTRY_SAVEPOINT
	except Exception:
		return None


def _rollback_to(savepoint) -> None:
	if not savepoint:
		return
	try:
		frappe.db.rollback(save_point=savepoint)
	except Exception:
		pass


# ── 1. sync_bucket_entries ───────────────────────────────────────────────


def sync_bucket_entries(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Receive a batch of bucket captures synced from a
	BucketLog device: create the Bucket Log Entry records, resolve each one's
	employee from its worker_badge against the Bucket Log Badge Map register,
	and keep the Bucket Log Session each belongs to up to date.

	DEDUPLICATES BY entry_uuid — resyncing a batch the site already has is a
	no-op, not a duplicate record. An entry that fails validation is reported
	and skipped rather than failing the call, and so (v0.59.2) is one the
	database itself refuses at the write; see the module docstring.

	`badge_policy` (v0.50.0) DECIDES WHAT AN UNRESOLVABLE BADGE MEANS.
	`lenient`, the default, is the v0.44.0 behaviour: the capture is filed with
	no employee and a later `link_badge_to_employee` backfills it — right for a
	Desk import of captures taken before somebody got round to mapping the card.
	`strict` refuses it, and is what the phone sends: badges are minted by this
	app now, so a handset scanning a string this site never issued has scanned
	something that is not a badge. The badge's SHAPE is checked under both —
	nothing with a colon, a brace or a space in it was ever a badge."""
	_require()
	actor = kpi_tools.require_kpi_role()

	entries = _as_json_arg(args, "entries", list)
	if not entries:
		raise ToolError("entries is required and must be a non-empty JSON array. Nothing was changed.")
	if len(entries) > BATCH_CAP:
		raise ToolError(
			f"{len(entries)} entries is more than one sync call accepts ({BATCH_CAP}). Split the "
			"batch. Nothing was changed."
		)

	policy = (as_str(args, "badge_policy") or bucket_bridge.BADGE_POLICY_LENIENT).strip().lower()
	if policy not in bucket_bridge.BADGE_POLICIES:
		raise ToolError(
			f"badge_policy must be one of {', '.join(bucket_bridge.BADGE_POLICIES)}; got "
			f"{policy!r}. Nothing was changed."
		)
	shift = as_str(args, "shift")
	if shift and policy != bucket_bridge.BADGE_POLICY_STRICT:
		raise ToolError(
			"shift is a strict-policy check — under badge_policy=lenient an entry is filed "
			"whatever its badge resolves to, so validating it against a crew would be a rule "
			"that never refuses anything. Pass badge_policy=strict, or drop shift. Nothing was "
			"changed."
		)
	admission = {
		"shift": shift,
		"roster": _shift_roster(shift) if shift else set(),
		"badges": {},
		"statuses": {},
	}

	uuids = [str(e.get("entry_uuid") or "").strip() for e in entries if isinstance(e, dict)]
	existing = set(
		frappe.db.get_all(
			ENTRY_DOCTYPE,
			filters={"entry_uuid": ("in", [u for u in uuids if u] or [""])},
			pluck="entry_uuid",
		)
		or []
	)

	created = []
	duplicates = []
	invalid = []
	touched_sessions: set = set()
	badge_maps: dict = {}

	for entry in entries:
		if not isinstance(entry, dict):
			invalid.append({"entry_uuid": None, "errors": ["entry must be a JSON object"]})
			continue

		entry_uuid = str(entry.get("entry_uuid") or "").strip()
		if entry_uuid and entry_uuid in existing:
			duplicates.append(entry_uuid)
			continue

		errors = bucket_bridge.validate_bucket_entry(entry)
		if errors:
			invalid.append({"entry_uuid": entry_uuid or None, "errors": errors})
			continue

		try:
			company = resolve_company(as_str(entry, "company"), required=True)
		except ToolError as exc:
			invalid.append({"entry_uuid": entry_uuid or None, "errors": [str(exc)]})
			continue

		if company not in badge_maps:
			badge_maps[company] = _badge_map(company)
		badge = str(entry.get("worker_badge") or "").strip()
		employee = str(entry.get("employee") or "").strip() or bucket_bridge.resolve_badge_to_employee(
			badge,
			badge_maps[company],
		)

		# THE SHAPE CHECK RUNS UNDER BOTH POLICIES. A string with whitespace, a
		# brace or a colon in it is a URL, a Wi-Fi join code or a login QR that
		# was held in front of the badge scanner — filing it produces a row in
		# the piecework register that nobody can ever claim, and no later
		# `link_badge_to_employee` will rescue it because nobody will map it.
		if badge:
			shape = bucket_bridge.validate_badge_id(badge)
			if shape:
				invalid.append({"entry_uuid": entry_uuid or None, "errors": shape})
				continue

		if policy == bucket_bridge.BADGE_POLICY_STRICT:
			refusals = _admission_errors(badge, employee, company, admission)
			if refusals:
				invalid.append({"entry_uuid": entry_uuid or None, "errors": refusals})
				continue

		doc = frappe.new_doc(ENTRY_DOCTYPE)
		doc.entry_uuid = entry_uuid
		doc.session_uuid = str(entry.get("session_uuid") or "").strip() or None
		doc.company = company
		doc.worker_badge = badge or None
		doc.employee = employee or None
		doc.timestamp = entry.get("timestamp")
		doc.verdict = entry.get("verdict")
		if entry.get("coverage_percent") not in (None, ""):
			doc.coverage_percent = _num(entry.get("coverage_percent"))
		doc.model_uuid = str(entry.get("model_uuid") or "").strip() or None
		doc.container_type = str(entry.get("container_type") or "").strip() or None
		if entry.get("mask_area_px") not in (None, ""):
			doc.mask_area_px = _num(entry.get("mask_area_px"))
		if entry.get("container_area_px") not in (None, ""):
			doc.container_area_px = _num(entry.get("container_area_px"))
		if entry.get("gps_lat") not in (None, ""):
			doc.gps_lat = _num(entry.get("gps_lat"))
		if entry.get("gps_lon") not in (None, ""):
			doc.gps_lon = _num(entry.get("gps_lon"))
		doc.h3_cell = str(entry.get("h3_cell") or "").strip() or None
		doc.device_id = str(entry.get("device_id") or "").strip() or None
		doc.flags.ignore_permissions = True
		savepoint = _savepoint()
		try:
			doc.insert(ignore_permissions=True)
		except Exception as exc:
			_rollback_to(savepoint)
			if _is_duplicate(exc):
				# The `existing` set above is a snapshot from before this loop ran —
				# it cannot see a row a CONCURRENT retry of this same batch inserted
				# a moment ago. That race is real: a phone that timed out waiting for
				# a response and resent the batch is the ordinary case, not an
				# exotic one. Losing that race is not a failure to report, it is the
				# duplicate this function already promises to skip.
				duplicates.append(entry_uuid)
				continue
			# ONE ENTRY THE COLUMN REFUSES MUST NOT COST THE OTHER THIRTY. Until
			# v0.59.2 this clause named `UniqueValidationError` and nothing else,
			# so anything the insert raised for any other reason left the loop and
			# came back as a 500 with no per-entry detail — and the queue on the
			# handset, which retries a batch that failed, resent the same poison
			# entry with the same thirty good ones behind it, forever. That is
			# exactly how the ISO-8601 timestamp bug presented: a payload every
			# check in this app approved, refused by MariaDB at the write.
			#
			# `invalid` is the channel this function already promises for "we
			# could not file this one, here is why", and `BucketSyncResult`
			# already decodes it and shows it on the capture screen. The message
			# carries the exception's class as well as its text because a
			# DataError and a TimestampError read very differently to whoever gets
			# handed the screenshot.
			invalid.append(
				{
					"entry_uuid": entry_uuid or None,
					"errors": [f"could not be filed: {type(exc).__name__}: {exc}"],
				}
			)
			continue

		created.append(_describe_entry(doc))
		existing.add(entry_uuid)
		if doc.session_uuid:
			touched_sessions.add(doc.session_uuid)

	for session_uuid in touched_sessions:
		_sync_session(session_uuid)

	summary = f"{len(created)} entry(ies) created"
	if duplicates:
		summary += f", {len(duplicates)} duplicate(s) skipped"
	if invalid:
		summary += f", {len(invalid)} invalid entry(ies) skipped"

	return ToolResult(
		data={
			"actor": actor,
			"badge_policy": policy,
			"shift": shift or None,
			"created_count": len(created),
			"duplicate_count": len(duplicates),
			"invalid_count": len(invalid),
			"created": created,
			"duplicates": duplicates,
			"invalid": invalid,
			"sessions_updated": sorted(touched_sessions),
		},
		summary=summary,
		docstatus_delta=(
			f"{len(created)} Bucket Log Entry record(s) created"
			if created
			else "none — nothing new to create"
		),
	)


# ── 2. list_bucket_entries ───────────────────────────────────────────────


def list_bucket_entries(args: dict) -> ToolResult:
	"""Read-only. Every Bucket Log Entry matching the filters, newest first."""
	_require()
	limit = min(as_limit(args), RECORD_CAP)

	filters: list = []
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters.append(["company", "=", company])
	employee = as_str(args, "employee")
	if employee:
		filters.append(["employee", "=", employee])
	badge = as_str(args, "badge") or as_str(args, "worker_badge")
	if badge:
		filters.append(["worker_badge", "=", badge])
	session = as_str(args, "session") or as_str(args, "session_uuid")
	if session:
		filters.append(["session_uuid", "=", session])
	verdict = as_str(args, "verdict")
	if verdict:
		if verdict not in bucket_bridge.VERDICTS:
			raise ToolError(f"verdict must be one of {', '.join(bucket_bridge.VERDICTS)}; got {verdict!r}.")
		filters.append(["verdict", "=", verdict])
	status = as_str(args, "status")
	if status:
		if status not in bucket_bridge.STATUSES:
			raise ToolError(f"status must be one of {', '.join(bucket_bridge.STATUSES)}; got {status!r}.")
		filters.append(["status", "=", status])
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date:
		filters.append(["timestamp", ">=", f"{from_date} 00:00:00"])
	if to_date:
		filters.append(["timestamp", "<=", f"{to_date} 23:59:59"])

	found = frappe.db.get_all(
		ENTRY_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"entry_uuid",
			"session_uuid",
			"company",
			"worker_badge",
			"employee",
			"timestamp",
			"verdict",
			"coverage_percent",
			"model_uuid",
			"container_type",
			"mask_area_px",
			"container_area_px",
			"shift",
			"status",
			"synced_at",
		],
		order_by="timestamp desc",
		limit=limit + 1,
	)
	truncated = len(found) > limit
	found = found[:limit]

	data = {"entries": found, "count": len(found), "truncated": truncated, "limit": limit}
	summary = f"{len(found)} bucket entry(ies)" + (f", truncated at {limit}" if truncated else "")
	return ToolResult(data=data, summary=summary)


# ── 3. get_bucket_session ────────────────────────────────────────────────


def get_bucket_session(args: dict) -> ToolResult:
	"""Read-only. One session, with totals computed LIVE from its own entries
	(bucket_bridge.aggregate_session) rather than only the stored counters —
	the two can drift if a badge resolves after the session was last synced,
	and this always answers with the current truth."""
	_require(SESSION_DOCTYPE)
	session = as_str(args, "session") or as_str(args, "session_uuid", required=True)
	name = (
		session
		if frappe.db.exists(SESSION_DOCTYPE, session)
		else frappe.db.get_value(
			SESSION_DOCTYPE,
			{"session_uuid": session},
			"name",
		)
	)
	if not name:
		raise ToolError(f"no Bucket Log Session matching {session!r} on this site.")
	doc = frappe.get_doc(SESSION_DOCTYPE, name)

	rows = frappe.db.get_all(
		ENTRY_DOCTYPE,
		filters={"session_uuid": doc.session_uuid},
		fields=["verdict", "timestamp", "status"],
		limit_page_length=0,
	)
	totals = bucket_bridge.aggregate_session(rows)
	linked_or_paid = sum(1 for r in rows if r.get("status") in ("Linked", "Paid"))

	data = {
		"name": doc.name,
		"session_uuid": doc.session_uuid,
		"company": doc.company,
		"worker_badge": doc.get("worker_badge") or None,
		"employee": doc.get("employee") or None,
		"device_id": doc.get("device_id") or None,
		"status": doc.status,
		**totals,
		"entries_linked_or_paid": linked_or_paid,
	}
	summary = (
		f"{doc.session_uuid}: {totals['total_accepted']} accepted, {totals['total_rejected']} rejected "
		f"({totals['acceptance_rate'] * 100:.0f}% acceptance)"
	)
	return ToolResult(data=data, summary=summary)


# ── 4. list_bucket_sessions ──────────────────────────────────────────────


def list_bucket_sessions(args: dict) -> ToolResult:
	"""Read-only. The session register, newest first."""
	_require(SESSION_DOCTYPE)
	limit = min(as_limit(args), RECORD_CAP)

	filters: list = []
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		filters.append(["company", "=", company])
	employee = as_str(args, "employee")
	if employee:
		filters.append(["employee", "=", employee])
	status = as_str(args, "status")
	if status:
		if status not in ("Open", "Closed", "Linked"):
			raise ToolError(f"status must be one of Open, Closed, Linked; got {status!r}.")
		filters.append(["status", "=", status])
	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date:
		filters.append(["started_at", ">=", f"{from_date} 00:00:00"])
	if to_date:
		filters.append(["started_at", "<=", f"{to_date} 23:59:59"])

	found = frappe.db.get_all(
		SESSION_DOCTYPE,
		filters=filters,
		fields=[
			"name",
			"session_uuid",
			"company",
			"worker_badge",
			"employee",
			"started_at",
			"ended_at",
			"total_accepted",
			"total_rejected",
			"device_id",
			"status",
		],
		order_by="started_at desc",
		limit=limit + 1,
	)
	truncated = len(found) > limit
	found = found[:limit]

	data = {"sessions": found, "count": len(found), "truncated": truncated, "limit": limit}
	summary = f"{len(found)} session(s)" + (f", truncated at {limit}" if truncated else "")
	return ToolResult(data=data, summary=summary)


# ── 5. link_badge_to_employee ────────────────────────────────────────────


def link_badge_to_employee(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Map a QR badge ID to an Employee — creates the
	Bucket Log Badge Map record if the badge is new, or repoints an existing one
	(a lost card reissued to somebody else). Backfills `employee` onto any
	already-synced Bucket Log Entry and Bucket Log Session that carries this
	badge and had no employee resolved yet — a badge mapped after the fact
	still pays for what was already picked."""
	_require(BADGE_DOCTYPE)
	actor = employee_tool.require_hr_role()

	badge_id = as_str(args, "badge_id", required=True)
	company = resolve_company(as_str(args, "company"), required=True)
	employee = as_str(args, "employee", required=True)
	if not frappe.db.exists("Employee", employee):
		raise ToolError(f"no Employee {employee!r} on this site. Nothing was changed.")

	active = as_bool(args, "active", True)
	notes = as_str(args, "notes")

	exists = frappe.db.exists(BADGE_DOCTYPE, badge_id)
	if exists:
		doc = frappe.get_doc(BADGE_DOCTYPE, badge_id)
		previous_employee = doc.employee
	else:
		doc = frappe.new_doc(BADGE_DOCTYPE)
		doc.badge_id = badge_id
		previous_employee = None

	doc.company = company
	doc.employee = employee
	doc.active = 1 if active else 0
	if notes:
		doc.notes = notes
	doc.flags.ignore_permissions = True
	if exists:
		doc.save(ignore_permissions=True)
	else:
		doc.insert(ignore_permissions=True)

	backfilled_entries = []
	backfilled_sessions = []
	if active:
		for row in (
			frappe.db.get_all(
				ENTRY_DOCTYPE,
				filters={"company": company, "worker_badge": badge_id},
				fields=["name", "employee"],
				limit_page_length=0,
			)
			or []
		):
			if not row.get("employee"):
				frappe.db.set_value(ENTRY_DOCTYPE, row["name"], "employee", employee)
				backfilled_entries.append(row["name"])
		for row in (
			frappe.db.get_all(
				SESSION_DOCTYPE,
				filters={"company": company, "worker_badge": badge_id},
				fields=["name", "employee"],
				limit_page_length=0,
			)
			or []
		):
			if not row.get("employee"):
				frappe.db.set_value(SESSION_DOCTYPE, row["name"], "employee", employee)
				backfilled_sessions.append(row["name"])

	described = {
		"badge_id": doc.name,
		"company": doc.company,
		"employee": doc.employee,
		"active": bool(doc.active),
		"notes": doc.get("notes") or None,
	}
	summary = f"badge {badge_id} -> {employee}"
	if previous_employee and previous_employee != employee:
		summary += f" (was {previous_employee})"
	if backfilled_entries:
		summary += f", backfilled {len(backfilled_entries)} entry(ies)"

	return ToolResult(
		data={
			"actor": actor,
			"badge": described,
			"previous_employee": previous_employee,
			"backfilled_entries": backfilled_entries,
			"backfilled_sessions": backfilled_sessions,
		},
		summary=summary,
		docstatus_delta="badge mapping updated" if exists else "none → 0 (badge mapped)",
	)


# ── 6. link_entries_to_shift ─────────────────────────────────────────────


def link_entries_to_shift(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Associate Bucket Log Entries with a Farm Shift so
	they are picked up as piece units when that shift's payroll runs. Pass
	EITHER `entries` (a list of entry_uuid or docname) or `session`
	(session_uuid — every not-yet-Paid entry in it). An entry already Paid is
	left untouched: status only ever advances toward Paid, never back."""
	_require()
	actor = kpi_tools.require_kpi_role()

	shift = as_str(args, "shift", required=True)
	if not frappe.db.exists("Farm Shift", shift):
		raise ToolError(f"no Farm Shift {shift!r} on this site. Nothing was changed.")

	entry_refs = _as_json_arg(args, "entries", list) or []
	session = as_str(args, "session") or as_str(args, "session_uuid")
	if not entry_refs and not session:
		raise ToolError("pass entries (a list of entry_uuid/docname) or session. Nothing was changed.")

	names: list = []
	if entry_refs:
		for ref in entry_refs:
			ref = str(ref or "").strip()
			if not ref:
				continue
			if frappe.db.exists(ENTRY_DOCTYPE, ref):
				names.append(ref)
			else:
				found = frappe.db.get_value(ENTRY_DOCTYPE, {"entry_uuid": ref}, "name")
				if not found:
					raise ToolError(f"no Bucket Log Entry matching {ref!r}. Nothing was changed.")
				names.append(found)
	else:
		names = (
			frappe.db.get_all(
				ENTRY_DOCTYPE,
				filters={"session_uuid": session, "status": ("!=", bucket_bridge.STATUS_PAID)},
				pluck="name",
				limit_page_length=0,
			)
			or []
		)
		if not names:
			raise ToolError(
				f"no linkable Bucket Log Entry in session {session!r} — none found, or all already "
				"Paid. Nothing was changed."
			)

	docs = [frappe.get_doc(ENTRY_DOCTYPE, name) for name in names]
	updated_shape = bucket_bridge.link_entries_to_shift([d.as_dict() for d in docs], shift)

	linked = []
	already_paid = []
	for doc, updated in zip(docs, updated_shape, strict=True):
		if updated["status"] == bucket_bridge.STATUS_PAID:
			already_paid.append(doc.name)
			continue
		doc.shift = updated["shift"]
		doc.status = updated["status"]
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)
		linked.append(doc.name)

	# A session whose every entry is now Linked or Paid is itself Linked — a
	# foreman should not have to open each entry to see whether payroll has it.
	for session_uuid in {d.session_uuid for d in docs if d.get("session_uuid")}:
		remaining = frappe.db.get_all(
			ENTRY_DOCTYPE,
			filters={"session_uuid": session_uuid, "status": "Pending"},
			pluck="name",
			limit_page_length=0,
		)
		if not remaining:
			session_name = frappe.db.get_value(SESSION_DOCTYPE, {"session_uuid": session_uuid}, "name")
			if session_name:
				frappe.db.set_value(SESSION_DOCTYPE, session_name, "status", "Linked")

	summary = f"linked {len(linked)} entry(ies) to {shift}"
	if already_paid:
		summary += f", {len(already_paid)} already Paid (untouched)"

	return ToolResult(
		data={"actor": actor, "shift": shift, "linked": linked, "already_paid": already_paid},
		summary=summary,
		docstatus_delta=f"shift set on {len(linked)} Bucket Log Entry record(s)",
	)


# ── 7. get_piecework_summary ─────────────────────────────────────────────


def get_piecework_summary(args: dict) -> ToolResult:
	"""Read-only. The payroll-ready summary for one employee over a date range:
	accepted buckets — what entries_to_payroll_shape would turn into piece
	units — sessions worked, and acceptance rate."""
	_require()
	employee = as_str(args, "employee", required=True)
	company = resolve_company(as_str(args, "company"), required=False)
	from_date = as_date(args, "from_date", required=True)
	to_date = as_date(args, "to_date", required=True)
	if from_date > to_date:
		raise ToolError(f"from_date {from_date} is after to_date {to_date}")

	filters = [
		["employee", "=", employee],
		["timestamp", ">=", f"{from_date} 00:00:00"],
		["timestamp", "<=", f"{to_date} 23:59:59"],
	]
	if company:
		filters.append(["company", "=", company])

	rows = frappe.db.get_all(
		ENTRY_DOCTYPE,
		filters=filters,
		fields=["verdict", "timestamp", "session_uuid", "status"],
		limit_page_length=0,
	)

	overall = bucket_bridge.aggregate_session(rows)
	by_session: dict = {}
	for row in rows:
		key = row.get("session_uuid") or ""
		if key:
			by_session.setdefault(key, []).append(row)
	session_rates = [
		bucket_bridge.aggregate_session(rows_)["acceptance_rate"] for rows_ in by_session.values()
	]
	avg_session_acceptance_rate = round(sum(session_rates) / len(session_rates), 4) if session_rates else 0.0

	status_counts = {"Pending": 0, "Linked": 0, "Paid": 0}
	for row in rows:
		status = row.get("status") or "Pending"
		if status in status_counts:
			status_counts[status] += 1

	data = {
		"employee": employee,
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"total_accepted": overall["total_accepted"],
		"total_rejected": overall["total_rejected"],
		"acceptance_rate": overall["acceptance_rate"],
		"session_count": len(by_session),
		"avg_session_acceptance_rate": avg_session_acceptance_rate,
		"status_breakdown": status_counts,
		"piece_units": overall["total_accepted"],
	}
	summary = (
		f"{employee}: {overall['total_accepted']} accepted bucket(s) across {len(by_session)} "
		f"session(s), {from_date}..{to_date}"
	)
	return ToolResult(data=data, summary=summary)


# ── 8. reconcile_bucket_payroll ──────────────────────────────────────────


def reconcile_bucket_payroll(args: dict) -> ToolResult:
	"""Read-only. Compares accepted Bucket Log Entries against what Farm
	Payroll Slips actually paid for the same company and period, and flags
	where they disagree.

	A DISCREPANCY IS NOT NECESSARILY AN ERROR. A bucket entry with no Farm
	Payroll Slip covering it yet is simply unpaid so far — this reports, it
	does not correct, the same posture check_minimum_wage_by_state takes."""
	_require()
	company = resolve_company(as_str(args, "company"), required=True)
	from_date = as_date(args, "from_date", required=True)
	to_date = as_date(args, "to_date", required=True)
	if from_date > to_date:
		raise ToolError(f"from_date {from_date} is after to_date {to_date}")
	employee_filter = as_str(args, "employee")

	entry_filters = [
		["company", "=", company],
		["verdict", "=", bucket_bridge.VERDICT_ACCEPTED],
		["timestamp", ">=", f"{from_date} 00:00:00"],
		["timestamp", "<=", f"{to_date} 23:59:59"],
	]
	if employee_filter:
		entry_filters.append(["employee", "=", employee_filter])

	entries = frappe.db.get_all(
		ENTRY_DOCTYPE,
		filters=entry_filters,
		fields=["employee", "status"],
		limit_page_length=0,
	)

	bucket_totals: dict = {}
	unattributed = 0
	unpaid_pending = 0
	for row in entries:
		employee = row.get("employee")
		if not employee:
			unattributed += 1
			continue
		bucket_totals[employee] = bucket_totals.get(employee, 0) + 1
		if row.get("status") == "Pending":
			unpaid_pending += 1

	farm_payroll_entry_available = compat.doctype_exists("Farm Payroll Entry")
	payroll_totals: dict = {}
	if farm_payroll_entry_available:
		overlapping = (
			frappe.db.get_all(
				"Farm Payroll Entry",
				filters=[
					["company", "=", company],
					["pay_period_start", "<=", to_date],
					["pay_period_end", ">=", from_date],
					["status", "!=", "Cancelled"],
				],
				pluck="name",
				limit_page_length=0,
			)
			or []
		)
		if overlapping:
			slip_filters = [["parenttype", "=", "Farm Payroll Entry"], ["parent", "in", overlapping]]
			if employee_filter:
				slip_filters.append(["employee", "=", employee_filter])
			for row in (
				frappe.db.get_all(
					"Farm Payroll Slip",
					filters=slip_filters,
					fields=["employee", "piece_units"],
					limit_page_length=0,
				)
				or []
			):
				employee = row.get("employee")
				if not employee:
					continue
				payroll_totals[employee] = payroll_totals.get(employee, 0.0) + _num(row.get("piece_units"))

	employees = sorted(set(bucket_totals) | set(payroll_totals))
	rows_out = []
	for employee in employees:
		bucket_units = bucket_totals.get(employee, 0)
		paid_units = payroll_totals.get(employee, 0.0)
		discrepancy = round(bucket_units - paid_units, 2)
		if abs(discrepancy) < 0.005:
			status = "matches"
		elif discrepancy > 0:
			status = "bucket_units_not_yet_paid"
		else:
			status = "payroll_units_exceed_bucket_log"
		rows_out.append(
			{
				"employee": employee,
				"accepted_bucket_entries": bucket_units,
				"payroll_piece_units": round(paid_units, 2),
				"discrepancy": discrepancy,
				"status": status,
			}
		)

	mismatches = [r for r in rows_out if r["status"] != "matches"]
	data = {
		"company": company,
		"from_date": from_date,
		"to_date": to_date,
		"employees": rows_out,
		"mismatch_count": len(mismatches),
		"unattributed_bucket_entries": unattributed,
		"unpaid_pending_bucket_entries": unpaid_pending,
		"farm_payroll_entry_available": farm_payroll_entry_available,
	}
	summary = f"{len(rows_out)} employee(s) compared, {len(mismatches)} mismatch(es)"
	if unattributed:
		summary += f", {unattributed} bucket entry(ies) with no employee resolved"
	return ToolResult(data=data, summary=summary)
