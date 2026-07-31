# SPDX-License-Identifier: MIT
"""The four external-evidence doctypes — Sprint 7 Wave 2.

WHAT THESE FOUR HAVE IN COMMON, AND WHY THERE ARE ONLY FOUR. Sprint 7's stance
is that compliance is a lens on operational data: every spray IS a Worker
Protection Standard record, so the applicator's name goes on the spray. These
four are the remainder — evidence that arrives from OUTSIDE the operation and
would exist even if the farm did nothing that day. Nobody writes a harvest
hygiene SOP by harvesting; the certifier's certificate is theirs; the agency's
docket number is theirs; an auditor's findings are an outside party's
conclusions.

THE REFUSALS ARE THE SUBJECT. Almost every test here is about something the
tools decline to do, because the value of an evidence record is entirely in
whether it can be relied on:

  * a version chain written from one end only — refused, because "which
    procedure was in force" is asked from whichever end the auditor starts;
  * a certificate whose expiration is edited forward — refused, because that
    hides a lapse and a lapse is exactly what an auditor asks about;
  * a filing marked Submitted with no date — refused, because it asserts
    something an agency's "we have no record" beats;
  * an audit closed over an open finding — refused, because it would be
    assembled into a packet and contradicted by the first question asked.

NOTHING HERE DELETES. Superseding writes a link; renewing appends to a history;
closing writes a date. Every test that exercises one of those counts the rows
before and after.

DATES ARE READ, STATUSES ARE NOT DERIVED. `Certification` does not flip itself
to Expired when a date passes, and `TheStatusIsNotDerived` says why that is the
right call rather than an omission: a controller only runs when somebody saves,
so the expired certificates would be exactly the ones still reading Active.
"""

from .fixtures import MAIN, OTHER, V12TestCase
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"list_compliance_policies",
		"get_compliance_policy",
		"create_compliance_policy",
		"update_compliance_policy",
		"supersede_compliance_policy",
		"list_certifications",
		"get_certification",
		"create_certification",
		"update_certification",
		"renew_certification",
		"list_regulatory_filings",
		"get_regulatory_filing",
		"create_regulatory_filing",
		"update_regulatory_filing",
		"list_audit_events",
		"get_audit_event",
		"create_audit_event",
		"update_audit_event",
		"close_audit_event",
	)
}

#: The fake site's "today". Every date in these fixtures is placed relative to it
#: rather than to a real clock, so the suite does not start failing in August.
TODAY = "2026-07-24"


class EvidenceTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)

	def a_policy(self, name="Harvest Hygiene SOP", **overrides):
		payload = {
			"policy_name": name,
			"category": "Harvest Hygiene",
			"version": "v3",
			"company": MAIN,
			"effective_date": "2026-01-01",
			"review_due_date": "2027-01-01",
		}
		payload.update(overrides)
		return self.tool_data("create_compliance_policy", payload)

	def a_certificate(self, name="GlobalGAP 2026", **overrides):
		payload = {
			"cert_name": name,
			"cert_type": "GlobalGAP",
			"company": MAIN,
			"holder": MAIN,
			"issuing_body": "Primus Auditing Ops",
			"issued_date": "2025-09-01",
			"expiration_date": "2027-09-01",
			"renewal_window_days": 90,
		}
		payload.update(overrides)
		return self.tool_data("create_certification", payload)

	def a_filing(self, name="1099-NEC 2025", **overrides):
		payload = {
			"filing_name": name,
			"agency": "IRS",
			"filing_type": "1099-NEC",
			"company": MAIN,
			"submission_date": "2026-01-28",
			"docket_number": "CONF-114-2026",
		}
		payload.update(overrides)
		return self.tool_data("create_regulatory_filing", payload)

	def an_audit(self, name="PrimusGFS 2026", actions=None, **overrides):
		payload = {
			"audit_name": name,
			"audit_type": "PrimusGFS",
			"audit_date": "2026-06-01",
			"auditor": "J. Reyes",
			"company": MAIN,
			"result": "Passed With Conditions",
			"corrective_actions": actions
			if actions is not None
			else [
				{
					"finding": "Hand wash station in Block 4 had no soap at 0800",
					"severity": "Major",
					"due_date": "2026-06-29",
					"assigned_to": "Camp Manager",
				}
			],
		}
		payload.update(overrides)
		return self.tool_data("create_audit_event", payload)


