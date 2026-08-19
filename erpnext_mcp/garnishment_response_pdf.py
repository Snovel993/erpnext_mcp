# SPDX-License-Identifier: MIT
"""The employer's answer to a withholding order, drawn on the tax forms' page.

WHAT AN EMPLOYER OWES ON BEING SERVED IS AN ANSWER. Every withholding regime
assumes one: the federal Income Withholding for Support order tells the employer
to begin within a stated number of days and to notify the issuer when the
employee leaves; a state writ of garnishment normally requires a sworn answer
within twenty or thirty days saying whether the defendant is employed and what
will be withheld; an administrative wage garnishment under 20 U.S.C. 1095a asks
for an employer certification. Until this existed the app could withhold
correctly and could not say so to anybody, which is the half of a garnishment
that gets an employer defaulted rather than merely audited.

IT IS THE EMPLOYER'S OWN LETTER AND THE PAGE SAYS SO. Where the issuing court or
agency prescribes its own answer form, this does not replace it and does not
imitate it — it is the acknowledgment that goes with it, and it is the record
that the employer answered at all. Drawing something that looked like a court's
own form would be the one page in this app that lies about what it is.

WHY THE OTHER ORDERS ARE ON IT. An employer served with a creditor judgment
against somebody who already has a support order does not get a fresh 25% pool:
29 CFR 870.11(b)(1) gives the ordinary garnishment only what is LEFT of the 25%
after support has come out, which is frequently nothing. A court that is told
"we will withhold 25%" and then receives less has been misled by the letter, not
by the payroll run — so the competing orders and the sentence explaining them
are printed, and the withholding figure is stated as what the order ASKS for
against a ceiling, never as a promise of a number.

NO FIGURE ON THIS PAGE IS A PREDICTION. Disposable earnings are a property of a
pay period that has not happened yet, so the page prints the ORDER's terms and
the CEILING that governs them and stops there. A letter to a court carrying a
dollar figure the next payroll run then contradicts is worse than one that
carried none.
"""

from __future__ import annotations

from . import form_pdf_renderer as render

#: What the page is called, in its masthead and in its filename.
TITLE = "Employer Response to Withholding Order"

#: The band across the top. NOT the tax forms' "verify before filing" — nothing
#: here is filed with anybody, and the sentence a reader needs is what the page
#: IS. The same reasoning `pay_stub_pdf` and `training_sheet_pdf` give for
#: overriding the default furniture.
HEADER_NOTE = "Employer acknowledgment of service - retain with the order"

#: The footer, drawn on every page. Where the authority lies, and the boundary
#: this document does not cross.
FOOTER = (
	"This is the employer's own acknowledgment of the order named above, produced from the "
	"employer's records. It does not replace any answer, certification or sworn response the "
	"issuing court or agency requires on its own form, and it is not legal advice. Amounts "
	"actually withheld are limited by the Consumer Credit Protection Act and are reported on "
	"the employee's wage statement each pay period."
)

#: The statutory ceiling sentence per type. Printed rather than computed,
#: because a page that showed 25% as a figure would read as a promise of 25%.
CEILING_SENTENCE = {
	"Child Support": (
		"Withholding for support is limited to 50% of disposable earnings where the employee "
		"supports another spouse or dependent child and 60% where they do not, plus 5 points "
		"where the arrears exceed twelve weeks (15 U.S.C. 1673(b)(2))."
	),
	"Creditor": (
		"Withholding is limited to the lesser of 25% of disposable earnings or the amount by "
		"which disposable earnings exceed 30 times the federal minimum hourly wage for the "
		"week (15 U.S.C. 1673(a)). On a short week the second figure is frequently zero."
	),
	"Tax Levy": (
		"A federal or state tax levy is not subject to the restrictions of Title III of the "
		"Consumer Credit Protection Act (29 CFR 870.11(b)(2)). Withholding is bounded instead "
		"by the amount the notice exempts from levy."
	),
	"Student Loan": (
		"Administrative wage garnishment for a defaulted student loan is limited to 15% of "
		"disposable earnings (20 U.S.C. 1095a(a)(1)), and shares the ordinary 25% pool where "
		"it competes with a creditor garnishment."
	),
}

#: The definition every one of the sentences above measures against, printed
#: once. Disposable earnings is the term a court uses and an employer is
#: routinely asked to confirm it understands.
DISPOSABLE_SENTENCE = (
	"Disposable earnings means the employee's compensation remaining after the deduction of "
	"amounts required by law to be withheld (29 CFR 870.10). It is not net pay: voluntary "
	"deductions, including pre-tax elections such as a retirement deferral or a health "
	"premium, do not reduce it."
)

#: Row geometry for the competing-orders table.
ROW_HEIGHT = 14.0

