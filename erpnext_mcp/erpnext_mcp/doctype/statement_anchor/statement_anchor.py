# SPDX-License-Identifier: MIT
"""Controller for Statement Anchor — the period that either ties out or does not.

v0.73.0. A bank feed gives a farm a list of transactions and no way at all to
know whether the list is COMPLETE. Every reconciliation question that matters is
about the gap between two records of the same month: the bank's opening balance,
everything the feed says moved, and the bank's closing balance. If the first plus
the second is not the third, something is missing — and nothing in the
transaction list itself can tell you that, because a transaction that never
arrived leaves no row behind.

THE ARITHMETIC IS COMPUTED HERE AND NOWHERE ELSE. `computed_closing`, `variance`
and `reconciled` are read-only fields written by `validate`, so a pipe pushing an
anchor, an operator editing one in the Desk and a rebuild all produce the same
three numbers from the same two inputs. A payload that arrives with its own
`variance` has it recomputed rather than trusted: the whole value of this record
is that the number cannot be asserted.

SIGN CONVENTION, ONCE, FOR EVERYTHING BELOW. Positive is money IN. So
`computed_closing = anchored_opening + transaction_sum` and
`variance = anchored_closing - computed_closing`. ERPNext's own Bank Transaction
signs the same way round, which is why nothing anywhere in this feature flips a
sign — and a flip introduced later would show up as every variance being exactly
twice the transaction sum, which is worth knowing what looks like.

WHY THE PERIOD IS UNIQUE PER ACCOUNT AND WHY THAT IS ENFORCED HERE. Frappe has no
composite unique index in a DocType JSON, so `validate_unique_period` is the
constraint. It has to be: two anchors for one month is two answers to "did
October tie out", and the push endpoint's idempotency — upsert on
(bank_account, period_start, period_end) — is only meaningful if the site cannot
hold a second row for the pair to disagree with.

WHY THIS DOCTYPE IS NOT SUBMITTABLE, given that it has `amended_from`. A pipe
upserts these on every sync, and a submitted document cannot be updated without
cancelling it, which would detach whatever already points at it. `amended_from`
is kept as the ordinary Link it looks like, for the case that actually happens:
a bank RESTATES a period, and the old numbers have to stay readable because
somebody reconciled against them last quarter.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, getdate

#: Tolerance used when the record does not carry one. A cent — which is rounding
#: and nothing else. See the field description for why widening it is the wrong
#: fix for a brokerage account whose advisory fee is not in the bank feed.
DEFAULT_TOLERANCE = 0.01


class StatementAnchor(Document):
	def validate(self):
		self.normalise()
		self.validate_period()
		self.inherit_company()
		self.compute_chain()
		self.validate_unique_period()
		self.detect_chain_gap()

	def normalise(self):
		for fieldname in ("plaid_account_mask", "parser_version", "variance_reason"):
			value = self.get(fieldname)
			if isinstance(value, str):
				self.set(fieldname, value.strip())
		if self.variance_tolerance in (None, ""):
			self.variance_tolerance = DEFAULT_TOLERANCE

	def validate_period(self):
		"""A period that ends before it starts orders wrongly in every chain query."""
		if not self.period_start or not self.period_end:
			return
		if getdate(self.period_end) < getdate(self.period_start):
			frappe.throw(
				_("Period End ({0}) is before Period Start ({1}).").format(self.period_end, self.period_start)
			)

	def inherit_company(self):
		"""The company comes from the Bank Account, always, and is never typed.

		Stored rather than resolved at read time because every query against this
		doctype is scoped to one company, and a stored column is the difference
		between a filter and a join on every one of them.
		"""
		if not self.bank_account:
			return
		owner = frappe.db.get_value("Bank Account", self.bank_account, "company")
		if owner:
			self.company = owner

	def compute_chain(self):
		"""The three derived numbers. The only place any of them is produced."""
		self.computed_closing = flt(flt(self.anchored_opening) + flt(self.transaction_sum), 2)
		self.variance = flt(flt(self.anchored_closing) - flt(self.computed_closing), 2)
		tolerance = abs(flt(self.variance_tolerance if self.variance_tolerance else DEFAULT_TOLERANCE))
		self.reconciled = 1 if abs(flt(self.variance)) <= tolerance else 0

	def validate_unique_period(self):
		"""One anchor per (account, period). See the module docstring."""
		if not (self.bank_account and self.period_start and self.period_end):
			return
		clash = frappe.db.get_value(
			self.doctype,
			{
				"bank_account": self.bank_account,
				"period_start": self.period_start,
				"period_end": self.period_end,
				"name": ("!=", self.name or ""),
			},
			"name",
		)
		if clash:
			frappe.throw(
				_(
					"{0} already has an anchor for {1} to {2} ({3}). Two anchors for one period are "
					"two answers to whether that period tied out — update the existing one instead."
				).format(self.bank_account, self.period_start, self.period_end, clash)
			)

	def detect_chain_gap(self):
		"""Whether the prior period's closing balance is this one's opening balance.

		COMPUTED, NEVER ACCEPTED FROM A PAYLOAD, for the reason the whole record
		exists: a gap is what a MISSING STATEMENT looks like, and a pipe that has
		not got the missing statement is exactly the thing that cannot be trusted
		to report its absence.

		Only the period immediately before this one is consulted. Inserting an
		anchor out of order therefore leaves the LATER period's flag stale —
		`rebuild_anchor_chain` is what fixes a whole chain at once, and it exists
		because doing it correctly here would mean a save cascading into every
		later row on the account.
		"""
		self.chain_gap_from_prior = 0
		if not (self.bank_account and self.period_start):
			return
		prior = frappe.db.get_all(
			self.doctype,
			filters={
				"bank_account": self.bank_account,
				"period_end": ("<", self.period_start),
				"name": ("!=", self.name or ""),
			},
			fields=["name", "anchored_closing", "period_end"],
			order_by="period_end desc",
			limit=1,
		)
		if not prior:
			return
		tolerance = abs(flt(self.variance_tolerance if self.variance_tolerance else DEFAULT_TOLERANCE))
		drift = flt(flt(self.anchored_opening) - flt(prior[0].get("anchored_closing")), 2)
		self.chain_gap_from_prior = 1 if abs(drift) > tolerance else 0
