# Changelog

All notable changes to this project are documented here. Versions follow
[semantic versioning](https://semver.org).

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
