# SPDX-License-Identifier: MIT
"""Controller for Farm Task Evidence — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. That is why this
file exists at all; see `audit_corrective_action.py` for the same note and
`test_packaging.py` for the release that learned it the hard way.

EVERY RULE ABOUT A PIECE OF EVIDENCE IS A RULE ABOUT THE COMPLETION IT BELONGS
TO. Whether a photograph satisfies a requirement depends on what the task asked
for, and the row does not know what the task asked for. So the checking lives in
`tools/dispatch.py`, where the contract and the submission are both in hand.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class FarmTaskEvidence(Document):
	pass
