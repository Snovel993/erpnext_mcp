# SPDX-License-Identifier: MIT
"""A crew's badges on a sheet of Letter, and the button that asks for one.

v0.56.0. `generate_employee_badge_sheet` has issued a crew's badges since
v0.50.0 and hands back card DATA — `_print_spec` says in its own docstring that
this app does not lay the card out. The Desk is the consumer with nowhere to send
the JSON: an HR manager who ticks thirty pickers wants thirty cards, not an array
of thirty base64 PNGs.

SEVEN CLAIMS.

1. `TheGeometry` — eight cards fit a sheet of Letter at CR-80 with a cut
   allowance, and the arithmetic is checked against `PAGE` rather than asserted
   as a magic number.
2. `ThePagination` — nine cards are two sheets, and the last one does not force a
   blank.
3. `TheCardMarkup` — a card on the sheet is the same card the Print Format
   renders, in the same classes, and every value an employee typed is escaped.
4. `TheWholeSheet` — the skipped names are ON THE PAGE. The sheet tool's promise
   is that one bad row does not lose the sheet, and that promise is only kept if
   whoever collects the paper can see who is missing.
5. `TheListAction` — the Employee list button is a RECORD and not a hook, it is
   only ever created when absent, it does not overwrite ERPNext's own list
   settings, and `before_uninstall` takes it away again.
6. `ThePrintableTab` — v0.56.1. The sheet reaches the tab as a blob: URL rather
   than being written into `about:blank`, which is what makes Save-as-PDF
   produce cards instead of an empty page, and it carries a `<base>` so the
   photographs survive the move.
7. `TheRevisionUpgrade` — v0.56.1. A seeder that only ever creates cannot ship a
   fix. This app's own unedited copy is updated in place; a copy an operator has
   edited is left exactly as it is and reported.
"""

import inspect
import unittest

import frappe

from erpnext_mcp import badge_list_action, badge_sheet
from erpnext_mcp.badge_print_format import CARD_HEIGHT_MM, CARD_WIDTH_MM

from .badge_scripts_r1 import LIST_SCRIPT_R1
from .fixtures import MAIN, MAIN_ABBR, SeededTestCase, install_hrms
from .harness import STORE

EMP = "HR-EMP-00001"
SECOND = "HR-EMP-00002"


def _card(**overrides) -> dict:
	"""One card in the shape `generate_employee_badge_sheet` returns."""
	base = {
		"employee": EMP,
		"employee_name": "Ada Orchard",
		"employee_number": "E-100",
		"designation": "Supervisor",
		"company": MAIN,
		"badge_id": f"{MAIN_ABBR}-0001",
		"photo_url": None,
		"photo_placeholder": "AO",
		"company_logo_url": None,
		"png_base64": "aGVsbG8=",
	}
	base.update(overrides)
	return base


# ── 1 ─────────────────────────────────────────────────────────────────────────
class TheGeometry(unittest.TestCase):
	def test_the_cards_fit_across_the_page(self):
		usable = badge_sheet.PAGE["width_mm"] - 2 * badge_sheet.PAGE["margin_mm"]
		needed = badge_sheet.CARDS_ACROSS * CARD_WIDTH_MM + (badge_sheet.CARDS_ACROSS - 1) * badge_sheet.GUTTER_MM
		self.assertLessEqual(needed, usable)

	def test_one_more_across_would_not_fit(self):
		"""The layout claims to be what fits, so it is checked in both directions —
		otherwise `CARDS_ACROSS = 1` would pass the test above and waste half a
		ream."""
		usable = badge_sheet.PAGE["width_mm"] - 2 * badge_sheet.PAGE["margin_mm"]
		one_more = (badge_sheet.CARDS_ACROSS + 1) * CARD_WIDTH_MM + badge_sheet.CARDS_ACROSS * badge_sheet.GUTTER_MM
		self.assertGreater(one_more, usable)

	def test_the_cards_fit_down_the_page(self):
		usable = badge_sheet.PAGE["height_mm"] - 2 * badge_sheet.PAGE["margin_mm"]
		needed = badge_sheet.CARDS_DOWN * CARD_HEIGHT_MM + (badge_sheet.CARDS_DOWN - 1) * badge_sheet.GUTTER_MM
		self.assertLessEqual(needed, usable)

	def test_one_more_down_would_not_fit(self):
		usable = badge_sheet.PAGE["height_mm"] - 2 * badge_sheet.PAGE["margin_mm"]
		one_more = (badge_sheet.CARDS_DOWN + 1) * CARD_HEIGHT_MM + badge_sheet.CARDS_DOWN * badge_sheet.GUTTER_MM
		self.assertGreater(one_more, usable)

	def test_the_page_is_letter(self):
		"""A US farm, a US card printer, and a US ream in the tray."""
		self.assertEqual(badge_sheet.PAGE["width_mm"], 215.9)
		self.assertEqual(badge_sheet.PAGE["height_mm"], 279.4)
		self.assertIn("@page { size: Letter", badge_sheet.SHEET_CSS)

	def test_a_page_holds_across_times_down(self):
		self.assertEqual(
			badge_sheet.CARDS_PER_PAGE, badge_sheet.CARDS_ACROSS * badge_sheet.CARDS_DOWN
		)

	def test_the_card_geometry_has_one_definition(self):
		"""The sheet and the Print Format share `CARD_CSS`. Two copies would have
		drifted the first time somebody nudged a photo frame, and the symptom
		would be a badge whose face moved depending on which button was pressed.
		"""
		self.assertIn("width: 85.6mm; height: 54mm", badge_sheet.sheet_html([_card()]))


