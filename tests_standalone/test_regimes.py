# SPDX-License-Identifier: MIT
"""v0.19.2 — the regime vocabulary as records, and the curriculum master.

THE CLAIM BEHIND THE RELEASE is that a compliance calendar which cannot be read
one audit at a time is a calendar nobody reads at all. Twelve rules produced one
undifferentiated list; "everything OR-OSHA will ask about in October" is an
afternoon's work and "everything" is not, and until now the only way to get the
first was to already know which of twelve rule names belonged to which agency.

It also closes a hole v0.19.0 left open on purpose and named in its own
docstring: `training_type` was free text, so "WPS Handler Training satisfies WPS"
had to be restated on every record, and thirty records of one course were thirty
chances to mistype the tag.

SEVEN CLAIMS.

1. `TheVocabularyIsStillOnePlace` — the master is SEEDED from `training.REGIMES`,
   the seeder is idempotent, and every read still goes through `canon`. Two
   storage shapes, one definition.

2. `AlertsCarryTheirRegimes` — the sweep writes the tags, per rule for ten rules
   and per RECORD for the two that fire on many kinds of thing, and a retag in a
   later release reaches the alerts already on the site.

3. `TheCalendarReadsOneAuditAtATime` — the regime filter returns only matching
   alerts, matches by TOKEN, and REFUSES an unrecognised value rather than
   returning an empty calendar that reads as a clean one.

4. `ANarrowedSweepDismissesNothing` — the sharpest edge in the release. A sweep
   filtered to one regime must not auto-dismiss the rules it did not run.

5. `TheMigrationIsIdempotent` — free text becomes links, twice, with no
   duplicates and no evidence rewritten.

6. `FreeTextStillFilesARecord` — `record_training` with a course this site has
   never run creates the curriculum rather than refusing, and does NOT give it
   this session's regimes.

7. `ThePacketAnswersToOneRegime` — `generate_audit_packet(regime=...)` narrows
   both regime-aware sections, and without it nothing is narrowed at all.
"""

import frappe

from erpnext_mcp import alerts, audit_packets, roles, training
from erpnext_mcp.patches import migrate_training_types

from .fixtures import MAIN, V12TestCase, install_hrms
from .harness import ROLES, STORE

ON = {
	f"allow_{name}": 1
	for name in (
		"record_training",
		"list_trainings",
		"refresh_compliance_alerts",
		"get_compliance_calendar",
		"generate_audit_packet",
		"create_employee",
	)
}

TRAINEE = "HR-EMP-00002"  # Ben Packhouse, Active, at MAIN
TOPICS = "Heat index, water, shade, symptoms, reporting, emergency response"


def days_out(count: int) -> str:
	return str(frappe.utils.add_days(frappe.utils.today(), count))


class RegimeTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		install_hrms()
		self._roles_before = {user: list(held) for user, held in ROLES.items()}
		self.addCleanup(self._restore_roles)
		roles.install_roles()

	def _restore_roles(self):
		ROLES.clear()
		ROLES.update(self._roles_before)

	# -- helpers -------------------------------------------------------------
	def record(self, **overrides):
		payload = {
			"employee": TRAINEE,
			"training_type": "Heat Illness Prevention",
			"completed_date": frappe.utils.today(),
			"regimes": ["OR-OSHA"],
			"content_topics_covered": TOPICS,
		}
		payload.update(overrides)
		return self.tool_data("record_training", payload)

	def a_certificate(self, docname: str, cert_type: str, days: int = 5, **extra) -> str:
		STORE.seed(
			"Certification",
			[
				{
					"name": docname,
					"cert_name": extra.pop("cert_name", docname),
					"cert_type": cert_type,
					"status": "Active",
					"company": MAIN,
					"expiration_date": days_out(days),
					**extra,
				}
			],
		)
		return docname

	def sweep(self, **args):
		return self.tool_data("refresh_compliance_alerts", {"company": MAIN, **args})

	def tags_on(self, alert: str) -> list:
		return training.rows_for_parents("Compliance Alert", [alert], "regime").get(alert, [])

	def live_alerts(self, alert_type: str) -> list:
		return [
			row
			for row in STORE.rows("Compliance Alert")
			if row.get("alert_type") == alert_type
			and str(row.get("dismissed") or "0").strip().lower() in ("0", "", "false", "none")
		]


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheVocabularyIsStillOnePlace(RegimeTestCase):
	"""Two storage shapes, one definition. See `training.py`'s docstring: a child
	table and a comma-separated column that disagreed about whether a row carries
	WPS is exactly the failure that module was written to prevent."""

	def test_the_master_holds_exactly_what_the_tuple_says(self):
		seeded = {row["name"] for row in STORE.rows(training.REGIME_DOCTYPE)}
		self.assertEqual(seeded, set(training.REGIMES))

	def test_seeding_twice_creates_nothing_the_second_time(self):
		before = len(STORE.rows(training.REGIME_DOCTYPE))
		report = training.seed_regimes()
		self.assertEqual(report["created"], [])
		self.assertEqual(sorted(report["present"]), sorted(training.REGIMES))
		self.assertEqual(len(STORE.rows(training.REGIME_DOCTYPE)), before)

	def test_the_seeder_carries_the_citation_beside_the_number(self):
		"""A retention period nobody can cite is one somebody will shorten."""
		row = dict(STORE.get_raw(training.REGIME_DOCTYPE, "NOP") or {})
		self.assertEqual(int(row["retention_years"]), 5)
		self.assertIn("205.103(b)(4)", row["retention_citation"])

	def test_a_row_somebody_edited_is_not_rewritten_on_the_next_migrate(self):
		"""The seeder is not a fixture, and this is the difference: an operator
		who reworded a description meant to."""
		doc = frappe.get_doc(training.REGIME_DOCTYPE, "WPS")
		doc.description = "Ours, as we run it."
		doc.save(ignore_permissions=True)
		training.seed_regimes()
		self.assertEqual(
			dict(STORE.get_raw(training.REGIME_DOCTYPE, "WPS"))["description"],
			"Ours, as we run it.",
		)

	def test_the_two_new_tokens_are_reachable_through_the_vocabulary(self):
		self.assertEqual(training.canon("otco"), "OTCO")
		self.assertEqual(training.canon("Internal"), "Internal")
		self.assertEqual(training.canon("in-house"), "Internal")

	def test_tilth_still_means_nop_because_records_were_written_through_it(self):
		"""OTCO is what Oregon Tilth actually is, and repointing the alias would
		make one word mean a different set of rows on the read path than it wrote
		on the write path — a filter that stops matching its own history."""
		self.assertEqual(training.canon("tilth"), "NOP")

	def test_child_rows_and_tokens_round_trip_without_losing_order(self):
		rows = training.to_rows(["WPS", "FSMA", "WPS"])
		self.assertEqual([row["regime"] for row in rows], ["FSMA", "WPS"])
		self.assertEqual(training.from_rows(rows), ["FSMA", "WPS"])

	def test_an_unknown_token_never_becomes_a_child_row(self):
		"""Dropped at the boundary rather than left to fail link validation with a
		message about a missing Compliance Regime — a true statement about the
		wrong thing. The write path refuses by name; `require` has that test."""
		self.assertEqual(training.to_rows(["WPS", "OSHA-ish"]), [{"regime": "WPS"}])

	def test_a_rule_naming_a_regime_outside_the_vocabulary_cannot_be_registered(self):
		"""Checked at IMPORT time, like the kairotic gate. A rule tagged with a
		regime nothing knows would raise alerts that no filter and no packet ever
		sees — evidence present, correct and invisible, which is the worst of the
		three ways to be wrong. Failing on import means it cannot ship."""
		with self.assertRaises(RuntimeError) as caught:
			alerts.register(
				alerts.Rule(
					key="a_rule_that_should_not_load",
					title="",
					category="Other",
					kairotic_gate="never",
					regimes=("OSHA-ish",),
					scan=lambda context: [],
				)
			)
		self.assertIn("OSHA-ish", str(caught.exception))
		self.assertNotIn("a_rule_that_should_not_load", alerts.RULES)

	# The three below call `on_trash()` DIRECTLY rather than `delete()`. The double
	# has no delete plumbing, and that is the right seam anyway: what is being
	# asserted is this app's guard, not Frappe's ability to remove a row.
	def test_deleting_a_regime_something_carries_is_refused(self):
		"""A regime deleted out from under an alert leaves a child row pointing at
		nothing, and the record silently stops appearing in the packet it was
		evidence for — quiet evidence loss, which is what `active` exists to avoid."""
		self.a_certificate("LIC-APPLICATOR", "Applicator License")
		self.sweep()
		with self.assertRaises(Exception) as caught:
			frappe.get_doc(training.REGIME_DOCTYPE, "WPS").on_trash()
		self.assertIn("still carried by", str(caught.exception))
		self.assertIn("Untick Active instead", str(caught.exception))

	def test_a_regime_nothing_carries_can_be_removed(self):
		"""The refusal is about what would be ORPHANED, not about this app owning
		the row. An operation that never runs PrimusGFS may tidy it away."""
		frappe.get_doc(training.REGIME_DOCTYPE, "PrimusGFS").on_trash()

	def test_deleting_a_curriculum_with_records_against_it_is_refused(self):
		"""A training log an auditor cannot resolve to a course is a log about an
		afternoon nobody can identify."""
		self.record(training_type="Custom Farm Orientation")
		with self.assertRaises(Exception) as caught:
			frappe.get_doc(training.TYPE_DOCTYPE, "Custom Farm Orientation").on_trash()
		self.assertIn("Untick Active instead", str(caught.exception))

	def test_a_curriculum_keeps_a_longer_retention_somebody_set_by_hand(self):
		"""An operation whose own document-retention policy is seven years is not
		wrong, and overwriting it with the regulator's floor would be this app
		quietly SHORTENING a retention period — the one direction it must never be
		wrong in. A shorter one is raised to the floor."""
		outcome = training.ensure_type("NOP Handler Training")
		doc = frappe.get_doc(training.TYPE_DOCTYPE, outcome["training_type"])
		self.assertEqual(int(doc.retention_years), 5)

		doc.retention_years = 7
		doc.save(ignore_permissions=True)
		self.assertEqual(int(frappe.get_doc(training.TYPE_DOCTYPE, doc.name).retention_years), 7)

		doc.retention_years = 1
		doc.save(ignore_permissions=True)
		self.assertEqual(int(frappe.get_doc(training.TYPE_DOCTYPE, doc.name).retention_years), 5)

	def test_a_regulators_own_spelling_is_not_treated_as_a_near_miss(self):
		"""`OSHA` IS accepted, and that is not a hole in the previous test. The
		alias table carries the spellings a regulator itself uses — OSHA writes
		both "OR-OSHA" and "Oregon OSHA" — and refusing those would be refusing
		the operator's own correct word. What is refused is a token nothing in the
		table resolves."""
		self.assertEqual(training.canon("OSHA"), "OR-OSHA")
		self.assertEqual(training.canon("OSHA-ish"), "")


