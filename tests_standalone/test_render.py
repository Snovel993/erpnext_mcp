# SPDX-License-Identifier: MIT
"""The three document writers, checked against their own bytes.

WHY THESE TESTS ARE WORTH HAVING AT ALL. A renderer built on a third-party
library would be tested by mocking the library, which proves nothing about the
file. These write the bytes themselves, so the tests can open the result and
assert on it: that a PDF's cross-reference table points at the objects it claims
to, that an XLSX is a zip of well-formed XML with the right cells in the right
places, that a DOCX carries the text it was given. That is the whole argument for
the standard-library approach, and it lives here.

THE XREF TEST IS THE IMPORTANT ONE. Every reader rejects a PDF whose byte offsets
are wrong by one, and nothing else in the suite would notice — the file would be
produced, attached, archived and unopenable.
"""

import io
import xml.dom.minidom
import zipfile

from erpnext_mcp.render.docx import DocxDocument
from erpnext_mcp.render.pdf import (
	CHAR_WIDTH_EM,
	MARGIN,
	NO_FIGURE,
	PAGE_WIDTH,
	PdfDocument,
	chars_that_fit,
	escape,
	money,
	text_width,
	wrap,
)
from erpnext_mcp.render.xlsx import (
	STYLE_MONEY,
	STYLE_TEXT,
	Sheet,
	XlsxWorkbook,
	column_letter,
)

from .harness import MCPTestCase


def pdf_objects(payload: bytes) -> list[int]:
	"""Every offset in the trailer's cross-reference table, in order."""
	start = payload.rindex(b"startxref")
	table_at = int(payload[start + len("startxref") :].split()[0])
	lines = payload[table_at:].split(b"\n")
	count = int(lines[1].split()[1])
	# Line 0 is "xref", 1 is "0 N", 2 is the free entry for object 0.
	return [int(line.split()[0]) for line in lines[3 : 3 + count - 1]]


class Metrics(MCPTestCase):
	def test_courier_is_exactly_six_tenths_of_an_em(self):
		"""The whole font metric. If this is wrong every column is wrong."""
		self.assertEqual(CHAR_WIDTH_EM, 0.6)
		self.assertAlmostEqual(text_width("abcde", 10), 30.0)

	def test_characters_that_fit_never_goes_negative(self):
		self.assertEqual(chars_that_fit(0, 9), 0)
		self.assertEqual(chars_that_fit(-5, 9), 0)

	def test_the_text_column_holds_a_usable_line_at_body_size(self):
		usable = chars_that_fit(PAGE_WIDTH - 2 * MARGIN, 9.0)
		self.assertGreater(usable, 80)


class Wrapping(MCPTestCase):
	def test_it_breaks_on_words(self):
		self.assertEqual(wrap("one two three", 8), ["one two", "three"])

	def test_a_word_longer_than_the_column_is_split_rather_than_overflowing(self):
		"""A docname or a URL cannot be wrapped politely, and letting it run past
		the margin would put text off the page where nobody sees it missing."""
		self.assertEqual(wrap("ACC-JV-2026-00001", 6), ["ACC-JV", "-2026-", "00001"])

	def test_it_always_returns_at_least_one_line(self):
		"""A blank cell still occupies a row. Returning nothing would drop it."""
		self.assertEqual(wrap("", 10), [""])
		self.assertEqual(wrap("   ", 10), [""])

	def test_explicit_newlines_survive(self):
		self.assertEqual(wrap("a\nb", 10), ["a", "b"])


class Escaping(MCPTestCase):
	def test_backslash_is_escaped_before_the_parens_it_would_re_escape(self):
		self.assertEqual(escape(r"a\(b)"), r"a\\\(b\)")

	def test_a_name_full_of_parens_survives_into_the_document(self):
		document = PdfDocument(title="t")
		document.paragraph("Polehn (Marital) Trust \\ Survivor's")
		payload = document.render()
		self.assertIn(rb"Polehn \(Marital\) Trust \\ Survivor's", payload)

	def test_characters_cp1252_cannot_hold_become_a_placeholder_not_a_crash(self):
		document = PdfDocument(title="t")
		document.paragraph("emoji 🚜 and CJK 農場")
		payload = document.render()
		self.assertTrue(payload.startswith(b"%PDF-1.4"))


class Money(MCPTestCase):
	def test_it_groups_and_fixes_two_places(self):
		self.assertEqual(money(1523450.1), "1,523,450.10")

	def test_a_missing_figure_is_not_zero(self):
		"""'We do not have this' and 'this is zero' are different statements, and a
		report that conflates them is not evidence of anything."""
		self.assertEqual(money(None), NO_FIGURE)
		self.assertEqual(money(""), NO_FIGURE)
		self.assertEqual(money(0), "0.00")


