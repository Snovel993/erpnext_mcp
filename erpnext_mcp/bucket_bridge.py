# SPDX-License-Identifier: MIT
"""BucketLog → ERPNext piecework bridge. PURE FUNCTIONS.

Same contract as `model_registry.py`, `budget_engine.py` and
`payroll_gl.py`: no database reads, no side effects, everything arrives as a
plain dict and everything returned is derivable again from the same dict.
`tools/bucket_log.py` is the only place that reads or writes a Bucket Log
Entry, Bucket Log Session or Bucket Log Badge Map document.

WHAT A BUCKET LOG ENTRY IS. BucketLog (the iOS app) runs an on-device ML model
against a photo of a picked bucket and records a verdict — Accepted or
Rejected — plus where, when, whose badge was scanned, and how much of the
bucket the model judged full (`coverage_percent`). This app never touches the
photo or the model; it holds the one fact BucketLog's own storage has no
reason to know: which of those captures is a piece of work somebody gets paid
for.

────────────────────────────────────────────────────────────────────────────
ONLY ACCEPTED BUCKETS ARE PIECE WORK
────────────────────────────────────────────────────────────────────────────

A Rejected verdict is the model saying the bucket was not actually filled —
underweight, mis-scanned, empty. Counting it as a unit would pay for a bucket
nobody picked, so `entries_to_payroll_shape` filters it out here, at the one
place every payroll-facing read of this data passes through, rather than
leaving it for a payroll run to notice.

────────────────────────────────────────────────────────────────────────────
THE SHAPE THIS PRODUCES IS `payroll_integration.py`'S, NOT A NEW ONE
────────────────────────────────────────────────────────────────────────────

`_piece_units_for` in `payroll_integration.py` already reads a `bucket_logs`
list off a shift dict, already matches a row to a worker through
`_WORKER_KEYS` (`employee` first), and already treats a row with no count
column as ONE unit — which is exactly what a Bucket Log Entry row is:
one entry, one bucket. `entries_to_payroll_shape` reshapes into precisely that,
so nothing in `payroll_integration.py` had to change to read this app's own
data.
"""

from __future__ import annotations

from datetime import datetime

#: The two verdicts BucketLog's on-device model can reach. Matches the
#: Bucket Log Entry `verdict` Select field exactly.
VERDICT_ACCEPTED = "Accepted"
VERDICT_REJECTED = "Rejected"
VERDICTS = (VERDICT_ACCEPTED, VERDICT_REJECTED)

#: Where a Bucket Log Entry sits in the payroll lifecycle. Pending is synced
#: and unlinked; Linked carries a `shift`; Paid has appeared on a slip.
#: `link_entries_to_shift` is the only thing that moves Pending → Linked, and
#: it never moves anything OUT of Paid — see its docstring.
STATUS_PENDING = "Pending"
STATUS_LINKED = "Linked"
STATUS_PAID = "Paid"
STATUSES = (STATUS_PENDING, STATUS_LINKED, STATUS_PAID)


def _clean(value) -> str:
	return str(value or "").strip()


def _as_float(value, default=0.0):
	try:
		if value is None or value == "":
			return default
		return float(value)
	except (TypeError, ValueError):
		return default


def _parse_dt(value) -> datetime | None:
	"""A datetime from whatever a caller had, or None — never a raise.

	Same tolerant reading as `payroll_integration._as_datetime`, restated here
	rather than imported: this module owes `payroll_integration.py` nothing at
	load time, and a Bucket Log Entry's timestamp arrives from an iPhone's own
	`ISO8601DateFormatter`, a Frappe Datetime column, or a test fixture, and all
	three are worth reading rather than rejecting.
	"""
	if value is None or value == "":
		return None
	if isinstance(value, datetime):
		return value
	text = str(value).strip().replace("T", " ")
	if text.endswith("Z"):
		text = text[:-1]
	if "+" in text[10:]:
		text = text[: 10 + text[10:].index("+")]
	if "." in text:
		text = text.split(".")[0]
	for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
		try:
			return datetime.strptime(text, fmt)
		except ValueError:
			continue
	return None


# ── 1. validate_bucket_entry ─────────────────────────────────────────────


def validate_bucket_entry(entry: dict) -> list[str]:
	"""Every reason `entry` cannot become (or remain) a Bucket Log Entry record.

	Empty list means valid. Checks only what this module can check without a
	database: required fields, a recognised verdict, a plausible
	`coverage_percent`, and that SOMETHING identifies who picked it — a badge or
	an already-resolved employee. Uniqueness of `entry_uuid` (the field
	`sync_bucket_entries` deduplicates a sync batch by) needs a database read
	and lives in the tool layer, the same split `validate_model_registration`
	keeps against `check_model_conflicts`.
	"""
	entry = entry or {}
	errors = []

	if not _clean(entry.get("entry_uuid")):
		errors.append("entry_uuid is required")

	if not _clean(entry.get("company")):
		errors.append("company is required")

	if not _clean(entry.get("timestamp")):
		errors.append("timestamp is required")
	elif _parse_dt(entry.get("timestamp")) is None:
		errors.append(f"timestamp {entry.get('timestamp')!r} is not a recognized date/time")

	verdict = entry.get("verdict")
	if not _clean(verdict):
		errors.append("verdict is required")
	elif verdict not in VERDICTS:
		errors.append(f"verdict must be one of {', '.join(VERDICTS)}; got {verdict!r}")

	if not _clean(entry.get("worker_badge")) and not _clean(entry.get("employee")):
		errors.append(
			"worker_badge or employee is required — a capture with neither cannot be attributed to anybody"
		)

	coverage = entry.get("coverage_percent")
	if coverage not in (None, ""):
		value = _as_float(coverage, default=None)
		if value is None:
			errors.append(f"coverage_percent must be a number, got {coverage!r}")
		elif not (0.0 <= value <= 100.0):
			errors.append(f"coverage_percent must be between 0 and 100, got {value!r}")

	status = entry.get("status")
	if status and status not in STATUSES:
		errors.append(f"status must be one of {', '.join(STATUSES)}; got {status!r}")

	return errors


