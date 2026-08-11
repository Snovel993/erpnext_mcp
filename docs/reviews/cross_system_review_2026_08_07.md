# Cross-system review — 2026-08-07

Scope: `erpnext_mcp` (this repo), `farm_app` (Flask server), `fafo_ios`
(FarmOpsKit + FarmOps app). Review only — no code was changed and nothing was
committed.

Everything below was read out of the source at the commits checked out on
2026-08-07: `erpnext_mcp` at `70959db` (v0.48.1), `farm_app` at `828caf4`,
`fafo_ios` working tree. Line numbers are from those files.

---

## The one-paragraph summary

**farm_app and erpnext_mcp are not integrated.** There is no runtime call in
either direction. They are two independent products that both do HR, payroll,
tax withholding, clock-in and badge identity, with two separate iOS clients. The
only thing connecting them is `import_farm_app_fields`, a one-shot manual paste
of legacy field records. That is a strategic finding, not a morning fix, and it
frames most of what follows: the "cross-system" defects are almost all
*divergence* defects — the same obligation implemented twice, once well and once
stale.

The single most damaging concrete bug is **C1**: every photo and signature the
iOS onboarding wizard collects for the I-9 and W-4 is silently thrown away. It
returns success, the wizard advances, the Review screen shows the step green,
and nothing lands on the server.

---

## Critical — blocks a workflow entirely

### C1. Every onboarding document photo and signature is silently discarded

**Where:** `fafo_ios/FarmOpsKit/Sources/FarmOpsKit/Networking/FrappeClient.swift:456-533`,
`erpnext_mcp/api/fallback_auth.py:151,436-449`

`OnboardingAPI.attachDocument` is the upload path for all five onboarding
artifacts: the I-9 Section 1 signature, the List A / List B / List C document
photos, the Section 3 reverification signature, the signed W-4 photo, and the
photographed signed I-9. It calls `FrappeClient.uploadFile`, which posts to:

```swift
url.appendPathComponent("api")
url.appendPathComponent("method")
url.appendPathComponent("upload_file")     // FrappeClient.swift:466-468
```

That is Frappe's own `/api/method/upload_file` — the exact path family the
farmops-api sidecar exists to avoid. `MobileAPI.swift:14-17` states the reason:
the Tailscale funnel drops the `Authorization` header on that path.

The fallback that rescues everything else does not cover this path:

```python
_PATH_PREFIX = "/api/method/erpnext_mcp.api."          # fallback_auth.py:151

def _is_mobile_path() -> bool:
    if request is not None and str(...path...).startswith(_PATH_PREFIX):
        return True                                     # fallback_auth.py:444
```

`/api/method/upload_file` does not start with that prefix, so `authenticate()`
returns `""` immediately and the `X-FarmOps-Token` the client did send
(`FrappeClient.swift:475-476`) is never read. The request arrives as Guest.

**Why it is silent rather than an error.** The documented Guest failure mode
(v0.17.2, quoted in `fallback_auth.py:15-27`) is *HTTP 200 carrying the Desk's
`/me` HTML page*. `uploadFile` never looks at the body:

```swift
switch http.statusCode {
case 200...299:
    return                                              // FrappeClient.swift:520-522
```

So the call succeeds, `advance()` continues, `completedSteps.insert(...)` runs,
and the operator is told the step is done.

**Consequence.** A farm running behind the funnel — which is the deployment this
whole sidecar was built for — has been collecting I-9 List A/B/C document
photographs and Section 1 signatures on handsets and storing none of them. The
I-9 Form record says Complete. The evidence behind it does not exist.

**Fix (morning-sized), in preference order:**

1. Route onboarding attachments through `ChunkUploader` →
   `/farmops/api/files/stage_file_chunk` + `finalize_staged_file`, which are
   already published (`farmops_api/routes.py:174-175`) and already carry task
   evidence and shift signatures correctly. Then call `upload_signed_i9` with
   the returned token for the signed sheet (see H1).