#: Column edges as fractions of the content width.
COLUMNS = (
	("Rank", 0.00, 0.08),
	("Type", 0.08, 0.30),
	("Case number", 0.30, 0.58),
	("Order asks", 0.58, 0.80),
	("Balance", 0.80, 1.00),
)


def available() -> bool:
	return render.available()


def requires_sentence() -> str:
	return render.requires_sentence()


def require() -> None:
	render.require()


def file_name_for(garnishment: dict) -> str:
	"""`garnishment-response-GARN-2026-0001.pdf`, and nothing a caller chose.

	The docname is in it because a private files directory holding forty of
	these should be readable by eye. The CASE NUMBER is deliberately not, since
	it is quoted verbatim off a court order and is not a filename.
	"""
	docname = str(garnishment.get("name") or "garnishment").strip().replace(" ", "-")
	return f"garnishment-response-{docname}.pdf"


def render_response(
	garnishment: dict,
	company: dict | None = None,
	signatory: dict | None = None,
	competing: list | None = None,
	pay_frequency: str = "",
) -> bytes:
	"""The acknowledgment letter as PDF bytes.

	`garnishment` is `tools.garnishments._describe`'s output. Everything else
	arrives as an argument because this module has no database — the same
	contract `form_pdf_renderer` keeps with the six tax forms, and what lets a
	page be rendered from a fixture and compared against one somebody drew.
	"""
	require()
	sheet = render._Sheet(
		"Employer Response",
		TITLE,
		str(garnishment.get("case_number") or ""),
		header_note=HEADER_NOTE,
		page_label=f"{TITLE} - {garnishment.get('name') or ''}",
		footer=FOOTER,
	)
	_masthead(sheet, garnishment)
	_parties(sheet, garnishment, company or {})
	_order(sheet, garnishment)
	_acknowledgment(sheet, garnishment, pay_frequency)
	_limits(sheet, garnishment)
	_competing(sheet, competing or [])
	_undertakings(sheet, garnishment)
	_signature(sheet, signatory or {})
	return sheet.render()


def _masthead(sheet, garnishment: dict) -> None:
	sheet.masthead(
		TITLE,
		f"{garnishment.get('garnishment_type') or 'Withholding'} order - employer acknowledgment "
		"of service and of withholding",
		str(garnishment.get("case_number") or ""),
		copy_line=f"Employer record: {garnishment.get('name') or ''}",
	)


def _parties(sheet, garnishment: dict, company: dict) -> None:
	"""Who this is to and who it is from, as a letter opens.

	THE ADDRESSEE IS THE FIELD MOST LIKELY TO BE EMPTY, so an empty one prints
	the words that say what is missing rather than a blank rule. A letter that
	silently addressed nobody is one somebody posts.
	"""
	width = render.CONTENT_WIDTH
	half = width / 2.0
	top = sheet.top
	height = 46.0

	addressee = str(garnishment.get("issuing_court_or_agency") or "").strip()
	sheet.box(
		render.MARGIN,
		top,
		half,
		height,
		"To - issuing court or agency",
		[
			addressee or "NOT RECORDED - set issuing_court_or_agency before posting",
			f"Case: {garnishment.get('case_number') or ''}",
		],
		value_font=render.FONT_PLAIN,
		value_size=render.SIZE_SMALL,
	)

	address = [line for line in str(company.get("address") or "").split("\n") if line.strip()][:2]
	sheet.box(
		render.MARGIN + half,
		top,
		half,
		height,
		"From - employer",
		[str(company.get("name") or garnishment.get("company") or ""), *address],
		value_font=render.FONT_PLAIN,
		value_size=render.SIZE_SMALL,
	)
	sheet.top = top + height + 6


def _order(sheet, garnishment: dict) -> None:
	"""The order's own terms, in boxes, so a clerk can check them against paper."""
	width = render.CONTENT_WIDTH
	third = width / 3.0
	quarter = width / 4.0
	top = sheet.top
	height = 28.0

	sheet.box(render.MARGIN, top, third, height, "Employee", _person(garnishment))
	sheet.box(
		render.MARGIN + third,
		top,
		third,
		height,
		"Order type",
		str(garnishment.get("garnishment_type") or ""),
	)
	sheet.box(
		render.MARGIN + third * 2, top, third, height, "Federal priority among orders", _rank(garnishment)
	)
	top += height

	sheet.box(
		render.MARGIN,
		top,
		quarter,
		height,
		"Date order received",
		str(garnishment.get("received_date") or "not recorded"),
	)
	sheet.box(
		render.MARGIN + quarter,
		top,
		quarter,
		height,
		"Withholding begins",
		str(garnishment.get("effective_date") or "not recorded"),
	)
	sheet.box(render.MARGIN + quarter * 2, top, quarter, height, "Order directs", _asks(garnishment))
	sheet.box(
		render.MARGIN + quarter * 3,
		top,
		quarter,
		height,
		"Employer status",
		str(garnishment.get("status") or ""),
	)
	top += height

	# THE BALANCE BOXES ARE OMITTED WHERE THERE IS NO BALANCE rather than drawn
	# as zeros. A support order has no principal to run down, and a court that
	# read "total owed: 0.00" on one would be reading a statement the employer
	# did not mean to make.
	if garnishment.get("has_balance"):
		sheet.money_box(
			render.MARGIN, top, third, height, "Total owed under this order", garnishment.get("total_owed")
		)
		sheet.money_box(
			render.MARGIN + third, top, third, height, "Withheld to date", garnishment.get("total_withheld")
		)
		sheet.money_box(
			render.MARGIN + third * 2,
			top,
			third,
			height,
			"Remaining balance",
			garnishment.get("remaining_balance"),
		)
		top += height
	sheet.top = top + 6


