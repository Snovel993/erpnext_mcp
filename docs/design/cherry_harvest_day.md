# The Cherry Harvest Day

**Status:** Draft for review
**Target:** v0.58.x – v0.62.x (five phases)
**Author:** design pass, 2026-08-10
**Operator:** Constancy Farms LLC — sweet cherry, Oregon and Washington blocks
**Supersedes:** nothing. Completes the shift register (v0.19.3), the weather
timeline (v0.19.4), the BucketLog bridge (v0.44.0) and the crew clock (v0.50.0)
into one workflow.

---

## 0. Why this document exists

Every part of a cherry harvest day already exists in this system, and none of
them are joined up into a day.

- A **Farm Shift** knows its crew, its foreman, its span, and each worker's own
  `joined_at` / `left_at`.
- A **Shift Weather Timeline** fills itself every fifteen minutes from
  Open-Meteo, computes the NWS heat index, and writes a `Threshold Crossed`
  compliance event when it passes OR-OSHA's engagement temperature.
- A **Bucket Log Entry** carries one bucket, one badge, one moment, and a binary
  verdict — and `entries_to_payroll_shape` already reshapes it into the rows the
  payroll engine reads.
- **`payroll_integration.py`** already segments a shift per worker, buckets
  segments into workweeks, splits overtime chronologically by state, and hands
  `payroll_calc` a `break_hours` figure so a piece-rate worker's rest can be paid
  at the average rate *Demetrio v. Sakuma* requires.
- The **iOS crew clock** scans badges onto a shift, and the **bucket capture**
  screen logs buckets in vision-gated or badge-only mode.

Four things are missing, and they are the whole of this document:

> **1. Nobody can log a break.** `Farm Shift Compliance Event` has no break
> types, no duration, and no employee. There is no mobile route to
> `log_shift_event` at all. `payroll_integration` reads `break_hours` off a
> shift dict — and **no column on Farm Shift or Farm Shift Crew Member holds
> one**, so `_load_period_shifts` cannot select it and it is `0.0` on every
> payroll run this system has ever produced. Piece-rate break compensation is
> implemented, tested, and has never been paid.

> **2. Nobody can clock out early.** `remove_worker_from_shift` is an MCP tool
> with no route in `farmops_api/routes.py` and no caller in Swift.
> `CrewClockSession.clockOut(badgeID:)` writes a local `ClockEvent` and nothing
> else. A picker who leaves at eleven is paid to the close of the shift.

> **3. The foreman cannot see production.** There is no read that answers "how
> many buckets has each picker logged today". `get_bucket_session` answers per
> BucketLog session; `get_piecework_summary` answers per period. Neither is the
> board a foreman wants at the bin trailer.

> **4. The server cannot tell a counted bucket from a checked one.**
> `BucketEntry.autoVerdict` distinguishes `full`, `manualOverride` and
> `manualTally` — and the model's own comment says *"It never crosses the
> wire."* `BadgeAPI.payload` sends `accepted` only. A bucket a model looked at
> and a bucket somebody tapped arrive as the same `verdict: "Accepted"`.

Everything below is those four, plus the compliance obligations that attach to
them, designed as one harvest day.

### 0.1 The two constraints that decide the shape

**Cherries are a three-week window.** Sweet cherry does not wait. A crew of
thirty picks from first light, the fruit goes to the packing house the same day,
and a foreman who has to open a form loses the crew. Every interaction in §2 is
one gesture or it does not ship.

**The compliance obligations are per-shift and per-worker at once.** OR-OSHA
-1131 asks what the *crew* did about the heat. BOLI and L&I ask what each
*worker* was owed in rest. The record has to answer both from one set of events,
which §1.2 is the argument for.

---

## 1. Architecture

### 1.1 The shift is the spine, and stays the spine

```
                       ┌──────────────────────────────┐
                       │        Farm Shift            │  ← the exposure period,
                       │  foreman · company · state   │    the crew envelope,
                       │  pay_type · pay_rate         │    the compliance anchor
                       └──────────────────────────────┘
                          │        │         │      │
       ┌──────────────────┘        │         │      └────────────────┐
       ▼                           ▼         ▼                       ▼
┌──────────────┐   ┌───────────────────────┐ ┌──────────────┐ ┌──────────────┐
│ Crew Member  │   │ Compliance Event      │ │ Weather      │ │ Shift        │
│ (child)      │   │ (child)               │ │ Reading      │ │ Location Log │
│ employee     │   │ event_type            │ │ (child)      │ │ (separate)   │
│ joined_at    │   │ event_datetime        │ │ temp_f       │ │ breadcrumbs  │
│ left_at      │   │ ended_at      ← NEW   │ │ heat_index_f │ └──────────────┘
│ pay_type     │   │ duration_minutes ←NEW │ │ every 15 min │
│ pay_rate     │   │ break_kind    ← NEW   │ └──────────────┘
└──────────────┘   │ applies_to    ← NEW   │        │
       │           │ employee      ← NEW   │        │
       │           └───────────────────────┘        │
       │                     │                      │
       │                     │            ┌─────────▼──────────┐
       │                     │            │ Compliance Rule    │
       │                     │            │ (data, not code)   │
       │                     │            └─────────┬──────────┘
       │                     │                      ▼
       │                     │            ┌────────────────────┐
       │                     │            │ Compliance Alert   │
       │                     │            │  + Farm Task       │
       │                     │            └────────────────────┘
       ▼                     ▼
┌──────────────────────────────────────┐
│  breaks.py  (NEW, pure functions)    │  entitlement × events → owed / taken /
│  Labor Break Policy (NEW doctype)    │  missed / paid-rest-hours / meal-hours
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐        ┌───────────────────────────┐
│  payroll_integration.aggregate_...   │◄───────│  Bucket Log Entry         │
│  segments · workweeks · overtime     │        │  one bucket · one badge   │
│  break_hours · unpaid_break_hours    │        │  shift · status           │
└──────────────────┬───────────────────┘        └───────────────────────────┘
                   ▼                                        ▲
┌──────────────────────────────────────┐                    │
│  payroll_calc.calculate_full_payroll │        ┌───────────────────────────┐
│  piece × rate + break_pay + OT + min │        │  Bucket Log Session       │
└──────────────────┬───────────────────┘        │  totals, live-computed    │
                   ▼                            └───────────────────────────┘
          Farm Payroll Slip → GL
```

### 1.2 The one decision that makes the rest fall out

**A break is a crew-wide event, and per-worker break hours are derived from the
overlap of that event with each worker's own segment.**

The alternative — a `Farm Shift Worker Break` child table, one row per worker per
break — is thirty rows per break and a hundred and twenty rows a day for a crew
of thirty. It also asks the foreman to record something they did not do: they
called *one* break, for the crew, at 9:40.

