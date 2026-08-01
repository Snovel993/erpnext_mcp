# SPDX-License-Identifier: MIT
"""QR codes, and the PNG they go in. v0.17.0.

TWO HALVES, AND ONLY ONE OF THEM IS BORROWED.

The **matrix** — which modules are black — is error-correcting coding with
Reed–Solomon, Galois field arithmetic, eight candidate masks scored against four
penalty rules, and a format-information BCH code. Reimplementing that would be
four hundred lines of arithmetic whose only test is "does a phone read it", and
this app has no business owning it. So it is borrowed, from `segno` first and
`qrcode` second, and a bench with neither loses the QR tool BY NAME with the pip
command to fix it — the same contract the geospatial tools have had since
v0.12.0.

The **PNG** is written here, in thirty lines of `zlib` and `struct`, and that is
deliberate rather than lazy. `segno` writes PNG; `qrcode` writes PNG only with
Pillow, which is a large dependency to add for a two-kilobyte black-and-white
image. Writing it here means the output is byte-identical whichever encoder
produced the matrix, that the format is one this app's own tests can decode
without a third library, and that adding `qrcode` to a bench does not silently
change what an operator's archived login card looks like.

WHY 8-BIT GREYSCALE AND NOT 1-BIT. A 1-bit PNG is smaller and is a bit-packing
exercise with an off-by-one at every row that is not a multiple of eight. At
this size the difference is under two kilobytes, and a format a reader can
verify by eye in a hex dump is worth more than the saving.

THE QUIET ZONE IS NOT OPTIONAL. Four modules of white on every side is in the
specification, and a scanner given a QR that runs to the edge of the image
frequently fails to find it at all — which presents to a worker in an orchard as
"the app is broken". `BORDER` is four and there is a test that it is.
"""

from __future__ import annotations

import struct
import zlib

#: Modules of quiet zone on each side. Four is the specification's minimum and
#: this app does not go below it. See the module docstring.
BORDER = 4

#: Pixels per module. Eight gives a ~350px image for the payloads this app
#: produces, which is big enough to scan off a phone screen held at arm's length
#: and small enough to base64 into a tool result without being rude about it.
SCALE = 8

#: What an operator is told when neither encoder is importable. Named libraries,
#: exact command — the same shape as the geospatial `requires` string, because
#: "install a QR library" is not an actionable sentence.
REQUIRES = (
	"a QR encoder: either `segno` (pure Python, no other dependency) or `qrcode`. "
	"Install one into the bench's environment with `./env/bin/pip install segno` and restart. "
	"Everything else in the mobile login flow works without it — generate_api_token returns "
	"the same credential as text, and the QR is only a way of typing it in fewer keystrokes."
)


def available() -> bool:
	"""Is either encoder importable? Never raises."""
	return encoder_name() != ""


def encoder_name() -> str:
	"""Which encoder this bench will use: 'segno', 'qrcode' or ''.

	Reported in every result that produces a QR, because two encoders producing
	the same payload at different versions is exactly the kind of difference
	somebody debugging a scanner needs to be able to see.
	"""
	try:
		import segno  # noqa: F401

		return "segno"
	except Exception:
		pass
	try:
		import qrcode  # noqa: F401

		return "qrcode"
	except Exception:
		return ""


def qr_matrix(text: str, error: str = "M") -> list:
	"""The QR module matrix for `text`: a list of rows of 0/1, NO quiet zone.

	`error` is the error-correction level — L, M, Q or H. M is the default and
	is the right one here: it survives about fifteen per cent of the image being
	obscured, which covers a thumb over the corner of a printed card, and it
	keeps the module count low enough that a phone screen renders it crisply.

	Raises RuntimeError with `REQUIRES` when no encoder is importable. The
	caller turns that into a ToolError; this module does not import the app's
	error type, because a renderer that depended on the tool layer could not be
	used from anywhere else.
	"""
	text = str(text)
	level = str(error or "M").strip().upper()
	if level not in ("L", "M", "Q", "H"):
		raise ValueError(f"error must be one of L, M, Q, H — not {error!r}")

	name = encoder_name()
	if name == "segno":
		import segno

		code = segno.make(text, error=level.lower(), micro=False)
		# segno's `matrix` is rows of ints with no quiet zone, which is exactly
		# the contract here. Materialised to lists so the two branches return
		# the same type and a caller can index into it.
		return [[int(bool(module)) for module in row] for row in code.matrix]

	if name == "qrcode":
		import qrcode

		levels = {
			"L": qrcode.constants.ERROR_CORRECT_L,
			"M": qrcode.constants.ERROR_CORRECT_M,
			"Q": qrcode.constants.ERROR_CORRECT_Q,
			"H": qrcode.constants.ERROR_CORRECT_H,
		}
		# border=0 because the quiet zone is added by `png_bytes`, in one place,
		# so both encoders produce the same image for the same payload.
		code = qrcode.QRCode(error_correction=levels[level], border=0, box_size=1)
		code.add_data(text)
		code.make(fit=True)
		return [[int(bool(module)) for module in row] for row in code.modules]

	raise RuntimeError(REQUIRES)


def png_bytes(matrix: list, scale: int = SCALE, border: int = BORDER) -> bytes:
	"""Render a module matrix as an 8-bit greyscale PNG. Pure standard library.

	Black modules are 0x00, everything else — including the quiet zone — is
	0xFF. No palette, no alpha, no interlacing: the simplest PNG that is still a
	PNG, which is what makes it decodable by the thirty lines in the test that
	proves this image carries the payload it claims to.
	"""
	scale = max(1, int(scale))
	border = max(0, int(border))
	if not matrix or not matrix[0]:
		raise ValueError("an empty matrix is not a QR code")

	modules = len(matrix)
	side = (modules + 2 * border) * scale

	quiet_row = b"\x00" + b"\xff" * side
	rows = [quiet_row] * (border * scale)
	pad = b"\xff" * (border * scale)
	for line in matrix:
		pixels = bytearray()
		for module in line:
			pixels += (b"\x00" if module else b"\xff") * scale
		row = b"\x00" + pad + bytes(pixels) + pad
		rows.extend([row] * scale)
	rows.extend([quiet_row] * (border * scale))

	raw = b"".join(rows)
	header = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)  # 8-bit greyscale
	return b"".join(
		[
			b"\x89PNG\r\n\x1a\n",
			_chunk(b"IHDR", header),
			_chunk(b"IDAT", zlib.compress(raw, 9)),
			_chunk(b"IEND", b""),
		]
	)


def _chunk(kind: bytes, payload: bytes) -> bytes:
	return (
		struct.pack(">I", len(payload))
		+ kind
		+ payload
		+ struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
	)


def render(text: str, error: str = "M", scale: int = SCALE, border: int = BORDER) -> dict:
	"""The matrix and the PNG together, with the facts a caller reports.

	Returned as a dict rather than a tuple because every caller wants the
	dimensions and the encoder name too, and a four-tuple is a thing people
	unpack in the wrong order.
	"""
	matrix = qr_matrix(text, error=error)
	png = png_bytes(matrix, scale=scale, border=border)
	side = (len(matrix) + 2 * max(0, int(border))) * max(1, int(scale))
	return {
		"matrix": matrix,
		"png": png,
		"modules": len(matrix),
		"pixels": side,
		"scale": max(1, int(scale)),
		"border": max(0, int(border)),
		"error_correction": str(error or "M").strip().upper(),
		"encoder": encoder_name(),
	}
