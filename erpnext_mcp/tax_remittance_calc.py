# SPDX-License-Identifier: MIT
"""Tax remittance and deposit arithmetic — PURE FUNCTIONS.

No database reads, no side effects. Same contract as `payroll_calc.py`,
`withholding.py` and `form_generators.py`: everything arrives as an argument, so
a deposit due date or a FUTA line can be checked against a number somebody
worked out on paper.

v0.92.0. `form_generators.py` turns a quarter of payroll into the boxes on a
RETURN — the form that says what was owed. This module answers the other
question, the one `tools/taxforms.py` says in its own docstring that it does not
answer: **when the money has to be in the government's account, and how much.**
A return is filed once a quarter. Deposits are made every payday, and the
penalty for being a day late is a percentage of the deposit.

FOUR THINGS LIVE HERE.

  * **The federal deposit calendar.** Whether an employer is a monthly or a
    semiweekly depositor is decided by a LOOKBACK PERIOD — the four quarters
    ending 30 June of the prior year — and not by anything about the current
    one. Above $50,000 of reported tax in that window the employer deposits
    semiweekly for the whole of the following year, below it monthly. See
    `deposit_frequency`.

  * **Semiweekly due dates, which are not "twice a week".** A Wednesday,
    Thursday or Friday payday is due the following Wednesday; a Saturday,
    Sunday, Monday or Tuesday payday is due the following Friday. See
    `semiweekly_due_date`, and the three-banking-day rule it implements.

  * **Federal holidays**, because every due date above shifts off a weekend or a
    legal holiday, and a deposit calendar that does not know about Juneteenth
    posts a payment a day late in June. See `federal_holidays`.

  * **Form 940 (FUTA)**, which no generator in `form_generators.py` produces.
    6.0% on the first $7,000 of each employee's wages for the year, less a
    credit of up to 5.4% for state unemployment tax actually paid — a net 0.6%
    for an employer in a state that is not under credit reduction, which in 2025
    neither Oregon nor Washington is. See `generate_940_data`.

THE $7,000 CAP IS CONSUMED IN DATE ORDER, PER EMPLOYEE, and that is the whole
difficulty of a 940. A worker who earns $7,000 by the end of April generates
FUTA in Q1 and Q2 and none afterwards, so the quarterly liabilities in Part 5
are not the annual tax split four ways — they are what the cap had left in each
quarter. `futa_taxable_by_quarter` walks the slips chronologically for exactly
this reason, and it is why the function needs the slips rather than a total.

FARMWORKERS ARE NOT AUTOMATICALLY COVERED BY FUTA. An agricultural employer owes
it only if it paid $20,000 or more in cash wages for farm work in ANY calendar
quarter, or employed 10 or more farmworkers for some part of a day in each of 20
or more different weeks. Both tests are computed here and reported on the
result, because an employer under both thresholds files no 940 at all and one
over either of them owes for every dollar from the first — see `_ag_futa_tests`.
The tests are reported, never enforced: this module does not decide whether an
employer is liable, it shows the two numbers the decision is made from.

NOTHING HERE FILES OR PAYS ANYTHING. Federal deposits are made through EFTPS,
Oregon's through Revenue Online and Washington's through its agencies' own
portals. This app has no visibility into any of them, which is why every
function that could report a balance takes `deposits` as an argument and says in
`warnings` when it was not given one.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

#: The lookback threshold that decides the federal deposit schedule. Reported
#: tax ABOVE this in the lookback period makes an employer a semiweekly
#: depositor for the whole of the next calendar year; at or below it, monthly.
LOOKBACK_THRESHOLD = 50000.0

#: The one rule that overrides both schedules. Accumulate $100,000 or more of
#: undeposited liability on ANY day and it is due by the next business day —
#: and a monthly depositor who trips it becomes semiweekly for the rest of that
#: calendar year and all of the next.
NEXT_DAY_THRESHOLD = 100000.0

#: Below this much accumulated FUTA at the end of a quarter, nothing is
#: deposited: the liability is carried into the next quarter instead.
FUTA_DEPOSIT_THRESHOLD = 500.0

#: 2025 FUTA. The gross rate, the per-employee annual wage base, and the largest
#: credit available for state unemployment tax paid on time.
FUTA_RATE = 0.06
FUTA_WAGE_BASE = 7000.0
FUTA_STATE_CREDIT_MAX = 0.054

#: The two thresholds that decide whether an agricultural employer owes FUTA on
#: farm labour at all. Either one is enough; neither means no Form 940.
AG_FUTA_CASH_WAGE_TEST = 20000.0
AG_FUTA_HEADCOUNT_TEST = 10
AG_FUTA_WEEKS_TEST = 20

#: Which weekday a semiweekly deposit is due, by the weekday of the payday.
#: `date.weekday()` is 0 for Monday. Wednesday/Thursday/Friday paydays settle on
#: the following Wednesday; the other four on the following Friday.
_SEMIWEEKLY_WEDNESDAY_PAYDAYS = frozenset({2, 3, 4})

#: Calendar months in each quarter. Duplicated from `form_generators` rather
#: than imported so this module stays free of it — the dependency would be
#: circular the moment a generator wanted a due date.
QUARTER_MONTHS = {"Q1": (1, 2, 3), "Q2": (4, 5, 6), "Q3": (7, 8, 9), "Q4": (10, 11, 12)}

QUARTERS = ("Q1", "Q2", "Q3", "Q4")


# ── Business days and federal holidays ────────────────────────────────────


def federal_holidays(year: int) -> dict[str, str]:
	"""The eleven federal holidays of a year, as {ISO date: name}, AS OBSERVED.

	A holiday that falls on a Saturday is observed the Friday before and one that
	falls on a Sunday the Monday after, which is what moves a deposit deadline —
	the statutory date is not the day the banks and the Federal Reserve are shut.

	NEW YEAR'S DAY OF THE FOLLOWING YEAR IS INCLUDED WHEN IT IS OBSERVED IN THIS
	ONE. 1 January 2028 is a Saturday, so the holiday is observed on Friday 31
	December 2027, and a December semiweekly deposit that ignored it would be
	posted a day late in the last week of the year.
	"""
	year = int(year)
	fixed = (
		(1, 1, "New Year's Day"),
		(6, 19, "Juneteenth National Independence Day"),
		(7, 4, "Independence Day"),
		(11, 11, "Veterans Day"),
		(12, 25, "Christmas Day"),
	)
	floating = (
		(_nth_weekday(year, 1, 0, 3), "Birthday of Martin Luther King, Jr."),
		(_nth_weekday(year, 2, 0, 3), "Washington's Birthday"),
		(_last_weekday(year, 5, 0), "Memorial Day"),
		(_nth_weekday(year, 9, 0, 1), "Labor Day"),
		(_nth_weekday(year, 10, 0, 2), "Columbus Day"),
		(_nth_weekday(year, 11, 3, 4), "Thanksgiving Day"),
	)

	holidays: dict[str, str] = {}
	for month, day, name in fixed:
		holidays[_observed(date(year, month, day)).isoformat()] = name
	for moment, name in floating:
		# A floating holiday is always a Monday or a Thursday and never needs
		# observing, but it costs nothing to route it through the same call.
		holidays[_observed(moment).isoformat()] = name

	# The 31 December case described above.
	next_new_year = _observed(date(year + 1, 1, 1))
	if next_new_year.year == year:
		holidays[next_new_year.isoformat()] = "New Year's Day (observed)"

	return holidays


def is_business_day(moment: date, holidays: dict[str, str] | None = None) -> bool:
	"""Whether a date is a banking day: a weekday that is not a federal holiday."""
	if moment.weekday() >= 5:
		return False
	table = holidays if holidays is not None else federal_holidays(moment.year)
	return moment.isoformat() not in table


def next_business_day(moment: date, holidays: dict[str, str] | None = None) -> date:
	"""The first banking day on or after `moment`.

	The holiday table is rebuilt when the search crosses a year boundary, which
	is the case that matters: a deadline pushed off Christmas can land in
	January, where a table for the old year knows nothing about New Year's Day.
	That happens whether the caller supplied a table or one was built here — a
	31 December search that kept the December table would return 1 January.
	"""
	table = holidays if holidays is not None else federal_holidays(moment.year)
	current = moment
	for _ in range(14):  # No run of non-banking days is anywhere near this long.
		if current.year != moment.year:
			table = federal_holidays(current.year)
		if is_business_day(current, table):
			return current
		current += timedelta(days=1)
	return current


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
	"""The nth given weekday of a month — 3rd Monday, 4th Thursday."""
	first = date(year, month, 1)
	offset = (weekday - first.weekday()) % 7
	return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
	"""The last given weekday of a month. Memorial Day is the only user."""
	last = date(year, month, calendar.monthrange(year, month)[1])
	return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(moment: date) -> date:
	"""Saturday holidays are observed on Friday, Sunday holidays on Monday."""
	if moment.weekday() == 5:
		return moment - timedelta(days=1)
	if moment.weekday() == 6:
		return moment + timedelta(days=1)
	return moment


# ── The federal deposit calendar ──────────────────────────────────────────


def deposit_frequency(lookback_total: float | None) -> dict:
	"""Monthly or semiweekly, from the tax reported in the lookback period.

	Args:
		lookback_total: Total employment tax reported on the four quarterly
			returns in the lookback period. `None` means it is not known.

	Returns:
		`schedule`, the `threshold` it was decided against, and `basis` — a
		sentence saying which of the three cases this is, because "monthly"
		with no reason attached is indistinguishable from "monthly because we
		had nothing to go on".
	"""
	if lookback_total is None:
		return {
			"schedule": "Monthly",
			"lookback_total": None,
			"threshold": LOOKBACK_THRESHOLD,
			"basis": (
				"no lookback total supplied, so the new-employer default of MONTHLY is "
				"assumed. An employer who reported more than "
				f"${LOOKBACK_THRESHOLD:,.0f} in the lookback period is a semiweekly "
				"depositor and every due date below is wrong for them by days."
			),
			"assumed": True,
		}

	total = round(float(lookback_total), 2)
	semiweekly = total > LOOKBACK_THRESHOLD
	return {
		"schedule": "Semiweekly" if semiweekly else "Monthly",
		"lookback_total": total,
		"threshold": LOOKBACK_THRESHOLD,
		"basis": (
			f"${total:,.2f} of employment tax reported in the lookback period, "
			f"{'above' if semiweekly else 'at or below'} the ${LOOKBACK_THRESHOLD:,.0f} "
			f"threshold."
		),
		"assumed": False,
	}


def lookback_period(year: int) -> dict:
	"""The four quarters the deposit schedule for `year` is decided from.

	1 July of the second preceding year to 30 June of the preceding one. The
	name is the trap: the lookback period for 2026 ends in the middle of 2025,
	so an employer whose payroll doubled in late 2025 is still a monthly
	depositor for the whole of 2026.
	"""
	year = int(year)
	return {
		"deposit_year": year,
		"start": date(year - 2, 7, 1).isoformat(),
		"end": date(year - 1, 6, 30).isoformat(),
		"quarters": [
			f"Q3 {year - 2}",
			f"Q4 {year - 2}",
			f"Q1 {year - 1}",
			f"Q2 {year - 1}",
		],
	}


def monthly_due_date(year: int, month: int, holidays: dict[str, str] | None = None) -> date:
	"""A monthly depositor's deadline: the 15th of the following month."""
	year, month = int(year), int(month)
	following = date(year + 1, 1, 15) if month == 12 else date(year, month + 1, 15)
	return next_business_day(following, holidays)


