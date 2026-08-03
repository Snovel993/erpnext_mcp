# SPDX-License-Identifier: MIT
"""The iOS Codable structs, mirrored in Python, run against the real responses.

WHY THIS FILE EXISTS. Four times in one month a release shipped a payload the
app could not decode, and every one of them was found by a person holding a
phone rather than by this suite:

  * v0.17.1 — the login QR had no `type`, so `LoginQRParser` refused every scan.
  * v0.17.2 — Tailscale stripped `Authorization` and every call arrived as Guest.
  * v0.18.2 — `claim_task` answered `{"name": null, …}` because
    `dispatch.claim_farm_task` spreads the task at the TOP LEVEL of `data` and
    the wrapper asked for a `"task"` key that was not there. `FarmTask` decodes
    `name` with `try c.decode(String.self)`, so the row did not degrade — it
    threw, mid-flow, on a claim the worker had already made.
  * v0.18.4 — evidence Files came back unreadable to anyone but the uploader.

THE COMMON SHAPE IS THE POINT. The server was tested against itself and passed
every time. What nobody had written down in code was the *other* side's reading
of the same bytes, so the two drifted apart silently and the drift was published.

WHAT A MIRROR IS. Each `Codable` subclass below is a transcription of one struct
in `fafo_ios/FarmOpsKit/Sources/FarmOpsKit/Models/`, and it records the one
distinction that decides whether a bad payload is a blank line on a screen or a
crash in a worker's hands:

    STRICT   `let x: String` decoded with `try c.decode(...)`. Absent, null or
             the wrong type and the WHOLE ROW throws. This is the v0.18.2 class.
    LENIENT  decoded through `c.lenientString(...)` and friends, which return
             nil on absence OR mismatch. A miss here is silent — the field just
             is not on the screen — which is why they are checked too. An
             inspection whose "Why this task exists" card is quietly missing
             looks like an inspection nobody could justify.

Each mirror carries the Swift file and line the rule is transcribed from, and a
failure quotes it, so somebody reading a red test is one click from the source of
truth rather than from this transcription of it.

THE PAYLOAD IS PUT THROUGH JSON FIRST, deliberately. `frappe.as_json` is what
turns a `datetime` into `"2026-08-02 21:14:03"` on the way to the phone, and
half the drift this file is looking for lives in that conversion. Asserting
against the Python dict the wrapper returned would test one step short of the
thing that actually broke.

WHAT THIS FILE DOES NOT PROVE. That the app compiles, or that Swift's decoder
behaves exactly as transcribed here. `test_api_mobile.TheAppCanDecodeThis` makes
the same bet and states it: a transcription is only worth having if it is strict
enough to have caught the bug it was written for, so
`TheMirrorsAreStrictEnough` below feeds each mirror the exact broken payload
from the release that shipped it and asserts the mirror refuses it.
"""

import json

import frappe

from erpnext_mcp.api import files as files_api
from erpnext_mcp.api import mobile as mobile_api

from .fixtures import MAIN
from .harness import STORE
from .test_api_mobile import WORKER, MobileAPITestCase

#: Frappe's own timestamp renderings, from `FrappeDate.formatters` +
#: `ISO8601DateFormatter` in `LenientDecoding.swift:70-85`. A date the app cannot
#: parse decodes to nil, which puts "—" where a claim time should be.
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S")


class ContractError(AssertionError):
	"""A drift between what the server emitted and what the app decodes."""


