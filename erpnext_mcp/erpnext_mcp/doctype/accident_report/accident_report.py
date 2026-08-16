# SPDX-License-Identifier: MIT
"""Controller for Accident Report — one incident, investigated over days.

WHAT THIS REFUSES IS DELIBERATELY LITTLE, AND THE REASON IS THE FIRST TEN
MINUTES. This record is opened on a phone at a scene, by somebody whose
attention is on a person on the ground. A controller that demanded a root cause,
a recordability determination and a follow-up date to save a row would produce
exactly one outcome: nobody would open the record until the evening, and the
account written in the evening is worth a fraction of the one written at the
scene.

So the controller checks only what would make the record incoherent — a report
of an incident with no time, reported before it happened, closed with nothing to
close — and the SEQUENCING rules live in `tools/accidents.py`, on the calls that
carry a claim about who determined what.

THE ONE THING IT DOES ENFORCE ABOUT CLOSING is that Closed means something.
29 CFR 1904 does not say when an investigation is finished, but an investigation
closed with no corrective action, no recordability determination and no date to
check the fix is one that will not survive being read — so those three are
required at the moment the status becomes Closed, and at no moment before it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

OPEN = "Open"
IN_PROGRESS = "In Progress"
CORRECTIVE_PENDING = "Corrective Actions Pending"
CLOSED = "Closed"

#: The investigation's own walk. Ordered, because "which way is forward" is a
#: question `tools/accidents.py` answers from this tuple rather than from a
#: second table of transitions.
STATUSES = (OPEN, IN_PROGRESS, CORRECTIVE_PENDING, CLOSED)

UNDETERMINED = "Undetermined"

#: Severities that make 1904.39's clock run — a call to OSHA within 8 hours for
#: a fatality, 24 for the rest. Listed here so the tool can say so rather than
#: leaving somebody to know it.
IMMEDIATELY_REPORTABLE = ("Fatality", "Hospitalisation", "Amputation", "Loss of Eye")


class AccidentReport(Document):
	def validate(self):
		self.status = str(self.status or OPEN).strip() or OPEN
		if self.status not in STATUSES:
			frappe.throw(_("Status {0} is not one of: {1}.").format(self.status, ", ".join(STATUSES)))

		if not self.occurred_at:
			frappe.throw(
				_(
					"Occurred At is required. Every clock this record is measured against — the "
					"gap to reporting, OSHA's 8 and 24 hours, the days-away count — runs from it."
				)
			)
		if not self.reported_at:
			self.reported_at = frappe.utils.now()
		if str(self.reported_at) < str(self.occurred_at):
			frappe.throw(
				_("Reported At {0} is before the incident happened ({1}).").format(
					self.reported_at, self.occurred_at
				)
			)

		if not str(self.incident_description or "").strip():
			frappe.throw(
				_(
					"What Happened is required. Everything else on this record can be filled in "
					"over the following days; the account of the incident is the one part that is "
					"worth less every hour it is not written."
				),
				title=_("The incident has to be described"),
			)

		for field, label in (
			("days_away_from_work", "Days Away From Work"),
			("days_restricted_duty", "Days on Restricted Duty"),
		):
			if int(self.get(field) or 0) < 0:
				frappe.throw(_("{0} cannot be negative.").format(label))

		if self.status == CLOSED:
			self._refuse_an_empty_closure()

	def _refuse_an_empty_closure(self) -> None:
		"""Closed has to mean something. Three things, and each has a reason.

		NOT ENFORCED BEFORE CLOSURE, on purpose. Every one of these is unknown at
		the scene and knowable only after the work, and requiring them earlier is
		what turns an investigation into a form nobody opens until it is finished
		— which is the same as not having one.
		"""
		if not str(self.corrective_actions or "").strip():
			frappe.throw(
				_(
					"An investigation cannot be closed with no corrective actions recorded. One "
					"that identified a cause and changed nothing is a filing exercise, and closing "
					"it is the moment that becomes permanent. Where the honest answer is that no "
					"change is needed, write that down and why."
				),
				title=_("Corrective Actions are required to close"),
			)
		if not self.followup_date:
			frappe.throw(
				_(
					"An investigation cannot be closed with no follow-up date. A fix nobody "
					"verified is a fix that gets undone the next time the line jams."
				),
				title=_("Follow-Up Date is required to close"),
			)
		if str(self.osha_recordable or UNDETERMINED) == UNDETERMINED:
			frappe.throw(
				_(
					"OSHA Recordability is still Undetermined. Closing an investigation without "
					"deciding whether it goes on the 300 log leaves the decision unmade rather "
					"than made — which is the state an inspector reads as a missing entry."
				),
				title=_("Recordability has to be determined to close"),
			)
