// SPDX-License-Identifier: MIT
//
// /app/mobile-onboarding — enrolling a worker's phone, from a page somebody can
// stand at with them. See erpnext_mcp/mobile_onboarding.py, which argues the
// whole thing; the short version of what this file is allowed to do:
//
//   * It CALLS four whitelisted methods and renders what they return. It makes
//     no decision of its own about who may enrol, which role exists, or whether
//     a card can be drawn — every one of those is answered by the server, and
//     the answers arrive with the sentence to show.
//
//   * The readiness banner is rendered from `blockers` at load time and the
//     button is disabled behind it. That is a courtesy and not a control: the
//     same check runs again on the server before anything is written, because a
//     disabled button is a suggestion.
//
//   * THE SECRET IS NEVER IN THIS FILE'S HANDS. The response carries a PNG and
//     no credential; the credential is inside the symbol, and the only thing
//     that reads it is the phone's camera.
//
// THE CARD IS HANDED TO A TAB AS A BLOB URL AND NOT WRITTEN INTO ONE, which is
// the bug badge_list_action.js fixed in v0.56.1 and the reason it is worth
// copying rather than rediscovering: `tab.document.write()` fills in a document
// whose URL is still about:blank, and a browser's print path renders a page by
// going back to its URL. The card looked right on screen and came out of
// Save-as-PDF EMPTY. A blob: URL is a real resource the print preview can read a
// second time. There is no <base> to add here — the card's only image is a data:
// URI, so nothing on it resolves against the document's own URL.

frappe.pages["mobile-onboarding"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Mobile Onboarding"),
		single_column: true,
	});
	wrapper.mobile_onboarding = erpnext_mcp_mobile_onboarding(page);
};

frappe.pages["mobile-onboarding"].on_page_show = function (wrapper) {
	// The roster goes stale while somebody is on another page — a peer enrols
	// two pickers, a sweep revokes a token — so it is re-read on every return
	// rather than only at load.
	if (wrapper.mobile_onboarding) {
		wrapper.mobile_onboarding.refresh_roster();
	}
};

