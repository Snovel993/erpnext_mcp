# SPDX-License-Identifier: MIT
"""The badge as a wallet pass: the archive, the signature, and the tool. v0.53.0.

WHAT THIS FILE IS ABOUT. `generate_employee_badge_qr` has minted `ETC-0001` and
drawn its QR since v0.50.0, and what came back was a PNG somebody had to print.
v0.53.0 adds the delivery that needs no laminator: an Apple Wallet `.pkpass` the
foreman AirDrops off the handset, and a Google Wallet save link for the Android
half. `test_badges.py` still owns minting, the register and the refusals; this
file owns the two pass formats and the one tool that builds them.

SEVEN CLAIMS.

1. `TheArchive` — a `.pkpass` is a zip whose `manifest.json` holds a correct
   SHA-1 for every other member, built in a fixed order with fixed timestamps so
   the same badge produces the same bytes twice.

2. `ThePassContent` — `pass.json` carries the badge ID as the serial AND as both
   barcode keys, and nothing else identifying. The pass is not a second
   credential; it is the same one rendered somewhere else.

3. `SigningIt` — a real certificate produces a real detached PKCS#7 over the
   manifest, from a `.p12` or from a PEM pair. Every way signing can fail leaves
   a COMPLETE, UNSIGNED pass and a sentence, never an exception and never a
   missing badge.

4. `TheImages` — a photograph becomes Apple's 90 and 180 pixel thumbnails, a
   logo becomes the icons, and an unreadable image becomes initials plus a
   warning rather than a broken pass. `icon.png` is present whatever happens,
   because a `.pkpass` without one is not a pass.

5. `GoogleWallet` — the object shape, a save link whose JWT verifies against the
   service account's own key, and the asymmetry that matters: Google FETCHES
   images, so a private photograph is named in a warning rather than written as
   a URL that 403s.

6. `TheTool` — it issues or reuses the badge through `badges.py`'s own path, is
   idempotent, files the `.pkpass` privately against the Employee, replaces the
   one it supersedes rather than stacking copies, and refuses what the QR tool
   refuses.

7. `TheMobileRoute` — the handset gets the BYTES, because it cannot fetch a
   private `file_url` through a door that authenticates with `X-FarmOps-Token`.
"""

from __future__ import annotations

import base64
import datetime
import io
import json
import os
import tempfile
import unittest
import zipfile

import frappe

from erpnext_mcp import wallet
from erpnext_mcp.api import mobile as mobile_api

from .fixtures import MAIN, MAIN_ABBR, OTHER, SeededTestCase, install_hrms
from .harness import STORE
from .test_api_mobile import WORKER, MobileAPITestCase

BADGE_DOCTYPE = "Bucket Log Badge Map"

EMP = "HR-EMP-00001"
EMP_NAME = "Ana Reyes"
SECOND = "HR-EMP-00002"
LEAVER = "HR-EMP-00009"

#: Every switch these tests touch. The pass tool is MUTATING and ships OFF; the
#: QR issuer is listed because the pass tool mints through its own path and a
#: test that only turned one on would be testing a half-wired site.
ON = {
	f"allow_{name}": 1
	for name in (
		"generate_employee_badge_pass",
		"generate_employee_badge_qr",
		"resolve_badge",
		"link_badge_to_employee",
	)
}

CARD = {
	"badge_id": "ETC-0001",
	"employee": EMP,
	"employee_name": EMP_NAME,
	"employee_number": "E-7",
	"designation": "Picker",
	"company": "Example Trading Co",
	"initials": "AR",
	"issued_on": "2026-08-08",
	"photo_png": b"",
	"logo_png": b"",
	"public_photo_url": "",
	"public_logo_url": "",
}


def _png(width, height, color=(200, 60, 60), fmt="PNG", mode="RGB"):
	"""One real image, for the paths that decode one."""
	from PIL import Image

	buffer = io.BytesIO()
	Image.new(mode, (width, height), color).save(buffer, format=fmt)
	return buffer.getvalue()


def _members(payload: bytes) -> dict:
	with zipfile.ZipFile(io.BytesIO(payload)) as archive:
		return {name: archive.read(name) for name in archive.namelist()}


def _self_signed(directory: str, password: bytes | None = None) -> dict:
	"""A throwaway Pass Type ID certificate, in both shapes an operator arrives with.

	NOT A WORKING APPLE CERTIFICATE and not pretending to be — Wallet would
	refuse a pass signed with it, exactly as `wallet.py`'s header says. What it
	proves is that the SIGNING PATH produces a real detached PKCS#7 over the real
	manifest, which is the half of "will this work with Tim's certificate" that
	can be tested before Tim has one.
	"""
	from cryptography import x509
	from cryptography.hazmat.primitives import hashes, serialization
	from cryptography.hazmat.primitives.asymmetric import rsa
	from cryptography.hazmat.primitives.serialization import pkcs12
	from cryptography.x509.oid import NameOID

	key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
	subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Pass Type ID: pass.test.badge")])
	start = datetime.datetime(2026, 1, 1)
	certificate = (
		x509.CertificateBuilder()
		.subject_name(subject)
		.issuer_name(subject)
		.public_key(key.public_key())
		.serial_number(x509.random_serial_number())
		.not_valid_before(start)
		.not_valid_after(start + datetime.timedelta(days=365))
		.sign(key, hashes.SHA256())
	)

	paths = {}
	paths["cert_pem"] = os.path.join(directory, "cert.pem")
	paths["key_pem"] = os.path.join(directory, "key.pem")
	paths["p12"] = os.path.join(directory, "pass.p12")
	with open(paths["cert_pem"], "wb") as handle:
		handle.write(certificate.public_bytes(serialization.Encoding.PEM))
	with open(paths["key_pem"], "wb") as handle:
		handle.write(
			key.private_bytes(
				serialization.Encoding.PEM,
				serialization.PrivateFormat.PKCS8,
				serialization.NoEncryption(),
			)
		)
	encryption = serialization.BestAvailableEncryption(password) if password else serialization.NoEncryption()
	with open(paths["p12"], "wb") as handle:
		handle.write(pkcs12.serialize_key_and_certificates(b"pass", key, certificate, None, encryption))
	paths["key"] = key
	paths["certificate"] = certificate
	return paths


