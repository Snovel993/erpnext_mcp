# SPDX-License-Identifier: MIT
"""Controller for Irrigation Zone — one valve's worth of ground.

THE DOCNAME IS SUFFIXED WITH THE PARCEL, NOT THE FIELD. `"YC3-Zone2 - MC"`. A
zone name already carries its field in the only place that matters — the
operator's own naming — and suffixing it with the field's key as well gives
`"YC3-Zone2 - YC3"`, which repeats what you already knew and drops what you
did not. So the parcel's short key is the suffix at every level of this
hierarchy, and the register enforces one zone name per parcel to make that safe.

TWO ARITHMETIC RULES, BOTH OF THEM CONTRADICTIONS RATHER THAN OPINIONS. Zones of
one field summing to more area than the field has; two zones of one field
claiming the same zone number. The second is not a database nicety — the zone
number is what somebody types into the controller at two in the morning, and two
answers to it means water goes somewhere nobody chose.

ACRES ARE COMPUTED, NEVER TYPED. `area_acres` is `area_sq_ft / 43560`, rewritten
on every save. A pair of fields a human can edit independently is a pair of
fields that will disagree, and the disagreement will be discovered by a report.
The same rule governs everything derived from the boundary polygon.

A ZONE OUTSIDE ITS BLOCK IS REPORTED, NOT REFUSED. The obvious rule is that a
zone must sit inside the field it waters, and it is wrong often enough to matter:
a shared water line crosses a boundary, a pump house sits on the headland, a
mainline runs down a road easement. Those are real and a refusal would make them
unrecordable. So containment is checked, the answer is reported, and the operator
decides — see `boundary_contained_in_field` on the tool result.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import geo
from erpnext_mcp.abbr import parcel_abbr, suffixed

#: Square feet in an acre. Named because a bare 43560 in a division is a number
#: a reader has to recognise before they can check it.
SQ_FT_PER_ACRE = 43560.0


class IrrigationZone(Document):
	def autoname(self):
		self.name = suffixed(str(self.zone_name or "").strip(), parcel_abbr(self.parcel or self._parcel()))

	def validate(self):
		self.zone_name = str(self.zone_name or "").strip()
		if not self.zone_name:
			frappe.throw(_("Zone Name is required."))
		if not self.field:
			frappe.throw(_("Field is required — a zone waters a block, or it waters nothing."))

		field = (
			frappe.db.get_value(
				"Field", self.field, ["name", "parcel", "owning_entity", "acreage"], as_dict=True
			)
			or {}
		)
		self.parcel = field.get("parcel") or self.parcel
		self.owning_entity = field.get("owning_entity") or self.owning_entity

		if float(self.area_sq_ft or 0) < 0:
			frappe.throw(_("Area cannot be negative."))
		if float(self.flow_rate_gpm or 0) < 0:
			frappe.throw(_("Flow Rate cannot be negative."))
		self.area_acres = round(float(self.area_sq_ft or 0) / SQ_FT_PER_ACRE, 3)

		self._check_zone_name()
		self._check_zone_number()
		self._check_field_area(field)
		self._check_boundary()

	def _parcel(self) -> str:
		return frappe.db.get_value("Field", self.field, "parcel") or "" if self.field else ""

	def _check_zone_name(self) -> None:
		"""One zone name per parcel, because the docname is suffixed with the parcel.

		Checked across the parcel rather than the field even though the zone
		belongs to a field: the docname is what has to be unique, and it does not
		carry the field.
		"""
		if not self.parcel:
			return
		duplicate = frappe.db.get_value(
			"Irrigation Zone",
			{"zone_name": self.zone_name, "parcel": self.parcel, "name": ("!=", self.name or "")},
			["name", "field"],
			as_dict=True,
		)
		if duplicate:
			frappe.throw(
				_(
					"Irrigation Zone {0} on {1} is already called {2}. Zone names are unique "
					"within a parcel because the docname is filed under the parcel — name this "
					"one for its field, the way 'YC3-Zone2' does."
				).format(duplicate.get("name"), duplicate.get("field"), self.zone_name),
				title=_("Duplicate Irrigation Zone"),
			)

	def _check_zone_number(self) -> None:
		if self.zone_number in (None, ""):
			return
		if int(self.zone_number) < 0:
			frappe.throw(_("Zone Number cannot be negative."))
		duplicate = frappe.db.get_value(
			"Irrigation Zone",
			{
				"zone_number": int(self.zone_number),
				"field": self.field,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Zone number {0} on {1} is already {2}. The zone number is what somebody "
					"types into the controller — two answers to it means water goes somewhere "
					"nobody chose."
				).format(int(self.zone_number), self.field, duplicate),
				title=_("Duplicate Zone Number"),
			)

	def _check_field_area(self, field: dict) -> None:
		"""Zones of one field cannot cover more ground than the field has.

		Compared in acres because that is the unit the field is recorded in, and
		reported in both because the zone was entered in square feet and the
		person fixing it needs the number they typed.
		"""
		limit = float(field.get("acreage") or 0)
		if limit <= 0:
			return
		siblings = frappe.db.get_all(
			"Irrigation Zone",
			filters={"field": self.field, "name": ("!=", self.name or "")},
			fields=["name", "area_acres"],
			limit=500,
		)
		others = sum(float(row.get("area_acres") or 0) for row in siblings)
		total = round(others + float(self.area_acres or 0), 3)
		if total > round(limit, 3):
			frappe.throw(
				_(
					"{0} is {1} acres, and its zones would total {2} ({3} sq ft here) — {4} "
					"more than the block. Either the block's acreage is understated or a "
					"zone's area is overstated; both cannot be right."
				).format(
					self.field,
					round(limit, 2),
					total,
					round(float(self.area_sq_ft or 0), 2),
					round(total - limit, 3),
				),
				title=_("Zone Area Exceeds Field"),
			)

	def _check_boundary(self) -> None:
		"""Validate the polygon and rewrite everything derived from it.

		Structural checks always run; the geometric ones need shapely and h3.
		The comparison against `area_acres` here is against the figure computed
		from `area_sq_ft`, which is what somebody measured off a design drawing —
		a polygon and a drawing disagreeing by a quarter means one of them is a
		different zone.
		"""
		if not str(self.boundary_geojson or "").strip():
			return
		geometry = geo.parse(self.boundary_geojson)
		if not geo.available():
			return
		derived = geo.derive(geometry)
		derived.pop("shape", None)
		for fieldname, value in derived.items():
			self.set(fieldname, value)

		_ratio, verdict = geo.area_disagreement(self.area_acres, self.area_computed_acres)
		if verdict == "refuse":
			frappe.throw(
				_(
					"The boundary encloses {0} acres and this zone's area is recorded as {1} "
					"({2} sq ft). One of the two is about a different zone. Fix whichever is "
					"wrong before saving."
				).format(
					self.area_computed_acres,
					round(float(self.area_acres or 0), 3),
					round(float(self.area_sq_ft or 0), 2),
				),
				title=_("Boundary Disagrees With Area"),
			)
