# SPDX-License-Identifier: MIT
"""Authoring compliance rules: create, approve, supersede, deactivate, test.

THESE SIX TOOLS ARE THE POINT OF v0.22.0. Everything else in the release —
the doctype, the engine, the sandbox, the seeder — exists so that these can
exist: an operation can change what its compliance calendar watches, and change
it in an afternoon, without a release, a deploy or an engineer.

FOUR PROPERTIES THEY ALL SHARE, AND EACH ONE IS A REFUSAL SOMEWHERE:

  * NOTHING GOES LIVE UNAPPROVED. `create_compliance_rule` writes a Draft. Only
    `approve_compliance_rule` can enable one, and it records who and when on the
    row. That gate is what keeps "a model wrote a rule and it started firing"
    from ever being a true sentence about this app.
  * EDITS SUPERSEDE, THEY DO NOT OVERWRITE. `update_compliance_rule` writes a
    new row at version+1 and points the old one at it. A sweep already running
    against v1 finishes against v1; an alert raised last April can still be read
    against the definition that raised it. Same pattern, same reasoning, as
    Inspection Templates in v0.21.0.
  * THERE IS NO DELETE. `deactivate_compliance_rule` turns a rule off and says
    why. The alerts it already owns stay exactly as they were — a rule that is
    off raises nothing AND DISMISSES NOTHING, which is the same reading a rule
    skipped for a missing DocType gets, and for the same reason: switching a
    rule off is not evidence that anybody did the work.
  * EVERY CHANGE IS LOGGED WITH ITS BEFORE-VALUES. `dispatch` already writes an
    MCP Action Log row for every call; these tools additionally return the
    field-by-field diff, and `update` puts the previous values in the result so
    the log row carries them. An auditor asking "who changed this and what did
    it say before" reads the answer off this app rather than off a git history
    they have no access to.

WHO MAY CALL THEM. The Compliance Rule DocType is granted to System Manager, to
Compliance Officer and to Farm Manager, and to nobody else — see
`erpnext_mcp/roles.py`. A Foreman reads the calendar and cannot rewrite what
fills it; a Field Worker cannot see it at all. That is the dispatch separation
this app already keeps, moved one layer up: the person who can silently redefine
what "required" means must be fewer people than the people who answer to it.
"""

from __future__ import annotations

import json

import frappe

from .. import compat, compliance_rules
from .. import training as regimes_vocabulary
from ..alerts import engine, sandbox
from ..args import as_bool, as_date, as_int, as_limit, as_str
from ..errors import ToolError
from ..result import ToolResult

DOCTYPE = compliance_rules.DOCTYPE

#: Fields `create` and `update` read straight off the arguments, and the reader
#: each one goes through. One table so the two tools cannot drift into accepting
#: different spellings of the same rule.
_TEXT_FIELDS = (
	"title",
	"category",
	"target_doctype",
	"requires_doctypes",
	"requires_fields",
	"builtin_scanner",
	"custom_python",
	"date_field",
	"window_field",
	"missing_date_behaviour",
	"due_date_mode",
	"severity_critical",
	"severity_warning",
	"severity_expired",
	"message_template",
	"regimes_from_field",
	"producer_task_template",
	"producer_farm_task_type",
	"producer_skill_required",
	"regulation_citations",
	"kairotic_gate_description",
	"purpose",
	"ai_source_citation",
)

_INT_FIELDS = (
	"cadence_days",
	"threshold_critical_days",
	"threshold_warning_days",
	"retention_years",
)

_BLOB_FIELDS = (
	("scope_filters", "scope_filters_json"),
	("evidence_contract", "evidence_contract_json"),
	("extra_parameters", "extra_parameters_json"),
	("audit_packet_types", "audit_packet_types"),
)


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app. "
		"Until then the sweep runs the rule definitions that ship with the app, so the calendar "
		"is correct; it is just not yet editable.",
	)