# ── 1. The archive ───────────────────────────────────────────────────────


class TheArchive(unittest.TestCase):
	"""A .pkpass is a zip, and `manifest.json` is what makes it tamper-evident."""

	def setUp(self):
		self.built = wallet.build_pkpass(CARD, wallet.apple_config({}))
		self.files = _members(self.built["pkpass"])

	def test_it_is_a_zip_holding_the_mandatory_members(self):
		self.assertIn("pass.json", self.files)
		self.assertIn("manifest.json", self.files)
		# icon.png is not optional: Wallet rejects the ARCHIVE without one rather
		# than showing a blank square.
		self.assertIn("icon.png", self.files)

	def test_the_manifest_holds_a_correct_sha1_for_every_other_member(self):
		"""SHA-1 IS APPLE'S FORMAT, NOT THIS APP'S SECURITY CHOICE — see
		`wallet.manifest_for`. A stronger digest here produces a pass Wallet
		refuses; the strength lives in the SHA-256 signature over this file."""
		import hashlib

		manifest = json.loads(self.files["manifest.json"].decode("utf-8"))
		self.assertEqual(set(manifest), set(self.files) - {"manifest.json", "signature"})
		for member, digest in manifest.items():
			with self.subTest(member=member):
				self.assertEqual(hashlib.sha1(self.files[member]).hexdigest(), digest)

	def test_the_same_badge_twice_produces_the_same_bytes(self):
		"""Deterministic on purpose: fixed member order, fixed timestamps. It is
		what lets the tool say whether a regenerated pass actually differs from
		the one already attached, rather than replacing it every time."""
		again = wallet.build_pkpass(CARD, wallet.apple_config({}))
		self.assertEqual(again["pkpass"], self.built["pkpass"])

	def test_an_unconfigured_site_gets_a_complete_pass_with_no_signature_member(self):
		"""THE HONEST FAILURE. A self-signed blob with a `signature` in it fails
		in Wallet just as hard while looking upstream like it worked."""
		self.assertNotIn("signature", self.files)
		self.assertFalse(self.built["signed"])
		reason = " ".join(self.built["warnings"])
		self.assertIn("UNSIGNED", reason)
		# The keys, not the concept. "Configure the certificate" is not actionable.
		self.assertIn("apple_wallet_pass_type_identifier", reason)
		self.assertIn("apple_wallet_certificate", reason)

	def test_the_placeholder_identifiers_are_visibly_placeholders(self):
		content = json.loads(self.files["pass.json"].decode("utf-8"))
		self.assertEqual(content["passTypeIdentifier"], wallet.PLACEHOLDER_PASS_TYPE_ID)
		self.assertEqual(content["teamIdentifier"], wallet.PLACEHOLDER_TEAM_ID)
		self.assertIn("example", content["passTypeIdentifier"])


# ── 2. What the pass says ────────────────────────────────────────────────


class ThePassContent(unittest.TestCase):
	def setUp(self):
		self.content = wallet.build_pass_json(CARD, wallet.apple_config({}))

	def test_the_barcode_is_the_badge_id_and_nothing_else(self):
		"""THE PASS IS NOT A SECOND CREDENTIAL. A bin trailer scanning a phone
		and one scanning a laminated card produce the identical string."""
		self.assertEqual(self.content["barcodes"][0]["message"], "ETC-0001")
		self.assertEqual(self.content["serialNumber"], "ETC-0001")

	def test_it_declares_the_barcode_twice_for_the_devices_that_need_each(self):
		"""`barcodes` is what every iOS since 9 reads; `barcode` is the
		deprecated key an older device still needs. Apple's own guidance is both."""
		self.assertEqual(self.content["barcode"]["message"], self.content["barcodes"][0]["message"])
		self.assertEqual(self.content["barcode"]["format"], "PKBarcodeFormatQR")

	def test_the_badge_id_is_printed_under_the_symbol_as_well(self):
		"""Every scanner eventually fails. A badge nobody can read out over a
		radio is a picker whose buckets go unattributed — the same reason
		`badges._print_spec` puts a caption on the printed card."""
		self.assertEqual(self.content["barcodes"][0]["altText"], "ETC-0001")

	def test_the_encoding_is_the_one_the_format_specifies(self):
		self.assertEqual(self.content["barcodes"][0]["messageEncoding"], "iso-8859-1")

	def test_it_carries_what_the_physical_card_carries(self):
		generic = self.content[wallet.PASS_STYLE]
		self.assertEqual(generic["primaryFields"][0]["value"], EMP_NAME)
		values = [field["value"] for field in generic["secondaryFields"]]
		self.assertIn("ETC-0001", values)
		self.assertIn("Picker", values)
		self.assertEqual(self.content["logoText"], "Example Trading Co")

	def test_sharing_is_prohibited_because_a_shared_badge_is_two_people_scanning(self):
		self.assertTrue(self.content["sharingProhibited"])

	def test_it_is_a_generic_pass_rather_than_an_event_ticket(self):
		"""`eventTicket` and `boardingPass` carry travel semantics Wallet lays
		out around and a badge has no value for."""
		self.assertIn("generic", self.content)
		self.assertNotIn("eventTicket", self.content)

	def test_the_crew_and_the_camp_reach_the_phone_too(self):
		"""v0.103.0. The printed card gained a crew line and a camp-and-cabin
		line; the pass a worker carries instead of the card carries the same
		three facts. Role and crew read together and sit side by side; the camp
		sits with the company, which is the other answer to "where does this
		person belong"."""
		card = dict(CARD, crew="Rosa Ramirez", housing="Mill Creek \u00b7 Cabin 7")
		content = wallet.build_pass_json(card, wallet.apple_config({}))
		generic = content[wallet.PASS_STYLE]
		self.assertIn("Rosa Ramirez", [field["value"] for field in generic["secondaryFields"]])
		self.assertIn("Crew", [field["label"] for field in generic["secondaryFields"]])
		auxiliary = generic["auxiliaryFields"]
		self.assertIn("Mill Creek \u00b7 Cabin 7", [field["value"] for field in auxiliary])
		self.assertIn("Camp", [field["label"] for field in auxiliary])

	def test_a_pass_for_somebody_the_site_records_neither_for_shows_neither(self):
		"""THE NEGATIVE CONTROL. A labelled blank on a phone reads as a system
		that lost the answer."""
		content = wallet.build_pass_json(dict(CARD), wallet.apple_config({}))
		generic = content[wallet.PASS_STYLE]
		self.assertNotIn("Crew", [field["label"] for field in generic["secondaryFields"]])
		self.assertEqual([field["label"] for field in generic["auxiliaryFields"]], ["Company"])

	def test_a_badge_with_no_designation_still_produces_a_pass(self):
		card = dict(CARD, designation="")
		content = wallet.build_pass_json(card, wallet.apple_config({}))
		labels = [field["label"] for field in content[wallet.PASS_STYLE]["secondaryFields"]]
		self.assertEqual(labels, ["Badge"])


