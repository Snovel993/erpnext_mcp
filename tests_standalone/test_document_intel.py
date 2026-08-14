# SPDX-License-Identifier: MIT
"""Document Intelligence — v0.69.0, Sprint 4. The rules, then the record.

`document_intel.py` imports nothing from `frappe` and reads no database, the
same contract `model_registry.py` and `budget_engine.py` are tested under: every
function takes plain values in and returns plain values out, so a bare
`unittest.TestCase` is the honest test for it. The impure half —
`tools/docvalidation.py`, which is the only place a Document Validation is read
or written — is exercised through the tool layer in the second half of this
file.

ELEVEN CLAIMS.

1. `EPARegistrationNumbers` — the single most useful rule in the file. The
   pattern accepts what 40 CFR 152.132 describes and refuses what it does not;
   a number OCR misread into letter-shaped characters is REPAIRED only when the
   substitution produces something well-formed, because a repair that is still
   malformed replaces a value a person can see is wrong with one they cannot.

2. `PesticideLabelRules` — every check that has teeth: an REI that disagrees
   with the active ingredient the label names, a PHI shorter than its own REI
   (you cannot harvest a block you may not walk into), an ingredient statement
   totalling past 100%, and PPE, which is an ERROR on a DANGER label and a
   warning otherwise.

3. `ComparingAgainstTheOCRText` — the checks that only exist because the raw
   text is kept. A well-formed number that appears nowhere on the page did not
   come off this photograph, and the absence of `ocr_text` is reported rather
   than passed silently.

4. `CredentialRules` — expiry present, readable, not passed, not before issue,
   not ten years out; and the name match, which is the one check that needs a
   fact from outside the document.

5. `ReceiptAndEvidenceRules` — lines that do not sum, a total that is not a
   purchase, a date that has not happened yet, and a device whose clock is
   wrong.

6. `Scoring` — the two penalties are MULTIPLIED, not averaged: a clean
   extraction that captured three fields out of eight does not score as though
   it had been checked.

7. `RevalidationDue` — the document's own expiry wins over the cadence, and a
   type that never goes stale is never due.

8. `MergingAnAssessment` — a deterministic error outranks any judgement, the
   worst reading wins otherwise, the confidence is the LOWER of the two, and no
   assessment at all produces `Pending` with an issue that SAYS so.

9. `ValidatingThroughTheTool` — the record it writes, the role gate,
   `auto_store=false`, and the name pulled off the source Employee record —
   which is the one thing the pure layer cannot do for itself.

10. `Revalidating` — the stored extraction is the input, the count increments,
    the stored assessment is REUSED rather than dropped, and a replacement
    extraction is applied and said so.

11. `TheRegisterAndTheController` — the two list tools, and the two columns the
    controller owns: who confirmed, and when, in both directions.

12. `EntityScoping` — the `company` column exists so a Company User Permission
    reaches this doctype at the framework level, and it is resolved from the
    SOURCE RECORD rather than asked for twice. Without it an applicator
    licence's OCR text and an I-9 document's number would be readable across
    every entity on a multi-entity site.
"""

import json
import unittest

import frappe

from erpnext_mcp import document_intel as engine

from .fixtures import MAIN, OTHER, SeededTestCase
from .harness import ROLES, STORE, set_roles

#: Administrator's shipped roles, snapshotted before any test mutates the global
#: (never auto-reset) ROLES map. System Manager is in it, which is enough for
#: `require_validation_role` — the same pattern `test_fill_pipeline.py` uses.
SHIPPED_ROLES = list(ROLES["Administrator"])

ON = {
	f"allow_{name}": 1
	for name in (
		"validate_document_extraction",
		"get_document_validation",
		"list_document_validations",
		"revalidate_document",
		"list_revalidation_due",
	)
}

#: A label extraction with nothing wrong with it, for the tests that want to
#: change exactly one thing and see exactly one finding.
CLEAN_LABEL = {
	"epa_registration_number": "10163-169",
	"signal_word": "Warning",
	"rei_hours": 120,
	"phi_days": 14,
	"phi_crop": "Cherries",
	"active_ingredients": [{"name": "phosmet", "concentration": 70, "unit": "%"}],
	"application_rate": "2.125 lb/acre",
	"ppe_requirements": "coveralls over long sleeves, chemical-resistant gloves",
}

#: Enough OCR text that every "does this appear on the page" check passes for
#: `CLEAN_LABEL`. Real labels are far longer; this is the subset the rules read.
CLEAN_OCR = (
	"IMIDAN 70-W AGRICULTURAL INSECTICIDE\n"
	"ACTIVE INGREDIENT: phosmet .... 70%\n"
	"WARNING / AVISO\n"
	"EPA Reg. No. 10163 169    EPA Est. No. 10163-GA-1\n"
	"RESTRICTED ENTRY INTERVAL: 120 hours\n"
	"Cherries: do not apply within 14 days of harvest\n"
	"Rate: 2.125 lb per acre\n"
	"PPE: coveralls over long sleeves, chemical-resistant gloves\n"
)


def label(**overrides):
	base = dict(CLEAN_LABEL)
	base.update(overrides)
	return base


def codes(result) -> list:
	"""Every issue code in a result, in order."""
	return [entry["code"] for entry in result["issues"]]


def one(result, code) -> dict:
	"""The single issue with `code`, or a failure naming what came back instead.

	More than one match means a rule fired twice for one document, which is a
	real bug and is not what the caller of this helper is testing.
	"""
	found = [entry for entry in result["issues"] if entry["code"] == code]
	if len(found) != 1:
		raise AssertionError(f"expected exactly one {code!r}, got {codes(result)}")
	return found[0]


def run(document_type, fields, ocr_text="", **context):
	return engine.validate_extraction(document_type, ocr_text, fields, context)


# ── 1. the EPA registration number ──────────────────────────────────────────


