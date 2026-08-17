# SPDX-License-Identifier: MIT
"""Controller for Market — an outlet, and the grades it packs to.

THERE IS NO COMPANY COLUMN AND THAT IS THE DESIGN. A market is a place in the
world, not a thing a company owns. Two growers shipping into the Pacific
Northwest fresh cherry market are shipping into ONE market, and giving each of
them a private copy would give the site two answers to what a No. 1 is — which
is the failure this doctype exists to prevent, reproduced at the top level.

A GRADE NAME IS UNIQUE WITHIN ITS MARKET. Two rows called 'No. 1' are two prices
for one grade. Whichever a packout assumption read first would decide the
season's revenue projection, and nothing would record that a second answer
existed. This is the same rule as Crop's variety check and it is here for the
same reason: a duplicate in a child table is not a tidiness problem, it is a
wrong number that looks right.

THE PERCENTAGES ARE BOUNDED IN DIFFERENT DIRECTIONS, ON PURPOSE.
`max_defect_pct` is bounded 0 to 100 because a defect tolerance outside that is
a transcription of something else, and storing 150 would make every tolerance
comparison pass. `premium_pct` is bounded only BELOW, at -100, because a grade
worth less than the base is the normal case — orchard run against fancy, juice
against fresh — and a column that refused negatives would make every operation
invent a base grade nothing falls under. Above, it is left open: a 400% premium
on an export programme is a real number somebody negotiated.

RETIRED, NEVER DELETED. `is_active` is the off switch. Last season's settlements
name this market, and deleting it turns those into rows pointing at nothing.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The floor on a premium. -100% is fruit given away; anything past it is a sign
#: error rather than a steeper discount, because there is no such thing as being
#: paid less than nothing for a grade you nonetheless shipped.
MIN_PREMIUM_PCT = -100.0


class Market(Document):
	def validate(self):
		self.market_name = str(self.market_name or "").strip()
		if not self.market_name:
			frappe.throw(_("Market Name is required — it is the docname a settlement spells."))
		self.region = str(self.region or "").strip()
		self.shipping_point = str(self.shipping_point or "").strip()

		self._check_grade_standards()

	def _check_grade_standards(self) -> None:
		seen: dict = {}
		for position, row in enumerate(self.grade_standards or [], start=1):
			index = int(row.get("idx") or position)
			grade_name = str(row.get("grade_name") or "").strip()
			if not grade_name:
				frappe.throw(_("Row {0}: a grade needs a name.").format(index))

			key = grade_name.casefold()
			if key in seen:
				frappe.throw(
					_(
						"Rows {0} and {1} are both called {2}. Two prices for one grade means a "
						"packout assumption reads whichever sorted first, and nothing records "
						"that a second answer existed — keep one."
					).format(seen[key], index, grade_name),
					title=_("Duplicate Grade"),
				)
			seen[key] = index

			if float(row.get("min_size_mm") or 0) < 0:
				frappe.throw(
					_("Row {0} ({1}): a minimum size cannot be negative.").format(index, grade_name)
				)

			defect = float(row.get("max_defect_pct") or 0)
			if defect < 0 or defect > 100:
				frappe.throw(
					_(
						"Row {0} ({1}) has a maximum defect of {2}%. A defect tolerance outside 0 "
						"to 100 is a transcription of something else — stored, it would make "
						"every tolerance comparison pass."
					).format(index, grade_name, defect),
					title=_("Defect Tolerance Out of Range"),
				)

			premium = float(row.get("premium_pct") or 0)
			if premium < MIN_PREMIUM_PCT:
				frappe.throw(
					_(
						"Row {0} ({1}) has a premium of {2}%. Below -100% is fruit you shipped and "
						"paid for the privilege — a sign error rather than a steeper discount. "
						"Negative premiums themselves are fine and normal: juice against fresh is "
						"one."
					).format(index, grade_name, premium),
					title=_("Premium Below the Floor"),
				)
