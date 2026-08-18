# SPDX-License-Identifier: MIT
"""Attachment tools — the three tools in this app that check Frappe permissions.

WHY THESE ARE DIFFERENT. Every other read tool in this app uses
`frappe.db.get_all`, which does not consult role permissions: the bearer token is
the read authorization, and docs/security.md argues that case for ledger data.
Attachments are not ledger data. A File on a Frappe site is whatever somebody
uploaded — a signed contract, a passport scan, a payroll export — and `is_private`
is a promise the framework makes about who can see it. Handing that promise to
anyone holding an API token would be a different product.

So the read tools here check `read` permission on the *parent* document before
returning anything, and `get_attachment_content` additionally asks the File
doctype's own controller. An attachment with no parent is treated as private to
its owner. `attach_file_to_document` asks for `write` on the parent, which is
the permission the Desk's own attach control requires.

Note that this means the answer depends on the MCP System User's roles. That is
the point. Give it the roles it needs and no more.

SIZE. Base64 inflates by a third and a token is roughly four characters, so a
2 MiB file is on the order of 700k tokens — far past what any client will accept.
The cap here is a guard against a hung request, not a suggestion: prefer files
measured in kilobytes, and use `file_url` for anything larger.

WHAT IS NOT HARDCODED HERE. There is no list of blessed file extensions and no
list of doctypes that may be attached to. Both would be a snapshot of one site
frozen into an app installed on others, and both would refuse things the site
itself allows. Every constraint `attach_file_to_document` enforces is read off
the site at call time: the parent doctype's existence and its `max_attachments`
from `frappe.get_meta`, whatever extension allowlist System Settings declares
(nothing, on a site that declares none — which is Frappe's own answer), the
parent's `docstatus`, and Frappe's permission model. The only number this
module chooses for itself is the byte ceiling, and that is a property of a JSON
tool call rather than of the site.
"""

import base64
import hashlib
import mimetypes
import os

import frappe

from .. import compat
from ..args import as_bool, as_int, as_str, resolve_company
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
		row["size_human"] = human_size(row.get("file_size"))
		row["mime_type"] = _mime_type(row.get("file_name"))
		row["retrievable"] = int(row.get("file_size") or 0) <= DEFAULT_MAX_BYTES

	total = sum(int(row.get("file_size") or 0) for row in rows)
	data = {
		"doctype": doctype,
		"name": name,
		"attachments": rows,
		"count": len(rows),
		"total_size": total,
		"total_size_human": human_size(total),
		"note": (
			"Use get_attachment_content with an attachment's `name` to read one. "
			"`retrievable` is false for files over the default size cap — fetch "
			"those from file_url instead."
		),
	}
	return ToolResult(data, f"{doctype} {name}: {len(rows)} attachment(s), {human_size(total)}")


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
			f"{ABSOLUTE_MAX_BYTES} bytes ({human_size(ABSOLUTE_MAX_BYTES)}). "
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
			f"{doc.get('file_name')} is {human_size(declared)}, over the "
			f"{human_size(max_bytes)} cap. Raise max_bytes (hard ceiling "
			f"{human_size(ABSOLUTE_MAX_BYTES)}), or fetch it from "
			f"{doc.get('file_url')}. Base64 inflates content by a third, so "
			"anything past a few hundred kilobytes will not fit in a model's "
			"context anyway."
		)

	content = _content_bytes(doc)
	if len(content) > max_bytes:
		# file_size can be stale — a file replaced on disk, or a row imported
		# without it. The real length is the one that matters.
		raise ToolError(
			f"{doc.get('file_name')} is actually {human_size(len(content))} on "
			f"disk (its File record says {human_size(declared)}), over the "
			f"{human_size(max_bytes)} cap. Nothing was returned."
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
		"size_human": human_size(len(content)),
		"mime_type": _mime_type(doc.get("file_name")),
		"encoding": "base64",
		"content_base64": base64.b64encode(content).decode("ascii"),
	}
	return ToolResult(
		data,
		f"read attachment {doc.get('file_name')} ({human_size(len(content))}) "
		f"from {doc.get('attached_to_doctype') or '<unattached>'} "
		f"{doc.get('attached_to_name') or ''}".strip(),
	)


