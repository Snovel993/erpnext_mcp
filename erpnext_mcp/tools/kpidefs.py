# SPDX-License-Identifier: MIT
"""Authoring and running financial KPIs: create, update, list, get, compute, refresh.

THESE SEVEN TOOLS ARE THE POINT OF v0.39.0, in the same way the six rule tools
were the point of v0.22.0. Everything else in the release — the doctype, the
engine, the expression sandbox, the seeder, the overnight job — exists so that
these can exist: an operation can add the ratio its own lender asks about, set
the threshold its own covenant uses, and see it on the dashboard the same
afternoon, without a release, a deploy or an engineer.

FOUR PROPERTIES THEY SHARE, AND EACH ONE IS A REFUSAL SOMEWHERE.

  * NOTHING HERE CAN RUN CODE. `create` and `update` accept a built-in
    computer's NAME or an arithmetic EXPRESSION, and the expression is parsed to
    an AST and checked against an allowlist before it is ever stored. There is
    no field on the record that holds Python, and there is no argument on these
    tools that becomes one. That is a deliberate difference from
    `create_compliance_rule`, which does have `custom_python`: a compliance rule
    can need to express a shape no set of fields captures, and a financial KPI
    is a number divided by another number.
  * A DEFINITION IS REFUSED AT SAVE TIME, NOT AT COMPUTE TIME. A built-in that
    names a computer this site has not got, an expression that reads an
    undefined variable, a critical threshold on the wrong side of its warning:
    each is refused with the sentence saying why, at the moment somebody is
    looking at what they typed. The failure this prevents is the quiet one — a
    definition that saves, computes nightly, draws a line, and is wrong in a way
    nothing announces.
  * THE ARITHMETIC OF A LIVE KPI DOES NOT CHANGE UNDER A CHART. `update` says so
    out loud when the formula moves, and says what to do instead: a new kpi_id
    beside the old one, so both series stay readable. Unlike a Compliance Rule
    this record is NOT versioned by copy, and the module docstring of
    `services/kpi_engine.py` argues why — an alert is an event with a definition
    behind it, and a KPI is a line.
  * THE COMPUTE TOOLS ARE READS THAT WARM A CACHE, AND NOTHING ELSE. `compute_kpi`
    and `compute_all_kpis` write `Financial KPI History` rows and touch no
    Account, no GL Entry, no Journal Entry, no Asset and no Field. Every row
    they write is derived state the overnight job would have written anyway, and
    deleting the lot changes no answer they give — only how long it takes.

WHO MAY CALL THEM. The same three roles that hold the normalization register:
System Manager, Accounts Manager, Farm Manager. A KPI definition decides what
number a lender is shown and what threshold raises an alert about it, which is
the same class of authority as approving an add-back to operating cash flow, and
it is a smaller list than the one that reads the calendar those alerts land on.
"""

from __future__ import annotations

import json

import frappe

from .. import compat
from ..args import as_bool, as_int, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from ..services import kpi_engine
from ..services import windowed_reports as windows
from . import employee as employee_tool
from . import kpi as kpi_tools

DOCTYPE = kpi_engine.DOCTYPE

#: Most definitions one list call returns. A site with two hundred KPIs has a
#: problem this cap does not solve, but a cap that is reported beats a register
#: that silently stops somewhere nobody chose.
RECORD_CAP = 200

#: Fields `create` and `update` read straight off the arguments, and the reader
#: each one goes through. ONE TABLE so the two tools cannot drift into accepting
#: different spellings of the same KPI — the failure `tools/rules.py` keeps its
#: own `_TEXT_FIELDS` table to prevent, for the same reason.
_TEXT_FIELDS = (
	"title",
	"description",
	"category",
	"unit",
	"formula_type",
	"builtin_function",
	"expression",
	"default_window_type",
	"default_computation_step",
)

_INT_FIELDS = (
	"default_window_months",
	"historical_lookback_years",
	"display_order",
)

_FLOAT_FIELDS = (
	"threshold_warning_low",
	"threshold_critical_low",
	"threshold_warning_high",
	"threshold_critical_high",
)

_BOOL_FIELDS = (
	"enabled",
	"historical_averaging_enabled",
	"dashboard_visible",
)

