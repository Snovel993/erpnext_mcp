# SPDX-License-Identifier: MIT
"""Stamping a captured signature into a federal PDF, and flattening what is left.

v0.51.0. `i9_pdf.py` and `w4_pdf.py` both fill a government form and both used
to stop short of the signature line — see the rewritten section in each. This is
the machinery they now share, in one place because the two forms need identical
behaviour and the thing that goes wrong when it is copied is that one of them
keeps a form field somebody can still edit.

────────────────────────────────────────────────────────────────────────────
WHAT "INDESTRUCTIBLE" HAS TO MEAN, AND WHAT IT COSTS
────────────────────────────────────────────────────────────────────────────

An image dropped into a PDF can arrive three ways, and two of them are wrong
for a document an employer retains for an inspection:

1. As a **form field value** — deletable by anybody who opens the form.
2. As an **annotation** (a stamp) — deletable in Preview with a single click,
   and Preview is what a Mac opens a PDF in.
3. As **page content** — part of the content stream itself, no more removable
   than the form's own printed rules without editing the page.

This module does the third. `stamp` composes an overlay page carrying the image
and merges it into the target page's content stream, so what comes out has no
annotation to delete and no field to clear.

`flatten` then does the same to every remaining widget: it takes each field's
appearance stream — the Form XObject the viewer would have drawn — merges it
into the page content at the widget's own rectangle, and deletes the widget and
the AcroForm along with it. The values stay visible and stop being values.

**THE ORDER IS FILL, FLATTEN, THEN STAMP, AND IT IS NOT INTERCHANGEABLE.** The
I-9's signature boxes are `/Tx` widgets with their own appearance streams. Stamp
first and `flatten` paints that widget's appearance — an empty box, in some
viewers with an opaque background — directly over the signature that was just
placed. Flattening first removes the widget, and the signature goes onto a page
that has nothing left to paint over it. The rectangles are read BEFORE either
step, because after flattening there are no widgets left to read them from.

────────────────────────────────────────────────────────────────────────────
THE CAPTURE IS WHITE PAPER WITH BLACK INK, NOT INK ON GLASS
────────────────────────────────────────────────────────────────────────────

`SignatureCanvas.renderPNG` on the handset renders with `format.opaque = true`
and fills the canvas white before it strokes. So the PNG on the record is a
white rectangle with a signature in the middle of it, and drawing that onto the
form as-is paints a white box over the signature rule, over the caption
underneath it, and over whatever the form printed either side.

`ink_only` is what makes it a signature rather than a sticker. It keys the paper
out — anything at or above `INK_THRESHOLD` luminance becomes transparent — and
then crops to the bounding box of what is left. The crop matters as much as the
key: a 700x200 capture of a signature written in the middle of the canvas is
about 4.4:1 once cropped and about 3.5:1 before, and the signature line it has
to fit inside is 25:1. Scaling the uncropped capture to fit that line makes the
ink a third of the size it should be.

An image that is ALREADY transparent is left alone and simply cropped, so a
future capture path that renders ink on glass needs no change here.

────────────────────────────────────────────────────────────────────────────
PILLOW
────────────────────────────────────────────────────────────────────────────

Imported defensively, like shapely, h3, segno, reportlab and pypdf before it,
and declared in `pyproject.toml` alongside them. Frappe itself depends on
Pillow, so a bench that can run this app almost certainly has it; the defensive
import is for the case where it does not, and it costs the SIGNATURE rather than
the PDF. `require()` says so by name with the pip command, and every caller here
treats a missing Pillow as "render the form unsigned" rather than as a failure —
an unsigned I-9 is the page an employer prints and signs with a pen, which is
what this app produced for its whole life before this release.
"""

from __future__ import annotations

import io

from .errors import ToolError

#: Luminance at or above which a pixel is paper rather than ink. 200 of 255 is
#: well clear of anti-aliased grey at the edge of a stroke — which must survive,
#: or the signature comes out jagged — and well clear of a capture whose white
#: is not quite 255 because it went through a JPEG on the way.
INK_THRESHOLD = 200

#: How far a signature may grow ABOVE its box, as a multiple of the box height.
#: A signature line on a printed form is a rule with writing sitting on it, and
#: the writing is taller than the gap the form allots — the I-9's employee
#: signature widget is 12.9pt tall, and ink scaled to 12.9pt on a 323pt line is
#: a smudge nobody would accept as a signature. Bounded by the real gap to the
#: row above as well; see `box_for`.
MAX_GROWTH = 1.8

#: Clearance kept below the next widget up, so a tall ascender cannot touch it.
HEADROOM_MARGIN = 1.5

#: Inset from the box edge, so the ink does not sit on the rule.
BOX_PADDING = 1.5


def available() -> bool:
	"""Whether this bench can stamp a signature at all."""
	try:
		import PIL  # noqa: F401
		import reportlab  # noqa: F401
	except ImportError:
		return False
	return True