The overlap rule gets both answers from one event:

| Fact | How it is answered |
|---|---|
| "What did the operation do about the heat?" | The event list. Four cool-downs, timestamped, with the weather snapshot on each. |
| "Was Ana owed a rest period she did not get?" | Her segment is 06:00–11:00 → five hours → two rest periods owed under OR. Two crew rest events overlap her segment. Owed 2, taken 2. |
| "How much paid rest does Luis get on his cheque?" | Sum of `duration_minutes` for `break_kind = Paid Rest` events whose window intersects his segment. |
| "Did the crew get their meal period?" | The `Unpaid Meal` event, its start, its 30 minutes, and which segments it fell inside. |

**A worker who was not there does not get the break.** Somebody who clocked out
at 11:00 does not accrue the 13:30 rest period, and does not have the 12:00 meal
subtracted from their day. This falls straight out of intersecting the event
window with `[joined_at, left_at]` and needs no special case.

**The individual case is not lost.** `applies_to = Individual` with an
`employee` set covers the picker who takes their meal late because they were
finishing a row. It is the exception; the crew event is the rule.

**This obeys the house rule on table sprawl** (`CLAUDE.md`: *Maximal Data
Science, avoid table sprawl*). Zero new child tables. Five new columns on a
child table that already exists, and one new small doctype for the policy —
which is §1.3.

### 1.3 The break schedule is data, for the same reason every other threshold is

`docs/configurable_compliance_framework.md` §1 makes the argument already:
regulations do not move on a release cadence, and a threshold that lives in code
is a threshold that needs a deploy. Oregon renumbered heat illness from -1130 to
-1131 mid-stream. Washington's ag heat rule became permanent in 2023 and was
amended in 2024.

So the break schedule is a **`Labor Break Policy`** record: a state, an
effective date, and a child table of schedule rows. Not a Python dict.

```
Labor Break Policy
  policy_id            Data        "OR-2026" / "WA-2026"
  work_state           Select      OR / WA
  effective_from       Date
  effective_to         Date        (blank = current)
  enabled              Check
  regulation_citations Small Text
  human_approved_by    Link User   ← the same provenance rail Compliance Rule uses
  human_approved_on    Datetime
  notes                Text

  rest_schedule        Table → Labor Break Schedule Row
  meal_schedule        Table → Labor Break Schedule Row
  heat_schedule        Table → Labor Heat Break Row
```

```
Labor Break Schedule Row          Labor Heat Break Row
  hours_from    Float               heat_index_from  Float
  hours_to      Float               heat_index_to    Float
  periods_owed  Int                 minutes_each     Int
  minutes_each  Int                 every_hours      Float
  paid          Check               concurrent_with_rest  Check
```

**Seeded, not invented.** The seed below is the author's reading and is
`human_approved_by`-blank until somebody at Constancy Farms reads each citation
against the rule and approves it. That gate is not ceremony — it is the same one
`approve_compliance_rule` enforces, and it is what makes an alert defensible when
BOLI asks where the number came from.

**Oregon — OAR 839-020-0050, ORS 653.261**

| Shift length | Paid rest ×10 min | Unpaid meal ×30 min |
|---|---|---|
| < 2:00 | 0 | 0 |
| 2:00 – 5:59 | 1 | 0 |
| 6:00 – 9:59 | 2 | 1 |
| 10:00 – 13:59 | 3 | 1 |
| 14:00 – 17:59 | 4 | 2 |

Rest periods are taken approximately in the middle of each four-hour work
segment. The meal period falls between the second and fifth hour on a shift of
seven hours or less, and between the third and sixth on a longer one.

**Washington — WAC 296-131-020 (agricultural employees)**

| Shift length | Paid rest ×10 min | Unpaid meal ×30 min |
|---|---|---|
| < 3:00 | 0 | 0 |
| 3:00 – 4:59 | 1 | 0 |
| 5:00 – 10:59 | at least 10 min per 4 hours worked | 1 |
| 11:00 + | as above | 2 |

Washington adds a hard ceiling the Oregon rule does not: **no employee shall work
more than three hours without a rest period.** That is a *timing* obligation, not
a *count* obligation, and §4.2 is where it becomes an alert the foreman can act
on before it is breached rather than after.

**Heat — OAR 437-004-1131 (OR) and WAC 296-307-097 (WA)**

| Heat index | Preventative cool-down | Notes |
|---|---|---|
| ≥ 80 °F | rule engages: water, shade, training, observation | already the trigger for `shift_heat_threshold_crossed` |
| ≥ 90 °F | 10 min per 2 hours, paid | may run concurrently with the ordinary rest period |
| ≥ 100 °F | 15 min per hour, paid | |

**Cool-down breaks are `Paid Rest` for payroll and `Cool-Down` for compliance,
and they are the same minutes.** A cool-down that runs concurrently with a
scheduled rest period satisfies both and is counted once —
`concurrent_with_rest` on the schedule row is what says so. Double-counting them
would inflate `break_hours` and overpay; counting the cool-down as unpaid would
be a wage violation on top of a heat one.

### 1.4 What runs where, and why the handset does arithmetic

The compliance alert sweep is **hourly** (`hooks.py: scheduler_events["hourly"]`).
That is the right cadence for an I-9 that is three days overdue and useless for
*"Washington says nobody works past three hours without a rest and this crew is
at 2:47."*

So break timing is computed **on the handset**, from the same `Labor Break
Policy` the server computes from, fetched at shift start and cached:

| Concern | Where | Why |
|---|---|---|
| "Rest period due in 13 minutes" | iOS, local clock | Must be right now, offline, in a block with no signal. |
| "This shift's meal window closes at 11:32" | iOS, local clock | Same. |
| "Heat index crossed 90 — cool-downs are now every 2 hours" | Server writes the reading; iOS reads it on next sync and changes its own countdown | The reading is a server fact; the countdown is a field fact. |
| "This closed shift's crew was owed 60 rest-minutes and took 40" | Server, at close and at sweep | It is the record, not the reminder. |
| "Ana's cheque owes her 0.33h of paid rest" | Server, `payroll_integration` | Payroll is never computed on a phone. |

**The handset never decides compliance; it decides when to nudge.** The record
is written by the foreman tapping the break, and every figure that reaches a
payroll slip or an audit packet is recomputed server-side from the events. A
phone whose clock is wrong produces a nudge at the wrong minute and an event with
a timestamp the server keeps as given — the same posture `log_shift_event`
already takes, and it already says so in its `timing_note`.

---

## 2. The iOS harvest day, screen by screen

The whole day lives in one tab. Today the crew clock (`Features/Clock`) and the
bucket capture (`Features/Capture`) are separate flows that do not know about
each other except through `BucketSyncEngine.shift`. They become one
**Harvest Day** flow with a shared session.

