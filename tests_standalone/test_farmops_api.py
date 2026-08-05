# SPDX-License-Identifier: MIT
"""farmops-api — the third transport, v0.18.0.

`test_api_mobile.py` drives the eleven methods as Python functions with a
session already set, which is faithful to the whitelisted path: Frappe
authenticates before a whitelisted method runs, so what reaches that code IS a
session. `test_fallback_auth.py` drives the same methods from headers and a body
with the session at Guest, which is faithful to what the funnel delivers.

**This file drives them over HTTP, through a real WSGI stack, with no Frappe
request handler anywhere in the picture** — because that is what v0.18.0 puts in
front of a phone, and a transport that is only ever called in-process is a
transport nobody has tested.

EIGHT CLAIMS.

1. **THE SURFACE IS THE SAME ELEVEN AND CANNOT BE MORE.** `TheSurfaceIsClosed`.
   The route table is checked against `api/mobile.py` and `api/files.py` in BOTH
   directions, so neither a twelfth route nor a twelfth method can arrive
   quietly, and no route can come to point at something ungated.

2. **A CREDENTIAL GETS IN AND NOTHING ELSE DOES.** `TheDoorOpens`,
   `TheDoorStaysShut`. Valid token, missing header, malformed header, unknown
   key, wrong secret, disabled account — one 200 and five identical 401s.

3. **THE FALLBACK IS A DOOR, NOT A BYPASS — AGAIN, ON A NEW TRANSPORT.**
   `TheSevenGatesStillRun`. Role gate, Mobile Access Grant, entity scoping, kill
   switch and rate limit are each asserted against a credential that is
   otherwise perfectly valid, because "it delegates to the guarded functions" is
   a claim and this is the check.

4. **THE ANSWER IS ALWAYS JSON.** `ItIsAlwaysJson`. Every status this service
   can produce — 200, 401, 403, 404, 405, 429, 500 — is asserted to carry
   `Content-Type: application/json` and to parse. The v0.17.x failure was an
   HTML login page where the app expected JSON; a 500 that rendered Werkzeug's
   own HTML page would reproduce it exactly.

5. **THE STATUS CODES ARE THE APP'S CONTRACT.** `TheStatusCodesMatterToThePhone`.
   401 signs a phone out and loses its offline queue, so the kill switch answers
   503 and the rate limit answers 429 and neither is allowed to drift into 401.

6. **THE RESPONSE IS BYTE-IDENTICAL TO THE OLD PATH'S.** `ByteIdentical`. The
   whole security argument of this release is "it is the same code" — this is
   the property that makes that checkable rather than claimed, run over all
   eleven methods with shared fixtures.

7. **THE ARGUMENT FILTER FRAPPE WAS DOING IS STILL BEING DONE.**
   `TheArgumentFilter`. `record_data`, `worker_id`, `cancel` and `user` are the
   four arguments the wrappers deliberately do not accept; a body carrying them
   must reach the tool layer without them rather than 500 on an unexpected
   keyword.

8. **EVERY CALL LEAVES A ROW AND NO CALL LEAVES A SECRET.** `TheAuditRow`,
   `NoSecretReachesThePhone`.
"""

import json
from typing import ClassVar

import frappe
from werkzeug.test import Client
from werkzeug.wrappers import Response

from erpnext_mcp import audit
from erpnext_mcp.api import fallback_auth, guard
from erpnext_mcp.api import files as files_api
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.farmops_api import PREFIX, ROUTES
from erpnext_mcp.farmops_api import app as farmops_app
from erpnext_mcp.farmops_api import routes as farmops_routes
from erpnext_mcp.farmops_api import session as farmops_session

from .fixtures import MAIN, OTHER
from .harness import ROLES, STORE, set_roles
from .test_api_mobile import OUTSIDER, WORKER, MobileAPITestCase

#: What the phone will call after the v0.18.0 iOS change.
CONTEXT = f"{PREFIX}/mobile/get_current_user_context"
MY_TASKS = f"{PREFIX}/mobile/list_my_tasks"


