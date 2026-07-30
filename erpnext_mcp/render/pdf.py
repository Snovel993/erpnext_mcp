# SPDX-License-Identifier: MIT
"""A PDF writer for reports that are text in boxes. Standard library only.

THE FILE FORMAT, IN THE ONLY DETAIL THAT MATTERS HERE. A PDF is a sequence of
numbered objects, then a cross-reference table giving the **byte offset** of each
one, then a trailer pointing at the table. Get an offset wrong by one and every
reader rejects the file, so `render()` builds the body first, records where each
object landed as it appends it, and writes the table from those recorded numbers.
There is no second source of truth for an offset in this module.

WHAT IS DELIBERATELY MISSING. No compression (these documents are kilobytes, and
an uncompressed content stream is one that a human can diff), no images, no
colour, no embedded fonts. The three fonts are base-14 Courier variants, which
every conforming reader has and no PDF has to carry.

METRICS ARE EXACT, NOT ESTIMATED. Courier is monospaced at 600/1000 em, so a
string of n characters at size s is exactly `n * 0.6 * s` points wide. Wrapping,
centring and right-alignment are therefore arithmetic rather than approximation —
see the package docstring for why that was worth giving up proportional type for.

PAGINATION IS AUTOMATIC AND HONEST. Every emitter checks the remaining vertical
space before it draws, and a table that crosses a page boundary repeats its
header row on the new page. A row is never split across pages: a half-row at a
page break is how a reader loses a number without noticing.
"""

from __future__ import annotations

#: US Letter, in points. Not configurable: these documents are printed, signed
#: and mailed in the United States, and a page size that varies between runs
#: would make two copies of the same report physically different objects.
PAGE_WIDTH = 612.0
PAGE_HEIGHT = 792.0

#: Three quarters of an inch on every side — enough for a hole punch on the left
#: and a staple in the corner, which is what happens to these once printed.
MARGIN = 54.0

#: Where the footer rule and page number sit, measured from the bottom edge.
FOOTER_BASELINE = 30.0
FOOTER_RULE_Y = 44.0

#: Every Courier glyph is 600/1000 em wide. This is the whole font metric.
CHAR_WIDTH_EM = 0.6

FONT_REGULAR = "F1"
FONT_BOLD = "F2"
FONT_ITALIC = "F3"

_FONT_NAMES = {
	FONT_REGULAR: "Courier",
	FONT_BOLD: "Courier-Bold",
	FONT_ITALIC: "Courier-Oblique",
}

#: Default type sizes. Body at 9pt gives 95 characters across the text column,
#: which is wide enough for a description plus two money columns.
SIZE_TITLE = 15.0
SIZE_HEADING = 11.0
SIZE_SUBHEADING = 9.5
SIZE_BODY = 9.0
SIZE_SMALL = 7.5

#: Leading as a multiple of type size. 1.35 is loose enough that a monospaced
#: face does not read as a wall.
LEADING = 1.35

#: Space between table columns, in characters.
COLUMN_GAP = 2

#: Narrowest a column may be squeezed to before the text in it stops being
#: readable. A table that cannot fit inside this wraps instead of shrinking.
MIN_COLUMN_CHARS = 6


def text_width(text: str, size: float) -> float:
	"""Exact width of `text` at `size`, in points."""
	return len(text) * CHAR_WIDTH_EM * size


def chars_that_fit(width: float, size: float) -> int:
	"""How many characters fit in `width` points at `size`. Never negative."""
	per_char = CHAR_WIDTH_EM * size
	if per_char <= 0:  # pragma: no cover - size is always positive here
		return 0
	return max(0, int(width / per_char))


def wrap(text: str, width_chars: int) -> list[str]:
	"""Greedy word wrap to `width_chars`, splitting words longer than the column.

	Returns at least one line — a caller drawing a blank cell still needs a row
	to exist, and returning an empty list would silently drop it.
	"""
	if width_chars <= 0:  # pragma: no cover - guarded by MIN_COLUMN_CHARS
		return [""]
	lines: list[str] = []
	for paragraph in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
		words = paragraph.split()
		if not words:
			lines.append("")
			continue
		current = ""
		for word in words:
			while len(word) > width_chars:
				# A word longer than the column — a docname, a URL. Break it
				# rather than letting it run past the margin.
				if current:
					lines.append(current)
					current = ""
				lines.append(word[:width_chars])
				word = word[width_chars:]
			if not current:
				current = word
			elif len(current) + 1 + len(word) <= width_chars:
				current = f"{current} {word}"
			else:
				lines.append(current)
				current = word
		if current:
			lines.append(current)
	return lines or [""]


