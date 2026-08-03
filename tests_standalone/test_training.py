# SPDX-License-Identifier: MIT
"""The training register — v0.19.0's four tools, the twelfth rule, and the packet section.

THE CLAIM BEHIND THE WHOLE RELEASE is that a compliance calendar which can see
every DOCUMENT on the farm and nothing a PERSON knows is half a compliance
system. Eleven rules watched certificates, policies, cabins, water, filings and
audits. None watched training — which is what WPS asks for every twelve months,
what Oregon's heat rule asks for annually before the first hot shift, what FSMA
Subpart C asks for on hiring and periodically, and what a GAP auditor asks for by
name with the signature attached.

EIGHT CLAIMS.

1. `TheRecordComputesItsOwnStatus` — status, activity_datetime, the farm-name
   snapshot and the canonical regime spelling are derived on save, and every
   date the record cannot mean is refused.

2. `LapsedTrainingReachesTheCalendar` — a record with a past expiry raises a
   Critical alert on the next sweep, one sixty days out raises Warning, and a
   record with NO expiry raises nothing at all. The last is the one worth
   testing: a renewal alert nobody can clear is how a calendar stops being read.

3. `OneAfternoonAnswersFourAudits` — a record tagged GAP and WPS appears in the
   GAP packet AND the EPA packet, and a GlobalGAP-only record does NOT appear in
   the GAP packet, because `"GlobalGAP"` contains `"GAP"` and a substring match
   would hand a USDA auditor evidence from another scheme.

4. `TheGuards` — the role gate, the company scope and the kill switch, all three
   on the principal this app acts as.

5. `TheSupervisorReview` — the §112.161(b) flow: created without it, refused for
   a self-review, refused for a review dated before the training, and populated
   by the tool that exists for it alone.

6. `ReadingTheRegister` — the regime, status, expiry-window and unreviewed
   filters, all computed as of TODAY rather than read off a stored column.

7. `TheRetentionAnswer` — five years where any tag is NOP, longest tag governs,
   with the citation beside the number.

8. `TheComplianceAnnex` — `generate_compliance_packet(regime=...)` staples the
   right training records to an accounting packet and states the period it used.
"""

import frappe

from erpnext_mcp import audit_packets, roles, training
from erpnext_mcp.alerts import base as alerts_base

from .fixtures import MAIN, OTHER, V12TestCase, install_hrms
from .harness import ROLES, STORE, set_roles

#: Every switch this suite needs. Listed rather than globbed so that turning one
#: off in a test is visibly a change from the on-by-default posture.
ON = {
	f"allow_{name}": 1
	for name in (
		"record_training",
		"list_trainings",
		"get_training",
		"sign_training_supervisor_review",
		"create_employee",
		"refresh_compliance_alerts",
		"get_compliance_calendar",
		"generate_audit_packet",
		"generate_compliance_packet",
	)
}

TRAINEE = "HR-EMP-00002"  # Ben Packhouse, Active, at MAIN
SUPERVISOR = "HR-EMP-00001"  # Ada Orchard, Active, at MAIN

TOPICS = "Heat index, water, shade, symptoms, reporting, emergency response"


def days_out(count: int) -> str:
	return str(frappe.utils.add_days(frappe.utils.today(), count))


class TrainingTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ON)
		# The double ships without an Employee register — Frappe HR is a separate
		# app, and `record_training` refuses on a site that has none.
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

	def record_error(self, **overrides):
		payload = {
			"employee": TRAINEE,
			"training_type": "Heat Illness Prevention",
			"completed_date": frappe.utils.today(),
			"regimes": ["OR-OSHA"],
			"content_topics_covered": TOPICS,
		}
		payload.update(overrides)
		return self.tool_error("record_training", payload)

	def raw(self, name: str) -> dict:
		return dict(STORE.get_raw(training.DOCTYPE, name) or {})

	def sweep(self, alert_type="training_expiring") -> list:
		"""Run the hourly compliance sweep and return this rule's live alerts."""
		self.tool_data("refresh_compliance_alerts", {"company": MAIN})
		return [
			row
			for row in STORE.rows("Compliance Alert")
			if row.get("alert_type") == alert_type
			and str(row.get("dismissed") or "0").strip().lower() in ("0", "", "false", "none")
		]

	def an_employee_at(self, company: str, name: str, docname: str) -> str:
		STORE.seed(
			"Employee",
			[
				{
					"name": docname,
					"employee_name": name,
					"status": "Active",
					"date_of_joining": "2025-01-01",
					"company": company,
				}
			],
		)
		return docname


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheRecordComputesItsOwnStatus(TrainingTestCase):
	def test_every_field_it_takes_is_written_and_the_derived_ones_are_derived(self):
		data = self.record(
			training_type="WPS Handler Training",
			training_source="External",
			provider="OSU Extension",
			completed_date=days_out(-10),
			completed_time="14:30",
			expires_date=days_out(355),
			regimes=["WPS", "GAP"],
			content_topics_covered="Label reading, PPE, REI, decontamination",
			certificate_file="/files/wps-card.pdf",
			person_performed_signature="/files/ben-signature.png",
			notes="Delivered in Spanish.",
		)
		row = self.raw(data["name"])

		self.assertEqual(row["employee"], TRAINEE)
		self.assertEqual(row["employee_name"], "Ben Packhouse")
		self.assertEqual(row["company"], MAIN)
		self.assertEqual(row["training_source"], "External")
		self.assertEqual(row["provider"], "OSU Extension")
		# Canonical spelling and REGIMES ORDER, not the order they arrived.
		self.assertEqual(row["regimes"], "GAP,WPS")
		self.assertEqual(row["status"], "Active")
		self.assertEqual(data["regimes"], ["GAP", "WPS"])
		self.assertTrue(data["trainee_signed"])
		self.assertTrue(data["certificate_attached"])
		self.assertEqual(data["content_topics_covered"][0], "Label reading")

	def test_the_activity_datetime_is_the_stamp_112_161_asks_for(self):
		"""§112.161(a)(1)(v) wants date AND time, and a packet should not have to
		stitch two columns together on the page an inspector is reading."""
		data = self.record(completed_date=days_out(-1), completed_time="07:05")
		self.assertEqual(self.raw(data["name"])["activity_datetime"], f"{days_out(-1)} 07:05:00")

	def test_no_time_recorded_becomes_midnight_rather_than_a_plausible_hour(self):
		"""The day is known and the hour is not. Inventing one would be worse than
		admitting the gap, because the gap is what `fsma_112_161_gaps` reports."""
		data = self.record(completed_date=days_out(-1))
		self.assertTrue(self.raw(data["name"])["activity_datetime"].endswith("00:00:00"))
		# The stamp exists, so §112.161(a)(1)(v) is NOT reported as a gap — the
		# day is recorded. What is missing is the hour, and claiming a gap for it
		# would bury the four gaps that are real.
		self.assertNotIn("§112.161(a)(1)(v)", " ".join(data["fsma_112_161_gaps"]))

	def test_the_farm_name_is_snapshotted_onto_the_record_itself(self):
		"""§112.161(a)(1)(i). A lookup through the Company link at report time
		answers with today's name; an inspector reading last season's logs is
		entitled to the name that was on the gate then."""
		data = self.record()
		self.assertEqual(self.raw(data["name"])["farm_name_snapshot"], MAIN)

	def test_the_docname_reads_as_a_month_somebody_can_find(self):
		data = self.record(completed_date="2026-07-14")
		self.assertTrue(data["name"].startswith("ETR-2026-07-"), data["name"])

	def test_no_expiry_is_active_forever_and_says_which(self):
		data = self.record()
		self.assertEqual(data["status"], "Active")
		self.assertTrue(data["one_time"])
		self.assertIn("one-time training", data["expiry_note"])
		self.assertIn("WRONG for WPS", data["expiry_note"])

	def test_an_expiry_sixty_days_out_is_expiring(self):
		data = self.record(expires_date=days_out(60))
		self.assertEqual(data["status"], "Expiring")
		self.assertEqual(data["days_until_expiry"], 60)

	def test_an_expiry_in_the_past_is_expired(self):
		data = self.record(completed_date=days_out(-400), expires_date=days_out(-35))
		self.assertEqual(data["status"], "Expired")
		self.assertEqual(data["days_until_expiry"], -35)

	def test_an_expiry_a_year_out_is_active(self):
		self.assertEqual(self.record(expires_date=days_out(365))["status"], "Active")

	def test_training_dated_in_the_future_is_refused(self):
		message = self.record_error(completed_date=days_out(3))
		self.assertIn("in the future", message)
		self.assertIn("112.161(a)(2)", message)

	def test_an_expiry_before_the_completion_is_refused_rather_than_swapped(self):
		message = self.record_error(completed_date=days_out(-5), expires_date=days_out(-10))
		self.assertIn("expired before it was delivered", message)

	def test_an_unknown_regime_is_refused_by_name_with_the_eight_listed(self):
		"""'OSHA' for 'OR-OSHA' would file the evidence where no packet looks for
		it, and nobody finds that out until an inspector does."""
		message = self.record_error(regimes=["OSHA-ish"])
		self.assertIn("OSHA-ish", message)
		self.assertIn("OR-OSHA", message)
		self.assertIn("Nothing was created", message)

	def test_a_regulators_own_spelling_is_accepted_and_canonicalised(self):
		data = self.record(regimes="oregon osha, 40 CFR 170")
		self.assertEqual(data["regimes"], ["WPS", "OR-OSHA"])

	def test_topics_are_required_because_a_tag_without_them_is_a_claim(self):
		message = self.record_error(content_topics_covered="")
		self.assertIn("content_topics_covered is required", message)
		self.assertIn("six topics", message)

	def test_no_regime_at_all_is_refused(self):
		message = self.record_error(regimes=[])
		self.assertIn("regimes is required", message)
		self.assertIn("appears in no packet", message)

	def test_a_renewal_adds_a_record_and_names_what_it_supersedes(self):
		"""Last year's card is the evidence about last year. Editing it would
		destroy the only proof the crew was trained then."""
		first = self.record(completed_date=days_out(-370), expires_date=days_out(-5))
		second = self.record(completed_date=days_out(-2), expires_date=days_out(363))
		self.assertNotEqual(first["name"], second["name"])
		self.assertEqual([row["name"] for row in second["supersedes"]], [first["name"]])
		self.assertTrue(frappe.db.exists(training.DOCTYPE, first["name"]))


