# SPDX-License-Identifier: MIT
"""The shift: what one is, how it is read, and the Attendance it produces.

v0.19.3. THE ARCHITECTURAL MOVE OF THE RELEASE IS THAT COMPLIANCE ANCHORS TO A
SHIFT RATHER THAN TO A TASK, and this module is the single place that knows what
that means, for the same reason `training.py` exists: the controller, the five
mutating tools, the four reads, the compliance rule and the audit packet builder
must not be able to disagree about what a shift's crew was or how long somebody
was on it.

────────────────────────────────────────────────────────────────────────────
WHY THE SHIFT AND NOT THE TASK
────────────────────────────────────────────────────────────────────────────

A task completion carries a point-in-time reading. A shift carries a timeline.
Oregon OSHA does not ask what the temperature was when one job closed; it asks
whether the July 15 shift complied with OAR 437-004-1131 from start to finish —
water at the required rate for the whole exposure, shade within reach, rest
cycles enforced once the heat index passed 80 °F, the supervisor observing for
signs. Every one of those is a statement about a PERIOD and about a CREW, and a
record scoped to a single job cannot make it.

The same shape serves the other exposure regimes without being redesigned. A
spray shift is a wind-speed timeline plus product and PPE, which is what WPS
asks for. A frost-protection irrigation shift is a soil-temperature timeline
plus valve changes. A ten-hour harvest is heat and hydration. One doctype, one
timeline, three regimes.

────────────────────────────────────────────────────────────────────────────
THE FOREMAN IS THE SOLE ACTOR, AND THAT IS A COMPLIANCE DECISION
────────────────────────────────────────────────────────────────────────────

Workers do not self-clock into a shift. The foreman forms the crew, adds and
removes people, logs the events and signs the close-out. Three reasons, and the
first is the one that decides it:

  * -1131 puts the obligations on a named responsible person — the one who calls
    the water break, who observes for signs, who decides a rest cycle is needed.
    The record has to carry that person's identity as a fact, not derive it.
  * FSMA §112.161(b) asks a supervisor to review and sign. Their identity must be
    ON the shift rather than inferred from whoever happened to be rostered.
  * Worker self-clock is the failure mode where nobody logged the water break
    because everybody assumed somebody else had. A crew of thirty with thirty
    people responsible for the record has nobody responsible for the record.

Some workers will not have a device in the field at all, which makes the point
moot in practice as well as in principle.

────────────────────────────────────────────────────────────────────────────
PER-WORKER ATTENDANCE SURVIVES INSIDE THE CREW ENVELOPE
────────────────────────────────────────────────────────────────────────────

The obvious objection to a crew-shaped record is that payroll is person-shaped,
and it would be a real objection if the crew table were a list of names. It is
not: every row carries `joined_at` and `left_at`, so "the crew worked 06:00 to
15:00" and "Ana joined at 07:10 and left at 13:00" are both true, both stored,
and neither derived from the other.

`remove_worker_from_shift` SETS `left_at`; it does not delete the row. Deleting
would destroy the only record that the person was on the shift at all, which is
the record a wage claim turns on.

────────────────────────────────────────────────────────────────────────────
THE ATTENDANCE BRIDGE RUNS ONE WAY, ON PURPOSE
────────────────────────────────────────────────────────────────────────────

When a shift closes, `bridge_to_attendance` writes one submitted Frappe HR
`Attendance` per crew member, spanning that person's own `joined_at` to their
own `left_at` — or to the shift's end where they stayed to it. So farm_hr keeps
one canonical answer to "when was Ana at work" without the shift stopping being
a single record, and `get_attendance_summary` counts a shift-formed day exactly
as it counts a hand-entered one.

IT DOES NOT RUN THE OTHER WAY, and that is not an omission. A shift is formed by
a foreman naming a crew, a location and a type; an Attendance row carries none of
those. Deriving shifts from attendance would invent a foreman, invent a location
and invent the boundary between two crews who worked the same day — three
fabrications on a record an inspector reads. Sites with existing attendance keep
it; shifts start when somebody starts forming them.

The bridge NEVER RAISES INTO THE CLOSE. A site without Frappe HR has no
Attendance doctype, an employee may have been archived since the shift ran, and
a duplicate row for a day somebody already keyed in by hand is a real
possibility. None of those is a reason to refuse to close a shift whose
supervisor has signed it — the signature is the compliance act and the payroll
row is the convenience — so every failure is REPORTED in the close-out's result
and none of them stops it.
"""

