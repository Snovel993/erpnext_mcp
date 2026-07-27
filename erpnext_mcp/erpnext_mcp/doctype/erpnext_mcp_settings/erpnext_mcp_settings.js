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
		render_connect_panel(frm);

		frm.set_df_property(
			"generate_token",
			"description",
			frm.doc.token_generated_on
				? __("Generating a new token immediately invalidates the current one.")
				: __("No token yet. The endpoint stays off until one exists.")
		);
	},

	enabled(frm) {
		render_connect_panel(frm);
	},

	public_url(frm) {
		render_connect_panel(frm);
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

// ── Connect to Claude Desktop ───────────────────────────────────────────────
//
// The panel renders masked. Copy and Download re-fetch with reveal=1, so the
// clipboard and the file get a working config while the screen stays safe to
// share — the operator never has to choose between "usable" and "not on my
// screenshot".

const CONNECT_FIELD = "claude_desktop_html";

function render_connect_panel(frm, revealed_payload) {
	const wrapper = frm.get_field(CONNECT_FIELD) && frm.get_field(CONNECT_FIELD).$wrapper;
	if (!wrapper) return;
	if (!frm.doc.enabled) {
		wrapper.empty();
		return;
	}

	const draw = (d) => {
		wrapper.empty().append(connect_panel_html(d || {}));
		wire_connect_panel(frm, wrapper, d || {});
	};

	if (revealed_payload) {
		draw(revealed_payload);
		return;
	}
	frappe
		.call({ method: "erpnext_mcp.onboarding.claude_desktop_config", args: { reveal: 0 } })
		.then((r) => draw(r.message));
}

function connect_panel_html(d) {
	if (!d.token_configured) {
		return `<div class="form-message orange">${__(
			"Generate an auth token above, then this panel will show the client configuration."
		)}</div>`;
	}

	const esc = frappe.utils.escape_html;
	const os_rows = Object.keys(d.config_paths || {})
		.map((key) => {
			const active = key === d.detected_os;
			return `<tr class="${active ? "" : "text-muted"}">
				<td style="padding:2px 12px 2px 0;white-space:nowrap">
					${active ? "<b>" : ""}${esc(d.os_labels[key])}${active ? "</b> ←" : ""}
				</td>
				<td><code>${esc(d.config_paths[key])}</code></td>
			</tr>`;
		})
		.join("");

	// A bare-IP URL reaches Frappe's site router and matches no site directory,
	// so a client can get "site not found" while this very browser works. That
	// asymmetry is baffling without being told, so say it here.
	const routing_warning = d.routing_warning && d.routing_warning.code
		? `<div class="form-message red" style="margin-bottom:12px">
			<b>${__("This URL uses a bare IP address")}</b><br>${esc(d.routing_warning.message)}
		   </div>`
		: "";

	const http_note = d.is_http
		? `<p class="text-muted small">${__(
				"This endpoint is plain HTTP, so the config includes <code>--allow-http</code>. mcp-remote refuses a non-HTTPS origin without it."
		  )}</p>`
		: "";

	return `
<div class="erpnext-mcp-connect">
	${routing_warning}
	<p><b>${__("1. Save this to your Claude Desktop config")}</b><br>
	<span class="text-muted small">${__("Default location — highlighted row is the platform this browser reports.")}</span></p>
	<table style="margin-bottom:12px">${os_rows}</table>

	<pre data-connect="json" style="max-height:260px;overflow:auto;padding:10px;border-radius:4px">${esc(
		d.config_json
	)}</pre>
	${http_note}
	<p>
		<button class="btn btn-xs btn-primary" data-connect-action="copy-json">${__("Copy config JSON")}</button>
		<button class="btn btn-xs btn-default" data-connect-action="download">${__("Download config file")}</button>
		<button class="btn btn-xs btn-default" data-connect-action="reveal">${
			d.revealed ? __("Hide token") : __("Reveal for copy")
		}</button>
		<span class="text-muted small" style="margin-left:8px">${
			d.revealed
				? __("Token visible — do not screenshot.")
				: __("Preview is masked. Copy and Download still give you the real token.")
		}</span>
	</p>

	<p style="margin-top:16px"><b>${__("2. Restart Claude Desktop")}</b><br>
	<span class="text-muted small">${__("Fully quit ({0}) and reopen — reloading the window is not enough.", [
		esc(d.quit_keys[d.detected_os] || "⌘Q"),
	])}</span></p>

	<p><b>${__("3. Check it worked")}</b><br>
	<span class="text-muted small">${__(
		'Ask Claude "get the company topology from erpnext". The <code>erpnext__*</code> tools should be available.'
	)}</span></p>

	<p class="text-muted small">${__(
		"If the file already exists, merge the <code>erpnext</code> key into your existing <code>mcpServers</code> object rather than replacing the file."
	)}</p>

	<hr>
	<p><b>${__("Connect from Claude Code")}</b><br>
	<span class="text-muted small">${__("No bridge needed — Claude Code speaks HTTP MCP directly.")}</span></p>
	<pre data-connect="cli" style="padding:10px;border-radius:4px;white-space:pre-wrap">${esc(
		d.claude_code_command
	)}</pre>
	<p><button class="btn btn-xs btn-default" data-connect-action="copy-cli">${__("Copy command")}</button></p>

	<p class="text-muted small" style="margin-top:12px">${__("Endpoint")}:
		<code>${esc(d.endpoint_url)}</code> <span>${__("from")} ${esc(d.url_source)}</span>
		${
			(d.url_candidates || []).length > 1
				? `<br><span title="${esc(
						(d.url_candidates || [])
							.map((c) => `${c.source}: ${c.base}`)
							.join("\n")
				  )}">${__("Other addresses were available — hover to see what was considered.")}</span>`
				: ""
		}
		<br>${__(
			"Wrong address? Set Public URL above — that always wins."
		)}
	</p>
</div>`;
}

function wire_connect_panel(frm, wrapper, current) {
	wrapper.find("[data-connect-action]").on("click", function () {
		const action = $(this).attr("data-connect-action");

		if (action === "download") {
			// A GET the browser can open. The token rides in the response body,
			// never in the URL, so it stays out of proxy logs and history.
			window.open(current.download_url, "_blank");
			return;
		}

		if (action === "reveal") {
			if (current.revealed) {
				render_connect_panel(frm);
				return;
			}
			with_revealed_config((payload) => render_connect_panel(frm, payload));
			return;
		}

		// Copy always fetches the real token, whatever the preview is showing.
		with_revealed_config((payload) => {
			const text = action === "copy-cli" ? payload.claude_code_command : payload.config_json;
			copy_text(text);
		});
	});
}

function with_revealed_config(callback) {
	frappe
		.call({ method: "erpnext_mcp.onboarding.claude_desktop_config", args: { reveal: 1 } })
		.then((r) => r.message && callback(r.message));
}

function copy_text(text) {
	if (frappe.utils && frappe.utils.copy_to_clipboard) {
		frappe.utils.copy_to_clipboard(text);
		frappe.show_alert({ message: __("Copied — includes your real token"), indicator: "green" });
		return;
	}
	// Non-secure contexts (a LAN site on plain http) have no navigator.clipboard.
	const area = document.createElement("textarea");
	area.value = text;
	area.style.position = "fixed";
	area.style.opacity = "0";
	document.body.appendChild(area);
	area.select();
	try {
		document.execCommand("copy");
		frappe.show_alert({ message: __("Copied — includes your real token"), indicator: "green" });
	} catch (e) {
		frappe.msgprint(__("Could not copy automatically. Select the text above and copy it."));
	}
	document.body.removeChild(area);
}
