# SPDX-License-Identifier: MIT
"""Controller for Farm Task Template Checklist Item — a child table, empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with a
JSON and no module breaks `bench migrate` rather than degrading. That is why this
file exists at all; see `inspection_template_section.py` for the same note and
`test_packaging.py` for the release that learned it the hard way.

EVERY RULE ABOUT A CHECKLIST ITEM IS A RULE ABOUT THE TEMPLATE IT BELONGS TO.
Whether an item name is unique, whether the sort orders make a sequence, whether
a list with no required item is worth having — none of those is answerable from
one row, because a row does not know its siblings. The checking lives in
`farm_task_template.py`, where the whole list is in hand.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class FarmTaskTemplateChecklistItem(Document):
	pass
