# SPDX-License-Identifier: MIT
"""The OAR 437-004-1131 record for one shift, and the two reads of it.

v0.19.3. THREE TOOLS, AND THE FIRST ONE IS THE ANSWER TO ONE QUESTION AN OREGON
OSHA COMPLIANCE OFFICER ASKS: prove the July 15 crew complied. Before this the
best available answer was a policy document saying water is provided and a
handful of task completions with a temperature on them, which is an answer about
intentions and an answer about moments. Neither is an answer about the shift.

`create_heat_exposure_event` produces one record, anchored to one shift, that
says what was actually done — water at the required rate, shade within reach,
the rest cycle taken, the crew observed, the training current — with the crew
list, the compliance-event timeline and (from v0.19.4) the weather timeline
hanging off the shift behind it.

────────────────────────────────────────────────────────────────────────────
ONE PER SHIFT, AND THE SECOND CALL IS REFUSED
────────────────────────────────────────────────────────────────────────────

Two records about one exposure period will disagree, and the one an inspector
finds will be whichever was filed second. The doctype carries a unique index and
the controller refuses by name; this tool refuses BEFORE writing anything and
names the record that already exists, because "amend HEAT-2026-0004" is an
instruction somebody can follow and a duplicate-key error is not.

────────────────────────────────────────────────────────────────────────────
IT CHECKS THE TRAINING CLAIM AGAINST THE TRAINING REGISTER
────────────────────────────────────────────────────────────────────────────

`training_verified` is the one box on the form that another table on this site
can contradict. -1131 requires heat illness prevention training annually AND
before work at a site where the heat index will reach 80 °F, so the register
knows the answer: walk the crew, look for a current OR-OSHA-tagged record for
each of them, and say who is missing one.

WHAT HAPPENS WHEN THE REGISTER DISAGREES IS THE INTERESTING PART. A claim of
`training_verified=1` for a crew with an untrained worker is REFUSED — that is
the one direction where the record would assert something the same audit packet
disproves, and a packet that contradicts itself is worse than a packet with a
gap. A claim of `training_verified=0` is accepted and the missing names are
reported, because a shift that ran with an untrained worker happened and the
record of it is what an operation needs to have.

────────────────────────────────────────────────────────────────────────────
SUBMITTED, NOT SAVED
────────────────────────────────────────────────────────────────────────────

The signature is required, and submitting is what the signature means. A draft is
a record in progress — on the morning of a heat wave the foreman may not know yet
whether anybody will show signs — and the doctype deliberately lets a draft be
saved incomplete so the record is written at the time rather than assembled at
the end of the day from memory. This tool creates and submits in one call because
a caller with a signature in hand is a caller who is attesting.
"""

from __future__ import annotations

import frappe

from .. import compat, shifts, training
from ..args import as_bool, as_date, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import employee as employee_tool
from . import shifts as shift_tools

DOCTYPE = shifts.HEAT_DOCTYPE

RECORD_CAP = 500

#: The regime tag a heat illness prevention training record carries, and the
#: window it is current for. Read off `training` rather than restated, so the
#: crew check here and the `training_expiring` rule cannot disagree about whether
#: somebody's card is still good.
HEAT_REGIME = "OR-OSHA"

#: What the curriculum is called when this app seeds it. Matched
#: case-insensitively and by substring against the training type, because an
#: operation may file it as "Heat Illness Prevention (Spanish)" or "Heat Illness
#: Prevention — annual refresher" and all three are the same course.
HEAT_TRAINING_NAME = "heat illness"


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def _resolve_record(args: dict) -> dict:
	name = (
		as_str(args, "name") or as_str(args, "heat_exposure_event") or as_str(args, "record", required=True)
	).strip()
	if not frappe.db.exists(DOCTYPE, name):
		raise ToolError(
			f"no {DOCTYPE} called {name!r} on this site. list_heat_exposure_events has the "
			"register; a docname looks like HEAT-2026-0001."
		)
	return dict(
		frappe.db.get_value(DOCTYPE, name, compat.existing_fields(DOCTYPE, shifts.HEAT_FIELDS), as_dict=True)
		or {}
	)


