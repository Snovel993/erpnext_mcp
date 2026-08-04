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

  DECLARATIVE     the record says everything: target doctype, date field(s),
                  cadence, thresholds, scope filters, gates, supersession,
                  heuristics, message template. ELEVEN of the thirteen shipped
                  rules are this since v0.22.1, which added the four primitives
                  v0.22.0's notes said the built-ins were waiting for.
  BUILT-IN        the record still owns every tunable, but the SHAPE of the
                  scan is a named scanner that ships with the app, reviewed and
                  tested. TWO of the thirteen, and both are PERMANENT rather
                  than pending: `audit_action_overdue` folds a child table to
                  its worst row, which is an aggregation rather than a filter,
                  and `supervisor_review_lapsed` walks a table of doctypes on a
                  clock that measures days ELAPSED where every other rule's
                  thresholds mean days REMAINING.
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

#: v0.22.1. What `date_field_role` may say, and it is the smallest of the new
#: fields and the one that unlocked the two supersession rules.
#:
#: `Clock` is every rule up to now: the date is a DEADLINE, and how far away it
#: is decides the severity band. `Timestamp` says the date is WHEN THE THING WAS
#: FOUND — a finding is not more or less true for being three weeks old, so the
#: date is read for the message and for the supersession test and bands nothing.
#: Every matching row raises `severity_expired`, and a row with no date raises
#: too, because a corrective action nobody dated is still open.
DATE_ROLE_CLOCK = "Clock"
DATE_ROLE_TIMESTAMP = "Timestamp"

#: v0.22.5. The third role, and the one that made `default_severity` necessary.
#:
#: `State` says the rule fires on a DATA STATE rather than on any distance from a
#: date. Its gates decide whether it fires; the date field is read for the
#: message and bands nothing; and the severity is `default_severity` rather than
#: any of the three band severities, because a rule with no clock has no band to
#: be in and no expiry to be past.
#:
#: IT IS A THIRD VALUE AND NOT A FOURTH THRESHOLD, and the distinction is the
#: whole reason this field exists rather than a convention. `threshold_*_days`
#: are Int columns, so "no threshold" and "a threshold of zero" are one value in
#: the database — and zero is a real setting meaning "fire on the due date
#: itself". Nothing the engine can read off the numbers distinguishes a rule with
#: no clock from a rule whose clock is tight, so the rule has to SAY so.
#:
#: v0.22.1 refused a third `date_field_role` value deliberately, and this is not
#: the one it refused. What it refused was a role that inverted the SIGN of every
#: threshold beside it — a number meaning days elapsed where the same number on
#: twelve other rules means days remaining. `State` reads no thresholds at all,
#: so there is no number left to misread.
DATE_ROLE_STATE = "State"

#: v0.22.1. Where `gate_date_field` is read from. `Direct` is a column on the
#: row being scanned; `Latest Related` is the newest date on another doctype
#: that points back at it, which is what "when was this block last sprayed"
#: looks like on a site keeping a spray log rather than a column.
GATE_DIRECT = "Direct"
GATE_LATEST_RELATED = "Latest Related"

#: v0.22.5. The comparisons a threshold condition may make. Deliberately a
#: SHORTER list than `FILTER_OPS`: a threshold is a number read against a number,
#: and `contains` on a temperature is a question nobody meant to ask.
THRESHOLD_OPS = ("gte", "gt", "lte", "lt", "eq", "ne")

#: v0.22.5. Where a threshold condition may read its NUMBER from, instead of
#: carrying a literal on the rule.
#:
#: A CLOSED REGISTRY, and it is closed for the same reason `custom_python` runs
#: in an interpreter this app wrote: "read a number from somewhere" is one
#: sentence away from "read anything from anywhere". Each key names one setting
#: this app already resolves per company, and nothing else is reachable.
#:
#: THE POINT IS THE PER-COMPANY OVERRIDE. OR-OSHA's heat threshold is 80 °F, and
#: an entity that has decided its own is 75 sets it once on Weather Settings —
#: where the v0.19.4 shift sweep already reads it. A literal on the rule would
#: make the alert layer and the operational layer disagree about what "hot" means
#: on the same afternoon, on the same shift, and the disagreement would be
#: invisible until somebody compared two records.
THRESHOLD_SOURCES = {
	"weather.heat_threshold_temp_f": (
		"the ambient-temperature heat threshold on Weather Settings, per company (default 80 °F)"
	),
	"weather.heat_threshold_heat_index_f": (
		"the heat-index threshold on Weather Settings, per company (default 80 °F)"
	),
	"weather.wind_threshold_mph_spray_block": (
		"the spray-block wind threshold on Weather Settings, per company (default 10 mph)"
	),
}

#: What `latest_child_field_threshold.match` may say.
MATCH_ANY = "any"
MATCH_ALL = "all"

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

