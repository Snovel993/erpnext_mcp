# SPDX-License-Identifier: MIT
"""Wizards as data, in the worker's own language.

FOUR CLAIMS.

1. **A WIZARD IS A ROW.** `TheDefinitionIsData`. The problem this solves is the
   App Store: a flow compiled into Swift needs a release to change a question,
   and the questions change when the law does. Adding a wizard is adding a
   record.

2. **AN EDITED WIZARD IS NEVER OVERWRITTEN.** `TheSeederRespectsEdits`. A farm
   that reworded a question or added the step their state started requiring in
   July must not have it reset by the next `bench migrate` — otherwise "config
   not code" is a slogan rather than a property.

3. **THE LANGUAGE IS THE WORKER'S, AND A GAP IS REPORTED.** `TheLanguageIsResolved`.
   A missing translation falls back to English and is LISTED: silently serving
   English means nobody finds out until a worker is in front of a screen they
   cannot read, and refusing to serve the wizard locks the crew out over a
   missing string.

4. **THE FLOW HAS TO BE WALKABLE.** `TheFlowIsChecked`. A `next_step` pointing at
   nothing, two steps with one key, two fields with one name — all three strand
   a worker on a screen in an orchard, and all three are cheap to catch here.
"""

from erpnext_mcp.tools import wizards

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in ("get_wizard_definition", "list_wizard_definitions", "install_compliance_fields")
}

WORKER = "HR-EMP-00001"
ENGLISH_SPEAKER = "HR-EMP-00002"


class WizardTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		# `preferred_language` is a Custom Field, so the resolver only sees it
		# once the installer has run. A test that seeded the VALUE without the
		# COLUMN would prove the language lookup works on a site this app never
		# produces.
		from erpnext_mcp import compliance_fields

		compliance_fields.install_compliance_fields()
		wizards.install_wizard_definitions()
		STORE.seed(
			"Employee",
			[
				{
					"name": WORKER,
					"employee_name": "Ana Ramos",
					"company": MAIN,
					"status": "Active",
					"preferred_language": "es",
				},
				{
					"name": ENGLISH_SPEAKER,
					"employee_name": "Sam Doyle",
					"company": MAIN,
					"status": "Active",
					"preferred_language": "en",
				},
			],
		)


