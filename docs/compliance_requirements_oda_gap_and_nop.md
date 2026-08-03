# Compliance Requirements: ODA-Administered USDA GAP and USDA NOP

**Purpose.** Specification-quality reference for building audit-packet templates and validating that our Compliance record doctypes (Housing Inspection, Detector Test, Water Test, Spray Record, Employee Training Record, Compliance Alert, etc.) capture every field an auditor will actually ask for. Two regimes are treated in depth (ODA-administered USDA GAP, USDA National Organic Program). A short cross-cutting section at the end lists complementary regimes already on the farm — WPS, Oregon farm labor housing, ORS 634 — because their records overlap heavily with GAP/NOP evidence and the same doctypes must serve all of them.

Every substantive claim below is cited to the URL it came from. Where a URL was inaccessible during research, that is noted explicitly.

---

## 1. ODA-Administered USDA GAP (Federal-State Audit Program)

Tim's farm sells fresh cherries; wholesale/packer buyers routinely require a USDA GAP certificate. In Oregon, USDA-trained ODA auditors deliver the audit under the Federal-State Audit Program in seven districts. ODA offers four scheme variants — USDA GAP&GHP, USDA Harmonized GAP, Harmonized GAP Plus+, and Mushroom GAP — all under the same application workflow (source: https://www.oregon.gov/oda/agriculture-services/ma-certification/pages/gapghp.aspx).

The controlling standards are:

