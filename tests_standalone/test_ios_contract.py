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
    NULLABLE `let x: String?` on a struct with a synthesized `init(from:)` and
             no lenient helper. Absence is fine, the WRONG TYPE throws. Between
             the two, and it is what `ExistingEmployee` and `CreatedEmployee`
             actually do — calling either of them LENIENT would claim a
             tolerance the app does not have.

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
import unittest
from typing import ClassVar

import frappe

from erpnext_mcp import i9_pdf
from erpnext_mcp.api import files as files_api
from erpnext_mcp.api import mobile as mobile_api

from .fixtures import MAIN, install_hrms
from .harness import STORE, set_roles
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
	#: `(json_key, python_type, swift_line)` — a `String?`/`Int?` on a struct with
	#: SYNTHESIZED `init(from:)` and no lenient helper. Absent or null is fine and
	#: the field is simply nil; the WRONG TYPE throws exactly as a strict field
	#: does, because Swift's generated decoder calls `decodeIfPresent`, which is
	#: forgiving about presence and not about type. Between STRICT and LENIENT, and
	#: neither of them would be a true transcription of `ExistingEmployee`.
	NULLABLE = ()
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
		for key, wanted, line in cls.NULLABLE:
			cls._nullable(payload, key, wanted, where, line)
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
	def _nullable(cls, payload, key, wanted, where, line) -> None:
		value = payload.get(key)
		if value is None:
			# `decodeIfPresent` on an absent or null key is nil, and the struct
			# declares the property optional. Nothing to answer for.
			return
		if not isinstance(value, wanted):
			raise ContractError(
				f"{where} emitted {key!r} as {type(value).__name__} ({value!r}). "
				f"{cls.SWIFT}:{line} declares {wanted.__name__}? on a struct with a synthesized "
				f"decoder, so a wrong TYPE throws the whole row even though a missing key would "
				f"not — this crashes in a worker's hand exactly as a strict field does."
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
				f'which the app renders as "Unrecognized" — the state machine has moved and '
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
		(
			"state",
			{
				"Draft",
				"Available",
				"Claimed",
				"In-Progress",
				"Awaiting-Review",
				"Completed",
				"Rejected",
				"Cancelled",
			},
			109,
		),
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
				f'{cls.SWIFT}:39 throws `FrappeError.decoding("Upload finalize returned no '
				f'file handle.")` on exactly this, and the evidence a worker has already '
				f"captured cannot be attached to anything."
			)
		return payload


# ── v0.24.0: Universal Asset Tags ──────────────────────────────────────────
class ScanAssetModel(Codable):
	"""`AssetScanResponse` — what the phone shows after scanning a tag."""

	SWIFT = "AssetScanResponse.swift"
	STRICT = (("name", str, 5), ("scan_recorded", bool, 6))
	LENIENT = (
		("asset_type", str, 8),
		("company", str, 9),
		("description", str, 12),
	)


class AssetDetailModel(Codable):
	"""`AssetDetailResponse` — the full detail screen behind an asset tag."""

	SWIFT = "AssetDetailResponse.swift"
	STRICT = (("name", str, 5),)
	LENIENT = (
		("asset_type", str, 8),
		("company", str, 9),
		("description", str, 12),
	)


class StateChangeModel(Codable):
	"""`StateChangeResponse` — confirmation after logging a state change."""

	SWIFT = "StateChangeResponse.swift"
	STRICT = (
		("asset_name", str, 5),
		("action", str, 6),
		("from_state", str, 7),
		("to_state", str, 8),
	)
	LENIENT = (
		("asset_type", str, 10),
		("log_name", str, 11),
		("performed_by", str, 12),
	)


class AvailableActionsModel(Codable):
	"""`AvailableActionsResponse` — what a worker can do to an asset now."""

	SWIFT = "AvailableActionsResponse.swift"
	STRICT = (
		("asset_name", str, 5),
		("current_state", str, 6),
	)
	LENIENT = (
		("asset_type", str, 8),
		("available_actions", list, 9),
	)


class CreatedEmployeeModel(Codable):
	"""`OnboardingAPI.CreatedEmployee` — what step 1 of the wizard keeps.

	THE ONE STRICT FIELD IS THE ONE EVERY LATER STEP IS ADDRESSED TO. `employeeName`
	becomes `employee` on `create_i9_form`, on both I-9 sections, on the W-4 and on
	the badge map; a null or absent `name` here throws on the person's first
	morning, after their date of birth has been typed in and before anything has
	been filed. This is the v0.18.2 class of bug with four more screens behind it.

	`employee_id` is `String?` — a site that does not keep payroll numbers sends
	null and the app falls back to the docname (`OnboardingWizardViewModel:181`).
	NULLABLE rather than LENIENT because the struct has no lenient decoding: an
	`employee_id` that arrived as a NUMBER would throw.
	"""

	SWIFT = "OnboardingAPI.swift"
	STRICT = (("name", str, 16),)
	NULLABLE = (("employee_id", str, 17),)


class ExistingEmployeeModel(Codable):
	"""`ExistingEmployee` — one row of the search the Identity step opens with.

	`name` and `employee_name` are non-optional `String`s on a synthesized decoder,
	so either one missing throws the WHOLE LIST and the foreman sees an error where
	the returning picker's name should be — and then hires them a second time,
	which is the duplicate `create_employee` exists to refuse.

	`status` is what the wizard branches on (`OnboardingWizardViewModel:171`):
	anything other than "Active" routes to `reactivate_employee`. It is a plain
	optional String rather than a Swift enum, so an unexpected value does not
	degrade to `.unknown` — it reactivates.
	"""

	SWIFT = "OnboardingModels.swift"
	STRICT = (("name", str, 274), ("employee_name", str, 275))
	NULLABLE = (
		("employee_id", str, 276),
		("status", str, 277),
		("date_of_joining", str, 278),
		("employment_type", str, 279),
	)