from __future__ import annotations

import frappe

from . import compat

DOCTYPE = "Farm Shift"
CREW_DOCTYPE = "Farm Shift Crew Member"
EVENT_DOCTYPE = "Farm Shift Compliance Event"
WEATHER_DOCTYPE = "Farm Shift Weather Reading"
HEAT_DOCTYPE = "Heat Exposure Event"
ACCLIMATIZATION_DOCTYPE = "Heat Acclimatization Worker"

ATTENDANCE_DOCTYPE = "Attendance"

#: The Custom Field the bridge writes on Attendance. Installed by
#: `compliance_fields.py` alongside the v0.15.0 fields, which is where every
#: column this app grafts onto somebody else's doctype is declared and where
#: `before_uninstall` finds it to warn about.
ATTENDANCE_SHIFT_FIELD = "farm_shift"

STATUS_ACTIVE = "Active"
STATUS_CLOSED = "Closed"
STATUS_CANCELLED = "Cancelled"

#: The Attendance status a shift-formed day produces. Present, always: a person
#: on a crew list was at work, and any other status is a fact somebody has to
#: assert rather than one a closed shift proves.
ATTENDANCE_STATUS = "Present"

#: The heat index Oregon's rule engages at, and the one it adds obligations at.
#: Here rather than in the rule that reads them, because v0.19.4's threshold hook
#: and the Heat Exposure Event's own notes both quote the same numbers, and a
#: threshold that lives in two files is a threshold that changes in one.
HEAT_THRESHOLD_F = 80.0
HEAT_HIGH_THRESHOLD_F = 90.0

#: OAR 437-004-1131(g): fewer than this many days working in the heat and the
#: worker needs an acclimatization plan.
ACCLIMATIZATION_DAYS = 14

CITATION = "OAR 437-004-1131"


# ── naming ──────────────────────────────────────────────────────────────────
def next_in_series(doctype: str, prefix: str, year: str, width: int = 4) -> str:
	"""`PREFIX-YYYY-0001`, counted from what is already on the site.

	EXPANDED HERE RATHER THAN BY FRAPPE'S `naming_series` HOOK. Both doctypes
	declare their series as a field so the Desk shows it and an operator can read
	what the shape is meant to be, but the expansion is this function — which
	means a docname is the same string on a bench, in a patch, and in the
	standalone suite, and a test asserting `SHIFT-2026-0001` is asserting the
	thing that ships.

	The year is the record's own year, not this year. A shift that ran on 31
	December and was closed on 1 January belongs to the year it started, and a
	series keyed off `today()` would file it under the wrong one for ever.
	"""
	head = f"{prefix}-{year}-"
	existing = frappe.db.get_all(doctype, filters={"name": ("like", f"{head}%")}, pluck="name", limit=100000)
	highest = 0
	for name in existing or []:
		tail = str(name).rsplit("-", 1)[-1]
		if tail.isdigit():
			highest = max(highest, int(tail))
	return f"{head}{highest + 1:0{width}d}"


# ── status ──────────────────────────────────────────────────────────────────
def status_for(end_datetime, cancelled=False) -> str:
	"""Active, Closed or Cancelled, from the two facts that decide it.

	No end time means the shift is still running, whatever anybody ticked — an
	open shift is what the v0.19.4 weather sweep walks, and a shift marked Closed
	with no end would silently drop out of the fetch while still being worked.
	That ordering is the whole of the rule.
	"""
	if not str(end_datetime or "").strip():
		return STATUS_ACTIVE
	return STATUS_CANCELLED if compat.checked(cancelled) else STATUS_CLOSED


def is_open(row: dict) -> bool:
	return not str(row.get("end_datetime") or "").strip()


