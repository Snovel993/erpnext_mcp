# SPDX-License-Identifier: MIT
"""Controller for Spray Tank Mix — the recipe, not the event.

THE LONGEST INTERVAL IN THE TANK WINS, and it is computed here so that no
surface has to decide it. A four-hour product and a twenty-four-hour one
restrict a block for twenty-four hours, and the block does not become
half-enterable at hour twelve. `tools/spray.py` and `stock_bridge.spray_windows`
apply the same rule to the same numbers; this is where it is written onto the
recipe, so the mix a crew is handed already says how long the block will be shut.

THE LABEL NUMBERS ARE COPIED, NOT FETCHED. Each product line carries the
`rei_hours` and `phi_days` that were on the Item when the mix was written. A mix
filed in April and read in a hearing in November has to say what the label said
in April, and a JOIN to a master somebody has since corrected says what the label
says now — which is a different claim.

A DUAL MIX WITH NOTHING ON ONE SIDE IS REFUSED. That is not pedantry about a
checkbox: the whole point of the flag is that an application filed against this
mix will ask which set was running when, and a mix that says "two sets" while
putting every product on "Both" cannot answer. It is a single-set mix somebody
ticked by accident, and the cheapest place to find that out is here.
"""

import frappe
from frappe import _
from frappe.model.document import Document

BOTH = "Both"
SET_A = "A"
SET_B = "B"


class SprayTankMix(Document):
	def validate(self):
		if not self.products:
			frappe.throw(
				_(
					"A tank mix has to have something in the tank. Add at least one product with "
					"its own rate per acre."
				)
			)
		self._check_rates()
		self._check_duplicates()
		self._check_nozzle_sets()
		self._roll_up_intervals()
		self._acres_per_tank()

	def _check_rates(self):
		for line in self.products:
			try:
				rate = float(line.rate_per_acre or 0)
			except (TypeError, ValueError):
				frappe.throw(_("Rate / Acre must be a number on row {0}.").format(line.idx))
			if rate <= 0:
				frappe.throw(
					_(
						"Rate / Acre must be greater than zero on row {0} ({1}). A product in the "
						"tank at no rate is either not in the tank or is a rate nobody entered, and "
						"the two need different corrections."
					).format(line.idx, line.item)
				)

	def _check_duplicates(self):
		"""One product, one line.

		Two lines for the same Item are two rates for one product, and every
		reader — the acre arithmetic, the label roll-up, the record an inspector
		reads — would have to pick one. A farm genuinely putting a product in at
		two rates is describing two mixes.
		"""
		seen = {}
		for line in self.products:
			key = str(line.item or "")
			if key in seen:
				frappe.throw(
					_(
						"{0} is on rows {1} and {2}. One product gets one rate in one tank — two "
						"lines is two rates, and nothing reading this mix could say which applied."
					).format(key, seen[key], line.idx)
				)
			seen[key] = line.idx

	def _check_nozzle_sets(self):
		if not self.dual_nozzle:
			return
		sets = {str(line.nozzle_set or BOTH) for line in self.products}
		if SET_A not in sets or SET_B not in sets:
			frappe.throw(
				_(
					"This mix is marked Dual Flip Nozzle but nothing is assigned to set {0}. A dual "
					"mix is products split across two sets that get flipped mid-pass; if everything "
					"runs through whichever set is on, untick Dual Flip Nozzle — it is a single-set "
					"mix. Assign each product to A, to B, or to Both."
				).format(SET_B if SET_B not in sets else SET_A)
			)
		if not self.nozzle_set_a or not self.nozzle_set_b:
			frappe.throw(
				_(
					"A dual mix names both nozzle sets. Set A and Set B are what the application "
					"records as having been flipped between, and a flip to an unnamed set is not a "
					"record of anything."
				)
			)
		if self.nozzle_set_a == self.nozzle_set_b:
			frappe.throw(
				_(
					"Set A and Set B are the same nozzle configuration ({0}), which is one set "
					"rather than two. Nothing gets flipped."
				).format(self.nozzle_set_a)
			)

	def _roll_up_intervals(self):
		"""The strictest label in the tank, onto the header. Never an average."""
		rei_hours = phi_days = 0.0
		rei_item = phi_item = ""
		for line in self.products:
			hours = _number(line.rei_hours)
			days = _number(line.phi_days)
			if hours > rei_hours:
				rei_hours, rei_item = hours, str(line.item or "")
			if days > phi_days:
				phi_days, phi_item = days, str(line.item or "")
		self.rei_hours = rei_hours
		self.rei_source_item = rei_item or None
		self.phi_days = phi_days
		self.phi_source_item = phi_item or None

	def _acres_per_tank(self):
		"""Tank size over carrier rate. Computed, never typed — see the JSON."""
		tank = _number(self.tank_size_gal)
		carrier = _number(self.carrier_gpa)
		self.acres_per_tank = round(tank / carrier, 3) if tank and carrier else 0.0


def _number(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0
