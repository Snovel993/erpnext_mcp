# SPDX-License-Identifier: MIT
"""Controller for Asset Cost Center Allocation — a child table, and empty on purpose.

See `asset_depreciation_posting.py` for why an empty controller module is still
mandatory: Frappe imports one per DocType, child tables included, and a folder
with a JSON and no module breaks `bench migrate` rather than degrading.

The rules about an allocation — that the rows total 100, that no cost center
appears twice, that every share is positive — are all statements about the whole
table, so they live on the parent, `AssetCostProfile`. There is nothing true of a
single row on its own.
"""

from frappe.model.document import Document


class AssetCostCenterAllocation(Document):
	pass