def _person(garnishment: dict) -> str:
	"""The worker's name, and the docname only where there is genuinely no name.

	A court matches this letter to its own defendant by the name on the order.
	Printing HR-EMP-00001 would be a page a court cannot act on, so the fallback
	is marked as what it is rather than passed off as a name.
	"""
	name = str(garnishment.get("employee_name") or "").strip()
	if name:
		return name
	docname = str(garnishment.get("employee") or "").strip()
	return f"{docname} (name not recorded)" if docname else "the employee"


def _rank(garnishment: dict) -> str:
	priority = garnishment.get("federal_priority") or garnishment.get("priority") or 0
	if not priority:
		return "not ranked"
	return f"{priority} of 4"


def _asks(garnishment: dict) -> str:
	"""What the order directs, in its own units. Never a computed dollar figure.

	See the module docstring: disposable earnings belong to a pay period that
	has not happened, and a court told a number the next run contradicts has
	been misled by this page.
	"""
	amount = float(garnishment.get("withholding_amount") or 0)
	if str(garnishment.get("withholding_type") or "") == "Percentage of Disposable":
		return f"{amount:g}% of disposable"
	return render._money(amount)


def _acknowledgment(sheet, garnishment: dict, pay_frequency: str) -> None:
	"""The three sentences the whole page exists to say."""
	sheet.heading("Acknowledgment")
	employee = _person(garnishment)
	received = str(garnishment.get("received_date") or "").strip()
	effective = str(garnishment.get("effective_date") or "").strip()
	served = f"on {received}" if received else "and this employer has recorded its receipt"

	sheet.paragraph(
		f"This employer acknowledges service of the order identified above {served}, and "
		f"confirms that {employee} is employed by this employer."
	)
	sheet.spacer(2)
	period = f" from each {pay_frequency.lower()} pay period" if pay_frequency else " from each pay period"
	sheet.paragraph(
		f"Withholding under this order began or will begin with the pay period covering "
		f"{effective or 'the effective date recorded above'}. The order directs "
		f"{_asks(garnishment)}{period}, subject to the limits below."
	)
	sheet.spacer(2)
	sheet.paragraph(
		"The amount actually withheld in any period is the lesser of what this order directs "
		"and what the applicable limit allows for that period's earnings. Where less is "
		"withheld than the order directs, the shortfall is itemised on the employee's wage "
		"statement for that period.",
		font=render.FONT_NOTE,
	)


def _limits(sheet, garnishment: dict) -> None:
	sheet.heading("Limits that apply")
	kind = str(garnishment.get("garnishment_type") or "")
	sentence = CEILING_SENTENCE.get(kind)
	if sentence:
		sheet.paragraph(sentence)
		sheet.spacer(2)

	stated = float(garnishment.get("max_disposable_earnings_percentage") or 0)
	if stated:
		sheet.paragraph(
			f"This order states its own ceiling of {stated:g}% of disposable earnings. Where "
			"that is lower than the statutory limit above, the lower figure governs."
		)
		sheet.spacer(2)
	sheet.paragraph(DISPOSABLE_SENTENCE, font=render.FONT_NOTE)


def _competing(sheet, competing: list) -> None:
	"""The other live orders, and why the court is being told about them.

	PRINTED WHETHER OR NOT THERE ARE ANY. "No other orders" is a statement a
	court asks for and an employer is frequently required to make; a section
	that simply vanished would leave the reader unable to tell a clean answer
	from an omission.
	"""
	sheet.heading("Other orders against these wages")
	if not competing:
		sheet.paragraph(
			"This employer holds no other active withholding order against this employee's "
			"wages as at the date of this response."
		)
		return

	sheet.paragraph(
		"This employer holds the following other active orders against this employee's wages. "
		"Where a support order and an ordinary garnishment compete, the ordinary garnishment "
		"receives only the part of the 25% limit remaining after the support withholding "
		"(29 CFR 870.11(b)(1)), which may be nothing."
	)
	sheet.spacer(3)
	_header_row(sheet)
	for row in competing:
		sheet.room_for(ROW_HEIGHT + 2)
		_row(sheet, row)