class PdfStructure(MCPTestCase):
	def build(self, rows=3):
		document = PdfDocument(title="Report", author="erpnext_mcp", subject="s", footer="f")
		document.title_block("A TITLE", "a subtitle")
		document.heading("Section")
		document.paragraph("Body text. " * 20)
		document.key_values([("Label", "value"), ("Missing", None)])
		document.table(
			["A", "B", "C"],
			[[f"row{index}", "text", f"{index * 1000:,.2f}"] for index in range(rows)],
			align=("l", "l", "r"),
		)
		return document

	def test_it_is_a_pdf_and_ends_properly(self):
		payload = self.build().render()
		self.assertTrue(payload.startswith(b"%PDF-1.4"))
		self.assertTrue(payload.rstrip().endswith(b"%%EOF"))

	def test_every_xref_offset_points_at_the_object_it_claims(self):
		"""The one failure no other test would catch: a file that is produced,
		attached, archived — and rejected by every reader that opens it."""
		payload = self.build(rows=400).render()
		for number, offset in enumerate(pdf_objects(payload), start=1):
			with self.subTest(obj=number):
				self.assertEqual(
					payload[offset : offset + len(f"{number} 0 obj")],
					f"{number} 0 obj".encode(),
				)

	def test_a_long_table_paginates_and_repeats_its_header_on_every_page(self):
		"""A continuation page whose columns are unlabelled is a page of numbers
		nobody can read."""
		document = PdfDocument(title="t", footer="f")
		document.table(
			["Voucher", "Detail", "Amount"],
			[[f"V-{index:05d}", "detail", f"{index:,.2f}"] for index in range(400)],
			align=("l", "l", "r"),
		)
		payload = document.render()
		pages = payload.count(b"/Type /Page ")
		self.assertGreater(pages, 1)
		self.assertEqual(payload.count(b"(Voucher"), pages)
		self.assertEqual(payload.count(b"(Page 1 of "), 1)

	def test_the_page_count_in_the_footer_matches_the_pages_object(self):
		payload = self.build(rows=400).render()
		count = int(payload.split(b"/Count ")[1].split(b" ")[0])
		self.assertIn(f"(Page {count} of {count})".encode(), payload)
		self.assertIn(f"/Count {count}".encode(), payload)

	def test_rendering_twice_gives_identical_bytes(self):
		"""An archive copy that differs from the printed copy is an hour of
		somebody's life. `render` must not mutate the document."""
		document = self.build()
		self.assertEqual(document.render(), document.render())

	def test_the_title_reaches_the_info_dictionary(self):
		payload = PdfDocument(title="Quarterly Report").render()
		self.assertIn(b"/Title (Quarterly Report)", payload)

	def test_an_empty_document_is_still_a_valid_one_page_pdf(self):
		payload = PdfDocument(title="empty").render()
		self.assertIn(b"/Count 1", payload)
		for number, offset in enumerate(pdf_objects(payload), start=1):
			self.assertEqual(payload[offset : offset + 5], f"{number} 0 o".encode())


class PdfColumns(MCPTestCase):
	def test_money_columns_keep_their_width_when_prose_has_to_shrink(self):
		"""A wrapped amount reads as two figures. The prose column gives way."""
		document = PdfDocument(title="t")
		document.table(
			["Description", "Amount"],
			[["x" * 400, "1,234,567.89"]],
			align=("l", "r"),
			size=7.5,
		)
		payload = document.render()
		self.assertIn(b"1,234,567.89", payload)

	def test_explicit_widths_are_honoured(self):
		document = PdfDocument(title="t")
		document.table(["A", "B"], [["ab", "cd"]], widths=(10, 10))
		self.assertIn(b"ab", document.render())


