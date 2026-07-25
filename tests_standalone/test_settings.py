# SPDX-License-Identifier: MIT
"""ERPNext MCP Settings: the shipped defaults, the form's guardrails, the token.

The first class here is the one that matters if you only read one: it checks the
DocType JSON against the tool registry, so a tool added to `registry.py` without
a switch on the form — which would ship with no way to turn it off — fails the
build.
"""

import json

from erpnext_mcp import registry, settings
from erpnext_mcp.erpnext_mcp.doctype.erpnext_mcp_settings.erpnext_mcp_settings import (
	TOKEN_LENGTH,
)

from .fixtures import SeededTestCase
from .harness import META, STORE, _load_app_doctype, frappe


class ShippedDefaults(SeededTestCase):
	def setUp(self):
		super().setUp()
		self.json = _load_app_doctype("erpnext_mcp_settings")
		self.by_name = {field["fieldname"]: field for field in self.json["fields"]}

	def test_every_tool_has_a_switch(self):
		for name in registry.TOOLS:
			with self.subTest(tool=name):
				self.assertIn(
					f"allow_{name}",
					self.by_name,
					f"tool {name} has no allow_ field, so an operator cannot turn it off",
				)

	def test_every_switch_has_a_tool(self):
		"""The other direction: a leftover switch is a control that does nothing."""
		for fieldname in self.by_name:
			if fieldname.startswith("allow_"):
				with self.subTest(field=fieldname):
					self.assertIn(fieldname[len("allow_") :], registry.TOOLS)

	def test_read_tools_default_on(self):
		for name in registry.READ_TOOLS:
			with self.subTest(tool=name):
				self.assertEqual(self.by_name[f"allow_{name}"]["default"], "1")

	def test_mutating_tools_default_off(self):
		for name in registry.MUTATING_TOOLS:
			with self.subTest(tool=name):
				self.assertEqual(self.by_name[f"allow_{name}"]["default"], "0")

	def test_the_master_switch_defaults_off(self):
		self.assertEqual(self.by_name["enabled"]["default"], "0")

	def test_the_token_is_a_password_field(self):
		self.assertEqual(self.by_name["auth_token"]["fieldtype"], "Password")

	def test_the_default_cidrs_are_lan_only(self):
		default = self.by_name["allowed_cidrs"]["default"]
		self.assertEqual(default, settings.DEFAULT_CIDRS)
		self.assertNotIn("0.0.0.0/0", default)

	def test_user_attribution_defaults_on(self):
		self.assertEqual(self.by_name["require_user_context"]["default"], "1")

	def test_only_system_manager_can_read_the_settings(self):
		self.assertEqual([p["role"] for p in self.json["permissions"]], ["System Manager"])

	def test_settings_changes_are_version_tracked(self):
		"""Who turned write access on, and when, is exactly the change worth a
		Version row."""
		self.assertTrue(self.json.get("track_changes"))

	def test_every_switch_carries_a_description(self):
		"""The description is the only explanation an operator gets before ticking
		a box that lets an AI post to the ledger."""
		for fieldname, field in self.by_name.items():
			if fieldname.startswith("allow_"):
				with self.subTest(field=fieldname):
					self.assertTrue(field.get("description"))


class BooleanCasting(SeededTestCase):
	def test_the_string_zero_reads_as_off(self):
		"""`tabSingles.value` is text, so a Check comes back as "0" — which Python
		calls truthy. Getting this wrong turns every switch on."""
		self.assertFalse(settings.as_bool("0"))
		self.assertFalse(settings.as_bool(""))
		self.assertFalse(settings.as_bool(None))
		self.assertFalse(settings.as_bool(0))

	def test_the_string_one_reads_as_on(self):
		self.assertTrue(settings.as_bool("1"))
		self.assertTrue(settings.as_bool(1))
		self.assertTrue(settings.as_bool(True))

	def test_a_string_zero_master_switch_disables_the_endpoint(self):
		self.configure(enabled="0")
		_, status = self.call("tools/list")
		self.assertEqual(status, 404)

	def test_a_string_zero_tool_switch_hides_the_tool(self):
		self.configure(enabled=1, allow_search_accounts="0")
		body, _ = self.call("tools/list")
		self.assertNotIn(
			"search_accounts", [tool["name"] for tool in body["result"]["tools"]]
		)


