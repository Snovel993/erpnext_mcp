# SPDX-License-Identifier: MIT
"""Entity scoping for the two doctypes Frappe's own mechanism cannot reach.

v0.17.1. THIS FILE EXISTS BECAUSE A PROMISE MADE ELSEWHERE WAS NOT TRUE OF
EVERYTHING IT CLAIMED.

`tools/mobile.py` says, and `create_mobile_user` relies on:

    one row saying `allow=Company, for_value=<name>, apply_to_all_doctypes=1`
    restricts EVERY document that links to a Company, across every doctype

That is exactly right, and it is the reason this app writes no scoping code of
its own for Farm Task, Housing Inspection, Water Test, Detector Test,
Certification, Regulatory Filing, Compliance Policy, Audit Event or any of the
other registers: every one of them carries a `company` or `owning_entity` field
that is a **Link to Company**, so Frappe restricts them at the framework level
in every list, report, `/api/resource` call and Desk view.

**The clause doing the work is "that links to a Company."** Two doctypes this
app created do not, and were therefore never restricted by anything:

  * **Housing Assignment** — who sleeps in which cabin. It links to `Housing
    Unit` and to `Parcel`, both of which carry `owning_entity`, but not to
    Company itself. `roles.py` grants it READ to Field Worker, Foreman,
    Compliance Officer and Family Member, and FULL to Farm Manager. So a field
    worker scoped to one entity could list every camp bed assignment on the
    site — names, units, wage-deduction status — across every other entity.
    That is labour-camp personnel data and it is the exact failure the v0.17.0
    scoping release existed to prevent.

  * **Family** — the family-office member register. It links to `Related Party`,
    which carries `company`. Granted READ to Family Member, so one family's
    members could read another's.

Neither was a coding mistake anybody made; both are the silent half of a rule
that is true of thirty doctypes and false of two, which is the most expensive
shape a security assumption can have.

────────────────────────────────────────────────────────────────────────────
WHY THIS DOES NOT BREAK THE PROMISE `hooks.py` MAKES
────────────────────────────────────────────────────────────────────────────

`hooks.py` says installing this app "cannot change the behaviour of anything
already on the site", and `test_hooks.py` used to FORBID the two hook keys below
on the grounds that "this app changes nobody's visibility". The invariant worth
keeping is the one about OTHER PEOPLE'S doctypes; the blanket ban was a proxy
for it, and the proxy was wrong here.

So the ban is now a narrower and stronger rule, asserted by
`test_permissions.py`: **every doctype named in these hooks must be one this app
created.** Restricting a doctype that would not exist without this app cannot
change how a site behaved before it was installed, because before it was
installed there was nothing there to behave.

────────────────────────────────────────────────────────────────────────────
AN EMPTY PERMISSION LIST STILL MEANS UNRESTRICTED, AND THAT IS DELIBERATE HERE
────────────────────────────────────────────────────────────────────────────

A user with no Company User Permission gets `""` — no condition — exactly as
every other doctype on the site treats them. That is Frappe's rule, it is what
makes Administrator and an operator's own login work, and departing from it
*here* would produce a site where one doctype is invisible to an admin for
reasons nothing else on the page shares.

It is ALSO the opposite of what `api/guard.accessible_companies` does, which
refuses a caller with no entities — and the difference is the point. The mobile
API is a specific endpoint on the open internet with a closed list of callers,
where fail-closed is affordable and correct. A `permission_query_conditions`
hook applies to EVERY reader of the doctype forever, including migrations,
reports and the operator; fail-closed there would be a footgun with a very long
barrel. The two layers disagree on purpose, and the strict one is the one facing
outward.
"""

from __future__ import annotations

import frappe

from . import roles
from .compat import doctype_exists

HOUSING_ASSIGNMENT = "Housing Assignment"
HOUSING_UNIT = "Housing Unit"
PARCEL = "Parcel"
FAMILY = "Family"
RELATED_PARTY = "Related Party"

#: How each scoped doctype reaches a Company: (field on this doctype, the
#: doctype it points at, the Company field over there).
#:
#: Ordered. The FIRST link that is filled in decides, rather than any of them
#: being enough — see `_condition`. A Housing Assignment whose `unit` belongs to
#: one entity and whose `parcel` belongs to another is a data error, and the
#: reading that shows it to both entities is the wrong one.
ROUTES = {
	HOUSING_ASSIGNMENT: (("unit", HOUSING_UNIT, "owning_entity"), ("parcel", PARCEL, "owning_entity")),
	FAMILY: (("related_party", RELATED_PARTY, "company"),),
}


