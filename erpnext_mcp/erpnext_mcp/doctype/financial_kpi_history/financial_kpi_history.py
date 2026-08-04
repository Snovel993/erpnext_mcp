# SPDX-License-Identifier: MIT
"""Controller for Financial KPI History — the cache, and the two rules it needs.

A CACHE DOCTYPE HAS ALMOST NO CONTROLLER, AND SHOULD NOT. Every row here is
written by `services/windowed_reports.py` or by the overnight sweep, both of
which have already decided what the figure is; validation that second-guessed
them would be a second definition of the same computation, living in a file
nobody reads when they change the first one.

So there are exactly two rules, and both are about the row's IDENTITY rather
than its contents.

────────────────────────────────────────────────────────────────────────────
ONE ROW PER (KPI, COMPANY, STEP, WINDOW TYPE, WINDOW MONTHS, AS OF)
────────────────────────────────────────────────────────────────────────────

Enforced here rather than by a unique index, for the same reason
`Normalization Adjustment` enforces its uniqueness in a controller: the six
columns include two whose values are Selects and one that is nullable in
practice on older rows, and a database constraint that a site cannot migrate
past is worse than a duplicate nobody has yet created.

The failure it prevents is quiet and specific. Two rows for one reporting
moment are two answers about the same window, and the one a chart draws will be
whichever sorted first — so a KPI would silently change value depending on
which of two rows a query happened to reach, with both rows internally
consistent and neither wrong on its face. The writers upsert; this is what
catches a path that forgets to.

────────────────────────────────────────────────────────────────────────────
THE WINDOW HAS TO BE A WINDOW
────────────────────────────────────────────────────────────────────────────

`period_end` before `period_start` is a row that will be read as covering
negative time, and it will not fail anywhere — it will produce a figure over an
empty range and cache it as authoritative. `as_of` before `period_end` is the
same class of mistake in the other direction: a snapshot claiming to know, on a
Tuesday, what a window ending on the following Friday was worth.

Both are refused, because a cache whose rows can be incoherent is a cache
nobody can reason about, and the whole argument for having one is that reading
it is equivalent to recomputing.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import shifts

DOCTYPE = "Financial KPI History"

#: The six columns that make a snapshot the snapshot it is.
IDENTITY = (
	"kpi_key",
	"company",
	"computation_step",
	"window_type",
	"window_months",
	"as_of",
)


class FinancialKPIHistory(Document):
	def autoname(self):
		"""KPIH-YYYY-0001, where YYYY is the year the snapshot is AS OF.

		Not the year the sweep ran. A January backfill writes rows about five
		different years in one pass, and a series keyed off `today()` would file
		every one of them under this one — which would make the docname actively
		misleading on exactly the rows somebody goes looking for by hand.
		"""
		year = str(self.as_of or frappe.utils.today())[:4]
		self.name = shifts.next_in_series(DOCTYPE, "KPIH", year)

	def validate(self):
		self._require_a_coherent_window()
		self._refuse_a_second_snapshot()

	def _require_a_coherent_window(self) -> None:
		if not self.period_start or not self.period_end:
			return
		if str(self.period_end) < str(self.period_start):
			frappe.throw(
				_(
					"period_end ({0}) is before period_start ({1}). A cached figure over negative "
					"time does not fail anywhere — it computes over an empty range and is stored as "
					"authoritative, which is the worst way for this to go wrong. Nothing was saved."
				).format(self.period_end, self.period_start)
			)
		if self.as_of and str(self.as_of) < str(self.period_end):
			frappe.throw(
				_(
					"as_of ({0}) is before period_end ({1}), so this row claims to know what a "
					"window that had not finished was worth. as_of is the REPORTING MOMENT and the "
					"window ends at the last completed step boundary on or before it, so as_of is "
					"never the earlier of the two. Nothing was saved."
				).format(self.as_of, self.period_end)
			)

	def _refuse_a_second_snapshot(self) -> None:
		filters = {field: self.get(field) for field in IDENTITY}
		filters["name"] = ("!=", self.name or "")
		existing = frappe.db.get_all(DOCTYPE, filters=filters, pluck="name", limit=1)
		if existing:
			frappe.throw(
				_(
					"{0} is already the cached {1} snapshot for {2}, {3} step, {4} window, as of "
					"{5}. Two rows for one reporting moment are two answers about one window, and "
					"the one a chart draws will be whichever sorted first — so the KPI would change "
					"value depending on which row a query reached, with both rows internally "
					"consistent and neither wrong on its face. This is a CACHE: update that row "
					"rather than adding this one, or delete it and let the next read rebuild it. "
					"Nothing was saved."
				).format(
					existing[0],
					self.kpi_key,
					self.company,
					self.computation_step,
					self.window_type,
					self.as_of,
				)
			)
