# SPDX-License-Identifier: MIT
"""Employee badges: minting, printing, resolving, and refusing. v0.50.0.

WHAT THIS FILE IS ABOUT. The badge audit of 2026-08-07 found the pipeline about
sixty per cent built, and the missing forty concentrated in two places: nobody
ISSUED a badge on the ERPNext side, and nobody SENT a bucket capture off the
phone. This file covers the first of those and the validation that follows from
it; `test_bucket_bridge.py` still owns the sync's own arithmetic and
`fafo_ios/FarmOpsKit/Tests/…/BucketPipelineTests.swift` owns the handset half.

SEVEN CLAIMS.

1. `MintingBadgeIDs` — the pure half. A minted ID is readable, sequential, never
   reuses a retired number, and a register holding `farm_app` uuids alongside
   minted IDs does not confuse the counter.

2. `TheBadgeShapeContract` — `validate_badge_id` refuses the class of string
   that is not a badge (a URL, a Wi-Fi code, a JSON login payload) and accepts
   both a minted `ETC-0001` and a 36-character legacy uuid. Refusing the second
   would be a reprint of the whole workforce on upgrade day.

3. `IssuingABadge` — `generate_employee_badge_qr` mints, records and draws; it
   is IDEMPOTENT without `regenerate` (a reprint must not consume an
   identifier); `regenerate` retires the card it replaces, because a
   replacement that leaves its predecessor resolving is how a found badge keeps
   earning.

4. `PrintingASheet` — a crew at once, and one bad name does not lose the sheet.

5. `ResolvingABadge` — the read between a scan and a name, and the three
   refusals that are three different sentences on purpose: never issued,
   retired, and belonging to somebody who has left.

6. `TheStrictBadgePolicy` — what the phone sends. An unissued badge, a retired
   one and a picker who is not clocked in are each refused BY NAME and the rest
   of the batch is still filed. The default stays lenient, which is what keeps
   the deliberate `link_badge_to_employee` backfill working for a Desk import.

7. `EndToEnd` — issue a badge, scan it, log a bucket, sync it, link it to a
   shift, and find it in the payroll shape. The whole pipeline in one test,
   because every piece of it was individually tested before this release and
   the thing that was broken was the joins between them.
"""

import base64
import unittest
import unittest.mock
from typing import ClassVar

import frappe

from erpnext_mcp import bucket_bridge as engine
from erpnext_mcp import payroll_integration
from erpnext_mcp.render import qr
from erpnext_mcp.tools import badges

from .fixtures import MAIN, MAIN_ABBR, OTHER, SeededTestCase, install_hrms
from .harness import STORE

BADGE_DOCTYPE = "Bucket Log Badge Map"
ENTRY_DOCTYPE = "Bucket Log Entry"

EMP = "HR-EMP-00001"
EMP_NAME = "Ana Reyes"
SECOND = "HR-EMP-00002"
SECOND_NAME = "Marco Vega"
LEAVER = "HR-EMP-00009"

#: Every switch these tests touch. The two issuers are MUTATING and ship OFF;
#: `resolve_badge` is a read and ships ON, and is listed anyway so a test that
#: turns the others on does not accidentally document it as needing a switch.
ON = {
	f"allow_{name}": 1
	for name in (
		"generate_employee_badge_qr",
		"generate_employee_badge_sheet",
		"resolve_badge",
		"link_badge_to_employee",
		"sync_bucket_entries",
		"link_entries_to_shift",
		"start_shift",
		"add_worker_to_shift",
	)
}


def _entry(**overrides):
	base = {
		"entry_uuid": "11111111-1111-1111-1111-111111111111",
		"session_uuid": "S-1",
		"company": MAIN,
		"worker_badge": f"{MAIN_ABBR}-0001",
		"timestamp": "2026-06-01 08:00:00",
		"verdict": "Accepted",
		"coverage_percent": 92.5,
	}
	base.update(overrides)
	return base


# ── 1. The pure half: minting an identifier ──────────────────────────────


class MintingBadgeIDs(unittest.TestCase):
	def test_a_minted_id_is_the_prefix_and_a_padded_sequence(self):
		self.assertEqual(engine.format_badge_id("CF", 1), "CF-0001")
		self.assertEqual(engine.format_badge_id("CF", 42), "CF-0042")

	def test_a_sequence_past_four_digits_widens_rather_than_truncating(self):
		"""A wider card is untidy; a truncated badge ID is the wrong badge."""
		self.assertEqual(engine.format_badge_id("CF", 12345), "CF-12345")

	def test_the_prefix_is_the_abbreviation_cleaned_and_uppercased(self):
		self.assertEqual(engine.normalise_badge_prefix("etc"), "ETC")
		self.assertEqual(engine.normalise_badge_prefix("C.F. Farms"), "CFFARM")
		self.assertEqual(engine.normalise_badge_prefix("  cf-1 "), "CF1")

	def test_a_name_with_no_letters_or_digits_gives_no_prefix_rather_than_a_guess(self):
		self.assertEqual(engine.normalise_badge_prefix("---"), "")
		with self.assertRaises(ValueError):
			engine.format_badge_id("---", 1)

	def test_the_first_badge_under_a_prefix_is_one(self):
		self.assertEqual(engine.next_badge_id([], "CF"), "CF-0001")

	def test_it_counts_up_from_the_highest_rather_than_from_the_count(self):
		"""A gap left by a retired card is NEVER handed to the next picker —
		two people sharing one identifier makes the history unreadable."""
		self.assertEqual(engine.next_badge_id(["CF-0001", "CF-0002", "CF-0007"], "CF"), "CF-0008")

	def test_another_companys_badges_do_not_move_this_companys_counter(self):
		self.assertEqual(engine.next_badge_id(["HO-0099", "CF-0001"], "CF"), "CF-0002")

	def test_a_legacy_uuid_in_the_register_is_ignored_by_the_counter(self):
		"""The compatibility case: a site mid-transition holds both, and the
		string this app cannot read must not influence the number it picks."""
		register = ["3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2", "CF-0003", "BADGE-77"]
		self.assertEqual(engine.next_badge_id(register, "CF"), "CF-0004")

	def test_parsing_reads_a_minted_id_and_nothing_else(self):
		self.assertEqual(engine.parse_badge_sequence("CF-0042", "CF"), 42)
		self.assertIsNone(engine.parse_badge_sequence("CF-0042", "HO"))
		self.assertIsNone(engine.parse_badge_sequence("3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2"))
		self.assertIsNone(engine.parse_badge_sequence(""))


