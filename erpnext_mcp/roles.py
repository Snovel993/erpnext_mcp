# SPDX-License-Identifier: MIT
"""The six mobile roles, and the installer that puts them on a site.

v0.17.0. Sprint 8 gave the operation a dispatch board. This is what makes it
safe to point a phone at from outside the LAN: six roles that say what KIND of
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

So: SIX ROLES, and `create_mobile_user` writes the User Permissions.

  Field Worker        the phone in the orchard. Reads the pool and the job,
                      writes their own assignment and the evidence on it.
  Foreman             the dispatch board. Raises work, sends people to it,
                      reads the compliance calendar for the operating company.
  Compliance Officer  the registers: policies, certificates, filings, audits.
                      Cannot dispatch anybody — see below.
  Farm Manager        operations and the ground under them: tasks, compliance,
                      parcels, leases, housing. Not the investment side.
  Family Member       the holding-company view: cap table, member events,
                      governance, related parties, the land. Not the operator's
                      day-to-day task list.
  Advisor             the narrowest role in the app: governance documents,
                      related parties and regulatory filings, read-only.

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
     the installer runs is the set that was in force before it, PLUS the six
     roles — which is the same promise the rest of this app makes.

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

	`delete` is here and is used exactly nowhere in the six specs below. It is
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
GROUND = ("Parcel", "Field", "Irrigation Zone")
CAMP = ("Housing Unit", "Housing Assignment")
HOLDING = ("Cap Table Entry", "Member Event", "Note Payable", "Asset Cost Profile")
PAPER = ("Governance Document", "Related Party")
PEOPLE = ("Family",)
PROPERTY = ("Lease",)


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
		),
		cannot=(
			"read the SOP library (Compliance Policy) — a task's instructions belong in its notes",
			"read the compliance calendar",
			"raise, assign or cancel work",
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
			*_grant(READ, COMPLIANCE_REGISTERS, CAMP, GROUND),
		),
		cannot=(
			"touch accounting — no account, journal entry or ledger permission is granted "
			"to this role anywhere",
			"edit the certificate or SOP registers (read only)",
			"see the cap table, member events or governance archive",
		),
	),
	RoleSpec(
		name="Compliance Officer",
		description=(
			"The compliance framework end to end: policies, certificates, filings, audits "
			"and the alert calendar, for the companies their User Permissions name."
		),
		summary=(
			"You keep the registers an audit asks for, and you can build the packet that "
			"answers it."
		),
		permissions=(
			*_grant(FULL, COMPLIANCE_REGISTERS, FIELD_RECORDS),
			*_grant(READ_WRITE, CALENDAR),
			# READ, deliberately. See the module docstring: the person who decides
			# a walk is required and the person who decides who walks it must not
			# be the same account.
			*_grant(READ, DISPATCH, CAMP, GROUND, PAPER, SHIFTS),
		),
		cannot=(
			"dispatch anybody — Farm Task is read-only for this role, on purpose",
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
		companion_roles=("Employee",),
		permissions=(
			*_grant(FULL, DISPATCH, FIELD_RECORDS, COMPLIANCE_REGISTERS, GROUND, CAMP, PROPERTY, SHIFTS),
			*_grant(READ_WRITE, CALENDAR),
			*_grant(READ, PAPER),
		),
		cannot=(
			"see the cap table or member events — the operating side does not read the "
			"holding company's equity",
			"edit the governance archive (read only)",
			"touch notes payable or asset cost profiles",
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


def spec_for(role: str) -> RoleSpec | None:
	return BY_NAME.get((role or "").strip())


def permission_targets() -> set:
	"""Every doctype any of the six roles is granted something on."""
	return {doctype for spec in ROLE_SPECS for doctype, _flags in spec.permissions}


# ── the installer ───────────────────────────────────────────────────────────
def install_roles() -> dict:
	"""Create the six roles and their permissions. Idempotent, never raises.

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
	"""
	if frappe.db.exists(CUSTOM_DOCPERM, {"parent": doctype}):
		return
	# `fields="*"` — the string, which is Frappe's own idiom and what its
	# `copy_perms` passes. A permission is a row of twenty flag columns and
	# naming them here would mean a Frappe version that adds a twenty-first
	# silently drops it from the mirror, which is how a mirrored permission
	# quietly becomes a weaker one.
	rows = frappe.db.get_all(DOCPERM, filters={"parent": doctype}, fields="*", order_by="idx asc") or []
	for row in rows:
		payload = {
			key: value
			for key, value in dict(row).items()
			if key not in ("name", "creation", "modified", "owner", "modified_by", "idx", "doctype", "parentfield", "parenttype")
		}
		payload.update({"doctype": CUSTOM_DOCPERM, "parent": doctype})
		doc = frappe.get_doc(payload)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_if_duplicate=True)
	if rows and doctype not in report["mirrored_doctypes"]:
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
	"""Which of THIS APP'S six roles a user holds, in spec order.

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
