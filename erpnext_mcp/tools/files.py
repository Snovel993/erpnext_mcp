# SPDX-License-Identifier: MIT
"""Attachment tools — the two tools in this app that check Frappe permissions.

WHY THESE ARE DIFFERENT. Every other read tool in this app uses
`frappe.db.get_all`, which does not consult role permissions: the bearer token is
the read authorization, and docs/security.md argues that case for ledger data.
Attachments are not ledger data. A File on a Frappe site is whatever somebody
uploaded — a signed contract, a passport scan, a payroll export — and `is_private`
is a promise the framework makes about who can see it. Handing that promise to
anyone holding an API token would be a different product.

So both tools here check `read` permission on the *parent* document before
returning anything, and `get_attachment_content` additionally asks the File
doctype's own controller. An attachment with no parent is treated as private to
its owner.

Note that this means the answer depends on the MCP System User's roles. That is
the point. Give it the roles it needs and no more.

SIZE. Base64 inflates by a third and a token is roughly four characters, so a
2 MiB file is on the order of 700k tokens — far past what any client will accept.
The cap here is a guard against a hung request, not a suggestion: prefer files
measured in kilobytes, and use `file_url` for anything larger.
"""

import base64
import mimetypes

import frappe

from .. import compat
from ..args import as_int, as_str
from ..errors import ToolError
from ..result import ToolResult

#: Default ceiling on returned content, in bytes.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024

#: Hard ceiling, whatever `max_bytes` asks for. Past this the response is larger
#: than any MCP client's context however the caller feels about it.
ABSOLUTE_MAX_BYTES = 8 * 1024 * 1024


# ── 23. list_attachments ────────────────────────────────────────────────────
def list_attachments(args: dict) -> ToolResult:
	"""Every File attached to one document."""
	doctype = as_str(args, "doctype", required=True)
	name = as_str(args, "name", required=True)
	_require_parent_read(doctype, name)

	fields = compat.existing_fields(
		"File",
		[
			"name",
			"file_name",
			"file_url",
			"file_size",
			"is_private",
			"owner",
			"creation",
			"content_hash",
			"attached_to_field",
			"folder",
		],
	)
	rows = frappe.db.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": name},
		fields=fields,
		order_by="creation desc",
	)
	for row in rows:
		row["uploaded_by"] = row.get("owner")
		row["uploaded_on"] = row.get("creation")
		row["size_human"] = _human_size(row.get("file_size"))
		row["mime_type"] = _mime_type(row.get("file_name"))
		row["retrievable"] = int(row.get("file_size") or 0) <= DEFAULT_MAX_BYTES

	total = sum(int(row.get("file_size") or 0) for row in rows)
	data = {
		"doctype": doctype,
		"name": name,
		"attachments": rows,
		"count": len(rows),
		"total_size": total,
		"total_size_human": _human_size(total),
		"note": (
			"Use get_attachment_content with an attachment's `name` to read one. "
			"`retrievable` is false for files over the default size cap — fetch "
			"those from file_url instead."
		),
	}
	return ToolResult(data, f"{doctype} {name}: {len(rows)} attachment(s), {_human_size(total)}")


