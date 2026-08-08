# Federal Forms Template Repository

**Status:** Draft for review
**Target:** v0.48.x – v0.52.x (five phases)
**Author:** design pass, 2026-08-07
**Supersedes:** nothing. Generalizes the I-9 pipeline shipped v0.27.0 – v0.47.1.

---

## 0. Why this document exists

The I-9 pipeline works. It fills the real USCIS PDF, attaches the result to the
record, prints it, and takes a signed scan back. Nothing else in the app does
that. Every other government form the app knows about — W-2, 1099-NEC, 941,
OQ, OR-WR, the WA ESD report — gets its numbers computed correctly by
`form_generators.py` and then drawn onto a **reportlab facsimile** that says, on
every page, that it is not a filing.

That gap is fine for a working copy and wrong for a form somebody signs. The
fix is not to write a second I-9-shaped pipeline per form type. It is to take
the four things `i9_pdf.py` hardcodes — the template path, the edition string,
the SHA-256, and the field map — and make them **rows in a table** instead of
**constants in a module**.

Two requirements drive the shape of everything below:

> **"Make sure we keep the historical ones on file as well."**
> Templates are never deleted. Generated forms are never overwritten. A form
> filled in 2025 must still be explainable in 2029 against the template it was
> actually filled from.

> **"Traceability is a thing. Not for fault but to fix the problem not the blame."**
> When a W-2 box is wrong, the question is *which link in the chain produced the
> wrong number* — the W-4 election, the withholding calc, a mid-year W-4
> supersession, or the year-end aggregation. Today two of those four links are
> not recorded. Section 3 says which.

---

## 1. What exists today

Read this before reviewing the proposal. Most of the work is already done, in
two incompatible halves.

### 1.1 The I-9 half — real government PDF, hardcoded template

| Concern | Where it lives today |
|---|---|
| Template bytes | `erpnext_mcp/templates/i9_form.pdf`, shipped byte-for-byte |
| Template path | `i9_pdf.TEMPLATE_PATH` (module constant) |
| Edition string | `i9_pdf.EDITION` (module constant) |
| Integrity check | `i9_pdf.TEMPLATE_SHA256` (module constant), asserted by `tests_standalone/test_i9_pdf.py` |
| Field map | ~133 AcroForm field names spread across Python dicts in `i9_pdf.py` (`CITIZENSHIP_BOXES`, `DOCUMENT_FIELDS`, `_section_1`, `_section_2`, `_supplement_a`, `_supplement_b`) |
| Fill | `i9_pdf.fill_i9_pdf()` — `PdfWriter(clone_from=TEMPLATE_PATH)`, in-memory, template never edited |
| Store | `tools/i9.py::render_i9_pdf` → `artifacts.attach_bytes(..., field="generated_pdf")` |
| Signed scan back | `tools/i9.py::attach_signed_i9` → `signed_pdf` |
| Audit | `I-9 Audit Log` doctype, 13 actions including `Printed`, `Exported`, `Destroyed` |
| Retention | `I-9 Form.retention_until` / `.destruction_eligible_date`, `get_i9_retention_report`, `destroy_i9` |

This is the pattern worth generalizing. It is also already showing the failure
mode this design is meant to prevent: **the repo states the I-9 edition in two
places and they disagree.** `i9_pdf.EDITION` says `Edition 01/20/25`;
`templates/README.md` says `Rev. 08/01/23`. Both claim expiry 05/31/2027. One
of them is stale, and nothing in the test suite can tell which, because the
edition is prose in two files rather than a value in one row. Resolving that
discrepancy is a Phase 1 task, not a separate bug.

### 1.2 The tax-form half — real numbers, facsimile page

| Concern | Where it lives today |
|---|---|
| Box arithmetic | `form_generators.py` — W-2, 1099-NEC, 941, OR-WR, OQ, WA-ESD. Pure functions, no DB. |
| Record | `Tax Form` doctype — `form_data_json` (snapshot), `generated_pdf`, `generated_by`, `generated_date`, `amends`, `status`, `confirmation_number` |
| Tools | `tools/taxforms.py` — `generate_tax_form`, `regenerate_tax_form`, `render_tax_form_pdf`, `bulk_render_tax_form_pdfs`, `mark_tax_form_filed` |
| Page | `form_pdf_renderer.py` — reportlab, draws official box numbering in roughly official arrangement, **stamps a disclaimer naming the real filing channel on every page** |
| Audit | none |
| Retention | none |

