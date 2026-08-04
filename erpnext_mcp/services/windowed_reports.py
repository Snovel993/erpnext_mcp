# SPDX-License-Identifier: MIT
"""The window standard: every financial figure reported over its natural cycle.

v0.19.6. One utility that turns any point-in-time computation into a windowed
one, and the shape every financial report in this app takes from here on.

    Trailing Twelve Months is the DEFAULT, not an option somebody asks for.

────────────────────────────────────────────────────────────────────────────
WHY TTM IS THE DEFAULT AND A SINGLE PERIOD IS NOT
────────────────────────────────────────────────────────────────────────────

Agricultural revenue is aggressively seasonal, and a headline figure that
ignores it is not merely imprecise — it is confidently wrong in a direction that
changes with the month somebody asked. Q3 is harvest and Q1 is pruning, so a
quarter-on-quarter comparison of the two says a farm collapsed in January and
recovered in September, every single year, on every farm. Nobody believes that
about their own operation and everybody quotes numbers that assert it.

TTM smooths the annual cycle out by construction: the window is always one
complete turn of the business's own calendar, so pruning, thinning, harvest and
the winter are all in it exactly once no matter when it is read. That is why it
is the standard lens for public-company reporting and for lender covenants, and
it is a better fit here than it is there.

THREE QUESTIONS, THREE BLOCKS, AND ALL THREE ARE IN EVERY PAYLOAD:

  * `point_in_time` answers "where were we in the period just finished"
  * `window` (`ttm` by default) answers "how is the business running over its
    natural cycle"
  * `historical_averages` answers "is that good or bad for THIS operation" —
    which is the question the first two cannot touch, and the one somebody
    setting a budget is actually asking. A TTM ROIC of 12 % means one thing
    against a five-year average of 8 % and the opposite against 14 %.

Snapshot alone flatters harvest and demonizes pruning. TTM alone hides an
emerging trend inside eleven months of history. Historical average without the
current figure is a description of a farm that no longer exists. The standard
shows all three because each is the correction for the other two.

────────────────────────────────────────────────────────────────────────────
THE BOUNDARY RULE, WHICH IS ONE RULE AND NOT FIVE
────────────────────────────────────────────────────────────────────────────

`as_of` is the reporting MOMENT, not the end of the window. The window ends at
the last step boundary that has actually completed on or before it:

    period_end   = last completed `computation_step` boundary <= as_of
    period_start = add_months(period_end, -window_months) + 1 day

A Monthly report read on 2026-08-03 ends 2026-07-31 and starts 2025-08-01. A
Quarterly one read the same morning ends 2026-06-30 and starts 2025-07-01. A
Daily one ends 2026-08-02 and starts 2025-08-03.

THE PART-FINISHED PERIOD IS EXCLUDED AND THAT IS THE WHOLE POINT. Three days of
August against twelve months of everything else is a figure that falls every
first of the month and recovers by the thirty-first, and an operator who reads
it on the fourth will believe the fall. A window whose last period is complete
can be compared with the one before it; a window with a stub on the end cannot
be compared with anything, including itself an hour later.

`period_start` is the day AFTER the same calendar date twelve months back,
because `period_end` is inclusive everywhere in this app — 2025-08-01 to
2026-07-31 is twelve months and 2025-07-31 to 2026-07-31 is twelve months and a
day. See `sustainable_cf_per_acre.inclusive_days` for the same argument about
the same off-by-one, which is the shape of error nobody notices and everybody
inherits.

────────────────────────────────────────────────────────────────────────────
QUARTERS AND YEARS FOLLOW THE COMPANY'S FISCAL YEAR. MONTHS DO NOT.
────────────────────────────────────────────────────────────────────────────

A month is a month on every calendar anybody uses, so Daily, Weekly and Monthly
boundaries are the plain ones. A QUARTER IS NOT: an operation whose fiscal year
opens on 1 July has a first quarter running July to September, and stepping its
history by calendar quarters would put every one of its year-end closes in the
middle of a bucket. So Quarterly and Yearly boundaries are anchored on the
company's own `Fiscal Year.year_start_date`, and the anchor month is reported in
the payload as `fiscal_year_start_month` so nobody has to infer it.

THIS DIVERGES FROM `report/sustainable_cf_per_acre_by_quarter`, DELIBERATELY,
and the two are not in conflict. That report draws CALENDAR quarters and argues
for them: it is read beside a lender's own quarters, and a "Q1" starting in July
is one nobody outside the operation can line up with anything. It is a
presentation of four discrete quarters. This is the history of a rolling window,
and a rolling window whose steps straddle the operation's own year-end produces
a series where the year-end effects land in two buckets and belong to neither.
Different jobs, different anchors, and both say which they used.

────────────────────────────────────────────────────────────────────────────
THE WINDOW IS COMPUTED WHOLE. IT IS NOT TWELVE MONTHLY ANSWERS ADDED UP.
────────────────────────────────────────────────────────────────────────────

This is the correctness rule that decides the module's shape, and it has two
halves.

The first is arithmetic: SUSTAINABLE CF/ACRE IS A RATIO, and the average of
twelve monthly ratios is not the ratio of the twelve-month totals. A month with
two productive acres and a month with two hundred contribute equally to the
first and not at all equally to the second, and only the second is the number
anybody means.

The second is subtler and is why bucket-summing is not merely a shortcut for
sums either. `kpi.approved_in_period` counts an adjustment whose period falls
INSIDE the window — deliberately, so a quarterly insurance recovery is not
counted in a quarter and again in the year containing it. A Q1 adjustment falls
inside no single MONTHLY bucket at all, so a TTM figure assembled from twelve
monthly buckets would silently drop it, and the loss would be invisible: the
number would simply be lower, with nothing anywhere saying why.

So a computer declares whether it is `bucket_additive`. Revenue is — it is a sum
over GL rows with no containment rule anywhere in it, and summing buckets both
agrees with the direct computation and hands back the per-bucket trail for free.
Sustainable CF/Acre and normalized OCF are not, and are computed once over the
whole window. `aggregate_components` implements the merge for the ones that can
use it and is not reached for the ones that cannot.

────────────────────────────────────────────────────────────────────────────
PARTIAL HISTORY IS A WARNING, NEVER A QUIETLY SMALLER NUMBER
────────────────────────────────────────────────────────────────────────────

A site with four months of ledger has no trailing twelve months, and the honest
answer is four months of data labelled as four months of data. The wrong answers
are both available and both get quoted: annualizing it (which invents eight
months of a season that has not happened) or returning it unlabelled as a TTM
figure (which is the same invention with the evidence removed).

So the window is computed over whatever is actually there, the payload says
`only 4 month(s) of ledger history available; the TTM window is partial`, and
every statistic derived from a short series says how many entries it had. The
same discipline `sustainable_cf_per_acre` applies to a zero denominator: None
rather than zero, because a division nobody performed is not an answer.

────────────────────────────────────────────────────────────────────────────
THE CACHE, AND WHY A LIVE QUERY IS BOUNDED
────────────────────────────────────────────────────────────────────────────

A five-year Monthly history is sixty TTM windows, and each one is a full
computation over twelve months of GL. Recomputing that per query would make the
tool unusable at exactly the moment somebody is using it, so `Financial KPI
History` holds one row per (kpi, company, step, window type, window months,
as_of) with the components dict the live computation would have produced, and
the overnight sweep fills it in while nobody is waiting.

A LIVE QUERY COMPUTES AT MOST `LIVE_COMPUTATION_CAP` MISSING SNAPSHOTS and then
stops, returning a shorter series with a warning naming the tool that fills the
rest. A read tool that runs for four minutes on a cold cache is a read tool
somebody kills and then distrusts; a short series that says it is short is one
they can act on.

INVALIDATION IS ACTIVE ON APPROVAL AND PASSIVE ON READ. Approving a
normalization adjustment for a period that history already covers DELETES the
cached rows whose window overlaps it, and the next read or the next sweep
recomputes them. Deleted rather than flagged, because a cached row carries the
components dict as well as the figure, and a stale components dict is worse than
a missing one — it is a list of ingredients that no longer produce the number
above them. Nothing is lost: it is a cache, and every row in it is derivable.
"""

