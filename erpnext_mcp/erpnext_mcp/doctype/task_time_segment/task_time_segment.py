# SPDX-License-Identifier: MIT
"""Controller for Task Time Segment — one stretch a worker was on a task.

THE ARITHMETIC LIVES IN `tools/dispatch.py`, which opens and closes segments as
a worker starts, pauses, resumes and finishes. What is checked here is only that
a closed segment is coherent: it cannot end before it started, and its minutes
cannot be negative. A segment with no end is the one that is still running, and
that is a legitimate state rather than a missing value.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class TaskTimeSegment(Document):
	def validate(self):
		if not self.started_at:
			frappe.throw(_("A time segment has to say when it started."))
		if self.ended_at and str(self.ended_at) < str(self.started_at):
			frappe.throw(
				_("This segment ends {0}, before it starts {1}.").format(self.ended_at, self.started_at)
			)
		if float(self.minutes or 0) < 0:
			frappe.throw(_("A time segment cannot have negative minutes."))
