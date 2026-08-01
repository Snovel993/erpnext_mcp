# SPDX-License-Identifier: MIT
"""`render/qr.py` — the matrix is borrowed, the PNG is ours. v0.17.0.

The split is the point, and it is what these tests are shaped around.

THE MATRIX IS BORROWED and is barely tested here, deliberately. Reed–Solomon
over GF(256), eight candidate masks scored against four penalty rules and a BCH
format code are `segno`'s arithmetic, and a test suite that re-derived them
would be testing somebody else's library at this app's expense. What IS tested
is the contract around it: that a bench with no encoder loses ONE tool by name
with the command to fix it, and that both supported encoders are asked for the
same thing in the same way.

THE PNG IS OURS and is tested properly — the header, the quiet zone, the
scaling, the colour of a set module, and the fact that the same payload produces
the same bytes twice. `test_mobile.decode_png` reads one of these back and
compares it with a fresh encoding of the payload, which is the end-to-end claim;
this file is the unit underneath it.

THE QUIET ZONE IS NOT DECORATION. Four modules of white on every side is in the
specification, and a scanner handed a QR that runs to the edge of the image
frequently fails to find it at all — which presents to a worker in an orchard as
"the app is broken".
"""

import struct
import unittest
import zlib

from erpnext_mcp.render import qr

from .harness import frappe  # noqa: F401 - installs the double before erpnext_mcp imports

PAYLOAD = '{"url":"https://umbrel.tail4a2b.ts.net","user":"ana@example.test","token":"abc:def"}'


class TheEncoderContract(unittest.TestCase):
	def test_this_bench_has_one(self):
		"""Guard against every other test in this file passing vacuously."""
		self.assertIn(qr.encoder_name(), ("segno", "qrcode"))
		self.assertTrue(qr.available())

	def test_the_requires_sentence_names_the_libraries_and_the_command(self):
		"""'Install a QR library' is not an actionable sentence."""
		self.assertIn("segno", qr.REQUIRES)
		self.assertIn("qrcode", qr.REQUIRES)
		self.assertIn("pip install", qr.REQUIRES)

	def test_it_says_what_still_works_without_one(self):
		"""Losing the QR loses a convenience, not the flow. generate_api_token
		returns the same credential as text."""
		self.assertIn("generate_api_token", qr.REQUIRES)

	def test_the_result_reports_which_encoder_drew_it(self):
		"""Two encoders at two versions producing the same payload is exactly the
		kind of difference somebody debugging a scanner has to be able to see."""
		self.assertEqual(qr.render(PAYLOAD)["encoder"], qr.encoder_name())

	def test_an_unknown_error_level_is_refused_before_anything_is_drawn(self):
		with self.assertRaises(ValueError):
			qr.qr_matrix(PAYLOAD, error="Z")

	def test_all_four_error_levels_encode(self):
		for level in ("L", "M", "Q", "H"):
			with self.subTest(level=level):
				self.assertTrue(qr.qr_matrix(PAYLOAD, error=level))

	def test_more_error_correction_needs_at_least_as_many_modules(self):
		"""A sanity check on the borrowed half that does not re-derive it."""
		self.assertGreaterEqual(
			len(qr.qr_matrix(PAYLOAD, error="H")), len(qr.qr_matrix(PAYLOAD, error="L"))
		)


class TheMatrix(unittest.TestCase):
	def test_it_is_square_and_carries_no_quiet_zone(self):
		"""The border is added by `png_bytes`, in ONE place, so both encoders
		produce the same image for the same payload."""
		matrix = qr.qr_matrix(PAYLOAD)
		self.assertTrue(all(len(row) == len(matrix) for row in matrix))
		# A QR's top-left finder pattern starts at (0,0). A matrix carrying a
		# quiet zone would have a white corner.
		self.assertEqual(matrix[0][0], 1)

	def test_every_cell_is_zero_or_one(self):
		for row in qr.qr_matrix(PAYLOAD):
			self.assertTrue(set(row) <= {0, 1})

	def test_the_same_payload_gives_the_same_matrix(self):
		self.assertEqual(qr.qr_matrix(PAYLOAD), qr.qr_matrix(PAYLOAD))

	def test_a_different_payload_gives_a_different_one(self):
		self.assertNotEqual(qr.qr_matrix(PAYLOAD), qr.qr_matrix(PAYLOAD + " "))


