# SPDX-License-Identifier: MIT
"""Install / migrate / uninstall hooks.

Six jobs. The second arrived in v0.12.0, the third and fourth in v0.15.0,
the fifth — the Farm Task Dispatch Kanban board — in v0.16.0, and the sixth —
the six mobile roles — in v0.17.0.

The first is making the DocType JSON's declared defaults *true in the
database*. A Frappe Single stores a row per field that has been set, so straight
after `bench install-app` the settings document has no rows at all and every
field reads as None — which for the read-tool switches (default ON) would look
like "everything is disabled". `settings.seed_defaults()` writes them out.

It runs on install and after every migrate, so a version that adds a tool gets
its switch seeded without a bespoke patch. It only ever fills in fields with no
stored value, so it cannot undo an operator's decision — including a deliberate
"off".

The second is registering the two custom Party Types — `Family` and `Contact`.
Those are not settings; they are records a Journal Entry line links to, and a
site without them cannot book a payment to a family member at all. Seeding them
here rather than in a one-shot patch means a site upgrading from any earlier
version gets them on its next migrate, and re-running is a no-op because the
seeder checks before it inserts.

Registering a Party Type changes nothing that already exists. Rules and entries
using Shareholder, Employee or Supplier keep working exactly as they did; this
adds options, it does not reclassify anything.

The third is the v0.15.0 compliance fields, and it is the one that needs a
sentence of defence. It adds Custom Fields to Spray Log, to Employee and to the
BucketLog bridge — three doctypes this app did not create — which is the exact
thing `hooks.py` promises it does not do. `compliance_fields.py` argues the case
at length; the short version is that compliance woven into the operational record
is defensible under audit and a shadow log beside it is not, and you cannot weave
anything into a doctype you refuse to touch. It is the only such exception in the
app, it is behind a switch, and `before_uninstall` names the cost.

The fourth is the Compliance Command Center: a Dashboard, its Charts and its
Number Cards, built idempotently on every migrate, checking before it writes.
Deliberately NOT shipped as `fixtures`, which `test_hooks.py` forbids by name: a
fixture is imported by `bench migrate` with no ability to skip what a site
already has, and an operator who rearranged their dashboard would get it
silently rearranged back.

The fifth arrived in v0.16.0 and is the Farm Task Dispatch Kanban board, plus the
workspace that lands somebody on it. Built exactly like the fourth and for
exactly the same reasons — an existing board is left alone, including every
column somebody has since reordered or deleted.

The sixth arrived in v0.17.0: the six mobile roles — Field Worker, Foreman,
Compliance Officer, Farm Manager, Family Member, Advisor — and their Custom
DocPerm rows. It is the second job here that touches something outside this app's
own records, and it is the one with the sharpest edge, which `roles.py` spends
forty lines on: **the moment any Custom DocPerm row exists for a doctype, Frappe
ignores every STANDARD permission on that doctype, for every role on the site.**
So the installer mirrors the standard perms into custom ones first, and refuses
outright to write a permission onto a doctype this app does not own. A role
nobody holds changes nothing; a Custom DocPerm on somebody else's doctype could
have taken HR Manager off Employee during a migration with nothing printed.

None of the six raises. Every one of them runs inside `bench migrate`, where an
exception aborts the migration for the whole bench — so a failure here is
reported and the next job still runs. That is not defensive padding: v0.12.0
shipped an `after_migrate` that died on a link validation and left operators with
a traceback instead of an app.

What install does NOT do: generate a token, or set `enabled`. A freshly
installed app must be inert. Turning it on is a decision an operator makes on
the settings form, and there is no code path that makes it for them.
"""

import frappe

from . import compliance_fields, dashboard, roles, settings
from .tools import company


def after_install() -> None:
	settings.seed_defaults()
	company.ensure_party_types()
	_compliance_fields()
	_command_center()
	_dispatch_board()
	_mobile_roles()
	frappe.db.commit()


def after_migrate() -> None:
	settings.seed_defaults()
	company.ensure_party_types()
	_compliance_fields()
	_command_center()
	_dispatch_board()
	_mobile_roles()


def _compliance_fields() -> None:
	"""Add the v0.15.0 compliance fields, reporting anything that could not be done.

	`install_compliance_fields` already never raises; this wraps it anyway,
	because the thing that would take a migration down is not the installer
	failing, it is the installer failing in a way the installer did not anticipate.
	"""
	try:
		report = compliance_fields.install_compliance_fields()
	except Exception as exc:  # pragma: no cover - the installer already swallows its own
		print(f"erpnext_mcp: compliance fields were not installed — {type(exc).__name__}: {exc}")
		return
	for failure in report.get("failed") or ():
		print(
			f"erpnext_mcp: could not add {failure['doctype']}.{failure['fieldname']} — "
			f"{failure['reason']}"
		)


def _command_center() -> None:
	"""Build or repair the Compliance Command Center dashboard."""
	_report_failures("the Compliance Command Center", dashboard.install_command_center)