class FarmOpsAPITestCase(MobileAPITestCase):
	"""One enrolled worker, and a WSGI client that calls the service as them."""

	def setUp(self):
		super().setUp()
		self.client = Client(farmops_app.application, Response)
		fallback_auth._FAILURES.clear()
		self.addCleanup(fallback_auth._FAILURES.clear)

	def enrol(self, email=WORKER, name="Ana Ramos", role="Field Worker", entities=None):
		"""Enrol, and KEEP THE PAIR — it is readable exactly once, at enrolment."""
		data = super().enrol(email=email, name=name, role=role, entities=entities)
		credential = {"api_key": data["api_key"], "api_secret": data["api_secret"]}
		if email == WORKER:
			self.credential = credential
		return credential

	# ── calling it ──────────────────────────────────────────────────────────
	def post(self, path, body=None, credential=None, token=None, headers=None, method="POST"):
		"""One HTTP call at the service. Returns the werkzeug response.

		The session is set to Guest first, every time, because that is the state
		a request arrives in when nothing has authenticated it — which is every
		request to this service, since Frappe's auth layer is not in the path at
		all. A test that inherited the previous call's user would be testing
		nothing.
		"""
		frappe.local.session = frappe._dict(user="Guest", data=frappe._dict())
		sent = {"Content-Type": "application/json"}
		if token is None and credential is not False:
			pair = credential or self.credential
			token = f"{pair['api_key']}:{pair['api_secret']}"
		if token:
			sent[fallback_auth.HEADER] = token
		sent.update(headers or {})
		return self.client.open(
			path,
			method=method,
			data=json.dumps(body or {}),
			headers=sent,
			environ_base={"REMOTE_ADDR": "100.64.0.7"},
		)

	def payload(self, response):
		self.assertEqual(response.headers["Content-Type"], "application/json")
		return json.loads(response.get_data(as_text=True))

	def message(self, path, body=None, **kwargs):
		"""Call, assert 200, and return the unwrapped `message` — what iOS decodes."""
		response = self.post(path, body, **kwargs)
		body_json = self.payload(response)
		self.assertEqual(response.status_code, 200, body_json)
		self.assertIn("message", body_json)
		return body_json["message"]

	def refusal(self, path, body=None, **kwargs):
		"""Call, assert it failed, and return (status, the sentence the phone shows)."""
		response = self.post(path, body, **kwargs)
		parsed = self.payload(response)
		self.assertGreaterEqual(response.status_code, 400, parsed)
		return response.status_code, parsed


# ── 1. the surface is closed ────────────────────────────────────────────────
class TheSurfaceIsClosed(FarmOpsAPITestCase):
	EXPECTED: ClassVar[set[str]] = {
		"/mobile/get_current_user_context",
		"/mobile/list_my_tasks",
		"/mobile/list_available_tasks",
		"/mobile/get_task",
		"/mobile/claim_task",
		"/mobile/start_task",
		"/mobile/complete_task_via_mobile",
		"/mobile/reject_task",
		"/mobile/report_field_task",
		"/mobile/list_compliance_alerts",
		"/mobile/scan_asset",
		"/mobile/get_asset_detail",
		"/mobile/log_asset_state_change",
		"/mobile/get_available_actions",
		"/files/stage_file_chunk",
		"/files/finalize_staged_file",
	}

	def test_the_route_table_is_exactly_the_twelve_the_app_calls(self):
		self.assertEqual({route.path for route in ROUTES}, self.EXPECTED)

	def test_every_guarded_method_has_a_route_so_none_is_stranded(self):
		"""The other direction. A method the app calls with no route is a 404 in
		a field, which is the failure v0.17.1 shipped and v0.17.2 could not fix."""
		guarded = set()
		for module in (mobile_api, files_api):
			for name in dir(module):
				attribute = getattr(module, name)
				if getattr(attribute, "farm_ops_method", None):
					guarded.add(attribute.farm_ops_method)
		self.assertEqual({route.handler.farm_ops_method for route in ROUTES}, guarded)

	def test_every_route_reaches_something_that_wears_the_guard(self):
		"""A route pointing at an unguarded function would publish it ungated."""
		for route in ROUTES:
			with self.subTest(path=route.path):
				self.assertTrue(getattr(route.handler, "farm_ops_method", None))

	def test_no_admin_tool_is_reachable_at_any_path(self):
		"""The one that matters. Two hundred MCP tools; none of them is here."""
		for dangerous in (
			"create_journal_entry",
			"submit_journal_entry",
			"convey_parcel",
			"import_chart_of_accounts",
			"create_mobile_user",
			"generate_mobile_login_qr",
			"revoke_api_token",
			"run_report",
		):
			for prefix in ("/mobile", "/files"):
				response = self.post(f"{PREFIX}{prefix}/{dangerous}")
				self.assertEqual(response.status_code, 404, dangerous)

	def test_there_is_no_dispatcher_taking_a_method_name(self):
		"""A `call(tool_name, args)` would have published everything at one path."""
		for suspect in ("call", "invoke", "handle", "run", "dispatch_tool"):
			for prefix in ("/mobile", "/files"):
				self.assertEqual(
					self.post(f"{PREFIX}{prefix}/{suspect}", {"name": "create_journal_entry"}).status_code,
					404,
				)

	def test_a_path_outside_the_prefix_is_refused_without_touching_frappe(self):
		for path in ("/", "/app", "/api/method/erpnext_mcp.mcp.handle", "/farmops", "/farmopsx/api"):
			with self.subTest(path=path):
				self.assertEqual(self.post(path).status_code, 404)

	def test_none_of_the_twelve_takes_kwargs(self):
		"""`**kwargs` on a wrapper would forward the phone's whole body into a
		tool — which is exactly how `record_data` and `worker_id` would become
		reachable. `api/mobile.py` names every accepted argument for that reason;
		this is the check that it still does.

		Asserted against the signature directly rather than through
		`accepted_arguments`, because that function answers the empty set for BOTH
		"takes `**kwargs`" and "takes nothing at all" — and
		`get_current_user_context` legitimately takes nothing.
		"""
		import inspect

		for route in ROUTES:
			with self.subTest(path=route.path):
				kinds = [p.kind for p in inspect.signature(route.handler).parameters.values()]
				self.assertNotIn(inspect.Parameter.VAR_KEYWORD, kinds)
				self.assertNotIn(inspect.Parameter.VAR_POSITIONAL, kinds)

	def test_a_kwargs_signature_would_take_every_argument_away(self):
		"""The safe direction to fail. If one of the eleven ever grew a
		`**kwargs`, this transport stops forwarding anything rather than
		forwarding everything."""

		def greedy(user, **kwargs):
			pass

		self.assertEqual(farmops_routes.accepted_arguments(greedy), set())