def semiweekly_due_date(payday: date, holidays: dict[str, str] | None = None) -> dict:
	"""A semiweekly depositor's deadline for one payday, with the rule applied.

	THE THREE-BANKING-DAY RULE IS THE PART THAT IS EASY TO GET WRONG. A
	semiweekly depositor always gets at least three banking days after the end of
	the semiweekly period, so when one of the three weekdays following that
	period is a legal holiday the deadline moves out by a further banking day —
	not merely off the holiday itself. A Friday payday in the week before
	Thanksgiving is the case that bites: the Wednesday deadline is not a holiday,
	but the Thursday inside the window is, and the deposit is due Thursday rather
	than Wednesday.

	Returns:
		The payday, the semiweekly period it falls in, the `due_date`, and
		`rule` — the sentence naming which of the two settlement days applied.
	"""
	weekday = payday.weekday()
	if weekday in _SEMIWEEKLY_WEDNESDAY_PAYDAYS:
		period_start = payday - timedelta(days=weekday - 2)  # that Wednesday
		period_end = period_start + timedelta(days=2)  # that Friday
		due = period_end + timedelta(days=5)  # the following Wednesday
		rule = "Wednesday, Thursday or Friday payday — due the following Wednesday."
	else:
		# Saturday(5), Sunday(6), Monday(0) or Tuesday(1). The period runs
		# Saturday to Tuesday, so step forward to its Tuesday.
		period_end = payday + timedelta(days=(1 - weekday) % 7)
		period_start = period_end - timedelta(days=3)  # that Saturday
		due = period_end + timedelta(days=3)  # the following Friday
		rule = "Saturday, Sunday, Monday or Tuesday payday — due the following Friday."

	table = holidays if holidays is not None else federal_holidays(payday.year)
	window = [period_end + timedelta(days=n) for n in range(1, 6)]
	weekdays_after = [d for d in window if d.weekday() < 5][:3]
	extra_days = sum(1 for d in weekdays_after if d.isoformat() in table)

	due_date = next_business_day(due, table)
	for _ in range(extra_days):
		due_date = next_business_day(due_date + timedelta(days=1), table)

	return {
		"payday": payday.isoformat(),
		"semiweekly_period_start": period_start.isoformat(),
		"semiweekly_period_end": period_end.isoformat(),
		"due_date": due_date.isoformat(),
		"rule": rule,
		"holiday_extension_days": extra_days,
	}


