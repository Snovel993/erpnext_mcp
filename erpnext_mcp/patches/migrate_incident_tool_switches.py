# SPDX-License-Identifier: MIT
"""v0.95.0. Rename the six discipline tools; carry their `allow_*` switches.

`rename_discipline_record.py` renamed the DOCTYPE in v0.94.0 and deliberately
left the six MCP tools alone, because `settings.tool_enabled` derives a switch
as `allow_<tool_name>` and a tool rename is a switch rename. Its docstring was
explicit about what a later release doing this MUST do:

    "If a later release does rename them, it MUST carry each `allow_<old>`
    value across to `allow_<new>` in the same patch, and assert both
    directions: a site with the old switch ON ends with the new switch ON,
    and a site with it OFF stays OFF."

This is that patch, for:

    allow_create_discipline_record       -> allow_create_incident_record
    allow_acknowledge_discipline_record  -> allow_acknowledge_incident_record
    allow_get_discipline_record          -> allow_get_incident_record
    allow_list_discipline_history        -> allow_list_incident_history
    allow_get_discipline_report          -> allow_get_incident_report
    allow_expire_discipline_record       -> allow_expire_incident_record

THREE OF THE SIX DEFAULT TO `0`. Without this patch, a site with
`allow_create_discipline_record` switched ON would read a brand-new
`allow_create_incident_record` Check defaulting to `0` and arrive with the
tool DISABLED — silently, on upgrade, with no error to point at.

WHY THIS MUST RUN BEFORE `set_default_tool_switches`. That patch seeds any
field with no stored value from its declared default, and a fresh
`allow_create_incident_record` has never been stored. If it seeds first, this
patch finds the new field already carrying a default value and (correctly,
per its own no-clobber rule below) refuses to overwrite an operator's
apparent choice — except it would not be one, it would be the seed. So this
is listed in `patches.txt` ABOVE `set_default_tool_switches`, and that
ordering is load-bearing.

READS AND WRITES GO THROUGH RAW SQL ON `tabSingles`, NOT THE ORM. The first
cut of this patch used `frappe.db.get_single_value` / `frappe.db.set_value`,
and it failed on deploy: `ValidationError: Field allow_create_discipline_record
does not exist on ERPNext MCP Settings`. The DocType JSON only declares the
NEW fieldnames — the old ones are gone from the schema by the time this patch
runs — and the ORM validates a Single's fieldname against that schema before
it will touch `tabSingles`. Raw SQL is the only way to reach a row whose field
no longer exists in the DocType at all, which is exactly the row this patch
exists to read.

`tabSingles` is a (doctype, field, value) table with no schema of its own, so
a field dropped from the DocType JSON leaves its stored row exactly where it
was — an orphaned key, still readable by name, same as any other orphaned
column. Each pair is carried by INSERTing a new row for `allow_<new>` rather
than renaming the old row in place: renaming would either collide with an
already-stored `allow_<new>` row (two rows for the same field, whichever a
plain SELECT happens to return first) or destroy the old row's audit trail.
Leaving the old row where it is costs nothing and there is no cleanup patch
provided for it.

NEVER CLOBBERS AN EXPLICIT NEW-FIELD VALUE. If `allow_create_incident_record`
already has a stored value — an operator who flipped it by hand after
upgrading, or a re-run of this patch on a site that already migrated — that
value wins and the old one is left alone, read but not touched.

IT IS IDEMPOTENT AND NEVER RAISES, for the same reason `rename_discipline_record`
is: this runs inside `bench migrate`, where an exception here aborts every
other app's migration too.
"""

from erpnext_mcp import settings

PAIRS = (
	("allow_create_discipline_record", "allow_create_incident_record"),
	("allow_acknowledge_discipline_record", "allow_acknowledge_incident_record"),
	("allow_get_discipline_record", "allow_get_incident_record"),
	("allow_list_discipline_history", "allow_list_incident_history"),
	("allow_get_discipline_report", "allow_get_incident_report"),
	("allow_expire_discipline_record", "allow_expire_incident_record"),
)


def execute():
	import frappe

	if not frappe.db.exists("DocType", settings.SETTINGS_DOCTYPE):
		print(f"erpnext_mcp: {settings.SETTINGS_DOCTYPE} is not installed yet — nothing to migrate.")
		return

	for old, new in PAIRS:
		try:
			old_rows = frappe.db.sql(
				"SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s",
				(settings.SETTINGS_DOCTYPE, old),
			)
		except Exception as exc:  # pragma: no cover - reported, never fatal to a migrate
			print(f"erpnext_mcp: could not read {old!r}: {type(exc).__name__}: {exc}")
			continue

		if not old_rows:
			# Never stored on this site — a fresh install, or a site that has
			# already migrated and has nothing left on the old key.
			continue

		old_value = old_rows[0][0]

		try:
			new_rows = frappe.db.sql(
				"SELECT value FROM `tabSingles` WHERE doctype=%s AND field=%s",
				(settings.SETTINGS_DOCTYPE, new),
			)
		except Exception as exc:  # pragma: no cover
			print(f"erpnext_mcp: could not read {new!r}: {type(exc).__name__}: {exc}")
			continue

		if new_rows:
			print(
				f"erpnext_mcp: {new!r} already has a stored value ({new_rows[0][0]!r}) — "
				f"leaving it, not overwriting from {old!r} ({old_value!r})."
			)
			continue

		try:
			frappe.db.sql(
				"INSERT INTO `tabSingles` (doctype, field, value) VALUES (%s, %s, %s)",
				(settings.SETTINGS_DOCTYPE, new, old_value),
			)
			frappe.db.commit()
		except Exception as exc:  # pragma: no cover
			print(
				f"erpnext_mcp: could not carry {old!r} ({old_value!r}) to {new!r}: "
				f"{type(exc).__name__}: {exc}"
			)
			continue

		print(f"erpnext_mcp: carried {old!r} = {old_value!r} to {new!r}.")