# ── 3. Signing ───────────────────────────────────────────────────────────


class SigningIt(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.mkdtemp()
		self.addCleanup(self._cleanup)
		self.certificate = _self_signed(self.directory)

	def _cleanup(self):
		import shutil

		shutil.rmtree(self.directory, ignore_errors=True)

	def _config(self, **overrides):
		conf = {
			"apple_wallet_pass_type_identifier": "pass.test.badge",
			"apple_wallet_team_identifier": "ABCDE12345",
			"apple_wallet_certificate": self.certificate["cert_pem"],
			"apple_wallet_private_key": self.certificate["key_pem"],
		}
		conf.update(overrides)
		return wallet.apple_config(conf)

	def test_a_pem_pair_signs_the_pass(self):
		built = wallet.build_pkpass(CARD, self._config())
		self.assertTrue(built["signed"], built["warnings"])
		self.assertEqual(built["warnings"], [])
		self.assertIn("signature", _members(built["pkpass"]))

	def test_the_signature_is_a_detached_pkcs7_carrying_the_signing_certificate(self):
		"""What Wallet verifies. DER, detached — the manifest is already in the
		archive — and carrying the chain, because Wallet builds the chain from
		what the signature holds rather than going and fetching it."""
		from cryptography.hazmat.primitives.serialization import pkcs7

		built = wallet.build_pkpass(CARD, self._config())
		signature = _members(built["pkpass"])["signature"]
		self.assertTrue(signature.startswith(b"\x30\x82"), "DER SEQUENCE")
		certificates = pkcs7.load_der_pkcs7_certificates(signature)
		self.assertEqual(certificates[0].subject, self.certificate["certificate"].subject)

	def test_the_signature_is_detached_and_the_manifest_it_covers_is_in_the_archive(self):
		"""DETACHED MEANS THE MANIFEST IS NOT INSIDE THE SIGNATURE. A PKCS#7 that
		carried its own copy is not the pass format, and Wallet verifies the
		archive's `manifest.json` against the signature over it — so the two have
		to be the same bytes. The `Binary` option is what keeps them the same:
		without it S/MIME rewrites every newline before hashing."""
		import hashlib

		built = wallet.build_pkpass(CARD, self._config())
		files = _members(built["pkpass"])
		manifest = files["manifest.json"]
		self.assertEqual(manifest, json.dumps(built["manifest"], indent="\t", sort_keys=True).encode())
		# Detached: the content is absent from the signature, so the manifest's
		# own bytes do not appear inside it.
		self.assertNotIn(manifest, files["signature"])
		# And the manifest still describes the archive it travelled in.
		for member, digest in built["manifest"].items():
			self.assertEqual(hashlib.sha1(files[member]).hexdigest(), digest)

	def test_a_p12_signs_it_too_because_that_is_what_keychain_exports(self):
		built = wallet.build_pkpass(CARD, self._config(apple_wallet_certificate=self.certificate["p12"]))
		self.assertTrue(built["signed"], built["warnings"])

	def test_a_p12_with_a_password_signs_when_given_the_password(self):
		protected = _self_signed(tempfile.mkdtemp(), password=b"hunter2")
		built = wallet.build_pkpass(
			CARD,
			self._config(
				apple_wallet_certificate=protected["p12"],
				apple_wallet_certificate_password="hunter2",
			),
		)
		self.assertTrue(built["signed"], built["warnings"])

	def test_the_wrong_password_leaves_a_complete_unsigned_pass_and_a_sentence(self):
		"""A FOREMAN IN A YARD CANNOT FIX A CERTIFICATE PATH. What they need is
		the badge; what the operator needs is the reason."""
		built = wallet.build_pkpass(
			CARD,
			self._config(
				apple_wallet_certificate=self.certificate["p12"], apple_wallet_certificate_password="wrong"
			),
		)
		self.assertFalse(built["signed"])
		self.assertNotIn("signature", _members(built["pkpass"]))
		self.assertIn("could not be signed", " ".join(built["warnings"]))
		# The pass itself is untouched by the failure.
		self.assertIn("pass.json", _members(built["pkpass"]))

	def test_a_certificate_path_that_moved_is_named_rather_than_raised(self):
		built = wallet.build_pkpass(CARD, self._config(apple_wallet_certificate="/nowhere/pass.p12"))
		self.assertFalse(built["signed"])
		self.assertIn("/nowhere/pass.p12", " ".join(built["warnings"]))

	def test_a_wwdr_intermediate_is_included_in_the_chain_when_configured(self):
		from cryptography.hazmat.primitives.serialization import pkcs7

		built = wallet.build_pkpass(
			CARD, self._config(apple_wallet_wwdr_certificate=self.certificate["cert_pem"])
		)
		self.assertTrue(built["signed"], built["warnings"])
		certificates = pkcs7.load_der_pkcs7_certificates(_members(built["pkpass"])["signature"])
		self.assertGreaterEqual(len(certificates), 1)

	def test_a_certificate_with_no_pass_type_id_does_not_count_as_configured(self):
		"""All three or none: a certificate signing a pass whose identifier it
		does not cover is refused by Wallet exactly as hard as an unsigned one."""
		config = self._config(apple_wallet_pass_type_identifier="")
		self.assertFalse(config["configured"])
		self.assertFalse(wallet.build_pkpass(CARD, config)["signed"])


# ── 4. Images ────────────────────────────────────────────────────────────


class TheImages(unittest.TestCase):
	def _sizes(self, card):
		from PIL import Image

		files = _members(wallet.build_pkpass(card, wallet.apple_config({}))["pkpass"])
		return {
			name: Image.open(io.BytesIO(payload)).size
			for name, payload in files.items()
			if name.endswith(".png")
		}

	def test_the_photograph_becomes_apples_thumbnail_at_both_scales(self):
		card = dict(CARD, photo_png=_png(600, 900))
		sizes = self._sizes(card)
		self.assertEqual(sizes["thumbnail.png"], (90, 90))
		self.assertEqual(sizes["thumbnail@2x.png"], (180, 180))

	def test_a_jpeg_headshot_is_converted_rather_than_refused(self):
		"""`Employee.image` is whatever was uploaded, and a phone uploads JPEG.
		A pass member has to be a PNG."""
		card = dict(CARD, photo_png=_png(400, 400, fmt="JPEG"))
		files = _members(wallet.build_pkpass(card, wallet.apple_config({}))["pkpass"])
		self.assertTrue(files["thumbnail.png"].startswith(b"\x89PNG\r\n\x1a\n"))

	def test_the_logo_becomes_the_logo_and_the_icons(self):
		card = dict(CARD, logo_png=_png(400, 120))
		sizes = self._sizes(card)
		self.assertEqual(sizes["logo.png"], (160, 50))
		self.assertEqual(sizes["logo@2x.png"], (320, 100))
		self.assertEqual(sizes["icon.png"], (29, 29))

	def test_a_square_logo_is_letterboxed_into_the_wide_box_not_cropped_to_it(self):
		"""THE ONE THAT REGRESSES SILENTLY. `logo.png` is 160x50 — a 3.2:1
		letterbox — and a farm's mark is usually square. Cropping a square logo to
		FILL that box keeps a horizontal band through the middle and throws the
		name away, which for most marks is unrecognisable. Padded, it reads.

		Checked on the pixels: with a square red logo, contain leaves the pass's
		own background colour at the left and right edges and the logo in the
		middle. Cover would make every pixel red."""
		from PIL import Image

		card = dict(CARD, logo_png=_png(300, 300, color=(200, 60, 60)))
		files = _members(wallet.build_pkpass(card, wallet.apple_config({}))["pkpass"])
		logo = Image.open(io.BytesIO(files["logo.png"])).convert("RGB")
		self.assertEqual(logo.size, (160, 50))
		self.assertEqual(logo.getpixel((80, 25)), (200, 60, 60), "the mark is in the middle")
		# The default background is the pass's farm green, not the logo.
		self.assertEqual(logo.getpixel((2, 25)), (31, 77, 43), "padding, not a crop")

	def test_a_wide_wordmark_is_letterboxed_into_the_square_icon_too(self):
		"""The same rule in the other direction: a 400x120 wordmark cropped
		square keeps about three letters of it."""
		from PIL import Image

		card = dict(CARD, logo_png=_png(400, 120, color=(200, 60, 60)))
		files = _members(wallet.build_pkpass(card, wallet.apple_config({}))["pkpass"])
		icon = Image.open(io.BytesIO(files["icon@3x.png"])).convert("RGB")
		self.assertEqual(icon.size, (87, 87))
		self.assertEqual(icon.getpixel((43, 43)), (200, 60, 60))
		self.assertEqual(icon.getpixel((43, 2)), (31, 77, 43), "padding above and below")

	def test_the_photograph_is_cropped_to_fill_because_a_face_is_the_point(self):
		"""The OPPOSITE choice, deliberately: a headshot letterboxed into 90x90
		is a face a third of the size it could be, and the thing a foreman looks
		at across a bin trailer is the face."""
		from PIL import Image

		card = dict(CARD, photo_png=_png(600, 900, color=(200, 60, 60)))
		files = _members(wallet.build_pkpass(card, wallet.apple_config({}))["pkpass"])
		thumbnail = Image.open(io.BytesIO(files["thumbnail.png"])).convert("RGB")
		for point in ((45, 45), (2, 2), (87, 87)):
			self.assertEqual(thumbnail.getpixel(point), (200, 60, 60), "no padding anywhere")

	def test_an_unreadable_photograph_becomes_initials_and_a_warning(self):
		"""A pass with initials where a face should be is a WORKING pass. The
		same fallback the printed card has had since v0.50.0."""
		built = wallet.build_pkpass(dict(CARD, photo_png=b"not an image"), wallet.apple_config({}))
		self.assertIn("thumbnail.png", _members(built["pkpass"]))
		self.assertIn("photograph could not be used", " ".join(built["warnings"]))

	def test_there_is_always_an_icon_because_a_pkpass_without_one_is_not_a_pass(self):
		for card in (CARD, dict(CARD, logo_png=b"junk"), dict(CARD, logo_png=_png(400, 120))):
			with self.subTest(logo=bool(card.get("logo_png"))):
				self.assertIn(
					"icon.png", _members(wallet.build_pkpass(card, wallet.apple_config({}))["pkpass"])
				)

	def test_no_logo_means_no_logo_member_rather_than_an_empty_one(self):
		self.assertNotIn("logo.png", _members(wallet.build_pkpass(CARD, wallet.apple_config({}))["pkpass"]))

	def test_the_stdlib_fallback_writes_a_real_png_without_pillow(self):
		"""`solid_png` is what keeps `icon.png` possible on a bench with no
		Pillow — the same trade `render/qr.py` makes with the same writer."""
		from PIL import Image

		payload = wallet.solid_png(29, 29, (31, 77, 43))
		self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
		image = Image.open(io.BytesIO(payload))
		self.assertEqual(image.size, (29, 29))
		self.assertEqual(image.convert("RGB").getpixel((14, 14)), (31, 77, 43))

	def test_a_colour_typed_wrong_gives_the_default_rather_than_an_exception(self):
		"""A badge in the wrong green beats a hiring day with no passes on it."""
		self.assertEqual(wallet._rgb("#1f4d2b"), (31, 77, 43))
		self.assertEqual(wallet._rgb("rgb(1,2,3)"), (1, 2, 3))
		self.assertEqual(wallet._rgb("chartreuse"), (31, 77, 43))
		self.assertEqual(wallet._rgb(None), (31, 77, 43))


# ── 5. Google Wallet ─────────────────────────────────────────────────────


class GoogleWallet(unittest.TestCase):
	def setUp(self):
		self.directory = tempfile.mkdtemp()
		self.addCleanup(self._cleanup)
		self.certificate = _self_signed(self.directory)
		self.account_path = os.path.join(self.directory, "service-account.json")
		from cryptography.hazmat.primitives import serialization

		pem = self.certificate["key"].private_bytes(
			serialization.Encoding.PEM,
			serialization.PrivateFormat.PKCS8,
			serialization.NoEncryption(),
		)
		with open(self.account_path, "w") as handle:
			json.dump(
				{"client_email": "badge@farm.iam.gserviceaccount.com", "private_key": pem.decode()}, handle
			)

	def _cleanup(self):
		import shutil

		shutil.rmtree(self.directory, ignore_errors=True)

	def _config(self, **overrides):
		conf = {
			"google_wallet_issuer_id": "3388000000012345678",
			"google_wallet_service_account": self.account_path,
		}
		conf.update(overrides)
		return wallet.google_config(conf)

	def test_an_unconfigured_site_still_gets_the_object_but_no_link(self):
		"""The object is the thing an operator pastes into Google's Pass Builder
		to check the shape before committing to a service account."""
		built = wallet.build_google_pass(CARD, wallet.google_config({}))
		self.assertTrue(built["object"])
		self.assertFalse(built["save_url"])
		self.assertIn("google_wallet_issuer_id", " ".join(built["warnings"]))

	def test_the_object_carries_the_badge_as_its_barcode(self):
		built = wallet.build_google_pass(CARD, self._config())
		self.assertEqual(
			built["object"]["barcode"], {"type": "QR_CODE", "value": "ETC-0001", "alternateText": "ETC-0001"}
		)
		self.assertEqual(built["object"]["id"], "3388000000012345678.ETC-0001")
		self.assertEqual(built["class_id"], "3388000000012345678.farm_employee_badge")

	def test_the_save_link_is_a_jwt_that_verifies_against_the_service_account_key(self):
		from cryptography.hazmat.primitives import hashes
		from cryptography.hazmat.primitives.asymmetric import padding

		built = wallet.build_google_pass(CARD, self._config(), issued_at=1754600000)
		self.assertTrue(built["signed"], built["warnings"])
		self.assertTrue(built["save_url"].startswith(wallet.GOOGLE_SAVE_PREFIX))

		token = built["save_url"][len(wallet.GOOGLE_SAVE_PREFIX) :]
		header, payload, signature = token.split(".")

		def unpad(segment):
			return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))

		self.certificate["key"].public_key().verify(
			unpad(signature), f"{header}.{payload}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256()
		)
		claims = json.loads(unpad(payload))
		self.assertEqual(claims["aud"], "google")
		self.assertEqual(claims["typ"], "savetowallet")
		self.assertEqual(claims["iss"], "badge@farm.iam.gserviceaccount.com")
		self.assertEqual(claims["iat"], 1754600000)
		self.assertEqual(claims["payload"]["genericObjects"][0]["id"], built["object_id"])

	def test_a_public_image_url_goes_on_the_object(self):
		card = dict(CARD, logo_png=b"x", public_logo_url="https://erp.example.com/files/logo.png")
		built = wallet.build_google_pass(card, self._config())
		self.assertEqual(
			built["object"]["logo"]["sourceUri"]["uri"], "https://erp.example.com/files/logo.png"
		)

	def test_a_private_photograph_is_named_in_a_warning_rather_than_written(self):
		"""THE ASYMMETRY THAT MATTERS. Google FETCHES images over the public
		internet; a private Frappe File is a 403 to it. The Apple pass carries
		the same photograph inside the archive and is unaffected."""
		card = dict(CARD, photo_png=b"real bytes", public_photo_url="")
		built = wallet.build_google_pass(card, self._config())
		self.assertNotIn("heroImage", built["object"])
		self.assertIn("403", " ".join(built["warnings"]))

	def test_a_service_account_file_that_is_not_one_is_refused_by_name(self):
		wrong = os.path.join(self.directory, "oauth-client.json")
		with open(wrong, "w") as handle:
			json.dump({"installed": {"client_id": "x"}}, handle)
		built = wallet.build_google_pass(CARD, self._config(google_wallet_service_account=wrong))
		self.assertFalse(built["signed"])
		self.assertIn("not a Google service account key", " ".join(built["warnings"]))

	def test_a_legacy_uuid_badge_is_sanitised_for_googles_id_but_not_for_the_barcode(self):
		"""The badge the pass CARRIES is untouched — the Google-side key is a
		different thing from the scanned payload."""
		card = dict(CARD, badge_id="3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2")
		built = wallet.build_google_pass(card, self._config())
		self.assertEqual(built["object"]["barcode"]["value"], "3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2")
		self.assertTrue(built["object_id"].endswith("3f2c9a10-5b77-4a6e-9c31-7a51d0e4b8f2"))


