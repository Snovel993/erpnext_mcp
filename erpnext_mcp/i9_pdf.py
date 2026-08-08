# SPDX-License-Identifier: MIT
"""Form I-9 drawn on the federal form itself, not redrawn to look like it.

v0.47.1. `form_pdf_renderer.py` reproduces a W-2 and a 941 with reportlab
because the IRS's own Copy A is red-ink stock no laser printer makes, so a
reproduction is the only honest thing to produce. FORM I-9 IS THE OPPOSITE CASE.
USCIS publishes it as a plain-paper fillable PDF, it is completed on plain paper
by every employer in the country, and it is retained rather than filed — nobody
sends it anywhere. So the right output is the government's own page with the
boxes filled in, and this module fills the government's own page.

    erpnext_mcp/templates/i9_form.pdf   ← shipped byte-for-byte, never edited
    ↓ 133 AcroForm fields, no XFA
    fill_i9_pdf(record, employer, reverifications)  →  PDF bytes

PURE FUNCTION, same contract as `form_pdf_renderer`. No database reads, no
attachment writing, no side effects: three dicts go in and bytes come out, and
the whole thing is checkable against a fixture. `tools/i9.render_i9_pdf` does
the reading and the attaching; this does the drawing and nothing else.

────────────────────────────────────────────────────────────────────────────
FOUR THINGS THIS DOES NOT PUT ON THE PAGE, AND WHY
────────────────────────────────────────────────────────────────────────────

**NO EIN BOX, BECAUSE FORM I-9 HAS NONE.** v0.48.0. `tools/i9._employer_block`
has returned an `ein` since v0.47.1 — off I-9 Settings' `business_ein`, falling
back to the Company's `tax_id` — and this module never wrote it anywhere, which
read like a mapping somebody forgot. It is not. All 133 of the template's
AcroForm fields are enumerated in `test_i9_pdf.py`, and Section 2's employer
block is five boxes: the representative's name and title, their signature,
today's date, `Employers Business or Org Name` and `Employers Business or Org
Address`. THERE IS NO EMPLOYER IDENTIFICATION NUMBER FIELD ON FORM I-9. The EIN
is an E-VERIFY datum — it is on the E-Verify case, not on the retained form —
and the two are separate obligations that this app keeps separate.

So the number goes where USCIS provides for an employer to write something the
boxes do not ask for: Additional Information, LABELLED, by `_employer_lines`.
The alternative was to append it to `Employers Business or Org Address`, whose
own label is "Address, City or Town, State, ZIP Code" — a box that would then
hold something that is not an address, on a form an inspector reads box by box.
An unlabelled number in an address box is worse than no number at all.

**NO SIGNATURE, EVER.** Three of the four signature boxes are `/Tx` text
fields, so a name typed into one would render as a signature and would be
indistinguishable from one. It would not BE one: an electronic I-9 signature
has to meet 8 CFR 274a.2(h)'s own requirements, and a string this app typed
into a PDF meets none of them. What the app genuinely holds is a signature
CAPTURE and a timestamp, and those go into Additional Information as what they
are — attested on this date, from this address, through this app — so an
auditor reads a fact instead of a picture of one. The boxes stay empty for a
pen.

**NO SOCIAL SECURITY NUMBER UNLESS ASKED FOR BY NAME.** `tools/i9.py` keeps the
last four digits in a column and the nine in Frappe's encrypted `__Auth` table,
and says at length that nothing in this app reads the nine back. This is the
call site that has a real reason to — a printed I-9 has an SSN box — so it is
an argument (`full_ssn`) rather than a lookup, the caller has to pass it, and
`tools/i9.render_i9_pdf` will only fetch it when an operator asks in the same
breath. Absent it the comb stays empty, which is the same page an employee gets
handed to fill in by hand. Nine digits, no dashes: the box is a 9-cell comb.

**NO ALTERNATIVE-PROCEDURE TICK.** `CB_Alt` says the employer used the DHS
alternative (remote) document-examination procedure. Nothing in this app records
whether they did, and a tick nobody chose is a false attestation on a federal
form.

**NO NEW NAME IN SUPPLEMENT B.** The three name boxes on a reverification row
are for a worker whose legal name CHANGED. `I-9 Reverification` records the
reason 'Name Change' but not the new name, so the boxes stay empty rather than
being filled with the name already printed at the top of the page.

────────────────────────────────────────────────────────────────────────────
THE FIELD TABLE IS THE WHOLE INTERFACE, AND IT IS FILLED PER PAGE
────────────────────────────────────────────────────────────────────────────

Every value below is placed by AcroForm field name, and every write names its
page. Naming the page is what keeps the three Supplement B rows three rows.

THERE IS ONE FIELD IN THE GOVERNMENT'S FILE THAT IS ON TWO PAGES AT ONCE, and
it has to be taken apart before anything is written. `Document Title 1` is a
single AcroForm field with TWO widget annotations: one in Section 2's List A
block on page 1, one in Supplement B's second reverification row on page 4. One
field means one value, so filling either box fills both — a hire-day document
title silently overwritten by a reverification made two seasons later, or the
reverse, with a plausible-looking form either way. `_split_shared_title` promotes
the page-4 widget into a field of its own IN THE GENERATED COPY before any value
is set. The template on disk is untouched; USCIS's page keeps its own field
structure, and the copy this app produces has one more field than the copy it
started from. That is the whole of the surgery, it is asserted by name in
`test_i9_pdf.py`, and it is the only place this module edits the form's
structure rather than its values.

Dates go in as `MM/DD/YYYY`, which is what the boxes are labelled and what
`_us_date` produces from the ISO strings the doctype stores. A date this module
cannot parse is left BLANK rather than printed through: a federal form with a
half-converted date in a date box is worse than one with a gap somebody fills in.

`NeedAppearances` is set as well as the appearance streams pypdf generates,
because the two together are what makes the values visible in every viewer
anybody actually opens this in — Preview, Chrome, Acrobat — rather than in most
of them.

PYPDF IS OPTIONAL AT IMPORT TIME, like shapely, h3, segno and reportlab before
it. A bench without it loses exactly one tool — `render_i9_pdf`, and with it the
`generate_i9_pdf` endpoint in front of it — which says so by name with the pip
command to fix it. `attach_signed_i9` files a scan somebody else produced and
needs no PDF library at all. Every other I-9 tool works without it too, the
record is unaffected, and
the I-9 Form Print Format (v0.47.1, seeded on migrate) still produces a readable
page in the Desk with no PDF library involved at all.
"""