def require() -> None:
	"""Raise naming what is missing. Callers that can degrade should not call this."""
	if not available():
		raise ToolError(
			"stamping a signature into a PDF needs the Pillow and reportlab Python packages, "
			"which this app declares as dependencies — install them into the bench's "
			"environment with `./env/bin/pip install Pillow 'reportlab>=4.0'` and restart. "
			"The form still renders without them, with the signature boxes left for a pen."
		)


# ── the capture ──────────────────────────────────────────────────────────


def ink_only(image_bytes: bytes, threshold: int = INK_THRESHOLD):
	"""Paper keyed out, cropped to the ink. `(png, width, height)` or None.

	None means there was nothing to draw — an empty canvas, or a capture that
	is entirely paper — and every caller treats that as "no signature" rather
	than as an error. A blank rectangle stamped on a signature line is worse
	than an empty line, because an empty line is honest about what happened.
	"""
	if not image_bytes:
		return None
	try:
		from PIL import Image
	except ImportError:
		return None

	try:
		image = Image.open(io.BytesIO(image_bytes))
		image.load()
		image = image.convert("RGBA")
	except Exception:
		# A truncated upload or a format Pillow will not open. The form still
		# renders; see the module docstring.
		return None

	if image.getchannel("A").getextrema()[0] == 255:
		# Fully opaque: this is the handset's white-paper capture. Luminance
		# becomes the mask, so the ink keeps its anti-aliased edge.
		grey = image.convert("L")
		image.putalpha(grey.point(lambda value: 0 if value >= threshold else 255))

	box = image.getchannel("A").getbbox()
	if not box:
		return None
	image = image.crop(box)
	if image.width < 2 or image.height < 2:
		return None

	buffer = io.BytesIO()
	image.save(buffer, format="PNG", optimize=True)
	return buffer.getvalue(), image.width, image.height


# ── the geometry ─────────────────────────────────────────────────────────


def _widget_name(widget) -> str:
	name = widget.get("/T")
	if name is None and widget.get("/Parent") is not None:
		name = widget["/Parent"].get_object().get("/T")
	return str(name) if name is not None else ""


def box_for(writer, wanted: set) -> dict:
	"""`{field name: {page, rect, max_height}}`, read BEFORE the form is flattened.

	`max_height` is how tall the ink may be drawn: the box itself plus whatever
	clear vertical space is above it, capped at `MAX_GROWTH`. The clear space is
	measured against the other widgets on the page that overlap horizontally,
	which is what keeps a tall signature out of the row above on any template
	rather than only on the one measured by hand today.
	"""
	found: dict = {}
	for page_index, page in enumerate(writer.pages):
		annots = list(page.get("/Annots") or [])
		rects = []
		for annot in annots:
			widget = annot.get_object()
			if widget.get("/Rect") is None:
				continue
			rects.append((_widget_name(widget), [float(v) for v in widget["/Rect"]]))

		for name, rect in rects:
			if name not in wanted or name in found:
				continue
			x0, y0, x1, y1 = min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3])
			ceiling = float(page.mediabox.height)
			for other_name, other in rects:
				if other_name == name:
					continue
				ox0, oy0 = min(other[0], other[2]), min(other[1], other[3])
				ox1 = max(other[0], other[2])
				if oy0 >= y1 and ox1 > x0 and ox0 < x1:
					ceiling = min(ceiling, oy0)
			headroom = max(0.0, ceiling - y1 - HEADROOM_MARGIN)
			height = y1 - y0
			found[name] = {
				"page": page_index,
				"rect": [x0, y0, x1, y1],
				"max_height": min(height + headroom, height * MAX_GROWTH),
			}
	return found


# ── stamping ─────────────────────────────────────────────────────────────


def stamp(writer, page_index: int, rect, image_bytes: bytes, max_height: float = 0.0,
          align: str = "left") -> dict | None:
	"""Draw the signature into the page's CONTENT STREAM. Returns where it went.

	None when there was nothing to draw or this bench cannot draw it — see
	`ink_only` and `require`. Callers render the form unsigned in that case.
	"""
	prepared = ink_only(image_bytes)
	if not prepared:
		return None
	try:
		from reportlab.lib.utils import ImageReader
		from reportlab.pdfgen import canvas
	except ImportError:
		return None
	from pypdf import PdfReader

	png, ink_width, ink_height = prepared
	page = writer.pages[page_index]
	page_width, page_height = float(page.mediabox.width), float(page.mediabox.height)

	x0, y0, x1, y1 = rect
	box_width = (x1 - x0) - 2 * BOX_PADDING
	box_height = (y1 - y0) - 2 * BOX_PADDING
	# The growth allowance is the whole reason a signature on a 12.9pt line is
	# legible; see MAX_GROWTH.
	allowed_height = max(box_height, (max_height or 0.0) - 2 * BOX_PADDING)
	if box_width <= 0 or allowed_height <= 0:
		return None

	scale = min(box_width / ink_width, allowed_height / ink_height)
	width, height = ink_width * scale, ink_height * scale
	if align == "center":
		x = x0 + BOX_PADDING + (box_width - width) / 2.0
	else:
		x = x0 + BOX_PADDING
	# Sits ON the line: anchored to the bottom of the box, growing upward, which
	# is where handwriting goes on a printed rule.
	y = y0 + BOX_PADDING

	buffer = io.BytesIO()
	overlay = canvas.Canvas(buffer, pagesize=(page_width, page_height))
	overlay.drawImage(ImageReader(io.BytesIO(png)), x, y, width=width, height=height, mask="auto")
	overlay.save()
	page.merge_page(PdfReader(io.BytesIO(buffer.getvalue())).pages[0])

	return {"page": page_index, "x": round(x, 2), "y": round(y, 2),
	        "width": round(width, 2), "height": round(height, 2)}


