# SPDX-License-Identifier: MIT
"""The pay stub — the one page on this site that is the record it looks like.

v0.91.0. `tools/payroll.py` computes and stores a Farm Payroll Slip per employee
per period; this draws one of them as the itemised statement of earnings an
employer has to hand a worker with their pay. ORS 652.610(1) and RCW 49.46.020
both require it, both enumerate roughly what is on it, and until now this app
computed every figure on that list and produced no page carrying them.

    render_pay_stub(stub, company_info)  →  PDF bytes

PURE FUNCTION, the same contract `form_pdf_renderer` and `i9_pdf` keep: two
dicts go in and bytes come out. No database read, no attachment write, nothing
to mock. `tools/payroll.render_pay_stub` does the reading and the attaching.

────────────────────────────────────────────────────────────────────────────
IT IS NOT A WORKING COPY, AND THAT IS THE ONE THING IT DOES DIFFERENTLY
────────────────────────────────────────────────────────────────────────────

Every page `form_pdf_renderer` draws is stamped "WORKING COPY - NOT AN OFFICIAL
FORM ... it is not a filing", and that is exactly right for a W-2 nobody can
print on red-ink stock. It would be a FALSE STATEMENT here. A pay stub is not a
copy of a filing held somewhere else — it is the statement itself, it is drawn
from the slip that was actually paid, and a worker holding one has the document
the statute names. So this module borrows `_Sheet` (the boxes, the clipping, the
wrap and the pagination are the same problem) and gives it its own header note
and its own footer. v0.91.0 made that an argument with the tax forms' text as
the default, so nothing about the six forms moved.

WHAT THE FOOTER SAYS INSTEAD is the thing a stub actually has to carry: who to
ask about it, and that the employer's own payroll record is the authority if a
figure is disputed.

────────────────────────────────────────────────────────────────────────────
THE EARNINGS LINES ARE DERIVED AND `earned_gross` IS NOT
────────────────────────────────────────────────────────────────────────────

The slip stores hours, piece units, the piece rate and the gross. It does NOT
store what each component of gross came to, because the engine computes gross in
one pass — piece earnings, break pay at the average piece-rate hourly, and the
FLSA §778.111 half-time premium are not three columns on a record, they are one
number with a method behind it.

So this page itemises what it can — regular hours at the rate, overtime hours at
the rate, units at the piece rate — and then draws a BALANCING LINE for whatever
the itemisation did not account for, named "Other earnings (break pay, overtime
premium)". The alternative was to print three lines that do not add up to the
gross beneath them, which on a wage statement is the failure that starts a claim
rather than answers one. `earned_gross` and `gross_pay` are printed as stored,
always, and the lines above them are the explanation rather than the source.

A NEGATIVE BALANCE IS DRAWN TOO rather than clamped to zero. It means the rate
this page was given does not match the rate the slip was computed at — a salary
structure edited after payroll ran, most likely — and a line reading `-84.00` is
a page somebody queries. A zero is a page that looks right and is not.

────────────────────────────────────────────────────────────────────────────
YTD IS THE CALENDAR YEAR, WHICH IS NOT THIS SITE'S FISCAL YEAR
────────────────────────────────────────────────────────────────────────────

Every year-to-date figure a stub carries is a WITHHOLDING total, and withholding
years are calendar years: the W-2 covers January to December, the FICA wage base
resets on 1 January, and a worker checking their stub against their W-2 is
comparing two calendar-year numbers. This site's Fiscal Year doctype may say
something else entirely — a fruit operation's books often close after harvest —
and using it here would produce a stub whose YTD federal withholding cannot be
reconciled with any form the IRS will ever see.

So the caller sums the calendar year and the page LABELS it as one: the heading
reads "Year to date (calendar 2026)" rather than "YTD", because the difference
between the two years is the sort of thing that is obvious to whoever built the
page and invisible to whoever reads it.

`ytd` may be absent entirely — a site with no earlier slips, or a caller that did
not compute it — and the section is then omitted rather than drawn with zeros. A
column of `0.00` next to "Year to date" reads as a year in which nothing was
withheld, which is a different claim from "not computed".

────────────────────────────────────────────────────────────────────────────
THREE OTHER CHOICES WORTH STATING
────────────────────────────────────────────────────────────────────────────

**THE EMPLOYER SECTION IS OPTIONAL AND OFF BY DEFAULT.** Employer FICA, FUTA and
SUTA are what the farm owes ON TOP of gross; none of it is deducted from anybody
and none of it changes net pay. Some employers show it (it is a real answer to
"what do I cost"), and some workers read any figure on a stub as something taken
off them. `show_employer_contributions` decides, the section is captioned with
the sentence that prevents the misreading, and the default is off.

**NO SOCIAL SECURITY NUMBER, NOT EVEN THE LAST FOUR.** Neither statute asks for
one on a stub, the employee ID identifies the row, and a wage statement is a
piece of paper that gets left in a truck. `w4_pdf` declines to print one for a
related reason and says so at the same length.

**DEDUCTIONS ITEMISE WHERE THE SLIP LETS THEM.** `deduction_lines` — a list of
`{"label", "amount"}` — is drawn line by line when the caller supplies it, and
otherwise everything that is not one of the four named taxes lands on a single
"Other deductions" line derived from `total_deductions`. Deriving rather than
reading a named field is what makes this forward-compatible: a garnishment or a
401(k) row that lands inside `total_deductions` shows up here on the day it
lands, with no change to this module.

REPORTLAB IS OPTIONAL AT IMPORT TIME, through `form_pdf_renderer`, and a bench
without it loses exactly this one tool by name. Every payroll figure stays
readable through `get_payroll_entry` and `get_payroll_register`.
"""

