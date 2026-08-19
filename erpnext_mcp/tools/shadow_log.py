# SPDX-License-Identifier: MIT
"""The RACI feed: what happened, frozen, addressed up the chain of command.

────────────────────────────────────────────────────────────────────────────
WHAT THIS IS FOR, AND WHY IT IS NOT A NOTIFICATION
────────────────────────────────────────────────────────────────────────────

A notification tells you to go and look at a record. This does the opposite: it
carries A FROZEN COPY of the record's values at the moment of the event, and the
recipient reads the copy rather than the source. Three consequences, and each of
them is the reason for a design decision further down this file:

  1. WHAT WAS ACKNOWLEDGED IS WHAT WAS SHOWN. A supervisor who ticked a session
     of 412 buckets acknowledged 412. If a recount later makes it 380, the
     supervisor's acknowledgement did not silently become an acknowledgement of
     380 — which is what a notification pointing at a live record does, and
     which is indefensible the moment somebody disputes a piece rate.

  2. IT IS A BACKUP. Three levels get three copies. Losing the Bucket Log
     Session — a bad migration, an over-enthusiastic delete, a doctype
     uninstalled — does not lose the account of what happened that morning,
     because three rows in another table still hold it. That is why
     `source_doctype` and `source_name` are Data and not Links: a Dynamic Link
     would let the delete cascade into the backup, which would make the backup
     worthless in exactly the scenario it exists for.

  3. THE CHAIN IS EVIDENCE. "Did anybody above this crew know?" is a question
     that comes up in an OSHA interview, a wage claim and an audit, and the
     honest answers are "yes, on this date" and "no, nobody was told". Both are
     answerable from this table and neither is answerable from a feed that only
     kept the latest state.

────────────────────────────────────────────────────────────────────────────
THE CHAIN IS `Employee.reports_to`, WALKED, AND IT IS ALLOWED TO BE SHORT
────────────────────────────────────────────────────────────────────────────

Level 1 is the subject's direct supervisor, 2 is that person's supervisor, 3 is
the one above that — on a farm this size, the owner. THE NUMBER IS A DISTANCE
AND NOT A JOB TITLE. A crew whose chain is two deep produces two copies, not
three with the third addressed to whoever happened to be around; inventing a
recipient would put somebody's name against work they were never told about.

`dispatch._resolve_employee_supervisor` reads the same column for the same
reason and this does not re-implement it: both answer "who would go and find
this person", and a second reading of `reports_to` that disagreed with the first
would be a bug nobody could see.

A CYCLE IS SURVIVED AND REPORTED. `reports_to` is operator-maintained and two
people who report to each other is a data-entry error somebody makes, not a
hypothetical. The walk carries a `seen` set and stops; the result says so, and
`raci_chain` is what a caller reads to find out. A recursion that blew the stack
inside a bucket sync would take a morning's picking with it.

A PERSON APPEARS AT MOST ONCE. Where the chain doubles back — a foreman who
reports to a manager who reports to that same foreman for another purpose — the
nearer level wins and the further one is dropped. Three copies of one event in
one person's feed is how a feed stops being read.

────────────────────────────────────────────────────────────────────────────
PROPAGATION CANNOT FAIL ITS CALLER. EVER.
────────────────────────────────────────────────────────────────────────────

`propagate` is called at the tail of a bucket sync, a shift close, an alert
raise and a task completion. Every one of those is somebody's real work being
filed, and none of them may fail because a supervisory copy could not be
written. So `propagate` catches everything, returns a report, and the callers
put that report in their result under `shadow_log` — visible, greppable, and
never fatal.

That is the same trade `shifts.bridge_to_attendance` makes and for the same
reason: the compliance act is the thing being filed, and the convenience built
on top of it does not get to veto it.

IDEMPOTENCE IS A UNIQUE INDEX, NOT A CHECK. `shadow_key` is derived from the
event, the source record and the recipient — never from a timestamp — so a
handset that timed out and resent a batch produces the same key and the insert
is refused by the database. A pre-check would lose the race between two workers
syncing the same session at once, which on a crew of forty is a Tuesday.

THE FIRST SNAPSHOT WINS AND IS NEVER REFRESHED. That follows from consequence 1
above: if re-raising the same event overwrote the snapshot, the supervisor's
acknowledgement would migrate onto values they never saw. A genuinely new fact
about the same record is a new EVENT with its own key.
"""