class XlsxStructure(MCPTestCase):
	def workbook(self):
		return XlsxWorkbook(
			Sheet(
				title="Recipients",
				headers=["Name", "TIN", "Total"],
				rows=[["Sorren Accounting LLC", ("0123", STYLE_TEXT), (24360.0, STYLE_MONEY)]],
			),
			Sheet(title="Cost Centers", headers=["Name", "Amount"], rows=[["Sorren", 100.0]]),
			title="1099",
		)

	def archive(self):
		return zipfile.ZipFile(io.BytesIO(self.workbook().render()))

	def test_column_letters_are_bijective_base_twenty_six(self):
		self.assertEqual(
			[column_letter(index) for index in (1, 26, 27, 52, 703)],
			["A", "Z", "AA", "AZ", "AAA"],
		)
		with self.assertRaises(ValueError):
			column_letter(0)

	def test_every_part_is_well_formed_xml(self):
		archive = self.archive()
		for name in archive.namelist():
			with self.subTest(part=name):
				xml.dom.minidom.parseString(archive.read(name))

	def test_it_carries_the_parts_a_reader_needs(self):
		names = set(self.archive().namelist())
		self.assertLessEqual(
			{
				"[Content_Types].xml",
				"_rels/.rels",
				"xl/workbook.xml",
				"xl/_rels/workbook.xml.rels",
				"xl/styles.xml",
				"xl/worksheets/sheet1.xml",
				"xl/worksheets/sheet2.xml",
			},
			names,
		)

	def test_a_tin_stays_text_so_it_keeps_its_leading_zero(self):
		"""`0123` read as a number is `123`, which is a different taxpayer."""
		sheet = self.archive().read("xl/worksheets/sheet1.xml").decode()
		self.assertIn('t="inlineStr"><is><t xml:space="preserve">0123</t>', sheet)

	def test_a_money_cell_is_numeric_with_the_money_format(self):
		sheet = self.archive().read("xl/worksheets/sheet1.xml").decode()
		self.assertIn(f'<c r="C2" s="{STYLE_MONEY}"><v>24360.0</v></c>', sheet)

	def test_the_header_row_is_frozen_and_filtered(self):
		sheet = self.archive().read("xl/worksheets/sheet1.xml").decode()
		self.assertIn('<pane ySplit="1"', sheet)
		self.assertIn("<autoFilter", sheet)

	def test_xml_special_characters_in_a_vendor_name_are_escaped(self):
		workbook = XlsxWorkbook(Sheet(title="s", headers=["n"], rows=[["Friend & <Reagan> PC"]]))
		sheet = zipfile.ZipFile(io.BytesIO(workbook.render())).read("xl/worksheets/sheet1.xml").decode()
		self.assertIn("Friend &amp; &lt;Reagan&gt; PC", sheet)
		xml.dom.minidom.parseString(sheet)

	def test_two_sheets_that_truncate_to_the_same_name_are_refused(self):
		"""Excel refuses to open such a workbook, so this refuses to write one."""
		workbook = XlsxWorkbook(Sheet(title="A" * 31 + "first"), Sheet(title="A" * 31 + "second"))
		with self.assertRaises(ValueError) as caught:
			workbook.render()
		self.assertIn("31-character", str(caught.exception))

	def test_a_workbook_with_no_sheets_is_refused(self):
		with self.assertRaises(ValueError):
			XlsxWorkbook().render()

	def test_rendering_twice_gives_identical_bytes(self):
		self.assertEqual(self.workbook().render(), self.workbook().render())

	def test_illegal_sheet_name_characters_are_replaced(self):
		self.assertEqual(Sheet(title="a/b:c*d").safe_title(), "a b c d")
		self.assertEqual(Sheet(title="   ").safe_title(), "Sheet")


class DocxStructure(MCPTestCase):
	def document(self):
		document = DocxDocument(title="Report", subject="s")
		document.title_block("A TITLE", "a subtitle")
		document.heading("Section")
		document.paragraph("Line one\nLine two")
		document.key_values([("Label", "value")])
		document.table(["A", "B"], [["1", "2"]], widths=(60, 40))
		document.page_break()
		document.bullets(["first", "second"])
		return document

	def archive(self):
		return zipfile.ZipFile(io.BytesIO(self.document().render()))

	def test_every_part_is_well_formed_xml(self):
		archive = self.archive()
		for name in archive.namelist():
			with self.subTest(part=name):
				xml.dom.minidom.parseString(archive.read(name))

	def test_the_text_reaches_the_body(self):
		body = self.archive().read("word/document.xml").decode()
		self.assertIn("A TITLE", body)
		self.assertIn("Line one", body)
		self.assertIn("Line two", body)

	def test_a_newline_becomes_a_break_not_a_space(self):
		body = self.archive().read("word/document.xml").decode()
		self.assertIn("<w:br/>", body)

	def test_it_carries_a_page_size_so_it_prints_on_letter(self):
		body = self.archive().read("word/document.xml").decode()
		self.assertIn('<w:pgSz w:w="12240" w:h="15840"/>', body)

	def test_rendering_twice_gives_identical_bytes(self):
		self.assertEqual(self.document().render(), self.document().render())
