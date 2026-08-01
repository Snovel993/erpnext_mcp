# SPDX-License-Identifier: MIT
"""Controller for Farm Task — one piece of work, and the evidence closing it needs.

THE NAME IS `FT-YYYY-MM-<seq>`, SEQUENCED WITHIN THE MONTH, AND NOT THE TASK NAME.
The Sprint 8 specification asked for `task_name` as the docname and it is the one
place this implementation departs from it, deliberately and with a reason that
shows up in the second season rather than the first:

  * a habitability walk on MC-Cabin-01 happens EVERY year. A docname built from
    "Habitability walk — MC-Cabin-01" collides with its own history the moment
    the second one is raised, and the failure lands in front of whoever ran
    `generate_tasks_from_compliance_alerts` on a Tuesday in August;
  * fifty-four tasks generated from fifty-four alerts in one call have to produce
    fifty-four distinct names with no human in the loop to disambiguate them.

So `task_name` stays as the title — it is what a foreman reads on the board — and
the key is a sequence. The month is in it for the same reason Housing Assignment
carries one: farm work arrives in the same fortnight every year, and a name
carrying the month sorts into seasons without a report. The sequence is computed
from the rows already in that month rather than from Frappe's naming series,
because the series counter is global and would leave gaps that read like deleted
records in an audit somebody is defending.

`evidence_required` IS MANDATORY AND THAT IS THE WHOLE DESIGN. A task that does
not say what closing it requires is a task that gets closed with a tick in a box.
The controller refuses a blank one, refuses one that is not a JSON object, and
refuses a key it does not recognise — because `{"photo": true}` where `"photos"`
was meant asks for nothing, refuses nothing, and looks exactly like a task with
a photo requirement right up until the audit.

`assigned_to` IS A DATA FIELD, NOT A LINK, for the same reason
`Housing Assignment.employee` is. Frappe HR is not a dependency of this app; a
Link to Employee would make the whole doctype fail to migrate on every site that
has not installed it. The id is validated against the Employee register *where
there is one*.

WHAT THIS REFUSES IS SMALL. A missing evidence contract, a nonsense one, a
negative duration, and a state that is not one of the eight. It does NOT police
the transitions between states: the Kanban board is a foreman dragging a card,
and a controller that refused a drag would make the board a decoration. The
ORDER is enforced in the tools, where the transition carries a claim about who
did what and when — see `erpnext_mcp/tools/dispatch.py`.
"""

import json

import frappe
from frappe import _
from frappe.model.document import Document

#: The eight states, in the order they read across a dispatch board.
DRAFT = "Draft"
AVAILABLE = "Available"
CLAIMED = "Claimed"
IN_PROGRESS = "In-Progress"
AWAITING_REVIEW = "Awaiting-Review"
COMPLETED = "Completed"
REJECTED = "Rejected"
CANCELLED = "Cancelled"

STATES = (
	DRAFT,
	AVAILABLE,
	CLAIMED,
	IN_PROGRESS,
	AWAITING_REVIEW,
	COMPLETED,
	REJECTED,
	CANCELLED,
)

#: States in which the work is finished or abandoned. Nothing claims, starts,
#: completes or rejects out of one of these.
TERMINAL_STATES = (COMPLETED, REJECTED, CANCELLED)

#: States that hold a worker's name and count against their concurrent-claim
#: limit. Awaiting-Review deliberately does NOT: the worker has finished and the
#: task is somebody else's problem, so holding it against their limit would
#: punish them for finding something.
LIVE_ASSIGNMENT_STATES = (CLAIMED, IN_PROGRESS)

#: How many tasks one worker may hold at once. Three is a morning: enough to
#: plan a trip round the camp, few enough that nobody can empty the pool onto
#: their own name and leave the board looking worked. It is a hoarding limit,
#: not a productivity one — completing one frees a slot immediately.
MAX_CONCURRENT_CLAIMS = 3

#: The evidence contract's vocabulary, and what each key obliges a completion to
#: carry. A key outside this set is REFUSED rather than ignored: `{"photo": true}`
#: where `"photos"` was meant asks for nothing and looks like it asks for
#: something, which is the worst of both.
EVIDENCE_KEYS = {
	"photos": "at least one photograph filed against the completion",
	"signature": "a signature capture — the worker attesting to what they found",
	"findings_text": "what they actually saw, in words, whether or not anything was wrong",
	"witness": "the name of somebody else who was there and saw the same thing",
}

#: The dispatch modes, and which of them a worker may claim from the pool.
DISPATCH_EITHER = "Either"
DISPATCH_DISPATCHED = "Dispatched"
DISPATCH_SELF_PICK = "Self-pick"
SELF_PICKABLE = (DISPATCH_EITHER, DISPATCH_SELF_PICK)