# ── 6. The tool ──────────────────────────────────────────────────────────


class WalletToolTestCase(SeededTestCase):
	"""A site with two active pickers, one leaver, and the pass switch on."""

	def setUp(self):
		super().setUp()
		install_hrms()
		self.configure(enabled=1, public_url="https://erp.example.test", **ON)
		STORE.seed(
			"Employee",
			[
				{
					"name": EMP,
					"employee_name": EMP_NAME,
					"first_name": "Ana",
					"last_name": "Reyes",
					"company": MAIN,
					"status": "Active",
					"designation": "Picker",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": SECOND,
					"employee_name": "Marco Vega",
					"first_name": "Marco",
					"last_name": "Vega",
					"company": MAIN,
					"status": "Active",
					"designation": "Picker",
					"date_of_joining": "2025-01-15",
				},
				{
					"name": LEAVER,
					"employee_name": "Sam Ortiz",
					"company": MAIN,
					"status": "Left",
					"date_of_joining": "2024-05-01",
				},
			],
		)

	def build(self, employee=EMP, **overrides):
		payload = {"employee": employee, "company": MAIN}
		payload.update(overrides)
		return self.tool_data("generate_employee_badge_pass", payload)

	def attach_photo(self, employee=EMP, private=True):
		"""Put a real headshot on the Employee, the way `set_employee_photo` does."""
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"{employee}-photo.png",
				"is_private": 1 if private else 0,
				"content": _png(300, 300),
				"attached_to_doctype": "Employee",
				"attached_to_name": employee,
			}
		).insert()
		frappe.db.set_value("Employee", employee, "image", doc.get("file_url"))
		return doc.get("file_url")

	def pass_files(self, employee=EMP):
		return [
			row
			for row in STORE.rows("File")
			if row.get("attached_to_name") == employee and str(row.get("file_name") or "").endswith(".pkpass")
		]


