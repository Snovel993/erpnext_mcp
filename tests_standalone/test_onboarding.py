# SPDX-License-Identifier: MIT
"""The "Connect to Claude Desktop" panel: config shape, masking, URL choice.

Permission enforcement is in-bench (`erpnext_mcp/tests/test_onboarding.py`) —
`frappe.only_for` against a real role set is not something a double can prove.
What is worth proving here is the shape: an operator pastes this into a file and
gets no feedback beyond "no tools", so a wrong flag or a missing argument costs
them an afternoon.
"""

import json

from erpnext_mcp import onboarding, settings

from .fixtures import V2TestCase
from .harness import STORE, frappe

TOKEN = "t" * 48


class Masking(V2TestCase):
	def test_it_keeps_the_last_four(self):
		"""Enough to tell two tokens apart, not enough to use one."""
		masked = onboarding.mask("abcdefghijklmnop")
		self.assertTrue(masked.endswith("mnop"))
		self.assertNotIn("abcdefghijkl", masked)

	def test_a_short_token_reveals_nothing(self):
		self.assertEqual(onboarding.mask("abcd"), "••••")

	def test_an_empty_token_masks_to_nothing(self):
		self.assertEqual(onboarding.mask(""), "")
		self.assertEqual(onboarding.mask(None), "")

	def test_the_mask_is_a_fixed_width(self):
		"""So the length of the real token is not leaked by the length of the
		mask."""
		short = onboarding.mask("a" * 20)
		long = onboarding.mask("a" * 80)
		self.assertEqual(len(short), len(long))


class ConfigShape(V2TestCase):
	def test_it_is_the_shape_claude_desktop_expects(self):
		config = onboarding.build_config("https://erp.example.com/api/method/x", TOKEN)
		self.assertEqual(list(config), ["mcpServers"])
		self.assertEqual(list(config["mcpServers"]), ["erpnext"])
		self.assertEqual(config["mcpServers"]["erpnext"]["command"], "npx")

	def test_the_args_are_in_the_order_mcp_remote_wants(self):
		args = onboarding.build_config("https://erp.example.com/x", TOKEN)["mcpServers"]["erpnext"]["args"]
		self.assertEqual(args[:2], ["-y", "mcp-remote"])
		self.assertEqual(args[2], "https://erp.example.com/x")
		self.assertIn("--transport", args)
		self.assertEqual(args[args.index("--transport") + 1], "http-only")

	def test_the_header_is_one_argument(self):
		"""`X-MCP-Token: <token>` must stay a single argv entry — splitting it on
		the space is the classic way this config silently fails."""
		args = onboarding.build_config("https://erp.example.com/x", TOKEN)["mcpServers"]["erpnext"]["args"]
		self.assertEqual(args[args.index("--header") + 1], f"X-MCP-Token: {TOKEN}")

	def test_it_uses_x_mcp_token_not_authorization(self):
		"""Frappe's OAuth layer eats Authorization: Bearer, which is why the
		documented header is this one."""
		blob = json.dumps(onboarding.build_config("https://erp.example.com/x", TOKEN))
		self.assertIn("X-MCP-Token", blob)
		self.assertNotIn("Authorization", blob)

	def test_allow_http_is_added_for_a_plain_http_endpoint(self):
		"""mcp-remote refuses a non-HTTPS origin without it."""
		args = onboarding.build_config("http://10.0.0.5:8000/x", TOKEN)["mcpServers"]["erpnext"]["args"]
		self.assertIn("--allow-http", args)

	def test_allow_http_is_omitted_for_https(self):
		"""Noise that invites the question "why is this allowing http"."""
		args = onboarding.build_config("https://erp.example.com/x", TOKEN)["mcpServers"]["erpnext"]["args"]
		self.assertNotIn("--allow-http", args)

	def test_the_claude_code_command_quotes_the_header(self):
		command = onboarding.build_claude_code_command("https://erp.example.com/x", TOKEN)
		self.assertIn('--header "X-MCP-Token: ' + TOKEN + '"', command)
		self.assertIn("--transport http", command)