class FarmTask(Document):
	def autoname(self):
		month = str(frappe.utils.today())[:7]
		prefix = f"FT-{month}-"
		existing = frappe.db.get_all(
			"Farm Task",
			filters={"name": ("like", f"{prefix}%")},
			pluck="name",
			limit=100000,
		)
		highest = 0
		for name in existing or []:
			tail = str(name).rsplit("-", 1)[-1]
			if tail.isdigit():
				highest = max(highest, int(tail))
		self.name = f"{prefix}{highest + 1:05d}"

	def validate(self):
		self.task_name = str(self.task_name or "").strip()
		if not self.task_name:
			frappe.throw(_("Task is required — a task nobody can name is a task nobody will do."))
		if not self.task_type:
			frappe.throw(_("Task Type is required."))

		self.state = str(self.state or DRAFT).strip() or DRAFT
		if self.state not in STATES:
			frappe.throw(
				_("State {0} is not one of: {1}.").format(self.state, ", ".join(STATES)),
				title=_("Unknown Farm Task state"),
			)

		if int(self.estimated_duration_minutes or 0) < 0:
			frappe.throw(_("Estimated Duration cannot be negative."))

		self.evidence_required = json.dumps(parse_evidence_required(self.evidence_required))
		self.creates_record_data = json.dumps(parse_json_object(self.creates_record_data, "Creates Record Data"))

		self.assigned_to = str(self.assigned_to or "").strip()
		self.assigned_to_name = str(self.assigned_to_name or "").strip() or self.assigned_to

		if self.location and not self.location_doctype:
			frappe.throw(
				_(
					"Location {0} was given with no Location DocType, so nothing can resolve it. "
					"A Dynamic Link needs both halves."
				).format(self.location)
			)

		self.company = self.company or _company_of(self.location_doctype, self.location)


def parse_evidence_required(raw) -> dict:
	"""The evidence contract as a dict of booleans, or a refusal saying why.

	Refuses a blank contract, a contract that is not an object, a key outside
	`EVIDENCE_KEYS`, and a contract that requires nothing at all. The last one is
	the interesting refusal: `{}` is well-formed JSON and would create a task
	anybody could close by saying they had done it, which is precisely the shape
	of record this whole doctype exists to stop being written.
	"""
	value = parse_json_object(raw, "Evidence Required")
	if not value:
		frappe.throw(
			_(
				"Evidence Required is empty. Say what closing this task obliges somebody to "
				"produce — one or more of: {0}. A task that requires no evidence is a task "
				"that gets closed with a tick in a box, and a tick in a box is what an "
				"auditor is trained to disbelieve."
			).format(", ".join(sorted(EVIDENCE_KEYS))),
			title=_("Evidence Required is required"),
		)

	unknown = sorted(set(value) - set(EVIDENCE_KEYS))
	if unknown:
		frappe.throw(
			_(
				"Evidence Required names {0}, which nothing checks. The keys are: {1}. A "
				"misspelt key asks for nothing, refuses nothing, and looks exactly like a "
				"requirement right up until somebody reads the record."
			).format(", ".join(repr(key) for key in unknown), ", ".join(sorted(EVIDENCE_KEYS))),
			title=_("Unknown evidence key"),
		)

	out = {key: bool(value.get(key)) for key in sorted(value)}
	if not any(out.values()):
		frappe.throw(
			_(
				"Evidence Required has every requirement switched off, which is the same as "
				"asking for nothing. Set at least one of {0} to true."
			).format(", ".join(sorted(EVIDENCE_KEYS)))
		)
	return out


def parse_json_object(raw, label: str) -> dict:
	"""A JSON object from a string, a dict, or nothing. Anything else is refused."""
	if raw in (None, ""):
		return {}
	if isinstance(raw, dict):
		return dict(raw)
	try:
		value = json.loads(raw)
	except Exception:
		frappe.throw(
			_("{0} is not valid JSON: {1}").format(label, str(raw)[:200]),
			title=_("Malformed JSON"),
		)
	if not isinstance(value, dict):
		frappe.throw(
			_("{0} must be a JSON object, got {1}.").format(label, type(value).__name__),
			title=_("Malformed JSON"),
		)
	return value


def evidence_contract(raw) -> dict:
	"""The evidence contract, tolerant of a stored value nobody can now parse.

	Used on the READ side, where a document that somehow holds bad JSON should be
	reported rather than made unreadable. The write side goes through
	`parse_evidence_required`, which refuses.
	"""
	try:
		value = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
	except Exception:
		return {}
	if not isinstance(value, dict):
		return {}
	return {key: bool(value.get(key)) for key in EVIDENCE_KEYS if key in value}


def _company_of(location_doctype: str, location: str) -> str | None:
	"""The company a location belongs to, where the register knows.

	Every location register in this app names its company differently — a Parcel
	and a Housing Unit have `owning_entity`, a Certification has `company` — so
	this asks for whichever the doctype actually has rather than assuming.
	"""
	if not location_doctype or not location:
		return None
	for fieldname in ("owning_entity", "company"):
		try:
			if not frappe.get_meta(location_doctype).has_field(fieldname):
				continue
			value = frappe.db.get_value(location_doctype, location, fieldname)
		except Exception:
			continue
		if value:
			return value
	return None
