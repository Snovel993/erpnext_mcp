# SPDX-License-Identifier: MIT
""" "QR Tag" on the Asset Register form, as a record and not as a hook. v0.83.0.

`asset_tag_list_action.py` puts "Generate QR Sheet" on the Asset Register LIST,
for thirty machines at once. This is the other half and the one somebody standing
at a bench actually wants: a valve open on the screen, and a label for it.

────────────────────────────────────────────────────────────────────────────
A CLIENT SCRIPT, EVEN THOUGH `doctype_js` WOULD BE ALLOWED HERE
────────────────────────────────────────────────────────────────────────────

This is the interesting difference from `badge_form_action.py`. That module is a
Client Script row because it HAD to be: Employee is ERPNext's doctype, and
`test_hooks.TheFormScripts` enforces that every doctype named in `doctype_js` is
one this app created. Asset Register is ours, and it is already in `doctype_js`
carrying the map widget — so the hook was open to this button and was not taken.

THREE REASONS, AND THE FIRST IS THE ONE THAT DECIDED IT:

  1. `test_hooks.TheFormScripts.test_the_shared_widget_is_listed_first_everywhere`
     asserts `len(files) == 2` — "one widget plus one per-doctype script" — for
     every entry. A third file on Asset Register does not break the form; it
     breaks the assertion that keeps the map widget's load order honest, and
     loosening a load-bearing test to hang a button off it is the wrong trade.
  2. AN OPERATOR CAN SEE AND DECLINE THIS ONE. It is in the Desk under Client
     Script with the source in front of them, they can untick `enabled` or delete
     it, and this module will not put it back. A `doctype_js` file reappears at
     every `bench migrate` and cannot be turned off without editing the app.
  3. It is the same three-state seeder the badge buttons use, so a fix to the
     script reaches sites that have already migrated. `doctype_js` gets that for
     free, but only by taking (2) away.

It goes when the app goes either way: `before_uninstall` names this row, exactly
as it names the two badge rows.

────────────────────────────────────────────────────────────────────────────
WHAT THE BUTTON DOES
────────────────────────────────────────────────────────────────────────────

It calls `api/asset_tags.asset_qr_tag`, which renders the symbol from the
register's own `qr_url` and hands back both the symbol and a printable label. The
dialog shows the symbol large — a QR somebody is about to stick on a pump is
worth looking at before it is printed — and the Print button prints the label.

IT DOES NOT DRAW THE LABEL ITSELF. The layout is `asset_tag_sheet.sheet_html`,
which is the same markup and the same millimetres the list action's sheet uses. A
label drawn in JavaScript here would be a second layout that drifts from the
first, and the drift would show up as tags that print inside the die cut from one
button and across the perforation from the other.

IT WRITES NOTHING. Printing a tag is not a sighting of the machine — see
`api/asset_tags.py` on why this is a `read` permission and why `last_scan_at`
stays where `scan_asset` left it. So there is no `frm.reload_doc()` at the end of
this one, unlike the badge button: nothing about the record has changed.
"""

from __future__ import annotations

import hashlib

import frappe

CLIENT_SCRIPT = "Client Script"
ASSET_REGISTER = "Asset Register"
FORM_VIEW = "Form"

#: The name this app asks for. Frappe autonames Client Script, so it is a request
#: rather than a guarantee — which is why `_existing` matches on the marker.
SCRIPT_NAME = "Asset Register — QR Tag"

#: HOW THIS APP RECOGNISES ITS OWN ROW. A comment in the source rather than the
#: docname, for the reason `badge_list_action.SCRIPT_MARKER` gives at length: a
#: seeder that could not find what it wrote last time would write it again at
#: every migrate.
SCRIPT_MARKER = "erpnext_mcp:asset-qr-tag-button"

#: WHICH REVISION OF THE TEXT THIS APP SHIPS. The marker above is identity and
#: never changes; this changes with the script.
SCRIPT_REVISION = "r1"

#: Marker and revision on one line — what the seeder matches to answer "is this
#: site already on the current text".
SCRIPT_STAMP = f"{SCRIPT_MARKER}@{SCRIPT_REVISION}"

