# SPDX-License-Identifier: MIT
"""Controller for Crop — what is grown, as a record rather than as a string.

THE DOCNAME IS THE CROP NAME, AND RENAME IS OFF. `field:crop_name` with
`allow_rename: 0`. Every other record that names a crop spells this string, and
a master that can be re-keyed is a master whose name on last season's records
means something else this season. Renaming a crop is therefore not an edit; it
is a new crop and a migration of what pointed at the old one.

A HARVEST WINDOW IS BOTH ENDS OR NEITHER. A start with no end is a season
nothing closes, and every reader of it has to guess the other half. The window
is deliberately ALLOWED TO WRAP the year — November to February is a real
harvest, and a controller that insisted start <= end would be refusing the
southern hemisphere and the greenhouse both, which is a rule about integers
mistaken for a rule about farming.

`maturity_years` IS A CONTRADICTION ON AN ANNUAL, NOT A LONG NUMBER. Years to
first commercial crop only means anything where the planting lives across
seasons. A Crop Variety row claiming three years to maturity under an Annual
growth cycle is refused by name, because the alternative — storing it — puts a
number into the capitalisation of development cost for a planting that has no
development period at all.

TWO UNIQUENESS RULES, BOTH ABOUT DOUBLE-COUNTING. Two variety rows called
'Bing' are two rows about one tree, and every read that groups a packout or a
yield by variety silently doubles it. Two water rows for 'Bloom' are two answers
to one irrigation question, and which one wins depends on row order. Neither is
a database nicety; both are wrong answers that look like right ones.

WHAT THIS CONTROLLER DOES NOT DO IS DECIDE A PHI. `default_phi_days` is the
crop's own floor for the case where nothing more specific is known. The binding
interval is on the label of the material actually applied, and a spray gate that
read this column and stopped reading would be a gate that clears fruit a label
would hold. See `tools/agronomy.get_crop`, which reports the number and says
what it is not.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The growth cycles for which `maturity_years` is a meaningful number. An
#: annual and a biennial both finish inside the period they are planted for, so
#: "years until it bears" is not a question either of them has.
BEARING_CYCLES = ("Perennial",)

#: The highest crop coefficient this controller will store. Kc runs from about
#: 0.2 in dormancy to about 1.2 at full canopy; the ceiling is set well clear of
#: the real range so it only ever catches a decimal point in the wrong place,
#: which is the error that would otherwise multiply an irrigation set by ten.
MAX_KC = 1.5


def _row_index(row, position: int) -> int:
	"""The row number to put in a refusal: Frappe's `idx` where there is one.

	A child row that has never been through a save has no `idx` yet, and a
	message reading "Row None" is a message nobody can act on — so the position
	in the list stands in. Read with `.get` rather than attribute access because
	that is what every other controller in this app does with a child row, and
	because the two are not the same object at every point in a document's life.
	"""
	return int(row.get("idx") or position)


class Crop(Document):
	def validate(self):
		self.crop_name = str(self.crop_name or "").strip()
		if not self.crop_name:
			frappe.throw(_("Crop Name is required — it is the docname every other record spells."))
		self.scientific_name = str(self.scientific_name or "").strip()

		self._check_counts()
		self._check_harvest_window()
		self._check_varieties()
		self._check_water_requirements()

	def _check_counts(self) -> None:
		"""Days and intervals are counts, so negative ones are typos, not values."""
		for fieldname, label in (
			("days_to_harvest", "Days to Harvest"),
			("default_phi_days", "Default PHI"),
		):
			value = self.get(fieldname)
			if value in (None, ""):
				continue
			if int(value) < 0:
				frappe.throw(_("{0} cannot be negative.").format(label))

	def _check_harvest_window(self) -> None:
		"""Both ends of the window or neither.

		Deliberately says nothing about the ORDER of the two months. A window
		that wraps the year end is a real harvest, and the obvious `start <= end`
		check would refuse it — which is a rule about integers wearing the
		costume of a rule about farming.
		"""
		start = str(self.harvest_window_start or "").strip()
		end = str(self.harvest_window_end or "").strip()
		if bool(start) == bool(end):
			return
		named, missing = ("start", "end") if start else ("end", "start")
		frappe.throw(
			_(
				"The harvest window has a {0} and no {1}. Half a window is a season nothing "
				"closes, and every reader of it has to guess the other month. Give both or "
				"neither — a window that wraps the year end (November to February) is accepted."
			).format(named, missing),
			title=_("Incomplete Harvest Window"),
		)

	def _check_varieties(self) -> None:
		"""No duplicate variety, and no years-to-maturity on something that has none."""
		seen: dict = {}
		for position, row in enumerate(self.varieties or [], start=1):
			index = _row_index(row, position)
			variety_name = str(row.get("variety_name") or "").strip()
			if not variety_name:
				frappe.throw(_("Row {0}: a variety needs a name.").format(index))

			key = variety_name.casefold()
			if key in seen:
				frappe.throw(
					_(
						"Rows {0} and {1} are both called {2}. Two rows about one tree double it "
						"in every read that groups a packout or a yield by variety — keep one."
					).format(seen[key], index, variety_name),
					title=_("Duplicate Variety"),
				)
			seen[key] = index

			maturity = int(row.get("maturity_years") or 0)
			if float(row.get("expected_yield_per_acre") or 0) < 0:
				frappe.throw(_("Row {0}: expected yield cannot be negative.").format(index))
			if maturity < 0:
				frappe.throw(_("Row {0}: years to maturity cannot be negative.").format(index))
			if maturity and self.growth_cycle not in BEARING_CYCLES:
				frappe.throw(
					_(
						"Row {0} ({1}) says it takes {2} year(s) to bear, but {3} is a {4} crop — "
						"it finishes inside the season it is planted for, so there are no "
						"non-bearing years to record. Either clear the years, or the growth "
						"cycle is wrong."
					).format(
						index,
						variety_name,
						maturity,
						self.crop_name,
						str(self.growth_cycle or "").lower(),
					),
					title=_("Maturity Years on a Non-Perennial"),
				)

	def _check_water_requirements(self) -> None:
		"""One row per growth stage, and a Kc inside the range a Kc can be."""
		seen: dict = {}
		for position, row in enumerate(self.water_requirements or [], start=1):
			index = _row_index(row, position)
			stage = str(row.get("growth_stage") or "").strip()
			if not stage:
				frappe.throw(_("Row {0}: a water requirement needs a growth stage.").format(index))
			if stage in seen:
				frappe.throw(
					_(
						"Rows {0} and {1} are both about {2}. Two answers to how much water this "
						"crop needs at one stage is the same as none — which one an irrigation "
						"plan reads would depend on row order."
					).format(seen[stage], index, stage),
					title=_("Duplicate Growth Stage"),
				)
			seen[stage] = index

			kc = float(row.get("crop_coefficient_kc") or 0)
			if kc < 0:
				frappe.throw(_("Row {0}: a crop coefficient cannot be negative.").format(index))
			if float(row.get("water_inches_per_week") or 0) < 0:
				frappe.throw(_("Row {0}: weekly water cannot be negative.").format(index))
			if kc > MAX_KC:
				frappe.throw(
					_(
						"Row {0} ({1}) has a crop coefficient of {2}. Kc runs from about 0.2 in "
						"dormancy to about 1.2 at full canopy — a value above {3} is a decimal "
						"point in the wrong place, and it would multiply an irrigation set by ten."
					).format(index, stage, kc, MAX_KC),
					title=_("Crop Coefficient Out of Range"),
				)
