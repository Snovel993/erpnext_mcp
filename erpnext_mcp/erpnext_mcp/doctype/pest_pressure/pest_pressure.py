# SPDX-License-Identifier: MIT
"""Controller for Pest Pressure — one threat on one block for one season.

THE ROLL-UP IS NOT HERE, for the reason it is not on Crop Observation either:
recomputing the running state means reading the observation register, and a
controller that did that would fire on every save from every surface. The upsert
is driven once, from `tools/cropprotect.py`, at the moment an observation is
filed. This file holds the invariants that must be true of the record whoever
wrote it.

`peak_value` IS NEVER ALLOWED BELOW `latest_value`. That is the one arithmetic
lie this record can tell that nothing downstream would catch: a season's
programme is planned off the peak, and a peak that has quietly been overwritten
by a later smaller reading reads as a mild year. So it is repaired on save
rather than trusted — the repair is cheap and the failure is invisible.
"""

import frappe
from frappe import _
from frappe.model.document import Document

MONITORING = "Monitoring"
WATCH = "Watch"
ACTION = "Action"
CONTROLLED = "Controlled"
CLOSED = "Closed"

STATUSES = (MONITORING, WATCH, ACTION, CONTROLLED, CLOSED)
THREAT_CATEGORIES = ("Insect", "Disease", "Weed", "Vertebrate", "Abiotic", "Nutrient")


class PestPressure(Document):
	def validate(self):
		if self.threat_category not in THREAT_CATEGORIES:
			frappe.throw(_("Threat Category must be one of: {0}.").format(", ".join(THREAT_CATEGORIES)))
		if self.status not in STATUSES:
			self.status = MONITORING
		if not self.season_year:
			frappe.throw(
				_(
					"Season Year is part of this record's identity. A pest that was a problem last "
					"year and is quiet this year has to be two stories, or the first flare of the "
					"new season is read against last year's peak and looks like nothing."
				)
			)
		self._keep_the_peak()
		self._order_the_dates()
		if self.status == CONTROLLED and not self.controlled_on:
			self.controlled_on = frappe.utils.nowdate()

	def _keep_the_peak(self):
		"""The worst reading of the season stays the worst reading of the season."""
		latest = _number(self.latest_value)
		if _number(self.peak_value) < latest:
			self.peak_value = latest
			self.peak_on = self.last_observed_on or frappe.utils.nowdate()

	def _order_the_dates(self):
		if (
			self.first_observed_on
			and self.last_observed_on
			and str(self.last_observed_on) < str(self.first_observed_on)
		):
			frappe.throw(
				_("Last Observed On ({0}) is before First Observed On ({1}).").format(
					self.last_observed_on, self.first_observed_on
				)
			)
		if (
			self.first_exceeded_on
			and self.last_exceeded_on
			and str(self.last_exceeded_on) < str(self.first_exceeded_on)
		):
			frappe.throw(_("Last Over Threshold is before First Over Threshold."))


def _number(value) -> float:
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0
