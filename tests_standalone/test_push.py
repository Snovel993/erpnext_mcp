# SPDX-License-Identifier: MIT
"""v0.99.0 — the break horn that reaches the crew rather than the foreman.

`BreakAlarm` on the handset plays a tone the instant a foreman calls a break,
over an audio session that rings through the silent switch. That is one phone.
Every other worker on the shift learned about the break when somebody shouted.
This is the other twenty phones, and these are the seven claims it rests on.

1. **THE DEVICE IS THE IDENTITY AND THE TOKEN IS NOT.** `TheDeviceIsTheIdentity`.
   Apple rotates a device token whenever the app is reinstalled or the phone is
   restored, and iOS calls `register_push_token` on every launch. A register
   that appended would hold a season of dead rows per handset, each one a wasted
   request on every crew push. Same device_id and platform is the same row.

2. **NOTHING IS DELETED.** `RetiringAHandset`. Logout clears `is_active` and
   keeps the row, because "this phone stopped receiving, on this date, for this
   reason" is the fact somebody needs when a worker reports a silent handset.

3. **A PHONE ENROLS ITSELF.** `TheSurfaceEnrolsOnlyItsOwnCaller`. Neither mobile
   wrapper declares `user` or `employee`, so `routes.bind` drops both and no
   body can point a registration at a colleague and have their break horns
   delivered to a handset of its choosing. Asserted by INSPECTING the signature,
   which is the only form of that claim a later edit cannot quietly falsify.

4. **AN UNCONFIGURED SITE LOGS THE BREAK AND SENDS NOTHING.**
   `WhatTheSiteHasBeenTold` and `SendingAndFailing`. The p8 key is an operator
   artefact that does not exist yet; the break record is compliance evidence
   under OAR 437-004-1131. The first must never be able to cost the second.

5. **APPLE'S WORD RETIRES A TOKEN, AND NOTHING ELSE DOES.** `SendingAndFailing`.
   `Unregistered` and `BadDeviceToken` deactivate the row. `TopicDisallowed` and
   the 5xxs deliberately do not — they say something about this farm's
   configuration, and treating them alike would unsubscribe a whole crew over
   one wrong line in site_config.json.

6. **A CREW BREAK PUSHES; AN INDIVIDUAL BREAK DOES NOT.**
   `TheBreakHornReachesTheCrew`. A break covering one named worker is not news
   to the other nineteen, and a tone that rings through a silent switch is not a
   thing to send to somebody it is not about.

7. **THE TWO ADMIN TOOLS ANSWER "WHY IS THEIR PHONE QUIET".** `TheAdminTools`.
   The register, with the site's own APNs state beside it — because the
   commonest answer to that question is not on any row.
"""

import base64
import json
import os
import tempfile
import unittest
from unittest import mock

import frappe

from erpnext_mcp.api import guard
from erpnext_mcp.api import mobile as mobile_api
from erpnext_mcp.erpnext_mcp.doctype.mobile_push_token import mobile_push_token as token_doctype
from erpnext_mcp.errors import ToolError
from erpnext_mcp.farmops_api import routes as farmops_routes
from erpnext_mcp.services import push as push_service
from erpnext_mcp.tools import push as push_tools

from .fixtures import V12TestCase, install_hrms
from .harness import ROLES, STORE, set_roles
from .test_api_mobile import ON as MOBILE_ON
from .test_api_mobile import WORKER, WORKER_EMPLOYEE, MobileAPITestCase

PUSH_TOKEN = "Mobile Push Token"

FOREMAN = "HR-EMP-00001"  # Ada Orchard, at MAIN
PICKER = "HR-EMP-00002"  # Ben Packhouse, at MAIN

ADA = "ada@example.test"
BEN = "ben.push@example.test"

ON = {
	f"allow_{name}": 1
	for name in (
		"list_push_tokens",
		"send_test_push",
		"start_shift",
		"add_worker_to_shift",
		"remove_worker_from_shift",
		"log_shift_break",
		"end_shift_break",
		"get_shift",
	)
}


#: A real ES256 key, generated once per run. THE SIGNING IS NOT MOCKED: a
#: provider token is the one piece of this pipeline that fails silently and
#: identically whether the key is wrong, the curve is wrong, or the signature is
#: DER where Apple wanted raw r||s — and the last of those produces a perfectly
#: well-formed JWT that Apple rejects with InvalidProviderToken. A test that
#: stubbed `provider_token` would pass with that bug in place.
def _test_key() -> str:
	from cryptography.hazmat.primitives import serialization
	from cryptography.hazmat.primitives.asymmetric import ec

	key = ec.generate_private_key(ec.SECP256R1())
	return key.private_bytes(
		serialization.Encoding.PEM,
		serialization.PrivateFormat.PKCS8,
		serialization.NoEncryption(),
	).decode("ascii")


try:
	TEST_KEY = _test_key()
except Exception:  # pragma: no cover - a bench without cryptography
	TEST_KEY = ""


def _unb64(segment: str) -> bytes:
	"""One base64url JWT segment, with the padding a JWT strips put back."""
	return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def apns_conf(**overrides) -> dict:
	conf = {
		"apns_key": TEST_KEY,
		"apns_key_id": "ABC1234567",
		"apns_team_id": "TEAM123456",
		"apns_topic": "farm.fafo.scanclock",
		"apns_environment": "sandbox",
	}
	conf.update(overrides)
	return conf


