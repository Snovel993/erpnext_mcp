# SPDX-License-Identifier: MIT
"""The personnel register: creating an Employee, editing one, and linking it to a login.

v0.18.1. THIS FILE EXISTS BECAUSE THE MOBILE APP WORKED AND THE DATA DID NOT.

v0.18.0 got a phone all the way through the funnel, the credential and the eleven
methods — and then `list_my_tasks` refused it, correctly, with "set user_id on
their Employee record to this email address". Every Farm Ops method scopes work by
EMPLOYEE, not by User: a task board is a list of what a *person* is assigned, and
the Employee record is the only thing on a Frappe site that says which person a
login is. So an account with no Employee behind it is an account with nothing to
show, and the refusal was right.

What was missing was any way to fix it from here. This app could create the User,
the roles, the entity scoping, the grant, the credential and the QR — six things —
and could not create or edit the ONE record that makes the other six useful. An
operator had to leave the conversation, open the Desk, and find a form. That is
the gap, and these three tools are it:

  * `create_employee`      — the record itself.
  * `update_employee`      — the fields on it that a new hire's first week changes.
  * `link_employee_to_user` — the one field that turns a login into a person.

`onboard_employee` in `newhire.py` orchestrates all three plus the mobile account
and the QR; it is the tool to reach for when the person is new. These are the
tools for when they are not.

────────────────────────────────────────────────────────────────────────────
IT WRITES FOURTEEN FIELDS AND REFUSES EVERYTHING ELSE BY NAME
────────────────────────────────────────────────────────────────────────────

`WRITABLE` is a closed list. Not a filter over the doctype, not "everything
except" — a list, written out, that a reader can check against what they expected.
Everything on it is an identity or assignment fact: who this person is, which
entity hired them, what they do, when they started, how to reach them.

Everything NOT on it is refused, and the payroll, tax and banking fields are
refused with their OWN message, because those are the ones somebody will actually
try. A salary structure, an income tax slab and a bank account number each have a
form, an approval and a retention rule around them that this app knows nothing
about, and a tool that let a language model set `ctc` because it appeared in a
sentence would be a tool that quietly moved money.

────────────────────────────────────────────────────────────────────────────
THE SCHEMA IS ASKED, NEVER ASSUMED
────────────────────────────────────────────────────────────────────────────

Frappe HR's Employee doctype is not the same on every site and is not the same on
every version. `gender` and `employment_type` are Links on a stock install and
Selects on some customised ones; `date_of_birth` and `gender` are MANDATORY on a
stock Frappe HR and are made optional by plenty of operators; a site that never
installed HR's masters has no Department records at all.

So: every field is checked against `frappe.get_meta` before it is written, every
Link target comes off the field's own `options` (with `LINK_TARGETS` only as the
fallback for a site whose meta says nothing), every Select is matched
case-insensitively against the site's own choices, and a Link whose target doctype
does not exist here is NOT validated rather than refused — the double cannot tell
"missing record" from "missing schema", and neither can this.

And the mandatory fields are the site's, not this file's. `_mandatory_gaps` reads
`reqd` off the meta and is used to turn Frappe's `MandatoryError` — which arrives
as a wall of HTML from a controller — into a sentence naming the fields this site
wants. That is the difference between "tell me what to send" and "something went
wrong in hrms".

────────────────────────────────────────────────────────────────────────────
ONE PERSON, ONE EMPLOYEE, ONE LOGIN
────────────────────────────────────────────────────────────────────────────

`Employee.user_id` is a one-to-one in every direction that matters. Two Employee
records naming the same User puts somebody on the dispatch board twice and in the
payroll register once — `newhire.py` has argued that at length since v0.17.1 — and
`list_my_tasks` resolving a login to two employees has no correct answer to give.
So the link is refused when the User already belongs to somebody else, and
refused when the Employee already belongs to somebody else, and is a NO-OP when it
is already exactly what was asked for. Re-running is safe; re-pointing is a
decision (`replace=true`).

A link to a User with no Farm Ops role and no Mobile Access Grant is refused too,
and that refusal is the least obvious one here so it is worth the sentence: the
link's entire purpose is to make the mobile methods answer. A User who cannot
reach them is a link that changes nothing today and silently grants a task board
on the day somebody grants that account a role for an unrelated reason.
`allow_unenrolled_user=true` says "I know, I am doing this ahead of the grant",
which is a real order of operations and is why the escape exists.
"""

