# The Financial KPI Framework

*v0.39.0. The companion to [`reporting_ttm_standard.md`](reporting_ttm_standard.md),
which says how a figure is windowed, and to
[`configurable_compliance_framework.md`](configurable_compliance_framework.md),
which made compliance rules data first.*

---

## Why this exists

v0.19.5 shipped one KPI — Sustainable CF/Acre. v0.19.6 put it under a window
standard and demonstrated the standard generalized by registering two more beside
it. All three are Python functions in `services/financial_reports.py`, and adding
a fourth is six lines, a test, a review, a release and a deploy.

That is a good process for a KPI this app's authors chose. It is not a process at
all for the KPI an operation's own lender asked about on a Tuesday.

Every farm has two or three ratios that are genuinely its own — a cost per bin
that only means anything against its own bin count, a debt service coverage on the
covenant's arithmetic rather than anybody else's, distributions against normalized
cash flow for the December conversation with the members. None of those belong in
a shipped app; all of them belong on the dashboard of the farm that needs them.

So a KPI is a record: `Financial KPI Definition`.

---

## The shape of a definition

| Field | What it is |
|---|---|
| `kpi_id` | The stable key. **It is the cache key** on every `Financial KPI History` row, so it is unique and cannot be renamed |
| `title` | What it is called on a dashboard |
| `category` | Profitability, Liquidity, Leverage, Efficiency, Operational, Custom |
| `unit` | Currency, Percentage, Ratio, Days, Acres, Units |
| `formula_type` | `Built-in` or `Expression`. There is no third value |
| `builtin_function` | For Built-in: which shipped computer |
| `expression` / `expression_inputs` | For Expression: the arithmetic and what each variable is |
| `default_window_type` / `default_window_months` / `default_computation_step` | The window, defaulting to a monthly TTM |
| `historical_averaging_enabled` / `historical_lookback_years` | Whether and how far back to build the prior-window series |
| `threshold_warning_low` / `threshold_critical_low` / `threshold_warning_high` / `threshold_critical_high` | The lines a breach is measured against |
| `enabled` / `dashboard_visible` / `display_order` / `company` | Whether it runs, whether it shows, where it sits, and where it applies |

An **empty `company` means every company**, which is the ordinary case: a current
ratio is a current ratio wherever it is computed.

---

## The two formula types, and why there is no third

### `Built-in`

Delegates to a computer that ships with this app — `sustainable_cf_per_acre`,
`ocf`, `revenue` — registered in `services/financial_reports.py` and reviewed like
any other code. The record still owns everything an operator would legitimately
change: the window, the step, the lookback, the thresholds, the dashboard
position, the switch. It owns nothing about the arithmetic.

A definition naming a computer this site has not got is **refused at save time**,
because a definition pointing at a computer that does not exist produces nothing,
for ever, quietly — which on a dashboard is indistinguishable from a business with
no activity. The same failure `Compliance Rule.builtin_scanner` is guarded
against, for the same reason.

### `Expression`

Evaluates the text in `expression` over the variables `expression_inputs` names.

```
(current_assets - inventory) / current_liabilities
net_income / revenue * 100
max(harvest_cost / bins, 0)
```

The text is parsed to an AST, every node is checked against an allowlist of
arithmetic, and the surviving tree is walked by an evaluator in
`services/kpi_engine.py` that has **no builtins, no attribute access, no
subscripts, no statements, no name binding, and no calls except to four bound
numeric functions.**

**Allowed:** `+ - * / // % **` on numbers and variable names, unary minus,
parentheses, comparisons inside a conditional, and `min`, `max`, `abs`, `round`.

**Refused, by name, at save time:** `import` and `__import__`; every attribute
access; subscripts, lists, dicts, sets, tuples, f-strings, starred arguments;
lambdas; every comprehension; assignment expressions; string constants; any call
to anything not on the four-name list; underscore-leading names. An exponent above
64 is refused at evaluation.

