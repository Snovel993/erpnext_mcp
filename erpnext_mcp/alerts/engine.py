# SPDX-License-Identifier: MIT
"""Turning a Compliance Rule record into something the sweep can run.

THE SWEEP DID NOT CHANGE IN v0.22.0 AND THAT IS THE POINT. `base.py` still walks
a set of `Rule` objects, still asks each one the same question — *is this
condition true right now* — still keys each alert on the rule and the record and
nothing that moves daily, still auto-dismisses what it did not observe. All that
changed is WHERE THE RULE SET COMES FROM: it used to be a dict populated by
`register()` at import time, and it is now assembled from Compliance Rule rows.

This module is the assembly. It reads a row and hands back a `Rule` whose `scan`
is one of three things:

  DECLARATIVE   a closure over the row, evaluated here: query the target
                doctype, apply the scope filters, compute the days remaining
                against the cadence anchor, pick the severity band, render the
                message. Deterministic, bounded, and identical every time for
                identical data.
  BUILT-IN      the shipped scanner the row names, called with the row so it
                can read the row's own thresholds and citations. The tunables
                are still data; only the shape of the join is code.
  CUSTOM        the row's `custom_python`, run by `sandbox.py`.

NO MODEL RUNS HERE. There is no classifier, no embedding, no natural-language
interpretation of anything at sweep time. A rule fires because a date crossed a
threshold or a column matched a filter, and the report can name which. That is
what makes an alert defensible in front of somebody who is allowed to ask.

FALLING BACK IS DELIBERATE AND IS NOT A SILENT FALLBACK. On a site that has the
app but has not yet migrated the DocType, `rule_set()` returns the rules
`rules.py` registered at import, so the compliance calendar keeps working
through the upgrade window rather than going blank in it — and the sweep report
says, in a sentence, that it ran the shipped definitions rather than the site's.
A calendar that quietly emptied itself during a migrate would be the single
worst failure this app could have.
"""

from __future__ import annotations

import datetime

import frappe

from .. import compat, compliance_rules
from .. import training as regimes_vocabulary
from . import sandbox
from .base import (
	SEVERITY_CRITICAL,
	SEVERITY_INFO,
	SEVERITY_WARNING,
	Observation,
	Rule,
	days_since,
	days_until,
)

#: See `_DateTimes` for why these are bound here rather than in the class body.
_DATE = datetime.date
_DATETIME = datetime.datetime
_TIMEDELTA = datetime.timedelta

#: Rows one declarative rule will read from its target doctype. The same 2000
#: `rules.py` has always used, for the same reason: past it, a rule is scanning
#: an operation this app is not shaped for, and the `RULE_CAP` on observations
#: will have bitten long before.
SCAN_CAP = 2000

#: Fieldtypes that are layout rather than data, and are never selected.
_LAYOUT_FIELDTYPES = (
	"Section Break",
	"Column Break",
	"Tab Break",
	"HTML",
	"Heading",
	"Table",
	"Table MultiSelect",
	"Fold",
	"Button",
)

#: The field a target doctype scopes to a company by, in preference order.
#: `Field` and `Housing Unit` say `owning_entity`; everything else says
#: `company`. Read off the doctype rather than configured on the rule, because
#: it is a property of the doctype and a rule that got it wrong would scope to
#: nothing and look like a clean company.
_COMPANY_FIELDS = ("company", "owning_entity")


# ── the rule set ────────────────────────────────────────────────────────────
def rule_set() -> tuple:
	"""(rules, notes) — the live rule set from records, or the shipped fallback.

	`rules` is `{rule_id: Rule}` exactly as `base.RULES` always was, so every
	caller downstream of it — the sweep, `list_compliance_rules`, the regime
	filter, the dispatch board's alert mapping — is unchanged.
	"""
	from . import rules as shipped

	notes = []
	if not compat.doctype_exists(compliance_rules.DOCTYPE):
		notes.append(
			"This site has no Compliance Rule DocType yet, so the sweep ran the rule definitions "
			"that ship with erpnext_mcp rather than the site's own. That is the upgrade window and "
			"nothing else: run `bench --site <site> migrate` and the rules become records you can "
			"edit. Behaviour is identical either way — the shipped definitions ARE what gets seeded."
		)
		return dict(shipped.RULES), notes

	rows = compliance_rules.rule_rows()
	if not rows:
		notes.append(
			"The Compliance Rule DocType is here and holds no live rule, so the sweep ran the "
			"shipped definitions. On a site mid-migrate that is expected and the seeder fixes it. "
			"If somebody disabled every rule on purpose, that is what an empty calendar means and "
			"this note is how you can tell the two apart."
		)
		return dict(shipped.RULES), notes

	assembled = {}
	for row in rows:
		key = str(row.get("rule_id") or "").strip()
		if not key or key in assembled:
			continue
		try:
			assembled[key] = rule_from_row(row)
		except Exception as exc:
			notes.append(
				f"Compliance Rule {row.get('name')} ({key}) could not be assembled and did not run: "
				f"{type(exc).__name__}: {exc}. It raised nothing AND DISMISSED NOTHING."
			)
	return assembled, notes


