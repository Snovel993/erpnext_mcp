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

`related_to` ANSWERS "OF WHOM", AND IT IS DATA RATHER THAN A LINK ON PURPOSE.
v0.12.2 shipped a register that could say "Alexander Polehn — Child" and could
not say whose child, which is ambiguous the moment an entity has two members —
and Orchard Meadow has two. The obvious fix is a Link, and it does not fit: a
Frappe Link points at exactly one doctype, the answer is a Family record OR a
Related Party record OR somebody in neither register, and a Dynamic Link would
buy the first two at the cost of a discriminator column beside it. So the field
holds a name and `parties._resolve_related_to` decides at read time which
register that name is in, reporting `related_to_doctype` as `Family`, `Related
Party` or None. A name in neither is not an error: a grandmother who has never
received a transfer and holds no role is exactly the person a free-text fallback
is for.

WHAT IT REFUSES IS ONLY THE SELF-REFERENCE. Somebody related to themselves is a
cycle of length one and the tree walk would have to special-case it. Longer
cycles are caught by the walk itself rather than here, because seeing one
requires reading records this document has no business loading during validate.
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

		self.related_to = str(self.related_to or "").strip()
		if self.related_to and self.related_to.lower() == self.family_member_name.lower():
			frappe.throw(
				_(
					"{0} cannot be related to themselves. Related To names the OTHER person — "
					"the parent, the grandparent, the member this one hangs off."
				).format(self.family_member_name),
				title=_("Circular Relationship"),
			)