# ── 2. resolve_badge_to_employee ─────────────────────────────────────────


def resolve_badge_to_employee(badge_id: str, badge_map: dict) -> str | None:
	"""The Employee a badge belongs to, from a pre-fetched `{badge_id: employee}` map.

	Pure — the map is looked up, not queried. `tools/bucket_log.py` is what
	reads the Bucket Log Badge Map records into this shape before calling in.
	Matched exactly: a badge ID is either the same string or it is a different
	badge, and normalising case here would let two badges that print
	differently resolve to the same worker.
	"""
	badge_id = _clean(badge_id)
	if not badge_id or not badge_map:
		return None
	return _clean(badge_map.get(badge_id)) or None


# ── 3. aggregate_session ─────────────────────────────────────────────────


def aggregate_session(entries: list[dict]) -> dict:
	"""One session's totals, computed from its own entries rather than trusted
	off a running counter.

	`started_at`/`ended_at` are the earliest and latest entry timestamp — the
	entries are the ground truth of when a scan happened, not whatever a phone
	app's own session-open/session-close events claim. `acceptance_rate` is
	`0.0` for a session with no verdicted entries at all, not a division error.
	"""
	entries = entries or []
	accepted = rejected = 0
	parsed_times: list[datetime] = []

	for entry in entries:
		verdict = _clean(entry.get("verdict"))
		if verdict == VERDICT_ACCEPTED:
			accepted += 1
		elif verdict == VERDICT_REJECTED:
			rejected += 1
		when = _parse_dt(entry.get("timestamp"))
		if when is not None:
			parsed_times.append(when)

	total = accepted + rejected
	started = min(parsed_times) if parsed_times else None
	ended = max(parsed_times) if parsed_times else None
	duration_minutes = 0.0
	if started and ended and ended > started:
		duration_minutes = round((ended - started).total_seconds() / 60.0, 2)

	return {
		"total_entries": len(entries),
		"total_accepted": accepted,
		"total_rejected": rejected,
		"acceptance_rate": round(accepted / total, 4) if total else 0.0,
		"started_at": started.strftime("%Y-%m-%d %H:%M:%S") if started else None,
		"ended_at": ended.strftime("%Y-%m-%d %H:%M:%S") if ended else None,
		"duration_minutes": duration_minutes,
	}


# ── 4. entries_to_payroll_shape ──────────────────────────────────────────


def entries_to_payroll_shape(entries: list[dict]) -> list[dict]:
	"""Bucket Log Entry rows, reshaped into the `bucket_logs` rows
	`payroll_integration._piece_units_for` reads off a shift.

	ONLY ACCEPTED ENTRIES SURVIVE — see the module docstring. Entries with no
	resolved `employee` are dropped too: an unresolved badge cannot be paid,
	and a payroll run silently attributing it to nobody would be worse than a
	bucket that goes uncounted until `link_badge_to_employee` fixes the map.

	One row per accepted, attributed entry, and no unit-count key on the row —
	`payroll_integration._row_units` reads a row with none of `_UNIT_KEYS` as
	ONE bucket, which is exactly what a Bucket Log Entry row means. `entry_uuid`
	rides along for traceability; `_piece_units_for` ignores keys it does not
	recognise.
	"""
	rows = []
	for entry in entries or []:
		if _clean(entry.get("verdict")) != VERDICT_ACCEPTED:
			continue
		employee = _clean(entry.get("employee"))
		if not employee:
			continue
		rows.append({"employee": employee, "entry_uuid": entry.get("entry_uuid") or None})
	return rows


# ── 5. link_entries_to_shift ─────────────────────────────────────────────


def link_entries_to_shift(entries: list[dict], shift_name: str) -> list[dict]:
	"""Bucket Log Entry rows, updated to carry `shift` and move to `status=Linked`.

	Pure — returns NEW dicts; `tools/bucket_log.py::link_entries_to_shift` is
	what saves them. An entry already `Paid` is returned unchanged: status only
	ever advances Pending → Linked → Paid, and re-linking a paid bucket to a
	different shift would detach it from the slip that already paid for it
	without the ledger knowing anything moved.
	"""
	shift_name = _clean(shift_name)
	out = []
	for entry in entries or []:
		updated = dict(entry)
		if _clean(updated.get("status")) == STATUS_PAID:
			out.append(updated)
			continue
		updated["shift"] = shift_name
		updated["status"] = STATUS_LINKED
		out.append(updated)
	return out
