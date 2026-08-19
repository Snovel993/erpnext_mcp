# SPDX-License-Identifier: MIT
"""Controller for Trade Document — one piece of shipping paperwork, any kind.

THE STATUS WALK IS ONE WAY, AND THE ENFORCEMENT IS HERE RATHER THAN IN THE TOOL.
`approve_trade_document` and `seal_trade_document` are not the only paths to this
row — the Desk is another, and so is any later tool somebody writes — so a rule
that lived only in the tool layer would be a rule with a door beside it. The
transition table below is checked on every save, whatever wrote it.

A SEALED DOCUMENT IS CLOSED. That is the entire value of sealing: the hash says
"this is what we presented", and a hash over a row that anybody can still edit
says nothing at all. So a sealed document refuses content edits outright rather
than resealing quietly — the second behaviour would make the seal a timestamp
rather than a seal. Voiding is the escape hatch, and it is a state rather than a
deletion because a certificate that was presented and then withdrawn is a fact
about the shipment that the shipment's file should keep.

WHY `Void` EXISTS AT ALL. A shipment that falls through leaves approved and
sealed paperwork behind it, and the honest thing to do with a certificate for a
container that never sailed is to mark it withdrawn, not to delete it and not to
leave it looking live.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document

DRAFT = "Draft"
PENDING_REVIEW = "Pending Review"
APPROVED = "Approved"
SEALED = "Sealed"
VOID = "Void"

STATUSES = (DRAFT, PENDING_REVIEW, APPROVED, SEALED, VOID)

#: What each status may become. `Sealed` leads only to `Void`, and `Void` leads
#: nowhere: both are deliberate dead ends, and the reason is in the module
#: docstring.
TRANSITIONS = {
	DRAFT: (PENDING_REVIEW, APPROVED, VOID),
	PENDING_REVIEW: (DRAFT, APPROVED, VOID),
	APPROVED: (PENDING_REVIEW, SEALED, VOID),
	SEALED: (VOID,),
	VOID: (),
}

#: Statuses that count as "this document is done" for a readiness check. Sealed
#: is here as well as Approved because sealing is a step BEYOND approval, not an
#: alternative to it — a checklist that stopped counting a document once it was
#: sealed would report a fully-prepared shipment as incomplete.
SATISFYING = (APPROVED, SEALED)

#: The columns a seal covers. Named explicitly rather than "everything except a
#: deny-list", because a column added in a later version would silently join an
#: all-columns hash and make every previously sealed document fail verification.
#: An allow-list grows only when somebody means it to.
SEALED_FIELDS = (
	"title",
	"document_type",
	"shipment",
	"template",
	"company",
	"required",
	"document_data",
	"source_doctype",
	"source_name",
	"issued_on",
	"expires_on",
	"requires_external_filing",
	"external_system",
	"external_reference",
	"external_filed_on",
	"approved_by",
	"approved_on",
	"approval_notes",
)


def normalise_status(value, required: bool = True) -> str:
	"""One of `STATUSES`, or a refusal naming them all."""
	raw = str(value or "").strip()
	if not raw:
		if required:
			frappe.throw(_("A status is required — one of {0}.").format(", ".join(STATUSES)))
		return ""
	for status in STATUSES:
		if status.casefold() == raw.casefold():
			return status
	frappe.throw(
		_("{0} is not a trade document status. It is one of {1}.").format(
			frappe.utils.cstr(value), ", ".join(STATUSES)
		)
	)


def parse_data(raw) -> dict:
	"""`document_data` as a dict. Never raises; a blob that will not parse is {}.

	The callers that need a malformed blob to be an ERROR check it themselves
	before writing; the callers that are merely READING one — a checklist, a
	packet — want the rest of the document rather than an exception over a
	column somebody hand-edited in the Desk.
	"""
	if not raw:
		return {}
	try:
		parsed = json.loads(raw) if isinstance(raw, str) else raw
	except (json.JSONDecodeError, ValueError, TypeError):
		return {}
	return parsed if isinstance(parsed, dict) else {}


class TradeDocument(Document):
	def validate(self):
		self.status = normalise_status(self.status or DRAFT)
		if not str(self.title or "").strip():
			self.title = str(self.document_type or "").strip() or "Trade Document"

		if self.document_data:
			try:
				parsed = (
					json.loads(self.document_data)
					if isinstance(self.document_data, str)
					else self.document_data
				)
			except (json.JSONDecodeError, ValueError, TypeError):
				frappe.throw(
					_(
						"Document Data is not valid JSON. It is the whole content of this "
						"document, so a blob that will not parse is a certificate with nothing "
						"in it — and sealing one would fingerprint the emptiness."
					)
				)
				return
			if not isinstance(parsed, dict):
				frappe.throw(
					_("Document Data must be a JSON object of field → value, got {0}.").format(
						type(parsed).__name__
					)
				)

		self._check_transition()
		self._check_seal_is_closed()

	def _previous(self) -> dict:
		"""The row as it stands in the database, or {} when it is new."""
		if self.is_new():
			return {}
		try:
			before = self.get_doc_before_save()
		except Exception:  # pragma: no cover - a site without the hook loaded
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
			# A new document may be created in any status except Sealed, which is
			# a claim about a review that did not happen.
			if self.status == SEALED:
				frappe.throw(
					_(
						"A document cannot be created already sealed. The seal is a "
						"fingerprint of content somebody approved, and there is nothing yet "
						"to have approved. Create it, complete it, approve it, then seal it."
					)
				)
			return
		was = str(before.get("status") or DRAFT)
		if was == self.status:
			return
		allowed = TRANSITIONS.get(was, ())
		if self.status not in allowed:
			frappe.throw(
				_(
					"{0} cannot go from {1} to {2}. From {1} it can go to {3}. The walk is one "
					"way on purpose — a document that could return to Draft after approval is "
					"a document whose approval means nothing."
				).format(
					self.name,
					was,
					self.status,
					", ".join(allowed) if allowed else _("nowhere; it is a final state"),
				)
			)

	def _check_seal_is_closed(self) -> None:
		"""A sealed document's content does not change. That is what sealing IS."""
		before = self._previous()
		if not before or str(before.get("status") or "") != SEALED:
			return
		if self.status == VOID:
			# Withdrawing a sealed certificate is allowed and is the escape
			# hatch; what is refused is editing one and leaving it looking live.
			return
		changed = [
			field for field in SEALED_FIELDS if str(before.get(field) or "") != str(self.get(field) or "")
		]
		if changed:
			frappe.throw(
				_(
					"{0} is sealed, and {1} would change. A hash over a row anybody can still "
					"edit proves nothing, so a sealed document is closed: void it and issue a "
					"replacement, which is also what actually happens when a certificate is "
					"withdrawn and reissued."
				).format(self.name, ", ".join(changed))
			)
