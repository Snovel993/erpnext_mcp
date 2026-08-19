# SPDX-License-Identifier: MIT
"""The five organisational masters an Employee links to, and their CRUD.

Designation, Department, Branch, Employment Type and Employee Grade. Every one
of them ships with Frappe HR, every one of them is a Link target on `Employee`,
and until this module none of them could be created from here — so
`create_employee` refused a designation nobody could add, listing the site's own
records as the answer to a question the caller had no way of answering.

That refusal is right and STAYS EXACTLY AS IT IS. `tools/employee._clean` asks
this site's meta what each Link points at and refuses a value naming no record;
these tools create the records it reads. The check and the register are two
halves of one thing and neither is loosened for the other.

────────────────────────────────────────────────────────────────────────────
THE DOCNAME IS NOT THE NAME FIELD, AND THIS FILE NEVER ASSUMES IT IS
────────────────────────────────────────────────────────────────────────────

Frappe names these five in three different ways:

  Designation, Branch, Employment Type   `field:` — the docname IS the name
                                         somebody typed.
  Employee Grade                         Prompt — the docname is whatever the
                                         caller supplies, with no column of
                                         its own behind it.
  Department                             a controller that appends the
                                         company's abbreviation, so "Harvest"
                                         at Orchard Meadow becomes
                                         "Harvest - OML".

So a caller who created "Harvest" and then asked for "Harvest" would be told it
does not exist, on one of the five and only on a site with more than a default
company. `_resolve` therefore tries the docname AND the name column, on all
five, always — and `_existing` does the same before an insert, which is what
makes these tools idempotent on the register whose docname they cannot predict.

That is also why nothing here computes a docname. `doc.insert()` lets the
doctype's own controller name the record and the result reports what it got,
because a name this app derived would be a second implementation of a rule
Frappe already owns and would drift from it the first time a site customised
`Department.autoname`.

────────────────────────────────────────────────────────────────────────────
WHAT UPDATE MEANS ON A MASTER WHOSE ONLY CONTENT IS ITS NAME
────────────────────────────────────────────────────────────────────────────

Branch and Employment Type carry one column each: the name. There is nothing on
them to edit, so an `update_` tool for them that only set fields would be a tool
that can never do anything. What an operator actually needs on those two is the
correction — "Mill Creak" was typed at six in the morning and every Employee
hired since points at it.

So `new_name` is on all five, and it goes through `frappe.rename_doc`, which
repoints every Link on the site that named the old docname. A rename is refused
where the target already exists, because merging two designations is a decision
about people rather than a spelling fix, and Frappe's `merge` flag would do it
silently.

PAY FIELDS ARE REFUSED BY NAME ON Employee Grade, the same way
`tools/employee.SENSITIVE_FIELDS` refuses them on the Employee. A grade's
`default_base_pay` and `default_salary_structure` set what a whole band of
people is paid; that has a form, an approval and a retention rule this app
knows nothing about, and one insert here would reach further than one Employee.

────────────────────────────────────────────────────────────────────────────

WHO MAY WRITE. `employee.require_hr_role` — System Manager, HR Manager, HR User
or Farm Manager — because these ARE the personnel register: a designation is
what a Position Wage Default is keyed on, so anybody who can add one can create
the row a wage rate hangs off. The reads are not role-gated, for the reason
every read in this app is not: a hiring wizard has to be able to offer the list
it is about to refuse a value against.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe

from .. import compat
from ..args import as_bool, as_limit, as_str, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from . import employee as employee_tools

DESIGNATION = "Designation"
DEPARTMENT = "Department"
BRANCH = "Branch"
EMPLOYMENT_TYPE = "Employment Type"
EMPLOYEE_GRADE = "Employee Grade"
EMPLOYEE = "Employee"

#: What every refusal about a missing doctype says next. All five ship with the
#: same app, so all five say the same sentence.
_HINT = "It comes with the Frappe HR (hrms) app."

#: Most rows any list here returns. These are registers a farm reads on a form —
#: a site with more than this many designations has a data problem, and a cap
#: that truncates silently is a cap that lies, so `truncated` is reported.
LIST_CAP = 500

#: The pay columns Frappe HR puts on Employee Grade, refused by name rather than
#: by omission. See the module docstring: a grade's default pay reaches every
#: person on it, and the sentence a caller gets for one of these is different
#: from the sentence they get for a typo.
GRADE_PAY_FIELDS = ("default_base_pay", "default_salary_structure", "default_leave_policy")


@dataclass(frozen=True)
class OrgMaster:
	"""One of the five: what it is called, and what may be written on it."""

	doctype: str
	#: The argument a caller passes to name an existing record.
	argument: str
	#: The Data column Frappe builds the docname from, where there is one.
	#: Empty for a Prompt-named doctype, where the docname stands alone.
	name_field: str
	#: The argument that carries the name on a create. Kept separate from
	#: `name_field` because a caller should not have to know that a Branch's
	#: column is called `branch` and a Designation's is `designation_name`.
	create_argument: str
	#: Columns beyond the name that `update_` may set, in the order the refusal
	#: lists them. Empty means this master's only content is its name.
	writable: tuple = ()
	#: Extra columns worth reading back on a list.
	readable: tuple = ()
	#: True for the one master that belongs to a Company.
	company_scoped: bool = False
	#: The plural the tools and the messages use.
	plural: str = ""


MASTERS = (
	OrgMaster(
		doctype=DESIGNATION,
		argument="designation",
		name_field="designation_name",
		create_argument="designation_name",
		writable=("description",),
		readable=("description",),
		plural="designations",
	),
	OrgMaster(
		doctype=DEPARTMENT,
		argument="department",
		name_field="department_name",
		create_argument="department_name",
		# `is_group` and `parent_department` are the tree, and the tree is real:
		# Frappe HR's Department is a NestedSet and a leave approver walks it.
		writable=("company", "parent_department", "is_group", "disabled"),
		readable=("company", "parent_department", "is_group", "disabled"),
		company_scoped=True,
		plural="departments",
	),
	OrgMaster(
		doctype=BRANCH,
		argument="branch",
		name_field="branch",
		create_argument="branch",
		plural="branches",
	),
	OrgMaster(
		doctype=EMPLOYMENT_TYPE,
		argument="employment_type",
		name_field="employee_type_name",
		create_argument="employment_type_name",
		plural="employment_types",
	),
	OrgMaster(
		doctype=EMPLOYEE_GRADE,
		argument="employee_grade",
		# Prompt-named on a stock site: there is no column behind the docname.
		# `_name_column` still asks this site's meta before it believes that, so
		# an operator who added one is not written around.
		name_field="",
		create_argument="employee_grade_name",
		plural="employee_grades",
	),
)

BY_DOCTYPE = {master.doctype: master for master in MASTERS}

#: The Employee column each master is counted through on a list. Named rather
#: than derived from the doctype, because `Employee.grade` is not
#: `Employee.employee_grade` and a headcount against a column that does not
#: exist would report zero for every row — which reads as "nobody holds this"
#: rather than as "this app asked the wrong question".
EMPLOYEE_COLUMN = {
	DESIGNATION: "designation",
	DEPARTMENT: "department",
	BRANCH: "branch",
	EMPLOYMENT_TYPE: "employment_type",
	EMPLOYEE_GRADE: "grade",
}


# ── shared helpers ──────────────────────────────────────────────────────────
def _require(master: OrgMaster) -> None:
	compat.require_doctype(master.doctype, _HINT)


def _name_column(master: OrgMaster) -> str:
	"""The column this site actually builds the docname from, or "".

	The spec's `name_field` is a claim about a stock Frappe HR install and the
	site's own meta is the fact. A doctype whose column was renamed, or an
	Employee Grade an operator gave a name column to, is answered by asking.
	"""
	if master.name_field and compat.has_field(master.doctype, master.name_field):
		return master.name_field
	return ""


def _resolve(master: OrgMaster, value: str, verb: str = "changed") -> str:
	"""The docname for one record, from its docname or from its name column.

	Both, always, on all five. See the module docstring: a Department created as
	"Harvest" is named "Harvest - OML" and a caller who has to know that is a
	caller this app has failed.

	Ambiguity is reported rather than resolved. Two Departments called "Harvest"
	at two companies is an ordinary shape, and picking one silently would edit
	the wrong entity's org chart.
	"""
	value = (value or "").strip()
	if not value:
		raise ToolError(f"{master.argument} is required. Nothing was {verb}.")
	if frappe.db.exists(master.doctype, value):
		return value

	column = _name_column(master)
	if column:
		matches = frappe.db.get_all(master.doctype, filters={column: value}, pluck="name", limit=5) or []
		if len(matches) == 1:
			return str(matches[0])
		if len(matches) > 1:
			raise ToolError(
				f"{len(matches)} {master.doctype} records are called {value!r}: "
				f"{', '.join(str(name) for name in matches)}. Pass the docname you mean — "
				f"list_{master.plural} has every one on this site. Nothing was {verb}."
			)
	raise ToolError(
		f"no {master.doctype} matching {value!r} on this site (tried the docname and "
		f"{column or 'the name'}). list_{master.plural} has every one. Nothing was {verb}."
	)


def _existing(master: OrgMaster, value: str, company: str = "") -> str:
	"""The docname of a record already carrying this name, or "".

	The idempotence check every `create_` here runs, and it is deliberately NOT
	`frappe.db.exists(doctype, value)`: on the two masters whose docname is built
	rather than typed that question is false for a record that exists, and a
	create that believed it would insert a second one.
	"""
	value = (value or "").strip()
	if not value:
		return ""
	column = _name_column(master)
	if column:
		filters: dict = {column: value}
		if company and master.company_scoped and compat.has_field(master.doctype, "company"):
			filters["company"] = company
		match = frappe.db.get_value(master.doctype, filters, "name")
		if match:
			return str(match)
	if frappe.db.exists(master.doctype, value):
		return value
	return ""


def _headcount(master: OrgMaster, names: list) -> dict:
	"""`{docname: employees}` for a batch. Never raises.

	A register with no headcount on it is a list somebody has to cross-reference
	by hand before they can retire a row, and retiring a designation forty people
	hold is the mistake this column exists to prevent.
	"""
	column = EMPLOYEE_COLUMN.get(master.doctype, "")
	if not names or not column:
		return {}
	if not compat.doctype_exists(EMPLOYEE) or not compat.has_field(EMPLOYEE, column):
		return {}
	counts: dict = {}
	try:
		rows = frappe.db.get_all(
			EMPLOYEE,
			filters={column: ("in", names), "status": "Active"},
			fields=["name", column],
			limit=LIST_CAP * 20,
		)
	except Exception:  # pragma: no cover - a site shaping Employee differently
		return {}
	for row in rows or []:
		key = str(dict(row).get(column) or "")
		if key:
			counts[key] = counts.get(key, 0) + 1
	return counts


def _describe(master: OrgMaster, row: dict, counts: dict) -> dict:
	"""One register row, with its name column and its live headcount."""
	name = str(row.get("name") or "")
	column = _name_column(master)
	out = {
		"name": name,
		# THE TYPED NAME AND THE DOCNAME, BOTH, ALWAYS. They are the same string
		# on three of the five and different on the other two, and a caller that
		# displays one and passes the other is the bug this pair prevents.
		master.name_field or "label": str(row.get(column) or name) if column else name,
		"active_employees": int(counts.get(name, 0)),
	}
	for field in master.readable:
		if field in row:
			value = row.get(field)
			out[field] = compat.checked(value) if field in ("is_group", "disabled") else (value or None)
	return out


def _rows(master: OrgMaster, filters: dict, limit: int) -> list[dict]:
	candidates = ["name", *([_name_column(master)] if _name_column(master) else []), *master.readable]
	rows = frappe.db.get_all(
		master.doctype,
		filters=filters,
		fields=compat.existing_fields(master.doctype, candidates),
		order_by="name asc",
		limit=limit,
	)
	return [dict(row) for row in rows or []]


def _list(master: OrgMaster, args: dict) -> ToolResult:
	"""The shared body of the five reads."""
	_require(master)
	limit = min(as_limit(args), LIST_CAP)

	filters: dict = {}
	company = ""
	if master.company_scoped and compat.has_field(master.doctype, "company"):
		company = resolve_company(as_str(args, "company")) or ""
		if company:
			filters["company"] = company

	rows = _rows(master, filters, limit)
	names = [str(row.get("name") or "") for row in rows]
	counts = _headcount(master, names)
	described = [_describe(master, row, counts) for row in rows]

	if as_bool(args, "in_use_only", False):
		described = [entry for entry in described if entry["active_employees"]]

	unused = [entry["name"] for entry in described if not entry["active_employees"]]
	data = {
		"count": len(described),
		master.plural: described,
		"unused": unused,
		"truncated": len(rows) >= limit,
		"note": (
			f"`active_employees` counts Active Employees pointing at each row through "
			f"`Employee.{EMPLOYEE_COLUMN.get(master.doctype, '')}`. A row in `unused` is safe to "
			"rename or retire; one with a headcount is not, because every one of those records "
			"names it."
		),
	}
	if company:
		data["company"] = company
	return ToolResult(
		data=data,
		summary=f"{len(described)} {master.doctype} record(s), {len(unused)} with nobody on them",
	)


def _create(master: OrgMaster, args: dict) -> ToolResult:
	"""The shared body of the five creates."""
	_require(master)
	actor = employee_tools.require_hr_role()
	name = as_str(args, master.create_argument, required=True).strip()
	if not name:
		raise ToolError(f"{master.create_argument} is required. Nothing was created.")

	company = ""
	if master.company_scoped and compat.has_field(master.doctype, "company"):
		company = resolve_company(as_str(args, "company"), required=True) or ""
		employee_tools.require_company_scope(actor, company)

	already = _existing(master, name, company)
	if already:
		raise ToolError(
			f"there is already a {master.doctype} called {name!r} on this site"
			+ (f" for {company}" if company else "")
			+ f" — it is {already!r}. Two rows with the same name would split the people who hold "
			f"it across both, and no report would add them back together. Use update_"
			f"{master.argument} to correct the one that exists. Nothing was created."
		)

	doc = frappe.new_doc(master.doctype)
	column = _name_column(master)
	if column:
		doc.set(column, name)
	else:
		# Prompt-named: the docname IS the argument. BOTH spellings are set on
		# purpose — Frappe's `Document.insert` copies `__newname` onto `name`
		# before `set_new_name` runs, and `set_new_name` leaves an already-set
		# `name` alone for a Prompt doctype. Setting one of the two would work on
		# whichever path that version takes and silently serial-name on the other.
		doc.name = name
		doc.set("__newname", name)
	if company:
		doc.company = company

	written = _apply(master, doc, args, creating=True)
	doc.insert(ignore_permissions=True)

	counts = _headcount(master, [doc.name])
	described = _describe(master, dict(doc.as_dict()), counts)
	return ToolResult(
		data={
			**described,
			"actor": actor,
			"fields_set": sorted(written),
			"note": (
				f"The docname is {doc.name!r}. Frappe's own controller named it — this app does "
				"not compute one — so pass that, or the name you typed, to every other tool: "
				f"both resolve. It is now a value `create_employee` and `update_employee` accept "
				f"for {EMPLOYEE_COLUMN.get(master.doctype, master.argument)}."
			),
		},
		summary=f"created {master.doctype} {doc.name}",
		docstatus_delta="none → 0 (created)",
	)


def _apply(master: OrgMaster, doc, args: dict, creating: bool) -> list:
	"""Set the writable columns this site actually has. Returns what was written."""
	written = []
	for field in master.writable:
		if field == "company":
			# Handled by the caller: it is scope-checked before anything is built.
			continue
		if field not in args:
			continue
		if not compat.has_field(master.doctype, field):
			continue
		raw = args.get(field)
		if field in ("is_group", "disabled"):
			value = 1 if as_bool(args, field, False) else 0
		elif field == "parent_department":
			value = _resolve(master, str(raw or ""), "created" if creating else "changed") if raw else ""
			if value and not creating and value == doc.name:
				raise ToolError(f"{doc.name} cannot be its own parent_department. Nothing was changed.")
		else:
			value = str(raw or "").strip()
		doc.set(field, value)
		written.append(field)
	return written


def _update(master: OrgMaster, args: dict) -> ToolResult:
	"""The shared body of the five updates, renames included."""
	_require(master)
	actor = employee_tools.require_hr_role()
	name = _resolve(master, as_str(args, master.argument, required=True))

	if master.doctype == EMPLOYEE_GRADE:
		_refuse_grade_pay(args)

	company = ""
	if master.company_scoped and "company" in args and compat.has_field(master.doctype, "company"):
		company = resolve_company(as_str(args, "company"), required=True) or ""
		employee_tools.require_company_scope(actor, company)

	doc = frappe.get_doc(master.doctype, name)
	before = {field: doc.get(field) for field in master.writable}
	written = _apply(master, doc, args, creating=False)
	if company:
		doc.company = company
		written.append("company")

	changed = [
		{"field": field, "from": before.get(field), "to": doc.get(field)}
		for field in written
		if str(before.get(field) or "") != str(doc.get(field) or "")
	]
	unchanged = sorted(set(written) - {entry["field"] for entry in changed})

	if changed:
		doc.flags.ignore_permissions = True
		doc.save(ignore_permissions=True)

	renamed = _rename(master, doc.name, as_str(args, "new_name"))
	final = renamed or doc.name

	if not changed and not renamed:
		if not written and not as_str(args, "new_name"):
			raise ToolError(
				f"update_{master.argument} was given nothing to change. "
				+ (
					f"Pass at least one of: {', '.join(master.writable)}, or new_name. "
					if master.writable
					else (
						f"A {master.doctype} carries nothing but its name on a stock site, so "
						"new_name is the only thing there is to change here. "
					)
				)
				+ "Nothing was changed."
			)

	counts = _headcount(master, [final])
	described = _describe(master, dict(frappe.get_doc(master.doctype, final).as_dict()), counts)
	return ToolResult(
		data={
			**described,
			"actor": actor,
			"changed": changed,
			"unchanged": unchanged,
			"renamed_from": name if renamed else None,
			"note": (
				(
					f"Renaming moved the docname AND every Link that pointed at it — "
					f"{described['active_employees']} Active Employee(s) now read {final!r} "
					"without anybody editing them. Anything that stored the old string OUTSIDE "
					"a Link field (a report filter, a saved view) still says the old one."
				)
				if renamed
				else "The docname did not move, so nothing that links to this record changed."
			),
		},
		summary=(
			f"updated {master.doctype} {final}"
			+ (f" (renamed from {name})" if renamed else "")
			+ (f": {', '.join(entry['field'] for entry in changed)}" if changed else "")
		),
		docstatus_delta="0 → 0 (amended)" if (changed or renamed) else "none",
	)


def _refuse_grade_pay(args: dict) -> None:
	"""Frappe HR's pay columns on Employee Grade, refused with their own sentence."""
	for field in GRADE_PAY_FIELDS:
		if field in args:
			raise ToolError(
				f"{field!r} sets what an entire BAND of people is paid, and this app does not "
				"write pay. A default base pay, a salary structure and a leave policy each have "
				"a form, an approval and a retention rule around them that this app knows "
				"nothing about — and unlike a field on one Employee, one value here reaches "
				"everybody on the grade. Set it in the Desk, where the HR module's own checks "
				"run. Nothing was changed."
			)