```
ShiftSetupView ──► CrewBuildView ──► HarvestDayView ──► ShiftCloseView ──► SummaryView
   (§2.1)            (§2.2)          (§2.3, all day)      (§2.6)           (§2.7)
                                     │
                                     ├─► BreakSheet (§2.4)
                                     ├─► WorkerSheet (§2.5) → individual clock-out
                                     └─► ProductionBoard (§2.3.2)
```

### 2.1 Shift setup — one screen, four facts, ten seconds

Existing `ShiftSetupView` scans the foreman's badge. It grows three fields, all
pre-filled and all one tap to change:

| Field | Default | Why it must be here |
|---|---|---|
| Block / location | last block used on this handset | Traceability on every bucket, and the GPS the weather timeline needs. |
| Work state | from the block, else the company's | **Decides which break policy applies.** An OR crew and a WA crew do not owe the same rest. |
| Piece rate | from the active Salary Structure | The number the picker will ask about at 7am. Shown, not editable. |

Then: **Start Harvest Day.** `start_shift` with `shift_type: "Harvest"`, GPS
attached. Fails soft exactly as it does today — the day opens on the handset and
the roster queues.

**New on this screen:** the break policy for the chosen state is fetched and
cached (§3.1). If it cannot be fetched and none is cached, the day still starts
and the break coach (§2.3.1) shows *"Break schedule unavailable — log breaks as
you take them"* rather than a countdown it cannot compute.

### 2.2 Crew build — the scan loop that already works

`ShiftScanningView` is close to right. Keep the loop, change the feedback.

Today a scan shows the resolved name in a strip. It should show a **card that
stays** — a growing grid of the crew, each tile a name, a photo from
`Employee.image`, and a badge number. Thirty tiles is the crew, and the foreman
can see at a glance who is missing without counting a list.

**Three refusals get their own treatment**, because at 5:40am in a dark orchard
a red sentence is not enough:

| Refusal | Today | Should be |
|---|---|---|
| Badge not in register | red text | Card with **Issue a badge** → `generate_employee_badge_qr` inline. A picker standing there without a card is a hire-day problem, not a scanning problem. |
| Employee status not Active | red text | Card with the person's name and **Rehire** → `reactivate_employee`. |
| No I-9 / expired work authorization | *not surfaced at all* | Amber card. See §4.4 — this is a gate the system currently does not apply at the clock. |

**Crew build never blocks the day.** Any of those three can be dismissed with
*"Add anyway"*, which rosters the worker and raises the corresponding compliance
alert. A foreman who cannot clock somebody in will clock them in on paper, and
then the record is gone.

### 2.3 Harvest day — the screen the foreman lives on

One screen, all day. Three zones, top to bottom.

```
┌──────────────────────────────────────────────┐
│  ⏱ 6:42  ·  ☀ 84 °F / HI 88 °F  ·  28 on crew │  status bar
│  ▓▓▓▓▓▓▓▓░░░░  Rest period due in 18 min      │  ← break coach (§2.3.1)
├──────────────────────────────────────────────┤
│                                              │
│            [ CAMERA / SCAN RETICLE ]         │  ← bucket loop (§2.3.3)
│                                              │
│        Last: Ana Ruiz  ·  bucket 41          │
├──────────────────────────────────────────────┤
│  Ana Ruiz      41  [+1]     Luis Mora  38 [+1]│  ← production board (§2.3.2)
│  Jorge P.      37  [+1]     Marta S.   36 [+1]│
│  …                                           │
├──────────────────────────────────────────────┤
│   [ Break ]      [ Crew ]      [ End day ]   │
└──────────────────────────────────────────────┘
```

#### 2.3.1 The break coach

A progress bar and one sentence. It is the single most valuable pixel on the
screen, because a missed meal period in Oregon is a wage claim and the foreman
has no way to know they are approaching one.

States, in priority order:

| State | Bar | Sentence |
|---|---|---|
| WA, 2:30 since last rest | amber | "Rest period within 30 minutes — WA allows no more than 3 hours." |
| Meal window opens | blue | "Meal period window is open (until 11:32)." |
| Meal window closing | red | "Meal period must start within 12 minutes." |
| Heat index ≥ 90 | orange | "Cool-down due — 10 min every 2 hours at this heat index." |
| Nothing due | grey | "Next rest period around 9:40." |
| No policy cached | grey | "Log breaks as you take them." |

**Tapping the bar opens the break sheet with the due break pre-selected.** One
tap from nudge to record.

#### 2.3.2 The production board

The answer to *"real-time sync so the foreman sees production as it happens"*.

Per-picker bucket count for **today, on this shift**, sorted by count descending
so the board is also a picking-rate read. Each row carries a `+1` — which is the
tally path (§2.3.3) for the picker whose next four buckets go in without a
re-scan.

The count is **local-first and server-reconciled**: the phone's own count of
queued-plus-synced entries, corrected by `get_shift_production` (§3.2) on every
successful sync. A number that only updated when the network did would be wrong
in exactly the place a foreman would notice.

A picker whose count is behind the server's — because another handset logged for
them — reconciles up. A picker whose local queue has entries the server rejected
shows an amber dot and the reason on tap.

#### 2.3.3 The bucket loop

Two modes, already decided by `BucketCaptureMode.resolved(...)`, and the design
does not change that decision. What changes is what crosses the wire.

**Vision-gated** — camera over the bucket, model votes, badge scan, entry.
**Tally** — badge scan or `+1`, entry.

| | vision-gated | tally |
|---|---|---|
| gesture | point, then scan | scan, or tap `+1` |
| `verdict` | model's | `Accepted` |
| `coverage_percent` | model's | absent |
| `capture_mode` **(new)** | `ML Verified` | `Badge Only` |
| `auto_verdict` **(new)** | `full` / `not_full` / `manual_override` | `manual_tally` |

**`capture_mode` and `auto_verdict` must cross the wire, and §5.2 is the
argument.** In one line: a farm that pays for thirty thousand buckets a season
should be able to answer "how many of those did a model look at" and today it
cannot.

**Sync is immediate, not batched** — §3.4.

#### 2.3.4 What is deliberately not on this screen

No task list, no compliance tab, no alert badge. The foreman's alerts are
delivered as the break coach and nowhere else during the pick. Everything else
waits for the close.

### 2.4 The break sheet — one gesture, and a second one to end it

```
┌────────────────────────────────┐
│  Break                    ✕    │
│                                │
│   ○ Rest period    10 min  PAID│  ← pre-selected if due
│   ○ Meal period    30 min UNPAID│
│   ○ Cool-down      10 min  PAID│  ← only shown when HI ≥ 90
│   ○ Water break                │  ← no duration; an observation
│   ○ Shade break                │
│                                │
│   Who:  ● Whole crew (28)      │
│         ○ One worker…          │
│                                │
│      [ Start break now ]       │
└────────────────────────────────┘
```

