# SPDX-License-Identifier: MIT
"""A DOCX writer, for the one caller who asks for `output_format="docx"`.

PDF IS THE PRIMARY FORMAT AND THIS IS NOT IT. A `.docx` handed to somebody whose
machine has no Word is a file they cannot open — which is exactly what happened
here on 2026-07-29, and is why the quarterly report defaults to PDF and says so.
This exists because a report that will be *edited* before it is signed has to
arrive in something editable, and refusing a documented argument value is worse
than supporting it properly.

Same shape as `xlsx.py`: WordprocessingML is a zip of XML, `zipfile` is stdlib,
members carry a fixed timestamp so the same inputs give the same bytes.

Supports headings, body paragraphs, and tables with a bold header row. No
images, no headers/footers, no styles beyond the three declared in `styles.xml` —
the same restraint as the PDF writer, for the same reason.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: Twips (twentieths of a point) — Word's unit for everything on the page.
#: 12240 x 15840 is US Letter; 1080 is three quarters of an inch of margin.
PAGE_WIDTH_TWIPS = 12240
PAGE_HEIGHT_TWIPS = 15840
MARGIN_TWIPS = 1080
CONTENT_WIDTH_TWIPS = PAGE_WIDTH_TWIPS - 2 * MARGIN_TWIPS


def escape(text) -> str:
	out = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
	return "".join(
		char for char in out if char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD
	)


class DocxDocument:
	"""A flowing Word document. Emit in order, then `render()`."""

	def __init__(self, title: str = "", author: str = "erpnext_mcp", subject: str = ""):
		self.title = title
		self.author = author
		self.subject = subject
		self._body: list[str] = []

	# ── blocks ──────────────────────────────────────────────────────────────
	def title_block(self, title: str, *subtitles: str) -> None:
		self._paragraph(title, style="McpTitle", align="center")
		for subtitle in subtitles:
			if subtitle:
				self._paragraph(subtitle, align="center")
		self.spacer()

	def heading(self, text: str) -> None:
		self._paragraph(text, style="McpHeading")

	def subheading(self, text: str) -> None:
		self._paragraph(text, bold=True)

	def paragraph(self, text: str) -> None:
		self._paragraph(text)

	def bullets(self, items) -> None:
		for item in items:
			self._paragraph(f"• {item}", indent=360)

	def key_values(self, pairs) -> None:
		"""A two-column borderless table — Word's answer to a dotted leader."""
		self.table(
			[],
			[[str(label), "" if value is None else str(value)] for label, value in pairs],
			widths=(38, 62),
			borders=False,
		)

	def spacer(self) -> None:
		self._body.append("<w:p/>")

	def page_break(self) -> None:
		self._body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')

	def table(self, headers, rows, widths=None, borders: bool = True) -> None:
		"""A table. `widths` are percentages of the text column, not twips."""
		columns = max([len(headers)] + [len(row) for row in rows] or [1]) or 1
		if widths:
			shares = [float(width) for width in list(widths)[:columns]]
			shares += [0.0] * (columns - len(shares))
			total = sum(shares) or float(columns)
			twips = [int(CONTENT_WIDTH_TWIPS * share / total) for share in shares]
		else:
			twips = [CONTENT_WIDTH_TWIPS // columns] * columns

		grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in twips)
		border_style = (
			"".join(
				f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="999999"/>'
				for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
			)
			if borders
			else "".join(
				f'<w:{edge} w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
				for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
			)
		)
		body = []
		if headers:
			body.append(self._row(headers, twips, bold=True))
		for row in rows:
			body.append(self._row(row, twips, bold=False))
		self._body.append(
			'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
			f"<w:tblBorders>{border_style}</w:tblBorders></w:tblPr>"
			f"<w:tblGrid>{grid}</w:tblGrid>{''.join(body)}</w:tbl>"
		)

	def _row(self, cells, twips, bold: bool) -> str:
		out = []
		for index, width in enumerate(twips):
			cell = cells[index] if index < len(cells) else ""
			text = "" if cell is None else str(cell)
			out.append(
				f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/></w:tcPr>'
				f"{self._paragraph_xml(text, bold=bold)}</w:tc>"
			)
		return f"<w:tr>{''.join(out)}</w:tr>"

	# ── primitives ──────────────────────────────────────────────────────────
	def _paragraph(self, text: str, style: str = "", bold: bool = False, align: str = "", indent: int = 0):
		self._body.append(self._paragraph_xml(text, style=style, bold=bold, align=align, indent=indent))

	@staticmethod
	def _paragraph_xml(text: str, style: str = "", bold: bool = False, align: str = "", indent: int = 0) -> str:
		properties = []
		if style:
			properties.append(f'<w:pStyle w:val="{style}"/>')
		if indent:
			properties.append(f'<w:ind w:left="{indent}"/>')
		if align:
			properties.append(f'<w:jc w:val="{align}"/>')
		if bold:
			properties.append("<w:rPr><w:b/></w:rPr>")
		prefix = f"<w:pPr>{''.join(properties)}</w:pPr>" if properties else ""
		run_properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
		# Word treats a literal newline in <w:t> as a space; <w:br/> is the break.
		runs = []
		for index, chunk in enumerate(str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")):
			if index:
				runs.append(f"<w:r>{run_properties}<w:br/></w:r>")
			runs.append(f'<w:r>{run_properties}<w:t xml:space="preserve">{escape(chunk)}</w:t></w:r>')
		return f"<w:p>{prefix}{''.join(runs)}</w:p>"

	# ── output ──────────────────────────────────────────────────────────────
	def render(self) -> bytes:
		buffer = BytesIO()
		with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
			self._write(archive, "[Content_Types].xml", _CONTENT_TYPES)
			self._write(archive, "_rels/.rels", _ROOT_RELS)
			self._write(archive, "docProps/core.xml", self._core_xml())
			self._write(archive, "word/document.xml", self._document_xml())
			self._write(archive, "word/_rels/document.xml.rels", _DOCUMENT_RELS)
			self._write(archive, "word/styles.xml", _STYLES_XML)
		return buffer.getvalue()

	@staticmethod
	def _write(archive: zipfile.ZipFile, path: str, payload: str) -> None:
		info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
		info.compress_type = zipfile.ZIP_DEFLATED
		info.external_attr = 0o600 << 16
		archive.writestr(info, payload.encode("utf-8"))

	def _document_xml(self) -> str:
		return (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
			f"<w:body>{''.join(self._body)}"
			f'<w:sectPr><w:pgSz w:w="{PAGE_WIDTH_TWIPS}" w:h="{PAGE_HEIGHT_TWIPS}"/>'
			f'<w:pgMar w:top="{MARGIN_TWIPS}" w:right="{MARGIN_TWIPS}" w:bottom="{MARGIN_TWIPS}" '
			f'w:left="{MARGIN_TWIPS}" w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
			"</w:body></w:document>"
		)

	def _core_xml(self) -> str:
		return (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			'<cp:coreProperties '
			'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
			'xmlns:dc="http://purl.org/dc/elements/1.1/">'
			f"<dc:title>{escape(self.title)}</dc:title>"
			f"<dc:subject>{escape(self.subject)}</dc:subject>"
			f"<dc:creator>{escape(self.author)}</dc:creator>"
			"</cp:coreProperties>"
		)


_CONTENT_TYPES = (
	'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
	'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
	'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
	'<Default Extension="xml" ContentType="application/xml"/>'
	'<Override PartName="/word/document.xml" '
	'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
	'<Override PartName="/word/styles.xml" '
	'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
	'<Override PartName="/docProps/core.xml" '
	'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
	"</Types>"
)

_ROOT_RELS = (
	'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
	'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
	'<Relationship Id="rId1" '
	'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
	'Target="word/document.xml"/>'
	'<Relationship Id="rId2" '
	'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
	'Target="docProps/core.xml"/>'
	"</Relationships>"
)

_DOCUMENT_RELS = (
	'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
	'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
	'<Relationship Id="rId1" '
	'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
	'Target="styles.xml"/>'
	"</Relationships>"
)

_STYLES_XML = (
	'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
	'<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
	'<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/>'
	'<w:sz w:val="20"/></w:rPr></w:rPrDefault></w:docDefaults>'
	'<w:style w:type="paragraph" w:styleId="McpTitle"><w:name w:val="MCP Title"/>'
	'<w:pPr><w:spacing w:after="120"/></w:pPr><w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>'
	'<w:style w:type="paragraph" w:styleId="McpHeading"><w:name w:val="MCP Heading"/>'
	'<w:pPr><w:spacing w:before="240" w:after="120"/></w:pPr>'
	'<w:rPr><w:b/><w:caps/><w:sz w:val="22"/></w:rPr></w:style>'
	"</w:styles>"
)