def _rename(master: OrgMaster, name: str, new_name: str) -> str:
	"""Move a docname, or "" where none was asked for.

	REFUSED WHERE THE TARGET EXISTS. Frappe's `rename_doc` takes a `merge` flag
	that folds one record into another and repoints everything at the survivor;
	that is a decision about which of two designations forty people actually
	hold, not a spelling fix, and this tool must not make it by accident.
	"""
	new_name = (new_name or "").strip()
	if not new_name or new_name == name:
		return ""
	if _existing(master, new_name) or frappe.db.exists(master.doctype, new_name):
		raise ToolError(
			f"a {master.doctype} called {new_name!r} already exists. Renaming {name!r} onto it "
			"would MERGE two registers into one, which is a decision about which of them the "
			"people on both actually hold — not a spelling correction. Nothing was renamed; any "
			"other field this call changed was saved."
		)
	moved = frappe.rename_doc(master.doctype, name, new_name, force=True)
	return str(moved or new_name)


# ── Designation ─────────────────────────────────────────────────────────────
def create_designation(args: dict) -> ToolResult:
	"""Add one job title to the register `create_employee` refuses against."""
	return _create(BY_DOCTYPE[DESIGNATION], args)


def list_designations(args: dict) -> ToolResult:
	"""Every job title on this site, with how many people hold each."""
	return _list(BY_DOCTYPE[DESIGNATION], args)