def _resolve_or_refuse(reference: str, label: str = "name") -> str:
	name = compliance_rules.resolve(reference)
	if not name:
		raise ToolError(
			f"no Compliance Rule for {reference!r} on this site. {label} takes a docname "
			"(CRULE-2026-0004, one exact version) or a rule_id (`training_expiring`, whichever "
			"version is live). list_compliance_rules has the register."
		)
	return name


# ── reads ───────────────────────────────────────────────────────────────────
def get_compliance_rule(args: dict) -> ToolResult:
	"""One rule in full: its definition, its provenance, and who approved it."""
	_require()
	name = _resolve_or_refuse(as_str(args, "name") or as_str(args, "rule", required=True))
	try:
		row = compliance_rules.rule_row(name)
	except ValueError as exc:
		raise ToolError(str(exc)) from None
	described = compliance_rules.describe(row, with_definition=True)
	described["note"] = _shape_note(described["shape"])
	return ToolResult(
		data=described,
		summary=(
			f"{described['rule_id']} v{described['version']} ({described['shape']}, "
			f"{'enabled' if described['enabled'] else 'off'}) — {described['title']}"
		),
	)


def _shape_note(shape: str) -> str:
	if shape == compliance_rules.SHAPE_BUILTIN:
		return (
			"This rule's SHAPE is a scanner that ships with the app — a join no set of "
			"declarative fields expresses yet, such as a finding superseded by a later clean "
			"record. Everything an operator would change is still on this record: the "
			"thresholds, the scope filters, the citations, the regimes and the switch. Only the "
			"shape of the scan is code, and it is code that was reviewed and shipped rather than "
			"typed into a field."
		)
	if shape == compliance_rules.SHAPE_CUSTOM:
		return (
			"This rule is a PROGRAM, run by this app's own restricted interpreter — never `exec`, "
			"never `eval`, no imports, no filesystem, no network, bounded in steps and in wall "
			"clock. It is the escape hatch, and every use of it is a request for a declarative "
			"primitive: if you can say what shape of question it asks, that shape probably wants "
			"to be a field. See docs/configurable_compliance_framework.md."
		)
	return (
		"This rule is fully DECLARATIVE: everything it does is on this record, and changing what "
		"it watches is changing a field. No code runs that is specific to this rule."
	)


def test_compliance_rule(args: dict) -> ToolResult:
	"""Run one rule against today's data and report what it WOULD raise. Writes nothing.

	The tool to call between authoring a rule and approving it. It takes the same
	code path the sweep takes — deliberately, because a dry run with a second
	implementation is a dry run that can disagree with the real one, which is the
	single property a dry run must not have.
	"""
	_require()
	name = _resolve_or_refuse(as_str(args, "name") or as_str(args, "rule", required=True))
	try:
		row = compliance_rules.rule_row(name)
	except ValueError as exc:
		raise ToolError(str(exc)) from None
	if not as_bool(args, "dry_run", True):
		raise ToolError(
			"test_compliance_rule only ever performs a dry run — that is the whole of what it is "
			"for. To make a rule's alerts real, approve it and run refresh_compliance_alerts."
		)

	today = as_date(args, "as_of") or frappe.utils.today()
	company = as_str(args, "company")
	result = engine.preview(row, {"today": today, "company": company or ""})
	result["name"] = name
	result["title"] = row.get("title")
	result["enabled"] = bool(compat.checked(row.get("enabled")))
	result["as_of"] = today
	result["company"] = company or None
	result["note"] = (
		"NOTHING WAS WRITTEN. These are the observations this rule would make against the data as "
		"it stands right now, with the alert docname each one would take. Run it before approving "
		"a rule: a rule that observes four hundred rows is a rule whose condition is wrong — "
		"usually a field that is empty everywhere rather than stale on a few — and finding that "
		"out here costs nothing, while finding it out after approval fills a calendar nobody then "
		"reads."
	)
	if not result["enabled"]:
		result["draft_note"] = (
			"This rule is not enabled, so the nightly sweep is not running it and none of these "
			"observations exists as an alert. approve_compliance_rule turns it on."
		)
	return ToolResult(
		data=result,
		summary=(
			f"{row.get('rule_id')} would raise {result['observed']} alert(s) as of {today} "
			f"(dry run, nothing written)"
		),
	)


