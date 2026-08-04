# SPDX-License-Identifier: MIT
"""Controller for Audit Event — who came, what they found, and whether it got fixed.

AN OPERATION IS NOT JUDGED ON HAVING NO FINDINGS. Every audit produces findings;
a clean report usually means the auditor did not look hard. What an operation is
judged on is closing them, and being able to prove the closure on the next visit
when the same auditor asks what happened about last year's item four. The
corrective actions table is therefore the substance of this doctype and the rest
is context for it.

THE ONE INVARIANT WORTH ENFORCING IN A CONTROLLER: a closure date must not sit
over an open action. `corrective_actions_closed` is read by the audit packet
generator as "this audit is finished", and an audit marked finished with an open
Critical action is the most misleading single fact this app could record — it
would be assembled into a packet, handed to an auditor, and contradicted by the
first question. So it is refused here, in the controller, where it cannot be got
round by writing the field some other way.

CLOSING AN ACTION REQUIRES SAYING WHAT WAS DONE. A row marked Closed with an
empty `corrective_action` is a tick in a box, and a tick in a box is what an
auditor is specifically trained to disbelieve. Refused.

A CLOSED DATE BEFORE THE AUDIT DATE IS REFUSED. Fixing something before it was
found is not evidence of diligence, it is two dates transposed — and if the fix
genuinely predated the audit then the finding was wrong and belongs in the notes,
not in a closure that claims a response that never happened.

WHAT IS DELIBERATELY NOT ENFORCED: an overdue action. Whether an action is late
is a fact about today, and a controller only runs when somebody saves. The alert
engine reads the due dates against today every night and is right without anybody
touching the document — which is the same reason `Certification` does not flip
its own status when a date passes.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Row statuses that mean the action still needs doing.
OPEN_STATUSES = ("Open", "In Progress")

#: Row statuses that mean it does not.
SETTLED_STATUSES = ("Closed", "Not Applicable")


class AuditEvent(Document):
	def validate(self):
		self.audit_name = str(self.audit_name or "").strip()
		if not self.audit_name:
			frappe.throw(_("Audit Name is required — it is the docname."))

		rows = list(self.get("corrective_actions_required") or [])
		for index, row in enumerate(rows, start=1):
			status = str(row.get("status") or "Open")
			if status == "Closed":
				if not str(row.get("corrective_action") or "").strip():
					frappe.throw(
						_(
							"Corrective action {0} is marked Closed and does not say what was done. "
							"A tick in a box is what an auditor is trained to disbelieve — write "
							"what actually changed."
						).format(index),
						title=_("Closed With No Corrective Action"),
					)
				if not row.get("closed_date"):
					row.closed_date = frappe.utils.today()
			if row.get("closed_date") and self.audit_date:
				if str(row.closed_date) < str(self.audit_date):
					frappe.throw(
						_(
							"Corrective action {0} is recorded as closed on {1}, before the audit "
							"on {2} that raised it. If the fix genuinely predated the audit then "
							"the finding was wrong — say so in the notes rather than recording a "
							"response that never happened."
						).format(index, row.closed_date, self.audit_date),
						title=_("Closed Before The Audit"),
					)
			if status in SETTLED_STATUSES and status != "Closed":
				continue

		still_open = [
			index
			for index, row in enumerate(rows, start=1)
			if str(row.get("status") or "Open") in OPEN_STATUSES
		]
		if self.corrective_actions_closed and still_open:
			frappe.throw(
				_(
					"This audit is marked closed on {0} and corrective action(s) {1} are still "
					"open. That combination would be assembled into an audit packet as a finished "
					"audit and contradicted by the auditor's first question. Close the actions, "
					"or clear the closure date."
				).format(self.corrective_actions_closed, ", ".join(str(i) for i in still_open)),
				title=_("Closed With Open Actions"),
			)

		if self.corrective_actions_closed and self.audit_date:
			if str(self.corrective_actions_closed) < str(self.audit_date):
				frappe.throw(
					_("The closure date {0} is before the audit date {1}.").format(
						self.corrective_actions_closed, self.audit_date
					),
					title=_("Closed Before The Audit"),
				)

	def open_actions(self) -> list:
		"""Which corrective actions are still open, as (index, row).

		Public because `close_audit_event` and the alert engine both need the same
		answer, and two implementations of "still open" would eventually disagree
		about `Not Applicable`.
		"""
		return [
			(index, row)
			for index, row in enumerate(self.get("corrective_actions_required") or [], start=1)
			if str(row.get("status") or "Open") in OPEN_STATUSES
		]