def rule_from_row(row: dict) -> Rule:
	"""One `Rule` from one Compliance Rule record."""
	row = dict(row)
	row.setdefault("regimes", compliance_rules.regimes_of(row.get("name")))
	shape = compliance_rules.shape_of(row)
	requires = tuple(
		compliance_rules.names_list(row.get("requires_doctypes"))
		or ([str(row.get("target_doctype"))] if row.get("target_doctype") else [])
	)
	regimes = tuple(regimes_vocabulary.parse(row.get("regimes")) or ("Internal",))

	if shape == compliance_rules.SHAPE_BUILTIN:
		scan = _builtin_scan(row)
	elif shape == compliance_rules.SHAPE_CUSTOM:
		scan = _custom_scan(row)
	else:
		scan = _declarative_scan(row)

	return Rule(
		key=str(row.get("rule_id")),
		title=str(row.get("title") or row.get("rule_id")),
		category=str(row.get("category") or "Records"),
		scan=scan,
		kairotic_gate=str(row.get("kairotic_gate_description") or ""),
		purpose=str(row.get("purpose") or ""),
		requires=requires,
		framework=str(row.get("regulation_citations") or ""),
		regimes=regimes,
		record=str(row.get("name") or ""),
		version=int(row.get("version") or 1),
		shape=shape,
		authored_by=str(row.get("authored_by") or ""),
	)


def preview(row: dict, context: dict) -> dict:
	"""Run one rule and report what it WOULD observe, writing nothing.

	The read behind `test_compliance_rule`. It is the same code path the sweep
	takes, deliberately — a dry run that used a second implementation would be a
	dry run that could disagree with the real one, which is the one property a
	dry run must not have.
	"""
	rule = rule_from_row(row)
	observations = list(rule.scan(context) or [])
	return {
		"rule_id": rule.key,
		"shape": compliance_rules.shape_of(row),
		"observed": len(observations),
		"observations": [
			{
				"source_doctype": observation.source_doctype,
				"source_docname": observation.source_docname,
				"severity": observation.severity,
				"due_date": observation.due_date or None,
				"company": observation.company or None,
				"category": observation.category or rule.category,
				"regimes": (
					list(observation.regimes) if observation.regimes is not None else list(rule.regimes)
				),
				"message": observation.message,
				"would_be_alert": f"{rule.key}:{observation.source_doctype}:{observation.source_docname}",
			}
			for observation in observations
		],
		"computation_warnings": sorted(
			{warning for observation in observations for warning in (observation.computation_warnings or ())}
		),
	}