# ── flattening ───────────────────────────────────────────────────────────


def _appearance_matrix(bbox, matrix, rect):
	"""PDF 32000-1 12.5.5: map the transformed appearance BBox onto the Rect."""
	a, b, c, d, e, f = matrix
	xs, ys = [], []
	for (x, y) in ((bbox[0], bbox[1]), (bbox[2], bbox[1]), (bbox[2], bbox[3]), (bbox[0], bbox[3])):
		xs.append(a * x + c * y + e)
		ys.append(b * x + d * y + f)
	bx0, bx1, by0, by1 = min(xs), max(xs), min(ys), max(ys)
	rx0, ry0 = min(rect[0], rect[2]), min(rect[1], rect[3])
	rx1, ry1 = max(rect[0], rect[2]), max(rect[1], rect[3])
	sx = (rx1 - rx0) / (bx1 - bx0) if (bx1 - bx0) else 1.0
	sy = (ry1 - ry0) / (by1 - by0) if (by1 - by0) else 1.0
	return (sx, 0.0, 0.0, sy, rx0 - bx0 * sx, ry0 - by0 * sy)


def flatten(writer) -> int:
	"""Burn every field into the page and take the form away. Returns the count.

	ONE OVERLAY PER PAGE, not one per field. Merging 126 separate overlay pages
	into a four-page I-9 — which is what the obvious loop does — copies the
	page's resource dictionary 126 times and turns a 512 KB template into a 3 MB
	file. Batching the whole page into a single content stream with a single
	XObject dictionary is the difference between 3.2 MB and 0.9 MB, measured on
	the real template.

	Afterwards there is no `/AcroForm` and there are no `/Widget` annotations, so
	`get_fields()` answers None and no viewer offers a cursor. That is the
	property the release is for: the values, the ticks and the signatures are all
	page content, and none of them can be changed without editing the page.
	"""
	from pypdf._page import PageObject
	from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

	stamped = 0
	for page in writer.pages:
		operations: list[str] = []
		xobjects = DictionaryObject()
		for index, annot in enumerate(list(page.get("/Annots") or [])):
			widget = annot.get_object()
			if widget.get("/Subtype") != "/Widget":
				continue
			appearance = widget.get("/AP")
			normal = appearance.get("/N") if appearance else None
			if normal is None:
				continue
			normal = normal.get_object()
			if "/BBox" not in normal:
				# A checkbox or radio: `/N` is a dictionary of states and `/AS`
				# names the one that is showing. An unticked box with no `/AS`
				# has nothing to draw, which is correct.
				state = widget.get("/AS")
				if state is None or state not in normal:
					continue
				normal = normal[state].get_object()
			if "/BBox" not in normal:
				continue
			# USCIS's streams omit this and pypdf's resource merger needs it.
			if "/Subtype" not in normal:
				normal[NameObject("/Subtype")] = NameObject("/Form")

			key = NameObject(f"/FlatFld{index}")
			xobjects[key] = normal.indirect_reference
			ctm = _appearance_matrix(
				[float(v) for v in normal["/BBox"]],
				[float(v) for v in (normal.get("/Matrix") or [1, 0, 0, 1, 0, 0])],
				[float(v) for v in widget["/Rect"]],
			)
			operations.append("q %.5f %.5f %.5f %.5f %.5f %.5f cm %s Do Q" % (*ctm, key))
			stamped += 1

		if not operations:
			continue
		overlay = PageObject.create_blank_page(
			width=float(page.mediabox.width), height=float(page.mediabox.height))
		overlay[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): xobjects})
		content = DecodedStreamObject()
		content.set_data("\n".join(operations).encode("ascii"))
		overlay[NameObject("/Contents")] = content
		page.merge_page(overlay)

	writer.remove_annotations(subtypes="/Widget")
	root = writer._root_object
	if "/AcroForm" in root:
		from pypdf.generic import NameObject as _Name

		del root[_Name("/AcroForm")]
	return stamped
