# SPDX-License-Identifier: MIT
"""Controller for Related Party — the governance register, and its one hard rule.

FOUR DIGITS, NEVER NINE. `tax_id_last4` is refused unless it is exactly four
digits. Not truncated, not masked, not accepted-with-a-warning: refused. A field
that quietly stores whatever it is handed is a field that will one day hold a
full SSN, and the difference between a site holding four digits and a site
holding nine is the difference between an inconvenience and a notifiable breach.
The full number belongs on the signed W-9, on paper, in a drawer.

The refusal is deliberately loud about *what was sent*: a nine-digit value gets
told it looks like a whole SSN or EIN and is shown which four digits to send
instead. A validator that says "invalid format" to somebody who has just pasted a
real taxpayer identification number has told them nothing about why it matters.

WHY THIS IS NOT THE JOURNAL ENTRY PARTY FIELD. ERPNext already answers "who was
this transaction with" through Supplier, Customer and Employee links, and those
work. This answers a different question — "who is related to us, how, and since
when" — which no transactional field can, because a relationship is not an event.
The two meet at `supplier`, which is how a payment in the ledger becomes a
related-party disclosure on a return.

A PERSON IS NOT ONE ROW, AND THE DOCNAME SAYS SO. In an LLC the ordinary case is
somebody who is both Manager and Member, under two different instruments, from
two different dates. One row with one Select cannot hold that, and picking a
"primary" role would mean the register quietly disagrees with the operating
agreement. So the docname carries the relationship —
`"Tim Polehn - Manager - OML"` beside `"Tim Polehn - Member - OML"` — and
uniqueness is on (name, relationship, company). Two entries with the same name
and role is a duplicate; two with different roles is the truth, and the register
exists to hold the truth.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class RelatedParty(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		parts = [str(self.party_name or "").strip(), str(self.relationship_to_company or "").strip()]
		if abbr:
			parts.append(abbr)
		self.name = " - ".join(part for part in parts if part)

	def validate(self):
		self.party_name = str(self.party_name or "").strip()
		if not self.party_name:
			frappe.throw(_("Party Name is required."))

		duplicate = frappe.db.get_value(
			"Related Party",
			{
				"party_name": self.party_name,
				"relationship_to_company": self.relationship_to_company,
				"company": self.company,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Related Party {0} already records {1} as {2} of {3}. A second role for "
					"the same person is a second entry with a different relationship; the "
					"same role twice is a duplicate."
				).format(duplicate, self.party_name, self.relationship_to_company, self.company),
				title=_("Duplicate Related Party"),
			)

		self._validate_tax_id()

		if self.end_date and self.effective_date and self.end_date < self.effective_date:
			frappe.throw(_("End Date cannot be before Effective Date."))

	def _validate_tax_id(self):
		value = str(self.tax_id_last4 or "").strip()
		self.tax_id_last4 = value
		if not value:
			if self.tax_id_type in ("SSN", "EIN"):
				frappe.throw(
					_(
						"Tax ID Type is {0} but no last four digits were given. Either record "
						"the last four, or set Tax ID Type to None."
					).format(self.tax_id_type)
				)
			return

		if self.tax_id_type in (None, "", "None"):
			frappe.throw(
				_("Tax ID Last 4 was given but Tax ID Type is None. Say which kind of number it is.")
			)
		if not value.isdigit():
			frappe.throw(_("Tax ID Last 4 must be four digits. Got {0!r}.").format(value))
		if len(value) == 9:
			frappe.throw(
				_(
					"That is nine digits — a whole {0}, not the last four of one. This field "
					"stores four digits and only four: send {1} instead. The full number "
					"belongs on the signed W-9, not on this site."
				).format(self.tax_id_type or "taxpayer id", value[-4:]),
				title=_("Do Not Store a Full Tax ID"),
			)
		if len(value) != 4:
			frappe.throw(
				_("Tax ID Last 4 must be exactly four digits. Got {0} ({1} digits).").format(
					value, len(value)
				)
			)
