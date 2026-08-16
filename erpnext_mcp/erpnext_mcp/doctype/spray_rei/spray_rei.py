# SPDX-License-Identifier: MIT
"""Controller for Spray REI — one block, restricted until one moment.

THE WINDOW IS COMPUTED ONCE AND THEN IT IS A FACT. `expires_at` is written at
application time from `started_at` plus `rei_hours`, and this controller only
fills it in where a caller left it blank. It is deliberately NOT recomputed on
every save: a worker was told at the tailgate that the block clears at 18:40,
and an Item whose label interval somebody corrects next March must not silently
move a window that has already been published to a crew and already passed.

THE STATUS IS NOT DERIVED FROM THE CLOCK EITHER, for a reason that is the whole
posture of this record. Deriving "active" as `expires_at > now` at read time
would be simpler and would make a filtered query — "every active restriction on
this farm" — impossible to write against the database, so every reader would
have to pull every row of the register and filter it in Python. Instead the
status is a column, the closing is an explicit act (`close_expired_reis`, or the
sweep any read performs first), and `expires_at` is what decides it. See
`tools/spray_rei.py`.
"""

import frappe
from frappe import _
from frappe.model.document import Document

ACTIVE = "Active"
EXPIRED = "Expired"
CANCELLED = "Cancelled"


class SprayREI(Document):
	def validate(self):
		if not self.block:
			frappe.throw(_("A restricted-entry window names the block it restricts."))
		if not self.block_doctype:
			frappe.throw(_("Block DocType is required — the block has to be in a register."))
		try:
			hours = float(self.rei_hours or 0)
		except (TypeError, ValueError):
			frappe.throw(_("REI Hours must be a number."))
		if hours <= 0:
			frappe.throw(
				_(
					"REI Hours must be greater than zero. A window of no hours is not a "
					"restriction, and a record saying a block is restricted for nothing is "
					"worse than no record."
				)
			)
		if not self.started_at:
			self.started_at = frappe.utils.now()
		if not self.expires_at:
			self.expires_at = frappe.utils.add_to_date(self.started_at, hours=hours)
		if self.status not in (ACTIVE, EXPIRED, CANCELLED):
			self.status = ACTIVE