#: v0.22.5. What a `State` rule raises at when its record says nothing.
#:
#: WARNING RATHER THAN CRITICAL, deliberately. A state-driven rule fires the
#: moment a condition becomes true, which for the shipped one is the first
#: reading at or above the threshold — and a threshold cross is a heads-up, not
#: a finding that something has stopped being lawful. Critical belongs to
#: sustained exposure and to worker-reported symptoms, which are different rules
#: watching different facts. A default of Critical would put every hot afternoon
#: at the top of a board where "Critical" is supposed to mean somebody stops what
#: they are doing.
SEVERITY_DEFAULT = "Warning"

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
	"istrue": "a CHECK BOX is ticked (value is ignored) — never `isnotnull`, because a Check read back before the database layer holds the string '0', which every truthiness test calls true",
	"isfalse": "a check box is not ticked (value is ignored)",
	"contains": "the value appears in the column, case-insensitively",
	"ncontains": "the value does not appear in the column",
}

#: Ops that take no `value`.
_NULLARY_OPS = ("isnull", "isnotnull", "istrue", "isfalse")

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
	"target_doctypes_json",
	"date_field",
	"date_field_role",
	"date_fields_json",
	"cadence_days",
	"window_field",
	"missing_date_behaviour",
	"due_date_mode",
	"gate_date_field",
	"gate_within_days",
	"gate_scope",
	"gate_related_table_json",
	"latest_child_field_threshold_json",
	"superseded_by_later_clean_json",
	"regime_heuristics_json",
	"category_heuristics_json",
	"threshold_critical_days",
	"threshold_warning_days",
	"severity_critical",
	"severity_warning",
	"severity_expired",
	"default_severity",
	"scope_filters_json",
	"extra_parameters_json",
	"message_template",
	"regimes_from_field",
	"producer_task_template",
	"producer_farm_task_type",
	"producer_skill_required",
	"producer_assigned_to_expression",
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


# ── v0.22.1: the primitives that took five scanners declarative ─────────────
#
# Each parser below refuses AT AUTHORING TIME for the same reason `parse_filters`
# does: every one of these blobs asks for nothing when it is wrong and looks like
# it asks for something, and finding that out from a quiet calendar three weeks
# later is the failure this app is written against.


def parse_target_doctypes(raw, label: str = "target_doctypes") -> list:
	"""The doctypes one rule walks, where it walks more than one.

	`target_doctype` is singular by design and stays the default. This exists
	because ONE shipped rule is genuinely about two record types —
	`housing_corrective_action_open` reads Housing Inspection and Detector Test —
	and splitting it into two rule_ids would split one afternoon's walk round the
	camp across two alert types and change every alert docname on every site.

	Each entry: `doctype` (required), `date_field` (defaults to the rule's), and
	`label`, which is the fragment the message template says instead of the
	doctype name — "the habitability inspection" reads like the errand it is and
	"Housing Inspection" reads like a table.
	"""
	out = []
	for index, entry in enumerate(as_list(raw, label)):
		if isinstance(entry, str):
			entry = {"doctype": entry}
		if not isinstance(entry, dict):
			raise ValueError(
				f'{label}[{index}] must be an object like {{"doctype": "Detector Test", '
				f'"date_field": "test_date", "label": "the detector test"}}, got {type(entry).__name__}.'
			)
		doctype = str(entry.get("doctype") or "").strip()
		if not doctype:
			raise ValueError(f"{label}[{index}] names no `doctype`.")
		out.append(
			{
				"doctype": doctype,
				"date_field": str(entry.get("date_field") or "").strip(),
				"label": str(entry.get("label") or "").strip(),
			}
		)
	return out


def parse_date_fields(raw, label: str = "date_fields") -> list:
	"""Several anchors of the SAME kind, where either being stale fires the rule.

	`housing_detector_test_stale` is the case: a cabin has a smoke detector and a
	CO detector, they are tested independently, and an alert saying only "a
	detector is overdue" sends somebody to test the wrong one. So each entry
	carries a `label`, and the message enumerates the stale ones by name.

	The label is a FRAGMENT, not a sentence — "smoke" and "CO", because the
	template around it supplies "detector". A rule whose labels read as sentences
	produces a message that reads as a list.
	"""
	out = []
	for index, entry in enumerate(as_list(raw, label)):
		if isinstance(entry, str):
			entry = {"field": entry}
		if not isinstance(entry, dict):
			raise ValueError(
				f'{label}[{index}] must be an object like {{"field": "co_detector_last_test", '
				f'"label": "CO"}}, got {type(entry).__name__}.'
			)
		field = str(entry.get("field") or "").strip()
		if not field:
			raise ValueError(f"{label}[{index}] names no `field`.")
		out.append({"field": field, "label": str(entry.get("label") or field).strip()})
	return out


