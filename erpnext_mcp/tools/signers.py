# SPDX-License-Identifier: MIT
"""Who may sign a federal employment form on this employer's behalf. v0.48.0.

Section 2 of Form I-9 is an attestation under penalty of perjury that a NAMED
PERSON examined the employee's documents. Until this release, `verifier_name`
was whatever the caller typed — a string on a JSON body, from a phone in an
orchard, with nothing on the server that said whether that person had ever been
authorised to make the attestation. Form W-4's Employers Only block has the same
shape and had the same gap.

The roster is a child table on I-9 Settings. Six columns: the account, the
printed name, the title, a flag per form, and an active flag.

────────────────────────────────────────────────────────────────────────────
AN EMPTY ROSTER AUTHORISES EVERYBODY, AND THAT IS THE DESIGN
────────────────────────────────────────────────────────────────────────────

A site that has never added a row behaves exactly as it did before this
release. That is not a loophole left open by accident — it is what makes the
feature installable. Every existing site migrates into an empty roster, and a
version that started refusing signatures on migrate would break the I-9 flow on
every farm running this app until somebody found the new table.

SO THE FIRST ROW IS THE SWITCH. Adding one row turns enforcement on for the
form it covers, and from that moment an account not on the roster cannot sign.
`add_authorized_signer` says so in its result, because "I added myself and now
my foreman can't file an I-9" is the surprise this design has to pay for.

────────────────────────────────────────────────────────────────────────────
WHO SIGNS IS NOT WHO CALLS
────────────────────────────────────────────────────────────────────────────

The account making the request is matched against `user`; the name written onto
the form is `full_name`. They are different facts and the app has always kept
both — every mutating I-9 tool writes an I-9 Audit Log row naming the account
regardless of whose name went in the box.

AN EXPLICIT `verifier_name` IS STILL ACCEPTED, and it is checked. One
authorised person filing on behalf of another authorised person is a real thing
in a packing shed — the foreman examined the documents, the office files the
form — and refusing it outright would push that case onto a Desk edit. What is
refused is a name that is on nobody's roster, because a name nobody authorised
is the exact string this module exists to stop.

────────────────────────────────────────────────────────────────────────────
DEACTIVATE, NEVER DELETE
────────────────────────────────────────────────────────────────────────────

`remove_authorized_signer` clears `active`. There is no delete tool, for the
same reason `destroy_i9` writes a certificate instead of dropping a row: a form
signed in 2026 was signed by whoever was authorised in 2026, and a roster that
forgets its own history cannot answer the question an inspection asks.
"""
from __future__ import annotations

import frappe

from ..args import as_bool, as_str
from ..errors import ToolError
from ..result import ToolResult

I9_SETTINGS = "I-9 Settings"
AUTHORIZED_SIGNER = "Authorized Signer"
SIGNERS_FIELD = "authorized_signers"

#: Which form each `can_sign_*` flag governs, and the only two spellings of a
#: form type this module answers to. One table rather than an `if` per call site
#: so `get_authorized_signer` and the four tools cannot come to disagree about
#: what "W-4" means.
FORM_FLAGS = {
	"I-9": "can_sign_i9",
	"W-4": "can_sign_w4",
}

#: Spellings a caller will reasonably send for each of the two, normalised
#: before the table above is consulted. A model asked to name a federal form
#: writes `I9`, `i-9`, `Form I-9` and `W4` with roughly equal probability, and
#: each has one unambiguous reading.
FORM_ALIASES = {
	"I-9": "I-9", "I9": "I-9", "FORM I-9": "I-9", "FORM I9": "I-9", "I-9 FORM": "I-9",
	"W-4": "W-4", "W4": "W-4", "FORM W-4": "W-4", "FORM W4": "W-4", "W-4 FORM": "W-4",
}

#: The columns a roster row reports. `user` is included because the whole point
#: is that the account and the printed name are separate facts.
SIGNER_FIELDS = ("user", "full_name", "title", "can_sign_i9", "can_sign_w4", "active")


def normalise_form_type(value: str | None) -> str:
	"""`i9` → `I-9`, or a refusal naming both forms this roster covers."""
	raw = str(value or "I-9").strip().upper()
	resolved = FORM_ALIASES.get(raw)
	if not resolved:
		raise ToolError(
			f"form_type must be 'I-9' or 'W-4', got {value!r}. Those are the two federal "
			f"forms an authorized signer signs on this site."
		)
	return resolved


def _flag(value) -> bool:
	"""A Frappe Check as a bool, whichever of the four shapes it arrives in."""
	return value in (1, "1", True, "True")


