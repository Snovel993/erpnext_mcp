# SPDX-License-Identifier: MIT
"""Controller for Budget — refuses the two ways this record could lie to itself.

A duplicate account or a duplicate KPI target would give check_budget_variances
two conflicting answers to one question ("is this account over budget?"), and
computed data written on top of a duplicate is data nobody can trust without
first checking whether the row it landed on was the right one. Both are refused
here, at save time, rather than discovered when refresh_budget quietly picks one
row's account and ignores the other's.
"""

import frappe
from frappe import _
from frappe.model.document import Document

STATUSES = ("Draft", "Active", "Closed")


class Budget(Document):
	def validate(self):
		self._require_a_known_status()
		self._refuse_duplicate_accounts()
		self._refuse_duplicate_kpi_targets()

	def _require_a_known_status(self) -> None:
		status = str(self.status or "Draft")
		if status not in STATUSES:
			frappe.throw(
				_("status must be one of {0}; got {1}. Nothing was saved.").format(
					", ".join(STATUSES), status
				)
			)
		self.status = status

	def _refuse_duplicate_accounts(self) -> None:
		seen = {}
		for row in self.get("line_items") or []:
			account = str(row.get("account") or "").strip()
			if not account:
				frappe.throw(_("every budget line item needs an account. Nothing was saved."))
			if account in seen:
				frappe.throw(
					_(
						"{0} appears twice in this budget's line items (rows {1} and {2}). One row per "
						"account — two would be two different budgeted amounts for the same money, and "
						"refresh_budget would have to pick one and silently ignore the other. Nothing "
						"was saved."
					).format(account, seen[account], row.idx)
				)
			seen[account] = row.idx

	def _refuse_duplicate_kpi_targets(self) -> None:
		seen = {}
		for row in self.get("kpi_targets") or []:
			kpi_definition = str(row.get("kpi_definition") or "").strip()
			if not kpi_definition:
				frappe.throw(_("every KPI target needs a kpi_definition. Nothing was saved."))
			if kpi_definition in seen:
				frappe.throw(
					_(
						"{0} appears twice in this budget's KPI targets (rows {1} and {2}). One row per "
						"KPI — two would be two different targets for the same figure. Nothing was saved."
					).format(kpi_definition, seen[kpi_definition], row.idx)
				)
			seen[kpi_definition] = row.idx