# ── 2 ───────────────────────────────────────────────────────────────────────
class AlertsCarryTheirRegimes(RegimeTestCase):
	def test_a_constant_rule_tags_every_alert_it_raises(self):
		STORE.seed(
			"Housing Unit",
			[
				{
					"name": "CABIN-9",
					"unit_name": "Cabin 9",
					"owning_entity": MAIN,
					"unit_type": "Cabin",
					"capacity": 4,
					"condition": "Habitable",
					"last_habitability_inspection": days_out(-400),
				}
			],
		)
		self.sweep()
		raised = self.live_alerts("housing_inspection_overdue")
		self.assertTrue(raised, "the overdue inspection should have raised an alert")
		self.assertEqual(self.tags_on(raised[0]["name"]), ["OR-OSHA"])

	def test_the_certificate_rule_tags_per_row_rather_than_per_rule(self):
		"""One rule, eleven kinds of certificate. An applicator licence is WPS
		evidence and a GlobalGAP certificate is not, so a constant on the rule
		would be wrong ten times out of eleven."""
		self.a_certificate("LIC-APPLICATOR", "Applicator License")
		self.a_certificate("CERT-GLOBALGAP", "GlobalGAP")
		self.a_certificate("CERT-ORGANIC", "Organic")
		self.sweep()

		by_source = {
			row["source_docname"]: self.tags_on(row["name"])
			for row in self.live_alerts("certification_expiring")
		}
		self.assertEqual(by_source["LIC-APPLICATOR"], ["WPS", "OR-OSHA"])
		self.assertEqual(by_source["CERT-ORGANIC"], ["NOP"])
		# THE SUBSTRING TRAP: "GlobalGAP" contains "GAP", and tagging it GAP would
		# put another scheme's certificate in front of a USDA GAP auditor.
		self.assertEqual(by_source["CERT-GLOBALGAP"], ["GlobalGAP"])

	def test_a_certificate_type_this_app_does_not_model_is_internal_not_bare(self):
		"""An alert nobody can filter to is worse than one filed under 'ours'."""
		self.a_certificate("CDL-0001", "Commercial Driver License")
		self.sweep()
		found = [
			row for row in self.live_alerts("certification_expiring")
			if row["source_docname"] == "CDL-0001"
		]
		self.assertEqual(self.tags_on(found[0]["name"]), ["Internal"])

	def test_the_training_rule_copies_the_records_own_tags(self):
		"""The record says what THAT afternoon covered. Inheriting from the
		curriculum would produce an alert asserting evidence the record does not
		support."""
		self.record(
			training_type="WPS Handler Training",
			completed_date=days_out(-370),
			expires_date=days_out(-5),
			regimes=["WPS", "GAP"],
		)
		self.sweep()
		raised = self.live_alerts("training_expiring")
		self.assertEqual(self.tags_on(raised[0]["name"]), ["GAP", "WPS"])

	def test_an_untagged_record_produces_an_untagged_alert(self):
		"""Deliberately. Inventing a tag so the row is not bare would make the
		calendar claim coverage the register does not have — and the alert's own
		message already tells the reader how to fix it."""
		self.record(expires_date=days_out(-3), completed_date=days_out(-370))
		frappe.db.set_value(
			training.DOCTYPE,
			STORE.rows(training.DOCTYPE)[0]["name"],
			"regimes",
			"",
		)
		self.sweep()
		raised = self.live_alerts("training_expiring")
		self.assertEqual(self.tags_on(raised[0]["name"]), [])

	def test_a_retag_in_a_later_release_reaches_alerts_already_on_the_site(self):
		"""Tags are rewritten on every refresh, not fixed at first raise —
		otherwise an operation's oldest and most chronic items would sit
		permanently outside the filter built to find them."""
		self.a_certificate("LIC-APPLICATOR", "Applicator License")
		self.sweep()
		alert = self.live_alerts("certification_expiring")[0]["name"]

		doc = frappe.get_doc("Compliance Alert", alert)
		training.set_rows(doc, "regime", ["Other"])
		doc.save(ignore_permissions=True)
		self.assertEqual(self.tags_on(alert), ["Other"])

		self.sweep()
		self.assertEqual(self.tags_on(alert), ["WPS", "OR-OSHA"])

	def test_every_shipped_rule_names_at_least_one_regime(self):
		"""An untagged rule is invisible to every regime filter, and silently
		invisible is the one thing a compliance calendar must not be."""
		for key in alerts.names():
			with self.subTest(rule=key):
				self.assertTrue(
					training.parse(alerts.RULES[key].regimes),
					f"{key} carries no regime this app knows",
				)


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheCalendarReadsOneAuditAtATime(RegimeTestCase):
	def a_calendar_with_two_kinds_of_item(self):
		self.a_certificate("LIC-APPLICATOR", "Applicator License")  # WPS, OR-OSHA
		STORE.seed(
			"Housing Unit",
			[
				{
					"name": "CABIN-9",
					"unit_name": "Cabin 9",
					"owning_entity": MAIN,
					"unit_type": "Cabin",
					"capacity": 4,
					"condition": "Habitable",
					"last_habitability_inspection": days_out(-400),
				}
			],
		)
		self.sweep()

	def test_a_regime_filter_returns_only_the_alerts_tagged_with_it(self):
		self.a_calendar_with_two_kinds_of_item()
		everything = self.tool_data("get_compliance_calendar", {"company": MAIN})
		wps = self.tool_data("get_compliance_calendar", {"company": MAIN, "regime": "WPS"})

		self.assertGreater(everything["alert_count"], wps["alert_count"])
		self.assertEqual(wps["regime"], "WPS")
		types = {alert["alert_type"] for group in wps["by_category"].values() for alert in group["alerts"]}
		self.assertIn("certification_expiring", types)
		self.assertNotIn("housing_inspection_overdue", types)

	def test_every_alert_reports_the_tags_it_actually_carries(self):
		self.a_calendar_with_two_kinds_of_item()
		data = self.tool_data("get_compliance_calendar", {"company": MAIN})
		by_type = {
			alert["alert_type"]: alert["regimes"]
			for group in data["by_category"].values()
			for alert in group["alerts"]
		}
		self.assertEqual(by_type["housing_inspection_overdue"], ["OR-OSHA"])
		self.assertEqual(by_type["certification_expiring"], ["WPS", "OR-OSHA"])

	def test_a_regulators_own_spelling_is_accepted(self):
		self.a_calendar_with_two_kinds_of_item()
		data = self.tool_data("get_compliance_calendar", {"company": MAIN, "regime": "oregon osha"})
		self.assertEqual(data["regime"], "OR-OSHA")
		self.assertGreater(data["alert_count"], 0)

	def test_an_unrecognised_regime_is_refused_rather_than_returning_nothing(self):
		"""THE POINT OF THE REFUSAL: an empty compliance calendar reads as a clean
		one, which is the most expensive way for this tool to be wrong."""
		message = self.tool_error("get_compliance_calendar", {"company": MAIN, "regime": "OSHA-ish"})
		self.assertIn("OSHA-ish", message)
		self.assertIn("OR-OSHA", message)
		self.assertIn("reads as a clean one", message)

	def test_globalgap_evidence_stays_out_of_a_gap_calendar(self):
		"""'GlobalGAP' contains 'GAP'. A substring match here would put another
		scheme's findings in front of a USDA GAP auditor."""
		self.a_certificate("CERT-GLOBALGAP", "GlobalGAP")
		self.sweep()
		gap = self.tool_data("get_compliance_calendar", {"company": MAIN, "regime": "GAP"})
		globalgap = self.tool_data("get_compliance_calendar", {"company": MAIN, "regime": "GlobalGAP"})
		self.assertEqual(gap["alert_count"], 0)
		self.assertEqual(globalgap["alert_count"], 1)

	def test_the_breakdown_counts_every_tag_and_names_the_untagged(self):
		self.a_calendar_with_two_kinds_of_item()
		data = self.tool_data("get_compliance_calendar", {"company": MAIN})
		self.assertEqual(data["by_regime"]["OR-OSHA"], 2)
		self.assertEqual(data["by_regime"]["WPS"], 1)


