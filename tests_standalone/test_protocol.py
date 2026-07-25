# SPDX-License-Identifier: MIT
"""JSON-RPC and MCP handshake behaviour."""

import json

from erpnext_mcp import protocol, registry

from .fixtures import SeededTestCase


class Handshake(SeededTestCase):
	def test_initialize_echoes_a_supported_version(self):
		for version in protocol.SUPPORTED_PROTOCOL_VERSIONS:
			body, _ = self.call("initialize", {"protocolVersion": version})
			self.assertEqual(body["result"]["protocolVersion"], version)

	def test_initialize_answers_with_its_preferred_version_when_unknown(self):
		body, _ = self.call("initialize", {"protocolVersion": "1999-01-01"})
		self.assertEqual(
			body["result"]["protocolVersion"], protocol.PREFERRED_PROTOCOL_VERSION
		)

	def test_initialize_advertises_tools_only(self):
		body, _ = self.call("initialize", {"protocolVersion": "2025-06-18"})
		self.assertEqual(list(body["result"]["capabilities"]), ["tools"])

	def test_initialize_carries_usable_instructions(self):
		"""The instructions are how a client learns to orient itself on an
		unfamiliar site, so they must name the tool that does that."""
		body, _ = self.call("initialize", {"protocolVersion": "2025-06-18"})
		self.assertIn("get_company_topology", body["result"]["instructions"])

	def test_server_info_reports_the_app_version(self):
		from erpnext_mcp import __version__

		body, _ = self.call("initialize", {})
		self.assertEqual(body["result"]["serverInfo"]["version"], __version__)


class Methods(SeededTestCase):
	def test_unknown_method_is_32601(self):
		body, _ = self.call("wat")
		self.assertEqual(body["error"]["code"], protocol.METHOD_NOT_FOUND)

	def test_unadvertised_capabilities_say_so(self):
		for method in ("resources/list", "prompts/list"):
			body, _ = self.call(method)
			self.assertEqual(body["error"]["code"], protocol.METHOD_NOT_FOUND)
			self.assertIn("tools only", body["error"]["message"])

	def test_notification_for_an_unknown_method_is_silent(self):
		"""A notification has no id, so there is nobody to answer."""
		from erpnext_mcp import mcp

		self.request({"jsonrpc": "2.0", "method": "wat"})
		self.assertEqual(mcp.handle().status_code, 202)

	def test_missing_method_is_invalid_request(self):
		body, _ = self.call(None)
		self.assertEqual(body["error"]["code"], protocol.INVALID_REQUEST)

	def test_non_object_params_is_invalid_params(self):
		from erpnext_mcp import mcp

		self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []})
		body = json.loads(mcp.handle().get_data(as_text=True))
		self.assertEqual(body["error"]["code"], protocol.INVALID_PARAMS)

	def test_tools_call_without_a_name_is_invalid_params(self):
		body, _ = self.call("tools/call", {"arguments": {}})
		self.assertEqual(body["error"]["code"], protocol.INVALID_PARAMS)

	def test_tools_call_with_non_object_arguments_is_invalid_params(self):
		body, _ = self.call("tools/call", {"name": "search_accounts", "arguments": "cash"})
		self.assertEqual(body["error"]["code"], protocol.INVALID_PARAMS)

	def test_request_id_is_echoed_including_a_string_id(self):
		body, _ = self.call("ping", request_id="abc-1")
		self.assertEqual(body["id"], "abc-1")

	def test_tool_failure_is_a_result_not_a_jsonrpc_error(self):
		"""A model needs to tell "you called this wrong" from "the call itself was
		malformed" — only the second is a JSON-RPC error."""
		body, status = self.call(
			"tools/call", {"name": "get_account_balance", "arguments": {"account": "nope"}}
		)
		self.assertEqual(status, 200)
		self.assertNotIn("error", body)
		self.assertTrue(body["result"]["isError"])


class Catalogue(SeededTestCase):
	def test_lists_every_tool_when_all_are_enabled(self):
		self.configure(**{f"allow_{name}": 1 for name in registry.TOOLS}, enabled=1)
		body, _ = self.call("tools/list")
		self.assertEqual(
			sorted(tool["name"] for tool in body["result"]["tools"]),
			sorted(registry.TOOLS),
		)

	def test_default_catalogue_is_the_ten_read_tools(self):
		"""Straight out of the box: everything readable, nothing writable."""
		body, _ = self.call("tools/list")
		names = sorted(tool["name"] for tool in body["result"]["tools"])
		self.assertEqual(names, sorted(registry.READ_TOOLS))
		self.assertEqual(len(names), 10)

	def test_disabled_tool_is_not_advertised_at_all(self):
		self.configure(enabled=1, allow_search_accounts=0)
		body, _ = self.call("tools/list")
		names = [tool["name"] for tool in body["result"]["tools"]]
		self.assertNotIn("search_accounts", names)

	def test_every_tool_has_a_schema_and_annotations(self):
		self.configure(**{f"allow_{name}": 1 for name in registry.TOOLS}, enabled=1)
		body, _ = self.call("tools/list")
		for tool in body["result"]["tools"]:
			with self.subTest(tool=tool["name"]):
				self.assertEqual(tool["inputSchema"]["type"], "object")
				self.assertFalse(tool["inputSchema"]["additionalProperties"])
				self.assertTrue(tool["description"])
				self.assertIn("readOnlyHint", tool["annotations"])

	def test_read_only_hint_matches_the_mutating_flag(self):
		"""Derived, not hand-written, so a write tool cannot advertise itself as
		safe."""
		for name, spec in registry.TOOLS.items():
			with self.subTest(tool=name):
				self.assertEqual(spec["annotations"]["readOnlyHint"], not spec["mutating"])

	def test_mutating_descriptions_announce_themselves(self):
		for name in registry.MUTATING_TOOLS:
			with self.subTest(tool=name):
				self.assertIn("MUTATING", registry.TOOLS[name]["description"])

	def test_required_arguments_are_declared_in_the_schema(self):
		self.assertEqual(
			registry.TOOLS["create_journal_entry"]["inputSchema"]["required"],
			["company", "posting_date", "accounts", "user_remark"],
		)

	def test_catalogue_split_is_ten_read_five_write(self):
		self.assertEqual(len(registry.READ_TOOLS), 10)
		self.assertEqual(len(registry.MUTATING_TOOLS), 5)