from __future__ import annotations

import json

import frappe

from .. import compat, settings
from ..args import as_bool, as_int, as_limit, as_str
from ..errors import ToolError
from ..result import ToolResult

DOCTYPE = "Shadow Log Entry"
EMPLOYEE = "Employee"

#: How far up the chain a copy travels. Three because that is what the RACI
#: brief asks for and because a fourth level on a farm this size is the same
#: person twice. `Shadow Log Entry`'s controller refuses anything outside 1–3,
#: so this constant and that check cannot drift.
MAX_LEVEL = 3

#: What `shadow_level` means, in words, for a reader who has only the number.
LEVEL_NAMES = {
	1: "direct supervisor",
	2: "manager",
	3: "owner",
}

#: The four events that propagate. A CLOSED SET, mirroring the Select on the
#: doctype: an event type nobody can filter for is a row nobody reads.
EVENT_BUCKET_SESSION = "Bucket Session Synced"
EVENT_SHIFT_CLOSED = "Shift Closed"
EVENT_ALERT_RAISED = "Compliance Alert Raised"
EVENT_TASK_COMPLETED = "Farm Task Completed"

EVENTS = (EVENT_BUCKET_SESSION, EVENT_SHIFT_CLOSED, EVENT_ALERT_RAISED, EVENT_TASK_COMPLETED)

#: What a source doctype's "who is this about" column is called, per doctype.
#: Read by name rather than configured, on the same argument
#: `dispatch._employee_of_source` makes: a doctype whose column is called
#: something else is a doctype this feed does not serve, and answering "no
#: subject" is better than a setting that lets somebody point the feed at a
#: Purchase Order.
SUBJECT_FIELDS = {
	"Bucket Log Session": ("employee",),
	"Farm Shift": ("foreman",),
	"Farm Task": ("assigned_to", "reported_by"),
	"Farm Task Assignment": ("assigned_to",),
	"Compliance Alert": ("employee",),
}

#: How many copies one call may write. A backstop, not a policy: the nightly
#: alert sweep can raise a lot of alerts on a site that has just installed the
#: compliance framework, and forty new alerts times three levels is a hundred
#: and twenty rows in one transaction. Exceeding it is REPORTED rather than
#: silent — see `propagate`, which puts the number it dropped in its result.
BATCH_CAP = 200

RECORD_CAP = 500

#: Snapshot fields never worth copying: framework bookkeeping that says nothing
#: about what happened, and would make every snapshot twice the size.
_SKIP_FIELDS = frozenset(
	{
		"doctype",
		"idx",
		"docstatus",
		"parent",
		"parentfield",
		"parenttype",
		"_user_tags",
		"_comments",
		"_assign",
		"_liked_by",
		"modified_by",
	}
)


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)


def available() -> bool:
	"""Whether this site can hold a shadow copy at all. Never raises."""
	try:
		return compat.doctype_exists(DOCTYPE)
	except Exception:  # pragma: no cover - a bench mid-migrate
		return False


def feed_enabled() -> bool:
	"""The operator's switch for the propagation itself. Never raises.

	SEPARATE FROM THE TOOL SWITCHES, because it answers a different question. The
	`allow_*` switches decide what an MCP client may call; this decides whether a
	bucket sync writes supervisory copies at all. An operation that does not run
	a chain of command — a two-person orchard where everybody is the owner —
	turns it off and stops generating rows nobody reads, while keeping the three
	read tools live for the rows it already has.

	Ships ON: the feature is the release, and a switch that had to be found and
	flipped before anything happened would be a support call rather than a
	safeguard.

	Fails ON rather than off, which is the opposite of every security gate in
	this app and is correct here: this is not a gate. Nothing is exposed by
	writing a copy, and a settings read that failed mid-migrate silently
	switching off the audit trail is the worse of the two failures.
	"""
	try:
		return settings.as_bool(settings._value("shadow_log_feed_enabled"))
	except Exception:  # pragma: no cover - a site mid-migrate
		return True