from __future__ import annotations

from .form_pdf_renderer import (
	CONTENT_WIDTH,
	FONT_LABEL,
	FONT_LABEL_BOLD,
	FONT_PLAIN,
	FONT_VALUE,
	MARGIN,
	SIZE_HEADING,
	SIZE_SMALL,
	SIZE_SUBTITLE,
	SIZE_VALUE,
	_address_lines,
	_clean,
	_first,
	_money,
	_Sheet,
	available,
	require,
	requires_sentence,
)

__all__ = [
	"available",
	"require",
	"requires_sentence",
	"render_pay_stub",
	"file_name_for",
	"HEADER_NOTE",
	"FOOTER",
	"OT_MULTIPLIER",
]

#: The line at the head of every page. The tax forms' header says "verify before
#: filing" because they are drafts of something filed elsewhere; this one names
#: what the page is, because a worker who has been handed it is entitled to know
#: that it is the record and not a summary of one.
HEADER_NOTE = "Statement of earnings and deductions"

#: The band at the foot of every page. NOT a disclaimer — see the module
#: docstring. What a stub has to carry is where to take a query, and the fact
#: that the payroll record is the authority behind every figure above.
FOOTER = (
	"This statement is generated from the payroll record for the period shown and lists "
	"every deduction taken from these earnings. Keep it with your records. If any figure "
	"here does not match the hours or units you worked, raise it with your employer before "
	"the next pay period closes - the payroll record for this period is the authority for "
	"every amount on this page, and it can be corrected."
)

#: The overtime premium a printed line is priced at. `payroll_calc.OT_MULTIPLIER`
#: by value rather than by import, and the duplication is deliberate: this is a
#: LABEL on a page ("Overtime hours at 1.5x"), not the arithmetic that paid
#: anybody. The engine's own multiplier is what produced `earned_gross`, and the
#: balancing line absorbs any difference between the two — which is exactly what
#: would happen if the engine changed its method and this constant did not.
OT_MULTIPLIER = 1.5

#: How tall one row of the earnings or deductions table is, in points.
ROW_HEIGHT = 15.0

