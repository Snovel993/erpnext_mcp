# The Configurable Compliance Framework

**Compliance rules are data. The engine that runs them is code. Nothing
probabilistic runs at sweep time.**

Until v0.22.0 a compliance rule in this app was a Python function. Moving a
threshold, correcting a citation, narrowing a rule to one company or switching
one off for a season was a code change, a release and a deploy.

Regulations do not move on a release cadence. Oregon OSHA renumbered heat
illness from OAR 437-004-1130 to -1131. Oregon Tilth added a Fraud Prevention
Plan requirement. The FDA re-phased FSMA Produce Safety. Every one of those is a
data change that used to wear a code change's clothes.

Since v0.22.0 a rule is a **Compliance Rule** record. Its thresholds, its scope,
its citations, its regimes, its message and its switch are fields somebody edits.
What did not move is the sweep: `alerts/base.py` still walks a rule set, still
keys each alert on the rule and the record and nothing that moves daily, still
auto-dismisses what it did not observe. Only *where the rule set comes from*
changed.

---

## 1. The non-negotiable: the runtime is deterministic

**There is no model in the trigger path.** No classifier, no embedding, no
natural-language interpretation of anything at sweep time. A rule fires because a
date crossed a threshold or a column matched a filter, and the report can name
which.

That is not squeamishness about AI; it is what makes an alert *defensible*. For
every alert an auditor questions, the answer traces to:

- a **Compliance Rule** row,
- its `regulation_citations`,
- its `human_approved_by` and `human_approved_on`,
- and the specific field on the specific record that crossed a threshold.

Not to a model output nobody can explain.

**AI's role is confined to authoring.** An AI-proposed rule is *text* until a
human reads the citation against the regulation and approves it. Once approved it
executes the identical deterministic path as every other rule. `authored_by` is
provenance, not behaviour.

`propose_compliance_rule` is declared and refuses in v0.22.0. Phase 2 (v0.23.5)
wires it, and it will write drafts with `enabled = 0`, never edit or disable an
existing rule, and flag any proposal carrying `custom_python` for extra review.

---

## 2. The three shapes of a rule

| Shape | What is on the record | What is code | Shipped rules |
| --- | --- | --- | --- |
| **declarative** | everything | nothing rule-specific | 6 |
| **builtin_scanner** | every tunable — thresholds, scope, citations, regimes, message where used, the switch | the *shape of the join* | 7 |
| **custom_python** | everything, including a restricted program | the interpreter that runs it | **0** |

That `0` is the important number. `custom_python` is an escape hatch for rules an
operator or a proposer writes that the primitives do not reach — and a framework
that needed it for its own thirteen rules would be a framework whose vocabulary
does not reach its own problem domain.

**The right response to reaching for `custom_python` is to say what shape of
question the rule asks, and turn that shape into a field.** §5 does exactly that
for the seven built-ins.

---

## 3. Authoring a declarative rule

A declarative rule is evaluated like this, per row of `target_doctype`:

```
apply scope_filters (ALL must hold)
anchor          = row[date_field]                      (empty date_field → no clock)
if anchor is missing → missing_date_behaviour: Skip, or Raise at severity_expired
due             = anchor + cadence_days                (cadence 0 → the anchor IS the deadline)
days_remaining  = due - today
severity        = severity_expired   if days_remaining < 0 or there is no clock
                  severity_critical  if days_remaining <= threshold_critical_days
                  severity_warning   if days_remaining <= threshold_warning_days (or window_field)
                  otherwise: say nothing
message         = render(message_template, row + computed context)
```

### The fields, and what each one is for

