# SPDX-License-Identifier: MIT
"""Argument coercion and name resolution shared by every tool.

An MCP client is a language model. It will send `"2026-1-5"` for a date, the
string `"50"` for a limit, an account's *label* where its docname belongs, and
sometimes nothing at all where the site has only one sensible answer. Rejecting
those with a schema error wastes a round trip; each one has an unambiguous
reading, so this module does the reading and raises `ToolError` with a message
that says what to send instead only when it genuinely cannot tell.

The resolvers are the important part. `resolve_company` fills in the company
when the site has exactly one, which is the common case and saves the model
guessing. `resolve_account` accepts a docname, an account number, or an account
name, because those are three things a model will call "the account" and only
one of them is the primary key.
"""

import frappe

from .errors import ToolError

#: Hard ceiling on any `limit`, whatever the caller asks for. A model that says
#: `limit=100000` wants "all of them"; answering with 500 rows and saying so is
#: more useful than timing out the request or blowing the context window.
MAX_LIMIT = 500
DEFAULT_LIMIT = 100


def as_str(args: dict, key: str, required: bool = False, default: str = "") -> str:
	value = args.get(key)
	if value is None or value == "":
		if required:
			raise ToolError(f"{key} is required")
		return default
	return str(value).strip()


def as_filter(args: dict, key: str, default: str = "") -> str:
	"""A filter value where an explicit empty string means "no filter".

	`as_str` cannot express this. It treats `""` the same as missing, so a tool
	whose default is `"Active"` would silently override a caller who deliberately
	passed an empty status meaning "every status" — and the tool's own
	description promises that works. Absent → the default; present but empty →
	no filter.
	"""
	if key not in args or args[key] is None:
		return default
	return str(args[key]).strip()


def as_date(args: dict, key: str, required: bool = False) -> str | None:
	"""An ISO `YYYY-MM-DD` date string, or None when absent and not required.

	Accepts anything `frappe.utils.getdate` accepts (which includes datetimes
	and single-digit months) and normalises it, so downstream comparisons are
	always string-comparable ISO dates.
	"""
	raw = args.get(key)
	if raw is None or raw == "":
		if required:
			raise ToolError(f"{key} is required (YYYY-MM-DD)")
		return None
	try:
		return frappe.utils.getdate(raw).isoformat()
	except Exception:
		raise ToolError(f"{key} must be a date as YYYY-MM-DD, got {raw!r}") from None


def as_int(args: dict, key: str, default: int | None = None) -> int | None:
	raw = args.get(key)
	if raw is None or raw == "":
		return default
	try:
		return int(raw)
	except (TypeError, ValueError):
		raise ToolError(f"{key} must be a whole number, got {raw!r}") from None


def as_float(value, key: str) -> float:
	if value is None or value == "":
		return 0.0
	try:
		return float(value)
	except (TypeError, ValueError):
		raise ToolError(f"{key} must be a number, got {value!r}") from None


def as_limit(args: dict, key: str = "limit") -> int:
	"""A row limit, clamped to [1, MAX_LIMIT].

	An explicit 0 clamps to 1 rather than falling back to the default: `x or
	DEFAULT` reads nicely and quietly discards a caller's real answer, which is
	the wrong trade when the caller is a model that may have meant it.
	"""
	value = as_int(args, key, DEFAULT_LIMIT)
	if value is None:
		value = DEFAULT_LIMIT
	return max(1, min(MAX_LIMIT, value))


def as_docstatus(args: dict, key: str = "docstatus") -> int | None:
	"""0 (draft), 1 (submitted) or 2 (cancelled) — or None for "any".

	Also accepts the words, since a model is at least as likely to say
	`"submitted"` as `1`.
	"""
	raw = args.get(key)
	if raw is None or raw == "":
		return None
	words = {"draft": 0, "submitted": 1, "cancelled": 2, "canceled": 2}
	if isinstance(raw, str) and raw.strip().lower() in words:
		return words[raw.strip().lower()]
	value = as_int(args, key)
	if value not in (0, 1, 2):
		raise ToolError(f"{key} must be 0 (draft), 1 (submitted) or 2 (cancelled), got {raw!r}")
	return value


def resolve_company(company: str = "", required: bool = False) -> str | None:
	"""A Company docname, inferred when the site leaves no ambiguity.

	Empty input on a single-company site returns that company. On a
	multi-company site it raises with the list of names, which is a far more
	useful reply than a wrong default silently applied to a Journal Entry.
	"""
	company = (company or "").strip()
	if company:
		if not frappe.db.exists("Company", company):
			match = frappe.db.get_value("Company", {"abbr": company}, "name")
			if match:
				return match
			raise ToolError(
				f"no Company named {company!r} on this site. "
				f"Known companies: {', '.join(_company_names()) or '<none>'}"
			)
		return company
	names = _company_names()
	if len(names) == 1:
		return names[0]
	if required:
		raise ToolError(
			f"company is required on a multi-company site. Known companies: {', '.join(names) or '<none>'}"
		)
	return None


def resolve_account(account: str, company: str = "") -> str:
	"""An Account docname from a docname, an account number, or an account name.

	ERPNext's Account primary key is `"<name> - <company abbr>"`, which a model
	rarely reproduces exactly. Resolution order is most-specific first:

	  1. exact docname
	  2. exact `account_number` (unique per company, when the site numbers them)
	  3. exact `account_name`
	  4. case-insensitive `account_name`

	Anything ambiguous raises with the candidates listed, so the model can pick
	rather than this module guessing which "Cash" was meant.
	"""
	account = (account or "").strip()
	if not account:
		raise ToolError("account is required")
	base = {"company": company} if company else {}

	if frappe.db.exists("Account", account):
		found = frappe.db.get_value("Account", account, ["name", "company"], as_dict=True)
		if company and found and found["company"] != company:
			raise ToolError(f"account {account!r} belongs to company {found['company']!r}, not {company!r}")
		return account

	for filters in (
		{**base, "account_number": account},
		{**base, "account_name": account},
		{**base, "account_name": ("like", account)},
	):
		matches = frappe.db.get_all("Account", filters=filters, pluck="name", limit=25)
		if len(matches) == 1:
			return matches[0]
		if len(matches) > 1:
			raise ToolError(
				f"{account!r} matches {len(matches)} accounts: "
				f"{', '.join(sorted(matches)[:10])}. "
				"Pass the full account name, or set company to narrow it."
			)

	scope = f" in company {company!r}" if company else ""
	raise ToolError(f"no Account matching {account!r}{scope}. Try search_accounts to find the right name.")


def _company_names() -> list[str]:
	return sorted(frappe.db.get_all("Company", pluck="name") or [])