def crew_training_status(crew: list, on_date: str) -> dict:
	"""Which of a crew had current heat illness training on the day, and which did not.

	Current AS OF THE DAY OF THE SHIFT, not as of today. A record that expired
	last week was current in July, and a check run against today's date would
	report a shift as non-compliant because of something that happened after it —
	which is the wrong answer to the only question anybody is asking about that
	shift.
	"""
	trained, untrained = [], []
	if not compat.doctype_exists(training.DOCTYPE):
		return {
			"available": False,
			"trained": [],
			"untrained": [],
			"note": (
				"This site has no Employee Training Record doctype, so the training claim on this "
				"record cannot be checked against anything. It is taken as given."
			),
		}
	for entry in crew or []:
		person = str(entry.get("employee") or "")
		if not person:
			continue
		current = False
		for row in training.rows({"employee": person}, limit=200):
			name = str(row.get("training_type") or "").lower()
			if HEAT_TRAINING_NAME not in name and HEAT_REGIME not in training.parse(row.get("regimes")):
				continue
			completed = str(row.get("completed_date") or "")
			if completed and completed > on_date:
				# Delivered after this shift. Evidence about a later shift.
				continue
			expires = str(row.get("expires_date") or "")
			if expires and expires < on_date:
				continue
			current = True
			break
		(trained if current else untrained).append(
			{"employee": person, "employee_name": entry.get("employee_name") or person}
		)
	return {"available": True, "trained": trained, "untrained": untrained}