`Tax Form` is already about 80% of the traceability model this design needs. It
snapshots the data at generation, links amendments via `amends`, and records who
generated it and when. What it does not record is **which template edition
produced the page** — because today there is no template, only a renderer.

### 1.3 The W-4 → payroll chain

Tim asked whether `preview_federal_withholding` and `calculate_payroll_taxes`
actually read the W-4 Form doctype. **They do.** Both call
`tools/w4.py::_load_w4_data` (line 387), which queries `W-4 Form` filtered on
`status = "Active"`, ordered by `tax_year desc, effective_date desc`. The
integrated payroll run reaches the same data by a different path:
`tools/payroll.py::_load_w4_data` (line 1532) → `build_payroll_inputs` →
`payroll_calc.calculate_full_payroll` → `withholding.calculate_federal_withholding`.

So the wiring is not the gap. The gaps are four smaller ones, listed in §3.2.

---

## 2. Template Repository

### 2.1 `Form Template` doctype

One row per *edition* of one *form type*. Not submittable — a template is
reference data, not a transaction.

| Field | Type | Notes |
|---|---|---|
| `naming_series` | Select | `FTPL-.####` |
| `form_type` | Link → `Form Type` | see §2.2 |
| `edition` | Data, reqd | Exactly as printed on the form: `Rev. 01/2025`, `Edition 08/01/23`, `2025` |
| `agency` | Select | `USCIS` / `IRS` / `SSA` / `Oregon DOR` / `Oregon OED` / `Oregon DCBS` / `WA ESD` / `WA L&I` |
| `omb_number` | Data | `1615-0047`, `1545-0008`, … blank where the agency assigns none |
| `omb_expires` | Date | The expiry printed on the form, not our retention date |
| `effective_from` | Date, reqd | First date this edition may be used for a *new* fill |
| `effective_through` | Date | Null while current. Set when superseded — **never** used to delete |
| `status` | Select, reqd | `Active` / `Superseded` / `Withdrawn` |
| `superseded_by` | Link → `Form Template` | Points forward to the replacement |
| `template_pdf` | Attach | The government PDF, byte-for-byte |
| `template_sha256` | Data, read-only | Computed on save from the attached bytes |
| `template_bytes` | Int, read-only | Sanity check alongside the checksum |
| `acroform_field_count` | Int, read-only | Introspected on save; a drop to 0 means XFA-only or a flattened file |
| `field_map_json` | Code (JSON), reqd | see §2.3 |
| `fill_module` | Data | Optional dotted path to a form-specific hook for what JSON cannot express |
| `copy_a_prohibited` | Check | see §2.5 |
| `source_url` | Data | Where it was downloaded from |
| `notes` | Text | Free-text migration notes for the next person |

**Invariants, enforced in `validate()`:**

1. `template_sha256` is recomputed from the attached file on every save. If it
   changes on a row that already has generated artifacts pointing at it, the
   save is **rejected** — you create a new edition, you do not re-point an old
   one.
2. At most one `Active` row per `form_type` at any instant. Activating a new
   edition sets the prior `Active` row to `Superseded`, stamps its
   `effective_through`, and sets its `superseded_by`.
3. `Superseded` and `Withdrawn` rows are read-only and **cannot be deleted**.
   A `before_cancel`/`on_trash` hook raises. This is the "keep the historical
   ones on file" requirement, enforced rather than documented.
4. `effective_from` on a new edition must be ≥ `effective_from` of the row it
   supersedes.

### 2.2 `Form Type` doctype

A small reference table so `form_type` is a Link, not a free-for-all Select that
has to be migrated every time a form is added. One row per form, seeded by
`install.py`.

| Field | Notes |
|---|---|
| `form_code` | `I-9`, `W-4`, `W-2`, `W-3`, `1099-NEC`, `1099-MISC`, `1096`, `OR-W-4`, `OQ`, `132`, `WA-LNI-Q` |
| `label` | `Employment Eligibility Verification` |
| `jurisdiction` | `Federal` / `OR` / `WA` |
| `category` | `Onboarding` / `Year-End Employer` / `Compliance` / `Quarterly` |
| `subject_scope` | `Employee` / `Contractor` / `Company` |
| `period` | `Event` / `Quarter` / `Year` |
| `source_doctype` | `I-9 Form`, `W-4 Form`, `Tax Form` — where the filled record lives |
| `retention_rule` | see §6 |
| `filed_electronically` | Check — true where paper is not the filing channel (OQ, WA ESD) |

