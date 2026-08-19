# SPDX-License-Identifier: MIT
"""Who is under eighteen, what that changes, and the citation for each change.

Same contract as `breaks.py`, `bucket_bridge.py` and `payroll_gl.py`: everything
arrives as a value or a dict, nothing writes, nothing imports `frappe`. The
callers — `tools/shifts.add_worker_to_shift`, `tools/dispatch.assign_farm_task`,
`tools/employee.employee_detail`, `tools/hr.list_employees` and the
`minor_hours_approaching` alert rule — do the reading and the refusing; this
module owns the arithmetic and the regulation.

────────────────────────────────────────────────────────────────────────────
`is_minor` IS DERIVED AND IS NEVER STORED
────────────────────────────────────────────────────────────────────────────

There is no `is_minor` column on Employee and there will not be one. A stored
flag is correct on the day somebody ticks it and wrong every day afterwards: a
fifteen-year-old hired in April is sixteen in July and seventeen the following
season, and the one thing a child-labour column must not be is stale in the
direction that permits more work. `date_of_birth` is a fact that does not move;
the answer is computed from it against the day being asked about, which is why
every function here takes an `on_date`.

The other half of the same argument: a farm that does not record a date of birth
gets `None` and NOT `False`. "We do not know" and "they are an adult" are
different answers, and a module that collapsed them would clear a minor onto a
ten-hour shift because a column was empty. `describe` says which of the two it
is, and the callers say so too.

────────────────────────────────────────────────────────────────────────────
TWO BANDS, BECAUSE THE LAW HAS TWO
────────────────────────────────────────────────────────────────────────────

Fourteen and fifteen is a different set of rules from sixteen and seventeen, and
treating "minor" as one category would either forbid lawful work for the older
band or permit unlawful work for the younger. `band()` answers `"under-16"`,
`"16-17"` or `""` (an adult, or somebody too young to be employed at all — see
`MINIMUM_AGE`).

  under-16   8 h a day, 40 h a week, and 07:00 to 19:00 only.
             ORS 653.315 / OAR 839-021-0220 for the hours; 29 CFR 570.35 for
             the clock, which is the FLSA's own limit on a fourteen- or
             fifteen-year-old outside school hours.

  16-17      10 h a day, 60 h a week, no time-of-day limit in agriculture.
             OAR 839-021-0104. There is no FLSA hours limit on this band in
             agriculture at all; the Oregon ceiling is the binding one, which
             is why it is the number here.

The daily figures are NON-SCHOOL-DAY figures, deliberately. A school-day limit
is three hours (ORS 653.315), and applying it would need this app to know each
worker's school calendar, which it does not and should not — so the ceiling
encoded is the one that holds during harvest, and the refusal says which figure
it used so a foreman rostering during term time can see what was assumed.

────────────────────────────────────────────────────────────────────────────
WHAT A MINOR MAY NOT BE SENT TO
────────────────────────────────────────────────────────────────────────────

`PROHIBITED_TASK_TYPES` is short, and short on purpose. Every entry names a
regulation that forbids the work by AGE rather than by training, because a list
that also carried "probably unwise" would be a list a foreman learns to override.

  Spray, either band   40 CFR §170.309(c). The 2015 Worker Protection Standard
                       sets a minimum age of EIGHTEEN for pesticide handlers
                       and for early-entry workers. It is not a training gap
                       that can be closed with a course.

  Repair, under-16     29 CFR §570.71(a). The Hazardous Occupations Orders in
                       Agriculture put power-driven machinery — tractors over
                       20 PTO horsepower, balers, augers, power take-offs —
                       out of reach of anybody under sixteen. Repair is where
                       this app's task vocabulary puts that work.

The 16-17 band is NOT barred from Repair, and that is the regulation rather than
an oversight: the HO/A orders bind under-sixteens, and a seventeen-year-old
lawfully runs a tractor on a farm in Oregon.
"""

from __future__ import annotations

import datetime as _dt

