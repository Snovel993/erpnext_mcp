# The window standard: TTM by default

v0.19.6. Every financial report in this app defaults to a **trailing twelve
months**, with a configurable computation step and a historical-averages block
beside it. This is not a feature on one metric. It is the shape every financial
figure takes from here on, and a report that skips it is a bug.

## Why TTM, on a farm especially

Agricultural revenue is aggressively seasonal, and a figure that ignores that is
not merely imprecise — it is confidently wrong in a direction that changes with
the month somebody asked. Q3 is harvest and Q1 is pruning. Set those two beside
each other and the answer is that the operation collapsed in January and
recovered in September, **every year, on every farm**, whether or not anything
happened. Nobody believes that about their own business, and everybody quotes
numbers that assert it.

A trailing twelve months smooths the cycle out by construction: the window is
always one complete turn of the operation's own calendar, so pruning, thinning,
harvest and the winter are each inside it exactly once no matter when it is read.
Consecutive points differ only by the month that entered and the month that left,
which means **the line moves when the business moves**. That is why TTM is the
standard lens for public-company reporting and for lender covenants, and it fits
here better than it fits there.

## Three blocks, and each corrects the other two

```python
{
  "as_of": "2026-08-03",
  "company": "Highland LLC",
  "window_type": "TTM", "window_months": 12, "computation_step": "Monthly",
  "fiscal_year_start_month": 1,

  "point_in_time": {                       # the step just finished
    "value": 910.0,
    "period_start": "2026-07-01", "period_end": "2026-07-31",
    "components": {...}
  },
  "window": {                              # also `ttm` when window_type is TTM
    "value": 921.0,
    "period_start": "2025-08-01", "period_end": "2026-07-31",
    "components": {                        # summed / re-weighted over the window
      "raw_ocf": {"value": 412000.0, ...},
      "normalization_adjustments": [ {...with justification and signature...} ],
      "normalized_ocf": 436000.0,
      "maintenance_capex": {"total": 68000.0, "itemized": [...]},
      "productive_acres": {"time_weighted": 399.6, "itemized": [...]}
    }
  },
  "historical_averages": {
    "prior_ttm_series": [{"as_of": "2026-06-30", "value": 850.0}, ...],
    "prior_ttm_count": 60,
    "prior_ttm_mean": 861.0, "prior_ttm_median": 858.5,
    "prior_ttm_min": 820.0,  "prior_ttm_max": 895.0, "prior_ttm_stddev": 15.2,
    "current_vs_mean_pct_delta": 6.9,
    "current_vs_prior_year_pct_delta": 4.2
  },

  "ledger_starts": "2019-03-04", "ledger_months_available": 89,
  "computation_warnings": [...],
  "computed_at": "2026-08-03T10:30:00", "kpi_key": "sustainable_cf_per_acre"
}
```

`point_in_time` answers *where were we in the period just finished*. `window`
answers *how is the business running over its natural cycle*.
`historical_averages` answers *is that good or bad for this operation* — which
is the question the first two cannot touch and the one somebody setting a budget
is actually asking. A TTM figure of 921 means one thing against a five-year mean
of 861 and the opposite against one of 990.

Snapshot alone flatters harvest and demonizes pruning. TTM alone hides an
emerging trend inside eleven months of history. A historical average without the
current figure describes a farm that no longer exists. All three, always.

## The boundary rule

One rule, not five:

```
period_end   = last completed `computation_step` boundary <= as_of
period_start = add_months(period_end, -window_months) + 1 day
```

Read on 2026-08-03: **Monthly** ends 2026-07-31 and starts 2025-08-01;
**Quarterly** ends 2026-06-30 and starts 2025-07-01; **Daily** ends 2026-08-02
and starts 2025-08-03.

The part-finished period is excluded and that is the point. Three days of August
against twelve months of everything else is a figure that falls every first of
the month and recovers by the thirty-first — an operator reading it on the fourth
will believe the fall. `period_start` is the day *after* the same date twelve
months back, because `period_end` is inclusive everywhere in this app.