def quarter_of_month(month: int) -> str:
	"""Which quarter a calendar month belongs to."""
	for quarter, months in QUARTER_MONTHS.items():
		if int(month) in months:
			return quarter
	raise ValueError(f"month must be 1 to 12, got {month!r}.")


def quarter_end(quarter: str, year: int) -> date:
	"""The last day of a quarter."""
	months = QUARTER_MONTHS.get(quarter)
	if not months:
		raise ValueError(f"quarter must be one of {', '.join(QUARTERS)}, got {quarter!r}.")
	last = months[-1]
	return date(int(year), last, calendar.monthrange(int(year), last)[1])


def quarterly_return_due(quarter: str, year: int, holidays: dict[str, str] | None = None) -> date:
	"""When a quarterly return is due: the last day of the month after the quarter.

	The same date for the federal 941, Oregon's OQ and Washington's ESD report,
	which is the one convenience in this entire calendar.
	"""
	end = quarter_end(quarter, year)
	following_month = end.month + 1
	following_year = end.year
	if following_month > 12:
		following_month, following_year = 1, following_year + 1
	last_day = calendar.monthrange(following_year, following_month)[1]
	return next_business_day(date(following_year, following_month, last_day), holidays)


# ── Form 940 — FUTA ───────────────────────────────────────────────────────