# ── writes ──────────────────────────────────────────────────────────────────
def create_compliance_rule(args: dict) -> ToolResult:
	"""Author a new rule. It arrives as a DRAFT and fires nothing until approved."""
	_require()
	rule_id = as_str(args, "rule_id", required=True)
	if compliance_rules.resolve(rule_id):
		raise ToolError(
			f"there is already a Compliance Rule with rule_id {rule_id!r} on this site. A rule_id "
			"is the key every alert of that rule is filed under, so two rules cannot share one — "
			"the alerts would collide on the docname. Edit the existing rule with "
			"update_compliance_rule, which supersedes rather than overwrites, or pick another id. "
			"Nothing was written."
		)

	spec = _spec_from_args(args, required=True)
	spec["rule_id"] = rule_id
	spec["version"] = 1
	spec["authored_by"] = as_str(args, "authored_by") or compliance_rules.AUTHOR_OPERATOR
	# ALWAYS A DRAFT, whatever the caller asked for. The approval gate is not a
	# default somebody can pass past; it is the property that makes an
	# AI-proposed rule safe to accept at all.
	spec["enabled"] = 0

	doc = _insert(compliance_rules.build_rule(spec))
	described = compliance_rules.describe(compliance_rules.rule_row(doc.name), with_definition=True)
	return ToolResult(
		data={
			**described,
			"note": (
				"This rule is a DRAFT and is firing nothing. Run test_compliance_rule to see what "
				"it would raise against today's data, then approve_compliance_rule to turn it on — "
				"which records who accepted it and when, on the row, where an auditor can read it. "
				"A rule that could go live without a name against it would make the compliance "
				"calendar an assertion nobody signed."
			),
			"next": ["test_compliance_rule", "approve_compliance_rule"],
		},
		summary=f"created compliance rule {doc.name} ({rule_id} v1, draft — not yet firing)",
		docstatus_delta="none → 0 (created)",
	)


def approve_compliance_rule(args: dict) -> ToolResult:
	"""Accept a rule and turn it on, recording who accepted it and when."""
	_require()
	name = _resolve_or_refuse(as_str(args, "name") or as_str(args, "rule", required=True))
	try:
		row = compliance_rules.rule_row(name)
	except ValueError as exc:
		raise ToolError(str(exc)) from None

	if str(row.get("superseded_by") or "").strip():
		raise ToolError(
			f"Compliance Rule {name} was superseded by {row['superseded_by']} and cannot be "
			"enabled — two live definitions of one rule_id are two answers to one question. "
			"Approve that one instead, or pass the rule_id, which always resolves to the live "
			"version. Nothing was written."
		)

	approver = as_str(args, "approver") or _calling_user()
	employee = as_str(args, "approver_employee")
	if employee and compat.doctype_exists("Employee") and not frappe.db.exists("Employee", employee):
		raise ToolError(
			f"no Employee {employee!r} on this site. An approval naming somebody payroll has never "
			"heard of has already drifted from the operation it governs. Nothing was written."
		)
	signature = _signature_url(as_str(args, "approver_signature_file_token"))

	before = {
		"enabled": bool(compat.checked(row.get("enabled"))),
		"human_approved_by": row.get("human_approved_by"),
		"human_approved_on": str(row.get("human_approved_on") or "") or None,
	}
	doc = frappe.get_doc(DOCTYPE, name)
	doc.human_approved_by = approver
	doc.human_approved_on = frappe.utils.now()
	if employee:
		doc.approver_employee = employee
	if signature:
		doc.approver_signature = signature
	doc.enabled = 1
	doc.save(ignore_permissions=True)
	_link_signature(doc, signature)

	described = compliance_rules.describe(compliance_rules.rule_row(name), with_definition=True)
	return ToolResult(
		data={
			**described,
			"was": before,
			"note": (
				f"{described['rule_id']} is live from the next sweep. It was approved by "
				f"{approver} on {described['human_approved_on']}, and both of those are on the "
				"record rather than in a log — an auditor asking who decided this reads the answer "
				"off the rule itself."
				+ (
					" It was AI-proposed, so what was approved is a human's reading of the "
					"citation against the regulation, not the model's."
					if described["authored_by"] == compliance_rules.AUTHOR_AI
					else ""
				)
			),
		},
		summary=f"approved and enabled {name} ({described['rule_id']} v{described['version']})",
		docstatus_delta="0 → 0 (updated)",
	)


