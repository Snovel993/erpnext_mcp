# SPDX-License-Identifier: MIT
"""Tailscale Funnel — v0.17.0 Feature C.

THREE CLAIMS, AND THE FIRST IS THE ONE THAT KEEPS SOMEBODY OUT OF TROUBLE.

1. **THE OUTBOUND PROBE CANNOT BE POINTED AT ANYTHING.** `TheUrlAllowlist`.
   `validate_public_endpoint` makes an HTTPS request FROM INSIDE the site's
   network, which is the shape of every server-side request forgery there has
   ever been. So the reachable set is the operator's own configured
   `public_url` plus hosts under `.ts.net`, over HTTPS, base URL only, redirects
   not followed — and `authenticate=true`, which sends the site's real bearer
   token, refuses every URL except the configured one. A tool that will POST
   your token to a hostname in its arguments is a tool that exfiltrates it.

2. **THE VERDICTS SAY THE RIGHT THING, INCLUDING WHEN IT IS BAD NEWS.**
   `TheVerdict`. A 401 to an unauthenticated probe is the BEST result and is
   reported as such; a 200 with tools advertised is reported with the sentence
   an operator needs to read, which is that everything the enabled tools expose
   is now reachable from the internet by anybody holding the token.

3. **IT DEGRADES INSTEAD OF FAILING WHEN TAILSCALE IS NOT VISIBLE.**
   `TheLocalConfig`. A containerised Frappe worker has neither the `tailscale`
   binary nor the host's socket, and on the target deployment that is the
   EXPECTED state rather than a fault. The tool says which of the two situations
   it is in and points at the probe that needs none of it.

There is deliberately no test for a tool that turns Funnel on, because there is
deliberately no such tool. `NoMutation` asserts that, so one cannot arrive
quietly.
"""

import json
import socket
import ssl

from erpnext_mcp import registry
from erpnext_mcp.tools import funnel

from .fixtures import SeededTestCase

FUNNEL = "https://umbrel.tail4a2b.ts.net"

ON = {f"allow_{name}": 1 for name in ("validate_public_endpoint", "get_tailscale_funnel_config")}


class FunnelTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, public_url=FUNNEL, **ON)
		self._patched = []

	def tearDown(self):
		for target, name, original in reversed(self._patched):
			setattr(target, name, original)
		super().tearDown()

	def patch(self, name, replacement):
		self._patched.append((funnel, name, getattr(funnel, name)))
		setattr(funnel, name, replacement)

	def probe(self, tls=None, http=None, **arguments):
		self.patch("_tls_report", lambda *a, **k: dict(tls if tls is not None else GOOD_TLS))
		self.patch("_http_report", lambda *a, **k: dict(http if http is not None else UNAUTH_401))
		return self.tool_data("validate_public_endpoint", arguments)


GOOD_TLS = {
	"reachable": True,
	"verified": True,
	"protocol": "TLSv1.3",
	"issuer": "CN=E5, O=Let's Encrypt, C=US",
	"subject": "CN=umbrel.tail4a2b.ts.net",
	"not_after": "Nov  1 00:00:00 2026 GMT",
	"days_until_expiry": 60,
	"latency_ms": 42.0,
}
UNAUTH_401 = {"status": 401, "authenticated": False, "json_rpc": True, "latency_ms": 55.0}