#: What a new definition looks like before anybody says otherwise. Every default
#: here is argued on the DocType field it fills in.
_DEFAULTS = {
	"enabled": 1,
	"category": kpi_engine.CATEGORY_CUSTOM,
	"unit": kpi_engine.UNIT_CURRENCY,
	"formula_type": kpi_engine.FORMULA_BUILTIN,
	"default_window_type": kpi_engine.DEFAULT_WINDOW_TYPE,
	"default_window_months": kpi_engine.DEFAULT_WINDOW_MONTHS,
	"default_computation_step": kpi_engine.DEFAULT_STEP,
	"historical_averaging_enabled": 1,
	"historical_lookback_years": kpi_engine.DEFAULT_LOOKBACK_YEARS,
	"dashboard_visible": 1,
	"display_order": 0,
}


def _require() -> None:
	compat.require_doctype(
		DOCTYPE,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app. "
		"Until then the three built-in reports still answer through get_windowed_report; they are "
		"simply not yet editable, extendable or thresholded.",
	)


def _resolve_or_refuse(reference: str) -> dict:
	row = kpi_engine.definition_row(reference)
	if not row:
		raise ToolError(
			f"no {DOCTYPE} called {reference!r} is on this site. It can be given by kpi_id "
			"(`sustainable_cf_per_acre`) or by docname (`KPID-2026-0001`); "
			"list_financial_kpi_definitions has the register. Nothing was changed."
		)
	return row


def _as_float(args: dict, key: str):
	"""One threshold off the arguments, or `None` for absent. EMPTY IS NOT ZERO.

	The distinction is the whole of reading a threshold correctly: an absent
	`threshold_warning_low` means low is not bad for this KPI, and a zero there
	means a negative value is a warning. On a cash-flow KPI those are two very
	different operations, and a reader that coerced absence to zero would turn
	the first silently into the second.
	"""
	raw = args.get(key)
	if raw in (None, ""):
		return None
	try:
		return float(raw)
	except (TypeError, ValueError):
		raise ToolError(
			f"{key} must be a number; got {raw!r}. Leave it out entirely to say that this KPI has "
			"no threshold in that direction — an omitted threshold and a threshold of zero are "
			"different claims. Nothing was changed."
		) from None


def _spec_from_args(args: dict, current: dict | None = None) -> dict:
	"""The field values one create or update writes, validated once for both."""
	spec: dict = {}
	for field in _TEXT_FIELDS:
		value = as_str(args, field)
		if value:
			spec[field] = value
	for field in _INT_FIELDS:
		value = as_int(args, field)
		if value is not None:
			spec[field] = value
	for field in _FLOAT_FIELDS:
		if field in args:
			spec[field] = _as_float(args, field)
	for field in _BOOL_FIELDS:
		value = as_bool(args, field, None)
		if value is not None:
			spec[field] = 1 if value else 0

	company = as_str(args, "company")
	if company:
		spec["company"] = resolve_company(company, required=True)
	elif "company" in args:
		# An explicit empty company is a deliberate widening to every entity, and
		# it has to be distinguishable from not mentioning company at all.
		spec["company"] = ""

	if "expression_inputs" in args:
		spec["expression_inputs"] = _inputs_json(args.get("expression_inputs"))

	merged = {**(current or {}), **spec}

	category = merged.get("category")
	if category and category not in kpi_engine.CATEGORIES:
		raise ToolError(
			f"category must be one of {', '.join(kpi_engine.CATEGORIES)}; got {category!r}. "
			"Custom is the honest answer for a metric that is genuinely one operation's own "
			"question, and it is the default for exactly that reason. Nothing was changed."
		)
	unit = merged.get("unit")
	if unit and unit not in kpi_engine.UNITS:
		raise ToolError(
			f"unit must be one of {', '.join(kpi_engine.UNITS)}; got {unit!r}. It is not "
			"decoration: 0.42 is a catastrophe as a current ratio, a fine margin as a percentage "
			"and a rounding error as dollars. Nothing was changed."
		)
	formula_type = merged.get("formula_type")
	if formula_type and formula_type not in kpi_engine.FORMULA_TYPES:
		raise ToolError(
			f"formula_type must be {' or '.join(kpi_engine.FORMULA_TYPES)}; got {formula_type!r}. "
			"There is no third value and there is deliberately no argument on this tool that "
			"becomes Python: a KPI whose shape is neither of these wants a built-in computer, "
			"which is six lines in services/financial_reports.py with a test. Nothing was changed."
		)

	if formula_type == kpi_engine.FORMULA_BUILTIN:
		named = str(merged.get("builtin_function") or "").strip()
		if not named:
			raise ToolError(
				"a Built-in KPI has to name a builtin_function. This site has: "
				f"{', '.join(kpi_engine.builtin_names()) or '<none>'}. Nothing was changed."
			)
		if named not in windows.COMPUTERS:
			raise ToolError(
				f"{named!r} is not a built-in computer on this site. It has: "
				f"{', '.join(kpi_engine.builtin_names()) or '<none>'}. A definition pointing at a "
				"computer that does not exist produces nothing, for ever, and says nothing about "
				"it — which on a dashboard is indistinguishable from a business with no activity. "
				"Nothing was changed."
			)
	elif formula_type == kpi_engine.FORMULA_EXPRESSION:
		try:
			specs = kpi_engine.parse_inputs(merged.get("expression_inputs"))
			kpi_engine.check_expression(merged.get("expression"), specs.keys())
		except kpi_engine.ExpressionError as exc:
			raise ToolError(f"{exc} Nothing was changed.") from None

	return spec