# ── 77. attach_file_to_document ─────────────────────────────────────────────
def attach_file_to_document(args: dict) -> ToolResult:
	"""Attach one file to any document on this site.

	THE WORKFLOW THIS EXISTS FOR. A year of brokerage statements belongs on the
	Journal Entries that book them, a receipt belongs on the Bank Transaction it
	explains, a purchase contract belongs on the Asset. Before this tool the only
	MCP path that moved bytes onto a site was `attach_governance_document`, which
	files a *new* Governance Document and attaches the file to that — useful for a
	trust instrument, useless for putting the December statement on the December
	entry. An auditor asking "what supports this posting" wants the answer on the
	posting.

	`dry_run` DEFAULTS TO FALSE, unlike `import_chart_of_accounts` and
	`run_depreciation_cycle`. Those write many documents and are hard to unpick;
	this one adds a single File and changes no balance, no docstatus and no
	existing row. Making the common case cost two round trips would be safety
	theatre. `dry_run=true` is there for the caller who wants the parent validated
	before spending the upload — a batch script should use it once to check its
	target list, then run live.

	CANCELLED PARENTS ARE REFUSED BY DEFAULT. A cancelled document is history;
	quietly growing its evidence file afterwards is how a record stops meaning
	what it says. `allow_cancelled=true` says the caller knows and meant it, which
	is a different thing from not having noticed.
	"""
	doctype = as_str(args, "doctype", required=True)
	name = as_str(args, "name", required=True)
	file_name = as_str(args, "file_name", required=True)
	file_content = as_str(args, "file_content")
	file_url = as_str(args, "file_url")
	is_private = bool(as_bool(args, "is_private", True))
	dry_run = bool(as_bool(args, "dry_run", False))
	allow_cancelled = bool(as_bool(args, "allow_cancelled", False))
	company = as_str(args, "company")

	if file_content and file_url:
		raise ToolError(
			"pass file_content (the bytes, base64) or file_url (where the file already "
			"lives), not both. Nothing was attached."
		)
	if not file_content and not file_url:
		raise ToolError(
			"one of file_content (the bytes, base64) or file_url (where the file already "
			"lives) is required — there is nothing to attach otherwise. Nothing was attached."
		)

	checks = check_attachable(
		doctype,
		name,
		file_name,
		allow_cancelled=allow_cancelled,
		company=company,
		tail="Nothing was attached.",
	)
	parent = checks["parent"]
	company_field, parent_company = checks["company_field"], checks["parent_company"]
	existing = checks["existing"]

	content = decode_base64_content(file_content, tail="Nothing was attached.") if file_content else b""
	digest = hashlib.sha256(content).hexdigest() if content else ""

	proposed = {
		"doctype": doctype,
		"name": name,
		"parent_docstatus": _docstatus(parent),
		"parent_company": parent_company,
		"company_field": company_field,
		"file_name": file_name,
		"file_size": len(content) if content else None,
		"size_human": human_size(len(content)) if content else None,
		"mime_type": _mime_type(file_name),
		"sha256": digest or None,
		"is_private": is_private,
		"source": "file_content" if content else "file_url",
		"file_url": file_url or None,
		"attachments_before": len(existing),
	}

	if dry_run:
		data = {
			**proposed,
			"dry_run": True,
			"would_attach": True,
			"note": (
				f"Nothing was written. {doctype} {name} exists, is writable, and accepts this "
				"attachment. Call again with dry_run=false to attach it."
			),
		}
		return ToolResult(
			data,
			f"dry run: would attach {file_name}"
			+ (f" ({human_size(len(content))}, sha256 {digest[:12]})" if content else f" from {file_url}")
			+ f" to {doctype} {name}",
		)

	attachment = insert_attachment(
		file_name,
		content,
		is_private=is_private,
		doctype=doctype,
		name=name,
		file_url=file_url,
	)

	stored_size = int(attachment.get("file_size") or len(content) or 0)
	data = {
		**proposed,
		"dry_run": False,
		"file": attachment.name,
		"file_url": attachment.get("file_url") or file_url,
		"file_size": stored_size or None,
		"size_human": human_size(stored_size) if stored_size else None,
		"attachments_after": len(existing) + 1,
		"attached_to_doctype": doctype,
		"attached_to_name": name,
		"note": (
			f"File {attachment.name} is linked to {doctype} {name} and will appear in "
			"list_attachments"
			+ (
				". It is PRIVATE: reading it back through get_attachment_content requires read "
				"permission on the parent document."
				if is_private
				else ". It is PUBLIC: anyone who can guess the URL can read it. Pass "
				"is_private=true unless the file is genuinely publishable."
			)
		),
		"next_step": (
			f"list_attachments on {doctype} {name} now shows {len(existing) + 1} file(s). "
			"The sha256 in this result and in the audit log is what proves the bytes on the "
			"site are the bytes that were sent."
		),
	}
	return ToolResult(
		data,
		f"attached {file_name}"
		+ (f" ({human_size(stored_size)}, sha256 {digest[:12]})" if content else f" from {file_url}")
		+ f" to {doctype} {name} as File {attachment.name}"
		+ (" (private)" if is_private else " (PUBLIC)"),
		docstatus_delta="none → 0 (File created)",
	)


