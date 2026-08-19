# SPDX-License-Identifier: MIT
"""Employee Bank Account controller.

THE VALIDATION IS HERE AND NOT ONLY IN THE TOOL, for the reason `i_9_form.py`
gives: a row that arrives through the Desk, a data import or somebody else's
script never passes through `tools/ach.py` at all. What is being protected is a
payment instruction — a routing number wrong by one digit sends somebody's wages
to another bank, and nothing about that failure is visible until the worker says
they were not paid.

WHAT IS ENFORCED

`routing_number` is normalised to nine digits and checked against the ABA check
digit, which catches every single-digit typo and every adjacent transposition.

`account_number` is written encrypted by Frappe because the field is a Password,
and `account_number_last_four` is derived from it on every save so that display
never needs the real thing. The derivation happens even when the number is
unchanged, because a row edited in the Desk can have its last-four column
overwritten by hand otherwise.

THE ALLOCATION RULES ARE ABOUT THE SET, NOT THE ROW, which is why they read
their siblings. One employee may have at most one Full account; percentages
across their active rows may not exceed 100. A row that is individually valid
can still make the set unpayable, and the set is what `generate_nacha_file`
resolves against a real net pay.
"""

from __future__ import annotations

import re

import frappe
from frappe.model.document import Document

from ....nacha import routing_checksum_ok


class EmployeeBankAccount(Document):
	def validate(self):
		self.routing_number = _clean_routing(self.routing_number)
		self._normalize_account_number()
		self._validate_allocation()
		self._validate_sibling_allocations()

	def _normalize_account_number(self):
		"""Strip the account number to what a bank would accept, and derive last-four.

		A Password field reads back as the stored value on a loaded document and as
		the new value on one being edited, so this runs on both and is idempotent.
		Frappe hands back the literal placeholder `'*'*n` on some read paths; a
		value that is nothing but asterisks is an unchanged secret rather than an
		account number, and rewriting last-four from it would destroy the column.
		"""
		raw = str(self.account_number or "").strip()
		if not raw:
			frappe.throw("An account number is required.", frappe.ValidationError)
		if set(raw) == {"*"}:
			return

		cleaned = re.sub(r"[^A-Za-z0-9]", "", raw)
		if not cleaned:
			frappe.throw(
				f"The account number {raw!r} has no letters or digits in it.",
				frappe.ValidationError,
			)
		if len(cleaned) > 17:
			frappe.throw(
				f"The account number is {len(cleaned)} characters. An ACH entry carries at most "
				"17, so a longer one cannot be sent without being truncated into a different "
				"account.",
				frappe.ValidationError,
			)
		self.account_number = cleaned
		self.account_number_last_four = cleaned[-4:]

	def _validate_allocation(self):
		allocation = str(self.allocation_type or "Full")
		amount = float(self.allocation_amount or 0)

		if allocation == "Full":
			# Not an error — a Full row with a leftover amount from an earlier
			# edit is common, and the amount is simply meaningless here.
			self.allocation_amount = 0
			return

		if amount <= 0:
			frappe.throw(
				f"An allocation type of {allocation} needs an allocation_amount above zero. "
				"Use Full for the account that takes whatever is left.",
				frappe.ValidationError,
			)
		if allocation == "Percentage" and amount > 100:
			frappe.throw(
				f"A percentage allocation of {amount} is more than the whole cheque.",
				frappe.ValidationError,
			)

	def _validate_sibling_allocations(self):
		"""One Full account per employee; percentages that total 100 or less."""
		if str(self.status or "Active") != "Active" or not self.employee:
			return

		siblings = frappe.db.get_all(
			"Employee Bank Account",
			filters={
				"employee": self.employee,
				"status": "Active",
				"name": ("!=", self.name or ""),
			},
			fields=["name", "allocation_type", "allocation_amount"],
		)

		if str(self.allocation_type) == "Full":
			existing_full = [s for s in siblings if str(s.allocation_type) == "Full"]
			if existing_full:
				frappe.throw(
					f"{self.employee} already has a Full deposit account ({existing_full[0].name}). "
					"Only one account can take the remainder of a cheque — make this one a Fixed "
					"Amount or a Percentage, or deactivate the other.",
					frappe.ValidationError,
				)

		if str(self.allocation_type) == "Percentage":
			total = float(self.allocation_amount or 0) + sum(
				float(s.allocation_amount or 0) for s in siblings if str(s.allocation_type) == "Percentage"
			)
			if total > 100:
				frappe.throw(
					f"The percentage allocations for {self.employee} would total {total:g}%, which "
					"is more than the cheque. Reduce one of them.",
					frappe.ValidationError,
				)


def _clean_routing(value) -> str:
	digits = re.sub(r"\D", "", str(value or ""))
	if not digits:
		frappe.throw("A routing number is required.", frappe.ValidationError)
	if len(digits) != 9:
		frappe.throw(
			f"The routing number {value!r} has {len(digits)} digits. An ABA routing number has exactly 9.",
			frappe.ValidationError,
		)
	if not routing_checksum_ok(digits):
		frappe.throw(
			f"The routing number {digits} fails the ABA check-digit test, so it is not a real "
			"routing number — most often that means two digits are transposed. Check it against "
			"the bottom-left corner of a cheque.",
			frappe.ValidationError,
		)
	return digits