def _inputs_json(raw) -> str:
	"""`expression_inputs` as the JSON string the field holds, validated first.

	Accepts a dict as well as a JSON string, because a model calling this tool
	will send an object and a person pasting from the Desk will send text, and
	the two must not be admitted on different terms.
	"""
	if raw in (None, ""):
		return ""
	try:
		parsed = kpi_engine.parse_inputs(raw)
	except kpi_engine.ExpressionError as exc:
		raise ToolError(f"{exc} Nothing was changed.") from None
	return json.dumps(parsed, indent=1, sort_keys=True)


def _headline(described: dict) -> dict:
	"""The five fields a summary line and a log row need."""
	return {
		"name": described.get("name"),
		"kpi_id": described.get("kpi_id"),
		"title": described.get("title"),
		"formula_type": described.get("formula_type"),
		"enabled": described.get("enabled"),
	}


def _diff(before: dict, after: dict) -> dict:
	"""Field-by-field, what one update changed. Written into the result so the
	MCP Action Log row carries the before-values — an auditor asking "who changed
	this and what did it say before" reads the answer off this app rather than
	off a git history they have no access to."""
	out = {}
	for field in sorted(set(before) | set(after)):
		if field in ("modified", "creation", "owner"):
			continue
		was = before.get(field)
		now = after.get(field)
		if str(was or "") != str(now or ""):
			out[field] = {"was": was, "now": now}
	return out


# ── 1. create_financial_kpi_definition ──────────────────────────────────────
def create_financial_kpi_definition(args: dict) -> ToolResult:
	"""Define one KPI. Enabled on creation, and that is the argued default.

	A COMPLIANCE RULE IS AUTHORED IN DRAFT AND THIS IS NOT, and the difference is
	what the two things do when they are wrong. A rule that fires wrongly accuses
	somebody of a compliance failure, so nothing goes live unapproved. A KPI that
	is wrong reports a number, on a dashboard, beside its own ingredients and its
	own computation warnings — which is a thing a reader can check, and an
	accusation is not. Pass `enabled=false` to author one quietly.
	"""
	_require()
	actor = kpi_tools.require_kpi_role()

	kpi_id = as_str(args, "kpi_id", required=True).strip()
	if kpi_engine.definition_row(kpi_id):
		raise ToolError(
			f"{kpi_id!r} is already defined on this site. THE KEY IS THE CACHE KEY: every "
			"Financial KPI History row carries it, so two definitions sharing one would write "
			"into each other's series and a single chart line would hold two different numbers "
			"with nothing marking where one stops. update_financial_kpi_definition edits the "
			"existing one; a genuinely different metric wants a different kpi_id. Nothing was "
			"changed."
		)
	if not as_str(args, "title"):
		raise ToolError(
			"title is required. It is what the KPI is called on a dashboard, in the words the "
			"person reading it uses — kpi_id is the key and title is the label. Nothing was "
			"changed."
		)

	spec = _spec_from_args({**_DEFAULTS, **args})
	doc = frappe.new_doc(DOCTYPE)
	doc.kpi_id = kpi_id
	for field, value in spec.items():
		setattr(doc, field, value)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	described = kpi_engine.describe(_resolve_or_refuse(doc.name))
	data = {
		"actor": actor,
		"definition": described,
		"headline": _headline(described),
		"note": (
			"NOTHING HERE IS A NUMBER. A definition holds the QUESTION; every value is computed "
			"from the ledger when somebody asks, cached with the components that produced it, and "
			"derivable again by rerunning the same computation. compute_kpi runs it now; the "
			"overnight job fills its history."
		),
	}
	if not described["thresholds"]["has_any"]:
		data["threshold_note"] = (
			"No thresholds are set, so this KPI reports a value and never raises an alert. That is "
			"a legitimate state for a metric somebody watches by eye, and it is NOT the same as "
			"being inside a line — compute_kpi will say `No thresholds` rather than `OK`. Set them "
			"from this operation's own history, which list_financial_kpi_history has."
		)
	return ToolResult(
		data=data,
		summary=(
			f"defined KPI {described['kpi_id']} ({described['title']}) as {described['formula_type']}"
			+ (f" on {described['builtin_function']}" if described["builtin_function"] else "")
			+ (", enabled" if described["enabled"] else ", disabled")
		),
		docstatus_delta="none → 0 (KPI definition created)",
	)