Tapping **Start break now** posts `log_shift_break` (§3.3) immediately with
`event_datetime = now` and the scheduled duration. The screen returns to the
harvest day with the bar replaced by a running break timer and one button:
**End break**.

**Why start-and-end rather than a single "we took a 10-minute break" button.**
OAR 437-004-1131(h) and its Washington equivalent turn on rest *taken*, not rest
*offered*, and a piece-rate crew declining a break to keep picking is precisely
the failure the requirement exists for — `shifts.heat_gaps` already says so in
those words. A break with a start and an end is a claim about what happened. A
break with a duration typed at 4pm is a claim about what was intended.

**Ending is not required.** Tapping End writes `ended_at` and the true duration.
Not tapping it leaves `duration_minutes` as the scheduled figure and sets
`duration_source = Scheduled` — honest, usable for payroll, and visibly weaker
evidence than `duration_source = Observed`. The close (§2.6) shows how many of
the day's breaks were scheduled rather than observed.

**Water and shade breaks have no duration** and stay exactly what they are
today: `Water Break` / `Shade Break` events with a timestamp and a weather
snapshot. They are -1131 evidence, not payroll input.

### 2.5 The worker sheet — individual clock-out

Long-press a picker on the production board, or scan their badge in Crew mode.

```
┌────────────────────────────────┐
│  Ana Ruiz            CF-0014   │
│  On since 05:58 · 5h 12m       │
│  41 buckets · $61.50 at $1.50  │
│                                │
│  Breaks: 2 rest · 1 meal       │
│  Owed:   2 rest · 1 meal   ✓   │
│                                │
│  [ Clock out ]   [ Log bucket ]│
└────────────────────────────────┘
```

**Clock out** posts `remove_worker_from_shift` (§3.5) with `left_at = now`. The
tile leaves the board, the roster count drops, and the break coach recomputes —
because a crew of 27 has the same obligations but Ana no longer accrues them.

The **Owed / taken** line is the per-worker break reconciliation computed
locally from the same overlap rule the server uses (§1.2), and it is the reason
this sheet exists at all: it is the last moment anyone can fix a missed rest
period, and it is showing it to the one person who can.

If the owed and taken figures disagree, the clock-out button carries a confirm:
*"Ana was owed 2 rest periods and 1 has been logged. Clock out anyway?"* —
refusing would be worse. She is leaving either way.

### 2.6 Shift close — what the foreman signs

`ShiftEndView` exists and collects a signature. It grows a **review sheet above
the signature pad**, and that ordering is the whole point: a signature under a
blank screen attests to nothing.

```
Harvest day · SHIFT-2026-0184 · Block 7 · OR
06:00 – 15:20 · 9h 20m · 28 workers · 1,043 buckets

WEATHER          peak 96 °F, heat index 101 °F at 14:15
                 threshold crossed 11:45

BREAKS LOGGED    3 rest (crew) · 1 meal (crew) · 4 cool-down (crew)
                 2 individual meal periods

⚠ 2 WORKERS SHORT OF THEIR REST ENTITLEMENT
   Jorge Perez  owed 3, logged 2
   Marta Salas  owed 3, logged 2
   Both joined at 05:50 and left at 15:20.

⚠ HEAT EXPOSURE EVENT NOT FILED
   The heat index passed 100 °F. -1131 asks who was exposed, whether the
   rest cycle was taken, and whether anybody showed signs.
   [ File it now ]

BUCKETS          1,043 accepted · 0 rejected
                 1,043 Badge Only · 0 ML Verified
                 all synced

[ Foreman notes……………………………………………… ]

        ✍ Supervisor review signature
        [ Close the harvest day ]
```

**Every warning is reported and none of them refuses the close.** This is
`shifts.heat_gaps`' posture, restated: a shift where the shade trailer broke and
the crew went home at eleven is a shift with a real gap, and a system that would
not let the gap be recorded produces a false record or no record. The honest one
is worth more under investigation, and it is also the one that tells somebody
what to fix.

**The heat exposure event is offered, never written automatically.**
`services/weather.py` argues this at length and the argument holds: that record
says which crew was exposed, whether water was provided at the required rate,
whether the rest cycle was *taken* rather than offered, whether anybody showed
signs, and what was done — five judgements about people, made by the person who
was there. A machine filing one from a temperature reading produces a document
with nobody behind it, in the exact place where having nobody behind it is the
failure.

### 2.7 Summary

Unchanged in shape from today's `ShiftSummaryView`, plus: what synced, what did
not, and a **Retry** for anything the farm has not accepted. A foreman who
drives out of signal at 15:30 needs to know at 16:00 whether the day landed.

---

## 3. Server endpoints

### 3.0 Everything below is a two-file change, and then a third

Every new route is (a) a wrapper in `erpnext_mcp/api/mobile.py` and (b) a line in
`erpnext_mcp/farmops_api/routes.py`. `test_farmops_api.py` asserts the table
against the module in both directions, so neither can be forgotten.

**And then somebody must mount it on the funnel.** The Tailscale funnel
publishes `/farmops/api/...` with `tailscale funnel --set-path`, one line per
path. A route that is not mounted is a plain-text 404 with no access log, no
audit row and no traceback, which the app renders as its generic miss. Six routes
shipped unmounted across v0.54.0–v0.57.0. **Every phase in §7 ends with
`sudo sh scripts/mount_farmops_funnel.sh` on the Umbrel and a
`validate_public_endpoint(probe_routes=true)`, and that step needs Tim.**

### 3.1 `get_break_policy(company=None, work_state=None)` — NEW, read

The schedule the handset computes its countdown from.

```json
{
  "policy": "OR-2026",
  "work_state": "OR",
  "effective_from": "2026-01-01",
  "approved_by": "tim@constancyfarms.com",
  "regulation_citations": "OAR 839-020-0050; ORS 653.261",
  "rest_schedule": [
    {"hours_from": 2.0,  "hours_to": 5.99,  "periods_owed": 1, "minutes_each": 10, "paid": true},
    {"hours_from": 6.0,  "hours_to": 9.99,  "periods_owed": 2, "minutes_each": 10, "paid": true},
    {"hours_from": 10.0, "hours_to": 13.99, "periods_owed": 3, "minutes_each": 10, "paid": true}
  ],
  "meal_schedule": [
    {"hours_from": 6.0, "hours_to": 13.99, "periods_owed": 1, "minutes_each": 30, "paid": false,
     "window_starts_after_hours": 2.0, "window_ends_after_hours": 5.0}
  ],
  "heat_schedule": [
    {"heat_index_from": 90.0,  "heat_index_to": 99.99, "minutes_each": 10, "every_hours": 2.0,
     "concurrent_with_rest": true},
    {"heat_index_from": 100.0, "heat_index_to": 200.0, "minutes_each": 15, "every_hours": 1.0,
     "concurrent_with_rest": true}
  ],
  "max_hours_without_rest": null
}
```