#: The four columns of the earnings table, measured from the left margin.
_QTY_X = MARGIN + 250.0
_RATE_X = MARGIN + 350.0
_AMOUNT_X = MARGIN + CONTENT_WIDTH


def _num(value, default: float = 0.0) -> float:
	"""A stored figure as a float, with a default where the key is absent.

	The same posture `tools/payroll._num` takes and for the same reason: a
	column that came back None is not a caller mistake and must not raise into a
	page somebody is waiting on.
	"""
	if value is None or value == "":
		return default
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _hours(value) -> str:
	"""Hours as a stub prints them: two places, because quarter hours are paid."""
	amount = _num(value)
	if not amount:
		return ""
	return f"{amount:,.2f}"


def _units(value) -> str:
	"""Piece units. Two places as well — a half bin is a real count."""
	return _hours(value)


def _rate(value) -> str:
	"""A rate, at the precision it was set at. Never blank for a real rate."""
	amount = _num(value)
	if not amount:
		return ""
	return f"{amount:,.4f}".rstrip("0").rstrip(".") if amount < 1 else f"{amount:,.2f}"


# ── the table ───────────────────────────────────────────────────────────────


def _table_head(sheet: _Sheet, first_column: str, quantity: str, rate: str) -> None:
	"""The column captions above an earnings or deductions table."""
	sheet.room_for(ROW_HEIGHT * 2)
	top = sheet.top
	sheet.text(MARGIN + 2, top + SIZE_SMALL, first_column, FONT_LABEL_BOLD, SIZE_SMALL)
	if quantity:
		sheet.text(_QTY_X, top + SIZE_SMALL, quantity, FONT_LABEL_BOLD, SIZE_SMALL, align="right")
	if rate:
		sheet.text(_RATE_X, top + SIZE_SMALL, rate, FONT_LABEL_BOLD, SIZE_SMALL, align="right")
	sheet.text(_AMOUNT_X, top + SIZE_SMALL, "Amount", FONT_LABEL_BOLD, SIZE_SMALL, align="right")
	sheet.top = top + SIZE_SMALL + 3
	sheet.line(MARGIN, sheet.top, MARGIN + CONTENT_WIDTH, line_width=0.5)
	sheet.top += 3


def _row(sheet: _Sheet, label: str, quantity: str, rate: str, amount, bold: bool = False) -> None:
	"""One line of a table. `amount` is a figure; the other three are text.

	`amount=None` draws NO figure at all, which is not the same as drawing zero.
	It is the informational hours row on a piece-rate stub: a count that was not
	paid at a rate, and a `0.00` beside it would read as an hour worked for
	nothing.
	"""
	sheet.room_for(ROW_HEIGHT)
	top = sheet.top
	baseline = top + SIZE_VALUE
	font = FONT_LABEL_BOLD if bold else FONT_LABEL
	value_font = FONT_VALUE if bold else FONT_PLAIN
	sheet.text(MARGIN + 2, baseline, sheet.clip(label, 240, font, SIZE_SUBTITLE), font, SIZE_SUBTITLE)
	if quantity:
		sheet.text(_QTY_X, baseline, quantity, FONT_PLAIN, SIZE_SUBTITLE, align="right")
	if rate:
		sheet.text(_RATE_X, baseline, rate, FONT_PLAIN, SIZE_SUBTITLE, align="right")
	if amount is not None:
		sheet.text(_AMOUNT_X, baseline, _money(amount), value_font, SIZE_VALUE, align="right")
	sheet.top = top + ROW_HEIGHT


def _total_row(sheet: _Sheet, label: str, amount) -> None:
	"""A table's closing line: ruled above, bold, and the figure in the value font."""
	sheet.room_for(ROW_HEIGHT + 4)
	sheet.line(MARGIN, sheet.top, MARGIN + CONTENT_WIDTH, line_width=0.5)
	sheet.top += 3
	_row(sheet, label, "", "", amount, bold=True)


# ── the sections ────────────────────────────────────────────────────────────


