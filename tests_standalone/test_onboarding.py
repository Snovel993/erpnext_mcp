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
		"""`request Host` outranks `get_url()` as of v0.4.1 — get_url is the last
		resort, not the first choice."""
		self.request_as()
		self.assertEqual(onboarding.claude_desktop_config()["url_source"], "request Host")
		self.configure(enabled=1, public_url="https://erp.example.com")
		self.assertEqual(onboarding.claude_desktop_config()["url_source"], "public_url")

	def test_it_shows_its_working(self):
		"""An operator staring at a URL they did not expect can see what else was
		available and why this one won."""
		self.request_as()
		sources = [row["source"] for row in onboarding.claude_desktop_config()["url_candidates"]]
		self.assertIn("request Host", sources)
		self.assertIn("frappe.utils.get_url()", sources)

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


class PortPreservation(V2TestCase):
	"""v0.4.1 bug 1: the widget emitted `http://100.69.162.122/api/...`, no port.

	The port was never dropped — it never arrived. frappe_docker's nginx proxies
	with `proxy_set_header Host $host`, and nginx's `$host` is normalised: no
	port. By the time Python sees the request there is nothing to preserve, and
	the port `get_url()` would append is the container-internal one, not the
	published one. The only component that knows the real external port is the
	browser, and it tells us in `Origin`.
	"""

	def browsing_from(self, origin, host="100.69.162.122", **headers):
		"""A request as nginx delivers it: portless Host, browser Origin intact."""
		all_headers = {"Origin": origin} if origin else {}
		all_headers.update(headers)
		self.request({}, headers=all_headers, method="POST")
		frappe.local.request.host = host
		frappe.local.request.scheme = "http"
		frappe.local.session.user = "Administrator"

	def test_the_v0_4_0_regression(self):
		"""Portless Host from nginx, browser on :5300 — the port must survive."""
		self.browsing_from("http://100.69.162.122:5300")
		self.assertEqual(
			onboarding.endpoint_url(),
			"http://100.69.162.122:5300/api/method/erpnext_mcp.mcp.handle",
		)

	def test_without_the_fix_the_host_alone_would_have_no_port(self):
		"""Pins the thing that was wrong: request Host really is portless here."""
		self.browsing_from("http://100.69.162.122:5300")
		self.assertEqual(onboarding.request_base(), "http://100.69.162.122")

	def test_referer_carries_the_port_when_origin_does_not(self):
		"""The download is a GET from a link: no Origin, but a Referer."""
		self.browsing_from(None, Referer="http://100.69.162.122:5300/app/erpnext-mcp-settings")
		self.assertEqual(
			onboarding.endpoint_url(),
			"http://100.69.162.122:5300/api/method/erpnext_mcp.mcp.handle",
		)

	def test_a_default_port_is_not_invented(self):
		"""Browsing on :80 gives an origin with no port, and none is added."""
		self.browsing_from("http://umbrel.local")
		self.assertEqual(onboarding.endpoint_url(), "http://umbrel.local/api/method/erpnext_mcp.mcp.handle")

	def test_forwarded_headers_are_used_when_a_proxy_sets_them_properly(self):
		self.request(
			{},
			headers={
				"X-Forwarded-Host": "erp.example.com",
				"X-Forwarded-Port": "8443",
				"X-Forwarded-Proto": "https",
			},
		)
		frappe.local.session.user = "Administrator"
		self.assertEqual(onboarding.forwarded_base(), "https://erp.example.com:8443")

	def test_a_forwarded_host_that_already_has_a_port_is_left_alone(self):
		self.request(
			{},
			headers={"X-Forwarded-Host": "erp.example.com:9000", "X-Forwarded-Port": "8443"},
		)
		self.assertEqual(onboarding.forwarded_base(), "http://erp.example.com:9000")

	def test_the_scheme_follows_the_browser(self):
		self.browsing_from("https://erp.example.com:8443")
		self.assertTrue(onboarding.endpoint_url().startswith("https://"))


