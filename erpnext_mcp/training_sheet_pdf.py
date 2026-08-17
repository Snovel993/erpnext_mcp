# SPDX-License-Identifier: MIT
"""The training sign-in sheet, drawn on the page the tax forms are drawn on.

WHAT THIS IS FOR. `complete_training_session` files an Employee Training Record
per attendee and those records are the evidence a compliance matrix reads. What
an auditor asks to SEE is the sheet: one page, the course at the top, a line per
person with their mark on it. Until this existed the sheet was twelve database
rows, and "here are twelve records that agree about the date" is a different
conversation from handing somebody a page.

IT IS ALSO WHAT MAKES THE DOCUMENT SEALABLE. `seal_signed_document` staples a
verification page onto a RENDERED form and hashes the result; with no renderer
there was nothing to seal, so a training session could collect signatures through
the same chain as an I-9 and could not produce the same tamper-evident copy at
the end. `signatures.FORM_HANDLERS` names this module, and the seal follows.

────────────────────────────────────────────────────────────────────────────
IT IS NOT A WORKING COPY, AND THE FOOTER SAYS SO
────────────────────────────────────────────────────────────────────────────

`form_pdf_renderer._Sheet` defaults to the six tax forms' furniture, which
stamps "NOT AN OFFICIAL FORM" across the top — right for a 941 this app drew
from a payroll run, and false here. A training sign-in sheet is not a
reproduction of a government form; there IS no government form. It is the
employer's own record of an afternoon, produced from the session that recorded
it, and it carries the signatures people actually made. So the header note, the
page label and the footer are given, exactly as `pay_stub_pdf` gives its own and
for the same reason.

────────────────────────────────────────────────────────────────────────────
WHAT EACH ROW SAYS, AND WHAT IT DELIBERATELY DOES NOT
────────────────────────────────────────────────────────────────────────────

A row carries the person, the badge that identified them, when it was scanned,
their mark, and when they made it. THE BADGE AND THE TWO TIMESTAMPS ARE ON THE
PAGE rather than in the database only, because they are what distinguishes this
from the sheet an auditor has learned to distrust: thirty names in one hand, all
signed at once, identified by nobody. A page that showed only names and marks
would look exactly like that page.

WHAT IS NOT DRAWN is the GPS fix. It is on the record and it is in the Signing
Evidence row, and printing coordinates against a worker's name on a document that
gets handed around is tracking data on a page that does not need it — the seal's
verification appendix is where an auditor reads it, from the evidence rows,
under the access control those carry.

AN UNSIGNED ROW PRINTS ITS RULED LINE AND SAYS "not signed". It is not omitted
and it is not left ambiguous: a sheet where four of twelve are blank is the true
state of that afternoon, and a page that hid them would be the one document in
this app that flatters the record.
"""

from __future__ import annotations

from . import form_pdf_renderer as render

#: What the page is called, in its own masthead and in its filename.
TITLE = "Training Attendance Record"

#: The band across the top. NOT the tax forms' "verify before filing": nothing
#: here is filed with anybody, and the sentence a reader needs is what the page
#: IS.
HEADER_NOTE = "Employer training record - retain per the regimes listed below"

#: The footer, drawn on every page. It says where the authority lies, which on a
#: record that will be read against a signature is the sentence worth having.
FOOTER = (
    "This record was produced from the training session named above. The signatures shown are "
    "the captures made by each person at the time of signing; the employer's Training Session "
    "record and its Signing Evidence rows are the authority for what was captured, by whom and "
    "where. Retain for the period the regimes on this record require."
)

#: Row geometry. A signature needs room to be a signature — 22 points of it is
#: about what a finger on glass produces at a legible size, and it is what
#: `pdf_signing.MAX_GROWTH` allows an I-9's own line to grow to.
ROW_HEIGHT = 30.0
SIGNATURE_HEIGHT = 22.0

#: Column edges, as fractions of the content width. The signature gets the most
#: because it is the only column whose content is a picture.
COLUMNS = (
    ("#", 0.00, 0.04),
    ("Name", 0.04, 0.28),
    ("Badge", 0.28, 0.44),
    ("Scanned", 0.44, 0.60),
    ("Signature", 0.60, 0.86),
    ("Signed", 0.86, 1.00),
)


def available() -> bool:
    return render.available()


def requires_sentence() -> str:
    return render.requires_sentence()


def require() -> None:
    render.require()


def file_name_for(session: dict) -> str:
    """`training-attendance-TRNS-2026-0001.pdf`, and nothing a caller chose.

    The docname is in it because a private files directory holding forty of
    these should be readable by eye, which is the same reason a captured
    signature's filename carries the row's employee.
    """
    docname = str(session.get("name") or "training-session").strip().replace(" ", "-")
    return f"training-attendance-{docname}.pdf"


