# SPDX-License-Identifier: MIT
"""Controller for Lease — the same record whichever side of it we are on.

DIRECTION IS STORED, NOT DERIVED. Given `lessor` and `lessee` as legal names, a
controller could try to work out which one is us by matching against the Company
name — and would be wrong for every entity whose legal name is not its ERPNext
docname, which is most of them ("Highland Ltd Liability Co." against a Company
called "Highland LLC"). So the caller says which direction it is and this checks
the claim is at least self-consistent: an Outbound lease where neither party
resembles the owning entity is not refused, but the tool that created it says so.

WHAT `validate` REFUSES. A duplicate lease name inside one entity, for the same
reason Parcel refuses one — the docname is built from it. A term that ends before
it starts. A termination date before the effective date. A lease whose lessor and
lessee are the same string, which is not a lease. And Terminated status with no
termination date, because "we ended it" without "when" is not a record anybody
can use.

WHAT IT DOES NOT DO. It does not expire a lease because a date has passed. A
farm lease that runs on month-to-month past its stated term is the ordinary case,
and a status that changed itself on the calendar would erase the difference
between "still running" and "nobody has looked at this since 2019". The list tool
reports the mismatch and leaves the decision to a person.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class Lease(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.owning_entity, "abbr") or ""
		lease_name = str(self.lease_name or "").strip()
		self.name = f"{lease_name} - {abbr}" if abbr else lease_name

	def validate(self):
		self.lease_name = str(self.lease_name or "").strip()
		if not self.lease_name:
			frappe.throw(_("Lease Name is required."))

		duplicate = frappe.db.get_value(
			"Lease",
			{
				"lease_name": self.lease_name,
				"owning_entity": self.owning_entity,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Lease {0} already records a lease called {1} for {2}. One lease per "
					"name per entity — edit that one, or name this one for its term."
				).format(duplicate, self.lease_name, self.owning_entity),
				title=_("Duplicate Lease"),
			)

		lessor = str(self.lessor or "").strip()
		lessee = str(self.lessee or "").strip()
		if lessor and lessee and lessor.lower() == lessee.lower():
			frappe.throw(
				_("Lessor and Lessee are both {0}. A party cannot lease from itself.").format(lessor)
			)

		if self.expiration_date and self.effective_date and self.expiration_date < self.effective_date:
			frappe.throw(_("Expiration Date cannot be before Effective Date."))
		if self.termination_date and self.effective_date and self.termination_date < self.effective_date:
			frappe.throw(_("Termination Date cannot be before Effective Date."))
		if self.status == "Terminated" and not self.termination_date:
			frappe.throw(
				_(
					"A Terminated lease needs a Termination Date. 'We ended it' without "
					"'when' is not a record anybody can rely on later."
				)
			)
		if float(self.rent_amount or 0) < 0:
			frappe.throw(_("Rent Amount cannot be negative. Rent flowing the other way is a lease in the other direction."))
