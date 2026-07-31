<!-- SPDX-License-Identifier: MIT -->
# Compliance fields on operational DocTypes

**v0.15.0.** Every field this app adds to a DocType it did not create, which
framework wants it, and — the part that matters — what breaks in the day-to-day
work if it is missing.

---

## Why this file exists, and why the fields are where they are

erpnext_mcp promises that installing it adds no field to any DocType it did not
create. `hooks.py` says so, `before_uninstall` is built around it, and v0.7.0's
asset tooling keeps its cost split in an `Asset Cost Profile` beside ERPNext's
Asset rather than in custom fields grafted onto it — because a DocType of ours
goes with the app and a field on theirs does not.

**Sprint 7 breaks that promise, once, on purpose.**

Compliance is a lens on operational data rather than a duplicate set of records.
Every spray IS an EPA and Worker Protection Standard record. Every hire IS an
I-9 record. Every bucket IS an FSMA traceability record. The bolt-on version of
this feature is a "Spray Compliance Log" that somebody fills in *after* doing the
spraying, and it fails the only test that matters:

> Does removing the feature break **operations**, or only break **compliance
> reporting**?
>
> * breaks operations too → compliance is woven in correctly
> * only breaks reporting → it is a shadow layer; refactor

A shadow log drifts from reality the first busy week of harvest, and an auditor
who finds two records of one spray that disagree has found something far worse
than a missing field. So the applicator's name, the EPA registration number, the
restricted-entry interval and the pre-harvest interval go **on the spray
record** — where the person doing the spraying already is, and where leaving them
blank stops the spray being recorded at all.

The last column of every table below is that test, answered per field. A field
whose honest answer is "nothing breaks" is a shadow field and belongs in one of
the four external-evidence DocTypes instead. There is a test — `test_compliance_fields.py`
— that requires the sentence to exist, so a field cannot be added without
somebody confronting the question.

## What it costs, said plainly

Uninstalling erpnext_mcp from a site where these have been filled in **drops the
columns and everything typed into them.** The records themselves — the spray
logs, the employees, the bucket log entries — survive; the applicator names, EPA
registration numbers, REIs, PHIs, I-9 statuses and traceability links do not.
`before_uninstall` names every column by hand before it happens, with the
`bench backup --only-doctype` lines to run first.

That is a real cost and it is the right trade. An app that refuses to touch
anybody else's DocType cannot make compliance fundamental to operations; it can
only make it adjacent to them.

## How they are added

Every field is a **Custom Field**, which is Frappe's supported way for one app to
extend another's DocType. The target app's repository is untouched, its own
migrations keep working, and a later version of farm_precision_ag that ships
`epa_reg_number` itself finds this one already there rather than ending up with
two columns — the check is "is the field present at all", not "is there a Custom
Field row we wrote".

The installer runs on **every** `bench migrate` and is a no-op on the second run.
`install_compliance_fields` is the same installer on demand, with a `dry_run`
that reports what would happen without doing it.

It is behind `allow_install_compliance_fields`, which is the **only** mutating
switch in this app that ships ON — because a compliance field that arrives only
when an operator remembers to tick a box is a compliance field that is missing on
the sites that needed it most. Turn it off and no field is ever added, through
the hook or through the tool. `registry.DEFAULT_ON_MUTATING_TOOLS` names the
exception and argues for it, and a test asserts it is the only one.

## Graceful degradation

A DocType that is not on this site is **skipped by name**, with the app that
would bring it. A site without farm_precision_ag has no Spray Log and is told so;
it is not a failure and nothing else is disturbed. Install the owning app, run
the tool again, and only the newly-possible fields are added.

## The required fields, and the backlog they create

Seven of the twenty-four are required. Frappe enforces `reqd` **on save**, not
retroactively — so existing records stay readable and stop being re-saveable
until somebody fills the field in. That is the intended behaviour: a spray record
that never had an applicator was never compliant, and the field makes that
impossible to paper over.

It is also a surprise if nobody says it first, so the installer counts the rows.
`install_compliance_fields` reports `backlog` per field and `backlog_total`
across all of them. **That number is the operation's compliance debt, stated in
rows,** and it is the most useful thing either the hook or the tool produces on a
site with history.

---

### `Spray Log` — farm_precision_ag

Pesticide application records under FIFRA, the EPA Worker Protection Standard and Oregon's ORS 634. Every spray is a compliance event; these are the columns that make it one.