class HostNamePreference(V2TestCase):
	"""v0.4.1 bug 2: a bare-IP Host may not route to any site."""

	def configured(self, host_name, origin=None):
		frappe.conf["host_name"] = host_name
		headers = {"Origin": origin} if origin else {}
		self.request({}, headers=headers)
		frappe.local.request.host = "100.69.162.122"
		frappe.local.session.user = "Administrator"

	def test_host_name_beats_the_request_host(self):
		"""It is the name Frappe itself prefers, and on a multi-site bench it is
		the one that actually routes."""
		self.configured("http://umbrel.local")
		self.assertEqual(onboarding.endpoint_url(), "http://umbrel.local/api/method/erpnext_mcp.mcp.handle")
		self.assertEqual(onboarding.claude_desktop_config()["url_source"], "host_name (site config)")

	def test_a_bare_host_name_gains_a_scheme(self):
		self.configured("umbrel.local")
		self.assertTrue(onboarding.endpoint_url().startswith("http://umbrel.local/"))

	def test_it_borrows_the_port_from_a_matching_browser_origin(self):
		"""The configured name is the right host, the browser knows the right
		port; neither alone is a working URL."""
		self.configured("http://umbrel.local", origin="http://umbrel.local:5300")
		self.assertEqual(
			onboarding.endpoint_url(),
			"http://umbrel.local:5300/api/method/erpnext_mcp.mcp.handle",
		)

	def test_it_does_not_borrow_a_port_from_a_different_host(self):
		"""A host_name pointing somewhere else must never be given a port that
		does not belong to it."""
		self.configured("http://erp.internal", origin="http://100.69.162.122:5300")
		self.assertEqual(onboarding.endpoint_url(), "http://erp.internal/api/method/erpnext_mcp.mcp.handle")

	def test_a_host_name_with_its_own_port_is_not_modified(self):
		self.configured("http://umbrel.local:9000", origin="http://umbrel.local:5300")
		self.assertEqual(
			onboarding.endpoint_url(),
			"http://umbrel.local:9000/api/method/erpnext_mcp.mcp.handle",
		)

	def test_the_hostname_key_is_honoured_too(self):
		"""get_url() accepts either spelling, so this must as well."""
		frappe.conf["hostname"] = "erp.example.com"
		self.request({})
		self.assertEqual(onboarding.configured_host_name(), "http://erp.example.com")

	def test_public_url_still_wins_over_host_name(self):
		self.configured("http://umbrel.local")
		self.configure(enabled=1, public_url="https://erp.tail1234.ts.net")
		self.assertEqual(
			onboarding.endpoint_url(),
			"https://erp.tail1234.ts.net/api/method/erpnext_mcp.mcp.handle",
		)


class RoutingWarning(V2TestCase):
	"""A bare IP reaches Frappe's site router and matches no site directory."""

	def browsing_from_ip(self, **conf):
		frappe.conf.update(conf)
		self.request({}, headers={"Origin": "http://100.69.162.122:5300"})
		frappe.local.request.host = "100.69.162.122"
		frappe.local.session.user = "Administrator"

	def test_a_bare_ip_without_a_default_site_warns(self):
		self.browsing_from_ip()
		warning = onboarding.claude_desktop_config()["routing_warning"]
		self.assertEqual(warning["code"], "BARE_IP_NO_DEFAULT_SITE")
		self.assertEqual(warning["host"], "100.69.162.122")

	def test_the_warning_names_all_three_fixes(self):
		"""An operator reading it should not have to go and find out what to do."""
		self.browsing_from_ip()
		message = onboarding.claude_desktop_config()["routing_warning"]["message"]
		self.assertIn("default_site", message)
		self.assertIn("host_name", message)
		self.assertIn("Public URL", message)

	def test_a_default_site_silences_it(self):
		self.browsing_from_ip(default_site="frontend")
		self.assertEqual(onboarding.claude_desktop_config()["routing_warning"], {})

	def test_a_site_name_header_silences_it(self):
		"""A proxy pinning X-Frappe-Site-Name routes every request regardless of
		Host, so the IP is harmless — and the same proxy serves the MCP client."""
		self.request(
			{},
			headers={"Origin": "http://100.69.162.122:5300", "X-Frappe-Site-Name": "frontend"},
		)
		frappe.local.request.host = "100.69.162.122"
		frappe.local.session.user = "Administrator"
		self.assertEqual(onboarding.claude_desktop_config()["routing_warning"], {})

	def test_a_named_host_never_warns(self):
		self.request({}, headers={"Origin": "http://umbrel.local:5300"})
		frappe.local.session.user = "Administrator"
		self.assertEqual(onboarding.claude_desktop_config()["routing_warning"], {})

	def test_ipv6_literals_count_as_bare_ips(self):
		self.assertTrue(onboarding.is_bare_ip("http://[::1]:5300/x"))
		self.assertTrue(onboarding.is_bare_ip("http://10.0.0.5/x"))
		self.assertFalse(onboarding.is_bare_ip("http://umbrel.local:5300/x"))