class EmployeeDetailModel(Codable):
	"""`EmployeeDetail` — the record the returning-worker path skips steps on.

	`name` and `employee_name` are non-optional `String`s on a synthesized decoder,
	so either one missing throws and the foreman is left on the Identity step with
	a person standing in front of them who demonstrably exists — they were just
	picked out of `search_employees`.

	THE THREE STATUS FIELDS ARE WHY THIS MIRROR IS STRICTER THAN IT LOOKS.
	`satisfiedSteps` (`OnboardingModels.swift:352`) reads `i9_status`, `w4_status`
	and `badge_id` and marks whole wizard steps done from them. A wrong TYPE
	throws the row like any other; a wrong VALUE does not throw at all, it just
	silently re-collects an I-9 — or, far worse in the other direction, skips one.
	They are plain optional Strings rather than Swift enums, so there is no
	`.unknown` case to degrade into and nothing on the handset would report it.
	The value side is asserted in `test_30`, against the site's own Select options.

	`jurisdiction` is NOT on `ExistingEmployee` and is here: it arrived with the
	same iOS change that moved this call onto the sidecar, and the W-4 step needs
	it the moment a crew works across a state line.
	"""

	SWIFT = "OnboardingModels.swift"
	STRICT = (("name", str, 294), ("employee_name", str, 295))
	NULLABLE = (
		("first_name", str, 296),
		("last_name", str, 297),
		("employee_id", str, 298),
		("status", str, 299),
		("gender", str, 300),
		("date_of_birth", str, 301),
		("date_of_joining", str, 302),
		("company", str, 303),
		("i9_status", str, 304),
		("w4_status", str, 305),
		("badge_id", str, 306),
		("jurisdiction", str, 307),
	)


class VoidResponseModel(Codable):
	"""The onboarding methods whose answer the app decodes NOTHING out of.

	`OnboardingAPI` calls the five through `FrappeClient.callVoid`, which returns
	the raw `Data` and throws it away — the flow advances on the HTTP status and on
	nothing else. `reactivateEmployee` discards its answer the same way. So there
	is no struct to transcribe, and a mirror that invented one would be asserting a
	contract neither side has.

	WHAT IS STILL WORTH ASSERTING is that the answer is a JSON OBJECT. `callRaw`
	unwraps Frappe's `message` envelope for every other call in the app, and a
	wrapper that answered a bare list or a string would be the shape no later
	decoder could be added to without a server change — which is exactly the drift
	this file exists to catch early. The onboarding screens will grow a decoder the
	first time one of them wants to show the I-9's docname back to the person who
	filled it in.
	"""

	SWIFT = "OnboardingAPI.swift"


class UndecodedResponseModel(Codable):
	"""A method `MobileAPI.swift` names and no Swift struct decodes yet.

	The crew clock and the bucket sync: v0.45.0 publishes the endpoints, and
	`MobileAPI` carries their paths, but `CrewClockSession` still keeps its shift
	on the handset and `BucketEntry` is still only ever written to the local queue.
	The mirror records that honestly rather than pretending to a contract, and
	holds the one line that IS already true — the answer is an object — so that
	whoever writes `Shift.swift` starts from a payload that can carry a struct.

	v0.47.0 puts two more here, and they are the case this class was written for
	rather than an exception to it. `list_i9_document_types` and `reverify_i9`
	PRECEDE their screens: the app's Section 2 picker is a hardcoded Swift array
	today and its wizard has no reverification step at all, so there is no struct
	to transcribe and inventing one would assert a contract neither side has —
	the sentence `VoidResponseModel` above already refuses to write. What the two
	tests covering them do instead is assert the PAYLOAD, key by key, because the
	shape is the thing being published and a picker built against it is the next
	thing anybody writes.

	v0.47.1 puts three more here for the same reason and one more besides. The
	I-9 review screen, the Print button and the "photograph the signed sheet"
	step do not exist in the shipped app, so `get_i9_form`, `generate_i9_pdf` and
	`upload_signed_i9` have no struct to mirror — and `generate_i9_pdf`'s answer
	is deliberately a NARROWED reshape of the tool's, not the tool's own dict, so
	the Swift written against it later is written against a stable dozen keys
	rather than against everything `render_i9_pdf` happens to return. The tests
	assert those keys one by one.
	"""

	SWIFT = "MobileAPI.swift"


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

	#: The person v0.45.0's onboarding methods are run against. Carries
	#: `first_name`/`last_name` and NOT `legal_*`, because the fallback in
	#: `submit_i9_section_1` reading them off the Employee row is the one thing
	#: that makes the shipped `OnboardingI9Section1.apiParams` — which sends
	#: neither legal name — a call that succeeds rather than a required-field
	#: refusal on a phone.
	NEW_HIRE = "EMP-NEWHIRE"
	SECOND_HAND = "EMP-SECOND"

	def the_hr_furniture(self):
		"""Everything the nine v0.45.0 methods need, and the role that reaches them.

		THE ROLE IS THE POINT OF THIS HELPER. `tools/i9.py`, `tools/w4.py`,
		`tools/shifts.py` and `tools/bucket_log.py` each gate on
		`employee.require_hr_role` or `kpi.require_kpi_role`, and the only role in
		BOTH those lists and `guard.FARM_OPS_ROLES` is Farm Manager. Ana is enrolled
		as a Field Worker like every other test in this file, so she is granted the
		second role here — which is exactly what an operator has to do on a real
		site before a phone can open a shift or file an I-9. A fixture that widened
		the gate instead would be testing a site nobody runs.
		"""
		install_hrms()
		STORE.singles["I-9 Settings"] = {
			"doctype": "I-9 Settings",
			"store_document_copies": "0",
			"enrolled_in_e_verify": "0",
			"business_legal_name": "Test Farm LLC",
			"business_address": "123 Orchard Rd",
			"business_ein": "12-3456789",
			"reminder_days_before_doc_expiration": "90",
			"reminder_days_before_destruction": "60",
		}
		STORE.seed(
			"Employee",
			[
				{
					"name": self.NEW_HIRE,
					"employee_name": "Rosa Delgado",
					"first_name": "Rosa",
					"last_name": "Delgado",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": frappe.utils.today(),
				},
				{
					"name": self.SECOND_HAND,
					"employee_name": "Marco Vega",
					"first_name": "Marco",
					"last_name": "Vega",
					"company": MAIN,
					"status": "Active",
					"date_of_joining": frappe.utils.today(),
				},
			],
		)
		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		self.be()

	def a_shift(self, **overrides):
		"""One open shift, started by Ana from her own phone."""
		payload = {"location": "Block 7 North", "shift_type": "Harvest", "company": MAIN}
		payload.update(overrides)
		return self.wire("start_shift", **payload)

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