def futa_taxable_by_quarter(
	slips: list[dict],
	wage_base: float,
	prior_wages: dict[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float], set[str]]:
	"""Walk the year's slips in date order, consuming each employee's wage base.

	THE ORDER IS THE POINT. FUTA is owed on the first `wage_base` dollars an
	employee earns in the year and nothing after, so which quarter the tax lands
	in depends entirely on when those dollars were paid. Summing the year and
	dividing by four produces a Part 5 that does not match any real quarter.

	Args:
		slips: Every slip for the year, each with `employee`, `gross_pay` and
			`period_end`.
		wage_base: The per-employee annual cap.
		prior_wages: Wages already paid this year before the first slip in hand,
			per employee. Lets a part-year view consume the cap correctly.

	Returns:
		`(taxable_by_quarter, excess_by_quarter, employees_who_reached_the_cap)`.
	"""
	consumed = dict(prior_wages or {})
	taxable = {quarter: 0.0 for quarter in QUARTERS}
	excess = {quarter: 0.0 for quarter in QUARTERS}
	capped: set[str] = set()

	for slip in sorted(slips, key=lambda s: (str(s.get("period_end") or ""), str(s.get("employee") or ""))):
		employee = str(slip.get("employee") or "")
		gross = _float(slip.get("gross_pay"))
		if gross <= 0:
			continue

		month = _month_of(slip.get("period_end"))
		if month is None:
			continue
		quarter = quarter_of_month(month)

		already = float(consumed.get(employee, 0.0))
		room = max(wage_base - already, 0.0)
		countable = min(gross, room)
		taxable[quarter] += countable
		excess[quarter] += gross - countable
		consumed[employee] = already + gross
		if countable < gross:
			capped.add(employee)

	return (
		{quarter: _money(value) for quarter, value in taxable.items()},
		{quarter: _money(value) for quarter, value in excess.items()},
		capped,
	)