| Field | Meaning |
| --- | --- |
| `rule_id` | The stable key alerts are filed under. **First segment of every alert docname** — never change it on a live rule. |
| `target_doctype` | The DocType whose rows the rule walks. |
| `date_field` | The cadence anchor. **Leave empty** for a rule with no clock; every matching row then raises at `severity_expired`. |
| `cadence_days` | How often the activity must recur. 365 on a last-inspection date is the annual walk; 0 means the date field *is* the deadline. |
| `threshold_critical_days` / `threshold_warning_days` | Fire at this many days remaining or fewer. **Negative means the band never fires.** The warning threshold is also the outer window: outside it the rule says nothing. |
| `severity_critical` / `severity_warning` / `severity_expired` | The severity of each band. Separate fields because `filing_response_due` escalates Info → Warning as the deadline passes, and a fixed ladder could not express it. |
| `missing_date_behaviour` | `Skip` for an expiry (a training with no expiry does not lapse). `Raise` for a cadence (a cabin nobody has ever inspected is the most overdue cabin there is). |
| `due_date_mode` | `From Anchor`, `Today`, or `None`. The calendar sorts on it. |
| `window_field` | A field on the row carrying its **own** lead time, used instead of `threshold_warning_days` — `renewal_window_days` on a certificate, because the turnaround an issuing body takes is a property of the certificate. |
| `scope_filters_json` | ANDed filters. See below. |
| `message_template` | Jinja, rendered in a sandbox with **no framework in it**. |
| `regimes` / `regimes_from_field` | The audits the alert answers to, from the rule or copied off the row. |
| `requires_doctypes` / `requires_fields` | What must exist for the rule to run at all. |

### Scope filters, and why `default` is load-bearing

```json
[
  {"field": "status", "op": "eq", "value": "Active", "default": "Active"},
  {"field": "unit_type", "op": "nin", "value": ["Toilet-Shower", "Kitchen"], "default": ""}
]
```

Operators: `eq`, `ne`, `gt`, `lt`, `gte`, `lte`, `in`, `nin`, `isnull`,
`isnotnull`, `contains`, `ncontains`.

**Filters are evaluated in Python, not pushed into SQL, and `default` is why.**
In SQL, `status != 'Active'` excludes every row whose status was never set —
which on a new camp is most of them. Three of the shipped rules read a column
whose empty value means something specific:

- a Compliance Policy with no status **is in force**,
- a Regulatory Filing with no status is **neither Draft nor Withdrawn**,
- a Housing Unit with no condition is **not Uninhabitable**.

`default` says out loud what the legacy `str(row.get("status") or "Active")` said
in an idiom. Omitting it where it matters is how a rule goes quiet on exactly the
records nobody has touched.

A filter naming a field this site has not got is **skipped and reported** in
`computation_warnings`, not treated as a failed row — half this app's compliance
columns are installed on demand, and a rule that refused every row on a site that
had not run `install_compliance_fields` would look exactly like a clean
operation.

### Message templates

Jinja, rendered by `jinja2.sandbox.SandboxedEnvironment` with **no `frappe` in
the globals** — deliberately not `frappe.render_template`, whose environment
carries the framework and would be a second, undocumented escape hatch beside the
one this release spent a module sandboxing.

Available: every field on the row by name, plus `row`, `days_remaining`,
`days_overdue`, `days_since_anchor`, `anchor`, `due_date`, `today`, `severity`,
`regimes`, `subject`, `cadence_days`, `threshold_critical_days`,
`threshold_warning_days`, `rule_title`, `regulation_citations`.

A template that fails to render produces a plain, honest fallback and a warning
rather than killing the rule. **An ugly alert is a problem somebody fixes; a
missing one is not.**

Write the message somebody will actually act on. `"WPS handler training expires
in 12 days and he cannot lawfully spray after that"` is a different decision from
`"expires in 12 days"`.

---

## 4. `custom_python`: what it is, and when not to use it

A short restricted program, evaluated by `alerts/sandbox.py` — an **AST
interpreter**, never `exec`, never `eval`.

### What is in scope

`frappe` (a read-only facade: `get_all`, `get_value`, `get_doc`, `exists`,
`count`, and a `utils` namespace), `today`, `company`, `target_doctype`,
`doctype_meta`, `rule`, `regimes`, the rule's thresholds, `observation(...)`,
`warn(...)`, `days_until`, `days_since`, `datetime`, `timedelta`, the severity
constants, and the safe half of the builtins. **There is nothing else** — a name
the caller did not provide is a refusal listing what is in scope.

`frappe.get_doc` returns a **plain dict**, not a Document. A Document has
`.save()` on it, and a read-only sandbox that hands back a live document is
read-only in the same sense a locked door with the key in it is locked.

### What is refused, and why