# ── the mirror machinery ────────────────────────────────────────────────────
class Codable:
	"""One iOS `Codable` struct, transcribed.

	Subclasses declare their fields and nothing else; `decode` is the whole
	implementation, and it is deliberately dumb — every rule it applies is one
	line of Swift it can point at.
	"""

	#: The file this mirror transcribes, for the failure message.
	SWIFT = ""
	#: `(json_key, python_type, swift_line)` — `try c.decode`, throws on a miss.
	STRICT = ()
	#: `(json_key, python_type, swift_line)` — `c.lenient*`, nil on a miss.
	LENIENT = ()
	#: `(json_key, swift_line)` — `c.lenientDate`, nil on an unparseable string.
	DATES = ()
	#: `(json_key, allowed_values, swift_line)` — a Swift enum with an `unknown`
	#: case. Decoding never fails; an unrecognised value degrades, which is a
	#: silent miss and therefore checked.
	ENUMS = ()
	#: `(json_key, Codable subclass, is_list, swift_line)`.
	NESTED = ()

	@classmethod
	def decode(cls, payload, method, path="") -> dict:
		where = f"{method}{path}"
		if not isinstance(payload, dict):
			raise ContractError(
				f"{where} answered {type(payload).__name__} where {cls.__name__} "
				f"({cls.SWIFT}) decodes an object."
			)

		for key, wanted, line in cls.STRICT:
			cls._strict(payload, key, wanted, where, line)
		for key, wanted, line in cls.LENIENT:
			cls._lenient(payload, key, wanted, where, line)
		for key, line in cls.DATES:
			cls._date(payload, key, where, line)
		for key, allowed, line in cls.ENUMS:
			cls._enum(payload, key, allowed, where, line)
		for key, model, is_list, line in cls.NESTED:
			cls._nested(payload, key, model, is_list, method, path, line)
		return payload

	# ── one rule per method, each naming its Swift line ─────────────────────
	@classmethod
	def _strict(cls, payload, key, wanted, where, line) -> None:
		if key not in payload:
			raise ContractError(
				f"{where} did not emit {key!r}. {cls.SWIFT}:{line} decodes it with "
				f"`try c.decode(...)`, so the whole row throws — this is not a blank "
				f"field on a screen, it is a crash in a worker's hand. "
				f"Emitted: {sorted(payload)}"
			)
		value = payload[key]
		if value is None:
			raise ContractError(
				f"{where} emitted {key!r} as null. {cls.SWIFT}:{line} decodes it with "
				f"`try c.decode(...)`, which throws on null — THIS IS THE v0.18.2 BUG, "
				f"in which claim_task answered {{'name': null}} and iOS reported "
				f"\"Bad value at 'name'\"."
			)
		if not isinstance(value, wanted):
			raise ContractError(
				f"{where} emitted {key!r} as {type(value).__name__} ({value!r}). "
				f"{cls.SWIFT}:{line} decodes {wanted.__name__}, and a strict decode of "
				f"the wrong type throws exactly as null does."
			)

	@classmethod
	def _lenient(cls, payload, key, wanted, where, line) -> None:
		value = payload.get(key)
		if value is None:
			# Genuinely absent is allowed — that is what lenient MEANS. The app
			# renders a default or hides the row's card.
			return
		if not isinstance(value, wanted):
			raise ContractError(
				f"{where} emitted {key!r} as {type(value).__name__} ({value!r}), and "
				f"{cls.SWIFT}:{line} reads it with a lenient {wanted.__name__} decode "
				f"that returns nil on a type mismatch. Nothing crashes — the field just "
				f"silently is not there, which is why this is asserted rather than "
				f"trusted."
			)

	@classmethod
	def _date(cls, payload, key, where, line) -> None:
		value = payload.get(key)
		if value is None or value == "":
			return
		import datetime

		if isinstance(value, (datetime.datetime, datetime.date)):
			raise ContractError(
				f"{where} emitted {key!r} as a Python {type(value).__name__} that survived "
				f"JSON encoding. It reaches the phone as whatever `frappe.as_json` made of "
				f"it, and {cls.SWIFT}:{line} parses a string."
			)
		if not isinstance(value, str):
			raise ContractError(
				f"{where} emitted {key!r} as {type(value).__name__}; {cls.SWIFT}:{line} "
				f"reads it with `c.lenientDate`, which decodes a String and nothing else."
			)
		for fmt in _DATE_FORMATS:
			try:
				datetime.datetime.strptime(value, fmt)
				return
			except ValueError:
				continue
		raise ContractError(
			f"{where} emitted {key!r} as {value!r}, which none of `FrappeDate`'s formats "
			f"parse ({', '.join(_DATE_FORMATS)}). {cls.SWIFT}:{line} decodes it to nil, so "
			f"the app shows no time at all rather than the wrong one."
		)

	@classmethod
	def _enum(cls, payload, key, allowed, where, line) -> None:
		value = payload.get(key)
		if value is None:
			return
		if value not in allowed:
			raise ContractError(
				f"{where} emitted {key}={value!r}, which is not a case of the Swift enum at "
				f"{cls.SWIFT}:{line} ({', '.join(sorted(allowed))}). It decodes to `.unknown`, "
				f"which the app renders as \"Unrecognized\" — the state machine has moved and "
				f"the fielded build has not."
			)

	@classmethod
	def _nested(cls, payload, key, model, is_list, method, path, line) -> None:
		value = payload.get(key)
		if value is None:
			return
		if is_list:
			if not isinstance(value, list):
				raise ContractError(
					f"{method}{path} emitted {key!r} as {type(value).__name__}; "
					f"{cls.SWIFT}:{line} decodes an array."
				)
			for index, entry in enumerate(value):
				model.decode(entry, method, f"{path}.{key}[{index}]")
			return
		model.decode(value, method, f"{path}.{key}")