def generate_940_data(slips: list[dict], company_info: dict, year: int) -> dict:
	"""Compute Form 940 — the annual federal unemployment tax return.

	Args:
		slips: Every slip for the calendar year, for every employee.
		company_info: `name`, `ein`, `address`, and optionally `futa_rate` and
			`futa_state_credit_max` as PERCENTAGES, `futa_wage_base`,
			`exempt_payments`, `deposits`, `credit_reduction`,
			`ytd_wages_by_employee` and `state_unemployment_paid`.
		year: The calendar year.

	Returns:
		Form 940's lines 3 to 17, the Part 5 quarterly liabilities, the two
		agricultural coverage tests, and `warnings`.
	"""
	warnings: list[str] = []
	slips = list(slips or [])

	rate = _float(company_info.get("futa_rate"), FUTA_RATE * 100) / 100.0
	credit = _float(company_info.get("futa_state_credit_max"), FUTA_STATE_CREDIT_MAX * 100) / 100.0
	wage_base = _float(company_info.get("futa_wage_base"), FUTA_WAGE_BASE)
	effective_rate = max(rate - credit, 0.0)

	total_payments = _sum(slips, "gross_pay")
	exempt = _money(company_info.get("exempt_payments"))

	taxable_by_quarter, excess_by_quarter, capped = futa_taxable_by_quarter(
		slips, wage_base, company_info.get("ytd_wages_by_employee") or {}
	)
	over_base = _money(sum(excess_by_quarter.values()))
	taxable_wages = _money(sum(taxable_by_quarter.values()))

	# Line 7 is line 3 less lines 4 and 5. The walk above already removed the
	# over-base dollars, so exempt payments are the only thing left to take off —
	# and they are taken off the taxable figure rather than the gross, because a
	# payment that is exempt from FUTA never consumed anybody's wage base either.
	if exempt > 0:
		taxable_wages = _money(max(taxable_wages - exempt, 0.0))
		warnings.append(
			f"${exempt:,.2f} of exempt payments were subtracted from taxable FUTA wages as "
			"a lump sum. They were NOT excluded from the per-employee wage-base walk, so "
			"where the exempt pay belongs to somebody who also reached the "
			f"${wage_base:,.2f} base, line 7 is understated. Exempt pay has to be kept off "
			"the slips to be handled exactly."
		)

	line8 = _money(taxable_wages * effective_rate)
	credit_reduction = _money(company_info.get("credit_reduction"))
	line12 = _money(line8 + credit_reduction)
	line13 = _money(company_info.get("deposits"))
	balance = _money(line12 - line13)

	liabilities = {
		quarter: _money(amount * effective_rate) for quarter, amount in taxable_by_quarter.items()
	}
	if exempt > 0 and taxable_wages > 0:
		# Keep Part 5 reconciling to line 12, which the form requires of it.
		scale = taxable_wages / max(sum(taxable_by_quarter.values()), 0.01)
		liabilities = {quarter: _money(amount * scale) for quarter, amount in liabilities.items()}

	ag_tests = _ag_futa_tests(slips)

	if capped:
		warnings.append(
			f"{len(capped)} employee(s) reached the ${wage_base:,.2f} FUTA wage base during "
			f"the year: {', '.join(sorted(capped))}. Their later pay is on line 5 and is not "
			"taxed."
		)
	if not company_info.get("ytd_wages_by_employee"):
		warnings.append(
			"no prior-year-to-date wages supplied, so the wage base was consumed from the "
			"first slip in hand. Correct for a full calendar year; for a part-year view it "
			"overstates taxable wages for anybody who had already reached the base."
		)
	if credit >= rate:
		warnings.append(
			f"the state credit ({credit * 100:.2f}%) is not less than the FUTA rate "
			f"({rate * 100:.2f}%), so the effective rate is zero and line 8 is zero. Check "
			"the FICA Configuration for this year."
		)
	if not company_info.get("deposits"):
		warnings.append(
			"no deposit total supplied, so line 13 is zero and the whole tax shows as a "
			"balance due. FUTA deposits are made through EFTPS and this app does not see them."
		)
	if not company_info.get("ein"):
		warnings.append("no EIN recorded on the company — a 940 cannot be filed without one.")
	if not ag_tests["liable"]:
		warnings.append(
			"NEITHER AGRICULTURAL FUTA TEST IS MET on this payroll: cash wages peaked at "
			f"${ag_tests['highest_quarter_cash_wages']:,.2f} in a quarter (test is "
			f"${AG_FUTA_CASH_WAGE_TEST:,.0f}) and {ag_tests['weeks_with_ten_or_more']} week(s) "
			f"had {AG_FUTA_HEADCOUNT_TEST} or more farmworkers (test is {AG_FUTA_WEEKS_TEST}). "
			"An employer under both tests owes no FUTA on farm labour and files no Form 940. "
			"These figures come from this app's payroll alone — farm work paid outside it "
			"counts toward both tests."
		)
	if not slips:
		warnings.append(f"no payroll slips found for {year}; every line is zero.")

	return {
		"form_type": "940",
		"tax_year": int(year),
		"period_start": date(int(year), 1, 1).isoformat(),
		"period_end": date(int(year), 12, 31).isoformat(),
		"due_date": next_business_day(date(int(year) + 1, 1, 31)).isoformat(),
		"employer": {
			"name": company_info.get("name") or "",
			"ein": company_info.get("ein") or "",
			"address": company_info.get("address") or "",
		},
		"futa_rate": _money(rate * 100),
		"state_credit_rate": _money(credit * 100),
		"effective_rate": round(effective_rate * 100, 4),
		"wage_base": _money(wage_base),
		"line3_total_payments": _money(total_payments),
		"line4_payments_exempt_from_futa": exempt,
		"line5_payments_over_wage_base": over_base,
		"line6_subtotal": _money(exempt + over_base),
		"line7_total_taxable_futa_wages": taxable_wages,
		"line8_futa_tax_before_adjustments": line8,
		"line11_credit_reduction": credit_reduction,
		"line12_total_futa_tax": line12,
		"line13_futa_tax_deposited": line13,
		"line14_balance_due": _money(balance) if balance > 0 else 0.0,
		"line15_overpayment": _money(-balance) if balance < 0 else 0.0,
		"line16_quarterly_liabilities": liabilities,
		"line17_total_liability": _money(sum(liabilities.values())),
		"taxable_wages_by_quarter": taxable_by_quarter,
		"deposit_threshold": FUTA_DEPOSIT_THRESHOLD,
		"quarterly_deposits": futa_deposit_plan(liabilities, int(year)),
		"agricultural_coverage": ag_tests,
		"employee_count": len({s.get("employee") for s in slips if s.get("employee")}),
		"slip_count": len(slips),
		"warnings": warnings,
	}


