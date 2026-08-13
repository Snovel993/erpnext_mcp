# SPDX-License-Identifier: MIT
"""Controller for Settlement Statement — the packer's account, recomputed.

v0.67.0. A settlement statement is a document somebody else wrote about the
grower's fruit, and every number on it is one the grower is being asked to
accept. What this controller does is recompute the four that can be recomputed
and leave the rest exactly as they arrived.

────────────────────────────────────────────────────────────────────────────
WHAT IS COMPUTED, AND WHY EACH ONE
────────────────────────────────────────────────────────────────────────────

`packout_pct` and `cull_pct` are packed-over-delivered and culled-over-delivered.
They are the numbers one packer is compared with another by, and they are the
numbers a statement is most likely to state in a way that flatters the packer —
computed off a different denominator, or off a period that is not the period.
Recomputing them from the weights on the record means the comparison is always
made the same way, which is the only thing that makes a comparison mean
anything.

`total_gross_revenue` is the sum of the line items. `total_deductions` is the
sum of the deduction rows. `net_proceeds` is the first less the second, and it
is the one number here worth reconciling against what the bank actually received.

`gross_amount` on a line is packed weight times price per unit WHERE THE
STATEMENT LEFT IT BLANK, and is left alone where the statement gave one. Same
rule, and same reason, as the line totals on an Expense Receipt: a packer who
applied a promotion or a partial-pool adjustment to one line is telling the
truth and the multiplication is not.

────────────────────────────────────────────────────────────────────────────
WHAT IS DELIBERATELY *NOT* RECONCILED
────────────────────────────────────────────────────────────────────────────

**The line items are never reconciled against `packed_weight`.** Fruit still in
storage, fruit repacked into a later pool and fruit sold on a different price
basis all live between the packout figure and the priced lines, and a validation
demanding they agree would refuse most real statements.

**`cull_weight` is never derived from delivered minus packed.** Juice, shrink
and fruit not yet run all sit in that gap. Deriving the cull from it would
manufacture a cull percentage out of a storage lag, and cull percentage is a
number growers renegotiate contracts over.

**The Scale Tickets are not summed into `gross_delivered_weight`.** The whole
value of keeping tickets is that the two figures are INDEPENDENT: one is the
packer's, one is the grower's, and a controller that overwrote the grower's copy
with the packer's would destroy the only comparison worth making. `get_settlement_statement`
reports both side by side and names the difference.

────────────────────────────────────────────────────────────────────────────
STATUS IS DERIVED, LIKE A SCALE TICKET'S
────────────────────────────────────────────────────────────────────────────

    Draft      docstatus 0
    Submitted  docstatus 1, no journal entry
    Posted     docstatus 1, `posted_journal_entry` set
    Cancelled  docstatus 2

NOTHING IN v0.67.0 SETS `posted_journal_entry`, and that is said here rather than
left to be discovered: the tool that books settlement proceeds to the ledger is a
later sprint. The column and the state exist now so that the sprint which writes
it does not have to migrate every statement captured before it.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import shifts

DOCTYPE = "Settlement Statement"

DRAFT = "Draft"
SUBMITTED = "Submitted"
POSTED = "Posted"
CANCELLED = "Cancelled"

STATUSES = (DRAFT, SUBMITTED, POSTED, CANCELLED)

#: The same five units a Scale Ticket carries, because the two are compared.
WEIGHT_UOMS = ("Kg", "Lb", "Ton", "Bin", "Box")

#: What the Select on Settlement Deduction ships with. Kept here as well as in
#: the JSON so a bad value is refused with the list rather than with a Frappe
#: traceback, and asserted equal to the JSON's options by the tests.
DEDUCTION_TYPES = ("Packing", "Cold Storage", "Marketing", "Commission", "Other")


def status_for(docstatus, posted_journal_entry) -> str:
	"""The one status those two facts allow. The only place status is decided."""
	if int(docstatus or 0) == 2:
		return CANCELLED
	if int(docstatus or 0) == 0:
		return DRAFT
	return POSTED if posted_journal_entry else SUBMITTED


def next_statement_name(company: str) -> str:
	"""`SS-<abbr>-0001`, counted from the statements this company already has."""
	abbr = str(frappe.db.get_value("Company", company, "abbr") or "").strip() or "X"
	return shifts.next_in_series(DOCTYPE, "SS", abbr)


def percent_of(part, whole) -> float:
	"""`part / whole` as a percentage, or 0 where there is no denominator.

	Zero rather than None: `packout_pct` is a Percent column and a settlement with
	no delivered weight on it has a packout of nothing, not of unknown. The
	distinction that matters — "the packer did not state a delivered weight" — is
	visible in `gross_delivered_weight` itself, which is where somebody would look.
	"""
	whole = float(whole or 0)
	if not whole:
		return 0.0
	return round(float(part or 0) / whole * 100, 2)


class SettlementStatement(Document):
	def autoname(self):
		self.name = next_statement_name(self.company)

	def validate(self):
		self.check_the_period()
		self.check_the_weights()
		self.fill_in_line_amounts()
		self.compute_the_percentages()
		self.compute_the_money()
		self.status = status_for(self.docstatus, self.posted_journal_entry)

	def on_submit(self):
		self.status = status_for(1, self.posted_journal_entry)
		self.db_set("status", self.status, update_modified=False)

	def on_cancel(self):
		self.status = CANCELLED
		self.db_set("status", CANCELLED, update_modified=False)
		self.release_matched_tickets()

	def release_matched_tickets(self) -> None:
		"""Un-claim every Scale Ticket this statement had matched.

		A cancelled settlement has not paid for anything, and a ticket left
		pointing at one would sit in the register reading `Matched` — which is to
		say, paid for — for ever. `list_scale_tickets(unmatched=true)` is the
		unpaid list, and a load whose only settlement was withdrawn belongs back
		on it. The tickets go to `Submitted`, which is exactly what they were
		before the settlement claimed them.
		"""
		for name in frappe.db.get_all("Scale Ticket", filters={"settlement": self.name}, pluck="name"):
			frappe.db.set_value(
				"Scale Ticket",
				name,
				{"settlement": None, "status": SUBMITTED},
				update_modified=False,
			)

	# ── the parts ───────────────────────────────────────────────────────────
	def check_the_period(self) -> None:
		if self.period_start and self.period_end and str(self.period_start) > str(self.period_end):
			frappe.throw(
				_("Period Start ({0}) is after Period End ({1}).").format(self.period_start, self.period_end)
			)

	def check_the_weights(self) -> None:
		for fieldname, label in (
			("gross_delivered_weight", "Gross Delivered Weight"),
			("packed_weight", "Packed Weight"),
			("cull_weight", "Cull Weight"),
		):
			if float(self.get(fieldname) or 0) < 0:
				frappe.throw(_("{0} cannot be negative.").format(_(label)))

		if self.weight_uom and self.weight_uom not in WEIGHT_UOMS:
			frappe.throw(_("Weight UOM must be one of: {0}.").format(", ".join(WEIGHT_UOMS)))

	def fill_in_line_amounts(self) -> None:
		"""Packed weight times price, only where the statement left the amount blank."""
		for row in self.get("line_items") or []:
			existing = _get(row, "gross_amount")
			if existing not in (None, "", 0, 0.0):
				continue
			weight = float(_get(row, "packed_weight") or 0)
			price = float(_get(row, "price_per_unit") or 0)
			if not weight or not price:
				continue
			_set(row, "gross_amount", round(weight * price, 2))

	def compute_the_percentages(self) -> None:
		self.packout_pct = percent_of(self.packed_weight, self.gross_delivered_weight)
		self.cull_pct = percent_of(self.cull_weight, self.gross_delivered_weight)

	def compute_the_money(self) -> None:
		gross = sum(float(_get(row, "gross_amount") or 0) for row in self.get("line_items") or [])
		deducted = sum(float(_get(row, "amount") or 0) for row in self.get("deductions") or [])
		self.total_gross_revenue = round(gross, 2)
		self.total_deductions = round(deducted, 2)
		self.net_proceeds = round(gross - deducted, 2)


def _get(row, key):
	"""One field off a child row, whichever shape the row is in."""
	return row.get(key) if isinstance(row, dict) else getattr(row, key, None)


def _set(row, key, value) -> None:
	if isinstance(row, dict):
		row[key] = value
	else:
		setattr(row, key, value)
