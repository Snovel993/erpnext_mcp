# SPDX-License-Identifier: MIT
"""Controller for Financial KPI Definition — where a KPI is refused before it lies.

EVERY CHECK HERE IS ABOUT ONE FAILURE MODE, AND IT IS ALWAYS THE SAME ONE: a
definition that is accepted, saved, computed nightly, drawn on a dashboard, and
wrong in a way nothing announces. A compliance rule that is wrong accuses
somebody of a failure, which is loud. A KPI that is wrong is a number beside
four other numbers, and the only thing that says it is wrong is that somebody
eventually recomputes it by hand.

So the controller refuses at SAVE time what would otherwise be discovered at
compute time, and it says why in the words of the thing being defined.

  * A `kpi_id` that already exists. It is the cache key on every
    `Financial KPI History` row, so two definitions sharing one would write into
    each other's series and the chart would hold two different numbers on one
    line. Unique at the schema level AND here, because the message matters.
  * A `kpi_id` that is not a key. Spaces and capitals in a cache key are how a
    series splits in two the first time somebody types it slightly differently.
  * A Built-in naming a computer this site has not got. It would produce
    nothing, for ever, quietly — the same failure `Compliance Rule.builtin_scanner`
    is guarded against, for the same reason.
  * An Expression that does not parse, or that reads a variable
    `expression_inputs` does not define. Both are found by `check_expression`,
    which is the same function the evaluator runs, so a definition that saves is
    one that will evaluate.
  * A threshold pair that crosses. A critical floor ABOVE the warning floor can
    never be read the way it is written: every value past critical is also past
    warning, and the dashboard would report the lesser of the two on the more
    serious breach.
  * A window that is not a window — a step, a type or a month count outside what
    the standard has.

WHAT IT DELIBERATELY DOES NOT DO IS VERSION BY COPY. A Compliance Rule edit
writes v+1 and leaves the old row, so an alert raised in April can still be read
against the definition that raised it. A KPI is a LINE rather than an event, and
a line assembled from two definitions of one number is a chart with an unmarked
join in it. Editing the arithmetic of a live KPI is a NEW kpi_id beside the old
one; the controller cannot enforce that, but `update_financial_kpi_definition`
says it out loud when the formula moves.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import shifts
from erpnext_mcp.services import kpi_engine
from erpnext_mcp.services import windowed_reports as windows

DOCTYPE = "Financial KPI Definition"


class FinancialKPIDefinition(Document):
	def autoname(self):
		"""KPID-YYYY-0001, where YYYY is the year the definition was authored."""
		year = str(frappe.utils.today())[:4]
		self.name = shifts.next_in_series(DOCTYPE, "KPID", year)

	def validate(self):
		self._require_a_usable_key()
		self._refuse_a_duplicate_key()
		self._require_a_coherent_window()
		self._require_a_formula()
		self._require_thresholds_that_can_be_read()

	# ── the key ─────────────────────────────────────────────────────────
	def _require_a_usable_key(self) -> None:
		key = str(self.kpi_id or "").strip()
		if not key:
			frappe.throw(
				_(
					"kpi_id is required. It is the stable key this KPI's cached history is filed "
					"under, and it is what every chart, every alert and every export joins on. "
					"Nothing was saved."
				)
			)
		normalized = key.lower().replace(" ", "_").replace("-", "_")
		if key != normalized or not normalized.replace("_", "").isalnum():
			frappe.throw(
				_(
					"kpi_id must be lower-case letters, digits and underscores — {0} would be "
					"{1}. It is a KEY and not a label: it is written into every cached history "
					"row, and a key that can be typed two ways is a series that splits in two the "
					"first time somebody types it the other way. `title` is where the words go. "
					"Nothing was saved."
				).format(key, normalized)
			)
		self.kpi_id = normalized

	def _refuse_a_duplicate_key(self) -> None:
		existing = frappe.db.get_all(
			DOCTYPE,
			filters={"kpi_id": self.kpi_id, "name": ("!=", self.name or "")},
			pluck="name",
			limit=1,
		)
		if existing:
			frappe.throw(
				_(
					"{0} already holds the kpi_id {1}. THE KEY IS THE CACHE KEY: every "
					"Financial KPI History row carries it, so two definitions sharing one would "
					"write into each other's series and a single chart line would hold two "
					"different numbers with nothing marking where one stops. Edit that definition, "
					"or pick a new kpi_id and let both series stay readable. Nothing was saved."
				).format(existing[0], self.kpi_id)
			)

	# ── the window ──────────────────────────────────────────────────────
	def _require_a_coherent_window(self) -> None:
		window_type = str(self.default_window_type or windows.WINDOW_TTM)
		if window_type not in windows.WINDOW_TYPES:
			frappe.throw(
				_("default_window_type must be one of {0}; got {1}. Nothing was saved.").format(
					", ".join(windows.WINDOW_TYPES), window_type
				)
			)
		step = str(self.default_computation_step or windows.STEP_MONTHLY)
		if step not in windows.STEPS:
			frappe.throw(
				_("default_computation_step must be one of {0}; got {1}. Nothing was saved.").format(
					", ".join(windows.STEPS), step
				)
			)
		months = int(self.default_window_months or windows.DEFAULT_WINDOW_MONTHS)
		if months < 1 or months > 120:
			frappe.throw(
				_(
					"default_window_months must be between 1 and 120; got {0}. TTM is 12, which is "
					"what the T and the M mean. Nothing was saved."
				).format(months)
			)
		self.default_window_months = months

		lookback = int(self.historical_lookback_years or kpi_engine.DEFAULT_LOOKBACK_YEARS)
		if lookback < 0 or lookback > windows.MAX_LOOKBACK_YEARS:
			frappe.throw(
				_(
					"historical_lookback_years must be between 0 and {0}; got {1}. Past that, "
					"every extra entry is a row saying 'no ledger', which is a slower way of "
					"saying nothing. Nothing was saved."
				).format(windows.MAX_LOOKBACK_YEARS, lookback)
			)
		self.historical_lookback_years = lookback

	# ── the formula ─────────────────────────────────────────────────────
	def _require_a_formula(self) -> None:
		formula_type = str(self.formula_type or kpi_engine.FORMULA_BUILTIN)
		if formula_type not in kpi_engine.FORMULA_TYPES:
			frappe.throw(
				_(
					"formula_type must be {0}. There is no third value, and there is deliberately "
					"no field on this record that holds Python: a financial KPI is a number "
					"divided by another number, and a KPI whose shape is neither of these wants a "
					"built-in computer — six lines in services/financial_reports.py, with a test. "
					"Nothing was saved."
				).format(" or ".join(kpi_engine.FORMULA_TYPES))
			)

		if formula_type == kpi_engine.FORMULA_BUILTIN:
			self._require_a_real_builtin()
			return
		self._require_an_expression_that_parses()

	def _require_a_real_builtin(self) -> None:
		named = str(self.builtin_function or "").strip()
		if not named:
			frappe.throw(
				_(
					"a Built-in KPI has to name a builtin_function. This site has: {0}. Nothing was saved."
				).format(", ".join(kpi_engine.builtin_names()) or "<none>")
			)
		if named not in windows.COMPUTERS:
			frappe.throw(
				_(
					"{0} is not a built-in computer on this site. It has: {1}. A definition "
					"pointing at a computer that does not exist produces nothing, for ever, and "
					"says nothing about it — which on a dashboard is indistinguishable from a "
					"business with no activity. Nothing was saved."
				).format(named, ", ".join(kpi_engine.builtin_names()) or "<none>")
			)
		self.builtin_function = named

	def _require_an_expression_that_parses(self) -> None:
		try:
			specs = kpi_engine.parse_inputs(self.expression_inputs)
		except kpi_engine.ExpressionError as exc:
			frappe.throw(_("{0} Nothing was saved.").format(str(exc)))

		try:
			checked = kpi_engine.check_expression(self.expression, specs.keys())
		except kpi_engine.ExpressionError as exc:
			frappe.throw(_("{0} Nothing was saved.").format(str(exc)))

		self._refuse_a_self_reference(specs)
		if checked["unused"]:
			# NOT A REFUSAL. The expression is correct and will evaluate; an unused
			# input is usually half of a rename, and refusing the save would mean an
			# operator could not fix the other half.
			frappe.msgprint(
				_(
					"expression_inputs defines {0}, which the expression never reads. That is "
					"usually a rename that got as far as one field. The KPI is saved and will "
					"compute; the unused input is simply queried for nothing."
				).format(", ".join(checked["unused"])),
				indicator="orange",
			)

	def _refuse_a_self_reference(self, specs: dict) -> None:
		"""A KPI that reads itself, directly or one hop away.

		The engine catches a cycle at compute time and reports it rather than
		running out of stack, which is the guarantee that matters. This catches
		the two shapes somebody actually builds by hand — a definition naming
		itself, and a pair naming each other — at the moment they are typed,
		which is when it is still obvious what was meant.
		"""
		targets = [
			str(spec.get("kpi_id")) for spec in specs.values() if spec.get("source") == kpi_engine.SOURCE_KPI
		]
		if self.kpi_id in targets:
			frappe.throw(
				_(
					"{0} reads itself. A KPI that is an input to its own formula has no value at "
					"all — there is no order in which the two halves can be computed. Nothing was "
					"saved."
				).format(self.kpi_id)
			)
		for target in targets:
			row = kpi_engine.definition_row(target)
			if not row:
				frappe.throw(
					_(
						"the expression reads the KPI {0}, which is not a definition on this "
						"site. Nothing was saved."
					).format(target)
				)
			try:
				inner = kpi_engine.parse_inputs(row.get("expression_inputs"))
			except kpi_engine.ExpressionError:
				continue
			back = [
				str(spec.get("kpi_id"))
				for spec in inner.values()
				if spec.get("source") == kpi_engine.SOURCE_KPI
			]
			if self.kpi_id in back:
				frappe.throw(
					_(
						"{0} reads {1}, and {1} reads {0} back. Neither can be computed first, so "
						"neither has a value. Point one of them at its own inputs directly. "
						"Nothing was saved."
					).format(self.kpi_id, target)
				)

	# ── the thresholds ──────────────────────────────────────────────────
	def _require_thresholds_that_can_be_read(self) -> None:
		# `thresholds_of` READS ALL FOUR AT ZERO AS NONE AT ALL, and this check
		# depends on it rather than repeating the reasoning: a Frappe `Float` is
		# `NOT NULL DEFAULT 0`, so a definition saved with no thresholds — the
		# seeded one, and any created through the tool without them — arrives
		# here as four zeros. Read literally that is a floor and a ceiling at
		# the same number, which is what the last check below refuses, and it
		# would refuse every definition that declined to draw a line.
		limits = kpi_engine.thresholds_of(self.as_dict())
		low_warning = limits["threshold_warning_low"]
		low_critical = limits["threshold_critical_low"]
		high_warning = limits["threshold_warning_high"]
		high_critical = limits["threshold_critical_high"]

		if low_warning is not None and low_critical is not None and low_critical > low_warning:
			frappe.throw(
				_(
					"the critical floor ({0}) is above the warning floor ({1}). That pair can "
					"never be read the way it is written: every value past critical is also past "
					"warning, so the critical line is the one that never fires on its own and the "
					"dashboard reports the lesser of the two on the more serious breach. Critical "
					"is the WORSE number, so it goes further from the safe side. Nothing was saved."
				).format(low_critical, low_warning)
			)
		if high_warning is not None and high_critical is not None and high_critical < high_warning:
			frappe.throw(
				_(
					"the critical ceiling ({0}) is below the warning ceiling ({1}), which is the "
					"same pair of lines crossed in the other direction. Critical is the WORSE "
					"number. Nothing was saved."
				).format(high_critical, high_warning)
			)

		low = low_critical if low_critical is not None else low_warning
		high = high_critical if high_critical is not None else high_warning
		if low is not None and high is not None and low >= high:
			frappe.throw(
				_(
					"the low thresholds ({0}) and the high thresholds ({1}) overlap, so every "
					"possible value breaches one of them and the KPI is permanently in alert. A "
					"two-sided threshold needs a band between the floors and the ceilings that "
					"counts as healthy. Nothing was saved."
				).format(low, high)
			)
