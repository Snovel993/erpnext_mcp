# SPDX-License-Identifier: MIT
"""Controller for Farm Shift Compliance Event — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`farm_task_evidence.py` for the same note.

WHAT IS NOT CHECKED HERE, AND WHY NOT AT ALL. An event outside the shift's own
period is the obvious candidate, and it is deliberately allowed: a water break
logged at 05:55 for a shift the foreman recorded as starting at 06:00 is a clock
five minutes out, not a false record, and refusing it would mean the break goes
unlogged rather than logged approximately. The reader is better served by an
event with an honest timestamp slightly outside the envelope than by no event.

`log_shift_event` reports an event that falls outside the shift rather than
refusing it, which is the same posture the training tools take towards a
§112.161 element that is missing: state the gap, keep the record.
"""

from frappe.model.document import Document


class FarmShiftComplianceEvent(Document):
	pass
