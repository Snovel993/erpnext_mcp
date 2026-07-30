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

- **121 tools** — 57 read-only, 64 mutating.
- **Every mutating tool ships OFF.** A fresh install cannot change a document
  until you tick a box.
- **Every call is audited**, reads included, in an append-only doctype.
- **LAN-only by default.** Token *and* a CIDR allowlist.
- **Its own doctypes, one endpoint, no hooks.** It adds no field to any doctype
  it did not create, so installing it cannot change how anything already on your
  site behaves.
- MIT. Two runtime dependencies beyond Frappe/ERPNext (`shapely` and `h3`, for
  field boundaries), and the app still loads without them.

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

- **It cannot write by default.** All 38 mutating tools are off on install.
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
  overrides, no fixtures, and no field added to a doctype it did not create.
  Its own doctypes and an endpoint, and then they are gone.

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

`bench get-app` installs the two declared dependencies (`shapely` and `h3`) into
the bench's environment along with the app. If yours did not — an offline
install, a locked-down environment — the five field-boundary tools go quietly
unavailable and say so by name when a client asks for them; everything else
works. To add them afterwards:

```bash
./env/bin/pip install "shapely>=2.0" "h3>=4.0.0" && bench restart
```

`install-app` syncs this app's doctypes and seeds their defaults, so after step 3
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
7. Use the **Connect to Claude Desktop** section that appears below the token to
   copy or download a ready-made client config — no hand-editing JSON. The panel
   shows which address it used and what else it considered; if that address is
   wrong, set **Public URL** and it always wins.

   Two cases need **Public URL** set explicitly. A site reached from outside on a
   different hostname (a Tailscale Funnel, a tunnel, a reverse proxy) has no way
   of knowing its own public name. And a published Docker port is invisible from
   inside the container — the panel recovers it from your browser's `Origin`
   header, which works when you are looking at the form, but not if the address
   you browse differs from the one clients use.

   If the generated URL is a bare IP, the panel warns you: Frappe routes requests
   to a site by Host header and an IP matches no site, so a client can get "site
   not found" while your browser works. Set `default_site` in
   `common_site_config.json`, or give the site a `host_name` that resolves for
   your clients, or put that name in **Public URL**.

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

> **The settings form will do this for you.** Once the endpoint is enabled, the
> **Connect to Claude Desktop** section on ERPNext MCP Settings renders the exact
> JSON for your site — right URL, right token — with copy and download buttons and
> the config-file path for your platform. The rest of this section is what it
> generates, for anyone who would rather write it by hand.

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

## The 121 tools

Full arguments, return shapes and worked examples:
**[docs/tool-catalog.md](docs/tool-catalog.md)**.

Tools whose site prerequisite is missing are not advertised at all — on a site
without Frappe HR, the three HR tools simply do not exist as far as a client is
concerned.

### Read-only — 57, all ON by default, each individually switchable

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
| `propose_clean_chart` | A complete numbered chart of accounts for a company, from a static template, in the shape `import_chart_of_accounts` takes. Reports what it would collide with. Creates nothing. |
| `list_cost_centers` | The company's cost centers as a nested tree. Disabled ones excluded and counted unless asked for. |

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

(`attach_file_to_document` puts one *there* — it writes, so it lives with the
write tools below.)

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

**Compliance packets**