def _settings():
	"""The I-9 Settings Single, or a refusal that names the fix.

	Raises rather than returning None: every caller here is either reading the
	roster or writing to it, and a site whose Single has not migrated cannot do
	either. `get_authorized_signer` catches this and treats it as an empty
	roster, because a signature is not the right place to discover that a
	migration is outstanding.
	"""
	try:
		return frappe.get_doc(I9_SETTINGS)
	except Exception:
		raise ToolError(
			f"{I9_SETTINGS} does not exist on this site, so there is nowhere to keep an "
			f"authorized signer. Run bench migrate. Nothing was changed."
		) from None


def _rows(doc=None) -> list[dict]:
	"""Every roster row, active and not, as plain dicts.

	READ OFF THE SINGLE rather than through `frappe.db.get_all` on the child
	doctype. A Single's child rows carry `parent = 'I-9 Settings'` and could be
	queried directly, but `frappe.get_doc` on a Single loads its tables anyway
	and one read is cheaper than two — and this is called on every Section 2.
	"""
	source = doc if doc is not None else _settings()
	rows = []
	for index, row in enumerate(source.get(SIGNERS_FIELD) or [], start=1):
		entry = {key: row.get(key) for key in SIGNER_FIELDS}
		for key in ("user", "full_name", "title"):
			entry[key] = str(entry.get(key) or "").strip()
		for key in ("can_sign_i9", "can_sign_w4", "active"):
			entry[key] = _flag(entry.get(key))
		entry["idx"] = int(row.get("idx") or index)
		rows.append(entry)
	return rows


def roster_is_configured(rows: list[dict] | None = None) -> bool:
	"""Has anybody been put on this roster? See the module docstring.

	ANY ROW COUNTS, active or not. A site that added one signer and then
	deactivated them has configured a roster and decided it is empty of anyone
	who may sign — falling back to "everybody" there would mean deactivating the
	last signer silently reopened the door.
	"""
	return bool(rows if rows is not None else _rows())


def get_authorized_signer(user: str, form_type: str = "I-9") -> dict:
	"""The roster row authorising `user` to sign `form_type`, or a refusal.

	Returns a dict either way, and `configured` is the key that says which case
	this is:

	* `{"configured": False, ...}` — no roster on this site. The caller carries
	  on with whatever name it was given, which is every install's behaviour
	  before v0.48.0 and every install's behaviour on the day it upgrades.
	* `{"configured": True, "full_name": ..., "title": ...}` — this account is
	  on the roster and may sign this form.

	Raises `ToolError` where a roster EXISTS and this account is not on it, or
	is on it inactive, or is on it without the flag for this form. Three
	different refusals with three different messages, because "you were removed"
	and "you may sign an I-9 but not a W-4" send an operator to different places.

	NEVER RAISES FOR A SITE THAT HAS NOT MIGRATED. A missing Single reads as an
	empty roster: the middle of somebody's signature is the wrong place to learn
	that a migration is outstanding, and the pre-v0.48.0 behaviour is the safe
	direction.
	"""
	form = normalise_form_type(form_type)
	account = str(user or "").strip()

	try:
		rows = _rows()
	except ToolError:  # pragma: no cover - a site whose Single has not migrated
		rows = []

	if not roster_is_configured(rows):
		return {
			"configured": False,
			"user": account,
			"full_name": "",
			"title": "",
			"form_type": form,
		}

	mine = [row for row in rows if row["user"] and row["user"] == account]
	if not mine:
		raise ToolError(
			f"{account or 'this account'} is not an authorized signer for {form} on this "
			f"site. {len(rows)} signer(s) are configured in I-9 Settings and a federal form "
			f"is signed by one of them; list_authorized_signers has the roster, and "
			f"add_authorized_signer puts somebody on it. Nothing was changed."
		)

	active = [row for row in mine if row["active"]]
	if not active:
		raise ToolError(
			f"{account} is on the authorized signer roster and is not active. The row is kept "
			f"rather than deleted so forms they already signed still name somebody the site "
			f"had authorised; reactivate them with update_authorized_signer(active=true) if "
			f"they should be signing again. Nothing was changed."
		)

	flag = FORM_FLAGS[form]
	permitted = [row for row in active if row[flag]]
	if not permitted:
		other = "W-4" if form == "I-9" else "I-9"
		raise ToolError(
			f"{account} is an active authorized signer and is not authorized for {form} "
			f"specifically ({flag} is off). They may still sign a {other} if that flag is on. "
			f"update_authorized_signer({flag}=true) grants it. Nothing was changed."
		)

	row = permitted[0]
	return {
		"configured": True,
		"user": row["user"],
		"full_name": row["full_name"],
		"title": row["title"],
		"form_type": form,
	}