| Field | Type | Required | Framework | Why the regulator wants it | What breaks in the WORK without it |
| --- | --- | --- | --- | --- | --- |
| `applicator_name` | Data | **yes** | EPA WPS 40 CFR 170.309(f); ORS 634 / OAR 603-057 | Federal and Oregon pesticide records must name the person who made the application. Oregon additionally ties the record to a licensed applicator. | Nobody can be asked what the tank actually held, whether the nozzles were the high- or low-volume set, or why a block was skipped. The applicator is the only person who knows what happened in the field that day. |
| `epa_reg_number` | Data | **yes** | FIFRA; EPA WPS 40 CFR 170.309(f)(3) | The registration number identifies the product as registered for this crop and this use. It is the number a residue detection is traced back through. | The label is the law: without the registration number nothing downstream can check the product against the crop, the rate or the buyer's maximum residue limit, so a load can be rejected at the packing house with no way to find out which block it came from. |
| `rei_hours` | Int | **yes** | EPA WPS 40 CFR 170.407 — restricted-entry interval | The interval during which workers may not enter the treated area without PPE. Posting and notification obligations run off it. | THE crew-scheduling number. Without it nobody knows when the block can be picked, thinned or irrigated, and the crew boss guesses. This is the field that makes the compliance record and the work order the same record. |
| `phi_hours` | Int | **yes** | FIFRA label; FDA tolerances 40 CFR 180 | The pre-harvest interval: how long after application the fruit may not be picked. Violating it is a residue violation on a shipped load. | Harvest scheduling. A block sprayed inside its PHI cannot be picked, and the pick date is planned off this number weeks in advance. |
| `weather_temp_f` | Float | no | EPA WPS 40 CFR 170.309; label temperature restrictions | Many labels restrict application above a stated temperature, and an inversion is the usual cause of an off-target drift complaint. | Efficacy. Half the products in a tank behave differently at 90°F, and the reason a spray did not work is read out of this column the following week. |
| `weather_wind_mph` | Float | no | EPA label drift restrictions; ODA drift investigations | Nearly every label sets a maximum wind speed. It is the first thing an Oregon Department of Agriculture drift investigation asks for. | Whether to spray at all that morning, and the defence when a neighbour complains. Without it a drift complaint is unanswerable. |
| `wind_direction` | Data | no | EPA label drift restrictions; ODA drift investigations | Direction is what turns a wind speed into a statement about where the spray went, and about which neighbouring property was downwind. | Which end of the block to start at, and which rows to leave for a calmer day. A drift complaint from upwind answers itself. |
| `target_pest` | Data | no | FIFRA label use; IPM records for GAP / GlobalGAP | A product applied for a pest not on its label is an off-label application. Food safety audits ask for the IPM justification for every application. | The IPM loop. The threshold that triggered the spray and the assessment of whether it worked both key off the target pest; without it the next application is chosen blind. |

### `Employee` — farm_hr / hrms

Employment eligibility, tax withholding, the wage law that governs this person's pay, and farm labor contractor licensing. Every hire is a compliance event.

| Field | Type | Required | Framework | Why the regulator wants it | What breaks in the WORK without it |
| --- | --- | --- | --- | --- | --- |
| `i9_status` | Select<br>`Verified` `Pending` `Expired` `N-A` | **yes** | IRCA 8 USC 1324a; Form I-9 | Employment eligibility must be verified within three business days of hire and re-verified when a document expires. ICE fines are per form. | Whether this person may be put on a crew at all. Expired means they cannot lawfully work tomorrow, which is a scheduling fact before it is a filing fact — and it is what the Sprint 7 alert engine blocks employment on. |
| `w4_status` | Select<br>`On-File` `Missing` `Requires-Update` | **yes** | IRC §3402; Form W-4 | Withholding must follow a signed W-4. Missing means the employer withholds at the default single rate and owes an explanation if asked. | Payroll cannot compute a net cheque without it. Missing is not a reporting gap, it is a cheque that comes out at the wrong number. |
| `jurisdiction` | Data | **yes** | FLSA; ORS 653 (Oregon); RCW 49.46 (Washington) | Wage law follows the location where the work is performed, not where the employer sits. Oregon and Washington differ on overtime for agricultural labour, on rest breaks and on minimum wage regions. | The minimum wage and the overtime rule used to compute this person's pay. A crew that crossed the river to a Washington block is paid under a different rule that day, and this is the field that says so. |
| `flc_license_status` | Data | no | MSPA 29 USC 1801; ORS 658.405 farm labor contractor licensing | Anyone recruiting, supervising or transporting agricultural workers for a fee needs a farm labor contractor licence, federally and in Oregon. Using an unlicensed contractor is the grower's violation as well as theirs. | Whether this person may lawfully run a crew or drive the bus. An expired licence takes a crew boss off the schedule that morning. |
| `flc_license_expiration` | Date | no | MSPA 29 USC 1801; ORS 658.405 | A licence is only a defence while it is current. The expiration date is the fact. | Feeds the renewal alert. A crew boss whose licence lapses mid-harvest is a crew with nobody who can lawfully supervise it. |

### `Bucket Log Entry` — the BucketLog bridge

Harvest chain of custody: bucket → picker → crew → block → bin → shipment. The FSMA Food Traceability Rule's critical tracking events, in the record the iPad already writes.