def _identity(sheet: _Sheet, stub: dict, company: dict) -> None:
	"""Who this is for, who paid it, and for which days. Two columns."""
	employer_name = _first(company.get("name"), stub.get("company"))
	address = _address_lines(_first(company.get("address")), limit=3)

	top = sheet.top
	sheet.text(MARGIN, top + SIZE_SUBTITLE, "EMPLOYEE", FONT_LABEL_BOLD, SIZE_SMALL)
	sheet.text(MARGIN + 260, top + SIZE_SUBTITLE, "EMPLOYER", FONT_LABEL_BOLD, SIZE_SMALL)
	top += SIZE_SMALL + 4

	left = [
		_first(stub.get("employee_name"), stub.get("employee")),
		f"Employee ID: {_clean(stub.get('employee') or '')}",
	]
	work_state = _clean(stub.get("work_state") or "")
	if work_state:
		left.append(f"Work state: {work_state}")
	pay_type = _clean(stub.get("pay_type") or "")
	if pay_type:
		left.append(f"Paid: {pay_type}")

	right = [employer_name, *address]
	ein = _clean(company.get("ein") or "")
	if ein:
		right.append(f"EIN: {ein}")

	# BOTH COLUMNS ARE DRAWN OFF THE SAME `top`, and the cursor is advanced ONCE
	# at the end to whichever ran longer. Advancing per row would stagger the two
	# against each other the moment an employer's address took a third line.
	line_height = SIZE_SUBTITLE + 3
	for index, body in enumerate(left):
		sheet.text(MARGIN, top + index * line_height + SIZE_SUBTITLE,
		           sheet.clip(body, 250, FONT_LABEL, SIZE_SUBTITLE), FONT_LABEL, SIZE_SUBTITLE)
	for index, body in enumerate(right):
		sheet.text(MARGIN + 260, top + index * line_height + SIZE_SUBTITLE,
		           sheet.clip(body, 250, FONT_LABEL, SIZE_SUBTITLE), FONT_LABEL, SIZE_SUBTITLE)
	sheet.top = top + max(len(left), len(right)) * line_height

	sheet.spacer(4)
	sheet.line(MARGIN, sheet.top, MARGIN + CONTENT_WIDTH, line_width=0.5)
	sheet.spacer(4)

	period = (
		f"Pay period: {_clean(stub.get('pay_period_start') or '')} "
		f"to {_clean(stub.get('pay_period_end') or '')}"
	)
	frequency = _clean(stub.get("pay_frequency") or "")
	if frequency:
		period += f"   ({frequency})"
	sheet.text(MARGIN, sheet.top + SIZE_SUBTITLE, period, FONT_LABEL_BOLD, SIZE_SUBTITLE)
	reference = _clean(stub.get("payroll_entry") or "")
	if reference:
		sheet.text(_AMOUNT_X, sheet.top + SIZE_SUBTITLE, f"Payroll run: {reference}",
		           FONT_LABEL, SIZE_SUBTITLE, align="right")
	sheet.top += SIZE_SUBTITLE + 6


