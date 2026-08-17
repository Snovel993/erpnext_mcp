# SPDX-License-Identifier: MIT
"""Controller for Pest Action Threshold — the number that decides an action.

THE WARNING THRESHOLD HAS TO SIT ON THE SAFE SIDE OF THE ACTION THRESHOLD, and
which side that is depends on the comparison. For a pest count (Greater Than)
the warning is BELOW the action number — you want a week's notice on the way up.
For a nutrient floor (Less Than) it is ABOVE it, for exactly the same reason.

That rule is worth a controller because getting it backwards produces a
threshold that never warns and then fires, which is indistinguishable at a
glance from a threshold with no warning at all. The failure is silent, it is
discovered in the one week it mattered, and by then the block is a write-off.

`comparison` IS ALSO WHY THIS FILE OWNS `exceeds`. Every reader of a threshold —
the observation that evaluates against it, the pressure record that counts how
often it was crossed, the recommendation that cites it — has to answer the same
question the same way. A second copy of a four-branch comparison somewhere else
is a second chance to write `>` where `<` belongs.
"""

import frappe
from frappe import _
from frappe.model.document import Document

GREATER = "Greater Than"
GREATER_EQUAL = "Greater Than Or Equal"
LESS = "Less Than"
LESS_EQUAL = "Less Than Or Equal"

#: The six categories. Fixed, because they are what an IPM programme reports
#: against — a seventh invented on one farm drops out of every roll-up.
THREAT_CATEGORIES = ("Insect", "Disease", "Weed", "Vertebrate", "Abiotic", "Nutrient")


def exceeds(value, threshold, comparison: str) -> bool:
	"""Whether `value` has crossed `threshold` in the direction `comparison` means.

	The ONE implementation. Imported by the observation, the pressure roll-up and
	the recommendation rather than reimplemented in each, because three copies of
	a four-branch comparison is three chances to write the wrong angle bracket —
	and the wrong one on a Nutrient threshold fires on every healthy block while
	staying silent on every deficient one.
	"""
	try:
		left = float(value)
		right = float(threshold)
	except (TypeError, ValueError):
		return False
	if comparison == GREATER_EQUAL:
		return left >= right
	if comparison == LESS:
		return left < right
	if comparison == LESS_EQUAL:
		return left <= right
	return left > right


def rises_with(comparison: str) -> bool:
	"""Whether a bigger number is a worse number under this comparison.

	True for the pest-count direction, False for a nutrient or moisture floor.
	Used to decide which side of the action number a warning belongs on, and to
	decide whether a rising series is a deteriorating one.
	"""
	return comparison not in (LESS, LESS_EQUAL)


class PestActionThreshold(Document):
	def validate(self):
		if not str(self.crop or "").strip():
			frappe.throw(_("A threshold is for a crop. Name it."))
		if not str(self.threat or "").strip():
			frappe.throw(_("A threshold is for a threat. Name it."))
		if self.threat_category not in THREAT_CATEGORIES:
			frappe.throw(_("Threat Category must be one of: {0}.").format(", ".join(THREAT_CATEGORIES)))
		if self.action_threshold in (None, ""):
			frappe.throw(_("Action Threshold is the number this record exists to hold."))
		self._check_warning_side()
		if self.min_sample_size not in (None, "") and int(self.min_sample_size) < 0:
			frappe.throw(_("Minimum Sample Size cannot be negative."))

	def _check_warning_side(self):
		if self.warning_threshold in (None, ""):
			return
		action = float(self.action_threshold or 0)
		warning = float(self.warning_threshold)
		upward = rises_with(str(self.comparison or GREATER))
		if upward and warning > action:
			frappe.throw(
				_(
					"Warning Threshold ({0}) is above Action Threshold ({1}) on a '{2}' comparison. "
					"The warning is meant to arrive FIRST — on the way up, that means below the "
					"action number. As written this threshold would warn only after it had already "
					"told somebody to act, which looks exactly like a threshold with no warning at "
					"all until the week it matters."
				).format(warning, action, self.comparison)
			)
		if not upward and warning < action:
			frappe.throw(
				_(
					"Warning Threshold ({0}) is below Action Threshold ({1}) on a '{2}' comparison. "
					"This threshold fires as a number FALLS, so the warning has to sit above the "
					"action number to arrive first."
				).format(warning, action, self.comparison)
			)