#: Every earlier text this app shipped, fingerprinted by `_fingerprint`. Empty at
#: r1 and that is not a placeholder: there has never been an earlier revision, so
#: any row on a site that is not the current text is one somebody has edited.
PRIOR_REVISIONS: dict[str, str] = {}

#: The method the button calls, spelled once so a test can hold the script and the
#: whitelisted function to the same dotted path.
TAG_METHOD = "erpnext_mcp.api.asset_tags.asset_qr_tag"

SCRIPT_SOURCE = """// %(stamp)s
// Added by erpnext_mcp (v0.83.0). Untick `enabled` above, or delete this row,
// to remove the button — the app will not put it back.
//
// It adds a "QR Tag" button to the Asset Register form. The label it prints is
// drawn by the server, in the same millimetres the "Generate QR Sheet" list
// action uses, so this file never lays a label out itself.

(function () {
	// THE LABEL IS HANDED TO THE TAB AS A BLOB URL AND NOT WRITTEN INTO IT.
	// The same fix `badge_form_action` carries, for the same reason:
	// `tab.document.write()` fills in a document whose URL is still
	// `about:blank`, and a browser's print path renders a page by going back to
	// its URL — which for `about:blank` is nothing. The label looked right on
	// screen and came out of Save-as-PDF EMPTY.
	//
	// The <base> is written in for the same reason it is there: nothing relative
	// resolves against a blob: URL. This document carries its symbol as a data:
	// URI and needs no <base> today, but a layout that later grows a company
	// logo would lose it silently, and the line costs nothing.
	function show_printable(tab, html) {
		var url;
		try {
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
			// label on screen the old way.
			tab.document.open();
			tab.document.write(html);
			tab.document.close();
			return;
		}

		tab.location.href = url;

		// REVOKED WHEN THE TAB CLOSES AND NOT ON A TIMER: the print preview reads
		// the URL a second time, and one revoked while the label is still open is
		// the blank page this exists to stop.
		var watch = setInterval(function () {
			if (!tab || tab.closed) {
				clearInterval(watch);
				URL.revokeObjectURL(url);
			}
		}, 5000);
	}

	frappe.ui.form.on("%(doctype)s", {
		refresh: function (frm) {
			frm.add_custom_button(
				__("QR Tag"),
				function () {
					if (frm.is_new()) {
						// Present and explaining itself, rather than absent. A
						// button that comes and goes reads as a broken install.
						frappe.msgprint(__("Save this asset before printing a tag."));
						return;
					}

					frappe.call({
						method: "%(method)s",
						args: { asset_name: frm.doc.name },
						freeze: true,
						freeze_message: __("Rendering the tag..."),
					}).then(function (response) {
						const tag = (response || {}).message;
						if (!tag || !tag.png_data_uri) {
							// A bench with no QR encoder answers with no symbol.
							// Say so rather than opening an empty dialog.
							frappe.msgprint(
								__("This site produced no QR symbol for {0}.", [frm.doc.name])
							);
							return;
						}

						const caption = [tag.asset_type, tag.location]
							.filter(function (part) {
								return part;
							})
							.join(" · ");

						const dialog = new frappe.ui.Dialog({
							title: __("QR Tag {0}", [tag.asset_name]),
							size: "small",
							fields: [
								{
									fieldtype: "HTML",
									fieldname: "tag",
									options:
										'<div style="text-align:center;padding:12px 0">' +
										'<img src="' + tag.png_data_uri +
											'" style="width:220px;height:220px;image-rendering:pixelated" alt="">' +
										'<div style="font-weight:600;margin-top:8px;word-break:break-all">' +
										frappe.utils.escape_html(tag.asset_name) +
										"</div>" +
										(caption
											? '<div class="text-muted small">' +
											  frappe.utils.escape_html(caption) +
											  "</div>"
											: "") +
										(tag.qr_url
											? '<div class="text-muted small" style="margin-top:6px;word-break:break-all">' +
											  frappe.utils.escape_html(tag.qr_url) +
											  "</div>"
											: "") +
										"</div>",
								},
							],
							// PRINT FROM THE DIALOG, so a bench with no PDF renderer
							// still gets paper. The browser's own print dialog draws
							// the label the server laid out — see `show_printable`
							// for how it gets there and why it is not written in.
							primary_action_label: __("Print"),
							primary_action: function () {
								const tab = window.open("", "_blank");
								if (!tab) {
									frappe.msgprint(__("Allow pop-ups for this site to print the tag."));
									return;
								}
								show_printable(tab, tag.html);
							},
						});
						dialog.show();
					});
				},
				__("Tags")
			);
		},
	});
})();
""" % {"stamp": SCRIPT_STAMP, "doctype": ASSET_REGISTER, "method": TAG_METHOD}


