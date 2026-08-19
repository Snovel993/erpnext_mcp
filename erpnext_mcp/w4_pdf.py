# SPDX-License-Identifier: MIT
"""Form W-4 drawn on the IRS's own page, and the employer half that was missing.

v0.48.0. Same shape and the same argument as `i9_pdf.py`: the IRS publishes
Form W-4 as a plain-paper fillable PDF, every employer in the country completes
it on plain paper, and it is RETAINED BY THE EMPLOYER rather than filed with
anybody — so the right output is the government's own page with the boxes filled
in, not a reproduction of it and not the field dump a Desk print gives.

    erpnext_mcp/templates/w4_form.pdf   ← shipped byte-for-byte, never edited
    ↓ 54 AcroForm fields across 5 pages, of which this writes 16 on page 1
    fill_w4_pdf(record, employee, employer)  →  PDF bytes

PURE FUNCTION, the same contract `i9_pdf` and `form_pdf_renderer` keep: three
dicts go in and bytes come out, no database read, no attachment write, nothing
to mock. `tools/w4.render_w4_pdf` does the reading and the attaching.

────────────────────────────────────────────────────────────────────────────
WHAT THIS SOLVES: THE W-4 HAD NO EMPLOYER HALF AT ALL
────────────────────────────────────────────────────────────────────────────

v0.28.0 collected the employee's withholding elections — filing status, the
Step 2 checkbox, dependents, other income, deductions, extra withholding — and
the withholding engine has been computing off them ever since. What it never
produced was FORM W-4, and the form has a block the app had nothing for: the
Employers Only row asks for the employer's name and address, the first date of
employment, and the EIN. Three facts the site already holds, on three lines
nothing wrote.

So `render_w4_pdf` resolves them at render time rather than copying them onto
every W-4 row: the employer block comes from I-9 Settings or the Company (the
same `_employer_block` Section 2 of the I-9 uses, because a farm that put its
legal name in one place should not have to put it in two), and the first date of
employment comes from `Employee.date_of_joining`. What IS stored on the W-4 is
who processed it — `employer_signer_name`, `employer_signer_title` and
`employer_signed_at` — because that is a fact about this form and this day, and
nothing else on the site records it.

────────────────────────────────────────────────────────────────────────────
FOUR THINGS THIS DOES NOT PUT ON THE PAGE, AND WHY
────────────────────────────────────────────────────────────────────────────

**THE SIGNATURE IS STAMPED, AND THERE IS NO BOX TO STAMP IT INTO.** v0.51.0.
Step 5's "Employee's signature" and "Date" are printed rules on the IRS page
with NO AcroForm widget behind them — still true, still asserted by name in
`test_w4_pdf.py`, and it is the whole difficulty. `i9_pdf` reads USCIS's own
widget rectangle and never hardcodes a coordinate; here there is no rectangle to
read, so `SIGNATURE_BOX` is measured off the page's own text and documented
against the landmarks it was measured from. That makes it the one piece of
geometry in either module that a template revision could move silently, which is
why `test_w4_pdf.py` re-derives those landmarks from the shipped file rather
than trusting the constant.

What goes in it is the capture on `W-4 Form.signature` — the employee's own
stroke, taken with `signed_at` and `signed_ip` beside it. "This form is not
valid unless you sign it" is exactly why: the app held the signature and the
printed page did not show it, so what came out was an invalid W-4 with all the
right numbers on it.

The page is flattened once anything is stamped, so the retained copy cannot be
edited afterwards. A record with no capture renders precisely as it always did —
every field live, nothing flattened — because that page is one somebody prints
and signs with a pen.

**NO SOCIAL SECURITY NUMBER.** `i9_pdf` prints nine digits when a caller asks
for them by name AND the site switched `store_full_ssn` on, because Form I-9 is
completed by the employer and the number is part of what the employer verified.
A W-4 is completed by the EMPLOYEE, the number is theirs to write, and adding a
second call site that decrypts somebody's SSN to fill a box the employee is
holding a pen over would be a real risk taken for no gain. Step 1(b) is left for
them.

**NO EXEMPT TICK.** `c1_3[0]` claims exemption from withholding for the whole
year under penalty of perjury. Nothing in the W-4 Form doctype records that
claim — the withholding engine has no exempt path — and a tick nobody made is a
false statement on a federal form.

**NO WORKSHEET PAGES.** Pages 3 and 4 are the Multiple Jobs and Deductions
worksheets, 29 fields between them. The IRS's own instruction is that they are
NOT given to the employer: "Keep the worksheet for your records." The doctype
stores the RESULT — `additional_income_from_other_jobs` and `deductions` — and
the result is what goes on page 1.

────────────────────────────────────────────────────────────────────────────
XFA IS REMOVED FROM THE COPY, AND THAT IS THE ONE STRUCTURAL EDIT
────────────────────────────────────────────────────────────────────────────

The IRS's file is a hybrid: an ordinary AcroForm plus an `/XFA` payload
describing the same form in XML. A viewer that understands XFA — Acrobat does —
renders the XFA layout and IGNORES the AcroForm values, so a fill like this one
would produce a file whose fields hold every right answer and whose pages print
blank in the one reader an accountant is most likely to open it in. Dropping
`/XFA` from the generated copy makes every viewer fall back to the AcroForm,
which is the form the boxes were filled in.

THE TEMPLATE ON DISK IS NOT TOUCHED. The IRS's page keeps its XFA; the copy
this app produces does not have it, exactly as `i9_pdf._split_shared_title`
edits a copy and leaves the government's file alone.

`i9_pdf`'s field-name table is the whole interface there and the same is true
here — with one difference worth stating, because it is what a reader trips
over. USCIS named its fields after the boxes (`Employers Business or Org Name`);
the IRS named its fields `f1_12[0]`. There is no way to check this table by
reading it, so `test_w4_pdf.py` checks it against the page's own GEOMETRY: every
name below is asserted to be the widget at the coordinates the label sits on.

PYPDF IS OPTIONAL AT IMPORT TIME, like it is for the I-9. A bench without it
loses exactly one tool — `render_w4_pdf`, and the `generate_w4_pdf` endpoint in
front of it — which says so by name with the pip command to fix it. Every other
W-4 tool, and the whole withholding engine, works without it.
"""

