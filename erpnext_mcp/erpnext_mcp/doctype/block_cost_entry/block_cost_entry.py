# SPDX-License-Identifier: MIT
"""Controller for Block Cost Entry — one cost attributed to one planting.

A NEGATIVE AMOUNT IS ALLOWED AND THAT IS DELIBERATE. The obvious rule is that a
cost is positive, and it is wrong here often enough to matter: a credit note, a
rebate on a chemical order, a reallocation that moves cost off this block and
onto another. All three are corrections, all three have to land in the same
register as the thing they correct, and a farm that cannot record a rebate
against the block that carried the invoice will record it nowhere.

What IS refused is zero — a cost of nothing is either a row somebody abandoned
half-typed or a placeholder, and both are noise in a register whose whole job is
to sum to something.

THE SEASON YEAR DEFAULTS FROM THE POSTING DATE AND IS NOT LOCKED TO IT. Dormant
pruning done in December belongs to next season's crop, and a register that
filed it by posting date would credit the wrong year with the work. So the
default is the convenience and the override is the point.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class BlockCostEntry(Document):
	def validate(self):
		amount = _number(self.amount)
		if amount == 0:
			frappe.throw(
				_(
					"Amount cannot be zero. A cost of nothing is either a row abandoned half-typed "
					"or a placeholder, and both are noise in a register whose job is to sum to "
					"something. Negative amounts ARE allowed — a rebate, a credit note or a "
					"reallocation off this block all belong here."
				)
			)
		if _number(self.acres) < 0:
			frappe.throw(_("Acres cannot be negative."))
		if self.allocation_pct not in (None, "") and not 0 <= _number(self.allocation_pct) <= 100:
			frappe.throw(_("Allocation % must be between 0 and 100."))
		if str(self.allocation_basis or "Direct") == "Direct" and not self.allocation_pct:
			# A direct cost is all of itself. Stamped rather than left blank so a
			# report summing allocation percentages does not have to special-case
			# the commonest row in the register.
			self.allocation_pct = 100.0
		if not self.season_year and self.posting_date:
			self.season_year = int(str(self.posting_date)[:4])
		self._per_acre()
		self._inherit_from_planting()

	def _per_acre(self):
		acres = _number(self.acres)
		self.per_acre = round(_number(self.amount) / acres, 2) if acres > 0 else 0.0

	def _inherit_from_planting(self):
		"""Copy the block's identity down so the register filters without a join.

		Only fills what is blank. A row whose field was set deliberately — a cost
		attributed to a planting that has since been re-pointed at another field —
		keeps what somebody put on it.
		"""
		if not self.planting_season:
			return
		if self.field and self.block_name and self.company:
			return
		try:
			parent = frappe.db.get_value(
				"Planting Season",
				self.planting_season,
				["field", "block_name", "company", "cost_center"],
				as_dict=True,
			)
		except Exception:  # pragma: no cover - a site shaping these columns differently
			return
		if not parent:
			return
		self.field = self.field or parent.get("field")
		self.block_name = self.block_name or parent.get("block_name")
		self.company = self.company or parent.get("company")
		self.cost_center = self.cost_center or parent.get("cost_center")


def _number(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0
