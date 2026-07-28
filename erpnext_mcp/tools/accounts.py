# SPDX-License-Identifier: MIT
"""Chart-of-accounts management. Six write tools and one planner.

WHY THE CHART GETS ITS OWN MODULE. Everything in `mutate.py` writes documents
*into* a chart of accounts. These tools change the chart itself, which is a
different kind of risk: a bad journal entry is one wrong number that a reversing
entry fixes, while a bad reparent silently changes what every balance-sheet
subtotal means, retroactively, for every period already reported.

THE SPLIT BETWEEN RENAME AND MOVE IS DELIBERATE. `update_account` will not
change a parent and `move_account` will not change a name, even though ERPNext
would happily do both in one save. Renaming is routine and reversible; moving is
neither. Keeping them apart means a bad reparent cannot happen as a side effect
of a rename, and means an operator can enable the safe one without the other.

THE RENAME QUIRK, WHICH IS THE THING TO KNOW BEFORE EDITING THIS FILE. An
Account's docname is `"<number> - <name> - <abbr>"`, built by `autoname` at
insert and never rebuilt afterwards. So:

  * `frappe.rename_doc("Account", old, new)` on its own moves the key and leaves
    `account_name` / `account_number` holding the old values. The chart then
    shows one thing and reports another, permanently, because nothing recomputes
    the name on a later save.
  * `frappe.db.set_value("Account", name, "account_name", ...)` on its own
    updates the fields and leaves the docname — and therefore every link,
    report label and GL Entry reference — reading the old text.

Both halves are required, in that order, and ERPNext ships exactly that as
`erpnext.accounts.doctype.account.account.update_account_number`, which also
propagates the change to child companies in a group structure. So this module
delegates to it and treats the hand-rolled two-step only as a fallback for a
version that predates it. Reimplementing it here would mean reimplementing the
child-company sync, and getting *that* wrong on a consolidated group is not a
failure anybody notices quickly.

ROOTS ARE NOT EDITABLE, AND THAT IS ERPNEXT'S RULE, NOT THIS APP'S. A root
account (one with no parent) fails `validate_root_details` on any save, so
`update_account`'s type and disabled paths and all of `move_account` and
`disable_account` refuse them up front with that reason, rather than letting the
caller discover it as a framework traceback.

CREATING A ROOT NEEDS `ignore_mandatory`, WHICH IS NOT AN OPINION. ERPNext's
Account marks `parent_account` as required, so inserting a top-level account —
which by definition has none — dies with
`MandatoryError: [Account, 1000 - Assets - ABC]: parent_account` before any of
this app's own checks are reached. ERPNext's own chart-of-accounts importer sets
`flags.ignore_mandatory = True` for exactly and only its root nodes
(`erpnext/accounts/doctype/account/chart_of_accounts/__init__.py`), and
`import_chart_of_accounts` does the same, on the same condition. The flag is set
per document rather than per import: a *child* insert that skipped mandatory
validation would be this app quietly disabling a check ERPNext meant to run.
"""

import frappe

from .. import charts, compat
from ..args import as_bool, as_float, as_str, resolve_account, resolve_company
from ..errors import ToolError
from ..result import ToolResult
from .read import _fiscal_years_by_company

#: Fields this module reads off an Account. Filtered through `compat` before any
#: query, because `disabled` and `account_number` are both younger than the
#: doctype and an operator on an old ERPNext should get a degraded answer rather
#: than a SQL error.
_ACCOUNT_FIELDS = (
	"name",
	"account_name",
	"account_number",
	"parent_account",
	"is_group",
	"root_type",
	"report_type",
	"account_type",
	"account_currency",
	"disabled",
	"company",
	"lft",
	"rgt",
)

#: Changing an account between a party account type and anything else once it
#: carries GL entries breaks ERPNext's party-ledger reconciliation, and the
#: framework's own message about it is opaque. Checked here so the refusal names
#: the entry count and the fix.
_PARTY_TYPES = {"Receivable", "Payable"}


# ── shared lookups ──────────────────────────────────────────────────────────
def _account(name: str, company: str = "") -> dict:
	"""One Account as a dict, resolved from a docname, number or name."""
	docname = resolve_account(name, company)
	row = frappe.db.get_value(
		"Account",
		docname,
		compat.existing_fields("Account", _ACCOUNT_FIELDS),
		as_dict=True,
	)
	if not row:  # pragma: no cover - resolve_account already proved it exists
		raise ToolError(f"no Account named {docname!r}")
	return dict(row)


def _company_abbr(company: str) -> str:
	return frappe.db.get_value("Company", company, "abbr") or ""


def _is_root(row: dict) -> bool:
	return not str(row.get("parent_account") or "").strip()


def _number_holder(company: str, account_number: str, exclude: str = "") -> str:
	"""Which account already holds this number in this company, if any."""
	number = str(account_number or "").strip()
	if not number or not compat.has_field("Account", "account_number"):
		return ""
	for name in frappe.db.get_all(
		"Account",
		filters={"company": company, "account_number": number},
		pluck="name",
		limit=5,
	):
		if name != exclude:
			return name
	return ""


def _ancestors(docname: str) -> list:
	"""Every parent up to the root, nearest first.

	Walks `parent_account` rather than comparing `lft`/`rgt`. The nested-set
	columns are maintained by NestedSet on save and are correct almost all the
	time, but "almost" is not the standard for a cycle check whose failure mode
	is an infinite loop inside a tree rebuild. The parent chain is the definition
	of the tree; lft/rgt is a cached traversal of it.
	"""
	seen, out = set(), []
	current = frappe.db.get_value("Account", docname, "parent_account")
	while current and current not in seen:
		seen.add(current)
		out.append(current)
		current = frappe.db.get_value("Account", current, "parent_account")
	return out


def _child_count(docname: str) -> int:
	return frappe.db.count("Account", {"parent_account": docname})


