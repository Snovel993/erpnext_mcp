# SPDX-License-Identifier: MIT
"""The "Onboard Worker" landing page: hire, badge, print, enrol. v0.83.0.

Every piece of onboarding this app ships already worked before this release, and
that was the problem. `badge_form_action` put "ID Card" on the Employee form in
v0.56.0, `badge_list_action` put "Print Badge Sheet" on the Employee list, the
I-9 and W-4 registers arrived in v0.20.0, and `generate_mobile_login_qr` has
enrolled phones since v0.17.1. Four surfaces, four routes, and nothing anywhere
in the Desk that says what order to do them in.

WHAT AN HR MANAGER ACTUALLY ASKS IS "SOMEBODY STARTS MONDAY, WHAT DO I DO", and
until now the answer was institutional memory. This page is that answer written
down as shortcuts, in the order the day goes.

────────────────────────────────────────────────────────────────────────────
IT IS A WORKSPACE OF SHORTCUTS, NOT A WIZARD
────────────────────────────────────────────────────────────────────────────

`dashboard._build_dispatch_workspace` argues this for the dispatch board and the
argument carries whole: a Workspace is Frappe's own landing page. It renders with
the site's permissions, the site's theme and the site's mobile layout, it keeps
working when Frappe changes its front end, and it costs no custom UI.

A WIZARD WOULD HAVE BEEN THE WRONG SHAPE ANYWAY, and not merely more work. Real
onboarding does not complete in one sitting: somebody is hired on Monday, their
I-9 is verified when their documents turn up on Wednesday, and their phone is
enrolled when they remember to bring it. A wizard models a transaction that
either finishes or is abandoned; this is a checklist somebody comes back to. The
shortcuts are in order and every one of them is reachable on its own.

────────────────────────────────────────────────────────────────────────────
THE PAGE IS NEVER REBUILT OVER SOMEBODY'S ARRANGEMENT
────────────────────────────────────────────────────────────────────────────

`_workspace_is_empty` is the same guard the dispatch board uses and it is the
whole safety property here: a page with blocks on it is somebody's arrangement
and is left exactly as they left it. Only an empty `content` — this app's own
half-built page, or a fresh install — is written.

Best-effort throughout, because the Workspace doctype has been rewritten twice
across the Frappe versions this app supports. Every field and every child table is
written only where the site has it, every shortcut is dropped if its doctype is
absent, and a failure is REPORTED rather than raised. `install.py` prints what it
could not build — the lesson of v0.16.0, where a Kanban insert failed silently and
the migration reported success.
"""

from __future__ import annotations

import json

import frappe

from . import compat
from .dashboard import MODULE, WORKSPACE, _select_value, _slug, _workspace_is_empty

#: The docname and the title. `/app/onboard-worker` is the route Frappe derives.
WORKSPACE_NAME = "Onboard Worker"

EMPLOYEE = "Employee"
BADGE_MAP = "Bucket Log Badge Map"
GRANT = "Mobile Access Grant"

#: The row of shortcuts across the top, IN THE ORDER THE DAY GOES. This ordering
#: is the actual content of this release — the buttons all existed, the sequence
#: did not.
#:
#: 1. Hire them. A `doc_view: New` shortcut, which is the quick-add button, for
#:    the reason `DISPATCH_SHORTCUTS` gives: making somebody open a list to find
#:    New is the friction that ends with the step being skipped.
#: 2. Badge them. This lands on the Employee LIST rather than on a badge screen,
#:    because the badge is issued from the list's "Print Badge Sheet" action and
#:    from the form's "ID Card" button, and both live there. A shortcut cannot
#:    press a button; it can put somebody in front of one.
#: 3. The badge register, for "we printed one, where did it go".
#: 4. The phone. `Mobile Access Grant` is the register `generate_mobile_login_qr`
#:    writes, and it is the one screen that answers "is this person's phone
#:    actually enrolled" — which is not the same question as "did we make a QR".
ONBOARD_SHORTCUTS = (
	{
		"label": "Hire Somebody",
		"link_to": EMPLOYEE,
		"type": "DocType",
		"doc_view": "New",
		"why": "Step one. The Employee record is what every other step here hangs off.",
	},
	{
		"label": "Badge & Print",
		"link_to": EMPLOYEE,
		"type": "DocType",
		"doc_view": "List",
		"why": (
			"Step two. Tick the new starters and use Actions › Print Badge Sheet for a "
			"sheet of cards, or open one and use Badge › ID Card for a single card."
		),
	},
	{
		"label": "Badge Register",
		"link_to": BADGE_MAP,
		"type": "DocType",
		"doc_view": "List",
		"why": "Which badge belongs to whom, and which ones have been retired.",
	},
	{
		"label": "Phone Enrolment",
		"link_to": GRANT,
		"type": "DocType",
		"doc_view": "List",
		"why": (
			"Step three, and the one that is not the same question as 'did we make a QR': "
			"a grant is Active or it is not, and a phone with no live grant cannot claim work."
		),
	},
)

