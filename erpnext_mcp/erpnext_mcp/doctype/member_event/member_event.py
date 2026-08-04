# SPDX-License-Identifier: MIT
"""Controller for Member Event — the equity trail.

WHAT IS ENFORCED HERE RATHER THAN IN THE TOOL. The tool that writes these
(`record_member_event`) checks a great deal more, but a Member Event can also be
created by hand in the Desk, and three rules have to hold either way: a transfer
needs somebody to transfer to, an event cannot supersede itself, and an event
cannot be filed against a member of another company. Each of those produces a
trail that reads as complete and is not.

Amounts are never negative. A distribution is not a negative contribution — it
is a different event type with a different pair of accounts — and allowing the
sign to carry the meaning is how a trail ends up with two ways to say the same
thing and no way to total either.
"""

import frappe
from frappe import _
from frappe.model.document import Document

#: Event types that move an interest between two members and so need a second one.
PAIRED_EVENTS = ("Transfer", "Reallocation")


class MemberEvent(Document):
	def validate(self):
		if float(self.amount or 0) < 0:
			frappe.throw(
				_(
					"Amount cannot be negative. A distribution is its own event type, "
					"not a contribution with a minus sign."
				)
			)

		if self.event_type in PAIRED_EVENTS and not self.counterparty_member:
			frappe.throw(
				_("A {0} needs a Counterparty Member — the member the interest moves to.").format(
					self.event_type
				)
			)

		if self.counterparty_member and self.counterparty_member == self.member:
			frappe.throw(_("Counterparty Member cannot be the same member."))

		if self.superseded_by and self.superseded_by == self.name:
			frappe.throw(_("A Member Event cannot supersede itself."))

		for fieldname in ("member", "counterparty_member"):
			member = self.get(fieldname)
			if not member:
				continue
			company = frappe.db.get_value("Cap Table Entry", member, "company")
			if company and company != self.company:
				frappe.throw(
					_("Cap Table Entry {0} belongs to {1}, not {2}.").format(member, company, self.company)
				)