# ── Compliance Policy ───────────────────────────────────────────────────────
class Policies(EvidenceTestCase):
	def test_creating_one_records_the_category_and_the_dates(self):
		data = self.a_policy()
		self.assertEqual(data["name"], "Harvest Hygiene SOP")
		self.assertEqual(data["category"], "Harvest Hygiene")
		self.assertTrue(data["in_force"])
		self.assertFalse(data["review_overdue"])

	def test_a_policy_with_no_document_says_so_out_loud(self):
		"""A policy record with no attached procedure is a CLAIM that a procedure
		exists, and an auditor asks to read the procedure."""
		data = self.a_policy()
		self.assertTrue(any("not the same as a procedure existing" in note for note in data["warnings"]))
		listing = self.tool_data("list_compliance_policies", {"company": MAIN})
		self.assertIn("Harvest Hygiene SOP", listing["without_a_document"])

	def test_the_version_is_a_field_so_a_duplicate_name_is_refused(self):
		"""A policy at v3 is the same policy it was at v1 — every audit finding
		that cited it should still resolve to it."""
		self.a_policy()
		message = self.tool_error(
			"create_compliance_policy",
			{"policy_name": "Harvest Hygiene SOP", "category": "Harvest Hygiene", "company": MAIN},
		)
		self.assertIn("version is a FIELD", message.replace("The ", ""))
		self.assertIn("Nothing was created", message)

	def test_a_review_date_before_the_effective_date_is_refused(self):
		message = self.tool_error(
			"create_compliance_policy",
			{
				"policy_name": "Backwards SOP",
				"category": "Spray SOP",
				"company": MAIN,
				"effective_date": "2026-06-01",
				"review_due_date": "2026-01-01",
			},
		)
		self.assertIn("before the Effective Date", message)

	def test_an_overdue_review_is_read_from_the_date_not_from_a_flag(self):
		self.a_policy(review_due_date="2026-01-01")
		listing = self.tool_data("list_compliance_policies", {"company": MAIN})
		self.assertEqual(listing["review_overdue"], ["Harvest Hygiene SOP"])

	def test_a_superseded_policy_is_not_reported_as_overdue(self):
		"""Only a procedure actually in force can be overdue for review. A
		historical version being flagged would put a permanent row on the calendar
		that nothing can ever clear."""
		self.a_policy(review_due_date="2026-01-01")
		self.a_policy("Harvest Hygiene SOP 2027", version="v4", effective_date="2026-07-01")
		self.tool_data(
			"supersede_compliance_policy",
			{
				"policy": "Harvest Hygiene SOP",
				"superseded_by": "Harvest Hygiene SOP 2027",
				"reason": "revised after the 2026 PrimusGFS finding on hand-wash stations",
			},
		)
		self.assertEqual(self.tool_data("list_compliance_policies", {"company": MAIN})["review_overdue"], [])

	def test_a_policy_owner_who_does_not_exist_is_refused_not_dropped(self):
		"""A field that quietly emptied itself would look exactly like a field
		nobody filled in, and nobody would be answerable for the procedure."""
		message = self.tool_error(
			"create_compliance_policy",
			{
				"policy_name": "Orphan SOP",
				"category": "Spray SOP",
				"company": MAIN,
				"policy_owner": "nobody@example.invalid",
			},
		)
		self.assertIn("no User", message)
		self.assertIn("Nothing was changed", message)

	def test_it_cannot_be_re_keyed(self):
		self.a_policy()
		message = self.tool_error(
			"update_compliance_policy",
			{"policy": "Harvest Hygiene SOP", "policy_name": "Something Else"},
		)
		self.assertIn("cannot be changed", message)

	def test_neither_end_of_the_chain_can_be_set_by_update(self):
		self.a_policy()
		for field in ("supersedes", "superseded_by"):
			with self.subTest(field=field):
				message = self.tool_error(
					"update_compliance_policy",
					{"policy": "Harvest Hygiene SOP", field: "Harvest Hygiene SOP"},
				)
				self.assertIn("one end only", message)


