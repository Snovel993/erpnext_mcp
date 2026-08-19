# SPDX-License-Identifier: MIT
"""Controller for Pest Management Provider — a child table, empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with
a JSON and no module breaks `bench migrate` rather than degrading. That is why
this file exists at all; see `crop_variety.py` for the same note.

WHAT WOULD BE CHECKED HERE NEEDS THE SIBLING ROWS, so it is checked where they
are. Two rows naming the same consultant for the same commodity are two answers
to one question, and which one a report reads depends on row order — a rule
about the whole table rather than about a row. It lives in
`tools/company.py`, beside the write that assembles the list.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class PestManagementProvider(Document):
	pass
