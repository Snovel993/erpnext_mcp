# Employee Badge → QR → Bucket Validation: Pipeline Audit

**Date:** 2026-08-07
**Scope:** `erpnext_mcp` v0.48.1, `fafo_ios` (Farm Ops / FarmOpsKit), `farm_app` (Flask), `farm_app_scanClock` (FarmCore)
**Status:** review only — no code changed, nothing committed.

---

## 0. Executive summary

The badge pipeline is **about 60% built, and the missing 40% is concentrated in
two places**: nobody *issues* a badge on the ERPNext side, and nobody *sends* a
bucket capture off the phone.

What works today, end to end, is the **register**: a badge ID can be mapped to
an Employee (`link_badge_to_employee`), that mapping resolves a
`worker_badge` into an `employee` on every synced capture, an unresolved badge
mapped after the fact backfills what was already picked, and only Accepted,
attributed captures reach payroll. That half is well-built and tested.

The two hard stops:

1. **There is no badge issuer in `erpnext_mcp`.** `link_badge_to_employee`
   *records* a badge ID an operator typed or scanned; it never *mints* one and
   there is no printable card. Of 386 registered tools, exactly one is
   badge-related, and the two QR generators that exist
   (`generate_mobile_login_qr`, `generate_asset_qr`) are for operator logins and
   asset tags. So today the only real badge cards in the business are printed by
   `farm_app` (Flask), encoding a `farm_app` `QRToken` UUID that ERPNext has
   never heard of.

2. **`sync_bucket_entries` has no caller.** `MobileAPI.syncBucketEntries` is
   declared in `FarmOpsKit/Sources/FarmOpsKit/Networking/MobileAPI.swift:63` and
   has **zero call sites** in the app. `BucketCaptureSession.commitEntry` appends
   the `BucketEntry` to an in-memory array and stops. Every bucket scanned in an
   orchard today dies when the view is dismissed. The same is true of
   `startShift` / `addWorkerToShift` / `endShift`.

Everything between those two points — the scanner, the badge capture in the
capture loop, the transport wrapper, the doctype, the payroll reshape — exists
and looks correct.

---

## 1. Badge creation

### 1.1 The register: `Bucket Log Badge Map` (ERPNext)

**Doctype:** `erpnext_mcp/erpnext_mcp/doctype/bucket_log_badge_map/bucket_log_badge_map.json`
(controller `…/bucket_log_badge_map.py` — a bare `Document` subclass, no hooks)

| Field | Type | Notes |
|---|---|---|
| `badge_id` | Data, **unique**, `autoname: field:badge_id` | the docname *is* the badge ID |
| `company` | Link → Company, reqd | |
| `employee` | Link → Employee, reqd | |
| `active` | Check, default 1 | unticking retires without deleting |
| `notes` | Small Text | |

Permissions: System Manager (full), HR Manager and Farm Manager (create/read/write, no delete).
`track_changes: 1`.

The design note in the doctype's own `description` is worth keeping: this is a
separate table rather than a field on `Employee` because (a) `employee.py` edits
core Employee only through a closed `WRITABLE` list, and (b) a badge is
reassignable independent of the person — a lost card, a re-printed one.

### 1.2 The one tool: `link_badge_to_employee`

- **Tool:** `erpnext_mcp/tools/bucket_log.py:485`
- **Registry:** `erpnext_mcp/registry.py:12637` — MUTATING, default OFF
- **Transport wrapper:** `erpnext_mcp/api/mobile.py:1585`
  (`@guard.endpoint("link_badge_to_employee", mutating=True, limit=guard.WRITE_LIMIT)`)
- **Route:** `erpnext_mcp/farmops_api/routes.py:168`
- **iOS caller:** `FarmOpsKit/Sources/FarmOpsKit/Networking/OnboardingAPI.swift:340` (`linkBadge`)
- **Test:** `tests_standalone/test_ios_contract.py:967` (`test_22_link_badge_to_employee`)

Behaviour: upserts the map row (repointing an existing badge to a new person is
explicitly allowed — a reissued card), then **backfills** `employee` onto every
already-synced `Bucket Log Entry` *and* `Bucket Log Session` carrying that badge
with no employee yet (`bucket_log.py:524-550`). That backfill is the right call
and is the reason a badge scanned before the map exists still gets paid.