class TheUrlAllowlist(FunnelTestCase):
	def test_the_configured_public_url_is_reachable(self):
		self.assertEqual(funnel._target_url({}), FUNNEL)

	def test_any_ts_net_host_is_reachable(self):
		self.assertEqual(
			funnel._target_url({"url": "https://other.tailnet.ts.net/"}),
			"https://other.tailnet.ts.net",
		)

	def test_a_host_that_is_neither_is_refused(self):
		message = self.tool_error("validate_public_endpoint", {"url": "https://internal.example.com"})
		self.assertIn("short allowlist and not an argument", message)
		self.assertIn("Nothing was sent", message)

	def test_a_plaintext_url_is_refused(self):
		message = self.tool_error("validate_public_endpoint", {"url": "http://umbrel.local"})
		self.assertIn("not an https:// URL", message)

	def test_a_url_carrying_a_path_is_refused_as_a_forgery_primitive(self):
		"""The tool appends the MCP path itself. One that fetched an arbitrary path
		from inside the network would be a request-forgery primitive."""
		message = self.tool_error("validate_public_endpoint", {"url": "https://umbrel.tail4a2b.ts.net/admin"})
		self.assertIn("carries a path", message)
		self.assertIn("request-forgery", message)

	def test_a_url_carrying_a_query_is_refused_too(self):
		self.assertIn(
			"carries a path",
			self.tool_error("validate_public_endpoint", {"url": "https://umbrel.tail4a2b.ts.net?x=1"}),
		)

	def test_a_site_with_nothing_configured_and_no_url_says_what_to_fill_in(self):
		self.configure(enabled=1, public_url="", **ON)
		message = self.tool_error("validate_public_endpoint", {})
		self.assertIn("public_url", message)
		self.assertIn("ts.net", message)

	def test_authenticate_refuses_any_url_but_the_configured_one(self):
		"""THE ONE THAT MATTERS. `authenticate=true` sends this site's real bearer
		token; a tool that sent it to a hostname in its arguments would exfiltrate
		it, and `.ts.net` is not a small enough set for that."""
		message = self.tool_error(
			"validate_public_endpoint",
			{"url": "https://attacker.tail9999.ts.net", "authenticate": True},
		)
		self.assertIn("exfiltrates it", message)
		self.assertIn("Nothing was sent", message)

	def test_authenticate_with_no_token_configured_is_refused(self):
		"""Called directly rather than through the endpoint, because a site with no
		auth_token answers every request with 404 by design — see security.py. The
		branch is still real: an operator can clear the token between requests."""
		from erpnext_mcp.errors import ToolError

		self.set_token("")
		with self.assertRaises(ToolError) as caught:
			funnel.validate_public_endpoint({"url": FUNNEL, "authenticate": True})
		self.assertIn("nothing to present", str(caught.exception))

	def test_authenticate_against_the_configured_url_is_allowed(self):
		sent = {}

		def http(endpoint, authenticate, timeout):
			sent["authenticate"] = authenticate
			return dict(UNAUTH_401, authenticated=True, status=200, tools_advertised=12)

		self.patch("_tls_report", lambda *a, **k: dict(GOOD_TLS))
		self.patch("_http_report", http)
		data = self.tool_data("validate_public_endpoint", {"url": FUNNEL, "authenticate": True})
		self.assertTrue(sent["authenticate"])
		self.assertTrue(data["working"])

	def test_it_never_follows_a_redirect(self):
		"""A 30x from a public endpoint is a finding, not a step."""
		self.assertIsNone(
			funnel._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere")
		)


class TheVerdict(FunnelTestCase):
	def test_a_401_to_an_unauthenticated_probe_is_the_best_result(self):
		"""It proves the path is reachable, the certificate is valid, and the token
		gate is holding — all three at once."""
		data = self.probe()
		self.assertTrue(data["working"])
		self.assertIn("exactly right", data["summary"])
		self.assertIn("token gate is holding", data["summary"])

	def test_a_200_says_what_is_now_public(self):
		data = self.probe(http={"status": 200, "json_rpc": True, "tools_advertised": 93})
		self.assertTrue(data["working"])
		self.assertIn("93 tool(s)", data["summary"])
		self.assertIn("reachable from the internet", data["next_step"])

	def test_a_404_names_the_three_things_it_can_mean(self):
		data = self.probe(http={"status": 404, "json_rpc": False})
		self.assertFalse(data["working"])
		self.assertIn("enabled", data["next_step"])
		self.assertIn("auth_token", data["next_step"])

	def test_an_unreachable_host_points_at_the_local_config_tool(self):
		data = self.probe(tls={"reachable": False, "error": "timed out after 8s", "latency_ms": 8000})
		self.assertFalse(data["working"])
		self.assertIn("did not answer", data["summary"])
		self.assertIn("get_tailscale_funnel_config", data["next_step"])

	def test_an_unverifiable_certificate_is_not_funnel(self):
		data = self.probe(
			tls={
				"reachable": True,
				"verified": False,
				"error": "certificate verification failed",
				"diagnosis": "self-signed, so something else is answering on 443",
			}
		)
		self.assertFalse(data["working"])
		self.assertIn("did not verify", data["summary"])
		self.assertIn("self-signed", data["next_step"])

	def test_valid_tls_with_no_http_answer_blames_the_forward(self):
		data = self.probe(http={"error": "timed out after 8s"})
		self.assertFalse(data["working"])
		self.assertIn("Funnel forwards to the port", data["next_step"])

	def test_it_reports_the_certificate_facts_an_operator_reads(self):
		data = self.probe()
		self.assertEqual(data["tls"]["issuer"], GOOD_TLS["issuer"])
		self.assertEqual(data["tls"]["days_until_expiry"], 60)
		self.assertTrue(data["looks_like_tailscale_funnel"])
		self.assertTrue(data["is_configured_url"])

	def test_the_endpoint_it_probes_is_the_mcp_path(self):
		data = self.probe()
		self.assertEqual(data["endpoint"], f"{FUNNEL}{funnel.MCP_PATH}")

	def test_it_is_on_out_of_the_box(self):
		self.configure(enabled=1, public_url=FUNNEL)
		self.patch("_tls_report", lambda *a, **k: dict(GOOD_TLS))
		self.patch("_http_report", lambda *a, **k: dict(UNAUTH_401))
		self.assertFalse(self.tool("validate_public_endpoint").get("isError"))