def update_designation(args: dict) -> ToolResult:
	"""Correct one job title's description, or its spelling."""
	return _update(BY_DOCTYPE[DESIGNATION], args)


# ── Department ──────────────────────────────────────────────────────────────
def create_department(args: dict) -> ToolResult:
	"""Add one department to a company's org chart."""
	return _create(BY_DOCTYPE[DEPARTMENT], args)


def list_departments(args: dict) -> ToolResult:
	"""Every department, optionally for one company, with its headcount."""
	return _list(BY_DOCTYPE[DEPARTMENT], args)


def update_department(args: dict) -> ToolResult:
	"""Move a department in the tree, disable it, or correct its spelling."""
	return _update(BY_DOCTYPE[DEPARTMENT], args)


# ── Branch ──────────────────────────────────────────────────────────────────
def create_branch(args: dict) -> ToolResult:
	"""Add one operating unit or camp."""
	return _create(BY_DOCTYPE[BRANCH], args)


def list_branches(args: dict) -> ToolResult:
	"""Every branch on this site, with how many people are posted to each."""
	return _list(BY_DOCTYPE[BRANCH], args)


def update_branch(args: dict) -> ToolResult:
	"""Correct a branch's spelling. A Branch carries nothing else."""
	return _update(BY_DOCTYPE[BRANCH], args)