# ── 2. The shape contract ────────────────────────────────────────────────


class TheBadgeShapeContract(unittest.TestCase):
	def test_a_minted_id_is_a_badge(self):
		self.assertEqual(engine.validate_badge_id("ETC-0001"), [])

	def test_a_legacy_farm_app_uuid_is_still_a_badge(self):
		"""EVERY CARD ALREADY IN A POCKET IS ONE OF THESE. A format check that
		refused them would be a reprint of the workforce on upgrade day."""
		self.assertEqual(engine.validate_badge_id("3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2"), [])

	def test_a_hand_typed_badge_from_an_older_site_is_still_a_badge(self):
		self.assertEqual(engine.validate_badge_id("QR-0042"), [])
		self.assertEqual(engine.validate_badge_id("BADGE-77"), [])

	def test_a_login_qr_is_not_a_badge(self):
		"""THE SCAN THIS CHECK EXISTS FOR. `generate_mobile_login_qr` produces a
		JSON credential; scanned at the badge step it became a badge ID with an
		api_secret inside it."""
		payload = '{"url":"https://erp.example.com","api_key":"abc","api_secret":"s3cr3t"}'
		self.assertTrue(engine.validate_badge_id(payload))

	def test_the_refusal_does_not_repeat_the_credential_it_refused(self):
		"""A check whose whole job is catching a credential scanned by mistake
		must not quote it back — into a refusal, an audit row or a screen."""
		payload = '{"url":"https://erp.example.com","api_key":"abc","api_secret":"s3cr3t"}'
		reported = " ".join(engine.validate_badge_id(payload))
		self.assertNotIn("s3cr3t", reported)
		self.assertNotIn("api_secret", reported.replace("api_key:api_secret", ""))
		self.assertIn(str(len(payload)), reported, "it still says HOW MUCH was scanned")

	def test_a_near_miss_badge_is_quoted_whole_because_that_is_where_it_helps(self):
		self.assertIn("'ETC 0001'", " ".join(engine.validate_badge_id("ETC 0001")))

	def test_a_url_is_not_a_badge(self):
		self.assertTrue(engine.validate_badge_id("https://example.com/scan/1234"))

	def test_a_wifi_join_code_is_not_a_badge(self):
		self.assertTrue(engine.validate_badge_id("WIFI:S:OrchardNet;T:WPA;P:hunter2;;"))

	def test_a_key_secret_pair_is_not_a_badge(self):
		self.assertTrue(engine.validate_badge_id("d41d8cd98f00b204:e9800998ecf8427e"))

	def test_something_with_a_space_in_it_is_not_a_badge(self):
		self.assertTrue(engine.validate_badge_id("ETC 0001"))

	def test_too_short_and_too_long_are_both_refused(self):
		self.assertTrue(engine.validate_badge_id("A1"))
		self.assertTrue(engine.validate_badge_id("A" * 65))

	def test_empty_is_refused_without_raising(self):
		self.assertEqual(engine.validate_badge_id(None), ["badge ID is empty"])
		self.assertEqual(engine.validate_badge_id("   "), ["badge ID is empty"])

	def test_a_plain_barcode_passes_the_shape_check_and_is_the_registers_problem(self):
		"""The shape check cannot know a thirteen-digit number came off a soda
		can. `badge_admission_errors` is what refuses it, which is why there are
		two layers rather than one clever one."""
		self.assertEqual(engine.validate_badge_id("0123456789012"), [])