# ── 2. update_financial_kpi_definition ──────────────────────────────────────
def update_financial_kpi_definition(args: dict) -> ToolResult:
	"""Edit one definition in place, and say so loudly when the arithmetic moved.

	IT EDITS RATHER THAN SUPERSEDING, unlike `update_compliance_rule`, and the
	reason is in `services/kpi_engine.py`: a rule is a definition behind an
	EVENT, and a KPI is a definition behind a LINE. Keeping two live versions of
	one KPI would mean a chart whose points came from two different formulas,
	which is the single most misleading object a finance dashboard can hold.

	SO CHANGING THE ARITHMETIC OF A KPI THAT HAS HISTORY IS REPORTED AS A
	DECISION, with the cached row count in front of the caller. The right move is
	usually a new kpi_id beside the old one; where it genuinely is a correction
	rather than a redefinition, `refresh_kpi_cache(force=true)` rebuilds the
	whole series under the new formula so the line holds one definition again.
	"""
	_require()
	actor = kpi_tools.require_kpi_role()

	reference = as_str(args, "kpi_id") or as_str(args, "name") or as_str(args, "definition", required=True)
	before = _resolve_or_refuse(reference)

	if as_str(args, "new_kpi_id"):
		raise ToolError(
			"a kpi_id cannot be changed. It is the cache key on every Financial KPI History row, "
			"so renaming it orphans the whole series: the old line stays in the table under a name "
			"nothing reads, and the chart starts again from nothing. Create a new definition with "
			"the new key instead, and disable this one — both series then stay readable. Nothing "
			"was changed."
		)

	spec = _spec_from_args(args, current=dict(before))
	if not spec:
		raise ToolError(
			f"nothing to update on {before.get('kpi_id')}. Pass at least one of: title, "
			"description, enabled, company, category, unit, formula_type, builtin_function, "
			"expression, expression_inputs, the window defaults, the four thresholds, "
			"dashboard_visible or display_order. Nothing was changed."
		)

	doc = frappe.get_doc(DOCTYPE, before["name"])
	for field, value in spec.items():
		setattr(doc, field, value)
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	after = _resolve_or_refuse(before["name"])
	described = kpi_engine.describe(after)
	changed = _diff(dict(before), dict(after))

	data = {
		"actor": actor,
		"definition": described,
		"headline": _headline(described),
		"changed": changed,
		"changed_fields": sorted(changed),
		"previous": {field: entry["was"] for field, entry in changed.items()},
	}

	arithmetic = sorted(
		set(changed) & {"formula_type", "builtin_function", "expression", "expression_inputs"}
	)
	if arithmetic:
		cached = _cached_rows(str(after.get("kpi_id")))
		data["arithmetic_note"] = (
			f"THE ARITHMETIC OF THIS KPI CHANGED ({', '.join(arithmetic)}) and there are {cached} "
			f"cached snapshot(s) already filed under {after.get('kpi_id')}. Those rows were "
			"computed by the OLD formula, so the series now holds two definitions of one number on "
			"one line with nothing marking the join — which is exactly what source_version exists "
			"to make visible and exactly what a reader will not notice. Either "
			"refresh_kpi_cache(force=true) to rebuild the whole series under the new formula, or, "
			"if this is a redefinition rather than a correction, undo it and create a new kpi_id "
			"beside this one so both lines stay readable."
			if cached
			else (
				f"The arithmetic of this KPI changed ({', '.join(arithmetic)}). Nothing is cached "
				"under this kpi_id yet, so there is no old series to reconcile — the first "
				"computation will be under the new formula throughout."
			)
		)
	if "enabled" in changed and not described["enabled"]:
		data["disabled_note"] = (
			"This KPI is now disabled. OFF IS NOT DELETED: every cached snapshot it produced is "
			"still there and still charts, and it simply stops being extended and stops raising "
			"threshold alerts."
		)
	return ToolResult(
		data=data,
		summary=(f"updated KPI {described['kpi_id']}: {', '.join(sorted(changed)) or 'no effective change'}"),
		docstatus_delta=f"{len(changed)} field(s) changed on {before['name']}",
	)