from __future__ import annotations

import hashlib
import io
import os

from . import pdf_signing
from .errors import ToolError

try:  # pragma: no cover - exercised by whichever branch the test bench has
	from pypdf import PdfWriter
	from pypdf.generic import NameObject

	HAVE_PYPDF = True
except Exception:  # pragma: no cover - a bench without the dependency
	PdfWriter = None
	NameObject = None
	HAVE_PYPDF = False


#: The shipped government form. See `templates/README.md` for its provenance.
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "w4_form.pdf")

#: The edition this app's field table was written against, as printed in the
#: bottom-right corner of page 1. Restated in every result so a form rendered
#: from a superseded edition can be identified as one — the IRS revises W-4
#: EVERY YEAR, which makes this the most perishable table in the app.
EDITION = "Form W-4 (2026), OMB No. 1545-0074, Cat. No. 10220Q"

#: The tax year the shipped template prints. A W-4 Form row for a different year
#: still renders — an employer keeps last year's elections and the page is still
#: the right shape — and `render_w4_pdf` says so in its result rather than
#: refusing, because a 2025 election printed on the 2026 form is a readable
#: record and no form at all is not.
TEMPLATE_TAX_YEAR = 2026

#: The template's digest. Asserted by the test suite rather than checked at run
#: time, for the same reason `i9_pdf` does it that way: a mismatch is a
#: development-time fact about the repository, and hashing 200 KB on every
#: render to re-learn it is a cost paid on a site where nothing can have changed.
TEMPLATE_SHA256 = "92444d8856ce55d9e25dca8b6d1420634fc68b11e1ab1f760916ea29ddd312b2"

#: Which page carries what. Zero-based. Only the first is written to.
PAGE_FORM = 0  # Steps 1-5 and the Employers Only block
PAGE_INSTRUCTIONS = 1  # General Instructions — nothing is written here
PAGE_MULTIPLE_JOBS = 2  # Step 2(b) worksheet — the employee keeps it
PAGE_DEDUCTIONS = 3  # Step 4(b) worksheet — the employee keeps it
PAGE_TABLES = 4  # The Multiple Jobs tables