class AdmittingABadge(unittest.TestCase):
	"""`badge_admission_errors` — the rule the strict policy applies, pure."""

	FACTS: ClassVar[dict] = {"known": True, "active": True, "employee": EMP, "employee_status": "Active"}

	def test_a_live_badge_on_an_employed_person_is_admitted(self):
		self.assertEqual(engine.badge_admission_errors("ETC-0001", self.FACTS), [])

	def test_an_unissued_badge_is_refused_and_says_how_to_fix_it(self):
		errors = engine.badge_admission_errors("0123456789012", {"known": False})
		self.assertEqual(len(errors), 1)
		self.assertIn("not in this company's badge register", errors[0])
		self.assertIn("generate_employee_badge_qr", errors[0])

	def test_a_retired_badge_is_refused_differently_from_an_unissued_one(self):
		errors = engine.badge_admission_errors("ETC-0001", {**self.FACTS, "active": False})
		self.assertEqual(len(errors), 1)
		self.assertIn("retired", errors[0])

	def test_a_badge_belonging_to_somebody_who_has_left_is_refused(self):
		errors = engine.badge_admission_errors("ETC-0001", {**self.FACTS, "employee_status": "Left"})
		self.assertTrue(any("not employed" in error for error in errors))

	def test_a_picker_not_on_the_shift_is_refused_by_name(self):
		errors = engine.badge_admission_errors(
			"ETC-0001", {**self.FACTS, "shift": "SHIFT-0001", "on_shift": False}
		)
		self.assertTrue(any("not clocked in" in error for error in errors))
		self.assertTrue(any(EMP in error for error in errors))

	def test_the_roster_is_not_checked_when_no_shift_was_named(self):
		"""`on_shift=None` means the question was not asked, which is NOT the
		same as False — a rule that read it as False would refuse every sync
		that did not name a shift."""
		self.assertEqual(engine.badge_admission_errors("ETC-0001", {**self.FACTS, "on_shift": None}), [])

	def test_a_badge_shaped_like_nothing_never_reaches_the_register_questions(self):
		errors = engine.badge_admission_errors("https://example.com/x", {"known": True, "active": True})
		self.assertTrue(errors)
		self.assertFalse(any("register" in error for error in errors))


# ── the tool layer ───────────────────────────────────────────────────────


@unittest.skipUnless(qr.available(), "needs a QR encoder (segno or qrcode)")
class BadgeToolTestCase(SeededTestCase):
	"""A site with two active pickers, one leaver, and every switch on."""

	def setUp(self):
		super().setUp()
		install_hrms()
		self.configure(enabled=1, **ON)
		STORE.seed(
			"Employee",
			[
				{
					"name": EMP,
					"employee_name": EMP_NAME,
					"first_name": "Ana",
					"last_name": "Reyes",
					"company": MAIN,
					"status": "Active",
					"designation": "Picker",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": SECOND,
					"employee_name": SECOND_NAME,
					"first_name": "Marco",
					"last_name": "Vega",
					"company": MAIN,
					"status": "Active",
					"designation": "Picker",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": LEAVER,
					"employee_name": "Sam Ortiz",
					"first_name": "Sam",
					"last_name": "Ortiz",
					"company": MAIN,
					"status": "Left",
					"date_of_joining": "2024-05-01",
				},
			],
		)

	def issue(self, employee=EMP, **overrides):
		payload = {"employee": employee, "company": MAIN}
		payload.update(overrides)
		return self.tool_data("generate_employee_badge_qr", payload)

	def badge_rows(self):
		return STORE.rows(BADGE_DOCTYPE)


# ── 3. Issuing ───────────────────────────────────────────────────────────


