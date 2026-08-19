# SPDX-License-Identifier: MIT
"""Everything one badge card prints, resolved in a single call. THIS IS A HOOK TARGET.

v0.56.0. `badge_print_format.py` lays a Bucket Log Badge Map out as a CR-80 ID
card. A Print Format is Jinja, and the facts that card needs are spread over
four records — the badge, the Employee it was issued to, that Employee's photo,
and the Company's badge mark — none of which the badge row carries. A template
that went and got them itself would be four framework calls in presentation
code, each of which is a blank card when it returns None.

So the template asks ONE question and gets a dict. `erpnext_mcp_badge_card` is
the Jinja global that answers it, and the template below it is pure layout.

────────────────────────────────────────────────────────────────────────────
EVERY IMAGE IS A `data:` URI, AND THAT IS THE WHOLE REASON THIS FILE EXISTS
────────────────────────────────────────────────────────────────────────────

`i9_print_format.py` bans `<img>` outright and states why: wkhtmltopdf fetches
every external URL synchronously with no timeout worth the name, so one image
that 404s — a photo whose private File URL the renderer cannot authenticate to —
hangs the render or blanks the page. That ban was the right answer for a form
made of rules and tick boxes.

A BADGE CANNOT TAKE IT. A card with no QR is not a badge, and a card with no
face on it is not an ID. The ban was a proxy for the actual hazard, which is the
FETCH and not the tag: an `<img src="data:image/png;base64,...">` is bytes
already in the document, and wkhtmltopdf resolves it without opening a socket.
So this module reads the file off the site's own disk and inlines it, and the
rendered card references no external resource — the same promise the I-9 format
makes, kept a different way.

`_site_path` refuses anything that is not under this site's own two file
directories, `..` included. The URLs it reads come from `Employee.image` and
`Company.badge_logo`, which HR sets through Frappe's own attach control, so the
traversal guard is not the threat model — it is the cheap half of a function
that turns a string from a database column into an open file handle.

────────────────────────────────────────────────────────────────────────────
IT NEVER RAISES, AND THE IMPORTS AT THE TOP OF THIS FILE ARE PART OF THAT
────────────────────────────────────────────────────────────────────────────

Frappe resolves a `jinja` hook path when it BUILDS THE JINJA ENVIRONMENT, which
it also does to render the error page. v0.14.1 learned what that costs: a bad
hook string took every page on the site down, including the page that would have
explained it. `render/checks.py` tells that story at length.

This module therefore imports `frappe`, `base64` and `os` and nothing of this
app's own. `tools/badges.py` — which owns the error-correction level and the
scale arithmetic, and which pulls in the tool layer, the bridge and the compat
shims behind it — is imported INSIDE the function, so an import error anywhere
in that chain costs a blank QR on one card instead of every Desk page on the
site. For the same reason every branch below returns a value: a missing photo,
an unreadable file, a Company row that has gone, an encoder that is not
installed. A card that prints without a logo is a working badge. A card that
raises is a Print button that does nothing and says nothing.
"""

from __future__ import annotations

import base64
import os

import frappe

BADGE_DOCTYPE = "Bucket Log Badge Map"
EMPLOYEE = "Employee"
COMPANY = "Company"

#: The Company field a card's mark comes from. Spelled here rather than imported
#: from `tools/badges.py` for the reason in the docstring — this module holds no
#: import of this app's own at module level. `test_badge_print_format.py` asserts
#: the two spellings agree, so they cannot drift in silence.
BADGE_LOGO_FIELD = "badge_logo"

#: Employee columns a card reads. `image` is Frappe's own photo field.
_EMPLOYEE_FIELDS = ("employee_name", "designation", "employee_number", "company", "image", "status")

#: What an inlined image may weigh. A base64 `data:` URI is a third larger than
#: the file, and it lands in the HTML twice over on a two-sided card — so a
#: twelve-megapixel phone photograph somebody attached to an Employee record
#: would be a thirty-megabyte print page that the browser renders as a spinner.
#: Past this the card falls back to the initials block, which is a legible badge.
MAX_EMBEDDED_BYTES = 2 * 1024 * 1024