def _edges() -> list:
	width = render.CONTENT_WIDTH
	return [
		(label, render.MARGIN + start * width, render.MARGIN + end * width) for label, start, end in COLUMNS
	]


def _header_row(sheet) -> None:
	top = sheet.top
	sheet.fill_rect(render.MARGIN, top, render.CONTENT_WIDTH, 12.0, grey=0.88)
	for label, left, _right in _edges():
		sheet.text(left + 3, top + 8.5, label, render.FONT_LABEL_BOLD, render.SIZE_SMALL)
	sheet.rect(render.MARGIN, top, render.CONTENT_WIDTH, 12.0)
	sheet.top = top + 12.0


def _row(sheet, row: dict) -> None:
	top = sheet.top
	sheet.rect(render.MARGIN, top, render.CONTENT_WIDTH, ROW_HEIGHT)
	baseline = top + ROW_HEIGHT - 4.0
	balance = float(row.get("remaining_balance") or 0)
	values = {
		"Rank": str(row.get("priority") or "-"),
		"Type": str(row.get("garnishment_type") or ""),
		"Case number": str(row.get("case_number") or ""),
		"Order asks": _asks(row),
		# A zero balance on a competing order means it has none to run down, not
		# that it is paid off — same trap as `total_owed` everywhere else.
		"Balance": render._money(balance) if balance else "ongoing",
	}
	for label, left, right in _edges():
		sheet.text(
			left + 3,
			baseline,
			sheet.clip(values[label], right - left - 6, render.FONT_PLAIN, render.SIZE_SMALL),
			render.FONT_PLAIN,
			render.SIZE_SMALL,
		)
	for _label, left, _right in _edges()[1:]:
		sheet.line(left, top, left, line_width=0.4)
	sheet.top = top + ROW_HEIGHT


def _undertakings(sheet, garnishment: dict) -> None:
	"""What the employer is telling the issuer it will do next.

	These are the three things every regime asks of an employer after the first
	payment, and the three an employer most often fails to do: notify on
	separation, notify on satisfaction, and keep remitting until told to stop.
	"""
	sheet.heading("This employer undertakes")
	items = [
		"To withhold under this order each pay period, in the priority the law gives it, until "
		"the order is satisfied, released or otherwise terminated by the issuing court or agency.",
		"To remit each amount withheld to the payee named in the order within the time the order requires.",
		"To notify the issuing court or agency promptly if this employee's employment ends, "
		"and to state the last date worked and any known subsequent employer.",
	]
	if garnishment.get("has_balance"):
		items.append(
			"To notify the issuing court or agency when the balance stated above has been "
			"withheld in full, and to stop withholding at that point."
		)
	sheet.bullets(items)


def _signature(sheet, signatory: dict) -> None:
	"""A ruled line, a name and a date. Nobody's captured mark goes on this page.

	The signing chain in this app captures marks from workers on a pad. An
	employer's answer to a court is signed by whoever actually signs it, on
	paper, after reading it — so this draws the line for that and does not
	pretend a rendering is an execution.
	"""
	sheet.spacer(10)
	sheet.heading("Signed for the employer")
	width = render.CONTENT_WIDTH
	half = width / 2.0
	top = sheet.top
	height = 34.0

	sheet.box(render.MARGIN, top, half, height, "Signature", "")
	sheet.line(render.MARGIN + 6, top + height - 8.0, render.MARGIN + half - 6, line_width=0.6)
	sheet.box(
		render.MARGIN + half,
		top,
		half / 2.0,
		height,
		"Printed name",
		str(signatory.get("name") or ""),
		value_font=render.FONT_PLAIN,
		value_size=render.SIZE_SMALL,
	)
	sheet.box(
		render.MARGIN + half + half / 2.0,
		top,
		half / 2.0,
		height,
		"Title",
		str(signatory.get("title") or ""),
		value_font=render.FONT_PLAIN,
		value_size=render.SIZE_SMALL,
	)
	sheet.top = top + height + 4

	sheet.box(render.MARGIN, sheet.top, half / 2.0, 24.0, "Date signed", "")
	sheet.line(render.MARGIN + 6, sheet.top + 24.0 - 6.0, render.MARGIN + half / 2.0 - 6, line_width=0.6)
	sheet.top += 24.0 + 4
	sheet.paragraph(
		"The name and title above identify the person this employer authorised to answer. The "
		"signature and date are made on the printed page; this document is not executed by "
		"having been generated.",
		font=render.FONT_NOTE,
	)
