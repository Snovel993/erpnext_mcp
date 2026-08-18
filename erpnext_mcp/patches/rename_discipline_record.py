# SPDX-License-Identifier: MIT
"""v0.94.0. Rename `Discipline Record` to `Farm Incident Record`, in place.

WHY THE NAME WAS WRONG. The doctype holds one incident-reporting and resolution
protocol that runs in two directions — the farm documenting a worker, and a
worker raising a grievance or a dispute. Both voices were on the record from the
start: `employee_statement` sits beside `manager_signature` on one page. What it
could not express was the worker being the one who OPENS it, and v0.94.0 adds
`report_direction` for exactly that. From that moment "Discipline Record" is the
wrong name for half the rows, and it is a name every future reader would have to
be told to ignore.

NOW IS THE CHEAPEST MOMENT THIS WILL EVER BE. The blast radius is server-side
only — the iOS app has no discipline surface yet — and it grows the day one is
built.

WHAT THIS DOES. `frappe.rename_doc` on the DocType itself, which moves the table
and repoints every Link, followed by a repoint of this doctype's own
self-referential `prior_record` column. The chain is what the whole feature is,
and a chain whose links point at a table name that no longer exists is not a
chain.

IT IS IDEMPOTENT AND NEVER RAISES. A site already renamed finds the new name and
returns; a site that has not migrated the DocType at all has nothing to rename
and says so. It runs inside `bench migrate`, where an exception aborts the
migration for the whole bench, so every branch reports and none throws.

────────────────────────────────────────────────────────────────────────────
WHAT THIS DELIBERATELY DOES **NOT** TOUCH: THE TOOL SWITCHES
────────────────────────────────────────────────────────────────────────────

`settings.tool_enabled` resolves a tool's switch as `allow_<tool_name>`, so the
key is derived from the TOOL name and not from the doctype's. The six tools keep
their names in v0.94.0 — `create_discipline_record`, `acknowledge_discipline_record`,
`get_discipline_record`, `list_discipline_history`, `get_discipline_report`,
`expire_discipline_record` — which means every stored switch value on the
operator's site keeps resolving to the same tool after this patch.

THAT IS THE POINT, NOT AN OVERSIGHT. Three of those six default to `0`:

    allow_create_discipline_record        default '0'
    allow_acknowledge_discipline_record   default '0'
    allow_expire_discipline_record        default '0'

Renaming the tools would rename their switches, the operator's stored values
would stay on the old fieldnames, and a site with `allow_create_discipline_record`
switched ON today would read a brand-new Check defaulting to `0` and arrive with
the tool DISABLED — at the exact moment this release is widening it to foremen.
The standalone suite could not catch it, because the harness does not carry the
deployed settings document.

So the doctype is renamed and the tools are not. If a later release does rename
them, it MUST carry each `allow_<old>` value across to `allow_<new>` in the same
patch, and assert both directions: a site with the old switch ON ends with the
new switch ON, and a site with it OFF stays OFF.
"""

import frappe

OLD = "Discipline Record"
NEW = "Farm Incident Record"
SUPERVISOR = "Supervisor Report"


def execute():
	if not frappe.db.exists("DocType", OLD):
		if frappe.db.exists("DocType", NEW):
			print(f"erpnext_mcp: {NEW} is already named that — nothing to rename.")
		else:
			print(
				f"erpnext_mcp: neither {OLD!r} nor {NEW!r} is on this site, so there is nothing "
				"to rename. The DocType arrives with the next migrate."
			)
		return

	try:
		frappe.rename_doc("DocType", OLD, NEW, force=True, ignore_permissions=True)
		frappe.db.commit()
	except Exception as exc:  # pragma: no cover - reported, never fatal to a migrate
		print(
			f"erpnext_mcp: could not rename {OLD!r} to {NEW!r}: {type(exc).__name__}: {exc}. "
			"The records are untouched and still readable under the old name. Rename it in the "
			"Desk, or re-run `bench migrate` once the cause is cleared."
		)
		return

	print(f"erpnext_mcp: renamed DocType {OLD!r} → {NEW!r}.")
	_backfill_direction()
	_repoint_prior_records()


def _backfill_direction():
	"""Every existing row is a Supervisor Report, because that is all it could be.

	THIS IS NOT COSMETIC. `chain_for` filters on `report_direction`, and a NULL
	column matches neither `= 'Supervisor Report'` nor `!= 'Worker Report'` — SQL
	makes the second one NULL, so the row drops out silently. The tool layer
	defends against that in Python by treating empty as supervisor, and this
	writes the fact down so the column means something to anybody reading the
	table directly: a Desk list view, a report builder, a SQL query in an audit.

	It is the same statement either way, so it is idempotent — a second run
	updates nothing because nothing is left blank.
	"""
	try:
		frappe.db.sql(
			f"""UPDATE `tab{NEW}`
			    SET report_direction = %s
			    WHERE report_direction IS NULL OR report_direction = ''""",
			(SUPERVISOR,),
		)
		frappe.db.commit()
	except Exception as exc:  # pragma: no cover
		print(
			f"erpnext_mcp: could not backfill report_direction on {NEW}: "
			f"{type(exc).__name__}: {exc}. The tool layer treats a blank as "
			f"{SUPERVISOR!r} regardless, so no chain read is affected."
		)
		return
	print(f"erpnext_mcp: every existing {NEW} is marked {SUPERVISOR!r} — that is all the table held.")


def _repoint_prior_records():
	"""The self-referential column, which `rename_doc` cannot know to follow.

	`prior_record` links each step to the one before it, and that chain IS the
	progressive-discipline defence. Frappe repoints Links whose OPTIONS name the
	renamed doctype, so this is belt-and-braces on the one column pointing at its
	own table — and it verifies rather than assumes, printing what it found.
	"""
	try:
		orphaned = frappe.db.sql(
			f"""SELECT name, prior_record FROM `tab{NEW}`
			    WHERE prior_record IS NOT NULL AND prior_record != ''
			      AND prior_record NOT IN (SELECT name FROM `tab{NEW}`)""",
			as_dict=True,
		)
	except Exception as exc:  # pragma: no cover
		print(f"erpnext_mcp: could not verify the {NEW} chain: {type(exc).__name__}: {exc}")
		return

	if not orphaned:
		print(f"erpnext_mcp: every {NEW} prior_record still resolves — the chain is intact.")
		return

	# Named rather than cleared. A broken link in a discipline chain is a fact
	# somebody has to look at, and silently blanking it would destroy the only
	# evidence that the chain was ever complete.
	for row in orphaned:
		print(
			f"erpnext_mcp: {NEW} {row['name']} has prior_record {row['prior_record']!r} which "
			"resolves to nothing. Left as-is deliberately — a broken link in a discipline chain "
			"is evidence about the chain, and clearing it would destroy the only trace."
		)
