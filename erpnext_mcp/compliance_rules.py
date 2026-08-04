# SPDX-License-Identifier: MIT
"""Compliance Rule as data: the vocabulary, the readers, and the seeder.

v0.22.0, and it is the change the whole Configurable Compliance Framework rests
on. Until now a compliance rule was a Python function: changing a threshold,
adding a citation, or turning one off for a season meant a code change, a
release and a deploy. Regulations do not move on a release cadence. OR-OSHA
renumbered heat illness from -1130 to -1131; OTCO added a Fraud Prevention Plan
requirement; FDA re-phased FSMA Produce Safety. Every one of those is a data
change wearing a code change's clothes, and this module is where it takes them
off.

WHAT MOVED, AND WHAT DELIBERATELY DID NOT:

  * THE RULE DEFINITIONS ARE NOW RECORDS. Thresholds, scope, citations,
    regimes, message text, the kairotic gate, the switch — all of it is on a
    Compliance Rule row an operator can edit, and every edit is versioned,
    approved and audit-logged.
  * THE ENGINE IS STILL CODE. `alerts/base.py` sweeps, dedups, upserts and
    auto-dismisses exactly as it did in v0.21.0 — none of that is editable and
    none of it should be.
  * RUNTIME IS DETERMINISTIC. The engine reads the current state of the target
    records and evaluates a declarative expression. There is NO model in the
    trigger path — no classifier, no natural-language interpretation, nothing
    probabilistic. AI's role is confined to AUTHORING: proposing a rule
    definition a human then reads, approves and enables. That is what makes an
    alert defensible: for every one an auditor questions, the answer traces to
    a row, its `regulation_citations`, its `human_approved_by` and
    `human_approved_on`, and the specific field that crossed a threshold.

THREE SHAPES OF RULE, AND THE SPLIT IS THE INTERESTING PART:

  DECLARATIVE     the record says everything: target doctype, date field,
                  cadence, thresholds, scope filters, message template. Six of
                  the thirteen shipped rules are this, and a seventh is one
                  primitive away.
  BUILT-IN        the record still owns every tunable, but the SHAPE of the
                  scan — a finding superseded by a later clean record, a child
                  table reduced to its worst row, two date fields that only
                  matter together — is a named scanner that ships with the app,
                  reviewed and tested. Seven of the thirteen.
  CUSTOM PYTHON   a restricted program in a field. ZERO of the thirteen use it.
                  It exists for the rule an operator or an AI proposer writes
                  that the primitives do not reach yet, and every use of it is
                  a request for a primitive — see `docs/configurable_compliance_framework.md`.

That ordering is the design. `custom_python` is an escape hatch, not a
load-bearing wall: it runs in `alerts/sandbox.py`, which is an AST interpreter
rather than an `exec`, and the thing to do with a rule that needs it is to grow
the declarative vocabulary until it does not.

SEEDED, NOT FIXTURED. `test_hooks.py` forbids the word `fixtures` by name, and
for the reason it always has: a Frappe fixture is imported by `bench migrate`
with no ability to skip what a site already has, so an operator who raised a
threshold would have it corrected back on the next upgrade. `seed_compliance_rules`
checks for the rule_id BEFORE it writes, so a rule somebody edited, superseded
or disabled stays that way across every future migrate.
"""

from __future__ import annotations

import json

import frappe

from . import compat
from . import training as regimes_vocabulary

DOCTYPE = "Compliance Rule"

#: The three shapes above, as the value `describe` reports and tests assert on.
SHAPE_DECLARATIVE = "declarative"
SHAPE_BUILTIN = "builtin_scanner"
SHAPE_CUSTOM = "custom_python"

#: What `missing_date_behaviour` may say.
ON_MISSING_SKIP = "Skip"
ON_MISSING_RAISE = "Raise"

#: What `due_date_mode` may say.
DUE_FROM_ANCHOR = "From Anchor"
DUE_TODAY = "Today"
DUE_NONE = "None"

#: What `authored_by` may say. `System` is a rule this app shipped and seeded.
AUTHOR_SYSTEM = "System"
AUTHOR_OPERATOR = "Operator"
AUTHOR_AI = "AI-proposed"

