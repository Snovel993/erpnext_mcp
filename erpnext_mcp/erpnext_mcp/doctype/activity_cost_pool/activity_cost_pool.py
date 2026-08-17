# SPDX-License-Identifier: MIT
"""Controller for Activity Cost Pool — one activity's money for one year.

ONE POOL PER ACTIVITY PER YEAR, ENFORCED HERE. Two pools for one activity and
one fiscal year would make "what did spraying cost in 2026" depend on which row
was read first, and the allocation engine — which sums every Ready pool — would
count the activity twice while reporting a total that still balanced against
itself. That is the failure mode this doctype exists to prevent, so the refusal
lives on the document rather than in the tool that usually creates it.

THE SOURCES ARE CHECKED AGAINST THE AMOUNT. Where the amount was read from the
ledger, the itemised sources must add up to it. A pool whose trail does not reach
its own figure is worse than a pool with no trail at all: the trail is what a
reader trusts, and one that silently disagrees with the number above it turns a
control into a decoration.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: How far the itemised sources may drift from the pool amount before the
#: document refuses. A cent covers per-row rounding; anything larger is a
#: missing or double-counted source rather than arithmetic.
SOURCE_TOLERANCE = 0.01


class ActivityCostPool(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		activity = frappe.db.get_value("Cost Activity", self.activity, "activity_name") or self.activity
		stem = f"{activity} {self.fiscal_year}"
		self.name = f"{stem} - {abbr}" if abbr else stem

	def validate(self):
		if float(self.pool_amount or 0) < 0:
			frappe.throw(
				_(
					"Pool Amount cannot be negative. A negative pool allocates a credit to every "
					"block in proportion to how much of the activity it consumed, which is arithmetic "
					"nobody asked for — book the correction in the ledger and recompute the pool."
				)
			)
		if self.period_start and self.period_end and self.period_end < self.period_start:
			frappe.throw(_("Period End cannot be before Period Start."))

		self._refuse_a_second_pool()
		self._refuse_a_trail_that_does_not_reach_the_figure()

	def _refuse_a_second_pool(self) -> None:
		# Self is excluded by whether this is an edit rather than by docname:
		# `autoname` derives the docname from the activity and the year, so the
		# twin this is looking for carries the same name and a
		# `name != self.name` filter would hide it. On an insert every match is a
		# twin; only an edit has a self to skip.
		filters = {"company": self.company, "activity": self.activity, "fiscal_year": self.fiscal_year}
		if not (self.flags.in_insert or self.is_new()):
			filters["name"] = ("!=", self.name or "")
		twin = frappe.db.get_all("Activity Cost Pool", filters=filters, pluck="name", limit=1)
		if twin:
			frappe.throw(
				_(
					"Activity Cost Pool {0} already covers {1} for {2}. The allocation engine sums "
					"every Ready pool in the year, so a second one does not produce a conflict it "
					"can see — it produces a total that is quietly too big and internally consistent. "
					"Edit the existing pool instead."
				).format(twin[0], self.activity, self.fiscal_year),
				title=_("Pool already exists"),
			)

	def _refuse_a_trail_that_does_not_reach_the_figure(self) -> None:
		if self.amount_source != "Ledger":
			return
		rows = self.get("sources") or []
		if not rows:
			return
		itemised = sum(
			float((row.get("amount") if isinstance(row, dict) else row.amount) or 0) for row in rows
		)
		if abs(itemised - float(self.pool_amount or 0)) > SOURCE_TOLERANCE:
			frappe.throw(
				_(
					"The sources on this pool add up to {0}, and the pool amount is {1}. A ledger "
					"pool's trail is the only reason to believe its figure, and a trail that "
					"disagrees with the number above it is worse than no trail — it reads as "
					"evidence. Set Amount Source to Manual if the figure is deliberately not the "
					"sum of its sources."
				).format(round(itemised, 2), round(float(self.pool_amount or 0), 2)),
				title=_("Sources do not reach the pool amount"),
			)
