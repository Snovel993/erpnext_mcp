# SPDX-License-Identifier: MIT
"""Controller for Training Type — one curriculum, and the audits it answers.

WHY THIS EXISTS WHEN v0.19.0 ARGUED FOR FREE TEXT. It argued against a SELECT,
and that argument still stands: a closed list would have to be edited every time
a regulator renames a curriculum, and an operator faced with a list that does not
contain the course somebody actually attended will pick the nearest wrong one.
This is not a Select. It is a master anybody can add a row to, and
`training.ensure_type` creates one from free text the first time somebody records
a course this site has not run before — so nothing has to be configured before a
training can be filed, which was the whole of free text's advantage.

What it buys is the thing free text could not. "WPS Handler Training satisfies
WPS" is a fact about the CURRICULUM, not about one afternoon in a shed. Under
free text it had to be restated on every record, which made thirty records of one
course thirty chances to type `OSHA` where the vocabulary says `OR-OSHA` — and
that near-miss files evidence where no packet looks for it, which nobody finds
out about until an inspector does.

THE TYPE'S REGIMES ARE A DEFAULT, NOT A CONSTRAINT, and that distinction is the
one thing here worth defending. `record_training` still writes regimes onto each
record. The type says what the course NORMALLY answers; the record says what THAT
afternoon actually covered. A heat-illness session that ran out of time before
the emergency-response topic did not satisfy OAR 437-004-1131 that day, and a
system that inherited the tag from the curriculum would have produced a record
claiming it did. The curriculum is what was scheduled; the record is what
happened, and only one of those is evidence.

RETENTION IS DERIVED FROM THE REGIMES, and the longest governs — same doctrine as
`training.retention_years`, for the same reason: a type tagged GAP and NOP whose
retention said two years would be a standing instruction to destroy the NOP
evidence three years early.

RENAMING IS ALLOWED AND DELETING IS NOT. A curriculum renamed by its regulator is
still the same curriculum and Frappe carries the link across the rename; a
curriculum deleted out from under thirty training records leaves thirty records
pointing at nothing. `active` is the honest way to retire one.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from erpnext_mcp import training


class TrainingType(Document):
	def validate(self):
		self.training_type_name = " ".join(str(self.training_type_name or "").split()).strip()
		if not self.training_type_name:
			frappe.throw(
				_(
					"Training Type is required — it is what the auditor reads first, and "
					"'training' is not an answer to 'trained in what'."
				)
			)
		found = training.from_rows(self.get("regimes"))
		if found:
			# Rewritten through the vocabulary so the rows come back deduplicated and
			# in REGIMES order, which is what makes two types with the same tags
			# compare equal and read consistently in a packet.
			training.set_rows(self, "regimes", found)
		self.retention_years = self._retention(found)

	def _retention(self, found: list) -> int:
		"""The longest window any tag demands, unless somebody set a longer one.

		A HAND-SET LONGER FIGURE IS KEPT. An operation whose own document-retention
		policy is seven years is not wrong, and overwriting it every save with the
		regulator's floor would be this app quietly shortening a retention period —
		which is the one direction it must never be wrong in. A SHORTER one is
		raised to the floor, because that is the direction that destroys evidence.
		"""
		floor = training.retention_years(found)
		try:
			stated = int(self.retention_years or 0)
		except (TypeError, ValueError):
			stated = 0
		return max(stated, floor)

	def on_trash(self):
		count = 0
		try:
			count = int(frappe.db.count(training.DOCTYPE, {"training_type": self.name}) or 0)
		except Exception:  # pragma: no cover - a site mid-migration
			return
		if not count:
			return
		frappe.throw(
			_(
				"{0} training record(s) name {1}. Deleting the curriculum would leave every one of "
				"them pointing at nothing, and a training log an auditor cannot resolve to a course "
				"is a log about an afternoon nobody can identify. Untick Active instead — the "
				"records keep their history and nobody is offered the course again."
			).format(count, self.name),
			title=_("Training Type Is In Use"),
		)