def read_file_bytes(name: str) -> bytes:
	"""One File's bytes, with the same permission check `get_attachment_content` applies.

	Public because `regenerate_governance_document_pdf` needs the .docx it is
	about to convert, and it needs it as BYTES rather than as the base64 payload
	`get_attachment_content` returns — a 5 MB document round-tripped through
	base64 to be immediately decoded is a third of the memory and all of the CPU
	spent on nothing. The authorization is identical: whoever can read the parent
	document can read what hangs off it.

	No size ceiling. The one on `get_attachment_content` is about what fits in a
	model's context on the way out; nothing here leaves the server.
	"""
	if not frappe.db.exists("File", name):
		raise ToolError(f"no File named {name!r}")
	doc = frappe.get_doc("File", name)
	if doc.get("is_folder"):
		raise ToolError(f"File {name!r} is a folder, not a document")
	_authorize_file(doc)
	return _content_bytes(doc)


def read_attached_bytes_unchecked(doctype: str, name: str, file_name: str) -> bytes:
	"""ONE named file on ONE named parent, WITHOUT the Frappe read check above.

	v0.92.2, AND IT IS THE ONLY FUNCTION IN THIS FILE THAT SKIPS `_authorize_file`.
	Read the name. Everything else here honours Frappe's permission on the parent
	document, which `docs/security.md` calls the one family of tools in this app
	that does, and that rule is right for a door addressed by a File docname: a
	File id is a global handle, so the parent's permission is the only thing
	standing between a caller and any file on the site.

	WHY ONE CALLER NEEDS THE EXCEPTION. A worker's own pay stub is attached to the
	`Farm Payroll Entry` for the run, and `Farm Payroll Entry` grants `read` to
	System Manager, HR Manager and HR User and to nobody else — correctly, because
	the run holds a slip for every person on it and a picker must not read the
	crew's wages. So Frappe's parent permission cannot express the one right the
	worker actually has: not "this run", but "the single file on this run whose
	name is mine". ORS 652.610 and RCW 49.46.020 say they are owed that statement,
	and the check that proves it belongs to them is `api/mobile.get_my_pay_stub_pdf`'s,
	which resolves the employee from the LOGIN and the file name from
	`pay_stub_pdf.file_name_for` — a narrower right than a role can hold.

	WHAT MAKES IT SAFE TO EXIST IS THE SIGNATURE. It takes a parent and a file
	NAME, never a File docname, and it refuses unless exactly one file matches all
	three. A caller cannot walk the File table with it, cannot reach an unattached
	file, and cannot ask it for "whatever is on this record" — every call has to
	already know the name of the one file it is entitled to, which is the whole of
	the authorization it does not perform.

	IT IS NOT WHITELISTED AND IS ON NO TOOL SCHEMA. `registry.py` does not publish
	it and no route binds to it; it is a library function for a caller in this repo
	that has done the check itself.
	"""
	rows = frappe.db.get_all(
		"File",
		filters={
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"file_name": file_name,
		},
		fields=["name"],
		limit_page_length=2,
	) or []
	if len(rows) != 1:
		raise ToolError(
			f"{file_name!r} is not a file on {doctype} {name} — "
			f"{len(rows)} matched, and this reader takes exactly one."
		)
	doc = frappe.get_doc("File", rows[0]["name"])
	if doc.get("is_folder"):
		raise ToolError(f"File {doc.name!r} is a folder, not a document")
	return _content_bytes(doc)


def check_attachable(
	doctype: str,
	name: str,
	file_name: str,
	*,
	allow_cancelled: bool = False,
	company: str = "",
	tail: str = "Nothing was attached.",
) -> dict:
	"""Every site-read check that has to pass before a file may hang off a document.

	Public because two tools now need the same set and neither of them should own
	it. `attach_file_to_document` runs it with bytes already in hand;
	`commit_staged_file` runs it BEFORE reassembling ninety megabytes of chunks,
	which is the whole reason it is a separate function — finding out the parent
	is cancelled after building the file in memory is finding out too late.

	Returns the parent's row, whether it has a company field and what that says,
	and the attachments already on it, so callers do not each re-fetch them.
	Raises `ToolError` and writes nothing.
	"""
	parent = _require_parent_write(doctype, name, tail=tail)
	_check_docstatus(doctype, name, parent, allow_cancelled, tail=tail)
	company_field, parent_company = _check_company(doctype, name, parent, company, tail=tail)
	_check_extension(file_name, tail=tail)
	existing = _existing_attachments(doctype, name)
	_check_duplicate(doctype, name, file_name, existing, tail=tail)
	_check_attachment_limit(doctype, name, len(existing), tail=tail)
	return {
		"parent": parent,
		"company_field": company_field,
		"parent_company": parent_company,
		"existing": existing,
	}