class Recorder:
	"""A transport that answers whatever it is told to and remembers every call.

	The seam the module's docstring argues for: the decisions worth testing —
	which tokens are addressed, what a 410 does, whether an unconfigured site
	even gets this far — are all above the network and unreachable behind a real
	HTTP client no test may call.
	"""

	def __init__(self, status=200, reason=""):
		self.status = status
		self.reason = reason
		self.calls = []

	def __call__(self, url, headers, body, timeout=10.0):
		self.calls.append({"url": url, "headers": headers, "body": body})
		if callable(self.status):
			return self.status(url, headers, body)
		return {"status": self.status, "reason": self.reason}


class PushTestCase(V12TestCase):
	"""A site with two workers, two logins, and the switches this suite drives."""

	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		STORE.seed(
			"User",
			[
				{"name": ADA, "email": ADA, "enabled": 1, "full_name": "Ada Orchard"},
				{"name": BEN, "email": BEN, "enabled": 1, "full_name": "Ben Packhouse"},
			],
		)
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	# -- helpers -------------------------------------------------------------
	def register(self, **overrides):
		payload = {
			"user": ADA,
			"employee": FOREMAN,
			"token": "AAAA1111",
			"device_id": "DEVICE-ADA",
			"platform": "ios",
		}
		payload.update(overrides)
		return push_tools.register_push_token(payload).data

	def rows(self) -> list:
		return [dict(row) for row in STORE.rows(PUSH_TOKEN)]

	def row(self, name: str) -> dict:
		return dict(STORE.get_raw(PUSH_TOKEN, name) or {})


# ── 1. the upsert ───────────────────────────────────────────────────────────
class TheDeviceIsTheIdentity(PushTestCase):
	def test_a_first_registration_creates_one_row(self):
		answer = self.register()
		self.assertTrue(answer["created"])
		self.assertFalse(answer["token_rotated"])
		self.assertEqual(len(self.rows()), 1)

		stored = answer["push_token"]
		self.assertEqual(stored["user"], ADA)
		self.assertEqual(stored["employee"], FOREMAN)
		self.assertEqual(stored["device_key"], "ios::DEVICE-ADA")
		self.assertEqual(stored["is_active"], 1)
		self.assertTrue(stored["registered_at"])
		self.assertTrue(stored["last_used_at"])

	def test_the_same_device_launching_again_updates_the_row_it_already_has(self):
		"""The claim the whole doctype is shaped around. iOS calls this on every
		launch; a register that appended would have a season of rows per phone."""
		first = self.register()
		second = self.register()

		self.assertTrue(first["created"])
		self.assertFalse(second["created"])
		self.assertEqual(len(self.rows()), 1)
		self.assertEqual(second["push_token"]["name"], first["push_token"]["name"])

	def test_a_rotated_token_is_written_and_reported(self):
		self.register(token="AAAA1111")
		answer = self.register(token="BBBB2222")

		self.assertTrue(answer["token_rotated"])
		self.assertEqual(answer["push_token"]["token"], "BBBB2222")
		self.assertEqual(len(self.rows()), 1)

	def test_the_same_token_presented_twice_is_not_a_rotation(self):
		"""The negative control for the assertion above: `token_rotated` has to
		mean something, and a flag that was true on every launch would not."""
		self.register(token="AAAA1111")
		self.assertFalse(self.register(token="AAAA1111")["token_rotated"])

	def test_one_device_id_on_two_platforms_is_two_rows(self):
		"""The key is the PAIR. An Android and an iOS handset are entitled to
		report the same identifier, and a key of device_id alone would have one
		of them overwriting the other's token forever."""
		self.register(device_id="SHARED", platform="ios")
		self.register(device_id="SHARED", platform="android", token="ANDROID-TOK")

		self.assertEqual(len(self.rows()), 2)
		self.assertEqual(
			sorted(row["device_key"] for row in self.rows()),
			["android::SHARED", "ios::SHARED"],
		)

	def test_platform_is_normalised_so_one_phone_is_one_row(self):
		"""A client sending `iOS` and one sending `ios` are the same handset."""
		self.register(platform="ios")
		self.register(platform="iOS", token="SECOND")

		self.assertEqual(len(self.rows()), 1)
		self.assertEqual(self.rows()[0]["token"], "SECOND")

	def test_a_launch_that_carries_no_employee_does_not_erase_the_link(self):
		"""A registration arriving before the Employee record was linked must not
		blank a link a previous one established: an employee-less token is a token
		no crew push will ever reach."""
		self.register(employee=FOREMAN)
		answer = self.register(employee=None, token="LATER")

		self.assertEqual(answer["push_token"]["employee"], FOREMAN)

	def test_re_registering_reactivates_a_handset_that_had_logged_out(self):
		"""Logging back in IS the consent to be pushed to — the app asks the OS
		for permission and the server never sees the answer, so a handset calling
		this method is by definition one the worker has signed into."""
		self.register()
		push_tools.unregister_push_token({"user": ADA, "device_id": "DEVICE-ADA", "platform": "ios"})
		self.assertEqual(self.rows()[0]["is_active"], 0)

		answer = self.register()
		self.assertEqual(answer["push_token"]["is_active"], 1)
		self.assertEqual(len(self.rows()), 1)

	def test_an_unknown_platform_is_refused(self):
		with self.assertRaises(ToolError) as caught:
			self.register(platform="blackberry")
		self.assertIn("platform must be one of", str(caught.exception))
		self.assertEqual(self.rows(), [])

	def test_a_device_id_longer_than_the_cap_is_refused(self):
		with self.assertRaises(ToolError) as caught:
			self.register(device_id="x" * (token_doctype.MAX_DEVICE_ID + 1))
		self.assertIn("maximum is", str(caught.exception))
		self.assertEqual(self.rows(), [])

	def test_a_registration_with_no_token_is_refused(self):
		with self.assertRaises(ToolError):
			self.register(token="")
		self.assertEqual(self.rows(), [])