# ── 1. create_heat_exposure_event ───────────────────────────────────────────
def create_heat_exposure_event(args: dict) -> ToolResult:
	"""Document OAR 437-004-1131 for one shift, signed and submitted."""
	_require()
	actor = employee_tool.require_hr_role()

	shift_name = (as_str(args, "farm_shift") or as_str(args, "shift", required=True)).strip()
	if not frappe.db.exists(shifts.DOCTYPE, shift_name):
		raise ToolError(
			f"no Farm Shift called {shift_name!r} on this site. A heat record documents an "
			"exposure period, and the period is the shift — list_shifts has the register. Nothing "
			"was created."
		)
	shift = dict(
		frappe.db.get_value(
			shifts.DOCTYPE, shift_name, compat.existing_fields(shifts.DOCTYPE, shifts.FIELDS), as_dict=True
		)
		or {}
	)
	employee_tool.require_company_scope(actor, str(shift.get("company") or ""))

	existing = shifts.heat_rows({"farm_shift": shift_name}, limit=2)
	if existing:
		raise ToolError(
			f"{existing[0]['name']} already documents shift {shift_name}. ONE SHIFT HAS AT MOST "
			"ONE HEAT RECORD: two records about one exposure period will disagree, and the one an "
			f"inspector finds will be whichever was filed second. Amend {existing[0]['name']} "
			"instead. Nothing was created."
		)

	signature = shift_tools.file_reference(
		as_str(args, "supervisor_signature_file_token") or as_str(args, "supervisor_signature"),
		"supervisor_signature_file_token",
	)
	if not signature:
		raise ToolError(
			"supervisor_signature_file_token is required. Submitting this record is the "
			"attestation — an unsigned heat record is a claim with nobody behind it, and this is "
			"the record a serious-injury investigation is read from. Upload the signature with "
			"stage_file_chunk and commit_staged_file, then pass its File docname. Nothing was "
			"created."
		)

	event_date = as_date(args, "event_date") or str(shift.get("start_datetime") or "")[:10]
	crew = shifts.crew_of(shift_name)
	status = crew_training_status(crew, event_date)

	signs = as_bool(args, "heat_illness_signs_observed", False)
	response = as_bool(args, "emergency_response_activated", False)
	notes = as_str(args, "notes")
	if signs and not response and not notes:
		raise ToolError(
			"this call says heat illness signs were observed and no emergency response was "
			"activated, and there are no notes. SIGNS SEEN AND NOTHING DONE IS THE SEQUENCE THAT "
			"KILLS PEOPLE. There are legitimate versions of it — the worker recovered in shade "
			"within minutes and declined further help is one — and every one of them is a "
			"sentence somebody can write. Pass emergency_response_activated=true, or write what "
			"happened in `notes`. Nothing was created."
		)

	verified = as_bool(args, "training_verified", False)
	if verified and status.get("available") and status["untrained"]:
		names = ", ".join(entry["employee_name"] for entry in status["untrained"])
		raise ToolError(
			f"training_verified=true, and {len(status['untrained'])} worker(s) on this crew have "
			f"no current heat illness prevention training on file as of {event_date}: {names}. "
			"THE SAME AUDIT PACKET CARRIES BOTH THIS RECORD AND THE TRAINING REGISTER, and a "
			"packet that contradicts itself is worse than a packet with a gap. Pass "
			"training_verified=false and say in `notes` who was untrained and how the shift was "
			"adjusted for them — a shift that ran that way happened, and the record of it is what "
			"the operation needs to have. Nothing was created."
		)

	plan = _acclimatization_argument(args.get("acclimatization_plan"), crew, shift_name)

	doc = frappe.new_doc(DOCTYPE)
	doc.farm_shift = shift_name
	doc.event_date = event_date
	doc.max_temp_f = args.get("max_temp_f")
	doc.max_heat_index_f = args.get("max_heat_index_f")
	doc.threshold_crossed_at = as_str(args, "threshold_crossed_at") or None
	doc.water_provided = 1 if as_bool(args, "water_provided", False) else 0
	doc.shade_provided = 1 if as_bool(args, "shade_provided", False) else 0
	doc.mandatory_rest_taken = 1 if as_bool(args, "mandatory_rest_taken", False) else 0
	doc.heat_illness_signs_observed = 1 if signs else 0
	doc.worker_reported_symptoms = 1 if as_bool(args, "worker_reported_symptoms", False) else 0
	doc.emergency_response_activated = 1 if response else 0
	doc.training_verified = 1 if verified else 0
	doc.supervisor_signature = signature
	doc.regulation_citation = as_str(args, "regulation_citation") or shifts.CITATION
	doc.notes = notes
	for person in plan:
		doc.append("acclimatization_plan", {"employee": person})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	doc.submit()

	described = shifts.describe_heat_event(dict(doc.as_dict()))
	gaps = shifts.heat_gaps(described)
	events = shifts.events_of(shift_name)

	data = {
		**described,
		"actor": actor,
		"shift": shifts.describe(shift),
		"crew_size": len(crew),
		"crew_training": {
			"checked_as_of": event_date,
			"available": status.get("available", True),
			"with_current_training": [entry["employee_name"] for entry in status.get("trained") or []],
			"without_current_training": [
				entry["employee_name"] for entry in status.get("untrained") or []
			],
		},
		"shift_timeline_events": len(events),
		"obligation_gaps": gaps,
		"note": (
			f"{doc.name} documents {shifts.CITATION} for shift {shift_name} on {event_date}, "
			"submitted and signed. An inspector asking 'prove that shift complied' now has one "
			"record with the crew list, the event timeline and the supervisor's signature behind "
			"it."
		),
	}
	if gaps:
		data["gap_note"] = (
			f"{len(gaps)} obligation(s) this record does NOT claim were met. Filed anyway — a day "
			"where the shade trailer broke down and the crew was sent home at eleven is a real "
			"shift with a real gap, and a system that would not let it be recorded would produce "
			"either a false record or no record. The honest one is worth more under investigation, "
			"and it is also the one that says what to fix."
		)
	if status.get("available") and status["untrained"]:
		data["training_note"] = (
			f"{len(status['untrained'])} worker(s) had no current heat illness prevention training "
			f"as of {event_date}: "
			+ ", ".join(entry["employee_name"] for entry in status["untrained"])
			+ f". {shifts.CITATION}(i) requires it annually and BEFORE work where the heat index "
			"will reach 80 °F. record_training files the retraining; the compliance calendar is "
			"already raising it."
		)
	if not events:
		data["timeline_note"] = (
			"THE SHIFT THIS DOCUMENTS HAS AN EMPTY EVENT TIMELINE. This record says the "
			"obligations were met and nothing on the shift shows when — no water break, no rest "
			"cycle, no observation. The checkboxes are an assertion and the timeline is the "
			"evidence for it, and an inspector will ask for the second. log_shift_event is how it "
			"gets there, at the time; it cannot be added convincingly afterwards."
		)
	if not described["max_heat_index_f"]:
		data["conditions_note"] = (
			"No maximum heat index is recorded, which is the number the rule actually turns on — "
			f"{shifts.CITATION} engages at a {shifts.HEAT_THRESHOLD_F:.0f} °F heat index and adds "
			f"obligations at {shifts.HEAT_HIGH_THRESHOLD_F:.0f} °F. It is entered by hand until "
			"v0.19.4 computes it from the shift's weather timeline."
		)
	return ToolResult(
		data=data,
		summary=(
			f"filed {doc.name} for shift {shift_name} on {event_date} "
			f"({len(gaps)} obligation gap(s), {len(crew)} on the crew)"
		),
		docstatus_delta="none → 1 (submitted)",
	)


