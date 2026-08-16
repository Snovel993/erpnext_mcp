# SPDX-License-Identifier: MIT
"""Controller for Trade Shipment — fruit leaving, and the paper leaving with it.

THE STATUS WALK IS ENFORCED HERE AND THE DOCUMENT GATE IS NOT. That split is
deliberate and is the design decision this module turns on.

The walk — Draft → Documents Pending → Ready to Ship → In Transit → Delivered —
is enforced in this controller because it is a fact about the world. A shipment
cannot be delivered before it has departed, whatever anybody ticks, and a rule
about the order of real events belongs where every writer passes.

The document gate is NOT enforced here. Whether an incomplete checklist blocks a
release is a POLICY, it is per-site and per-shipment, and overriding it has to
leave a reason behind — none of which a `validate` can do, because a validate
cannot ask the caller why. So `update_shipment_status` owns that check, and this
controller owns the physics. A gate in `validate` would also mean an operator
could not fix a mis-keyed shipment in the Desk without first satisfying a
customs requirement, which is a rule that would be turned off within a week.

WHAT THE CHECKLIST IS FOR. `documents` is what this destination asks for. It is
built once when the shipment is created and is not silently rebuilt afterwards:
a destination's rules changing in March must not quietly add a requirement to a
February shipment that has already sailed. `get_shipment_readiness` reports the
drift instead, which is the honest half of that trade.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from ..destination_document_requirement.destination_document_requirement import normalise_country
from ..trade_document_template.trade_document_template import INTERNATIONAL, normalise_tier

DRAFT = "Draft"
DOCUMENTS_PENDING = "Documents Pending"
READY_TO_SHIP = "Ready to Ship"
IN_TRANSIT = "In Transit"
DELIVERED = "Delivered"
CANCELLED = "Cancelled"

STATUSES = (DRAFT, DOCUMENTS_PENDING, READY_TO_SHIP, IN_TRANSIT, DELIVERED, CANCELLED)

#: The states from which a shipment has not yet left. `Ready to Ship` is here:
#: released is not departed, and a shipment sitting on the dock with its papers
#: in order is one somebody can still stop.
OPEN_STATES = (DRAFT, DOCUMENTS_PENDING, READY_TO_SHIP)

#: Terminal. Nothing leaves either of these.
TERMINAL_STATES = (DELIVERED, CANCELLED)

#: What each status may become. Cancellation is reachable from every open state
#: and from In Transit — a load really can be turned around — and from nowhere
#: after delivery, because a delivered shipment did happen.
TRANSITIONS = {
	DRAFT: (DOCUMENTS_PENDING, READY_TO_SHIP, CANCELLED),
	DOCUMENTS_PENDING: (DRAFT, READY_TO_SHIP, CANCELLED),
	READY_TO_SHIP: (DOCUMENTS_PENDING, IN_TRANSIT, CANCELLED),
	IN_TRANSIT: (DELIVERED, CANCELLED),
	DELIVERED: (),
	CANCELLED: (),
}

#: The transition the document gate guards. Named once so the tool and this
#: controller cannot come to disagree about which step is the controlled one.
GATED_TRANSITION = READY_TO_SHIP

SITE_DEFAULT = "Site Default"
ADVISORY = "Advisory"
ENFORCED = "Enforced"
ENFORCEMENT_MODES = (SITE_DEFAULT, ADVISORY, ENFORCED)


def normalise_shipment_status(value, required: bool = True) -> str:
	"""One of `STATUSES`, or a refusal naming them all."""
	raw = str(value or "").strip()
	if not raw:
		if required:
			frappe.throw(_("A shipment status is required — one of {0}.").format(", ".join(STATUSES)))
		return ""
	for status in STATUSES:
		if status.casefold() == raw.casefold():
			return status
	frappe.throw(
		_("{0} is not a shipment status. It is one of {1}.").format(
			frappe.utils.cstr(value), ", ".join(STATUSES)
		)
	)


class TradeShipment(Document):
	def validate(self):
		self.status = normalise_shipment_status(self.status or DRAFT)
		self.destination_tier = normalise_tier(self.destination_tier)
		self.destination_country = normalise_country(self.destination_country)

		if self.destination_tier == INTERNATIONAL and not self.destination_country:
			frappe.throw(
				_(
					"An International shipment has to say which country. The checklist is "
					"looked up by country, so one without a country gets only the rules that "
					"apply to every export — which is a shorter checklist that looks exactly "
					"like a complete one."
				)
			)
		if self.destination_tier != INTERNATIONAL and self.destination_country:
			frappe.throw(
				_(
					"This shipment is {0} but names the country {1}. Only International "
					"shipments carry a country; a {0} one naming a destination abroad is a "
					"tier somebody set wrong, and it would be documented as domestic freight."
				).format(self.destination_tier, self.destination_country)
			)

		if self.enforcement and self.enforcement not in ENFORCEMENT_MODES:
			frappe.throw(
				_("Enforcement is one of {0}.").format(", ".join(ENFORCEMENT_MODES))
			)
		if not self.enforcement:
			self.enforcement = SITE_DEFAULT

		self._check_transition()
		self._check_checklist()

	def _previous(self) -> dict:
		if self.is_new():
			return {}
		try:
			before = self.get_doc_before_save()
		except Exception:  # pragma: no cover
			before = None
		if before is not None:
			# Frappe hands back a Document; some callers and test doubles hand
			# back the plain dict it wraps. Both are the row before this save,
			# and a controller that only understood one of them would skip its
			# own transition check on whichever site produced the other.
			return dict(before.as_dict() if hasattr(before, "as_dict") else before)
		try:
			return dict(frappe.get_doc(self.doctype, self.name).as_dict())
		except Exception:  # pragma: no cover
			return {}

	def _check_transition(self) -> None:
		before = self._previous()
		if not before:
			if self.status in (IN_TRANSIT, DELIVERED):
				frappe.throw(
					_(
						"A shipment cannot be created already {0}. The point of the record is "
						"the paperwork raised before it leaves; one created after the fact has "
						"skipped the only part that was load-bearing. Create it as Draft and "
						"walk it forward — the timeline columns take the real dates."
					).format(self.status)
				)
			return
		was = str(before.get("status") or DRAFT)
		if was == self.status:
			return
		allowed = TRANSITIONS.get(was, ())
		if self.status not in allowed:
			frappe.throw(
				_(
					"{0} cannot go from {1} to {2}. From {1} it can go to {3}. These are real "
					"events in an order the world imposes — a shipment is not delivered before "
					"it has departed, whatever a status column says."
				).format(
					self.name,
					was,
					self.status,
					", ".join(allowed) if allowed else _("nowhere; it is a final state"),
				)
			)

	def _check_checklist(self) -> None:
		"""One template per checklist line, and every line naming a template.

		Enforced HERE rather than in the child controller because Frappe does not
		call a child row's `validate` when its parent is saved — the same fact the
		Wizard Definition controller sets out. A duplicated line is one document
		appearing twice on a readiness answer, with somebody satisfying whichever
		of the two they happened to open.
		"""
		seen = []
		for row in self.get("documents") or []:
			template = str(row.get("template") or "").strip()
			if not template:
				frappe.throw(
					_(
						"A checklist line has no template. The line IS the requirement, so one "
						"naming no document is a row nobody can satisfy and nobody can clear."
					)
				)
			if template in seen:
				frappe.throw(
					_(
						"{0} is on this checklist twice. Two lines for one document mean it "
						"shows up twice on every readiness answer, and somebody satisfies "
						"whichever of them they open first."
					).format(template)
				)
			seen.append(template)
			if row.get("sequence") in (None, ""):
				row.sequence = 50