This table is also the inventory in §4, made queryable instead of prose.

### 2.3 The field map

`field_map_json` is a JSON object mapping **source paths** to **AcroForm field
names**, with an optional transform. It replaces the Python dicts in
`i9_pdf.py`.

```json
{
  "version": 1,
  "pages": {"form": 0, "lists": 1, "supplement_a": 2, "supplement_b": 3},
  "text": {
    "legal_last_name":  {"field": "Last Name (Family Name)", "page": "form"},
    "legal_first_name": {"field": "First Name Given Name",   "page": "form"},
    "date_of_birth":    {"field": "Date of Birth mmddyyyy",  "page": "form",
                         "transform": "us_date"},
    "ssn_full":         {"field": "US Social Security Number","page": "form",
                         "transform": "ssn_digits", "sensitive": true}
  },
  "checkboxes": {
    "citizenship_status": {
      "page": "form",
      "values": {
        "US Citizen":           "CB_1",
        "Noncitizen National":  "CB_2",
        "Lawful Permanent Resident": "CB_3",
        "Alien Authorized to Work":  "CB_4"
      }
    }
  },
  "repeating": {
    "reverifications": {
      "page": "supplement_b",
      "max_rows": 3,
      "overflow": "additional_information",
      "row_fields": {
        "document_title": "Document Title 1 (Supplement B)",
        "document_number": "Document Number Supplement B"
      }
    }
  }
}
```

**Transforms are a closed vocabulary**, implemented once in the renderer and
named by string: `us_date`, `ssn_digits`, `initial`, `state_code`, `money`,
`whole_dollars`, `upper`, `bool_x`. The map is data; it never carries code.
Anything a transform cannot express — the I-9's shared-title field split, its
overflow into Additional Information, its COMB-field font handling — stays in
Python behind `fill_module`. That is the escape hatch, and the I-9 will use it.

**Rationale for JSON-in-a-field over a child table:** a child table gives Desk
editing and referential tidiness; JSON gives one atomic versioned blob that can
be diffed against the previous edition in one `git diff`-shaped view. Since the
map is written by a developer at edition-swap time and never by an operator, the
blob wins. It is validated against a schema on save.

### 2.4 Fill pipeline

```
Form Template (Active for form_type)
        │  bytes + field_map_json + sha256
        ▼
form_fill.py  ──  generic AcroForm filler
        │         PdfWriter(clone_from=<template bytes>)
        │         template file is opened read-only, never edited
        ▼
    PDF bytes  ──►  artifacts.attach_bytes(source_doctype, name, field="generated_pdf")
        │
        └──►  Form Artifact row (§3.1)
```

`form_fill.py` is a new module with the same contract as `withholding.py`,
`payroll_calc.py`, and `form_generators.py`: **pure function, no DB reads, no
attachment writing.** Template bytes, field map, and a record dict go in; PDF
bytes come out. This is what makes it testable against a fixture, and it is the
contract the codebase already uses everywhere the arithmetic matters.

### 2.5 The Copy A rule

`form_pdf_renderer.py` already knows this and the template repository must not
lose it: **Copy A of a W-2 or a 1099 is printed in a scannable red ink no laser
printer reproduces.** Filing a black-and-white Copy A draws a penalty. Oregon's
OQ and Washington's ESD report are filed through Frances Online and ESD's
portal, not on paper.

So `Form Template` carries `copy_a_prohibited`, and:

- Templates are stocked for the copies an employer legitimately prints:
  **W-2 Copy B, C, and 2; 1099-NEC Copy B and C.** These are fillable IRS PDFs.
- No Copy A template is ever stocked. `copy_a_prohibited` on the form type makes
  the renderer refuse a Copy A request by name, with the SSA/IRS e-file channel
  in the error message.
- Where a form has no printable copy at all (OQ, WA ESD, Form 132), the
  `Form Type` row sets `filed_electronically`, and the existing reportlab
  facsimile with its disclaimer block **stays** as the review/keying copy. This
  design does not replace it; it sits alongside.

