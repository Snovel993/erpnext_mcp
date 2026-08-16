# SPDX-License-Identifier: MIT
"""Controller for Revenue Contract — what was promised, and what it is worth.

WHY A CONTRACT DOCTYPE AT ALL, GIVEN THERE IS A SALES ORDER. A Sales Order is a
fulfilment document: what to ship, when, at what price. ASC 606 asks four
questions a Sales Order does not carry — what the distinct performance
obligations are, how the transaction price is allocated between them, whether
each transfers control at a point in time or over time, and when each was
actually satisfied. On a produce operation the gap is at its widest: fruit is
delivered in September against a settlement that arrives in December at a price
nobody knew in September, and the Sales Order has no field that can hold the
difference between "delivered" and "earned".

WHAT `validate` REFUSES, and both are about a contract that would make a
recognition schedule mean something other than what it says:

  * OBLIGATIONS ALLOCATED PAST THE TRANSACTION PRICE. If the parts sum to more
    than the whole, some of them cannot be earned, and which ones would depend on
    the order somebody recognised them in.
  * A SCHEDULE TOTALLING MORE THAN THE CONTRACT. The same failure one layer out.

Both are refused as ERRORS rather than reported, and that is a deliberate
exception to this release's usual instinct. The advisory/enforced switch exists
for judgements about an operation's PROCESS — who approved a spend, whether a
month was closed properly. This is not a judgement: a contract whose parts
exceed its whole is arithmetically incoherent, and there is no operating posture
under which writing one down is the right outcome.

WHAT IT DOES NOT REFUSE. Obligations summing to LESS than the transaction price —
that is a contract somebody is still writing, and refusing it would make the
doctype unusable until the last line was typed. An unallocated contract with no
obligations at all: a single-obligation contract is the common case and forcing
somebody to restate it as a one-row table is ceremony.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Currency tolerance on the two sum checks. Matches the double-entry tolerance
#: in `tools/mutate.py` — the same question, about the same kind of number.
SUM_TOLERANCE = 0.005


class RevenueContract(Document):
	def autoname(self):
		abbr = frappe.db.get_value("Company", self.company, "abbr") or ""
		title = str(self.contract_name or "").strip()
		self.name = f"{title} - {abbr}" if abbr else title

	def validate(self):
		self.contract_name = str(self.contract_name or "").strip()
		if not self.contract_name:
			frappe.throw(_("Contract Name is required."))
		if not self.status:
			self.status = "Draft"
		if not self.recognition_method:
			self.recognition_method = "Point in Time"

		duplicate = frappe.db.get_value(
			"Revenue Contract",
			{
				"contract_name": self.contract_name,
				"company": self.company,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"Revenue Contract {0} already carries that name for this company. One contract "
					"per name per company — edit that one, or name this for what distinguishes it."
				).format(duplicate),
				title=_("Duplicate Contract"),
			)

		if float(self.total_value or 0) < 0:
			frappe.throw(_("Total Value cannot be negative."))
		if self.end_date and self.start_date and self.end_date < self.start_date:
			frappe.throw(_("End Date cannot be before Start Date."))

		self._check_the_sums()
		self._recount()

	def _check_the_sums(self) -> None:
		total = float(self.total_value or 0)
		if not total:
			return

		allocated = sum(float(row.get("allocated_amount") or 0) for row in self.get("obligations") or [])
		if allocated - total > SUM_TOLERANCE:
			frappe.throw(
				_(
					"The performance obligations allocate {0}, which is more than the contract's "
					"transaction price of {1}. Some of that can never be earned, and which part "
					"would depend on the order somebody recognised them in."
				).format(round(allocated, 2), round(total, 2)),
				title=_("Allocated past the price"),
			)

		scheduled = sum(float(row.get("amount") or 0) for row in self.get("schedule") or [])
		if scheduled - total > SUM_TOLERANCE:
			frappe.throw(
				_(
					"The recognition schedule totals {0}, which is more than the contract's "
					"transaction price of {1}. A schedule that can be drawn down past the contract "
					"is a schedule that will restate."
				).format(round(scheduled, 2), round(total, 2)),
				title=_("Scheduled past the price"),
			)

	def _recount(self) -> None:
		"""The three figures a list view needs and cannot aggregate for itself."""
		schedule = list(self.get("schedule") or [])
		self.recognized_amount = round(
			sum(float(row.get("amount") or 0) for row in schedule if int(row.get("recognized") or 0)), 2
		)
		self.scheduled_amount = round(sum(float(row.get("amount") or 0) for row in schedule), 2)
		self.unrecognized_amount = round(float(self.total_value or 0) - self.recognized_amount, 2)
