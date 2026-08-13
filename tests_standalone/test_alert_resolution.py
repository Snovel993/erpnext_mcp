# SPDX-License-Identifier: MIT
"""A compliance row somebody can act on, and the one they may close. v0.57.0.

THE CALENDAR WAS A NOTICEBOARD. *"I-9 Section 1 was completed but carries no
employee signature — Critical"* is a sentence a foreman can read and not act on:
the pad that fixes it lives behind another tab, findable only by knowing which
Farm Task the sweep raised and that it was a signature task at all. This release
puts the ADDRESS on the alert — which doctype, which record, which column — and
opens the one door out of a row that is not "do the work": a dismissal, for the
alerts somebody has said in advance are stale.

FIVE CLAIMS, the last of them v0.64.1's.

1. `TheAlertCarriesItsSignatureBox` — a missing-signature alert names the form,
   the record and the column, plus the words the person at the pad is shown. The
   negatives are the load-bearing half: an overdue inspection must not grow a
   pad, and an address must never point at a box the write path would refuse.

2. `Av0550AlertIsUnchanged` — every key the previous release emitted still means
   what it meant, and an alert with no signature and no permission carries the
   two new keys as "no" rather than as absence-shaped surprises.

3. `TheDismissalIsGated` — `dismiss_compliance_alert` refuses an alert nobody
   marked dismissible, refuses a reason that is not one, and records who decided,
   when, and why on the alert itself. `dismiss_alert` is unchanged and ungated,
   because the operator at the desk is not the caller this gate is about.

4. `TheLoopCloses` — the address the alert published is one
   `collect_form_signature` accepts, the signature lands on the form, and the
   next sweep dismisses the alert with nobody having touched it. That last step
   is what makes this a loop rather than three features.

5. `TheRowGoesWhenTheInkLands` — v0.64.1, and it is claim 4 with the wait taken
   out. The signature re-runs its own box's rules where it lands, so the row
   goes at the moment the pad closes rather than at the top of the hour — on a
   site where nobody generated a Farm Task for the alert, which is the default
   and was the branch nothing swept.
"""

import base64

import frappe

from erpnext_mcp import compliance_rules
from erpnext_mcp.tools import signatures

from .fixtures import MAIN
from .harness import STORE
from .test_missing_signatures import TODAY, SignatureTestCase

A_CAPTURE = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"signature").decode()

ALERT = "Compliance Alert"


class ResolutionTestCase(SignatureTestCase):
	"""The signature fixtures, plus the two dismissal switches."""

	def setUp(self):
		super().setUp()
		self.configure(
			enabled=1,
			allow_refresh_compliance_alerts=1,
			allow_get_compliance_calendar=1,
			allow_generate_tasks_from_compliance_alerts=1,
			allow_collect_form_signature=1,
			allow_dismiss_compliance_alert=1,
			allow_dismiss_alert=1,
		)

	# ── reading the calendar the way a phone does ───────────────────────
	def calendar(self) -> dict:
		"""alert docname → the described row, flattened out of the categories."""
		data = self.tool_data("get_compliance_calendar", {"company": MAIN, "as_of": TODAY})
		return {
			row["name"]: row for group in (data.get("by_category") or {}).values() for row in group["alerts"]
		}

	def row_for(self, alert_type: str) -> dict:
		for row in self.calendar().values():
			if row["alert_type"] == alert_type:
				return row
		raise AssertionError(f"{alert_type} is not on the calendar")

	def mark_dismissible(self, name: str) -> None:
		frappe.db.set_value(ALERT, name, "can_dismiss", 1)


