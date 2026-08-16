# SPDX-License-Identifier: MIT
"""Controller for Disclosure Checklist Item — a row, and no rules of its own.

Every rule about an item is a rule about the LIST: duplicate names, how many
required ones are outstanding, whether reopening one should clear a completion.
None of those is visible from inside a single row, so all of them live on the
parent's `validate`.
"""

from frappe.model.document import Document


class DisclosureChecklistItem(Document):
	pass