# ── reading ─────────────────────────────────────────────────────────────────
FIELDS = (
	"name",
	"foreman",
	"foreman_name",
	"company",
	"location",
	"farm_location_gps",
	"shift_type",
	"start_datetime",
	"end_datetime",
	"cancelled",
	"cancellation_reason",
	"status",
	"foreman_notes",
	"supervisor_review_signature",
	"supervisor_review_on",
)

CREW_FIELDS = ("name", "employee", "employee_name", "role", "joined_at", "left_at", "notes", "idx")

EVENT_FIELDS = (
	"name",
	"event_type",
	"event_datetime",
	"logged_by",
	"description",
	"producer_record_doctype",
	"producer_record_name",
	"weather_snapshot_temp_f",
	"weather_snapshot_heat_index_f",
	"evidence_file",
	"idx",
)

WEATHER_FIELDS = (
	"name",
	"reading_datetime",
	"temp_f",
	"heat_index_f",
	"humidity_pct",
	"wind_speed_mph",
	"wind_direction_deg",
	"precipitation_mm",
	"source",
	"fetched_at",
	"idx",
)

HEAT_FIELDS = (
	"name",
	"farm_shift",
	"foreman",
	"company",
	"event_date",
	"max_temp_f",
	"max_heat_index_f",
	"threshold_crossed_at",
	"water_provided",
	"shade_provided",
	"mandatory_rest_taken",
	"heat_illness_signs_observed",
	"worker_reported_symptoms",
	"emergency_response_activated",
	"training_verified",
	"supervisor_signature",
	"supervisor_signed_on",
	"regulation_citation",
	"notes",
	"docstatus",
)


def rows(filters: dict, limit: int = 2000, order_by: str = "start_datetime desc") -> list:
	"""Farm Shifts, selecting only the columns this site actually has."""
	if not compat.doctype_exists(DOCTYPE):
		return []
	return [
		dict(row)
		for row in frappe.db.get_all(
			DOCTYPE,
			filters=filters or {},
			fields=compat.existing_fields(DOCTYPE, FIELDS),
			order_by=order_by,
			limit=limit,
		)
		or []
	]


def heat_rows(filters: dict, limit: int = 2000, order_by: str = "event_date desc") -> list:
	if not compat.doctype_exists(HEAT_DOCTYPE):
		return []
	return [
		dict(row)
		for row in frappe.db.get_all(
			HEAT_DOCTYPE,
			filters=filters or {},
			fields=compat.existing_fields(HEAT_DOCTYPE, HEAT_FIELDS),
			order_by=order_by,
			limit=limit,
		)
		or []
	]


def _child_rows(doctype: str, fields, parent: str, parentfield: str, order_by: str) -> list:
	if not compat.doctype_exists(doctype):
		return []
	return [
		dict(row)
		for row in frappe.db.get_all(
			doctype,
			filters={"parent": parent, "parenttype": DOCTYPE, "parentfield": parentfield},
			fields=compat.existing_fields(doctype, fields),
			order_by=order_by,
			limit=2000,
		)
		or []
	]


def crew_of(shift: str) -> list:
	"""The crew rows of one shift, in the order the foreman built them."""
	return _child_rows(CREW_DOCTYPE, CREW_FIELDS, shift, "crew", "idx asc")


def events_of(shift: str) -> list:
	"""The compliance events of one shift, IN TIME ORDER.

	Ordered by when they happened rather than by the order somebody entered them,
	because the thing being read is a timeline and an event logged late in the
	afternoon about the morning belongs where it happened.
	"""
	return sorted(
		_child_rows(EVENT_DOCTYPE, EVENT_FIELDS, shift, "compliance_events", "idx asc"),
		key=lambda row: str(row.get("event_datetime") or ""),
	)


def weather_of(shift: str) -> list:
	"""The weather timeline of one shift. Empty in v0.19.3 — see the doctype."""
	return sorted(
		_child_rows(WEATHER_DOCTYPE, WEATHER_FIELDS, shift, "weather_timeline", "idx asc"),
		key=lambda row: str(row.get("reading_datetime") or ""),
	)