# ── the mirrors ─────────────────────────────────────────────────────────────
class EvidenceContractModel(Codable):
	"""`EvidenceContract.swift` — every field lenient, defaults on a miss."""

	SWIFT = "EvidenceContract.swift"
	LENIENT = (
		("photos", bool, 42),
		("signature", bool, 43),
		("findings_text", bool, 44),
		("witness", bool, 45),
		("min_photos", int, 46),
	)


class FarmTaskModel(Codable):
	"""`FarmTask.swift` — `name` is the only strict field, and it is the one that
	broke. Everything else degrades to a default or an empty card."""

	SWIFT = "FarmTask.swift"
	STRICT = (("name", str, 105),)
	LENIENT = (
		("task_name", str, 107),
		("task_type", str, 108),
		("estimated_duration_minutes", int, 112),
		("skill_required", str, 113),
		("notes", str, 114),
		("company", str, 115),
		("location", str, 116),
		("location_type", str, 117),
		("latitude", float, 118),
		("longitude", float, 119),
		("creates_record", str, 121),
		("source_alert", str, 122),
		("source_alert_explanation", str, 123),
		("assignment", str, 124),
		("assigned_to", str, 125),
	)
	DATES = (("claimed_at", 126), ("started_at", 127))
	ENUMS = (
		("state", {"Draft", "Available", "Claimed", "In-Progress", "Awaiting-Review", "Completed", "Rejected", "Cancelled"}, 109),
		("urgency", {"Low", "Normal", "High", "Critical"}, 110),
		("dispatch_mode", {"Dispatched", "Self-pick", "Either"}, 111),
	)
	NESTED = (("evidence_required", EvidenceContractModel, False, 120),)


class AccessibleCompanyModel(Codable):
	"""`UserContext.swift` — a company whose `name` is null takes the whole
	context down with it, and the context is the first call after a scan."""

	SWIFT = "UserContext.swift"
	STRICT = (("name", str, 17),)
	LENIENT = (("abbr", str, 18),)


class UserContextModel(Codable):
	SWIFT = "UserContext.swift"
	STRICT = (("user", str, 56),)
	LENIENT = (("full_name", str, 57), ("employee", str, 58), ("default_company", str, 61))
	NESTED = (("companies", AccessibleCompanyModel, True, 60),)


class ComplianceAlertSummaryModel(Codable):
	SWIFT = "ComplianceAlertSummary.swift"
	STRICT = (("name", str, 31),)
	LENIENT = (
		("title", str, 32),
		("explanation", str, 33),
		("company", str, 36),
		("regulation", str, 37),
		("linked_task", str, 38),
	)
	DATES = (("due_date", 34),)
	ENUMS = (("urgency", {"Low", "Normal", "High", "Critical"}, 35),)


class CompletionResultModel(Codable):
	"""`CompletionSubmission.swift` — every field lenient, which is the reason
	v0.18.2's completion drift showed up as a worker being told "Done" instead of
	"Housing Inspection HI-… created" rather than as a crash."""

	SWIFT = "CompletionSubmission.swift"
	LENIENT = (
		("task", str, 170),
		("created_record_doctype", str, 171),
		("created_record_name", str, 172),
		("dismissed_alert", str, 173),
		("corrective_action_opened", bool, 174),
	)


