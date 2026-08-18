# SPDX-License-Identifier: MIT
"""Controller for Farm Incident Record — one incident, from either direction.

RENAMED FROM "Discipline Record" IN v0.94.0, and the rename is the schema
admitting what the table already was. Both voices were on the record from the
start — `employee_statement` beside `manager_signature`, on one page — and the
only thing it could not express was the WORKER being the one who opens it. One
doctype with a direction field is not a shortcut around building a grievance
feature; it is the recognition that four fifths of it was already here under a
name that described one half.

WHAT THIS REFUSES, AND WHY IT IS SHORT. The controller checks the things that
would make a record indefensible on its face: no employee, no incident, no
expected improvement, no follow-up date, discipline dated before the incident it
is about, and a suspension whose end precedes its start. Everything about the
SHAPE OF THE CHAIN — whether this step follows the last one, whether a skip was
explained — lives in `tools/discipline.py`, where the call carries a claim about
who decided what. A controller that policed the chain would refuse a Desk
correction of a typo on a two-year-old record, which is not a defensible place
to be strict.

THE ACKNOWLEDGEMENT IS EITHER/OR AND THE CONTROLLER SAYS SO. A record marked
acknowledged with no signature and no explicit "declined to sign" is a record
claiming something happened that nothing evidences. An employee is entitled to
refuse; what the file may not contain is silence dressed up as agreement.
"""

import frappe
from frappe import _
from frappe.model.document import Document

VERBAL = "Verbal Warning"
WRITTEN = "Written Warning"
FINAL = "Final Warning"
SUSPENSION = "Suspension"
TERMINATION = "Termination"

#: The chain, in order. The index of a type in this tuple IS its severity, which
#: is what lets `tools/discipline.py` tell an escalation from a repeat from a
#: skip without a second table of rankings to keep in step.
DISCIPLINE_TYPES = (VERBAL, WRITTEN, FINAL, SUSPENSION, TERMINATION)

#: The two directions one incident-reporting protocol runs in.
#:
#: `SUPERVISOR_REPORT` is the farm documenting a worker — progressive discipline,
#: the chain that is the defence. `WORKER_REPORT` is a worker raising a grievance
#: or disputing something, which is the SAME protocol read the other way: either
#: party opens it, both sign, the resolution handles both.
#:
#: THE CHAIN IS SUPERVISOR-DIRECTION ONLY AND THAT IS LOAD-BEARING. `chain_for`,
#: `_gaps`, `get_incident_report` and the `prior_record` / `step_number`
#: assignment all filter on this, because a worker's own complaints appearing as
#: their disciplinary history in the document an HR manager hands a lawyer is
#: worse than the second table this design avoided.
SUPERVISOR_REPORT = "Supervisor Report"
WORKER_REPORT = "Worker Report"
DIRECTIONS = (SUPERVISOR_REPORT, WORKER_REPORT)

#: The shared resolution machine, in order. A SECOND lifecycle beside `status`
#: rather than an extension of it — see the field's own description on why
#: widening `status` would silently change every existing chain read.
REPORTED = "Reported"
ACKNOWLEDGED = "Acknowledged"
UNDER_REVIEW = "Under Review"
RESOLVED = "Resolved"
RESOLUTION_STATES = (REPORTED, ACKNOWLEDGED, UNDER_REVIEW, RESOLVED)

ACTIVE = "Active"
EXPIRED = "Expired"
RESCINDED = "Rescinded"
STATUSES = (ACTIVE, EXPIRED, RESCINDED)


def severity(discipline_type: str) -> int:
	"""How far up the chain a type sits, from 1. Zero for anything unrecognised."""
	try:
		return DISCIPLINE_TYPES.index(str(discipline_type)) + 1
	except ValueError:
		return 0