# ── the chain ───────────────────────────────────────────────────────────────
def raci_chain(employee: str, max_level: int = MAX_LEVEL) -> dict:
	"""Walk `reports_to` up from `employee`. Never raises.

	Returns `{"chain": [{"level": 1, "employee": ..., "employee_name": ...}, ...],
	"notes": [...]}`. The chain is allowed to be SHORT and to be EMPTY, and both
	are real answers rather than failures — see the module docstring on why
	inventing a recipient is worse than reporting that there is none.
	"""
	out: dict = {"chain": [], "notes": []}
	subject = str(employee or "").strip()
	if not subject:
		out["notes"].append("no subject employee, so there is no chain to walk.")
		return out
	if not compat.doctype_exists(EMPLOYEE):
		out["notes"].append("this site has no Employee register, so there is no chain of command to read.")
		return out
	if not compat.has_field(EMPLOYEE, "reports_to"):
		out["notes"].append(
			"this site's Employee register has no reports_to column, so this app does not know "
			"who anybody reports to. No shadow copies were raised."
		)
		return out

	seen = {subject}
	current = subject
	for level in range(1, max(1, min(int(max_level or MAX_LEVEL), MAX_LEVEL)) + 1):
		try:
			row = frappe.db.get_value(
				EMPLOYEE, current, ["reports_to", "employee_name", "status"], as_dict=True
			)
		except Exception:  # pragma: no cover - a site mid-migrate
			out["notes"].append("the Employee register could not be read; the chain stops here.")
			break
		supervisor = str((row or {}).get("reports_to") or "").strip()
		if not supervisor:
			if level == 1:
				out["notes"].append(
					f"{current} has no reports_to on their Employee record, so this app does not "
					"know who to send a copy to. Fill it in and later events will propagate."
				)
			else:
				out["notes"].append(
					f"the chain above {current} ends at level {level - 1}. That is a short chain, "
					"not a missing one — no copy was addressed to a level that does not exist."
				)
			break
		if supervisor in seen:
			# See the module docstring: `reports_to` is operator-maintained and a
			# loop is a data-entry error somebody makes, not a hypothetical.
			out["notes"].append(
				f"{supervisor} already appears in this chain, so the walk stopped at level "
				f"{level - 1} rather than going round again. Two people who report to each other "
				"is a reports_to somebody needs to correct."
			)
			break

		try:
			detail = (
				frappe.db.get_value(
					EMPLOYEE, supervisor, ["employee_name", "status", "company"], as_dict=True
				)
				or {}
			)
		except Exception:  # pragma: no cover
			detail = {}

		seen.add(supervisor)
		out["chain"].append(
			{
				"level": level,
				"level_name": LEVEL_NAMES.get(level, f"level {level}"),
				"employee": supervisor,
				"employee_name": str(detail.get("employee_name") or "") or supervisor,
				"status": str(detail.get("status") or "") or None,
				"company": str(detail.get("company") or "") or None,
			}
		)
		current = supervisor

	return out


def subject_of(doctype: str, row: dict) -> str:
	"""Who a source record is about, off the column its doctype uses. `""` if none."""
	for fieldname in SUBJECT_FIELDS.get(str(doctype or ""), ()):
		value = str((row or {}).get(fieldname) or "").strip()
		if value:
			return value
	return ""