from __future__ import annotations

import frappe

from .. import compat, roles, security
from ..args import as_bool, as_choice, as_date, as_str, resolve_company, select_options
from ..errors import ToolError
from ..result import ToolResult

EMPLOYEE = "Employee"
USER = "User"
GRANT = "Mobile Access Grant"

#: Who may touch the personnel register through this app.
#:
#: `Administrator` holds every role Frappe has, so the default configuration —
#: where `effective_user` falls back to Administrator — passes. An operator who
#: pointed `mcp_system_user` at a purpose-built account gets a refusal that NAMES
#: the account and these four roles, because "permission denied" on a principal
#: the operator chose themselves is a one-line fix they cannot make without
#: knowing which line.
#:
#: "Farm Manager" is this app's own role and is here because on this site it is
#: the person who actually hires. "HR User" is Frappe HR's own read/write role and
#: is the one a real HR clerk holds; "HR Manager" is the one an operator assumes
#: is required and would be surprised to find missing.
HR_ROLES = ("System Manager", "HR Manager", "HR User", "Farm Manager")

def _farm_ops_roles() -> frozenset:
	"""The roles that make a login worth linking to an Employee.

	`api/guard.FARM_OPS_ROLES` itself — read rather than re-listed, so the set this
	file refuses against and the set the eleven mobile methods gate on cannot drift
	apart. Imported inside the function because `api/guard` imports the tool layer
	in turn, and a module-level import here would close that circle at load time.
	"""
	from ..api import guard

	return guard.FARM_OPS_ROLES


#: Every field these tools will write, and the doctype each Link points at when
#: this site's meta does not say. See the module docstring on why this is a
#: closed list rather than a filter.
LINK_TARGETS = {
	"company": "Company",
	"department": "Department",
	"designation": "Designation",
	"employment_type": "Employment Type",
	"gender": "Gender",
	"user_id": USER,
}

DATE_FIELDS = ("date_of_joining", "date_of_birth")

SELECT_FIELDS = ("status",)

#: The fourteen. Ordered as somebody filling in a form would read them, because
#: this tuple is what the refusal messages list.
WRITABLE = (
	"employee_name",
	"first_name",
	"last_name",
	"company",
	"date_of_joining",
	"date_of_birth",
	"gender",
	"department",
	"designation",
	"employment_type",
	"status",
	"user_id",
	"personal_email",
	"cell_number",
)

#: Fields refused with their own sentence rather than the generic one. Not a
#: security boundary — everything outside `WRITABLE` is refused either way — but
#: the message is the difference between a caller who understands why and a caller
#: who tries a synonym. Payroll, tax and banking: each has a form, an approval and
#: a retention rule this app knows nothing about.
SENSITIVE_FIELDS = frozenset(
	{
		"bank_ac_no",
		"bank_name",
		"ctc",
		"iban",
		"income_tax_slab",
		"payroll_cost_center",
		"provident_fund_account",
		"salary_currency",
		"salary_mode",
		"salary_structure",
		"tax_withholding_category",
	}
)

#: What a new Employee is unless the caller says otherwise.
DEFAULT_STATUS = "Active"


# ── the guards every tool in this file runs first ───────────────────────────
def require_hr_role() -> str:
	"""The principal this call is attributed to, once it has proved it may hire.

	Returns the user so a caller has one thing to hold and to log. Raises rather
	than returning a flag: there is no half-permitted path through any of these.

	WHICH IDENTITY. `security.caller_identity()` is whoever Frappe authenticated
	THIS request — a phone or a Desk session presenting its own credential — and
	is empty on the ordinary MCP path, where the operator's client presents a
	shared token and no human identity exists to read. There the principal is
	`frappe.session.user`, which by the time any tool runs is the MCP System User
	the operator configured. Both are real principals whose roles the operator
	controls, and gating on whichever one is present is what makes this check mean
	something on both transports instead of meaning nothing on one.
	"""
	actor = security.caller_identity() or str(getattr(frappe.session, "user", "") or "")
	if not actor or actor == "Guest":
		raise ToolError(
			"this call has no identity to attribute a personnel change to. Nothing was changed."
		)
	held = set(frappe.get_roles(actor) or []) or set(roles.all_roles_of(actor) or [])
	if not held & set(HR_ROLES):
		raise ToolError(
			f"{actor} may not change the personnel register: it holds none of "
			f"{', '.join(HR_ROLES)}. This is the account this app acts as — an operator "
			"sets it with `mcp_system_user` on ERPNext MCP Settings, and grants it a role "
			"in the Desk. Nothing was changed."
		)
	return actor