class EPARegistrationNumbers(unittest.TestCase):
	def test_the_pattern_accepts_both_shapes_a_real_number_takes(self):
		for number in ("524-537", "10163-169", "524-537-1381", "1-1", "7969-338-59639"):
			with self.subTest(number=number):
				self.assertTrue(engine.EPA_REG_PATTERN.match(number))

	def test_the_pattern_refuses_what_is_not_one(self):
		for number in ("524", "524-", "-537", "524-537-", "524_537", "524-537-1381-9", "S24-537"):
			with self.subTest(number=number):
				self.assertIsNone(engine.EPA_REG_PATTERN.match(number))

	def test_whitespace_and_a_fancy_dash_are_normalised_losslessly(self):
		self.assertEqual(engine.normalise_epa_number(" 524 – 537 "), "524-537")

	def test_a_letter_shaped_misread_is_repaired(self):
		self.assertEqual(engine.repair_epa_number("1O163-l69"), "10163-169")
		self.assertEqual(engine.repair_epa_number("S24-S37"), "524-537")

	def test_a_repair_that_would_still_be_malformed_is_not_offered(self):
		"""See the module docstring: a malformed repair replaces a value a person
		can see is wrong with one they cannot."""
		self.assertEqual(engine.repair_epa_number("not a number at all"), "")
		self.assertEqual(engine.repair_epa_number("524"), "")

	def test_a_well_formed_number_is_never_repaired(self):
		self.assertEqual(engine.repair_epa_number("524-537"), "")

	def test_a_repairable_number_is_a_warning_and_a_proposal_not_a_correction(self):
		result = run("Pesticide Label", label(epa_registration_number="1O163-169"), CLEAN_OCR)
		issue = one(result, "epa_registration_number_repairable")
		self.assertEqual(issue["severity"], engine.WARNING)
		proposal = result["corrected_fields"]["epa_registration_number"]
		self.assertEqual(proposal["value"], "10163-169")
		self.assertEqual(proposal["was"], "1O163-169")
		self.assertEqual(proposal["rule"], "epa_reg_ocr_lookalikes")

	def test_an_unrepairable_number_is_an_error_with_nothing_proposed(self):
		result = run("Pesticide Label", label(epa_registration_number="see back panel"), CLEAN_OCR)
		self.assertEqual(one(result, "epa_registration_number_malformed")["severity"], engine.ERROR)
		self.assertNotIn("epa_registration_number", result["corrected_fields"])

	def test_a_missing_number_is_an_error_in_its_own_right(self):
		result = run("Pesticide Label", label(epa_registration_number=""), CLEAN_OCR)
		self.assertEqual(one(result, "epa_registration_number_missing")["severity"], engine.ERROR)


# ── 2. the label rules with teeth ───────────────────────────────────────────


class PesticideLabelRules(unittest.TestCase):
	def test_a_clean_label_produces_nothing_at_all(self):
		result = run("Pesticide Label", CLEAN_LABEL, CLEAN_OCR)
		self.assertEqual(codes(result), [])
		self.assertEqual(result["status"], engine.STATUS_PENDING)
		self.assertEqual(result["error_count"], 0)

	def test_an_rei_that_disagrees_with_the_ingredient_names_the_expected_range(self):
		"""Phosmet labels carry 5–14 days. A 12-hour REI beside phosmet is a
		misread digit or an interval taken from the wrong product panel."""
		result = run("Pesticide Label", label(rei_hours=12), CLEAN_OCR)
		issue = one(result, "rei_disagrees_with_active_ingredient")
		self.assertEqual(issue["severity"], engine.WARNING)
		self.assertIn("120–336", issue["message"])
		self.assertIn("phosmet", issue["message"])

	def test_it_never_corrects_an_rei_from_the_reference_table(self):
		"""The label is the law; the table is a reader's memory of it."""
		result = run("Pesticide Label", label(rei_hours=12), CLEAN_OCR)
		self.assertNotIn("rei_hours", result["corrected_fields"])

	def test_an_ingredient_the_table_does_not_know_produces_no_disagreement(self):
		result = run(
			"Pesticide Label",
			label(active_ingredients=[{"name": "something new", "concentration": 5, "unit": "%"}]),
			CLEAN_OCR,
		)
		self.assertNotIn("rei_disagrees_with_active_ingredient", codes(result))

	def test_a_phi_of_zero_days_against_a_five_day_rei_is_an_error(self):
		result = run("Pesticide Label", label(phi_days=0), CLEAN_OCR)
		issue = one(result, "phi_shorter_than_rei")
		self.assertEqual(issue["severity"], engine.ERROR)
		self.assertIn("nobody may enter it on foot", issue["message"])

	def test_a_phi_merely_shorter_than_the_rei_is_a_warning(self):
		"""It happens on a mechanically harvested label and nowhere else, so it
		is worth a person's eyes rather than a refusal."""
		result = run("Pesticide Label", label(phi_days=2, rei_hours=120), CLEAN_OCR)
		self.assertEqual(one(result, "phi_shorter_than_rei")["severity"], engine.WARNING)

	def test_a_phi_equal_to_the_rei_is_not_a_finding(self):
		result = run("Pesticide Label", label(phi_days=5, rei_hours=120), CLEAN_OCR)
		self.assertNotIn("phi_shorter_than_rei", codes(result))

	def test_a_phi_with_no_crop_is_an_error_because_the_number_is_unusable(self):
		result = run("Pesticide Label", label(phi_crop=""), CLEAN_OCR)
		issue = one(result, "phi_crop_missing")
		self.assertEqual(issue["severity"], engine.ERROR)
		self.assertIn("cannot be applied to a block", issue["message"])

	def test_an_rei_in_days_read_as_hours_is_caught_by_the_absolute_bound(self):
		result = run("Pesticide Label", label(rei_hours=2000), CLEAN_OCR)
		self.assertEqual(one(result, "rei_hours_out_of_range")["severity"], engine.ERROR)

	def test_an_rei_that_is_long_but_possible_is_only_a_warning(self):
		result = run("Pesticide Label", label(rei_hours=600), CLEAN_OCR)
		self.assertEqual(one(result, "rei_hours_unusually_long")["severity"], engine.WARNING)

	def test_ingredients_totalling_past_one_hundred_percent_is_an_error(self):
		result = run(
			"Pesticide Label",
			label(
				active_ingredients=[
					{"name": "phosmet", "concentration": 70, "unit": "%"},
					{"name": "sulfur", "concentration": 45, "unit": "%"},
				]
			),
			CLEAN_OCR,
		)
		issue = one(result, "active_ingredients_exceed_100_percent")
		self.assertEqual(issue["severity"], engine.ERROR)
		self.assertIn("115.00%", issue["message"])

	def test_concentrations_in_other_units_are_not_added_up(self):
		"""Two ingredients at 4 lb/gal each do not total 8% of anything."""
		result = run(
			"Pesticide Label",
			label(
				active_ingredients=[
					{"name": "phosmet", "concentration": 400, "unit": "g/L"},
					{"name": "sulfur", "concentration": 400, "unit": "g/L"},
				]
			),
			CLEAN_OCR,
		)
		self.assertNotIn("active_ingredients_exceed_100_percent", codes(result))

	def test_missing_ppe_is_an_error_on_a_danger_label_and_a_warning_otherwise(self):
		danger = run("Pesticide Label", label(signal_word="Danger", ppe_requirements=""), CLEAN_OCR)
		self.assertEqual(one(danger, "ppe_requirements_missing")["severity"], engine.ERROR)
		caution = run("Pesticide Label", label(signal_word="Caution", ppe_requirements=""), CLEAN_OCR)
		self.assertEqual(one(caution, "ppe_requirements_missing")["severity"], engine.WARNING)

	def test_a_signal_word_that_is_not_one_of_the_four_is_an_error(self):
		result = run("Pesticide Label", label(signal_word="Poison"), CLEAN_OCR)
		self.assertEqual(one(result, "signal_word_unrecognised")["severity"], engine.ERROR)

	def test_none_is_a_real_signal_word_and_is_not_checked_against_the_page(self):
		"""A Category IV product carries no signal word, which is why 'None' is in
		the Select — and looking for the word 'None' on the label would find
		nothing and say so wrongly."""
		result = run("Pesticide Label", label(signal_word="None"), CLEAN_OCR)
		self.assertEqual(codes(result), [])

	def test_a_rate_that_is_a_sentence_is_a_warning_not_an_error(self):
		result = run(
			"Pesticide Label",
			label(application_rate="Apply as directed on the following pages"),
			CLEAN_OCR,
		)
		self.assertEqual(one(result, "application_rate_unparseable")["severity"], engine.WARNING)

	def test_the_common_rate_spellings_all_parse(self):
		for rate in ("2 pt/acre", "1.5 lb/A", "32 fl oz per acre", "2.125 lb/acre", "1-2 qt/acre"):
			with self.subTest(rate=rate):
				result = run("Pesticide Label", label(application_rate=rate), CLEAN_OCR)
				self.assertNotIn("application_rate_unparseable", codes(result))

	def test_an_empty_extraction_is_one_error_plus_the_missing_fields(self):
		result = run("Pesticide Label", {}, CLEAN_OCR)
		self.assertIn("extraction_empty", codes(result))
		self.assertEqual(result["status"], engine.STATUS_FLAGGED)