| Field | Type | Required | Framework | Why the regulator wants it | What breaks in the WORK without it |
| --- | --- | --- | --- | --- | --- |
| `picker_id` | Data | no | FSMA 21 CFR 1 Subpart S; GAP worker hygiene traceback | A worker health or hygiene investigation traces from a lot back to the people who handled it. Without the picker the trace stops at the crew. | Piecework pay. Every bucket is somebody's money, and an unattributed bucket is a payroll dispute at the end of the week. |
| `crew_id` | Data | no | FSMA Subpart S; MSPA crew records | The crew is the unit a hygiene training record, a field sanitation inspection and a wage-law jurisdiction all attach to. | Who to pay, who to send where tomorrow, and which crew boss answers for the block. Harvest is organised by crew, not by picker. |
| `block_id` | Data | no | FSMA Subpart S critical tracking event; spray REI/PHI linkage | The block is where the lot came from, and it is the join to the spray record — which is how a residue question becomes an answerable question. | Yield by block, cost by block, and the REI check that says whether the block could lawfully be picked at all. |
| `bin_id` | Data | no | FSMA Subpart S — commingling / transformation event | A bin is where buckets from several pickers become one lot. It is the transformation event the rule asks to be recorded. | What actually goes on the truck. The bin is the physical unit the packing house receives and pays against. |
| `shipment_id` | Data | no | FSMA Subpart S — shipping event; buyer traceback exercises | The shipping event closes the chain. A buyer's mock recall is timed, and an operation that cannot answer in four hours fails the audit. | Getting paid. The shipment is what the invoice is raised against, and an unlinked bin is fruit that left the farm with no receivable behind it. |

### `Housing Unit` — erpnext_mcp

FSMA Produce Safety Rule Subpart L worker facilities, and the habitability and detector-test dates Oregon's agricultural labor housing rules turn on. Shipped as declared fields in v0.12.0, verified here.

**Verified, not added.** These are declared fields of a DocType this app ships.
A missing one means the DocType did not migrate, and the installer reports it
rather than papering over it with a Custom Field — two columns and no error is
worse than the problem it would hide.

| Field | Type | Required | Framework | Why the regulator wants it | What breaks in the WORK without it |
| --- | --- | --- | --- | --- | --- |
| `fsma_worker_facility` | Check | no | FSMA Produce Safety Rule 21 CFR 112 Subpart L | Which of fifty buildings are subject to the worker facility sanitation requirements. Without the flag every building is either in scope or none is. | Which buildings get walked on the sanitation round, and which need supplies restocked before a crew arrives. |
| `last_habitability_inspection` | Date | no | OAR 437-004-1120 agricultural labor housing; 29 CFR 1910.142 | Annual habitability inspection is the cadence a camp is walked on. | Whether a cabin can be assigned. An uninspected unit is one nobody has confirmed has running water this season. |
| `smoke_detector_last_test` | Date | no | OAR 437-004-1120; ORS 479 smoke alarm requirements | A detector nobody has tested is a detector nobody knows works. | Somebody sleeps there tonight. |
| `co_detector_last_test` | Date | no | OAR 437-004-1120; ORS 690 carbon monoxide alarms | Required wherever there is a fuel-burning appliance, which on a camp cabin usually means a propane heater. | Somebody sleeps there tonight. |

### `Field` — erpnext_mcp

Food safety zoning and the agricultural water and spray dates the Produce Safety Rule turns on. Shipped as declared fields in v0.12.0, verified here.

**Verified, not added.** These are declared fields of a DocType this app ships.
A missing one means the DocType did not migrate, and the installer reports it
rather than papering over it with a Custom Field — two columns and no error is
worse than the problem it would hide.

| Field | Type | Required | Framework | Why the regulator wants it | What breaks in the WORK without it |
| --- | --- | --- | --- | --- | --- |
| `food_safety_zone` | Data | no | FSMA Produce Safety Rule 21 CFR 112; GAP / GlobalGAP zoning | Zoning is how a hazard assessment is expressed on the ground — which ground is adjacent to a dairy, a road, a wildlife corridor. | Which blocks get walked for animal intrusion before a pick, and which can be picked at all after a flood event. |
| `last_spray_date` | Date | no | EPA WPS 40 CFR 170.407 REI; FIFRA label PHI | The date the REI and PHI windows are counted from. | Whether a crew can enter this block today. It is read before every pick and every thinning pass. |

---

## Keeping this file true

The tables above are the contents of `compliance_fields.TARGETS`, and
`test_compliance_fields.py` asserts that every field in that table appears here
with its framework and its operational answer. A field added to the code and not
to this file fails the suite; a field described here and removed from the code
does too. Neither can drift from the other in a release.

## Related

* `erpnext_mcp/compliance_fields.py` — the table, the installer, and the argument
* `erpnext_mcp/install.py` — the `after_migrate` hook and the uninstall warning
* `docs/tool-catalog.md` — `install_compliance_fields` and `get_compliance_field_map`
* `tests_standalone/test_compliance_fields.py` — including `WovenNotShadow`
* `tests_standalone/test_housing.py` — the same `WovenNotShadow` argument, run
  against live DocTypes this app owns
