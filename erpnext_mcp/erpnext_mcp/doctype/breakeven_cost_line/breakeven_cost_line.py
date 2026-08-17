# SPDX-License-Identifier: MIT
"""Controller for Breakeven Cost Line — one account, and what its money does.

A CHILD ROW WITH A `validate` BECAUSE THE SPLIT HAS TO HOLD. `fixed_amount` and
`variable_amount` are derived columns, and the property that makes them worth
storing at all is that they sum back to `amount`. Deriving them here rather than
only in `tools/breakeven.py` means a row edited by hand in the Desk gets the same
arithmetic as a row written by `compute_breakeven`, and the total on the parent
does not quietly stop agreeing with the lines it was added up from.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The behaviours, in the order the Select declares them.
BEHAVIORS = ("Fixed", "Variable", "Mixed", "Excluded")


class BreakevenCostLine(Document):
	def validate(self):
		behavior = str(self.cost_behavior or "Fixed")
		if behavior not in BEHAVIORS:
			frappe.throw(_("Behavior must be one of: {0}.").format(", ".join(BEHAVIORS)))

		amount = float(self.amount or 0)
		share = _clamped_share(self.variable_pct)

		if behavior == "Excluded":
			# Excluded means the model never sees this money. Zeroing BOTH derived
			# columns rather than leaving the amount in one of them is what stops
			# an excluded account being added back in by a reader who summed the
			# wrong column.
			self.fixed_amount = 0.0
			self.variable_amount = 0.0
		elif behavior == "Fixed":
			self.fixed_amount = amount
			self.variable_amount = 0.0
		elif behavior == "Variable":
			self.fixed_amount = 0.0
			self.variable_amount = amount
		else:  # Mixed
			self.variable_amount = round(amount * share, 6)
			self.fixed_amount = round(amount - self.variable_amount, 6)

		if behavior in ("Variable", "Mixed") and not self.volume_basis:
			# A variable cost with no volume to be variable AGAINST is the one
			# combination that would silently produce a wrong breakeven rather
			# than a missing one, so it gets the commonest answer rather than a
			# throw: almost every variable orchard cost that is not packing is
			# picking, hauling or bins, and all three follow the harvest.
			self.volume_basis = "Harvested"
		if behavior in ("Fixed", "Excluded"):
			self.volume_basis = None


def _clamped_share(variable_pct) -> float:
	"""`variable_pct` as a 0–1 share, refusing nonsense rather than modelling it.

	A percentage outside 0–100 on a mixed cost would produce a negative fixed
	part or a variable part larger than the account — both of which read as a
	real number in every column that follows.
	"""
	try:
		pct = float(variable_pct or 0)
	except (TypeError, ValueError):
		frappe.throw(_("Variable % must be a number."))
	if pct < 0 or pct > 100:
		frappe.throw(_("Variable % must be between 0 and 100. Got {0}.").format(pct))
	return pct / 100.0