### 2.6 The edition-swap procedure

This is the operational core of the whole design, and it is deliberately the
same procedure that `templates/README.md` documents for the I-9 today — just
generalized and moved into data.

1. Download the new PDF from the agency.
2. `import_form_template(form_type, edition, file_token, effective_from)` —
   creates a **new** `Form Template` row in `Draft`, computes the checksum,
   introspects the AcroForm field names.
3. `diff_form_template_fields(new, old)` reports which field names moved,
   appeared, or vanished. This is the report that tells you how much of the
   field map needs editing.
4. Edit `field_map_json` on the new row.
5. `validate_form_template(name)` does a dry fill against a synthetic record and
   asserts every mapped field name exists in the PDF. **A field name in the map
   that is not in the PDF is a hard error**, because a silently-dropped value is
   the failure mode that produces an unfileable form.
6. `activate_form_template(name)` — supersedes the prior edition, stamps dates,
   points `superseded_by`.
7. Old artifacts keep pointing at the old template row. Nothing regenerates.

The checksum test that catches an unannounced template change becomes a suite
test that walks every `Form Template` row and asserts stored checksum equals
computed checksum. One test, all form types, instead of one constant per module.

---

## 3. Form lifecycle and traceability

### 3.1 `Form Artifact` doctype

Every generated PDF gets one row. The row is the traceability record; the PDF is
the payload.

| Field | Notes |
|---|---|
| `naming_series` | `FART-.YYYY.-.#####` |
| `form_type` | Link → `Form Type` |
| `form_template` | Link → `Form Template` — **the exact edition used** |
| `template_sha256` | Data — copied at generation, so the artifact is verifiable even if the template row is later touched |
| `source_doctype` | `I-9 Form` / `W-4 Form` / `Tax Form` |
| `source_name` | Dynamic Link |
| `employee` / `related_party` / `company` | Denormalized for querying without joins |
| `tax_year`, `period_start`, `period_end` | For year-end forms |
| `artifact_kind` | `Generated` / `Signed Scan` / `Amended` |
| `copy_designation` | `Copy B`, `Copy C`, `Copy 2`, `Employee`, `Employer` |
| `file` | Attach — the immutable PDF |
| `file_sha256` | Data — the artifact's own checksum |
| `data_snapshot_json` | Code (JSON) — the record as it was at fill time |
| `generated_by`, `generated_on` | Who and when |
| `supersedes` / `superseded_by` | Link → `Form Artifact` — the chain |
| `void_reason` | Small Text — a superseded artifact says why, and is still kept |

**Artifacts are immutable.** `on_update` rejects any change to `file`,
`file_sha256`, `data_snapshot_json`, `form_template`, or `template_sha256` once
set. A correction is a **new** artifact whose `supersedes` points at the old
one. The old file stays attached. This is exactly what `render_i9_pdf` already
does when re-rendering ("the old File stays attached either way") — the design
gives that behavior a record instead of leaving it implicit in an attachment
list.

`data_snapshot_json` is what makes a 2029 audit answerable. `Tax Form` already
proves the principle: `form_pdf_renderer.py`'s docstring is explicit that
`form_data` is the record and today's arguments only fill holes, because *"a
renderer that preferred today's arguments would print a form that disagrees with
the record it claims to render."* Same reasoning, applied to every form.

**SSN handling:** `data_snapshot_json` stores masked values only. The I-9
pipeline already treats a full SSN as an event to be logged rather than a value
to be copied around (`tools/i9.py::_full_ssn` logs *which* identifier, never the
identifier). The snapshot follows that rule: `ssn_last_four` yes, `ssn_full`
never.

### 3.2 The W-4 → paycheck link, and the four gaps

The chain Tim wants traceable:

```
W-4 Form (election)
   └─► withholding.calculate_federal_withholding()   [pure]
         └─► Farm Payroll Slip.federal_withholding   [per period]
               └─► YTD aggregation
                     └─► form_generators.generate_w2_data()
                           └─► Tax Form.form_data_json
                                 └─► Form Artifact (filled W-2 Copy B)
```

Four links are weaker than they look:

**Gap 1 — the slip does not record which W-4 it used.** `Farm Payroll Slip` has
`federal_withholding` and `state_taxes_detail` but no link to a `W-4 Form`
document. Withholding is derived from "the Active W-4 *now*". When an employee
files a new W-4 mid-year, `submit_w4` supersedes the old one — and every prior
slip silently starts appearing to have been computed from a W-4 that did not
exist when it was run. *This is the traceability break that matters most.*

> **Fix (Phase 2):** add `w4_form` (Link), `w4_snapshot_json` (Code), and
> `withholding_computed_on` to `Farm Payroll Slip`. `build_payroll_inputs`
> already has the W-4 dict in hand at line 802 — it stamps it onto the slip
> instead of discarding it.

**Gap 2 — the no-W-4 default is silent.** `payroll_integration.DEFAULT_W4` treats
an employee with no W-4 as Single with no adjustments. That is the legally
correct treatment, and it is invisible on the resulting slip. A W-2 that looks
wrong to an employee in January should be explainable in one query.

> **Fix (Phase 2):** `w4_source` on the slip — `On File` / `Default (no W-4)`.
> The existing `list_employees_missing_w4` tool and the `w4-missing` compliance
> rule already surface the population; this records it per paycheck.

**Gap 3 — two W-4 loaders.** `tools/w4.py::_load_w4_data` (line 387) and
`tools/payroll.py::_load_w4_data` (line 1532) are independent implementations of
the same read, and `payroll_integration.DEFAULT_W4`'s docstring already
acknowledges the duplication by restating the defaults "because this module
reads no database". They agree today. Nothing enforces that.

> **Fix (Phase 2):** one loader in `tools/w4.py`, imported by both. Not a
> rewrite — a delete and an import.

**Gap 4 — Oregon reads the federal W-4.** `tools/state_tax.py::_resolve_filing_status`
(line 436) pulls filing status from `W-4 Form`. There is no OR-W-4 record and no
Oregon allowances field anywhere. Oregon's OR-W-4 is a separate election with
its own allowance count; an employee who files one gets it ignored.

> **Fix (Phase 5):** `State W-4 Form` doctype (§4). Until then, the behavior
> should be *stated* on the slip rather than assumed — `state_w4_source:
> "Derived from federal W-4"`.

### 3.3 Audit log

`I-9 Audit Log` works and is I-9-specific. Rather than rewrite it, add a
parallel `Form Audit Log` for W-4 and Tax Form, with the same shape and a
superset of actions:

`Created`, `Submitted`, `Signed`, `Rendered`, `Printed`, `Exported`,
`Signed Copy Filed`, `Superseded`, `Amended`, `Filed`, `Status Changed`,
`Destroyed`.

Fields mirror `I-9 Audit Log`: `timestamp`, `user`, `ip_address`, `action`,
`details` (JSON), plus a Dynamic Link `source_doctype`/`source_name` and an
optional `form_artifact` link.

**Do not migrate the I-9 log.** It has production rows, tools built on it
(`get_i9_audit_log`), and thirteen actions tuned to the federal I-9 lifecycle.
Two logs with the same shape is a smaller cost than a migration that risks
compliance history. Revisit only if a third consumer appears.

### 3.4 Reconstructing a year

The design succeeds if this query answers in one pass, from records rather than
recomputation:

> *"Employee E's 2027 W-2 box 2 says $4,180. Where did that come from?"*

1. `Tax Form` where `form_type = "W-2"`, `employee = E`, `fiscal_year = 2027`
   → `form_data_json` has the box values as computed.
2. `Form Artifact` where `source_name` = that Tax Form → the PDF actually
   produced, and `form_template` → the exact IRS edition it was filled on.
3. `Farm Payroll Slip` rows for E in 2027 → each with `federal_withholding`,
   `w4_form`, and `w4_snapshot_json`. Sum equals box 2, or the discrepancy is
   visible per period.
4. The slips whose `w4_form` differs mark the date E changed their election.
5. `Form Audit Log` for each of those W-4s → who entered it, when, from where.

No step in that chain requires re-running a calculation, and no step depends on
current state. That is the whole point.

---

## 4. Forms inventory

`have` = box arithmetic implemented in `form_generators.py`.
`Facsimile` = reportlab working copy exists. `Template` = official PDF fill.

### Onboarding

