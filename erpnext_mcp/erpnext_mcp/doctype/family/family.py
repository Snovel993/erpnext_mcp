# SPDX-License-Identifier: MIT
"""Controller for Family — the register a `Family` party posting points at.

WHY THIS DOCTYPE HAS TO EXIST, WHICH IS NOT OBVIOUS. ERPNext does not store a
posting's counterparty as free text. A Journal Entry line carries `party_type`,
which is a Link to **DocType**, and `party`, which is a **Dynamic Link** resolved
through it. So `party_type = "Family"` is only valid if there is a DocType called
`Family`, and `party = "Alex Bramwell"` is only valid if that is a record in it.
Customer, Supplier, Employee and Shareholder each have their own DocType for
exactly this reason; a relative had none, so v0.12.0's `Family` party type could
be registered on a site and then rejected by the first posting that used it.

v0.12.1 exists because it was rejected earlier than that — `bench migrate` refused
to insert the Party Type row itself, since `party_type` is a Link to DocType and
there was nothing for it to point at. That refusal was correct and it was the
cheapest possible place to find out.

NO TAX IDENTITY LIVES HERE, ON PURPOSE. A transfer below the IRS annual gift
exclusion is not compensation for services: it needs no W-9 and produces no 1099,
which is the whole reason this party type is separate from Supplier. Where a
relative also has a tax identity worth recording — because they are a member, a
lessor, a trustee — `related_party` points at the Related Party register, which
holds four digits and never more. A relative who is genuinely paid for work is
not a Family posting at all; that is a Contact or a Supplier, and the entry
should be reclassified rather than the exclusion widened.

NOTHING IS DELETED. `active` is a checkbox rather than a delete, because a person
who no longer receives transfers still appears on every posting that already
named them.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class Family(Document):
	def validate(self):
		self.family_member_name = str(self.family_member_name or "").strip()
		if not self.family_member_name:
			frappe.throw(_("Name is required — a party with no name cannot be posted to."))

		duplicate = frappe.db.get_value(
			"Family",
			{"family_member_name": self.family_member_name, "name": ("!=", self.name or "")},
			"name",
		)
		if duplicate:
			frappe.throw(
				_(
					"{0} is already on the family register. One record per person: the name is "
					"the docname, and it is what every posting to them points at."
				).format(duplicate),
				title=_("Duplicate Family Member"),
			)