# ── 2. the soft delete ──────────────────────────────────────────────────────
class RetiringAHandset(PushTestCase):
	def test_logout_clears_the_flag_and_keeps_the_row(self):
		self.register()
		answer = push_tools.unregister_push_token(
			{"user": ADA, "device_id": "DEVICE-ADA", "platform": "ios"}
		).data

		self.assertTrue(answer["deactivated"])
		self.assertTrue(answer["found"])
		self.assertEqual(len(self.rows()), 1)
		stored = self.rows()[0]
		self.assertEqual(stored["is_active"], 0)
		self.assertEqual(stored["last_error"], "unregistered")

	def test_a_retired_handset_is_not_sent_to(self):
		self.register()
		push_tools.unregister_push_token({"user": ADA, "device_id": "DEVICE-ADA", "platform": "ios"})
		self.assertEqual(push_service.active_tokens_for_employees([FOREMAN]), [])

	def test_a_device_this_site_has_never_seen_is_answered_rather_than_refused(self):
		"""A phone logging out on a bad signal before its registration landed is a
		normal thing to happen, and an error dialog on a screen the worker is
		already leaving helps nobody."""
		answer = push_tools.unregister_push_token(
			{"user": ADA, "device_id": "NEVER-SEEN", "platform": "ios"}
		).data
		self.assertFalse(answer["found"])
		self.assertFalse(answer["deactivated"])

	def test_one_login_cannot_retire_another_login_s_handset(self):
		"""Without this, a caller who guessed a device key could take a colleague
		off every crew push for the rest of the season."""
		self.register(user=ADA, device_id="DEVICE-ADA")
		with self.assertRaises(ToolError) as caught:
			push_tools.unregister_push_token({"user": BEN, "device_id": "DEVICE-ADA", "platform": "ios"})
		self.assertIn("registered to another account", str(caught.exception))
		self.assertEqual(self.rows()[0]["is_active"], 1)

	def test_logging_out_twice_is_not_an_error(self):
		self.register()
		push_tools.unregister_push_token({"user": ADA, "device_id": "DEVICE-ADA", "platform": "ios"})
		again = push_tools.unregister_push_token(
			{"user": ADA, "device_id": "DEVICE-ADA", "platform": "ios"}
		).data
		self.assertTrue(again["was_already_inactive"])
		self.assertEqual(len(self.rows()), 1)


# ── 3. what the site has been told ──────────────────────────────────────────
class WhatTheSiteHasBeenTold(unittest.TestCase):
	"""`apns_config` is pure and takes its conf as an argument, so this class
	needs no site at all."""

	def test_a_bench_with_nothing_configured_is_not_configured(self):
		config = push_service.apns_config({})
		self.assertFalse(config["configured"])
		self.assertEqual(config["environment"], "production")

	def test_all_four_facts_are_required_and_each_one_alone_is_not_enough(self):
		"""ANDed rather than one flag an operator could tick while leaving the key
		out. A push missing any of the four is rejected by Apple with a 403 that
		says nothing useful, which is a worse failure than not sending."""
		self.assertTrue(push_service.apns_config(apns_conf())["configured"])
		for missing in ("apns_key", "apns_key_id", "apns_team_id", "apns_topic"):
			with self.subTest(missing=missing):
				self.assertFalse(push_service.apns_config(apns_conf(**{missing: ""}))["configured"])

	def test_the_key_may_be_a_path_on_the_bench(self):
		handle = tempfile.NamedTemporaryFile("w", suffix=".p8", delete=False)
		handle.write(TEST_KEY)
		handle.close()
		self.addCleanup(os.unlink, handle.name)

		config = push_service.apns_config(apns_conf(apns_key=handle.name))
		self.assertTrue(config["configured"])
		self.assertIn("PRIVATE KEY", config["key"])

	def test_a_path_that_cannot_be_read_leaves_the_site_unconfigured(self):
		"""Rather than a signing failure at push time, which would surface in the
		middle of a break instead of when somebody was looking at the config."""
		config = push_service.apns_config(apns_conf(apns_key="/nonexistent/apns.p8"))
		self.assertFalse(config["configured"])

	def test_the_environment_picks_the_host_and_a_typo_falls_back(self):
		self.assertEqual(
			push_service.apns_config(apns_conf(apns_environment="sandbox"))["host"],
			"https://api.sandbox.push.apple.com",
		)
		self.assertEqual(
			push_service.apns_config(apns_conf(apns_environment="staging"))["host"],
			"https://api.push.apple.com",
		)