class SupersedingAPolicy(EvidenceTestCase):
	def setUp(self):
		super().setUp()
		self.a_policy(effective_date="2026-01-01")
		self.a_policy("Harvest Hygiene SOP 2027", version="v4", effective_date="2026-07-01")

	def supersede(self, **overrides):
		payload = {
			"policy": "Harvest Hygiene SOP",
			"superseded_by": "Harvest Hygiene SOP 2027",
			"reason": "revised after the 2026 PrimusGFS finding on hand-wash stations",
		}
		payload.update(overrides)
		return payload

	def test_both_ends_are_written_in_one_act(self):
		"""A chain written from one side only tells a reader coming from the other
		side something different — and "what was in force on the day" is asked from
		whichever end the auditor happens to start."""
		self.tool_data("supersede_compliance_policy", self.supersede())
		old = STORE.get_raw("Compliance Policy", "Harvest Hygiene SOP")
		new = STORE.get_raw("Compliance Policy", "Harvest Hygiene SOP 2027")
		self.assertEqual(old["superseded_by"], "Harvest Hygiene SOP 2027")
		self.assertEqual(new["supersedes"], "Harvest Hygiene SOP")
		self.assertEqual(old["status"], "Superseded")

	def test_the_chain_reads_the_same_from_either_end(self):
		self.tool_data("supersede_compliance_policy", self.supersede())
		from_old = self.tool_data("get_compliance_policy", {"policy": "Harvest Hygiene SOP"})
		from_new = self.tool_data("get_compliance_policy", {"policy": "Harvest Hygiene SOP 2027"})
		self.assertEqual(
			[entry["name"] for entry in from_old["version_chain"]],
			[entry["name"] for entry in from_new["version_chain"]],
		)
		self.assertEqual(from_old["chain_length"], 2)

	def test_nothing_is_deleted(self):
		before = len(STORE.rows("Compliance Policy"))
		self.tool_data("supersede_compliance_policy", self.supersede())
		self.assertEqual(len(STORE.rows("Compliance Policy")), before)

	def test_a_policy_cannot_supersede_itself(self):
		message = self.tool_error(
			"supersede_compliance_policy", self.supersede(superseded_by="Harvest Hygiene SOP")
		)
		self.assertIn("cannot supersede itself", message)

	def test_a_second_successor_is_refused(self):
		"""Two successors would make "what was in force" unanswerable."""
		self.tool_data("supersede_compliance_policy", self.supersede())
		self.a_policy("Harvest Hygiene SOP 2028", version="v5", effective_date="2026-07-02")
		message = self.tool_error(
			"supersede_compliance_policy", self.supersede(superseded_by="Harvest Hygiene SOP 2028")
		)
		self.assertIn("already superseded", message)

	def test_a_successor_that_predates_its_predecessor_is_refused(self):
		"""That would leave a period with two procedures in force and no way to
		tell which the crew was working to."""
		self.a_policy("Earlier SOP", version="v1", effective_date="2025-01-01")
		message = self.tool_error(
			"supersede_compliance_policy", self.supersede(superseded_by="Earlier SOP")
		)
		self.assertIn("before", message)
		self.assertIn("Nothing was changed", message)

	def test_a_dry_run_writes_neither_end(self):
		self.tool_data("supersede_compliance_policy", self.supersede(dry_run=True))
		self.assertIsNone(STORE.get_raw("Compliance Policy", "Harvest Hygiene SOP").get("superseded_by"))

	def test_the_reason_is_mandatory_and_has_to_be_a_sentence(self):
		message = self.tool_error("supersede_compliance_policy", self.supersede(reason="fix"))
		self.assertIn("real explanation", message)