def companies_for(user: str) -> list:
	"""The Companies this user's User Permissions allow. Empty means every one."""
	return roles.companies_for(user or frappe.session.user)


# ── the query conditions ────────────────────────────────────────────────────
def housing_assignment_query(user: str | None = None) -> str:
	return _condition(HOUSING_ASSIGNMENT, user)


def family_query(user: str | None = None) -> str:
	return _condition(FAMILY, user)


def _condition(doctype: str, user: str | None = None) -> str:
	"""SQL restricting `doctype` to the caller's entities, or "" for unrestricted.

	NEVER RAISES. A `permission_query_conditions` hook that throws does not fail
	one query, it fails every list view of that doctype for everybody, forever,
	including the Desk page an operator would use to fix it. On any doubt this
	returns `""`, which is the behaviour of the site before this file existed —
	a scoping that silently stops working is bad, and a site nobody can open is
	worse.

	A row whose linking field is BLANK is visible to everyone. That matches
	`api/guard.scoped`, which keeps a row with no company for the same reason: a
	Housing Assignment with no unit is a data problem, not another entity's
	secret, and hiding it makes it invisible rather than fixed.
	"""
	try:
		companies = companies_for(user)
		if not companies:
			return ""
		routes = [route for route in ROUTES.get(doctype, ()) if doctype_exists(route[1])]
		if not routes:
			return ""
		allowed = ", ".join(frappe.db.escape(name) for name in companies)
		clauses = []
		for index, (field, target, company_field) in enumerate(routes):
			column = f"`tab{doctype}`.`{field}`"
			inner = f"SELECT `name` FROM `tab{target}` WHERE `{company_field}` IN ({allowed})"
			if index == 0:
				# The primary link decides whenever it is set.
				clauses.append(f"({column} IS NOT NULL AND {column} != '' AND {column} IN ({inner}))")
				primary_blank = f"({column} IS NULL OR {column} = '')"
			else:
				# A fallback link is consulted ONLY where the primary is blank,
				# so a mismatched pair never widens access to both entities.
				clauses.append(f"({primary_blank} AND {column} IN ({inner}))")
		blank_everything = " AND ".join(
			f"(`tab{doctype}`.`{field}` IS NULL OR `tab{doctype}`.`{field}` = '')"
			for field, _target, _company in routes
		)
		clauses.append(f"({blank_everything})")
		return "(" + " OR ".join(clauses) + ")"
	except Exception:  # pragma: no cover - see the docstring; never fail closed here
		try:
			frappe.log_error(
				title=f"erpnext_mcp: could not build a permission query for {doctype}",
				message=frappe.get_traceback(),
			)
		except Exception:
			pass
		return ""


# ── the single-document check ───────────────────────────────────────────────
def housing_assignment_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _allowed(HOUSING_ASSIGNMENT, doc, user)


def family_has_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	return _allowed(FAMILY, doc, user)


def _allowed(doctype: str, doc, user: str | None = None) -> bool:
	"""Whether one document is inside the caller's entities.

	The query condition covers lists; this covers `frappe.get_doc` and the form
	view, which do not go through it. Both are needed — a doctype filtered out of
	a list but readable at `/app/housing-assignment/HA-0007` is not scoped, it is
	merely tidy.

	Returns True on any doubt, for the same reason `_condition` returns "": this
	runs on every read of the doctype, and a check that fails closed on an
	unexpected shape locks an operator out of their own record.
	"""
	try:
		companies = companies_for(user)
		if not companies:
			return True
		company = document_company(doctype, doc)
		if not company:
			return True
		return company in companies
	except Exception:  # pragma: no cover
		return True


def document_company(doctype: str, doc) -> str:
	"""Which Company a document belongs to, followed through its links.

	Returns "" when nothing says — which every caller reads as "do not restrict".
	"""
	for field, target, company_field in ROUTES.get(doctype, ()):
		value = _get(doc, field)
		if not value or not doctype_exists(target):
			continue
		owner = frappe.db.get_value(target, value, company_field)
		if owner:
			return str(owner)
	return ""


def _get(doc, field: str):
	if isinstance(doc, dict):
		return str(doc.get(field) or "").strip()
	value = getattr(doc, field, None)
	if value is None and hasattr(doc, "get"):
		value = doc.get(field)
	return str(value or "").strip()


def scoped_doctypes() -> tuple:
	"""Every doctype these hooks restrict. Read by the test that proves they are
	all doctypes this app created."""
	return tuple(sorted(ROUTES))
