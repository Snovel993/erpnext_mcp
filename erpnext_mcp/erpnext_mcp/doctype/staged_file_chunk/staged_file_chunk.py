# SPDX-License-Identifier: MIT
"""Controller for Staged File Chunk — one piece of a file in flight.

WHY THIS IS NOT A CHILD TABLE, which is the design note worth reading. The
obvious shape for "many pieces belonging to one upload" is a child table on the
session, and the spec this was built from asked for exactly that. It does not
work, for a reason that only shows up at the far end of a big upload: Frappe
rewrites a document's ENTIRE child table on every save. Appending piece 600 to a
child table means writing 600 rows of 200 KB — 120 MB of INSERT — to record 200
KB of new data, and doing that for every piece makes a large upload quadratic in
its own size. It would work fine for the 25-chunk test and fall over on the real
5 MB PDF it was built for.

A separate doctype with a Link back at the session costs one row per piece, one
write per call, and lets `frappe.db.get_all` count pieces, sum bytes and find
gaps without ever loading a payload into memory. The parent/child relationship
the spec wanted is still there — it is just expressed as a Link, which is what
Frappe uses everywhere else that a "child" outlives a single form submission.

THE PAYLOAD STAYS AS BASE64. Decoding on the way in and re-encoding on the way
out would cost two conversions per piece to save a third of the storage on rows
that are deleted within the hour. It also keeps the stored bytes identical to the
bytes the caller sent, which is what makes a per-piece hash worth having.

EVERY PIECE CARRIES ITS OWN SHA-256. Not for security — the transport already had
a bearer token — but for diagnosis. A file whose aggregate hash does not match on
commit is a mystery; a file whose aggregate hash does not match AND whose piece
17 hashes differently from what the caller recorded is a fixed piece 17.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class StagedFileChunk(Document):
	def validate(self):
		if int(self.chunk_index or 0) < 0:
			frappe.throw(_("Chunk Index counts from 0 and cannot be negative."))
		if not str(self.chunk_base64 or "").strip():
			frappe.throw(_("Chunk Base64 is empty — there is no piece here to stage."))
		self.chunk_bytes = max(0, int(self.chunk_bytes or 0))