def _top_of_house(company: str) -> list:
	"""Active employees at the top of the chain in one company — nobody above them.

	THE FALLBACK FOR AN EVENT WITH NO SUBJECT, and it is deliberately narrow. A
	Compliance Alert about a cabin, a water test or a filing is about the
	OPERATION rather than about a person, so there is no chain to walk — and a
	feed that therefore raised nothing for the whole category would make the
	"compliance alert" half of this release decorative.

	So those go to whoever has no supervisor, AT LEVEL 3 ONLY. Level 3 because
	that is what the level means: 1 and 2 are relative to a subject and there is
	no subject here, while 3 is "the top", which is exactly who is being told.
	Filing it as a level 1 would put an alert about a water test in a foreman's
	direct-report feed beside the crew events, where it does not belong.

	Never raises; an empty list is a real answer and is reported by the caller.
	"""
	if not (company and compat.doctype_exists(EMPLOYEE) and compat.has_field(EMPLOYEE, "reports_to")):
		return []
	try:
		rows = (
			frappe.db.get_all(
				EMPLOYEE,
				filters={"company": company, "status": "Active", "reports_to": ["in", ["", None]]},
				fields=["name", "employee_name"],
				limit=10,
			)
			or []
		)
	except Exception:  # pragma: no cover - a site mid-migrate
		return []
	return [
		{
			"level": MAX_LEVEL,
			"level_name": LEVEL_NAMES.get(MAX_LEVEL, "owner"),
			"employee": str(row.get("name")),
			"employee_name": str(row.get("employee_name") or "") or str(row.get("name")),
			"status": "Active",
			"company": company,
		}
		for row in rows
	]


# ── the snapshot ────────────────────────────────────────────────────────────
def snapshot_of(doctype: str, name: str, extra: dict | None = None) -> dict:
	"""The source record's values, flattened for freezing. Never raises.

	Child tables are included as lists of dicts, because a bucket session without
	its entries or a shift without its crew is not a copy of what happened — it
	is a header. `_SKIP_FIELDS` drops framework bookkeeping that says nothing
	about the event and would double the size of every row.
	"""
	out: dict = {"_doctype": doctype, "_name": name}
	try:
		doc = frappe.get_doc(doctype, name)
	except Exception as exc:
		out["_snapshot_error"] = f"{type(exc).__name__}: {exc}"
		if extra:
			out.update(extra)
		return out

	try:
		raw = dict(doc.as_dict())
	except Exception as exc:  # pragma: no cover - a document that will not serialise
		out["_snapshot_error"] = f"{type(exc).__name__}: {exc}"
		if extra:
			out.update(extra)
		return out

	for key, value in raw.items():
		if key in _SKIP_FIELDS or str(key).startswith("__"):
			continue
		out[key] = _plain(value)

	if extra:
		out.update({key: _plain(value) for key, value in extra.items()})
	return out


def _plain(value):
	"""A value JSON can hold. Dates become strings; child rows become dicts."""
	if value is None or isinstance(value, (bool, int, float, str)):
		return value
	if isinstance(value, (list, tuple)):
		return [_plain(item) for item in value]
	if isinstance(value, dict):
		return {
			str(key): _plain(item)
			for key, item in value.items()
			if key not in _SKIP_FIELDS and not str(key).startswith("__")
		}
	if hasattr(value, "as_dict"):
		try:
			return _plain(dict(value.as_dict()))
		except Exception:  # pragma: no cover
			return str(value)
	return str(value)


def shadow_key(event_type: str, source_doctype: str, source_name: str, recipient: str) -> str:
	"""The deterministic identity of one copy. NEVER includes a timestamp.

	The same argument `alerts.base.alert_key` makes: a key carrying the moment
	would make every retry a new row, and would discard the acknowledgement
	somebody already gave. `Compliance Alert` hashes for the same reason; this
	does not, because the four parts are short enough to fit a docname and a
	readable key is worth more than four saved characters when somebody is
	looking at this table trying to work out why a copy did or did not appear.
	"""
	parts = [
		str(part or "").strip().replace("::", ":")
		for part in (event_type, source_doctype, source_name, recipient)
	]
	key = "::".join(parts)
	if len(key) <= 140:
		return key
	# A docname is 140 characters. A source name long enough to overflow that is
	# pathological rather than impossible, and truncating the middle would make
	# two different records collide — so hash the whole thing and keep the event
	# on the front so the row is still identifiable by eye.
	import hashlib

	digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
	return f"{parts[0]}::{digest}"


