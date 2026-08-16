# SPDX-License-Identifier: MIT
"""Controller for Disclosure Checklist — and the one distinction it exists to keep.

`Not Applicable` IS A COMPLETED STATE. `Outstanding` IS NOT. Everything else here
follows from that sentence. The value of a checklist is not that every line ends
up ticked — it is that somebody DECIDED about every line, and "we have no
reportable segments" is a decision while an empty row is an omission. A system
that let those two look alike would produce a checklist that reads as finished
and is not, which is the exact document a disclosure control exists to prevent.

WHAT IS CHECKED ON SAVE, AND WHAT IS DELIBERATELY LEFT TO THE GATE. Duplicate
item names are refused here: the completion tool names an item, and two rows with
one name would make "complete the related party disclosure" ambiguous in a way
nobody would notice until the wrong one was ticked. Completeness is NOT refused
here — a checklist is supposed to sit half-done for weeks, that is what it is
for. Whether an incomplete filing may be marked Filed is a question for the
`disclosure_completeness` control point, where an operation can set its own
strictness, and not for a controller that would impose one answer on everybody.

THE PERIOD IS REQUIRED AND THE DUE DATE IS NOT, which is the right way round: a
disclosure is about a period, and a filing with no period is a filing about
nothing. When it is due is scheduling.
"""

import frappe
from frappe import _
from frappe.model.document import Document

OUTSTANDING = "Outstanding"
IN_PROGRESS_ITEM = "In Progress"
COMPLETE_ITEM = "Complete"
NOT_APPLICABLE = "Not Applicable"

#: Item states that are NOT a gap. `Not Applicable` is here and `In Progress` is
#: not: work started is not a decision reached.
SETTLED_STATES = (COMPLETE_ITEM, NOT_APPLICABLE)

OPEN = "Open"
IN_PROGRESS = "In Progress"
COMPLETE = "Complete"
FILED = "Filed"

#: The statuses at which the completeness control is consulted. Reaching either
#: is a claim that the filing is done, and that is the claim the control tests.
FINAL_STATES = (COMPLETE, FILED)


def cell(row, key, default=None):
	"""One field of a child row, whether it arrived as a document or as a dict.

	A freshly appended row is a Document; a row read back off a loaded parent can
	be a plain mapping, depending on how the framework hydrated it. Code that
	assumed one shape worked until the first time a checklist was loaded rather
	than built, which is a failure that appears in `complete_disclosure_item` and
	nowhere in the tests that create one.
	"""
	if isinstance(row, dict):
		return row.get(key, default)
	return getattr(row, key, default)


def set_cell(row, key, value) -> None:
	"""The setter half of `cell`, so a mutation lands whichever shape the row is."""
	if isinstance(row, dict):
		row[key] = value
	else:
		setattr(row, key, value)


class DisclosureChecklist(Document):
	def validate(self):
		if self.period_start and self.period_end and self.period_end < self.period_start:
			frappe.throw(
				_("Period End ({0}) is before Period Start ({1}).").format(self.period_end, self.period_start)
			)
		if self.filed_on and self.status != FILED:
			frappe.throw(
				_(
					"Filed On is set but the status is {0}. A filing date on something not filed "
					"is the kind of detail that later reads as evidence."
				).format(self.status)
			)
		if self.status == FILED and not self.filed_on:
			self.filed_on = frappe.utils.today()

		if self.reporting_template and self.company:
			owner = frappe.db.get_value("Reporting Template", self.reporting_template, "company")
			if owner and owner != self.company:
				frappe.throw(
					_("Reporting Template {0} belongs to {1}, not {2}.").format(
						self.reporting_template, owner, self.company
					)
				)

		seen = {}
		for index, row in enumerate(self.get("items") or [], start=1):
			idx = cell(row, "idx") or index
			key = str(cell(row, "disclosure_item") or "").strip()
			if not key:
				frappe.throw(_("Row {0}: Disclosure Item is required.").format(idx))
			set_cell(row, "disclosure_item", key)
			folded = key.casefold()
			if folded in seen:
				frappe.throw(
					_(
						"Rows {0} and {1} are both {2!r}. complete_disclosure_item names an item, "
						"so two rows with one name would make a completion ambiguous — and "
						"nobody would notice until the wrong one had been ticked."
					).format(seen[folded], idx, key)
				)
			seen[folded] = idx

			status = cell(row, "status")
			if status in SETTLED_STATES and not cell(row, "completed_on"):
				set_cell(row, "completed_on", frappe.utils.now())
				set_cell(row, "completed_by", cell(row, "completed_by") or frappe.session.user)
			if status == OUTSTANDING:
				# Reopening clears the completion, so a reopened item cannot
				# carry somebody's name as having finished it.
				set_cell(row, "completed_on", None)
				set_cell(row, "completed_by", None)

	def outstanding_required(self) -> list:
		"""The required items nobody has decided about. The gap, and nothing else."""
		return [
			row
			for row in (self.get("items") or [])
			if frappe.utils.cint(cell(row, "required")) and cell(row, "status") not in SETTLED_STATES
		]

	def independently_reviewed(self) -> bool:
		"""A checklist the preparer also reviewed is not reviewed."""
		return bool(self.reviewed_by and self.reviewed_by != self.prepared_by)
