# SPDX-License-Identifier: MIT
"""An XLSX writer. A spreadsheet is a zip of XML, and `zipfile` is stdlib.

WHAT THIS PRODUCES. An OOXML workbook with one or more worksheets, a bold header
row, frozen panes under it, an autofilter over the used range, explicit column
widths, and three number formats — plain, two-decimal currency-style with
thousands separators, and text (which is what stops a TIN like `0123` losing its
leading zero the moment somebody opens the file). Excel, LibreOffice, Numbers and
Google Sheets all read it.

WHY INLINE STRINGS. The alternative is a shared-string table, which is smaller
for a workbook that repeats the same text thousands of times and is a second
index to keep consistent for one that does not. These workbooks are hundreds of
rows of mostly-distinct names and amounts, so the table would save nothing and
cost an invariant. `t="inlineStr"` keeps every cell self-describing.

DETERMINISM. Every zip member is written with a fixed timestamp, so generating
the same report twice gives byte-identical files. An archive copy that differs
from the copy somebody printed — only in a zip header nobody can see — is a
question that costs an hour to answer.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from io import BytesIO

#: Fixed member timestamp. 1980-01-01 is the earliest a zip can express, and is
#: the convention for reproducible archives.
_ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: Style indices into the `cellXfs` list written by `_styles_xml`, in order.
STYLE_DEFAULT = 0
STYLE_HEADER = 1
STYLE_MONEY = 2
STYLE_TEXT = 3
STYLE_BOLD = 4
STYLE_DATE = 5

#: Widen a column to fit its content, within these bounds. A column sized to a
#: 400-character note is not a column anybody scrolls past.
MIN_COLUMN_WIDTH = 8
MAX_COLUMN_WIDTH = 60


def column_letter(index: int) -> str:
	"""1 → A, 26 → Z, 27 → AA. Excel's bijective base-26."""
	if index < 1:
		raise ValueError(f"column index must be 1 or more, got {index}")
	out = ""
	while index:
		index, remainder = divmod(index - 1, 26)
		out = chr(ord("A") + remainder) + out
	return out


def escape(text) -> str:
	"""XML-escape, and drop the control characters XML 1.0 cannot carry.

	A NUL or a form feed pasted into a vendor name is not a reason to write a
	workbook no reader will open.
	"""
	out = str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	out = out.replace('"', "&quot;")
	return "".join(
		char
		for char in out
		if char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF or 0xE000 <= ord(char) <= 0xFFFD
	)


@dataclass
class Sheet:
	"""One worksheet: a title, a header row, and rows of values.

	A cell is a Python value — `str`, `int`, `float`, `None` — or a `(value,
	style)` tuple when the default style for that type is wrong. The one that
	matters is a TIN or an account number, which is a string that looks like a
	number: pass `(value, STYLE_TEXT)` and it stays what it is.
	"""

	title: str
	headers: list = field(default_factory=list)
	rows: list = field(default_factory=list)
	#: Column widths in characters. Anything omitted is measured from content.
	widths: list = field(default_factory=list)
	#: Style index per column applied to body cells, overriding the type default.
	column_styles: list = field(default_factory=list)
	freeze_header: bool = True
	autofilter: bool = True

	def safe_title(self) -> str:
		"""Excel's rules: 31 characters, none of `[]:*?/\\`, and never empty."""
		out = "".join(" " if char in "[]:*?/\\" else char for char in str(self.title))
		out = out.strip()[:31]
		return out or "Sheet"