def earnings_lines(stub: dict) -> list[dict]:
	"""What the earnings table will show, before any of it is drawn.

	Split out from the drawing for the reason `i9_pdf.plan` is: a test — and a
	caller who wants to know what a stub would say — can read the itemisation
	without reportlab on the bench, and "the balancing line is what is left of
	earned gross" becomes something that can be asserted rather than inferred
	from a rendered page.

	The last line is ALWAYS the balance and is ALWAYS present when it is not
	zero, including when it is negative. See the module docstring.

	PIECED WORK AND HOURLY WORK ARE NEVER BOTH PRICED ON ONE STUB, and that is
	the rule the whole function turns on. A piece-rate worker's hours ARE the
	hours they picked in: the slip records them for the minimum wage check, not
	because they were paid by the hour. A salary structure may carry an
	`hourly_rate` beside a piece `base_rate` — that is the mixed worker, six
	hours picking and two on a tractor — and pricing the units AND all the hours
	would bill the picking twice and print a gross nobody was paid.

	The record cannot say how the hours split, so the honest page prices the
	units, states the hours WITHOUT a rate, and lets the balancing line carry the
	hourly half under a label that names it. That also keeps the overtime line
	off a piece stub, where the premium is the §778.111 HALF-time one rather
	than the 1.5x this module knows how to print.
	"""
	regular_hours = _num(stub.get("regular_hours"))
	overtime_hours = _num(stub.get("overtime_hours"))
	total_hours = _num(stub.get("total_hours"), regular_hours + overtime_hours)
	piece_units = _num(stub.get("piece_units"))
	piece_rate = _num(stub.get("piece_rate"))
	hourly_rate = _num(stub.get("hourly_rate"))
	earned_gross = _num(stub.get("earned_gross"), _num(stub.get("gross_pay")))

	lines = []
	pieced = bool(piece_units and piece_rate)
	if pieced:
		lines.append({
			"label": "Piece work",
			"quantity": _units(piece_units),
			"rate": _rate(piece_rate),
			"amount": piece_units * piece_rate,
		})
		if total_hours:
			# INFORMATION, NOT AN EARNING: no rate, no amount, and nothing added
			# to the running total. It is the count the minimum wage floor was
			# tested against, and a stub that showed piece units and no hours
			# gives a worker no way to check that test themselves.
			lines.append({
				"label": "Hours worked (paid by the piece, not by the hour)",
				"quantity": _hours(total_hours),
				"rate": "",
				"amount": None,
			})
	else:
		if regular_hours and hourly_rate:
			lines.append({
				"label": "Regular hours",
				"quantity": _hours(regular_hours),
				"rate": _rate(hourly_rate),
				"amount": regular_hours * hourly_rate,
			})
		if overtime_hours and hourly_rate:
			lines.append({
				"label": f"Overtime hours at {OT_MULTIPLIER:g}x",
				"quantity": _hours(overtime_hours),
				"rate": _rate(hourly_rate * OT_MULTIPLIER),
				"amount": overtime_hours * hourly_rate * OT_MULTIPLIER,
			})

	priced = [line for line in lines if line["amount"] is not None]
	balance = earned_gross - sum(line["amount"] for line in priced)
	if abs(balance) >= 0.005:
		if pieced:
			label = "Other earnings (hourly work, break pay, overtime premium)"
		elif priced:
			label = "Other earnings (break pay, overtime premium)"
		else:
			label = "Earnings"
		lines.append({"label": label, "quantity": "", "rate": "", "amount": balance})
	return lines


def _earnings(sheet: _Sheet, stub: dict) -> None:
	sheet.heading("Earnings")
	_table_head(sheet, "Description", "Hours / units", "Rate")
	lines = earnings_lines(stub)
	for line in lines:
		_row(sheet, line["label"], line["quantity"], line["rate"], line["amount"])

	makeup = _num(stub.get("minimum_wage_makeup"))
	if makeup:
		_total_row(sheet, "Earned gross", _num(stub.get("earned_gross")))
		_row(sheet, "Minimum wage adjustment", "", "", makeup)
	_total_row(sheet, "GROSS PAY", _num(stub.get("gross_pay")))

	# Only where the table did not already carry the hours. On a piece-rate stub
	# it did, as its own unpriced row, and repeating it beneath would read as a
	# second count of something.
	total_hours = _num(stub.get("total_hours"))
	if total_hours and not any(line["amount"] is None for line in lines):
		sheet.spacer(2)
		sheet.paragraph(
			f"Total hours worked this period: {_hours(total_hours)}", size=SIZE_SMALL,
		)
	if makeup:
		sheet.paragraph(
			"The minimum wage adjustment is an amount ADDED to your earnings so that this "
			"period's pay meets the minimum wage for the hours worked. Nothing was deducted "
			"to produce it.",
			size=SIZE_SMALL,
		)


