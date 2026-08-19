# SPDX-License-Identifier: MIT
"""BucketLog → ERPNext piecework bridge. PURE FUNCTIONS.

Same contract as `model_registry.py`, `budget_engine.py` and
`payroll_gl.py`: no database reads, no side effects, everything arrives as a
plain dict and everything returned is derivable again from the same dict.
`tools/bucket_log.py` is the only place that reads or writes a Bucket Log
Entry, Bucket Log Session or Bucket Log Badge Map document.

WHAT A BUCKET LOG ENTRY IS. BucketLog (the iOS app) runs an on-device ML model
against a photo of a picked bucket and reaches a verdict — Accepted or
Rejected — plus where, when and whose badge was scanned. This app never touches
the photo or the model; it holds the one fact BucketLog's own storage has no
reason to know: which of those captures is a piece of work somebody gets paid
for.

────────────────────────────────────────────────────────────────────────────
THE MODEL IS A BINARY GATE. THERE IS NO PARTIAL CREDIT ANYWHERE
────────────────────────────────────────────────────────────────────────────

A bucket is full or it is not full. The phone makes that decision once, on
device, and the ONLY thing that crosses to this app is which way it went:

    Accepted  →  1 bucket
    Rejected  →  0 buckets

    piecework pay = number of Accepted buckets × the piece rate

That is the whole arithmetic. **There is no partial bucket, no fractional
unit, and no percentage anywhere in the pay calculation.** A bucket the model
judged 51% full and one it judged 99% full are worth exactly the same thing if
they were both Accepted — which is one — and both worth nothing if they were
Rejected. The gate has already been applied by the time a capture gets here;
this app does not re-derive it and must never second-guess it with a number.

`coverage_percent` IS DIAGNOSTIC AND IS NOT AN INPUT TO PAY. It is the model's
own record of why the gate went the way it did, kept so somebody auditing a
model version can see what it was looking at — the same reason `model_uuid`
rides along. Nothing in `payroll_integration.py` reads it: it is deliberately
absent from `_UNIT_KEYS`, and `entries_to_payroll_shape` deliberately does not
emit it. `test_bucket_bridge.TheGateIsBinary` asserts both directions, against
`payroll_integration._UNIT_KEYS` itself so the two cannot drift apart.

If a future release wants to pay a partial bucket, that is a change to the
GATE — a third verdict, priced deliberately — and not a multiplication by a
coverage figure that was only ever evidence.

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

import re
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

#: How much of a scanned string this app will accept as a badge ID. Four
#: because the shortest thing anybody would print on a card is a prefix and a
#: digit; sixty-four because a `farm_app` `QRToken` uuid4 is thirty-six and a
#: string twice that length is a payload somebody scanned by mistake. See
#: `validate_badge_id` for what the bound is actually defending against.
BADGE_ID_MIN = 4
BADGE_ID_MAX = 64

#: The characters a badge ID may contain. Letters, digits and the three
#: separators a printable identifier uses. Deliberately EXCLUDES `:` and `/`
#: and whitespace, which is the whole point — see `validate_badge_id`.
_BADGE_ID_CHARS = re.compile(r"^[A-Za-z0-9._-]+$")

#: Digits in a minted badge's sequence number. Four gives `CF-0001` … `CF-9999`,
#: which is more pickers than any single entity in tree fruit runs in a season,
#: and it keeps every card the same width so a stack of them reads as a stack.
BADGE_SEQUENCE_DIGITS = 4

#: Longest prefix a minted badge carries. Six characters is a generous company
#: abbreviation and keeps `CFARMS-0001` readable aloud over a radio, which is
#: the failure mode a human-readable badge ID exists for.
BADGE_PREFIX_MAX = 6

#: A minted badge, as a pattern: prefix, one hyphen, digits. `parse_badge_sequence`
#: is the reader; nothing else should be matching badge IDs with a regex.
_MINTED_BADGE = re.compile(rf"^(?P<prefix>[A-Z0-9]{{1,{BADGE_PREFIX_MAX}}})-(?P<sequence>\d+)$")

#: What `sync_bucket_entries` does with a badge it cannot resolve.
#:
#: `lenient` is the v0.44.0 behaviour and stays the default for the MCP tool: an
#: entry whose badge is not in the register yet is still filed with no employee,
#: and a later `link_badge_to_employee` backfills it. That is deliberate — a
#: badge is scanned before somebody gets round to mapping it more often than
#: after, and refusing the capture would lose a morning of picking.
#:
#: `strict` is what the PHONE sends, and the reason it exists is that the two
#: situations are not the same one. Since badges are minted by this app
#: (`generate_employee_badge_qr`) the register is written at the moment the card
#: is printed, so a handset scanning a string this site has never heard of has
#: scanned something that is not a badge — a barcode on a soda can, a Wi-Fi QR,
#: a login card. Filing that as an unattributed capture puts a row in the
#: piecework register that nobody can ever claim.
BADGE_POLICY_LENIENT = "lenient"
BADGE_POLICY_STRICT = "strict"
BADGE_POLICIES = (BADGE_POLICY_LENIENT, BADGE_POLICY_STRICT)


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

	THE VERDICT IS THE ONLY THING THAT DECIDES PAY, and it is checked as one of
	exactly two words. `coverage_percent` is range-checked because a number
	outside 0–100 means the sender's units are wrong and its diagnostics are
	worthless — NOT because the figure buys anything. A capture with no coverage
	at all is perfectly valid and is worth exactly what an Accepted capture with
	94.2% is worth: one bucket. See the module docstring on the binary gate.
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

	# v0.68.0. Same plausibility check as coverage_percent, and the same reason:
	# these two feed get_fill_determination's arithmetic, and a negative pixel
	# count is not a smaller area, it is the sender's units being wrong.
	for field in ("mask_area_px", "container_area_px"):
		raw = entry.get(field)
		if raw not in (None, ""):
			value = _as_float(raw, default=None)
			if value is None:
				errors.append(f"{field} must be a number, got {raw!r}")
			elif value < 0:
				errors.append(f"{field} must not be negative, got {value!r}")

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

	ONE ROW PER ACCEPTED ENTRY, AND NO COUNT KEY ON IT. That is the binary gate
	expressed in the data rather than asserted in a comment:
	`payroll_integration._row_units` reads a row carrying none of `_UNIT_KEYS`
	as exactly ONE bucket, so a full bucket is 1 and a not-full bucket is not
	here at all. `entry_uuid` rides along for traceability and
	`_piece_units_for` ignores keys it does not recognise.

	THE ROW IS BUILT KEY BY KEY RATHER THAN COPIED. Passing the entry through
	and deleting what payroll must not see would be one forgotten field away
	from a `coverage_percent` — or a future column named `units` — reaching
	`_row_units` and turning a bucket into a fraction of one. Two keys go out
	because two keys are what this means; `TheGateIsBinary` in
	`test_bucket_bridge.py` checks the emitted keys against
	`payroll_integration._UNIT_KEYS` itself, so neither side can drift into
	paying partial credit.
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


# ── 6. The badge ID itself ───────────────────────────────────────────────
#
# WHICH STRING IS A BADGE ID. Through v0.49.0 the answer was "whatever was in
# front of the camera". `QRScannerModel.handleScan` hands the raw
# `stringValue` to its caller undecoded, both consumers take it verbatim, and
# `resolve_badge_to_employee` matches it exactly — so a soda can's barcode, a
# Wi-Fi join code and an operator's login QR were all, as far as this app was
# concerned, badges.
#
# That was a virtue as well as a hole: the SAME raw string goes into the
# register at onboarding and into the capture in the orchard, so the two agree
# by construction, and a card printed by `farm_app` (a uuid4 `QRToken`) works
# unmodified. The functions below keep that property — matching stays exact and
# format-free — and add the two things it was missing: a shape a scanned string
# has to have before this app will call it a badge, and a way to MINT one that
# a human can read off a scuffed card and say aloud over a radio at 6am.
#
# THE SHAPE IS A FLOOR, NOT A GRAMMAR. `validate_badge_id` does not require the
# minted `CF-0001` form, because every badge already in a worker's pocket is a
# thirty-six character uuid4 and refusing those would be a reprint of the whole
# workforce on the day of the upgrade. What it refuses is the class of string
# that is definitely not a badge: something with whitespace in it, something
# that starts a JSON document, something with a `:` or a `/` in it — which is
# every URL, every Wi-Fi payload and the `key:secret` half of a login card.


def normalise_badge_prefix(text: str) -> str:
	"""A company's abbreviation, as the prefix of a minted badge ID.

	Uppercased, stripped to letters and digits, cut to `BADGE_PREFIX_MAX`. A
	value with no alphanumerics at all gives `""` and the caller decides — this
	never invents a prefix, the same posture `abbr.derive_abbr` takes.
	"""
	cleaned = re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()
	return cleaned[:BADGE_PREFIX_MAX]


def format_badge_id(prefix: str, sequence: int) -> str:
	"""`("CF", 1)` → `"CF-0001"`. Zero-padded so a stack of cards reads as a stack.

	A sequence past four digits is NOT truncated — `CF-10000` is a wider card
	and a wrong badge ID is worse than an untidy one.
	"""
	prefix = normalise_badge_prefix(prefix)
	if not prefix:
		raise ValueError("a badge ID needs a company prefix; got nothing usable")
	return f"{prefix}-{max(0, int(sequence)):0{BADGE_SEQUENCE_DIGITS}d}"


def parse_badge_sequence(badge_id: str, prefix: str = "") -> int | None:
	"""The sequence number out of a minted badge ID, or None.

	None for anything this app did not mint — a `farm_app` uuid4, an operator's
	hand-typed `BADGE-77`, a badge whose prefix belongs to another company. That
	is what makes `next_badge_sequence` safe to run over a register holding all
	three: the strings it cannot read cannot influence the number it picks.
	"""
	match = _MINTED_BADGE.match(_clean(badge_id).upper())
	if not match:
		return None
	if prefix and match.group("prefix") != normalise_badge_prefix(prefix):
		return None
	return int(match.group("sequence"))


def next_badge_sequence(existing_ids, prefix: str) -> int:
	"""One past the highest sequence already minted under `prefix`.

	COUNTS UP FROM THE HIGHEST, NOT FROM THE ROW COUNT, and never reuses a gap.
	A badge retired when a card was lost keeps its row (`active` unticked), and
	handing its number to the next picker would make two people's piecework
	share one identifier in every audit that reads the history rather than the
	current register.
	"""
	prefix = normalise_badge_prefix(prefix)
	if not prefix:
		raise ValueError("a badge ID needs a company prefix; got nothing usable")
	highest = 0
	for badge_id in existing_ids or []:
		sequence = parse_badge_sequence(badge_id, prefix)
		if sequence is not None and sequence > highest:
			highest = sequence
	return highest + 1


def next_badge_id(existing_ids, prefix: str) -> str:
	"""The next badge ID to mint under `prefix`, given what is already issued."""
	return format_badge_id(prefix, next_badge_sequence(existing_ids, prefix))


#: How much of a rejected scan a refusal is allowed to quote. See `_display`.
BADGE_ECHO_CHARS = 12


def _display(text: str) -> str:
	"""A scanned string, safe to put in a refusal, an audit row and a phone.

	THE REFUSAL MUST NOT ECHO WHAT IT REFUSED. The strings that fail this check
	are exactly the ones worth not repeating: `generate_mobile_login_qr` produces
	`{"url":…,"api_key":…,"api_secret":…}`, and a badge step that scanned one and
	then quoted it back would put the credential in a `ValidationError`, in the
	MCP Action Log's arguments, and on a screen in an orchard — three places it
	was never meant to be, over a mis-scan somebody made by accident.

	Twelve characters and a length is enough to recognise WHICH thing was
	scanned ("it starts with a brace", "it is 54 characters of URL") without
	reproducing it. A genuinely near-miss badge — `ETC 0001` — is under the bound
	and is quoted whole, which is where quoting actually helps.
	"""
	text = _clean(text)
	if len(text) <= BADGE_ECHO_CHARS:
		return repr(text)
	return f"{text[:BADGE_ECHO_CHARS]!r}… ({len(text)} characters)"


def validate_badge_id(badge_id) -> list[str]:
	"""Every reason this string cannot be a badge ID. Empty list means it can.

	See the section comment above for what this is and is not. In one line: it
	is the check that stops the camera turning a URL, a Wi-Fi join code, a JSON
	login credential or a scanned line of text into somebody's payroll
	identifier, and it is NOT a requirement that the badge was minted here.

	The refusals name what was wrong and quote only as much of the scan as
	`_display` allows — a check whose whole job is catching credentials scanned
	by mistake must not repeat one back.
	"""
	text = _clean(badge_id)
	errors = []

	if not text:
		return ["badge ID is empty"]
	shown = _display(text)
	if len(text) < BADGE_ID_MIN:
		errors.append(f"badge ID {shown} is shorter than {BADGE_ID_MIN} characters")
	if len(text) > BADGE_ID_MAX:
		errors.append(
			f"badge ID is {len(text)} characters, past the {BADGE_ID_MAX} a badge carries — "
			"this is a payload, not a badge"
		)
	if text[:1] in ("{", "["):
		errors.append(
			"badge ID starts a JSON document — a login QR or an app's own payload was scanned "
			"at the badge step, not a badge"
		)
	if not _BADGE_ID_CHARS.match(text):
		errors.append(
			f"badge ID {shown} carries characters a badge does not: a badge is letters, digits, "
			"'-', '_' and '.' only, which is what refuses a URL, a Wi-Fi join code and an "
			"api_key:api_secret pair"
		)
	return errors


# ── 7. badge_admission_errors ────────────────────────────────────────────


def badge_admission_errors(badge_id: str, facts: dict) -> list[str]:
	"""Every reason a scanned badge may not attribute this capture. Pure.

	`facts` is what `tools/bucket_log.py` read out of the database on this
	badge's behalf, kept as a plain dict so the RULE is testable without a site:

	    known            the badge has a Bucket Log Badge Map row in this company
	    active           that row's `active` is ticked
	    employee         the Employee it resolves to, or None
	    employee_status  that Employee's `status` column
	    shift            the Farm Shift the caller is validating against, or ""
	    on_shift         whether the employee is on that shift's crew (None = not asked)

	SEPARATE FROM `validate_bucket_entry` ON PURPOSE. That one answers "is this
	a well-formed capture", which a Desk import and a handset are asked
	identically. This answers "may this badge be paid for it", which they are
	not: a Desk import of last week's captures legitimately carries badges the
	register did not have yet, and a phone in an orchard scanning an
	unregistered string has scanned a soda can.
	"""
	facts = facts or {}
	errors = validate_badge_id(badge_id)
	if errors:
		return errors

	badge_id = _clean(badge_id)
	if not facts.get("known"):
		return [
			f"badge {badge_id!r} is not in this company's badge register — nothing was issued "
			"with that ID, so it cannot be attributed to anybody. Issue a badge with "
			"generate_employee_badge_qr, or map an existing card with link_badge_to_employee"
		]
	if not facts.get("active"):
		return [
			f"badge {badge_id!r} was retired — the register keeps who held it and it no longer "
			"resolves to anybody. A reissued card has its own ID"
		]

	employee = _clean(facts.get("employee"))
	if not employee:
		return [f"badge {badge_id!r} has a register row with no Employee on it"]

	status = _clean(facts.get("employee_status")) or "Active"
	if status != "Active":
		errors.append(
			f"badge {badge_id!r} resolves to {employee}, whose employment status is {status} — "
			"a capture cannot be paid to somebody who is not employed"
		)

	shift = _clean(facts.get("shift"))
	on_shift = facts.get("on_shift")
	if shift and on_shift is not True:
		errors.append(
			f"badge {badge_id!r} resolves to {employee}, who is not clocked in on shift {shift} — "
			"add them with add_worker_to_shift before their buckets can be attributed to it"
		)

	return errors


# ── 7. fill_determination ────────────────────────────────────────────────
#
# v0.68.0. Container-Agnostic Fill Pipeline. NOT PAY, THE SAME WAY
# `coverage_percent` IS NOT PAY — see the module docstring's binary gate. What
# this answers is "does this capture's fill percentage sit inside the band a
# foreman set for this container type", which is quality control a foreman or
# checker reads; `verdict` on the entry is still the only thing that reaches a
# payroll slip and this function does not touch it.

FILL_UNKNOWN = "Unknown"
FILL_UNDERFILL = "Underfill"
FILL_OVERFILL = "Overfill"
FILL_PASS = "Pass"


def fill_determination(entry: dict, threshold: dict | None) -> dict:
	"""The fill-percentage arithmetic and threshold comparison for ONE entry, pure.

	`tools/fill_pipeline.py::get_fill_determination` is what fetches `entry`
	(a Bucket Log Entry, as a plain dict) and `threshold` (the current
	Container Fill Threshold row for that entry's `container_type`, or `None`
	if none is set yet) and calls in — this function reads no database.

	COMPUTED FROM AREAS WHEN BOTH ARE PRESENT, falling back to the stored
	`coverage_percent` otherwise. That order is deliberate: `mask_area_px` /
	`container_area_px` are the segmentation model's own numbers for THIS
	capture, and recomputing from them is what makes the result "the math
	behind it" rather than a number trusted verbatim. An entry synced before
	v0.68.0, or from a device that only ever sent `coverage_percent`, still
	gets an answer — just without the pixel-area explanation.

	`result` is one of `FILL_UNKNOWN` (no fill percentage could be determined,
	or no threshold is set for this container type yet), `FILL_UNDERFILL`,
	`FILL_OVERFILL` or `FILL_PASS`. `FILL_OVERFILL` can only be reached when
	the threshold carries an `upper_bound_pct` — a container type nobody has
	ever set one for (a cherry bucket) cannot overfill by construction, not by
	a special case naming it.
	"""
	entry = entry or {}
	mask = entry.get("mask_area_px")
	container = entry.get("container_area_px")

	computed_pct = None
	math_explanation = None
	if mask not in (None, "") and container not in (None, ""):
		mask_val = _as_float(mask, default=None)
		container_val = _as_float(container, default=None)
		if mask_val is not None and container_val is not None and container_val > 0:
			computed_pct = round((mask_val / container_val) * 100, 2)
			math_explanation = (
				f"{mask_val:g} mask px ÷ {container_val:g} container px × 100 = {computed_pct:g}%"
			)

	stored_pct = entry.get("coverage_percent")
	stored_val = _as_float(stored_pct, default=None) if stored_pct not in (None, "") else None
	fill_percentage = computed_pct if computed_pct is not None else stored_val

	result = {
		"entry": entry.get("name") or None,
		"entry_uuid": entry.get("entry_uuid") or None,
		"container_type": entry.get("container_type") or None,
		"mask_area_px": mask,
		"container_area_px": container,
		"computed_fill_percentage": computed_pct,
		"stored_coverage_percent": stored_pct if stored_pct not in (None, "") else None,
		"fill_percentage": fill_percentage,
		"math_explanation": math_explanation,
	}

	if fill_percentage is None:
		result["threshold_applied"] = None
		result["result"] = FILL_UNKNOWN
		result["explanation"] = (
			"no fill percentage is available for this entry — it carries neither "
			"mask_area_px/container_area_px nor coverage_percent"
		)
		return result

	if not threshold:
		result["threshold_applied"] = None
		result["result"] = FILL_UNKNOWN
		container_type = entry.get("container_type") or "(no container_type on this entry)"
		result["explanation"] = (
			f"no Container Fill Threshold is set for {container_type!r} yet — "
			"update_fill_threshold has not been called for it"
		)
		return result

	lower = _as_float(threshold.get("lower_bound_pct"), default=0.0) or 0.0
	upper_raw = threshold.get("upper_bound_pct")
	upper = _as_float(upper_raw, default=None) if upper_raw not in (None, "") else None

	if fill_percentage < lower:
		verdict = FILL_UNDERFILL
		explanation = f"{fill_percentage:g}% is below the {lower:g}% lower bound"
	elif upper is not None and fill_percentage > upper:
		verdict = FILL_OVERFILL
		explanation = f"{fill_percentage:g}% is above the {upper:g}% upper bound"
	elif upper is not None:
		verdict = FILL_PASS
		explanation = f"{fill_percentage:g}% is within the {lower:g}%–{upper:g}% band"
	else:
		verdict = FILL_PASS
		explanation = (
			f"{fill_percentage:g}% is at or above the {lower:g}% lower bound "
			"(this container type has no upper bound)"
		)

	result["threshold_applied"] = {
		"container_type": threshold.get("container_type"),
		"lower_bound_pct": lower,
		"upper_bound_pct": upper,
		"version": threshold.get("version"),
	}
	result["result"] = verdict
	result["explanation"] = explanation
	return result