def require_company_scope(actor: str, company: str) -> str:
	"""One Company, checked against what this principal may actually reach.

	FRAPPE'S RULE, NOT THE MOBILE SURFACE'S. A principal with no Company User
	Permission is UNRESTRICTED here, which is what every Desk surface on the site
	already does and what makes an operator's own login work. `api/guard.py`
	deliberately inverts that for the eleven mobile methods and says why at
	length: those face the open internet with a closed list of callers, where
	fail-closed is affordable. This does not, and a personnel tool that refused
	every correctly-configured operator would be turned off within the hour.

	Where the principal IS scoped, the scope is enforced: creating an Employee for
	an entity you cannot see is exactly the mistake this checks for.
	"""
	allowed = roles.companies_for(actor) or []
	if allowed and company not in allowed:
		raise ToolError(
			f"{actor} has no access to company {company!r} — its entity access is "
			f"{', '.join(allowed)}. An Employee belongs to the entity that hired them, and "
			"creating one for an entity you cannot see would put a person on a payroll "
			"register you cannot read. Nothing was changed."
		)
	return company


# ── name resolution ─────────────────────────────────────────────────────────
def resolve_employee(value: str) -> str:
	"""An Employee docname from a docname, an employee_number, a name, or a login.

	Four ways in because those are the four things somebody calls "the employee",
	and only the first is the primary key. Ambiguity is reported with the
	candidates rather than resolved by guessing — two people called Ana Ramos is a
	real situation and picking one of them silently is the worst available answer.
	"""
	value = (value or "").strip()
	if not value:
		raise ToolError("employee is required (its docname, employee number, name, or linked login).")
	if frappe.db.exists(EMPLOYEE, value):
		return value
	for filters in ({"employee_number": value}, {"employee_name": value}, {"user_id": value.lower()}):
		field = next(iter(filters))
		if not compat.has_field(EMPLOYEE, field):
			continue
		matches = frappe.db.get_all(EMPLOYEE, filters=filters, pluck="name", limit=10)
		if len(matches) == 1:
			return matches[0]
		if len(matches) > 1:
			raise ToolError(
				f"{value!r} matches {len(matches)} employees: {', '.join(sorted(matches))}. "
				"Pass the Employee docname. Nothing was changed."
			)
	raise ToolError(
		f"no Employee matching {value!r} (tried docname, employee_number, employee_name and "
		"user_id). list_employees has every one on this site. Nothing was changed."
	)


# ── field validation ────────────────────────────────────────────────────────
def _link_target(fieldname: str) -> str:
	"""What a Link field points at, asked of the site before assumed.

	The site's own `options` wins, because an operator who re-pointed
	`Employee.designation` at a doctype of their own is right and this file is not.
	`LINK_TARGETS` is the fallback for a meta that says nothing, which is what a
	Data field customised into place looks like.
	"""
	field = compat.field_meta(EMPLOYEE, fieldname)
	fieldtype = str((field or {}).get("fieldtype") or "")
	if fieldtype in ("Link", "Dynamic Link"):
		target = str((field or {}).get("options") or "").strip()
		if target:
			return target
	if fieldtype in ("Select", "Data"):
		# Explicitly not a Link on this site. Nothing to check a record against.
		return ""
	return LINK_TARGETS.get(fieldname, "")


