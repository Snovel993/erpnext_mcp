# SPDX-License-Identifier: MIT
"""Install / migrate / uninstall hooks.

The one job here is making the DocType JSON's declared defaults *true in the
database*. A Frappe Single stores a row per field that has been set, so straight
after `bench install-app` the settings document has no rows at all and every
field reads as None — which for the read-tool switches (default ON) would look
like "everything is disabled". `settings.seed_defaults()` writes them out.

It runs on install and after every migrate, so a version that adds a tool gets
its switch seeded without a bespoke patch. It only ever fills in fields with no
stored value, so it cannot undo an operator's decision — including a deliberate
"off".

What install does NOT do: generate a token, or set `enabled`. A freshly
installed app must be inert. Turning it on is a decision an operator makes on
the settings form, and there is no code path that makes it for them.
"""

import frappe

from . import settings


def after_install() -> None:
	settings.seed_defaults()
	frappe.db.commit()


def after_migrate() -> None:
	settings.seed_defaults()


def before_uninstall() -> None:
	"""Warn that the audit trail goes with the app.

	Frappe drops an app's doctypes and their tables on uninstall, so the MCP
	Action Log history disappears. An operator uninstalling for compliance
	reasons is exactly the person who wanted to keep it, so say so while there
	is still time to export.
	"""
	try:
		count = frappe.db.count("MCP Action Log")
	except Exception:
		return
	if count:
		print(
			f"\nerpnext_mcp: uninstalling will drop {count} MCP Action Log row(s) "
			"— the audit trail of every MCP call.\n"
			"To keep it, export the doctype first: open MCP Action Log in Report "
			"View and use Menu > Export, or run\n"
			"  bench --site <site> backup --only-doctype 'MCP Action Log'\n"
		)
