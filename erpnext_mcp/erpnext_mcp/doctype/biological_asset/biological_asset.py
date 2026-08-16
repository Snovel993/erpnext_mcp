# SPDX-License-Identifier: MIT
"""Controller for Biological Asset — a growing crop, carried at a value.

WHY THIS IS NOT ERPNEXT'S Asset DOCTYPE. An ERPNext Asset is a depreciating
purchase: it has a cost, a useful life and a schedule that writes it down. A
bearer plant is genuinely close to that — IAS 16 moved bearer plants into
property, plant and equipment in 2014, and a mature block IS depreciated — but a
CONSUMABLE biological asset is the opposite of a depreciating purchase: an annual
planting GROWS in value until it is harvested and then ceases to exist. Modelling
that as an Asset with a negative depreciation rate would be a lie that reports
would repeat.

THE TWO TYPES DIFFER IN WHAT HAPPENS AT THE END, WHICH IS WHY THE FIELD EXISTS:

    Bearer      an orchard block. Bears fruit for thirty years; the FRUIT is the
                harvest and the block stays. Depreciated once mature.
    Consumable  a row of annual vegetables, a nursery lot. IS the harvest. Not
                depreciated at all — it is remeasured until it is picked.

WHAT `validate` REFUSES. A valuation history that disagrees with the current
value, which it fixes rather than refuses: `current_value` and
`last_valuation_date` are DERIVED from the newest row, never typed. A value
somebody typed that no valuation supports is exactly the assertion this doctype
exists to make impossible.

It also refuses a negative value — a crop that owes money is a liability
somebody has misfiled — and a valuation dated in the future, because a
remeasurement is a statement about a date that has happened.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class BiologicalAsset(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		title = str(self.asset_name or "").strip()
		self.name = f"{title} - {abbr}" if abbr else title

	def validate(self):
		self.asset_name = str(self.asset_name or "").strip()
		if not self.asset_name:
			frappe.throw(_("Asset Name is required."))
		if not self.status:
			self.status = "Growing"
		if not self.asset_type:
			self.asset_type = "Bearer"
		if not self.valuation_method:
			self.valuation_method = "Cost"

		duplicate = frappe.db.get_value(
			"Biological Asset",
			{"asset_name": self.asset_name, "company": self.company, "name": ("!=", self.name or "")},
			"name",
		)
		if duplicate:
			frappe.throw(
				_("Biological Asset {0} already carries that name for this company.").format(duplicate),
				title=_("Duplicate Asset"),
			)

		self._check_the_valuations()
		self._derive_current()

	def _check_the_valuations(self) -> None:
		today = frappe.utils.today()
		for row in self.get("valuations") or []:
			if float(row.get("value") or 0) < 0:
				frappe.throw(
					_(
						"A valuation of {0} on {1} is negative. A crop that owes money is a "
						"liability somebody has misfiled, not an asset with a minus sign."
					).format(row.get("value"), row.get("valuation_date"))
				)
			if str(row.get("valuation_date") or "") > today:
				frappe.throw(
					_(
						"A valuation is dated {0}, which is in the future. A remeasurement is a "
						"statement about a date that has happened."
					).format(row.get("valuation_date"))
				)

	def _derive_current(self) -> None:
		"""`current_value` is the NEWEST valuation. It is never typed.

		The whole point of the doctype: a figure on the face of the record that no
		valuation supports is precisely the unevidenced assertion this exists to
		prevent. So both columns are read-only and computed here, and a site with no
		valuations yet shows nothing rather than zero — because "not yet valued" and
		"valued at nothing" are different statements about a growing crop.
		"""
		rows = sorted(
			self.get("valuations") or [],
			key=lambda row: (str(row.get("valuation_date") or ""), int(row.get("idx") or 0)),
		)
		if not rows:
			self.current_value = None
			self.last_valuation_date = None
			return
		newest = rows[-1]
		self.current_value = float(newest.get("value") or 0)
		self.last_valuation_date = newest.get("valuation_date")
		# The asset-level method tracks the basis of the LATEST valuation, so the
		# register and the newest row cannot disagree about how the number on the
		# face of the record was arrived at.
		if newest.get("valuation_method"):
			self.valuation_method = newest.get("valuation_method")