# ── 4. the payload the handset plays ────────────────────────────────────────
class ThePayloadTheHandsetPlays(unittest.TestCase):
	def test_a_break_starting_names_the_start_tone(self):
		payload = push_service.break_payload("Paid Rest", "start", 10, "SHIFT-0001", "EV-1")
		self.assertEqual(payload["aps"]["sound"], "break_start.caf")
		self.assertEqual(payload["aps"]["interruption-level"], "time-sensitive")
		self.assertIn("10 minutes", payload["aps"]["alert"]["body"])

	def test_a_break_ending_names_the_end_tone(self):
		payload = push_service.break_payload("Paid Rest", "end", 10, "SHIFT-0001", "EV-1")
		self.assertEqual(payload["aps"]["sound"], "break_end.caf")
		self.assertEqual(payload["phase"], "end")

	def test_the_two_tones_are_never_the_same_file(self):
		"""They are a rising double blast and a descending triple pip on purpose:
		a worker in an orchard has to know which one they just heard without
		taking the phone out of a pocket."""
		self.assertNotEqual(push_service.SOUND_BREAK_START, push_service.SOUND_BREAK_END)

	def test_the_custom_keys_sit_beside_aps_and_not_inside_it(self):
		"""Apple owns `aps` and will one day add a key this app already uses."""
		payload = push_service.break_payload("Cool-Down", "start", 15, "SHIFT-0001", "EV-9")
		self.assertEqual(payload["break_kind"], "Cool-Down")
		self.assertEqual(payload["shift"], "SHIFT-0001")
		self.assertEqual(payload["event"], "EV-9")
		for key in ("break_kind", "shift", "event", "phase"):
			self.assertNotIn(key, payload["aps"])

	def test_a_break_with_no_duration_still_produces_a_sentence(self):
		body = push_service.break_payload("Water Break", "start")["aps"]["alert"]["body"]
		self.assertIn("Water Break", body)
		self.assertNotIn("None", body)


# ── 5. the dispatch ─────────────────────────────────────────────────────────
class SendingAndFailing(PushTestCase):
	def tokens(self) -> list:
		self.register(user=ADA, employee=FOREMAN, device_id="DEV-ADA", token="TOK-ADA")
		self.register(user=BEN, employee=PICKER, device_id="DEV-BEN", token="TOK-BEN")
		return push_service.active_tokens_for_employees([FOREMAN, PICKER])

	def test_one_request_per_handset_at_the_configured_host(self):
		transport = Recorder()
		report = push_service.send_push(self.tokens(), {"aps": {}}, transport=transport, conf=apns_conf())

		self.assertEqual(report["sent"], 2)
		self.assertEqual(report["failed"], 0)
		self.assertEqual(report["reason"], "sent")
		self.assertEqual(
			sorted(call["url"] for call in transport.calls),
			[
				"https://api.sandbox.push.apple.com/3/device/TOK-ADA",
				"https://api.sandbox.push.apple.com/3/device/TOK-BEN",
			],
		)

	def test_the_headers_carry_the_topic_a_provider_token_and_the_urgent_priority(self):
		transport = Recorder()
		push_service.send_push(self.tokens(), {"aps": {}}, transport=transport, conf=apns_conf())

		headers = transport.calls[0]["headers"]
		self.assertEqual(headers["apns-topic"], "farm.fafo.scanclock")
		self.assertEqual(headers["apns-push-type"], "alert")
		# 10 is "deliver now". A break horn that arrives when the phone next
		# wakes up is not a break horn.
		self.assertEqual(headers["apns-priority"], "10")
		self.assertTrue(headers["authorization"].startswith("bearer "))

		header, claims, signature = headers["authorization"][len("bearer ") :].split(".")
		self.assertEqual(json.loads(_unb64(header)), {"alg": "ES256", "kid": "ABC1234567"})
		self.assertEqual(json.loads(_unb64(claims))["iss"], "TEAM123456")
		# THE SIGNATURE IS 64 RAW BYTES — r||s — AND NOT DER. `cryptography`
		# signs to DER, and handing Apple the DER blob produces a perfectly
		# well-formed JWT that it rejects with InvalidProviderToken: every push
		# fails, with nothing on this side to show for it. This assertion is the
		# only place that difference is visible without an Apple account.
		self.assertEqual(len(_unb64(signature)), 64)

	def test_an_unconfigured_site_never_reaches_the_transport(self):
		"""THE NEGATIVE CONTROL FOR THE WHOLE FEATURE. Until Tim puts a p8 key on
		the bench this is the only path that runs, and it has to be a named skip
		rather than an exception, a hang, or a silent zero."""
		transport = Recorder()
		report = push_service.send_push(self.tokens(), {"aps": {}}, transport=transport, conf={})

		self.assertEqual(report["reason"], "not_configured")
		self.assertEqual(report["skipped"], 2)
		self.assertEqual(report["sent"], 0)
		self.assertEqual(transport.calls, [])

	def test_apple_saying_unregistered_retires_that_handset(self):
		tokens = self.tokens()
		transport = Recorder(status=410, reason="Unregistered")
		report = push_service.send_push(tokens, {"aps": {}}, transport=transport, conf=apns_conf())

		self.assertEqual(report["deactivated"], 2)
		self.assertTrue(all(row["is_active"] == 0 for row in self.rows()))
		self.assertTrue(all(row["last_error"] == "Unregistered" for row in self.rows()))

	def test_a_bad_device_token_retires_that_handset_too(self):
		tokens = self.tokens()
		push_service.send_push(
			tokens, {"aps": {}}, transport=Recorder(status=400, reason="BadDeviceToken"), conf=apns_conf()
		)
		self.assertTrue(all(row["is_active"] == 0 for row in self.rows()))

	def test_a_configuration_failure_does_not_unsubscribe_the_crew(self):
		"""THE COUNTERPART TO THE TWO ABOVE, and the one that matters most.
		`TopicDisallowed` says the bundle id in site_config.json is wrong — it is
		true of every handset on the farm, and deactivating on it would silence
		the whole crew over one line an operator can fix in a minute."""
		tokens = self.tokens()
		report = push_service.send_push(
			tokens, {"aps": {}}, transport=Recorder(status=400, reason="TopicDisallowed"), conf=apns_conf()
		)

		self.assertEqual(report["failed"], 2)
		self.assertEqual(report["deactivated"], 0)
		self.assertTrue(all(row["is_active"] == 1 for row in self.rows()))

	def test_a_transport_that_raises_is_a_failed_send_and_not_an_exception(self):
		def explode(url, headers, body, timeout=10.0):
			raise RuntimeError("connection reset")

		report = push_service.send_push(self.tokens(), {"aps": {}}, transport=explode, conf=apns_conf())
		self.assertEqual(report["failed"], 2)
		self.assertEqual(report["sent"], 0)

	def test_a_row_with_no_token_string_is_skipped_rather_than_addressed(self):
		self.register(user=ADA, employee=FOREMAN, device_id="DEV-ADA", token="TOK-ADA")
		frappe.db.set_value(PUSH_TOKEN, self.rows()[0]["name"], "token", "")
		transport = Recorder()
		report = push_service.send_push(
			push_service.active_tokens_for_employees([FOREMAN]),
			{"aps": {}},
			transport=transport,
			conf=apns_conf(),
		)
		self.assertEqual(report["skipped"], 1)
		self.assertEqual(transport.calls, [])

	def test_no_tokens_at_all_says_so(self):
		report = push_service.send_push([], {"aps": {}}, transport=Recorder(), conf=apns_conf())
		self.assertEqual(report["reason"], "no_tokens")
		self.assertEqual(report["attempted"], 0)