def _dispatch_board() -> None:
	"""Build or repair the Farm Task Dispatch Kanban board and its workspace."""
	_report_failures("the Farm Task Dispatch board", dashboard.install_dispatch_board)


def _mobile_roles() -> None:
	"""Create the six v0.17.0 mobile roles and their permissions.

	Reported through the same printer as the two dashboard builders, and for the
	same v0.16.1 reason: a builder that cannot raise and is never read cannot
	report anything at all. A refused permission — one aimed at a doctype
	belonging to another app — lands in `failed` and gets printed here, which is
	the only way anybody would ever find out it did not happen.
	"""
	_report_failures("the mobile roles", roles.install_roles)


def _report_failures(what: str, builder) -> None:
	"""Run a dashboard builder and PRINT WHATEVER IT COULD NOT BUILD.

	THIS FUNCTION IS THE v0.16.1 HOTFIX, and it matters more than either of the
	two bugs it was written for.

	Both builders catch their own exceptions into `report["failed"]` and return a
	report — which is right, because an exception here aborts `bench migrate` for
	the whole bench. But v0.16.0 called them and threw the report away. So when
	the Kanban Board insert failed on a real site the migration printed nothing,
	exited zero, and the board did not exist; the first anybody knew was an
	operator opening the documented route a week later and being offered a "New
	Kanban Board" dialog.

	A builder that cannot raise AND is never read cannot report anything at all.
	Not raising was the correct half; this is the half that was missing.
	"""
	try:
		report = builder()
	except Exception as exc:  # pragma: no cover - the builder already swallows its own
		print(f"erpnext_mcp: {what} was not built — {type(exc).__name__}: {exc}")
		return
	for failure in (report or {}).get("failed") or ():
		print(f"erpnext_mcp: could not build {failure.get('name')} — {failure.get('reason')}")
	# v0.18.5. A repair is the one thing this installer does to a document somebody
	# else may have edited, so it says so by name. Reading "repaired the filters on
	# Tasks in the Pool" in a migrate log is how an operator who had customised that
	# card finds out, rather than wondering later why it counts what it counts.
	repaired = (report or {}).get("repaired_filters") or ()
	if repaired:
		print(
			f"erpnext_mcp: rewrote dict-shaped filters into list form on {len(repaired)} card(s)/"
			f"chart(s) of {what}, which could not be counted otherwise: {', '.join(repaired)}"
		)


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
		"the land register — assessor parcel ids, acreage, appraised values, the "
		"dates they were appraised as of, and the conveyance history of any ground "
		"that has changed entities, none of which is anywhere else on the site",
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
	(
		"Family",
		"the family register — the people a Family-party posting points at. Deleting "
		"it orphans every journal entry that named them, and those postings are the "
		"record of money that moved",
	),
	(
		"Field",
		"the block register — acreage, variety, rootstock, planting year and the "
		"food-safety facts about each piece of planted ground, including the last "
		"spray date a Worker Protection Standard report is built from",
	),
	(
		"Irrigation Zone",
		"the irrigation register — water sources, Oregon water right numbers, flow "
		"rates and the agricultural water test dates FSMA Subpart E turns on",
	),
	(
		"Housing Unit",
		"the labor camp register — every cabin and building with its capacity, "
		"condition, habitability inspection and detector test dates",
	),
	(
		"Housing Assignment",
		"who slept where and when. The audit trail defending an IRS Section 119 "
		"exclusion, the answer to an ORS 653 wage-deduction claim, and the camp "
		"roster a food safety investigation asks for. It exists nowhere else",
	),
	(
		"Compliance Policy",
		"the SOP library — harvest hygiene procedures, spray SOPs, worker training "
		"documents, with their versions, their effective dates and the PDFs "
		"attached. An audit asks which procedure was in force on a date, and this "
		"is the only record that answers",
	),
	(
		"Certification",
		"the certificate and licence register — GAP, GlobalGAP, PrimusGFS, organic, "
		"applicator and farm labor contractor licences, with issue and expiration "
		"dates and the certificates themselves. Operating without a current one is "
		"a violation, and this is what says whether it is current",
	),
	(
		"Regulatory Filing",
		"what was filed, to whom, on what date, under what docket number, and what "
		"they said back. A filing nobody can prove was made is a filing that was "
		"not made",
	),
	(
		"Audit Event",
		"every third-party audit and agency inspection, its findings, and whether "
		"each corrective action was ever closed. The single most damaging record to "
		"lose: an open corrective action nobody can produce a closure for is how a "
		"finding becomes a penalty",
	),
	(
		"Farm Task",
		"the dispatch register — what work was raised, from which compliance alert, "
		"what evidence closing it required and what it produced. The record that an "
		"alert was not merely read but answered",
	),
	(
		"Farm Task Assignment",
		"the chain of custody: who took each job, when they claimed it, when they "
		"started, when they finished, what they found, what proves it — and, for "
		"every job somebody could NOT do, the reason they gave. That last one exists "
		"nowhere else and is the answer to 'why was this never done'",
	),
	(
		"Housing Inspection",
		"every habitability walk of every cabin, with its findings and its "
		"photographs. The evidence behind OAR 437-004-1120 and 29 CFR 1910.142, and "
		"the only record that says a building somebody slept in was fit to",
	),
	(
		"Detector Test",
		"every smoke and CO detector test in the camp. A propane heater in a cabin "
		"with an untested CO detector is how somebody dies in their sleep, and this "
		"is the only record that says anybody checked",
	),
	(
		"Water Test",
		"every agricultural water sample, what the laboratory said, and the report "
		"itself. FSMA Subpart E asks whether the water that touched a harvested crop "
		"was tested, and nothing else on the site can answer",
	),
	(
		"Mobile Access Grant",
		"who was given a phone, what for, which entities they could see, when their "
		"credential was issued — and, for every account that ended, WHO ended it and "
		"WHY. Frappe keeps the access; it keeps none of the story. 'Left at the end "
		"of harvest' and 'dismissed for cause' are different answers to the same "
		"question and this is the only place either survives",
	),
)

