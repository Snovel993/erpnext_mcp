# SPDX-License-Identifier: MIT
"""Controller for Block Revenue Entry — one return attributed to one planting.

NEGATIVE AMOUNTS ARE ALLOWED HERE TOO, and the case is even more ordinary than
it is on the cost side: a packing house settlement that lands NEGATIVE is a
routine event in tree fruit. A block whose fruit did not pack out can owe the
packer more in picking, hauling and storage than the fruit returned, and the
statement comes with a number in brackets. A register that refused it would push
the single most important fact about that block's season out of the system.

`price_per_unit` IS COMPUTED AND MAY BE NEGATIVE FOR THE SAME REASON. It is not
clamped at zero: a per-bin return of -$4.10 is the honest description of that
settlement, and a zero in its place would read as "unpriced" — which is what a
missing quantity legitimately means, and the two must not look alike.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class BlockRevenueEntry(Document):
	def validate(self):
		if _number(self.amount) == 0:
			frappe.throw(
				_(
					"Amount cannot be zero. Negative IS allowed and is ordinary — a settlement on "
					"fruit that did not pack out can come back in brackets, and that is the most "
					"important fact about the block's season. Zero is a row nobody finished."
				)
			)
		if _number(self.quantity) < 0:
			frappe.throw(
				_(
					"Quantity cannot be negative. A return can be negative; the weight that earned "
					"it cannot, and a negative quantity would flip the sign of the price per unit "
					"and make a bad settlement look like a good one."
				)
			)
		if self.allocation_pct not in (None, "") and not 0 <= _number(self.allocation_pct) <= 100:
			frappe.throw(_("Allocation % must be between 0 and 100."))
		if str(self.allocation_basis or "Direct") == "Direct" and not self.allocation_pct:
			self.allocation_pct = 100.0
		if not self.season_year and self.posting_date:
			self.season_year = int(str(self.posting_date)[:4])
		self._price_per_unit()
		self._inherit_from_planting()

	def _price_per_unit(self):
		quantity = _number(self.quantity)
		self.price_per_unit = round(_number(self.amount) / quantity, 4) if quantity > 0 else 0.0

	def _inherit_from_planting(self):
		"""Copy the block's identity down, filling only what is blank.

		Same rule as Block Cost Entry: a row whose field was set deliberately
		keeps what somebody put on it.
		"""
		if not self.planting_season:
			return
		if self.field and self.block_name and self.company and self.variety:
			return
		try:
			parent = frappe.db.get_value(
				"Planting Season",
				self.planting_season,
				["field", "block_name", "company", "variety"],
				as_dict=True,
			)
		except Exception:  # pragma: no cover - a site shaping these columns differently
			return
		if not parent:
			return
		self.field = self.field or parent.get("field")
		self.block_name = self.block_name or parent.get("block_name")
		self.company = self.company or parent.get("company")
		self.variety = self.variety or parent.get("variety")


def _number(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0
