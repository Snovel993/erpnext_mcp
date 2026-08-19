# SPDX-License-Identifier: MIT
"""The string register: what a worker reads, in the language they read it in.

FIVE CLAIMS, AND EVERY CLASS IN THIS FILE IS ONE OF THEM.

1. **THE KEY IS STABLE AND THE ENGLISH IS A ROW.** `TheKeyIsNotTheEnglish`. This
   is the whole reason not to use Frappe's own Translation doctype, which is
   keyed by the source string: rewording an English sentence there orphans its
   Spanish silently, and "Open" the shift status cannot hold different Spanish
   from "Open" the button. Here the English is a row like any other and
   rewording it is editing that row.

2. **A MISSING TRANSLATION SERVES ENGLISH AND SAYS SO.** `TheFallbackIsLoud`.
   Never a blank (a screen nobody can act on), never the raw key (what a system
   shows when it has given up), never a refusal (a crew locked out of a flow over
   one sentence). And the gap is REPORTED, because silently serving English means
   nobody finds out until a worker is in front of a screen they cannot read.

3. **THE EMPLOYEE'S COLUMN BEATS THE DEVICE.** `TheLanguageIsTheWorkers`. A phone
   set to English by whoever handed it over says nothing about who is holding it
   now. The header is honoured only where the column is empty, and every answer
   says which of the two decided — because "why is this person seeing English" is
   a real support question.

4. **AN OPERATOR'S WORDING SURVIVES AN UPGRADE.** `TheSeederRespectsEdits`. A
   farm that reworded a phrase its crew kept misreading keeps the rewording; an
   UNEDITED shipped row is refreshed, which is what lets this app fix its own
   mistranslations on sites that never touched them. Both halves matter and the
   test proves both.

5. **PLACEHOLDERS ARE CHECKED ACROSS LANGUAGES.** `ThePlaceholdersMustMatch`. A
   Spanish string naming `{bloque}` where the English names `{block}` is not a
   wording difference — the caller fills by NAME, so it prints a literal brace to
   a worker or silently drops the value they were meant to act on. It is the one
   invariant nobody can check by eye, because the two strings are in different
   languages and one is one the reviewer may not read.
"""

import frappe

from erpnext_mcp.tools import translations, wizards

from .fixtures import MAIN, V12TestCase
from .harness import STORE

ALL_ON = {
	f"allow_{name}": 1
	for name in (
		"list_translations",
		"get_translation",
		"update_translation",
		"get_wizard_definition",
		"install_compliance_fields",
	)
}

SPANISH_SPEAKER = "HR-EMP-00001"
ENGLISH_SPEAKER = "HR-EMP-00002"
NOBODY_ASKED = "HR-EMP-00003"


