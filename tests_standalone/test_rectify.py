# SPDX-License-Identifier: MIT
"""What a tap on one compliance alert does — v0.68.0, Sprint 3.

`api/rectify.py` is a CLOSED MAP from alert type to the route that fixes it,
and a closed map has exactly two failure modes. It can point somewhere that
does not exist, and it can fail to cover something that does. Both are silent
in production — the first is a 404 on a worker's phone at the moment they
finally tapped the button, the second is the "there is nothing this app can do
for you" screen the whole sprint existed to delete — so both are asserted here.

FOUR CLAIMS.

1. **EVERY ENDPOINT NAMED IS A ROUTE THAT IS ACTUALLY MOUNTED.**
   `TheEndpointsAreReal`. The module writes its paths as string constants on
   purpose (a composed path would let a typo open a different door), which
   means nothing but a test joins those strings back to `ROUTES`. This is that
   join.

2. **EVERY SEEDED RULE GETS AN ANSWER.** `EveryAlertTypeIsAnswered` walks the
   rules the app actually seeds — not a list restated here — so a rule shipped
   in a later release without a rectification fails this file rather than
   reaching a handset as a dead end.

3. **THE REFUSALS ARE DELIBERATE, AND SAY WHICH KIND THEY ARE.**
   `TheRefusalsAreHonest`. Three alert types are answered "no, and here is why"
   rather than left to fall through to the generic sentence, because "a lawyer
   signs off on destroying an I-9" is a different fact from "nobody has written
   this yet" and only the second invites somebody to go looking.

4. **THE ROWS THE APP ACTUALLY BUILDS DECODE.** `TheBuildersReadRealRows`
   drives representative rows through the builders that do a lookup, including
   the two that share `reverify_i9` and resolve their employee from different
   doctypes.
"""

from typing import ClassVar

import frappe

from erpnext_mcp import compliance_rules
from erpnext_mcp.api import rectify
from erpnext_mcp.farmops_api import PREFIX, ROUTES

from .fixtures import MAIN, SeededTestCase


class RectifyTestCase(SeededTestCase):
	"""Shared vocabulary: the mounted paths, and the seeded rule ids."""

	def mounted(self) -> set:
		return {f"{PREFIX}{route.path}" for route in ROUTES}

	def seeded_rule_ids(self) -> set:
		"""Every rule id this app seeds, from the seeders themselves.

		BOTH HALVES. `seed_specs` derives the rules that exist as Python `Rule`
		objects; `declarative_seed_specs` returns the ones authored as records
		with no scanner behind them. A rectification map that covered only the
		first half would miss `w4_tax_year_outdated`, which is the alert this
		sprint was named after.
		"""
		specs = list(compliance_rules.seed_specs()) + list(compliance_rules.declarative_seed_specs())
		return {str(spec["rule_id"]) for spec in specs}


class TheEndpointsAreReal(RectifyTestCase):
	"""Claim 1. Every path the map can hand a phone is a path the app answers."""

	def test_every_action_endpoint_is_a_mounted_route(self):
		mounted = self.mounted()
		for alert_type in sorted(rectify._BUILDERS):
			built = rectify.describe_rectification({"alert_type": alert_type}) or {}
			endpoint = built.get("action_endpoint")
			if not endpoint:
				continue
			with self.subTest(alert_type=alert_type):
				self.assertIn(endpoint, mounted)

	def test_the_endpoint_constants_are_all_mounted(self):
		"""The constants directly, so one that no builder currently reaches —
		because its builder returned None for the bare row above — is still
		proved to point at a real route."""
		mounted = self.mounted()
		constants = {
			name: value
			for name, value in vars(rectify).items()
			if name.isupper() and isinstance(value, str) and value.startswith(PREFIX)
		}
		self.assertTrue(constants, "no endpoint constants found — did they get renamed?")
		for name, endpoint in sorted(constants.items()):
			with self.subTest(constant=name):
				self.assertIn(endpoint, mounted)


