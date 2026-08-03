# Compliance Requirements: Oregon Tilth (NOP ACA), Oregon OSHA (state-plan ag), FDA FSMA Produce Safety Rule

**Purpose.** Second-regime companion to `compliance_requirements_oda_gap_and_nop.md`. Same structure: Records required, Field data at time of activity, Cadence, Audit format, Common failure modes, Records our doctypes don't have yet. Every claim is cited to the URL it came from. Where a URL was inaccessible during research, that is noted explicitly.

## 0. Verification of Prior Citation — OAR 437-004-1120

**Confirmed.** The current live agricultural-labor-housing rule is **OAR 437-004-1120 — "Agricultural Labor Housing and Related Facilities"** (title verified verbatim from oregon.public.law page metadata: `meta-title: OAR 437-004-1120 – Agricultural Labor Housing and Related Facilities`, source https://oregon.public.law/rules/oar_437-004-1120). The prior doc is correct on this; the older `OAR 437-004-1090` that appears in some legacy memory files is superseded by -1120 and should be updated across saved memory. Every substantive labor-housing memory reference should be normalized to `OAR 437-004-1120`.

While correcting one number, correct another: the user's brief listed `OAR 437-004-1130` for heat illness. **That is off by one.** The permanent Oregon heat-illness rule for agriculture is **OAR 437-004-1131** (companion non-ag rule is OAR 437-002-0156), adopted 9 May 2022. Source, quoted verbatim: "On May 9, 2022, Oregon adopted two permanent rules – 437-002-0156 and 437-004-1131 … 437-004-1131 applies to agricultural workplaces and 437-002-0156 applies to all other workplaces" (Oregon OSHA Fact Sheet FS-91, https://osha.oregon.gov/OSHAPubs/factsheets/fs91.pdf). Fix any doctype enum and any memory file that says -1130. Public.law's Division 4 index (last updated May 2025) does not yet list -1131 because it lags the SoS-OARD source; the SoS-OARD site is authoritative for rule numbers.

Similarly, `OAR 437-004-1005` cited in the user's brief is actually **"General Requirements for Protective Equipment"** (PPE), not sanitation (source: https://oregon.public.law/rules/oar_437-004-1005). The ag sanitation citations are **OAR 437-004-1105** (Sanitation) and **OAR 437-004-1110** (Field Sanitation for Hand Labor Work) (source: https://oregon.public.law/rules/oar_437-004-1110). WPS handwashing pieces the user cited under 29 CFR 1928.110 are handled in Oregon via WPS-adopted rules at OAR 437-002-0170 plus the ag-specific handler/worker rules OAR 437-004-6501/6508 (source: https://oregon.public.law/rules/oar_chapter_437_division_4 rule index).

---

## 1. Oregon Tilth (OTCO) — Accredited Certifying Agent for the National Organic Program

Oregon Tilth Certified Organic (OTCO) is one of the largest US ACAs and is Oregon-headquartered. Its NOP certification service uses **the same underlying regulation (7 CFR 205)** as any other ACA, but adds:

- Its own scope-specific **Organic System Plan (OSP)** template families — Crop, Livestock Mammals, Avian, Handling, and starting 2024 an **Organic Fraud Prevention Plan (OFPP)** overlay (source: https://tilth.org/help-center/the-organic-system-plan-osp-the-foundation-of-organic-certification/).
- The **MyOTCO** client portal for OSP submission, document upload, inspection report delivery, and file storage (source: https://tilth.org/help-center/understanding-organic-inspections/).
- Its own pre-inspection checklists (Crop, Livestock, Processing) that map to the OSP's scope sections — these are the day-of expectations (source: https://tilth.org/help-center/understanding-organic-inspections/, checklist links: `OTCO_Crop_InspectionCheckConfirmation.docx`, `OTCOLivestock_InspectionCheckConfirmation.docx`, `OTCOProcessing_InspectionCheckConfirmation.docx`).

For Tim's operation the relevant scopes are **Crop** and eventually **Handling** (any post-harvest cherry sort/pack/cold-storage activity beyond the field is Handling).

### A. Records Required (OTCO overlay on top of §205.103 NOP baseline)

The generic NOP retention (**5 years** per §205.103(b)(4)) is quoted verbatim by OTCO: "Records must be well-organized, readable, and stored for **at least five years**" (source: https://tilth.org/help-center/recordkeeping-for-farm/).

Beyond the eCFR-verbatim record list already covered in the ODA GAP + NOP doc, OTCO's Crop track expects the following as a client (that is, records/documents held in MyOTCO that the OTCO reviewer or inspector will pull):

| Record | Our doctype | Retention | Notes |
|---|---|---|---|
| Current-year OSP (Crop scope, all applicable sections) via MyOTCO | Governance Document → Organic System Plan doctype | 5 yr | Annual submission before inspection |
| Organic Fraud Prevention Plan (OFPP) | Governance Document → Fraud Prevention Plan subtype (new) | 5 yr | Per Strengthening Organic Enforcement (SOE), required for all OTCO clients (source: https://tilth.org/help-center/organic-fraud-prevention-plan-ofpp-resource-guide/) |
| Form O1 — Operation Information | Governance Document | 5 yr | OTCO intake form (source: https://tilth.org/wp-content/uploads/2015/05/O1OperationInformation.docx) |
| Commercial Availability Form (if using non-organic seed) | Farm Task → Amendment / dedicated attachment | 5 yr | OTCO template (source: https://tilth.org/wp-content/uploads/2025/04/CommercialAvailabilityForm.docx) |
| Pre-inspection checklist filled out by operator | Audit Event (subtype: Pre-Inspection Checklist) | 5 yr | Signed and dated before inspector arrival |
| Traceback exercise record (backward: sale → seed) | Audit Event (subtype: Traceback Audit) | 5 yr | Two exercises minimum per inspection — see §D |
| Mass Balance exercise record (inputs vs outputs) | Audit Event (subtype: Mass Balance Audit) | 5 yr | |
| Sales Audit reconciliation | Audit Event (subtype: Sales Audit) | 5 yr | Volume produced vs volume sold vs income received |
| Clean-truck affidavits for outbound organic shipments | Attachment on Farm Task (harvest/ship) | 5 yr | OTCO called out explicitly (source: https://tilth.org/help-center/recordkeeping-for-farm/) |
| Buffer maps + neighbor communication log | Buffer Zone doctype | 5 yr | Both required — OTCO wants adjacency communication evidence |
| Irrigation-district substance calendar + no-irrigation-on-treatment-days record | Water Source + Farm Task | 5 yr | OTCO-specific — Oregon districts add prohibited materials; grower must document non-irrigation on those days |
| Split-operation equipment cleanout log | Farm Task (subtype: Equipment Cleanout) | 5 yr | Required if any acreage is non-organic |
| Non-compliance / Adverse Action correspondence with OTCO | Compliance Alert w/ ACA reply thread | 5 yr | Per OTCO NC & Adverse Actions guidance |
| Certificate of organic operation (annual, from OTCO/USDA OID) | Governance Document (Certificate subtype) | 5 yr | OTCO issues; USDA OID posts publicly |
| OTCO annual fee payment record | Attachment | 5 yr | Non-payment = suspension per OTCO T&Cs |

**OFPP is a first-class new obligation.** It is not part of 7 CFR 205 verbatim but is now required by OTCO as a component of the OSP under NOP's Strengthening Organic Enforcement (SOE) final rule (89 FR effective March 2024). The plan must describe how the operation "monitors your operation and supply chain, verifies suppliers and incoming organic products, and prevents fraud, including how often these activities take place" (source: https://tilth.org/help-center/organic-fraud-prevention-plan-ofpp-resource-guide/, referenced from https://tilth.org/help-center/). Our current doctype set has nothing for this; it's a Governance Document subtype at minimum, with monitoring cadence and supplier-verification child rows.

### B. Field Data Captured at Time of Activity

Same field lists as the ODA GAP + NOP doc §1.B and §2.B, with these OTCO-specific additions on the Crop Farm scope:

**Seed / planting-stock purchase.** OTCO's crop page adds two must-haves beyond §205.204:

1. **Non-GMO verification** — required for varieties that have a commercial GMO version (corn, soy, alfalfa; not cherries but relevant if Tim adds cover crops or diversifies) — supplier statement kept on file.
2. **Commercial availability documentation** — OTCO uses its own Commercial Availability Form; a bare invoice is insufficient (source: https://tilth.org/help-center/recordkeeping-for-farm/).

**Contamination prevention.** OTCO explicitly requires **clean-truck affidavits** for outbound organic shipments, **water source documentation with irrigation-district communication**, and **buffer maps with neighbor agreements** — this last item is more specific than §205.202(c)'s general buffer requirement. The doctype needs both `neighbor_operator_name` (already in the prior doc §3) and `neighbor_communication_log_link` (new).

**Harvest logs (OTCO Crop-scope minimum).** Beyond the fields in the NOP doc §2.B: `harvest_container_type`, `container_cleanout_status`, `transport_vehicle_id`, `equipment_cleanout_verified_by`, `equipment_cleanout_datetime`.

### C. Cadence

- **OSP annual update** — same as NOP §205.406; OTCO client services team must be emailed for OSP updates any time inputs, products, land, or processes change (source: https://tilth.org/help-center/the-organic-system-plan-osp-the-foundation-of-organic-certification/).
- **Annual inspection** — verbatim OTCO: "Certified operations are inspected **once per year**, at minimum" (source: https://tilth.org/help-center/understanding-organic-inspections/).
- **Unannounced inspections** — OTCO conducts these as required by USDA; refusal = suspension or revocation. Any certified operation, any time (source: same URL).
- **New operations** — inspection must occur within **6 months** of the operation being ready; crops must be inspected **before harvest or sale** (source: same URL).
- **Certification decision timeline** — inspection report available in MyOTCO within a few weeks; certification decision typically **30–45 days** after inspection (source: same URL).
- **OFPP review** — no explicit cadence in OTCO help center; presumed annual with OSP. Track internally to be safe.

### D. Audit Format Expectations

OTCO inspections are on-site walkthroughs conducted by an OTCO-assigned qualified inspector. The inspector must be granted **full access** including certified and non-certified areas, facilities and storage, and financial records related to organic activities (source: https://tilth.org/help-center/understanding-organic-inspections/). The operator (or an authorized rep familiar with the organic system) must be present, plus any staff needed to answer questions.

The two audit-trail exercises are the day-of anchor. Both are typically executed at each annual inspection:

1. **Traceback**: Follow a product backward from sale invoice → shipping doc → harvest log → field/block → seed source. Must reconcile.
2. **Mass Balance**: Compare input quantities (seeds, ingredients, amendments) with output quantities (harvest volume). Must reconcile.

OTCO also lists a third when relevant:

3. **Sales Audit**: Volume sold matches volume produced matches income received.

If traceability cannot be verified, "an additional inspection will be required" (source: https://tilth.org/help-center/understanding-organic-inspections/). This is a real cost — plan the recordkeeping around passing these three exercises on the first walkthrough.

**Deliverable expectation for our audit-packet generator, NOP variant:** the PDF for OTCO must include per-parcel-group the current-year OSP (with OFPP annex), the input/seed/harvest/sales logs organized to support both a traceback (pick any recent sale, trace back within the packet) and a mass balance (input totals vs harvest totals in a single table), buffer-zone maps, split-operation cleanout logs, and the certifier correspondence thread. This is a superset of the plain-NOP packet spec in the prior doc §3.

### E. Common Failure Modes

Combined from OTCO's noncompliances page and the recordkeeping guide (both under https://tilth.org/help-center/):

- **OSP–practice divergence** — inspector observes something in the field that isn't in the OSP (input, activity, boundary, source). OTCO stresses this repeatedly ("your written plan should reflect your actual practices"). Same-day OSP update is not allowed to close it; it becomes a noncompliance.
- **Materials list stale** — inputs on the shelf that aren't on the OSP input list (or vice-versa).
- **Broken audit trail** — traceback exercise breaks at any step; usually harvest-log to field or field-map to lot-code.
- **Mass balance mismatch** — harvest quantity doesn't equal sold + stored + waste. Cherries are especially vulnerable because packout percentage varies year to year.
- **Missing OFPP** — after SOE effective date, no OFPP on file with OTCO is a client-level noncompliance regardless of field practice.
- **Refusal or obstruction of an unannounced inspection** — automatic path to suspension/revocation.
- **Late annual renewal / non-payment of OTCO fees** — administrative suspension; not a field failure but shows up the same way in the OID record.
- **Cancellation ≤7 days before scheduled inspection** — reimbursement + admin fee (source: https://tilth.org/help-center/understanding-organic-inspections/). Not a compliance failure per se but a common operator error to design against with our scheduling doctype.

### F. Records Our Doctypes Don't Have Yet (OTCO overlay)

Additions beyond what the ODA GAP + NOP doc §3 already lists:

- **New doctype: `Organic Fraud Prevention Plan`** (or subtype under `Governance Document`). Fields: plan_year, supplier_list (child rows: supplier_name, product, certificate_id, certificate_expiration, verification_method, verification_frequency), monitoring_frequency, incoming_product_verification_procedure, corrective_action_procedure_if_fraud_detected, plan_last_reviewed_on.
- **Farm Task (Harvest subtype):** add `container_type`, `container_cleanout_status` (enum: verified-clean / not-verified / shared-non-organic), `equipment_cleanout_verified_by`, `equipment_cleanout_datetime`.
- **Farm Task (Shipping subtype):** add `clean_truck_affidavit_attachment` (required for organic outbound).
- **Water Source:** add `irrigation_district_id`, `irrigation_district_substance_calendar_url`, `no_irrigation_on_district_treatment_days_log` (proof that grower stopped irrigation on days the district added prohibited substances — OTCO-specific expectation).
- **Buffer Zone:** add `neighbor_communication_log_link` (multi-attach; letters, texts, meeting notes) and `neighbor_agreement_attachment` where a formal agreement exists.
- **Audit Event:** extend enum with `Traceback Audit`, `Mass Balance Audit`, `Sales Audit`, `Pre-Inspection Checklist`; each with a `completeness_pct` and `gaps_identified` narrative for post-exercise analysis.
- **Certification (certificate of organic operation):** add `otco_certificate_id`, `usda_oid_url` (public), `annual_fee_paid_on`, `annual_fee_amount`, `otco_client_id`.
- **`generate_audit_packet` (regime=NOP):** add ACA parameter and, when ACA=OTCO, emit MyOTCO-shaped filenames and include the OFPP annex, the three audit-trail exercises' results, and the OTCO Crop Inspection Checklist as a filled-out cover.

---

## 2. Oregon OSHA (OR-OSHA, state-plan) — Agricultural Program (OAR 437 Division 4)

Oregon is one of 22 state-plan OSHA states, so agricultural workplace safety here is enforced by **Oregon OSHA**, not federal OSHA, under OAR 437 Division 4 (source: https://oregon.public.law/rules/oar_chapter_437_division_4). The scope covers the full farm workplace, not just labor housing. Six rule clusters are the ones most likely to surface in an inspection of Tim's operation:

1. **OAR 437-004-1120** — Agricultural Labor Housing and Related Facilities (already covered in the prior doc; annual pre-occupancy registration ≥45 days before first occupancy).
2. **OAR 437-004-1110** — Field Sanitation for Hand Labor Work (potable water, toilets, handwashing, notice, reasonable-use training) (source: https://oregon.public.law/rules/oar_437-004-1110).
3. **OAR 437-004-1131** — Heat Illness Prevention (heat-index-based, ≥80 °F triggers; ≥90 °F high-heat practices) (source: https://osha.oregon.gov/OSHAPubs/factsheets/fs91.pdf).
4. **OAR 437-002-0170** + **OAR 437-004-6501 / 6502 / 6508 / 6509 / 6405 / 6406** — Worker Protection Standard adoption plus Oregon handler-training, respiratory protection, eye-wash, outdoor-production restrictions and drift rules (source: https://oregon.public.law/rules/oar_chapter_437_division_4 rule index).
5. **OAR 437-004-9800** — Hazard Communication Standard for Agricultural Employers (labels, SDS, employee info & training; source: https://oregon.public.law/rules/oar_437-004-9800).
6. **OAR 437-004-1005** — PPE general requirements including hazard assessment, employer pays (with named exceptions), and training with retraining triggers (source: https://oregon.public.law/rules/oar_437-004-1005).

Plus injury/illness recordkeeping (OSHA 300 log) under federal 29 CFR 1904 adopted by reference; and safety-committee/safety-meeting requirements at OAR 437-004-0251 and the Safety Orientation for Seasonal Workers at OAR 437-004-0240.

### A. Records Required

| Record | Rule anchor | Our doctype | Retention | Notes |
|---|---|---|---|---|
| Agricultural Labor Housing annual pre-occupancy registration filing | OAR 437-004-1120 | Compliance Calendar item + attached filing PDF | 3 yr (typical OR-OSHA inspection cycle) | Filed ≥45 days pre-occupancy with OR-OSHA |
| Housing pre-occupancy inspection results (self or third-party) | OAR 437-004-1120 | Housing Inspection | 3 yr | Prior doc already covers |
| Field sanitation station daily / per-use checklist (toilet stocked, handwash working, water refilled) | OAR 437-004-1110 | Facility Inspection (subtype: Field Sanitation Station) | 3 yr | Extension of Housing Inspection recommended |
| Chemical toilet empty-and-recharge log (every 6 months or 3/4-full, whichever first) | OAR 437-004-1110(7)(d) | Facility Inspection | 3 yr | Season-start + interval events |
| Field sanitation notice posted in majority language | OAR 437-004-1110(8) | Compliance Alert + posted-notice attachment | 3 yr | Posted, not filed; keep attachment as evidence |
| Reasonable-use hygiene training log (verbal at hire counts if documented) | OAR 437-004-1110(9) | Employee Training Record (subtype: Field Sanitation Hygiene) | 3 yr | |
| Heat illness prevention training log (annual, per employee, before hot-index work) | OAR 437-004-1131 | Employee Training Record (subtype: Heat Illness Prevention) | 3 yr | Annual; content list mandated |
| Heat index measurement / observation log for ≥80 °F work | OAR 437-004-1131 | Farm Task attachment or new `Heat Exposure Event` record | 3 yr | Daily on hot days; documents that shade+water were provided |
| Written acclimatization plan (employer's own or NIOSH-referenced) | OAR 437-004-1131 | Compliance Policy (SOP subtype) | Life of policy | Plan itself, not per-event |
| Emergency medical plan referenced by heat rule | OAR 437-004-1131 → 437-002-0161 / 437-004-1305 | Compliance Policy (SOP subtype) | Life of policy | Required when heat index ≥80 |
| WPS pesticide application records | OAR 437-002-0170 adopting 40 CFR 170.309 | Spray Record | 2 yr | Prior doc covers |
| WPS handler training record | OAR 437-004-6501 / 6502 | Employee Training Record (subtype: WPS Handler) | 2 yr | Every 12 months minimum |
| WPS worker training record | OAR 437-002-0170 → 40 CFR 170.401 | Employee Training Record (subtype: WPS Worker) | 2 yr | |
| Respiratory Protection Program (written) for any applicator using a respirator per label | OAR 437-004-1041 + 437-004-6508 | Compliance Policy | Life of policy | Written program mandatory when any respirator is used |
| Respirator fit-test records (annual) | OAR 437-004-1041 by adoption of 29 CFR 1910.134 | Employee Training Record (subtype: Respirator Fit Test) | Duration of employment + retention per fed rule | |
| Medical evaluation for respirator use | OAR 437-004-1041 | Attachment on employee record | Long-retention (per fed rule) | HIPAA-sensitive |
| Emergency eye-wash and eye-flushing supplies inventory + inspection | OAR 437-004-6509 | Facility Inspection | 3 yr | For handler areas |
| Hazard Communication written program + SDS binder + employee training log | OAR 437-004-9800 (adopts 29 CFR 1910.1200 for ag) | Compliance Policy + Employee Training Record | Life of policy | SDS for every hazardous chemical (pesticides, fertilizers, fuels, cleaners) |
| Container labels present on secondary containers | OAR 437-004-9800 | Facility Inspection field | 3 yr | Inspector will spot-check spray tanks, jugs |
| Hazard assessment (PPE selection) | OAR 437-004-1005(2) | Compliance Policy (subtype: Hazard Assessment) | Life of policy | Written per workplace |
| PPE training log with demonstrated understanding, plus retraining triggers | OAR 437-004-1005(10) | Employee Training Record (subtype: PPE) | 3 yr | Retrain when tasks/PPE change or deficiency observed |
| Safety Orientation for Seasonal Workers | OAR 437-004-0240 | Employee Training Record (subtype: Seasonal Safety Orientation) | 3 yr | At hire, before starting work |
| Safety Committee minutes / Safety Meetings | OAR 437-004-0251 | Audit Event (subtype: Safety Meeting) | 3 yr | Frequency depends on employer size |
| Injury / illness recordkeeping — **OSHA 300 log**, 300A summary, 301 incident forms | 29 CFR 1904 (adopted) | Audit Event (subtype: Injury/Illness — OSHA 300) | **5 yr** past the calendar year the record covers (29 CFR 1904.33) | Ag is not universally exempt: partially exempt for employers with ≤10 employees at all times in the prior year (29 CFR 1904.1); if ≥11 anytime in prior calendar year, full 300 log required |
| Access to Employee Exposure and Medical Records | OAR 437-004-0005 | Employee document repository | 30 yr for exposure records, duration+30 yr for medical (per adopted 29 CFR 1910.1020) | Long-tail retention — architect for it |

**Retention overview.** OR-OSHA does not publish a universal ag retention window; typical inspection reach-back is 3 years, so **3 years is the defensible minimum** for anything not otherwise specified. Federal-rule-adopted items keep their federal retention: OSHA 300 = 5 years, exposure/medical = 30 years, respirator fit-test = duration of employment, WPS = 2 years.

### B. Field Data Captured at Time of Activity

**Field sanitation station check** (per OAR 437-004-1110): station_id, date, time, inspector, potable_water_present (bool + level), water_source_type (bulk cooler / plumbed), handwash_working (bool), soap_present, single_use_towels_present, toilet_paper_present, toilet_clean (pass/fail), toilet_ventilation_ok, doors_latch, distance_from_work_area_ft (must be ≤1320 ft / 5-min walk per (6)(g)), 1-per-20-workers ratio met (bool), corrective_action_link.

**Heat exposure event** (per OAR 437-004-1131 for ≥80 °F heat-index shifts): date, site_or_block_id, heat_index_source (NOAA, on-site instrument), heat_index_high_for_shift, shade_area_established (bool + description), water_stocked_qty_gal_available, employees_on_site_count, high_heat_practices_activated (bool, if heat_index ≥90), buddy_system_active, cool_down_break_events (child rows: employee_id, start_time, duration_min, taken/not-taken/refused), emergency_medical_plan_reviewed_at_shift_start (bool), incidents_observed (child rows w/ symptom list and action taken).

**WPS handler / worker training record** (per OAR 437-004-6501 / 6502 and adopted 40 CFR 170.401/501): all fields listed in prior doc §1.B under Employee Training Record + `trainer_qualification` (train-the-trainer certificate ID for Oregon under -6502) + `training_verification_card_id`.

**Hazard Communication event** (per OAR 437-004-9800): substance, sds_document_id, label_present_on_container (bool), training_delivered_to (child rows of employee_ids), delivered_in_language.

**Respirator fit-test record** (per OAR 437-004-1041 + 29 CFR 1910.134): employee_id, respirator_make_model_size, fit_test_method (QLFT/QNFT), pass/fail, tester_name, test_date, next_due_date (annual), medical_clearance_on_file (bool + date).

**Injury / illness report** (per adopted 29 CFR 1904): incident_datetime, employee_id, injury_or_illness_type, body_part, days_away_from_work, days_of_restricted_work, medical_treatment_beyond_first_aid (bool), classification (recordable Y/N, reason).

### C. Cadence

- **Labor housing registration**: annual, ≥45 days before first occupancy (OAR 437-004-1120).
- **Housing pre-occupancy inspection**: annual, before first occupant enters.
- **Field sanitation stations**: continuous provision; documentable checks each shift a station is in use (rule is silent on inspection cadence but continuous compliance is expected — see (7)(a)-(g)).
- **Field sanitation posted notice**: posted whenever hand-labor is occurring for food crops for human consumption (OAR 437-004-1110(8)).
- **Chemical toilet empty-and-recharge**: prior to start of each season **and** at least every 6 months during use **or** when tank is ¾ full — whichever occurs first (OAR 437-004-1110(7)(d)).
- **Heat illness training**: annually, per employee, before beginning work at any site where heat index will be ≥80 °F (OAR 437-004-1131; source FS-91).
- **High-heat practices** (buddy system, cool-down breaks, communication check, emergency-med plan): activated whenever heat index ≥90 °F.
- **WPS training**: at least every 12 months per worker / handler.
- **Respirator fit test**: annual per user; medical clearance every 5 years (per adopted 1910.134) or on symptom trigger.
- **Safety Orientation for Seasonal Workers**: at hire, before starting work (OAR 437-004-0240).
- **Safety committee / safety meetings**: employer-size-dependent, typically monthly (OAR 437-004-0251).
- **OSHA 300 log**: entries within 7 days of learning of a recordable incident; **300A annual summary posted Feb 1–Apr 30** each year (29 CFR 1904.32); log itself retained 5 years past the calendar year.
- **Hazard Communication training**: at hire and whenever a new hazardous chemical is introduced.

### D. Audit Format Expectations

OR-OSHA inspections are typically **unannounced**. Triggers: routine (programmed), fatality/serious injury complaint, referral, follow-up on prior citations, or a Local Emphasis Program push (heat illness has had an active LEP since 2021 — source: https://osha.oregon.gov/OSHARules/pd/pd-299.pdf).

On arrival the compliance officer will:

1. Present credentials, ask for the employer representative and the safety committee designee.
2. Ask for the **written programs binder** — hazard assessment, respiratory protection program, hazard communication program, emergency action plan, heat illness prevention procedures (including acclimatization plan and emergency medical plan), safety committee minutes, seasonal worker orientation records.
3. Ask for **training records** for a sample of employees — WPS handler/worker, heat illness, hazard communication, PPE, respirator fit test, seasonal orientation.
4. Ask for the **OSHA 300 log** and the 300A posted summary (if the employer is not partially exempt).
5. Walk the operation — field sanitation stations, spray equipment area, chemical storage, PPE cabinet, labor housing (if in season).
6. Interview workers privately about training, sanitation access, drinking water, break policy, retaliation.
7. Closing conference with citation preview if any.

**Deliverable expectation for our audit-packet generator:** the OR-OSHA regime doesn't produce a "packet" in the same buyer-facing sense as GAP; the deliverable is instead a **live-ready binder** and the ability to hand any specific document to the CO within minutes of the request. Our generator should produce a "safety inspection binder" mode that assembles all written programs + training records for sample employees + OSHA 300 log + housing registration + heat illness plan + field-sanitation checks for the past 12 months, on demand. This mode should be triggerable by SMS if the CO shows up unannounced.

### E. Common Failure Modes

Synthesized from OR-OSHA LEP guidance (source: https://osha.oregon.gov/OSHARules/pd/pd-299.pdf) plus Fact Sheet FS-91 plus the rule text:

- **No written heat illness prevention plan / no acclimatization procedure** — instant citation once ambient work is ≥80 °F. FS-91 lists the six training topics that must be covered annually; missing any one is a documented deficiency.
- **Water not cool (66–77 °F) or not enough for 32 oz/hr per employee** — cited under -1131.
- **Shade not established or too far from work area** — cited under -1131.
- **High-heat buddy system / communication check not implemented at ≥90 °F** — cited under -1131.
- **Field sanitation station too far** (>1320 ft / >5-min walk without vehicular-access exception documented) — cited under -1110(6)(g).
- **1-per-20 ratio not met** at peak crew count — cited under -1110(6)(a).
- **Chemical toilet overdue for pumping** (>6 months or >¾ full) — cited under -1110(7)(d).
- **Field sanitation notice not posted in majority language** — cited under -1110(8).
- **Housing operated without current-year pre-occupancy registration** — cited under -1120; commonly triggers a broader housing inspection.
- **WPS handler training expired (>12 months)** — cited under -6501; handler cannot legally perform application until re-trained.
- **No respiratory protection program** while a label-required respirator is in use — cited under -1041; typically pushes an ancillary citation for missing medical clearance and fit test.
- **Hazard Communication: missing SDS for a chemical on-site, or secondary container without label** — cited under -9800.
- **PPE training missing the demonstrated-understanding element** — cited under -1005(10)(b).
- **OSHA 300A summary not posted Feb 1–Apr 30** — cited under adopted 1904.32 (only for non-partially-exempt employers).
- **Retaliation** — separate ORS 654 employee-rights complaint; treat as a compliance-critical event with its own alert.

### F. Records Our Doctypes Don't Have Yet (OR-OSHA overlay)

- **`Employee Training Record` enum extension** to include: `Heat-Illness-Prevention`, `Field-Sanitation-Hygiene`, `Hazard-Communication`, `Respirator-Fit-Test`, `Respirator-Medical-Clearance`, `Seasonal-Safety-Orientation`, `PPE-General`. Each with `regime` enum populated correctly (see below).
- **`Compliance Alert` regime enum** — extend to include `OAR-437-004-1110` (field sanitation), `OAR-437-004-1131` (heat illness), `OAR-437-004-9800` (haz-com), `OAR-437-004-1005` (PPE), `29-CFR-1904` (OSHA 300), plus **removing** any legacy `OAR-437-004-1090` or `OAR-437-004-1130` values that memory files might still carry (rename to -1120 and -1131 respectively).
- **New doctype: `Heat Exposure Event`.** Fields: date, site_or_block_id, heat_index_source, heat_index_high_for_shift, shade_area_id, water_stocked_gal, employees_on_site, high_heat_practices_activated_at, buddy_system_active, cool_down_break_events (child), emergency_med_plan_reviewed, incidents_observed (child), corrective_action_link.
- **New doctype (or subtype of Facility Inspection): `Field Sanitation Station Check`.** Fields: station_id, block/parcel served, date, time, inspector, station_type (toilet-only / handwash-only / combined), potable_water_present, water_level_gal, handwash_working, soap_present, towels_present, toilet_paper_present, toilet_clean_score, ventilation_ok, doors_latch, distance_to_work_area_ft, workers_served_count, corrective_action_link, photo.
- **New doctype: `OSHA 300 Log Entry`.** Fields: incident_datetime, employee_id, injury_or_illness_type, body_part, classification (recordable Y/N + basis), days_away_from_work, days_of_restricted_work, treatment_beyond_first_aid, physician_name, entered_in_log_on, incident_narrative, 301_form_link, calendar_year (drives 5-yr retention).
- **New doctype: `Respiratory Protection Program`** (or subtype of Compliance Policy). Fields: written_program_link, program_administrator_employee_id, hazard_assessment_link, respirator_types_authorized (child), medical_evaluation_procedure, fit_test_procedure, training_procedure, cartridge_change_schedule, program_last_reviewed_on.
- **`Employee Training Record`** — add `content_topics_covered` (multi-select or child, enum matches the six required heat-illness topics from FS-91) and `demonstrated_understanding` (bool + method) for PPE and heat compliance.
- **`Compliance Calendar`** — add `posting_window_start` / `posting_window_end` for records like the OSHA 300A summary (Feb 1–Apr 30) that have a posting window rather than a due date.
- **`generate_audit_packet` — new mode: `binder=OR-OSHA-Inspection`.** On demand, assemble: written programs binder, sample-employee training records for last 12 months, OSHA 300 log for current + prior 4 years, current 300A summary, housing registration for current year, heat illness plan + acclimatization + emergency medical plan, field sanitation station checks for last 12 months, PPE hazard assessment + training records, WPS handler/worker training records, Hazard Communication written program + SDS index. Should be generatable in <5 minutes from an SMS trigger.

---

## 3. FDA FSMA Produce Safety Rule (21 CFR Part 112)

Fresh cherries appear by name in the covered-produce list at **21 CFR 112.1(b)(1)** — "cherries (sweet)" (source: https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-A/section-112.1). Any Tim-cherry sold intact for human consumption is covered produce. In Oregon, ODA runs FSMA on-farm inspections under FDA contract — the same ODA inspector may be doing a GAP audit one day and an FSMA Produce Safety inspection the next.

### A. Coverage Threshold and Exemptions

- **Covered farm** if average annual produce sales (rolling 3-year, inflation-adjusted from 2011 baseline) exceed **$25,000** — verbatim from **21 CFR 112.4(a)** (source: https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-A/section-112.4). "A farm or farm mixed-type facility with an average annual monetary value of produce … sold during the previous 3-year period of more than $25,000 (on a rolling basis), adjusted for inflation using 2011 as the baseline year for calculating the adjustment, is a 'covered farm' subject to this part."
- **Qualified exemption** available under **21 CFR 112.5** if BOTH: (1) during the previous 3-year period, average annual monetary value of food sold directly to **qualified end-users** exceeded the value sold to all other buyers; AND (2) average annual monetary value of **all food** the farm sold during the 3-year period was less than **$500,000, adjusted for inflation** from 2011 (source: https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-A/section-112.5). "Qualified end-user" is the consumer OR a restaurant / retail food establishment in the same state or within 275 miles.
- **Below-$25k farm**: not covered at all.
- **Qualified-exempt farm**: subject to §112.6 modified requirements (label with farm name & address) and to the §112.7 recordkeeping — but **not** the full Subpart D-M records. This changes the doctype work drastically depending on Tim's revenue tier.

For a family cherry operation moving majority-wholesale to packing sheds and processors, expect **fully covered** status (>$25k, doesn't meet the direct-to-end-user majority test). Design for the fully covered case; qualified-exempt would be a lighter-weight subset.

### B. Records Required (fully covered farm)

**No "Food Safety Plan" is required by the Produce Safety Rule.** This is a real difference from USDA GAP. FSMA Part 112 is a prescriptive-rule regulation — the farm must follow the rules and keep the specific records the rules generate. There is no single anchor document analogous to the GAP food safety plan or the NOP OSP. (Contrast: Preventive Controls for Human Food, 21 CFR Part 117, does require a Food Safety Plan — but that is not what applies to farms as farms. When Tim runs a packing shed that is not a "farm" under the FDA definition, that changes.)

That said, records are still substantial. The rule organizes records by subpart:

| Record | Rule anchor | Our doctype | Retention |
|---|---|---|---|
| Written procedures for taking measurements of ag water quality (if applicable, per revised Subpart E — Agricultural Water) | Subpart E | Compliance Policy | 2 yr past use |
| Agricultural water inspection findings + corrective actions | Subpart E | Water Test + Compliance Alert | 2 yr |
| Agricultural water testing analytical results | Subpart E | Water Test | 2 yr past use |
| Biological soil amendment of animal origin — receiving, treatment status, application date + application-to-harvest interval | Subpart F | Farm Task (subtype: Amendment) | 2 yr |
| Supplier documentation for treated BSAAO | Subpart F | Attachment | 2 yr |
| Sprouts — not applicable (Tim doesn't grow sprouts, but the Sprouts subpart is where the most-detailed records live for those who do) | Subpart M | — | — |
| Domesticated + wild animal monitoring — significant animal-intrusion evidence + response | Subpart I | Farm Task (subtype: Wildlife Monitoring, new) or Compliance Alert | 2 yr |
| Growing, harvesting, packing, holding activity records — measures to prevent contamination + corrective actions | Subpart K | Farm Task + Compliance Alert | 2 yr |
| Personnel qualifications and training records — date, topics covered, persons trained | Subpart C, §112.30(b) | Employee Training Record (subtype: FSMA Produce Safety) | 2 yr |
| Records of qualification and training of the individual designated to be a supervisor per §112.22(c) | Subpart C | Employee Training Record (subtype: FSMA Supervisor Qualification) | 2 yr |
| Equipment, tools, buildings, sanitation records — cleaning/sanitizing of food-contact surfaces | Subpart L | Facility Inspection (subtype: Food-Contact Sanitation) | 2 yr |
| Records substantiating status as a qualified-exempt farm (only if claiming §112.5 exemption) | §112.7(b) | Governance Document + Sales log | As long as necessary to support current-year status |
| Annual review-and-verification record of continued qualified-exemption eligibility | §112.7(b) | Audit Event (subtype: Qualified-Exemption Annual Review, new) | As long as necessary |

**Retention rule (all §112 records unless otherwise specified):** at least **2 years past the date the record was created** — verbatim from **21 CFR 112.164(a)(1)**: "You must keep records required by this part for at least 2 years past the date the record was created." Records for equipment or process adequacy: 2 years past the discontinuation of that equipment or process. Qualified-exemption records: as long as necessary to support current-year status (source: https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-O/section-112.164).

**General record requirements** (verbatim from **§112.161**, source: https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-O/section-112.161): records must (i) include farm name and location; (ii) actual values and observations obtained during monitoring; (iii) adequate description of covered produce; (iv) location of the growing area or other area; (v) date and time of activity; (2) be created at the time the activity is performed or observed; (3) be accurate, legible, and indelible; (4) be dated, and signed or initialed by the person who performed the activity documented. Certain higher-consequence records (Subpart K §112.140, worker qualifications §112.30, water §112.50, etc.) must also be **reviewed, dated, and signed within a reasonable time after the record is made by a supervisor or responsible party** (§112.161(b)).

**No Food Safety Plan required — but the GAP Food Safety Plan works as a substitute recordkeeping architecture.** If Tim maintains the GAP plan required in the prior doc, the FSMA records can hang inside that plan's SOPs and logs. The gap: **FSMA § requirements are literal record types**, not narrative sections. Our doctypes must produce the literal records with the §112.161 fields on every one.

### C. Field Data Captured at Time of Activity

For every §112 record, per §112.161(a)(1), every activity capture must include:

- Farm name and location
- Actual values and observations obtained during monitoring
- Adequate description of covered produce (commodity name, variety, brand name, lot number or other identifier)
- Location of growing area (specific field/block) or other area (specific packing shed)
- Date and time of activity
- Person performing the activity — signed or initialed
- Supervisor review + signature (for the §112.161(b) list — includes worker training records, ag water records, and Subpart K activity records)

**Ag water testing (Subpart E)** — sample date, source ID, source type (surface / GW / municipal / RO / other), intended use (pre-harvest, harvest/post-harvest for food-contact), analyte (generic *E. coli* CFU/100 mL for pre-harvest surface water), result value, sampling method, lab name (ISO 17025 preferred), corrective action if outside criteria. FDA revised Subpart E in 2024–2025; keep an eye on the current text at https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-E — the "microbial water quality profile" model was replaced by an agricultural water assessment.

**Biological soil amendment of animal origin (Subpart F)** — receipt date, supplier, treatment status (untreated / composted per National Organic Program interval / other), application date, application method (surface / incorporated), field/block, application-to-harvest interval assumed.

**Wildlife/domesticated animal monitoring (Subpart I)** — date, area/block observed, animal species/evidence type, severity (isolated / significant intrusion), corrective action (not harvesting affected produce is the common response, per §112.112).

**Harvest / packing / holding activities (Subpart K)** — task type, crew, produce, lot code, container_type + cleanout_status, corrective actions for observed contamination, equipment sanitation performed, water contact status (if water was applied).

**Personnel training (§112.30)** — date, topics covered, persons trained (roster).

**Food-contact surface sanitation (Subpart L)** — surface identifier (tote, sorter belt, bin, hose, tank), cleaning agent, sanitizer, concentration, contact time, personnel who performed, verification method (visual, ATP swab, etc.).

### D. Cadence

FSMA doesn't set a single audit cadence like GAP or NOP. Instead:

- **On-site inspection** — driven by FDA/ODA risk-tiering. For a covered farm the target is once every 3-5 years in a routine cycle, more frequently if issues surface. In Oregon, FDA-contracted ODA is the boots-on-the-ground inspector for FSMA — same organization as GAP but different rule set.
- **Ag water testing (Subpart E)** — cadence depends on source type and updated Subpart E rule; do at least annual per untreated source used during production and per untreated source used during harvest activities, more if surface source.
- **Biological soil amendment application-to-harvest interval** — per §112.56 (specific intervals dependent on treatment status). Interval is baked into the operation, not a separate cadence.
- **Training** — the rule requires training upon hiring and periodically thereafter "at a frequency deemed necessary" (§112.21). Best practice is annual, matching WPS.
- **Qualified-exemption annual review** — annual per §112.7(b).
- **Records supervisor review** — "within a reasonable time" per §112.161(b). Best practice: weekly for daily-generated records.

### E. Audit Format Expectations

FDA/ODA inspections under FSMA are typically **announced but with short lead time** (a week or two). The inspector will:

1. Present FDA credentials (or ODA credentials with FDA authority).
2. Review the farm's coverage/exemption status — sales records aggregated over 3 years.
3. Ask for the ag water assessment and testing records.
4. Ask for BSAAO records (source, treatment, application, interval).
5. Ask for training records — 112.30 format.
6. Walk the operation observing harvest, packing, holding.
7. Discuss corrective-action logs for animal intrusion, water non-conformances, contamination events.
8. Exit interview with observations. Written observations issued as FDA Form 483 for objectionable conditions; ODA equivalent may be a Notice of Observation.

Because ODA runs both GAP audits (voluntary, buyer-driven, USDA scheme) and FSMA inspections (mandatory, regulatory, FDA-contracted) using overlapping personnel, **assume the same auditor may return in a different hat**. Records prepared for GAP will largely serve FSMA — but not entirely, because FSMA's §112.161 record-format requirements are prescriptive in a way GAP's aren't. In particular, **supervisor-signature-within-reasonable-time on the specific §112.161(b) records is FSMA-only** and is a common gap in GAP-only operations.

**Deliverable expectation for our audit-packet generator, FSMA variant:** a PDF that walks subpart by subpart — coverage/exemption evidence (Subpart A), personnel qualifications and training (Subpart C), ag water assessment and testing (Subpart E), soil amendments (Subpart F), animal monitoring and response (Subpart I), growing/harvesting/packing/holding activities (Subpart K), equipment sanitation (Subpart L), other records (Subpart M) — with the §112.161 form on the front of each log page.

### F. Common Failure Modes

- **Coverage misclassification** — operator believes qualified-exempt applies when the direct-to-qualified-end-user majority test fails, so records that would have been kept aren't. First finding on inspection is usually a demand for §112.5 substantiation records.
- **Missing supervisor sign-off on the §112.161(b) records** — activity was performed and initialed by the worker but not reviewed / dated / signed by a supervisor within a reasonable time. FDA-cited even when the underlying activity was fine.
- **Ag water assessment out of date or missing** for a surface-water source used pre-harvest or in harvest activities.
- **BSAAO treated as raw manure and applied without documenting an application-to-harvest interval** — automatic non-conformance.
- **Animal intrusion evidence observed but no monitoring/response record** — Subpart I is heavily inspector-observed on-site; if the inspector sees droppings and nothing in the log, that's a finding.
- **Training records missing dates / topics / persons trained** — §112.30 spells this out verbatim; anything less is deficient.
- **Records dated later than the activity** — violates §112.161(a)(2) ("Be created at the time an activity is performed or observed").
- **Records unsigned or illegibly signed** — violates §112.161(a)(3)-(4).
- **Cleaning and sanitation of food-contact surfaces performed but not logged** — Subpart L non-conformance.
- **Qualified-exemption withdrawal in play** — if an outbreak is traced to a farm, FDA can withdraw the exemption per Subpart R; the farm has no records to fall back on.

### G. Records Our Doctypes Don't Have Yet (FSMA overlay)

- **`§112.161 compliance fields` on every activity-generated doctype** (Farm Task, Water Test, Employee Training Record, Facility Inspection, Spray Record). Fields: `farm_name` (denormalized snapshot), `farm_location_gps`, `activity_datetime` (not date-only — time required), `person_performed_signature`, `supervisor_reviewed_by`, `supervisor_reviewed_on`, `supervisor_signature`. Denormalization matters: rule requires each individual record to carry these; a Farm-level lookup at report time doesn't satisfy an FDA inspector paging through logs.
- **Enum `regime`** on `Compliance Alert` and `Employee Training Record` — add `21-CFR-112` and each subpart (`FSMA-Subpart-C-Training`, `FSMA-Subpart-E-Water`, `FSMA-Subpart-F-Amendments`, `FSMA-Subpart-I-Animals`, `FSMA-Subpart-K-Activities`, `FSMA-Subpart-L-Sanitation`, `FSMA-Subpart-M-Records`).
- **New doctype: `FSMA Coverage Determination`.** Fields: fiscal_year, produce_sales_ttm (3-year avg), all_food_sales_ttm (3-year avg), direct_to_qualified_end_user_share_pct, computed_coverage_status enum (Not-Covered / Covered / Qualified-Exempt), computed_by, computed_on, sales_evidence_attachments (child), annual_review_verified_by, annual_review_verified_on.
- **New doctype: `Wildlife/Animal Intrusion Monitoring`.** Fields: date, area/block, monitoring_method, species_evidence, severity, decision (harvest / do-not-harvest / other), corrective_action_link, observer, signed_by_supervisor_on.
- **New doctype: `Food-Contact Surface Sanitation Event`.** Fields: surface_id, surface_type, cleaning_agent, sanitizer_product, sanitizer_ppm, contact_time_sec, water_source_id (if rinse), performed_by, performed_datetime, verification_method (visual / ATP / other), verification_result, supervisor_reviewed_by, supervisor_reviewed_on.
- **New doctype: `BSAAO Application`** (Biological Soil Amendment of Animal Origin) — subtype under Farm Task or standalone. Fields: amendment_product, supplier, treatment_status (untreated / composted-NOP-interval / heat-treated / other), treatment_evidence_attachment, application_datetime, block/parcel, application_method, application_to_harvest_interval_days, planned_harvest_date, computed_interval_ok (bool).
- **`Water Test`** — add `intended_use` values `FSMA-Preharvest`, `FSMA-Harvest-Food-Contact`; add `sampling_procedure_link` (FK to SOP); add `supervisor_reviewed_by` + date.
- **`generate_audit_packet` — new mode: `regime=FSMA`.** Sections: Coverage/Exemption, Subpart C Training, Subpart E Water, Subpart F Amendments, Subpart I Animals, Subpart K Activities, Subpart L Sanitation, Subpart M Records. Every logged record printed with the §112.161 form applied.
- **New audit event subtype: `Qualified-Exemption Annual Review`.** Fields: review_date, reviewer, sales_evidence_snapshot, direct-to-qualified-end-user_share_pct, all_food_sales_3yr_avg, exemption_status_maintained (bool).

---

## 4. Cross-Regime Overlaps and Consolidation Notes

Because ODA does GAP under one hat and FSMA under another (contracted for FDA), and OTCO's OSP shares a huge amount of underlying content with the GAP food safety plan, and OR-OSHA's field-sanitation rule at -1110 overlaps directly with GAP's handwash / drinking-water requirement and FSMA's Subpart D personnel-hygiene section, **the same doctype should carry all regime tags for a single event and be searchable per regime**. Two design implications:

1. **Multi-regime tagging is required on every activity record**, not just on Compliance Alerts. Add a `regimes` multi-select field to Farm Task, Water Test, Employee Training Record, Facility Inspection, and Spray Record. This lets `generate_audit_packet` filter to the right subset per audit.
2. **A single Employee Training Record can satisfy multiple regime training obligations** if the topics covered include all required content. Add `content_topics_covered` as multi-select and let a single training event tick WPS-Worker, FSMA-Subpart-C, and GAP-Hygiene at once — providing the trainer covered all three curricula.

Retention: use the **longest applicable retention** for a multi-regime record. Simple rule: if any regime tag is `NOP`, retain 5 years; else if `21-CFR-1904` (OSHA 300), 5 years past calendar year; else if `29-CFR-1910.1020` (exposure/medical), 30 years; else 2–3 years per the specific regime.

---

## 5. Doctype/Field Additions Not Already In the Prior Doc

Consolidated for the parent agent so this can be diffed against the prior doc §3 without re-reading. Only new items:

**New doctypes (5):**

1. `Organic Fraud Prevention Plan` (Governance Document subtype) — OTCO / NOP SOE.
2. `Heat Exposure Event` — OR-OSHA -1131.
3. `Field Sanitation Station Check` (or subtype of Facility Inspection) — OR-OSHA -1110.
4. `OSHA 300 Log Entry` — adopted 29 CFR 1904.
5. `Respiratory Protection Program` (Compliance Policy subtype) — OR-OSHA -1041 + -6508.
6. `FSMA Coverage Determination` — 21 CFR 112.4 / .5 / .7.
7. `Wildlife/Animal Intrusion Monitoring` — FSMA Subpart I.
8. `Food-Contact Surface Sanitation Event` — FSMA Subpart L.
9. `BSAAO Application` — FSMA Subpart F.

(That's nine — several align to prior-doc gaps but weren't listed there.)

**New universal fields on every activity-generated doctype (Farm Task, Water Test, Employee Training Record, Facility Inspection, Spray Record):**

- `farm_name_snapshot`, `farm_location_gps` (denormalized per §112.161(a)(1))
- `activity_datetime` (must include time)
- `person_performed_signature`
- `supervisor_reviewed_by`, `supervisor_reviewed_on`, `supervisor_signature`
- `regimes` multi-select (see below)
- `content_topics_covered` (multi-select) on Employee Training Record

**Enum extensions:**

- `Compliance Alert.regime` — add `OAR-437-004-1110`, `OAR-437-004-1131`, `OAR-437-004-9800`, `OAR-437-004-1005`, `OAR-437-004-1041`, `OAR-437-004-6501`, `OAR-437-004-6508`, `OAR-437-002-0170`, `29-CFR-1904`, `29-CFR-1910.1020`, `21-CFR-112`, `21-CFR-112-Subpart-C`, `21-CFR-112-Subpart-E`, `21-CFR-112-Subpart-F`, `21-CFR-112-Subpart-I`, `21-CFR-112-Subpart-K`, `21-CFR-112-Subpart-L`, `21-CFR-112-Subpart-M`. **Remove** any `OAR-437-004-1090` or `OAR-437-004-1130` from existing memory-file enums (rename to -1120 and -1131 respectively).
- `Employee Training Record.training_topic_taxonomy` — add `Heat-Illness-Prevention`, `Field-Sanitation-Hygiene`, `Hazard-Communication`, `Respirator-Fit-Test`, `Respirator-Medical-Clearance`, `Seasonal-Safety-Orientation`, `PPE-General`, `FSMA-Produce-Safety`, `FSMA-Supervisor-Qualification`.
- `Audit Event.subtype` — add `Traceback Audit`, `Mass Balance Audit`, `Sales Audit`, `Pre-Inspection Checklist`, `Qualified-Exemption Annual Review`, `Safety Meeting`, `Injury/Illness — OSHA 300`.

**`generate_audit_packet` new modes:**

- `regime=NOP, aca=OTCO` — OTCO-shaped MyOTCO packet.
- `binder=OR-OSHA-Inspection` — on-demand inspection binder mode (target <5 min from SMS trigger).
- `regime=FSMA` — Subpart-organized packet with §112.161 form on each log.

---

## 6. Sources

**Oregon Tilth:**
- Certification Eligibility and Requirements — https://tilth.org/help-center/certification-eligibility-and-requirements/
- The Organic System Plan (OSP): The Foundation of Organic Certification — https://tilth.org/help-center/the-organic-system-plan-osp-the-foundation-of-organic-certification/
- Understanding Organic Inspections — https://tilth.org/help-center/understanding-organic-inspections/
- Recordkeeping for Farms — https://tilth.org/help-center/recordkeeping-for-farm/
- OFPP Resource Guide — https://tilth.org/help-center/organic-fraud-prevention-plan-ofpp-resource-guide/ (linked from Help Center; full contents not fetched — verify if quoting specific text)
- OTCO Help Center index — https://tilth.org/help-center/
- Commercial Availability Form (docx) — https://tilth.org/wp-content/uploads/2025/04/CommercialAvailabilityForm.docx
- OTCO Crop Inspection Checklist (docx) — https://tilth.org/wp-content/uploads/2014/12/OTCO_Crop_InspectionCheckConfirmation.docx

**Oregon OSHA (state-plan agriculture):**
- OAR 437-004-1120 (Agricultural Labor Housing and Related Facilities) — https://oregon.public.law/rules/oar_437-004-1120
- OAR 437-004-1110 (Field Sanitation for Hand Labor Work) — https://oregon.public.law/rules/oar_437-004-1110
- OAR 437-004-1005 (General Requirements for Protective Equipment) — https://oregon.public.law/rules/oar_437-004-1005
- OAR 437-004-9800 (Hazard Communication Standard for Agricultural Employers) — https://oregon.public.law/rules/oar_437-004-9800
- OAR Chapter 437 Division 4 rule index — https://oregon.public.law/rules/oar_chapter_437_division_4
- OAR Chapter 437 Division 2 rule index — https://oregon.public.law/rules/oar_chapter_437_division_2
- OR-OSHA Fact Sheet FS-91 — Key Requirements: Oregon OSHA's Permanent Rules for Heat Illness Prevention (10/23) — https://osha.oregon.gov/OSHAPubs/factsheets/fs91.pdf (confirms heat rules 437-002-0156 and 437-004-1131, adopted May 9, 2022)
- OR-OSHA Program Directive PD-299 — Local Emphasis Program: Preventing Heat Related Illness — https://osha.oregon.gov/OSHARules/pd/pd-299.pdf (title only from search results; full content not fetched)

**Not directly retrieved (attempted, failed or deferred):**
- OR-OSHA Division 4 Subdivision H PDF (`https://osha.oregon.gov/OSHARules/div4/div4H.pdf`) — timed out at 180 s. Rule content used instead from oregon.public.law per-rule pages, which are the same text sourced from the same SoS-OARD authority.
- OR-OSHA Heat Rules All 2022 PDF (`https://osha.oregon.gov/rules/advisory/heatillness/Documents/Heat_Rules_All_2022.pdf`) — timed out. Fact sheet FS-91 used instead.
- OR-OSHA Program Directive PD-299 content — search result title only; body not fetched. Cite for concept (heat LEP exists since 2021) but not for specifics.
- 21 CFR 112.140 (Subpart K activity records) — timed out at 180 s. Content described from §112.161 supervisor-review cross-reference and rule structure knowledge; verify verbatim before quoting in an SOP.

**FDA / eCFR:**
- 21 CFR 112.1 — What food is covered — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-A/section-112.1
- 21 CFR 112.4 — Which farms are subject — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-A/section-112.4
- 21 CFR 112.5 — Qualified exemption — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-A/section-112.5
- 21 CFR 112.7 — Records if qualified-exempt — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-A/section-112.7
- 21 CFR 112.30 — Training records — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-C/section-112.30
- 21 CFR 112.161 — General requirements for records — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-O/section-112.161
- 21 CFR 112.164 — Retention (2 years) — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112/subpart-O/section-112.164
- Part 112 top-level TOC — https://www.ecfr.gov/current/title-21/chapter-I/subchapter-B/part-112?toc=1

---

## 7. Corrections to Memory Files

For the parent agent to propagate — the following legacy citations in saved memory files (including `project_labor_camp_housing_erpnext.md`, `project_farm_hr_architecture.md`, and any Compliance Alert regime enum stored in a doctype schema) should be updated:

1. **`OAR 437-004-1090`** → **`OAR 437-004-1120`** (Agricultural Labor Housing and Related Facilities). The -1090 citation is superseded/renumbered.
2. **`OAR 437-004-1130`** → **`OAR 437-004-1131`** (Heat Illness Prevention, agricultural). The -1130 number is not the live rule; -1131 was adopted 9 May 2022 alongside the non-ag companion -002-0156.
3. Wherever `OAR 437-004-1005` was cited for sanitation, correct to **`OAR 437-004-1110`** (Field Sanitation for Hand Labor Work) or **`OAR 437-004-1105`** (General Sanitation). -1005 is General Requirements for Protective Equipment (PPE), a different rule cluster.
