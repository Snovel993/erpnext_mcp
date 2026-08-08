# SPDX-License-Identifier: MIT
"""Install / migrate / uninstall hooks.

Eleven jobs. The second arrived in v0.12.0, the third and fourth in v0.15.0,
the fifth — the Farm Task Dispatch Kanban board — in v0.16.0, the sixth —
the six mobile roles — in v0.17.0, the seventh — the compliance vocabulary
and the training curricula — in v0.19.2, the eighth — the Weather Settings
defaults — in v0.19.4, and the ninth — the Sustainable CF/Acre dashboard charts
— in v0.19.5, which v0.19.6 turned into two charts rather than a tenth job. The
tenth — signing the completions filed before v0.20.1 — arrived in v0.20.1, and
the eleventh — the four seeded Inspection Templates — in v0.21.0.

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
sentence of defence. It adds Custom Fields to Spray Log, to Employee, to the
BucketLog bridge and — from v0.19.3 — to Attendance: four doctypes this app did
not create, which is the exact thing `hooks.py` promises it does not do. `compliance_fields.py` argues the case
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

The seventh arrived in v0.19.2 and is two seeders and a migration: the ten
`Compliance Regime` rows, the ten common `Training Type` curricula, and the
one-time conversion of `Employee Training Record.training_type` from free text
into links to those curricula.

IT IS NOT A FRAPPE `fixtures` ENTRY, AND THAT IS DELIBERATE — `test_hooks.py`
forbids the word by name, for the reason the fourth job spells out: a fixture is
imported by `bench migrate` with no ability to skip what a site already has, so
an operator who corrected the regimes on a curriculum would get them silently
corrected back on the next upgrade. Both seeders check before they write and
leave an edited row exactly as it is, including one somebody deactivated. The
regime list is seeded FROM `training.REGIMES`, which stays the single definition
of what a regime is; the table exists so the Desk can offer a picker instead of a
text box, and a picker is what stops somebody typing `OSHA` where the vocabulary
says `OR-OSHA`.

The eighth arrived in v0.19.4 and is the first job's problem on a second Single.
`Weather Settings` ships an HTTP timeout, a cache lifetime and three compliance
thresholds as declared defaults, and until somebody saves the form none of them
has a row in `tabSingles` — so a timeout reads None, becomes zero, and fails every
connection immediately with nothing in a log to say why. It is the same function
as the first job with a doctype argument, so it inherits the same contract: it
fills blanks and never overwrites a choice, including a deliberate "off".

None of the eight raises. Every one of them runs inside `bench migrate`, where an
exception aborts the migration for the whole bench — so a failure here is
reported and the next job still runs. That is not defensive padding: v0.12.0
shipped an `after_migrate` that died on a link validation and left operators with
a traceback instead of an app.

What install does NOT do: generate a token, or set `enabled`. A freshly
installed app must be inert. Turning it on is a decision an operator makes on
the settings form, and there is no code path that makes it for them.
"""

import frappe

from . import (
	compliance_fields,
	dashboard,
	i9_documents,
	i9_print_format,
	roles,
	settings,
	training,
	withholding,
)
from .patches import backfill_completion_signatures, migrate_training_types
from .tools import company


def after_install() -> None:
	settings.seed_defaults()
	_weather_settings()
	_i9_settings()
	_fica_settings()
	company.ensure_party_types()
	_compliance_fields()
	_command_center()
	_dispatch_board()
	_mobile_roles()
	_compliance_vocabulary()
	_kpi_charts()
	_completion_signatures()
	_inspection_templates()
	_compliance_rules()
	_i9_document_types()
	_i9_print_format()
	_federal_tax_table()
	_kpi_definitions()
	_farm_task_templates()
	frappe.db.commit()


def after_migrate() -> None:
	settings.seed_defaults()
	_weather_settings()
	_i9_settings()
	_fica_settings()
	company.ensure_party_types()
	_compliance_fields()
	_command_center()
	_dispatch_board()
	_mobile_roles()
	_compliance_vocabulary()
	_kpi_charts()
	_completion_signatures()
	_inspection_templates()
	_compliance_rules()
	_i9_document_types()
	_i9_print_format()
	_federal_tax_table()
	_kpi_definitions()
	_farm_task_templates()