# ── 4 ───────────────────────────────────────────────────────────────────────
class ANarrowedSweepDismissesNothing(RegimeTestCase):
	"""THE SHARPEST EDGE IN THE RELEASE. A filtered sweep that auto-dismissed the
	rules it did not run would clear most of the calendar and call it progress."""

	def test_the_rules_that_do_not_answer_to_the_regime_are_skipped_not_cleared(self):
		self.a_certificate("LIC-APPLICATOR", "Applicator License")
		STORE.seed(
			"Housing Unit",
			[
				{
					"name": "CABIN-9",
					"unit_name": "Cabin 9",
					"owning_entity": MAIN,
					"unit_type": "Cabin",
					"capacity": 4,
					"condition": "Habitable",
					"last_habitability_inspection": days_out(-400),
				}
			],
		)
		self.sweep()
		self.assertEqual(len(self.live_alerts("housing_inspection_overdue")), 1)

		report = self.sweep(regime="WPS")

		# The camp alert is UNTOUCHED — not refreshed, and above all not dismissed.
		self.assertEqual(len(self.live_alerts("housing_inspection_overdue")), 1)
		self.assertEqual(report["auto_dismissed"], 0)
		skipped = {entry["alert_type"] for entry in report["rules_skipped"]}
		self.assertIn("housing_inspection_overdue", skipped)
		self.assertNotIn("housing_inspection_overdue", report["rules_run"])
		self.assertIn("certification_expiring", report["rules_run"])

	def test_the_report_says_it_was_narrowed_and_why_the_counts_are_partial(self):
		report = self.sweep(regime="OR-OSHA")
		self.assertEqual(report["regime"], "OR-OSHA")
		self.assertIn("DISMISSED NOTHING", report["regime_note"])

	def test_a_per_row_rule_runs_whenever_its_union_contains_the_regime(self):
		"""A WPS sweep has to rescan every certificate, because whether any one of
		them is WPS evidence is a fact about the row rather than about the rule."""
		self.a_certificate("LIC-APPLICATOR", "Applicator License")
		report = self.sweep(regime="WPS")
		self.assertIn("certification_expiring", report["rules_run"])
		self.assertEqual(len(self.live_alerts("certification_expiring")), 1)

	def test_an_unrecognised_regime_is_refused_before_anything_is_written(self):
		message = self.tool_error("refresh_compliance_alerts", {"company": MAIN, "regime": "OSHA-ish"})
		self.assertIn("OR-OSHA", message)
		self.assertEqual(STORE.rows("Compliance Alert"), [])


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheMigrationIsIdempotent(RegimeTestCase):
	"""Pre-v0.19.2 records hold free text in a column that is now a Link. The
	docname IS that text, so the ordinary case rewrites no evidence at all."""

	def a_legacy_record(self, text: str, docname: str = "ETR-2026-01-00001") -> str:
		"""One training record as v0.19.0 wrote it: free text, no Training Type."""
		STORE.seed(
			training.DOCTYPE,
			[
				{
					"name": docname,
					"employee": TRAINEE,
					"employee_name": "Ben Packhouse",
					"company": MAIN,
					"training_type": text,
					"completed_date": days_out(-30),
					"regimes": "OR-OSHA",
					"content_topics_covered": "Heat, water, shade",
					"status": "Active",
				}
			],
		)
		return docname

	def test_free_text_becomes_a_curriculum_with_the_regimes_its_name_implies(self):
		self.a_legacy_record("WPS Handler Training")
		report = migrate_training_types.migrate_training_types()

		self.assertEqual(report["types_created"], ["WPS Handler Training"])
		self.assertEqual(
			dict(STORE.get_raw(training.DOCTYPE, "ETR-2026-01-00001"))["training_type"],
			"WPS Handler Training",
		)
		self.assertEqual(training.type_regimes("WPS Handler Training"), ["WPS"])

	def test_running_it_twice_creates_no_duplicate(self):
		self.a_legacy_record("WPS Handler Training")
		migrate_training_types.migrate_training_types()
		second = migrate_training_types.migrate_training_types()

		self.assertEqual(second["types_created"], [])
		self.assertEqual(second["records_relinked"], [])
		self.assertEqual(len(STORE.rows(training.TYPE_DOCTYPE)), 1)

	def test_two_spellings_of_one_course_produce_one_curriculum(self):
		"""Case- and space-insensitive, or one curriculum's history would be split
		across two masters and each would look like half an operation's training."""
		self.a_legacy_record("WPS Handler Training", "ETR-2026-01-00001")
		self.a_legacy_record("wps  handler training", "ETR-2026-01-00002")
		report = migrate_training_types.migrate_training_types()

		self.assertEqual(len(STORE.rows(training.TYPE_DOCTYPE)), 1)
		self.assertEqual(len(report["records_relinked"]), 1)
		self.assertEqual(
			dict(STORE.get_raw(training.DOCTYPE, "ETR-2026-01-00002"))["training_type"],
			"WPS Handler Training",
		)

	def test_it_does_not_touch_the_regimes_on_any_existing_record(self):
		"""The guess is about the CURRICULUM. The record says what that afternoon
		covered, and replacing it with a heuristic would be replacing evidence."""
		self.a_legacy_record("WPS Handler Training")
		migrate_training_types.migrate_training_types()
		self.assertEqual(
			dict(STORE.get_raw(training.DOCTYPE, "ETR-2026-01-00001"))["regimes"],
			"OR-OSHA",
		)

	def test_a_name_implying_nothing_gets_internal_rather_than_no_tag(self):
		self.a_legacy_record("Monday Morning Meeting")
		migrate_training_types.migrate_training_types()
		self.assertEqual(training.type_regimes("Monday Morning Meeting"), ["Internal"])

	def test_a_globalgap_course_is_not_filed_as_usda_gap(self):
		self.a_legacy_record("GlobalGAP Refresher")
		migrate_training_types.migrate_training_types()
		self.assertEqual(training.type_regimes("GlobalGAP Refresher"), ["GlobalGAP"])

	def test_an_empty_site_migrates_without_complaint(self):
		report = migrate_training_types.migrate_training_types()
		self.assertEqual(report["scanned"], 0)
		self.assertEqual(migrate_training_types.report_lines(report), [])

	def test_the_seeded_curricula_are_idempotent_too(self):
		first = training.seed_training_types()
		second = training.seed_training_types()
		self.assertEqual(len(first["created"]), len(training.SEED_TRAINING_TYPES))
		self.assertEqual(second["created"], [])
		self.assertEqual(len(STORE.rows(training.TYPE_DOCTYPE)), len(training.SEED_TRAINING_TYPES))

	def test_every_seeded_curriculum_keeps_records_as_long_as_its_longest_regime(self):
		"""Derived rather than stated, so a seed cannot contradict the doctrine
		that the longest tag governs — a curriculum tagged GAP and NOP whose
		retention said two years would be a standing instruction to destroy the
		NOP evidence three years early."""
		training.seed_training_types()
		for name, regimes, _description in training.SEED_TRAINING_TYPES:
			with self.subTest(training_type=name):
				row = dict(STORE.get_raw(training.TYPE_DOCTYPE, name) or {})
				self.assertEqual(int(row["retention_years"]), training.retention_years(regimes))