#: Under this, nothing here applies because the employment itself is the
#: problem. Twelve is the floor for hand-harvest work on a non-exempt farm
#: outside school hours with written parental consent (29 CFR §570.2(b)); below
#: it, only a parent's own farm. `band` answers `"under-16"` for anybody under
#: sixteen including these, and `describe` carries `below_minimum_age` so a
#: caller can say the stronger thing.
MINIMUM_AGE = 12

#: The age at which every rule in this module stops applying.
MAJORITY_AGE = 18

BAND_UNDER_16 = "under-16"
BAND_16_17 = "16-17"

#: What each band may work, and when. `earliest`/`latest` are wall-clock strings
#: and empty means "no limit in agriculture", which is the true answer for the
#: older band rather than a gap.
LIMITS = {
	BAND_UNDER_16: {
		"daily_hours": 8.0,
		"weekly_hours": 40.0,
		"earliest": "07:00",
		"latest": "19:00",
		"citation": "ORS 653.315 / OAR 839-021-0220; 29 CFR 570.35",
	},
	BAND_16_17: {
		"daily_hours": 10.0,
		"weekly_hours": 60.0,
		"earliest": "",
		"latest": "",
		"citation": "OAR 839-021-0104",
	},
}

#: How close to a ceiling is close enough to say so. An hour of the day and four
#: of the week — one more pick round and one more afternoon respectively, which
#: is the granularity a foreman can actually act on. Warnings, never refusals.
DAILY_WARNING_HOURS = 1.0
WEEKLY_WARNING_HOURS = 4.0

#: Farm Task `task_type` → the bands it is closed to, with the citation.
PROHIBITED_TASK_TYPES = {
	"Spray": (
		(BAND_UNDER_16, BAND_16_17),
		"40 CFR §170.309(c) — the Worker Protection Standard sets a minimum age of 18 for "
		"pesticide handlers and for early-entry workers. It is an age bar, not a training gap: "
		"there is no course that closes it.",
	),
	"Repair": (
		(BAND_UNDER_16,),
		"29 CFR §570.71(a) — the Hazardous Occupations Orders in Agriculture put power-driven "
		"machinery out of reach of anybody under sixteen. A 16- or 17-year-old may lawfully do "
		"this work in Oregon.",
	),
}


#: The rows OAR 839-021-0072 asks for, as a `Labor Break Schedule Row` table
#: would hold them. PUBLISHED, NOT SEEDED — and the difference is the point.
#:
#: `Labor Break Policy` carries `human_approved_by` because a break schedule is a
#: statement this operation makes about what it owes its crew, and an app that
#: wrote rows into an approved policy on `bench migrate` would move that
#: statement without anybody signing it. So nothing here is ever written by this
#: app; `get_break_policy` returns these BESIDE a policy that has no minor rows,
#: marked as unapproved, so the gap is visible on the handset and the operator
#: has the rows to paste rather than a citation to go and read.
#:
#: A REST EVERY TWO HOURS AND A MEAL EVERY FOUR. The bands are cumulative in the
#: same way the adult tables are — `breaks._best_band` picks the highest band a
#: span reaches — so a nine-hour day owes four rests and two meals.
MINOR_REST_SCHEDULE = (
	{"hours_from": 2.0, "hours_to": 4.0, "periods_owed": 1, "minutes_each": 15, "paid": 1},
	{"hours_from": 4.0, "hours_to": 6.0, "periods_owed": 2, "minutes_each": 15, "paid": 1},
	{"hours_from": 6.0, "hours_to": 8.0, "periods_owed": 3, "minutes_each": 15, "paid": 1},
	{"hours_from": 8.0, "hours_to": 10.0, "periods_owed": 4, "minutes_each": 15, "paid": 1},
)

MINOR_MEAL_SCHEDULE = (
	{"hours_from": 4.0, "hours_to": 8.0, "periods_owed": 1, "minutes_each": 30, "paid": 0},
	{"hours_from": 8.0, "hours_to": 12.0, "periods_owed": 2, "minutes_each": 30, "paid": 0},
)

#: The citation the two tables above come from, quoted wherever they are.
MINOR_SCHEDULE_CITATION = "OAR 839-021-0072"