2. Independently, make `uploadFile` reject a 200 whose body is not JSON. A
   transport that cannot tell success from the login page will hide the next bug
   of this class too.
3. Optionally widen `_is_mobile_path()` to include `upload_file` — but note this
   opens Frappe's generic uploader to the fallback credential, which is a wider
   surface than the sidecar's two file routes. Prefer (1).

### C2. Onboarding an employee with the vault locked returns an unhandled 500

**Where:** `farm_app/app/blueprints/hr.py:143,197-397`,
`farm_app/app/utils/encryption.py:416-430`

`onboard_employee` computes `vault_locked = not is_unlocked()` at line 143 and
passes it to the template — but the `POST` branch never checks it. It goes
straight to:

```python
employee.full_legal_name = full_legal_name    # hr.py:286
employee.address_line1 = ...                  # hr.py:294
...
db.session.commit()                           # hr.py:397
```

Every one of those columns is `EncryptedText`, whose own docstring says:

> `write (vault locked): raises RuntimeError`

So the commit raises, uncaught, and the manager gets a 500 after filling in a
complete employee record. Worse, on the manual-creation path the
`flash(f'New user created. Temporary password: {temp_phrase}')` at `hr.py:256`
has already been queued — so the operator is shown a temporary password for a
User that the rollback destroyed.

**Fix:** guard the top of the POST branch on `vault_locked`, flash "Unlock the
vault before onboarding — employee records hold encrypted personal data", and
re-render. Ten lines.

### C3. Onboarding a second employee without a username is a 500

**Where:** `farm_app/app/blueprints/hr.py:198-259`, `farm_app/app/models.py:182,197-199`

The only validation on the form is `if not full_legal_name` (`hr.py:202`).
`username` is never checked. On the manual path with a blank username:

```python
user = User.query.filter_by(username=username).first()   # hr.py:244, username == ""
if not user:
    user = User(username=username, ...)                  # creates User(username="")
```

`User.username` is `unique=True, nullable=False` — `""` is not NULL, so the first
one succeeds. The **second** manual onboarding with a blank username finds that
user, reuses it, and then `Employee(user_id=user.id)` violates
`Employee.user_id`'s `unique=True` (`models.py:342`) → IntegrityError → 500 with
no usable message.

**Fix:** require a non-empty username on the manual path with the same
flash-and-re-render treatment `full_legal_name` gets.

---

## High — the workflow runs but produces wrong or incomplete results

### H1. The signed I-9 photo is filed against the wrong record, and the code says so

**Where:** `fafo_ios/FarmOpsKit/Sources/FarmOpsKit/Networking/OnboardingAPI.swift:219-246`

The doc comment is emphatic and now false:

> `THIS IS NOT upload_signed_i9 AND THERE IS NO SUCH ENDPOINT.`

v0.47.1 added it. `farmops_api/routes.py:148-150` routes `get_i9_form`,
`generate_i9_pdf` and `upload_signed_i9`. The app still hangs the photograph on
the **Employee** record under a filename convention (`i9_signed_<formID>.jpg`),
which the comment itself flags as the cost: an auditor pulling the I-9 Form will
not find it there.

Combined with **C1**, the photograph does not land anywhere at all today.

**Fix:** `upload_signed_i9(employee:, file_token:)` after a chunked upload. Note
its two rules: it requires the HR role with *no* self-service exception, and it
takes a `file_token`, never bytes (`api/mobile.py:1881-1935`).

### H2. Eight shipped server routes have no iOS client

**Where:** `fafo_ios/FarmOpsKit/Sources/FarmOpsKit/Networking/MobileAPI.swift`

`MobileAPI` declares paths through v0.47.0. These v0.47.1 / v0.48.0 routes exist
on the server and are named nowhere in Swift:

| Route | What the app loses |
|---|---|
| `get_i9_form` | The wizard cannot read an existing I-9 beyond a one-word status. `OnboardingModels.swift:539` still documents this as impossible. |
| `generate_i9_pdf` | No printable I-9. The wizard collects both sections in an orchard and produces no artifact for §1324a(b)(3). |
| `upload_signed_i9` | See H1. |
| `generate_w4_pdf` | No printable W-4. Withholding elections collected since v0.45.0 with nothing to show. |
| `list_authorized_signers` | See H3 — this is the one that turns into a hard failure. |
| `add/update/remove_authorized_signer` | Roster is Desk-only, so it cannot be fixed at 6am on a hire day — the exact case `routes.py:156-157` says it was published for. |

### H3. The Section 2 verifier field is pre-filled with a login, not a name — and a configured signer roster will refuse it

**Where:** `fafo_ios/.../Steps/I9Section2StepView.swift:84-85`,
`OnboardingWizardViewModel.swift:408-410`, `erpnext_mcp/tools/signers.py:233-320`

Both the Section 2 step and the reverification step do:

```swift
if data.verifierName.isEmpty, let user = session.credentials?.user {
    data.verifierName = user           // the account identifier, e.g. claude@fafo.farm
}
```

v0.48.0's `resolve_signature` matches an explicitly-sent `verifier_name`
case-insensitively against the roster's **printed `full_name`**
(`signers.py:286-289`, `_authorized_by_name` at 302-320). An account identifier
will not match a printed name, so the moment a site adds its first signer row —
which `signers.py:24-27` calls "the switch" — every I-9 Section 2 and every
reverification filed from a phone is refused with:

> `'…' is not an active authorized signer for I-9 on this site.`

The app has no `list_authorized_signers` call (H2), so the foreman cannot
discover the right spelling from the handset.

The escape hatch already exists and the app blocks itself from using it:
`resolve_signature`'s first preference is that **a caller sending nothing gets
the authorised person's real name off their own roster row**. But
`OnboardingI9Section2.isValid` requires a non-empty `verifierName`
(`OnboardingModels.swift:291`), so the app forces a value.

**Fix:** stop pre-filling with `credentials.user`; make the field optional when
a roster is configured, or populate it from `list_authorized_signers`.

*Note: sites with an empty roster are unaffected. This is a latent failure that
fires on the day someone uses the v0.48.0 feature.*

### H4. farm_app's federal withholding engine is pinned to 2024

**Where:** `farm_app/app/utils/payroll_calcs.py:14-73`

Header comment: `2024 Federal Tax Constants`. Brackets, standard deductions
(`14600 / 29200 / 21900`) and `SS_WAGE_BASE = 168600.0` are all 2024 figures,
hardcoded, with no year parameter. Today is 2026-08-07, so every paycheck
computed in farm_app for 2025 and 2026 uses two-year-old numbers. The Social
Security wage base is the sharpest edge — anyone crossing $168,600 stops having
SS withheld well before they should.

Contrast `erpnext_mcp`, which was fixed yesterday (v0.48.1) to key the
dependent credit to the form's own `tax_year` rather than the calendar, with a
patch to restate already-filed rows. farm_app has no equivalent mechanism.

### H5. farm_app computes overtime against a 40-hour threshold on any-length pay period

**Where:** `farm_app/app/utils/payroll_calcs.py:264-311,392`,
`farm_app/app/blueprints/payroll.py:333-336,363-366`

`_get_employee_hours` sums clock logs across the **whole pay period**, and that
total goes straight into:

```python
ot_result = calculate_overtime(hours_worked, employee.pay_rate or 0.0, is_exempt=is_exempt)
#                              ^ period hours          ot_threshold defaults to 40.0
```

For a `weekly` period this is right. For `bi_weekly`, `semi_monthly` or
`monthly` it is badly wrong: a bi-weekly worker doing two ordinary 40-hour weeks
is paid 40 regular + 40 overtime hours. FLSA overtime is per workweek, not per
pay period.

`PayPeriodSchedule` supports all four frequencies, so this is reachable on a
default configuration.

### H6. `overtime_eligible = None` makes a worker exempt — the opposite of the comment

**Where:** `farm_app/app/utils/payroll_calcs.py:390-391`