def futa_deposit_plan(liabilities: dict[str, float], year: int) -> list[dict]:
	"""When each quarter's FUTA has to be deposited, and whether it has to be.

	Under `FUTA_DEPOSIT_THRESHOLD` at the end of a quarter, nothing is deposited
	and the liability is CARRIED FORWARD into the next quarter — so a small
	employer can reach Q4 having deposited nothing all year and owe the lot with
	the return. The carry is what makes this a walk rather than four independent
	comparisons.
	"""
	plan = []
	carried = 0.0
	for quarter in QUARTERS:
		amount = _money(liabilities.get(quarter, 0.0))
		accumulated = _money(carried + amount)
		due = accumulated > FUTA_DEPOSIT_THRESHOLD
		plan.append({
			"quarter": quarter,
			"liability": amount,
			"carried_in": _money(carried),
			"accumulated": accumulated,
			"deposit_required": due,
			"deposit_amount": accumulated if due else 0.0,
			"due_date": quarterly_return_due(quarter, year).isoformat(),
			"note": (
				f"accumulated FUTA is over ${FUTA_DEPOSIT_THRESHOLD:,.0f}, so it is deposited "
				"by the last day of the month after the quarter."
				if due
				else (
					f"accumulated FUTA is ${accumulated:,.2f}, at or under the "
					f"${FUTA_DEPOSIT_THRESHOLD:,.0f} threshold — nothing is deposited and the "
					"liability carries into the next quarter."
				)
			),
		})
		carried = 0.0 if due else accumulated
	return plan


def _ag_futa_tests(slips: list[dict]) -> dict:
	"""The two thresholds that decide whether farm labour is subject to FUTA.

	THE WEEKS TEST IS AN ESTIMATE AND SAYS SO. The statute counts a week in which
	ten or more farmworkers were employed for some part of ANY day; a pay period
	is the finest grain a payroll register has, so a slip is attributed to every
	ISO week its period touches. That is right for anybody who worked through the
	period and generous to anybody who worked one day of it — which is the
	direction that matters, since the statute itself asks only whether they were
	employed for part of a day.
	"""
	cash_by_quarter = {quarter: 0.0 for quarter in QUARTERS}
	weeks: dict[tuple[int, int], set[str]] = {}

	for slip in slips:
		gross = _float(slip.get("gross_pay"))
		month = _month_of(slip.get("period_end"))
		if month is not None:
			cash_by_quarter[quarter_of_month(month)] += gross

		employee = str(slip.get("employee") or "")
		if not employee:
			continue
		start = _as_date(slip.get("period_start")) or _as_date(slip.get("period_end"))
		end = _as_date(slip.get("period_end")) or start
		if not start or not end or end < start:
			continue
		current = start
		while current <= end:
			iso = current.isocalendar()
			weeks.setdefault((iso[0], iso[1]), set()).add(employee)
			current += timedelta(days=7 - current.weekday())

	cash_by_quarter = {quarter: _money(value) for quarter, value in cash_by_quarter.items()}
	highest = max(cash_by_quarter.values()) if cash_by_quarter else 0.0
	qualifying_weeks = sum(1 for crew in weeks.values() if len(crew) >= AG_FUTA_HEADCOUNT_TEST)

	wage_test = highest >= AG_FUTA_CASH_WAGE_TEST
	weeks_test = qualifying_weeks >= AG_FUTA_WEEKS_TEST

	return {
		"cash_wages_by_quarter": cash_by_quarter,
		"highest_quarter_cash_wages": highest,
		"cash_wage_test_threshold": AG_FUTA_CASH_WAGE_TEST,
		"cash_wage_test_met": wage_test,
		"weeks_with_ten_or_more": qualifying_weeks,
		"weeks_test_threshold": AG_FUTA_WEEKS_TEST,
		"headcount_test_threshold": AG_FUTA_HEADCOUNT_TEST,
		"weeks_test_met": weeks_test,
		"liable": wage_test or weeks_test,
		"cash_wage_test_source": (
			"EXACT — cash wages summed from the slips themselves, bucketed by the quarter "
			"each pay period ends in."
		),
		"weeks_test_source": (
			f"DERIVED, NOT MEASURED — a slip is attributed to every ISO week its pay period "
			f"touches, and a week counts when {AG_FUTA_HEADCOUNT_TEST} or more distinct "
			"employees are attributed to it. The statute counts employment for part of any "
			"DAY, which a payroll register does not record; this errs generous, which is "
			"the direction that avoids understating liability."
		),
		"basis": (
			"either test is enough, and an employer that meets one owes FUTA on farm "
			"wages from the first dollar of the year — not only from the week the test "
			"was met. An employer that meets NEITHER owes no FUTA on farm labour at all "
			"and files no Form 940: the tax is not reduced, it does not apply. Wages paid "
			"for farm work outside this app count toward both tests and are not visible "
			"here, so these figures are a floor for the tests and not a determination."
		),
	}


# ── Oregon Form 132 — the employee detail the OQ is filed with ────────────


