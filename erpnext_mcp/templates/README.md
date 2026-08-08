# Static form templates

## `i9_form.pdf`

The official **USCIS Form I-9, Employment Eligibility Verification**, downloaded
unmodified from

    https://www.uscis.gov/sites/default/files/document/forms/i-9-paper-version.pdf

| | |
|---|---|
| Agency | U.S. Citizenship and Immigration Services, Department of Homeland Security |
| OMB control number | 1615-0047 |
| Edition | Rev. 08/01/23 |
| Expires | 05/31/2027 |
| Pages | 4 — Section 1 + Section 2, Lists of Acceptable Documents, Supplement A (Preparer/Translator), Supplement B (Reverification and Rehire) |
| SHA-256 | `780f348c34df694bb0b4dbbfaf9f22b99b9757b80d16a37ba89aadf069597281` |
| Bytes | 524,095 |
| AcroForm fields | 133, no XFA |

**IT IS SHIPPED BYTE-FOR-BYTE AND IS NEVER EDITED IN PLACE.** `erpnext_mcp/i9_pdf.py`
opens it read-only, writes the field values into a copy in memory, and hands the
copy back. The file on disk is the government's page and stays the government's
page — the checksum above is asserted by `tests_standalone/test_i9_pdf.py`, so a
template that was swapped, re-saved by a PDF editor, or corrupted in transit
fails the suite rather than quietly producing a form nobody can file.

**A U.S. GOVERNMENT WORK, and therefore not under this app's MIT licence and not
under anyone's copyright** (17 U.S.C. §105). It is redistributed here for the
purpose it was published for: an employer completing it.

### When USCIS revises it

The edition date is printed in the bottom-left corner of every page and is
restated in `i9_pdf.EDITION`. A new edition means:

1. Download the new PDF over this one.
2. Re-run `python3 -m unittest tests_standalone.test_i9_pdf` — the checksum test
   fails by design, and the field-name tests say which of the 133 names moved.
3. Update `EDITION`, the checksum in `i9_pdf.TEMPLATE_SHA256`, and this file.

Nothing else in the app hardcodes the layout. The field *names* are the whole
interface, and they live in one table in `i9_pdf.py`.