def _clean(fieldname: str, raw, label: str = "") -> str:
	"""One field value, coerced and checked against this site's schema.

	Returns the value to write. Raises with the site's own choices listed for
	anything it cannot accept — a caller that is a language model can act on
	"must be one of: Active, Inactive, Suspended, Left" and cannot act on a
	controller traceback.
	"""
	label = label or fieldname
	if fieldname in DATE_FIELDS:
		return as_date({fieldname: raw}, fieldname) or ""

	value = str(raw or "").strip()
	if not value:
		return ""

	if fieldname == "user_id":
		value = value.lower()

	if fieldname in SELECT_FIELDS and select_options(EMPLOYEE, fieldname):
		return as_choice(EMPLOYEE, fieldname, value, label)

	target = _link_target(fieldname)
	if target and compat.doctype_exists(target) and not frappe.db.exists(target, value):
		# A Link whose target doctype is ABSENT is deliberately not checked: the
		# value cannot be verified against a schema that is not here, and refusing
		# would refuse a perfectly good value on a site that simply never installed
		# HR's masters. Frappe does not validate that link either.
		raise ToolError(
			f"{label} {value!r} is not a {target} on this site. {_known(target)} Nothing was changed."
		)
	return value


def _known(doctype: str) -> str:
	"""A few real values of `doctype`, so a refusal is also an answer."""
	try:
		names = frappe.db.get_all(doctype, pluck="name", limit=12, order_by="name asc") or []
	except Exception:  # pragma: no cover - a doctype with no name column
		return ""
	if not names:
		return f"This site has no {doctype} records at all — create one first."
	return f"Known {doctype}: {', '.join(str(name) for name in names)}."


def _reject_unknown(args: dict, allowed: tuple, reserved: tuple = ()) -> None:
	"""Refuse a field this tool does not write, and say which kind of no it is."""
	for key in args:
		if key in allowed or key in reserved:
			continue
		if key in SENSITIVE_FIELDS:
			raise ToolError(
				f"{key!r} is a payroll, tax or banking field, and this app does not write those. "
				"A salary structure, an income tax slab and a bank account number each have a "
				"form, an approval and a retention rule around them that this app knows nothing "
				"about. Set it in the Desk, where the HR module's own checks run. Nothing was "
				"changed."
			)
		if not compat.has_field(EMPLOYEE, key):
			raise ToolError(
				f"{key!r} is not an argument this tool takes, and is not a field on this site's "
				f"Employee doctype either. The fields it writes are: {', '.join(WRITABLE)}. "
				"Nothing was changed."
			)
		raise ToolError(
			f"{key!r} is a real Employee field on this site but is not one this tool writes. "
			f"The fourteen it does are: {', '.join(WRITABLE)}. Anything else belongs in the "
			"Desk, where the HR module's own validation runs. Nothing was changed."
		)


def _supported(payload: dict) -> tuple:
	"""Split a payload into what this site's Employee has and what it does not.

	A field the doctype does not carry is NOT written and NOT silently dropped —
	it is reported, because "this Frappe HR does not have `cell_number`" is a fact
	about the site the caller needs, and a tool that swallowed it would report
	success for a value that went nowhere.
	"""
	writable, absent = {}, []
	for key, value in payload.items():
		if compat.has_field(EMPLOYEE, key):
			writable[key] = value
		else:
			absent.append(key)
	return writable, absent


def _mandatory_gaps(payload: dict) -> list:
	"""Fields this site marks mandatory on Employee that the payload has not filled.

	Read off the meta rather than listed here, because which fields are required
	is an operator's decision: stock Frappe HR requires `gender` and
	`date_of_birth` and plenty of sites make both optional.

	CHECKED BEFORE THE INSERT RATHER THAN AFTER IT. Frappe would refuse the same
	record a moment later, so refusing here costs nothing and buys a sentence
	naming the fields instead of a controller's `MandatoryError`. `_insert` keeps
	the same message as a catch, for a requirement a controller imposes in code
	that the meta does not express.
	"""
	try:
		fields = frappe.get_meta(EMPLOYEE).fields
	except Exception:  # pragma: no cover - no meta for Employee
		return []
	gaps = []
	for field in fields:
		if not compat.checked(field.get("reqd")):
			continue
		name = str(field.get("fieldname") or "")
		if not name or field.get("default") or name in ("naming_series", "employee"):
			continue
		if not str(payload.get(name) or "").strip():
			gaps.append(name)
	return sorted(gaps)


