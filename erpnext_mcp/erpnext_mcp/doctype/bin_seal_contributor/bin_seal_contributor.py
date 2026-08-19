# SPDX-License-Identifier: MIT
"""Controller for Bin Seal Contributor — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. See
`farm_shift_compliance_event.py` for the same note.

WHAT IS NOT CHECKED HERE. That the contributors' bucket counts sum to the bin's
`bucket_count`. They are allowed to disagree and the disagreement is the record:
a bucket tipped by somebody whose badge did not scan is in the bin and not in
these rows, and a controller that balanced the two would delete the only trace of
it. `Bin Seal` REPORTS the difference — see its own controller — which is the
posture this app takes everywhere a measured figure meets a counted one.
"""

from frappe.model.document import Document


class BinSealContributor(Document):
	pass