#: The IRS's own field names, which are `f1_12[0]` rather than anything a reader
#: can check. Every one is asserted against the widget's coordinates in
#: `test_w4_pdf.py`, because a mistyped name here would put a value in the wrong
#: box on a federal form and reading the table could never catch it.
_PAGE1 = "topmostSubform[0].Page1[0]"
_STEP1 = f"{_PAGE1}.Step1a[0]"
_STEP3 = f"{_PAGE1}.Step3_ReadOrder[0]"

#: Step 1 — who the employee is.
FIELD_FIRST_NAME = f"{_STEP1}.f1_01[0]"  # First name and middle initial
FIELD_LAST_NAME = f"{_STEP1}.f1_02[0]"
FIELD_ADDRESS = f"{_STEP1}.f1_03[0]"  # Street number and name
FIELD_CITY_STATE_ZIP = f"{_STEP1}.f1_04[0]"
FIELD_SSN = f"{_PAGE1}.f1_05[0]"  # Left empty. See the module docstring.

#: Step 1(c) — the filing status, as three separate one-state checkboxes rather
#: than one radio group. Each has its OWN on-state, which is why the value is in
#: the table beside the name: `/1` on the first box, `/2` on the second, `/3` on
#: the third. Keyed by the W-4 Form doctype's `filing_status` Select, so a
#: status not in this table ticks NOTHING — a form an inspector can see is
#: incomplete rather than one that guessed.
FILING_STATUS_BOXES = {
	"Single or Married Filing Separately": (f"{_PAGE1}.c1_1[0]", "/1"),
	"Married Filing Jointly": (f"{_PAGE1}.c1_1[1]", "/2"),
	"Head of Household": (f"{_PAGE1}.c1_1[2]", "/3"),
}

#: Step 2(c) — the two-jobs checkbox.
FIELD_MULTIPLE_JOBS = f"{_PAGE1}.c1_2[0]"
MULTIPLE_JOBS_ON = "/1"

#: Steps 3 and 4 — the money. Every one is a dollar box and every one is written
#: by `_money`, which prints `2,200.00` and omits a zero entirely.
FIELD_DEPENDENTS_UNDER_17 = f"{_STEP3}.f1_06[0]"  # 3(a)
FIELD_OTHER_DEPENDENTS = f"{_STEP3}.f1_07[0]"  # 3(b)
FIELD_DEPENDENTS_TOTAL = f"{_PAGE1}.f1_08[0]"  # 3
FIELD_OTHER_INCOME = f"{_PAGE1}.f1_09[0]"  # 4(a)
FIELD_DEDUCTIONS = f"{_PAGE1}.f1_10[0]"  # 4(b)
FIELD_EXTRA_WITHHOLDING = f"{_PAGE1}.f1_11[0]"  # 4(c)

#: The exempt-from-withholding tick. Named so the test that asserts it is never
#: written can name it. See the module docstring.
FIELD_EXEMPT = f"{_PAGE1}.c1_3[0]"

#: Employers Only — the three boxes v0.48.0 exists for.
FIELD_EMPLOYER_NAME_ADDRESS = f"{_PAGE1}.f1_12[0]"
FIELD_FIRST_DATE_OF_EMPLOYMENT = f"{_PAGE1}.f1_13[0]"
FIELD_EMPLOYER_EIN = f"{_PAGE1}.f1_14[0]"

#: Step 2(b)'s worksheet result lands in Step 4(c) on the printed form — the
#: form's own instruction, in the sentence "Use the Multiple Jobs Worksheet on
#: page 3 and enter the result in Step 4(c) below." The doctype keeps the two
#: apart (`additional_income_from_other_jobs` and
#: `extra_withholding_per_period`) because the withholding engine treats them
#: differently, and this is where they meet.
#:
#: THEY ARE ADDED RATHER THAN ONE OVERWRITING THE OTHER. An employee with a
#: second job AND an extra $25 a period asked for both, and printing either
#: alone understates what they told the employer to withhold.
ADD_OTHER_JOBS_TO_EXTRA_WITHHOLDING = True

#: How wide the Employers Only name-and-address box is, in characters, at the
#: size the widget declares. The box is 293 points and three lines tall; past
#: this the value is truncated with an ellipsis rather than silently running off
#: the page, and `render_w4_pdf` reports that it happened.
EMPLOYER_BLOCK_LIMIT = 120


