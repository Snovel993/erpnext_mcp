# SPDX-License-Identifier: MIT
"""Controller for Agricultural UOM Context Entry — a child table, empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with
a JSON and no module breaks `bench migrate` rather than degrading. See
`crop_variety.py` for the same note.

EVERY RULE ABOUT AN ENTRY NEEDS ITS SIBLINGS. Whether a unit is listed twice,
whether two rows both claim to be the default, whether the list holds together
as one measurement — none is answerable from one row. All three live in
`agricultural_uom_context.py`.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class AgriculturalUOMContextEntry(Document):
	pass