from __future__ import annotations

import datetime
import json
import statistics

import frappe

from .. import compat

DOCTYPE = "Financial KPI History"

# ── the vocabulary ──────────────────────────────────────────────────────────
WINDOW_SNAPSHOT = "Snapshot"
WINDOW_TTM = "TTM"
WINDOW_MTD = "MTD"
WINDOW_QTD = "QTD"
WINDOW_YTD = "YTD"
WINDOW_CUSTOM = "Custom"
WINDOW_TYPES = (WINDOW_SNAPSHOT, WINDOW_TTM, WINDOW_MTD, WINDOW_QTD, WINDOW_YTD, WINDOW_CUSTOM)

#: The accumulating windows. They run from the start of the current period to
#: `as_of` itself rather than to the last completed step, because that is what
#: "to date" means and what makes them comparable with the same span of the
#: prior year — which is the one comparison they exist for.
TO_DATE_WINDOWS = (WINDOW_MTD, WINDOW_QTD, WINDOW_YTD)

STEP_DAILY = "Daily"
STEP_WEEKLY = "Weekly"
STEP_MONTHLY = "Monthly"
STEP_QUARTERLY = "Quarterly"
STEP_YEARLY = "Yearly"
STEPS = (STEP_DAILY, STEP_WEEKLY, STEP_MONTHLY, STEP_QUARTERLY, STEP_YEARLY)

#: How many steps make a year, which is how `current_vs_prior_year_pct_delta`
#: finds its comparator. Counted in STEPS rather than in months, deliberately:
#: the entry twelve months back from `as_of` may land in the middle of a bucket,
#: and the entry the reader means is the one exactly one lap of the series ago.
#: 365 and 52 are the ordinary year; a leap year or a 53-week year moves the
#: comparator by one bucket, which on a rolling twelve-month figure is noise
#: several orders of magnitude below the thing being compared.
STEPS_PER_YEAR = {
	STEP_DAILY: 365,
	STEP_WEEKLY: 52,
	STEP_MONTHLY: 12,
	STEP_QUARTERLY: 4,
	STEP_YEARLY: 1,
}

#: Quarters, in months, for a fiscal year opening in month 1. Rotated by the
#: company's own fiscal start month — see the module docstring.
MONTHS_PER_QUARTER = 3

#: The most missing snapshots one live query will compute before it gives up and
#: says so. Sixty TTM windows is sixty full computations over twelve months of
#: GL each, and a read tool that takes minutes is one somebody kills. The
#: overnight sweep has no such cap because nobody is waiting on it.
LIVE_COMPUTATION_CAP = 24

#: Ceiling on the history a caller may ask for, in years. Past this the series is
#: longer than most operations' ledgers and every entry past the data is a row
#: saying "no data", which is a slower way of saying nothing.
MAX_LOOKBACK_YEARS = 10

#: Default TTM. Twelve months, because that is what the T and the M mean.
DEFAULT_WINDOW_MONTHS = 12
DEFAULT_LOOKBACK_YEARS = 5


# ── dates, in plain Python ──────────────────────────────────────────────────
#
# `frappe.utils` has no `add_months` this app can rely on across the versions in
# its compatibility table, and month arithmetic is the one piece of this module
# that has to be exactly right on every boundary. So it is written out: parsing
# goes through `frappe.utils.getdate` (which accepts everything a Frappe field
# hands back), and the arithmetic is `datetime`.
def as_date(value) -> datetime.date:
	"""Anything Frappe might hand back, as a `datetime.date`."""
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return frappe.utils.getdate(value)


def iso(value) -> str:
	return as_date(value).isoformat()


def days_in_month(year: int, month: int) -> int:
	if month == 12:
		return 31
	return (datetime.date(year, month + 1, 1) - datetime.timedelta(days=1)).day


def add_months(value, months: int) -> datetime.date:
	"""`value` moved by `months`, with the day CLAMPED to the target month.

	31 January minus one month is 31 December and plus one month is 28 February,
	because there is no 31 February and rolling into March would make a "one
	month" step occasionally two. Clamping is what every calendar does and what
	every reader expects; rolling is what naive day arithmetic does and is how a
	monthly series grows a thirteenth bucket in a year with a long February.
	"""
	base = as_date(value)
	total = base.year * 12 + (base.month - 1) + months
	year, month = divmod(total, 12)
	month += 1
	return datetime.date(year, month, min(base.day, days_in_month(year, month)))


def add_days(value, days: int) -> datetime.date:
	return as_date(value) + datetime.timedelta(days=days)


def first_of_month(value) -> datetime.date:
	base = as_date(value)
	return datetime.date(base.year, base.month, 1)


def last_of_month(value) -> datetime.date:
	base = as_date(value)
	return datetime.date(base.year, base.month, days_in_month(base.year, base.month))


def fiscal_year_start_month(company: str) -> int:
	"""The month this company's fiscal year opens in, or 1 where nothing says.

	Read off the LATEST Fiscal Year row rather than the one containing today,
	because a site part-way through setting its years up may have exactly one and
	it may be next year's. Falls back to January on any failure at all: an anchor
	nobody can establish is not a reason to refuse a report, and January is both
	the overwhelming majority case and the one a reader assumes when the payload
	does not say otherwise. It always says otherwise when it is otherwise.
	"""
	try:
		if not compat.doctype_exists("Fiscal Year"):
			return 1
		rows = frappe.db.get_all(
			"Fiscal Year",
			fields=["name", "year_start_date"],
			order_by="year_start_date desc",
			limit=1,
		)
		if rows and rows[0].get("year_start_date"):
			return int(as_date(rows[0]["year_start_date"]).month)
	except Exception:
		pass
	return 1