def _fingerprint(text: str) -> str:
	"""A hash of a script with line endings and trailing whitespace normalised.

	The same function `badge_form_action._fingerprint` is, and spelled out here
	rather than imported for the reason those two modules are separate at all:
	these are independent rows on a form, and none should stop working because
	another was refactored.
	"""
	body = str(text or "").replace("\r\n", "\n").strip()
	lines = [line.rstrip() for line in body.split("\n")]
	return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _existing() -> str:
	"""The docname of this app's Client Script on the Asset Register form, or "".

	Matched on the marker in the source. See `SCRIPT_MARKER` for why that and not
	the docname.
	"""
	# `frappe.db.get_all` rather than `frappe.get_all`, which is this app's
	# convention everywhere and is right here for a reason beyond consistency: the
	# permissioned reader answers as the CURRENT USER, and this runs inside
	# `after_migrate` where the question is "is the row there" and not "may this
	# session see it".
	rows = frappe.db.get_all(
		CLIENT_SCRIPT,
		filters={"dt": ASSET_REGISTER, "view": FORM_VIEW},
		fields=["name", "script"],
		limit=0,
	)
	for row in rows:
		if SCRIPT_MARKER in str(row.get("script") or ""):
			return str(row.get("name"))
	return ""


def seed_asset_tag_form_action() -> dict:
	"""Create the Asset Register form button, or bring this app's own copy up to date.

	THE THREE STATES ARE ARGUED IN `badge_list_action`'s MODULE DOCSTRING and this
	keeps them exactly: absent is written, a copy still fingerprinting as one this
	app shipped is updated, and a copy an operator has edited is left alone and
	reported. An operator who deleted the row is none of the three — the seeder
	never sees it, and they keep declining it.

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
				# SOMEBODY HAS BEEN IN IT. Their edit stands, and the caller is told
				# plainly rather than left thinking the fix went out.
				report["reason"] = (
					"left alone — this site's copy has been edited, so it is not this app's "
					f"to rewrite. It is missing revision {SCRIPT_REVISION}"
				)
				return report

			# This app's own text, unchanged since it wrote it. Saved through the
			# document rather than `db.set_value` so Client Script's `on_update`
			# runs and the Desk stops serving the cached old one.
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


def remove_asset_tag_form_action() -> dict:
	"""Take this app's button off the Asset Register form before the app goes.

	CLEANUP RATHER THAN A WARNING, and `install._remove_badge_list_action` argues
	the asymmetry: everything else `before_uninstall` does is REPORT what an
	uninstall will destroy, because those are the operator's records. A Client
	Script this app wrote is not one of those — left behind it is a button calling
	a method that no longer exists.

	Asset Register goes with the app, so the form this row hangs on goes too. The
	row is removed anyway rather than left to be orphaned, because a Client Script
	pointing at a doctype that no longer exists is a row somebody has to work out
	the provenance of later.

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
	"PRIOR_REVISIONS",
	"SCRIPT_MARKER",
	"SCRIPT_NAME",
	"SCRIPT_REVISION",
	"SCRIPT_SOURCE",
	"SCRIPT_STAMP",
	"TAG_METHOD",
	"remove_asset_tag_form_action",
	"seed_asset_tag_form_action",
)