def _validate_parent(parent_input: str, company: str, root_type: str, label: str = "parent_account") -> dict:
	"""Resolve and check a would-be parent: exists, in this company, a group, same root."""
	parent = _account(parent_input, company)
	if parent["company"] != company:
		raise ToolError(
			f"{label} {parent['name']!r} belongs to company {parent['company']!r}, "
			f"not {company!r}. An account cannot be parented across companies."
		)
	if not int(parent.get("is_group") or 0):
		raise ToolError(
			f"{label} {parent['name']!r} is a ledger account, not a group. In ERPNext "
			"only a group account can have children — and an account that has "
			"children can no longer be posted to, which is why this is not done "
			"automatically."
		)
	parent_root = str(parent.get("root_type") or "").strip()
	if root_type and parent_root and parent_root != root_type:
		raise ToolError(
			f"root_type {root_type!r} does not match {label} {parent['name']!r}, whose "
			f"root_type is {parent_root!r}. A subtree in ERPNext shares one root type; "
			"an Asset under Liabilities would break every report that walks the tree."
		)
	return parent


def _current_fiscal_year(company: str) -> dict | None:
	"""The Fiscal Year containing today for this company, or None.

	Company-restricted years win over global ones, which is how ERPNext resolves
	the same question: a Fiscal Year with no rows in its `companies` child table
	applies everywhere, and one with rows applies only there.
	"""
	today = frappe.utils.getdate().isoformat()
	grouped = _fiscal_years_by_company()
	for bucket in (grouped.get(company) or [], grouped.get("__all__") or []):
		for year in bucket:
			if int(year.get("disabled") or 0):
				continue
			start = frappe.utils.getdate(year.get("year_start_date")).isoformat()
			end = frappe.utils.getdate(year.get("year_end_date")).isoformat()
			if start <= today <= end:
				return {"name": year.get("name"), "year_start_date": start, "year_end_date": end}
	return None


def _gl_count(account: str, start: str = "", end: str = "") -> int:
	filters = {"account": account}
	if compat.has_field("GL Entry", "is_cancelled"):
		filters["is_cancelled"] = 0
	if start and end:
		filters["posting_date"] = ("between", [start, end])
	return frappe.db.count("GL Entry", filters)


def _describe(row: dict) -> dict:
	"""The account shape every tool in this module returns."""
	return {
		"name": row.get("name"),
		"account_number": row.get("account_number") or "",
		"account_name": row.get("account_name"),
		"parent_account": row.get("parent_account") or "",
		"is_group": bool(int(row.get("is_group") or 0)),
		"root_type": row.get("root_type") or "",
		"report_type": row.get("report_type") or "",
		"account_type": row.get("account_type") or "",
		"account_currency": row.get("account_currency") or "",
		"disabled": bool(int(row.get("disabled") or 0)),
		"company": row.get("company"),
	}


def _apply_description(doc, description: str) -> str:
	"""Attach a template's account description where this site can hold one.

	Stock ERPNext's Account has no description field. Rather than drop the text —
	which is the part of a template that stops the next person misusing an
	account — it goes on as a comment, which is visible in the Desk sidebar and
	readable back through `list_comments`. A site that has added a `description`
	custom field gets it in the field instead. Returns which happened.
	"""
	text = str(description or "").strip()
	if not text:
		return ""
	if compat.has_field("Account", "description"):
		doc.db_set("description", text, update_modified=False)
		return "description field"
	try:
		doc.add_comment("Comment", text)
		return "comment"
	except Exception:
		# The account exists and is correct; a missing note must not fail the
		# call or roll back the chart it belongs to.
		frappe.log_error(
			title="erpnext_mcp: could not attach account description",
			message=compat.traceback_text(),
		)
		return "dropped"


# ── 38. create_account ──────────────────────────────────────────────────────
def create_account(args: dict) -> ToolResult:
	"""Create one Account under an existing group."""
	company = resolve_company(as_str(args, "company"), required=True)
	account_name = as_str(args, "account_name", required=True)
	account_number = as_str(args, "account_number", required=True)
	root_type = as_str(args, "root_type", required=True)
	parent_input = as_str(args, "parent_account", required=True)
	is_group = bool(as_bool(args, "is_group", False))
	account_type = as_str(args, "account_type")
	account_currency = as_str(args, "account_currency")
	tax_rate = args.get("tax_rate")

	if root_type not in charts.ROOT_TYPES:
		raise ToolError(f"root_type must be one of {', '.join(charts.ROOT_TYPES)}; got {root_type!r}")

	parent = _validate_parent(parent_input, company, root_type)

	holder = _number_holder(company, account_number)
	if holder:
		raise ToolError(
			f"account_number {account_number!r} is already used by {holder!r} in "
			f"{company}. Account numbers are unique per company; pick another, or "
			"use update_account to renumber the existing one. Nothing was created."
		)

	abbr = _company_abbr(company)
	predicted = charts.account_docname(account_number, account_name, abbr)
	if frappe.db.exists("Account", predicted):
		raise ToolError(
			f"an Account named {predicted!r} already exists. ERPNext names accounts "
			"'<number> - <name> - <abbr>', so this number-and-name pair is taken. "
			"Nothing was created."
		)

	supported = charts.site_account_types()
	if account_type:
		if supported and account_type not in supported:
			raise ToolError(
				f"this site's ERPNext has no account_type {account_type!r}. Valid "
				f"values: {', '.join(sorted(supported))}. Nothing was created."
			)
		conflict = charts.account_type_conflict(account_type, root_type)
		if conflict:
			raise ToolError(f"{conflict}. Nothing was created.")

	if account_currency and not frappe.db.exists("Currency", account_currency):
		raise ToolError(f"no Currency named {account_currency!r} on this site. Nothing was created.")

	doc = frappe.new_doc("Account")
	doc.company = company
	doc.account_name = account_name
	doc.parent_account = parent["name"]
	doc.is_group = 1 if is_group else 0
	doc.root_type = root_type
	if compat.has_field("Account", "account_number"):
		doc.account_number = account_number
	if account_type:
		doc.account_type = account_type
	if account_currency:
		doc.account_currency = account_currency
	if tax_rate not in (None, ""):
		doc.tax_rate = as_float(tax_rate, "tax_rate")
	doc.insert()

	row = _account(doc.name, company)
	data = {
		**_describe(row),
		"next_step": (
			"A group account cannot be posted to; add ledger accounts under it."
			if is_group
			else "Ledger account, ready to post to."
		),
	}
	kind = "group" if is_group else "ledger"
	return ToolResult(
		data,
		f"created {kind} Account {doc.name} ({root_type}) under {parent['name']}",
		docstatus_delta="none → 0 (created)",
	)


