# SPDX-License-Identifier: MIT
"""Controller for Training Session Attendee — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`inspection_session_evidence.py`, which is this row's older sibling and says the
same thing about the same shape.

WHETHER A ROW IS READY TO BECOME A TRAINING RECORD is not a question this row can
answer, and deliberately so. It needs the badge, the signature, the `attended`
tick AND the session's own regimes and topics — three of those are on the parent
— so the readiness rule lives in `erpnext_mcp/training_sessions.py`, where all of
it is in hand at once, and `complete_training_session` is the only thing that
applies it. A row that decided for itself would be a second opinion nobody
reconciles.
"""

from frappe.model.document import Document


class TrainingSessionAttendee(Document):
	pass