def _i9_settings() -> None:
	"""Make I-9 Settings' declared defaults true in the database.

	v0.27.0, and the same pattern as _weather_settings — a Frappe Single whose
	defaults need seeding. Skipped silently on a site whose doctype has not
	migrated yet.
	"""
	try:
		if not frappe.db.exists("DocType", "I-9 Settings"):
			return
		settings.seed_defaults("I-9 Settings")
	except Exception as exc:  # pragma: no cover - a site mid-migrate
		print(f"erpnext_mcp: I-9 Settings defaults were not seeded — {type(exc).__name__}: {exc}")


def _i9_document_types() -> None:
	"""Seed the USCIS-accepted documents for I-9 verification.

	v0.27.0. IT ONLY EVER CREATES WHAT IS NOT THERE, checked by doc_title.
	An operator who edited or deactivated a document keeps their change.
	"""
	try:
		report = i9_documents.seed_i9_document_types()
	except Exception as exc:  # pragma: no cover - the seeder swallows its own
		print(f"erpnext_mcp: the I-9 document types were not seeded — {type(exc).__name__}: {exc}")
		return
	if report.get("created"):
		print(f"erpnext_mcp: seeded {len(report['created'])} I-9 document type(s)")
	for failure in report.get("failed") or ():
		print(f"erpnext_mcp: could not seed I-9 document {failure.get('name')} — {failure.get('reason')}")


def _i9_print_format() -> None:
	"""Give the Desk's Print button an I-9 that looks like an I-9.

	v0.47.1. Without it the button renders Frappe's standard format — every one of
	the doctype's eighty-four fields, two to a row, `naming_series` and `pdf_col`
	included. IT ONLY EVER CREATES WHAT IS NOT THERE, by name, so an operator who
	edited the layout for their own printer keeps it through every future migrate.
	"""
	report = i9_print_format.seed_i9_print_format()
	if report.get("created"):
		print(
			f"erpnext_mcp: seeded the {report['name']!r} Print Format for I-9 Form — the Desk's "
			f"Print button now lays the record out as the form's own sections. It is a CUSTOM "
			f"format, so anything you change about it survives the next migrate."
		)
	elif report.get("reason") not in ("already present", ""):
		print(f"erpnext_mcp: the I-9 Print Format was not seeded — {report['reason']}")


def _fica_settings() -> None:
	"""Make FICA Configuration's declared defaults true in the database.

	v0.28.0, and the same pattern as _i9_settings — a Frappe Single whose
	defaults need seeding. Skipped silently on a site whose doctype has not
	migrated yet.
	"""
	try:
		if not frappe.db.exists("DocType", "FICA Configuration"):
			return
		settings.seed_defaults("FICA Configuration")
	except Exception as exc:  # pragma: no cover - a site mid-migrate
		print(f"erpnext_mcp: FICA Configuration defaults were not seeded — {type(exc).__name__}: {exc}")


def _federal_tax_table() -> None:
	"""Seed 2025 federal tax brackets if the table is empty.

	v0.28.0. IT ONLY SEEDS WHEN THE TABLE HAS NO ROWS AT ALL for the seed
	year, so an operator who imported their own brackets or edited the seeded
	ones keeps their data.
	"""
	try:
		if not frappe.db.exists("DocType", "Federal Tax Table"):
			return
		existing = frappe.db.count("Federal Tax Table", {"tax_year": withholding.SEED_TAX_YEAR})
		if existing:
			return
		brackets = withholding.seed_brackets()
		created = 0
		for bracket in brackets:
			doc = frappe.get_doc({
				"doctype": "Federal Tax Table",
				**bracket,
			})
			doc.flags.ignore_permissions = True
			doc.insert()
			created += 1
		if created:
			print(f"erpnext_mcp: seeded {created} federal tax bracket(s) for tax year {withholding.SEED_TAX_YEAR}")
	except Exception as exc:  # pragma: no cover - a site mid-migrate
		print(f"erpnext_mcp: the federal tax table was not seeded — {type(exc).__name__}: {exc}")