# ── 39. update_account ──────────────────────────────────────────────────────
def update_account(args: dict) -> ToolResult:
	"""Rename, renumber, re-type or disable/enable one Account in place.

	Never changes the parent. See the module docstring for why that is a separate
	tool, and for the docname quirk this delegates around.
	"""
	name = as_str(args, "name", required=True)
	company = as_str(args, "company")
	row = _account(name, company)
	company = row["company"]

	new_name = as_str(args, "new_account_name")
	new_number = as_str(args, "new_account_number")
	new_type = args.get("new_account_type")
	disabled = as_bool(args, "disabled")

	if not any(value not in (None, "") for value in (new_name, new_number, new_type)) and disabled is None:
		raise ToolError(
			"nothing to change. Pass at least one of new_account_name, "
			"new_account_number, new_account_type or disabled."
		)
	if "parent_account" in args or "new_parent_account" in args:  # pragma: no cover - schema blocks it
		raise ToolError("update_account cannot reparent an account; use move_account.")

	root = _is_root(row)
	if root and (new_type is not None or disabled is not None):
		raise ToolError(
			f"{row['name']} is a root account, and ERPNext refuses any save against a "
			"root ('Root cannot be edited'), so its account_type and disabled flag "
			"cannot be changed. Renaming and renumbering go through a different path "
			"and do work on a root."
		)

	changes = {}

	# -- type and disabled: an ordinary save, so the doctype validates them ----
	account_type = None
	if new_type is not None:
		account_type = str(new_type).strip()
		supported = charts.site_account_types()
		if account_type and supported and account_type not in supported:
			raise ToolError(
				f"this site's ERPNext has no account_type {account_type!r}. Valid "
				f"values: {', '.join(sorted(supported))}. Nothing was changed."
			)
		conflict = charts.account_type_conflict(account_type, row.get("root_type") or "")
		if conflict:
			raise ToolError(f"{conflict}. Nothing was changed.")
		current_type = str(row.get("account_type") or "")
		crosses_party = (current_type in _PARTY_TYPES) != (account_type in _PARTY_TYPES)
		if crosses_party and current_type != account_type:
			entries = _gl_count(row["name"])
			if entries:
				raise ToolError(
					f"{row['name']} has {entries} GL Entry row(s) and changing its "
					f"account_type from {current_type or '<none>'!r} to "
					f"{account_type or '<none>'!r} crosses ERPNext's Receivable/Payable "
					"boundary. Party balances are keyed off that flag, so the existing "
					"ledger would stop reconciling. Nothing was changed."
				)

	if account_type is not None or disabled is not None:
		doc = frappe.get_doc("Account", row["name"])
		if account_type is not None and account_type != str(row.get("account_type") or ""):
			doc.account_type = account_type
			changes["account_type"] = [row.get("account_type") or "", account_type]
		if disabled is not None and bool(int(row.get("disabled") or 0)) != disabled:
			doc.disabled = 1 if disabled else 0
			changes["disabled"] = [bool(int(row.get("disabled") or 0)), disabled]
		if changes:
			doc.save()

	# -- name and number: the docname has to move with them -------------------
	final_name = row["name"]
	rename_method = ""
	if new_name or new_number:
		target_name = new_name or row["account_name"]
		target_number = new_number if new_number else (row.get("account_number") or "")
		if new_number:
			holder = _number_holder(company, new_number, exclude=row["name"])
			if holder:
				raise ToolError(
					f"account_number {new_number!r} is already used by {holder!r} in "
					f"{company}. Nothing was renamed."
				)
		predicted = charts.account_docname(target_number, target_name, _company_abbr(company))
		if predicted != row["name"] and frappe.db.exists("Account", predicted):
			raise ToolError(f"an Account named {predicted!r} already exists. Nothing was renamed.")

		final_name, rename_method = _rename_account(row["name"], target_name, target_number)
		if new_name:
			changes["account_name"] = [row["account_name"], target_name]
		if new_number:
			changes["account_number"] = [row.get("account_number") or "", target_number]

	if not changes:
		raise ToolError(
			f"{row['name']} already has those values; nothing to change. Pass a value "
			"that differs from the current one."
		)

	after = _account(final_name, company)
	data = {
		**_describe(after),
		"previous_name": row["name"],
		"renamed": final_name != row["name"],
		"changes": changes,
		"rename_method": rename_method or None,
		"note": (
			"The docname changed, so links, reports and GL Entry references were "
			"updated by ERPNext's rename. Any docname held outside ERPNext is stale."
			if final_name != row["name"]
			else "The docname is unchanged."
		),
	}
	summary = ", ".join(
		f"{field} {before!r} → {after_value!r}" for field, (before, after_value) in changes.items()
	)
	return ToolResult(
		data,
		f"updated Account {row['name']}: {summary}"
		+ (f" (now {final_name})" if final_name != row["name"] else ""),
		docstatus_delta="",
	)


def _rename_account(name: str, account_name: str, account_number: str) -> tuple:
	"""Change an Account's name and/or number, keeping the docname in step.

	Delegates to ERPNext's `update_account_number`, which is the only code path
	that also syncs the change into child companies in a group structure. The
	two-step fallback exists for a version that predates it and is deliberately
	*not* the default: see the module docstring.
	"""
	try:
		from erpnext.accounts.doctype.account.account import update_account_number
	except Exception:
		update_account_number = None

	if callable(update_account_number):
		returned = update_account_number(name, account_name, account_number or None)
		# It returns the new docname only when the name actually moved.
		return (returned or name), "erpnext update_account_number"

	frappe.db.set_value("Account", name, "account_name", account_name)
	if compat.has_field("Account", "account_number"):
		frappe.db.set_value("Account", name, "account_number", account_number)
	company = frappe.db.get_value("Account", name, "company")
	predicted = charts.account_docname(account_number, account_name, _company_abbr(company))
	if predicted != name:
		frappe.rename_doc("Account", name, predicted, force=True)
		return predicted, "set_value + rename_doc (legacy ERPNext)"
	return name, "set_value (legacy ERPNext)"