class EndpointUrl(V2TestCase):
	def test_it_defaults_to_the_sites_own_url(self):
		self.assertEqual(
			onboarding.endpoint_url(),
			"https://test.localhost/api/method/erpnext_mcp.mcp.handle",
		)

	def test_public_url_wins_when_set(self):
		"""A site behind a Tailscale Funnel has a get_url() that is correct for
		the server and useless to the client."""
		self.configure(enabled=1, public_url="https://erp.tail1234.ts.net")
		self.assertEqual(
			onboarding.endpoint_url(),
			"https://erp.tail1234.ts.net/api/method/erpnext_mcp.mcp.handle",
		)

	def test_a_trailing_slash_does_not_double_up(self):
		self.configure(enabled=1, public_url="https://erp.example.com/")
		self.assertNotIn("//api", onboarding.endpoint_url().replace("https://", ""))

	def test_whitespace_in_the_field_is_ignored(self):
		self.configure(enabled=1, public_url="  https://erp.example.com  ")
		self.assertTrue(onboarding.endpoint_url().startswith("https://erp.example.com/api"))

	def test_settings_exposes_it(self):
		self.configure(enabled=1, public_url="https://erp.example.com")
		self.assertEqual(settings.public_url(), "https://erp.example.com")

	def test_an_unset_public_url_is_empty_not_none(self):
		self.assertEqual(settings.public_url(), "")


class OsDetection(V2TestCase):
	def test_it_reads_the_user_agent(self):
		self.assertEqual(onboarding.detect_os("Mozilla/5.0 (Macintosh; Intel Mac OS X)"), "macos")
		self.assertEqual(onboarding.detect_os("Mozilla/5.0 (Windows NT 10.0; Win64)"), "windows")
		self.assertEqual(onboarding.detect_os("Mozilla/5.0 (X11; Linux x86_64)"), "linux")

	def test_an_unknown_agent_falls_back_rather_than_failing(self):
		"""It only decides which row is highlighted; all three paths are always
		rendered because the browser is not necessarily the machine running
		Claude Desktop."""
		self.assertEqual(onboarding.detect_os(""), "macos")
		self.assertEqual(onboarding.detect_os("curl/8.4.0"), "macos")

	def test_every_platform_has_a_path_and_a_quit_key(self):
		for key in onboarding.CONFIG_PATHS:
			with self.subTest(os=key):
				self.assertTrue(onboarding.CONFIG_PATHS[key])
				self.assertIn(key, onboarding.OS_LABELS)
				self.assertIn(key, onboarding.QUIT_KEYS)


class Payload(V2TestCase):
	def request_as(self, user="Administrator", user_agent=""):
		self.request({}, headers={"User-Agent": user_agent} if user_agent else {})
		frappe.local.session.user = user

	def test_masked_by_default(self):
		self.request_as()
		payload = onboarding.claude_desktop_config()
		self.assertFalse(payload["revealed"])
		self.assertNotIn(self.TOKEN, payload["config_json"])
		self.assertNotIn(self.TOKEN, payload["claude_code_command"])

	def test_reveal_returns_the_real_token(self):
		self.request_as()
		payload = onboarding.claude_desktop_config(reveal=1)
		self.assertTrue(payload["revealed"])
		self.assertIn(self.TOKEN, payload["config_json"])

	def test_reveal_accepts_the_string_a_form_will_send(self):
		self.request_as()
		self.assertTrue(onboarding.claude_desktop_config(reveal="1")["revealed"])

	def test_it_reports_readiness(self):
		self.request_as()
		self.assertTrue(onboarding.claude_desktop_config()["ready"])

	def test_no_token_is_not_ready_and_reveals_nothing(self):
		self.set_token("")
		self.request_as()
		payload = onboarding.claude_desktop_config(reveal=1)
		self.assertFalse(payload["ready"])
		self.assertFalse(payload["token_configured"])
		self.assertFalse(payload["revealed"])

	def test_it_carries_the_os_paths_for_the_panel(self):
		self.request_as(user_agent="Mozilla/5.0 (Windows NT 10.0)")
		payload = onboarding.claude_desktop_config()
		self.assertEqual(payload["detected_os"], "windows")
		self.assertIn("%APPDATA%", payload["config_paths"]["windows"])

	def test_it_says_where_the_url_came_from(self):
		self.request_as()
		self.assertEqual(onboarding.claude_desktop_config()["url_source"], "frappe.utils.get_url()")
		self.configure(enabled=1, public_url="https://erp.example.com")
		self.assertEqual(onboarding.claude_desktop_config()["url_source"], "public_url")

	def test_the_download_url_is_the_documented_path(self):
		self.request_as()
		self.assertEqual(
			onboarding.claude_desktop_config()["download_url"],
			"/api/method/erpnext_mcp.onboarding.download_claude_desktop_config",
		)

	def test_a_non_system_manager_is_refused(self):
		"""Belt for the in-bench test, which is where the real role set lives."""
		self.request_as(user="mcp@example.test")
		with self.assertRaises(Exception) as caught:
			onboarding.claude_desktop_config()
		self.assertIn("System Manager", str(caught.exception))