def quarter_end_months(anchor_month: int) -> list:
	"""The four months a fiscal quarter ends in, for a year opening in `anchor_month`.

	A January year ends its quarters in March, June, September and December; a
	July year ends them in September, December, March and June. Returned sorted so
	the boundary search below can walk it without caring which is "Q1".
	"""
	anchor = ((int(anchor_month) - 1) % 12) + 1
	return sorted(((anchor - 1 + offset) % 12) + 1 for offset in (2, 5, 8, 11))


def last_completed_boundary(as_of, computation_step: str, anchor_month: int = 1) -> datetime.date:
	"""The last `computation_step` boundary that has actually finished on or before `as_of`.

	The one rule the whole module turns on. See the module docstring for why the
	part-finished period is excluded rather than included as a stub.

	DAILY IS `as_of - 1`, not `as_of`. Today has not happened yet — the ledger
	gets postings all afternoon, and a daily figure that changes four times
	between morning and evening is one nobody can quote in an email.
	"""
	moment = as_date(as_of)
	step = computation_step

	if step == STEP_DAILY:
		return moment - datetime.timedelta(days=1)

	if step == STEP_WEEKLY:
		# ISO weeks, ending Sunday. `weekday()` is 0 for Monday, so a Sunday is 6
		# and is itself a completed boundary; every other day walks back to the
		# Sunday before it.
		offset = (moment.weekday() + 1) % 7
		return moment - datetime.timedelta(days=offset)

	if step == STEP_MONTHLY:
		end = last_of_month(moment)
		return end if end <= moment else last_of_month(add_months(first_of_month(moment), -1))

	if step == STEP_QUARTERLY:
		ends = quarter_end_months(anchor_month)
		candidate = moment
		for _ in range(13):
			if candidate.month in ends:
				end = last_of_month(candidate)
				if end <= moment:
					return end
			candidate = add_months(first_of_month(candidate), -1)
		return last_of_month(add_months(first_of_month(moment), -1))  # pragma: no cover

	if step == STEP_YEARLY:
		anchor = ((int(anchor_month) - 1) % 12) + 1
		end_month = ((anchor - 2) % 12) + 1
		candidate = moment
		for _ in range(13):
			if candidate.month == end_month:
				end = last_of_month(candidate)
				if end <= moment:
					return end
			candidate = add_months(first_of_month(candidate), -1)
		return last_of_month(add_months(first_of_month(moment), -1))  # pragma: no cover

	raise ValueError(f"computation_step must be one of {', '.join(STEPS)}; got {computation_step!r}.")


def step_back(boundary, computation_step: str, count: int = 1, anchor_month: int = 1) -> datetime.date:
	"""`count` step boundaries before `boundary`, staying on boundaries."""
	if count <= 0:
		return as_date(boundary)
	if computation_step == STEP_DAILY:
		return as_date(boundary) - datetime.timedelta(days=count)
	if computation_step == STEP_WEEKLY:
		return as_date(boundary) - datetime.timedelta(days=7 * count)
	months = {STEP_MONTHLY: 1, STEP_QUARTERLY: 3, STEP_YEARLY: 12}[computation_step]
	return last_of_month(add_months(first_of_month(boundary), -months * count))


def step_period_start(boundary, computation_step: str) -> datetime.date:
	"""The first day of the step that ENDS on `boundary` — the point-in-time period."""
	end = as_date(boundary)
	if computation_step == STEP_DAILY:
		return end
	if computation_step == STEP_WEEKLY:
		return end - datetime.timedelta(days=6)
	months = {STEP_MONTHLY: 1, STEP_QUARTERLY: 3, STEP_YEARLY: 12}[computation_step]
	return first_of_month(add_months(first_of_month(end), -(months - 1)))


def window_start(period_end, window_months: int) -> datetime.date:
	"""`period_end` less `window_months`, plus a day. Inclusive at both ends."""
	return add_days(add_months(period_end, -int(window_months)), 1)


def to_date_start(as_of, window_type: str, anchor_month: int = 1) -> datetime.date:
	"""Where an MTD / QTD / YTD window opens, relative to the moment it is read."""
	moment = as_date(as_of)
	if window_type == WINDOW_MTD:
		return first_of_month(moment)
	if window_type == WINDOW_QTD:
		ends = quarter_end_months(anchor_month)
		candidate = moment
		for _ in range(4):
			if candidate.month in ends:
				break
			candidate = add_months(first_of_month(candidate), 1)
		return first_of_month(add_months(first_of_month(candidate), -2))
	anchor = ((int(anchor_month) - 1) % 12) + 1
	year = moment.year if moment.month >= anchor else moment.year - 1
	return datetime.date(year, anchor, 1)


#: How many months one lap of a to-date window is. MTD laps by month, QTD by
#: quarter, YTD by year — which is the whole reason these windows exist.
TO_DATE_PERIOD_MONTHS = {WINDOW_MTD: 1, WINDOW_QTD: 3, WINDOW_YTD: 12}


def to_date_prior(as_of, window_type: str, laps: int, anchor_month: int = 1) -> tuple:
	"""The SAME to-date span, `laps` periods earlier. Returns (start, end).

	THE COMPARATOR FOR A TO-DATE WINDOW IS THE SAME SPAN, NOT A ROLLING YEAR, and
	getting this wrong is the quietest way to produce a confident nonsense. "Are
	we ahead of last year?" means the first eight months of this year against the
	first eight months of last year — and a prior series built out of
	trailing-twelve-month windows would answer a different question with the same
	number of decimal places. Nobody would notice, because both series are
	plausible and neither is labelled.

	So the span is preserved by DAY OFFSET into the period: 34 days into this
	quarter is compared with 34 days into that one. The end is clamped to the
	prior period's own last day, which only bites on the month-length edges — 31
	March has no counterpart in February, and 28 February is the honest answer
	rather than 3 March.
	"""
	moment = as_date(as_of)
	months = TO_DATE_PERIOD_MONTHS[window_type]
	current_start = to_date_start(moment, window_type, anchor_month)
	offset = (moment - current_start).days

	prior_start = add_months(current_start, -months * int(laps))
	prior_period_end = add_days(add_months(prior_start, months), -1)
	prior_end = min(add_days(prior_start, offset), prior_period_end)
	return prior_start, prior_end


# ── the computer registry ───────────────────────────────────────────────────
#
# A computer is a callable taking (company, period_start, period_end) and
# returning a components dict with a `value` key. Registering one is the whole of
# adding a windowed report — the boundaries, the history, the cache, the
# statistics and the warnings all come from here.
COMPUTERS: dict = {}


