# SPDX-License-Identifier: MIT
"""JSON-RPC and MCP handshake behaviour."""

import json

from erpnext_mcp import protocol, registry

from .fixtures import SeededTestCase
from .harness import STORE


class Handshake(SeededTestCase):
	def test_initialize_echoes_a_supported_version(self):
		for version in protocol.SUPPORTED_PROTOCOL_VERSIONS:
			body, _ = self.call("initialize", {"protocolVersion": version})
			self.assertEqual(body["result"]["protocolVersion"], version)

	def test_initialize_answers_with_its_preferred_version_when_unknown(self):
		body, _ = self.call("initialize", {"protocolVersion": "1999-01-01"})
		self.assertEqual(body["result"]["protocolVersion"], protocol.PREFERRED_PROTOCOL_VERSION)

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

	def test_the_app_version_matches_the_changelog(self):
		"""v0.2.0 tagged and shipped with `__version__` still reading "0.1.0", so
		every client's handshake reported the wrong server version. Comparing the
		two things a release has to keep in step costs one test."""
		import pathlib
		import re

		from erpnext_mcp import __version__

		changelog = pathlib.Path(__file__).resolve().parents[1] / "CHANGELOG.md"
		latest = re.search(r"^## (\d+\.\d+\.\d+)", changelog.read_text(), re.M)
		self.assertIsNotNone(latest, "no version heading in CHANGELOG.md")
		self.assertEqual(
			__version__,
			latest.group(1),
			"erpnext_mcp.__version__ and the newest CHANGELOG heading disagree",
		)


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
	def test_lists_every_available_tool_when_all_are_enabled(self):
		self.configure(**{f"allow_{name}": 1 for name in registry.TOOLS}, enabled=1)
		body, _ = self.call("tools/list")
		self.assertEqual(
			sorted(tool["name"] for tool in body["result"]["tools"]),
			sorted(name for name in registry.TOOLS if registry.is_available(name)),
		)

	def test_default_catalogue_is_every_available_read_tool_plus_the_one_installer(self):
		"""Straight out of the box: everything readable, and one thing writable.

		The one is `install_compliance_fields`, which ships enabled — the single
		exception to "mutating tools default off", named and argued for in
		`registry.DEFAULT_ON_MUTATING_TOOLS`. It is asserted here EXPLICITLY rather
		than filtered out, because the catalogue a fresh install advertises is the
		thing this test exists to pin down, and a second default-on write tool
		should break it.
		"""
		body, _ = self.call("tools/list")
		names = sorted(tool["name"] for tool in body["result"]["tools"])
		expected = sorted(
			[name for name in registry.READ_TOOLS if registry.is_available(name)]
			+ [
				name
				for name in registry.DEFAULT_ON_MUTATING_TOOLS
				if registry.is_available(name)
			]
		)
		self.assertEqual(names, expected)
		self.assertEqual(
			set(names) & set(registry.MUTATING_TOOLS),
			{"install_compliance_fields"},
		)

	def test_hr_tools_are_absent_without_the_hrms_app(self):
		"""A tool that can never work here is not a tool that fails — it is a tool
		that does not exist, and the catalogue should say so."""
		self.configure(**{f"allow_{name}": 1 for name in registry.TOOLS}, enabled=1)
		body, _ = self.call("tools/list")
		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertNotIn("get_leave_balance", names)
		self.assertNotIn("list_employees", names)

	def test_hr_tools_appear_once_hrms_is_installed(self):
		STORE.installed_apps.append("hrms")
		self.configure(**{f"allow_{name}": 1 for name in registry.TOOLS}, enabled=1)
		body, _ = self.call("tools/list")
		names = {tool["name"] for tool in body["result"]["tools"]}
		self.assertIn("get_leave_balance", names)
		self.assertIn("get_attendance_summary", names)

	def test_an_unavailable_tool_says_it_cannot_be_switched_on(self):
		message = self.tool_error("list_employees")
		self.assertIn("not available on this site", message)
		self.assertIn("hrms", message)
		self.assertIn("not something an operator can switch on", message)

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

	def test_catalogue_is_two_hundred_ten_tools_ninety_three_read_one_hundred_seventeen_write(self):
		"""v0.13.0 added two writes: convey_parcel and update_journal_entry_party,
		both corrections to records that already exist, which is why neither is a
		read. v0.14.0 added eight — six writes and two reads.

		The six writes: three that move a file onto the site a piece at a time
		(stage_file_chunk, commit_staged_file, cancel_staged_upload),
		bulk_wire_default_accounts, create_check_print_format, and
		regenerate_governance_document_pdf. The two reads are the ones you call
		when something has gone wrong and you need to see rather than change it:
		list_staged_uploads for an upload that died partway, and
		investigate_je_gl_link for a voucher and a ledger that disagree.

		v0.15.0 ADDED THIRTY-TWO, and they are the compliance framework:

		  * two for the fields that go ON the operational doctypes —
		    get_compliance_field_map reads the table, install_compliance_fields
		    writes the columns;
		  * nineteen over the four external-evidence doctypes (Compliance Policy,
		    Certification, Regulatory Filing, Audit Event), eight of which are
		    reads;
		  * seven for the kairotic compliance calendar —
		    get_compliance_calendar, list_compliance_rules and get_audit_readiness
		    read it, refresh_compliance_alerts rebuilds it, and snooze_alert,
		    dismiss_alert and dismiss_alert_bulk are the three different ways
		    something comes off it;
		  * two for audit packets, one of which assembles a PDF and files it;
		  * two for the Journal Entry attribution drift v0.13.0 left behind —
		    find_drifted_je_attributions reads it, repair_drifted_je_attributions
		    fixes it in a batch.

		v0.16.0 ADDED TWENTY-THREE, and they are the operational half of the same
		framework — the half that lets somebody be SENT to fix what Sprint 7 could
		only report:

		  * eleven for Farm Task Dispatch — four reads (the pool, one worker's
		    load, the board, one task in full), six writes for the state machine
		    (create, assign, claim, start, complete, reject), and
		    generate_tasks_from_compliance_alerts, which is the bridge that turns
		    open alerts into dispatchable work;
		  * twelve over the three compliance records a completion produces
		    (Housing Inspection, Detector Test, Water Test) — list, get, create
		    and update apiece, six of which are reads.

		v0.17.0 ADDED SIXTEEN, and they are what makes a phone outside the LAN a
		safe thing to point at all of the above:

		  * three for mobile accounts — list_mobile_users reads the roster and
		    every way it has drifted, create_mobile_user and revoke_mobile_user
		    write it. The role says what kind of work; the Company User
		    Permissions this app writes say whose;
		  * four for the credential — generate_api_token and revoke_api_token
		    mint and destroy it, generate_mobile_login_qr puts it on a scannable
		    card, and get_current_user_context is what the phone calls first to
		    find out who it is;
		  * two for the transport, both READ, and there is deliberately no third
		    that flips it: validate_public_endpoint asks from outside whether the
		    Funnel is up and the token gate is holding, get_tailscale_funnel_config
		    asks from inside what this machine is serving;
		  * seven mobile-ergonomic wrappers over Sprint 8's dispatch tools — four
		    reads and three writes — which add the worker resolved from the
		    authenticated request, a screen-shaped payload, and NOTHING ELSE. Every
		    refusal in them comes from the tool underneath, because it IS the tool
		    underneath.

		v0.17.1 ADDED ONE, and only one, because a hotfix that grew the catalogue
		would be a release nobody could review. `onboard_employee` is an
		ORCHESTRATOR over tools that already exist — the Employee, the paperwork
		through attach_file_to_document, the login through create_mobile_user, the
		first-day tasks through create_farm_task. It adds no rule any of them does
		not already enforce. Its whole reason to exist is that the paperwork has to
		land ON THE EMPLOYEE RECORD and not in the governance archive, and a
		checklist cannot make that mistake impossible.

		v0.18.1 ADDED THREE, ALL WRITES, AND THEY CLOSE THE GAP v0.18.0 OPENED. The
		mobile app worked end to end and then `list_my_tasks` refused every account
		— correctly — because the Farm Ops methods scope work by EMPLOYEE and this
		app could not create, edit or link one. It could make the User, the role,
		the entity scoping, the grant, the credential and the QR: six things, and
		not the one that makes the other six useful.

		  * `create_employee` writes fourteen identity and assignment fields and
		    refuses everything else by name — payroll, tax and banking with their
		    own message, because each has a form, an approval and a retention rule
		    this app knows nothing about;
		  * `update_employee` changes the same fourteen on a record that exists, and
		    reports field by field what actually moved;
		  * `link_employee_to_user` sets the one field that turns a working
		    credential into a working task board, and reports whether the phone will
		    NOW work rather than merely whether the field was written.

		`onboard_employee` grew no siblings — it gained the link step it was
		missing, and delegates its creation to `create_employee`, so there is still
		exactly one implementation of what an Employee record may contain.
		"""
		self.assertEqual(len(registry.TOOLS), 210)
		self.assertEqual(len(registry.READ_TOOLS), 93)
		self.assertEqual(len(registry.MUTATING_TOOLS), 117)

	def test_every_tool_declares_why_it_might_be_unavailable(self):
		"""A predicate with no `requires` sentence produces a refusal that says
		nothing a caller can act on."""
		for name, spec in registry.TOOLS.items():
			with self.subTest(tool=name):
				if spec["available"] is not registry._always:
					self.assertTrue(spec["requires"], f"{name} has a predicate but no reason")
