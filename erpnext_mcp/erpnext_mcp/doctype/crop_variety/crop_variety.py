# SPDX-License-Identifier: MIT
"""Controller for Crop Variety — a child table, empty on purpose.

Frappe imports one module per DocType, child tables included, and a folder with
a JSON and no module breaks `bench migrate` rather than degrading. That is why
this file exists at all; see `farm_task_template_checklist_item.py` for the same
note and `test_packaging.py` for the release that learned it the hard way.

EVERY RULE ABOUT A VARIETY IS A RULE ABOUT ITS CROP. Whether the name is unique
needs the sibling rows. Whether `maturity_years` means anything needs the
parent's growth cycle — three years to bear is a fact on a perennial and a
contradiction on an annual, and the row cannot tell which it is. Both checks
live in `crop.py`, where the whole list and the cycle are in hand.

There is nothing true of one of these rows on its own.
"""

from frappe.model.document import Document


class CropVariety(Document):
	pass