class RejectionModel(Codable):
	"""`FarmOpsAPI.reject` discards the body, so nothing here is strict at the
	decoder. `task` is asserted anyway: it is the only proof in the response that
	the rejection landed on the task the worker named, and a wrapper that stopped
	emitting it would be invisible until an audit."""

	SWIFT = "FarmOpsAPI.swift"
	STRICT = (("task", str, 103),)
	LENIENT = (("returned_to_state", str, 103), ("reason", str, 103))


class StagedChunkModel(Codable):
	"""`ChunkUploader.swift:63` calls this through `callVoid` — the app reads
	nothing back. The progress fields are asserted regardless, because the
	sequential uploader's ONLY recovery path after a timeout is re-sending the
	index the server says it wants next."""

	SWIFT = "ChunkUploader.swift"
	LENIENT = (
		("upload_id", str, 63),
		("file_name", str, 65),
		("chunk_index", int, 66),
		("chunk_count", int, 67),
		("chunks_received", int, 63),
		("next_expected_index", int, 63),
		("complete", bool, 63),
	)


class FinalizedFileModel(Codable):
	"""`ChunkUploader.FinalizeResponse` — the one place in the app that throws on
	a MISSING pair rather than a missing field. `file_token` or `file_url` must
	survive; neither and the completion cannot be submitted at all."""

	SWIFT = "ChunkUploader.swift"
	LENIENT = (("file_token", str, 33), ("file_url", str, 40), ("file_name", str, 33))

	@classmethod
	def decode(cls, payload, method, path=""):
		payload = super().decode(payload, method, path)
		if not (payload.get("file_token") or payload.get("file_url")):
			raise ContractError(
				f"{method} returned neither `file_token` nor `file_url`. "
				f"{cls.SWIFT}:39 throws `FrappeError.decoding(\"Upload finalize returned no "
				f"file handle.\")` on exactly this, and the evidence a worker has already "
				f"captured cannot be attached to anything."
			)
		return payload


# ── the wire ────────────────────────────────────────────────────────────────
def on_the_wire(value):
	"""What `frappe.as_json` would put on the phone's socket.

	Not decoration. `default=str` is what turns a `datetime` into the string
	`FrappeDate` parses, and asserting against the wrapper's own return value
	would stop one step before the conversion that half of this file is looking
	for.
	"""
	return json.loads(json.dumps(value, default=str))


class ContractTestCase(MobileAPITestCase):
	"""One enrolled worker, one camp, one inspection task on the board."""

	def setUp(self):
		super().setUp()
		self.unit = self.a_camp()
		self.task = self.a_task(
			creates_record="Housing Inspection",
			location_doctype="Housing Unit",
			location=self.unit,
		)
		self.be()

	def wire(self, method, **kwargs):
		"""Call one mobile method as the worker and return what the phone gets."""
		return on_the_wire(getattr(mobile_api, method)(**kwargs))

	def files_wire(self, method, **kwargs):
		return on_the_wire(getattr(files_api, method)(**kwargs))

	def upload(self, kind="photo", name="FT_photo.jpg"):
		import base64
		import hashlib

		body = f"{kind}-bytes".encode()
		payload = base64.b64encode(body).decode()
		upload_id = f"{kind}-contract"
		staged = self.files_wire(
			"stage_file_chunk",
			upload_id=upload_id,
			file_name=name,
			chunk_index=0,
			chunk_count=1,
			total_bytes=len(body),
			data=payload,
		)
		finalized = self.files_wire(
			"finalize_staged_file",
			upload_id=upload_id,
			file_name=name,
			sha256=hashlib.sha256(body).hexdigest(),
			total_bytes=len(body),
		)
		return staged, finalized