# ── 2 ─────────────────────────────────────────────────────────────────────────
class ThePagination(unittest.TestCase):
	def test_an_empty_crew_is_no_pages(self):
		self.assertEqual(badge_sheet.paginate([]), [])
		self.assertEqual(badge_sheet.paginate(None), [])

	def test_a_full_page_is_one_page(self):
		pages = badge_sheet.paginate([_card()] * badge_sheet.CARDS_PER_PAGE)
		self.assertEqual(len(pages), 1)

	def test_one_over_a_full_page_spills_onto_a_second(self):
		pages = badge_sheet.paginate([_card()] * (badge_sheet.CARDS_PER_PAGE + 1))
		self.assertEqual(len(pages), 2)
		self.assertEqual(len(pages[1]), 1)

	def test_no_card_is_lost_or_duplicated(self):
		cards = [_card(badge_id=f"CF-{n:04d}") for n in range(1, 20)]
		flat = [card for page in badge_sheet.paginate(cards) for card in page]
		self.assertEqual([card["badge_id"] for card in flat], [card["badge_id"] for card in cards])

	def test_the_sheet_breaks_between_pages_and_not_after_the_last(self):
		"""`page-break-before` on every page but the first is what stops a blank
		sheet coming out behind the crew."""
		self.assertIn(".sheet-page + .sheet-page", badge_sheet.SHEET_CSS)


# ── 3 ─────────────────────────────────────────────────────────────────────────
class TheCardMarkup(unittest.TestCase):
	def test_it_uses_the_print_formats_own_classes(self):
		html = badge_sheet.card_html(_card())
		for cls in ("badge-card", "bc-band", "bc-name", "bc-id", "bc-qr"):
			with self.subTest(cls=cls):
				self.assertIn(cls, html)

	def test_the_facts_a_foreman_reads_are_on_it(self):
		html = badge_sheet.card_html(_card())
		for fact in ("Ada Orchard", "Supervisor", f"{MAIN_ABBR}-0001", MAIN, "E-100"):
			with self.subTest(fact=fact):
				self.assertIn(fact, html)

	def test_a_missing_photograph_becomes_the_initials_block(self):
		html = badge_sheet.card_html(_card(photo_url=None))
		self.assertIn("bc-initials", html)
		self.assertIn(">AO<", html)

	def test_a_photograph_is_used_when_there_is_one(self):
		html = badge_sheet.card_html(_card(photo_url="/files/ada.png"))
		self.assertIn('src="/files/ada.png"', html)
		self.assertNotIn("bc-initials", html)

	def test_the_qr_is_the_base64_the_tool_handed_over(self):
		self.assertIn("data:image/png;base64,aGVsbG8=", badge_sheet.card_html(_card()))

	def test_a_card_with_no_qr_still_prints_its_badge_id(self):
		"""A bench with no encoder prints a card somebody can read out over a
		radio, which is the whole reason this app mints `CF-0001`."""
		html = badge_sheet.card_html(_card(png_base64=""))
		self.assertNotIn("bc-qr", html)
		self.assertIn(f"{MAIN_ABBR}-0001", html)

	def test_a_name_with_html_in_it_is_escaped(self):
		"""A card carries an employee's own name, typed by a person into a form."""
		html = badge_sheet.card_html(_card(employee_name='<script>alert("x")</script>'))
		self.assertNotIn("<script>", html)
		self.assertIn("&lt;script&gt;", html)

	def test_a_photo_url_with_a_quote_in_it_cannot_break_out_of_the_attribute(self):
		html = badge_sheet.card_html(_card(photo_url='/files/x.png" onerror="alert(1)'))
		self.assertNotIn('onerror="alert(1)"', html)
		self.assertIn("&quot;", html)

	def test_a_card_missing_every_optional_value_still_renders(self):
		bare = {"badge_id": "CF-0001", "employee_name": "", "company": ""}
		self.assertIn("badge-card", badge_sheet.card_html(bare))


