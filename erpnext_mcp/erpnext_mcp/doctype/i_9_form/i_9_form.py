# SPDX-License-Identifier: MIT
"""I-9 Form controller.

Retention dates are auto-calculated on every save. The retention rule is
federal: retain for MAX(hire_date + 3 years, termination_date + 1 year).
When the employee has no termination date yet, only the first term applies.
"""
from datetime import timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import getdate


class I9Form(Document):
	def validate(self):
		self._strip_ssn()
		self._compute_retention()

	def _strip_ssn(self):
		if self.ssn_last_four:
			digits = "".join(c for c in str(self.ssn_last_four) if c.isdigit())
			self.ssn_last_four = digits[-4:] if len(digits) >= 4 else digits

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