# ── 1. the twelve methods, each decoded by its mirror ─────────────────────────
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

	def test_10_report_field_task(self):
		_staged, photo = self.upload("photo", "FR_photo.jpg")
		row = self.wire(
			"report_field_task",
			photo_file_token=photo["file_token"],
			description="broken sprinkler head",
		)
		FarmTaskModel.decode(row, "report_field_task")

	def test_11_stage_file_chunk(self):
		staged, _finalized = self.upload()
		StagedChunkModel.decode(staged, "stage_file_chunk")
		self.assertEqual(staged["chunk_index"], 0)
		self.assertTrue(staged["complete"])

	def test_12_finalize_staged_file(self):
		_staged, finalized = self.upload()
		FinalizedFileModel.decode(finalized, "finalize_staged_file")
		self.assertTrue(finalized["file_token"])

	def test_13_scan_asset(self):
		self.configure(enabled=1, allow_scan_asset=1, allow_register_asset=1)
		self.tool_data("register_asset", {
			"name": "MC-Valve-05",
			"asset_type": "Irrigation Valve",
			"company": MAIN,
		})
		self.be()
		row = self.wire("scan_asset", asset_name="MC-Valve-05")
		ScanAssetModel.decode(row, "scan_asset")
		self.assertTrue(row["scan_recorded"])

	def test_14_get_asset_detail(self):
		self.configure(enabled=1, allow_get_asset_detail=1, allow_register_asset=1)
		self.tool_data("register_asset", {
			"name": "MC-Valve-05",
			"asset_type": "Irrigation Valve",
			"company": MAIN,
		})
		self.be()
		row = self.wire("get_asset_detail", asset_name="MC-Valve-05")
		AssetDetailModel.decode(row, "get_asset_detail")
		self.assertEqual(row["name"], "MC-Valve-05")

	def test_15_log_asset_state_change(self):
		self.configure(enabled=1, allow_log_asset_state_change=1, allow_register_asset=1)
		self.tool_data("register_asset", {
			"name": "MC-Valve-05",
			"asset_type": "Irrigation Valve",
			"company": MAIN,
		})
		self.be()
		row = self.wire("log_asset_state_change", asset_name="MC-Valve-05", action="open_valve")
		StateChangeModel.decode(row, "log_asset_state_change")
		self.assertEqual(row["from_state"], "closed")
		self.assertEqual(row["to_state"], "open")

	def test_16_get_available_actions(self):
		self.configure(enabled=1, allow_get_available_actions=1, allow_register_asset=1)
		self.tool_data("register_asset", {
			"name": "MC-Valve-05",
			"asset_type": "Irrigation Valve",
			"company": MAIN,
		})
		self.be()
		row = self.wire("get_available_actions", asset_name="MC-Valve-05")
		AvailableActionsModel.decode(row, "get_available_actions")
		self.assertEqual(row["current_state"], "closed")

	def test_17_report_asset_issue(self):
		self.configure(
			enabled=1, allow_report_asset_issue=1, allow_register_asset=1,
			allow_report_field_task=1,
		)
		self.tool_data("register_asset", {
			"name": "MC-Valve-05",
			"asset_type": "Irrigation Valve",
			"company": MAIN,
		})
		self.be()
		_staged, photo = self.upload("photo", "AI_photo.jpg")
		row = self.wire(
			"report_asset_issue",
			asset_name="MC-Valve-05",
			description="Leaking badly",
			photo_file_token=photo["file_token"],
		)
		FarmTaskModel.decode(row, "report_asset_issue")

	# ── v0.45.0: onboarding ─────────────────────────────────────────────────
	def test_18_create_i9_form(self):
		self.the_hr_furniture()
		row = self.wire(
			"create_i9_form",
			employee=self.NEW_HIRE,
			company=MAIN,
			hire_date=frappe.utils.today(),
		)
		VoidResponseModel.decode(row, "create_i9_form")
		self.assertEqual(row["status"], "Draft")

	def test_19_submit_i9_section_1(self):
		"""THE LEGAL NAMES ARE NOT SENT, exactly as the shipped app does not send them.

		`OnboardingI9Section1.apiParams` has no `legal_first_name` and no
		`legal_last_name`, and the tool requires both. If this test ever passes them
		it stops testing the call the phone actually makes.
		"""
		self.the_hr_furniture()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		row = self.wire(
			"submit_i9_section_1",
			employee=self.NEW_HIRE,
			citizenship_status="US Citizen",
			ssn_last_four="6789",
			address_street="14 Orchard Lane",
			address_city="Hood River",
			address_state="OR",
			address_zip="97031",
		)
		VoidResponseModel.decode(row, "submit_i9_section_1")
		self.assertEqual(row["status"], "Section 1 Complete")
		filed = next(iter(STORE.rows("I-9 Form")))
		self.assertEqual(filed["legal_first_name"], "Rosa")
		self.assertEqual(filed["legal_last_name"], "Delgado")

	def test_20_submit_i9_section_2(self):
		"""The three renames per document list, run through the List B + C path."""
		self.the_hr_furniture()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire("submit_i9_section_1", employee=self.NEW_HIRE, citizenship_status="US Citizen")
		row = self.wire(
			"submit_i9_section_2",
			employee=self.NEW_HIRE,
			document_path="List B + C",
			verifier_name="Ana Ramos",
			verifier_title="Farm Manager",
			verification_date=frappe.utils.today(),
			list_b_doc_type="Driver's License",
			list_b_doc_number="OR-4417",
			list_b_authority="Oregon DMV",
			list_c_doc_type="Social Security Card (Unrestricted)",
			list_c_doc_number="***-**-6789",
			list_c_authority="SSA",
		)
		VoidResponseModel.decode(row, "submit_i9_section_2")
		self.assertEqual(row["status"], "Complete")
		filed = next(iter(STORE.rows("I-9 Form")))
		self.assertEqual(filed["list_b_doc_title"], "Driver's License")
		self.assertEqual(filed["list_c_doc_authority"], "SSA")

	def test_21_submit_w4(self):
		self.the_hr_furniture()
		row = self.wire(
			"submit_w4",
			employee=self.NEW_HIRE,
			company=MAIN,
			tax_year=2026,
			filing_status="Married Filing Jointly",
			multiple_jobs=False,
			dependents_under_17=2,
			other_dependents=1,
			extra_withholding=25,
		)
		VoidResponseModel.decode(row, "submit_w4")
		self.assertEqual(row["status"], "Active")
		filed = next(iter(STORE.rows("W-4 Form")))
		self.assertEqual(int(filed["dependents_under_17_count"]), 2)
		self.assertEqual(float(filed["extra_withholding_per_period"]), 25.0)

	def test_22_link_badge_to_employee(self):
		self.the_hr_furniture()
		row = self.wire(
			"link_badge_to_employee",
			badge_id="QR-0042",
			employee=self.NEW_HIRE,
			company=MAIN,
		)
		VoidResponseModel.decode(row, "link_badge_to_employee")
		self.assertEqual(row["badge"]["employee"], self.NEW_HIRE)
		self.assertTrue(row["badge"]["active"])

	# ── v0.45.0: the capture queue and the crew clock ───────────────────────
	def test_23_sync_bucket_entries(self):
		"""The handset's own spelling — `id`, `session_id`, `badge_id`, `accepted`."""
		self.the_hr_furniture()
		self.wire("link_badge_to_employee", badge_id="QR-0042", employee=self.NEW_HIRE, company=MAIN)
		row = self.wire(
			"sync_bucket_entries",
			company=MAIN,
			entries=[
				{
					"id": "1E4A0C7A-0001",
					"session_id": "SESSION-A",
					"badge_id": "QR-0042",
					"timestamp": f"{frappe.utils.today()} 07:12:00",
					"accepted": True,
					"coverage_percent": 94.5,
					"device_id": "iPad-7",
				},
				{
					"id": "1E4A0C7A-0002",
					"session_id": "SESSION-A",
					"badge_id": "QR-0042",
					"timestamp": f"{frappe.utils.today()} 07:19:00",
					"accepted": False,
					"coverage_percent": 41.0,
					"device_id": "iPad-7",
				},
			],
		)
		UndecodedResponseModel.decode(row, "sync_bucket_entries")
		self.assertEqual(row["created_count"], 2)
		self.assertEqual(row["invalid_count"], 0, row["invalid"])
		filed = {entry["verdict"] for entry in STORE.rows("Bucket Log Entry")}
		self.assertEqual(filed, {"Accepted", "Rejected"})
		# The badge resolved through the map rather than through the body.
		self.assertTrue(all(entry["employee"] == self.NEW_HIRE for entry in STORE.rows("Bucket Log Entry")))

	def test_24_start_shift(self):
		"""`foreman` IS NOT IN THE SIGNATURE — it comes off the authenticated caller."""
		self.the_hr_furniture()
		row = self.a_shift(crew_employees=[self.NEW_HIRE, self.SECOND_HAND])
		UndecodedResponseModel.decode(row, "start_shift")
		self.assertEqual(row["foreman"], "EMP-ANA")
		self.assertEqual(row["crew_size"], 2)

	def test_25_add_worker_to_shift(self):
		self.the_hr_furniture()
		shift = self.a_shift(crew_employees=[self.NEW_HIRE])
		row = self.wire("add_worker_to_shift", shift=shift["name"], employee=self.SECOND_HAND)
		UndecodedResponseModel.decode(row, "add_worker_to_shift")
		self.assertEqual(row["added"]["employee"], self.SECOND_HAND)
		self.assertEqual(row["crew_size"], 2)

	def test_26_end_shift(self):
		self.the_hr_furniture()
		shift = self.a_shift(crew_employees=[self.NEW_HIRE, self.SECOND_HAND])
		_staged, signature = self.upload("signature", "shift_signature.png")
		row = self.wire(
			"end_shift",
			shift=shift["name"],
			supervisor_signature_file_token=signature["file_token"],
		)
		UndecodedResponseModel.decode(row, "end_shift")
		self.assertEqual(row["attendance_created"], 2)

	# ── v0.46.0: the Identity step the wizard 404'd on ──────────────────────
	def test_27_create_employee(self):
		"""THE CALL THE WHOLE FLOW WAS STUCK BEHIND. Step 1 of five."""
		self.the_hr_furniture()
		row = self.wire(
			"create_employee",
			first_name="Elena",
			last_name="Marquez",
			employee_name="Elena Marquez",
			gender="Female",
			date_of_birth="1994-03-11",
			company=MAIN,
		)
		CreatedEmployeeModel.decode(row, "create_employee")
		filed = STORE.tables["Employee"][row["name"]]
		self.assertEqual(filed["employee_name"], "Elena Marquez")
		self.assertEqual(filed["company"], MAIN)
		self.assertEqual(filed["status"], "Active")

	def test_28_search_employees(self):
		"""The list the foreman picks a returning picker out of, Left ones included."""
		self.the_hr_furniture()
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-RETURNED",
					"employee_name": "Rosa Aguilar",
					"first_name": "Rosa",
					"last_name": "Aguilar",
					"company": MAIN,
					"status": "Left",
					"date_of_joining": "2025-06-02",
					"employment_type": "Seasonal",
				}
			],
		)
		body = self.wire("search_employees", query="Rosa", company=MAIN)
		self.assertTrue(body["employees"], "the fixture seeded two Rosas and the search came back empty")
		for index, row in enumerate(body["employees"]):
			ExistingEmployeeModel.decode(row, "search_employees", f".employees[{index}]")
		found = {row["name"]: row for row in body["employees"]}
		# The one who LEFT is the one this search exists to find.
		self.assertEqual(found["EMP-RETURNED"]["status"], "Left")
		self.assertEqual(found["EMP-RETURNED"]["employment_type"], "Seasonal")
		self.assertIn(self.NEW_HIRE, found)

	def test_29_reactivate_employee(self):
		"""The other branch: one record with a history, not two with one between them."""
		self.the_hr_furniture()
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-RETURNED",
					"employee_name": "Rosa Aguilar",
					"first_name": "Rosa",
					"last_name": "Aguilar",
					"company": MAIN,
					"status": "Left",
					"date_of_joining": "2025-06-02",
				}
			],
		)
		row = self.wire("reactivate_employee", employee="EMP-RETURNED")
		VoidResponseModel.decode(row, "reactivate_employee")
		filed = STORE.tables["Employee"]["EMP-RETURNED"]
		self.assertEqual(filed["status"], "Active")
		self.assertEqual(str(filed["date_of_joining"]), frappe.utils.today())
		# The date it replaced is reported rather than lost — see the wrapper.
		self.assertIn(
			{"field": "status", "from": "Left", "to": "Active"},
			[dict(entry) for entry in row["changed"]],
		)

	# ── v0.46.2: the branch a returning seasonal worker takes ───────────────
	def the_compliance_columns(self):
		"""Employee as `compliance_fields.py` leaves a migrated site.

		Built from that module's own specs rather than restated, for the reason
		`test_farmops_api.py` gives: a flag or an option changed there has to be
		able to fail here."""
		from erpnext_mcp import compliance_fields

		from .harness import add_field

		for spec in compliance_fields.targets_by_doctype()["Employee"].fields:
			add_field(
				"Employee",
				spec.fieldname,
				fieldtype=spec.fieldtype,
				options=spec.options or None,
				label=spec.label,
				reqd=1 if spec.reqd else 0,
			)

	def the_i9_document_table(self):
		"""Four of the 24 `i9_documents.py` seeds, one per branch these tests take.

		SEEDED HERE RATHER THAN INSTALLED, because `install.py` runs against a
		bench and this suite does not. Four rather than 24 for the reason every
		fixture in this file is minimal: the tests below assert which list a
		document came back in, and 24 rows would make the assertion a transcription
		of `i9_documents.py` rather than a check of the grouping.
		"""
		STORE.seed(
			"I-9 Document Type",
			[
				{
					"name": "U.S. Passport",
					"doc_title": "U.S. Passport",
					"list_category": "A",
					"uscis_code": "passport",
					"requires_photo": 1,
					"enabled": 1,
					"description": "Unexpired U.S. passport",
				},
				{
					"name": "Employment Authorization Document (Form I-766)",
					"doc_title": "Employment Authorization Document (Form I-766)",
					"list_category": "A",
					"uscis_code": "i-766",
					"requires_photo": 1,
					"enabled": 1,
					"description": "Unexpired Employment Authorization Document",
				},
				{
					"name": "Driver's License",
					"doc_title": "Driver's License",
					"list_category": "B",
					"uscis_code": "drivers-license",
					"requires_photo": 1,
					"enabled": 1,
					"description": "State-issued driver's license",
				},
				{
					"name": "Social Security Card (Unrestricted)",
					"doc_title": "Social Security Card (Unrestricted)",
					"list_category": "C",
					"uscis_code": "ssn-card",
					"requires_photo": 0,
					"enabled": 1,
					"description": "Social Security Account Number card",
				},
			],
		)

	def a_documented_returning_picker(self, i9_status="Pending", w4_status="Missing"):
		"""Somebody who worked last season: an I-9 verified, a W-4 filed, a badge.

		THE COLUMNS ARE LEFT WHERE `create_employee` PUT THEM, because that is
		where a real site's are: nothing in this app has ever moved them. The
		records are Complete and Active, which is the disagreement the endpoint
		exists to resolve."""
		self.the_compliance_columns()
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-RETURNED",
					"employee_name": "Rosa Aguilar",
					"first_name": "Rosa",
					"last_name": "Aguilar",
					"company": MAIN,
					"status": "Left",
					"gender": "Female",
					"date_of_birth": "1991-04-18",
					"date_of_joining": "2025-06-02",
					"employment_type": "Seasonal Worker",
					"i9_status": i9_status,
					"w4_status": w4_status,
					"jurisdiction": "OR",
				}
			],
		)
		STORE.seed(
			"I-9 Form",
			[
				{
					"name": "I9-2025-RETURNED",
					"employee": "EMP-RETURNED",
					"company": MAIN,
					"status": "Complete",
					"hire_date": "2025-06-02",
					"verification_date": "2025-06-03",
				}
			],
		)
		STORE.seed(
			"W-4 Form",
			[
				{
					"name": "W4-2025-RETURNED",
					"employee": "EMP-RETURNED",
					"company": MAIN,
					"status": "Active",
					"tax_year": "2025",
					"effective_date": "2025-06-02",
				}
			],
		)
		STORE.seed(
			"Bucket Log Badge Map",
			[
				{
					"name": "BADGE-0042",
					"badge_id": "BADGE-0042",
					"employee": "EMP-RETURNED",
					"company": MAIN,
					"active": 1,
				}
			],
		)

	def test_30_get_employee(self):
		"""The record the wizard skips steps on, and the reconciliation it needs.

		THE VALUES ARE ASSERTED, NOT JUST THE TYPES. `satisfiedSteps`
		(`OnboardingModels.swift:352`) marks the I-9, the W-4 and the badge done
		from these three strings and nothing else — a type is what crashes, a
		value is what quietly re-collects an I-9 from somebody who has one."""
		self.the_hr_furniture()
		self.a_documented_returning_picker()

		row = self.wire("get_employee", employee="EMP-RETURNED")
		EmployeeDetailModel.decode(row, "get_employee")

		self.assertEqual(row["name"], "EMP-RETURNED")
		self.assertEqual(row["employee_name"], "Rosa Aguilar")
		self.assertEqual(row["status"], "Left")
		self.assertEqual(row["jurisdiction"], "OR")
		self.assertEqual(row["badge_id"], "BADGE-0042")
		# The columns say Pending/Missing on the stored row; the records answered.
		self.assertEqual(row["i9_status"], "Verified")
		self.assertEqual(row["w4_status"], "On-File")
		self.assertEqual(row["i9_status_recorded"], "Pending")
		self.assertEqual(row["w4_status_recorded"], "Missing")
		self.assertEqual(sorted(row["reconciled"]), ["i9_status", "w4_status"])
		self.assertEqual(row["i9"]["name"], "I9-2025-RETURNED")
		self.assertEqual(row["w4"]["name"], "W4-2025-RETURNED")

	def test_30_an_expired_i9_is_never_written_over_by_an_old_complete_record(self):
		"""The one direction that must not be reconciled. §1324a wants a returning
		worker re-verified when their authorization lapsed, and the record that
		says Complete is the very form that expired."""
		self.the_hr_furniture()
		self.a_documented_returning_picker(i9_status="Expired", w4_status="Requires-Update")

		row = self.wire("get_employee", employee="EMP-RETURNED")
		EmployeeDetailModel.decode(row, "get_employee")
		self.assertEqual(row["i9_status"], "Expired")
		self.assertEqual(row["w4_status"], "Requires-Update")
		self.assertEqual(row["reconciled"], [])
		# The records are still reported — the disagreement is visible, not hidden.
		self.assertTrue(row["i9_on_file"])
		self.assertTrue(row["w4_on_file"])

	def test_30_a_worker_with_no_history_needs_every_step(self):
		"""The other common case, and the one the columns are already right about."""
		self.the_hr_furniture()
		self.the_compliance_columns()
		frappe.db.set_value("Employee", self.NEW_HIRE, "i9_status", "Pending")
		frappe.db.set_value("Employee", self.NEW_HIRE, "w4_status", "Missing")

		row = self.wire("get_employee", employee=self.NEW_HIRE)
		EmployeeDetailModel.decode(row, "get_employee")
		self.assertEqual(row["i9_status"], "Pending")
		self.assertEqual(row["w4_status"], "Missing")
		self.assertIsNone(row["badge_id"])
		self.assertIsNone(row["i9"])
		self.assertIsNone(row["w4"])
		self.assertEqual(row["reconciled"], [])

	# ── v0.47.0: the I-9's two missing calls ────────────────────────────────
	def test_31_list_i9_document_types(self):
		"""The list the Section 2 picker has been hardcoding, as records.

		THE THREE LIST KEYS ARE THE CONTRACT, not `documents`. Section 2 asks
		"one from List A" OR "one from B and one from C", so a picker draws three
		sections and needs three keys it can rely on being there — including an
		empty one, because a missing `list_b` is a crash and an empty `list_b` is
		a section with nothing in it. That is asserted below against a site that
		has only List A documents enabled.
		"""
		self.the_hr_furniture()
		self.the_i9_document_table()

		row = self.wire("list_i9_document_types")
		UndecodedResponseModel.decode(row, "list_i9_document_types")

		self.assertEqual(row["count"], 4)
		self.assertEqual(
			[d["doc_title"] for d in row["list_a"]],
			["Employment Authorization Document (Form I-766)", "U.S. Passport"],
		)
		self.assertEqual([d["doc_title"] for d in row["list_b"]], ["Driver's License"])
		self.assertEqual(
			[d["doc_title"] for d in row["list_c"]], ["Social Security Card (Unrestricted)"]
		)
		self.assertIsNone(row["list_category"])
		# What a picker cell renders. A title with no `requires_photo` beside it
		# is a row the foreman cannot tell a photo document from.
		passport = next(d for d in row["list_a"] if d["doc_title"] == "U.S. Passport")
		self.assertEqual(passport["uscis_code"], "passport")
		self.assertEqual(passport["requires_photo"], 1)

	def test_31_a_filtered_list_still_carries_all_three_keys(self):
		self.the_hr_furniture()
		self.the_i9_document_table()

		row = self.wire("list_i9_document_types", list_category="a")
		self.assertEqual(row["list_category"], "A")
		self.assertEqual(row["count"], 2)
		self.assertEqual(row["list_b"], [])
		self.assertEqual(row["list_c"], [])

	def test_31_a_list_that_is_not_a_list_is_refused(self):
		self.the_hr_furniture()
		self.the_i9_document_table()
		with self.assertRaises(Exception) as caught:
			self.wire("list_i9_document_types", list_category="D")
		self.assertIn("is not an I-9 list", str(caught.exception))

	def test_32_reverify_i9(self):
		"""Section 3, on the record `get_employee` reports as expired.

		THIS IS THE PAIR test_30 LEFT OPEN. That test asserts a returning picker's
		expired I-9 is reported as expired and never written over; this is the call
		the wizard makes about it, and until v0.47.0 there was not one.
		"""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.a_documented_returning_picker(i9_status="Expired")

		row = self.wire(
			"reverify_i9",
			employee="EMP-RETURNED",
			reason="Work Authorization Expired",
			document_title="Employment Authorization Document (Form I-766)",
			document_number="SRC1234567890",
			issuing_authority="USCIS",
			document_expiry="2027-06-01",
			verifier_name="Ana Ramos",
			verifier_title="Farm Manager",
		)
		UndecodedResponseModel.decode(row, "reverify_i9")

		self.assertEqual(row["name"], "I9-2025-RETURNED")
		self.assertEqual(row["status"], "Complete")
		self.assertEqual(row["reverification_count"], 1)
		self.assertEqual(row["work_authorization_expiry"], "2027-06-01")
		self.assertFalse(row["receipt_pending"])

		# It APPENDED. The 2025 verification is still what the form says was
		# examined on the day of hire — the claim the child table exists for.
		filed = next(r for r in STORE.rows("I-9 Form") if r["name"] == "I9-2025-RETURNED")
		self.assertEqual(filed["verification_date"], "2025-06-03")
		self.assertEqual(len(filed["reverifications"]), 1)

		# THE ROUND TRIP, and the reason the whole feature exists. test_30 asserts
		# `get_employee` reports this picker's I-9 as Expired and refuses to
		# reconcile it away. After the reverification the same read has to stop
		# saying so, or the wizard sends a lawfully reverified worker to
		# `create_i9_form` — which refuses, because she has one.
		self.assertEqual(row["employee_i9_status"], "Verified")
		detail = self.wire("get_employee", employee="EMP-RETURNED")
		self.assertEqual(detail["i9_status"], "Verified")

	def test_32_a_reverification_of_a_form_with_no_section_2_is_refused(self):
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		with self.assertRaises(Exception) as caught:
			self.wire(
				"reverify_i9",
				employee=self.NEW_HIRE,
				reason="Work Authorization Expired",
				document_title="U.S. Passport",
				verifier_name="Ana Ramos",
			)
		self.assertIn("needs a first one to follow", str(caught.exception))

	# ── v0.47.1: the I-9 as a document ──────────────────────────────────────
	def test_33_get_i9_form(self):
		"""The read the wizard never had, and the four questions an audit asks.

		EVERY OTHER I-9 CALL HANDS BACK THE RECORD IT JUST WROTE. A foreman who
		opens the flow on somebody already verified could be told `Verified` by
		`get_employee` and nothing else — which documents, examined by whom, is
		anything still owed. All of it was on the server and none of it was
		reachable.
		"""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire(
			"submit_i9_section_1",
			employee=self.NEW_HIRE,
			citizenship_status="US Citizen",
			ssn_last_four="6789",
			address_street="1420 Orchard Road",
			address_city="Yakima",
			address_state="WA",
			address_zip="98901",
			date_of_birth="1994-03-11",
		)
		self.wire(
			"submit_i9_section_2",
			employee=self.NEW_HIRE,
			document_path="List A",
			list_a_doc_type="U.S. Passport",
			list_a_authority="US Dept of State",
			list_a_doc_number="P1234567",
			verifier_name="Ana Ramos",
			verifier_title="Farm Manager",
			verification_date=frappe.utils.today(),
		)

		row = self.wire("get_i9_form", employee=self.NEW_HIRE)
		UndecodedResponseModel.decode(row, "get_i9_form")

		self.assertEqual(row["employee"], self.NEW_HIRE)
		self.assertEqual(row["status"], "Complete")
		self.assertEqual(row["list_a_doc_title"], "U.S. Passport")
		self.assertEqual(row["verifier_name"], "Ana Ramos")
		self.assertEqual(row["address_city"], "Yakima")
		self.assertEqual(row["reverifications"], [])
		self.assertEqual(row["reverification_count"], 0)

	def test_33_the_ssn_comes_back_as_the_last_four_and_nothing_more(self):
		"""`_i9_fields` does not list `ssn_full` and argues why it never will."""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire(
			"submit_i9_section_1",
			employee=self.NEW_HIRE,
			citizenship_status="US Citizen",
			ssn_last_four="123-45-6789",
		)
		row = self.wire("get_i9_form", employee=self.NEW_HIRE)
		self.assertEqual(row["ssn_last_four"], "6789")
		self.assertNotIn("ssn_full", row)

	def test_33_a_worker_may_read_their_own_and_not_somebody_elses(self):
		"""The same one-record exception `get_employee` carries, and its limit.

		Ana is a Field Worker here — `the_hr_furniture` is deliberately NOT
		called — so the HR role is absent, which is the state a picker's phone is
		actually in.
		"""
		install_hrms()
		STORE.seed(
			"Employee",
			[
				{
					"name": "EMP-ANA",
					"employee_name": "Ana Ramos",
					"user_id": WORKER,
					"company": MAIN,
					"status": "Active",
				},
				{
					"name": "EMP-OTHER",
					"employee_name": "Marco Vega",
					"company": MAIN,
					"status": "Active",
				},
			],
		)
		STORE.seed(
			"I-9 Form",
			[
				{
					"name": "I9-2026-ANA",
					"employee": "EMP-ANA",
					"company": MAIN,
					"status": "Complete",
					"hire_date": "2026-04-01",
				},
				{
					"name": "I9-2026-OTHER",
					"employee": "EMP-OTHER",
					"company": MAIN,
					"status": "Complete",
					"hire_date": "2026-04-01",
				},
			],
		)
		self.be()

		mine = self.wire("get_i9_form", employee="EMP-ANA")
		self.assertEqual(mine["name"], "I9-2026-ANA")

		with self.assertRaises(Exception):
			self.wire("get_i9_form", employee="EMP-OTHER")

	@unittest.skipUnless(
		i9_pdf.available(),
		"filling the federal form needs pypdf and the shipped USCIS template; this bench "
		"has one or neither, which is what render_i9_pdf goes unavailable for.",
	)
	def test_34_generate_i9_pdf(self):
		"""The end of the onboarding flow: a URL the phone can print from."""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire(
			"submit_i9_section_1",
			employee=self.NEW_HIRE,
			citizenship_status="US Citizen",
			ssn_last_four="6789",
			date_of_birth="1994-03-11",
			address_street="1420 Orchard Road",
		)
		self.wire(
			"submit_i9_section_2",
			employee=self.NEW_HIRE,
			document_path="List A",
			list_a_doc_type="U.S. Passport",
			list_a_authority="US Dept of State",
			list_a_doc_number="P1234567",
			verifier_name="Ana Ramos",
			verification_date=frappe.utils.today(),
		)

		row = self.wire("generate_i9_pdf", employee=self.NEW_HIRE)
		UndecodedResponseModel.decode(row, "generate_i9_pdf")

		self.assertTrue(str(row["file_url"]).endswith(".pdf"))
		self.assertGreater(row["bytes"], 100_000)
		self.assertIn("1615-0047", row["edition"])
		self.assertEqual(row["incomplete"], [])
		self.assertEqual(row["reverifications_not_on_page"], 0)
		self.assertIn("8 CFR 274a.2(h)", row["note"])

		# The URL the phone was handed is the one on the record.
		name = frappe.db.get_value("I-9 Form", {"employee": self.NEW_HIRE}, "name")
		self.assertEqual(frappe.db.get_value("I-9 Form", name, "generated_pdf"), row["file_url"])

	def test_34_the_full_ssn_is_not_an_argument_a_handset_has(self):
		"""It would print a nine-digit number onto a page a phone could mail
		anywhere, and it needs a retention decision an operator makes."""
		import inspect

		self.assertNotIn(
			"include_full_ssn", inspect.signature(mobile_api.generate_i9_pdf).parameters
		)

	def test_35_upload_signed_i9(self):
		"""The photograph of the signed sheet, filed against the record.

		THE UPLOAD IS THE EVIDENCE PATH, unchanged: `stage_file_chunk` then
		`finalize_staged_file`, and the token that returns is what this call
		names. No bytes cross this endpoint.
		"""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		_staged, finalized = self.upload(kind="signed-i9", name="signed-i9.pdf")

		row = self.wire(
			"upload_signed_i9", employee=self.NEW_HIRE, file_token=finalized["file_token"]
		)
		UndecodedResponseModel.decode(row, "upload_signed_i9")

		self.assertEqual(row["file_token"], finalized["file_token"])
		self.assertTrue(row["signed_pdf"])
		self.assertIsNone(row["replaced"])

		name = frappe.db.get_value("I-9 Form", {"employee": self.NEW_HIRE}, "name")
		self.assertEqual(frappe.db.get_value("I-9 Form", name, "signed_pdf"), row["signed_pdf"])
		self.assertEqual(
			int(frappe.db.get_value("File", finalized["file_token"], "is_private") or 0), 1
		)

	def test_35_a_call_with_no_token_says_what_to_upload_first(self):
		self.the_hr_furniture()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		with self.assertRaises(Exception) as caught:
			self.wire("upload_signed_i9", employee=self.NEW_HIRE)
		self.assertIn("finalize_staged_file", str(caught.exception))

	# ── v0.47.2: what v0.47.0 taught the tool and the transport dropped ──────
	def test_36_the_i94_admission_number_reaches_the_record(self):
		"""v0.47.0 gave Section 1 three ways to identify an alien authorized to
		work and this wrapper carried one of them. A worker holding an I-94 and
		no A-Number filled the form on a phone and was refused for a field the
		transport had thrown away before the tool ever saw it."""
		self.the_hr_furniture()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		row = self.wire(
			"submit_i9_section_1",
			employee=self.NEW_HIRE,
			citizenship_status="Alien Authorized to Work",
			i94_admission_number="94123456789",
			work_authorization_expiry="2027-05-01",
		)
		self.assertEqual(row["status"], "Section 1 Complete")
		filed = next(iter(STORE.rows("I-9 Form")))
		self.assertEqual(filed["i94_admission_number"], "94123456789")
		self.assertEqual(str(filed["alien_work_authorization_expiry"]), "2027-05-01")

	def test_36_the_foreign_passport_pair_reaches_the_record_together(self):
		"""Both halves or neither. The tool refuses a passport number with no
		issuing country, and that refusal is only reachable if BOTH keys survive
		the transport — a wrapper carrying the number alone would turn a complete
		form into a permanent refusal."""
		self.the_hr_furniture()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire(
			"submit_i9_section_1",
			employee=self.NEW_HIRE,
			citizenship_status="Alien Authorized to Work",
			foreign_passport_number="X1234567",
			foreign_passport_country="Mexico",
			work_authorization_expiry="2027-05-01",
		)
		filed = next(iter(STORE.rows("I-9 Form")))
		self.assertEqual(filed["foreign_passport_number"], "X1234567")
		self.assertEqual(filed["foreign_passport_country"], "Mexico")

	def test_36_a_passport_with_no_country_is_still_the_tools_refusal(self):
		"""The point of forwarding the pair is that the tool gets to judge it."""
		self.the_hr_furniture()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		with self.assertRaises(Exception) as caught:
			self.wire(
				"submit_i9_section_1",
				employee=self.NEW_HIRE,
				citizenship_status="Alien Authorized to Work",
				foreign_passport_number="X1234567",
			)
		self.assertIn("foreign_passport_country", str(caught.exception))

	def test_37_a_receipt_examined_on_a_phone_is_recorded_as_a_receipt(self):
		"""THE ONE THAT MATTERS. Dropping the flag did not lose a nicety: it
		filed a receipt as though the document itself had been examined, which is
		a false attestation on a federal form, and the 90-day clock that says when
		the real document is owed never started."""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire("submit_i9_section_1", employee=self.NEW_HIRE, citizenship_status="US Citizen")
		row = self.wire(
			"submit_i9_section_2",
			employee=self.NEW_HIRE,
			document_path="List A",
			list_a_doc_type="U.S. Passport",
			list_a_authority="US Dept of State",
			list_a_doc_number="P1234567",
			list_a_is_receipt=True,
			verifier_name="Ana Ramos",
			verifier_title="Farm Manager",
			verification_date=frappe.utils.today(),
		)
		self.assertEqual(row["status"], "Complete")
		filed = next(iter(STORE.rows("I-9 Form")))
		self.assertTrue(int(filed["list_a_is_receipt"] or 0))
		self.assertTrue(int(filed["receipt_pending"] or 0))
		self.assertTrue(filed["receipt_expires_on"])

	def test_37_the_b_and_c_receipt_flags_travel_independently(self):
		"""Two documents, two answers. A worker may present the real driver's
		licence and a receipt for the replacement Social Security card, and a
		transport that carried one flag for both would record the wrong one."""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire("submit_i9_section_1", employee=self.NEW_HIRE, citizenship_status="US Citizen")
		self.wire(
			"submit_i9_section_2",
			employee=self.NEW_HIRE,
			document_path="List B + C",
			list_b_doc_type="Driver's License",
			list_b_authority="Oregon DMV",
			list_b_is_receipt=False,
			list_c_doc_type="Social Security Card (Unrestricted)",
			list_c_authority="SSA",
			list_c_is_receipt=True,
			verifier_name="Ana Ramos",
			verifier_title="Farm Manager",
			verification_date=frappe.utils.today(),
		)
		filed = next(iter(STORE.rows("I-9 Form")))
		self.assertFalse(int(filed["list_b_is_receipt"] or 0))
		self.assertTrue(int(filed["list_c_is_receipt"] or 0))

	def test_37_a_section_2_that_sends_no_flag_records_no_receipt(self):
		"""The pre-v0.47.2 caller, unchanged. An app that has not grown the
		checkbox sends nothing and gets the honest answer rather than a default
		that quietly asserts a receipt nobody saw."""
		self.the_hr_furniture()
		self.the_i9_document_table()
		self.wire("create_i9_form", employee=self.NEW_HIRE, company=MAIN, hire_date=frappe.utils.today())
		self.wire("submit_i9_section_1", employee=self.NEW_HIRE, citizenship_status="US Citizen")
		self.wire(
			"submit_i9_section_2",
			employee=self.NEW_HIRE,
			document_path="List A",
			list_a_doc_type="U.S. Passport",
			list_a_authority="US Dept of State",
			verifier_name="Ana Ramos",
			verification_date=frappe.utils.today(),
		)
		filed = next(iter(STORE.rows("I-9 Form")))
		self.assertFalse(int(filed["list_a_is_receipt"] or 0))
		self.assertFalse(int(filed["receipt_pending"] or 0))


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

	def test_a_created_employee_with_no_name_is_refused_by_the_step_that_needs_it(self):
		"""Five later calls are addressed to this string. Null here is v0.18.2 again."""
		with self.assertRaises(ContractError) as caught:
			CreatedEmployeeModel.decode({"name": None, "employee_id": "042"}, "create_employee")
		self.assertIn("v0.18.2", str(caught.exception))
		self.assertIn("OnboardingAPI.swift:16", str(caught.exception))

	def test_a_numeric_employee_id_is_refused_even_though_the_field_is_optional(self):
		"""The NULLABLE rule earning its place. `employee_number` is a Data field on
		a stock site and an Int on one where somebody made it a number — and
		`decodeIfPresent(String.self)` throws on the second, optional or not."""
		with self.assertRaises(ContractError) as caught:
			CreatedEmployeeModel.decode({"name": "HR-EMP-00042", "employee_id": 42}, "create_employee")
		self.assertIn("synthesized decoder", str(caught.exception))
		self.assertIn("OnboardingAPI.swift:17", str(caught.exception))

	def test_an_absent_employee_id_is_accepted_because_the_app_accepts_it(self):
		"""Not stricter than the app: a site that keeps no payroll numbers is fine,
		and the wizard falls back to the docname."""
		CreatedEmployeeModel.decode({"name": "HR-EMP-00042", "employee_id": None}, "create_employee")

	def test_a_search_row_with_no_employee_name_takes_the_whole_list_down(self):
		with self.assertRaises(ContractError) as caught:
			ExistingEmployeeModel.decode({"name": "EMP-1"}, "search_employees", ".employees[0]")
		self.assertIn("OnboardingModels.swift:275", str(caught.exception))
		self.assertIn("employees[0]", str(caught.exception))

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
	"""The test that fails when somebody adds a thirteenth method and no mirror.

	Without this, the suite silently stops covering the surface the moment it
	grows — which is precisely how the app got four undecodable releases while
	a green suite watched.
	"""

	#: Method name → the test that decodes it, by number.
	COVERED: ClassVar[dict[str, str]] = {
		"get_current_user_context": "test_01",
		"list_my_tasks": "test_02",
		"list_available_tasks": "test_03",
		"get_task": "test_04",
		"claim_task": "test_05",
		"start_task": "test_06",
		"complete_task_via_mobile": "test_07",
		"reject_task": "test_08",
		"list_compliance_alerts": "test_09",
		"report_field_task": "test_10",
		"stage_file_chunk": "test_11",
		"finalize_staged_file": "test_12",
		"scan_asset": "test_13",
		"get_asset_detail": "test_14",
		"log_asset_state_change": "test_15",
		"get_available_actions": "test_16",
		"report_asset_issue": "test_17",
		"create_i9_form": "test_18",
		"submit_i9_section_1": "test_19",
		"submit_i9_section_2": "test_20",
		"submit_w4": "test_21",
		"link_badge_to_employee": "test_22",
		"sync_bucket_entries": "test_23",
		"start_shift": "test_24",
		"add_worker_to_shift": "test_25",
		"end_shift": "test_26",
		"create_employee": "test_27",
		"search_employees": "test_28",
		"reactivate_employee": "test_29",
		"get_employee": "test_30",
		"list_i9_document_types": "test_31",
		"reverify_i9": "test_32",
		# v0.47.1 — the I-9 as a document.
		"get_i9_form": "test_33",
		"generate_i9_pdf": "test_34",
		"upload_signed_i9": "test_35",
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
					f"{method} claims to be covered by {prefix}_*, which is not in EveryMobileMethodDecodes.",
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
			VoidResponseModel,
			UndecodedResponseModel,
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
		""" "Housing Inspection HI-… created" closes the loop; "Done" does not."""
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