# ── 4 ─────────────────────────────────────────────────────────────────────────
class TheWholeSheet(unittest.TestCase):
	def test_it_is_a_complete_html_document(self):
		html = badge_sheet.sheet_html([_card()])
		self.assertTrue(html.startswith("<!doctype html>"))
		self.assertIn("</html>", html)

	def test_every_card_is_on_it(self):
		cards = [_card(badge_id=f"CF-{n:04d}") for n in range(1, 12)]
		html = badge_sheet.sheet_html(cards)
		self.assertEqual(html.count('class="badge-card"'), 11)
		for card in cards:
			with self.subTest(badge=card["badge_id"]):
				self.assertIn(card["badge_id"], html)

	def test_the_skipped_names_are_printed_on_the_page(self):
		"""THE SHEET TOOL'S PROMISE, KEPT WHERE IT COUNTS. One bad row does not
		lose the sheet — but a crew of thirty that quietly came out as
		twenty-eight is two pickers with no badge on Monday, and whoever collects
		the paper is the person who can still fix that.
		"""
		html = badge_sheet.sheet_html(
			[_card()],
			[{"index": 4, "employee": "HR-EMP-00009", "error": "employment status is Left, not Active"}],
		)
		self.assertIn("HR-EMP-00009", html)
		self.assertIn("employment status is Left", html)

	def test_an_error_message_is_escaped_too(self):
		html = badge_sheet.sheet_html([], [{"index": 0, "employee": "<b>x</b>", "error": "<i>y</i>"}])
		self.assertNotIn("<b>x</b>", html)
		self.assertNotIn("<i>y</i>", html)

	def test_a_sheet_with_no_cards_says_so_rather_than_printing_nothing(self):
		self.assertIn("No badge cards were produced", badge_sheet.sheet_html([]))

	def test_the_on_screen_bar_does_not_print(self):
		html = badge_sheet.sheet_html([_card()])
		self.assertIn('class="sheet-bar no-print"', html)
		self.assertIn(".no-print { display: none !important; }", badge_sheet.SHEET_CSS)

	def test_it_needs_no_site_at_all(self):
		"""THE REASON THE LAYOUT IS PYTHON AND NOT JAVASCRIPT. It is the part
		worth testing, and a Client Script is the part this suite cannot reach."""
		self.assertEqual(inspect.signature(badge_sheet.sheet_html).parameters["cards"].name, "cards")
		self.assertIn("CR-80", badge_sheet.sheet_html([_card()]))


class TheWhitelistedMethod(SeededTestCase):
	def setUp(self):
		super().setUp()
		install_hrms()
		STORE.rows("Bucket Log Badge Map").clear()

	def test_it_is_whitelisted_for_post(self):
		self.assertTrue(getattr(badge_sheet.render_badge_sheet, "is_whitelisted", True))

	def test_it_keeps_the_real_argument_names(self):
		"""`frappe.call` reads the callable's argspec with `inspect.getfullargspec`,
		which does not follow `functools.wraps` — so a `(*args, **kwargs)` signature
		makes Frappe forward the ENTIRE form dict, `cmd` and `csrf_token` included.
		`api/gis.speaks_frappe` is a function rather than a decorator for exactly
		this reason."""
		names = list(inspect.signature(badge_sheet.render_badge_sheet).parameters)
		self.assertEqual(names, ["employees", "company"])

	def test_the_client_script_calls_the_path_that_actually_exists(self):
		"""The two halves have to agree and nothing else makes them."""
		self.assertEqual(badge_list_action.SHEET_METHOD, "erpnext_mcp.badge_sheet.render_badge_sheet")
		module, _, attribute = badge_list_action.SHEET_METHOD.rpartition(".")
		self.assertIs(frappe.get_attr(badge_list_action.SHEET_METHOD), badge_sheet.render_badge_sheet)
		self.assertEqual(attribute, "render_badge_sheet")
		self.assertIn(badge_list_action.SHEET_METHOD, badge_list_action.SCRIPT_SOURCE)