def update_compliance_rule(args: dict) -> ToolResult:
	"""Change a rule by SUPERSEDING it: a new row at version+1, the old one stood down."""
	_require()
	name = _resolve_or_refuse(as_str(args, "name") or as_str(args, "rule", required=True))
	try:
		old = compliance_rules.rule_row(name)
	except ValueError as exc:
		raise ToolError(str(exc)) from None
	if str(old.get("superseded_by") or "").strip():
		raise ToolError(
			f"Compliance Rule {name} was already superseded by {old['superseded_by']}. Edit that "
			"one — or pass the rule_id rather than the docname, which always resolves to the live "
			"version. Nothing was written."
		)
	reason = as_str(args, "reason")
	if reason and len(reason) < 10:
		raise ToolError(
			"reason must say something. A threshold moved with no sentence beside it is a change "
			"nobody can explain to the auditor who asks why the calendar looks different from last "
			"year's. Nothing was written."
		)

	current = compliance_rules.describe(old, with_definition=True)
	spec = _spec_from_args(args, required=False, current=old)
	spec["rule_id"] = as_str(args, "rule_id") or old.get("rule_id")
	spec["version"] = int(old.get("version") or 1) + 1
	spec["authored_by"] = as_str(args, "authored_by") or old.get("authored_by")
	# The new version inherits the old one's approval where the old one was live.
	# A THRESHOLD MOVED IS NOT A NEW RULE, and forcing a re-approval on every
	# tuning edit would train people to click through approvals, which is worse
	# than not having the gate. A rule that was OFF stays off and needs approving.
	spec["enabled"] = 1 if compat.checked(old.get("enabled")) else 0
	spec["human_approved_by"] = old.get("human_approved_by") or _calling_user()
	spec["human_approved_on"] = old.get("human_approved_on") or frappe.utils.now()

	# The old row is stood down FIRST, so the "one live row per rule_id" check in
	# the controller sees a clear field when the new row is inserted. If the
	# insert then fails, the dispatcher's rollback puts the old row back exactly
	# as it was — there is no ordering here that can leave a rule_id with no live
	# definition.
	frappe.db.set_value(DOCTYPE, name, {"enabled": 0, "active_row_flag": 0}, update_modified=False)
	try:
		doc = _insert(compliance_rules.build_rule(spec))
	except Exception:
		frappe.db.set_value(
			DOCTYPE,
			name,
			{
				"enabled": 1 if compat.checked(old.get("enabled")) else 0,
				"active_row_flag": 1 if compat.checked(old.get("active_row_flag")) else 0,
			},
			update_modified=False,
		)
		raise
	frappe.db.set_value(DOCTYPE, name, "superseded_by", doc.name, update_modified=False)

	described = compliance_rules.describe(compliance_rules.rule_row(doc.name), with_definition=True)
	changes = _diff(current["definition"], described["definition"])
	changes.update(_diff(_headline(current), _headline(described)))
	return ToolResult(
		data={
			**described,
			"supersedes": name,
			"previous_version": int(old.get("version") or 1),
			"changes": changes,
			"reason": reason or None,
			"note": (
				f"{name} was NOT edited. It is still on this site in full, disabled and pointing at "
				f"{doc.name} — so every alert raised under it can still be read against the "
				"definition that raised it, and a sweep that was already running when this call "
				"landed finishes against the version it started with. That is the whole reason "
				"rules are versioned by copy rather than in place."
			),
		},
		summary=(
			f"superseded {name} (v{old.get('version') or 1}) with {doc.name} (v{spec['version']}): "
			f"{', '.join(sorted(changes)) or 'no field changed'}"
		),
		docstatus_delta="none → 0 (created)",
	)