# ── 6 ───────────────────────────────────────────────────────────────────────
class FreeTextStillFilesARecord(RegimeTestCase):
	"""The ergonomics v0.19.0 defended, kept. A Link that refused an unrecognised
	course would train people to file training under whatever name already
	exists, which is worse than free text ever was."""

	def test_a_course_this_site_has_never_run_is_created_rather_than_refused(self):
		data = self.record(training_type="Custom Farm Orientation", regimes=["GAP"])
		self.assertTrue(data["training_type_created"])
		self.assertEqual(data["training_type"], "Custom Farm Orientation")
		self.assertTrue(frappe.db.exists(training.TYPE_DOCTYPE, "Custom Farm Orientation"))

	def test_the_new_curriculum_takes_its_name_s_regimes_not_this_sessions(self):
		"""A heat session a crew leader also used to cover hygiene is tagged GAP on
		the RECORD and must not make the curriculum a GAP course forever."""
		data = self.record(training_type="Custom Farm Orientation", regimes=["GAP"])
		self.assertEqual(training.type_regimes("Custom Farm Orientation"), ["Internal"])
		self.assertEqual(data["regimes"], ["GAP"])
		self.assertIn("ABOUT THE COURSE, NOT ABOUT THIS SESSION", data["training_type_note"])

	def test_a_second_record_of_the_same_course_creates_nothing(self):
		self.record(training_type="Custom Farm Orientation")
		second = self.record(training_type="Custom Farm Orientation")
		self.assertFalse(second["training_type_created"])
		self.assertEqual(len(STORE.rows(training.TYPE_DOCTYPE)), 1)

	def test_casing_finds_the_existing_curriculum_instead_of_splitting_it(self):
		self.record(training_type="Custom Farm Orientation")
		second = self.record(training_type="custom farm orientation")
		self.assertEqual(second["training_type"], "Custom Farm Orientation")
		self.assertEqual(len(STORE.rows(training.TYPE_DOCTYPE)), 1)

	def test_a_session_that_disagrees_with_its_curriculum_is_recorded_and_flagged(self):
		"""Recorded as given — a session that ran short is entitled to say so —
		but worth a second look if it was not deliberate."""
		self.record(training_type="WPS Handler Training", regimes=["WPS"])
		data = self.record(training_type="WPS Handler Training", regimes=["GAP"])
		self.assertEqual(data["regimes"], ["GAP"])
		self.assertIn("normally answers to", data["training_type_note"])

	def test_the_desk_path_creates_the_curriculum_too(self):
		"""In `validate`, not in the tool — otherwise a data import or the iOS app
		would get a link error where the tool gets a record."""
		doc = frappe.new_doc(training.DOCTYPE)
		doc.employee = TRAINEE
		doc.training_type = "Barn Safety Walkthrough"
		doc.completed_date = days_out(-1)
		doc.regimes = "Internal"
		doc.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists(training.TYPE_DOCTYPE, "Barn Safety Walkthrough"))