def resolve_signature(args: dict, form_type: str, name_key: str, title_key: str,
                      required: bool = True) -> dict:
	"""The name and title that go on the form, and where each of them came from.

	THE CALL SITE FOR `submit_i9_section_2` AND `submit_w4`, written once
	because the two forms make the same decision and a second copy of it would
	drift. Returns `{"name", "title", "signer", "override", "configured"}`.

	The order of preference is deliberate:

	1. Where the roster is configured, this account's own row supplies both, so
	   a caller that sends nothing gets the authorised person's real name rather
	   than a required-argument error.
	2. An explicit name overrides it — and MUST ITSELF BE ON THE ROSTER, matched
	   on the printed name. One authorised person filing on behalf of another is
	   a real workflow; a name nobody authorised is the thing this module exists
	   to refuse.
	3. Where the roster is NOT configured, the explicit name is the only source.

	An explicit TITLE is taken as given in every case. It is descriptive rather
	than authorising — nothing about a title decides whether a signature is
	valid — and an operator correcting `Manager` to `HR Manager` on one form
	should not have to edit the roster to do it.

	`required` IS WHAT THE TWO FORMS DISAGREE ABOUT, and it is a compatibility
	fact rather than a policy one. `submit_i9_section_2` has demanded
	`verifier_name` since v0.27.0, so on an unconfigured site it goes on
	demanding it — silently substituting a name there would change what a
	four-release-old federal attestation means. `submit_w4` never took one, so
	an unconfigured site falls back to the calling account's own full name
	rather than starting to refuse every W-4 the day this version lands.
	"""
	form = normalise_form_type(form_type)
	account = _current_user()
	signer = get_authorized_signer(account, form)
	asked_name = as_str(args, name_key)
	asked_title = as_str(args, title_key)

	if not signer["configured"]:
		name = asked_name or ("" if required else (_default_full_name(account) or account))
		if not name:
			raise ToolError(f"{name_key} is required")
		return {
			"name": name,
			"title": asked_title,
			"signer": signer,
			"override": None,
			"configured": False,
		}

	override = None
	name = signer["full_name"]
	title = signer["title"]
	if asked_name and asked_name.casefold() != signer["full_name"].casefold():
		override = _authorized_by_name(asked_name, form)
		name = override["full_name"]
		title = override["title"]
	if asked_title:
		title = asked_title

	return {
		"name": name,
		"title": title,
		"signer": signer,
		"override": override,
		"configured": True,
	}


def _authorized_by_name(printed_name: str, form: str) -> dict:
	"""One active roster row whose printed name is `printed_name`, or a refusal.

	Matched case-insensitively on `full_name`, which is what a caller sending a
	name has. Two active rows with the same printed name would be a roster with
	two people of one name on it, and the first is as good an answer as any —
	they are equally authorised, which is the only question being asked.
	"""
	flag = FORM_FLAGS[form]
	wanted = printed_name.casefold()
	for row in _rows():
		if row["active"] and row[flag] and row["full_name"].casefold() == wanted:
			return row
	raise ToolError(
		f"{printed_name!r} is not an active authorized signer for {form} on this site, so "
		f"their name cannot go on the form. A form is signed by somebody this employer has "
		f"authorised; list_authorized_signers has the roster. Omit the name to sign as "
		f"yourself, or add them with add_authorized_signer first. Nothing was changed."
	)


def _current_user() -> str:
	"""The account making this call. NOT `frappe.session.user` first.

	THIS IS THE ONE THING THE WHOLE MODULE GETS WRONG IF IT IS GOT WRONG.
	`mcp.handle` calls `frappe.set_user(settings.effective_user())` a line after
	it authenticates, so from there on every tool runs as the MCP System User
	and `frappe.session.user` says nothing about which of forty accounts made
	the request. `security.caller_identity` reads back the identity
	`capture_calling_user` saved before that switch, and BOTH transports capture
	it — `mcp.handle` for the JSON-RPC path, `guard.endpoint` for the mobile one.
	A roster matched against the effective user would authorise every caller
	identically, which is the same as having no roster at all.

	FALLS BACK TO THE SESSION USER rather than to nobody. An operator working
	through Claude Desktop presents an MCP token and no human credential, so
	there IS no calling identity; the account the work is attributed to is the
	MCP System User, and naming it is what lets an operator put it on the roster
	deliberately or see in the refusal why their call was turned down.
	"""
	try:
		from ..security import caller_identity

		caller = str(caller_identity() or "").strip()
		if caller:
			return caller
	except Exception:  # pragma: no cover - a request with no local
		pass
	try:
		user = str(frappe.session.user or "").strip()
	except Exception:  # pragma: no cover - a context with no session
		return ""
	return "" if user == "Guest" else user