def _weather_settings() -> None:
	"""Make Weather Settings' declared defaults true in the database.

	v0.19.4, and the eighth job. Exactly the first job's problem on a second
	Single: a Frappe Single stores one row per field somebody has saved, so
	straight after `bench install-app` `http_timeout_seconds` has no row and reads
	None — which becomes a timeout of ZERO one `int()` later, and a timeout of zero
	is not "no timeout" to `requests`, it is a connection that fails immediately,
	every time, with nothing in a log to say why. `services/weather._setting` falls
	back to the meta default and then to its own constants for exactly this
	reason, but a belt that never gets a brace is a belt somebody eventually
	removes.

	IDEMPOTENT AND IT NEVER OVERWRITES A CHOICE, including a deliberate "off": it
	only fills fields with no stored value, which is the same contract as the
	first job because it is the same function.

	NOT A FRAPPE `fixtures` ENTRY — `test_hooks.py` forbids the word by name. A
	fixture is imported by `bench migrate` with no ability to skip what a site
	already has, so an operator who lowered their heat threshold to 75 °F would
	get it silently raised back to 80 on the next upgrade, and the first anybody
	would know is a hot afternoon that logged nothing.

	Skipped silently on a site whose doctype has not migrated yet — the same
	`bench migrate` that runs this creates it, so the next run finds it.
	"""
	try:
		from .services import weather

		if not frappe.db.exists("DocType", weather.SETTINGS_DOCTYPE):
			return
		settings.seed_defaults(weather.SETTINGS_DOCTYPE)
	except Exception as exc:  # pragma: no cover - a site mid-migrate
		print(f"erpnext_mcp: Weather Settings defaults were not seeded — {type(exc).__name__}: {exc}")


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
		print(f"erpnext_mcp: could not add {failure['doctype']}.{failure['fieldname']} — {failure['reason']}")


def _command_center() -> None:
	"""Build or repair the Compliance Command Center dashboard."""
	_report_failures("the Compliance Command Center", dashboard.install_command_center)


def _dispatch_board() -> None:
	"""Build or repair the Farm Task Dispatch Kanban board and its workspace."""
	_report_failures("the Farm Task Dispatch board", dashboard.install_dispatch_board)


def _kpi_charts() -> None:
	"""Build the Sustainable CF/Acre charts. v0.19.5, and the ninth job.

	Reported through the same printer as the two dashboard builders, and it has
	one failure mode worth printing: each chart's source is a standard Script
	Report created by the SAME `bench migrate` that runs this, so a first pass may
	find a Report row not yet written. That lands in `failed` with the sentence
	saying the next migrate builds it, which is a far better outcome than a chart
	pointing at a report that does not exist — a missing chart renders nothing, and
	a broken one renders an error.

	v0.19.6 MADE IT TWO CHARTS AND EACH IS CHECKED SEPARATELY. The rolling
	twelve-month view is the new default and the discrete quarterly one stays
	beside it; a site part-way through a migrate may have one Report row and not
	the other, and building the chart that CAN be built is strictly better than
	refusing both. The quarterly chart is NOT renamed — a Dashboard Chart's
	docname is what a dashboard points at, and demoting it by renaming the record
	would silently empty the dashboards of every site that installed v0.19.5.
	"""
	_report_failures("the Sustainable CF/Acre charts", dashboard.install_kpi_charts)


def _mobile_roles() -> None:
	"""Create the six v0.17.0 mobile roles and their permissions.

	Reported through the same printer as the two dashboard builders, and for the
	same v0.16.1 reason: a builder that cannot raise and is never read cannot
	report anything at all. A refused permission — one aimed at a doctype
	belonging to another app — lands in `failed` and gets printed here, which is
	the only way anybody would ever find out it did not happen.
	"""
	_report_failures("the mobile roles", roles.install_roles)


