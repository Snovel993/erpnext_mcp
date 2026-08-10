# SPDX-License-Identifier: MIT
""""ID Card" on the Employee form, as a record and not as a hook. v0.56.0.

`badge_list_action.py` puts "Print Badge Sheet" on the Employee LIST, for thirty
people at once. This is the other half and the one the complaint was actually
about: an HR manager opens somebody's Employee form to find their badge, and
until v0.56.0 there was nothing there to find. The badge existed, the register
knew it, and the two ways to see it were an MCP call and a Bucket Log Badge Map
docname nobody memorises.

────────────────────────────────────────────────────────────────────────────
A CLIENT SCRIPT, FOR THE REASON `badge_list_action.py` GIVES AT LENGTH
────────────────────────────────────────────────────────────────────────────

`hooks.py` opens by promising that installing this app "cannot change the
behaviour of anything already on the site", and `test_hooks.TheFormScripts`
enforces the sharp end of it: every doctype named in `doctype_js` is one this
app created. Employee is ERPNext's. A `doctype_js` entry for it would break that
promise and that test, with the change living in code an operator cannot see or
switch off.

So this is a Client Script row, exactly as the list button is, and it keeps the
same three properties: an operator can SEE it in the Desk, can turn it off or
delete it and this module will not put it back, and it goes when the app goes
because `before_uninstall` names it.

────────────────────────────────────────────────────────────────────────────
WHAT THE BUTTON DOES, AND THE ONE THING IT DELIBERATELY DOES NOT
────────────────────────────────────────────────────────────────────────────

It calls `api/badges.employee_badge_card`, which issues a badge if the person
has none and reprints the one they hold if they do, attaches both files to the
Employee record, and hands back the card as HTML. The dialog shows the card and
links to what landed in the sidebar.

IT DOES NOT DRAW THE CARD ITSELF. The layout is `badge_sheet.card_html`, which
is the same markup and the same millimetres the Desk's own "Employee Badge Card"
print format uses. A card drawn in JavaScript here would be a third layout, and
the failure mode of three layouts is a photograph that sits 2mm differently
depending on which button somebody pressed — which nobody notices until a box of
pre-printed lanyard cards is already cut.

IT IS ON A NEW FORM'S TOOLBAR TOO, AND SAYS WHY IT CANNOT RUN. `frm.is_new()`
is checked in the handler rather than used to hide the button, because a button
that is sometimes absent reads as a broken install; one that is present and
explains itself reads as a form that is not ready yet.
"""

from __future__ import annotations

import frappe

CLIENT_SCRIPT = "Client Script"
EMPLOYEE = "Employee"
FORM_VIEW = "Form"

#: The name this app asks for. Frappe autonames Client Script, so it is a request
#: rather than a guarantee — which is why `_existing` matches on the marker.
SCRIPT_NAME = "Employee — ID Card"

#: HOW THIS APP RECOGNISES ITS OWN ROW. A comment in the source rather than the
#: docname, for the reason `badge_list_action.SCRIPT_MARKER` gives: a seeder that
#: could not find what it wrote last time would write it again at every migrate.
SCRIPT_MARKER = "erpnext_mcp:badge-card-button"

#: The method the button calls, spelled once so a test can hold the script and
#: the whitelisted function to the same dotted path.
CARD_METHOD = "erpnext_mcp.api.badges.employee_badge_card"

SCRIPT_SOURCE = """// %(marker)s
// Added by erpnext_mcp (v0.56.0). Untick `enabled` above, or delete this row,
// to remove the button — the app will not put it back.
//
// It adds an "ID Card" button to the Employee form. The card it shows is drawn
// by the server, in the same markup as the "Employee Badge Card" print format,
// so this file never lays a card out itself.

frappe.ui.form.on("%(doctype)s", {
	refresh: function (frm) {
		frm.add_custom_button(
			__("ID Card"),
			function () {
				if (frm.is_new()) {
					// Present and explaining itself, rather than absent. See the
					// module docstring: a button that comes and goes reads as a
					// broken install.
					frappe.msgprint(__("Save this employee before issuing a badge."));
					return;
				}

				frappe.call({
					method: "%(method)s",
					args: { employee: frm.doc.name },
					freeze: true,
					freeze_message: __("Drawing the badge..."),
				}).then(function (response) {
					const card = (response || {}).message;
					if (!card || !card.html) {
						return;
					}

					const links = [];
					if (card.card_url) {
						links.push(
							'<a href="' + frappe.utils.escape_html(card.card_url) +
								'" target="_blank">' + __("Open the PDF") + "</a>"
						);
					}
					if (card.qr_url) {
						links.push(
							'<a href="' + frappe.utils.escape_html(card.qr_url) +
								'" target="_blank">' + __("Open the QR") + "</a>"
						);
					}

					const dialog = new frappe.ui.Dialog({
						title: __("Badge {0}", [card.badge_id]),
						size: "large",
						fields: [
							{
								fieldtype: "HTML",
								fieldname: "card",
								options:
									'<div style="display:flex;justify-content:center;padding:12px 0">' +
									card.html +
									"</div>" +
									(links.length
										? '<div style="text-align:center;padding-bottom:8px">' +
										  links.join(" &nbsp;·&nbsp; ") +
										  "</div>"
										: "") +
									(card.note
										? '<div class="text-muted small" style="text-align:center">' +
										  frappe.utils.escape_html(card.note) +
										  "</div>"
										: ""),
							},
						],
						// PRINT FROM THE DIALOG, so a bench with no PDF renderer
						// still gets paper. The browser's own print dialog draws
						// the same HTML the PDF would have carried.
						primary_action_label: __("Print"),
						primary_action: function () {
							const tab = window.open("", "_blank");
							if (!tab) {
								frappe.msgprint(__("Allow pop-ups for this site to print the card."));
								return;
							}
							tab.document.open();
							tab.document.write(card.html);
							tab.document.close();
						},
					});
					dialog.show();

					// The sidebar only refetches its attachments on a reload, and
					// the whole point of this feature is that the file is THERE
					// afterwards. Without this the operator prints a card, looks
					// at the sidebar, and sees what was there before they pressed
					// the button.
					if (card.attached || card.qr_url) {
						frm.reload_doc();
					}
				});
			},
			__("Badge")
		);
	},
});
""" % {"marker": SCRIPT_MARKER, "doctype": EMPLOYEE, "method": CARD_METHOD}