from __future__ import annotations

import hashlib
import io
import os

from .errors import ToolError

try:  # pragma: no cover - exercised by whichever branch the test bench has
	from pypdf import PdfWriter
	from pypdf.generic import ArrayObject, NameObject, TextStringObject

	HAVE_PYPDF = True
except Exception:  # pragma: no cover - a bench without the dependency
	PdfWriter = None
	ArrayObject = NameObject = TextStringObject = None
	HAVE_PYPDF = False


#: The shipped government form. See `templates/README.md` for its provenance.
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "i9_form.pdf")

#: The edition this app's field table was written against, as printed in the
#: bottom-left corner of every page of the template. Restated in every result so
#: a form rendered from a superseded edition can be identified as one.
EDITION = "Form I-9, Edition 01/20/25, OMB No. 1615-0047, expires 05/31/2027"

#: The template's digest. Asserted by the test suite rather than checked at run
#: time — a mismatch is a development-time fact about the repository, and
#: hashing half a megabyte on every render to re-learn it would be a cost paid
#: on a site where nothing can have changed.
TEMPLATE_SHA256 = "780f348c34df694bb0b4dbbfaf9f22b99b9757b80d16a37ba89aadf069597281"

#: Which page carries what. Zero-based, because that is how the pages are
#: indexed when they are written to.
PAGE_FORM = 0  # Section 1 and Section 2, both on the first page
PAGE_LISTS = 1  # Lists of Acceptable Documents — nothing is written here
PAGE_SUPPLEMENT_A = 2  # Preparer and/or Translator Certification
PAGE_SUPPLEMENT_B = 3  # Reverification and Rehire (what used to be Section 3)

#: Supplement B has three reverification rows on its one page. A worker with a
#: fourth is not truncated silently — `_overflow_note` names every entry that
#: did not fit, and `render_i9_pdf` returns the count.
SUPPLEMENT_B_ROWS = 3

#: The one field the government's file puts on two pages at once, and the name
#: the page-4 half is given in the generated copy. See the module docstring.
SHARED_TITLE_FIELD = "Document Title 1"
SUPPLEMENT_B_TITLE_FIELD = "Document Title 1 (Supplement B)"

#: Which field carries each Supplement B row's document title. Row 1 is the
#: split one and is therefore not `f"Document Title {index}"` like its
#: neighbours — the exception is in the table rather than in an `if` inside the
#: loop, so a reader of the table can see there is one.
SUPPLEMENT_B_TITLE_FIELDS = (
	"Document Title 0",
	SUPPLEMENT_B_TITLE_FIELD,
	"Document Title 2",
)

#: The citizenship attestation, as the four tick boxes USCIS numbers 1 to 4 and
#: as this app's `citizenship_status` Select spells the same four answers. A
#: status not in this table ticks NOTHING, which is a form an auditor can see is
#: incomplete rather than one that guessed.
CITIZENSHIP_BOXES = {
	"US Citizen": "CB_1",
	"Noncitizen National": "CB_2",
	"Lawful Permanent Resident": "CB_3",
	"Alien Authorized to Work": "CB_4",
}

#: What the template's `State` dropdown will accept. Two-letter USPS codes, the
#: five territories, and Canada and Mexico for a preparer abroad. A value not in
#: here is left blank: a `/Ch` field set to something outside its own option
#: list is a field a viewer renders empty anyway, and blank-on-purpose is at
#: least honest about it.
STATE_CODES = frozenset(
	"AK AL AR AS AZ CA CO CT DC DE FL GA GU HI IA ID IL IN KS KY LA MA MD ME MI MN MO MP MS MT "
	"NC ND NE NH NJ NM NV NY OH OK OR PA PR RI SC SD TN TX UT VA VI VT WA WI WV WY CAN MEX".split()
)

