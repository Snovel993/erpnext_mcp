# SPDX-License-Identifier: MIT
"""Controller for Approval Threshold — spending authority, as a record.

WHY AN AUTHORITY TABLE IS A DOCUMENT AND NOT A SETTING. A setting has one value
and one audience. Spending authority has a shape: it differs by what is being
booked (a journal entry is not a purchase order), it differs by amount, and the
amounts move as an operation grows. All three of those are things somebody edits
without a code release, and all three are things an auditor asks to see the
history of — which is what `track_changes` on a document gives and a Single does
not.

WHAT `validate` REFUSES, and each refusal is about a chain that would give a
DIFFERENT ANSWER DEPENDING ON ROW ORDER — the one property an authority table
cannot have:

  * TWO UNCAPPED RUNGS. Both would match any amount, and which one authorised a
    million dollars would depend on which sorted first.
  * TWO RUNGS WITH THE SAME CEILING. Same failure, one step down.
  * NO RUNGS AT ALL on an enabled threshold. A threshold that names no approver
    for any amount does not restrict spending — it forbids it, silently, the
    first time somebody exceeds the auto-approve floor.
  * AN AUTO-APPROVE FLOOR ABOVE THE FIRST RUNG'S CEILING. That would mean the
    first rung's approver could only release amounts that needed no approval,
    which is a rung that can never fire.

WHAT IT DOES NOT REFUSE. A threshold with no auto-approve floor, which means
every transaction of that type needs somebody. A ceiling in a currency the
company does not use — that is a data question the tools report rather than a
structural one. A role nobody holds: roles are created and staffed on different
days, and refusing the table because nobody is a Compliance Officer yet would
block the setup step that comes before staffing.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ApprovalThreshold(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		title = str(self.threshold_name or "").strip()
		self.name = f"{title} - {abbr}" if abbr else title

	def validate(self):
		self.threshold_name = str(self.threshold_name or "").strip()
		if not self.threshold_name:
			frappe.throw(_("Threshold Name is required."))
		if not self.document_type:
			self.document_type = "Any"

		duplicate = frappe.db.get_value(
			"Approval Threshold",
			{
				"threshold_name": self.threshold_name,
				"company": self.company,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Approval Threshold {0} already covers {1} for this company. One threshold per "
					"name per company — edit that one, or name this one for what distinguishes it."
				).format(duplicate, self.threshold_name),
				title=_("Duplicate Threshold"),
			)

		if float(self.auto_approve_below or 0) < 0:
			frappe.throw(_("Auto-Approve Below cannot be negative."))

		self._check_the_chain()

	def _check_the_chain(self) -> None:
		rungs = list(self.get("levels") or [])
		if not rungs:
			if int(self.enabled or 0):
				frappe.throw(
					_(
						"An enabled Approval Threshold needs at least one level. A threshold that "
						"names no approver for any amount does not restrict spending — it forbids "
						"everything above the auto-approve floor, with nobody able to release it."
					),
					title=_("A chain with no rungs"),
				)
			return

		uncapped = [row for row in rungs if not float(row.get("up_to_amount") or 0)]
		if len(uncapped) > 1:
			frappe.throw(
				_(
					"{0} levels have no ceiling. Exactly one rung may be uncapped — it is the top "
					"of the chain. Two would make the answer to 'who approves a million' depend on "
					"which row sorted first, which is the one thing an authority table must never do."
				).format(len(uncapped)),
				title=_("Two tops to one chain"),
			)

		seen = {}
		for row in rungs:
			ceiling = float(row.get("up_to_amount") or 0)
			if not ceiling:
				continue
			if ceiling in seen:
				frappe.throw(
					_(
						"Two levels both stop at {0} ({1} and {2}). Which of them authorised a "
						"transaction at that amount would depend on row order."
					).format(ceiling, seen[ceiling], row.get("approver_role")),
					title=_("Two rungs, one ceiling"),
				)
			seen[ceiling] = row.get("approver_role")

		floor = float(self.auto_approve_below or 0)
		ceilings = sorted(seen)
		if floor and ceilings and floor >= ceilings[0]:
			frappe.throw(
				_(
					"Auto-Approve Below is {0}, at or above the first rung's ceiling of {1}. That "
					"rung could then only release amounts that need no approval at all, so it "
					"would never fire."
				).format(floor, ceilings[0]),
				title=_("A rung that can never fire"),
			)