#: Suffixes worth inlining, and what to call them. A file this map does not know
#: is not guessed at: `data:` with the wrong media type renders as a broken image
#: icon on the card, which looks like a bug in the badge rather than in the upload.
_MIME_BY_SUFFIX = {
	".png": "image/png",
	".jpg": "image/jpeg",
	".jpeg": "image/jpeg",
	".gif": "image/gif",
	".webp": "image/webp",
}


def _site_path(url: str) -> str:
	"""An absolute path under this site's file directories, or "" for anything else.

	Refuses an absolute URL outright. `http://…` is the fetch the module docstring
	is about, and a badge that silently reached out to a host named in a database
	column would be the hazard wearing the fix's clothes.
	"""
	text = str(url or "").strip()
	if not text or "://" in text or text.startswith("//"):
		return ""
	text = text.split("?", 1)[0].split("#", 1)[0]

	if text.startswith("/private/files/"):
		root, relative = frappe.get_site_path("private", "files"), text[len("/private/files/") :]
	elif text.startswith("/files/"):
		root, relative = frappe.get_site_path("public", "files"), text[len("/files/") :]
	else:
		return ""
	if not relative:
		return ""

	root = os.path.realpath(root)
	path = os.path.realpath(os.path.join(root, relative))
	# `realpath` first, compare after: this is what makes `../../site_config.json`
	# a "" rather than an open file.
	if path != root and not path.startswith(root + os.sep):
		return ""
	return path


def _data_uri(url: str) -> str:
	"""A file on this site's disk as a `data:` URI, or "" — never an exception."""
	try:
		path = _site_path(url)
		if not path:
			return ""
		mime = _MIME_BY_SUFFIX.get(os.path.splitext(path)[1].lower())
		if not mime:
			return ""
		if not os.path.isfile(path) or os.path.getsize(path) > MAX_EMBEDDED_BYTES:
			return ""
		with open(path, "rb") as handle:
			blob = handle.read(MAX_EMBEDDED_BYTES + 1)
		if len(blob) > MAX_EMBEDDED_BYTES:
			return ""
		return f"data:{mime};base64," + base64.b64encode(blob).decode("ascii")
	except Exception:
		return ""


def initials(employee_name: str) -> str:
	"""The two letters that stand in for a photograph nobody uploaded.

	The same rule `tools/badges._initials` applies, and the tests hold the two to
	the same answer. It is duplicated rather than imported for the reason the
	module docstring gives about imports at the top of this file.
	"""
	parts = [part for part in str(employee_name or "").replace(".", " ").split() if part]
	if not parts:
		return "?"
	if len(parts) == 1:
		return parts[0][:2].upper()
	return (parts[0][:1] + parts[-1][:1]).upper()


def _qr_data_uri(badge_id: str) -> dict:
	"""The badge's QR as a `data:` URI, with the facts a layout has to honour.

	THE PAYLOAD IS THE BARE BADGE ID, because that is what reads it back:
	`bucket_bridge.resolve_badge_to_employee` matches `worker_badge` as an exact
	string. A URL, a JSON blob or a signed token here would be a symbol this
	site's own scanners do not resolve.

	Error correction comes from `tools/badges` rather than from `qr.render`'s
	default, and the difference is the point — H recovers 30% against M's 15%,
	which is a scuffed card that still scans at a bin trailer. Imported inside
	the function; see the module docstring.
	"""
	blank = {"qr": "", "qr_modules": 0, "qr_pixels": 0, "error_correction": "", "encoder": ""}
	try:
		from ..render import qr
		from ..tools import badges

		rendered = qr.render(
			str(badge_id),
			error=badges.BADGE_ERROR_CORRECTION,
			scale=qr.SCALE,
			border=qr.BORDER,
		)
		rendered = qr.render(
			str(badge_id),
			error=badges.BADGE_ERROR_CORRECTION,
			scale=badges._badge_scale(rendered["modules"]),
			border=qr.BORDER,
		)
		return {
			"qr": "data:image/png;base64," + base64.b64encode(rendered["png"]).decode("ascii"),
			"qr_modules": rendered["modules"],
			"qr_pixels": rendered["pixels"],
			"error_correction": rendered["error_correction"],
			"encoder": rendered["encoder"],
		}
	except Exception:
		# A bench with neither `segno` nor `qrcode` prints a card with the badge
		# ID on it and no symbol. That is a worse badge and a real one; a Print
		# button that 500s is neither.
		return blank


