# Sustainable CF/Acre

$$\text{Sustainable CF/Acre} = \frac{\text{Normalized OCF} - \text{Maintenance Capex}}{\text{Productive Acres}}$$

Per-acre earning power of the productive asset, after the non-recurring items are
taken back out and after replacing what wore out is paid for. The number that
says whether the operation can pay its owners **and** stay whole.

## Why not headline operating cash flow

Headline OCF lies in two directions at once. It is **flattered** by money that
came in and will not come in again — an insurance recovery, a litigation
settlement, a gain on selling a tractor that landed in the operating section —
and it is flattered **again** by maintenance that was not done. A farm running
its irrigation to failure to make a year look good is destroying the thing the
year was earned with, and the headline number goes *up* while it happens.

Both corrections are judgements. That is why this is a doctype and a workflow
rather than a formula.

## The components, and where each one comes from

| Component | Source | The rule |
| --- | --- | --- |
| Raw OCF | `GL Entry`, direct method | Cash and bank movement per **submitted** voucher, apportioned to operating / investing / financing by the accounts on the other side. A mixed voucher is split proportionally. |
| Normalization adjustments | `Normalization Adjustment`, status `Approved` only | Each carries a justification of at least forty characters and an approver's signature. Drafts, rejections and superseded rows do not count. |
| Maintenance capex | `Asset.capex_type` / `maintenance_portion`, purchased in the period | **Actual spend.** Never a percentage of revenue. Unclassified assets are excluded and reported. |
| Productive acres | `Field.productive_from_date` / `productive_through_date` | Time-weighted by days productive in the period, inclusive at both ends. Fallow and pre-yield acres are out, and counted separately. |

### Raw OCF is not ERPNext's Cash Flow report

It is computed here from GL Entry instead, for two reasons. The report needs the
Frappe report engine and a filter dict shaped to whatever version the site is
running; more importantly, a report's output cannot be traced back to rows, and
the whole argument of this KPI is that it has to be. `raw_ocf.computation_note`
states the method on the face of every result, and the investing and financing
sections come back beside the operating one so a reader can see the split rather
than take it on trust.

Transfers between two of the operation's own cash accounts fall out at zero
without a special case: the two cash rows net against each other inside the
voucher.

## Itemized output is the audit defence

`get_sustainable_cf_per_acre` never returns a bare figure. It returns every
approved adjustment with its justification and the name behind it, every
maintenance-capex asset with its purchase date and portion, and every productive
block with the days it was in service — and the KPI itself is the last key rather
than the only one.

This is not presentation. Every buyer, lender and auditor who reads a normalized
figure will test it **one add-back at a time**, and the FSMA-era habit this app
is built on applies here as much as it does to a spray record: a claim whose
evidence is one click away survives a question, and a claim whose evidence is
somewhere else does not. A normalized number nobody can inspect is
indistinguishable from an arranged one.

`computation_warnings` carries what a reader has to know before quoting the
figure — undated blocks, unclassified assets, a period with no approved
adjustments at all. Each is a sentence rather than a code, because the audience
is a person defending the number across a table.

## Approving a normalization adjustment

**AI proposes, human approves.** The two are separate tools with separate
switches.

1. `create_normalization_adjustment(company, fiscal_year, period_start,
   period_end, amount, direction, category, justification)` — creates a
   **Draft**. Nothing in this tool can make it count. `amount` is always
   positive; the sign lives in `direction`.
2. Somebody reads the justification and decides.
3. `approve_normalization_adjustment(name, approver_signature_file_token)` —
   status to `Approved`, signature attached, `approved_on` **written rather than
   taken as input**. There is no unsigned path.
   Or `reject_normalization_adjustment(name, rejection_reason)`, and the
   rejection is kept: a refusal with a reason teaches the next proposal.

Only one approved adjustment per company, period and category. A correction
**supersedes** rather than duplicates — point the old row's `superseded_by` at
the new one and set its status to `Superseded`, which leaves the trail of what
was believed before.

Finding a non-recurring item scattered through a ledger nobody reads line by line
is worth a great deal and is something a model is good at. Deciding that a
hailstorm in a region that hails every third year is non-recurring is a judgement
with a lender on the other end of it.

## Maintenance capex is actual spend, not a heuristic

The common shortcut is to model maintenance capex as some fraction of revenue. It
destroys the only interesting thing the metric measures: an operation that spent
nothing on replacement did not have a cheap year, it borrowed the year from the
orchard — and **under-investment is the signal**, not noise to be smoothed. A
percentage formula reports a well-maintained farm every time, including in the
years it matters.

So `create_asset` requires a `capex_type` (Maintenance / Growth / Mixed) at the
moment of purchase, when the person raising it knows why they are buying the
thing. Mixed splits across `maintenance_portion` and `growth_portion`, which must
sum to the invoice within a cent. Growth and Mixed additionally require a
`capex_justification`: classifying spend as growth *raises* sustainable cash
flow, which is the one direction a misclassification flatters the operation.

`backfill_asset_capex_type` classifies history in bulk — dry-run by default,
never overwriting an answer somebody gave, on the narrow heuristic that
everything bought before the operation started tracking is generally maintenance.
It is a starting position, and the result says so.

## Time-weighting the denominator

The denominator is what is **productive**, not what is owned. Fallow ground has
acreage, a cost centre and a water right and earns nothing; a perennial in its
pre-yield years is capital under construction wearing the costume of an orchard.
Both are excluded and counted separately, because pre-yield acres are next year's
denominator and a reader who cannot see them coming cannot read the trend.

```
overlap_start = max(productive_from_date, period_start)
overlap_end   = min(productive_through_date or period_end, period_end)
weight        = inclusive_days(overlap) / inclusive_days(period)
```

Inclusive at both ends, because `period_end` is an inclusive date everywhere else
in this app: 1 January to 31 March is ninety days.

A block with **no** `productive_from_date` is excluded and named in the warnings.
Assuming an undated block is productive puts acres in the denominator that may be
a three-year-old planting — which makes the figure look conservative while
quietly turning a data gap into a number somebody acts on.

## Roadmap

Phase 2 is the **Financial KPI Framework**, and it parallels the Configurable
Compliance Framework exactly. A `Financial KPI Definition` doctype carrying the
formula, its input references, its cadence and its threshold ranges makes
Sustainable CF/Acre the first data-defined metric, with DSCR, ROIC, EBITDA/acre
and labour cost per acre added as records rather than as releases. Same
AI-supportive-not-dictator shape: a model can propose a definition from a lender's
covenant or an earnings call, and a person enables it.

The quarterly chart ships as a core Frappe Report plus Dashboard Chart rather
than as a Frappe Insights view, so it works on every site this app supports. An
Insights view over the same service loses nothing by waiting.

## Related

- [`docs/compliance_fields.md`](compliance_fields.md) — the Asset capex columns
  and the Field productive dates, with the argument for grafting them on.
- [`docs/tool-catalog.md`](tool-catalog.md) — the six tools, in full.
- [`RELEASES/v0.19.5.md`](../RELEASES/v0.19.5.md) — the release this shipped in.
