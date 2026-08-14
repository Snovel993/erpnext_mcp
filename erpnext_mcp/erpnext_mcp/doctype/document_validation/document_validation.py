# SPDX-License-Identifier: MIT
"""Controller for Document Validation — the naming, and the one field a caller
may not fill in for somebody else.

WHAT THIS CONTROLLER OWNS THAT THE TOOL LAYER DOES NOT. `human_confirmed` is
the only column on this record that is not a machine's reading of a photograph,
and it is therefore the only one an audit actually asks about. So the two
columns beside it — who confirmed, and when — are stamped HERE rather than only
in a tool, for the same reason `ML Model`'s uniqueness invariant lives in its
controller: the guarantee has to hold whichever door the save came through, and
this record can be edited in the Desk by anybody who may write it.

The rule has two halves and the second is the one people forget. Ticking the
box stamps the user and the time. UNticking it CLEARS them — a confirmation
time with nobody's name against it is a half-retracted record, and it reads to
the next person like somebody confirmed this and we lost track of who.

VALIDATION STATUS IS NOT RECOMPUTED HERE, AND THAT IS DELIBERATE. The status
on this record is the outcome of a run against a particular extraction with a
particular assessment; recomputing it on every save would mean an operator
correcting a typo in `source_name` silently changed what the document was
found to be. `revalidate_document` is the door that re-decides a status, it
stamps `last_revalidated` when it does, and `revalidation_count` is how a
reader tells a fresh answer from a stale one.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import document_intel, shifts

DOCTYPE = "Document Validation"


class DocumentValidation(Document):
	def autoname(self):
		"""DVAL-YYYY-0001, where YYYY is the year the document was validated."""
		year = str(frappe.utils.today())[:4]
		self.name = shifts.next_in_series(DOCTYPE, "DVAL", year)

	def validate(self):
		self._check_vocabulary()
		self._clamp_confidence()
		self._stamp_confirmation()

	def _check_vocabulary(self) -> None:
		"""The two Selects, checked here as well as at the tool layer.

		Frappe does not enforce Select options on a programmatic save, so a
		status of "validated" in the wrong case would land in the column and
		then never match a filter. Refusing it is cheaper than the afternoon
		somebody spends finding out why a list is short.
		"""
		if self.document_type and self.document_type not in document_intel.DOCUMENT_TYPES:
			frappe.throw(
				_("{0} is not a document type. It is one of: {1}. Nothing was saved.").format(
					self.document_type, ", ".join(document_intel.DOCUMENT_TYPES)
				)
			)
		if self.validation_status and self.validation_status not in document_intel.VALIDATION_STATUSES:
			frappe.throw(
				_("{0} is not a validation status. It is one of: {1}. Nothing was saved.").format(
					self.validation_status, ", ".join(document_intel.VALIDATION_STATUSES)
				)
			)

	def _clamp_confidence(self) -> None:
		"""`overall_confidence` is a probability and lives in 0–1.

		Clamped rather than refused: the figure is derived, a caller has no
		business setting it by hand, and taking down a save over a rounding
		artefact at the fourth decimal would be a refusal nobody learns
		anything from.
		"""
		if self.overall_confidence in (None, ""):
			return
		self.overall_confidence = max(0.0, min(1.0, float(self.overall_confidence)))

	def _stamp_confirmation(self) -> None:
		"""Who confirmed, and when — see the module docstring on both halves."""
		if self.human_confirmed:
			if not self.human_confirmed_by:
				self.human_confirmed_by = frappe.session.user
			if not self.human_confirmed_at:
				self.human_confirmed_at = frappe.utils.now()
			return
		self.human_confirmed_by = None
		self.human_confirmed_at = None