def register(
	report_name: str,
	computer,
	*,
	kpi_key: str = "",
	label: str = "",
	value_key: str = "value",
	sum_keys=(),
	weighted_keys=(),
	list_keys=(),
	bucket_additive: bool = False,
	default_step: str = STEP_MONTHLY,
	allow_daily: bool = False,
	unit: str = "",
	available=None,
) -> dict:
	"""Put one computation under the window standard. This is the entire API.

	`sum_keys`, `weighted_keys` and `list_keys` are dotted paths into the
	components dict, and they are what `aggregate_components` needs to merge
	buckets: what adds up, what has to be re-weighted over the longer window, and
	what is a list of records to be concatenated and de-duplicated by docname.
	They are declared per computer rather than inferred, because a key called
	`total` may be either and guessing wrong is how a denominator gets summed.

	`allow_daily` is FALSE by default and that is the standing position of this
	standard. Daily is cheap for one report and ruinous across a framework —
	sixty snapshots becomes eighteen hundred — so an operator opts into it per
	KPI, having read what it costs.
	"""
	entry = {
		"report_name": report_name,
		"kpi_key": kpi_key or report_name,
		"label": label or report_name.replace("_", " "),
		"computer": computer,
		"value_key": value_key,
		"sum_keys": tuple(sum_keys),
		"weighted_keys": tuple(weighted_keys),
		"list_keys": tuple(list_keys),
		"bucket_additive": bool(bucket_additive),
		"default_step": default_step,
		"allow_daily": bool(allow_daily),
		"unit": unit,
		"available": available,
	}
	COMPUTERS[report_name] = entry
	return entry


def registered(report_name: str) -> dict:
	"""One registered computer, or a refusal naming the ones there are."""
	entry = COMPUTERS.get(str(report_name or "").strip())
	if entry:
		return entry
	raise ValueError(
		f"no windowed report called {report_name!r} is registered. This site has: "
		f"{', '.join(sorted(COMPUTERS)) or '<none>'}. A report is registered by calling "
		"erpnext_mcp.services.windowed_reports.register — see docs/reporting_ttm_standard.md."
	)


# ── components: reading, merging ────────────────────────────────────────────
def dig(components: dict, path: str):
	"""One dotted path out of a components dict, or None."""
	node = components
	for part in str(path).split("."):
		if not isinstance(node, dict):
			return None
		node = node.get(part)
	return node


def _plant(target: dict, path: str, value) -> None:
	parts = str(path).split(".")
	node = target
	for part in parts[:-1]:
		nxt = node.get(part)
		if not isinstance(nxt, dict):
			nxt = {}
			node[part] = nxt
		node = nxt
	node[parts[-1]] = value


def aggregate_components(buckets: list, entry: dict, window_days: int) -> dict:
	"""Merge per-step component dicts into one set for the whole window.

	Only ever reached for a `bucket_additive` computer — see the module docstring
	on why Sustainable CF/Acre is not one. Three merges, and each is a different
	kind of thing:

	  * SUMS add up. Revenue, raw OCF, payroll, maintenance capex: twelve months
	    of a flow is the twelve figures added.
	  * WEIGHTED denominators are RE-WEIGHTED, not summed and not averaged. A
	    block productive for six months of the twelve contributed a full month's
	    weight to each of six buckets, and contributes half of its acreage to the
	    year — so each bucket's weighted figure is scaled by that bucket's share
	    of the window's days and the scaled figures are added. Summing twelve
	    monthly time-weighted acreages would give twelve times the farm.
	  * LISTS concatenate and DE-DUPLICATE BY DOCNAME. An asset bought on the
	    first of a month appears in that month's bucket and in no other, but a
	    computation whose buckets overlap at the edges would otherwise report one
	    purchase twice and count it once, which is the worst of both.
	"""
	merged: dict = {}
	base = dict(buckets[0]["components"]) if buckets else {}
	for key, value in base.items():
		if not isinstance(value, (dict, list)):
			merged[key] = value

	for path in entry["sum_keys"]:
		total = 0.0
		for bucket in buckets:
			total += float(dig(bucket["components"], path) or 0)
		_plant(merged, path, round(total, 2))

	for path in entry["weighted_keys"]:
		total = 0.0
		for bucket in buckets:
			days = int(bucket.get("days") or 0)
			if not days or not window_days:
				continue
			total += float(dig(bucket["components"], path) or 0) * days / window_days
		_plant(merged, path, round(total, 4))

	for path in entry["list_keys"]:
		seen: dict = {}
		order: list = []
		for bucket in buckets:
			for row in dig(bucket["components"], path) or []:
				key = _row_key(row)
				if key in seen:
					continue
				seen[key] = row
				order.append(key)
		_plant(merged, path, [seen[key] for key in order])

	return merged


def _row_key(row):
	"""What makes one itemized row the same row as another: its docname.

	Falls back to the whole row rendered as JSON where there is no docname to
	go on, which is exactly right for a computer whose itemization is derived
	rather than record-backed: two identical derived rows ARE the same row, and
	two different ones survive.
	"""
	if isinstance(row, dict):
		for key in ("name", "docname", "asset", "field", "adjustment", "voucher_no"):
			value = row.get(key)
			if value:
				return f"{key}:{value}"
		try:
			return json.dumps(row, sort_keys=True, default=str)
		except Exception:  # pragma: no cover - a row holding something unserialisable
			return repr(row)
	return repr(row)


# ── the cache ───────────────────────────────────────────────────────────────
def cache_available() -> bool:
	try:
		return bool(compat.doctype_exists(DOCTYPE))
	except Exception:  # pragma: no cover - a site mid-migrate
		return False


def _cache_filters(kpi_key: str, company: str, step: str, window_type: str, window_months: int) -> dict:
	return {
		"kpi_key": kpi_key,
		"company": company,
		"computation_step": step,
		"window_type": window_type,
		"window_months": int(window_months),
	}


def cache_read(kpi_key: str, company: str, step: str, window_type: str, window_months: int, as_of) -> dict:
	"""One cached snapshot, or {}. Never raises — a cache miss and a broken cache
	are the same instruction: compute it."""
	if not cache_available() or not kpi_key:
		return {}
	try:
		filters = _cache_filters(kpi_key, company, step, window_type, window_months)
		filters["as_of"] = iso(as_of)
		rows = frappe.db.get_all(
			DOCTYPE,
			filters=filters,
			fields=compat.existing_fields(
				DOCTYPE,
				(
					"name",
					"value",
					"period_start",
					"period_end",
					"components_json",
					"computation_warnings_json",
					"computed_at",
					"source_version",
				),
			),
			limit=1,
		)
		return dict(rows[0]) if rows else {}
	except Exception:  # pragma: no cover - a cache that cannot be read is a miss
		return {}


def cache_write(
	kpi_key: str,
	company: str,
	step: str,
	window_type: str,
	window_months: int,
	as_of,
	snapshot: dict,
	source_version: str = "",
) -> str:
	"""Upsert one snapshot. Never raises — a report is not worth failing over a cache."""
	if not cache_available() or not kpi_key:
		return ""
	try:
		filters = _cache_filters(kpi_key, company, step, window_type, window_months)
		filters["as_of"] = iso(as_of)
		values = {
			"period_start": snapshot.get("period_start"),
			"period_end": snapshot.get("period_end"),
			"value": snapshot.get("value"),
			"components_json": json.dumps(snapshot.get("components") or {}, default=str),
			"computation_warnings_json": json.dumps(snapshot.get("computation_warnings") or [], default=str),
			"computed_at": frappe.utils.now(),
			"source_version": source_version or _version(),
		}
		existing = frappe.db.get_all(DOCTYPE, filters=filters, pluck="name", limit=1)
		if existing:
			for fieldname, value in values.items():
				if compat.has_field(DOCTYPE, fieldname):
					frappe.db.set_value(DOCTYPE, existing[0], fieldname, value)
			return str(existing[0])
		doc = frappe.new_doc(DOCTYPE)
		for fieldname, value in {**filters, **values}.items():
			setattr(doc, fieldname, value)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		return str(doc.name)
	except Exception:  # pragma: no cover - a cache write is best-effort by design
		return ""