class TheTool(WalletToolTestCase):
	def test_it_mints_records_and_builds_in_one_call(self):
		result = self.build()
		self.assertEqual(result["badge_id"], f"{MAIN_ABBR}-0001")
		self.assertTrue(result["created"])
		self.assertEqual(result["employee_name"], EMP_NAME)
		# The register row is what a scan resolves through — the same one the QR
		# tool writes, because it is the same call underneath.
		self.assertEqual(len(STORE.rows(BADGE_DOCTYPE)), 1)
		self.assertEqual(STORE.rows(BADGE_DOCTYPE)[0]["employee"], EMP)

	def test_the_pass_and_the_printed_card_carry_one_identifier(self):
		"""IF THESE COULD DIVERGE, A BUCKET SCANNED OFF A PHONE WOULD PAY
		SOMEBODY THE CARD DOES NOT."""
		printed = self.tool_data("generate_employee_badge_qr", {"employee": EMP, "company": MAIN})
		pass_result = self.build()
		self.assertEqual(pass_result["badge_id"], printed["badge_id"])
		self.assertFalse(pass_result["created"])
		content = pass_result["apple"]["pass_json"]
		self.assertEqual(content["barcodes"][0]["message"], printed["badge_id"])

	def test_calling_it_twice_does_not_consume_a_second_identifier(self):
		first = self.build()
		again = self.build()
		self.assertEqual(again["badge_id"], first["badge_id"])
		self.assertTrue(again["reused"])
		self.assertEqual(len(STORE.rows(BADGE_DOCTYPE)), 1)

	def test_regenerate_mints_a_new_badge_and_retires_the_old_one(self):
		first = self.build()
		replacement = self.build(regenerate=True)
		self.assertNotEqual(replacement["badge_id"], first["badge_id"])
		self.assertEqual(replacement["retired_badges"], [first["badge_id"]])

	def test_the_pkpass_is_filed_privately_against_the_employee(self):
		result = self.build()
		self.assertEqual(result["apple"]["file_name"], f"badge-{result['badge_id']}.pkpass")
		self.assertTrue(result["apple"]["file_url"])
		rows = self.pass_files()
		self.assertEqual(len(rows), 1)
		self.assertTrue(rows[0]["is_private"])

	def test_rebuilding_replaces_that_one_file_rather_than_stacking_copies(self):
		"""Otherwise the attachment list grows one file every time a foreman
		presses the button on a bad connection, all identical but one."""
		self.build()
		second = self.build()
		self.assertEqual(len(self.pass_files()), 1)
		self.assertEqual(len(second["apple"]["replaced_files"]), 1)

	def test_a_reissued_badge_gets_its_own_file_and_leaves_the_old_one(self):
		"""The superseded pass is stale, but it is the record of what was
		issued — and `_replace_existing` only ever matches its own name."""
		first = self.build()
		self.build(regenerate=True)
		names = sorted(row["file_name"] for row in self.pass_files())
		self.assertEqual(len(names), 2)
		self.assertIn(f"badge-{first['badge_id']}.pkpass", names)

	def test_the_attached_bytes_are_the_pass_that_was_described(self):
		import hashlib

		result = self.build()
		row = self.pass_files()[0]
		payload = STORE.file_contents[row["name"]]
		self.assertEqual(len(payload), result["apple"]["bytes"])
		self.assertEqual(hashlib.sha256(payload).hexdigest(), result["apple"]["sha256"])
		self.assertIn("pass.json", _members(payload))

	def test_the_photograph_on_the_record_is_the_photograph_in_the_pass(self):
		from PIL import Image

		self.attach_photo()
		result = self.build()
		payload = STORE.file_contents[self.pass_files()[0]["name"]]
		thumbnail = _members(payload)["thumbnail.png"]
		self.assertEqual(Image.open(io.BytesIO(thumbnail)).size, (90, 90))
		self.assertEqual(result["warnings"], [])

	def test_a_private_photograph_is_on_the_apple_pass_and_not_the_google_one(self):
		self.attach_photo(private=True)
		result = self.build()
		self.assertNotIn("heroImage", result["google"]["object"])
		self.assertIn("403", " ".join(result["google"]["warnings"]))

	def test_a_public_photograph_reaches_the_google_object_as_an_absolute_url(self):
		url = self.attach_photo(private=False)
		result = self.build()
		self.assertEqual(
			result["google"]["object"]["heroImage"]["sourceUri"]["uri"], f"https://erp.example.test{url}"
		)

	def test_the_google_object_carries_the_crew_and_the_camp_as_text_modules(self):
		"""v0.103.0, and the same rule the Apple builder keeps: a fact the site
		does not record is left off the pass rather than shown as an empty row."""
		card = dict(CARD, crew="Rosa Ramirez", housing="Mill Creek \u00b7 Cabin 7")
		obj = wallet.build_google_pass(card, wallet.google_config({}))["object"]
		modules = {entry["id"]: entry["body"] for entry in obj["textModulesData"]}
		self.assertEqual(modules["crew"], "Rosa Ramirez")
		self.assertEqual(modules["housing"], "Mill Creek \u00b7 Cabin 7")

		bare = wallet.build_google_pass(dict(CARD), wallet.google_config({}))["object"]
		self.assertNotIn("crew", {entry["id"] for entry in bare["textModulesData"]})

	def test_it_says_it_is_unsigned_rather_than_implying_it_worked(self):
		result = self.build()
		self.assertFalse(result["apple"]["signed"])
		self.assertFalse(result["apple"]["configured"])
		self.assertIn("apple_wallet_certificate", result["apple"]["requires"])

	def test_a_configured_site_signs_it_through_the_same_call(self):
		"""NOTHING IN THE APP CHANGES THE DAY THE CERTIFICATE LANDS."""
		directory = tempfile.mkdtemp()
		self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
		certificate = _self_signed(directory)
		frappe.conf.update(
			{
				"apple_wallet_pass_type_identifier": "pass.test.badge",
				"apple_wallet_team_identifier": "ABCDE12345",
				"apple_wallet_certificate": certificate["cert_pem"],
				"apple_wallet_private_key": certificate["key_pem"],
			}
		)
		result = self.build()
		self.assertTrue(result["apple"]["signed"], result["apple"]["warnings"])
		self.assertEqual(result["apple"]["pass_type_identifier"], "pass.test.badge")

	def test_platform_apple_builds_no_google_object(self):
		self.assertIsNone(self.build(platform="apple")["google"])

	def test_platform_google_writes_no_pkpass_at_all(self):
		google_only = self.build(platform="google")
		self.assertIsNone(google_only["apple"])
		self.assertTrue(google_only["google"]["object"])
		self.assertEqual(self.pass_files(), [])

	def test_the_base64_is_off_by_default_and_is_the_pass_when_asked_for(self):
		"""A megabyte of base64 in a model's context to say what a file_url says
		in forty characters — the posture `artifacts.describe_attachment` takes."""
		self.assertNotIn("pkpass_base64", self.build()["apple"])
		inline = self.build(include_base64=True)
		self.assertEqual(
			base64.b64decode(inline["apple"]["pkpass_base64"]),
			STORE.file_contents[self.pass_files()[0]["name"]],
		)

	def test_attach_false_builds_without_writing_a_file(self):
		result = self.build(attach=False)
		self.assertIsNone(result["apple"]["file_url"])
		self.assertEqual(self.pass_files(), [])
		self.assertGreater(result["apple"]["bytes"], 0)

	def test_the_content_type_is_the_one_that_makes_ios_open_it_in_wallet(self):
		self.assertEqual(self.build()["apple"]["content_type"], "application/vnd.apple.pkpass")

	# ── refusals ────────────────────────────────────────────────────────
	def test_somebody_who_has_left_gets_no_pass(self):
		error = self.tool_error("generate_employee_badge_pass", {"employee": LEAVER, "company": MAIN})
		self.assertIn("Left", error)
		self.assertIn("reactivate_employee", error)
		self.assertEqual(STORE.rows(BADGE_DOCTYPE), [])

	def test_an_employee_of_another_entity_is_refused_by_name(self):
		error = self.tool_error("generate_employee_badge_pass", {"employee": EMP, "company": OTHER})
		self.assertIn(MAIN, error)
		self.assertIn("Nothing was changed", error)

	def test_an_unknown_platform_is_refused_before_anything_is_written(self):
		error = self.tool_error(
			"generate_employee_badge_pass", {"employee": EMP, "company": MAIN, "platform": "samsung"}
		)
		self.assertIn("platform must be", error)
		self.assertEqual(STORE.rows(BADGE_DOCTYPE), [])

	def test_the_tool_ships_off(self):
		"""It mints a payroll key and writes a File. Both are writes."""
		self.configure(enabled=1)
		error = self.tool_error("generate_employee_badge_pass", {"employee": EMP, "company": MAIN})
		self.assertIn("generate_employee_badge_pass", error)