# ── the linkage rules ───────────────────────────────────────────────────────
def linkage_state(user: str) -> dict:
	"""Everything that decides whether linking this User is worth doing.

	Returned in the result of all three tools, because "the link exists" and "the
	phone will now get a task list" are different facts and only the second is the
	one somebody actually wanted.
	"""
	held = set(roles.all_roles_of(user) or [])
	grant = {}
	if compat.doctype_exists(GRANT):
		grant = (
			frappe.db.get_value(GRANT, {"user": user}, ["name", "state", "mobile_role"], as_dict=True)
			or {}
		)
	farm_ops = sorted(held & _farm_ops_roles())
	return {
		"user": user,
		"roles_held": sorted(held),
		"farm_ops_roles": farm_ops,
		"grant": grant.get("name"),
		"grant_state": grant.get("state"),
		"entity_access": roles.companies_for(user),
		"farm_ops_ready": bool(farm_ops) and str(grant.get("state") or "") == "Active",
	}


def _require_enrolled(user: str, args: dict) -> dict:
	"""A User worth linking, or the reason it is not one.

	See the module docstring. The escape is real and named, because enrolling
	after the Employee exists is a legitimate order of operations — it is what
	`onboard_employee` does, in that order, on purpose.
	"""
	state = linkage_state(user)
	if as_bool(args, "allow_unenrolled_user", False):
		return state
	if state["farm_ops_ready"] or state["farm_ops_roles"]:
		return state
	raise ToolError(
		f"{user} has no Farm Ops role and no Mobile Access Grant, so linking it to an Employee "
		"would change nothing today: the eleven mobile methods gate on "
		f"{', '.join(sorted(_farm_ops_roles()))} and on an Active grant, and this account has "
		"neither. Run create_mobile_user for it first — or pass allow_unenrolled_user=true if "
		"you are deliberately linking ahead of the grant. Nothing was changed."
	)


def _require_free_user(user: str, employee: str = "") -> None:
	"""Refuse a User that already belongs to somebody else. One person, one login."""
	if not compat.has_field(EMPLOYEE, "user_id"):
		return
	holder = frappe.db.get_value(EMPLOYEE, {"user_id": user}, "name")
	if holder and str(holder) != employee:
		name = frappe.db.get_value(EMPLOYEE, holder, "employee_name") or holder
		raise ToolError(
			f"{user} is already the login for Employee {holder} ({name}). Employee.user_id is a "
			"one-to-one: two Employee records naming one login puts somebody on the dispatch "
			"board twice, and gives list_my_tasks two answers where it needs one. Unlink that "
			"record first with update_employee, or use a different address. Nothing was changed."
		)


# ── 1. create_employee ──────────────────────────────────────────────────────
def create_employee(args: dict) -> ToolResult:
	"""One Employee record, with the site's own schema as the arbiter of every field."""
	compat.require_doctype(EMPLOYEE, "It comes with the Frappe HR (hrms) app.")
	_reject_unknown(args, WRITABLE, reserved=("allow_unenrolled_user", "allow_duplicate_name"))
	actor = require_hr_role()

	employee_name = as_str(args, "employee_name", required=True)
	if " " not in employee_name:
		raise ToolError(
			f"employee_name is {employee_name!r}. An I-9, a payroll register and a dispatch board "
			"all name the same person, and a record carrying one word names nobody findable. "
			"Nothing was created."
		)
	company = require_company_scope(actor, resolve_company(as_str(args, "company"), required=True))

	# first/last default off the full name rather than being demanded again. The
	# first token and the last token is what a form filled in by hand produces,
	# and a caller who wants something else passes it.
	tokens = [part for part in employee_name.replace(",", " ").split() if part]
	payload = {
		"employee_name": employee_name,
		"company": company,
		"first_name": as_str(args, "first_name") or (tokens[0] if tokens else employee_name),
		"last_name": as_str(args, "last_name") or (tokens[-1] if len(tokens) > 1 else ""),
		"date_of_joining": _clean("date_of_joining", args.get("date_of_joining"))
		or frappe.utils.today(),
		"status": _clean("status", args.get("status") or DEFAULT_STATUS, "status"),
	}
	optional = (
		"date_of_birth",
		"gender",
		"department",
		"designation",
		"employment_type",
		"personal_email",
		"cell_number",
	)
	for key in optional:
		value = _clean(key, args.get(key))
		if value:
			payload[key] = value

	user_id = _clean("user_id", args.get("user_id"), "user_id")
	linkage = None
	if user_id:
		_require_free_user(user_id)
		linkage = _require_enrolled(user_id, args)
		payload["user_id"] = user_id

	_refuse_a_second_record(employee_name, company, user_id, args)

	writable, absent = _supported(payload)
	gaps = _mandatory_gaps(writable)
	if gaps:
		raise ToolError(_mandatory_message(gaps))
	name = _insert(writable)

	data = {
		"employee": name,
		"employee_name": employee_name,
		"company": company,
		"actor": actor,
		"fields_set": {key: value for key, value in writable.items() if key != "doctype" and value},
		"defaults_applied": sorted(
			key
			for key in ("first_name", "last_name", "date_of_joining", "status")
			if not str(args.get(key) or "").strip() and writable.get(key)
		),
		"fields_not_on_this_site": absent,
		"linkage": linkage,
		"note": (
			"This is an identity and assignment record only. Payroll, tax and banking fields are "
			"refused by name — they have forms, approvals and retention rules this app knows "
			"nothing about."
			+ (
				""
				if not absent
				else f" This site's Employee doctype has no {', '.join(absent)}, so "
				"those values were NOT written."
			)
		),
		"next_step": (
			"generate_mobile_login_qr hands the credential over."
			if linkage and linkage.get("farm_ops_ready")
			else "create_mobile_user gives this person a scoped login, then "
			"link_employee_to_user connects the two."
		),
	}
	return ToolResult(
		data=data,
		summary=f"created Employee {name} — {employee_name} at {company}"
		+ (f", linked to {user_id}" if user_id else ""),
		docstatus_delta="none → 0 (Employee created)",
	)


