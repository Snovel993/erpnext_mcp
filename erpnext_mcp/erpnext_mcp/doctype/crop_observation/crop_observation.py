# SPDX-License-Identifier: MIT
"""Controller for Crop Observation — what was seen, and nothing about what to do.

THE THRESHOLD EVALUATION IS NOT HERE. It lives in `tools/cropprotect.py`, and
the split is deliberate rather than incidental: evaluating a threshold means
resolving one (a query), upserting a Pest Pressure row (a write to another
doctype) and possibly generating a recommendation (a second write). A controller
that did all that would fire on every save from every surface — a Desk edit
correcting a typo in the notes would regenerate a recommendation somebody had
already declined.

So this validates the observation AS A MEASUREMENT and stops. The pipeline is
driven from the tool, once, at the moment the observation is filed.

WHAT IS VALIDATED IS ONLY WHAT MAKES THE NUMBER MEANINGLESS. A negative count, a
sample smaller than the count taken out of it, a percentage over a hundred, a
date in the future. Not a missing sample size — that is flagged downstream and
recorded, because a scout who saw something worth writing down should never be
arguing with a form.
"""

import frappe
from frappe import _
from frappe.model.document import Document

THREAT_CATEGORIES = ("Insect", "Disease", "Weed", "Vertebrate", "Abiotic", "Nutrient")

#: Sample units where the count is a proportion rather than a tally, and so
#: cannot exceed one hundred. Kept as a set rather than a substring test on
#: "Percent" so that a unit added later has to be considered rather than
#: silently inheriting a rule written for a different shape of number.
PERCENT_UNITS = ("Percent Infested", "Percent Defoliation", "Percent Dry Weight")


class CropObservation(Document):
	def validate(self):
		if self.threat_category not in THREAT_CATEGORIES:
			frappe.throw(_("Threat Category must be one of: {0}.").format(", ".join(THREAT_CATEGORIES)))
		if not str(self.threat or "").strip():
			frappe.throw(_("An observation is of something. Name the threat."))

		count = _number(self.count_observed)
		if count < 0:
			frappe.throw(
				_(
					"Count Observed cannot be negative. A count of nothing is 0, which is a real "
					"observation and a useful one — it is how a block is shown to have been walked "
					"and found clean."
				)
			)
		if self.sample_size not in (None, ""):
			size = int(self.sample_size)
			if size < 0:
				frappe.throw(_("Sample Size cannot be negative."))
			if size and str(self.sample_unit or "") in PERCENT_UNITS and count > 100:
				frappe.throw(
					_("Count Observed is {0} on a percentage unit ({1}).").format(count, self.sample_unit)
				)
		if self.percent_affected not in (None, "") and not 0 <= _number(self.percent_affected) <= 100:
			frappe.throw(_("Percent Affected must be between 0 and 100."))
		if _number(self.beneficials_observed) < 0:
			frappe.throw(_("Beneficials Observed cannot be negative."))
		if self.observed_on and str(self.observed_on) > str(frappe.utils.nowdate()):
			frappe.throw(
				_(
					"Observed On is {0}, which is in the future. A scouting round is filed after it "
					"is walked; a future-dated observation puts a pressure trend ahead of the season "
					"and no later correction can tell it apart from a real one."
				).format(self.observed_on)
			)
		if not self.observed_at and self.observed_on:
			self.observed_at = f"{self.observed_on} 12:00:00"


def _number(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0