# ── 1. the alert names the box ──────────────────────────────────────────────
class TheAlertCarriesItsSignatureBox(ResolutionTestCase):
	def test_an_unsigned_section_1_alert_is_addressed_at_the_form_and_the_column(self):
		"""The three keys that make a row a tap. Without all three the app falls
		through to the task or the detail rather than opening a pad at nothing."""
		name = self.an_i9()
		self.sweep()

		request = self.row_for("i9_section_1_unsigned")["signature_request"]
		self.assertEqual(request["doctype"], "I-9 Form")
		self.assertEqual(request["docname"], name)
		self.assertEqual(request["signature_field"], "section_1_signature")

	def test_the_words_the_signer_is_shown_come_from_the_server(self):
		"""A handset must not paraphrase 8 CFR § 274a.2, and must not compose an
		attestation for a form it has never heard of. The person signing and the
		auditor reading the file should be looking at the same sentence."""
		self.an_i9()
		self.sweep()

		request = self.row_for("i9_section_1_unsigned")["signature_request"]
		self.assertEqual(request["signer_role"], "employee")
		self.assertEqual(request["form_label"], "Form I-9, Employment Eligibility Verification")
		self.assertEqual(request["section_label"], "Section 1. Employee Information and Attestation")
		self.assertIn("under penalty of perjury", request["attestation"])
		self.assertEqual(request["employee"], "HR-EMP-00002")
		self.assertEqual(request["employee_name"], "Ben Packhouse")

	def test_section_1_prints_the_employees_name_and_section_2_prints_the_verifiers(self):
		"""THE NAME UNDER THE RULED LINE IS NOT ALWAYS THE SUBJECT'S. On Section 2
		the printed name is the person who examined the documents, and printing
		the employee's there would be a claim about who attested — on the one
		document where that is the whole question."""
		name = self.an_i9()
		frappe.db.set_value(
			"I-9 Form",
			name,
			{
				"verification_date": "2026-07-03",
				"status": "Complete",
				"verifier_name": "Ada Orchard",
			},
		)
		self.sweep()

		self.assertEqual(
			self.row_for("i9_section_1_unsigned")["signature_request"]["printed_name"],
			"Ben Packhouse",
		)
		employer = self.row_for("i9_section_2_unsigned")["signature_request"]
		self.assertEqual(employer["signer_role"], "employer")
		self.assertEqual(employer["printed_name"], "Ada Orchard")
		self.assertEqual(employer["signature_field"], "section_2_signature")

	def test_a_section_2_nobody_has_verified_yet_prints_no_name_at_all(self):
		"""Absent rather than guessed. The app drops the line."""
		name = self.an_i9()
		frappe.db.set_value("I-9 Form", name, {"verification_date": "2026-07-03", "status": "Complete"})
		self.sweep()
		self.assertNotIn("printed_name", self.row_for("i9_section_2_unsigned")["signature_request"])

	def test_the_w4_alert_is_addressed_at_its_one_box(self):
		name = self.a_w4()
		self.sweep()

		request = self.row_for("w4_signature_missing")["signature_request"]
		self.assertEqual(request["doctype"], "W-4 Form")
		self.assertEqual(request["docname"], name)
		self.assertEqual(request["signature_field"], "signature")
		self.assertEqual(request["form_label"], "Form W-4, Employee's Withholding Certificate")

	def test_a_supplement_b_alert_addresses_the_parent_form_not_the_row(self):
		"""The pad posts against the I-9, and `collect_form_signature` picks the
		reverification row itself — the newest unsigned one, which is the row the
		alert's own message quotes. An address that named a child docname would
		be one nothing on this surface can resolve."""
		name = self.an_i9()
		self.a_reverification(name)
		self.sweep()

		request = self.row_for("i9_supplement_b_unsigned")["signature_request"]
		self.assertEqual(request["doctype"], "I-9 Form")
		self.assertEqual(request["docname"], name)
		self.assertEqual(request["signature_field"], "section_3_signature")
		self.assertEqual(request["section_label"], "Supplement B. Reverification and Rehire")

	def test_the_tax_return_names_which_return_it_is_and_a_role_the_app_has_no_caption_for(self):
		"""`officer` IS NOT ONE OF THE FOUR the app has words for, and that is the
		design working rather than a gap: §14.1a keeps an unrecognised role
		verbatim and prints `signer_label` over the line. A 941 is signed by an
		officer of the employer, not by the "authorized representative" the I-9's
		caption names."""
		STORE.seed(
			"Tax Form",
			[
				{
					"name": "TAX-941-2026Q2",
					"company": MAIN,
					"form_type": "941",
					"status": "Filed",
					"period_start": "2026-04-01",
					"period_end": "2026-06-30",
				}
			],
		)
		self.sweep()

		request = self.row_for("tax_form_signature_missing")["signature_request"]
		self.assertEqual(request["doctype"], "Tax Form")
		self.assertEqual(request["docname"], "TAX-941-2026Q2")
		self.assertEqual(request["signature_field"], "signature")
		# Read off the record, because one column covers four returns and the
		# person signing is entitled to be told which of them this is.
		self.assertEqual(request["form_label"], "Form 941")
		self.assertEqual(request["signer_role"], "officer")
		self.assertEqual(request["signer_label"], "Signature of officer or authorized agent")
		# The employer's own return names no employee, and nothing is invented.
		self.assertNotIn("employee", request)
		self.assertNotIn("printed_name", request)

	def test_an_alert_that_is_not_about_a_signature_carries_no_pad(self):
		"""THE NEGATIVE THAT MATTERS. An overdue housing inspection is work in a
		cabin, and a signature pad over it would collect ink instead of an
		inspection."""
		self.an_i9()
		self.sweep()
		for row in self.calendar().values():
			if row["alert_type"] in signatures.BOXES_BY_ALERT_TYPE:
				continue
			with self.subTest(alert_type=row["alert_type"]):
				self.assertIsNone(row["signature_request"])

	def test_every_address_an_alert_publishes_is_one_the_write_path_accepts(self):
		"""The property the whole design rests on. A request composed anywhere
		else could name a column `collect_form_signature` refuses, which is a pad
		that collects a signature into nothing and asks for it again wherever the
		form really lives."""
		name = self.an_i9()
		frappe.db.set_value(
			"I-9 Form", name, {"verification_date": "2026-07-03", "verifier_name": "Ada Orchard"}
		)
		self.a_w4()
		self.sweep()

		published = [row["signature_request"] for row in self.calendar().values() if row["signature_request"]]
		self.assertTrue(published, "the sweep raised no signature alerts for this to check")
		for request in published:
			with self.subTest(box=f"{request['doctype']}.{request['signature_field']}"):
				self.assertIn(f"{request['doctype']}.{request['signature_field']}", signatures.BOXES_BY_KEY)

	def test_a_farm_task_raised_from_the_alert_carries_the_same_address(self):
		"""The pad opened from the Available tab and the pad opened from the
		calendar are addressed identically, because both read the same alert."""
		from erpnext_mcp.api import shape

		self.an_i9()
		self.sweep()
		task = self.task_for("i9_section_1_unsigned")

		on_the_task = shape.task(dict(task))["signature_request"]
		self.assertEqual(on_the_task["signature_field"], "section_1_signature")
		self.assertEqual(on_the_task, self.row_for("i9_section_1_unsigned")["signature_request"])