#: The link cards under the shortcuts — the paperwork that follows somebody
#: through their first fortnight rather than their first hour.
#:
#: SEPARATED FROM THE SHORTCUTS ON PURPOSE. The four shortcuts above are Monday
#: morning and they are done in order. These are the things that come back: an
#: I-9 whose documents arrive on Wednesday, a W-4 that needs redoing when somebody
#: marries, a training record that expires. Putting them in the same row would
#: have made the sequence unreadable, which is the one thing this page is for.
ONBOARD_LINK_CARDS = (
	(
		"Right to Work and Tax",
		("I-9 Form", "W-4 Form", "Authorized Signer"),
	),
	(
		"Training and Certification",
		("Employee Training Record", "Training Type", "Certification"),
	),
	(
		"Housing and Transport",
		("Housing Assignment", "Housing Unit"),
	),
)


def available() -> bool:
	"""Whether this site can have the page at all.

	`Workspace` AND `Employee`: a landing page for onboarding on a site with no HR
	app is a page of dead links, and building one would be worse than building
	nothing.
	"""
	return compat.doctype_exists(WORKSPACE) and compat.doctype_exists(EMPLOYEE)


def install_onboard_worker() -> dict:
	"""Build or repair the Onboard Worker workspace.

	Never raises. Returns a report `install.py` can print one true sentence from:
	`{"created": bool, "filled": bool, "existed": bool, "blocks": int,
	"shortcuts": int, "note": str, "failed": list}`.
	"""
	report = {
		"created": False,
		"filled": False,
		"existed": False,
		"blocks": 0,
		"shortcuts": 0,
		"note": "",
		"failed": [],
	}
	if not compat.doctype_exists(WORKSPACE):
		report["note"] = "this site has no Workspace doctype, so there is no landing page to build"
		return report
	if not compat.doctype_exists(EMPLOYEE):
		report["note"] = (
			"this site has no Employee doctype — HR is not installed, so an onboarding "
			"page would be a page of dead links"
		)
		return report

	try:
		existing = frappe.db.exists(WORKSPACE, WORKSPACE_NAME)
		if existing and not _workspace_is_empty(WORKSPACE_NAME):
			# Somebody arranged this page. Leave it exactly as they left it.
			report["existed"] = True
			return report

		doc = frappe.get_doc(WORKSPACE, WORKSPACE_NAME) if existing else frappe.new_doc(WORKSPACE)
		if existing:
			# Repairing a page this app shipped blank. Clear the child tables first
			# so a partial set cannot be doubled.
			for fieldname in ("shortcuts", "links", "number_cards", "charts"):
				if compat.has_field(WORKSPACE, fieldname):
					doc.set(fieldname, [])
		else:
			doc.name = WORKSPACE_NAME
			doc.flags.name_set = True

		for fieldname, value in (
			("title", WORKSPACE_NAME),
			("label", WORKSPACE_NAME),
			("module", MODULE),
			("icon", "users"),
			("public", 1),
			("is_hidden", 0),
			# After the dispatch board (20.0), because dispatch is a daily page and
			# this is a page somebody opens when a new person starts.
			("sequence_id", 21.0),
		):
			if compat.has_field(WORKSPACE, fieldname):
				doc.set(fieldname, value)

		content = _build_content(doc, report)
		if compat.has_field(WORKSPACE, "content"):
			doc.content = json.dumps(content)

		doc.save(ignore_permissions=True) if existing else doc.insert(ignore_permissions=True)
		report["filled" if existing else "created"] = True
		report["blocks"] = len(content)
	except Exception as exc:
		report["failed"].append(
			{"name": f"{WORKSPACE_NAME} workspace", "reason": f"{type(exc).__name__}: {exc}"}
		)
	return report


