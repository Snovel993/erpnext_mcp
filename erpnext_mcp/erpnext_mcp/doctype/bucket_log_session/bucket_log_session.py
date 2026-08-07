# SPDX-License-Identifier: MIT
"""Controller for Bucket Log Session — light validation only. Totals are
computed by `tools/bucket_log.py::sync_bucket_entries` from
`bucket_bridge.aggregate_session`, not derived here, because the session is
saved before every entry that belongs to it necessarily exists yet — a sync
batch writes the session row and then its entries in the same call.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import shifts

DOCTYPE = "Bucket Log Session"


class BucketLogSession(Document):
	def autoname(self):
		year = str(self.started_at or frappe.utils.today())[:4]
		self.name = shifts.next_in_series(DOCTYPE, "BLS", year)

	def validate(self):
		if not self.session_uuid:
			frappe.throw(_("session_uuid is required. Nothing was saved."))
		if not self.company:
			frappe.throw(_("company is required. Nothing was saved."))
		if self.started_at and self.ended_at and str(self.ended_at) < str(self.started_at):
			frappe.throw(_("ended_at cannot be before started_at. Nothing was saved."))
