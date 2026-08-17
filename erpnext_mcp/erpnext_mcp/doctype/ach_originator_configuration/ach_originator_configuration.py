# SPDX-License-Identifier: MIT
"""ACH Originator Configuration controller.

Both routing numbers are check-digit validated on save for the same reason the
employee's is: a wrong one here is wrong on every entry in every file this
company ever originates, rather than on one worker's deposit.

The blanks are filled in here rather than at generation time, so that what an
operator sees on the form is what will actually appear in the file — a default
resolved silently downstream is a default nobody can check before payday.
"""
from __future__ import annotations

import re

import frappe
from frappe.model.document import Document

from ....nacha import routing_checksum_ok


class ACHOriginatorConfiguration(Document):
	def validate(self):
		self.originating_dfi = _clean_routing(self.originating_dfi, "originating DFI routing number")

		if str(self.immediate_destination or "").strip():
			self.immediate_destination = _clean_routing(
				self.immediate_destination, "immediate destination",
			)
		else:
			self.immediate_destination = self.originating_dfi

		company_id = str(self.company_identification or "").strip()
		if not company_id:
			frappe.throw(
				"A company identification is required — it is the ten-character identifier the "
				"originating bank issued, and the receiving bank shows it to the employee as the "
				"source of the deposit.",
				frappe.ValidationError,
			)
		if len(company_id) > 10:
			frappe.throw(
				f"The company identification {company_id!r} is {len(company_id)} characters and the "
				"ACH field is 10.",
				frappe.ValidationError,
			)
		self.company_identification = company_id

		if not str(self.immediate_origin or "").strip():
			self.immediate_origin = company_id

		if not str(self.company_name or "").strip() and self.company:
			self.company_name = str(self.company)[:16]

		if not str(self.entry_description or "").strip():
			self.entry_description = "PAYROLL"

		if int(self.settlement_days or 0) < 0:
			frappe.throw("Settlement days cannot be negative.", frappe.ValidationError)


def _clean_routing(value, label: str) -> str:
	digits = re.sub(r"\D", "", str(value or ""))
	if not digits:
		frappe.throw(f"A {label} is required.", frappe.ValidationError)
	if len(digits) != 9:
		frappe.throw(
			f"The {label} {value!r} has {len(digits)} digits. An ABA routing number has exactly 9.",
			frappe.ValidationError,
		)
	if not routing_checksum_ok(digits):
		frappe.throw(
			f"The {label} {digits} fails the ABA check-digit test, so it is not a real routing "
			"number — most often that means two digits are transposed.",
			frappe.ValidationError,
		)
	return digits