# ── Certification ───────────────────────────────────────────────────────────
class Certifications(EvidenceTestCase):
	def test_it_records_the_expiration_and_computes_the_window(self):
		data = self.a_certificate(expiration_date="2026-08-15", renewal_window_days=90)
		self.assertTrue(data["inside_renewal_window"])
		self.assertFalse(data["expired"])
		self.assertEqual(data["days_until_expiry"], 22)

	def test_an_expiration_before_the_issue_date_is_refused(self):
		message = self.tool_error(
			"create_certification",
			{
				"cert_name": "Backwards Cert",
				"cert_type": "Organic",
				"company": MAIN,
				"issued_date": "2026-06-01",
				"expiration_date": "2026-01-01",
			},
		)
		self.assertIn("expired before it was issued", message)

	def test_the_holder_is_resolved_against_whichever_register_has_them(self):
		"""Free text with resolution on read, exactly as Family.related_to does it
		— the holder may be in any of four registers or in none."""
		data = self.a_certificate(holder=MAIN)
		got = self.tool_data("get_certification", {"certification": data["name"]})
		self.assertEqual(got["holder_doctype"], "Company")

	def test_a_holder_in_no_register_is_not_an_error(self):
		"""An applicator licence held by a contractor on nobody's payroll is
		exactly what the fallback is for."""
		data = self.a_certificate("Applicator — R. Mendez", cert_type="Applicator License", holder="R. Mendez")
		got = self.tool_data("get_certification", {"certification": data["name"]})
		self.assertIsNone(got["holder_doctype"])
		self.assertEqual(got["holder"], "R. Mendez")

	def test_a_renewal_is_not_a_second_record(self):
		self.a_certificate()
		message = self.tool_error(
			"create_certification",
			{"cert_name": "GlobalGAP 2026", "cert_type": "GlobalGAP", "company": MAIN},
		)
		self.assertIn("renew_certification", message)

	def test_the_register_sorts_soonest_expiry_first(self):
		"""The order somebody works them in."""
		self.a_certificate("Later", expiration_date="2028-01-01")
		self.a_certificate("Sooner", expiration_date="2026-09-01")
		names = [row["name"] for row in self.tool_data("list_certifications", {"company": MAIN})["certifications"]]
		self.assertEqual(names[0], "Sooner")


class TheStatusIsNotDerived(EvidenceTestCase):
	"""A certificate's status is what somebody DECIDED; the dates are what is TRUE.

	The obvious alternative — a controller that flips `status` to Expired when a
	date passes — is worse in the one direction that matters. It would only run on
	documents somebody happened to save, so the expired certificates would be
	exactly the ones still reading Active, and a list filtered on status would show
	the lapsed ones as current. A derived field that is only correct when touched
	is worse than no derived field.
	"""

	def test_an_expired_certificate_still_says_active_and_the_tools_do_not_care(self):
		self.a_certificate(expiration_date="2026-01-01")
		row = STORE.get_raw("Certification", "GlobalGAP 2026")
		self.assertEqual(row["status"], "Active")
		listing = self.tool_data("list_certifications", {"company": MAIN})
		self.assertEqual(listing["expired"], ["GlobalGAP 2026"])

	def test_the_detail_view_names_the_contradiction_rather_than_hiding_it(self):
		self.a_certificate(expiration_date="2026-01-01")
		notes = self.tool_data("get_certification", {"certification": "GlobalGAP 2026"})["compliance_notes"]
		self.assertTrue(any("still reads Active" in note for note in notes))

	def test_the_listing_says_which_column_it_read(self):
		self.a_certificate(expiration_date="2026-01-01")
		note = self.tool_data("list_certifications", {"company": MAIN})["note"]
		self.assertIn("read from the DATE", note)