#: Section 1's boxes, keyed by the I-9 Form column that fills each one.
SECTION_1_FIELDS = {
	"legal_last_name": "Last Name (Family Name)",
	"legal_first_name": "First Name Given Name",
	"other_last_names": "Employee Other Last Names Used (if any)",
	"address_street": "Address Street Number and Name",
	"address_city": "City or Town",
	"address_zip": "ZIP Code",
	"email": "Employees E-mail Address",
	"phone": "Telephone Number",
}

#: Section 2's document boxes: which of the three lists, and the four boxes each
#: list gets. The column prefixes are this app's (`list_a_doc_title`); the field
#: names are USCIS's, and they are not consistent between the three lists
#: because the government's file is not.
DOCUMENT_FIELDS = {
	"a": {
		"title": "Document Title 1",
		"authority": "Issuing Authority 1",
		"number": "Document Number 0 (if any)",
		"expiry": "Expiration Date if any",
	},
	"b": {
		"title": "List B Document 1 Title",
		"authority": "List B Issuing Authority 1",
		"number": "List B Document Number 1",
		"expiry": "List B Expiration Date 1",
	},
	"c": {
		"title": "List C Document Title 1",
		"authority": "List C Issuing Authority 1",
		"number": "List C Document Number 1",
		"expiry": "List C Expiration Date 1",
	},
}

#: What each list is called when a note has to name one.
LIST_LABELS = {"a": "List A", "b": "List B", "c": "List C"}

#: How much prose the Additional Information box will hold. It is 309 × 117
#: points and `_prepare_widgets` will shrink the type to 4pt to make text fit, so
#: real limit is legibility rather than geometry: past this, the box is a grey
#: smudge on a photocopy and the record it was summarising is the one to read.
#: What does not fit is not lost — it is on the I-9 Form record, which is where
#: `get_i9_form` reads it from.
ADDITIONAL_INFORMATION_LIMIT = 900

#: Roughly how wide one character is, as a fraction of the font size, in the
#: bold Helvetica the template's widgets declare. Used ONLY to decide whether a
#: value will overflow its box — never to lay anything out. An estimate is the
#: right tool for that question: pypdf does the real measuring when it builds
#: the appearance stream, and all this has to get right is which boxes to hand
#: it.
AVERAGE_GLYPH_EM = 0.55

#: Points of the widget's width taken by its own border and inset.
WIDGET_PADDING = 4.0

#: PDF field flag bit 13 — a text field that wraps rather than scrolls.
FF_MULTILINE = 1 << 12

#: PDF field flag bit 25 — a text field drawn as evenly spaced cells. The SSN
#: box is the only one on this form, and it must never be auto-sized: the cell
#: spacing comes from `/MaxLen`, and a comb rendered at a computed size stops
#: lining up with the boxes printed under it.
FF_COMB = 1 << 24

#: Font resource names in the template that a reader cannot resolve
#: unambiguously, and what to name instead.
#:
#: The template's resource dictionary defines BOTH `/HeBo` (Helvetica-Bold) and
#: `/HeBO` (Helvetica-BoldOblique) — two names differing only in the case of the
#: last letter — and pypdf's lookup reports `/HeBo` missing, falls back to
#: Helvetica, and logs a line for every field of every render. The fallback is
#: fine; a bench log filling with what reads like an error and is not is the
#: problem. So the widgets this app writes name the face they were going to get.
#:
#: `/Helvetica` RATHER THAN `/Helv`, which is what the template's own document
#: default says. `/Helv` is an abbreviation that only means anything if it is in
#: the resource dictionary; `/Helvetica` is one of the fourteen core PDF fonts,
#: which any reader can construct from its own tables and which pypdf adds to
#: the output's resources as it writes. One name works everywhere, the other
#: works where somebody remembered to declare it.
AMBIGUOUS_FONTS = frozenset({"/HeBo", "/HeBO"})
FALLBACK_FONT = "/Helvetica"


# ── availability ────────────────────────────────────────────────────────────


def available() -> bool:
	"""Can this bench fill a federal I-9 at all? Never raises."""
	return bool(HAVE_PYPDF) and os.path.exists(TEMPLATE_PATH)


def requires_sentence() -> str:
	if not HAVE_PYPDF:
		return (
			"the pypdf Python package, which this app declares as a dependency — install it "
			"into the bench's environment with `./env/bin/pip install 'pypdf>=4.0'` and restart"
		)
	return (
		f"the shipped USCIS template at {TEMPLATE_PATH}, which is missing from this "
		f"installation — reinstall the app, or download the form from "
		f"https://www.uscis.gov/i-9 and put it there"
	)


def require() -> None:
	if not available():
		raise ToolError(
			f"this site is missing {requires_sentence()}. The I-9's collected values are "
			f"unaffected — read them with get_i9_form, or print the record from the Desk, "
			f"which needs no PDF library. Nothing was changed."
		)


