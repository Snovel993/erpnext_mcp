# SPDX-License-Identifier: MIT
"""Collecting the signature a compliance alert said was missing. v0.55.0.

THE OTHER END OF `i9_section_1_unsigned` AND ITS THREE SIBLINGS. Those rules
find the boxes nobody signed; `generate_tasks_from_compliance_alerts` turns each
into a Farm Task held by the person who can fix it; and this is what that person
calls when they have got the signature. Without it the loop stops one step short
— the app would be very good at telling an operation which forms are unsigned
and would offer no way to sign one.

────────────────────────────────────────────────────────────────────────────
ONE CALL DOES FOUR THINGS, AND THE ORDER IS THE WHOLE OF THE DESIGN
────────────────────────────────────────────────────────────────────────────

    store the image  →  write it onto the form  →  close the task  →  redraw the PDF

EACH STEP IS ALLOWED TO FAIL WITHOUT UNDOING THE ONE BEFORE IT, and the result
says which of the four happened. That asymmetry is deliberate and it runs the
right way round: the signature IS the compliance artefact, and the task and the
PDF are bookkeeping about it. A phone in an orchard that captured a signature,
wrote it to the federal record, and then could not reach the PDF renderer has
NOT failed — it has done the part §274a asks for. Rolling that back to keep four
things tidy would throw away the only irreplaceable one: the person who signed
has gone back to work and is not signing again.

The reverse ordering is what would be wrong. Closing the task first and then
failing to store the image would leave a board saying the signature was
collected and a form saying it was not.

────────────────────────────────────────────────────────────────────────────
WHY THIS ONE TAKES BASE64 WHEN `attach_signed_i9` REFUSES TO
────────────────────────────────────────────────────────────────────────────

`attach_signed_i9` says, at length, that no bytes cross its boundary: a signed
I-9 is a SCAN OF A PAGE, arriving as a photograph or a multi-megabyte PDF, and
it goes up through `stage_file_chunk` / `finalize_staged_file` — 512 KB at a
time, hashed at capture, resumable over a link that drops. A second upload path
with its own size limit and its own way of failing halfway up a hill is exactly
what that refusal is protecting.

A SIGNATURE CAPTURE IS NOT THAT FILE. It is what a finger drew on a glass
rectangle: a few kilobytes of monochrome PNG, produced in one gesture, complete
or not at all. Chunking it would be three round trips and a staging session to
move less data than the JSON body around it, and every one of those round trips
is another place for a signature to be lost between the drawing and the storing
— with the person who drew it already walking away.

So both doors are open here and the ceiling says which is which:
`SIGNATURE_MAX_BYTES` is 512 KB, a hundredfold more than a real capture and far
short of a scanned page. Anything larger is a scan, and a scan belongs on
`attach_signed_i9` with the chunked upload — which the refusal says by name.
`file_token` is accepted too, for the caller that already has one.

────────────────────────────────────────────────────────────────────────────
THE FIELD IS NAMED BY THE CALLER AND CHECKED AGAINST A TABLE
────────────────────────────────────────────────────────────────────────────

`SIGNATURE_BOXES` is a closed registry of (doctype, field) pairs, and it is
closed for the reason `alerts/rules.SCANNERS` is: "write this image into that
column" is one sentence away from "write anything into any column". A caller
naming a field outside it is refused with the list — an endpoint taking an
arbitrary fieldname on an arbitrary doctype is an arbitrary write with an
Attach-shaped hat on.

Each entry also carries what has to be TRUE beside the image: the timestamp
column, the IP column, and — for the two employer-side boxes — that the caller
is on the authorized-signer roster. A signature image on its own is not an
electronic signature; 8 CFR 274a.2(h) asks for the record of who made it and
when, and this is where that record gets written.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field

import frappe

from .. import compat
from ..args import as_bool, as_str
from ..errors import ToolError
from ..result import ToolResult
from . import files, i9, signers, w4

I9_FORM = "I-9 Form"
W4_FORM = "W-4 Form"
FARM_TASK = "Farm Task"

#: The most a signature capture may be, in bytes. See the module docstring: it
#: is a hundredfold over a real capture and an order of magnitude under a
#: scanned page, so it separates the two transports rather than merely limiting
#: one of them.
SIGNATURE_MAX_BYTES = 512 * 1024

#: What a capture is allowed to arrive as. PNG is what every signature pad in
#: this app's iOS build produces; JPEG is what a re-encode produces. `.svg` is
#: absent for the reason `api/files.py` gives — it executes script when served —
#: and a signature is the last file on this site that should.
SIGNATURE_EXTENSIONS = (".png", ".jpg", ".jpeg")

#: Suffix on a stored capture's filename, so a private files directory can be
#: read by eye. The docname and the field are in it because one form can carry
#: four of these and "which box is this" should not need a database.
FILE_STEM = "signature"


@dataclass(frozen=True)
class SignatureBox:
	"""One box that can be signed, and everything true about signing it.

	`form_type` is empty on the two EMPLOYEE boxes and set on the two employer
	boxes, and that single field is the whole roster gate: Section 1 and the W-4
	are signed by the worker themselves, who is on nobody's roster and should not
	need to be, while Section 2 and Supplement B are the employer attesting and
	`tools/signers.py` holds who may do that.
	"""

	doctype: str
	field: str
	label: str
	#: Where the moment is written. Required — see the module docstring on
	#: 274a.2(h): the image without the timestamp is not the record.
	signed_at_field: str
	#: Where the address it arrived from is written, if the doctype has one.
	signed_ip_field: str = ""
	#: "I-9" or "W-4" where the roster gates this box; empty where it does not.
	form_type: str = ""
	#: The child table this box lives in, empty for a box on the form itself.
	child_table: str = ""
	#: Columns on the row that say WHO signed, filled from the roster where the
	#: caller is on one and left alone where they are not.
	name_field: str = ""
	title_field: str = ""
	#: Which rule's alert this box answers, so a collected signature can find the
	#: task that asked for it.
	alert_types: tuple = dataclass_field(default_factory=tuple)

	@property
	def key(self) -> str:
		return f"{self.doctype}.{self.field}"


#: THE CLOSED REGISTRY. Four boxes, one per missing-signature rule, and adding a
#: fifth is a code change on purpose: every entry is a claim about which column
#: on which doctype is a signature and who is allowed to put one there.
SIGNATURE_BOXES = (
	SignatureBox(
		doctype=I9_FORM,
		field="section_1_signature",
		label="I-9 Section 1 (employee attestation)",
		signed_at_field="section_1_signed_at",
		signed_ip_field="section_1_signed_ip",
		alert_types=("i9_section_1_unsigned",),
	),
	SignatureBox(
		doctype=I9_FORM,
		field="section_2_signature",
		label="I-9 Section 2 (employer verification)",
		signed_at_field="section_2_signed_at",
		signed_ip_field="section_2_signed_ip",
		form_type="I-9",
		name_field="verifier_name",
		title_field="verifier_title",
		alert_types=("i9_section_2_unsigned",),
	),
	SignatureBox(
		doctype=I9_FORM,
		field="section_3_signature",
		label="I-9 Supplement B (reverification attestation)",
		signed_at_field="signed_at",
		signed_ip_field="signed_ip",
		form_type="I-9",
		child_table="reverifications",
		name_field="verifier_name",
		title_field="verifier_title",
		alert_types=("i9_supplement_b_unsigned",),
	),
	SignatureBox(
		doctype=W4_FORM,
		field="signature",
		label="W-4 (employee signature)",
		signed_at_field="signed_at",
		signed_ip_field="signed_ip",
		alert_types=("w4_signature_missing",),
	),
)

BOXES_BY_KEY = {box.key: box for box in SIGNATURE_BOXES}

#: Spellings a caller will reasonably send for each doctype. A phone composing
#: this call from a Farm Task's `subject_doctype` sends the exact docname; a
#: model composing it from a sentence sends "I9" as often as not, and each of
#: these has one unambiguous reading.
DOCTYPE_ALIASES = {
	"i-9 form": I9_FORM,
	"i9 form": I9_FORM,
	"i-9": I9_FORM,
	"i9": I9_FORM,
	"form i-9": I9_FORM,
	"w-4 form": W4_FORM,
	"w4 form": W4_FORM,
	"w-4": W4_FORM,
	"w4": W4_FORM,
	"form w-4": W4_FORM,
}


# ── resolving what is being signed ──────────────────────────────────────────
def _box(args: dict) -> SignatureBox:
	"""Which of the four boxes this call is about, or the refusal naming all four."""
	doctype = str(args.get("doctype") or args.get("form_doctype") or "").strip()
	resolved = DOCTYPE_ALIASES.get(doctype.casefold(), doctype)
	fieldname = as_str(args, "field") or as_str(args, "signature_field")

	if not resolved:
		raise ToolError(
			"doctype is required — the form the signature goes on, 'I-9 Form' or 'W-4 Form'. "
			"A Farm Task raised from a missing-signature alert carries it in subject_doctype. "
			"Nothing was changed."
		)
	if not fieldname:
		# The commonest case has ONE answer, so answering it beats refusing: a
		# W-4 has exactly one signature box and asking which one is a question
		# with a single possible reply.
		candidates = [box for box in SIGNATURE_BOXES if box.doctype == resolved]
		if len(candidates) == 1:
			return candidates[0]
		raise ToolError(
			f"field is required — {resolved} has more than one signature box, and which one is "
			f"being signed is the difference between an employee attesting and an employer "
			f"attesting: {', '.join(box.field for box in candidates) or '(none)'}. "
			"Nothing was changed."
		)

	box = BOXES_BY_KEY.get(f"{resolved}.{fieldname}")
	if box is None:
		raise ToolError(
			f"{resolved}.{fieldname} is not a signature box this app will write to. It takes "
			f"exactly these, and the list is closed on purpose — an endpoint that wrote an "
			f"image into any column somebody named would be an arbitrary write:\n"
			+ "\n".join(f"  - {entry.key} — {entry.label}" for entry in SIGNATURE_BOXES)
			+ "\n\nNothing was changed."
		)
	return box


def _form_name(box: SignatureBox, args: dict) -> str:
	"""The docname being signed, accepting the same aliases its own tools do."""
	given = (
		as_str(args, "form")
		or as_str(args, "form_name")
		or as_str(args, "name")
		or as_str(args, "docname")
	)
	inner = dict(args)
	if given:
		inner["name"] = given
	# DELEGATED, NOT REIMPLEMENTED. `i9._resolve_form` and `w4._resolve_w4`
	# already take a docname OR an employee and already say the right thing when
	# neither resolves; a third copy of that reading here would be a third place
	# for "which W-4" to mean something slightly different.
	return i9._resolve_form(inner) if box.doctype == I9_FORM else w4._resolve_w4(inner)


def _child_row(box: SignatureBox, doc, args: dict) -> object:
	"""Which Supplement B row is being signed, or the refusal.

	DEFAULTS TO THE NEWEST UNSIGNED ROW, because that is what the alert was
	about: `i9_supplement_b_unsigned` fires on the form and its message quotes
	the most recent unsigned entry. A caller who means a different one names it,
	and a caller who names one that is already signed is refused rather than
	silently redirected — "I signed the wrong row" and "the row I meant was
	already done" are different mistakes.
	"""
	rows = list(doc.get(box.child_table) or [])
	if not rows:
		raise ToolError(
			f"{doc.name} has no reverification entries, so there is no Supplement B "
			f"attestation to sign. reverify_i9 records one. Nothing was changed."
		)
	wanted = as_str(args, "row") or as_str(args, "reverification") or as_str(args, "child_row")
	if wanted:
		for row in rows:
			if str(row.get("name") or "") == wanted:
				return row
		if wanted.isdigit() and 1 <= int(wanted) <= len(rows):
			return rows[int(wanted) - 1]
		raise ToolError(
			f"{doc.name} has no reverification row {wanted!r}. Its rows are: "
			+ ", ".join(
				f"{index}. {row.get('name')} ({row.get('reverification_date') or 'undated'})"
				for index, row in enumerate(rows, start=1)
			)
			+ ". Pass the row docname or its 1-based position. Nothing was changed."
		)
	unsigned = [row for row in rows if not str(row.get(box.field) or "").strip()]
	if not unsigned:
		raise ToolError(
			f"every reverification entry on {doc.name} already carries a signature. Name the "
			f"row explicitly with `row` and pass overwrite=true to replace one filed in error. "
			f"Nothing was changed."
		)
	unsigned.sort(key=lambda row: str(row.get("reverification_date") or ""), reverse=True)
	return unsigned[0]


# ── the image ───────────────────────────────────────────────────────────────
def _capture(box: SignatureBox, form: str, args: dict) -> tuple[str, bytes, str]:
	"""(file_docname, content, file_url) for the image, from base64 or a token.

	Returns bytes for the base64 path and an empty `content` for the token path,
	because the two produce a File differently: one has to be written and the
	other already exists and only has to be pointed at the right record.
	"""
	token = as_str(args, "file_token") or as_str(args, "file") or as_str(args, "file_url")
	raw = (
		as_str(args, "signature_base64")
		or as_str(args, "signature_image")
		or as_str(args, "signature")
		or as_str(args, "file_content")
	)
	if token and raw:
		raise ToolError(
			"both a base64 image and a file_token were given, and they are two different "
			"pictures as far as this call can tell. Send one. Nothing was changed."
		)
	if not (token or raw):
		raise ToolError(
			"the signature image is required: signature_base64 for a capture taken in the app "
			f"(up to {SIGNATURE_MAX_BYTES // 1024} KB, PNG or JPEG, no data: prefix), or "
			"file_token for one already uploaded with stage_file_chunk / finalize_staged_file. "
			"Nothing was changed."
		)

	if token:
		docname = token if frappe.db.exists("File", token) else ""
		if not docname and (token.startswith("/") or token.startswith("http")):
			docname = str(frappe.db.get_value("File", {"file_url": token}, "name") or "")
		if not docname:
			raise ToolError(
				f"no File called {token!r} on this site. Upload the capture first, or send it "
				f"as signature_base64. Nothing was changed."
			)
		row = frappe.db.get_value("File", docname, ["file_name", "file_url"], as_dict=True) or {}
		_check_extension(str(row.get("file_name") or token))
		return docname, b"", str(row.get("file_url") or "")

	# THE PREFIX IS STRIPPED RATHER THAN REFUSED. A browser canvas hands back
	# `data:image/png;base64,iVBOR…` and a phone hands back the tail of it; both
	# are the same picture, and refusing one of them would be this app being
	# precious about a comma.
	if raw.startswith("data:"):
		_, _, raw = raw.partition(",")
	content = files.decode_base64_content(raw, tail="Nothing was changed.")
	if len(content) > SIGNATURE_MAX_BYTES:
		raise ToolError(
			f"the capture is {len(content)} bytes and this endpoint takes signature captures "
			f"up to {SIGNATURE_MAX_BYTES} — a finger on a glass rectangle is a few kilobytes of "
			f"monochrome PNG. Something this size is a SCAN of a signed page, which goes up "
			f"through stage_file_chunk / finalize_staged_file and is filed with attach_signed_i9 "
			f"as the retained federal copy. Nothing was changed."
		)
	extension = _sniff(content)
	_check_extension(f"x{extension}")
	name = f"{form}-{box.field}-{FILE_STEM}{extension}"
	return "", content, name


def _sniff(content: bytes) -> str:
	"""`.png` or `.jpg` off the magic bytes, and a refusal for anything else.

	THE BYTES DECIDE, NOT A FILENAME THE CALLER CHOSE. This endpoint stores what
	it is sent under a name it composes itself, so there is no caller-supplied
	extension to trust — and a signature box holding an HTML document that a
	filename called `.png` is the exact confusion `api/files.py` keeps `.svg`
	off its allowlist to avoid.
	"""
	if content[:8] == b"\x89PNG\r\n\x1a\n":
		return ".png"
	if content[:3] == b"\xff\xd8\xff":
		return ".jpg"
	raise ToolError(
		"the capture is neither a PNG nor a JPEG — this app reads the first bytes rather than "
		"trusting a name, because a signature box holding something that merely ends in .png "
		"is worse than an empty one. Export the capture as PNG. Nothing was changed."
	)


def _check_extension(file_name: str) -> None:
	extension = ("." + file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else ""
	if extension not in SIGNATURE_EXTENSIONS:
		raise ToolError(
			f"{file_name!r} is a {extension or 'file with no extension'} and a signature capture "
			f"is an image: {', '.join(SIGNATURE_EXTENSIONS)}. Nothing was changed."
		)


# ── the tool ────────────────────────────────────────────────────────────────
def collect_form_signature(args: dict) -> ToolResult:
	"""Attach one signature capture to the box that was missing it, and close the task.

	THE CALL A FARM TASK RAISED BY `i9_section_2_unsigned` IS ASKING FOR. The
	alert found the empty box, the task put it on somebody's phone, and this is
	what that phone calls when the signature has been drawn.

	WHAT IT REFUSES, AND WHY EACH REFUSAL IS BEFORE THE WRITE:

	  * a field that is not one of the four signature boxes — an endpoint that
	    wrote an image into any column somebody named is an arbitrary write;
	  * a caller who cannot WRITE the form, checked through Frappe's own
	    permission system rather than through a role list this app invented;
	  * a caller not on the authorized-signer roster, for the two EMPLOYER
	    boxes only — Section 1 and the W-4 are signed by the worker, who is on
	    nobody's roster and must not need to be;
	  * a box that already carries a signature, unless `overwrite` — replacing
	    an attestation silently is the one write here that could not be noticed
	    afterwards;
	  * a destroyed I-9, which is a record whose documents no longer exist.

	AND WHAT IT DOES NOT REFUSE: a task it could not close, and a PDF it could
	not redraw. Both are reported, neither undoes the signature. See the module
	docstring — the capture is the compliance artefact and the rest is
	bookkeeping about it.
	"""
	box = _box(args)
	compat.require_doctype(
		box.doctype,
		"It ships with erpnext_mcp — run `bench --site <site> migrate` after upgrading the app.",
	)
	name = _form_name(box, args)

	doc = frappe.get_doc(box.doctype, name)
	if box.doctype == I9_FORM and str(doc.get("status") or "") == "Destroyed":
		raise ToolError(
			f"I-9 {name} was destroyed at the end of its retention period. A signature filed "
			f"against a destroyed record would attest to documents that no longer exist. "
			f"Nothing was changed."
		)

	# PERMISSION FIRST, AND THROUGH FRAPPE'S OWN CHECK. `write`, not `read`: a
	# signature changes what the document asserts about a person. Doing this
	# before anything is decoded means a caller who may not sign is refused
	# having had nothing of theirs stored.
	_require_write(box, name)
	signer = _require_signer(box)

	target = doc
	child = None
	if box.child_table:
		child = _child_row(box, doc, args)
		target = child

	overwrite = as_bool(args, "overwrite", False)
	existing = str(target.get(box.field) or "").strip()
	if existing and not overwrite:
		raise ToolError(
			f"{box.label} on {name} already carries a signature at {existing}. That is an "
			f"attestation somebody made under penalty of perjury. Pass overwrite=true only to "
			f"replace one filed in error; the File that was there stays attached to the record "
			f"either way. Nothing was changed."
		)

	file_docname, content, file_name = _capture(box, name, args)

	# ── 1. the image ────────────────────────────────────────────────────
	if content:
		handle = files.insert_attachment(
			file_name,
			content,
			is_private=True,
			doctype=box.doctype,
			name=name,
			attached_to_field=box.field,
		)
	else:
		# THROUGH THE DOCUMENT, NOT `db.set_value`, for the reason
		# `i9.attach_signed_i9` gives: making a public File private MOVES the
		# bytes and rewrites the URL, and only the File controller does the
		# moving. Flipping the column would leave a URL that says private and
		# resolves public.
		handle = frappe.get_doc("File", file_docname)
		handle.attached_to_doctype = box.doctype
		handle.attached_to_name = name
		handle.attached_to_field = box.field
		handle.is_private = 1
		handle.flags.ignore_permissions = True
		handle.save()
	stored = str(handle.get("file_url") or "")

	# ── 2. the form ─────────────────────────────────────────────────────
	_write(target, box.field, stored)
	_write(target, box.signed_at_field, frappe.utils.now())
	if box.signed_ip_field:
		_write(target, box.signed_ip_field, _remote_addr())
	# The printed name goes on ONLY where the roster supplied one and the box has
	# nothing in it. A form that already names its verifier is a form where
	# somebody examined the documents and this call is finishing their signature,
	# not re-attributing their examination.
	if signer.get("full_name") and box.name_field and not str(target.get(box.name_field) or "").strip():
		_write(target, box.name_field, signer["full_name"])
		if box.title_field and signer.get("title"):
			_write(target, box.title_field, signer["title"])
	doc.flags.ignore_permissions = True
	doc.save()

	if box.doctype == I9_FORM:
		i9._log_action(
			name,
			str(doc.get("employee") or ""),
			"Signature Collected",
			{
				"field": box.field,
				"row": str(child.get("name")) if child is not None else None,
				"file": stored,
				"file_docname": handle.name,
				"replaced": existing or None,
				"signer": signer.get("full_name") or None,
			},
		)

	result = {
		"doctype": box.doctype,
		"name": name,
		"field": box.field,
		"label": box.label,
		"row": str(child.get("name")) if child is not None else None,
		"employee": doc.get("employee"),
		"employee_name": doc.get("employee_name"),
		"signature": stored,
		"file_docname": handle.name,
		"signed_at": str(target.get(box.signed_at_field) or ""),
		"replaced": existing or None,
		"signer_roster_enforced": bool(signer.get("configured")),
	}

	# ── 3. the task, and ── 4. the PDF ──────────────────────────────────
	result["task"] = _close_the_task(box, name, handle.name, args)
	result["pdf"] = _redraw(box, name)

	return ToolResult(
		data=result,
		summary=f"{box.label} signed on {name}"
		+ (f" for {doc.get('employee_name')}" if doc.get("employee_name") else "")
		+ (f", task {result['task']['task']} completed" if (result["task"] or {}).get("completed") else ""),
	)


def _write(target, fieldname: str, value) -> None:
	"""Set one column on a document OR on a child row.

	A CHILD ROW IS NOT ALWAYS A DOCUMENT. Frappe hands back child tables as
	Documents most of the time and as plain dicts some of the time — a row read
	straight off `get_all`, a row on a doctype nothing has rehydrated — and the
	only write this module makes to a child is this one. `doc.set` on a dict is
	an AttributeError in the middle of storing a signature, which is the worst
	possible place for one.
	"""
	setter = getattr(target, "set", None)
	if callable(setter):
		setter(fieldname, value)
	else:
		target[fieldname] = value


def _remote_addr() -> str:
	return (
		frappe.local.request.remote_addr
		if hasattr(frappe, "local") and hasattr(frappe.local, "request") and frappe.local.request
		else ""
	)


def _require_write(box: SignatureBox, name: str) -> None:
	"""Refuse unless the acting account may modify the form being signed.

	FRAPPE'S OWN CHECK, not a role list compiled into this app. Who may write an
	I-9 on a given site is a question that site's permission rules answer — a
	role, a user permission on a company, a share — and reimplementing it here
	would be a second answer that drifts from the Desk's.
	"""
	try:
		permitted = frappe.has_permission(box.doctype, "write", doc=name)
	except Exception:  # pragma: no cover - a site whose permission cache is cold
		permitted = frappe.has_permission(box.doctype, "write")
	if not permitted:
		raise ToolError(
			f"this account may not write {box.doctype} {name}, so it may not put a signature on "
			f"it. A signature changes what the form asserts about a person, which is why the "
			f"check is `write` and not `read`. Nothing was changed."
		)


def _require_signer(box: SignatureBox) -> dict:
	"""The roster row authorising this account, `{}` where the box is not gated.

	THE EMPLOYEE BOXES ARE NOT GATED, and that is the point of `form_type` being
	empty on two of the four. Section 1 and the W-4 are signed by the worker
	themselves under their own penalty of perjury; requiring the account holding
	the phone to be an authorized signer would mean the only people who could
	collect a worker's signature are the people authorised to sign FOR the
	employer, which is precisely the conflation §274a keeps apart.
	"""
	if not box.form_type:
		return {}
	return signers.get_authorized_signer(_current_user(), box.form_type)


def _current_user() -> str:
	try:
		return str(frappe.session.user or "")
	except Exception:  # pragma: no cover - no session, as in a scheduled run
		return ""


def _close_the_task(box: SignatureBox, form: str, file_docname: str, args: dict) -> dict:
	"""Complete the Farm Task that asked for this signature, where there is one.

	NEVER RAISES, AND NEVER FORCES A COMPLETION. It delegates to
	`complete_farm_task`, which refuses a completion filed by somebody who was
	not holding the task — "a completion filed by somebody who was not there is
	not a chain of custody, it is a rumour". That refusal is the point of the
	doctype and this is not the place to route around it: an office account that
	collected a signature on behalf of the foreman holding the task has done
	something real, and what should happen is that the FOREMAN closes their own
	task, not that the office closes it in their name.

	So the return is a report rather than an outcome: which task was found, and
	whether it closed or why it did not.
	"""
	if not compat.doctype_exists(FARM_TASK):
		return {}
	from . import dispatch

	task = as_str(args, "task")
	if not task:
		task = _task_for(box, form)
	if not task:
		return {}

	row = frappe.db.get_value(FARM_TASK, task, ["name", "state", "assigned_to"], as_dict=True) or {}
	if not row:
		return {"task": task, "completed": False, "note": f"no Farm Task called {task!r} on this site."}
	if str(row.get("state") or "") in ("Completed", "Cancelled"):
		return {"task": task, "completed": False, "note": f"already {row.get('state')}."}

	try:
		dispatch.complete_farm_task(
			{
				"task": task,
				"worker_id": row.get("assigned_to"),
				"signature_file": file_docname,
				"findings_text": "",
				"completion_narrative": f"{box.label} collected and attached to {form}.",
			}
		)
	except Exception as exc:
		return {
			"task": task,
			"completed": False,
			"note": (
				f"the signature was filed and the task was left open ({exc}). Close it from the "
				"account holding it — a completion filed by somebody who was not there is not a "
				"chain of custody."
			),
		}
	return {"task": task, "completed": True}


def _task_for(box: SignatureBox, form: str) -> str:
	"""The open Farm Task raised for this exact box, or "".

	MATCHED ON THE SUBJECT LINK AND THE ALERT TYPE, both. The link alone would
	find a task raised for a DIFFERENT box on the same form — an I-9 missing both
	signatures has two tasks, held by two different people — and closing the
	wrong one would tell a supervisor the employee had signed because an
	authorized signer had.
	"""
	if not (compat.has_field(FARM_TASK, "subject_docname") and box.alert_types):
		return ""
	rows = frappe.db.get_all(
		FARM_TASK,
		filters={
			"subject_doctype": box.doctype,
			"subject_docname": form,
			"state": ("not in", ("Completed", "Cancelled")),
		},
		fields=["name", "source_alert"],
		order_by="creation desc",
		limit=20,
	)
	for row in rows or []:
		alert_type = str(
			frappe.db.get_value("Compliance Alert", row.get("source_alert"), "alert_type") or ""
		)
		if alert_type in box.alert_types:
			return str(row["name"])
	return ""


def _redraw(box: SignatureBox, form: str) -> dict:
	"""Regenerate the form's PDF so the retained page carries the new signature.

	THE WHOLE REASON THIS STEP IS HERE. `render_i9_pdf` stamps captured
	signatures into the page content and flattens it, so the generated PDF is
	the page an inspection is shown — and a PDF rendered before the signature
	arrived is a page with an empty box on a form that is signed. Leaving it
	stale would mean the record and its printable copy disagree about the one
	fact somebody would print it to prove.

	REGENERATION, AND ONLY REGENERATION. A form that has never been rendered gets
	nothing: `render_i9_pdf` is one call away, it is the caller's decision when to
	make it, and producing a federal form nobody asked for — on a phone, in an
	orchard, as a side effect of collecting a signature — is this app deciding
	something that is not its to decide. What it will not leave behind is a
	rendered page that has gone stale, which is why `overwrite` is passed: the
	File that was there stays attached to the record either way, so the printed
	copy somebody already had signed is not lost.

	NEVER RAISES. The renderers need `pypdf` and the blank federal form on disk,
	and a site missing either has a working signature and no PDF — which is a
	smaller problem than a signature this call refused to store.
	"""
	try:
		existing = str(frappe.db.get_value(box.doctype, form, "generated_pdf") or "").strip()
	except Exception:  # pragma: no cover - a site whose column is not migrated
		existing = ""
	if not existing:
		return {
			"regenerated": False,
			"note": (
				"no PDF had been rendered for this form, so there was nothing to bring up to "
				"date. render_i9_pdf / render_w4_pdf draws one, with the signature stamped in."
			),
		}
	try:
		if box.doctype == I9_FORM:
			result = i9.render_i9_pdf({"i9_form": form, "overwrite": True})
		else:
			result = w4.render_w4_pdf({"w4_form": form, "overwrite": True})
	except Exception as exc:
		return {
			"regenerated": False,
			"note": (
				f"the signature is on the record and the PDF was not redrawn ({exc}). The "
				f"rendered page at {existing} is now out of date with the record it was drawn "
				f"from; nothing about the signature depends on it."
			),
		}
	data = getattr(result, "data", None) or {}
	return {
		"regenerated": True,
		"file_url": data.get("file_url"),
		"file_name": data.get("file_name"),
		"replaced": existing,
	}


__all__ = [
	"SIGNATURE_BOXES",
	"SIGNATURE_MAX_BYTES",
	"SignatureBox",
	"collect_form_signature",
]
