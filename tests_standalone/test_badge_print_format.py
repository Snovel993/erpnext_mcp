# SPDX-License-Identifier: MIT
"""The Desk's Print button on a badge — the seeded format and the card it renders.

v0.56.0. `generate_employee_badge_qr` has issued the identifier and drawn the
symbol since v0.50.0, and `_print_spec` states in numbers what a layout must
honour and then says THIS APP DOES NOT LAY THE CARD OUT. Without a format the
Print button rendered Frappe's standard one: Badge ID, Company, Employee, Active,
Notes — every fact on the record and nothing anybody can clip to a lanyard.

SEVEN CLAIMS.

1. `TheSeeder` — created on migrate, once, and an operator's edits survive every
   migrate after that. It is the whole reason this is seeded rather than
   fixtured, and it matters more here than for the I-9: laying out a CR-80 card
   is a fractional-millimetre argument with one specific card printer.
2. `TheFormatRecord` — custom rather than standard, Jinja, pointed at Bucket Log
   Badge Map, and zero margins because the card IS the page.
3. `TheCardGeometry` — CR-80 in three places that have to agree, and a back-of-
   card symbol at exactly the 1.5" `tools/badges` says a phone needs.
4. `TheJinjaGlobal` — `erpnext_mcp_badge_card` answers with a dict, never raises,
   and refuses to read a file outside this site's own two directories.
5. `TheTemplateRenders` — it renders against a real record without raising, under
   `StrictUndefined`, so a field nobody has fails here and not under somebody's
   finger at six in the morning.
6. `NoExternalFetch` — every image on the card is a `data:` URI. This is the
   claim `i9_print_format` keeps by banning `<img>` outright; a badge cannot take
   that ban, so it keeps the same promise a different way.
7. `TheFormScript` — the "View Badge" button names the format that is actually
   seeded. Two halves that have to agree, and nothing else makes them.

`jinja2` is not a declared dependency of this app — it arrives with Frappe — so
the rendering classes skip without it, exactly as `test_i9_print_format.py` does.
"""

import os
import pathlib
import unittest

import frappe

from erpnext_mcp import badge_print_format
from erpnext_mcp.badge_print_format import FORMAT_NAME, PRINT_FORMAT
from erpnext_mcp.render import badge_card
from erpnext_mcp.tools import badges

from .fixtures import MAIN, MAIN_ABBR, SeededTestCase, install_hrms
from .harness import STORE

try:
	import jinja2

	HAS_JINJA = True
except Exception:  # pragma: no cover - a bench without jinja2
	jinja2 = None
	HAS_JINJA = False

BADGE_DOCTYPE = "Bucket Log Badge Map"
EMP = "HR-EMP-00001"
EMP_NAME = "Ada Orchard"
BADGE = f"{MAIN_ABBR}-0001"

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "erpnext_mcp"
FORM_SCRIPT = APP_DIR / "erpnext_mcp" / "doctype" / "bucket_log_badge_map" / "bucket_log_badge_map.js"


class BadgeFormatTestCase(SeededTestCase):
	"""One badge on the fixture site, and the format seeded against it."""

	def setUp(self):
		super().setUp()
		install_hrms()
		STORE.rows(PRINT_FORMAT).clear()
		STORE.rows(BADGE_DOCTYPE).clear()
		STORE.seed(
			BADGE_DOCTYPE,
			[{"name": BADGE, "badge_id": BADGE, "company": MAIN, "employee": EMP, "active": 1}],
		)

	def render(self, name: str = BADGE) -> str:
		"""The page Frappe's Print button would produce for one badge.

		`autoescape=False` because that is what Frappe's own print environment
		does — every stock ERPNext format relies on it. `StrictUndefined` is the
		opposite trade and deliberately stricter than Frappe's own: a key the
		template asks for and the global does not return raises here rather than
		printing the word "Undefined" onto somebody's ID card.
		"""
		badge_print_format.seed_badge_print_format()
		html = STORE.get_raw(PRINT_FORMAT, FORMAT_NAME)["html"]
		environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
		environment.globals["erpnext_mcp_badge_card"] = badge_card.erpnext_mcp_badge_card
		return environment.from_string(html).render(doc=frappe.get_doc(BADGE_DOCTYPE, name))