```python
# When overtime_eligible is None, default to eligible (False = not exempt)
is_exempt = not (employee.overtime_eligible if employee.overtime_eligible is not None else False)
```

`None` → `False` → `not False` → `is_exempt = True`. The code does the opposite
of what its own comment says, and the direction it errs in underpays. The column
default is `True`, so this only bites rows created outside the ORM default —
seeds, imports, migrations.

### H7. A W-4 that isn't re-filed each January silently reverts the worker to single/zero

**Where:** `farm_app/app/utils/payroll_calcs.py:376-380`, `farm_app/app/models.py:1212-1214`

```python
w4 = EmployeeW4.query.filter_by(employee_id=employee.id, tax_year=year).first()
```

Exact-year match, and `uq_employee_w4_year` enforces one row per employee per
year. An employee with a 2025 W-4 and no 2026 row gets `w4 = None`, which
`calculate_federal_withholding` treats as single / no dependents / no extra
withholding. No warning, no flag on the preview screen.

`erpnext_mcp` handles this correctly the other way: `submit_w4` supersedes the
prior form and keeps the chain (`api/mobile.py:1550-1556`), so "which W-4 was in
force when this cheque was cut" is answerable.

### H8. Local tax is deducted from net pay but never written to the pay record

**Where:** `farm_app/app/utils/payroll_calcs.py:432-441,468`,
`farm_app/app/blueprints/payroll.py:367-390`

`calculate_full_paycheck` computes `local_tax_total` and folds it into
`total_taxes` → `net_pay`. It returns both `local_tax_withholdings` and
`local_tax_total`. `PayRecord` has columns for both (`models.py:1116-1117`).
`calculate_commit` writes neither.

Result: the stored record's listed withholdings do not reconcile to its stored
`net_pay`. Anyone auditing a stub, or building a quarterly summary from
`PayRecord`, sees money vanish.

### H9. Nothing in erpnext_mcp gates work on I-9 status

**Where:** grep for `i9_status` across `erpnext_mcp/` — the only non-test, non-patch
consumers are `compliance_fields.py` (installs the column), `tools/i9.py`
(writes it), `api/mobile.py:get_employee` (reconciles it), and
`audit_packets.py:584-646` (reports it).

It is read by **no** gate. `start_shift`, `add_worker_to_shift`,
`link_badge_to_employee`, `sync_bucket_entries` and `claim_task` never consult
it. A person with no I-9 at all can be put on a crew shift, mapped to a badge,
credited with buckets and paid.

This is the "work readiness pipeline" gap in the brief: the pipeline ends at
"the records exist". Nothing downstream consumes the readiness signal, so the
whole onboarding wizard is advisory.

The honest morning-sized version of a fix is a **warning**, not a block —
`start_shift` and `add_worker_to_shift` returning an `unverified` list alongside
the crew, so a foreman sees it. A hard refusal would strand crews mid-harvest
and belongs behind a setting.

---

## Medium — usability friction, confusing UX

### M1. The iOS W-4 hardcodes the tax year

`OnboardingModels.swift:507` — `"tax_year": "2026"`, a literal. A W-4 signed in
January 2027 is filed against 2026. Should be derived from the current date, or
better, from the server.

### M2. The I-9 address state defaults to `"Oregon"`, and the printed form drops it

`OnboardingModels.swift:141` sets `state: String = "Oregon"`; the UI is a plain
`TextField` (`I9Section1StepView.swift:203`). The doctype stores it as free-text
`Data` with no validation, but `i9_pdf._state` (`i9_pdf.py:372-375`) accepts only
the two-letter codes in `STATE_CODES` and returns `""` otherwise.

So every worker onboarded without a driver's-license scan — a passport scan, or
the manual path — gets a **blank State box on the printed federal I-9**, and
nobody finds out until the form is printed. A scanned licence is fine: AAMVA
`DAJ` is already the 2-letter code (`IDScanResult.swift:102`).