#: Categories a rule's alerts can land under, matching Compliance Alert.
CATEGORIES = (
	"Audits",
	"Certifications",
	"Filings",
	"Housing",
	"Policies",
	"Records",
	"Water and Sanitation",
	"Workforce",
)

SEVERITIES = ("Critical", "Warning", "Info")

#: The scope-filter operators, and what each one means when the column is EMPTY.
#: That last column is the whole reason filters are evaluated in Python rather
#: than pushed into SQL: `status != 'Active'` in SQL excludes every row whose
#: status was never set, and every one of the shipped rules that reads a status
#: treats an unset one as the default. A filter that silently dropped those rows
#: would make a rule quiet on exactly the records nobody has touched.
FILTER_OPS = {
	"eq": "equal to, as text",
	"ne": "not equal to, as text",
	"gt": "greater than — numeric where both sides are numbers, else lexical (which is correct for ISO dates)",
	"lt": "less than, same comparison",
	"gte": "greater than or equal, same comparison",
	"lte": "less than or equal, same comparison",
	"in": "one of a list",
	"nin": "not one of a list",
	"isnull": "the column is empty (value is ignored)",
	"isnotnull": "the column has something in it (value is ignored)",
	"contains": "the value appears in the column, case-insensitively",
	"ncontains": "the value does not appear in the column",
}

#: Ops that take no `value`.
_NULLARY_OPS = ("isnull", "isnotnull")

#: Ops whose `value` must be a list.
_LIST_OPS = ("in", "nin")

#: Evidence-contract keys, shared with Inspection Template sections so a rule's
#: producer task and a template section ask for evidence in one vocabulary.
CONTRACT_KEYS = (
	"photos",
	"signature",
	"findings_text",
	"witness",
	"checklist_items",
	"measurements",
)

#: Most rules one site may have. A rule set larger than this is not a rule set,
#: it is an import that went wrong, and the sweep would spend its night on it.
RULE_CAP = 200

#: Longest a `rule_id` may be. It is the first segment of every alert docname
#: and `alert_key` has 140 characters to fit the whole thing into.
MAX_RULE_ID = 60

#: The fields `rule_rows` reads. Named rather than `*` so a column added in a
#: later version cannot silently change what the sweep is reading.
RULE_FIELDS = (
	"name",
	"rule_id",
	"title",
	"category",
	"enabled",
	"version",
	"superseded_by",
	"active_row_flag",
	"target_doctype",
	"requires_doctypes",
	"requires_fields",
	"builtin_scanner",
	"custom_python",
	"date_field",
	"cadence_days",
	"window_field",
	"missing_date_behaviour",
	"due_date_mode",
	"threshold_critical_days",
	"threshold_warning_days",
	"severity_critical",
	"severity_warning",
	"severity_expired",
	"scope_filters_json",
	"extra_parameters_json",
	"message_template",
	"regimes_from_field",
	"producer_task_template",
	"producer_farm_task_type",
	"producer_skill_required",
	"evidence_contract_json",
	"regulation_citations",
	"retention_years",
	"audit_packet_types",
	"kairotic_gate_description",
	"purpose",
	"authored_by",
	"ai_source_citation",
	"human_approved_by",
	"human_approved_on",
	"approver_employee",
	"approver_signature",
	"modified",
	"owner",
)


# ── JSON blobs, parsed the same way everywhere ──────────────────────────────
def as_object(raw, label: str = "the JSON object") -> dict:
	"""A JSON object from a text column, or a ValueError naming the column."""
	if raw in (None, ""):
		return {}
	if isinstance(raw, dict):
		return dict(raw)
	try:
		parsed = json.loads(raw)
	except Exception as exc:
		raise ValueError(f"{label} is not valid JSON: {exc}") from None
	if parsed in (None, ""):
		return {}
	if not isinstance(parsed, dict):
		raise ValueError(f"{label} must be a JSON object, got {type(parsed).__name__}.")
	return parsed


def as_list(raw, label: str) -> list:
	"""A JSON list from a text column, or a ValueError naming the column."""
	if raw in (None, ""):
		return []
	if isinstance(raw, (list, tuple)):
		return list(raw)
	try:
		parsed = json.loads(raw)
	except Exception as exc:
		raise ValueError(f"{label} is not valid JSON: {exc}") from None
	if parsed in (None, ""):
		return []
	if not isinstance(parsed, list):
		raise ValueError(f"{label} must be a JSON list, got {type(parsed).__name__}.")
	return parsed


