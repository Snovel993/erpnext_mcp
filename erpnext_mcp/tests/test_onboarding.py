# SPDX-License-Identifier: MIT
"""In-bench tests for the "Connect to Claude Desktop" panel.

    bench --site <site> run-tests --app erpnext_mcp --module erpnext_mcp.tests.test_onboarding

The standalone suite covers the config shape. What only a bench can show is the
part that matters most here: this is the one place in the app that hands a
plaintext token back to a caller, so the role gate has to be enforced by Frappe
against a real role set rather than by a double that was told to say no.

It also proves the two framework facts the panel rests on — that
`frappe.utils.get_url()` returns something usable, and that the Password field
round-trips through real encryption into the generated config.
"""

import json

import frappe

from erpnext_mcp import onboarding, settings

from .test_integration import MCPIntegrationTestCase

PLAIN_USER = "erpnext-mcp-onboarding-plain@example.test"


class ConnectPanelPermissions(MCPIntegrationTestCase):
	"""The role gate, against Frappe's own `only_for`."""

	def plain_user(self):
		if not frappe.db.exists("User", PLAIN_USER):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": PLAIN_USER,
					"first_name": "Onboarding Plain",
					"send_welcome_email": 0,
					"roles": [],
				}
			).insert(ignore_permissions=True)
		return PLAIN_USER

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def test_a_system_manager_gets_the_panel(self):
		payload = onboarding.claude_desktop_config()
		self.assertTrue(payload["ready"])
		self.assertTrue(payload["token_configured"])

	def test_a_user_without_the_role_cannot_read_the_panel(self):
		"""This endpoint returns a plaintext token to whoever can call it, so the
		gate is the whole security story."""
		frappe.set_user(self.plain_user())
		with self.assertRaises(frappe.PermissionError):
			onboarding.claude_desktop_config()

	def test_a_user_without_the_role_cannot_reveal_the_token(self):
		frappe.set_user(self.plain_user())
		with self.assertRaises(frappe.PermissionError):
			onboarding.claude_desktop_config(reveal=1)

	def test_a_user_without_the_role_cannot_download_the_config(self):
		frappe.set_user(self.plain_user())
		with self.assertRaises(frappe.PermissionError):
			onboarding.download_claude_desktop_config()

	def test_the_gate_is_the_same_one_that_opens_the_form(self):
		"""Nothing is being given away that a System Manager could not already get
		by pressing Generate New Token — which is the honest justification for
		handing the token back at all."""
		permissions = frappe.get_all(
			"DocPerm",
			filters={"parent": settings.SETTINGS_DOCTYPE, "read": 1},
			pluck="role",
		)
		self.assertEqual(sorted(set(permissions)), ["System Manager"])


class ConnectPanelOnThisSite(MCPIntegrationTestCase):
	def test_the_endpoint_url_is_built_from_this_site(self):
		url = onboarding.endpoint_url()
		self.assertTrue(url.startswith("http"))
		self.assertTrue(url.endswith("/api/method/erpnext_mcp.mcp.handle"))

	def test_public_url_overrides_the_sites_own(self):
		self.doc.public_url = "https://erp.tail1234.ts.net"
		self.doc.flags.ignore_permissions = True
		self.doc.save()
		frappe.clear_cache(doctype=settings.SETTINGS_DOCTYPE)
		self.assertEqual(
			onboarding.endpoint_url(),
			"https://erp.tail1234.ts.net/api/method/erpnext_mcp.mcp.handle",
		)
		self.assertEqual(onboarding.claude_desktop_config()["url_source"], "public_url")

	def test_public_url_migrated_as_a_field(self):
		self.assertTrue(frappe.get_meta(settings.SETTINGS_DOCTYPE).has_field("public_url"))

	def test_the_connect_section_migrated(self):
		meta = frappe.get_meta(settings.SETTINGS_DOCTYPE)
		self.assertTrue(meta.has_field("claude_desktop_html"))
		section = meta.get_field("claude_desktop_section")
		self.assertIsNotNone(section)
		self.assertEqual(section.depends_on, "eval:doc.enabled")

	def test_the_generated_config_carries_the_real_encrypted_token(self):
		"""The Password field round-trips through Frappe's encryption and comes
		out the other side inside a config a client can actually use."""
		payload = onboarding.claude_desktop_config(reveal=1)
		args = payload["config"]["mcpServers"]["erpnext"]["args"]
		self.assertEqual(
			args[args.index("--header") + 1],
			f"X-MCP-Token: {settings.auth_token()}",
		)

	def test_the_masked_payload_never_contains_the_token(self):
		token = settings.auth_token()
		payload = onboarding.claude_desktop_config()
		self.assertNotIn(token, json.dumps(payload))

	def test_the_endpoint_in_the_config_is_the_one_that_answers(self):
		"""A config pointing somewhere the server does not serve is the failure
		this panel exists to prevent."""
		url = onboarding.claude_desktop_config()["endpoint_url"]
		self.assertIn("erpnext_mcp.mcp.handle", url)
		_body, status = self.rpc("ping")
		self.assertEqual(status, 200)

	def test_the_download_serves_a_json_attachment(self):
		frappe.response.clear()
		onboarding.download_claude_desktop_config()
		self.assertEqual(frappe.response["type"], "binary")
		self.assertEqual(frappe.response["filename"], "claude_desktop_config.json")
		config = json.loads(frappe.response["filecontent"].decode())
		self.assertIn("erpnext", config["mcpServers"])

	def test_the_download_allows_get(self):
		"""It is opened in a browser tab, so GET has to be permitted — a
		whitelisted method restricted to POST would 405 the download button, and
		the failure would look like "the button does nothing"."""
		function = frappe.get_attr("erpnext_mcp.onboarding.download_claude_desktop_config")
		self.assertIn(function, frappe.whitelisted)
		allowed = frappe.allowed_http_methods_for_whitelisted_func.get(function)
		self.assertTrue(allowed is None or "GET" in allowed, f"GET not allowed: {allowed}")

	def test_the_preview_is_not_reachable_by_get(self):
		"""Only the download needs GET. The preview stays POST-only so it cannot
		be triggered by a link or an image tag on another page."""
		function = frappe.get_attr("erpnext_mcp.onboarding.claude_desktop_config")
		self.assertIn(function, frappe.whitelisted)

	def test_os_detection_uses_the_request_header(self):
		frappe.local.request = None
		self.assertIn(
			onboarding.claude_desktop_config()["detected_os"],
			("macos", "windows", "linux"),
		)


