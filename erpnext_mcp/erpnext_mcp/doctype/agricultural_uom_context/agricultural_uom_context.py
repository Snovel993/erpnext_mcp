# SPDX-License-Identifier: MIT
"""Controller for Agricultural UOM Context — which units are allowed where.

AN EMPTY ALLOW-LIST IS REFUSED, AND THAT IS THE MOST IMPORTANT RULE HERE. A
context with no rows either forbids everything or permits everything depending
on which reader you ask, and both readings have been shipped by somebody. So a
context that lists nothing is not a permissive context; it is an unsaveable one.

AT MOST ONE DEFAULT. Two defaults is the same as none — which one a form offered
first would depend on row order, and row order is not a decision anybody made.
Zero defaults is allowed and is sometimes right: for harvest units, making
somebody choose between bins and lugs is better than quietly picking bins.

THE DIMENSION CHECK ONLY SPEAKS WHERE IT KNOWS. `ag_uom.DIMENSION` covers the
ten units this app seeds; a unit outside it is not checked at all, because an
operator adding their own unit to a context should not have to teach this module
about it first. Where it does know, the rule is strict: a list mixing gallons and
acres is not a unit list, it is two, and every downstream reader that treats the
list as interchangeable options would be wrong about half of them.

CONTAINERS COUNT AS `Count`, WHICH IS WHY 'Harvest' AND 'Scale Ticket' ARE TWO
CONTEXTS. A bin is a box; what is in it is a weight the shed measures. Keeping
them apart is what stops a counted delivery and a weighed one being added
together — see `ag_uom` for the long version.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import ag_uom


class AgriculturalUOMContext(Document):
	def validate(self):
		self.context_name = str(self.context_name or "").strip()
		if not self.context_name:
			frappe.throw(_("Context is required — it is the docname a caller passes."))

		self._check_not_empty()
		self._check_no_duplicates()
		self._check_one_default()
		self._check_one_dimension()

	def _check_not_empty(self) -> None:
		if self.uoms:
			return
		frappe.throw(
			_(
				"{0} lists no units. An empty allow-list forbids everything or permits "
				"everything depending on who reads it, and both readings have been shipped — "
				"so it is refused rather than stored. List the units this work is measured in, "
				"or switch the context off."
			).format(self.context_name),
			title=_("Empty Unit List"),
		)

	def _check_no_duplicates(self) -> None:
		seen: dict = {}
		for position, row in enumerate(self.uoms or [], start=1):
			index = int(row.get("idx") or position)
			unit = str(row.get("uom") or "").strip()
			if not unit:
				frappe.throw(_("Row {0}: a unit row needs a unit.").format(index))
			if unit in seen:
				frappe.throw(
					_("Rows {0} and {1} both list {2}. Listing a unit twice says nothing twice.").format(
						seen[unit], index, unit
					),
					title=_("Duplicate Unit"),
				)
			seen[unit] = index

	def _check_one_default(self) -> None:
		defaults = [str(row.get("uom")) for row in self.uoms or [] if row.get("is_default")]
		if len(defaults) < 2:
			return
		frappe.throw(
			_(
				"{0} has {1} default units ({2}). Two defaults is the same as none — which one "
				"a form offered first would depend on row order. Pick one, or none: a context "
				"with no default makes somebody choose, which for harvest units is arguably "
				"the right answer."
			).format(self.context_name, len(defaults), ", ".join(defaults)),
			title=_("More Than One Default"),
		)

	def _check_one_dimension(self) -> None:
		"""Every unit this app knows must measure what the context says it measures.

		Silent about units it does not know. That is not laxity: the map covers
		the units this app seeds, and refusing an operator's own unit for being
		unfamiliar would make the register unusable by the farms it is for.
		"""
		wanted = str(self.applies_to or "").strip()
		if not wanted:
			return
		wrong = []
		for row in self.uoms or []:
			measures = ag_uom.dimension_of(row.get("uom"))
			if measures and measures != wanted:
				wrong.append((str(row.get("uom")), measures))
		if not wrong:
			return
		named = ", ".join(f"{unit} measures {measures}" for unit, measures in wrong)
		frappe.throw(
			_(
				"{0} says it measures {1}, but {2}. A list mixing two dimensions is not a unit "
				"list, it is two — and everything downstream treats the entries as "
				"interchangeable options. Split it into one context per measurement."
			).format(self.context_name, wanted, named),
			title=_("Context Mixes Two Measurements"),
		)