def parse_filters(raw, label: str = "scope_filters") -> list:
	"""Validate a scope-filter list, or refuse saying which entry and why.

	REFUSED AT AUTHORING TIME, which is the whole point of doing it here: a
	filter with a typo'd operator asks for nothing and looks like it asks for
	something, and discovering that from an empty calendar three weeks later is
	the failure this app is written against.
	"""
	out = []
	for index, entry in enumerate(as_list(raw, label)):
		if not isinstance(entry, dict):
			raise ValueError(
				f'{label}[{index}] must be an object like {{"field": "status", "op": "eq", '
				f'"value": "Active"}}, got {type(entry).__name__}.'
			)
		field = str(entry.get("field") or "").strip()
		if not field:
			raise ValueError(f"{label}[{index}] names no `field`.")
		op = str(entry.get("op") or "eq").strip().lower()
		if op not in FILTER_OPS:
			raise ValueError(
				f"{label}[{index}] uses operator {op!r}, which is not one of: "
				f"{', '.join(sorted(FILTER_OPS))}."
			)
		value = entry.get("value")
		if op in _LIST_OPS:
			if not isinstance(value, (list, tuple)):
				raise ValueError(f"{label}[{index}] uses `{op}`, whose `value` must be a list.")
			value = list(value)
		elif op in _NULLARY_OPS:
			value = None
		row = {"field": field, "op": op, "value": value}
		if "default" in entry:
			row["default"] = entry["default"]
		out.append(row)
	return out


def parse_contract(raw, label: str = "evidence_contract") -> dict:
	"""An evidence contract, refusing any key outside the shared vocabulary."""
	contract = as_object(raw, label)
	unknown = [key for key in contract if key not in CONTRACT_KEYS]
	if unknown:
		raise ValueError(
			f"{label} names {', '.join(repr(key) for key in sorted(unknown))}, which no producer "
			f"task understands. The vocabulary is: {', '.join(CONTRACT_KEYS)}. A key outside it "
			'asks for nothing and looks like it asks for something — {"photo": true} is the one '
			"that catches everybody."
		)
	return contract


def parse_packet_types(raw, label: str = "audit_packet_types") -> list:
	"""Audit packet keys, checked against the packets this app can actually build."""
	values = [str(entry or "").strip() for entry in as_list(raw, label)]
	values = [entry for entry in values if entry]
	try:
		from . import audit_packets

		known = set(audit_packets.names())
	except Exception:  # pragma: no cover - a site mid-import
		known = set()
	if known:
		unknown = [entry for entry in values if entry not in known]
		if unknown:
			raise ValueError(
				f"{label} names packet type(s) {', '.join(repr(entry) for entry in unknown)}, which "
				f"this app does not build. The packets are: {', '.join(sorted(known))}. A packet "
				"name nobody generates is a claim that an alert reaches a document it never reaches."
			)
	return values


# ── the filter engine ───────────────────────────────────────────────────────
def row_matches(row: dict, filters: list, present_fields: set | None = None) -> tuple:
	"""Does one row pass every filter? Returns (matched, warnings).

	IN PYTHON, NOT IN SQL, and that is a decision rather than an oversight. Three
	of the shipped rules read a column whose EMPTY value means something specific
	— a Compliance Policy with no status counts as Active, a Regulatory Filing
	with no status is neither Draft nor Withdrawn, a Housing Unit with no
	condition is not Uninhabitable — and every one of those rows is excluded by
	the equivalent SQL comparison, silently. Reading `default` here reproduces
	the legacy Python exactly, and says out loud what the legacy `or "Active"`
	said in an idiom.

	A filter naming a field the site has not got is SKIPPED AND REPORTED rather
	than failing the row. Half this app's compliance columns are installed on
	demand, and a rule that refused every row on a site that had not run
	`install_compliance_fields` would look exactly like a clean operation.
	"""
	warnings = []
	for entry in filters or []:
		field = entry["field"]
		if present_fields is not None and field not in present_fields:
			warnings.append(
				f"scope filter on {field!r} was skipped: this site's target doctype has no such "
				"column. The rule ran without it, so it scanned MORE rows than it was scoped to, "
				"not fewer."
			)
			continue
		value = row.get(field)
		if value in (None, "") and "default" in entry:
			value = entry["default"]
		if not _passes(value, entry["op"], entry.get("value")):
			return False, warnings
	return True, warnings