def parse_supersession(raw, label: str = "superseded_by_later_clean") -> dict:
	"""The one gate in the engine that is a question about OTHER rows.

	A finding stops being true when a LATER CLEAN RECORD FOR THE SAME SUBJECT
	supersedes it: a cabin re-inspected in September with nothing found says more
	about July's water stain than a checkbox does, and it requires nobody to
	remember a field. No filter on the finding's own row can answer that.

	  subject_field             what the two records have in common (`unit`, `source`)
	  date_field                the date "later" is measured on; defaults to the target's
	  doctype                   where to look for the clean record; defaults to the target
	  clean_state_field         the column that says a record found nothing
	  clean_state_values        the values of it that count as clean
	  unreadable_counts_as_dirty  default TRUE: a record whose state is empty or
	                            unreadable does NOT supersede. A result nobody can
	                            interpret is not evidence that the water is safe,
	                            and treating it as clean is how a compliance file
	                            becomes a clean record of nothing.
	"""
	config = as_object(raw, label)
	if not config:
		return {}
	subject = str(config.get("subject_field") or "").strip()
	if not subject:
		raise ValueError(
			f"{label} names no `subject_field`, so there is nothing for a later record to be "
			"'about the same thing' as. It is the column the finding and the clean record share — "
			"`unit` on a camp record, `source` on a water test."
		)
	state_field = str(config.get("clean_state_field") or "").strip()
	if not state_field:
		raise ValueError(f"{label} names no `clean_state_field`, so no record can be read as clean.")
	values = [
		str(entry or "").strip()
		for entry in as_list(config.get("clean_state_values"), f"{label}.clean_state_values")
	]
	values = [entry for entry in values if entry]
	if not values:
		raise ValueError(
			f"{label} lists no `clean_state_values`. With none, NOTHING supersedes and the rule can "
			"only ever be silenced by hand — which is the behaviour this primitive exists to remove."
		)
	return {
		"doctype": str(config.get("doctype") or "").strip(),
		"subject_field": subject,
		"date_field": str(config.get("date_field") or "").strip(),
		"clean_state_field": state_field,
		"clean_state_values": values,
		"unreadable_counts_as_dirty": bool(config.get("unreadable_counts_as_dirty", True)),
	}


def parse_gate_table(raw, label: str = "gate_related_table") -> dict:
	"""Where a `Latest Related` gate date is read from.

	`{doctype, subject_field, date_field, scope_filters}` — the newest `date_field`
	on a row of `doctype` whose `subject_field` points back at the row being
	scanned. `scope_filters` narrows which of those rows count, in the same
	vocabulary as the rule's own filters.

	`subject_key` is the column on the SCANNED row that the related rows name,
	and it defaults to `name`, which is what a Link column holds.
	"""
	config = as_object(raw, label)
	if not config:
		return {}
	out = {"subject_key": str(config.get("subject_key") or "name").strip() or "name"}
	for key in ("doctype", "subject_field", "date_field"):
		value = str(config.get(key) or "").strip()
		if not value:
			raise ValueError(
				f"{label} names no `{key}`. A related-table gate needs all three of doctype, "
				"subject_field and date_field, or it reads nothing and silences the rule entirely."
			)
		out[key] = value
	out["scope_filters"] = parse_filters(config.get("scope_filters"), f"{label}.scope_filters")
	return out


