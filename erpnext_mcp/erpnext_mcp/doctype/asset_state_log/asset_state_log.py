# SPDX-License-Identifier: MIT
"""Controller for Asset State Log — append-only record of state changes."""

import frappe
from frappe import _
from frappe.model.document import Document


class AssetStateLog(Document):
	def before_save(self):
		if self.flags.in_insert:
			return
		frappe.throw(
			_("Asset State Log rows are immutable — they record what happened to an asset."),
			title=_("Append-Only Log"),
		)