def _acclimatization_argument(raw, crew: list, shift_name: str) -> list:
	"""Employee docnames for the plan, checked against the crew that worked the shift.

	REFUSED WHERE SOMEBODY IS NOT ON THE CREW, because -1131(g) asks for a plan
	for the unacclimatized workers ON THIS SHIFT — a plan naming somebody who was
	not there is a plan an inspector will read as filled in from a list rather
	than from the block, which is exactly the impression a real plan is fighting.
	"""
	if raw in (None, "", []):
		return []
	if isinstance(raw, str):
		raw = [part.strip() for part in raw.split(",") if part.strip()]
	if not isinstance(raw, (list, tuple)):
		raise ToolError(
			f"acclimatization_plan must be a list of employees. Got {type(raw).__name__}."
		)
	on_crew = {str(entry.get("employee")) for entry in crew or []}
	out = []
	for entry in raw:
		person = employee_tool.resolve_employee(
			str(entry.get("employee") if isinstance(entry, dict) else entry or "")
		)
		if on_crew and person not in on_crew:
			raise ToolError(
				f"{person} is on the acclimatization plan and not on the crew of {shift_name}. "
				f"{shifts.CITATION}(g) asks for a plan for the unacclimatized workers ON THIS "
				"SHIFT, and one naming somebody who was not there reads as filled in from a list "
				"rather than from the block. Nothing was created."
			)
		if person not in out:
			out.append(person)
	return out