def generate_or_132_data(
	or_slips: list[dict],
	company_info: dict,
	quarter: str,
	year: int,
) -> dict:
	"""Oregon's Employee Detail Report — one row per employee, filed WITH the OQ.

	AN OQ WITHOUT A 132 IS NOT A FILING. The OQ carries the employer's totals;
	Form 132 carries the per-employee wages and hours that Oregon assesses
	benefit eligibility from. `form_generators.py` produces the first and not the
	second, which is why this is here.

	HOURS ARE ROUNDED DOWN TO WHOLE HOURS, which is Oregon's instruction and not
	a display choice — 39.9 hours is reported as 39. Rounding to nearest would
	report an hour nobody worked.

	THE UI WAGE BASE IS CONSUMED FROM 1 JANUARY, NOT FROM THE START OF THE
	QUARTER. This is the bug worth naming: an employee who reached the base in
	the spring has no subject wages in Q3, and a Q3 report that applies the cap
	to Q3's wages alone reports subject wages that were already exhausted. The
	caller supplies `ytd_wages_by_employee` for the quarters before this one;
	absent, the cap starts fresh here and `warnings` says the figure is a
	ceiling rather than a number.

	Args:
		or_slips: Every slip with Oregon wages whose period ends in the quarter.
		company_info: `name`, `state_ids`, optional `or_ui_wage_base`,
			`ytd_wages_by_employee` and `ssn_last4_by_employee`.
		quarter: Q1, Q2, Q3 or Q4.
		year: The calendar year.

	Returns:
		`employees` — the rows as filed — plus the totals the OQ is checked
		against, and `warnings`.
	"""
	warnings: list[str] = []
	slips = list(or_slips or [])
	ssn_map = company_info.get("ssn_last4_by_employee") or {}
	ytd = dict(company_info.get("ytd_wages_by_employee") or {})
	ui_base = _float(company_info.get("or_ui_wage_base"))

	rows: dict[str, dict] = {}
	for slip in sorted(slips, key=lambda s: str(s.get("period_end") or "")):
		employee = str(slip.get("employee") or "")
		if not employee:
			continue
		wages = _state_wages_of(slip, "OR")
		row = rows.setdefault(employee, {
			"employee": employee,
			"employee_name": slip.get("employee_name") or "",
			"ssn_last4": str(ssn_map.get(employee) or ""),
			"hours_worked": 0.0,
			"total_wages": 0.0,
			"ui_subject_wages": 0.0,
			"excess_wages": 0.0,
		})
		row["hours_worked"] += _float(slip.get("total_hours"))
		row["total_wages"] += wages

		if ui_base > 0:
			already = float(ytd.get(employee, 0.0))
			countable = min(wages, max(ui_base - already, 0.0))
			ytd[employee] = already + wages
		else:
			countable = wages
		row["ui_subject_wages"] += countable
		row["excess_wages"] += wages - countable

	employees = []
	for row in sorted(rows.values(), key=lambda r: (str(r["employee_name"]), str(r["employee"]))):
		employees.append({
			**row,
			# Oregon asks for WHOLE hours and rounds down. int() truncates, which
			# is the same thing for the non-negative values an hours field holds.
			"hours_worked": int(_float(row["hours_worked"])),
			"total_wages": _money(row["total_wages"]),
			"ui_subject_wages": _money(row["ui_subject_wages"]),
			"excess_wages": _money(row["excess_wages"]),
		})

	missing_ssn = [r["employee"] for r in employees if not r["ssn_last4"]]
	if missing_ssn:
		warnings.append(
			f"{len(missing_ssn)} employee(s) have no SSN recorded, so their Form 132 row "
			"has no identifier: Oregon matches a wage record to a person by SSN and cannot "
			"credit wages without one."
		)
	if ui_base <= 0:
		warnings.append(
			"no Oregon UI taxable wage base supplied, so every dollar is reported as UI "
			"subject and excess wages are zero. Oregon sets the base annually."
		)
	elif not company_info.get("ytd_wages_by_employee"):
		warnings.append(
			f"no year-to-date wages supplied, so the ${ui_base:,.2f} UI wage base was "
			"consumed from this quarter's wages alone. Correct for Q1; for any later "
			"quarter it OVERSTATES subject wages for anybody who reached the base earlier "
			"in the year."
		)
	if not employees:
		warnings.append(f"no Oregon payroll found for {quarter} {year}; Form 132 has no rows.")

	return {
		"form_type": "Form 132",
		"tax_year": int(year),
		"quarter": quarter,
		"oregon_bin": str((company_info.get("state_ids") or {}).get("OR") or ""),
		"employees": employees,
		"employee_count": len(employees),
		"total_hours": sum(r["hours_worked"] for r in employees),
		"total_wages": _money(sum(r["total_wages"] for r in employees)),
		"total_ui_subject_wages": _money(sum(r["ui_subject_wages"] for r in employees)),
		"total_excess_wages": _money(sum(r["excess_wages"] for r in employees)),
		"warnings": warnings,
	}


# ── Form 941 Part 2 — the monthly liability that has to reconcile ─────────