def acclimatization_of(heat_event: str) -> list:
	"""The Employee docnames on one heat record's acclimatization plan."""
	if not compat.doctype_exists(ACCLIMATIZATION_DOCTYPE):
		return []
	return [
		str(row.get("employee"))
		for row in frappe.db.get_all(
			ACCLIMATIZATION_DOCTYPE,
			filters={"parent": heat_event, "parenttype": HEAT_DOCTYPE},
			fields=["employee", "idx"],
			order_by="idx asc",
			limit=2000,
		)
		or []
		if row.get("employee")
	]


# ── describing ──────────────────────────────────────────────────────────────
def describe_crew_row(row: dict, shift_end: str = "") -> dict:
	"""One crew member, with the span the Attendance bridge will use.

	`present_until` is the honest reading of an empty `left_at`: they were there
	to the end. It is COMPUTED and reported rather than written back onto the row,
	because writing it would destroy the distinction between "left at 13:00" and
	"stayed to the end" the moment the end time ever changed.
	"""
	left = str(row.get("left_at") or "") or None
	return {
		"employee": row.get("employee"),
		"employee_name": row.get("employee_name"),
		"role": row.get("role") or "Worker",
		"joined_at": str(row.get("joined_at") or "") or None,
		"left_at": left,
		"present_until": left or (str(shift_end or "") or None),
		"left_early": bool(left),
		"notes": row.get("notes") or None,
	}


def describe_event_row(row: dict) -> dict:
	producer = str(row.get("producer_record_doctype") or "").strip()
	name = str(row.get("producer_record_name") or "").strip()
	return {
		"event_type": row.get("event_type"),
		"event_datetime": str(row.get("event_datetime") or "") or None,
		"logged_by": row.get("logged_by") or None,
		"description": row.get("description") or None,
		"producer_record": f"{producer} {name}".strip() or None,
		"producer_record_doctype": producer or None,
		"producer_record_name": name or None,
		"temp_f": row.get("weather_snapshot_temp_f"),
		"heat_index_f": row.get("weather_snapshot_heat_index_f"),
		"evidence_attached": bool(row.get("evidence_file")),
	}


def describe(row: dict, with_children: bool = False) -> dict:
	"""One shift in the shape every tool and packet reports it."""
	name = str(row.get("name") or "")
	end = str(row.get("end_datetime") or "") or None
	out = {
		"name": name,
		"foreman": row.get("foreman"),
		"foreman_name": row.get("foreman_name"),
		"company": row.get("company"),
		"location": row.get("location") or None,
		"farm_location_gps": row.get("farm_location_gps") or None,
		"shift_type": row.get("shift_type") or None,
		"start_datetime": str(row.get("start_datetime") or "") or None,
		"end_datetime": end,
		"open": end is None,
		"status": status_for(end, row.get("cancelled")),
		"cancelled": compat.checked(row.get("cancelled")),
		"cancellation_reason": row.get("cancellation_reason") or None,
		"supervisor_reviewed": bool(row.get("supervisor_review_signature")),
		"supervisor_review_on": str(row.get("supervisor_review_on") or "") or None,
		"foreman_notes": row.get("foreman_notes") or None,
	}
	if not with_children or not name:
		return out
	crew = crew_of(name)
	events = events_of(name)
	readings = weather_of(name)
	out["crew"] = [describe_crew_row(entry, end or "") for entry in crew]
	out["crew_size"] = len(crew)
	out["still_on_shift"] = len([entry for entry in crew if not entry.get("left_at")])
	out["compliance_events"] = [describe_event_row(entry) for entry in events]
	out["compliance_event_count"] = len(events)
	out["weather_timeline"] = readings
	out["weather_reading_count"] = len(readings)
	return out


