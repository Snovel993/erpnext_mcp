# SPDX-License-Identifier: MIT
"""Controller for Spray Application — the event, and the advisories it earns.

THE WEATHER IS RECORDED AND NOT ENFORCED, and that is the whole posture of this
record. Wind at 14 mph is outside every label's window, and a controller that
refused to save the document would not have prevented the spray — the tank went
out three hours ago. It would only have prevented the RECORD of the spray, which
is the half an inspector actually asks for and the half the operator is least
motivated to keep. So the out-of-window condition is written onto the document
as an advisory, in words, where it is visible to a report and to an auditor, and
the document saves.

Two directions matter and only one is obvious. Above the window is drift. BELOW
the window is a temperature inversion — still air holds the spray in a layer
that moves off-target later and further than a breeze would have — which is why
a low wind reading earns an advisory rather than a clean bill.

`total_acres` AND `gallons_per_acre` ARE COMPUTED, NEVER TYPED. The first is the
sum of the block rows; the second is the calibration arithmetic off the nozzle
set and the ground speed. Both are figures a person can produce independently of
the rows they come from, and two independent figures for one quantity is a pair
that will disagree — here in front of a state inspector recomputing the second
one on a calculator.
"""

import frappe
from frappe import _
from frappe.model.document import Document

APPLIED = "Applied"
PLANNED = "Planned"
CANCELLED = "Cancelled"

#: The ordinary label window for wind speed, in mph. Most product labels state
#: something close to this pair; a few state their own, which is why exceeding it
#: is an advisory naming the number rather than a rule enforcing one.
WIND_MIN_MPH = 3.0
WIND_MAX_MPH = 10.0

#: Above this, volatilisation and evaporative loss are the label's concern on
#: most summer materials.
TEMP_ADVISORY_F = 90.0

#: The terms behind the 5940 in the calibration formula. Named individually
#: because a bare 5940 is a number a reader has to take on faith, and this is
#: the one figure on this record a state inspector recomputes by hand.
FEET_PER_MILE = 5280.0
INCHES_PER_FOOT = 12.0
SQ_FT_PER_ACRE = 43560.0
SQ_IN_PER_ACRE = SQ_FT_PER_ACRE * INCHES_PER_FOOT * INCHES_PER_FOOT
MINUTES_PER_HOUR = 60.0

#: Inches travelled per minute at one mile per hour: 5280 × 12 ÷ 60 = 1056.
INCHES_PER_MINUTE_PER_MPH = (FEET_PER_MILE * INCHES_PER_FOOT) / MINUTES_PER_HOUR

#: The constant every calibration chart calls 5940 — square inches in an acre
#: divided by the inches a machine covers in a minute at 1 mph.
GPA_CONSTANT = SQ_IN_PER_ACRE / INCHES_PER_MINUTE_PER_MPH


