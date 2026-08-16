# SPDX-License-Identifier: MIT
"""Controller for Standard Cost — what a thing is SUPPOSED to cost.

A STANDARD IS DATED, WHICH IS THE WHOLE DESIGN. Costs move every season, and a
variance computed against the wrong season's standard is worse than no variance
at all: it is a confident wrong number. So a standard is not a value on an item —
it is a row with an effective range, and `select_effective` picks the row that
covered the date being asked about. That is the same shape `Piecework Rate` and
`Position Wage Default` already use in this app, and it is used here for the same
reason rather than invented again.

WHAT `validate` REFUSES. Two standards for one subject and one basis whose
effective ranges OVERLAP — because then "what should a bin have cost in August"
has two answers and the report's figure would depend on row order. An open-ended
row (no `effective_to`) counts as covering everything after its start, which is
the normal shape and the one most likely to collide.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class StandardCost(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		parts = [str(self.subject or "").strip(), str(self.cost_basis or "").strip(), str(self.effective_from or "")]
		stem = " ".join(part for part in parts if part)
		self.name = f"{stem} - {abbr}" if abbr else stem

	def validate(self):
		self.subject = str(self.subject or "").strip()
		if not self.subject:
			frappe.throw(_("Subject is required — the item or activity this standard is for."))
		if not self.effective_from:
			frappe.throw(_("Effective From is required. A standard with no date cannot be selected for one."))
		if self.effective_to and self.effective_to < self.effective_from:
			frappe.throw(_("Effective To cannot be before Effective From."))
		if float(self.standard_rate or 0) < 0:
			frappe.throw(_("Standard Rate cannot be negative."))

		self._refuse_an_overlap()

	def _refuse_an_overlap(self) -> None:
		siblings = frappe.db.get_all(
			"Standard Cost",
			filters={
				"company": self.company,
				"subject": self.subject,
				"cost_basis": self.cost_basis,
				"name": ("!=", self.name or ""),
			},
			fields=["name", "effective_from", "effective_to"],
			limit=200,
		)
		start = str(self.effective_from)
		end = str(self.effective_to or "9999-12-31")
		for row in siblings or []:
			other_start = str(row["effective_from"])
			other_end = str(row["effective_to"] or "9999-12-31")
			if start <= other_end and other_start <= end:
				frappe.throw(
					_(
						"Standard Cost {0} already covers {1} to {2} for {3} on this basis, which "
						"overlaps {4} to {5}. Two standards covering one date would make 'what "
						"should this have cost' depend on which row was read first — close the "
						"earlier one with an Effective To before starting the new one."
					).format(
						row["name"],
						other_start,
						row["effective_to"] or "open",
						self.subject,
						start,
						self.effective_to or "open",
					),
					title=_("Overlapping standards"),
				)