# ── 2. nothing that was there has moved ─────────────────────────────────────
class Av0550AlertIsUnchanged(ResolutionTestCase):
	#: Every key the calendar emitted before this release. An alert sent exactly
	#: as v0.55.0 sent it still lists, still sorts and still opens its detail.
	V0550_KEYS = (
		"name",
		"alert_type",
		"title",
		"severity",
		"category",
		"regimes",
		"company",
		"source_doctype",
		"source_docname",
		"message",
		"due_date",
		"days_until_due",
		"overdue",
		"first_seen",
		"open_for_days",
		"last_refreshed",
		"snoozed_until",
		"snoozed",
		"dismissed",
		"auto_dismissed",
		"dismissed_by",
		"dismissed_on",
		"dismissed_reason",
		"framework",
	)

	def test_every_key_the_previous_release_emitted_is_still_emitted(self):
		self.an_i9()
		self.sweep()
		row = self.row_for("i9_section_1_unsigned")
		for key in self.V0550_KEYS:
			with self.subTest(key=key):
				self.assertIn(key, row)

	def test_an_ordinary_alert_is_closable_by_nobody_and_asks_for_no_signature(self):
		"""Both new keys default to "no" rather than to absence. A permission that
		failed open would be the one bug this feature cannot have."""
		self.an_i9()
		self.sweep()
		row = self.row_for("i9_section_1_unsigned")
		self.assertIs(row["can_dismiss"], False)

		for other in self.calendar().values():
			with self.subTest(alert_type=other["alert_type"]):
				self.assertIs(other["can_dismiss"], False)