def _cached_rows(kpi_key: str) -> int:
	try:
		if not windows.cache_available():
			return 0
		return len(
			frappe.db.get_all(windows.DOCTYPE, filters={"kpi_key": kpi_key}, pluck="name", limit=100000) or []
		)
	except Exception:  # pragma: no cover
		return 0


# ── 3. list_financial_kpi_definitions ───────────────────────────────────────
def list_financial_kpi_definitions(args: dict) -> ToolResult:
	"""The register: what this site computes, how, and what it alerts on."""
	_require()
	actor = kpi_tools.require_kpi_role()
	limit = min(as_limit(args), RECORD_CAP)

	filters: dict = {}
	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)
	category = as_str(args, "category")
	if category:
		if category not in kpi_engine.CATEGORIES:
			raise ToolError(f"category must be one of {', '.join(kpi_engine.CATEGORIES)}; got {category!r}.")
		filters["category"] = category
	formula_type = as_str(args, "formula_type")
	if formula_type:
		if formula_type not in kpi_engine.FORMULA_TYPES:
			raise ToolError(
				f"formula_type must be {' or '.join(kpi_engine.FORMULA_TYPES)}; got {formula_type!r}."
			)
		filters["formula_type"] = formula_type
	enabled = as_bool(args, "enabled", None)
	if enabled is not None:
		filters["enabled"] = 1 if enabled else 0
	if as_bool(args, "dashboard_only", False):
		filters["dashboard_visible"] = 1

	found = kpi_engine.rows(filters, limit=limit + 1)
	if company:
		found = [row for row in found if not row.get("company") or row["company"] == company]
	truncated = len(found) > limit
	found = found[:limit]
	described = [kpi_engine.describe(row) for row in found]

	broken = [item for item in described if item["expression_inputs_error"]]
	data = {
		"actor": actor,
		"company": company,
		"count": len(described),
		"limit": limit,
		"truncated": truncated,
		"definitions": described,
		"by_category": {
			name: [item["kpi_id"] for item in described if item["category"] == name]
			for name in kpi_engine.CATEGORIES
			if any(item["category"] == name for item in described)
		},
		"enabled_count": sum(1 for item in described if item["enabled"]),
		"thresholded_count": sum(1 for item in described if item["thresholds"]["has_any"]),
		"builtin_functions_available": kpi_engine.builtin_names(),
		"note": (
			"A KPI IS A RECORD, WHICH IS THE WHOLE OF v0.39.0. Every field on one is editable "
			"through update_financial_kpi_definition with no code release: the window, the step, "
			"the lookback, the four thresholds, the dashboard order and the switch. What is NOT "
			"editable is the engine — formula_type has exactly two values, both deterministic, "
			"and nothing on this record holds Python."
		),
	}
	if broken:
		data["broken_note"] = (
			f"{len(broken)} definition(s) have expression_inputs that no longer parse: "
			+ ", ".join(item["kpi_id"] for item in broken)
			+ ". Each computes to nothing and says so in its warnings rather than reporting a "
			"zero. `expression_inputs_error` on each says what is wrong."
		)
	if not described:
		data["empty_note"] = (
			"No definitions match. A site that has just migrated has one — Sustainable CF/Acre, "
			"seeded by the install — and create_financial_kpi_definition adds the rest."
		)
	if truncated:
		data["truncation_note"] = (
			f"More than {limit} definition(s) matched and this is the first {limit} in dashboard "
			"order. Narrow with category, formula_type or enabled."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{len(described)} KPI definition(s)"
			+ (f" for {company}" if company else "")
			+ f"; {data['enabled_count']} enabled, {data['thresholded_count']} with thresholds"
		),
	)


