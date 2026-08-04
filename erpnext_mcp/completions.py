# SPDX-License-Identifier: MIT
"""What makes two completion submissions THE SAME SUBMISSION rather than two.

v0.20.1. THIS MODULE EXISTS BECAUSE OF ONE EVENING ON ONE iPAD. A worker finished
their tasks with the wifi off, the queue drained when the handset found signal,
the server accepted every completion — and the acknowledgement did not survive
the trip back. The app did the only correct thing available to it and re-sent,
and the server answered `already Completed` as a hard error. Three Failed entries
per task, on work that was filed, recorded and evidenced the first time.

The refusal was not wrong as a data-integrity rule; it was wrong as an API
contract. A CLIENT CANNOT KNOW WHETHER ITS REQUEST LANDED. That is not a bug in
the client and no amount of retry logic on the phone fixes it — the only place
the question can be answered is the server, and it can only be answered by the
server being able to recognise a request it has already served.

────────────────────────────────────────────────────────────────────────────
WHAT GOES INTO THE HASH, AND WHY EACH ONE
────────────────────────────────────────────────────────────────────────────

Five components, hashed as one sha256 over a unit-separated string:

  * THE ASSIGNMENT DOCNAME. The completion is of one assignment. Two assignments
    are never the same submission even where everything else matches.
  * THE WORKER. The Employee whose completion this is — `assigned_to`, not the
    login. A completion carries the completing worker's identity as evidence, so
    two different people filing the same words is a conflict and not a retry.
    The same person on a second handset IS a retry, which is the reason this is
    keyed on the Employee rather than on a device id or a session: a worker who
    dropped their phone in an orchard and finished the queue on a spare is doing
    exactly one completion, and the record should say so.
  * THE EVIDENCE, as FILE REFERENCES, sorted. See below.
  * WHAT THEY WROTE — `findings_text` and `completion_narrative`, whitespace
    trimmed at the ends. A change to either is a different account of the same
    work and must not be silently discarded by an idempotent success.
  * THE CLOCK-OUT TIME AS SENT, or empty where the client did not send one. The
    server fills a missing `completed_at` with `now()`, and `now()` is different
    on every retry — hashing the SERVER's answer would make every resubmission a
    conflict, which is the exact failure this module exists to prevent.

WHAT IS DELIBERATELY NOT IN IT. The evidence type and caption: a caption is
cosmetic metadata about a file whose identity is already in the hash, and a
client that re-serialised `"photo"` as `"Photo"` between attempts would be told
its retry was a conflict. `farm_location_gps`: the second attempt may carry a
fix the first one did not have, because the worker walked out of the shed — and
that is the same completion with a better location, not a different one.
`clean_pass`: it is derived from, and must agree with, `findings_text`, which is
hashed. `visit_id`: it groups completions, it does not identify one.

────────────────────────────────────────────────────────────────────────────
FILE DOCNAMES AND NOT CONTENT HASHES, AND THE REASON IS NOT CONVENIENCE
────────────────────────────────────────────────────────────────────────────

The obvious alternative is to hash the evidence bytes. It is the wrong choice
here, twice over.

A File docname in this app is minted by exactly one thing —
`api/files.finalize_staged_file` — and that function verifies the client's
declared sha256 against the assembled bytes before it mints anything. So the
docname is not a weaker identifier than the content hash; it is a token that
already had the content hash checked against it, by the one code path that can
produce one.

And a content hash CANNOT BE BACKFILLED. Farm Task Assignment's evidence child
table records `file` and `file_url`; it has no hash column — RELEASES/v0.17.1.md
names that as an open follow-up and it is still open. A signature scheme whose
backfill could not run on the rows that already exist would leave every
pre-v0.20.1 completion permanently unmatchable, which is most of the rows that
an iPad in the field is going to re-submit.

────────────────────────────────────────────────────────────────────────────
TWO SCHEMES, AND THE SECOND ONE IS HONEST ABOUT WHAT IT CANNOT KNOW
────────────────────────────────────────────────────────────────────────────

`v1:` is written at completion time, from the payload as it arrived. It includes
the clock-out time exactly as the client sent it, including the fact that the
client sent nothing.

`v1b:` is written by the backfill patch onto rows completed before this release.
Those rows carry a `completed_at`, but NOTHING ON THE ROW SAYS WHETHER THE
CLIENT CHOSE IT OR THE SERVER DID. A backfill that guessed would produce false
conflicts on real retries — the failure mode this whole release is about — so it
does not guess: `v1b` leaves the clock-out time out of the hash, and a stored
`v1b` signature is compared against an incoming `v1b` signature. Everything else
is still compared, so a different worker, different findings or different
evidence on a legacy row is still the conflict it should be.

The scheme is a PREFIX on the stored value rather than a second column because
it is a property of that one signature, and a row's scheme never changes after
it is written.
"""

