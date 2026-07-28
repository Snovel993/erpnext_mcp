# SPDX-License-Identifier: MIT
"""Controller for Note Payable — what a company still owes, and to whom.

WHY THIS DOCTYPE EXISTS BESIDE ERPNEXT'S OWN LOAN MODULE. ERPNext's Loan is a
lending product: it models the company as the *lender*, with an application, a
disbursement, a repayment schedule and its own accounting. A holding company with
four notes outstanding is on the other side of all of that, and installing the
lending module to record them would add a dozen doctypes to a site that will
never originate a loan.

THE BALANCE HERE IS A CONVENIENCE, NOT THE LEDGER. `principal_outstanding` is
maintained by `record_loan_payment` so a person can answer "what is left on the
sorter" without running a report. The authoritative number is the balance of the
account named in `linked_gl_account`, and it will disagree with this field
whenever a payment has been recorded as a draft that nobody has posted — which is
the normal state of affairs in this app, since nothing here submits. The tools
say so in their responses; this controller does not pretend otherwise by
recomputing anything.

WHAT `validate` REFUSES. Only the things that make a note unreadable rather than
merely wrong: a duplicate name for the same borrower (the docname is built from
it), a maturity before origination, an outstanding balance below zero, and a note
that supersedes itself. Whether a balance is *right* is an accounting question
this cannot answer.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class NotePayable(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.borrower, "abbr") or ""
		note_name = str(self.note_name or "").strip()
		self.name = f"{note_name} - {abbr}" if abbr else note_name

	def validate(self):
		self.note_name = str(self.note_name or "").strip()
		if not self.note_name:
			frappe.throw(_("Note Name is required."))

		duplicate = frappe.db.get_value(
			"Note Payable",
			{
				"note_name": self.note_name,
				"borrower": self.borrower,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Note Payable {0} already records a note called {1} for {2}. "
					"One note per name per borrower — edit that one, or give this "
					"one a name that distinguishes it."
				).format(duplicate, self.note_name, self.borrower),
				title=_("Duplicate Note"),
			)

		if float(self.principal_original or 0) < 0:
			frappe.throw(_("Original Principal cannot be negative."))
		if float(self.principal_outstanding or 0) < 0:
			frappe.throw(
				_(
					"Principal Outstanding cannot be negative. A note paid past its "
					"balance is an overpayment, which is a receivable rather than a "
					"smaller note."
				)
			)

		if self.maturity_date and self.origination_date and self.maturity_date < self.origination_date:
			frappe.throw(_("Maturity Date cannot be before Origination Date."))

		if self.superseded_by and self.superseded_by == self.name:
			frappe.throw(_("A note cannot supersede itself."))