# ── 1. the definition is data ───────────────────────────────────────────────
class TheDefinitionIsData(WizardTestCase):
	def test_the_five_shipped_wizards_are_records(self):
		names = set(STORE.tables.get("Wizard Definition", {}))
		self.assertEqual(
			names,
			{
				"accident_investigation",
				"progressive_discipline",
				"asset_registration",
				"employee_onboarding",
				"inspection_session",
			},
		)

	def test_a_wizard_comes_back_as_ordered_steps(self):
		data = self.tool_data("get_wizard_definition", {"wizard": "accident_investigation", "language": "en"})
		self.assertEqual(
			[step["step_key"] for step in data["steps"]],
			["what", "where", "who", "witnesses", "immediate"],
		)

	def test_each_step_carries_its_fields_with_types(self):
		data = self.tool_data("get_wizard_definition", {"wizard": "accident_investigation", "language": "en"})
		first = data["steps"][0]
		types = {field["type"] for field in first["fields"]}
		self.assertIn("datetime", types)
		self.assertIn("long_text", types)

	def test_the_field_types_are_the_handsets_not_frappes(self):
		"""`photo`, `signature`, `qr_scan` and `audio_note` are things a phone
		does and a Desk form does not."""
		data = self.tool_data("get_wizard_definition", {"wizard": "accident_investigation", "language": "en"})
		types = {field["type"] for step in data["steps"] for field in step["fields"]}
		self.assertTrue({"photo", "qr_scan", "audio_note"} <= types)

	def test_a_select_carries_its_choices(self):
		data = self.tool_data("get_wizard_definition", {"wizard": "accident_investigation", "language": "en"})
		severity = next(
			field for step in data["steps"] for field in step["fields"] if field["fieldname"] == "severity"
		)
		values = {option["value"] for option in severity["options"]}
		self.assertIn("Near Miss", values)
		self.assertIn("Fatality", values)

	def test_conditional_logic_is_data(self):
		"""'Skip the injury step on a near miss' is a property of the step."""
		data = self.tool_data("get_wizard_definition", {"wizard": "accident_investigation", "language": "en"})
		who = next(step for step in data["steps"] if step["step_key"] == "who")
		self.assertEqual(who["visible_if"], {"field": "severity", "not_equals": "Near Miss"})

	def test_it_names_where_the_answers_are_posted(self):
		"""A wizard the app knows where to submit is a wizard the app has code
		for, which is what this doctype exists to avoid."""
		data = self.tool_data("get_wizard_definition", {"wizard": "accident_investigation", "language": "en"})
		self.assertEqual(data["submit_method"], "create_accident_report")

	def test_the_validation_is_declared_as_a_courtesy_and_says_so(self):
		data = self.tool_data("get_wizard_definition", {"wizard": "asset_registration", "language": "en"})
		self.assertIn("not a control", data["server_validates_anyway"])

	def test_an_unknown_wizard_is_refused_with_the_list(self):
		error = self.tool_error("get_wizard_definition", {"wizard": "nope"})
		self.assertIn("accident_investigation", error)

	def test_a_disabled_wizard_is_withheld_unless_asked_for(self):
		STORE.get_raw("Wizard Definition", "inspection_session")["enabled"] = 0
		self.assertIn(
			"is disabled", self.tool_error("get_wizard_definition", {"wizard": "inspection_session"})
		)
		data = self.tool_data(
			"get_wizard_definition", {"wizard": "inspection_session", "include_disabled": True}
		)
		self.assertEqual(data["wizard_key"], "inspection_session")

	def test_the_list_does_not_pull_every_field(self):
		data = self.tool_data("list_wizard_definitions", {"language": "en"})
		self.assertEqual(data["wizard_count"], 5)
		self.assertNotIn("steps", data["wizards"][0])

	def test_the_list_can_be_narrowed_to_a_category(self):
		data = self.tool_data("list_wizard_definitions", {"category": "Safety", "language": "en"})
		self.assertEqual([w["wizard_key"] for w in data["wizards"]], ["accident_investigation"])


# ── 2. an edited wizard is never overwritten ────────────────────────────────
class TheSeederRespectsEdits(WizardTestCase):
	def test_re_seeding_creates_nothing(self):
		report = wizards.install_wizard_definitions()
		self.assertEqual(report["created"], [])
		self.assertEqual(len(report["existing"]), 5)

	def test_an_operators_edit_survives_a_migrate(self):
		"""The whole promise of the doctype. A flow somebody tuned for their own
		crew being reset by an upgrade is what would make 'config not code' a
		lie."""
		doc = STORE.get_raw("Wizard Definition", "accident_investigation")
		doc["title_es"] = "Reportar un Incidente"
		wizards.install_wizard_definitions()
		self.assertEqual(
			STORE.get_raw("Wizard Definition", "accident_investigation")["title_es"],
			"Reportar un Incidente",
		)

	def test_a_deliberate_reset_is_available(self):
		STORE.get_raw("Wizard Definition", "accident_investigation")["title_es"] = "Changed"
		report = wizards.install_wizard_definitions(overwrite=True)
		self.assertIn("accident_investigation", report["overwritten"])
		self.assertEqual(
			STORE.get_raw("Wizard Definition", "accident_investigation")["title_es"],
			"Reportar un Accidente",
		)

	def test_the_seeder_never_raises_without_the_doctype(self):
		from .harness import INSTALLED_DOCTYPES

		INSTALLED_DOCTYPES.discard("Wizard Definition")
		try:
			self.assertEqual(wizards.install_wizard_definitions()["created"], [])
		finally:
			INSTALLED_DOCTYPES.add("Wizard Definition")

	def test_a_shipped_wizard_is_marked_as_one(self):
		data = self.tool_data("list_wizard_definitions", {"language": "en"})
		self.assertTrue(all(wizard["shipped_default"] for wizard in data["wizards"]))


