# SPDX-License-Identifier: MIT
"""Controller for Scale Ticket — the weight the grower does not control.

v0.67.0. A load of fruit goes onto a packer's truck, crosses a packer's scale,
and the grower is handed a slip of thermal paper. Months later a settlement
statement arrives saying how much of it packed out and what it was worth. The
only thing standing between those two documents is this record — and if it does
not exist, the settlement is unauditable, because there is nothing to check it
against.

────────────────────────────────────────────────────────────────────────────
NET WEIGHT IS COMPUTED, NOT TAKEN
────────────────────────────────────────────────────────────────────────────

`net_weight` is `gross_weight - tare_weight`, written here on every save and
read-only in the Desk. A net weight somebody typed is a number with no
arithmetic behind it, and it is precisely the number a settlement dispute turns
on. Where the paper ticket's own printed net disagrees with the subtraction,
THAT DISAGREEMENT IS THE FINDING — it belongs in `notes` beside the photograph,
not silently in the field the grower will later argue from.

A tare greater than the gross is refused. It is not a rounding artefact; it is
two numbers off two different tickets, or a gross entered in pounds against a
tare in kilos, and a negative net propagating into a settlement check would make
the check say the packer overpaid.

ONE UNIT PER TICKET, for the same reason. `weight_uom` covers all three weights.
A gross in pounds and a tare in kilos is a net of nothing.

────────────────────────────────────────────────────────────────────────────
STATUS IS DERIVED FROM `docstatus` AND `settlement`, NEVER SET BY HAND
────────────────────────────────────────────────────────────────────────────

Four states, and each is a fact somewhere else on the record:

    Draft      docstatus 0 — the phone still has it
    Submitted  docstatus 1, no settlement — filed, unpaid
    Matched    docstatus 1, settlement set — a statement has claimed this load
    Cancelled  docstatus 2

A `status` column an operator could type into would eventually disagree with the
docstatus beside it, and the question this doctype exists to answer — *which
delivered loads has nobody paid for yet* — is a query on exactly that column.
So it is computed on every save and on every submit, and the field is read-only.

WHY BOTH A DOCSTATUS AND A STATUS AT ALL. The docstatus is what makes a
submitted ticket immutable, which is the property a third party's weight record
needs. The status is what makes "Submitted" and "Matched" distinguishable, and
Frappe has no third docstatus to spend on it.

────────────────────────────────────────────────────────────────────────────
THE DOCNAME CARRIES THE COMPANY ABBREVIATION, NOT THE YEAR
────────────────────────────────────────────────────────────────────────────

`ST-OML-0001`. Every other dated register this app ships names itself by year,
because the question asked of a shift or a heat record is "which season". The
question asked of a scale ticket is "whose fruit" — two entities delivering to
the same packer under one bench is the ordinary case here, and a shared
`ST-2026-…` run would interleave them.

`ticket_number` is the PACKER'S number and is a separate field. It is not
unique on this site and is not made so: two packers will both have a ticket 4471
sooner or later, and a uniqueness constraint would refuse the second grower's
real ticket.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import shifts

DOCTYPE = "Scale Ticket"

DRAFT = "Draft"
SUBMITTED = "Submitted"
MATCHED = "Matched"
CANCELLED = "Cancelled"

#: Every status the Select declares, in the order a ticket moves through them.
STATUSES = (DRAFT, SUBMITTED, MATCHED, CANCELLED)

#: The units all three weights on one ticket share. A Select rather than a Link
#: to UOM: a bin is a real unit on a fruit ticket and is not on ERPNext's stock
#: UOM list, and a Link that had to be seeded before the first ticket could be
#: captured is a Link that stops a capture in an orchard.
WEIGHT_UOMS = ("Kg", "Lb", "Ton", "Bin", "Box")


def status_for(docstatus, settlement) -> str:
	"""The one status those two facts allow. The only place status is decided."""
	if int(docstatus or 0) == 2:
		return CANCELLED
	if int(docstatus or 0) == 0:
		return DRAFT
	return MATCHED if settlement else SUBMITTED


def next_ticket_name(company: str) -> str:
	"""`ST-<abbr>-0001`, counted from the tickets this company already has."""
	abbr = str(frappe.db.get_value("Company", company, "abbr") or "").strip() or "X"
	return shifts.next_in_series(DOCTYPE, "ST", abbr)


class ScaleTicket(Document):
	def autoname(self):
		self.name = next_ticket_name(self.company)

	def validate(self):
		self.check_the_weights()
		self.compute_net_weight()
		self.status = status_for(self.docstatus, self.settlement)

	def before_submit(self):
		"""A ticket with no weight on it is a delivery nobody can be paid for.

		Refused at submit rather than at save, for the reason every other
		submittable doctype in this app gives: a draft is a record in progress,
		and a foreman photographing a slip at a tailgate may have the ticket
		number and the truck before they have read the scale.
		"""
		if not float(self.gross_weight or 0) and not float(self.net_weight or 0):
			frappe.throw(
				_(
					"A Scale Ticket cannot be submitted with no weight on it. Enter the gross "
					"and tare the scale read — net is computed from them."
				),
				title=_("No Weight"),
			)

	def on_submit(self):
		self.status = status_for(1, self.settlement)
		self.db_set("status", self.status, update_modified=False)

	def on_cancel(self):
		self.status = CANCELLED
		self.db_set("status", CANCELLED, update_modified=False)

	# ── the parts ───────────────────────────────────────────────────────────
	def check_the_weights(self) -> None:
		for fieldname, label in (
			("gross_weight", "Gross Weight"),
			("tare_weight", "Tare Weight"),
		):
			if float(self.get(fieldname) or 0) < 0:
				frappe.throw(_("{0} cannot be negative.").format(_(label)))

		gross = float(self.gross_weight or 0)
		tare = float(self.tare_weight or 0)
		if gross and tare > gross:
			frappe.throw(
				_(
					"Tare Weight ({0}) is greater than Gross Weight ({1}), which would make the "
					"net weight negative. That is two numbers off different tickets, or a gross "
					"and a tare in different units — both are worth fixing before this is filed."
				).format(tare, gross),
				title=_("Tare Above Gross"),
			)

		if self.weight_uom and self.weight_uom not in WEIGHT_UOMS:
			frappe.throw(
				_("Weight UOM must be one of: {0}.").format(", ".join(WEIGHT_UOMS)),
			)

	def compute_net_weight(self) -> None:
		"""Gross minus tare, always. Never read from input.

		A ticket carrying only a net — which is all some packers print — keeps it,
		because subtracting a tare of zero from a gross of zero would erase the one
		weight the slip actually gave. That is the single case where the field is
		not the subtraction, and it is a ticket whose gross is genuinely unknown
		rather than one whose arithmetic was skipped.
		"""
		gross = float(self.gross_weight or 0)
		tare = float(self.tare_weight or 0)
		if not gross and float(self.net_weight or 0):
			return
		self.net_weight = round(gross - tare, 3)