`max_hours_without_rest` is `3.0` on the WA policy and `null` on the OR one.
Nullable rather than a large sentinel, so a client that ignores it produces no
countdown rather than a wrong one.

**A policy with no `approved_by` is returned with `"approved": false` and is
still returned.** The handset shows the coach with a caveat. Withholding the
schedule until somebody signs it would mean no coach at all in the first season,
which is worse than a coach whose provenance is visible.

### 3.2 `get_shift_production(shift)` — NEW, read

The production board.

```json
{
  "shift": "SHIFT-2026-0184",
  "as_of": "2026-07-14 10:41:02",
  "crew_size": 28, "still_on_shift": 27,
  "total_accepted": 612, "total_rejected": 0,
  "workers": [
    {"employee": "HR-EMP-00031", "employee_name": "Ana Ruiz", "badge_id": "CF-0014",
     "joined_at": "2026-07-14 05:58:00", "left_at": null,
     "buckets_accepted": 41, "buckets_rejected": 0,
     "capture_modes": {"ML Verified": 0, "Badge Only": 41},
     "hours_present": 4.72,
     "rest_periods_owed": 2, "rest_periods_taken": 2,
     "meal_periods_owed": 1, "meal_periods_taken": 1,
     "paid_break_minutes": 20, "unpaid_break_minutes": 30}
  ],
  "unattributed_entries": 0
}
```

**READ_LIMIT, and the handset polls it on every successful bucket sync rather
than on a timer.** A board that refreshed every thirty seconds would burn a
day's rate limit on a crew that logs a bucket every four seconds; a board that
refreshes when something changed is both cheaper and fresher.

`unattributed_entries` is the count of this shift's entries with no resolved
employee. It should always be zero — `sync_bucket_entries` runs
`badge_policy: strict` from a phone — and if it is not, something imported.

### 3.3 `log_shift_break(shift, break_kind, ...)` — NEW, write

The break sheet's post. A thin, opinionated wrapper over the existing
`shifts.log_shift_event`, and it is a separate method rather than a route onto
`log_shift_event` for two reasons:

1. `log_shift_event` takes `producer_record_doctype` and `producer_record_name`
   — a pair a phone has no business setting, and `routes.bind` drops what a
   signature does not name. A break method that does not declare them cannot be
   made to write them.
2. The break needs `duration_minutes`, `break_kind`, `applies_to` and `employee`
   validated *together* — an `Individual` break with no employee, or a `Paid
   Rest` with a 90-minute duration, are both refusable at the door.

```
log_shift_break(
    shift,                    # required, scoped
    break_kind,               # Paid Rest | Unpaid Meal | Cool-Down | Water | Shade
    started_at = None,        # defaults to now in the tool; the phone sends its own
    duration_minutes = None,  # from the policy; null for Water/Shade
    applies_to = "Crew",      # Crew | Individual
    employee = None,          # required when Individual
    description = None,
)  →  the break event, plus the shift's live break tally
```

Returns the whole `describe(with_children=True)` shift, as every shift tool
does, plus:

```json
{"logged": {"break_kind": "Paid Rest", "started_at": "…", "duration_minutes": 10,
            "applies_to": "Crew", "covers_workers": 27},
 "breaks_today": {"Paid Rest": 2, "Unpaid Meal": 1, "Cool-Down": 3},
 "note": "27 of the 28 workers on this crew are on shift at 09:40 and this rest
          period counts toward their entitlement. Marta Salas left at 09:12 and
          does not accrue it."}
```

`covers_workers` is the overlap count, computed and reported at write time so the
foreman's screen can say something true without a second call.

### 3.4 `end_shift_break(shift, event, ended_at=None)` — NEW, write

Writes `ended_at` and the observed `duration_minutes`, and flips
`duration_source` to `Observed`. Refuses an `ended_at` before the start with the
sentence naming both times; accepts one after the shift's own end with a note,
because a crew that took their last break as the bins were loaded is an ordinary
day and refusing loses the record.

### 3.5 `clock_out_worker(shift, employee, left_at=None, notes=None)` — NEW route

**The tool already exists and is already correct.** `remove_worker_from_shift`
sets `left_at` rather than deleting the row, refuses a second call that would
silently lengthen a day already ended, and returns the hours present. It has no
route.

The mobile wrapper adds exactly one thing over the tool: it **cannot name a
different company's employee** — `_employee_argument(employee, allowed)`, the
same guard `add_worker_to_shift` uses.

Named `clock_out_worker` on this surface rather than `remove_worker_from_shift`,
because the phone's verb is the operational one and the tool's verb is the
storage one, and `shifts.py` already says those are allowed to differ.

### 3.6 `sync_bucket_entries` — CHANGED

**No signature change. Two behavioural changes, both on the client.**

Today `BucketSyncEngine` drains on a 60-second `Timer`
(`FarmOpsConfig.autoSyncInterval`). Tim's requirement is immediate sync, and the
right change is:

1. **Fire on enqueue.** `BucketEntryQueue.didChangeNotification` already exists
   and already drives `refreshCounts()`. It also kicks `sync()`, debounced to
   ~750 ms so a picker unloading four buckets in a row produces one call and not
   four.
2. **Keep the timer as the floor, at 60 s.** It is what drains the morning that
   was captured out of signal. Immediate sync is an optimisation on top of a
   durable queue, never a replacement for it — a design where the entry only
   exists if the POST succeeded is a design that loses a bucket every time
   somebody walks behind a hill.

Server-side, one addition: the response gains `production` — the same shape as
`get_shift_production` — when `shift` was passed. **The board updates from the
sync's own answer and needs no second round trip**, which on a handset in a
canyon is the difference between a live board and a stale one.

### 3.7 `get_shift(shift)` — CHANGED

Grows `break_summary` in `describe(with_children=True)`:

```json
"break_summary": {
  "policy": "OR-2026", "policy_approved": true,
  "crew_totals": {"paid_rest_minutes": 30, "unpaid_meal_minutes": 30,
                  "cool_down_minutes": 40},
  "workers_short": [
    {"employee": "HR-EMP-00019", "employee_name": "Jorge Perez",
     "rest_owed": 3, "rest_taken": 2, "meal_owed": 1, "meal_taken": 1,
     "shortfall_minutes": 10}
  ],
  "breaks_scheduled_not_observed": 2
}
```

This is what the close screen renders and what the audit packet reads. It is a
**computed** field and is never stored: storing it would let the summary and the
events disagree the moment somebody corrects a timestamp.

### 3.8 Endpoint summary

