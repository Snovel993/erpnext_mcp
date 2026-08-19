# SPDX-License-Identifier: MIT
"""The badge card as a thing a worker's own phone holds. v0.53.0.

WHAT THIS IS FOR. `badges.py` mints `CFL-0001` and draws the QR that goes on a
printed card, and a printed card is a thing that goes through a wash cycle, gets
left in a truck, and has to be reprinted by somebody with a laminator. Every
worker in the orchard is already carrying a phone with a wallet on it. This
module turns the same badge — the same identifier, the same QR, the same face —
into a pass that lives there instead.

THE PASS IS NOT A SECOND IDENTIFIER AND MUST NEVER BECOME ONE. What it carries
is `badge_id` and nothing else; a bin trailer scanning a phone screen and a bin
trailer scanning a laminated card produce the identical string and resolve
through the identical `resolve_badge` call. If this module ever needed its own
serial, its own token or its own registry, the design would have gone wrong —
that is the whole reason the QR payload here is read off the card data rather
than generated.

────────────────────────────────────────────────────────────────────────────
NOTHING HERE TOUCHES FRAPPE, AND THAT IS THE POINT
────────────────────────────────────────────────────────────────────────────

Same split `bucket_bridge.py`, `payroll_calc.py` and `model_registry.py` keep:
bytes and dicts in, bytes and dicts out. `tools/wallet.py` is the only thing
that reads an Employee, a Company logo or a File. The consequence worth having
is that the signing, the manifest arithmetic and the JSON shaping are testable
without a site — and a pass format that Apple changes under us is a change in
one file that a test can pin.

────────────────────────────────────────────────────────────────────────────
A .pkpass IS A SIGNED ZIP, AND THE SIGNATURE IS THE HARD PART
────────────────────────────────────────────────────────────────────────────

The archive is: `pass.json` (the content), the images, `manifest.json` (a SHA-1
per file), and `signature` — a **detached PKCS#7** over `manifest.json` made
with a certificate Apple issues against one Pass Type ID. Wallet verifies the
signature, then verifies every file against the manifest, so changing a single
pixel of the photograph after signing invalidates the whole pass.

THE SHA-1 IS NOT A SECURITY CHOICE THIS APP MADE. Apple's pass format specifies
SHA-1 for the manifest; a stronger digest there produces a pass Wallet refuses.
The signature over it is SHA-256, which is where the strength actually lives.
`hashlib.sha1(..., usedforsecurity=False)` says so to anything auditing digest
use, and to a FIPS build that would otherwise refuse the call outright.

────────────────────────────────────────────────────────────────────────────
AN UNSIGNED PASS IS BUILT AND SAID TO BE UNSIGNED. IT IS NOT FAKED
────────────────────────────────────────────────────────────────────────────

A site with no Apple certificate configured still gets a complete `.pkpass`
here — correct `pass.json`, correct images, correct manifest — with **no
`signature` member**, and every caller is told `signed: false` in the same
breath. Wallet will refuse to open it, and that refusal is the honest outcome:
the alternative is a self-signed blob that has a `signature` file in it, which
Wallet refuses just as hard while looking to everybody upstream like it should
have worked.

So the unsigned build is a way to see the pass this site would produce, and to
run the whole path in a test, without pretending. The moment a real certificate
lands in `site_config.json` the same code signs it — see `docs/wallet-passes.md`
for what to obtain and where to put it. `SIGNING_REQUIREMENTS` is the sentence a
caller gets, and it names the keys rather than saying "configure the certificate".

────────────────────────────────────────────────────────────────────────────
GOOGLE WALLET IS A LINK, NOT A FILE, AND THAT CHANGES WHAT IMAGES CAN BE
────────────────────────────────────────────────────────────────────────────

Android has no equivalent of the AirDropped file. A Google Wallet pass is a JSON
object signed into a JWT that becomes a `https://pay.google.com/gp/v/save/<jwt>`
link — the worker taps it and Google builds the pass server-side, which means
**Google fetches the images itself over the public internet**. A private Frappe
File at `/private/files/...` is a 403 to Google, so `google_pass_object` takes
only URLs it was told are publicly reachable and reports every image it had to
leave out instead of embedding a broken one. Apple's pass has the bytes inside
it and has no such problem; this asymmetry is real and is not worth hiding.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import zipfile
import zlib

#: Every file in a `.pkpass`, in the order they are written into the archive.
#: Fixed rather than sorted at write time so two runs over the same card produce
#: byte-identical archives — which is what lets `tools/wallet.py` say whether a
#: regenerated pass actually differs from the one already attached.
PASS_MEMBER_ORDER = (
	"pass.json",
	"icon.png",
	"icon@2x.png",
	"icon@3x.png",
	"logo.png",
	"logo@2x.png",
	"thumbnail.png",
	"thumbnail@2x.png",
	"manifest.json",
	"signature",
)

#: The archive's stored timestamp. A fixed value rather than "now" for the same
#: reason the member order is fixed: `zipfile` writes the mtime into every local
#: header, so a clock would make otherwise identical passes differ by eight
#: bytes each. 1980-01-01 is the earliest a DOS timestamp can express.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)

#: Apple's own pixel dimensions, at 1x/2x/3x where the format has them.
#: `thumbnail` is the employee photograph and is the one a foreman looks at;
#: `logo` is the farm's mark and sits beside `logoText` on the pass header.
ICON_SIZES = ((29, "icon.png"), (58, "icon@2x.png"), (87, "icon@3x.png"))
THUMBNAIL_SIZES = ((90, "thumbnail.png"), (180, "thumbnail@2x.png"))
LOGO_SIZES = (((160, 50), "logo.png"), ((320, 100), "logo@2x.png"))

#: The most any single embedded image may weigh after processing. A pass is
#: AirDropped between two handsets on a yard's wifi and then lives in a wallet;
#: a three-megabyte headshot dropped in whole makes both of those worse for no
#: gain at 180 pixels square. An image that cannot be brought under this is
#: LEFT OUT and named in `warnings` — a badge with initials where a face should
#: be is a working badge, and a pass too heavy to send is not.
MAX_IMAGE_BYTES = 512 * 1024

#: The pass style. `generic` is Apple's own answer for an identification card —
#: `eventTicket` and `boardingPass` both carry travel semantics (a date, a
#: transit type) that a badge has no value for and that Wallet lays out around.
PASS_STYLE = "generic"

#: What goes in `pass.json` when nothing has been configured. THESE ARE NOT
#: WORKING VALUES and are never signed — `pass.com.example.badge` is not a Pass
#: Type ID Apple ever issued to anybody. They exist so an unconfigured site
#: produces a pass whose shape can be inspected, and so the field that has to be
#: replaced is visible in the artefact rather than absent from it.
PLACEHOLDER_PASS_TYPE_ID = "pass.com.example.badge"
PLACEHOLDER_TEAM_ID = "TEAMIDXXXX"

#: The farm-green a pass falls back to. Overridable per site; a badge is a
#: uniform item and an operator who has a brand colour should use it.
DEFAULT_BACKGROUND = "rgb(31,77,43)"
DEFAULT_FOREGROUND = "rgb(255,255,255)"
DEFAULT_LABEL_COLOR = "rgb(190,214,196)"

#: What an operator is told when a pass could not be signed. Names the keys, not
#: the concept: "configure the certificate" is not an actionable sentence, and
#: the file that explains where each of these comes from is named too.
SIGNING_REQUIREMENTS = (
	"an Apple Pass Type ID certificate. Set `apple_wallet_pass_type_identifier`, "
	"`apple_wallet_team_identifier` and `apple_wallet_certificate` (a .p12 exported from "
	"Keychain, or a PEM) in the site's site_config.json, with "
	"`apple_wallet_certificate_password` if the .p12 has one and "
	"`apple_wallet_wwdr_certificate` pointing at Apple's WWDR intermediate. "
	"docs/wallet-passes.md walks through obtaining each. Until then the pass is built "
	"UNSIGNED and Apple Wallet will refuse to open it."
)

#: The same sentence for the Android half.
GOOGLE_REQUIREMENTS = (
	"a Google Wallet issuer and service account. Set `google_wallet_issuer_id` and "
	"`google_wallet_service_account` (the path to the service account's JSON key) in the "
	"site's site_config.json. docs/wallet-passes.md walks through obtaining both. Until "
	"then the pass object is built and returned but there is no save link to send anybody."
)

#: Where Google's save flow lives. A JWT is appended to it.
GOOGLE_SAVE_PREFIX = "https://pay.google.com/gp/v/save/"


# ── Configuration ────────────────────────────────────────────────────────


def _text(conf, key: str, default: str = "") -> str:
	return str((conf or {}).get(key) or default).strip()


def apple_config(conf) -> dict:
	"""What this site has been told about Apple Wallet. Never raises.

	READ FROM `site_config.json` RATHER THAN FROM THE SETTINGS DOCTYPE, which is
	the same place `guard.mobile_enabled` looks and for the same reason: this is a
	private key and the path to it. A Single doctype is editable by anybody who
	reaches the Desk with the right role and is dumped in full by a dozen Frappe
	debug paths; `site_config.json` is a file on the bench that only the operator
	who deploys the site can write. A signing certificate belongs in the second
	kind of place.

	`configured` is the question every caller actually asks — can this site sign —
	and it is deliberately three separate facts ANDed rather than one flag an
	operator could set while leaving the certificate out.
	"""
	certificate = _text(conf, "apple_wallet_certificate")
	pass_type = _text(conf, "apple_wallet_pass_type_identifier")
	team = _text(conf, "apple_wallet_team_identifier")
	return {
		"pass_type_identifier": pass_type or PLACEHOLDER_PASS_TYPE_ID,
		"team_identifier": team or PLACEHOLDER_TEAM_ID,
		"certificate": certificate,
		"certificate_password": str((conf or {}).get("apple_wallet_certificate_password") or ""),
		"private_key": _text(conf, "apple_wallet_private_key"),
		"wwdr_certificate": _text(conf, "apple_wallet_wwdr_certificate"),
		"organization_name": _text(conf, "apple_wallet_organization_name"),
		"background_color": _text(conf, "apple_wallet_background_color", DEFAULT_BACKGROUND),
		"foreground_color": _text(conf, "apple_wallet_foreground_color", DEFAULT_FOREGROUND),
		"label_color": _text(conf, "apple_wallet_label_color", DEFAULT_LABEL_COLOR),
		# All three, because a certificate with no Pass Type ID signs a pass whose
		# identifier the certificate does not cover, and Wallet refuses that just
		# as completely as it refuses an unsigned one.
		"configured": bool(certificate and pass_type and team),
	}


def google_config(conf) -> dict:
	"""What this site has been told about Google Wallet. Never raises."""
	issuer = _text(conf, "google_wallet_issuer_id")
	account = _text(conf, "google_wallet_service_account")
	origins = (conf or {}).get("google_wallet_origins") or []
	if isinstance(origins, str):
		origins = [part.strip() for part in origins.split(",") if part.strip()]
	return {
		"issuer_id": issuer,
		"service_account": account,
		"class_suffix": _text(conf, "google_wallet_class_suffix", "farm_employee_badge"),
		"origins": [str(origin).strip() for origin in origins if str(origin).strip()],
		"background_color": _text(conf, "google_wallet_background_color", "#1f4d2b"),
		"configured": bool(issuer and account),
	}


# ── Images ───────────────────────────────────────────────────────────────


def _pillow():
	"""Pillow, or None. Imported here rather than at module scope because this
	app's promise is that a bench missing an optional library loses a feature by
	name instead of failing to import."""
	try:
		from PIL import Image

		return Image
	except Exception:  # pragma: no cover - a bench without Pillow
		return None


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
	return (
		struct.pack(">I", len(payload))
		+ kind
		+ payload
		+ struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
	)


def solid_png(width: int, height: int, rgb) -> bytes:
	"""A flat rectangle of one colour, as an 8-bit RGB PNG. Standard library only.

	THE POINT OF THIS IS THAT `icon.png` IS MANDATORY. A `.pkpass` without one is
	not a pass — Wallet rejects the archive rather than showing a blank square —
	so a site with no logo uploaded and no Pillow installed would otherwise be a
	site that cannot produce a badge pass at all. Thirty lines of `zlib` here is
	the difference between a plain-coloured icon and a feature that silently does
	not exist on some benches. It is the same trade `render/qr.py` makes for the
	same reason, with the same writer.
	"""
	width = max(1, int(width))
	height = max(1, int(height))
	red, green, blue = (max(0, min(255, int(part))) for part in rgb)
	row = b"\x00" + bytes((red, green, blue)) * width
	raw = row * height
	header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour
	return b"".join(
		[
			b"\x89PNG\r\n\x1a\n",
			_png_chunk(b"IHDR", header),
			_png_chunk(b"IDAT", zlib.compress(raw, 9)),
			_png_chunk(b"IEND", b""),
		]
	)


def _rgb(color: str, fallback=(31, 77, 43)) -> tuple:
	"""`rgb(31,77,43)` or `#1f4d2b` as a triple. Anything else is the fallback.

	Never raises: a colour typed wrong in `site_config.json` should produce a
	badge in the default green, not a hiring day with no passes on it.
	"""
	text = str(color or "").strip()
	try:
		if text.startswith("#") and len(text) == 7:
			return tuple(int(text[index : index + 2], 16) for index in (1, 3, 5))
		if text.lower().startswith("rgb(") and text.endswith(")"):
			parts = [int(float(part)) for part in text[4:-1].split(",")]
			if len(parts) >= 3:
				return tuple(max(0, min(255, part)) for part in parts[:3])
	except (ValueError, TypeError):
		pass
	return fallback


#: Fill the box and crop what does not fit. What a PHOTOGRAPH wants.
FIT_COVER = "cover"

#: Fit the whole image inside the box and pad the rest. What a LOGO wants.
FIT_CONTAIN = "contain"


def fit_png(data: bytes, width: int, height: int, background=(255, 255, 255), fit=FIT_COVER) -> bytes | None:
	"""One source image, converted to a PNG of exactly `width`x`height`, or None.

	THE TWO IMAGES ON A BADGE WANT OPPOSITE THINGS AND THIS IS WHERE THAT LIVES.

	`cover` is for the PHOTOGRAPH. A headshot letterboxed into a 90x90 box with
	bars down the sides is a face a third of the size it could be, and the thing
	a foreman is looking at across a bin trailer is the face. The crop is centred,
	which is where a headshot's subject is, and losing the edges of a portrait
	loses background.

	`contain` is for the LOGO, and getting this wrong is not cosmetic. Apple's
	`logo.png` is 160x50 — a 3.2:1 letterbox — and a farm's mark is usually
	square or nearly so. Cropping a square logo to cover that box keeps a
	horizontal band through the middle of it and throws the rest away, which for
	most marks means the name is gone and what is left is unrecognisable. Padded
	to the pass's own background colour it reads as a logo on a card.

	Returns None — never raises, never a broken image — when Pillow is missing or
	the bytes are not a decodable image. The caller drops that image and says so;
	a pass with initials instead of a photograph is a working pass.
	"""
	image_module = _pillow()
	if not image_module or not data:
		return None
	import io

	try:
		with image_module.open(io.BytesIO(data)) as source:
			source.load()
			image = source.convert("RGBA")
	except Exception:
		return None

	# Flattened onto an opaque background because a pass image with an alpha
	# channel renders over Wallet's own card colour, and a transparent PNG of a
	# dark logo on a dark pass is an invisible logo. It is also what the padding
	# in `contain` is made of.
	flat = image_module.new("RGB", (width, height), tuple(background))

	if fit == FIT_CONTAIN:
		fitted = image.copy()
		fitted.thumbnail((width, height), image_module.LANCZOS)
		flat.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2), fitted)
	else:
		target_ratio = width / height
		source_ratio = (image.width / image.height) if image.height else target_ratio
		if source_ratio > target_ratio:  # wider than the box: trim the sides
			crop_width = max(1, round(image.height * target_ratio))
			left = max(0, (image.width - crop_width) // 2)
			image = image.crop((left, 0, left + crop_width, image.height))
		elif source_ratio < target_ratio:  # taller than the box: trim top and bottom
			crop_height = max(1, round(image.width / target_ratio))
			top = max(0, (image.height - crop_height) // 2)
			image = image.crop((0, top, image.width, top + crop_height))
		image = image.resize((width, height), image_module.LANCZOS)
		flat.paste(image, (0, 0), image)

	buffer = io.BytesIO()
	flat.save(buffer, format="PNG", optimize=True)
	rendered = buffer.getvalue()
	return rendered if len(rendered) <= MAX_IMAGE_BYTES else None


def initials_tile(initials: str, size: int, rgb) -> bytes:
	"""The square that goes where a photograph is not. Pillow if it is here.

	`badges._initials` already decided that two letters are what a foreman glances
	at when the Employee record has no image, and the printed card has drawn them
	since v0.50.0. This is the same fallback for the same reason on the phone —
	and when Pillow is missing it degrades to a plain tile rather than to nothing,
	because `icon.png` is not optional.
	"""
	image_module = _pillow()
	size = max(1, int(size))
	if not image_module:
		return solid_png(size, size, rgb)
	import io

	try:
		from PIL import ImageDraw, ImageFont

		image = image_module.new("RGB", (size, size), tuple(rgb))
		draw = ImageDraw.Draw(image)
		text = str(initials or "?")[:2].upper()
		try:
			font = ImageFont.load_default(size=max(8, int(size * 0.45)))
		except TypeError:  # pragma: no cover - Pillow < 10.1 has no size argument
			font = ImageFont.load_default()
		left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
		draw.text(
			((size - (right - left)) / 2 - left, (size - (bottom - top)) / 2 - top),
			text,
			font=font,
			fill=(255, 255, 255),
		)
		buffer = io.BytesIO()
		image.save(buffer, format="PNG", optimize=True)
		return buffer.getvalue()
	except Exception:  # pragma: no cover - a Pillow without the font modules
		return solid_png(size, size, rgb)


def pass_images(card: dict, config: dict) -> tuple:
	"""Every image member of the archive, and what had to be left out.

	Three sources and a fallback for each: the employee photograph becomes the
	thumbnail, the company mark becomes the logo and the icon, and anything
	missing or undecodable becomes an initials tile in the pass's own background
	colour. `warnings` is how the caller — and the operator reading the result —
	finds out that the face they expected is not on the pass.
	"""
	background = _rgb(config.get("background_color"), (31, 77, 43))
	photo = card.get("photo_png") or b""
	logo = card.get("logo_png") or b""
	warnings = []
	files = {}

	for pixels, member in THUMBNAIL_SIZES:
		rendered = fit_png(photo, pixels, pixels, background) if photo else None
		if photo and rendered is None and member == "thumbnail.png":
			warnings.append(
				f"the employee photograph could not be used ({len(photo)} bytes) — it is not a "
				"decodable image, this bench has no Pillow, or it did not fit under "
				f"{MAX_IMAGE_BYTES} bytes. The pass carries initials instead."
			)
		files[member] = rendered or initials_tile(card.get("initials") or "?", pixels, background)

	for (width, height), member in LOGO_SIZES:
		# CONTAIN, not cover — see `fit_png`. `logo.png` is a 3.2:1 letterbox and
		# a farm's mark is usually square; cropping it to fill would keep a band
		# through the middle and throw the name away.
		rendered = fit_png(logo, width, height, background, fit=FIT_CONTAIN) if logo else None
		if logo and rendered is None and member == "logo.png":
			warnings.append(
				"the company badge logo could not be used — it is not a decodable image, this "
				"bench has no Pillow, or it did not fit. The pass shows the company name only."
			)
		if rendered:
			files[member] = rendered

	for pixels, member in ICON_SIZES:
		# The icon is what Wallet shows in notifications and on the lock screen,
		# and it is MANDATORY. The logo first because it is the farm's mark; the
		# initials tile second because something has to be there. CONTAIN again,
		# for the same reason and in the other direction: a wide wordmark cropped
		# square keeps three letters of it.
		rendered = fit_png(logo, pixels, pixels, background, fit=FIT_CONTAIN) if logo else None
		files[member] = rendered or initials_tile(card.get("initials") or "?", pixels, background)

	return files, warnings


# ── pass.json ────────────────────────────────────────────────────────────


def _field(key: str, label: str, value) -> dict:
	return {"key": key, "label": label, "value": str(value)}


def build_pass_json(card: dict, config: dict) -> dict:
	"""`pass.json` for one employee badge.

	THE BARCODE IS DECLARED TWICE ON PURPOSE. `barcodes` (plural) is what every
	iOS since 9 reads; `barcode` (singular) is the deprecated key an older
	device still needs, and Apple's own guidance is to write both. They carry the
	identical payload, which is `badge_id` and only `badge_id` — see the module
	docstring on why this pass never gets a serial of its own.

	`messageEncoding` IS `iso-8859-1` BECAUSE THE FORMAT SAYS SO. A minted badge
	is ASCII and it makes no difference; an adopted legacy identifier with a
	non-ASCII character in it would encode differently, and matching the spec is
	what makes the string a scanner reads equal the string that was stored.

	`sharingProhibited` IS SET. A badge attributes piece work to a person, so a
	pass a picker can AirDrop onward is a pass two people can scan buckets with.
	The foreman AirDrops it once, at onboarding; after that it does not travel.
	"""
	badge_id = str(card.get("badge_id") or "")
	name = str(card.get("employee_name") or "")
	company = str(card.get("company") or "")
	organization = str(config.get("organization_name") or "") or company

	back_fields = [
		_field("badge_back", "Badge Number", badge_id),
		_field("company_back", "Company", company),
	]
	if card.get("employee_number"):
		back_fields.append(_field("employee_number", "Employee Number", card["employee_number"]))
	if card.get("issued_on"):
		back_fields.append(_field("issued_on", "Issued", card["issued_on"]))
	if card.get("reports_to_name"):
		back_fields.append(_field("reports_to", "Reports to", card["reports_to_name"]))
	if card.get("branch"):
		back_fields.append(_field("branch", "Branch", card["branch"]))
	back_fields.append(
		_field(
			"terms",
			"Terms",
			# Read by whoever finds a lost phone as much as by the holder. Says
			# what the pass is FOR, which is the fact that makes a found badge
			# worth handing in rather than scanning.
			f"This pass identifies an employee of {company} for time and piece-work "
			"recording. It is not a payment card and carries no personal data beyond "
			"what is printed on it. Report a lost badge to your supervisor — a "
			"replacement retires this one.",
		)
	)

	# v0.104.0. The same facts the printed card gained, laid into the slots Apple
	# gives a generic pass. Role and department read together on the front; the
	# camp sits with the company, which is the other answer to "where does this
	# person belong" rather than "what do they do"; and the reporting line and the
	# branch go on the BACK, where there is room for a label and a full name and
	# where somebody who needs the chain of command is already looking.
	#
	# Every one is omitted when unknown rather than printed empty — a labelled
	# blank on a phone reads as a system that lost the answer.
	secondary = [_field("badge", "Badge", badge_id)]
	if card.get("designation"):
		secondary.append(_field("role", "Role", card["designation"]))
	if card.get("department"):
		secondary.append(_field("department", "Department", card["department"]))

	auxiliary = [_field("company", "Company", company)]
	if card.get("housing"):
		auxiliary.append(_field("housing", "Camp", card["housing"]))

	return {
		"formatVersion": 1,
		"passTypeIdentifier": str(config.get("pass_type_identifier") or PLACEHOLDER_PASS_TYPE_ID),
		"teamIdentifier": str(config.get("team_identifier") or PLACEHOLDER_TEAM_ID),
		"serialNumber": badge_id,
		"organizationName": organization,
		"description": f"{company} employee badge",
		"logoText": company,
		"backgroundColor": str(config.get("background_color") or DEFAULT_BACKGROUND),
		"foregroundColor": str(config.get("foreground_color") or DEFAULT_FOREGROUND),
		"labelColor": str(config.get("label_color") or DEFAULT_LABEL_COLOR),
		"sharingProhibited": True,
		"barcodes": [
			{
				"format": "PKBarcodeFormatQR",
				"message": badge_id,
				"messageEncoding": "iso-8859-1",
				# The human-readable fallback, below the symbol, for exactly the
				# reason `badges._print_spec` puts it on the printed card: every
				# scanner eventually fails and a badge nobody can read out over a
				# radio is a picker whose buckets go unattributed.
				"altText": badge_id,
			}
		],
		"barcode": {
			"format": "PKBarcodeFormatQR",
			"message": badge_id,
			"messageEncoding": "iso-8859-1",
			"altText": badge_id,
		},
		PASS_STYLE: {
			"primaryFields": [_field("name", "Employee", name)],
			"secondaryFields": secondary,
			"auxiliaryFields": auxiliary,
			"backFields": back_fields,
		},
	}


# ── The archive ──────────────────────────────────────────────────────────


def manifest_for(files: dict) -> dict:
	"""SHA-1 per member, which is what the pass format specifies. See the header.

	`usedforsecurity=False` on Python 3.9+ marks this as a format requirement
	rather than a security choice — it is what keeps the call working on a build
	with FIPS restrictions, where a bare `sha1()` raises.
	"""
	manifest = {}
	for member, payload in files.items():
		try:
			digest = hashlib.sha1(payload, usedforsecurity=False)
		except TypeError:  # pragma: no cover - Python without the keyword
			digest = hashlib.sha1(payload)
		manifest[member] = digest.hexdigest()
	return manifest


def _load_pem_or_der(path: str):
	"""One X.509 certificate off disk, whichever encoding it is in.

	Apple ships the WWDR intermediate as a DER `.cer` from the developer site and
	as PEM from most of the instructions written about it, and an operator will
	have whichever they downloaded. Trying both is two lines and removes a whole
	class of "it says the file is not a certificate".
	"""
	from cryptography import x509

	with open(path, "rb") as handle:
		blob = handle.read()
	try:
		return x509.load_pem_x509_certificate(blob)
	except Exception:
		return x509.load_der_x509_certificate(blob)


def load_signer(config: dict) -> tuple:
	"""(private key, certificate, extra certificates) for signing, or a refusal.

	TWO SHAPES ACCEPTED, because an operator arrives with one or the other and
	neither is wrong. Keychain's "Export…" on a Pass Type ID certificate produces
	a **.p12** holding the key, the certificate and usually the WWDR intermediate
	that signed it — which is why the intermediate is taken from inside the
	bundle when it is there. A certificate built with `openssl` for a development
	run is a **PEM** pair, and that is the shape `docs/wallet-passes.md` uses for
	the self-signed path.

	Raises `ValueError` with a sentence naming the file. The caller turns that
	into an unsigned pass and a warning rather than losing the badge — a foreman
	standing in a yard cannot fix a certificate path.
	"""
	from cryptography.hazmat.primitives.serialization import pkcs12

	path = str(config.get("certificate") or "")
	if not path:
		raise ValueError("no apple_wallet_certificate is configured")
	if not os.path.isfile(path):
		raise ValueError(f"apple_wallet_certificate {path!r} is not a file this site can read")

	password = config.get("certificate_password") or ""
	password_bytes = password.encode("utf-8") if password else None

	if path.lower().endswith((".p12", ".pfx")):
		with open(path, "rb") as handle:
			key, certificate, extra = pkcs12.load_key_and_certificates(handle.read(), password_bytes)
		if not key or not certificate:
			raise ValueError(f"{path!r} holds no private key and certificate pair")
		return key, certificate, list(extra or [])

	from cryptography import x509
	from cryptography.hazmat.primitives.serialization import load_pem_private_key

	with open(path, "rb") as handle:
		certificate_pem = handle.read()
	key_path = str(config.get("private_key") or "") or path
	if not os.path.isfile(key_path):
		raise ValueError(f"apple_wallet_private_key {key_path!r} is not a file this site can read")
	with open(key_path, "rb") as handle:
		key = load_pem_private_key(handle.read(), password=password_bytes)
	return key, x509.load_pem_x509_certificate(certificate_pem), []


def sign_manifest(manifest_bytes: bytes, config: dict) -> bytes:
	"""The detached PKCS#7 signature over `manifest.json`. Raises on any failure.

	THE OPTIONS ARE THE TWO `openssl smime -binary -sign … -outform DER` PASSES,
	and neither is discretionary. `DetachedSignature` is what "detached" means —
	the manifest itself is already in the archive and a signature carrying a
	second copy of it is not the format. `Binary` suppresses the S/MIME text
	canonicalisation that would rewrite every `\\n` in the manifest to `\\r\\n`
	before hashing, which produces a signature over bytes that are not the bytes
	in the archive — a pass that fails verification for a reason nothing reports.

	SIGNED ATTRIBUTES ARE LEFT ON. `NoAttributes` would strip the content-type
	and message-digest attributes that Apple's verifier expects to find, and is
	the other half of the same class of silent refusal.

	The WWDR intermediate is included because Wallet builds the chain from what
	the signature carries — it does not go and fetch it. A .p12 exported from
	Keychain usually has it already, which is why the configured file is a
	fallback rather than a requirement.
	"""
	from cryptography.hazmat.primitives import hashes
	from cryptography.hazmat.primitives.serialization import Encoding, pkcs7

	key, certificate, extra = load_signer(config)
	builder = pkcs7.PKCS7SignatureBuilder().set_data(manifest_bytes)
	builder = builder.add_signer(certificate, key, hashes.SHA256())

	chain = list(extra)
	wwdr = str(config.get("wwdr_certificate") or "")
	if wwdr:
		if not os.path.isfile(wwdr):
			raise ValueError(f"apple_wallet_wwdr_certificate {wwdr!r} is not a file this site can read")
		chain.append(_load_pem_or_der(wwdr))
	for authority in chain:
		builder = builder.add_certificate(authority)

	return builder.sign(Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])


def build_pkpass(card: dict, config: dict) -> dict:
	"""The whole `.pkpass`, and an honest account of whether it will open.

	THE ORDER IS: content, then manifest over the content, then signature over
	the manifest. It cannot be anything else — a manifest computed before the
	last image is written describes an archive that does not exist, and Wallet
	checks every member against it.

	A SIGNING FAILURE DOES NOT LOSE THE PASS. Whatever went wrong — no
	certificate, a path that moved, a .p12 whose password is wrong, a bench with
	no `cryptography` — the archive is still built, `signed` is False, and the
	reason is a sentence in `warnings`. The caller reports that upward. The
	alternative is a foreman in a yard being told "signing failed" and having
	no badge at all, when what they needed was the QR.
	"""
	config = dict(config or {})
	files, warnings = pass_images(card, config)
	pass_json = build_pass_json(card, config)
	files["pass.json"] = json.dumps(pass_json, indent="\t", sort_keys=True, ensure_ascii=False).encode(
		"utf-8"
	)

	manifest = manifest_for(files)
	files["manifest.json"] = json.dumps(manifest, indent="\t", sort_keys=True).encode("utf-8")

	signed = False
	if config.get("configured"):
		try:
			files["signature"] = sign_manifest(files["manifest.json"], config)
			signed = True
		except Exception as exc:
			warnings.append(
				f"the pass could not be signed ({type(exc).__name__}: {exc}). It is built and "
				"complete but Apple Wallet will refuse to open it until this is fixed."
			)
	else:
		warnings.append(
			"this pass is UNSIGNED and Apple Wallet will refuse it. It needs " + SIGNING_REQUIREMENTS
		)

	return {
		"pkpass": zip_pass(files),
		"pass_json": pass_json,
		"manifest": manifest,
		"members": [member for member in PASS_MEMBER_ORDER if member in files],
		"signed": signed,
		"pass_type_identifier": pass_json["passTypeIdentifier"],
		"serial_number": pass_json["serialNumber"],
		"warnings": warnings,
	}


def zip_pass(files: dict) -> bytes:
	"""The archive itself. Deterministic: fixed order, fixed timestamps.

	`ZIP_DEFLATED` rather than stored, because the members that dominate the size
	are PNGs that are already compressed and a JSON manifest that is not — the
	saving is on the JSON and it is free.
	"""
	import io

	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
		for member in PASS_MEMBER_ORDER:
			if member not in files:
				continue
			info = zipfile.ZipInfo(member, date_time=ZIP_EPOCH)
			info.compress_type = zipfile.ZIP_DEFLATED
			info.external_attr = 0o644 << 16
			archive.writestr(info, files[member])
	return buffer.getvalue()


# ── Google Wallet ────────────────────────────────────────────────────────


def _localized(value: str) -> dict:
	return {"defaultValue": {"language": "en-US", "value": str(value or "")}}


def google_class_id(config: dict) -> str:
	return f"{config.get('issuer_id') or 'ISSUER_ID'}.{config.get('class_suffix') or 'farm_employee_badge'}"


def google_object_id(config: dict, badge_id: str) -> str:
	"""`<issuer>.<badge>`, with the badge sanitised to what Google accepts.

	Google's object IDs allow only alphanumerics, `.`, `_` and `-`. A minted
	`CFL-0001` is already legal and passes through unchanged, which matters — an
	ID that mangled the badge would make the pass on the phone and the row in the
	register hard to reconcile by eye. An adopted legacy identifier with anything
	else in it is rewritten to underscores rather than refused, because the badge
	the pass CARRIES is untouched either way: this is a Google-side key, not the
	scanned payload.
	"""
	safe = "".join(char if (char.isalnum() or char in "._-") else "_" for char in str(badge_id or ""))
	return f"{config.get('issuer_id') or 'ISSUER_ID'}.{safe}"


def google_pass_class(card: dict, config: dict) -> dict:
	"""The `genericClass` — the template every badge for this farm shares.

	ONE CLASS PER ISSUER, CREATED ONCE, and it is returned on every call rather
	than only the first because Google's save-link flow accepts the class inline
	in the JWT and treats a class it already has as a no-op. An operator who
	would rather POST it once to the Wallet API can take this object and do that;
	either way there is nothing here to remember to run.
	"""
	return {
		"id": google_class_id(config),
		"issuerName": str(card.get("company") or ""),
		"reviewStatus": "UNDER_REVIEW",
	}


def google_pass_object(card: dict, config: dict) -> tuple:
	"""The `genericObject` for one badge, and the images it had to leave out.

	GOOGLE FETCHES IMAGES; IT IS NOT SENT THEM. That is the whole difference from
	the Apple half — see the module docstring — and it means a photograph living
	at `/private/files/…` cannot be on this pass at all, because Google's fetcher
	arrives unauthenticated and gets a login page. So a URL goes on the object
	only when the caller has said it is publicly reachable, and every one that is
	not is named in the returned warnings instead of being written and quietly
	rendering as a broken tile on a worker's phone.
	"""
	badge_id = str(card.get("badge_id") or "")
	warnings = []
	obj = {
		"id": google_object_id(config, badge_id),
		"classId": google_class_id(config),
		"state": "ACTIVE",
		"cardTitle": _localized(card.get("company") or ""),
		"header": _localized(card.get("employee_name") or ""),
		"subheader": _localized(card.get("designation") or "Employee"),
		"hexBackgroundColor": str(config.get("background_color") or "#1f4d2b"),
		"barcode": {"type": "QR_CODE", "value": badge_id, "alternateText": badge_id},
		"textModulesData": [
			{"id": "badge", "header": "Badge", "body": badge_id},
			{"id": "company", "header": "Company", "body": str(card.get("company") or "")},
		],
	}
	if card.get("employee_number"):
		obj["textModulesData"].append(
			{"id": "employee_number", "header": "Employee Number", "body": str(card["employee_number"])}
		)
	# v0.104.0, and the same rule the Apple builder above keeps: a fact the site
	# does not record is left off the pass rather than shown as an empty row.
	for key, header in (
		("department", "Department"),
		("branch", "Branch"),
		("reports_to_name", "Reports to"),
		("housing", "Camp"),
	):
		if card.get(key):
			obj["textModulesData"].append({"id": key, "header": header, "body": str(card[key])})

	logo_url = str(card.get("public_logo_url") or "")
	photo_url = str(card.get("public_photo_url") or "")
	if logo_url:
		obj["logo"] = {"sourceUri": {"uri": logo_url}}
	elif card.get("logo_png"):
		warnings.append(
			"the company badge logo is on this pass for Apple but not for Google: Google fetches "
			"images over the public internet and this site's logo is not at a public URL."
		)
	if photo_url:
		obj["heroImage"] = {"sourceUri": {"uri": photo_url}}
	elif card.get("photo_png"):
		warnings.append(
			"the employee photograph is on the Apple pass but not the Google one, for the same "
			"reason: a private Frappe File is a 403 to Google's image fetcher."
		)
	return obj, warnings


def _b64url(payload: bytes) -> str:
	return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def load_service_account(path: str) -> dict:
	"""The Google service account key JSON, or a `ValueError` naming the file."""
	if not path:
		raise ValueError("no google_wallet_service_account is configured")
	if not os.path.isfile(path):
		raise ValueError(f"google_wallet_service_account {path!r} is not a file this site can read")
	with open(path, "rb") as handle:
		account = json.loads(handle.read().decode("utf-8"))
	if not account.get("private_key") or not account.get("client_email"):
		raise ValueError(
			f"{path!r} is not a Google service account key — it has no private_key and "
			"client_email. Download the JSON key from the service account, not the OAuth client."
		)
	return account


def sign_jwt(claims: dict, account: dict) -> str:
	"""An RS256 JWT of `claims`, signed with the service account's private key.

	HAND-ROLLED RATHER THAN `PyJWT`, and it is twelve lines: two base64url
	segments, a PKCS#1 v1.5 signature over the dot-joined pair, a third segment.
	The reason is the one `render/qr.py` gives for writing its own PNG — the
	dependency would be carried by every bench for one call, and what it does
	here is small enough to read in full and pin in a test that verifies the
	signature back against the public key.
	"""
	from cryptography.hazmat.primitives import hashes, serialization
	from cryptography.hazmat.primitives.asymmetric import padding

	key = serialization.load_pem_private_key(str(account["private_key"]).encode("utf-8"), password=None)
	header = {"alg": "RS256", "typ": "JWT"}
	segments = [
		_b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")),
		_b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")),
	]
	signing_input = ".".join(segments).encode("ascii")
	signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
	segments.append(_b64url(signature))
	return ".".join(segments)


def build_google_pass(card: dict, config: dict, issued_at: int = 0) -> dict:
	"""The Google Wallet object, and the save link when this site can sign one.

	`issued_at` IS PASSED IN RATHER THAN READ FROM THE CLOCK, because this module
	has no clock — the same property that makes every other function here
	testable without a site. `tools/wallet.py` supplies it from Frappe's own
	time so that the timestamp on a pass and the timestamp in the audit row for
	the call that made it are the same clock.

	AN UNCONFIGURED SITE STILL GETS THE OBJECT. It is the thing an operator pastes
	into Google's Pass Builder or POSTs with `curl` to check the shape before
	committing to a service account, and returning nothing until credentials
	exist would make that impossible.
	"""
	config = dict(config or {})
	obj, warnings = google_pass_object(card, config)
	pass_class = google_pass_class(card, config)
	payload = {
		"iss": "",
		"aud": "google",
		"typ": "savetowallet",
		"iat": int(issued_at or 0),
		"origins": list(config.get("origins") or []),
		"payload": {"genericClasses": [pass_class], "genericObjects": [obj]},
	}

	save_url = ""
	if config.get("configured"):
		try:
			account = load_service_account(str(config.get("service_account") or ""))
			payload["iss"] = account["client_email"]
			save_url = GOOGLE_SAVE_PREFIX + sign_jwt(payload, account)
		except Exception as exc:
			warnings.append(
				f"the Google Wallet save link could not be signed ({type(exc).__name__}: {exc}). "
				"The pass object below is complete; there is just no link to send anybody."
			)
	else:
		warnings.append(
			"there is no Google Wallet save link because this site has not been given " + GOOGLE_REQUIREMENTS
		)

	return {
		"object": obj,
		"class": pass_class,
		"object_id": obj["id"],
		"class_id": pass_class["id"],
		"save_url": save_url,
		"signed": bool(save_url),
		"warnings": warnings,
	}