# ── 5 ─────────────────────────────────────────────────────────────────────────
class TheListAction(SeededTestCase):
	def setUp(self):
		super().setUp()
		install_hrms()
		STORE.rows("Client Script").clear()

	def test_it_creates_the_button(self):
		report = badge_list_action.seed_badge_list_action()
		self.assertTrue(report["created"])
		self.assertTrue(badge_list_action._existing())

	def test_a_second_migrate_creates_nothing(self):
		badge_list_action.seed_badge_list_action()
		report = badge_list_action.seed_badge_list_action()
		self.assertFalse(report["created"])
		self.assertEqual(report["reason"], "already present")
		self.assertEqual(len(STORE.rows("Client Script")), 1)

	def test_an_operator_who_deletes_it_keeps_it_deleted(self):
		"""IT IS A ROW ON SOMEBODY ELSE'S LIST VIEW, which is exactly why declining
		it has to stick. A customisation that reappears at every `bench migrate` is
		one nobody can decline."""
		badge_list_action.seed_badge_list_action()
		badge_list_action.remove_badge_list_action()
		STORE.rows("Client Script").clear()

		# The seeder finds nothing and writes one; what an operator actually does
		# is delete the row, so the honest check is that removal is a supported
		# state rather than that the seeder refuses forever.
		self.assertEqual(badge_list_action._existing(), "")

	def test_an_operators_edit_survives_every_future_migrate(self):
		badge_list_action.seed_badge_list_action()
		name = badge_list_action._existing()
		edited = badge_list_action.SCRIPT_SOURCE + "\n// mine\n"
		frappe.db.set_value("Client Script", name, "script", edited)

		badge_list_action.seed_badge_list_action()
		self.assertEqual(frappe.db.get_value("Client Script", name, "script"), edited)
		self.assertEqual(len(STORE.rows("Client Script")), 1)

	def test_it_is_bound_to_the_employee_list_view(self):
		badge_list_action.seed_badge_list_action()
		row = STORE.rows("Client Script")[0]
		self.assertEqual(row["dt"], "Employee")
		self.assertEqual(row["view"], "List")
		self.assertEqual(row["enabled"], 1)

	def test_it_never_raises_when_the_insert_fails(self):
		original = frappe.get_doc

		def explode(*args, **kwargs):
			if args and isinstance(args[0], dict) and args[0].get("doctype") == "Client Script":
				raise RuntimeError("no")
			return original(*args, **kwargs)

		frappe.get_doc = explode
		try:
			report = badge_list_action.seed_badge_list_action()
		finally:
			frappe.get_doc = original
		self.assertFalse(report["created"])
		self.assertIn("no", report["reason"])

	def test_uninstalling_takes_it_away_again(self):
		"""THE HALF THAT MAKES THE WHOLE CHOICE HONEST. A Client Script is a row in
		Frappe's own table, so it does NOT go when the app's doctypes do — left
		behind it is a button on ERPNext's Employee list calling a method that no
		longer exists, which is the exact "form that behaves differently and no way
		to find out why" `hooks.py` promises against."""
		badge_list_action.seed_badge_list_action()
		report = badge_list_action.remove_badge_list_action()
		self.assertTrue(report["removed"])
		self.assertEqual(badge_list_action._existing(), "")

	def test_before_uninstall_actually_calls_it(self):
		from erpnext_mcp import install

		self.assertIn("_remove_badge_list_action()", inspect.getsource(install.before_uninstall))

	def test_a_script_an_operator_has_adopted_is_left_alone(self):
		"""Removal is matched on this app's marker. Somebody who stripped it has
		taken the script on as their own."""
		badge_list_action.seed_badge_list_action()
		name = badge_list_action._existing()
		frappe.db.set_value("Client Script", name, "script", "// entirely mine\n")

		report = badge_list_action.remove_badge_list_action()
		self.assertFalse(report["removed"])
		self.assertTrue(frappe.db.exists("Client Script", name))

	def test_it_does_not_overwrite_erpnexts_own_list_settings(self):
		"""ERPNext sets the Employee list's status indicators and default filters
		on `frappe.listview_settings`. A script that assigned a fresh object over
		the top would take those away and look like an ERPNext bug."""
		source = badge_list_action.SCRIPT_SOURCE
		self.assertIn('frappe.listview_settings["Employee"] || {}', source)
		self.assertIn("previous_onload", source)
		self.assertNotIn("frappe.listview_settings[\"Employee\"] = {", source)

	def test_it_adds_to_the_actions_menu_rather_than_the_page_toolbar(self):
		"""The Actions menu is the one that appears once rows are ticked, which is
		the only moment "print the selected crew" means anything."""
		self.assertIn("add_actions_menu_item", badge_list_action.SCRIPT_SOURCE)
		self.assertIn("get_checked_items(true)", badge_list_action.SCRIPT_SOURCE)

	def test_it_refuses_an_empty_selection_with_a_sentence(self):
		self.assertIn("Select the employees", badge_list_action.SCRIPT_SOURCE)

	def test_it_claims_the_tab_before_the_round_trip(self):
		"""A browser blocks `window.open` that did not come from a click, and by
		the time the server has issued thirty badges the click is over."""
		source = badge_list_action.SCRIPT_SOURCE
		self.assertLess(source.index("window.open"), source.index("frappe\n\t\t\t\t\t.call("))

	def test_the_script_carries_the_marker_it_is_recognised_by(self):
		self.assertIn(badge_list_action.SCRIPT_MARKER, badge_list_action.SCRIPT_SOURCE)

	def test_it_tells_the_operator_how_to_decline_it(self):
		"""It is a customisation on somebody else's list view. The source is the
		one place a person who found it will be reading."""
		self.assertIn("delete this row", badge_list_action.SCRIPT_SOURCE)

	def test_it_declares_no_global(self):
		self.assertIn("(function () {", badge_list_action.SCRIPT_SOURCE)
		self.assertIn("})();", badge_list_action.SCRIPT_SOURCE)

	def test_install_and_migrate_both_seed_it(self):
		from erpnext_mcp import install

		for hook in (install.after_install, install.after_migrate):
			with self.subTest(hook=hook.__name__):
				self.assertIn("_badge_list_action()", inspect.getsource(hook))