function erpnext_mcp_mobile_onboarding(page) {
	const METHOD = "erpnext_mcp.mobile_onboarding.";
	let context = null;
	let card = null;

	function esc(value) {
		return String(value === null || value === undefined ? "" : value)
			.replace(/&/g, "&amp;")
			.replace(/</g, "&lt;")
			.replace(/>/g, "&gt;")
			.replace(/"/g, "&quot;");
	}

	let body;
	try {
		body = $(frappe.render_template("mobile_onboarding", {})).appendTo(page.body);
	} catch (error) {
		// The template ships beside this file and is compiled into the page's own
		// script by Frappe. If it is not there the page cannot render at all, and
		// saying so beats an empty white panel and a console nobody opens.
		$(page.body).html(
			`<div class="text-muted" style="padding:20px">${__(
				"This page's template did not load. Run bench build and reload."
			)}</div>`
		);
		// eslint-disable-next-line no-console
		console.error(error);
		return null;
	}

	const $blockers = body.find(".mo-blockers");
	const $role = body.find(".mo-role");
	const $role_note = body.find(".mo-role-note");
	const $entities = body.find(".mo-entities");
	const $form = body.find(".mo-form");
	const $submit = body.find(".mo-submit");
	const $card_panel = body.find(".mo-card-panel");
	const $card_result = body.find(".mo-card-result");
	const $card_preview = body.find(".mo-card-preview");
	const $roster = body.find(".mo-roster-body");
	const $roster_summary = body.find(".mo-roster-summary");
	const $filter_company = body.find(".mo-filter-company");
	const $filter_revoked = body.find(".mo-filter-revoked");

	function alert_html(kind, paragraphs) {
		const inner = paragraphs.map((line) => `<p>${line}</p>`).join("");
		return `<div class="mo-alert mo-alert-${kind}">${inner}</div>`;
	}

	function render_readiness() {
		const parts = [];
		if (!context.may_enrol) {
			parts.push(
				alert_html("bad", [
					`<b>${__("You cannot enrol anybody from here.")}</b>`,
					esc(context.permission_note),
				])
			);
		}
		(context.blockers || []).forEach((blocker) => {
			parts.push(
				alert_html("bad", [
					`<b>${esc(blocker.message)}</b>`,
					`<i>${esc(blocker.fix)}</i>`,
				])
			);
		});
		$blockers.html(parts.join(""));

		const ready = context.may_enrol && !(context.blockers || []).length;
		$submit.prop("disabled", !ready);
		$form.find("input, select").prop("disabled", !ready);
		$submit.attr(
			"title",
			ready ? "" : __("Fix what the banner above says before enrolling anybody.")
		);
	}

	function render_roles() {
		const options = (context.roles || []).map(
			(role) => `<option value="${esc(role.role)}">${esc(role.role)}</option>`
		);
		$role.html(options.join(""));
		show_role_note();
	}

	function show_role_note() {
		const chosen = (context.roles || []).find((role) => role.role === $role.val());
		if (!chosen) {
			$role_note.text("");
			return;
		}
		const lines = [esc(chosen.summary || chosen.description)];
		if (!chosen.installed) {
			lines.push(
				`<b>${__("This role is not installed on this site yet.")}</b> ` +
					__("Run bench migrate; enrolling on it now would assign nothing.")
			);
		}
		if ((chosen.cannot || []).length) {
			lines.push(`<b>${__("Cannot")}:</b> ${esc(chosen.cannot.join("; "))}`);
		}
		if (chosen.desk_access) {
			lines.push(__("Holds Desk access as well as the app."));
		}
		$role_note.html(lines.join("<br>"));
	}

	function render_entities() {
		const companies = context.companies || [];
		if (!companies.length) {
			$entities.html(
				`<div class="mo-note">${__(
					"No company on this site is readable from this account."
				)}</div>`
			);
			return;
		}
		// PRE-TICKED ONLY WHEN THERE IS EXACTLY ONE, which is the single-entity
		// farm. On a site with several, the scoping is the decision this whole
		// form exists to record and a default would be somebody's guess.
		const only = companies.length === 1;
		$entities.html(
			companies
				.map(
					(name) =>
						`<label><input type="checkbox" class="mo-entity" value="${esc(name)}"${
							only ? " checked" : ""
						}>${esc(name)}</label>`
				)
				.join("")
		);

		$filter_company.html(
			[`<option value="">${__("All entities")}</option>`]
				.concat(companies.map((name) => `<option value="${esc(name)}">${esc(name)}</option>`))
				.join("")
		);
	}

	function chosen_entities() {
		return $entities
			.find(".mo-entity:checked")
			.map(function () {
				return $(this).val();
			})
			.get();
	}

	function load_context() {
		return frappe
			.call({ method: METHOD + "onboarding_context" })
			.then((response) => {
				context = (response || {}).message;
				if (!context) {
					return;
				}
				body.find("#mo-hours").val(context.default_expiry_hours);
				body.find("#mo-hours").attr("max", context.max_expiry_hours);
				render_roles();
				render_entities();
				render_readiness();
			});
	}

	function show_card(payload, heading) {
		card = payload;
		$card_panel.show();

		const lines = [];
		if (heading) {
			lines.push(`<b>${esc(heading)}</b>`);
		}
		if (payload.qr_error) {
			$card_result.html(
				alert_html("bad", [
					`<b>${__("The account is ready, but no card could be drawn.")}</b>`,
					esc(payload.qr_error),
					__("Fix that, then use Regenerate QR on the row below."),
				])
			);
			$card_preview.hide();
			return;
		}
		if ((payload.companion_roles_missing || []).length) {
			lines.push(
				esc(
					__("This site has no {0} role, so it was not assigned.", [
						payload.companion_roles_missing.join(", "),
					])
				)
			);
		}
		if (payload.qr && payload.qr.token_rotated) {
			lines.push(
				`<b>${__("The previous credential has stopped working.")}</b> ` +
					__("Any phone already enrolled on this account must scan this card.")
			);
		}
		$card_result.html(lines.length ? alert_html("ok", lines) : "");

		body.find(".mo-card-img").attr("src", "data:image/png;base64," + payload.qr.png_base64);
		body
			.find(".mo-card-expiry")
			.text(__("Valid to enrol until {0}", [payload.qr.expires_at]));
		$card_preview.show();
	}

	function print_card() {
		if (!card || !card.card_html) {
			return;
		}
		// Opened inside the click handler: a browser blocks a window.open() that
		// did not come from a gesture, and the markup is already in hand so
		// there is no round trip to lose the gesture to.
		const tab = window.open("", "_blank");
		if (!tab) {
			frappe.msgprint(__("Allow pop-ups for this site to print the enrolment card."));
			return;
		}
		let url;
		try {
			url = URL.createObjectURL(new Blob([card.card_html], { type: "text/html;charset=utf-8" }));
		} catch (error) {
			tab.document.open();
			tab.document.write(card.card_html);
			tab.document.close();
			return;
		}
		tab.location.href = url;
		// Revoked when the tab closes and not on a timer: the print preview reads
		// the URL a second time, and revoking it early prints a blank page.
		const watch = setInterval(function () {
			if (!tab || tab.closed) {
				clearInterval(watch);
				URL.revokeObjectURL(url);
			}
		}, 5000);
	}

	function submit(event) {
		event.preventDefault();
		const entities = chosen_entities();
		if (!entities.length) {
			frappe.msgprint(
				__("Tick at least one entity. An account with none would see every entity on the site.")
			);
			return;
		}
		frappe
			.call({
				method: METHOD + "create_and_enrol",
				args: {
					full_name: body.find("#mo-full-name").val(),
					email: body.find("#mo-email").val(),
					role: $role.val(),
					companies: entities,
					expiry_hours: body.find("#mo-hours").val(),
					notes: body.find("#mo-notes").val(),
					update_existing: body.find("#mo-update-existing").is(":checked") ? 1 : 0,
				},
				freeze: true,
				freeze_message: __("Creating the account and drawing the card..."),
			})
			.then((response) => {
				const payload = (response || {}).message;
				if (!payload) {
					return;
				}
				show_card(payload, payload.summary);
				$form[0].reset();
				render_entities();
				refresh_roster();
			});
	}

	function regenerate(user) {
		frappe.confirm(
			__(
				"Issue a new card for {0}? This replaces the credential, so a phone already enrolled on this account stops working until it scans the new card.",
				[user]
			),
			function () {
				frappe
					.call({
						method: METHOD + "regenerate_qr",
						args: { user: user, rotate_token: 1 },
						freeze: true,
						freeze_message: __("Drawing a new card..."),
					})
					.then((response) => {
						const payload = (response || {}).message;
						if (payload) {
							show_card(payload, __("New card for {0}", [user]));
							refresh_roster();
						}
					});
			}
		);
	}

	function row_html(row) {
		const state = String(row.state || "");
		const pill =
			state === "Revoked" ? "mo-pill-revoked" : state === "Active" ? "mo-pill-active" : "";
		const concerns = (row.concerns || [])
			.map((line) => `<span class="mo-concern">&#9888; ${esc(line)}</span>`)
			.join("");
		const token = row.has_live_token ? __("live") : __("none");
		return `<tr>
			<td>
				<div>${esc(row.full_name || row.user)}</div>
				<div class="mo-note">${esc(row.user)}</div>
				${concerns}
			</td>
			<td>${esc(row.role)}</td>
			<td>
				<span class="mo-pill ${pill}">${esc(state)}</span>
				<div class="mo-note">${__("token")}: ${esc(token)}</div>
			</td>
			<td>${esc((row.entity_access || []).join(", "))}</td>
			<td>${esc(row.last_seen_on || "—")}</td>
			<td class="text-right">
				<button type="button" class="btn btn-xs btn-default mo-regen" data-user="${esc(
					row.user
				)}">${__("Regenerate QR")}</button>
			</td>
		</tr>`;
	}

	function render_roster(payload) {
		const rows = payload.users || [];
		$roster_summary.text(
			rows.length
				? __("{0} account(s), {1} needing attention", [
						payload.count,
						payload.needing_attention,
				  ])
				: ""
		);
		if (!rows.length) {
			$roster.html(
				`<div class="mo-note">${__("No mobile account matches this filter yet.")}</div>`
			);
			return;
		}
		$roster.html(
			`<table class="mo-roster-table">
				<thead><tr>
					<th>${__("Person")}</th>
					<th>${__("Role")}</th>
					<th>${__("Status")}</th>
					<th>${__("Entities")}</th>
					<th>${__("Last activity")}</th>
					<th></th>
				</tr></thead>
				<tbody>${rows.map(row_html).join("")}</tbody>
			</table>`
		);
		// Disabled rather than hidden when the caller may read the roster but not
		// enrol: the button is what the page is for, and hiding it would make a
		// permission look like a missing feature.
		if (context && !context.may_enrol) {
			$roster.find(".mo-regen").prop("disabled", true).attr("title", context.permission_note);
		}
	}

	function refresh_roster() {
		return frappe
			.call({
				method: METHOD + "mobile_users",
				args: {
					company: $filter_company.val() || "",
					include_revoked: $filter_revoked.is(":checked") ? 1 : 0,
				},
			})
			.then((response) => {
				const payload = (response || {}).message;
				if (payload) {
					render_roster(payload);
				}
			});
	}

	$form.on("submit", submit);
	$role.on("change", show_role_note);
	body.find(".mo-print").on("click", print_card);
	body.find(".mo-clear").on("click", function () {
		card = null;
		$card_panel.hide();
	});
	body.find(".mo-refresh").on("click", refresh_roster);
	$filter_company.on("change", refresh_roster);
	$filter_revoked.on("change", refresh_roster);
	$roster.on("click", ".mo-regen", function () {
		regenerate($(this).data("user"));
	});

	page.set_primary_action(__("Refresh"), refresh_roster, "refresh");

	load_context().then(refresh_roster);

	return { refresh_roster: refresh_roster };
}