Attribute access is refused **by node type rather than by name** because
`x.__class__.__bases__[0].__subclasses__()` is the standard escape from every
sandbox that forgot it, and it needs no imports at all.

### Why not `custom_python`, like a compliance rule has

`alerts/sandbox.py` is a restricted *interpreter* with loops, statements and
comprehensions in it, and `Compliance Rule.custom_python` needs it: a rule can
genuinely have to express *"this record is stale unless that other record
supersedes it"*, which is a shape no set of declarative fields captures.

A financial KPI is a number divided by another number. Nothing about a ratio needs
a `for` loop, so the sandbox here does not have one — and a sandbox without loops,
without statements and without name binding is a very much smaller thing to be
confident about than one with them.

Two sandboxes, on purpose. Merging them would mean the finance side inherited
every node the compliance side needs.

**A KPI that genuinely needs more than arithmetic wants a built-in computer**,
which is six lines in `services/financial_reports.py` with a test.

---

## The four input sources

```json
{
  "current_assets":      {"source": "gl", "root_type": "Asset", "balance": true},
  "current_liabilities": {"source": "gl", "root_type": "Liability", "balance": true},
  "sales":               {"source": "report", "report_name": "revenue", "path": "total"},
  "cf_per_acre":         {"source": "kpi", "kpi_id": "sustainable_cf_per_acre"},
  "sqft_per_acre":       {"source": "constant", "value": 43560}
}
```

### `gl`

Reads GL Entry directly, for the reason `sustainable_cf_per_acre` computes OCF
from GL rather than off the Cash Flow report: every figure traces back to rows
somebody can open, which is what makes it defensible to a lender.

Narrow with `root_type` (one of the five ERPNext has), `account_type`, `accounts`
(an explicit list) or `account_number_prefix`. **An input that narrows nothing is
refused**, because an unnarrowed query sums the entire chart of accounts to
approximately zero and that is a number nobody would question.

**`"balance": true` is the flag the whole source turns on.** A balance is a
POSITION at the window's end — every posting from the beginning of the ledger
through `period_end`. A movement is what crossed the window. A current ratio built
from twelve months of movement in a cash account is not a current ratio; it is a
cash flow with a ratio's name on it, and it will be wrong by whatever the opening
balance was.

**`sign` is `natural` by default**, which reads credit-balance roots (Income,
Liability, Equity) as credits less debits and debit-balance roots (Asset, Expense)
the other way. Revenue read the wrong way round comes out negative on every
well-kept set of books, and a figure that is negative everywhere is one everybody
misreads as a loss. Where one input matches accounts across roots that disagree,
the mixture is **warned about** rather than silently netted; `credit_minus_debit`
and `debit_minus_credit` are there for when somebody means it.

**An input that matched no account is a null, not a zero**, and the warning says
it is a chart-of-accounts gap rather than a period with no activity.

### `report`

Reads a dotted component path off a shipped computer, so an Expression KPI can be
built on top of one. A `report_name` this site has not registered is refused at
save time.

### `kpi`

Another definition's value for the same window. **Cycles are refused at save
time** for the two shapes people build by hand — a definition naming itself, and a
mutual pair — and caught at compute time with the whole chain named for everything
else, rather than by a worker running out of stack at three in the morning. The
depth ceiling is three: a KPI built on a KPI built on a KPI is one nobody can read
the provenance of.

### `constant`

A number with a name, which is what a magic constant in a formula should always
have been. A bare number is accepted as the short form.

---

## A definition holds the question. It never holds an answer

Nothing on a `Financial KPI Definition` is a number that came out of the ledger.
The thresholds are lines somebody drew and the window fields say which period to
ask about; that is the whole of it.

Every figure is computed from the ledger at the moment it is asked for, cached in
`Financial KPI History` **with the components that produced it**, and derivable
again by rerunning the same computation over the same window. That is what makes
the cache safe to delete, and what makes a historical figure defensible a year
later.