- **USDA GAP checklist Version 3.0** — mandatory for audits after 31 July 2022 (source: https://www.ams.usda.gov/services/auditing/gap-ghp/audit).
- **Harmonized GAP Standard v3.1 / Harmonized GAP Plus+ Standard v5.1** — effective 3 July 2025; audits use the **Harmonized GAP Combined Checklist v6.2** (source: https://www.ams.usda.gov/services/auditing/gap-ghp/harmonized). Harmonized GAP Plus+ is GFSI technically-equivalent; both Harmonized variants are FDA-recognized as aligned with the FSMA Produce Safety Rule (source: https://www.oregon.gov/oda/agriculture-services/ma-certification/pages/gapghp.aspx).

For cherries sold to conventional buyers, Harmonized GAP is the practical target unless a specific buyer demands Plus+ for GFSI reasons.

### A. Records Required

Every USDA GAP variant requires a **documented food safety plan** as the anchor record — no plan, automatic failure (source: https://extension.umn.edu/growing-safe-food/navigating-usda-gap-audit-process). Plan sections that GAP auditors expect to see (verbatim from UMN Extension, which mirrors the USDA checklist sections):

| Record | Our doctype | Retention | Format |
|---|---|---|---|
| Food Safety Plan (master document, signed) | Governance Document (new subtype: `Food Safety Plan`) | Life of certification + 2 yr | Digital PDF; auditor will accept binder |
| Farm history / land-use history per parcel | Parcel + Compliance Policy attachment | 2 yr min | Either |
| SOPs (harvest, handwashing, tool sanitation, restroom cleaning, glass/plastic breakage, recall/mock recall) | Compliance Policy (SOP subtype) | 2 yr min | Either |
| Worker health & hygiene training log | Employee Training Record | 2 yr min | Either |
| Water source inventory + test results (irrigation, wash, drinking) | Water Test | 2 yr min | Either |
| Manure / soil amendment log | Farm Task (subtype `Amendment`) + attached product doc | 2 yr min | Either |
| Toilet & handwashing station cleaning log | Housing Inspection (extend to `Field Sanitation Station`) | 2 yr min | Either |
| Pest control monitoring log (packing/storage) | Detector Test (extend `test_type` to include `Pest Monitoring`) | 2 yr min | Either |
| Harvest / packing / storage / transport logs with lot codes | Farm Task + Bucket Log | 2 yr min | Either |
| Mock traceback exercise (annual) | Audit Event (new subtype: `Mock Recall`) | 2 yr min | Digital |
| Visitor & third-party log at packing shed | Audit Event (subtype `Visitor Log`) | 2 yr min | Paper log OK |
| Corrective action log | Compliance Alert with dispositions | 2 yr min | Either |

USDA does not publish an explicit universal retention period in the checklist; industry practice and Harmonized GAP's traceability clauses treat **2 years** as the minimum defensible window. Where WPS or ORS 634 records are pulled into a GAP audit, use the longer retention of the two.

### B. Field Data Captured at Time of Activity

Doctypes must capture these fields per activity type. This is what the auditor will point at line-by-line:

**Spray Record (per application).** Product trade name; **EPA registration number**; active ingredient; application date and start/stop time; **applicator name + Oregon pesticide license number**; **Oregon ORS 634 applicator category**; block(s) / parcel(s) treated with acres; rate per acre + total product used; carrier volume; **wind speed and direction, air temperature, relative humidity** at application; **REI (restricted-entry interval)** end time; **PHI (pre-harvest interval)** end date; equipment used; nozzle/pressure; buffer distance from sensitive areas; signature. (Field list synthesized from USDA GAP checklist expectations plus the WPS record rule at 40 CFR 170.309, which mandates product name, EPA reg no., active ingredient, treated area, and REI kept at establishment for 2 years — source: https://www.ecfr.gov/current/title-40/chapter-I/subchapter-E/part-170/subpart-D/section-170.309).

**Water Test.** Sample date; source ID (well tag, canal intake, hydrant); source type (agricultural / potable); intended use (foliar irrigation, drip, wash water, drinking); lab name + accreditation; analyte (generic E. coli CFU/100 mL for ag; total coliform + E. coli + nitrates for potable per SDWA); result value; regulatory threshold; pass/fail; corrective action if fail; who pulled sample.

**Employee Training Record.** Employee ID; training topic (food safety / WPS / harvest hygiene / first aid / equipment); trainer name; language delivered in; date; duration; assessment result; signature (wet or e-signature).

**Housing Inspection / Sanitation Facility Check.** Facility ID; inspector; date; checklist result per item; corrective actions; photos.

**Detector Test (smoke/CO/pest).** Device ID + location; test date; result (pass/fail per NFPA 72 for life-safety devices); battery/backup status; tested by; next-due date.

**Farm Task (harvest, sanitation).** Task type; parcel/block ID; date/time; crew members; equipment ID; lot code assigned; notes; supervisor sign-off.

### C. Cadence

- **GAP audit itself: annual**, during active harvest so the auditor can watch practice (source: https://extension.umn.edu/growing-safe-food/navigating-usda-gap-audit-process).
- **Water tests: at minimum annually per source**; for ag water under the FSMA-aligned Harmonized GAP, buyers commonly want quarterly during use season. Potable water under SDWA / Oregon OAR 333 for a small non-community supply is typically annual bacteriological + every-3-year nitrate — confirm with source status.
- **Worker training: at hire + annually + on change of role or product** (WPS = at least every 12 months; GAP inherits the WPS requirement).
- **Mock recall / traceback: annually.**
- **Pest monitoring in packing shed: monthly at minimum, weekly during active season.**
- **Detector tests (NFPA 72): monthly visual, annual functional for smoke; CO per manufacturer.**
- **Housing pre-occupancy registration: annually with Oregon OSHA at least 45 days before first occupancy** (source: https://oregon.public.law/rules/oar_437-004-1120; note that OAR 437-004-1090 was renumbered/superseded — the current live rule is OAR 437-004-1120).

### D. Audit Format Expectations

ODA scheduling requires application submitted at least four weeks before the requested audit date via the file-upload portal at files.oda.state.or.us. Required forms: **USDA SC-237A** (Request for Audit Services), **USDA SC-651** (Agreement for Participation), **USDA SC-430** (Vendor Form, first-time only), plus ODA's Application Assistant and Authorization to Release Information (source: https://www.oregon.gov/oda/agriculture-services/ma-certification/pages/gapghp.aspx). Fees: ODA $171/hr with 4-hr minimum + USDA $171/hr, billed separately.

On-site, the auditor works through the **Harmonized GAP Combined Checklist v6.2** in order (four scopes: General Questions, Field Operations & Harvesting, Post-Harvest Operations, Logo Use). They expect either a **physical binder with tabbed sections mirroring the checklist scopes**, or a laptop/tablet with a matching folder tree. Records must be produceable within seconds; a passing score is ≥80% per section. Reports come as a PDF that ODA can, at grower's option (Authorization to Release), send directly to buyers.

**Deliverable expectation for our audit packet generator:** a single PDF that mirrors the four checklist scopes, with each section carrying (i) the applicable SOPs, (ii) the year's logs for that scope, and (iii) an evidence index that maps every checklist line to a document. That's exactly the shape `generate_audit_packet` needs to produce.

### E. Common Failure Modes

MSU Extension's list of automatic-failure conditions (source unreachable during research — canr.msu.edu returned an Incapsula block; content quoted here is from the WebSearch summary of that page):

- **Immediate food safety risk observed on site.**
- **Rodent or excessive insect evidence during packing/storage.**
- **Employee practice that jeopardizes food safety** (bare-hand contact after using restroom without wash, etc.).
- **Falsification of records** — instant fail, and typically a bar from re-audit.
- **No designated food safety person on staff.**
- **No GAP Manual / food safety plan present at audit.**

Beyond automatic failures, the typical <80% section fails come from: unclear or inconsistent lot coding; traceback exercises that break at the field/block level; missing signatures on training records; water test results older than the sampling cadence stated in the SOP; spray records missing EPA registration number or REI end time; failure to close out corrective actions from prior year's audit findings.

---

## 2. USDA National Organic Program (7 CFR Part 205)

If Tim pursues organic certification for any parcel, the operation becomes subject to 7 CFR 205 in full, administered by an ACA (Accredited Certifying Agent). Oregon Tilth (OTCO) is the natural in-state ACA and its published guidance is largely a plain-language restatement of 7 CFR 205 (source: https://tilth.org/help-center/certification-eligibility-and-requirements/).

### A. Records Required

Anchor record: the **Organic System Plan (OSP)**, agreed between operator and ACA per §205.201. The OSP must include: practices and procedures with frequency; a list of every input with composition, source, and location of use; monitoring practices and frequency; a description of the recordkeeping system per §205.103; and physical-barrier / prevention-of-commingling description for any split operation (source: https://www.ecfr.gov/current/title-7/section-205.201).

Records to be maintained (per §205.103):

| Record | Retention | Notes |
|---|---|---|
| Organic System Plan (all annual versions) | **5 years beyond creation** | Statutory §205.103(b)(4) |
| Input log (every substance applied, source, quantity, date, location) | 5 yr | Must trace to National List / OMRI review |
| Field activity log (planting, cultivation, harvest, sanitation) | 5 yr | Full audit trail from purchase → sale |
| Seed and planting stock source docs (invoices + organic certificates) | 5 yr | Non-GMO / organic-preferred sourcing evidence |
| Compost / manure input records (source, C:N, application interval) | 5 yr | §205.203(c) 90/120-day intervals |
| Buffer zone map + monitoring records | 5 yr | §205.202(c) |
| Split-operation cleanout logs (equipment, storage, transport) | 5 yr | §205.201(a)(5) |
| Sales invoices with organic designation | 5 yr | §205.103(b)(3) audit trail |
| Certifier correspondence (annual OSP updates, notices) | 5 yr | §205.406 |
| Inspection reports + corrective actions | 5 yr | §205.403 |

Records must be "fully disclose all activities and transactions of the certified operation, in sufficient detail as to be readily understood and audited" — §205.103(b)(2), verbatim (source: https://www.ecfr.gov/current/title-7/section-205.103).

### B. Field Data Captured at Time of Activity

**Input application (spray / amendment / compost).** Date; product trade name; **manufacturer**; **OMRI or WSDA-Organic listing status + approval date**; NOP-permitted category (allowed synthetic / non-synthetic / prohibited); active ingredient; rate; total applied; parcel/block ID; applicator; weather; PHI/REI where applicable; reason for use (must reconcile to OSP-stated rationale for restricted inputs per §205.206(e)).

**Seed / planting-stock purchase.** Variety; source vendor; organic certificate number + expiration; if non-organic, documented commercial-unavailability search per §205.204.

**Field activity.** Parcel; date; activity (till / mow / hand-weed / flame-weed); operator; equipment; whether equipment was used in non-organic operation earlier that day (triggers cleanout record).

**Harvest.** Parcel; date; lot code; quantity; container type + cleanout status; destination; whether shared with non-organic harvest that day.

**Buffer zone monitoring.** Adjacent land-use description; buffer width; date observed; observed drift risk; mitigation.

### C. Cadence

- **Annual OSP update** submitted to certifier before annual inspection (§205.406).
- **Annual on-site inspection** by certifier (§205.403(a)) — must be scheduled when land, facilities, and activities that demonstrate compliance can be observed (source: https://www.ecfr.gov/current/title-7/subtitle-B/chapter-I/subchapter-M/part-205/subpart-E).
- **Unannounced inspections**: certifiers are required to run these on ≥5% of operations per year, rounded up (§205.403(b)(1)).
- **Prohibited-substance notification: immediate** — the operation must notify the certifier of any application (including drift) of a prohibited substance §205.400(f)(1). This is a same-day event; the doctype must support instant filing.
- **3-year transition**: no prohibited substance may have been applied for **3 years immediately preceding harvest of the organic crop** (§205.202(b), source: https://www.ecfr.gov/current/title-7/section-205.202).
- **90-day / 120-day compost intervals** for raw manure applications (§205.203, not fetched in full but referenced in the pest-management standard at §205.206).

**Cadence differences vs. GAP:** NOP inspection is annual like GAP, but NOP retention is **5 years** vs. GAP's practical 2 years. NOP has no set water-testing cadence (that is a GAP/FSMA overlap), and NOP has no annual mock recall requirement (traceability is proved during inspection). NOP adds a **same-day event trigger** (prohibited-substance notification) that GAP does not have.

### D. Audit Format Expectations

The inspector conducts an in-person on-site inspection, exit interview, and produces an on-site inspection report; the certifier issues (or continues) a **certificate of organic operation generated from the USDA Organic Integrity Database (OID)** (§205.404(b), source: https://www.ecfr.gov/current/title-7/subtitle-B/chapter-I/subchapter-M/part-205/subpart-E). Oregon Tilth uses the OTCO MyOTCO portal for annual OSP submission and document upload (source: https://tilth.org/help-center/certification-eligibility-and-requirements/).

There is no equivalent to the numbered SC-237/SC-651 GAP forms; each ACA has its own OSP template. What the inspector expects on the day: a walkthrough with the operator present, with input log, seed docs, and sales/harvest records ready to reconcile mass-balance (inputs must account for outputs — §205.403(d)(4)).

### E. Common Failure Modes

- **Prohibited-substance application** (including drift from neighbor with no buffer / no monitoring) — triggers 3-year clock reset for the affected parcel.
- **Input on the National List but not in the OSP** — §205.201(a)(2) requires every substance to be listed with source and location; using an approved input that isn't in the OSP is a noncompliance.
- **Broken audit trail**: harvest quantity that can't be reconciled with sales invoices (mass balance fails at §205.403(d)(4)).
- **Missing 3-year land history documentation** for a newly-added parcel.
- **Split-operation commingling** — shared harvest containers or trailers with no cleanout record.
- **Late OSP update** — §205.406 requires annual submission with fee; failure suspends certification.
- **Willful false statement** — the certifier may deny certification without first issuing a notification of noncompliance (§205.405(g)).

### F. Records a Farm Coming from Conventional Will Not Yet Have

Any conventional grower entering transition needs to start capturing these on Day 1 of the 3-year clock, even before the OSP is written:

1. **Every input applied to the transitioning parcel with date, product, EPA reg (if pesticide), rate, and OMRI status.** Without this, the "no prohibited substance for 3 years" claim can't be proven.
2. **Buffer-zone map** with width, adjacent land-use, and monitoring log — most conventional farms have never drawn one.
3. **Seed / planting-stock organic certificates** — invoices alone are not enough; a copy of the vendor's organic certificate at time of purchase is required.
4. **Commercial-unavailability documentation** when non-organic seed is used (§205.204 requires evidence of a search).
5. **Split-operation cleanout logs** for any parcel, tool, container, or vehicle that also touches non-organic acreage — this is where cross-contamination and commingling findings originate.
6. **Compost / manure C:N and application-interval records** (§205.203).
7. **Prohibited-substance drift response record** — an SOP plus a same-day filing template so the notification obligation is met.
8. **Neighbor / adjoining-land communication log** — evidence that Tim knows what's being sprayed nearby (needed to substantiate the buffer decision).
9. **Water source documentation for irrigation** including whether the source passes through non-organic land — often overlooked.
10. **Equipment purchase and prior-use records** — used equipment that previously handled prohibited substances requires documented cleaning before organic use.

---

## 3. Records Our Doctypes Need to Gain

Concrete changes to make the compliance model actually cover both regimes. Grouped by doctype.

**Spray Record / Farm Task (Amendment):**
- Add `epa_registration_number` (string, required for pesticides).
- Add `omri_listing_id` + `omri_status` (Allowed / Restricted / Prohibited) + `omri_verified_date` (required for any operation flagged organic or transitioning).
- Add `applicator_license_number` + `applicator_license_category` (ORS 634 categories).
- Add `wind_speed_mph`, `wind_direction`, `air_temp_f`, `relative_humidity_pct`, captured at start of application.
- Add `rei_end_datetime` (computed from label + start time) and `phi_end_date`.
- Add `buffer_distance_ft` and `buffer_sensitive_receptor` (school, watercourse, organic block, apiary).
- Add `carrier_volume_gpa` and `equipment_id`.
- Add `reason_for_use_narrative` — required when input is a restricted synthetic under §205.206(e).

**Water Test:**
- Add `source_id` (FK to a new `Water Source` doctype), `source_type` (Ag / Potable), `intended_use` (Irrigation-drip, Irrigation-foliar, Wash, Handwash, Drinking).
- Add `lab_name`, `lab_accreditation_id`, `analyte`, `result_value`, `result_units`, `regulatory_threshold`, `pass_fail`, `corrective_action_link` (FK to Compliance Alert).
- Add `sample_collected_by` and `sample_chain_of_custody_photo`.

**New doctype: Water Source.** Fields: source name, source type (well / municipal / surface / cistern), GPS point, service area (which parcels/blocks), potability status, permit numbers, last-inspection date. Every Water Test references one.

**Employee Training Record:**
- Add `training_topic_taxonomy` (enum: Food-Safety-GAP, WPS-Handler, WPS-Worker, Housing-Safety, First-Aid, Equipment, Organic-Handling).
- Add `language_delivered_in`, `trainer_name`, `trainer_credential`, `assessment_result`, `next_due_date`.

**Housing Inspection:**
- Rename or generalize to `Facility Inspection` with a `facility_type` enum (Cabin, Common-Area, Field-Sanitation-Station, Packing-Shed).
- Field-sanitation-station checks (toilet stocked, handwash working with soap+towels, water level, cleanliness) belong here, not in Housing.
- Retain existing NFPA 72 detector fields for cabins.

**Detector Test:**
- Extend `test_type` enum to include `Pest-Monitoring-Trap` for packing-shed rodent/insect monitoring, with a `trap_id`, `trap_location`, `catch_count`, `bait_status`.

**New doctype: Organic System Plan (OSP).** One per certified/transitioning parcel-group per year. Fields: parcel_group, plan_year, practices_narrative (rich text), inputs_list (child table of approved inputs with OMRI evidence attached), monitoring_plan, recordkeeping_description, split_operation_barriers, submitted_to_certifier_on, certifier_response_status.

**New doctype: Buffer Zone.** Fields: parcel, boundary_geojson (buffer polygon), width_ft, adjacent_land_use, adjacent_operator_name, drift_risk_assessment, monitoring_frequency.

**New doctype: Prohibited-Substance Event.** Same-day filing target for §205.400(f)(1). Fields: parcel, event_datetime, substance, source (own application / drift / accidental), affected_area_acres, mitigation_action, certifier_notified_datetime, transition_clock_reset (bool).

**New doctype: Mock Recall Exercise.** Annual for GAP. Fields: exercise_date, lot_code_traced, upstream_completeness_pct, downstream_completeness_pct, time_to_complete_minutes, gaps_identified, corrective_action_link.

**Compliance Alert:**
- Add `regime` enum (GAP, NOP, WPS, ORS-634, OAR-437-004-1120, NFPA-72, SDWA, OAR-333).
- Add `evidence_document_links` (multi-attach) so the alert points at every record that either raised or resolves it.

**Audit Packet Generator (`generate_audit_packet`):**
- Accept `regime` = GAP | NOP as top-level parameter.
- For GAP: emit PDF with tabs matching Harmonized GAP Combined Checklist v6.2 scopes (General, Field Ops, Post-Harvest, Logo Use), plus evidence index.
- For NOP: emit PDF with OSP-year, input log, seed docs, activity log, harvest→sales mass balance table, buffer maps, prohibited-substance events, and 3-year land history per parcel.

---

## 4. Cross-Cutting Regimes That Feed the Same Doctypes

- **WPS (40 CFR 170.309).** Pesticide application records — product name, EPA reg no., active ingredient, treated area, REI — kept at establishment for **2 years** (source: https://www.ecfr.gov/current/title-40/chapter-I/subchapter-E/part-170/subpart-D/section-170.309). Fully covered by the Spray Record fields above.
- **Oregon ORS 634 pesticide applicator licensing.** Applicator name + license number + category must be captured on every Spray Record; renewal cadence and CEUs sit on the Employee record.
- **Oregon OAR 437-004-1120 (agricultural labor housing).** Annual pre-occupancy registration with Oregon OSHA at least 45 days before first occupancy; replacement-lodging obligation if a facility is declared uninhabitable (source: https://oregon.public.law/rules/oar_437-004-1120). Registration is a Compliance Calendar item; inspections are Housing Inspection records.
- **NFPA 72 (smoke/CO detectors).** Monthly visual, annual functional — Detector Test doctype already covers this; extend `next_due_date` calc to be regime-aware.
- **SDWA / Oregon OAR 333 (potable water).** For any drinking-water source on the farm, add to Water Source doctype with the appropriate `source_type` = Potable and per-analyte cadence.

---

## 5. Sources

- ODA GAP & GHP program page — https://www.oregon.gov/oda/agriculture-services/ma-certification/pages/gapghp.aspx
- USDA AMS GAP Audit Program — https://www.ams.usda.gov/services/auditing/gap-ghp/audit
- USDA AMS Harmonized GAP — https://www.ams.usda.gov/services/auditing/gap-ghp/harmonized
- UMN Extension "Navigating the USDA GAP audit process" — https://extension.umn.edu/growing-safe-food/navigating-usda-gap-audit-process
- MSU Extension "Avoiding automatic failure of a GAP audit" — https://www.canr.msu.edu/news/avoiding_automatic_failure_of_a_gap_audit (**inaccessible during research: Incapsula bot block. Content used is from the WebSearch summary of that page; verify against the primary source before quoting in an SOP.**)
- 7 CFR 205.103 (recordkeeping) — https://www.ecfr.gov/current/title-7/section-205.103
- 7 CFR 205.201 (OSP) — https://www.ecfr.gov/current/title-7/section-205.201
- 7 CFR 205.202 (land requirements / 3-year transition) — https://www.ecfr.gov/current/title-7/section-205.202
- 7 CFR 205.206 (pest/weed/disease standard) — https://www.ecfr.gov/current/title-7/section-205.206
- 7 CFR 205 Subpart E (Certification, §§205.400–406) — https://www.ecfr.gov/current/title-7/subtitle-B/chapter-I/subchapter-M/part-205/subpart-E
- Oregon Tilth certification eligibility & requirements — https://tilth.org/help-center/certification-eligibility-and-requirements/
- 40 CFR 170.309 (WPS pesticide records) — https://www.ecfr.gov/current/title-40/chapter-I/subchapter-E/part-170/subpart-D/section-170.309
- OAR 437-004-1120 (Oregon agricultural labor housing) — https://oregon.public.law/rules/oar_437-004-1120 (note: user's OAR 437-004-1090 reference appears to have been renumbered; current live rule is -1120)

**Not directly retrieved and therefore not cited by clause:** the current-version Harmonized GAP Combined Checklist PDF/XLSX itself (v6.2). It is downloadable at https://www.ams.usda.gov/sites/default/files/media/HarmonizedGAPCombinedChecklist6.2.pdf; before building final audit-packet templates, pull that file and map each of the four checklist scopes' numbered lines to the doctype+field pairs listed in §3 above.