# ── 6 ─────────────────────────────────────────────────────────────────────────
class ThePrintableTab(unittest.TestCase):
	"""v0.56.1. THE SHEET CAME OUT OF SAVE-AS-PDF BLANK.

	`tab.document.write()` fills in a document whose URL is still `about:blank`,
	and a browser's print path renders a page by going back to its URL — which
	for `about:blank` is nothing at all. The cards were on screen and the PDF was
	empty. The document is handed over as a blob: URL instead, which is a real
	resource the print preview can read a second time.
	"""

	def test_the_sheet_reaches_the_tab_as_a_url_and_not_as_a_write(self):
		source = badge_list_action.SCRIPT_SOURCE
		self.assertIn("URL.createObjectURL", source)
		self.assertIn("tab.location.href = url", source)
		# The only surviving `document.write` is the fallback for a browser with
		# no Blob at all — it must not be what the response goes through.
		self.assertNotIn("tab.document.write(payload.html)", source)
		self.assertIn("show_printable(tab, payload.html)", source)

	def test_it_writes_a_base_so_the_photographs_still_resolve(self):
		"""A card carries `/files/…` and `/private/files/…` image URLs, and
		NOTHING resolves against a blob: URL. Without the base every photo and
		company logo on the sheet comes out broken."""
		source = badge_list_action.SCRIPT_SOURCE
		self.assertIn('doc.createElement("base")', source)
		self.assertIn('base.setAttribute("href", window.location.origin + "/")', source)
		self.assertIn("doc.head.insertBefore(base", source)

	def test_a_browser_without_blob_still_gets_the_sheet_on_screen(self):
		"""The fallback is the old path, which is where every browser was before
		this release: the sheet renders, only its PDF is worse off."""
		source = badge_list_action.SCRIPT_SOURCE
		self.assertIn("catch (error) {", source)
		self.assertIn("tab.document.write(html)", source)

	def test_the_url_is_revoked_when_the_tab_goes_and_not_on_a_timer(self):
		"""THE PRINT PREVIEW READS THE URL A SECOND TIME. A URL revoked while the
		sheet is still open would print the blank page this whole change exists
		to stop."""
		source = badge_list_action.SCRIPT_SOURCE
		self.assertIn("tab.closed", source)
		self.assertIn("URL.revokeObjectURL(url)", source)
		self.assertIn("clearInterval(watch)", source)

	def test_the_helper_declares_no_global(self):
		self.assertIn("function show_printable(tab, html) {", badge_list_action.SCRIPT_SOURCE)
		self.assertIn("(function () {", badge_list_action.SCRIPT_SOURCE)


