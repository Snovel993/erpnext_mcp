# SPDX-License-Identifier: MIT
"""Controller for Farm Garnishment — the court order itself, not the withholding.

WHY THIS EXISTS ALONGSIDE Farm Payroll Deduction. A deduction row is a standing
instruction: take this much, from this date, under this ceiling. That is what a
payroll run needs and it is all a payroll run needs. It is NOT what a court
needs. When a child support agency writes to ask why withholding stopped in
March, the answer is a case number, a date of service, a balance and a letter —
none of which belongs on a row whose job is to be read forty times a year by an
arithmetic engine.

So the ORDER is one record and the INSTRUCTION is another, and the order owns
the instruction: creating a garnishment creates the deduction, and satisfying or
terminating the garnishment stops it. The alternative — one row carrying both —
was tried in the shape of `Farm Payroll Deduction.reference`, and a free-text
reference cannot hold a balance, cannot be answered to, and cannot tell an order
that was paid off from one a court released.

────────────────────────────────────────────────────────────────────────────
PRIORITY IS DERIVED, BECAUSE IT IS NOT THE EMPLOYER'S OPINION
────────────────────────────────────────────────────────────────────────────

Child support 1, tax levy 2, student loan 3, creditor 4. The field is read-only
and recomputed on every save from the type, because it is what federal law says
and a field somebody could type into would eventually hold something else. The
rank is not decorative: 15 U.S.C. §1673(b) gives support its own and much higher
ceiling, and 29 CFR §870.11(b)(1) makes an ordinary garnishment take only what
is LEFT of the 25% pool after support has come out. An employer that ran them
in the order they arrived would short the support order and answer for it.

THE RANK IS NOT THE ENGINE'S QUEUE NUMBER. `payroll_deductions` orders by the
deduction's category — 10, 20, 30, 40 — and this doctype deliberately does not
push 1..4 into that field: a creditor order carrying priority 4 would sort ahead
of a support order at 10 and invert the very precedence this field records. The
two sequences agree about the ORDER and disagree about the integers, and the
place that reconciles them is `tools/garnishments._deduction_payload`, which
passes no priority at all and lets the category speak.

────────────────────────────────────────────────────────────────────────────
ZERO OWED IS NOT A DEBT OF NOTHING
────────────────────────────────────────────────────────────────────────────

A Currency field is 0 on every row nobody filled in, and a child support order
HAS no principal to run down — it is an ongoing obligation that ends when the
agency ends it. So the balance arithmetic only runs where `total_owed` is
positive. Treating 0 as "paid off" would mark every support order Satisfied on
the day it was filed and stop the withholding, which is the exact failure this
whole module exists to prevent.

WHERE THERE IS A BALANCE, REACHING ZERO STOPS THE MONEY. The status goes to
Satisfied, `satisfied_on` is stamped, and `on_update` retires the linked
deduction. Withholding past a satisfied judgment is money taken from somebody
under an authority that has expired, and it is the employer that took it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Federal precedence among competing orders, lowest first. See the module
#: docstring for why this is derived rather than entered, and why it is NOT the
#: integer the payroll engine sorts on.
PRIORITY_BY_TYPE = {
	"Child Support": 1,
	"Tax Levy": 2,
	"Student Loan": 3,
	"Creditor": 4,
}

#: The Farm Payroll Deduction category each type is processed under. The engine
#: knows nothing about garnishment_type; it switches on the category, and this
#: is the only place the two vocabularies meet. "Creditor" is the ordinary
#: judgment the CCPA calls a wage garnishment.
DEDUCTION_CATEGORY = {
	"Child Support": "Child Support",
	"Creditor": "Wage Garnishment",
	"Tax Levy": "Tax Levy",
	"Student Loan": "Student Loan",
}

#: The highest share of disposable earnings the statute allows each type, as a
#: percentage. `None` for a tax levy, which 29 CFR §870.11(b)(2) exempts from
#: Title III entirely — it is bounded by the exempt amount the notice leaves the
#: worker, a figure that lives on the deduction and not here. Child support is
#: 65 because that is the ceiling with both aggravating facts (no other
#: dependents, arrears over twelve weeks); which of 50/55/60/65 actually applies
#: is decided per run from the deduction's own two flags.
STATUTORY_CEILING = {
	"Child Support": 65.0,
	"Creditor": 25.0,
	"Student Loan": 15.0,
	"Tax Levy": None,
}

#: A garnishment that is no longer Active retires its deduction; one restored to
#: Active brings it back. Both directions are here on purpose — an operator who
#: corrected a Terminated entered by mistake expects the withholding to resume,
#: and a one-way map would leave the order live on the file and dead in payroll.
DEDUCTION_STATUS = {
	"Active": "Active",
	"Satisfied": "Completed",
	"Terminated": "Completed",
}


class FarmGarnishment(Document):
	def validate(self):
		self._require_identity()
		self._derive_priority()
		self._check_withholding()
		self._settle()

	def _require_identity(self):
		if not self.employee:
			frappe.throw(_("Employee is required — an order against nobody is not a record."))
		if not self.company:
			frappe.throw(_("Company is required. An order is served on an employer, and this is which one."))
		if not str(self.case_number or "").strip():
			frappe.throw(
				_(
					"Case Number is required. It is what ties every dollar withheld to the paper "
					"that authorised taking it, and an employer answering a court without one has "
					"nothing to answer with."
				)
			)
		if not self.garnishment_type:
			frappe.throw(
				_(
					"Garnishment Type is required. It decides the federal priority, the ceiling and "
					"the deduction category, and there is no lawful default among the four."
				)
			)

		# The effective date is what the acknowledgment letter tells the court,
		# so it is never left empty. Service is the better default than today:
		# an order entered a week late still began applying when it was served.
		if not self.effective_date:
			self.effective_date = self.received_date or frappe.utils.today()

	def _derive_priority(self):
		"""Always overwritten. See the module docstring — this is law, not input.

		A TYPE THIS TABLE DOES NOT KNOW RANKS LAST, NOT FIRST. The obvious
		fallback is 0, and 0 is the worst possible answer: lowest sorts first, so
		a fifth Select option added without a line in `PRIORITY_BY_TYPE` would
		rank ahead of child support and take its share of the pool. Ranking an
		unrecognised order behind every recognised one is the failure that
		collects too little rather than the one that shorts a support order, and
		`tools/garnishments._notes` says out loud that it happened.
		"""
		kind = str(self.garnishment_type or "")
		self.priority = PRIORITY_BY_TYPE.get(kind, max(PRIORITY_BY_TYPE.values()) + 1)

	def _check_withholding(self):
		amount = float(self.withholding_amount or 0)
		if amount <= 0:
			frappe.throw(
				_(
					"Withholding Amount must be greater than zero. An order that takes nothing "
					"would sit on the file looking like one that is being honoured."
				)
			)
		if self.withholding_type == "Percentage of Disposable" and amount > 100:
			frappe.throw(
				_("A percentage of disposable earnings cannot exceed 100% (got {0}).").format(amount)
			)

		ceiling = float(self.max_disposable_earnings_percentage or 0)
		if ceiling < 0:
			frappe.throw(
				_("Max Disposable Earnings % cannot be negative. Leave it at 0 to use the statutory ceiling.")
			)
		if ceiling > 100:
			frappe.throw(
				_(
					"Max Disposable Earnings % cannot exceed 100 (got {0}). No order may reach more "
					"of a worker's disposable earnings than exist."
				).format(ceiling)
			)

	def _settle(self):
		"""The balance, and the one status change arithmetic is allowed to make.

		ONLY WHERE A BALANCE EXISTS. `total_owed` of 0 is a field nobody filled
		in, and every child support order is one — see the module docstring for
		why treating it as paid off would stop the withholding on the day the
		order was filed.
		"""
		owed = float(self.total_owed or 0)
		withheld = float(self.total_withheld or 0)
		if withheld < 0:
			frappe.throw(_("Total Withheld cannot be negative. Payroll adds to it; nothing subtracts."))

		if owed <= 0:
			self.remaining_balance = 0
			return

		self.remaining_balance = max(owed - withheld, 0.0)
		if self.remaining_balance > 0 or self.status != "Active":
			return

		# Paid off. The status moves itself, because the alternative is a row
		# that reads "Active, 0.00 remaining" and goes on withholding until
		# somebody notices — and it is the employer, not the payroll clerk, that
		# answers for the money taken in the meantime.
		self.status = "Satisfied"
		if not self.satisfied_on:
			self.satisfied_on = frappe.utils.today()

	def on_update(self):
		"""Keep the linked deduction's status in step with the order's.

		THE ORDER IS THE AUTHORITY AND THE DEDUCTION IS ITS INSTRUMENT. A
		garnishment that is Satisfied or Terminated whose deduction is still
		Active is the failure mode with money in it: withholding continues under
		an authority that has expired. Restoring the order to Active restores the
		deduction, for the mirror-image reason.

		The deduction is written with `frappe.db.set_value` rather than through a
		document save: this runs inside the garnishment's own save, and a nested
		save whose validation failed would abandon a half-applied change with no
		obvious author. A status is one column and it is the only one touched.
		"""
		deduction = str(self.payroll_deduction or "").strip()
		if not deduction or not frappe.db.exists("Farm Payroll Deduction", deduction):
			return
		wanted = DEDUCTION_STATUS.get(str(self.status or ""))
		if not wanted:
			return
		current = frappe.db.get_value("Farm Payroll Deduction", deduction, "status")
		if current == wanted:
			return
		# A deduction a court has STAYED is Suspended and this must not undo
		# that: a stay is a fact about the deduction that the order's own status
		# does not carry, so only the two ends of the map are enforced.
		if current == "Suspended" and wanted == "Active":
			return
		frappe.db.set_value("Farm Payroll Deduction", deduction, "status", wanted)