def _version() -> str:
	try:
		from .. import __version__

		return str(__version__)
	except Exception:  # pragma: no cover
		return ""


def invalidate_overlapping(company: str, period_start, period_end, kpi_key: str = "") -> dict:
	"""Drop every cached snapshot whose window overlaps a period that has changed.

	Called when an approved normalization adjustment lands on a period history
	already covers — a retroactive approval genuinely changes what every window
	containing it was worth, and a cache that kept serving the old figure would be
	the most expensive kind of wrong: confidently precise, itemized, and stale.

	DELETED RATHER THAN FLAGGED. A cached row carries the components dict as well
	as the number, and a stale components list is worse than a missing one — it is
	a set of ingredients that does not produce the figure printed above it. It is a
	cache; every row in it is derivable, and the next read or the next sweep
	rebuilds exactly what was dropped.

	Never raises. It is called from inside an approval, and an approval that
	failed because a cache would not clear would be a compliance act lost to
	housekeeping.
	"""
	out = {"deleted": 0, "company": company, "kpi_key": kpi_key or None}
	if not cache_available():
		out["note"] = f"this site has no {DOCTYPE} doctype, so there is nothing cached to invalidate."
		return out
	try:
		filters = {
			"company": company,
			"period_end": (">=", iso(period_start)),
			"period_start": ("<=", iso(period_end)),
		}
		if kpi_key:
			filters["kpi_key"] = kpi_key
		names = frappe.db.get_all(DOCTYPE, filters=filters, pluck="name", limit=100000)
		for name in names or []:
			frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True, delete_permanently=True)
		out["deleted"] = len(names or [])
	except Exception as exc:  # pragma: no cover - housekeeping never fails a write
		out["error"] = f"{type(exc).__name__}: {exc}"
	return out


# ── one snapshot ────────────────────────────────────────────────────────────
def compute_snapshot(
	entry: dict,
	company: str,
	period_start,
	period_end,
	computation_step: str,
	use_cache: bool = True,
	window_type: str = WINDOW_TTM,
	window_months: int = DEFAULT_WINDOW_MONTHS,
	as_of=None,
) -> dict:
	"""One window's value and components, from the cache where there is one."""
	as_of = as_of if as_of is not None else period_end
	kpi_key = entry["kpi_key"]
	if use_cache:
		cached = cache_read(kpi_key, company, computation_step, window_type, window_months, as_of)
		if cached:
			return {
				"value": cached.get("value"),
				"period_start": iso(cached["period_start"]) if cached.get("period_start") else iso(period_start),
				"period_end": iso(cached["period_end"]) if cached.get("period_end") else iso(period_end),
				"components": _loads(cached.get("components_json"), {}),
				"computation_warnings": _loads(cached.get("computation_warnings_json"), []),
				"from_cache": True,
				"computed_at": str(cached.get("computed_at") or "") or None,
				"source_version": cached.get("source_version") or None,
			}

	components = entry["computer"](company, iso(period_start), iso(period_end)) or {}
	warnings = list(components.get("computation_warnings") or [])
	snapshot = {
		"value": components.get(entry["value_key"]),
		"period_start": iso(period_start),
		"period_end": iso(period_end),
		"components": components,
		"computation_warnings": warnings,
		"from_cache": False,
		"computed_at": str(frappe.utils.now()),
		"source_version": _version(),
	}
	if use_cache:
		cache_write(
			kpi_key, company, computation_step, window_type, window_months, as_of, snapshot
		)
	return snapshot


def _loads(raw, fallback):
	try:
		return json.loads(raw) if raw else fallback
	except Exception:  # pragma: no cover - a cache row somebody edited
		return fallback


def compute_bucketed(
	entry: dict, company: str, period_start, period_end, computation_step: str, anchor_month: int = 1
) -> dict:
	"""A window assembled from its steps, for a computer that is `bucket_additive`.

	Returns the merged components AND the per-bucket trail, which is the reason to
	take this path at all where it is correct: a reader who wants to know which
	month the revenue fell in has the twelve figures rather than one.
	"""
	start = as_date(period_start)
	end = as_date(period_end)
	buckets = []
	cursor = end
	guard = 0
	while cursor >= start and guard < 400:
		guard += 1
		bucket_start = max(step_period_start(cursor, computation_step), start)
		components = entry["computer"](company, iso(bucket_start), iso(cursor)) or {}
		buckets.append(
			{
				"period_start": iso(bucket_start),
				"period_end": iso(cursor),
				"days": (cursor - bucket_start).days + 1,
				"value": components.get(entry["value_key"]),
				"components": components,
			}
		)
		cursor = bucket_start - datetime.timedelta(days=1)
	buckets.reverse()
	window_days = (end - start).days + 1
	merged = aggregate_components(buckets, entry, window_days) if buckets else {}
	warnings = []
	for bucket in buckets:
		for warning in bucket["components"].get("computation_warnings") or []:
			if warning not in warnings:
				warnings.append(warning)
	return {
		"value": merged.get(entry["value_key"]),
		"period_start": iso(start),
		"period_end": iso(end),
		"components": merged,
		"buckets": [
			{key: bucket[key] for key in ("period_start", "period_end", "days", "value")}
			for bucket in buckets
		],
		"computation_warnings": warnings,
		"from_cache": False,
		"computed_at": str(frappe.utils.now()),
		"source_version": _version(),
	}


# ── how much ledger there actually is ───────────────────────────────────────
def earliest_posting(company: str) -> str:
	"""The oldest submitted GL posting date for a company, or "".

	The honest answer to "is there twelve months of history here". Asked of GL
	Entry because that is where every financial computer in this app ultimately
	reads from, and because a site can have five years of Fiscal Year rows and
	four months of ledger.
	"""
	try:
		if not compat.doctype_exists("GL Entry"):
			return ""
		rows = frappe.db.get_all(
			"GL Entry",
			filters={"company": company, "is_cancelled": 0},
			fields=["posting_date"],
			order_by="posting_date asc",
			limit=1,
		)
		return iso(rows[0]["posting_date"]) if rows and rows[0].get("posting_date") else ""
	except Exception:  # pragma: no cover
		return ""


def months_between(start, end) -> int:
	"""Whole months from `start` to `end`, counting a part month as one.

	Used only to say how much history there is in the warning, where rounding up
	is the conservative direction: it makes a partial window look LESS partial,
	so a site that is warned is a site that genuinely has a gap.
	"""
	first = as_date(start)
	last = as_date(end)
	if last < first:
		return 0
	return (last.year - first.year) * 12 + (last.month - first.month) + 1