# ── 7 ───────────────────────────────────────────────────────────────────────
class ThePacketAnswersToOneRegime(RegimeTestCase):
	def a_farm_with_evidence_and_open_items(self):
		self.record(
			training_type="WPS Handler Training",
			completed_date=days_out(-40),
			expires_date=days_out(325),
			regimes=["WPS"],
		)
		self.record(
			training_type="NOP Handler Training",
			completed_date=days_out(-40),
			regimes=["NOP"],
		)
		self.a_certificate("LIC-APPLICATOR", "Applicator License")  # WPS, OR-OSHA
		STORE.seed(
			"Housing Unit",
			[
				{
					"name": "CABIN-9",
					"unit_name": "Cabin 9",
					"owning_entity": MAIN,
					"unit_type": "Cabin",
					"capacity": 4,
					"condition": "Habitable",
					"last_habitability_inspection": days_out(-400),
				}
			],
		)
		self.sweep()

	def build(self, regime: str = "") -> dict:
		return audit_packets.build(
			audit_packets.get("GAP"),
			MAIN,
			days_out(-90),
			days_out(-1),
			regime=regime,
		)

	def sections_of(self, packet: dict) -> dict:
		return {section["key"]: section for section in packet["sections"]}

	def test_a_narrowed_packet_carries_only_that_regimes_training_and_items(self):
		self.a_farm_with_evidence_and_open_items()
		sections = self.sections_of(self.build(regime="WPS"))

		trainings = {row["training"] for row in sections["training"]["rows"]}
		self.assertEqual(trainings, {"WPS Handler Training"})

		alert_rows = sections["alerts"]["rows"]
		self.assertTrue(alert_rows, "the applicator licence should be an open WPS item")
		self.assertTrue(all("WPS" in row["regimes"] for row in alert_rows))
		self.assertFalse(any("housing" in row["alert"] for row in alert_rows))

	def test_without_a_regime_nothing_is_narrowed(self):
		"""Backward compatibility, stated as a test: the packet's own scoping is
		what applies, exactly as it did before v0.19.2."""
		self.a_farm_with_evidence_and_open_items()
		packet = self.build()
		sections = self.sections_of(packet)

		self.assertIsNone(packet["regime_override"])
		self.assertIsNone(packet["training_regime_override"])
		self.assertEqual(sections["alerts"]["regimes_pulled"], ["GAP", "WPS"])
		# Wider than the WPS packet: the camp item is OR-OSHA and stays out of a
		# GAP packet either way, but the licence and the training both land.
		self.assertGreaterEqual(sections["alerts"]["row_count"], 1)

	def test_the_old_key_still_holds_the_value_the_new_one_does(self):
		"""`training_regime_override` is on every packet produced since v0.19.0. A
		key renamed is a key that silently reads as None in whatever consumed it."""
		packet = self.build(regime="WPS")
		self.assertEqual(packet["training_regime_override"], "WPS")
		self.assertEqual(packet["regime_override"], "WPS")

	def test_snoozed_and_dismissed_items_are_not_open_obligations(self):
		self.a_farm_with_evidence_and_open_items()
		alert = self.live_alerts("certification_expiring")[0]["name"]
		frappe.db.set_value("Compliance Alert", alert, "snoozed_until", days_out(30))

		rows = self.sections_of(self.build(regime="WPS"))["alerts"]["rows"]
		self.assertFalse([row for row in rows if row["alert"] == alert])

	def test_an_empty_section_says_so_rather_than_reading_as_nothing_outstanding(self):
		packet = self.build(regime="WPS")
		section = self.sections_of(packet)["alerts"]
		self.assertEqual(section["row_count"], 0)
		self.assertIn("Snoozed and dismissed items are excluded", section["empty_note"])

	def test_every_audit_type_carries_the_open_items_section(self):
		for key in audit_packets.names():
			with self.subTest(audit_type=key):
				self.assertIn("alerts", audit_packets.get(key).sections)


if __name__ == "__main__":
	import unittest

	unittest.main()