def _refuse_a_second_record(employee_name: str, company: str, user_id: str, args: dict) -> None:
	"""One person, one Employee. See `newhire.py`, which has argued this since v0.17.1.

	Matched on name AND company, because the same person genuinely can be employed
	by two entities on a multi-entity site and that is a second record on purpose.
	`allow_duplicate_name=true` covers the real collision — two people called Ana
	Ramos — and names the existing record so the caller can tell which case they
	are in before they answer.
	"""
	if as_bool(args, "allow_duplicate_name", False):
		return
	existing = frappe.db.get_all(
		EMPLOYEE,
		filters={"employee_name": employee_name, "company": company},
		fields=["name", "status", "user_id"] if compat.has_field(EMPLOYEE, "user_id") else ["name", "status"],
		limit=5,
	) or []
	if not existing:
		return
	row = existing[0]
	raise ToolError(
		f"{employee_name} already has an Employee record at {company}: {row['name']} "
		f"(status {row.get('status') or 'unknown'}"
		+ (f", login {row['user_id']}" if row.get("user_id") else ", no login")
		+ "). Two Employee records for one person puts them on the dispatch board twice and in "
		"the payroll register once, and is far easier to make than to find. Use update_employee "
		+ (
			f"or link_employee_to_user on {row['name']}"
			if user_id
			else f"on {row['name']}"
		)
		+ ", or pass allow_duplicate_name=true if this really is a different person with the "
		"same name. Nothing was created."
	)


def _insert(payload: dict) -> str:
	"""Insert the record, and turn a mandatory-field refusal into a sentence.

	`ignore_permissions` because the gate is `require_hr_role` plus the tool's own
	switch, both of which have already run — the same contract every other
	mutating tool in this app has with `dispatch`.

	`doctype` is added HERE rather than carried in the payload, because the payload
	has been through `_supported`, which keeps only keys that are fields on this
	site's Employee — and `doctype` is not one.
	"""
	doc = frappe.get_doc({**payload, "doctype": EMPLOYEE})
	doc.flags.ignore_permissions = True
	try:
		doc.insert(ignore_permissions=True)
	except Exception as exc:
		gaps = _mandatory_gaps(payload)
		if gaps and _is_mandatory_error(exc):
			raise ToolError(_mandatory_message(gaps)) from None
		raise
	return doc.name


def _mandatory_message(gaps: list) -> str:
	"""What to say when this site wants a field the call did not supply."""
	return (
		f"this site's Frappe HR marks {', '.join(gaps)} mandatory on Employee, and the call did "
		"not supply " + ("it" if len(gaps) == 1 else "them") + ". Which fields are required is an "
		"operator's decision on the doctype, not this app's: pass "
		+ ("the value" if len(gaps) == 1 else "the values")
		+ ", or clear the `reqd` flag in the Desk. Nothing was created."
	)