# ── 2 ───────────────────────────────────────────────────────────────────────
class LapsedTrainingReachesTheCalendar(TrainingTestCase):
	def test_expired_training_raises_a_critical_alert_on_the_next_sweep(self):
		record = self.record(
			training_type="WPS Handler Training",
			regimes=["WPS"],
			completed_date=days_out(-400),
			expires_date=days_out(-35),
		)
		alerts = self.sweep()
		self.assertEqual(len(alerts), 1, alerts)
		alert = alerts[0]
		self.assertEqual(alert["severity"], "Critical")
		self.assertEqual(alert["source_docname"], record["name"])
		self.assertEqual(alert["category"], "Workforce")
		self.assertIn("EXPIRED 35 day(s) ago", alert["alert_message"])
		# The regimes are IN the message, because "expires in 12 days" and "WPS
		# handler training expires in 12 days" are different decisions.
		self.assertIn("WPS", alert["alert_message"])
		self.assertIn("cannot lawfully perform an application", alert["alert_message"])

	def test_sixty_days_out_is_a_warning_and_names_the_ninety_day_window(self):
		self.record(expires_date=days_out(60))
		alerts = self.sweep()
		self.assertEqual(len(alerts), 1, alerts)
		self.assertEqual(alerts[0]["severity"], "Warning")
		self.assertIn("expires in 60 day(s)", alerts[0]["alert_message"])
		self.assertIn("90-day window", alerts[0]["alert_message"])

	def test_inside_thirty_days_it_becomes_critical(self):
		self.record(expires_date=days_out(12))
		alerts = self.sweep()
		self.assertEqual(alerts[0]["severity"], "Critical")
		self.assertIn("expires in 12 day(s)", alerts[0]["alert_message"])

	def test_two_hundred_days_out_raises_nothing_however_often_the_sweep_runs(self):
		"""The kairotic gate, as a number. A rule that fired on the first of the
		month would fire on this record eleven times before it mattered."""
		self.record(expires_date=days_out(200))
		self.assertEqual(self.sweep(), [])
		self.assertEqual(self.sweep(), [])

	def test_one_time_training_never_raises_anything(self):
		"""A renewal alert nobody can clear is how a compliance calendar stops
		being read."""
		self.record(training_type="PSA Grower Training", regimes=["FSMA"])
		self.assertEqual(self.sweep(), [])

	def test_the_alert_clears_itself_when_a_newer_record_pushes_the_expiry_out(self):
		"""The alert is a statement about the present, not a task somebody closed.

		The lapsed record is still there and still lapsed, so its own alert
		remains — what changes is that the crew is trained again, which is the
		fact the NEW record carries. Both are true and both are shown; the point
		asserted here is that filing the retraining does not silently rewrite
		history by dismissing the evidence of the lapse."""
		lapsed = self.record(completed_date=days_out(-400), expires_date=days_out(-5))
		self.assertEqual(len(self.sweep()), 1)
		self.record(completed_date=days_out(-1), expires_date=days_out(364))
		alerts = self.sweep()
		self.assertEqual([row["source_docname"] for row in alerts], [lapsed["name"]])

	def test_the_rule_is_registered_with_a_kairotic_gate_and_its_citations(self):
		rule = alerts_base.RULES["training_expiring"]
		self.assertEqual(rule.category, "Workforce")
		self.assertEqual(rule.requires, (training.DOCTYPE,))
		self.assertIn("170.401", rule.framework)
		self.assertIn("437-004-1131", rule.framework)
		self.assertIn("112.161", rule.framework)
		self.assertIn("expires_date", rule.kairotic_gate)

	def test_it_has_a_dispatch_recipe_so_the_alert_can_become_work(self):
		"""Every other alert type can be turned into a Farm Task. One that could
		not would be an alert with nowhere to go."""
		from erpnext_mcp.tools.dispatch import ALERT_TASK_MAP

		recipe = ALERT_TASK_MAP["training_expiring"]
		# Deliberately empty: completing the task is arranging a retraining, and
		# no builder can invent the topics covered or the trainee's signature.
		self.assertEqual(recipe["creates_record"], "")
		self.assertTrue(recipe["evidence"]["signature"])