# ── 6. who hears it ─────────────────────────────────────────────────────────
class WhoHearsIt(PushTestCase):
	def a_shift(self, crew=(FOREMAN, PICKER)) -> str:
		return self.tool_data(
			"start_shift",
			{
				"foreman": FOREMAN,
				"location": "Block 7 North",
				"shift_type": "Harvest",
				"start_datetime": f"{frappe.utils.today()} 06:00:00",
				"crew_employees": list(crew),
			},
		)["name"]

	def test_the_crew_is_read_through_the_shift_document(self):
		shift = self.a_shift()
		self.assertEqual(sorted(push_service.shift_crew_employees(shift)), sorted([FOREMAN, PICKER]))

	def test_a_worker_who_clocked_out_is_not_sent_to(self):
		"""Their phone is elsewhere, and a tone that rings through the silent
		switch is not a thing to send to somebody who went home."""
		shift = self.a_shift()
		self.tool_data("remove_worker_from_shift", {"shift": shift, "employee": PICKER})
		self.assertEqual(push_service.shift_crew_employees(shift), [FOREMAN])

	def test_an_unknown_shift_is_nobody_rather_than_an_exception(self):
		self.assertEqual(push_service.shift_crew_employees("SHIFT-NOPE"), [])
		self.assertEqual(push_service.shift_crew_employees(""), [])

	def test_an_empty_crew_issues_no_query_at_all(self):
		"""THE NEGATIVE CONTROL. Frappe reads `{"in": []}` as no filter at all on
		some backends, which would turn a crew of nobody into a push to the whole
		farm — the single worst outcome this module could produce."""
		with mock.patch.object(frappe.db, "get_all") as queried:
			self.assertEqual(push_service.active_tokens_for_employees([]), [])
			self.assertEqual(push_service.active_tokens_for_employees(["", "  "]), [])
		queried.assert_not_called()

	def test_only_active_tokens_are_returned(self):
		self.register(user=ADA, employee=FOREMAN, device_id="LIVE", token="LIVE-TOK")
		self.register(user=ADA, employee=FOREMAN, device_id="DEAD", token="DEAD-TOK")
		push_tools.unregister_push_token({"user": ADA, "device_id": "DEAD", "platform": "ios"})

		found = push_service.active_tokens_for_employees([FOREMAN])
		self.assertEqual([row["device_id"] for row in found], ["LIVE"])