# ── 7. The route ─────────────────────────────────────────────────────────


class TheMobileRoute(MobileAPITestCase):
	"""The handset half. A foreman finishes onboarding and shares the file."""

	def setUp(self):
		super().setUp()
		install_hrms()
		self.configure(enabled=1, public_url="https://erp.example.test", **ON)
		from .test_api_mobile import set_roles

		set_roles(WORKER, ["Field Worker", "Farm Manager"])
		STORE.seed(
			"Employee",
			[
				{
					"name": EMP,
					"employee_name": EMP_NAME,
					"company": MAIN,
					"status": "Active",
					"designation": "Picker",
				}
			],
		)

	def test_the_phone_gets_the_bytes_because_it_cannot_fetch_a_private_file(self):
		"""THE WHOLE REASON THE ROUTE SETS `include_base64` ITSELF. This door
		authenticates with `X-FarmOps-Token`; a private `file_url` is a login
		page to it, and a foreman cannot AirDrop a login page."""
		self.be()
		row = mobile_api.get_employee_badge_pass(employee=EMP, company=MAIN)
		payload = base64.b64decode(row["apple"]["pkpass_base64"])
		self.assertIn("pass.json", _members(payload))
		self.assertEqual(len(payload), row["apple"]["bytes"])
		self.assertEqual(row["apple"]["content_type"], "application/vnd.apple.pkpass")
		self.assertEqual(row["apple"]["file_name"], f"badge-{row['badge_id']}.pkpass")

	def test_it_tells_the_app_the_pass_is_unsigned_rather_than_letting_it_share_one(self):
		self.be()
		row = mobile_api.get_employee_badge_pass(employee=EMP, company=MAIN)
		self.assertFalse(row["apple"]["signed"])
		self.assertTrue(row["apple"]["warnings"])

	def test_the_pass_is_still_filed_against_the_employee_from_the_handset(self):
		"""`attach` is not a body key: a reissue has to have a record, and a Desk
		operator has to be able to hand the same file to somebody who lost their
		phone rather than reissuing a badge for it."""
		self.be()
		row = mobile_api.get_employee_badge_pass(employee=EMP, company=MAIN)
		files = [
			file_row
			for file_row in STORE.rows("File")
			if str(file_row.get("file_name") or "").endswith(".pkpass")
		]
		self.assertEqual(len(files), 1)
		self.assertEqual(files[0]["attached_to_name"], row["employee"])

	def test_the_route_cannot_be_told_which_badge_id_to_mint(self):
		"""Minting a payroll key is the server's job, not whatever a foreman
		typed — the same argument `generate_employee_badge_qr`'s wrapper makes."""
		import inspect

		taken = set(inspect.signature(mobile_api.get_employee_badge_pass).parameters)
		self.assertNotIn("badge_id", taken)
		self.assertNotIn("include_base64", taken)
		self.assertNotIn("attach", taken)

	def test_it_is_idempotent_so_the_wizards_button_is_safe_to_press_twice(self):
		self.be()
		first = mobile_api.get_employee_badge_pass(employee=EMP, company=MAIN)
		second = mobile_api.get_employee_badge_pass(employee=EMP, company=MAIN)
		self.assertEqual(second["badge_id"], first["badge_id"])
		self.assertTrue(second["reused"])
		self.assertEqual(len(STORE.rows(BADGE_DOCTYPE)), 1)

	def test_a_field_worker_without_the_hr_role_cannot_issue_one(self):
		"""A picker who could mint themselves a badge could mint themselves two.
		The gate is `tools/employee.require_hr_role`, the same one every other
		personnel write on this surface inherits — not a check this route adds."""
		from .test_api_mobile import set_roles

		set_roles(WORKER, ["Field Worker"])
		self.be()
		with self.assertRaises(Exception) as caught:
			mobile_api.get_employee_badge_pass(employee=EMP, company=MAIN)
		self.assertIn("personnel register", str(caught.exception))
		self.assertEqual(STORE.rows(BADGE_DOCTYPE), [])


if __name__ == "__main__":
	unittest.main()