# ── 3 ───────────────────────────────────────────────────────────────────────
class OneAfternoonAnswersFourAudits(TrainingTestCase):
	"""The architecture, asserted: one record, many packets, matched by TAG."""

	def packet(self, audit_type: str, regime: str = "") -> dict:
		spec = audit_packets.TYPES[audit_type]
		built = audit_packets.build(spec, MAIN, days_out(-30), frappe.utils.today(), regime=regime)
		return next(
			section for section in built["sections"] if section["key"] == "training"
		)

	def test_a_gap_and_wps_record_lands_in_both_the_gap_and_the_epa_packet(self):
		record = self.record(
			training_type="Harvest Hygiene and Pesticide Safety",
			regimes=["GAP", "WPS"],
			completed_date=days_out(-7),
		)
		for audit_type in ("GAP", "EPA", "FSMA", "OSHA"):
			with self.subTest(audit_type=audit_type):
				rows = self.packet(audit_type)["rows"]
				self.assertEqual([row["record"] for row in rows], [record["name"]])

	def test_a_globalgap_record_is_not_pulled_into_a_usda_gap_packet(self):
		"""`"GlobalGAP"` CONTAINS `"GAP"`. A LIKE filter would hand a USDA auditor
		evidence from a different scheme, quietly, and this is the assertion that
		stops somebody optimising the Python filter into SQL."""
		self.record(regimes=["GlobalGAP"], completed_date=days_out(-7))
		self.assertEqual(self.packet("GAP")["rows"], [])
		self.assertEqual(len(self.packet("GlobalGAP")["rows"]), 1)

	def test_an_organic_record_stays_out_of_the_pesticide_packet(self):
		"""An EPA packet containing a worker's organic-handling training invites a
		question nobody wanted to answer."""
		self.record(training_type="Organic Handling", regimes=["NOP"], completed_date=days_out(-7))
		self.assertEqual(self.packet("EPA")["rows"], [])

	def test_the_regime_argument_narrows_the_section_and_nothing_else(self):
		self.record(regimes=["GAP"], completed_date=days_out(-7), training_type="Hygiene")
		self.record(regimes=["WPS"], completed_date=days_out(-7), training_type="Handler")
		self.assertEqual(len(self.packet("GAP")["rows"]), 2)
		narrowed = self.packet("GAP", regime="WPS")
		self.assertEqual([row["training"] for row in narrowed["rows"]], ["Handler"])
		self.assertEqual(narrowed["regimes_pulled"], ["WPS"])

	def test_training_outside_the_period_is_not_in_the_packet_for_that_period(self):
		self.record(regimes=["GAP"], completed_date=days_out(-200))
		self.assertEqual(self.packet("GAP")["rows"], [])

	def test_training_that_has_since_lapsed_is_still_in_the_period_it_happened_in(self):
		"""An auditor asking about the period is asking what the crew had been
		taught by then. Dropping it would OVERSTATE the position."""
		self.record(regimes=["GAP"], completed_date=days_out(-7), expires_date=days_out(-1))
		section = self.packet("GAP")
		self.assertEqual(len(section["rows"]), 1)
		self.assertEqual(section["rows"][0]["status_at_period_end"], "Expired")
		self.assertEqual(len(section["expired_by_period_end"]), 1)

	def test_the_missing_signatures_are_disclosed_in_the_packet_not_filtered_out(self):
		self.record(regimes=["GAP"], completed_date=days_out(-7))
		section = self.packet("GAP")
		self.assertEqual(len(section["without_trainee_signature"]), 1)
		self.assertEqual(len(section["without_supervisor_review"]), 1)
		self.assertIn("112.161(b)", section["problem_note"])
		self.assertIn("asks a much harder question", section["problem_note"])

	def test_an_empty_section_says_why_rather_than_reading_as_nobody_trained(self):
		section = self.packet("GAP")
		self.assertEqual(section["rows"], [])
		self.assertIn("statement about the period and the tags", section["empty_note"])

	def test_the_tool_refuses_a_regime_the_app_does_not_know(self):
		message = self.tool_error(
			"generate_audit_packet",
			{
				"audit_type": "GAP",
				"company": MAIN,
				"period_start": days_out(-30),
				"period_end": frappe.utils.today(),
				"regime": "SQF",
				"dry_run": True,
			},
		)
		self.assertIn("SQF", message)
		self.assertIn("OR-OSHA", message)

	def test_the_regime_is_part_of_the_idempotence_key(self):
		"""A WPS-narrowed GAP packet and a full GAP packet are different documents.
		Filing the second over the first would silently replace a buyer's evidence
		bundle with a narrower one."""
		self.record(regimes=["GAP", "WPS"], completed_date=days_out(-7))
		common = {
			"audit_type": "GAP",
			"company": MAIN,
			"period_start": days_out(-30),
			"period_end": frappe.utils.today(),
			"dry_run": True,
		}
		full = self.tool_data("generate_audit_packet", common)
		narrow = self.tool_data("generate_audit_packet", {**common, "regime": "WPS"})
		self.assertNotEqual(full["archive_title"], narrow["archive_title"])
		self.assertIn("[WPS training]", narrow["archive_title"])
		self.assertIsNone(full["training_regime"])
		self.assertEqual(narrow["training_regime"], "WPS")


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheGuards(TrainingTestCase):
	def test_a_principal_with_no_hr_role_may_not_record_training(self):
		set_roles("Administrator", ["Accounts User"])
		message = self.record_error()
		self.assertIn("may not change the personnel register", message)
		self.assertIn("HR Manager", message)
		self.assertIn("Farm Manager", message)

	def test_a_farm_manager_may(self):
		set_roles("Administrator", ["Farm Manager"])
		self.assertTrue(self.record()["name"])

	def test_the_reads_are_gated_too_because_a_training_record_names_a_worker(self):
		self.record()
		set_roles("Administrator", ["Accounts User"])
		self.assertIn("may not change the personnel register", self.tool_error("list_trainings", {}))

	def test_a_cross_entity_employee_is_refused_naming_both_companies(self):
		"""A training record belongs to the entity that employed the person on the
		day. Filing it against another one puts the evidence in a packet that will
		be handed to an auditor asking about a different company."""
		stranger = self.an_employee_at(OTHER, "Otto Elsewhere", "HR-EMP-OTHER")
		message = self.record_error(employee=stranger, company=MAIN)
		self.assertIn(OTHER, message)
		self.assertIn(MAIN, message)
		self.assertIn("Nothing was created", message)

	def test_a_scoped_principal_cannot_record_against_an_entity_it_cannot_see(self):
		self.an_employee_at(OTHER, "Otto Elsewhere", "HR-EMP-OTHER")
		STORE.seed(
			"User Permission",
			[
				{
					"name": "UP-SCOPE-MAIN",
					"user": "Administrator",
					"allow": "Company",
					"for_value": MAIN,
					"apply_to_all_doctypes": 1,
					"is_default": 1,
				}
			],
		)
		message = self.record_error(employee="HR-EMP-OTHER")
		self.assertIn("has no access to company", message)
		self.assertIn(OTHER, message)

	def test_the_kill_switch_refuses_and_names_the_field_to_tick(self):
		self.configure(enabled=1, **{**ON, "allow_record_training": 0})
		message = self.record_error()
		self.assertIn("allow_record_training", message)
		self.assertIn("switched off", message)

	def test_the_kill_switch_on_the_review_is_its_own(self):
		"""Two acts, two switches. An operator who wants the register filled in
		and the signatures held back is expressing a real position."""
		record = self.record()
		self.configure(enabled=1, **{**ON, "allow_sign_training_supervisor_review": 0})
		message = self.tool_error(
			"sign_training_supervisor_review", {"name": record["name"], "supervisor": SUPERVISOR}
		)
		self.assertIn("allow_sign_training_supervisor_review", message)

	def test_every_write_leaves_an_audit_row(self):
		record = self.record()
		self.assertAudited("record_training", "Success")
		self.tool_data(
			"sign_training_supervisor_review", {"name": record["name"], "supervisor": SUPERVISOR}
		)
		self.assertAudited("sign_training_supervisor_review", "Success")

	def test_a_refused_write_is_audited_too(self):
		self.record_error(regimes=["nonsense"])
		self.assertAudited("record_training", "Error")


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheSupervisorReview(TrainingTestCase):
	"""§112.161(b): the requirement USDA GAP does not have and FDA cites most."""

	def test_a_new_record_has_no_review_and_the_result_says_what_is_missing(self):
		data = self.record()
		self.assertFalse(data["supervisor_reviewed"])
		self.assertIsNone(data["supervisor_reviewed_by"])
		self.assertIsNone(data["supervisor_reviewed_on"])
		self.assertIn(
			"§112.161(b)", " ".join(data["fsma_112_161_gaps"])
		)
		self.assertIn("sign_training_supervisor_review", data["next_step"])

	def test_signing_it_populates_all_three_fields(self):
		record = self.record()
		signed = self.tool_data(
			"sign_training_supervisor_review",
			{
				"name": record["name"],
				"supervisor": SUPERVISOR,
				"supervisor_signature": "/files/ada-signature.png",
			},
		)
		self.assertTrue(signed["supervisor_reviewed"])
		self.assertEqual(signed["supervisor_reviewed_by"], SUPERVISOR)
		self.assertTrue(signed["supervisor_reviewed_on"])
		self.assertTrue(signed["supervisor_signed"])
		self.assertEqual(signed["supervisor_name"], "Ada Orchard")

		row = self.raw(record["name"])
		self.assertEqual(row["supervisor_reviewed_by"], SUPERVISOR)
		self.assertEqual(row["supervisor_signature"], "/files/ada-signature.png")
		# The §112.161(b) gap is gone from the list, not merely from the summary.
		self.assertNotIn("§112.161(b)", " ".join(signed["fsma_112_161_gaps"]))

	def test_it_resolves_a_supervisor_by_name_as_well_as_by_docname(self):
		record = self.record()
		signed = self.tool_data(
			"sign_training_supervisor_review",
			{"name": record["name"], "supervisor": "Ada Orchard"},
		)
		self.assertEqual(signed["supervisor_reviewed_by"], SUPERVISOR)

	def test_a_self_review_is_refused(self):
		record = self.record()
		message = self.tool_error(
			"sign_training_supervisor_review", {"name": record["name"], "supervisor": TRAINEE}
		)
		self.assertIn("second pair of eyes", message)
		self.assertIn("Nothing was changed", message)

	def test_a_supervisor_from_another_entity_is_refused(self):
		record = self.record()
		self.an_employee_at(OTHER, "Otto Elsewhere", "HR-EMP-OTHER")
		message = self.tool_error(
			"sign_training_supervisor_review",
			{"name": record["name"], "supervisor": "HR-EMP-OTHER"},
		)
		self.assertIn("no responsibility for the entity", message)

	def test_a_review_dated_before_the_training_is_refused(self):
		record = self.record(completed_date=days_out(-2))
		message = self.tool_error(
			"sign_training_supervisor_review",
			{"name": record["name"], "supervisor": SUPERVISOR, "reviewed_on": days_out(-9)},
		)
		self.assertIn("did not exist yet was not reviewed", message)

	def test_replacing_an_existing_signature_needs_saying_so(self):
		record = self.record()
		self.tool_data(
			"sign_training_supervisor_review", {"name": record["name"], "supervisor": SUPERVISOR}
		)
		third = self.an_employee_at(MAIN, "Cara Third", "HR-EMP-THIRD")
		message = self.tool_error(
			"sign_training_supervisor_review", {"name": record["name"], "supervisor": third}
		)
		self.assertIn("already reviewed by", message)
		self.assertIn("replace_reviewer=true", message)

		signed = self.tool_data(
			"sign_training_supervisor_review",
			{"name": record["name"], "supervisor": third, "replace_reviewer": True},
		)
		self.assertEqual(signed["replaced_reviewer"], SUPERVISOR)

	def test_a_review_with_no_signature_file_says_the_review_is_incomplete(self):
		record = self.record()
		signed = self.tool_data(
			"sign_training_supervisor_review", {"name": record["name"], "supervisor": SUPERVISOR}
		)
		self.assertFalse(signed["supervisor_signed"])
		self.assertIn("stage_file_chunk", signed["signature_note"])

	def test_a_review_a_quarter_late_is_recorded_honestly_and_flagged(self):
		record = self.record(completed_date=days_out(-120))
		signed = self.tool_data(
			"sign_training_supervisor_review", {"name": record["name"], "supervisor": SUPERVISOR}
		)
		self.assertGreater(signed["days_between_training_and_review"], 30)
		self.assertIn("reasonable time", signed["timeliness_note"])