# ── 3. what the raw text is kept for ────────────────────────────────────────


class ComparingAgainstTheOCRText(unittest.TestCase):
	def test_a_well_formed_number_absent_from_the_page_is_a_warning(self):
		result = run("Pesticide Label", label(epa_registration_number="524-537"), CLEAN_OCR)
		issue = one(result, "epa_registration_number_not_in_ocr")
		self.assertEqual(issue["severity"], engine.WARNING)

	def test_a_hyphen_the_ocr_read_as_a_space_is_not_a_finding(self):
		"""`CLEAN_OCR` prints `10163 169` where the label printed a hyphen. Both
		sides are normalised before comparing, so this is the same reading."""
		result = run("Pesticide Label", CLEAN_LABEL, CLEAN_OCR)
		self.assertNotIn("epa_registration_number_not_in_ocr", codes(result))

	def test_a_converted_interval_is_flagged_as_worth_confirming(self):
		"""A label printing '5 days' and an extraction holding 120 is a
		conversion the extraction did — reported, not refused."""
		text = CLEAN_OCR.replace("120 hours", "5 days")
		result = run("Pesticide Label", CLEAN_LABEL, text)
		self.assertEqual(one(result, "rei_hours_not_in_ocr")["severity"], engine.WARNING)

	def test_a_crop_that_is_not_on_the_page_is_a_warning(self):
		result = run("Pesticide Label", label(phi_crop="Apples"), CLEAN_OCR)
		self.assertEqual(one(result, "phi_crop_not_in_ocr")["severity"], engine.WARNING)

	def test_no_ocr_text_at_all_says_which_checks_did_not_run(self):
		result = run("Pesticide Label", CLEAN_LABEL, "")
		issue = one(result, "ocr_text_absent")
		self.assertEqual(issue["severity"], engine.INFO)
		self.assertIn("against nothing on the page", issue["message"])

	def test_no_ocr_text_never_produces_a_not_in_ocr_finding(self):
		result = run("Pesticide Label", CLEAN_LABEL, "")
		self.assertEqual([code for code in codes(result) if code.endswith("_not_in_ocr")], [])


# ── 4. credentials ──────────────────────────────────────────────────────────


LICENSE = {
	"license_number": "AG-L-99213",
	"licensee_name": "Ana Ruiz Delgado",
	"expiration_date": "2027-06-30",
	"issue_date": "2024-07-01",
	"issuing_state": "Oregon",
	"categories": "Ag Weed, Ag Insect",
}


def licence(**overrides):
	base = dict(LICENSE)
	base.update(overrides)
	return base


