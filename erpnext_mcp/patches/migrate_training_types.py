# SPDX-License-Identifier: MIT
"""Turn the free text in `Employee Training Record.training_type` into records.

v0.19.2. THE COLUMN DID NOT CHANGE SHAPE, WHICH IS WHY THIS IS SAFE. `training_type`
was a Data field holding 'WPS Handler Training'; it is now a Link holding
'WPS Handler Training', because `Training Type` names itself from
`training_type_name` and the docname IS the text somebody already typed. So this
patch does not rewrite a single training record's value in the ordinary case. It
creates the masters those values were already pointing at in spirit, and only
touches a record where the text needed normalising — trailing whitespace, or a
casing that would otherwise have split one curriculum across two masters.

That is the whole reason the master is named `field:training_type_name` rather
than by a series. A `TT-00007` autoname would have meant rewriting every training
record on the site inside a migration, and a migration that rewrites compliance
evidence is a migration somebody has to be able to prove was correct afterwards.

────────────────────────────────────────────────────────────────────────────
IDEMPOTENT, AND NOT MERELY BY ACCIDENT
────────────────────────────────────────────────────────────────────────────

It is listed in `patches.txt` AND called from `after_migrate`, exactly as
`register_custom_party_types` is and for the same reason: the patch entry records
in the Patch Log when this first ran, and the hook catches a site that upgraded
across the version. So it runs at least twice on any real bench and has to be a
no-op the second time.

It is, in all three of its steps. Creating a type checks `find_type` first, which
matches case- and whitespace-insensitively. Re-linking only writes where the
resolved name differs from what is stored. And the ORDER matters: every type is
created BEFORE any record is re-linked, because a link written to a master that
does not exist yet is a link validation error inside a migration.

────────────────────────────────────────────────────────────────────────────
WHAT IT WILL NOT DO
────────────────────────────────────────────────────────────────────────────

It does not touch `regimes` on any Employee Training Record. The new type carries
regimes guessed from its name, and those are a DEFAULT for records filed from now
on; the records already on the site carry what somebody tagged them with at the
time, and a patch that overwrote that would be replacing evidence with a
heuristic. If the guess and the record disagree, the record is right — it says
what that afternoon actually covered.

It does not raise. It runs inside `bench migrate`, where an exception aborts the
migration for the whole bench; a record it could not re-link is named on the
console and the rest still run. A training record left pointing at free text on a
Link column is readable, editable and reportable — it simply will not resolve in
the Desk until somebody creates the type, which is a much smaller problem than a
bench that will not migrate.
"""

import frappe

from erpnext_mcp import compat, training


def execute() -> None:
	report = migrate_training_types()
	for line in report_lines(report):
		print(line)


def migrate_training_types() -> dict:
	"""Create a `Training Type` for every distinct value, then re-link. Idempotent."""
	report = {
		"scanned": 0,
		"distinct": [],
		"types_created": [],
		"types_present": [],
		"records_relinked": [],
		"failed": [],
		"skipped": "",
	}
	if not compat.doctype_exists(training.TYPE_DOCTYPE):
		report["skipped"] = (
			f"this site has no {training.TYPE_DOCTYPE} DocType yet — it ships with erpnext_mcp "
			"v0.19.2, so run `bench --site <site> migrate` again"
		)
		return report
	if not compat.doctype_exists(training.DOCTYPE):
		report["skipped"] = f"this site has no {training.DOCTYPE} DocType, so there is nothing to migrate"
		return report

	rows = (
		frappe.db.get_all(
			training.DOCTYPE,
			fields=["name", "training_type"],
			limit=100000,
		)
		or []
	)
	report["scanned"] = len(rows)

	# ── 1. the distinct values, folded the way `find_type` folds them ─────────
	# Keyed on the folded form so 'WPS Handler Training' and 'wps  handler
	# training' produce ONE master rather than two that split one curriculum's
	# history. The first spelling seen wins, which is arbitrary and has to be:
	# neither is more correct, and picking one is better than creating both.
	distinct = {}
	for row in rows:
		text = " ".join(str(row.get("training_type") or "").split()).strip()
		if not text:
			continue
		distinct.setdefault(text.casefold(), text)
	report["distinct"] = sorted(distinct.values())

	# ── 2. one Training Type per distinct value, BEFORE anything is re-linked ──
	resolved = {}
	for folded, text in sorted(distinct.items()):
		try:
			outcome = training.ensure_type(text)
		except Exception as exc:
			report["failed"].append(
				{"training_type": text, "reason": f"{type(exc).__name__}: {exc}"}
			)
			continue
		resolved[folded] = outcome["training_type"]
		report["types_created" if outcome["created"] else "types_present"].append(
			outcome["training_type"]
		)

	# ── 3. re-link only where the stored text is not already the docname ──────
	for row in rows:
		stored = str(row.get("training_type") or "")
		text = " ".join(stored.split()).strip()
		if not text:
			continue
		target = resolved.get(text.casefold())
		if not target or target == stored:
			continue
		try:
			frappe.db.set_value(
				training.DOCTYPE, row["name"], "training_type", target, update_modified=False
			)
		except Exception as exc:
			report["failed"].append({"record": row["name"], "reason": f"{type(exc).__name__}: {exc}"})
			continue
		report["records_relinked"].append({"record": row["name"], "from": stored, "to": target})

	return report


def report_lines(report: dict) -> list:
	"""What the migration did, for the console. Silent when there was nothing to do.

	A migration that prints on every migrate is a migration whose output nobody
	reads by the third release, so a no-op run says nothing at all — but a run
	that CREATED a master or REWROTE an evidence record says so by name, because
	those are the two things somebody may want to check afterwards.
	"""
	lines = []
	if report.get("skipped"):
		return [f"erpnext_mcp: training types were not migrated — {report['skipped']}."]
	created = report.get("types_created") or []
	relinked = report.get("records_relinked") or []
	if created:
		lines.append(
			f"erpnext_mcp: created {len(created)} Training Type(s) from the free text already on "
			f"{report.get('scanned', 0)} training record(s): {', '.join(sorted(created))}. Regimes "
			"on each were guessed from its name — check them, and note that the guess does NOT "
			"touch the regimes already tagged on any existing training record."
		)
	if relinked:
		lines.append(
			f"erpnext_mcp: normalised training_type on {len(relinked)} record(s) whose stored text "
			"differed from the curriculum's name only by casing or spacing: "
			+ ", ".join(f"{item['record']} {item['from']!r} → {item['to']!r}" for item in relinked[:20])
			+ (f", and {len(relinked) - 20} more" if len(relinked) > 20 else "")
		)
	for failure in report.get("failed") or ():
		lines.append(
			"erpnext_mcp: could not migrate "
			+ (failure.get("record") or repr(failure.get("training_type")))
			+ f" — {failure['reason']}"
		)
	return lines
