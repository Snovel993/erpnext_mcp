# SPDX-License-Identifier: MIT
"""I-9 Audit Log controller — append-only, immutable after insertion.

The same pattern as MCP Action Log: `before_save` allows inserts and refuses
every subsequent write. Delete is allowed in permissions so operators can
prune under a retention policy; Frappe's own Deleted Document records each
deletion.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class I9AuditLog(Document):
	def before_save(self):
		if self.flags.in_insert:
			return
		frappe.throw(
			_("I-9 Audit Log rows are immutable. They record what happened and cannot be revised."),
			title=_("Append-Only Log"),
		)