class CredentialRules(unittest.TestCase):
	def test_a_current_licence_produces_nothing(self):
		result = run("Applicator License", LICENSE, as_of="2026-08-14")
		self.assertEqual([code for code in codes(result) if code != "ocr_text_absent"], [])

	def test_an_expired_licence_is_an_error_naming_how_long_ago(self):
		result = run("Applicator License", licence(expiration_date="2026-06-30"), as_of="2026-08-14")
		issue = one(result, "license_expired")
		self.assertEqual(issue["severity"], engine.ERROR)
		self.assertIn("45 days ago", issue["message"])

	def test_a_licence_expiring_within_the_month_is_a_warning(self):
		result = run("Applicator License", licence(expiration_date="2026-09-01"), as_of="2026-08-14")
		self.assertEqual(one(result, "license_expiring")["severity"], engine.WARNING)

	def test_an_expiry_before_the_issue_date_is_read_the_wrong_way_round(self):
		result = run(
			"Applicator License",
			licence(expiration_date="2024-06-30", issue_date="2027-07-01"),
			as_of="2026-08-14",
		)
		self.assertEqual(one(result, "expiration_precedes_issue")["severity"], engine.ERROR)

	def test_an_expiry_ten_years_out_is_an_implausible_year_not_a_valid_licence(self):
		result = run("Applicator License", licence(expiration_date="2044-06-30"), as_of="2026-08-14")
		self.assertEqual(one(result, "expiration_date_implausible")["severity"], engine.WARNING)

	def test_a_missing_expiry_is_an_error_because_nobody_can_be_told_to_renew(self):
		result = run("Applicator License", licence(expiration_date=""), as_of="2026-08-14")
		self.assertEqual(one(result, "expiration_date_missing")["severity"], engine.ERROR)

	def test_a_name_that_matches_the_record_produces_nothing(self):
		result = run("Applicator License", LICENSE, as_of="2026-08-14", expected_name="Ana Ruiz Delgado")
		self.assertEqual([code for code in codes(result) if code.startswith("name_")], [])

	def test_a_surname_first_spelling_is_the_same_name(self):
		result = run(
			"Applicator License",
			licence(licensee_name="Delgado, Ana Ruiz"),
			as_of="2026-08-14",
			expected_name="Ana Ruiz Delgado",
		)
		self.assertEqual([code for code in codes(result) if code.startswith("name_")], [])

	def test_a_missing_middle_name_is_a_warning_rather_than_a_refusal(self):
		result = run(
			"Applicator License",
			licence(licensee_name="Ana Delgado"),
			as_of="2026-08-14",
			expected_name="Ana Ruiz Delgado",
		)
		self.assertEqual(one(result, "name_partially_matches_record")["severity"], engine.WARNING)

	def test_a_different_person_entirely_is_an_error(self):
		result = run(
			"Applicator License",
			licence(licensee_name="Robert Fenwick"),
			as_of="2026-08-14",
			expected_name="Ana Ruiz Delgado",
		)
		issue = one(result, "name_does_not_match_record")
		self.assertEqual(issue["severity"], engine.ERROR)
		self.assertIn("filed against the wrong person", issue["message"])

	def test_no_expected_name_means_the_check_stays_silent(self):
		"""A document filed against nothing has nothing to disagree with, and
		inventing a finding there trains people to ignore the code."""
		result = run("Applicator License", licence(licensee_name="Robert Fenwick"), as_of="2026-08-14")
		self.assertEqual([code for code in codes(result) if code.startswith("name_")], [])

	def test_an_expired_i9_document_says_what_it_actually_means(self):
		result = run(
			"I-9 Document",
			{
				"document_title": "Employment Authorization Document",
				"issuing_authority": "USCIS",
				"document_number": "SRC1234567890",
				"expiration_date": "2026-05-01",
			},
			as_of="2026-08-14",
		)
		issue = one(result, "work_authorization_expired")
		self.assertEqual(issue["severity"], engine.ERROR)
		self.assertIn("cannot lawfully be put on a crew", issue["message"])

	def test_an_i9_document_with_no_expiry_is_information_not_a_finding(self):
		"""Several acceptable I-9 documents carry no expiry at all."""
		result = run(
			"I-9 Document",
			{
				"document_title": "US Passport",
				"issuing_authority": "US Department of State",
				"document_number": "X12345678",
			},
			as_of="2026-08-14",
		)
		self.assertEqual(one(result, "expiration_date_absent")["severity"], engine.INFO)
		self.assertEqual(result["status"], engine.STATUS_PENDING)

	def test_training_completed_more_than_a_year_ago_is_out_of_date(self):
		result = run(
			"WPS Certificate",
			{"trainee_name": "Ana Ruiz", "completion_date": "2025-01-04", "trainer_name": "R. Ito"},
			as_of="2026-08-14",
		)
		self.assertEqual(one(result, "training_out_of_date")["severity"], engine.WARNING)

	def test_a_certificate_dated_in_the_future_is_an_error(self):
		result = run(
			"WPS Certificate",
			{"trainee_name": "Ana Ruiz", "completion_date": "2027-01-04", "trainer_name": "R. Ito"},
			as_of="2026-08-14",
		)
		self.assertEqual(one(result, "completion_date_in_future")["severity"], engine.ERROR)


# ── 5. receipts and evidence ────────────────────────────────────────────────


class ReceiptAndEvidenceRules(unittest.TestCase):
	def test_lines_that_sum_to_the_total_with_tax_produce_nothing(self):
		result = run(
			"Receipt",
			{
				"merchant": "Valley Fuel",
				"amount": 108.50,
				"receipt_date": "2026-08-01",
				"items": [{"amount": 60.0}, {"amount": 40.0}],
				"tax": 8.50,
			},
			as_of="2026-08-14",
		)
		self.assertNotIn("line_items_do_not_sum", codes(result))

	def test_a_gap_between_the_lines_and_the_total_names_the_gap(self):
		result = run(
			"Receipt",
			{
				"merchant": "Valley Fuel",
				"amount": 120.00,
				"receipt_date": "2026-08-01",
				"items": [{"amount": 60.0}, {"amount": 40.0}],
			},
			as_of="2026-08-14",
		)
		issue = one(result, "line_items_do_not_sum")
		self.assertEqual(issue["severity"], engine.WARNING)
		self.assertIn("20.00", issue["message"])

	def test_lines_priced_by_rate_and_quantity_are_summed_too(self):
		result = run(
			"Receipt",
			{
				"merchant": "Valley Fuel",
				"amount": 100.00,
				"receipt_date": "2026-08-01",
				"items": [{"rate": 4.0, "qty": 25}],
			},
			as_of="2026-08-14",
		)
		self.assertNotIn("line_items_do_not_sum", codes(result))

	def test_a_negative_total_is_a_credit_note_and_is_refused(self):
		result = run(
			"Receipt",
			{"merchant": "Valley Fuel", "amount": -40.0, "receipt_date": "2026-08-01"},
			as_of="2026-08-14",
		)
		self.assertEqual(one(result, "amount_not_positive")["severity"], engine.ERROR)

	def test_a_receipt_dated_in_the_future_is_an_error(self):
		result = run(
			"Receipt",
			{"merchant": "Valley Fuel", "amount": 40.0, "receipt_date": "2026-09-01"},
			as_of="2026-08-14",
		)
		self.assertEqual(one(result, "receipt_date_in_future")["severity"], engine.ERROR)

	def test_evidence_captured_in_the_future_is_a_clock_that_is_wrong(self):
		result = run("Task Evidence", {"captured_at": "2026-09-01"}, as_of="2026-08-14")
		issue = one(result, "captured_at_in_future")
		self.assertEqual(issue["severity"], engine.ERROR)
		self.assertIn("clock is wrong", issue["message"])

	def test_a_signature_with_no_signer_is_not_evidence_of_anything(self):
		result = run("Signature", {"signed_at": "2026-08-01"}, as_of="2026-08-14")
		self.assertEqual(one(result, "signer_name_missing")["severity"], engine.ERROR)


# ── 6. the score ────────────────────────────────────────────────────────────