# ── statistics ──────────────────────────────────────────────────────────────
def summarise(series: list, current, steps_per_year: int) -> dict:
	"""Mean, median, spread and the two deltas, from a series that may be short.

	EVERY STATISTIC IS None WHERE IT CANNOT BE COMPUTED, never zero. A standard
	deviation of zero means a perfectly steady business and a standard deviation
	of None means one snapshot; reporting the first for the second is how a
	volatility figure becomes an argument for a covenant nobody can hold to.
	"""
	values = [float(row["value"]) for row in series if row.get("value") is not None]
	out = {
		"prior_ttm_series": series,
		"prior_ttm_count": len(values),
		"prior_ttm_mean": None,
		"prior_ttm_median": None,
		"prior_ttm_min": None,
		"prior_ttm_max": None,
		"prior_ttm_stddev": None,
		"current_vs_mean_pct_delta": None,
		"current_vs_prior_year_pct_delta": None,
		"prior_year_value": None,
	}
	if values:
		out["prior_ttm_mean"] = round(statistics.fmean(values), 2)
		out["prior_ttm_median"] = round(statistics.median(values), 2)
		out["prior_ttm_min"] = round(min(values), 2)
		out["prior_ttm_max"] = round(max(values), 2)
	if len(values) > 1:
		out["prior_ttm_stddev"] = round(statistics.stdev(values), 2)

	if current is not None and out["prior_ttm_mean"]:
		out["current_vs_mean_pct_delta"] = round(
			(float(current) - out["prior_ttm_mean"]) / abs(out["prior_ttm_mean"]) * 100, 2
		)

	# The comparator is the entry one full lap of the series back, NOT the date
	# twelve months before `as_of` — those differ whenever twelve months back
	# lands inside a bucket, and the reader means "the same point last year".
	# `series` is newest first, so the prior year's entry is at index
	# steps_per_year - 1: index 0 is one step back, so index 11 is twelve.
	index = int(steps_per_year) - 1
	if 0 <= index < len(series):
		prior = series[index].get("value")
		out["prior_year_value"] = prior
		if current is not None and prior:
			out["current_vs_prior_year_pct_delta"] = round(
				(float(current) - float(prior)) / abs(float(prior)) * 100, 2
			)
	return out


