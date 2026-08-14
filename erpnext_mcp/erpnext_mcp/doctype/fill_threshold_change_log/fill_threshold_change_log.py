# SPDX-License-Identifier: MIT
"""Controller for Fill Threshold Change Log.

Append-only, same posture as Asset State Log: `update_fill_threshold` is the
only writer of a new row, and `acknowledge_threshold_update` is the only writer
of a row's `acknowledgments` child table afterward. Nothing here re-derives
`version` or the old/new bound columns — those are the tool layer's arithmetic,
carried over from Container Fill Threshold at the moment of the change, and a
controller re-deriving them from the CURRENT threshold row would make a change
log entry describe whatever the threshold happens to say now instead of what it
said when this row was written.
"""

from frappe.model.document import Document


class FillThresholdChangeLog(Document):
	pass