# ── the declarative evaluator ───────────────────────────────────────────────
def _declarative_scan(row: dict):
	"""A closure that scans `target_doctype` per the record's own definition."""

	def scan(context: dict) -> list:
		today = context.get("today") or frappe.utils.today()
		company = context.get("company") or ""
		doctype = str(row.get("target_doctype") or "").strip()
		if not doctype or not compat.doctype_exists(doctype):
			return []
		# A compliance column this app installs on demand. Absent means the rule
		# has nothing to read, which is an EMPTY SCAN and not a failure — the
		# same answer `_scan_i9` has always given, and it matters because an
		# empty scan auto-dismisses stale alerts while a failure would not.
		for fieldname in compliance_rules.names_list(row.get("requires_fields")):
			if not compat.has_field(doctype, fieldname):
				return []

		warnings = []
		try:
			filters = compliance_rules.parse_filters(row.get("scope_filters_json"))
		except ValueError as exc:
			warnings.append(f"scope_filters could not be read ({exc}); the rule scanned unscoped.")
			filters = []

		company_field = compat.first_field(doctype, *_COMPANY_FIELDS)
		selected = _selectable_fields(doctype)
		present = set(selected)
		db_filters = {company_field: company} if (company and company_field) else {}
		rows = frappe.db.get_all(doctype, filters=db_filters, fields=selected, limit=SCAN_CAP)

		date_field = str(row.get("date_field") or "").strip()
		cadence = int(row.get("cadence_days") or 0)
		window_field = str(row.get("window_field") or "").strip()
		on_missing = str(row.get("missing_date_behaviour") or compliance_rules.ON_MISSING_SKIP)
		due_mode = str(row.get("due_date_mode") or compliance_rules.DUE_FROM_ANCHOR)
		critical_days = int(row.get("threshold_critical_days") or 0)
		warning_days = int(row.get("threshold_warning_days") or 0)
		severity_critical = str(row.get("severity_critical") or SEVERITY_CRITICAL)
		severity_warning = str(row.get("severity_warning") or SEVERITY_WARNING)
		severity_expired = str(row.get("severity_expired") or SEVERITY_CRITICAL)
		regimes_field = str(row.get("regimes_from_field") or "").strip()
		rule_regimes = list(regimes_vocabulary.parse(row.get("regimes")) or [])
		template = str(row.get("message_template") or "")

		out = []
		for candidate in rows or []:
			candidate = dict(candidate)
			matched, filter_warnings = compliance_rules.row_matches(candidate, filters, present)
			for warning in filter_warnings:
				if warning not in warnings:
					warnings.append(warning)
			if not matched:
				continue

			anchor = candidate.get(date_field) if date_field else None
			anchor_text = str(anchor or "").strip()
			due_text = ""
			days_remaining = None

			if date_field:
				if not anchor_text:
					if on_missing == compliance_rules.ON_MISSING_SKIP:
						continue
				else:
					due_text = _due_from(anchor_text, cadence)
					days_remaining = days_until(today, due_text)
					if days_remaining is None:
						# An unparseable date. Skipped rather than raised: one bad
						# cell must not take a whole rule's night with it.
						warnings.append(
							f"{doctype} {candidate.get('name')} has an unreadable {date_field} "
							f"({anchor!r}) and was skipped."
						)
						continue

			severity = _band(
				days_remaining,
				candidate,
				window_field,
				critical_days,
				warning_days,
				severity_critical,
				severity_warning,
				severity_expired,
			)
			if severity is None:
				continue

			regimes = (
				regimes_vocabulary.parse(candidate.get(regimes_field))
				if regimes_field
				else (list(rule_regimes) if rule_regimes else None)
			)
			rendered = render_message(
				template,
				candidate,
				{
					"today": today,
					"days_remaining": days_remaining,
					"days_overdue": (None if days_remaining is None else -days_remaining),
					"days_since_anchor": days_since(today, anchor_text) if anchor_text else None,
					"anchor": anchor_text or None,
					"due_date": due_text or None,
					"severity": severity,
					"regimes": regimes if regimes is not None else list(rule_regimes),
					"subject": candidate.get("name"),
					"cadence_days": cadence,
					"threshold_critical_days": critical_days,
					"threshold_warning_days": warning_days,
					"rule_title": row.get("title"),
					"regulation_citations": row.get("regulation_citations"),
				},
				warnings,
			)

			out.append(
				Observation(
					source_doctype=doctype,
					source_docname=str(candidate.get("name")),
					message=rendered,
					severity=severity,
					due_date=_due_date(due_mode, due_text, today),
					company=str(candidate.get(company_field) or "") if company_field else "",
					category="",
					regimes=regimes,
					computation_warnings=list(warnings) or None,
				)
			)
		return out

	return scan


def _due_from(anchor: str, cadence: int) -> str:
	"""The deadline: the anchor itself where the cadence is 0, else anchor+cadence.

	The `cadence == 0` branch returns the anchor UNTOUCHED rather than running it
	through `add_days(…, 0)`, because an expiry alert's due date is the string
	that was on the record and a round trip through `getdate` can renormalise it.
	"""
	if not cadence:
		return anchor
	return str(frappe.utils.add_days(frappe.utils.getdate(anchor), cadence))


def _due_date(mode: str, due_text: str, today: str) -> str:
	if mode == compliance_rules.DUE_TODAY:
		return today
	if mode == compliance_rules.DUE_NONE:
		return ""
	return due_text