def insert_attachment(
	file_name: str,
	content: bytes,
	*,
	is_private: bool = True,
	doctype: str = "",
	name: str = "",
	file_url: str = "",
	attached_to_field: str = "",
):
	"""Write one File, from bytes or from a URL, and return the document.

	The single place in this app that inserts a File. `content` wins over
	`file_url` when both are given, because bytes are the thing the caller
	actually has; a caller that means the URL passes no content.

	No size ceiling here on purpose. The 8 MB limit in `decode_base64_content` is
	a property of what fits in ONE JSON tool call, and a file assembled from
	chunks never travelled in one — applying it here would refuse exactly the
	uploads chunking exists to make possible.
	"""
	payload = {
		"doctype": "File",
		"file_name": file_name,
		"is_private": 1 if is_private else 0,
	}
	if doctype and name:
		payload["attached_to_doctype"] = doctype
		payload["attached_to_name"] = name
	if attached_to_field:
		payload["attached_to_field"] = attached_to_field
	if content:
		payload["content"] = content
	else:
		payload["file_url"] = file_url
	return frappe.get_doc(payload).insert()


def decode_base64_content(file_content: str, tail: str = "Nothing was created.") -> bytes:
	"""Base64 in, bytes out, refusing anything too large to be sane in a JSON call.

	`tail` is the sentence that says what did not happen, because "Nothing was
	created" and "Nothing was attached" are different promises and a caller
	deciding whether to retry needs the right one.
	"""
	# Whitespace first: MIME-style base64 arrives wrapped at 76 columns, and
	# `validate=True` — which is what makes "not base64 at all" an error rather
	# than silently-truncated bytes — counts a newline as an invalid character.
	cleaned = "".join(str(file_content).split())
	try:
		content = base64.b64decode(cleaned, validate=True)
	except Exception as exc:
		raise ToolError(
			f"file_content is not valid base64 ({type(exc).__name__}). Encode the document's "
			f"bytes as base64 without a data: prefix. {tail}"
		) from None
	if not content:
		raise ToolError(f"file_content decoded to zero bytes. {tail}")
	if len(content) > ABSOLUTE_MAX_BYTES:
		raise ToolError(
			f"the decoded document is {len(content)} bytes, over this app's "
			f"{ABSOLUTE_MAX_BYTES}-byte ceiling for content moved through a tool call. "
			f"Upload it in the Desk and record it here with file_url instead. {tail}"
		)
	return content


# ── attach-side validation, all of it read off the site ─────────────────────
def _require_parent_write(doctype: str, name: str, tail: str = "Nothing was attached.") -> dict:
	"""Refuse unless the acting user may modify the document being attached to.

	`write`, not `read`: the Desk's attach control is disabled without write
	permission, and an attachment changes what a document says about itself.
	Returns the parent's row so the callers below do not each re-fetch it.
	"""
	if not compat.doctype_exists(doctype):
		raise ToolError(
			f"no DocType named {doctype!r} on this site. {tail} "
			"get_company_topology lists what this install actually has."
		)
	if not frappe.db.exists(doctype, name):
		raise ToolError(f"no {doctype} named {name!r} on this site. {tail}")
	if not frappe.has_permission(doctype, "write", doc=name):
		raise ToolError(
			f"{frappe.session.user} is not permitted to write {doctype} {name}, so nothing "
			"may be attached to it. Attachment tools honour Frappe permissions even though "
			f"the ledger read tools do not — see docs/security.md. {tail}"
		)

	fields = compat.existing_fields(doctype, ("name", "docstatus", "company"))
	return frappe.db.get_value(doctype, name, fields, as_dict=True) or {"name": name}


def _docstatus(parent: dict):
	raw = parent.get("docstatus")
	return int(raw) if raw is not None else None


