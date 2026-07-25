# SPDX-License-Identifier: MIT
"""The three gates, and the HTTP shape of the endpoint.

These are the tests worth having most: everything else in this app is a query,
but these decide whether a stranger on the network can read a general ledger.
"""

import json

from .fixtures import SeededTestCase
from .harness import STORE, frappe


class MasterSwitch(SeededTestCase):
	def test_disabled_is_indistinguishable_from_not_installed(self):
		self.configure(enabled=0)
		body, status = self.call("tools/list")
		self.assertEqual(status, 404)
		self.assertNotIn("tool", json.dumps(body).lower())

	def test_no_token_configured_is_also_404(self):
		"""A configured-but-tokenless server must not admit to existing.

		The form refuses to save `enabled` without a token, so this state only
		arises from a direct DB edit or a half-finished restore — exactly when
		you least want the endpoint answering.
		"""
		self.set_token("")
		body, status = self.call("tools/list")
		self.assertEqual(status, 404)

	def test_disabled_endpoint_writes_no_audit_rows(self):
		"""Otherwise anyone who can reach the URL can grow the table at will."""
		self.configure(enabled=0)
		self.call("tools/list")
		self.assertEqual(self.audit_rows(), [])


class BearerToken(SeededTestCase):
	def test_correct_token_is_accepted(self):
		body, status = self.call("ping")
		self.assertEqual(status, 200)
		self.assertEqual(body["result"], {})

	def test_wrong_token_is_401(self):
		body, status = self.call("ping", token="w" * 48)
		self.assertEqual(status, 401)

	def test_missing_header_is_401(self):
		body, status = self.call("ping", token=False)
		self.assertEqual(status, 401)

	def test_rejection_does_not_say_which_gate_failed(self):
		"""No oracle: a bad token and a bad IP must look identical."""
		bad_token, _ = self.call("ping", token="w" * 48)
		self.configure(enabled=1, allowed_cidrs="10.9.9.0/24")
		bad_ip, _ = self.call("ping", remote_addr="203.0.113.7")
		self.assertEqual(bad_token["error"]["message"], bad_ip["error"]["message"])
		self.assertEqual(bad_token["error"]["message"], "unauthorized")

	def test_x_mcp_token_header_also_works(self):
		"""The documented fallback for a Frappe version that eats Bearer."""
		body, status = self.call("ping", token=False, headers={"X-MCP-Token": self.TOKEN})
		self.assertEqual(status, 200)

	def test_token_never_appears_in_a_response(self):
		body, _ = self.call("initialize", {"protocolVersion": "2025-06-18"})
		self.assertNotIn(self.TOKEN, json.dumps(body))


class NetworkAllowlist(SeededTestCase):
	def test_loopback_is_allowed_by_default(self):
		_, status = self.call("ping", remote_addr="127.0.0.1")
		self.assertEqual(status, 200)

	def test_ipv6_loopback_is_allowed_by_default(self):
		"""`localhost` resolves to ::1 first on a modern host, so a default that
		omitted it would fail every operator's very first curl."""
		_, status = self.call("ping", remote_addr="::1")
		self.assertEqual(status, 200)

	def test_public_address_is_403(self):
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8")
		_, status = self.call("ping", remote_addr="203.0.113.7")
		self.assertEqual(status, 403)

	def test_empty_allowlist_denies_everyone(self):
		self.configure(enabled=1, allowed_cidrs="")
		_, status = self.call("ping", remote_addr="127.0.0.1")
		self.assertEqual(status, 403)

	def test_one_bad_entry_does_not_disable_the_gate(self):
		self.configure(enabled=1, allowed_cidrs="not-a-cidr,127.0.0.1/32")
		_, status = self.call("ping", remote_addr="127.0.0.1")
		self.assertEqual(status, 200)

	def test_gate_uses_the_rightmost_forwarded_hop(self):
		"""bench's nginx appends to X-Forwarded-For, so the leftmost entry is
		whatever the client claimed. Trusting it would make the allowlist
		decorative."""
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8")
		_, status = self.call(
			"ping",
			remote_addr="127.0.0.1",
			headers={"X-Forwarded-For": "10.0.0.5, 203.0.113.7"},
		)
		self.assertEqual(status, 403, "spoofed leftmost hop was trusted")

		_, status = self.call(
			"ping",
			remote_addr="127.0.0.1",
			headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.5"},
		)
		self.assertEqual(status, 200)

	def test_ipv4_cidr_does_not_match_an_ipv6_caller(self):
		self.configure(enabled=1, allowed_cidrs="0.0.0.0/0")
		_, status = self.call("ping", remote_addr="2001:db8::1")
		self.assertEqual(status, 403)

	def test_same_origin_system_manager_bypasses_the_cidr_gate(self):
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8")
		frappe.local.session.user = "Administrator"
		_, status = self.call(
			"ping",
			remote_addr="203.0.113.7",
			headers={"Origin": "https://test.localhost"},
		)
		self.assertEqual(status, 200)

	def test_forged_origin_alone_does_not_bypass_it(self):
		"""Any non-browser client can send any Origin, so the session half of the
		check is what makes the allowance safe."""
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8")
		frappe.local.session.user = "Guest"
		_, status = self.call(
			"ping",
			remote_addr="203.0.113.7",
			headers={"Origin": "https://test.localhost"},
		)
		self.assertEqual(status, 403)

	def test_origin_for_another_host_does_not_bypass_it(self):
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8")
		frappe.local.session.user = "Administrator"
		_, status = self.call(
			"ping",
			remote_addr="203.0.113.7",
			headers={"Origin": "https://evil.example"},
		)
		self.assertEqual(status, 403)