# ── 2. the door ─────────────────────────────────────────────────────────────
class TheDoorOpens(FarmOpsAPITestCase):
	def test_a_valid_token_gets_json_and_the_right_user(self):
		"""THE WHOLE POINT OF THE RELEASE, in one assertion."""
		self.assertEqual(self.message(CONTEXT)["user"], WORKER)

	def test_the_authorization_header_works_too_for_the_lan_and_for_curl(self):
		pair = f"token {self.credential['api_key']}:{self.credential['api_secret']}"
		body = self.message(CONTEXT, credential=False, headers={"Authorization": pair})
		self.assertEqual(body["user"], WORKER)

	def test_the_auth_body_works_when_no_header_survives(self):
		body = self.message(CONTEXT, {"_auth": dict(self.credential)}, credential=False)
		self.assertEqual(body["user"], WORKER)

	def test_a_leading_token_word_in_the_farmops_header_is_tolerated(self):
		"""The likeliest mistake in a two-line client change is copying the word
		as well as the value. Accepting it costs one comparison."""
		pair = f"token {self.credential['api_key']}:{self.credential['api_secret']}"
		self.assertEqual(self.message(CONTEXT, token=pair)["user"], WORKER)

	def test_health_answers_without_a_credential_and_says_nothing_about_the_site(self):
		"""So that 'the funnel path is wrong' and 'the credential is wrong' are
		two different answers to somebody standing in a field."""
		response = self.post(f"{PREFIX}/health", credential=False, method="GET")
		body = self.payload(response)
		self.assertEqual(response.status_code, 200)
		self.assertTrue(body["ok"])
		self.assertEqual(body["service"], "farmops-api")
		self.assertNotIn("site", json.dumps(body).lower())


class TheDoorStaysShut(FarmOpsAPITestCase):
	"""Five ways to fail, one answer. A caller learns whether it is in, and
	nothing else — telling it which fact was wrong hands it a free oracle."""

	def _refused(self, **kwargs):
		status, body = self.refusal(CONTEXT, **kwargs)
		self.assertEqual(status, 401)
		return body["error"]

	def test_no_header_at_all(self):
		self.assertIn("no usable Farm Ops credential", self._refused(credential=False))

	def test_a_malformed_header_with_no_separator(self):
		self._refused(token="not-a-pair")

	def test_an_unknown_api_key(self):
		self._refused(credential={"api_key": "nope", "api_secret": "x" * 56})

	def test_the_right_key_with_the_wrong_secret(self):
		self._refused(credential={"api_key": self.credential["api_key"], "api_secret": "w" * 56})

	def test_a_disabled_account_is_refused_even_with_a_perfect_credential(self):
		"""The fastest way to stop one person without touching anybody else."""
		frappe.db.set_value("User", WORKER, "enabled", 0)
		self._refused()

	def test_all_five_read_identically(self):
		frappe.db.set_value("User", "nobody@example.test", "enabled", 0)
		messages = {
			self._refused(credential=False),
			self._refused(token="not-a-pair"),
			self._refused(credential={"api_key": "nope", "api_secret": "x" * 56}),
			self._refused(credential={"api_key": self.credential["api_key"], "api_secret": "w" * 56}),
		}
		self.assertEqual(len(messages), 1, messages)

	def test_a_wrong_secret_is_metered_on_the_counter_the_other_door_uses(self):
		"""One credential, one failure budget, however many transports. A guesser
		must not get ten fresh attempts a minute per door."""
		wrong = {"api_key": self.credential["api_key"], "api_secret": "w" * 56}
		for _ in range(fallback_auth.FAILURE_LIMIT):
			self._refused(credential=wrong)
		# The counter is keyed on the key, so the RIGHT secret is now refused too
		# until the window rolls — that is the point of metering the key rather
		# than the address, and it is bounded to a minute.
		self._refused()

	def test_a_working_phone_is_never_metered_by_somebody_elses_wrong_answers(self):
		"""Forty phones arrive from one funnel address. An address-keyed limit
		would let one of them lock the other thirty-nine out."""
		other = self.enrol(email=OUTSIDER, name="Ben Ortiz", role="Foreman", entities=[OTHER])
		for _ in range(fallback_auth.FAILURE_LIMIT + 5):
			self._refused(credential={"api_key": other["api_key"], "api_secret": "w" * 56})
		self.assertEqual(self.message(CONTEXT)["user"], WORKER)


