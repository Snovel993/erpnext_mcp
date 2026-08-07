# SPDX-License-Identifier: MIT
"""Controller for Bucket Log Entry — validation only. All arithmetic and shape
work lives in `erpnext_mcp/bucket_bridge.py`, the same split `ML Model`'s
controller keeps against `model_registry.py`; this file reuses
`bucket_bridge.validate_bucket_entry` rather than restating its checks, so a
record edited straight in the Desk gets the same guarantees `sync_bucket_entries`
gives one that arrived from a device.

THE YEAR IN THE SERIES IS THE CAPTURE'S OWN YEAR, not the sync date — the same
reasoning `shifts.next_in_series` gives for Farm Shift: a bucket picked on 31
December and synced on 1 January belongs to the year it was picked.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import bucket_bridge, shifts

DOCTYPE = "Bucket Log Entry"


class BucketLogEntry(Document):
	def autoname(self):
		year = str(self.timestamp or frappe.utils.today())[:4]
		self.name = shifts.next_in_series(DOCTYPE, "BLE", year)

	def validate(self):
		errors = bucket_bridge.validate_bucket_entry(self.as_dict())
		if errors:
			frappe.throw(_("{0} Nothing was saved.").format("; ".join(errors)))
		if not self.synced_at:
			self.synced_at = frappe.utils.now()