# ── 3. the language is the worker's ─────────────────────────────────────────
class TheLanguageIsResolved(WizardTestCase):
	def test_a_spanish_speaker_gets_spanish(self):
		data = self.tool_data(
			"get_wizard_definition", {"wizard": "accident_investigation", "employee": WORKER}
		)
		self.assertEqual(data["language"], "es")
		self.assertEqual(data["title"], "Reportar un Accidente")

	def test_an_english_speaker_gets_english(self):
		data = self.tool_data(
			"get_wizard_definition",
			{"wizard": "accident_investigation", "employee": ENGLISH_SPEAKER},
		)
		self.assertEqual(data["title"], "Report an Accident")

	def test_the_field_labels_are_translated_too(self):
		data = self.tool_data(
			"get_wizard_definition", {"wizard": "accident_investigation", "employee": WORKER}
		)
		labels = [field["label"] for field in data["steps"][0]["fields"]]
		self.assertIn("¿Cuándo pasó?", labels)

	def test_the_select_options_are_translated(self):
		"""A severity a worker cannot read is a severity they pick at random."""
		data = self.tool_data(
			"get_wizard_definition", {"wizard": "accident_investigation", "employee": WORKER}
		)
		severity = next(
			field for step in data["steps"] for field in step["fields"] if field["fieldname"] == "severity"
		)
		labels = {option["label"] for option in severity["options"]}
		self.assertIn("Casi accidente — nadie herido", labels)

	def test_the_help_text_is_translated(self):
		"""On a discipline or accident wizard the difference between a usable
		record and a form is largely written in the help text."""
		data = self.tool_data(
			"get_wizard_definition", {"wizard": "progressive_discipline", "employee": WORKER}
		)
		helps = [field["help"] for step in data["steps"] for field in step["fields"] if field["help"]]
		self.assertTrue(any("actitud" in text for text in helps))

	def test_a_missing_translation_falls_back_and_is_reported(self):
		"""Silently serving English means nobody finds out until a worker is in
		front of a screen they cannot read."""
		doc = STORE.get_raw("Wizard Definition", "asset_registration")
		next(iter(doc["steps"]))["title_es"] = ""

		data = self.tool_data("get_wizard_definition", {"wizard": "asset_registration", "employee": WORKER})
		self.assertFalse(data["fully_translated"])
		self.assertTrue(data["untranslated"])
		self.assertIn("fell back", data["translation_note"])

	def test_the_fallback_is_the_english_string_rather_than_a_blank(self):
		doc = STORE.get_raw("Wizard Definition", "asset_registration")
		next(iter(doc["steps"]))["title_es"] = ""
		data = self.tool_data("get_wizard_definition", {"wizard": "asset_registration", "employee": WORKER})
		self.assertEqual(data["steps"][0]["title"], "What Is It")

	def test_a_fully_translated_wizard_reports_no_gaps(self):
		data = self.tool_data(
			"get_wizard_definition", {"wizard": "accident_investigation", "employee": WORKER}
		)
		self.assertTrue(data["fully_translated"])
		self.assertNotIn("translation_note", data)

	def test_an_employee_with_no_preference_gets_english_rather_than_a_guess(self):
		"""A phone set to English by whoever handed it over says nothing about
		who is holding it now."""
		STORE.get_raw("Employee", WORKER)["preferred_language"] = ""
		data = self.tool_data(
			"get_wizard_definition", {"wizard": "accident_investigation", "employee": WORKER}
		)
		self.assertEqual(data["language"], "en")

	def test_an_explicit_language_overrides_the_preference(self):
		"""For a supervisor checking a flow in English before their crew sees it
		in Spanish."""
		data = self.tool_data(
			"get_wizard_definition",
			{"wizard": "accident_investigation", "employee": WORKER, "language": "en"},
		)
		self.assertEqual(data["language"], "en")

	def test_the_onboarding_wizard_asks_which_language_at_hire(self):
		"""Because getting it wrong silently is the failure."""
		data = self.tool_data("get_wizard_definition", {"wizard": "employee_onboarding", "language": "en"})
		fields = [field["fieldname"] for step in data["steps"] for field in step["fields"]]
		self.assertIn("preferred_language", fields)