# ── 1. the eleven methods, each decoded by its mirror ────────────────────────
class EveryMobileMethodDecodes(ContractTestCase):
	"""The suite proper. One test per method the app calls."""

	def test_01_get_current_user_context(self):
		UserContextModel.decode(self.wire("get_current_user_context"), "get_current_user_context")

	def test_02_list_my_tasks(self):
		mobile_api.claim_task(task=self.task)
		body = self.wire("list_my_tasks")
		self.assertTrue(body["tasks"], "the fixture claimed a task and the list came back empty")
		for index, row in enumerate(body["tasks"]):
			FarmTaskModel.decode(row, "list_my_tasks", f".tasks[{index}]")

	def test_03_list_available_tasks(self):
		body = self.wire("list_available_tasks")
		self.assertTrue(body["tasks"], "the fixture put a task in the pool and the pool came back empty")
		for index, row in enumerate(body["tasks"]):
			FarmTaskModel.decode(row, "list_available_tasks", f".tasks[{index}]")

	def test_04_get_task(self):
		FarmTaskModel.decode(self.wire("get_task", task=self.task), "get_task")

	def test_05_claim_task(self):
		"""THE v0.18.2 REGRESSION TEST. This is the call that shipped broken."""
		row = self.wire("claim_task", task=self.task)
		FarmTaskModel.decode(row, "claim_task")
		self.assertEqual(row["name"], self.task)
		self.assertTrue(row["assignment"], "a claim with no assignment is not a claim")

	def test_06_start_task(self):
		mobile_api.claim_task(task=self.task)
		row = self.wire("start_task", task=self.task)
		FarmTaskModel.decode(row, "start_task")
		self.assertEqual(row["state"], "In-Progress")

	def test_07_complete_task_via_mobile(self):
		claimed = mobile_api.claim_task(task=self.task)
		mobile_api.start_task(task=self.task)
		_staged, photo = self.upload("photo", "FT_photo.jpg")
		_staged, signature = self.upload("signature", "FT_signature.png")
		row = self.wire(
			"complete_task_via_mobile",
			task=self.task,
			task_assignment=claimed["assignment"],
			findings_text="",
			clean_pass=True,
			completion_narrative="walked it",
			evidence_files=[
				{"file_token": photo["file_token"], "file_name": "FT_photo.jpg", "kind": "photo"},
				{
					"file_token": signature["file_token"],
					"file_name": "FT_signature.png",
					"kind": "signature",
				},
			],
		)
		CompletionResultModel.decode(row, "complete_task_via_mobile")
		self.assertEqual(row["created_record_doctype"], "Housing Inspection")

	def test_08_reject_task(self):
		mobile_api.claim_task(task=self.task)
		row = self.wire("reject_task", task=self.task, reason="the ladder is broken")
		RejectionModel.decode(row, "reject_task")
		self.assertEqual(row["task"], self.task)

	def test_09_list_compliance_alerts(self):
		# The sweep is an operator's call, not a worker's — the phone only ever
		# reads the calendar it produces.
		frappe.local.session.user = "Administrator"
		self.tool_data("refresh_compliance_alerts", {"company": MAIN})
		self.be()
		body = self.wire("list_compliance_alerts")
		for index, row in enumerate(body["alerts"]):
			ComplianceAlertSummaryModel.decode(row, "list_compliance_alerts", f".alerts[{index}]")

	def test_10_stage_file_chunk(self):
		staged, _finalized = self.upload()
		StagedChunkModel.decode(staged, "stage_file_chunk")
		self.assertEqual(staged["chunk_index"], 0)
		self.assertTrue(staged["complete"])

	def test_11_finalize_staged_file(self):
		_staged, finalized = self.upload()
		FinalizedFileModel.decode(finalized, "finalize_staged_file")
		self.assertTrue(finalized["file_token"])