def template_sha256() -> str:
	"""The digest of the template actually on disk. For the test suite."""
	with open(TEMPLATE_PATH, "rb") as handle:
		return hashlib.sha256(handle.read()).hexdigest()


# ── text and dates ──────────────────────────────────────────────────────────


def _text(value) -> str:
	"""One stored value as a string a PDF text field will take.

	Newlines are folded to spaces: an AcroForm single-line widget renders a
	newline as a box that has visibly lost its second half.
	"""
	if value is None:
		return ""
	return " ".join(str(value).split())


def _us_date(value) -> str:
	"""`2026-08-07` as `08/07/2026`, or blank.

	BLANK RATHER THAN PASSED THROUGH is the whole point. The doctype stores ISO
	dates and the boxes are labelled mmddyyyy; a value this cannot parse is
	something unexpected, and a date box carrying an unexpected string on a
	federal form reads as a date somebody chose.
	"""
	raw = _text(value)
	if not raw:
		return ""
	head = raw.split(" ")[0].split("T")[0]
	parts = head.split("-")
	if len(parts) != 3:
		return ""
	year, month, day = parts
	if not (len(year) == 4 and year.isdigit() and month.isdigit() and day.isdigit()):
		return ""
	return f"{int(month):02d}/{int(day):02d}/{year}"


def _timestamp(value) -> str:
	"""A Datetime as `08/07/2026 14:22`, for the prose in Additional Information."""
	raw = _text(value)
	if not raw:
		return ""
	head = raw.replace("T", " ").split(" ")
	stamped = _us_date(head[0])
	if not stamped:
		return ""
	clock = head[1][:5] if len(head) > 1 and len(head[1]) >= 5 else ""
	return f"{stamped} {clock}".strip()


def _initial(value) -> str:
	"""A middle name as the middle INITIAL the box asks for."""
	name = _text(value)
	return name[0].upper() if name else ""


def _state(value) -> str:
	"""A state as the dropdown spells it, or blank. See `STATE_CODES`."""
	code = _text(value).upper()
	return code if code in STATE_CODES else ""


def _ssn_digits(value) -> str:
	"""Nine digits for a nine-cell comb, or blank.

	A partial number is NOT written. The comb has no way to say "these four are
	the last four", so five empty cells followed by four digits reads as an SSN
	beginning 0000 to anybody who is not looking closely.
	"""
	digits = "".join(character for character in _text(value) if character.isdigit())
	return digits if len(digits) == 9 else ""


def _flag(value) -> bool:
	"""A Frappe Check as a bool, whichever of the four shapes it arrives in."""
	return value in (1, "1", True, "True")


def _document(record: dict, prefix: str) -> dict:
	"""One list's four values off the record.

	THE RECEIPT IS NOT WRITTEN INTO THE TITLE. USCIS's own handbook (M-274 §4.3)
	has the employer record the receipt's title, authority, number and expiry in
	the document boxes exactly as if it were the document, and note that it IS a
	receipt in Additional Information — which is what `_receipt_lines` does. A
	`RECEIPT: ` prefix in the title box would say the same thing in the wrong
	place and would push a 45-character document title past the width of a
	136-point box.
	"""
	title = _text(record.get(f"{prefix}_doc_title"))
	if not title:
		return {}
	return {
		"title": title,
		"authority": _text(record.get(f"{prefix}_doc_authority")),
		"number": _text(record.get(f"{prefix}_doc_number")),
		"expiry": _us_date(record.get(f"{prefix}_doc_expiry")),
	}


# ── the page each half fills ────────────────────────────────────────────────


def _section_1(record: dict, full_ssn: str) -> dict:
	"""Section 1's boxes: who the employee is and how they may work."""
	values = {}
	for column, field in SECTION_1_FIELDS.items():
		text = _text(record.get(column))
		if text:
			values[field] = text

	initial = _initial(record.get("legal_middle_name"))
	if initial:
		values["Employee Middle Initial (if any)"] = initial

	state = _state(record.get("address_state"))
	if state:
		values["State"] = state

	born = _us_date(record.get("date_of_birth"))
	if born:
		values["Date of Birth mmddyyyy"] = born

	ssn = _ssn_digits(full_ssn)
	if ssn:
		values["US Social Security Number"] = ssn

	status = _text(record.get("citizenship_status"))
	box = CITIZENSHIP_BOXES.get(status)
	if box:
		values[box] = "/On"

	# The three identifiers Section 1 accepts, each in the box that belongs to
	# the status that asks for it. A lawful permanent resident's A-number and an
	# authorized alien's A-number are DIFFERENT BOXES on the form even though
	# they are one column here, which is why the status decides.
	alien_number = _text(record.get("alien_registration_number"))
	if alien_number and status == "Lawful Permanent Resident":
		values["3 A lawful permanent resident Enter USCIS or ANumber"] = alien_number
	elif status == "Alien Authorized to Work":
		if alien_number:
			values["USCIS ANumber"] = alien_number
		i94 = _text(record.get("i94_admission_number"))
		if i94:
			values["Form I94 Admission Number"] = i94
		passport = _text(record.get("foreign_passport_number"))
		country = _text(record.get("foreign_passport_country"))
		if passport:
			values["Foreign Passport Number and Country of IssuanceRow1"] = (
				f"{passport} / {country}" if country else passport
			)
		expiry = _us_date(record.get("alien_work_authorization_expiry"))
		if expiry:
			values["Exp Date mmddyyyy"] = expiry

	signed = _us_date(record.get("section_1_signed_at"))
	if signed:
		values["Today's Date mmddyyy"] = signed

	return values


