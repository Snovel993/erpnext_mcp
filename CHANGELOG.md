# Changelog

All notable changes to this project are documented here. Versions follow
[semantic versioning](https://semver.org).

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

The one shipped template is **`us_llc_farm`** — 81 accounts (16 groups, 65
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