| Method | Route | New? | Mutating | Purpose |
|---|---|---|---|---|
| `get_break_policy` | `/mobile/get_break_policy` | NEW | no | the schedule the coach counts from |
| `get_shift_production` | `/mobile/get_shift_production` | NEW | no | the production board |
| `log_shift_break` | `/mobile/log_shift_break` | NEW | yes | start a break |
| `end_shift_break` | `/mobile/end_shift_break` | NEW | yes | end one |
| `clock_out_worker` | `/mobile/clock_out_worker` | NEW route, existing tool | yes | individual clock-out |
| `sync_bucket_entries` | existing | — | yes | + `capture_mode`, + `production` in response |
| `start_shift` | existing | — | yes | + `work_state` argument |
| `get_shift` | *no mobile route today* | NEW route, existing tool | no | the close screen's read |
| `end_shift` | existing | — | yes | unchanged |

Nine routes; five genuinely new methods.

---

## 4. Compliance rule hooks

### 4.1 What fires today

One rule touches a shift: **`shift_heat_threshold_crossed`** — declarative,
fires on a weather fact rather than a date, raises a Farm Task assigned to
`row.foreman` to document the water, shade and rest cycle, and silences itself
when the shift closes. It is the framework's own proof that a new obligation can
be absorbed as data. It stays exactly as it is.

The 15-minute weather sweep also writes a `Threshold Crossed` compliance event
directly onto the shift. That is arithmetic about a reading, not a compliance
judgement, and `services/weather.py` draws that line explicitly.

### 4.2 What this design adds — five rules, all declarative

Each is a `Compliance Rule` record, seeded `enabled = 0` and
`human_approved_by`-blank, exactly as `propose_compliance_rule` requires.

| `rule_id` | Fires on | Severity | Producer |
|---|---|---|---|
| `shift_rest_period_overdue` | open shift, `now − last Paid Rest > max_hours_without_rest` for the state | Critical | Farm Task → foreman, "Call a rest period" |
| `shift_meal_period_window_closing` | open shift, meal window ends within 15 min, no `Unpaid Meal` event | Critical | Farm Task → foreman |
| `shift_closed_with_break_shortfall` | closed shift, any worker's taken < owed | Warning | Farm Task → foreman, evidence contract `{findings_text, signature}` |
| `shift_high_heat_no_cooldown` | open shift, latest heat index ≥ 90, no `Cool-Down` in the last `every_hours` | Critical | Farm Task → foreman |
| `piece_rate_break_pay_unpaid` | submitted payroll slip, `pay_type = Piece Rate`, `break_hours > 0`, `break_pay = 0` | Critical | Farm Task → payroll |

**The first, second and fourth cannot wait for the hourly sweep**, which is
§1.4's whole argument. They exist as rules so that the *record* is written and
so that an unattended shift — a foreman whose phone died — still produces an
alert somebody sees. The *nudge* is the handset's break coach. Two mechanisms,
one policy, and the policy is the same `Labor Break Policy` row.

**The third is the one that matters most in a wage claim.** It fires after the
close, on a closed record, and it is the only one whose Farm Task carries a
signature in its evidence contract: the foreman explains, on the record, why
Jorge got two rest periods instead of three. "Sent home early at his request" and
"we were behind on the bins" are different facts and an investigator is entitled
to which one it was.

**The fifth is a payroll rule, not a field rule**, and it exists because of §0's
finding: break compensation is implemented and has never fired. A rule that
watches for `break_hours > 0 ∧ break_pay = 0` is the regression test that
survives a refactor.

### 4.3 The vocabulary these need

Four of the five are expressible in today's declarative vocabulary. The gap:

- `shift_rest_period_overdue` and `shift_high_heat_no_cooldown` ask *"is there a
  child row of this type within the last N hours"* — an **absence over a
  window**. `latest_child_field_threshold_json` (v0.22.5) finds the latest child
  and tests its fields; it cannot ask whether the latest child is *old enough to
  be a problem*.

**Proposed primitive: `child_recency_json`.** One new field, same shape family as
`latest_child_field_threshold_json`:

```json
{"child_doctype": "Farm Shift Compliance Event",
 "parentfield": "compliance_events",
 "filters": [{"field": "break_kind", "op": "eq", "value": "Paid Rest"}],
 "timestamp_field": "event_datetime",
 "max_age_hours": 3.0,
 "max_age_source": "break.max_hours_without_rest",
 "context_key": "last_rest",
 "on_absent": "fire"}
```

`max_age_source` follows the exact pattern `threshold_source` already
establishes for `weather.heat_threshold_temp_f` — the number lives on the policy
record and the rule points at it, so changing the WA ceiling changes one row.

`on_absent: "fire"` is the case with no child at all, and it must be explicit:
a shift with no rest period logged all day is the worst case and the one a naive
"latest child" join returns nothing for.

**This is the framework working as designed.** §2 of
`configurable_compliance_framework.md`: *"The right response to reaching for
`custom_python` is to say what shape of question the rule asks, and turn that
shape into a field."* The shape is *absence over a window*. One field, and the
split stays 17 declarative / 2 built-in-permanent / 0 `custom_python`.

### 4.4 The gate that does not exist: work authorization at the clock

Noted in the 2026-08-07 cross-system review as **H9 — nothing in erpnext_mcp
gates work on I-9 status**. It belongs in this design because the crew clock is
where it would apply.

`shifts.start_shift` already calls `_i9_unverified(employees)` and reports the
crew members without a verified I-9 in its answer. `add_worker_to_shift` does
not.

**Proposed: `add_worker_to_shift` reports the same fact, and refuses nothing.**
The refusal belongs at hiring, not at 5:40am in an orchard — a foreman who cannot
clock somebody in clocks them in on paper. What it does is (a) return
`work_authorization: {status, expires_on, i9_state}` on the roster answer, which
is what §2.2's amber card renders, and (b) raise `i9_verification_overdue`,
which already exists.

---

## 5. Data model changes

### 5.1 `Farm Shift Compliance Event` — five new fields

| Field | Type | Options / default | Why |
|---|---|---|---|
| `break_kind` | Select | *(blank)* / Paid Rest / Unpaid Meal / Cool-Down | The payroll classification. Blank on every non-break event, which is every event that exists today. |
| `ended_at` | Datetime | | Rest *taken*, not offered. |
| `duration_minutes` | Float | | What payroll sums. |
| `duration_source` | Select | Scheduled / Observed | Weaker evidence, visibly weaker. |
| `applies_to` | Select | Crew (default) / Individual | §1.2. |
| `employee` | Link Employee | | Required when Individual; ignored otherwise. |

`event_type` gains **`Rest Period`** and **`Meal Period`** alongside the existing
nine. `Cool-Down` already exists and keeps its meaning.

**Every existing row stays valid.** A `Water Break` event written last season has
a blank `break_kind`, contributes nothing to `break_hours`, and reads exactly as
it always did.