class RenewingACertificate(EvidenceTestCase):
	def setUp(self):
		super().setUp()
		self.a_certificate(expiration_date="2026-08-15")

	def test_it_appends_to_the_history_and_moves_the_date(self):
		data = self.tool_data(
			"renew_certification",
			{
				"certification": "GlobalGAP 2026",
				"new_expiration": "2027-08-15",
				"what_was_done": "passed the 2026 re-audit on 2026-07-10, fee paid",
			},
		)
		self.assertEqual(data["new_expiration"], "2027-08-15")
		got = self.tool_data("get_certification", {"certification": "GlobalGAP 2026"})
		self.assertEqual(got["renewal_count"], 1)
		self.assertEqual(got["renewals"][0]["previous_expiration"], "2026-08-15")

	def test_editing_the_expiration_forward_is_refused_and_names_the_right_tool(self):
		"""Editing it in place would produce a certificate that looks as though it
		never expired — exactly the fact somebody would want hidden."""
		message = self.tool_error(
			"update_certification",
			{"certification": "GlobalGAP 2026", "expiration_date": "2027-08-15"},
		)
		self.assertIn("RENEWAL", message)
		self.assertIn("renew_certification", message)
		self.assertIn("never expired", message)

	def test_moving_the_expiration_backwards_is_allowed_by_update(self):
		"""A correction is a correction. It is only the forward direction that
		hides something."""
		self.tool_data(
			"update_certification", {"certification": "GlobalGAP 2026", "expiration_date": "2026-08-01"}
		)
		self.assertEqual(
			STORE.get_raw("Certification", "GlobalGAP 2026")["expiration_date"], "2026-08-01"
		)

	def test_a_lapse_is_reported_and_not_hidden(self):
		"""Renewing late does not close a gap that already happened."""
		self.tool_data(
			"update_certification", {"certification": "GlobalGAP 2026", "expiration_date": "2026-06-01"}
		)
		data = self.tool_data(
			"renew_certification",
			{
				"certification": "GlobalGAP 2026",
				"new_expiration": "2027-06-01",
				"renewed_on": "2026-07-01",
				"what_was_done": "re-audit was delayed by the auditor's schedule",
			},
		)
		self.assertEqual(data["lapsed_days"], 30)
		self.assertTrue(any("not a defence during that gap" in warning for warning in data["warnings"]))
		got = self.tool_data("get_certification", {"certification": "GlobalGAP 2026"})
		self.assertEqual(got["lapses"][0]["days"], 30)

	def test_a_renewal_that_does_not_move_the_date_out_is_refused(self):
		message = self.tool_error(
			"renew_certification",
			{
				"certification": "GlobalGAP 2026",
				"new_expiration": "2026-08-01",
				"what_was_done": "trying to correct a typo in the expiry date",
			},
		)
		self.assertIn("not after the current one", message)
		self.assertIn("update_certification", message)

	def test_a_renewal_dated_in_the_future_is_refused(self):
		message = self.tool_error(
			"renew_certification",
			{
				"certification": "GlobalGAP 2026",
				"new_expiration": "2028-08-15",
				"renewed_on": "2027-01-01",
				"what_was_done": "the audit is booked for next January",
			},
		)
		self.assertIn("in the future", message)

	def test_a_dry_run_appends_nothing(self):
		self.tool_data(
			"renew_certification",
			{
				"certification": "GlobalGAP 2026",
				"new_expiration": "2027-08-15",
				"what_was_done": "passed the 2026 re-audit on 2026-07-10",
				"dry_run": True,
			},
		)
		self.assertEqual(
			self.tool_data("get_certification", {"certification": "GlobalGAP 2026"})["renewal_count"], 0
		)

	def test_it_brings_a_lapsed_status_back_to_active(self):
		self.tool_data("update_certification", {"certification": "GlobalGAP 2026", "status": "Expired"})
		self.tool_data(
			"renew_certification",
			{
				"certification": "GlobalGAP 2026",
				"new_expiration": "2027-08-15",
				"what_was_done": "renewed after the lapse was noticed on 2026-07-20",
			},
		)
		self.assertEqual(STORE.get_raw("Certification", "GlobalGAP 2026")["status"], "Active")