def _compliance_vocabulary() -> None:
	"""Seed the regimes and the curricula, then migrate free-text training types.

	ORDER IS LOAD-BEARING, all three steps. A `Training Type` carries regimes as
	links, so the regimes have to exist before any curriculum is written; and every
	curriculum has to exist before a training record is re-linked to one, because a
	link written to a master that is not there yet is a validation error inside a
	migration.

	The migration ALSO runs as a listed patch. Both, on purpose and for the same
	reason `register_custom_party_types` is both: the patch entry records in the
	Patch Log when the conversion first happened, and this hook catches a site that
	upgraded straight across v0.19.2. It is idempotent, so the second run is a
	no-op and prints nothing.
	"""
	for what, seeder in (
		("the compliance regimes", training.seed_regimes),
		("the common training types", training.seed_training_types),
	):
		try:
			report = seeder()
		except Exception as exc:  # pragma: no cover - the seeders swallow their own
			print(f"erpnext_mcp: {what} were not seeded — {type(exc).__name__}: {exc}")
			continue
		for failure in report.get("failed") or ():
			print(f"erpnext_mcp: could not seed {failure.get('name')} — {failure.get('reason')}")

	try:
		report = migrate_training_types.migrate_training_types()
	except Exception as exc:  # pragma: no cover - the migration swallows its own
		print(f"erpnext_mcp: training types were not migrated — {type(exc).__name__}: {exc}")
		return
	for line in migrate_training_types.report_lines(report):
		print(line)


def _completion_signatures() -> None:
	"""Sign the completions filed before v0.20.1, so a re-sent one is recognised.

	v0.20.1, and the tenth job. Runs as a listed patch AS WELL, for the same
	reason `_compliance_vocabulary` runs its migration twice: the patch entry
	records in the Patch Log when the backfill first happened, and this hook
	catches a site that upgraded straight across the version. It is idempotent —
	it only writes rows whose signature is empty — so the second run is a no-op
	and prints nothing.

	ON `after_install` IT WILL FIND NOTHING, which is correct and not worth
	branching on: a site installing this app for the first time has no completed
	assignments to sign, and a job that is only wired into one of the two hooks
	is a job somebody has to remember about later.
	"""
	try:
		report = backfill_completion_signatures.backfill_completion_signatures()
	except Exception as exc:  # pragma: no cover - the backfill swallows its own
		print(f"erpnext_mcp: completion signatures were not backfilled — {type(exc).__name__}: {exc}")
		return
	for line in backfill_completion_signatures.report_lines(report):
		print(line)


def _inspection_templates() -> None:
	"""Seed the four shapes of visit. v0.21.0, and the eleventh job.

	IT ONLY EVER CREATES WHAT IS NOT THERE, checked by template name across every
	row rather than only the live ones — which is the whole difference between
	this and a Frappe `fixtures` entry, and `test_hooks.py` forbids that word by
	name for exactly this reason. An operator who added a section to their
	close-down keeps it. One who deactivated a template the operation does not run
	keeps it deactivated. One who superseded a seeded template with their own
	version 2 does not get version 1 seeded back beside it every migrate — which
	would put two live templates with one name on the site and make the sweep's
	choice between them arbitrary.

	Runs on install AND after every migrate, so a site upgrading from any earlier
	version gets the four on its next migrate rather than needing a bespoke patch.
	"""
	try:
		from . import sessions

		report = sessions.seed_inspection_templates()
	except Exception as exc:  # pragma: no cover - the seeder swallows its own
		print(f"erpnext_mcp: the inspection templates were not seeded — {type(exc).__name__}: {exc}")
		return
	for failure in report.get("failed") or ():
		print(f"erpnext_mcp: could not seed template {failure.get('name')} — {failure.get('reason')}")