# ── availability ────────────────────────────────────────────────────────────


def available() -> bool:
	"""Can this bench fill a federal W-4 at all? Never raises."""
	return bool(HAVE_PYPDF) and os.path.exists(TEMPLATE_PATH)


def requires_sentence() -> str:
	if not HAVE_PYPDF:
		return (
			"the pypdf Python package, which this app declares as a dependency — install it "
			"into the bench's environment with `./env/bin/pip install 'pypdf>=4.0'` and restart"
		)
	return (
		f"the shipped IRS template at {TEMPLATE_PATH}, which is missing from this "
		f"installation — reinstall the app, or download the form from "
		f"https://www.irs.gov/pub/irs-pdf/fw4.pdf and put it there"
	)


def require() -> None:
	if not available():
		raise ToolError(
			f"this site is missing {requires_sentence()}. The W-4's collected elections are "
			f"unaffected — read them with get_w4, and the withholding engine computes from "
			f"them either way. Nothing was changed."
		)


def template_sha256() -> str:
	"""The digest of the template actually on disk. For the test suite."""
	with open(TEMPLATE_PATH, "rb") as handle:
		return hashlib.sha256(handle.read()).hexdigest()


# ── text, dates and money ───────────────────────────────────────────────────


def _text(value) -> str:
	"""One stored value as a string a PDF text field will take."""
	if value is None:
		return ""
	return " ".join(str(value).split())


def _us_date(value) -> str:
	"""`2026-08-07` as `08/07/2026`, or blank.

	BLANK RATHER THAN PASSED THROUGH, for the reason `i9_pdf._us_date` gives at
	length: a date box on a federal form carrying an unexpected string reads as
	a date somebody chose.
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


def _money(value) -> str:
	"""A Currency column as the dollar box wants it, or blank for nothing.

	ZERO IS BLANK, NOT `0.00`. Every money box on this form is optional and the
	IRS's own instruction for each is to leave it empty where it does not apply
	— a page of `0.00`s reads as six deliberate elections rather than as none.

	The `$` is PRINTED ON THE FORM, to the left of every one of these boxes, so
	it is not in the value.
	"""
	raw = _text(value)
	if not raw:
		return ""
	try:
		amount = float(raw)
	except (TypeError, ValueError):
		return ""
	if not amount:
		return ""
	return f"{amount:,.2f}"


def _initial(value) -> str:
	"""A middle name as the middle INITIAL the box asks for."""
	name = _text(value)
	return name[0].upper() if name else ""


def _flag(value) -> bool:
	"""A Frappe Check as a bool, whichever of the four shapes it arrives in."""
	return value in (1, "1", True, "True")


def _first_name_and_initial(employee: dict) -> str:
	"""`Maria E` — one box on the form and two columns everywhere else.

	The box is labelled "First name and middle initial", so the initial goes in
	with the first name rather than nowhere.
	"""
	first = _text(employee.get("first_name"))
	initial = _initial(employee.get("middle_name"))
	return f"{first} {initial}".strip() if first else initial


def _city_state_zip(employee: dict) -> str:
	"""`Yakima, WA 98901` from three columns, skipping whatever is missing."""
	city = _text(employee.get("city"))
	state = _text(employee.get("state"))
	postcode = _text(employee.get("zip"))
	tail = " ".join(part for part in (state, postcode) if part)
	return ", ".join(part for part in (city, tail) if part)


def _employer_block(employer: dict) -> str:
	"""The employer's name and address as the one box the form gives them.

	Truncated rather than allowed to run off the page — the box is three lines
	of a fixed width and a value past that renders as a value with its end
	missing, which on an address is worse than one that says it was cut.
	"""
	name = _text((employer or {}).get("name"))
	address = _text((employer or {}).get("address"))
	block = ", ".join(part for part in (name, address) if part)
	if len(block) > EMPLOYER_BLOCK_LIMIT:
		block = block[: EMPLOYER_BLOCK_LIMIT - 1].rstrip() + "…"
	return block


def employer_block_overflows(employer: dict) -> bool:
	"""Did the employer's name and address have to be cut? For the result."""
	name = _text((employer or {}).get("name"))
	address = _text((employer or {}).get("address"))
	return len(", ".join(part for part in (name, address) if part)) > EMPLOYER_BLOCK_LIMIT