def deduction_lines(stub: dict) -> list[dict]:
	"""The deductions table, itemised where the slip allows it.

	The four named taxes always appear in this order when they are non-zero.
	Anything else comes from `deduction_lines` when the caller supplied it, and
	otherwise from the difference between `total_deductions` and the four —
	which is what makes a garnishment written by some later release appear here
	without this module changing. See the module docstring.
	"""
	named = [
		("Federal income tax", _num(stub.get("federal_withholding"))),
		("State income tax", _num(stub.get("state_withholding"))),
		("Social Security", _num(stub.get("social_security"))),
		("Medicare", _num(stub.get("medicare"))),
	]
	lines = [{"label": label, "amount": amount} for label, amount in named if amount]

	supplied = stub.get("deduction_lines") or []
	if supplied:
		for row in supplied:
			amount = _num((row or {}).get("amount"))
			if amount:
				lines.append({
					"label": _clean((row or {}).get("label") or "Other deduction"),
					"amount": amount,
				})
		return lines

	total = _num(stub.get("total_deductions"))
	other = total - sum(amount for _label, amount in named)
	if abs(other) >= 0.005:
		lines.append({"label": "Other deductions", "amount": other})
	return lines


def _deductions(sheet: _Sheet, stub: dict) -> None:
	sheet.heading("Deductions")
	_table_head(sheet, "Description", "", "")
	rows = deduction_lines(stub)
	if not rows:
		_row(sheet, "No deductions were taken from these earnings", "", "", 0.0)
	for line in rows:
		_row(sheet, line["label"], "", "", line["amount"])
	_total_row(sheet, "TOTAL DEDUCTIONS", _num(stub.get("total_deductions")))


def _net(sheet: _Sheet, stub: dict) -> None:
	sheet.spacer(4)
	sheet.room_for(24)
	top = sheet.top
	sheet.fill_rect(MARGIN, top, CONTENT_WIDTH, 22, grey=0.9)
	sheet.text(MARGIN + 6, top + 15, "NET PAY", FONT_LABEL_BOLD, SIZE_HEADING)
	sheet.text(_AMOUNT_X - 4, top + 15, _money(stub.get("net_pay")), FONT_VALUE, SIZE_HEADING,
	           align="right")
	sheet.top = top + 22


#: The year-to-date columns, in the order a stub reads them, and where each one
#: comes from. Keyed off the same names the slip uses, so a caller summing slips
#: builds the `ytd` block by adding up the fields it already has.
_YTD_ROWS = (
	("Gross pay", "gross_pay"),
	("Federal income tax", "federal_withholding"),
	("State income tax", "state_withholding"),
	("Social Security", "social_security"),
	("Medicare", "medicare"),
	("Total deductions", "total_deductions"),
	("Net pay", "net_pay"),
)


def _year_to_date(sheet: _Sheet, stub: dict) -> None:
	"""The calendar year so far. Omitted entirely when it was not computed."""
	ytd = stub.get("ytd") or {}
	if not ytd:
		return
	year = _clean(ytd.get("year") or "")
	sheet.heading(f"Year to date (calendar {year})" if year else "Year to date")
	_table_head(sheet, "Description", "", "")
	for label, key in _YTD_ROWS:
		_row(sheet, label, "", "", _num(ytd.get(key)), bold=(key == "net_pay"))

	periods = ytd.get("periods")
	if periods:
		sheet.spacer(2)
		sheet.paragraph(
			f"Summed from {int(periods)} pay period(s) in calendar year {year}, this one "
			"included. Withholding years are calendar years - these are the figures your "
			"W-2 for this year will be built from.",
			size=SIZE_SMALL,
		)


