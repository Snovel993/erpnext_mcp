# SPDX-License-Identifier: MIT
"""Every call gets a row in MCP Action Log. No exceptions, including the
rejected ones.

WHY READS ARE LOGGED TOO. A read tool cannot corrupt the ledger, but the
interesting question after the fact is rarely "what did it change" — it is
"what did it see". A log that only records mutations cannot answer whether an
AI client enumerated every account before it was switched off, so this one
records the lot.

SURVIVING A ROLLBACK. A Frappe request is one transaction. If a mutating tool
half-wrote a Journal Entry and then raised, the caller must roll that back — and
a naive audit row inserted before the rollback would go with it, losing exactly
the record you most want. So the contract is: `mcp.py` rolls back FIRST, then
calls `record(..., commit=True)`, so the failure row is inserted into a clean
transaction and committed on its own. This module never rolls back on its own
behalf; doing so would discard a caller's still-valid work.

BEST EFFORT, NEVER FATAL. An audit write that fails must not turn a working
tool call into a 500, and must not swallow the real error either. Failures here
go to the site's Error Log and are otherwise ignored.

REDACTION. Arguments are logged verbatim except for keys that name a secret —
no current tool takes one, but a future tool might, and a log that is read-only
forever is the wrong place to discover that.
"""

import json

import frappe

from .compat import traceback_text

LOG_DOCTYPE = "MCP Action Log"

STATUS_SUCCESS = "Success"
STATUS_ERROR = "Error"
STATUS_BLOCKED = "Blocked"
STATUS_UNAUTHORIZED = "Unauthorized"

#: Column widths in the doctype. Truncating here rather than letting MariaDB
#: do it means the log says it truncated.
_MAX_ARGS = 8000
_MAX_SUMMARY = 2000

_SECRET_HINTS = ("token", "password", "secret", "api_key", "apikey", "credential")


def record(
	tool_name: str,
	arguments: dict | None = None,
	status: str = STATUS_SUCCESS,
	summary: str = "",
	docstatus_delta: str = "",
	caller_ip: str = "",
	commit: bool = False,
) -> str | None:
	"""Insert one audit row. Returns its name, or None if the write failed.

	`commit=True` is for rows that must outlive a rolled-back transaction; see
	the module docstring.
	"""
	try:
		doc = frappe.get_doc(
			{
				"doctype": LOG_DOCTYPE,
				"timestamp": frappe.utils.now(),
				"tool_name": (tool_name or "<none>")[:140],
				"caller_ip": (caller_ip or "")[:140],
				"arguments_json": _arguments_json(arguments),
				"result_status": status,
				"result_summary": _truncate(summary or "", _MAX_SUMMARY),
				"docstatus_delta": (docstatus_delta or "")[:140],
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		if commit:
			frappe.db.commit()
		return doc.name
	except Exception:
		# Deliberately swallowed: see module docstring. frappe.log_error opens
		# its own transaction, so this survives even a poisoned one.
		try:
			frappe.log_error(
				title="erpnext_mcp: audit log write failed",
				message=traceback_text(),
			)
		except Exception:
			pass
		return None


def _arguments_json(arguments: dict | None) -> str:
	"""Arguments as JSON, secrets masked, truncated to fit the column."""
	safe = {}
	for key, value in (arguments or {}).items():
		lowered = str(key).lower()
		if any(hint in lowered for hint in _SECRET_HINTS):
			safe[key] = "***redacted***"
		else:
			safe[key] = value
	try:
		text = json.dumps(safe, default=str, sort_keys=True)
	except Exception:
		text = repr(safe)
	return _truncate(text, _MAX_ARGS)


def _truncate(text: str, limit: int) -> str:
	if len(text) <= limit:
		return text
	return text[: limit - 15] + "…[truncated]"