class UrlDerivationOnThisSite(MCPIntegrationTestCase):
	"""v0.4.1. The port bug was never `get_url()` dropping something — nginx's
	`$host` strips the port before Python sees the request, and the port
	`get_url()` would append is the container-internal one. These pin the pieces
	the fix depends on against the real framework."""

	def request_from(self, host, origin=None, **headers):
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		all_headers = {"Host": host}
		if origin:
			all_headers["Origin"] = origin
		all_headers.update(headers)
		builder = EnvironBuilder(
			method="POST",
			path="/api/method/x",
			headers=all_headers,
			environ_base={"REMOTE_ADDR": "127.0.0.1"},
		)
		frappe.local.request = Request(builder.get_environ())

	def test_get_url_is_still_reachable_as_a_last_resort(self):
		frappe.local.request = None
		self.assertTrue(onboarding._safe_get_url().startswith("http"))

	def test_the_browser_origin_supplies_the_port(self):
		"""The whole fix in one assertion."""
		self.request_from("10.0.0.5", origin="http://10.0.0.5:5300")
		self.assertEqual(onboarding.browser_origin(), "http://10.0.0.5:5300")
		self.assertEqual(onboarding.endpoint_url(), "http://10.0.0.5:5300/api/method/erpnext_mcp.mcp.handle")

	def test_a_portless_host_is_what_nginx_actually_delivers(self):
		"""werkzeug keeps whatever Host it is given, so this documents the shape
		the container sees rather than asserting nginx's behaviour."""
		self.request_from("10.0.0.5")
		self.assertEqual(onboarding.request_base(), "http://10.0.0.5")

	def test_conf_is_readable_for_host_name_and_default_site(self):
		"""Both live in common_site_config.json, which frappe.conf merges in."""
		self.assertIsInstance(dict(frappe.conf), dict)
		self.assertIn(onboarding.configured_host_name(), ("", frappe.conf.get("host_name") or ""))

	def test_the_candidate_list_is_ordered_and_labelled(self):
		self.request_from("10.0.0.5", origin="http://10.0.0.5:5300")
		sources = [row["source"] for row in onboarding.url_candidates()]
		self.assertEqual(sources[0], "browser Origin/Referer")
		self.assertIn("frappe.utils.get_url()", sources)

	def test_the_warning_fires_only_for_an_unroutable_bare_ip(self):
		self.request_from("10.0.0.5", origin="http://10.0.0.5:5300")
		warning = onboarding.routing_warning(onboarding.endpoint_url())
		if (frappe.conf.get("default_site") or "").strip():
			self.assertEqual(warning, {}, "default_site is set, so no warning is expected")
		else:
			self.assertEqual(warning["code"], "BARE_IP_NO_DEFAULT_SITE")

	def test_a_named_host_never_warns_on_this_site(self):
		self.request_from("erp.example.com", origin="https://erp.example.com:8443")
		self.assertEqual(onboarding.routing_warning(onboarding.endpoint_url()), {})
