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

THE ABBREVIATION IS A KEY, AND IT IS FILLED IN RATHER THAN DEMANDED. Fields,
irrigation zones and housing units are all named `"<their name> - <parcel
abbr>"`, so a parcel with no abbreviation is a parcel nothing can hang off. An
operator who supplies one gets theirs and a collision is refused; one who does
not gets initials, and a *derived* collision is disambiguated instead of refused
— see `erpnext_mcp.abbr`. Parcels registered before v0.12.0 have no stored
abbreviation and acquire one the next time they are saved; nothing reads the
field without falling back to the derivation, so an unsaved one still works.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp.abbr import derive_abbr, normalise_abbr, unique_abbr


class Parcel(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.owning_entity, "abbr") or ""
		parcel_name = str(self.parcel_name or "").strip()
		self.name = f"{parcel_name} - {abbr}" if abbr else parcel_name

	def validate(self):
		self.parcel_name = str(self.parcel_name or "").strip()
		if not self.parcel_name:
			frappe.throw(_("Parcel Name is required."))

		self._set_abbr()

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

		self._check_branch()

	def _check_branch(self) -> None:
		"""The operating unit this ground belongs to, checked where it can be.

		v0.54.0. `branch` is a Data field rather than a Link for the reason set
		out on the field itself and on `Housing Assignment.employee`: Branch
		belongs to Frappe HR, and a Link would make this doctype fail to migrate
		on every site that has not installed it.

		So the refusal is real ONLY WHERE THERE IS A TABLE TO CHECK AGAINST. A
		branch naming nothing on a site that HAS Branch records is a typo, and it
		is the expensive kind: `list_available_housing(branch=…)` resolves a
		branch to its parcels and would return an empty camp for a name that is
		one letter out. On a site with no Branch doctype the value is stored as
		typed, because refusing there would refuse a perfectly good answer on a
		site that simply never installed an HR app.
		"""
		self.branch = str(self.branch or "").strip()
		if not self.branch:
			return
		try:
			present = bool(frappe.db.exists("DocType", "Branch"))
		except Exception:  # pragma: no cover - a site mid-migration
			return
		if not present:
			return
		if not frappe.db.exists("Branch", self.branch):
			known = frappe.db.get_all("Branch", pluck="name", limit=12, order_by="name asc") or []
			frappe.throw(
				_(
					"Branch {0} is not one on this site. A parcel tagged with a branch that "
					"does not exist is a camp the hiring wizard will show as having no "
					"cabins at all. {1}"
				).format(
					self.branch,
					_("Known Branch: {0}.").format(", ".join(str(name) for name in known))
					if known
					else _("This site has no Branch records yet — create one first."),
				),
				title=_("Unknown Branch"),
			)

	def _set_abbr(self) -> None:
		"""Fill in the short key, and refuse a collision only when it was chosen.

		The asymmetry is the whole design. A supplied abbreviation that another
		parcel of the same entity already holds is refused, because the operator
		typed it and a silent change would file their records somewhere they did
		not look. A derived one is disambiguated, because nobody chose it and
		refusing would stop a perfectly good parcel over a key the tool invented.
		"""
		supplied = bool(str(self.abbr or "").strip())
		abbr = normalise_abbr(self.abbr) if supplied else derive_abbr(self.parcel_name)
		if not abbr:
			abbr = derive_abbr(self.parcel_name) or "P"

		taken = frappe.db.get_all(
			"Parcel",
			filters={"owning_entity": self.owning_entity, "name": ("!=", self.name or "")},
			pluck="abbr",
			limit=500,
		)
		taken = {str(entry).upper() for entry in (taken or []) if entry}
		if supplied:
			if abbr in taken:
				frappe.throw(
					_(
						"Abbreviation {0} is already used by another parcel of {1}. Every "
						"field, irrigation zone and housing unit is filed under this key, so "
						"two parcels sharing one would file them together."
					).format(abbr, self.owning_entity),
					title=_("Duplicate Parcel Abbreviation"),
				)
			self.abbr = abbr
			return
		self.abbr = unique_abbr(abbr, taken)
