# SPDX-License-Identifier: MIT
"""Controller for Asset Cost Profile — the sidecar this app keeps beside an Asset.

WHY A SIDECAR RATHER THAN FIELDS ON ASSET. Everything here could have been ten
custom fields and two child tables bolted onto ERPNext's Asset. It is a separate
doctype instead, for the reason the app manifest gives: installing this app must
not change the behaviour of anything already on the site, and uninstalling it
must give the site back as it was. A profile is deleted with the app; an Asset
that had custom fields grafted onto it is not so easily returned.

THE INVARIANTS. Two, and both are here rather than only in the tools because a
profile can be edited in the Desk. The allocation has to total 100 — a
99-percent asset silently under-depreciates the business — and the frequency has
to divide the useful life exactly, because a schedule with a stub period at the
end is a schedule whose last entry nobody can explain.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Percentage points of slack allowed on the allocation total. Percent fields
#: hold two decimals, so three rows of 33.33 have to be allowed to fail — but a
#: tenth of a point of drift is a typo, not rounding.
ALLOCATION_TOLERANCE = 0.011


class AssetCostProfile(Document):
	def validate(self):
		self._validate_allocation()
		self._validate_schedule()

	def _validate_allocation(self):
		rows = self.get("cost_center_allocation") or []
		if not rows:
			frappe.throw(
				_(
					"An Asset Cost Profile needs at least one cost center allocation row. "
					"An asset nobody uses still has to depreciate somewhere."
				)
			)
		total = sum(float(row.get("percentage") or 0) for row in rows)
		if abs(total - 100.0) > ALLOCATION_TOLERANCE:
			frappe.throw(
				_("Cost center allocation totals {0}%, not 100%.").format(round(total, 4)),
				title=_("Allocation Must Total 100"),
			)
		seen = set()
		for row in rows:
			cost_center = row.get("cost_center")
			if cost_center in seen:
				frappe.throw(
					_("Cost center {0} appears twice. Combine the rows into one share.").format(
						cost_center
					)
				)
			seen.add(cost_center)
			if float(row.get("percentage") or 0) <= 0:
				frappe.throw(_("Allocation to {0} must be a positive percentage.").format(cost_center))

	def _validate_schedule(self):
		life = int(self.useful_life_months or 0)
		frequency = int(self.depreciation_frequency_months or 0)
		if life <= 0:
			frappe.throw(_("Useful Life (Months) must be positive."))
		if frequency <= 0:
			frappe.throw(_("Frequency (Months) must be positive."))
		if life % frequency:
			frappe.throw(
				_(
					"Frequency of {0} month(s) does not divide a useful life of {1} months. "
					"A schedule with a stub period at the end is one nobody can reconcile."
				).format(frequency, life)
			)
		if float(self.salvage_value or 0) < 0:
			frappe.throw(_("Salvage Value cannot be negative."))
		if float(self.salvage_value or 0) > float(self.gross_purchase_amount or 0):
			frappe.throw(
				_("Salvage Value {0} is more than the asset cost {1}; there would be nothing to depreciate.").format(
					self.salvage_value, self.gross_purchase_amount
				)
			)