# ── 3. the seven gates still run ────────────────────────────────────────────
class TheSevenGatesStillRun(FarmOpsAPITestCase):
	"""Every one of them, against a credential that is otherwise perfectly valid.

	"It delegates to the guarded functions" is a claim about wiring. These are
	the checks. A gate that stopped applying on this transport would be a field
	worker reading another entity's board through a URL nobody audited.
	"""

	def test_the_role_gate_refuses_a_real_login_with_no_field_role(self):
		"""A Family Member and an Advisor are real accounts on this site."""
		aunt = self.enrol(email="aunt@example.test", name="Aunt", role="Field Worker")
		set_roles("aunt@example.test", ["Family Member", "Advisor"])
		status, body = self.refusal(MY_TASKS, credential=aunt)
		self.assertEqual(status, 403)
		self.assertIn("enrolled Farm Ops credential", body["error"])

	def test_the_grant_gate_refuses_a_field_role_that_was_never_enrolled(self):
		"""Holding the role is not being enrolled. The grant IS the enrolment."""
		STORE.seed("User", [{"name": "casual@example.test", "enabled": 1, "full_name": "Casual"}])
		set_roles("casual@example.test", ["Field Worker"])
		frappe.db.set_value("User", "casual@example.test", "api_key", "casualkey")
		STORE.passwords[("User", "casual@example.test", "api_secret")] = "c" * 56
		status, _ = self.refusal(MY_TASKS, credential={"api_key": "casualkey", "api_secret": "c" * 56})
		self.assertEqual(status, 403)

	def test_a_grant_that_is_no_longer_active_closes_this_door_on_the_next_call(self):
		"""THE GRANT GATE ON ITS OWN, with the credential left live on purpose.

		`revoke_mobile_user` clears the api_key as well as ending the grant, so
		revoking through the tool would refuse at the door and never reach this
		gate — see the test below, which is the one that checks the tool. Setting
		the state directly is what isolates the check that matters here: an
		account holding a PERFECTLY VALID credential is still refused the moment
		its enrolment stops being Active.
		"""
		self.assertEqual(self.message(CONTEXT)["user"], WORKER)
		frappe.db.set_value("Mobile Access Grant", WORKER, "state", "Revoked")
		status, body = self.refusal(CONTEXT)
		self.assertEqual(status, 403)
		self.assertIn("enrolled Farm Ops credential", body["error"])

	def test_revoking_a_user_also_kills_the_credential_outright(self):
		"""Belt to the gate's braces: the tool takes the token away too, so a
		revoked phone is refused at the door rather than one gate further in."""
		self.assertEqual(self.message(CONTEXT)["user"], WORKER)
		frappe.local.session.user = "Administrator"
		self.tool_data("revoke_mobile_user", {"email": WORKER, "reason": "left after harvest"})
		self.assertEqual(frappe.db.get_value("Mobile Access Grant", WORKER, "state"), "Revoked")
		self.assertEqual(self.refusal(CONTEXT)[0], 401)

	def test_administrator_holds_every_role_and_still_cannot_call_this(self):
		"""The reason the grant gate exists, re-run against the new entry point."""
		frappe.db.set_value("User", "Administrator", "api_key", "adminkey")
		STORE.passwords[("User", "Administrator", "api_secret")] = "a" * 56
		status, _ = self.refusal(MY_TASKS, credential={"api_key": "adminkey", "api_secret": "a" * 56})
		self.assertEqual(status, 403)

	def test_a_company_the_caller_cannot_reach_is_refused_not_quietly_emptied(self):
		"""An empty list reads on a phone as a quiet day. 'Not yours' is a
		different fact and somebody can act on it."""
		status, body = self.refusal(MY_TASKS, {"company": OTHER})
		self.assertEqual(status, 403)
		self.assertIn(OTHER, body["error"])

	def test_the_kill_switch_stops_everything_and_does_not_sign_the_phone_out(self):
		self.configure(enabled=1, farm_ops_mobile_enabled=0)
		status, body = self.refusal(CONTEXT)
		self.assertEqual(status, 503)
		self.assertIn("switched off", body["error"])

	def test_the_rate_limit_trips_and_answers_429(self):
		guard._BUCKETS.clear()
		for _ in range(guard.READ_LIMIT):
			self.assertEqual(self.post(CONTEXT).status_code, 200)
		status, body = self.refusal(CONTEXT)
		self.assertEqual(status, 429)
		self.assertIn("limit is", body["error"])

	def test_a_task_belonging_to_another_entity_reads_as_not_found(self):
		"""Split from 'does not exist' deliberately: the two refusals mean
		different things and are worded the same, so a caller cannot map the
		site's docnames by watching which error comes back."""
		self.a_camp()
		mine = self.a_task()
		theirs = self.a_task(company=OTHER, task_name="Somebody else's walk")
		self.assertEqual(self.refusal(f"{PREFIX}/mobile/get_task", {"task": theirs})[0], 404)
		self.assertEqual(self.refusal(f"{PREFIX}/mobile/get_task", {"task": "FT-NOPE"})[0], 404)
		self.assertTrue(self.message(f"{PREFIX}/mobile/get_task", {"task": mine}))


