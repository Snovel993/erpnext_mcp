# SPDX-License-Identifier: MIT
"""Controller for Farm Payroll Deduction — the standing instructions to withhold.

WHAT THIS DOCTYPE IS FOR. Every release before it computed a slip whose only
deductions were the ones the government requires. That is the easy half of a
payroll run and not the half with a liability attached: a court sends a child
support order to the EMPLOYER, and an employer who pays the worker in full is
answerable for the money it failed to withhold and, in most states, for the
arrears on top.

ONE ROW IS ONE STANDING INSTRUCTION, NOT ONE WITHHOLDING. The row says what to
take and from when; each payroll run reads it and decides what it can actually
take out of that period's pay. The two are different numbers whenever a ceiling
binds, and the difference is reported on the slip rather than stored back here —
see `erpnext_mcp.payroll_deductions` for the ceilings and the order.

VALIDATION REFUSES THE THINGS THAT WOULD BE WRONG SILENTLY, and only those. A
percentage over 100, a window that ends before it starts, a garnishment marked
pre-tax: each of these produces a slip that computes cleanly and pays the wrong
person the wrong money, which is the only kind of wrong worth refusing a save
over. Everything else — a category this app does not know, a priority nobody
set — falls back to a lawful default in the engine rather than blocking a hire
day, and the engine says which default it used.

NOTHING HERE IS DELETED WHEN IT ENDS. `status` retires a row: Completed for an
order that has been satisfied, Suspended for one a court has stayed. A
garnishment removed from the file cannot answer the court that asks why the
withholding stopped, and a satisfied order is the record proving it was paid.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from .... import payroll_deductions

#: Categories that are garnishments whatever else the row says. Kept here as
#: the human spellings the Select actually stores; `payroll_deductions` owns
#: the normalised keys and the law behind each one.
GARNISHMENT_CATEGORIES = ("Child Support", "Wage Garnishment", "Tax Levy", "Student Loan")


class FarmPayrollDeduction(Document):
	def validate(self):
		if not self.employee:
			frappe.throw(_("Employee is required — a deduction with nobody to deduct from is not a record."))
		if not self.effective_from:
			frappe.throw(_("Effective From is required. A deduction with no start date has no period to apply to."))

		if self.effective_to and str(self.effective_to) < str(self.effective_from):
			frappe.throw(
				_("Effective To ({0}) is before Effective From ({1}). Nothing was saved.").format(
					self.effective_to, self.effective_from
				)
			)

		amount = float(self.amount or 0)
		if amount <= 0:
			frappe.throw(
				_(
					"Amount must be greater than zero. A deduction of nothing withholds nothing and "
					"would sit on the file looking like an order that is being honoured."
				)
			)

		if self.amount_type == "Percentage" and amount > 100:
			frappe.throw(
				_("A percentage deduction cannot exceed 100% (got {0}).").format(amount)
			)

		# A garnishment is never pre-tax. Money taken under a court order is
		# wages the worker earned and was taxed on; the order reaches what is
		# left. Allowing the flag would under-report taxable wages on a W-2 —
		# the engine ignores it either way, so refusing here is what stops the
		# file from carrying a claim the payroll run silently disagrees with.
		if self.deduction_type == "Garnishment" and self.pre_tax:
			frappe.throw(
				_(
					"A garnishment cannot be pre-tax. Money withheld under a court order is wages "
					"the employee earned and was taxed on, and the order reaches what is left after "
					"withholding. Untick Pre-Tax."
				)
			)

		# AGAINST THE EFFECTIVE PRE-TAX STATUS, not the stored flag. `pre_tax`
		# unset means "follow the category", and for a 401(k) or a health
		# premium the category says pre-tax — so comparing the raw field would
		# refuse the ordinary case of ticking FICA-exempt on a Section 125 row
		# that never needed the pre_tax box ticked because its category already
		# implies it. The check is only meant to catch a genuinely POST-tax
		# deduction claiming an exemption there is nothing left to exempt.
		if self.fica_exempt and not payroll_deductions.row_is_pre_tax(self.as_dict()):
			frappe.throw(
				_(
					"Also Exempt From FICA only means something on a pre-tax deduction. A post-tax "
					"deduction comes out of pay that has already been taxed for Social Security and "
					"Medicare, so there is nothing left to exempt. Tick Pre-Tax, or pick a category "
					"that is pre-tax by default."
				)
			)

		# The category decides the type unless somebody deliberately said
		# otherwise — and saying otherwise is legitimate, because `Other` is a
		# real category on both sides of the line. What is NOT legitimate is a
		# child support order filed as a voluntary election, which would put it
		# behind the union dues in the queue and outside its own ceiling.
		if self.deduction_category in GARNISHMENT_CATEGORIES and self.deduction_type != "Garnishment":
			frappe.throw(
				_(
					"{0} is a garnishment and has to be typed as one. As a voluntary deduction it "
					"would be withheld after the employee's own elections instead of ahead of them, "
					"and outside the ceiling that governs it."
				).format(self.deduction_category)
			)

		if self.priority and int(self.priority) < 0:
			frappe.throw(_("Priority cannot be negative. Leave it at 0 to use the order the law gives the category."))