# ── Employment Type ─────────────────────────────────────────────────────────
def create_employment_type(args: dict) -> ToolResult:
	"""Add one employment category — Hourly, Seasonal Worker, H-2A."""
	return _create(BY_DOCTYPE[EMPLOYMENT_TYPE], args)


def list_employment_types(args: dict) -> ToolResult:
	"""Every employment category, with how many people are on each."""
	return _list(BY_DOCTYPE[EMPLOYMENT_TYPE], args)


def update_employment_type(args: dict) -> ToolResult:
	"""Correct an employment category's spelling. It carries nothing else."""
	return _update(BY_DOCTYPE[EMPLOYMENT_TYPE], args)


# ── Employee Grade ──────────────────────────────────────────────────────────
def create_employee_grade(args: dict) -> ToolResult:
	"""Add one pay band's LABEL. The pay on it is set in the Desk."""
	_refuse_grade_pay(args)
	return _create(BY_DOCTYPE[EMPLOYEE_GRADE], args)


def list_employee_grades(args: dict) -> ToolResult:
	"""Every grade on this site, with how many people are on each."""
	return _list(BY_DOCTYPE[EMPLOYEE_GRADE], args)


def update_employee_grade(args: dict) -> ToolResult:
	"""Correct a grade's spelling. Its pay columns are refused by name."""
	return _update(BY_DOCTYPE[EMPLOYEE_GRADE], args)