def _section_2(record: dict, employer: dict) -> dict:
	"""Section 2's boxes: what was examined, by whom, and for which employer."""
	values = {}
	for prefix, fields in DOCUMENT_FIELDS.items():
		document = _document(record, f"list_{prefix}")
		for key, field in fields.items():
			if document.get(key):
				values[field] = document[key]

	first_day = _us_date(record.get("hire_date"))
	if first_day:
		values["FirstDayEmployed mmddyyyy"] = first_day

	verifier = _text(record.get("verifier_name"))
	title = _text(record.get("verifier_title"))
	if verifier:
		values["Last Name First Name and Title of Employer or Authorized Representative"] = (
			f"{verifier}, {title}" if title else verifier
		)

	verified = _us_date(record.get("verification_date")) or _us_date(record.get("section_2_signed_at"))
	if verified:
		values["S2 Todays Date mmddyyyy"] = verified

	name = _text((employer or {}).get("name"))
	if name:
		values["Employers Business or Org Name"] = name
	address = _text((employer or {}).get("address"))
	if address:
		values["Employers Business or Org Address"] = address

	return values


def _attestation_lines(record: dict) -> list[str]:
	"""What this app actually holds in place of the two empty signature boxes.

	Said as prose, in the box the form provides for prose, because that is what
	it is. A capture and a timestamp are evidence that somebody attested; they
	are not the attestation, and the difference is the whole reason the
	signature boxes above are empty.
	"""
	lines = []
	for label, stamp, address, capture in (
		(
			"Section 1",
			record.get("section_1_signed_at"),
			record.get("section_1_signed_ip"),
			record.get("section_1_signature"),
		),
		(
			"Section 2",
			record.get("section_2_signed_at"),
			record.get("section_2_signed_ip"),
			record.get("section_2_signature"),
		),
	):
		when = _timestamp(stamp)
		if not when:
			continue
		sentence = f"{label} attested electronically {when}"
		where = _text(address)
		if where:
			sentence += f" from {where}"
		sentence += " via the Farm Ops app"
		if _text(capture):
			sentence += "; signature capture retained on the record"
		lines.append(sentence + ".")
	return lines


def _receipt_lines(record: dict) -> list[str]:
	"""What the form has to say about a receipt, in the box that says it.

	8 CFR 274a.2(b)(1)(vi) is the whole reason this is prose rather than a tick:
	a receipt stands in for a lost, stolen or damaged document for 90 days from
	the hire date, the employee may work throughout, and the employer owes the
	real document at the end of it. The DATE is the part somebody has to act on,
	so it is in the sentence rather than left to be worked out from the hire date.
	"""
	lines = []
	presented = [
		LIST_LABELS[key]
		for key in ("a", "b", "c")
		if _flag(record.get(f"list_{key}_is_receipt")) and _text(record.get(f"list_{key}_doc_title"))
	]
	if not presented and not _flag(record.get("receipt_pending")):
		return lines

	where = " and ".join(presented) if presented else "a document"
	expires = _us_date(record.get("receipt_expires_on"))
	sentence = (
		f"RECEIPT under 8 CFR 274a.2(b)(1)(vi): the {where} entry above records a receipt "
		f"for a replacement document, not the document itself"
	)
	if expires:
		sentence += f". The employee must present the document by {expires}"
	lines.append(sentence + ".")
	return lines


def _employer_lines(employer: dict) -> list[str]:
	"""The employer facts Section 2 collects and has no box for. Today: the EIN.

	See the module docstring. `tools/i9._employer_block` resolves the number from
	I-9 Settings' `business_ein` or the Company's `tax_id`; Form I-9 has nowhere
	to put it, and this is the box the form provides for prose.

	IT IS LABELLED. A bare `12-3456789` in a free-text box beside a receipt
	deadline and two attestation timestamps is a number an inspector has to guess
	the meaning of. `EIN: ` is four characters and removes the guess.
	"""
	ein = _text((employer or {}).get("ein"))
	if not ein:
		return []
	return [f"Employer EIN: {ein}."]