class IssuingABadge(BadgeToolTestCase):
	def test_a_badge_is_minted_recorded_and_drawn(self):
		result = self.issue()
		self.assertEqual(result["badge_id"], f"{MAIN_ABBR}-0001")
		self.assertTrue(result["created"])
		self.assertEqual(result["employee"], EMP)
		self.assertEqual(result["employee_name"], EMP_NAME)
		self.assertEqual(result["company"], MAIN)
		# The register row is what a scan resolves through.
		self.assertEqual(len(self.badge_rows()), 1)
		row = self.badge_rows()[0]
		self.assertEqual(row["employee"], EMP)
		self.assertTrue(row["active"])

	def test_the_png_is_a_real_png_carrying_the_badge_id(self):
		result = self.issue()
		png = base64.b64decode(result["png_base64"])
		self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
		self.assertEqual(len(png), result["png_bytes"])
		self.assertGreater(result["modules"], 0)
		# The matrix is the same code, and it is the badge ID that was encoded.
		matrix = self.issue(format="matrix")["matrix"]
		self.assertEqual(len(matrix), result["modules"])

	def test_the_card_carries_what_a_print_template_needs(self):
		result = self.issue()
		self.assertEqual(result["designation"], "Picker")
		self.assertEqual(result["photo_placeholder"], "AR")
		self.assertIn("photo_url", result)

	def test_the_second_badge_on_the_site_is_the_next_number(self):
		self.issue()
		second = self.issue(employee=SECOND)
		self.assertEqual(second["badge_id"], f"{MAIN_ABBR}-0002")

	def test_reprinting_gives_back_the_same_badge_rather_than_a_second_one(self):
		"""IDEMPOTENT ON PURPOSE. A card that went through a wash cycle is
		reprinted; consuming an identifier for that would leave the register
		full of badges nobody holds."""
		first = self.issue()
		again = self.issue()
		self.assertEqual(again["badge_id"], first["badge_id"])
		self.assertFalse(again["created"])
		self.assertTrue(again["reused"])
		self.assertEqual(len(self.badge_rows()), 1)

	def test_regenerate_mints_a_new_id_and_retires_the_one_it_replaces(self):
		"""THE LOST-CARD PATH. A replacement that leaves its predecessor
		resolving is how a badge found in an orchard keeps earning."""
		first = self.issue()
		replacement = self.issue(regenerate=True)
		self.assertNotEqual(replacement["badge_id"], first["badge_id"])
		self.assertTrue(replacement["created"])
		self.assertEqual(replacement["retired_badges"], [first["badge_id"]])

		by_id = {row["name"]: row for row in self.badge_rows()}
		self.assertFalse(by_id[first["badge_id"]]["active"])
		self.assertTrue(by_id[replacement["badge_id"]]["active"])

	def test_a_retired_number_is_never_reissued(self):
		first = self.issue()
		second = self.issue(regenerate=True)
		third = self.issue(regenerate=True)
		self.assertEqual(
			[first["badge_id"], second["badge_id"], third["badge_id"]],
			[f"{MAIN_ABBR}-0001", f"{MAIN_ABBR}-0002", f"{MAIN_ABBR}-0003"],
		)

	def test_an_existing_printed_card_can_be_adopted_instead_of_minted(self):
		"""The migration path §7 of the audit argued for: an old `farm_app` uuid
		and a new minted ID both resolve, so the cutover is incremental."""
		legacy = "3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2"
		result = self.issue(badge_id=legacy)
		self.assertEqual(result["badge_id"], legacy)
		self.assertTrue(frappe.db.exists(BADGE_DOCTYPE, legacy))

	def test_a_card_already_live_for_somebody_else_is_refused(self):
		"""DUPLICATE BADGES ARE ONE PERSON'S PIECEWORK PAID TO ANOTHER."""
		first = self.issue()
		error = self.tool_error(
			"generate_employee_badge_qr",
			{"employee": SECOND, "company": MAIN, "badge_id": first["badge_id"]},
		)
		self.assertIn("already live", error)
		self.assertIn(EMP, error)

	def test_a_string_that_is_not_a_badge_cannot_be_adopted(self):
		error = self.tool_error(
			"generate_employee_badge_qr",
			{"employee": EMP, "company": MAIN, "badge_id": "https://example.com/x"},
		)
		self.assertIn("not a usable badge", error)
		self.assertEqual(self.badge_rows(), [])

	def test_the_qr_is_drawn_before_the_register_is_written(self):
		"""ORDER MATTERS ON A BENCH THAT CANNOT DRAW. Consuming an identifier and
		then failing to render leaves a register holding a badge nobody holds."""
		with unittest.mock.patch.object(
			qr, "qr_matrix", side_effect=RuntimeError("no encoder on this bench")
		):
			error = self.tool_error("generate_employee_badge_qr", {"employee": EMP, "company": MAIN})
		self.assertIn("no encoder", error)
		self.assertEqual(self.badge_rows(), [], "no badge was issued for a card nobody can print")

	def test_a_badge_is_not_issued_to_somebody_who_has_left(self):
		error = self.tool_error("generate_employee_badge_qr", {"employee": LEAVER, "company": MAIN})
		self.assertIn("Left", error)
		self.assertEqual(self.badge_rows(), [])

	def test_a_badge_is_not_issued_against_another_entitys_name(self):
		error = self.tool_error("generate_employee_badge_qr", {"employee": EMP, "company": OTHER})
		self.assertIn("employed by", error)
		self.assertEqual(self.badge_rows(), [])

	def test_issuing_backfills_captures_already_synced_against_an_adopted_badge(self):
		"""The reason issuing goes through `link_badge_to_employee` rather than
		writing the row itself: a card mapped after a morning of picking claims
		what was already scanned with it."""
		legacy = "3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2"
		self.tool_data("sync_bucket_entries", {"entries": [_entry(worker_badge=legacy)]})
		self.assertIsNone(STORE.rows(ENTRY_DOCTYPE)[0]["employee"])

		result = self.issue(badge_id=legacy)
		self.assertEqual(len(result["backfilled_entries"]), 1)
		self.assertEqual(STORE.rows(ENTRY_DOCTYPE)[0]["employee"], EMP)

	def test_the_switch_off_refuses_the_call(self):
		self.configure(enabled=1)
		self.tool_error("generate_employee_badge_qr", {"employee": EMP, "company": MAIN})


# ── 4. Printing a sheet ──────────────────────────────────────────────────


class PrintingASheet(BadgeToolTestCase):
	def test_a_crew_gets_a_card_each(self):
		result = self.tool_data(
			"generate_employee_badge_sheet", {"employees": [EMP, SECOND], "company": MAIN}
		)
		self.assertEqual(result["card_count"], 2)
		self.assertEqual(result["issued_count"], 2)
		self.assertEqual(result["error_count"], 0)
		self.assertEqual(
			[card["badge_id"] for card in result["cards"]],
			[f"{MAIN_ABBR}-0001", f"{MAIN_ABBR}-0002"],
		)
		for card in result["cards"]:
			self.assertTrue(base64.b64decode(card["png_base64"]).startswith(b"\x89PNG"))
			self.assertTrue(card["employee_name"])
			self.assertTrue(card["photo_placeholder"])

	def test_somebody_who_already_holds_a_badge_gets_that_one_on_the_sheet(self):
		existing = self.issue()["badge_id"]
		result = self.tool_data(
			"generate_employee_badge_sheet", {"employees": [EMP, SECOND], "company": MAIN}
		)
		by_employee = {card["employee"]: card for card in result["cards"]}
		self.assertEqual(by_employee[EMP]["badge_id"], existing)
		self.assertFalse(by_employee[EMP]["created"])
		self.assertTrue(by_employee[SECOND]["created"])
		self.assertEqual(result["issued_count"], 1)

	def test_one_bad_name_does_not_lose_the_sheet(self):
		"""A hiring day with one bad row in the list is not a hiring day with
		no badges — the same posture `sync_bucket_entries` takes for a batch."""
		result = self.tool_data(
			"generate_employee_badge_sheet",
			{"employees": [EMP, "HR-EMP-NOBODY", LEAVER, SECOND], "company": MAIN},
		)
		self.assertEqual(result["card_count"], 2)
		self.assertEqual(result["error_count"], 2)
		reasons = " ".join(error["error"] for error in result["errors"])
		self.assertIn("no Employee", reasons)
		self.assertIn("Left", reasons)

	def test_naming_somebody_twice_prints_one_card_and_says_so(self):
		result = self.tool_data("generate_employee_badge_sheet", {"employees": [EMP, EMP], "company": MAIN})
		self.assertEqual(result["card_count"], 1)
		self.assertEqual(result["error_count"], 1)
		self.assertIn("twice", result["errors"][0]["error"])

	def test_a_sheet_past_the_cap_is_refused_rather_than_truncated(self):
		error = self.tool_error(
			"generate_employee_badge_sheet",
			{"employees": [EMP] * 101, "company": MAIN},
		)
		self.assertIn("more than one sheet call produces", error)

	def test_an_empty_list_is_refused(self):
		self.tool_error("generate_employee_badge_sheet", {"employees": [], "company": MAIN})