def _band(
	days_remaining,
	candidate: dict,
	window_field: str,
	critical_days: int,
	warning_days: int,
	severity_critical: str,
	severity_warning: str,
	severity_expired: str,
):
	"""Which severity this row raises, or None for "say nothing".

	A NEGATIVE THRESHOLD MEANS THE BAND NEVER FIRES, which is how a rule that
	only has something to say once the date has passed is written without a
	second field to say so. `threshold_warning_days` is also the OUTER window: a
	certificate two hundred days out reaches no band and the rule stays quiet,
	which is the difference between this and a reminder.
	"""
	if days_remaining is None:
		# Either the rule has no clock at all, or the anchor is missing on a rule
		# whose `missing_date_behaviour` is Raise. Both mean "past due by the only
		# measure this rule has".
		return severity_expired
	if days_remaining < 0:
		return severity_expired
	window = warning_days
	if window_field:
		try:
			window = int(candidate.get(window_field) or 0) or warning_days
		except (TypeError, ValueError):
			window = warning_days
	if 0 <= critical_days and days_remaining <= critical_days:
		return severity_critical
	if 0 <= window and days_remaining <= window:
		return severity_warning
	return None


def _selectable_fields(doctype: str) -> list:
	"""Every data column on the doctype this site actually has, plus `name`.

	Selected by name rather than with `*` so the query is explicit about what it
	reads, and so a doctype whose meta this site has trimmed produces a shorter
	SELECT rather than an error.
	"""
	candidates = []
	try:
		for field in frappe.get_meta(doctype).fields:
			fieldname = str(getattr(field, "fieldname", "") or "")
			fieldtype = str(getattr(field, "fieldtype", "") or "")
			if fieldname and fieldtype not in _LAYOUT_FIELDTYPES:
				candidates.append(fieldname)
	except Exception:  # pragma: no cover - a doctype whose meta cannot be built
		return ["name"]
	seen = dict.fromkeys(candidates)
	return ["name", *compat.existing_fields(doctype, seen)]


# ── the message ─────────────────────────────────────────────────────────────
def render_message(template: str, row: dict, extra: dict, warnings: list) -> str:
	"""Render one alert's text in a Jinja sandbox with NO frappe in it.

	DELIBERATELY NOT `frappe.render_template`. That builds the site's own Jinja
	environment, which has `frappe` in its globals — and a message template that
	could call `frappe.db.set_value` would be a second, undocumented escape hatch
	sitting next to the one this app spent a module sandboxing. The message is
	text about a row; the row is all it gets.

	A template that fails to render produces a plain, honest fallback rather than
	killing the rule. An alert with an ugly message is a problem somebody fixes;
	an alert that never appeared is one nobody knows about.
	"""
	text = str(template or "").strip()
	context = {**{key: value for key, value in row.items() if not str(key).startswith("_")}, **extra}
	context["row"] = row
	if not text:
		return _fallback_message(context)
	try:
		from jinja2 import StrictUndefined  # noqa: F401 - imported to prove jinja2 is here
		from jinja2.sandbox import SandboxedEnvironment
	except Exception:  # pragma: no cover - Frappe depends on Jinja2, so never on a bench
		warnings.append(
			"jinja2 is not importable on this bench, so message templates were not rendered and "
			"each alert carries a plain description instead. Frappe depends on Jinja2, so this "
			"means the environment is broken rather than that the app is."
		)
		return _fallback_message(context)
	try:
		environment = SandboxedEnvironment(autoescape=False, keep_trailing_newline=False)
		return environment.from_string(text).render(**context).strip()
	except Exception as exc:
		warnings.append(
			f"the message template failed to render ({type(exc).__name__}: {exc}); the alert "
			"carries a plain description instead."
		)
		return _fallback_message(context)


def _fallback_message(context: dict) -> str:
	subject = context.get("subject") or context.get("name") or "a record"
	remaining = context.get("days_remaining")
	if remaining is None:
		return f"{subject} matches this rule's condition."
	if remaining < 0:
		return f"{subject} is {abs(int(remaining))} day(s) past due (due {context.get('due_date') or 'an unrecorded date'})."
	return (
		f"{subject} is due in {int(remaining)} day(s), on {context.get('due_date') or 'an unrecorded date'}."
	)


# ── the built-in scanners ───────────────────────────────────────────────────
def _builtin_scan(row: dict):
	"""A closure calling the shipped scanner the record names.

	The scanner is handed the RECORD as well as the context, so the thresholds,
	the scope filters and the citations it reads are the ones on the row rather
	than the constants it was written beside. That is what keeps a built-in rule
	genuinely configurable: an operator moving the annual housing walk from 365
	days to 300 changes a record, not a release.
	"""

	def scan(context: dict) -> list:
		from . import rules as shipped

		scanner = shipped.SCANNERS.get(str(row.get("builtin_scanner") or "").strip())
		if scanner is None:
			return []
		return list(scanner({**context, "rule": row}) or [])

	return scan