#: Doctypes that go with the app and are NOT worth warning about, with why. The
#: list exists so a reader can tell "deliberately omitted" from "forgotten".
#:
#: Compliance Alert is regenerated from operational state by the nightly job, so
#: losing it loses nothing that cannot be rebuilt in one scheduler tick. The only
#: irreplaceable thing on one is a human's dismissal reason, and a dismissal on
#: an alert whose source condition still holds comes back anyway.
_REGENERATED_DOCTYPES = (
	("Compliance Alert", "regenerated from operational state by the nightly sweep"),
	("Staged File Upload Session", "half-finished uploads"),
	("Staged File Chunk", "half-finished uploads"),
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

	grafted = _compliance_field_losses()
	_report_surviving_roles()
	if not losses and not grafted:
		return

	if losses:
		lines = "\n".join(f"  {count:>6}  {doctype} — {what}" for doctype, count, what in losses)
		exports = "\n".join(
			f"  bench --site <site> backup --only-doctype '{doctype}'"
			for doctype, _count, _what in losses
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

	if grafted:
		columns = "\n".join(f"  {doctype}.{fieldname}" for doctype, fieldname in grafted)
		print(
			"\nerpnext_mcp: it will ALSO drop these columns from doctypes belonging to "
			"OTHER apps, and everything anybody has typed into them:\n"
			f"{columns}\n\n"
			"These are the v0.15.0 compliance fields. The records they sit on — spray "
			"logs, employees, bucket log entries — survive the uninstall; the applicator "
			"names, EPA registration numbers, REIs, PHIs, I-9 statuses and traceability "
			"links do not. Export the affected doctypes BEFORE uninstalling, not after:\n"
			f"{chr(10).join(sorted({f'  bench --site <site> backup --only-doctype {doctype!r}' for doctype, _f in grafted}))}\n"
		)


def _report_surviving_roles() -> None:
	"""Say what UNINSTALLING DOES NOT REMOVE, which is the other half of honesty.

	The six v0.17.0 roles are rows in Frappe's own `Role` table and the User
	Permissions are rows in `User Permission`. Neither belongs to this app, so
	neither is dropped — and an operator uninstalling to revoke a fleet of phones
	would otherwise believe they had. They have not: the accounts still exist,
	still hold the roles, and still have live API credentials. What they lose is
	the ability to reach the MCP endpoint, which is a different thing.

	`_PRECIOUS_DOCTYPES` warns about what goes. This warns about what stays,
	because a surviving credential nobody knows about is worse than a lost
	record somebody was told about.
	"""
	try:
		held = [
			name
			for name in roles.ROLE_NAMES
			if frappe.db.exists("Role", name)
			and frappe.db.count("Has Role", {"role": name, "parenttype": "User"})
		]
	except Exception:
		return
	if not held:
		return
	print(
		"\nerpnext_mcp: uninstalling does NOT remove these, and they are not this app's to "
		"remove:\n"
		+ "\n".join(f"  the {name} role, and every user holding it" for name in held)
		+ "\n  every Company User Permission this app wrote\n"
		+ "  every API key and secret on those users\n\n"
		"Those accounts will lose the MCP endpoint and keep everything else — including "
		"live credentials for Frappe's own REST API. To actually end mobile access, run "
		"revoke_mobile_user for each account BEFORE uninstalling, or disable the users "
		"afterwards by hand.\n"
	)


def _compliance_field_losses() -> list:
	"""The v0.15.0 Custom Fields that are on this site, as (doctype, fieldname).

	Only the ones this app grafted onto somebody ELSE'S doctype — the `verify`
	targets are declared fields of this app's own doctypes and go with those, and
	are already covered by `_PRECIOUS_DOCTYPES`.
	"""
	out = []
	for target in compliance_fields.TARGETS:
		if target.mode != "extend":
			continue
		for spec in target.fields:
			try:
				if frappe.db.exists(
					compliance_fields.CUSTOM_FIELD,
					{"dt": target.doctype, "fieldname": spec.fieldname},
				):
					out.append((target.doctype, spec.fieldname))
			except Exception:
				continue
	return out