def deactivate_compliance_rule(args: dict) -> ToolResult:
	"""Stop a rule firing, and say why. Nothing it already raised is touched."""
	_require()
	name = _resolve_or_refuse(as_str(args, "name") or as_str(args, "rule", required=True))
	reason = as_str(args, "reason", required=True)
	if len(reason) < 10:
		raise ToolError(
			"reason must say something. A compliance rule switched off with no sentence beside it "
			"is the hardest thing in this app to explain a year later — to an auditor, or to "
			"whoever asks why the calendar stopped mentioning the thing that then went wrong. "
			"Nothing was written."
		)
	try:
		row = compliance_rules.rule_row(name)
	except ValueError as exc:
		raise ToolError(str(exc)) from None
	if not compat.checked(row.get("enabled")):
		raise ToolError(f"Compliance Rule {name} ({row.get('rule_id')}) is already off. Nothing was written.")

	standing = _alerts_of(str(row.get("rule_id") or ""))
	doc = frappe.get_doc(DOCTYPE, name)
	doc.enabled = 0
	note = str(doc.purpose or "")
	doc.purpose = (note + f"\n\nDisabled {frappe.utils.today()}: {reason}").strip()[:1000]
	doc.save(ignore_permissions=True)

	return ToolResult(
		data={
			**compliance_rules.describe(compliance_rules.rule_row(name)),
			"reason": reason,
			"alerts_left_standing": standing,
			"note": (
				f"{standing} alert(s) this rule raised are STILL ON THE CALENDAR, exactly as they "
				"were, and the next sweep will not touch them. A rule that is off raises nothing "
				"AND DISMISSES NOTHING — the same reading a rule skipped for a missing DocType "
				"gets, and for the same reason: switching a rule off is not evidence that anybody "
				"did the work. Dismiss them by hand if the condition really has resolved. "
				"approve_compliance_rule turns the rule back on."
			),
		},
		summary=f"disabled compliance rule {name} ({row.get('rule_id')}); {standing} alert(s) left standing",
		docstatus_delta="0 → 0 (updated)",
	)


def propose_compliance_rule(args: dict) -> ToolResult:
	"""Declared in v0.22.0 and refuses. The AI authoring hook, reserved not wired."""
	raise ToolError(
		"propose_compliance_rule is declared and not implemented in v0.22.0. It is the surface an "
		"AI rule proposer will occupy — read a regulation, draft a Compliance Rule with its target "
		"doctype, thresholds, scope, citation and kairotic gate, mark it `AI-proposed` with the "
		"URL and section it was read from, and leave it DISABLED for a human to check against the "
		"regulation and approve.\n\n"
		"It is declared now so the shape is fixed before anything fills it, and it is inert now "
		"because the architecture it belongs to is explicit about where AI is allowed to be: at "
		"AUTHORING TIME, behind a human approval, and never in the trigger path. At runtime this "
		"app evaluates declarative expressions against record state and nothing else, which is "
		"what lets every alert be traced to a rule, a citation, an approver and a field that "
		"crossed a threshold. Phase 2 of the Configurable Compliance Framework (v0.23.5) wires "
		"this.\n\n"
		"Until then, author rules with create_compliance_rule — a rule is a record, and writing "
		"one takes one call. test_compliance_rule shows what it would raise before you approve it."
	)