class EveryAlertTypeIsAnswered(RectifyTestCase):
	"""Claim 2. No seeded rule reaches a phone as a dead end."""

	def test_every_seeded_rule_has_a_rectification_object(self):
		for rule_id in sorted(self.seeded_rule_ids()):
			with self.subTest(rule_id=rule_id):
				built = rectify.describe_rectification({"alert_type": rule_id})
				self.assertIsNotNone(built)
				self.assertIn("can_rectify_mobile", built)
				self.assertIn("action_params", built)

	def test_every_seeded_rule_is_named_in_the_map(self):
		"""STRONGER THAN THE ABOVE, and the one that catches a new release.

		An alert type with no builder still gets an object — the generic task
		fallback or the honest refusal — so the previous test passes for a rule
		nobody has thought about. This one fails, and the fix is either a
		builder or an explicit entry saying the refusal is deliberate.
		"""
		self.assertEqual(self.seeded_rule_ids() - set(rectify._BUILDERS), set())

	def test_the_map_names_nothing_that_is_not_a_rule(self):
		"""The other direction. A builder keyed on a misspelled alert type is
		dead code that silently never fires."""
		self.assertEqual(set(rectify._BUILDERS) - self.seeded_rule_ids(), set())

	def test_an_unknown_alert_type_is_refused_rather_than_raising(self):
		built = rectify.describe_rectification({"alert_type": "not_a_rule_at_all"})
		self.assertFalse(built["can_rectify_mobile"])
		self.assertTrue(built["explanation"])

	def test_no_row_at_all_is_none(self):
		self.assertIsNone(rectify.describe_rectification({}))
		self.assertIsNone(rectify.describe_rectification(None))


class TheRefusalsAreHonest(RectifyTestCase):
	"""Claim 3. The deliberate "no" is distinguishable from the generic one."""

	#: Answered "no" on purpose, each with its own reason.
	DELIBERATE: ClassVar[set[str]] = {
		"i9_retention_destruction_eligible",
		"financial_kpi_threshold_breach",
		"budget_variance_breach",
	}

	def test_the_deliberate_refusals_do_not_use_the_generic_sentence(self):
		for alert_type in sorted(self.DELIBERATE):
			with self.subTest(alert_type=alert_type):
				built = rectify.describe_rectification({"alert_type": alert_type})
				self.assertFalse(built["can_rectify_mobile"])
				self.assertNotEqual(built["explanation"], rectify._NO_MOBILE_FIX)
				self.assertTrue(built["explanation"].strip())

	def test_a_refusal_still_carries_every_key_a_fix_does(self):
		"""So the app reads one shape and branches on the flag, rather than
		branching on which keys happen to be present."""
		refusal = rectify.describe_rectification({"alert_type": "budget_variance_breach"})
		fix = rectify.describe_rectification({"alert_type": "certification_expiring"})
		self.assertEqual(set(refusal) - {"explanation"}, set(fix) - {"explanation"})

	def test_destroying_an_i9_is_never_offered_from_a_phone(self):
		built = rectify.describe_rectification(
			{"alert_type": "i9_retention_destruction_eligible", "source_docname": "HR-EMP-00001"}
		)
		self.assertIsNone(built["action_endpoint"])
		self.assertFalse(built["can_rectify_mobile"])