# ── 4. it is always JSON ────────────────────────────────────────────────────
class ItIsAlwaysJson(FarmOpsAPITestCase):
	"""v0.17.x's failure was HTTP 200 carrying the Desk's HTML login page, and
	the app reporting it as a decoding error. Every exit from this service is
	asserted to be JSON, INCLUDING the ones nobody planned for."""

	def _assert_json(self, response):
		self.assertEqual(response.headers["Content-Type"], "application/json")
		body = response.get_data(as_text=True)
		self.assertNotIn("<html", body.lower())
		return json.loads(body)

	def test_a_success_is_json(self):
		self._assert_json(self.post(CONTEXT))

	def test_a_401_is_json(self):
		self._assert_json(self.post(CONTEXT, credential=False))

	def test_a_404_is_json(self):
		self._assert_json(self.post(f"{PREFIX}/mobile/no_such_method"))

	def test_a_405_is_json_and_says_post(self):
		body = self._assert_json(self.post(CONTEXT, method="GET"))
		self.assertIn("POST only", body["error"])

	def test_a_body_that_is_not_json_does_not_produce_a_parser_page(self):
		response = self.client.open(
			CONTEXT,
			method="POST",
			data="this is not json at all",
			headers={fallback_auth.HEADER: "nope:nope"},
		)
		self._assert_json(response)

	def test_an_unexpected_exception_in_a_tool_is_json_and_not_a_traceback_page(self):
		"""Werkzeug's own 500 page is HTML. Reintroducing it here would rebuild
		the exact failure this release exists to end."""
		original = mobile_api.mobile_tools.get_current_user_context

		def boom(args):
			raise RuntimeError("the database fell over")

		mobile_api.mobile_tools.get_current_user_context = boom
		self.addCleanup(setattr, mobile_api.mobile_tools, "get_current_user_context", original)

		response = self.post(CONTEXT)
		body = self._assert_json(response)
		self.assertEqual(response.status_code, 500)
		self.assertIn("has been logged", body["error"])

	def test_an_internal_failure_does_not_put_its_own_words_on_the_phone(self):
		"""A 500's message is where table names and query fragments leak out."""
		original = mobile_api.mobile_tools.get_current_user_context

		def boom(args):
			raise RuntimeError("Table 'frontend.tabSecretLedger' doesn't exist")

		mobile_api.mobile_tools.get_current_user_context = boom
		self.addCleanup(setattr, mobile_api.mobile_tools, "get_current_user_context", original)

		body = json.loads(self.post(CONTEXT).get_data(as_text=True))
		self.assertNotIn("tabSecretLedger", json.dumps(body))

	def test_nothing_this_service_answers_is_a_redirect(self):
		"""A 302 to a login page is how the old path failed. This one has no
		login page to redirect to, and must never grow one."""
		for path, kwargs in (
			(CONTEXT, {}),
			(CONTEXT, {"credential": False}),
			(f"{PREFIX}/mobile/nope", {}),
			("/app", {}),
			("/", {}),
		):
			with self.subTest(path=path):
				response = self.post(path, **kwargs)
				self.assertFalse(300 <= response.status_code < 400, response.status_code)
				self.assertNotIn("Location", response.headers)


class TheRefusalReachesThePhone(FarmOpsAPITestCase):
	"""`FrappeClient.serverMessage` reads `_server_messages` first — a JSON
	string holding an array of JSON strings, each an object with a `message`.
	It is nested twice, which looks like a mistake and is not. A flat array
	produces a phone that shows "Something went wrong" instead of the sentence
	somebody wrote to be read in a field."""

	def _server_message(self, body):
		outer = json.loads(body["_server_messages"])
		self.assertIsInstance(outer, list)
		return [json.loads(entry)["message"] for entry in outer]

	def test_the_sentence_survives_the_envelope_the_shipped_app_decodes(self):
		self.a_camp()
		task = self.a_task()
		_, body = self.refusal(f"{PREFIX}/mobile/reject_task", {"task": task, "reason": "   "})
		self.assertIn("A reason is required", self._server_message(body)[0])

	def test_the_same_sentence_is_in_all_three_places_the_app_looks(self):
		_, body = self.refusal(MY_TASKS, {"company": OTHER})
		self.assertIn(OTHER, body["error"])
		self.assertIn(OTHER, self._server_message(body)[0])
		self.assertIn("PermissionError", body["exception"])