# ── 3. the gate ─────────────────────────────────────────────────────────────
class TheDismissalIsGated(ResolutionTestCase):
	def an_alert(self) -> str:
		self.an_i9()
		self.sweep()
		return self.row_for("i9_section_1_unsigned")["name"]

	def test_an_alert_nobody_marked_dismissible_is_refused_and_says_how_to_offer_it(self):
		name = self.an_alert()
		message = self.tool_error(
			"dismiss_compliance_alert",
			{"alert": name, "reason": "Not worth doing this week."},
		)
		self.assertIn("not marked as dismissible", message)
		self.assertIn("May Be Dismissed From The Field", message)
		self.assertEqual(int(frappe.db.get_value(ALERT, name, "dismissed") or 0), 0)

	def test_a_marked_alert_is_dismissed_and_the_judgement_is_recorded(self):
		name = self.an_alert()
		self.mark_dismissible(name)

		self.tool_data(
			"dismiss_compliance_alert",
			{"alert": name, "reason": "This I-9 belongs to a hire who never started."},
		)
		stored = frappe.db.get_value(
			ALERT,
			name,
			["dismissed", "auto_dismissed", "dismissed_by", "dismissed_on", "dismissed_reason"],
			as_dict=True,
		)
		self.assertEqual(int(stored["dismissed"] or 0), 1)
		# NOT an auto-dismissal. The two are told apart six months later by
		# exactly this pair of columns.
		self.assertEqual(int(stored["auto_dismissed"] or 0), 0)
		self.assertEqual(stored["dismissed_by"], frappe.session.user)
		self.assertTrue(stored["dismissed_on"])
		self.assertEqual(stored["dismissed_reason"], "This I-9 belongs to a hire who never started.")
		self.assertNotIn(name, self.calendar())

	def test_a_word_is_not_a_reason(self):
		name = self.an_alert()
		self.mark_dismissible(name)
		message = self.tool_error("dismiss_compliance_alert", {"alert": name, "reason": "ok"})
		self.assertIn("real explanation", message)
		self.assertEqual(int(frappe.db.get_value(ALERT, name, "dismissed") or 0), 0)

	def test_an_alert_already_dismissed_is_refused_rather_than_re_dismissed(self):
		name = self.an_alert()
		self.mark_dismissible(name)
		self.tool_data(
			"dismiss_compliance_alert", {"alert": name, "reason": "A duplicate of a filing made in June."}
		)
		message = self.tool_error(
			"dismiss_compliance_alert", {"alert": name, "reason": "A duplicate of a filing made in June."}
		)
		self.assertIn("already dismissed", message)

	def test_the_ungated_desk_route_is_unchanged(self):
		"""`dismiss_alert` does not consult `can_dismiss` and must not start to.
		The operator with the source record open in the next tab is not the
		caller this gate is about, and gating them would be a breaking change
		wearing a feature's clothes."""
		name = self.an_alert()
		self.assertEqual(int(frappe.db.get_value(ALERT, name, "can_dismiss") or 0), 0)
		self.tool_data("dismiss_alert", {"alert": name, "reason": "Handled outside this system in June."})
		self.assertEqual(int(frappe.db.get_value(ALERT, name, "dismissed") or 0), 1)

	def test_the_sweep_neither_grants_the_permission_nor_takes_it_away(self):
		"""It is a judgement a person made about this alert, and the sweep does
		not get to overrule them by looking at the record again — the same
		reasoning that leaves a snooze alone."""
		name = self.an_alert()
		self.mark_dismissible(name)
		self.sweep()
		self.assertEqual(int(frappe.db.get_value(ALERT, name, "can_dismiss") or 0), 1)

		frappe.db.set_value(ALERT, name, "can_dismiss", 0)
		self.sweep()
		self.assertEqual(int(frappe.db.get_value(ALERT, name, "can_dismiss") or 0), 0)