# ── v0.22.5: the gate about the LATEST ROW of a child table ─────────────────
def parse_latest_child_threshold(raw, label: str = "latest_child_field_threshold") -> dict:
	"""The newest child row of each scanned record, and a number read off it.

	SIBLING OF `gate_related_table`, NOT AN EXTENSION OF IT, and the difference is
	the fold rather than the syntax. `gate_related_table` folds a related doctype
	to ONE VALUE PER SUBJECT — the maximum date — and asks how old it is. This one
	folds to ONE ROW per subject, the latest, and then asks a question about its
	OTHER columns. A maximum over dates cannot answer "and what was the
	temperature on that row", because the answer is not a maximum of anything: the
	85 °F reading at noon says nothing about compliance at four o'clock if a 72 °F
	reading was written at half past three.

	It is also what puts the whole row into the message template, under
	`context_key`. An alert that says a threshold was crossed and cannot say WHAT
	the reading was is an alert somebody has to go and look up before they can act
	on it.

	  child_doctype     where the rows live — a child table's own doctype
	  parent_field      the column on the child naming the scanned row. `parent`
	                    by default, which is what a child table uses.
	  parentfield       which child table of the parent, where the doctype is used
	                    as more than one. Optional; narrows nothing when empty.
	  subject_key       the column on the SCANNED row the children name. `name`.
	  order_by          the column "latest" is measured on. REQUIRED — without it
	                    there is no latest and the rule would read whichever row
	                    the database handed back first, which is a rule whose
	                    answer changes when somebody adds an index.
	  context_key       what the message template calls the row. `latest_child`.
	  match             `any` (the default, an OR over the conditions) or `all`.
	  conditions        [{field, op, threshold | threshold_source}]
	  scope_filters     which child rows count at all, in the rule's own filter
	                    vocabulary.

	A SUBJECT WITH NO CHILD ROW IS GATED OUT, the same direction `gate_date_field`
	takes and for the same reason. A shift whose weather timeline is empty is not
	a shift that is cool; it is a shift nobody has a reading for, and raising a
	heat alert off no reading would be this app asserting a fact it does not have.
	"""
	config = as_object(raw, label)
	if not config:
		return {}

	child = str(config.get("child_doctype") or "").strip()
	if not child:
		raise ValueError(
			f"{label} names no `child_doctype`, so there are no rows to find the latest of. It is "
			"the doctype the child rows are — `Farm Shift Weather Reading`, not `Farm Shift`."
		)
	order_by = str(config.get("order_by") or "").strip()
	if not order_by:
		raise ValueError(
			f"{label} names no `order_by`, so there is no LATEST row — the rule would read whichever "
			"of a shift's thirty readings the database handed back first, and that is an answer that "
			"changes when somebody adds an index. Name the column time runs on."
		)

	raw_conditions = config.get("conditions")
	if raw_conditions in (None, "", []) and "field" in config:
		# The flat single-condition shape, because most gates are one comparison
		# and making somebody write a one-element list for it is a paper cut on
		# the commonest case.
		raw_conditions = [
			{
				"field": config.get("field"),
				"op": config.get("op"),
				"threshold": config.get("threshold"),
				"threshold_source": config.get("threshold_source"),
			}
		]
	conditions = []
	for index, entry in enumerate(as_list(raw_conditions, f"{label}.conditions")):
		if not isinstance(entry, dict):
			raise ValueError(
				f'{label}.conditions[{index}] must be an object like {{"field": "temp_f", "op": '
				f'"gte", "threshold": 80}}, got {type(entry).__name__}.'
			)
		field = str(entry.get("field") or "").strip()
		if not field:
			raise ValueError(f"{label}.conditions[{index}] names no `field`.")
		op = str(entry.get("op") or "gte").strip().lower()
		if op not in THRESHOLD_OPS:
			raise ValueError(
				f"{label}.conditions[{index}] uses operator {op!r}, which is not one of: "
				f"{', '.join(THRESHOLD_OPS)}. A threshold is a number read against a number."
			)
		source = str(entry.get("threshold_source") or "").strip()
		if source and source not in THRESHOLD_SOURCES:
			raise ValueError(
				f"{label}.conditions[{index}] reads its number from {source!r}, which is not a "
				f"setting this app resolves. The sources are: {', '.join(sorted(THRESHOLD_SOURCES))}."
			)
		has_literal = entry.get("threshold") not in (None, "")
		if not (source or has_literal):
			raise ValueError(
				f"{label}.conditions[{index}] names neither `threshold` nor `threshold_source`, so "
				"there is no number to compare against. A condition with no number matches nothing "
				"and looks like it matches something."
			)
		threshold = None
		if has_literal:
			try:
				threshold = float(entry["threshold"])
			except (TypeError, ValueError):
				raise ValueError(
					f"{label}.conditions[{index}] has a `threshold` of {entry['threshold']!r}, which "
					"is not a number."
				) from None
		conditions.append({"field": field, "op": op, "threshold": threshold, "threshold_source": source})
	if not conditions:
		raise ValueError(
			f"{label} lists no `conditions`. A gate that tests nothing gates NOTHING IN — every row "
			"with a child record would fire, which on an open-shift rule is every crew on the farm."
		)

	match = str(config.get("match") or MATCH_ANY).strip().lower()
	if match not in (MATCH_ANY, MATCH_ALL):
		raise ValueError(
			f"{label}.match is {match!r}; it is {MATCH_ANY!r} (an OR over the conditions, the "
			f"default) or {MATCH_ALL!r} (an AND)."
		)

	return {
		"child_doctype": child,
		"parent_field": str(config.get("parent_field") or "parent").strip() or "parent",
		"parentfield": str(config.get("parentfield") or "").strip(),
		"subject_key": str(config.get("subject_key") or "name").strip() or "name",
		"order_by": order_by,
		"context_key": str(config.get("context_key") or "latest_child").strip() or "latest_child",
		"match": match,
		"conditions": conditions,
		"scope_filters": parse_filters(config.get("scope_filters"), f"{label}.scope_filters"),
	}


def threshold_from_source(source: str, company: str = ""):
	"""One number out of `THRESHOLD_SOURCES`, for one company. None where unreadable.

	LAZILY IMPORTED, because the settings this reaches live in a service that
	imports the shift tools, and this module is imported by the DocType
	controller. None on failure rather than a raise: a rule whose threshold could
	not be read falls back to the literal on the condition, which is the number
	the regulation says — and a sweep that died because a Single had not migrated
	would take the whole calendar with it.
	"""
	key = str(source or "").strip()
	if key not in THRESHOLD_SOURCES:
		return None
	namespace, _, fieldname = key.partition(".")
	if namespace != "weather":
		return None  # pragma: no cover - the registry has one namespace today
	try:
		from .services import weather

		return float(weather.thresholds_for(str(company or ""))[fieldname])
	except Exception:
		return None


#: The two outcome vocabularies `parse_heuristics` understands, and the key each
#: one's default is written under.
HEURISTIC_OUTCOMES = {
	"regimes": ("then_regimes", "default_regimes"),
	"category": ("then_category", "default_category"),
}

#: The matchers a heuristic entry may use. `if_field_contains` is a substring
#: read of a name; `if_field_in` is exact membership of a closed list.
HEURISTIC_MATCHERS = ("if_field_contains", "if_field_in")