| Form | Agency | Data | Page today | Target |
|---|---|---|---|---|
| **I-9** | USCIS | ✅ `I-9 Form` | ✅ Template fill | Migrate to `Form Template` (Ph 1) |
| **W-4** | IRS | ✅ `W-4 Form` | ❌ none | Template fill (Ph 2) |
| **OR-W-4** | Oregon DOR | ❌ no doctype | ❌ none | `State W-4 Form` + template (Ph 5) |
| **WA** | — | n/a | n/a | No state income tax; no state W-4 exists |

### Year-end, employer

| Form | Agency | Data | Page today | Target |
|---|---|---|---|---|
| **W-2** | SSA | ✅ `generate_w2_data` | Facsimile | Template fill, **Copies B/C/2 only** (Ph 3) |
| **W-3** | SSA | ❌ | ❌ | Transmittal; aggregates W-2s (Ph 3) |
| **1099-NEC** | IRS | ✅ `generate_1099_nec_data` | Facsimile | Template fill, **Copies B/C only** (Ph 4) |
| **1099-MISC** | IRS | ❌ | ❌ | Rent (land leases), other income (Ph 4) |
| **1096** | IRS | ❌ | ❌ | Transmittal; aggregates 1099s (Ph 4) |
| **941** | IRS | ✅ `generate_941_data` | Facsimile | Quarterly; fillable IRS PDF exists (Ph 3, stretch) |
| **940** | IRS | ❌ | ❌ | Annual FUTA. Out of scope; noted for completeness |

1099-MISC matters more on a farm than it does in most shops: land leases, custom
harvest work, and equipment rent are Box 1 rent, not Box 1 nonemployee
compensation. The `Lease` and `Related Party` doctypes already hold what it
needs.

### Compliance

| Form | Data | Page | Target |
|---|---|---|---|
| **I-9 Supplement B** (reverification) | ✅ `I-9 Reverification` child table | ✅ Template fill, 3 rows + overflow | Carried into `Form Template` unchanged (Ph 1) |
| **I-9 Supplement A** (preparer/translator) | ✅ | ✅ Template fill | Carried unchanged (Ph 1) |

### Oregon

| Form | Agency | Data | Page today | Target |
|---|---|---|---|---|
| **OQ** (quarterly combined) | OED / DOR | ✅ `generate_or_oq_data` | Facsimile | Stays facsimile — filed via Frances Online (Ph 5) |
| **OR-WR** (annual reconciliation) | DOR | ✅ `generate_or_wr_data` | Facsimile | Stays facsimile (already shipped) |
| **Form 132** (employee detail) | OED | ❌ | ❌ | Per-employee wage/hour detail accompanying OQ (Ph 5) |
| **OR-W-4** | DOR | ❌ | ❌ | Template fill (Ph 5) |

### Washington

| Form | Agency | Data | Page today | Target |
|---|---|---|---|---|
| **ESD quarterly** | WA ESD | ✅ `generate_wa_esd_data` | Facsimile | Stays facsimile — filed via ESD portal (Ph 5) |
| **L&I quarterly** | WA L&I | ❌ | ❌ | Hours-based, by risk class, not wage-based (Ph 5) |

L&I is the one Washington form with no analogue elsewhere in the app: premiums
are assessed **per worker-hour per risk classification**, not per dollar. The
hours are already in `Farm Shift` and aggregated by
`payroll_integration.aggregate_shifts_for_period`; what is missing is a risk
class per employee or per task. That is a schema question to settle in Phase 5,
not a rendering question.

---

## 5. Implementation phases

Each phase ships independently and leaves the app working. No phase requires the
next one.

### Phase 1 — Generalize the I-9 pattern (refactor, not rewrite)

**This phase must not change a single byte of I-9 output.** The existing I-9
fixtures in `tests_standalone/test_i9_pdf.py` are the acceptance criterion:
byte-identical PDFs before and after.

- Add `Form Type`, `Form Template`, `Form Artifact` doctypes.
- Add `form_fill.py` — generic AcroForm filler, pure function.
- Seed `Form Type` rows for the full §4 inventory (rows for forms not yet
  implemented are harmless and make the inventory queryable).
- Migrate `templates/i9_form.pdf` into a `Form Template` row. Resolve the
  edition-string discrepancy noted in §1.1 while doing it; the resolved value
  becomes the single source and `templates/README.md` links to the row rather
  than restating it.
