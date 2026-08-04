# SPDX-License-Identifier: MIT
"""v0.22.1. Move the five rules that became declarative onto their new definitions.

THE SEEDER CANNOT DO THIS, AND THAT IS BY DESIGN. `seed_compliance_rules` checks
for the `rule_id` before it writes and leaves anything it finds alone — which is
the property that stops an operator's raised threshold being corrected back on
every upgrade. A site that installed v0.22.0 therefore already has a
`certification_expiring` row naming its built-in scanner, and the seeder will
never touch it again.

So the five have to be migrated deliberately, by this patch, and it has to answer
two questions the seeder never faces:

  * WHAT DOES AN OPERATOR'S EDIT DO TO IT? Everything both shapes share is
    carried across from the row that is here: the thresholds, the severities, the
    cadence, the citations, the regimes, the retention, the packet list, the
    producer recipe, the approval and the switch. A site that moved its annual
    detector cycle from 365 days to 300 still has 300 afterwards. Only the fields
    that describe the SHAPE of the scan — the ones that did not exist before this
    release — come from the shipped definition.
  * WHAT HAPPENS TO THE OLD ROW? It is SUPERSEDED, not edited. Same as
    `update_compliance_rule`, same reasoning: an alert raised last April can
    still be read against the definition that raised it, and a sweep already
    running against v1 finishes against v1. The old row stays on the site in
    full, disabled, pointing at the new one.

`scope_filters` IS CONCATENATED RATHER THAN REPLACED, and it is the one merge in
here. In v0.22.0 a built-in rule seeded with an EMPTY filter list because its
scoping lived in the scanner; anything in that column was therefore added by an
operator, on top. The declarative rule carries the scoping the scanner used to
do, so the two lists are both wanted and both are ANDed. Dropping the operator's
would silently widen a rule somebody deliberately narrowed.

`spray_season_days` IS READ ACROSS INTO `gate_within_days`. It lived in
`extra_parameters` because v0.22.0 had no column for it; it has one now, and a
site that tuned its spray season would otherwise have that tuning quietly ignored
by the new gate. The key stays in `extra_parameters` as well, so nothing that
reads it by name breaks.

NEVER RAISES. It runs inside `bench migrate`, where an exception aborts the
migration for the whole bench. A rule it could not migrate is named on the
console, keeps its built-in scanner — which still ships, still runs and still
produces the same alerts — and can be migrated by hand.
"""

import frappe

from erpnext_mcp import compat, compliance_rules

#: The five rules v0.22.1 took declarative, and nothing else. Named explicitly
#: rather than derived from "every rule whose shipped shape has no scanner",
#: because a future release adding a sixth should have to say so here — a patch
#: that silently widens its own scope on the next upgrade is a patch nobody can
#: review once.
MIGRATED = (
	"certification_expiring",
	"water_test_stale",
	"housing_detector_test_stale",
	"housing_corrective_action_open",
	"water_test_contamination",
)

#: Everything an operator may have tuned that BOTH shapes understand. Carried
#: across from the row that is on the site rather than taken from the shipped
#: definition, because on these fields the site is right and the app is only the
#: default it started from.
KEPT = (
	"title",
	"category",
	"purpose",
	"kairotic_gate_description",
	"regulation_citations",
	"retention_years",
	"cadence_days",
	"window_field",
	"threshold_critical_days",
	"threshold_warning_days",
	"severity_critical",
	"severity_warning",
	"severity_expired",
	"requires_doctypes",
	"requires_fields",
	"producer_task_template",
	"producer_farm_task_type",
	"producer_skill_required",
	"authored_by",
	"ai_source_citation",
	"approver_employee",
	"approver_signature",
)


def execute() -> None:
	report = migrate_declarative_rules()
	for line in report_lines(report):
		print(f"erpnext_mcp: {line}")


def migrate_declarative_rules() -> dict:
	"""Supersede each still-built-in row with its declarative definition."""
	report = {"migrated": [], "already": [], "absent": [], "failed": []}
	if not compat.doctype_exists(compliance_rules.DOCTYPE):
		return report

	try:
		specs = {spec["rule_id"]: spec for spec in compliance_rules.seed_specs()}
	except Exception as exc:  # pragma: no cover - a partial import during a migrate
		report["failed"].append({"name": "seed_specs", "reason": f"{type(exc).__name__}: {exc}"})
		return report

	for rule_id in MIGRATED:
		try:
			outcome = _migrate_one(rule_id, specs.get(rule_id) or {})
		except Exception as exc:  # pragma: no cover - reported, never raised
			report["failed"].append({"name": rule_id, "reason": f"{type(exc).__name__}: {exc}"})
			continue
		report[outcome].append(rule_id)
	return report