# ── custom_python ───────────────────────────────────────────────────────────
def _custom_scan(row: dict):
	"""A closure running the record's own program in `sandbox.py`."""

	def scan(context: dict) -> list:
		today = context.get("today") or frappe.utils.today()
		company = context.get("company") or ""
		doctype = str(row.get("target_doctype") or "")
		warnings = []
		names = _sandbox_names(row, today, company, doctype, warnings)
		try:
			produced = sandbox.run(str(row.get("custom_python") or ""), names)
		except sandbox.SandboxError as exc:
			# REPORTED, NOT RAISED. A refused operation on one rule must not take
			# the sweep down, and it must not be silent either: the warning lands
			# on the report, and the rule observes nothing — which auto-dismisses
			# nothing, because a rule that could not run is not evidence that
			# anybody did the work.
			warnings.append(f"custom_python was refused or failed: {exc}")
			return [
				Observation(
					source_doctype=compliance_rules.DOCTYPE,
					source_docname=str(row.get("name") or row.get("rule_id")),
					message=(
						f"This rule's custom_python did not run: {exc} Until it does, the condition "
						"it watches is UNWATCHED — nothing was raised for it and nothing was "
						"dismissed. Fix the program with update_compliance_rule, or disable the "
						"rule so the calendar stops claiming to cover this."
					),
					severity=SEVERITY_WARNING,
					category="Records",
					regimes=list(regimes_vocabulary.parse(row.get("regimes")) or []),
					computation_warnings=warnings,
				)
			]
		return _as_observations(produced, row, warnings)

	return scan


def _sandbox_names(row: dict, today: str, company: str, doctype: str, warnings: list) -> dict:
	"""Everything a `custom_python` program can see. There is nothing else."""
	rule_regimes = list(regimes_vocabulary.parse(row.get("regimes")) or [])

	def observation(
		source_doctype: str,
		source_docname: str,
		message: str,
		severity: str = SEVERITY_WARNING,
		due_date: str = "",
		company: str = "",
		category: str = "",
		regimes=None,
	) -> dict:
		"""The one constructor a program should use. Returns a plain dict."""
		return {
			"source_doctype": str(source_doctype or ""),
			"source_docname": str(source_docname or ""),
			"message": str(message or ""),
			"severity": str(severity or SEVERITY_WARNING),
			"due_date": str(due_date or ""),
			"company": str(company or ""),
			"category": str(category or ""),
			"regimes": list(regimes) if regimes is not None else None,
		}

	def warn(message: str) -> None:
		"""Say something the sweep report should carry. Not an error."""
		text = str(message or "").strip()
		if text and text not in warnings:
			warnings.append(text)

	return {
		"frappe": _ReadOnlyFrappe(),
		"today": today,
		"company": company,
		"target_doctype": doctype,
		"doctype_meta": _selectable_fields(doctype) if doctype else [],
		"rule": {
			key: row.get(key)
			for key in (
				"rule_id",
				"title",
				"category",
				"target_doctype",
				"date_field",
				"cadence_days",
				"regulation_citations",
			)
		},
		"regimes": rule_regimes,
		"threshold_critical_days": int(row.get("threshold_critical_days") or 0),
		"threshold_warning_days": int(row.get("threshold_warning_days") or 0),
		"cadence_days": int(row.get("cadence_days") or 0),
		"severity_expired": str(row.get("severity_expired") or SEVERITY_CRITICAL),
		"observation": observation,
		"warn": warn,
		"days_until": days_until,
		"days_since": days_since,
		"datetime": _DateTimes(),
		"timedelta": datetime.timedelta,
		"SEVERITY_CRITICAL": SEVERITY_CRITICAL,
		"SEVERITY_WARNING": SEVERITY_WARNING,
		"SEVERITY_INFO": SEVERITY_INFO,
		"observations": [],
		# The safe half of builtins. Everything that can reach the interpreter —
		# `getattr`, `type`, `open`, `eval` — is absent, and `sandbox.check`
		# refuses those names by hand as well, so a future edit here cannot
		# quietly reopen one.
		"len": len,
		"str": str,
		"int": int,
		"float": float,
		"bool": bool,
		"abs": abs,
		"min": min,
		"max": max,
		"round": round,
		"sum": sum,
		"sorted": sorted,
		"any": any,
		"all": all,
		"list": list,
		"dict": dict,
		"set": set,
		"tuple": tuple,
		"range": range,
		"enumerate": enumerate,
		"zip": zip,
		"reversed": reversed,
	}