# ── the utility ─────────────────────────────────────────────────────────────
def compute_windowed(
	computer,
	company: str,
	as_of=None,
	window_type: str = WINDOW_TTM,
	window_months: int = DEFAULT_WINDOW_MONTHS,
	computation_step: str = STEP_MONTHLY,
	historical_lookback_years: int = DEFAULT_LOOKBACK_YEARS,
	historical_averaging_enabled: bool = True,
	kpi_key: str | None = None,
	entry: dict | None = None,
	use_cache: bool = True,
	live_computation_cap: int = LIVE_COMPUTATION_CAP,
) -> dict:
	"""Any point-in-time computation, over a window, with its own history beside it.

	`computer` takes (company, period_start, period_end) and returns a components
	dict. Everything else — the boundaries, the history, the cache, the statistics
	and the warnings — is this function. See the module docstring for the boundary
	rule, the fiscal anchoring, why the window is computed whole, and what the cap
	on a live query buys.

	Pass `entry` (a `register` result) to get bucket aggregation and the declared
	component paths; pass a bare `computer` and it is treated as a non-additive
	one whose components are carried through unmerged, which is the safe default.
	"""
	if entry is None:
		entry = {
			"report_name": kpi_key or "anonymous",
			"kpi_key": kpi_key or "",
			"label": kpi_key or "windowed report",
			"computer": computer,
			"value_key": "value",
			"sum_keys": (),
			"weighted_keys": (),
			"list_keys": (),
			"bucket_additive": False,
			"default_step": computation_step,
			"allow_daily": True,
			"unit": "",
			"available": None,
		}
	else:
		entry = {**entry, "computer": computer or entry["computer"]}
		if kpi_key:
			entry["kpi_key"] = kpi_key
	# `kpi_key=None` disables caching, per the interface. An empty key would
	# otherwise write rows nothing could ever look up again.
	if kpi_key is None and not entry.get("kpi_key"):
		use_cache = False

	warnings: list = []

	window_type = str(window_type or WINDOW_TTM)
	if window_type not in WINDOW_TYPES:
		raise ValueError(f"window_type must be one of {', '.join(WINDOW_TYPES)}; got {window_type!r}.")
	computation_step = str(computation_step or STEP_MONTHLY)
	if computation_step not in STEPS:
		raise ValueError(
			f"computation_step must be one of {', '.join(STEPS)}; got {computation_step!r}."
		)
	if computation_step == STEP_DAILY and not entry.get("allow_daily", False):
		warnings.append(
			f"{entry['label']} is not marked `allow_daily`, and a Daily step was asked for anyway. "
			"It is honoured — the caller asked — but a five-year Daily history is over eighteen "
			"hundred full computations where a Monthly one is sixty, and this is why Daily is "
			"opt-in per KPI rather than a default anybody can reach by accident."
		)
	try:
		window_months = max(1, int(window_months or DEFAULT_WINDOW_MONTHS))
	except (TypeError, ValueError):
		window_months = DEFAULT_WINDOW_MONTHS
	try:
		historical_lookback_years = max(0, min(MAX_LOOKBACK_YEARS, int(historical_lookback_years)))
	except (TypeError, ValueError):
		historical_lookback_years = DEFAULT_LOOKBACK_YEARS

	as_of = as_date(as_of or frappe.utils.today())
	anchor_month = fiscal_year_start_month(company)
	period_end = last_completed_boundary(as_of, computation_step, anchor_month)
	steps_per_year = STEPS_PER_YEAR[computation_step]

	# ── the point-in-time block: the step just completed ──────────────────
	point_start = step_period_start(period_end, computation_step)
	point = compute_snapshot(
		entry,
		company,
		point_start,
		period_end,
		computation_step,
		use_cache=use_cache,
		window_type=WINDOW_SNAPSHOT,
		window_months=1,
		as_of=period_end,
	)
	for warning in point["computation_warnings"]:
		if warning not in warnings:
			warnings.append(warning)

	out = {
		"as_of": iso(as_of),
		"company": company,
		"report_name": entry["report_name"],
		"kpi_key": entry["kpi_key"] or None,
		"label": entry["label"],
		"unit": entry["unit"] or None,
		"window_type": window_type,
		"window_months": window_months if window_type in (WINDOW_TTM, WINDOW_CUSTOM) else None,
		"computation_step": computation_step,
		"fiscal_year_start_month": anchor_month,
		"point_in_time": {
			"value": point["value"],
			"period_start": point["period_start"],
			"period_end": point["period_end"],
			"components": point["components"],
			"from_cache": point["from_cache"],
		},
	}
	# A QTD or YTD window is fiscal-anchored whatever the computation step is —
	# it opens at the start of the company's own quarter or year — so the warning
	# has to cover it too. A reader told "year to date" who assumes 1 January on a
	# July-year operation is out by six months and has no way to find out.
	if anchor_month != 1 and (
		computation_step in (STEP_QUARTERLY, STEP_YEARLY)
		or window_type in (WINDOW_QTD, WINDOW_YTD)
	):
		warnings.append(
			f"{company}'s fiscal year opens in month {anchor_month}, so the {window_type} / "
			f"{computation_step} boundaries here are FISCAL rather than calendar — a period ending "
			f"{iso(period_end)} is a fiscal period end and not a calendar one, and 'year to date' "
			"means from the start of THIS company's year. That is deliberate: stepping a rolling "
			"window by calendar quarters on a July-year operation puts every year-end close in the "
			"middle of a bucket. The quarterly discrete report uses calendar quarters instead, "
			"because it is read beside a lender's own."
		)

	if window_type == WINDOW_SNAPSHOT:
		out["window"] = None
		out["historical_averages"] = None
		out["computation_warnings"] = warnings
		out["computed_at"] = str(frappe.utils.now())
		return out

	# ── the window block ──────────────────────────────────────────────────
	if window_type in TO_DATE_WINDOWS:
		# To-date windows run to the MOMENT, not to the last completed step: that
		# is what "to date" means, and the comparison they exist for is against
		# the same span of the prior year.
		win_start = to_date_start(as_of, window_type, anchor_month)
		win_end = as_of
	else:
		win_start = window_start(period_end, window_months)
		win_end = period_end

	if entry["bucket_additive"] and window_type not in TO_DATE_WINDOWS:
		window_snapshot = compute_bucketed(
			entry, company, win_start, win_end, computation_step, anchor_month
		)
		if use_cache:
			cache_write(
				entry["kpi_key"],
				company,
				computation_step,
				window_type,
				window_months,
				win_end,
				window_snapshot,
			)
	else:
		window_snapshot = compute_snapshot(
			entry,
			company,
			win_start,
			win_end,
			computation_step,
			use_cache=use_cache and window_type not in TO_DATE_WINDOWS,
			window_type=window_type,
			window_months=window_months,
			as_of=win_end,
		)
	for warning in window_snapshot["computation_warnings"]:
		if warning not in warnings:
			warnings.append(warning)

	window_block = {
		"value": window_snapshot["value"],
		"period_start": window_snapshot["period_start"],
		"period_end": window_snapshot["period_end"],
		"components": window_snapshot["components"],
		"from_cache": window_snapshot.get("from_cache", False),
	}
	if window_snapshot.get("buckets"):
		window_block["buckets"] = window_snapshot["buckets"]
	out["window"] = window_block
	# The spec's documented key. Present for TTM — which is the default and so the
	# ordinary case — and absent for the others rather than lying about them: a
	# YTD block under a key called `ttm` is the kind of thing somebody quotes.
	if window_type == WINDOW_TTM:
		out["ttm"] = window_block

	# ── how much history is actually behind it ────────────────────────────
	earliest = earliest_posting(company)
	if earliest:
		available = months_between(earliest, win_end)
		out["ledger_starts"] = earliest
		out["ledger_months_available"] = available
		if window_type in (WINDOW_TTM, WINDOW_CUSTOM) and available < window_months:
			warnings.append(
				f"only {available} month(s) of ledger history available (the first posting for "
				f"{company} is {earliest}); the {window_months}-month window is PARTIAL. The figure "
				"covers what is there and is not annualized — annualizing it would invent "
				f"{window_months - available} month(s) of a season that has not happened. Read it as "
				f"{available} month(s) of trading, and expect it to move as the window fills."
			)
	else:
		out["ledger_starts"] = None
		out["ledger_months_available"] = None
		warnings.append(
			f"there are no submitted GL postings for {company} at all, so every figure here is "
			"computed over an empty ledger. That is not the same as a business that earned nothing: "
			"it is a business whose books are not on this site, or one whose entries are all still "
			"drafts. GL Entry exists only for submitted vouchers."
		)

	# ── the history ───────────────────────────────────────────────────────
	if not historical_averaging_enabled or historical_lookback_years <= 0:
		out["historical_averages"] = None
		out["computation_warnings"] = warnings
		out["computed_at"] = str(frappe.utils.now())
		return out

	# A TO-DATE WINDOW LAPS BY ITS OWN PERIOD, NOT BY THE COMPUTATION STEP. Twelve
	# monthly entries under a YTD figure would be twelve trailing-twelve-month
	# windows sitting beneath a year-to-date one, and every average and delta
	# computed from them would compare a different thing with the same number of
	# decimal places. See `to_date_prior`.
	if window_type in TO_DATE_WINDOWS:
		series_steps_per_year = STEPS_PER_YEAR[
			{WINDOW_MTD: STEP_MONTHLY, WINDOW_QTD: STEP_QUARTERLY, WINDOW_YTD: STEP_YEARLY}[window_type]
		]
	else:
		series_steps_per_year = steps_per_year

	wanted = int(historical_lookback_years) * series_steps_per_year
	series: list = []
	computed_live = 0
	truncated_at = 0
	for index in range(1, wanted + 1):
		if window_type in TO_DATE_WINDOWS:
			prior_start, prior_end = to_date_prior(as_of, window_type, index, anchor_month)
		else:
			prior_end = step_back(period_end, computation_step, index, anchor_month)
			prior_start = window_start(prior_end, window_months)
		if earliest and iso(prior_end) < earliest:
			# Past the start of the ledger. Stopping is the honest end of the
			# series: rows saying "no data" are a slower way of saying nothing,
			# and they would drag every average down towards zero if anybody
			# forgot to filter them.
			break
		cached = (
			cache_read(entry["kpi_key"], company, computation_step, window_type, window_months, prior_end)
			if use_cache
			else {}
		)
		if cached:
			series.append(
				{
					"as_of": iso(prior_end),
					"period_start": iso(cached.get("period_start") or prior_start),
					"period_end": iso(cached.get("period_end") or prior_end),
					"value": cached.get("value"),
					"from_cache": True,
				}
			)
			continue
		if computed_live >= max(0, int(live_computation_cap)):
			truncated_at = index
			break
		snapshot = compute_snapshot(
			entry,
			company,
			prior_start,
			prior_end,
			computation_step,
			use_cache=use_cache,
			window_type=window_type,
			window_months=window_months,
			as_of=prior_end,
		)
		computed_live += 1
		series.append(
			{
				"as_of": iso(prior_end),
				"period_start": snapshot["period_start"],
				"period_end": snapshot["period_end"],
				"value": snapshot["value"],
				"from_cache": False,
			}
		)

	averages = summarise(series, window_block["value"], series_steps_per_year)
	averages["requested_entries"] = wanted
	averages["computed_live"] = computed_live
	averages["lookback_years"] = historical_lookback_years
	averages["series_step"] = (
		window_type if window_type in TO_DATE_WINDOWS else computation_step
	)
	out["historical_averages"] = averages

	if truncated_at:
		warnings.append(
			f"the historical series stops at {len(series)} of {wanted} entries: a live query "
			f"computes at most {live_computation_cap} missing snapshot(s) and then stops, because a "
			"read that runs for minutes is a read somebody kills. The rest are not lost — the "
			"overnight sweep fills the cache, or run "
			f"recompute_kpi_history(kpi_key={entry['kpi_key']!r}, company={company!r}) to build it "
			"now. Every average above is computed from the entries that ARE here and says how many "
			"that was."
		)
	elif len(series) < wanted:
		warnings.append(
			f"the historical series has {len(series)} of the {wanted} entries "
			f"{historical_lookback_years} year(s) of {computation_step} history would hold, because "
			f"the ledger does not go back that far. The averages are computed from {len(series)} "
			"entr(y/ies) — read prior_ttm_count before treating the mean as a norm."
		)
	if averages["current_vs_prior_year_pct_delta"] is None and window_block["value"] is not None:
		warnings.append(
			"there is no entry one full year back in the series, so "
			"current_vs_prior_year_pct_delta is null rather than zero. A year-on-year comparison "
			"needs a year to compare with."
		)

	out["computation_warnings"] = warnings
	out["computed_at"] = str(frappe.utils.now())
	return out