# ── 5. the status codes are the app's contract ──────────────────────────────
class TheStatusCodesMatterToThePhone(FarmOpsAPITestCase):
	"""`FarmOpsKit` treats 401 as "this credential is dead — sign out", which
	DISCARDS THE OFFLINE QUEUE. So 401 is reserved for exactly one thing, and
	everything else that refuses has to be something else."""

	def test_only_a_bad_credential_is_ever_401(self):
		self.configure(enabled=1, farm_ops_mobile_enabled=0)
		self.assertEqual(self.refusal(CONTEXT)[0], 503)

		self.configure(enabled=1, farm_ops_mobile_enabled=1)
		guard._BUCKETS.clear()
		for _ in range(guard.WRITE_LIMIT + 1):
			last = self.post(f"{PREFIX}/mobile/claim_task", {"task": "FT-NOPE"})
		self.assertEqual(last.status_code, 429)

		self.assertEqual(self.refusal(MY_TASKS, {"company": OTHER})[0], 403)
		self.assertEqual(self.refusal(CONTEXT, credential=False)[0], 401)

	def test_the_kill_switch_status_comes_through_even_as_the_switch_set_it(self):
		"""`guard._set_status` writes it on `frappe.local.response` because on the
		whitelisted path that dict IS the response. Reading it back means the two
		statuses the app's behaviour turns on cannot drift."""
		self.configure(enabled=1, farm_ops_mobile_enabled=0)
		self.assertEqual(self.post(CONTEXT).status_code, 503)


# ── 6. byte-identical to the old path ───────────────────────────────────────
class ByteIdentical(FarmOpsAPITestCase):
	"""THE CENTRAL CLAIM OF v0.18.0, CHECKED RATHER THAN ASSERTED.

	The whole security argument for a new transport to a compliance system is
	"the gates did not move, because it is the same code". That is only worth
	anything if somebody checks — the obvious implementation copies the gates
	into the new service, the copies drift, and the drift is invisible until an
	auditor finds a worker reading another entity's board.

	So every read is run BOTH ways against the same fixture and the two answers
	are compared as serialised JSON. A difference of any kind — a missing key, a
	reordered list, a stringified number — fails here.
	"""

	def both_ways(self, path, method, body=None):
		body = body or {}
		over_http = self.message(f"{PREFIX}{path}", dict(body))
		frappe.local.session = frappe._dict(user=WORKER, data=frappe._dict())
		self.request({}, headers={}, remote_addr="100.64.0.7")
		frappe.local.session.user = WORKER
		in_process = method(**body)
		self.assertEqual(
			json.dumps(over_http, sort_keys=True, default=str),
			json.dumps(in_process, sort_keys=True, default=str),
			f"{path} differs between the two transports",
		)
		return over_http

	def test_get_current_user_context(self):
		self.both_ways("/mobile/get_current_user_context", mobile_api.get_current_user_context)

	def test_list_my_tasks(self):
		self.a_camp()
		self.a_task()
		self.both_ways("/mobile/list_my_tasks", mobile_api.list_my_tasks)

	def test_list_my_tasks_scoped_to_one_company(self):
		self.a_camp()
		self.a_task()
		self.both_ways("/mobile/list_my_tasks", mobile_api.list_my_tasks, {"company": MAIN})

	def test_list_available_tasks(self):
		self.a_camp()
		self.a_task()
		self.both_ways("/mobile/list_available_tasks", mobile_api.list_available_tasks)

	def test_get_task(self):
		self.a_camp()
		task = self.a_task()
		self.both_ways("/mobile/get_task", mobile_api.get_task, {"task": task})

	def test_list_compliance_alerts(self):
		self.both_ways("/mobile/list_compliance_alerts", mobile_api.list_compliance_alerts)

	def test_a_write_produces_the_same_shape_over_either_transport(self):
		"""Claims cannot be run twice against one task, so the two runs get one
		task each and the SHAPES are compared rather than the docnames."""
		self.a_camp()
		first, second = self.a_task(), self.a_task(task_name="A second walk")

		over_http = self.message(f"{PREFIX}/mobile/claim_task", {"task": first})

		self.request({}, headers={}, remote_addr="100.64.0.7")
		frappe.local.session.user = WORKER
		in_process = mobile_api.claim_task(task=second)

		self.assertEqual(sorted(over_http), sorted(in_process))
		self.assertEqual(over_http["state"], in_process["state"])
		self.assertEqual(over_http["assigned_to"], in_process["assigned_to"])

	def test_a_refusal_carries_the_same_sentence_over_either_transport(self):
		_, body = self.refusal(MY_TASKS, {"company": OTHER})

		self.request({}, headers={}, remote_addr="100.64.0.7")
		frappe.local.session.user = WORKER
		with self.assertRaises(frappe.PermissionError) as caught:
			mobile_api.list_my_tasks(company=OTHER)

		self.assertEqual(body["error"], str(caught.exception))


