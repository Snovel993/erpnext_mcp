# SPDX-License-Identifier: MIT
"""Controller for MCP Action Log — append-only, by force rather than by habit.

The doctype's permissions grant System Manager read and delete but not write, so
the UI offers no way to edit a row. This controller closes the other doors: a
script, a background job or a `frappe.get_doc(...).save()` in a console would
otherwise sail past a UI-only restriction, and a log an insider can quietly
rewrite is not an audit trail.

Delete is allowed on purpose. A busy site accumulates rows and an operator needs
to be able to prune them; Frappe records every deletion in its own Deleted
Document doctype, so a pruned row leaves a trace even so.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class MCPActionLog(Document):
	def before_save(self):
		"""Refuse any change to a row that already exists.

		`flags.in_insert` is the reliable "this is the initial write" signal —
		`is_new()` is already False by the time before_save runs, because
		`insert()` stamps `creation` before calling it.
		"""
		if self.flags.in_insert:
			return
		frappe.throw(
			_(
				"MCP Action Log rows are immutable — they are the audit trail of "
				"what an AI client did. Delete the row if you must, which is "
				"itself recorded."
			),
			title=_("Append-Only Log"),
		)
