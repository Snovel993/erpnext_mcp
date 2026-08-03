# SPDX-License-Identifier: MIT
"""Controller for Normalization Adjustment — one add-back, and the case for it.

THE JUSTIFICATION IS THE RECORD, and the length floor is the only part of that
which can be enforced by a controller. Forty characters does not make an argument
good; it makes "one-time" and "per Tim" impossible, and those two strings are
what gets typed into a field that is merely required. Everything an auditor,
a lender or a buyer will do with this doctype starts by reading that sentence and
asking whether it survives "so why will it not happen again?" — a question no
schema can answer and every one of these records has to.

────────────────────────────────────────────────────────────────────────────
WHY THE APPROVAL RULES ARE ON SAVE AND NOT ON SUBMIT
────────────────────────────────────────────────────────────────────────────

This doctype is deliberately NOT submittable, which is a departure from Heat
Exposure Event and worth the sentence. A submittable doctype has one irreversible
transition and this workflow has two terminal states that are not the same thing
— Approved and Rejected — plus a third, Superseded, that a correction produces
years later. Modelling that as docstatus would make a rejection either
indistinguishable from an approval or impossible to record at all, and the
rejected proposals are worth keeping for exactly the reason a rejected insurance
claim is.

So the status field IS the workflow, and the rules bind wherever it moves:

  1. `Approved` with no signature is refused. The entire argument for this
     doctype is that a normalization is a judgement with somebody's name against
     it; a status set without one is the status without the name.
  2. `Approved` writes its own `approved_on` if nothing set it. An approval date
     somebody can type is an approval date somebody can type to before the
     quarter closed.
  3. `Rejected` with no reason is refused. A refusal with no reason reads as an
     argument somebody lost rather than a claim that failed, and it teaches the
     next proposal nothing.

────────────────────────────────────────────────────────────────────────────
ONE APPROVED ADJUSTMENT PER COMPANY, PERIOD AND CATEGORY
────────────────────────────────────────────────────────────────────────────

Enforced here rather than by a unique index, because the constraint is not on the
tuple — it is on the tuple AMONG APPROVED ROWS. A company may have three rejected
proposals and one approved one for the same quarter's insurance, and that is the
register working. What it may not have is two approved ones, because those are
two answers to one question and the one a reader finds will be whichever sorted
first.

A CORRECTION SUPERSEDES. Point the old row's `superseded_by` at the new one and
set its status to Superseded; it leaves the trail of what was believed before,
which is the half of a restatement anybody actually wants.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import kpi


class NormalizationAdjustment(Document):
	def autoname(self):
		"""NADJ-YYYY-0001, where YYYY is the year the PERIOD starts in.

		Not the year somebody keyed it. A Q4 adjustment written up in January is
		about the year that ended, and a series keyed off `today()` would file it
		under the following year for ever — which matters more here than on most
		doctypes, because these are looked up by the year they defend.
		"""
		year = str(self.period_start or frappe.utils.today())[:4]
		self.name = kpi.next_in_series(year)

	def validate(self):
		if not self.status:
			self.status = kpi.STATUS_DRAFT
		self._require_a_positive_amount()
		self._require_a_coherent_period()
		self._require_the_justification()
		self._check_the_approval()
		self._check_the_rejection()
		self._check_the_supersession()
		self._refuse_a_second_approved_adjustment()

	# ── the parts ───────────────────────────────────────────────────────────
	def _require_a_positive_amount(self) -> None:
		"""Positive always; the sign lives in `direction`.

		A negative amount beside a direction of Subtract is a double negative, and
		a double negative is how a normalization ends up moving the number the
		wrong way in a pack somebody is borrowing against.
		"""
		if float(self.amount or 0) <= 0:
			frappe.throw(
				_(
					"Amount must be positive. The direction of the adjustment is the Direction "
					"field — 'Add-back to OCF' or 'Subtract from OCF' — and keeping the sign out "
					"of the amount is deliberate: a negative amount beside a Subtract is a double "
					"negative, and a double negative is how an adjustment ends up moving the "
					"number the wrong way in a lender's pack."
				),
				title=_("Amount Must Be Positive"),
			)

	def _require_a_coherent_period(self) -> None:
		if not self.period_start or not self.period_end:
			frappe.throw(_("Period Start and Period End are both required."))
		if str(self.period_end) < str(self.period_start):
			frappe.throw(
				_("Period End ({0}) is before Period Start ({1}).").format(
					self.period_end, self.period_start
				),
				title=_("Period Runs Backwards"),
			)
		self._check_the_fiscal_year()

	def _check_the_fiscal_year(self) -> None:
		"""The period has to sit inside the year it is filed under.

		A normalization is defended INSIDE a closed set of books. One straddling
		two fiscal years cannot be checked against either year's cash flow
		statement, so it is refused with both windows named — the useful next
		question is "which of these two is wrong", and neither date alone answers
		it.

		A Fiscal Year this site cannot describe is not checked. The link field
		already refuses a year that does not exist; a year that exists and answers
		nothing about its own dates is a half-migrated record, and refusing every
		adjustment over it would be refusing the wrong thing.
		"""
		if not self.fiscal_year:
			return
		year = (
			frappe.db.get_value(
				"Fiscal Year", self.fiscal_year, ["year_start_date", "year_end_date"], as_dict=True
			)
			or {}
		)
		start = str(year.get("year_start_date") or "")
		end = str(year.get("year_end_date") or "")
		if not start or not end:
			return
		if str(self.period_start) < start or str(self.period_end) > end:
			frappe.throw(
				_(
					"The period {0} to {1} is not inside fiscal year {2}, which runs {3} to {4}. "
					"A normalization is defended inside a closed set of books — one straddling two "
					"years cannot be checked against either year's cash flow statement. Split it, "
					"or file it under the year it belongs to."
				).format(self.period_start, self.period_end, self.fiscal_year, start, end),
				title=_("Period Outside the Fiscal Year"),
			)

	def _require_the_justification(self) -> None:
		text = str(self.justification or "").strip()
		if len(text) < kpi.MIN_JUSTIFICATION:
			frappe.throw(
				_(
					"The justification is {0} character(s) and has to be at least {1}. This is not "
					"a quality bar — no character count is one. It is a floor under 'one-time' and "
					"'per Tim', which are what gets typed when the field is merely required, and "
					"both of which an auditor reads as an admission that nobody thought about it. "
					"What the sentence has to answer is the question every buyer asks: WHY WILL "
					"THIS NOT HAPPEN AGAIN?"
				).format(len(text), kpi.MIN_JUSTIFICATION),
				title=_("Justification Too Short"),
			)

	def _check_the_approval(self) -> None:
		if self.status != kpi.STATUS_APPROVED:
			return
		if not str(self.approver_signature or "").strip():
			frappe.throw(
				_(
					"This adjustment cannot be Approved without the approver's signature. The "
					"whole argument for this record is that a normalization is a judgement with "
					"somebody's name against it, and a status set with no signature is the status "
					"without the name. Leave it in {0} until the signature is captured."
				).format(kpi.STATUS_PENDING),
				title=_("Approved Without a Signature"),
			)
		self.approved_on = self.approved_on or frappe.utils.now()

	def _check_the_rejection(self) -> None:
		if self.status != kpi.STATUS_REJECTED:
			return
		if not str(self.rejection_reason or "").strip():
			frappe.throw(
				_(
					"A rejected adjustment needs a reason. It is the more useful half of the "
					"register: a rejection with a reason teaches the next proposal, and one "
					"without reads as an argument somebody lost rather than a claim that failed."
				),
				title=_("Rejected Without a Reason"),
			)

	def _check_the_supersession(self) -> None:
		if not self.superseded_by:
			return
		if self.superseded_by == self.name:
			frappe.throw(
				_("An adjustment cannot supersede itself."),
				title=_("Superseded By Itself"),
			)

	def _refuse_a_second_approved_adjustment(self) -> None:
		"""One approved row per company, period and category.

		Among APPROVED rows only. Three rejected proposals and one approved one for
		the same quarter's insurance is the register working; two approved ones are
		two answers to one question, and the one a reader finds will be whichever
		sorted first.
		"""
		if self.status != kpi.STATUS_APPROVED:
			return
		existing = frappe.db.get_all(
			kpi.DOCTYPE,
			filters={
				"company": self.company,
				"period_start": self.period_start,
				"period_end": self.period_end,
				"category": self.category,
				"status": kpi.STATUS_APPROVED,
				"name": ("!=", self.name or ""),
			},
			pluck="name",
			limit=2,
		)
		if existing:
			frappe.throw(
				_(
					"{0} is already an approved {1} adjustment for {2} covering {3} to {4}. Two "
					"approved adjustments for one company, period and category are two answers to "
					"one question, and the one a reader finds will be whichever sorted first. If "
					"this one corrects {0}, set {0}'s Superseded By to this record and its status "
					"to {5} — that leaves the trail of what was believed before, which is the half "
					"of a restatement anybody actually wants."
				).format(
					existing[0],
					self.category,
					self.company,
					self.period_start,
					self.period_end,
					kpi.STATUS_SUPERSEDED,
				),
				title=_("Already Approved for This Period"),
			)