# ── the page ────────────────────────────────────────────────────────────────


def _step_1(employee: dict, record: dict) -> dict:
	"""Who the employee is and how they file. The SSN box is not written."""
	values = {}
	first = _first_name_and_initial(employee)
	if first:
		values[FIELD_FIRST_NAME] = first
	last = _text(employee.get("last_name"))
	if last:
		values[FIELD_LAST_NAME] = last
	street = _text(employee.get("address"))
	if street:
		values[FIELD_ADDRESS] = street
	tail = _city_state_zip(employee)
	if tail:
		values[FIELD_CITY_STATE_ZIP] = tail

	status = _text(record.get("filing_status"))
	box = FILING_STATUS_BOXES.get(status)
	if box:
		values[box[0]] = box[1]
	return values


def _steps_2_to_4(record: dict) -> dict:
	"""The elections: the two-jobs tick, the dependents credits, the adjustments."""
	values = {}
	if _flag(record.get("multiple_jobs")):
		values[FIELD_MULTIPLE_JOBS] = MULTIPLE_JOBS_ON

	for field, column in (
		(FIELD_DEPENDENTS_UNDER_17, "dependents_under_17_amount"),
		(FIELD_OTHER_DEPENDENTS, "other_dependents_amount"),
		(FIELD_DEPENDENTS_TOTAL, "total_dependents_credit"),
		(FIELD_OTHER_INCOME, "other_income"),
		(FIELD_DEDUCTIONS, "deductions"),
	):
		amount = _money(record.get(column))
		if amount:
			values[field] = amount

	extra = _extra_withholding(record)
	if extra:
		values[FIELD_EXTRA_WITHHOLDING] = extra
	return values


def _extra_withholding(record: dict) -> str:
	"""Step 4(c), which is where Step 2(b)'s worksheet result lands too.

	See `ADD_OTHER_JOBS_TO_EXTRA_WITHHOLDING`. Both are per-pay-period dollar
	amounts the employee asked to have withheld on top of the table, and the
	form has one box for the sum.
	"""
	try:
		extra = float(_text(record.get("extra_withholding_per_period")) or 0)
	except (TypeError, ValueError):  # pragma: no cover - a non-numeric Currency
		extra = 0.0
	try:
		jobs = float(_text(record.get("additional_income_from_other_jobs")) or 0)
	except (TypeError, ValueError):  # pragma: no cover - a non-numeric Currency
		jobs = 0.0
	total = extra + (jobs if ADD_OTHER_JOBS_TO_EXTRA_WITHHOLDING else 0.0)
	return _money(total)


def _employers_only(record: dict, employer: dict) -> dict:
	"""The three boxes this release exists for."""
	values = {}
	block = _employer_block(employer)
	if block:
		values[FIELD_EMPLOYER_NAME_ADDRESS] = block
	hired = _us_date(record.get("first_date_of_employment"))
	if hired:
		values[FIELD_FIRST_DATE_OF_EMPLOYMENT] = hired
	ein = _text((employer or {}).get("ein"))
	if ein:
		values[FIELD_EMPLOYER_EIN] = ein
	return values


def plan(record: dict, employee: dict, employer: dict) -> dict:
	"""Every value this form will carry, keyed by page. No PDF is opened.

	Split out from `fill_w4_pdf` for the reason `i9_pdf.plan` is: a test — and a
	caller who wants to know what would be printed before printing it — can read
	the mapping without pypdf on the bench, and "the SSN box is empty" becomes a
	thing that can be asserted rather than inferred from a rendered page.
	"""
	page_one = _step_1(employee or {}, record)
	page_one.update(_steps_2_to_4(record))
	page_one.update(_employers_only(record, employer or {}))
	return {PAGE_FORM: page_one}