# ── 2. the mirrors are strict enough to have caught the bugs ────────────────
class TheMirrorsAreStrictEnough(ContractTestCase):
	"""A transcription that accepts the broken payload proves nothing at all.

	Every mirror above is fed the exact payload from the release that shipped
	broken, and has to refuse it. Without these, this whole file could be a
	no-op and every test in it would still be green.
	"""

	def test_the_v0_18_2_claim_task_payload_is_refused_by_name(self):
		"""`{"name": null, …}` — what iOS reported as "Bad value at 'name'"."""
		broken = dict(self.wire("get_task", task=self.task))
		broken["name"] = None
		with self.assertRaises(ContractError) as caught:
			FarmTaskModel.decode(broken, "claim_task")
		self.assertIn("v0.18.2", str(caught.exception))
		self.assertIn("claim_task", str(caught.exception))
		self.assertIn("FarmTask.swift:105", str(caught.exception))

	def test_a_missing_strict_field_is_refused_and_names_the_swift_line(self):
		broken = dict(self.wire("get_task", task=self.task))
		broken.pop("name")
		with self.assertRaises(ContractError) as caught:
			FarmTaskModel.decode(broken, "get_task")
		self.assertIn("FarmTask.swift:105", str(caught.exception))

	def test_a_user_context_whose_company_has_no_name_is_refused(self):
		"""The nested strict field. One bad row in `companies` throws the lot."""
		broken = dict(self.wire("get_current_user_context"))
		broken["companies"] = [{"name": None, "abbr": "MC"}]
		with self.assertRaises(ContractError) as caught:
			UserContextModel.decode(broken, "get_current_user_context")
		self.assertIn("UserContext.swift:17", str(caught.exception))
		self.assertIn("companies[0]", str(caught.exception))

	def test_a_state_outside_the_swift_enum_is_refused(self):
		broken = dict(self.wire("get_task", task=self.task))
		broken["state"] = "Paused"
		with self.assertRaises(ContractError) as caught:
			FarmTaskModel.decode(broken, "get_task")
		self.assertIn("Unrecognized", str(caught.exception))

	def test_a_timestamp_frappe_date_cannot_parse_is_refused(self):
		broken = dict(self.wire("get_task", task=self.task))
		broken["claimed_at"] = "02/08/2026 9:14 PM"
		with self.assertRaises(ContractError) as caught:
			FarmTaskModel.decode(broken, "get_task")
		self.assertIn("FrappeDate", str(caught.exception))

	def test_a_number_where_a_lenient_string_belongs_is_refused(self):
		"""Silent, not fatal — `lenientString` decodes String and nothing else,
		so an Int `company` simply is not on the screen."""
		broken = dict(self.wire("get_task", task=self.task))
		broken["company"] = 7
		with self.assertRaises(ContractError) as caught:
			FarmTaskModel.decode(broken, "get_task")
		self.assertIn("silently", str(caught.exception))

	def test_a_finalize_with_neither_handle_is_refused(self):
		with self.assertRaises(ContractError) as caught:
			FinalizedFileModel.decode({"file_name": "x.jpg"}, "finalize_staged_file")
		self.assertIn("no file handle", str(caught.exception))

	def test_a_finalize_with_only_a_url_is_accepted_because_the_app_accepts_it(self):
		"""The mirror is not stricter than the app. `FinalizeResponse` falls back
		to `file_url` on purpose, and a test that refused it would send somebody
		hunting a bug that is not there."""
		FinalizedFileModel.decode(
			{"file_url": "/private/files/x.jpg", "file_name": "x.jpg"}, "finalize_staged_file"
		)