# ── propagation ─────────────────────────────────────────────────────────────
def propagate(
	event_type: str,
	source_doctype: str,
	source_name: str,
	summary: str,
	subject_employee: str = "",
	company: str = "",
	occurred_at: str = "",
	snapshot: dict | None = None,
	extra: dict | None = None,
) -> dict:
	"""Raise one frozen copy per level of the chain. NEVER RAISES.

	This is the function the four propagating events call at their tail, and its
	whole contract is in that last sentence: a bucket sync, a shift close, an
	alert and a completion are somebody's real work being filed, and none of them
	may fail because a supervisory copy could not be written. Everything is
	caught; the report is what the caller puts in its result.

	Returns a dict the callers hand back verbatim under `shadow_log`:
	`{"raised": [...], "skipped": [...], "existing": [...], "notes": [...],
	"chain": [...]}`.
	"""
	report: dict = {
		"event_type": event_type,
		"source_doctype": source_doctype,
		"source_name": source_name,
		"raised": [],
		"existing": [],
		"skipped": [],
		"notes": [],
		"chain": [],
	}
	try:
		if not available():
			report["skipped"].append("this site has no Shadow Log Entry doctype — run `bench migrate`.")
			return report
		if not feed_enabled():
			report["skipped"].append(
				"the shadow log feed is switched off in ERPNext MCP Settings "
				"(shadow_log_feed_enabled). Nothing was written and nothing else changed."
			)
			return report
		if event_type not in EVENTS:
			report["skipped"].append(
				f"{event_type!r} is not one of the four propagating events: {', '.join(EVENTS)}."
			)
			return report

		subject = str(subject_employee or "").strip()
		chain_result = raci_chain(subject) if subject else {"chain": [], "notes": []}
		chain = list(chain_result.get("chain") or [])
		report["notes"].extend(chain_result.get("notes") or [])

		if not chain and not subject and company:
			chain = _top_of_house(company)
			if chain:
				report["notes"].append(
					f"this event is about the operation rather than about a person, so there is no "
					f"chain to walk. It went to the {len(chain)} employee(s) at the top of "
					f"{company} at level {MAX_LEVEL} — see shadow_log._top_of_house."
				)
			else:
				report["notes"].append(
					f"this event names no employee and {company} has nobody with an empty "
					"reports_to, so there was nobody to address a copy to. Nothing was written."
				)

		report["chain"] = chain
		if not chain:
			return report

		subject_name = ""
		if subject:
			try:
				subject_name = str(frappe.db.get_value(EMPLOYEE, subject, "employee_name") or "")
			except Exception:  # pragma: no cover
				subject_name = ""

		frozen = snapshot if snapshot is not None else snapshot_of(source_doctype, source_name, extra)
		try:
			payload = json.dumps(frozen, indent=2, sort_keys=True, default=str)
		except (TypeError, ValueError) as exc:  # pragma: no cover - `_plain` should prevent this
			report["skipped"].append(f"the snapshot could not be serialised: {type(exc).__name__}: {exc}")
			return report

		when = str(occurred_at or "") or frappe.utils.now()

		for entry in chain[:BATCH_CAP]:
			recipient = str(entry.get("employee") or "")
			if not recipient:
				continue
			key = shadow_key(event_type, source_doctype, source_name, recipient)
			try:
				if frappe.db.exists(DOCTYPE, key):
					# ALREADY RAISED, AND THE FIRST SNAPSHOT STANDS. Not an error
					# and not a no-op worth hiding: a handset that resent a batch
					# is the ordinary case, and saying "this was already filed"
					# is how somebody reading the sync result knows the retry did
					# what it was supposed to.
					report["existing"].append(key)
					continue
				doc = frappe.new_doc(DOCTYPE)
				doc.shadow_key = key
				doc.event_type = event_type
				doc.shadow_level = entry.get("level") or 1
				doc.recipient_employee = recipient
				doc.recipient_name = entry.get("employee_name") or recipient
				doc.company = company or entry.get("company") or None
				doc.source_doctype = source_doctype
				doc.source_name = source_name
				doc.subject_employee = subject or None
				doc.subject_name = subject_name or None
				doc.occurred_at = when
				doc.summary = summary
				doc.data_snapshot = payload
				doc.acknowledged = 0
				doc.flags.ignore_permissions = True
				doc.insert(ignore_permissions=True)
				report["raised"].append(
					{
						"name": doc.name,
						"level": doc.shadow_level,
						"recipient": recipient,
						"recipient_name": doc.recipient_name,
					}
				)
			except Exception as exc:
				# ONE LEVEL FAILING MUST NOT COST THE OTHER TWO, and none of them
				# may cost the caller. A duplicate lost to a concurrent insert
				# lands here and reads as what it is.
				report["skipped"].append(
					f"level {entry.get('level')} ({recipient}): {type(exc).__name__}: {exc}"
				)

		if len(chain) > BATCH_CAP:  # pragma: no cover - a chain longer than 200 cannot occur
			report["notes"].append(
				f"{len(chain) - BATCH_CAP} level(s) beyond this call's cap of {BATCH_CAP} were not written."
			)

	except Exception as exc:  # pragma: no cover - the contract's last line of defence
		report["skipped"].append(f"the shadow log feed failed entirely: {type(exc).__name__}: {exc}")
	return report