# ── 4. get_financial_kpi_definition ─────────────────────────────────────────
def get_financial_kpi_definition(args: dict) -> ToolResult:
	"""One definition in full, with what is cached under it and what it would compute."""
	_require()
	actor = kpi_tools.require_kpi_role()

	reference = as_str(args, "kpi_id") or as_str(args, "name") or as_str(args, "definition", required=True)
	row = _resolve_or_refuse(reference)
	described = kpi_engine.describe(row)

	problems = []
	if described["formula_type"] == kpi_engine.FORMULA_BUILTIN:
		if described["builtin_function"] not in windows.COMPUTERS:
			problems.append(
				f"it names the built-in computer {described['builtin_function']!r}, which is not "
				f"registered on this site. It has: {', '.join(kpi_engine.builtin_names()) or '<none>'}."
			)
	else:
		try:
			specs = kpi_engine.parse_inputs(row.get("expression_inputs"))
			checked = kpi_engine.check_expression(row.get("expression"), specs.keys())
			if checked["unused"]:
				problems.append(
					f"expression_inputs defines {', '.join(checked['unused'])}, which the "
					"expression never reads — usually half of a rename."
				)
		except kpi_engine.ExpressionError as exc:
			problems.append(str(exc))

	data = {
		"actor": actor,
		"definition": described,
		"headline": _headline(described),
		"cached_snapshots": _cached_rows(str(row.get("kpi_id"))),
		"problems": problems,
		"healthy": not problems,
		"reading_it": (
			"A DEFINITION HOLDS THE QUESTION AND NEVER AN ANSWER. Nothing on this record is a "
			"number that came out of the ledger; `thresholds` are lines somebody drew and the "
			"window fields say which period to ask about. compute_kpi runs it and returns the "
			"value with the components that produced it."
		),
	}
	return ToolResult(
		data=data,
		summary=(
			f"{described['kpi_id']} ({described['title']}): {described['formula_type']}, "
			f"{described['default_window_type']} over {described['default_window_months']} month(s), "
			f"{data['cached_snapshots']} cached snapshot(s)"
			+ (f"; {len(problems)} problem(s)" if problems else "")
		),
	)


# ── 5. compute_kpi ──────────────────────────────────────────────────────────
def compute_kpi(args: dict) -> ToolResult:
	"""One defined KPI, over its own window, with its history and its threshold verdict."""
	_require()
	actor = kpi_tools.require_kpi_role()

	reference = as_str(args, "kpi_id") or as_str(args, "name") or as_str(args, "definition", required=True)
	row = _resolve_or_refuse(reference)
	company = resolve_company(as_str(args, "company"), required=True)
	employee_tool.require_company_scope(actor, company)
	if row.get("company") and row["company"] != company:
		raise ToolError(
			f"{row.get('kpi_id')} is defined for {row['company']} and was asked for on {company}. "
			"A definition scoped to one entity means nothing at another — an orchard cost per bin "
			"computed at a family office that owns no trees would be a null on a dashboard every "
			"month for ever. Leave `company` empty on the definition to make it apply everywhere. "
			"Nothing was computed."
		)

	overrides = _overrides(args)
	report = kpi_engine.compute_kpi(row, company, **overrides)
	return ToolResult(data={"actor": actor, **report, "reading_it": _READING_IT}, summary=_summary(report))


_READING_IT = (
	"THREE BLOCKS AND A VERDICT, AND EACH CORRECTS THE OTHERS. `point_in_time` is the period just "
	"finished, which on a farm flatters harvest and demonizes pruning. `window` is the same figure "
	"over the KPI's own window — twelve rolling months unless the definition says otherwise — so "
	"the whole annual cycle is in it exactly once however it is read. `historical_averages` is "
	"what that window has been worth for this operation before, which is the only thing that says "
	"whether the current number is good. `threshold_status` is where it sits against the lines "
	"somebody drew, and `No thresholds` means nobody drew any — which is NOT the same as being "
	"inside them. Read `computation_warnings` before quoting any of it: a partial window and a "
	"full one look identical in the value and are not the same claim."
)