class Scoring(unittest.TestCase):
	def test_coverage_is_the_fraction_of_expected_fields_present(self):
		self.assertEqual(engine.extraction_coverage("Pesticide Label", CLEAN_LABEL), 1.0)
		self.assertEqual(
			engine.extraction_coverage("Pesticide Label", {"rei_hours": 12, "phi_days": 3}), 0.25
		)

	def test_a_type_with_no_expected_fields_is_fully_covered(self):
		self.assertEqual(engine.extraction_coverage("Nothing Like This", {}), 1.0)

	def test_a_clean_full_extraction_scores_at_the_top(self):
		self.assertEqual(engine.score_confidence([], 1.0), 1.0)

	def test_the_two_penalties_multiply_rather_than_average(self):
		"""A clean extraction covering 40% of a document is not 70% trustworthy —
		it is a document nobody has really checked."""
		self.assertEqual(engine.score_confidence([], 0.4), 0.4)

	def test_coverage_is_floored_so_an_empty_extraction_is_not_scored_at_zero(self):
		"""Zero would claim the document is KNOWN to be wrong. What is known is
		that almost nothing was read."""
		self.assertEqual(engine.score_confidence([], 0.0), 0.4)

	def test_an_error_costs_about_three_warnings(self):
		"""An error is a fact about the document; a warning is a disagreement
		with an expectation, and the expectations here are a small table."""
		error = engine.score_confidence([engine.issue("x", engine.ERROR, "", "")], 1.0)
		warnings = engine.score_confidence([engine.issue("x", engine.WARNING, "", "")] * 3, 1.0)
		self.assertAlmostEqual(error, warnings, delta=0.02)

	def test_the_score_never_goes_below_the_floor(self):
		self.assertEqual(
			engine.score_confidence([engine.issue("x", engine.ERROR, "", "")] * 40, 1.0),
			engine._CONFIDENCE_FLOOR,
		)

	def test_the_reasoning_names_the_codes_a_person_should_look_at(self):
		result = run("Pesticide Label", label(phi_days=0), CLEAN_OCR)
		self.assertIn("phi_shorter_than_rei", result["reasoning"])

	def test_an_unknown_document_type_says_nothing_was_checked(self):
		result = run("Bill Of Lading", {"anything": 1})
		self.assertEqual(one(result, "document_type_unknown")["severity"], engine.WARNING)
		self.assertIn("No document-specific rules", result["reasoning"])


# ── 7. when to look again ───────────────────────────────────────────────────


class RevalidationDue(unittest.TestCase):
	def test_a_type_that_never_goes_stale_is_never_due(self):
		for document_type in ("Receipt", "Task Evidence", "Inspection Evidence", "Signature"):
			with self.subTest(document_type=document_type):
				self.assertEqual(engine.revalidation_due(document_type, {}, "2026-08-14"), "")

	def test_a_label_falls_due_a_year_after_it_was_read(self):
		self.assertEqual(engine.revalidation_due("Pesticide Label", {}, "2026-08-14"), "2027-08-14")

	def test_the_documents_own_expiry_wins_when_it_comes_first(self):
		"""A licence is not due on the anniversary of somebody scanning it."""
		self.assertEqual(
			engine.revalidation_due("Applicator License", {"expiration_date": "2026-11-30"}, "2026-08-14"),
			"2026-11-30",
		)

	def test_the_cadence_wins_when_the_expiry_is_further_out_than_it(self):
		self.assertEqual(
			engine.revalidation_due("Applicator License", {"expiration_date": "2029-06-30"}, "2026-08-14"),
			"2027-08-14",
		)

	def test_an_expiry_already_in_the_past_falls_back_to_the_cadence(self):
		self.assertEqual(
			engine.revalidation_due("Applicator License", {"expiration_date": "2020-06-30"}, "2026-08-14"),
			"2027-08-14",
		)

	def test_a_receipt_carrying_an_expiry_is_still_never_due(self):
		self.assertEqual(
			engine.revalidation_due("Receipt", {"expiration_date": "2020-06-30"}, "2026-08-14"), ""
		)


# ── 8. merging the judgement half ───────────────────────────────────────────


class MergingAnAssessment(unittest.TestCase):
	def deterministic(self, **overrides):
		base = run("Pesticide Label", CLEAN_LABEL, CLEAN_OCR)
		base.update(overrides)
		return base

	def test_no_assessment_leaves_pending_and_says_why(self):
		merged = engine.merge_llm_assessment(self.deterministic(), None)
		self.assertEqual(merged["status"], engine.STATUS_PENDING)
		self.assertFalse(merged["llm_available"])
		issue = one(merged, "llm_validation_unavailable")
		self.assertEqual(issue["severity"], engine.INFO)
		self.assertIn("did not run", issue["message"])

	def test_no_assessment_still_flags_a_deterministic_error(self):
		"""Pending means 'nothing definite was found and nothing has judged it',
		not 'we did not look'."""
		deterministic = run("Pesticide Label", label(phi_days=0), CLEAN_OCR)
		merged = engine.merge_llm_assessment(deterministic, None)
		self.assertEqual(merged["status"], engine.STATUS_FLAGGED)

	def test_an_assessment_can_promote_a_clean_document_to_validated(self):
		assessment, problems = engine.validate_llm_assessment(
			{"status": "Validated", "confidence": 0.93, "reasoning": "Reads as a genuine label."}
		)
		self.assertEqual(problems, [])
		merged = engine.merge_llm_assessment(self.deterministic(), assessment, "claude-opus-5")
		self.assertEqual(merged["status"], engine.STATUS_VALIDATED)
		self.assertEqual(merged["llm_model"], "claude-opus-5")
		self.assertIn("Reads as a genuine label.", merged["reasoning"])

	def test_an_assessment_cannot_talk_a_rule_out_of_a_fact(self):
		deterministic = run("Pesticide Label", label(phi_days=0), CLEAN_OCR)
		assessment, _ = engine.validate_llm_assessment({"status": "Validated", "confidence": 0.99})
		merged = engine.merge_llm_assessment(deterministic, assessment, "claude-opus-5")
		self.assertEqual(merged["status"], engine.STATUS_FLAGGED)

	def test_a_model_may_go_further_than_the_rules_did(self):
		deterministic = run("Pesticide Label", label(phi_days=0), CLEAN_OCR)
		assessment, _ = engine.validate_llm_assessment({"status": "Rejected", "confidence": 0.1})
		merged = engine.merge_llm_assessment(deterministic, assessment, "claude-opus-5")
		self.assertEqual(merged["status"], engine.STATUS_REJECTED)

	def test_the_merged_confidence_is_the_lower_of_the_two(self):
		assessment, _ = engine.validate_llm_assessment({"status": "Validated", "confidence": 0.4})
		merged = engine.merge_llm_assessment(self.deterministic(), assessment, "m")
		self.assertEqual(merged["confidence"], 0.4)

	def test_the_models_own_findings_join_the_issue_list(self):
		assessment, _ = engine.validate_llm_assessment(
			{
				"status": "Flagged",
				"confidence": 0.5,
				"issues": [{"code": "reprinted_panel", "severity": "warning", "field": "", "message": "x"}],
			}
		)
		merged = engine.merge_llm_assessment(self.deterministic(), assessment, "m")
		self.assertIn("reprinted_panel", codes(merged))
		self.assertEqual(merged["status"], engine.STATUS_FLAGGED)

	def test_a_bare_string_finding_is_taken_as_a_warning(self):
		assessment, _ = engine.validate_llm_assessment({"issues": ["the panel looks reprinted"]})
		self.assertEqual(assessment["issues"][0]["severity"], engine.WARNING)
		self.assertEqual(assessment["issues"][0]["code"], "llm_finding")

	def test_an_assessment_of_the_wrong_shape_is_reported_never_dropped_silently(self):
		"""The failure worth catching is a client whose judgement is being
		quietly discarded every time."""
		assessment, problems = engine.validate_llm_assessment("looks fine to me")
		self.assertIsNone(assessment)
		self.assertEqual(problems[0]["code"], "llm_assessment_wrong_shape")

	def test_an_unrecognised_status_is_reported_and_the_rules_stand(self):
		assessment, problems = engine.validate_llm_assessment({"status": "probably ok"})
		self.assertEqual(problems[0]["code"], "llm_status_unrecognised")
		self.assertEqual(assessment["status"], "")

	def test_a_confidence_outside_zero_to_one_is_left_out_of_the_score(self):
		assessment, problems = engine.validate_llm_assessment({"status": "Validated", "confidence": 42})
		self.assertEqual(problems[0]["code"], "llm_confidence_out_of_range")
		merged = engine.merge_llm_assessment(self.deterministic(), assessment, "m")
		self.assertEqual(merged["confidence"], self.deterministic()["confidence"])

	def test_a_status_is_matched_case_insensitively(self):
		assessment, problems = engine.validate_llm_assessment({"status": "validated"})
		self.assertEqual(problems, [])
		self.assertEqual(assessment["status"], engine.STATUS_VALIDATED)