Same value is copied into the W-4 prefill (`tools/w4.py:473`) and the tax-forms
address line (`tools/taxforms.py:852`).

**Fix:** make it a picker over `STATE_CODES`, or normalise on the server.

### M3. Gender and date of birth are pre-answered with plausible defaults

`OnboardingModels.swift:44-45`: `dateOfBirth` defaults to today minus 25 years,
`gender` defaults to `.male`. `isValid` (line 54) checks only first and last
name, so both defaults sail through onto a federal form. A foreman who taps
through gets a DOB that looks deliberate and is fabricated.

**Fix:** make both un-set until touched and require them in `isValid`.

### M4. An attachment failure after a successful section submit dead-ends the wizard

`OnboardingWizardViewModel.swift:483-496` (and the same shape at 503-531,
539-546, 563-573):

```swift
try await OnboardingAPI.submitI9Section1(...)   // lands on the server
if let sig = i9Section1.signatureData {
    try await OnboardingAPI.attachDocument(...) // throws
}
...
completedSteps.insert(completedStep.rawValue)   // never reached
currentStep = nextStep(after: completedStep)    // never reached
```

Any attachment failure — offline, timeout, a large photo, all routine in an
orchard — leaves the step un-advanced while the server-side write already
succeeded. The operator retries, and `submit_i9_section_1` refuses because the
form is no longer `Draft` ("no Draft I-9 Form", `tools/i9.py:335`). Hard stop
with no way forward from the handset.

**Fix:** attachments are best-effort. Catch separately, surface a "photo didn't
upload, retry from Review" affordance, and advance. `uploadSignedI9` already
gets this right and says why (`OnboardingWizardViewModel.swift:656-661`) — apply
the same reasoning one step earlier.

### M5. A manager can onboard an employee but cannot file their W-4

`farm_app/app/blueprints/hr.py:137` — `@role_required('owner', 'admin', 'manager')`
`farm_app/app/blueprints/payroll.py:436` — `@role_required('admin', 'owner')`

The manager who hires someone hits a 403 on the next step of the same task.
Either widen the W-4 route or tell the manager on the onboarding success screen
who has to finish it.

### M6. farm_app's W-4 asks the user to do the arithmetic that erpnext_mcp does for them

`farm_app/app/models.py:1197` stores `dependents_amount` — a single dollar
figure — and `payroll.py:461` takes it verbatim off the form.

`erpnext_mcp` asks for **counts** (`dependents_under_17_count`,
`other_dependents_count`) and computes the credit from the form edition
(`w_4_form.py:25-56`). That difference is exactly why v0.48.1 could fix the 2026
$2,200-per-child change centrally, with a patch that restated already-filed rows.
In farm_app the same change is a memo to whoever types the number.

Two systems, same federal form, opposite designs. The erpnext_mcp one is right.

### M7. Payroll preview against an open clock log is non-deterministic

`farm_app/app/blueprints/payroll.py:292` — `log.clock_out or datetime.now()`.

A worker still clocked in produces a different preview on every refresh, and
`calculate_commit` silently freezes whatever partial shift happened to be running
when the button was pressed. There is no warning on the preview screen that any
log is open.

### M8. The temporary password is shown once, in a flash message, and is unrecoverable

`farm_app/app/blueprints/hr.py:246-256` generates a BIP-39 mnemonic and flashes
it. If the manager navigates away, misses it, or the request 500s later in the
same handler (see **C2**), there is no way to retrieve or regenerate it from the
UI.

---

## Low — cleanup and consistency

### L1. Stale doc comments now contradict shipped routes

- `OnboardingAPI.swift:221` — "THERE IS NO SUCH ENDPOINT" (there is; v0.47.1).
- `OnboardingModels.swift:534-542` — "The mobile surface publishes no full I-9
  read … `tools/i9.get_i9_form` … is an MCP tool only." It is a mobile route now
  (`routes.py:148`).

These are load-bearing comments in a codebase that documents its reasoning
heavily — leaving them stale is worse here than it would be elsewhere.

