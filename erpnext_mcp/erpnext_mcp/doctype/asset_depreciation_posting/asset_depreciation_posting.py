# SPDX-License-Identifier: MIT
"""Controller for Asset Depreciation Posting — a child table, and empty on purpose.

WHY THIS FILE EXISTS AT ALL, given that it does nothing. Frappe imports
`<app>/<module>/doctype/<scrubbed name>/<scrubbed name>.py` for **every** DocType
it loads, child tables included — `frappe.modules.utils.load_doctype_module` is
called from `get_controller`, and `bench migrate` reaches it while syncing the
DocType JSON. A folder with a JSON and no module raises
`ModuleNotFoundError: No module named '…asset_depreciation_posting'` and takes
the whole migrate down, which is exactly what v0.7.0 shipped.

WHY THERE IS NO LOGIC IN IT. A row here is one period of depreciation that has
already been written, and the only rule about it — that a period is written once
— is a property of the *set* of rows rather than of any one of them. That rule
lives on the parent, `AssetCostProfile`, which is the only thing that can see all
of them. A child controller that validated a row in isolation would be
duplicating nothing and hiding where the real check is.
"""

from frappe.model.document import Document


class AssetDepreciationPosting(Document):
	pass