def escape(text: str) -> str:
	r"""Escape a string for a PDF literal: backslash, both parens, and newlines.

	Order matters — backslash first, or the escapes added for the parens get
	escaped again and the reader sees a literal `\(`.
	"""
	out = str(text).replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
	return out.replace("\n", r"\n").replace("\r", r"\r").replace("\t", "    ")


def encode(text: str) -> bytes:
	"""Encode to WinAnsi (cp1252), which is the encoding the fonts declare.

	Anything cp1252 cannot represent becomes `?` rather than raising: a curly
	quote pasted into a note is not a reason to fail to produce a tax form.
	"""
	return str(text).encode("cp1252", "replace")


#: What a missing figure prints as. An em dash rather than "0.00", because "we
#: do not have this figure" and "this figure is zero" are different statements
#: and a report that conflates them is not evidence of anything. cp1252 has the
#: em dash at 0x97, so it survives `encode` intact.
NO_FIGURE = "—"


def money(value, places: int = 2) -> str:
	"""A number as it belongs in a financial statement: grouped, fixed places."""
	if value is None or value == "":
		return NO_FIGURE
	return f"{float(value):,.{places}f}"


class PdfDocument:
	"""A flowing text document: headings, paragraphs, key/value blocks, tables.

	The API is emit-in-order. There is no layout engine and no going back — each
	call draws at the current cursor and advances it, breaking the page when it
	has to. That is the right shape for a report whose sections are known in
	advance, and it makes the output a pure function of the call sequence.
	"""

	def __init__(
		self,
		title: str = "",
		author: str = "",
		subject: str = "",
		footer: str = "",
		producer: str = "erpnext_mcp",
	):
		self.title = title
		self.author = author
		self.subject = subject
		self.footer = footer
		self.producer = producer
		self._pages: list[list[str]] = []
		self._ops: list[str] = []
		self._y = PAGE_HEIGHT - MARGIN
		self._page_started = False

	# ── geometry ────────────────────────────────────────────────────────────
	@property
	def content_width(self) -> float:
		return PAGE_WIDTH - 2 * MARGIN

	@property
	def bottom_limit(self) -> float:
		"""The lowest baseline a line of body text may occupy."""
		return FOOTER_RULE_Y + 16.0

	def _ensure_page(self) -> None:
		if not self._page_started:
			self._page_started = True
			self._y = PAGE_HEIGHT - MARGIN

	def _room_for(self, height: float) -> None:
		"""Break the page if `height` more points would run into the footer."""
		self._ensure_page()
		if self._y - height < self.bottom_limit:
			self.page_break()

	def page_break(self) -> None:
		"""Start a new page. A no-op on a page nothing has been drawn on yet."""
		if not self._page_started or not self._ops:
			self._ensure_page()
			return
		self._pages.append(self._ops)
		self._ops = []
		self._y = PAGE_HEIGHT - MARGIN

	# ── primitives ──────────────────────────────────────────────────────────
	def _text(self, x: float, y: float, text: str, font: str = FONT_REGULAR, size: float = SIZE_BODY):
		self._ops.append(f"BT /{font} {size:.2f} Tf 1 0 0 1 {x:.2f} {y:.2f} Tm ({escape(text)}) Tj ET")

	def _line(self, x1: float, y: float, x2: float, width: float = 0.5):
		self._ops.append(f"{width:.2f} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

	def line(
		self,
		text: str,
		font: str = FONT_REGULAR,
		size: float = SIZE_BODY,
		align: str = "left",
		indent: float = 0.0,
	) -> None:
		"""One line of text, no wrapping. Breaks the page if it will not fit."""
		height = size * LEADING
		self._room_for(height)
		self._y -= size
		width = text_width(text, size)
		if align == "center":
			x = MARGIN + (self.content_width - width) / 2
		elif align == "right":
			x = PAGE_WIDTH - MARGIN - width
		else:
			x = MARGIN + indent
		self._text(x, self._y, text, font, size)
		self._y -= height - size

	def spacer(self, points: float = 6.0) -> None:
		self._ensure_page()
		self._y -= points

	def rule(self, width: float = 0.5, gap: float = 4.0) -> None:
		self._room_for(gap * 2)
		self._y -= gap
		self._line(MARGIN, self._y, PAGE_WIDTH - MARGIN, width)
		self._y -= gap

	# ── blocks ──────────────────────────────────────────────────────────────
	def title_block(self, title: str, *subtitles: str) -> None:
		"""The masthead: a centred title, centred subtitles, and a rule under it."""
		self.line(title, FONT_BOLD, SIZE_TITLE, align="center")
		for subtitle in subtitles:
			if subtitle:
				self.line(subtitle, FONT_REGULAR, SIZE_SUBHEADING, align="center")
		self.rule(width=1.0)
		self.spacer(4)

	def heading(self, text: str) -> None:
		"""A section heading, kept with at least three lines of what follows.

		The three lines are the point: a heading alone at the foot of a page tells
		a reader the section is empty.
		"""
		self._room_for(SIZE_HEADING * LEADING + 3 * SIZE_BODY * LEADING + 10)
		self.spacer(8)
		self.line(text.upper(), FONT_BOLD, SIZE_HEADING)
		self.rule(width=0.75, gap=2.0)
		self.spacer(2)

	def subheading(self, text: str) -> None:
		self._room_for(SIZE_SUBHEADING * LEADING * 3)
		self.spacer(4)
		self.line(text, FONT_BOLD, SIZE_SUBHEADING)

	def paragraph(self, text: str, size: float = SIZE_BODY, font: str = FONT_REGULAR, indent: float = 0.0):
		"""Wrapped body text."""
		width_chars = chars_that_fit(self.content_width - indent, size)
		for line in wrap(text, width_chars):
			self.line(line, font, size, indent=indent)

	def bullets(self, items, size: float = SIZE_BODY) -> None:
		for item in items:
			width_chars = chars_that_fit(self.content_width - 12, size)
			lines = wrap(str(item), width_chars)
			self.line(f"- {lines[0]}", size=size)
			for line in lines[1:]:
				self.line(f"  {line}", size=size)

	def key_values(self, pairs, label_chars: int = 34, size: float = SIZE_BODY) -> None:
		"""A two-column block of `label ... value`, wrapping the value.

		`label_chars` is in characters rather than points because the face is
		monospaced, which is the whole reason a dotted leader lines up here.
		"""
		value_chars = max(MIN_COLUMN_CHARS, chars_that_fit(self.content_width, size) - label_chars - 2)
		for label, value in pairs:
			text = "" if value is None else str(value)
			lines = wrap(text, value_chars)
			head = str(label)
			if len(head) > label_chars:
				head = head[: label_chars - 1] + "."
			self.line(f"{head.ljust(label_chars)}  {lines[0]}", size=size)
			for line in lines[1:]:
				self.line(f"{' ' * label_chars}  {line}", size=size)

	def table(self, headers, rows, align=None, size: float = SIZE_SMALL, widths=None) -> None:
		"""A bordered-by-rules table that repeats its header across page breaks.

		`align` is one of "l"/"r"/"c" per column. `widths` is an optional list of
		column widths in characters; anything omitted is measured from the content
		and then scaled down proportionally if the natural widths do not fit.
		"""
		headers = [str(header) for header in headers]
		rows = [[("" if cell is None else str(cell)) for cell in row] for row in rows]
		columns = len(headers)
		align = list(align or ["l"] * columns)
		align += ["l"] * (columns - len(align))

		available = chars_that_fit(self.content_width, size)
		widths = self._column_widths(headers, rows, columns, available, widths, align)
		rule_width = text_width("x" * (sum(widths) + COLUMN_GAP * (columns - 1)), size)

		def draw_header() -> None:
			self._draw_wrapped_row(headers, widths, align, size, FONT_BOLD)
			self._y -= 2
			self._line(MARGIN, self._y, MARGIN + rule_width)
			self._y -= 3

		header_height = max(len(wrap(header, widths[i])) for i, header in enumerate(headers))
		header_height = header_height * size * LEADING + 5
		self._room_for(header_height + size * LEADING * 2)
		draw_header()
		for row in rows:
			wrapped = [wrap(cell, widths[index]) for index, cell in enumerate(row)]
			depth = max(len(cell) for cell in wrapped)
			self._ensure_page()
			if self._y - depth * size * LEADING < self.bottom_limit:
				self.page_break()
				draw_header()
			self._draw_row_lines(wrapped, widths, align, size, FONT_REGULAR)

	def _draw_wrapped_row(self, cells, widths, align, size, font) -> None:
		wrapped = [wrap(str(cell), widths[index]) for index, cell in enumerate(cells)]
		self._draw_row_lines(wrapped, widths, align, size, font)

	def _draw_row_lines(self, wrapped, widths, align, size, font) -> None:
		for depth in range(max(len(cell) for cell in wrapped)):
			self._row(
				[cell[depth] if depth < len(cell) else "" for cell in wrapped],
				widths,
				align,
				size,
				font,
			)

	def _column_widths(self, headers, rows, columns, available, widths, align) -> list[int]:
		"""Natural widths where they fit; otherwise squeeze the prose, not the money.

		A right-aligned column holds a formatted amount, and an amount that wraps
		mid-number — `1,100.0` on one line and `0` on the next — is unreadable and
		looks like two figures. So right-aligned columns keep their natural width
		and the shrinking falls entirely on the left-aligned ones. Only when the
		numbers alone will not fit does everything scale together, which is a table
		that should have had fewer columns.
		"""
		if widths:
			return [max(MIN_COLUMN_CHARS, int(width)) for width in list(widths)[:columns]]
		natural = []
		for index in range(columns):
			longest = len(headers[index])
			for row in rows:
				if index < len(row):
					longest = max(longest, max((len(part) for part in row[index].split("\n")), default=0))
			natural.append(max(MIN_COLUMN_CHARS, longest))

		gaps = COLUMN_GAP * (columns - 1)
		room = max(columns * MIN_COLUMN_CHARS, available - gaps)
		if sum(natural) <= room:
			return natural

		fixed = [index for index in range(columns) if align[index] == "r"]
		flexible = [index for index in range(columns) if index not in fixed]
		fixed_total = sum(natural[index] for index in fixed)
		flexible_total = sum(natural[index] for index in flexible)
		flexible_room = room - fixed_total
		if flexible and flexible_room >= len(flexible) * MIN_COLUMN_CHARS:
			out = list(natural)
			for index in flexible:
				out[index] = max(MIN_COLUMN_CHARS, int(natural[index] * flexible_room / flexible_total))
			return self._spend_slack(out, room, flexible)

		scaled = [max(MIN_COLUMN_CHARS, int(width * room / sum(natural))) for width in natural]
		return self._spend_slack(scaled, room, list(range(columns)))

	@staticmethod
	def _spend_slack(widths: list[int], room: int, candidates: list[int]) -> list[int]:
		"""Give integer-division leftovers to the widest eligible column."""
		slack = room - sum(widths)
		if slack > 0 and candidates:
			widest = max(candidates, key=lambda index: widths[index])
			widths[widest] += slack
		return widths

	def _row(self, cells, widths, align, size, font) -> None:
		parts = []
		for index, cell in enumerate(cells):
			width = widths[index]
			text = cell if len(cell) <= width else cell[: max(1, width - 1)] + "."
			if align[index] == "r":
				parts.append(text.rjust(width))
			elif align[index] == "c":
				parts.append(text.center(width))
			else:
				parts.append(text.ljust(width))
		self.line((" " * COLUMN_GAP).join(parts).rstrip(), font, size)

	# ── output ──────────────────────────────────────────────────────────────
	def render(self) -> bytes:
		"""The whole document as PDF bytes. Callable once per document."""
		self._ensure_page()
		pages = list(self._pages)
		if self._ops or not pages:
			pages.append(self._ops)
		streams = [self._compose(index, len(pages), ops) for index, ops in enumerate(pages, start=1)]
		return _assemble(streams, self.title, self.author, self.subject, self.producer)

	def _compose(self, number: int, total: int, ops: list[str]) -> str:
		"""One page's content stream, with its footer drawn last."""
		body = list(ops)
		body.append(f"0.50 w {MARGIN:.2f} {FOOTER_RULE_Y:.2f} m {PAGE_WIDTH - MARGIN:.2f} {FOOTER_RULE_Y:.2f} l S")
		if self.footer:
			body.append(
				f"BT /{FONT_REGULAR} {SIZE_SMALL:.2f} Tf 1 0 0 1 {MARGIN:.2f} {FOOTER_BASELINE:.2f} Tm "
				f"({escape(self.footer)}) Tj ET"
			)
		stamp = f"Page {number} of {total}"
		x = PAGE_WIDTH - MARGIN - text_width(stamp, SIZE_SMALL)
		body.append(
			f"BT /{FONT_REGULAR} {SIZE_SMALL:.2f} Tf 1 0 0 1 {x:.2f} {FOOTER_BASELINE:.2f} Tm "
			f"({escape(stamp)}) Tj ET"
		)
		return "\n".join(body)


def _assemble(streams: list[str], title: str, author: str, subject: str, producer: str) -> bytes:
	"""Wrap page content streams in the object graph, xref table and trailer.

	Object numbering is fixed so the offsets stay readable while debugging:
	1 catalog, 2 pages, 3-5 fonts, 6 info, then two objects per page.
	"""
	count = len(streams)
	first_page_object = 7
	kids = " ".join(f"{first_page_object + 2 * index} 0 R" for index in range(count))

	objects: list[bytes] = []
	objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
	objects.append(
		f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode("ascii")
	)
	for font_key in (FONT_REGULAR, FONT_BOLD, FONT_ITALIC):
		objects.append(
			f"<< /Type /Font /Subtype /Type1 /BaseFont /{_FONT_NAMES[font_key]} "
			"/Encoding /WinAnsiEncoding >>".encode("ascii")
		)
	info = b"<< " + b" ".join(
		part
		for part in (
			b"/Title (" + encode(escape(title)) + b")" if title else b"",
			b"/Author (" + encode(escape(author)) + b")" if author else b"",
			b"/Subject (" + encode(escape(subject)) + b")" if subject else b"",
			b"/Producer (" + encode(escape(producer)) + b")" if producer else b"",
		)
		if part
	) + b" >>"
	objects.append(info)

	for index, stream in enumerate(streams):
		content_object = first_page_object + 2 * index + 1
		objects.append(
			(
				f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH:.0f} {PAGE_HEIGHT:.0f}] "
				f"/Resources << /Font << /{FONT_REGULAR} 3 0 R /{FONT_BOLD} 4 0 R /{FONT_ITALIC} 5 0 R >> >> "
				f"/Contents {content_object} 0 R >>"
			).encode("ascii")
		)
		payload = encode(stream)
		objects.append(b"<< /Length " + str(len(payload)).encode("ascii") + b" >>\nstream\n" + payload + b"\nendstream")

	out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
	offsets = [0]
	for number, payload in enumerate(objects, start=1):
		offsets.append(len(out))
		out += str(number).encode("ascii") + b" 0 obj\n" + payload + b"\nendobj\n"

	xref_at = len(out)
	out += b"xref\n0 " + str(len(objects) + 1).encode("ascii") + b"\n"
	out += b"0000000000 65535 f \n"
	for offset in offsets[1:]:
		out += f"{offset:010d} 00000 n \n".encode("ascii")
	out += (
		b"trailer\n<< /Size "
		+ str(len(objects) + 1).encode("ascii")
		+ b" /Root 1 0 R /Info 6 0 R >>\nstartxref\n"
		+ str(xref_at).encode("ascii")
		+ b"\n%%EOF\n"
	)
	return bytes(out)