def run(report_name: str, company: str, **kwargs) -> dict:
	"""One registered report, windowed. The entry point every tool goes through."""
	entry = registered(report_name)
	kwargs.setdefault("computation_step", entry["default_step"])
	kwargs.setdefault("kpi_key", entry["kpi_key"])
	return compute_windowed(entry["computer"], company, entry=entry, **kwargs)


# ── the overnight sweep ─────────────────────────────────────────────────────
def recompute_kpi_history_incremental() -> int:
	"""Fill the cache for every registered report and every company. NEVER RAISES.

	The one scheduled job this release adds, and it is ONE job that iterates
	rather than a cron entry per KPI. A framework of KPIs with a cron each is a
	scheduler nobody can read and a bench that wakes fifteen times a night to do
	fifteen things that could have been done once.

	IT IS INCREMENTAL BY CONSTRUCTION. For each (report, company, step) it finds
	the newest cached `as_of`, and computes forward from there to the last
	completed boundary. A cache that is current does nothing at all; a cache with
	a gap fills the gap; an empty cache builds the full lookback. It has no live
	computation cap because nobody is waiting on it.

	It writes only this app's own `Financial KPI History` and reads only what the
	registered computers read, which clears the bar `hooks.py` sets for every job
	on this app's schedule. It returns the number of snapshots written, which is
	what the scheduler log shows.
	"""
	written = 0
	try:
		# THE IMPORT IS LOAD-BEARING AND IS WHY IT IS HERE RATHER THAN AT THE TOP.
		# `financial_reports` registers the computers by importing, and the
		# scheduler reaches this function by dotted path — so on a worker that has
		# imported nothing else of this app, the registry would be empty and the
		# sweep would walk zero reports and report success. It is imported here
		# rather than at module scope because `financial_reports` imports THIS
		# module to call `register`, and a top-level import either way round is a
		# cycle.
		from . import financial_reports  # noqa: F401

		if not cache_available():
			return 0
		if not _sweep_enabled():
			return 0
		companies = frappe.db.get_all("Company", pluck="name", limit=200) or []
	except Exception:  # pragma: no cover - a site mid-migrate
		return 0

	for report_name, entry in sorted(COMPUTERS.items()):
		try:
			if entry.get("available") and not entry["available"]():
				continue
		except Exception:  # pragma: no cover
			continue
		for company in companies:
			try:
				written += _sweep_one(entry, str(company))
			except Exception as exc:  # pragma: no cover - one KPI must not stop the rest
				frappe.log_error(
					title="erpnext_mcp: KPI history sweep",
					message=f"{report_name} / {company}: {type(exc).__name__}: {exc}",
				) if hasattr(frappe, "log_error") else None
	try:
		frappe.db.commit()
	except Exception:  # pragma: no cover
		pass
	return written


def _sweep_enabled() -> bool:
	"""The kill switch. Off means the sweep does nothing and says nothing.

	True on any failure to read it, which is the opposite of how a security
	control fails and the right way round for this one: the sweep writes only a
	cache, and a site whose settings row cannot be read is better served by a
	warm cache than by a report that takes four minutes the first time somebody
	opens it.
	"""
	try:
		from .. import settings

		return settings.kpi_history_sweep_enabled()
	except Exception:  # pragma: no cover
		return True


def _sweep_one(entry: dict, company: str, lookback_years: int = DEFAULT_LOOKBACK_YEARS) -> int:
	"""Compute the missing snapshots for one report on one company. Returns how many."""
	step = entry["default_step"]
	window_type = WINDOW_TTM
	window_months = DEFAULT_WINDOW_MONTHS
	anchor_month = fiscal_year_start_month(company)
	today = as_date(frappe.utils.today())
	newest_wanted = last_completed_boundary(today, step, anchor_month)

	earliest = earliest_posting(company)
	if not earliest:
		return 0

	latest_cached = ""
	try:
		rows = frappe.db.get_all(
			DOCTYPE,
			filters=_cache_filters(entry["kpi_key"], company, step, window_type, window_months),
			fields=["as_of"],
			order_by="as_of desc",
			limit=1,
		)
		latest_cached = iso(rows[0]["as_of"]) if rows and rows[0].get("as_of") else ""
	except Exception:  # pragma: no cover
		latest_cached = ""

	wanted = int(lookback_years) * STEPS_PER_YEAR[step]
	written = 0
	for index in range(0, wanted + 1):
		boundary = step_back(newest_wanted, step, index, anchor_month)
		if iso(boundary) < earliest:
			break
		if latest_cached and iso(boundary) <= latest_cached:
			# Everything at or before the newest cached snapshot is either present
			# or deliberately gone (invalidated), and the invalidated ones come
			# back through the same loop on the run after the gap reaches the top.
			existing = cache_read(
				entry["kpi_key"], company, step, window_type, window_months, boundary
			)
			if existing:
				continue
		start = window_start(boundary, window_months)
		if entry["bucket_additive"]:
			snapshot = compute_bucketed(entry, company, start, boundary, step, anchor_month)
			cache_write(entry["kpi_key"], company, step, window_type, window_months, boundary, snapshot)
		else:
			compute_snapshot(
				entry,
				company,
				start,
				boundary,
				step,
				use_cache=True,
				window_type=window_type,
				window_months=window_months,
				as_of=boundary,
			)
		written += 1
	return written


def clear(kpi_key: str, company: str = "", computation_step: str = "") -> int:
	"""Drop every cached row for one KPI, optionally narrowed. Returns how many went."""
	if not cache_available():
		return 0
	filters: dict = {"kpi_key": kpi_key}
	if company:
		filters["company"] = company
	if computation_step:
		filters["computation_step"] = computation_step
	try:
		names = frappe.db.get_all(DOCTYPE, filters=filters, pluck="name", limit=100000) or []
		for name in names:
			frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True, delete_permanently=True)
		return len(names)
	except Exception:  # pragma: no cover
		return 0