class _DateTimes:
	"""`datetime.date` / `datetime.datetime`, without the module around them.

	The three are bound from module-level aliases rather than from `datetime.X`
	directly because a class body executes in its own namespace: the moment
	`datetime = datetime.datetime` runs, the name `datetime` inside this body is
	the CLASS, and the next line asking it for `timedelta` fails.
	"""

	date = _DATE
	datetime = _DATETIME
	timedelta = _TIMEDELTA


class _ReadOnlyFrappe:
	"""The `frappe` a rule program sees: five readers and a utils namespace.

	`get_doc` HANDS BACK A DICT, not a Document. A Document has `.save()` and
	`.delete()` on it, and a read-only sandbox that returns a live document is
	read-only in the same sense a locked door with the key in it is locked.
	"""

	def __init__(self):
		self.utils = _ReadOnlyUtils()
		self.db = self

	def get_all(self, doctype, filters=None, fields=None, order_by=None, limit=None, pluck=None):
		return [
			dict(row) if isinstance(row, dict) else row
			for row in frappe.db.get_all(
				doctype,
				filters=filters,
				fields=fields or ["name"],
				order_by=order_by,
				limit=min(int(limit or SCAN_CAP), SCAN_CAP),
				pluck=pluck,
			)
			or []
		]

	def get_value(self, doctype, filters=None, fieldname="name", as_dict=False):
		value = frappe.db.get_value(doctype, filters, fieldname, as_dict=as_dict)
		return dict(value) if as_dict and value else value

	def get_doc(self, doctype, name):
		"""A frozen snapshot of one document. See the class docstring."""
		try:
			return dict(frappe.get_doc(doctype, name).as_dict())
		except Exception:
			return {}

	def exists(self, doctype, filters=None):
		return frappe.db.exists(doctype, filters)

	def count(self, doctype, filters=None):
		return frappe.db.count(doctype, filters)


class _ReadOnlyUtils:
	"""The handful of `frappe.utils` a date-comparing rule actually needs."""

	def today(self):
		return frappe.utils.today()

	def nowdate(self):
		return frappe.utils.nowdate()

	def add_days(self, date, days):
		return str(frappe.utils.add_days(frappe.utils.getdate(date), int(days)))

	def add_months(self, date, months):
		return str(frappe.utils.add_months(frappe.utils.getdate(date), int(months)))

	def date_diff(self, later, earlier):
		return frappe.utils.date_diff(str(later), str(earlier))

	def getdate(self, value=None):
		return frappe.utils.getdate(value)

	def cint(self, value):
		return frappe.utils.cint(value)

	def flt(self, value):
		return frappe.utils.flt(value)


def _as_observations(produced, row: dict, warnings: list) -> list:
	"""Turn whatever a program returned into Observations, or say why it could not."""
	if produced in (None, ""):
		return []
	if isinstance(produced, dict):
		produced = [produced]
	if not isinstance(produced, (list, tuple)):
		warnings.append(
			f"custom_python returned {type(produced).__name__}; a rule returns a list of "
			"observation(...) objects. Nothing was raised."
		)
		return []

	out = []
	for index, entry in enumerate(produced):
		if isinstance(entry, Observation):
			entry.computation_warnings = list(warnings) or None
			out.append(entry)
			continue
		if not isinstance(entry, dict):
			warnings.append(f"custom_python returned a {type(entry).__name__} at position {index}; skipped.")
			continue
		doctype = str(entry.get("source_doctype") or "").strip()
		docname = str(entry.get("source_docname") or "").strip()
		if not (doctype and docname):
			warnings.append(
				f"observation {index} names no source_doctype/source_docname, so there is no record "
				"for the alert to be about and no stable key for it. Skipped."
			)
			continue
		severity = str(entry.get("severity") or SEVERITY_WARNING)
		if severity not in (SEVERITY_CRITICAL, SEVERITY_WARNING, SEVERITY_INFO):
			warnings.append(f"observation {index} asked for severity {severity!r}; used Warning.")
			severity = SEVERITY_WARNING
		out.append(
			Observation(
				source_doctype=doctype,
				source_docname=docname,
				message=str(entry.get("message") or ""),
				severity=severity,
				due_date=str(entry.get("due_date") or ""),
				company=str(entry.get("company") or ""),
				category=str(entry.get("category") or ""),
				regimes=entry.get("regimes"),
				computation_warnings=list(warnings) or None,
			)
		)
	return out