class MissingValues(SeededTestCase):
	def test_an_unset_field_falls_back_to_its_declared_default(self):
		"""A fresh install has no rows in tabSingles at all, and the read tools
		have to still be on."""
		STORE.singles["ERPNext MCP Settings"] = {"doctype": "ERPNext MCP Settings"}
		self.assertTrue(settings.tool_enabled("get_company_topology"))
		self.assertFalse(settings.tool_enabled("create_journal_entry"))
		self.assertEqual(settings.allowed_cidrs()[0], "127.0.0.1/32")

	def test_an_unset_master_switch_is_off(self):
		STORE.singles["ERPNext MCP Settings"] = {"doctype": "ERPNext MCP Settings"}
		self.assertFalse(settings.is_enabled())

	def test_a_tool_with_no_field_at_all_is_off(self):
		self.assertFalse(settings.tool_enabled("tool_from_a_future_version"))

	def test_seed_defaults_writes_the_declared_values(self):
		STORE.singles["ERPNext MCP Settings"] = {"doctype": "ERPNext MCP Settings"}
		settings.seed_defaults()
		stored = STORE.singles["ERPNext MCP Settings"]
		self.assertEqual(stored["allow_get_company_topology"], "1")
		self.assertEqual(stored["allow_create_journal_entry"], "0")
		self.assertEqual(stored["enabled"], "0")

	def test_seed_defaults_does_not_overwrite_an_operators_choice(self):
		"""Including a deliberate "off" — that is the whole point of "only fill in
		what is missing"."""
		self.configure(enabled=1, allow_search_accounts=0, allowed_cidrs="10.1.0.0/16")
		settings.seed_defaults()
		self.assertFalse(settings.tool_enabled("search_accounts"))
		self.assertEqual(settings.allowed_cidrs(), ["10.1.0.0/16"])

	def test_seed_defaults_is_idempotent(self):
		STORE.singles["ERPNext MCP Settings"] = {"doctype": "ERPNext MCP Settings"}
		settings.seed_defaults()
		first = dict(STORE.singles["ERPNext MCP Settings"])
		settings.seed_defaults()
		self.assertEqual(first, STORE.singles["ERPNext MCP Settings"])

	def test_settings_reads_fail_closed_when_the_doctype_is_missing(self):
		"""A request landing mid-migrate must not answer as if enabled."""
		removed = META.pop("ERPNext MCP Settings")
		try:
			self.assertFalse(settings.is_enabled())
			self.assertFalse(settings.tool_enabled("get_company_topology"))
		finally:
			META["ERPNext MCP Settings"] = removed


class CidrParsing(SeededTestCase):
	def test_newlines_and_commas_both_separate(self):
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8\n192.168.0.0/16, 127.0.0.1/32")
		self.assertEqual(
			settings.allowed_cidrs(), ["10.0.0.0/8", "192.168.0.0/16", "127.0.0.1/32"]
		)

	def test_trailing_comments_are_stripped(self):
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8 # the office\n127.0.0.1/32")
		self.assertEqual(settings.allowed_cidrs(), ["10.0.0.0/8", "127.0.0.1/32"])

	def test_blank_entries_are_dropped(self):
		self.configure(enabled=1, allowed_cidrs="10.0.0.0/8,,  ,127.0.0.1/32")
		self.assertEqual(settings.allowed_cidrs(), ["10.0.0.0/8", "127.0.0.1/32"])


class FormValidation(SeededTestCase):
	def doc(self, **overrides):
		doc = frappe.get_doc("ERPNext MCP Settings")
		for key, value in overrides.items():
			doc.set(key, value)
		return doc

	def test_an_invalid_cidr_is_refused_with_the_bad_entry_named(self):
		with self.assertRaises(Exception) as caught:
			self.doc(allowed_cidrs="10.0.0.0/8,192.168.1.999/24").save()
		self.assertIn("192.168.1.999/24", str(caught.exception))

	def test_a_valid_list_saves(self):
		self.doc(allowed_cidrs="10.0.0.0/8").save()
		self.assertEqual(settings.allowed_cidrs(), ["10.0.0.0/8"])

	def test_enabling_with_an_empty_allowlist_is_refused(self):
		with self.assertRaises(Exception) as caught:
			self.doc(enabled=1, allowed_cidrs="").save()
		self.assertIn("denies every caller", str(caught.exception))

	def test_an_empty_allowlist_is_allowed_while_disabled(self):
		"""Not a trap to walk into while tidying up a switched-off server."""
		self.doc(enabled=0, allowed_cidrs="").save()

	def test_enabling_without_a_token_is_refused(self):
		self.set_token("")
		with self.assertRaises(Exception) as caught:
			self.doc(enabled=1).save()
		self.assertIn("Generate an auth token", str(caught.exception))

	def test_a_disabled_mcp_system_user_is_refused(self):
		STORE.tables["User"]["mcp@example.test"]["enabled"] = 0
		with self.assertRaises(Exception) as caught:
			self.doc(require_user_context=1, mcp_system_user="mcp@example.test").save()
		self.assertIn("is disabled", str(caught.exception))

	def test_enabling_a_write_tool_warns_out_loud(self):
		self.doc(enabled=1, allow_submit_journal_entry=1).save()
		self.assertTrue(
			any(
				"submit_journal_entry" in comment.get("text", "")
				for comment in STORE.comments
				if comment.get("type") == "msgprint"
			)
		)

	def test_no_warning_when_only_read_tools_are_on(self):
		self.doc(enabled=1).save()
		self.assertFalse(
			[c for c in STORE.comments if c.get("type") == "msgprint"]
		)