**Quarterly and Yearly steps follow the company's own fiscal year.** A month is a
month on every calendar; a quarter is not, and a July-year operation stepping its
history by calendar quarters would put every year-end close in the middle of a
bucket. The anchor is reported as `fiscal_year_start_month` and a non-calendar
year produces a warning saying so. (The *discrete quarterly* report keeps calendar
quarters, deliberately — it is read beside a lender's own pack.)

## Adding a windowed report

Write a computer taking `(company, period_start, period_end)` and returning a
components dict with a value key, then register it:

```python
windows.register(
	"revenue",
	revenue,
	kpi_key="revenue",
	label="Revenue",
	value_key="total",
	sum_keys=("total",),
	list_keys=("by_account",),
	bucket_additive=True,
	unit="currency",
)
```

The boundaries, the history, the cache, the statistics, the warnings and the MCP
tool all follow. It is reachable through `get_windowed_report` immediately — a
framework whose every KPI costs a new tool is a framework with six KPIs in it.

`bucket_additive` is the one judgement. **Revenue is additive**: a sum over GL
rows with no containment rule in it, so its window is assembled from its steps
and the per-month trail rides along in `window.buckets`. **Sustainable CF/Acre is
not**, twice over — it is a *ratio*, and the average of twelve monthly ratios is
not the ratio of the twelve-month totals; and its approved adjustments are
counted by period *containment*, so a quarter-long insurance recovery falls
inside no monthly bucket and a year assembled from twelve months would drop it
with nothing saying why. Non-additive windows are computed whole.

## Budget vs actual

- *Are we on track against how we normally run?* — `current_vs_mean_pct_delta`,
  read beside `prior_ttm_stddev`. A 10 % swing is noise on one operation and an
  alarm on another, and the standard deviation is what tells them apart.
- *Are we on track against last year?* — `current_vs_prior_year_pct_delta`. The
  comparator is the series entry one full lap back, not the date twelve months
  before `as_of`, because that date can land inside a bucket.
- *Same period, prior year?* — `window_type="MTD" | "QTD" | "YTD"`, which
  accumulate from the start of the current period to `as_of`.

**A to-date window's prior series laps by its own period, not by the computation
step.** "Are we ahead of last year?" means the first eight months of this year
against the first eight months of last year, so a YTD figure gets prior *YTD*
entries one year apart, QTD gets prior quarters, MTD gets prior months. The span
is preserved by day offset into the period — 34 days into this quarter against 34
days into that one — clamped to the prior period's own last day, because 31 March
has no counterpart in February. A prior series built out of trailing-twelve-month
windows would answer a different question with the same number of decimal places,
and nobody would notice: both series are plausible and neither is labelled.
`historical_averages.series_step` says which was used.

## Partial history is a warning, never a smaller number

A site with four months of ledger has no trailing twelve months. Both wrong
answers are available and both get quoted: annualizing it (inventing eight months
of a season that has not happened) or returning it unlabelled as TTM (the same
invention with the evidence removed). So the window is computed over what is
there, and the payload says
`only 4 month(s) of ledger history available … the 12-month window is PARTIAL`.
Every statistic from a short series reports `prior_ttm_count`, and anything that
cannot be computed is **null rather than zero** — a standard deviation of zero
means a perfectly steady business, and a standard deviation of null means one
snapshot.

## Cache strategy

A five-year Monthly history is sixty full computations over twelve months of GL
each. `Financial KPI History` holds one row per
`(kpi_key, company, computation_step, window_type, window_months, as_of)`, with
the **components dict** as well as the figure — a cached number with no
ingredients is one an auditor cannot test, and the historical figures are exactly
the ones nobody can recompute from memory.

- **Overnight sweep**, `0 2 * * *`. One scheduled job that iterates every
  registered report and every company; adding a KPI adds no cron entry. It is
  incremental against what is cached, so a current cache costs nothing. Kill
  switch: `enable_kpi_history_sweep` on ERPNext MCP Settings.
- **On demand**, bounded. A live query computes at most 24 missing snapshots and
  then stops, returning a shorter series with a warning — a read that runs for
  four minutes is a read somebody kills and then distrusts.
- **Invalidation is active on approval, passive on read.**
  `approve_normalization_adjustment` **deletes** every cached snapshot whose
  window overlaps the adjustment's period; the next read or the next sweep
  rebuilds them. Deleted rather than flagged, because a stale components list is
  worse than a missing one — it is a set of ingredients that does not produce the
  number printed above it. `recompute_kpi_history` rebuilds now, for when the
  pack goes out this afternoon.

Every row is derivable. Dropping the whole table changes no answer, only how long
the next report takes.

## Roadmap: KPIs as data

The window fields here are Python arguments and the computer registry is a Python
dict, because a computer is a function. The **Financial KPI Framework** (Phase 2)
makes a KPI a *record*: a `KPI Definition` carrying `default_window_type`,
`default_window_months`, `default_computation_step`,
`historical_averaging_enabled` and `historical_lookback_years` as first-class
fields, so an operator defines a KPI once and the dashboard chart, the MCP output,
the alerts and the budget comparison all consume the same shape without code.
`Financial KPI History.kpi_key` becomes the link to it, and every row written
today keeps its meaning. It is the same rules-as-data move the configurable
compliance framework makes, applied to money.
