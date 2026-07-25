# erpnext_mcp

[![tests](https://github.com/polehntim-commits/erpnext_mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/polehntim-commits/erpnext_mcp/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Frappe v15](https://img.shields.io/badge/Frappe-v14%20%7C%20v15%20%7C%20v16-0089ff.svg)](#compatibility)

A [Model Context Protocol](https://modelcontextprotocol.io) server that ships as
a Frappe app. `bench get-app`, `bench install-app`, tick a box, and an MCP client
— Claude Desktop, Claude Code, or anything else that speaks the protocol — can
read your ERPNext site and, only behind per-tool switches you turn on by hand,
write to it.

Nothing in it is specific to one install. Company names, account numbers, fiscal
years, report names and the Bank Transaction schema are all discovered from your
site at call time.

- **35 tools** — 28 read-only, 7 mutating.
- **Every mutating tool ships OFF.** A fresh install cannot change a document
  until you tick a box.
- **Every call is audited**, reads included, in an append-only doctype.
- **LAN-only by default.** Token *and* a CIDR allowlist.
- **Two doctypes, one endpoint, no hooks.** Installing it cannot change how
  anything already on your site behaves.
- MIT. No runtime dependencies beyond Frappe/ERPNext.

<!-- Screenshots: replace these placeholders with real captures. -->
| | |
| --- | --- |
| ![ERPNext MCP Settings](docs/img/settings-form.png) | ![MCP Action Log](docs/img/action-log.png) |
| The settings form: master switch, token, allowlist, one switch per tool. | Every call, reads included, in MCP Action Log. |

---

## Why would I install this?

Because your ERPNext site already knows the answer and you are the API.

You have a chart of accounts somebody thought about, thirty reports that took
years to get right, a purchase-order workflow with three approval states, and an
HR module that knows who was off last month. When somebody asks "which invoices
are past 90 days and who owns those customers", the answer exists — it is just
behind a login, four clicks and a filter panel, and if you want a model to help
you reason about it you end up pasting screenshots.

An LLM is perfectly capable of reasoning about a general ledger. It cannot see
one. This closes that gap without handing anything away:

- **It cannot write by default.** All seven mutating tools are off on install.
  Turning one on is a checkbox on a form only System Manager can open.
- **The dangerous verb gets its own switch.** `create_journal_entry` only ever
  produces a draft — `docstatus=0`, affecting no balance. Posting is
  `submit_journal_entry`: different tool, different switch, and it takes a name
  so it cannot create what it posts. Enable the first and not the second and you
  have given a model a scratchpad in the general ledger, which is usually what
  you actually wanted.
- **Nothing bypasses Frappe.** Writes go through
  `frappe.get_doc().insert()/.submit()/.cancel()`; workflow actions go through
  `apply_workflow`; reports go through `frappe.desk.query_report.run`. Doctype
  validation, fiscal-year checks, period-closing vouchers, frozen accounts,
  transition conditions and `on_submit` hooks all run exactly as they do for a
  human. There is no raw SQL in this app, and the test harness raises if anyone
  adds any.
- **It runs your reports rather than reimplementing them.** `run_report` invokes
  the Script and Query Reports your site already has. Reconstructing "Accounts
  Receivable Summary" out of primitive queries is how you get a number that is
  nearly right; running the report your accountant already trusts is how you get
  the one they would have got.
- **It is one whitelisted method.** No second listener, no sidecar, no new port,
  no process to supervise. Your existing nginx, TLS and access logs already
  cover it, and the server is up whenever the site is.
- **Uninstalling leaves no trace.** No `doc_events`, no scheduler jobs, no
  overrides, no fixtures. Two doctypes and an endpoint, and then they are gone.

If you maintain a Frappe site for somebody else, the honest pitch is narrower:
this is a way to let them ask questions without giving them Desk access or
giving you a support ticket, and a way to let *you* debug their site from a
terminal you are already in.

---

## Install

```bash
cd ~/frappe-bench

# 1. Fetch the app. A local path works; so does a git URL.
bench get-app https://github.com/polehntim-commits/erpnext_mcp
# or: bench get-app /path/to/erpnext_mcp

# 2. Install it on your site.
bench --site yoursite.localhost install-app erpnext_mcp

# 3. Migrate and restart.
bench --site yoursite.localhost migrate
bench restart          # in development, `bench start` picks it up on its own
```

`install-app` syncs the two doctypes and seeds their defaults, so after step 3
the read tools are already switched on — but the server itself is **off**,
because no token exists and `enabled` is unticked. It stays inert until you do
the next part.

### Turn it on

1. Open **ERPNext MCP Settings** (search it, or go to
   `/app/erpnext-mcp-settings`).
2. Click **Generate New Token**. Copy it from the dialog — it is stored
   encrypted and shown exactly once.
3. Check **Allowed CIDRs**. It defaults to loopback plus the RFC1918 private
   ranges; narrow it to the subnet your client is actually on.
4. Set an **MCP System User** (see [Attribution](#attribution)). This is not
   optional if you plan to enable reports, attachments or workflow actions —
   those honour the acting user's permissions.
5. Tick **Enabled** and save.
6. Click **Test Configuration**. It reports whether the server is ready, which
   tools are enabled, and which are unavailable on this site.

Leave every switch in the write sections alone until you have a reason.

### Smoke test

```bash
TOKEN=paste-your-token-here
SITE=http://localhost:8000

curl -sS -X POST "$SITE/api/method/erpnext_mcp.mcp.handle" \
  -H 'Content-Type: application/json' \
  -H "X-MCP-Token: $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | python3 -m json.tool
```

Then ask it to describe your site:

```bash
curl -sS -X POST "$SITE/api/method/erpnext_mcp.mcp.handle" \
  -H 'Content-Type: application/json' \
  -H "X-MCP-Token: $TOKEN" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"get_company_topology","arguments":{}}}' \
  | python3 -m json.tool
```

A `404` means `enabled` is off or no token is stored — those two are
deliberately indistinguishable from the app not being installed. A `401` means
the token is wrong; a `403` means your address is outside **Allowed CIDRs**. The
real reason for any rejection is in **MCP Action Log**, where you can read it and
a stranger cannot.

---

## Connect a client

The endpoint is MCP Streamable HTTP (JSON-RPC 2.0 over `POST`). It is POST-only:
this server never initiates a message, so there is no SSE stream to open, and a
`GET` returns a 405 saying so.

**Use the `X-MCP-Token` header, not `Authorization: Bearer`.** Frappe's own auth
layer inspects `Authorization` before any whitelisted method runs and routes a
`Bearer` value into its OAuth2 validator; an MCP token does not survive that trip
on every version. `Authorization: Bearer` is still accepted where it works, but
`X-MCP-Token` is the one that always arrives.

### Claude Desktop

Claude Desktop launches MCP servers as local processes, so an HTTP server is
reached through the `mcp-remote` bridge. Edit
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "erpnext": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "https://erp.example.com/api/method/erpnext_mcp.mcp.handle",
        "--transport",
        "http-only",
        "--header",
        "X-MCP-Token: YOUR_TOKEN_HERE"
      ]
    }
  }
}
```

- **Plain HTTP on the LAN** — add `"--allow-http"` to `args`.
- **Keeping the token out of the config file** — pass it from the environment:
  ```json
  "args": ["-y", "mcp-remote", "https://erp.example.com/api/method/erpnext_mcp.mcp.handle",
           "--transport", "http-only",
           "--header", "X-MCP-Token: ${ERPNEXT_MCP_TOKEN}"],
  "env": { "ERPNEXT_MCP_TOKEN": "YOUR_TOKEN_HERE" }
  ```
  The whole header must stay one argument.

Restart Claude Desktop. The tools appear under the server name `erpnext`.

### Claude Code

No bridge needed:

```bash
claude mcp add --transport http erpnext \
  https://erp.example.com/api/method/erpnext_mcp.mcp.handle \
  --header "X-MCP-Token: YOUR_TOKEN_HERE"
```

### Anything else

`POST https://<your-site>/api/method/erpnext_mcp.mcp.handle` with
`X-MCP-Token: <token>`. The server negotiates protocol versions `2025-06-18`,
`2025-03-26` and `2024-11-05`, echoing back whichever the client asks for.

---

## The 35 tools

Full arguments, return shapes and worked examples:
**[docs/tool-catalog.md](docs/tool-catalog.md)**.

Tools whose site prerequisite is missing are not advertised at all — on a site
without Frappe HR, the three HR tools simply do not exist as far as a client is
concerned.

### Read-only — 28, all ON by default, each individually switchable

**Accounting** — the v0.1.0 surface

| Tool | What it answers |
| --- | --- |
| `get_company_topology` | What is this install? Companies, currencies, cost centers, fiscal years, chart roots, which optional doctypes exist. **Call this first.** |
| `get_account_balance` | Balance of one account as of a date, from GL Entry, in both raw and natural sign convention. |
| `get_journal_entries` | JE headers by date range, company, account-on-any-line, docstatus. |
| `get_journal_entry` | One JE in full, every line with party, cost center and reference. |
| `list_bank_transactions` | Bank Transactions by account, date range and status, amounts normalised to one signed number. |
| `get_bank_statement` | One Bank Statement, on versions that have the doctype. |
| `list_fiscal_years` | Fiscal years and the companies they apply to. |
| `get_chart_of_accounts` | The chart as a nested tree, optionally one root type. |
| `list_unreconciled_bank_transactions` | The reconciliation worklist. |
| `search_accounts` | Turn "cash clearing" into a docname. Ranked best-first. |

**Workflow**

| Tool | What it answers |
| --- | --- |
| `list_workflows` | Every workflow: governed doctype, states, transitions, roles, which states are terminal. |
| `get_workflow_state` | Where one document sits, and every transition out of that state. |
| `list_pending_approvals` | The worklist — documents in a state something still has to be done to, optionally narrowed to one user's roles. |
| `list_available_actions` | What the acting MCP user can do to this document *right now*, conditions and self-approval evaluated. |

**Reports**

| Tool | What it answers |
| --- | --- |
| `list_reports` | Every saved report, its `ref_doctype` and its type. |
| `run_report` | Runs it. Query, Script and Report Builder all handled. Enforces the acting user's permissions. |

**Attachments**

| Tool | What it answers |
| --- | --- |
| `list_attachments` | Files on a document: name, size, private flag, who uploaded it. |
| `get_attachment_content` | One file's bytes, base64, size-capped. |

**Comments and tasks**

| Tool | What it answers |
| --- | --- |
| `list_comments` | The comment and activity thread on a document. |
| `list_assigned_todos` | What is on somebody's list, with overdue flagged. |

**HR** — only where the `hrms` app is installed

| Tool | What it answers |
| --- | --- |
| `list_employees` | Employees with department, designation, status, joining date. |
| `get_attendance_summary` | Per-employee Present / Absent / Half Day / On Leave counts over a range. |
| `get_leave_balance` | Remaining leave per type, computed by HR's own balance function. |

**Sales and purchasing**

| Tool | What it answers |
| --- | --- |
| `list_sales_orders` | SO headers with totals and delivery progress. |
| `get_outstanding_invoices` | Who owes what, aged into buckets. |
| `list_purchase_orders` | PO headers with totals and receipt progress. |

**Site customisation**

| Tool | What it answers |
| --- | --- |
| `list_custom_fields` | Custom Fields in form order. The "why is my field not showing up" tool. |
| `list_client_scripts` | Client Scripts with a 500-character preview of each body. |

### Mutating — 7, all OFF by default

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_journal_entry` | Creates a **draft** JE. Refuses unbalanced entries, negative amounts, group accounts, single-line entries. | Submit. There is no argument for it. |
| `submit_journal_entry` | Submits an existing draft, `0 → 1`. Writes GL Entries. **This moves balances.** | Create anything — it takes a name. |
| `cancel_journal_entry` | Cancels a submitted JE, `1 → 2`, writing reversing entries. `reason` mandatory. | Delete anything. |
| `create_bank_transaction` | Inserts a **draft** Bank Transaction. | Submit it. |
| `reconcile_bank_transaction` | Attaches payment vouchers, refusing to over-allocate. | Allocate past the remaining amount. |
| `advance_workflow` | Takes a workflow action via `apply_workflow`. Can submit or cancel the document if the target state says so. | Take an action the acting user is not allowed. |
| `create_todo` | Assigns a ToDo. | Touch any ledger. |

---

## Security posture

Full threat model: **[docs/security.md](docs/security.md)**. The short version:

- **Three gates, all mandatory.** Master switch (off ⇒ 404), token
  (constant-time compare; missing or wrong ⇒ 401), CIDR allowlist (outside ⇒
  403). An empty allowlist denies everyone rather than allowing everyone.
- **Rejections are opaque.** Every refusal says the same thing. Which gate failed
  goes to the audit log, not to the caller.
- **The token is a Password field.** Encrypted at rest, never logged, never
  returned by any tool, shown to the operator exactly once.
- **Not for the internet.** LAN-facing, behind your existing reverse proxy.
- **Ledger reads are authorized by the token, not by roles.** The accounting read
  tools use `frappe.db.get_all`, which does not consult Frappe permissions. Three
  categories are different and *do* enforce them: **reports** (they run through
  the Desk APIs), **attachments** and **comments** (they check `read` on the
  parent document). Mutations always run the acting user's permission checks.
  The reasoning is in docs/security.md — it is a real trade-off, not an
  oversight.
- **To stop everything right now:** untick **Enabled** and save. Next request is
  a 404. No restart, no token rotation.

### Attribution

With **Attribute Actions To An MCP System User** on (the default), set an **MCP
System User** and every document a tool creates carries that `owner`, and every
permission-checked tool is bounded by that user's roles. Create a dedicated user
— `mcp@yourcompany.example` — with only what the tools you enabled need.
**Accounts User** covers the ledger tools; reports, attachments and workflow
actions need whatever their own doctypes require.

Leave it blank and mutations run as `Administrator`, which works and makes
MCP-authored documents indistinguishable from an admin's own.

---

## Audit log

Every call gets a row in **MCP Action Log** (`/app/mcp-action-log`) — reads
included, refusals included. The interesting question after the fact is rarely
"what did it change", it is "what did it see".

| Field | Example |
| --- | --- |
| `timestamp` | `2026-07-25 14:31:07.482119` |
| `tool_name` | `advance_workflow` |
| `caller_ip` | `10.0.4.22` |
| `arguments_json` | `{"action": "Approve", "doctype": "Purchase Order", "name": "PUR-ORD-2026-00184"}` |
| `result_status` | `Success` \| `Error` \| `Blocked` \| `Unauthorized` |
| `result_summary` | `Approve on Purchase Order PUR-ORD-2026-00184: 'Pending Approval' → 'Approved' as mcp@example.com` |
| `docstatus_delta` | `0 → 1 (submitted)` |

Rows are **append-only**: the doctype grants System Manager read and delete but
not write, and the controller refuses an update even from a script. Delete is
allowed so a busy site can be pruned — Frappe records every deletion in its own
Deleted Document doctype.

A row written by a *failed* mutation is committed on its own, after the failed
work is rolled back, so the record of the attempt survives even though the
attempt did not.

**Uninstalling drops this table.** Export it first if you need it; the
`before_uninstall` hook will remind you.

---

## Compatibility

| | Supported | Notes |
| --- | --- | --- |
| **Frappe** | v14, **v15**, v16 (`develop`) | Developed and run in production against **v15.115.0**. v15.100+ is the tested floor. |
| **ERPNext** | v14, v15, v16 | Required — `hooks.py` declares it, so `install-app` refuses on a Frappe-only site. |
| **Frappe HR (`hrms`)** | optional | Present → the three HR tools appear. Absent → they are not advertised at all. |
| **Python** | 3.10+ | CI runs 3.10 and 3.11. |
| **Database** | MariaDB, Postgres | No raw SQL anywhere, so whatever your bench runs. |

This app asks the site what it has rather than pinning a version — see
`erpnext_mcp/compat.py`. Concretely:

- **Bank Transaction amounts** — `deposit`/`withdrawal` on newer ERPNext, a
  single signed `amount` on older. Tools report one normalised `amount_signed`
  and say which layout they found.
- **Unallocated amounts** — the column where it exists, computed
  `gross - allocated` where it does not, and the response says which.
- **Company default cost center** — `cost_center` or `default_cost_center`, or
  `null` on a Company created before its chart of accounts.
- **Bank Statement** — absent on older ERPNext; the tool is not advertised there.
- **Client Script** — was `Custom Script` before v13; both are handled.
- **ToDo assignee** — `allocated_to`, or `owner` on versions before it existed.
- **Leave balance API** — `hrms.hr...` since v14, `erpnext.hr...` before the
  split.
- **Fields that do not exist are never selected.** Selecting a missing column is
  a hard SQL error, which is exactly the failure mode a reusable app must not
  have.

---

## Tests

```bash
# Logic: refusals, arithmetic, switch handling, protocol shape.
# No bench, no database, no third-party packages. ~0.3s.
python3 -m unittest discover -s tests_standalone -t .

# Framework contract: migration, real encryption, real ERPNext validation,
# real permission enforcement, the real workflow and report APIs.
bench --site yoursite.localhost run-tests --app erpnext_mcp
```

433 standalone tests and 96 in-bench tests. The standalone suite installs an
in-memory `frappe` double so the refusal tests get run every time rather than
only when a bench is handy; the in-bench suite covers what only a real site can
prove and skips rather than fails when the site lacks the setup a case needs.
Details in **[docs/development.md](docs/development.md)**.

---

## Roadmap

Candidates for v0.3, roughly in order of how often they come up:

- **Auth.** Per-token scopes, so one client can be given the reconciliation
  tools and another the reporting tools without two sites. Token expiry and
  rotation without a manual button press. Optional mTLS for the LAN case.
- **More workflow depth.** Bulk actions over a filtered set, workflow history
  (who moved what, when) from the Version table, and a `preview_workflow_action`
  that says what a transition *would* do before it does it.
- **Custom report authoring.** A mutating, default-off tool to create a Query
  Report from SQL an operator reviews first — the "I ask for a figure often
  enough that it should be a report" case.
- **Write coverage for the categories that only read today.** Nothing in HR or
  sales writes, on purpose; the useful additions are probably `create_todo`'s
  siblings (assign, close) rather than posting invoices.
- **Prepared-report support** in `run_report`, for reports too slow to run
  inline.
- **A Workspace**, so the two doctypes appear in the app switcher instead of
  only via search.

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Uninstall

```bash
bench --site yoursite.localhost uninstall-app erpnext_mcp
```

Drops **ERPNext MCP Settings** and **MCP Action Log**, including the audit
history. Nothing else on the site is touched.

---

## License

MIT. See [LICENSE](LICENSE).
