// SPDX-License-Identifier: MIT
//
// The token is shown once, in a dialog the operator has to dismiss, and never
// written into the form's own field display. Anything else — a msgprint that
// scrolls away, a value left in an input — ends up in a screenshot or a browser
// session someone else uses.

frappe.ui.form.on("ERPNext MCP Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Test Configuration"), () => show_selftest(frm));
		set_headline(frm);

		frm.set_df_property(
			"generate_token",
			"description",
			frm.doc.token_generated_on
				? __("Generating a new token immediately invalidates the current one.")
				: __("No token yet. The endpoint stays off until one exists.")
		);
	},

	generate_token(frm) {
		frappe.confirm(
			frm.doc.token_generated_on
				? __(
						"Generate a new token? Any MCP client using the current token will stop working immediately."
				  )
				: __("Generate an auth token for MCP clients?"),
			() => {
				frm.call({
					doc: frm.doc,
					method: "generate_token",
					freeze: true,
					freeze_message: __("Generating token..."),
				}).then((r) => {
					if (!r || !r.message) return;
					show_token_once(r.message);
					frm.reload_doc();
				});
			}
		);
	},
});

// The list of write tools comes from the server, not from a copy kept here.
// Hardcoding it is how a form ends up telling an operator "read-only" while a
// tool added in a later version is quietly live. Cached per page load.
let MUTATING_TOOLS = null;

function with_mutating_tools(callback) {
	if (MUTATING_TOOLS) {
		callback(MUTATING_TOOLS);
		return;
	}
	frappe.call({ method: "erpnext_mcp.mcp.mutating_tool_names" }).then((r) => {
		MUTATING_TOOLS = r.message || [];
		callback(MUTATING_TOOLS);
	});
}

function set_headline(frm) {
	if (!frm.doc.enabled) {
		frm.dashboard.set_headline_alert(
			__("The MCP endpoint is off. It answers 404 to every caller."),
			"orange"
		);
		return;
	}
	with_mutating_tools((names) => {
		const live = names.filter((name) => frm.doc[`allow_${name}`]);
		if (live.length) {
			frm.dashboard.set_headline_alert(
				__("MCP is live with {0} write tool(s) enabled: {1}", [
					live.length,
					live.join(", "),
				]),
				"red"
			);
		} else {
			frm.dashboard.set_headline_alert(
				__("MCP is live, read-only. No write tool is enabled."),
				"green"
			);
		}
	});
}

function show_token_once(payload) {
	const dialog = new frappe.ui.Dialog({
		title: __("Copy This Token Now"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				options: `<div class="form-message red">${__(
					"This is the only time this token is shown. It is stored encrypted and cannot be retrieved."
				)}</div>`,
			},
			{
				fieldname: "token",
				fieldtype: "Code",
				label: __("Bearer Token"),
				default: payload.token,
				read_only: 1,
			},
			{
				fieldtype: "HTML",
				options: `<p>${__("Use it as")} <code>Authorization: Bearer &lt;token&gt;</code> ${__(
					"against"
				)} <code>POST ${frappe.utils.escape_html(payload.endpoint)}</code></p>`,
			},
		],
		primary_action_label: __("Copy to Clipboard"),
		primary_action() {
			frappe.utils.copy_to_clipboard(payload.token);
			frappe.show_alert({ message: __("Token copied"), indicator: "green" });
		},
	});
	dialog.show();
}

function show_selftest(frm) {
	frappe.call({ method: "erpnext_mcp.mcp.selftest", freeze: true }).then((r) => {
		const d = r.message || {};
		const row = (label, value) =>
			`<tr><td class="text-muted" style="padding-right:1rem">${label}</td><td><b>${value}</b></td></tr>`;
		const yes_no = (v) =>
			v
				? `<span class="text-success">${__("yes")}</span>`
				: `<span class="text-danger">${__("no")}</span>`;
		frappe.msgprint({
			title: d.ready ? __("MCP Is Ready") : __("MCP Is Not Ready"),
			indicator: d.ready ? "green" : "orange",
			message: `<table>
				${row(__("Enabled"), yes_no(d.enabled))}
				${row(__("Token configured"), yes_no(d.token_configured))}
				${row(__("Allowed CIDRs"), (d.allowed_cidrs || []).join(", ") || "—")}
				${row(__("Runs as"), frappe.utils.escape_html(d.effective_user || "—"))}
				${row(__("Endpoint"), `<code>POST ${frappe.utils.escape_html(d.endpoint || "")}</code>`)}
				${row(__("Protocol versions"), (d.protocol_versions || []).join(", "))}
				${row(
					__("Tools enabled"),
					`${(d.tools_enabled || []).length} / ${d.tools_total || 0}`
				)}
				${row(
					__("Write tools enabled"),
					(d.mutating_tools_enabled || []).join(", ") ||
						`<span class="text-success">${__("none")}</span>`
				)}
				${row(
					__("Unavailable on this site"),
					Object.keys(d.tools_unavailable || {})
						.map(
							(name) =>
								`${frappe.utils.escape_html(name)} <span class="text-muted">(${frappe.utils.escape_html(
									d.tools_unavailable[name] || "prerequisite missing"
								)})</span>`
						)
						.join("<br>") || `<span class="text-muted">${__("none")}</span>`
				)}
			</table>`,
		});
	});
}
