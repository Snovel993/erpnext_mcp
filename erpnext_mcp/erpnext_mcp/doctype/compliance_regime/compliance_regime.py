# SPDX-License-Identifier: MIT
"""Controller for Compliance Regime — the picker's table, not the vocabulary.

WHAT THIS DOCTYPE IS FOR, AND WHAT IT IS NOT. It is not the definition of a
regime. `erpnext_mcp/training.py` holds that: the `REGIMES` tuple, the aliases a
person will genuinely type, the retention each one demands and the citation for
it, and — above all — `canon`, which is the only function on the site allowed to
decide that a piece of text means WPS. This table exists so that a Table
MultiSelect has something to link to, which is what lets somebody tag an alert by
PICKING a regime in the Desk instead of typing `OR-OSHA` from memory into a text
box. That near-miss is the failure the whole vocabulary was built to refuse, and
a picker refuses it before anybody types anything.

So the seeder in `training.seed_regimes` writes this table from the tuple on
every migrate, and the tuple wins every disagreement.

IT DOES NOT POLICE WHAT AN OPERATOR ADDS. A row for a scheme this app has never
heard of is allowed — that is how an operation records the one buyer scheme
nobody else runs — and it will simply not be pulled into any named audit packet,
because the packet builders match on `canon`. The alternative would be refusing
an operator's own true statement about their own operation on the grounds that
this app has not modelled it, which is not a trade worth making.

DELETING ONE IS REFUSED WHILE ANYTHING CARRIES IT. A regime deleted out from
under a Compliance Alert or a Training Type leaves a child row pointing at
nothing, and the record silently stops appearing in the packet it was evidence
for — the exact shape of quiet evidence loss this module exists to prevent.
`active` is the honest way to retire one: new pickers stop offering it and every
record that already carries it goes on carrying it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import training


class ComplianceRegime(Document):
	def validate(self):
		self.regime_name = " ".join(str(self.regime_name or "").split()).strip()
		if not self.regime_name:
			frappe.throw(_("Regime is required."))
		if self.retention_years in (None, ""):
			# The general defensible floor, and the safe direction to be wrong in.
			self.retention_years = training.RETENTION_YEARS.get(self.regime_name, 3)

	def on_trash(self):
		"""Refuse while any record still carries this tag. See the module docstring."""
		holders = self._holders()
		if not holders:
			return
		lines = "; ".join(f"{count} {doctype}" for doctype, count in holders)
		frappe.throw(
			_(
				"{0} is still carried by {1}. Deleting it would leave those records pointing at "
				"nothing, and each one would quietly stop appearing in the packet it is evidence "
				"for — which nobody finds out about until an auditor asks for evidence that is "
				"there and cannot be produced. Untick Active instead: new records stop offering "
				"it and every record that already carries it keeps it."
			).format(self.name, lines),
			title=_("Regime Is In Use"),
		)

	def _holders(self) -> list:
		"""(parent doctype, count) for everything tagged with this regime."""
		out = []
		for doctype, fieldname in (
			("Compliance Alert", "regime"),
			(training.TYPE_DOCTYPE, "regimes"),
		):
			try:
				count = frappe.db.count(
					training.REGIME_LINK_DOCTYPE,
					{"parenttype": doctype, "parentfield": fieldname, "regime": self.name},
				)
			except Exception:  # pragma: no cover - a site mid-migration
				continue
			if count:
				out.append((doctype, int(count)))
		return out
