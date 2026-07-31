# SPDX-License-Identifier: MIT
"""Controller for Compliance Policy — one written procedure at one version.

WHY A POLICY IS AN EXTERNAL-EVIDENCE DOCTYPE AND A SPRAY RECORD IS NOT. This
app's Sprint 7 stance is that compliance is a lens on operational data rather
than a duplicate set of records: every spray IS a Worker Protection Standard
record, so the applicator's name belongs on the spray, not in a shadow log
beside it. A written procedure has no operational act to hang off. Nobody writes
a harvest hygiene SOP by harvesting. It arrives from outside the operation — a
buyer's requirement, a certifier's template, a lawyer — and the record of it is
the only record there is. That is the whole test for what belongs in this group
of four doctypes, and it is why the group is small.

THE VERSION IS A FIELD AND NOT PART OF THE NAME. A policy at v3 is the same
policy it was at v1: the same Audit Event findings cite it, the same audit packets
should keep resolving to it. Putting the version in the docname would make every
revision a different record and quietly break every pointer at the old one. So the
docname is the policy's name, `version` says which revision the attached document
is, and `supersedes` / `superseded_by` chain the revisions that were significant
enough to keep separately.

THE CHAIN IS WRITTEN AT BOTH ENDS OR NOT AT ALL. `validate` refuses a policy that
supersedes itself and a policy whose two chain links point at the same record.
Everything else about the chain is enforced by `supersede_compliance_policy`,
which writes both directions in one act — a half-written chain is worse than none,
because a reader following it from one end concludes something different from a
reader following it from the other.

A REVIEW DATE BEFORE THE EFFECTIVE DATE IS REFUSED, because a procedure that was
overdue for review before it took effect is a typo every time, and the alert
engine would fire a Warning on it the first night.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class CompliancePolicy(Document):
	def validate(self):
		self.policy_name = str(self.policy_name or "").strip()
		if not self.policy_name:
			frappe.throw(_("Policy Name is required — it is the docname, and it is what an audit cites."))

		if self.supersedes and self.supersedes == self.name:
			frappe.throw(
				_("{0} cannot supersede itself.").format(self.name),
				title=_("Circular Supersession"),
			)
		if self.superseded_by and self.superseded_by == self.name:
			frappe.throw(
				_("{0} cannot be superseded by itself.").format(self.name),
				title=_("Circular Supersession"),
			)
		if self.supersedes and self.supersedes == self.superseded_by:
			frappe.throw(
				_(
					"{0} both supersedes and is superseded by {1}. One of those is wrong, and "
					"a reader following the chain from each end would reach opposite conclusions."
				).format(self.name or self.policy_name, self.supersedes),
				title=_("Contradictory Version Chain"),
			)

		if self.effective_date and self.review_due_date and self.review_due_date < self.effective_date:
			frappe.throw(
				_(
					"Review Due {0} is before the Effective Date {1} — the procedure would have "
					"been overdue for review before it took effect."
				).format(self.review_due_date, self.effective_date),
				title=_("Review Before Effect"),
			)
