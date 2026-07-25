# SPDX-License-Identifier: MIT
"""Read access to the "ERPNext MCP Settings" Single doctype.

Every gate in this app resolves through here, so there is exactly one place
that decides what "enabled" means. Three properties of this module matter:

MISSING IS NOT OFF. A Frappe Single stores one row per *set* field in
`tabSingles`; a field nobody has saved yet has no row at all, so a naive read
returns None. For the read-tool switches — which default ON — treating None as
"off" would silently disable the whole read surface on a fresh install. So
`tool_enabled()` falls back to the field's declared default from the DocType
meta whenever no value is stored. `seed_defaults()` (run on install and by a
patch after upgrades) writes the values out properly; the meta fallback is the
belt to that braces, and it is what makes adding a new tool in a later version
safe without a migration.

"0" IS NOT ON. `tabSingles.value` is a text column, so depending on the Frappe
version a Check field on a Single comes back as the *string* `"0"` rather than
the integer 0 — and `bool("0")` is True. Reading a switch with a bare `bool()`
would therefore report every disabled tool as enabled, including the master
switch, which is about the worst possible direction for that bug to fail in.
Every boolean here goes through `as_bool` — public, because the doctype
controller has to make the same judgement about the same values — which treats
"0", "", "false" and None as off. This is not defensive padding: it is the difference between a
fresh install being inert and a fresh install answering with write tools live.

THE TOKEN IS NEVER RETURNED TO A CALLER. `auth_token()` exists only for the
constant-time comparison in `security.py`. Nothing logs it, nothing renders it,
and the doctype stores it in a Password field so it is encrypted at rest and
excluded from `frappe.get_doc().as_dict()` output.

READS ARE CACHED. `frappe.get_cached_doc` on a Single is a redis hit, and the
framework invalidates it on save, so the per-tool switch check costs nothing
even though it runs on every single call.
"""

import frappe

SETTINGS_DOCTYPE = "ERPNext MCP Settings"

#: The Frappe user a mutation is attributed to when `require_user_context` is
#: off, or when it is on but no MCP System User has been chosen.
FALLBACK_USER = "Administrator"

#: Sensible LAN defaults: loopback (v4 and v6) plus the three RFC1918 blocks.
#: Deliberately NOT 0.0.0.0/0 — an operator who wants the endpoint reachable
#: from anywhere has to type that themselves.
#:
#: `::1/128` is in the list because it has to be: on a modern host `localhost`
#: resolves to IPv6 first, so a first-run `curl http://localhost/api/method/...`
#: arrives from `::1` and would otherwise be denied by a list that "obviously"
#: allows loopback. Sites that reach the endpoint over IPv6 from elsewhere on
#: the LAN need to add their own ULA prefix (typically `fd00::/8`).
DEFAULT_CIDRS = "127.0.0.1/32,::1/128,10.0.0.0/8,192.168.0.0/16,172.16.0.0/12"


def get_settings():
	"""The settings document. Cached; safe to call repeatedly per request."""
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def is_enabled() -> bool:
	"""The master switch. Off → the endpoint behaves as if it does not exist.

	This is the "turn everything off right now" control: one checkbox, no
	restart, no token rotation, takes effect on the next request.
	"""
	return as_bool(_value("enabled"))


def auth_token() -> str:
	"""The configured bearer token, or "" when none has been generated.

	Never log, echo, or include this in a tool result.
	"""
	try:
		token = get_settings().get_password("auth_token", raise_exception=False)
	except Exception:
		# A Password field with no stored value, or an undecryptable one on a
		# site whose encryption key was rotated. Either way: no valid token,
		# which fails closed in security.py.
		return ""
	return (token or "").strip()


def allowed_cidrs() -> list[str]:
	"""The network allowlist as a list of CIDR strings, comments stripped.

	An empty list means "no allowlist configured", which `security.py` treats
	as deny-all rather than allow-all.
	"""
	raw = _value("allowed_cidrs")
	if raw is None:
		raw = DEFAULT_CIDRS
	out = []
	for chunk in str(raw).replace("\n", ",").split(","):
		entry = chunk.split("#", 1)[0].strip()
		if entry:
			out.append(entry)
	return out