An Expression KPI is as defensible as a built-in one, and this is why: the
components dict carries every input with what it matched — how many accounts, how
many GL entries, which way round it was read, whether it was a balance or a
movement. A reader sees not just that `current_assets` was 412,000 but where the
412,000 came from.

---

## Every KPI goes through the window standard

`compute_kpi` does not compute a window. It builds a computer — a callable taking
`(company, period_start, period_end)` — and hands it to
`windowed_reports.compute_windowed`, which is the same function
`get_windowed_report` has gone through since v0.19.6.

The boundaries, the fiscal-year anchoring, the last-completed-step rule, the
cache, the prior-window history, the statistics and the partial-window warnings
therefore behave identically for a KPI somebody typed into a form this morning and
for the one that shipped in v0.19.5.

That is not tidiness. A framework whose new KPIs get a second, simpler window
implementation is a framework where the new KPIs are quietly wrong at the fiscal
year boundary, and nobody finds out until a lender does.

**An Expression KPI is never bucket-additive.** Most of them are ratios, and a
ratio assembled from twelve monthly ratios is the average of twelve ratios rather
than the ratio of twelve months — a different number, always, and a worse one. The
window is computed whole.

---

## `kpi_id` is the cache key

A Compliance Rule is versioned by copy: an edit writes v+1 and the old row stays,
so an alert raised in April can still be read against the definition that raised
it.

**A KPI is not**, and the difference is what the two records produce. An alert is
an EVENT with a definition behind it. A KPI is a LINE, and a line assembled from
two definitions of one number is a chart with an unmarked join in it — the single
most misleading object a finance dashboard can contain.

So:

* `kpi_id` is unique, lower-case, and cannot be renamed. Renaming it would orphan
  the whole cached series: the old line stays in the table under a name nothing
  reads, and the chart starts again from nothing.
* Changing the arithmetic of a KPI that has history is **reported as a decision**,
  with the cached row count in front of the caller. The usual right move is a new
  `kpi_id` beside the old one so both series stay readable.
* Where it genuinely is a correction, `refresh_kpi_cache(force=true)` rebuilds the
  whole series under the new formula.
* `source_version` on each cached row is the other half of the guarantee, for the
  case where the app itself changes a built-in computer.

---

## Thresholds, and the one calendar

Four thresholds: `threshold_warning_low`, `threshold_critical_low`,
`threshold_warning_high`, `threshold_critical_high`. Both directions are real —
a debt-to-equity ratio, a days-sales-outstanding and a cost per bin are all KPIs
whose bad news is a big number.

**Empty is not zero.** An omitted threshold means that direction is not bad for
this KPI; a zero means a negative value is a warning. On a cash-flow KPI those are
two very different operations.

**A crossed pair is refused at save time.** A critical floor above its warning
floor can never be read the way it is written — every value past critical is also
past warning, so the critical line never fires on its own and the dashboard
reports the lesser of the two on the more serious breach. Overlapping low and high
thresholds are refused too: every possible value would breach one of them.

**`No thresholds` is not `OK`.** "Nobody drew a line" and "inside the line" are
different statements, and a dashboard showing them the same green would be
claiming something nobody checked. `compute_all_kpis` names the unwatched KPIs,
because an empty `breached` list is not by itself a healthy operation.

### The alert

A breach raises a `Compliance Alert` through the existing hourly sweep, under a
new `Finance` category, with the same dismissal, the same snooze, and the same
auto-clear when the value comes back inside. Nobody closes it, because there was
never a task — there was a number.

An operation with two alerting systems reads neither, and a covenant about to be
breached is exactly as much a Monday-morning problem as a cabin with a dead carbon
monoxide detector.