# ── 5. Resolving ─────────────────────────────────────────────────────────


class ResolvingABadge(BadgeToolTestCase):
	def test_a_live_badge_answers_with_the_person(self):
		badge = self.issue()["badge_id"]
		result = self.tool_data("resolve_badge", {"badge_id": badge})
		self.assertEqual(result["employee"], EMP)
		self.assertEqual(result["employee_name"], EMP_NAME)
		self.assertEqual(result["designation"], "Picker")
		self.assertEqual(result["status"], "Active")
		self.assertTrue(result["active"])
		self.assertEqual(result["photo_placeholder"], "AR")

	def test_an_unissued_badge_is_refused_by_name(self):
		error = self.tool_error("resolve_badge", {"badge_id": "ETC-9999"})
		self.assertIn("no badge", error)
		self.assertIn("ETC-9999", error)

	def test_a_retired_badge_gets_its_own_sentence(self):
		"""THREE REFUSALS, THREE SITUATIONS, THREE FIXES. Collapsing them into
		'not found' is what makes a foreman rescan a perfectly good card."""
		first = self.issue()["badge_id"]
		self.issue(regenerate=True)
		error = self.tool_error("resolve_badge", {"badge_id": first})
		self.assertIn("retired", error)
		self.assertIn(EMP, error)

	def test_a_badge_belonging_to_somebody_who_has_left_is_refused(self):
		badge = self.issue()["badge_id"]
		frappe.db.set_value("Employee", EMP, "status", "Left")
		error = self.tool_error("resolve_badge", {"badge_id": badge})
		self.assertIn("Left", error)

	def test_a_string_that_is_not_a_badge_is_refused_before_the_register_is_read(self):
		error = self.tool_error("resolve_badge", {"badge_id": "https://example.com/scan/1"})
		self.assertIn("is not a badge ID", error)

	def test_a_badge_from_another_entity_reads_as_unknown_rather_than_wrong_company(self):
		"""Confirming a badge exists somewhere else on the site is a fact this
		read has no reason to hand to whoever is holding a card."""
		badge = self.issue()["badge_id"]
		error = self.tool_error("resolve_badge", {"badge_id": badge, "company": OTHER})
		self.assertIn("no badge", error)
		self.assertNotIn("Example Trading", error)

	def test_a_shift_turns_the_answer_into_an_admission(self):
		badge = self.issue()["badge_id"]
		shift = self.tool_data("start_shift", {"foreman": SECOND, "company": MAIN, "crew_employees": [EMP]})[
			"name"
		]
		result = self.tool_data("resolve_badge", {"badge_id": badge, "shift": shift})
		self.assertTrue(result["on_shift"])
		self.assertEqual(result["shift"], shift)
		self.assertIsNotNone(result["joined_at"])

	def test_somebody_not_on_the_crew_answers_on_shift_false_rather_than_refusing(self):
		badge = self.issue()["badge_id"]
		shift = self.tool_data("start_shift", {"foreman": SECOND, "company": MAIN, "crew_employees": []})[
			"name"
		]
		result = self.tool_data("resolve_badge", {"badge_id": badge, "shift": shift})
		self.assertFalse(result["on_shift"])
		self.assertEqual(result["shift_state"], "open")

	def test_a_shift_name_the_phone_is_stale_about_answers_rather_than_throwing(self):
		badge = self.issue()["badge_id"]
		result = self.tool_data("resolve_badge", {"badge_id": badge, "shift": "SHIFT-NOPE"})
		self.assertFalse(result["on_shift"])
		self.assertEqual(result["shift_state"], "no such shift")


# ── 6. The strict policy ─────────────────────────────────────────────────