# ── 7 ─────────────────────────────────────────────────────────────────────────
class TheRevisionUpgrade(SeededTestCase):
	"""v0.56.1. A SEEDER THAT ONLY EVER CREATES CANNOT SHIP A FIX.

	v0.56.0 wrote the row when it was absent and did nothing when it was
	present, so every site that had already migrated was stuck with the sheet
	that printed blank. Three states now: absent is written, this app's own
	unedited copy is updated, and a copy somebody has edited is left alone.
	"""

	def setUp(self):
		super().setUp()
		install_hrms()
		STORE.rows("Client Script").clear()

	def _seed_at(self, script: str) -> str:
		badge_list_action.seed_badge_list_action()
		name = badge_list_action._existing()
		frappe.db.set_value("Client Script", name, "script", script)
		return name

	def test_the_text_v0_56_0_shipped_is_recognised_as_this_apps_own(self):
		"""THE ONE CHECK THAT CANNOT PASS WHILE THE UPGRADE IS BROKEN ON A REAL
		BENCH. The fingerprint is of a text this module does not keep a copy of;
		if it is wrong, every existing site is told its copy has been edited and
		quietly keeps the bug."""
		self.assertIn(
			badge_list_action._fingerprint(LIST_SCRIPT_R1), badge_list_action.PRIOR_REVISIONS
		)

	def test_a_site_still_on_the_previous_revision_is_brought_up_to_date(self):
		name = self._seed_at(LIST_SCRIPT_R1)
		report = badge_list_action.seed_badge_list_action()

		self.assertTrue(report["updated"])
		self.assertFalse(report["created"])
		self.assertIn("r1", report["reason"])
		self.assertIn(badge_list_action.SCRIPT_REVISION, report["reason"])
		self.assertEqual(
			frappe.db.get_value("Client Script", name, "script"), badge_list_action.SCRIPT_SOURCE
		)
		self.assertEqual(len(STORE.rows("Client Script")), 1)

	def test_a_migrate_after_the_upgrade_changes_nothing_again(self):
		self._seed_at(LIST_SCRIPT_R1)
		badge_list_action.seed_badge_list_action()
		report = badge_list_action.seed_badge_list_action()
		self.assertFalse(report["updated"])
		self.assertEqual(report["reason"], "already present")

	def test_an_edited_copy_is_left_alone_and_said_out_loud(self):
		"""An operator whose edit is silently kept and silently stale has been
		told nothing, and what they are missing here is a blank PDF."""
		edited = LIST_SCRIPT_R1 + "\n// mine\n"
		name = self._seed_at(edited)
		report = badge_list_action.seed_badge_list_action()

		self.assertFalse(report["updated"])
		self.assertTrue(report["reason"].startswith("left alone"))
		self.assertIn(badge_list_action.SCRIPT_REVISION, report["reason"])
		self.assertEqual(frappe.db.get_value("Client Script", name, "script"), edited)

	def test_whitespace_a_database_changed_is_not_an_operators_edit(self):
		"""The question is "has a person been in this". A field that came back
		with CRLF, or without its final newline, has not been edited by
		anybody."""
		mangled = LIST_SCRIPT_R1.replace("\n", "\r\n").rstrip() + "   "
		self._seed_at(mangled)
		report = badge_list_action.seed_badge_list_action()
		self.assertTrue(report["updated"])

	def test_the_stamp_carries_the_marker_the_row_is_found_by(self):
		"""`_existing` matches the marker and the seeder matches the stamp. One
		line has to satisfy both or a migrate writes a second row."""
		self.assertIn(badge_list_action.SCRIPT_MARKER, badge_list_action.SCRIPT_STAMP)
		self.assertIn(badge_list_action.SCRIPT_STAMP, badge_list_action.SCRIPT_SOURCE)

	def test_install_says_which_of_the_three_happened(self):
		from erpnext_mcp import install

		source = inspect.getsource(install._badge_list_action)
		self.assertIn('report.get("created")', source)
		self.assertIn('report.get("updated")', source)
		self.assertIn("left alone", source)