def render_sheet(session: dict, attendees: list, company: dict | None = None,
                 signatures: dict | None = None) -> bytes:
    """The sign-in sheet as PDF bytes.

    `session` is `training_sessions.describe`'s output, `attendees` is its
    `attendee_rows`, and `signatures` maps an employee docname to the BYTES of
    their capture. The bytes are passed in rather than read here because this
    module has no database: it is the same contract `form_pdf_renderer` keeps
    with the six tax forms, and it is what lets a page be rendered from a
    fixture and compared against one somebody drew on paper.
    """
    require()
    sheet = render._Sheet(
        "Training Attendance",
        TITLE,
        str(session.get("training_type") or ""),
        header_note=HEADER_NOTE,
        page_label=f"{TITLE} - {session.get('name') or ''}",
        footer=FOOTER,
    )
    _masthead(sheet, session)
    _detail(sheet, session, company or {})
    _coverage(sheet, session)
    _attestation(sheet)
    _table(sheet, attendees, signatures or {})
    _summary(sheet, session, attendees)
    return sheet.render()


def _masthead(sheet, session: dict) -> None:
    sheet.masthead(
        TITLE,
        str(session.get("training_type") or "Training"),
        str(session.get("name") or ""),
        copy_line=f"Status: {session.get('status') or 'Scheduled'}",
    )


def _detail(sheet, session: dict, company: dict) -> None:
    """The two rows of boxes every reader looks at before the names."""
    width = render.CONTENT_WIDTH
    quarter = width / 4.0
    top = sheet.top
    height = 30.0

    trainer = (
        session.get("conducted_by_name")
        or session.get("instructor_name")
        or "not recorded"
    )
    when = str(session.get("session_date") or "")
    times = " to ".join(
        part[:5] for part in (session.get("start_time"), session.get("end_time")) if part
    )

    sheet.box(render.MARGIN, top, quarter, height, "Company",
              str(company.get("name") or session.get("company") or ""))
    sheet.box(render.MARGIN + quarter, top, quarter, height, "Date", when)
    sheet.box(render.MARGIN + quarter * 2, top, quarter, height, "Time",
              times or "not recorded")
    sheet.box(render.MARGIN + quarter * 3, top, quarter, height, "Duration",
              _minutes(session.get("duration_minutes")))
    top += height

    sheet.box(render.MARGIN, top, quarter, height, "Location",
              str(session.get("location") or "not recorded"))
    sheet.box(render.MARGIN + quarter, top, quarter, height, "Conducted by", trainer)
    sheet.box(render.MARGIN + quarter * 2, top, quarter, height, "Provider",
              str(session.get("provider") or session.get("training_source") or ""))
    sheet.box(render.MARGIN + quarter * 3, top, quarter, height, "Delivery",
              str(session.get("delivery_method") or "not recorded"))
    top += height

    sheet.box(render.MARGIN, top, width / 2.0, height, "Regimes this session counts towards",
              ", ".join(session.get("regimes") or []) or "untagged")
    sheet.box(render.MARGIN + width / 2.0, top, width / 2.0, height, "Training expires",
              str(session.get("expires_date") or "one-time - does not expire"))
    sheet.top = top + height + 6


def _coverage(sheet, session: dict) -> None:
    topics = session.get("content_topics_covered") or []
    sheet.heading("Topics covered")
    if topics:
        sheet.bullets(topics)
    else:
        sheet.paragraph("No topics were recorded for this session.")


def _attestation(sheet) -> None:
    """The sentence each person's mark is against, printed above the marks.

    TAKEN FROM THE SIGNATURE BOX rather than written here, so the page prints
    the same words `collect_form_signature` shows on the pad. A sheet that
    attested to something other than what the person was shown is the one
    failure this whole chain exists to make impossible.
    """
    from .tools import signatures as signature_boxes

    box = signature_boxes.BOXES_BY_KEY.get("Training Session.signature")
    if not (box and box.attestation):  # pragma: no cover - the box is in the registry
        return
    sheet.heading("Acknowledgment")
    sheet.paragraph(box.attestation, font=render.FONT_NOTE)
    sheet.spacer(3)


def _table(sheet, attendees: list, signatures: dict) -> None:
    sheet.heading("Attendance")
    _header_row(sheet)
    for index, row in enumerate(attendees, start=1):
        sheet.room_for(ROW_HEIGHT + 2)
        _row(sheet, index, row, signatures.get(str(row.get("employee") or "")))


def _edges() -> list:
    width = render.CONTENT_WIDTH
    return [
        (label, render.MARGIN + start * width, render.MARGIN + end * width)
        for label, start, end in COLUMNS
    ]