def _passes(value, op: str, wanted) -> bool:
	if op == "isnull":
		return not str(value or "").strip()
	if op == "isnotnull":
		return bool(str(value or "").strip())

	text = str(value if value is not None else "")
	if op == "eq":
		return text == str(wanted if wanted is not None else "")
	if op == "ne":
		return text != str(wanted if wanted is not None else "")
	if op == "in":
		return text in [str(entry if entry is not None else "") for entry in wanted or []]
	if op == "nin":
		return text not in [str(entry if entry is not None else "") for entry in wanted or []]
	if op == "contains":
		return str(wanted or "").lower() in text.lower()
	if op == "ncontains":
		return str(wanted or "").lower() not in text.lower()

	left, right = _comparable(value, wanted)
	if left is None or right is None:
		return False
	if op == "gt":
		return left > right
	if op == "lt":
		return left < right
	if op == "gte":
		return left >= right
	if op == "lte":
		return left <= right
	return False  # pragma: no cover - parse_filters refused everything else


def _comparable(left, right):
	"""Both sides as numbers where both are numeric, else both as text.

	Text is the right fallback because the values that reach an ordering
	comparison in a compliance rule are overwhelmingly ISO dates, and ISO dates
	sort correctly as strings. A comparison this cannot make answers False, which
	excludes the row rather than raising — a rule that died on one malformed cell
	would take the whole night's sweep of that rule with it.
	"""
	try:
		return float(left), float(right)
	except (TypeError, ValueError):
		pass
	if left is None or right is None:
		return None, None
	return str(left), str(right)


# ── reading the rule set ────────────────────────────────────────────────────
def rule_rows(include_inactive: bool = False, limit: int = RULE_CAP) -> list:
	"""Every Compliance Rule row, live ones by default.

	LIVE MEANS ENABLED AND UNSUPERSEDED, and the two conditions are separate on
	purpose. A superseded row is history — the definition an alert was raised
	under. A disabled row is a decision somebody made this season. Neither runs;
	both stay readable.
	"""
	if not compat.doctype_exists(DOCTYPE):
		return []
	filters = {}
	if not include_inactive:
		filters = {"enabled": 1, "superseded_by": ("in", ("", None))}
	rows = frappe.db.get_all(
		DOCTYPE,
		filters=filters,
		fields=compat.existing_fields(DOCTYPE, RULE_FIELDS),
		order_by="rule_id asc, version asc",
		limit=limit,
	)
	return [dict(row) for row in rows or []]


def rule_row(name: str) -> dict:
	"""One rule by docname, with its regimes attached. Raises ValueError if absent."""
	if not compat.doctype_exists(DOCTYPE):
		raise ValueError(
			f"this site has no {DOCTYPE} DocType, which ships with erpnext_mcp — run "
			"`bench --site <site> migrate`."
		)
	name = str(name or "").strip()
	if not name or not frappe.db.exists(DOCTYPE, name):
		raise ValueError(
			f"no Compliance Rule called {name!r} on this site. list_compliance_rules has the "
			"register, and it accepts a rule_id as well as a docname."
		)
	row = dict(
		frappe.db.get_value(DOCTYPE, name, compat.existing_fields(DOCTYPE, RULE_FIELDS), as_dict=True) or {}
	)
	row["regimes"] = regimes_of(name)
	return row


def resolve(reference: str) -> str:
	"""A docname from a docname OR a rule_id, preferring the live version.

	Somebody asking about `training_expiring` today means whichever version is
	live, and making them look the docname up first would make every tool call a
	two-step. A docname is still accepted and still means that exact version,
	which is what somebody reading history is after.
	"""
	reference = str(reference or "").strip()
	if not reference or not compat.doctype_exists(DOCTYPE):
		return ""
	if frappe.db.exists(DOCTYPE, reference):
		return reference
	live = frappe.db.get_all(
		DOCTYPE,
		filters={"rule_id": reference, "superseded_by": ("in", ("", None))},
		fields=["name"],
		order_by="version desc",
		limit=1,
	)
	if live:
		return str(live[0]["name"])
	any_version = frappe.db.get_all(
		DOCTYPE, filters={"rule_id": reference}, fields=["name"], order_by="version desc", limit=1
	)
	return str(any_version[0]["name"]) if any_version else ""