class TheStrictBadgePolicy(BadgeToolTestCase):
	def test_the_default_is_still_lenient_so_a_desk_import_backfills(self):
		"""THE v0.44.0 BEHAVIOUR IS THE DEFAULT AND STAYS THERE. A capture taken
		before anybody mapped the card is filed with no employee and
		`link_badge_to_employee` claims it later; refusing it would lose a
		morning of picking to an administrative gap."""
		result = self.tool_data("sync_bucket_entries", {"entries": [_entry(worker_badge="QR-0404")]})
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(result["badge_policy"], "lenient")
		self.assertIsNone(result["created"][0]["employee"])

	def test_a_malformed_badge_is_refused_under_the_lenient_policy_too(self):
		"""The SHAPE check is not the policy. Nothing with a colon or a brace in
		it was ever a badge, and no later mapping will rescue the entry —
		nobody is going to map a Wi-Fi join code to a picker."""
		result = self.tool_data(
			"sync_bucket_entries",
			{"entries": [_entry(worker_badge="WIFI:S:OrchardNet;T:WPA;P:hunter2;;")]},
		)
		self.assertEqual(result["created_count"], 0)
		self.assertEqual(result["invalid_count"], 1)

	def test_strict_refuses_an_unissued_badge_and_says_how_to_fix_it(self):
		result = self.tool_data(
			"sync_bucket_entries",
			{"entries": [_entry(worker_badge="0123456789012")], "badge_policy": "strict"},
		)
		self.assertEqual(result["created_count"], 0)
		self.assertEqual(result["invalid_count"], 1)
		self.assertIn("not in this company's badge register", result["invalid"][0]["errors"][0])

	def test_strict_files_a_capture_whose_badge_was_properly_issued(self):
		badge = self.issue()["badge_id"]
		result = self.tool_data(
			"sync_bucket_entries",
			{"entries": [_entry(worker_badge=badge)], "badge_policy": "strict"},
		)
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(result["created"][0]["employee"], EMP)

	def test_strict_refuses_a_retired_badge(self):
		first = self.issue()["badge_id"]
		self.issue(regenerate=True)
		result = self.tool_data(
			"sync_bucket_entries",
			{"entries": [_entry(worker_badge=first)], "badge_policy": "strict"},
		)
		self.assertEqual(result["invalid_count"], 1)
		self.assertIn("retired", result["invalid"][0]["errors"][0])

	def test_strict_refuses_a_badge_belonging_to_somebody_who_has_left(self):
		badge = self.issue()["badge_id"]
		frappe.db.set_value("Employee", EMP, "status", "Left")
		result = self.tool_data(
			"sync_bucket_entries",
			{"entries": [_entry(worker_badge=badge)], "badge_policy": "strict"},
		)
		self.assertEqual(result["invalid_count"], 1)
		self.assertIn("not employed", result["invalid"][0]["errors"][0])

	def test_one_bad_capture_does_not_lose_the_batch(self):
		badge = self.issue()["badge_id"]
		result = self.tool_data(
			"sync_bucket_entries",
			{
				"entries": [
					_entry(worker_badge=badge),
					_entry(
						entry_uuid="22222222-2222-2222-2222-222222222222",
						worker_badge="0123456789012",
					),
				],
				"badge_policy": "strict",
			},
		)
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(result["invalid_count"], 1)

	def test_a_picker_not_clocked_in_is_refused_by_name(self):
		badge = self.issue()["badge_id"]
		shift = self.tool_data("start_shift", {"foreman": SECOND, "company": MAIN, "crew_employees": []})[
			"name"
		]
		result = self.tool_data(
			"sync_bucket_entries",
			{"entries": [_entry(worker_badge=badge)], "badge_policy": "strict", "shift": shift},
		)
		self.assertEqual(result["invalid_count"], 1)
		self.assertIn("not clocked in", result["invalid"][0]["errors"][0])
		self.assertIn(EMP, result["invalid"][0]["errors"][0])

	def test_a_picker_who_is_clocked_in_is_filed(self):
		badge = self.issue()["badge_id"]
		shift = self.tool_data("start_shift", {"foreman": SECOND, "company": MAIN, "crew_employees": [EMP]})[
			"name"
		]
		result = self.tool_data(
			"sync_bucket_entries",
			{"entries": [_entry(worker_badge=badge)], "badge_policy": "strict", "shift": shift},
		)
		self.assertEqual(result["created_count"], 1)
		self.assertEqual(result["shift"], shift)

	def test_a_shift_check_under_the_lenient_policy_is_refused_as_meaningless(self):
		"""A rule that never refuses anything is worse than no rule: it reads
		as a check that ran."""
		error = self.tool_error("sync_bucket_entries", {"entries": [_entry()], "shift": "SHIFT-0001"})
		self.assertIn("strict-policy check", error)

	def test_an_unknown_policy_is_refused(self):
		error = self.tool_error("sync_bucket_entries", {"entries": [_entry()], "badge_policy": "whatever"})
		self.assertIn("badge_policy must be one of", error)

	def test_a_closed_shift_cannot_be_validated_against(self):
		shift = self.tool_data("start_shift", {"foreman": SECOND, "company": MAIN, "crew_employees": [EMP]})[
			"name"
		]
		frappe.db.set_value("Farm Shift", shift, "end_datetime", "2026-06-01 18:00:00")
		error = self.tool_error(
			"sync_bucket_entries",
			{"entries": [_entry()], "badge_policy": "strict", "shift": shift},
		)
		self.assertIn("closed at", error)


# ── 7. The whole pipeline ────────────────────────────────────────────────