### 5.2 `Bucket Log Entry` — two new fields

| Field | Type | Options | Why |
|---|---|---|---|
| `capture_mode` | Select | ML Verified / Badge Only / Foreman Override | §0 finding 4. |
| `auto_verdict` | Data | `full` / `not_full` / `manual_override` / `manual_tally` / `timeout` | The phone's own reason, preserved. |

**Neither is an input to pay, and the enforcement is structural.**
`entries_to_payroll_shape` builds its output row **key by key** — `employee` and
`entry_uuid`, nothing else — precisely so that a new column cannot leak into
`payroll_integration._row_units` and turn a bucket into a fraction of one.
`test_bucket_bridge.TheGateIsBinary` checks the emitted keys against
`payroll_integration._UNIT_KEYS` itself. Adding these two columns changes neither
file, and that is the test.

**What they buy:** `get_shift_production` and `get_piecework_summary` can report
the split, `reconcile_bucket_payroll` can flag a day that is 100 % `Badge Only`
on a farm that believes it deployed a model, and an audit can answer "how many of
this season's buckets did a model look at" — a question a cherry operation
buying a vision pipeline is entitled to ask of it.

**Client-side this is one line.** `BucketEntry.autoVerdict` already exists with
exactly these cases and already distinguishes `manualTally` from
`manualOverride`. `BadgeAPI.payload` stops dropping it.

### 5.3 `Labor Break Policy` + two child rows — NEW

§1.3. One parent, two child doctypes, one seeded row per state.

### 5.4 `Farm Shift` — one new field, and the one that is missing

| Field | Type | Why |
|---|---|---|
| `break_policy` | Link Labor Break Policy | Which schedule this shift was judged against, **snapshotted at start**. A policy amended in September must not retroactively make a July shift non-compliant. |

**`break_hours` and `unpaid_break_hours` are deliberately NOT added as columns.**
They are derived from the events by `breaks.py`, and a stored copy would let the
summary and the timeline disagree the moment anybody corrected a break's
timestamp. `_load_period_shifts` computes them per segment instead — §6.1.

### 5.5 `Farm Shift Crew Member` — no change

`joined_at`, `left_at`, `pay_type`, `pay_rate` are already exactly what the
overlap rule needs.

### 5.6 New module: `erpnext_mcp/breaks.py` — pure functions

Same contract as `bucket_bridge.py`, `payroll_integration.py` and `payroll_gl.py`:
no database, no side effects, everything arrives as a dict.

```python
entitlement(hours_worked, policy) -> {"rest_periods": 2, "rest_minutes": 20,
                                      "meal_periods": 1, "meal_minutes": 30}

heat_entitlement(hours_worked, weather_timeline, policy) -> {...}

overlap_minutes(event, segment_start, segment_end) -> float

worker_breaks(segment, events, policy) -> {"paid_break_hours": 0.33,
                                           "unpaid_break_hours": 0.5,
                                           "rest_taken": 2, "rest_owed": 2,
                                           "meal_taken": 1, "meal_owed": 1,
                                           "shortfall_minutes": 0}

crew_reconciliation(shift, crew, events, policy) -> {"workers_short": [...], ...}

next_break_due(now, segment_start, events, policy, heat_index) -> {...}
```

`next_break_due` is what the break coach renders, **and it is server code that
the handset mirrors in Swift** — one algorithm, two implementations, and
`FarmOpsKitTests` asserts the Swift one against fixtures generated from the
Python one. Two implementations of an entitlement calculation that can silently
disagree is how a farm ends up owing back wages, so they are tested against the
same table.

---

## 6. Payroll integration

### 6.1 The one wiring change that makes break pay real

```
_load_period_shifts()                    tools/payroll.py
  reads Farm Shift + crew rows
  ── ADD: read compliance_events where break_kind is set          ← NEW
  ── ADD: read the shift's break_policy                           ← NEW
  ── ADD: per crew row, breaks.worker_breaks(segment, events, policy)
          → row["break_hours"], row["unpaid_break_hours"]         ← NEW
        ▼
aggregate_shifts_for_period()            payroll_integration.py
  _segments_for() already reads row.get("break_hours", shift.get("break_hours"))
  ── UNCHANGED. It has been reading a key nothing ever wrote.
        ▼
  span − unpaid_break  →  total_hours
  paid_break kept inside total_hours, capped at the span
        ▼
engine_shift_rows()                      UNCHANGED
        ▼
calculate_full_payroll()                 payroll_calc.py
  piece:  earnings = units × rate
          avg_rate = earnings / (hours − break_hours)
          break_pay = avg_rate × break_hours        ← Demetrio v. Sakuma
          OT premium = half-time, 29 CFR 778.111
          gross = max(earned, minimum-wage floor)   ← makeup carried separately
```

**`payroll_integration.py` and `payroll_calc.py` do not change.** They were
written correctly and connected to nothing. The change is four lines in
`_load_period_shifts` and a new pure module.

### 6.2 The worked example

Ana, Block 7, Oregon, 14 July 2026. Piece rate $1.50/bucket.

```
joined 05:58, left 15:20                     9.37 h span
meal 12:00–12:30, unpaid                    −0.50 h
                                             8.87 h on the clock
rest 09:40, 13:30, each 10 min               0.33 h paid rest, inside the 8.87
cool-down 14:15, 10 min, concurrent with     0.00 h additional  ← counted once
  the 13:30 rest? no — 45 min apart          0.17 h paid rest
                                             0.50 h total paid rest

buckets accepted                             62
piece earnings         62 × $1.50          = $93.00
piece hours            8.87 − 0.50         =   8.37 h
average rate           $93.00 / 8.37       = $11.11/h
break pay              $11.11 × 0.50       =  $5.56
straight time                              = $98.56

OR minimum wage floor (non-urban, 2026)      8.87 h × $14.05 = $124.62
minimum_wage_makeup                          $26.06
gross                                        $124.62
```

Three things this example is here to show:

1. **The break pay is $5.56 and today it is $0.00.** Across a 28-person crew and
   a three-week window that is roughly $2,300 of unpaid rest — and the exposure
   is not the $2,300, it is that the wage statement says the rest was unpaid.
2. **The cool-down was counted once, not twice.** It did not run concurrently
   with a scheduled rest period here, so it added 0.17 h. Had it been called at
   13:30 it would have added nothing, because it *was* the rest period.
3. **The minimum wage makeup did the real work**, which is what a 62-bucket day
   in a light block looks like. `topped_up_to_minimum_wage` carries her name to
   the top of the run summary, which is the point: a top-up that vanished into
   gross would hide a piece rate set below the floor.

### 6.3 What `reconcile_bucket_payroll` gains

Three checks, on data it can now see:

| Check | Why |
|---|---|
| Entries `Linked` to a shift whose crew row has no overlap with the entry's timestamp | A bucket logged for somebody who had clocked out. |
| Slips with `break_hours > 0` and `break_pay = 0` | The §4.2 rule, as a report. |
| Shifts whose entries are 100 % `Badge Only` while the company has an active deployed model | The model is not reaching handsets. |

### 6.4 Where money and compliance deliberately stay apart

**A missed rest period is not a payroll line.** Oregon and Washington both treat
a missed rest period as a wage-and-hour violation with its own remedy, not as an
hour to be added to a cheque. `shift_closed_with_break_shortfall` raises an alert
and a Farm Task; it does not write a penalty into `calculate_full_payroll`.

Deciding what is owed for a missed break is a legal determination about a
specific claim, and a system that guessed at one would produce a number on a wage
statement that nobody could defend. The system's job is to make the shortfall
visible, dated, explained, and signed — which is the thing that is actually
missing today.

---

## 7. Phasing

Five phases. **Phase 1 is the one that stops a wage exposure and it should ship
before the 2027 window opens.** Everything after it is a better day for the
foreman.

### Phase 1 — v0.58.0 · Breaks exist

*The one that closes §0 finding 1.*

- `Farm Shift Compliance Event`: the five new fields, two new `event_type` values
- `Labor Break Policy` + two child doctypes; seed OR-2026 and WA-2026 unapproved
- `Farm Shift.break_policy`
- `breaks.py` — pure, with the standalone suite: entitlement tables for both
  states, overlap arithmetic, the concurrent cool-down case, the worker who left
  at eleven
- `log_shift_break` / `end_shift_break` methods + routes
- `_load_period_shifts` reads breaks → `break_hours` reaches `payroll_calc`
- MCP tools: `get_break_policy`, `log_shift_break`, `reconcile_break_compliance`
- **Mount the funnel. Probe it.**

**Done when** a `preview_payroll` for a piece-rate worker on a shift with a
logged rest period returns a non-zero `break_pay`, and `test_payroll_integration`
asserts it.

*No iOS in this phase.* Breaks are loggable through MCP and the Desk, which is
enough to prove the arithmetic and to back-fill a season if anybody needs to.

### Phase 2 — v0.59.0 · The foreman can run the day

*The one that closes findings 2 and 3.*

- `clock_out_worker` route (tool already exists)
- `get_shift_production` method + route
- `get_shift` route
- `sync_bucket_entries` returns `production` when given a shift
- iOS: merge Clock and Capture into one Harvest Day flow
- iOS: production board, worker sheet, individual clock-out
- iOS: immediate sync on enqueue, 60 s timer kept as the floor
- **Mount the funnel. Probe it.**

**Done when** a foreman can clock somebody out from the board and the closed
shift's Attendance row spans their real hours.

### Phase 3 — v0.60.0 · The break coach

*The one that prevents rather than records.*

- `get_break_policy` route
- Swift `BreakCoach` mirroring `breaks.next_break_due`, with fixtures generated
  from the Python
- iOS: the coach bar, the break sheet, the running break timer
- iOS: close-screen review sheet with the shortfall table

**Done when** a WA crew's handset warns at 2:30 and the shortfall table on the
close matches `get_shift.break_summary` exactly.

### Phase 4 — v0.61.0 · The rules

- `child_recency_json` primitive in `alerts/engine.py`
- The five rules from §4.2, seeded disabled, each with its citation and its
  `kairotic_gate_description`
- `add_worker_to_shift` returns `work_authorization` (§4.4)
- iOS: the three crew-build refusal cards
- Audit packet: the break timeline joins the OSHA and Wage-Hour packets

**Done when** `test_compliance_rule` fires each of the five against a fixture
shift and `preview` names the field that crossed.

### Phase 5 — v0.62.0 · Quality gating and provenance

*Last because nothing is broken by its absence — it is the season's second
question, not its first.*

- `Bucket Log Entry.capture_mode` and `auto_verdict`
- `BadgeAPI.payload` stops dropping `autoVerdict`
- `get_shift_production` and `get_piecework_summary` report the split
- `reconcile_bucket_payroll`'s three new checks (§6.3)
- iOS: the capture-mode indicator on the harvest screen, so a foreman knows
  which loop they are in without inferring it from whether a camera is on

**Done when** `get_piecework_summary` for a season reports the ML/tally split and
`TheGateIsBinary` still passes unchanged.

### 7.1 What is explicitly out of scope

| Not doing | Why |
|---|---|
| Worker self-clock | `shifts.py` argues it at length: -1131 puts the obligations on a named responsible person, and a crew of thirty each responsible for the record has nobody responsible for it. Some pickers have no device at all. |
| Automatic Heat Exposure Event filing | Five judgements about people, made by the person who was there. Offered at close (§2.6), never written. |
| Penalty pay for missed breaks | §6.4. |
| A second handset per crew | The board is per-shift and server-reconciled, so two handsets already work. Making it a *design goal* means solving conflict resolution for a case that has not happened. |
| Bin/lug-level traceability | `Bucket Log Entry` already carries `bin_id`, `block_id`, `crew_id` and `shipment_id`. Nothing writes them. That is a food-safety design, not this one. |

---

## 8. Open questions for Tim

1. **Do Oregon's meal and rest rules apply to your hand-harvest piece-rate
   crews as written?** Oregon's agricultural exemptions have moved and the answer
   decides whether OR-2026's meal schedule has one row or none. This is the one
   question in this document that should go to employment counsel before the
   policy is approved — and it is *why* the schedule is a record with an approver
   rather than a constant in Python.

2. **Constancy Farms runs blocks in both states. Is `work_state` a property of
   the block, the shift, or the crew member?** The design puts it on the shift
   with a per-crew-member override, matching how `pay_type` already works. If a
   single crew ever picks an OR block in the morning and a WA block in the
   afternoon, that is two shifts, not one — and the design should say so out loud
   if that happens.

3. **Piece rate per bucket — one rate, or per block?** `Farm Shift.pay_rate` and
   `Farm Shift Crew Member.pay_rate` both exist. A rate that varies by block is
   already expressible; a rate that varies by *variety within a block* is not.

4. **How many handsets per crew in the 2027 window?** One is assumed. Two work
   today. Ten is a different design.

---

## 9. What this document does not change

Worth stating, because the list is short and every item on it was argued
somewhere else and is still right:

- **The bucket gate stays binary.** No partial credit, no coverage in a
  multiplication, ever.
- **The foreman is the sole actor on a shift.**
- **The weather sweep logs observations and does not file compliance records.**
- **The Attendance bridge runs one way and never raises into the close.**
- **No model runs at compliance sweep time.** The break coach is arithmetic on a
  policy table; the rules are declarative records; AI's role stays authoring, and
  an authored rule is text until a human reads the citation.