# ── the tool layer ──────────────────────────────────────────────────────────


class DocumentIntelTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		set_roles("Administrator", SHIPPED_ROLES)
		self.configure(enabled=1, **ON)

	def an_employee(self, name="HR-EMP-RUIZ", employee_name="Ana Ruiz Delgado"):
		STORE.seed(
			"Employee",
			[
				{
					"name": name,
					"employee_name": employee_name,
					"status": "Active",
					"company": MAIN,
					"date_of_joining": "2026-01-01",
				}
			],
		)
		return name

	def a_validation(self, **overrides):
		"""One stored Document Validation, through the tool that writes them."""
		payload = {
			"document_type": "Pesticide Label",
			"ocr_text": CLEAN_OCR,
			"extracted_fields": CLEAN_LABEL,
			"as_of": "2026-08-14",
		}
		payload.update(overrides)
		return self.tool_data("validate_document_extraction", payload)


class ValidatingThroughTheTool(DocumentIntelTestCase):
	def test_it_stores_a_record_named_from_the_series(self):
		data = self.a_validation()
		self.assertTrue(data["stored"])
		self.assertTrue(data["validation_id"].startswith("DVAL-"))
		self.assertTrue(frappe.db.exists("Document Validation", data["validation_id"]))

	def test_the_record_keeps_the_ocr_text_and_the_extraction(self):
		"""Verbatim but for the surrounding whitespace `as_str` trims off every
		string argument on this surface — the page itself is not touched, which
		is what `revalidate_document` and every not-in-ocr check depend on."""
		data = self.a_validation()
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["ocr_text"], CLEAN_OCR.strip())
		self.assertEqual(stored["extraction"], CLEAN_LABEL)

	def test_a_clean_label_with_no_assessment_lands_pending(self):
		data = self.a_validation()
		self.assertEqual(data["status"], "Pending")
		self.assertFalse(data["llm_available"])
		self.assertIn("llm_validation_unavailable", [entry["code"] for entry in data["issues"]])

	def test_an_assessment_is_stored_as_the_caller_sent_it(self):
		"""Not as `validate_llm_assessment` made of it — storing the cleaned
		version would erase the evidence of a client being quietly dropped."""
		data = self.a_validation(
			llm_assessment={"status": "Validated", "confidence": 0.9, "extra": "kept"},
			llm_model="claude-opus-5",
		)
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["llm_assessment"]["extra"], "kept")
		self.assertEqual(stored["llm_model"], "claude-opus-5")
		self.assertEqual(data["status"], "Validated")

	def test_auto_store_false_computes_the_answer_and_writes_nothing(self):
		before = len(frappe.db.get_all("Document Validation", pluck="name"))
		data = self.a_validation(auto_store=False)
		self.assertFalse(data["stored"])
		self.assertEqual(data["validation_id"], "")
		self.assertEqual(len(frappe.db.get_all("Document Validation", pluck="name")), before)

	def test_the_revalidation_due_date_lands_on_the_record(self):
		data = self.a_validation()
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["revalidation_due"], "2027-08-14")

	def test_a_receipt_gets_no_revalidation_due_date_at_all(self):
		data = self.a_validation(
			document_type="Receipt",
			ocr_text="",
			extracted_fields={"merchant": "Valley Fuel", "amount": 40.0, "receipt_date": "2026-08-01"},
		)
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertIsNone(stored["revalidation_due"])

	def test_it_reads_the_expected_name_off_the_source_employee(self):
		"""The one fact the pure layer cannot get for itself — see the module
		docstring on `_context_for`."""
		employee = self.an_employee()
		data = self.a_validation(
			document_type="Applicator License",
			ocr_text="",
			extracted_fields=licence(licensee_name="Robert Fenwick"),
			source_doctype="Employee",
			source_name=employee,
		)
		self.assertIn("name_does_not_match_record", [entry["code"] for entry in data["issues"]])
		self.assertEqual(data["status"], "Flagged")

	def test_a_matching_name_on_the_source_employee_produces_no_finding(self):
		employee = self.an_employee()
		data = self.a_validation(
			document_type="Applicator License",
			ocr_text="",
			extracted_fields=LICENSE,
			source_doctype="Employee",
			source_name=employee,
		)
		self.assertEqual(
			[code for code in (entry["code"] for entry in data["issues"]) if code.startswith("name_")], []
		)

	def test_an_explicit_expected_name_wins_over_the_source_record(self):
		employee = self.an_employee()
		data = self.a_validation(
			document_type="Applicator License",
			ocr_text="",
			extracted_fields=licence(licensee_name="Robert Fenwick"),
			source_doctype="Employee",
			source_name=employee,
			expected_name="Robert Fenwick",
		)
		self.assertEqual(
			[code for code in (entry["code"] for entry in data["issues"]) if code.startswith("name_")], []
		)

	def test_a_source_record_that_does_not_exist_yet_is_noted_not_refused(self):
		"""A foreman photographs a label before the Item exists; a capture that
		requires master data first is a capture that does not happen."""
		data = self.a_validation(source_doctype="Employee", source_name="HR-EMP-NOBODY")
		issue = [entry for entry in data["issues"] if entry["code"] == "source_record_absent"]
		self.assertEqual(len(issue), 1)
		self.assertEqual(issue[0]["severity"], "info")
		self.assertTrue(data["stored"])

	def test_a_source_name_without_a_doctype_is_refused(self):
		error = self.tool_error(
			"validate_document_extraction",
			{
				"document_type": "Pesticide Label",
				"extracted_fields": CLEAN_LABEL,
				"source_name": "CHEM-1",
			},
		)
		self.assertIn("source_doctype", error)

	def test_an_unknown_document_type_is_refused_by_name(self):
		error = self.tool_error(
			"validate_document_extraction",
			{"document_type": "Bill Of Lading", "extracted_fields": {}},
		)
		self.assertIn("Pesticide Label", error)
		self.assertIn("no sensible default", error)

	def test_extracted_fields_is_required(self):
		error = self.tool_error("validate_document_extraction", {"document_type": "Pesticide Label"})
		self.assertIn("extracted_fields is required", error)

	def test_a_json_string_is_accepted_where_an_object_is_expected(self):
		data = self.a_validation(extracted_fields=json.dumps(CLEAN_LABEL))
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["extraction"], CLEAN_LABEL)

	def test_a_caller_without_the_role_may_not_validate(self):
		set_roles("Administrator", ["Employee"])
		error = self.tool_error(
			"validate_document_extraction",
			{"document_type": "Pesticide Label", "extracted_fields": CLEAN_LABEL},
		)
		self.assertIn("may not validate documents", error)
		self.assertIn("Farm Manager", error)

	def test_the_reads_are_open_to_a_caller_without_the_write_role(self):
		data = self.a_validation()
		set_roles("Administrator", ["Employee"])
		self.assertEqual(
			self.tool_data("get_document_validation", {"name": data["validation_id"]})["name"],
			data["validation_id"],
		)