def regimes_of(name: str) -> list:
	"""The regime tokens on one rule, read off its child table."""
	try:
		return regimes_vocabulary.rows_for_parents(DOCTYPE, [name], "regimes").get(name, [])
	except Exception:  # pragma: no cover - a site mid-migration
		return []


def shape_of(row: dict) -> str:
	"""Which of the three shapes this row is. The order is the precedence."""
	if str(row.get("custom_python") or "").strip():
		return SHAPE_CUSTOM
	if str(row.get("builtin_scanner") or "").strip():
		return SHAPE_BUILTIN
	return SHAPE_DECLARATIVE


def names_list(raw) -> list:
	"""A comma/newline separated column as a list of trimmed names."""
	out = []
	for chunk in str(raw or "").replace("\n", ",").split(","):
		entry = chunk.strip()
		if entry:
			out.append(entry)
	return out


def describe(row: dict, with_definition: bool = False) -> dict:
	"""One rule as a tool reports it.

	The keys `list_compliance_rules` returned before v0.22.0 are all here and
	mean what they always meant — `alert_type`, `title`, `category`, `purpose`,
	`kairotic_gate`, `framework`, `regimes`, `requires`, `available`. Everything
	new is additive, because a client that read the old shape has to go on
	working: this app's own iOS build reads that list.
	"""
	requires = names_list(row.get("requires_doctypes")) or (
		[str(row.get("target_doctype") or "")] if row.get("target_doctype") else []
	)
	out = {
		"alert_type": row.get("rule_id"),
		"title": row.get("title"),
		"category": row.get("category"),
		"purpose": str(row.get("purpose") or ""),
		"kairotic_gate": str(row.get("kairotic_gate_description") or ""),
		"framework": str(row.get("regulation_citations") or ""),
		"regimes": row.get("regimes") if row.get("regimes") is not None else regimes_of(row.get("name")),
		"requires": requires,
		"available": all(compat.doctype_exists(doctype) for doctype in requires)
		and all(
			compat.has_field(row.get("target_doctype"), field)
			for field in names_list(row.get("requires_fields"))
		),
		# v0.22.0 additions.
		"name": row.get("name"),
		"rule_id": row.get("rule_id"),
		"enabled": bool(compat.checked(row.get("enabled"))),
		"version": int(row.get("version") or 1),
		"superseded_by": str(row.get("superseded_by") or "") or None,
		"shape": shape_of(row),
		"target_doctype": row.get("target_doctype"),
		# The same text as `framework` above, under the name the DocType and the
		# tools use. Both, because `framework` is the key every client since
		# v0.19.2 reads and `regulation_citations` is the field somebody editing
		# a rule passes — and a reader who had to know that those are one thing
		# would be reading a trap.
		"regulation_citations": str(row.get("regulation_citations") or ""),
		"authored_by": row.get("authored_by"),
		"human_approved_by": row.get("human_approved_by"),
		"human_approved_on": str(row.get("human_approved_on") or "") or None,
		"retention_years": int(row.get("retention_years") or 0) or None,
	}
	if with_definition:
		out["definition"] = {
			"date_field": row.get("date_field"),
			"cadence_days": int(row.get("cadence_days") or 0),
			"window_field": row.get("window_field"),
			"missing_date_behaviour": row.get("missing_date_behaviour") or ON_MISSING_SKIP,
			"due_date_mode": row.get("due_date_mode") or DUE_FROM_ANCHOR,
			"threshold_critical_days": int(row.get("threshold_critical_days") or 0),
			"threshold_warning_days": int(row.get("threshold_warning_days") or 0),
			"severity_critical": row.get("severity_critical") or "Critical",
			"severity_warning": row.get("severity_warning") or "Warning",
			"severity_expired": row.get("severity_expired") or "Critical",
			"scope_filters": _quietly(parse_filters, row.get("scope_filters_json")),
			"extra_parameters": _quietly(as_object, row.get("extra_parameters_json"), {}),
			"message_template": row.get("message_template"),
			"regimes_from_field": row.get("regimes_from_field"),
			"builtin_scanner": row.get("builtin_scanner"),
			"custom_python": row.get("custom_python"),
			"requires_fields": names_list(row.get("requires_fields")),
			"producer_task_template": row.get("producer_task_template"),
			"producer_farm_task_type": row.get("producer_farm_task_type"),
			"producer_skill_required": row.get("producer_skill_required"),
			"evidence_contract": _quietly(parse_contract, row.get("evidence_contract_json"), {}),
			"audit_packet_types": _quietly(parse_packet_types, row.get("audit_packet_types")),
			"ai_source_citation": row.get("ai_source_citation"),
			"approver_employee": row.get("approver_employee"),
			"approver_signature": row.get("approver_signature"),
		}
	return out