def describe_heat_event(row: dict, with_plan: bool = True) -> dict:
	name = str(row.get("name") or "")
	out = {
		"name": name,
		"farm_shift": row.get("farm_shift"),
		"foreman": row.get("foreman"),
		"company": row.get("company"),
		"event_date": str(row.get("event_date") or "") or None,
		"max_temp_f": row.get("max_temp_f"),
		"max_heat_index_f": row.get("max_heat_index_f"),
		"threshold_crossed_at": str(row.get("threshold_crossed_at") or "") or None,
		"water_provided": compat.checked(row.get("water_provided")),
		"shade_provided": compat.checked(row.get("shade_provided")),
		"mandatory_rest_taken": compat.checked(row.get("mandatory_rest_taken")),
		"heat_illness_signs_observed": compat.checked(row.get("heat_illness_signs_observed")),
		"worker_reported_symptoms": compat.checked(row.get("worker_reported_symptoms")),
		"emergency_response_activated": compat.checked(row.get("emergency_response_activated")),
		"training_verified": compat.checked(row.get("training_verified")),
		"supervisor_signed": bool(row.get("supervisor_signature")),
		"supervisor_signed_on": str(row.get("supervisor_signed_on") or "") or None,
		"regulation_citation": row.get("regulation_citation") or CITATION,
		"submitted": int(row.get("docstatus") or 0) == 1,
		"notes": row.get("notes") or None,
	}
	if with_plan and name:
		plan = acclimatization_of(name)
		out["acclimatization_plan"] = plan
		out["unacclimatized_workers"] = len(plan)
	return out


def heat_gaps(described: dict) -> list:
	"""Which -1131 obligations this record does NOT claim were met.

	REPORTED, NEVER REFUSED — the same posture `training.fsma_161_gaps` takes and
	for the same reason. A shift where the shade trailer broke down and the crew
	was sent home at eleven is a shift with a real gap, and a system that would not
	let the gap be recorded would produce either a false record or no record. The
	honest one is worth more under investigation than a row of ticks, and it is
	also the one that tells somebody what to fix.
	"""
	gaps = []
	if not described.get("water_provided"):
		gaps.append(
			f"{CITATION}(f) — the record does not claim drinking water was provided at the "
			"required rate. This is the first thing an inspector asks about and the first "
			"thing a crew remembers"
		)
	if not described.get("shade_provided"):
		gaps.append(f"{CITATION}(e) — the record does not claim shade was available and accessible")
	if not described.get("mandatory_rest_taken"):
		gaps.append(
			f"{CITATION}(h) — the record does not claim the preventative cool-down rest cycle "
			"was taken. Offered is not taken, and a crew on a piece rate declining a break is "
			"the failure the requirement exists for"
		)
	if not described.get("heat_illness_signs_observed") and not described.get("worker_reported_symptoms"):
		# NOT a gap in itself — a shift where nobody showed signs is the ordinary
		# and desirable case. It is reported only so a reader knows the two
		# observation columns were answered rather than skipped.
		pass
	if not described.get("training_verified"):
		gaps.append(
			f"{CITATION}(i) — the record does not confirm every worker on the crew had current "
			"heat illness prevention training. The rule requires it annually AND before work at "
			"a site where the heat index will reach 80 °F, so a gap here is a citation on the "
			"first hot morning rather than at the next inspection"
		)
	if not described.get("supervisor_signed"):
		gaps.append(
			"FSMA §112.161(b) / -1131 supervision — no supervisor signature is attached, so "
			"nobody's name is against these claims"
		)
	return gaps


# ── the Attendance bridge ───────────────────────────────────────────────────
def attendance_span(crew_row: dict, shift_end: str) -> tuple:
	"""(in_time, out_time) for one crew member on a closing shift.

	Their own joined_at to their own left_at, falling back to the shift's end
	where they stayed to it. NOT the shift's own span: a worker who arrived an
	hour late and left two hours early worked six hours of a nine-hour shift, and
	an Attendance row claiming nine is a wage record that is wrong in the
	employer's favour — which is the direction that gets litigated.
	"""
	joined = str(crew_row.get("joined_at") or "").strip()
	left = str(crew_row.get("left_at") or "").strip() or str(shift_end or "").strip()
	return (joined or None, left or None)


def hours_between(start: str, end: str):
	if not start or not end:
		return None
	try:
		seconds = float(frappe.utils.time_diff_in_seconds(end, start))
	except Exception:
		return None
	if seconds < 0:
		return None
	return round(seconds / 3600.0, 2)