# ── 24. get_attachment_content ──────────────────────────────────────────────
def get_attachment_content(args: dict) -> ToolResult:
	"""One attachment's bytes, base64-encoded, if it is small enough and allowed."""
	name = as_str(args, "name", required=True)
	# Not `as_int(...) or DEFAULT`: that idiom turns an explicit 0 back into the
	# default, so a caller asking for something impossible gets a silent success
	# instead of the refusal below.
	max_bytes = as_int(args, "max_bytes", DEFAULT_MAX_BYTES)
	if max_bytes is None:
		max_bytes = DEFAULT_MAX_BYTES
	if max_bytes <= 0:
		raise ToolError("max_bytes must be positive")
	if max_bytes > ABSOLUTE_MAX_BYTES:
		raise ToolError(
			f"max_bytes {max_bytes} exceeds this tool's hard ceiling of "
			f"{ABSOLUTE_MAX_BYTES} bytes ({_human_size(ABSOLUTE_MAX_BYTES)}). "
			"Fetch larger files from their file_url."
		)

	if not frappe.db.exists("File", name):
		raise ToolError(
			f"no File named {name!r}. Note this is the File docname, not the "
			"filename — use list_attachments to get it."
		)
	doc = frappe.get_doc("File", name)
	if doc.get("is_folder"):
		raise ToolError(f"File {name!r} is a folder, not a document")
	_authorize_file(doc)

	declared = int(doc.get("file_size") or 0)
	if declared > max_bytes:
		raise ToolError(
			f"{doc.get('file_name')} is {_human_size(declared)}, over the "
			f"{_human_size(max_bytes)} cap. Raise max_bytes (hard ceiling "
			f"{_human_size(ABSOLUTE_MAX_BYTES)}), or fetch it from "
			f"{doc.get('file_url')}. Base64 inflates content by a third, so "
			"anything past a few hundred kilobytes will not fit in a model's "
			"context anyway."
		)

	content = _content_bytes(doc)
	if len(content) > max_bytes:
		# file_size can be stale — a file replaced on disk, or a row imported
		# without it. The real length is the one that matters.
		raise ToolError(
			f"{doc.get('file_name')} is actually {_human_size(len(content))} on "
			f"disk (its File record says {_human_size(declared)}), over the "
			f"{_human_size(max_bytes)} cap. Nothing was returned."
		)

	data = {
		"name": doc.name,
		"file_name": doc.get("file_name"),
		"file_url": doc.get("file_url"),
		"is_private": bool(doc.get("is_private")),
		"attached_to_doctype": doc.get("attached_to_doctype"),
		"attached_to_name": doc.get("attached_to_name"),
		"uploaded_by": doc.get("owner"),
		"uploaded_on": str(doc.get("creation")),
		"file_size": len(content),
		"size_human": _human_size(len(content)),
		"mime_type": _mime_type(doc.get("file_name")),
		"encoding": "base64",
		"content_base64": base64.b64encode(content).decode("ascii"),
	}
	return ToolResult(
		data,
		f"read attachment {doc.get('file_name')} ({_human_size(len(content))}) "
		f"from {doc.get('attached_to_doctype') or '<unattached>'} "
		f"{doc.get('attached_to_name') or ''}".strip(),
	)


# ── permission ──────────────────────────────────────────────────────────────
def _require_parent_read(doctype: str, name: str) -> None:
	"""Refuse unless the acting user may read the document being asked about."""
	if not compat.doctype_exists(doctype):
		raise ToolError(f"no DocType named {doctype!r} on this site")
	if not frappe.db.exists(doctype, name):
		raise ToolError(f"no {doctype} named {name!r}")
	if not frappe.has_permission(doctype, "read", doc=name):
		raise ToolError(
			f"{frappe.session.user} is not permitted to read {doctype} {name}, so "
			"its attachments are not available. Attachment tools honour Frappe "
			"permissions even though the ledger read tools do not — see "
			"docs/security.md."
		)


def _authorize_file(doc) -> None:
	"""Three checks, because a File can be reached three ways.

	Attached to a document: the parent's `read` permission decides, which is the
	same rule the Desk applies. Unattached and private: only its owner or a
	System Manager, because there is no parent to inherit a decision from and
	"somebody uploaded this outside a document" is not an invitation. Public and
	unattached: readable, which is what public means.

	The File doctype's own `has_permission` is consulted as well where the
	version implements one, so a site that has customised file access keeps its
	customisation.
	"""
	parent_doctype = doc.get("attached_to_doctype")
	parent_name = doc.get("attached_to_name")

	if parent_doctype and parent_name:
		if not compat.doctype_exists(parent_doctype):
			raise ToolError(
				f"File {doc.name} is attached to {parent_doctype}, which is not installed on this site"
			)
		if not frappe.has_permission(parent_doctype, "read", doc=parent_name):
			raise ToolError(
				f"{frappe.session.user} is not permitted to read "
				f"{parent_doctype} {parent_name}, which this file is attached to."
			)
	elif doc.get("is_private"):
		roles = set(frappe.get_roles(frappe.session.user) or [])
		if doc.get("owner") != frappe.session.user and "System Manager" not in roles:
			raise ToolError(
				f"File {doc.name} is private and attached to no document, so only "
				"its owner or a System Manager may read it."
			)

	if not frappe.has_permission("File", "read", doc=doc):
		raise ToolError(f"{frappe.session.user} is not permitted to read File {doc.name}.")


def _content_bytes(doc) -> bytes:
	"""The file's bytes, whichever type this Frappe version's accessor returns."""
	try:
		content = doc.get_content()
	except Exception as exc:
		raise ToolError(
			f"could not read {doc.get('file_name')!r} from storage: "
			f"{type(exc).__name__}: {exc}. The File record may point at a file "
			"that no longer exists on disk."
		) from exc
	if isinstance(content, str):
		return content.encode("utf-8", errors="replace")
	return bytes(content or b"")


def _mime_type(file_name) -> str | None:
	if not file_name:
		return None
	return mimetypes.guess_type(str(file_name))[0]


def _human_size(size) -> str:
	size = float(size or 0)
	for unit in ("B", "KB", "MB", "GB"):
		if size < 1024 or unit == "GB":
			return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
		size /= 1024
	return f"{size:.1f} GB"
