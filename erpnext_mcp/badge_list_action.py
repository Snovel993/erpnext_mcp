# SPDX-License-Identifier: MIT
""""Print Badge Sheet" on the Employee list, as a record and not as a hook. v0.56.0.

`badge_sheet.render_badge_sheet` turns a list of employees into a printable sheet
of CR-80 cards. The place an HR manager selects a list of employees is the
Employee list view, and that is the one surface in this whole feature that
belongs to somebody else.

────────────────────────────────────────────────────────────────────────────
WHY THIS IS NOT `doctype_list_js`, WHICH IS THE OBVIOUS WAY TO DO IT
────────────────────────────────────────────────────────────────────────────

`hooks.py` opens by promising that installing this app "cannot change the
behaviour of anything already on the site", and `test_hooks.TheFormScripts`
enforces the sharp end of it: every doctype named in `doctype_js` is one this app
created. A `doctype_list_js` entry for Employee would be the same claim broken
the same way — ERPNext's Employee list, behaving differently because we are
installed, with the change living in code an operator cannot see or switch off.

THE APP ALREADY REACHES ONTO OTHER PEOPLE'S DOCTYPES TWICE, and both times by
the same route: as a RECORD. `compliance_fields.py` adds Custom Fields to Spray
Log, Employee and Attendance — argued at length, behind a switch, with every
column named in `before_uninstall`. `badges.install_badge_logo_field` adds
`Company.badge_logo`, which is this same feature's own mark. Neither is a hook.

So this is a Client Script row. The difference is not pedantry:

  * An operator can SEE it. It is in the Desk under Client Script, with the
    source in front of them, next to every other customisation on the site.
  * An operator can TURN IT OFF — untick `enabled` — or delete it, and this
    module will not put it back. `seed_badge_list_action` only ever creates what
    is not there, the same contract `seed_i9_print_format` keeps and for the same
    reason: a customisation that reappears at every `bench migrate` is one nobody
    can decline.
  * It goes when the app goes, because `before_uninstall` names it.

WHAT IT DOES NOT DO IS OVERWRITE `frappe.listview_settings["Employee"]`. ERPNext
sets its own — the status indicator colours, the default filters — and a script
that assigned a fresh object over the top would take those away and look like an
ERPNext bug. The script below reads what is there, chains the existing `onload`
and adds one entry to the Actions menu, which is the menu that only appears once
somebody has ticked a row.
"""

from __future__ import annotations

import frappe

CLIENT_SCRIPT = "Client Script"
EMPLOYEE = "Employee"
LIST_VIEW = "List"

#: The name this app gives the row. Frappe autonames Client Script, so this is a
#: request rather than a guarantee — which is why `_existing` looks for the marker
#: below rather than for this string.
SCRIPT_NAME = "Employee — Print Badge Sheet"

#: HOW THIS APP RECOGNISES ITS OWN ROW. A comment in the source rather than the
#: docname, because Frappe's autonaming may not honour the name asked for and a
#: seeder that could not find what it wrote last time would write it again at
#: every migrate. An operator who edits the script keeps the marker and keeps
#: their edit; one who deletes the marker has adopted the script, and this module
#: leaves it alone either way.
SCRIPT_MARKER = "erpnext_mcp:badge-sheet-action"

#: The method the button calls, spelled once so the test can hold the script and
#: the whitelisted function to the same dotted path.
SHEET_METHOD = "erpnext_mcp.badge_sheet.render_badge_sheet"