def _migrate_one(rule_id: str, shipped: dict) -> str:
	name = compliance_rules.resolve(rule_id)
	if not name or not shipped:
		return "absent"
	old = compliance_rules.rule_row(name)
	if not str(old.get("builtin_scanner") or "").strip():
		# Already declarative — either this patch has run, or the site installed
		# at v0.22.1 and the seeder wrote the new shape directly.
		return "already"
	if str(old.get("superseded_by") or "").strip():  # pragma: no cover - resolve prefers the live row
		return "already"

	spec = dict(shipped)
	spec["rule_id"] = rule_id
	spec["version"] = int(old.get("version") or 1) + 1
	spec["builtin_scanner"] = ""
	for fieldname in KEPT:
		if fieldname in old:
			spec[fieldname] = old.get(fieldname)
	spec["regimes"] = old.get("regimes") or shipped.get("regimes") or []
	spec["enabled"] = 1 if compat.checked(old.get("enabled")) else 0
	spec["human_approved_by"] = old.get("human_approved_by") or _approver()
	spec["human_approved_on"] = old.get("human_approved_on") or frappe.utils.now()
	spec["evidence_contract"] = _quietly(
		compliance_rules.parse_contract, old.get("evidence_contract_json")
	) or shipped.get("evidence_contract", {})
	spec["audit_packet_types"] = _quietly(
		compliance_rules.parse_packet_types, old.get("audit_packet_types")
	) or shipped.get("audit_packet_types", [])
	spec["extra_parameters"] = {
		**(shipped.get("extra_parameters") or {}),
		**(_quietly(compliance_rules.as_object, old.get("extra_parameters_json")) or {}),
	}
	spec["scope_filters"] = _merged_filters(shipped.get("scope_filters"), old.get("scope_filters_json"))
	if str(old.get("message_template") or "").strip():
		spec["message_template"] = old.get("message_template")
	_carry_the_spray_season(spec)

	# The old row is stood down FIRST, so the controller's "one live row per
	# rule_id" check sees a clear field when the new row is inserted. If the
	# insert then fails, the old row is put back exactly as it was — there is no
	# ordering here that can leave a rule_id with no live definition.
	was_enabled = 1 if compat.checked(old.get("enabled")) else 0
	frappe.db.set_value(
		compliance_rules.DOCTYPE, name, {"enabled": 0, "active_row_flag": 0}, update_modified=False
	)
	try:
		doc = compliance_rules.build_rule(spec)
		doc.insert(ignore_permissions=True)
	except Exception:
		frappe.db.set_value(
			compliance_rules.DOCTYPE,
			name,
			{"enabled": was_enabled, "active_row_flag": was_enabled},
			update_modified=False,
		)
		raise
	frappe.db.set_value(compliance_rules.DOCTYPE, name, "superseded_by", doc.name, update_modified=False)
	return "migrated"


def _carry_the_spray_season(spec: dict) -> None:
	"""`extra_parameters.spray_season_days` → `gate_within_days`, where it was tuned."""
	tuned = (spec.get("extra_parameters") or {}).get("spray_season_days")
	if tuned in (None, ""):
		return
	try:
		spec["gate_within_days"] = int(tuned)
	except (TypeError, ValueError):  # pragma: no cover - a column somebody typed prose into
		pass


def _merged_filters(shipped, stored) -> list:
	"""The scanner's own scoping, plus whatever an operator added on top of it."""
	base = _quietly(compliance_rules.parse_filters, shipped) or []
	extra = _quietly(compliance_rules.parse_filters, stored) or []
	out = list(base)
	for entry in extra:
		if entry not in out:
			out.append(entry)
	return out


def _quietly(parser, raw):
	"""A malformed blob on a stored row must not stop the whole migration."""
	try:
		return parser(raw)
	except Exception:
		return []


def _approver() -> str:  # pragma: no cover - every seeded row already has one
	try:
		if frappe.db.exists("User", "Administrator"):
			return "Administrator"
	except Exception:
		pass
	return "Administrator"


def report_lines(report: dict) -> list:
	"""What the console says. One line per outcome that happened, and no line for
	the ones that did not — a migrate that prints five reassuring nothings trains
	people to scroll past the line that mattered."""
	lines = []
	if report["migrated"]:
		lines.append(
			f"migrated {len(report['migrated'])} compliance rule(s) from a built-in scanner to a "
			f"declarative definition: {', '.join(sorted(report['migrated']))}. Every threshold, "
			"scope filter, citation and switch on the old row was carried across; the old row is "
			"still here, disabled, so alerts raised under it stay readable against the definition "
			"that raised them. What each rule SAYS is unchanged."
		)
	for entry in report["failed"]:
		lines.append(
			f"could not migrate compliance rule {entry['name']} — {entry['reason']}. It keeps its "
			"built-in scanner, which still ships and still raises exactly the same alerts, so "
			"nothing has gone quiet. update_compliance_rule can move it by hand."
		)
	return lines
