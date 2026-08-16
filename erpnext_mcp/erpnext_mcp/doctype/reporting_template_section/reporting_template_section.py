# SPDX-License-Identifier: MIT
"""Controller for Reporting Template Section — a row, and deliberately no rules.

Every check that matters about a section is a check about the SET of them —
duplicate names, ordering, how many are required — and a set is not visible from
inside one row. They live on the parent's `validate`, which is the only place
that can see them.
"""

from frappe.model.document import Document


class ReportingTemplateSection(Document):
	pass
