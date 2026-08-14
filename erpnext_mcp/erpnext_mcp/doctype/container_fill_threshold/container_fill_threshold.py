# SPDX-License-Identifier: MIT
"""Controller for Container Fill Threshold.

THE ONE INVARIANT THIS ENFORCES: at most one row per (company, container_type).
`update_fill_threshold` relies on that to mean "the current settings" rather
than "a settings" — the tool layer looks the row up, edits it in place and bumps
`version`; this is the backstop against a row typed straight into the Desk
creating a second, silently-shadowing one for a container type that already has
a row. `fill_pipeline.py` carries the bound check (`upper_bound_pct`, when set,
must exceed `lower_bound_pct`) because that arithmetic is shared with the tool
that builds the row in the first place and this app keeps that split everywhere
else too.
"""

import frappe
from frappe import _
from frappe.model.document import Document

DOCTYPE = "Container Fill Threshold"


class ContainerFillThreshold(Document):
	def validate(self):
		self._refuse_a_duplicate_container_type()

	def _refuse_a_duplicate_container_type(self) -> None:
		clash = frappe.db.get_value(
			DOCTYPE,
			{
				"company": self.company,
				"container_type": self.container_type,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if clash:
			frappe.throw(
				_(
					"{0} already holds the {1} threshold for {2}. There is one current threshold "
					"per container type per company — edit that row (or call update_fill_threshold, "
					"which does) rather than creating a second one. Nothing was saved."
				).format(clash, self.container_type, self.company)
			)
