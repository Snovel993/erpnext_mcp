# SPDX-License-Identifier: MIT
"""Controller for Inspection Template Section — a child table, and empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. That is why this
file exists at all; see `farm_task_evidence.py` for the same note and
`test_packaging.py` for the release that learned it the hard way.

EVERY RULE ABOUT A SECTION IS A RULE ABOUT THE TEMPLATE IT BELONGS TO. Whether a
section name is unique, whether the order indexes make a sequence, whether two
sections would produce two contradictory records — none of those is answerable
from one row, because a row does not know its siblings. So the checking lives in
`inspection_template.py`, where the whole list is in hand.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class InspectionTemplateSection(Document):
	pass