class TheProbeItself(FunnelTestCase):
	"""The two report builders, which must never raise — a diagnostic that throws
	on a broken network is a diagnostic nobody can use on the day they need it."""

	def test_a_dns_failure_is_reported_not_raised(self):
		def boom(*args, **kwargs):
			raise socket.gaierror("Name or service not known")

		self.patch("socket", _FakeSocket(boom))
		report = funnel._tls_report("nowhere.ts.net", 443, 1)
		self.assertFalse(report["reachable"])
		self.assertIn("gaierror", report["error"])
		self.assertIn("public DNS", report["diagnosis"])

	def test_a_certificate_failure_is_reported_not_raised(self):
		def boom(*args, **kwargs):
			raise ssl.SSLCertVerificationError("self signed certificate")

		self.patch("socket", _FakeSocket(boom))
		report = funnel._tls_report("nowhere.ts.net", 443, 1)
		self.assertTrue(report["reachable"])
		self.assertFalse(report["verified"])
		self.assertIn("Let's Encrypt", report["diagnosis"])

	def test_it_recognises_this_apps_own_answer(self):
		body = json.dumps(
			{"jsonrpc": "2.0", "id": "probe", "result": {"tools": [{"name": "a"}, {"name": "b"}]}}
		).encode()
		self.assertEqual(funnel._read_rpc(body), {"json_rpc": True, "tools_advertised": 2})

	def test_a_non_json_body_is_not_json_rpc(self):
		self.assertEqual(funnel._read_rpc(b"<html>nginx</html>"), {"json_rpc": False})

	def test_an_rpc_error_body_is_reported_with_its_message(self):
		body = json.dumps({"jsonrpc": "2.0", "error": {"message": "unauthorized"}}).encode()
		self.assertEqual(funnel._read_rpc(body)["rpc_error"], "unauthorized")

	def test_a_certificate_name_flattens_readably(self):
		self.assertEqual(
			funnel._name_from(((("commonName", "x.ts.net"),), (("organizationName", "Tailscale"),))),
			"commonName=x.ts.net, organizationName=Tailscale",
		)


class _FakeSocket:
	"""Just enough `socket` for `_tls_report` to fail the way we asked it to."""

	timeout = socket.timeout

	def __init__(self, create_connection):
		self.create_connection = create_connection


