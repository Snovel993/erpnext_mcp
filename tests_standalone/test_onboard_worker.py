# SPDX-License-Identifier: MIT
"""The Onboard Worker landing page. v0.83.0.

Every step this page links to already worked. What did not exist was anything in
the Desk saying what order to do them in, so "somebody starts Monday, what do I
do" was answered by institutional memory.

FIVE CLAIMS.

1. `ThePageIsBuilt` — it is created, it carries the app's module, and it lands at
   a route Frappe derives from its name.
2. `ThePageHasSomethingOnIt` — the v0.16.0 BUG, which is the reason this file
   exists at all: a Workspace renders ONLY what its `content` block list names,
   and that release wrote the child rows and left `content` empty. Every shortcut
   row has a block and every block names a row that exists.
3. `TheOrderIsTheContent` — hire, then badge, then enrol. The buttons all existed
   before this release; the sequence is what shipped, so the sequence is what is
   asserted.
4. `NothingIsRebuiltOverSomebody` — a page somebody has arranged is never
   touched, which is the whole safety property.
5. `ItDegradesRatherThanExploding` — a site with no Workspace doctype, or no HR,
   gets a reported note and no exception. This runs inside `bench migrate`.
"""

import json

import frappe

from erpnext_mcp import onboard_worker
from erpnext_mcp.dashboard import MODULE, WORKSPACE

from .fixtures import SeededTestCase, install_hrms
from .harness import STORE


class OnboardTestCase(SeededTestCase):
	def setUp(self):
		super().setUp()
		install_hrms()
		STORE.rows(WORKSPACE).clear()

	def build(self) -> dict:
		return onboard_worker.install_onboard_worker()

	def page(self) -> dict:
		return dict(STORE.get_raw(WORKSPACE, onboard_worker.WORKSPACE_NAME) or {})

	def content(self) -> list:
		raw = self.page().get("content")
		return json.loads(raw) if raw else []


# ── 1 ───────────────────────────────────────────────────────────────────────
class ThePageIsBuilt(OnboardTestCase):
	def test_it_creates_the_page(self):
		report = self.build()
		self.assertTrue(report["created"])
		self.assertTrue(frappe.db.exists(WORKSPACE, onboard_worker.WORKSPACE_NAME))

	def test_it_belongs_to_this_app(self):
		"""`remove_onboard_worker` refuses to delete a page that has been moved to
		another module, so the module is not decoration — it is the ownership
		test an uninstall depends on."""
		self.build()
		self.assertEqual(self.page().get("module"), MODULE)

	def test_it_is_public_rather_than_somebodys_private_page(self):
		self.build()
		self.assertEqual(self.page().get("public"), 1)
		self.assertEqual(self.page().get("is_hidden"), 0)

	def test_a_second_migrate_does_not_double_the_rows(self):
		self.build()
		self.build()
		self.assertEqual(len(STORE.rows(WORKSPACE)), 1)

	def test_it_sorts_after_the_dispatch_board(self):
		"""Dispatch is a daily page; this is one somebody opens when a new person
		starts. A landing page nobody opens daily should not sit above one they
		do."""
		self.build()
		self.assertGreater(float(self.page().get("sequence_id") or 0), 20.0)