# ── Regulatory Filing ───────────────────────────────────────────────────────
class Filings(EvidenceTestCase):
	def test_it_records_what_proves_the_filing_was_made(self):
		data = self.a_filing()
		self.assertEqual(data["docket_number"], "CONF-114-2026")
		self.assertEqual(data["submission_date"], "2026-01-28")

	def test_submitted_with_no_date_is_refused(self):
		"""A half-filled filing record is more dangerous than none: an audit packet
		would include it and an auditor would read it as evidence."""
		message = self.tool_error(
			"create_regulatory_filing",
			{
				"filing_name": "Undated",
				"agency": "IRS",
				"filing_type": "1099-NEC",
				"company": MAIN,
				"status": "Submitted",
			},
		)
		self.assertIn("when it was submitted", message)
		self.assertIn("no record of that", message)

	def test_a_draft_with_no_dates_is_allowed(self):
		"""Exactly what a filing being prepared looks like. Refusing it would mean
		the record could not be created until the moment nobody has time."""
		data = self.a_filing("Being Prepared", status="Draft", submission_date=None)
		self.assertEqual(data["status"], "Draft")
		self.assertIsNone(data["submission_date"])

	def test_a_submission_date_in_the_future_is_refused(self):
		message = self.tool_error(
			"create_regulatory_filing",
			{
				"filing_name": "Next Year",
				"agency": "IRS",
				"filing_type": "1099-NEC",
				"company": MAIN,
				"submission_date": "2027-01-28",
			},
		)
		self.assertIn("in the future", message)

	def test_a_response_dated_before_the_filing_is_refused(self):
		message = self.tool_error(
			"create_regulatory_filing",
			{
				"filing_name": "Time Traveller",
				"agency": "ODA",
				"filing_type": "Pesticide-Application-Report",
				"company": MAIN,
				"submission_date": "2026-06-01",
				"response_received_date": "2026-01-01",
			},
		)
		self.assertIn("answered before it was sent", message)

	def test_a_missing_docket_number_is_a_note_rather_than_a_refusal(self):
		"""It is the cheapest possible proof the filing arrived, and plenty of
		agencies do not issue one."""
		data = self.a_filing("No Docket", docket_number=None)
		self.assertTrue(any("docket or confirmation" in warning for warning in data["warnings"]))

	def test_awaiting_a_response_is_computed_from_both_dates(self):
		self.a_filing(response_due_date="2026-09-01")
		listing = self.tool_data("list_regulatory_filings", {"company": MAIN})
		self.assertEqual(listing["awaiting_response"], ["1099-NEC 2025"])

	def test_recording_the_response_clears_the_wait_and_says_the_alert_will_clear(self):
		self.a_filing(response_due_date="2026-09-01")
		data = self.tool_data(
			"update_regulatory_filing",
			{"filing": "1099-NEC 2025", "response_received_date": "2026-08-20", "response": "Accepted"},
		)
		self.assertFalse(data["awaiting_response"])
		self.assertIn("auto-dismisses", data["next_step"])


# ── Audit Event ─────────────────────────────────────────────────────────────
class Audits(EvidenceTestCase):
	def test_it_records_the_findings_and_the_actions(self):
		data = self.an_audit()
		self.assertEqual(data["action_count"], 1)
		self.assertEqual(data["open_action_count"], 1)
		self.assertEqual(data["worst_open_severity"], "Major")

	def test_an_action_with_no_due_date_is_warned_about(self):
		"""Nothing will ever say it is late."""
		data = self.an_audit(actions=[{"finding": "Signage faded", "severity": "Minor"}])
		self.assertTrue(any("no due date" in warning for warning in data["warnings"]))

	def test_a_conditional_pass_with_no_actions_is_warned_about(self):
		"""A conditional pass HAS conditions. If they are not written down here,
		they are not written down anywhere."""
		data = self.an_audit(actions=[])
		self.assertTrue(any("has conditions" in warning for warning in data["warnings"]))

	def test_an_overdue_action_is_counted_from_its_own_deadline(self):
		"""The deadline the SCHEME set, not an interval this app chose."""
		data = self.tool_data("get_audit_event", {"audit": self.an_audit()["name"]})
		self.assertTrue(data["corrective_actions"][0]["overdue"])
		self.assertEqual(data["corrective_actions"][0]["days_overdue"], 25)

	def test_an_action_not_yet_due_is_not_overdue(self):
		self.an_audit(actions=[{"finding": "Repaint the bin numbers", "severity": "Minor", "due_date": "2026-12-01"}])
		data = self.tool_data("get_audit_event", {"audit": "PrimusGFS 2026"})
		self.assertFalse(data["corrective_actions"][0]["overdue"])

	def test_closing_an_action_requires_saying_what_was_done(self):
		"""A tick in a box is what an auditor is specifically trained to
		disbelieve."""
		self.an_audit()
		message = self.tool_error(
			"update_audit_event", {"audit": "PrimusGFS 2026", "close_corrective_action": 1}
		)
		self.assertIn("what", message)
		self.assertIn("disbelieve", message)

	def test_closing_an_action_out_of_range_is_refused(self):
		self.an_audit()
		message = self.tool_error(
			"update_audit_event",
			{
				"audit": "PrimusGFS 2026",
				"close_corrective_action": 9,
				"corrective_action": "did the thing",
			},
		)
		self.assertIn("outside this audit", message)

	def test_replacing_the_table_is_reported_as_a_replacement(self):
		"""A merge would silently reorder rows addressed by index and close the
		wrong finding."""
		self.an_audit()
		data = self.tool_data(
			"update_audit_event",
			{
				"audit": "PrimusGFS 2026",
				"corrective_actions": [{"finding": "One finding only", "severity": "Minor"}],
			},
		)
		self.assertEqual(data["action_count"], 1)
		self.assertIn("REPLACED", data["note"])

	def test_adding_an_action_leaves_the_existing_ones_alone(self):
		self.an_audit()
		data = self.tool_data(
			"update_audit_event",
			{
				"audit": "PrimusGFS 2026",
				"add_corrective_actions": [{"finding": "Second finding", "severity": "Minor"}],
			},
		)
		self.assertEqual(data["action_count"], 2)

	def test_an_unknown_field_on_an_action_is_refused_by_name(self):
		message = self.tool_error(
			"create_audit_event",
			{
				"audit_name": "Typo Audit",
				"audit_type": "GAP",
				"audit_date": "2026-06-01",
				"company": MAIN,
				"corrective_actions": [{"finding": "x", "owner": "somebody"}],
			},
		)
		self.assertIn("owner", message)
		self.assertIn("Supported", message)

	def test_an_action_with_no_finding_is_refused(self):
		message = self.tool_error(
			"create_audit_event",
			{
				"audit_name": "Empty Finding",
				"audit_type": "GAP",
				"audit_date": "2026-06-01",
				"company": MAIN,
				"corrective_actions": [{"severity": "Major"}],
			},
		)
		self.assertIn("no finding", message)