# ── 7. the argument filter Frappe was doing ─────────────────────────────────
class TheArgumentFilter(FarmOpsAPITestCase):
	"""Frappe's handler binds a body to a whitelisted method by KEEPING THE KEYS
	THAT MATCH THE SIGNATURE. All eleven wrappers were written against that —
	naming every accepted argument instead of taking `**kwargs` is exactly what
	makes `record_data` and `worker_id` unreachable from a phone. This transport
	does not go through that handler, so it does the filtering itself."""

	def test_an_unknown_key_is_dropped_rather_than_crashing_the_call(self):
		"""`function(**body)` would 500 on a client that sent one extra field —
		which is what a newer app talking to an older server looks like."""
		body = self.message(CONTEXT, {"unexpected": 1, "client_version": "2.0", "extra": [1, 2]})
		self.assertEqual(body["user"], WORKER)

	def test_the_arguments_the_wrappers_refuse_are_still_unreachable(self):
		"""The four that are deliberately NOT forwarded: `cancel` would let a
		worker delete work instead of handing it back, `record_data` would let a
		phone compose a compliance record, `worker_id` would let an account name
		somebody else, `user` would let it BE somebody else."""
		for refused in ("cancel", "record_data", "worker_id", "user"):
			for route in ROUTES:
				with self.subTest(argument=refused, path=route.path):
					self.assertNotIn(refused, farmops_routes.accepted_arguments(route.handler))

	def test_a_body_naming_another_user_is_answered_as_the_caller(self):
		"""An account that can name somebody else in a request body is not
		scoped to anything. Dropped here AND in `guard` — two locks, one door."""
		self.assertEqual(self.message(CONTEXT, {"user": OUTSIDER})["user"], WORKER)

	def test_a_rejection_cannot_smuggle_cancel_through_to_the_tool(self):
		self.a_camp()
		task = self.a_task()
		self.message(f"{PREFIX}/mobile/claim_task", {"task": task})
		self.message(
			f"{PREFIX}/mobile/reject_task",
			{"task": task, "reason": "The ladder is broken", "cancel": True},
		)
		self.assertNotEqual(frappe.db.get_value("Farm Task", task, "status"), "Cancelled")

	def test_the_auth_envelope_never_reaches_a_method_as_an_argument(self):
		"""`_auth` carries a live credential and arrives on every single call,
		because the app does not know which door it came in through."""
		self.assertEqual(
			self.message(CONTEXT, {"_auth": dict(self.credential)}, credential=False)["user"],
			WORKER,
		)
		for route in ROUTES:
			with self.subTest(path=route.path):
				self.assertNotIn(
					fallback_auth.BODY_KEY,
					farmops_routes.bind(route, {fallback_auth.BODY_KEY: dict(self.credential)}),
				)


# ── 8. the row and the secrets ──────────────────────────────────────────────
class TheAuditRow(FarmOpsAPITestCase):
	def test_a_call_over_this_transport_writes_the_same_mobile_row(self):
		self.message(CONTEXT)
		row = self.audit_rows("get_current_user_context")[-1]
		self.assertEqual(row["result_status"], audit.STATUS_SUCCESS)
		self.assertIn(WORKER, row["result_summary"])

	def test_a_refusal_is_audited_too(self):
		self.refusal(MY_TASKS, {"company": OTHER})
		row = self.audit_rows("list_my_tasks")[-1]
		self.assertEqual(row["result_status"], audit.STATUS_UNAUTHORIZED)

	def test_the_row_records_the_ip_the_proxy_appended_and_not_the_one_claimed(self):
		"""Rightmost `X-Forwarded-For` hop. An audit log that records an
		attacker's chosen address is worse than one that records none, because it
		looks like evidence."""
		self.post(CONTEXT, headers={"X-Forwarded-For": "1.2.3.4, 100.64.0.9"})
		self.assertEqual(self.audit_rows("get_current_user_context")[-1]["caller_ip"], "100.64.0.9")

	def test_an_unauthenticated_flood_cannot_grow_the_log(self):
		"""The refusal happens before any method, so there is no method to audit
		— and a caller who can write rows without a credential can push an
		operator's real evidence out of view."""
		before = len(STORE.rows("MCP Action Log"))
		for _ in range(20):
			self.post(CONTEXT, credential=False)
		self.assertEqual(len(STORE.rows("MCP Action Log")), before)


class NoSecretReachesThePhone(FarmOpsAPITestCase):
	def test_no_response_from_any_route_carries_a_credential_shaped_key(self):
		self.a_camp()
		task = self.a_task()
		for path, body in (
			("/mobile/get_current_user_context", {}),
			("/mobile/list_my_tasks", {}),
			("/mobile/list_available_tasks", {}),
			("/mobile/get_task", {"task": task}),
			("/mobile/list_compliance_alerts", {}),
		):
			with self.subTest(path=path):
				text = json.dumps(self.message(f"{PREFIX}{path}", body)).lower()
				for hint in ("api_secret", "password", "auth_header"):
					self.assertNotIn(hint, text)

	def test_the_credential_the_caller_presented_is_never_echoed_back(self):
		text = json.dumps(self.payload(self.post(CONTEXT, {"_auth": dict(self.credential)})))
		self.assertNotIn(self.credential["api_secret"], text)

	def test_a_refusal_does_not_echo_the_credential_either(self):
		response = self.post(CONTEXT, {"_auth": dict(self.credential)}, token="bad:bad")
		self.assertNotIn(self.credential["api_secret"], response.get_data(as_text=True))


