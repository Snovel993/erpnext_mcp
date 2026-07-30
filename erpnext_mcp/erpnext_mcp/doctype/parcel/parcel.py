# SPDX-License-Identifier: MIT
"""Controller for Parcel — one piece of ground, identified the way a county is.

THE DOCNAME CARRIES THE ENTITY, THE SAME WAY AN ACCOUNT'S DOES. `"Red Camp -
HLD"`, not `"Red Camp"`. Family land gets reorganised: a parcel moves between a
trust and an LLC, and two entities in the same family end up with a "Home Place"
apiece. A docname keyed on the name alone would make the second one impossible to
file and the first one impossible to trust.

WHAT `validate` REFUSES, AND WHY IT IS SO LITTLE. A duplicate name inside one
entity, because the docname is built from it and a silent collision is a lost
record. An assessor parcel id claimed by two parcels of the same entity, because
that number is the county's primary key and two of them means one is a typo.
Negative acreage and a negative appraised value, which are not opinions.

Everything else — an appraisal older than the tax year, acreage that disagrees
with the deed, a parcel with no county — is a judgement about someone's records
rather than an error in this one, and a controller that refuses those is a
controller that stops the day somebody is trying to get the truth written down.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class Parcel(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.owning_entity, "abbr") or ""
		parcel_name = str(self.parcel_name or "").strip()
		self.name = f"{parcel_name} - {abbr}" if abbr else parcel_name

	def validate(self):
		self.parcel_name = str(self.parcel_name or "").strip()
		if not self.parcel_name:
			frappe.throw(_("Parcel Name is required."))

		duplicate = frappe.db.get_value(
			"Parcel",
			{
				"parcel_name": self.parcel_name,
				"owning_entity": self.owning_entity,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Parcel {0} already records a parcel called {1} for {2}. One parcel "
					"per name per entity — edit that one, or give this one a name that "
					"distinguishes it."
				).format(duplicate, self.parcel_name, self.owning_entity),
				title=_("Duplicate Parcel"),
			)

		self.parcel_id = str(self.parcel_id or "").strip()
		if self.parcel_id:
			collision = frappe.db.get_value(
				"Parcel",
				{
					"parcel_id": self.parcel_id,
					"owning_entity": self.owning_entity,
					"name": ("!=", self.name or ""),
				},
				"name",
			)
			if collision:
				frappe.throw(
					_(
						"Parcel {0} already carries assessor parcel id {1} for {2}. That "
						"number is the county's key: two parcels sharing one means a typo "
						"in one of them."
					).format(collision, self.parcel_id, self.owning_entity),
					title=_("Duplicate Assessor Parcel ID"),
				)

		if float(self.acreage or 0) < 0:
			frappe.throw(_("Acreage cannot be negative."))
		if float(self.appraised_value or 0) < 0:
			frappe.throw(_("Appraised Value cannot be negative."))