# ── 4. the flow has to be walkable ──────────────────────────────────────────
class TheFlowIsChecked(WizardTestCase):
	def _new(self, **overrides):
		import frappe

		payload = {
			"wizard_key": "custom_flow",
			"title_en": "Custom",
			"steps": [
				{
					"step_key": "one",
					"title_en": "One",
					"fields": [{"fieldname": "a", "field_type": "text", "label_en": "A"}],
				}
			],
		}
		payload.update(overrides)
		doc = frappe.new_doc("Wizard Definition")
		doc.wizard_key = payload["wizard_key"]
		doc.__newname = payload["wizard_key"]
		doc.title_en = payload["title_en"]
		for step in payload["steps"]:
			fields = step.pop("fields", [])
			row = doc.append("steps", step)
			for field in fields:
				row.append("fields", field)
		return doc

	def test_a_wizard_with_no_steps_is_refused(self):
		doc = self._new(steps=[])
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("no steps", str(caught.exception))

	def test_two_steps_with_one_key_are_refused(self):
		doc = self._new(
			steps=[
				{"step_key": "one", "title_en": "One", "fields": []},
				{"step_key": "one", "title_en": "Also one", "fields": []},
			]
		)
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("share the key", str(caught.exception))

	def test_a_next_step_pointing_at_nothing_is_refused(self):
		"""A worker reaching it would have nowhere to go."""
		doc = self._new(
			steps=[
				{"step_key": "one", "title_en": "One", "next_step": "nowhere", "fields": []},
			]
		)
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("nowhere to go", str(caught.exception))

	def test_two_fields_with_one_name_are_refused(self):
		"""One answer would overwrite the other."""
		doc = self._new(
			steps=[
				{
					"step_key": "one",
					"title_en": "One",
					"fields": [
						{"fieldname": "a", "field_type": "text", "label_en": "A"},
						{"fieldname": "a", "field_type": "number", "label_en": "A again"},
					],
				}
			]
		)
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("two fields called", str(caught.exception))

	def test_a_field_with_no_english_label_is_refused(self):
		"""English is what a missing translation falls back to."""
		doc = self._new(
			steps=[
				{
					"step_key": "one",
					"title_en": "One",
					"fields": [{"fieldname": "a", "field_type": "text", "label_en": ""}],
				}
			]
		)
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("English label", str(caught.exception))

	def test_malformed_options_are_refused_at_save_rather_than_at_a_gate(self):
		"""A select whose options will not parse renders empty on a handset in
		an orchard."""
		doc = self._new(
			steps=[
				{
					"step_key": "one",
					"title_en": "One",
					"fields": [
						{
							"fieldname": "a",
							"field_type": "select",
							"label_en": "A",
							"options": "{not json",
						}
					],
				}
			]
		)
		with self.assertRaises(Exception) as caught:
			doc.insert()
		self.assertIn("not valid JSON", str(caught.exception))

	def test_a_malformed_condition_shows_the_field_rather_than_hiding_it(self):
		"""Fail open: a worker seeing a question that did not apply is a minor
		annoyance; a required question silently hidden by a typo is a record
		with a hole in it that nobody notices."""
		self.assertIsNone(wizards._condition("{not json"))