class FarmIncidentRecord(Document):
	def validate(self):
		if not str(self.employee or "").strip():
			frappe.throw(_("Employee is required — an incident record names a person."))
		self.employee = str(self.employee).strip()
		self.employee_name = str(self.employee_name or "").strip() or self.employee

		self.report_direction = str(self.report_direction or "").strip() or SUPERVISOR_REPORT
		if self.report_direction not in DIRECTIONS:
			frappe.throw(
				_("Report Direction {0} is not one of: {1}.").format(
					self.report_direction, ", ".join(DIRECTIONS)
				)
			)
		self.resolution_state = str(self.resolution_state or "").strip() or REPORTED
		if self.resolution_state not in RESOLUTION_STATES:
			frappe.throw(
				_("Resolution State {0} is not one of: {1}.").format(
					self.resolution_state, ", ".join(RESOLUTION_STATES)
				)
			)

		# THE DISCIPLINE FIELDS ARE THE SUPERVISOR DIRECTION'S. A worker's report
		# has no warning level, no "expected improvement" to demand of them, and
		# no follow-up review of their own conduct — discipline is an OUTCOME of
		# this protocol, not its container. Demanding those three of a grievance
		# would make the grievance unfileable, which is the failure this whole
		# direction exists to prevent.
		supervisor = self.report_direction == SUPERVISOR_REPORT

		if supervisor and self.discipline_type not in DISCIPLINE_TYPES:
			frappe.throw(
				_("Type {0} is not one of: {1}.").format(self.discipline_type, ", ".join(DISCIPLINE_TYPES))
			)
		if not supervisor and str(self.discipline_type or "").strip():
			frappe.throw(
				_(
					"A Worker Report carries no discipline type. This record is somebody "
					"raising something, and a warning level on it would file their own report "
					"as a step against them."
				)
			)
		self.status = str(self.status or ACTIVE).strip() or ACTIVE
		if self.status not in STATUSES:
			frappe.throw(_("Status {0} is not one of: {1}.").format(self.status, ", ".join(STATUSES)))

		if not str(self.incident_description or "").strip():
			frappe.throw(
				_(
					"What Happened is required, in specifics. 'Attitude problem' is what a claim "
					"wins on; a dated, described, witnessed incident is what it loses on."
				),
				title=_("The incident has to be described"),
			)

		if supervisor and not str(self.expected_improvement or "").strip():
			frappe.throw(
				_(
					"Expected Improvement is required. Progressive discipline is progressive "
					"because it gives somebody a chance to correct — a step that states no "
					"expectation is a punishment, and it is the half a claim most often finds "
					"missing."
				),
				title=_("Expected Improvement is required"),
			)

		if supervisor and not self.followup_date:
			frappe.throw(
				_(
					"Follow-Up Date is required. A warning nobody reviewed is the one a claim "
					"points at to show the process was theatre."
				),
				title=_("Follow-Up Date is required"),
			)

		if self.incident_date and self.issued_on and str(self.issued_on) < str(self.incident_date):
			frappe.throw(
				_("Issued On {0} is before the incident it is about ({1}).").format(
					self.issued_on, self.incident_date
				)
			)
		if self.followup_date and self.issued_on and str(self.followup_date) < str(self.issued_on):
			frappe.throw(
				_("Follow-Up {0} is before this step was issued ({1}).").format(
					self.followup_date, self.issued_on
				)
			)

		if self.suspension_start and self.suspension_end:
			if str(self.suspension_end) < str(self.suspension_start):
				frappe.throw(
					_("Suspension ends {0}, before it starts {1}.").format(
						self.suspension_end, self.suspension_start
					)
				)

		# READ THROUGH `cint`, NOT AS A TRUTH VALUE. A Frappe Check arrives as the
		# STRING "0" from a default, and `bool("0")` is True — the same trap
		# `settings.py` documents at length for the tool switches. Read naively,
		# every record created with defaults would look both acknowledged and
		# refused at once.
		acknowledged = bool(frappe.utils.cint(self.employee_acknowledged))
		declined = bool(frappe.utils.cint(self.employee_declined_to_sign))

		if acknowledged and declined:
			frappe.throw(
				_(
					"This record says the employee both acknowledged it and declined to sign it. "
					"Those are different outcomes and the file has to say which."
				),
				title=_("Contradictory acknowledgement"),
			)

		if acknowledged and not self.employee_signature and not declined:
			frappe.throw(
				_(
					"This record is marked acknowledged with no signature and no note that the "
					"employee declined to sign. An employee is entitled to refuse; what the file "
					"may not contain is silence presented as agreement."
				),
				title=_("Acknowledgement needs evidence"),
			)