def _header_row(sheet) -> None:
    top = sheet.top
    sheet.fill_rect(render.MARGIN, top, render.CONTENT_WIDTH, 12.0, grey=0.88)
    for label, left, right in _edges():
        sheet.text(left + 3, top + 8.5, label, render.FONT_LABEL_BOLD, render.SIZE_SMALL)
    sheet.rect(render.MARGIN, top, render.CONTENT_WIDTH, 12.0)
    sheet.top = top + 12.0


def _row(sheet, index: int, row: dict, capture: bytes | None) -> None:
    top = sheet.top
    edges = {label: (left, right) for label, left, right in _edges()}
    sheet.rect(render.MARGIN, top, render.CONTENT_WIDTH, ROW_HEIGHT)

    baseline = top + ROW_HEIGHT - 8.0
    sheet.text(edges["#"][0] + 3, baseline, str(index), render.FONT_PLAIN, render.SIZE_SMALL)

    name_left, name_right = edges["Name"]
    sheet.text(
        name_left + 3, baseline,
        sheet.clip(str(row.get("employee_name") or row.get("employee") or ""),
                   name_right - name_left - 6, render.FONT_PLAIN, render.SIZE_SMALL),
        render.FONT_PLAIN, render.SIZE_SMALL,
    )

    badge_left, badge_right = edges["Badge"]
    sheet.text(
        badge_left + 3, baseline,
        sheet.clip(str(row.get("badge_scan") or "no badge scanned"),
                   badge_right - badge_left - 6, render.FONT_PLAIN, render.SIZE_SMALL),
        render.FONT_PLAIN, render.SIZE_SMALL,
    )

    scanned_left, scanned_right = edges["Scanned"]
    sheet.text(
        scanned_left + 3, baseline,
        sheet.clip(_moment(row.get("scanned_at")), scanned_right - scanned_left - 6,
                   render.FONT_PLAIN, render.SIZE_SMALL),
        render.FONT_PLAIN, render.SIZE_SMALL,
    )

    # THE RULED LINE IS DRAWN WHETHER OR NOT THERE IS A MARK FOR IT. A row with
    # a line and no signature reads as a row somebody has to go and get signed;
    # a row with no line reads as a row nobody expected to sign.
    sign_left, sign_right = edges["Signature"]
    rule_top = top + ROW_HEIGHT - 5.0
    sheet.line(sign_left + 4, rule_top, sign_right - 4, line_width=0.5)
    drawn = False
    if capture:
        drawn = sheet.ink(
            sign_left + 5, rule_top - 1.0, capture,
            max_width=(sign_right - sign_left) - 10.0,
            max_height=SIGNATURE_HEIGHT,
        )
    if not drawn:
        sheet.text(
            sign_left + 5, rule_top - 3.0,
            "not signed" if not row.get("signed") else "signature on file",
            render.FONT_NOTE, render.SIZE_LABEL,
        )

    signed_left, signed_right = edges["Signed"]
    sheet.text(
        signed_left + 3, baseline,
        sheet.clip(_moment(row.get("signed_at")), signed_right - signed_left - 6,
                   render.FONT_PLAIN, render.SIZE_LABEL),
        render.FONT_PLAIN, render.SIZE_LABEL,
    )

    for _, left, _right in _edges()[1:]:
        sheet.line(left, top, left, line_width=0.4)
    sheet.top = top + ROW_HEIGHT


def _summary(sheet, session: dict, attendees: list) -> None:
    """What the page adds up to, stated rather than left to be counted.

    THE UNSIGNED COUNT IS ON THE PAGE. A reader who has to count blank lines to
    find out whether a sheet is complete will not count them, and the number is
    the one an auditor is actually looking for.
    """
    counted = session.get("attendance") or {}
    sheet.spacer(6)
    sheet.heading("Summary")
    signed = sum(1 for row in attendees if row.get("signed"))
    scanned = sum(1 for row in attendees if row.get("badge_scanned"))
    sheet.bullets(
        [
            f"{len(attendees)} person(s) on this sheet; {counted.get('absent', 0)} recorded as "
            f"not present.",
            f"{scanned} identified by a badge scanned at the door.",
            f"{signed} signed. {len(attendees) - signed} line(s) carry no signature.",
            f"{counted.get('recorded', 0)} training record(s) have been filed from this session.",
        ]
    )


def _minutes(value) -> str:
    try:
        minutes = int(value or 0)
    except (TypeError, ValueError):
        return "not recorded"
    if not minutes:
        return "not recorded"
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _moment(value) -> str:
    """A datetime as `2026-07-01 09:14`, or a dash. Seconds are noise on a page."""
    text = str(value or "").strip()
    if not text:
        return "-"
    return text[:16]
