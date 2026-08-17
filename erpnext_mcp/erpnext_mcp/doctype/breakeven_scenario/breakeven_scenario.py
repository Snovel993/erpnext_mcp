# SPDX-License-Identifier: MIT
"""Controller for Breakeven Scenario — one stored what-if.

NO ARITHMETIC HERE, DELIBERATELY. Every number on this row is computed by
`tools/breakeven.py` against the whole analysis — the fixed pile, both variable
piles, the packout and the price — and a controller that recomputed any of them
from the row alone would be working from a fraction of the inputs and would
disagree with the parent. The row is a record of a run, so the only thing worth
enforcing here is that it says which variable it moved.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The variables a scenario may move, in the order the Select declares them.
VARIABLES = ("Price", "Yield", "Packout", "Fixed Cost", "Variable Cost")


class BreakevenScenario(Document):
	def validate(self):
		if str(self.variable or "") not in VARIABLES:
			frappe.throw(
				_(
					"Variable must be one of: {0}. A scenario that does not say what it moved "
					"is a number with no question attached to it."
				).format(", ".join(VARIABLES))
			)