# ── the shared reader ───────────────────────────────────────────────────────
def _spec_from_args(args: dict, required: bool, current: dict | None = None) -> dict:
	"""The plain dict `compliance_rules.build_rule` takes, from tool arguments.

	`current` is the existing row on an update, so an argument LEFT OUT means
	"unchanged" rather than "cleared" — which is what somebody moving one
	threshold means, and the opposite of what they would get from a create-shaped
	reader. It is the difference between tuning a rule and silently blanking its
	citation.
	"""
	current = current or {}
	spec: dict = {}

	for fieldname in _TEXT_FIELDS:
		if fieldname in args:
			spec[fieldname] = as_str(args, fieldname)
		elif current:
			spec[fieldname] = current.get(fieldname) or ""
	for fieldname in _INT_FIELDS:
		if fieldname in args:
			spec[fieldname] = as_int(args, fieldname)
		elif current:
			spec[fieldname] = int(current.get(fieldname) or 0)

	for argument, column in _BLOB_FIELDS:
		if argument in args:
			spec[argument] = args.get(argument)
		elif column in args:
			spec[argument] = args.get(column)
		elif current:
			spec[argument] = current.get(column)

	if "regimes" in args:
		spec["regimes"] = _regimes(args.get("regimes"))
	elif current:
		spec["regimes"] = current.get("regimes") or []

	if required:
		for fieldname, what in (
			("title", "a title — a rule nobody can read on a calendar"),
			("target_doctype", "a target_doctype — the DocType whose rows this rule walks"),
			(
				"kairotic_gate_description",
				"a kairotic_gate_description — the sentence saying what STATE makes this rule ripe, "
				"and what keeps it quiet. A rule without one is a calendar reminder",
			),
		):
			if not str(spec.get(fieldname) or "").strip():
				raise ToolError(f"{fieldname} is required: {what}. Nothing was written.")
		if spec.get("target_doctype") and not compat.doctype_exists(str(spec["target_doctype"])):
			raise ToolError(
				f"there is no DocType called {spec['target_doctype']!r} on this site, so this rule "
				"would scan nothing, for ever, quietly. Nothing was written."
			)

	program = str(spec.get("custom_python") or "").strip()
	if program:
		try:
			sandbox.check(program)
		except sandbox.SandboxError as exc:
			raise ToolError(
				f"the sandbox refused this custom_python: {exc}\n\nIt is refused here, while you can "
				"still change it, rather than at two in the morning in a sweep report nobody reads. "
				"If the rule genuinely needs what was refused, it wants a declarative primitive or "
				"a shipped scanner. Nothing was written."
			) from None

	for argument, label in (
		("scope_filters", "scope_filters"),
		("evidence_contract", "evidence_contract"),
		("audit_packet_types", "audit_packet_types"),
		("extra_parameters", "extra_parameters"),
	):
		if argument not in spec:
			continue
		parser = {
			"scope_filters": compliance_rules.parse_filters,
			"evidence_contract": compliance_rules.parse_contract,
			"audit_packet_types": compliance_rules.parse_packet_types,
			"extra_parameters": compliance_rules.as_object,
		}[argument]
		try:
			parser(spec[argument], label)
		except ValueError as exc:
			raise ToolError(f"{exc} Nothing was written.") from None
	return spec


def _regimes(raw) -> list:
	if isinstance(raw, str):
		raw = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
	try:
		return regimes_vocabulary.require(raw or [], "regimes")
	except ValueError as exc:
		raise ToolError(f"{exc} Nothing was written.") from None


def _insert(doc):
	try:
		doc.insert(ignore_permissions=True)
	except ToolError:
		raise
	except Exception as exc:
		raise ToolError(f"{exc}") from None
	return doc


def _headline(described: dict) -> dict:
	return {
		key: described.get(key)
		for key in ("title", "category", "target_doctype", "enabled", "shape", "retention_years")
	}