class XlsxWorkbook:
	"""A workbook of `Sheet`s. Build it, then call `render()` for the bytes."""

	def __init__(self, *sheets: Sheet, creator: str = "erpnext_mcp", title: str = ""):
		self.sheets: list[Sheet] = list(sheets)
		self.creator = creator
		self.title = title

	def add(self, sheet: Sheet) -> Sheet:
		self.sheets.append(sheet)
		return sheet

	def render(self) -> bytes:
		if not self.sheets:
			raise ValueError("a workbook needs at least one sheet")
		self._refuse_duplicate_titles()
		buffer = BytesIO()
		with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
			self._write(archive, "[Content_Types].xml", self._content_types())
			self._write(archive, "_rels/.rels", _ROOT_RELS)
			self._write(archive, "docProps/core.xml", self._core_xml())
			self._write(archive, "docProps/app.xml", _APP_XML)
			self._write(archive, "xl/workbook.xml", self._workbook_xml())
			self._write(archive, "xl/_rels/workbook.xml.rels", self._workbook_rels())
			self._write(archive, "xl/styles.xml", _STYLES_XML)
			for index, sheet in enumerate(self.sheets, start=1):
				self._write(archive, f"xl/worksheets/sheet{index}.xml", _sheet_xml(sheet))
		return buffer.getvalue()

	def _refuse_duplicate_titles(self) -> None:
		"""Two sheets with the same name is a workbook Excel refuses to open.

		Truncation to 31 characters is what makes it happen by accident, so the
		check is on the truncated name rather than the one the caller passed.
		"""
		seen = {}
		for sheet in self.sheets:
			title = sheet.safe_title().lower()
			if title in seen:
				raise ValueError(
					f"two sheets are both named {sheet.safe_title()!r} once truncated to Excel's "
					"31-character limit; give them names that differ in the first 31 characters"
				)
			seen[title] = True

	@staticmethod
	def _write(archive: zipfile.ZipFile, path: str, payload: str) -> None:
		info = zipfile.ZipInfo(path, date_time=_ZIP_EPOCH)
		info.compress_type = zipfile.ZIP_DEFLATED
		info.external_attr = 0o600 << 16
		archive.writestr(info, payload.encode("utf-8"))

	def _content_types(self) -> str:
		overrides = "".join(
			f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
			'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
			for index in range(1, len(self.sheets) + 1)
		)
		return (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
			'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
			'<Default Extension="xml" ContentType="application/xml"/>'
			'<Override PartName="/xl/workbook.xml" '
			'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
			f"{overrides}"
			'<Override PartName="/xl/styles.xml" '
			'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
			'<Override PartName="/docProps/core.xml" '
			'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
			'<Override PartName="/docProps/app.xml" '
			'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
			"</Types>"
		)

	def _workbook_xml(self) -> str:
		sheets = "".join(
			f'<sheet name="{escape(sheet.safe_title())}" sheetId="{index}" r:id="rId{index}"/>'
			for index, sheet in enumerate(self.sheets, start=1)
		)
		return (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
			'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
			f"<sheets>{sheets}</sheets></workbook>"
		)

	def _workbook_rels(self) -> str:
		relationships = "".join(
			f'<Relationship Id="rId{index}" '
			'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
			f'Target="worksheets/sheet{index}.xml"/>'
			for index in range(1, len(self.sheets) + 1)
		)
		styles_id = len(self.sheets) + 1
		return (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
			f"{relationships}"
			f'<Relationship Id="rId{styles_id}" '
			'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
			'Target="styles.xml"/>'
			"</Relationships>"
		)

	def _core_xml(self) -> str:
		return (
			'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
			"<cp:coreProperties "
			'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
			'xmlns:dc="http://purl.org/dc/elements/1.1/">'
			f"<dc:title>{escape(self.title)}</dc:title>"
			f"<dc:creator>{escape(self.creator)}</dc:creator>"
			f"<cp:lastModifiedBy>{escape(self.creator)}</cp:lastModifiedBy>"
			"</cp:coreProperties>"
		)


def _cell_xml(reference: str, value, style: int) -> str:
	if value is None or value == "":
		return f'<c r="{reference}" s="{style}"/>'
	if isinstance(value, bool):
		# Before the numeric branch: bool is an int in Python and TRUE/FALSE in a
		# spreadsheet is not the number 1.
		return f'<c r="{reference}" s="{style}" t="b"><v>{1 if value else 0}</v></c>'
	if isinstance(value, (int, float)) and style != STYLE_TEXT:
		return f'<c r="{reference}" s="{style}"><v>{value!r}</v></c>'
	return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'


def _unpack(cell):
	"""A cell is a value, or a (value, style) pair when the default is wrong."""
	if isinstance(cell, tuple) and len(cell) == 2 and isinstance(cell[1], int):
		return cell[0], cell[1]
	return cell, None


def _default_style(value) -> int:
	if isinstance(value, bool) or value is None:
		return STYLE_DEFAULT
	if isinstance(value, (int, float)):
		return STYLE_MONEY
	return STYLE_DEFAULT


def _measured_widths(sheet: Sheet) -> list[int]:
	columns = max([len(sheet.headers)] + [len(row) for row in sheet.rows])
	widths = list(sheet.widths) + [0] * max(0, columns - len(sheet.widths))
	for index in range(columns):
		if widths[index]:
			continue
		longest = len(str(sheet.headers[index])) if index < len(sheet.headers) else 0
		for row in sheet.rows:
			if index < len(row):
				value, _style = _unpack(row[index])
				text = "" if value is None else str(value)
				longest = max(longest, len(text))
		widths[index] = max(MIN_COLUMN_WIDTH, min(MAX_COLUMN_WIDTH, longest + 2))
	return widths