# ── 6 ───────────────────────────────────────────────────────────────────────
class ReadingTheRegister(TrainingTestCase):
	def setUp(self):
		super().setUp()
		self.wps = self.record(
			training_type="WPS Handler Training",
			regimes=["WPS"],
			completed_date=days_out(-370),
			expires_date=days_out(-5),
		)["name"]
		self.heat = self.record(
			training_type="Heat Illness Prevention",
			regimes=["OR-OSHA"],
			completed_date=days_out(-300),
			expires_date=days_out(60),
		)["name"]
		self.psa = self.record(
			training_type="PSA Grower Training", regimes=["FSMA", "GAP"], completed_date=days_out(-40)
		)["name"]

	def names(self, **arguments) -> list:
		return sorted(row["name"] for row in self.tool_data("list_trainings", arguments)["records"])

	def test_it_lists_everything_by_default_with_the_counts_that_matter(self):
		data = self.tool_data("list_trainings", {})
		self.assertEqual(data["count"], 3)
		self.assertEqual(data["expired"], [self.wps])
		self.assertEqual(data["expiring"], [self.heat])
		self.assertEqual(len(data["without_supervisor_review"]), 3)
		self.assertEqual(data["by_regime"], {"FSMA": 1, "GAP": 1, "OR-OSHA": 1, "WPS": 1})

	def test_the_regime_filter_is_how_an_audit_packet_is_assembled(self):
		self.assertEqual(self.names(regime="WPS"), [self.wps])
		self.assertEqual(self.names(regime="FSMA"), [self.psa])
		self.assertEqual(self.names(regime="GAP"), [self.psa])

	def test_the_regime_filter_matches_by_tag_and_not_by_substring(self):
		globalgap = self.record(regimes=["GlobalGAP"], completed_date=days_out(-3))["name"]
		self.assertNotIn(globalgap, self.names(regime="GAP"))
		self.assertEqual(self.names(regime="GlobalGAP"), [globalgap])

	def test_status_is_computed_as_of_today_rather_than_read_off_the_column(self):
		"""A record last saved in March holds March's answer. Filtering on the
		stored column would report the lapsed set as current."""
		frappe.db.set_value(training.DOCTYPE, self.wps, "status", "Active")
		self.assertEqual(self.names(status="Expired"), [self.wps])
		self.assertEqual(self.names(status="Active"), [self.psa])

	def test_expiring_within_days_covers_the_lapsed_as_well(self):
		self.assertEqual(sorted(self.names(expiring_within_days=90)), sorted([self.wps, self.heat]))
		self.assertEqual(self.names(expiring_within_days=0), [self.wps])

	def test_one_time_training_is_in_no_expiry_window(self):
		self.assertNotIn(self.psa, self.names(expiring_within_days=3650))

	def test_unreviewed_only_is_the_worklist_for_the_gap_fda_cites_most(self):
		self.tool_data(
			"sign_training_supervisor_review", {"name": self.psa, "supervisor": SUPERVISOR}
		)
		self.assertEqual(sorted(self.names(unreviewed_only=True)), sorted([self.wps, self.heat]))

	def test_it_filters_to_one_person(self):
		other = self.an_employee_at(MAIN, "Dana Fourth", "HR-EMP-FOURTH")
		theirs = self.record(employee=other, completed_date=days_out(-1))["name"]
		self.assertEqual(self.names(employee=other), [theirs])
		self.assertNotIn(theirs, self.names(employee=TRAINEE))

	def test_it_filters_by_completion_period(self):
		self.assertEqual(self.names(from_date=days_out(-50)), [self.psa])

	def test_an_unknown_regime_filter_is_refused_with_the_eight_listed(self):
		message = self.tool_error("list_trainings", {"regime": "SQF"})
		self.assertIn("SQF", message)
		self.assertIn("PrimusGFS", message)

	def test_get_training_returns_the_history_and_the_161_gaps(self):
		data = self.tool_data("get_training", {"name": self.wps})
		self.assertEqual(data["training_type"], "WPS Handler Training")
		self.assertEqual(data["status_now"], "Expired")
		self.assertEqual(len(data["employee_training_history"]), 3)
		self.assertIn("§112.161(b)", " ".join(data["fsma_112_161_gaps"]))
		self.assertIn("170.401", data["regime_notes"]["WPS"])

	def test_get_training_names_the_later_records_without_making_this_one_deletable(self):
		later = self.record(
			training_type="WPS Handler Training",
			regimes=["WPS"],
			completed_date=days_out(-1),
			expires_date=days_out(364),
		)["name"]
		data = self.tool_data("get_training", {"name": self.wps})
		self.assertIn(later, data["superseded_by"])
		self.assertIn("wants the row that was true last season", data["supersession_note"])

	def test_an_unknown_docname_is_refused_with_where_to_look(self):
		self.assertIn("list_trainings", self.tool_error("get_training", {"name": "ETR-nope"}))