# ── 7. the trigger ──────────────────────────────────────────────────────────
class TheBreakHornReachesTheCrew(PushTestCase):
	def setUp(self):
		super().setUp()
		self.transport = Recorder()
		patched = mock.patch.object(push_service, "_apns_transport", self.transport)
		patched.start()
		self.addCleanup(patched.stop)
		frappe.conf.update(apns_conf())
		self.addCleanup(frappe.conf.clear)
		push_service._JWT_CACHE.clear()

		self.register(user=ADA, employee=FOREMAN, device_id="DEV-ADA", token="TOK-ADA")
		self.register(user=BEN, employee=PICKER, device_id="DEV-BEN", token="TOK-BEN")
		self.shift = self.tool_data(
			"start_shift",
			{
				"foreman": FOREMAN,
				"location": "Block 7 North",
				"shift_type": "Harvest",
				"start_datetime": f"{frappe.utils.today()} 06:00:00",
				"crew_employees": [FOREMAN, PICKER],
			},
		)["name"]

	def sounds(self) -> list:
		return [call["body"]["aps"]["sound"] for call in self.transport.calls]

	def test_a_crew_break_rings_every_handset_on_the_shift(self):
		answer = self.tool_data(
			"log_shift_break",
			{"shift": self.shift, "break_kind": "Paid Rest", "duration_minutes": 10},
		)

		self.assertEqual(answer["push"]["crew"], 2)
		self.assertEqual(answer["push"]["tokens"], 2)
		self.assertEqual(answer["push"]["sent"], 2)
		self.assertEqual(self.sounds(), ["break_start.caf", "break_start.caf"])

	def test_the_payload_carries_the_event_the_handset_ends_the_break_with(self):
		answer = self.tool_data(
			"log_shift_break",
			{"shift": self.shift, "break_kind": "Paid Rest", "duration_minutes": 10},
		)
		event = answer["logged"]["event"]
		self.assertTrue(event)
		self.assertEqual(self.transport.calls[0]["body"]["event"], event)
		self.assertEqual(self.transport.calls[0]["body"]["shift"], self.shift)

	def test_ending_a_crew_break_rings_the_other_tone(self):
		started = self.tool_data(
			"log_shift_break",
			{"shift": self.shift, "break_kind": "Paid Rest", "duration_minutes": 10},
		)
		self.transport.calls.clear()

		ended = self.tool_data("end_shift_break", {"shift": self.shift, "event": started["logged"]["event"]})
		self.assertEqual(ended["push"]["sent"], 2)
		self.assertEqual(self.sounds(), ["break_end.caf", "break_end.caf"])

	def test_an_individual_break_rings_nobody(self):
		"""A break covering one named worker is not news to the other nineteen."""
		answer = self.tool_data(
			"log_shift_break",
			{
				"shift": self.shift,
				"break_kind": "Unpaid Meal",
				"applies_to": "Individual",
				"employee": PICKER,
				"duration_minutes": 30,
			},
		)
		self.assertEqual(answer["push"]["reason"], "individual_break")
		self.assertEqual(self.transport.calls, [])

	def test_a_heat_break_reaches_the_crew_like_any_other(self):
		"""The kinds v0.96.0 added are the ones the heat rules are written about,
		and they are exactly the breaks somebody in a hot block needs told about."""
		answer = self.tool_data(
			"log_shift_break",
			{"shift": self.shift, "break_kind": "Shade Break", "duration_minutes": 15},
		)
		self.assertEqual(answer["push"]["sent"], 2)
		self.assertEqual(self.transport.calls[0]["body"]["break_kind"], "Shade Break")

	def test_a_push_that_fails_completely_never_costs_the_break_record(self):
		"""THE INVARIANT THE WHOLE TRIGGER IS SUBORDINATE TO. The break log IS the
		evidence heat relief was provided under OAR 437-004-1131; the push is a
		convenience on top of it."""
		with mock.patch.object(push_service, "send_push_to_shift_crew", side_effect=RuntimeError("boom")):
			answer = self.tool_data(
				"log_shift_break",
				{"shift": self.shift, "break_kind": "Cool-Down", "duration_minutes": 15},
			)

		self.assertIn("error:", answer["push"]["reason"])
		events = [
			row
			for row in self.tool_data("get_shift", {"farm_shift": self.shift})["compliance_events"]
			if row.get("break_kind") == "Cool-Down"
		]
		self.assertEqual(len(events), 1)

	def test_an_unconfigured_bench_logs_the_break_and_sends_nothing(self):
		frappe.conf.clear()
		answer = self.tool_data(
			"log_shift_break",
			{"shift": self.shift, "break_kind": "Paid Rest", "duration_minutes": 10},
		)
		self.assertEqual(answer["push"]["reason"], "not_configured")
		self.assertEqual(self.transport.calls, [])
		self.assertEqual(answer["logged"]["break_kind"], "Paid Rest")

	def test_a_crew_with_no_enrolled_handsets_is_reported_as_such(self):
		"""`crew` and `tokens` are separate numbers on purpose: eight workers with
		two tokens is six people who never enrolled a phone, which is a different
		conversation from a push that failed."""
		for row in self.rows():
			frappe.db.set_value(PUSH_TOKEN, row["name"], "is_active", 0)

		answer = self.tool_data(
			"log_shift_break",
			{"shift": self.shift, "break_kind": "Paid Rest", "duration_minutes": 10},
		)
		self.assertEqual(answer["push"]["crew"], 2)
		self.assertEqual(answer["push"]["tokens"], 0)
		self.assertEqual(answer["push"]["reason"], "no_tokens")