def _sheet_xml(sheet: Sheet) -> str:
	widths = _measured_widths(sheet)
	columns = len(widths)
	body = []

	if sheet.headers:
		cells = "".join(
			_cell_xml(f"{column_letter(index + 1)}1", header, STYLE_HEADER)
			for index, header in enumerate(sheet.headers)
		)
		body.append(f'<row r="1">{cells}</row>')

	offset = 2 if sheet.headers else 1
	for number, row in enumerate(sheet.rows, start=offset):
		cells = []
		for index, cell in enumerate(row):
			value, style = _unpack(cell)
			if style is None and index < len(sheet.column_styles) and sheet.column_styles[index] is not None:
				style = sheet.column_styles[index]
			if style is None:
				style = _default_style(value)
			cells.append(_cell_xml(f"{column_letter(index + 1)}{number}", value, style))
		body.append(f'<row r="{number}">{"".join(cells)}</row>')

	cols = "".join(
		f'<col min="{index + 1}" max="{index + 1}" width="{width}" customWidth="1"/>'
		for index, width in enumerate(widths)
	)
	last_row = len(sheet.rows) + (1 if sheet.headers else 0)
	dimension = f"A1:{column_letter(max(1, columns))}{max(1, last_row)}"
	panes = (
		'<sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
		'state="frozen"/></sheetView>'
		if (sheet.freeze_header and sheet.headers)
		else '<sheetView workbookViewId="0"/>'
	)
	filters = (
		f'<autoFilter ref="A1:{column_letter(max(1, columns))}{max(1, last_row)}"/>'
		if (sheet.autofilter and sheet.headers and sheet.rows)
		else ""
	)
	# Element order is fixed by the OOXML schema: dimension, sheetViews,
	# sheetFormatPr, cols, sheetData, autoFilter. An empty <cols/> is invalid,
	# so a sheet with no columns omits the element rather than emitting one.
	return (
		'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
		'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
		f'<dimension ref="{dimension}"/>'
		f"<sheetViews>{panes}</sheetViews>"
		'<sheetFormatPr defaultRowHeight="15"/>'
		f"{f'<cols>{cols}</cols>' if cols else ''}"
		f"<sheetData>{''.join(body)}</sheetData>"
		f"{filters}"
		"</worksheet>"
	)


#: numFmtId 164 is the first id available to a document; 49 is the built-in "@"
#: (text) format, and 0 is General. The money format keeps negatives in
#: parentheses, which is what an accountant expects to see.
_STYLES_XML = (
	'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
	'<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
	'<numFmts count="1">'
	'<numFmt numFmtId="164" formatCode="#,##0.00_);(#,##0.00)"/>'
	"</numFmts>"
	'<fonts count="2">'
	'<font><sz val="10"/><name val="Calibri"/></font>'
	'<font><b/><sz val="10"/><name val="Calibri"/></font>'
	"</fonts>"
	'<fills count="3">'
	'<fill><patternFill patternType="none"/></fill>'
	'<fill><patternFill patternType="gray125"/></fill>'
	'<fill><patternFill patternType="solid"><fgColor rgb="FFEEEEEE"/><bgColor indexed="64"/></patternFill></fill>'
	"</fills>"
	'<borders count="2">'
	"<border><left/><right/><top/><bottom/><diagonal/></border>"
	'<border><left/><right/><top/><bottom style="thin"><color rgb="FF888888"/></bottom><diagonal/></border>'
	"</borders>"
	'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
	'<cellXfs count="6">'
	'<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
	'<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" '
	'applyBorder="1" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf>'
	'<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
	'<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
	'<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
	'<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
	"</cellXfs>"
	'<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
	"</styleSheet>"
)

_ROOT_RELS = (
	'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
	'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
	'<Relationship Id="rId1" '
	'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
	'Target="xl/workbook.xml"/>'
	'<Relationship Id="rId2" '
	'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" '
	'Target="docProps/core.xml"/>'
	'<Relationship Id="rId3" '
	'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" '
	'Target="docProps/app.xml"/>'
	"</Relationships>"
)

_APP_XML = (
	'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
	'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
	"<Application>erpnext_mcp</Application>"
	"</Properties>"
)
