# SPDX-License-Identifier: MIT
"""The seven mobile roles, and the installer that puts them on a site.

v0.17.0. Sprint 8 gave the operation a dispatch board. This is what makes it
safe to point a phone at from outside the LAN: roles that say what KIND of
work somebody does, and Frappe's own User Permissions that say WHOSE.

THAT SPLIT IS THE WHOLE DESIGN, AND IT IS WHY NO COMPANY NAME APPEARS IN THIS
FILE. A role is a job description — a Field Worker fills in assignments and
files evidence; a Compliance Officer keeps the certificate register — and a job
description is the same job at Constancy Farms as it is at Highland. Which
entity's records somebody may see is a different question with a different
answer per person, and Frappe already answers it: a User Permission row on
Company scopes every document that links to a Company, for that user, across
every doctype at once.

Bolting entity names into the roles instead would have produced "Field Worker —
Constancy Farms", "Field Worker — Highland", and a new role every time a family
adds an LLC. It would also have made this app specific to one install, which is
the promise the module docstring in `hooks.py` opens with.

So: SEVEN ROLES, and `create_mobile_user` writes the User Permissions.

  Field Worker        the phone in the orchard. Reads the pool and the job,
                      writes their own assignment and the evidence on it.
  Foreman             the dispatch board. Raises work, sends people to it,
                      reads the compliance calendar for the operating company.
  Crew Leader         v0.68.1. The board-less half of a Foreman: forms the crew,
                      calls the breaks, closes the shift and writes its
                      Attendance. Cannot raise, assign or cancel work. For sites
                      where the crew lead is not the foreman — a distinction
                      `employee.SHIFT_ROLES` and the iOS toolbar already made,
                      against a role this file did not create until now.
  Compliance Officer  the registers: policies, certificates, filings, audits.
                      Cannot dispatch anybody — see below.
  Farm Manager        operations and the ground under them: tasks, compliance,
                      parcels, leases, housing. Not the investment side.
  Family Member       the holding-company view: cap table, member events,
                      governance, related parties, the land. Not the operator's
                      day-to-day task list.
  Advisor             the narrowest role in the app: governance documents,
                      related parties and regulatory filings, read-only.

"CHECKER" AND "TRACTOR DRIVER" ARE NOT ROLES AND ARE NOT MEANT TO BE. They are
JOB TITLES — `Employee.designation` — and this app already reads that column:
`list_pending_threshold_acknowledgments` finds every checker on the site by
filtering Active Employees on it. The test for a role is not "is it a distinct
job", it is "does it touch a DIFFERENT SET OF RECORDS", and neither of those two
does: v0.68.1 gave the Field Worker role the one read a checker was missing (the
fill band) instead of inventing a role for it. `JOB_TITLES` below is that mapping
written down and returned by `list_mobile_users`, so "we hired a checker, what do
we give them" has a machine-readable answer.

TWO REFUSALS WORTH ARGUING FOR, BECAUSE BOTH LOOK LIKE OVERSIGHTS:

**A Compliance Officer cannot dispatch a task.** They get `read` on Farm Task
and nothing else. That is not a slight — it is the separation that makes the
compliance record worth anything. The person who decides a walk is required and
the person who decides who walks it are different people, and a role that did
both could raise a task, assign it to themselves, and close it. An auditor is
trained to look for exactly that.

**A Field Worker cannot read a Compliance Policy.** They get the job, the
evidence contract, and the pool. The SOP library is a different surface with
different privacy — it names procedures, versions and effective dates that an
operation's certification hangs on — and a worker who needs one gets it in the
task's `notes`, put there by the person who raised the job. The tests assert
this in both directions, because "Field Worker cannot read Compliance Policy;
Compliance Officer can" is the shortest true statement of what these roles are.

────────────────────────────────────────────────────────────────────────────
THE CUSTOM DocPerm TRAP, WHICH IS THE MOST DANGEROUS THING IN THIS FILE
────────────────────────────────────────────────────────────────────────────

Frappe resolves a role's permissions like this (`frappe.permissions.get_all_perms`,
paraphrased and unchanged in substance since v13):

    perms         = every DocPerm for the role
    custom_perms  = every Custom DocPerm for the role
    doctypes_with_custom_perms = distinct parent from `tabCustom DocPerm`
    for p in perms:
        if p.parent not in doctypes_with_custom_perms:
            custom_perms.append(p)

Read the third line again. **The moment ANY Custom DocPerm row exists for a
doctype, EVERY standard DocPerm on that doctype is ignored — for every role on
the site, not just the one the row was written for.** Adding one row granting
Field Worker read on Employee would silently revoke HR Manager, HR User and
System Manager from Employee, on a live site, during `bench migrate`, with
nothing printed.

That is not a hypothetical. It is what Frappe's own Role Permission Manager
guards against with `setup_custom_perms`, which copies every DocPerm into Custom
DocPerm *before* the first custom row lands. This module does the same thing, in
`_mirror_standard_perms`, and there is a test that removing it breaks.

TWO RULES FOLLOW, AND THE INSTALLER ENFORCES BOTH IN CODE:

  1. **Permissions are written ONLY onto doctypes this app owns.** A target
     whose `module` is not "ERPNext MCP" is refused and reported, not written.
     Not because writing it would fail — it would succeed, which is the
     problem. `Employee`, `Company`, `File` and `ToDo` belong to other apps and
     this app does not get to redefine who may read them.

  2. **The standard perms are mirrored first, per doctype, before any row for
     one of these roles is added.** So the set of permissions in force after
     the installer runs is the set that was in force before it, PLUS this
     app's own roles — which is the same promise the rest of this app makes.

Rule 1 has a consequence worth stating plainly rather than hiding: a Field
Worker who needs to read their own Employee record needs a role from the app
that owns Employee. `create_mobile_user` therefore assigns the site's own
`Employee` role alongside `Field Worker` where the site has one, and says so in
its result. Granting it here would have been one line and would have taken the
HR module's permissions down with it.

────────────────────────────────────────────────────────────────────────────

NOTHING HERE RAISES. `install_roles` runs inside `bench migrate`, where an
exception aborts the migration for the whole bench. Every failure lands in
`report["failed"]` with a reason, and `install.py` PRINTS them — which is the
v0.16.1 lesson: a builder that cannot raise and is never read cannot report
anything at all.

IT IS IDEMPOTENT AND IT NEVER TAKES ANYTHING AWAY. A role that exists is left
alone. A permission row that exists is left alone, *including* one an operator
has since edited — if somebody decided their Foremen should not create Farm
Tasks, the next migrate does not argue with them. The installer only ever adds
what is missing, which is the only behaviour that is safe to run on every
migrate forever.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

import frappe

from .compat import doctype_exists

ROLE_DOCTYPE = "Role"
DOCPERM = "DocPerm"
CUSTOM_DOCPERM = "Custom DocPerm"
USER_PERMISSION = "User Permission"

#: The module every doctype this app ships declares. A permission target outside
#: it is refused — see the trap discussion above.
OWNED_MODULE = "ERPNext MCP"

#: Frappe's DocPerm flag columns, and what this app sets them to when it is not
#: told otherwise. `report` and `export` ride with `read` because a role that can
#: read a register and cannot run a report over it is a role that will be asked
#: to email screenshots.
_FLAG_DEFAULTS = {
	"read": 0,
	"write": 0,
	"create": 0,
	"delete": 0,
	"submit": 0,
	"cancel": 0,
	"amend": 0,
	"report": 0,
	"export": 0,
	"print": 0,
	"email": 0,
	"share": 0,
	"if_owner": 0,
	"permlevel": 0,
}


def _perm(read=0, write=0, create=0, delete=0, if_owner=0) -> dict:
	"""One permission row's flags, with the reading conveniences filled in.

	`delete` is here and is used exactly nowhere in the specs below. It is
	kept as a parameter so that a future role which genuinely needs it has to
	pass it by name in a diff somebody reviews, rather than discovering that the
	helper cannot express it and reaching for a raw dict.
	"""
	flags = dict(_FLAG_DEFAULTS)
	flags.update(
		{
			"read": int(bool(read)),
			"write": int(bool(write)),
			"create": int(bool(create)),
			"delete": int(bool(delete)),
			"if_owner": int(bool(if_owner)),
			"report": int(bool(read)),
			"export": int(bool(read)),
			"print": int(bool(read)),
			"email": int(bool(read)),
			"share": int(bool(write)),
		}
	)
	return flags


READ = _perm(read=1)
READ_WRITE = _perm(read=1, write=1)
FULL = _perm(read=1, write=1, create=1)


@dataclass(frozen=True)
class RoleSpec:
	"""One role: its name, what it is for, and what it may touch.

	`desk_access` is 0 for the two roles that only ever hold a phone. A Frappe
	role with desk access lets its holder into `/app`, which for a field worker
	is a large, confusing surface with a search bar that reaches further than
	their permissions do (it finds nothing they may not read, but it *looks*
	like it might). The mobile app talks to the API; it does not need the Desk,
	and a role that cannot open the Desk is one fewer thing to explain.

	`companion_roles` are roles belonging to OTHER apps that this one cannot
	grant safely — see rule 1 in the module docstring. `create_mobile_user`
	assigns any of them the site actually has, and reports the ones it does not.
	"""

	name: str
	description: str
	permissions: tuple = ()
	desk_access: int = 1
	companion_roles: tuple = ()
	#: What this role is for, in the words somebody staffing it would use. Shown
	#: by `list_mobile_users` and `get_current_user_context` so the mobile app can
	#: put a sentence on the account screen instead of a bare role name.
	summary: str = ""
	cannot: tuple = field(default_factory=tuple)


#: ── the registers, grouped so the specs below read as sentences ─────────────
#:
#: Named tuples of doctype names rather than repeated literals, because the
#: interesting part of each role is which GROUPS it holds, and a reader comparing
#: Foreman with Farm Manager should be able to see the difference in one line.
DISPATCH = ("Farm Task", "Farm Task Assignment")
FIELD_RECORDS = ("Housing Inspection", "Detector Test", "Water Test")
#: NOT `Certification Renewal` or `Audit Corrective Action`. Both are CHILD
#: TABLES, and in Frappe a child row's access follows its parent's — a DocPerm on
#: one is read by nothing. Worse, writing a Custom DocPerm on any doctype turns
#: off its standard set (see the trap discussion above), so a permission that
#: does nothing is not merely useless: it is a way to break something while
#: appearing to grant something. `_ensure_permission` refuses one, and there is a
#: test that no group here contains a child table.
COMPLIANCE_REGISTERS = (
	"Compliance Policy",
	"Certification",
	"Regulatory Filing",
	"Audit Event",
	# v0.19.0. A training record is compliance evidence in the same sense a
	# certificate is — it is what a GAP auditor, an OR-OSHA compliance officer and
	# an FDA inspector each ask to see — so it belongs in the group rather than in
	# a permission of its own. A Foreman who can read the certificate register and
	# not the training register cannot answer the question an inspector asks about
	# their own crew.
	"Employee Training Record",
)
#: v0.19.3. The shift is where the crew was and the heat record is what the
#: operation says it did about the conditions — a group of its own rather than a
#: line in COMPLIANCE_REGISTERS, because the two halves belong to different
#: people. FORMING A SHIFT IS OPERATIONAL and it is the Foreman's; a heat record
#: is compliance evidence and a Compliance Officer reads it. Folding them into
#: the register group would have given a Compliance Officer the power to form a
#: crew, which is the same separation `_grant(READ, DISPATCH)` on that role
#: already exists to keep: the person who decides a walk is required and the
#: person who decides who walks it must not be the same account.
SHIFTS = ("Farm Shift", "Heat Exposure Event")
CALENDAR = ("Compliance Alert",)
#: v0.22.0. THE RULE DEFINITIONS, AND THEY ARE A GROUP OF THEIR OWN BECAUSE THEY
#: ARE A DIFFERENT KIND OF THING FROM THE CALENDAR. A Compliance Alert is an
#: observation about this operation on this day; a Compliance Rule is the
#: DEFINITION that produced it, and editing one changes what the whole site
#: watches from tonight onwards. So it is granted to the two roles whose job is
#: the compliance framework — Compliance Officer and Farm Manager — and to
#: nobody else. A Foreman reads the calendar and cannot rewrite what fills it; a
#: Field Worker cannot see it at all.
#:
#: That is the same separation `_grant(READ, DISPATCH)` on the Compliance Officer
#: already exists to keep, pointed one layer up: the person who decides a walk is
#: required and the person who decides who walks it must not be the same account,
#: and the person who can silently redefine "required" must be fewer people
#: still.
#:
#: v0.38.0 ADDS `Regulation Feed` TO THIS GROUP RATHER THAN TO THE REGISTERS,
#: and the reason is the same one that put Compliance Rule here. A feed is not an
#: observation about this operation; it is a pointer at the TEXT a rule was
#: written from, and editing one changes what the site checks and what a change
#: log will say about which rules to re-read. The two roles whose job is the
#: compliance framework hold it, and nobody else — a Foreman reads the calendar,
#: and the register of where the rules came from is one layer up from that.
RULE_DEFINITIONS = ("Compliance Rule", "Regulation Feed")
GROUND = ("Parcel", "Field", "Irrigation Zone")
CAMP = ("Housing Unit", "Housing Assignment")
HOLDING = ("Cap Table Entry", "Member Event", "Note Payable", "Asset Cost Profile")
PAPER = ("Governance Document", "Related Party")
PEOPLE = ("Family",)
PROPERTY = ("Lease",)
#: v0.59.3. THE FEDERAL HIRING FORM, AND IT IS A GROUP OF ONE ON PURPOSE.
#:
#: Form I-9 IS SIGNED TWICE BY TWO DIFFERENT PEOPLE. The worker attests in
#: Section 1; within three business days of the first day of work the EMPLOYER
#: attests in Section 2 that it examined the documents that worker presented.
#: 8 CFR § 274a.2(b)(1)(ii) puts that second attestation on the employer or its
#: authorised representative, and on an operation this size that is the person
#: standing in the packing shed with the phone — the Farm Manager. A role that
#: supervises hiring and cannot WRITE the form cannot do the half of the form
#: that is legally its own.
#:
#: THE GAP SHOWED UP AS A SENTENCE ABOUT A COLUMN RATHER THAN A MISSING BUTTON,
#: which is worth recording because the design that produced it is the right
#: one: `tools/signatures._require_write` gates every signature on Frappe's own
#: `has_permission(..., "write", doc=...)` instead of on a role list of its own,
#: so a Farm Manager signing Section 2 from the iOS app was refused with "this
#: account may not write I-9 Form … so it may not put a signature on it". The
#: check was correct and the permission table was incomplete.
#:
#: READ AND WRITE. NOT `create`, AND NOT `delete`.
#:
#:   * An I-9 BEGINS with the worker's Section 1 — the hiring path raises it as
#:     the MCP system user — and a manager who could raise one could raise one
#:     for somebody who was never hired, which is the shape a fabricated
#:     verification takes.
#:   * DESTROYING ONE IS THE RETENTION SCHEDULE'S DECISION AND NOBODY ELSE'S.
#:     § 274a.2(b)(2) keeps the form three years from hire or one year from
#:     separation, whichever is later, and an I-9 that disappears before then is
#:     precisely the failure the register exists to prevent. `delete` stays with
#:     System Manager, where the doctype's own standard permissions put it.
#:
#: THE AUTHORIZED-SIGNER ROSTER IS STILL THE SECOND GATE AND THIS DOES NOT TOUCH
#: IT. Write permission decides whether an account may edit the record;
#: `tools/signers.py` decides whose name may appear as the employer's authorised
#: representative, and Section 2 and Supplement B are both gated on it *after*
#: this check passes. Two different questions, two different registers, and
#: collapsing them would mean the list of people who may attest for the employer
#: was maintained by whoever last edited a role.
#:
#: WHOSE I-9 REMAINS FRAPPE'S QUESTION, NOT THIS FILE'S — see the module
#: docstring on the split, and `permissions.py` on the clause doing the work.
#: `I-9 Form.company` is a REQUIRED Link to Company, so a Farm Manager's Company
#: User Permission scopes every list, every read and — because the signature
#: check is made WITH the document — every signature to the entities that
#: manager actually manages. This grant says what kind of work the role does;
#: the User Permission `create_mobile_user` writes says whose. A Farm Manager
#: scoped to one entity gets no reach into another's personnel file out of it.
HIRING_FORMS = ("I-9 Form",)
#: v0.63.0. THE OTHER FEDERAL FORM THE HIRING WIZARD COLLECTS, AND THE SAME
#: FINDING v0.59.3 MADE ABOUT THE FIRST ONE.
#:
#: `HIRING_FORMS` above records how the I-9 gap surfaced: `signatures._require_write`
#: gates every signature on Frappe's own `has_permission(..., "write", doc=...)`
#: rather than on a role list of its own, so a Farm Manager signing Section 2 from
#: the app was refused with "this account may not write I-9 Form" — the check was
#: correct and the permission table was incomplete. Form W-4 is in exactly that
#: state today and the same three calls are closed by it: the signature box in
#: Step 5, `seal_signed_document`, and `get_document_preview`, which is the read a
#: pad has to make BEFORE anybody signs anything.
#:
#: THE GRANT IS NOT A WIDENING, WHICH IS THE PART WORTH CHECKING. `w4.submit_w4`
#: inserts with `flags.ignore_permissions = True`, and Farm Manager has therefore
#: been creating and editing W-4 records through this app since the wizard
#: shipped, on every site, with no DocPerm saying so. What the table said and what
#: the role did were already out of step; this puts them back in step rather than
#: granting something new.
#:
#: CORRECTION, v0.94.0: THIS PARAGRAPH USED TO SAY `submit_w4` RUNS "BEHIND
#: `employee.require_hr_role()`" AND THAT WAS NEVER TRUE. It ran behind no role
#: gate at all — enrolment was the whole check — so the sentence described a
#: protection the code did not have, on the one surface where being wrong about
#: that matters most. It now runs behind `employee.require_hiring_role()`
#: (`HIRING_ROLES` = `HR_ROLES` + Foreman + Crew Leader), which is a RESTRICTION
#: against what shipped and a widening against putting it behind `HR_ROLES`.
#: Whose name goes in the Employers Only block is a separate question answered by
#: the authorized-signer roster, not by any role on that list.
#:
#: READ AND WRITE. NOT `create`, AND NOT `delete`, for the reasons `HIRING_FORMS`
#: gives: a withholding certificate begins with the worker's own elections, and a
#: W-4 that disappears is a withholding basis nobody can reconstruct at year end.
#:
#: THE SIGNER IS THE WORKER, NOT THE MANAGER, and nothing here changes that. The
#: W-4's box carries no `form_type`, so the authorized-signer roster does not gate
#: it — and `signatures._identity` refuses a badge that resolves to somebody other
#: than the form's own employee on precisely this box. Write permission decides
#: whose account may hold the pad; the identity check decides whose hand may sign.
#:
#: `Tax Form` IS DELIBERATELY NOT HERE. A 941 or an OR-WR is signed by an OFFICER
#: of the employer under penalties of perjury, this app keeps no register of
#: officers, and a Farm Manager is not one by virtue of running the orchard. That
#: box stays gated by whatever write permission the site's own rules give.
WITHHOLDING_FORMS = ("W-4 Form",)
#: v0.61.0. THE TWO COMPANY-WIDE RATE TABLES, AND THEY ARE A GROUP OF THEIR OWN
#: BECAUSE OF WHAT READING THEM MEANS.
#:
#: A Piecework Rate and a Position Wage Default are not somebody's pay — they are
#: what the OPERATION pays for a bucket and for an hour of a job title. No name
#: appears on either row. That is what makes them safe to put in front of a Farm
#: Manager, who cannot read `Farm Salary Structure` and still cannot: the
#: register of what each individual earns belongs to the HR module that owns
#: `Employee`, and this release does not go round it. What a manager gets is the
#: PRICE LIST, which is the thing they are actually asked about in a packing shed
#: at six in the morning.
#:
#: READ AND WRITE FOR THE FARM MANAGER. NOT `create`.
#:
#: The distinction looks pedantic until you notice what `create` would mean here.
#: `select_effective` gives the row with the latest `effective_from` that covers a
#: date, so ADDING A ROW IS HOW A RAISE HAPPENS — one insert changes what the
#: whole company's next payroll pays, for everybody on that activity, with no
#: second party to it. Editing an existing row is bounded by contrast: the rate
#: was already set by somebody who could set rates, and a manager fixing a typo
#: in it is fixing this season's number, not opening next season's. So the person
#: who runs the operation may correct the table and the person who sets what the
#: operation pays — System Manager or HR Manager, through the doctype's own
#: standard permissions — is who adds to it.
#:
#: READ ONLY FOR THE COMPLIANCE OFFICER, and it is a read they need rather than a
#: courtesy. The piece rate is the input to the minimum wage makeup on every slip,
#: and "was the rate high enough that these hours cleared the floor" is a
#: compliance question — ORS 653.025 and RCW 49.46.020 — that cannot be answered
#: from the slip alone. They may not touch either table, which is the same
#: separation this file keeps everywhere: the officer who checks whether a rate
#: was lawful must not be the account that set it.
WAGE_TABLES = ("Piecework Rate", "Position Wage Default")
#: v0.60.0. THE RECORD OF WHO SIGNED WHAT, AND HOW ANYBODY KNOWS.
#:
#: A group of one, and READ-ONLY TO EVERY ROLE IN THIS FILE — including the two
#: that hold it. That is not caution about a new doctype; it is the doctype's
#: entire point. A Signing Evidence row is append-only, `in_create` keeps the
#: Desk's New button off it, and its controller refuses every write after the
#: insert, so the only thing that makes one is the signature path itself. A role
#: granted `write` here could not use it — but the grant would say, in the one
#: register whose value is that it cannot be edited, that somebody is expected to
#: edit it.
#:
#: WHY THESE TWO AND NOT THE OTHER FOUR:
#:
#:   * A COMPLIANCE OFFICER is the person the auditor actually talks to. This is
#:     the register that answers "how do you know it was him", and a compliance
#:     role that keeps the certificate register and cannot open the signature
#:     evidence for the forms in it is a role that has to ask somebody else for
#:     the second half of every answer.
#:   * A FARM MANAGER signs Section 2 themselves — see `HIRING_FORMS` — and
#:     reading back what was recorded about their own attestation is the same
#:     access they already have to the form it is about.
#:
#:   * A FOREMAN does not get it, and the reason is the separation this file
#:     keeps everywhere else: a foreman runs the board, and the evidence trail
#:     over signatures collected on that board is checked by somebody who is not
#:     running it.
#:   * A FIELD WORKER does not get it. The register carries badge IDs, device
#:     identifiers and coordinates for every signature on the operation, which is
#:     a movement record for the whole crew — it is the Housing Assignment
#:     argument in `permissions.py` pointed at a different table.
#:   * FAMILY MEMBER AND ADVISOR do not get it. Whose I-9 was signed where is the
#:     operating company's personnel file, not the holding company's business.
#:
#: `Signing Evidence.company` is a REQUIRED Link to Company, so whose evidence a
#: reader may see is Frappe's question and not this file's — the same split the
#: module docstring opens with, and the reason this app writes no query condition
#: of its own for it.
SIGNING_EVIDENCE = ("Signing Evidence",)

#: v0.68.1. THE BAND A CHECKER IS ASKED TO ENFORCE, and it is a POLICY NUMBER
#: rather than anybody's pay — which is the whole reason it is a group of its own
#: and not part of the one below it.
#:
#: `Container Fill Threshold` is the percentage range a bucket has to be filled
#: to before it counts, `Fill Threshold Change Log` is every time somebody moved
#: it and why. A checker standing at the bin enforces that number all day and
#: could not read it: `update_fill_threshold` gates on Foreman-or-above, which is
#: right, and nothing granted the person applying it so much as a read. So a
#: handset showed a band it had no permission to fetch, and the acknowledgment
#: loop — `list_pending_threshold_acknowledgments`, which finds checkers by
#: DESIGNATION — asked people to confirm a number they were not allowed to see.
#:
#: READ FOR EVERYBODY IN THE FIELD, READ_WRITE FOR FOREMAN AND ABOVE. That is the
#: same split `fill_pipeline.FOREMAN_ROLES` already enforces one layer up, said
#: in Frappe's own permissions so the two cannot drift.
FILL_STANDARDS = ("Container Fill Threshold", "Fill Threshold Change Log")

#: v0.68.1. What a crew actually turned in: the session, and the buckets on it.
#:
#: NOT GRANTED TO A FIELD WORKER, and that is deliberate in the direction that
#: looks unhelpful. A Bucket Log Entry is a piece-rate count, which is to say it
#: is somebody's pay — and a User Permission scopes by COMPANY, not by employee,
#: so a picker granted read here could read the whole crew's day. `permissions.py`
#: makes the same argument about Housing Assignment. A worker's own count comes
#: back through their own session on the mobile surface, where the caller is
#: resolved to an Employee first.
#:
#: The three roles that DO get it are the three that run the crew, and read-only:
#: the rows are written by `sync_bucket_entries` running as the MCP System User,
#: so a write grant would describe an editing path nobody takes.
HARVEST_LOG = ("Bucket Log Session", "Bucket Log Entry")


def _grant(perm: dict, *groups) -> tuple:
	"""(doctype, flags) pairs — one permission applied across several groups."""
	out = []
	for group in groups:
		for doctype in group:
			out.append((doctype, perm))
	return tuple(out)


ROLE_SPECS = (
	RoleSpec(
		name="Field Worker",
		description=(
			"Farm Ops mobile: the pool, the jobs they are holding, and the evidence they "
			"file against them. Scoped to one company by User Permission."
		),
		summary=(
			"You see work you can take and work you are holding, and you file the "
			"photographs and findings that close it."
		),
		desk_access=0,
		companion_roles=("Employee",),
		permissions=(
			# READ on the task, WRITE on the assignment. The distinction is the
			# point: a worker moves their own record through its states and files
			# what they found; they do not get to rewrite the job — its urgency,
			# its evidence contract, or which compliance record it produces.
			("Farm Task", READ),
			("Farm Task Assignment", FULL),
			# Read on what their completion produced, so the app can show somebody
			# the inspection they just filed. The record itself is written by
			# `complete_farm_task` running as the MCP System User, which is why
			# create is not here.
			*_grant(READ, FIELD_RECORDS),
			# Where the job is. A task at MC-Cabin-01 is not a task until the app
			# can say which cabin that is.
			*_grant(READ, CAMP, GROUND),
			# v0.19.3. READ on the shift they are standing in, so the app can show
			# a worker which crew they are on and what breaks have been called. NOT
			# write: the foreman forms the crew and logs the events, which is the
			# whole argument for a foreman-driven shift — a worker who could edit
			# the crew list could edit their own hours.
			*_grant(READ, SHIFTS),
			# v0.68.1. THE BAND, FOR THE CHECKER. A "Checker" is a DESIGNATION and
			# not a role — see `JOB_TITLES` below for why — so the person doing that
			# job holds this role, and this is the one thing the job needs that the
			# role did not carry. Read only: `update_fill_threshold` is Foreman and
			# above, and a checker who could move the number they are being asked to
			# trust is the exact shape that gate exists to prevent.
			*_grant(READ, FILL_STANDARDS),
		),
		cannot=(
			"read the SOP library (Compliance Policy) — a task's instructions belong in its notes",
			"read the compliance calendar",
			"raise, assign or cancel work",
			"move the container fill threshold — a checker enforces the band, a Foreman or above sets it",
			"read the bucket log — a bucket count is somebody's piece-rate pay, and a User "
			"Permission scopes by company rather than by person, so a read here would be a "
			"read of the whole crew's day",
			"see any company but the one their User Permission names",
		),
	),
	RoleSpec(
		name="Foreman",
		description=(
			"The dispatch board for one operating company: raise work, send people to it, "
			"read the compliance calendar that generated it."
		),
		summary="You run the board: what needs doing, who is doing it, and what came back.",
		companion_roles=("Employee",),
		permissions=(
			# v0.19.3. THE FOREMAN OWNS THE SHIFT, and this is the one register
			# where that role has more than the Compliance Officer. -1131 puts the
			# water, shade, rest-cycle and observation obligations on the named
			# supervisor, so the account that forms the crew and logs what it did
			# has to be theirs — anybody else's would be a record about a shift
			# they were not standing on.
			*_grant(FULL, DISPATCH, FIELD_RECORDS, SHIFTS),
			*_grant(READ_WRITE, CALENDAR),
			# v0.68.1. The fill band is READ_WRITE here and read-only below it,
			# which is `fill_pipeline.FOREMAN_ROLES` said in Frappe's own
			# permissions: a foreman may move the number, a checker may only read
			# the one they are enforcing. The bucket log itself is read — the rows
			# are written by the sync running as the MCP System User.
			*_grant(READ_WRITE, FILL_STANDARDS),
			*_grant(READ, COMPLIANCE_REGISTERS, CAMP, GROUND, HARVEST_LOG),
		),
		cannot=(
			"touch accounting — no account, journal entry or ledger permission is granted "
			"to this role anywhere",
			"edit the certificate or SOP registers (read only)",
			"see the cap table, member events or governance archive",
		),
	),
	# ── v0.68.1: the seventh, and the one this app already half had ─────────
	#
	# "Crew Leader" WAS ALREADY A ROLE NAME IN THIS APP AND WAS NOT A ROLE.
	# `employee.SHIFT_ROLES` is `HR_ROLES` plus Foreman and Crew Leader, and it
	# has been since v0.19.3 — the iOS `ShiftToolsToolbar` offers Crew Clock to a
	# Crew Leader, and the shift tools accept one. What `roles.py` never did was
	# CREATE the role or grant it anything, so:
	#
	#   * a site that wanted one had to build it by hand in the Desk, and
	#   * `create_mobile_user` refused to enrol one at all — `_role_spec` rejects
	#     anything outside this tuple by name.
	#
	# So the app named a role in two gates and in a shipped iOS build, and had no
	# way to make one. That is the gap this closes, and it is why Crew Leader is a
	# role while Checker and Tractor Driver are not: see `JOB_TITLES` below.
	#
	# IT IS NOT A SECOND FOREMAN. The crew lead runs the SHIFT — who is clocked
	# on, the breaks called, the heat record — because OAR 437-004-1131 puts the
	# water, shade, rest-cycle and observation obligations on the supervisor who
	# was standing on the block, and on a site where the crew lead is not the
	# foreman that is this person. They do not run the BOARD: raising work,
	# sending people to it and cancelling it stay the Foreman's, which is the same
	# separation this file keeps between the Compliance Officer and the dispatch
	# register one role up.
	RoleSpec(
		name="Crew Leader",
		description=(
			"The crew on the block for one operating company: the shift, who is on it, the "
			"breaks called, and what the day turned in. Not the dispatch board."
		),
		summary=(
			"You run the crew in front of you: who is clocked on, the breaks you called, and "
			"the buckets they turned in."
		),
		# A phone role. Same reasoning as the Field Worker: the crew lead is
		# standing on a block, and `/app` is a surface with a search bar that
		# reaches further than it looks like it should.
		desk_access=0,
		companion_roles=("Employee",),
		permissions=(
			# THE SHIFT IS THEIRS, in full, and it is the only register where that
			# is true. `end_shift` writes one submitted Attendance per crew member
			# from their own joined_at/left_at, so a crew lead who cannot close is
			# a crew with no wage record for the day — which is the same argument
			# `employee.SHIFT_ROLES` already makes one layer up.
			*_grant(FULL, SHIFTS),
			# The board is READ. They see the work their crew is holding and they
			# do not raise, assign or cancel it.
			*_grant(READ, DISPATCH, CAMP, GROUND, FILL_STANDARDS, HARVEST_LOG),
		),
		cannot=(
			"raise, assign or cancel work — the board is the Foreman's, and a crew lead who "
			"could dispatch could send their own crew to their own work",
			"move the container fill threshold — read-only here, the same as it is for the "
			"checker applying it",
			"read the personnel register, or edit an Employee who already exists — "
			"`search_employees` and `update_employee` keep `employee.HR_ROLES`, which is why "
			"that list and `employee.SHIFT_ROLES` are still two lists",
			"make the employer's I-9 Section 2 attestation unless the farm has named them "
			"on the authorized-signer roster — that is a designation on a PERSON under "
			"8 U.S.C. §1324a and no role substitutes for it",
			"read the compliance calendar, the SOP library or the certificate register",
			"touch accounting",
		),
	),
	RoleSpec(
		name="Compliance Officer",
		description=(
			"The compliance framework end to end: policies, certificates, filings, audits "
			"and the alert calendar, for the companies their User Permissions name."
		),
		summary=("You keep the registers an audit asks for, and you can build the packet that answers it."),
		permissions=(
			*_grant(FULL, COMPLIANCE_REGISTERS, FIELD_RECORDS, RULE_DEFINITIONS),
			*_grant(READ_WRITE, CALENDAR),
			# READ, deliberately. See the module docstring: the person who decides
			# a walk is required and the person who decides who walks it must not
			# be the same account.
			*_grant(READ, DISPATCH, CAMP, GROUND, PAPER, SHIFTS, SIGNING_EVIDENCE, WAGE_TABLES),
		),
		cannot=(
			"dispatch anybody — Farm Task is read-only for this role, on purpose",
			"set or correct a wage rate — the two company-wide tables are read-only here, "
			"because the officer who checks whether a rate cleared the minimum wage floor "
			"must not be the account that set it",
			"alter a signature evidence row — the register is read-only to every role in "
			"this app, because a chain of custody that can be edited is not one",
			"form a crew shift or sign one off — the shift register is read-only here, "
			"because OAR 437-004-1131 puts the obligations on the supervisor who was "
			"standing on the block",
			"edit parcels, leases or housing units",
			"touch accounting",
		),
	),
	RoleSpec(
		name="Farm Manager",
		description=(
			"Operations and the ground under them: dispatch, compliance, parcels, fields, "
			"zones, housing and leases for their assigned companies."
		),
		summary="You run the operation: the work, the compliance behind it, and the ground it happens on.",
		# v0.62.0. `HR User` JOINS `Employee`, AND IT IS NAMED HERE RATHER THAN
		# GRANTED BELOW BECAUSE RULE 1 IS NOT NEGOTIABLE — see the module
		# docstring. Both roles belong to Frappe HR, which owns the `Employee`
		# doctype, and a Custom DocPerm written on Employee by this app would make
		# Frappe ignore EVERY standard permission that doctype has, for every role
		# on the site, silently, during `bench migrate`.
		#
		# WHAT MADE IT NECESSARY: the seven routes v0.62.0 published to close the
		# handset's 404s include two Employee writes and the attachment reads for
		# an Employee's folder. This app's own gate on those is
		# `employee.HR_ROLES`, which lists Farm Manager and passes — but
		# `tools/files.py` is the one family of tools in this app that consults
		# FRAPPE's permissions, deliberately, because `is_private` is a promise
		# the framework makes about who may see a passport scan. That check asks
		# whether the account may read the Employee, and a Farm Manager holding
		# only this app's roles and the site's own `Employee` role may read its
		# OWN record and no one else's. So the manager running a hire could file
		# a licence photograph and could not read the folder back.
		#
		# `HR User` IS THE NARROWER OF FRAPPE HR'S TWO and is the role a real HR
		# clerk holds; `HR Manager` additionally administers the module's own
		# masters and setup. `employee.HR_ROLES` has accepted both since v0.18.1,
		# which is the same judgement made one layer up.
		#
		# NOTHING IS ASSIGNED SILENTLY AND NOTHING BREAKS WITHOUT IT.
		# `create_mobile_user` assigns a companion role only where the site
		# actually has it and REPORTS the ones it does not, so a bench without
		# hrms enrols a Farm Manager exactly as it did before and says why the
		# folder reads will refuse.
		companion_roles=("Employee", "HR User"),
		permissions=(
			*_grant(
				FULL,
				DISPATCH,
				FIELD_RECORDS,
				COMPLIANCE_REGISTERS,
				GROUND,
				CAMP,
				PROPERTY,
				SHIFTS,
				RULE_DEFINITIONS,
			),
			*_grant(READ_WRITE, CALENDAR),
			# v0.59.3. Section 2 is the employer's half of the I-9 and this is the
			# role that stands in front of the worker to do it. READ_WRITE, not
			# FULL: the form is raised by the hiring path and destroyed by the
			# retention schedule, neither of which is a manager's call.
			*_grant(READ_WRITE, HIRING_FORMS, WITHHOLDING_FORMS),
			# v0.61.0. The PRICE LIST, which is what a manager is actually asked
			# about in a packing shed at six in the morning — no name appears on
			# either row, which is what makes it safe to hand to a role that
			# cannot read Farm Salary Structure and still cannot. READ_WRITE, not
			# FULL: adding a row is how a raise happens, and one insert changes
			# what the whole company's next payroll pays. See WAGE_TABLES.
			*_grant(READ_WRITE, WAGE_TABLES),
			*_grant(READ, PAPER, SIGNING_EVIDENCE),
			# v0.68.1. `FILL_STANDARDS` AND `HARVEST_LOG` ARE DELIBERATELY NOT
			# LISTED HERE, and the omission is the accurate thing rather than the
			# lazy one. All four of those doctypes SHIP a standard DocPerm giving
			# Farm Manager read, write and create — so the installer's mirror
			# copies it and `_ensure_permission` finds the row already there and
			# leaves it. A grant written here would be a silent no-op AND a lie:
			# `describe_role` reads this tuple, so the catalogue would advertise
			# read-only on a register this role can already write. What a role
			# actually holds and what this file says it holds have to be the same
			# sentence. The Foreman, the Crew Leader and the Field Worker are all
			# granted above because none of them is on those doctypes at all.
		),
		cannot=(
			"see the cap table or member events — the operating side does not read the "
			"holding company's equity",
			"edit the governance archive (read only)",
			"touch notes payable or asset cost profiles",
			"raise or destroy an I-9 — Section 2 is theirs to complete and sign, but the "
			"form begins with the worker's Section 1 and its life is the retention "
			"schedule's, not a manager's",
			"ADD a piecework rate or a position wage default — they may correct what is "
			"in the tables, but adding a row is how a raise happens, and one insert "
			"changes what the whole company's next payroll pays",
		),
	),
	RoleSpec(
		name="Family Member",
		description=(
			"The holding-company view: cap table, member events, governance, related "
			"parties, land and debt for the entities their User Permissions name."
		),
		summary="You see what the family owns, who owns it, and the paper that says so.",
		permissions=(
			*_grant(READ_WRITE, PAPER),
			*_grant(READ, HOLDING, PEOPLE, GROUND, PROPERTY, CAMP),
		),
		cannot=(
			"see the operating company's task board — the day-to-day of whoever farms "
			"the ground is not the holding company's business",
			"edit the cap table or post a member event",
			"edit parcels, leases or housing",
		),
	),
	RoleSpec(
		name="Advisor",
		description=(
			"The narrowest role in the app. Governance documents, related parties and "
			"regulatory filings, read-only, for the one entity they advise."
		),
		summary="You read the documents for the entity you advise, and nothing else.",
		permissions=(
			*_grant(READ, PAPER),
			("Regulatory Filing", READ),
		),
		cannot=(
			"write anything, anywhere",
			"see the cap table, the ledger, the task board or the compliance calendar",
			"see any entity but the ones their User Permissions name — which for an "
			"advisor is usually exactly one",
		),
	),
)

ROLE_NAMES = tuple(spec.name for spec in ROLE_SPECS)

#: Name → spec, for every tool that takes a role as an argument.
BY_NAME = {spec.name: spec for spec in ROLE_SPECS}


# ── v0.68.1: the job titles, and which role carries each ────────────────────
#
# THIS TABLE EXISTS BECAUSE THE QUESTION KEEPS BEING ASKED IN THE WRONG SHAPE.
# "We need a Checker role" and "we need a Tractor Driver role" both sound like
# requests for a seventh and eighth entry above, and neither is — because a
# Frappe role answers a different question from a job title:
#
#   A ROLE SAYS WHAT KIND OF RECORD SOMEBODY MAY TOUCH. It is the thing a
#   Custom DocPerm hangs off, it is coarse on purpose, and adding one is a
#   permanent widening of this app's permission surface.
#
#   A JOB TITLE SAYS WHAT SOMEBODY DOES ALL DAY. It is `Employee.designation`,
#   it is a master an operator adds to in ten seconds, and this app ALREADY
#   reads it: `list_pending_threshold_acknowledgments` finds every checker on
#   the site by filtering Active Employees on `designation == "Checker"`, and
#   `Position Wage Default` keys a wage rate on the same column.
#
# So the test for "should this be a role" is not "is it a distinct job" — it is
# "does it touch a DIFFERENT SET OF RECORDS". Applying that:
#
#   Checker          Reads the fill band, checks buckets into a session that the
#                    sync writes. Same records as any Field Worker plus the band
#                    — which is why v0.68.1 granted Field Worker `FILL_STANDARDS`
#                    rather than inventing a role for one read.
#   Tractor Driver   Completes tasks and files evidence, exactly as any other
#                    Field Worker does. A tractor is an Asset Register row that
#                    `resolve_asset_tag` scans and the task names; nothing about
#                    driving one touches a register a picker does not.
#   Crew Leader      Runs the SHIFT — forms the crew, calls the breaks, closes
#                    it and writes the Attendance rows. A register no Field
#                    Worker may write and no Foreman-less site had an account
#                    for. That is a different set of records, so it IS a role,
#                    and it is the one this release added.
#
# WHAT THIS TABLE IS FOR. It is the answer to "we hired a checker, what do we
# give them", returned by `list_mobile_users` beside the role catalogue so the
# mapping is machine-readable rather than a paragraph in a release note. Each
# entry names the DESIGNATION to put on the Employee and the ROLE to pass to
# `create_mobile_user` — two different fields, set by two different tools, and
# the pair is the whole configuration.
#
# THE DESIGNATIONS ARE SEEDED BY `install._farm_designations`, create-only, so
# `Checker` resolves on a stock Frappe HR install rather than being a title the
# app filters on and nothing ever creates.
JOB_TITLES = (
	{
		"designation": "Picker",
		"mobile_role": "Field Worker",
		"why": (
			"The job the Field Worker role was written for: take work from the pool, file the "
			"evidence that closes it, see the shift you are standing on."
		),
	},
	{
		"designation": "Checker",
		"mobile_role": "Field Worker",
		"why": (
			"A checker is a Field Worker who also reads the container fill threshold — the band "
			"they enforce at the bin all day. v0.68.1 granted that read to the role rather than "
			"making a role of the title. They may NOT move the band: `update_fill_threshold` is "
			"Foreman and above, because a checker who could move the number they are asked to "
			"trust is the shape that gate exists to prevent. `list_pending_threshold_"
			"acknowledgments` finds them by this designation."
		),
	},
	{
		"designation": "Tractor Driver",
		"mobile_role": "Field Worker",
		"why": (
			"Nothing about driving a tractor touches a register a picker does not. The machine is "
			"an Asset Register row the task names and `resolve_asset_tag` scans; the work is a "
			"Farm Task with an evidence contract. Where a job genuinely needs a named licence "
			"holder, that is `skill_required` on the task and dispatch_mode=Dispatched — a "
			"property of the WORK, which is where it belongs, rather than of the account."
		),
	},
	{
		"designation": "Crew Leader",
		"mobile_role": "Crew Leader",
		"why": (
			"The one of the three that IS a role, because it is the one that writes a register "
			"nobody else in the field may: the Farm Shift. Forming the crew, calling the breaks "
			"and closing the day writes one submitted Attendance per crew member. On a site "
			"where the crew lead IS the foreman, give them Foreman instead — this role is the "
			"board-less half of it."
		),
	},
	{
		"designation": "Foreman",
		"mobile_role": "Foreman",
		"why": (
			"The dispatch board as well as the shift: raise work, send people to it, read the "
			"compliance calendar that generated it."
		),
	},
)

#: Designation → the mobile role that carries it, for a caller that wants the
#: lookup rather than the table.
ROLE_FOR_JOB_TITLE = {entry["designation"]: entry["mobile_role"] for entry in JOB_TITLES}


def job_titles() -> list:
	"""The job-title → mobile-role mapping, with what each designation resolves to.

	`designation_exists` is asked of the site rather than assumed, because the
	mapping is only actionable if the master is there — a site whose `Checker`
	designation was renamed should see that here rather than discover it when
	`list_pending_threshold_acknowledgments` returns nobody.
	"""
	out = []
	for entry in JOB_TITLES:
		row = dict(entry)
		row["role_installed"] = bool(_role_installed(entry["mobile_role"]))
		try:
			row["designation_exists"] = bool(frappe.db.exists("Designation", entry["designation"]))
		except Exception:  # pragma: no cover - a site without Frappe HR
			row["designation_exists"] = False
		out.append(row)
	return out


def spec_for(role: str) -> RoleSpec | None:
	return BY_NAME.get((role or "").strip())


def permission_targets() -> set:
	"""Every doctype any of these roles is granted something on."""
	return {doctype for spec in ROLE_SPECS for doctype, _flags in spec.permissions}


# ── the installer ───────────────────────────────────────────────────────────
def install_roles() -> dict:
	"""Create the roles and their permissions. Idempotent, never raises.

	Runs from `after_install` and `after_migrate`. Everything it could not do
	lands in `report["failed"]` for `install.py` to print.
	"""
	report = {
		"roles": list(ROLE_NAMES),
		"created_roles": [],
		"existing_roles": [],
		"created_permissions": [],
		"existing_permissions": [],
		"mirrored_doctypes": [],
		"skipped_doctypes": [],
		"failed": [],
	}

	if not doctype_exists(ROLE_DOCTYPE):  # pragma: no cover - a site with no Role table
		report["failed"].append(
			{"name": ROLE_DOCTYPE, "reason": "this site has no Role doctype, which should not be possible"}
		)
		return report

	for spec in ROLE_SPECS:
		try:
			_ensure_role(spec, report)
		except Exception as exc:  # pragma: no cover - _ensure_role catches its own
			report["failed"].append({"name": spec.name, "reason": f"{type(exc).__name__}: {exc}"})

	for spec in ROLE_SPECS:
		for doctype, flags in spec.permissions:
			try:
				_ensure_permission(spec.name, doctype, flags, report)
			except Exception as exc:
				report["failed"].append(
					{"name": f"{spec.name} on {doctype}", "reason": f"{type(exc).__name__}: {exc}"}
				)
	return report


def _ensure_role(spec: RoleSpec, report: dict) -> None:
	if frappe.db.exists(ROLE_DOCTYPE, spec.name):
		report["existing_roles"].append(spec.name)
		return
	doc = frappe.get_doc(
		{
			"doctype": ROLE_DOCTYPE,
			"role_name": spec.name,
			"desk_access": spec.desk_access,
			# A role that is not "disabled" and has no users is inert, so there is
			# nothing to gate here. Restricting to IP or two-factor is an operator
			# decision on the Role form, not one this app makes for them.
			"is_custom": 1,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_if_duplicate=True)
	report["created_roles"].append(spec.name)


def _ensure_permission(role: str, doctype: str, flags: dict, report: dict) -> None:
	"""Add one Custom DocPerm, mirroring the standard set first if it has to.

	Refuses, loudly, on a doctype this app does not own. See the module docstring
	— that refusal is the single most important line in this file.
	"""
	if not doctype_exists(doctype):
		# A site that has not migrated yet, or an older ERPNext. Not a failure:
		# the doctype arrives with the app and the next migrate picks it up.
		if doctype not in report["skipped_doctypes"]:
			report["skipped_doctypes"].append(doctype)
		return

	if _is_child_table(doctype):
		report["failed"].append(
			{
				"name": f"{role} on {doctype}",
				"reason": (
					f"{doctype} is a CHILD TABLE. A child row's access follows its parent's in "
					"Frappe, so a permission here would be read by nothing — while still turning "
					"off every standard permission the doctype has. Grant the parent instead."
				),
			}
		)
		return

	if not _owned_by_this_app(doctype):
		report["failed"].append(
			{
				"name": f"{role} on {doctype}",
				"reason": (
					f"{doctype} belongs to another app, and writing a Custom DocPerm on it would "
					"make Frappe ignore EVERY standard permission that doctype has, for every "
					"role on this site. Refused. Assign the owning app's own role instead — see "
					"RoleSpec.companion_roles."
				),
			}
		)
		return

	if frappe.db.exists(CUSTOM_DOCPERM, {"parent": doctype, "role": role, "permlevel": 0}):
		report["existing_permissions"].append(f"{role} on {doctype}")
		return

	_mirror_standard_perms(doctype, report)

	doc = frappe.get_doc({"doctype": CUSTOM_DOCPERM, "parent": doctype, **flags, "role": role})
	doc.flags.ignore_permissions = True
	doc.insert(ignore_if_duplicate=True)
	report["created_permissions"].append(f"{role} on {doctype}")


def _is_child_table(doctype: str) -> bool:
	try:
		return bool(int(frappe.db.get_value("DocType", doctype, "istable") or 0))
	except Exception:  # pragma: no cover - a DocType table we cannot read
		return False


def _owned_by_this_app(doctype: str) -> bool:
	"""Does this app ship the doctype? Read from `tabDocType.module`.

	Read from the database rather than from a hardcoded list, because a list
	would go stale the first time somebody adds a doctype and the failure would
	be a silently skipped permission rather than an error.
	"""
	try:
		return str(frappe.db.get_value("DocType", doctype, "module") or "") == OWNED_MODULE
	except Exception:  # pragma: no cover - a DocType table we cannot read
		return False


def _mirror_standard_perms(doctype: str, report: dict) -> None:
	"""Copy every DocPerm into Custom DocPerm, ONCE, before the first custom row.

	THIS FUNCTION IS THE REASON THE INSTALLER IS SAFE, and Frappe's own Role
	Permission Manager does exactly the same thing under the name
	`setup_custom_perms`. Read `get_all_perms` and the necessity is immediate:
	the presence of ANY Custom DocPerm row for a doctype makes Frappe discard
	every standard DocPerm on it, for every role. Without this mirror, granting
	Field Worker read on Farm Task would revoke System Manager from Farm Task.

	Runs once per doctype — the existence check is on the doctype, not on the
	role — and does nothing at all on a doctype an operator has already
	customised through the Desk, because then the mirror has already happened.

	A STANDARD ROW NAMING A ROLE THIS SITE DOES NOT HAVE IS SKIPPED, AND THAT IS
	NOT A HOLE IN THE MIRROR. v0.59.3, found by granting Farm Manager the I-9:
	that doctype ships a DocPerm for `HR Manager`, which comes from `hrms` and is
	absent on a bench that never installed it. `Custom DocPerm.role` is a Link, so
	copying the row there raises `LinkValidationError` — and a mirror that raises
	HALF WAY THROUGH is the exact failure this function exists to prevent, because
	the rows it managed to write are enough to make Frappe discard every standard
	DocPerm the doctype had. Skipping the unresolvable row takes nothing away from
	anybody: a permission held by a role no site has is held by no user, and the
	rows for roles that DO exist are mirrored intact. The alternative — abort, and
	report the grant as failed — leaves the doctype in the state the abort caused.
	"""
	if frappe.db.exists(CUSTOM_DOCPERM, {"parent": doctype}):
		return
	# `fields="*"` — the string, which is Frappe's own idiom and what its
	# `copy_perms` passes. A permission is a row of twenty flag columns and
	# naming them here would mean a Frappe version that adds a twenty-first
	# silently drops it from the mirror, which is how a mirrored permission
	# quietly becomes a weaker one.
	rows = frappe.db.get_all(DOCPERM, filters={"parent": doctype}, fields="*", order_by="idx asc") or []
	mirrored = 0
	for row in rows:
		if not _role_installed(str(dict(row).get("role") or "")):
			# See the docstring: a permission for a role this site does not have
			# grants nobody anything, and copying it would abort the mirror.
			continue
		payload = {
			key: value
			for key, value in dict(row).items()
			if key
			not in (
				"name",
				"creation",
				"modified",
				"owner",
				"modified_by",
				"idx",
				"doctype",
				"parentfield",
				"parenttype",
			)
		}
		payload.update({"doctype": CUSTOM_DOCPERM, "parent": doctype})
		doc = frappe.get_doc(payload)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_if_duplicate=True)
		mirrored += 1
	if mirrored and doctype not in report["mirrored_doctypes"]:
		report["mirrored_doctypes"].append(doctype)


# ── reading it back ─────────────────────────────────────────────────────────
def describe_role(spec: RoleSpec, include_permissions: bool = True) -> dict:
	"""One role as a tool result: what it is for, and what it may and may not do."""
	out = {
		"role": spec.name,
		"description": spec.description,
		"summary": spec.summary,
		"desk_access": bool(spec.desk_access),
		"companion_roles": list(spec.companion_roles),
		"cannot": list(spec.cannot),
		"installed": bool(_role_installed(spec.name)),
	}
	if include_permissions:
		out["permissions"] = [
			{
				"doctype": doctype,
				"read": bool(flags["read"]),
				"write": bool(flags["write"]),
				"create": bool(flags["create"]),
				"delete": bool(flags["delete"]),
			}
			for doctype, flags in spec.permissions
		]
	return out


def _role_installed(role: str) -> bool:
	try:
		return bool(frappe.db.exists(ROLE_DOCTYPE, role))
	except Exception:  # pragma: no cover
		return False


def roles_of(user: str) -> list:
	"""Which of THIS APP'S own roles a user holds, in spec order.

	Deliberately not every role they hold: a Farm Manager who is also a System
	Manager is a fact about the site's own configuration, and this app's tools
	answer questions about this app's roles.
	"""
	try:
		held = {
			row["role"]
			for row in frappe.db.get_all(
				"Has Role", filters={"parent": user, "parenttype": "User"}, fields=["role"], limit=200
			)
			or []
		}
	except Exception:  # pragma: no cover - no Has Role table
		return []
	return [name for name in ROLE_NAMES if name in held]


#: The roles that may DISPATCH — raise work, send somebody to it, or read a
#: board that is not their own. Defined HERE, beside the specs, rather than in
#: `api/guard.py` where it used to live and which now imports it: a client
#: asking "may this person be handed an approval task" and the server refusing
#: the same person must be reading one list, and two frozensets in two modules
#: are two lists that agree until somebody edits one.
DISPATCH_ROLES = frozenset({"Foreman", "Farm Manager"})


def capability_of(user_id: str) -> dict:
	"""What this app's roles say ONE PERSON may do. Never raises. v0.106.0.

	WHY A HANDSET NEEDS THIS AT ALL. A picker screen that offers everybody as the
	holder of an approval task offers the wrong people: the server refuses a
	Field Worker, so the foreman picks a name, taps Assign, and reads a 403 about
	somebody else's roles. The app cannot work the answer out — a `designation`
	is a job title and not a permission, and until this release the mobile API
	returned no role on anybody but the CALLER, from
	`get_current_user_context`. So the picker filtered by nothing, or by
	designation, which is a different register (see the table below).

	`can_dispatch` IS THE ANSWER TO THE QUESTION ACTUALLY BEING ASKED, and it is
	computed from `DISPATCH_ROLES` — the same frozenset `guard.require_dispatch_role`
	refuses on — rather than from a client-side list of role names that would go
	stale the release a seventh role is added.

	A COURTESY, NOT A BOUNDARY, and every caller has to be told so. The gate is
	`guard.require_dispatch_role` on the server, on every call, and it is
	unchanged; this exists so a picker can grey a row out instead of letting
	somebody discover the refusal after they have chosen. A client that trusted
	this INSTEAD of the server would be a sign on a door with no lock.

	`""` OR AN EMPTY ANSWER IS NOT "no roles". A worker with no `user_id` has no
	login and therefore no roles to read — which is most of a picking crew — and
	`has_login` says which of the two a caller is looking at, because "this
	person may not dispatch" and "this person has no account on this system" are
	different sentences to put in front of a foreman.
	"""
	login = str(user_id or "").strip()
	if not login:
		return {"has_login": False, "mobile_roles": [], "primary_role": None, "can_dispatch": False}
	held = roles_of(login)
	return {
		"has_login": True,
		"mobile_roles": held,
		# `held[0]` — THE FIRST ROLE IN `ROLE_SPECS` THAT THIS PERSON HOLDS, AND
		# NOTHING MORE. `ROLE_SPECS` is in the order roles were ADDED to this app,
		# not in order of capability, and v0.106.0 shipped a comment here claiming
		# the opposite. It was false in both directions: Crew Leader sits after
		# Foreman while its own spec says "Not the dispatch board", and Advisor
		# sits last while its own spec calls it "the narrowest role in the app".
		#
		# THE KEY PREDATES THAT COMMENT BY NINETY RELEASES — `tools/mobile.py`'s
		# `get_current_user_context` has reported `held[0]` since v0.17.0 — which
		# is why it is still here rather than deleted with the false claim. What
		# was removed is `senior_role`, `held[-1]`, added in v0.106.0 on the same
		# bad premise and read by nothing: no caller in this app and no field in
		# the iOS client ever decoded it.
		#
		# DO NOT BUILD A BADGE ON THIS. `role_indicator` below is the answer to
		# "what word describes this person", and `ROLE_INDICATORS` states its own
		# precedence and says in as many words that precedence is not seniority.
		# `test_role_indicator.py` holds the counterexamples as negative controls.
		"primary_role": held[0] if held else None,
		"can_dispatch": bool(set(held) & DISPATCH_ROLES),
	}


class RoleIndicator:
	"""One badge: the high-level category a person falls into, and how to draw it.

	`precedence` IS NOT SENIORITY AND MUST NOT BE READ AS IT. It is the order the
	badge is PICKED in when somebody holds several roles, and it is chosen for
	what a field app should show — see `ROLE_INDICATORS`.
	"""

	__slots__ = ("description", "key", "label", "precedence", "role", "short_label")

	def __init__(self, key: str, label: str, short_label: str, role: str, precedence: int, description: str):
		self.key = key
		self.label = label
		self.short_label = short_label
		self.role = role
		self.precedence = precedence
		self.description = description


#: The badge vocabulary, in the order a badge is chosen when somebody holds more
#: than one role. v0.108.0.
#:
#: WHY THIS IS A SEPARATE TABLE FROM `ROLE_SPECS` AND NOT ITS ORDER. The handset
#: has been drawing its own badge out of the raw `roles` array
#: `get_current_user_context` returns — intersecting it with a hardcoded Swift
#: list and taking whatever came first — which is a copy of this app's role
#: vocabulary living in a compiled binary, and it goes stale the release a role
#: is added or renamed. The obvious fix was to send `capability_of`'s
#: `senior_role` — `held[-1]` in `ROLE_SPECS` order — and that would have been
#: WRONG, because `ROLE_SPECS` is ordered by when each role was WRITTEN, not by
#: capability: it puts `Advisor`, the narrowest role in the app, last, so a Farm
#: Manager who also advises an entity would have badged "Advisor". That field was
#: deleted in v0.109.0 rather than left as a trap; `primary_role` remains, is
#: `held[0]`, is equally not a ranking, and is documented as such at its own
#: definition. Neither has ever been a badge.
#:
#: So the badge order is stated here, once, explicitly, and it answers exactly
#: one question: **of everything this person holds, which one word describes them
#: on a screen the size of a phone?** Two consequences follow, and both are
#: deliberate:
#:
#:   * `Administrator` outranks every farm role. Somebody carrying System Manager
#:     on a handset is the operator, and a badge saying "Field Worker" over an
#:     account that can do anything is the badge lying.
#:   * `Family Member` and `Advisor` come LAST, below `Field Worker`, because they
#:     are not on the operational ladder at all. Somebody who holds one of them
#:     AND an operational role is holding a phone in an orchard, and the
#:     operational word is the useful one. Somebody who holds only one of them
#:     badges as it, which is the case that made them worth listing.
#:
#: IT IS A DISPLAY FACT AND NOT A PERMISSION, and every caller has to be told so
#: in as many words. `can_dispatch` beside it is the same courtesy
#: `capability_of` already documents: `guard.require_dispatch_role` still runs on
#: every call, and a client that trusted the badge instead of the server would be
#: a sign on a door with no lock.
ROLE_INDICATORS = (
	RoleIndicator(
		"administrator",
		"Administrator",
		"ADM",
		"System Manager",
		1,
		"Holds System Manager on this site — the operator's own account, not a farm role.",
	),
	RoleIndicator(
		"farm_manager",
		"Farm Manager",
		"MGR",
		"Farm Manager",
		2,
		"Runs the operation: the work, the compliance behind it, and the ground it happens on.",
	),
	RoleIndicator(
		"compliance_officer",
		"Compliance Officer",
		"CMP",
		"Compliance Officer",
		3,
		"Keeps the registers an audit asks for, and builds the packet that answers it.",
	),
	RoleIndicator(
		"foreman",
		"Foreman",
		"FRM",
		"Foreman",
		4,
		"Runs the board: what needs doing, who is doing it, and what came back.",
	),
	RoleIndicator(
		"crew_leader",
		"Crew Leader",
		"CRW",
		"Crew Leader",
		5,
		"Runs the crew in front of them: the shift, the breaks called, and what the day turned in.",
	),
	RoleIndicator(
		"field_worker",
		"Field Worker",
		"FLD",
		"Field Worker",
		6,
		"Takes work from the pool and files the evidence that closes it.",
	),
	RoleIndicator(
		"family_member",
		"Family Member",
		"FAM",
		"Family Member",
		7,
		"Sees what the family owns, who owns it, and the paper that says so.",
	),
	RoleIndicator(
		"advisor",
		"Advisor",
		"ADV",
		"Advisor",
		8,
		"Reads the documents for the entity they advise, and nothing else.",
	),
)

#: What somebody with a login and none of the roles above badges as. A REAL
#: ANSWER RATHER THAN A NULL, because the phone has to draw something and "no
#: role on this system" is a different sentence from "we could not work it out"
#: — the first sends somebody to an operator, the second sends them to support.
NO_ROLE_INDICATOR = RoleIndicator(
	"none",
	"No Role",
	"—",
	"",
	99,
	"Has a login but holds none of this app's roles. An operator has to grant one.",
)

INDICATOR_BY_ROLE = {item.role: item for item in ROLE_INDICATORS}


def role_indicator(user_id: str) -> dict:
	"""The badge for ONE person: which high-level category they fall into. v0.108.0.

	Never raises, for the same reason `capability_of` does not: it is folded into
	`get_current_user_context`, which doubles as credential validation, and a
	failure here would read on the handset as "this token is dead, sign out".

	`has_login` IS FALSE FOR MOST OF A PICKING CREW. A worker with no `user_id`
	holds no roles because there is no account for a role to hang off, and
	"nobody has given this person an account" is a different fact from "this
	person's account may not dispatch". The badge is `none` either way; only
	`has_login` tells the two apart.
	"""
	login = str(user_id or "").strip()
	if not login:
		return _indicator_payload(NO_ROLE_INDICATOR, held=[], has_login=False, is_admin=False)
	held = roles_of(login)
	try:
		is_admin = "System Manager" in set(all_roles_of(login))
	except Exception:  # pragma: no cover - no Has Role table
		is_admin = False
	candidates = [INDICATOR_BY_ROLE[name] for name in held if name in INDICATOR_BY_ROLE]
	if is_admin:
		candidates.append(INDICATOR_BY_ROLE["System Manager"])
	chosen = min(candidates, key=lambda item: item.precedence) if candidates else NO_ROLE_INDICATOR
	return _indicator_payload(chosen, held=held, has_login=True, is_admin=is_admin)


def _indicator_payload(indicator: RoleIndicator, held: list, has_login: bool, is_admin: bool) -> dict:
	return {
		"key": indicator.key,
		"label": indicator.label,
		"short_label": indicator.short_label,
		"precedence": indicator.precedence,
		"description": indicator.description,
		"has_login": has_login,
		"is_administrator": is_admin,
		# Repeated inside the badge rather than only beside it, so a client can
		# decode this one object and have everything a row needs. It is the same
		# value `capability_of` reports and is computed the same way, off the
		# frozenset `guard.require_dispatch_role` refuses on.
		"can_dispatch": bool(set(held) & DISPATCH_ROLES),
	}


def all_roles_of(user: str) -> list:
	"""Every role on a user, this app's and everybody else's. Sorted."""
	try:
		return sorted(
			{
				row["role"]
				for row in frappe.db.get_all(
					"Has Role", filters={"parent": user, "parenttype": "User"}, fields=["role"], limit=500
				)
				or []
			}
		)
	except Exception:  # pragma: no cover
		return []


def companies_for(user: str) -> list:
	"""The Companies a user's User Permissions allow, default first.

	AN EMPTY LIST MEANS "EVERY COMPANY", NOT "NONE". That is Frappe's rule and
	not this app's choice: a user with no User Permission on Company is
	unrestricted, which is why `create_mobile_user` refuses to make a mobile
	account without naming at least one entity. `entity_access_note` below says
	so in words, everywhere it is reported.
	"""
	try:
		rows = (
			frappe.db.get_all(
				USER_PERMISSION,
				filters={"user": user, "allow": "Company"},
				fields=["for_value", "is_default", "applicable_for", "apply_to_all_doctypes"],
				limit=200,
			)
			or []
		)
	except Exception:  # pragma: no cover
		return []
	ordered = sorted(rows, key=lambda row: (0 if row.get("is_default") else 1, str(row.get("for_value"))))
	return [str(row["for_value"]) for row in ordered if row.get("for_value")]


def default_company_for(user: str) -> str:
	companies = companies_for(user)
	return companies[0] if companies else ""


def entity_access_note(companies: list) -> str:
	if companies:
		return (
			f"{len(companies)} entity/entities: {', '.join(companies)}. Every document linking to "
			"a Company outside that list is invisible to this user, across every doctype."
		)
	return (
		"NO User Permission on Company, which in Frappe means UNRESTRICTED — this user sees "
		"every entity on the site. That is almost never what a mobile account should be. "
		"create_mobile_user refuses to produce one; a user in this state was made some other way."
	)


# ── entity_access, and the comma that is part of a company's name ─────────────
#
# "Orchard Meadow, LLC" IS ONE COMPANY. Every parser here used to read it as two,
# because both of them treated a comma as a separator unconditionally:
# `mobile_access_grant._tidy_lines` did `str(raw).replace(",", "\n")` on the way
# into the column and `tools/mobile._resolve_entities` did
# `raw.replace("\n", ",").split(",")` on the way in from a request body. A farm
# whose entities are LLCs — which is most of them — got "Orchard Meadow" and
# "LLC", neither of which is a Company, and the grant either refused a name that
# was correct or recorded two lines of nonsense in a column an auditor reads.
#
# NEWLINE IS THE DELIMITER AND THE COMMA IS A GUESS. A newline cannot occur in a
# Frappe docname, so splitting on one is always right; a comma occurs in company
# names constantly, so splitting on one is only right when the pieces turn out to
# be companies. That asymmetry is the whole of the fix below — the comma is tried
# and then CHECKED, and a split that does not produce known entities is undone.
#
# WHICH WAY TO FAIL. Where nothing can be verified — no `known` predicate — the
# line is kept WHOLE. The two failures are not equal: an unsplit "A, B" typed
# into a form fails loudly at `resolve_company` with "is not a Company on this
# site", which is a sentence somebody can act on, while a wrongly-split "Orchard
# Meadow, LLC" silently writes two entities that scope nothing and reads, on the
# roster, as though somebody deliberately granted them.


def _quote_aware_parts(line: str) -> list:
	"""`'"Orchard Meadow, LLC", Highland'` → the two names, quotes consumed.

	Only used where the line actually carries a quote character. A person who has
	quoted a name has said, unambiguously, where it ends — that is a stronger
	signal than any lookup, so it is honoured before the Company check below and
	without one.
	"""
	try:
		rows = list(csv.reader(io.StringIO(line), skipinitialspace=True))
	except Exception:  # pragma: no cover - csv does not raise on a single line
		return [line]
	parts = [str(cell).strip() for row in rows for cell in row]
	return [part for part in parts if part] or [line]


def split_entity_names(raw, known=None) -> list:
	"""The entity names in `raw`, with a company's own comma left inside it.

	`raw` may be a list — in which case every element is taken WHOLE, because a
	caller that built a list has already said where each name ends and second-
	guessing it is how "Orchard Meadow, LLC" became two entries in the first
	place — or a string, which is split on newlines and then, per line, on commas
	only where that produces names `known` recognises.

	`known(name) -> bool` is the check. `parse_entity_access` supplies the
	Company register; `tools/mobile._resolve_entities` supplies `resolve_company`,
	so abbreviations resolve too. WITH NO PREDICATE NOTHING IS COMMA-SPLIT — see
	the block above for why that is the safe direction.
	"""
	if isinstance(raw, (list, tuple, set)):
		lines = [str(entry).strip() for entry in raw]
	else:
		lines = [chunk.strip() for chunk in str(raw or "").split("\n")]

	out = []
	for line in lines:
		if not line:
			continue
		for name in _names_in(line, known):
			if name and name not in out:
				out.append(name)
	return out


def _names_in(line: str, known) -> list:
	"""One line of `entity_access` as the names it holds. Never returns [].

	LONGEST MATCH FIRST, LEFT TO RIGHT. "Orchard Meadow, LLC, Example Trading Co"
	is two companies and there is no way to see that from the commas alone: the
	line has three comma-separated pieces, one of them ("LLC") is not a company
	on its own, and an all-or-nothing rule reads the whole thing as one name
	nobody can resolve. So each position tries the LONGEST run of pieces that is
	a known entity before it tries a shorter one, which is what makes
	"Orchard Meadow, LLC" win over "Orchard Meadow" at the same starting point.

	THE UNRESOLVABLE TAIL COMES BACK WHOLE rather than in pieces. Where no run
	starting at some position is known, everything from there to the end of the
	line is returned as one name — so the caller's refusal says
	"'Nowhere Farms, LLC' is not a Company on this site" and names the thing
	somebody actually typed, instead of blaming a fragment of it.
	"""
	if '"' in line or "'" in line:
		return _quote_aware_parts(line)
	if "," not in line or known is None:
		# No comma, or nothing to check one against. Keep it whole and let the
		# caller's own resolver be the one to complain.
		return [line]

	# Split without stripping, so a candidate rejoined with "," is character-for-
	# character the substring somebody typed — "A,B" and "A, B" both look up as
	# they were written rather than as this function would have spelled them.
	pieces = line.split(",")
	names = []
	start = 0
	while start < len(pieces):
		for end in range(len(pieces), start, -1):
			candidate = ",".join(pieces[start:end]).strip()
			if candidate and known(candidate):
				names.append(candidate)
				start = end
				break
		else:
			tail = ",".join(pieces[start:]).strip()
			if tail:
				names.append(tail)
			break
	return names or [line.strip()]


def parse_entity_access(raw) -> list:
	"""`Mobile Access Grant.entity_access` as a list of Company names.

	The reader half of the column. `known` is the Company register itself, so a
	stored "Orchard Meadow, LLC" survives a round trip and a stored "A, B" — two
	companies somebody comma-separated into one line before this was fixed — is
	still read as the two it was meant to be.
	"""

	def known(name: str) -> bool:
		try:
			return bool(frappe.db.exists("Company", name))
		except Exception:  # pragma: no cover - no table on a bare site
			return False

	return split_entity_names(raw, known)


def tidy_entity_access(raw) -> str:
	"""The column's stored form: one entity per line, blanks and duplicates gone."""
	return "\n".join(parse_entity_access(raw))
