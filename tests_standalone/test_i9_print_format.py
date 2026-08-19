# SPDX-License-Identifier: MIT
"""The Desk's Print button on an I-9 — the seeded format and what it renders.

v0.47.1. `render_i9_pdf` fills the government's fillable PDF and is the right
answer when pypdf and the shipped template are both present. THIS IS THE OTHER
PATH: Frappe's Print button, which renders a Print Format through Jinja and
needs no PDF library at all. Without one it renders the STANDARD format — every
one of the doctype's eighty-four fields, two to a row, `naming_series` and
`pdf_col` included — which is a field dump that happens to contain an I-9.

FIVE CLAIMS.

1. `TheSeeder` — the format is created on migrate, once, and an operator's edits
   survive every migrate after that.
2. `TheFormatRecord` — custom rather than standard, Jinja, pointed at I-9 Form,
   and Letter.
3. `TheTemplateRenders` — it renders against a real record without raising, with
   `StrictUndefined`, so a field nobody has fails here rather than under
   somebody's finger.
4. `WhatThePageSays` — the values are on it, the citizenship attestation ticks
   one box, a receipt is flagged with its deadline, and the SSN is the last four.
5. `NoExternalResources` — no `<img>`, no `<link>`, no `http`. wkhtmltopdf
   fetches every external URL synchronously and one that 404s blanks the page;
   the signature captures are private Files it could not authenticate to anyway.

`jinja2` is not a declared dependency of this app — it arrives with Frappe — so
the rendering classes skip without it, exactly as `test_printing.py` does.
"""

import unittest

import frappe

from erpnext_mcp import i9_print_format
from erpnext_mcp.i9_print_format import FORMAT_NAME, PRINT_FORMAT

from .harness import STORE
from .test_i9 import I9TestCase

try:
	import jinja2

	HAS_JINJA = True
except Exception:  # pragma: no cover - a bench without jinja2
	jinja2 = None
	HAS_JINJA = False


class PrintFormatTestCase(I9TestCase):
	"""One complete I-9 on the fixture site, and the format seeded against it."""

	def setUp(self):
		super().setUp()
		STORE.rows(PRINT_FORMAT).clear()

	def a_complete_i9(self, employee="HR-EMP-00001") -> str:
		self._create_draft(employee=employee)
		self._submit_section_1(
			employee=employee,
			address_street="1420 Orchard Road",
			address_city="Yakima",
			address_state="WA",
			address_zip="98901",
			date_of_birth="1994-03-11",
			ssn_last_four="6789",
		)
		self._submit_section_2(employee=employee)
		return str(frappe.db.get_value("I-9 Form", {"employee": employee}, "name"))

	def render(self, name: str) -> str:
		"""The page Frappe's Print button would produce for one I-9.

		`autoescape=False` because that is what Frappe's own print environment
		does — every stock ERPNext format relies on it. `StrictUndefined` is the
		opposite trade and is deliberately stricter than Frappe's own
		`DebugUndefined`: a field nobody has raises here rather than printing the
		word "Undefined" onto a federal record.
		"""
		i9_print_format.seed_i9_print_format()
		html = STORE.get_raw(PRINT_FORMAT, FORMAT_NAME)["html"]
		environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
		return environment.from_string(html).render(doc=frappe.get_doc("I-9 Form", name))


