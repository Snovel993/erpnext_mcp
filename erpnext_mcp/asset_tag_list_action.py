# SPDX-License-Identifier: MIT
""" "Generate QR Sheet" on the Asset Register list, as a record and not as a hook. v0.83.0.

`asset_tag_sheet.render_asset_qr_sheet` turns a list of assets into a printable
sheet of labels. The place somebody selects a list of assets is the Asset Register
list view, and this is the one line of JavaScript that connects the two.

────────────────────────────────────────────────────────────────────────────
WHY A CLIENT SCRIPT RATHER THAN `doctype_list_js`
────────────────────────────────────────────────────────────────────────────

`badge_list_action.py` is a Client Script because Employee belongs to ERPNext and
a `doctype_list_js` entry for it would break the promise `hooks.py` opens with.
Asset Register is OURS, so that argument does not apply and the hook was open.

`asset_tag_form_action.py` states the three reasons this feature went to Client
Scripts anyway; the short version is that an operator can SEE this row in the
Desk, can untick `enabled` or delete it and this module will not put it back,
and a hook file gets neither of those. The pair also stays symmetric: the form
button and the list action are two rows of the same kind, seeded and removed by
the same machinery, rather than one row and one file.

It goes when the app goes, because `before_uninstall` names it.

────────────────────────────────────────────────────────────────────────────
THE THREE STATES, AND WHY A SEEDER THAT ONLY CREATES IS HALF A CONTRACT
────────────────────────────────────────────────────────────────────────────

`badge_list_action` argues this at length and its conclusion is inherited whole:

  * NOT THERE — write it. An operator who deleted it is not in this state,
    because they get to keep declining it; a site that has never had it is.
  * THERE AND UNTOUCHED — the stored text fingerprints as one this app shipped
    (`PRIOR_REVISIONS`). Update it, because leaving it would leave a known bug on
    the site and it is this app's own text.
  * THERE AND EDITED — the fingerprint matches nothing this app ever shipped, so
    a person has been in it. LEFT EXACTLY AS IT IS, and `install.py` prints the
    revision they are on and the one they are missing.

WHAT IT DOES NOT DO IS OVERWRITE `frappe.listview_settings["Asset Register"]`.
Nothing sets one today, which makes this the easy case and exactly the case where
an assignment gets written and survives review — until something else on the site
starts setting indicators for retired assets and silently loses them. The script
reads what is there, chains any existing `onload` and adds one entry to the
Actions menu, which is the menu that only appears once somebody has ticked a row.
"""

from __future__ import annotations

import hashlib

import frappe

CLIENT_SCRIPT = "Client Script"
ASSET_REGISTER = "Asset Register"
LIST_VIEW = "List"

#: The name this app gives the row. Frappe autonames Client Script, so this is a
#: request rather than a guarantee — which is why `_existing` looks for the marker.
SCRIPT_NAME = "Asset Register — Generate QR Sheet"

#: HOW THIS APP RECOGNISES ITS OWN ROW. See `asset_tag_form_action.SCRIPT_MARKER`.
SCRIPT_MARKER = "erpnext_mcp:asset-qr-sheet-action"

#: WHICH REVISION OF THE TEXT THIS APP SHIPS. The marker is identity and never
#: changes; this changes every time the script itself does.
SCRIPT_REVISION = "r1"

#: Marker and revision on one line, which is what the seeder matches to answer
#: "is this site already on the current text".
SCRIPT_STAMP = f"{SCRIPT_MARKER}@{SCRIPT_REVISION}"

#: Every earlier text this app shipped, fingerprinted by `_fingerprint`. Empty at
#: r1: there has never been an earlier revision, so any row that is not the
#: current text is one somebody has edited.
PRIOR_REVISIONS: dict[str, str] = {}

#: The method the action calls, spelled once so a test can hold the script and the
#: whitelisted function to the same dotted path.
SHEET_METHOD = "erpnext_mcp.asset_tag_sheet.render_asset_qr_sheet"