class Download(V2TestCase):
	def test_it_serves_a_json_attachment(self):
		frappe.local.session.user = "Administrator"
		frappe.response.clear()
		onboarding.download_claude_desktop_config()
		self.assertEqual(frappe.response["type"], "binary")
		self.assertEqual(frappe.response["filename"], "claude_desktop_config.json")

	def test_the_file_contains_a_working_config(self):
		frappe.local.session.user = "Administrator"
		frappe.response.clear()
		onboarding.download_claude_desktop_config()
		config = json.loads(frappe.response["filecontent"].decode())
		args = config["mcpServers"]["erpnext"]["args"]
		self.assertEqual(args[args.index("--header") + 1], f"X-MCP-Token: {self.TOKEN}")

	def test_the_download_is_never_masked(self):
		"""A masked config file is a file that does not work."""
		frappe.local.session.user = "Administrator"
		frappe.response.clear()
		onboarding.download_claude_desktop_config()
		self.assertNotIn("•", frappe.response["filecontent"].decode())

	def test_it_refuses_without_a_token(self):
		self.set_token("")
		frappe.local.session.user = "Administrator"
		with self.assertRaises(Exception) as caught:
			onboarding.download_claude_desktop_config()
		self.assertIn("Generate an auth token", str(caught.exception))

	def test_a_non_system_manager_is_refused(self):
		frappe.local.session.user = "mcp@example.test"
		with self.assertRaises(Exception) as caught:
			onboarding.download_claude_desktop_config()
		self.assertIn("System Manager", str(caught.exception))


class TheTokenDoesNotLeakElsewhere(V2TestCase):
	def test_selftest_still_only_reports_whether_a_token_exists(self):
		from erpnext_mcp import mcp

		frappe.local.session.user = "Administrator"
		self.assertNotIn(self.TOKEN, json.dumps(mcp.selftest()))

	def test_the_connection_panel_is_not_an_mcp_tool(self):
		"""It is Desk UI for an operator, not surface for a client — an MCP caller
		holding the token has no business asking the site to hand it back."""
		from erpnext_mcp import registry

		self.assertNotIn("claude_desktop_config", registry.TOOLS)
		self.assertNotIn("download_claude_desktop_config", registry.TOOLS)

	def test_no_audit_row_is_written_for_a_desk_call(self):
		"""MCP Action Log records MCP calls. A Desk form fetching its own panel is
		not one, and logging it would bury the log in UI chatter."""
		self.request({}, headers={})
		frappe.local.session.user = "Administrator"
		before = len(STORE.rows("MCP Action Log"))
		onboarding.claude_desktop_config()
		self.assertEqual(len(STORE.rows("MCP Action Log")), before)