def _existing() -> str:
	"""The docname of this app's Client Script on the Employee form, or "".

	Matched on the marker in the source. See `SCRIPT_MARKER` for why that and
	not the docname.
	"""
	# `frappe.db.get_all` rather than `frappe.get_all`, which is this app's
	# convention everywhere and is the right call here for a reason beyond
	# consistency: the permissioned reader answers as the CURRENT USER, and this
	# runs inside `after_migrate` where the question is "is the row there" and
	# not "may this session see it".
	rows = frappe.db.get_all(
		CLIENT_SCRIPT,
		filters={"dt": EMPLOYEE, "view": FORM_VIEW},
		fields=["name", "script"],
		limit=0,
	)
	for row in rows:
		if SCRIPT_MARKER in str(row.get("script") or ""):
			return str(row.get("name"))
	return ""


def seed_badge_form_action() -> dict:
	"""Create the Employee form button if this site has not got it. Never raises.

	IT ONLY EVER CREATES WHAT IS NOT THERE. An operator who deleted it has
	declined it and gets to keep declining it; one who edited it keeps the edit.
	The same contract `seed_i9_print_format` and `seed_badge_list_action` keep,
	and it matters here for the same reason it matters there — this is a button
	on somebody else's form, and a customisation that reappears at every
	`bench migrate` is one nobody can decline.

	Returns `{"created": bool, "name": str, "reason": str}` so `install.py` can
	say one true sentence either way.
	"""
	report = {"created": False, "name": SCRIPT_NAME, "reason": ""}
	try:
		if not frappe.db.exists("DocType", CLIENT_SCRIPT):  # pragma: no cover - not a real Frappe
			report["reason"] = "this site has no Client Script doctype"
			return report
		if not frappe.db.exists("DocType", EMPLOYEE):
			report["reason"] = "this site has no Employee doctype — HR is not installed"
			return report

		found = _existing()
		if found:
			report["name"] = found
			report["reason"] = "already present"
			return report

		doc = frappe.get_doc(
			{
				"doctype": CLIENT_SCRIPT,
				"name": SCRIPT_NAME,
				"dt": EMPLOYEE,
				"view": FORM_VIEW,
				"enabled": 1,
				"script": SCRIPT_SOURCE,
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		report["created"] = True
		report["name"] = doc.name
	except Exception as exc:  # pragma: no cover - a site mid-migrate
		report["reason"] = f"{type(exc).__name__}: {exc}"
	return report


def remove_badge_form_action() -> dict:
	"""Take this app's button off the Employee form before the app goes.

	CLEANUP RATHER THAN A WARNING, and `install._remove_badge_list_action`
	argues the asymmetry for the list button: everything else `before_uninstall`
	does is REPORT what an uninstall will destroy, because those are the
	operator's records. A Client Script this app wrote onto ERPNext's Employee
	form is not the operator's record — left behind it is a button calling a
	method that no longer exists, which is the exact outcome `hooks.py` promises
	against.

	It only removes a row still carrying this app's marker, so a script somebody
	has adopted and rewritten stays theirs. Never raises: an uninstall that died
	here would leave the app half-removed.
	"""
	report = {"removed": False, "name": SCRIPT_NAME, "reason": ""}
	try:
		if not frappe.db.exists("DocType", CLIENT_SCRIPT):  # pragma: no cover - not a real Frappe
			report["reason"] = "this site has no Client Script doctype"
			return report
		found = _existing()
		if not found:
			report["reason"] = "not present"
			return report
		report["name"] = found
		frappe.delete_doc(CLIENT_SCRIPT, found, ignore_permissions=True, force=True)
		report["removed"] = True
	except Exception as exc:  # pragma: no cover - a site mid-uninstall
		report["reason"] = f"{type(exc).__name__}: {exc}"
	return report


__all__ = (
	"CARD_METHOD",
	"SCRIPT_MARKER",
	"SCRIPT_NAME",
	"SCRIPT_SOURCE",
	"remove_badge_form_action",
	"seed_badge_form_action",
)