# ── 3. the mirrors cover the surface, and keep covering it ──────────────────
class TheContractIsComplete(ContractTestCase):
	"""The test that fails when somebody adds a twelfth method and no mirror.

	Without this, the suite silently stops covering the surface the moment it
	grows — which is precisely how the app got four undecodable releases while
	a green suite watched.
	"""

	#: Method name → the test that decodes it, by number.
	COVERED = {
		"get_current_user_context": "test_01",
		"list_my_tasks": "test_02",
		"list_available_tasks": "test_03",
		"get_task": "test_04",
		"claim_task": "test_05",
		"start_task": "test_06",
		"complete_task_via_mobile": "test_07",
		"reject_task": "test_08",
		"list_compliance_alerts": "test_09",
		"stage_file_chunk": "test_10",
		"finalize_staged_file": "test_11",
	}

	def _published(self, module):
		return {
			name
			for name in dir(module)
			if not name.startswith("_") and getattr(getattr(module, name), "farm_ops_method", None)
		}

	def test_every_published_method_has_a_mirror_test(self):
		published = self._published(mobile_api) | self._published(files_api)
		missing = published - set(self.COVERED)
		self.assertEqual(
			missing,
			set(),
			f"{sorted(missing)} is reachable from the app and nothing in this file decodes its "
			f"response. Add a mirror of its iOS Codable and a test_NN_ method.",
		)

	def test_no_mirror_test_names_a_method_that_no_longer_exists(self):
		published = self._published(mobile_api) | self._published(files_api)
		stale = set(self.COVERED) - published
		self.assertEqual(stale, set(), f"{sorted(stale)} is covered here and published nowhere.")

	def test_the_covering_tests_all_exist(self):
		names = {name for name in dir(EveryMobileMethodDecodes) if name.startswith("test_")}
		for method, prefix in self.COVERED.items():
			with self.subTest(method=method):
				self.assertTrue(
					any(name.startswith(prefix) for name in names),
					f"{method} claims to be covered by {prefix}_*, which is not in "
					f"EveryMobileMethodDecodes.",
				)

	def test_every_swift_file_a_mirror_names_is_on_disk(self):
		"""A mirror pointing at a renamed file sends somebody to nowhere.

		Skipped rather than failed when the iOS checkout is not beside this one —
		the server suite has to run on a bench that has never seen the app.
		"""
		import os

		root = os.path.abspath(
			os.path.join(
				os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
				"..",
				"fafo_ios",
				"FarmOpsKit",
				"Sources",
				"FarmOpsKit",
			)
		)
		if not os.path.isdir(root):
			self.skipTest("the fafo_ios checkout is not beside this one")
		mirrors = (
			EvidenceContractModel,
			FarmTaskModel,
			AccessibleCompanyModel,
			UserContextModel,
			ComplianceAlertSummaryModel,
			CompletionResultModel,
			StagedChunkModel,
			FinalizedFileModel,
		)
		for mirror in mirrors:
			with self.subTest(mirror=mirror.__name__):
				found = [
					os.path.join(where, mirror.SWIFT)
					for where in (
						os.path.join(root, "Models"),
						os.path.join(root, "Networking"),
					)
					if os.path.isfile(os.path.join(where, mirror.SWIFT))
				]
				self.assertTrue(found, f"{mirror.__name__} points at {mirror.SWIFT}, which is not there")


# ── 4. the fields that only matter because a person reads them ──────────────
class TheScreensHaveSomethingToShow(ContractTestCase):
	"""Decoding is not the whole contract. A payload can decode perfectly and
	still leave a worker looking at a card with nothing on it, which is the
	failure mode `shape.py`'s docstring calls worse than a crash."""

	def test_a_task_raised_by_an_alert_carries_the_sentence_the_card_renders(self):
		frappe.local.session.user = "Administrator"
		self.tool_data("refresh_compliance_alerts", {"company": MAIN})
		alert = next(iter(STORE.rows("Compliance Alert")), None)
		if alert is None:
			self.skipTest("this fixture raised no compliance alert")
		task = self.a_task(source_alert=alert["name"])
		self.be()
		row = self.wire("get_task", task=task)
		FarmTaskModel.decode(row, "get_task")
		self.assertTrue(
			row.get("source_alert_explanation"),
			"the app hides its 'Why this task exists' card without this, and an inspection "
			"with no stated reason is worse than none",
		)

	def test_the_evidence_contract_survives_the_wire_as_an_object(self):
		"""`lenientJSON` takes an object OR a string of JSON; both work, and a
		list would silently become `.none` — a completion screen asking for
		nothing on a task that requires two photographs and a signature."""
		row = self.wire("get_task", task=self.task)
		contract = row["evidence_required"]
		self.assertIsInstance(contract, (dict, str), f"evidence_required arrived as {type(contract)}")
		if isinstance(contract, str):
			contract = json.loads(contract)
		EvidenceContractModel.decode(contract, "get_task", ".evidence_required")

	def test_the_completion_tells_the_worker_what_their_work_produced(self):
		""""Housing Inspection HI-… created" closes the loop; "Done" does not."""
		claimed = mobile_api.claim_task(task=self.task)
		mobile_api.start_task(task=self.task)
		_staged, photo = self.upload("photo", "FT_photo.jpg")
		_staged, signature = self.upload("signature", "FT_signature.png")
		row = self.wire(
			"complete_task_via_mobile",
			task=self.task,
			task_assignment=claimed["assignment"],
			findings_text="",
			clean_pass=True,
			evidence_files=[
				{"file_token": photo["file_token"], "kind": "photo"},
				{"file_token": signature["file_token"], "kind": "signature"},
			],
		)
		self.assertTrue(row["created_record_name"], "the worker is told nothing was produced")
		self.assertTrue(row["created_record_doctype"])
