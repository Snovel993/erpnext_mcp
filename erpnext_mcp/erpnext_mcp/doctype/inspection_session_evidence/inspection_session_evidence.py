# SPDX-License-Identifier: MIT
"""Controller for Inspection Session Evidence — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`farm_task_evidence.py`, which is this row's older sibling and says the same
thing about the same shape.

WHETHER A PHOTOGRAPH SATISFIES ANYTHING DEPENDS ON THE SECTION THAT ASKED FOR IT,
and the row does not know what the section asked for. The checking lives in
`tools/sessions.py`, where the template's contract and the worker's submission
are both in hand.
"""

from frappe.model.document import Document


class InspectionSessionEvidence(Document):
	pass