- Move the I-9 field map into `field_map_json`; keep the genuinely I-9-specific
  logic (shared-title split, Additional Information overflow, COMB font
  handling) in `i9_pdf.py` behind `fill_module`.
- Backfill `Form Artifact` rows for existing I-9 `generated_pdf` /
  `signed_pdf` attachments, pointing at the migrated template row.
- Replace the per-module checksum constant with a suite test walking every
  `Form Template`.
- Tools: `list_form_templates`, `get_form_template`, `import_form_template`,
  `diff_form_template_fields`, `validate_form_template`,
  `activate_form_template`, `list_form_artifacts`, `get_form_artifact`.

**Risk:** the I-9 field map is 133 fields with real subtlety. Mitigation is that
Phase 1 changes *where the map lives*, not *what it says*, and the fixture tests
prove it.

### Phase 2 — W-4 template fill + close the payroll traceability gaps

- Stock the IRS Form W-4 PDF as a `Form Template`; write its field map.
- `render_w4_pdf` + `attach_signed_w4`, mirroring the I-9 tools exactly.
- Add `Form Audit Log`; wire `submit_w4` and the render/sign tools to it.
- Close Gaps 1–3 from §3.2: `w4_form`, `w4_snapshot_json`,
  `withholding_computed_on`, `w4_source` on `Farm Payroll Slip`; single W-4
  loader.
- Add W-4 retention fields (§6).
- Backfill: existing slips get `w4_source = "Unknown (pre-v0.49)"` rather than a
  guess. An honest null beats a fabricated link.

### Phase 3 — W-2 from payroll data + template fill

- Stock W-2 Copy B, C, and 2 templates. Enforce `copy_a_prohibited`.
- Extend `render_tax_form_pdf` to prefer a `Form Template` when one exists for
  the form type and fall back to the reportlab facsimile when none does. **The
  facsimile is not removed** — it stays the review/keying copy, and for OQ and
  ESD it stays the only copy.
- `Form Artifact` rows for tax forms; wire `Tax Form` into `Form Audit Log`.
- Add W-3 generation aggregating the year's W-2s.
- Reconciliation check: sum of `Farm Payroll Slip.federal_withholding` for the
  year must equal W-2 box 2, per employee. A mismatch is a hard error at
  generation with the offending periods listed — the same posture
  `generate_or_wr_data` already takes when OQ figures disagree with annual
  totals.

### Phase 4 — 1099 generation

- 1099-NEC Copy B/C templates.
- 1099-MISC generator (rent from `Lease`, other income) + templates.
- 1096 transmittal.
- Reconciliation against the same payment data
  `tools/taxforms.py::_load_contractor_payments` already reads.
- $600 threshold reporting: `generate_1099_nec_data` already computes figures
  below threshold and says so rather than suppressing them. Keep that.

### Phase 5 — State forms

- `State W-4 Form` doctype (state, filing status, allowances, additional
  amount, exempt flag) + OR-W-4 template.
- Rewire `state_withholding.calculate_oregon_withholding` to prefer a State W-4
  and fall back to the federal one, recording which on the slip (Gap 4).
- Oregon Form 132 employee-detail generator.
- WA L&I quarterly: settle risk-class schema, then generate.

---

## 6. Retention

Retention is a **policy per form type**, evaluated against a per-record anchor
date. `Form Type.retention_rule` names the rule; the rule is implemented in
code, not configured in a field, because getting these wrong has legal
consequences and a typo in a Desk field should not be able to cause one.

| Class | Rule | Anchor | Basis |
|---|---|---|---|
| **Generated PDFs** (`Form Artifact`) | Indefinite | — | Compliance records. Storage is cheap; a missing artifact is not recoverable |
| **Template PDFs** (`Form Template`) | Forever, never deleted | — | You need the template to verify any historical form filled from it |
| **I-9** | 3 years after hire **or** 1 year after termination, **whichever is later** | `hire_date` / `relieving_date` | 8 CFR 274a.2(b)(2)(i)(A) — **already implemented**: `retention_until`, `destruction_eligible_date`, `get_i9_retention_report`, `destroy_i9` |
| **W-4** | 4 years after the tax year it applies to | `tax_year` + 4 | 26 CFR 31.6001-1 |
| **W-2 / W-3** | 4 years (statutory) / **7 years recommended** | `fiscal_year` | Statute is 4; the recommendation reflects amended-return and audit reality |
| **1099 / 1096** | 4 years | `fiscal_year` | 26 CFR 31.6001-1 |
| **State quarterly** (OQ, 132, ESD, L&I) | 4 years | period end | OR and WA both align to 4 |

