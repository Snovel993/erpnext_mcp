# SPDX-License-Identifier: MIT
""" "Print Badge Sheet" on the Employee list, as a record and not as a hook. v0.56.1.

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
    module will not put it back. A customisation that reappears at every
    `bench migrate` is one nobody can decline.
  * It goes when the app goes, because `before_uninstall` names it.

────────────────────────────────────────────────────────────────────────────
AND YET IT CAN BE FIXED IN PLACE, WHICH v0.56.0 COULD NOT DO
────────────────────────────────────────────────────────────────────────────

v0.56.0's seeder created the row when it was absent and did nothing at all when
it was present, which was the right half of the contract and only the right
half. The sheet it wrote in that release came out of "Save as PDF" BLANK — see
`SCRIPT_SOURCE` for why — and every site that had already migrated was stuck
with the broken copy, because a seeder that never updates cannot ship a fix.

So `seed_badge_list_action` now recognises THREE states and not two:

  * NOT THERE — write it. An operator who deleted it is not in this state,
    because they get to keep declining it; a site that has never had it is.
  * THERE AND UNTOUCHED — the stored text fingerprints as one this app shipped
    (`PRIOR_REVISIONS`). Update it. Nobody's work is being overwritten: it is
    this app's own text, and leaving it would leave a known bug on the site.
  * THERE AND EDITED — the fingerprint matches nothing this app ever shipped, so
    a person has been in it. LEFT EXACTLY AS IT IS, and `install.py` prints the
    revision they are on and the one they are missing, because an operator whose
    edit is silently kept and silently stale has been told nothing.

The fingerprint is a hash of the whole script with line endings and trailing
whitespace normalised, which is deliberately strict: "did anybody touch this" is
the question, and a single character of theirs makes it theirs.


WHAT IT DOES NOT DO IS OVERWRITE `frappe.listview_settings["Employee"]`. ERPNext
sets its own — the status indicator colours, the default filters — and a script
that assigned a fresh object over the top would take those away and look like an
ERPNext bug. The script below reads what is there, chains the existing `onload`
and adds one entry to the Actions menu, which is the menu that only appears once
somebody has ticked a row.
"""

from __future__ import annotations

import hashlib

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

#: WHICH REVISION OF THE TEXT THIS APP SHIPS. `SCRIPT_MARKER` is identity and
#: never changes; this changes every time the script itself does. It is stamped
#: into the first line of the source so an operator reading the row in the Desk
#: can see which one they are looking at without diffing anything.
SCRIPT_REVISION = "r2"

#: Marker and revision on one line, which is what the seeder matches to answer
#: "is this site already on the current text".
SCRIPT_STAMP = f"{SCRIPT_MARKER}@{SCRIPT_REVISION}"

#: EVERY EARLIER TEXT THIS APP SHIPPED, fingerprinted by `_fingerprint`. This is
#: the whole of how the seeder tells "the copy we wrote last release" from "the
#: copy an operator has since edited". A hash rather than the text itself
#: because the alternative is carrying every retired revision of the script in
#: this file forever, and forty lines of dead JavaScript per release is how a
#: module stops being readable.
PRIOR_REVISIONS = {
	"02be7ace80a72addfc60486070e90f1036e866cad3159226777b1d92c0970185": (
		"r1 — v0.56.0, which wrote the sheet into the tab with document.write"
	),
}

#: The method the button calls, spelled once so the test can hold the script and
#: the whitelisted function to the same dotted path.
SHEET_METHOD = "erpnext_mcp.badge_sheet.render_badge_sheet"

