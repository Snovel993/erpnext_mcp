# SPDX-License-Identifier: MIT
"""Controller for Compliance Regime Link — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. That is why this
file exists at all; see `farm_task_evidence.py` for the same note and
`test_packaging.py` for the release that learned it the hard way.

THERE IS NOTHING TRUE OF ONE OF THESE ROWS ON ITS OWN. A row says "this record
answers to WPS", and every rule about that claim — whether the token is one the
vocabulary knows, whether the set is deduplicated, what order it reads in, how
long the record therefore has to be kept — is a rule about the SET, which the row
cannot see. So all of it lives in `erpnext_mcp/training.py`, which is the one
place the whole app agrees about what a regime is, and the rows are written
through `training.set_rows` rather than appended by hand.
"""

from frappe.model.document import Document


class ComplianceRegimeLink(Document):
	pass
