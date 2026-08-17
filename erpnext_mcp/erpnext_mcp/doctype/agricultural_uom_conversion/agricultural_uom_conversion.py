# SPDX-License-Identifier: MIT
"""Controller for Agricultural UOM Conversion — how many of one unit are in another.

THE DOCNAME IS BUILT, NOT TYPED. `"Bin to Pound - Sweet Cherry"`, or
`"Gallon to Fluid Ounce"` where the factor holds for everything. It is built
rather than hashed because this is a master an operator reads in a list view,
and a register of forty hashes is a register nobody audits. It carries the crop
because the crop is part of the key: bin-to-pound for cherries and bin-to-pound
for apples are two different true statements, and a docname that hid that would
make the register look like it contradicted itself.

ONE ACTIVE ROW PER (from, to, crop). This is the rule the whole doctype exists
to enforce. Two live answers to "how much does a bin weigh" is precisely the
failure that ERPNext's own crop-blind `UOM Conversion Factor` produces when a
mixed orchard enters both — and reproducing it here, one level down, would have
been a waste of a table. INACTIVE ROWS ARE NOT CHECKED, deliberately: a
superseded factor is kept so last season's settlements remain explicable, and it
must be allowed to sit beside the row that replaced it.

`Exact` WITH A CROP IS REFUSED. An exact conversion is a definition — 128 fluid
ounces to a gallon, 43,560 square feet to an acre — and a definition does not
vary by fruit. A row claiming both is claiming that arithmetic depends on what
is in the box, and whichever half is wrong, storing it would let a caller trust
the wrong one.

AN `Operation Average` MUST SAY WHERE IT CAME FROM. It is the only basis that
claims to be evidence rather than a rule of thumb, and evidence with no account
of how it was gathered is a guess wearing better clothes. It is also the basis
that beats the trade figure in every lookup, so the bar for asserting one is the
higher bar on purpose.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: The basis that is a definition rather than a measurement, and therefore
#: cannot be crop-specific.
EXACT = "Exact"

#: The basis that claims to be this farm's own evidence, and therefore has to
#: cite it.
MEASURED = "Operation Average"


class AgriculturalUOMConversion(Document):
	def autoname(self):
		base = f"{str(self.from_uom or '').strip()} to {str(self.to_uom or '').strip()}"
		crop = str(self.crop or "").strip()
		self.name = f"{base} - {crop}" if crop else base

	def validate(self):
		self._check_units()
		self._check_factor()
		self._check_basis()
		self._check_no_second_active_row()

	def _check_units(self) -> None:
		self.from_uom = str(self.from_uom or "").strip()
		self.to_uom = str(self.to_uom or "").strip()
		if not self.from_uom or not self.to_uom:
			frappe.throw(_("A conversion needs both a From Unit and a To Unit."))
		if self.from_uom == self.to_uom:
			frappe.throw(
				_(
					"{0} to {0} is not a conversion. Stored among real ones it is a rounding "
					"error waiting to be multiplied by something."
				).format(self.from_uom),
				title=_("Unit Converted To Itself"),
			)

	def _check_factor(self) -> None:
		factor = float(self.factor or 0)
		if factor <= 0:
			frappe.throw(
				_(
					"The factor must be greater than zero — it is how many {0} are in ONE {1}. "
					"A factor of zero makes every quantity vanish and a negative one has no "
					"meaning."
				).format(self.to_uom or "of the second unit", self.from_uom or "of the first"),
				title=_("Factor Out of Range"),
			)

	def _check_basis(self) -> None:
		basis = str(self.basis or "").strip()
		crop = str(self.crop or "").strip()

		if basis == EXACT and crop:
			frappe.throw(
				_(
					"{0} to {1} is marked Exact and also specific to {2}. An exact conversion is "
					"a definition, and a definition does not vary by fruit — if this factor "
					"really depends on the crop it is Nominal or an Operation Average, and if it "
					"does not, it should carry no crop."
				).format(self.from_uom, self.to_uom, crop),
				title=_("Exact Conversion With a Crop"),
			)

		if basis == MEASURED and not str(self.source or "").strip():
			frappe.throw(
				_(
					"An Operation Average needs a source — 'weighed 340 bins Aug 2025'. It is "
					"the one basis that claims to be this farm's own evidence rather than the "
					"trade's rule of thumb, and it is the one that wins every lookup, so it has "
					"to say how it was measured."
				),
				title=_("Measured Factor With No Source"),
			)

	def _check_no_second_active_row(self) -> None:
		"""One live answer per (from, to, crop).

		Inactive rows are excluded from BOTH sides of the comparison: a
		superseded factor is kept so last season's numbers stay explicable, and
		it has to be allowed to sit beside its replacement.
		"""
		if not self.is_active:
			return
		clash = frappe.db.get_value(
			"Agricultural UOM Conversion",
			{
				"from_uom": self.from_uom,
				"to_uom": self.to_uom,
				"crop": self.crop or "",
				"is_active": 1,
				"name": ("!=", self.name or ""),
			},
			["name", "factor"],
			as_dict=True,
		)
		if not clash:
			return
		frappe.throw(
			_(
				"{0} already says one {1} is {2} {3}{4}. Two live answers to that is the whole "
				"failure this register exists to prevent. Switch the old row off — it is kept "
				"so last season's settlements stay explicable — and this one becomes the "
				"current factor."
			).format(
				clash.get("name"),
				self.from_uom,
				clash.get("factor"),
				self.to_uom,
				f" for {self.crop}" if self.crop else "",
			),
			title=_("Conversion Already Recorded"),
		)