# ── 1 ─────────────────────────────────────────────────────────────────────────
class TheSeeder(BadgeFormatTestCase):
	def test_it_creates_the_format(self):
		report = badge_print_format.seed_badge_print_format()
		self.assertTrue(report["created"])
		self.assertEqual(report["name"], FORMAT_NAME)
		self.assertTrue(frappe.db.exists(PRINT_FORMAT, FORMAT_NAME))

	def test_a_second_migrate_creates_nothing(self):
		badge_print_format.seed_badge_print_format()
		report = badge_print_format.seed_badge_print_format()
		self.assertFalse(report["created"])
		self.assertEqual(report["reason"], "already present")

	def test_an_operators_edit_survives_every_future_migrate(self):
		"""THE WHOLE REASON THIS IS SEEDED RATHER THAN FIXTURED.

		A fixture is rewritten from the app's files on every `bench migrate`. On a
		card printer that is not a cosmetic loss: an operator whose own DTC4250e
		feeds four tenths of a millimetre high nudges the layout once, and a
		fixture would put it back at the next upgrade without a word.
		`test_hooks.py` forbids the word `fixtures` by name; this is what the rule
		buys.
		"""
		badge_print_format.seed_badge_print_format()
		frappe.db.set_value(PRINT_FORMAT, FORMAT_NAME, "html", "<p>mine</p>")

		badge_print_format.seed_badge_print_format()
		self.assertEqual(frappe.db.get_value(PRINT_FORMAT, FORMAT_NAME, "html"), "<p>mine</p>")

	def test_it_never_raises_when_the_insert_itself_fails(self):
		"""A seeder that raised would take `bench migrate` down with it."""
		original = frappe.get_doc

		def explode(*args, **kwargs):
			if args and isinstance(args[0], dict) and args[0].get("doctype") == PRINT_FORMAT:
				raise RuntimeError("no room at the inn")
			return original(*args, **kwargs)

		frappe.get_doc = explode
		try:
			report = badge_print_format.seed_badge_print_format()
		finally:
			frappe.get_doc = original

		self.assertFalse(report["created"])
		self.assertIn("no room at the inn", report["reason"])
		self.assertFalse(frappe.db.exists(PRINT_FORMAT, FORMAT_NAME))

	def test_install_and_migrate_both_seed_it(self):
		"""A site upgrading gets the card without a bespoke patch, which is the
		same contract every other seeder in `install.py` keeps."""
		import inspect

		from erpnext_mcp import install

		for hook in (install.after_install, install.after_migrate):
			with self.subTest(hook=hook.__name__):
				self.assertIn("_badge_print_format()", inspect.getsource(hook))


# ── 2 ─────────────────────────────────────────────────────────────────────────
class TheFormatRecord(BadgeFormatTestCase):
	def fields(self) -> dict:
		badge_print_format.seed_badge_print_format()
		return STORE.get_raw(PRINT_FORMAT, FORMAT_NAME)

	def test_it_is_custom_rather_than_standard(self):
		"""`standard = "No"` is the other half of what makes an edit survive."""
		row = self.fields()
		self.assertEqual(row["standard"], "No")
		self.assertEqual(row["custom_format"], 1)

	def test_it_is_jinja_and_not_the_print_format_builder(self):
		row = self.fields()
		self.assertEqual(row["print_format_type"], "Jinja")
		self.assertEqual(row["print_format_builder"], 0)

	def test_it_is_pointed_at_the_badge_register(self):
		self.assertEqual(self.fields()["doc_type"], BADGE_DOCTYPE)

	def test_every_margin_is_zero_because_the_card_is_the_page(self):
		"""The opposite of what `i9_print_format` chooses, and for the opposite
		reason: an I-9 is printed on plain paper and wants a margin to punch holes
		in. A CR-80 blank has no margin — a millimetre of one is a millimetre of
		artwork off the edge of the card."""
		row = self.fields()
		for side in ("margin_top", "margin_bottom", "margin_left", "margin_right"):
			with self.subTest(side=side):
				self.assertEqual(row[side], 0)

	def test_the_page_size_is_one_this_site_actually_offers(self):
		"""A Print Format's `page_size` is a Select and Frappe validates a Select
		against its options — so a "Custom" written to a site that does not offer
		it is not a slightly wrong page, it is NO FORMAT AT ALL."""
		row = self.fields()
		meta = frappe.get_meta(PRINT_FORMAT)
		field = meta.get_field("page_size") if hasattr(meta, "get_field") else None
		options = [line.strip() for line in str(getattr(field, "options", "") or "").splitlines()]
		if options:
			self.assertIn(row["page_size"], options)

	def test_a_custom_page_size_carries_the_card_millimetres(self):
		fields = badge_print_format.print_format_fields()
		if fields.get("page_size") != "Custom":
			self.skipTest("this site's Print Format offers no Custom page size")
		self.assertEqual(fields["page_width"], badge_print_format.CARD_WIDTH_MM)
		self.assertEqual(fields["page_height"], badge_print_format.CARD_HEIGHT_MM)