def _diff(before: dict, after: dict) -> dict:
	"""Field-by-field before → after, for the result and therefore for the log.

	The MCP Action Log records the ARGUMENTS of a call, which on an update is
	only what somebody asked to change. What an auditor wants is what it said
	BEFORE, and this is where that comes from.
	"""
	out = {}
	for key in sorted(set(before) | set(after)):
		was, now = before.get(key), after.get(key)
		if _comparable(was) == _comparable(now):
			continue
		out[key] = {"before": was, "after": now}
	return out


def _comparable(value):
	if isinstance(value, (list, dict)):
		return json.dumps(value, sort_keys=True, default=str)
	return str(value if value is not None else "")


def _calling_user() -> str:
	"""Whoever Frappe authenticated, else the session user, else Administrator."""
	try:
		from ..security import caller_identity

		user = str(caller_identity() or "").strip()
		if user:
			return user
	except Exception:
		pass
	user = str(getattr(frappe.session, "user", "") or "").strip()
	return user if user and user != "Guest" else "Administrator"


def _signature_url(file_token: str) -> str:
	"""The file URL behind a File docname, refusing a token that points at nothing.

	Same reading `normalise_evidence` takes on inspection photographs: evidence
	pointing at nothing is worse than no evidence, because it satisfies a
	contract and proves nothing until an auditor clicks it.
	"""
	token = str(file_token or "").strip()
	if not token:
		return ""
	if not compat.doctype_exists("File"):
		return token
	if not frappe.db.exists("File", token):
		raise ToolError(
			f"approver_signature_file_token points at File {token!r}, which is not on this site. "
			"Upload it with stage_file_chunk and commit_staged_file first. Nothing was written."
		)
	return str(frappe.db.get_value("File", token, "file_url") or token)


def _link_signature(doc, signature: str) -> None:
	"""Attach the signature File to the rule so anyone who can read one reads both."""
	if not (signature and compat.doctype_exists("File")):
		return
	try:
		name = frappe.db.get_value("File", {"file_url": signature}, "name") or (
			signature if frappe.db.exists("File", signature) else ""
		)
		if name:
			frappe.db.set_value(
				"File",
				name,
				{"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name},
				update_modified=False,
			)
	except Exception:  # pragma: no cover - one unlinked file must not fail an approval
		pass


def _alerts_of(rule_id: str) -> int:
	from ..alerts import ALERT_DOCTYPE

	if not (rule_id and compat.doctype_exists(ALERT_DOCTYPE)):
		return 0
	try:
		return int(frappe.db.count(ALERT_DOCTYPE, {"alert_type": rule_id, "dismissed": 0}) or 0)
	except Exception:  # pragma: no cover
		return 0


def rule_list(args: dict) -> dict:
	"""The register behind `list_compliance_rules`, filtered. Shared with calendar.py."""
	limit = min(as_limit(args), compliance_rules.RULE_CAP)
	active = as_bool(args, "active", None)
	include_inactive = active is not True
	rows = compliance_rules.rule_rows(include_inactive=include_inactive, limit=limit)
	described = [compliance_rules.describe(row) for row in rows]

	if active is False:
		described = [entry for entry in described if not entry["enabled"]]
	category = as_str(args, "category")
	if category:
		described = [entry for entry in described if str(entry["category"] or "") == category]
	target = as_str(args, "target_doctype")
	if target:
		described = [entry for entry in described if str(entry["target_doctype"] or "") == target]
	shape = as_str(args, "shape")
	if shape:
		described = [entry for entry in described if entry["shape"] == shape]
	regime = as_str(args, "regime")
	if regime:
		# By TOKEN, never substring — "GlobalGAP" contains "GAP", and a LIKE would
		# put another scheme's rules in a USDA GAP answer.
		try:
			regimes_vocabulary.require([regime], "regime")
		except ValueError as exc:
			raise ToolError(str(exc)) from None
		described = [entry for entry in described if regimes_vocabulary.matches(entry["regimes"], regime)]
	return {"rules": described, "limit": limit, "filtered": bool(category or target or regime or shape)}