class ClosingAnAudit(EvidenceTestCase):
	def setUp(self):
		super().setUp()
		self.an_audit()

	def test_it_refuses_while_an_action_is_open_and_names_every_one(self):
		"""THE ONE THAT MATTERS. A closure date over an open finding would be
		assembled into an audit packet as a finished audit and contradicted by the
		auditor's first question."""
		message = self.tool_error(
			"close_audit_event",
			{"audit": "PrimusGFS 2026", "closure_note": "the auditor confirmed by email"},
		)
		self.assertIn("still open", message)
		self.assertIn("Hand wash station", message)
		self.assertIn("Nothing was changed", message)

	def test_the_controller_refuses_it_too_so_there_is_no_second_door(self):
		"""Enforced in `validate` rather than only in the tool, because a field
		written some other way would otherwise get past it."""
		import frappe

		doc = frappe.get_doc("Audit Event", "PrimusGFS 2026")
		doc.corrective_actions_closed = "2026-07-01"
		with self.assertRaises(Exception) as caught:
			doc.save()
		self.assertIn("still open", str(caught.exception))

	def test_it_closes_once_every_action_is_closed(self):
		self.tool_data(
			"update_audit_event",
			{
				"audit": "PrimusGFS 2026",
				"close_corrective_action": 1,
				"corrective_action": "restocked soap and added a daily check to the pre-harvest walk",
				"closed_date": "2026-06-10",
			},
		)
		data = self.tool_data(
			"close_audit_event",
			{"audit": "PrimusGFS 2026", "closure_note": "auditor confirmed by email on 2026-06-14"},
		)
		self.assertTrue(data["closed"])
		self.assertEqual(data["open_action_count"], 0)

	def test_an_audit_with_no_findings_can_be_closed(self):
		"""A clean PrimusGFS is a real event."""
		self.an_audit("Clean Audit 2026", actions=[], result="Passed")
		data = self.tool_data(
			"close_audit_event",
			{"audit": "Clean Audit 2026", "closure_note": "passed with no findings; certificate issued"},
		)
		self.assertTrue(data["closed"])
		self.assertIn("raised no corrective actions", data["note"])

	def test_re_closing_is_refused(self):
		self.an_audit("Clean Audit 2026", actions=[], result="Passed")
		payload = {"audit": "Clean Audit 2026", "closure_note": "passed with no findings at all"}
		self.tool_data("close_audit_event", payload)
		self.assertIn("already closed", self.tool_error("close_audit_event", payload))

	def test_a_closure_dated_before_the_audit_is_refused(self):
		self.an_audit("Clean Audit 2026", actions=[], result="Passed")
		message = self.tool_error(
			"close_audit_event",
			{
				"audit": "Clean Audit 2026",
				"closed_date": "2025-01-01",
				"closure_note": "this cannot possibly be right",
			},
		)
		self.assertIn("before the audit", message)

	def test_an_action_closed_before_the_audit_is_refused(self):
		"""Fixing something before it was found is two dates transposed. If the fix
		genuinely predated the audit, the finding was wrong."""
		message = self.tool_error(
			"update_audit_event",
			{
				"audit": "PrimusGFS 2026",
				"close_corrective_action": 1,
				"corrective_action": "was already done before they arrived",
				"closed_date": "2026-01-01",
			},
		)
		self.assertIn("before the audit", message)

	def test_a_dry_run_writes_no_closure_date(self):
		self.an_audit("Clean Audit 2026", actions=[], result="Passed")
		self.tool_data(
			"close_audit_event",
			{"audit": "Clean Audit 2026", "closure_note": "passed with no findings at all", "dry_run": True},
		)
		self.assertIsNone(STORE.get_raw("Audit Event", "Clean Audit 2026").get("corrective_actions_closed"))

	def test_update_cannot_set_the_closure_date(self):
		message = self.tool_error(
			"update_audit_event",
			{"audit": "PrimusGFS 2026", "corrective_actions_closed": "2026-07-01"},
		)
		self.assertIn("close_audit_event", message)


