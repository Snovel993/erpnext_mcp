# SPDX-License-Identifier: MIT
"""Controller for Staged File Upload Session — the accounting for one chunked upload.

WHY CHUNKED UPLOADS EXIST AT ALL. Two ceilings sit between an AI operator and a
5 MB appraisal PDF, and only one of them is this app's. `attach_file_to_document`
accepts up to 8 MB of base64 in a call, which is generous — but a model cannot
COMPOSE 8 MB of base64, because the string itself has to fit in the tool call it
is writing, and that runs out at a couple of hundred kilobytes. So through all
of Sprint 5 and 6 every file-bearing operation on this project turned into the
same ritual: Claude writes a Python script, Tim scp's it to the box, docker cp's
it into the container, docker exec's it. Four manual steps to move bytes that
were already sitting in the conversation. Tim's ask on 2026-07-30 was exactly one
sentence long: "So we don't have to run these scripts."

WHY THE PIECES ARE IN A TABLE AND NOT IN THE CACHE. A redis key is the obvious
place to stage half a file and it is the wrong one. Uploading 5 MB is a hundred
round trips over some minutes; a worker restart, a redis eviction under memory
pressure or a `bench restart` in the middle throws all of it away, and the caller
finds out at commit. Rows in a table survive every one of those. That is the
whole design decision, and it is why "restart the bench halfway through and
finish the upload" is a test.

A SESSION OWNS NOTHING PERMANENT. It is deleted on commit and on cancel, and
swept when it has been idle for a day — see `uploads.collect_expired_sessions`.
Nothing here is evidence of anything. The File that comes out the other end is
the record; this is the scaffolding, and scaffolding that outlives the building
is just rubbish on the site.

WHAT THIS CONTROLLER ENFORCES, AND WHAT IT DELIBERATELY DOES NOT. It enforces the
things that are true of a session in isolation: an id, a sane chunk count,
counters that cannot go negative, a hash that looks like a hash. It does NOT
enforce whether the pieces add up, who may commit it, or whether the file is too
big — those are facts about the chunk rows and the calling user, and they live in
`tools/uploads.py` where the whole picture is in view.
"""

import re

import frappe
from frappe import _
from frappe.model.document import Document

#: Ceiling on the declared piece count. 600 pieces of 200 KB of base64 is roughly
#: 90 MB of file, which is past the point where the Desk's own upload control or
#: a `file_url` is the better tool. Named here as well as in `tools/uploads.py`
#: because a session saved by hand in the Desk has to obey the same rule.
MAX_TOTAL_CHUNKS = 600

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StagedFileUploadSession(Document):
	def validate(self):
		self.session_id = str(self.session_id or "").strip()
		if not self.session_id:
			frappe.throw(_("Session Id is required — an upload nobody can name cannot be resumed."))

		total = int(self.total_chunks or 0)
		if total < 1:
			frappe.throw(_("Total Chunks must be at least 1."))
		if total > MAX_TOTAL_CHUNKS:
			frappe.throw(
				_(
					"Total Chunks is {0}, over the ceiling of {1}. A file that needs more pieces "
					"than that belongs in the Desk's own upload control, or on a URL this site "
					"can fetch."
				).format(total, MAX_TOTAL_CHUNKS)
			)

		self.chunks_received = max(0, int(self.chunks_received or 0))
		self.staged_bytes = max(0, int(self.staged_bytes or 0))

		# A hash that is not a hash is worse than no hash: it will fail on commit
		# with an integrity message, and somebody will go looking for corruption
		# in the file rather than in the argument.
		self.expected_sha256 = str(self.expected_sha256 or "").strip().lower()
		if self.expected_sha256 and not _SHA256.match(self.expected_sha256):
			frappe.throw(
				_(
					"Expected Sha256 must be 64 hexadecimal characters — that is what a SHA-256 "
					"digest is. Got {0} character(s)."
				).format(len(self.expected_sha256))
			)

		if int(self.expected_size or 0) < 0:
			frappe.throw(_("Expected Size cannot be negative."))

	def before_save(self):
		self.last_activity = frappe.utils.now()