def erpnext_mcp_badge_card(badge_id) -> dict:
	"""Every fact one badge card prints. THE JINJA GLOBAL. Never raises.

	Frappe names a Jinja global after the callable's `__name__`, so this function
	is spelled with the app's own prefix — the name lands in a namespace shared
	with Frappe, ERPNext and every other installed app. See `render/checks.py`,
	which paid for that sentence.

	Takes the badge ID rather than the document because that makes it callable
	from a test with a string, and because the four lookups happen here either
	way. `ok` is False when the register has no such badge; the template prints
	the ID and the words that say so rather than an empty card, since a card that
	came out blank is one somebody prints twice.
	"""
	card = {
		"ok": False,
		"badge_id": str(badge_id or ""),
		"active": False,
		"company": "",
		"company_name": "",
		"employee": "",
		"employee_name": "",
		"designation": "",
		"employee_number": "",
		"status": "",
		"photo": "",
		"initials": "?",
		"logo": "",
		"qr": "",
		"qr_modules": 0,
		"qr_pixels": 0,
		"error_correction": "",
		"encoder": "",
	}
	try:
		if not card["badge_id"]:
			return card

		row = frappe.db.get_value(
			BADGE_DOCTYPE,
			card["badge_id"],
			("badge_id", "company", "employee", "active"),
			as_dict=True,
		)
		if not row:
			return card

		card["ok"] = True
		card["active"] = bool(row.get("active"))
		card["company"] = str(row.get("company") or "")
		card["employee"] = str(row.get("employee") or "")

		if card["employee"]:
			person = frappe.db.get_value(EMPLOYEE, card["employee"], _EMPLOYEE_FIELDS, as_dict=True) or {}
			card["employee_name"] = str(person.get("employee_name") or card["employee"])
			card["designation"] = str(person.get("designation") or "")
			card["employee_number"] = str(person.get("employee_number") or "")
			card["status"] = str(person.get("status") or "")
			card["photo"] = _data_uri(person.get("image"))
		card["initials"] = initials(card["employee_name"])

		if card["company"]:
			card["company_name"] = _company_name(card["company"])
			card["logo"] = _company_logo(card["company"])

		card.update(_qr_data_uri(card["badge_id"]))
	except Exception:
		# Whatever went wrong, the caller is a Print Format mid-render. Returning
		# what has been filled in so far prints a partial card; raising prints a
		# traceback where a badge should be.
		pass
	return card


def _company_name(company: str) -> str:
	"""The entity's printable name. The docname is the fallback, never a blank."""
	try:
		return str(frappe.db.get_value(COMPANY, company, "company_name") or company)
	except Exception:
		return str(company)


def _company_logo(company: str) -> str:
	"""The badge mark as a `data:` URI, or "".

	A SITE WITHOUT THE FIELD STILL PRINTS BADGES — `tools/badges._company_logo`
	makes the same promise for the same reason. `badge_logo` arrives on the next
	`bench migrate` and a card is perfectly legible without a mark on it.
	"""
	try:
		# `compat.has_field` spelled out rather than imported, for the reason the
		# module docstring gives about what may be imported at the top of this file.
		if not frappe.get_meta(COMPANY).has_field(BADGE_LOGO_FIELD):
			return ""
		return _data_uri(frappe.db.get_value(COMPANY, company, BADGE_LOGO_FIELD))
	except Exception:
		return ""


__all__ = (
	"BADGE_LOGO_FIELD",
	"MAX_EMBEDDED_BYTES",
	"erpnext_mcp_badge_card",
	"initials",
)