# ── 2. list_heat_exposure_events ────────────────────────────────────────────
def list_heat_exposure_events(args: dict) -> ToolResult:
	"""The heat register: every documented hot shift, and what each one claims."""
	_require()
	actor = employee_tool.require_hr_role()
	limit = min(as_limit(args), RECORD_CAP)

	filters = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
		filters["company"] = company
	else:
		allowed = shift_tools._readable_companies(actor)
		if allowed:
			filters["company"] = ("in", allowed)

	from_date = as_date(args, "from_date")
	to_date = as_date(args, "to_date")
	if from_date and to_date:
		filters["event_date"] = ("between", [from_date, to_date])
	elif from_date:
		filters["event_date"] = (">=", from_date)
	elif to_date:
		filters["event_date"] = ("<=", to_date)

	found = shifts.heat_rows(filters, limit=max(limit * 2, limit))
	described = [shifts.describe_heat_event(row, with_plan=False) for row in found]

	if as_bool(args, "with_gaps_only", False):
		described = [entry for entry in described if shifts.heat_gaps(entry)]

	truncated = len(described) > limit
	described = described[:limit]

	unsigned = [entry["name"] for entry in described if not entry["supervisor_signed"]]
	untrained = [entry["name"] for entry in described if not entry["training_verified"]]
	incidents = [entry["name"] for entry in described if entry["heat_illness_signs_observed"]]

	data = {
		"company": company,
		"count": len(described),
		"limit": limit,
		"truncated": truncated,
		"records": described,
		"without_a_signature": unsigned,
		"without_verified_training": untrained,
		"with_signs_observed": incidents,
		"regulation_citation": shifts.CITATION,
		"note": (
			f"{len(incidents)} shift(s) where heat illness signs were observed. Those are the "
			"records an investigation reads first, and observing is the obligation working rather "
			"than failing."
			if incidents
			else "No shift in this selection recorded heat illness signs."
		),
	}
	if untrained:
		data["training_note"] = (
			f"{len(untrained)} record(s) do not confirm the whole crew had current heat illness "
			f"prevention training. {shifts.CITATION}(i) requires it annually AND before work where "
			"the heat index will reach 80 °F — each of these is a citation on the first hot "
			"morning rather than at the next inspection."
		)
	if truncated:
		data["truncation_note"] = (
			f"More than {limit} record(s) matched and this is the first {limit}. Narrow by company "
			"or period before relying on the counts above."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} heat exposure record(s)"
			+ (f" for {company}" if company else "")
			+ f"; {len(incidents)} with signs observed, {len(untrained)} without verified training"
		),
	)


# ── 3. get_heat_exposure_event ──────────────────────────────────────────────
def get_heat_exposure_event(args: dict) -> ToolResult:
	"""One heat record in full, with the shift's crew and timeline behind it."""
	_require()
	actor = employee_tool.require_hr_role()
	row = _resolve_record(args)
	employee_tool.require_company_scope(actor, str(row.get("company") or ""))

	described = shifts.describe_heat_event(row)
	gaps = shifts.heat_gaps(described)
	shift_name = str(row.get("farm_shift") or "")
	shift = (
		dict(
			frappe.db.get_value(
				shifts.DOCTYPE,
				shift_name,
				compat.existing_fields(shifts.DOCTYPE, shifts.FIELDS),
				as_dict=True,
			)
			or {}
		)
		if shift_name and frappe.db.exists(shifts.DOCTYPE, shift_name)
		else {}
	)

	data = {
		**described,
		"obligation_gaps": gaps,
		"shift": shifts.describe(shift, with_children=True) if shift else None,
		"evidence_chain": (
			f"{DOCTYPE} {row['name']} → Farm Shift {shift_name} → its crew, its compliance-event "
			"timeline and its weather timeline. That chain is the answer to 'prove that shift "
			"complied' — this record is the claim and the shift is the evidence for it."
		),
	}
	if gaps:
		data["gap_note"] = (
			"These are the obligations the record does not claim were met. Listed rather than "
			"fixed: a tick added now is a tick added now, and a record completed before an "
			"inspection is what an inspector is trained to spot."
		)
	if shift and not shifts.describe(shift, with_children=True)["compliance_events"]:
		data["timeline_note"] = (
			"The shift behind this record has an empty event timeline, so the checkboxes here are "
			"an assertion with nothing showing WHEN any of it happened. That is the gap an "
			"inspector opens next."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{row['name']} — {shifts.CITATION} for shift {shift_name} on "
			f"{described['event_date']}, {len(gaps)} obligation gap(s)"
			+ (", signs observed" if described["heat_illness_signs_observed"] else "")
		),
	)