`financial_kpi_threshold_breach` is a **built-in scanner** for a reason neither of
the other two permanent built-ins has: its thresholds are not on its own row. They
live on each definition, because they are per-KPI and per-unit — a ratio's warning
line and a dollar figure's warning line are not values that can share a column.
The rule record still owns the two severities, the scope filters and the switch.

**It reads the cache and never computes.** The alert sweep runs hourly beside
somebody's real work; a scan that recomputed every KPI for every company would put
minutes of GL arithmetic on that path, every hour, for a figure that moves once a
month. The overnight refresh fills the cache and the scan reads the newest
snapshot it finds — which is at most a day old and is the *same* figure the
dashboard is showing, so an alert and a dashboard can never disagree.

**A KPI with no cached value raises nothing AND DISMISSES NOTHING**, which is the
reading every rule in that file gives to an absent record.

---

## Adding a KPI

```
create_financial_kpi_definition({
  "kpi_id": "current_ratio",
  "title": "Current Ratio",
  "category": "Liquidity",
  "unit": "Ratio",
  "formula_type": "Expression",
  "expression": "current_assets / current_liabilities",
  "expression_inputs": {
    "current_assets":      {"source": "gl", "root_type": "Asset", "balance": true},
    "current_liabilities": {"source": "gl", "root_type": "Liability", "balance": true}
  },
  "default_window_type": "Snapshot",
  "threshold_warning_low": 1.5,
  "threshold_critical_low": 1.0
})
```

`Snapshot` rather than TTM, because a current ratio is a **position** rather than
a flow — and the `"balance": true` on both inputs is what makes it one.

Then `refresh_kpi_cache({"kpi_id": "current_ratio"})` to build its history now,
or wait for the 03:00 job. A chart with one point on it is not a trend.

---

## What the install seeds

Exactly one definition: **Sustainable CF/Acre**, as a Built-in on the computer
that has shipped since v0.19.5, adopting the `kpi_key` the cache has been using
since v0.19.6 so the existing series continues rather than a second one starting
beside it.

A seeded KPI is a claim that this app knows what an operation should watch, and it
can only honestly make that claim about a metric it also ships the computer for. A
current ratio does not qualify — not because it is a bad metric, but because the
accounts that make it up are named differently on every chart, and a seeded
definition pointing at the wrong ones would put a confident, precise, **wrong**
number on somebody's dashboard from the day they installed the app.

**No thresholds are seeded either.** A defensible floor under cash flow per acre is
a number about one operation's own cost structure and debt service, and a seeded
one would be a line somebody had not drawn being enforced on a calendar.

The seeder is idempotent and checked by `kpi_id`: an operator who disabled it
stays disabled, one who set thresholds keeps them, one who moved it down the
dashboard does not find it back at the top after every migrate.

---

## The overnight job

`erpnext_mcp.services.kpi_engine.refresh_all_kpi_caches` at `0 3 * * *` — between
the shipped-report sweep at two and the regulation feed at four.

It is the two o'clock job's counterpart for KPIs that are records, and it exists
separately because a shipped report has no definition to read its window type,
window length and step from, so the older job assumes a monthly TTM.

**One job that iterates**, and here that is load-bearing rather than tidy: the
whole point of the framework is that an operator adds a KPI without a code
release, and a KPI that needed its own scheduler entry would be one they could not
add. Adding a KPI adds no line to `hooks.py`.

It shares `enable_kpi_history_sweep` with the two o'clock job rather than getting a
second checkbox — they cache the same doctype for the same reason, and a second
setting called something almost identical is how a setting stops being read.
Turning it off costs speed and nothing else: every KPI still answers, from a cold
cache, saying how much history it had to leave out.

---

## Who may do any of this

System Manager, Accounts Manager, Farm Manager — the same three that hold the
normalization register. A KPI definition decides what number a lender is shown and
what threshold raises an alert about it, which is the same class of authority as
approving an add-back to operating cash flow, and it is a smaller list than the
one that reads the calendar those alerts land on.