def _overflow_note(reverifications: list) -> list[str]:
	"""Every Supplement B entry that did not fit on the page, named.

	A silent truncation on a reverification history is the failure mode that
	matters most here: the page would look complete and would be missing the
	season somebody is asking about.
	"""
	extra = list(reverifications or [])[SUPPLEMENT_B_ROWS:]
	if not extra:
		return []
	lines = [
		f"Supplement B has {SUPPLEMENT_B_ROWS} rows and this employee has "
		f"{len(reverifications)} reverification(s). The {len(extra)} most recent are listed "
		f"here and are on the I-9 Form record in full; attach a second Supplement B page "
		f"for the file."
	]
	for entry in extra:
		when = _us_date(entry.get("reverification_date"))
		document = _text(entry.get("document_title")) or "document not recorded"
		reason = _text(entry.get("reason"))
		expiry = _us_date(entry.get("document_expiry"))
		lines.append(
			f"  {when or 'date not recorded'}: {reason or 'reason not recorded'} - {document}"
			+ (f", expires {expiry}" if expiry else "")
		)
	return lines


def _additional_information(record: dict, employer: dict, reverifications: list,
                            notes: list | None) -> str:
	"""The Section 2 free-text box, as one string the widget will take.

	ORDER IS DELIBERATE: the receipt first, because it is the only entry that
	names something still owed and has a deadline on it; then the attestations;
	then the employer facts the form has no box for; then anything the caller
	added; then the overflow.

	NEWLINE-SEPARATED, not space-separated. The box is the form's one multiline
	field, and a viewer wraps a paragraph but does not invent the breaks between
	four separate statements — run together, the receipt deadline and the
	Section 1 timestamp read as one sentence.
	"""
	lines: list[str] = []
	lines.extend(_receipt_lines(record))
	lines.extend(_attestation_lines(record))
	lines.extend(_employer_lines(employer))
	lines.extend(_text(note) for note in (notes or []) if _text(note))
	lines.extend(_overflow_note(reverifications))

	body = "\n".join(line for line in lines if line)
	if len(body) > ADDITIONAL_INFORMATION_LIMIT:
		body = body[: ADDITIONAL_INFORMATION_LIMIT - 1].rstrip() + "."
	return body


def _supplement_a(record: dict) -> dict:
	"""The preparer/translator certification, row 1 only.

	ONLY WHERE `preparer_used` IS SET. The certification is an attestation by a
	third party, and a page carrying one made by nobody is worse than a page
	with an empty supplement.

	`preparer_name` is ONE column here and two boxes there, so it is split on
	the last space — which is right for `Ana Ramos` and wrong for a compound
	surname, and is the reason the whole name also goes nowhere else. An
	operator who needs it exact edits the printed page or the record.
	"""
	if not _flag(record.get("preparer_used")):
		return {}

	values = {}
	last = _text(record.get("legal_last_name"))
	first = _text(record.get("legal_first_name"))
	if last:
		values["Last Name Family Name from Section 1"] = last
	if first:
		values["First Name Given Name from Section 1"] = first
	initial = _initial(record.get("legal_middle_name"))
	if initial:
		values["Middle initial if any from Section 1"] = initial

	name = _text(record.get("preparer_name"))
	if name:
		parts = name.rsplit(" ", 1)
		if len(parts) == 2:
			values["Preparer or Translator First Name (Given Name) 0"] = parts[0]
			values["Preparer or Translator Last Name (Family Name) 0"] = parts[1]
		else:
			values["Preparer or Translator Last Name (Family Name) 0"] = name

	address = _text(record.get("preparer_address"))
	if address:
		values["Preparer or Translator Address (Street Number and Name) 0"] = address

	return values


def _supplement_b(record: dict, reverifications: list) -> dict:
	"""Reverification and Rehire — the three rows, oldest first.

	The header repeats the employee's name from Section 1 because the page is
	detachable and USCIS asks for it there.
	"""
	values = {}
	last = _text(record.get("legal_last_name"))
	first = _text(record.get("legal_first_name"))
	if last:
		values["Last Name Family Name from Section 1-2"] = last
	if first:
		values["First Name Given Name from Section 1-2"] = first
	initial = _initial(record.get("legal_middle_name"))
	if initial:
		values["Middle initial if any from Section 1-2"] = initial

	for index, entry in enumerate(list(reverifications or [])[:SUPPLEMENT_B_ROWS]):
		rehired = _us_date(entry.get("rehire_date"))
		if rehired:
			values[f"Date of Rehire {index}"] = rehired
		title = _text(entry.get("document_title"))
		if title:
			values[SUPPLEMENT_B_TITLE_FIELDS[index]] = title
		number = _text(entry.get("document_number"))
		if number:
			values[f"Document Number {index}"] = number
		expiry = _us_date(entry.get("document_expiry"))
		if expiry:
			values[f"Expiration Date {index}"] = expiry

		verifier = _text(entry.get("verifier_name"))
		verifier_title = _text(entry.get("verifier_title"))
		if verifier:
			values[f"Name of Emp or Auth Rep {index}"] = (
				f"{verifier}, {verifier_title}" if verifier_title else verifier
			)
		when = _us_date(entry.get("reverification_date"))
		if when:
			values[f"Todays Date {index}"] = when

		annotations = []
		reason = _text(entry.get("reason"))
		if reason:
			annotations.append(f"Reason: {reason}.")
		authority = _text(entry.get("issuing_authority"))
		if authority:
			annotations.append(f"Issuing authority: {authority}.")
		signed = _timestamp(entry.get("signed_at"))
		if signed:
			annotations.append(f"Attested electronically {signed} via the Farm Ops app.")
		note = _text(entry.get("notes"))
		if note:
			annotations.append(note)
		if annotations:
			values[f"Addtl Info {index}"] = " ".join(annotations)

	return values