| Refused | Why |
| --- | --- |
| `import`, `from … import` | One import is `os`, and `os` is the filesystem. |
| `exec`, `eval`, `compile`, `open`, `globals`, `locals`, `getattr`, `setattr`, `type`, `object`, `super` | Each is a way back to the interpreter the allowlist just removed. |
| **every underscore-prefixed attribute** | `x.__class__.__bases__[0].__subclasses__()` is the standard escape from every sandbox that forgot this, and it needs no imports at all. |
| `while` | Unbounded by construction. `for` over a sequence is bounded by the sequence. |
| `def`, `class`, `lambda`, `yield` | A rule that needs to define a function has outgrown this field. |
| `try` / `except` | A rule that swallows its own errors is a rule that goes quiet, which is the failure this whole app is written against. |
| `with`, `del`, `global`, `nonlocal`, `assert`, `raise`, `await`, `:=` | No use case, and each is a surface. |

Bounded at **200,000 node visits** and **5 seconds** of wall clock. A program
that exceeds either is reported against that one rule; the other rules still run,
because the sweep has never let one rule take the night down.

A refused or failed program does **not** silently observe nothing. It raises a
Warning against the rule itself saying the condition is now **UNWATCHED** — a
compliance rule that quietly stops watching is worse than one that visibly
breaks.

### Why RestrictedPython or asteval are not used

Both are good libraries, and neither is on this bench. `pyproject.toml` has three
runtime dependencies, each argued for and each imported defensively so a bench
missing one loses a named feature rather than the app. Adding a fourth for a
field that ships **used by zero of the thirteen rules** would be the tail wagging
the dog. The subset actually needed here is small and closed — read some rows,
compare some dates, build some observations — and an interpreter for that subset
has no supply chain and refuses by construction rather than by configuration.

### The rule of thumb

> If you can say in one sentence what *shape* of question your rule asks, that
> shape probably wants to be a declarative field rather than a program.

---

## 5. The seven built-ins, and the primitive each one is waiting for

These are ranked by what a new primitive would buy. This section is the v0.22.1
backlog.

### Best value: `superseded_by_later_clean` — takes **two** rules declarative

`housing_corrective_action_open` and `water_test_contamination` share one gate:
**is this finding still true?** It stops being true when a *later clean record
for the same subject* supersedes it — a cabin re-inspected in September with
nothing found says more about July's water stain than a checkbox does.

No filter on the finding's own row can answer a question about *other rows*. The
primitive is four values: `(doctype, subject_field, date_field, clean_state)`.
One field group, two rules.

### Second: `regime_heuristics_json` — takes `certification_expiring` declarative

Three things keep it built-in, and only one is hard:

- the renewal window is per row → **already solved** by `window_field`;
- the category is per row (an applicator licence is Workforce, a GlobalGAP
  certificate is Certifications) → wants `category_from_field`;
- the **regimes are derived from the certificate's TYPE** through an ordered
  eleven-row needle table. `regimes_from_field` copies tags off a column; here
  there is no column, only a name to read.

`regime_heuristics_json` is that table as data: ordered `(needles → regimes)`
pairs, first match wins. The ordering is the whole content — `globalgap` must be
checked before `gap`, because "GlobalGAP" contains "GAP" and a USDA GAP packet
must not be handed another scheme's certificate.

### Third: `gate_date_field` + `gate_within_days` — takes `water_test_stale` declarative

The declarative engine has **one** cadence anchor. This rule's gate is a
conjunction over two independent fields: the block was sprayed inside the season
**and** its water was tested outside the cadence. Neither half fires alone —
ground nobody is spraying raises nothing however stale its water.

The primitive is a second anchor used only as a gate: "only consider rows whose
`<field>` is inside `<n>` days".

### Fourth: plural `date_fields` — takes `housing_detector_test_stale` declarative

A cabin has a smoke detector and a CO detector, tested independently; **either**
being stale fires, and the message must name which. "The CO detector was last
tested 400 days ago" is a different errand from "no smoke detector test has ever
been recorded", and an alert saying only "a detector is overdue" sends somebody
to test the wrong one.

Needs per-field labels, a fold to worst-remaining for the severity, and an
enumeration for the message. More machinery, and it buys one rule.

### Probably never: `audit_action_overdue`

It walks an Audit Event's corrective-action **child rows**, keeps the overdue
ones, picks the worst, takes its severity from the worst finding's own severity,
and raises **one alert per audit** rather than one per action — five open items
on one PrimusGFS audit are one conversation with one auditor, and five rows would
look like five problems.

Every part of that is an aggregation, and an aggregation is not a filter. The
primitive would be a second engine: group-by, fold, pick. That is a fair
description of "write it in Python". This is not a gap in the vocabulary; it is a
different shape of question.

### Probably never: `supervisor_review_lapsed`