### L2. Two phone number fields, both collected, differently protected

`farm_app/app/models.py:371-372` — `phone` (`EncryptedText`) and `phone_number`
(plaintext `String(20)`). `hr.py:291-292` writes both from two separate form
inputs. Nothing in the review indicated which one downstream code reads. One
number, one column.

### L3. `multiple_jobs` is collected on both W-4s and consumed by neither

- `farm_app`: `models.py:1194` stores it, `payroll.py:459` writes it,
  `calculate_federal_withholding` never reads it. Step 2(c) changes the
  withholding table — ignoring it under-withholds two-income households.
- `erpnext_mcp`: forwarded through `submit_w4` (`api/mobile.py:1566`) and stored.
  Worth confirming `withholding.py` actually branches on it.

### L4. `filing_status` is computed and unused in `calculate_full_paycheck`

`farm_app/app/utils/payroll_calcs.py:381` assigns it; the federal call re-reads
it off `w4` itself. Only the state call uses the local. Harmless, but it reads
like the federal path honours it when it doesn't.

### L5. `Employee.pay_frequency` is collected at onboarding and never used

`hr.py:308` writes it; `calculate_full_paycheck` takes frequency from
`pay_period.period_type` (`payroll_calcs.py:369`). Either the per-employee field
means something or the form should stop asking.

### L6. Filing-status vocabularies differ across the two systems

farm_app uses `single` / `married` / `head_of_household`
(`payroll_calcs.py:28-54`). erpnext_mcp uses
`Single or Married Filing Separately` / `Married Filing Jointly` /
`Head of Household` (`OnboardingModels.swift:490-494`). Nothing translates
between them today because nothing crosses — but anything that ever does will
need a mapping table, and erpnext_mcp's spelling is the one on the federal form.

### L7. `api.py` carries ~140 lines of commented-out routes

`farm_app/app/blueprints/api.py:254-400` — dead route definitions behind `##`.
Also a duplicate live registration: `/commodities/<id>/bbch` is declared twice
(lines 82 and 105); Flask will take the first and the second is dead code.

### L8. Debug `print` left in the onboarding handler

`farm_app/app/blueprints/hr.py:325` — `print('try to save photo')`.

---

## Suggested order for a morning session

| # | Item | Why first |
|---|---|---|
| 1 | **C1** | Silent loss of federal compliance evidence. Everything else is visible; this one isn't. |
| 2 | **C2**, **C3** | Two unhandled 500s on the first screen of the hiring flow. ~20 lines together. |
| 3 | **H8**, **H6** | One-line-ish payroll correctness fixes with clear right answers. |
| 4 | **H5** | Overtime threshold — needs a per-workweek split, so budget real time. |
| 5 | **M1**, **M2**, **M3** | Three small iOS defaults that put wrong data on federal forms. |
| 6 | **H4**, **H7** | Bigger: a year-keyed constants table for farm_app, mirroring what
`w_4_form.py:25-28` already does. Not a morning on its own. |
| 7 | **H3**, **H2**, **H1** | Only urgent if the signer roster is about to be used. Confirm whether any site has added a row. |

**H9** (no work-readiness gate) and the farm_app / erpnext_mcp duplication
question are both design decisions rather than bugs. They want a conversation,
not a patch.

---

## What this review did not cover

- The Nostr protocol layer, bucket vision pipeline, IoT, satellite, and spray
  modules in farm_app.
- `farm_app_scanClock`'s iOS ↔ farm_app contract. The brief named the
  FarmOpsKit/MobileAPI/OnboardingAPI contract, which is fafo_ios ↔ erpnext_mcp,
  and that is what was checked. farm_app has its own `field_ops_api` surface
  (~30 routes) with a separate client that was not reviewed.
- Runtime verification. Nothing was executed; every finding above is read from
  source. **C1** in particular is worth confirming with a single curl through
  the funnel to `/api/method/upload_file` carrying only `X-FarmOps-Token`,
  before any code is changed — it should return the `/me` page with HTTP 200.
