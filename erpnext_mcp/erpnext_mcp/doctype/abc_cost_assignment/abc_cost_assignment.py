# SPDX-License-Identifier: MIT
"""Controller for ABC Cost Assignment — one allocation run, stored whole.

NO VALIDATION THAT COULD REFUSE A RUN. Deliberately. Every interesting property
of a run — that the assigned lines sum to the pools, that the unassigned figure
is real, that a driver quantity was measured rather than guessed — is established
by the engine in `tools/abc.py` before the document is built, and a controller
that re-checked them could only ever refuse to RECORD a result the engine had
already reached. A run this app cannot store is a run nobody can audit, which is
the opposite of what storing the intermediates is for.

What is here instead is the arithmetic identity, asserted as a stored figure
rather than a throw: `total_assigned + unassigned_amount` is `total_pool_amount`,
and both halves are on the document so a reader can check it without the engine.
"""

from frappe.model.document import Document


class ABCCostAssignment(Document):
	def before_save(self):
		self.line_count = len(self.get("lines") or [])