def _find_row(doc, user: str):
	"""The roster row for one account on a loaded Settings document, or None."""
	wanted = str(user or "").strip()
	for row in doc.get(SIGNERS_FIELD) or []:
		if str(row.get("user") or "").strip() == wanted:
			return row
	return None


def _resolve_user(args: dict) -> str:
	"""One User docname from `user`, refusing an account that is not on the site."""
	user = as_str(args, "user") or as_str(args, "email")
	if not user:
		raise ToolError("user is required — the User account being authorized to sign.")
	if not frappe.db.exists("User", user):
		raise ToolError(
			f"no User called {user!r} on this site. An authorized signer is an account "
			f"rather than a name, because the signature check matches on the account making "
			f"the call. Nothing was changed."
		)
	return user


def _describe(rows: list[dict]) -> list[dict]:
	"""The roster as a result carries it."""
	return [
		{
			"user": row["user"],
			"full_name": row["full_name"],
			"title": row["title"],
			"can_sign_i9": row["can_sign_i9"],
			"can_sign_w4": row["can_sign_w4"],
			"active": row["active"],
		}
		for row in rows
	]


#: Said whenever the roster goes from empty to not-empty. See the module
#: docstring: the first row is the switch, and an operator who adds a colleague
#: and not themselves has just locked themselves out of Section 2.
_FIRST_ROW_NOTE = (
	"This is the FIRST signer on this site, so the roster is now enforced: from this call "
	"on, only an account listed here can complete Section 2 of an I-9 or the employer block "
	"of a W-4. Add everybody who verifies documents — including yourself — before the next "
	"hire, or those calls will be refused."
)


# ── read-only ───────────────────────────────────────────────────────────────


def list_authorized_signers(args: dict) -> ToolResult:
	"""Who may sign a federal employment form on this employer's behalf."""
	include_inactive = as_bool(args, "include_inactive", True)
	form_type = as_str(args, "form_type")

	rows = _rows()
	shown = rows if include_inactive else [row for row in rows if row["active"]]
	if form_type:
		flag = FORM_FLAGS[normalise_form_type(form_type)]
		shown = [row for row in shown if row[flag]]

	active = [row for row in rows if row["active"]]
	data = {
		"signers": _describe(shown),
		"count": len(shown),
		"configured": roster_is_configured(rows),
		"active_count": len(active),
		"i9_signers": len([row for row in active if row["can_sign_i9"]]),
		"w4_signers": len([row for row in active if row["can_sign_w4"]]),
	}
	if not data["configured"]:
		data["note"] = (
			"No authorized signers are configured, so this site is UNRESTRICTED: "
			"submit_i9_section_2 and submit_w4 accept whatever name they are given, which is "
			"the behaviour every site had before v0.48.0. Adding the first signer turns "
			"enforcement on."
		)
	summary = (
		f"{len(shown)} authorized signer(s)"
		if data["configured"]
		else "no authorized signers configured — federal form signing is unrestricted"
	)
	return ToolResult(data=data, summary=summary)


# ── mutating ────────────────────────────────────────────────────────────────


def add_authorized_signer(args: dict) -> ToolResult:
	"""Authorize one account to sign federal employment forms for this employer.

	REFUSES A SECOND ROW FOR ONE ACCOUNT. Two rows for one user would make the
	answer to "may this account sign" depend on which row was read first, and
	`update_authorized_signer` is what changes an existing authorisation.
	"""
	doc = _settings()
	user = _resolve_user(args)
	if _find_row(doc, user) is not None:
		raise ToolError(
			f"{user} is already on the authorized signer roster. Change what they may sign "
			f"with update_authorized_signer, or reactivate them with "
			f"update_authorized_signer(active=true). Nothing was changed."
		)

	full_name = as_str(args, "full_name") or _default_full_name(user)
	if not full_name:
		raise ToolError(
			f"full_name is required — it is the name printed on the form, and {user} has no "
			f"full name on their User record to fall back to. Nothing was changed."
		)

	was_configured = roster_is_configured(_rows(doc))
	doc.append(SIGNERS_FIELD, {
		"user": user,
		"full_name": full_name,
		"title": as_str(args, "title"),
		"can_sign_i9": 1 if as_bool(args, "can_sign_i9", True) else 0,
		"can_sign_w4": 1 if as_bool(args, "can_sign_w4", True) else 0,
		"active": 1 if as_bool(args, "active", True) else 0,
	})
	doc.flags.ignore_permissions = True
	doc.save()

	rows = _rows()
	added = next(row for row in rows if row["user"] == user)
	data = {"signer": _describe([added])[0], "count": len(rows), "first_signer": not was_configured}
	if not was_configured:
		data["note"] = _FIRST_ROW_NOTE
	summary = f"{full_name} ({user}) authorized to sign " + _forms_sentence(added)
	if not was_configured:
		summary += " — the roster is now enforced"
	return ToolResult(data=data, summary=summary)