from __future__ import annotations

import hashlib

#: Written at completion time, from the submitted payload. Includes the
#: clock-out time as the client sent it.
SCHEME = "v1"

#: Written by the backfill onto rows that predate this release. Identical except
#: that the clock-out time is not hashed — see the module docstring.
BACKFILL_SCHEME = "v1b"

#: Between components, and between evidence references within the one component.
#: ASCII 31 and 30, which no docname, findings text or timestamp contains — a
#: separator that can appear in a component is a separator that lets two
#: different submissions hash the same.
_FIELD = "\x1f"
_ITEM = "\x1e"


def evidence_references(rows) -> list:
	"""The evidence, as the sorted file references that identify it.

	SORTED, because the order a phone drained its upload queue in is not part of
	what was submitted — the same four photographs in a different order are the
	same four photographs.

	Takes both shapes it will meet: the normalised rows `complete_farm_task`
	appends (`{"file": ..., "file_url": ..., "evidence_type": ...}`) and the
	child rows read back off a saved assignment, which are the same keys. A bare
	string is accepted so the backfill and the tests can pass docnames directly.
	"""
	out = []
	for row in rows or ():
		if isinstance(row, str):
			reference = row.strip()
		elif isinstance(row, dict):
			reference = str(row.get("file") or "").strip() or str(row.get("file_url") or "").strip()
		else:
			reference = (
				str(getattr(row, "file", "") or "").strip() or str(getattr(row, "file_url", "") or "").strip()
			)
		if reference:
			out.append(reference)
	return sorted(out)


def _digest(assignment, worker, evidence, findings, narrative, completed_at) -> str:
	payload = _FIELD.join(
		(
			str(assignment or "").strip(),
			str(worker or "").strip(),
			_ITEM.join(evidence_references(evidence)),
			str(findings or "").strip(),
			str(narrative or "").strip(),
			str(completed_at or "").strip(),
		)
	)
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def signature(assignment, worker, evidence, findings, narrative, completed_at="") -> str:
	"""The `v1` signature of one submitted completion.

	`completed_at` is THE CLIENT'S, or empty. Pass what arrived in the request,
	not what the record ended up with — see the module docstring.
	"""
	return f"{SCHEME}:{_digest(assignment, worker, evidence, findings, narrative, completed_at)}"


def backfill_signature(assignment, worker, evidence, findings, narrative) -> str:
	"""The `v1b` signature of a completion that was filed before this release."""
	return f"{BACKFILL_SCHEME}:{_digest(assignment, worker, evidence, findings, narrative, '')}"


def matches(stored, assignment, worker, evidence, findings, narrative, completed_at="") -> bool:
	"""Is this submission the one the stored signature was written for?

	The stored value picks the scheme it is compared under, which is the whole
	point of the prefix: a `v1b` row is a row where nobody can say whether the
	stored clock-out time came from a client or from the server, so it is
	compared without one. An unrecognised or empty stored value matches nothing —
	a signature that cannot be read is not a signature that agrees.
	"""
	stored = str(stored or "").strip()
	if not stored:
		return False
	if stored.startswith(f"{BACKFILL_SCHEME}:"):
		return stored == backfill_signature(assignment, worker, evidence, findings, narrative)
	if stored.startswith(f"{SCHEME}:"):
		return stored == signature(assignment, worker, evidence, findings, narrative, completed_at)
	return False