def _summary(report: dict) -> str:
	definition = report.get("definition") or {}
	status = (report.get("threshold_status") or {}).get("status")
	value = report.get("value")
	window = report.get("window") or {}
	if value is None:
		return (
			f"{report.get('company')} {definition.get('title')}: no defensible value "
			f"({len(report.get('computation_warnings') or [])} warning(s)) — read computation_warnings"
		)
	return (
		f"{report.get('company')} {definition.get('title')} {report.get('window_type')} to "
		f"{window.get('period_end')}: {value} {definition.get('unit')} — {status}"
	)


def _overrides(args: dict) -> dict:
	"""The window arguments a caller may put on top of the definition's defaults."""
	out: dict = {}
	as_of = as_str(args, "as_of")
	if as_of:
		out["as_of"] = as_of
	window_type = as_str(args, "window_type")
	if window_type:
		matched = next(
			(option for option in windows.WINDOW_TYPES if option.lower() == window_type.lower()), ""
		)
		if not matched:
			raise ToolError(
				f"window_type must be one of {', '.join(windows.WINDOW_TYPES)}; got {window_type!r}. "
				"Omit it to use the window the definition itself declares, which is the case that "
				"keeps a dashboard, its alerts and its cache agreeing without anybody passing "
				"anything."
			)
		out["window_type"] = matched
	step = as_str(args, "computation_step")
	if step:
		matched = next((option for option in windows.STEPS if option.lower() == step.lower()), "")
		if not matched:
			raise ToolError(f"computation_step must be one of {', '.join(windows.STEPS)}; got {step!r}.")
		out["computation_step"] = matched
	months = as_int(args, "window_months")
	if months is not None:
		if months < 1 or months > 120:
			raise ToolError(f"window_months must be between 1 and 120; got {months}.")
		out["window_months"] = months
	lookback = as_int(args, "historical_lookback_years")
	if lookback is not None:
		if lookback < 0 or lookback > windows.MAX_LOOKBACK_YEARS:
			raise ToolError(
				f"historical_lookback_years must be between 0 and {windows.MAX_LOOKBACK_YEARS}; "
				f"got {lookback}."
			)
		out["historical_lookback_years"] = lookback
	averaging = as_bool(args, "include_historical_averages", None)
	if averaging is not None:
		out["historical_averaging_enabled"] = bool(averaging)
	return out


# ── 6. compute_all_kpis ─────────────────────────────────────────────────────
def compute_all_kpis(args: dict) -> ToolResult:
	"""The whole dashboard in one call: every enabled KPI, in order, with its thresholds.

	ONE CALL RATHER THAN N, for the reason `get_windowed_report` is one tool
	rather than one per report: a framework whose every KPI costs a round trip is
	a framework with six KPIs in it.

	ONE BROKEN DEFINITION DOES NOT EMPTY THE DASHBOARD. Each KPI is computed
	independently and a failure becomes a null value with a warning on that row —
	the same promise the compliance sweep makes about one rule that throws.
	"""
	_require()
	actor = kpi_tools.require_kpi_role()
	company = resolve_company(as_str(args, "company"), required=True)
	employee_tool.require_company_scope(actor, company)

	category = as_str(args, "category")
	if category and category not in kpi_engine.CATEGORIES:
		raise ToolError(f"category must be one of {', '.join(kpi_engine.CATEGORIES)}; got {category!r}.")

	report = kpi_engine.compute_all_kpis(
		company,
		dashboard_only=as_bool(args, "dashboard_only", False),
		category=category,
		include_historical_averages=as_bool(args, "include_historical_averages", None),
		**_overrides(args),
	)
	data = {
		"actor": actor,
		**report,
		"reading_it": (
			"THE `breached` LIST IS THE ONE TO READ FIRST, and an empty one is not the same as a "
			"healthy operation: a KPI with no thresholds reports `No thresholds` and can never "
			"appear there, whatever it is worth. `definition.thresholds.has_any` says which KPIs "
			"are actually being watched. Every value here carries its own "
			"`computation_warnings`, and a null value is an answer — a division nobody could "
			"perform — rather than a zero."
		),
	}
	unwatched = [
		item["definition"]["kpi_id"]
		for item in report["kpis"]
		if not (item.get("definition") or {}).get("thresholds", {}).get("has_any")
	]
	if unwatched:
		data["unwatched_note"] = (
			f"{len(unwatched)} KPI(s) have no thresholds and therefore cannot ever appear in "
			f"`breached`: {', '.join(unwatched)}. That is a legitimate state for a metric somebody "
			"watches by eye; it is worth knowing that nothing is watching it for them."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{company}: {report['kpi_count']} KPI(s) computed, {report['breach_count']} past a "
			"threshold"
			+ (f" ({', '.join(item['kpi_id'] for item in report['breached'])})" if report["breached"] else "")
		),
	)