class HTTPShape(SeededTestCase):
	def test_get_is_405_with_an_explanation(self):
		from erpnext_mcp import mcp

		self.request({}, method="GET")
		response = mcp.handle()
		self.assertEqual(response.status_code, 405)
		self.assertIn("POST-only", response.get_data(as_text=True))

	def test_unparseable_body_is_a_jsonrpc_parse_error(self):
		from erpnext_mcp import mcp

		self.request("{not json", method="POST")
		response = mcp.handle()
		self.assertEqual(response.status_code, 400)
		self.assertEqual(json.loads(response.get_data(as_text=True))["error"]["code"], -32700)

	def test_notification_gets_202_and_no_body(self):
		from erpnext_mcp import mcp

		self.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
		response = mcp.handle()
		self.assertEqual(response.status_code, 202)
		self.assertEqual(response.get_data(as_text=True), "")

	def test_batch_returns_one_response_per_request(self):
		from erpnext_mcp import mcp

		self.request(
			[
				{"jsonrpc": "2.0", "id": 1, "method": "ping"},
				{"jsonrpc": "2.0", "method": "notifications/initialized"},
				{"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
			]
		)
		response = mcp.handle()
		body = json.loads(response.get_data(as_text=True))
		self.assertEqual([item["id"] for item in body], [1, 2])

	def test_sse_accept_header_gets_an_event_frame(self):
		from erpnext_mcp import mcp

		self.request(
			{"jsonrpc": "2.0", "id": 1, "method": "ping"},
			headers={"Accept": "text/event-stream"},
		)
		response = mcp.handle()
		text = response.get_data(as_text=True)
		self.assertTrue(response.content_type.startswith("text/event-stream"))
		self.assertTrue(text.startswith("event: message\ndata: "))
		self.assertTrue(text.endswith("\n\n"))
		self.assertEqual(json.loads(text.split("data: ", 1)[1].strip())["id"], 1)

	def test_response_is_bare_jsonrpc_not_wrapped_in_message(self):
		"""A Frappe whitelisted method normally answers `{"message": ...}`, which
		no MCP client can parse."""
		body, _ = self.call("ping")
		self.assertEqual(set(body), {"jsonrpc", "id", "result"})


class TransportAudit(SeededTestCase):
	def test_rejected_call_is_logged_with_the_real_reason(self):
		self.call("ping", token="w" * 48)
		row = self.assertAudited("<transport>", status="Unauthorized")
		self.assertIn("bearer token", row["result_summary"])

	def test_rejection_row_is_committed_immediately(self):
		"""It has to outlive the request even if nothing else commits."""
		before = STORE.committed
		self.call("ping", token="w" * 48)
		self.assertGreater(STORE.committed, before)


class UserContext(SeededTestCase):
	def test_falls_back_to_administrator_without_a_configured_user(self):
		self.call("ping")
		self.assertEqual(frappe.session.user, "Administrator")

	def test_runs_as_the_configured_mcp_system_user(self):
		self.configure(
			enabled=1, require_user_context=1, mcp_system_user="mcp@example.test"
		)
		self.call("ping")
		self.assertEqual(frappe.session.user, "mcp@example.test")

	def test_ignores_a_user_that_does_not_exist(self):
		# A configured user who has since been deleted must not take the server
		# down; falling back to Administrator keeps it working, and the audit log
		# records what ran either way.
		self.configure(enabled=1, require_user_context=1, mcp_system_user="ghost@example.test")
		self.call("ping")
		self.assertEqual(frappe.session.user, "Administrator")

	def test_require_user_context_off_uses_administrator(self):
		self.configure(
			enabled=1, require_user_context=0, mcp_system_user="mcp@example.test"
		)
		self.call("ping")
		self.assertEqual(frappe.session.user, "Administrator")
