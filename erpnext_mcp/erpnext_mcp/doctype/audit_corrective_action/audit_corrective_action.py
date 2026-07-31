# SPDX-License-Identifier: MIT
"""Controller for Audit Corrective Action — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. That is why this
file exists at all; see `asset_depreciation_posting.py` for the same note.

EVERY RULE ABOUT A CORRECTIVE ACTION IS A RULE ABOUT THE AUDIT IT BELONGS TO, so
they all live on the parent in `audit_event.py`. A row marked Closed with nothing
in `corrective_action` is refused there, because the judgement is "an auditor is
trained to disbelieve a tick in a box" and that is a statement about the audit's
credibility rather than about the row. A closure dated before the audit is refused
there, because the row does not know when the audit was. And "this audit cannot be
closed while an action is open" is by construction a statement about the whole
table.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class AuditCorrectiveAction(Document):
	pass