# ── 40. move_account ────────────────────────────────────────────────────────
def move_account(args: dict) -> ToolResult:
	"""Reparent one Account. Changes nothing else about it."""
	name = as_str(args, "name", required=True)
	new_parent_input = as_str(args, "new_parent_account", required=True)
	company = as_str(args, "company")
	row = _account(name, company)
	company = row["company"]

	if _is_root(row):
		raise ToolError(
			f"{row['name']} is a root account. ERPNext refuses any save against a root "
			"('Root cannot be edited'), and a root has nowhere to move to. Nothing "
			"was changed."
		)

	parent = _validate_parent(new_parent_input, company, row.get("root_type") or "", "new_parent_account")

	if parent["name"] == row["parent_account"]:
		raise ToolError(f"{row['name']} is already under {parent['name']}; nothing to do.")
	if parent["name"] == row["name"]:
		raise ToolError("an account cannot be its own parent.")

	ancestry = _ancestors(parent["name"])
	if row["name"] in ancestry:
		raise ToolError(
			f"{parent['name']} is a descendant of {row['name']}, so moving it there "
			"would make a cycle: "
			f"{' → '.join([row['name'], *reversed(ancestry[: ancestry.index(row['name'])])])}"
			f" → {parent['name']}. Nothing was changed."
		)

	old_parent = row["parent_account"]
	entries = _gl_count(row["name"])
	doc = frappe.get_doc("Account", row["name"])
	doc.parent_account = parent["name"]
	doc.save()

	after = _account(row["name"], company)
	data = {
		**_describe(after),
		"previous_parent_account": old_parent,
		"new_parent_account": parent["name"],
		"gl_entries_on_this_account": entries,
		"note": (
			"Reparenting does not move a single GL Entry — every posting stays "
			"exactly where it was. What changes is which subtotal on the balance "
			"sheet and P&L those postings roll up into, for every period, including "
			"ones already reported. Re-run any report whose figures were quoted."
		),
	}
	return ToolResult(
		data,
		f"moved Account {row['name']} from {old_parent} to {parent['name']}",
		docstatus_delta="",
	)


# ── 41. disable_account ─────────────────────────────────────────────────────
def disable_account(args: dict) -> ToolResult:
	"""Disable an Account — ERPNext's soft delete — recording why.

	Refuses if the account carries GL entries inside the current fiscal year.
	That is the line between "tidying the chart" and "breaking this year's
	reports": a disabled account keeps its history and keeps appearing in reports
	that cover it, but it drops out of pickers and out of some period comparisons,
	and doing that to an account the current year is still posting through
	produces figures nobody can reconcile.
	"""
	name = as_str(args, "name", required=True)
	reason = as_str(args, "reason", required=True)
	company = as_str(args, "company")
	if len(reason) < 4:
		raise ToolError("reason must be a real explanation, not a placeholder")

	row = _account(name, company)
	company = row["company"]

	if not compat.has_field("Account", "disabled"):
		raise ToolError(
			"this site's Account doctype has no `disabled` field, so there is no soft delete to perform."
		)
	if bool(int(row.get("disabled") or 0)):
		raise ToolError(f"Account {row['name']} is already disabled.")
	if _is_root(row):
		raise ToolError(
			f"{row['name']} is a root account. ERPNext refuses any save against a root "
			"('Root cannot be edited'), so it cannot be disabled. Nothing was changed."
		)

	year = _current_fiscal_year(company)
	if year:
		entries = _gl_count(row["name"], year["year_start_date"], year["year_end_date"])
		window = f"fiscal year {year['name']} ({year['year_start_date']} to {year['year_end_date']})"
	else:
		# No fiscal year covers today. Rather than treat "cannot tell" as "no
		# entries", fall back to the trailing year — a wider net than the real
		# one, which errs towards refusing.
		start = frappe.utils.add_days(frappe.utils.getdate(), -365).isoformat()
		end = frappe.utils.getdate().isoformat()
		entries = _gl_count(row["name"], start, end)
		window = f"the last 365 days ({start} to {end}) — no Fiscal Year on this site covers today"

	if entries:
		raise ToolError(
			f"{row['name']} has {entries} GL Entry row(s) in {window}. Disabling an "
			"account the current year is still posting through corrupts period "
			"comparisons and hides the account from pickers a correction would need. "
			"Move or reverse those entries first, or disable it after the year "
			"closes. Nothing was changed."
		)

	children = _child_count(row["name"])
	total_entries = _gl_count(row["name"])

	doc = frappe.get_doc("Account", row["name"])
	doc.disabled = 1
	doc.save()
	try:
		doc.add_comment("Comment", f"Disabled via MCP (erpnext_mcp). Reason: {reason}")
	except Exception:
		# The disable succeeded and is what was asked for; the reason is in the
		# audit log regardless.
		frappe.log_error(
			title="erpnext_mcp: could not attach disable reason comment",
			message=compat.traceback_text(),
		)

	after = _account(row["name"], company)
	data = {
		**_describe(after),
		"reason": reason,
		"checked_window": window,
		"gl_entries_in_window": 0,
		"gl_entries_all_time": total_entries,
		"child_accounts": children,
		"note": (
			"Nothing was deleted. The account, its history and its GL entries all "
			"remain; it is hidden from pickers and marked inactive. Re-enable it with "
			"update_account(disabled=false)."
		)
		+ (
			f" It is a group with {children} child account(s), which are NOT disabled "
			"by this call and remain postable."
			if children
			else ""
		),
	}
	return ToolResult(
		data,
		f"disabled Account {row['name']} — {reason}",
		docstatus_delta="enabled → disabled",
	)


# ── 71. delete_account ──────────────────────────────────────────────────────
#: The safety checks `delete_account` runs, in the order they are reported. Each
#: is a `force_check_<key>` argument that defaults to true; turning one off skips
#: this app's check and leaves whatever the framework does in its place.
_DELETE_CHECKS = ("gl_entries", "children", "company_defaults", "bank_accounts")