#: Step 5's signature rule, in PDF user space on page 1. MEASURED, NOT READ,
#: because the IRS put no widget there — see the module docstring.
#:
#: The landmarks it was measured from, all on page 1 of the shipped 2026 file:
#: the "Sign Here" flag occupies x 36-60 at y 87-99; the caption "Employee's
#: signature (This form is not valid unless you sign it.)" is drawn at x 102.8,
#: y 81.7, so the rule sits just above it; and the Employers Only row's first
#: widget `f1_12[0]` starts at y 66. The band below is clear of all three and
#: stops short of the Date column on the right.
SIGNATURE_BOX = (102.0, 86.0, 400.0, 104.0)


def fill_w4_pdf(
	record: dict, employee: dict, employer: dict, signature: bytes | None = None, flatten: bool | None = None
) -> bytes:
	"""The shipped IRS form with this record's values in its boxes.

	Args:
		record: a W-4 Form row, as `tools/w4._w4_fields` reads it, plus
			`first_date_of_employment` resolved at render time.
		employee: `{"first_name", "middle_name", "last_name", "address",
			"city", "state", "zip"}` — Step 1(a). No SSN; see the module docstring.
		employer: `{"name", "address", "ein"}`, as `tools/i9._employer_block`
			returns it. The Employers Only row.
		signature: the employee's captured signature as image BYTES, or None.
			Bytes rather than a file URL for the reason `i9_pdf` takes bytes:
			this module reads nothing. An unreadable capture is skipped and the
			rule is left for a pen.
		flatten: None — the default — means "flatten when the signature was
			stamped". A signed W-4 must not be editable afterwards; an unsigned
			one is the page somebody prints and signs, so flattening it would
			take away the only thing it is for.

	Returns:
		PDF bytes. The template on disk is never modified.
	"""
	require()

	writer = PdfWriter(clone_from=TEMPLATE_PATH)
	acroform = writer.root_object["/AcroForm"]
	# BEFORE ANY VALUE IS SET, though the order does not strictly matter here —
	# it does in `i9_pdf` and keeping the two modules the same shape is worth
	# more than the two lines saved. See the module docstring for why the XFA
	# payload cannot stay: Acrobat would render it and ignore every value below.
	if NameObject("/XFA") in acroform:
		del acroform[NameObject("/XFA")]
	# Belt and braces with the appearance streams pypdf writes: some viewers
	# regenerate from the field value and ignore the stream, and some do the
	# opposite.
	writer.set_need_appearances_writer(True)

	for page_index, values in plan(record, employee, employer).items():
		if not values:
			continue
		writer.update_page_form_field_values(writer.pages[page_index], values, auto_regenerate=False)

	# Flatten BEFORE stamping, the same order and for the same reason as the
	# I-9: a widget's appearance painted after the ink covers the ink. There is
	# no signature widget here to cover it, but Step 5's Date column and the
	# Employers Only row are close enough that keeping the two modules in step
	# is worth more than the reasoning saved.
	should_flatten = flatten
	if should_flatten is None:
		should_flatten = bool(signature) and pdf_signing.ink_only(signature) is not None
	if should_flatten:
		pdf_signing.flatten(writer)
	if signature:
		x0, y0, x1, y1 = SIGNATURE_BOX
		pdf_signing.stamp(writer, PAGE_FORM, [x0, y0, x1, y1], signature, max_height=y1 - y0)

	buffer = io.BytesIO()
	writer.write(buffer)
	payload = buffer.getvalue()
	if not payload.startswith(b"%PDF"):  # pragma: no cover - defensive
		raise ToolError(
			f"filling the W-4 template produced {len(payload)} byte(s) that are not a PDF. "
			f"Nothing was attached."
		)
	return payload


def file_name_for(record: dict, employee: dict | None = None) -> str:
	"""`W-4-W4-2026-0001-2026-Garcia-Maria.pdf`, or as close as the record allows.

	The docname leads because it is the thing that is unique and the thing an
	auditor is given; the tax year follows because a folder of W-4s is a folder
	of years; the name follows because a directory of docnames is unreadable.
	"""
	parts = ["W-4", _text(record.get("name")) or "form"]
	year = _text(record.get("tax_year"))
	if year:
		parts.append(year)
	person = employee or {}
	for key in ("last_name", "first_name"):
		value = _text(person.get(key))
		if value:
			parts.append(value)
	slug = "-".join(parts)
	safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in slug)
	while "--" in safe:
		safe = safe.replace("--", "-")
	return f"{safe.strip('-')}.pdf"
