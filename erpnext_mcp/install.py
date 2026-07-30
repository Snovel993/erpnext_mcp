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


#: Doctypes whose contents are records an operator would want back, and what
#: each one is, in the words somebody reading an uninstall prompt needs.
#:
#: The governance three are here for a reason the audit log is not: they are the
#: only copy. An MCP Action Log row records something that also happened
#: somewhere else, but a Cap Table Entry is the *only* place a member id is
#: mapped to a legal name, and a Governance Document may hold the only digital
#: copy of a trust instrument. Dropping those silently would be unforgivable.
_PRECIOUS_DOCTYPES = (
	("MCP Action Log", "the audit trail of every MCP call"),
	("Cap Table Entry", "the member register — the only mapping from member id to legal name"),
	("Member Event", "the equity trail: contributions, distributions, transfers and their narratives"),
	("Governance Document", "the governance archive, including any attached agreements"),
	("Asset Cost Profile", "asset cost splits, note links and depreciation history"),
	(
		"Note Payable",
		"the notes and loans register — terms, provenance and payment history for "
		"debts whose only other record is a balance on a liability account",
	),
	(
		"Parcel",
		"the land register — assessor parcel ids, acreage, appraised values and the "
		"dates they were appraised as of, none of which is anywhere else on the site",
	),
	(
		"Lease",
		"the lease register, in both directions, including rent terms that exist in "
		"no other digital form",
	),
	(
		"Related Party",
		"the related-party register — who is related to the company, in what "
		"capacity, from when, and which document says so. The source for a "
		"related-party disclosure on a return",
	),
)


def before_uninstall() -> None:
	"""Warn about every record that goes with the app, while there is time to export.

	Frappe drops an app's doctypes and their tables on uninstall. An operator
	uninstalling for compliance reasons is exactly the person who wanted to keep
	this, so it is spelled out rather than discovered afterwards.
	"""
	losses = []
	for doctype, what in _PRECIOUS_DOCTYPES:
		try:
			count = frappe.db.count(doctype)
		except Exception:
			continue
		if count:
			losses.append((doctype, count, what))
	if not losses:
		return

	lines = "\n".join(f"  {count:>6}  {doctype} — {what}" for doctype, count, what in losses)
	exports = "\n".join(
		f"  bench --site <site> backup --only-doctype '{doctype}'" for doctype, _count, _what in losses
	)
	print(
		"\nerpnext_mcp: uninstalling will drop these records permanently:\n"
		f"{lines}\n\n"
		"Attachments on a Governance Document are Files and survive the uninstall, "
		"but nothing will say which document they belonged to.\n"
		"To keep any of it, export first — in the Desk via Report View > Menu > "
		"Export, or:\n"
		f"{exports}\n"
	)