def quiet_propagate(**kwargs) -> dict | None:
	"""`propagate`, returning None where nothing at all happened.

	What the four call sites use, so a result gains a `shadow_log` key only when
	there is something to say. A key that is always present and usually empty is
	noise in every response on the surface with the least room for it.
	"""
	report = propagate(**kwargs)
	if report.get("raised") or report.get("skipped") or report.get("existing"):
		return report
	if report.get("notes"):
		return report
	return None


# ── 1. list_shadow_log_entries ──────────────────────────────────────────────
def list_shadow_log_entries(args: dict) -> ToolResult:
	"""Read-only. The feed, filtered the three ways the brief asks for."""
	_require()

	filters: dict = {}
	employee = as_str(args, "employee")
	if employee:
		filters["recipient_employee"] = employee
	level = as_int(args, "level")
	if level is not None:
		filters["shadow_level"] = level
	acknowledged = as_bool(args, "acknowledged", None)
	if acknowledged is not None:
		filters["acknowledged"] = 1 if acknowledged else 0
	event_type = as_str(args, "event_type")
	if event_type:
		if event_type not in EVENTS:
			raise ToolError(
				f"{event_type!r} is not an event this feed carries. The four are: {', '.join(EVENTS)}."
			)
		filters["event_type"] = event_type
	source_doctype = as_str(args, "source_doctype")
	if source_doctype:
		filters["source_doctype"] = source_doctype
	source_name = as_str(args, "source_name")
	if source_name:
		filters["source_name"] = source_name
	subject = as_str(args, "subject_employee")
	if subject:
		filters["subject_employee"] = subject
	company = as_str(args, "company")
	if company:
		filters["company"] = company

	limit = min(as_limit(args), RECORD_CAP)
	rows = (
		frappe.db.get_all(
			DOCTYPE,
			filters=filters,
			fields=compat.existing_fields(
				DOCTYPE,
				(
					"name",
					"event_type",
					"shadow_level",
					"recipient_employee",
					"recipient_name",
					"subject_employee",
					"subject_name",
					"company",
					"source_doctype",
					"source_name",
					"occurred_at",
					"summary",
					"acknowledged",
					"acknowledged_at",
					"acknowledged_by",
				),
			),
			order_by="occurred_at desc, creation desc",
			limit=limit,
		)
		or []
	)

	described = [_describe_row(row) for row in rows]
	unacknowledged = [row for row in described if not row["acknowledged"]]

	data = {
		"count": len(described),
		"unacknowledged_count": len(unacknowledged),
		"filters": {
			"employee": employee or None,
			"level": level,
			"acknowledged": acknowledged,
			"event_type": event_type or None,
			"source_doctype": source_doctype or None,
			"company": company or None,
		},
		"entries": described,
		"levels": {str(number): name for number, name in LEVEL_NAMES.items()},
	}
	if not described and employee:
		data["empty_note"] = (
			f"{employee} has no shadow copies matching these filters. An EMPTY FEED IS A REAL "
			"ANSWER and is not the same as a broken one: nobody may report to them, the feed may "
			"be switched off in ERPNext MCP Settings, or nothing has happened below them. "
			"list_shadow_log_entries with no employee shows whether the feed is producing anything "
			"at all."
		)
	if len(described) >= limit:
		data["truncated"] = (
			f"{limit} row(s) returned, which is this tool's cap. Narrow by employee, event_type or "
			"acknowledged rather than raising the limit."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} shadow copy(ies)"
			+ (f" for {employee}" if employee else "")
			+ f", {len(unacknowledged)} unacknowledged"
		),
	)