def _is_mandatory_error(exc: Exception) -> bool:
	"""Whether a failed insert failed for want of a required field.

	Matched on the exception's NAME rather than by importing `frappe.MandatoryError`,
	which does not exist on every version this app supports, and on the message as
	a fallback for a controller that raises a plain ValidationError instead.
	"""
	if type(exc).__name__ in ("MandatoryError", "MandatoryFieldError"):
		return True
	return "mandatory" in str(exc).lower()


# ── 2. update_employee ──────────────────────────────────────────────────────
def update_employee(args: dict) -> ToolResult:
	"""Change the identity and assignment fields on an Employee that already exists."""
	compat.require_doctype(EMPLOYEE, "It comes with the Frappe HR (hrms) app.")
	_reject_unknown(
		args,
		WRITABLE,
		reserved=("name", "employee", "allow_unenrolled_user", "replace_user", "allow_duplicate_name"),
	)
	actor = require_hr_role()

	name = resolve_employee(as_str(args, "name") or as_str(args, "employee", required=True))
	current = frappe.db.get_value(
		EMPLOYEE,
		name,
		compat.existing_fields(EMPLOYEE, ["employee_name", "company", "user_id", *WRITABLE]),
		as_dict=True,
	) or {}
	require_company_scope(actor, str(current.get("company") or ""))

	wanted = {key: args[key] for key in WRITABLE if key in args}
	if not wanted:
		raise ToolError(
			"update_employee was given nothing to change. Pass at least one of: "
			f"{', '.join(WRITABLE)}. Nothing was changed."
		)

	if "company" in wanted:
		wanted["company"] = require_company_scope(
			actor, resolve_company(as_str(wanted, "company"), required=True)
		)
	if "employee_name" in wanted and " " not in str(wanted["employee_name"] or "").strip():
		raise ToolError(
			f"employee_name is {wanted['employee_name']!r}, and a record carrying one word names "
			"nobody findable. Nothing was changed."
		)

	linkage = None
	values = {}
	for key, raw in wanted.items():
		if key == "company":
			values[key] = wanted[key]
			continue
		value = _clean(key, raw, key)
		if key == "user_id" and value:
			_require_free_user(value, employee=name)
			existing_login = str(current.get("user_id") or "")
			if existing_login and existing_login != value and not as_bool(args, "replace_user", False):
				raise ToolError(
					f"{name} is already linked to {existing_login}. Re-pointing an Employee at a "
					"different login moves that person's whole task history to another account, "
					"which is a decision rather than a retry — pass replace_user=true to say so. "
					"Nothing was changed."
				)
			linkage = _require_enrolled(value, args)
		values[key] = value

	writable, absent = _supported(values)
	changed, unchanged = [], []
	for key, value in writable.items():
		before = current.get(key)
		if str(before or "") == str(value or ""):
			unchanged.append(key)
			continue
		changed.append({"field": key, "from": before, "to": value or None})

	if changed:
		doc = frappe.get_doc(EMPLOYEE, name)
		for entry in changed:
			doc.set(entry["field"], entry["to"])
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)

	data = {
		"employee": name,
		"employee_name": current.get("employee_name"),
		"company": writable.get("company") or current.get("company"),
		"actor": actor,
		"changed": changed,
		"unchanged": sorted(unchanged),
		"fields_not_on_this_site": absent,
		"linkage": linkage,
		"note": (
			"Only the fourteen identity and assignment fields are writable here. Payroll, tax "
			"and banking fields are refused by name and belong in the Desk, where the HR "
			"module's own validation runs."
		),
	}
	return ToolResult(
		data=data,
		summary=(
			f"updated Employee {name}: "
			+ (", ".join(entry["field"] for entry in changed) if changed else "nothing to change")
		),
		docstatus_delta="0 → 0 (Employee amended)" if changed else "none",
	)


