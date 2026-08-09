# SPDX-License-Identifier: MIT
"""v0.51.2. Un-escape a literal `\\n` sitting in stored instructions text.

WHAT WENT WRONG. `create_farm_task_template` / `update_farm_task_template`
store whatever text a caller sends in `instructions`, `.strip()`ed and nothing
else — see `args.as_str`. A caller (an MCP client, or a hand-edit through the
Desk form) that sent the two-character sequence backslash-n instead of an
actual line break got that sequence stored byte-for-byte in
`Farm Task Template.instructions`. `task_templates.snapshot()` then copies it
straight onto `Farm Task.notes` for every task raised from that template —
`"notes": str(row.get("instructions") or "")`, a plain passthrough, nothing
re-encoded. The iOS client renders the field with SwiftUI `Text`, which turns
a real newline into a line break but prints a literal backslash-n as two
visible characters, so a worker reads "Step 1\\nStep 2" instead of two lines.

WHAT THIS PATCH DOES. Finds every `Farm Task Template.instructions` and
`Farm Task.notes` value that contains the literal two-character sequence and
replaces it with a real newline. Existing tasks already snapshotted from a bad
template are fixed too, not just the template they came from. Idempotent — a
value with no literal sequence is left alone, so running it twice does
nothing the second time.

WHY BOTH DOCTYPES. Fixing only the template would leave every task already
raised from it — including ones a worker is looking at right now — showing
the broken text until somebody re-triggers a snapshot that may never happen.

NEVER RAISES. It runs inside `bench migrate`, where an exception aborts the
migration for the whole bench.
"""

import frappe

from .. import compat

#: The two-character sequence a stored string should never contain: a real
#: line break is one character, not this.
_LITERAL_NEWLINE = "\\n"

#: `(doctype, the text field on it)`.
_TARGETS = (
	("Farm Task Template", "instructions"),
	("Farm Task", "notes"),
)


def execute() -> None:
	for line in report_lines(unescape_literal_newlines()):
		print(line)


def unescape_literal_newlines() -> dict:
	report = {"fixed": [], "skipped": []}
	for doctype, field in _TARGETS:
		_fix_doctype(doctype, field, report["fixed"], report["skipped"])
	return report


def _fix_doctype(doctype: str, field: str, fixed: list, skipped: list) -> None:
	if not compat.doctype_exists(doctype) or not compat.has_field(doctype, field):
		skipped.append(f"{doctype}.{field} does not exist on this site.")
		return

	try:
		rows = frappe.db.get_all(
			doctype,
			filters={field: ("not in", ("", None))},
			fields=["name", field],
			limit=5000,
		)
	except Exception as exc:  # pragma: no cover - reported, never raised
		skipped.append(f"{doctype} rows could not be read — {type(exc).__name__}: {exc}")
		return

	# Filtered in Python rather than with a SQL LIKE: a pattern containing a
	# literal backslash has to be escaped for the query's own escape
	# character, and getting that wrong either misses rows or matches too
	# many. A straight substring check has no such ambiguity.
	for row in rows or []:
		value = row.get(field)
		if not value or _LITERAL_NEWLINE not in value:
			continue
		cleaned = value.replace(_LITERAL_NEWLINE, "\n")
		try:
			frappe.db.set_value(doctype, row["name"], field, cleaned, update_modified=False)
		except Exception as exc:  # pragma: no cover - reported, never raised
			skipped.append(f"{doctype} {row['name']} could not be fixed — {type(exc).__name__}: {exc}")
			continue
		fixed.append({"doctype": doctype, "name": str(row["name"])})


def report_lines(report: dict) -> list:
	"""What the migrate prints. Every row it touched by name — silence would
	be worse, since this is rewriting content an operator wrote."""
	lines = []
	for entry in report.get("fixed") or ():
		lines.append(
			f"erpnext_mcp: {entry['doctype']} {entry['name']} had a literal backslash-n in its "
			"instructions text — replaced with a real newline."
		)
	for note in report.get("skipped") or ():
		lines.append(f"erpnext_mcp: literal-newline fixup skipped — {note}")
	return lines