# ── 1 ─────────────────────────────────────────────────────────────────────────
class TheSeeder(PrintFormatTestCase):
	def test_it_creates_the_format(self):
		report = i9_print_format.seed_i9_print_format()
		self.assertTrue(report["created"])
		self.assertEqual(report["name"], FORMAT_NAME)
		self.assertTrue(frappe.db.exists(PRINT_FORMAT, FORMAT_NAME))

	def test_a_second_migrate_creates_nothing(self):
		i9_print_format.seed_i9_print_format()
		report = i9_print_format.seed_i9_print_format()
		self.assertFalse(report["created"])
		self.assertEqual(report["reason"], "already present")

	def test_an_operators_edit_survives_every_future_migrate(self):
		"""The whole reason this is seeded rather than fixtured.

		A fixture is rewritten from the app's files on every `bench migrate`, so
		a margin somebody tuned for their own printer would disappear at the next
		upgrade without a word. `test_hooks.py` forbids the word `fixtures` by
		name; this is what that rule buys.
		"""
		i9_print_format.seed_i9_print_format()
		frappe.db.set_value(PRINT_FORMAT, FORMAT_NAME, "html", "<p>mine</p>")

		i9_print_format.seed_i9_print_format()
		self.assertEqual(frappe.db.get_value(PRINT_FORMAT, FORMAT_NAME, "html"), "<p>mine</p>")

	def test_it_never_raises_when_the_insert_itself_fails(self):
		"""A seeder that raised would take `bench migrate` down with it.

		The insert is the step that can genuinely fail on a real site — a Print
		Format doctype this Frappe version spells differently, a permission the
		migrate user has not got — so that is the step this breaks.
		"""
		original = frappe.get_doc

		def explode(*args, **kwargs):
			if args and isinstance(args[0], dict) and args[0].get("doctype") == PRINT_FORMAT:
				raise RuntimeError("no room at the inn")
			return original(*args, **kwargs)

		frappe.get_doc = explode
		try:
			report = i9_print_format.seed_i9_print_format()
		finally:
			frappe.get_doc = original

		self.assertFalse(report["created"])
		self.assertIn("no room at the inn", report["reason"])
		self.assertFalse(frappe.db.exists(PRINT_FORMAT, FORMAT_NAME))

	def test_the_doctype_asks_for_it_by_name(self):
		"""`default_print_format` is what makes the Print button pick this one.

		Read off the shipped JSON rather than restated, so a rename in one place
		and not the other fails here instead of leaving the button on Standard.
		"""
		import json
		import os

		path = os.path.join(
			os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
			"erpnext_mcp",
			"erpnext_mcp",
			"doctype",
			"i_9_form",
			"i_9_form.json",
		)
		with open(path, encoding="utf-8") as handle:
			declared = json.load(handle).get("default_print_format")
		self.assertEqual(declared, FORMAT_NAME)


# ── 2 ─────────────────────────────────────────────────────────────────────────
class TheFormatRecord(PrintFormatTestCase):
	def setUp(self):
		super().setUp()
		i9_print_format.seed_i9_print_format()
		self.row = STORE.get_raw(PRINT_FORMAT, FORMAT_NAME)

	def test_it_prints_the_i9_form_and_nothing_else(self):
		self.assertEqual(self.row["doc_type"], "I-9 Form")

	def test_it_is_a_custom_jinja_format(self):
		"""`standard = "No"` is what stops `bench migrate` overwriting an edit."""
		self.assertEqual(self.row["standard"], "No")
		self.assertEqual(int(self.row["custom_format"]), 1)
		self.assertEqual(self.row["print_format_type"], "Jinja")

	def test_it_is_letter_because_form_i9_is_a_us_federal_form(self):
		self.assertEqual(self.row["page_size"], "Letter")

	def test_it_belongs_to_this_apps_module_so_it_goes_when_the_app_goes(self):
		self.assertEqual(self.row["module"], "ERPNext MCP")


# ── 3 ─────────────────────────────────────────────────────────────────────────
@unittest.skipUnless(HAS_JINJA, "needs jinja2, which arrives with Frappe on a real bench")
class TheTemplateRenders(PrintFormatTestCase):
	def test_a_complete_i9_renders_without_raising(self):
		page = self.render(self.a_complete_i9())
		self.assertIn("Employment Eligibility Verification", page)
		self.assertIn("Form I-9", page)

	def test_a_draft_with_almost_nothing_on_it_still_renders(self):
		"""StrictUndefined and a record where most columns are None. A print
		format that only works on a finished record is one that fails on the
		half-finished one somebody is trying to look at."""
		self._create_draft()
		name = str(frappe.db.get_value("I-9 Form", {"employee": "HR-EMP-00001"}, "name"))
		page = self.render(name)
		self.assertIn("Section 1. Employee Information", page)
		self.assertIn("No reverification or rehire has been recorded", page)

	def test_a_reverification_history_renders_as_a_table(self):
		name = self.a_complete_i9()
		doc = frappe.get_doc("I-9 Form", name)
		doc.append(
			"reverifications",
			{
				"reverification_date": "2027-05-01",
				"reason": "Work Authorization Expired",
				"document_title": "Employment Authorization Document (Form I-766)",
				"document_number": "SRC0001",
				"document_expiry": "2029-05-01",
				"verifier_name": "Ana Ramos",
			},
		)
		doc.flags.ignore_permissions = True
		doc.save()

		page = self.render(name)
		self.assertIn("2027-05-01", page)
		self.assertIn("SRC0001", page)
		self.assertNotIn("No reverification or rehire", page)


