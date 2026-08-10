// SPDX-License-Identifier: MIT
/**
 * The badge register's own form, with a way to see the badge. v0.56.0.
 *
 * `badge_print_format.py` seeds an "Employee Badge Card" Print Format that lays
 * this record out at CR-80 — the card a DTC4250e takes out of its hopper. Frappe
 * will find it under Menu > Print and then a format dropdown, which is two
 * clicks and a piece of knowledge, and the whole point of the format is that
 * somebody who has just issued a badge can look at it.
 *
 * IT IS A FORM SCRIPT IN THE DOCTYPE'S OWN DIRECTORY AND NOT A `doctype_js`
 * ENTRY. Frappe loads `<doctype>.js` from the folder beside the JSON for every
 * doctype an app ships, so this needs no hook at all — and `hooks.py` keeping
 * one fewer key is worth more than the consistency with the seven map scripts,
 * which are in `public/js` because they SHARE a widget and this shares nothing.
 * `erpnext_mcp_settings.js` is already here on the same footing.
 */

// WRAPPED IN AN IIFE so nothing here declares a global — the same reason
// `farm_task_map.js` gives. A form script is evaluated by appending a <script>
// to the page, and a top-level `const` in a classic script is a global lexical
// binding: a second evaluation of the same file is a SyntaxError that takes the
// whole form script down, and the symptom is a form with no buttons and no
// explanation.

(function () {
	//: The format `badge_print_format.FORMAT_NAME` seeds. The two halves have to
	//: agree and nothing else makes them — `test_badge_print_format.py` reads this
	//: file and asserts the string, which is the same guard `test_hooks.py` puts
	//: on the check template's Jinja global.
	const FORMAT = "Employee Badge Card";

	frappe.ui.form.on("Bucket Log Badge Map", {
		refresh(frm) {
			if (frm.is_new()) {
				return;
			}

			frm.add_custom_button(__("View Badge"), () => {
				// Frappe's own print view, pointed at this app's format. Opened in
				// a new tab rather than navigated to, because the person pressing
				// this has usually just issued the badge and wants the record still
				// in front of them when the card comes out.
				const url = frappe.urllib.get_full_url(
					"/printview?doctype=" +
						encodeURIComponent(frm.doc.doctype) +
						"&name=" +
						encodeURIComponent(frm.doc.name) +
						"&format=" +
						encodeURIComponent(FORMAT) +
						// A letterhead is a band across the top of a sheet of Letter.
						// On an 85.6 x 54mm card it is the top third of the badge.
						"&no_letterhead=1"
				);
				window.open(url, "_blank");
			});

			// A RETIRED BADGE STILL PRINTS, and says RETIRED across the middle of
			// the card. That is the format's job; this is the warning on the form,
			// so nobody gets as far as a wasted blank wondering why.
			if (!frm.doc.active) {
				frm.dashboard.add_comment(
					__(
						"This badge is retired. It resolves to nobody, and a card printed from it is stamped RETIRED."
					),
					"orange",
					true
				);
			}
		},
	});
})();
