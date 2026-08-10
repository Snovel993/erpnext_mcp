# SPDX-License-Identifier: MIT
"""The two badge Client Scripts exactly as v0.56.0 shipped them.

NOT DEAD CODE — THIS IS WHAT IS ON THE SITES. v0.56.1 fixed a sheet that came
out of Save-as-PDF blank, and the fix only reaches a site that already migrated
if `seed_badge_list_action` can recognise the copy IT wrote as its own and
update it. That recognition is a fingerprint in `PRIOR_REVISIONS`, a hash of a
text nobody keeps a copy of — so if the hash is wrong, every existing site is
told "this copy has been edited, left alone" and quietly keeps the bug.

The text below is that copy, lifted out of the v0.56.0 tag. The tests hold the
constants to it, which is the one check that cannot pass while the upgrade path
is broken on a real bench.
"""

LIST_SCRIPT_R1 = """// erpnext_mcp:badge-sheet-action
// Added by erpnext_mcp (v0.56.0). Untick `enabled` above, or delete this row,
// to remove the button — the app will not put it back.
//
// It adds ONE entry to the Employee list's Actions menu, which only appears once
// rows are ticked. It does not replace frappe.listview_settings["Employee"]:
// ERPNext sets the status indicators and default filters there, and assigning
// over the top would silently take those away.

(function () {
	const settings = frappe.listview_settings["Employee"] || {};
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
						method: "erpnext_mcp.badge_sheet.render_badge_sheet",
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

	frappe.listview_settings["Employee"] = settings;
})();
"""

FORM_SCRIPT_R1 = """// erpnext_mcp:badge-card-button
// Added by erpnext_mcp (v0.56.0). Untick `enabled` above, or delete this row,
// to remove the button — the app will not put it back.
//
// It adds an "ID Card" button to the Employee form. The card it shows is drawn
// by the server, in the same markup as the "Employee Badge Card" print format,
// so this file never lays a card out itself.

frappe.ui.form.on("Employee", {
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
					method: "erpnext_mcp.api.badges.employee_badge_card",
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
"""