# ── 8. the mobile surface ───────────────────────────────────────────────────
class TheSurfaceEnrolsOnlyItsOwnCaller(MobileAPITestCase):
	"""Subclassed from the mobile case rather than from `PushTestCase`, because
	what is under test here is the ROUTE — the scope gate, the subject resolution
	and what `routes.bind` will and will not pass through."""

	def setUp(self):
		super().setUp()
		# MERGED WITH THE MOBILE CASE'S OWN SET rather than replacing it:
		# `configure` writes the whole Single, so passing only this file's
		# switches would turn `create_mobile_user` off underneath the base
		# class's `enrol` helper.
		self.configure(enabled=1, public_url="https://umbrel.tail4a2b.ts.net", **{**MOBILE_ON, **ON})

	def rows(self) -> list:
		return [dict(row) for row in STORE.rows(PUSH_TOKEN)]

	def test_the_wrapper_declares_no_subject_at_all(self):
		"""THE WHOLE OF THE SECURITY, asserted by inspecting the signature rather
		than by driving a body past it: a body naming `employee` would be a way to
		have a colleague's break horns delivered to a handset of your choosing,
		and the reason it cannot is that the argument does not exist."""
		accepted = farmops_routes.accepted_arguments(mobile_api.register_push_token)
		self.assertEqual(accepted, {"token", "device_id", "platform", "app_version", "device_model"})
		self.assertNotIn("employee", accepted)
		self.assertNotIn("user", accepted)

	def test_a_body_naming_somebody_else_is_dropped_at_the_door(self):
		route = farmops_routes.BY_PATH["/mobile/register_push_token"]
		bound = farmops_routes.bind(
			route,
			{"token": "TOK", "device_id": "DEV", "platform": "ios", "employee": "HR-EMP-00001", "user": "x"},
		)
		self.assertEqual(bound, {"token": "TOK", "device_id": "DEV", "platform": "ios"})

	def test_the_logout_wrapper_does_not_accept_a_token(self):
		"""The device is the identity. A logout that had to present the current
		token would fail exactly when it matters — a phone whose token Apple
		rotated between login and logout would go on receiving another shift's
		break horns forever."""
		accepted = farmops_routes.accepted_arguments(mobile_api.unregister_push_token)
		self.assertEqual(accepted, {"device_id", "platform"})

	def test_registering_stores_the_caller_s_own_login_and_employee(self):
		self.be(WORKER)
		answer = mobile_api.register_push_token(token="PHONE-TOK", device_id="PHONE-1", platform="ios")

		self.assertTrue(answer["created"])
		self.assertEqual(answer["device"]["employee"], WORKER_EMPLOYEE)
		self.assertEqual(self.rows()[0]["user"], WORKER)
		self.assertEqual(self.rows()[0]["token"], "PHONE-TOK")

	def test_the_device_token_is_never_echoed_back_to_the_handset(self):
		"""`guard.strip_secrets` removes every token-shaped key on the way out of
		this surface, so a pass-through response would have holes in it whose
		names depended on a hint list two files away. The app does not need it —
		it is the thing that just sent it."""
		self.be(WORKER)
		answer = mobile_api.register_push_token(token="PHONE-TOK", device_id="PHONE-1", platform="ios")

		self.assertNotIn("token", answer)
		self.assertNotIn("push_token", answer)
		self.assertNotIn("token", answer["device"])
		self.assertNotIn("PHONE-TOK", str(answer))

	def test_the_answer_says_whether_this_bench_can_deliver_anything(self):
		"""False means the app should go on playing its own local tone rather
		than expecting one to arrive."""
		self.be(WORKER)
		answer = mobile_api.register_push_token(token="TOK", device_id="PHONE-1", platform="ios")
		self.assertFalse(answer["push_enabled"])

	def test_the_second_launch_of_the_day_does_not_add_a_row(self):
		self.be(WORKER)
		mobile_api.register_push_token(token="TOK-1", device_id="PHONE-1", platform="ios")
		mobile_api.register_push_token(token="TOK-2", device_id="PHONE-1", platform="ios")
		self.assertEqual(len(self.rows()), 1)
		self.assertEqual(self.rows()[0]["token"], "TOK-2")

	def test_platform_defaults_to_ios_when_the_handset_omits_it(self):
		self.be(WORKER)
		answer = mobile_api.register_push_token(token="TOK", device_id="PHONE-1")
		self.assertEqual(answer["device"]["platform"], "ios")

	def test_logging_out_retires_the_handset(self):
		self.be(WORKER)
		mobile_api.register_push_token(token="TOK", device_id="PHONE-1", platform="ios")
		answer = mobile_api.unregister_push_token(device_id="PHONE-1", platform="ios")

		self.assertTrue(answer["deactivated"])
		self.assertEqual(answer["device"]["device_id"], "PHONE-1")
		self.assertEqual(self.rows()[0]["is_active"], 0)

	def test_a_login_with_no_employee_record_still_gets_a_row(self):
		"""Refusing here would be worse than storing a row no crew push reaches
		yet: the handset has already asked the OS for notification permission and
		will not ask again, and the row is repaired the moment somebody links the
		Employee record."""
		unlinked = "casual@example.test"
		self.enrol(email=unlinked, name="Casual Picker")
		self.be(unlinked)
		answer = mobile_api.register_push_token(token="TOK", device_id="PHONE-1", platform="ios")

		self.assertTrue(answer["created"])
		self.assertFalse(answer["device"]["employee"])
		self.assertEqual(self.rows()[0]["user"], unlinked)

	def test_both_routes_are_on_the_table_and_both_are_mutating(self):
		for path in ("/mobile/register_push_token", "/mobile/unregister_push_token"):
			with self.subTest(path=path):
				self.assertIn(path, farmops_routes.BY_PATH)
				self.assertTrue(farmops_routes.BY_PATH[path].mutating)

	def test_the_kill_switch_stops_them_like_every_other_method(self):
		self.configure(enabled=1, farm_ops_mobile_enabled=0, **{**MOBILE_ON, **ON})
		self.be(WORKER)
		with self.assertRaises(guard.MobileDisabled):
			mobile_api.register_push_token(token="TOK", device_id="PHONE-1", platform="ios")
		with self.assertRaises(guard.MobileDisabled):
			mobile_api.unregister_push_token(device_id="PHONE-1", platform="ios")


