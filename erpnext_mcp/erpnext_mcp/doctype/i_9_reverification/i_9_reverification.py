# SPDX-License-Identifier: MIT
"""Controller for I-9 Reverification — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included; a folder with a
JSON and no module breaks `bench migrate`. See `certification_renewal.py`.

THE RULES ABOUT A REVERIFICATION LIVE ON THE PARENT, in `tools/i9.py`, and the
reason is the same one that keeps Certification Renewal's rule out of its own
controller: none of them can be decided from the row alone. Whether a new
expiration date is in the future is a fact about the row; whether recording it
closes a gap or documents one is a fact about the row AND the parent's current
`alien_work_authorization_expiry`, and whether this entry may be written at all
depends on the parent having a signed Section 2 in the first place. Splitting one
judgement across two controllers is how the two halves eventually disagree.
"""

from frappe.model.document import Document


class I9Reverification(Document):
	pass