class EndToEnd(BadgeToolTestCase):
	def test_issue_a_badge_scan_it_log_a_bucket_and_be_paid_for_it(self):
		"""EVERY PIECE OF THIS WAS TESTED BEFORE v0.50.0 AND THE PIPELINE DID
		NOT WORK, because what was missing was the joins: nobody issued a badge
		and nobody sent a capture. This is the join, asserted end to end."""
		# 1. A hire gets a card.
		issued = self.issue()
		badge = issued["badge_id"]
		self.assertEqual(badge, f"{MAIN_ABBR}-0001")
		self.assertTrue(base64.b64decode(issued["png_base64"]).startswith(b"\x89PNG"))

		# 2. A foreman opens a shift and the picker clocks in.
		shift = self.tool_data("start_shift", {"foreman": SECOND, "company": MAIN, "crew_employees": [EMP]})[
			"name"
		]

		# 3. The card is scanned at a bin trailer: the crew clock's own read.
		scanned = self.tool_data("resolve_badge", {"badge_id": badge, "shift": shift})
		self.assertEqual(scanned["employee"], EMP)
		self.assertTrue(scanned["on_shift"])

		# 4. Three captures, synced under the strict policy. THE COVERAGES ARE
		# DELIBERATELY ALL DIFFERENT AND DELIBERATELY IRRELEVANT: the gate is
		# binary, so the barely-full Accepted bucket at 51% and the brimming one
		# at 99% are each worth exactly one, and the Rejected one at 97% is worth
		# nothing however full the model thought it looked.
		entries = [
			_entry(
				entry_uuid="e1111111-1111-1111-1111-111111111111",
				worker_badge=badge,
				timestamp="2026-06-01 08:00:00",
				coverage_percent=51.0,
			),
			_entry(
				entry_uuid="e2222222-2222-2222-2222-222222222222",
				worker_badge=badge,
				timestamp="2026-06-01 08:05:00",
				coverage_percent=99.0,
			),
			_entry(
				entry_uuid="e3333333-3333-3333-3333-333333333333",
				worker_badge=badge,
				verdict="Rejected",
				timestamp="2026-06-01 08:10:00",
				coverage_percent=97.0,
			),
		]
		synced = self.tool_data(
			"sync_bucket_entries",
			{"entries": entries, "badge_policy": "strict", "shift": shift},
		)
		self.assertEqual(synced["created_count"], 3)
		self.assertEqual(synced["invalid_count"], 0, synced["invalid"])

		# 5. Resending the same batch is a no-op, not a double payment.
		again = self.tool_data(
			"sync_bucket_entries",
			{"entries": entries, "badge_policy": "strict", "shift": shift},
		)
		self.assertEqual(again["created_count"], 0)
		self.assertEqual(again["duplicate_count"], 3)

		# 6. The session's totals are computed from the captures themselves.
		session = self.tool_data("get_bucket_session", {"session": "S-1"})
		self.assertEqual(session["total_accepted"], 2)
		self.assertEqual(session["total_rejected"], 1)
		self.assertEqual(session["employee"], EMP)

		# 7. The captures are linked to the shift payroll will read.
		linked = self.tool_data("link_entries_to_shift", {"shift": shift, "session": "S-1"})
		self.assertEqual(len(linked["linked"]), 3)

		# 8. And only the ACCEPTED, attributed ones become piece units.
		rows = [dict(row) for row in STORE.rows(ENTRY_DOCTYPE)]
		shaped = engine.entries_to_payroll_shape(rows)
		self.assertEqual(len(shaped), 2)
		self.assertEqual({row["employee"] for row in shaped}, {EMP})

		# 9. Which is exactly the shape `payroll_integration` reads off a shift —
		# TWO WHOLE BUCKETS. Not 1.5, which is what 51% + 99% would come to if
		# anything anywhere multiplied by coverage.
		units = payroll_integration._piece_units_for({"bucket_logs": shaped}, EMP)
		self.assertEqual(units, 2.0)
		self.assertEqual(units, float(int(units)), "pay is a whole number of buckets")
		for row in shaped:
			self.assertNotIn("coverage_percent", row)

		# 10. And the summary a foreman asks for agrees with all of it.
		summary = self.tool_data(
			"get_piecework_summary",
			{"employee": EMP, "from_date": "2026-06-01", "to_date": "2026-06-01"},
		)
		self.assertEqual(summary["piece_units"], 2)
		self.assertEqual(summary["total_rejected"], 1)

	def test_a_soda_can_scanned_at_the_bin_trailer_never_becomes_piece_work(self):
		"""The other half of the same story, and the one the audit named."""
		self.issue()
		shift = self.tool_data("start_shift", {"foreman": SECOND, "company": MAIN, "crew_employees": [EMP]})[
			"name"
		]
		result = self.tool_data(
			"sync_bucket_entries",
			{
				"entries": [_entry(worker_badge="0123456789012")],
				"badge_policy": "strict",
				"shift": shift,
			},
		)
		self.assertEqual(result["created_count"], 0)
		self.assertEqual(STORE.rows(ENTRY_DOCTYPE), [])
		self.assertEqual(
			engine.entries_to_payroll_shape([dict(row) for row in STORE.rows(ENTRY_DOCTYPE)]), []
		)


# ── registration ─────────────────────────────────────────────────────────