# ── 3 ─────────────────────────────────────────────────────────────────────────
class TheCardGeometry(unittest.TestCase):
	"""CR-80 is stated in three places that have to agree, because three different
	renderers read three different ones of them."""

	def test_the_card_is_iso_7810_id_1(self):
		self.assertEqual(badge_print_format.CARD_WIDTH_MM, 85.6)
		self.assertEqual(badge_print_format.CARD_HEIGHT_MM, 54.0)

	def test_the_at_page_rule_carries_the_same_millimetres(self):
		"""`@page` is what a BROWSER honours, and the browser is the path that
		matters: a card printer is driven from the print dialogue, not from a
		downloaded PDF."""
		self.assertIn("@page { size: 85.6mm 54mm; margin: 0; }", badge_print_format.CARD_PAGE_CSS)

	def test_the_card_box_carries_the_same_millimetres(self):
		"""A page that is the right size with content laid out for a Letter sheet
		is a card with the top left corner of a form on it."""
		self.assertIn("width: 85.6mm; height: 54mm", badge_print_format.CARD_CSS)

	def test_the_back_symbol_is_exactly_the_scanning_floor_tools_badges_states(self):
		"""`BADGE_PRINT_INCHES` is 1.5" and says why: below about an inch a phone
		at arm's length in bright orchard sun hunts instead of locking, and that
		presents to a picker as "the scanner is broken". A 38.1mm square will not
		sit on the front of an 85.6 x 54 card beside a photograph — hence a second
		side, and hence this assertion, so the two cannot drift apart in silence.
		"""
		self.assertAlmostEqual(badge_print_format.QR_BACK_MM, badges.BADGE_PRINT_INCHES * 25.4, places=2)
		self.assertIn("width: 38.1mm; height: 38.1mm", badge_print_format.CARD_CSS)

	def test_the_front_symbol_is_smaller_and_that_is_the_documented_trade(self):
		self.assertLess(badge_print_format.QR_FRONT_MM, badge_print_format.QR_BACK_MM)
		self.assertIn("dual-side", badge_print_format.__doc__.lower())

	def test_the_media_print_block_hides_frappes_preview_chrome(self):
		"""On a sheet of Letter the print view's gutter and shadow are invisible
		padding. On a card they are the reason the artwork comes out down and to
		the right of where it was drawn."""
		css = badge_print_format.CARD_PAGE_CSS
		self.assertIn("@media print", css)
		for chrome in (".print-preview", ".print-format", ".letter-head", ".print-toolbar"):
			with self.subTest(chrome=chrome):
				self.assertIn(chrome, css)

	def test_the_last_card_does_not_force_a_blank_page_after_it(self):
		self.assertIn(".badge-card:last-child", badge_print_format.CARD_PAGE_CSS)

	def test_the_base_rules_come_before_the_print_overrides(self):
		"""ORDER IS PRECEDENCE. A stylesheet that stated the `@media print`
		override first would lose the argument at the moment it mattered."""
		template = badge_print_format.BADGE_TEMPLATE
		self.assertLess(template.index(".badge-card {"), template.index("@media print"))