def delete_account(args: dict) -> ToolResult:
	"""Hard-delete an Account that nothing has ever touched. The complement to disable.

	WHEN THIS IS THE RIGHT TOOL AND `disable_account` IS NOT. Disabling is for an
	account with history: the postings stay, the reports that cover them still
	balance, and the account drops out of pickers. It is right almost always. This
	is for the other case — the fifty accounts a bundled chart of accounts created
	on day one that nobody ever posted to, which clutter every picker and every
	tree and cannot be disabled out of existence because *a disabled account still
	holds its account number*. On a company being renumbered onto a real chart,
	that is the whole problem.

	EVERY CHECK IS A REFUSAL, NOT A WARNING. All four run before anything is
	deleted and every failure is collected, so one call reports every reason
	rather than one reason four times. A deletion is the only genuinely
	irreversible thing in this app; there is no undo and no draft.
	"""
	name = as_str(args, "name", required=True)
	company = as_str(args, "company")
	row = _account(name, company)
	company = row["company"]

	wanted = {key: bool(as_bool(args, f"force_check_{key}", True)) for key in _DELETE_CHECKS}
	skipped = sorted(key for key, on in wanted.items() if not on)

	passed: dict = {}
	blockers: list = []

	if wanted["gl_entries"]:
		entries = _gl_count(row["name"])
		lines = _draft_journal_lines(row["name"])
		if entries or lines:
			blockers.append(
				f"it has {entries} GL Entry row(s) and appears on {lines} journal entry line(s). "
				"An account with history cannot be deleted — the postings would have nowhere to "
				"point. Use disable_account, which keeps all of it and hides the account from "
				"pickers."
			)
		else:
			passed["gl_entries"] = "no GL entries, ever, and no journal entry line references it"

	if wanted["children"]:
		children = frappe.db.get_all(
			"Account",
			filters={"parent_account": row["name"]},
			fields=compat.existing_fields("Account", ("name", "disabled")),
			limit=50,
		)
		if children:
			listing = ", ".join(str(child["name"]) for child in children[:8])
			blockers.append(
				f"it is a group with {len(children)} child account(s) ({listing}"
				+ (", …" if len(children) > 8 else "")
				+ "). Deleting it would orphan them. Delete or move the children first — "
				"disabled children count, because a disabled account is still a child."
			)
		else:
			passed["children"] = "no child accounts, enabled or disabled"

	if wanted["company_defaults"]:
		references = _company_default_references(row["name"])
		if references:
			listing = ", ".join(f"{holder}.{field}" for holder, field in references)
			blockers.append(
				f"it is a company default: {listing}. Deleting it would leave the field pointing "
				"at nothing, and the next document that reaches for that default fails on save. "
				"Repoint it with set_company_defaults first."
			)
		else:
			passed["company_defaults"] = "no Company field points at it"

	if wanted["bank_accounts"]:
		linked = _bank_accounts_for(row["name"])
		if linked:
			blockers.append(
				f"{len(linked)} Bank Account record(s) post to it ({', '.join(linked)}). A bank "
				"feed writing into one of those would have nowhere to reconcile to. Repoint or "
				"delete the Bank Account first."
			)
		else:
			passed["bank_accounts"] = "no Bank Account record posts to it"

	if blockers:
		raise ToolError(
			f"{row['name']} cannot be deleted: "
			+ "; ".join(f"({index}) {reason}" for index, reason in enumerate(blockers, start=1))
			+ ". Nothing was deleted."
		)

	before = _describe(row)
	is_root = _is_root(row)
	frappe.delete_doc("Account", row["name"])

	data = {
		"deleted": row["name"],
		"account": before,
		"checks_passed": passed,
		"checks_skipped": skipped,
		"was_root": is_root,
		"note": (
			"Gone. Unlike disable_account there is nothing left: no record, no history, and the "
			f"account number {before['account_number'] or '<none>'} is free for another account "
			"to take — which is usually the reason for doing this at all."
		)
		+ (
			f" force_check_{', force_check_'.join(skipped)} was turned off, so this app did not "
			"run that check. Frappe's own link-integrity check still ran on the delete, so a "
			"reference it can see would have refused anyway — the flag changes which error you "
			"get, not whether a referenced account can be removed."
			if skipped
			else ""
		),
	}
	if is_root:
		data["warning"] = (
			"That was a root account. It had no children, so nothing was orphaned, but a company "
			"whose chart is missing a root type has no home for the next account of that type — "
			"import_chart_of_accounts can create one."
		)
	return ToolResult(
		data,
		f"deleted Account {row['name']} ({len(passed)} safety check(s) passed"
		+ (f", {len(skipped)} skipped" if skipped else "")
		+ ")",
		docstatus_delta="existed → deleted",
	)


def _draft_journal_lines(account: str) -> int:
	"""Journal entry lines naming this account, including on drafts.

	A draft Journal Entry writes no GL Entry, so an account can be referenced by
	one and read as untouched. Deleting it leaves a draft nobody can submit and
	nobody can fix, because the account it names no longer exists.
	"""
	if not compat.doctype_exists("Journal Entry Account"):  # pragma: no cover - core doctype
		return 0
	return frappe.db.count("Journal Entry Account", {"account": account})


def _company_default_references(account: str) -> list:
	"""Every (company, fieldname) whose Account link points at this account."""
	try:
		fields = [
			field.get("fieldname")
			for field in frappe.get_meta("Company").fields
			if str(field.get("fieldtype") or "") == "Link" and str(field.get("options") or "") == "Account"
		]
	except Exception:  # pragma: no cover - a site mid-migrate
		fields = []
	# A site whose Company meta does not describe its Link options (an older
	# Frappe, or a meta this app can only read through `compat`) still has the
	# columns; fall back to the account fields this app knows how to set. The
	# cost-center defaults are deliberately not in that list — comparing a Cost
	# Center column against an account docname can only ever be a no-op.
	if not fields:
		from .dimensions import ACCOUNT_COMPANY_DEFAULTS

		fields = list(ACCOUNT_COMPANY_DEFAULTS)
	fields = sorted({field for field in fields if field and compat.has_field("Company", field)})
	if not fields:  # pragma: no cover - a Company with no account fields at all
		return []

	out = []
	for row in frappe.db.get_all("Company", fields=["name", *fields], limit=500):
		for field in fields:
			if str(row.get(field) or "") == account:
				out.append((row["name"], field))
	return out


