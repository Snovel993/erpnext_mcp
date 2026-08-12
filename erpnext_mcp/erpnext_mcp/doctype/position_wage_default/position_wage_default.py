# SPDX-License-Identifier: MIT
"""Controller for Position Wage Default.

The same shape as `piecework_rate.py`, and for the same reasons: the pure checks
come from `wage_defaults` so the Desk and the MCP tools agree, and the duplicate
check lives here because it needs a database read.

At most one active row per (company, designation, effective_from). Two rows
starting the same day for the same job title would make "what does a Picker
start on" depend on which was created first, and this table's whole purpose is
that the answer is the same for everybody hired that week.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import wage_defaults

DOCTYPE = "Position Wage Default"


class PositionWageDefault(Document):
	def validate(self):
		errors = wage_defaults.validate_position_wage_default(self.as_dict())
		if errors:
			frappe.throw(_("{0}. Nothing was saved.").format("; ".join(errors)))
		self._refuse_a_duplicate_start()

	def _refuse_a_duplicate_start(self) -> None:
		clash = frappe.db.get_value(
			DOCTYPE,
			{
				"company": self.company,
				"designation": self.designation,
				"effective_from": self.effective_from,
				"is_active": 1,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if clash:
			frappe.throw(
				_(
					"{0} already sets a {1} rate for {2} from {3}. Two active rows starting on "
					"the same day for the same designation is two answers to one question — edit "
					"that row, or close it and start this one on a later date. Nothing was saved."
				).format(clash, self.designation, self.company, self.effective_from)
			)
