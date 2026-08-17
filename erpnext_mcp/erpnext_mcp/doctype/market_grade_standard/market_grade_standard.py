# SPDX-License-Identifier: MIT
"""Controller for Market Grade Standard — a child table, empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with
a JSON and no module breaks `bench migrate` rather than degrading. See
`crop_variety.py` for the same note.

EVERY RULE ABOUT A GRADE IS A RULE ABOUT ITS MARKET. Whether the name is unique
needs the sibling rows. Whether a premium is a discount or a sign error is a
judgement about the row, but it is checked beside the others so that a reader
looking for "what can be wrong with a grade" finds all of it in one place. Both
live in `market.py`.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class MarketGradeStandard(Document):
	pass