def _describe_row(row: dict) -> dict:
	return {
		"name": row.get("name"),
		"event_type": str(row.get("event_type") or ""),
		"shadow_level": frappe.utils.cint(row.get("shadow_level")),
		"level_name": LEVEL_NAMES.get(frappe.utils.cint(row.get("shadow_level")), ""),
		"recipient_employee": row.get("recipient_employee"),
		"recipient_name": row.get("recipient_name") or None,
		"subject_employee": row.get("subject_employee") or None,
		"subject_name": row.get("subject_name") or None,
		"company": row.get("company") or None,
		"source_doctype": row.get("source_doctype"),
		"source_name": row.get("source_name"),
		"occurred_at": str(row.get("occurred_at") or "") or None,
		"summary": row.get("summary"),
		"acknowledged": compat.checked(row.get("acknowledged")),
		"acknowledged_at": str(row.get("acknowledged_at") or "") or None,
		"acknowledged_by": row.get("acknowledged_by") or None,
	}


# ── 2. get_shadow_log_entry ─────────────────────────────────────────────────
def get_shadow_log_entry(args: dict) -> ToolResult:
	"""Read-only. One copy, with the frozen snapshot and what it no longer matches."""
	_require()
	name = as_str(args, "name", required=True)
	if not frappe.db.exists(DOCTYPE, name):
		raise ToolError(f"no Shadow Log Entry called {name!r} on this site.")

	doc = frappe.get_doc(DOCTYPE, name)
	row = dict(doc.as_dict())
	data = _describe_row(row)
	data["acknowledged_note"] = row.get("acknowledged_note") or None
	data["snapshot"] = doc.snapshot()
	data["snapshot_hash"] = row.get("snapshot_hash") or None
	data["snapshot_intact"] = doc.snapshot_intact()

	if not data["snapshot_intact"]:
		data["integrity_warning"] = (
			"THE STORED HASH DOES NOT MATCH THE STORED SNAPSHOT. This row was written or altered "
			"by something that went round the doctype's controller, which refuses a change to a "
			"snapshot after insert. The copy is shown anyway — hiding it would hide the evidence "
			"of the alteration — but it is no longer proof of what the recipient was shown."
		)

	# WHAT THE SOURCE SAYS NOW, AND WHETHER IT STILL EXISTS. Reported beside the
	# frozen copy and never merged into it: the whole point of the row is that
	# these two can disagree, and a reader who cannot see the disagreement has
	# the same feed a notification would have given them.
	source_doctype = str(row.get("source_doctype") or "")
	source_name = str(row.get("source_name") or "")
	if source_doctype and source_name:
		try:
			still_there = bool(
				compat.doctype_exists(source_doctype) and frappe.db.exists(source_doctype, source_name)
			)
		except Exception:  # pragma: no cover - a site mid-migrate
			still_there = False
		data["source_still_exists"] = still_there
		if not still_there:
			data["source_note"] = (
				f"{source_doctype} {source_name} is no longer on this site. THE SNAPSHOT ABOVE IS "
				"NOW THE ONLY RECORD OF IT, which is the case this feed exists for — the copies "
				"are a redundant backup, deliberately not linked to the source so a delete could "
				"not take them with it."
			)

	if not data["acknowledged"]:
		data["next_step"] = (
			"acknowledge_shadow_log(name) records that the recipient has seen this. It is ONE-WAY "
			"and cannot be withdrawn: 'I saw this' is a statement about the past."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{data['event_type']} → {data['recipient_name'] or data['recipient_employee']} "
			f"(level {data['shadow_level']}), "
			+ ("acknowledged" if data["acknowledged"] else "unacknowledged")
		),
	)