class TranslationTestCase(V12TestCase):
	def setUp(self):
		super().setUp()
		self.configure(enabled=1, **ALL_ON)
		# `preferred_language` is a Custom Field, so the resolver only sees it
		# once the installer has run. Seeding the VALUE without the COLUMN would
		# prove the lookup works on a site this app never produces.
		from erpnext_mcp import compliance_fields

		compliance_fields.install_compliance_fields()
		translations.install_translations()
		STORE.seed(
			"Employee",
			[
				{
					"name": SPANISH_SPEAKER,
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
				{
					# THE THIRD PERSON IS THE POINT OF THE FIXTURE. An empty
					# column means "nobody asked", which is a different fact from
					# somebody saying English — and it is the only state in which
					# `Accept-Language` is allowed to decide anything.
					"name": NOBODY_ASKED,
					"employee_name": "Chris Vega",
					"company": MAIN,
					"status": "Active",
				},
			],
		)


# ── 1 ───────────────────────────────────────────────────────────────────────
class TheKeyIsNotTheEnglish(TranslationTestCase):
	def test_the_shipped_catalogue_lands_in_both_languages(self):
		rows = STORE.rows("Farm Translation")
		self.assertTrue(rows, "the seeder wrote nothing")
		languages = {row["language"] for row in rows}
		self.assertEqual(languages, {"en", "es"})

	def test_every_shipped_key_has_both_languages(self):
		"""A shipped key with no Spanish is a promise this release does not keep,
		and it would be INVISIBLE: `missing_only` would report it beside the
		operator's own gaps and nobody could tell which was which."""
		have = {(row["translation_key"], row["language"]) for row in STORE.rows("Farm Translation")}
		for key in translations.SHIPPED:
			with self.subTest(key=key):
				self.assertIn((key, "en"), have)
				self.assertIn((key, "es"), have)

	def test_the_docname_is_key_and_language(self):
		self.assertTrue(frappe.db.exists("Farm Translation", "shift.status.open::es"))

	def test_the_five_asked_for_groups_are_all_seeded(self):
		"""Farm task types, wizard labels, compliance form labels, shift status
		messages and error messages — the five the brief names."""
		categories = {row["category"] for row in STORE.rows("Farm Translation")}
		self.assertTrue(
			{
				"Task Types",
				"Wizard Labels",
				"Compliance Forms",
				"Shift Status",
				"Error Messages",
			}
			<= categories
		)

	def test_rewording_the_english_does_not_orphan_the_spanish(self):
		"""THE CLAIM THIS WHOLE DOCTYPE EXISTS FOR. Keyed by source text, editing
		the English would silently detach its translation."""
		before = self.tool_data("get_translation", {"key": "shift.status.open", "language": "es"})
		self.tool_data(
			"update_translation",
			{"key": "shift.status.open", "language": "en", "value": "Currently running"},
		)
		after = self.tool_data("get_translation", {"key": "shift.status.open", "language": "es"})
		self.assertEqual(after["value"], before["value"])
		self.assertFalse(after["fell_back"])

	def test_two_english_identical_strings_hold_different_spanish(self):
		"""'Open' the shift status is `abierto`; 'Open' the button is `abrir`.
		One source key cannot hold both, which is the second reason not to key on
		the English."""
		self.tool_data(
			"update_translation",
			{"key": "wizard.action.open", "language": "en", "value": "Open", "category": "Wizard Labels"},
		)
		self.tool_data(
			"update_translation", {"key": "wizard.action.open", "language": "es", "value": "Abrir"}
		)
		self.tool_data("update_translation", {"key": "shift.status.open", "language": "en", "value": "Open"})

		button = self.tool_data("get_translation", {"key": "wizard.action.open", "language": "es"})
		status = self.tool_data("get_translation", {"key": "shift.status.open", "language": "es"})
		self.assertEqual(button["value"], "Abrir")
		self.assertEqual(status["value"], "Abierto")

	def test_the_prefix_is_the_grouping(self):
		data = self.tool_data("list_translations", {"language": "es", "key_prefix": "error."})
		self.assertTrue(data["translations"])
		for row in data["translations"]:
			self.assertTrue(row["translation_key"].startswith("error."))

	def test_a_key_that_is_not_dotted_and_lowercase_is_refused(self):
		error = self.tool_error(
			"update_translation", {"key": "Shift Status Open", "language": "en", "value": "Open"}
		)
		self.assertIn("dotted", error)
		self.assertIn("Nothing was written", error)

	def test_a_single_segment_key_is_refused_because_the_prefix_is_the_grouping(self):
		error = self.tool_error(
			"update_translation", {"key": "harvest", "language": "en", "value": "Harvest"}
		)
		self.assertIn("at least two parts", error)


# ── 2 ───────────────────────────────────────────────────────────────────────
class TheFallbackIsLoud(TranslationTestCase):
	def test_a_missing_translation_serves_english_and_flags_it(self):
		self.tool_data(
			"update_translation",
			{"key": "block.gate.locked", "language": "en", "value": "The gate is locked."},
		)
		data = self.tool_data("get_translation", {"key": "block.gate.locked", "language": "es"})
		self.assertEqual(data["value"], "The gate is locked.")
		self.assertTrue(data["fell_back"])
		self.assertEqual(data["rendered_from"], "en")
		self.assertIn("no es for", data["translation_note"])

	def test_a_missing_translation_is_never_a_blank(self):
		self.tool_data(
			"update_translation", {"key": "block.gate.locked", "language": "en", "value": "Locked."}
		)
		data = self.tool_data("get_translation", {"key": "block.gate.locked", "language": "es"})
		self.assertNotEqual(data["value"].strip(), "")

	def test_a_missing_translation_is_never_the_raw_key(self):
		self.tool_data(
			"update_translation", {"key": "block.gate.locked", "language": "en", "value": "Locked."}
		)
		data = self.tool_data("get_translation", {"key": "block.gate.locked", "language": "es"})
		self.assertNotEqual(data["value"], "block.gate.locked")

	def test_english_asked_for_and_english_served_is_not_a_fallback(self):
		"""Reporting it as one would fill `untranslated` with noise until nobody
		read it."""
		data = self.tool_data("get_translation", {"key": "shift.status.open", "language": "en"})
		self.assertFalse(data["fell_back"])

	def test_a_missing_key_is_refused_and_named_as_a_different_problem(self):
		"""A key the app asks for and nobody seeded is a bug or an un-run
		migrate; a key with English and no Spanish is a translator's to-do.
		Conflating them files the first as the second and nobody looks again."""
		error = self.tool_error("get_translation", {"key": "no.such.key"})
		self.assertIn("MISSING KEY", error)
		self.assertIn("update_translation", error)

	def test_the_gap_is_listable(self):
		self.tool_data(
			"update_translation", {"key": "block.gate.locked", "language": "en", "value": "Locked."}
		)
		data = self.tool_data("list_translations", {"language": "es"})
		self.assertIn("block.gate.locked", data["missing_keys"])
		self.assertIn("serves ENGLISH", data["translation_note"])

	def test_missing_only_narrows_to_the_gap(self):
		self.tool_data(
			"update_translation", {"key": "block.gate.locked", "language": "en", "value": "Locked."}
		)
		data = self.tool_data("list_translations", {"language": "es", "missing_only": True})
		self.assertEqual([row["translation_key"] for row in data["translations"]], ["block.gate.locked"])

	def test_a_disabled_row_falls_back_as_though_absent(self):
		"""A wrong translation a worker acted on is evidence, so it is withdrawn
		rather than deleted — and a withdrawn row must not still be served."""
		STORE.get_raw("Farm Translation", "shift.status.open::es")["enabled"] = 0
		data = self.tool_data("get_translation", {"key": "shift.status.open", "language": "es"})
		self.assertEqual(data["value"], "Open")
		self.assertTrue(data["fell_back"])

	def test_translate_never_raises_on_a_key_that_does_not_exist(self):
		"""The convenience the rest of the app calls. A translation layer that
		could fail would become the reason a bucket sync failed."""
		self.assertEqual(translations.translate("no.such.key", "es"), "no.such.key")
		self.assertEqual(translations.translate("no.such.key", "es", default="Fallback"), "Fallback")

	def test_render_leaves_a_placeholder_it_was_given_no_value_for(self):
		"""`str.format` raises KeyError, and this is called on the ERROR path —
		so a translation naming a placeholder the caller did not pass would turn
		a handled refusal into an unhandled crash at the worst moment."""
		self.assertEqual(translations.render("Until {until} at {gate}", until="6pm"), "Until 6pm at {gate}")


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheLanguageIsTheWorkers(TranslationTestCase):
	def test_the_employee_column_decides(self):
		data = self.tool_data("get_translation", {"key": "shift.status.open", "employee": SPANISH_SPEAKER})
		self.assertEqual(data["language"], "es")
		self.assertEqual(data["language_source"], "employee")
		self.assertEqual(data["value"], "Abierto")

	def test_an_explicit_language_wins_over_the_column(self):
		"""An operator previewing what the Spanish crew will see."""
		data = self.tool_data(
			"get_translation",
			{"key": "shift.status.open", "employee": SPANISH_SPEAKER, "language": "en"},
		)
		self.assertEqual(data["language_source"], "explicit")
		self.assertEqual(data["value"], "Open")

	def test_the_header_is_only_consulted_when_the_column_is_empty(self):
		"""THE COMPLIANCE POSITION. A phone set to English by whoever handed it
		over says nothing about who is holding it now, and this app's claim to
		have trained somebody in a language they understand rests on the column."""
		stated, source = translations.resolve_language(employee=SPANISH_SPEAKER, header="en-US,en;q=0.9")
		self.assertEqual((stated, source), ("es", "employee"))

		guessed, source = translations.resolve_language(employee=NOBODY_ASKED, header="es-MX,es;q=0.9")
		self.assertEqual((guessed, source), ("es", "header"))

	def test_nothing_at_all_is_english_and_says_so(self):
		self.assertEqual(translations.resolve_language(), ("en", "default"))

	def test_a_region_subtag_resolves_to_the_language_this_site_has(self):
		"""`es-MX` and `es-419` are both asking for the Spanish this site holds.
		Answering 'no such language' would serve English to a Spanish speaker
		over a subtag nobody in this app has an opinion about."""
		self.assertEqual(translations.normalize_language("es-MX"), "es")
		self.assertEqual(translations.accept_language("es-419,es;q=0.9"), "es")

	def test_a_known_language_beats_a_higher_weighted_unknown_one(self):
		"""A phone whose first choice is French and second is Spanish should get
		Spanish, not English."""
		self.assertEqual(translations.accept_language("fr-CA,fr;q=0.9,es;q=0.5"), "es")

	def test_an_unparseable_header_says_nothing_rather_than_raising(self):
		self.assertEqual(translations.accept_language("!!!"), "")
		self.assertEqual(translations.accept_language(""), "")
		self.assertEqual(translations.accept_language("en;q=banana"), "")

	def test_the_employee_default_does_not_backfill_an_existing_blank(self):
		"""A default on the FORM is not a backfill. An empty column still means
		'nobody asked', which is a different fact from somebody saying English —
		and this is the person whose training record has a hole in it."""
		self.assertEqual(translations.preferred_language(employee=NOBODY_ASKED), "")


# ── 4 ───────────────────────────────────────────────────────────────────────
class TheSeederRespectsEdits(TranslationTestCase):
	def test_an_operator_edit_survives_the_next_migrate(self):
		self.tool_data(
			"update_translation",
			{"key": "shift.status.open", "language": "es", "value": "En marcha"},
		)
		translations.install_translations()
		self.assertEqual(STORE.get_raw("Farm Translation", "shift.status.open::es")["value"], "En marcha")

	def test_an_update_marks_the_row_as_the_operators(self):
		data = self.tool_data(
			"update_translation", {"key": "shift.status.open", "language": "es", "value": "En marcha"}
		)
		self.assertTrue(data["operator_edited"])
		self.assertIn("never overwrite", data["seeder_note"])

	def test_an_unedited_shipped_row_is_refreshed(self):
		"""THE OTHER HALF, and it is why this seeder is not shaped like the
		wizard one. Without it a shipped MISTRANSLATION would be permanent on
		every site it had ever landed on."""
		STORE.get_raw("Farm Translation", "shift.status.open::es")["value"] = "una traducción vieja"
		report = translations.install_translations()
		self.assertIn("shift.status.open::es", report["updated"])
		self.assertEqual(STORE.get_raw("Farm Translation", "shift.status.open::es")["value"], "Abierto")

	def test_the_seeder_is_idempotent(self):
		first = translations.install_translations()
		second = translations.install_translations()
		self.assertEqual(second["created"], [])
		self.assertEqual(second["updated"], [])
		self.assertEqual(first["failed"], [])
		self.assertEqual(second["failed"], [])

	def test_a_key_the_app_has_never_heard_of_can_be_added(self):
		"""How a site labels something this app ships no word for."""
		self.tool_data(
			"update_translation",
			{"key": "variety.honeycrisp", "language": "es", "value": "Honeycrisp", "category": "Other"},
		)
		self.assertTrue(frappe.db.exists("Farm Translation", "variety.honeycrisp::es"))

	def test_a_category_that_is_not_on_the_register_is_refused_with_the_list(self):
		error = self.tool_error(
			"update_translation",
			{"key": "a.b", "language": "en", "value": "x", "category": "Nonsense"},
		)
		self.assertIn("Shift Status", error)
		self.assertIn("Nothing was written", error)


# ── 5 ───────────────────────────────────────────────────────────────────────
class ThePlaceholdersMustMatch(TranslationTestCase):
	def test_a_mismatched_placeholder_is_refused_before_anything_is_written(self):
		self.tool_data(
			"update_translation",
			{"key": "block.rei.until", "language": "en", "value": "Do not enter until {until}."},
		)
		error = self.tool_error(
			"update_translation",
			{"key": "block.rei.until", "language": "es", "value": "No entrar hasta {hasta}."},
		)
		self.assertIn("{until}", error)
		self.assertIn("{hasta}", error)
		self.assertIn("Nothing was written", error)
		self.assertFalse(frappe.db.exists("Farm Translation", "block.rei.until::es"))

	def test_a_matching_placeholder_is_accepted(self):
		self.tool_data(
			"update_translation",
			{"key": "block.rei.until", "language": "en", "value": "Do not enter until {until}."},
		)
		data = self.tool_data(
			"update_translation",
			{"key": "block.rei.until", "language": "es", "value": "No entrar hasta {until}."},
		)
		self.assertEqual(data["placeholders"], ["until"])

	def test_the_english_is_never_held_hostage_by_a_bad_translation(self):
		"""Refusing to save the SOURCE string because a translation of it
		disagrees would make one bad Spanish row block every correction to the
		English."""
		self.tool_data(
			"update_translation",
			{"key": "block.rei.until", "language": "en", "value": "Until {until}."},
		)
		self.tool_data(
			"update_translation",
			{"key": "block.rei.until", "language": "en", "value": "Closed until {until}, no entry."},
		)

	def test_every_shipped_pair_agrees_on_its_placeholders(self):
		"""The catalogue this app ships has to pass its own rule."""
		import re

		pattern = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
		for key, (_category, english, spanish) in translations.SHIPPED.items():
			with self.subTest(key=key):
				self.assertEqual(set(pattern.findall(english)), set(pattern.findall(spanish)))


# ── 6: the `tr:` escape, which is how a wizard shares a string ──────────────
class TheWizardCanPointAtTheRegister(TranslationTestCase):
	def setUp(self):
		super().setUp()
		wizards.install_wizard_definitions()

	def test_a_tr_prefixed_label_resolves_through_the_register(self):
		"""The string that appears in nine wizards. Nine copies drift, and the
		ninth is the one nobody fixed."""
		row = {"label_en": "tr:wizard.field.photo"}
		missing: list = []
		self.assertEqual(wizards._resolve(row, "label", "es", missing, "step.field"), "Fotografía")
		self.assertEqual(missing, [])

	def test_a_plain_label_is_a_literal_and_is_untouched(self):
		"""What makes this safe on a site whose wizards an operator has already
		edited: anything without the prefix behaves exactly as it did before."""
		row = {"label_en": "When did it happen?"}
		missing: list = []
		self.assertEqual(wizards._resolve(row, "label", "es", missing, "step.field"), "When did it happen?")
		self.assertEqual(missing, [{"where": "step.field", "key": "label", "language": "es"}])

	def test_a_tr_reference_that_falls_back_is_reported_on_the_same_channel(self):
		"""One gap looks the same to a reader wherever it came from."""
		self.tool_data(
			"update_translation",
			{"key": "wizard.field.gate", "language": "en", "value": "Gate", "category": "Wizard Labels"},
		)
		row = {"label_en": "tr:wizard.field.gate"}
		missing: list = []
		self.assertEqual(wizards._resolve(row, "label", "es", missing, "step.gate"), "Gate")
		self.assertEqual(missing[0]["key"], "wizard.field.gate")
		self.assertEqual(missing[0]["language"], "es")

	def test_a_tr_reference_to_a_key_nobody_seeded_shows_the_key_not_a_blank(self):
		"""A wizard step whose label vanished is unreportable by the worker
		looking at it. The key at least tells whoever gets the screenshot which
		row to go and write."""
		row = {"label_en": "tr:wizard.field.nothing"}
		missing: list = []
		self.assertEqual(wizards._resolve(row, "label", "es", missing, "step.x"), "wizard.field.nothing")
		self.assertEqual(missing[0]["reason"], "no such key")

	def test_a_per_wizard_column_still_wins_over_the_register(self):
		"""A string that belongs to one wizard stays on that wizard's own
		columns. The register is for the string that appears in nine."""
		row = {"label_en": "tr:wizard.field.photo", "label_es": "Foto del accidente"}
		missing: list = []
		self.assertEqual(wizards._resolve(row, "label", "es", missing, "step.field"), "Foto del accidente")