SCRIPT_SOURCE = """// %(marker)s
// Added by erpnext_mcp (v0.56.0). Untick `enabled` above, or delete this row,
// to remove the button — the app will not put it back.
//
// It adds ONE entry to the Employee list's Actions menu, which only appears once
// rows are ticked. It does not replace frappe.listview_settings["Employee"]:
// ERPNext sets the status indicators and default filters there, and assigning
// over the top would silently take those away.

(function () {
	const settings = frappe.listview_settings["%(doctype)s"] || {};
	const previous_onload = settings.onload;

	settings.onload = function (listview) {
		if (typeof previous_onload === "function") {
			previous_onload(listview);
		}

		listview.page.add_actions_menu_item(
			__("Print Badge Sheet"),
			function () {
				const names = listview.get_checked_items(true);
				if (!names || !names.length) {
					frappe.msgprint(__("Select the employees whose badges you want to print."));
					return;
				}

				// Opened BEFORE the call, not after. A browser blocks
				// window.open() that did not come from a click, and by the time a
				// server round trip has issued thirty badges the click is over —
				// so the tab is claimed while the gesture is still live and filled
				// in when the answer lands.
				const tab = window.open("", "_blank");

				frappe
					.call({
						method: "%(method)s",
						args: { employees: names },
						freeze: true,
						freeze_message: __("Issuing badges and drawing cards..."),
					})
					.then(function (response) {
						const payload = (response || {}).message;
						if (!payload || !payload.html) {
							if (tab) {
								tab.close();
							}
							return;
						}
						if (!tab) {
							frappe.msgprint(
								__("Allow pop-ups for this site to open the badge sheet.")
							);
							return;
						}
						tab.document.open();
						tab.document.write(payload.html);
						tab.document.close();

						if (payload.errors && payload.errors.length) {
							// The sheet prints the skipped names too. This is the
							// second half of the same promise: whoever pressed the
							// button finds out without reading the paper.
							frappe.show_alert({
								message: __("{0} of {1} selected had no card printed - see the sheet.", [
									payload.errors.length,
									names.length,
								]),
								indicator: "orange",
							});
						}
					})
					.catch(function () {
						// frappe.call has already shown the server's message. This
						// only closes the tab that was claimed for a sheet that is
						// not coming, so the operator is not left with a blank one.
						if (tab) {
							tab.close();
						}
					});
			},
			false
		);
	};

	frappe.listview_settings["%(doctype)s"] = settings;
})();
""" % {"marker": SCRIPT_MARKER, "doctype": EMPLOYEE, "method": SHEET_METHOD}


def _existing() -> str:
	"""The docname of this app's Client Script on the Employee list, or "".

	Matched on the marker in the source. See `SCRIPT_MARKER` for why that and not
	the docname.
	"""
	rows = frappe.db.get_all(
		CLIENT_SCRIPT,
		filters={"dt": EMPLOYEE, "view": LIST_VIEW},
		fields=["name", "script"],
		limit_page_length=0,
	)
	for row in rows:
		if SCRIPT_MARKER in str(row.get("script") or ""):
			return str(row.get("name"))
	return ""


def seed_badge_list_action() -> dict:
	"""Create the Employee list button if this site has not got it. Never raises.

	IT ONLY EVER CREATES WHAT IS NOT THERE. An operator who deleted it has
	declined it and gets to keep declining it; one who edited it keeps the edit.
	Same contract as `seed_i9_print_format`, and the module docstring argues why
	it matters more here — this is a row on somebody else's list view.

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
				"view": LIST_VIEW,
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


def remove_badge_list_action() -> dict:
	"""Delete this app's Employee list button. Called from `before_uninstall`.

	THIS IS THE HALF THAT MAKES THE CHOICE IN THE MODULE DOCSTRING HONEST. A
	Client Script is a row in Frappe's own table, so uninstalling this app would
	NOT take it with the app's doctypes — it would leave a button on the Employee
	list calling a method that no longer exists, which is the exact "form that
	behaves differently and no way to find out why" `hooks.py` promises against.

	Only ever removes a row still carrying `SCRIPT_MARKER`, so a script an
	operator has adopted and rewritten is left where it is.
	"""
	report = {"removed": False, "name": "", "reason": ""}
	try:
		found = _existing()
		if not found:
			report["reason"] = "not present"
			return report
		frappe.delete_doc(CLIENT_SCRIPT, found, ignore_permissions=True, force=True)
		report["removed"] = True
		report["name"] = found
	except Exception as exc:  # pragma: no cover
		report["reason"] = f"{type(exc).__name__}: {exc}"
	return report


__all__ = (
	"SCRIPT_MARKER",
	"SCRIPT_NAME",
	"SCRIPT_SOURCE",
	"SHEET_METHOD",
	"remove_badge_list_action",
	"seed_badge_list_action",
)