def _compliance_rules() -> None:
	"""Migrate the thirteen shipped rules into Compliance Rule records. v0.22.0.

	THE TWELFTH JOB, AND THE ONE WITH THE MOST TO GET WRONG. Until this release
	the compliance rules were Python functions and a threshold was a code change;
	after it they are records, and this is what puts them there on the migrate
	that installs the DocType.

	IT ONLY EVER CREATES WHAT IS NOT THERE, checked by `rule_id` across every row
	rather than only the live ones — the same contract as `_inspection_templates`
	above and the same reason `test_hooks.py` forbids the word `fixtures` by name.
	An operator who moved the annual housing walk from 365 days to 300 keeps 300.
	One who switched off a rule their operation does not run keeps it switched
	off. One who superseded a seeded rule with their own version 2 does not get
	version 1 seeded back beside it every migrate — which would put two live
	definitions of one rule_id on the site and make the sweep's choice between
	them arbitrary.

	THE RULES ARRIVE ENABLED, and that is deliberate against this app's usual
	instinct. Everything mutating here ships off; a migrated rule does not,
	because it was ALREADY RUNNING — as Python — the night before, and seeding it
	disabled would silently switch the whole compliance calendar off during an
	upgrade. Of the two ways to be wrong on a migrate, a calendar that keeps
	saying what it said yesterday is much the better one.

	Runs on install AND after every migrate, so a site upgrading from any earlier
	version gets the thirteen on its next migrate rather than needing a bespoke
	patch. Until it does, the sweep runs the shipped definitions and says so in
	its report — the calendar never goes blank.
	"""
	try:
		from . import compliance_rules

		report = compliance_rules.seed_compliance_rules()
	except Exception as exc:  # pragma: no cover - the seeder swallows its own
		print(f"erpnext_mcp: the compliance rules were not seeded — {type(exc).__name__}: {exc}")
		return
	if report.get("created"):
		print(
			f"erpnext_mcp: seeded {len(report['created'])} compliance rule(s) as records — "
			"thresholds, citations, scope and message are now editable with "
			"update_compliance_rule, with no code release. list_compliance_rules has the register."
		)
	for failure in report.get("failed") or ():
		print(f"erpnext_mcp: could not seed rule {failure.get('name')} — {failure.get('reason')}")
	_declarative_rules()


