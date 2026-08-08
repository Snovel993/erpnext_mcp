# SPDX-License-Identifier: MIT
"""Controller for Authorized Signer — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included; a folder with a
JSON and no module breaks `bench migrate`. See `i_9_reverification.py`, which
says the same thing for the same reason.

THE RULES ABOUT A SIGNER LIVE IN `tools/signers.py`, and none of them can be
decided from the row alone. Whether an account may sign an I-9 depends on this
row AND on whether the roster has any rows at all — an empty roster authorises
everybody, which is what an install does before anybody has configured
anything, and a row cannot see that it is the only one. Whether a name passed
by a caller may override this one depends on every other row. Splitting one
judgement across two places is how the two halves eventually disagree.
"""

from frappe.model.document import Document


class AuthorizedSigner(Document):
	pass