# ── parsing ───────────────────────────────────────────────────────────────────


def _as_date(value):
	"""A date from whatever the column or the argument held, or None.

	Accepts a `date`, a `datetime`, `YYYY-MM-DD`, and a MariaDB DATETIME string —
	the last because a caller may hand this a shift's `start_datetime` as the day
	being asked about, and re-parsing it in three call sites would be three
	chances to disagree.
	"""
	if isinstance(value, _dt.datetime):
		return value.date()
	if isinstance(value, _dt.date):
		return value
	text = str(value or "").strip()
	if not text:
		return None
	head = text.replace("T", " ").split(" ")[0]
	try:
		return _dt.datetime.strptime(head, "%Y-%m-%d").date()
	except ValueError:
		return None


def _as_datetime(value):
	if isinstance(value, _dt.datetime):
		return value
	text = str(value or "").strip()
	if not text:
		return None
	# `T` normalised to a space FIRST, so one format list covers both spellings.
	# The iOS app posts ISO-8601 and MariaDB stores a space; a parser that knew
	# only one of them would silently fall through to the date-only branch below
	# and read every afternoon shift as starting at midnight.
	text = text.replace("T", " ")
	for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
		try:
			return _dt.datetime.strptime(text, fmt)
		except ValueError:
			continue
	day = _as_date(value)
	return _dt.datetime(day.year, day.month, day.day) if day else None


def _minutes(clock: str):
	""""07:00" → 420. Empty or unparseable → None, which reads as "no limit"."""
	parts = str(clock or "").strip().split(":")
	if len(parts) < 2:
		return None
	try:
		return int(parts[0]) * 60 + int(parts[1])
	except ValueError:
		return None


# ── age ───────────────────────────────────────────────────────────────────────


def age_on(date_of_birth, on_date) -> int | None:
	"""Completed years on `on_date`, or None where either date is unreadable.

	Birthday arithmetic done by comparing (month, day) tuples rather than by
	dividing days by 365.25: the second is wrong for one day in four on somebody
	born in a leap year, and the day it is wrong is their birthday.
	"""
	born = _as_date(date_of_birth)
	when = _as_date(on_date)
	if born is None or when is None:
		return None
	years = when.year - born.year
	if (when.month, when.day) < (born.month, born.day):
		years -= 1
	return years


def band(date_of_birth, on_date) -> str:
	"""`"under-16"`, `"16-17"`, or `""` for an adult or an unknown birth date.

	AN UNKNOWN BIRTH DATE ANSWERS `""` HERE and `None` from `is_minor`. This
	function's answer feeds `LIMITS`, and a limit lookup has to be total; the
	"we do not know" distinction is `is_minor`'s and `describe`'s to carry, and
	every caller that refuses reads one of those rather than this.
	"""
	age = age_on(date_of_birth, on_date)
	if age is None or age >= MAJORITY_AGE:
		return ""
	return BAND_UNDER_16 if age < 16 else BAND_16_17


def is_minor(date_of_birth, on_date) -> bool | None:
	"""True, False, or None where the birth date is missing or unreadable.

	THREE-VALUED ON PURPOSE. See the module docstring: a farm with no date of
	birth on file has not told this app that somebody is an adult.
	"""
	age = age_on(date_of_birth, on_date)
	if age is None:
		return None
	return age < MAJORITY_AGE


def limits_for(date_of_birth, on_date) -> dict:
	"""The band's limits, or an empty dict for an adult or an unknown birth date."""
	return dict(LIMITS.get(band(date_of_birth, on_date)) or {})


def describe(date_of_birth, on_date) -> dict:
	"""Everything a roster row, an employee read or a refusal needs, in one shape.

	`is_minor` is the key iOS branches on for the purple badge. It is `None` where
	nothing is recorded, and `date_of_birth_recorded` says which case that is —
	a client rendering `is_minor == true` as a badge renders nothing for either,
	which is right, and a foreman reading the roster can still see the gap.
	"""
	age = age_on(date_of_birth, on_date)
	which = band(date_of_birth, on_date)
	out = {
		"date_of_birth_recorded": bool(_as_date(date_of_birth)),
		"age": age,
		"is_minor": None if age is None else age < MAJORITY_AGE,
		"minor_band": which or None,
		"minor_limits": dict(LIMITS.get(which) or {}) or None,
	}
	if age is not None and age < MINIMUM_AGE:
		out["below_minimum_age"] = True
	return out


