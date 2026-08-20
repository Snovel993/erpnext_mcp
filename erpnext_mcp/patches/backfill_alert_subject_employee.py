# SPDX-License-Identifier: MIT
"""Fill `subject_employee` on the alerts a site already has.

THE NIGHTLY SWEEP WOULD DO THIS ON ITS OWN, AND WAITING FOR IT IS THE WRONG
ANSWER. `alerts/base.py::_upsert` rewrites the column on every refresh, so every
open alert would carry a subject by the morning after the migrate. But the
handset feature this column exists for — removing the person an alert is about
from the picker, so nobody signs off their own gap — would be silently wrong for
one working day, on every alert on the site. A compliance control that is off
until tomorrow is one that was off when somebody used it.

IT ALSO REACHES ROWS THE SWEEP NEVER WILL. A dismissed alert is not refreshed;
it is history, and history is what an auditor reads. Backfilling it costs one
`get_value` per row and makes the column mean the same thing everywhere.

DERIVED, SO REWRITING IS SAFE — but it still does not rewrite. A row that
already carries a subject is left alone, because the only way one exists today
is that a sweep under this release wrote it, and that sweep read the same source
record this patch would. Skipping them keeps the pass cheap on a site that has
been running the release for a week.

Written with `update_modified=False`: `last_refreshed` and `modified` are how an
operator tells a live calendar from a stale one, and a backfill that touched
every row would report the whole calendar as refreshed tonight when nothing was
observed.

It does not raise. Inside `bench migrate` an exception aborts the migration for
the whole bench, and an alert without a subject is exactly the alert every site
had this morning — the client falls back to reading the name out of the prose,
which is what it did before this release.
"""

import frappe

from .. import compat
from ..alerts import base as alerts_base

DOCTYPE = "Compliance Alert"
FIELD = "subject_employee"

#: Most alerts one pass will read. A calendar larger than this is a calendar
#: with a rule firing on a column that is empty everywhere; the pass says so
#: rather than silently covering the first slice.
SCAN_CAP = 50000


def execute() -> None:
	report = backfill_alert_subject_employee()
	for line in report_lines(report):
		print(line)


def backfill_alert_subject_employee() -> dict:
	"""Derive and store the subject of every alert that has none. Idempotent."""
	report = {"scanned": 0, "filled": 0, "no_subject": 0, "already_set": 0, "skipped": ""}
	if not compat.doctype_exists(DOCTYPE):
		report["skipped"] = f"this site has no {DOCTYPE} DocType, so there is no calendar to fill"
		return report
	if not compat.has_field(DOCTYPE, FIELD):
		report["skipped"] = (
			f"this site's {DOCTYPE} has no `{FIELD}` column yet — it ships with erpnext_mcp "
			"v0.106.0, so run `bench --site <site> migrate` again"
		)
		return report

	rows = (
		frappe.db.get_all(
			DOCTYPE,
			fields=["name", "source_doctype", "source_docname", "company", FIELD],
			limit=SCAN_CAP,
		)
		or []
	)
	report["scanned"] = len(rows)

	for row in rows:
		if str(row.get(FIELD) or "").strip():
			report["already_set"] += 1
			continue
		subject = alerts_base.subject_employee(
			row.get("source_doctype"), row.get("source_docname"), row.get("company")
		)
		if not subject:
			# THE COMMON CASE AND NOT A FAILURE. A stale water test, an
			# uninspected cabin and an overdue filing are about the operation.
			report["no_subject"] += 1
			continue
		try:
			frappe.db.set_value(DOCTYPE, row["name"], FIELD, subject, update_modified=False)
		except Exception:  # pragma: no cover - a row that vanished mid-pass
			continue
		report["filled"] += 1

	return report


def report_lines(report: dict) -> list:
	"""What it did, for the console. Silent on a run that had nothing to fill."""
	lines = []
	if report.get("skipped"):
		return [f"erpnext_mcp: alert subjects were not backfilled — {report['skipped']}."]
	if report.get("filled"):
		lines.append(
			f"erpnext_mcp: named the subject of {report['filled']} Compliance Alert(s), so a "
			"handset can exclude the person an alert is about from the list of people it may be "
			f"handed to. {report['no_subject']} alert(s) are about the operation rather than "
			"about anybody, which is the ordinary case and not a gap."
		)
	if report.get("scanned", 0) >= SCAN_CAP:
		lines.append(
			f"erpnext_mcp: the alert-subject backfill read its {SCAN_CAP} row ceiling, so older "
			"alerts may still have no subject. Those rows behave exactly as they did in "
			"v0.105.0. Re-running `bench migrate` fills the next slice."
		)
	return lines