# ── the fill ────────────────────────────────────────────────────────────────


def plan(record: dict, employer: dict, reverifications: list | None = None,
         full_ssn: str = "", notes: list | None = None) -> dict:
	"""Every value this form will carry, keyed by page. No PDF is opened.

	Split out from `fill_i9_pdf` so a test — and a caller who wants to know what
	would be printed before printing it — can read the mapping without pypdf on
	the bench. It is also what makes "the SSN box is empty" a thing that can be
	asserted rather than inferred from a rendered page.
	"""
	entries = list(reverifications or [])
	page_one = _section_1(record, full_ssn)
	page_one.update(_section_2(record, employer))
	additional = _additional_information(record, employer, entries, notes)
	if additional:
		page_one["Additional Information"] = additional

	return {
		PAGE_FORM: page_one,
		PAGE_SUPPLEMENT_A: _supplement_a(record),
		PAGE_SUPPLEMENT_B: _supplement_b(record, entries),
	}


def _widget_name(widget) -> str:
	"""The field name a widget answers to — its own, or the one it inherits."""
	own = widget.get("/T")
	if own is not None:
		return str(own)
	parent = widget.get("/Parent")
	return str(parent.get_object().get("/T") or "") if parent is not None else ""


def _inherited(widget, key: str):
	"""One attribute off the widget, or off the field lending it to the widget."""
	if key in widget:
		return widget[key]
	parent = widget.get("/Parent")
	if parent is not None:
		return parent.get_object().get(key)
	return None


def _prepare_widgets(writer, page_index: int, values: dict) -> list[str]:
	"""Make each box about to be written a box that can be written well.

	TWO EDITS, both to the widget's default appearance string and both invisible
	on the page.

	**The font name is normalised.** The template's widgets declare `/HeBo`, and
	its resource dictionary really does define `/HeBo` as Helvetica-Bold — but it
	ALSO defines `/HeBO` as Helvetica-BoldOblique, and pypdf's lookup cannot tell
	the two apart. It reports `/HeBo` missing, falls back to Helvetica, and logs
	a line while doing it. The fallback is fine; the log is not — a bench that
	renders fifty I-9s writes fifty warnings that read like an error and are not
	one. Naming a core font outright (see `FALLBACK_FONT`) gets the same glyphs
	with nothing written to the log.

	**The size is released where the value would overflow.** THE FORM ITSELF
	ALREADY WORKS THIS WAY: the template's document-level default appearance is
	`/Helv 0 Tf` — auto — and the individual widgets override it with a fixed 10
	point, which is right for the name of a person and wrong for
	`Employment Authorization Document (Form I-766)` in a box 136 points wide.
	Acrobat shrinks an auto-sized field to fit; so does pypdf, down to 4 points,
	wrapping as it goes where the field is multiline. Handing it a zero puts the
	box back on the form's own default for exactly the values that need it.

	SHRINK-ONLY, AND ONLY WHERE NEEDED. Auto-sizing every box would make short
	values render LARGER than the form was designed for — pypdf grows a
	single-line field to fill its height — so a name would come out at 14 point
	beside a date at 10. The estimate above decides: a value that fits at the
	declared size keeps the declared size, and nothing else is touched.

	A COMB IS NEVER TOUCHED. See `FF_COMB`.

	Returns the field names whose size was released, for the tests and for the
	render result.
	"""
	released = []
	for reference in writer.pages[page_index].get("/Annots") or []:
		widget = reference.get_object()
		name = _widget_name(widget)
		text = values.get(name)
		if not isinstance(text, str) or not text:
			continue

		appearance = str(_inherited(widget, "/DA") or "")
		parts = appearance.split()
		if "Tf" not in parts:
			continue
		size_index = parts.index("Tf") - 1
		font_index = size_index - 1
		try:
			size = float(parts[size_index])
		except (ValueError, IndexError):  # pragma: no cover - a malformed /DA
			continue

		changed = False
		if font_index >= 0 and parts[font_index] in AMBIGUOUS_FONTS:
			parts[font_index] = FALLBACK_FONT
			changed = True

		flags = int(_inherited(widget, "/Ff") or 0)
		if size > 0 and not (flags & FF_COMB) and _overflows(widget, text, size, flags):
			parts[size_index] = "0"
			released.append(name)
			changed = True

		if changed:
			widget[NameObject("/DA")] = TextStringObject(" ".join(parts))
	return released