def _bank_accounts_for(account: str) -> list:
	if not compat.doctype_exists("Bank Account"):
		return []
	return sorted(
		str(name)
		for name in frappe.db.get_all("Bank Account", filters={"account": account}, pluck="name", limit=25)
	)


# ── 42. import_chart_of_accounts ────────────────────────────────────────────
def import_chart_of_accounts(args: dict) -> ToolResult:
	"""Create a whole tree of accounts, or report what doing so would do.

	`dry_run` defaults to TRUE and that default is load-bearing. An accidental
	call — a model retrying, a client replaying a message — must not rearrange a
	live chart of accounts, and the only way to guarantee that is for the
	dangerous behaviour to be the one you have to ask for.

	A real run is atomic. The first failure raises, `registry.dispatch` rolls the
	transaction back, and the site is left exactly as it was — there is no
	half-imported chart to unpick, which matters more here than anywhere else in
	this app because a partial tree has orphaned groups in it.
	"""
	company = resolve_company(as_str(args, "company"), required=True)
	dry_run = as_bool(args, "dry_run", True)
	tree = charts.parse_accounts_json(args.get("accounts_json"))

	abbr = _company_abbr(company)
	supported = charts.site_account_types()
	rows: list = []
	_plan_nodes(
		tree, None, "", None, {"company": company, "abbr": abbr, "supported": supported, "rows": rows}
	)

	errors = [row for row in rows if row["action"] == "error"]
	to_create = [row for row in rows if row["action"] == "create"]
	to_skip = [row for row in rows if row["action"] == "skip"]
	# One bad group takes its whole subtree with it, so a chart with five real
	# problems can produce seventy error rows. Separating the causes from the
	# fallout is the difference between a fixable answer and a wall of text.
	causes = [row for row in errors if not row["_cascade"]]

	if errors and not dry_run:
		detail = "; ".join(
			f"{row['account_number']} {row['account_name']}: {row['note']}" for row in causes[:6]
		)
		raise ToolError(
			f"{len(causes)} problem(s) in this chart block {len(errors)} account(s) "
			f"from being created against {company}: {detail}"
			+ ("; …" if len(causes) > 6 else "")
			+ ". Nothing was created — re-run with dry_run=true for the full plan."
		)

	if dry_run:
		data = _plan_payload(company, abbr, rows, dry_run=True)
		data["blocking_problems"] = [
			{
				"account_number": row["account_number"],
				"account_name": row["account_name"],
				"problem": row["note"],
			}
			for row in causes
		]
		data["next_step"] = (
			f"{len(causes)} problem(s) to fix, blocking {len(errors)} of {len(rows)} "
			"accounts. `blocking_problems` lists the causes; every other error row is "
			"a child of a group that cannot be created. On a company built from one of "
			"ERPNext's bundled charts the usual cause is that the bundled accounts "
			"already hold these numbers — renumber those out of the way with "
			"update_account first, and propose_clean_chart lists exactly which ones."
			if causes
			else (
				f"Review the {len(to_create)} account(s) above, then call again with "
				"dry_run=false to create them."
			)
		)
		return ToolResult(
			data,
			f"planned {len(to_create)} new account(s) for {company} "
			f"({len(to_skip)} already present, {len(causes)} problem(s) blocking "
			f"{len(errors)}) — nothing was created",
		)

	if not to_create:
		raise ToolError(
			f"every account in this chart already exists in {company}; there is nothing to create."
		)

	created = 0
	descriptions = {"description field": 0, "comment": 0, "dropped": 0}
	for row in rows:
		if row["action"] == "skip":
			row["_actual"] = row["existing_docname"]
			continue
		parent_row = row["_parent_row"]
		parent_name = parent_row["_actual"] if parent_row else row["parent_account"]
		doc = frappe.new_doc("Account")
		doc.company = company
		doc.account_name = row["account_name"]
		doc.parent_account = parent_name or None
		doc.is_group = 1 if row["is_group"] else 0
		doc.root_type = row["root_type"]
		if not parent_name:
			# A new top-level account. ERPNext marks parent_account required, so
			# this insert would otherwise die with `MandatoryError: [Account, …]:
			# parent_account` — see the module docstring. Set per document and only
			# for a root: a child that skipped mandatory validation would be this
			# app disabling a check ERPNext meant to run.
			doc.flags.ignore_mandatory = True
		if row["account_number"] and compat.has_field("Account", "account_number"):
			doc.account_number = row["account_number"]
		if row["account_type"]:
			doc.account_type = row["account_type"]
		if row.get("account_currency"):
			doc.account_currency = row["account_currency"]
		if row.get("tax_rate") not in (None, ""):
			doc.tax_rate = as_float(row["tax_rate"], "tax_rate")
		doc.insert()
		row["_actual"] = doc.name
		row["docname"] = doc.name
		row["action"] = "created"
		created += 1
		if row.get("description"):
			where = _apply_description(doc, row["description"])
			if where in descriptions:
				descriptions[where] += 1

	data = _plan_payload(company, abbr, rows, dry_run=False)
	data["descriptions_written"] = {key: value for key, value in descriptions.items() if value}
	data["note"] = (
		"Created in one transaction: any failure would have rolled the whole import "
		"back rather than leaving a partial tree."
	)
	return ToolResult(
		data,
		f"created {created} account(s) in {company} ({len(to_skip)} already present)",
		docstatus_delta=f"none → 0 ({created} accounts created)",
	)


def _plan_nodes(nodes, parent_row, parent_root_type, parent_docname, ctx) -> None:
	"""Turn a tree of template nodes into a flat, ordered plan.

	Recursive rather than a flat walk because each child's parent docname is only
	known once its parent's row exists — including for a node that turns out to
	already be on the site, whose real docname is what the children must hang off.
	"""
	for node in nodes or ():
		row = _plan_one(node, parent_row, parent_root_type, parent_docname, ctx)
		ctx["rows"].append(row)
		_plan_nodes(node.get("children") or (), row, row["root_type"], row["docname"], ctx)


