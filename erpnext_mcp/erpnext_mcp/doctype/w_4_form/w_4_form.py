# SPDX-License-Identifier: MIT
"""W-4 Form controller.

Dependents credits are auto-calculated on every save:
  under_17 × $2,000 + other × $500 = total_dependents_credit.
"""
from frappe.model.document import Document


class W4Form(Document):
    def validate(self):
        self._compute_dependents()

    def _compute_dependents(self):
        under_17 = int(self.dependents_under_17_count or 0)
        other = int(self.other_dependents_count or 0)
        self.dependents_under_17_amount = under_17 * 2000
        self.other_dependents_amount = other * 500
        self.total_dependents_credit = self.dependents_under_17_amount + self.other_dependents_amount