def _build_content(doc, report: dict) -> list:
	"""Build the page, appending each child row as the block that renders it.

	THE TWO HAVE TO BE WRITTEN TOGETHER, which is `dashboard._workspace_content`'s
	rule and the bug v0.16.0 shipped: in a modern Frappe a Workspace renders ONLY
	what its `content` block list names. A shortcut row with no block is invisible;
	a block naming a row that does not exist is a rendering error. One pass, and
	nothing can drift.
	"""
	content = []

	def block(kind: str, key: str, value: str, col: int) -> None:
		content.append({"id": _slug(f"{kind}-{value}")[:32], "type": kind, "data": {key: value, "col": col}})

	def header(text: str) -> None:
		content.append(
			{
				"id": _slug(f"header-{text}")[:32],
				"type": "header",
				"data": {"text": f'<span class="h4"><b>{text}</b></span>', "col": 12},
			}
		)

	def paragraph(text: str) -> None:
		content.append(
			{"id": _slug(f"para-{text}")[:32], "type": "paragraph", "data": {"text": text, "col": 12}}
		)

	if compat.has_field(WORKSPACE, "shortcuts"):
		header("Somebody starts Monday")
		# THE ORDER IN WORDS AS WELL AS IN THE ROW. A shortcut row reads as four
		# equal choices; the sentence is what makes it a sequence. It is one line
		# because a paragraph nobody reads is worse than no paragraph.
		paragraph(
			"Hire them, badge them, enrol their phone — in that order. "
			"The paperwork below follows in their first fortnight."
		)
		for spec in ONBOARD_SHORTCUTS:
			if not compat.doctype_exists(spec["link_to"]):
				continue
			row = {"label": spec["label"], "link_to": spec["link_to"]}
			kind = _select_value("Workspace Shortcut", "type", spec.get("type") or "DocType")
			if kind:
				row["type"] = kind
			view = _select_value("Workspace Shortcut", "doc_view", spec.get("doc_view") or "")
			if view and compat.has_field("Workspace Shortcut", "doc_view"):
				row["doc_view"] = view
			doc.append("shortcuts", row)
			block("shortcut", "shortcut_name", spec["label"], 3)
			report["shortcuts"] += 1

	if compat.has_field(WORKSPACE, "links"):
		header("The paperwork that follows")
		for card_name, links in ONBOARD_LINK_CARDS:
			present = [link for link in links if compat.doctype_exists(link)]
			if not present:
				continue
			break_row = {"label": card_name, "link_count": len(present)}
			kind = _select_value("Workspace Link", "type", "Card Break")
			if kind:
				break_row["type"] = kind
			doc.append("links", break_row)
			for link in present:
				link_row = {"label": link, "link_to": link}
				kind = _select_value("Workspace Link", "type", "Link")
				if kind:
					link_row["type"] = kind
				link_type = _select_value("Workspace Link", "link_type", "DocType")
				if link_type:
					link_row["link_type"] = link_type
				doc.append("links", link_row)
			block("card", "card_name", card_name, 4)

	return content


def remove_onboard_worker() -> dict:
	"""Take the page off before the app goes.

	THE SAME ASYMMETRY `asset_tag_form_action.remove_asset_tag_form_action` states:
	everything else `before_uninstall` does is REPORT what an uninstall destroys,
	because those are the operator's records. A Workspace this app built is not one
	— left behind it is a page of links to doctypes that no longer exist.

	A PAGE SOMEBODY HAS REARRANGED IS THEIRS AND STAYS. `_workspace_is_empty` is
	the wrong test for that here — a page this app built is not empty — so the
	guard is the one thing that cannot be faked: it is removed only if it still
	carries this app's own module. Never raises.
	"""
	report = {"removed": False, "name": WORKSPACE_NAME, "reason": ""}
	try:
		if not compat.doctype_exists(WORKSPACE):  # pragma: no cover - not a real Frappe
			report["reason"] = "this site has no Workspace doctype"
			return report
		if not frappe.db.exists(WORKSPACE, WORKSPACE_NAME):
			report["reason"] = "not present"
			return report
		if compat.has_field(WORKSPACE, "module"):
			module = frappe.db.get_value(WORKSPACE, WORKSPACE_NAME, "module")
			if module and str(module) != MODULE:
				report["reason"] = (
					f"left alone — this page has been moved to the {module!r} module, so it is "
					"not this app's to delete"
				)
				return report
		frappe.delete_doc(WORKSPACE, WORKSPACE_NAME, ignore_permissions=True, force=True)
		report["removed"] = True
	except Exception as exc:  # pragma: no cover - a site mid-uninstall
		report["reason"] = f"{type(exc).__name__}: {exc}"
	return report


__all__ = (
	"ONBOARD_LINK_CARDS",
	"ONBOARD_SHORTCUTS",
	"WORKSPACE_NAME",
	"available",
	"install_onboard_worker",
	"remove_onboard_worker",
)