def _employer_contributions(sheet: _Sheet, stub: dict) -> None:
	"""What the farm paid on top. Never deducted from anybody; says so."""
	rows = [
		("Social Security (employer share)", _num(stub.get("social_security_employer"))),
		("Medicare (employer share)", _num(stub.get("medicare_employer"))),
		("Federal unemployment (FUTA)", _num(stub.get("futa"))),
		("State unemployment (SUTA)", _num(stub.get("state_unemployment"))),
		("Other state employer taxes", _num(stub.get("state_employer_other"))),
	]
	sheet.heading("Employer contributions")
	sheet.paragraph(
		"NOT DEDUCTED FROM YOUR PAY. Every amount below was paid by your employer in "
		"addition to your gross pay. None of it comes out of your earnings and none of it "
		"changes the net pay above; it is shown so the full cost of this period's "
		"employment is on one page.",
		size=SIZE_SMALL,
	)
	sheet.spacer(3)
	_table_head(sheet, "Description", "", "")
	for label, amount in rows:
		if amount:
			_row(sheet, label, "", "", amount)
	_total_row(sheet, "TOTAL EMPLOYER CONTRIBUTIONS", _num(stub.get("total_employer_taxes")))


# ── the page ────────────────────────────────────────────────────────────────


def render_pay_stub(
	stub: dict,
	company_info: dict | None = None,
	show_employer_contributions: bool = False,
) -> bytes:
	"""One employee's pay stub for one period, as PDF bytes.

	Args:
		stub: one Farm Payroll Slip's stored values, flattened, plus the period
			and the employer reference the parent entry carries — as
			`tools/payroll._stub_payload` assembles it. `hourly_rate` is resolved
			from the salary structure by the caller, `ytd` is optional, and
			`deduction_lines` is optional and forward-compatible.
		company_info: `{"name", "address", "ein"}`. Consulted only where the stub
			is silent, the same rule `form_pdf_renderer._employer` follows.
		show_employer_contributions: draw the employer FICA/FUTA/SUTA section.
			Off by default — see the module docstring for why that is a choice
			rather than an oversight.

	Returns:
		PDF bytes. One page for an ordinary stub; the sections paginate
		themselves, and the footer is drawn on every page a table overflows onto.
	"""
	require()

	stub = dict(stub or {})
	company = dict(company_info or {})
	name = _first(stub.get("employee_name"), stub.get("employee"), "employee")
	period = f"{_clean(stub.get('pay_period_start') or '')} to {_clean(stub.get('pay_period_end') or '')}"

	sheet = _Sheet(
		"Pay Stub",
		f"Pay statement - {name} - {period}",
		f"Statement of earnings and deductions for the period {period}",
		header_note=HEADER_NOTE,
		page_label="Pay statement",
		footer=FOOTER,
	)
	sheet.masthead(
		"Pay Statement",
		_first(company.get("name"), stub.get("company")),
		period,
	)
	_identity(sheet, stub, company)
	_earnings(sheet, stub)
	_deductions(sheet, stub)
	_net(sheet, stub)
	_year_to_date(sheet, stub)
	if show_employer_contributions:
		_employer_contributions(sheet, stub)

	payload = sheet.render()
	if not payload.startswith(b"%PDF"):  # pragma: no cover - defensive
		raise ValueError(
			f"drawing the pay stub produced {len(payload)} byte(s) that are not a PDF."
		)
	return payload


def file_name_for(stub: dict) -> str:
	"""`Pay-Stub-PAY-2026-0004-HR-EMP-00001-2026-06-14.pdf`, or as close as it can get.

	The payroll run leads because it is what the file is attached to and a run's
	attachments sort together; the employee follows because a folder of one run's
	stubs is a folder of people; the period end closes it because a folder of one
	person's stubs is a folder of dates.
	"""
	stub = dict(stub or {})
	parts = [
		"Pay-Stub",
		_clean(stub.get("payroll_entry") or "") or "run",
		_clean(stub.get("employee") or "") or "employee",
		_clean(stub.get("pay_period_end") or ""),
	]
	slug = "-".join(part for part in parts if part)
	safe = "".join(character if character.isalnum() or character in "-_" else "-" for character in slug)
	while "--" in safe:
		safe = safe.replace("--", "-")
	return f"{safe.strip('-')}.pdf"