def _overflows(widget, text: str, size: float, flags: int) -> bool:
	"""Will this value run past its own box at the size the widget declares?

	An ESTIMATE, and it is only ever used to decide which boxes to hand to
	pypdf's own measuring. A multiline box always says yes — it has to wrap, and
	wrapping is what the auto path does.
	"""
	if flags & FF_MULTILINE:
		return True
	rectangle = widget.get("/Rect")
	if rectangle is None:  # pragma: no cover - a widget with no geometry
		return False
	width = abs(float(rectangle[2]) - float(rectangle[0])) - WIDGET_PADDING
	longest = max(len(line) for line in text.split("\n"))
	return longest * AVERAGE_GLYPH_EM * size > width


def _split_shared_title(writer) -> bool:
	"""Give Supplement B's second document-title box a field of its own.

	See the module docstring for why. Mechanically: the page-4 widget is one of
	two Kids of the AcroForm field `Document Title 1`, so it is promoted to a
	terminal field — given the name, the field type and the inherited attributes
	its parent was lending it — removed from its parent's Kids, and appended to
	the AcroForm's own field list. It is then an ordinary field that happens to
	sit where it always sat.

	Returns whether the split happened. FALSE IS NOT AN ERROR: a future edition
	of the form that has fixed the duplicate name would find nothing to split,
	and the fill that follows would be correct without it. What would be an
	error is writing to both boxes through one name, and that cannot happen —
	the table above sends page 4 to a name page 1 does not have.
	"""
	page = writer.pages[PAGE_SUPPLEMENT_B]
	for reference in page.get("/Annots") or []:
		widget = reference.get_object()
		parent_reference = widget.get("/Parent")
		parent = parent_reference.get_object() if parent_reference is not None else None
		if parent is None or str(parent.get("/T") or "") != SHARED_TITLE_FIELD:
			continue

		widget[NameObject("/T")] = TextStringObject(SUPPLEMENT_B_TITLE_FIELD)
		widget[NameObject("/FT")] = NameObject("/Tx")
		# What the parent was lending this widget by inheritance and will stop
		# lending it the moment it is no longer a Kid: the default appearance
		# string, the field flags, the quadding and any comb length.
		for key in ("/DA", "/Ff", "/Q", "/MaxLen"):
			if key not in widget and key in parent:
				widget[NameObject(key)] = parent[key]
		del widget["/Parent"]

		kids = parent.get("/Kids")
		if kids is not None:
			parent[NameObject("/Kids")] = ArrayObject(
				[kid for kid in kids if kid.idnum != reference.idnum]
			)
		writer.root_object["/AcroForm"][NameObject("/Fields")].append(reference)
		return True
	return False  # pragma: no cover - a template whose duplicate name was fixed


def fill_i9_pdf(record: dict, employer: dict, reverifications: list | None = None,
                full_ssn: str = "", notes: list | None = None) -> bytes:
	"""The shipped USCIS form with this record's values in its boxes.

	Args:
		record: an I-9 Form row, as `tools/i9._i9_fields` reads it.
		employer: `{"name": ..., "address": ..., "ein": ...}` for Section 2's
			employer block, as `tools/i9._employer_block` returns it. The EIN
			has no box on Form I-9 and goes into Additional Information; see the
			module docstring.
		reverifications: Section 3 rows oldest first, as `get_i9_form` returns them.
		full_ssn: nine digits, or "". See the module docstring — this is never
			looked up here, and anything that is not nine digits is dropped.
		notes: extra lines for the Additional Information box.

	Returns:
		PDF bytes. The template on disk is never modified.
	"""
	require()

	writer = PdfWriter(clone_from=TEMPLATE_PATH)
	# BEFORE ANY VALUE IS SET, not after: the split is what makes the two boxes
	# two boxes, and a value written first would already be in both of them.
	_split_shared_title(writer)
	# Belt and braces with the appearance streams pypdf writes below: some
	# viewers regenerate from the field value and ignore the stream, and some do
	# the opposite. Setting both is what makes the page look the same in all of
	# them.
	writer.set_need_appearances_writer(True)

	for page_index, values in plan(record, employer, reverifications, full_ssn, notes).items():
		if not values:
			continue
		# Before the write, not after: the appearance stream is built from the
		# /DA that is on the widget at the moment the value is set.
		_prepare_widgets(writer, page_index, values)
		writer.update_page_form_field_values(writer.pages[page_index], values, auto_regenerate=False)

	buffer = io.BytesIO()
	writer.write(buffer)
	payload = buffer.getvalue()
	if not payload.startswith(b"%PDF"):  # pragma: no cover - defensive
		raise ToolError(
			f"filling the I-9 template produced {len(payload)} byte(s) that are not a PDF. "
			f"Nothing was attached."
		)
	return payload


def file_name_for(record: dict) -> str:
	"""`I-9-I9-2026-0001-Garcia-Maria.pdf`, or as close as the record allows.

	The docname leads because it is the thing that is unique and the thing an
	auditor is given; the name follows because a directory of docnames is
	unreadable.
	"""
	parts = ["I-9", _text(record.get("name")) or "form"]
	for key in ("legal_last_name", "legal_first_name"):
		value = _text(record.get(key))
		if value:
			parts.append(value)
	slug = "-".join(parts)
	safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in slug)
	while "--" in safe:
		safe = safe.replace("--", "-")
	return f"{safe.strip('-')}.pdf"
