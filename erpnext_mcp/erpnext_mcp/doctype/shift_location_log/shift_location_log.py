# SPDX-License-Identifier: MIT
"""Controller for Shift Location Log — one GPS fix, and what can be derived from it.

WHY THIS IS NOT A CHILD TABLE OF THE SHIFT. It was the obvious shape and it is
the wrong one for two reasons that both bite in production. A nine-hour shift
posting a fix every two minutes is two hundred and seventy rows, and a child
table is loaded WHOLE every time anybody opens the shift form — so the record
that makes the shift useful is also the record that makes the shift unopenable.
And a breadcrumb has to be writable by a phone that is not editing the shift: an
append to a standalone doctype is one INSERT, while an append to a child table is
a save of the parent, which re-runs every validation on the crew, the events and
the weather timeline and will happily fail because somebody left the shift's
cancellation reason blank an hour ago.

WHAT IT DERIVES rather than takes: the company, off the shift, so a User
Permission on Company reaches a track the way it reaches everything else this app
ships; the employee's name, snapshotted at write time for the same reason every
other record here snapshots one — a lookup at report time answers with today's
name, and a track read in a wage dispute is entitled to who the person was then;
and the resolution-10 H3 cell, so a track can be joined against a Field's or a
Parcel's stored coverage without loading a single polygon.

WHAT IT REFUSES: a coordinate that is not on Earth. That is the whole list, and
the refusal is worth having because [45.6, -121.2] and [-121.2, 45.6] are the
same two numbers and only one of them is in Oregon — a latitude past 90 is the
transposition announcing itself, and it is the only version of this mistake a
computer can catch.

WHAT IT MERELY RECORDS: a terrible fix. `accuracy_meters` is kept and never
gated on. A phone under a canopy in a canyon reports three hundred metres and is
still the only record that the crew was there at all, so a threshold that dropped
it would delete the evidence from precisely the ground that is hardest to work.
The number is stored so a reader can weigh the breadcrumb — a 5-metre fix and a
300-metre fix are both true and they are not the same claim.

NOTHING HERE EDITS A ROW. `log_shift_location` only ever appends. A breadcrumb
somebody can correct is not a record of where the phone was, it is a record of
where somebody would like it to have been, and those two documents look identical
after the fact.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import geo, shifts

#: The resolution the stored cell is taken at. Ten, because it is the finest of
#: the five a boundary is indexed at (`geo.H3_RESOLUTIONS`) and every coarser one
#: is its parent — so one stored cell answers at every resolution a Field or a
#: Parcel was indexed with, and a column per resolution would be four columns
#: holding a value derivable from the fifth.
H3_RESOLUTION = 10


class ShiftLocationLog(Document):
	def validate(self):
		self._check_the_coordinates()
		self._fill_from_the_shift()
		self._fill_from_the_employee()
		self._derive_the_cell()
		if not str(self.timestamp or "").strip():
			self.timestamp = frappe.utils.now()
		if not str(self.source or "").strip():
			self.source = "iOS"

	# ── the parts ───────────────────────────────────────────────────────────
	def _check_the_coordinates(self) -> None:
		"""On Earth, or a sentence saying which of the two numbers gives it away."""
		try:
			latitude = float(self.latitude)
			longitude = float(self.longitude)
		except (TypeError, ValueError):
			frappe.throw(
				_(
					"A location log needs a latitude and a longitude, both in decimal degrees. "
					"A breadcrumb with no position is a timestamp."
				),
				title=_("No Position"),
			)
			return
		if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
			frappe.throw(
				_(
					"[{0}, {1}] is not a point on Earth. Latitude runs -90 to 90 and longitude "
					"-180 to 180 — a latitude past 90 is almost always the pair the wrong way "
					"round, which is a mistake nothing downstream can catch because both numbers "
					"are real coordinates somewhere."
				).format(longitude, latitude),
				title=_("Not a Point on Earth"),
			)

	def _fill_from_the_shift(self) -> None:
		"""The company, off the shift. See the module docstring on scoping."""
		if not self.shift:
			return
		row = frappe.db.get_value(shifts.DOCTYPE, self.shift, ["company"], as_dict=True) or {}
		if not self.company:
			self.company = row.get("company")

	def _fill_from_the_employee(self) -> None:
		if not self.employee or str(self.employee_name or "").strip():
			return
		self.employee_name = frappe.db.get_value("Employee", self.employee, "employee_name") or self.employee

	def _derive_the_cell(self) -> None:
		"""The resolution-10 cell, on a site that can compute one.

		NEVER RAISES. h3 is a declared dependency and a normal install has it, but
		a bench without it must still be able to record where the crew was — the
		coordinates are the evidence and the cell is an index over them. A
		breadcrumb refused because a Python package is missing is a gap in a
		compliance record caused by a packaging problem, which is the worst trade
		this app could make.
		"""
		try:
			if geo.available():
				self.h3_cell = geo.cell_for_point(self.latitude, self.longitude, H3_RESOLUTION)
		except Exception:  # pragma: no cover - an h3 build that refuses a valid point
			pass