class TokenGeneration(SeededTestCase):
	def test_generating_returns_the_token_once_and_stores_it(self):
		doc = frappe.get_doc("ERPNext MCP Settings")
		result = doc.generate_token()
		self.assertEqual(len(result["token"]), TOKEN_LENGTH)
		self.assertEqual(settings.auth_token(), result["token"])

	def test_the_stored_row_does_not_hold_the_plaintext(self):
		doc = frappe.get_doc("ERPNext MCP Settings")
		token = doc.generate_token()["token"]
		self.assertNotIn(token, json.dumps(STORE.singles["ERPNext MCP Settings"]))

	def test_the_new_token_authenticates_and_the_old_one_stops(self):
		old = self.TOKEN
		new = frappe.get_doc("ERPNext MCP Settings").generate_token()["token"]
		_, status = self.call("ping", token=new)
		self.assertEqual(status, 200)
		_, status = self.call("ping", token=old)
		self.assertEqual(status, 401)

	def test_it_stamps_when_the_token_was_generated(self):
		result = frappe.get_doc("ERPNext MCP Settings").generate_token()
		self.assertTrue(result["generated_on"])
		self.assertEqual(
			STORE.singles["ERPNext MCP Settings"]["token_generated_on"],
			result["generated_on"],
		)

	def test_it_tells_the_operator_it_cannot_be_shown_again(self):
		result = frappe.get_doc("ERPNext MCP Settings").generate_token()
		self.assertIn("cannot be shown again", result["note"])

	def test_it_requires_system_manager(self):
		frappe.local.session.user = "mcp@example.test"
		with self.assertRaises(Exception) as caught:
			frappe.get_doc("ERPNext MCP Settings").generate_token()
		self.assertIn("System Manager", str(caught.exception))

	def test_two_tokens_are_never_the_same(self):
		first = frappe.get_doc("ERPNext MCP Settings").generate_token()["token"]
		second = frappe.get_doc("ERPNext MCP Settings").generate_token()["token"]
		self.assertNotEqual(first, second)

	def test_an_undecryptable_token_fails_closed(self):
		"""A site restored without its encryption key must not become an open
		endpoint."""
		STORE.passwords.clear()
		self.assertEqual(settings.auth_token(), "")
		_, status = self.call("ping")
		self.assertEqual(status, 404)


class SelfTest(SeededTestCase):
	def test_it_reports_readiness_without_revealing_the_token(self):
		from erpnext_mcp import mcp

		report = mcp.selftest()
		self.assertTrue(report["ready"])
		self.assertTrue(report["token_configured"])
		self.assertNotIn(self.TOKEN, json.dumps(report))

	def test_it_lists_the_enabled_tools_and_flags_write_access(self):
		from erpnext_mcp import mcp

		self.configure(enabled=1, allow_create_journal_entry=1)
		report = mcp.selftest()
		self.assertEqual(report["mutating_tools_enabled"], ["create_journal_entry"])
		self.assertEqual(report["tools_total"], 15)
		self.assertEqual(len(report["tools_enabled"]), 11)

	def test_it_reports_not_ready_when_disabled(self):
		from erpnext_mcp import mcp

		self.configure(enabled=0)
		self.assertFalse(mcp.selftest()["ready"])

	def test_it_names_the_endpoint_the_docs_promise(self):
		from erpnext_mcp import mcp

		self.assertEqual(mcp.selftest()["endpoint"], "/api/method/erpnext_mcp.mcp.handle")

	def test_it_requires_system_manager(self):
		from erpnext_mcp import mcp

		frappe.local.session.user = "mcp@example.test"
		with self.assertRaises(Exception):
			mcp.selftest()