class CompanyScoping(EvidenceTestCase):
	"""Two companies, and evidence that belongs to one of them."""

	def test_a_record_on_another_company_is_refused_by_name(self):
		self.a_policy(company=OTHER)
		message = self.tool_error(
			"get_compliance_policy", {"policy": "Harvest Hygiene SOP", "company": MAIN}
		)
		self.assertIn(OTHER, message)

	def test_a_listing_is_scoped_to_the_company_asked_for(self):
		self.a_certificate("Main Cert", company=MAIN)
		self.a_certificate("Other Cert", company=OTHER)
		names = [
			row["name"]
			for row in self.tool_data("list_certifications", {"company": MAIN})["certifications"]
		]
		self.assertEqual(names, ["Main Cert"])


class NothingIsDeleted(EvidenceTestCase):
	"""Every one of these four is the ONLY copy of what it records."""

	def test_no_evidence_tool_removes_a_row(self):
		self.a_policy()
		self.a_policy("Harvest Hygiene SOP 2027", version="v4", effective_date="2026-07-01")
		self.a_certificate(expiration_date="2026-08-15")
		self.a_filing()
		self.an_audit()
		before = {
			doctype: len(STORE.rows(doctype))
			for doctype in ("Compliance Policy", "Certification", "Regulatory Filing", "Audit Event")
		}
		self.tool_data(
			"supersede_compliance_policy",
			{
				"policy": "Harvest Hygiene SOP",
				"superseded_by": "Harvest Hygiene SOP 2027",
				"reason": "revised after the 2026 PrimusGFS finding on hand-wash stations",
			},
		)
		self.tool_data(
			"renew_certification",
			{
				"certification": "GlobalGAP 2026",
				"new_expiration": "2027-08-15",
				"what_was_done": "passed the 2026 re-audit on 2026-07-10",
			},
		)
		self.tool_data(
			"update_audit_event",
			{
				"audit": "PrimusGFS 2026",
				"close_corrective_action": 1,
				"corrective_action": "restocked soap and added a daily check",
				"closed_date": "2026-06-10",
			},
		)
		self.tool_data(
			"close_audit_event",
			{"audit": "PrimusGFS 2026", "closure_note": "auditor confirmed by email on 2026-06-14"},
		)
		after = {doctype: len(STORE.rows(doctype)) for doctype in before}
		self.assertEqual(before, after)


class Defaults(EvidenceTestCase):
	def test_the_reads_are_on_and_the_writes_are_off_out_of_the_box(self):
		self.configure(enabled=1)
		for name in ("list_compliance_policies", "list_certifications", "list_audit_events"):
			with self.subTest(tool=name):
				self.tool_data(name, {"company": MAIN})
		for name in ("create_compliance_policy", "renew_certification", "close_audit_event"):
			with self.subTest(tool=name):
				self.assertIn(f"allow_{name}", self.tool_error(name, {}))
