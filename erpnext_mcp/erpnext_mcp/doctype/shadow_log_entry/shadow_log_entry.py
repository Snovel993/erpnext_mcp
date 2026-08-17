# SPDX-License-Identifier: MIT
"""Controller for Shadow Log Entry — the doctype whose whole job is not changing.

THE SNAPSHOT IS FROZEN AT INSERT AND THE CONTROLLER IS WHAT MAKES THAT TRUE.
Everything else in this app records the current state of something; this records
what was true at one moment, and the difference is the entire value of the row.
A supervisor who acknowledged a session of 412 buckets acknowledged 412. If a
recount later makes it 380, that is a SECOND fact — a second event, a second
copy, a second acknowledgement — and a feed that rewrote the first one would
destroy the only evidence of what the supervisor was actually shown.

So `data_snapshot` and `occurred_at` are refused on update, and `snapshot_hash`
is written once from the snapshot as inserted. The hash is not security — anyone
with the database can rewrite both — it is a CHECK: a row whose hash no longer
matches its snapshot was changed by something that went round this controller,
and that is a fact worth being able to establish.

ACKNOWLEDGEMENT IS ONE-WAY. "I saw this" is a statement about the past, and
un-seeing it is not an event that can happen. A supervisor who acknowledged
something they now dispute files the dispute against the source record, where it
belongs and where somebody will read it; letting them clear the tick instead
would leave no trace of either the seeing or the dispute.

THE LEVEL IS A DISTANCE, NOT A TITLE. 1 is the direct supervisor, 2 theirs, 3
the one above that. It is refused outside 1–3 because a level nobody can filter
on is a copy nobody reads, and because the chain walker that writes these stops
at three by construction — a fourth would mean something upstream is wrong.

THE SOURCE IS DATA AND NOT A LINK, which is a deliberate loosening of this app's
usual rule. The point of the copy is to survive the original: a Dynamic Link
would let a delete cascade into the backup, and a Link to DocType would break
the row if the source doctype were ever uninstalled. What is lost is referential
integrity on a column that is a POINTER TO HISTORY rather than a foreign key,
and `get_shadow_log_entry` says plainly when the source has gone.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document

MIN_LEVEL = 1
MAX_LEVEL = 3


class ShadowLogEntry(Document):
	def validate(self):
		if not str(self.shadow_key or "").strip():
			frappe.throw(
				_(
					"A Shadow Log Entry needs a shadow_key. It is derived from the event, the "
					"source record and the recipient by erpnext_mcp.tools.shadow_log, and it is "
					"what stops a resent batch putting a second copy of one morning in front of "
					"the same foreman."
				),
				title=_("No Shadow Key"),
			)

		level = frappe.utils.cint(self.shadow_level)
		if level < MIN_LEVEL or level > MAX_LEVEL:
			frappe.throw(
				_(
					"shadow_level is {0}. It is a DISTANCE up the chain of command and this app "
					"walks {1} — 1 the direct supervisor, 2 theirs, 3 the one above that. A level "
					"outside that range is a copy no reader filters for."
				).format(level, MAX_LEVEL),
				title=_("Level Out Of Range"),
			)

		if not str(self.summary or "").strip():
			frappe.throw(
				_(
					"A shadow copy with no summary is a row somebody has to read JSON to "
					"understand, which means nobody reads it."
				),
				title=_("No Summary"),
			)

		self._validate_snapshot()

		if not self.occurred_at:
			self.occurred_at = frappe.utils.now()

		if frappe.utils.cint(self.acknowledged) and not self.acknowledged_at:
			self.acknowledged_at = frappe.utils.now()

		self._refuse_rewrites()

	def _validate_snapshot(self) -> None:
		"""The snapshot must be a JSON object, and the hash is computed from it.

		Refused rather than coerced: a snapshot that is a bare string or a list is
		one whose writer had a different idea of the shape from every reader, and
		accepting it produces a feed where half the entries cannot be rendered.
		"""
		raw = self.data_snapshot
		if isinstance(raw, dict):
			raw = json.dumps(raw, indent=2, sort_keys=True, default=str)
			self.data_snapshot = raw
		text = str(raw or "").strip()
		if not text:
			frappe.throw(
				_("A shadow copy with no snapshot is a notification, not a backup."),
				title=_("No Snapshot"),
			)
		try:
			parsed = json.loads(text)
		except (ValueError, TypeError):
			frappe.throw(
				_("data_snapshot is not valid JSON. It is the frozen copy and it has to be readable."),
				title=_("Snapshot Is Not JSON"),
			)
			return
		if not isinstance(parsed, dict):
			frappe.throw(
				_(
					"data_snapshot is a {0}. It has to be a JSON OBJECT — every reader of this "
					"feed indexes it by field name."
				).format(type(parsed).__name__),
				title=_("Snapshot Is Not An Object"),
			)
		if self.is_new() or not self.snapshot_hash:
			self.snapshot_hash = snapshot_hash(text)

	def _refuse_rewrites(self) -> None:
		"""Refuse a change to the frozen half, and refuse un-acknowledging.

		`get_doc_before_save` is None on insert and on a save that loaded nothing,
		so this is a no-op exactly where there is no previous state to compare —
		which is the correct reading, not a gap: a row being created cannot be
		rewriting anything.
		"""
		before = self.get_doc_before_save()
		if before is None:
			return

		# READ THROUGH `.get`, NOT THROUGH ATTRIBUTES. `get_doc_before_save`
		# hands back a Document on a live bench and a plain row mapping when the
		# previous state was loaded straight out of the database — and attribute
		# access works on one of those and raises AttributeError on the other.
		# An AttributeError raised inside the freeze check does not fail open in
		# a helpful way: it turns "this snapshot is frozen" into a crash on an
		# ordinary acknowledge, which is exactly the call this doctype has to
		# make cheap. `.get` is the accessor both shapes share.
		def previous(fieldname):
			try:
				return before.get(fieldname)
			except AttributeError:  # pragma: no cover - neither shape lacks .get
				return None

		if str(previous("data_snapshot") or "") != str(self.data_snapshot or ""):
			frappe.throw(
				_(
					"The snapshot on a Shadow Log Entry is FROZEN. What {0} was shown is what "
					"they acknowledged, and rewriting it would leave no record of either. A "
					"corrected figure is a NEW event with a new copy — raise that instead."
				).format(self.recipient_name or self.recipient_employee or _("the recipient")),
				title=_("The Snapshot Is Frozen"),
			)

		if str(previous("occurred_at") or "") != str(self.occurred_at or ""):
			frappe.throw(
				_("occurred_at is when the event happened. It cannot be moved after the fact."),
				title=_("The Timestamp Is Frozen"),
			)

		if frappe.utils.cint(previous("acknowledged")) and not frappe.utils.cint(self.acknowledged):
			frappe.throw(
				_(
					"An acknowledgement cannot be withdrawn. 'I saw this' is a statement about "
					"the past. Dispute what happened on the source record, where somebody will "
					"read it — clearing the tick would leave no trace of the seeing or the dispute."
				),
				title=_("Acknowledgement Is One-Way"),
			)

	def snapshot(self) -> dict:
		"""The frozen copy as a dict, or `{}` where it cannot be parsed. Never raises."""
		try:
			parsed = json.loads(str(self.data_snapshot or "") or "{}")
		except (ValueError, TypeError):  # pragma: no cover - validate refuses this on write
			return {}
		return parsed if isinstance(parsed, dict) else {}

	def snapshot_intact(self) -> bool:
		"""Does the stored hash still match the stored snapshot?

		False means somebody wrote this row without going through this controller.
		Reported by `get_shadow_log_entry` rather than raised: a tampered backup is
		a finding, and refusing to show it would hide the evidence of the tampering.
		"""
		if not self.snapshot_hash:
			return False
		return snapshot_hash(str(self.data_snapshot or "")) == str(self.snapshot_hash)


def snapshot_hash(text: str) -> str:
	"""SHA-256 of a snapshot's exact bytes. One place, so writer and checker agree."""
	return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()
