# SPDX-License-Identifier: MIT
"""Give every completion filed before v0.20.1 a signature, so a retry can match it.

THE ROWS THIS IS FOR ARE THE ROWS MOST LIKELY TO BE RE-SENT. An iPad that has
been carrying a stuck sync queue since before this release is carrying
completions of tasks that were finished under v0.19; if those rows have no
signature, the first thing the new idempotency check does is fail to recognise
them, and the worker sees the same three Failed entries per task that motivated
the release. A feature that works only on data created after it shipped would
have missed the actual complaint.

────────────────────────────────────────────────────────────────────────────
WHAT IT CAN AND CANNOT KNOW, AND IT DOES NOT PRETEND OTHERWISE
────────────────────────────────────────────────────────────────────────────

Four of the five components are on the row and are exactly what the client sent:
the assignment's own name, `assigned_to`, the evidence child rows, and
`findings_text` / `completion_narrative`.

THE FIFTH IS NOT KNOWABLE. `completed_at` is on the row, but nothing on the row
says whether the client chose that timestamp or the server filled it with
`now()` — and those two produce different hashes for the same resubmission. A
backfill that guessed would create exactly the false conflicts this release
exists to remove, on exactly the oldest rows.

So it does not guess. These rows get the `v1b` scheme, which leaves the
clock-out time out of the hash, and `completions.matches` compares a stored
`v1b` under the same rule. Everything else is still compared, so a legacy row
re-submitted by a different worker, with different evidence, or with a different
account of the work is still the conflict it should be. See
`erpnext_mcp/completions.py`.

────────────────────────────────────────────────────────────────────────────
IDEMPOTENT, AND ON PURPOSE RATHER THAN BY LUCK
────────────────────────────────────────────────────────────────────────────

Listed in `patches.txt` AND called from `after_migrate`, the same pair
`migrate_training_types` uses and for the same reason: the patch entry records
the first run in the Patch Log, and the hook catches a site that upgraded across
the version. So it runs at least twice on any real bench.

The second run is a no-op because the query only selects rows where
`completion_signature` is empty. A row that already has one is never rewritten —
NOT EVEN WHERE RECOMPUTING WOULD PRODUCE THE SAME STRING. A signature is a
statement about a submission that was made at a particular moment; a patch that
felt free to rewrite one is a patch that could, on a later release with a
changed hash input, silently invalidate a queue somebody is still holding.

It writes with `update_modified=False`. `_last_completion` orders by `modified`
to pick which completed assignment a bare task name refers to, and a backfill
that touched every row's timestamp would reorder that history for no reason.

It does not raise. Inside `bench migrate` an exception aborts the migration for
the whole bench, and a completion left without a signature is not broken — it is
a row where a resubmission behaves exactly as it did in v0.19.7, which is where
every one of these rows already was this morning.
"""

import frappe

from .. import compat, completions

DOCTYPE = "Farm Task Assignment"
COMPLETED = "Completed"

#: Most rows one pass will read. A site with more completed assignments than
#: this has a genuine backlog and the patch says so rather than silently
#: covering the first slice — see `report_lines`.
SCAN_CAP = 100000


def execute() -> None:
	report = backfill_completion_signatures()
	for line in report_lines(report):
		print(line)


def backfill_completion_signatures() -> dict:
	"""Write a `v1b` signature onto every Completed row that has none. Idempotent."""
	report = {"scanned": 0, "signed": 0, "already_signed": 0, "failed": [], "skipped": ""}
	if not compat.doctype_exists(DOCTYPE):
		report["skipped"] = f"this site has no {DOCTYPE} DocType, so there are no completions to sign"
		return report
	if not compat.has_field(DOCTYPE, "completion_signature"):
		report["skipped"] = (
			"this site's Farm Task Assignment has no `completion_signature` column yet — it "
			"ships with erpnext_mcp v0.20.1, so run `bench --site <site> migrate` again"
		)
		return report

	rows = (
		frappe.db.get_all(
			DOCTYPE,
			filters={"state": COMPLETED},
			fields=["name", "assigned_to", "findings_text", "completion_narrative", "completion_signature"],
			limit=SCAN_CAP,
		)
		or []
	)
	report["scanned"] = len(rows)

	for row in rows:
		if str(row.get("completion_signature") or "").strip():
			report["already_signed"] += 1
			continue
		try:
			evidence = (
				frappe.db.get_all(
					"Farm Task Evidence",
					filters={"parent": row["name"], "parenttype": DOCTYPE},
					fields=["file", "file_url"],
					limit=200,
				)
				or []
			)
			value = completions.backfill_signature(
				row["name"],
				row.get("assigned_to"),
				evidence,
				row.get("findings_text"),
				row.get("completion_narrative"),
			)
			frappe.db.set_value(DOCTYPE, row["name"], "completion_signature", value, update_modified=False)
		except Exception as exc:
			report["failed"].append({"assignment": row["name"], "reason": f"{type(exc).__name__}: {exc}"})
			continue
		report["signed"] += 1

	return report


def report_lines(report: dict) -> list:
	"""What it did, for the console. Silent on a run that had nothing to sign."""
	lines = []
	if report.get("skipped"):
		return [f"erpnext_mcp: completion signatures were not backfilled — {report['skipped']}."]
	if report.get("signed"):
		lines.append(
			f"erpnext_mcp: signed {report['signed']} completed Farm Task Assignment(s) so a "
			"re-sent completion of pre-v0.20.1 work is recognised as the same completion rather "
			"than refused. The clock-out time is not part of a backfilled signature — nothing on "
			"the row says whether the client or the server chose it."
		)
	if report.get("scanned", 0) >= SCAN_CAP:
		lines.append(
			f"erpnext_mcp: the completion-signature backfill read its {SCAN_CAP} row ceiling, so "
			"there may be older completions still unsigned. They behave exactly as they did in "
			"v0.19.7 — a resubmission is refused rather than recognised. Re-running `bench "
			"migrate` signs the next slice."
		)
	for failure in report.get("failed") or ():
		lines.append(
			f"erpnext_mcp: could not sign {failure['assignment']} — {failure['reason']}. That "
			"completion stands; only a resubmission of it is affected."
		)
	return lines