# ── the clock ─────────────────────────────────────────────────────────────────


def time_of_day_violation(work_band: str, start, end="") -> str | None:
	"""Why this span is outside the hours the band may work, or None.

	Checked against the START and the END separately rather than against the
	span, because those are the two the regulation names and because a span
	crossing midnight is a different violation with a different sentence — one
	this returns on the end check, since 00:30 is before 07:00.
	"""
	limits = LIMITS.get(work_band) or {}
	earliest = _minutes(limits.get("earliest"))
	latest = _minutes(limits.get("latest"))
	if earliest is None and latest is None:
		return None

	for value, label in ((start, "starts"), (end, "ends")):
		moment = _as_datetime(value)
		if moment is None:
			continue
		minute = moment.hour * 60 + moment.minute
		if earliest is not None and minute < earliest:
			return (
				f"the shift {label} at {moment.strftime('%H:%M')}, before {limits['earliest']}. "
				f"A worker in the {work_band} band may not work earlier ({limits['citation']})."
			)
		if latest is not None and minute > latest:
			return (
				f"the shift {label} at {moment.strftime('%H:%M')}, after {limits['latest']}. "
				f"A worker in the {work_band} band may not work later ({limits['citation']})."
			)
	return None


def hours_violation(work_band: str, hours_today: float, hours_this_week: float) -> str | None:
	"""Why these totals are over the band's ceiling, or None.

	The DAILY figure is reported first where both are over, because it is the one
	a foreman can still do something about before the crew starts.
	"""
	limits = LIMITS.get(work_band) or {}
	if not limits:
		return None
	daily = float(limits["daily_hours"])
	weekly = float(limits["weekly_hours"])
	if hours_today > daily:
		return (
			f"that would be {hours_today:.1f} hours today against a {daily:.0f}-hour non-school-day "
			f"ceiling for the {work_band} band ({limits['citation']})."
		)
	if hours_this_week > weekly:
		return (
			f"that would be {hours_this_week:.1f} hours this week against a {weekly:.0f}-hour "
			f"ceiling for the {work_band} band ({limits['citation']})."
		)
	return None


def hours_warning(work_band: str, hours_today: float, hours_this_week: float) -> str | None:
	"""How close to the ceiling this is, where it is close but not over.

	Returns None once it is actually over — that is `hours_violation`'s sentence,
	and two messages about one fact is how a warning stops being read.
	"""
	limits = LIMITS.get(work_band) or {}
	if not limits or hours_violation(work_band, hours_today, hours_this_week):
		return None
	daily = float(limits["daily_hours"])
	weekly = float(limits["weekly_hours"])
	if daily - hours_today <= DAILY_WARNING_HOURS:
		return (
			f"{hours_today:.1f} of {daily:.0f} hours for the day. "
			f"{daily - hours_today:.1f} hour(s) left before the {work_band} ceiling."
		)
	if weekly - hours_this_week <= WEEKLY_WARNING_HOURS:
		return (
			f"{hours_this_week:.1f} of {weekly:.0f} hours for the week. "
			f"{weekly - hours_this_week:.1f} hour(s) left before the {work_band} ceiling."
		)
	return None


# ── the work ──────────────────────────────────────────────────────────────────


def prohibited_reason(work_band: str, task_type: str) -> str | None:
	"""Why this band may not be sent to this kind of task, or None.

	An unknown task type answers None. This app's Farm Task Select has eleven
	values and two of them are named here; inventing a refusal for the other nine
	— or for a value an operator added — would be this module deciding policy it
	has no citation for.
	"""
	if not work_band:
		return None
	entry = PROHIBITED_TASK_TYPES.get(str(task_type or "").strip())
	if not entry:
		return None
	bands, citation = entry
	return citation if work_band in bands else None