# ── 3. link_employee_to_user ────────────────────────────────────────────────
def link_employee_to_user(args: dict) -> ToolResult:
	"""Point one Employee at one login, and report whether the phone will now work."""
	compat.require_doctype(EMPLOYEE, "It comes with the Frappe HR (hrms) app.")
	actor = require_hr_role()

	if not compat.has_field(EMPLOYEE, "user_id"):
		raise ToolError(
			"this site's Employee doctype has no `user_id` field, so there is nothing to link a "
			"login to. That field is how every Farm Ops method resolves a phone to a person; "
			"without it the mobile API cannot scope anything. Nothing was changed."
		)

	employee = resolve_employee(
		as_str(args, "employee_name") or as_str(args, "employee") or as_str(args, "name", required=True)
	)
	user = as_str(args, "user_id", required=True).strip().lower()
	if not frappe.db.exists(USER, user):
		raise ToolError(
			f"no User {user!r} on this site. A login has to exist before an Employee can point at "
			"it — create_mobile_user makes one with a role, entity scoping and a credential. "
			"Nothing was changed."
		)

	row = frappe.db.get_value(
		EMPLOYEE, employee, ["employee_name", "company", "user_id", "status"], as_dict=True
	) or {}
	require_company_scope(actor, str(row.get("company") or ""))

	already = str(row.get("user_id") or "")
	if already == user:
		# IDEMPOTENT, AND SAYING SO. An orchestrator re-run must not look like a
		# failure, and a caller who asked for a state that already holds got what
		# they asked for.
		state = linkage_state(user)
		return ToolResult(
			data={
				"employee": employee,
				"employee_name": row.get("employee_name"),
				"company": row.get("company"),
				"user_id": user,
				"action": "already linked",
				"actor": actor,
				"linkage": state,
				"note": _readiness_note(state, row),
			},
			summary=f"{employee} was already linked to {user}",
			docstatus_delta="none",
		)

	_require_free_user(user, employee=employee)
	if already and not as_bool(args, "replace_user", False):
		raise ToolError(
			f"{employee} ({row.get('employee_name')}) is already linked to {already}. Re-pointing "
			"an Employee at a different login moves that person's whole task history to another "
			"account, which is a decision rather than a retry — pass replace_user=true to say so. "
			"Nothing was changed."
		)
	state = _require_enrolled(user, args)

	doc = frappe.get_doc(EMPLOYEE, employee)
	doc.user_id = user
	doc.flags.ignore_permissions = True
	doc.save(ignore_permissions=True)

	state = linkage_state(user)
	return ToolResult(
		data={
			"employee": employee,
			"employee_name": row.get("employee_name"),
			"company": row.get("company"),
			"user_id": user,
			"action": "relinked" if already else "linked",
			"previous_user_id": already or None,
			"actor": actor,
			"linkage": state,
			"note": _readiness_note(state, row),
		},
		summary=f"linked Employee {employee} ({row.get('employee_name')}) to {user}",
		docstatus_delta="0 → 0 (Employee amended)",
	)


def _readiness_note(state: dict, row: dict) -> str:
	"""Whether the phone will actually get a task list now, and what is missing if not.

	THIS IS THE SENTENCE THE RELEASE EXISTS FOR. "The link was written" is not the
	fact anybody wanted; "list_my_tasks will now answer for this account" is.
	"""
	if str(row.get("status") or "Active") != "Active":
		return (
			f"LINKED, BUT THIS EMPLOYEE IS {row.get('status')}. The mobile methods answer for "
			"Active employees; set status=Active with update_employee when they start."
		)
	if state.get("farm_ops_ready"):
		return (
			"list_my_tasks and the other ten mobile methods will now answer for this account: it "
			"holds "
			+ ", ".join(state.get("farm_ops_roles") or [])
			+ ", its Mobile Access Grant is Active, and the Employee it resolves to is this one."
		)
	if not state.get("farm_ops_roles"):
		return (
			"LINKED, BUT THE MOBILE METHODS WILL STILL REFUSE THIS ACCOUNT: it holds none of "
			+ ", ".join(sorted(_farm_ops_roles()))
			+ ". create_mobile_user grants one."
		)
	return (
		"LINKED, BUT THE MOBILE METHODS WILL STILL REFUSE THIS ACCOUNT: its Mobile Access Grant "
		f"is {state.get('grant_state') or 'missing'}, and the enrolment gate wants Active. "
		"create_mobile_user (update_existing=true) enrols it."
	)
