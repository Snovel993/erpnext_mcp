# SPDX-License-Identifier: MIT
"""Controller for Breakeven Analysis — the record behind "what price do I need?".

THREE THINGS HAPPEN HERE AND NOWHERE ELSE.

THE WINDOW FILLS ITSELF FROM THE FISCAL YEAR. A grower asking about the 2026
season should not have to type two dates that the year already knows, and a
window typed by hand is a window that can silently disagree with the year the
record claims to be for. Typed dates always win — an analysis of the harvest
months inside a year is a real thing to want.

AN EDITED INPUT GOES STALE, IT DOES NOT RECOMPUTE. `validate` compares the
inputs against what was on the row before the save and flips a Computed record
to Stale when any of them moved. It would be easy to recompute instead, and it
would be wrong: recomputing on save means a breakeven changes underneath the
person reading it, with the same `computed_on` it had a moment ago and no record
that anything moved. Stale says "these numbers answer the old question", which is
the only honest thing a record in that state can say. `compute_breakeven` sets
the flag that exempts its own save.

THE PACKOUT IS REFUSED AT ZERO AND ABOVE A HUNDRED. Zero is the interesting one:
it is arithmetically a division by zero in every per-unit figure that follows,
and semantically it is not a business — a block with no sellable fruit has no
breakeven price, it has a loss. Refusing it at the field is what stops that
becoming an infinity somewhere downstream that renders as a large, confident
number.

WHAT THIS CONTROLLER DELIBERATELY DOES NOT DO: the costing arithmetic. That
lives in `tools/breakeven.py` because it reads the ledger, the chart of accounts
and the market register, and a controller that did all that would run on every
Desk save of every field including `notes`.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Changing any of these invalidates a stored result. `cost_lines` is here too:
#: reclassifying an account by hand in the Desk changes both piles, and is
#: exactly the edit somebody makes between reading a breakeven and disbelieving
#: it.
INPUT_FIELDS = (
	"expected_harvest_units",
	"packout_pct",
	"expected_price",
	"cull_credit_per_unit",
	"cost_source",
	"from_date",
	"to_date",
	"cost_center",
	"fiscal_year",
	"company",
	"unit_label",
)


class BreakevenAnalysis(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		parts = [str(self.analysis_name or "").strip(), str(self.fiscal_year or "").strip()]
		stem = " ".join(part for part in parts if part)
		self.name = f"{stem} - {abbr}" if abbr else stem

	def validate(self):
		self.analysis_name = str(self.analysis_name or "").strip()
		if not self.analysis_name:
			frappe.throw(_("Analysis Name is required."))
		self.crop_type = str(self.crop_type or "").strip()
		if not self.crop_type:
			frappe.throw(_("Crop is required — a breakeven is always a breakeven ON something."))

		self._fill_the_window()
		self._refuse_impossible_volumes()
		self._default_currency()
		self._derive_sellable_units()
		self._go_stale_if_an_input_moved()

	def _fill_the_window(self) -> None:
		if self.from_date and self.to_date:
			if self.from_date > self.to_date:
				frappe.throw(_("From Date {0} is after To Date {1}.").format(self.from_date, self.to_date))
			return
		year = frappe.db.get_value(
			"Fiscal Year", self.fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
		)
		if not year:
			# A fiscal year that does not resolve is a link error Frappe will
			# report itself; filling nothing here leaves the window empty and
			# `compute_breakeven` says so rather than reading the whole ledger.
			return
		if not self.from_date:
			self.from_date = year.get("year_start_date")
		if not self.to_date:
			self.to_date = year.get("year_end_date")

	def _refuse_impossible_volumes(self) -> None:
		harvest = _as_float(self.expected_harvest_units, "Expected Harvest")
		if harvest <= 0:
			frappe.throw(
				_(
					"Expected Harvest must be greater than zero. A block with no crop has no "
					"breakeven price — it has a loss equal to its fixed costs, which is a "
					"different question from the one this record answers."
				)
			)
		packout = _as_float(self.packout_pct, "Packout %")
		if packout <= 0:
			frappe.throw(
				_(
					"Packout % must be greater than zero. At zero packout every per-unit figure "
					"on this record is a division by zero, and the result would render as a "
					"large confident number rather than as the impossibility it is."
				)
			)
		if packout > 100:
			frappe.throw(
				_("Packout % is {0}. More fruit cannot pack out than came off the trees.").format(packout)
			)
		if _as_float(self.expected_price, "Expected Price") < 0:
			frappe.throw(_("Expected Price cannot be negative."))
		if _as_float(self.cull_credit_per_unit, "Cull Credit / Unit") < 0:
			frappe.throw(
				_(
					"Cull Credit / Unit cannot be negative. A cull that costs money to dispose of "
					"is a cost line, not a negative price — putting it here would net it against "
					"the fruit that did pack, which is where it stops being visible."
				)
			)

	def _default_currency(self) -> None:
		if self.currency:
			return
		self.currency = frappe.db.get_value("Company", self.company, "default_currency") or None

	def _derive_sellable_units(self) -> None:
		harvest = float(self.expected_harvest_units or 0)
		packout = float(self.packout_pct or 0) / 100.0
		self.sellable_units = round(harvest * packout, 6)

	def _go_stale_if_an_input_moved(self) -> None:
		if self.flags.get("breakeven_computed"):
			# `compute_breakeven`'s own save. It has just written the results
			# these inputs produce, so they are by definition not stale.
			return
		if self.is_new() or str(self.status or "") != "Computed":
			return
		before = self.get_doc_before_save()
		if before is None:
			return
		moved = [field for field in INPUT_FIELDS if _differs(before.get(field), self.get(field))]
		if not moved:
			# The child table is compared separately: `_differs` on two lists of
			# Documents is never usefully equal, and a cost line whose behaviour
			# somebody changed is the edit most worth catching.
			if not _lines_differ(before.get("cost_lines"), self.get("cost_lines")):
				return
			moved = ["cost_lines"]
		self.status = "Stale"
		self.computation_warnings = (
			f"{', '.join(moved)} changed after the last computation on {self.computed_on or 'an unknown date'}. "
			"The results below still answer the OLD inputs — run compute_breakeven to bring them forward. "
			"Nothing was recomputed on save on purpose: a breakeven that changed underneath the person "
			"reading it, keeping the same computed_on, is worse than one that says it is out of date."
		)


def _as_float(value, label: str) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		frappe.throw(_("{0} must be a number.").format(label))
		return 0.0


def _differs(before, after) -> bool:
	"""Whether two field values are meaningfully different.

	Compared as strings because a Date arrives as a `date` from the database and
	as a `str` from a tool payload, and a comparison that called those two
	different would mark every record Stale on the first save that touched it.
	"""
	return str(before if before is not None else "") != str(after if after is not None else "")


def _lines_differ(before, after) -> bool:
	def shape(rows):
		return [
			(
				str(_row(row, "account") or ""),
				str(_row(row, "cost_behavior") or ""),
				str(_row(row, "volume_basis") or ""),
				str(_row(row, "variable_pct") or 0),
				str(_row(row, "amount") or 0),
			)
			for row in rows or []
		]

	return shape(before) != shape(after)


def _row(row, key):
	if isinstance(row, dict):
		return row.get(key)
	return getattr(row, key, None)