def _check_docstatus(
	doctype: str, name: str, parent: dict, allow_cancelled: bool, tail: str = "Nothing was attached."
) -> None:
	"""Kairos, not chronos: attach when the document is in a state that means it.

	A draft and a submitted document are both live records that evidence belongs
	on. A cancelled one is not — it is the record of something that was undone,
	and adding to it after the fact makes the archive say something nobody
	decided.
	"""
	if _docstatus(parent) == 2 and not allow_cancelled:
		raise ToolError(
			f"{doctype} {name} is CANCELLED (docstatus 2). Attaching to it now would grow "
			"the evidence file of a document that was undone, which is rarely what a caller "
			"means. If it is — a cancellation memo belongs on the thing cancelled — pass "
			f"allow_cancelled=true. {tail}"
		)


def _check_company(doctype: str, name: str, parent: dict, company: str, tail: str = "Nothing was attached."):
	"""Refuse a cross-company attach, and refuse to pretend to check one.

	A `company` argument this cannot evaluate is worse than no argument: the
	caller believes a guard ran. So a company passed for a doctype with no
	company field is an error, not a shrug.
	"""
	company_field = "company" if compat.has_field(doctype, "company") else None
	parent_company = parent.get("company") if company_field else None

	if not company:
		return company_field, parent_company
	if not company_field:
		raise ToolError(
			f"{doctype} has no company field on this site, so the company guard you asked "
			f"for cannot be applied to {name}. Drop the company argument if you meant to "
			f"attach anyway. {tail}"
		)
	resolved = resolve_company(company, required=True)
	if resolved != parent_company:
		raise ToolError(
			f"{doctype} {name} belongs to company {parent_company!r}, not {resolved!r}. "
			"Attaching a document from one company's books onto another's is how two sets "
			f"of records stop reconciling. {tail}"
		)
	return company_field, parent_company


def _check_extension(file_name: str, tail: str = "Nothing was attached.") -> None:
	"""Honour whatever extension allowlist this site declares, and no other.

	Frappe grew a System Settings allowlist at different points in different
	versions, and a site that declares none permits everything — so this reads
	the field if the site has it and is silent if it does not. A list compiled
	into this app would refuse uploads the site itself accepts, which is a
	false refusal wearing a safety badge.
	"""
	if not compat.has_field("System Settings", "allowed_file_extensions"):
		return
	try:
		raw = frappe.db.get_single_value("System Settings", "allowed_file_extensions")
	except Exception:
		return
	allowed = [chunk.strip().lstrip(".").lower() for chunk in str(raw or "").replace(",", "\n").split("\n")]
	allowed = [chunk for chunk in allowed if chunk]
	if not allowed:
		return
	extension = os.path.splitext(file_name)[1].lstrip(".").lower()
	if extension not in allowed:
		raise ToolError(
			f"this site's System Settings allow only these file extensions: "
			f"{', '.join(sorted(allowed))} — and {file_name!r} is "
			f"{'.' + extension if extension else 'extensionless'}. That list is the site's "
			f"own, not this app's. {tail}"
		)


def _existing_attachments(doctype: str, name: str) -> list:
	return frappe.db.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": name},
		fields=compat.existing_fields("File", ("name", "file_name", "file_size")),
		limit=1000,
	)


def _check_duplicate(
	doctype: str, name: str, file_name: str, existing: list, tail: str = "Nothing was attached."
) -> None:
	"""One filename per document. A re-run of a batch attach says so and stops.

	The anticipated caller is a script walking a year of statements onto their
	entries. Half of it failing and being re-run is the normal case, and the
	useful answer to "attach 2025-12.pdf" when 2025-12.pdf is already there is
	"that one is done", named precisely enough to skip — not a second copy that
	makes somebody later ask which is authoritative.
	"""
	clash = next((row for row in existing if str(row.get("file_name") or "") == file_name), None)
	if clash:
		raise ToolError(
			f"{doctype} {name} already has an attachment named {file_name!r} "
			f"(File {clash['name']}). Two files with one name on one document is a question "
			"nobody can answer later. If this is a re-run, that one is already done; if the "
			f"contents differ, give this one a name that says how. {tail}"
		)


def _check_attachment_limit(
	doctype: str, name: str, current: int, tail: str = "Nothing was attached."
) -> None:
	"""The parent doctype's own `max_attachments`, whatever this site set it to."""
	try:
		limit = int(getattr(frappe.get_meta(doctype), "max_attachments", 0) or 0)
	except Exception:
		return
	if limit and current >= limit:
		raise ToolError(
			f"{doctype} allows at most {limit} attachment(s) on this site and {name} already "
			f"has {current}. That limit is set on the DocType, not by this app — raise it in "
			f"the Desk or remove an attachment first. {tail}"
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


def human_size(size) -> str:
	size = float(size or 0)
	for unit in ("B", "KB", "MB", "GB"):
		if size < 1024 or unit == "GB":
			return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
		size /= 1024
	return f"{size:.1f} GB"