class SprayApplication(Document):
	def validate(self):
		if not self.blocks:
			frappe.throw(
				_(
					"A spray application names the blocks it reached. A pass recorded against "
					"nowhere restricts nobody and costs nothing to any block."
				)
			)
		self._check_timing()
		self._check_blocks()
		self._total_acres()
		self._gallons_per_acre()
		self._weather_advisories()

	def _check_timing(self):
		if self.started_at and self.completed_at and str(self.completed_at) < str(self.started_at):
			frappe.throw(
				_(
					"Completed At ({0}) is before Started At ({1}). Every restricted-entry window "
					"runs from the completion, so a completion that precedes the start would open "
					"a window that closed before the spray began."
				).format(self.completed_at, self.started_at)
			)
		if self.flip_at and self.started_at and str(self.flip_at) < str(self.started_at):
			frappe.throw(_("Flipped At is before the application started."))
		if self.flip_at and self.completed_at and str(self.flip_at) > str(self.completed_at):
			frappe.throw(_("Flipped At is after the application finished."))

	def _check_blocks(self):
		"""One block, one row, and no negative acres.

		Two rows for one block is two acre figures for one place, and the product
		used on that block — rate times acres — would silently double. The block
		would also get two restricted-entry windows with the same expiry, which
		is not wrong so much as it is a register nobody can reconcile.
		"""
		seen = {}
		for row in self.blocks:
			key = (str(row.block_doctype or ""), str(row.block or ""))
			if key in seen:
				frappe.throw(
					_(
						"{0} is on rows {1} and {2}. One block gets one row — two rows is two acre "
						"figures for one place, and the product applied to it would double."
					).format(row.block, seen[key], row.idx)
				)
			seen[key] = row.idx
			if row.acres not in (None, "") and float(row.acres) < 0:
				frappe.throw(_("Acres cannot be negative on row {0}.").format(row.idx))
			if row.completed_at and row.started_at and str(row.completed_at) < str(row.started_at):
				frappe.throw(_("Completed At is before Started At on row {0}.").format(row.idx))

	def _total_acres(self):
		self.total_acres = round(sum(_number(row.acres) for row in self.blocks), 3)

	def _gallons_per_acre(self):
		"""Flow, spacing and speed → gallons per acre. Blank where any is missing.

		THE FORMULA IS THE STANDARD ONE off every calibration chart:

			GPA = (GPM per nozzle × 5940) / (mph × nozzle spacing in inches)

		5940 is not a physical constant. It is the square inches in an acre
		(43560 × 144) divided by the inches a machine covers in a minute at one
		mile per hour (5280 × 12 ÷ 60 = 1056), and it is spelled out in the
		constants above so nobody has to take it on faith — this is the one
		figure on the record an inspector recomputes by hand.

		An air-blast set has no meaningful boom spacing, so this stays blank for
		one. A wrong GPA is worse than none, because the wrong one gets filed.
		"""
		flow = spacing = 0.0
		config = self.nozzle_set_a or self.nozzle_set_b
		if config:
			try:
				row = frappe.db.get_value(
					"Spray Nozzle Config", config, ["flow_rate_gpm", "spacing_inches"], as_dict=True
				)
			except Exception:  # pragma: no cover - a site shaping these columns differently
				row = None
			if row:
				flow = _number(row.get("flow_rate_gpm"))
				spacing = _number(row.get("spacing_inches"))
		speed = _number(self.ground_speed_mph)
		if not (flow and spacing and speed):
			self.gallons_per_acre = 0.0
			return
		self.gallons_per_acre = round((flow * GPA_CONSTANT) / (speed * spacing), 3)

	def _weather_advisories(self):
		"""What was outside the ordinary window, in words, on the record.

		A Planned application earns none of these: nothing has been put on the
		ground, and an advisory about the weather at a moment that has not
		happened is noise on a work order.
		"""
		if str(self.status or "") == PLANNED:
			self.weather_advisories = ""
			return

		lines = []
		wind = self.wind_speed_mph
		if wind in (None, ""):
			lines.append(
				"No wind speed recorded. Wind at the time of application is the single most asked-for "
				"line on a state pesticide record, and it cannot be reconstructed afterwards."
			)
		else:
			wind = _number(wind)
			if wind > WIND_MAX_MPH:
				lines.append(
					f"Wind was {wind:g} mph, above the {WIND_MAX_MPH:g} mph most labels allow — drift risk. "
					f"Check this product's own label, which may state a different ceiling."
				)
			elif wind < WIND_MIN_MPH:
				lines.append(
					f"Wind was {wind:g} mph, below the {WIND_MIN_MPH:g} mph most labels require. Still air is "
					f"not the safe end: an inversion holds spray in a layer that moves off-target later "
					f"and further than a breeze would have carried it."
				)
		if self.temperature_f not in (None, "") and _number(self.temperature_f) > TEMP_ADVISORY_F:
			lines.append(
				f"Temperature was {_number(self.temperature_f):g} F, above {TEMP_ADVISORY_F:g} F — "
				f"volatilisation and evaporative loss are a label concern on most summer materials."
			)
		if not self.weather_source:
			lines.append(
				"Weather source not stated — a hand meter at the block and a station eight miles away are different facts."
			)
		self.weather_advisories = "\n".join(lines)


def _number(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0
