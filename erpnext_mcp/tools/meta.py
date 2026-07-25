# SPDX-License-Identifier: MIT
"""Site-customisation tools, for the Frappe dev debugging their own site.

These answer the two questions that eat an afternoon: "why is my custom field
not showing up" and "what is that script doing to this form". Both are usually
answerable in thirty seconds if you can see the rows, and both are usually asked
by somebody who cannot get to the Desk right now.

CLIENT SCRIPTS ARE TRUNCATED, ON PURPOSE. A site can carry thousands of lines of
form JavaScript, and dumping it into a model's context is expensive and rarely
what was wanted — the useful facts are which DocType, which view, and whether it
is enabled. The first 500 characters are enough to recognise a script; the
response says how much was cut and where to read the rest.
"""

import frappe

from .. import compat
from ..args import as_limit, as_str
from ..errors import ToolError
from ..result import ToolResult

#: How much of a Client Script's body to include.
SCRIPT_PREVIEW_CHARS = 500

#: "Client Script" since v13; "Custom Script" before that.
SCRIPT_DOCTYPES = ("Client Script", "Custom Script")


# ── 34. list_custom_fields ──────────────────────────────────────────────────
def list_custom_fields(args: dict) -> ToolResult:
	"""Custom Fields on the site, optionally for one DocType.

	Ordered by target DocType then by `idx`, so the output reads in the order the
	fields appear on the form — which is the order the question is usually about.
	"""
	compat.require_doctype("Custom Field")
	doctype = as_str(args, "doctype")
	limit = as_limit(args)

	filters = {}
	if doctype:
		if not compat.doctype_exists(doctype):
			raise ToolError(f"no DocType named {doctype!r} on this site")
		filters["dt"] = doctype

	fields = compat.existing_fields(
		"Custom Field",
		[
			"name",
			"dt",
			"fieldname",
			"label",
			"fieldtype",
			"options",
			"insert_after",
			"idx",
			"reqd",
			"hidden",
			"read_only",
			"in_list_view",
			"in_standard_filter",
			"depends_on",
			"default",
			"description",
			"module",
			"owner",
			"modified",
		],
	)
	rows = frappe.db.get_all(
		"Custom Field", filters=filters, fields=fields, order_by="dt asc, idx asc", limit=limit
	)

	by_doctype = {}
	for row in rows:
		key = row.get("dt") or "<none>"
		by_doctype[key] = by_doctype.get(key, 0) + 1

	data = {
		"custom_fields": rows,
		"count": len(rows),
		"limit": limit,
		"truncated": len(rows) == limit,
		"by_doctype": by_doctype,
		"doctype_filter": doctype or None,
		"note": (
			"`dt` is the DocType the field was added to and `insert_after` is the "
			"field it sits below. A field that will not appear is usually hidden, "
			"gated by depends_on, or inserted after a fieldname that does not "
			"exist on this version."
		),
	}
	scope = f" on {doctype}" if doctype else ""
	return ToolResult(data, f"{len(rows)} custom field(s){scope}")


# ── 35. list_client_scripts ─────────────────────────────────────────────────
def list_client_scripts(args: dict) -> ToolResult:
	"""Client Scripts, enabled ones by default, with a preview of each body."""
	script_doctype = _script_doctype()
	doctype = as_str(args, "doctype")
	raw_enabled = args.get("enabled", True)
	limit = as_limit(args)

	filters = {}
	if doctype:
		if not compat.doctype_exists(doctype):
			raise ToolError(f"no DocType named {doctype!r} on this site")
		filters["dt"] = doctype
	enabled = _tristate(raw_enabled)
	if enabled is not None and compat.has_field(script_doctype, "enabled"):
		filters["enabled"] = 1 if enabled else 0

	fields = compat.existing_fields(
		script_doctype,
		["name", "dt", "view", "enabled", "script", "script_type", "module", "owner", "modified"],
	)
	rows = frappe.db.get_all(
		script_doctype, filters=filters, fields=fields, order_by="dt asc, name asc", limit=limit
	)

	for row in rows:
		body = row.pop("script", None) or ""
		row["script_length"] = len(body)
		row["script_preview"] = body[:SCRIPT_PREVIEW_CHARS]
		row["script_truncated"] = len(body) > SCRIPT_PREVIEW_CHARS

	data = {
		"client_scripts": rows,
		"count": len(rows),
		"limit": limit,
		"truncated": len(rows) == limit,
		"source_doctype": script_doctype,
		"preview_chars": SCRIPT_PREVIEW_CHARS,
		"filters": {"doctype": doctype or None, "enabled": enabled},
		"note": (
			f"script_preview is the first {SCRIPT_PREVIEW_CHARS} characters; "
			"script_length is the real size. Read the whole body in the Desk at "
			f"/app/{frappe.scrub(script_doctype).replace('_', '-')}/<name>."
		),
	}
	scope = f" on {doctype}" if doctype else ""
	return ToolResult(data, f"{len(rows)} client script(s){scope}")


def _script_doctype() -> str:
	for candidate in SCRIPT_DOCTYPES:
		if compat.doctype_exists(candidate):
			return candidate
	raise ToolError(
		f"this site has neither of {' nor '.join(SCRIPT_DOCTYPES)}, so there are no form scripts to list"
	)


def _tristate(raw):
	"""True, False, or None for "either" — because a caller may want both."""
	if raw is None or raw == "":
		return None
	if isinstance(raw, bool):
		return raw
	text = str(raw).strip().lower()
	if text in ("1", "true", "yes"):
		return True
	if text in ("0", "false", "no"):
		return False
	if text in ("any", "all", "both"):
		return None
	raise ToolError(f"enabled must be true, false, or 'any', got {raw!r}")