# ── 2 ───────────────────────────────────────────────────────────────────────
class ThePageHasSomethingOnIt(OnboardTestCase):
	"""THE v0.16.0 BUG, WRITTEN AS A TEST. That release created a Workspace,
	wrote its child rows, set `content` to `[]`, and shipped a page with a title
	and nothing else. Nothing noticed until somebody opened it."""

	def test_the_content_block_list_is_not_empty(self):
		self.build()
		self.assertTrue(self.content())

	def test_every_shortcut_row_has_a_block_that_renders_it(self):
		"""A shortcut row with no block is invisible: the data is there and the
		page shows nothing, which is indistinguishable from a broken install."""
		self.build()
		rendered = {
			entry["data"]["shortcut_name"] for entry in self.content() if entry.get("type") == "shortcut"
		}
		for row in self.page().get("shortcuts") or []:
			with self.subTest(shortcut=row["label"]):
				self.assertIn(row["label"], rendered)

	def test_every_block_names_a_row_that_exists(self):
		"""The other direction, and the one that renders as an error rather than
		as an absence."""
		self.build()
		labels = {row["label"] for row in self.page().get("shortcuts") or []}
		for entry in self.content():
			if entry.get("type") != "shortcut":
				continue
			with self.subTest(block=entry["id"]):
				self.assertIn(entry["data"]["shortcut_name"], labels)

	def test_the_link_cards_are_rendered_too(self):
		self.build()
		cards = {entry["data"]["card_name"] for entry in self.content() if entry.get("type") == "card"}
		self.assertTrue(cards)

	def test_a_link_card_names_only_doctypes_this_site_has(self):
		"""A card of dead links is worse than no card: it reads as a feature the
		operator has not been given access to."""
		self.build()
		for row in self.page().get("links") or []:
			if row.get("type") == "Card Break":
				continue
			with self.subTest(link=row["link_to"]):
				self.assertTrue(frappe.db.exists("DocType", row["link_to"]))


# ── 3 ───────────────────────────────────────────────────────────────────────
class TheOrderIsTheContent(OnboardTestCase):
	"""THE SEQUENCE IS WHAT SHIPPED. Every button this page points at already
	existed; a test that only checked they are present would pass against the
	state this release was written to fix."""

	def labels(self) -> list:
		return [row["label"] for row in self.page().get("shortcuts") or []]

	def test_hiring_comes_first(self):
		self.build()
		self.assertEqual(self.labels()[0], "Hire Somebody")

	def test_badging_comes_before_the_phone(self):
		self.build()
		labels = self.labels()
		self.assertLess(labels.index("Badge & Print"), labels.index("Phone Enrolment"))

	def test_hiring_is_the_quick_add_and_not_a_list(self):
		"""Making somebody open a list to find New is the friction that ends with
		the step being skipped."""
		self.build()
		row = next(r for r in self.page()["shortcuts"] if r["label"] == "Hire Somebody")
		self.assertEqual(row.get("doc_view"), "New")

	def test_badging_lands_on_the_employee_list(self):
		"""A shortcut cannot press a button; it can put somebody in front of one.
		The badge sheet is an Actions-menu entry on this list."""
		self.build()
		row = next(r for r in self.page()["shortcuts"] if r["label"] == "Badge & Print")
		self.assertEqual(row["link_to"], "Employee")
		self.assertEqual(row.get("doc_view"), "List")

	def test_the_sequence_is_written_out_in_words(self):
		"""A row of four shortcuts reads as four equal choices. The sentence is
		what makes it a sequence."""
		self.build()
		text = " ".join(
			entry["data"].get("text", "") for entry in self.content() if entry.get("type") == "paragraph"
		)
		self.assertIn("Hire them", text)


# ── 4 ───────────────────────────────────────────────────────────────────────
class NothingIsRebuiltOverSomebody(OnboardTestCase):
	def test_an_arranged_page_is_left_exactly_as_it_is(self):
		self.build()
		mine = json.dumps([{"id": "mine", "type": "header", "data": {"text": "Mine", "col": 12}}])
		frappe.db.set_value(WORKSPACE, onboard_worker.WORKSPACE_NAME, "content", mine)

		report = self.build()
		self.assertTrue(report["existed"])
		self.assertFalse(report["created"])
		self.assertEqual(self.page()["content"], mine)

	def test_a_page_left_empty_by_a_bad_release_is_repaired(self):
		"""The other half. A seeder that never repairs cannot ship a fix, which is
		exactly how v0.16.0's blank board stayed blank on every migrated site."""
		self.build()
		frappe.db.set_value(WORKSPACE, onboard_worker.WORKSPACE_NAME, "content", "[]")

		report = self.build()
		self.assertTrue(report["filled"])
		self.assertTrue(self.content())

	def test_repairing_does_not_double_the_child_rows(self):
		"""The bug a repair path invites: clear `content`, rebuild, and end up with
		two of every shortcut."""
		self.build()
		before = len(self.page().get("shortcuts") or [])
		frappe.db.set_value(WORKSPACE, onboard_worker.WORKSPACE_NAME, "content", "[]")
		self.build()
		self.assertEqual(len(self.page().get("shortcuts") or []), before)