def _plan_one(node, parent_row, parent_root_type, parent_docname, ctx) -> dict:
	company, abbr, supported = ctx["company"], ctx["abbr"], ctx["supported"]
	account_name = str(node.get("account_name") or "").strip()
	account_number = str(node.get("account_number") or "").strip()
	is_group = bool(node.get("is_group"))
	root_type = str(node.get("root_type") or "").strip() or parent_root_type
	depth = 0 if parent_row is None else parent_row["depth"] + 1

	notes = []
	account_type, adjustment = charts.resolve_account_type(
		node.get("account_type") or "", root_type, supported
	)
	if adjustment:
		notes.append(adjustment)

	currency = str(node.get("account_currency") or "").strip()
	if currency and not frappe.db.exists("Currency", currency):
		return _row(
			node,
			"error",
			"",
			"",
			root_type,
			account_type,
			is_group,
			depth,
			f"no Currency named {currency!r} on this site",
		)

	# Where this node hangs. A root node may name an existing parent explicitly;
	# without one it becomes a new root account of its own.
	parent_account = parent_docname or ""
	new_root = False
	if parent_row is None:
		declared = str(node.get("parent_account") or "").strip()
		if declared:
			try:
				parent_account = _validate_parent(declared, company, root_type)["name"]
			except ToolError as exc:
				return _row(node, "error", "", "", root_type, account_type, is_group, depth, str(exc))
		else:
			# A top-level account of its own. Planned explicitly rather than falling
			# out of an empty parent, because the live run has to know: a root is
			# the one insert that needs `ignore_mandatory`, and a dry run that did
			# not say "this would be a new root" would not be describing what the
			# live run does.
			new_root = True

	row = _row(
		node,
		"create",
		charts.account_docname(account_number, account_name, abbr),
		parent_account,
		root_type,
		account_type,
		is_group,
		depth,
		"; ".join(notes),
	)
	row["_parent_row"] = parent_row
	if new_root:
		# ERPNext's other rule about roots — that one must be a group — is already
		# enforced structurally by `charts.validate_tree` before this function is
		# reached, so it is not repeated here.
		row["new_root"] = True

	if parent_row is not None and parent_row["action"] == "error":
		row["action"] = "error"
		row["note"] = f"its parent {parent_row['account_name']} cannot be created"
		row["_cascade"] = True
		return row

	existing = _existing_account(company, account_number, row["docname"])
	if existing:
		row["existing_docname"] = existing["name"]
		row["docname"] = existing["name"]
		mismatch = _existing_mismatch(existing, account_name, is_group, root_type, parent_account)
		if mismatch:
			row["action"] = "error"
			row["note"] = mismatch
		else:
			row["action"] = "skip"
			row["note"] = "already present in this company; left exactly as it is"
		return row

	if not root_type:
		row["action"] = "error"
		row["note"] = "no root_type, and none inherited from a parent"
	return row


def _row(node, action, docname, parent_account, root_type, account_type, is_group, depth, note) -> dict:
	row = {
		"action": action,
		"account_number": str(node.get("account_number") or "").strip(),
		"account_name": str(node.get("account_name") or "").strip(),
		"docname": docname,
		"parent_account": parent_account,
		"root_type": root_type,
		"account_type": account_type,
		"is_group": is_group,
		"depth": depth,
		"note": note,
		"existing_docname": "",
		"_parent_row": None,
		"_actual": "",
		"_cascade": False,
	}
	for key in ("description", "account_currency", "tax_rate"):
		if node.get(key) not in (None, ""):
			row[key] = node[key]
	if node.get("optional"):
		row["optional"] = True
	return row


def _existing_account(company: str, account_number: str, predicted_docname: str):
	"""The account already occupying this number, or this docname, if any."""
	if account_number and compat.has_field("Account", "account_number"):
		found = frappe.db.get_all(
			"Account",
			filters={"company": company, "account_number": account_number},
			fields=compat.existing_fields("Account", _ACCOUNT_FIELDS),
			limit=1,
		)
		if found:
			return dict(found[0])
	row = frappe.db.get_value(
		"Account",
		predicted_docname,
		compat.existing_fields("Account", _ACCOUNT_FIELDS),
		as_dict=True,
	)
	return dict(row) if row else None


def _existing_mismatch(existing, account_name, is_group, root_type, parent_account) -> str:
	"""Why an account already holding this number is not the one asked for.

	Skipping an existing account is what makes an import re-runnable. Skipping an
	account that merely *shares a number* with the one in the chart is how a
	chart silently ends up meaning something other than what was reviewed — so
	anything beyond a name match is an error, not a skip.
	"""
	if str(existing.get("account_name") or "").strip() != account_name:
		return (
			f"account_number {existing.get('account_number')} is already used by "
			f"{existing['name']!r} (account_name {existing.get('account_name')!r}), not "
			f"by {account_name!r}. Renumber the chart, or rename the existing account "
			"with update_account first."
		)
	if bool(int(existing.get("is_group") or 0)) != bool(is_group):
		return (
			f"{existing['name']} exists but is a "
			f"{'group' if existing.get('is_group') else 'ledger'} account, and the chart "
			f"asks for a {'group' if is_group else 'ledger'} one."
		)
	if root_type and str(existing.get("root_type") or "") != root_type:
		return (
			f"{existing['name']} exists with root_type "
			f"{existing.get('root_type')!r}, and the chart asks for {root_type!r}."
		)
	if parent_account and str(existing.get("parent_account") or "") != parent_account:
		return (
			f"{existing['name']} exists under {existing.get('parent_account')!r}, and the "
			f"chart puts it under {parent_account!r}. Use move_account if the chart is "
			"right — an import will not silently reparent an existing account."
		)
	return ""