# ── 4. the loop ─────────────────────────────────────────────────────────────
class TheLoopCloses(ResolutionTestCase):
	def test_the_address_the_alert_published_collects_the_signature(self):
		"""End to end, off the alert's own words: the calendar names the box, the
		capture is filed against exactly that box, and the next sweep takes the
		row away with nobody having dismissed anything."""
		self.a_roster()
		name = self.an_i9()
		self.sweep()
		request = self.row_for("i9_section_1_unsigned")["signature_request"]

		self.tool_data(
			"collect_form_signature",
			{
				"doctype": request["doctype"],
				"name": request["docname"],
				"field": request["signature_field"],
				"signature_base64": A_CAPTURE,
			},
		)
		self.assertTrue(frappe.db.get_value("I-9 Form", name, "section_1_signature"))

		self.sweep()
		self.assertNotIn("i9_section_1_unsigned", self.raised())

	def test_the_alert_a_signature_answers_is_named_by_key(self):
		"""What `submit_form_signature` reports as `dismissed_alert`. NOT a claim
		that anything was dismissed — the sweep does that — but the row the phone
		should take off the tab it was tapped from."""
		self.a_roster()
		name = self.an_i9()
		self.sweep()
		box = signatures.BOXES_BY_ALERT_TYPE["i9_section_1_unsigned"]

		self.assertEqual(
			signatures.alert_answered_by(box, name),
			self.row_for("i9_section_1_unsigned")["name"],
		)

	def test_an_alert_already_dismissed_is_not_named_as_answered(self):
		self.a_roster()
		name = self.an_i9()
		self.sweep()
		alert = self.row_for("i9_section_1_unsigned")["name"]
		self.mark_dismissible(alert)
		self.tool_data("dismiss_compliance_alert", {"alert": alert, "reason": "Hire never started."})

		box = signatures.BOXES_BY_ALERT_TYPE["i9_section_1_unsigned"]
		self.assertEqual(signatures.alert_answered_by(box, name), "")