class TheLocalConfig(FunnelTestCase):
	def test_with_no_binary_it_says_which_situation_this_is(self):
		"""A container with no Tailscale is the EXPECTED state on an Umbrel, not a
		fault. Saying 'not found' and stopping would send somebody to fix a thing
		that is not broken."""
		self.patch("_tailscale_binary", lambda: "")
		self.patch("_tailscale_socket", lambda: "")
		data = self.tool_data("get_tailscale_funnel_config")
		self.assertFalse(data["can_read_config"])
		self.assertIn("EXPECTED state", data["diagnosis"])
		self.assertIn("not a fault", data["diagnosis"])
		self.assertIn("validate_public_endpoint", data["next_step"])

	def test_a_socket_with_no_client_is_a_different_diagnosis(self):
		self.patch("_tailscale_binary", lambda: "")
		self.patch("_tailscale_socket", lambda: "/var/run/tailscale/tailscaled.sock")
		data = self.tool_data("get_tailscale_funnel_config")
		self.assertIn("daemon is reachable and the client is not installed", data["diagnosis"])

	def test_it_still_reports_the_configured_url_when_it_can_read_nothing(self):
		self.patch("_tailscale_binary", lambda: "")
		self.patch("_tailscale_socket", lambda: "")
		self.assertEqual(self.tool_data("get_tailscale_funnel_config")["configured_public_url"], FUNNEL)

	def test_it_reads_the_ports_and_urls_out_of_a_serve_config(self):
		self.patch("_tailscale_binary", lambda: "/usr/bin/tailscale")
		self.patch("_run", _fake_run)
		data = self.tool_data("get_tailscale_funnel_config")
		self.assertEqual(data["funnel_ports"], [443])
		self.assertEqual(data["funnel_urls"], ["https://umbrel.tail4a2b.ts.net"])
		self.assertIn(443, data["serve_ports"])
		self.assertEqual(data["node"]["dns_name"], "umbrel.tail4a2b.ts.net")
		self.assertTrue(data["public_url_matches"])

	def test_it_says_what_is_published_to_the_internet(self):
		self.patch("_tailscale_binary", lambda: "/usr/bin/tailscale")
		self.patch("_run", _fake_run)
		note = self.tool_data("get_tailscale_funnel_config")["exposure_note"]
		self.assertIn("PUBLIC INTERNET", note)
		self.assertIn("certificate transparency", note)
		self.assertIn("token is the whole boundary", note)

	def test_a_config_it_cannot_parse_is_reported_as_unparsed(self):
		"""'No funnel ports' and 'I could not tell' are different answers."""
		self.patch("_tailscale_binary", lambda: "/usr/bin/tailscale")
		self.patch("_run", lambda *a, **k: {"ok": True, "stdout": "not json", "command": "x"})
		data = self.tool_data("get_tailscale_funnel_config")
		self.assertIn("parse_error", data)
		self.assertEqual(data["funnel_ports"], [])

	def test_a_web_handler_shows_what_the_port_forwards_to(self):
		parsed = {
			"AllowFunnel": {"umbrel.tail4a2b.ts.net:443": True},
			"Web": {"umbrel.tail4a2b.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}},
		}
		read = funnel._read_serve_config(parsed)
		self.assertEqual(read["web_handlers"][0]["proxy"], "http://127.0.0.1:8000")

	def test_a_funnel_that_is_declared_but_off_is_not_counted(self):
		read = funnel._read_serve_config({"AllowFunnel": {"host:443": False}})
		self.assertEqual(read["funnel_ports"], [])

	def test_it_is_on_out_of_the_box(self):
		self.configure(enabled=1, public_url=FUNNEL)
		self.patch("_tailscale_binary", lambda: "")
		self.patch("_tailscale_socket", lambda: "")
		self.assertFalse(self.tool("get_tailscale_funnel_config").get("isError"))


def _fake_run(binary, argv, timeout):
	if argv[:2] == ["serve", "status"]:
		return {
			"ok": True,
			"command": "serve status --json",
			"stdout": json.dumps(
				{
					"AllowFunnel": {"umbrel.tail4a2b.ts.net:443": True},
					"TCP": {"443": {"HTTPS": True}},
					"Web": {
						"umbrel.tail4a2b.ts.net:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8000"}}}
					},
				}
			),
		}
	return {
		"ok": True,
		"command": "status --json",
		"stdout": json.dumps(
			{
				"BackendState": "Running",
				"Self": {
					"DNSName": "umbrel.tail4a2b.ts.net.",
					"HostName": "umbrel",
					"Online": True,
					"TailscaleIPs": ["100.64.0.7"],
				},
			}
		),
	}


class NoMutation(SeededTestCase):
	"""There is no tool that turns Funnel on or off, and one must not arrive
	quietly. Changing what is reachable from the entire internet is an operator
	decision made deliberately — and `tailscale funnel` needs a local socket and
	privileges a containerised Frappe worker does not have, so a tool that
	shipped would fail on the target deployment while looking like it might work.
	"""

	def test_no_funnel_tool_is_declared_mutating(self):
		for name, spec in registry.TOOLS.items():
			if spec["handler"].__module__.endswith(".funnel"):
				with self.subTest(tool=name):
					self.assertFalse(spec["mutating"], f"{name} would change public exposure")

	def test_there_are_exactly_two_of_them(self):
		names = sorted(
			name for name, spec in registry.TOOLS.items() if spec["handler"].__module__.endswith(".funnel")
		)
		self.assertEqual(names, ["get_tailscale_funnel_config", "validate_public_endpoint"])

	def test_the_config_tool_says_so_in_its_own_result(self):
		self.configure(enabled=1, **ON)
		original = funnel._tailscale_binary
		funnel._tailscale_binary = lambda: "/usr/bin/tailscale"
		original_run = funnel._run
		funnel._run = _fake_run
		try:
			note = self.tool_data("get_tailscale_funnel_config")["mutation_note"]
		finally:
			funnel._tailscale_binary = original
			funnel._run = original_run
		self.assertIn("NOTHING IN THIS APP CAN TURN FUNNEL ON OR OFF", note)
		self.assertIn("README", note)

	def test_the_subprocess_argv_is_never_built_from_a_tool_argument(self):
		"""Fixed argv, no shell. There is nothing for a caller to inject into, and
		the only caller-supplied value anywhere near it is a timeout."""
		import inspect

		source = inspect.getsource(funnel.get_tailscale_funnel_config)
		self.assertIn('_run(binary, ["serve", "status", "--json"]', source)
		self.assertIn('_run(binary, ["status", "--json"]', source)
		self.assertNotIn("shell=True", inspect.getsource(funnel))