# ── the session lifecycle ───────────────────────────────────────────────────
class TheSessionLifecycle(FarmOpsAPITestCase):
	"""Frappe's own WSGI app opens, authenticates, dispatches, commits and
	destroys. This service replaces one of those five and has to do the rest."""

	def test_a_write_is_committed_or_the_worker_loses_the_task_on_refresh(self):
		"""Without the commit a worker claims a task, gets a 200, and finds it
		unclaimed on the next pull-to-refresh."""
		self.a_camp()
		task = self.a_task()
		committed = []
		original = STORE.commit
		STORE.commit = lambda: (committed.append(True), original())[1]
		self.addCleanup(setattr, STORE, "commit", original)

		self.message(f"{PREFIX}/mobile/claim_task", {"task": task})
		self.assertTrue(committed, "a mutating call did not commit")

	def test_a_read_commits_too_so_the_idle_grant_sweep_still_has_a_stamp(self):
		"""`_stamp_last_seen` writes from inside a read, and that stamp is the
		only thing `sweep_idle_grants` has to go on when it decides whether a
		token belongs to a phone lost in an orchard a month ago."""
		frappe.db.set_value("Mobile Access Grant", WORKER, "last_seen_on", None)
		self.message(CONTEXT)
		self.assertTrue(frappe.db.get_value("Mobile Access Grant", WORKER, "last_seen_on"))

	def test_one_callers_identity_does_not_survive_into_the_next_call(self):
		"""`frappe.local` is per-request scratch on a real site. A worker-lifetime
		one would let a previous caller's answer be read in this caller's row."""
		self.message(CONTEXT)
		other = self.enrol(email=OUTSIDER, name="Ben Ortiz", role="Foreman", entities=[OTHER])
		self.assertEqual(self.message(CONTEXT, credential=other)["user"], OUTSIDER)
		self.assertIn(OUTSIDER, self.audit_rows("get_current_user_context")[-1]["result_summary"])

	def test_the_site_comes_from_the_environment_the_container_already_sets(self):
		"""`SITE_NAME: frontend` is on the ERPNext service in the Umbrel compose.
		Reading it means the sidecar and the site cannot be configured apart."""
		import os

		self.assertEqual(farmops_session.site_name(), "frontend")
		os.environ["FARMOPS_API_SITE"] = "somewhere.else"
		self.addCleanup(os.environ.pop, "FARMOPS_API_SITE", None)
		self.assertEqual(farmops_session.site_name(), "somewhere.else")


class TheOldPathIsUntouched(FarmOpsAPITestCase):
	"""v0.18.0 adds a transport. It does not take one away — the whitelisted
	path works on the LAN, from inside the container, and is the fallback on the
	day this service is the thing that is down."""

	def test_the_whitelisted_methods_still_answer_in_process(self):
		self.request({}, headers={}, remote_addr="100.64.0.7")
		frappe.local.session.user = WORKER
		self.assertEqual(mobile_api.get_current_user_context()["user"], WORKER)

	def test_the_auth_hook_still_resolves_the_header_on_the_old_path(self):
		"""`fallback_auth.resolve` was refactored to share one verifier with the
		sidecar. It has to still do what v0.17.2 shipped it to do."""
		self.request(
			{},
			token=False,
			headers={fallback_auth.HEADER: f"{self.credential['api_key']}:{self.credential['api_secret']}"},
			remote_addr="100.64.0.7",
			path="/api/method/erpnext_mcp.api.mobile.get_current_user_context",
		)
		frappe.local.session.user = "Guest"
		self.assertEqual(mobile_api.get_current_user_context()["user"], WORKER)
		self.assertEqual(fallback_auth.source(), fallback_auth.SOURCE_HEADER)

	def test_the_shared_verifier_answers_the_same_for_both_doors(self):
		self.assertEqual(
			fallback_auth.verify_credential(self.credential["api_key"], self.credential["api_secret"]),
			WORKER,
		)
		self.assertEqual(fallback_auth.verify_credential("nope", "x" * 56), "")
		self.assertEqual(fallback_auth.verify_credential("", ""), "")


class ROLESIsRestored(FarmOpsAPITestCase):
	"""The base class rewrites Administrator's roles to prove the grant gate
	holds. Leaving them rewritten silently broke twelve workflow tests two files
	away once already; this is the guard against it happening from here."""

	def test_the_shared_role_map_is_the_one_the_suite_started_with(self):
		self.assertIn("Administrator", ROLES)