def update_authorized_signer(args: dict) -> ToolResult:
	"""Change one signer's printed name, title, or what they may sign.

	`active` IS SETTABLE HERE AND IN BOTH DIRECTIONS, which is what makes
	`remove_authorized_signer` a convenience rather than the only way back.
	"""
	doc = _settings()
	user = _resolve_user(args)
	row = _find_row(doc, user)
	if row is None:
		raise ToolError(
			f"{user} is not on the authorized signer roster, so there is nothing to update. "
			f"add_authorized_signer puts them on it; list_authorized_signers has the roster. "
			f"Nothing was changed."
		)

	changed = []
	for field in ("full_name", "title"):
		value = as_str(args, field)
		if value:
			row.set(field, value)
			changed.append(field)
	for field in ("can_sign_i9", "can_sign_w4", "active"):
		value = as_bool(args, field, None)
		if value is not None:
			row.set(field, 1 if value else 0)
			changed.append(field)

	if not changed:
		raise ToolError(
			"no fields to update. Pass at least one of full_name, title, can_sign_i9, "
			"can_sign_w4 or active."
		)

	doc.flags.ignore_permissions = True
	doc.save()

	updated = next(entry for entry in _rows() if entry["user"] == user)
	return ToolResult(
		data={"signer": _describe([updated])[0], "updated": changed},
		summary=f"{updated['full_name']} ({user}) updated: {', '.join(changed)}",
	)


def remove_authorized_signer(args: dict) -> ToolResult:
	"""Deactivate one signer. THE ROW STAYS — see the module docstring.

	There is no delete. A form signed last season was signed by whoever was
	authorised last season, and a roster that forgets its own history cannot
	answer the question an inspection asks. `active` off is what stops the next
	signature; the row is what explains the last one.
	"""
	doc = _settings()
	user = _resolve_user(args)
	row = _find_row(doc, user)
	if row is None:
		raise ToolError(
			f"{user} is not on the authorized signer roster, so there is nothing to "
			f"deactivate. list_authorized_signers has the roster. Nothing was changed."
		)
	if not _flag(row.get("active")):
		raise ToolError(
			f"{user} is already inactive on the authorized signer roster and signs nothing. "
			f"update_authorized_signer(active=true) puts them back. Nothing was changed."
		)

	row.set("active", 0)
	doc.flags.ignore_permissions = True
	doc.save()

	rows = _rows()
	remaining = [entry for entry in rows if entry["active"]]
	data = {
		"user": user,
		"active": False,
		"deleted": False,
		"remaining_active": len(remaining),
		"note": (
			"The row was kept and its active flag cleared. A federal form this person signed "
			"still names somebody this employer had authorised, which a deleted row could not "
			"show."
		),
	}
	summary = f"{user} deactivated as an authorized signer — {len(remaining)} active signer(s) left"
	if not remaining:
		data["warning"] = (
			"NO ACTIVE SIGNERS REMAIN. The roster is still configured, so submit_i9_section_2 "
			"and submit_w4 will now refuse every caller. Reactivate somebody with "
			"update_authorized_signer(active=true) before the next hire."
		)
		summary += " — nobody can sign a federal form until one is reactivated"
	return ToolResult(data=data, summary=summary)


def _forms_sentence(row: dict) -> str:
	"""`Form I-9 and Form W-4`, for a summary line."""
	forms = [name for name, flag in FORM_FLAGS.items() if row.get(flag)]
	return " and ".join(f"Form {name}" for name in forms) if forms else "no forms"


def _default_full_name(user: str) -> str:
	"""The account's own full name, so the common call needs one argument.

	Read defensively: a site whose User doctype answers something unexpected
	gets a required-argument error rather than an exception inside a tool that
	was going to succeed.
	"""
	try:
		return str(frappe.db.get_value("User", user, "full_name") or "").strip()
	except Exception:  # pragma: no cover - a User row that will not read
		return ""