def _quietly(parser, raw, fallback=None):
	"""Parse for a READ. A malformed blob on a stored row must not break a list."""
	try:
		return parser(raw)
	except Exception:
		return [] if fallback is None else fallback


# ── writing one ─────────────────────────────────────────────────────────────
#: Every field `build_rule` copies off a plain dict, and the default where the
#: spec is silent. One table so the seeder, `create_compliance_rule` and
#: `update_compliance_rule` cannot drift into writing different shapes of row.
RULE_DEFAULTS = {
	"rule_id": "",
	"title": "",
	"category": "Records",
	"enabled": 0,
	"version": 1,
	"target_doctype": "",
	"requires_doctypes": "",
	"requires_fields": "",
	"builtin_scanner": "",
	"custom_python": "",
	"date_field": "",
	"cadence_days": 0,
	"window_field": "",
	"missing_date_behaviour": ON_MISSING_SKIP,
	"due_date_mode": DUE_FROM_ANCHOR,
	"threshold_critical_days": 30,
	"threshold_warning_days": 90,
	"severity_critical": "Critical",
	"severity_warning": "Warning",
	"severity_expired": "Critical",
	"message_template": "",
	"regimes_from_field": "",
	"producer_task_template": None,
	"producer_farm_task_type": "",
	"producer_skill_required": "",
	"regulation_citations": "",
	"retention_years": 0,
	"kairotic_gate_description": "",
	"purpose": "",
	"authored_by": AUTHOR_OPERATOR,
	"ai_source_citation": "",
	"human_approved_by": None,
	"human_approved_on": None,
	"approver_employee": None,
	"approver_signature": "",
}


def build_rule(spec: dict):
	"""One unsaved Compliance Rule from a plain dict.

	Shared by the seeder and by the MCP tools, so a rule this app ships and one
	somebody authors through MCP are the same shape of record — including the
	parts a seeder could quietly have skipped.
	"""
	doc = frappe.new_doc(DOCTYPE)
	for fieldname, default in RULE_DEFAULTS.items():
		value = spec.get(fieldname, default)
		if isinstance(default, str) and value is not None and not isinstance(value, str):
			value = str(value)
		if isinstance(default, str):
			value = str(value or "").strip()
		doc.set(fieldname, value)
	doc.scope_filters_json = json.dumps(
		parse_filters(spec.get("scope_filters", spec.get("scope_filters_json")))
	)
	doc.extra_parameters_json = json.dumps(
		as_object(spec.get("extra_parameters", spec.get("extra_parameters_json")), "extra_parameters")
	)
	doc.evidence_contract_json = json.dumps(
		parse_contract(spec.get("evidence_contract", spec.get("evidence_contract_json")))
	)
	doc.audit_packet_types = json.dumps(
		parse_packet_types(spec.get("audit_packet_types", spec.get("audit_packet_types_json")))
	)
	for regime in regimes_vocabulary.to_rows(spec.get("regimes") or []):
		doc.append("regimes", dict(regime))
	return doc


