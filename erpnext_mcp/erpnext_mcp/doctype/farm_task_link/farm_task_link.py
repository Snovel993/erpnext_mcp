# SPDX-License-Identifier: MIT
"""Controller for Farm Task Link — one task's relationship to another.

THE BIDIRECTIONAL WRITE IS THE TOOL'S JOB, not this one's. `link_farm_tasks` and
`merge_farm_task` write a row on each side; a controller that tried to would have
to save the other parent from inside this one's validation, which is how Frappe
recursion bugs are made. What is checked here is that a link names something
other than its own parent.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class FarmTaskLink(Document):
	def validate(self):
		if not self.linked_task:
			frappe.throw(_("A link has to name the other task."))
		if self.parent and str(self.linked_task) == str(self.parent):
			frappe.throw(_("A task cannot be linked to itself."))
