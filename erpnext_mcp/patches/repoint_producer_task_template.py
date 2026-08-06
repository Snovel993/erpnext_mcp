# SPDX-License-Identifier: MIT
"""v0.41.0. Clear a `producer_task_template` that names an Inspection Template.

WHY THE FIELD MOVED. `Compliance Rule.producer_task_template` linked to
Inspection Template from v0.22.0 to v0.40.0, and in all that time nothing on the
dispatch side ever read it: multi-section visits are raised by
`_bundle_into_sessions`, which matches a template's sections against the records
a place's pending alerts are asking for, deterministically, and never consults
this column. It was a field with a description and no behaviour.

v0.41.0 gave it behaviour, pointed at the right size of thing. A Farm Task
Template is the shape of ONE task — the type, the skill, the duration, the
dispatch mode, the evidence contract, the record a completion produces and the
checklist — which is what a rule saying 'test the smoke detector' actually needs.
An Inspection Template is a whole visit and has no business being the answer.

WHAT THIS PATCH DOES, AND WHY IT IS AS SMALL AS IT IS. A Link column whose target
DocType changed will refuse the next save of any row still holding an old value:
Frappe validates links on save, and `INSPT-2026-0001` is not a Farm Task
Template. So a rule an operator set — and the value never did anything — would
become a rule that cannot be edited, with an error naming a DocType nobody
mentioned. This finds those rows, clears the column, and PRINTS EVERY ONE BY
NAME with what it held, so the operator can point the rule at a Farm Task
Template if they meant it to produce work.

IT CLEARS RATHER THAN CONVERTS. There is no honest automatic translation from a
multi-section visit to a single task shape — a Cabin Opening with four sections
is four records, and picking one of them would be this app inventing an
operator's intent and then generating work from the invention. Clearing loses
nothing that was ever read, and says so out loud.

NOTHING ELSE IS TOUCHED. `producer_farm_task_type`, `producer_skill_required`
and `evidence_contract_json` are the fallback recipe for a rule with no template
and they keep working exactly as they did. Every Inspection Template stays on
the site, every session worked from one stays readable, and the bundling that
raises them is unchanged.

NEVER RAISES. It runs inside `bench migrate`, where an exception aborts the
migration for the whole bench.
"""

import frappe

from .. import compat

RULE = "Compliance Rule"
FIELD = "producer_task_template"


def execute() -> None:
	for line in report_lines(repoint_producer_task_template()):
		print(line)


def repoint_producer_task_template() -> dict:
	"""Clear every `producer_task_template` that is not a Farm Task Template."""
	report = {"cleared": [], "kept": [], "skipped": ""}
	if not compat.doctype_exists(RULE) or not compat.has_field(RULE, FIELD):
		report["skipped"] = f"this site has no {RULE}.{FIELD} column yet."
		return report

	try:
		rows = frappe.db.get_all(
			RULE,
			filters={FIELD: ("not in", ("", None))},
			fields=["name", "rule_id", FIELD],
			limit=1000,
		)
	except Exception as exc:  # pragma: no cover - reported, never raised
		report["skipped"] = f"the rules could not be read — {type(exc).__name__}: {exc}"
		return report

	templates_exist = compat.doctype_exists("Farm Task Template")
	for row in rows or []:
		value = str(row.get(FIELD) or "").strip()
		if not value:
			continue
		if templates_exist and frappe.db.exists("Farm Task Template", value):
			report["kept"].append({"rule": str(row["name"]), "template": value})
			continue
		try:
			frappe.db.set_value(RULE, str(row["name"]), FIELD, None, update_modified=False)
		except Exception as exc:  # pragma: no cover - reported, never raised
			report["skipped"] = f"{row['name']} could not be cleared — {type(exc).__name__}: {exc}"
			continue
		report["cleared"].append(
			{"rule": str(row["name"]), "rule_id": str(row.get("rule_id") or ""), "was": value}
		)
	return report


def report_lines(report: dict) -> list:
	"""What the migrate prints. Every cleared rule by name — silence would be worse."""
	lines = []
	if report.get("skipped"):
		lines.append(f"erpnext_mcp: producer_task_template was not repointed — {report['skipped']}")
	for entry in report.get("cleared") or ():
		lines.append(
			f"erpnext_mcp: Compliance Rule {entry['rule']} ({entry['rule_id']}) had "
			f"producer_task_template = {entry['was']!r}, which is an Inspection Template. The "
			"field now names a Farm Task Template — the shape of ONE task — and the old value was "
			"never read by anything, so it has been cleared. If that rule should produce templated "
			"work, point it at a Farm Task Template with update_compliance_rule; "
			"list_farm_task_templates has the register. Multi-section visits are unaffected: they "
			"are matched by their sections, not by this field."
		)
	return lines