# ── the migration of the thirteen ───────────────────────────────────────────
def seed_specs() -> list:
	"""The thirteen shipped rules, as Compliance Rule specs.

	DERIVED FROM THE RULE OBJECTS THEMSELVES rather than restated beside them.
	`alerts/rules.py` holds one `register(Rule(...))` per rule with its title,
	category, purpose, kairotic gate, framework citation, regimes and required
	doctypes, and one `shape(...)` saying how that rule migrates. This function
	joins the two. A rule whose gate prose is edited in `rules.py` therefore
	seeds with the edited prose, and there is no second copy to go stale — which
	matters more here than anywhere else in the app, because the gate and the
	citation are the two fields an auditor reads.
	"""
	from .alerts import rules as shipped

	# The producer half comes from `ALERT_TASK_MAP`, which has been the single
	# definition of "what work answers this alert" since v0.18.0 and is what
	# `generate_tasks_from_compliance_alerts` reads. Copying it onto the rule
	# rather than restating it means the two cannot disagree about which skill a
	# water sample needs — and it is what makes `producer_*` on the record
	# meaningful from the first migrate rather than empty until somebody fills
	# it in.
	try:
		from .tools.dispatch import ALERT_TASK_MAP
	except Exception:  # pragma: no cover - a partial import during a migrate
		ALERT_TASK_MAP = {}

	out = []
	for key in sorted(shipped.RULES):
		rule = shipped.RULES[key]
		shape = dict(shipped.SHAPES.get(key) or {})
		recipe = ALERT_TASK_MAP.get(key) or {}
		spec = {
			"rule_id": rule.key,
			"title": rule.title,
			"category": rule.category,
			"purpose": rule.purpose,
			"kairotic_gate_description": rule.kairotic_gate,
			"regulation_citations": rule.framework,
			"regimes": list(rule.regimes),
			"requires_doctypes": ", ".join(rule.requires),
			"producer_farm_task_type": str(recipe.get("task_type") or ""),
			"producer_skill_required": str(recipe.get("skill") or ""),
			"evidence_contract": dict(recipe.get("evidence") or {}),
			"authored_by": AUTHOR_SYSTEM,
			"enabled": 1,
		}
		spec.update(shape)
		out.append(spec)
	return out


def seed_compliance_rules(approver: str = "") -> dict:
	"""One Compliance Rule per shipped rule. Idempotent, and never raises.

	CHECKED BY `rule_id`, AND AN EDITED RULE IS LEFT ALONE. The check is "does
	ANY row hold this rule_id" and not "does a live row" — deliberately, and it
	is the difference between this and a Frappe fixture. A rule somebody disabled
	because their operation does not do it that way stays disabled. One somebody
	superseded with their own version 2 does not get version 1 seeded back beside
	it every migrate, which would give the rule_id two live rows and make the
	sweep's answer depend on sort order.

	`approver` is who the seeded rules are approved by. It has to be somebody,
	because `enabled` is refused without an approver and a seeded rule that
	arrived disabled would silently turn the whole compliance calendar off on
	upgrade — which is the one migration outcome worse than a noisy one.

	NEVER RAISES. It runs inside `bench migrate`, where an exception aborts the
	migration for the whole bench.
	"""
	report = {"created": [], "present": [], "failed": []}
	if not compat.doctype_exists(DOCTYPE):
		return report
	approver = approver or _seed_approver()
	stamped = frappe.utils.now()
	for spec in seed_specs():
		rule_id = spec["rule_id"]
		try:
			if frappe.db.exists(DOCTYPE, {"rule_id": rule_id}):
				report["present"].append(rule_id)
				continue
			doc = build_rule(
				{
					**spec,
					"human_approved_by": approver,
					"human_approved_on": stamped,
				}
			)
			doc.insert(ignore_permissions=True)
			report["created"].append(rule_id)
		except Exception as exc:  # pragma: no cover - reported, never raised
			report["failed"].append({"name": rule_id, "reason": f"{type(exc).__name__}: {exc}"})
	return report


def _seed_approver() -> str:
	"""Who a migrated rule is approved by: Administrator, which is the truth.

	A rule this app shipped was reviewed by whoever reviewed the release, and the
	site has no record of that person. Attributing it to Administrator says "the
	system installed this" and is checkable; inventing a name would be the one
	thing a provenance field must never do. `authored_by = System` on the same
	row is what distinguishes it from a rule a human here actually read.
	"""
	try:
		if frappe.db.exists("User", "Administrator"):
			return "Administrator"
	except Exception:  # pragma: no cover
		pass
	return "Administrator"