**Nothing auto-deletes. Ever.** The system reports eligibility; a human runs the
destruction tool; the destruction is logged. This is exactly how `destroy_i9`
and the `Destroyed` audit action already work, and the pattern generalizes
unchanged:

- `get_form_retention_report(form_type=None, company=None)` — one report across
  all form types, sections for approaching (≤ 90 days) and eligible.
- `destroy_form_record(source_doctype, source_name, reason)` — clears the
  payload, keeps the record shell and the audit trail. **A destroyed record
  still proves it existed and was destroyed on purpose.**
- `Form Artifact` rows are never destroyed even when their source record is;
  the artifact's `file` may be cleared, the row and its checksum stay.

The recommended-vs-statutory split on W-2s is deliberate and should be a
settings toggle defaulting to 7 years, with the statutory 4 available for
anyone who wants the shorter horizon. The report says which basis it used.

---

## 7. What this design deliberately does not do

- **Does not e-file anything.** No SSA BSO, no IRS FIRE, no Frances Online, no
  ESD portal integration. The app produces forms and records; a human files
  them. E-filing is a different design with different failure modes.
- **Does not replace the reportlab facsimiles.** They stay for review, for
  keying into portals, and as the only page for forms with no printable copy.
- **Does not print Copy A of anything.** §2.5.
- **Does not migrate the I-9 audit log.** §3.3.
- **Does not add a signature ceremony.** E-sign stays what it is today — an
  `Attach` field for a signature image plus `signed_at` / `signed_ip`. Adequate
  for a farm; not a DocuSign replacement.
- **Does not auto-download new editions.** A government form changing is a thing
  a person should look at. The checksum test is the alarm, not a fetcher.

---

## 8. Open questions for review

1. **Field map as JSON blob vs child table.** §2.3 argues for the blob. Worth a
   second opinion — the blob is developer-facing, and if operators ever need to
   touch it the calculus changes.
2. **W-2 retention default: 4 or 7 years?** §6 recommends 7 with a toggle.
3. **WA L&I risk class — per employee or per task?** A worker who prunes in
   March and drives forklift in September may fall in two classes. Per task is
   more correct and more work; per employee is simpler and occasionally wrong.
   This decides the Phase 5 schema.
4. **Do we stock W-2 Copy 2 (state/local)?** Oregon requires an employee copy;
   Washington has no income tax. Probably yes, low cost.
5. **Should `Form Artifact` backfill (Phase 1) attempt to identify the template
   edition for old I-9 PDFs?** There has only ever been one template, so the
   answer is trivially yes — but the *principle* is that a backfill should not
   assert what it cannot verify. Flagging it so the reviewer agrees explicitly.
6. **941 in Phase 3 or its own phase?** The data exists and the IRS PDF is
   fillable. It is quarterly, not year-end, so it does not fit Phase 3's shape
   cleanly.

---

## 9. Summary of new schema

| Doctype | Purpose | Phase |
|---|---|---|
| `Form Type` | Reference table — one row per government form | 1 |
| `Form Template` | Versioned government PDF + field map + checksum | 1 |
| `Form Artifact` | Immutable generated PDF + provenance | 1 |
| `Form Audit Log` | State-change trail for W-4 and Tax Form | 2 |
| `State W-4 Form` | OR-W-4 and future state equivalents | 5 |

| Existing doctype | Change | Phase |
|---|---|---|
| `Farm Payroll Slip` | `+ w4_form`, `w4_snapshot_json`, `w4_source`, `withholding_computed_on`, `state_w4_source` | 2 |
| `W-4 Form` | `+ generated_pdf`, `signed_pdf`, `retention_until`, `destruction_eligible_date` | 2 |
| `Tax Form` | `+ form_template`, `retention_until`; `form_type` Select → Link | 3 |
| `I-9 Form` | no change | — |

| New module | Purpose | Phase |
|---|---|---|
| `form_fill.py` | Generic AcroForm filler, pure function | 1 |
| `form_retention.py` | Retention rules per form type, pure function | 2 |
