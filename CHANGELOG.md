# Changelog

All notable changes to this project are documented here. Versions follow
[semantic versioning](https://semver.org).

## 0.64.0 — 2026-08-12

**What happened to Ana, and which shift the work was done on.** The shift has
anchored compliance since v0.19.3 and collected its own weather since v0.19.4,
and could still not answer a question about one person on it or say which shift
a task was done on. Both gaps had the same shape: the data was already stored
and nothing read it.

- **`get_shift_crew_timeline`** — every crew member's own envelope. Their span,
  the weather **they** stood in, the water/shade/rest events inside it, and what
  their own hours entitle them to. A picker who joined at 09:40 was absent for
  the hour it was hottest and for three of the five water breaks; a heat record
  scoped to the crew says otherwise, and that record is read in an
  investigation. `present_at_shift_first_crossing` is the field that says so.
- **Nothing is interpolated.** `minutes_bracketed_by_crossings` is a bracket
  between two samples, not a sum of exposure, and `sample_gap_minutes` reports
  the real cadence so a loosely-bracketed archive reconstruction cannot be read
  as a live quarter-hour timeline. `breaks` is null without a policy, not zero.
- **`Farm Task` and `Farm Task Assignment` gain `farm_shift`**, and they are not
  the same field: the task's says which shift the work was **raised for**, the
  assignment's which it was **done on**. Settable at creation, dispatch,
  clock-in and completion; **inferred** at clock-in from the one open shift the
  worker is rostered on, and only when there is exactly one — two would mean
  guessing which crew's compliance record the evidence lands on. A shift at
  another company is refused by name.
- **A completion's evidence reaches the shift's own timeline.** One
  `Task Completed` event — its own type, not `Other` — carrying the timestamp,
  the worker, the signature file and the weather **as it stood at or before** the
  work finishing. It points at the assignment rather than copying its
  photographs. One event per assignment; a replay writes nothing.
- **`get_shift` reports `farm_tasks`** (the work still open on the crew) and
  `list_dispatch_board` takes `farm_shift`, reporting `by_shift` and
  `not_anchored_to_a_shift`.
- **The calendar looks again at the moment the world changed.**
  `refresh_compliance_alerts` grew an `alert_types` allowlist with the same
  raised-nothing-**dismissed-nothing** promise its `regime` filter has, and
  `complete_farm_task` calls it for the rule that raised the task plus every
  rule reading the register the completion wrote to — nobody links the task to
  the rule, the **record** is the link. It is the sweep called sooner, not a
  shortcut around it: the rule's own condition still decides, and a completion
  against a condition that is still true leaves its alert standing.

### Fixed

- **`log_shift_break` failed on every call since v0.58.0.** It passed the whole
  request dict to `as_float` where the value belonged, so `float({...})` raised
  and the refusal quoted the caller's entire payload back as the offending
  "number". The tool was unreachable.
- **`shifts.EVENT_FIELDS` never fetched the six break columns**, which was not a
  missing key but silent wrong arithmetic in three places: `describe_event_row`'s
  break branch was unreachable, an **Individual** break counted as taken by the
  whole crew because `applies_to` was absent and defaulted to Crew, and
  `duration_minutes` was absent and read as zero — so paid and unpaid break
  minutes were **zero on every shift**, including in `get_shift_production`.

Catalogue: **413 tools, 189 read**. Migration is `bench migrate`; nothing is
backfilled and nothing needs to be.

## 0.63.1 — 2026-08-12

**The same argument drop, pointed the other way.** v0.62.0 published three
aliases because `routes.bind` reduces a request body to the keys the answering
signature names, so the handset's spellings could not otherwise arrive. It
declared one spelling per door, which left the identical drop open in the
opposite direction: `include_full` sent at `list_housing_units` vanished exactly
as `assignable_only` sent at `list_available_housing` did, and
`housing_unit`/`check_in_date` sent at `create_housing_assignment` vanished
exactly as `unit`/`assigned_date` sent at `assign_housing` did. A dropped filter
is a list of cabins nobody can be put in; a dropped cabin is a hire refused for
want of a field the phone sent, naming an argument the caller never heard of.

All four methods now declare **both** spellings, reconciled in one place in
`api/mobile.py`.

- **Neither door's default moved.** `include_full` and `assignable_only` are one
  flag in opposite senses with opposite defaults — "where can somebody sleep"
  against "show me the camp" — and a body naming neither spelling still gets the
  answer its door has always given. Every handset in the field sends neither or
  exactly one, so nothing already deployed changes answer.
- **Refusals quote the spelling the body used.** A phone told `check_in_date is
  required` by a method it called with `assigned_date` is a phone whose operator
  cannot act on the sentence.
- **A body saying both to contradictory effect is refused rather than resolved**,
  with both keys quoted and nothing read or written.
- `assign_housing` still declares neither `allow_multi_occupancy` nor `company`,
  on purpose: it passes the barracks flag on the caller's behalf under capacity
  and refuses at it, and a spelling alias is not the place to hand a phone the
  argument that changes that answer.

## 0.63.0 — 2026-08-12

**The two ends of a signature, and the wage floor somebody can actually set.**
Three pieces of work: the page a signer has to be shown before they sign, the
tamper-evident copy produced after they do, and a minimum wage that is a row in
the Desk rather than a constant in a module.

### The presentation step — `get_document_preview`

`API_CONTRACT.md` §17.5 named this a server-side gap and said the fix is one
route. Step 1 of the signing evidence chain is that the signer SAW the form, and
the app could not show it to them: `render_i9_pdf` and `render_w4_pdf` answer
with a **private** `file_url`, the handset authenticates to the FarmOps sidecar
with `X-FarmOps-Token` rather than to Frappe, and a private URL is a login page
to it. So the app could show the *completed* form after signing — those bytes
travel in `submit_form_signature`'s answer — and not the blank one before.

The page now travels as base64 under `content`, `content_base64` and `base64`.
Three spellings of one string, because a client written against the contract,
against this app's file tools or against the signature answer should all read
the same page.

**It draws once and does not silently redraw.** A form with no page gets one —
which on a fresh I-9 is every time, and without it the route would answer "no
page" on the exact case the pad opens for. A form that has one is handed back as
it stands, with `stale` saying whether the record has changed since it was drawn
and `refresh=true` available for a caller who needs certainty. Re-rendering on
every screen open would repoint `generated_pdf` a dozen times a hire day, and
that field is the copy somebody printed and had signed.

`signature_boxes` comes back with it: what can be signed, which boxes already
carry a signature, and the **verbatim** attestation for each. A pad that
discovers Section 1 is taken by submitting to it is a pad that discovers it with
the worker standing there.

### The tamper-evident copy — `seal_signed_document`

Flattening a form has made it tamper-**resistant** since v0.51.0: no annotation
to delete, no field to clear. That says nothing about **detection** — an edited
page looks exactly like an unedited one. Two things fix that, and the release
adds both:

- **A verification page**, appended, naming for every signature on the form the
  signer, the badge scanned, how identity was established, the moment, the
  device, the coordinates and the fingerprint of the record as it was presented.
  Drawn from the Signing Evidence rows rather than from a second copy of the
  same facts.
- **A SHA-256 of the finished file**, recorded on those rows. It cannot be
  printed on the file it is taken over — printing it would change the bytes — so
  the page carries the *document* fingerprint and the register carries the
  *file* hash. Two hashes, two questions: "is this the form they saw" and "is
  this the file we produced".

`submit_form_signature` takes the step automatically and reports it under
`seal`, following `include_pdf`. **Never fatally**: a bench without reportlab
gets `sealed: false` with the reason and a signature that is on the federal
record regardless, which is the ordering rule the whole signature path keeps.

**An unsigned form is refused.** A verification page on a form nobody signed is
an official-looking appendix that vouches for nothing, and somebody would file
it. A signed form with **no** evidence row — every signature collected before
v0.60.0 — is sealed anyway, with the page saying in as many words that the
identity, device and location were never captured and cannot be reconstructed.

**Signing Evidence grows three columns** — `sealed_pdf`, `sealed_pdf_hash`,
`sealed_at` — and they are the only thing on an append-only row that moves. They
name an artefact produced *afterwards* rather than a fact about the signing, and
they legitimately change: a Section 1 attestation sealed alone in July appears in
a two-signature sealed copy once the employer signs in August. Written with
`db.set_value`, so the controller's refusal stays absolute for every path that
could revise what the row says about the signature. No sealed copy is deleted.

### The wage floor, configurable

`calculate_full_payroll` has declared `min_wage_rates` since v0.49.0 and
**nothing ever supplied one**, so every run on every install used the table
compiled into `payroll_calc` and an Oregon rate change was a release. It has
declared `min_wage_regions` for just as long and nothing supplied one either, so
Oregon's Portland metro rate — the highest of the three, and the one an orchard
inside the urban growth boundary is on — was unreachable from any tool here.

**State Tax Configuration** grows `minimum_wage`, `minimum_wage_non_urban` and
`minimum_wage_portland_metro`, per company, state and tax year. A **zero means
"not set here"**, never "the floor is zero": currency fields default to 0 on
every row, and treating one as an override would let a site that filled in
nothing but its SUTA rate drop the floor to nothing for its whole payroll. A
site that configures nothing pays exactly what it paid before this release.

**Farm Salary Structure** grows `min_wage_region` — Standard, Non-Urban or
Portland Metro. It is on the structure rather than on the shift because it is a
fact about where somebody *works*, and a crew moving between blocks inside one
region must not have its floor move with it. A region a state does not define
falls back to that state's standard rate, so a Portland-metro worker who spent a
week over the river is owed Washington's $16.66.

**The independent cross-check now reads the same table the engine paid from.**
Without that, a farm that raised its own floor would see every topped-up slip
flagged as below the minimum.

### The floor, made visible before anybody posts

`preview_payroll` carries a `minimum_wage` block — the region, the rate table it
used, which states took their floor off their own configuration, the makeup and
the sentence explaining it — and names the makeup on its summary line. It has
been in the answer since v0.49.0, in a nested key nobody opens.

`compliant` and `makeup` are deliberately two different facts. The floor is
PAID, so a slip that needed makeup was paid lawfully; the makeup is the number
that says the **rate** is set below what the hours are worth, and it recurs every
period until somebody changes it. Conflating them would either report every
underpriced bucket as a violation or hide it entirely.

`calculate_payroll` now writes `earned_gross` and `minimum_wage_makeup` onto the
slips it stores — two columns the doctype has carried since v0.49.0 that this
path never filled, so a stored row could not answer "how much of this was
makeup" — and reports the run's floor picture on the draft somebody is about to
submit: who was topped up, what it cost, and separately anything still below the
floor, which on a piece-rate or hourly slip usually means a shift carries no
`work_state` and so no legislature and no floor.

### Permissions

`W-4 Form` is granted read and write to **Farm Manager**, which is the v0.59.3
finding restated for the other federal form. `signatures._require_write` gates
every signature on Frappe's own `has_permission`, so the W-4's signature box,
`seal_signed_document` and `get_document_preview` were all closed to the role
that runs onboarding. It is **not a widening**: `w4.submit_w4` inserts with
`ignore_permissions` behind `require_hr_role()`, and `HR_ROLES` names Farm
Manager — the role has been creating and editing W-4 records on every site since
the wizard shipped, with no DocPerm saying so. `Tax Form` is deliberately not
granted: a 941 is signed by an officer of the employer, and this app keeps no
register of officers.

## 0.62.0 — 2026-08-12

**Seven routes the iOS app already calls and this server answered 404.**
`MobileAPI.swift` was audited against v0.61.0 on 2026-08-12. Three of the seven
exist here under a different name; four did not exist at all. Every one of them
is a path a shipped handset posts to today, on a screen a foreman is standing in
front of.

**The three name mismatches are published as ALIASES, not renames.** A rename
fixes the next TestFlight build and breaks every phone already in an orchard —
the same promise `collect_signature` kept in v0.57.0. Each alias delegates to a
private function the older wrapper now also calls, so the camp rules, the
capacity ceiling and the entity scoping cannot come to differ between the two
names.

**An alias is not a bare forward, because `routes.bind` reduces a body to the
keys a signature declares.** Two of the three needed a parameter change to be
correct rather than merely reachable:

- `list_housing_units` declares `assignable_only` where `list_available_housing`
  declares `include_full`. Sent at a signature that does not name it, the flag
  does not error — it vanishes, and the camp list comes back full of cabins
  nobody can be put in. The sense inverts with the name, so the default flips:
  the ordinary call now asks for the whole camp, full and condemned units marked
  and greyed out with the reason printed.
- `create_housing_assignment` declares `unit`, `assigned_date`, `company` and
  `allow_multi_occupancy` where `assign_housing` declares `housing_unit`,
  `check_in_date` and neither of the last two. Under the old signature the body
  the app posts arrives with no cabin and no date in it.

**`allow_multi_occupancy` is forwarded and is still not an override.** The
capacity ceiling is checked before the write on both doors and no argument lifts
it. What the flag decides is the case UNDER capacity: a bunk room that really is
shared, said out loud, versus a foreman tapping the same cabin twice. The older
wrapper cannot receive it and passes true on the caller's behalf; the new one
defaults to refusing the second body, naming who is already in the cabin.

**`set_employee_org_fields` and `set_employee_contact_fields`** are the two
writes the hiring wizard has never had. Thin subsets of `update_employee`, so the
HR role gate, the company scoping and the Link validation are the tool's. An
unsent field is left alone and an empty one is not an answer — both steps are
shown to returning workers, and a call that wrote `""` for an untouched box would
clear a column somebody set in the office last season.

**`employee.WRITABLE` grows from nineteen to twenty-two** — `current_address`,
`person_to_be_contacted`, `emergency_phone_number`. The contact method names five
fields and the allowlist carried two. An emergency contact is the same kind of
fact as the cell number beside it: how somebody is reached, and by whom, on the
day it matters. None of the three is payroll, tax or banking.

**`list_attachments` and `get_attachment_content`** have existed as MCP tools
since v0.1 and were never routed, so six routes could FILE documents against an
Employee and none could ask what was already there. They carry three gates the
tools cannot run themselves: a closed list of parent doctypes, the HR role on the
personnel ones, and — for the content read — a re-check of the parent the File is
actually attached to, because a File docname is a global handle. An unattached
file is refused outright. The bytes travel rather than a `file_url`, because
every file this app writes is private and the handset authenticates to the
sidecar rather than to Frappe.

**`HR User` joins Farm Manager's companion roles.** `tools/files.py` consults
Frappe's own permissions, deliberately, so a Farm Manager holding only this app's
roles could file a licence photograph and not read the folder back. Named as a
companion role rather than granted here: a Custom DocPerm on `Employee` would
make Frappe ignore every standard permission that doctype has, for every role on
the site. Re-run `create_mobile_user(..., update_existing=true)` for a manager who
needs it.

See [`RELEASES/v0.62.0.md`](RELEASES/v0.62.0.md).

## 0.61.0 — 2026-08-12

**Collect once, use everywhere.** Until this release a piece rate was a number on
one worker's Farm Salary Structure. $1.25 a bucket was typed once per picker, a
season's raise was a hundred edits nobody could audit, and *what does this farm
pay for a bucket* had no record to answer it from.

**Two registers replace that, and they are read at opposite moments.** A
**Piecework Rate** — `(company, activity)` → rate per unit, from a date — is read
on **every payroll run**, for every worker whose structure names no rate of its
own; raise the row and the next run pays the new rate. A **Position Wage
Default** — `(company, designation)` → hourly rate, from a date — is read
**once**, when a salary structure is created, and never reaches back through it.

**That asymmetry is deliberate.** A piece rate is a property of the WORK: a bucket
is a bucket, and the operation pays what the operation pays. An hourly wage is a
property of the EMPLOYMENT — it is what a person was hired at, it is what a wage
claim asks about, and a table that could silently restate somebody's agreed rate
for a period already worked would be a table that rewrites history. So the hourly
default is copied ONTO the structure and the piece rate is inherited BY it.

**The lookup order is the only thing payroll asks:** the structure's own
`base_rate` where it is greater than zero, then the active company rate for that
employee's company and activity, then **refuse**. The structure wins because it is
the more specific record — a company table cannot know about a rate somebody
negotiated with one person.

**A missing rate is an error and not a zero,** and that is the failure this
release exists to make loud. A piece-rate worker paid at a rate of nothing earns
nothing at the rate, and what they are then paid is the minimum wage makeup — a
real, correct number. The slip balances, the run reports no failure, and the only
symptom is a makeup figure that looks like a rate set too low rather than a rate
that was never set at all. A batch run does not abort over it: the worker lands in
`employees_missing_piece_rates` and everybody else is paid, the posture
`run_payroll_for_period` has taken towards a missing salary structure since
v0.35.0. A single-employee preview has nobody else to hold up, so there it raises.

**Which activity, when the hours do not say.** No bucket row records what KIND of
piecework it is, so the activity comes from `Farm Salary Structure.piecework_activity`
— new, optional, and the same vocabulary `ML Model` uses. Where a structure names
none, one unambiguous company rate is used and several are refused *by name*: a
company with one rate in force has already answered the question and a company
with three has not. Matched case-folded, with spaces and hyphens read as
underscores, so the Desk and the iPad spell the same activity.

**Raising a rate is a new row, not an edit.** The latest `effective_from` covering
a date wins, so adding a row from 1 June leaves the old one paying the periods it
already paid. **There is no delete on either table** — `is_active=false` takes a
row out of every future lookup and leaves it readable. Two ACTIVE rows starting
the same day for the same activity are refused: that is two answers to one
question, not a raise.

**Eight tools, four reads and four writes**, plus `piecework_activity` on
`create_salary_structure`, `effective_rate`/`rate_source` on `get_salary_structure`,
`inheriting_company_piecework_rate` on `list_salary_structures`, and two opposite
lists on every period run — `piece_rates_from_company` (the fallback working) and
`employees_missing_piece_rates` (the fallback finding nothing). A Piece Rate
structure created with `base_rate` 0 is checked at creation that the inheritance
resolves, so a structure that would fail on payday fails in front of the person
creating it.

**Farm Manager reads and writes both tables; Compliance Officer reads them.**
Neither may CREATE: no name appears on either row — this is the price list, not
somebody's pay — but adding a row is how a raise happens, and one insert changes
what the whole company's next payroll pays.

Run `bench --site <site> migrate`. Nothing changes for a site that creates no
rows: every existing structure names its own rate, and the fallback is simply
never reached.

## 0.60.0 — 2026-08-12

**An auditor's second question.** The first is *was it signed*, and this app has
answered it since v0.55.0 — the image, the moment and the address, which is what
8 CFR § 274a.2(h) asks for. The second is **how do you know it was him**, and
until now the honest answer was that a phone said so.

**`Signing Evidence` — one row per signature event**, across every form that
carries one: who signed and in what capacity, which badge was scanned to prove
they were standing there, which account made the call, the device, the
coordinates, the address, and a hash of the record as it stood when they were
shown it. Not six columns on the I-9: one form carries three signatures by two
people in two capacities, and per-form columns would be that set again on every
form that grows a signature line.

**Append-only, and there is no tool that creates one.** The controller refuses
every write after the insert, `in_create` takes away the Desk's New button, and
the signature path is the only thing that writes a row — a tool that could add
one would be a tool that could manufacture an identity check that never happened.
A replaced signature appends a row naming the old one in `supersedes`; the old
row keeps saying what it said.

**Identity is refused, not recorded, when it fails.** A badge that resolves to
somebody other than the worker whose form is open stops the call before the image
is stored — either the wrong person is at the pad or the wrong form is, and a
signature filed across that gap would attest under one person's penalty of
perjury to another person's document. `verification_method: "Badge QR"` with no
badge attached is refused rather than recorded: the column would look like proof
and hold nothing. No badge at all is not an error — the row says `Unverified`,
and the register reports how many of those it holds.

**The capacity comes off the box, not off the caller.** Section 1 is a worker
attesting and Section 2 is the employer attesting it examined that worker's
documents. A stated `signature_role` is checked against the box, never believed
over it.

**The authorized-signer check now asks the whole question.** The roster answers
which form; the entity is a Company User Permission checked elsewhere, so a
signer scoped to a different farm used to be refused with a sentence about
`write` permission — true, and it sends an operator to the wrong register.
`signers.authorized_signer_for_company` asks both and names the actual gap. The
roster does not grow a company column: on a multi-entity family operation the
same people sign for all of it, and three copies of one list means the copy
somebody forgot to update is a signature refused in the packing shed.

**The document hash is taken before the signature is written**, and covers the
columns that HELD SOMETHING when the record was presented — not the signature
columns, the rendered PDF or the workflow status. So the employer completing
Section 2 in August does not make the worker's July attestation read as tampered,
while anything changed or erased does. The field list is stored beside the hash,
because the rule cannot be re-derived once the empty columns are full — and it
doubles as the answer to *which parts of this form does this signature vouch
for*.

**`submit_form_signature` declares five more arguments** — `signer_badge`,
`verification_method`, `device_id`, `gps_lat`, `gps_lon`. The last two are in
§14.2 and have been dropped by `routes.bind` since v0.57.0, because the server
had nowhere to put a location; it has one now. `signed_on` is still dropped — a
handset that could set the attestation's timestamp could backdate it. The answer
carries `evidence`, `evidence_status` and `evidence_note`, reported rather than
silent. The idempotent retry writes no row and says so.

**Two reads and no writes**: `list_signing_evidence` (by document, signer, badge,
capacity, company or date range, with `unverified_count` reported separately) and
`get_signing_evidence` (one event in full, hash re-checked on every read, plus
`superseded_by`). Farm Manager and Compliance Officer read the register; System
Manager and HR Manager hold it fully; **no role in this app gets write access**,
including the two that read it.

Run `bench --site <site> migrate`. Nothing changes for a site that sends no new
arguments — signatures are collected as before and each writes an `Unverified`
row, which is a true statement about one captured without an identity check.

## 0.59.3 — 2026-08-12

**A Farm Manager could not sign the half of the I-9 that is legally theirs.**
Section 2 is the employer attesting that it examined the documents the worker
presented — 8 CFR § 274a.2(b)(1)(ii) puts it on the employer or its authorised
representative, within three business days — and on this operation that is the
person in the packing shed with the phone. `signatures._require_write` gates
every signature on Frappe's own `write` check rather than on a role list of its
own, which is the right design and is exactly why the gap surfaced as a sentence
about a column: *"this account may not write I-9 Form I9-2026-0001, so it may
not put a signature on it."* The check was correct and `roles.py` was
incomplete. Farm Manager now holds **read and write** on I-9 Form.

**Not `create`, and not `delete`.** An I-9 begins with the worker's Section 1,
raised on the hiring path — a manager who could raise one could raise one for
somebody who was never hired. Destroying one is the retention schedule's
decision and nobody else's: § 274a.2(b)(2) keeps the form three years from hire
or one year from separation, whichever is later. Both stay with System Manager,
where the doctype's own permissions put them. The authorized-signer roster is
untouched and is still the second gate: `write` decides whether an account may
edit the record, `tools/signers.py` decides whose name may appear as the
employer's representative, and Section 2 asks both.

**Which I-9, not just which role.** `I-9 Form.company` is a required Link, so a
manager's Company User Permission already scopes every list, read and — because
the check is made *with* the document — every signature. What did not hold was
the fallback: `_require_write` answers a permission-cache failure with a
doctype-level check, which knows nothing about whose record it is. Harmless
while no mobile role held `write` on any federal form; now the difference
between "may this role sign an I-9" and "may this manager sign THIS one".
`_require_entity` asks the second question against the `company` column, and a
form belonging to an entity the account is not scoped to is refused by name. An
account with no User Permission stays unrestricted — Frappe's rule, kept so an
operator's own login works; the strict reading lives on the mobile door, where
`guard.require_scope` refuses a phone with no entities outright.

**The mirror no longer aborts on a role the site does not have.** I-9 Form ships
a DocPerm for `HR Manager`, which comes from `hrms`; `Custom DocPerm.role` is a
Link, so on a bench without it the mirror raised `LinkValidationError` partway
through — and a half-written mirror is the precise failure `_mirror_standard_perms`
exists to prevent, because the rows it did write are enough to make Frappe
discard every standard DocPerm the doctype had. System Manager would have lost
the I-9 register during a migration. Unresolvable rows are skipped instead: a
permission held by a role no site has is held by no user.

## 0.59.2 — 2026-08-11

**Not one bucket entry has ever synced from a handset, and it is v0.59.1's bug
at the other boundary.** `BadgeAPI.payload` stamps every capture with an
`ISO8601DateFormatter` set to `.withInternetDateTime` in UTC, so the wire
carries `2026-08-11T07:12:00Z`; Bucket Log Entry's `timestamp` is a Frappe
`Datetime`, which is a MariaDB `DATETIME`, which answers that string with
`OperationalError (1292, "Incorrect datetime value")`. The same `T` and the same
`Z` that failed the model pull, on the path that carries piece-rate.
`api/mobile._bucket_entries` converts before the tool sees it.

**The failure was invisible from both ends, which is why it lasted.**
`bucket_bridge.validate_bucket_entry` READ the string quite happily — `_parse_dt`
splits the `T` and drops the `Z` — so every entry passed every check this app
makes and then died at the insert. The standalone suite agreed, because
`test_ios_contract`'s bucket case fed `f"{today()} 07:12:00"`, a Frappe Datetime
string in the shape a Desk import has and no handset has ever produced. The
suite now drives the format the phone actually sends, offset case included.

**`as_mariadb_datetime` moved to `erpnext_mcp/datetimes.py`.** It was written in
`model_registry.py` because the failing case was a trained model's
`training_completed_at`, but the rule was never a fact about ML models — it is a
fact about every boundary where something that speaks JSON writes a timestamp
into a `Datetime` column, and this app has two. `model_registry` re-exports the
name, so its own callers are unchanged. A value that will not convert is passed
through unchanged rather than blanked, so the refusal names the value instead of
reporting a field the phone did send as missing.

**One capture the column refuses no longer costs the batch.** The `doc.insert()`
handler in `sync_bucket_entries` named `UniqueValidationError` and nothing else,
so any other refusal at the write left the loop and came back as a 500 with no
per-entry detail — and a device that retries a failed batch resends the poison
entry with the good ones behind it, forever, which is the second half of why an
iPad had 31 captures queued. Other failures now land in `invalid[]`, the channel
this endpoint already promises and `BucketSyncResult` already decodes, behind a
savepoint so skipping one entry does not take the transaction with it. The
duplicate-race branch is unchanged in behaviour and is now matched by class name
rather than through `frappe.exceptions`, which the standalone double does not
implement — meaning that branch had never once been exercised locally.

**`capture_mode` and `auto_verdict` are still dropped, now knowingly.** The app
sends both on every row so the farm can answer "how many of this season's
buckets did a model look at"; Bucket Log Entry has no column for either. Neither
is an input to pay, so nothing is owed a picker while they are unstored. The
discard is documented in `_bucket_entries` rather than fixed here — two new
fields is a doctype change with a patch behind it, and not something to bundle
with a datetime fix.

## 0.59.1 — 2026-08-11

**`pull_model_from_vv` failed at the save, after the model had already come down
the wire.** Volume Vision writes `training_completed_at` into its manifest the
way every JSON producer on earth writes a timestamp — ISO 8601,
`2026-07-08T02:38:43Z` — and a Frappe `Datetime` column is a MariaDB `DATETIME`,
which answers that string with `OperationalError (1292, "Incorrect datetime
value")`. The `T` and the `Z` are the whole problem; the instant was always
fine. `model_registry.as_mariadb_datetime` converts before the write, and
`reconcile_bundle_manifest` calls it.

**An offset is applied, not discarded.** `2026-07-08T04:38:43+02:00` is stored
as `2026-07-08 02:38:43`, so the column holds one zone for every bundle rather
than whichever zone the training box happened to be in. A `DATETIME` has nowhere
to put a zone, and the alternative — keeping the wall clock and dropping the
offset — files two timestamps two hours apart as the same moment. A value with
no offset is taken as written, because there is nothing else it could mean.
Fractional seconds are dropped, a date with no time becomes midnight, and
anything unreadable leaves the field unset with a warning rather than failing an
attach that has otherwise succeeded.

**The standalone double now refuses what MariaDB refuses.** This bug passed 6718
local tests because the in-memory `frappe` stored whatever string it was handed
into a `Datetime` field. `Document._validate_datetimes` closes that the same way
v0.16.1's `_validate_selects` closed the Kanban-column class: the `T` separator
is tolerated (the server tolerates it), a zone designator is not, and the error
names the column and the likely cause. **The rest of the suite passes it
unchanged** — every other datetime this app writes was already in the column's
own format — so the check costs nothing and makes the class of bug catchable
rather than the instance. It is deliberately a second implementation of the
rule rather than a call into `as_mariadb_datetime`: a double that used the app's
own converter would agree with the app and prove nothing.

## 0.59.0 — 2026-08-11

**A model's labels and a model's weights have never travelled together, and
nothing noticed when they disagreed.** `class_names` was typed onto an ML Model
record at registration; the weights were exported from Volume Vision separately;
a phone cached its own copy of the list months ago. Three lists, no check, and
the failure is silent — output index 2 keeps meaning `bucket` on the record and
`lip` in the model, and inference goes on returning confident numbers against
the wrong names. That is worse than an error, because nothing in a log says it
happened.

**A model bundle is one zip carrying `model.mlmodel` beside a `manifest.json`,
and `manifest.json.class_names` is now the only list anything reads.** Volume
Vision writes it at export time, in model-output-index order, from the same
training config the weights came out of. This release is ERPNext's half —
Phase 2 of the model bundle pipeline; the export itself is Phase 1 on the
training server and the iOS bundle loader is Phase 3.

**`pull_model_from_vv` is the whole manual procedure as one call.** Getting a
trained model onto a phone took a laptop, `curl`, a base64 encode and a
`bench console`, every step forgettable and the one that mattered — do these
labels match these weights? — done by nobody. The new tool asks Volume Vision's
new `/training/models/<uuid>/bundle` for the zip, attaches it, and reconciles
the manifest onto the record. `source_server` and `source_uuid` come from the ML
Model record unless passed; **host and port are read from the record, never
assumed**, because a Volume Vision on an operator's Umbrel is on whatever port
they put it on.

**The bundle endpoint is asked first and the original one is the fallback.**
`/training/models/<uuid>/download` is unchanged and stays forever — LiDAR
Capture and BucketLog pull raw files through it and are untouched by any of
this. When `/bundle` answers 404/405/501, which is exactly what a Volume Vision
without the Phase 1 export deployed answers, the pull falls back to `/download`
and **says so** in `warnings` and in the summary: a raw file has no manifest, so
`class_names` stay whatever somebody typed and the record's new
`manifest_source` field records that. `allow_raw_fallback=false` refuses rather
than taking it.

**`attach_model_file` reads the bytes, not the file name.** Four bytes of `PK`
magic decide whether an upload is a bundle or a raw model — the name is whatever
a browser called the download, and a `.mlmodel` that is really a zip fails hours
later as a CoreML compile error on a handset in an orchard. A bundle's manifest
supplies `class_names`, `metrics`, `model_kind` and the rest; a raw file behaves
exactly as it did in v0.52.0. A zip that will not open, or one with no
`manifest.json`, is refused with nothing written.

**The bundle wins, and says what it overwrote.** When a manifest's `class_names`
disagree with the record's, the record's are replaced, the previous list comes
back in the result's `previous` block, and the warning names both. Three things
the bundle does NOT get to settle: `version` and `piecework_activity` are this
record's identity rather than training's to assign, and a manifest naming a
different `source_uuid` than the record's is **refused** — that is a different
trained model, and attaching it would make every iOS cache keyed on the uuid
wrong. `force=true` is there for the operator who means it.

**Two new fields on ML Model, and no new table.** `manifest_source` carries the
sentence — *"class_names source: bundle manifest from VV training (uuid …)"* —
so somebody reading the form in a year can see where the labels came from.
`bundle_manifest` keeps the manifest whole, because it carries the two things
this doctype has no column for and an iOS app needs at inference:
`preprocessing` (input size, normalization, colour space) and `class_roles`.
Both reach a phone through `get_active_model`'s manifest under
`metadata.bundle`, so nothing has to unpack a zip to read them.

`get_model_file_chunk` serves whatever is attached and computes `total_bytes`
and `total_chunks` from the stored bytes on every call, so a bundle simply has
more pieces than the raw model it contains; its answer now carries `is_bundle`,
read from the bytes rather than the record, so a client can branch on
unzip-vs-compile from the first chunk it receives.

**This is the only tool in this app that fetches a file from another server**,
which is the shape of every server-side request forgery there has ever been.
`services/volume_vision.py` states the position: http/https only, no credentials
in the URL, no redirects followed, and a 512 MB ceiling checked against
`Content-Length` before the body is read and against the body after. The
allowlist posture is deliberately different from `validate_public_endpoint`'s —
the target here is an operator's own training box on their own LAN, so a list of
public suffixes would be exactly wrong and a hardcoded host would be a script
for one site.

Tool surface **400** (181 read, 219 write). Full standalone suite green.

## 0.57.1 — 2026-08-10

**Six routes have never been reachable from a phone, and the server could not
see it.** The funnel mounts paths one at a time with
`tailscale funnel --set-path`, so a route added to `farmops_api/routes.py` is
not published by having been added — and nothing published the ones added in
v0.54.0, v0.55.0 or v0.57.0. A probe of all 53 from outside: 47 answer, and the
six that do not are `list_onboarding_reference_data`, `list_available_housing`,
`assign_housing`, `collect_signature`, `dismiss_compliance_alert` and
`submit_form_signature`. The request stops at the proxy, so there is no log
line, no audit row and no traceback; what the worker sees is Tailscale's own
plain-text 404, which the app cannot parse into a sentence, so it shows its
generic miss — *"That task no longer exists — someone may have taken it."* — for
a signature that was working perfectly on the other side.
`scripts/mount_farmops_funnel.sh` mounts the lot and is asserted against the
route table in both directions by the test suite;
`validate_public_endpoint(probe_routes=true)` asks the same question from
outside. **Fixing this needs an operator on the Umbrel, not an upgrade.**

**The rendered I-9 and W-4 have never carried the signature, since v0.51.0.**
`render_i9_pdf` built its record from `_i9_fields()`, which does not list
`section_1_signature` or `section_2_signature` — so `_signature_captures` looked
for keys that were never in the dict and returned `{}` every time, and the page
came out unstamped and unflattened. `render_w4_pdf` had the same bug against
`signature`. Both now read their capture columns at the render. The stamping
tests pointed at the pure renderer and passed throughout; the new regression
tests go through the tools, from a capture on the record, with a real PNG.

**`submit_form_signature` returns the signed page.** A new `pdf` object carries
the completed form as base64 beside `file_url`, because a private File is a
login page to a handset that authenticates to the sidecar — the same reason
`get_employee_badge_pass` sends its bytes. It renders one where none existed,
reports rather than fails when it cannot (`pdf.available: false`), and a retry
reads the existing page instead of drawing a second. `include_pdf=false` turns
it off. iOS shows it behind a "See the signed form" button. See
[`RELEASES/v0.57.1.md`](RELEASES/v0.57.1.md).

## 0.57.0 — 2026-08-10

**The compliance calendar stops being a noticeboard.** *"I-9 Section 1 was
completed but carries no employee signature — Critical"* is a sentence a foreman
can read and not act on: the pad that fixes it lives behind another tab,
findable only by knowing which Farm Task the sweep raised and that it was a
signature task at all. A missing-signature alert now carries the **address** of
the box it is about — `{doctype, docname, signature_field}` plus the form's name,
the section's name and the attestation in the government's own words — so a row
on the calendar opens the signature pad itself. Farm Tasks raised from those
alerts carry the same object off the same alert, so the pad opened from the task
list and the pad opened from the calendar are addressed identically.

**It is derived at read time from the table the write path gates on**, which is
`tools/signatures.SIGNATURE_BOXES`. A pad can therefore only ever be opened at a
column `collect_form_signature` would accept ink into; an alert raised before
this release gets its address with no patch and no sweep; and there is no second
copy of the address to fall out of step with the rule.

**`dismiss_compliance_alert` — one alert may now be closed from a handset, and
only where the alert says so.** Compliance Alert grows `can_dismiss`, which
defaults **false** and which the nightly sweep never writes: it neither grants
the permission nor takes it away, exactly as it leaves a snooze alone. An
overdue housing inspection is not a notification — waving it off leaves a cabin
uninspected and the calendar quiet about it — so the alerts that genuinely are
stale are marked one at a time, by somebody who can see the whole picture, on
the alert's own **May Be Dismissed From The Field** box. `dismiss_alert` is
unchanged and still ungated: the operator at the desk with the source record
open in the next tab is not the caller this gate is about. The reason is
required, has to be a sentence, and lands beside `dismissed_by` and
`dismissed_on` through the same code both routes use.

**`submit_form_signature` — the route the signature pad was already posting to.**
v0.55.0 published this write as `collect_signature`, which declares `field` and
`signature_base64`; `API_CONTRACT.md` §14.2 sends `signature_field` and
`signature_image`, and `farmops_api/routes.bind` reduces a body to the keys a
signature declares — so the contract's own body reached the v0.55.0 method with
neither the field name nor the picture in it. Both methods now exist and neither
grows a second spelling of an argument. The new one answers §14.3: `form_status`,
`task_state`, and `dismissed_alert` naming the alert the signature answered — not
a claim that anything was dismissed, since the sweep does that by looking at the
record again, but the row the phone should take off the tab it was tapped from.
**It is idempotent**: a box that already carries an attestation answers success
with `already_signed: true` and nothing is overwritten, because a worker shown an
error for a signature that landed is a worker who signs again.

**Every addition is optional and additive.** An alert sent exactly as v0.55.0
sent it still lists, still sorts and still opens its detail; `can_dismiss` reads
false on a site whose column has not migrated yet, which is the safe direction
for a permission to fail.

## 0.56.1 — 2026-08-10

**The badge sheet came out of Save-as-PDF blank.** The cards were on screen and
the PDF was an empty page. Both buttons handed their printable document to a new
tab with `document.write()`, which fills in a document whose URL is still
`about:blank` — and a browser's print path renders a page by going back to its
URL, where there is nothing. The sheet and the ID card now reach the tab as a
**blob: URL**, a real resource the print preview can read a second time.

**The photographs survive the move because the document carries a `<base>`.** A
card's photo and company logo are `/files/…` and `/private/files/…` URLs, which a
browser resolves against the document's own URL, and *nothing* resolves against a
blob: one. The site's own origin is written into the document before it leaves,
so a private photo still fetches with the operator's own session. The blob URL is
released when the tab closes rather than on a timer: the print preview reads it
again, and a URL revoked mid-preview would print the same blank page.

**The seeders can now ship a fix.** v0.56.0's Client Script seeders created the
row when it was absent and did nothing when it was present, which meant no site
that had already migrated could ever receive a correction. They now recognise
three states: absent is written, **this app's own unedited copy is updated in
place**, and a copy an operator has edited is left exactly as it is — and said
out loud at `bench migrate`, with the revision they are missing, rather than
silently kept and silently stale. Ownership is decided by fingerprinting the
stored text against every revision this app has shipped, so a single character of
somebody's own still makes the script theirs. Deleting the row still declines the
button for good.

## 0.56.0 — 2026-08-09

**The badge, where somebody is already looking.** Badges were being issued and
then being unfindable. `generate_employee_badge_qr` answered with base64 in a
JSON payload — exactly right for a handset drawing a card on a screen, and
nothing at all to an HR manager who has opened an Employee form looking for a
card to print. The badge existed, the register knew it, and the two ways to see
it were an MCP call and a Bucket Log Badge Map docname nobody memorises.

**Issuing a badge now attaches it to the Employee record.** The QR lands in the
Attachments sidebar, private, one file per badge however many times it is
reprinted — the filename carries the badge ID, so a reprint replaces its own
copy and a reissue leaves the retired card's QR alone as the evidence of what
that card was. A failed attachment never loses the badge: the register row is
written first and the outcome is reported rather than raised.

**`generate_employee_id_card` is the new tool**, and it draws the card in the
print format's own layout rather than a second opinion about one — the same
markup and the same millimetres, so the card off the tool and the card off the
Desk Print button line up with the same pre-printed lanyard slot. The PDF is
**best-effort**: a card needs a photograph and a QR, so it is the one document
this app cannot draw with its own dependency-free writer, and on a bench with no
wkhtmltopdf the badge is still issued, the QR is still attached, the card still
comes back as HTML and the note says what is missing.

**Two buttons in the Desk, and both are records rather than hooks.** An **ID
Card** button on the Employee form and **Print Badge Sheet** in the Employee
list's Actions menu. `hooks.py` promises that installing this app cannot change
how a form the operator already had behaves, and `test_hooks.TheFormScripts`
holds every `doctype_js` entry to a doctype this app created — Employee is
ERPNext's. So both are Client Script rows: visible in the Desk, switchable off,
never re-created once declined, and removed by `before_uninstall` so neither is
left calling a method that has gone.

**A CR-80 print format for the badge itself**, front and back, with every image
inlined as a `data:` URI — wkhtmltopdf fetches external URLs synchronously and
hangs on one it cannot authenticate to, which is why `i9_print_format` bans
`<img>` outright. The ban was a proxy for the fetch, not the tag, so a card that
references no external resource keeps the same promise a different way.

**The fifth missing-signature rule covers the employer's own returns.**
`tax_form_signature_missing` fires on a Tax Form of type 941, OR-WR, OQ or
WA-ESD that has reached Generated, Filed or Amended with an empty signature
column. Tax Form had no signature columns at all before this release; it has five
now, and the rule is what makes them mean something.

**W-2 and 1099-NEC raise nothing and never will.** Neither form has a signature
line: the recipient copies are statements, and the penalties-of-perjury
declaration for a batch is made once, on the Form W-3 or Form 1096 transmittal
that accompanies it. A rule that raised on every W-2 would raise on every
employee every January for a box that is not on the page. Naming one at
`collect_form_signature` is refused with that sentence rather than with a list of
field names, because "Form W-2 has no signature line" ends the question and "not
a signature box" starts a search for a column that does not exist.

**The signature endpoint is now generic across form types.** `FORM_HANDLERS`
replaced two `if doctype == I9_FORM` branches with one table of resolve-and-
render pairs, so the fifth box was a row rather than a third branch. The tax
return box is the only one of the five with no roster gate: an I-9 and a W-4 are
signed by people `tools/signers.py` records, and a 941 is signed by an officer of
the employer this app keeps no register of — so it is gated by `write` permission
on the record and says so on the entry rather than leaving it to be inferred from
a blank.

## 0.55.0 — 2026-08-09

**The boxes nobody signed, and the loop that closes them.** A form can be filled
in perfectly, filed on time, and attest to nothing. Every I-9 and W-4 rule this
app shipped before today watches a CLOCK — verification is overdue, work
authorization expires, retention has run out — and none of them notices the
signature box that stayed empty. An unsigned Section 1 is the commonest I-9
finding an ICE inspection writes up, and it is a substantive violation rather
than a technical one: without the employee's own attestation the form asserts
nothing about their work authorization.

**Four new compliance rules, watching a box rather than a date.**
`i9_section_1_unsigned` and `i9_section_2_unsigned` fire on an I-9 past Draft
whose employee or employer signature column is empty — the second gated on
`verification_date` rather than status, because a form still awaiting
verification has an empty Section 2 and nothing to have signed, which is
`i9_verification_overdue`'s question and not this one's.
`w4_signature_missing` fires on any Active W-4 with no employee signature, and
**expect it to fire broadly on the first sweep after upgrade**: `submit_w4` has
never captured one, so every W-4 this app has written is, to the IRS, invalid.
`i9_supplement_b_unsigned` is the fifth permanent built-in scanner — Supplement B
lives in a CHILD TABLE, so the question folds a set of rows to a count and a
newest date, which is an aggregation rather than a filter, and it raises **one
alert per form** because two unsigned entries on one worker are one conversation.

**A missing-signature alert becomes a task held by somebody who can actually
fix it.** Sections 1 and the W-4 are signed by the WORKER, so the errand is
finding them — and those tasks go to whoever the employee `reports_to`. Sections
2 and Supplement B are the EMPLOYER attesting, so those go to an authorized
signer off the `tools/signers.py` roster, preferring whoever already examined the
documents. That is a routing an assignee EXPRESSION cannot express: it walks to a
doctype the tripped row does not mention, so `ALERT_TASK_MAP` and a rule's
`extra_parameters` gained `producer_assignee_resolver`, a closed registry of
reviewed lookups, beside the existing sandboxed expression.

**Farm Task gained `subject_doctype` / `subject_docname`.** The record a task is
ABOUT, which is not `location` — a location is somewhere a worker can be sent and
is what the pool listing filters on, and an I-9 is not a place. Written for
every generated task, not only the signature ones. Tasks raised by these rules
are titled *"Collect I-9 Section 2 signature for Juan Lopez"* rather than
*"… — I9-2026-0043"*, because a docname is the right subject for a cabin and the
wrong one for a person.

**`collect_form_signature` is the other end of the loop.** It attaches a capture
to one of four boxes — the list is closed, because an endpoint that wrote an
image into any column somebody named would be an arbitrary write — closes the
Farm Task that asked for it, and regenerates the form's PDF so the printable copy
and the record do not disagree about the one fact somebody would print it to
prove. Published on the mobile surface as `collect_signature`.

**It takes base64, which `attach_signed_i9` refuses to,** and the two are
answering different questions. That one files a SCAN OF A PAGE: megabytes, taken
on a camera, chunked because a link that drops halfway through eight megabytes
has to be resumable. This one carries what a finger drew on glass — a few
kilobytes of monochrome PNG, complete in one gesture — where chunking would be
three round trips to move less data than the JSON around it, and three more
places to lose a signature while the person who drew it walks back to the block.
A 512 KB ceiling separates the two and something over it is told which door to
use. The format is read off the first bytes, never off a filename.

**Each step may fail without undoing the one before it.** The signature is the
compliance artefact; the task and the PDF are bookkeeping about it. A task that
could not be closed — `complete_farm_task` will not take a completion from an
account that was not holding it — and a PDF that could not be redrawn are both
reported, and neither rolls back the capture.

## 0.54.0 — 2026-08-09

**The hiring wizard can say where somebody works and where they sleep.** The
handset has scanned a driver's licence and matched a returning worker's name
since v0.51.0, and the hire it produced recorded which *company* employs
somebody and nothing else about the assignment. Three new mobile methods finish
the Assignment and Housing steps.

**The four dropdowns are read off the site instead of compiled into the app.**
`list_onboarding_reference_data` returns Branch, Department, Designation and
Employment Type in one call. This is the same staleness `list_i9_document_types`
removed from the I-9's document picker, and the same consequence: `create_employee`
checks every one of these against *this site's* records, so a Swift array is not
merely out of date — it is a wizard whose Assignment step fails at the END of a
hire with "not a Designation on this site" and no way to find out what is. A
master the site does not have comes back empty and named in `masters_absent`,
never as an error; departments are scoped to the caller's entities and group
departments are dropped.

**`branch` is now writable, and that is why the wizard could not record it.**
`tools/employee.WRITABLE` carried designation, department and employment type
and not Frappe HR's own operating-unit field, so the allowlist is nineteen now
rather than eighteen. It reaches `create_employee` on the MCP registry, the
mobile `create_employee`, and `onboard_employee` — which also gained
`department` and `employment_type`, having carried only `designation` since it
shipped. `employment_type` is the one that decides whether somebody is Seasonal,
which is the fact an H-2A roster, an ACA hours count and a piece-rate wage
statement all turn on.

**The camp has a read and a write.** `list_available_housing` is beds and bodies
per unit — capacity, current occupants, open beds, and whether the unit can take
anybody — so a foreman at a tailgate can house somebody without walking the camp.
It **counts occupants and never names them**: who sleeps in which cabin is a
personnel fact, and `list_housing_units`' `occupants` array has no business on a
picker's phone merely because the vacancy count does. Shower blocks and shops are
not offered at all; a condemned cabin is listed with the reason rather than
silently missing, because a foreman who cannot find the unit they expected needs
to be told it is Uninhabitable.

**`assign_housing` refuses to overfill a cabin where the MCP tool only warns**,
and the difference is deliberate. `create_housing_assignment` reports "now holds
5 against a recorded capacity of 4" and writes the row, which is right on a
console where an operator can weigh it — a barracks really does take a fifth bunk
some seasons. It is wrong on a phone: nothing on the Housing step displays a
warning, the foreman has already walked away, and a bed that does not exist
becomes somebody sleeping in a truck. `allow_multi_occupancy` is not in the
signature, so the phone cannot turn that check off. The endpoint carries the HR
role gate `search_employees` does, because a Housing Assignment is the audit
trail defending an IRS Section 119 exclusion and the answer to an ORS 653 wage
claim.

**`Parcel.branch` is the join between a person and a cabin, and it did not
exist.** An Employee carries a Branch and a Housing Unit stands on a Parcel, and
no column connected the two — so a wizard that had just asked which camp somebody
was hired to could not then show that camp's housing. Parcel gains a `branch`
column: **Data, not a Link**, for the reason `Housing Assignment.employee` is
one, because a Link to Frappe HR's Branch would make Parcel fail to migrate on
every site without hrms. It is validated against the Branch table when there is
one, and a parcel tagged with a branch that does not exist is refused at the
record — that typo is a camp the wizard would show as having no cabins at all.
It is carried across a `convey_parcel`, because the camp does not change because
the deed did.

**Every branch row now carries the parcels it holds**, and
`list_available_housing` resolves `branch` to those parcels server-side, so the
phone passes the camp and gets that camp's cabins with no lookup of its own. Both
endpoints resolve through the same function, so the mapping the wizard was shown
and the mapping the housing list filters on cannot come apart. A camp spanning
two parcels returns both — `parcels` is the real answer and the scalar `parcel`
is set only when there is exactly one.

**A returning worker's cabin is offered back to them.** Passing `employee`
returns `previous_assignment` — the unit they last left, both dates, and whether
it can actually be had tonight — so the wizard shows "Last year: MC-Cabin-07" at
the top of the list and a returning picker is one tap instead of a scroll through
forty cabins nobody remembers the numbers of. Availability is computed for the
unit itself rather than looked up in the list beside it, because that list drops
full and condemned units by default and a cabin missing from it is precisely the
case this field exists to answer for.

**That one argument carries the HR role gate, and nothing else on the endpoint
does.** `list_available_housing` counts occupants and never names them, which is
why a Field Worker may call it; "where did this named person sleep last season"
is the personnel fact that split keeps off the endpoint. So `require_hr_role`
runs when and only when `employee` is passed — otherwise this would be a way to
walk the housing register one employee docname at a time from a picker's phone.
Somebody with an **open** assignment is reported as `currently_housed` rather
than offered their own bed back, and an open row wins over any finished one:
offering last year's cabin to a person who already has one tonight is an offer to
double-book them.

**The three ways a branch filter can fail are three different answers, and none
is a silent empty list**, because an empty camp reads on a phone as "no room": a
branch naming no Branch record is *refused*; a real branch with no ground tagged
to it returns the whole list with `branch_note` naming `update_parcel` as the
fix; a site that has not migrated the column returns the whole list with
`branch_note` naming the migration. `parcel` is still accepted and intersects
with `branch`.

All three are routed at `/farmops/api/mobile/…` as well as
`/api/method/erpnext_mcp.api.mobile.…`. **The iOS side is separate, tracked
work** — `MobileAPI.swift` names none of them yet, so they sit in
`PENDING_IOS_INTEGRATION` rather than claiming a Codable that does not exist.

## 0.53.0 — 2026-08-08

**The badge goes in the wallet the worker already carries.**
`generate_employee_badge_qr` has minted `CFL-0001` and drawn its QR since
0.50.0, and what came back was a PNG somebody has to print — a trip back to an
office in the middle of a hire day, a laminator, and a card that goes through a
wash cycle in August. `generate_employee_badge_pass` is the same badge with a
different delivery: an **Apple Wallet `.pkpass`** the foreman AirDrops straight
off the handset, which opens into Wallet on the worker's device with nothing
installed there, plus a **Google Wallet save link** for the Android half. Farm
logo, employee photograph, name, company, badge number and the QR, exactly as on
the printed card.

**It is the same badge, not a second credential.** The identifier, the minting
and the Bucket Log Badge Map row are `generate_employee_badge_qr`'s, called
underneath — so it is idempotent without `regenerate` in the same way, and a
bucket scanned off a phone screen and one scanned off a laminated card produce
the identical string and resolve through the identical `resolve_badge`. If these
could diverge, a bucket scanned off the phone would pay somebody the card does
not.

**A site with no Apple certificate still gets a pass, and is told it is
unsigned.** The `.pkpass` is complete and correct — right `pass.json`, right
images, right SHA-1 manifest — with no `signature` member, and the result says
`apple.signed: false` with the `site_config.json` keys it needs named in the
sentence. Apple Wallet will refuse to open it; that refusal is the honest
outcome, where a self-signed blob with a `signature` in it would fail just as
hard while looking upstream like it worked. **Nothing in the app changes the day
the certificate arrives** — the same call starts signing.
`docs/wallet-passes.md` is what to obtain from Apple (Pass Type ID, WWDR G4, a
`.p12` from Keychain) and Google (issuer account, service account key), and
where to put it.

**Google fetches pass images; it is not sent them.** A Google Wallet pass is a
JSON object signed into an RS256 JWT that becomes a `pay.google.com/gp/v/save/…`
link, and Google builds the pass server-side — so a photograph at
`/private/files/…`, which is what `set_employee_photo` correctly writes, cannot
be on the Android pass at all. Every image left out for that reason is named in
`google.warnings` rather than written as a URL that 403s. The Apple pass carries
its pixels inside the archive and is unaffected.

Reachable at `/farmops/api/mobile/get_employee_badge_pass`, where the bytes
travel **in the answer** rather than as a `file_url` — the handset authenticates
with `X-FarmOps-Token` and a private file URL is a login page to it. The iOS
side is separate, tracked work; nothing in `fafo_ios` calls it yet.

**There is no push update service, on purpose.** A pass already on a phone stays
there. What makes that safe is that it carries only `badge_id` — revocation
happens in the register, and a worker holding a retired pass scans a badge that
resolves to nobody, exactly as with a retired laminated card.

## 0.52.0 — 2026-08-08

**Models are served from ERPNext, not Volume Vision directly.** Through
0.51.1 an ML Model record said which model was deployed and where it was
trained, and `ModelDownloadService` on iOS read `source_server` off the
manifest to query Volume Vision's own `/api/sync/models/...` endpoints and
pull the binary from there — a second connection, with a second credential,
that this app's farmops-api sidecar exists specifically to not need.
`attach_model_file` is the new tool that gives an ML Model record the binary
itself: a File docname already staged through `stage_file_chunk`/
`commit_staged_file` for anything too large for one call, or base64 in the
call for anything small — stored as `model_file`, a plain Attach field.
`get_model_file_chunk` reads it back, base64, in the same chunked shape
`stage_file_chunk` already takes uploads in, and refuses **by name** when
nothing has been attached yet rather than reaching for `source_server` on a
caller's behalf. Both are now reachable at `/farmops/api/mobile/`
(`get_active_model`, `get_model_file_chunk`) through the same
`X-FarmOps-Token` door every other mobile call uses. The iOS-side cutover —
`ModelDownloadService` calling this route instead of Volume Vision — is
separate, tracked work; nothing in `fafo_ios` calls it yet.

## 0.51.2 — 2026-08-08

**A template written with a literal backslash-n instead of a line break stayed
a literal backslash-n all the way to the phone.** `create_farm_task_template`
and `update_farm_task_template` store `instructions` byte-for-byte —
`.strip()` and nothing else — and `task_templates.snapshot()` copies whatever
is there straight onto `Farm Task.notes` for every task raised from that
template. SwiftUI's `Text` renders a real newline as a line break but a
literal `\n` as two visible characters, so a worker read "Step 1\nStep 2"
instead of two lines. `fix_literal_newlines_in_instructions` finds every
`Farm Task Template.instructions` and `Farm Task.notes` value carrying the
literal sequence and replaces it with a real newline — templates and the
tasks already snapshotted from them, so a worker looking at a task right now
does not wait on a re-snapshot that may never happen. Idempotent: a value
with no literal sequence is left alone.

## 0.51.1 — 2026-08-08

**A badge QR is not a login QR, and it was being drawn like one.** `qr.render`
defaults to error correction M — 15% of the symbol recoverable — which is right
for a code held up to a screen for ten seconds and wrong for a card that lives
in a picker's back pocket through a cherry harvest. Employee badges are H now:
30% recoverable, which is the difference between a scuffed, creased, muddy card
that still resolves at a bin trailer and one a foreman has to read out over a
radio. It costs nothing in size — `CF-0001` is seven alphanumeric characters and
fits a version-1 symbol at H, so the module count does not change for any badge
this app mints. Still overridable per call.

**The PNG now carries enough pixels to be printed at 1.5 inches without
interpolation.** A printer scaling a QR up from too few pixels softens the module
edges, and soft edges cost more scans than the dirt does. The scale is computed
from the symbol rather than hardcoded, because a longer company prefix pushes
the badge into a version-2 symbol and a fixed scale would then print the same
card at two-thirds the resolution with nobody noticing. Every badge shape comes
out above 300 dpi at 1.5"; there is a test that sweeps the module counts.

**The print requirement travels with the card, because this app does not lay the
card out.** `generate_employee_badge_sheet` returns card DATA and a template
NAME — an Avery sheet, a label printer, the Desk, the handset's preview — so
"at least an inch and a half, black on white, ID printed below" cannot be
enforced here. It is stated instead, in units a renderer can act on: minimum
width in inches and points, the quiet zone in modules, the two hex colours, and
the caption with its position. The quiet zone was already the specification's
four modules and now says so in the payload, so a renderer that crops or insets
the image knows what it would be destroying. A colored badge card is fine; a
colored QR is not.

**The human-readable ID is the fallback the whole identifier scheme exists for.**
Every scanner eventually fails on a card that went through a wash cycle, and a
badge nobody can read aloud is a picker whose buckets go unattributed for the
morning. `caption`/`caption_position` put `CF-0001` under the symbol.

### The middle name that was read and thrown away

**The handset has parsed AAMVA's `DAD` off every licence barcode since the ID
scanner shipped, and there was nowhere for it to land.** `middle_name` was not
on `employee.WRITABLE` and not in `create_employee`'s optional set, so it was
read at the tailgate and dropped on arrival. `DBN` — the element some
jurisdictions emit instead — was not read at all.

**It silently emptied a box on a federal form.** `submit_i9_section_1` has
filled Legal Middle Name from `Employee.middle_name` since v0.45.0 whenever the
caller sends none, and the column it reads could never be written — so that
fallback resolved to empty on every I-9 this app has ever filed. The join is
now tested end to end: create with a middle name, open an I-9, submit Section 1
without one, and the box has it.

It is optional at every step. Most people have no middle name, and a wizard that
stopped on the field would stop on one the worker cannot fill. It is also not
folded into `employee_name`, which is what the dispatch board, the payroll
register and the returning-worker search match on — widening that would stop
everybody hired after this release matching the record they already have.

## 0.51.0 — 2026-08-08

**The signature the worker made is now on the form they signed.** The I-9 record
has held `section_1_signature` and `section_2_signature` — real strokes captured
on the handset's canvas, with `signed_at` and `signed_ip` beside them — since
v0.45.0, and `render_i9_pdf` printed an empty signature line every time. The
employer held the signature and the retained page did not show it. Same on the
W-4, where "This form is not valid unless you sign it" is printed on the page:
what came out was an invalid W-4 with all the right numbers on it.

**What is refused has not changed.** A NAME typed into a signature box still
never happens. That was the earlier module's argument and it was right — a
string this app typed would render as a signature and would not be one. The
capture is a different thing: it is the attestation itself, and printing an
empty box beside it was the gap.

**It goes in as page content, which is the whole point.** A field value is
deletable by anybody who opens the form and an annotation is deletable in
Preview with one click; both would satisfy a screenshot and neither would
satisfy an inspection. `pdf_signing.stamp` merges the ink into the content
stream, and `pdf_signing.flatten` then burns every remaining field in and
deletes the AcroForm, so a signed copy has no fields at all — `get_fields()`
answers None. Order matters and is asserted: fill, flatten, then stamp. Stamping
first lets the signature box's own empty appearance paint over the signature.

**The capture is white paper, not ink on glass.** `SignatureCanvas.renderPNG`
renders opaque and fills white before it strokes, so the PNG on the record is a
white rectangle with a signature in the middle. Drawn as-is it covers the
signature rule and everything printed around it. `pdf_signing.ink_only` keys the
paper out by luminance and crops to the stroke — which is also worth about 25%
more ink on the page, because the I-9's employee signature line is 25:1 and an
uncropped capture is 3.5:1. Pillow is declared for this and imported
defensively; a bench without it renders the form unsigned rather than failing.

**Geometry is read, not guessed.** `pdf_signing.box_for` takes USCIS's own
widget rectangle and measures the clear space above it, so the ink grows into
the gap instead of into the A-Number row, on any template revision. The W-4 has
no widget on its signature rule — the IRS printed a line, not a field — so that
one constant is measured, documented against the landmarks it came from, and
re-derived from the shipped file by its own test.

**Signing metadata is the part a picture cannot carry.** 8 CFR 274a.2(h)(2)
asks for a record verifying who produced the signature and when; "attested
electronically on 1 April" verifies nobody. Additional Information now names the
signer, the timestamp, the IP the attestation came from, and closes with
"Electronically signed pursuant to 8 CFR 274a.2." A record with a timestamp and
no image still gets the sentence — the attestation happened — and only claims
the image was affixed where one was.

**Unsigned renders are untouched.** No capture means no stamping and no
flattening: every field stays live, which is the page an employer prints and
signs with a pen, and it is what this app produced for its whole life until now.

### The badge a phone could not issue

**`generate_employee_badge_qr` was published on the MCP tool registry only.** It
has minted readable `CF-0001` identifiers since v0.50.0 and the handset does not
speak that surface, so the onboarding wizard's badge step could map a card
printed somewhere else and could not produce one — on a hire day, in a yard, for
a worker standing there waiting to be told their number. It now has an
`api/mobile.py` wrapper and a `farmops_api` route. `badge_id` is deliberately
NOT a body key: the tool lets a Desk operator adopt a card from the old
`farm_app` uuid stock, and letting a handset name it would put the uniqueness of
a payroll key in whatever a foreman typed. `regenerate` IS accepted, because a
lost card is a field problem.

**`set_employee_photo`, and the reason every badge printed initials.**
`generate_employee_badge_sheet` lays a card out from `Employee.image` and falls
back to two letters where it is empty. `attach_onboarding_document` files
evidence — the bytes land as a private File pointing at the Employee and nothing
on the Employee points back — which is right for a List B photograph and left
the badge with no face on it. The new tool does the same attach and the one
field write that closes the loop. It stays private, refuses a PDF before the
attach rather than after, and uses `db.set_value` so a half-filled wizard record
cannot fail validation over a photograph.

**`Company.badge_logo`.** An Attach Image custom field, installed from its own
`install.py` job and NOT from `compliance_fields.py` — that table refuses any
field that cannot name a regulator, and a farm's logo has none. It is also not
behind the compliance switch: an operator who declined having their Spray Log
extended did not thereby decide to print logo-less badges. `_card` now carries
`company_logo_url`, so the single card and the printed sheet both have it.

## 0.50.1 — 2026-08-08

**A bucket is full or it is not.** Full counts as 1, not-full counts as 0, and
piecework pay is `number_of_full_buckets × piece_rate`. No percentage, no partial
credit, no fractional unit anywhere in that sum. **The code already did this** —
both paths that turn a capture into piece units were already binary, and
`coverage_percent` was already absent from `payroll_integration._UNIT_KEYS` and
from the payroll row. Nothing here changes a figure on a slip.

**What was missing was anything that said so.** The guarantee rested on two facts
staying true in two modules that do not import each other's constants, and the
failure would have been silent: pay would still come out, wrong by a fraction
nobody could see without recomputing a period by hand. `TheGateIsBinary` asserts
it against `_UNIT_KEYS` itself — 51% and 99% are both one bucket, a Rejected
99.9% is nothing, a capture with no coverage at all is a whole bucket, and ten
captures at ten coverages are ten units and a whole number. `BucketPipelineTests`
carries the same claim on the phone.

**Three places the interface implied partial credit.** The verdict pill read
`Full — 94%` and now reads `Full — counts as 1 bucket`; the bucket history put
coverage in the headline position beside each row and now shows `1` or `0` with a
running count, coverage demoted to a `model read 94%` caption; the live aiming
meter stays (it tells a picker when the model is about to fire) and now says
`Full — will count as 1` once it crosses.

**And the 0.50.0 note below is corrected.** The coverage scaling it describes was
a real bug about the **model-audit trail** — an unscaled fraction would be stored
as "0.94% full" — not about pay, which it never touched.

## 0.50.0 — 2026-08-08

**A badge is issued here now, and a bucket leaves the phone.** The badge audit of
2026-08-07 found the Badge → QR → Bucket pipeline about sixty per cent built,
with the missing forty in two places: nothing *issued* a badge on this side —
`link_badge_to_employee` recorded a string somebody else had printed — and
nothing *sent* a capture off the handset, because `MobileAPI.syncBucketEntries`
had zero call sites and `commitEntry` appended to an in-memory array. Everything
between those two points already existed and was already tested. What was broken
was the joins. Read `RELEASES/v0.50.0.md` before upgrading a fleet of phones.

**Three tools (389 now, 178 read / 211 write).**
`generate_employee_badge_qr` mints a readable `CF-0001`, records it, and returns
the card's QR — idempotent without `regenerate`, and `regenerate` retires the
badge it replaces, because a replacement that leaves its predecessor resolving is
how a card found in an orchard keeps earning. `generate_employee_badge_sheet`
does a crew at once and one bad name does not lose the sheet. `resolve_badge` is
the read between a scan and a name: `add_worker_to_shift` takes an Employee
docname and a camera produces a badge string, so until now a crew clock could
scan a whole crew and roster none of it.

**A soda can is no longer a badge.** Two layers. The shape check refuses every
URL, Wi-Fi join code and `api_key:api_secret` — including a
`generate_mobile_login_qr` payload, which scanned at a badge step used to become
a badge ID with a live secret in it — while still accepting the 36-character
uuids already in workers' pockets. The register check is the new `badge_policy`
on `sync_bucket_entries`: `lenient` keeps v0.44.0's deliberate backfill for a
Desk import, `strict` is what the phone sends and cannot relax, and with a
`shift` it also refuses a picker who is not clocked in. **A refusal never repeats
what it refused** — not in the message, and not in the audit row.

**The phone half.** A durable `BucketEntryQueue` (a capture now survives a
force-quit, and the launch sweep stops deleting its photograph), a
`BucketSyncEngine` that treats a duplicate as landed and keeps a refusal visible,
an on-device badge directory so a scan names the picker with no signal, and the
crew clock wired to `start_shift` / `resolve_badge` / `add_worker_to_shift` /
`end_shift`. Wiring the sync found a live bug in the **model-audit trail**:
`coverage_percent` was being held as a fraction and would have been filed into a
column that stores a percentage, recording a full bucket's diagnostic as "0.94%
full". It is a diagnostic and not a pay input — the gate is binary and pay is the
count of Accepted buckets — so no figure on a slip was ever affected. See 0.50.1.

## 0.49.0 — 2026-08-08

**The minimum wage is now paid, not just priced.** v0.48.2's scenario tests found
four gaps in the piecework rules and pinned each one as a passing assertion of the
wrong behaviour. All four are closed. Gross pay is now the greater of what the
work earned and what the hours are owed — FLSA §6, ORS 653.025, RCW 49.46.020 —
so forty-seven buckets at $1.50 in an eight-hour Oregon day pays $117.60 rather
than $70.50. **This changes what workers are paid; read `RELEASES/v0.49.0.md`
before running a period.**

**The top-up is never silent, because the old posture's reason still holds.** Pay
that quietly inflated would hide a rate set below the lawful floor, so it does not
inflate quietly: `earned_gross` and `minimum_wage_makeup` are separate figures on
the slip and separate columns on Farm Payroll Slip, and
`totals.topped_up_to_minimum_wage` names every worker whose rate needed one.
Nobody on that list is underpaid — that is the change — and every name on it is a
rate worth looking at. One exception on purpose: a **Salary** structure is
reported and not topped up, because whether a salaried employee is exempt from the
minimum wage at all is a fact about their job this app does not hold.

**The floor carries the overtime premium.** `regular × minimum + overtime ×
minimum × 1.5`, not `hours × minimum`. Fifty Oregon hours are owed $808.50, not
$735 — and $780 of piece earnings used to clear the flat number while being $28.50
short of the law. `check_minimum_wage` takes `overtime_hours` and
`check_minimum_wage_by_state` takes `overtime_hours_by_state`; both compute the
floor through the same `minimum_wage_floor()` so they cannot drift.

**A worker can be part piece-rate and part hourly.** Farm Shift and Farm Shift
Crew Member carry `pay_type` and `pay_rate`, blank on the ordinary day; Farm
Salary Structure carries `hourly_rate`. Six hours picking at $1.50 a bucket and
two of irrigation at $16.00 is $167, and the floor is tested on all eight hours.
The premium is half of one regular rate blended across both kinds of work, which
is the method 29 CFR 778.115 gives for two rates in one workweek.

**The piece-rate overtime premium is now half-time, not time and a half.** The one
figure here that goes down. 29 CFR 778.111: the piece earnings already paid
straight time for the overtime hours, so what is owed on top is half the regular
rate. In practice it rarely reduces a cheque, because the overtime-inclusive floor
lands on the same slip — the fifty-hour week above pays $808.50, which is $28.50
more than v0.48.2 paid, not $120 less.

**Weekly overtime was verified and not changed.** A ten-hour day in a thirty-hour
week is not overtime, a forty-five hour week is five hours of it, and each week of
a multi-week period is its own threshold. All three were already right and are now
asserted by name.

**Run `bench migrate`** — five new columns across four doctypes. An unmigrated
bench keeps working and loses the overrides; slips written earlier read back with
zero makeup and `earned_gross` equal to gross, which is what they were.

## 0.48.3 — 2026-08-08

**Every photograph and signature the onboarding wizard collected was reported as
filed and stored nowhere.** `FrappeClient.uploadFile` POSTed to Frappe's own
`/api/method/upload_file`, which is not under
`fallback_auth._PATH_PREFIX` (`/api/method/erpnext_mcp.api.`), so the
`X-FarmOps-Token` header was never read on it. Behind the Tailscale funnel —
which strips `Authorization` — the request arrived as Guest, Frappe answered
HTTP 200 with the Desk login page, and the client returned success on any 2xx
without looking at the body. Six files per hire: the Section 1 signature, the
List A photograph or the List B/C pair, the Section 3 signature and the
photographed W-4. The I-9 Form reached Complete with nothing behind it, which
under 8 U.S.C. §1324a(b)(3) is worse than an empty file because it asserts
something.

**Uploads now take one path, and it is the one that authenticates.** The staged
route — `stage_file_chunk` then `finalize_staged_file` under `/farmops/api/files/`
— has carried task evidence since v0.14.0. `ChunkUploader` grew an entry point
for bytes the app is holding rather than a file it has stored, which is what the
wizard's captures are and why the multipart path existed. `FrappeClient.uploadFile`
is deleted.

**`attach_onboarding_document` is the call whose absence caused it.**
`finalize_staged_file` commits evidence unattached on purpose — forwarding an
attachment target from a handset would let a field worker hang a file off a
Journal Entry — so onboarding had nothing to file its photographs with. The new
endpoint names one parent doctype in code, proves the Employee is inside the
caller's entities, requires the HR role with no exception, takes a `file_token`
rather than bytes, forces the File private through the document controller,
refuses a File already filed against another record, and treats a repeat of the
same file as a no-op. The signed paper I-9 goes to `upload_signed_i9` instead,
which puts it on the I-9 Form and points `signed_pdf` at it — an endpoint that
has existed since v0.47.1 while the app carried a comment saying it did not.

**A 200 is no longer evidence that anything happened.** `FrappeClient` refuses
any 2xx whose body is not a JSON object, on every path, and reports HTML as
`unauthorized` rather than as a decoding error — because that is what it has
meant every time. This closes a wider hole than the one it was written for:
`callVoid` discards the body by design and every onboarding write goes through
it, so all of them would have reported success on a login page in the same way.
`attach_onboarding_document`'s answer is decoded rather than discarded, with a
non-optional `file_token`.

**What is not fixed:** a handset running an older build still loses its
evidence, because the fix is that the app stops calling `/api/method/upload_file`
and a shipped build goes on calling it. Adding that path to
`_is_mobile_path` would rescue it and would extend this app's auth hook over a
Frappe core endpoint none of `guard.py`'s checks cover; that is deliberately not
done here. Evidence already lost is not recoverable — the bytes never arrived.

See `RELEASES/v0.48.3.md`.

## 0.48.2 — 2026-08-07

**The piecework pay rules now have the scenario tests they never had, and three
of them found something.** `test_payroll.py` tested the engine one function at a
time and `test_payroll_integration.py` tested the join from the shift register to
the slip. Neither walked a whole day of picking end to end and asserted the
dollar figure, so the places where the code and a reader's assumption come apart
were nowhere in the suite. `tests_standalone/test_piecework_rules.py` is
sixty-one tests over eight claims, and it is a test-only release: NO
CALCULATION CHANGED.

**Three gaps are now pinned rather than latent.** First, *there is no higher-of
rule* — forty-seven buckets at $1.50 in an eight-hour Oregon day pays $70.50, not
the $117.60 the minimum wage would have. That is deliberate and documented in
`payroll_integration.py`'s header: the $47.10 shortfall is priced and reported,
and topping gross up quietly would hide the fact that a rate is set below the
lawful floor. Second, *the minimum wage check ignores the overtime premium* —
`check_minimum_wage_by_state` compares gross against `minimum_wage × hours`, flat,
so fifty hours and four hundred buckets grosses $780 and PASSES a floor of $735
while the real floor with the premium is $808.50. Nothing computes that $28.50.
Third, *a slip carries two minimum wage verdicts that can disagree*:
`minimum_wage_check` tests the whole period against the state holding the most
hours, `minimum_wage_detail` tests each state against its own, and for thirty
Oregon hours and ten Washington ones at $15.00 the first says pass and the second
names Washington. `tools/payroll.py::_slip_row` already resolves that in favour
of the per-state answer, which is the right one — now asserted.

**And one thing that does not exist:** a worker cannot be part piece-rate and part
hourly in one period. `pay_type` is one field on one salary structure, so a picker
who spent Monday on buckets and Tuesday on irrigation is paid for Tuesday at the
piece rate — which is nothing, because Tuesday produced no buckets. The tests
assert the current behaviour and name it rather than asserting a wish.

**Two shipped tools had a registration test and nothing else.**
`get_piecework_summary` and `reconcile_bucket_payroll` arrived in v0.44.0 with
their names asserted in `registry.TOOLS` and no test of what they return. Both now
have one: accepted-versus-rejected counting, employee and date scoping, and the
three reconciliation verdicts including a cancelled payroll run counting as having
paid nothing.

## 0.48.1 — 2026-08-07

**The 2026 Form W-4 credits $2,200 a qualifying child and this app credited
$2,000.** Step 3 of the 2020-2025 editions said "Multiply the number of
qualifying children under age 17 by $2,000"; the 2026 edition says $2,200. The
W-4 Form controller multiplied by a flat 2000, so every W-4 filed for tax year
2026 was $200 a child short in `total_dependents_credit`.

**It was short in two places at once, which is the part worth reading.** The
credit is a STORED column, computed once in `validate` — and two unrelated things
read the stored number rather than recomputing it. `withholding.py` subtracts it
from the tentative tax, so the employee was over-withheld every period.
`w4_pdf.py` prints it into box 3 of the government form, so the copy USCIS-style
retention keeps had the wrong number on it. v0.48.0 shipped the fillable W-4 and
its test fixtures were hand-written at $2,200 — the printed form and the engine
filling it disagreed, and nothing failed, because no test ran a count through the
controller and into the PDF.

**The amount is now keyed to the form's own `tax_year`, not to the calendar.** A
W-4 is filed against an edition, and this app keeps one Active W-4 per employee
per tax year, so a 2025 form re-saved today must still restate itself at $2,000 —
a flat constant would rewrite a prior-year filing on the next save. The schedule
is two tuples at the top of `w_4_form.py`; a year later than every entry takes
the newest, so 2027 needs no edit unless the IRS moves the number again. **The
other-dependent credit was already right** and is unchanged: $500 on both
editions.

**A new patch restates the rows that were already filed.** Fixing the controller
fixes the next save and nothing else, and nobody re-saves an Active W-4 — the
design is that a change supersedes rather than edits, so the stale total would
have outlived the fix on every existing row.
`erpnext_mcp.patches.recompute_2026_dependents_credit` recomputes the three Step
3 columns on every W-4 for 2026 or later, from the counts and the year, and
**prints each restated form by name with what it held and what it now holds**. It
leaves 2025 and earlier alone, counts and skips rows already correct, and is a
no-op on a second run. It also says out loud what it cannot fix: **a payroll slip
already posted from the old credit over-withheld and is not rewritten**, and the
W-4 PDF on file needs reprinting with `render_w4_pdf`.

## 0.48.0 — 2026-08-07

**Three gaps in the federal employment forms, and they are three different kinds
of gap.** One was a mapping that could not exist. One was a string the server
believed because a client sent it. One was a whole half of a form.

**The EIN had nowhere to go, and that is the finding.**
`tools/i9._employer_block` has returned an `ein` since v0.47.1 and `i9_pdf` never
wrote it anywhere, which read like a line somebody forgot. It is not: **Form I-9
has no EIN box.** All 133 of the shipped template's AcroForm fields are
enumerated by the test suite, and Section 2's employer block is a name and title,
a signature, a date, a business name and a business address. The EIN is an
E-Verify datum — it belongs to the E-Verify case, not to the retained form. So
the number now goes where USCIS provides for an employer to write something the
boxes do not ask for: **Additional Information**, labelled. Appending it to the
address box, whose own label is "Address, City or Town, State, ZIP Code", would
have put something that is not an address in a box an inspector reads by name. A
test pins the premise to the file, so an edition that grows an EIN box fails
loudly rather than leaving the prose line to rot.

**`verifier_name` was a string on a JSON body.** Section 2 is an attestation
under penalty of perjury that a NAMED PERSON examined the documents, and until
now that name was whatever the caller typed — from a phone, in an orchard, with
nothing on the server that said whether they had ever been authorised to make it.
A new **Authorized Signer** child table on I-9 Settings carries the account, the
printed name, a title, a flag per form and an active flag;
`submit_i9_section_2` and `submit_w4` take the name and title off the roster row
rather than off the request. The check reads `security.caller_identity()` and not
`frappe.session.user`, which is the whole feature — `mcp.handle` becomes the MCP
System User a line after it authenticates, so a roster matched against the
effective user would authorise every caller identically.

**An empty roster authorises everybody, and that is the design.** Every existing
site migrates into one, and a version that started refusing signatures on migrate
would break the I-9 flow on every farm running this app. **The first row is the
switch:** adding one signer turns enforcement on for the whole site, and
`add_authorized_signer` says so in its own result — because "I added myself and
now my foreman can't file an I-9" is the surprise this design has to pay for.
An explicit name is still accepted, because one authorised person filing for
another is real (the foreman examined the documents, the office files the form),
and it has to be on the roster too. **Nothing is ever deleted:**
`remove_authorized_signer` clears a flag and keeps the row, there is no delete
tool, and a form signed last season still names somebody this employer had
authorised. Four tools, and all four are published to the phone as well — a
roster that can only be edited in the Desk is one nobody fixes at 6am on a hire
day.

**The W-4 collected elections for four releases and never produced a form** —
the same gap `render_i9_pdf` closed for the I-9 in v0.47.1. `render_w4_pdf`
fills the IRS's own fillable Form W-4, now shipped at
`erpnext_mcp/templates/w4_form.pdf`. **And the form has a block the app had
nothing for:** Step 5's Employers Only row asks for the employer's name and
address, the first date of employment, and the EIN — three facts the site already
held and no W-4 could reach. They are resolved at render time from
`i9._employer_block` and `Employee.date_of_joining`, not copied onto every row,
so a farm that changes its registered address does not have a hundred W-4s
carrying the old one. What IS stored is who processed it. Step 1(a)'s address is
read off the employee's I-9, where it is already structured, rather than split
out of `Employee.current_address` by guesswork.

**The one structural edit is that XFA comes out of the copy.** The IRS file is a
hybrid — an AcroForm plus an XML payload describing the same form — and Acrobat
renders the XFA and ignores the AcroForm. A fill that left it in place would
produce a file holding every right answer and printing blank in the reader an
accountant is most likely to open it in. And because the IRS names its fields
`f1_12[0]` rather than after the boxes, "every name exists" is not a strong
enough test — a mistyped name would exist too — so the table is checked against
the page's own **geometry**: the name boxes share a row, the filing-status ticks
descend in the order the form prints them, and the Employers Only boxes are the
bottom band left to right. The same class asserts that the Step 5 signature band
holds no widget at all, which is why nothing has to be deliberately skipped there.

Five new tools (386 total: 177 read, 209 mutating), five new mobile methods, one
new DocType, 97 new tests. `bench migrate` and nothing changes behaviour on its
own — see `RELEASES/v0.48.0.md`.

## 0.47.2 — 2026-08-07

**v0.47.0 taught the tool six fields and the transport dropped every one of
them.** The mobile wrappers in `api/mobile.py` declare their arguments one by
one, and `farmops_api/routes.py` reads the accepted body keys straight off those
signatures with `inspect` — which is the right design, because an allowlist that
cannot go stale is better than one somebody has to remember to edit. What it
means is that a field the tool learned and the wrapper did not is not a
half-working field: it is dropped silently, before the tool ever sees it, and the
call succeeds. Six of v0.47.0's new fields were in that state.

**Three of them made a lawful worker unfileable.** Section 1 of Form I-9 asks an
Alien Authorized to Work for **one** of three identifiers, and v0.47.0 gave the
tool all three — A-Number, I-94 admission number, or foreign passport with its
country of issuance. `submit_i9_section_1` carried only the A-Number. A picker
holding an I-94 and no A-Number filled in the form on a phone, sent the number,
and was refused for a missing identifier they had just provided; the same for a
passport. Both keys of the passport pair are now forwarded together, so the
tool's own refusal of a number with no country is reachable rather than
pre-empted.

**The other three filed a false attestation.** 8 CFR 274a.2(b)(1)(vi) lets an
employee whose document was lost, stolen or damaged present a receipt for the
replacement and work while it comes. v0.47.0 recorded that — `list_a_is_receipt`
and its two siblings set `receipt_pending` and start a 90-day clock in
`receipt_expires_on`. Dropped at the transport, every receipt a foreman examined
in an orchard was filed as though the document itself had been examined, and the
clock that says when the real one is owed never started. The three flags travel
independently, because a worker may present a real driver's licence alongside a
receipt for a replacement Social Security card and the form has to say which is
which.

**Nothing else was missing.** `reverify_i9` already declares every field its tool
accepts, and all seven I-9 routes — including v0.47.1's `get_i9_form`,
`generate_i9_pdf` and `upload_signed_i9` — are published. Six tests cover the
pass-through end to end; five of them fail against v0.47.1. No doctype changed,
no migration is needed, and a caller that has not grown the new inputs behaves
exactly as it did.

## 0.47.1 — 2026-08-07

**Four releases collected the data and nothing produced the form.** v0.27.0
through v0.47.0 built Section 1, Section 2, receipts, Supplement B, a retention
clock and an append-only audit log — and what an operator could actually put in a
folder was a Desk print of the doctype: every one of its eighty-four fields, two
to a row, in the order the JSON declares them, `naming_series` and `pdf_col`
included. That is not Form I-9, and an inspection under 8 U.S.C. §1324a(b)(3)
asks to see Form I-9.

**So the app ships the government's page and fills it in.** The USCIS fillable
PDF — OMB No. 1615-0047, Edition 01/20/25, four pages, 133 named AcroForm fields
— is now a static asset at `erpnext_mcp/templates/i9_form.pdf`, byte for byte,
with its SHA-256 asserted by the test suite so a template somebody re-saved in a
PDF editor fails the suite rather than producing a form nobody can file.
`render_i9_pdf` writes the collected values into its own named fields and
attaches the copy privately to `generated_pdf`. **The file on disk is never
edited.** This is deliberately not what v0.36.0 does for a W-2: the IRS's Copy A
is red-ink scannable stock no laser printer makes, so a reproduction is the only
honest output there — an I-9 is completed on plain paper by every employer in the
country and retained rather than filed, so the honest output is the real form.

**Four boxes are left blank on purpose, and each is argued in the code.** Neither
signature box is ever written: an electronic I-9 signature has to meet 8 CFR
274a.2(h)'s own requirements and a name typed into a `/Tx` field would *render*
as a signature without being one. What the app genuinely holds — a capture and a
timestamp — goes into Additional Information as what it is, beside the receipt
deadline and any reverification that did not fit on the page. The SSN comb stays
empty unless `include_full_ssn` is passed **and** `store_full_ssn` is on; that is
the only read of the encrypted column anywhere in this app, v0.47.0 said the day
would come, and the read is recorded in the audit row. The DHS
alternative-procedure tick is never set, because nothing here records whether it
was used. Supplement B's new-name boxes stay empty, because the child table
records the reason `Name Change` and not the new name.

**One field in USCIS's own file is on two pages at once.** `Document Title 1` is
a single AcroForm field with a widget in Section 2's List A block *and* one in
Supplement B's second reverification row — so filling either box fills both, and
a hire-day document title would be silently overwritten by a reverification made
two seasons later, on a form that looks perfectly plausible. The generated copy
gets that widget promoted into a field of its own before any value is written.
It is the only place this app edits the form's structure rather than its values,
and the defect it works around is itself pinned by a test, so a USCIS edition
that fixes it fails loudly and the surgery can be deleted.

**`attach_signed_i9` closes the loop, and it is the half that matters.** The
rendered page is printed, signed with a pen by two people, and photographed; that
photograph is the record §1324a asks the employer to have kept. It arrives
through the existing evidence path — `stage_file_chunk` then
`finalize_staged_file`, hashed at capture and verified on assembly — and this
call names the File and attaches it to `signed_pdf`, **making it private on the
way in whatever it was**. No bytes cross the endpoint: a base64 body would be a
second upload path with its own size limit and its own way of failing halfway up
a hill. A second signed copy is refused without `overwrite`, being the one write
on this doctype that could not be undone from the record itself.

**Three routes, and the first is a read the wizard never had.** Every other I-9
call hands back the record it just wrote, and `get_employee` reports a one-word
status — so a foreman opening the flow on somebody already verified could be told
`Verified` and nothing else. Which documents? Examined by whom? Is anything still
owed? All on the server, none of it reachable. `get_i9_form` publishes it (SSN
still the last four, and a worker may read their own record but nobody else's);
`generate_i9_pdf` hands back a URL to print from; `upload_signed_i9` files the
photograph back. `include_full_ssn` is **absent** from the handset endpoint
rather than renamed — printing somebody's nine-digit number onto a page a phone
could mail anywhere needs a retention decision an operator makes at a desk.

**The Desk's Print button gets a format, and it fixes the wkhtmltopdf failure by
not depending on anything.** The I-9 Print Format is seeded on migrate — created
once, never overwritten, so an operator's edits survive every upgrade — and lays
the record out as the form's own sections with the citizenship attestation as
ticked boxes and the three document lists in their own columns. It references no
image, no stylesheet, no webfont and no URL at all: wkhtmltopdf fetches every
external resource synchronously and one that 404s blanks the page. The signature
captures are private Files it could not authenticate to anyway, so the page
reports whether one is on file, which is the fact rather than the picture. The
*other* wkhtmltopdf failure — fontconfig aborting on a read-only
`/var/cache/fontconfig` before it lays out a glyph — is fixed where it has to be,
in the container image, because no template can work around a renderer that
cannot start.

**One new dependency, imported defensively like the four before it.** `pypdf`
fills the form. A bench without it — or without the shipped template — loses
exactly `render_i9_pdf`, which says so by name with the pip command to fix it.
`attach_signed_i9` is unaffected, every I-9 value stays readable through
`get_i9_form`, and the Print Format still prints with no PDF library at all.

## 0.47.0 — 2026-08-07

**The I-9 could be opened and completed and never re-examined.** v0.27.0 built
Section 1 and Section 2 as a structured record with an immutable audit trail, and
that is most of Form I-9. What it had no shape for was the third of it: 8 CFR
274a.2(b)(1)(vii) requires an employer to reverify an employee whose work
authorization expires, and `flag_i9_reverification` could say a form needed
re-examining while nothing in the app could record that it had been. On a real
site that left two doors, and both are wrong — a second I-9, which
`create_i9_form` refuses outright, or a Desk edit over Section 2's own columns,
which erases what was examined on the day of hire. `reverify_i9` is the call, and
it **appends**: each one adds a row to an `I-9 Reverification` child table and
touches nothing Section 2 wrote. A seasonal picker on a renewing EAD accumulates
one a season, in order, and the entry from four seasons ago still says what was
examined four seasons ago.

**It moves two columns and no others, and the second is the interesting one.**
`alien_work_authorization_expiry` goes forward to the new document's date, so
`list_expiring_work_authorizations` follows the document currently in force
rather than the one just replaced. And **`Employee.i9_status` moves off
`Expired`** — the only write this app makes to that column. v0.46.2 established
that no I-9 tool writes it and that `employee_detail` reconciles a stale
`Pending` on the way out while leaving `Expired` strictly alone, because an
expired I-9 is somebody's deliberate statement and a Complete form from an
earlier season is the wrong thing to trust against it. A reverification is the
one event that *answers* that statement: leave the column and the wizard goes on
routing the worker to `create_i9_form`, which refuses because they have one, and
the `i9_expired` alert goes on firing about an authorization renewed this
morning. So a deliberate statement is answered by an equally deliberate action
and by nothing else — a column reading anything other than `Expired` is not
touched, and the write is best-effort, because losing a convenience column must
not lose a federal record.

It refuses a document that had already expired on the day it was examined — that
is not evidence of *continuing* authorization — and refuses List B outright,
because List B establishes identity and identity does not expire.

**Section 1 asks for one of three identifiers and could store one.** An Alien
Authorized to Work gives a USCIS/A-Number, **or** a Form I-94 admission number,
**or** a foreign passport number with its country of issuance. The form had a
column for the first, so the other two arrived at a record with nowhere to put
them and were dropped — and the resulting Section 1 looked answered. All three
are stored now, a passport number without its country is refused because a
passport number alone identifies nobody, and a status of Alien Authorized to Work
carrying none of the three is refused as the unfiled Section 1 it is.

**Section 2 accepted free text against a table of 24 documents it never
consulted.** `i9_documents.py` has seeded every USCIS-accepted document since
v0.27.0. Nothing checked a title against it, so nothing stopped a driver's licence
being recorded in the List A slot — a form asserting one document proved both
identity and work authorization when it proved neither. Titles are now checked
against the list they claim to be from and stored in that list's own spelling. A
site whose table is empty is not checked at all: the alternative is an upgrade
that turns into a compliance outage between install and migrate.

**A receipt is temporarily acceptable, and the form still completes.** Under
8 CFR 274a.2(b)(1)(vi) an employee whose document was lost, stolen or damaged may
present a receipt and work while the replacement comes. So the status stays
**Complete** — the person may lawfully work, and a status that said otherwise
would have the wizard collecting a whole new I-9 — and `receipt_pending` with
`receipt_expires_on` (hire date + 90 days) carry what is still owed.
`list_pending_i9_verifications` reports them in their own list beside the
unsigned Section 2s, because "never verified" and "verified against a receipt"
are different obligations. `reverify_i9` with reason `Receipt Replaced` closes one.

**Two routes, and one of them is the first read on the onboarding surface.**
`list_i9_document_types` publishes the table grouped by List A / List B / List C,
which is the shape Section 2's own question has; the app has been drawing that
picker off a hardcoded Swift array that goes stale the next time USCIS revises
the list, and goes stale silently. `reverify_i9` is the branch the wizard has been
able to *see* since v0.46.2, when `get_employee` began reporting a returning
picker's expired I-9 as expired, and has had no call to take.

**The full SSN is storable now, and off.** E-Verify submits nine digits and
cannot be run from four, so a site that runs it needs somewhere to keep them —
and a site that does not should not be holding them. `store_full_ssn` on I-9
Settings is off by default; the column is a Frappe Password field, so Frappe
writes it to the encrypted `__Auth` table rather than to a row; no tool reads it
back and `get_i9_form` does not return it. Turning the switch off blanks it on
each form's next save, which is a fact about the next save rather than a promise
about the past, and the test suite says which.

## 0.46.2 — 2026-08-07

**The returning picker, which in tree fruit is the common case.** v0.46.0 gave
the wizard's Identity step a search and a rehire; what it still had no way to ask
was *what does this person already have on file*. `OnboardingAPI.getEmployeeDetail`
was reaching `GET /api/resource/Employee/<name>` — the same path the funnel does
not carry credentials through, and the same 404 the other three had. So the
foreman could find Rosa, who has picked here four seasons running, and then had
to walk her through an I-9, a W-4 and a badge she already holds.
`get_employee` is now a guarded wrapper with a route, and `tools/employee.py` has
the read behind it.

**It found a second bug, and answering it raw would have shipped one.**
`i9_status` and `w4_status` are Custom Fields *this app* installs on Employee;
`create_employee` starts a hire at Pending/Missing and **nothing in this app ever
moves them again** — `submit_i9_section_2` writes `I-9 Form.status` and
`submit_w4` writes `W-4 Form.status`, each on its own doctype, and neither writes
back to the Employee row. `EmployeeDetail.satisfiedSteps` on the handset branches
on the *column*. Handing it over as stored would have told the wizard that every
worker who has ever completed a form still needs to complete it.

**So the columns are reconciled against the records, in one direction only.** A
live `Complete` I-9 or `Active` W-4 fills a column still sitting at its hire-time
default, and nothing else: `Expired` and `Requires-Update` are somebody's
deliberate statement and stand — an expired I-9 is precisely the case §1324a
wants re-verified, and the form that says Complete is the one that expired. The
site's own Select options are still the arbiter of the value written, so an
operator who edited them gets their value or none. `i9_status_recorded`,
`w4_status_recorded`, `i9`, `w4`, `i9_on_file`, `w4_on_file` and `reconciled`
carry the unreconciled truth beside it, because an alert rule reading the column
will disagree with this and somebody has to be able to see why. The real fix —
`submit_i9_section_2` and `submit_w4` writing the column through — is a separate
release, and would not help the seasons already worked.

**`badge_id` is a lookup, not a column.** `link_badge_to_employee` writes a
Bucket Log Badge Map row rather than a field on Employee, and only an **active**
mapping counts: a badge handed back in November is exactly the one step 5 has to
issue again in June.

**The one read on the mobile surface whose gate has an exception in it.**
`search_employees` applies `require_hr_role` flatly and should — it hands back an
entity's whole personnel register. This names one record, and a worker asking for
their own hire date, I-9 status and badge is not browsing the register. So the HR
role is required for anybody else's record and not for the caller's own, and the
caller's own is resolved through `Employee.user_id` rather than from the body, so
the exception cannot be claimed by naming somebody. Entity scoping does not bend
to it either: an Employee of an entity this account cannot reach reads as not
found whatever roles the account holds.

## 0.46.1 — 2026-08-07

**The second wall in front of step one, and it was this app's.** v0.46.0 gave the
wizard's Identity step a route that answers; it then refused every hire with
"this site's Frappe HR marks i9_status, w4_status, jurisdiction mandatory on
Employee, and the call did not supply them". Frappe HR has never heard of those
three. `compliance_fields.py` installs them as Custom Fields with `reqd=True`
from this app's own `after_migrate`, so `_mandatory_gaps` was reading a
requirement erpnext_mcp itself wrote and the message was sending the caller off
to argue with their operator about it. It blocked `create_employee`,
`onboard_employee` and the wizard alike — the phone is simply where somebody
finally noticed.

**A hire has a known state on all three, so it gets one.** `i9_status` starts at
`Pending` — the I-9 is a separate form with §1324a's three-business-day clock on
it and `create_i9_form` is step 3 of the same wizard. `w4_status` starts at
`Missing`, **not** the `Pending` the analogy suggests: that field's options are
On-File, Missing and Requires-Update, and `docs/compliance_fields.md` defines
Missing as "the employer withholds at the default single rate", which is exactly
true of somebody whose W-4 step has not run. `jurisdiction` is read off the
hiring entity's own Address and falls back to `OR`; a Washington entity gets
`WA` rather than being quietly paid under ORS 653.

**The defaults are in `tools/employee.py`, not in the wrapper.** Three lines in
`api/mobile.py` would have fixed the phone and left `onboard_employee` and the
MCP tool refusing, and a wrapper cannot pass a field the fourteen-field
allowlist does not carry anyway. So the allowlist is now seventeen — the three
compliance statuses joined it — and the defaults live next to the check they
answer. `api/mobile.py` gained the three as arguments and forwards them when the
app sends them, which it does not yet; a later build that asks the foreman which
state the crew is working needs no server change.

**The safety net is untouched.** `_mandatory_gaps` still runs and still refuses,
which is the right answer when an operator marks `date_of_birth` or `gender`
required: nobody can default a date of birth, and inventing one would be worse
than the refusal. What changed is only that this app fills in the fields this app
required. Every default it applies is named in `defaults_applied` and in the
note, because a record that quietly acquired an I-9 status is the record nobody
goes back to fix. `_mandatory_message` now says whose requirement it is reporting.

**The double could not have caught this.** `harness.add_field` dropped `reqd`, so
a Custom Field installed as mandatory read as optional and `_mandatory_gaps` was
unreachable for exactly the fields this app is the reason for. It carries the
flag now, and the new tests build the Employee columns from
`compliance_fields.py`'s own specs rather than restating them — a fourth field
marked required there and not defaulted here rebuilds the wall, and
`test_the_three_are_exactly_the_three_the_installer_marks_required` is what says
so.

## 0.46.0 — 2026-08-07

**The step before the nine.** v0.45.0 published onboarding, the crew clock and
the bucket sync, and the wizard still could not reach any of it: its Identity
step asks for `POST /api/resource/Employee`, `GET /api/resource/Employee?…` and
`PUT /api/resource/Employee/<name>`, the Tailscale funnel publishes
`/farmops/api/…` and nothing else, and a flow that 404s on step 1 never gets to
steps 2 through 5. `create_employee`, `search_employees` and
`reactivate_employee` are now guarded wrappers with routes —
`MobileAPI.swift` has named all three paths since Sprint 9.

**None of them writes an Employee itself.** `frappe.get_doc({...}).insert()` in
the wrapper would have been four lines and would have stepped around every rule
`tools/employee.py` has held since v0.18.1: the fourteen-field allowlist that
refuses `ctc` and `salary_structure` by name, the second-record check that keeps
one person off the dispatch board twice, this site's own mandatory fields read
off the meta rather than assumed, and `require_hr_role`. So `create_employee`
delegates to `employee.create_employee` and `reactivate_employee` to
`employee.update_employee`, exactly as the nine before them delegate to
`tools/i9.py` and `tools/shifts.py`.

**`status` is dropped and the app keeps sending it.**
`OnboardingIdentity.employeePayload` carries `"status": "Active"`, which is what
the tool writes anyway; what the argument would also buy is a phone that can
file somebody as Left on the day they were hired. `user_id` is refused for the
same shape of reason — a body that could link a login could point somebody
else's task history at an account it names.

**The one read on this surface with a role gate of its own.** Every other read
here is field work a picker is entitled to. `search_employees` is the entity's
personnel register — names, hire dates, employment types, and the people who
have LEFT, which is the whole point of it — so it calls
`employee.require_hr_role()` by hand rather than inheriting one. Status is
deliberately not filtered: a Left employee is who the search is for, and
`hr.list_employees` defaults to Active, which is why this reads the register
directly instead of calling it.

**A rehire overwrites `date_of_joining` with today, on purpose.** The I-9 opened
four screens later is checked against the hire date and §1324a's three-day clock
counts from the day this person started *this* time. The date it replaced is
reported in `changed` and lands in the audit row rather than being lost, and the
date is not an argument — a backdated rehire is a correction, made in the Desk.

**A mirror kind the contract suite did not have.** `ExistingEmployee` and
`CreatedEmployee` decode `String?` through Swift's synthesized `init(from:)`,
which is forgiving about a missing key and not about a wrong type — between
STRICT and LENIENT, and calling either of them LENIENT would have claimed a
tolerance the app does not have. `test_ios_contract.py` grew `NULLABLE` for it.

## 0.45.0 — 2026-08-07

**The nine methods iOS already knew how to call.** `MobileAPI.swift` has named
paths for onboarding, the crew clock and the bucket sync since Sprint 9;
`api/mobile.py` published fifteen wrappers and `farmops_api/routes.py` fifteen
routes, so all nine of those paths 404'd. This release closes the gap:
`create_i9_form`, `submit_i9_section_1`, `submit_i9_section_2`, `submit_w4`,
`link_badge_to_employee`, `sync_bucket_entries`, `start_shift`,
`add_worker_to_shift` and `end_shift` are now guarded wrappers with routes. No
new MCP tools — every one delegates to the tool that already existed.

**Four renames, so no phone has to be rebuilt.** `OnboardingI9Section1.apiParams`
sends `work_authorization_expiry` and the column is
`alien_work_authorization_expiry`; Section 2 sends `list_?_doc_type`,
`list_?_authority` and `list_?_expiry` against columns spelled `_doc_title`,
`_doc_authority` and `_doc_expiry`; the W-4 sends `dependents_under_17`,
`other_dependents` and `extra_withholding` against `_count`, `_count` and
`_per_period`. The backend moves, which is the trade `api/shape.py` already
states: the alternative is a new build on every phone in the valley to rename a
key.

**Section 1's legal names fall back to the Employee record.** The tool requires
`legal_first_name` and `legal_last_name` and the shipped app sends neither —
step 1 of its own flow already created the Employee with them, and asking
somebody to type their name twice on a phone in a packing shed is how a form
gets abandoned. Sent explicitly they win; a legal name and a payroll name
genuinely differ for some people.

**What the wrappers take away.** `foreman` is not accepted on `start_shift` and
is filled from the authenticated caller — OAR 437-004-1131 puts the water, shade
and rest obligations on a NAMED responsible person and §112.161(b) asks that
person to sign, and the phone in the hand at the start of the shift is that
person. `active` is not accepted on `link_badge_to_employee`, because
deactivating a badge is a decision about somebody's piece-rate made in the Desk.
`status` and `effective_date` are not accepted on `submit_w4`, because the
Active → Superseded chain is what answers "which W-4 was in force the day this
cheque was cut". `sync_bucket_entries` takes ONE company for the whole batch,
checked once against the caller's scope and stamped over whatever each entry
claimed, and refuses an entry that names its own picker — the badge is what
attributes a bucket and the Bucket Log Badge Map is what resolves it.

**A field worker cannot call these, and that is the design.** All nine reach
tools that gate on `employee.require_hr_role` or `kpi.require_kpi_role`, and the
only role in both those lists and `guard.FARM_OPS_ROLES` is Farm Manager. A
Field Worker or Foreman with a perfectly good grant clears all seven of
`guard`'s gates and is then refused by the tool with its own sentence. An I-9 is
a personnel record and a shift is a wage record; copying the gate up into the
wrapper, or widening it, would be two sets of personnel rules to keep in step.

**`sync_bucket_entries` is metered as an upload, not as a write.** A picker
works a morning with no signal and the queue drains in a burst when the phone
finds the yard's wifi; ten calls a minute would refuse most of it, and a refused
sync is a morning of somebody's piece-rate sitting on a device that might not
come back. The batch cap and the tool's own `entry_uuid` deduplication bound it
instead, so a client retrying because it never saw the answer is a no-op rather
than a double payment.

**Nine more mirrors in `test_ios_contract.py`.** The five onboarding methods go
through `FrappeClient.callVoid`, which throws the body away — so the mirror
records that honestly and asserts only that the answer is a JSON object, which
is the shape a later decoder can be added to without a server change. The crew
clock and bucket sync have no Swift decoder yet and say so.

## 0.44.0 — 2026-08-06

**BucketLog → ERPNext Piecework Bridge.** `payroll_integration.py` has read a
`bucket_logs` row off a shift since v0.35.0, and `tools/payroll.py` has
speculatively queried a doctype called `Bucket Log Entry` since the same
release — waiting on the one piece that was missing: the doctype itself, and
everything that gets a capture from an iPhone in an orchard into it. Eight
tools (377 total: 175 read, 202 mutating), three DocTypes.

**Bucket Log Entry is erpnext_mcp's OWN doctype now, not a hypothetical
external app's.** Through v0.43.0, `compliance_fields.py` grafted five FSMA
traceability columns onto a `Bucket Log Entry` it assumed belonged to a
separate "BucketLog bridge" app — the same pattern it uses for Spray Log
(`farm_precision_ag`) and Employee (`farm_hr`/`hrms`). v0.44.0 makes it
erpnext_mcp's own: the sync endpoint, the badge register and the doctype ship
together, so its Target entry moves from `mode="extend"` (Custom Fields
grafted on) to `mode="verify"` (declared fields, checked present) — the same
treatment Housing Unit and Field already get. `picker_id` is retired in
favour of a proper `employee` Link, resolved from `worker_badge`;
`crew_id`/`block_id`/`bin_id`/`shipment_id` ship as declared fields.

**The arithmetic is pure.** `bucket_bridge.py` reads no database, the same
split `model_registry.py` and `budget_engine.py` keep:
`validate_bucket_entry` checks a capture's shape, `resolve_badge_to_employee`
reads a pre-fetched badge map, `aggregate_session` computes a session's
totals from its own entries, and `entries_to_payroll_shape` reshapes synced
captures into exactly the `bucket_logs` row `payroll_integration._piece_units_for`
already reads — no change to that module's aggregation logic was needed, only
a new `attach_bucket_log_entries` helper that matches entries onto the shift
each employee worked that day. `tools/bucket_log.py` is the only place that
reads or writes a Bucket Log Entry, Bucket Log Session or Bucket Log Badge
Map document.

**Only an Accepted verdict is piece work.** A Rejected capture is the
on-device ML model saying the bucket was not actually filled;
`entries_to_payroll_shape` filters it out at the one place every
payroll-facing read of this data passes through, and `tools/payroll.py`'s
own bucket-log loader now does the same filtering for the production path.

**`sync_bucket_entries` deduplicates a resynced batch by `entry_uuid` rather
than failing it**, and an entry that fails validation is reported and skipped
rather than taking the other forty-nine captures in the batch down with it —
a device that never heard back for a batch it already delivered can resend
it safely. `link_badge_to_employee` backfills `employee` onto any
already-synced entry and session that carries the badge and had none
resolved yet, so a badge mapped after the fact still pays for what was
already picked.

## 0.43.0 — 2026-08-06

**ML Model Registry.** Volume Vision trains models and holds the weights; this
app never sees a weight and never computes a metric. What was missing was the
one fact Volume Vision has no reason to know: which of its trained models is
**deployed** — for which company, for which piecework activity — so an iOS app
(BucketLog, Farm Ops) has somewhere to ask instead of guessing or shipping the
answer baked into the app. Seven tools (369 total: 170 read, 199 mutating), one
DocType.

**The arithmetic is pure.** `model_registry.py` reads no database, the same
split `budget_engine.py` and `payroll_gl.py` keep: `validate_model_registration`
checks a candidate record's shape, `build_model_manifest` reshapes an ERPNext
record into **Volume Vision's own `to_dict()` shape**
(`uuid`/`name`/`class_names`/`metadata`) so an iOS client's existing manifest
parser reads this too, and `check_model_conflicts` says what activating a
candidate would supersede. `tools/ml_model.py` is the only place that reads or
writes an ML Model document.

**Only one model per (company, piecework_activity) is ever Active.**
`activate_model` sets `status=Active` and `deployed_at=now`; whichever other
model held Active for the same pair auto-transitions to `Deprecated`. The
invariant is enforced twice, deliberately: the tool computes and reports what
it is superseding before saving, using a database read `check_model_conflicts`
cannot do itself, and the DocType controller separately guarantees the
database cannot disagree — regardless of whether a save came through the tool,
the Desk, or a data import.

**`get_active_model` answers with data, not an error, when nothing is
deployed.** An iOS app polling at startup for "what should I be running" gets
`{"active": false, "model": null}` rather than a tool error when a company
has not activated a model for an activity yet — the honest state for a
farm that has not turned a feature on, not a failure.

## 0.42.0 — 2026-08-06

**Budget + Variance Alerts.** A `Budget` is one company's plan for one fiscal
year: which general ledger accounts and which Financial KPI Definitions it
tracks, what it planned for each, and — once `refresh_budget` has run — what
actually happened and how far apart the two are. Seven tools (362 total: 167
read, 195 mutating), three DocTypes, one compliance rule, one overnight sweep.

**The arithmetic is pure.** `budget_engine.py` reads no database, the same
split `payroll_gl.py` keeps: `compute_budget_actuals` fills in actual and
variance from plain dicts, `check_budget_variances` finds the rows whose
variance has crossed their own threshold, and `refresh_budget` is the two run
together. `tools/budget.py` is the only place that reads GL Entry — year-to-date
movement within the budget's own fiscal year, against the line's full-year
budgeted amount — or the KPI framework, which it reads from
`compute_kpi(..., use_cache=True)` rather than recomputing, so a budget and the
KPI dashboard can never disagree about a figure.

**Severity is a ratio of the variance to its own threshold.** Every line item
and KPI target carries its own `threshold_pct` (default 10): Warning at 1×–2×
that number, Critical past 2×, so a tightly-watched line and a loosely-watched
one escalate on the same rule wherever their own line was drawn.

**`refresh_budget` does not write a Compliance Alert directly.** It saves the
computed fields onto the budget; the new `budget_variance_breach` rule reads
them the way `financial_kpi_threshold_breach` reads the KPI cache, and the
hourly sweep is what turns a breaching **Active** budget into an alert on the
calendar — with the same dismissal, snooze and auto-clear every other alert
gets, rather than a second alerting path none of that machinery knows about. A
Draft or Closed budget's breaches never reach the calendar. The overnight cron
(`refresh_all_active_budgets`, 03:15 — fifteen minutes after the KPI cache job,
so every KPI target reads a same-night figure) only ever touches Active
budgets.

## 0.41.0 — 2026-08-06

**The job had a shape and three places kept it.** The template concept has been
in this app since v0.16.0 and has never had a record. What one recurring piece of
work looks like — its type, its skill, its duration, whether a foreman sends
somebody or a worker picks it up, what evidence closing it requires, what
compliance record completing it produces, and what a worker is actually meant to
check — lived in `ALERT_TASK_MAP`, a Python dict of thirteen recipes where moving
a habitability walk from forty-five minutes to sixty was a code release; in three
loose `producer_*` fields on every Compliance Rule, which worked but could not be
shared, so two rules asking for the same job stated it twice and drifted the
first time one was edited; and in whatever a foreman typed that morning. Five
tools (355 total: 164 read, 191 mutating), two DocTypes, one field repointed,
five seeded templates, and nothing wired.

**IT IS NOT AN INSPECTION TEMPLATE, AND THE DIFFERENCE IS LOAD-BEARING.** A
Cabin Opening is one trip producing a Housing Inspection, two Detector Tests and
a Water Test, out of one claim, at four separate regulated cadences — that is an
Inspection Template, and it is versioned by copy because a session submits
against its pinned version weeks after starting. A smoke detector test is one
job. Collapsing the two would make every detector test carry a sections table
with a single row in it, and make `sections[0].produces_record_doctype` the
answer to a question the Farm Task already has a field for.

**A TASK SNAPSHOTS ITS TEMPLATE, AND THAT IS THE LOAD-BEARING RULE.**
`create_task_from_template` copies the type, the skill, the duration, the
dispatch mode, the evidence contract, the produced record and its defaults, the
instructions and the whole checklist onto the task. After that the task is
self-contained — there is a test that deletes the template and completes the task
anyway. So editing a template changes what FUTURE tasks look like and nothing
else: a worker halfway through a five-item walk whose template lost an item does
not find their evidence attached to a list that no longer contains it, and
nobody's evidence contract tightens under them mid-job. It also means the record
needs no versioning by copy — nothing reads back from it, so versioning would buy
an audit trail `track_changes` already keeps at the cost of a register where the
eight templates an operation runs are lost among forty superseded rows.
`update_farm_task_template` edits in place and reports how many tasks the edit
cannot reach.

**THE CHECKLIST HAS TEETH, AND MOST TEMPLATES SHOULD NOT HAVE ONE.** A detector
test is a checklist; "renew the certificate before it lapses" is not, and a
one-item list saying *do the task* is a form people learn to tick without
reading — once that habit is formed the required flag stops meaning anything on
every template, so the tool warns when you author one without a checklist rather
than nagging for one. Where there is one, `complete_farm_task` REFUSES a
completion with a required item unticked, by name, before any compliance record
is written. The checklist is checked BEFORE the evidence contract: an unticked
required item says part of the WORK was not done, and the contract is about what
the work PRODUCED — telling somebody their photograph is missing when the real
answer is that they never tested the CO detector sends them back for the wrong
thing. An optional item left undone does not refuse, which is what keeps a
template covering more than today needs usable. A tick naming an item the task
does not hold is refused rather than ignored: a typo that silently marks nothing
looks exactly like a tick right up until the completion is refused for a
different item.

**`producer_task_template` WAS REPOINTED, AND IT HAD NO BEHAVIOUR TO LOSE.** It
linked to Inspection Template from v0.22.0 to v0.40.0 and nothing on the dispatch
side ever read it — multi-section visits are raised by matching a template's
sections against the records a place's pending alerts ask for, which never
consults this column. It now names a Farm Task Template and has behaviour for the
first time. Where a rule names one, the template is the WHOLE recipe and the
rule's inline `producer_farm_task_type`, `producer_skill_required` and
`evidence_contract_json` are not read; those three stay as the fallback for a
rule without one, which is most of them.
`generate_tasks_from_compliance_alerts` resolves in three steps — the rule's
template, then `ALERT_TASK_MAP`, then the rule's inline fields — at a cost of one
query for the whole sweep rather than four per alert. The alert still supplies
what only it knows: the severity that becomes urgency, the place, and its own
message, which lands AFTER the template's standing instructions because a worker
needs "how this job is done" before "what is wrong with this cabin". The
`repoint_producer_task_template` patch clears any value naming an Inspection
Template and prints each one by name — it has to, because a Link whose target
moved refuses the next save of any row holding an old value, which would turn a
rule an operator set into a rule nobody can edit. It clears rather than converts:
there is no honest automatic translation from a four-section visit to a single
task shape, and picking one section would be this app inventing an operator's
intent and then generating work from the invention.

**FIVE SEEDED TEMPLATES, AND SEEDING WIRES NOTHING.** Cabin Habitability
Inspection, Smoke Detector Test, Water Quality Test, Certification Renewal and
Training Record. Every task type, skill, duration, dispatch mode and evidence
contract on the first three matches `ALERT_TASK_MAP` to the letter and a test
asserts the equality — so pointing a shipped rule at its template raises exactly
the task it raised in v0.16.0, plus a checklist, and the day somebody edits one
table and not the other the build says so. `producer_task_template` is left
exactly as it was on every rule, so an upgrade changes no task any sweep
produces; pointing a rule at a template is a deliberate act by somebody who has
read what the template asks for. An upgrade that silently changed the shape of
the work a compliance calendar dispatches is the one thing this app will not do.
Seeded, not fixtured — the seeder checks by template name across every row, so an
operator who added an item to the detector checklist keeps it and one who
disabled a template their operation does not run keeps it disabled.

**`evidence_required` IS MANDATORY ON A TEMPLATE**, for exactly the reason it is
mandatory on a task: a template with no contract raises tasks with no contract,
`create_farm_task` refuses those, and the failure would land in front of whoever
is stood in the cabin rather than in front of whoever wrote the template. What is
NOT refused is a `creates_record` naming a DocType this site has not got — this
app installs doctypes over time and refusing here would make a template
unsaveable today for a spelling that will be right in a month; the refusal that
matters is at task creation, where somebody is about to be sent somewhere, and it
is there.

**THERE IS NO `delete_farm_task_template`.** A template that has raised work is
the answer to "what did this job ask for last season", and every task raised from
one links back to it. `enabled=false` retires one while keeping all of that
readable — the same doctrine `deactivate_inspection_template` and Training Type's
`active` flag follow.

## 0.40.0 — 2026-08-06

**Payroll into the general ledger.** v0.30.0 built an engine that computed a
payroll slip. v0.35.0 fed it the hours the foreman had already written down.
v0.36.0 drew the tax forms the slips add up to. And a completed payroll run
produced Farm Payroll Slips, correct to the cent, and a general ledger that had
never heard of any of it. Wages are the largest number on a farm's income
statement and they were the one number somebody keyed in twice — out of a
report, into a journal entry, by hand, every fortnight, with a transposition
waiting in it. Four new tools (350 total: 162 read, 188 mutating), three new
DocTypes, and one open loop closed.

**NO ACCOUNT NAME IS SHIPPED, AND THAT IS THE DESIGN RATHER THAN AN OMISSION.**
The mapping from a payroll component to a general ledger account is a RECORD —
`Farm Payroll Account Mapping`, one per company, one row per component. A
default would be right on the chart of accounts it was written against and
quietly wrong everywhere else, and "quietly wrong" in a chart of accounts is not
an error message: it is a year of wages sitting in an expense line nobody looks
at until the tax preparer asks why field labour halved. `configure_payroll_accounts`
writes the mapping, `get_payroll_account_mapping` reads it back with the gaps
named, and neither of them has an account name in its source.

**ELEVEN COMPONENTS, AND THE SPLIT IS ABOUT WHO OWES WHAT.** Six are
employee-side and together they are the two sides of gross pay, so all six are
required whatever the amounts are: gross pay debited to the wage expense, and
federal tax, employee Social Security, employee Medicare, state tax and net pay
credited to their liabilities. Five are employer-side — employer Social
Security, employer Medicare, FUTA, SUTA and the other state employer programmes
— and each of those is an expense AND a liability, because it is money the farm
owes on top of gross rather than money carved out of it. They stay five rather
than one because they are remitted to five different places on five different
schedules, and one "payroll tax payable" account makes every one of those
reconciliations a spreadsheet exercise.

**DRAFTS ONLY, THE SAME AS EVERY OTHER JOURNAL ENTRY THIS APP WRITES.**
`post_payroll_to_gl` inserts through `mutate.insert_draft_journal_entry` — the
one function in this app that writes a Journal Entry, with its never-submitted
assertion — and stops. There is no `submit_payroll_journal_entries` and there is
not going to be one, for the same reason there is no `post_journal_entry`:
arithmetic anybody can redo and a statement about money leaving the farm are
different acts, and they want different people in front of them.

**POSTING TWICE IS THE FAILURE THIS GUARDS HARDEST AGAINST.** A payroll posted
twice is a doubled wage expense against a doubled liability, and it is the
easiest mistake in the world to make from a chat client that lost its
scrollback. Every draft is linked back onto the run's `gl_postings` table and a
run with live entries against it is refused BY NAME. The check is about what is
in the LEDGER rather than what the table remembers: a run whose drafts were
deleted, or whose entries were cancelled, can be posted again, because then
there is genuinely nothing in the books. Four other refusals sit beside it — a
run that is not Calculated or Submitted, a company with no mapping, a mapping
with a hole in it, and an entry that does not balance — and all five are
reported AT ONCE rather than one per round trip.

**THE PREVIEW REFUSES NOTHING, ON PURPOSE.** `preview_payroll_gl` returns every
line of every entry, both totals and the balance check, with `would_post: false`
and the blockers listed. An incomplete mapping is exactly what somebody calls a
preview to find out about, and a preview that raised instead of reporting would
be useless for the one job it has. It and the posting go through one pure
function, so the entry described and the entry inserted cannot drift.

**THE PAYROLL ENGINE NOW KEEPS WHAT IT ALWAYS COMPUTED.** Employer taxes have
been calculated since v0.28.0 and stored nowhere: the slip carried the worker's
deductions and none of what the farm owed on top, so the employer share of FICA
alone — 7.65% of gross — never appeared on a record. Farm Payroll Slip gains
`social_security_employer`, `medicare_employer`, `futa`, `state_unemployment`,
`state_employer_other` and `total_employer_taxes`, and `calculate_full_payroll`
returns them plus `total_cost_of_employment`. No total moved; these are the same
figures the engines already returned, lifted to where they can be kept. Slips
written before this release carry zeros, and posting such a run says so in a
warning rather than leaving somebody to infer it from four zeros — an employer
with no employer taxes is a real thing, and an entry that quietly left them out
is not.

**STATE UNEMPLOYMENT IS NEW AND DEFAULTS TO ZERO.** State Tax Configuration
gains `suta_rate` and `suta_wage_base`, both employer-entered for exactly the
reason workers' compensation is: a SUTA rate is assigned to one employer by one
state agency out of that employer's own experience rating, and there is no table
anybody could ship. A site that enters no rate computes precisely what it
computed before this release — the addition is a place to put a number, not a
new charge. The wage base is consumed by year-to-date gross the way FUTA's
$7,000 is, and a base of zero means no cap.

**101 new standalone tests**, 5,478 in total.

## 0.39.0 — 2026-08-06

**Financial KPI Framework.** v0.19.5 shipped one KPI. v0.19.6 put it under a
window standard and showed the standard generalized by registering two more
beside it. All three are Python functions, and adding a fourth is six lines, a
test, a review, a release and a deploy — a perfectly good process for a KPI this
app's authors chose, and no process at all for a KPI somebody's lender asked
about on a Tuesday. Every operation has two or three ratios that are genuinely
its own: a packing house watches cost per bin, an operation carrying an equipment
note watches debt service coverage on the covenant's own definition, a family
office watches distributions against normalized cash flow. None of those belong
in a shipped app and all of them belong on the dashboard of the farm that needs
them. So a KPI is now a RECORD. Seven new tools (346 total: 160 read, 186
mutating), one new DocType, one new compliance rule, one new scheduled job.

**The engine does not move, and that is the release rather than a caveat.**
`formula_type` has exactly two values and both are deterministic. `Built-in`
delegates to a computer that ships with this app, reviewed and tested like any
other code, while the record owns the window, the step, the lookback, the four
thresholds, the dashboard position and the switch — and owns nothing about the
arithmetic. `Expression` evaluates the text over the variables
`expression_inputs` names, parsed to an AST with every node checked against an
allowlist of arithmetic and walked by an evaluator with no builtins, no attribute
access, no subscripts, no statements and no name binding. **There is no third
value and there is no field on the record that holds Python.** That is a
deliberate difference from `Compliance Rule.custom_python`: a compliance rule can
need to express a shape no set of fields captures, and a financial KPI is a
number divided by another number. Two sandboxes, on purpose — merging them would
mean the finance side inherited every node the compliance side needs.

**Refused at SAVE time, not at compute time.** `import`, every attribute access
(`x.__class__.__bases__[0].__subclasses__()` needs no imports at all),
subscripts, comprehensions, lambdas, string constants, calls to anything but
`min`/`max`/`abs`/`round`, and exponents above 64. Also refused: a Built-in
naming a computer this site has not got, an expression reading a variable
`expression_inputs` does not define, a critical threshold on the wrong side of
its warning threshold, and a KPI that reads itself. The failure every one of
these prevents is the quiet one — a definition that saves, computes nightly,
draws a line, and is wrong in a way nothing announces.

**Every KPI goes through the window standard and none of them gets its own.**
`compute_kpi` builds a computer and hands it to `windowed_reports.compute_windowed`
— the same function `get_windowed_report` has gone through since v0.19.6 — so the
boundaries, the fiscal-year anchoring, the cache, the history, the statistics and
the partial-window warnings behave identically for a KPI typed into a form this
morning and for the one that shipped in v0.19.5. There is a test asserting the
two produce the same figure. A framework whose new KPIs get a second, simpler
window implementation is one where the new KPIs are quietly wrong at the fiscal
year boundary, and nobody finds out until a lender does.

**A definition holds the question and never an answer.** Nothing on the record is
a number from the ledger. Every figure is computed when asked for, cached in
`Financial KPI History` with the components that produced it, and derivable again
— which is what makes an Expression KPI as defensible as a built-in one: the
components carry every input with how many accounts it matched, how many GL
entries, which way round it was read, and whether it was a balance or a movement.

**`kpi_id` is the cache key, so it is unique and it cannot move.** A Compliance
Rule is versioned by copy; a KPI is not, because an alert is an EVENT with a
definition behind it and a KPI is a LINE — and a line assembled from two
definitions of one number is a chart with an unmarked join in it. `update` refuses
a rename outright and reports an arithmetic change as a decision with the cached
row count in front of the caller.

**Four input sources.** `gl` reads GL Entry directly so every figure traces back
to rows somebody can open; `"balance": true` makes it a POSITION at the window's
end rather than a movement across it, because a current ratio built from twelve
months of movement in a cash account is not a current ratio. `report` reads a
component off a shipped computer. `kpi` is another definition, with cycles refused
at save time and caught with a named chain at compute time. `constant` is a number
with a name. The `natural` sign reads credit-balance roots as credits less debits
so every figure comes out positive on a well-kept set of books.

**Thresholds go to the compliance calendar, not to a second alerting system.**
`financial_kpi_threshold_breach` is the twenty-first shipped rule and the first
about money rather than a regulator, under a new `Finance` category, with the
same dismissal, snooze and auto-clear as everything else — nobody closes it,
because there was never a task, there was a number. It is built-in for a reason
neither other built-in has: its thresholds are not on its own row, they are on
each definition, because a ratio's warning line and a dollar figure's warning line
cannot share a column. **It reads the cache and never computes** — the sweep runs
hourly beside somebody's real work — so an alert and a dashboard can never
disagree about the value. A KPI with no cached value raises nothing AND DISMISSES
NOTHING.

**Empty is not zero and `No thresholds` is not `OK`.** An omitted threshold means
that direction is not bad for this KPI; a zero means a negative value is a
warning. "Nobody drew a line" and "inside the line" are different statements, and
`compute_all_kpis` names the unwatched KPIs, because an empty `breached` list is
not by itself a healthy operation.

**The seeder writes exactly one definition** — Sustainable CF/Acre, on the
computer that has shipped since v0.19.5, adopting the `kpi_key` the cache has
been using since v0.19.6 so the existing series continues. A seeded KPI is a claim
that this app knows what an operation should watch, and it can only honestly make
that claim about a metric it also ships the computer for. No thresholds are seeded
either: a defensible floor under cash flow per acre is a number about one
operation's own cost structure.

**The eighth scheduled job**, `refresh_all_kpi_caches` at 03:00, between the
shipped-report sweep and the regulation feed. One job that iterates, which here is
load-bearing rather than tidy: the whole point of the release is that an operator
adds a KPI without a code release, and a KPI needing its own scheduler entry would
be one they could not add. It shares `enable_kpi_history_sweep` with the 02:00 job.

**The existing tools got two things.** `recompute_kpi_history` accepts a
`kpi_key` that names a definition and delegates to the framework, which reads that
definition's own window rather than assuming a monthly TTM.
`list_financial_kpi_history` returns a `definitions` block joining each key to its
record — title, unit, category, thresholds — and reports orphaned keys. Sites that
have not migrated the DocType get exactly the payload v0.38.0 gave.

82 new standalone tests (5,377 total).

## 0.38.0 — 2026-08-05

**Regulation Feed and Change Detection.** v0.22.0 made a compliance rule a
record. v0.37.0 let a model draft one from regulation text and a person approve
it. Both of those are about the moment a rule is written, and neither has
anything to say six months later, when OR-OSHA renumbers a subsection or a
certifier reissues a handbook — so the rules got easy to write and stayed exactly
as hard to keep current. This is the pointer that was missing: a URL, a hash of
what was there last time, and a job that looks. Seven new tools (339 total: 156
read, 183 mutating), two new DocTypes, one new scheduled job.

**It detects and it does not remediate, and that line is the design.** A changed
page is evidence that somebody should read a regulation again. It is not evidence
about what the regulation now says, and it is not authority to rewrite a rule
firing on somebody's compliance calendar. The failure mode of the other design is
specific: a page reflows, a scraper reads a threshold out of the new layout
wrong, a rule is silently updated, and an operation spends a season inspecting to
a number nobody chose. So a detected change writes four things — a hash, a
timestamp, a log line, and the `rule_id` of every rule derived from that source —
and stops. Where a rule genuinely needs to change, the path is the one v0.37.0
built and this release does not go round: `propose_compliance_rule` drafts the
replacement DISABLED with its citation, and `approve_compliance_rule` supersedes
the live rule with somebody's name on it. There is a test that a rule row is
identical after a detected change, and it is the whole guarantee.

**The hash is of normalised text and not of the bytes, because a detector that
always fires detects nothing.** A rulebook page carries a "Last updated 08/05/2026
14:02" stamp, a session nonce, a build hash on its stylesheet and a copyright
year, and a hash of the bytes reports a change on every check for ever — the
first week somebody reads those alerts, the second week nobody does, and the
month the rule actually changes the alert is indistinguishable from the noise it
has been buried in. `normalise` throws away scripts, styles, comments, tags,
entity escapes, ISO and US and month-name dates, clock times, long hex strings
and every whitespace difference. **The cost is stated rather than hidden: a change
that is ONLY a date is invisible**, and there is a test asserting exactly that, so
a release that changes its mind about the trade has to change it on purpose.

**`Regulation Feed`, and the four fields that are the detector's own memory.**
`last_content_hash`, `last_checked`, `last_change_detected` and `change_log` are
written only by a check and are REFUSED as arguments to `update_regulation_feed`
— a hash somebody typed is a change that will never be reported, and a change log
somebody edited is the one record here whose entire value is that nobody edited
it. The log is append-only, oldest-first on the record and newest-first when
read, and at its cap the OLDEST lines are dropped with a line saying so: a
detector whose log filled up and stopped recording would be one that quietly
switched itself off.

**Four outcomes, and each of them lands on the record.** BASELINE — a first check
cannot be a change, so `last_change_detected` stays empty and a feed registered
today does not top every "what moved" list. UNCHANGED — `last_checked` moves and
nothing else does, and no log line is written, because a weekly feed accumulating
a hundred and fifty "still the same" lines a year is a log the changes are buried
in. CHANGED — both hashes, the size of the normalised text, and the rules to
re-read. ERROR — status and message on the record, and **`last_checked`
deliberately not moved**, so a monthly feed that failed today is retried tomorrow
rather than in thirty days.

**Paused is the kill switch; Error is a report.** `Error` is what the last check
said, not a decision anybody made — the sweep retries an errored feed and a
successful check clears it back to Active with a RECOVERED line. `Paused` is the
decision, it is the only state that keeps a feed out of the sweep, and it keeps
the whole change log. There is no `delete_regulation_feed`, for the reason there
is no delete on a Compliance Rule: a source this operation watched for two
seasons is a record of what it was watching and when.

**The seventh scheduled job, at four in the morning.** `sweep_due_feeds` is the
second job in this app that talks to somebody else's server and the first that
talks to a different one per record. It writes only `Regulation Feed`, it never
raises, and one agency site behind a WAF does not stop the other eleven being
checked. Each feed's `check_frequency` is the floor and the cron is the ceiling,
exactly as `Weather Settings.fetch_interval_minutes` is: the sweep cannot run
more often than it is scheduled, so a Monthly feed is skipped on the twenty-nine
mornings it is not due. `check_all_regulation_feeds` runs the same function, so a
manual sweep cannot disagree with the nightly one.

**Three reads ship ON and four writes ship OFF.** The register is readable out of
the box; nothing reaches out of the box. Two of the four writes make outbound
requests to servers this operation does not own, which is a decision an operator
makes rather than inherits — and until the switch is ticked, nothing on the site
talks to anybody.

## 0.37.0 — 2026-08-05

**AI-Proposed Compliance Rules.** v0.21.0 declared
`propose_inspection_template_from_regulation` and left it refusing; v0.22.0 did
the same for `propose_compliance_rule`. Both said in the sentence they refused
with exactly what would fill them. This is that. One new tool
(`approve_inspection_template`, 332 total: 153 read, 179 mutating), two declared
surfaces filled — which moved no count, because they were always tools.

**Nothing here calls a model, and that is the whole design.** The AI doing the
proposing is the MCP client: it reads the regulation, it drafts the record, it
passes the record here as structured arguments. What the tools do is the part a
proposer cannot do for itself — refuse the wrong shape, stamp the provenance it
does not get to choose, land the draft OFF, and put what needs a second pair of
eyes onto the row where the approver will see it. A validator and a gate, not an
author. The runtime is exactly as deterministic as it was: every alert still
traces to a rule, a citation, an approver and the field that crossed a threshold.

**Four rails, written once in `erpnext_mcp/proposals.py`** so a rule proposal and
a template proposal cannot be safe in different amounts. *It lands off* —
`enabled`/`active` forced to 0 whatever was passed. *It says who wrote it* —
`authored_by` forced to `AI-proposed`, and a caller passing `Operator` is refused
rather than quietly corrected, because that argument is an attempt to launder
provenance. *It says where it was read* — `ai_source_citation` required, built
from a URL and a section or written out, with a short excerpt of the regulation's
own words quoted beside it. *It cannot sign itself* — the approver, the approval
date, the approver's employee and their signature are refused as arguments.

**Propose new, or propose an update. Never a delete and never a disable.** A
proposal for a `rule_id` that already exists is drafted at version+1 and touches
nothing: the live rule goes on running on its own definition until a person
approves the replacement, and the result carries the field-by-field diff, because
what a reviewer of an edit needs is what changed. The supersession happens at
approval, by the person approving — old row disabled, pointed at the new one,
never edited, every alert it raised left exactly where it was. The two tools that
stand something down still take a written reason from a person; a model that has
decided a rule is obsolete can say so, and cannot act on it.

**`custom_python` is flagged, and the flag has teeth.** The sandbox refuses what
it refuses; what it cannot say is whether a program that runs perfectly asks the
right question. So a draft carrying one — or a producer assignee expression,
which is the same sandbox and the same judgement — gets a flag on a new read-only
`ai_review_flags` field, and `approve_compliance_rule` refuses it until the
approver passes `accept_ai_authored_code`. The refusal prints the program back at
them: an acknowledgement of code nobody displayed is not one. The gate is on code
a *model* wrote; gating rules somebody typed here would train everybody to pass
the argument every time, which is how a gate stops being one. The flags travel
across an edit, so superseding a flagged draft is not a way to launder it clean.

**`approve_inspection_template` is the counterpart the templates half was
missing.** A template is live the instant it is written, so the only thing between
a plausible draft and a worker's screen is that a proposal lands inactive — and
the only thing that makes inactive useful is a way to turn it on with a name
against it. It records the approver and the date on the record, and where a live
template already holds the name at a lower version it deactivates that one and
points it here: superseded, never edited, so every session already worked from it
stays readable against the sections the worker actually saw.

**Six new fields, no new DocTypes.** Compliance Rule gains `ai_review_flags`.
Inspection Template gains the same three-word provenance and the same two
approval columns the Compliance Rule has carried since v0.22.0 — `authored_by`,
`ai_source_citation`, `ai_review_flags`, `human_approved_by`,
`human_approved_on` — and the templates this app seeds now say `System`, because
a seeded row reading `Operator` puts the words in somebody's mouth. All additive:
every existing row reads exactly as it did.

**All three tools ship OFF**, separately, because "let the model draft rules" and
"let somebody turn a drafted form on" are two decisions.

## 0.36.0 — 2026-08-05

**Tax Form PDF Rendering.** v0.34.0 computed the boxes. This draws them: a
letter-size portrait page per form, in the official box and line numbering, with
the computed value in each box, attached privately to the Tax Form record's
`generated_pdf` field. Six layouts — **W-2** (Copy B), **1099-NEC** (Copy B),
**Form 941**, **OR-WR**, **OQ** and the **Washington ESD quarterly report**.

**`erpnext_mcp/form_pdf_renderer.py` is pure**, on the same contract as
`form_generators.py` and `payroll_calc.py` before it: a form dict and two blocks
of identifying text go in, PDF bytes come out, no database read and no side
effect. Which is what lets a test assert that box 1 says `4,000.00` against a
fixture, rather than that a renderer ran without raising.

**Nothing is recomputed.** The page renders `form_data_json` exactly as it was
calculated at generation time. That is the whole reason v0.34.0 stored those
values instead of recomputing them on read, and a renderer that recalculated
would produce a page disagreeing with the record it claims to render. Rendering
moves no status and changes no figure — a Filed form can be rendered and stays
Filed.

**Every page is a working copy and says so twice.** A header note on every page,
and a footer block naming the agency and the channel the form is really filed
through: Copy A of a W-2 or a 1099 is red-ink scannable stock or an electronic
filing (BSO, IRIS/FIRE); 941 goes on the official form with deposits through
EFTPS; OR-WR through Revenue Online, OQ through Frances Online, the ESD report
through EAMS. A page that only exists because a forty-employee wage detail
overflowed carries both as well — the disclaimer is drawn as the last act of
every page, so a page cannot exist without it.

**The generator's `warnings` print in full**, under *Before this form is filed*
at the foot of each form. They are the only place a form says which of its
figures is a floor rather than a figure, and a page that dropped them would look
more certain than the arithmetic behind it. Four digits of a Social Security
number and never nine, exactly as v0.34.0 computes them.

**Two tools, both default OFF.** `render_tax_form_pdf` renders one form;
`bulk_render_tax_form_pdfs` renders a set — every W-2 for a tax year, every 941
for a company — selected by `names` or by the same filters `list_tax_forms`
takes. Three refusals worth naming:

* **A form that already has a PDF is refused** unless `overwrite` is passed. That
  field may hold the copy somebody reviewed, or the one the agency issued, which
  nothing here can reproduce. Overwriting repoints the field and leaves the
  earlier File attached — the record gains and never loses.
* **A form with no computed values is refused** rather than drawn. A page of
  zeroes from an empty record is indistinguishable from a page of real zeroes.
* **A bulk selection larger than `limit` is refused, not truncated**, and a bulk
  run with no selector at all is refused outright. A batch that silently stopped
  short would look like it had covered everything. Inside a batch the opposite
  rule applies: a form that already has a PDF is skipped and counted, and one
  that fails is recorded by name and the run continues, so `rendered`, `skipped`
  and `failed` between them account for every form matched.

**One new dependency, imported defensively.** `reportlab` draws the pages, and
joins `shapely`, `h3` and `segno` in being optional at import time: a bench
without it loses exactly these two tools — which say so by name with the pip
command to fix it — and every tax form's computed values stay readable through
`get_tax_form`. The numbers are the deliverable; the page is a convenience. CI
runs the whole suite without it first, for the same reason it does for the
geospatial pair.

## 0.35.0 — 2026-08-05

**Payroll Integration.** v0.30.0 built an engine that could compute a payroll
slip and v0.19.3 built a shift register that recorded the hours, and the join
between them was a stub: `_load_shifts` returned the crew's whole span for every
worker, zero overtime and zero piece units. Every figure past "how long was the
shift" had to be keyed in by hand, which is a payroll with two sources of truth
and one of them wrong. This release is the join.

**`erpnext_mcp/payroll_integration.py` is pure**, on the same contract as
`payroll_calc.py`, `form_generators.py` and `withholding.py`: no database reads,
no side effects, every input an argument. `aggregate_shifts_for_period` turns
shifts into per-employee timesheet totals, `build_payroll_inputs` marries those
to salary structures and tax configuration, and `run_integrated_payroll` runs
the whole thing end to end.

**The unit of work is a segment, not a shift.** A shift is crew-shaped and
payroll is person-shaped, and v0.19.3 already resolved that — every crew row
carries its own `joined_at` and `left_at`. So the thing that gets aggregated is
one person's own span on one shift. "The crew worked 06:00 to 15:00" and "Ana
joined at 07:10 and left at 13:00" stay two different facts, and the one payroll
reads is Ana's. Paying her the crew's nine hours would be paying her for two she
was not there for; paying the crew her six would be the wage claim.

**Overtime is a weekly question, answered chronologically.** Oregon HB 4002 and
Washington SB 5172 both put agricultural overtime at 40 hours in a workweek,
both fully phased, both at 1.5x. A pay period is not a workweek: forty-five
hours in week one and thirty-five in week two is five hours of overtime, and
comparing an eighty-hour biweekly total to eighty finds none of it. Segments are
bucketed into seven-day weeks anchored at the period start — `workweek_anchor`
moves it for an employer whose declared week does not line up — and each week is
walked in time order.

Chronological rather than proportional, because it also decides **which state**
the overtime was worked in. A picker who spends Monday to Thursday in Oregon and
Friday in Washington crossed forty on Friday, so the premium is Washington's
hours and Washington's tax allocation. Splitting it pro rata would be tidier
arithmetic about a day that did not happen.

**Breaks come in two kinds and they do not behave the same.** `break_hours` is
paid rest: it stays inside the hours worked and is handed to the engine
separately so a piece-rate worker's break is paid at the average hourly the
period earned (WAC 296-131-020, OAR 839-020-0050). `unpaid_break_hours` is the
meal period: it comes off the span, and counts toward neither hours worked nor
the overtime threshold. Conflating the two is the standard way a farm ends up
owing back wages.

**Minimum wage is checked per state, and reported rather than remedied.**
Washington's $16.66 against Oregon's $14.70 are different floors, and an average
across both would let a compliant Washington week paper over an Oregon week that
was not. Each state's shortfall is priced — what it would cost to bring those
hours up to that floor — and stops there. A payroll engine that quietly inflated
gross pay would hide the fact that a piece rate is set too low to be lawful.

**Piece units come from whichever source the site has, and the result says
which.** The BucketLog bridge's Bucket Log Entry, a count column on Farm Task
Assignment, a count column a site has added to the crew row — all three are
looked for, all three are summed rather than preferred, and every source that is
*absent* is named. A piece-rate run that found no bucket log has produced a set
of zeros, and whether that means nobody picked or means the bridge is not
installed is the whole difference between a payroll and a mistake. A bucket log
row with no count column is **one bucket**: the row is the record of the bucket.
Piece work on a day with no shift behind it is still paid, carries no hours, and
is counted in `piece_rows_without_a_shift`.

**Three new tools.** `get_employee_timesheet_summary` (READ, default ON) is the
hours without the money — the answer to "why is my cheque this?", and it needs
none of the payroll switches because the hours are not the payroll.
`preview_payroll_for_period` (READ, default ON) computes a whole company's
period and writes nothing. `run_payroll_for_period` (MUTATING, default OFF) does
the identical calculation through the identical code path and stores it as a
Farm Payroll Entry in Calculated status. Submitting stays `submit_payroll`,
behind its own switch: arithmetic anybody can redo and a statement about what
the farm is paying are two different acts.

**A run with problems in it is not refused.** A worker below minimum wage, a
shift nobody ended, a picker with no salary structure — all reported on the
result, none of them a reason to hold up everybody else's pay. The same posture
`end_shift` takes towards a shift with an unmet obligation: state it, keep it.
The one refusal is a run where *nobody* can be paid, and it names them.

**An employee with hours and no salary structure is never zeroed.** They come
back in `employees_missing_structures`, by name, with their hours. Zero is a
number, and a number nobody questions.

**The v0.30.0 tools were rewired, not left behind.** `preview_payroll` and
`calculate_payroll` read through the same aggregation, so the single-employee
preview now reports the same hours, the same overtime and the same piece units
as the company-wide run. `_load_shifts` also carried a real bug — the same
filter key twice in a Frappe dict, where only the second survives, so the
start-date bound was silently discarded — which is fixed by passing a list of
conditions.

**Year-to-date carries across periods.** The Social Security wage base is an
annual per-person cap, so a period run in isolation would restart it and
over-withhold on anybody who has crossed it. Prior `Calculated` and `Submitted`
entries in the same calendar year are read first.

## 0.34.0 — 2026-08-05

**Tax Form Generators.** Three releases put the arithmetic in place — federal
withholding (v0.28.0), the Oregon and Washington engines (v0.29.0), salary
structures and the payroll engine (v0.30.0). This one turns a year or a quarter
of it into the six forms an agricultural employer in those two states actually
files: **W-2**, **1099-NEC**, **Form 941**, **Oregon OR-WR**, **Oregon OQ** and
the **Washington ESD quarterly report**.

**`erpnext_mcp/form_generators.py` is pure**, on the same contract as
`payroll_calc.py` and `withholding.py`: no database reads, no side effects,
every input an argument. A W-2 can be computed from a fixture and checked
against a number somebody worked out on paper, which is the only way an
arithmetic claim about somebody's wages is worth making.

**Every form carries a `warnings` list, and it is the part to read first.**
Three things a generator cannot work out for itself, each of which is a real
limit rather than an unfinished edge, and each of which says so on the form
rather than quietly picking a number:

* **Which state a dollar was earned in.** A slip carries one `work_state` — the
  state the worker spent the most hours in — but a cross-state pay period
  allocates gross between the two. The slip's `state_wages` is that allocation
  when the caller has it; without it the whole gross lands on `work_state`, and
  only a slip that genuinely ran two engines raises the flag.
* **Year-to-date wages before the first slip in hand.** The Social Security wage
  base and Washington's UI taxable wage base are annual per-employee caps, and a
  quarterly form cannot see the quarters before it. Absent
  `ytd_wages_by_employee` the cap is applied to the quarter alone — exactly
  right for Q1, and an overstatement in every quarter after it. The form says
  which it was.
* **Additional Medicare, separately.** A slip's `medicare` is the ordinary 1.45%
  and the 0.9% surcharge added together, because that is what comes out of a
  paycheck. Form 941 line 5d needs the surcharge on its own, and where the two
  were never stored apart the line is zero and the form says why.

**The wage bases are consumed employee by employee, not against the company
total.** Two workers at $100,000 each are $200,000 of Social Security wages on a
941, not $176,100 — applying an annual per-person ceiling to a company's grand
total would let one high earner's headroom absorb everybody else's excess. It is
the kind of mistake that produces a plausible number and an assessment letter.

**`form_data_json` is written once and read back verbatim.** A filed form is a
statement about what an employer told an agency, on a date. Payroll gets
corrected — a slip is amended, a shift's hours are fixed, a rate changes — and a
`get_tax_form` that recomputed from today's data would quietly return a
different W-2 than the one in the envelope. `regenerate_tax_form` is a separate
tool behind a separate switch, and it reports what moved: which value, from
what, to what. It refuses a Filed form unless `allow_filed` is passed, because
recomputing one replaces the record of what was actually sent.

**Only `Calculated` and `Submitted` payroll entries are counted.** A `Draft`
payroll has not been paid and a `Cancelled` one was not.

**Nothing here files anything.** No transmission, no official scannable Copy A,
no deposit schedule. The tools compute box and line values; a person reads them
onto the real form or into the agency's portal, and `mark_tax_form_filed`
records that they did, with whatever confirmation number came back. Filing the
same form twice is refused — it would overwrite the date and confirmation of the
filing that actually happened.

**Social Security numbers stay four digits.** Every form prints `XXX-XX-1234`,
read off the I-9 — the one record on this site that legitimately holds any of
it — and the person filing completes the rest from the paper. Same judgement as
`generate_1099_prefill` made in v0.11.0, for the same reason.

**One new DocType, `Tax Form`**, and one new field on `State Tax Configuration`:
`employer_account_number`, which is Oregon's BIN and Washington's ESD account
number. Without it every state return prints a blank where its account number
goes, and the alternative — an argument the model must be told each time — is
worse than a field an operator sets once.

**Five new tools** (326 total: 151 read, 175 mutating): `list_tax_forms` and
`get_tax_form` read; `generate_tax_form`, `regenerate_tax_form` and
`mark_tax_form_filed` write, all three default OFF.

### The boundary editor, on a record that does not exist yet

A patch to v0.33.0's map, client-side only — no new tool, no schema change.

**The map and the draw tools now appear on a NEW Parcel, Field or Irrigation
Zone**, and Save Boundary on one creates the record first and sets the boundary
second, in a single press. v0.33.0 withheld the map until the record was saved,
because `save_boundary` needs a docname and the boundary tools read the recorded
acreage out of the database to compare the drawn area against. Both are still
true; the order is now enforced in the widget instead of being demanded of the
operator. Nothing skips validation — `frm.save()` runs every mandatory field and
every doctype check before a polygon is offered to anything, and a record that
will not save does not get a boundary.

**The county import works the same way on a blank Parcel form.** Importing a
Wasco County tax lot fills in the parcel ID, the acreage and the county, saves
them, and only then applies the polygon — because those are precisely the values
`set_parcel_boundary` checks the shape against, so they have to be on disk
first.

**Every `frappe.call` in the widget now has a `.catch`.** It did not, and the
failure that produced was the worst available: a rejected promise nobody
listened to, so an expired session or a dropped connection lifted the freeze,
re-enabled the button, left the drawing where it was and said nothing at all. A
boundary that silently did not save looks identical to one that did. Where the
server refused on purpose — a self-intersection, an acreage that disagrees by
half — Frappe has already shown that sentence and it is not repeated.

**A block's parcel outline now appears when the parcel is chosen**, rather than
only on the next refresh. On a new Field the link starts empty, which is exactly
when somebody wants the deed line to trace against. A form where a shape has
already been drawn is left alone; losing a traced boundary to a link field would
be the worse trade.

## 0.33.0 — 2026-08-05

**Interactive Map Drawing + Wasco County GIS Import.** v0.32.0 put a map on seven
forms and made a point of the fact that none of them could write. This release
gives the three forms that carry a polygon — Parcel, Field, Irrigation Zone — a
draw toolbar and a Save Boundary button, and the sentence that mattered in that
refusal is unchanged: a boundary is compliance evidence and it is set through the
three boundary tools.

**The draw tool is a second caller of those tools, not a way round them.** The
polygon goes to `erpnext_mcp.api.gis.save_boundary`, which calls
`set_parcel_boundary`, `set_field_boundary` or `set_zone_boundary` unchanged —
the shape is parsed, a self-intersection is refused, the enclosed area is compared
against the recorded acreage and a disagreement past a quarter is refused
outright, containment against the shape above is reported, and every derived field
is recomputed. A vertex nudged by accident gets an area disagreement on screen
rather than a quiet save. Two shapes on one record become a MultiPolygon — a
parcel cut in half by a county road is two pieces of one parcel.

**No area is computed in the browser**, and that is deliberate rather than lazy.
Leaflet.draw offers a live acreage readout while you drag; taking it would put a
second area implementation in the app, in a different language, and the day it
disagreed with `geo.area_acres` by three percent nobody would know which figure
the compliance record was built on.

**Satellite imagery is the default layer**, with OpenStreetMap one click away in
a layer control. A street map cannot be traced against: an orchard block's corner
is a change in canopy, a headland or a road edge, and none of those is on a street
map — which for most of this county draws two roads and a lot of white.

**Wasco County's tax lots can be imported directly.** Type a tax lot number
(`2N11E35BA-01600`), or press Find Under a Point and click the parcel on the
satellite map. The shape is drawn dashed on the map **before** anything is
applied, next to the block the operator already knows, because a tax lot number
typed with one character wrong returns a real parcel somewhere else and every
number on it looks plausible. The county's polygon is what the assessor, the deed
and the tax bill are all describing, which is a far better starting point than
tracing an outline off an image by eye.

**The county request is proxied by the server and never made by the browser.**
CORS is not ours to promise, the URL belongs in one place rather than in a cached
JavaScript file, and the ArcGIS `where` clause is a query language the browser is
the wrong place to be careful about. The tax lot is checked against an allowlist —
letters, digits, dots and hyphens — rather than escaped, because escaping means
implementing somebody else's SQL dialect correctly with no way to test it. There
is no argument anywhere from which a URL could be built: the host and path are a
literal in `api/gis.py`.

**Two whitelisted methods, and the permission gate had to be rebuilt for them.**
`security.authorize()` does not run on a `@frappe.whitelist()` path, and
`api/guard.py` is the wrong gate — its Mobile Access Grant check would mean an
operator could not draw a boundary on their own Desk until they had enrolled
themselves as a field device. So: a named user, `frappe.has_permission(doctype,
"write", doc=name, throw=True)` on the **specific** document, and a closed list of
three doctypes with no dispatcher. That last check is the one that would have been
easy to skip — the boundary tools save with `ignore_permissions=True`, correct for
them, and a wrapper that trusted the framework would have handed every signed-in
account a write to every parcel.

**The `allow_<tool>` switches are deliberately not consulted**, the same call
v0.17.1 made for the phone. Those switches are the model's leash;
`allow_set_parcel_boundary` off means "the model may not redraw the farm", and
reading it here would mean an operator who distrusts the AI also loses the ability
to trace a parcel by hand.

**What the import fills in, and what it refuses to.** An empty field being filled
is not an overwrite: `parcel_id` from `MapTaxlot`, `acreage` from
`CalculatedAcres`, `county` from the service label — each only if blank, with any
difference reported rather than applied. `title_holder` is **never** set: the
county's `Taxpayer` is free text off a tax roll and `title_holder` is a Link to a
Related Party, and matching one to the other by string is how a parcel ends up
owned by the wrong entity in an accounting system.

**No new MCP tools, no new doctypes, no new hook keys.** The draw plugin and both
stylesheets are fetched from a CDN by the widget when a form that needs them is
opened — there is no `app_include_js` or `app_include_css`, so the four read-only
map forms never fetch the plugin at all.

**Forty-eight new tests** in `tests_standalone/test_gis_import.py`, including the
three that pin the mistakes this integration is most likely to make: `x` is
longitude and `y` is latitude (swapping them asks about the Southern Ocean, which
comes back empty rather than wrong, so nothing would ever say what happened); an
ArcGIS error arrives as an HTTP 200 with no `features` key (a client checking only
the status reads a malformed query as "no such parcel"); and `outSR=4326`, without
which the county answers in Oregon Stateplane feet, which parses as perfectly
valid GeoJSON near longitude 7,600,000.

## 0.32.0 — 2026-08-05

**Geo Map Views + Crew Tracking.** This app has been storing polygons since
v0.12.0 and coordinates since v0.17.0, and until now the only way to see one was
to read a Long Text field full of numbers. A boundary nobody can look at is a
boundary nobody checks, and the failure that produces is specific: a block traced
with two vertices transposed passes every validation this app makes — it is a
valid polygon, it is on Earth, its area is plausible — and it is obviously wrong
to anybody who sees it drawn.

**A Leaflet map on seven Desk forms.** Parcel, Field and Irrigation Zone draw
their boundary with the shape they are expected to sit inside underneath it, so a
containment disagreement the tools merely *report* is a thing somebody can look
at. Housing Unit and Asset Register draw a marker. Farm Task resolves its
location through whichever register names the place. Farm Shift draws the crew's
track. The library comes from a CDN and the tiles from OpenStreetMap, so a bench
with no outbound internet gets a sentence saying so and the coordinates printed
underneath — the record is the coordinates and the map is a reading of them.
Nothing on any of these forms writes: a map that could nudge a vertex would be a
way round every validation the boundary tools make, with no audit row.

**`doctype_js` was a forbidden hook until this release**, spelled "this app adds
no client script to a doctype it does not own" — and the clause after the comma
was always the real rule. All seven doctypes here are ones this app created, so
an operator who removes the app loses the forms too. `test_hooks.py` now asserts
the rule the sentence stated rather than the ban that stood in for it, the same
move `permission_query_conditions` made in v0.17.1.

**One new DocType: Shift Location Log.** One GPS fix, taken during a shift, at a
time. Standalone rather than a child table of the shift, for two reasons that
both bite in production: a nine-hour shift at a fix every two minutes is two
hundred and seventy rows and a child table is loaded whole every time anybody
opens the shift form; and an append to a standalone doctype is one INSERT, while
an append to a child table is a save of the parent that re-runs every validation
on the crew, the events and the weather timeline.

**Three new tools** — one read and two writes:
- `get_shift_track` (read, default ON)
- `log_shift_location`, `set_parcel_boundary` (write, default OFF)

**A track is read in the order the fixes were TAKEN, not the order they
arrived.** A phone out of signal in a canyon posts an hour of breadcrumbs the
moment the bars come back, so a track sorted by insertion draws the crew standing
still all morning where the signal returned and then teleporting across the farm.
Every silence longer than ten minutes is reported as a gap with its length, and
nothing is interpolated — an invented position on a record read in a wage dispute
or a re-entry-interval question is the worst thing this app could put on a map.

**`log_shift_location` is the one tool on the shift surface a worker's phone
drives rather than the foreman**, and it does not contradict v0.19.3's sole-actor
rule. That rule is about who is *answerable* — who forms the crew, calls the
water break, signs the close — and none of it moves. A breadcrumb attests to
nothing; it records where a device was, which is a measurement rather than a
claim. It appends and never edits, and it does not require an open shift: a phone
that could not reach the site until the evening is posting about a shift the
foreman has already closed, and refusing those would throw away the evidence that
is hardest to collect.

**`accuracy_meters` is kept and never gated on.** A fix under a canopy in a
canyon reports three hundred metres and is still the only record that the crew
was there, so a threshold that dropped it would delete the evidence from
precisely the ground that is hardest to work. Past fifty metres it is noted,
because a fix that coarse cannot settle which side of a block line somebody was
on.

**`set_parcel_boundary` closes a gap `set_field_boundary` had been apologising
for since v0.12.0**, in a warning on every single call: a parcel had no boundary,
so nothing checked that the block sat inside its parcel. Parcel now carries the
same derived suite Field and Irrigation Zone do — centroid, bounding box, H3
coverage at resolutions 6–10 and computed area — and the check runs in both
directions. It is reported and never enforced, which matters more here than
anywhere else this app checks containment: a planting that predates a deed split
really does straddle the line. Only things that *have* a position are tested; a
block with no polygon and a cabin with no coordinates are unmapped, which is a
different answer.

**Housing Unit gains `gps_latitude` and `gps_longitude`**, accepted by
`create_housing_unit` and `update_housing_unit`. The pair moves together or not
at all: passing one is filled in from the stored other, and a genuine half-pair
is refused, because a unit carrying a corrected longitude beside a stale latitude
sits somewhere neither reading of the record meant. A camp address is a driveway
off a county road and a cabin number is paint on a door; neither of them puts an
ambulance at the right building.

4799 standalone tests, zero failures.

## 0.31.0 — 2026-08-05

**Expense Receipt Capture.** A foreman photographs a receipt at the fuel pump or
the parts counter, iOS Vision OCR reads the merchant, the total and the date off
it on-device, and the phone posts the extracted fields, the image and the raw OCR
text here in one call. The extraction runs on the device on purpose: the photo is
the largest thing in the payload and the extraction is the cheapest part of the
job, so doing it there means the foreman sees what the machine read and can
correct it while the paper is still in their hand.

**Two new DocTypes:** Expense Receipt (the register, with the photograph, the raw
OCR text and the scanner's confidence kept beside the extracted fields) and
Expense Receipt Item (the line detail, as a child table).

**Five new tools** — two reads and three writes:
- `list_expense_receipts`, `get_expense_receipt` (read)
- `submit_expense_receipt`, `approve_expense_receipt`,
  `reject_expense_receipt` (write)

**`ocr_confidence` is a sorting key, not a gate.** A crumpled thermal slip
photographed in the sun reads badly and is still a real expense.
`list_expense_receipts` orders lowest confidence first, so the receipts somebody
most needs to open the photo for are at the top of the page rather than at the end
of it. The field is range-checked to 0..1 — a scanner reporting `87` meant `0.87`,
and a field that silently accepts both makes the ordering meaningless.

**Approval and rejection are separate tools with separate switches**, because an
operator who wants a manager able to approve reimbursements is not thereby saying
the same surface may refuse them. A rejection **requires a reason**, stored on the
record rather than in a comment so the phone that submitted the receipt gets it
back. Neither decision can be taken twice: overwriting would erase the name and
date of whoever decided first.

**Line totals are derived only where the scanner left them blank.** OCR reads a
bold receipt total far more reliably than a column of line arithmetic, so a row
with a quantity and a unit price and no total gets the product — and a row that
carries its own total keeps it, because a receipt that charges four at $3 and
totals $11.50 after a discount is telling the truth and the multiplication is not.
The lines are never reconciled against the receipt total; tax, tips, deposits and
core charges all live in between, and a validation that demanded they agree would
reject most real receipts.

**Also in this release:** the tool counts in `docs/tool-catalog.md`,
`test_protocol.py` and `test_read_tools.py` are back in step with the registry —
v0.30.0's nine tools landed without them, and the six tests that guard the
agreement had been red since.

## 0.30.0 — 2026-08-05

**Salary Structures + Payroll Engine.** A pure-function payroll calculator over
the state and federal engines v0.28.0 and v0.29.0 built. A salary structure links
an Employee to a pay type — Piece Rate, Hourly or Salary — and a base rate; the
calculator walks the shifts in a pay period, computes gross with overtime and
break pay, runs federal withholding, state withholding and FICA over it, and
checks the result against the minimum wage for the state the work happened in.

**Three new DocTypes:** Farm Salary Structure (one active structure per employee,
bounded by effective dates), Farm Payroll Entry (one run per pay period per
company) and Farm Payroll Slip (the per-employee child rows under it).

**Nine new tools** — five reads and four writes:
- `get_salary_structure`, `list_salary_structures`, `preview_payroll`,
  `get_payroll_entry`, `list_payroll_entries` (read)
- `create_salary_structure`, `deactivate_salary_structure`,
  `calculate_payroll`, `submit_payroll` (write)

**`preview_payroll` writes nothing.** It runs one employee down the same code
path `calculate_payroll` takes and reports what the slip would say — which is how
a rate change gets checked before a pay run rather than after one.

## 0.29.0 — 2026-08-05

**State Tax Engines — Oregon + Washington.** Pure-function calculation engines
for state-level payroll taxes, keyed by work location per shift (not employer
HQ). Oregon: income tax (ORS 316.037, 4 brackets), Statewide Transit Tax,
Paid Leave Oregon (ORS 657B), workers' comp. Washington: PFML (RCW 50A),
WA Cares Fund (RCW 50B), L&I workers' comp. No WA state income tax.

**Two new DocTypes:** State Tax Configuration (per company × state × tax year,
with state-conditional field visibility) and State Tax Table (Oregon income
tax brackets by filing status).

**Nine new tools** — six reads and three writes:
- `get_state_tax_config`, `list_state_tax_configs`, `get_state_tax_table`,
  `preview_state_withholding`, `preview_total_payroll_taxes`,
  `list_employees_by_work_state` (read)
- `create_state_tax_config`, `update_state_tax_config`,
  `import_state_tax_table` (write)

**Combined payroll preview:** `preview_total_payroll_taxes` returns federal
withholding, FICA, and state taxes in one call. Cross-state pay periods
(employee works shifts in both OR and WA) are handled per-shift.

**Farm Shift gains `work_state`:** a per-shift Select field (OR / WA) that
routes each shift's gross to the correct state engine.

## 0.28.0 — 2026-08-04

**W-4 + Federal Withholding Engine.** A pure-function calculation engine for
IRS Pub 15-T percentage-method withholding (2020+ W-4), Social Security,
Medicare (including additional Medicare over $200k), and FUTA. Pre-seeded with
2025 tax brackets for all filing statuses and payroll periods.

**Three new DocTypes:** W-4 Form (the employee's filing, with superseding
workflow), Federal Tax Table (period-specific marginal brackets), and FICA
Configuration (single doctype for SS/Medicare/FUTA rates and thresholds).

**Ten new tools** — seven reads and three writes:
- `get_w4`, `list_w4_forms`, `get_fica_config`, `get_federal_tax_table`,
  `preview_federal_withholding`, `list_employees_missing_w4`,
  `calculate_payroll_taxes` (read)
- `submit_w4`, `update_fica_config`, `import_federal_tax_table` (write)

**Two new compliance rules:** `employee_missing_w4` (Warning — active employee
has no current-year W-4) and `w4_tax_year_outdated` (Info — active W-4 is for
a prior tax year).

## 0.27.0 — 2026-08-04

**Structured I-9 workflow.** Replaces opaque file attachments with a structured
record carrying Section 1 (employee info), Section 2 (employer verification),
retention dates, and an immutable audit trail. SSN is stripped to the last four
digits before it touches the database.

**Four new DocTypes:** I-9 Form (the workflow record), I-9 Settings (per-site
configuration), I-9 Audit Log (append-only, immutable trail of every I-9
action), and I-9 Document Type (USCIS-seeded lookup of acceptable documents by
List A/B/C category).

**Fourteen new tools** — eight reads and six writes:
- `get_i9_settings`, `get_i9_form`, `list_i9_forms`,
  `list_pending_i9_verifications`, `get_i9_audit_log`,
  `list_i9_document_types`, `get_i9_retention_report`,
  `list_expiring_work_authorizations` (reads)
- `create_i9_form`, `submit_i9_section_1`, `submit_i9_section_2`,
  `update_i9_settings`, `flag_i9_reverification`, `destroy_i9` (writes)

**Section 2 enforces the 3-business-day rule** from the hire date, refusing
verification that arrives late.

**Retention dates are federal:** MAX(hire + 3 years, termination + 1 year).
`destroy_i9` refuses to mark an I-9 as destroyed until the retention date has
passed.

**Integration with `onboard_employee`:** auto-creates a Draft I-9 Form when the
I-9 Form doctype exists on the site.

**Three new compliance rules:** `i9_verification_overdue`,
`work_authorization_expiring`, `i9_retention_destruction_eligible`.

## 0.26.0 — 2026-08-04

**Field-initiated task creation from asset scan.** Worker scans an asset's QR
tag and taps "Flag needs repair" to create a Farm Task linked to the asset, with
skill and location auto-filled from the asset type.

**New tool: `report_asset_issue`.** Convenience wrapper that takes an asset name,
auto-maps skill_required from the asset type (Housing Unit → camp_maintenance,
Irrigation Valve → irrigation, etc.), and creates a linked Farm Task.

**Enhanced: `report_field_task` gains an `asset` parameter.** Links the task to
an asset and auto-fills skill/location when not given explicitly.

**Enhanced: `scan_asset` response** now includes `can_report` and
`suggested_skill` for the iOS "Flag needs repair" button.

**New field on Farm Task: `asset`.** Link to Asset Register. Tasks linked to an
asset appear in `get_asset_detail`'s history timeline.

## 0.25.0 — 2026-08-04

**State-Change Actions: every asset knows what you can do to it.** Workers scan
an asset tag and see not just what it is, but what they can do to it right now.
Each asset type defines its own state machine — a valve can be opened, closed,
or winterized; a sprayer cycles through empty, loaded, in-use, and cleaned; a
housing unit tracks occupancy and winterization. The system validates every
transition: you cannot winterize an open valve, and you cannot load a sprayer
that is already in use.

**New DocType: Asset State Log.** Append-only audit trail of every state change.
Immutable rows — the controller refuses edits after insert. Fields: asset_name,
asset_type, action, from_state, to_state, performed_by, performed_at, notes,
GPS coordinates, photo attachment.

**3 new MCP tools** — 2 read-only (get_available_actions,
list_asset_state_history), 1 mutating (log_asset_state_change). State changes
appear in the cross-doctype asset history timeline alongside tasks, inspections,
and compliance alerts.

**New mobile API endpoints:** log_asset_state_change (POST, mutating),
get_available_actions (POST/GET, read-only).

**Bug fix:** Asset Register naming — records now correctly use the user-specified
tag ID as the docname instead of a random hash.

Tool count: **270** (120 read, 150 mutating).

## 0.24.0 — 2026-08-04

**Universal Asset Tags: scan it, see its history, log what happened.** Every
reportable asset on the farm — a valve, a sprayer, a cabin, a cold storage
unit — gets a durable ID tag (QR and optional NFC). A worker scans the tag and
sees what it is, what has happened to it, and what is due. The tag is the
docname, and the docname is the printable ID.

**New DocType: Asset Register.** Docname IS the tag ID (set-by-user naming, no
rename). Fields: asset_type (10 types), company, location (self-referential
Link for tree structure), description, retired_at, qr_url (auto-built),
nfc_uid, GPS coordinates, current_state (JSON), last_scan_at, last_scan_by.

**10 new MCP tools** — 5 read-only (list_assets, get_asset_detail,
get_asset_history, generate_asset_qr, generate_asset_qr_sheet), 5 mutating
(scan_asset, register_asset, update_registered_asset, retire_asset,
bulk_create_assets). Cross-doctype history timeline pulls from Farm Task,
Housing Inspection, Detector Test, Water Test, Inspection Session, and
Compliance Alert. Retirement is soft — sets retired_at, preserves history.

**New mobile API endpoints:** scan_asset (POST, mutating), get_asset_detail
(POST/GET, read-only).

Tool count: **267** (118 read, 149 mutating).

## 0.23.0 — 2026-08-04

**Field-Initiated Tasks: every worker becomes a compliance sensor.** Workers in
the field can report problems on the spot — tap "Report a problem," snap a photo,
add a description, and create a Farm Task immediately. The field report IS the
work order: no separate "Issue" or "Ticket" doctype. Photo-taking IS
ticket-creation IS dispatch entry, all one act.

**New fields on Farm Task:** `origin` (how the task came into being:
`compliance_rule`, `foreman_dispatch`, `field_reported`,
`worker_self_pick_from_pool`), `reported_by` (the Employee who flagged it),
`reported_at` (when they flagged it), `report_photo` (the "before" photo).

**New MCP tool:** `report_field_task` — mutating, rate-limited (5 per worker per
hour), photo required. Workers may choose Normal or High urgency; Critical is
restricted to Foreman and Farm Manager roles.

**New mobile API endpoint:** `report_field_task` — whitelisted, same anti-spam
rules, reporter resolved from the authenticated session.

**New compliance rule:** `field_flag_awaiting_dispatch` — if a field-reported
task sits in Available state for more than 24 hours without being claimed, raise a
Warning alert to the foreman.

**Anti-spam:** a foreman dismissing a report as "not a real issue" (Cancelled
state) counts against the reporter's rate limit for the next 24 hours.

The split is now **13 declarative / 2 built-in-permanent / 0 `custom_python`**.
Tool count: **257** (113 read, 144 mutating).

## 0.22.5 — 2026-08-04

**A rule that fires on the weather.** Every rule this app had ever shipped fired
on a distance from a date. `shift_heat_threshold_crossed` fires because the
latest row of one shift's weather timeline says 82 °F, and goes quiet because the
next row says 75 or because somebody closed the shift. It is also **the first
rule this app ships that was authored as a record** — there is no Python behind
it and there never was, which is the first evidence the vocabulary can absorb a
new obligation rather than only the thirteen it was reverse-engineered from. The
split is now **12 declarative / 2 built-in-permanent / 0 `custom_python`**. Tool
surface **unchanged at 256**; suite **4,277 → 4,330 passing**. **Behaviour drift:
zero** — the thirteen pre-existing rules produce identical rows. Full notes:
[`RELEASES/v0.22.5.md`](RELEASES/v0.22.5.md).

### Added

- **`latest_child_field_threshold_json`** — a sibling of `gate_related_table_json`
  rather than an extension of it. That one folds a related doctype to one *value*
  per subject (the maximum date); this folds to one *row*, the latest, and reads
  a number off its other columns — which a maximum cannot answer, because the
  85 °F reading at noon says nothing about four o'clock if a 72 °F reading was
  written at half past three. The whole row goes into the message template under
  `context_key`. Indexed once per sweep, capped at `SCAN_CAP`, folded in Python.
- **`threshold_source`** — a **closed registry** letting a condition read its
  number from a per-company setting instead of a literal on the rule. The three
  entries are the Weather Settings heat and wind thresholds, which the v0.19.4
  shift sweep already reads: a literal would make the alert layer and the
  operational layer disagree about the same afternoon on the same shift,
  invisibly. The literal stays on the condition as the floor the setting falls
  back to.
- **`date_field_role: "State"` + `default_severity`** — a rule with no clock.
  `default_severity` alone is *not* enough: `threshold_*_days` are Int columns, so
  "no threshold" and "a threshold of zero" are one value, and zero is a real
  setting meaning "fire on the due date itself". A shift that started this morning
  is zero days from its own start, so a rule read as a clock says Critical about a
  crew who are merely at work. The rule has to say which it is.
- **`producer_assigned_to_expression`** — a safe expression over the alert's
  source row (`row.foreman`) producing an Employee. The producer task is assigned
  to that person, `dispatch_mode` = Dispatched, state Claimed, with an open
  assignment and **no skill**. Exclusive with `producer_skill_required` at both
  doors: a skill is a pool and an assignee is a person.
- **`sandbox.evaluate`** — one expression, one value, same grammar and refusals
  and budget as `sandbox.run`. Written as an assignment rather than parsed in
  `eval` mode so the tree that is vetted is exactly the tree that runs.
- **`shift_heat_threshold_crossed`** — seeded, enabled, OR-OSHA, three-year
  retention, in the OSHA packet. Fires on an open Farm Shift whose latest weather
  reading is at or above the heat threshold; the producer task goes to
  `shift.foreman` and asks for findings text and their signature. The app
  surfaces the trigger; the foreman makes the compliance decision.

### Changed

- **The producer path now reads the Compliance Rule record** where
  `ALERT_TASK_MAP` has nothing to say. Since v0.22.0 every rule had carried
  `producer_farm_task_type`, `producer_skill_required` and `evidence_contract` —
  seeded *from* that table — and nothing read them back, so a rule authored after
  the framework shipped landed in `skipped_unmapped` anyway. The table is still
  consulted **first**, which is what keeps the thirteen shipped rules producing
  exactly the tasks they always did. Both a task type and an evidence contract are
  still required before an alert becomes work.
- `create_compliance_rule`, `update_compliance_rule`, `test_compliance_rule`,
  `get_compliance_rule` and `list_compliance_rules` accept and report the three
  new fields. **No new tools** — the surface stays at 256 / 113 / 143.

### Fixed

- Nothing was broken. Three notes on directions chosen where both were available:
  a subject with no child row is **gated out** (a shift with an empty timeline is
  not a cool shift); a child row whose field is empty does not satisfy its
  condition; and the threshold comparison is **numeric only**, unlike
  `scope_filters`, because a reading somebody typed as `"warm"` sorts after
  `"80"`.

### Auto-dismissal, unchanged

The alert goes quiet through **no new mechanism**. The temperature drops and the
gate stops matching; the shift closes and the scope filters stop matching. In
both cases the rule observes nothing and the sweep auto-dismisses what it did not
observe — exactly what happens when a certificate is renewed. The task the
foreman was given stays: a shift that closed is not evidence that anybody wrote
down the water and the shade.

---

## 0.22.1 — 2026-08-04

**The vocabulary reaches its own problem domain.** v0.22.0 shipped six
declarative rules and seven built-in scanners, and named the four primitives that
would move most of the seven. This release built them: **five rules migrated**,
and the split is now **11 declarative / 2 built-in-permanent / 0
`custom_python`**. The two that stay are argued as *permanent* rather than
pending — an aggregation and a walk over a table of doctypes whose thresholds
mean days elapsed rather than days remaining. Tool surface **unchanged at 256**;
suite **4,187 → 4,277 passing**. **Behaviour drift: zero**, asserted per rule
against the shipped scanners themselves. Full notes:
[`RELEASES/v0.22.1.md`](RELEASES/v0.22.1.md).

### Added

- **`superseded_by_later_clean_json`** — the one gate that is a question about
  *other rows*. A finding stops being true when a later clean record for the same
  subject supersedes it. Took `housing_corrective_action_open` and
  `water_test_contamination` declarative at once. `unreadable_counts_as_dirty`
  defaults to true: a record whose state nobody can read does not supersede.
  Indexed once per sweep, not queried per candidate.
- **`regime_heuristics_json`** — an ordered lookup that reads the regimes off a
  *name* rather than a column, for the case `regimes_from_field` cannot reach.
  First match wins and the order is the content (`globalgap` before `gap`); where
  entries name several fields the **field order is the outer loop**, so a
  certificate's type is never overridden by a word in its name. Took
  `certification_expiring` declarative. Derived from `CERT_REGIME_HEURISTICS`
  rather than restated beside it.
- **`gate_date_field` + `gate_within_days` + `gate_scope` +
  `gate_related_table_json`** — a second date used only as a gate, for a rule
  whose condition is a conjunction over two independent dates. Took
  `water_test_stale` declarative. A row with no gate date is gated *out*, which is
  deliberately the opposite of `missing_date_behaviour`.
- **`date_fields_json`** — several anchors of the same kind, where either being
  stale fires and the message must name which. The severity folds to the worst;
  the template gets `stale_dates` and `first_stale_label`. Took
  `housing_detector_test_stale` declarative.
- **`date_field_role`** (`Clock` / `Timestamp`) — a finding's date is when the
  thing was found, not a deadline. Without it, both supersession rules would have
  stopped firing on the day a finding was written.
- **`target_doctypes_json`** — the one rule that walks two record types under one
  `rule_id`, with a per-entry label so the message says "the detector test"
  rather than "Detector Test".
- **`category_heuristics_json`** — the same ordered shape producing the alert's
  category, because an applicator licence is a Workforce item and a GlobalGAP
  certificate is not.
- **`istrue` / `isfalse` scope-filter operators** — the only correct way to
  filter on a Check box, which read back before the database layer holds the
  *string* `"0"`.
- **`patches/migrate_declarative_rules.py`** — the upgrade a v0.22.0 site gets.
  The seeder cannot do this (it leaves alone what is already there, which is what
  protects an operator's edits), so the five are migrated deliberately:
  thresholds, filters, citations, regimes and the switch carried across, scope
  filters *concatenated* rather than replaced, `spray_season_days` read into
  `gate_within_days`, and the old row superseded rather than edited.

### Changed

- **`_band` checks the outer window before the critical band.** Indistinguishable
  from the shipped scanner until a per-row `window_field` is *narrower* than the
  rule's critical threshold — a certificate whose issuing body turns renewals
  round in ten days. The window is the claim about when work can usefully start.
  No shipped rule's behaviour changes.
- `get_compliance_rule` reports every new primitive, and an **empty** rather than
  a null for the ones a rule does not use.
- `docs/configurable_compliance_framework.md` §5 is now the migration's record
  rather than a backlog, and §4 answers "when should I reach for `custom_python`"
  with a table of eleven questions that are already fields.

### Unchanged

- Tool count, at **256 / 113 read / 143 mutating**.
- The alert docname format, the `list_compliance_rules` return shape, the six
  rules that were already declarative, and the two permanent built-ins.
- Every existing test — none was modified.

## 0.22.0 — 2026-08-04

**The rules themselves are data.** A compliance rule used to be a Python
function, so moving a threshold, correcting a citation or switching a rule off
for a season was a code change, a release and a deploy — and regulations do not
move on a release cadence. OR-OSHA renumbered heat illness from -1130 to -1131;
OTCO added a Fraud Prevention Plan requirement; the FDA re-phased FSMA Produce
Safety. Each of those is now an **edit to a record**. A `Compliance Rule` carries
its thresholds, scope, citations, regimes, message and switch; the sweep reads
them; **no model runs in the trigger path**, which is what keeps every alert
traceable to a row, a citation, an approver and the field that crossed a
threshold. Tool surface **249 → 256** (113 read, 143 mutating); suite **4,121 →
4,187 passing**. **Behaviour drift: zero** — the thirteen shipped rules produce
byte-identical alerts, asserted row by row. Full notes:
[`RELEASES/v0.22.0.md`](RELEASES/v0.22.0.md).

### Added

- **`Compliance Rule` doctype** (`CRULE-2026-0001`). Target doctype, cadence
  anchor, thresholds and per-band severities, scope filters, Jinja message,
  regimes, regulation citations, retention window, producer task, kairotic gate,
  and the provenance trio `authored_by` / `human_approved_by` /
  `human_approved_on`. No new child tables — `Compliance Regime Link` is reused,
  and the filter, contract and packet lists are JSON blobs validated at authoring
  time.
- **A declarative rule engine** (`alerts/engine.py`). Query the target, apply the
  scope filters, measure the anchor against the cadence and the thresholds, pick
  the band, render the message. Deterministic, bounded, identical every time for
  identical data.
- **Seven tools.** `create_compliance_rule`, `approve_compliance_rule`,
  `update_compliance_rule`, `deactivate_compliance_rule` and the
  declared-but-inert `propose_compliance_rule` (five mutating, all shipping OFF);
  `get_compliance_rule` and `test_compliance_rule` (two read).
  `list_compliance_rules` was **retrofitted, not replaced** — it reads the
  records and takes `regime` / `category` / `target_doctype` / `shape` /
  `active`, and every key it returned before means what it always meant.
- **`test_compliance_rule`**, the read between authoring and approving. It runs a
  rule down **the same code path the sweep takes** — a dry run with its own
  second implementation is one that can disagree with the real one — and reports
  what it *would* raise, with the docname each alert would take, writing nothing.
- **A restricted-Python sandbox** (`alerts/sandbox.py`) for `custom_python`: an
  AST interpreter, never `exec`, never `eval`. Refuses `import`, `exec`, `eval`,
  `open`, **every underscore-prefixed attribute**, `while`, `def`/`class`/
  `lambda` and `try`, each with a sentence saying why. Bounded at 200,000 node
  visits and 5 seconds. `frappe.get_doc` hands back a plain dict, because a
  Document has `.save()` on it.
- **`docs/configurable_compliance_framework.md`** — how to author a rule, when
  not to use `custom_python`, the provenance model, and the ranked list of
  primitives that would shrink the built-in surface.

### Changed

- **The thirteen shipped rules are now records**, seeded on install and after
  every migrate. **Six migrated pure-declarative** (`policy_review_overdue`,
  `housing_inspection_overdue`, `i9_expired`, `flc_license_expiring`,
  `filing_response_due`, `training_expiring`); **seven keep a shipped scanner**
  for a join no declarative field expresses yet — supersession by a later clean
  record, a child table folded to its worst row, two dates that only matter
  together. **Zero use `custom_python`**, which is the honest measure of an
  escape hatch. Every built-in still carries its thresholds, scope, citations and
  switch on the record; only the shape of the join is code.
- **`alerts/base.py` reads the rule set from records** and falls back to the
  shipped definitions on a site that has not migrated yet — **saying so** in
  `engine_notes`. A compliance calendar that quietly emptied itself for the
  length of an upgrade would be the worst failure this app could have. The sweep,
  the reconciliation, the idempotent docnames and the auto-dismissal are
  unchanged.
- **Rules are versioned by copy**, exactly as Inspection Templates are.
  `update_compliance_rule` writes v+1 and points the old row's `superseded_by` at
  it; the old row is disabled, never edited, never deleted. A sweep that started
  against v1 finishes against v1.
- **`Compliance Rule` is granted to System Manager, Compliance Officer and Farm
  Manager only.** A Foreman reads the calendar and cannot rewrite what fills it;
  a Field Worker cannot see it at all — the dispatch separation this app already
  keeps, moved one layer up.

### Guarantees

- **`enabled` is refused without an approver AND a date.** `create_compliance_rule`
  always writes a Draft whatever the caller asked for, so "a model wrote a rule
  and it started firing" is a sentence that cannot be true about this app.
- **One live row per `rule_id`**, enforced in the controller and materialised as
  the indexed `active_row_flag`.
- **Deactivating dismisses nothing.** The alerts a rule raised stay exactly as
  they were — switching a rule off is not evidence that anybody did the work.
  There is no delete.
- **Scope filters are evaluated in Python with a documented `default`**, because
  in SQL `status != 'Active'` excludes every row whose status was never set — and
  a policy with no status is in force, a filing with no status is neither Draft
  nor Withdrawn, a cabin with no condition is not Uninhabitable.
- **Message templates render in a Jinja sandbox with no framework in it** —
  deliberately not `frappe.render_template`, whose environment would be a second
  undocumented escape hatch beside the one this release sandboxed.
- **The seeder is not a Frappe `fixtures` entry.** It checks by `rule_id` across
  every row before writing, so an operator's edit, a disabled rule and a
  superseded version all survive every future migrate.

## 0.21.0 — 2026-08-03

**The shape of a visit is data.** A worker walks into a cabin once and does
everything it needs; the register still gets a Housing Inspection, a Detector
Test and a Water Test, separately, at their own cadences, because those are
different regulators asking on different schedules. What defines that visit is
now a **row an operator writes** rather than a release somebody ships: an
Inspection Template says which sections a trip consists of, what evidence each
needs and which compliance record each produces, and `create_inspection_template`
makes one live on the next fetch — no code, no DocType edit, no TestFlight build.
Tool surface **239 → 249** (111 read, 138 mutating); suite **4,055 → 4,121
passing**. Backend only; the iOS sectioned-form renderer is v0.21.1. Full notes:
[`RELEASES/v0.21.0.md`](RELEASES/v0.21.0.md).

### Added

- **Five doctypes.** `Inspection Template` (`INSPT-2026-0001`) and its
  `Inspection Template Section` child define the shape; `Inspection Session`
  (`INSPS-2026-0001`) records one worker's execution of it at one place, with
  `Inspection Session Evidence` as the visit's **shared tray** — one photograph,
  filed by reference against every record it answers — and `Inspection Session
  Section Submission` carrying what was ticked, what was measured and **which
  compliance record each section produced**.
- **`Farm Task.inspection_session`**, nullable. A task carrying it is a
  multi-section visit. **The task is still the dispatch atom** — one card, one
  claim, one entry against the concurrent-claim limit — and the session is the
  form behind that card, never a second kind of card beside it.
- **Ten tools**: `create_inspection_template`, `update_inspection_template`,
  `deactivate_inspection_template`, `start_inspection_session`,
  `submit_inspection_session` and the declared-but-inert
  `propose_inspection_template_from_regulation` (six mutating, all shipping OFF);
  `list_inspection_templates`, `get_inspection_template`,
  `list_inspection_sessions` and `get_inspection_session` (four read).
- **Templates are VERSIONED BY COPY.** `update_inspection_template` writes a NEW
  row at version+1 and never edits the old one, which it deactivates and points
  at the new one. That is what makes a session from April readable in November,
  and it is why a session started against v1 **while v2 is being authored** is
  unaffected: v2 is a different document and v1 is never touched. A session pins
  the row, and the row is the version.
- **Four seeded templates**, on install and every migrate: Pre-season Cabin
  Opening, Mid-season Habitability, Post-harvest Cabin Close-down, Spray Day
  Inspection. Idempotent and checked by name across every row, so an operator who
  edited, deactivated or superseded one keeps their decision. **Not a Frappe
  `fixtures` entry**, and `test_hooks.py` still forbids the word.
- **A `sessions` section on every audit packet** that already carries housing or
  water. It adds no record — the records are already there in their own sections
  — it adds the sentence joining them: *these were captured in a single Cabin
  Opening session on 2026-04-15 by Ana Ramos, foreman Miguel Torres, worked from
  version 2 of the template, evidence timestamped and signed.* Counted by
  **record** rather than by submission, and by **distinct file** rather than by
  evidence row.

### Changed

- **`generate_tasks_from_compliance_alerts` bundles.** Where a place has two or
  more pending alerts of different types and an active template's sections
  produce a **superset** of the records those alerts ask for, it raises ONE Farm
  Task carrying an Inspection Session instead of N tasks. Matching is set
  inclusion, tie-broken by `(extra sections, total sections, docname)` — **no
  model, no interpretation, nothing probabilistic in the trigger path**. No match
  is a first-class answer and leaves every alert on the unchanged per-alert path;
  a site with no templates behaves exactly as v0.20.1 did. Idempotent by a
  different mechanism from the per-alert path: a session records every alert it
  answers, read back as whole docnames split on newlines, never as a substring.
- **Two sections producing the same record for the same subject produce ONE
  record.** A Detector Test carries a smoke result AND a CO result, both required
  — so filing two Detector Tests for one cabin on one day would mean each
  asserting something it was never told about the other detector, and two
  compliance records that disagree is the failure this app exists to prevent.
  Both section submissions link the one record, so the trail from either side of
  the walk is intact, and both of the unit's detector dates move.

### Not in this release

- **No iOS changes.** Every template shipped here is renderable from
  `get_inspection_template` alone; `renderer_hint` is a hint, and a client that
  does not know one falls back to a freeform form with the submission still
  valid. The sectioned-form renderer is v0.21.1.
- **No `Spray Record` doctype**, so the Spray Day template's product-and-rate
  section produces no standalone record and captures the product, EPA number,
  rate, REI and PHI as findings and measurements. A section pointing at a doctype
  a site does not have is refused **at authoring time**, rather than at
  submission time while somebody is standing beside a sprayer. The day the
  doctype ships, one `update_inspection_template` call points the section at it
  and every session worked before then stays readable against its own version.

## 0.20.1 — 2026-08-03

**The acknowledgement that never arrived.** A worker's iPad drained its offline
queue into a connection that dropped between the server's acknowledgement and
the app's receipt of it. The server had accepted every completion; the app
re-sent, as any client must; the server answered `already Completed` as a hard
error. Three Failed entries per task, on work that was filed and evidenced the
first time. **A client cannot know whether its request landed**, and the only
place that question can be answered is here. Tool surface **238 → 239** (107
read, 132 mutating); suite **3,984 → 4,055 passing**. Full notes:
[`RELEASES/v0.20.1.md`](RELEASES/v0.20.1.md).

### Fixed

- **`complete_task_via_mobile` and `complete_farm_task` are idempotent.** An
  identical resubmission — same assignment, same worker, same evidence, same
  words, same `completed_at` as sent — returns the completion already on record
  with `x_idempotent: true` and **writes nothing**: no second compliance record,
  no duplicated evidence rows, no state transition. A resubmission that differs
  in any of those is still refused, because two people cannot file the same
  completion and a second account of the same work is not the first one again.
- **A retry naming only the task no longer fails differently.** A completion
  ends the *live* assignment, so a second call carrying just a task name used to
  be refused with "has nobody holding it". `_assignment_for` now falls back to
  the newest Completed assignment — for the completion path only; starting or
  rejecting a finished task still says so.

### Added

- **`completion_signature` on Farm Task Assignment** — sha256 over the
  assignment, the worker, the sorted evidence file references, the findings and
  narrative, and **the clock-out time as the client sent it**. Hashing the
  server's `now()` fallback instead would make every retry a conflict. See
  [`erpnext_mcp/completions.py`](erpnext_mcp/completions.py) for what is
  excluded and why.
- **A migration backfill** (`patches/backfill_completion_signatures.py`) so the
  pre-v0.20.1 rows — the ones most likely to be sitting in a stuck queue — are
  recognised too. It uses a distinct `v1b` scheme that leaves the clock-out time
  out of the hash, because nothing on a legacy row says whether the client or
  the server chose it and guessing would create false conflicts on exactly the
  oldest rows. Idempotent; never rewrites a signature a completion wrote.
- **`visit_id` on Farm Task Assignment**, accepted by
  `complete_task_via_mobile` and returned in the payload. The identifier the
  handset mints when a worker arrives somewhere and reuses for every task closed
  before they leave. Unvalidated in v0.20.1 beyond being a string.
- **`list_visits`** (read, on by default). Completed assignments grouped into the
  trips their handsets recorded, with the span, the places, the distinct
  evidence-file count and the task list. The grouping is the phone's, not a guess
  from timestamps — no threshold gets both an unhurried walk and two fast jobs at
  opposite ends of a property right. A completion with no `visit_id` is in **no**
  visit and is counted separately; one-task visits **are** returned, because that
  is what a question about wasted travel is looking for.

## 0.19.7 — 2026-08-03

**A green board.** A maintenance pass with no behaviour changes: the
SPDX-header check and the `ruff` job had both been failing since before
v0.19.5, and a permanently red board teaches contributors that red is normal.
Tool surface unchanged at **238** (106 read, 132 mutating); suite unchanged at
**3,984 passing**. Full notes:
[`RELEASES/v0.19.7.md`](RELEASES/v0.19.7.md).

### Fixed

- **SPDX headers on 21 files.** Every empty `doctype/*/__init__.py` package
  marker added between v0.19.2 and v0.19.6 now carries
  `# SPDX-License-Identifier: MIT`. Files missing the header: 21 → **0**.
- **64 ruff findings → 0**, at ruff 0.16.1. Eighteen auto-fixed; the rest
  reviewed a rule at a time. Implicit `Optional` annotations made explicit
  (`RUF013`, 14 sites), test-class constants annotated `ClassVar` (`RUF012`,
  5), single-element slices become `next(...)` (`RUF015`, 4), concatenation
  becomes unpacking (`RUF005`, 5), a redundant `int(round(...))` cast dropped
  (`RUF046`), a re-raise given `from None` (`B904`), plus unsorted imports,
  three stale `noqa` directives, an unused import and two `TimeoutError`
  aliases. **No fix changed a value, a branch or a payload.**
- **138 unformatted files → 0.** `ruff format` applied repo-wide, in its own
  commit so no future behavioural diff has a formatting sweep hidden inside it.

### Changed

- **`RUF001`/`RUF002`/`RUF003` ignored in `pyproject.toml`,** with the reasoning
  recorded beside them. All sixteen flagged characters are deliberate typography
  in operator-facing prose — an EN DASH in a range (`1–30`, `§112.21–.30`), a
  MULTIPLICATION SIGN in a dimension (`8.5×11`), a MINUS SIGN in arithmetic.
  Rewriting them to ASCII would make the text worse.

## 0.19.6 — 2026-08-03

**The window standard.** Every financial report in this app now defaults to a
**trailing twelve months**, with a configurable computation step and a
historical-averages block beside it. Not a feature on one metric — the shape
every financial figure takes from here on. Agricultural revenue is aggressively
seasonal, so Q3 is harvest and Q1 is pruning, and two single periods set against
each other say the operation collapsed in January and recovered in September:
every year, on every farm, whether or not anything happened. Full notes:
[`RELEASES/v0.19.6.md`](RELEASES/v0.19.6.md). The standard itself:
[`docs/reporting_ttm_standard.md`](docs/reporting_ttm_standard.md).

Suite: 3,895 → **3,980 passing**. Tool surface: 235 → **238** (106 read, 132
mutating).

### Added

- **`services/windowed_reports.py`.** One utility that turns any point-in-time
  computation into a windowed one. The boundary rule is one rule and not five:
  `period_end` is the last **completed** computation-step boundary on or before
  `as_of`, and `period_start` is `add_months(period_end, -window_months) + 1
  day`. Read on 2026-08-03, a Monthly window is 2025-08-01 to 2026-07-31 and a
  Quarterly one is 2025-07-01 to 2026-06-30. **The part-finished period is
  excluded and that is the point**: three days of August against twelve months of
  everything else is a figure that falls every first of the month and recovers by
  the thirty-first, and an operator reading it on the fourth will believe the
  fall.
- **Three blocks in every payload, and each corrects the other two.**
  `point_in_time` is the period just finished; `window` (also `ttm` when the type
  is TTM) is the same figure over twelve rolling months; `historical_averages` is
  what that window has been worth for this operation before, with mean, median,
  min, max, standard deviation and the deltas against the mean and against the
  same window a year ago. A TTM figure means one thing above its five-year mean
  and the opposite below it, and the first two blocks cannot say which.
- **`Financial KPI History` DocType.** The cache: one row per `(kpi_key, company,
  computation_step, window_type, window_months, as_of)`, carrying the
  **components dict** as well as the figure. That is not an optimisation — a
  cached number with no ingredients is one an auditor cannot test, and the
  historical figures are exactly the ones nobody can recompute from memory. It is
  `in_create` with no create permission: writes come from the service and the
  sweep, because a row somebody typed would be a figure with no computation
  behind it in a table whose whole claim is that every row has one.
- **`services/financial_reports.py`** — three registered computers, deliberately
  not three of a kind. `sustainable_cf_per_acre` (a ratio), `ocf` (raw and
  normalized operating cash flow, so a covenant test can have the figure without
  an acreage denominator attached) and `revenue` (a sum over GL Income rows,
  credits less debits, submitted vouchers only).
- **`get_windowed_report`** — READ, on by default. The generic entry point, and
  the reason the standard generalizes: a report registered in
  `financial_reports.py` is reachable through it without another tool, another
  switch and another catalogue section. A framework whose every KPI costs a tool
  is a framework with six KPIs in it.
- **`list_financial_kpi_history`** — READ, on by default. The cache as a plain
  series, for drawing or exporting a line. It reports what is **not** there: a
  gap is a window nobody has computed yet, or one invalidated by a retroactively
  approved adjustment and not yet rebuilt, and plotting it as a continuous line
  draws a trend that did not happen.
- **`recompute_kpi_history`** — MUTATING, off by default, and the mildest
  mutating tool in the catalogue. The only thing it can change is a cache: every
  row it writes is what the live computation would have produced and every row it
  deletes comes back, so the worst outcome of running it at the wrong moment is
  time spent. It is the answer to a retroactive approval when the pack goes out
  this afternoon rather than tomorrow.
- **An overnight sweep at `0 2 * * *`** — the sixth scheduled job, and **one job
  that iterates** every registered report and every company rather than a cron
  per KPI. `daily` would be tidier and is wrong: Frappe's `daily` fires on the
  day's first tick, which on a farm bench is during the morning, and this is the
  one job that can take minutes on a large ledger. It is the only scheduled job
  in this app with a kill switch of its own — `enable_kpi_history_sweep` — because
  it is the only one whose cost scales with the size of somebody's books.
- **`Sustainable CF Per Acre TTM Monthly` report and chart**, the new default
  view: twenty-four rolling points, each a full twelve months, with a **dashed
  reference rule** at the prior-window mean. The mean is a frappe-charts
  `yMarker` rather than a second dataset — a second solid line invites the reader
  to compare its *shape* with the first, which is meaningless because it has
  none.
- **`docs/reporting_ttm_standard.md`** — the standard, the annotated output
  shape, the boundary rule, how to add a windowed report, budget-vs-actual usage,
  the cache strategy, and the tie-in to the Financial KPI Framework.

### Changed

- **`get_sustainable_cf_per_acre` defaults to TTM.** Call it with only a company
  and you get the trailing twelve months ending at the last completed month, the
  month just finished beside it, and five years of prior windows under both — with
  every ingredient still itemized inside the window. **Passing `period_start` and
  `period_end` returns the v0.19.5 payload, exactly**, with a deprecation
  sentence at the head of `computation_warnings`. That path is kept because this
  figure is quoted in packs that were sent before the window existed, and a
  release that changed what an unchanged call returned would silently alter a
  number somebody had already given a bank. The v0.19.5 end-to-end test passes
  unmodified. One of the two arguments without the other is refused rather than
  guessed at.
- **`approve_normalization_adjustment` invalidates the cache.** A retroactive
  approval genuinely changes what every window containing it was worth, so every
  cached snapshot whose window overlaps the adjustment's period is **deleted**
  and the next read or the next sweep rebuilds it. Deleted rather than flagged,
  because a cached row carries the components list as well as the figure, and a
  stale components list is worse than a missing one — it is a set of ingredients
  that does not produce the number printed above it. The result says how many
  went.
- **Quarterly and Yearly steps follow the company's own fiscal year**, with
  `fiscal_year_start_month` reported in the payload and a warning on a
  non-calendar year. A July-year operation stepping a rolling window by calendar
  quarters would put every year-end close in the middle of a bucket. The
  *discrete quarterly* report keeps calendar quarters, deliberately: it is read
  beside a lender's own pack.
- **The v0.19.5 quarterly chart is demoted, not renamed.** It stays as the
  secondary discrete view with its `why` text rewritten. Renaming the record
  would silently empty the dashboards of every site that installed v0.19.5,
  because a Dashboard Chart's docname is what a Dashboard and anybody's saved
  link point at.

### Notes

- **Partial history is a warning, never a quietly smaller number.** A site with
  four months of ledger gets four months of ledger, labelled, and it is **not**
  annualized — annualizing would invent eight months of a season that has not
  happened. Every statistic from a short series reports `prior_ttm_count`, and
  anything that cannot be computed is **null rather than zero**: a standard
  deviation of zero means a perfectly steady business, and one of null means a
  single snapshot.
- **The window is computed whole, not assembled from twelve months.** Two
  reasons, and the second is the one that decided it. Sustainable CF/Acre is a
  *ratio*, and the average of twelve monthly ratios is not the ratio of the
  twelve-month totals. And `kpi.approved_in_period` counts an adjustment whose
  period falls *inside* the window — so a quarter-long insurance recovery falls
  inside no monthly bucket, and a year assembled from twelve months would drop it
  with nothing anywhere saying why. Computers declare `bucket_additive`; revenue
  is, and the cash-flow figures are not.
- **A live query computes at most 24 missing snapshots** and then stops, with a
  warning naming the tool that fills the rest. A read that runs for four minutes
  is a read somebody kills and then distrusts.
- **`get_windowed_report` is annotated read-only and does write the cache.**
  Nothing in a ledger is touched — no Account, no GL Entry, no Journal Entry, no
  Asset, no Field, no adjustment — and a test asserts that a windowed read
  changes no table except `Financial KPI History` and the audit log. Deleting
  every cached row changes no answer, only how long the next report takes.

## 0.19.5 — 2026-08-03

**What the year actually earned per acre.** The first release in the v0.19.x run
that no regulator asked for. Sustainable CF/Acre is (normalized operating cash
flow − maintenance capex) ÷ productive acres, and it exists because headline OCF
lies in two directions at once: it is flattered by money that will not come in
again, and flattered **again** by maintenance that was not done. Full notes:
[`RELEASES/v0.19.5.md`](RELEASES/v0.19.5.md).

Suite: 3,835 → **3,895 passing**. Tool surface: 229 → **235** (104 read, 131
mutating).

### Added

- **`Normalization Adjustment` DocType.** One add-back to or subtraction from
  operating cash flow, for one company and one period, with the sentence saying
  why it will not recur and the signature of whoever accepted that sentence. The
  justification carries a **forty-character floor** — not a quality bar, but a
  floor under "one-time" and "per Tim", which are what gets typed when a field is
  merely required and both of which an auditor reads as an admission that nobody
  thought about it. **Only `Approved` counts**; drafts, rejections and superseded
  rows are all in the register and none of them moves the number. Deliberately
  **not submittable**: this workflow has two terminal states that are not the same
  thing, plus a third a correction produces years later, and `docstatus` cannot
  hold that. One approved adjustment per company, period and category — a
  correction **supersedes** rather than duplicates.
- **Four Custom Fields on ERPNext's `Asset`** — `capex_type`,
  `maintenance_portion`, `growth_portion`, `capex_justification` — the fifth
  doctype this app grafts a column onto, and the first target in
  `compliance_fields.py` that is not about a regulator. Maintenance capex replaces
  what wore out; growth capex buys capacity that was never there, and an operation
  that cannot tell them apart funds the second out of the first. **`capex_type` is
  not `reqd`** — Frappe enforces `reqd` on save rather than retroactively, so it
  would leave every existing Asset readable and unsaveable. The gate is in
  `create_asset` instead, and it does not engage until the column exists.
- **Three declared fields on `Field`** — `productive_from_date`,
  `productive_through_date`, `pre_yield_end_date`. Declared rather than Custom,
  because `Field` is this app's own doctype. Nothing already carried these dates:
  there is no `PlantingSeason` junction in this app, and `planting_year` is not the
  same fact — a block planted in 2019 may have come into bearing in 2022 or 2023
  depending on variety and rootstock.
- **`erpnext_mcp/services/sustainable_cf_per_acre.py`**, computing raw operating
  cash flow from `GL Entry` by the **direct method** rather than reading ERPNext's
  Cash Flow report. Cash and bank movement per submitted voucher, apportioned to
  operating / investing / financing by the accounts on the other side, with a
  mixed voucher split proportionally. A report's output cannot be traced back to
  rows, and the whole argument of this release is that the figure has to be
  inspectable.
- **Six tools** — `create_normalization_adjustment` (creates a **Draft**, always),
  `approve_normalization_adjustment` (signature required, timestamp written rather
  than taken), `reject_normalization_adjustment` (the rejection is kept),
  `backfill_asset_capex_type` (dry-run by default, never overwrites, idempotent),
  `list_normalization_adjustments` and `get_sustainable_cf_per_acre`. The role gate
  is **Accounts Manager / Farm Manager / System Manager** — deliberately not the HR
  list, because an HR User who can file a training record has no business moving
  the number a lender reads.
- **`Sustainable CF Per Acre by Quarter`**, a standard Script Report, and the
  `Sustainable CF/Acre by Quarter` Dashboard Chart over it, installed idempotently
  on every migrate. The components travel with the figure in columns rather than in
  a tooltip: the interesting question about a quarter where the number fell is
  always *which of the three moved*.
- **`docs/kpi_sustainable_cf_per_acre.md`** — the formula, where each input comes
  from, the approval workflow, and the audit-defensibility argument for itemized
  output.

### Changed

- **`create_asset` now requires `capex_type`** once the column exists, with no
  default. An unclassified purchase quietly read as maintenance would let growth
  spending disappear into the line the replacement budget is built on. Mixed must
  split to the invoice within a cent; Growth and Mixed additionally require a
  `capex_justification`, because classifying spend as growth *raises* sustainable
  cash flow and that is the one direction a misclassification flatters the
  operation.
- **`create_field` and `update_field`** take and report the three productive
  dates.
- **`before_uninstall`** names `Normalization Adjustment` among the records that
  go with the app. Losing it does not lose a number — it loses the *defence* of
  every Sustainable CF/Acre figure ever quoted from the site.

### Notes

- **The KPI output is itemized and that is not presentation.** Every buyer, lender
  and auditor who reads a normalized figure tests it one add-back at a time, so
  `get_sustainable_cf_per_acre` returns each adjustment with its justification and
  signature, each maintenance-capex asset with its portion, and each productive
  block with its days in service. The figure is the last key rather than the only
  one.
- **A block with no `productive_from_date` is excluded and named.** Assuming it is
  productive puts acres in the denominator that may be a three-year-old planting —
  which makes the figure *look* conservative while turning a data gap into a number
  somebody acts on.
- **Maintenance capex is actual spend, never a percentage of revenue.** The
  shortcut destroys the only interesting signal: an operation that spent nothing on
  replacement borrowed the year from the orchard, and a percentage formula reports
  a well-maintained farm every time.
- **After migrating**: run `backfill_asset_capex_type` with a cutoff date, then
  fill in `productive_from_date` on the blocks. `computation_warnings` is the
  worklist for the second.

## 0.19.4 — 2026-08-03

**What the shift was actually like.** v0.19.3 shipped `Farm Shift Weather Reading`
with nothing writing to it, so that wiring a fetch would mean writing a service
rather than migrating a schema under live compliance records. This is the fetch. A
foreman's logged water break says what was **done**; nothing on the shift said what
it was done **about**, and OAR 437-004-1131 is a rule about conditions. Full notes:
[`RELEASES/v0.19.4.md`](RELEASES/v0.19.4.md).

Suite: 3,734 → **3,835 passing**. Tool surface: 224 → **229** (102 read, 127
mutating).

### Added

- **`erpnext_mcp/services/weather.py`**, in a new `services/` package whose premise
  is one property: a module in here talks to somebody else's server. Ported in
  **shape** rather than in code from `farm_app/app/utils/weather.py` — that module
  is the Flask side's agronomy surface (soil temperature, chill hours, growing
  degree days, evapotranspiration) and none of its functions is what a shift needs.
  What carried over is the idiom: one `requests.get` with an explicit timeout, a
  normalised dict out, an error path that returns rather than throws.
- **A fifteen-minute scheduled sweep** — the app's first `cron` entry and its fifth
  scheduled job. It walks every Farm Shift with no `end_datetime` and a
  `farm_location_gps`, asks Open-Meteo what the conditions are there, and appends a
  reading. Fifteen minutes rather than hourly because -1131 asks what the conditions
  were across an exposure period, and nine readings on a nine-hour shift is a sketch
  where thirty-six is a timeline.
- **`Weather Settings` Single and `Weather Company Override` child table.** The kill
  switch, the cadence, the cache lifetime, the HTTP timeout, three thresholds and
  three configurable Open-Meteo endpoints — plus per-entity threshold overrides where
  the shipped numbers are wrong for a crop or a camp. **Every override column is
  nullable and null means the parent**, so a row that exists to lower one entity's
  wind limit leaves its heat limits alone. The controller refuses a non-positive
  timeout, a non-`http(s)` URL, a negative threshold and two rows for one company.
- **`Threshold Crossed` compliance events, logged automatically.** A reading at or
  above threshold writes one — **once per shift, not once per reading**, or a hot
  afternoon buries the water breaks under thirty-six identical rows. It carries no
  `logged_by`: nobody logged it, and naming the foreman would put their identity
  against an observation they did not make. Wind fires on **Spray** shifts only.
- **Five tools** — two mutating (default OFF), three read (default on):
  `fetch_weather_now`, `backfill_weather_for_shift`, `list_shifts_missing_weather`,
  `get_weather_timeline`, `get_weather_settings`. The guards are the shift tools',
  imported rather than restated.
- **Historical backfill.** `backfill_weather_for_shift` reconstructs a closed
  shift's timeline from Open-Meteo's archive at that API's own hourly granularity,
  filtered to the shift's own period, idempotent to the minute, never editing a
  reading. Every site that installs this has a season of shifts with an empty weather
  table, and a shift with no timeline is not one that was compliant or non-compliant
  — it is one nobody can say anything about.
- **`list_shifts_missing_weather`**, the worklist: closed shifts carrying fewer than
  one reading per hour of their own length. Shifts with no coordinates are reported
  separately, because no amount of backfilling documents one.

### Changed

- **Compliance event weather snapshots fill themselves in**, from the reading
  current at the event's own instant — the last one at or before it, within half an
  hour. **Earlier beats later**: the reading current at 09:15 is the conditions the
  foreman was standing in when they called the break. Past thirty minutes nothing is
  copied, because a temperature from an hour away is not evidence about this moment.
- **Heat Exposure Event maxima compute off the shift's timeline.** `max_temp_f`,
  `max_heat_index_f`, and `threshold_crossed_at` — the **earliest** crossing rather
  than the hottest moment, because every obligation runs from the instant the shift
  passed the threshold.
- **Manual entry always wins** in both of the above. An on-site reading beats a
  modelled figure for a grid square measured in kilometres; the computed value fills
  a blank and never corrects an answer.
- **`settings.seed_defaults` takes a doctype**, so the eighth install job can seed
  `Weather Settings` through the one place that knows how a Frappe Single hides its
  declared defaults. On a fresh install `http_timeout_seconds` has no row, reads
  `None`, and becomes a timeout of zero one `int()` later — a connection that fails
  immediately, every time, with nothing in a log to say why.

### Notes

- **The heat index is computed, not read off the API.** Open-Meteo returns
  `apparent_temperature`, which folds in wind and radiation and is a wind-chill
  figure in winter. The NWS heat index is temperature and humidity, and it is what
  the rule turns on: **88 °F at 70 % humidity is a 100 °F heat index** — the worked
  example in the doctype's own field description, and now a test. Both inputs are
  stored beside the result so a disputed index can be recomputed from the observation.
- **Nothing here ever creates a Heat Exposure Event.** That record says which crew
  was exposed, what water was provided, whether the rest cycle was *taken*, whether
  anybody showed signs and what was done — five judgements by the person who was
  standing there, under their signature. **The sweep surfaces the condition; the
  foreman decides whether it is a record.**
- **The backfill writes no compliance events.** A `Threshold Crossed` row dated last
  July on a closed and signed shift would be an observation nobody made, sitting
  beside water breaks somebody did. The crossings are counted and reported instead.
- **The cron is the ceiling and `fetch_interval_minutes` is the floor.** A Frappe
  cron expression cannot be rewritten from a form, so the setting is honoured by
  skipping a shift whose newest reading is younger than it — raising it gets readings
  less often, which is the change operations ask for; lowering it below fifteen
  changes nothing.
- **Open-Meteo needs no API key, which is a reason to be more careful with it.**
  Cache by coordinate rounded to four decimals (~11 m, the same block), skip a shift
  read within the interval, and treat a 429 or 5xx as an instruction: exponential
  backoff per coordinate, doubling, capped at an hour. Nothing raises — a failed
  fetch is a missing reading, and a shift with a gap is an infinitely better outcome
  than a scheduler that stopped.
- **No `update_weather_settings` tool, deliberately.** Three outbound URLs and three
  numbers deciding whether a hot afternoon is logged at all; a model that could raise
  the heat threshold past anything Oregon produces would leave a site that behaves
  normally and never says anything is wrong. The Desk form is the write surface.
- **Both new hooks are controllers, not `doc_events`.** `hooks.py` promises this app
  installs no document hooks and `test_hooks.py` forbids the key by name — because
  that hook is how an app changes a doctype it does not own. `Farm Shift` and `Heat
  Exposure Event` are this app's, so the rules live in their controllers, where they
  also run on a Desk edit.

## 0.19.3 — 2026-08-03

**Compliance anchors to a shift, not to a task.** A task completion carries a
point-in-time reading; a shift carries a timeline. Oregon OSHA does not ask what
the temperature was when one job closed — it asks whether the July 15 shift
complied with OAR 437-004-1131 from start to finish, and only a record spanning
the exposure period can answer. Six new DocTypes, ten new tools, the thirteenth
compliance rule, and a one-way bridge into Frappe HR. Full notes:
[`RELEASES/v0.19.3.md`](RELEASES/v0.19.3.md).

Suite: 3,653 → **3,734 passing**. Tool surface: 214 → **224** (99 read, 125
mutating).

### Added

- **`Farm Shift`, `Farm Shift Crew Member`, `Farm Shift Compliance Event` and
  `Farm Shift Weather Reading` DocTypes.** A crew, at a place, for a span, with a
  timeline of what was done about the conditions. `status` is computed from two
  facts in one order: no end time means **Active** whatever anybody ticked,
  because an open shift is what the v0.19.4 weather sweep walks. Docnames are
  `SHIFT-2026-0001`, keyed to the year the shift **started** — a night shift
  beginning on 31 December belongs to the year it began.
- **The foreman is the sole actor, and there is no clock-in tool.**
  OAR 437-004-1131 puts the water, shade, rest-cycle and observation obligations
  on a *named* responsible person, and FSMA §112.161(b) asks that person to sign.
  A crew of thirty each clocking themselves in is a shift with thirty people
  responsible for the record, which is a shift with nobody responsible for it.
- **Per-worker attendance inside the crew envelope.** Every crew row carries its
  own `joined_at` and `left_at`. `remove_worker_from_shift` **sets `left_at`; it
  does not delete the row** — the row is the only record that this person was on
  the shift at all, which is what a wage claim turns on.
- **`Heat Exposure Event` DocType**, with `Heat Acclimatization Worker` behind
  its plan. One per shift, submittable, signature required to submit. The
  acclimatization plan NAMES the workers with under fourteen days in the heat per
  -1131(g), because these are the people most likely to be hospitalised and a plan
  for "the new workers" is one an inspector cannot check.
- **Ten tools** — six mutating (default OFF), four read-only (default ON):
  `start_shift`, `add_worker_to_shift`, `remove_worker_from_shift`,
  `log_shift_event`, `end_shift`, `create_heat_exposure_event`, `list_shifts`,
  `get_shift`, `list_heat_exposure_events`, `get_heat_exposure_event`. The guards
  are `create_employee`'s, imported rather than restated: a shift is a personnel
  record before it is a compliance record.
- **The Attendance bridge.** Closing a shift writes one **submitted**
  `Attendance` per crew member spanning **that person's own** `joined_at` to their
  own `left_at`. Not the shift's span: a worker who arrived an hour late and left
  two hours early worked six hours of a nine-hour shift, and a row claiming nine
  is wrong in the employer's favour. Submitted rather than drafted because
  `get_attendance_summary` counts `docstatus 1` only.
- **`Attendance.farm_shift` Custom Field**, declared in `compliance_fields.py`
  beside the v0.15.0 columns and reported by `before_uninstall`. Without it a
  shift-formed day is indistinguishable from a hand-keyed one, and the bridge
  cannot tell its own rows from anybody else's.
- **`supervisor_review_lapsed`, the thirteenth compliance rule.** It watches a
  signature that was never put on a record. Warning at 14 days, Critical past 30,
  auto-dismissed the moment somebody signs. FSMA §112.161(b) is the most commonly
  cited finding against farms whose actual practice is sound — USDA GAP does not
  ask for a supervisor's review, so an immaculate GAP binder fails on the one
  element its own auditor never mentioned. It walks a **table** of doctypes
  carrying the §112.161(b) columns; one row today, and four more of this app's
  doctypes are a one-line addition each.
- **A heat exposure section on the OSHA audit packet.** On that packet alone: a
  GAP auditor handed a heat register is being shown evidence for a scheme they do
  not audit. Drafts excluded, gaps disclosed.
- **`Records` as a `Compliance Alert.category`.** A supervisor review is not a
  Workforce item once the rule reaches water tests and cabin walks.
- **A `SHIFTS` role group.** Foreman and Farm Manager get **full**; Compliance
  Officer gets **read**, because forming a shift is operational and signing one
  off belongs to the supervisor who was standing on the block. Field Worker gets
  read, so the app can show a worker which crew they are on.

### Changed

- **`Farm Shift Weather Reading` ships empty.** Its shape is what the
  compliance-event snapshots denormalise from and what the heat record's maxima
  will be computed over, so fixing it now means v0.19.4 wires a fetch rather than
  migrating a schema under live compliance records. `start_shift` says so when a
  shift has no `farm_location_gps`.
- **`add_worker_to_shift` defaults `joined_at` to NOW; `start_shift` defaults it
  to the SHIFT'S START.** Opposite defaults, and right for the same reason:
  everybody rostered at the beginning was there at the beginning, and stamping
  them with the moment the API call landed would shave minutes off every one of
  their days.

### Refused

- **A close with no signature.** An unsigned close is an `UPDATE` setting a
  timestamp; §112.161(b) asks for a review dated **and signed**. The shift stays
  open and nothing is written.
- **A second heat record for one shift.** Two records about one exposure period
  will disagree, and the one an inspector finds will be whichever was filed
  second. Refused before writing anything, naming the record that already exists.
- **A `training_verified` claim the register contradicts**, checked **as of the
  day of the shift** rather than as of today — a card that expired last week was
  current in July. The same audit packet carries both this record and the
  register, and a packet that contradicts itself is worse than one with a gap.
  Claiming `false` is accepted and the missing names are reported.
- **Heat illness signs observed, no emergency response, and no notes.** Signs
  seen and nothing done is the sequence that kills people. There are legitimate
  versions and every one of them is a sentence somebody can write — what is
  refused is the silence.
- **The same person on a crew twice**, an acclimatization plan naming somebody
  off the crew, a shift ending before it started, and a crew row leaving before it
  joined.

### Not refused, and stated instead

- **A shift with obligations unmet, or an empty event timeline.** A day where the
  shade trailer broke down and the crew went home at eleven is a real shift with a
  real gap, and a system that would not let it be recorded would produce either a
  false record or no record.
- **An event timestamped slightly outside the shift.** A clock five minutes out
  is not a false record, and refusing would mean the break goes unlogged rather
  than logged approximately.
- **A failed Attendance write on close.** A site without Frappe HR, an employee
  archived since the shift ran, a day already keyed in by hand: each is reported
  and none refuses a signed shift. The signature is the compliance act and the
  payroll row is the convenience.

## 0.19.2 — 2026-08-03

**Two facts the app already knew stop living only in comments.** A compliance
alert now says which audit it is evidence for, so the calendar can be read one
inspection at a time; and a training curriculum now says which audits it answers,
so thirty records of one course stop being thirty chances to mistype the tag.
Both close holes named in earlier releases' own docstrings — v0.19.1's debrief
item 1, and the free-text `training_type` v0.19.0 argued for and flagged. Full
notes: [`RELEASES/v0.19.2.md`](RELEASES/v0.19.2.md).

Suite: 3,601 → **3,653 passing**. Tool surface unchanged at 214 / 95 read.

### Added

- **`Compliance Regime`, `Compliance Regime Link` and `Training Type` DocTypes.**
  The first is a picker's table seeded from `erpnext_mcp/training.py`'s `REGIMES`
  on every migrate — the tuple in code is still what decides what a regime *is*.
  The second is the Table MultiSelect child behind two fields. The third is a
  curriculum master anybody can add a row to.
- **`Compliance Alert.regime`** — written by the sweep, never typed. Ten rules
  carry a constant (an overdue cabin inspection is an OR-OSHA item whoever is
  asking); `certification_expiring` and `training_expiring` tag each alert from
  the RECORD, because an applicator licence is WPS evidence and a GlobalGAP
  certificate is not. Multi-select because an untested block in spray rotation is
  an FSMA Subpart E finding **and** an OR-OSHA one.
- **`regime` on `get_compliance_calendar`, `list_compliance_calendar_for_me` and
  `refresh_compliance_alerts`.** Matching is by TAG, never substring —
  `GlobalGAP` contains `GAP`. An unrecognised value is **refused**: filtering on a
  word nobody understood returns an empty calendar, and an empty compliance
  calendar reads as a clean one.
- **A narrowed sweep dismisses nothing.** `refresh_compliance_alerts(regime=...)`
  runs only the rules that raise that audit's evidence; every other rule raises
  nothing **and its alerts are untouched**, because a filtered sweep that cleared
  the rules it did not run would empty most of the calendar and look like
  progress. `rules_skipped` names each one.
- **An open-items section on every audit packet**, scoped to the same regimes as
  the training section and narrowed by the same `regime` argument. A disclosure
  rather than a confession: the kairotic gate has already refused the packet over
  any open corrective action from inside the period, so what is left is
  forward-looking work from a list the operation's own records generated. It is
  the one section not scoped to the period — an expired licence is expired now
  whatever quarter the packet covers. `generate_compliance_packet` gained the
  matching annex.
- **Two regime tokens.** `OTCO` (Oregon Tilth Certified Organic — the certifier
  that holds the file, as against NOP the rule it certifies) and `Internal` (the
  operation's own standard: real work, real due date, nobody coming to inspect).
  `Internal` is a tag rather than an absence because an untagged alert is
  invisible to every regime filter, and silently invisible is the one thing a
  compliance calendar must not be.
- **Ten seeded curricula**, with their regimes and a retention **derived** from
  the longest tag rather than stated — so a seed cannot contradict the doctrine
  that the longest window governs. Seeded through the idempotent installer, not
  as a Frappe `fixtures` entry, which `test_hooks.py` forbids by name: a fixture
  cannot skip what a site already has, so an operator who corrected a curriculum
  would get it corrected back on the next migrate.

### Changed

- **`Employee Training Record.training_type` is a Link to `Training Type`.** Still
  not a Select — `record_training` accepts free text and CREATES the curriculum
  the first time somebody files a course this site has not run, so nothing has to
  be configured before a training can be filed. That happens in the controller's
  `validate`, not in the tool, because Frappe checks Links after `validate`: doing
  it only in the tool would leave the Desk form, a data import and the iOS app
  throwing a link error at somebody who typed the true name of a real course.
  The new curriculum takes the regimes its NAME implies, not the session's — the
  record says what one afternoon covered, the curriculum says what the course
  normally answers.
- **`training.py`'s v0.19.0 argument against a regime doctype is reversed by
  half, and the half that still holds is stated.** `Employee Training Record.regimes`
  is still a delimited tag list. `REGIMES` is still the only definition, the master
  is seeded from it, and `canon`/`parse`/`require`/`matches` are still the only
  readers — child rows are converted at the boundary so nothing downstream knows
  which shape a field used.
- **Four places said "the eight are" against a list of ten.** They call
  `training.vocabulary_note()` now; a count in prose is a second copy of a fact
  the tuple already holds.

### Migration

- **`erpnext_mcp.patches.migrate_training_types`** — creates a `Training Type` for
  every distinct free-text value, then re-links. Because the master names itself
  from `training_type_name`, the docname **is** the text already stored, so the
  ordinary record is not rewritten at all; only text needing normalising (spacing,
  or a casing that would split one curriculum across two masters) is touched. It
  **does not touch `regimes` on any existing record** — those carry what somebody
  tagged them with at the time, and overwriting that with a heuristic would be
  replacing evidence. Idempotent, listed in `patches.txt` **and** called from
  `after_migrate`, so it runs at least twice on any real bench and is a no-op the
  second time.
- The `tilth` alias still resolves to **NOP**, not to the new `OTCO`. Records
  written through it since v0.19.0 are stored as NOP, and repointing it would make
  one word mean a different set of rows on the read path than it wrote on the
  write path.

### Upgrade note

Alerts already on the site are **untagged until the next sweep** — at most an hour,
or one `refresh_compliance_alerts` call. Until then a regime-filtered calendar
returns fewer rows than it will, so the unfiltered calendar reports the untagged
count and says so rather than letting a short list read as a short list of
problems.

## 0.19.1 — 2026-08-03

**Three items off the v0.19.0 debrief, one of which turned out not to exist.**
The stale-citation sweep found the codebase already clean — every `-1090` and
`-1130` in the repository is in the research documents' own account of
correcting them — and the count assertion it was paired with found a real drift
nobody had noticed. Full notes:
[`RELEASES/v0.19.1.md`](RELEASES/v0.19.1.md).

Suite: 3,588 → **3,601 passing**.

### Added

- **`farm_location_gps` on `Farm Task Assignment` and `Water Test`** — FSMA
  §112.161(a)(1)(i) asks an activity record for the farm's name **and** its
  location; the name was snapshotted and the location was not. Data, optional,
  free text: `"45.5152,-122.6784"` where the handset had a fix, `"MC-Cabin-01"`
  where a metal roof meant it did not — a coordinate nobody could take is worth
  less than a place name somebody can stand in. Additive, so no migration and no
  back-filling; records filed before this release have it blank, which makes
  them older rather than invalid. `complete_task_via_mobile` and
  `create_water_test` accept and forward it.
- **The HTTP mobile API fills it from the fix the app already sends.**
  `latitude`/`longitude` have been in every completion since v0.18 and reached
  only the audit row, because Farm Task Assignment had no column for them. They
  now become `farm_location_gps` — so the location half of §112.161(a)(1)(i)
  arrives without an iOS release. An explicit `farm_location_gps` wins over the
  pair, and **a pair that will not parse is dropped rather than raised on**:
  failing a completion carrying photographs, a signature and a compliance record
  over a malformed coordinate would trade the record for its least important
  field. The pair as sent stays in the audit row either way.
- **`tests_standalone/test_tool_catalog_count.py`** — the catalogue's own counts,
  asserted against the registry they document. v0.19.0 caught the total saying
  206 against 210 and fixed it by hand; this is the test that was named as the
  follow-up.

### Fixed

- **`docs/tool-catalog.md` said 85 read tools; `registry.READ_TOOLS` has 95.**
  Found by the new test on its first run — the total was correct at 214, so the
  drift was in the number nobody had thought to check. Long-standing; no release
  can be blamed for it, which is the argument for the test.

### Unchanged, and why

- **No OR-OSHA citation edits.** The sweep for `-1090`, `-1130` and
  `-1005`-as-sanitation found nothing to correct in code, doctype JSON, fixtures
  or comments. Every live citation was already `-1120` (labor housing), `-1131`
  (heat illness) or `-1005(10)` used correctly for PPE. See the release notes for
  the matches that were left alone and why.
- **No `Spray Record` doctype.** It does not ship with this app — `Spray Log`
  belongs to `farm_precision_ag` — so the field was not added there and nothing
  was invented to hold it.

## 0.19.0 — 2026-08-03

**The calendar could see every document on the farm and nothing a person knew.**
Eleven compliance rules watched certificates, policies, cabins, water, filings
and audits. None watched TRAINING — what WPS asks for every twelve months, what
Oregon's heat rule asks for annually before the first 80 °F shift, what FSMA
Subpart C asks for on hiring and periodically, and what a GAP auditor asks for by
name with the signature attached. Full notes:
[`RELEASES/v0.19.0.md`](RELEASES/v0.19.0.md).

Suite: 3,514 → **3,588 passing**.

### Added

- **`Employee Training Record` doctype** — one training event, tagged with every
  regime it answers. A single session covering hygiene, pesticide safety and heat
  satisfies GAP, WPS and OR-OSHA at once, so `regimes` is a TAG LIST over a
  closed vocabulary of eight and one record appears in every packet it earned.
  Filing it three times produces three records that disagree by August.
  **Matching is by token, never by substring**: `GlobalGAP` contains `GAP`, and a
  `LIKE` filter would hand a USDA auditor evidence from a different scheme.
  `status` is computed on save; `activity_datetime` and `farm_name_snapshot` are
  derived; a future completion date, an expiry before the completion, a
  self-review, a review dated before the training and an unknown regime tag are
  all refused.
- **The FSMA §112.161 fields, from the doctype's first version** —
  `person_performed_signature`, `supervisor_reviewed_by/on/signature`,
  `activity_datetime`, `regimes`, `content_topics_covered`, plus
  `farm_name_snapshot` (§112.161(a)(1)(i), which the spec did not list). They are
  impossible to retrofit truthfully: a signature backfilled the week before an
  inspection is evidence that somebody signed the week before the inspection.
  What is still missing is REPORTED as `fsma_112_161_gaps` rather than refused.
- **`training_expiring`, the twelfth compliance rule** — Warning at 90 days
  (what arranging a retraining actually takes), Critical at 30 (the next
  scheduled course may already be after the lapse), Critical once lapsed, and
  **nothing at all** where there is no expiry, because a renewal alert nobody can
  clear is how a calendar stops being read. The message carries the regimes and
  what stops being lawful — a handler whose WPS training lapsed cannot legally
  perform an application. Reads `expires_date`, not `status`.
- **Four MCP tools** — `record_training` and `sign_training_supervisor_review`
  (mutating, ship OFF), `list_trainings` and `get_training` (read). Guards are
  `create_employee`'s, imported rather than copied. The supervisor review is a
  SEPARATE call because §112.161(b) says "after the record is made" — a sequence,
  not a form field, and simultaneous timestamps are the shape of a record an
  inspector reads as assembled rather than kept.
- **A worker training section on every audit packet**, scoped to each audit
  type's own regimes (GAP → GAP + WPS; OSHA → OR-OSHA + WPS; EPA → WPS; FSMA →
  FSMA + WPS), plus a `regime` argument on `generate_audit_packet` that narrows
  it and is **part of the idempotence key** — a narrowed packet must never
  silently overwrite a buyer's full one. Unsigned and unreviewed records are
  disclosed in the section rather than filtered out of it.
- **`regime` on `generate_compliance_packet`** — a training annex over the
  packet's own period. A top-level argument rather than a filter on each packet
  type: both types this app ships are accounting artefacts, and a WPS key in a
  reconciliation's filter schema would be a worker-training question on a form
  about a bank account.
- **Retention, with citations** — five years where any tag is NOP
  (7 CFR 205.103(b)(4)), three for OR-OSHA, two for FSMA (21 CFR 112.164(a)(1))
  and WPS (40 CFR 170.309). The longest tag governs; computed on read, because a
  stored `destroy_after` that was right in 2026 and wrong in 2027 is worse than
  no column.
- **`tests_standalone/test_training.py`** (73 tests) and one integration test in
  `test_e2e_workflow.py` walking onboard → record_training →
  sign_training_supervisor_review → the record found by the packet generator for
  both regimes it was tagged for, and absent from one it was not.

### Changed

- **`ALERT_TASK_MAP`** gains `training_expiring`, so a lapse becomes dispatchable
  work like every other alert. `creates_record` is deliberately EMPTY: completing
  the task is arranging a retraining, and no builder can invent the topics
  covered or the trainee's signature — a task that auto-filed a record with
  neither would produce exactly the document an auditor disallows.
- **`roles.COMPLIANCE_REGISTERS`** gains `Employee Training Record`. A Foreman who
  can read the certificate register and not the training register cannot answer
  the question an inspector asks about their own crew.
- **`docs/tool-catalog.md`** said 206 tools while the catalogue held 210. The
  count is asserted in `test_protocol.py` for `registry.TOOLS` and nowhere for
  the documentation.

## 0.18.5 — 2026-08-02

**The workflow walks in CI, not at the iPhone.** A prevention release. v0.18.2,
v0.18.3 and v0.18.4 all shipped the same evening and every one of them was found
by Tim holding a phone in the field, because the suite tested each side against
itself and nothing tested the seam. This fixes one live crash and spends the rest
of its weight making the next three bugs of that shape fail in
`python3 -m unittest` instead. Full notes:
[`RELEASES/v0.18.5.md`](RELEASES/v0.18.5.md).

Suite: 3,474 tests with 1 failure → **3,514 passing**.

### Fixed

- **`dashboard.py` Number Card `filters_json`** — the Farm Task Dispatch
  workspace answered Internal Server Error. Every card spec used Frappe's dict
  filter shape, which is valid to query WITH and invalid to build ON:
  `number_card.get_result` appends its comparison-arrow date clause to the
  parsed filters, `frappe._dict` has no `.append`, and `_dict.__getattr__`
  turns that into `TypeError: 'NoneType' object is not callable`. New
  `card_filters()` emits the list shape and all four spec tuples go through it;
  the three charts v0.18.3 gave `"{}"` are now `"[]"` for the same reason.
- **`dashboard._repair_filters`** — new. Fixing the specs only fixes new sites;
  `_build` leaves existing cards alone by design, so every site from v0.16.0
  onward holds eleven broken ones. The repair rewrites dict-shaped
  `filters_json` in place, **carrying the operator's own clauses across rather
  than replacing them**, and never touches one already in the list shape.
  `install.py` prints what it repaired, by name.
- **`tools/inspections.py:_link_evidence_files_to_parent`** — v0.18.4's
  permission cascade read `row["file"]` off the evidence rows, which covers the
  child tables and nothing else. Two evidence Files are Attach fields:
  `Housing Inspection.signature` (where `complete_farm_task` puts
  `signature_file` — the attestation an auditor is most certain to open) and
  `Water Test.lab_report` (the entire evidentiary content of a water test).
  Both stayed readable only by the uploader. Evidence rows naming a `file_url`
  rather than a docname were skipped for the same reason; `_file_docname` now
  resolves either spelling.
- **`tests_standalone/test_uploads.py`** — the suite's one pre-existing failure.
  v0.18.4 raised `MAX_CHUNK_BASE64` to 800 KB and this assertion still had
  `200 * 1024` typed into it. Now reads the constant.

### Added

- **`tests_standalone/test_ios_contract.py`** (26 tests) — a Python mirror of
  each iOS `Codable`, run against the real response of all eleven mobile
  methods, with the wire's own JSON encoding applied first. Distinguishes
  STRICT fields (`try c.decode` — the whole row throws, which is what v0.18.2
  did) from LENIENT ones (silently absent), and checks enum and timestamp drift.
  Every mirror cites the Swift file and line it transcribes, is itself fed the
  broken payload from the release that shipped it, and the suite fails if a
  twelfth method is published with no mirror.
- **`tests_standalone/test_e2e_workflow.py`** (9 tests) — Tim's ask. Builds a
  company, camp, worker and credential from an empty site through the real
  tools, then walks claim → start → chunked upload → complete → Housing
  Inspection through the mobile endpoints alone. Asserts the **state of the site
  afterwards**: the child table, each `File`'s `attached_to_doctype` /
  `attached_to_name`, and that the answered alert is gone AND `auto_dismissed`
  on the next sweep. Covers the clean-pass branch, the evidence-contract
  refusal, upload-session ownership and cross-entity scoping.

### Changed

- `RELEASES/v0.18.5-spec.md` → `RELEASES/v0.18.6-spec.md`. It was a v0.18.3
  planning doc by its own first line; its contract-test item shipped here, and
  `record_training` is what remains.

## 0.18.4 — 2026-08-02

**Chunk size ceiling + evidence file permission cascade.** Two bugs bundled
— v0.18.3 unblocked the upload permission, then Tim's phone tripped on the
NEXT constraint (server-capped chunk size), and after that was fixed the
Housing Inspection record appeared but Tim's admin account couldn't read the
attached photos (uploader-owned private Files with no link to the parent
record). Full notes: [`RELEASES/v0.18.4.md`](RELEASES/v0.18.4.md).

### Fixed

- **`tools/uploads.py:MAX_CHUNK_BASE64`** — `200 * 1024` → `800 * 1024`. Old
  cap was set for MCP tool callers composing arguments in a model's context
  window, not relevant to iOS. Farm Ops iOS sends 512 KB raw chunks (~700 KB
  base64); the old ceiling rejected every iPhone photo and iOS's SyncEngine
  marked seven queued completions Failed. Total-file cap moves from ~90 MB to
  ~360 MB (600 chunks × 800 KB), plenty for compliance photos.
- **`tools/inspections.py:_link_evidence_files_to_parent`** — new helper.
  After a Housing Inspection / Detector Test / Water Test is inserted, sets
  `File.attached_to_doctype` and `File.attached_to_name` on every evidence
  File. Without this, uploader-owned private Files stay unlinked to the
  compliance record — an auditor opening the record sees the child rows but
  cannot preview the photos. `File.has_permission` doesn't traverse child-
  table references; setting `attached_to_*` is Frappe's own idiom for "this
  file belongs to that record" and cascades the parent's permission. Uploader
  still owns the File, still marked private, but the parent record's read
  permission is now the gate.

## 0.18.3 — 2026-08-02

**Evidence upload permission fix — the last thing keeping Farm Ops's Complete
button from writing a Housing Inspection.** Tim's iPhone showed "Completed —
saved on this device, will sync when back in range" but the task stayed
In-Progress on the server and no record ever appeared. Root cause: the very
first server call in the evidence path, `stage_file_chunk`, returned HTTP 403
"That request could not be completed" — Frappe's default PermissionError —
because the `Staged File Upload Session` and `Staged File Chunk` doctypes grant
Desk permissions to `System Manager` and `Accounts Manager` only. `guard.endpoint`
had already validated the caller as a Farm Ops user with an active Mobile Access
Grant (which IS the permission boundary for evidence uploads), but Frappe's
doctype-level check refused the insert anyway. Full notes:
[`RELEASES/v0.18.3.md`](RELEASES/v0.18.3.md).

### Fixed

- **`tools/uploads.py`** — four insert/save call sites now
  `ignore_permissions=True`: `_open_session`, `stage_file_chunk` chunk insert +
  session save, `declare_expectations` save, `commit_staged_file` bulk-chunk
  insert + session save. No doctype JSON change (Desk visibility stays exactly
  what it was — operators do not want the Desk showing every in-flight photo).
  No guard change, no new roles, no schema migration.

## 0.18.2 — 2026-08-02

**iOS workflow hotfix — three bugs Tim's iPhone testing surfaced tonight.**
`claim_task` returned `{"name": null, ...}` because it asked for a `"task"`
wrapper `dispatch.claim_farm_task` deliberately doesn't produce, so every claim
crashed the iOS Codable decoder with "Bad value at 'name'". Evidence never
persisted because the iOS phone spelled its file references `file_token` and
`kind` and the backend read only `file` and `evidence_type` — so
`normalise_evidence` refused every completion silently, no Housing Inspection
was ever written, no photo was ever attached. And three dashboard charts had
been failing to build every migrate for weeks because their specs didn't set
`filters_json` and Frappe treats it as mandatory. Full notes:
[`RELEASES/v0.18.2.md`](RELEASES/v0.18.2.md).

### Fixed

- **`api/mobile.py:claim_task`** — extract task fields out of the flat
  `dispatch.claim_farm_task` response instead of asking for a `"task"` wrapper
  it doesn't produce. `start_task` and `get_task` worked because their inner
  tools DO wrap `data["task"] = task` explicitly; claim was the odd one out.
- **`tools/inspections.py:normalise_evidence`** — accepts iOS's field spellings
  alongside the existing ones: `file_token` beside `file`, `kind` beside
  `evidence_type`. iOS sends lowercase `"photo"` / `"signature"`, which is
  title-cased before the doctype's Select validator sees it. Fixes evidence
  attachment for Housing Inspection, Detector Test, Water Test, and Farm Task
  Assignment (all four use the same `Farm Task Evidence` child doctype).
- **`dashboard.py:CHARTS`** — three chart specs (Compliance Alerts Raised Over
  Time, Certificate Expirations Ahead, Regulatory Filings by Agency) now set
  `filters_json: "{}"`. Migrate no longer warns and the Command Center renders
  complete.

## 0.18.1 — 2026-08-02

**The Employee register, because a working credential is not a working task
board.** v0.18.0 got a phone all the way through the funnel and then
`list_my_tasks` refused it — correctly — with "set `user_id` on their Employee
record to this email address". Every Farm Ops method scopes work by EMPLOYEE, and
this app could create the User, the role, the entity scoping, the grant, the
credential and the QR — six things — and not the one that makes the other six
useful. Full notes: [`RELEASES/v0.18.1.md`](RELEASES/v0.18.1.md).

### Added — three tools, all mutating, all default OFF

- **`create_employee`** — one Employee record. Writes fourteen identity and
  assignment fields and refuses everything else BY NAME; payroll, tax and banking
  fields get their own refusal, because each has a form, an approval and a
  retention rule this app knows nothing about. Every Link is checked against this
  site's own records and every Select against this site's own options, with both
  refusals listing what is actually available. A field this site's Employee
  doctype does not carry is REPORTED rather than silently dropped. Mandatory
  fields are read off `frappe.get_meta` — stock Frappe HR requires `gender` and
  `date_of_birth`, plenty of operators do not — and the refusal names them
  before anything is written. A second record for the same name at the same
  company is refused with the existing docname.
- **`update_employee`** — the same fourteen fields on a record that exists, with
  the same allowlist and the same schema checks. Reports field by field what
  actually changed, with the previous value. Re-pointing an existing login needs
  `replace_user=true` — it moves that person's whole task history with it.
- **`link_employee_to_user`** — sets `Employee.user_id`, and REPORTS WHETHER THE
  PHONE WILL NOW WORK rather than merely whether the field was written.
  `linkage.farm_ops_ready` is true only when the account holds a Farm Ops role,
  its Mobile Access Grant is Active and the Employee is Active; when it is false
  the note says which of the three is missing and which tool fixes it. One
  person, one login in both directions; idempotent when the link already says
  what was asked for; refused for a User with no Farm Ops role and no grant,
  because such a link changes nothing today and silently grants a task board on
  the day somebody grants that account a role for an unrelated reason.

Three switches on ERPNext MCP Settings — `allow_create_employee`,
`allow_update_employee`, `allow_link_employee_to_user` — all default off.
Catalogue: 207 → **210 tools** (93 read, 117 write).

### Fixed — `onboard_employee` created the Employee before the login it named

It set `user_id` on the Employee and THEN created the User. `Employee.user_id` is
a Link, so on a real bench Frappe validates it on insert and the very first step
raised: **any onboarding that named an email could not complete.** The standalone
suite modelled that field as plain `Data` and called it a pass.

The order is now employee → login → **link** → QR → tasks, creation delegates to
`create_employee`, and the whole thing is idempotent: a second run with the same
arguments finds the Employee (by login, then by name and hiring company), the
account and the link, and duplicates none of them.

### Changed

- `onboard_employee` gains `issue_qr` (default **false**) and `url`. It returns
  the scannable PNG in the same response — and still NOT the decoded payload,
  which carries `api_secret` as readable text. The default is false because
  minting a QR rotates the account's secret, so a default-true would mean
  re-running an onboarding to add a W-4 knocked a live phone offline.
- `onboard_employee`'s result gains `link` and `qr` blocks, and `next_step` now
  names one thing rather than the first thing.

### Tests

78 new in `tests_standalone/test_employee.py`; suite 3,396 → **3,474**.

Two fidelity fixes in the double, both of which turned a silent pass into a real
check: the Employee's six Link fields are now modelled as Links rather than Data
(`user_id` is the one that mattered — see the fix above), `Employee.status`
carries its four options, and the seven personal fields `create_employee` writes
were added to the doctype's field list. Without them `compat.has_field` would
have dropped every value the new tool wrote. The four HR master doctypes
(Department, Designation, Employment Type, Gender) are seeded by `install_hrms`.

## 0.18.0 — 2026-08-01

**farmops-api: the mobile methods, off Frappe's request handler.** v0.17.2
carried the credential five ways and every one of them still came back as the
Desk's HTML login page through the Tailscale funnel. Five independent carriers
do not fail by coincidence — the remaining common factor was `/api/method/*`
itself. Bank Bridge, a plain WSGI service, works perfectly through the same
funnel. This release is that shape. Full notes:
[`RELEASES/v0.18.0.md`](RELEASES/v0.18.0.md).

### Added — a third transport for the same eleven methods

`erpnext_mcp/farmops_api/` is a Werkzeug WSGI service on `0.0.0.0:5250` inside
the ERPNext container, published to the host as **`127.0.0.1:5250`** so the
Tailscale container (which shares the host network namespace) can reach it and
the LAN cannot. The routes are:

```
POST /farmops/api/mobile/<one of nine>
POST /farmops/api/files/<one of two>
X-FarmOps-Token: <api_key>:<api_secret>
```

**IT DELEGATES TO v0.17.2's WRAPPERS — IT DOES NOT REIMPLEMENT THEM.**
`farmops_api/routes.py` is eleven entries and each one names the same
`@guard.endpoint`-wrapped function the whitelisted path calls, so the kill
switch, the role gate, the **Active Mobile Access Grant**, entity scoping, the
rate limit, the MCP Action Log row and the secret strip all run here because
they ARE the same code running. `ByteIdentical` in the test suite asserts the
responses match the old path's, serialised, over every read — which is what
makes "the same code" checkable rather than claimed.

Three things Frappe was doing and now are not, so this service does them:
**identity** (`X-FarmOps-Token`, verified by v0.17.2's own verifier, sharing
v0.17.2's failure counter — one credential, one budget, however many
transports); **a request-scoped Frappe session** (`init`/`connect`/`set_user`/
`destroy`, plus the commit Frappe's handler does at the end of a request —
without it a worker claims a task, gets a 200, and finds it unclaimed on the
next refresh); and **an envelope** — `{"message": …}` on success and Frappe's
`_server_messages` shape on failure, so a refusal reaches the phone as the
sentence it was written as.

**Every answer is `application/json`.** There is no path out of the service that
renders HTML and none that redirects — including a 404, a 405, and an unhandled
exception in the service's own error handling. That is the whole point: the
v0.17.x failure was HTTP 200 carrying an HTML login page.

**No new dependencies.** Werkzeug ships with Frappe and gunicorn is already in
the bench venv. Flask would have contributed `@app.route` and risked resolving
against Frappe's pinned Werkzeug inside the venv that runs the ledger.

### Changed

- `generate_mobile_login_qr` — the payload gains `api_base` (`/farmops/api`) and
  `endpoint` now points at the first URL a phone actually calls, so an operator
  can `curl` a card before handing the phone to somebody. **`v` deliberately
  stays 1**: `LoginQRParser` refuses a payload above the build's supported
  version, so bumping it would make every card unscannable by every phone
  already in the field.
- `create_mobile_user` — a `mobile_endpoint` key, and a `transport_note` that
  says plainly that the mobile path has no shared token and no CIDR gate, and
  what stands in their place.
- `api/fallback_auth.py` — `verify_credential` and `split_token` split out as
  public functions so both transports share one verifier and one failure
  counter. Behaviour unchanged.

### Unchanged, on purpose

`/api/method/erpnext_mcp.api.mobile.*` and `…api.files.*` are **still live and
still tested**. They work on the LAN and from inside the container, and they are
the fallback on the day the sidecar is the thing that is down.

### Deployment

Needs a rebuilt image (`ERPNEXT_MCP_VERSION=0.18.0`), the compose change that
publishes `127.0.0.1:5250`, eleven Tailscale Funnel paths, and a two-file iOS
change. All of it, in order, with the exact commands:
[`RELEASES/v0.18.0.md`](RELEASES/v0.18.0.md).

**Tests:** 3396 pass, 75 skipped, 0 failures (72 new).

---

## 0.17.2 — 2026-08-01

**The tunnel was eating the credential.** v0.17.1 shipped a working mobile API
and a working app, and every call from a phone still came back as an HTTP 200
carrying the Desk's `/me` page. Full notes:
[`RELEASES/v0.17.2.md`](RELEASES/v0.17.2.md).

### Fixed — the Tailscale proxy strips `Authorization`, so every call was Guest

Proven three ways on 2026-08-01: the call returns correct JSON against
`localhost` inside the container with `Authorization: token key:secret`, and
returns HTML `/me` with `sid=Guest` through `https://<host>.ts.net/…` — **both
from the public funnel and from a machine on the tailnet**, which rules out the
funnel edge and leaves the `tailscale serve` proxy step. Frappe authenticated
nobody, so `is_whitelisted` refused the Guest *before the method ran* and Frappe
rendered the login page at it. The credential was never wrong; it was never
presented.

**Fixed** — the app now sends the same `<api_key>:<api_secret>` pair three ways
and the server takes the first that resolves:

| | Carrier | Read by |
|---|---|---|
| a | `Authorization: token <key>:<secret>` | Frappe's own auth. Nothing in this app runs. |
| b | `X-FarmOps-Token: <key>:<secret>` | **new** `erpnext_mcp/api/fallback_auth.py` |
| c | `"_auth": {"api_key": …, "api_secret": …}` in the POST body | the same, when neither header survives |

**IT IS A SECOND DOOR AND NOT A BYPASS.** (b) and (c) answer exactly one
question — *which Frappe user is this* — using Frappe's own scheme: look the
`api_key` up on User, compare the stored secret with `hmac.compare_digest`,
refuse a disabled account. All seven of `api/guard.py`'s checks then run
unchanged on the user they establish: role gate, **Active Mobile Access Grant**,
entity scoping, kill switch, rate limit, audit row, secret strip. A wrong secret
is Guest, not an error, and produces the same opaque refusal as everything else.
An admin holding every role on the site still cannot get in without a grant.

**It could not have been done inside the endpoint.** `is_whitelisted` refuses a
Guest *before* a whitelisted method is dispatched, so a check written in
`guard.py` alone would have sat behind a door that never opens — the `/me` page
IS that refusal. Resolution therefore runs as an **`auth_hooks` entry**, which is
Frappe's own extension point for custom authentication and runs in the same
window Frappe settles identity in. `guard.endpoint` resolves a second time as a
belt: the standalone suite has no request lifecycle, and it is idempotent.

### Added — `auth_hooks`, this app's only request-lifecycle hook

Declared in `hooks.py` and bounded to the point of dullness: it acts on
`/api/method/erpnext_mcp.api.*` and returns after one attribute read for
everything else on the site, it never overrides an identity Frappe already
established, it grants no permission, and it cannot raise — `validate_auth` runs
it on every request, so an exception there would be an exception on every Desk
page of every installed app.

### Added — the audit row says which door the caller came in through

`mobile:<method>` rows in MCP Action Log now carry `(fallback_auth: header)` or
`(fallback_auth: body)`; a row with no such tag is a request whose
`Authorization` header survived. That is how you tell whether the tunnel is still
eating headers, and it is deliberately a tag on the row that was already being
written rather than a row of its own — if the proxy strips the header then every
call takes the fallback, and a row per fallback would be tens of thousands of
authentication records a day in the register a compliance auditor reads.

### Added — failed fallback verifications are metered, per key

Ten wrong answers for one `api_key` in a minute close the fallback path for that
key. The counter is keyed on a hash of the presented key and **not** on the
caller's address, because every phone on the farm arrives from the funnel's
single address and one stale credential would otherwise take the rest off the
air. A working phone never touches it.

### Changed — `create_mobile_user` and `generate_api_token` print both headers

New `farmops_auth_header` alongside `auth_header`, and the login-QR `app_note`
now names both headers and the `_auth` body form. Same credential, same strip:
the key trips `guard.strip_secrets` exactly as `auth_header` does.

## 0.17.1 — 2026-08-01

**Sprint 9's two tracks shipped incompatible contracts. This joins them.** The
backend worked and the app worked; they could not talk to each other, and the app
told a worker *"That task no longer exists — someone may have taken it."* No new
capability — every change here is a bridge to something v0.17.0 already shipped,
or a refusal. Full notes: [`RELEASES/v0.17.1.md`](RELEASES/v0.17.1.md).

### Fixed — the login QR was refused by the app it was for

`generate_mobile_login_qr` emitted no `type` field. `LoginQRParser` checks
`type == "farm_ops_login"` FIRST and refuses anything else by name, so every scan
failed and enrolment was impossible. The app's check is not pedantry: FarmCore and
BucketLog issue their own onboarding codes onto the same phones, and a scanner
that accepted any well-formed JSON would let two apps cross-sign credentials.

### Fixed — every call after enrolment was a 404

Wave A published the mobile capabilities as MCP tools behind a JSON-RPC envelope
at `erpnext_mcp.mcp.handle`; Wave B calls plain Frappe whitelisted methods at
`erpnext_mcp.api.mobile.*` with per-user token auth. Different transports.

New package **`erpnext_mcp/api/`** publishes exactly the eleven methods
`MobileAPI.swift` names, as guarded wrappers over the tools that already existed.

**THIS IS A NEW ATTACK SURFACE AND IS TREATED AS ONE.** `security.authorize()` is
called by `mcp.handle` and does not run on a directly-reached whitelisted method,
so these paths have no `X-MCP-Token`, no CIDR allowlist and no `allow_*` switch —
by construction, since a phone on LTE has none of the three. The gate is rebuilt
in `api/guard.py` and runs on every call: a global kill switch, a role gate, an
**Active Mobile Access Grant** (which is what keeps an admin's own account out —
Administrator holds every role, so the role gate alone would not), per-user rate
limits, entity scoping on every argument and every returned row, an MCP Action Log
row for every call including the refused ones, and secret stripping on the way out.

**An account with no Company User Permission is REFUSED, not shown everything.**
That deliberately inverts Frappe's own rule; on an endpoint reachable from the
internet the framework default is exactly backwards.

**There is no dispatcher.** A method exists as a function or its path 404s, so the
whole reachable surface is eleven `@frappe.whitelist()` lines. The other ~195
tools — `create_journal_entry`, `convey_parcel`, `import_chart_of_accounts` — are
not reachable from a phone at any path, and a test asserts it by enumeration.
Arguments that would have been dangerous are absent from the signatures rather
than filtered: `cancel` (a rejection could have deleted the work), `record_data`,
`worker_id`, `attach_to_doctype`/`attach_to_name`, `governance_document`,
`is_private`.

Refusals answer **503** (kill switch) and **429** (rate limit), never 401 —
`FarmOpsKit` reads 401 as "credential dead, sign out", which would lose every
queued completion on every phone.

### Added — `farm_ops_mobile_enabled`, the one-flip mobile shutdown

New Check field on ERPNext MCP Settings, **defaults ON**, honoured on every call.
Also settable in `site_config.json`; either source saying off means off.
Deliberately SEPARATE from the MCP master switch: stopping the AI and stopping the
phones are different decisions, and one control for both would guarantee that
doing either did both.

### Added — `clean_pass`, resolving a real contradiction in the spec

The rule "blank findings = clean pass" is unsatisfiable when the evidence contract
REQUIRES findings text, as MC-Cabin-01's habitability inspection does: blank is
then not submittable, so every completion would open a corrective action against a
cabin that is fine. The app asks the worker outright and sends the answer; the
server treats it as authoritative and does **not** parse the text for intent — a
worker typing the literal words "clean pass" must not trip a corrective action.

`clean_pass=true` leaves the produced record's findings field EMPTY, because that
is how `records.py` spells "nothing was wrong"; `"No findings reported by
inspector."` goes in the record's notes. `clean_pass=false` with nothing written
is refused. **Absent is a third state, not a synonym for false** — nobody was
asked, and the original rule applies unchanged. The worker's own words always
survive on the Farm Task Assignment.

### Added — the fields iOS decoded and the backend was not emitting

All closed on the backend so no new iOS build is needed: `location_type`,
`source_alert_explanation` (the app hides its "Why this task exists" card without
it), `assignment`/`claimed_at`/`started_at` on the task, `latitude`/`longitude`
where a boundary centroid exists (**omitted, not zeroed**, where it does not —
0,0 is a real place in the Gulf of Guinea), `roles`/`companies`/`default_company`/
`skills` on the user context, `urgency`/`regulation`/`linked_task` on alerts, and
`created_record_name`/`dismissed_alert`/`corrective_action_opened` on a completion.

### Added — evidence hashes are verified rather than recorded on trust

`finalize_staged_file` puts the app's SHA-256 on the staging session before
assembly (`uploads.declare_expectations`), so the commit is refused on mismatch
and the staged pieces are kept. The digest is not written to Farm Task Evidence —
that child doctype has no hash column and adding one is a schema change, not a
hotfix — and is deliberately not stuffed into `caption`.

### Cleanup sweep — six items landed, four needed no code

Full detail in [`RELEASES/v0.17.1.md`](RELEASES/v0.17.1.md). Each landed item is
its own commit.

**Fixed — `Housing Assignment` and `Family` were never entity-scoped.** The
promise that a User Permission on Company "restricts EVERY document that links
to a Company" is true, and the load-bearing clause is *that links to a Company*.
These two are the only doctypes this app ships that do not. `Housing Assignment`
is READ-granted to four roles and FULL to a fifth, so a field worker scoped to
one entity could list every camp bed assignment on the site — names, cabins,
wage-deduction status. Closed with `permission_query_conditions` and
`has_permission` hooks, which were previously FORBIDDEN outright by
`test_hooks.py`; the blanket ban is replaced by the narrower and stronger rule it
was a proxy for, asserted directly: every doctype these hooks name must be one
this app created. Both handlers fail OPEN — a query-conditions hook that raises
fails every list view of that doctype for everybody, forever.

**Changed — the compliance sweep runs hourly.** It is what makes a completed
task's alert go away, and nightly meant a worker saw the phone asking them to
walk a cabin they had already walked, all day. Safe at any cadence because it is
a full reconciliation, not an increment; there is now a test for that property.

**Added — a weekly Journal Entry drift watch that reports and never repairs.**
ACC-JV-2026-00073's defining property was that nothing complained for a week.
`repair_drifted_je_attributions` is deliberately not called from a schedule:
rewriting GL rows on submitted accounting documents on a timer would be a worse
bug than the one it watches for. It states the window it covered and says loudly
when it hit the scan cap, because a job that truncated silently and reported
nothing wrong would be worse than not running.

**Added — idle Farm Ops credentials are revoked after 30 days.** This REVERSES a
decision v0.17.0 wrote down, and the reversal is recorded rather than quietly
made: the threat changed when forty live credentials went into forty pockets on
the open internet. Mobile Access Grant gains `last_seen_on`, stamped by the
mobile gate at most once a day, and `persistent` to exempt a grant. It revokes
the TOKEN and never the account — roles and entity access are untouched and the
worker needs one new QR. It never ages a grant whose age it cannot establish.

**Added — `onboard_employee`.** One call for a new hire: Employee record,
paperwork, scoped login, optional first-day tasks. **The paperwork goes ON the
Employee record as private attachments, never in the governance archive** — that
register holds the documents describing the business and an auditor, an advisor
and a family member browse it. Asserted against the parsed module, because the
docstrings deliberately name the wrong tool in order to forbid it and a grep
cannot tell a prohibition from a call. Catalogue is now 207 tools.

**Audited, no change needed:** there are no `TODO`/`FIXME`/`HACK` markers or
`NotImplementedError` anywhere in the repository (every grep hit is a false
positive); the Kanban Board installer and the dispatch workspace were both
already shipped and tested in v0.16.1; and no dev flags (`developer_mode`,
`allow_tests`, `disable_website_cache`, `login_with_email_link`,
`enable_frappe_auto_indexer`) are set anywhere in the Docker image.

**3276 tests pass.** 168 of them are new.

## 0.17.0 — 2026-08-01

**Sprint 9 Wave A. Sprint 8 built a dispatch board; this is what makes it safe to
point forty phones at from outside the LAN.** Six roles, per-entity scoping
through Frappe's own User Permissions, an API credential with QR enrolment, a
public HTTPS transport over Tailscale Funnel, and seven tools shaped for a screen
rather than for a report.

**206 tools** — 93 read-only, 113 mutating. Every new mutating tool ships OFF.

### Feature A — the six roles, and the split that avoids a role per LLC

`Field Worker`, `Foreman`, `Compliance Officer`, `Farm Manager`, `Family Member`,
`Advisor`. Installed idempotently by `after_migrate`, alongside the Command
Center and the dispatch board.

**The role says what KIND of work somebody does; a User Permission on Company
says WHOSE.** No company name appears in any role definition, which is what keeps
the app install-agnostic — the alternative was "Field Worker — OpCo", "Field
Worker — Holdings", and a new role every time a family adds an LLC.

Two separations that look like oversights and are not, both asserted in **both**
directions because asserting one half of a separation proves nothing:

- **A Compliance Officer cannot dispatch.** Farm Task is read-only for that role.
  The person who decides a walk is required and the person who decides who walks
  it must not be one account, or that account could raise a task, assign it to
  itself and close it.
- **A Field Worker cannot read a Compliance Policy.** The SOP library names
  procedures, versions and effective dates a certification hangs on; a worker who
  needs one gets it in the task's `notes`, put there by whoever raised the job.

New tools: `create_mobile_user`, `list_mobile_users`, `revoke_mobile_user`. New
doctype: **Mobile Access Grant**, one row per person, named by their email —
because Frappe knows who has a login and none of the story around it, and the
part an audit asks for is *why it was taken away*.

#### The Custom DocPerm trap, which is the sharpest edge in the release

Frappe ignores **every standard DocPerm** on a doctype the moment ONE Custom
DocPerm exists for it — for every role on the site, not just the one the row was
written for. A single row granting Field Worker read on `Employee` would have
silently revoked HR Manager, HR User and System Manager from the Employee
register, during `bench migrate`, with nothing printed.

Two rules, both enforced in code and both tested:

1. **The standard permissions are mirrored into custom ones first**, per doctype,
   before the first new row lands — which is exactly what Frappe's own Role
   Permission Manager does under the name `setup_custom_perms`.
2. **Permissions are written ONLY onto doctypes this app owns.** A target whose
   module is not `ERPNext MCP` is refused and printed, not written. Not because
   the write would fail — it would succeed, which is the problem.

Consequence, stated rather than hidden: a Field Worker who needs their own
Employee record needs a role from the app that owns `Employee`.
`create_mobile_user` assigns the site's own `Employee` role alongside, and says so
when the site has not got one.

### Feature B — the credential, and what it does not promise

`generate_api_token`, `revoke_api_token`, `get_current_user_context`,
`generate_mobile_login_qr`.

A mobile client sends `Authorization: token <api_key>:<api_secret>` **alongside**
`X-MCP-Token`. The two do different jobs: the MCP token is **entry** and is still
CIDR-gated; the API credential is **identity** and grants nothing extra. Frappe
authenticates the second before this app's endpoint runs, and the new
`security.capture_calling_user` saves who it was in the one-line window before
`frappe.set_user()` assumes the MCP System User. That window is the whole basis of
per-user scoping, and it is the only transport change in this release.

A request that authenticated as one person and passes `user` naming another is
**refused**. An account that can name somebody else in a request body is not
scoped to anything.

**API secrets do not expire, and this release does not pretend they do.** Frappe
has no expiry on one, and adding a scheduled job to revoke them would mean
rewriting another app's User records on a timer with nobody watching — `hooks.py`
declares exactly two scheduled jobs and argues for both, and this would not have
survived the argument. `token_expires_on` is therefore a **review date**:
`list_mobile_users` flags an overdue grant loudly, `get_current_user_context`
reports it to the phone, and `revoke_api_token` is what actually ends access.
Calling a reminder an expiry would be a false assurance about a credential, which
is worse than none.

**The login QR is a live credential**, and every mitigation is time-shaped: 24
hours to enrol by default, `rotate_token` defaulting to true so re-minting
invalidates every older copy *and* every phone already enrolled, a hard refusal of
any non-HTTPS endpoint, and a **private** archive attachment for offline
distribution that the result tells you to delete once the phone is enrolled.

The matrix comes from `segno` (or `qrcode` where a bench has it); the **PNG is
written here**, in thirty lines of `zlib`, so the archived card is byte-identical
whichever encoder a bench happens to have — and so this app's own tests can decode
it. They do: the PNG is read back to a module matrix and compared with an
independent encoding of the payload the tool says it wrote.

### Feature C — Tailscale Funnel

`validate_public_endpoint` and `get_tailscale_funnel_config`, both read-only.
**There is deliberately no tool that turns Funnel on or off, and there will not
be** — changing what is reachable from the entire internet is an operator
decision made deliberately, and `tailscale funnel` needs a local socket and
privileges a containerised Frappe worker does not have.

`validate_public_endpoint` opens a TLS connection to the public name, reads the
certificate and POSTs a real MCP `tools/list`. **A 401 to the default
unauthenticated probe is the best possible result**: it proves the path is
reachable, the certificate is valid and the token gate is holding, all at once.
The reachable set is the configured `public_url` or a host under `.ts.net`, over
HTTPS, base URL only, redirects not followed — and `authenticate=true`, which
sends the real bearer token, refuses everything except `public_url`, because a
tool that will POST your token to a hostname in its arguments is a tool that
exfiltrates it.

`get_tailscale_funnel_config` degrades honestly. A container with neither the
`tailscale` binary nor the host's socket is the **expected** state on an Umbrel
and not a fault; the tool distinguishes that from "a daemon socket with no
client", and reports a config it cannot parse as unparsed rather than empty.

The README gains a full setup section — enabling Funnel on the tailnet, pointing
it at the port nginx already serves, making Frappe answer for the new hostname,
and the allowlist step people get wrong (a Funnel request arrives from loopback,
not from the phone). It also states the change in posture plainly: **everything
the API exposes becomes public and discoverable, and the auth token becomes the
whole boundary.**

### Feature D — seven tools shaped for a screen

`list_my_tasks`, `list_available_for_me`, `get_task_with_evidence_contract`,
`list_compliance_calendar_for_me`, `claim_task_via_mobile`,
`start_task_via_mobile`, `complete_task_via_mobile`.

Thin wrappers over Sprint 8's tools that add exactly three things: the worker
resolved from the authenticated request through their Employee record, a
screen-shaped payload, and the entity filter. **They add no rule and weaken
none** — the concurrent-claim limit, the refusal to self-pick Dispatched work,
the evidence-contract check and the empty-string `findings_text` distinction all
still come from `claim_farm_task` / `start_farm_task` / `complete_farm_task`,
because they *are* those tools.

Three refusals worth naming:

- **A login with no Employee record is refused by name.** An empty list would read
  on a phone as "nothing to do today", which is a different and much worse answer.
- **A `company` outside the worker's entities is refused, not emptied.** An empty
  result is indistinguishable from a quiet day.
- **`list_compliance_calendar_for_me` refuses an account with no Company User
  Permission.** This app reads through `frappe.db.get_all`, which does not consult
  User Permissions, so returning the whole site's calendar under that name would
  be a lie.

And one honesty: **`list_available_for_me` does not invent a skill register.**
Nothing on a Frappe site records what skills a worker has, so an unfiltered pool
comes back saying it is unfiltered. Guessing from a job title would have hidden a
spraying task from somebody because their title said "Harvest Crew", with no way
to tell.

### The test double grew four things it needed

`Role`, `Has Role`, `User Permission`, `DocPerm` and `Custom DocPerm` are now
modelled — `DocPerm` as a real child table of `DocType`, seeded from each
doctype's own shipped permissions, so the mirror has something real to copy and a
test can assert it copied. Three fidelity fixes came out of it, and each one is a
test that would otherwise have passed for the wrong reason:

- **`fields="*"` returns every column.** It is Frappe's own idiom and what
  `copy_perms` passes; a double answering it with one key literally called `"*"`
  would have made the mirror copy nothing while looking like it worked.
- **A Password field explicitly set to `""` is DELETED**, as
  `Document.save_passwords` does. Revocation clears `api_secret` that way, and a
  double that kept the old secret would let "revoke, then the credential stops
  working" pass while the credential still worked.
- **`Authorization: token <key>:<secret>` is authenticated**, reproducing
  Frappe's own api-key validation. That makes the credential round trip real
  rather than a fixture asserting who the caller is.

### Also

- `pyproject.toml` declares `segno`. All three runtime dependencies are imported
  defensively, and a bench missing one loses its own tools BY NAME with the pip
  command to fix it.
- `before_uninstall` warns about the Mobile Access Grant among the records that
  go — **and separately about what uninstalling does NOT remove**: the six roles,
  the User Permissions and the API credentials are all Frappe's own rows. Taking
  the app off removes the MCP endpoint from those accounts and leaves everything
  else, which is not what somebody uninstalling to revoke a fleet of phones would
  assume. Run `revoke_mobile_user` first.
- 3104 standalone tests, up from 2888.

## 0.16.1 — 2026-08-01

**Hotfix. v0.16.0's Farm Task Dispatch Kanban board was never created on a real
site, and the workspace beside it rendered empty.** The data half of v0.16.0 was
fine — 54 alerts became 54 tasks and `list_dispatch_board` returned them all —
but `/app/farm-task/view/kanban/Farm Task Dispatch` offered a "New Kanban Board"
dialog, because no such record existed.

Three defects, and the first is why nobody saw the other two.

### 1. The installer could not raise, and nobody read it either

`dashboard.install_dispatch_board()` catches its own exceptions into
`report["failed"]` and returns — which is correct, because an exception inside
`after_migrate` aborts `bench migrate` for the whole bench. But `install.py`
called it and **threw the report away**. So the Kanban insert failed, the
migration printed nothing, `bench migrate` exited zero, and the first anybody
knew was an operator opening the documented route a week later.

Not raising was the right half. This is the half that was missing:
`_report_failures` now prints every entry in `failed`, for the Command Center and
the dispatch board alike, so the next installer that cannot build something says
so while somebody is still watching the migrate scroll past.

**A builder that cannot raise AND is never read cannot report anything at all.**

### 2. It hardcoded another app's Select options

`Kanban Board Column.indicator` is Frappe's field, not this app's, and its
palette has been spelled differently across the versions erpnext_mcp supports.
v0.16.0 wrote `indicator="gray"`; the site's options were capitalised;
`doc.insert()` threw; defect 1 did the rest.

The fix is not a better guess — it is **not guessing**. `dashboard._select_value`
reads the options off the site and matches case-insensitively, returns them in
the site's own casing, and **drops the value entirely when nothing matches**: a
column with no colour is cosmetic, a board that does not exist is not. The rule
now generalises across the module — this app validates its OWN Selects against
its own JSON, and asks the site about everybody else's.

Two more belts on the same braces:

- **The columns are retried without themselves.** They are the only part of the
  document made of another app's Select values, so they are the only part a
  Frappe this app did not anticipate can refuse. A board with no columns still
  works — Frappe builds them from the distinct values of `state` on first view.
  Degrade; do not vanish. The retry runs inside a savepoint so a failed attempt
  cannot poison the migration's transaction.
- **The docname is forced.** `/app/farm-task/view/kanban/Farm Task Dispatch` is
  documented in three places, and a board Frappe autonamed something else is a
  board nobody finds. Where a version ignores the flag, the real name is reported
  rather than assumed.

### 3. The workspace was created empty

A second, independent bug from the same misunderstanding. In a modern Frappe a
Workspace renders **only what its `content` block list names**: the `shortcuts`,
`links`, `number_cards` and `charts` child tables supply the data, and `content`
decides what appears. v0.16.0 wrote the child rows and then set `content` to
`[]` — a page with a title and nothing else.

`/app/farm-task-dispatch` now carries:

- **a quick-add shortcut** (`Raise a Task`, a `doc_view: New` shortcut), the
  dispatch board, all tasks, assignments and the compliance calendar;
- **five Number Cards** — tasks in the pool, open Critical, awaiting review, and
  the raised-from-alerts / raised-by-hand **pair**. A Number Card counts one
  collection and cannot divide two, so the fraction of the board that came from
  the compliance calendar is shown as two counts side by side rather than as a
  percentage the card would have to invent;
- **two charts** — tasks by type and by urgency, both scoped to open work;
- **three link cards** — the compliance records a completion writes, the dispatch
  registers, and the camp.

Content and child rows are written in one pass, so a shortcut with no block (an
invisible row) or a block naming a row that is not there (a rendering error)
cannot drift apart.

**The upgrade path is handled.** A site that already took v0.16.0 has the blank
page, and a plain existence check would have skipped it forever — so a workspace
that exists AND is empty is now filled in, while one with anything on it is left
exactly as somebody arranged it. An empty page is not a choice; an arranged one
is.

### What let it through: the test double did not police Select options

`tests_standalone/harness.py` validated Links faithfully — that fidelity is why
v0.12.1 exists — and did not look at Selects at all. So `indicator="gray"` sailed
through 2864 tests and threw on a real bench.

`Document._validate_selects` now refuses a value a field does not offer, on the
parent and on child rows, exactly as Frappe does — empty values allowed, fields
with no options not policed. `TheIndicatorPaletteIsNotAssumed` re-declares the
field three incompatible ways (capitalised, lowercase, hex codes) and requires a
working board from all three; `MigrateSaysWhatItCouldNotBuild` captures stdout
and asserts a failed build is named on it.

**Full suite: 2888 pass, 0 fail** (24 new). No tool signature, doctype schema or
kill switch changed — this release is the installer and the harness only.

---

## 0.16.0 — 2026-07-31

**Sprint 8: the operational half of the compliance framework.** Twenty-three new
tools, six new DocTypes, two new alert rules and a Kanban board — and one
sentence that says why the release exists:

> Sprint 7 could tell an operation that fifty-four things were wrong. Nothing in
> it could send anybody to fix one.

That is not a missing feature; it is a missing half. A compliance calendar whose
alerts have no actionable path is a list somebody reads on a Tuesday and
transcribes onto a whiteboard, and by August the whiteboard and the calendar
disagree. v0.16.0 closes the loop: an alert becomes a dispatchable task, the task
carries the evidence its completion must produce, completing it writes the
compliance record, the record moves the operational register, and the alert
auto-dismisses on the next sweep because its condition is no longer true.

**Nothing in this release dismisses an alert.** That is the design, stated as a
prohibition because it is the thing that would be easiest to get wrong. The only
honest way an alert goes away is to change the world and let the sweep notice —
anything else is a system where the calendar and the camp disagree and the
calendar is the one that looks clean.

---

## Feature A — Farm Task Dispatch: FSM-style crew dispatch, compliance-native from day one

Three DocTypes — `Farm Task`, `Farm Task Assignment` and the `Farm Task
Evidence` child table — and eleven tools over them.

**`evidence_required` IS MANDATORY ON THE DOCTYPE, AND IT IS THE WHOLE DESIGN.**
A task cannot be created without stating, as JSON, what closing it obliges
somebody to produce: photographs, a signature, a statement of findings, a
witness. There is no path to a task somebody can close by saying they did it.
The controller refuses a blank contract, refuses one whose every requirement is
false, and refuses a key it does not recognise — because `{"photo": true}` asks
for nothing, refuses nothing, and looks exactly like a photograph requirement
right up until the audit.

`complete_farm_task` then refuses a submission that does not meet it, naming each
requirement that is short. **That refusal is the point of the whole doctype.**

**DUAL MODE, BECAUSE ONE MODE IS WRONG FOR HALF THE WORK.** A habitability walk
is general labour: anybody with camp-maintenance skills takes it from the pool,
and making a foreman assign fifty-four of them by hand is how fifty-four of them
do not happen. Fitting a CO detector, spraying under an applicator licence, or
anything where the named holder matters is *dispatched* — somebody is SENT, by
name, and the assignment records who sent them. `claim_farm_task` refuses a
Dispatched task outright: self-picking one would put the wrong person's name on a
regulated record.

**THE CONCURRENT-CLAIM LIMIT IS A HOARDING LIMIT, NOT A PRODUCTIVITY ONE.** Three
tasks at once per worker. Completing or rejecting one frees a slot in the same
instant, so it never stands between somebody and their next job — only between
them and their fourth simultaneous one. Without it one worker empties the pool
onto their own name and the board looks worked.

**REJECTION IS A FIRST-CLASS STATE WITH A MANDATORY REASON.** "Nobody got to it
and dispatch never followed up" is the answer nobody can defend. `reject_farm_task`
turns it into "the ladder is broken and I could not reach the detector", the task
goes back to the pool, and **the rejected assignment stays on the record** — it is
the proof somebody was sent, went, and could not do it, which answers an auditor
in a way an absence never does.

**AWAITING-REVIEW IS NOT A SECOND APPROVAL STEP.** A completion lands there when
the compliance record it produced found something. The work IS done and the
register IS updated — what needs a person is the finding, and the Critical alert
raised against the record is how they hear about it. A clean completion goes
straight to Completed, because routing clean work through a review queue is how a
review queue stops being read.

### Two decisions worth writing down

**`Farm Task Assignment` is a separate DocType, not a child table.** The Sprint 8
note left the choice open and asked for whichever shape survives the class of bug
v0.14.0 found in chunked uploads. It is the same arithmetic and it comes out the
same way — but the deciding factor is not the quadratic write. It is the query the
dispatch board runs constantly: *everything worker 42 is holding*. Against a child
table that is a scan of every Farm Task on the site, unnesting every history,
filtering in Python, because a child row's parent is the only indexed way in and
the worker is not the parent. Against a DocType it is one indexed read. The
concurrent-claim check asks that question on every claim, by every worker, all
morning. (`evidence_files` IS a child table, and that is consistent: written once
at completion, a handful of rows, only ever read with its parent.)

**The docname is `FT-YYYY-MM-<seq>`, not the task name.** The specification asked
for `task_name` as the docname and this is the one place the implementation
departs from it. A habitability walk on MC-Cabin-01 happens *every year*: a
docname built from the task's name collides with its own history the second time
it is raised, and fifty-four tasks generated from fifty-four alerts in one call
have to produce fifty-four distinct names with no human in the loop. So
`task_name` stays as the title a foreman reads on the board, and the key is a
sequence — carrying the month, like `Housing Assignment`, because farm work
arrives in the same fortnight every year.

### The dispatch board

`/app/farm-task/view/kanban/Farm Task Dispatch` — a Frappe **Kanban Board**,
built by the installer, with one column per state including Rejected. A foreman
drags a card and Frappe writes the field, on desktop and on a phone, with the
site's own permissions and theme. **There is no custom UI in this release and
none is needed.** A landing Workspace at `/app/farm-task-dispatch` is built
alongside it where the site has the doctype. `list_dispatch_board` returns the
same columns as JSON for a caller that cannot see a screen.

Built like the Compliance Command Center and for the same reason: an existing
board is left exactly as it is, including every column somebody has since
reordered or deleted. Not shipped as `fixtures`, which `test_hooks.py` forbids
by name.

---

## Feature B — Housing Inspection, Detector Test and Water Test

Three DocTypes and twelve tools. These are the records a task completion
produces, and each one is the evidence behind a specific obligation: OAR
437-004-1120 and 29 CFR 1910.142 for the habitability walk, ORS 479 and FSMA
Subpart L for the detectors, FSMA Subpart E for the water.

**THE WORKFLOW BRANCHES ON WHAT WAS FOUND, NOT ON WHO PRESSED WHAT.**

```
findings blank    →  Recorded
findings present  →  Corrective Action Required
```

A clean inspection is not something anybody should have to route or approve. It
happened, it was clean, the unit's inspection date moves forward, and the alert
that asked for it dismisses itself. The only records that need a human afterwards
are the ones that found something.

Deriving the state from the findings rather than from a transition somebody
chooses is what makes it honest: **a worker who has typed "water stain, north
wall, spreading" is not offered the option of marking the walk as passed**,
because the state is recomputed from the text on every save. `workflow_state` is
the framework's own field name, so a site that wants Frappe's native Workflow
layered on top attaches one and `advance_workflow` drives it — but the branch
ships working, because a branch that needs a Workflow record configured first is
a branch that is off on every site nobody configured.

**THE WRITE-BACK LIVES IN THE CONTROLLER, NOT IN THE TOOL.** A record typed into
the Desk by a camp manager who has never heard of MCP updates the register
exactly as one written through a tool does. A compliance system where the
evidence and the register agree only when the right door was used disagrees with
itself by August.

**IT ONLY EVER MOVES A DATE FORWARD.** March's walk entered in July is filed as
evidence and does not drag a register that already knows about June — that would
re-raise an alert about work which has since been done.

### The judgements inside each one

**A failed detector test still writes the date.** The stale-detector alert asks
one question — *does anybody know whether this works* — and a Fail answers it. The
answer is bad, so the record routes to Corrective Action Required and raises a
Critical alert of its own; but the ignorance is over, and leaving the date blank
would have the calendar saying "nobody has tested this" about a building somebody
tested this morning.

**"Not Present" writes no date**, for the mirror reason: there is nothing to have
tested, so nothing is known. It is also a finding in its own right — a building
somebody sleeps in with no CO detector is the most dangerous state this app
records.

**Replacement needed raises a Farm Task.** A checkbox with nobody dispatched
against it is a finding that survives until next year's test rediscovers it. This
is the one place a compliance record creates work rather than merely recording
it, and it is deliberate.

**A Water Test writes TWO registers.** The sample came out of an Irrigation Zone,
but `water_test_stale` reads the *block* — Subpart E is engaged by water
contacting a crop, and the crop is on the block. A test filed only against the
zone would leave the calendar calling ground untested whose water was tested last
week.

**AN UNREADABLE RESULT IS NOT A CLEAN RESULT.** A laboratory says the same thing
eight ways — "Absent", "<1 MPN/100mL", "0", "Present", "12", "Positive" — so
results are read by words first and numbers second, with generic E. coli compared
against the FSMA 112.44(b) criterion of 126 CFU/100 mL. Where neither reading
works, the record routes to Corrective Action Required and somebody has to go and
look at the report. Treating an uninterpretable result as a pass is how a
compliance file becomes a clean record of nothing.

### Two new alert rules — the calendar learns to fire on knowledge

`housing_corrective_action_open` and `water_test_contamination` bring the rule
set to eleven, and they are a different shape from the first nine. Rules 1–9 fire
on **ignorance**: nobody has walked this cabin, nobody has tested this water.
These two fire on **knowledge** — somebody went and looked and found something —
and they exist because Sprint 8 gave the operation a way to go and look.

Both close by being **superseded** rather than ticked: a cabin re-inspected with
nothing found, a water source re-sampled clean. The work that makes the finding
untrue is the work anybody would want done, so it is the work that silences the
alert. Closing the corrective action by hand also works, and needs a note saying
what was actually done.

---

## Feature C — `generate_tasks_from_compliance_alerts`, the bridge

One tool, and the reason the other twenty-two are worth having. It walks the open
Compliance Alerts, maps each to the *shape* of work it actually is, and raises a
Farm Task carrying the evidence its completion must produce.

The mapping is a table of judgements, and the two that matter are `dispatch` and
`evidence`. Self-pick for general labour; Dispatched wherever the named holder
matters. Urgency follows severity — Critical becomes **High**, Warning becomes
Normal, Info becomes Low — deliberately *not* the identity mapping, because a
board where everything is Critical is a board nobody reads.

**IDEMPOTENT BY CONSTRUCTION.** A task carries `source_alert`, so a second run
finds the task the first raised and skips the alert. Re-running after fixing half
the camp raises tasks only for the half still outstanding, which is the property
that makes it safe to run whenever somebody wonders. Two people are never sent to
walk the same cabin.

**An alert type with no recipe is reported by name rather than turned into a
generic task.** A task with a made-up evidence contract is worse than no task: it
produces a compliance record nobody can rely on.

**`dry_run` defaults FALSE**, unlike `dismiss_alert_bulk`, and the asymmetry is
deliberate. A mis-typed filter there *hides* non-compliance and leaves an
operation reading as clean while nothing was fixed. The failure mode here is too
many idempotent tasks on a board, none of which changes an operational record.
Gating the useful direction behind a second call would be safety theatre paid for
by the person trying to get work dispatched.

On a camp with twenty-seven cabins carrying stale detector tests and overdue
inspections, one call produces fifty-four dispatchable tasks and the dispatch
board fills.

---

## Also in this release

- **The compliance calendar is unchanged and untouched.** No tool in this release
  writes a Compliance Alert. Every dismissal in the loop happens because the
  nightly sweep found a condition no longer true.
- `install.py` gains a fifth idempotent job — the Kanban board and its workspace —
  and `before_uninstall` names all six new DocTypes among the records an operator
  would want back. `Farm Task Assignment` is called out specifically: the reason
  somebody could *not* do a job exists nowhere else on the site.
- The standalone harness learned two things a real bench already knew: one child
  table can have several parents (`Farm Task Evidence` has four), and
  `frappe.utils.time_diff_in_seconds` returns seconds between datetimes.

**Full suite: 2864 pass, 0 fail.** README and `docs/tool-catalog.md` updated for
v0.16.0. Version stamp bumped in `erpnext_mcp/__init__.py`.

---

## 0.15.0 — 2026-07-31

**Sprint 7: the compliance framework, and the cleanup of the attribution drift
v0.13.0 left behind.** Thirty-two new tools, seven new DocTypes, one new
scheduled job, and one deliberate exception to a promise this app has kept since
v0.1.0.

The organising idea is one sentence, and every design decision below answers to
it:

> **Compliance is a lens on operational data, not a duplicate set of records.**

Every spray IS an EPA and Worker Protection Standard record. Every hire IS an
I-9 record. Every bucket IS an FSMA traceability record. Compliance that lives
in its own module beside the operation is a shadow that drifts from reality the
first busy week of harvest — and an auditor who finds two records of one spray
that disagree has found something far worse than a missing field.

The test for whether a feature is woven in or bolted on, used throughout:

> Does removing it break **operations**, or only break **compliance reporting**?
> Breaks operations too → woven in correctly. Only breaks reporting → it is a
> shadow layer; refactor.

---

## Feature A — Wave 1: compliance metadata as Custom Fields on operational DocTypes

**This app now adds fields to three DocTypes it did not create, on purpose. It
is the only such exception, and it is the one thing in this release that needs
defending rather than describing.**

`hooks.py` has promised since v0.1.0 that installing erpnext_mcp adds no field
to any DocType it did not create — so an operator who removes it gets their site
back exactly as it was. v0.7.0's asset tooling keeps its cost split in an `Asset
Cost Profile` beside ERPNext's Asset for precisely that reason.

The alternative to breaking that promise is a "Spray Compliance Log" DocType
that somebody fills in *after* doing the spraying, and it fails the test above:
delete it and spraying carries on exactly as before. So the applicator's name,
the EPA registration number, the restricted-entry interval and the pre-harvest
interval go **on the spray record** — where the person doing the spraying
already is, and where leaving them blank stops the spray being recorded at all.

**Twenty-four fields across five DocTypes. Seven are required.**

| DocType | Owner | Fields |
| --- | --- | --- |
| Spray Log | farm_precision_ag | `applicator_name`\*, `epa_reg_number`\*, `rei_hours`\*, `phi_hours`\*, `weather_temp_f`, `weather_wind_mph`, `wind_direction`, `target_pest` |
| Employee | farm_hr / hrms | `i9_status`\*, `w4_status`\*, `jurisdiction`\*, `flc_license_status`, `flc_license_expiration` |
| Bucket Log Entry | the BucketLog bridge | `picker_id`, `crew_id`, `block_id`, `bin_id`, `shipment_id` |
| Housing Unit | erpnext_mcp — **verified, not added** | `fsma_worker_facility`, `last_habitability_inspection`, `smoke_detector_last_test`, `co_detector_last_test` |
| Field | erpnext_mcp — **verified, not added** | `food_safety_zone`, `last_spray_date` |

\* required.

`docs/compliance_fields.md` has every one with the framework that wants it, why
that framework wants it, and — the column that matters — what breaks in the
day-to-day WORK without it. `test_compliance_fields.py` requires that last
sentence to exist for every field, so a shadow field cannot be added without
somebody confronting the question, and it asserts the doc and the table cannot
drift apart in either direction.

Some of those answers, because they are the argument:

* `rei_hours` — THE crew-scheduling number. Without it nobody knows when the
  block can be picked, and the crew boss guesses. It is the field that makes the
  compliance record and the work order the same record.
* `i9_status` — whether this person may be put on a crew at all. Expired means
  they cannot lawfully work tomorrow, which is a rostering fact before it is a
  filing fact.
* `picker_id` — piecework pay. Every bucket is somebody's money, and an
  unattributed bucket is a payroll dispute at the end of the week.
* `co_detector_last_test` — somebody sleeps there tonight.

**How they are added.** Every field is a `Custom Field`, Frappe's supported way
for one app to extend another's. The target app's repository is untouched, and a
later farm_precision_ag that ships `epa_reg_number` itself finds this one
already there rather than ending up with two columns — the check is "is the
field present at all", not "is there a row we wrote".

**Graceful degradation.** A DocType that is not on this site is skipped BY NAME
with the app that would bring it. A site without farm_precision_ag is told so;
it is not a failure. Install the app, run the tool again.

**`verify` targets are not papered over.** Housing Unit and Field are this app's
own DocTypes and declare their compliance columns in their shipped JSON. A
missing one means the migration did not finish, and the installer REPORTS it and
adds nothing — a Custom Field over the top would leave the site with two columns
and no error, which is worse than the problem it hides.

**Idempotent, and asserted three times.** `MigrateThreeTimes` runs the whole
`after_migrate` hook three times and counts the Custom Field rows.

**The number worth reading is the backlog.** Frappe binds `reqd` on save, not
retroactively — so history stays readable and stops being re-saveable. The
installer counts the rows that would now fail, per field. That count is the
operation's compliance debt stated in rows, and it is the most useful thing
either the hook or the tool produces on a site with history.

**What it costs, said plainly.** Uninstalling this app drops those columns and
everything typed into them. `before_uninstall` now names every one before it
happens, with the `bench backup --only-doctype` lines to run first.

**New tools:** `install_compliance_fields` (mutating, **defaults ON**),
`get_compliance_field_map` (read).

`install_compliance_fields` is the only mutating tool in this app that ships
enabled, because a compliance field that arrives when an operator remembers to
tick a box is missing on the sites that needed it most. The exception is named
and argued for in `registry.DEFAULT_ON_MUTATING_TOOLS`, the settings form's
"write tools are live" banner skips it (a banner that fires every time is one
nobody reads), and a test asserts it is the ONLY exception. Turn the switch off
and no field is added, through the tool or through the hook.

---

## Feature B — Wave 2: the four external-evidence DocTypes

Compliance is a lens on operational data — but four kinds of evidence arrive
from OUTSIDE the operation and have no operational act to hang off. Nobody
writes a harvest hygiene SOP by harvesting. The certifier's certificate is
theirs. The agency's docket number is theirs. An auditor's findings are an
outside party's conclusions.

Four DocTypes, and the set is small because the test above is run in reverse: a
record that would be filled in AFTER an operational act, describing that act, is
a shadow record and belongs in the operational DocType.

### Compliance Policy — the SOP library

The version is a FIELD and not part of the name, so a policy at v3 is the same
record every audit finding already cites. `supersede_compliance_policy` writes
**both ends of the chain in one act**, because "which procedure was in force on
the day this happened" is asked from whichever end the auditor starts.

Refuses: a policy superseding itself; one already superseded (two successors
make "what was in force" unanswerable); a successor whose effective date
PREDATES the one it replaces, which would leave a period with two procedures in
force. A superseded policy is historical rather than wrong, and audit packets
covering the dates it governed still include it.

### Certification — certificates and licences

**The status is not derived from the dates, and that is deliberate.** A
controller that flipped `status` to Expired when a date passed would only run on
documents somebody saved — so the expired certificates would be exactly the ones
still reading Active, and a list filtered on status would show the lapsed ones as
current. A derived field that is only correct when touched is worse than none.
Every tool reads the DATE and says so.

`renew_certification` appends to a renewal history rather than editing the
expiration in place, and **reports any lapse rather than hiding it**: renewing
late does not close a gap that already happened, and that gap is exactly what an
auditor asks about. Editing the expiration forward through
`update_certification` is refused and points at the right tool.

`renewal_window_days` is a LEAD TIME, not a reminder preference — 90 days
because that is roughly what an Oregon farm labor contractor renewal takes once
the bond and background check are counted.

### Regulatory Filing — what went to an agency, and what came back

A filing nobody can prove was made is a filing that was not made; the agency's
position is that they have no record. So a filing marked Submitted with **no
submission date is refused** — a half-filled record would be assembled into an
audit packet and read as evidence of something that may not have happened. A
Draft with no dates is exactly what a filing being prepared looks like and is
allowed.

### Audit Event — audits, inspections, and whether the findings were closed

An operation is not judged on having no findings. Every audit produces some, and
a clean report usually means the auditor did not look hard. It is judged on
CLOSING them.

`close_audit_event` **refuses while any corrective action is open**, naming every
one — enforced in the controller as well as the tool, so there is no second door.
A closure date over an open finding is the most misleading thing this app could
record: `generate_audit_packet` reads it as "this audit is finished", would
assemble it into a packet, and the packet would be contradicted by the auditor's
first question. Closing an individual action requires saying what actually
changed; a tick in a box is what an auditor is trained to disbelieve, and it is
refused.

**Nineteen tools.** Eight reads (on by default), eleven writes (off).

---

## Feature C — Wave 3: the Kairotic Compliance Calendar

**Chronos serves Kairos, and this is where that stops being a slogan.**

The clock runs the sweep. The sweep decides nothing. Nine rules ask the same
question every night — *is this condition true right now* — and the answer is
read off the state of the world, never off the calendar:

> "It is the first of the month, so remind somebody about water testing" — fires
> on fallow ground, fires on ground tested last week, and is ignored by the third
> month because most of it is noise.
>
> "This block was sprayed eleven days ago and its agricultural water has not been
> tested in 118 days" — fires on exactly the blocks where FSMA Subpart E is
> engaged, on exactly the days it is engaged, and is worth reading every time.

**The nine rules, with the gate that makes each ripe:**

| Rule | Fires when | Silent when |
| --- | --- | --- |
| `certification_expiring` | inside the lead time the certificate's OWN issuing body takes; Critical inside 30 days | 200 days out; superseded; revoked |
| `policy_review_overdue` | a procedure IN FORCE is past the review date IT committed to | a draft; a superseded or retired version |
| `water_test_stale` | a block **in active spray rotation** has no test inside 90 days | fallow ground; a block nobody has sprayed this season; a current test |
| `housing_inspection_overdue` | a cabin somebody can be ASSIGNED to has no walk inside a year | a shower block; a unit already marked Uninhabitable |
| `housing_detector_test_stale` | a **FSMA worker facility** has an untested smoke or CO detector | a shed on the same parcel |
| `i9_expired` | an ACTIVE employee's I-9 has expired | Pending (inside the lawful 3-day window); a former employee |
| `flc_license_expiring` | a crew boss's licence is inside 90 days; Critical inside 30 | an employee with no licence |
| `filing_response_due` | a SUBMITTED filing has no response and the deadline is near | a draft; a filing that was answered |
| `audit_action_overdue` | an action is past the deadline the SCHEME set | an action with no due date; a closed audit |

`water_test_stale` is the clearest case and the one with the most tests. FSMA
Subpart E is engaged by water contacting a crop, and on a tree fruit block that
is mostly what goes through the sprayer — so an untested block nobody is
spraying is dormant rather than unsafe, and becomes Critical **the day it
re-enters rotation.** There is a test for the gate opening.

**Auto-dismissal is the other half, and it is the half people forget.** An alert
whose condition resolves is dismissed by the sweep with no human reason
attached. The water test was done; the licence was renewed; the cabin was
inspected. Nobody should have to remember to switch off a reminder about
something that already happened. If the condition comes BACK, the same alert is
reopened — because an alert is a statement about the present, not a task
somebody once closed. A dismissal a PERSON made is never reopened: they looked
and decided, and the sweep does not overrule them by noticing the same thing
again.

**The sweep is idempotent, and that is the whole design.** Each alert's docname
is derived from its rule and its source record and from NOTHING that changes
daily. A key carrying the due date would spawn a new alert every morning as a
certificate ticked from 60 days out to 59, each one discarding the snooze
somebody set on the last — silent and cumulative. `first_seen` is never moved
forward, so an alert open four months reads as four months old.

**Three different ways off the calendar, kept distinct:** auto-dismissal (the
sweep noticed the work was done), snooze (a DATE; the condition is still true
and it comes back on its own), and dismissal (a person decided, and the reason
is mandatory because it is the only part of the record nobody can reconstruct).

`dismiss_alert_bulk` **requires a dry run first.** The whole calendar is one
filter away: a `severity` typed where an `alert_type` was meant matches
everything, fails nothing, and looks exactly like success while leaving an
operation reading as compliant with nothing fixed.

**New DocType:** Compliance Alert (transient — the sweep rebuilds it).
**New scheduled job:** `erpnext_mcp.alerts.sweep`, daily, never raises, writes
only this app's own alert table.
**New tools:** `get_compliance_calendar`, `list_compliance_rules`,
`get_audit_readiness` (reads); `refresh_compliance_alerts`, `snooze_alert`,
`dismiss_alert`, `dismiss_alert_bulk` (writes, off).

---

## Feature D — Wave 4: the Audit Packet Generator and the Command Center

### `generate_audit_packet`

Eight regimes — FSMA, GAP, GlobalGAP, OSHA, DOL, EPA, USDA_NIFA and an unscoped
Other — assembled into a PDF and filed as a Governance Document in the company's
archive. Each is scoped to the evidence its regulator actually asks for: a DOL
packet has no business containing a GlobalGAP certificate, and including one
invites a question nobody wanted to answer.

**It pulls from the operational records, not from a copy.** The spray records
ARE the spray logs. The worker facility records ARE the housing register. The
traceability rows ARE the bucket log. Nothing in a packet is a compliance copy,
which is why nothing in one can have drifted from what was actually done.

**The kairotic gate is a REFUSAL, not a warning.** A packet asserts a compliant
period. It is refused on a period that has not finished, and on one whose
corrective actions are still OPEN — because an open finding inside the period
contradicts the assertion, and a warning at the top of a printed document is not
read by the person the document is handed to. Every open action is named in the
refusal. `allow_open_actions=true` produces it anyway, with the open items in a
section at the FRONT: an operation that must hand something over mid-remediation
is better served by disclosing the remediation than by having the auditor find
it.

**Empty sections say why they are empty.** An FSMA packet on a site with no
BucketLog bridge says the bridge is not installed and the traceability has to be
supplied separately. A silently omitted section reads as an operation with
nothing to declare.

Idempotent by (audit_type, company, period): a second call is refused without
`overwrite=true`. PDF by default, DOCX available — a .docx handed to somebody
who cannot open it is a document that did not arrive, which is why
`generate_quarterly_investment_report` made the same choice.

`stage_via_chunks` routes the assembled bytes through the v0.14.0 staging
pipeline for a checkpoint, and the tool is straight about when that matters: the
bytes never cross the MCP boundary, so it buys resumability on a large assembly
rather than transport. It defaults on above 2 MB and off below, where the
checkpoint costs more than the failure it guards against — and says so in the
result.

### The Compliance Command Center

A Frappe Dashboard at `/app/compliance-command-center`: six Number Cards
(Critical / Warning / Info alerts, overdue corrective actions, expiring
certificates, open audits) and four Charts (alerts by category, alerts raised
over time, the certificate expiration timeline, filings by agency).

**Built by an installer, NOT shipped as `fixtures`** — which `test_hooks.py`
forbids by name, and this is why. A fixture cannot look at what is already
there, so an operator who reordered their cards or deleted a chart would get it
silently put back on every migrate, forever. The installer checks before it
writes; a card somebody edited is left exactly as they left it. Three migrations
build it once, and there is a test.

`get_audit_readiness` computes the one number somebody acts on — resolved over
raised, as a percentage — because a count only means something to a person who
already knows what normal looks like, and a percentage is comparable to
yesterday's. It also reports how the score was EARNED: an operation at 95%
entirely through human dismissals is a different operation from one at 95%
because the work got done, and a score that could not tell them apart would be a
score worth gaming.

**New tools:** `generate_audit_packet` (write, off), `list_audit_packet_types`
(read).

---

## Feature E — Journal Entry attribution drift: find it, repair it, in bulk

### The damage class

A Journal Entry line carries `party_type` and `party`; so does every GL Entry row
it posted. The voucher is what the entry shows; the GL is what every ageing
report, party ledger and statement of account reads.

v0.13.0's `update_journal_entry_party` looked its GL rows up by
`voucher_detail_no == line.name` — the Sales Invoice Item convention, and NOT the
Journal Entry one. Every call against a submitted entry matched zero rows, wrote
the voucher, and returned a warning blaming the site. v0.14.0 fixed the matcher.
It did not fix the entries already damaged: a voucher saying one party, a ledger
saying another, and nothing in either table admitting to the disagreement.

### `find_drifted_je_attributions` (read, on by default)

Scans submitted entries in a date range and reports every line whose voucher and
ledger disagree, with both sides, the account, the amounts and the matched GL
row. Three queries whatever the range, matched by the same function the repair
writes through — so a line reported as drifted is one the repair can act on.

Lines whose GL rows cannot be identified with certainty (two lines of one
voucher posting the same amount to one account) are reported separately as
`ambiguous` and are NOT counted as drift: reporting a coin toss as a finding
would be worse than reporting nothing.

`by_vintage` groups on modification date against the window v0.13.0 was live,
and the window is an argument — a site that upgraded later ran the broken tool
for longer. The grouping is reported BESIDE the finding and never used to filter
it: drift from a restored backup or a direct database edit is just as real and
lands outside the window.

### `update_journal_entry_party` — the idempotence check now reads BOTH tables

v0.14.0 fixed the matcher and kept a check that read only the VOUCHER: if the
line already said what was asked for, it refused with "nothing to change". **On a
damaged line that is precisely wrong** — the voucher agreeing is the SIGNATURE of
the damage, so the one state the tool most needed to repair was the one it
declined to look at, while telling the caller everything was fine.

Nothing to change now means nothing to change ANYWHERE. A voucher that agrees
over a ledger that does not is a GL-only repair; it proceeds, and the result
reports `gl_only_update: true` so nobody mistakes it for a fresh attribution.
`force_gl_sync=true` writes the GL rows regardless, for an operator who wants the
write to be an explicit act rather than a consequence of a comparison.

### `repair_drifted_je_attributions` (write, off, dry run defaults TRUE)

Takes `find_drifted_je_attributions`' `repair_input` verbatim and brings each
drifted ledger row back into step with its voucher — the right direction for this
damage class by construction, since the broken tool wrote the voucher and failed
to write the ledger.

**Moves no balance, ever.** `party` is an attribution column: every debit,
credit, account and date is refused as an argument, so the trial balance after a
repair of two hundred lines is arithmetically identical to the one before it.
There is a test that adds up the ledger before and after. That property is what
makes a batch write to submitted vouchers defensible at all.

It does not abort on the first failure. Each item is a different voucher, and a
run that stopped half way would leave the ledger in a state neither the report
before it nor the report after it describes. Every item is attempted and every
outcome is reported.

`TheAccJv73Damage` reproduces the original incident — a $10 member distribution
against an Equity account, damaged exactly the way v0.13.0 damaged it — finds it,
repairs it, and rescans clean. It is also the regression guard: a matcher that
went back to v0.13.0's lookup would find nothing there and report a clean ledger.

---

## Tests

**2719 pass, 0 fail** (2424 before this release). New modules:

* `test_compliance_fields.py` — the table, the installer, three migrations,
  `WovenNotShadow`, and the doc-cannot-drift check
* `test_evidence.py` — the four DocTypes, and mostly what they refuse
* `test_alerts.py` — every rule gets fires-when-ripe, silent-when-unripe and
  auto-dismisses-when-resolved; plus idempotence over three sweeps
* `test_audit_packets.py` — every audit type round-trips AND its PDF renders;
  the kairotic gate; the Command Center over three migrations
* `test_je_drift.py` — including `TheAccJv73Damage`

`test_hooks.py` from v0.14.1 gained the second scheduled job and asserts the
list exactly, so a third has to be argued for.

## Housekeeping

* `README.md` and `docs/tool-catalog.md` updated for v0.15.0.
* `docs/compliance_fields.md` is new.
* `erpnext_mcp/__init__.py` `__version__` is `0.15.0`.
* The fixture site now seeds an `Administrator` User. Every Frappe site has one
  and this app writes it into Link-to-User columns; without the row the double
  was refusing something the real framework accepts.

## 0.14.1 — 2026-07-31

**Hotfix. v0.14.0's Jinja hook was malformed and took every page render on a
live site down, including the error page.** Upgrade immediately; there is no
workaround short of uninstalling the app.

### What broke

v0.14.0's Feature C declared its amount-in-words helper as

```python
jinja = {"methods": ["erpnext_mcp_amount_in_words:erpnext_mcp.render.checks.amount_in_words"]}
jenv = jinja
```

That `"<name>:<path>"` form belongs to Frappe's **older `jenv` hook**, whose
reader splits on the colon before resolving. The modern **`jinja` hook does
not**: it hands each entry straight to `frappe.get_attr` and takes the Jinja
global's name from the callable's own `__name__`. So `get_attr` received the
whole string, took everything before the first dot as an app name, and threw:

```
AppNotInstalledError: App erpnext_mcp_amount_in_words:erpnext_mcp is not installed
  File ".../frappe/utils/jinja.py", line 206, in get_jinja_hooks
  File ".../frappe/utils/jinja.py", line 192, in get_obj_dict_from_paths
  File ".../frappe/__init__.py", line 1748, in get_attr
```

**Frappe builds the Jinja environment to render the error page too**, so the
exception was raised inside the handler for its own exception. Every request
returned 500 — including the page that would have said why. The MCP endpoint
itself was largely unaffected (it returns JSON and renders no template), which
is how the site could be diagnosed at all.

Two mistakes, not one. The syntax was `jenv`'s under `jinja`'s key; and `jenv`
was declared as a bare alias of the same dict, so one wrong string was
registered under two hook names with two different grammars.

### The fix

```python
jinja = {"methods": ["erpnext_mcp.render.checks.erpnext_mcp_amount_in_words"]}
```

A bare dotted path, which is what the `jinja` hook has always taken. `jenv` is
gone: it is the deprecated spelling, the `jinja` hook has existed since v14 and
v14 is this app's compatibility floor, so a second declaration bought nothing
and doubled the surface for exactly this class of mistake.

Because the hook no longer names the Jinja global, the **function** does.
`erpnext_mcp.render.checks.erpnext_mcp_amount_in_words` is a one-line wrapper
around `amount_in_words` whose only job is to carry a namespaced `__name__` — a
Jinja global lands in a namespace shared with Frappe, ERPNext and every other
installed app, and that namespacing had been the hook string's job until it
stopped being.

Nothing else changed. The check Print Format is unaltered and already guarded
with `{% if erpnext_mcp_amount_in_words is defined %}`, falling back to
`frappe.utils.money_in_words` — which is why a check would still have printed,
wordier, on a site that had somehow got past the crash.

### `test_hooks.py` — the test that did not exist

The real defect is that a 2400-test suite had never read `hooks.py`. A hook is a
string this app never executes itself: nothing imports it, nothing calls it, and
every existing test exercises the functions it names *directly*. So a hook can
name a missing module, a renamed function, or a real function in a syntax the
reader does not speak, and the suite stays green until `bench migrate` on
somebody's site.

The new module resolves **every** dotted path in `hooks.py` the way Frappe
resolves it — reproducing `get_attr`'s app-name rule, which is the specific line
that threw, rather than skipping to `importlib` and proving nothing. It also:

- refuses a colon in any hook path, which is the shipped bug asserted directly;
- refuses `jenv`, `doc_events`, `override_whitelisted_methods`,
  `permission_query_conditions`, `has_permission`, `fixtures`,
  `override_doctype_class` and `doctype_js` by name, since the README and the
  module docstring both promise this app installs none of them;
- **fails on any hook key it does not already know about**, so a future hook
  cannot arrive without somebody stating its shape and therefore how it is
  validated;
- checks that the name the hook actually registers is the name the check
  template actually calls, which nothing else made true;
- resolves the `scheduler_events` daily sweep and the install/migrate/uninstall
  hooks, none of which had ever been resolved by a test either.

Verified against the defect: reverting `hooks.py` to the v0.14.0 string fails
eight tests in this module, including by name the app Frappe could not find.

Every other v0.14.0 hook was audited and is correct.
`scheduler_events["daily"] = ["erpnext_mcp.tools.uploads.collect_expired_sessions"]`
is a bare dotted path, which is that hook's format, and it resolves.

Full suite: 2424 pass, 0 fail.

## 0.14.0 — 2026-07-31

The Sprint 6 tail, closed. Five features, one release, every one of them
grounded in something that actually went wrong between 2026-07-25 and
2026-07-30 rather than in a list of things that would be nice to have.

Two new doctypes, eight new tools, one bug fix that matters more than any of
them, and a test double that has been made to stop agreeing with code that could
not work.

### `stage_file_chunk` / `commit_staged_file` — moving a file bigger than a tool call

**The bottleneck was never the 8 MB ceiling.** `attach_file_to_document` has
always accepted eight megabytes of base64 in a single call, and no caller has
ever reached it. The real constraint is that an AI operator has to *compose* the
argument, and a base64 string lives inside the tool call it is writing — which
runs out around two hundred kilobytes. The tool advertised 8 MB and could be
handed 200 KB.

So every file-bearing operation through Sprint 5 and Sprint 6 collapsed into the
same four manual steps: write a Python script, `scp` it to the box, `docker cp`
it into the container, `docker exec` it. Per-parcel appraisal PDFs, eight of
them. The 5.8 MB master appraisal. The same master appraisal again, three times,
once per company after the conveyance. Backfilling suppliers. Every one of them
interrupted the work it was part of. Tim, 2026-07-30, in one sentence: *"So we
don't have to run these scripts."*

**`stage_file_chunk`** takes one piece at a time and writes it to a table.
**`commit_staged_file`** reassembles the pieces, verifies them against a SHA-256
the caller computed before sending anything, and turns them into a File —
attached to a document, filed as a new Governance Document, or standing alone.
**`cancel_staged_upload`** throws a dead upload away. **`list_staged_uploads`**
(read-only, on by default) reports what is in flight and, more usefully, *which
indexes are missing*, as compact ranges — `3-6, 9` rather than three hundred
numbers.

**The pieces are rows in a table and not entries in the cache, and that is the
whole design.** A 5 MB upload is a hundred round trips over some minutes. In that
window a `bench restart`, a worker recycle or a redis eviction under memory
pressure would throw the lot away, and the caller would find out at commit having
spent the entire upload. Rows survive all of it. "Stage three pieces, restart the
workers, stage two more, commit" is a test, and it genuinely reloads the module
and rebinds the catalogue rather than asserting that no state exists.

**`Staged File Chunk` is NOT a child table, and the reason is arithmetic.** The
obvious shape for "many pieces belonging to one upload" is a child table on the
session — it is what the specification asked for — and it does not work at the
far end of a big upload. Frappe rewrites a document's entire child table on every
save, so appending piece 600 means writing 600 rows of 200 KB to record 200 KB of
new data, and doing that per piece makes a large upload quadratic in its own
size. It would have passed the 25-chunk test and fallen over on the real 5 MB PDF
it was built for. A separate doctype with a Link back at the session costs one
row per piece, one write per call, and lets the missing-piece query count and sum
without ever loading a payload into memory.

**Cut the bytes, then encode.** Each `chunk_base64` is the base64 of *its own
slice* of the file's bytes. Base64-ing the whole file and then cutting the
resulting string up produces middle pieces that are not valid base64 on their
own, cannot be checked when they arrive, and whose per-piece hashes mean nothing.
That is the one thing a caller can get wrong, so the refusal names it
specifically — a caller who has done it will otherwise go looking for corruption
in a file that is fine.

**Nothing is deleted until the File exists.** Every commit refusal — a gap, a
hash that does not match, a size that does not match, a cancelled parent, a
filename the document already has, a cross-company attach — leaves the staged
pieces exactly where they were. A refusal is fixed by changing the argument,
never by re-sending the file. The target document is validated *before* a byte is
reassembled, so a bad argument costs nothing rather than stalling a worker
through ninety megabytes first.

**Every piece carries the hash of its own bytes.** Not for security; the
transport already had a bearer token. For diagnosis. A file that fails its
aggregate check is a mystery; a file that fails its aggregate check *and* whose
piece 17 hashes differently from what the caller recorded is a fixed piece 17.

**A session belongs to whoever staged its first piece**, and only they may add
to it, commit it or cancel it. Not paranoia about other operators: two callers
who happened to pick the same session id would otherwise interleave their pieces
into one file, and the failure would present as corruption rather than as the
collision it is.

**Staging cleans up after itself twice.** A session is deleted on commit and on
cancel; sessions idle for 24 hours are swept by a daily scheduler job *and* at
the top of every `stage_file_chunk` call. The second is the kairotic one — the
right moment to clear out abandoned uploads is when somebody is uploading, not at
three in the morning — and it is what keeps a bench with its scheduler switched
off from quietly accumulating ninety megabytes of a PDF nobody finished sending.

Ceilings: 200 KB of base64 per call (because that is roughly where a model stops
being able to compose the argument), 600 pieces, 100 MB assembled.

Tests: a five-megabyte round trip in thirty-five calls compared **byte for byte**
against the original; a skipped chunk refused by index range; a wrong SHA-256
refused with the per-piece hashes pointed at; a wrong size refused; session
isolation in all three directions; worker-restart resilience; cancellation;
the governance-document flow including supersession; the audit log eliding the
payload rather than storing a second copy of every piece.

### `bulk_wire_default_accounts` — company setup that finds the accounts itself

Running `set_company_defaults` against four freshly-created companies on
2026-07-30 came back "idempotent" for receivable, payable, round-off and
write-off — the four `create_company` already does — and said nothing at all
about cash, bank, income and expense, because nobody had passed them. A company
with no `default_income_account` does not fail loudly. It fails weeks later, the
first time somebody saves an invoice line with no account on it, nowhere near the
setup that caused it.

This finds them. In order: the caller's `overrides`; then the well-known account
number for the chart template (1310 receivable, 2110 payable, 1140 cash, 1110
bank — descending into the sub-ledger when the number names a group, as ERPNext's
1110 "Bank Accounts" does, 4100 income, 5100 expense, 5212 round off, 5218 write
off); then an account whose `account_type` means the right thing; then an account
whose *name* says so; then, only where the field permits an untyped account, the
first leaf of the right root type.

**Every candidate has to pass the same type checks `set_company_defaults` applies
to a hand-written value.** The search proposes and those rules dispose. A 1310
that exists and is a plain Asset rather than a Receivable is not used — ERPNext
keys party ledgers off `account_type`, so a `default_receivable_account` pointed
at the wrong kind of account posts fine and stops ageing correctly a quarter
later. That is the test that matters most in the file.

**It never fills a field with something merely plausible, and it never sulks.**
A field nothing matched is reported in `unresolved` with what was looked for and
how to fix it, and every other field is still wired: a company with nine of ten
defaults set is better off than one with none, and a chart with no Cost of Goods
Sold account is ordinary rather than broken. `strict=true` refuses the whole call
instead. An `overrides` value that cannot be resolved is always a hard refusal —
an explicit instruction that cannot be honoured is a different thing from a search
that came up empty.

`exchange_gain_loss_account` is deliberately **not** in the table. Its only
constraint is a root type of Income or Expense, so the only way to "find" it is
to take the first expense leaf, which is exactly the plausible-looking guess this
tool exists not to make. A field with no honest search stays
`set_company_defaults`' job.

Deterministic: where two accounts of the same type exist, the lower account
number wins every time, so "idempotent" is true on the second run rather than a
claim that happens to hold.

### `create_check_print_format` — cutting a printed check

Sorren's monthly invoice, the utilities and the occasional vendor who does not
take an ACH get paid by check, and until now they got paid by somebody writing
one out by hand and keying it into the ledger afterwards. The ledger is the thing
that ends up wrong.

Payment Entry *is* ERPNext's check-cutting document — party, amount, bank
account, reference number, the invoices being settled, and it posts the ledger
side itself. What it has no opinion about is where any of that lands on a piece
of paper. ERPNext ships no Print Format that fits US laser check stock.

This writes one: **8.5 × 11, three 3.5-inch panels** — check on top, remittance
stub in the middle, remittance stub at the bottom, which is the Deluxe form
1000/9000 layout and the Costco and Intuit equivalents of it. Date, payee, amount
in figures in a box, amount in words, memo, signature line; both stubs carry the
invoice-by-invoice detail that answers "what was this for".

**The amount in words is ours and not Frappe's.** `frappe.utils.money_in_words`
appends the currency name, varies with the site's number format, and on an
Indian-format site groups in lakhs. A check that says "Dollars" where the stock
already says DOLLARS is one a teller queries, and one that reads "Twelve Lakh" is
one a US bank will not take. `erpnext_mcp.render.checks.amount_in_words` writes
`One Thousand Two Hundred Thirty-Four and 56/100` — no currency word, no "Only",
a hyphen inside the compound tens, cents as a two-digit numerator over 100
including `00/100` on a whole amount, because a words line that stops at the
dollars is a line somebody can add to. It reaches the template through a
namespaced Jinja method — **declared wrongly; see 0.14.1 above, which is the
release that fixed it.** The template falls back to Frappe's own if the method is
not registered, because a valid check with wordier text beats a blank line, and
that fallback is the only reason a check still printed at all.

**MICR is not rendered and should not be.** The routing and account numbers are
printed in magnetic ink on the stock you buy, by the people who sold it to you,
against your account. The README's new **Cutting a check** section has the stock
to order, the paper weight, the envelope caveat, the bank's MICR spec sheet, and
the advice to order 250 rather than 2000 and hold one over a real check at a
window before committing.

**The template is a constant and the Print Format is a per-company record.** A
Print Format shipped as an app fixture would be one record with one name on every
site that installs this, and its `standard = "Yes"` would mean an operator's
margin tuning is overwritten on the next `bench migrate`. So the tool writes a
CUSTOM format named after the company's abbreviation, and refuses to overwrite a
STANDARD one — anything written into one of those disappears at the next upgrade
without a word.

The format is not inspected for substrings in the tests; it is **rendered**,
through Jinja, against a real Payment Entry with real references, with
`StrictUndefined` on so a field nobody has raises in the suite rather than at the
moment somebody presses Print.

### `regenerate_governance_document_pdf` — a fixed copy beside the editable one

Several archive entries landed as `.docx` only — the Q3 25 and Q1/Q2 26
quarterlies, the 2025 annual. A `.docx` is an editing format: it renders
differently in different applications, some refuse to open it at all (Tim's Pages
did), and "the copy on file" stops being one thing the moment two people open it
in two programs. A governance document's primary format is a PDF; the `.docx` is
the version somebody amends.

This converts one and attaches the PDF beside it, then repoints `attached_file`
so a reader following the archive lands on something that opens. **The `.docx` is
kept.** An archive that threw away the editable original to gain a fixed one
would have traded a problem for a worse one.

It needs LibreOffice headless in the container, and says so. Converting a `.docx`
means a layout engine — a `.docx` encodes styles, numbering, tables, section
breaks and fonts, and reimplementing enough of that to lay it out on a page is
not a few hundred lines of `zipfile`, which is why everything else under
`render/` is standard-library and this is not. A host without a converter is
refused **before anything is read**, naming the package to install, and nothing
is installed at runtime: a tool that fetched a package mid-request would hang a
worker and leave the container different from its image.

LibreOffice is tried before `docx2pdf`, which is the opposite of the obvious
order and the right one: `docx2pdf` drives Microsoft Word through COM or
AppleScript, so on the Linux container this app actually runs in it does nothing
at all. Every invocation points `-env:UserInstallation` at a profile directory
inside the temp directory it just created, because `soffice` writes a profile on
first run and fails obscurely where HOME is not writable — the same lesson as "a
script that runs outside the bench must make its own log directories before it
connects".

Refuses an entry with no `.docx`; an entry with *several* unless
`source_docx_file` names one (an original and an amendment filed together is a
real thing, and being right half the time is worse than asking); a source that is
not attached here or is not a `.docx`; and an entry that already has a PDF unless
`overwrite=true` — which then names the File it deleted, because removing an
attachment from a governance archive is not something to do quietly.

### `investigate_je_gl_link` — and the v0.13.0 bug it found

Sprint 6 verification ran `update_journal_entry_party` against
ACC-JV-2026-00073, a $10 member distribution against an Equity account, and got
`gl_entries_matched: 0`. Three explanations were live: an Equity-account quirk, a
Bank Bridge JE-crafting bug, or ordinary ERPNext behaviour.

**It was ordinary ERPNext behaviour, and it was a real bug in v0.13.0.**

`GL Entry.voucher_detail_no` holds the child-row docname for Sales Invoice Item,
Purchase Invoice Item and the other line-item doctypes. It does not for a Journal
Entry: `JournalEntry.get_gl_entries` fills that column from the line's
**`reference_detail_no`**, a pointer at a payment schedule row on an invoice being
settled, which is empty on every ordinary line. So v0.13.0's lookup — keyed on
`voucher_detail_no == line.name` — matched **nothing**, on every submitted entry,
for every account type. The tool updated the voucher, silently failed to update
the ledger, and returned a warning suggesting the site was unusual. The site was
not unusual. This was.

**Fixed.** GL rows are now matched the way the ledger actually identifies a line
— account plus debit plus credit, preferring `voucher_detail_no` where a site
does carry one — and the write is **refused before anything happens** when the
match is not certain: two lines of one voucher with the same account and amounts
are indistinguishable in the ledger; `merge_similar_entries` collapses lines
sharing an account, party and cost center into one summed row, so writing a party
onto it would attribute somebody else's money to this party; and a line that
posted no GL row at all is reported rather than shrugged at.
`allow_unmatched_gl=true` goes ahead anyway, and the result leads with the
disagreement — a refusal a caller cannot get past is how a safety gate becomes
the failure.

**If you ran v0.13.0's party tool against a submitted entry, the ledger still
says what it said before.** `investigate_je_gl_link` shows which entries are in
that state: one read-only call returning every line beside every GL row it
posted, with `account_type` and `root_type`, the party on both sides, which lines
disagree with the ledger, which GL rows no single line explains, and a `finding`
that says in one paragraph what the counts mean. It works on drafts and on
cancelled entries and says which case it is looking at.

**Why the standalone suite did not catch it.** The fixture seeded GL rows by hand
with `voucher_detail_no = <the line's docname>` — which is what anybody would
write, and what the code believed. A double built from the same wrong belief as
the code cannot contradict it. `harness.post_journal_entry_gl` now models what
ERPNext actually writes, including `merge_similar_entries`, so a two-line entry
posting twice to the same account produces one merged GL row rather than two. It
is the fifth time in this project's history that a permissive double certified
code that could not run; the module docstring says so.

The harness also grew `add_to_date` and a total sort key: `_sorted` used to spell
its column read `row.get(column) or ""`, which turns a legitimate zero into a
string and then compares it against the integers beside it —
`TypeError: '<' not supported between instances of 'int' and 'str'` on any query
ordered by a column counting from 0. MariaDB has no such problem, so that was the
double refusing a query a real site answers, which is the mirror image of the
usual failure and just as capable of blocking working code.

### Also

- **`files.check_attachable` / `files.insert_attachment` / `files.read_file_bytes`
  are now public**, and `attach_file_to_document`, `attach_governance_document`
  and `commit_staged_file` all go through them. Three copies of "may this file
  hang off this document" would have been three places to forget a rule.
- **`governance.file_governance_document` takes bytes rather than base64**, so a
  chunked upload can file an archive entry without re-encoding ninety megabytes
  to have `decode_base64_content` refuse it against a ceiling that describes what
  fits in one JSON call — a limit that is simply not a fact about a chunked
  upload.
- **Two hooks, both additive and namespaced.** One daily scheduler job that
  deletes this app's own expired staging rows, and one Jinja method
  (`erpnext_mcp_amount_in_words`). `hooks.py`'s docstring, which used to say "no
  scheduler jobs", says what is true now.
- **CI installs `jinja2`** alongside `werkzeug`, for the same reason: neither is
  a declared dependency and both arrive with Frappe. The check-rendering tests
  skip themselves where it is absent, so a bare environment still passes;
  installing it is what stops that skip being permanent.

**135 tools** — 61 read-only, 74 mutating. Full suite: 2407 pass, 0 fail.

## 0.13.0 — 2026-07-31

A cleanup wave out of real verification friction on 2026-07-30. Four features,
one release, two new tools and no data migration.

Everything here came out of the same afternoon: eight parcels seeded under the
only company that existed at the time, a payment nobody could attribute to the
right son without opening the Desk, and a family register that could say what
somebody was but not whose.

### `convey_parcel` — ground moving between two entities' books

`update_parcel` has always refused to move a parcel between entities, and that
refusal is right: ground changing hands has a date, an instrument behind it and
consequences for two sets of books, and a tool that let it happen by changing a
field would record none of them. This is the door that refusal points at.

**It deletes and recreates, which is the honest shape.** A Parcel's docname
encodes its entity — `Mill Creek - OML` on one set of books and
`Mill Creek - HLD` on the other, the same way every Account docname carries a
company abbreviation. There is no field to change that makes the move true. So:
create the new record, repoint everything at it, move the attachments, delete the
old one, write the event.

**The parcel's own short key is preserved, which is why the farm registers
survive.** Every Field, Irrigation Zone and Housing Unit is named
`<its name> - <PARCEL abbr>` — the parcel's key, not the company's — so all 29 of
a camp's cabins keep the docnames they have always had and only their `parcel`
link moves. A target entity already using that key is refused rather than
disambiguated, because a silently changed key would file the parcel's future
blocks under a different suffix from its existing ones.

Five registers are repointed: Lease, Field, Irrigation Zone, Housing Unit and
Housing Assignment. `owning_entity` moves too on the three that describe the
*ground*; a **Lease's** does not, because a conveyance does not change who signed
a contract — that is a novation, and it is its own document. The list is declared
in `realestate.PARCEL_REFERRERS` and a test checks it against the shipped DocType
JSON, so a register added in a later release cannot be forgotten quietly. (And if
one ever were, Frappe would refuse the delete rather than leave an orphan, which
is the safe direction.)

Every `File` attached to the old docname is rewritten to the new one. A File
points at its parent by `attached_to_name`, which is a docname and not a link, so
a conveyance that did not rewrite those would leave the tax statements and the
survey attached to a string nothing resolves, with no error anywhere to say so.

**It writes no Journal Entry, deliberately** — the same discipline as
`close_note_payable`. Basis transfer and any gain or loss recognised are entries
with real tax consequences that somebody should write on purpose, with a
narrative of their own, not produce as a side effect of filing a deed. The result
names the entries still owed.

**The trail lives on the survivor.** A new child table, `Parcel Conveyance
Event`, hangs off Parcel: it names the entity the ground came from and the
docname it had there, the date, the narrative, what moved and whether the
appraisal came with it. After a conveyance there is exactly one document left to
carry the history, and it is the new one.

Refusals, each because it is a different document's job:

- **An active, unterminated lease whose term covers the conveyance date**, named.
  A lease with no expiration date counts as running — reading a missing end date
  as "already over" is the one wrong answer that fails silently.
- **A linked Fixed Asset.** That is the balance-sheet side and it moves by
  posting, not by filing.
- **A target company with no chart of accounts, or no cost centers.**
- **A parcel name, assessor id or abbreviation the target already uses.**
- **More referring records than the per-register ceiling.** No silent caps: a
  half-conveyed parcel is worse than an unconveyed one.

**Every refusal comes back at once**, not one per round trip — a conveyance that
failed on the lease, was fixed, and then failed on the asset is two round trips
to learn two things that were both true from the start. `dry_run: true` returns
the whole plan and the whole refusal list without touching anything.

The appraisal report does **not** follow if it is filed in the old entity's
archive; a Governance Document belongs to a company. That comes back as
`appraisal_document_status: "unlinked_needs_reattach"` in the result and in the
conveyance event, never as a silent null, because "the appraisal needs re-filing"
is real work and a quiet null is how it gets forgotten. The appraised value and
its as-of date do come across — they are facts about the ground.

Atomicity was already structural: `dispatch` rolls back before it logs. v0.13.0
adds the test that proves it for this tool specifically — and, more usefully,
**taught the standalone test double to model a rollback properly**. It used to
discard rows inserted since the last commit and nothing else, so a tool that
repointed a dozen leases and then died looked atomic when only a real MariaDB
transaction was making it so. The double now keeps before-images of every row it
changes or deletes and restores them, which is the fourth time in this project's
history a permissive double has been caught certifying something it could not
see.

Kill switch: `allow_convey_parcel`, OFF.

### `update_journal_entry_party` — attribution on a submitted entry

A payment leaves a shared account and only afterwards does anybody establish
which of two sons it was for. The posting is right — right account, right amount,
right date — and one attribution column is empty or wrong. Until now that meant
cancel-and-repost, which replaces a clerical correction with a cancelled voucher,
a reversing pair and a new number no statement reconciles against; or the Desk,
which is what an MCP server exists so nobody has to open.

One line, two columns, a mandatory reason.

**It cannot move a balance.** Account, debit, credit, date, cost center and
remark are not arguments to it. The trial balance after the call is
arithmetically identical to the one before, which is what makes editing a
submitted document defensible at all: this is attribution, not restatement.

**It writes in both places the party lives.** `tabJournal Entry Account` is what
the voucher shows; `tabGL Entry` is what every ageing report, party ledger and
statement of account reads. Updating one and not the other leaves the voucher and
the reports disagreeing with nothing to say which is right — worse than not
having edited at all. The GL rows are matched on `voucher_detail_no`, the line's
own docname, so an entry with two lines to the same account for the same amount
stays distinguishable, and the result reports how many rows moved. A **draft** is
saved through the document instead, since it has written no GL Entries and full
validation can still run.

This is the one field-level exception to "every write goes through the document"
in `tools/mutate.py`, and the module docstring now says so and fences it: still
the ORM's db layer rather than raw SQL, still incapable of touching an amount,
and there is no supported alternative — ERPNext marks `party` as not allowed on
submit. The rule that stands is the one that matters: no tool here writes an
*amount* to a GL Entry.

The reason is written twice — to the entry's own comment thread, where an
accountant with the voucher open will see it, and to the MCP Action Log.

Refuses a cancelled entry (evidence with a hole in it); a line index outside the
entry; the rounding or write-off line ERPNext wrote itself; a bank or cash line,
where a party would make an ageing report claim somebody owes the account
balance; a party type this site has not registered; a party that is not a record
in the register its type names; and a change that changes nothing. An account
whose type does not normally carry a party is refused unless
`allow_non_party_account=true` says it was meant — the refusal exists to catch a
mistake, not to become one, so it names the way past. Bank, cash and round-off
lines have no way past, on purpose.

`dry_run: true` reports the plan, including which GL rows would move.

A Family attribution stays excluded from `generate_1099_prefill`, with a test
saying so. Attributing a transfer correctly does not make it reportable.

Kill switch: `allow_update_journal_entry_party`, OFF.

### The family register learned Son, Daughter, and "of whom"

`Family.relationship` gained **Son** and **Daughter**, *beside* Child rather than
instead of it. Records already saying Child are still true, and a register that
forced a re-pick would be asking somebody to restate a fact that has not changed.
No migration, no backfill, nothing rewritten.

The bigger gap was that "Alexander Polehn — Child" did not say **whose** child,
which is ambiguous the moment an entity has two members — and Orchard Meadow has
two. **`related_to`** holds the other person's name.

It is a `Data` field rather than a `Link`, and that is the design rather than a
shortcut: a Frappe `Link` points at exactly one doctype, a `Dynamic Link` needs a
discriminator column beside it, and the answer here is a Family record *or* a
Related Party record *or* somebody in neither register. So the field holds a name
and the tools resolve it on read, reporting `related_to_doctype` as `Family`,
`Related Party` or `None`. A name in neither register is not an error — a
grandmother who has never received a transfer and holds no role is exactly who
the free-text fallback is for, and the result says out loud that it is being kept
as text rather than leaving it looking linked.

- `create_family_member` and `update_family_member` take it; the docstrings carry
  the simple case (one pointer), the complex case (one pointer plus prose in
  `notes`), and the line where genealogy stops being this register's job.
- `list_family_members` surfaces it on every row, adds `described_as`
  — `"Alexander Polehn — Son of Tim Polehn"` — and filters by it, so "everybody's
  children" is one call.
- `get_family_member` walks the chain: `related_to` upward through the family as
  far as it goes, then **once**, at the top, across `related_party` into the
  register that holds roles and entities. That is how
  `Alex → Son of Tim → Manager of Orchard Meadow, LLC` gets assembled out of
  Family → Related Party → Company, which no single record holds. It terminates
  on a cycle, a depth limit or free text, and says which.

The two edges are deliberately distinguished: `related_to` goes to another
*person*, `related_party` goes to the *same* person in another register. Treating
the second like the first produces "Tim → Parent of Tim", which is how the
distinction earned a field.

**Nothing was backfilled and nothing will be.** Which of two members somebody is
the child of is a fact only the family has, and a migration that guessed would
produce a register that looks complete and is wrong. Records written before this
release load with `related_to` empty — there is a test asserting exactly that —
and `list_family_members` names them under `without_related_to` and warns. That
is the work list, not an error.

Somebody related to themselves is refused, in the tool and in the controller,
because a cycle of length one would have to be special-cased everywhere else.

### Also

- The standalone test double now names child rows on save (Frappe does, and a GL
  Entry's `voucher_detail_no` *is* a Journal Entry Account row's name),
  supports `frappe.db.set_value` against a child doctype, returns the Comment
  document from `add_comment` as Frappe does, and models rollback properly as
  described above. Four fidelity gaps, all of which would have let something
  untrue pass.
- `Parcel.abbr` is now returned by `get_parcel` and `list_parcels`. It is the key
  every Field, Irrigation Zone and Housing Unit docname is suffixed with, and it
  was previously readable only in the Desk.
- The uninstall warning for `Parcel` now mentions the conveyance history it would
  take with it.

**Tools: 127** — 59 read, 68 mutating.

## 0.12.2 — 2026-07-30

Two Sprint 6 gaps closed. Four new tools, no new doctypes, no migration.

### `create_company` was already there — the switch was off

Worth stating plainly, because it cost somebody an afternoon: `create_company`
shipped in v0.12.0 and is in the catalogue. It was absent from a live
`tools/list` because **it is a mutating tool and mutating tools ship OFF**, which
is the entire point of them. `update_company` appeared in the same inventory
because its switch had been ticked and `create_company`'s had not.

`tools/list` advertises only what is switched on AND available, so an absent tool
means one of those two, never "not built". There is now a test class saying so
where somebody hunting for the tool will find it, and the tool's own refusal has
always named the switch to tick.

### What did change about `create_company`

The spec it was measured against had moved, so the tool moved to meet it:

- **`abbr` is now 2–5 characters.** One is not an abbreviation of anything and
  collides immediately; past five, every account docname on the books carries it
  and `1100 - Cash - LONGER` is a name nobody reads twice.
- **It refuses an abbreviation left behind by a deleted company.** A duplicate
  `Company.abbr` was already refused; this catches the harder case, where a
  company was removed in the Desk and its chart was not, so docnames ending in
  `" - GHO"` still exist with no company behind them. A new company reusing that
  abbreviation would inherit docnames that look like its own and are not.
- **`chart_of_accounts` defaults to `Standard with Numbers`.** Numbered because
  this app resolves accounts by number as well as by name, and an unnumbered
  chart makes `resolve_account("1100")` impossible on a brand-new company. Where
  ERPNext's own template list is importable, an unknown template is refused with
  the available ones named — a template ERPNext cannot find produces a company
  with no accounts, which looks like a success and is not. Where it is not
  importable the check degrades to "cannot say" rather than to "refuse
  everything".
- **It creates the current AND previous fiscal year.** A company stood up in
  March is one whose first task is often last year's closing balances, and an
  opening-balance journal entry with no fiscal year to land in is refused by
  ERPNext with a message about a period that does not exist. Two rows, one
  conversation saved. Years that already exist are left alone and reported as
  such.
- **The result now carries the cost center tree, the fiscal years created, and a
  `next_step`** pointing at `set_company_defaults` — ERPNext books to those
  default account fields without asking, and a company whose defaults are empty
  fails at the first invoice rather than at creation.

Atomicity was already structural: `dispatch` rolls back before it logs, so a tool
that wrote a Company and then died cannot leave a half-built entity behind.
v0.12.2 adds the test that proves it for this tool specifically, and a second one
proving a *refusal* never gets as far as writing, since every validation runs
before the insert.

### The family register got a way in that is not the Desk

v0.12.1 shipped the `Family` DocType so `bench migrate` would stop dying and so a
`party_type='Family'` posting could resolve. It shipped with no MCP surface, so
adding a person meant `/app/family`. Four tools close that:

- `create_family_member` — name (which becomes the docname), relationship,
  optional `related_party`, active flag, notes.
- `update_family_member` — relationship, related party, active, notes. **Refuses
  a rename**: the name IS the docname and every journal entry that named them
  points at it.
- `list_family_members` — the register, filterable by active status and
  relationship, reporting who has a related-party record behind them and who
  does not.
- `get_family_member` — one person, their related-party detail, and **every
  posting that names them**: count, first and last date, net amount, companies.

**The posting count is read from the ledger, not kept.** A stored copy would
drift from what actually happened, and the entire value of the number is that it
cannot. That is the traceability half of a family petty-cash arrangement.

**The register holds no tax id, still on purpose.** A transfer below the IRS
annual gift exclusion is not compensation for services: no W-9, no 1099, which is
the whole reason the party type is separate from Supplier. Where a relative also
holds a role worth disclosing — member, lessor, trustee — `related_party` points
at the register that keeps four digits and never more, and `get_family_member` is
tested for never returning more than four.

**Retiring somebody is `active=false`, not a delete**, and the tool reports how
many postings would have been orphaned — which is the argument for the flag
existing.

`list_family_members` says out loud that a missing related-party entry is *not* a
gap for most of these. A list that read as forty problems would be a list nobody
acts on; the entries that matter are the ones who also hold a role.

### Added

- `create_family_member`, `update_family_member`, `list_family_members`,
  `get_family_member`. Catalogue: **125 tools — 59 read, 66 mutating.**
- `tests_standalone/test_family.py` — 45 tests, including the end-to-end loop
  this release closes: create a member over MCP, post a journal entry naming
  them, read the count back. v0.12.0 claimed that worked and could not deliver
  it; v0.12.1 made it possible from the Desk only.
- A test that the 1099 pre-fill still excludes Family postings — adding a way to
  create members must not change what the pre-fill does with them.

### Notes

- No doctype changes, so **no migration is required beyond the usual
  `bench migrate`** to pick up the new settings switches.
- The two new read tools default ON; the two new write tools default OFF.
- Full suite: 2058 tests, 0 failures. 73 skip without shapely and h3.

## 0.12.1 — 2026-07-30

A hotfix. `bench migrate` on v0.12.0 aborted in
`erpnext_mcp.patches.register_custom_party_types` with a
`LinkValidationError`, and the standalone suite passed the whole way — which is
the more important half of this release.

### What actually broke, which is not what the traceback looks like

The error reads `Could not find Party Type: Family` and looks like a
self-referential link. It is not. ERPNext's `Party Type` names itself
`field:party_type`, and **that field is a `Link` to `DocType`** — so a Party
Type's name has to be the name of a real DocType on the site. There was no
DocType called `Family`, so the insert was refused.

The loop registers party types in sorted order, so `Contact` went in first and
**succeeded** — because Frappe ships a core `Contact` DocType — and `Family`
failed immediately after. That asymmetry is the whole diagnosis: the two party
types were not equivalent, and nothing in the release knew it.

It goes deeper than the patch. A Journal Entry line carries `party_type` (a
`Link` to `DocType`) and `party` (a **`Dynamic Link`** resolved through it). So
bypassing the validation with `db_insert()` or `flags.ignore_links` — the
obvious fixes — would have registered a party type that the first posting using
it would then reject. That is worse than the crash: a crash at migrate time is
found by the person running the migrate, and a party type that silently cannot
be posted to is found by whoever is closing the books.

### The fix

**This app now ships a `Family` DocType.** A small register — name,
relationship, an optional link to the related-party entry, an active flag. It
holds no tax id on purpose: a transfer below the IRS annual gift exclusion is not
compensation for services, which is the whole reason the party type is separate
from Supplier. A relative genuinely paid for work is a Contact or a Supplier, and
the posting should be reclassified rather than the exclusion widened.

`Contact` needs nothing: Frappe's own Contact DocType is the register, which is
the correct answer and was already working.

**`ensure_party_types()` checks the target DocType before inserting, and never
raises.** It returns `{"created": [...], "existing": [...], "skipped": {name:
why}}`, and the patch prints the skips. A party type that cannot be registered is
worth saying out loud on the console; it is not worth aborting a migration over —
in v0.12.0 it took down the whole bench's migrate, and because `after_migrate`
never ran, that release's new tool switches were never seeded either. The
operator got a traceback *and* a half-configured app.

The two skip reasons are deliberately different sentences. "Ours, not migrated
yet" is a retry; "nothing on this site ships that DocType" is a dead end.

### The test double had no link validation, and that is why this shipped

The same shape as v0.12.0's `bool("0")` bug: a double that answers a question the
real framework refuses is a double that certifies code which cannot run.

`harness.py` now implements `Document._validate_links` on insert and save, for
`Link`, `Dynamic Link`, and the `Link`-to-`"DocType"` case that caused this. It
walks child rows too, because on a Journal Entry the party fields are on the
**line**, not the header — validating only the header would have left the entire
party mechanism unchecked. The ERPNext fields the app depends on are now modelled
with their real fieldtypes rather than as Data (`ERPNEXT_FIELD_LINKS`), and the
fixture seeds the Family and Contact records its GL rows point at, because a
fixture with postings and no people describes a site that cannot exist.

`test_patches.py` asserts the double genuinely reproduces the production failure
before asserting anything else — otherwise every test under it is theatre.

### Added

- **`Family` DocType.** Required for the `Family` party type to resolve. Named
  `field:family_member_name`, so a posting reads `party = "Alex Bramwell"`.
- **`tests_standalone/test_patches.py`.** Every patch run against an empty store:
  survives, is a no-op the second time, and survives with its target DocType
  missing. Plus a schema audit that every `Link` this app declares points at a
  DocType something ships, every `Dynamic Link` resolves through a field on its
  own doctype, and every party type resolves to a real DocType.
- End-to-end coverage that a Family posting and a Contact posting go through, and
  that a party who is not on the register is refused — which v0.12.0 claimed and
  could not do.

### Fixed

- `register_custom_party_types` no longer aborts `bench migrate`.
- `ensure_party_types()` returns a report instead of a list, and skips rather
  than raising. `install.after_install` / `after_migrate` and the patch all use
  the same path.
- `register_party_types` (the MCP tool) reports `skipped` with the reason and
  `resolves_to_doctype` for each party type, so a client can see the rule rather
  than infer it.
- `list_companies` reports `party_types.resolves_to_doctype`.
- The uninstall warning names the Family register — deleting it orphans every
  journal entry that named those people.

### Notes

- No new tools. The catalogue is unchanged at 121.
- `Family` is a generic DocType name to take, the same caveat as `Field` in
  v0.12.0. It is not optional: the party type cannot resolve without it.
- **Nothing needs re-running by hand.** The next `bench migrate` finds the Family
  DocType synced in `post_model_sync` before the patch executes, registers both
  party types, and `after_migrate` seeds the switches v0.12.0's abort skipped.
- Full suite: 1982 tests, 0 failures. 73 skip without shapely and h3.

## 0.12.0 — 2026-07-30

Three features in one release, because they share a backbone. A field sits on a
parcel; a cabin sits on the same parcel; and both of them belong to a company
that, until this release, this app could read but not create. Shipping them
separately would have meant two releases that each pointed at something the next
one adds.

Twenty-nine tools, four DocTypes, two Party Types, one new field on `Parcel`,
and the app's first two runtime dependencies — `shapely` and `h3`, for field
boundaries, both imported defensively so a bench without them loses five tools
by name rather than failing to load the other hundred and sixteen.

### Multi-Company — `create_company`, `update_company`, `list_companies`

**Every other tool took a company and none of them could make one.** For an
operation whose structure is a holding company, an operating company and a
trust, "add the opco" is not an administrative afterthought — it is the step
everything else waits on, and it meant leaving the model and clicking through the
Desk.

`create_company` hands ERPNext a correct set of arguments and then reports what
it **actually** built, which is not always what was asked for: an account count
of zero means the named chart of accounts does not exist on this site, and the
result says so rather than looking like a success. It also creates the fiscal
year containing today for the start month given — April for a farm year, January
for a calendar one, named for the span it covers rather than for one of the two
years it straddles.

**`update_company` refuses three things and says why each one.** The
abbreviation and the company name, because both are baked into the docname of
every account, cost center, parcel and lease on the books — changing either is a
migration, not an edit. The currency, but only once something is posted: every
one of those entries was measured in the old one, and relabelling it would
restate the whole ledger without touching a single number. A company with no
postings can still have its currency corrected, because the rule is about the
ledger rather than about the field. And the fiscal year start month once any
fiscal year exists, because a year that changes shape mid-cycle produces two
periods claiming the same days and no way to say which one a posting belongs to —
a short year created deliberately with `create_fiscal_year` is how that is done.

`list_companies` reports the GL entry count with the first and last posting
dates, which is how a caller tells a live company from a shell before it tries
anything.

### Two custom Party Types — `Family` and `Contact`

ERPNext ships Customer, Supplier, Employee and Shareholder. A family operation
pays two kinds of people that fit none of them, and recording them as Suppliers
is wrong in two different directions.

**`Family`** is a relative receiving money that is neither payroll nor a
purchase. `generate_1099_prefill` now reads those postings and **excludes** them,
reporting the count, the total and the names — so "nobody looked" and "somebody
looked and excluded them" are different-looking answers, and so a Family posting
that was really a payment for work is visible enough to be reclassified. A
transfer below the IRS annual gift exclusion is not compensation for services: it
needs no W-9 and produces no form. Without this party type those payments end up
recorded as Supplier payments, which puts family money into vendor spend **and**
onto a 1099 the recipient owes no tax on.

**`Contact`** is the consultant who looks at the orchard twice a year, the
neighbour who runs a tractor for a weekend — not a formal Supplier, but paid for
services, which is exactly the shape a 1099 exists for. The pre-fill now reads
those postings too and classifies them **borderline**, naming the W-9, rather
than leaving them unclassified where it has nothing to go on.

Both are seeded on install and on every `bench migrate`, and both are idempotent.
Registering a Party Type changes nothing already recorded: existing rules and
Journal Entries using Shareholder, Employee or Supplier keep working exactly as
they did.

### Field and Irrigation Zone — the structure under a parcel

**This app owns structure; the field apps own events.** A spray, a pick, a water
set and a soil test all happen to a *block*, and every one of them is recorded by
a different system. What none of those systems can be is the place the block
itself is defined, because a block outlives the app that last recorded something
against it — and because a cost centre, a lease and an appraisal all need to
point at the same ground.

**The docname is suffixed with the parcel, at every level.** A field is
`"Yellow Camp Block 3 - MC"` and a zone is `"YC3-Zone2 - MC"` — not
`"YC3-Zone2 - YC3"`, because a zone name already carries its block and repeating
it says the same thing twice while dropping the ground. That needs a short key
per parcel, so `Parcel` gains an `abbr` field. An operator who types one gets
theirs and a collision is refused; one who does not gets initials, and a
*derived* collision is disambiguated rather than refused, because nobody chose
that key. Parcels registered before this release carry no stored abbreviation
until something saves them, and nothing reads the field without falling back to
the same deterministic derivation — so there is no data patch.

**Two arithmetic refusals, both contradictions rather than opinions.** Blocks
summing to more acres than their parcel; zones summing to more area than their
block. Both are the failure a bad import produces every time, and both name both
figures and the excess, because the useful next question is which of the two is
wrong. Blocks summing to *less* than the parcel is left alone: roads, ditches,
headlands and the house are all real, and a controller that complained about that
would complain about every real farm.

**The variety autosuggest comes from the ground.** `list_fields` reports the
varieties already planted on the site. A hardcoded list would be wrong the first
time somebody puts a new one in the ground; what is already there cannot be.

`import_farm_app_fields` is the schema-alignment foundation, not the sync: it
creates Fields carrying each legacy record's Farm App id so a later engine has
something to match on. Dry run by default, the whole batch validated before the
first insert — a half-imported farm is worse than an unimported one, because the
second run has to work out which half — and a block already registered is skipped
with the reason, so the same batch re-runs safely.

#### Boundaries, and the geofence they make possible

Both doctypes now carry a GeoJSON polygon, and `set_field_boundary` /
`set_zone_boundary` derive everything indexable from it: centroid, bounding box,
H3 coverage at resolutions 6-10, and the area the shape actually encloses. None
of those can be set directly — a figure a caller could edit independently of the
polygon is a figure that will disagree with it, and the disagreement surfaces as
a geofence saying no to somebody standing in the right place.

**THE H3 FILL STORES EVERY CELL THE SHAPE TOUCHES, and that is the single most
consequential line in the release.** H3's default polygon fill keeps cells whose
*centre* is inside the shape. An orchard block is smaller than one H3 cell at
resolutions 6, 7 and 8 — so the default returns an **empty set** for a real
field, and a spatial index built on it answers "in no field" for a point plainly
in one. A false negative that reads like a policy decision is exactly what a
geofence must not produce, so the fill uses `contain="overlap"`, which is a true
superset. There is a test asserting no stored resolution is ever empty, because
that empty set is what the obvious implementation silently returns.

For the same reason `find_fields_containing_point` narrows with the **bounding
box** rather than with the H3 cells — a bbox is a guaranteed superset of the
shape it bounds, so a candidate set built from it cannot miss the right answer —
and then tests every candidate exactly. The boundary counts as inside: a pick
recorded on the headland is in the block, and a geofence that excludes its own
edge tells the picker they are nowhere. The result also reports how many blocks
have **no** boundary, because on a half-mapped farm an empty answer means "not
inside any *mapped* block" rather than "not on the farm".

**Area is spherical and says so.** `shapely` computes area in the units of its
coordinates, and these are degrees — so `.area` is degrees squared, which is not
an area of anything. The computed acreage uses the standard spherical-excess
integral; a test checks it against a rectangle whose true size is worked out by
hand, and the two agree to 0.2%. A polygon more than 25% from the recorded
acreage is refused because one of the two figures is then about a different piece
of ground; 5-25% is reported and both figures are kept, since a deed, a GIS trace
and a tape measure routinely disagree.

**Zone containment is reported, never enforced.** A shared water line crosses a
boundary, a pump house sits on the headland, a mainline runs down an easement.
`boundary_contained_in_field` comes back true, false, or **null** when the block
has no boundary to check against — "we could not check" and "we checked and it is
outside" being different answers that a report must not conflate.

`import_field_boundary_geojson` migrates a farm's existing polygons in one go,
and is deliberately the OPPOSITE of `import_farm_app_fields`: per-feature errors
rather than whole-batch refusal, because it only sets a field on records that
already exist. One bad feature in forty is a bad feature, not a reason to refuse
the other thirty-nine. It never creates a Field.

The satellite fields on `Field` — provider, asset reference, last pull date, NDVI
mean and standard deviation — are schema only; nothing fetches imagery in this
release. NDVI is stored on its real range of **-1 to 1** rather than 0 to 1:
water and bare soil read negative, and clamping the floor to zero would make a
flooded block indistinguishable from an unmeasured one. When the pull lands it
should fire on state — a boundary exists AND the last pull is stale AND the block
is in an active crop cycle — not on a calendar tick that would spend imagery
credits on a fallow block in January.

### Housing Unit and Housing Assignment — the labor camp

Employer-provided farm housing sits at the intersection of three regimes that
each want a different fact about the same cabin, and none of them accept "we know
who lives there" as an answer: IRS Section 119, Oregon's ORS 653 and OAR 839-015,
and the FSMA Produce Safety Rule's Subpart L. None of the flags this release adds
is a determination and none of this is legal advice — they record what somebody
decided and when, so the decision can be defended or revisited.

**Overlap is refused by default and allowed on request.** Two people in one cabin
on one night is a data-entry mistake most of the time and the whole point of a
Multi-Unit Building the rest of the time. Refusing outright would make the
barracks unusable; allowing silently would let a typo become a bed somebody does
not have. So it refuses, names the assignment already there, and takes
`allow_multi_occupancy=true` from a caller who means it. Somebody moving out on
the 15th and somebody moving in on the 15th **did** share the cabin that night,
and the comparison is inclusive at both ends for that reason.

**Nothing deletes an assignment.** `end_housing_assignment` writes an end date;
the row stays. An assignment removed when the person leaves cannot defend a
Section 119 classification, cannot answer a wage claim about a housing deduction,
and cannot tell an investigator who was in the camp the week in question — and
those are the three moments the record exists for.

**The employee link is soft until an HR app makes it hard.** `Employee` is a Data
field rather than a Link, because Frappe HR is not a dependency of this app and a
Link would make the whole doctype fail to migrate on a site without it. Where an
HR app *is* installed the refusal is real: an assignment naming somebody not on
file is a roster that has already drifted from payroll.

**The lawful occupancy is computed once and then left alone.** Fifty square feet
of sleeping area per occupant — 29 CFR 1910.142(b)(1), which Oregon's rules
follow — gives a unit with a floor area an answer without anybody typing one. But
it is a default, not a derivation: a cabin with a fixed bunk layout keeps the
number somebody worked out, and changing the square footage recomputes only a
limit that was itself computed. A capacity over 20 outside a Multi-Unit Building
is warned about rather than refused, because a twenty-person cabin is barracks by
another name and some of them really are.

### Compliance is woven into the operational doctypes, not bolted beside them

The food-safety fields are on `Field`, the water-quality fields are on
`Irrigation Zone`, and the habitability and detector dates are on `Housing
Unit`. The test is whether removing a field breaks operations or only breaks
reporting — and each one has a test that asserts **both halves of the same
removal**:

- Remove `last_spray_date` and the Worker Protection Standard report loses a line
  *and* nobody can answer whether the re-entry interval on block 3 has run.
- Remove `worker_hygiene_station_present` and an inspector loses a checkbox *and*
  dispatch loses the fact that decides whether a crew may work that block at all.
- Blank a zone's `water_test_last_date` and it lands on the FSMA Subpart E list
  *and* `get_irrigation_zone` starts saying not to run it before harvest.
- Remove a Field's boundary and the spray record loses the one thing an auditor
  can check a GPS fix against *and* the geofence stops answering for a crew
  standing in the block.
- Mark a Housing Unit uninhabitable and it appears on the register's exception
  list *and* `create_housing_assignment` refuses to put anybody in it.

A separate "Field Compliance Log" that somebody fills in after the fact would
fail that test — nothing about picking would stop if it disappeared — which is
why this release does not have one.

### Fixed

- **`compat.checked`, and every Check field read through it.** `bool("0")` is
  True, and a Check field does not always come back as an integer:
  `frappe.new_doc` copies the DocType's declared default onto the document
  verbatim, and in the DocType JSON that default is the *string* `"0"`. A tool
  describing that document with a bare `bool()` reports every unticked box as
  ticked — which would have said a block with no worker hygiene station had one,
  and a housing unit outside the Produce Safety Rule was inside it. This is the
  same failure `settings.as_bool` exists to prevent for the tool switches, and
  the two are deliberately identical in behaviour.
- **`link_field_to_cost_center`'s cross-company refusal was unreachable.** The
  cost center resolver refused first with a terser message, so the sentence
  explaining *why* a cost allocated across two companies is an intercompany
  transaction rather than a dimension never appeared. Resolution is now scoped
  first and site-wide only as a fallback, so the explanatory refusal is the one a
  caller gets.
- **`create_housing_assignment` reported one occupant too many** in a shared
  unit, because it recounted the overlaps after inserting the row and counted the
  new row as one of its own.
- **`create_housing_unit` never checked an Asset's company.** It read
  `owning_entity` off a document whose controller had not run yet, so the field
  was empty and the cross-company check silently passed everything.
- **`create_housing_assignment` let an end date before the start reach the
  controller**, so the caller got a raw `ValidationError` instead of a sentence
  saying nothing was created.

### Notes

- `Field` is a doctype name with no core Frappe or ERPNext collision today, but
  it is a common enough word to be worth knowing you have taken. If a future app
  wants it, this one has it.
- `Parcel.abbr` is additive and nullable. Existing parcels are unaffected until
  something saves them, and every read falls back to deriving the same key.
- `shapely` and `h3` are declared dependencies but imported defensively, and CI
  runs the whole suite **twice** — once before installing them and once after —
  because a build that only ever saw them present would never check that the
  graceful-degrade path works.
- Full suite: 1951 tests, 0 failures. 73 of them skip on a bench without the
  geospatial libraries.

## 0.11.0 — 2026-07-30

Four features in one release, because they are one feature. A parcel is held by
an entity, an entity is a related party, a related party is a 1099 recipient, and
a quarterly report is the document all of it ends up inside. Shipping them
separately would have meant three releases that each pointed at a doctype the
next one adds.

Fifteen tools, three DocTypes, no child tables, and no new runtime dependency.

### Real Estate — `Parcel` and `Lease`

**The unit is the parcel as the county assessor knows it.** A family's land is
described four different ways by four different documents: an appraisal talks
about "Red Camp", a tax statement about parcel 1N-13E-8-1200, a deed about metes
and bounds, and the balance sheet about a Fixed Asset with a purchase price. Only
one of those is a unit everyone agrees on. So the register is keyed on the
parcel, carries the assessor's number as the identifier a third party will
recognise, and links out to the Asset rather than trying to be one.

**Appraised value is not book value, and they are meant to differ.**
`gross_purchase_amount` on an Asset is what was paid, which is what the balance
sheet must carry; `appraised_value` on a Parcel is what it is worth, which is
what an estate plan turns on. A single field would force one of those two
questions to be answered wrongly. `link_parcel_to_asset` reports the gap between
them — a parcel appraised at 3,100,000 sitting on the books at a 1998 cost of
240,000 is not a discrepancy to be fixed, it is unrealised appreciation, and it
is the single most important number in a succession conversation. Nothing posts
it, because unrealised appreciation is not a journal entry.

**The docname carries the entity**: `"Red Camp - HLD"`, not `"Red Camp"`. Family
land gets reorganised, and two entities in one family end up with a "Home Place"
apiece. A docname keyed on the name alone would make the second impossible to
file and the first impossible to trust.

**A duplicate assessor parcel id inside one entity is refused.** That number is
the county's primary key; two parcels sharing one means a typo in one of them,
and it is the refusal that catches a bad import.

**Direction on a lease is stated, not inferred.** Outbound means the owning
entity is the lessor. The alternative — working out which party is "us" by
matching a legal name against a Company docname — is wrong for every entity whose
legal name is not its ERPNext name, which is most of them ("Highland Ltd
Liability Co." against a Company called "Highland LLC"). So the caller says, and
`create_lease` reports whether the claim looks *consistent* with the parties
named. Reported, never enforced: a refusal built on a string comparison it cannot
win is a refusal nobody could get past.

**Nothing expires a lease.** A lease marked Active whose expiration date has
passed is reported by `list_leases` and left exactly as it was. Farm ground
routinely runs on month to month past its stated term, and a status that flipped
itself on a calendar would erase the difference between "still running" and
"nobody has looked at this in years". The warning says so in capitals, because a
reader who assumes the system tidied up is a reader who has stopped checking.

**The rent roll refuses to treat an unknown as a zero.** Rent is annualised from
amount and frequency for Active leases only. A crop share and a one-time payment
have no annual rate; they are listed under `rent_not_annualisable` rather than
counted as nothing, because a rent roll that quietly zeroed them would understate
the whole portfolio.

New tools: `create_parcel`, `update_parcel`, `list_parcels`, `get_parcel`,
`link_parcel_to_asset`, `create_lease`, `update_lease`, `list_leases`,
`get_lease`. The four read tools default ON, the five mutating ones OFF.

### `Related Party` — the governance register

**This is not the Party field on a Journal Entry.** ERPNext already answers "who
was this transaction with" through Supplier, Customer, Employee and Shareholder
links; those work and nothing here replaces or shadows them. This answers a
different question — "who is related to us, in what capacity, from when, and
under what document" — which no transactional field can, because a transaction is
an event and a relationship is a state. "Was the person we paid $24,000 last year
a manager of this company at the time" is a question the ledger cannot answer and
the IRS asks anyway.

**Four digits, never nine.** `tax_id_last4` takes exactly four digits and refuses
nine — not truncated, not masked, not accepted with a warning. The refusal names
the four digits to send instead, because a validator that says "invalid format"
to somebody who has just pasted a real SSN has told them nothing about why it
matters. The controller enforces the same rule, since the Desk form is a second
door into the same field, and the field is declared four characters long as the
belt to that brace. The full number belongs on the signed W-9, on paper. And
`get_related_party` never returns more than four digits *even from a linked
Supplier* — `supplier_detail.tax_id` says only whether one is on file.

**A person is not one row.** In an LLC the ordinary case is somebody who is both
Manager and Member, under two different instruments, from two different dates.
One row with one Select cannot hold that, and picking a "primary" role would mean
the register quietly disagrees with the operating agreement. So the docname
carries the relationship — `"Tim Polehn - Manager - OML"` beside
`"Tim Polehn - Member - OML"` — and `list_related_parties` reports `count`
(relationships) and `distinct_people` (names) separately.

**Nothing is deleted when a relationship ends.** `end_date` is set and the row
stays: the transactions it explains are still in the ledger, and a prior year's
disclosure schedule still needs to know who was who at the time.

It sits beside the cap table rather than inside it. `Cap Table Entry` maps an
anonymous member id to an ownership percentage — deliberately the only place on
the site where that mapping exists. Related Party holds every other kind of
relationship: the trustee who owns nothing, the estate attorney, the son who is a
beneficiary but not yet a member. Folding those in would mean rows with no
percentage in a register whose whole purpose is that the percentages total 100.
The two link, so a member appears in both without either being copied.

New tools: `create_related_party`, `update_related_party`,
`list_related_parties`, `get_related_party`.

### `generate_quarterly_investment_report` (mutating, default OFF)

**Kairos, not chronos.** A quarterly report is not due on a date; it is due when
the quarter is *actually closed*. Four things must be true, and the refusal names
every one that is not — all of them at once, so a single call answers "am I
ready?" rather than sending the caller round the loop four times:

1. the quarter has ended;
2. the custodian's statement is filed as a **Prior Statement** governance
   document with an effective date inside it — a report written before the
   statement arrived is a report written from a guess;
3. no journal entry touching the investment accounts is still a draft, because an
   account that reconciles today and will not once three drafts are posted is not
   reconciled, it is about to not be;
4. no bank transaction in the period is unreconciled.

A report generated on a calendar date regardless of state is a report whose
numbers may be wrong, signed by somebody who assumed the schedule meant
something. `dry_run=true` runs every precondition and computes every figure
without writing, which is the right first call.

**It invents nothing.** Without `benchmark_rate_percent` the return over
benchmark and the performance fee are NOT computed and say so in words. They are
not zero and not estimated: the 10-year Treasury yield is a market fact this site
does not hold, and a performance fee computed against an assumed benchmark of
nothing overstates what the manager is owed. Same for the high-water mark and for
`net_contributions`, which is reported as an assumption when it is one.

**Holdings come from the caller.** This app reads one ERPNext site and the
custodian's positions are not on it. Pass `holdings` and the report reconciles
the snapshot against the ledger and reports the variance; omit it and assets
under management are the ledger balance of the investment accounts, stated as
such. The accounts themselves are matched by name off the company's own chart and
**listed in the report**, so the reader sees exactly what was included — or named
explicitly, and a chart with no match is refused rather than guessed at.

**Manager and custody fees accrue at 1.00% each** by default — the split the
Investment Management Agreement is actually charged at, inside its 2.00% cap —
computed on average assets for the quarter. It is an accrual and nothing posts
it; the result says which tool does. A combined rate above the cap is flagged,
not refused, because a later agreement may raise it.

**PDF is the primary format and that is a requirement, not a preference.** A
`.docx` handed over on 2026-07-29 could not be opened on the machine it was sent
to. `output_format="docx"` exists for a report that has to be edited before it is
signed, and the default is never it.

### `generate_1099_prefill` (mutating, default OFF)

A calendar year of supplier payments, aggregated into an xlsx worksheet and a
per-recipient 1099-NEC form (Copies A, B and C), filed together in the governance
archive as a **Tax Filing**.

**It is called a pre-fill and it means it.** Recipient taxpayer ids print as
`XXX-XX-nnnn` because this site holds four digits on purpose. Copy A must be the
official scannable red-ink form or an electronic filing; the Copy A page here is
stamped as an information copy and says that printing and mailing it is not a
filing. Copies B and C print on plain paper and are the ones that go out.

**Classification is never silent.** Every recipient comes back `reportable`,
`exempt` or `borderline` with the reason in a sentence:

- an **LLC** is borderline, because a disregarded entity is reportable and one
  taxed as a corporation is not, and only the W-9 says which;
- a **law firm** is borderline even when incorporated, because attorneys are
  reportable regardless — which is precisely why "ends in PC, skip it" is the
  wrong rule, and why the matching is on word tokens rather than substrings
  ("Lawson Supply" is not an attorney);
- a **government-sounding name** is flagged rather than dropped, because a name
  is a hint and not a determination;
- a vendor with **nothing recorded** is borderline with the remedy: register it
  as a Related Party, or read the W-9.

That last one is why these two features are one release: a Supplier row cannot
say "this vendor is the manager's own LLC", and the related-party register can —
through the `supplier` link, which is what turns a payment in the ledger into a
disclosure on the return.

**The arithmetic, which is the part worth arguing with.** Payments are summed
from **GL Entry** rows carrying a Supplier party — so every voucher type, and
only submitted ones, since cancelled vouchers leave no GL row to filter out:

- on a **Payable** account: **debits only**. A debit to accounts payable is a
  bill being paid; a credit is a bill being raised, and a 1099 reports cash paid.
- on **every other account**: **debits minus credits**. A site that books a
  supplier straight from expense to bank puts the party on the expense line, so
  the debit is the payment and a credit is a refund that genuinely reduces it.

That rule is right in both bookkeeping styles, which is why it is a rule and not
a switch. `by_account` shows the debits and credits behind every total so the
reasoning can be checked rather than believed.

**What is excluded is said out loud.** Employees, because that is W-2 territory —
and the count and total of employee-party postings is reported anyway, so "nobody
looked" and "somebody looked and excluded them" are different-looking answers.
Opening entries. Anything under the threshold, listed with its total so a case
near $600 is visible rather than absent.

**It refuses a tax year that has not ended**, naming the earliest date it could
be run.

### Document writers: PDF, XLSX and DOCX in the standard library

`erpnext_mcp/render/` writes all three formats with `zipfile`, byte offsets and
nothing else. This app promises no runtime dependency beyond Frappe/ERPNext, and
that promise is what makes `bench get-app` safe on somebody else's bench. Frappe
ships two routes to a document and both are conditional: `frappe.utils.pdf`
shells out to a **wkhtmltopdf binary** present in some images and absent in
others, and `xlsxutils` imports openpyxl. Either means a tool that works on the
machine it was written on and fails on the one it was deployed to, at the moment
somebody needs the report.

**Courier, and only Courier.** A PDF naming a base-14 font carries no glyph data,
but the writer still has to know how wide each glyph is to wrap a line or
right-align money. For Helvetica that is a 230-entry width table transcribed by
hand, where one wrong number is a column that silently overlaps in the printed
copy. Courier is monospaced at exactly 600/1000 em: the arithmetic is exact
rather than approximately right, and decimal points line up because they cannot
do anything else.

**Money columns never wrap.** A right-aligned column holds a formatted amount,
and an amount broken across two lines reads as two figures. When a table will not
fit, the prose columns give way and the numbers keep their width.

**The same inputs give the same bytes.** Zip members carry a fixed timestamp and
`render()` does not mutate the document, so the archive copy and the printed copy
cannot differ in a way nobody can see.

### `scripts/seed_related_parties.py`

Seeds the related-party register from a JSON file the operator keeps **outside
this repository** — the useful content of that register is people's names, and
this repository is public.

It runs outside `bench execute`, so it configures Frappe itself: `--sites-path`
or auto-detection by looking for `common_site_config.json`, `--site` or
`currentsite.txt`, and the log directories created before `frappe.connect()`
rather than assumed. Dry-run by default; `--apply` writes. The whole plan is
validated before the first insert — including the four-digits-never-nine rule,
refused before Frappe is even started — so a plan of forty records is refused
whole rather than half-applied. The module docstring documents the
`docker cp` sequence for getting both the script and the plan into a container,
and there is a test comparing the flags the docstring names against the flags
`argparse` actually registers.

### Also

- `Governance Document` gained two categories: **Tax Filing** and **Lease**.
- `args.py` gained `select_options` and `as_choice`, which read a Select's
  options off the site's own meta. `governance.py`'s private copies now delegate
  to them rather than being a second implementation of the same rule.
- `output_path` on both generators is confined to the site's own
  `private/files` and `public/files`, checked on the **resolved** real path so a
  symlink cannot step outside, and refusing to overwrite an existing file unless
  told to. A bad path refuses the whole run before the first write rather than
  leaving an archive entry behind.
- `before_uninstall` now warns about Parcel, Lease and Related Party rows too.
- Standalone suite: 1222 → 1536 tests; in-bench suite 255 → 284.

## 0.10.0 — 2026-07-29

One tool, for the gap found the day somebody tried to put a year of brokerage
statements onto the entries that book them and discovered there was no way to.

### `attach_file_to_document` (mutating, default OFF)

Attaches one file to **any** document on the site. A WFA statement onto the
Journal Entry that books it. A receipt onto the Bank Transaction it explains. A
purchase contract onto the Asset.

**Why this was missing and why it was not obvious.** The app already had
`attach_governance_document`, and from the outside it looks like the tool for
this — it takes base64, it makes a private File, it says "attach" in the name.
It is not. It files a *new* Governance Document and attaches the file to
**that**. Correct for a trust instrument or an operating agreement, which are
documents in their own right. Useless for December's statement, which is not a
document in its own right — it is evidence for a posting, and an auditor asking
"what supports this entry" wants the answer *on the entry*. Thirteen statements
and three anchor Journal Entries later, there was no MCP path from one to the
other at all, and the only route left was clicking through the Desk.

**It creates a File and nothing else.** No balance moves, no docstatus changes,
no existing row is touched. That is the whole shape of the tool.

**Every constraint is read off the site, not compiled into the app.** There is
no list of blessed file extensions here and no list of doctypes that may be
attached to. Both would be a snapshot of one ERPNext install frozen into an app
that gets installed on others, and both would refuse things the site itself
permits. So:

| Refusal | Read from |
| --- | --- |
| Unknown `doctype` or `name` | the site's schema and tables |
| Acting user cannot `write` the parent | Frappe's permission model — the permission the Desk's own attach control needs |
| Parent is **cancelled** | the parent's `docstatus`; `allow_cancelled=true` overrides |
| A filename the document already has | that document's existing attachments, with the clashing File named |
| Too many attachments | the parent DocType's `max_attachments` |
| Disallowed extension | whatever allowlist System Settings declares — **nothing**, on a site that declares none, which is Frappe's own answer |
| `company` mismatch | the parent's `company` field |

**A guard that cannot be applied is an error, not a shrug.** Passing `company`
for a doctype that has no company field is refused rather than ignored. A caller
who believes a guard ran when it did not is worse off than one who never asked
for it.

**Cancelled parents are refused by default** because a cancelled document is
history, and quietly growing its evidence file afterwards is how a record stops
meaning what it says. `allow_cancelled=true` says the caller knows — which is a
different thing from not having noticed.

**A second file under the same name is refused, naming the first.** The
anticipated caller is a script walking a year of statements onto their entries;
half of it failing and being re-run is the normal case. "That one is already
done, here is its File docname" is the useful answer. Two files with one name on
one document is a question nobody can answer in 2031.

**`dry_run` defaults to FALSE**, unlike `import_chart_of_accounts` and
`run_depreciation_cycle`. Those write many documents and are hard to unpick;
this writes one File and moves no money. Making the ordinary case cost two round
trips would be safety theatre. `dry_run=true` validates the parent and returns
the proposed action — including the size and sha256 — without writing, which is
what a batch script should do over its target list once before running live.

Files are **private by default**, so reading one back through
`get_attachment_content` requires read permission on the parent. `file_content`
is base64 with the same 8 MB ceiling `attach_governance_document` uses;
`file_url` records an externally hosted file without copying it. The result and
the audit row both carry the sha256 of the stored bytes.

### The audit log stopped losing the interesting half of a row

`MCP Action Log.arguments_json` was truncated at 8000 characters *after*
serialisation, and `json.dumps(..., sort_keys=True)` puts `file_content` ahead
of `file_name`, `is_private` and `name`. A megabyte of base64 would therefore
have produced a row recording that a file was attached and nothing whatever
about which file, or to what.

Oversized *values* are now elided before serialisation —
`"<11184812 characters elided>"` — so the length survives and every other
argument stays in the row. Whole-payload truncation still applies on top, for a
payload that is large because it has many arguments rather than one big one. The
sha256 that identifies the bytes is in `result_summary` either way.

### Also

- `attach_governance_document` and `attach_file_to_document` share one base64
  decoder (`files.decode_base64_content`), so there is one 8 MB ceiling to raise
  rather than two to forget. The refusal wording either tool produces is
  unchanged.
- The standalone harness's `Meta` now carries `max_attachments`, Frappe's
  0-means-unlimited default, so the limit check is testable without a bench.

**77 tools** — 38 read-only, 39 mutating.

## 0.9.0 — 2026-07-28

Three tools for the day you post a year of history, and the fix for the bug that
made that day take twice as long as it should have.

### The bug fix, first, because it is the one that cost a day

**Every Journal Entry this app wrote was missing half of every amount.** A
`Journal Entry Account` row stores each figure twice — `debit` in the company's
currency and `debit_in_account_currency` in the account's — and ERPNext's
`set_amounts_in_account_currency` derives the first FROM the second on every
validate:

```python
d.debit = flt(d.debit_in_account_currency * d.exchange_rate, d.precision("debit"))
```

This app set `debit` and left `debit_in_account_currency` at zero. So the insert
succeeded, the draft was written to the database with its amounts silently
zeroed, and the entry was refused the moment anything validated it again:

```
Row 1: Both Debit and Credit values cannot be zero
```

Four auto-generated opening-balance entries did exactly that on a live site. The
workaround — rekeying every line through `create_journal_entry` with the
`_in_account_currency` fields set by hand — works, and is hours of typing to get
back to what the tool was supposed to have produced.

**The fix is in `validated_journal_lines`, not in the tool that surfaced it.**
`set_opening_balance` was where it was noticed, but every Journal Entry this app
writes — opening balances, member events, depreciation runs, loan payments,
hand-built entries — comes through that one function, and fixing only the tool
that showed the symptom would have left the other five wrong. Every line it
returns now carries both columns. At exchange rate 1 the account-currency figure
is *copied* rather than computed, so no rounding can put a fraction of a cent
between two columns of the same number.

Two things fell out of doing it there:

- **A line given only in the account's currency is now understood.**
  `{"account": "1100", "debit_in_account_currency": 100}` means the same as
  `{"account": "1100", "debit": 100}` and is no longer refused as a line with
  neither a debit nor a credit.
- **A foreign-currency line with no `exchange_rate` is now refused**, naming both
  currencies. Previously it would have been posted at the company-currency figure
  and then converted again by ERPNext. And a line whose `debit` and
  `debit_in_account_currency` disagree is refused rather than one of them being
  chosen: this app's double-entry check would have run on one set of numbers and
  the posting on another.

**Why the standalone suite did not catch it.** The double stored whatever it was
given. `harness.JournalEntryDocument` now models ERPNext's derivation *in the
order ERPNext does it* — zero-check against the values as given, then derive —
which is what reproduces the real failure: a draft that inserts cleanly, reads
0.00, and cannot be submitted. A double that derived first would have failed the
insert instead, and a double that filled the columns in from `debit` (the
intuitive direction) would have let the broken code pass. Fourth time now:
*when the double is more permissive than the framework, tests pass and sites
break.*

### Added — tools

- **`post_opening_balance_journal_entry`** (mutating, default off). A whole
  opening balance sheet as one Journal Entry, every line explicit.

  `set_opening_balance` is the right tool when you know one side of one
  historical event and want the equity plug computed. It is the wrong shape for
  transcribing a trial balance off the previous system, where both sides are
  already in hand: that means one call and one stray equity line per account.
  This takes the lines as given, adds a single balancing line to an
  `offset_account` you name — required exactly when the lines do not balance —
  and flags the entry `is_opening` with the `Opening Entry` voucher type.

  **It can post.** `submit: true` submits the entry after creating it, which is
  why it checks `allow_submit_journal_entry` as well as its own switch, and
  checks it *before* writing anything so a site with posting disabled gets a
  refusal rather than a draft nobody asked for.

  The offset account is not required to be equity, unlike `set_opening_balance`'s
  computed plug. A transcribed trial balance that is out by the retained earnings
  figure belongs against retained earnings, and the caller naming the account is
  making that call on purpose.

- **`bulk_submit_journal_entries`** (mutating, default off). Submit up to 500
  drafts in one call.

  Five hundred drafts posted one MCP round trip at a time is not the same job at
  a different speed. It is the job where somebody loses track at number four
  hundred and stops without knowing which ones went.

  **Each entry is submitted in its own transaction** — committed on success,
  rolled back on failure — and the loop carries on. This is the only place in
  this app that commits mid-call, and it is deliberate: the alternative is a
  batch where number four hundred fails and the request rolls back the three
  hundred and ninety-nine postings that were fine. It is also what Frappe's own
  bulk submit does. Returns a row per document with `ok` and the exact error,
  plus aggregate counts.

  An already-submitted entry comes back `ok` with `skipped: already_submitted`,
  never an error, so a half-finished batch is safe to retry whole. A cancelled
  one is a failure — it cannot be posted again. Checks
  `allow_submit_journal_entry` too, and fails before touching anything.

- **`delete_draft_journal_entry`** (mutating, default off, destructive). Delete a
  draft outright.

  `cancel_journal_entry` refuses a draft, correctly: there is nothing to reverse,
  because a draft has moved no balance. That left an unwanted draft with no MCP
  path at all, and a tool that can produce four hundred drafts and not withdraw
  one makes work rather than doing it.

  **Drafts only, whatever is asked.** A submitted entry has written GL Entries;
  deleting it would take those balances with it and leave nothing saying why, so
  it is refused and pointed at `cancel_journal_entry`. A cancelled entry and its
  reversing rows are the evidence that a posting was made and undone, so that is
  refused too.

  `reason` is mandatory, and the response carries the deleted entry's company,
  date, totals and every line — because once the call returns, the MCP Action Log
  row is the only record that the document ever existed.

### Changed

- `reconcile_bank_transaction` now names `payment_document` when a caller sends
  `payment_doctype`. The field is `payment_document` on ERPNext's Bank
  Transaction Payments table and always has been, in both this app's schema and
  its handler — but `payment_doctype` is what the field is called almost
  everywhere else in Frappe, so it is what a model reaches for first, and
  "payment_entries[1] needs both payment_document and payment_entry" did not say
  which of the two keys was the problem. It does now, quoting the value back in
  the right shape. Accepting both names was the other option and was not taken:
  this would have become the only tool in the app that reads a key it was not
  given.

### Tests

1180 standalone tests, up from 1112. The new ones worth naming:

- `AccountCurrencyAmounts` in `test_mutate_tools.py` — the regression suite for
  the bug above, including *the entry can actually be submitted*, which is the
  assertion whose absence let v0.8.0 ship.
- `TheOpeningEntryCanActuallyBePosted` in `test_opening.py` — the same thing
  through `set_opening_balance`, single-line and multi-line, including the
  computed equity plug, which is the line most likely to be the one nobody filled
  in because this app builds it rather than the caller.
- A round trip in `test_mutate_tools.py` from `create_journal_entry` through
  `submit_journal_entry` to `reconcile_bank_transaction`, and a test of what
  ERPNext's own `add_payment_entries` is handed and what is read back from it.

## 0.8.0 — 2026-07-27

The tooling a company needs on the day it goes live: the bank accounts money
actually arrives in, the balances that were true before day one, the notes it
owes, and a way to get rid of the accounts a bundled chart left behind.

v0.6.0 made the axes a posting is filed under reachable. v0.7.0 added who owns
the company and what the equipment is worth. This is the layer between those and
a first bank sync — eight new tools, one read tool, one new doctype, thirteen
more company defaults, and the fix for a bug that made setting up a real chart of
accounts harder than it should have been.

### The bug fix, first, because it is the one that cost time

**`import_chart_of_accounts` could not create a new root account.** Every live
import that included a top-level account died on the first one with:

```
MandatoryError: [Account, 1000 - Assets - OML]: parent_account
```

ERPNext's Account marks `parent_account` as required. A root account by
definition has none, so the insert never reached any of this app's own logic.
The workaround — renumber the company's existing roots to 91xxx and graft the new
tree under a renamed one — works, and is a lot of moving parts for something the
importer is supposed to do.

**The fix is one flag on one insert**, and it is the same flag ERPNext's own
chart-of-accounts importer sets for its own roots
(`erpnext/accounts/doctype/account/chart_of_accounts/__init__.py`):
`doc.flags.ignore_mandatory = True`, set **per document and only when the account
has no parent**. A child that skipped mandatory validation would be this app
quietly disabling a check the framework meant to run.

The plan reports it too, so dry run and live run still describe the same thing:
`new_root_accounts` lists the accounts that would become new roots, with a note
saying they are added *alongside* the company's existing ones — ERPNext will not
let a root be moved or renamed into an existing tree afterwards.

**Why the standalone suite did not catch it.** The double inserted root accounts
quite happily. `harness.AccountDocument` now models Frappe's mandatory pass and
raises the real `MandatoryError`, which turns eleven previously-green tests red
against the unfixed code. That is the recurring lesson from this project's own
history, third time now: *when the double is more permissive than the framework,
tests pass and sites break.*

Renumber-and-graft is unchanged and has its own test, because a live site is
already set up that way.

### Added — tools

- **`create_fiscal_year`** and **`update_fiscal_year`** (mutating, default off).
  The prerequisite for everything else in this release that touches history.
  ERPNext refuses a posting whose date falls outside a fiscal year, and it
  refuses it *from inside the document being saved* — so on a site whose only
  year is 2026, booking a March 2025 equipment transfer fails with an error about
  a date rather than about a missing year. `set_opening_balance` cannot reach a
  period until the year exists.

  **The overlap check is company-aware**, which is the part worth getting right:
  a fiscal year with no `companies` is global and collides with everything, two
  restricted years collide only where they share a company. Two years covering
  the same day for the same company make ERPNext's own `get_fiscal_year`
  ambiguous, and which year a posting lands in stops being a fact about the
  posting. Disabling a year does not free its range. ERPNext's own
  `validate_overlap` is company-blind on several versions and is stricter; where
  it is, its refusal is passed through unchanged — this never loosens a rule the
  framework enforces.

  **`update_fiscal_year` guards the dangerous half.** Moving a year's dates moves
  no posting; it changes which year — or no year at all — every posting already
  written falls into, retroactively. So the GL entries that would fall *out* of
  the new range are counted before anything is written and any at all is a
  refusal with the count. It cannot rename the year (the name is the docname, and
  is the string every Journal Entry and Budget that names a year holds) and
  cannot change `companies`; both are refused by name.

  Also: ERPNext requires a year to end exactly one year after it starts, less a
  day, unless `is_short_year` is set — and its own message does not say which
  date it wanted. This computes it, clamping leap days the way the calendar does
  (a year starting 29 February ends on the 27th).
- **`set_opening_balance`** (mutating, default off). Books one historical event —
  equipment transferred in, proceeds of a sale that predates this ledger, a
  portfolio's starting value — as a DRAFT journal entry, **computing** the
  offsetting line against Opening Balance Equity rather than trusting the caller
  to work it out. Also flags the entry `is_opening` and, where the site offers
  the voucher type, `Opening Entry`; those are what keep opening amounts out of
  the period's activity in every report that separates the two, and nothing warns
  you when they are missing. The equity account is *found* — account number 3300
  first, then a leaf Equity account named after opening balances — and anything
  other than exactly one match is refused with the candidates listed.
- **`create_bank_account`** (mutating, default off). Creates the `Bank Account`
  record a bank feed writes into, and the `Bank` institution behind it, in one
  transaction. Refuses a GL account that is neither an Asset (a bank account) nor
  a Liability (a credit card), and refuses an Asset account whose `account_type`
  is not Bank or Cash — ERPNext's own account picker and its reconciliation tool
  both filter on that flag, so an untyped account saves fine and then cannot be
  reconciled at all. Warns, rather than refuses, when a second Bank Account would
  post to the same GL account.
- **`delete_account`** (mutating, default off, **irreversible**). Hard-deletes an
  account with no history. The complement to `disable_account`, and almost never
  the right tool — but a disabled account **still holds its account number**, and
  on a company being renumbered onto a real chart that is the entire problem.
  Four checks, all on by default, all refusals, all run before anything is
  deleted so one call reports every reason: GL entries (including journal entry
  lines on unsubmitted drafts, which write no GL row and would otherwise read as
  untouched), child accounts (disabled ones count), Company default fields, and
  Bank Account records.
- **`create_note_payable`**, **`record_loan_payment`**, **`close_note_payable`**
  (mutating, default off) and **`list_notes_payable`** (read-only, default on).
  See below.

### Added — the `Note Payable` doctype

Two doctypes: `Note Payable` and its `Note Payable Event` child table.

**Why not ERPNext's Loan module.** ERPNext's Loan models the company as the
*lender* — an application, a disbursement, a repayment schedule, its own
accounting, half a dozen doctypes. A holding company with four notes outstanding
is on the other side of every one of those.

**What it adds to the liability account that already exists.** Three things a
balance on account 2310 cannot tell you: the terms (rate, maturity, frequency),
the provenance (what was agreed, by whom, where the original is — for a family
note traced back to 2003, that sentence is the whole record), and what it
secures.

`record_loan_payment` is mostly about the split. A payment leaving a bank account
is one number whose two halves land in completely different places: one reduces a
liability, one is an expense of the period. Booked as a single line against the
liability, the year's interest expense reads as nil and the balance sheet says
the note was paid down by more than it was. Pass `principal_split`,
`interest_split`, or one and let the other be derived — they have to add up or
nothing is written.

`close_note_payable` **writes no journal entry, deliberately.** Relieving a
written-off balance is a posting with real tax consequences (forgiven debt is
usually income), and a refinance moves a balance between two liability accounts.
Both belong to somebody who meant them. The response spells out exactly which
entry is still owed and against which account, so the omission is impossible to
miss.

`principal_outstanding` on a note is a **convenience figure**. The authoritative
balance is the linked GL account, and the two diverge by every payment recorded
as a draft nobody has posted — which, in an app where nothing submits, is the
normal state. Every response that reports the field says so.

`link_asset_to_note` now recognises `Note Payable` as a link target, and
`create_note_payable(related_asset=…)` delegates to it: the same tenor check,
from the other direction, refusing by default when an asset's useful life and its
note's term disagree. The note and the link are one transaction — a refused link
leaves no note behind.

### Added — thirteen more company defaults

`set_company_defaults` supported thirteen keys and now supports twenty-six. The
new ones are the fields a module will not save a document without:

`disposal_account`, `capital_work_in_progress_account`,
`expenses_included_in_asset_valuation`, `asset_received_but_not_billed`,
`stock_adjustment_account`, `stock_received_but_not_billed`,
`unrealized_exchange_gain_loss_account`, `unrealized_profit_loss_account`,
`default_advance_received_account`, `default_advance_paid_account`,
`default_operating_cost_account`, `default_selling_cost_center`,
`default_buying_cost_center`.

`disposal_account` is the one that actually bit: ERPNext refuses to scrap or sell
an Asset without it, and reports the refusal *from the Asset*, which is not where
anybody looks. All thirteen are type-checked the same way the original thirteen
are — including `default_advance_received_account`, which looks wrong until you
see why ERPNext filters it to a **Liability** with `account_type = Receivable`:
money held for a customer is a liability, keyed so the party ledger picks it up.

No new tool, no behaviour change to the existing keys, still all-or-nothing and
still idempotent.

### Changed

- `link_asset_to_note` tries `Note Payable` first when guessing which doctype a
  note reference lives in, and its refusal now names `create_note_payable`.
- `import_chart_of_accounts` returns `new_root_accounts` (and `new_root_note`
  when it is non-empty) in both dry and live runs, and each planned root row
  carries `new_root: true`.
- `before_uninstall` warns about `Note Payable` records alongside the other
  doctypes whose contents are the only copy.

### Tests

**1112 standalone tests, all passing** (was 902).

- **`tests_standalone/test_banking.py`** — 29 tests. Every refusal in
  `create_bank_account`, the shared-GL-account warning, and that a failure leaves
  no orphan `Bank` behind.
- **`tests_standalone/test_opening.py`** — 35 tests. The plug arithmetic in both
  directions, the already-balanced case, the flags, finding the equity account by
  number and by name, and both ways of failing to find it.
- **`tests_standalone/test_notes.py`** — 70 tests. The split, the balance, the
  history, the asset tenor check from the note's side, and every disposition.
- **`tests_standalone/test_fiscal.py`** — 44 tests. Every branch of the
  company-aware overlap rule (a date-only check would wrongly refuse the
  per-company years a group structure needs; a company-only one would let a
  global year sit on top of a restricted one), the leap-day clamp, the
  orphaned-postings refusal against real GL rows, and the end-to-end case the
  tool exists for: create the year, then book into it.
- **`test_accounts.ImportCreatesNewRoots`** — the regression above, including a
  test that the flag is set on the root **and only on the root**, and a
  guards-the-guard test asserting the double still refuses a bare root (so the
  others cannot pass for the wrong reason).
- **`test_accounts.DeleteAccount`** — every check, the "report every reason at
  once" behaviour, and that the account number is actually free afterwards.
- **`test_dimensions.SetCompanyDefaultsV8`** — one test per new shape of rule.
- **`erpnext_mcp/tests/test_notes.py`** (in-bench) — that the two doctypes
  migrate and their modules import, that the controller's throws fire on the Desk
  path, that ERPNext accepts an `is_opening` journal entry and a Bank Account
  built here, that a new root account can be created against a real Account
  doctype, and — the one a double cannot show — that ERPNext really does refuse a
  posting outside every fiscal year, and accepts the same one once the year has
  been created.

Harness additions: `MandatoryError` and Frappe's mandatory pass on root accounts;
the `Bank` doctype and ERPNext's `BankAccount.autoname`; the `Note Payable`
doctypes; Fiscal Year's `year` field and its `field:year` naming rule, so a year
is named the way a real insert names it rather than by writing `name` directly;
Journal Entry's real `voucher_type` option list; and six of the thirteen new
Company default fields — the other seven deliberately absent, so the "your
ERPNext has no such field" refusal is exercised against a real absence.

## 0.7.1 — 2026-07-27

**fix: missing Python controllers for child doctypes broke `bench migrate`.**

v0.7.0 shipped `Asset Cost Center Allocation` and `Asset Depreciation Posting`
with a DocType JSON, an `__init__.py`, and no `.py` module. On a live site
`bench migrate` stopped with:

```
ModuleNotFoundError: No module named
'erpnext_mcp.erpnext_mcp.doctype.asset_depreciation_posting.asset_depreciation_posting'
```

Frappe imports `<folder>/<folder>.py` for **every** DocType it loads —
`frappe.modules.utils.load_doctype_module`, reached from `get_controller`, which
migrate calls while syncing the JSON. Child tables are not an exception. Both
tables were left without a module because neither has any server-side logic;
their rules are properties of the whole table and live on the parent,
`AssetCostProfile`. That reasoning was right about where the logic belongs and
wrong about whether the file is optional. **An empty controller is mandatory.**

Nothing else about v0.7.0 changes: no tool, no schema, no behaviour. A site that
never got past the failed migrate loses nothing by upgrading straight to 0.7.1.

### Fixed

- Added `asset_cost_center_allocation.py` and `asset_depreciation_posting.py`,
  each an empty `Document` subclass with a docstring explaining why an empty
  controller is not optional.

### Added — the tests that should have caught it

The in-bench suite asserted `frappe.db.exists("DocType", …)` for all six new
doctypes and passed. That is a different question: a row can exist for a doctype
whose module cannot be imported, and the failure sat exactly in the gap between
"the JSON is there" and "Frappe can load it".

- **`tests_standalone/test_packaging.py`** — walks the app's doctype folders on
  disk and asserts each is a package Frappe could import: `__init__.py` present,
  `<folder>.py` present, the folder name equal to the scrubbed DocType name, a
  controller class named after the DocType that subclasses `Document`, the module
  set to this app, every child table flagged `istable`, and every `Table` field
  pointing at a doctype this app actually ships. No bench needed, so CI runs it
  on every push. Verified by deleting the controller again — it fails.
- **`test_frappe_can_import_every_doctypes_module`** (in-bench) — reproduces the
  regression through the exact frame at the top of the traceback,
  `load_doctype_module`, and additionally checks `get_controller` returns the
  app's class rather than silently falling back to a base `Document`, which would
  disable every validation the controller declares.
- The standalone harness no longer special-cases child tables when resolving a
  controller, so the double now imports a module where Frappe would.

902 standalone tests, all passing.

## 0.7.0 — 2026-07-27

Family-office governance and asset accounting. Fifteen tools and six doctypes,
so the things a farm holds for a generation — who owns it, what happened to their
interest, which paper says so, and what the equipment is worth — live in the
ledger rather than in somebody's filing cabinet.

v0.6.0 made the axes a posting is filed under reachable. This release builds on
top of them: members are an anonymous accounting dimension, cost centers are
value-chain segments, and the register that maps one to a legal name is a
doctype of its own.

### The idea the whole release rests on

**The ledger stays anonymous and the register carries the names.** A chart of
accounts and a cost center tree are read by everyone who touches the books — a
bookkeeper, a lender, an auditor, a model summarising the year. A family name in
either one leaks into every export, and cannot be taken out of a statement that
has already been sent. So a posting is tagged with a Member accounting dimension
value (`Member-01`), and exactly one doctype says who that is.

Anyone who needs the mapping can be given read access to one doctype. Nobody
needs it to read the ledger. `list_cap_table` is the tool that de-anonymises the
site, and it has its own switch for that reason.

### Added — the member register

**`Cap Table Entry`** (new doctype). One row per member per company: the
anonymous id, the legal entity name, entity type, admission date, withdrawal
date, ownership percentage, an optional member cost center for sites whose
convention uses one, and notes. The docname is `"<member id> - <company abbr>"`,
the same shape ERPNext gives an Account, so the register can be found by the
identifier every posting already carries.

**`create_cap_table_entry`** (mutating, default OFF). Refuses a second entry for
the same member in the same company; refuses a percentage outside 0–100; and —
the check worth knowing about — refuses a member id that is not already a value
of the site's Member accounting dimension, naming `create_dimension_value` as
the remedy. The cap table names a member the ledger can already refer to, so the
dimension value comes first. A site with no Member dimension yet is allowed and
told so.

Cannot create a member already retired. Ownership that does not total 100% is a
warning, not a refusal: mid-transition is a real state, and a tool that refused
it would be refusing the truth.

**`update_cap_table_entry`** (mutating, default OFF). Cannot retire a member —
that is `close_cap_table_entry`, so an exit reaches the event trail rather than
appearing only as a changed checkbox. Cannot change the `member_id`: it is the
key every posting is tagged with, and changing it here would leave journal entry
lines pointing at a member that no longer exists.

**`list_cap_table`** (read-only, on by default). Retired members are **included**
by default. The postings they are tagged on do not disappear when they leave, so
neither should the row that explains them. The response totals active ownership
and says whether it comes to 100%.

**`close_cap_table_entry`** (mutating, default OFF). Sets the withdrawal date,
marks the entry retired, and writes a Withdrawal event carrying the narrative.

Deliberately **moves no money**. A member leaving usually involves a final
distribution, and that is a separate `record_member_event` call with its own
amount, accounts and narrative — bundling them would make the tool that closes a
member also a tool that can pay one.

### Added — the event trail

**`Member Event`** (new doctype). Contribution, Distribution, Admission,
Withdrawal, Transfer or Reallocation, with an effective date, an amount, the
member (and counterparty, for a transfer), the Journal Entry that books it where
there is one, a `superseded_by` link for corrections, and a **mandatory
narrative**.

The narrative is mandatory for the same reason `cancel_journal_entry` demands a
reason. A Journal Entry survives on its own; the reason for it does not. "Why
did Member-02 take 40,000 in March 2031" is the question that gets asked once
the people who knew have gone.

**`record_member_event`** (mutating, default OFF). Writes the event, and — for
the five types that book money — a **DRAFT** Journal Entry:

- Contribution: debit the cash side, credit member capital.
- Distribution / Withdrawal: debit member distributions, credit the cash side.
- Transfer / Reallocation: debit the capital of `member`, credit the capital of
  `counterparty_member`. Money never leaves the company.

**Every line carries the member dimension, including the cash side.** Tagging
only the equity line makes a balance sheet filtered by member fail to balance,
and the first person to notice that is usually an auditor.

**Accounts are shortlisted, never guessed.** With no `capital_account` given,
the company's leaf Equity accounts are matched by name; zero matches or more
than one is refused with the candidates listed. Picking the first would post a
member's capital to whichever account happened to sort first, and nobody would
find out until they read an equity statement.

Refuses without a Member dimension on `Journal Entry Account`, because an
untagged equity entry is one nobody can attribute later.

**`submit_member_event`** (mutating, default OFF). Posts the draft the event is
waiting on — and **checks two switches**. Its own, and `submit_journal_entry`'s.
That second switch is where an operator decided whether an AI client may move a
balance at all; a second door into the same room with a different lock would
make the decision meaningless.

**`list_member_events`** (read-only, on by default). Filter by member, type and
date range. Legal names are resolved from the register; the events themselves
hold only the anonymous id.

### Added — the governance archive

**`Governance Document`** (new doctype). Operating agreements, trust documents,
advisory agreements, board resolutions, prior statements and amendments, with
effective and execution dates, parties, notes, and an amendment chain.

**The chain is the point.** An operating agreement amended three times is four
documents, and the question asked in 2050 is "which one was in force in 2031".
Naming `supersedes` writes the link in both directions, so a reader can follow
the chain forward to whatever is current. The controller refuses a cycle by
walking the whole chain rather than checking one hop, and
`attach_governance_document` refuses superseding a document that has already
been superseded — an amendment goes on the end of the chain, not into the
middle.

**`attach_governance_document`** (mutating, default OFF). `file_content` is
base64 of the document's bytes, stored as a **private** File on the record;
`file_url` records where an externally hosted document lives instead. Refuses a
second document with the same company, category and title, because two entries
claiming to be the same operating agreement is worse than none.

**`list_governance_documents`** and **`get_governance_document_content`**
(read-only, on by default). Content goes through the same path
`get_attachment_content` uses, so the same read-permission check on the parent
document and the same size cap apply. A governing document is exactly the kind
of file those checks exist for.

### Added — assets, cost splits and note-tenor discipline

ERPNext already has an Asset doctype, an Asset Category and a depreciation
schedule. It does not have the two things an orchard needs.

**A cost split.** A tractor is not a Harvest asset or a Perennial Care asset; it
is 40% one and 60% the other, and its depreciation should land that way every
period without anyone re-deciding it. ERPNext files an asset under one cost
center.

**Note-tenor discipline.** When an asset is financed, the month the note is paid
off and the month the asset is fully depreciated should be the same month.
Nothing in ERPNext enforces that, and the divergence is invisible until the last
year of the loan, when interest is still being paid on something with no book
value left.

**`Asset Cost Profile`** (new doctype, with the child tables `Asset Cost Center
Allocation` and `Asset Depreciation Posting`). One profile per Asset, holding
the allocation, the schedule, the linked note and every period already written.

*A sidecar rather than custom fields, deliberately.* All of this could have been
ten custom fields and two child tables bolted onto ERPNext's Asset. The app
manifest promises that installing this app changes the behaviour of nothing
already on the site and that uninstalling it gives the site back; grafting
fields onto ERPNext's own Asset would break both halves. An asset created here
is an ordinary ERPNext Asset an operator can open, edit and delete without ever
knowing this app exists.

**`create_asset`** (mutating, default OFF). Writes the Asset (a draft), the
profile, and a fixed-asset Item when the `item_code` does not exist yet.

**`calculate_depreciation` is set to 0 on the asset, and that is the most
important line in the feature.** ERPNext runs a daily scheduled job that posts
depreciation for every asset with that flag set, using its own schedule and its
own single cost center. If it also ran here, the asset would depreciate twice —
silently, monthly, in the background. So this app owns the schedule outright,
and there is a test that reads the flag off the stored Asset for the day
somebody removes the line.

The note tenor is enforced **before anything is written**: an asset whose life
disagrees with its note is refused with both numbers, rather than created and
then found to be wrong.

Also refuses an allocation that does not total 100 (a 99% asset
under-depreciates the business for the rest of its life), a group or disabled
cost center, a frequency that does not divide the useful life exactly, a salvage
value at or above the cost, and an existing Item that is not flagged as a fixed
asset — flipping that flag on an item with stock movements is an inventory
decision, not an asset one.

**`update_asset_allocation`** (mutating, default OFF). Replaces the split. **Not
retroactive**, and that is correct: depreciation already written keeps the split
it was written with, because that is the history, and rewriting it would change
periods already reported.

**`link_asset_to_note`** (mutating, default OFF). Ties an asset to its note and,
by default, refuses the link unless life and remaining tenor agree. The tenor
comes from `note_tenor_months`, from `note_maturity_date`, or from the note
document's own maturity or term field where its doctype has one — and the
response says which. `enforce_tenor=false` links anyway and records the
divergence.

**`run_depreciation_cycle`** (mutating, default OFF). One DRAFT Journal Entry
per asset per period: debit depreciation expense split across the cost centers,
credit accumulated depreciation in one line, each debit optionally carrying a
BBCH Stage dimension value.

- **`dry_run` defaults to TRUE**, like `import_chart_of_accounts`. This is the
  one tool here that writes to many documents at once, and a catch-up over a
  year of missed periods is a page of journal entries somebody should read
  first.
- **Idempotent by record.** Every period written is stored on the profile with
  the entry that carries it, so a second run cannot repeat one. Amounts are
  computed from the profile each time rather than read back from saved rows, so
  a catch-up produces exactly what month-by-month running would have.
- **The split adds up.** The last debit absorbs the rounding, so 33.33 / 33.33 /
  33.34 of 1000 is three debits totalling exactly 1000. A journal entry that does
  not balance is not a rounding problem, it is a refused save.
- **The last period lands on the salvage value to the cent**, for declining
  balance as well as straight line. Written Down Value with a salvage value of 0
  is refused rather than fudged: the rate `1 - (salvage/cost)^(1/n)` is
  undefined, because a declining balance never reaches nought.
- One misconfigured asset does not take the run down. Assets on the Manual
  method, assets with nothing due, and assets whose depreciation accounts are
  not configured are skipped and listed with the reason.

**`depreciation_note_alignment_check`** (read-only, on by default). For every
financed asset: months elapsed, months of depreciation left, months of note
left, the delta, and a sentence saying which way it reads. Reports on every
financed asset rather than only the broken ones, because "nothing is wrong" is
an answer somebody has to be able to see.

### Changed

- `mutate.py` grew two public functions, `insert_draft_journal_entry` and
  `validated_journal_lines` (previously private). Every Journal Entry this app
  writes — from `create_journal_entry`, from a member event, from a depreciation
  run — now goes through the same insert and the same never-submitted
  assertion. A second implementation elsewhere would have been a second chance
  to ship one that posts.
- `before_uninstall` now lists every doctype whose contents go with the app, with
  a row count and an export command for each. The governance three are there for
  a reason the audit log is not: they are the **only** copy. An MCP Action Log
  row records something that also happened somewhere else; a Cap Table Entry is
  the only mapping from a member id to a legal name.

### Notes

- Fifteen new kill switches, ten of them default OFF. The five read tools ship
  on, `list_cap_table` included — an operator who wants the register unreadable
  through MCP should untick that one deliberately.
- 118 new standalone tests (894 in total), plus 13 in-bench tests covering what
  only a real site can show: that the six doctypes migrate, that the controllers'
  refusals fire from the Desk path, that a real File round-trips through Frappe's
  storage, and that ERPNext accepts both the Asset and the depreciation entry.

## 0.6.0 — 2026-07-27

Cost centers and accounting dimensions. Six tools, so the *other* axes a posting
is filed under can be built through the MCP rather than by hand in the Desk.
v0.5.0 made the chart of accounts reachable — what kind of money a transaction
is. This release makes the rest of the classification reachable: which part of
the business it belongs to, whatever else the operator needs to slice by, and
which accounts a document reaches for when nothing on it says.

### Added

**`list_cost_centers`** (read-only, on by default). One company's cost centers as
a nested tree, in the same shape `get_chart_of_accounts` returns. Disabled cost
centers are left out and *counted*, in `disabled_count_excluded`, so "the tree
looks short" always has an answer rather than being a silent omission.

**`create_cost_center`** (mutating, default OFF). One cost center under an
existing group. Refuses before writing if the parent is missing, is a leaf, or
belongs to another company, or if the number is taken in that company.

Cannot casually add a root. ERPNext gives every company exactly one root cost
center and requires it to be named exactly after the company
(`CostCenter.validate_mandatory`), so omitting `parent_cost_center` on a company
that already has one is refused with the existing root named — which is nearly
always what a caller who forgot the parent needs to see. A company with no cost
centers at all can still be given its root.

**`update_cost_center`** (mutating, default OFF). Rename, renumber,
disable/enable. The docname moves with the fields, in that order, for the reason
set out at the top of `tools/accounts.py`: a Cost Center's key encodes two of its
own fields and is built once by `autoname`, so changing one without the other
leaves the tree showing one thing and reporting another, permanently.

Hand-rolled rather than delegated, unlike `update_account`, and that is a
decision rather than an omission. ERPNext's own helper
(`accounts.utils.update_number_field`) handles only the *number*, and the
compensating behaviour that makes delegation matter for Account — syncing a
rename down into child companies — has no cost-center equivalent to reproduce.
The naming rule is identical to Account's, and an in-bench test asserts that a
real insert produces exactly what this app predicts.

Deliberately cannot reparent, and this release ships no `move_cost_center`:
reparenting moves no posting but changes which subtotal every existing one rolls
up into, retroactively, for periods already reported. Also refuses to rename the
company's root. Disabling deletes nothing and says so — the response carries the
GL entry count, and, for a group, that its children were **not** disabled.

**`create_accounting_dimension`** (mutating, default OFF). The one to read the
description of before enabling.

An ERPNext Accounting Dimension does not hold its own values: it **points at a
DocType**, and every record of that DocType is a value. So this tool writes up to
three things, in one transaction so a failure leaves none of them — the master
DocType (only when asked for, via `create_master_if_missing`), the Accounting
Dimension record, and one Link Custom Field per target doctype.

- **A generated master is a custom DocType** (`custom: 1`): it lives entirely in
  the database, writes no files into an app and needs no developer mode, and an
  operator can delete it from the Desk. It is named `field:dimension_value`, so
  the record's own name *is* the value and `Member-01` reads as `Member-01`
  everywhere it is linked rather than as `MEM-00001`.
- **The custom fields are written here rather than left to ERPNext.** Inserting
  an Accounting Dimension makes ERPNext enqueue its own field-creation routine as
  a *background job* over its own fixed hook list. Both halves are wrong for an
  MCP caller: the next call is usually a Journal Entry that needs the field to
  exist now, and the caller asked for a specific set of doctypes. ERPNext's job
  still runs and still creates the rest of its list; both paths check for an
  existing field first, so they do not collide.
- **"Journal Entry" means the line.** ERPNext carries dimensions on `Journal
  Entry Account`, never on the header, because one entry books to several. Asking
  for `"Journal Entry"` wires up the child table and the response reports the
  redirection in `redirected`, rather than putting a field on a header that
  nothing would ever read.

Refuses a dimension that already exists for that label or that DocType (ERPNext
allows one per DocType — its values *are* that DocType's records), a master that
is a Single, a child table or a core doctype, a target doctype this site does not
have, and any target that already has a field of that name which is not a Link to
this master. Every one of those is checked before anything is written: a
half-wired dimension is worse than none, because it looks configured.

**`create_dimension_value`** (mutating, default OFF). One record in the DocType a
dimension points at. Finds the dimension by its label, by its DocType or by its
docname — three ways because the Accounting Dimension record's own docname is a
version detail, and a caller who created it through this app knows it by the
label it asked for. `extra_fields` is applied verbatim, with every key checked
against the master's own fields; an unknown one is a typo and is refused by name.

**`set_company_defaults`** (mutating, default OFF, idempotent). Points a
Company's default account and cost center fields at real accounts, in one call:
receivable, payable, cash, bank, income, expense, COGS, round-off (account and
cost center), exchange gain/loss, write-off, and deferred revenue/expense.

**Type-checked, not merely existence-checked**, and that is the whole point.
ERPNext keys party ledgers and every ageing report off `account_type` rather than
off an account's name or number, so a `default_receivable_account` pointed at a
plain Asset account produces invoices that post but never age — and the symptom
appears a quarter later with nothing to point at. Each field also has to match
the right root type. Group accounts, disabled accounts, accounts belonging to
another company and group cost centers are all refused, as is a key this ERPNext
version's Company does not have.

Nothing is written unless *every* value in the request validates, so a
partially-correct call leaves the company exactly as it was. And every field is
compared before it is written, so a re-run changes nothing and says so — which
matters more than usual because `Company.save` is not a cheap write.

### Changed

**`create_journal_entry` accepts a per-line `dimensions` object.** Custom
accounting dimensions go in `{"member": "Member-01", "bbch_stage": "BBCH-8"}` on
the line, not alongside `debit` and `cost_center`.

The separate door is deliberate. A dimension's fieldname is invented by whoever
created it, so there is no list this app could ship; but simply accepting unknown
per-line keys would turn `amount` — which a model will send, meaning `debit` —
from a corrected mistake into a silently dropped one. Unknown top-level keys stay
refused by name; passing a key through `dimensions` is an assertion that the
caller meant a dimension.

Both halves are then checked against the site itself: the field has to exist on
`Journal Entry Account`, and a Link value has to be a record of what it links to.
Without the first, a dimension nobody created yet would be written to an
attribute that never reaches a column and the entry would look filed and not be.
Without the second, ERPNext's own link validation runs on *submit*, so a bad
value would produce a draft that cannot be posted rather than a call that failed.
The response reports `dimension_fields_set`.

**`args.resolve_cost_center`** joins `resolve_account`: a cost center can be
named by its docname, its number or its name, anywhere one is taken. Unlike the
account resolver it checks that `cost_center_number` exists on the site before
filtering on it — account numbers predate every ERPNext this app supports, cost
center numbers do not, and selecting a missing column is a hard SQL error rather
than an empty result.

**`compat.field_meta`** returns a field's definition rather than only whether it
exists, which is what lets the dimension paths check a value against the DocType
a Link actually points at.

### Notes

Six new switches on the settings form — `list_cost_centers` on by default,
`create_cost_center`, `update_cost_center`, `create_accounting_dimension`,
`create_dimension_value` and `set_company_defaults` off — seeded by the existing
`after_migrate` hook, so no bespoke patch. `create_accounting_dimension` is the
only switch in this app that can add a DocType to a site, and only when a call
asks for it explicitly; it is the narrowest one to leave off.

The catalogue is now 49 tools: 32 read-only, 17 mutating.

The standalone test double gained real schema mutation to cover this: inserting
a DocType makes it creatable, and inserting a Custom Field makes
`frappe.get_meta` report the field, with the schema reset between tests. Without
that, the case the whole feature exists for — create a dimension, create a value,
put it on a journal entry line, read it back off the stored document — could not
have been written at all.

## 0.5.0 — 2026-07-27

Chart-of-accounts management. Six tools, so a complete ERPNext chart can be
built, corrected and retired entirely through the MCP instead of by hand in the
Desk.

### Added

**`propose_clean_chart`** (read-only, on by default). Returns a complete
numbered chart for a company from a static template, in the exact JSON shape
`import_chart_of_accounts` takes — so the review step is "read this, delete what
you do not want, pass it back". It also reports what the import would collide
with: the company's existing root accounts, and every template number already in
use. Templates live in `erpnext_mcp/charts/` and are pure Python literals with
no database dependency, which is what makes the proposal reviewable before
anything runs.

The one shipped template is **`us_llc_farm`** — 81 accounts (17 groups, 64
ledgers) for a US farming LLC that also runs an investment book. Compact by
design: nine flat operating-expense buckets and at most two levels of grouping,
because a chart with a line for every conceivable cost is one where nobody finds
the right line.

- **Crop labour is separated from administrative wages** (`5150` vs `6100`), and
  the employer's payroll tax splits out again at `6150` so wage cost and true
  cost of employment read apart — and neither is confused with `2140 Payroll Tax
  Withholdings`, which is employees' money and a liability.
- **The trading segment is a range set**: assets `1800-1849`, income
  `4200-4249`, losses and costs `7300-7339`, unrealised movement `3500`. Filter
  a P&L to those and you have the investment book — running costs included,
  since advisory (`7320`) and custodian/brokerage fees (`7330`) sit inside the
  segment rather than with the farm's professional services. Open option
  contracts get their own asset account so a covered-call programme's exposure
  is visible without unpicking it from the underlying equity, and their losses
  their own expense account (`7310`) because options and equity capital losses
  can be taxed differently. `1130 Cash Clearing - Brokerage` is the one account
  whose name reads as trading while deliberately sitting outside the segment —
  it is a bridge for paired brokerage/companion transactions and should hold
  zero.
- **`2120 Current Pay Period - Due to Employees`** is a live, continuously
  updated balance of what is owed for work already performed this period, not a
  period-end accrual. Its description says so explicitly, because the account
  only keeps that meaning if nobody drops a month-end adjusting entry into it.
- **Property tax appears in all three places it lives** — accrued (`2170`),
  prepaid (`1420`), expensed (`6650`).
- **`1830 Brokerage Cash & Money Market` ships as an empty group**, to be filled
  with one child per linked brokerage cash-services account. Which accounts
  exist is a property of the install rather than of the template, and a single
  combined ledger would leave a paired-brokerage feed no way to say which
  account a movement belongs to.

The package auto-discovers templates the way `packets/` does, so `us_c_corp`,
`us_s_corp` and `us_partnership` are a file drop each.

**`create_account`** (mutating, default OFF). One account under an existing
group. Refuses before writing if the parent is missing or is a ledger, if
`root_type` disagrees with the parent's, if the number is taken in that company,
or if the `account_type` cannot sit under that `root_type`.

**`update_account`** (mutating, default OFF). Rename, renumber, re-type,
enable/disable. Deliberately cannot reparent.

**`move_account`** (mutating, default OFF). Reparent, and nothing else. Separate
from `update_account` so a bad move cannot happen as a side effect of a rename —
reparenting moves no GL entry but changes which subtotal every existing posting
rolls up into, retroactively, for periods already reported.

**`disable_account`** (mutating, default OFF). ERPNext's soft delete, with a
mandatory reason written to the document and the audit log. **Refuses any
account carrying GL entries in the current fiscal year**, which is the line
between tidying the chart and breaking this year's reports.

**`import_chart_of_accounts`** (mutating, default OFF). Builds a whole tree in
one transaction, parents before children, rolling back entirely on any failure —
a half-imported chart has orphaned groups in it. **`dry_run` defaults to true**
and that default is load-bearing: an accidental call must not be able to
rearrange a live chart. A dry run returns the full ordered plan with the docname
each account would get, and marks every existing account as either a safe skip
(same number, same name, so re-running an import is idempotent) or a conflict to
fix first. Because one bad group takes its whole subtree with it, a dry run also
returns `blocking_problems` — the causes alone, separated from the fallout.

Expect collisions on a company created from a bundled ERPNext chart: "Standard
with Numbers" numbers its own roots 1000/2000/3000/4000/5000, which is the same
convention `us_llc_farm` uses. `propose_clean_chart` names every number already
taken and says what to do about it.

### Fixed

**`advance_workflow` read an unparseable `dry_run` as false.** The old private
coercion mapped anything it did not recognise to False, so `dry_run="sure"`
executed a live workflow transition — which can submit or cancel a document.
Boolean arguments now go through `args.as_bool`, which returns the caller's
default when the argument is absent and raises otherwise. `bool("false")` and
`bool("0")` are both True in Python, and any coercion that goes through
truthiness gets them backwards; this one does not.

### Notes for operators

Six new switches in a **Chart of Accounts** section on ERPNext MCP Settings.
Five are write tools and ship off; `propose_clean_chart` sits with the read
tools and ships on. Run `bench --site <site> migrate` after updating.

Importing a chart **adds** roots alongside whatever the company already has
rather than replacing them — ERPNext treats a root account as uneditable once
created. Plan to disable the bundled defaults afterwards, which is what
`disable_account` is for.

### Under the hood

`frappe.rename_doc` on an Account is not sufficient on its own. The docname
encodes `account_number` and `account_name` and is never rebuilt after insert,
so renaming the document leaves the fields stale and setting the fields leaves
the docname stale — permanently, in both directions. `update_account` therefore
delegates to ERPNext's own `update_account_number`, which does both halves in
the right order and also syncs the change into child companies in a group
structure; the hand-rolled two-step is a fallback for versions that predate it.
Documented in `docs/development.md` and at the top of `erpnext_mcp/tools/accounts.py`.

The standalone double now models `Account` faithfully — ERPNext's autoname, the
"Root cannot be edited" refusal, and the parent-must-be-a-group check — for the
reason this project has learned three times: where the double is more permissive
than the framework, tests pass and sites break.

## 0.4.1 — 2026-07-26

Two bugs in the v0.4.0 connection panel, both found by adding a second Umbrel
reached at a bare IP.

### Fixed

**The generated URL lost its port.** The panel emitted
`http://100.69.162.122/api/method/...` where the operator needed
`http://100.69.162.122:5300/...`, and the resulting config fails silently.

The port was not being dropped — **it never arrived**. frappe_docker's nginx
proxies with `proxy_set_header Host $host`, and nginx's `$host` is the
*normalised* host: lowercased, port removed (`$http_host` is the raw one). By the
time Python sees the request, `frappe.local.request.host` is already portless and
`frappe.utils.get_url()` has nothing to preserve. Worse, the port `get_url()`
*would* append in that branch is `frappe.conf.http_port or webserver_port` — the
container-internal 8000, not the published 5300. A published Docker port is a
property of the compose file and nothing inside the container can see it.

So the port now comes from the one component that was outside: the browser
rendering the settings form reached the site at the very address the operator
will paste into a client, and its `Origin` header (or `Referer`, for the download
link, which carries no Origin) has that address with the port intact.

**A bare-IP URL may not route.** Frappe picks a site from the request Host, and
an IP matches no site directory — so a client can get "site not found" while the
operator's own browser works fine, which is a baffling asymmetry to debug. The
panel now shows a red banner naming all three fixes: `default_site` in
common_site_config.json, a `host_name` that resolves for clients, or Public URL.
It stays quiet when `default_site` is set, when a proxy pins
`X-Frappe-Site-Name` (that proxy serves the MCP client too), or when the host is
a name rather than an address.

### Changed

URL derivation is now an ordered candidate list rather than a single call, and
the panel reports which one won and what else was available:

1. `public_url` — the explicit override, unchanged
2. `host_name` from site config — the name Frappe itself prefers, and the one
   that routes on a multi-site bench. If it has no port and the browser's origin
   names the *same host* with one, the port is borrowed; a `host_name` pointing
   elsewhere is never given a port that is not its own.
3. the browser's `Origin` / `Referer`
4. `X-Forwarded-Host` / `-Port` / `-Proto`
5. the request Host
6. `frappe.utils.get_url()` — now the last resort rather than the first choice

The one visible behaviour change beyond the fixes: `url_source` reads
`request Host` rather than `frappe.utils.get_url()` on a plain site. Same URL,
more accurate label.

### Tests

572 standalone (was 551), 179 in-bench (was 172).

## 0.4.0 — 2026-07-26

A **Connect to Claude Desktop** panel on the settings form. No new MCP tools —
still 37 — this is the last mile of installation.

### Added

- **`Connect to Claude Desktop` section** on ERPNext MCP Settings, shown once the
  master switch is on. It renders the `claude_desktop_config.json` entry built
  from this site's own URL and token, the default config-file path for macOS,
  Windows and Linux (with the platform the browser reports highlighted), and the
  three next steps: save, fully quit and reopen Claude Desktop, then ask for the
  company topology.
- **Copy config JSON**, **Download config file** and **Reveal for copy**
  buttons, plus a **Connect from Claude Code** subsection with the equivalent
  `claude mcp add` one-liner and its own copy button.
- **`public_url`** field. `frappe.utils.get_url()` is correct for the server and
  useless to a client on a site behind a Tailscale Funnel, a tunnel or a reverse
  proxy on another hostname, and there is no way to detect that from inside a
  request — so it is a field an operator fills in, and the panel prefers it. The
  payload says which source it used.
- **`erpnext_mcp.onboarding`**, with two whitelisted methods:
  `claude_desktop_config(reveal=0)` and
  `download_claude_desktop_config()` (GET, `Content-Disposition: attachment`).
  Both `frappe.only_for("System Manager")`.

### Notes on the token

This is the only place in the app that hands a plaintext token back to a caller,
so the reasoning is worth stating. The gate is the same role that can open the
form — somebody who can read this panel could press **Generate New Token** and
read the result anyway, so nothing new is being given away.

Everything else is belt. The preview renders masked (`••••••••…wxyz`), so the
panel is safe on a shared screen or in a screenshot, while **Copy** and
**Download** fetch the real value separately — an operator never has to choose
between a working config and a safe screen. The token is never put in a URL: the
download is a GET whose *response* carries it, so it stays out of proxy logs and
browser history. The masked payload is asserted not to contain the token, in both
suites.

`--allow-http` is emitted only for an `http://` endpoint. `mcp-remote` refuses a
non-HTTPS origin without it, and including it on an HTTPS config is noise that
invites the question "why is this allowing http".

### Tests

551 standalone (was 514), 172 in-bench (was 156).

## 0.3.0 — 2026-07-26

**37 tools** (was 35): a compliance-packet framework with two packet types, plus
`dry_run` on `advance_workflow` and end-to-end verification of the workflow tools
against real Frappe.

### Added — compliance packets

A packet is an *artefact*, not an answer: a structured JSON document for somebody
who has to sign something off. Three properties distinguish it from a query —
it says how it was made (`generated_at`, `generated_by`, `site`,
`generator_version` and the `mcp_action_log_id` of the call that produced it), it
never truncates quietly (any cap that bites raises a WARN naming the number
omitted), and it reports what is wrong with itself in `flags` (INFO / WARN /
ERROR, where ERROR means the numbers do not internally agree and the packet
should not be signed).

- **`generate_compliance_packet(packet_type, filters)`** — builds one and returns
  it inline. Nothing is stored, emailed or filed.
- **`list_compliance_packets()`** — discovery. Packet types are site-dependent
  and each has its own switch, so a client needs to ask rather than guess.
- **`reconciliation_packet`** (`account`, `period_start`, `period_end`,
  `company?`) — opening and closing balances, movement summary, every Journal
  Entry that touched the account, the drafts that would change it, and the
  cancellations a balance query cannot see. Checks `opening + net == closing` from
  two independent aggregates and raises ERROR if they disagree. Detects cancelled
  entries, unposted drafts, unbalanced entries, negative-balance dates, quiet
  periods, future-dated postings and outsized single entries. `external_sources`
  ships empty, ready for Bank Bridge variance in v0.4.
- **`fiscal_year_audit_packet`** (`company`, `fiscal_year`) — trial balance with
  each row stating its own basis (balance-sheet accounts cumulative,
  profit-and-loss within the year), income statement, balance sheet, twenty
  largest entries, intercompany activity found by resolving every line's account
  to its company, and document counts. Checks that cumulative debits equal
  credits, and that `Assets - (Liabilities + Equity) = Income - Expense`.

Adding a packet type is a single file drop in `erpnext_mcp/packets/` — the
package auto-discovers every module that registers a `PacketSpec`, so there is
no list to update and no handler to touch. Roadmap types (payroll,
organic-transition, tax-year, SOX) need nothing else.

### Added — workflow verification

- **`advance_workflow` gains `dry_run`.** It reports the target state, whether
  the document would be **submitted** or **cancelled**, the effects in plain
  words, and whether the action is even available — without executing. A dry run
  never raises for an unavailable action: "it would be refused, and here is why"
  is the answer to the question, not a failure to answer it. The intended pattern
  is dry-run, show the human, then execute.
- **`advance_workflow`'s description now states the risk model**: a transition
  into a `doc_status: 1` state submits the document, which on a Journal Entry
  writes GL Entries and moves balances, and what a given action does is a
  property of the site's workflow design rather than of the tool.
- **A real in-bench workflow suite** (`test_workflow_scenarios.py`) that builds a
  custom submittable DocType, four Workflow States, three Workflow Actions, two
  Roles, two Users and a Workflow, then walks documents through it: happy path,
  permission denial, condition failure, self-approval denial, a submit that fails
  validation, terminal states, and two workflows on one DocType.

### Fixed

- **`list_available_actions` and `dry_run` over-promised on self-approval.**
  Frappe's `get_transitions` filters on role and condition only — the
  `allow_self_approval` rule is enforced inside `apply_workflow` and throws at
  execution time. So the tools advertised an action the acting user could not
  take, and a dry run reported `would_succeed: true` for a transition destined to
  throw. Both now apply Frappe's rule up front, and `list_available_actions`
  reports what it withheld and why. Found by writing the in-bench suite; pinned
  by a test that fails if a future Frappe starts filtering earlier.
- **Two active Workflows on one DocType are now refused rather than resolved
  arbitrarily.** Frappe deactivates the others when you save one active, so this
  only arises from a direct database edit — but "which workflow governs this
  document" has no defined answer there, and guessing on a submitting transition
  is unrecoverable.
- The standalone double enforced self-approval in the wrong place, which is why
  the defect above survived v0.2. It now matches Frappe.
- The standalone fixture's ledger did not balance — a 500 debit with no
  counterpart. `fiscal_year_audit_packet` found it on its first run.

### Tests

514 standalone (was 443), 156 in-bench (was 103).

## 0.2.1 — 2026-07-25

Hotfix. **v0.2.0 breaks `bench migrate` on any site it is installed on** — if you
are on v0.2.0, upgrade before your next migrate.

### Fixed

- **`after_migrate` crashed with `Unknown column 'modified' in 'ORDER BY'`.**
  `settings.seed_defaults` read `tabSingles` through `frappe.db.get_values`
  without an `order_by`. That helper — and `get_value`, which is `get_values`
  with `limit=1` underneath — defaults to ordering by `modified`. `tabSingles` is
  not a DocType table: it has three columns, `doctype`, `field` and `value`, and
  none of the framework columns. Every `bench migrate` on an installed site died
  in the hook.

  Both reads now go through `frappe.db.get_singles_dict`, the framework's own
  accessor for that table, which issues no `ORDER BY` at all. Preferred over
  passing `order_by=None` because there is then no default left to get wrong.

- **A second instance of the same pattern** in the in-bench suite
  (`test_the_ciphertext_is_not_the_plaintext` used
  `frappe.db.get_value("Singles", …)`), which would have failed the same way the
  first time anyone ran `bench run-tests` on a real site.

### Why it shipped, and what stops the next one

The standalone test double answered a query MariaDB refuses, so three existing
`seed_defaults` tests passed against broken code. The double now models
`tabSingles` — and the other frameworkless tables — as having no framework
columns, and raises the real error when a query would default to ordering by
`modified`. Those three tests now fail against v0.2.0, alongside five new ones:

- `after_migrate` and the `patches.txt` patch each run end to end, standalone
  **and** in-bench against a real database. The hook that broke had no test at
  all; it does now.
- A grep-as-a-test fails if any source file queries `Singles` through
  `get_value` / `get_values` / `get_all` / `get_list` again.
- An in-bench test asserts `DESC tabSingles` really is those three columns, so
  the reason for all of the above is demonstrated rather than remembered.

Also fixed: `__version__` still read `"0.1.0"` after the v0.2.0 tag, so the MCP
handshake reported the wrong server version to every client. A test now compares
it against the newest CHANGELOG heading.

No behaviour, tool or API changes. 443 standalone tests (was 433), 103 in-bench
(was 96).

## 0.2.0 — 2026-07-25

**35 tools** (was 15): workflow, reports, attachments, comments and tasks, HR,
sales and purchasing, and site-customisation metadata.

### Added — tools

**Workflow** (4 read, 1 write)
`list_workflows`, `get_workflow_state`, `list_pending_approvals`,
`list_available_actions`, and `advance_workflow` (**MUTATING**, default off).
Transition availability and the action itself go through Frappe's own
`get_transitions` / `apply_workflow`, so conditions, the self-approval rule and
the resulting docstatus change behave exactly as the Desk button does.

**Reports** (2 read)
`list_reports`, `run_report`. Query and Script Reports run through
`frappe.desk.query_report.run` (with `ignore_prepared_report`, so a prepared
report returns rows rather than a job id); Report Builder reports are
materialised from their saved column and filter config via
`frappe.desk.reportview.get`, falling back to `frappe.get_list`. Old-style
`"Label:Fieldtype/Options:Width"` columns are parsed into objects.

**Attachments** (2 read)
`list_attachments`, `get_attachment_content`. Both check `read` permission on
the parent document; an unattached private file is treated as its owner's.
Content is base64, capped at 2 MB by default and 8 MB absolutely.

**Comments and tasks** (2 read, 1 write)
`list_comments`, `list_assigned_todos`, and `create_todo` (**MUTATING**, default
off). ToDo's `allocated_to`-vs-`owner` split and its missing `subject` field are
both normalised, and the response says which happened.

**HR** (3 read, only where `hrms` is installed)
`list_employees`, `get_attendance_summary`, `get_leave_balance`. Attendance is
aggregated per employee rather than returned day by day. Leave balances come
from HR's own `get_leave_balance_on`, so carry-forward and expiry rules apply.

**Sales and purchasing** (3 read)
`list_sales_orders`, `get_outstanding_invoices`, `list_purchase_orders`.
Receivables are aged into `current` / `0-30` / `31-60` / `61-90` / `90+` /
`unknown`; not-yet-due invoices get their own bucket rather than inflating
`0-30`.

**Site customisation** (2 read)
`list_custom_fields`, `list_client_scripts`. Script bodies are truncated to 500
characters with the real length reported.

### Added — behaviour

- **Availability predicates.** A tool can declare a site prerequisite. One that
  is unmet is not advertised in `tools/list` at all and cannot be called — a
  tool that is listed and always fails is a trap for a model. Applied to the HR
  tools (`hrms`), the sales/purchasing tools (`erpnext`), `get_bank_statement`
  (the Bank Statement doctype) and `list_client_scripts` (Client Script, or
  Custom Script on pre-v13). Refusals distinguish "your operator turned this
  off" from "this site does not have that", because those need different
  actions.
- `selftest` reports `tools_unavailable`, and the settings form shows it.
- New whitelisted `erpnext_mcp.mcp.mutating_tool_names`, so the settings form's
  "write tools are live" banner is derived from the registry instead of a
  hardcoded copy in JavaScript.
- Settings form grouped into sections: Connection, Network, Attribution,
  Accounting Read/Write, Workflow, Reports, Attachments, Comments & Tasks, HR,
  Sales & Purchasing, Meta.

### Changed

- **`X-MCP-Token` is now the documented header.** Frappe's auth layer routes
  `Authorization: Bearer` into its OAuth2 validator before a whitelisted method
  runs, and an MCP token does not survive that on every version — confirmed on a
  live v15 site. `X-MCP-Token` is a header Frappe has no opinion about.
  `Authorization: Bearer` is still accepted, second, and wins nothing when both
  are sent.
- `list_client_scripts`' availability predicate now covers `Custom Script` too,
  matching the fallback the tool already implemented.

### Fixed

- `max_bytes=0` on `get_attachment_content` was silently replaced by the default
  instead of being refused (`x or DEFAULT` swallows an explicit zero). Same
  pattern removed from `as_limit`.
- An explicitly empty `status` now means "every status" on `list_employees` and
  `list_assigned_todos`, as their descriptions promised. `as_str`'s default
  fired on `""` as well as on absent; the new `as_filter` distinguishes them.

### Packaging

`CONTRIBUTING.md`, GitHub issue and pull-request templates, and a GitHub Actions
workflow running the standalone suite on Python 3.10 and 3.11 plus `ruff check`,
`ruff format --check` and an SPDX-header check. README gains a compatibility
matrix, the full 35-tool catalogue, a roadmap and badges.

### Tests

433 standalone (was 228), 96 in-bench (was 53).

## 0.1.0 — 2026-07-24

Initial release: 15 tools, the `ERPNext MCP Settings` and `MCP Action Log`
doctypes.

An MCP server that installs into any Frappe/ERPNext bench as a custom app. One
whitelisted endpoint, two doctypes, no hooks that change existing behaviour.

**Tools.** Read-only, all on by default: `get_company_topology`,
`get_account_balance`, `get_journal_entries`, `get_journal_entry`,
`list_bank_transactions`, `get_bank_statement`, `list_fiscal_years`,
`get_chart_of_accounts`, `list_unreconciled_bank_transactions`,
`search_accounts`. Mutating, all off by default: `create_journal_entry` (draft
only), `submit_journal_entry`, `cancel_journal_entry`, `create_bank_transaction`
(draft only), `reconcile_bank_transaction`.

**Security.** Master switch (off ⇒ 404), token in a Password field
(constant-time compare), CIDR allowlist defaulting to loopback plus RFC1918.
Rejections are opaque to the caller and specific in the audit log. The CIDR gate
reads the rightmost `X-Forwarded-For` hop, the one a client cannot forge.

**Audit.** `MCP Action Log` records every call — reads, writes, refusals and
unknown tools — append-only, with a failure row committed after the failed work
is rolled back so the attempt is recorded even though it did not happen.

**Compatibility.** Frappe/ERPNext v14–v16, Python 3.10+. Field and doctype
presence is read from the site's own schema rather than pinned.

**Tests.** 228 standalone (no bench required) plus an in-bench `FrappeTestCase`
suite covering migration, encryption, real ERPNext validation and permission
enforcement.
