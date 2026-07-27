# SPDX-License-Identifier: MIT
"""Controller for Governance Document — the archive that has to outlive us.

THE CHAIN IS THE POINT. An operating agreement amended three times is four
documents, and the question somebody asks in 2050 is "which one was in force in
2031". That is answerable only if every amendment names what it replaces and
nothing in the chain loops. `validate` refuses a document that supersedes itself
and refuses a cycle, walking the chain rather than checking only one hop —
a two-document loop is just as unreadable as a one-document one.

`superseded_by` is written by whichever document names this one, not by hand.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: How far back the cycle check walks before giving up. A governance chain
#: hundreds long is not a chain, it is a bug, and either way refusing is right.
MAX_CHAIN = 200


class GovernanceDocument(Document):
	def validate(self):
		if self.supersedes and self.supersedes == self.name:
			frappe.throw(_("A Governance Document cannot supersede itself."))
		self._refuse_cycle()

	def _refuse_cycle(self):
		seen = {self.name} if self.name else set()
		current = self.supersedes
		for _hop in range(MAX_CHAIN):
			if not current:
				return
			if current in seen:
				frappe.throw(
					_(
						"This would make the amendment chain loop back on itself at {0}. "
						"Supersedes has to point backwards in time."
					).format(current),
					title=_("Circular Amendment Chain"),
				)
			seen.add(current)
			current = frappe.db.get_value("Governance Document", current, "supersedes")
		frappe.throw(_("Amendment chain is longer than {0} documents; refusing.").format(MAX_CHAIN))
