# SPDX-License-Identifier: MIT
"""I-9 Form controller.

Retention dates are auto-calculated on every save. The retention rule is
federal: retain for MAX(hire_date + 3 years, termination_date + 1 year).
When the employee has no termination date yet, only the first term applies.

THE SSN RULE IS ENFORCED HERE AND NOT ONLY IN THE TOOL, because a value that
arrives through the Desk, a data import or somebody else's script never passes
through `tools/i9.py` at all. `ssn_last_four` is stripped to four digits on
every save, whatever was put in it. `ssn_full` — v0.47.0, and the whole reason
it exists is that E-Verify submits nine digits and cannot be run from four — is
BLANKED on every save unless I-9 Settings has `store_full_ssn` switched on, so
turning that switch off is not a promise about future saves but a fact about the
next one. Frappe writes it to the encrypted `__Auth` table rather than to a
column, and nothing in this app reads it back.

RECEIPTS ARE COMPUTED, not asserted. `receipt_pending` is whether ANY examined
document on the form is a receipt, and `receipt_expires_on` is hire_date + 90
days — 8 CFR 274a.2(b)(1)(vi)'s window. Both are read-only and both are
recomputed here, so an operator who ticks a receipt box in the Desk gets the
same deadline the tool would have written.
"""
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import getdate

#: How long a receipt for a lost, stolen or damaged document stands in for the
#: document itself. 8 CFR 274a.2(b)(1)(vi).
RECEIPT_VALID_DAYS = 90

#: The three document slots Section 2 fills. Each can hold a receipt.
RECEIPT_FLAGS = ("list_a_is_receipt", "list_b_is_receipt", "list_c_is_receipt")


class I9Form(Document):
	def validate(self):
		self._strip_ssn()
		self._enforce_full_ssn_policy()
		self._compute_receipt_window()
		self._compute_retention()

	def _strip_ssn(self):
		if self.ssn_last_four:
			digits = "".join(c for c in str(self.ssn_last_four) if c.isdigit())
			self.ssn_last_four = digits[-4:] if len(digits) >= 4 else digits

	def _enforce_full_ssn_policy(self):
		"""Blank the encrypted full SSN unless the site has asked to keep it.

		Best effort on the read of the switch and deliberately so: a site whose
		I-9 Settings row does not exist yet — the state between installing this
		version and the first migrate — has not asked for anything, and the safe
		reading of "no answer" is the one that stores less.
		"""
		if not self.get("ssn_full"):
			return
		allowed = False
		try:
			allowed = bool(int(frappe.db.get_single_value("I-9 Settings", "store_full_ssn") or 0))
		except Exception:
			allowed = False
		if not allowed:
			self.ssn_full = ""

	def _compute_receipt_window(self):
		pending = any(int(self.get(flag) or 0) for flag in RECEIPT_FLAGS)
		self.receipt_pending = 1 if pending else 0
		if pending and self.hire_date:
			self.receipt_expires_on = getdate(self.hire_date) + timedelta(days=RECEIPT_VALID_DAYS)
		elif not pending:
			self.receipt_expires_on = None

	def _compute_retention(self):
		if not self.hire_date:
			return
		hire = getdate(self.hire_date)
		three_years = hire + timedelta(days=365 * 3)

		termination_date = None
		try:
			termination_date = frappe.db.get_value("Employee", self.employee, "relieving_date")
		except Exception:
			pass

		if termination_date:
			one_year_after = getdate(termination_date) + timedelta(days=365)
			retention = max(three_years, one_year_after)
		else:
			retention = three_years

		self.retention_until = retention
		self.destruction_eligible_date = retention