def monthly_liability(slips: list[dict], quarter: str, year: int, total_tax: float | None = None) -> dict:
	"""Form 941 Part 2's three monthly figures, and whether they add up.

	PART 2 MUST TOTAL LINE 12 EXACTLY or the return is rejected. Bucketing slips
	by month gives the AS-WITHHELD figures, and those do not reach line 12 on
	their own: line 7's fractions-of-cents adjustment, the sick-pay and
	group-term-life adjustments and the small-business credit all belong to the
	quarter rather than to any month in it. So both are reported — what the
	months actually hold, and the same figures with the residual applied to the
	last month, which is where the IRS instructions put it — plus a boolean
	saying whether the reconciled version agrees.

	FOR A SEMIWEEKLY DEPOSITOR PART 2 IS NOT THREE BOXES. It is Schedule B, filed
	daily. These monthly figures remain the control total that Schedule B has to
	match, which is what makes them worth computing either way.
	"""
	months = QUARTER_MONTHS.get(quarter)
	if not months:
		raise ValueError(f"quarter must be one of {', '.join(QUARTERS)}, got {quarter!r}.")

	as_withheld = {calendar.month_name[month]: 0.0 for month in months}
	for slip in slips or []:
		month = _month_of(slip.get("period_end"))
		if month not in months:
			continue
		liability = (
			_float(slip.get("federal_withholding"))
			+ _float(slip.get("social_security")) * 2
			+ (_float(slip.get("medicare")) - _float(slip.get("additional_medicare"))) * 2
			+ _float(slip.get("additional_medicare"))
		)
		as_withheld[calendar.month_name[month]] += liability

	as_withheld = {name: _money(value) for name, value in as_withheld.items()}
	withheld_total = _money(sum(as_withheld.values()))

	reconciled = dict(as_withheld)
	residual = 0.0
	residual_month = ""
	if total_tax is not None:
		residual = _money(_float(total_tax) - withheld_total)
		# The LAST MONTH THAT ACTUALLY HELD LIABILITY, not simply the last month
		# of the quarter. A quarter whose payroll stopped in January would
		# otherwise report a March liability in a month nobody was paid, which is
		# a figure an agency can ask about and the employer cannot explain.
		with_liability = [
			calendar.month_name[month]
			for month in months
			if as_withheld[calendar.month_name[month]] != 0
		]
		residual_month = with_liability[-1] if with_liability else calendar.month_name[months[-1]]
		reconciled[residual_month] = _money(reconciled[residual_month] + residual)

	reconciled_total = _money(sum(reconciled.values()))
	return {
		"quarter": quarter,
		"as_withheld": as_withheld,
		"as_withheld_total": withheld_total,
		"reconciled": reconciled,
		"reconciled_total": reconciled_total,
		"line12_total_tax": _money(total_tax) if total_tax is not None else None,
		"residual_applied_to_last_month": residual,
		"residual_month": residual_month,
		"reconciles": total_tax is None or abs(reconciled_total - _money(total_tax)) < 0.01,
		"note": (
			"Part 2 has to equal line 12 to the cent. `as_withheld` is what the months "
			"actually hold; `reconciled` moves the quarter-level residual — fractions of "
			"cents and any adjustment or credit — into the last month, which is where the "
			"941 instructions put it. A semiweekly depositor files Schedule B daily "
			"instead, and these figures are its control total."
		),
	}


def _state_wages_of(slip: dict, state: str) -> float:
	"""The wages one slip attributes to a state, using its split where it has one."""
	split = slip.get("state_wages") or {}
	if isinstance(split, dict) and state in split:
		return _float(split.get(state))
	if slip.get("work_state") == state:
		return _float(slip.get("gross_pay"))
	# A cross-state period that ran this state's engine but carries no split: the
	# detail proves there were wages here, and the gross is the only figure there
	# is. Reported whole, which the OQ's own warnings already say is a ceiling.
	if (slip.get("state_taxes_detail") or {}).get(state):
		return _float(slip.get("gross_pay"))
	return 0.0


# ── Small shared helpers ──────────────────────────────────────────────────


def _money(value) -> float:
	"""Round to cents. The only rounding anywhere in this module."""
	return round(_float(value), 2)


def _float(value, default: float = 0.0) -> float:
	"""A number from whatever a document field turned out to hold."""
	if value in (None, ""):
		return default
	try:
		return float(value)
	except (TypeError, ValueError):
		return default


def _sum(rows: list[dict], key: str) -> float:
	"""Total one key across rows, tolerating missing and unparseable values."""
	return sum(_float(row.get(key)) for row in rows)


def _as_date(value) -> date | None:
	"""A date from an ISO string, a date, or a datetime. None if unreadable."""
	if isinstance(value, date):
		return value
	if not value:
		return None
	try:
		return date.fromisoformat(str(value)[:10])
	except ValueError:
		return None


def _month_of(value) -> int | None:
	"""The calendar month of a date-ish value, or None."""
	moment = _as_date(value)
	return moment.month if moment else None
