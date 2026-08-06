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
			+ [name for name in registry.DEFAULT_ON_MUTATING_TOOLS if registry.is_available(name)]
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

	def test_catalogue_is_three_hundred_twenty_six_tools_one_hundred_fifty_one_read_one_hundred_seventy_five_write(
		self,
	):
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

		v0.19.0 ADDED FOUR — two reads, two writes — and they are the first pull
		from the HR roadmap. Eleven compliance rules watched certificates,
		policies, cabins, water, filings and audits, and not one of them watched
		TRAINING: what WPS asks for every twelve months, what Oregon's heat rule
		asks for annually before the first hot shift, what FSMA Subpart C asks for
		on hiring and periodically, and what a GAP auditor asks for by name with
		the signature attached. All of it lived in a binder.

		  * `record_training` files one event tagged with every regime it answers,
		    because one afternoon in a shed can satisfy four audits and filing it
		    four times produces four records that disagree by August;
		  * `list_trainings` filters by regime, which is how an audit packet is
		    assembled, and reports the §112.161 elements each record is missing;
		  * `get_training` answers the retention question — five years where any
		    tag is NOP, three for OR-OSHA, two for FSMA and WPS, longest governs —
		    with the citation beside the number;
		  * `sign_training_supervisor_review` is a SEPARATE write because
		    §112.161(b) asks for a review "after the record is made", and it is
		    the requirement USDA GAP does not have and FDA cites most.

		The twelfth compliance rule (`training_expiring`) and the training section
		on every audit packet came with them and are not tools, which is why the
		catalogue grew by four and the release by considerably more.

		v0.19.3 ADDED TEN — six writes and four reads — and they are one workflow
		with one actor. Compliance anchors to a SHIFT rather than to a task,
		because Oregon OSHA does not ask what the temperature was when one job
		closed, it asks whether the July 15 shift complied with OAR 437-004-1131
		from start to finish, and only a record spanning the exposure period can
		answer that.

		  * `start_shift` forms the crew at a place and starts the period.
		    THE FOREMAN FORMS IT and there is deliberately no clock-in tool: the
		    rule puts the water, shade, rest-cycle and observation obligations on
		    a NAMED responsible person, and a crew of thirty each clocking
		    themselves in is a shift with nobody responsible for the record;
		  * `add_worker_to_shift` and `remove_worker_from_shift` amend it. The
		    second SETS `left_at` rather than deleting the row, because the row is
		    the only record that this person was on the shift at all — which is
		    what a wage claim turns on;
		  * `log_shift_event` records what the foreman did about the conditions,
		    at the moment it happened. The timeline is the evidence; the heat
		    record is only the claim;
		  * `end_shift` closes it with a signature that is REQUIRED — an unsigned
		    close is an UPDATE setting a timestamp, and §112.161(b) asks for a
		    review dated AND signed — and writes one Attendance record per crew
		    member for that person's OWN span;
		  * `create_heat_exposure_event` is the -1131 record, one per shift,
		    checked against the training register as of the day of the shift;
		  * `list_shifts`, `get_shift`, `list_heat_exposure_events` and
		    `get_heat_exposure_event` read it all back.

		The thirteenth compliance rule (`supervisor_review_lapsed`), the
		Attendance bridge and the four new doctypes came with them and are not
		tools, which is again why the catalogue grew by ten and the release by
		considerably more.

		v0.19.4 ADDED FIVE — two writes and three reads — and they are the hands
		to a mechanism that is mostly a schedule. A fifteen-minute cron documents
		every open shift without anybody asking, because a timeline is only
		evidence if it was written while things were happening. What the cron
		cannot do is the rest:

		  * `fetch_weather_now` is for the foreman standing in a block on a day
		    that turned, who wants the conditions on the record this minute
		    rather than in eleven. It bypasses the cache, which is the point;
		  * `backfill_weather_for_shift` documents every shift that ran before
		    the service was switched on, from Open-Meteo's archive, idempotently
		    and at the archive's own hourly granularity;
		  * `list_shifts_missing_weather` is the worklist for the second one;
		  * `get_weather_timeline` answers 'how hot was it when the break was
		    called' without returning the whole shift;
		  * `get_weather_settings` reads the thresholds back — and there is no
		    write counterpart on purpose, because a model that could raise the
		    heat threshold past anything Oregon produces would leave a site that
		    behaves normally and never says anything is wrong.

		The Threshold Crossed events, the Heat Exposure Event maxima computed off
		the timeline, the compliance-event snapshots and the Weather Settings
		doctype came with them and are not tools — which is why the catalogue grew
		by five and the release by considerably more.

		v0.19.5 ADDED SIX — four writes and two reads — and they are the first
		tools in this run that no regulator asked for. Sustainable CF/Acre is
		(normalized operating cash flow − maintenance capex) ÷ productive acres,
		and it exists because headline OCF lies in two directions at once: it is
		flattered by money that will not come in again, and flattered AGAIN by
		maintenance that was not done.

		  * `create_normalization_adjustment` proposes one add-back or
		    subtraction and CREATES A DRAFT, always. Nothing in it can make the
		    adjustment count. That is the whole compliance posture: finding a
		    non-recurring item in a ledger nobody reads line by line is worth a
		    great deal and is something a model is good at, and deciding that it
		    will not recur is a judgement with a lender on the other end of it;
		  * `approve_normalization_adjustment` is the human half, with a
		    signature that has no bypass and an approval timestamp WRITTEN rather
		    than taken as input — an approval date somebody can set is one they
		    can set to before the quarter closed;
		  * `reject_normalization_adjustment` refuses one on the record with the
		    reason attached, and the rejection is KEPT, because a register with
		    only its successes in it says nothing about how hard they were to get;
		  * `backfill_asset_capex_type` classifies the history in bulk, dry-run by
		    default, never overwriting a classification somebody made — so a
		    second run finds nothing to do;
		  * `list_normalization_adjustments` reads the register and says which
		    rows actually count;
		  * `get_sustainable_cf_per_acre` returns the KPI WITH EVERY INGREDIENT
		    ITEMIZED, because a normalized figure nobody can inspect is
		    indistinguishable from an arranged one.

		The doctype, the four Asset capex columns, the three Field productive-date
		columns, the direct-method cash flow service and the quarterly dashboard
		chart came with them and are not tools — which is why the catalogue grew
		by six and the release by considerably more.

		v0.19.6 ADDED THREE — two reads and one write — and retrofitted a fourth
		without adding to the count. The release is the WINDOW STANDARD: every
		financial report now defaults to a trailing twelve months, because
		agricultural revenue is aggressively seasonal and two single periods set
		against each other say the operation collapsed in January and recovered
		in September, every year, on every farm.

		  * `get_windowed_report` is the generic entry point and the reason the
		    standard generalizes — a report registered in
		    services/financial_reports.py is reachable through it without another
		    tool, another switch and another catalogue section. A framework whose
		    every KPI costs a tool is a framework with six KPIs in it;
		  * `list_financial_kpi_history` reads the precomputed cache as a plain
		    series, for drawing a line, and reports what is NOT there — a gap is
		    a window nobody has computed, not a period the business earned
		    nothing in;
		  * `recompute_kpi_history` rebuilds that cache, and is the mildest
		    mutating tool in the catalogue: every row it writes is derivable and
		    every row it deletes comes back, so the worst outcome of running it
		    at the wrong moment is time spent;
		  * `get_sustainable_cf_per_acre` is the RETROFIT and adds nothing to the
		    count. It now defaults to TTM; passing v0.19.5's period_start and
		    period_end still returns v0.19.5's exact payload, because that figure
		    is quoted in packs that were sent before the window existed.

		The Financial KPI History doctype, the windowed-report engine, the two
		demonstration computers, the overnight sweep and the retrofitted chart
		came with them and are not tools — which is why the catalogue grew by
		three and the release by considerably more.

		v0.20.1 ADDED EXACTLY ONE, and it is a read:

		  * `list_visits` groups completed task assignments into the trips their
		    handsets recorded. Five cabins closed on one walk is one visit and
		    five completions, and the grouping is the phone's rather than a guess
		    from how close the timestamps are — no threshold gets both an
		    unhurried walk and two fast jobs at opposite ends of a property right.

		THE REST OF THAT RELEASE IS NOT A TOOL AND THAT IS THE POINT OF IT.
		`complete_task_via_mobile` became idempotent — a resubmission whose
		signature matches the completion already on record returns it with
		`x_idempotent: true` instead of a hard error — because a client cannot
		know whether its request landed before the connection dropped, and an
		iPad in an orchard proved it by showing three Failed entries per task on
		work that had been filed the first time. A fix that arrived as a NEW
		tool would have left the broken one in the catalogue for every client
		already calling it. The two new columns on Farm Task Assignment
		(`completion_signature`, `visit_id`) and the backfill patch came with it
		and are not tools either.

		v0.21.0 ADDED TEN — four reads and six writes — and the whole of the
		release is one claim: THE SHAPE OF A VISIT IS DATA. An Inspection Template
		says which sections a worker works through in one trip to one place, what
		evidence each needs and which compliance record each produces, and it is a
		ROW. Adding one is not a release.

		  * `create_inspection_template` authors one, live on the next fetch;
		  * `update_inspection_template` changes one by SUPERSEDING it — a new row
		    at version+1, the old one deactivated and pointing at it, never
		    edited. That is what makes a session from April readable in November,
		    and why a session started against v1 while v2 is being authored is
		    unaffected;
		  * `deactivate_inspection_template` withdraws one with a reason and
		    destroys nothing. There is deliberately no delete;
		  * `start_inspection_session` opens one visit and PINS the version;
		  * `submit_inspection_session` is the one with teeth: it files every
		    section against the pinned version's contract and writes the
		    compliance records the sections promise — separately, at their own
		    cadences, from one walk with one signature;
		  * `list_inspection_templates`, `get_inspection_template`,
		    `list_inspection_sessions` and `get_inspection_session` read it all
		    back. The second is what a handset renders a sectioned form from,
		    which is why a new template needs no app update;
		  * `propose_inspection_template_from_regulation` is DECLARED AND REFUSES.
		    It is the surface an AI template proposer will occupy, reserved now so
		    the shape is fixed before anything fills it, and inert now because at
		    runtime this app is deterministic. It counts as a tool because it has
		    a switch, a schema and a catalogue entry — everything a tool has
		    except an implementation — and a surface an operator cannot see in the
		    settings form is a surface nobody can refuse.

		The five doctypes, the Farm Task link, the four seeded templates and the
		rule engine's bundling — several overdue things at one cabin become ONE
		visit rather than three trips — came with them and are not tools, which is
		why the catalogue grew by ten and the release by considerably more.

		v0.22.0 ADDED SEVEN — two reads and five writes — and the release is the
		same claim as v0.21.0's, made about the thing underneath it: THE RULES
		THEMSELVES ARE DATA. A compliance rule used to be a Python function, so
		moving a threshold, correcting a citation or switching a rule off for a
		season was a code change, a release and a deploy. Regulations do not move
		on a release cadence — OR-OSHA renumbered heat illness from -1130 to
		-1131 — and now neither does this.

		  * `create_compliance_rule` authors one. It arrives as a DRAFT and fires
		    nothing, because the approval gate is not a default anybody can pass
		    past;
		  * `approve_compliance_rule` is the only way a rule can be enabled — the
		    DocType refuses `enabled` without an approver and a date — so "a model
		    wrote a rule and it started firing" is a sentence that cannot be true
		    about this app;
		  * `update_compliance_rule` changes one by SUPERSEDING it, exactly as a
		    template is superseded: a new row at version+1, the old one disabled
		    and pointing at it, never edited. A sweep that started against v1
		    finishes against v1, and an alert from April is still explicable in
		    November;
		  * `deactivate_compliance_rule` switches one off with a reason and
		    DISMISSES NOTHING — every alert it raised stays exactly as it was,
		    because switching a rule off is not evidence that anybody did the
		    work. There is deliberately no delete;
		  * `test_compliance_rule` is the read between authoring and approving:
		    it runs the rule down the same code path the sweep takes and reports
		    what it WOULD raise, writing nothing;
		  * `get_compliance_rule` reads one definition in full, including a
		    superseded one — which is how the rule an old alert was raised under
		    stays inspectable;
		  * `propose_compliance_rule` is DECLARED AND REFUSES, for the same
		    reasons and on the same terms as v0.21.0's template proposer. AI
		    belongs at authoring time behind a human approval and never in the
		    trigger path, and a surface an operator cannot see in the settings
		    form is a surface nobody can refuse.

		`list_compliance_rules` was RETROFITTED rather than replaced: it now reads
		the records and takes filters, and every key it returned before means what
		it always meant. The Compliance Rule DocType, the declarative evaluator,
		the restricted-Python sandbox and the migration of the thirteen shipped
		rules into records came with them and are not tools, which is why the
		catalogue grew by seven and the release by a great deal more.

		v0.25.0 added three for asset state-change actions: get_available_actions
		and list_asset_state_history are reads, log_asset_state_change is a write.

		v0.26.0 added one mutating tool: report_asset_issue.

		v0.27.0 ADDED FOURTEEN — eight reads and six writes — for the
		structured I-9 workflow: get_i9_settings, get_i9_form, list_i9_forms,
		list_pending_i9_verifications, get_i9_audit_log, list_i9_document_types,
		get_i9_retention_report and list_expiring_work_authorizations read;
		create_i9_form, submit_i9_section_1, submit_i9_section_2,
		update_i9_settings, flag_i9_reverification and destroy_i9 write.

		v0.28.0 ADDED TEN — seven reads and three writes — for the
		W-4 / Federal Withholding Engine: get_w4, list_w4_forms, get_fica_config,
		get_federal_tax_table, preview_federal_withholding,
		list_employees_missing_w4 and calculate_payroll_taxes read;
		submit_w4, update_fica_config and import_federal_tax_table write.

		v0.29.0 ADDED NINE — six reads and three writes — for the
		State Tax Engines (Oregon + Washington): get_state_tax_config,
		list_state_tax_configs, get_state_tax_table, preview_state_withholding,
		preview_total_payroll_taxes and list_employees_by_work_state read;
		create_state_tax_config, update_state_tax_config and
		import_state_tax_table write.

		v0.30.0 ADDED NINE — five reads and four writes — for salary structures
		and the payroll engine: get_salary_structure, list_salary_structures,
		preview_payroll, get_payroll_entry and list_payroll_entries read;
		create_salary_structure, deactivate_salary_structure, calculate_payroll
		and submit_payroll write.

		v0.31.0 ADDED FIVE — two reads and three writes — for expense receipt
		capture: list_expense_receipts and get_expense_receipt read;
		submit_expense_receipt, approve_expense_receipt and
		reject_expense_receipt write. Approval and rejection are two tools rather
		than one verdict argument because they are two switches an operator sets
		independently, and that is a difference a single tool cannot express.

		v0.32.0 ADDED THREE — one read and two writes — for geography and crew
		tracking: get_shift_track reads; log_shift_location and
		set_parcel_boundary write. The last of those closes a gap
		set_field_boundary had been apologising for since v0.12.0, in a warning
		on every single call: a parcel had no boundary, so nothing checked that
		the block sat inside its parcel.

		v0.34.0 ADDED FIVE — two reads and three writes — for the tax form
		generators: list_tax_forms and get_tax_form read; generate_tax_form,
		regenerate_tax_form and mark_tax_form_filed write. `regenerate` is a
		separate tool from `generate` rather than an argument on it because
		recomputing a form REPLACES what an employer told an agency, and a
		switch an operator can leave off is the only honest way to express
		that.

		THE THREE NUMBERS BELOW WERE RED FOR A RELEASE. v0.30.0 shipped its nine
		tools without touching them, so this test, `test_read_tools.py`'s copy of
		the read count and the three in `test_tool_catalog_count.py` all failed on
		main until v0.31.0. The counts are cheap to update and the failure is
		loud; what it cost was the signal — six red tests that everybody had
		learned to expect are six tests that cannot tell you about the seventh.
		"""
		self.assertEqual(len(registry.TOOLS), 326)
		self.assertEqual(len(registry.READ_TOOLS), 151)
		self.assertEqual(len(registry.MUTATING_TOOLS), 175)

	def test_every_tool_declares_why_it_might_be_unavailable(self):
		"""A predicate with no `requires` sentence produces a refusal that says
		nothing a caller can act on."""
		for name, spec in registry.TOOLS.items():
			with self.subTest(tool=name):
				if spec["available"] is not registry._always:
					self.assertTrue(spec["requires"], f"{name} has a predicate but no reason")