# ── 3. acknowledge_shadow_log ───────────────────────────────────────────────
def acknowledge_shadow_log(args: dict) -> ToolResult:
	"""MUTATING (default OFF). Record that the recipient has seen this copy."""
	_require()
	name = as_str(args, "name", required=True)
	if not frappe.db.exists(DOCTYPE, name):
		raise ToolError(f"no Shadow Log Entry called {name!r} on this site.")

	doc = frappe.get_doc(DOCTYPE, name)
	if compat.checked(doc.acknowledged):
		# NOT AN ERROR. A phone that lost its response and retried is the ordinary
		# case, and refusing the second call would make a client choose between
		# retrying and being correct. The existing acknowledgement is returned
		# unchanged, which is what the caller wanted to be true.
		return ToolResult(
			data={
				"name": doc.name,
				"acknowledged": True,
				"acknowledged_at": str(doc.acknowledged_at or "") or None,
				"acknowledged_by": doc.acknowledged_by,
				"acknowledged_note": doc.acknowledged_note or None,
				"x_idempotent": True,
				"note": (
					f"{doc.name} was already acknowledged"
					+ (f" at {doc.acknowledged_at}" if doc.acknowledged_at else "")
					+ ". Nothing was changed. An acknowledgement is one-way and this call is safe "
					"to retry."
				),
			},
			summary=f"{doc.name} was already acknowledged; nothing changed",
			docstatus_delta="none — already acknowledged",
		)

	doc.acknowledged = 1
	doc.acknowledged_at = as_str(args, "acknowledged_at") or frappe.utils.now()
	doc.acknowledged_by = frappe.session.user
	note = as_str(args, "note")
	if note:
		doc.acknowledged_note = note
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	return ToolResult(
		data={
			"name": doc.name,
			"event_type": doc.event_type,
			"shadow_level": frappe.utils.cint(doc.shadow_level),
			"recipient_employee": doc.recipient_employee,
			"recipient_name": doc.recipient_name,
			"source_doctype": doc.source_doctype,
			"source_name": doc.source_name,
			"occurred_at": str(doc.occurred_at or "") or None,
			"acknowledged": True,
			"acknowledged_at": str(doc.acknowledged_at or ""),
			"acknowledged_by": doc.acknowledged_by,
			"acknowledged_note": doc.acknowledged_note or None,
			"x_idempotent": False,
			"note": (
				"This records that the recipient saw THE SNAPSHOT ON THIS ROW, not the source "
				"record's current values. That distinction is the whole point of the copy: if the "
				"source is corrected later, this acknowledgement does not silently become an "
				"acknowledgement of the correction."
			),
		},
		summary=f"{doc.name} acknowledged by {doc.acknowledged_by}",
		docstatus_delta="unacknowledged → acknowledged",
	)