# ── 4 ─────────────────────────────────────────────────────────────────────────
class TheJinjaGlobal(BadgeFormatTestCase):
	def test_it_resolves_the_badge_the_employee_and_the_entity(self):
		card = badge_card.erpnext_mcp_badge_card(BADGE)
		self.assertTrue(card["ok"])
		self.assertEqual(card["badge_id"], BADGE)
		self.assertEqual(card["employee"], EMP)
		self.assertEqual(card["employee_name"], EMP_NAME)
		self.assertEqual(card["designation"], "Supervisor")
		self.assertEqual(card["employee_number"], "E-100")
		self.assertEqual(card["company"], MAIN)

	def test_a_badge_nothing_knows_about_is_a_dict_and_not_an_exception(self):
		"""It is called mid-render from a Print Format. A raise here is a Print
		button that does nothing and says nothing."""
		card = badge_card.erpnext_mcp_badge_card("CF-9999")
		self.assertFalse(card["ok"])
		self.assertEqual(card["badge_id"], "CF-9999")

	def test_an_empty_or_absent_badge_id_is_also_a_dict(self):
		for value in ("", None, 0):
			with self.subTest(value=value):
				self.assertFalse(badge_card.erpnext_mcp_badge_card(value)["ok"])

	def test_a_retired_badge_is_reported_as_retired(self):
		"""A retired badge that printed as a live one is the exact situation
		`active` exists to prevent."""
		frappe.db.set_value(BADGE_DOCTYPE, BADGE, "active", 0)
		self.assertFalse(badge_card.erpnext_mcp_badge_card(BADGE)["active"])

	def test_it_survives_the_database_falling_over_mid_render(self):
		original = frappe.db.get_value

		def explode(*args, **kwargs):
			raise RuntimeError("gone")

		frappe.db.get_value = explode
		try:
			card = badge_card.erpnext_mcp_badge_card(BADGE)
		finally:
			frappe.db.get_value = original
		self.assertIsInstance(card, dict)
		self.assertFalse(card["ok"])

	def test_initials_stand_in_for_a_photograph_nobody_uploaded(self):
		self.assertEqual(badge_card.initials("Ada Orchard"), "AO")
		self.assertEqual(badge_card.initials("Ada"), "AD")
		self.assertEqual(badge_card.initials(""), "?")

	def test_the_initials_rule_matches_the_one_the_sheet_tool_applies(self):
		"""Duplicated rather than imported — `render/badge_card.py` says why — so
		the two are held to the same answer here instead."""
		for name in ("Ada Orchard", "Ben Packhouse", "Cara", "", "  ", "Ana M. Reyes"):
			with self.subTest(name=name):
				self.assertEqual(badge_card.initials(name), badges._initials(name))

	def test_the_logo_field_is_spelled_the_same_in_both_modules(self):
		self.assertEqual(badge_card.BADGE_LOGO_FIELD, badges.BADGE_LOGO_FIELD)