class Revalidating(DocumentIntelTestCase):
	def test_it_re_runs_against_the_stored_extraction_and_counts(self):
		data = self.a_validation()
		again = self.tool_data("revalidate_document", {"name": data["validation_id"], "as_of": "2026-08-14"})
		self.assertEqual(again["revalidation_count"], 1)
		self.assertTrue(again["last_revalidated"])
		self.assertFalse(again["status_changed"])

	def test_a_second_run_counts_again(self):
		data = self.a_validation()
		self.tool_data("revalidate_document", {"name": data["validation_id"]})
		again = self.tool_data("revalidate_document", {"name": data["validation_id"]})
		self.assertEqual(again["revalidation_count"], 2)

	def test_a_licence_that_has_since_expired_changes_status_with_no_new_photograph(self):
		employee = self.an_employee()
		data = self.a_validation(
			document_type="Applicator License",
			ocr_text="",
			extracted_fields=licence(expiration_date="2026-09-01"),
			source_doctype="Employee",
			source_name=employee,
			as_of="2026-08-14",
		)
		self.assertEqual(data["status"], "Pending")
		later = self.tool_data("revalidate_document", {"name": data["validation_id"], "as_of": "2026-10-01"})
		self.assertEqual(later["status"], "Flagged")
		self.assertTrue(later["status_changed"])
		self.assertEqual(later["previous_status"], "Pending")
		self.assertIn("license_expired", [entry["code"] for entry in later["issues"]])

	def test_the_stored_assessment_is_reused_rather_than_dropped(self):
		"""A re-run that lost it would move a Validated record to Pending and
		look like the document had gone stale."""
		data = self.a_validation(
			llm_assessment={"status": "Validated", "confidence": 0.9}, llm_model="claude-opus-5"
		)
		self.assertEqual(data["status"], "Validated")
		again = self.tool_data("revalidate_document", {"name": data["validation_id"]})
		self.assertEqual(again["status"], "Validated")
		self.assertTrue(again["llm_available"])
		self.assertEqual(again["llm_model"], "claude-opus-5")

	def test_a_fresh_assessment_replaces_the_stored_one(self):
		data = self.a_validation(llm_assessment={"status": "Validated", "confidence": 0.9})
		again = self.tool_data(
			"revalidate_document",
			{"name": data["validation_id"], "llm_assessment": {"status": "Rejected", "confidence": 0.2}},
		)
		self.assertEqual(again["status"], "Rejected")
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["llm_assessment"]["status"], "Rejected")

	def test_a_corrected_extraction_replaces_the_stored_one_and_says_so(self):
		data = self.a_validation(extracted_fields=label(epa_registration_number="1O163-169"))
		self.assertIn("epa_registration_number_repairable", [entry["code"] for entry in data["issues"]])
		again = self.tool_data(
			"revalidate_document",
			{
				"name": data["validation_id"],
				"extracted_fields": CLEAN_LABEL,
				"reason": "supervisor confirmed the number against the jug",
			},
		)
		self.assertTrue(again["extraction_replaced"])
		self.assertNotIn("epa_registration_number_repairable", [entry["code"] for entry in again["issues"]])
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["extraction"], CLEAN_LABEL)

	def test_a_record_with_no_stored_extraction_is_refused_by_name(self):
		doc = frappe.new_doc("Document Validation")
		doc.document_type = "Receipt"
		doc.validation_status = "Pending"
		doc.insert(ignore_permissions=True)
		error = self.tool_error("revalidate_document", {"name": doc.name})
		self.assertIn("no stored extraction", error)

	def test_a_caller_without_the_role_may_not_revalidate(self):
		data = self.a_validation()
		set_roles("Administrator", ["Employee"])
		error = self.tool_error("revalidate_document", {"name": data["validation_id"]})
		self.assertIn("may not validate documents", error)

	def test_an_unknown_docname_is_refused_with_the_register_named(self):
		error = self.tool_error("revalidate_document", {"name": "DVAL-2026-9999"})
		self.assertIn("list_document_validations", error)


