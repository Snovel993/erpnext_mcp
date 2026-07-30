# SPDX-License-Identifier: MIT
"""Controller for Field — one planted block, filed under the ground it is on.

THE DOCNAME IS `"<field name> - <parcel abbr>"`. Not the company's abbreviation:
a company has eight parcels and a parcel has eight blocks, and "Block 3 - HLD"
would be ambiguous eight ways over. The parcel's short key is what makes
`"Yellow Camp Block 3 - MC"` readable to somebody standing in it.

ACREAGE IS CHECKED AGAINST THE PARCEL, AND THAT IS THE ONE ARITHMETIC RULE HERE.
A parcel's blocks summing to more than the parcel is not a judgement about
somebody's records — it is two numbers that cannot both be true, and the one
place it always surfaces is a bad import. Everything softer than that is left
alone: blocks summing to *less* than the parcel is the normal case (roads,
ditches, headlands, the house), and a controller that complained about it would
complain about every real farm.

WHY THE COMPLIANCE FIELDS ARE ON THIS DOCTYPE. `last_spray_date` answers "can
the crew go in" before it answers anything an inspector asks;
`worker_hygiene_station_present` decides whether a block can legally be worked
at all. Removing either breaks the day's dispatch as surely as it breaks a WPS
report, which is the test for whether compliance is woven in or bolted on. A
separate "Field Compliance Log" would fail that test — nothing about picking
would stop if it disappeared.

THE BOUNDARY IS THE SAME KIND OF FIELD, AND THE STRONGEST EXAMPLE OF IT. A
polygon is what turns "sprayed Block 3" into something an auditor can check
against a GPS fix, and it is also what lets a crew's phone answer "am I in the
right block". Remove it and the WPS record loses its evidence AND the gate loses
its geofence. Everything derived from it — centroid, bounding box, H3 coverage,
computed area — is recomputed here on every save, because a derived figure a
person can edit independently is a figure that will disagree with the shape.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import geo
from erpnext_mcp.abbr import parcel_abbr, suffixed


class Field(Document):
	def autoname(self):
		self.name = suffixed(str(self.field_name or "").strip(), parcel_abbr(self.parcel))

	def validate(self):
		self.field_name = str(self.field_name or "").strip()
		if not self.field_name:
			frappe.throw(_("Field Name is required."))
		if not self.parcel:
			frappe.throw(_("Parcel is required — a block with no ground is not a field."))

		parcel = (
			frappe.db.get_value(
				"Parcel", self.parcel, ["name", "owning_entity", "acreage"], as_dict=True
			)
			or {}
		)
		self.owning_entity = parcel.get("owning_entity") or self.owning_entity

		duplicate = frappe.db.get_value(
			"Field",
			{"field_name": self.field_name, "parcel": self.parcel, "name": ("!=", self.name or "")},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Field {0} already records a block called {1} on {2}. One block per name "
					"per parcel — edit that one, or name this one so a crew can tell them apart."
				).format(duplicate, self.field_name, self.parcel),
				title=_("Duplicate Field"),
			)

		if float(self.acreage or 0) < 0:
			frappe.throw(_("Acreage cannot be negative."))
		if int(self.planting_density_per_acre or 0) < 0:
			frappe.throw(_("Planting Density cannot be negative."))

		self._check_parcel_acreage(parcel)
		self._check_boundary()
		self._check_ndvi()

	def _check_parcel_acreage(self, parcel: dict) -> None:
		"""Refuse blocks that between them are bigger than the ground they are on.

		Reported with both numbers and the shortfall, because the useful next
		question is "which of these two is wrong" and neither figure alone
		answers it. A parcel with no acreage recorded is not checked — an unknown
		is not a zero, and treating it as one would refuse every field on a
		parcel somebody has not measured yet.
		"""
		limit = float(parcel.get("acreage") or 0)
		if limit <= 0:
			return
		siblings = frappe.db.get_all(
			"Field",
			filters={"parcel": self.parcel, "name": ("!=", self.name or "")},
			fields=["name", "acreage"],
			limit=500,
		)
		others = sum(float(row.get("acreage") or 0) for row in siblings)
		total = round(others + float(self.acreage or 0), 2)
		if total > round(limit, 2):
			frappe.throw(
				_(
					"{0} is {1} acres, and its blocks would total {2} — {3} more than the "
					"parcel. Either the parcel's acreage is understated or one of the blocks "
					"is overstated; both cannot be right."
				).format(self.parcel, round(limit, 2), total, round(total - limit, 2)),
				title=_("Field Acreage Exceeds Parcel"),
			)

	def _check_boundary(self) -> None:
		"""Validate the polygon and rewrite everything derived from it.

		The structural checks — valid JSON, a Polygon or MultiPolygon, closed
		rings, coordinates on Earth — need no third-party library and therefore
		always run, so a bad boundary is refused on any site. The geometric ones
		(self-intersection, area, centroid, H3) need shapely and h3; where those
		are absent the shape is stored as given and the derived fields are left
		alone rather than being silently zeroed, because a zero centroid is a
		coordinate in the Gulf of Guinea and reads like an answer.
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

		_ratio, verdict = geo.area_disagreement(self.acreage, self.area_computed_acres)
		if verdict == "refuse":
			frappe.throw(
				_(
					"The boundary encloses {0} acres and this block is recorded as {1}. That is "
					"not a survey disagreement — one of the two is about a different piece of "
					"ground. Fix whichever is wrong before saving."
				).format(self.area_computed_acres, round(float(self.acreage or 0), 2)),
				title=_("Boundary Disagrees With Acreage"),
			)

	def _check_ndvi(self) -> None:
		"""NDVI is an index from -1 to 1, and a stored value outside that is noise."""
		for fieldname in ("last_ndvi_mean",):
			value = self.get(fieldname)
			if value in (None, ""):
				continue
			if not geo.NDVI_MIN <= float(value) <= geo.NDVI_MAX:
				frappe.throw(
					_("{0} is {1}. NDVI runs from {2} to {3}.").format(
						fieldname, value, geo.NDVI_MIN, geo.NDVI_MAX
					)
				)
		if self.last_ndvi_stddev not in (None, "") and float(self.last_ndvi_stddev) < 0:
			frappe.throw(_("A standard deviation cannot be negative."))