class TheFileReader(unittest.TestCase):
	"""`_site_path` turns a string from a database column into an open file handle,
	so it gets the cheap half of that job checked."""

	def test_it_reads_a_public_file(self):
		root = frappe.get_site_path("public", "files")
		os.makedirs(root, exist_ok=True)
		self.assertEqual(
			badge_card._site_path("/files/badge.png"), os.path.realpath(os.path.join(root, "badge.png"))
		)

	def test_it_reads_a_private_file(self):
		root = frappe.get_site_path("private", "files")
		os.makedirs(root, exist_ok=True)
		self.assertEqual(
			badge_card._site_path("/private/files/x.png"),
			os.path.realpath(os.path.join(root, "x.png")),
		)

	def test_it_refuses_to_climb_out_of_the_files_directory(self):
		for url in ("/files/../../site_config.json", "/private/files/../../../etc/passwd"):
			with self.subTest(url=url):
				self.assertEqual(badge_card._site_path(url), "")

	def test_it_refuses_an_absolute_url_outright(self):
		"""THE FETCH IS THE HAZARD, and a badge that quietly reached out to a host
		named in a database column would be that hazard wearing the fix's
		clothes."""
		for url in ("http://example.test/x.png", "https://example.test/x.png", "//example.test/x.png"):
			with self.subTest(url=url):
				self.assertEqual(badge_card._site_path(url), "")

	def test_it_refuses_a_path_that_is_neither(self):
		for url in ("", None, "badge.png", "/assets/x.png", "/private/backups/x.sql"):
			with self.subTest(url=url):
				self.assertEqual(badge_card._site_path(url), "")

	def test_an_unreadable_file_is_an_empty_string_and_not_an_exception(self):
		self.assertEqual(badge_card._data_uri("/files/nothing-is-here.png"), "")

	def test_a_file_type_it_does_not_know_is_not_guessed_at(self):
		"""`data:` with the wrong media type renders as a broken-image icon, which
		looks like a bug in the badge rather than in the upload."""
		self.assertEqual(badge_card._data_uri("/files/resume.pdf"), "")

	def test_a_real_png_comes_back_as_a_data_uri(self):
		root = frappe.get_site_path("public", "files")
		os.makedirs(root, exist_ok=True)
		path = os.path.join(root, "badge-test-photo.png")
		with open(path, "wb") as handle:
			handle.write(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
		try:
			uri = badge_card._data_uri("/files/badge-test-photo.png")
		finally:
			os.remove(path)
		self.assertTrue(uri.startswith("data:image/png;base64,"))

	def test_an_oversized_photograph_falls_back_to_the_initials_block(self):
		"""A base64 `data:` URI is a third larger than the file and lands in the
		document twice on a two-sided card. A twelve-megapixel phone photograph
		would be a print page the browser renders as a spinner."""
		root = frappe.get_site_path("public", "files")
		os.makedirs(root, exist_ok=True)
		path = os.path.join(root, "badge-test-huge.png")
		with open(path, "wb") as handle:
			handle.write(b"0" * (badge_card.MAX_EMBEDDED_BYTES + 1))
		try:
			self.assertEqual(badge_card._data_uri("/files/badge-test-huge.png"), "")
		finally:
			os.remove(path)


# ── 5 ─────────────────────────────────────────────────────────────────────────
@unittest.skipUnless(HAS_JINJA, "jinja2 is not installed")
class TheTemplateRenders(BadgeFormatTestCase):
	def test_it_renders_a_real_badge_without_raising(self):
		self.assertIn("badge-card", self.render())

	def test_the_facts_a_foreman_reads_are_on_the_card(self):
		html = self.render()
		for fact in (BADGE, EMP_NAME, "Supervisor", MAIN):
			with self.subTest(fact=fact):
				self.assertIn(fact, html)

	def test_there_are_two_cards_because_the_printer_has_two_sides(self):
		self.assertEqual(self.render().count('class="badge-card"'), 2)

	def test_a_retired_badge_says_so_across_the_card(self):
		frappe.db.set_value(BADGE_DOCTYPE, BADGE, "active", 0)
		self.assertIn("RETIRED", self.render())

	def test_a_live_badge_does_not(self):
		self.assertNotIn("RETIRED", self.render())

	def test_an_employee_with_no_photograph_gets_their_initials(self):
		html = self.render()
		self.assertIn("bc-initials", html)
		self.assertIn(">AO<", html.replace("\n", "").replace("  ", ""))

	def test_it_renders_when_the_register_has_no_such_badge(self):
		"""A Print button pressed on a record mid-rename must not 500."""
		STORE.seed(
			BADGE_DOCTYPE,
			[{"name": "CF-0404", "badge_id": "CF-0404", "company": MAIN, "employee": EMP, "active": 1}],
		)
		STORE.rows(BADGE_DOCTYPE)[:] = [
			row for row in STORE.rows(BADGE_DOCTYPE) if row.get("name") != "CF-0404"
		]
		environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
		environment.globals["erpnext_mcp_badge_card"] = badge_card.erpnext_mcp_badge_card
		html = environment.from_string(badge_print_format.BADGE_TEMPLATE).render(
			doc=frappe.get_doc(
				{"doctype": BADGE_DOCTYPE, "name": "CF-0404", "badge_id": "CF-0404", "active": 1}
			)
		)
		self.assertIn("CF-0404", html)

	def test_the_fallback_card_answers_every_key_the_template_asks_for(self):
		"""THE TEST THAT WOULD HAVE CAUGHT IT. The `is defined` fallback builds a
		card dict inline in the template, and it shipped one key short of what the
		markup dereferences — so the belt-and-brace path, the one that only runs
		when something has already gone wrong, raised.

		Asserted against the real global's keys rather than a list written out
		here, so a key added to one side has to be added to the other.
		"""
		import re

		fallback = re.search(r"\{%- set card = \{(.*?)\} -%\}", badge_print_format.BADGE_TEMPLATE, re.S)
		self.assertIsNotNone(fallback, "the template no longer carries an inline fallback card")
		declared = set(re.findall(r'"(\w+)":', fallback.group(1)))

		asked_for = set(re.findall(r"card\.(\w+)", badge_print_format.BADGE_TEMPLATE))
		self.assertTrue(asked_for)
		self.assertEqual(
			asked_for - declared,
			set(),
			"the template dereferences a key the fallback card does not define",
		)

	def test_it_renders_with_the_jinja_global_absent_entirely(self):
		"""BELT TO THE BRACE, the same one `printing.CHECK_TEMPLATE` wears. A site
		whose hook has not loaded prints a plainer card, not a traceback."""
		environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
		html = environment.from_string(badge_print_format.BADGE_TEMPLATE).render(
			doc=frappe.get_doc(BADGE_DOCTYPE, BADGE)
		)
		self.assertIn(BADGE, html)
		self.assertIn("badge-card", html)


# ── 6 ─────────────────────────────────────────────────────────────────────────
@unittest.skipUnless(HAS_JINJA, "jinja2 is not installed")
class NoExternalFetch(BadgeFormatTestCase):
	"""wkhtmltopdf fetches every external URL synchronously with no timeout worth
	the name, so one image that 404s hangs the render or blanks the page.

	`i9_print_format` keeps that promise by banning `<img>` outright. A card with
	no QR is not a badge and a card with no face is not an ID, so this format
	keeps the SAME promise a different way: the hazard is the fetch, not the tag,
	and bytes already in the document open no socket.
	"""

	def sources(self) -> list:
		import re

		return re.findall(r'<img[^>]+src="([^"]*)"', self.render())

	def test_every_image_on_the_card_is_a_data_uri(self):
		found = self.sources()
		self.assertTrue(found, "the card rendered with no images at all")
		for src in found:
			with self.subTest(src=src[:40]):
				self.assertTrue(src.startswith("data:"))

	def test_the_page_references_no_external_host(self):
		html = self.render()
		for scheme in ("http://", "https://", "//cdn", "<link"):
			with self.subTest(scheme=scheme):
				self.assertNotIn(scheme, html)

	def test_no_webfont_is_asked_for(self):
		self.assertNotIn("@font-face", badge_print_format.BADGE_TEMPLATE)
		self.assertNotIn("@import", badge_print_format.BADGE_TEMPLATE)


# ── 7 ─────────────────────────────────────────────────────────────────────────
class TheFormScript(unittest.TestCase):
	"""The "View Badge" button, and the one thing about it a test can hold."""

	def source(self) -> str:
		return FORM_SCRIPT.read_text(encoding="utf-8")

	def test_the_script_ships_beside_the_doctype_it_belongs_to(self):
		"""Frappe loads `<doctype>.js` from the folder beside the JSON, so this
		button needs no hook at all — which is one fewer key in `hooks.py` and one
		fewer promise to keep."""
		self.assertTrue(FORM_SCRIPT.is_file())

	def test_it_carries_the_licence_header(self):
		self.assertEqual(self.source().split("\n", 1)[0], "// SPDX-License-Identifier: MIT")

	def test_it_names_the_format_that_is_actually_seeded(self):
		"""The two halves have to agree and nothing else makes them — the same
		guard `test_hooks.py` puts on the check template's Jinja global."""
		self.assertIn(f'"{FORMAT_NAME}"', self.source())

	def test_it_is_bound_to_the_badge_register(self):
		self.assertIn(f'frappe.ui.form.on("{BADGE_DOCTYPE}"', self.source())

	def test_it_asks_for_the_print_view_without_a_letterhead(self):
		"""A letterhead is a band across the top of a sheet of Letter. On an
		85.6 x 54mm card it is the top third of the badge."""
		source = self.source()
		self.assertIn("/printview?", source)
		self.assertIn("no_letterhead=1", source)

	def test_it_declares_no_global(self):
		"""A form script is evaluated by appending a <script> to the page, and a
		top-level `const` in a classic script is a global lexical binding — a
		second evaluation is a SyntaxError that takes the whole form script down.
		"""
		body = "\n".join(
			line
			for line in self.source().splitlines()
			if not line.startswith("//") and not line.strip().startswith("*")
		)
		self.assertIn("(function () {", body)
		self.assertIn("})();", body)
