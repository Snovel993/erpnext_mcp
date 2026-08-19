# SPDX-License-Identifier: MIT
"""Controller for Backup Record — the job, and the restore that makes it a control.

WHAT IS REFUSED HERE IS THE RECORD THAT WOULD READ AS REASSURANCE AND MEAN
NOTHING. A test restore with a result and no date cannot be counted by any
window, so the verification control would either ignore it — and report a farm
that had done the work as having not — or count it forever, which is worse. A
completion before its own start is a clock somebody typed wrong, and it is the
figure an RTO is measured from.

THE STATUS AND THE RESTORE ARE INDEPENDENT ON PURPOSE. A failed job can still
have been restored from — that is what testing an older copy means — and a green
job that nobody has ever restored from is the ordinary and dangerous case. Tying
the two fields together would collapse exactly the distinction this doctype
exists to keep.
"""

import frappe
from frappe import _
from frappe.model.document import Document

SUCCESS = "Success"
PARTIAL = "Partial"
FAILED = "Failed"
IN_PROGRESS = "In Progress"

NOT_TESTED = "Not Tested"
PASS = "Pass"
RESTORE_PARTIAL = "Partial"
FAIL = "Fail"

#: Results that count as "somebody proved this copy is restorable". `Partial` is
#: deliberately not among them: a restore that recovered some of the data is a
#: finding with a silver lining, not a verification.
VERIFYING_RESULTS = (PASS,)


class BackupRecord(Document):
	def validate(self):
		self.location = str(self.location or "").strip()

		if self.completed_at and self.started_at and str(self.completed_at) < str(self.started_at):
			frappe.throw(
				_("Completed At ({0}) is before Started At ({1}).").format(self.completed_at, self.started_at)
			)

		if self.status != IN_PROGRESS and not self.completed_at and self.status == SUCCESS:
			# A successful job that never finished is a contradiction, and the
			# completion time is what a duration is measured from.
			frappe.throw(
				_(
					"This backup is marked Success with no Completed At. A job that succeeded "
					"finished at some point — record when, or set the status to In Progress."
				)
			)

		if self.test_restore_result in (PASS, RESTORE_PARTIAL, FAIL) and not self.test_restore_on:
			frappe.throw(
				_(
					"A test restore result of {0} was recorded with no date. An undated "
					"verification cannot be counted by any window, so it would either be ignored "
					"— reporting an operation that did the work as having not — or counted "
					"forever, which is worse. Record when it was done."
				).format(self.test_restore_result),
				title=_("Undated Verification"),
			)

		if frappe.utils.cint(self.retention_days) < 0:
			frappe.throw(_("Retention (Days) cannot be negative."))
		for field, label in (("rpo_hours", "RPO"), ("rto_hours", "RTO")):
			if frappe.utils.cint(self.get(field)) < 0:
				frappe.throw(_("{0} cannot be negative.").format(label))

	def verifies(self) -> bool:
		"""Whether this row is evidence that a restore actually works."""
		return self.test_restore_result in VERIFYING_RESULTS