# ── 7. refresh_kpi_cache ────────────────────────────────────────────────────
def refresh_kpi_cache(args: dict) -> ToolResult:
	"""Fill the cached history for one defined KPI, or for every enabled one.

	MUTATING, AND THE ONLY THING IT CAN MUTATE IS A CACHE — the same promise
	`recompute_kpi_history` makes, extended to KPIs that are records rather than
	code. Every row it writes is what the live computation would have produced
	for that window; every row it deletes comes back on the next read or the next
	overnight run. Nothing here is the only copy of anything.
	"""
	_require()
	actor = kpi_tools.require_kpi_role()
	if not windows.cache_available():
		raise ToolError(
			f"this site has no {windows.DOCTYPE} doctype yet — it ships with erpnext_mcp and "
			"arrives with `bench --site <site> migrate`. Until then every KPI still computes; it "
			"just recomputes on every call and says how much history it had to leave out. Nothing "
			"was changed."
		)

	kpi_id = as_str(args, "kpi_id")
	if kpi_id:
		kpi_id = str(_resolve_or_refuse(kpi_id).get("kpi_id"))

	company = resolve_company(as_str(args, "company"), required=False)
	if company:
		employee_tool.require_company_scope(actor, company)

	back_years = as_int(args, "back_years", kpi_engine.DEFAULT_LOOKBACK_YEARS)
	if back_years is None:
		back_years = kpi_engine.DEFAULT_LOOKBACK_YEARS
	if back_years < 1 or back_years > windows.MAX_LOOKBACK_YEARS:
		raise ToolError(
			f"back_years must be between 1 and {windows.MAX_LOOKBACK_YEARS}; got {back_years}. "
			"Nothing was changed."
		)
	force = as_bool(args, "force", False)

	report = kpi_engine.refresh_kpi_cache(
		kpi_id=kpi_id or "", company=company or "", back_years=back_years, force=bool(force)
	)
	data = {
		"actor": actor,
		**report,
		"note": (
			"THE CACHE IS DERIVABLE AND THAT IS WHY THIS IS SAFE. Every snapshot written is what "
			"the live computation would have produced for that window, and every snapshot cleared "
			"comes back on the next read or the next overnight run. The cost of running this at "
			"the wrong moment is time."
		),
	}
	if force:
		data["force_note"] = (
			f"force=true, so {report['cleared']} existing snapshot(s) were DELETED and rebuilt "
			"rather than filled around. That is what a changed formula needs: an incremental fill "
			"leaves the old rows in place, and a series holding two definitions of one KPI is a "
			"line with an unmarked join in it."
		)
	if report["skipped"]:
		data["skipped_note"] = (
			f"{len(report['skipped'])} (KPI, company) pair(s) were skipped and each says why — a "
			"company with no posted ledger, or a definition whose formula no longer resolves. A "
			"skip is reported rather than counted as a success."
		)
	return ToolResult(
		data=data,
		summary=(
			f"{'rebuilt' if force else 'filled'} {report['written']} cached snapshot(s) across "
			f"{len(report['per_kpi'])} (KPI, company) pair(s), {back_years} year(s) back"
			+ (f"; {report['cleared']} cleared first" if report["cleared"] else "")
		),
		docstatus_delta=(
			f"{report['cleared']} cached snapshot(s) deleted, {report['written']} written"
			if report["cleared"]
			else f"{report['written']} cached snapshot(s) written"
		),
	)