| Tool | What it answers |
| --- | --- |
| `list_compliance_packets` | Which packet types this site can produce, and the filters each takes. |
| `generate_compliance_packet` | Builds one. See [Compliance packets](#compliance-packets) below. |

**Governance and assets**

| Tool | What it answers |
| --- | --- |
| `list_cap_table` | Who the members are: anonymous id, legal entity, admission, percentage. Retired members included. **This is the tool that de-anonymises the ledger** — see [Members are anonymous](#members-are-anonymous-on-purpose). |
| `list_member_events` | The equity trail: contributions, distributions, transfers, and the narrative for each. |
| `list_governance_documents` | What is in the archive, with the amendment chain and which entries are still in force. |
| `get_governance_document_content` | One archived document's metadata and its attachment, base64. |
| `depreciation_note_alignment_check` | For every financed asset, whether its remaining life and its note's remaining term still agree. |
| `list_notes_payable` | Every note or loan a company owes: lender, original and outstanding principal, rate, term, and when the next payment falls due. |

**Land, leases and related parties** — the v0.11.0 surface

| Tool | What it answers |
| --- | --- |
| `list_parcels` | The land register: acreage, county, assessor parcel id, use type, appraised value, and which parcels are on the balance sheet. Totals acreage and value, and reports the oldest appraisal — which is how you find out the valuation is four years stale. |
| `get_parcel` | One parcel in full, with every lease over it in either direction and the gap between what the Asset cost and what the appraisal says. |
| `list_leases` | Leases **both ways** with the rent roll: annual rent receivable, annual rent payable, the net, and what runs out inside 90 days. A crop share has no annual rate and is listed rather than counted as zero. |
| `get_lease` | One lease in full, with the parcel it covers, its attachments, and whether it is in force today by the dates on the record. |
| `list_related_parties` | Who is related to this company, in what capacity, since when, and which relationships have **no governing document behind them** — the first thing an examiner asks for. |
| `get_related_party` | One relationship with everything pointing at it: the person's other roles, their cap table entry, their Supplier, the parcels they hold title to. Never returns more than four digits of a taxpayer id. |

**Companies, ground and camps** — the v0.12.0 surface

| Tool | What it answers |
| --- | --- |
| `list_companies` | Every company with its abbreviation, currency, tax-id status, fiscal year period, cost center and account counts, and **the GL entry count with the first and last posting dates** — which is how you tell a live company from a shell, and what decides whether its currency can still be changed. Also reports whether the `Family` and `Contact` party types are registered. |
| `list_fields` | The block register: acreage totalled, plantings summarised, condition and food-safety facts per block — and **the varieties already planted on this site**, which is the autosuggest list worth having because a hardcoded one is wrong the first time somebody plants something new. |
| `get_field` | One block in full, with every irrigation zone over it, the water rights they run under, and how much of the block is not zoned at all. |
| `get_parcel_field_summary` | One parcel rolled up: blocks, planted acres against the parcel's own acreage **and the gap**, zones, flow, planting ages, counts by condition and variety, and the food-safety exceptions. |
| `list_irrigation_zones` | The zone register with area and flow totalled — plus the zones with **no agricultural water test** on record and the surface-water zones with **no water right named**. Those two lists are the report. |
| `get_irrigation_zone` | One zone in full, with the block it waters, this zone's share of it, and the compliance gaps in sentences rather than left to be inferred. |
| `list_housing_units` | The camp register: capacity, occupancy and open beds, plus the overdue habitability inspections, the uninhabitable units, the FSMA worker facilities, and the units filled past what their floor area lawfully allows. |
| `get_housing_unit` | One unit in full with its **whole assignment history**, and what each missing compliance date obliges. |
| `list_housing_assignments` | Who is housed where, currently or over a period, with the wage-deduction assignments named and the deposits still held. |
| `get_housing_capacity` | Beds, bodies and overdue inspections per parcel and in total, with a plain one-sentence-per-parcel readout. |
| `get_employee_housing_history` | Everywhere one person has been housed, in order, with deposits and wage deductions — the audit trail an IRS Section 119 exclusion is defended with. |
| `find_fields_containing_point` | **The geofence query.** Which blocks a GPS fix is inside — bounding box first, then exact point-in-polygon, with the boundary counting as inside. Reports how many blocks have no boundary at all, because an empty result on a half-mapped farm means "not inside any *mapped* block", not "not on the farm". |
| `find_fields_by_h3_cell` | Which blocks an H3 cell touches, at any resolution. The spatial-index join for anything else keyed on H3 — a bucket log, a crew track, a weather grid. |

### Mutating — 64, all OFF by default

**Postings into the ledger**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_journal_entry` | Creates a **draft** JE. Refuses unbalanced entries, negative amounts, group accounts, single-line entries. | Submit. There is no argument for it. |
| `submit_journal_entry` | Submits an existing draft, `0 → 1`. Writes GL Entries. **This moves balances.** | Create anything — it takes a name. |
| `bulk_submit_journal_entries` | Submits up to 500 drafts, each in its own transaction, and reports per document. One failure does not undo the rest. | Run at all unless `submit_journal_entry` is also switched on. |
| `cancel_journal_entry` | Cancels a submitted JE, `1 → 2`, writing reversing entries. `reason` mandatory. | Delete anything. |
| `delete_draft_journal_entry` | Deletes a **draft** outright, recording what it was and why in the audit log. | Touch a submitted entry (cancel it) or a cancelled one (its reversing rows are the trail). |
| `create_bank_transaction` | Inserts a **draft** Bank Transaction. | Submit it. |
| `reconcile_bank_transaction` | Attaches payment vouchers, refusing to over-allocate. | Allocate past the remaining amount. |
| `set_opening_balance` | Books one historical event as a **draft** opening-balance JE, **computing** the offsetting line against Opening Balance Equity and flagging the entry as opening. | Submit it, or guess an equity account when two could match. |
| `post_opening_balance_journal_entry` | Books a whole opening balance sheet as one JE, every line given, the difference going to an `offset_account` you name. Posts it too, with `submit: true`. | Submit unless `submit_journal_entry` is also switched on — checked before anything is written. |
| `create_bank_account` | Creates the Bank Account a feed writes into, and the Bank behind it, in one transaction. | Point at anything but an Asset (bank) or a Liability (credit card) — or at an Asset whose `account_type` is not Bank or Cash. |

**Structural changes to the chart itself**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_account` | One account under an existing group. Checks the parent, the root type, the number's uniqueness and the account type before writing. | Create a root — ERPNext treats roots as uneditable once made. |
| `update_account` | Rename, renumber, re-type, enable/disable. The docname moves with the fields, via ERPNext's own `update_account_number`. | Reparent. That is `move_account`, on purpose. |
| `move_account` | Reparent, and nothing else. Refuses a cross-root or cyclic move. | Rename anything. |
| `disable_account` | ERPNext's soft delete, `reason` mandatory. | Touch an account with GL entries in the current fiscal year, or delete anything ever. |
| `delete_account` | **Irreversible.** Hard-deletes an account with no history, freeing its number — which disabling does not. Four checks, all refusals, all reported at once. | Remove an account with GL entries, journal entry lines (drafts included), children, a company default or a Bank Account pointing at it. |
| `import_chart_of_accounts` | Builds a whole tree in one transaction, rolling back entirely on any failure. New top-level accounts included. **`dry_run` defaults to true.** | Silently reparent or rename an account that already exists. |
| `create_fiscal_year` | Creates a Fiscal Year, so ERPNext will accept postings dated inside it. Company-aware overlap check. | Overlap a year that shares a company, or take an end date that is not one year after the start unless `is_short_year`. |
| `update_fiscal_year` | Moves a year's dates, or enables/disables it. | Move dates in a way that leaves existing GL entries in no fiscal year, rename the year, or change which companies it applies to. |
| `advance_workflow` | Takes a workflow action via `apply_workflow`. **Can submit or cancel the document** if the target state says so. Supports `dry_run`. | Take an action the acting user is not allowed. |
| `create_todo` | Assigns a ToDo. | Touch any ledger. |

**How a posting is classified**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_cost_center` | One cost center under an existing group. Checks the parent and the number's uniqueness before writing. | Add a second root — ERPNext gives every company exactly one, named after the company. |
| `update_cost_center` | Rename, renumber, disable/enable. The docname moves with the fields. | Reparent, or rename the company's root. |
| `create_accounting_dimension` | Creates the dimension, adds its Link field to the documents that carry it, and — only when asked — generates the custom DocType whose records are its values. | Point at a Single, a child table or a core doctype; reuse a fieldname another field already holds. |
| `create_dimension_value` | One record in the DocType a dimension points at. | Touch a ledger, or add a field. |
| `set_company_defaults` | Points the company's default account and cost center fields at real accounts, **type-checked**. Idempotent. | Write any of them if one value in the batch fails validation. |

**Who owns it, and what happened to their interest**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_cap_table_entry` | Registers one member: anonymous id, legal entity, admission date, percentage. | Register a member the ledger cannot already refer to, or one already retired. |
| `update_cap_table_entry` | Changes a member's details. | Retire a member, or change the `member_id` every posting is tagged with. |
| `close_cap_table_entry` | Retires a member and writes the exit into the event trail. | Move any money — a final distribution is a separate call. |
| `record_member_event` | Records the event and, where it books money, a **draft** JE with the right accounts and the member tag on every line. | Post it. Guess an equity account when two could match. |
| `submit_member_event` | Posts the draft the event is waiting on. | Run at all unless `submit_journal_entry` is also switched on. |
| `attach_governance_document` | Files a governing document, attaches its content privately, and chains it onto what it supersedes. | File the same document twice, or make the amendment chain loop. |

**Evidence, onto the document it belongs to**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `attach_file_to_document` | Attaches one file to **any** document — a statement onto the Journal Entry that books it, a receipt onto a Bank Transaction, a contract onto an Asset. Private by default, sha256 in the result and the audit row. | Attach to a document the acting user cannot write, to a cancelled one without being told to, or twice under the same filename. Move a balance, or change any existing row. |

**Assets that serve more than one segment**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_asset` | Creates an ERPNext Asset plus the cost profile holding its usage split, its schedule and its note. **Switches ERPNext's own depreciation off on that asset.** | Accept a split that does not total 100, or a useful life that disagrees with the note's tenor. |
| `update_asset_allocation` | Replaces the split, from the next period onwards. | Rewrite periods already written — that is the history. |
| `link_asset_to_note` | Ties an asset to its financing and holds their terms together. | Link a diverging pair unless you pass `enforce_tenor=false`. |
| `run_depreciation_cycle` | Writes the depreciation due as **draft** JEs, split across each asset's cost centers. **`dry_run` defaults to true.** | Post a period twice, or post anything at all. |

**What the company owes**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_note_payable` | Registers one note or loan: terms, provenance, the liability account it posts to, and the asset it financed. | Link an asset whose useful life disagrees with the note's term, unless you pass `enforce_asset_tenor=false`. |
| `record_loan_payment` | Splits a payment into principal and interest and drafts the JE that books it, decrementing the balance. | Book a split that does not add up, clear more principal than is owed, or post anything. |
| `close_note_payable` | Closes a note as Paid Off, Refinanced or Written Off, recording the disposition in its own history. | Write a journal entry — relieving the balance is a posting somebody should make on purpose, and the response says which. |

**Land and the agreements over it**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_parcel` | Registers one parcel: county, assessor parcel id, acreage, use type, appraised value and the date it was appraised as of. | Register a second parcel with the same name, or the same assessor id, for one entity — that number is the county's key and two of them means a typo. |
| `update_parcel` | Changes a registered parcel, echoing every change as before → after. | Re-key it, move it between entities (a conveyance is not an edit), or set the asset link — that is its own tool. |
| `link_parcel_to_asset` | Points a parcel at the Fixed Asset carrying it and **reports the gap between cost and appraised value**. | Post anything. Land is not depreciated and this does not pretend otherwise. |
| `create_lease` | Records one lease in either direction, with the executed document attached. Reports whether the stated direction agrees with the party names. | Book anything. Recording the agreement and booking its consequences are separate acts. |
| `update_lease` | Changes status, term, rent, parties, parcel or counterparty. | Mark a lease Terminated without a termination date, or rename it — a renewal is a new lease with its own term. |

**Who is related to whom**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_related_party` | Registers one relationship: who, what kind of entity, in what capacity, from when, under what document. | Store more than four digits of a taxpayer id. Nine is **refused**, naming the four to send — see [Four digits, never nine](#four-digits-never-nine). |
| `update_related_party` | Changes party type, dates, tax identity, address and the links to a cap table entry, a Supplier and a governing document. | Re-key it. A change of role is a new relationship; the old entry gets an end date rather than being deleted. |

**Companies, and the two party types a family operation actually pays**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_company` | Stands up a Company plus the chart of accounts, cost centers and the fiscal year containing today — April for a farm year, January for a calendar one — and reports what ERPNext **actually** built, which is not always what was asked for. | Reuse a name or an abbreviation. Every account, cost center, parcel and lease docname ends in the abbreviation, and two companies sharing one makes those ambiguous. |
| `update_company` | Changes country, tax id and notes; changes the currency **only while nothing is posted**. Echoes every change as before → after, with the tax id redacted to its last four. | Change the abbreviation or the name (both are a migration, not an edit), the currency once anything is posted, or the fiscal year start month once any year exists — see [Two party types, and one exclusion](#two-party-types-and-one-exclusion). |
| `register_party_types` | Registers `Family` and `Contact` so a Journal Entry line can carry them. Idempotent, and already seeded on install and every `bench migrate`. | Reclassify anything. Existing rules and entries using Shareholder, Employee or Supplier are untouched. |

**The structure under a parcel**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_field` | Registers one planted block: acreage, crop, variety, rootstock, planting year and density, condition — and the food-safety facts, which live on the block because they are the same facts the operation runs on. Docname is `<field name> - <parcel abbr>`. | Register blocks whose acreage sums to **more than the parcel they are on**, named with both figures and the excess. That is the failure a bad import produces every time. |
| `update_field` | Changes any of the above, echoing before → after. | Re-key it, move it to another parcel (ground does not move), or set the cost center — that is its own tool. |
| `link_field_to_cost_center` | Points a block at the Cost Center its costs book to, so per-acre and per-block costing has somewhere to land. | Use a cost center on another company's books (that is an intercompany transaction, not a dimension), a group cost center, or repoint a linked block without `replace=true`. |
| `create_irrigation_zone` | Registers one zone: source, Oregon water right, flow, sprinkler type, area — and the FSMA agricultural water facts. Docname is `<zone name> - <parcel abbr>`; acres are **computed** from square feet. | Reuse a zone number on one block — that number is what somebody types into the controller at two in the morning — or let zones sum past their block's acreage. |
| `update_irrigation_zone` | Changes any of the above and recomputes the acres. | Re-key it, move it to another block (pipe does not move), or set the acres directly. |
| `import_farm_app_fields` | Creates Fields from a batch of legacy Farm App records **carrying their ids**, so a later sync engine has something to match on. Dry run by default; a block already registered is skipped with the reason, so the same batch re-runs safely. | Update anything, delete anything, or half-import a farm — the whole batch is validated before the first insert. |
| `set_field_boundary` | Gives a block its shape as GeoJSON and derives centroid, bounding box, H3 coverage at resolutions 6–10 and the area the polygon encloses. | Accept invalid GeoJSON, a self-intersecting polygon, coordinates off Earth, or an area more than 25% from the recorded acreage — at that point one of the two figures is about a different piece of ground. |
| `set_zone_boundary` | The same for a zone, and reports whether it sits inside the block it waters. | **Enforce** that containment. A shared water line crosses a boundary, a pump house sits on the headland — refusing would make those unrecordable, so it reports and lets the operator decide. |
| `import_field_boundary_geojson` | Sets boundaries on **existing** blocks from a GeoJSON FeatureCollection — for migrating a farm's polygons in one go. Per-feature errors, so one bad feature in forty does not stop the other thirty-nine. | Create a Field. A feature naming an unregistered block is skipped with that said. |

**The camp**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_housing_unit` | Registers one building with its capacity, condition and the compliance facts, computing the lawful occupancy from floor area at 50 sq ft a head unless you pass one. | Reuse a unit name on one parcel, or link an Asset on other books or already carrying a different unit. **Warns rather than refuses** a capacity over 20 outside a Multi-Unit Building — a twenty-person cabin is barracks by another name, and some really are. |
| `update_housing_unit` | Changes any of the above; recomputes the lawful occupancy when the square footage moves, **unless** the stored limit was one somebody typed. | Re-key it, or move a building between parcels — even a manufactured home that really was moved should be re-registered where it stands, so the assignment history stays attached to the ground it happened on. |
| `create_housing_assignment` | Puts one person in one unit from a date, with the deposit and the ORS 653 wage-deduction answer. Auto-named `HA-YYYY-MM-<seq>`. | Overlap an existing assignment on that unit unless `allow_multi_occupancy=true`; assign anybody to a shower block, a shop or an uninhabitable unit; or, where an HR app is installed, name somebody who is not on file. |
| `end_housing_assignment` | Writes the date somebody moved out and the deposit returned. | **Delete.** The record is what defends a Section 119 exclusion, answers a wage claim and tells an investigator who was in the camp that week. |

**Documents that get produced and filed**

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `generate_quarterly_investment_report` | Builds the quarter's report as a **PDF** and files it as a Prior Statement with the PDF attached: assets under management, activity, fee accrual, performance against a benchmark with a high-water mark, cash clearing, reconciliation state. | Run on a quarter that is not genuinely closed — see [A quarter closes when it closes](#a-quarter-closes-when-it-closes). Or invent a benchmark rate. |
| `generate_1099_prefill` | Aggregates a calendar year of supplier payments into an xlsx worksheet and a per-recipient 1099-NEC (Copies A, B and C), filed as a Tax Filing. | Finish the job: taxpayer ids print as the last four digits, and Copy A is stamped as an information copy rather than a filing. |

#### Four digits, never nine

`Related Party.tax_id_last4` takes exactly four digits and refuses nine — not
truncated, not masked, not accepted with a warning. The refusal names the four
digits to send instead, because a validator that says "invalid format" to
somebody who has just pasted a real SSN has told them nothing about why it
matters.

The controller enforces the same rule, because the Desk form is a second door
into the same field, and the field is declared four characters long as the belt
to that brace. `get_related_party` never returns more than four digits **even
from a linked Supplier** — `supplier_detail.tax_id` says only whether one is on
file.

#### Two party types, and one exclusion

ERPNext ships Customer, Supplier, Employee and Shareholder. A family operation
pays two kinds of people that fit none of them, and v0.12.0 registers both:

- **`Family`** — a relative receiving money that is neither payroll nor a
  purchase. `generate_1099_prefill` **excludes** those postings and reports the
  count, the total and the names, so "nobody looked" and "somebody looked and
  excluded them" are different-looking answers. A transfer below the IRS annual
  gift exclusion is not compensation for services: it needs no W-9 and produces
  no form. Without this party type those payments end up recorded as Supplier
  payments, which puts family money into vendor spend **and** onto a 1099 the
  recipient owes no tax on.
- **`Contact`** — the consultant who looks at the orchard twice a year, the
  neighbour who runs a tractor for a weekend. Not a formal Supplier, but paid
  for services. The pre-fill reads those postings and classifies them
  **borderline**, naming the W-9, rather than dropping them — which is the same
  mistake in the other direction.

Both are seeded on install and on every `bench migrate`, and neither touches
anything already recorded: an existing rule or Journal Entry using Shareholder,
Employee or Supplier keeps working exactly as it did.

**A party type's name has to be the name of a DocType**, and that is not a
convention — it is how ERPNext resolves a posting. `Party Type` names itself
`field:party_type`, and that field is a `Link` to `DocType`; a Journal Entry line
then carries `party`, a **`Dynamic Link`** resolved through `party_type`. So
`party_type = "Family"` requires a DocType called `Family`, and
`party = "Alex Bramwell"` requires Alex to be a record in it.

`Contact` already has one — Frappe ships it — which is why it registered
successfully in v0.12.0 while `Family` did not. **v0.12.1 ships a `Family`
DocType**: a small register of name, relationship, an optional link to the
related-party entry, and an active flag. It holds no tax id on purpose. A
relative who is genuinely paid for work is a Contact or a Supplier, and the
posting should be reclassified rather than the exclusion widened.

If a party type ever cannot be registered — its DocType is missing — the seeder
**skips it with the reason and carries on**. It does not raise: an exception
inside a patch aborts `bench migrate` for every app on the bench, not just this
one.

#### The polygon is the evidence, and the index has to be a superset

A boundary is not decoration on a Field. "Which block was sprayed" is a Worker
Protection Standard answer, "which zone got the water test" is an FSMA Subpart E
answer, and "was the worker in an authorised area" is both a payroll question and
a food-safety one. Every one of those resolves to a shape on the ground — without
it the record says a name, and a name is not something anybody can check against
a GPS fix.

**Everything derived is derived on save and cannot be set directly.** Centroid,
bounding box, H3 coverage and computed area are all functions of the polygon. A
figure a caller could edit independently is a figure that will disagree with the
shape, and the disagreement surfaces as a geofence saying no to somebody standing
in the right place.

**The H3 fill stores every cell the shape *touches*, not every cell whose centre
is inside it.** This is the one implementation detail worth knowing. H3's default
polygon fill is centre-based, and an orchard block is smaller than one H3 cell at
resolutions 6, 7 and 8 — so the default returns an **empty set** for a real
field, and an index built on it answers "in no field" for a point plainly in one.
A false negative that reads like a policy decision is exactly what a geofence
must not produce, so the fill uses `contain="overlap"`, which is a true superset.

For the same reason `find_fields_containing_point` narrows with the **bounding
box** rather than with the H3 cells: a bbox is a guaranteed superset of the shape
it bounds, so a candidate set built from it cannot miss the right answer. The
exact point-in-polygon test then settles every candidate, and the boundary counts
as inside — a pick recorded on the headland is in the block.

**Area is spherical, and said so.** `shapely` computes area in the units of its
coordinates, and these coordinates are degrees, so `.area` is degrees squared —
not an area of anything. The computed acreage uses the standard spherical-excess
integral, which differs from a WGS84 ellipsoidal area by well under 1% at these
latitudes. That is far inside the 5% and 25% thresholds it is compared against,
and a projection library to close a gap smaller than the disagreement between a
deed and a GIS trace would be precision theatre.

The satellite fields (`satellite_provider`, `imagery_asset_ref`,
`last_ndvi_pull_date`, `last_ndvi_mean`, `last_ndvi_stddev`) are schema only in
this release — nothing fetches imagery yet. NDVI is stored on its real range of
**-1 to 1**, not 0 to 1: water and bare soil read negative, and clamping the
floor to zero would make a flooded block indistinguishable from an unmeasured
one.

#### Compliance lives on the record it belongs to

The food-safety fields are on `Field`, the water-quality fields are on
`Irrigation Zone`, and the habitability and detector dates are on `Housing
Unit` — not in a compliance register beside them. The test is whether removing a
field breaks **operations** or only breaks **reporting**:

- Remove `last_spray_date` and the Worker Protection Standard report loses a
  line — *and* nobody can answer whether the re-entry interval on block 3 has
  run, which is a question a crew is waiting at the gate for.
- Remove `worker_hygiene_station_present` and an inspector loses a checkbox —
  *and* dispatch loses the fact that decides whether a crew may work that block
  at all.
- Remove a Housing Unit's condition and a habitability finding disappears —
  *and* `create_housing_assignment` stops refusing to put somebody in an
  uninhabitable cabin.
- Remove a Field's boundary and the spray record loses the thing an auditor
  checks a GPS fix against — *and* the geofence has no answer for a crew standing
  in the block.

Each of those has a test that asserts **both halves of the same removal**. A
separate "Field Compliance Log" that somebody fills in after the fact would fail
that test — nothing about picking would stop if it disappeared — which is why
this release does not have one.

The difference between a site holding four digits and a site holding nine is the
difference between an inconvenience and a notifiable breach. The full number
belongs on the signed W-9, on paper, in a drawer.

#### A quarter closes when it closes

`generate_quarterly_investment_report` refuses a quarter that is not ready, and
names everything that is missing in **one reply** rather than one per call. Four
things must be true:

1. the quarter has ended;
2. the custodian's statement is filed as a **Prior Statement** governance
   document with an effective date inside it;
3. no journal entry touching the investment accounts is still a draft;
4. no bank transaction in the period is unreconciled.

A report generated on a calendar date regardless of state is a report whose
numbers may be wrong, signed by somebody who assumed the schedule meant
something. `dry_run=true` runs every precondition and computes every figure
without writing.

It also invents nothing. Without `benchmark_rate_percent` the return over
benchmark and the performance fee are **not computed** and say so — they are not
zero and not estimated, because a performance fee against an assumed benchmark of
nothing overstates what the manager is owed. Holdings are the same: this app
reads one ERPNext site and the custodian's positions are not on it, so pass
`holdings` and the report reconciles the snapshot against the ledger, or omit it
and assets under management are the ledger balance, stated as such.

#### Where a generated document goes

Both generators file their output as a private File attached to a Governance
Document. That is the deliverable: permissioned, version-tracked, backed up with
the site, readable through `get_attachment_content`.

`output_path` is the second destination, for a batch of forms somebody has to
print. It is confined to the site's own `private/files` and `public/files` — a
relative path lands under the first — and everything else is refused, naming the
roots. The check is on the **resolved** real path, so a symlink cannot be used to
step outside, and an existing file is never overwritten unless `overwrite=true`.
A bad path refuses the whole run before the first write rather than leaving an
archive entry behind.

#### A fiscal year is a permission for a date

ERPNext refuses any posting whose date falls outside a Fiscal Year, and it
refuses it from *inside the document being saved* — so on a site whose only year
is 2026, booking a March 2025 equipment transfer fails with an error about a
date rather than about a missing year.

That makes `create_fiscal_year` the prerequisite for everything historical:
`set_opening_balance` and `create_journal_entry` cannot reach a period until the
year exists. Creating one books nothing and moves no balance; what changes is
which dates the ledger will accept.

A year with **no** `companies` applies to every company — that is how ERPNext
models a global fiscal year, and it is the default here. The overlap check
follows the same model: a global year collides with everything, two restricted
years collide only where they share a company. Disabling a year does not free its
range.

#### A note payable is not ERPNext's Loan

ERPNext's Loan module models the company as the **lender**: an application, a
disbursement, a repayment schedule, its own accounting. A holding company with
four notes outstanding is on the other side of all of it.

What a `Note Payable` record adds to the liability account that already exists is
three things the balance cannot tell you — the **terms** (rate, maturity,
frequency), the **provenance** (what was agreed, by whom, where the original is),
and **what it secures**. That last one is what lets
`depreciation_note_alignment_check` see whether a financed asset and its
financing still end in the same month.

`principal_outstanding` on a note is a **convenience figure** maintained by
`record_loan_payment`. The ledger's answer is the balance of the linked GL
account, and the two diverge by every payment recorded as a draft nobody has
posted — which, in an app where nothing submits, is the normal state. Every
response that reports the field says so.

#### Members are anonymous on purpose

Family names never go into the chart of accounts or the cost center tree. Those
are read by lenders, auditors and anyone handed an export, and a name that has
reached a statement cannot be taken out of it again.

So a posting is tagged with a **Member accounting dimension** value —
`Member-01` — and exactly one doctype, `Cap Table Entry`, says who that is. The
mapping can be granted to the people who need it without granting the ledger,
and the ledger can be read by everyone else without granting the mapping.

Cost centers stay what they are: segments of the *business*, not of the family.
A `Cap Table Entry` keeps an optional `member_cost_center` for sites whose
convention already gives each member one, but every tool files by the dimension.

```
Journal Entry line   →  account 3100 Member Capital, member = Member-01
Cap Table Entry      →  Member-01 = The Example Family Trust, admitted 2020-06-15, 60%
```

#### Depreciation is written here, not by ERPNext

An asset created by `create_asset` has ERPNext's `calculate_depreciation` set to
**0**, and that is deliberate. ERPNext runs a daily job that posts depreciation
for every asset with the flag set, through its own schedule and its own single
cost center. If it also ran, an asset would depreciate twice — silently, every
month. This app owns the schedule for the assets it creates, and
`run_depreciation_cycle` is the only thing that writes for them.

An asset you created in the Desk yourself is untouched by any of this: it has no
Asset Cost Profile, so these tools refuse it and ERPNext keeps depreciating it
exactly as before.

#### Building a chart from scratch

The four-step loop the chart tools are designed around, each step reviewable:

```
propose_clean_chart(company)                          → a proposal, nothing written
  ↓ delete what you do not want
import_chart_of_accounts(company, accounts)           → the full plan, nothing written
  ↓ read the plan
import_chart_of_accounts(company, accounts, dry_run=false)
  ↓
disable_account(...)                                  → retire the bundled defaults
```

Importing **adds** roots alongside whatever the company already has rather than
replacing them, because ERPNext will not let a root be edited or moved once it
exists. `propose_clean_chart` tells you which roots are already there and which
of the template's numbers are already taken, so you know before you run it.

Templates live in `erpnext_mcp/charts/` as plain Python literals with no
database dependency — which is what makes a proposal reviewable, diffable and
version-controllable before anything happens. The package auto-discovers them,
so adding `us_c_corp` is one file.

The one that ships is **`us_llc_farm`**: 81 accounts (17 groups, 64 ledgers) for
a US farming LLC that also runs an investment book. Deliberately compact — nine
flat operating-expense buckets rather than thirty-five, because a chart with a
line for every conceivable cost is one where nobody finds the right line. Three
things it does that a generic chart does not:

- **Crop labour is separate from administrative wages** — `5150 Direct Farm
  Labor` under COGS, `6100 Payroll & Benefits` under operating expenses, and
  `6150 Employer Payroll Tax Expense` split out again so wage cost and true cost
  of employment read apart.
- **The trading segment is its own range set** — assets `1800–1849`, income
  `4200–4249`, losses and costs `7300–7339`, unrealised movement `3500`. Filter
  a P&L to those and you have the investment book, running costs included;
  exclude them and you have the farm. No dashboard required, and a test fails if
  an account outside those ranges ever starts reading as trading.
- **`2120 Current Pay Period - Due to Employees` is a live balance**, not an
  accrual: updated continuously as work lands, flushed to zero at payroll. Its
  description says so, because the account only keeps that meaning if nobody
  books a month-end adjustment into it.

#### Adding an accounting dimension

An ERPNext accounting dimension does not hold its own values. It **points at a
DocType**, and every record of that DocType is a value — which is why setting one
up is three calls rather than one:

```
create_accounting_dimension(dimension_name="Member",
                            create_master_if_missing=true)   → the DocType, the
                                                               dimension, and a
                                                               Link field on each
                                                               target doctype
  ↓
create_dimension_value(dimension_name="Member",
                       value_name="Member-01")               → one value
  ↓
create_journal_entry(accounts=[
  {"account": "3100", "debit": 5000, "dimensions": {"member": "Member-01"}},
  ...
])
```

Two things worth knowing before the first call. **"Journal Entry" means the
line**: ERPNext carries dimensions on `Journal Entry Account`, never on the
header, because one entry books to several — the tool redirects and says so in
its response. And the dimension's **fieldname is the scrubbed label**, so
`Member` becomes `member` and `BBCH Stage` becomes `bbch_stage`; that is the key
you use in a line's `dimensions` object, and an unknown one is refused by name
rather than dropped.

---

## Compliance packets

A packet is an artefact rather than an answer: a structured JSON document for
somebody who has to sign something off. `generate_compliance_packet` returns one
inline — nothing is stored, emailed or filed.

```json
{"name": "generate_compliance_packet",
 "arguments": {"packet_type": "reconciliation_packet",
               "filters": {"account": "1100", "period_start": "2026-07-01",
                           "period_end": "2026-07-31"}}}
```

Three things make it more than a query:

- **It says how it was made.** `generated_at`, `generated_by`, `site`,
  `generator_version`, and `mcp_action_log_id` — the audit row for the very call
  that produced it. A figure can be traced back to a request, an IP and a user
  six months later.
- **It never truncates quietly.** Any collection that hits the cap raises a WARN
  naming how many rows were omitted. A packet that silently drops entries is a
  lie with provenance attached.
- **It reports what is wrong with itself.** `flags` carries INFO / WARN / ERROR
  findings, and `flag_summary.signable` is false whenever an ERROR is present.
  ERROR means the numbers do not internally agree — not merely that something is
  unusual.

| Packet type | Filters | What it contains |
| --- | --- | --- |
| `reconciliation_packet` | `account`, `period_start`, `period_end`, `company?` | Opening/closing balances, movement summary, every JE that touched the account, unposted drafts, cancelled entries, and a check that `opening + net == closing`. |
| `fiscal_year_audit_packet` | `company`, `fiscal_year` | Trial balance (each row stating its basis), income statement, balance sheet, top 20 entries, intercompany activity, document counts, and a check that `Assets - (Liabilities + Equity) = Income - Expense`. |

Each type has its own `allow_<packet_type>` switch, separate from the two tool
switches — so you can expose a reconciliation packet without exposing a payroll
one when that arrives.

**Adding a type is a single file drop** in `erpnext_mcp/packets/`. The package
auto-discovers every module that registers a `PacketSpec`; there is no list to
update and no handler to touch. Roadmap: payroll, organic-transition
attestations, tax-year summaries, SOX controls.

---

## Before you enable `advance_workflow`

It is the one write tool whose blast radius is decided by your site rather than
by the tool. A transition into a state with `doc_status: 1` **submits the
document** — on a Journal Entry that writes GL Entries and moves balances.

Run `list_workflows` first and check which target states carry `doc_status: 1`.
Then use the dry run:

```json
{"name": "advance_workflow",
 "arguments": {"doctype": "Purchase Order", "name": "PUR-ORD-2026-00184",
               "action": "Approve", "dry_run": true}}
```

```json
{"dry_run": true, "executed": false, "would_succeed": true,
 "current_state": "Pending Approval", "would_move_to": "Approved",
 "would_set_docstatus": 1, "would_submit": true,
 "effects": ["workflow_state: 'Pending Approval' → 'Approved'",
             "SUBMITS the document (target state has doc_status 1) — for a Journal Entry this writes GL Entries and moves balances"],
 "next_step": "Call again with dry_run=false to execute."}
```

The intended pattern is dry-run, show the human, execute. A dry run never raises
for an unavailable action — it reports `would_succeed: false` with the reason,
because "it would be refused" is the answer to the question.

One limit worth knowing: a dry run resolves the *transition*. It does not run the
document's own validation, so it cannot predict a submit that fails on a
mandatory field or a doctype hook.

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
| **Frappe** | v14, **v15**, v16 (`develop`) | Developed and run in production against **v15.115.0**. v15.100+ is the tested floor. The in-bench workflow suite builds a real Workflow on a synthetic DocType, so it verifies the framework contract on whichever version you run it against. |
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

## Seeding the related-party register

`Related Party` is the one doctype in this app whose useful content is a list of
people's names, so nothing is seeded for you and no names ship in this
repository. `scripts/seed_related_parties.py` reads a JSON file you keep outside
it:

```bash
python3 scripts/seed_related_parties.py --input parties.json --site yoursite.localhost
# read the plan, then:
python3 scripts/seed_related_parties.py --input parties.json --site yoursite.localhost --apply
```

It runs **outside** `bench execute`, so it configures Frappe itself: it finds the
bench's `sites` directory by looking for `common_site_config.json`, takes the
site from `--site` or `currentsite.txt`, and **creates the log directories before
`frappe.connect()`** rather than assuming they exist. Without `--apply` it writes
nothing. The whole plan is validated before the first insert — including the
four-digits-never-nine rule, refused before Frappe is even started — so a plan of
forty records is refused whole rather than half-applied.

Each record takes the same fields `create_related_party` does:

```json
[
  {"party_name": "A Person", "party_type": "Individual",
   "company": "Your Company", "relationship_to_company": "Manager",
   "effective_date": "2020-06-15", "tax_id_type": "SSN", "tax_id_last4": "6789"}
]
```

### Into a container

Both the script and the plan have to be copied in, in this order:

```bash
docker cp parties.json <container>:/home/frappe/frappe-bench/parties.json
docker cp scripts/seed_related_parties.py <container>:/home/frappe/frappe-bench/

docker exec -w /home/frappe/frappe-bench <container> \
    python3 seed_related_parties.py --input parties.json --site <site>

# read the plan, then re-run with --apply
docker exec -w /home/frappe/frappe-bench <container> \
    python3 seed_related_parties.py --input parties.json --site <site> --apply

# and take the names back out again
docker exec <container> rm -f /home/frappe/frappe-bench/parties.json
```

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

1536 standalone tests and 284 in-bench tests. The standalone suite installs an
in-memory `frappe` double so the refusal tests get run every time rather than
only when a bench is handy; the in-bench suite covers what only a real site can
prove and skips rather than fails when the site lacks the setup a case needs.
Details in **[docs/development.md](docs/development.md)**.

The document writers are tested against their own bytes rather than against a
mock of a library: the PDF tests open the file and check that every offset in the
cross-reference table points at the object it claims, which is the one failure
that would otherwise produce a report that is generated, attached, archived and
unopenable.

---

## Roadmap

Candidates for the next release, roughly in order of how often they come up:

- **More chart templates.** `us_c_corp`, `us_s_corp` and `us_partnership`, each
  a file drop against the framework `us_llc_farm` established. They differ from
  it almost entirely in the 3000s — which is exactly why entity type is in the
  template key rather than a flag on one chart.
- **Chart diffing.** "Show me how this company's chart differs from the
  template" is the question `propose_clean_chart` almost answers; the missing
  half is matching existing accounts to template ones by name and reporting the
  moves and renames that would reconcile them.

- **Auth.** Per-token scopes, so one client can be given the reconciliation
  tools and another the reporting tools without two sites. Token expiry and
  rotation without a manual button press. Optional mTLS for the LAN case.
- **More workflow depth.** Bulk actions over a filtered set, and workflow history
  (who moved what, when) from the Version table. The dry-run preview landed in
  v0.3.0.
- **More packet types.** `payroll_packet` (needs Frappe HR),
  `organic_transition_packet`, `tax_year_packet`, `sox_control_packet` — each a
  single file drop against the existing framework.
- **Packets that reach outside ERPNext.** `external_sources` is already in the
  payload shape, empty; Bank Bridge variance and its anchor chain go there.
- **Packet persistence.** v0.3.0 returns packets inline only. Storing one as a
  File against a document, with a hash, is what makes it evidence rather than a
  transcript.
- **Custom report authoring.** A mutating, default-off tool to create a Query
  Report from SQL an operator reviews first — the "I ask for a figure often
  enough that it should be a report" case.
- **Write coverage for the categories that only read today.** Nothing in HR or
  sales writes, on purpose; the useful additions are probably `create_todo`'s
  siblings (assign, close) rather than posting invoices.
- **Prepared-report support** in `run_report`, for reports too slow to run
  inline.
- **A Workspace**, so this app's doctypes appear in the app switcher instead of
  only via search.

- **Check printing**, and re-generating the PDF of a document that was
  archived as a `.docx`.
- **Deeper 1099 filing.** State 1099 filings and electronic filing to the IRS.
  What ships today stops at a pre-fill on purpose — the taxpayer id has to come
  off a signed W-9, and Copy A has to be the official scannable form.
- **Satellite imagery, on state rather than on a schedule.** The schema is here:
  provider, asset reference, last pull date, NDVI mean and standard deviation.
  The pull itself is not, and when it lands it should fire when a boundary
  exists AND the last pull is stale AND the block is in an active crop cycle —
  not on a calendar tick, which would burn imagery credits on a fallow block in
  January.
- **Geofence enforcement.** `find_fields_containing_point` answers the question;
  nothing yet *acts* on the answer. Wiring it into a bucket log ("is this pick
  inside an assigned block?") or a time clock ("is this worker on ground they
  are rostered to?") is the other half.
- **Parcel boundaries.** Fields and zones have polygons; parcels do not, so
  nothing checks that a block sits inside the ground it is recorded against.
  `set_field_boundary` says so in a warning rather than leaving it a silent gap.
- **The Farm App sync engine.** v0.12.0 ships `import_farm_app_fields`, which
  aligns the schemas by carrying each legacy record's id onto the ERPNext Field
  it creates. The engine that keeps the two in step afterwards — reading
  structure out, pushing per-zone cost and revenue events back — is the next
  half, and it needs the ids this release establishes.
- **The compliance framework proper.** The metadata is already woven into the
  operational doctypes; what is missing is the external evidence that does not
  emerge from operations — Compliance Policy, Certification/License, Regulatory
  Filing, Audit Event — and the engines that read both: a due-date calendar that
  fires on **state** rather than on a calendar tick, an audit packet generator,
  and a readiness dashboard.
- **Access control for the camp.** `Housing Unit.access_card_zone` is recorded
  now and read by nothing. Wiring it to a real card system is what turns the
  register into a door that opens.
- **Real estate for the operating company.** The land register is per-entity by
  construction; standing up a second company's parcels is data entry, not code —
  and since v0.12.0, `create_company` can stand up the company too.

Issues and PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Uninstall

```bash
bench --site yoursite.localhost uninstall-app erpnext_mcp
```

Drops this app's own doctypes and everything in them: **ERPNext MCP Settings**,
**MCP Action Log** (the audit history), **Cap Table Entry**, **Member Event**,
**Governance Document**, **Asset Cost Profile**, **Note Payable**, **Parcel**,
**Lease**, **Related Party**, **Family**, **Field**, **Irrigation Zone**,
**Housing Unit** and **Housing Assignment**. The last two are worth a pause: a camp roster is the
only record of who slept where, and it is what an IRS Section 119 exclusion and
an ORS 653 wage claim are both answered with. Nothing else on the site is
touched — the
accounts, cost centers, dimensions, journal entries, bank accounts and Assets
these tools created are ordinary ERPNext records and stay exactly as they are.

`before_uninstall` prints a row count and an export command for each doctype
that has anything in it, because most of them are the **only** copy: the cap
table is the only mapping from a member id to a legal name, the event trail the
only record of why an equity entry exists, a governance document may hold the
only digital copy of an agreement, a note payable is the only place a debt's
terms and provenance are written down, the parcel register holds appraised
values and the dates they were appraised as of, a lease holds rent terms that
exist in no other digital form, and the related-party register is the source for
a related-party disclosure on a return. The ledger has the balances and nothing
else. Export before you uninstall.

---

## License

MIT. See [LICENSE](LICENSE).