def tool_enabled(tool_name: str) -> bool:
	"""Whether the `allow_<tool_name>` switch is on for this tool.

	Read tools ship with the switch on, mutating tools with it off; both are
	honoured identically here, so an operator can disable a read tool for the
	same reason they can enable a write one.
	"""
	return as_bool(_value(f"allow_{tool_name}"))


def require_user_context() -> bool:
	return as_bool(_value("require_user_context"))


def effective_user() -> str:
	"""The Frappe user mutations run as, and are attributed to in `owner`.

	With `require_user_context` on (the default) this is the configured MCP
	System User, so every document the AI touches is traceable to a principal
	that is not a human and whose Role Profile the operator controls. With it
	off — or with no user chosen — it falls back to Administrator, which works
	but makes MCP-authored documents indistinguishable from an admin's own.
	See docs/security.md.
	"""
	if require_user_context():
		user = (_value("mcp_system_user") or "").strip()
		if user and frappe.db.exists("User", user):
			return user
	return FALLBACK_USER


def seed_defaults() -> None:
	"""Write every unset field's declared default into `tabSingles`.

	Idempotent, and safe to run on every migrate: it only fills in fields that
	have no stored value, so an operator's choices are never overwritten. This
	is what makes "read tools default ON" true in the database rather than only
	in the DocType JSON.
	"""
	doc = frappe.get_doc(SETTINGS_DOCTYPE)
	present = set(stored_fieldnames())
	changed = False
	for field in frappe.get_meta(SETTINGS_DOCTYPE).fields:
		if field.fieldtype in ("Section Break", "Column Break", "HTML", "Tab Break"):
			continue
		if field.fieldname in present or field.default in (None, ""):
			continue
		doc.set(field.fieldname, field.default)
		changed = True
	if changed:
		doc.flags.ignore_permissions = True
		doc.save()


def stored_fieldnames() -> set:
	"""Which of this Single's fields actually have a row in `tabSingles`.

	NEVER query `tabSingles` with `frappe.db.get_value` / `get_values` and no
	explicit `order_by`. Both default to ordering by `modified`, and `tabSingles`
	has three columns — `doctype`, `field`, `value` — so the query dies with
	`Unknown column 'modified' in 'ORDER BY'`. It is not a DocType table and does
	not get the framework columns. That is exactly how v0.2.0 shipped an
	`after_migrate` hook that crashed `bench migrate` on a real site: the default
	argument is invisible at the call site, and the standalone test double was
	happy to answer a query MariaDB refuses.

	`frappe.db.get_singles_dict` is the framework's own accessor for this table.
	It issues its own `SELECT field, value FROM tabSingles WHERE doctype = %s`
	with no ORDER BY at all, so there is no default to get wrong — which is the
	real reason to prefer it over passing `order_by=None` to the generic helper.
	"""
	return set(frappe.db.get_singles_dict(SETTINGS_DOCTYPE) or {})


def as_bool(value) -> bool:
	"""Frappe-safe truthiness for a Check field. See the module docstring.

	Public so the settings controller can use it: a form that read `"0"` as True
	would refuse to save a perfectly valid disabled configuration.

	The cases that matter are the string forms: `"0"` must be False even though
	Python says a non-empty string is truthy.
	"""
	if value is None or isinstance(value, bool):
		return bool(value)
	if isinstance(value, (int, float)):
		return bool(value)
	return str(value).strip().lower() not in ("", "0", "false", "no", "none")


def _value(fieldname: str):
	"""One settings value, falling back to the DocType's declared default.

	Returns None only when the field neither has a stored value nor a default —
	i.e. genuinely unconfigured, such as `auth_token` before the operator has
	pressed Generate.
	"""
	try:
		doc = get_settings()
	except Exception:
		# The doctype isn't migrated yet (a request landing mid-`bench migrate`).
		# Fail closed: every gate reads falsy.
		return None
	if doc.get(fieldname) is not None:
		return doc.get(fieldname)
	return _meta_default(fieldname)


def _meta_default(fieldname: str):
	try:
		field = frappe.get_meta(SETTINGS_DOCTYPE).get_field(fieldname)
	except Exception:
		return None
	if field is None or field.default in (None, ""):
		return None
	if field.fieldtype == "Check":
		return int(field.default or 0)
	return field.default