def _declarative_rules() -> None:
	"""Move the five rules v0.22.1 took declarative onto their new definitions.

	SEPARATE FROM THE SEEDER ABOVE, because the seeder's whole contract is that it
	leaves alone anything already on the site — which is exactly why it can never
	perform this migration. A v0.22.0 site already has a `certification_expiring`
	row naming a built-in scanner, and the seeder will never look at it again.

	Listed in `patches.txt` AND called from here, the same belt-and-braces
	`migrate_training_types` gets and for the same reason: the patch entry records
	in the Patch Log when this first ran, and the hook catches a site that upgraded
	across the version. It is therefore run at least twice on any real bench, and
	is a no-op the second time — the check is "does this row still name a
	scanner", which is false the moment the first run succeeded.
	"""
	try:
		from .patches import migrate_declarative_rules

		report = migrate_declarative_rules.migrate_declarative_rules()
	except Exception as exc:  # pragma: no cover - the patch swallows its own
		print(f"erpnext_mcp: the declarative rule migration did not run — {type(exc).__name__}: {exc}")
		return
	for line in migrate_declarative_rules.report_lines(report):
		print(f"erpnext_mcp: {line}")


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
		"the lease register, in both directions, including rent terms that exist in no other digital form",
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
		"Farm Shift",
		"the shift register — who was on which crew, at which block, from when to "
		"when, what the foreman did about the conditions hour by hour, WHAT THOSE "
		"CONDITIONS ACTUALLY WERE every fifteen minutes, and whose "
		"signature closed it. It is the exposure period every OAR 437-004-1131 "
		"question is asked against, and the per-worker joined/left spans inside it "
		"are what a wage claim turns on. The Attendance rows a close wrote survive; "
		"everything that explains them does not",
	),
	(
		"Heat Exposure Event",
		"the heat records — for each hot shift, whether water was provided at the "
		"required rate, whether shade was in reach, whether the rest cycle was "
		"taken, whether anybody showed signs and what was done about it, and the "
		"supervisor's signature under all of it. The record a serious-injury "
		"investigation is read from, and it exists nowhere else",
	),
	(
		"Normalization Adjustment",
		"the normalization register — every add-back and subtraction on operating "
		"cash flow with the sentence saying why it will not recur and the signature "
		"of whoever accepted that sentence. Losing it does not lose a number, it "
		"loses the DEFENCE of every Sustainable CF/Acre figure ever quoted from this "
		"site, and an adjusted figure nobody can inspect is indistinguishable from "
		"an arranged one",
	),
	(
		"Training Type",
		"the curriculum register — what each course is, which audits a session of "
		"it answers, and how long a record of it has to be kept. The ten this app "
		"seeds come back on a reinstall; a curriculum somebody added, and every "
		"regime somebody corrected on one, does not",
	),
	(
		"Inspection Session",
		"every templated visit: who went to which cabin on which afternoon, which "
		"version of which template they worked from, what they ticked in each "
		"section, and which Housing Inspection and Detector Test came out of it. "
		"The compliance records themselves are warned about separately and go "
		"either way; what dies with this is the CHAIN OF CUSTODY between them — "
		"that those three records are one walk with one signature rather than "
		"three claims filed a minute apart",
	),
	(
		"Inspection Template",
		"the visit register — what a Cabin Opening consists of, what evidence each "
		"section demands, which regulation each answers, and every superseded "
		"version of each. The four this app seeds come back on a reinstall; a "
		"template somebody wrote, and every edit anybody made to a seeded one, "
		"does not — and without the version a session was worked from, that "
		"session can no longer be read against what the worker was actually shown",
	),
	(
		"Mobile Access Grant",
		"who was given a phone, what for, which entities they could see, when their "
		"credential was issued — and, for every account that ended, WHO ended it and "
		"WHY. Frappe keeps the access; it keeps none of the story. 'Left at the end "
		"of harvest' and 'dismissed for cause' are different answers to the same "
		"question and this is the only place either survives",
	),
	(
		"I-9 Form",
		"every structured Form I-9 on the site — Section 1, Section 2, the Section 3 "
		"reverification history carried on the form itself, retention dates, and the status "
		"workflow from Draft through Complete to Destroyed. The only record of employment "
		"eligibility verification for every employee who has one, and for a seasonal worker "
		"on a renewing authorization the only record of every season they were reverified",
	),
	(
		"I-9 Audit Log",
		"the immutable trail of every I-9 action — who created, signed, viewed, printed, "
		"or destroyed each form, from which IP, at which moment. The record an audit asks "
		"for and the thing that says nobody tampered with the form between signing and filing",
	),
	(
		"W-4 Form",
		"every Form W-4 on the site — filing status, dependents credits, extra withholding, "
		"and the supersession chain showing which certificate replaced which. The basis for "
		"every federal withholding calculation and the record an IRS inquiry asks for",
	),
	(
		"Farm Salary Structure",
		"the salary register — which employee earns what rate, by what method (piece, "
		"hourly, salary), from which date. The basis for every payroll calculation and "
		"the record a wage claim or audit asks for",
	),
	(
		"Farm Payroll Entry",
		"the payroll register — every pay period's gross, deductions and net for every "
		"employee, with the per-slip breakdown of federal and state withholding, FICA, "
		"and the minimum wage check. The record that proves everybody was paid correctly",
	),
	(
		"Expense Receipt",
		"the operational expense register — every receipt a foreman photographed in the "
		"field, with the image, the raw OCR text it was read out of, and who approved or "
		"refused it. The photograph is the substantiation a deduction rests on, and it "
		"exists nowhere else once the paper slip is in a truck door pocket",
	),
	(
		"Shift Location Log",
		"the crew tracks — every GPS fix the phones posted during a shift, with the time "
		"each one was taken. It is the only record of where anybody actually went, which "
		"is what answers a re-entry-interval question and a disputed timesheet alike, and "
		"a measurement nobody kept cannot be taken again",
	),
	(
		"Regulation Feed",
		"the regulation register — every source this operation watches for change, and the "
		"append-only change log saying when each one moved. The log is the only record of "
		"WHEN a regulation shifted under a rule that was written from it, which is what an "
		"auditor asks about a citation that has been renumbered twice, and nothing outside "
		"this site holds it",
	),
	(
		"Tax Form",
		"the filing register — every W-2, 1099-NEC, 941, OR-WR, OQ and WA-ESD that was "
		"computed, holding the box and line values exactly as they stood when it was "
		"generated rather than as today's payroll would recompute them, when it was "
		"filed, and what the agency gave back. The record of what an employer told the "
		"IRS and two states, on a date",
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
	# Seeded from `training.REGIMES` on every migrate, so a reinstall restores the
	# ten. A row an operator ADDED for a scheme this app does not model would not
	# come back — but it carries nothing except its own name, and the records that
	# referenced it are gone with their own doctypes anyway.
	("Compliance Regime", "seeded from erpnext_mcp/training.py on every migrate"),
	("Compliance Regime Link", "the regime tags on alerts and training types, rewritten by the sweep"),
	# v0.19.4. A Single whose every field is reseeded from its own declared
	# defaults on the next migrate, so a reinstall restores a working
	# configuration rather than an empty one. The per-company threshold override
	# rows are the exception and are deliberately not warned about: they are a
	# handful of numbers an operator typed and can retype, and the readings they
	# governed go with `Farm Shift` — which IS on the precious list, and whose
	# entry is where the loss of a weather timeline is actually spelled out.
	("I-9 Document Type", "pre-seeded USCIS-accepted document list, rebuilt from i9_documents.py on every migrate"),
	("I-9 Settings", "reseeded from its own declared defaults on every migrate"),
	("FICA Configuration", "reseeded from its own declared defaults on every migrate"),
	("Federal Tax Table", "pre-seeded IRS Pub 15-T brackets, rebuilt from withholding.py on every migrate"),
	("Weather Settings", "reseeded from its own declared defaults on every migrate"),
	(
		"Weather Company Override",
		"per-company threshold rows on Weather Settings — a few numbers, retypable",
	),
	# v0.19.6. THE ONLY DOCTYPE THIS APP SHIPS THAT IS A CACHE, and the reason it
	# is on this list rather than the precious one is worth stating rather than
	# leaving to be inferred from its absence: every row in it is DERIVABLE. Each
	# is what `services/windowed_reports.py` would compute again from GL Entry,
	# the Asset register and the Field register — all of which survive, or are
	# themselves warned about above. Losing the cache loses no fact and no
	# judgement; it loses the speed of the first report somebody opens
	# afterwards, and the overnight sweep has it back by morning.
	(
		"Financial KPI History",
		"precomputed windowed KPI values — recomputed from the ledger by the overnight sweep",
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

	grafted = _compliance_field_losses()
	_report_surviving_roles()
	if not losses and not grafted:
		return

	if losses:
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


def _kpi_definitions() -> None:
	"""Seed Sustainable CF/Acre as the first Financial KPI Definition. v0.39.0.

	THE FOURTEENTH JOB, AND IT SEEDS EXACTLY ONE RECORD. That is the argument
	rather than a starting point somebody has not got round to finishing.

	A seeded KPI is a claim that this app knows what an operation should watch,
	and it can only honestly make that claim about a metric it also ships the
	computer for. Sustainable CF/Acre qualifies: the computer is tested, the
	reasoning is four pages in `docs/kpi_sustainable_cf_per_acre.md`, and the KPI
	is the reason v0.19.5 exists. A current ratio does not — not because it is a
	bad metric, but because the accounts that make it up are named differently on
	every chart, and a seeded definition pointing at the wrong ones would put a
	confident, precise, WRONG number on somebody's dashboard from the day they
	installed the app. `create_financial_kpi_definition` is how the rest arrive,
	authored by the operation whose accounts they are about.

	IT SEEDS NO THRESHOLDS EITHER, for the same reason. A defensible floor under
	cash flow per acre is a number about one operation's own cost structure and
	debt service, and a seeded one would be a line somebody had not drawn being
	enforced on a compliance calendar.

	THE `kpi_id` IS THE ONE THE CACHE ALREADY USES. Every Financial KPI History
	row written since v0.19.6 carries `kpi_key = "sustainable_cf_per_acre"`, so
	the seeded definition adopts that series rather than starting a second one
	beside it — which would be the unmarked join the whole framework is written
	against.

	Checked by `kpi_id` and idempotent, like every seeder here: an operator who
	disabled it stays disabled, one who set thresholds keeps them, one who moved
	it down the dashboard does not find it back at the top after every migrate.
	"""
	try:
		from .services import kpi_engine

		report = kpi_engine.seed_kpi_definitions()
	except Exception as exc:  # pragma: no cover - the seeder swallows its own
		print(f"erpnext_mcp: the KPI definitions were not seeded — {type(exc).__name__}: {exc}")
		return
	if report.get("created"):
		print(
			f"erpnext_mcp: seeded {len(report['created'])} financial KPI definition(s) — a KPI is "
			"now a record, so its window, thresholds and dashboard position are editable with "
			"update_financial_kpi_definition and new ones need no code release. "
			"list_financial_kpi_definitions has the register."
		)
	for failure in report.get("failed") or ():
		print(f"erpnext_mcp: could not seed KPI {failure.get('name')} — {failure.get('reason')}")


def _farm_task_templates() -> None:
	"""Seed the five shapes of single task. v0.41.0.

	THE FIVE ARE THE SHIPPED COMPLIANCE RULES WHOSE WORK REPEATS: a cabin
	habitability inspection, a smoke detector test, a water quality test, a
	certification renewal and a training record. Every type, skill, duration,
	dispatch mode and evidence contract on them matches `ALERT_TASK_MAP` in
	`tools/dispatch.py` to the letter, which is the backward-compatibility
	guarantee: a site that points its rules at these templates raises exactly the
	tasks it raised in v0.16.0, plus a checklist.

	NOTHING IS WIRED BY THIS JOB. Seeding a template does not point any rule at
	one — `Compliance Rule.producer_task_template` is left exactly as it was, so
	an upgrade changes no task any sweep produces. Pointing a rule at a template
	is a deliberate act, through `update_compliance_rule`, by somebody who has
	read what the template asks for. An upgrade that silently changed the shape of
	the work a calendar dispatches is the one thing this app will not do.

	IT ONLY EVER CREATES WHAT IS NOT THERE, checked by template name — the same
	contract `_inspection_templates` and `_compliance_rules` keep, and the same
	reason `test_hooks.py` forbids the word `fixtures` by name. An operator who
	added an item to the detector checklist keeps it. One who disabled a template
	their operation does not run keeps it disabled.

	Runs on install AND after every migrate, so a site upgrading from any earlier
	version gets the five on its next migrate rather than needing a bespoke patch.
	"""
	try:
		from . import task_templates

		report = task_templates.seed_farm_task_templates()
	except Exception as exc:  # pragma: no cover - the seeder swallows its own
		print(f"erpnext_mcp: the farm task templates were not seeded — {type(exc).__name__}: {exc}")
		return
	if report.get("created"):
		print(
			f"erpnext_mcp: seeded {len(report['created'])} farm task template(s) — the shape of a "
			"recurring job is now a record, so its skill, duration, evidence contract and "
			"checklist are editable with update_farm_task_template and new ones need no code "
			"release. list_farm_task_templates has the register. NOTHING WAS WIRED: point a "
			"Compliance Rule at one with update_compliance_rule(producer_task_template=...) when "
			"you have read what it asks for."
		)
	for failure in report.get("failed") or ():
		print(f"erpnext_mcp: could not seed farm task template {failure.get('name')} — {failure.get('reason')}")