def parse_heuristics(raw, label: str, outcome: str = "regimes") -> list:
	"""An ORDERED lookup table, as data. First match wins, and the order is the content.

	`regimes_from_field` copies tags off a column. This is for the case where
	there is no column — only a NAME TO READ. A certificate's regimes come from
	its type through an ordered eleven-row needle table, and the ordering is the
	whole content: `globalgap` must be checked before `gap`, because "GlobalGAP"
	contains "GAP" and a USDA GAP packet must not be handed another scheme's
	certificate.

	THE FIELD ORDER IS THE OUTER LOOP. Where entries name more than one field,
	every entry is tried against the first-named field before ANY entry is tried
	against the second. That is not an implementation detail: a certificate typed
	"Food Safety Certificate" and named "WPS refresher" is a food-safety
	certificate, and a table that scanned entry-first would retag it from a word
	somebody typed on the day. The type is consulted first and the name is the
	fallback, exactly as the Python it replaces did.

	```json
	[
	  {"if_field_contains": {"field": ["cert_type", "cert_name"], "value": ["wps", "worker protection"]},
	   "then_regimes": ["WPS"]},
	  {"if_field_contains": {"field": ["cert_type", "cert_name"], "value": "globalgap"},
	   "then_regimes": ["GlobalGAP"]},
	  {"default_regimes": ["Internal"]}
	]
	```
	"""
	if outcome not in HEURISTIC_OUTCOMES:
		raise ValueError(
			f"{label}: unknown outcome {outcome!r}."
		)  # pragma: no cover - callers pass constants
	then_key, default_key = HEURISTIC_OUTCOMES[outcome]
	out = []
	for index, entry in enumerate(as_list(raw, label)):
		if not isinstance(entry, dict):
			raise ValueError(
				f'{label}[{index}] must be an object like {{"if_field_contains": {{"field": "cert_type", '
				f'"value": "wps"}}, "{then_key}": [...]}}, got {type(entry).__name__}.'
			)
		if default_key in entry:
			out.append({default_key: _heuristic_outcome(entry[default_key], outcome, label, index)})
			continue
		matcher = next((key for key in HEURISTIC_MATCHERS if key in entry), "")
		if not matcher:
			raise ValueError(
				f"{label}[{index}] has no matcher and no {default_key}. Each entry either tests "
				f"something ({', '.join(HEURISTIC_MATCHERS)}) or is the fallback."
			)
		test = entry[matcher]
		if not isinstance(test, dict):
			raise ValueError(f"{label}[{index}].{matcher} must be an object with `field` and `value`.")
		fields = test.get("field")
		fields = [fields] if isinstance(fields, str) else list(fields or [])
		fields = [str(field or "").strip() for field in fields]
		fields = [field for field in fields if field]
		if not fields:
			raise ValueError(f"{label}[{index}].{matcher} names no `field`.")
		values = test.get("value")
		values = [values] if isinstance(values, (str, int, float)) else list(values or [])
		values = [str(value or "").strip() for value in values]
		values = [value for value in values if value]
		if not values:
			raise ValueError(
				f"{label}[{index}].{matcher} names no `value`. An entry that matches on nothing "
				"matches EVERYTHING that reaches it, which is a fallback wearing a test's clothes."
			)
		if then_key not in entry:
			raise ValueError(f"{label}[{index}] tests something but says no `{then_key}`.")
		# NORMALISED INTO THE VOCABULARY IT WAS WRITTEN IN, not into an internal
		# shape. Every one of these parsers has to accept its own output, because
		# the column is parsed on save, stored, and parsed again on every sweep —
		# a parser whose output it cannot read is a rule that validates once and
		# then quietly stops matching anything, which is the worst failure
		# available to a compliance rule. It also means an operator reading the
		# stored JSON reads the same words the documentation uses.
		out.append(
			{
				matcher: {
					"field": fields,
					"value": values,
					"case_insensitive": bool(test.get("case_insensitive", True)),
				},
				then_key: _heuristic_outcome(entry[then_key], outcome, label, index),
			}
		)
	return out


def _heuristic_outcome(value, outcome: str, label: str, index: int):
	if outcome == "category":
		text = str(value or "").strip()
		if text and text not in CATEGORIES:
			raise ValueError(
				f"{label}[{index}] produces category {text!r}, which is not one of: "
				f"{', '.join(CATEGORIES)}. A category nothing groups by puts the alert in a "
				"section of the calendar nobody opens."
			)
		return text
	values = [value] if isinstance(value, str) else list(value or [])
	try:
		return regimes_vocabulary.require(values, f"{label}[{index}]")
	except ValueError as exc:
		raise ValueError(str(exc)) from None


def match_heuristics(entries: list, row: dict, outcome: str = "regimes"):
	"""Evaluate a parsed heuristic table against one row. See `parse_heuristics`."""
	then_key, default_key = HEURISTIC_OUTCOMES[outcome]
	tests = []
	for entry in entries:
		matcher = next((key for key in HEURISTIC_MATCHERS if key in entry), "")
		if matcher:
			tests.append((matcher, entry[matcher], entry.get(then_key)))
	order: list = []
	for _matcher, test, _outcome in tests:
		for field in test["field"]:
			if field not in order:
				order.append(field)
	# THE FIELD ORDER IS THE OUTER LOOP. See `parse_heuristics`: the whole table
	# is tried against the type before any of it is tried against the name.
	for field in order:
		for matcher, test, produced in tests:
			if field in test["field"] and _heuristic_hit(matcher, test, row.get(field)):
				return produced
	for entry in entries:
		if default_key in entry:
			return entry[default_key]
	return None