# ── 9. the two admin tools ──────────────────────────────────────────────────
class TheAdminTools(PushTestCase):
	def setUp(self):
		super().setUp()
		self.register(user=ADA, employee=FOREMAN, device_id="DEV-ADA", token="TOK-ADA")
		self.register(user=BEN, employee=PICKER, device_id="DEV-BEN", token="TOK-BEN")

	def test_the_register_lists_every_handset(self):
		answer = self.tool_data("list_push_tokens", {})
		self.assertEqual(answer["count"], 2)
		self.assertEqual(answer["active"], 2)
		self.assertEqual(answer["inactive"], 0)

	def test_it_filters_by_employee(self):
		answer = self.tool_data("list_push_tokens", {"employee": FOREMAN})
		self.assertEqual([row["device_id"] for row in answer["push_tokens"]], ["DEV-ADA"])

	def test_it_filters_by_active_state_so_the_retired_half_is_findable(self):
		push_tools.unregister_push_token({"user": BEN, "device_id": "DEV-BEN", "platform": "ios"})

		self.assertEqual(self.tool_data("list_push_tokens", {"is_active": True})["count"], 1)
		retired = self.tool_data("list_push_tokens", {"is_active": False})
		self.assertEqual([row["device_id"] for row in retired["push_tokens"]], ["DEV-BEN"])

	def test_it_says_whether_this_bench_can_send_at_all(self):
		"""The commonest answer to "why is their phone quiet" is not on any row: a
		site with no p8 key has a perfect register and sends nothing."""
		answer = self.tool_data("list_push_tokens", {})
		self.assertFalse(answer["apns"]["configured"])
		self.assertIn("apns_key", answer["apns"]["requires"])

		frappe.conf.update(apns_conf())
		self.addCleanup(frappe.conf.clear)
		configured = self.tool_data("list_push_tokens", {})
		self.assertTrue(configured["apns"]["configured"])
		self.assertEqual(configured["apns"]["requires"], "")

	def test_the_register_is_not_a_field_worker_s_read(self):
		set_roles("Administrator", ["Field Worker"])
		refusal = str(self.tool_error("list_push_tokens", {}))
		self.assertIn("may not form or close a crew shift", refusal)
		self.assertIn("Farm Manager", refusal)

	def test_a_test_push_reaches_that_worker_s_handsets_and_nobody_else_s(self):
		frappe.conf.update(apns_conf())
		self.addCleanup(frappe.conf.clear)
		push_service._JWT_CACHE.clear()
		transport = Recorder()
		with mock.patch.object(push_service, "_apns_transport", transport):
			answer = self.tool_data("send_test_push", {"employee": FOREMAN, "message": "Testing the horn."})

		self.assertEqual(answer["tokens"], 1)
		self.assertEqual(answer["result"]["sent"], 1)
		self.assertEqual(len(transport.calls), 1)
		self.assertTrue(transport.calls[0]["url"].endswith("/TOK-ADA"))
		self.assertEqual(transport.calls[0]["body"]["aps"]["alert"]["body"], "Testing the horn.")

	def test_a_test_push_on_an_unconfigured_bench_reports_rather_than_refuses(self):
		"""'This bench has no p8 key' is exactly what somebody running this tool
		is trying to find out."""
		answer = self.tool_data("send_test_push", {"employee": FOREMAN})
		self.assertEqual(answer["result"]["reason"], "not_configured")
		self.assertEqual(answer["tokens"], 1)
		self.assertFalse(answer["apns"]["configured"])

	def test_the_test_push_switch_ships_off(self):
		"""It makes a phone in somebody's pocket ring at the time-sensitive
		interruption level, which rings through Focus."""
		self.configure(allow_send_test_push=0)
		self.assertIn("send_test_push", str(self.tool_error("send_test_push", {"employee": FOREMAN})))


if __name__ == "__main__":
	unittest.main()
