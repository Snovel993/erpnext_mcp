# SPDX-License-Identifier: MIT
"""Controller for Regulatory Filing — what went to an agency, and what came back.

A FILING NOBODY CAN PROVE WAS MADE IS A FILING THAT WAS NOT MADE. That is not
rhetoric; it is how the penalty is assessed. The agency's position is that they
have no record, and "we sent it" is worth nothing against that without a
submission date, a confirmation number and the document. So the three fields that
carry the proof are the three this controller is strict about.

STATUS AND SUBMISSION DATE HAVE TO AGREE. A filing marked Submitted with no
submission date is the most common way this record goes wrong — somebody creates
it while preparing the filing and never comes back. It is refused, because a
half-filled filing record is more dangerous than none: an audit packet would
include it and an auditor would read it as evidence of something that may not
have happened.

THE OTHER DIRECTION IS ALLOWED. A Draft with no dates is exactly what a filing
being prepared looks like, and refusing that would mean the record could not be
created until the moment it was sent — which is the moment nobody has time to
create records.

RESPONSE DATES ARE CHECKED AGAINST THE SUBMISSION, NOT AGAINST TODAY. A response
received before the filing was submitted is a transposition; a response received
in the future is somebody typing next year. Both are refused. Whether a response
is late is a question for the alert engine, which reads dates against today every
night and does not need this document saved to notice.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Statuses that assert the filing actually went. Each one requires a date.
SUBMITTED_STATUSES = ("Submitted", "Accepted", "Rejected", "Amended")


class RegulatoryFiling(Document):
	def validate(self):
		self.filing_name = str(self.filing_name or "").strip()
		if not self.filing_name:
			frappe.throw(_("Filing Name is required — it is the docname."))

		status = str(self.status or "Submitted")
		if status in SUBMITTED_STATUSES and not self.submission_date:
			frappe.throw(
				_(
					"A filing marked {0} has to say when it was submitted. The submission date is "
					"what proves the filing was made — without it this record asserts something "
					"an agency's 'we have no record of that' beats. Set the date, or set the "
					"status to Draft until it has actually gone."
				).format(status),
				title=_("Submitted With No Date"),
			)

		if self.response_received_date and self.submission_date:
			if str(self.response_received_date) < str(self.submission_date):
				frappe.throw(
					_(
						"The response is dated {0} and the filing was submitted on {1} — they "
						"answered before it was sent. Two dates transposed."
					).format(self.response_received_date, self.submission_date),
					title=_("Response Before Submission"),
				)

		if self.response_due_date and self.submission_date:
			if str(self.response_due_date) < str(self.submission_date):
				frappe.throw(
					_(
						"The response is due {0} and the filing was submitted on {1} — the "
						"deadline had passed before it was sent."
					).format(self.response_due_date, self.submission_date),
					title=_("Response Due Before Submission"),
				)

		if self.submission_date and str(self.submission_date) > str(frappe.utils.today()):
			frappe.throw(
				_(
					"The submission date {0} is in the future. A filing is recorded when it goes, "
					"not when it is planned — keep it as a Draft until then."
				).format(self.submission_date),
				title=_("Submitted In The Future"),
			)