# ── 5 ───────────────────────────────────────────────────────────────────────
class ItDegradesRatherThanExploding(OnboardTestCase):
	"""THIS RUNS INSIDE `bench migrate`. Anything that raises here takes a real
	site's migration down, which is the failure `install.py`'s whole docstring is
	about."""

	def test_a_site_with_no_workspace_doctype_gets_a_note(self):
		original = onboard_worker.compat.doctype_exists

		def absent(doctype):
			return False if doctype == WORKSPACE else original(doctype)

		onboard_worker.compat.doctype_exists = absent
		try:
			report = self.build()
		finally:
			onboard_worker.compat.doctype_exists = original
		self.assertFalse(report["created"])
		self.assertIn("Workspace", report["note"])

	def test_a_site_with_no_hr_gets_a_note_rather_than_a_page_of_dead_links(self):
		original = onboard_worker.compat.doctype_exists

		def absent(doctype):
			return False if doctype == "Employee" else original(doctype)

		onboard_worker.compat.doctype_exists = absent
		try:
			report = self.build()
		finally:
			onboard_worker.compat.doctype_exists = original
		self.assertFalse(report["created"])
		self.assertIn("HR is not installed", report["note"])

	def test_a_failure_is_reported_rather_than_raised(self):
		original = frappe.new_doc

		def explode(doctype, *args, **kwargs):
			if doctype == WORKSPACE:
				raise RuntimeError("no")
			return original(doctype, *args, **kwargs)

		frappe.new_doc = explode
		try:
			report = self.build()
		finally:
			frappe.new_doc = original
		self.assertFalse(report["created"])
		self.assertTrue(report["failed"])
		self.assertIn("RuntimeError", report["failed"][0]["reason"])

	def test_available_says_no_when_hr_is_absent(self):
		original = onboard_worker.compat.doctype_exists
		onboard_worker.compat.doctype_exists = lambda doctype: doctype != "Employee"
		try:
			self.assertFalse(onboard_worker.available())
		finally:
			onboard_worker.compat.doctype_exists = original


# ── 5b ──────────────────────────────────────────────────────────────────────
class UninstallTakesItAway(OnboardTestCase):
	def test_it_removes_the_page(self):
		self.build()
		report = onboard_worker.remove_onboard_worker()
		self.assertTrue(report["removed"])
		self.assertFalse(frappe.db.exists(WORKSPACE, onboard_worker.WORKSPACE_NAME))

	def test_removing_what_is_not_there_is_not_an_error(self):
		report = onboard_worker.remove_onboard_worker()
		self.assertFalse(report["removed"])
		self.assertEqual(report["reason"], "not present")

	def test_a_page_moved_to_another_module_is_not_this_apps_to_delete(self):
		"""The ownership test. Somebody who adopted this page into their own
		module has taken it, and an uninstall must not take it back."""
		self.build()
		frappe.db.set_value(WORKSPACE, onboard_worker.WORKSPACE_NAME, "module", "Accounts")

		report = onboard_worker.remove_onboard_worker()
		self.assertFalse(report["removed"])
		self.assertTrue(report["reason"].startswith("left alone"))
		self.assertTrue(frappe.db.exists(WORKSPACE, onboard_worker.WORKSPACE_NAME))

	def test_it_never_raises(self):
		"""An uninstall that died here would leave the app half-removed."""
		self.build()
		original = frappe.delete_doc
		frappe.delete_doc = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no"))
		try:
			report = onboard_worker.remove_onboard_worker()
		finally:
			frappe.delete_doc = original
		self.assertFalse(report["removed"])
		self.assertIn("RuntimeError", report["reason"])
