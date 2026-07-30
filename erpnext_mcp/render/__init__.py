# SPDX-License-Identifier: MIT
"""Document writers: PDF, XLSX and DOCX, in the standard library and nothing else.

WHY THIS EXISTS RATHER THAN A DEPENDENCY. `pyproject.toml` says this app has no
runtime dependencies beyond Frappe/ERPNext itself, and that is a promise worth
keeping — it is what makes `bench get-app` on somebody else's bench a safe
operation. Frappe does ship two routes to a document: `frappe.utils.pdf.get_pdf`
shells out to a **wkhtmltopdf binary** that is present in some images and absent
in others, and `frappe.utils.xlsxutils` imports openpyxl. Building a report on
either means a tool that works on the machine it was written on and fails on the
one it was deployed to, at the moment somebody needs the report.

A quarterly investment report and a 1099 form are both a page of text in boxes.
That is a few hundred lines of `zipfile` and byte offsets, it has no binary
dependency, it produces the same bytes on every host, and — the part that
matters most — **the standalone test suite can assert against the real output**
rather than against a mock of a library that is not installed in CI.

WHY COURIER, AND ONLY COURIER. A PDF that names a base-14 font carries no glyph
data, so a viewer draws it from its own copy — but the *writer* still has to know
how wide each glyph is to wrap a line or right-align a column of money. For
Helvetica that means a 230-entry width table, transcribed by hand, where one
wrong number is a column that silently overlaps in the printed copy. Courier is
monospaced: every glyph is exactly 600/1000 em. The arithmetic is then exact
rather than approximately right, decimal points line up down a column because
they cannot do anything else, and the result reads like what it is — a printed
financial statement. That is a deliberate trade of typographic range for
correctness, made in the direction correctness.

WHAT THESE ARE NOT. They are not general-purpose renderers. There are no images,
no colour beyond black on white, no embedded fonts, no styles a caller can
extend. Every one of those would be a reason for the output to differ between
two runs, and these files are evidence: the same inputs must give the same
document, or the archive copy and the printed copy can disagree.
"""

from .docx import DocxDocument
from .pdf import PdfDocument
from .xlsx import Sheet, XlsxWorkbook

__all__ = ("DocxDocument", "PdfDocument", "Sheet", "XlsxWorkbook")