class TheBuildersReadRealRows(RectifyTestCase):
	"""Claim 4. The lookups resolve against records this app actually writes."""

	def test_missing_w4_reads_the_employee_straight_off_the_row(self):
		built = rectify.describe_rectification(
			{"alert_type": "employee_missing_w4", "source_docname": "HR-EMP-00042", "company": MAIN}
		)
		self.assertEqual(built["action_type"], "submit_w4")
		self.assertEqual(built["action_params"]["employee"], "HR-EMP-00042")
		self.assertEqual(built["action_params"]["company"], MAIN)

	def test_the_w4_prefill_names_the_current_tax_year(self):
		built = rectify.describe_rectification(
			{"alert_type": "w4_tax_year_outdated", "source_docname": "W4-0001", "company": MAIN}
		)
		expected = str(frappe.utils.getdate(frappe.utils.today()).year)
		self.assertEqual(built["action_params"]["tax_year"], expected)

	def test_i9_expired_treats_the_row_as_an_employee(self):
		"""`i9_expired` reads the status column on the Employee, so
		`source_docname` IS the employee — no lookup."""
		built = rectify.describe_rectification(
			{"alert_type": "i9_expired", "source_docname": "HR-EMP-00042"}
		)
		self.assertEqual(built["action_type"], "reverify_i9")
		self.assertEqual(built["action_params"]["employee"], "HR-EMP-00042")

	def test_work_authorization_expiring_resolves_through_the_i9_form(self):
		"""Same endpoint as `i9_expired`, different doctype behind the row — the
		reason the two are separate builders rather than one shared one."""
		built = rectify.describe_rectification(
			{"alert_type": "work_authorization_expiring", "source_docname": "I9-0001"}
		)
		self.assertEqual(built["action_type"], "reverify_i9")
		# No I-9 Form by that name on the fake site: the prefill is omitted
		# rather than the row failing to describe.
		self.assertTrue(built["can_rectify_mobile"])
		self.assertNotIn("employee", built["action_params"])

	def test_an_unclaimed_field_report_is_claimed_not_re_raised(self):
		"""The task already exists — that is what the alert is complaining
		about. Raising a second one would answer an unclaimed task with an
		unclaimed task."""
		built = rectify.describe_rectification(
			{"alert_type": "field_flag_awaiting_dispatch", "source_docname": "TASK-0007"}
		)
		self.assertEqual(built["action_type"], "claim_task")
		self.assertEqual(built["action_params"]["task"], "TASK-0007")
		self.assertNotEqual(built["action_endpoint"], rectify._RECTIFY_ALERT)

	def test_a_signature_alert_addresses_the_pad_at_the_form(self):
		built = rectify.describe_rectification(
			{
				"alert_type": "i9_supplement_b_unsigned",
				"signature_request": {
					"doctype": "I-9 Form",
					"docname": "I9-0001",
					"signature_field": "supplement_b_signature",
				},
			}
		)
		self.assertEqual(built["action_type"], "collect_form_signature")
		self.assertEqual(built["action_params"]["docname"], "I9-0001")
		self.assertEqual(built["action_params"]["field"], "supplement_b_signature")

	def test_a_signature_alert_with_no_request_falls_back_rather_than_breaking(self):
		"""A row whose `signature_request` never got built is a reason to offer
		something else, not a reason to fail the alert list it is one row of."""
		built = rectify.describe_rectification({"alert_type": "i9_supplement_b_unsigned"})
		self.assertIsNotNone(built)
		self.assertNotEqual(built.get("action_type"), "collect_form_signature")

	def test_the_task_shaped_fixes_all_point_at_the_one_route(self):
		for alert_type in (
			"housing_inspection_overdue",
			"housing_detector_test_stale",
			"water_test_stale",
			"water_test_contamination",
			"shift_heat_threshold_crossed",
			"flc_license_expiring",
			"audit_action_overdue",
		):
			with self.subTest(alert_type=alert_type):
				built = rectify.describe_rectification({"alert_type": alert_type})
				self.assertEqual(built["action_endpoint"], rectify._RECTIFY_ALERT)
				self.assertTrue(built["can_rectify_mobile"])
				self.assertTrue(built["action_label"])

	def test_every_offered_fix_carries_a_label_a_worker_can_read(self):
		"""An action with no label is a button with no words on it."""
		for rule_id in sorted(self.seeded_rule_ids()):
			built = rectify.describe_rectification({"alert_type": rule_id}) or {}
			if not built.get("can_rectify_mobile"):
				continue
			with self.subTest(rule_id=rule_id):
				self.assertTrue(str(built.get("action_label") or "").strip())
				self.assertTrue(str(built.get("action_type") or "").strip())