def _heuristic_hit(matcher: str, test: dict, value) -> bool:
	text = str(value if value is not None else "")
	needles = list(test["value"])
	if test.get("case_insensitive", True):
		text = text.lower()
		needles = [needle.lower() for needle in needles]
	if not text:
		return False
	if matcher == "if_field_in":
		return text in needles
	return any(needle in text for needle in needles)


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
	# CHECK BOXES GO THROUGH `compat.checked` AND NOTHING ELSE. A Check field read
	# back before it has been through the database layer carries the STRING "0",
	# which `isnotnull` — and every other truthiness test — calls true. On the rule
	# that uses this the wrong answer is "this shed is a worker facility", which
	# puts a building nobody sleeps in on the camp's inspection list and, worse,
	# would have put a real bunkhouse off it had the sense been the other way.
	if op == "istrue":
		return compat.checked(value)
	if op == "isfalse":
		return not compat.checked(value)

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


def passes_threshold(value, op: str, threshold) -> bool:
	"""One numeric comparison, for `latest_child_field_threshold`. NUMBERS ONLY.

	Separate from `_passes` and deliberately narrower. `_passes` compares
	numerically where both sides are numbers and falls back to a LEXICAL
	comparison otherwise, which is right for its callers — the values reaching an
	ordering comparison in a scope filter are overwhelmingly ISO dates, and ISO
	dates sort correctly as strings.

	IT IS EXACTLY WRONG ON A THERMOMETER. A reading whose column somebody filled
	in as "warm", or that an import wrote as "n/a", is lexically greater than
	"80" — and would raise a heat alert off a word. A value that is not a number
	answers False here, which leaves the row alone: this app does not know how hot
	it was, and not knowing is not the same as knowing it was hot.
	"""
	try:
		left, right = float(value), float(threshold)
	except (TypeError, ValueError):
		return False
	if op == "gte":
		return left >= right
	if op == "gt":
		return left > right
	if op == "lte":
		return left <= right
	if op == "lt":
		return left < right
	if op == "eq":
		return left == right
	if op == "ne":
		return left != right
	return False  # pragma: no cover - parse_latest_child_threshold refused everything else


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
		# v0.22.5, and both are here rather than only in `definition` because both
		# change what somebody reading a LIST of rules should expect. `state_driven`
		# says this rule's alerts do not move with the calendar; the expression says
		# its producer task lands on one named person rather than in a skill pool.
		"date_field_role": row.get("date_field_role") or DATE_ROLE_CLOCK,
		"state_driven": str(row.get("date_field_role") or "") == DATE_ROLE_STATE,
		"default_severity": row.get("default_severity") or SEVERITY_DEFAULT,
		"producer_assigned_to_expression": str(row.get("producer_assigned_to_expression") or "") or None,
	}
	if with_definition:
		out["definition"] = {
			"date_field": row.get("date_field"),
			"date_field_role": row.get("date_field_role") or DATE_ROLE_CLOCK,
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
			# v0.22.1's primitives. NULLABLE AND ADDITIVE: a rule that uses none of
			# them reports empties here, and every key `get_compliance_rule` returned
			# before still means what it meant.
			"target_doctypes": _quietly(parse_target_doctypes, row.get("target_doctypes_json")),
			"date_fields": _quietly(parse_date_fields, row.get("date_fields_json")),
			"superseded_by_later_clean": _quietly(
				parse_supersession, row.get("superseded_by_later_clean_json"), {}
			),
			"gate_date_field": row.get("gate_date_field"),
			"gate_within_days": int(row.get("gate_within_days") or 0),
			"gate_scope": row.get("gate_scope") or GATE_DIRECT,
			"gate_related_table": _quietly(parse_gate_table, row.get("gate_related_table_json"), {}),
			# v0.22.5.
			"latest_child_field_threshold": _quietly(
				parse_latest_child_threshold, row.get("latest_child_field_threshold_json"), {}
			),
			"default_severity": row.get("default_severity") or SEVERITY_DEFAULT,
			"regime_heuristics": _quietly(
				lambda raw: parse_heuristics(raw, "regime_heuristics", "regimes"),
				row.get("regime_heuristics_json"),
			),
			"category_heuristics": _quietly(
				lambda raw: parse_heuristics(raw, "category_heuristics", "category"),
				row.get("category_heuristics_json"),
			),
			"message_template": row.get("message_template"),
			"regimes_from_field": row.get("regimes_from_field"),
			"builtin_scanner": row.get("builtin_scanner"),
			"custom_python": row.get("custom_python"),
			"requires_fields": names_list(row.get("requires_fields")),
			"producer_task_template": row.get("producer_task_template"),
			"producer_farm_task_type": row.get("producer_farm_task_type"),
			"producer_skill_required": row.get("producer_skill_required"),
			"producer_assigned_to_expression": row.get("producer_assigned_to_expression"),
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
	"date_field_role": DATE_ROLE_CLOCK,
	"cadence_days": 0,
	"window_field": "",
	"missing_date_behaviour": ON_MISSING_SKIP,
	"due_date_mode": DUE_FROM_ANCHOR,
	"gate_date_field": "",
	"gate_within_days": 0,
	"gate_scope": GATE_DIRECT,
	"threshold_critical_days": 30,
	"threshold_warning_days": 90,
	"severity_critical": "Critical",
	"severity_warning": "Warning",
	"severity_expired": "Critical",
	"default_severity": SEVERITY_DEFAULT,
	"message_template": "",
	"regimes_from_field": "",
	"producer_task_template": None,
	"producer_farm_task_type": "",
	"producer_skill_required": "",
	"producer_assigned_to_expression": "",
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


#: v0.22.1's four primitives (and the two helpers they needed), as
#: (spec key, column, parser). One table so the seeder, `create_compliance_rule`,
#: `update_compliance_rule`, the controller and `describe` cannot drift into
#: validating different things — the drift that would let a malformed blob
#: through one door and be refused at another.
_PRIMITIVE_BLOBS = (
	("target_doctypes", "target_doctypes_json", parse_target_doctypes),
	("date_fields", "date_fields_json", parse_date_fields),
	("superseded_by_later_clean", "superseded_by_later_clean_json", parse_supersession),
	("gate_related_table", "gate_related_table_json", parse_gate_table),
	(
		"latest_child_field_threshold",
		"latest_child_field_threshold_json",
		parse_latest_child_threshold,
	),
	(
		"regime_heuristics",
		"regime_heuristics_json",
		lambda raw, label="regime_heuristics": parse_heuristics(raw, label, "regimes"),
	),
	(
		"category_heuristics",
		"category_heuristics_json",
		lambda raw, label="category_heuristics": parse_heuristics(raw, label, "category"),
	),
)


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
	# v0.22.1. Each of these is written as `[]` / `{}` rather than left NULL where
	# the spec is silent, so a rule that does not use a primitive says so in the
	# same shape as one that does — an operator reading two rules side by side
	# should not have to know that a blank column and an empty list are one thing.
	for argument, column, parser in _PRIMITIVE_BLOBS:
		doc.set(column, json.dumps(parser(spec.get(argument, spec.get(column)))))
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


# ── v0.22.5: rules that are ONLY records ────────────────────────────────────
#: Rules this app seeds that have NO shipped Python scanner behind them.
#:
#: THE FIRST OF THEM, AND THAT IS THE POINT OF THE RELEASE. Every rule up to now
#: existed as a `Rule` object in `alerts/rules.py` first and became a record
#: second, because every one of them predates the framework. This one was
#: authored as a record, in the vocabulary, and there is nothing to fall back to.
#: A site that has the app and has not yet migrated the Compliance Rule DocType
#: therefore does not run it — which is correct and is said out loud rather than
#: papered over: the fallback rule set is the DEFINITIONS OF v0.21.0, and a rule
#: invented afterwards is not one of them.
#:
#: It also means this list is the shape an AI-proposed rule will arrive in when
#: `propose_compliance_rule` is wired: a dict of declarative fields, a citation,
#: a kairotic gate, and nothing executable anywhere in it.
def declarative_seed_specs() -> list:
	from .services import weather

	return [
		{
			"rule_id": "shift_heat_threshold_crossed",
			"title": "An open shift's latest weather reading has crossed OR-OSHA's heat threshold",
			"category": "Workforce",
			"target_doctype": "Farm Shift",
			"requires_doctypes": "Farm Shift, Farm Shift Weather Reading",
			# THE SHIFT'S OWN START, read for the message and for nothing else. The
			# role below is what says so.
			"date_field": "start_datetime",
			"date_field_role": DATE_ROLE_STATE,
			"default_severity": "Warning",
			"due_date_mode": DUE_NONE,
			# Belt and braces against a site that edits the role back to Clock: a
			# negative threshold is the documented way to say "this band never
			# fires", so even misread the rule cannot start banding on a date.
			"threshold_critical_days": -1,
			"threshold_warning_days": -1,
			"cadence_days": 0,
			"scope_filters": [
				# `end_datetime` IS THE FACT AND `status` IS THE SUMMARY OF IT, which
				# is why v0.19.4's own sweep filters on the first. Both are here
				# because both are true of an open shift and ANDing them is strictly
				# narrower — but the status filter carries a `default` of Active, so
				# a shift written by an import that never set the column is still
				# read as open rather than silently dropped off the hot-day list.
				{"field": "end_datetime", "op": "isnull"},
				{"field": "status", "op": "eq", "value": "Active", "default": "Active"},
			],
			"latest_child_field_threshold": {
				"child_doctype": "Farm Shift Weather Reading",
				"parent_field": "parent",
				"parentfield": "weather_timeline",
				"subject_key": "name",
				"order_by": "reading_datetime",
				"context_key": "latest_weather",
				"match": MATCH_ANY,
				"conditions": [
					{
						"field": "temp_f",
						"op": "gte",
						"threshold": weather.DEFAULTS["heat_threshold_temp_f"],
						"threshold_source": "weather.heat_threshold_temp_f",
					},
					{
						"field": "heat_index_f",
						"op": "gte",
						"threshold": weather.DEFAULTS["heat_threshold_heat_index_f"],
						"threshold_source": "weather.heat_threshold_heat_index_f",
					},
				],
			},
			"message_template": (
				"Heat threshold crossed on {{ row.name }} at {{ row.location }} — latest reading "
				"{{ latest_weather.temp_f }}°F ({{ latest_weather.heat_index_f }}°F heat index) at "
				"{{ latest_weather.reading_datetime }}. Document water/shade/rest breaks per "
				"OAR 437-004-1131."
			),
			"regimes": ["OR-OSHA"],
			"regulation_citations": "OAR 437-004-1131 heat illness prevention",
			"retention_years": 3,
			"audit_packet_types": ["OSHA"],
			# NO Inspection Template and NO skill. The assignee is not a pool; it is
			# the one person who was standing there.
			"producer_task_template": None,
			"producer_farm_task_type": "Compliance-Audit",
			"producer_skill_required": "",
			"producer_assigned_to_expression": "row.foreman",
			"extra_parameters": {
				"producer_task_what": "Document the water, shade and rest cycle the crew took"
			},
			"evidence_contract": {"findings_text": True, "signature": True},
			"purpose": (
				"OR-OSHA -1131 does not ask whether it was hot; it asks what the operation DID about "
				"it, shift by shift. The weather timeline already proves the conditions. What an "
				"inspection turns on is whether somebody wrote down the water, the shade and the "
				"rest cycle while the crew was still in the field — and the person who can write "
				"that is the foreman who called them."
			),
			"kairotic_gate_description": (
				"Fires on a WEATHER FACT, not a date. When the latest reading on an open shift is at "
				"or above the OR-OSHA heat threshold — currently 80°F on the ambient thermometer or "
				"heat index — the foreman gets a task to document the water, shade and rest cycle "
				"their crew took. Not a compliance decision by the app; the record is the foreman's, "
				"signed by the foreman, and this alert is what makes sure it exists. Silences by "
				"itself when the shift closes: an open shift on a hot day is a compliance "
				"obligation, a closed shift on the same hot day is a completed one."
			),
			"authored_by": AUTHOR_SYSTEM,
			"enabled": 1,
		},
		# ── v0.23.0: field report awaiting dispatch ────────────────────────
		{
			"rule_id": "field_flag_awaiting_dispatch",
			"title": "A field-reported task has sat in Available for more than 24 hours without being claimed",
			"category": "Workforce",
			"target_doctype": "Farm Task",
			"requires_doctypes": "Farm Task",
			"date_field": "reported_at",
			"date_field_role": DATE_ROLE_CLOCK,
			"default_severity": SEVERITY_DEFAULT,
			"due_date_mode": DUE_FROM_ANCHOR,
			"threshold_critical_days": -1,
			"threshold_warning_days": 1,
			"cadence_days": 0,
			"scope_filters": [
				{"field": "origin", "op": "eq", "value": "field_reported"},
				{"field": "state", "op": "eq", "value": "Available"},
			],
			"message_template": (
				"Field report {{ row.name }} ('{{ row.task_name }}') has been in Available "
				"state for more than 24 hours since {{ row.reported_at }}. A worker flagged "
				"a problem and nobody has claimed the task. Review and either dispatch "
				"somebody or dismiss the report with a reason."
			),
			"regimes": [],
			"regulation_citations": "",
			"retention_years": 1,
			"audit_packet_types": [],
			"producer_task_template": None,
			"producer_farm_task_type": None,
			"producer_skill_required": "",
			"producer_assigned_to_expression": "",
			"extra_parameters": {},
			"evidence_contract": {"findings_text": True},
			"purpose": (
				"A field report is a worker saying 'I see a problem'. If nobody claims the "
				"resulting task within 24 hours, the report is either being ignored or was "
				"never seen — and an ignored field report teaches the crew that reporting "
				"problems is a waste of their time, which is the fastest way to lose the "
				"eyes this whole feature exists to recruit."
			),
			"kairotic_gate_description": (
				"Fires when a field-reported task (origin = field_reported) has been in "
				"Available state for more than 24 hours. The clock starts at reported_at — "
				"when the worker tapped 'Report a problem' — not at creation, which may "
				"differ by the queue delay. Silences when the task is claimed, completed, "
				"or cancelled."
			),
			"authored_by": AUTHOR_SYSTEM,
			"enabled": 1,
		},
	]


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
	try:
		specs = seed_specs() + declarative_seed_specs()
	except Exception as exc:  # pragma: no cover - a site mid-import
		report["failed"].append({"name": "declarative_seed_specs", "reason": f"{type(exc).__name__}: {exc}"})
		specs = seed_specs()
	for spec in specs:
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
