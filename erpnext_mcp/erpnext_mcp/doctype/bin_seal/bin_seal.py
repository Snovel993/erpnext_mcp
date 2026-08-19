# SPDX-License-Identifier: MIT
"""Controller for Bin Seal — one closed bin, and the crew whose fruit is in it.

WHAT IT DERIVES rather than takes. The company, off the shift or off the checker,
so a User Permission on Company reaches a seal the way it reaches everything else
this app ships. The checker's name, snapshotted at write time, for the reason
every record here snapshots one: a lookup at report time answers with today's
name and a settlement dispute two years later is entitled to who the checker was
THEN. Each contributor's name, the same way. And the resolution-10 H3 cell, so a
bin can be placed on the ground without loading a single polygon — which is what
puts a bin in a block on the days nobody selected one.

WHAT IT REFUSES, AND IT IS A SHORT LIST BECAUSE A BIN IS A MEASUREMENT.

  * A coordinate that is not on Earth. [45.6, -121.2] and [-121.2, 45.6] are the
    same two numbers and only one of them is in Oregon; a latitude past 90 is the
    transposition announcing itself, and it is the only version of this mistake a
    computer can catch.
  * A negative bucket count. Not fussiness: `bucket_count` is a piece-rate figure
    and a negative one propagates into a settlement.
  * The same Employee twice in `contributors`. Two rows for one person is that
    person's share of the bin counted twice, and it is invisible on the form
    where the two rows sit next to each other looking deliberate — the identical
    argument Farm Shift makes about a duplicate crew member.

WHAT IT MERELY RECORDS, AND THE FIRST ONE IS THE INTERESTING ONE.

A bin whose contributors' buckets do not add up to its `bucket_count`. They are
two different measurements — one is what the checker's tally read when the bin
was closed, the other is what the badge scans attributed — and they are allowed
to disagree. A bucket tipped by somebody whose badge did not scan is in the bin
and not in the rows; a phone that lost a scan under a canopy produces the same
gap. Balancing them would delete the only evidence that either happened, so the
difference is COMPUTED AND REPORTED by `get_bin_seal` and `trace_bin` instead.

A bin with no contributors at all. That is a bin nobody can be paid for and a bin
whose trace stops at the block — worth knowing about, and not a reason to refuse
the record of a bin that genuinely went to the packing house.

A bin with no `field` and no `block`. A checker sealing at the end of a row on a
phone with a flat battery has a tag and a count, and those are worth keeping.

A bin tag that has been used before. Bin tags are reused between seasons and
between growers; a uniqueness constraint would refuse the second TRUE record
rather than the first false one. `trace_bin` answers with every seal carrying the
tag and says how many it found, which is the only honest answer to a reused tag.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import geo, shifts

#: The resolution the stored cell is taken at. Ten, for the reason
#: `Shift Location Log` gives: it is the finest of the five a boundary is indexed
#: at, and every coarser one is its parent, so one column answers at all five.
H3_RESOLUTION = 10


class BinSeal(Document):
	def validate(self):
		self._check_the_count()
		self._check_the_coordinates()
		self._fill_from_the_shift()
		self._fill_from_the_sealer()
		self._check_the_contributors()
		self._derive_the_cell()
		if not str(self.sealed_at or "").strip():
			self.sealed_at = frappe.utils.now()
		if not str(self.source or "").strip():
			self.source = "iOS"

	# ── the parts ───────────────────────────────────────────────────────────
	def _check_the_count(self) -> None:
		if frappe.utils.cint(self.bucket_count) < 0:
			frappe.throw(
				_(
					"A bin cannot hold {0} buckets. This figure is a piece-rate number before it "
					"is a traceability one, and a negative one propagates into a settlement."
				).format(self.bucket_count),
				title=_("Negative Bucket Count"),
			)

	def _check_the_coordinates(self) -> None:
		"""On Earth, or nothing at all. A seal with no coordinates is allowed."""
		if self.gps_lat in (None, "") and self.gps_lon in (None, ""):
			return
		try:
			latitude = float(self.gps_lat)
			longitude = float(self.gps_lon)
		except (TypeError, ValueError):
			frappe.throw(
				_(
					"gps_lat and gps_lon must both be decimal degrees, or both be empty. Half a "
					"coordinate places nothing."
				),
				title=_("Half a Coordinate"),
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
		if self.company or not self.shift:
			return
		row = frappe.db.get_value(shifts.DOCTYPE, self.shift, ["company"], as_dict=True) or {}
		self.company = row.get("company")

	def _fill_from_the_sealer(self) -> None:
		if not self.sealed_by:
			return
		row = frappe.db.get_value("Employee", self.sealed_by, ["employee_name", "company"], as_dict=True) or {}
		if not str(self.sealed_by_name or "").strip():
			self.sealed_by_name = row.get("employee_name") or self.sealed_by
		if not self.company:
			self.company = row.get("company")

	def _check_the_contributors(self) -> None:
		seen = {}
		for row in self.get("contributors") or []:
			if not row.employee:
				continue
			if row.employee in seen:
				frappe.throw(
					_(
						"{0} is on this bin's contributors twice. Two rows for one person is that "
						"person's share of the bin counted twice, and it is invisible on the form "
						"where the two rows sit next to each other looking deliberate. Somebody "
						"who tipped into the bin more than once is ONE row with the buckets added "
						"up and the scan window widened."
					).format(row.employee_name or row.employee),
					title=_("On the Bin Twice"),
				)
			seen[row.employee] = True
			if not str(row.employee_name or "").strip():
				row.employee_name = (
					frappe.db.get_value("Employee", row.employee, "employee_name") or row.employee
				)
			if frappe.utils.cint(row.buckets_contributed) < 0:
				frappe.throw(
					_("{0} cannot have contributed a negative number of buckets.").format(
						row.employee_name or row.employee
					),
					title=_("Negative Contribution"),
				)

	def _derive_the_cell(self) -> None:
		"""The resolution-10 cell, on a site that can compute one.

		NEVER RAISES, exactly as `Shift Location Log` does not: h3 is a declared
		dependency and a normal install has it, but a bench without it must still
		be able to record that a bin was sealed. The coordinates are the evidence
		and the cell is an index over them, and a bin refused because a Python
		package is missing is a hole in a traceability chain caused by a packaging
		problem.
		"""
		if self.gps_lat in (None, "") or self.gps_lon in (None, ""):
			return
		try:
			if geo.available():
				self.h3_hex = geo.cell_for_point(float(self.gps_lat), float(self.gps_lon), H3_RESOLUTION)
		except Exception:  # pragma: no cover - an h3 build that refuses a valid point
			pass