# ── 5 ───────────────────────────────────────────────────────────────────────
class TheRowGoesWhenTheInkLands(ResolutionTestCase):
	"""v0.64.1. The compliance tab clears at the moment the pad closes.

	v0.64.0 gave `complete_farm_task` a narrowed sweep so an alert stops standing
	until the top of the hour in front of the worker who just answered it. A
	SIGNATURE reached that only sideways — `_close_the_task` completes the Farm
	Task the sweep raised, and the completion sweeps — so it worked exactly where
	somebody had run `generate_tasks_from_compliance_alerts`, a manual tool that
	is off by default. Everywhere else the pad closed, the row came straight back
	on the next load, and the calendar read as decoration.
	"""

	def test_a_signature_with_no_task_behind_it_still_clears_the_row(self):
		"""The case the task path could not reach. Nobody generated a task, so
		there is no completion to sweep on this worker's behalf — and the alert
		has to go anyway, because the box it is about now has ink in it."""
		self.a_roster()
		name = self.an_i9()
		self.sweep()
		alert = self.row_for("i9_section_1_unsigned")["name"]
		self.assertFalse(
			frappe.db.get_all("Farm Task", filters={"source_alert": alert}, pluck="name"),
			"this test is about the branch with no task; generating one would test the other one",
		)

		result = self.tool_data(
			"collect_form_signature",
			{
				"doctype": "I-9 Form",
				"name": name,
				"field": "section_1_signature",
				"signature_base64": A_CAPTURE,
			},
		)

		evaluation = result["compliance_evaluation"]
		self.assertEqual(evaluation["rules_asked"], ["i9_section_1_unsigned"])
		self.assertEqual(evaluation["auto_dismissed"], [alert])
		# NOBODY DISMISSED IT. The rule looked again and found the column filled,
		# which is the only honest way an alert goes away.
		row = STORE.get_raw(ALERT, alert)
		self.assertTrue(int(row["auto_dismissed"]))
		self.assertFalse(row.get("dismissed_by"))
		self.assertFalse(row.get("dismissed_reason"))

	def test_the_answered_alert_is_still_named_after_the_sweep_took_it(self):
		"""The collision the fix creates, and why the read moved.

		`alert_answered_by` finds the OPEN alert a signature makes untrue, and the
		sweep two steps later makes it stop being open. Read afterwards it answers
		nothing — so the phone would be told no row was answered on exactly the
		calls where the work landed. The tool captures it before it sweeps, and
		`submit_form_signature` reports what the tool captured.
		"""
		self.a_roster()
		name = self.an_i9()
		self.sweep()
		alert = self.row_for("i9_section_1_unsigned")["name"]

		result = self.tool_data(
			"collect_form_signature",
			{
				"doctype": "I-9 Form",
				"name": name,
				"field": "section_1_signature",
				"signature_base64": A_CAPTURE,
			},
		)
		self.assertEqual(result["answered_alert"], alert)
		# And the fresh lookup now answers nothing, which is what makes the
		# captured value load-bearing rather than a convenience.
		box = signatures.BOXES_BY_ALERT_TYPE["i9_section_1_unsigned"]
		self.assertEqual(signatures.alert_answered_by(box, name), "")

	def test_signing_one_form_leaves_every_other_worker_alone(self):
		"""The sweep is narrowed by RULE and decided per RECORD, so one worker's
		signature cannot empty the row belonging to somebody who has not signed.
		A narrowed sweep that cleared what it did not look at would empty a
		calendar and read as progress — which is the failure the `alert_types`
		allowlist was given the same promise as the `regime` filter to prevent."""
		self.a_roster()
		mine = self.an_i9()
		theirs = self.an_i9(
			name="I9-2026-0002",
			employee="HR-EMP-00003",
			employee_name="Ana Ramirez",
			legal_first_name="Ana",
			legal_last_name="Ramirez",
		)
		self.sweep()
		unsigned = signatures.alert_answered_by(
			signatures.BOXES_BY_ALERT_TYPE["i9_section_1_unsigned"], theirs
		)
		self.assertTrue(unsigned, "the second worker's I-9 should have raised its own alert")

		self.tool_data(
			"collect_form_signature",
			{
				"doctype": "I-9 Form",
				"name": mine,
				"field": "section_1_signature",
				"signature_base64": A_CAPTURE,
			},
		)
		self.assertFalse(int(STORE.get_raw(ALERT, unsigned)["dismissed"]))


# ── the seeder is what puts these four rules on a site ──────────────────────
class TheRulesAreRecordsAndTheBoxesAreCode(ResolutionTestCase):
	def test_every_signature_rule_this_app_seeds_has_a_box_behind_it(self):
		"""Both directions of one mapping. A rule with no box raises alerts nobody
		can act on from a phone; a box naming a rule that does not exist is a pad
		nothing will ever open."""
		compliance_rules.seed_compliance_rules()
		seeded = {row["rule_id"] for row in STORE.rows("Compliance Rule")}
		for alert_type, box in signatures.BOXES_BY_ALERT_TYPE.items():
			with self.subTest(alert_type=alert_type):
				self.assertIn(alert_type, seeded)
				self.assertIn(box.key, signatures.BOXES_BY_KEY)

	def test_every_box_says_what_the_signer_is_swearing_to(self):
		"""A box with no attestation is a pad that omits the block entirely — the
		app invents nothing — so an operation would collect a signature under a
		declaration nobody was shown."""
		for box in signatures.SIGNATURE_BOXES:
			with self.subTest(box=box.key):
				self.assertTrue(box.attestation)
				self.assertTrue(box.form_label)
				self.assertTrue(box.section_label)
				self.assertTrue(box.signer_role)