SCRIPT_SOURCE = """// %(stamp)s
// Added by erpnext_mcp (v0.83.0). Untick `enabled` above, or delete this row,
// to remove the action — the app will not put it back.
//
// It adds ONE entry to the Asset Register list's Actions menu, which only appears
// once rows are ticked. It does not replace
// frappe.listview_settings["Asset Register"]: nothing sets one today, and a
// script that assigned a fresh object over the top would silently take away
// whatever does later.

(function () {
	// THE SHEET IS HANDED TO THE TAB AS A BLOB URL AND NOT WRITTEN INTO IT.
	// `tab.document.write()` fills in a document whose URL is still
	// `about:blank`, and a browser's print path renders a page by going back to
	// its URL — which for `about:blank` is nothing at all. The sheet looks right
	// on screen and comes out of Save-as-PDF EMPTY. A blob: URL is a real
	// resource the print preview can read a second time. This is the bug
	// v0.56.1 fixed for badges; the asset sheet ships with the fix already in.
	function show_printable(tab, html) {
		var url;
		try {
			// DOMParser rather than string surgery on the <head>: it does not run
			// scripts or fetch images, and it cannot put the tag in the wrong place.
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
			// A browser with no DOMParser, Blob or createObjectURL still gets the
			// sheet on screen the old way.
			tab.document.open();
			tab.document.write(html);
			tab.document.close();
			return;
		}

		tab.location.href = url;

		// REVOKED WHEN THE TAB CLOSES AND NOT ON A TIMER. The print preview reads
		// the URL a second time, so one revoked while the sheet is still open
		// would print exactly the blank page this exists to stop.
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
			__("Generate QR Sheet"),
			function () {
				const names = listview.get_checked_items(true);
				if (!names || !names.length) {
					frappe.msgprint(__("Select the assets you want tags for."));
					return;
				}

				// Opened BEFORE the call, not after. A browser blocks
				// window.open() that did not come from a click, and by the time a
				// server round trip has rendered thirty symbols the click is over
				// — so the tab is claimed while the gesture is still live and
				// filled in when the answer lands.
				const tab = window.open("", "_blank");

				frappe
					.call({
						method: "%(method)s",
						args: { assets: names },
						freeze: true,
						freeze_message: __("Rendering asset tags..."),
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
								__("Allow pop-ups for this site to open the tag sheet.")
							);
							return;
						}
						show_printable(tab, payload.html);

						if (payload.errors && payload.errors.length) {
							// The sheet prints the skipped names too. This is the
							// second half of the same promise: whoever pressed the
							// button finds out without reading the paper.
							frappe.show_alert({
								message: __("{0} of {1} selected had no tag printed - see the sheet.", [
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
						// not coming, so nobody is left with a blank one.
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
""" % {"stamp": SCRIPT_STAMP, "doctype": ASSET_REGISTER, "method": SHEET_METHOD}


def _fingerprint(text: str) -> str:
	"""A hash of a script with line endings and trailing whitespace normalised.

	Spelled out here rather than imported for the reason these modules are
	separate at all: they are independent rows, and neither should stop working
	because the other was refactored.
	"""
	body = str(text or "").replace("\r\n", "\n").strip()
	lines = [line.rstrip() for line in body.split("\n")]
	return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _existing() -> str:
	"""The docname of this app's Client Script on the Asset Register list, or "".

	Matched on the marker in the source. See `SCRIPT_MARKER` for why that and not
	the docname.
	"""
	rows = frappe.db.get_all(
		CLIENT_SCRIPT,
		filters={"dt": ASSET_REGISTER, "view": LIST_VIEW},
		fields=["name", "script"],
		limit=0,
	)
	for row in rows:
		if SCRIPT_MARKER in str(row.get("script") or ""):
			return str(row.get("name"))
	return ""


def seed_asset_tag_list_action() -> dict:
	"""Create the Asset Register list action, or bring this app's own copy up to date.

	The three states are the module docstring's. Never raises. Returns
	`{"created": bool, "updated": bool, "name": str, "revision": str,
	"reason": str}` so `install.py` can say one true sentence whichever of the
	four happened.
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
		if not frappe.db.exists("DocType", ASSET_REGISTER):
			report["reason"] = (
				"this site has no Asset Register doctype — run `bench migrate` for this app first"
			)
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
				report["reason"] = (
					"left alone — this site's copy has been edited, so it is not this app's "
					f"to rewrite. It is missing revision {SCRIPT_REVISION}"
				)
				return report

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
				"dt": ASSET_REGISTER,
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


def remove_asset_tag_list_action() -> dict:
	"""Take this app's action off the Asset Register list before the app goes.

	CLEANUP RATHER THAN A WARNING — see `asset_tag_form_action.remove_asset_tag_form_action`
	for the asymmetry with everything else `before_uninstall` does. Only a row
	still carrying this app's marker is removed, so a script somebody has adopted
	and rewritten stays theirs. Never raises: an uninstall that died here would
	leave the app half-removed.
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
	"PRIOR_REVISIONS",
	"SCRIPT_MARKER",
	"SCRIPT_NAME",
	"SCRIPT_REVISION",
	"SCRIPT_SOURCE",
	"SCRIPT_STAMP",
	"SHEET_METHOD",
	"remove_asset_tag_list_action",
	"seed_asset_tag_list_action",
)