def bridge_to_attendance(shift: dict, crew: list) -> dict:
	"""One submitted Attendance per crew member for a shift that has just closed.

	NEVER RAISES. See the module docstring: the supervisor's signature is the
	compliance act and the payroll row is the convenience, so a site without
	Frappe HR, an employee archived since the shift ran, and a day somebody
	already keyed in by hand all produce a REPORTED skip rather than a refusal to
	close a signed shift.

	A row already carrying this shift is left exactly as it is, so closing an
	amended shift twice does not double somebody's day.
	"""
	report = {"created": [], "skipped": [], "failed": [], "available": True}
	if not compat.doctype_exists(ATTENDANCE_DOCTYPE):
		report["available"] = False
		report["note"] = (
			"This site has no Attendance DocType, so no payroll rows were written. The shift is "
			"closed and its crew spans are on the shift itself — install Frappe HR and the bridge "
			"works on the next close. Nothing about the compliance record depends on it."
		)
		return report

	has_shift_field = compat.has_field(ATTENDANCE_DOCTYPE, ATTENDANCE_SHIFT_FIELD)
	end = str(shift.get("end_datetime") or "")
	date = end[:10] or str(shift.get("start_datetime") or "")[:10]

	for row in crew or []:
		employee = str(row.get("employee") or "")
		if not employee:
			continue
		joined, left = attendance_span(row, end)
		try:
			if has_shift_field and frappe.db.exists(
				ATTENDANCE_DOCTYPE,
				{ATTENDANCE_SHIFT_FIELD: shift.get("name"), "employee": employee},
			):
				report["skipped"].append(
					{
						"employee": employee,
						"reason": "an Attendance row for this employee already names this shift",
					}
				)
				continue
			doc = frappe.new_doc(ATTENDANCE_DOCTYPE)
			doc.employee = employee
			if compat.has_field(ATTENDANCE_DOCTYPE, "employee_name"):
				doc.employee_name = row.get("employee_name") or employee
			doc.attendance_date = str(joined or "")[:10] or date
			doc.status = ATTENDANCE_STATUS
			if compat.has_field(ATTENDANCE_DOCTYPE, "company"):
				doc.company = shift.get("company")
			if compat.has_field(ATTENDANCE_DOCTYPE, "in_time"):
				doc.in_time = joined
			if compat.has_field(ATTENDANCE_DOCTYPE, "out_time"):
				doc.out_time = left
			hours = hours_between(joined or "", left or "")
			if hours is not None and compat.has_field(ATTENDANCE_DOCTYPE, "working_hours"):
				doc.working_hours = hours
			if has_shift_field:
				doc.set(ATTENDANCE_SHIFT_FIELD, shift.get("name"))
			doc.flags.ignore_permissions = True
			doc.insert(ignore_permissions=True)
			# SUBMITTED, because `get_attendance_summary` counts docstatus 1 only —
			# a draft row is not a fact about whether somebody turned up, and a
			# bridge that left drafts would produce a payroll register that looked
			# empty for every shift-formed day.
			doc.submit()
			report["created"].append(
				{
					"attendance": doc.name,
					"employee": employee,
					"employee_name": row.get("employee_name") or employee,
					"in_time": joined,
					"out_time": left,
					"hours": hours,
				}
			)
		except Exception as exc:  # pragma: no cover - a half-migrated HR app
			report["failed"].append({"employee": employee, "error": f"{type(exc).__name__}: {exc}"})

	if not has_shift_field:
		report["link_note"] = (
			f"Attendance has no `{ATTENDANCE_SHIFT_FIELD}` column on this site, so the rows written "
			"do not point back at the shift. It is a Custom Field this app installs — run `bench "
			"--site <site> migrate`, or tick Install Compliance Fields on ERPNext MCP Settings if "
			"somebody turned it off. Until then the bridge cannot tell its own rows from a "
			"hand-keyed day, so re-closing an amended shift would duplicate them."
		)
	if report["failed"]:
		report["failure_note"] = (
			f"{len(report['failed'])} Attendance row(s) could not be written and the shift is "
			"closed anyway. The supervisor's signature is the compliance act and the payroll row "
			"is the convenience — refusing the close would have lost the first to save the second. "
			"The spans are on the shift's own crew table either way."
		)
	return report