SCRIPT_SOURCE = """// %(stamp)s
// Added by erpnext_mcp (v0.56.1). Untick `enabled` above, or delete this row,
// to remove the button — the app will not put it back.
//
// It adds ONE entry to the Employee list's Actions menu, which only appears once
// rows are ticked. It does not replace frappe.listview_settings["Employee"]:
// ERPNext sets the status indicators and default filters there, and assigning
// over the top would silently take those away.

(function () {
	// THE SHEET IS HANDED TO THE TAB AS A BLOB URL AND NOT WRITTEN INTO IT.
	// v0.56.1, and it is a bug fix rather than a tidy-up. `tab.document.write()`
	// fills in a document whose URL is still `about:blank`, and a browser's
	// print path renders a page by going back to its URL — which for
	// `about:blank` is nothing at all. The sheet looked right on screen and came
	// out of Save-as-PDF EMPTY. A blob: URL is a real resource the print preview
	// can read a second time and get the same sheet back from.
	//
	// THE <base> IS WHAT KEEPS THE PHOTOGRAPHS ON IT. The cards carry
	// `/files/…` and `/private/files/…` image URLs, which a browser resolves
	// against the document's own URL, and NOTHING resolves against a blob: one —
	// so without this every photo and company logo on the sheet would come out
	// broken. The site's own origin is written in before the document leaves,
	// and the tab is the operator's own signed-in one, so the private files
	// still fetch.
	function show_printable(tab, html) {
		var url;
		try {
			// DOMParser rather than string surgery on the <head>: it does not
			// run scripts or fetch images, and it cannot put the tag in the
			// wrong place.
			var doc = new DOMParser().parseFromString(html, "text/html");
			var base = doc.createElement("base");
			base.setAttribute("href", window.location.origin + "/");
			doc.head.insertBefore(base, doc.head.firstChild);
			url = URL.createObjectURL(
				new Blob(["<!doctype html>\\n" + doc.documentElement.outerHTML], {
					type: "text/html;charset=utf-8",
				})
			);
		} catch (error) {
			// A browser with no DOMParser, Blob or createObjectURL still gets
			// the sheet on screen the old way. Only its PDF is worse off, which
			// is the state every browser was in before this release.
			tab.document.open();
			tab.document.write(html);
			tab.document.close();
			return;
		}

		tab.location.href = url;

		// REVOKED WHEN THE TAB CLOSES AND NOT ON A TIMER. The print preview
		// reads the URL a second time, so a URL revoked while the sheet is
		// still open would print exactly the blank page this exists to stop.
		var watch = setInterval(function () {
			if (!tab || tab.closed) {
				clearInterval(watch);
				URL.revokeObjectURL(url);
			}
		}, 5000);
	}

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
						show_printable(tab, payload.html);

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
""" % {"stamp": SCRIPT_STAMP, "doctype": EMPLOYEE, "method": SHEET_METHOD}


def _fingerprint(text: str) -> str:
	"""A hash of a script with line endings and trailing whitespace normalised.

	NORMALISED BECAUSE THE QUESTION IS "HAS A PERSON BEEN IN THIS", and a text
	field that came back from MySQL with `\\r\\n`, or lost its final newline on the
	way through a form, has not been edited by anybody. Everything else is left
	exactly as it is: one character of somebody's own makes the script theirs,
	and this app does not overwrite it.
	"""
	body = str(text or "").replace("\r\n", "\n").strip()
	lines = [line.rstrip() for line in body.split("\n")]
	return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


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
	"""Create the Employee list button, or bring this app's own copy up to date.

	THE THREE STATES ARE ARGUED IN THE MODULE DOCSTRING and this is the whole of
	the code for them: absent is written, a copy still fingerprinting as one this
	app shipped is updated, and a copy somebody has edited is left alone and
	reported. An operator who deleted the row is not any of the three — the
	seeder never sees it, and they keep declining it.

	Never raises. Returns `{"created": bool, "updated": bool, "name": str,
	"revision": str, "reason": str}` so `install.py` can say one true sentence
	whichever of the four happened.
	"""
	report = {
		"created": False,
		"updated": False,
		"name": SCRIPT_NAME,
		"revision": SCRIPT_REVISION,
		"reason": "",
	}
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
			stored = str(frappe.db.get_value(CLIENT_SCRIPT, found, "script") or "")
			if SCRIPT_STAMP in stored:
				report["reason"] = "already present"
				return report

			shipped = PRIOR_REVISIONS.get(_fingerprint(stored))
			if not shipped:
				# SOMEBODY HAS BEEN IN IT. Their edit stands, and the caller is
				# told so plainly rather than left thinking the fix went out.
				report["reason"] = (
					"left alone — this site's copy has been edited, so it is not this app's "
					f"to rewrite. It is missing revision {SCRIPT_REVISION}"
				)
				return report

			# THIS APP'S OWN TEXT, unchanged since it wrote it. Updated through
			# the document rather than `db.set_value` so Client Script's own
			# `on_update` runs and the Desk stops serving the cached old one.
			doc = frappe.get_doc(CLIENT_SCRIPT, found)
			doc.script = SCRIPT_SOURCE
			doc.flags.ignore_permissions = True
			doc.save(ignore_permissions=True)
			report["updated"] = True
			report["reason"] = f"updated from {shipped.split(' —')[0]} to {SCRIPT_REVISION}"
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
	"PRIOR_REVISIONS",
	"SCRIPT_MARKER",
	"SCRIPT_NAME",
	"SCRIPT_REVISION",
	"SCRIPT_SOURCE",
	"SCRIPT_STAMP",
	"SHEET_METHOD",
	"remove_badge_list_action",
	"seed_badge_list_action",
)