Three reasons, any one of which is enough:

1. **It walks a table of doctypes, not one.** `REVIEW_TARGETS` is the list of
   records carrying the §112.161(b) review columns, written to grow — Housing
   Inspection, Water Test, Heat Exposure Event and Farm Task Assignment are each
   one row away. `target_doctype` is singular by design.
2. **The condition is an `OR` of two nulls.** A record is unreviewed when the
   reviewer is missing *or* the date is missing — a date with nobody attached is
   what an auditor is trained to disbelieve. Scope filters are ANDed
   deliberately; an OR-of-filters is a query language, and a query language in a
   text field is what `custom_python` already is.
3. **The clock runs on `creation`, not on the activity date.** §112.161(b)'s own
   words are "after the records are made", and reading the activity date would
   raise a Critical on every record of a season somebody backfilled.

Note also that this rule's thresholds mean **days elapsed**, not days remaining —
the thing measured is an absence getting older rather than a deadline
approaching. A number on a record that means the opposite of what the same number
means on the other twelve is a number somebody will eventually misread, and that
is the strongest single argument for leaving this one built-in.

---

## 6. Provenance, approval and audit

### The gate

`enabled` cannot be set without `human_approved_by` **and**
`human_approved_on` — the DocType refuses it. Both, because a review date with
nobody attached is what an auditor is trained to disbelieve.

There is no path by which a rule starts firing without a person having put their
name to it. That matters most for the case that does not exist yet: **"a model
wrote a rule and it went live" must never be a true sentence about this app.**

`create_compliance_rule` always writes a Draft, whatever the caller asked for.
Only `approve_compliance_rule` enables one.

### Versioning by copy

`update_compliance_rule` writes a **new row at version+1** and points the old
row's `superseded_by` at it. The old row is disabled, never edited, never
deleted.

Two consequences, and both are the point:

- a sweep that started against v1 **finishes against v1** — there is no window in
  which a running evaluation's definition changes underneath it;
- an alert raised last April can still be read against the definition that raised
  it, thresholds and citation as they were.

The new version **inherits** the old one's approval. A threshold moved is not a
new rule, and forcing re-approval on every tuning edit trains people to click
through approvals, which is worse than not having the gate. A rule that was off
stays off.

### One live row per `rule_id`

Enforced in the controller rather than by a unique index, because the constraint
is not on the column: v1 and v2 share a `rule_id` by construction. What may not
exist twice is a row that is **enabled and unsuperseded** — two definitions of
one rule, where the one tonight's sweep ran would be whichever sorted first.
`active_row_flag` materialises that condition as an indexed column.

### Off is not deleted

`deactivate_compliance_rule` requires a reason of at least a sentence and appends
it to the rule's purpose. The rule raises nothing **and dismisses nothing** — the
alerts it already owns stay exactly as they were, which is the same reading a
rule skipped for a missing DocType gets, and for the same reason: **switching a
rule off is not evidence that anybody did the work.**

There is deliberately no delete.

### The audit trail

Every call writes an MCP Action Log row through `registry.dispatch` — arguments,
caller, timestamp, result. `update_compliance_rule` additionally returns a
field-by-field `changes` diff, so the log row records what the rule said
**before** and not merely what was asked for. Combined with the DocType's own
`track_changes` history and the superseded rows themselves, "who changed this
rule and when" is answerable without leaving the app.

---

## 7. Migration and idempotency

The thirteen shipped rules are seeded into records by `install._compliance_rules()`
on install and after **every** migrate.

**It is a seeder, not a Frappe `fixtures` entry**, and `test_hooks.py` forbids
that word by name. A fixture is imported by `bench migrate` with no ability to
skip what a site already has, so an operator who raised a threshold would have it
corrected back on the next upgrade. The seeder checks for the `rule_id` **across
every row, not only live ones**, before it writes:

- a rule somebody edited keeps the edit;
- a rule somebody switched off stays off;
- a rule somebody superseded with their own v2 does not get v1 seeded back beside
  it — which would give the `rule_id` two live rows and make the sweep's answer
  depend on sort order.

**Migrated rules arrive `enabled = 1`**, against this app's usual instinct that
everything mutating ships off. They were *already running* — as Python — the
night before, and seeding them disabled would silently switch the whole
compliance calendar off during an upgrade.

