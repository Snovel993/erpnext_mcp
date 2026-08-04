# SPDX-License-Identifier: MIT
"""Controller for Inspection Session Section Submission — a child table, empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`farm_task_evidence.py` for the note and `test_packaging.py` for the release that
learned it the hard way.

WHETHER A SUBMISSION IS COMPLETE IS A QUESTION ABOUT THE TEMPLATE VERSION THE
SESSION PINNED, not about the row. A row cannot say whether the section it names
was required, what its evidence contract asked for, or whether another section of
the same visit produces the same compliance record for the same subject and must
therefore share it. All three of those are decided in `tools/sessions.py`, where
the pinned template and the whole submission are in hand.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class InspectionSessionSectionSubmission(Document):
	pass