One asymmetry worth noting: the MCP tool accepts `active=false` to retire a
badge, but the transport wrapper at `api/mobile.py:1585` **does not expose
`active`** — its docstring says so explicitly ("The wrapper always maps a badge
live"). Retiring a lost badge is a Desk/MCP action only; a foreman in the field
cannot kill a card from the phone.

### 1.3 Reading a badge back

There is **no `list_badges` / `get_badge` / `resolve_badge` tool**. The only read
path is indirect: `employee.py:1281 _active_badge(employee)` looks up the active
map row and stuffs it into `get_employee`'s payload as `detail["badge_id"]`
(`employee.py:1228`), which the onboarding wizard uses to mark step 5 done
(`test_ios_contract.py:1275`, `IdentityStepView.swift:283` → `emp.needsBadge`).

So: you can ask "does this person have a badge?" but not "who holds badge
QR-0042?" and not "list every badge in this company."

### 1.4 The QR *generator* that does not exist

`erpnext_mcp/render/qr.py` is a solid, dependency-tolerant QR renderer
(`segno` → `qrcode` fallback, hand-rolled 8-bit greyscale PNG, enforced 4-module
quiet zone, `available()` gating in `registry.py:159 _qr_available`). It has
exactly two consumers:

| Caller | File | Encodes |
|---|---|---|
| `generate_mobile_login_qr` | `tools/mobile.py:1113` | JSON `{url, user, api_key, api_secret, expires_at}` — an **operator login credential**, 1–168h expiry |
| `generate_asset_qr` / `generate_asset_qr_sheet` | `tools/asset_tags.py:649,695` | the asset's `qr_url` (or `/scan/<name>`) |

**Neither is an employee badge.** There is no `generate_badge_qr`,
no `generate_badge_sheet`, no badge print format. (`i9_print_format.py` and
`printing.py::create_check_print_format` are the only print formats in the repo;
neither touches badges.)

### 1.5 What `farm_app` (Flask) does instead — the parallel system

`farm_app` has a complete, older badge stack that ERPNext knows nothing about:

- **Model:** `farm_app/app/models.py:9934` `QRToken` — `token` (uuid4 string),
  `system_id`, `entity_type` (`'employee'`, `'pallet'`, `'field'`, …),
  `entity_id`, `is_active`, `expires_at`, `token_metadata`; plus
  `QRScanAudit` (models.py:9998).
- **Issue:** `farm_app/app/utils/qr_utils.py:20 generate_token_qr` and
  `:82 get_or_create_employee_token` — QR encodes **only the opaque UUID**, no
  entity data.
- **Print:** `farm_app/app/blueprints/hr.py:634 generate_employee_id` — composes a
  400×600 PNG ID card (name, employee ID, photo, 200×200 QR) and serves it as a
  download. Also `app/blueprints/wallet.py:91 print_badge` +
  `app/templates/printable_badge.html`, and Apple Wallet passes via
  `app/utils/wallet_pass.py` / `app/blueprints/admin/wallet_badges.py`
  (`models.py:10533` — "QR token for badge scanning — same payload as the
  physical card").
- **Lifecycle:** `qr_utils.py:189/215` revoke/reactivate on employment status
  change (`hr.py:691`); expiry configurable at
  `app/blueprints/admin/settings.py:146` (`employee_token_expiry_days`,
  `None` = never).
- **Lookup:** `app/blueprints/qr_api.py` — `/scan/<token>`, `/lookup`, `/cache`,
  `/employee_info/<user_id>`, `/audit`, `/validate_offline_scan`.

**This is the crux of the whole audit.** The physical badges that exist are
`farm_app` UUID tokens. ERPNext's map keys on an opaque string matched *exactly*
(`bucket_bridge.py:159-171` — deliberately no case-folding). So a `farm_app`
badge UUID *would* work as an ERPNext `badge_id` — but only because ERPNext
treats it as an arbitrary string. Nothing coordinates the two:
`farm_app` revoking a token on termination does **not** deactivate the ERPNext
map row, and ERPNext deactivating a row does not revoke the `farm_app` token.

---

## 2. What the QR code encodes

Three different payload formats are in play. This is the single biggest source
of confusion in the pipeline.

| # | Payload | Produced by | Consumed by |
|---|---|---|---|
| A | Bare uuid4 string, e.g. `3f2c…-…` | `farm_app` `qr_utils.generate_token_qr` | `farm_app` `/qr/lookup`; FarmCore `PieceTallyViewModel` / `SupervisorScanClockViewModel` (via ref-data cache) |
| B | JSON `{"type":"farm_app_nostr_link","version":1,…}` | `farm_app` onboarding | `farm_app_scanClock` `QRParser.parse` (`Services/QRScannerService.swift:50`) — **rejects anything whose `type` ≠ `farm_app_nostr_link`** |
| C | JSON `{url,user,api_key,api_secret,expires_at}` | `erpnext_mcp` `generate_mobile_login_qr` | `FarmOpsKit` `LoginQRParser` |

**The Farm Ops badge path uses none of these — it uses the raw string,
whatever it is.** `QRScannerModel.handleScan`
(`FarmOpsKit/Sources/FarmOpsKit/Capture/QRScannerView.swift:111-115`) calls
`onScan?(value)` with the undecoded `stringValue`, and both consumers take it
verbatim:

- Onboarding: `OnboardingWizardViewModel.handleBadgeScan(_ value:)` → `badgeID = value` (`:693`)
- Capture: `BucketCaptureSession` `badgeScanner.onScan = { badge in commitEntry(badgeID: badge, …) }` (`:184`, `:248`)

**This is actually a virtue, not a bug** — the *same* raw string goes into the
map at onboarding and into the capture in the field, so the two agree by
construction. It also means a `farm_app`-printed card (payload A) works
unmodified. But it has three consequences:

- **No validation.** Scan a soda can's barcode, a URL, a Wi-Fi QR — it becomes a
  badge ID. `validate_bucket_entry` (`bucket_bridge.py:105`) only checks that
  *something* identifies the picker, not that it looks like a badge.
- **No shape contract.** `test_ios_contract.py` uses `"QR-0042"` and
  `"BADGE-0042"`; `farm_app` issues 36-char UUIDs. Nothing anywhere asserts a
  format.
- **A payload-C login QR scanned at the badge step** becomes a badge ID
  containing an API secret. Low likelihood, but the scanner cannot tell the
  difference, and `QR_Scan_Routing_Safety_Analysis.md` in `farm_app_scanClock`
  exists precisely because this class of problem was recognised on the other app.

**Note the near-miss:** `QRScannerView.swift:81-91` restricts autofocus to
`.near` with the comment "Badges get held close to the lens." The scanner was
built for badges. Only the payload contract was left open.

---

## 3. iOS scanning

Three iOS codebases exist. Only one talks to ERPNext.

### 3.1 `fafo_ios` — Farm Ops + FarmOpsKit (**the ERPNext client**)

- `FarmOpsKit/Sources/FarmOpsKit/Capture/QRScannerView.swift` —
  `QRScannerModel` (AVFoundation, `@MainActor`, one-shot latch with `rearm()`,
  near-focus tuning) + `QRScannerView` (reticle + hint).
- **Onboarding badge step (WIRED, WORKING):**
  `Features/Onboarding/Steps/BadgeStepView.swift` (text field + scan button,
  skippable) → `OnboardingWizardView.swift:155` `onScanTapped: { vm.isScanning = true }`
  → `:36` `.fullScreenCover` → `:227` `QRScannerView(hint: "Scan employee badge")`
  → `vm.handleBadgeScan(value)` → `OnboardingWizardViewModel.swift:577-582`
  → `OnboardingAPI.linkBadge(...)` → `link_badge_to_employee`. **This whole path works.**
- **Bucket capture badge scan (WIRED locally, NEVER SENT):**
  `Features/Capture/BucketCaptureSession.swift:50` `let badgeScanner = QRScannerModel()`;
  phase machine → ML voting window → `:184` badge scan → `:208 commitEntry(badgeID:verdict:)`
  → builds a `BucketEntry` (with photo via `EvidenceStore`, GPS, coverage)
  → **`entries.append(entry)` (`:233`) and nothing else.**
  `BucketScanScreen.swift:113` offers "Skip badge" → `commitEntry(badgeID: nil, …)`,
  and `:202` times out to `badgeID: nil` too.
- **Crew clock:** `Features/Clock/CrewClockSession.swift` — scans badges
  (`:72 handleBadgeScan`), keeps a `roster` keyed on `badgeID`, emits local
  `ClockEvent(employeeBadgeID:)` values (`:96`, `:133`, `:150`). **No API call.**

### 3.2 `farm_app_scanClock` — FarmCore (**the Flask/NOSTR client**)

Badge scanning here is *more* complete than in Farm Ops, but points at
`farm_app`, not ERPNext:

- `Verticals/DocumentingWork/ViewModels/PieceTallyViewModel.swift` — continuous
  badge scanning, 1 bucket per scan, dispatches on `qrToken.entity_type`
  (`:154-183`), emits NOSTR kind-4003, falls back to a ref-data refresh on an
  unknown badge (`:337`) and audits the miss (`:346 action: "unknown_badge"`).
- `PieceRateViewModel.swift` — scan a worker's badge → log units for them;
  rejects non-employee tokens with `"Scanned \(entity_type) — not an employee badge"` (`:63`).
- `SupervisorScanClockViewModel.swift` — same pattern for clock-in.
- `Sources/Services/QRScannerService.swift:50` `QRParser.parse` — hard-gated to
  payload B (`farm_app_nostr_link`); **not** used for badges.

**This is the reference implementation for what Farm Ops' badge handling should
look like** — entity-type checking, local resolution against cached ref-data,
retry-after-refresh, audit of unknown badges. Farm Ops has none of it.

### 3.3 `farm_app_scanClock/ios/FarmApp/Sources` (outer tree)

A stale duplicate. `git ls-files` shows only the inner
`ios/FarmApp/FarmApp/FarmApp/Sources` tree is tracked (it has `FarmAppApp.swift`
and `Config.swift`). Mentioned only so nobody edits the wrong copy.

---

## 4. Bucket validation flow

### 4.1 The intended flow

```
badge QR ──scan──> BucketCaptureSession.commitEntry(badgeID:)
                        │
                        ▼
                   BucketEntry {id, session_id, badge_id, accepted, coverage_percent, gps, device_id}
                        │  ✗✗ MISSING LINK — nothing calls syncBucketEntries ✗✗
                        ▼
   POST /api/method/…farmops_api…/mobile/sync_bucket_entries
                        │
                        ▼
   api/mobile.py:1626 sync_bucket_entries(user, entries, company)
      └─ _bucket_entries() (:257) — renames id→entry_uuid, session_id→session_uuid,
         badge_id→worker_badge, accepted:Bool→verdict:"Accepted"/"Rejected";
         STAMPS company from the call, never from the entry;
         REFUSES an `employee` key on an entry (badge is the only attribution path)
                        │
                        ▼
   tools/bucket_log.py:192 sync_bucket_entries
      ├─ dedup by entry_uuid
      ├─ bucket_bridge.validate_bucket_entry (:105)
      ├─ _badge_map(company) (:111) → {badge_id: employee} for active rows
      ├─ bucket_bridge.resolve_badge_to_employee (:159) — EXACT string match
      ├─ insert Bucket Log Entry (status=Pending)
      └─ _sync_session(session_uuid) (:142) — recomputes totals from ALL entries
                        │
                        ▼
   link_entries_to_shift (tools:581 / bridge:250) — Pending → Linked, never un-Pays
                        ▼
   entries_to_payroll_shape (bridge:221) — Accepted only, attributed only
                        ▼
   payroll_integration._piece_units_for — one row = one bucket
```

### 4.2 What is genuinely solid here

- **Attribution is single-sourced.** `api/mobile.py:268-273` refuses an
  `employee` key on an entry: "a phone that could name the picker directly would
  be able to move somebody else's piece-rate onto its own badge." Correct
  reasoning; the badge map is the only writable attribution path.
- **Company is stamped from the authenticated call, not read off the entry**
  (`:260-266`). Closes a cross-entity write hole.
- **Rejected buckets never reach payroll** (`bridge:238`), and unattributed ones
  are dropped rather than paid to nobody (`:241`).
- **Session totals are recomputed from entries**, not trusted off a device
  counter (`bucket_log.py:142-186`), and a late `link_badge_to_employee`
  propagates to the session too (`:175-179`).
- **`Paid` is terminal** (`bridge:263`) — re-linking a paid bucket is refused.
- Both payload spellings accepted (handset's `id`/`session_id`/`badge_id`/`accepted`
  and the doctype's own), so a Desk import and a phone both work.

### 4.3 What breaks the flow

- **Nothing calls `syncBucketEntries`.** Confirmed by exhausting every
  `MobileAPI.` reference in `fafo_ios`: the only endpoints with call sites are
  the task calls (`FarmOpsAPI.swift`), the onboarding calls
  (`OnboardingAPI.swift`), and the chunk uploader. `syncBucketEntries`,
  `startShift`, `addWorkerToShift`, `endShift` are declared and unused.
- **No on-device persistence for entries.** `BucketCaptureSession.entries` is a
  plain in-memory array. There is no queue, no retry, no offline durability —
  and an orchard is exactly where you have no signal. (`EvidenceStore` persists
  the *photo*; the entry that references it does not survive.)
- **No badge→employee feedback on the phone.** The picker's name never appears;
  a supervisor cannot tell a mis-scan from a good scan until the data reaches
  the Desk. FarmCore solves this with cached ref-data; Farm Ops has no
  equivalent because there is no read endpoint (see §1.3).
- **`add_worker_to_shift` takes `employee`, the crew clock has `badgeID`.**
  `api/mobile.py:1708` requires an Employee docname
  (`_employee_argument`); `CrewClockSession` only ever holds badge IDs. Even once
  someone wires the call, it cannot be satisfied without a badge→employee
  resolution endpoint.
- **Silent unattributed captures.** "Skip badge" (`BucketScanScreen.swift:113`)
  and the scan timeout (`BucketCaptureSession.swift:202`) both commit
  `badgeID: nil`. Those fail `validate_bucket_entry` at sync and land in
  `invalid[]` — reported, but only to whoever reads the sync result, which today
  is nobody.

---

## 5. Badge printing

| Where | What exists |
|---|---|
| `erpnext_mcp` | **Nothing.** No badge print format, no badge PDF, no badge QR tool. Print formats present: `i9_print_format.py`, `printing.py::create_check_print_format`. PDF generation exists for I-9 (`generate_i9_pdf`) and W-4 (`generate_w4_pdf`) — both good templates to copy. |
| `farm_app` | **Working.** `hr.py:634 generate_employee_id` → composed PNG ID card (Pillow: name, employee ID, photo, QR). `wallet.py:91 print_badge` + `templates/printable_badge.html`. Apple Wallet passes: `utils/wallet_pass.py`, `admin/wallet_badges.py`, `templates/wallet_badge.html`, `models.py:10533`. |
| `erpnext_mcp` (adjacent) | `generate_asset_qr_sheet` (`asset_tags.py:673`) already does bulk QR → Avery 5160 label sheets, ≤100 per call. **This is the shape a badge sheet should take.** |

So badge printing exists — in the wrong system, keyed to the wrong identifier,
with no link back to the ERPNext register.

---

## 6. Missing pieces, ranked

### P0 — pipeline is dead without these

1. **Call `sync_bucket_entries` from Farm Ops.** Plus a durable on-device queue
   (file-backed, like `EvidenceStore`), batch slicing against `BUCKET_BATCH_CAP`,
   retry, and surfacing the `invalid[]` / `duplicate_count` result to the user.
2. **A badge issuer in `erpnext_mcp`.** Decide the identifier (§7) and build
   `generate_badge_qr` (single) + `generate_badge_sheet` (bulk, Avery, modelled
   on `generate_asset_qr_sheet`). `render/qr.py` already does all the hard work.

### P1 — needed for a foreman to trust it

3. **`resolve_badge` read endpoint** — badge ID → `{employee, employee_name,
   active, company}`. Needed by: the crew clock, badge-scan confirmation in the
   capture loop, and any "who is this?" affordance. Must be a `@guard.endpoint`
   read, scope-checked, and should be rate-limited — it is a PII lookup keyed on
   a string anyone holding a card can produce.
4. **`list_badges` / `get_badge` MCP tools** — an operator currently cannot
   answer "who holds QR-0042?" or "which badges are active?" without the Desk.
5. **A ref-data badge cache on the phone**, so a scan resolves offline. Port the
   FarmCore pattern (`PieceTallyViewModel.swift:337 refreshAndHandleBadge`,
   audit unknown badges).
6. **Badge printing on the ERPNext side** — a print format or PDF for the card
   itself (name, photo, company, QR), following `generate_i9_pdf` /
   `generate_w4_pdf`.

### P2 — correctness and hygiene

7. **A badge ID format contract**, validated on both sides. Right now any string
   is a badge. At minimum a prefix + length check in
   `bucket_bridge.validate_bucket_entry` and a matching guard in the scanner, so
   a scanned login QR or a random barcode is rejected at the camera.
8. **Expose `active` on the `link_badge_to_employee` wrapper** (or add a
   `retire_badge` endpoint) so a lost card can be killed from the field.
9. **Reconcile with `farm_app`'s `QRToken`** — at minimum, terminating an
   employee in `farm_app` (which calls `revoke_employee_tokens`) should
   deactivate the ERPNext map row. Today the two lifecycles are independent.
10. **Wire the crew clock** (`start_shift` / `add_worker_to_shift` / `end_shift`),
    which depends on #3.
11. **Handle "Skip badge" and scan-timeout** deliberately — either block the
    capture, or queue it as explicitly unattributed with a visible count the
    supervisor must resolve. Silently producing entries that will fail
    validation at sync is the worst of the three options.

---

## 7. The one decision that has to be made first

**Which string is a badge ID?** Everything else follows from this, and it cannot
be deferred — #2, #7, #9 and the physical cards all depend on it.

**Option A — adopt `farm_app`'s `QRToken` UUID.**
Works with every badge already printed and in workers' pockets. Reuses
`farm_app`'s revocation, expiry, wallet passes and scan audit. Costs: ERPNext
badge IDs become 36-char opaques nobody can read off a card or type in as a
fallback; ERPNext depends on a Flask app's identifier space; and
`link_badge_to_employee` still has to be called per worker to build the ERPNext
map, so there are two registers to keep honest.

**Option B — mint badge IDs in `erpnext_mcp`.**
One register, human-readable (`FAFO-0042`), typeable when a card is scuffed,
printable as an Avery sheet from `generate_badge_sheet`, and the lifecycle
(`active`) lives with the payroll data. Costs: reprint every card; two badge
systems coexist until `farm_app`'s is retired.

**Recommendation: Option B**, with a migration that maps existing `farm_app`
employee tokens into `Bucket Log Badge Map` rows as a compatibility layer —
`badge_id` matching is an exact string lookup with no format assumption
(`bucket_bridge.py:159`), so *both* an old UUID and a new `FAFO-0042` can resolve
to the same person during the transition. That is a genuine property of the
current design, not a workaround, and it makes the cutover incremental.

The reason to prefer B despite the reprint: piece-rate attribution is a payroll
record. The identifier that decides who gets paid for a bucket should live in
the system that pays, with a retirement flag an HR manager can flip, and should
be readable aloud over a radio when a scanner fails at 6am.

---

## 8. Recommended action plan

**Phase 1 — decide and issue (unblocks everything).**
Settle §7. Then in `erpnext_mcp`: a badge-ID minting helper, `generate_badge_qr`,
`generate_badge_sheet` (copy `asset_tags.py:673`), and `list_badges` / `get_badge`
reads. All gated on `_qr_available` (`registry.py:159`) exactly as the existing
QR tools are.

**Phase 2 — close the sync gap (makes buckets real).**
In `fafo_ios`: a durable `BucketEntryQueue`, wire `MobileAPI.syncBucketEntries`,
batch/retry/report. This is the highest-value single change in the audit — the
entire server half is already built and tested behind an endpoint nobody calls.

**Phase 3 — resolution and feedback.**
`resolve_badge` endpoint + on-device ref-data cache; show the picker's name on
scan; audit unknown badges. Then wire the crew clock, which #3 unblocks.

**Phase 4 — printing and lifecycle.**
Badge card PDF/print format in ERPNext; `active`/retire exposed on the transport;
`farm_app` termination → ERPNext badge deactivation.

**Phase 5 — hardening.**
Badge format contract validated at the camera and at
`validate_bucket_entry`; explicit handling of skip/timeout captures.

---

## 9. File index

**erpnext_mcp**
- `erpnext_mcp/bucket_bridge.py` — pure functions: `validate_bucket_entry:105`, `resolve_badge_to_employee:159`, `aggregate_session:177`, `entries_to_payroll_shape:221`, `link_entries_to_shift:250`
- `erpnext_mcp/tools/bucket_log.py` — `_badge_map:111`, `_sync_session:142`, `sync_bucket_entries:192`, `link_badge_to_employee:485`, `link_entries_to_shift:581`, `get_piecework_summary:671`, `reconcile_bucket_payroll:738`
- `erpnext_mcp/api/mobile.py` — `_bucket_entries:257`, `link_badge_to_employee:1585`, `sync_bucket_entries:1626`, `add_worker_to_shift:1708`
- `erpnext_mcp/farmops_api/routes.py:168-172` — badge/bucket/shift routes
- `erpnext_mcp/render/qr.py` — `available:59`, `qr_matrix:85`, `png_bytes:132`, `render:180`
- `erpnext_mcp/tools/mobile.py:1113` — `generate_mobile_login_qr`
- `erpnext_mcp/tools/asset_tags.py:638,673` — `generate_asset_qr`, `generate_asset_qr_sheet`
- `erpnext_mcp/tools/employee.py:1281` — `_active_badge`
- `erpnext_mcp/registry.py:12637` — the single badge tool; `:159` `_qr_available`
- `erpnext_mcp/erpnext_mcp/doctype/bucket_log_badge_map/` — the register
- `tests_standalone/test_ios_contract.py:967,981,1250` — badge contract tests

**fafo_ios (Farm Ops — the ERPNext client)**
- `FarmOpsKit/Sources/FarmOpsKit/Capture/QRScannerView.swift:111` — `handleScan`, raw passthrough
- `FarmOpsKit/Sources/FarmOpsKit/Capture/BucketEntry.swift` — the wire model
- `FarmOpsKit/Sources/FarmOpsKit/Networking/MobileAPI.swift:63,68,74,97` — endpoint constants (only `:97` has a caller)
- `FarmOpsKit/Sources/FarmOpsKit/Networking/OnboardingAPI.swift:340` — `linkBadge`
- `fafo_ios/FarmOps/Features/Capture/BucketCaptureSession.swift:50,184,208` — badge scanner + `commitEntry`
- `fafo_ios/FarmOps/Features/Onboarding/Steps/BadgeStepView.swift`, `OnboardingWizardView.swift:155,227`, `OnboardingWizardViewModel.swift:577,693`
- `fafo_ios/FarmOps/Features/Clock/CrewClockSession.swift:72,82,124` — badge-keyed, unwired

**farm_app (Flask)**
- `app/utils/qr_utils.py:20,82,118,189,215` — token issue/lookup/revoke
- `app/blueprints/hr.py:634` — `generate_employee_id` (the ID card)
- `app/blueprints/wallet.py:91` — `print_badge`; `app/utils/wallet_pass.py`
- `app/blueprints/qr_api.py` — scan/lookup/cache/audit/offline-validate
- `app/models.py:9934,9998,10533` — `QRToken`, `QRScanAudit`, wallet pass

**farm_app_scanClock (FarmCore — reference implementation)**
- `…/ViewModels/PieceTallyViewModel.swift:154,188,337` — entity-type dispatch, refresh-and-retry, unknown-badge audit
- `…/ViewModels/PieceRateViewModel.swift:50-92`
- `…/ViewModels/SupervisorScanClockViewModel.swift:83,96`
- `…/Services/QRScannerService.swift:50` — `QRParser`, gated to `farm_app_nostr_link`