class TheRegisterAndTheController(DocumentIntelTestCase):
	def test_the_list_carries_neither_the_ocr_text_nor_the_extraction(self):
		self.a_validation()
		rows = self.tool_data("list_document_validations")["validations"]
		self.assertEqual(len(rows), 1)
		self.assertNotIn("ocr_text", rows[0])
		self.assertNotIn("extraction", rows[0])

	def test_it_filters_by_document_type_and_by_source_record(self):
		self.a_validation()
		self.a_validation(
			document_type="Receipt",
			ocr_text="",
			extracted_fields={"merchant": "Valley Fuel", "amount": 40.0, "receipt_date": "2026-08-01"},
			source_doctype="Expense Receipt",
			source_name="EXP-0001",
		)
		labels = self.tool_data("list_document_validations", {"document_type": "Pesticide Label"})
		self.assertEqual(labels["count"], 1)
		by_source = self.tool_data("list_document_validations", {"source_name": "EXP-0001"})
		self.assertEqual(by_source["count"], 1)
		self.assertEqual(by_source["validations"][0]["document_type"], "Receipt")

	def test_an_unknown_filter_value_is_refused_rather_than_returning_nothing(self):
		self.assertIn("Pesticide Label", self.tool_error("list_document_validations", {"document_type": "x"}))
		self.assertIn("Validated", self.tool_error("list_document_validations", {"status": "x"}))

	def test_a_receipt_is_never_on_the_revalidation_list(self):
		"""An empty due date is not due — which is what stops a site with ten
		thousand receipts and forty licences returning ten thousand rows."""
		self.a_validation(
			document_type="Receipt",
			ocr_text="",
			extracted_fields={"merchant": "Valley Fuel", "amount": 40.0, "receipt_date": "2026-08-01"},
		)
		due = self.tool_data("list_revalidation_due", {"as_of": "2099-01-01"})
		self.assertEqual(due["count"], 0)

	def test_a_label_appears_once_its_due_date_has_passed_with_the_days_overdue(self):
		self.a_validation()
		self.assertEqual(self.tool_data("list_revalidation_due", {"as_of": "2027-08-13"})["count"], 0)
		due = self.tool_data("list_revalidation_due", {"as_of": "2027-08-20"})
		self.assertEqual(due["count"], 1)
		self.assertEqual(due["validations"][0]["days_overdue"], 6)
		self.assertEqual(due["never_human_confirmed"], 1)

	def test_confirming_stamps_who_and_when(self):
		data = self.a_validation()
		doc = frappe.get_doc("Document Validation", data["validation_id"])
		doc.human_confirmed = 1
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.human_confirmed_by, "Administrator")
		self.assertTrue(doc.human_confirmed_at)

	def test_unticking_clears_both_stamps(self):
		"""A confirmation time with nobody's name against it reads like somebody
		confirmed this and we lost track of who."""
		data = self.a_validation()
		doc = frappe.get_doc("Document Validation", data["validation_id"])
		doc.human_confirmed = 1
		doc.save(ignore_permissions=True)
		doc.human_confirmed = 0
		doc.save(ignore_permissions=True)
		self.assertFalse(doc.human_confirmed_by)
		self.assertFalse(doc.human_confirmed_at)

	def test_a_confirmed_record_is_not_counted_as_unconfirmed_on_the_due_list(self):
		data = self.a_validation()
		doc = frappe.get_doc("Document Validation", data["validation_id"])
		doc.human_confirmed = 1
		doc.save(ignore_permissions=True)
		due = self.tool_data("list_revalidation_due", {"as_of": "2027-08-20"})
		self.assertEqual(due["count"], 1)
		self.assertEqual(due["never_human_confirmed"], 0)

	def test_the_controller_refuses_a_status_the_select_does_not_carry(self):
		data = self.a_validation()
		doc = frappe.get_doc("Document Validation", data["validation_id"])
		doc.validation_status = "probably ok"
		with self.assertRaises(Exception) as caught:
			doc.save(ignore_permissions=True)
		self.assertIn("not a validation status", str(caught.exception))

	def test_the_controller_clamps_a_confidence_outside_zero_to_one(self):
		data = self.a_validation()
		doc = frappe.get_doc("Document Validation", data["validation_id"])
		doc.overall_confidence = 4.2
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.overall_confidence, 1.0)


class ToolRegistration(unittest.TestCase):
	def test_the_five_tools_exist_split_three_read_two_write(self):
		from erpnext_mcp import registry

		reads = ("get_document_validation", "list_document_validations", "list_revalidation_due")
		writes = ("validate_document_extraction", "revalidate_document")
		for name in reads + writes:
			with self.subTest(tool=name):
				self.assertIn(name, registry.TOOLS)
		for name in reads:
			self.assertIn(name, registry.READ_TOOLS)
		for name in writes:
			self.assertIn(name, registry.MUTATING_TOOLS)

	def test_every_one_of_them_gates_on_the_doctype_being_present(self):
		from erpnext_mcp import registry

		for name in (
			"validate_document_extraction",
			"get_document_validation",
			"list_document_validations",
			"revalidate_document",
			"list_revalidation_due",
		):
			with self.subTest(tool=name):
				self.assertIsNot(registry.TOOLS[name]["available"], registry._always)
				self.assertIn("Document Validation", registry.TOOLS[name]["requires"])


class EntityScoping(DocumentIntelTestCase):
	"""See `permissions.py`: a Company User Permission restricts every document
	that LINKS to a Company, at the framework level. That clause is why this
	doctype carries the column at all — an unscoped validation is one entity's
	licence and I-9 OCR text readable by another's."""

	def test_the_doctype_links_to_company_so_frappe_scopes_it(self):
		from erpnext_mcp import permissions

		meta = frappe.get_meta("Document Validation")
		field = meta.get_field("company")
		self.assertEqual(field.fieldtype, "Link")
		self.assertEqual(field.options, "Company")
		# And therefore it needs no hook of its own — the two doctypes in
		# `permissions.py` are the ones Frappe's mechanism CANNOT reach.
		self.assertNotIn("Document Validation", permissions.scoped_doctypes())

	def test_the_company_comes_off_the_source_record(self):
		"""A licence filed against an Employee belongs to whoever that Employee
		belongs to, and nobody should have to say so twice."""
		employee = self.an_employee()
		data = self.a_validation(source_doctype="Employee", source_name=employee)
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["company"], MAIN)

	def test_an_explicit_company_wins_over_the_source_record(self):
		employee = self.an_employee()
		data = self.a_validation(source_doctype="Employee", source_name=employee, company=OTHER)
		stored = self.tool_data("get_document_validation", {"name": data["validation_id"]})
		self.assertEqual(stored["company"], OTHER)

	def test_a_company_that_is_not_on_this_site_is_refused(self):
		error = self.tool_error(
			"validate_document_extraction",
			{
				"document_type": "Pesticide Label",
				"extracted_fields": CLEAN_LABEL,
				"company": "Nowhere Orchards",
			},
		)
		self.assertIn("no Company named", error)

	def test_the_register_filters_by_entity(self):
		employee = self.an_employee()
		self.a_validation(source_doctype="Employee", source_name=employee)
		self.a_validation(company=OTHER)
		self.assertEqual(self.tool_data("list_document_validations", {"company": MAIN})["count"], 1)
		self.assertEqual(self.tool_data("list_document_validations", {"company": OTHER})["count"], 1)
		self.assertEqual(self.tool_data("list_document_validations")["count"], 2)

	def test_the_revalidation_list_filters_by_entity_too(self):
		self.a_validation(company=OTHER)
		due = self.tool_data("list_revalidation_due", {"as_of": "2027-08-20", "company": MAIN})
		self.assertEqual(due["count"], 0)
		due = self.tool_data("list_revalidation_due", {"as_of": "2027-08-20", "company": OTHER})
		self.assertEqual(due["count"], 1)
