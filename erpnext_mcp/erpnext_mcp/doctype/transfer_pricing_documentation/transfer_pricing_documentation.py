# SPDX-License-Identifier: MIT
"""Controller for Transfer Pricing Documentation — the arm's-length case, dated.

WHAT IS CHECKED HERE IS WHAT WOULD OTHERWISE BE FOUND BY AN EXAMINER. A memo
whose period runs backwards covers nothing; a memo marked Complete with no
justification in it is a checkbox somebody ticked; a memo that supersedes itself
is a loop the disclosure report would walk forever. All three are cheap to find
on save and expensive to find in an audit, so all three are found here.

WHY `Complete` IS ENFORCED AS A CONTENT CHECK AND NOT AS A WORKFLOW. The status
is what the control reads: Draft does not cover a transaction, Complete does. If
Complete could be set on an empty record then the control would be satisfied by
the act of claiming to have done the work, which is the one failure mode a
documentation control cannot survive. So the promotion to Complete is where the
required content is demanded — not on every save, which would make a memo
impossible to draft in two sittings.

THE PERIOD IS THE UNIT, and it is a deliberate loosening. One memo covering a
season of hauling is how the work is actually done; a memo per invoice is how a
control gets abandoned in week two. `covers()` is the whole of the matching rule
and it lives here rather than in the tools, so the register, the disclosure
report and the gate cannot come to disagree about what "documented" means.
"""

import frappe
from frappe import _
from frappe.model.document import Document

DRAFT = "Draft"
COMPLETE = "Complete"
SUPERSEDED = "Superseded"

#: How far a period's actual dealings may exceed the documented amount before the
#: documentation stops covering them. Ten per cent, and it is a judgement rather
#: than a rule from anywhere: a memo written for a season is written in advance
#: and the season does not land on the number. What it is NOT is unbounded — a
#: memo for $12,000 does not document $140,000, and the control says so with both
#: figures in the sentence.
AMOUNT_TOLERANCE = 0.10


class TransferPricingDocumentation(Document):
	def validate(self):
		self.market_rate_reference = str(self.market_rate_reference or "").strip()
		self.justification = str(self.justification or "").strip()

		if self.period_start and self.period_end and self.period_end < self.period_start:
			frappe.throw(
				_(
					"Period End ({0}) is before Period Start ({1}). A period that runs backwards covers nothing."
				).format(self.period_end, self.period_start)
			)

		if self.superseded_by and self.superseded_by == self.name:
			frappe.throw(_("A memo cannot supersede itself."))

		if frappe.utils.flt(self.amount) < 0:
			frappe.throw(
				_(
					"Documented Amount is negative ({0}). Record the direction with Transaction Type, not with the sign."
				).format(self.amount)
			)

		self._check_the_party_belongs_to_this_company()
		self._check_it_is_finished_before_it_says_so()

	def _check_the_party_belongs_to_this_company(self) -> None:
		"""A memo defending entity A's books cannot name entity B's counterparty.

		Caught here because the alternative is a disclosure schedule that footed
		correctly and described the wrong entity's dealings.
		"""
		if not self.related_party or not self.company:
			return
		owner = frappe.db.get_value("Related Party", self.related_party, "company")
		if owner and owner != self.company:
			frappe.throw(
				_(
					"Related Party {0} belongs to {1}, but this documentation is for {2}. A "
					"transfer pricing memo defends one entity's books — register the "
					"relationship under {2} if it exists there too."
				).format(self.related_party, owner, self.company)
			)

	def _check_it_is_finished_before_it_says_so(self) -> None:
		"""Complete demands the content. Draft demands nothing — that is what Draft is for."""
		if self.status != COMPLETE:
			return
		missing = []
		if not self.justification:
			missing.append(_("Arm's-Length Justification"))
		if not self.market_rate_reference:
			missing.append(_("Market Rate Reference"))
		if not self.pricing_method:
			missing.append(_("Pricing Method"))
		if missing:
			frappe.throw(
				_(
					"This memo is marked Complete with {0} empty. Complete is what the "
					"related-party control reads as 'documented', so a Complete memo with no "
					"case in it would satisfy the control by claiming the work rather than "
					"doing it. Fill it in, or leave the status at Draft — a draft is a "
					"recognised state and the control reports it as one."
				).format(", ".join(missing)),
				title=_("Not Finished"),
			)

	def covers(self, transaction_type: str, posting_date: str, amount=None) -> bool:
		"""Whether this memo documents a dealing of that kind, on that date, at that size."""
		return covers_row(self.as_dict(), transaction_type, posting_date, amount)


def covers_row(row: dict, transaction_type: str, posting_date: str, amount=None) -> bool:
	"""THE ONE DEFINITION OF "DOCUMENTED" ON THIS SITE.

	Takes a plain row rather than a Document because every caller that matters
	reads memos in bulk — the gate checking one transaction, the register listing
	a season, the disclosure report footing a year — and loading each one as a
	document to ask it a four-field question is a query per memo for nothing.

	The method above delegates here rather than the other way round, so the rule
	has exactly one implementation. Two would eventually disagree, and the shape
	of that disagreement is the worst one available: a disclosure report telling
	an operator they are covered while the control refuses the transaction.

	Returns False for anything not Complete, which is what makes `Draft` a real
	state rather than a label — see the class docstring.
	"""
	if str(row.get("status") or "") != COMPLETE:
		return False
	if transaction_type and str(row.get("transaction_type") or "") != transaction_type:
		return False
	date = str(posting_date or "")
	start = str(row.get("period_start") or "")
	end = str(row.get("period_end") or "")
	if date and start and date < start:
		return False
	if date and end and date > end:
		return False
	if amount is not None:
		ceiling = frappe.utils.flt(row.get("amount")) * (1 + AMOUNT_TOLERANCE)
		if frappe.utils.flt(amount) > ceiling:
			return False
	return True
