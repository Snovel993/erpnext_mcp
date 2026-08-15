# SPDX-License-Identifier: MIT
"""Controller for Advisory Agreement — what somebody is charging, and for what.

v0.73.0. An advisory fee is the one recurring cost on a farm's books that arrives
already deducted: it appears on a brokerage statement as a number nobody
authorised line by line, and the only thing that says whether it is the right
number is an agreement in a drawer. This doctype is that agreement as a record,
so the question "is this quarter's fee what we agreed?" is arithmetic rather than
a document hunt.

WHAT IT VALIDATES AND WHY EACH ONE IS WORTH REFUSING A SAVE OVER:

  * **A fee type with no fee.** `Percent of AUM` with an empty percentage
    computes to nothing at all, and an agreement that computes to nothing looks
    exactly like an agreement with no fee. `Hybrid` needs both halves for the
    same reason — the half that survived becomes the whole fee.
  * **A termination before its effective date.** Not a typo worth inheriting:
    every "was this in force on that date" query reads the pair.
  * **A Terminated agreement with no termination date.** "It ended" and "we do
    not know when" are different facts, and only one of them can be billed
    against.
  * **A second Active agreement on one account.** Two live sets of terms on one
    brokerage account is two answers to what the fee is. An amendment goes
    through the versioning path — new record, old one Superseded — which is what
    `update_advisory_agreement` does and what `amended_from` records.
  * **An account belonging to another company.** A fee charged against one
    entity's account is not a cost of another's.

AMENDMENT IS A NEW RECORD, NOT AN EDIT, and that is the whole reason
`amended_from` is here. The terms in force when a fee was charged are what
justify the fee; editing them in place would make last quarter's charge
unjustifiable by a record that now says something else. The prior agreement
becomes Superseded and stays readable forever.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

STATUSES = ("Active", "Terminated", "Superseded")

FEE_TYPES = ("Percent of AUM", "Flat Annual", "Hybrid")

BILLING_FREQUENCIES = ("Monthly", "Quarterly", "Annually")

#: How many billings a year each frequency is, for turning an annualised rate
#: into the number that will actually appear on a statement. Here rather than in
#: the tool module so the Desk and the MCP surface divide by the same number.
PERIODS_PER_YEAR = {"Monthly": 12, "Quarterly": 4, "Annually": 1}


class AdvisoryAgreement(Document):
	def validate(self):
		self.normalise()
		self.validate_dates()
		self.validate_fee()
		self.validate_bank_account_company()
		self.validate_single_active()

	def normalise(self):
		for fieldname in ("agreement_name", "client_entity", "advisor_entity", "amendment_reason"):
			value = self.get(fieldname)
			if isinstance(value, str):
				self.set(fieldname, value.strip())
		self.status = self.status or "Active"
		self.fee_type = self.fee_type or "Percent of AUM"
		self.billing_frequency = self.billing_frequency or "Quarterly"

	def validate_dates(self):
		if self.termination_date and self.effective_date:
			if getdate(self.termination_date) < getdate(self.effective_date):
				frappe.throw(
					_("Termination Date ({0}) is before Effective Date ({1}).").format(
						self.termination_date, self.effective_date
					)
				)
		if self.status == "Terminated" and not self.termination_date:
			frappe.throw(
				_(
					"A Terminated agreement needs a Termination Date. 'It ended' and 'we do not know "
					"when' are different facts, and only one of them can be billed against."
				)
			)

	def validate_fee(self):
		"""A fee type has to have the fee it names. See the module docstring."""
		if self.fee_type not in FEE_TYPES:
			frappe.throw(
				_("Fee Type must be one of: {0} — got {1}.").format(", ".join(FEE_TYPES), self.fee_type)
			)
		if self.billing_frequency not in BILLING_FREQUENCIES:
			frappe.throw(
				_("Billing Frequency must be one of: {0} — got {1}.").format(
					", ".join(BILLING_FREQUENCIES), self.billing_frequency
				)
			)
		percent = flt(self.fee_percent_of_aum)
		flat = flt(self.fee_flat_annual)
		if percent < 0 or flat < 0:
			frappe.throw(_("A fee cannot be negative."))
		if self.fee_type in ("Percent of AUM", "Hybrid") and not percent:
			frappe.throw(
				_("Fee Type is {0}, so Fee Percent of AUM is required — 1.0 means 1%.").format(self.fee_type)
			)
		if self.fee_type in ("Flat Annual", "Hybrid") and not flat:
			frappe.throw(_("Fee Type is {0}, so Fee Flat Annual is required.").format(self.fee_type))

	def validate_bank_account_company(self):
		if not self.bank_account:
			return
		owner = frappe.db.get_value("Bank Account", self.bank_account, "company")
		if owner and self.company and owner != self.company:
			frappe.throw(
				_("Bank Account {0} belongs to {1}, but this agreement is for {2}.").format(
					self.bank_account, owner, self.company
				)
			)

	def validate_single_active(self):
		"""One live set of terms per managed account."""
		if self.status != "Active" or not self.bank_account:
			return
		clash = frappe.db.get_value(
			self.doctype,
			{
				"bank_account": self.bank_account,
				"status": "Active",
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if clash:
			frappe.throw(
				_(
					"{0} already has an Active advisory agreement ({1}). Two live sets of terms on one "
					"account are two answers to what the fee is — amend the existing one, which "
					"supersedes it and keeps it readable."
				).format(self.bank_account, clash)
			)

	# ── computation ──────────────────────────────────────────────────────────

	def annual_fee_on(self, assets_under_management: float) -> float:
		"""What a year of this agreement costs on `assets_under_management`.

		On the controller rather than in the tool module for the reason
		`BankCategorizationRule.matches_text` is: a second implementation of a fee
		formula is a second answer to what a client owes.
		"""
		aum = flt(assets_under_management)
		total = 0.0
		if self.fee_type in ("Percent of AUM", "Hybrid"):
			total += aum * flt(self.fee_percent_of_aum) / 100.0
		if self.fee_type in ("Flat Annual", "Hybrid"):
			total += flt(self.fee_flat_annual)
		return flt(total, 2)

	def periodic_fee_on(self, assets_under_management: float) -> float:
		"""The annual fee divided by how often it is actually billed."""
		periods = PERIODS_PER_YEAR.get(self.billing_frequency or "Quarterly", 4)
		return flt(self.annual_fee_on(assets_under_management) / periods, 2)