class ToolRegistration(unittest.TestCase):
	def setUp(self):
		from erpnext_mcp import registry

		self.registry = registry

	def test_the_three_tools_are_registered(self):
		for name in ("generate_employee_badge_qr", "generate_employee_badge_sheet", "resolve_badge"):
			with self.subTest(tool=name):
				self.assertIn(name, self.registry.TOOLS)

	def test_the_two_issuers_are_mutating_and_the_read_is_not(self):
		self.assertIn("generate_employee_badge_qr", self.registry.MUTATING_TOOLS)
		self.assertIn("generate_employee_badge_sheet", self.registry.MUTATING_TOOLS)
		self.assertIn("resolve_badge", self.registry.READ_TOOLS)

	def test_the_issuers_need_a_qr_encoder_and_the_read_does_not(self):
		"""A bench with no encoder loses the two that DRAW and keeps the one
		that turns a scan into a name, which needs no drawing at all."""
		for name in ("generate_employee_badge_qr", "generate_employee_badge_sheet"):
			with self.subTest(tool=name):
				self.assertIn("segno", self.registry.TOOLS[name]["requires"])
		self.assertNotIn("segno", self.registry.TOOLS["resolve_badge"]["requires"])


# ── 8. the card has to survive an orchard ────────────────────────────────


class TheBadgeQRIsBuiltForTheField(BadgeToolTestCase):
	"""v0.51.0. A login QR is held up to a screen for ten seconds; a badge lives
	in a picker's back pocket through a cherry harvest and is read at a bin
	trailer in bright sun by a phone with a dusty lens. The settings differ.
	"""

	def test_a_badge_is_error_correction_H_and_the_app_default_is_not(self):
		"""30% of the symbol recoverable rather than 15%. That is the difference
		between a scuffed, creased, muddy card that still resolves and one a
		foreman has to read out over a radio."""
		self.assertEqual(badges.BADGE_ERROR_CORRECTION, "H")
		self.assertEqual(self.issue()["error_correction"], "H")
		# Not a change to every QR this app draws — the login QR is still M.
		self.assertEqual(qr.render("anything")["error_correction"], "M")

	def test_H_costs_no_extra_modules_for_a_badge_this_app_mints(self):
		"""The reason H is affordable here. `CF-0001` is seven alphanumeric
		characters and fits a version-1 symbol at H, so the denser correction
		buys durability without buying a bigger card."""
		self.assertEqual(
			len(qr.qr_matrix(f"{MAIN_ABBR}-0001", error="H")),
			len(qr.qr_matrix(f"{MAIN_ABBR}-0001", error="M")),
		)

	def test_the_png_carries_enough_pixels_for_a_crisp_inch_and_a_half(self):
		"""A printer scaling a QR up from too few pixels softens the module
		edges, and soft edges cost more scans than the dirt does."""
		card = self.issue()
		self.assertGreaterEqual(
			card["print"]["dpi_at_min_width"],
			300.0,
			"a 1.5in badge would be printed below 300dpi and interpolated",
		)
		self.assertEqual(card["print"]["min_width_inches"], 1.5)

	def test_the_scale_is_computed_from_the_symbol_and_not_hardcoded(self):
		"""A longer company prefix pushes the badge into a version-2 symbol. A
		fixed scale would print the same 1.5in card at two-thirds the
		resolution and nobody would notice."""
		small = badges._badge_scale(21)
		larger = badges._badge_scale(25)
		self.assertGreater(small, larger, "a denser symbol needs a smaller scale")
		for modules in (21, 25, 29, 33):
			with self.subTest(modules=modules):
				pixels = (modules + 2 * qr.BORDER) * badges._badge_scale(modules)
				self.assertGreaterEqual(pixels / badges.BADGE_PRINT_INCHES, 300.0)

	def test_the_quiet_zone_is_four_modules_and_is_reported(self):
		"""The specification's own minimum. A renderer that crops or insets the
		image is destroying the thing that makes the symbol findable, so the
		payload says how much white is load-bearing."""
		card = self.issue()
		self.assertEqual(card["print"]["quiet_zone_modules"], 4)
		self.assertEqual(qr.BORDER, 4)

	def test_the_card_states_black_on_white_because_the_card_may_not_be(self):
		"""A colored badge card is fine; a colored QR is not. The symbol needs a
		white box under it whatever is printed behind it."""
		spec = self.issue()["print"]
		self.assertEqual(spec["foreground"], "#000000")
		self.assertEqual(spec["background"], "#FFFFFF")

	def test_the_human_readable_id_travels_with_the_card_and_goes_below_it(self):
		"""Every scanner eventually fails on a card that went through a wash
		cycle, and a badge nobody can read aloud is a picker whose buckets go
		unattributed for the morning. This is why the app mints CF-0001 rather
		than adopting farm_app's uuid."""
		card = self.issue()
		self.assertEqual(card["print"]["caption"], card["badge_id"])
		self.assertEqual(card["print"]["caption_position"], "below")

	def test_the_printed_sheet_gets_the_same_treatment_as_the_single_card(self):
		"""The sheet is what a hiring day actually prints, and it used to take
		`_render`'s default while the single card took an argument."""
		data = self.tool_data(
			"generate_employee_badge_sheet", {"employees": [EMP], "company": MAIN}
		)
		card = data["cards"][0]
		self.assertEqual(card["error_correction"], "H")
		self.assertGreaterEqual(card["print"]["dpi_at_min_width"], 300.0)
		self.assertEqual(card["print"]["caption"], card["badge_id"])

	def test_a_site_may_still_choose_a_looser_correction_for_its_own_stock(self):
		"""Overridable, because a site printing onto something smaller than a
		badge may need the density back."""
		self.assertEqual(self.issue(error_correction="Q")["error_correction"], "Q")