**Until the migrate runs, the sweep falls back to the shipped definitions and
says so** in `engine_notes` on its report. A compliance calendar that quietly
emptied itself for the length of an upgrade would be the single worst failure
this app could have.

### Backward compatibility, asserted rather than asserted-to

`test_compliance_rule_engine.TheMigrationChangesNothing` builds one fixed
database, runs the sweep with the shipped Python rules, snapshots every alert
row, deletes the alerts, seeds the thirteen records, runs the sweep again through
the record-driven engine, and compares the two snapshots **field by field** —
docname, severity, category, company, source, message, due date, first seen.

Not counts. Not "an alert of this type exists". The rows.

---

## 8. Worked example

An operation decides to watch for something no shipped rule covers: a cabin's
occupancy limit has to be posted in it (OAR 437-004-1120).

```jsonc
// 1. Author it. It arrives as a DRAFT and fires nothing.
create_compliance_rule({
  "rule_id": "cabin_capacity_unposted",
  "title": "A cabin in use has no posted occupancy limit",
  "category": "Housing",
  "target_doctype": "Housing Unit",
  "date_field": "",                    // no clock: the condition is true or it is not
  "severity_expired": "Warning",
  "threshold_critical_days": -1,
  "threshold_warning_days": -1,
  "due_date_mode": "Today",
  "scope_filters": [
    {"field": "unit_type", "op": "eq", "value": "Cabin"},
    {"field": "capacity", "op": "gt", "value": 0},
    {"field": "condition", "op": "ne", "value": "Uninhabitable", "default": ""}
  ],
  "message_template":
    "{{ name }} sleeps up to {{ capacity }} and the occupancy limit is not recorded as posted "
    "in the unit. OAR 437-004-1120 expects it where the people it protects can read it.",
  "regimes": ["OR-OSHA"],
  "regulation_citations": "OAR 437-004-1120(3)(b)",
  "kairotic_gate_description":
    "Fires on a cabin that can actually be slept in — a shower block raises nothing, and a "
    "unit marked Uninhabitable raises nothing because there is nobody in it to protect. "
    "It goes quiet when the limit is posted.",
  "audit_packet_types": ["OSHA"]
})

// 2. See what it WOULD do. Writes nothing.
test_compliance_rule({"name": "cabin_capacity_unposted"})

// 3. A human approves it. There is no other way to turn it on.
approve_compliance_rule({"name": "cabin_capacity_unposted"})

// 4. It is watching tonight, and its alerts are in the OSHA packet.
refresh_compliance_alerts({"company": "Highland LLC"})
```

No release. No deploy. No engineer.

A year later the citation is renumbered:

```jsonc
update_compliance_rule({
  "name": "cabin_capacity_unposted",
  "regulation_citations": "OAR 437-004-1121(3)(b)",
  "reason": "OR-OSHA renumbered the housing rule in the March 2027 revision"
})
```

That writes v2, disables v1, and leaves v1 fully readable — so an alert raised
under the old citation still shows the citation it was raised under.

---

## 9. What is deliberately NOT operator-editable

- **The sweep engine** — `alerts/base.py`. Reconciliation, idempotent docnames,
  auto-dismissal, the regime filter's refusal to dismiss what it did not run.
- **The Observation, Alert and Farm Task schemas.**
- **Security-critical logic** — role guards, kill switches, the audit log,
  transport authorisation.
- **MCP tool contracts** — these are API surface.
- **The sandbox itself**, and the allowlist it refuses by.

The rule *definitions* are data. The rule *engine* stays code.

---

## 10. Roadmap

| Version | What |
| --- | --- |
| **v0.22.0** (this) | Compliance Rule doctype, declarative engine, sandbox, migration of the thirteen, seven tools. |
| v0.22.1 | The primitives in §5, best-value first: `superseded_by_later_clean`, then `regime_heuristics_json`. Producer templates wired declaratively. |
| v0.23.5 | `propose_compliance_rule` wired: AI reads a regulation, drafts a rule with `authored_by = AI-proposed`, `enabled = 0` and an `ai_source_citation`; a review queue in Desk. |
| v0.24.5 | Regulation Feed doctype + scheduled re-evaluation: registered sources are re-read, and regulations that moved produce change proposals. |

The auditor test for all of it: *an auditor asks about a rule that changed last
month, and the record shows `human_approved_on` two weeks ago with an
`ai_source_citation` pointing at the Federal Register notice.* Proof the
operation tracked, evaluated and adopted the change — on the record itself.
