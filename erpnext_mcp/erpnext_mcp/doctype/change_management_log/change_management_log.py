# SPDX-License-Identifier: MIT
"""Controller for Change Management Log — and the one rule that makes it a control.

AN APPROVER WHO IS THE PERSON WHO MADE THE CHANGE IS NOT AN APPROVER. That is
the entire content of change approval as a control, and it is checked here rather
than in the tool layer because a row written from the Desk has to obey it too. A
farm where the same person genuinely does both things records that as a known
exception — `approval_status = Not Required` with the reason in the notes — which
is a defensible answer. Naming yourself as your own approver is not, and the
difference between those two is the difference between a documented compensating
control and a finding.

`Not Required` IS A DECISION AND IS RECORDED AS ONE. A change nobody needed to
approve and a change whose approval nobody can find must not look the same in a
sample of five, so the vocabulary keeps them apart and the report counts them
apart.

DATES ARE CHECKED AGAINST THE CHANGE, NOT AGAINST THE CLOCK. An approval dated
before the change it approves is either a typo or a rubber stamp prepared in
advance, and both are worth refusing at the moment somebody writes them down.
"""

import frappe
from frappe import _
from frappe.model.document import Document

PENDING = "Pending"
APPROVED = "Approved"
REJECTED = "Rejected"
NOT_REQUIRED = "Not Required"

#: The change types where an approver is the point of the record. Others may
#: still carry one; these are what `get_change_management_report` samples and what
#: the `change_approval` control reads.
APPROVAL_EXPECTED = (
	"Permission",
	"Role Assignment",
	"User Account",
	"DocType Schema",
	"Tool Switch",
	"Integration",
	"Infrastructure",
)


class ChangeManagementLog(Document):
	def validate(self):
		self.title = str(self.title or "").strip()
		if not self.title:
			frappe.throw(_("Title is required — it is what somebody scanning a year of these reads."))

		if self.approval_status == APPROVED and not self.approved_by:
			frappe.throw(
				_(
					"This change is marked Approved with nobody named as the approver. An "
					"approval with no name on it is the finding change management exists to "
					"prevent — name the person, or set Approval Status to Not Required and say "
					"why in the notes."
				),
				title=_("Approved By Whom?"),
			)

		if self.approved_by and self.approved_by == self.changed_by:
			frappe.throw(
				_(
					"{0} is named as both the person who made this change and the person who "
					"approved it, which is not an approval. If one person genuinely does both "
					"here, record it honestly: set Approval Status to Not Required and write the "
					"compensating control in the notes. A documented exception is defensible; a "
					"self-approval is a finding."
				).format(self.changed_by),
				title=_("Self-Approval"),
			)

		if self.approved_on and self.change_date and str(self.approved_on) < str(self.change_date):
			frappe.throw(
				_(
					"Approved On ({0}) is before the change itself ({1}). Either the dates are "
					"the wrong way round, or the approval was prepared before the change was — "
					"and both are worth fixing while somebody still remembers which."
				).format(self.approved_on, self.change_date)
			)

		if self.approval_status in (APPROVED, REJECTED) and not self.approved_on:
			self.approved_on = frappe.utils.now()

	def needs_approval(self) -> bool:
		"""Whether the absence of an approver on this row is a finding."""
		return self.change_type in APPROVAL_EXPECTED and self.approval_status != NOT_REQUIRED