# ── 4 ─────────────────────────────────────────────────────────────────────────
@unittest.skipUnless(HAS_JINJA, "needs jinja2, which arrives with Frappe on a real bench")
class WhatThePageSays(PrintFormatTestCase):
	def test_it_says_what_it_is_before_it_says_anything_else(self):
		"""The thing it most resembles is a federal form and it is not one."""
		page = self.render(self.a_complete_i9())
		self.assertIn("WORKING COPY", page)
		self.assertIn("render_i9_pdf", page)

	def test_the_collected_values_are_on_it(self):
		page = self.render(self.a_complete_i9())
		for value in ("Ada", "Orchard", "Yakima", "98901", "U.S. Passport", "Tim Polehn"):
			self.assertIn(value, page)

	def test_the_ssn_is_the_last_four_behind_a_mask(self):
		page = self.render(self.a_complete_i9())
		self.assertIn("XXX-XX-6789", page)

	def test_one_citizenship_box_is_ticked_and_only_one(self):
		page = self.render(self.a_complete_i9())
		self.assertIn("[X]\n          1. A citizen of the United States", page.replace("\r", ""))
		self.assertEqual(page.count("[X]"), 1)

	def test_a_receipt_is_flagged_with_the_deadline_it_carries(self):
		name = self.a_complete_i9()
		frappe.db.set_value("I-9 Form", name, {"receipt_pending": 1, "receipt_expires_on": "2026-06-30"})
		page = self.render(name)
		self.assertIn("RECEIPT PENDING", page)
		self.assertIn("8 CFR 274a.2(b)(1)(vi)", page)
		self.assertIn("2026-06-30", page)

	def test_it_reports_whether_a_signed_copy_was_ever_filed(self):
		"""A reader who cannot see that cannot tell a complete I-9 file from an
		incomplete one, which is the question an inspection opens with."""
		name = self.a_complete_i9()
		self.assertIn("not filed", self.render(name))

		frappe.db.set_value(
			"I-9 Form",
			name,
			{"signed_pdf": "/private/files/signed.pdf", "signed_pdf_on": "2026-04-03 10:00:00"},
		)
		self.assertIn("2026-04-03", self.render(name))

	def test_the_page_carries_no_signature_and_says_where_they_are(self):
		page = self.render(self.a_complete_i9())
		self.assertIn("This page carries no signature", page)


# ── 5 ─────────────────────────────────────────────────────────────────────────
class NoExternalResources(unittest.TestCase):
	"""wkhtmltopdf fetches every external URL synchronously. One 404 blanks it."""

	def setUp(self):
		self.html = i9_print_format.print_format_fields()["html"]

	def test_there_is_no_image_anywhere_in_the_template(self):
		self.assertNotIn("<img", self.html.lower())

	def test_there_is_no_stylesheet_or_webfont_link(self):
		lowered = self.html.lower()
		self.assertNotIn("<link", lowered)
		self.assertNotIn("@import", lowered)
		self.assertNotIn("@font-face", lowered)

	def test_nothing_in_it_reaches_off_the_box(self):
		lowered = self.html.lower()
		self.assertNotIn("http://", lowered)
		self.assertNotIn("https://", lowered)
		self.assertNotIn("//fonts.", lowered)

	def test_the_signature_captures_are_reported_rather_than_embedded(self):
		"""They are private Files the renderer could not authenticate to, and the
		fact is what an auditor needs rather than the picture."""
		self.assertIn("Signature capture on file", self.html)

	def test_the_header_note_is_substituted_rather_than_left_as_a_token(self):
		self.assertNotIn("{{ header_note }}", self.html)
		self.assertIn(i9_print_format.HEADER_NOTE, self.html)


if __name__ == "__main__":
	unittest.main()
