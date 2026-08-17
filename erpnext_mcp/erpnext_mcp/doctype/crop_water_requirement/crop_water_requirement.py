# SPDX-License-Identifier: MIT
"""Controller for Crop Water Requirement — a child table, empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with
a JSON and no module breaks `bench migrate` rather than degrading. See
`crop_variety.py` for the same note.

THE ONE RULE WORTH STATING IS A RULE ABOUT THE LIST. A row may not name a stage
another row already names, because two answers to "how much water at bloom" is
the same as none and which one wins would depend on row order. That needs the
siblings, so it lives in `crop.py`. The Kc range check lives there too, for no
better reason than that keeping both in one place means a reader looking for
"what can be wrong with a water row" finds all of it at once.
"""

from frappe.model.document import Document


class CropWaterRequirement(Document):
	pass