def _plan_payload(company: str, abbr: str, rows: list, dry_run: bool) -> dict:
	public = [
		{key: value for key, value in row.items() if not key.startswith("_") and value != ""} for row in rows
	]
	for row in public:
		row.setdefault("is_group", False)
		row.setdefault("depth", 0)
	counts: dict = {}
	for row in rows:
		counts[row["action"]] = counts.get(row["action"], 0) + 1
	new_roots = [
		row["docname"] for row in rows if row.get("new_root") and row["action"] in ("create", "created")
	]
	payload = {
		"company": company,
		"company_abbr": abbr,
		"dry_run": bool(dry_run),
		"total_accounts": len(rows),
		"counts": counts,
		"accounts": public,
		"new_root_accounts": new_roots,
	}
	if new_roots:
		payload["new_root_note"] = (
			f"{len(new_roots)} account(s) in this chart have no parent and would become new root "
			"accounts alongside the company's existing ones — ERPNext will not let a root be "
			"moved or renamed into an existing tree afterwards, so this adds a second set of "
			"roots rather than replacing anything. They are inserted with ignore_mandatory, "
			"which is what ERPNext's own chart importer does for a root and the only way past "
			"the required parent_account field."
		)
	return payload


# ── 43. propose_clean_chart ─────────────────────────────────────────────────
def propose_clean_chart(args: dict) -> ToolResult:
	"""Return a starting chart of accounts for review. Reads nothing, writes nothing.

	The output is deliberately the exact input shape `import_chart_of_accounts`
	takes, so the review step is "read this, delete what you do not want, pass it
	back" rather than "read this, then go and write the real thing".
	"""
	company = resolve_company(as_str(args, "company"), required=True)
	key = as_str(args, "template") or "us_llc_farm"
	template = charts.get(key)
	if template is None:
		raise ToolError(f"no chart template named {key!r}. Available: {', '.join(charts.names())}.")

	abbr = _company_abbr(company)
	supported = charts.site_account_types()
	adjustments: list = []
	tree = _materialise(template.tree, "", supported, adjustments)

	optional = [
		{"account_number": node.get("account_number"), "account_name": node.get("account_name")}
		for node, _parent, _depth in charts.walk(template.tree)
		if node.get("optional")
	]

	existing_roots = [
		{
			"name": row["name"],
			"account_name": row.get("account_name"),
			"root_type": row.get("root_type"),
			"children": _child_count(row["name"]),
		}
		for row in frappe.db.get_all(
			"Account",
			filters={"company": company, "parent_account": ("in", ["", None])},
			fields=compat.existing_fields("Account", ("name", "account_name", "root_type")),
			order_by="name asc",
		)
	]

	numbers = [
		str(node.get("account_number") or "").strip()
		for node, _parent, _depth in charts.walk(template.tree)
		if str(node.get("account_number") or "").strip()
	]
	already = _numbers_in_use(company, numbers)

	data = {
		**template.describe(),
		"company": company,
		"company_abbr": abbr,
		"accounts": tree,
		"optional_accounts": optional,
		"account_type_adjustments": adjustments,
		"existing_root_accounts": existing_roots,
		"account_numbers_already_in_use": already,
		"read_only": True,
		"next_step": (
			"Nothing was changed — this is a proposal. Strike any account you do not "
			"want (the optional ones first), then pass the whole response, or just "
			"its `accounts` list, to import_chart_of_accounts. That tool also "
			"defaults to a dry run, so there is a second look before anything is "
			"created."
		),
		"warning": _propose_warning(existing_roots, already),
	}
	return ToolResult(
		data,
		f"proposed the {template.key} chart for {company}: "
		f"{data['total_accounts']} accounts ({data['group_accounts']} groups, "
		f"{data['ledger_accounts']} ledgers), {len(already)} number(s) already in use",
	)


def _propose_warning(existing_roots: list, already: list) -> str:
	"""What a reviewer needs to know before running this, in the order it bites.

	The number collision comes first because it is the one that stops the import
	dead, and because it is near-certain on a company created from one of
	ERPNext's bundled charts: "Standard with Numbers" numbers its own roots
	1000/2000/3000/4000/5000, which is the same convention this template uses.
	"""
	parts = []
	if already:
		parts.append(
			f"{len(already)} of this template's account numbers are already held by "
			"accounts on this company, and ERPNext requires account numbers to be "
			"unique per company — so the import will refuse until that is resolved. "
			"They are listed in `account_numbers_already_in_use`. The usual fix is to "
			"renumber the existing ones out of the way first with update_account (for "
			"example prefixing each with a 9), which is safe: it moves the docname with "
			"the fields and repoints every child. Deleting them is not something this "
			"app offers, and disabling one does not free its number."
		)
	if existing_roots:
		parts.append(
			f"This company already has {len(existing_roots)} root account(s). Importing "
			"adds a second set of roots alongside them rather than replacing anything, "
			"because ERPNext will not let a root be edited or moved once it exists. Plan "
			"to retire the old tree afterwards — disable_account for the ledgers and "
			"groups, and a rename for the roots themselves."
		)
	return " ".join(parts)


def _materialise(nodes, parent_root_type: str, supported: set, adjustments: list) -> list:
	"""Copy a template tree, swapping in account types this site actually has."""
	out = []
	for node in nodes:
		root_type = str(node.get("root_type") or "").strip() or parent_root_type
		copy = {key: value for key, value in node.items() if key != "children"}
		wanted = str(node.get("account_type") or "").strip()
		usable, note = charts.resolve_account_type(wanted, root_type, supported)
		if note:
			adjustments.append(
				{
					"account_number": node.get("account_number"),
					"account_name": node.get("account_name"),
					"requested": wanted,
					"used": usable or None,
					"reason": note,
				}
			)
		if usable:
			copy["account_type"] = usable
		else:
			copy.pop("account_type", None)
		children = node.get("children")
		if children is not None:
			copy["children"] = _materialise(children, root_type, supported, adjustments)
		out.append(copy)
	return out


def _numbers_in_use(company: str, numbers: list) -> list:
	if not numbers or not compat.has_field("Account", "account_number"):
		return []
	rows = frappe.db.get_all(
		"Account",
		filters={"company": company, "account_number": ("in", numbers)},
		fields=compat.existing_fields("Account", ("name", "account_number", "account_name")),
		order_by="account_number asc",
		limit=len(numbers),
	)
	return [dict(row) for row in rows]