# ── 7 ───────────────────────────────────────────────────────────────────────
class TheRetentionAnswer(TrainingTestCase):
	"""The longest tag governs, because destroying a record at two years would
	destroy the five-year evidence."""

	def years(self, **overrides) -> dict:
		record = self.record(**overrides)
		return self.tool_data("get_training", {"name": record["name"]})

	def test_fsma_and_wps_are_two_years(self):
		self.assertEqual(self.years(regimes=["FSMA", "WPS"])["retention_years"], 2)

	def test_or_osha_is_three(self):
		self.assertEqual(self.years(regimes=["OR-OSHA"])["retention_years"], 3)

	def test_nop_is_five(self):
		data = self.years(regimes=["NOP"])
		self.assertEqual(data["retention_years"], 5)
		self.assertIn("205.103(b)(4)", data["retention_note"])

	def test_a_record_tagged_gap_and_nop_is_a_five_year_record(self):
		data = self.years(regimes=["GAP", "NOP"])
		self.assertEqual(data["retention_years"], 5)
		self.assertIn("NOP", data["retention_note"])
		self.assertIn("longest", data["retention_note"])


# ── 8 ───────────────────────────────────────────────────────────────────────
class TheComplianceAnnex(TrainingTestCase):
	"""`generate_compliance_packet(regime=...)` staples training evidence to an
	accounting packet — which is the request that produces it, verbatim: 'send the
	reconciliation, and your WPS training records for the same year'."""

	def a_packet(self, **overrides) -> dict:
		payload = {
			"packet_type": "reconciliation_packet",
			"filters": {
				"account": "1100",
				"period_start": days_out(-30),
				"period_end": frappe.utils.today(),
				"company": MAIN,
			},
		}
		payload.update(overrides)
		return self.tool_data("generate_compliance_packet", payload)

	def test_without_a_regime_there_is_no_annex_at_all(self):
		self.assertNotIn("training_evidence", self.a_packet())

	def test_the_annex_carries_only_the_regime_asked_for(self):
		wps = self.record(regimes=["WPS"], completed_date=days_out(-7))["name"]
		self.record(regimes=["NOP"], completed_date=days_out(-7))
		annex = self.a_packet(regime="WPS")["training_evidence"]
		self.assertEqual([row["name"] for row in annex["records"]], [wps])
		self.assertEqual(annex["retention_years"], 2)
		self.assertIn("170.309", annex["retention_citation"])

	def test_the_annex_uses_the_packets_own_period_and_says_so(self):
		self.record(regimes=["WPS"], completed_date=days_out(-200))
		annex = self.a_packet(regime="WPS")["training_evidence"]
		self.assertEqual(annex["records"], [])
		self.assertIn("period_start/period_end", annex["period_basis"])
		self.assertIn("statement about the period", annex["empty_note"])

	def test_an_unknown_regime_is_refused(self):
		message = self.tool_error(
			"generate_compliance_packet",
			{
				"packet_type": "reconciliation_packet",
				"filters": {
					"account": "1100",
					"period_start": days_out(-30),
					"period_end": frappe.utils.today(),
					"company": MAIN,
				},
				"regime": "SQF",
			},
		)
		self.assertIn("SQF", message)