class ThePNG(unittest.TestCase):
	def png(self, **kwargs):
		return qr.png_bytes(qr.qr_matrix(PAYLOAD), **kwargs)

	def header(self, data: bytes):
		(length,) = struct.unpack(">I", data[8:12])
		self.assertEqual(data[12:16], b"IHDR")
		return struct.unpack(">IIBBBBB", data[16 : 16 + length])

	def test_it_starts_with_the_png_signature(self):
		self.assertEqual(self.png()[:8], b"\x89PNG\r\n\x1a\n")

	def test_it_is_eight_bit_greyscale_with_no_interlacing(self):
		"""The simplest PNG that is still a PNG, which is what lets this app's own
		tests decode it without a third library."""
		width, height, depth, colour, compression, filter_method, interlace = self.header(self.png())
		self.assertEqual((depth, colour), (8, 0))
		self.assertEqual((compression, filter_method, interlace), (0, 0, 0))
		self.assertEqual(width, height)

	def test_the_side_is_modules_plus_two_borders_times_the_scale(self):
		matrix = qr.qr_matrix(PAYLOAD)
		data = qr.png_bytes(matrix, scale=4, border=4)
		width, _height, *_rest = self.header(data)
		self.assertEqual(width, (len(matrix) + 8) * 4)

	def test_the_quiet_zone_is_four_modules_and_stays_four(self):
		"""In the specification, and the difference between a scanner finding this
		and a worker deciding the app is broken."""
		self.assertEqual(qr.BORDER, 4)
		self.assertEqual(qr.render(PAYLOAD)["border"], 4)

	def test_it_ends_with_iend(self):
		self.assertEqual(self.png()[-8:], b"IEND\xaeB`\x82")

	def test_every_chunk_carries_a_correct_crc(self):
		"""A PNG with a bad CRC is one some readers refuse and others repair
		silently, which is the worst pair of behaviours to choose between."""
		data = self.png()
		pos = 8
		seen = []
		while pos < len(data):
			(length,) = struct.unpack(">I", data[pos : pos + 4])
			kind = data[pos + 4 : pos + 8]
			payload = data[pos + 8 : pos + 8 + length]
			(declared,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
			self.assertEqual(declared, zlib.crc32(kind + payload) & 0xFFFFFFFF, kind)
			seen.append(kind)
			pos += 12 + length
		self.assertEqual(seen, [b"IHDR", b"IDAT", b"IEND"])

	def test_a_set_module_is_black_and_the_quiet_zone_is_white(self):
		matrix = qr.qr_matrix(PAYLOAD)
		data = qr.png_bytes(matrix, scale=1, border=4)
		width, _height, *_rest = self.header(data)
		pixels = zlib.decompress(_idat(data))
		stride = width + 1

		def pixel(x, y):
			return pixels[y * stride + 1 + x]

		self.assertEqual(pixel(0, 0), 0xFF)  # quiet zone
		self.assertEqual(pixel(4, 4), 0x00)  # the finder pattern's corner
		self.assertEqual(pixel(width - 1, width - 1), 0xFF)

	def test_every_row_uses_filter_type_zero(self):
		data = self.png()
		width, height, *_rest = self.header(data)
		pixels = zlib.decompress(_idat(data))
		for row in range(height):
			with self.subTest(row=row):
				self.assertEqual(pixels[row * (width + 1)], 0)

	def test_the_same_payload_gives_byte_identical_output(self):
		"""So an operator's archived login card does not change appearance because
		somebody added a second QR library to the bench."""
		self.assertEqual(self.png(), self.png())

	def test_an_empty_matrix_is_refused_rather_than_drawn(self):
		with self.assertRaises(ValueError):
			qr.png_bytes([])

	def test_a_zero_scale_is_clamped_rather_than_producing_nothing(self):
		width, _height, *_rest = self.header(self.png(scale=0, border=0))
		self.assertEqual(width, len(qr.qr_matrix(PAYLOAD)))


class TheRenderReport(unittest.TestCase):
	def test_it_carries_everything_a_result_reports(self):
		drawn = qr.render(PAYLOAD)
		self.assertEqual(
			sorted(drawn),
			[
				"border",
				"encoder",
				"error_correction",
				"matrix",
				"modules",
				"pixels",
				"png",
				"scale",
			],
		)

	def test_pixels_agrees_with_the_png_it_returned(self):
		drawn = qr.render(PAYLOAD)
		width, height = struct.unpack(">II", drawn["png"][16:24])
		self.assertEqual((width, height), (drawn["pixels"], drawn["pixels"]))

	def test_modules_agrees_with_the_matrix_it_returned(self):
		drawn = qr.render(PAYLOAD)
		self.assertEqual(drawn["modules"], len(drawn["matrix"]))

	def test_the_default_scale_is_readable_off_a_phone_screen(self):
		"""Eight pixels a module puts the payloads this app produces somewhere
		around 350px, which scans at arm's length and base64s into a tool result
		without being rude about it."""
		self.assertEqual(qr.SCALE, 8)
		self.assertGreater(qr.render(PAYLOAD)["pixels"], 250)
		self.assertLess(len(qr.render(PAYLOAD)["png"]), 20_000)


def _idat(data: bytes) -> bytes:
	pos, out = 8, b""
	while pos < len(data):
		(length,) = struct.unpack(">I", data[pos : pos + 4])
		if data[pos + 4 : pos + 8] == b"IDAT":
			out += data[pos + 8 : pos + 8 + length]
		pos += 12 + length
	return out
