# SPDX-License-Identifier: MIT
"""Controller for Task Note — one narrative entry, appended and never edited.

THE APPEND-ONLY PROMISE IS ENFORCED IN THE TOOLS AND NOT HERE, and the reason is
that a child row has no independent save: it is written when its parent is, so a
controller that refused an edit would refuse every parent save that touched any
other field. `tools/narrative.py` is the only door that adds entries and it only
ever appends; the Desk can technically edit a row, and an operator correcting a
typo in their own account is not the threat this record is guarding against.

What IS checked here is that an entry says something and says when. An entry
with a type and a timestamp and no words in it is a row that looks like a record.
"""

import frappe
from frappe import _
from frappe.model.document import Document

SOURCE_TYPED = "typed"
SOURCE_AUDIO = "audio_transcription"


class TaskNote(Document):
	def validate(self):
		if not str(self.narrative or "").strip():
			frappe.throw(
				_(
					"A narrative entry needs words in it. A row with a type and a timestamp and "
					"nothing else is something that looks like a record."
				)
			)
		if not self.written_at:
			self.written_at = frappe.utils.now()
		self.source_type = str(self.source_type or SOURCE_TYPED).strip() or SOURCE_TYPED
		if self.source_language:
			self.source_language = str(self.source_language).strip().lower()[:12]
		if self.source_type == SOURCE_AUDIO and not self.source_language:
			# NOT A REFUSAL. A transcription with no language is still the account
			# somebody gave, and refusing it would lose the words over a metadata
			# field. The gap is reported by the tool instead.
			pass
