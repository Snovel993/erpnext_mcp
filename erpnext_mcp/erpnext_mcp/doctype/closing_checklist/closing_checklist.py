# SPDX-License-Identifier: MIT
"""Controller for Closing Checklist — one accounting period, and its two jobs.

WHY THE CHECKLIST AND THE LOCK ARE ONE DOCUMENT. They are the same object seen
from two sides. The checklist is what has to be true before a period is finished;
the lock is the statement that it now is. Splitting them into two doctypes would
mean a site could hold a locked period with an open checklist, or a completed
checklist for a period nobody closed — and both of those are questions somebody
would then have to write a report to answer. One row per period per company, with
`locked` on it, cannot get into either state. It is also the design philosophy
this repository states out loud: maximal data science, avoid table sprawl.

THE PERIOD IS THE KEY, AND IT IS A DATE RANGE RATHER THAN A NAME. 'March' is not
a period — a company on an August year-end has a March that belongs to two fiscal
years, and a range answers 'is this posting date inside a closed period' in one
comparison with no calendar arithmetic anywhere. `period_start` and `period_end`
are both inclusive.

WHAT `validate` REFUSES:

  * A PERIOD THAT ENDS BEFORE IT STARTS. Everything downstream compares dates
    against this range, and an inverted one silently matches nothing — so a
    locked period would let every posting through while reporting itself locked,
    which is the worst available failure of this doctype.
  * A PERIOD OVERLAPPING ANOTHER FOR THE SAME COMPANY. Two rows covering one date
    make 'is this date locked' depend on which is read first.
  * A LOCK WITH NO DATE OR NOBODY'S NAME ON IT. The lock is an assertion somebody
    made; an unattributed one is not evidence of anything.

WHAT IT DOES NOT REFUSE — AND THIS IS THE LOAD-BEARING OMISSION. **Locking a
period with required steps outstanding.** That decision belongs to the
`closing_checklist` control point and to the enforcement mode an operator set
there, NOT to this controller. A controller that refused it would make the
control unbypassable, which is the one property every control in this release is
built not to have: an operation running Advisory has to be able to close a month
with a step outstanding, and to have that fact filed against the period rather
than blocked. See `erpnext_mcp/enforcement.py`.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class ClosingChecklist(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		label = f"{self.period_type or 'Period'} {self.period_start or ''}"
		self.name = f"{label} - {abbr}" if abbr else label

	def validate(self):
		if not self.period_start or not self.period_end:
			frappe.throw(_("Period Start and Period End are both required — the period IS the range."))
		if self.period_end < self.period_start:
			frappe.throw(
				_(
					"Period End {0} is before Period Start {1}. An inverted range matches no date at "
					"all, so this period would report itself locked while letting every posting "
					"through."
				).format(self.period_end, self.period_start),
				title=_("A period that ends before it starts"),
			)
		if not self.period_type:
			self.period_type = "Month"
		if not self.status:
			self.status = "Open"

		self._refuse_an_overlap()
		self._check_the_lock()
		self._recount()

	def _refuse_an_overlap(self) -> None:
		clash = frappe.db.get_all(
			"Closing Checklist",
			filters={
				"company": self.company,
				"name": ("!=", self.name or ""),
				"period_start": ("<=", self.period_end),
				"period_end": (">=", self.period_start),
			},
			fields=["name", "period_start", "period_end"],
			limit=1,
		)
		if clash:
			row = clash[0]
			frappe.throw(
				_(
					"Closing Checklist {0} already covers {1} to {2} for this company, which overlaps "
					"{3} to {4}. Two rows covering one date would make 'is this date locked' depend "
					"on which was read first."
				).format(
					row["name"], row["period_start"], row["period_end"], self.period_start, self.period_end
				),
				title=_("Overlapping periods"),
			)

	def _check_the_lock(self) -> None:
		if not int(self.locked or 0):
			# Unlocking clears the attribution rather than leaving a stale name on a
			# period that is open again. A reopened period was never closed by the
			# person the column still named, and leaving it would be the record
			# saying something untrue.
			self.locked_on = None
			self.locked_by = None
			return
		if not self.locked_on:
			self.locked_on = frappe.utils.now()
		if not self.locked_by:
			self.locked_by = frappe.session.user
		if self.status not in ("Closed", "Locked"):
			self.status = "Closed"

	def _recount(self) -> None:
		"""The two counts, computed here so nothing anywhere types them.

		They are stored rather than derived on read because the list view is where
		somebody scans twelve periods looking for the one that is not finished, and
		a list view cannot aggregate a child table.
		"""
		rows = list(self.get("items") or [])
		self.item_count = len(rows)
		self.outstanding_count = sum(
			1 for row in rows if int(row.get("required") or 0) and not int(row.get("completed") or 0)
		)
