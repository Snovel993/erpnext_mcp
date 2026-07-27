# SPDX-License-Identifier: MIT
"""Controller for Cap Table Entry — the member register.

TWO RULES, BOTH ABOUT THE KEY. A cap table is only authoritative if there is
exactly one row per member per company, and if the row can be found by the
identifier every other document uses. So the docname is built from the member id
and the company abbreviation (`"Member-01 - OML"`), the same shape ERPNext gives
an Account or a Cost Center, and `validate` refuses a second entry for the same
member id in the same company before the database's unique index would.

Deliberately NOT auto-retired by anything here. `retired` and `withdrawal_date`
move together and only through `close_cap_table_entry`, because a member who
silently stopped appearing is the exact failure this register exists to prevent.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CapTableEntry(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		member_id = str(self.member_id or "").strip()
		self.name = f"{member_id} - {abbr}" if abbr else member_id

	def validate(self):
		self.member_id = str(self.member_id or "").strip()
		if not self.member_id:
			frappe.throw(_("Member ID is required."))

		duplicate = frappe.db.get_value(
			"Cap Table Entry",
			{
				"member_id": self.member_id,
				"company": self.company,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Cap Table Entry {0} already registers member {1} for {2}. "
					"One entry per member per company — edit that one."
				).format(duplicate, self.member_id, self.company),
				title=_("Duplicate Member"),
			)

		percentage = float(self.ownership_percentage or 0)
		if percentage < 0 or percentage > 100:
			frappe.throw(_("Ownership Percentage must be between 0 and 100."))

		if self.withdrawal_date and self.admission_date and self.withdrawal_date < self.admission_date:
			frappe.throw(_("Withdrawal Date cannot be before Admission Date."))
