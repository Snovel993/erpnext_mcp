# erpnext_mcp

A [Model Context Protocol](https://modelcontextprotocol.io) server that ships as
a Frappe app. Install it into any Frappe/ERPNext bench and an MCP client — Claude
Desktop, Claude Code, or anything else that speaks the protocol — can read your
ledger, and, only behind per-tool switches you turn on by hand, post to it.

Nothing in this app is specific to one install. Company names, account numbers,
fiscal years and the Bank Transaction schema are all discovered from your site at
call time.

- **15 tools** — 10 read-only, 5 mutating.
- **Every mutating tool ships OFF.** A fresh install cannot change a single
  document until you tick a box.
- **Every call is audited**, reads included, in a new append-only doctype.
- **LAN-only by default.** Bearer token *and* a CIDR allowlist.
- MIT licensed. No runtime dependencies beyond Frappe/ERPNext.

---

## Why would I install this?

Because the alternative is you, in a terminal, being an API.

If you have ever spent an afternoon relaying `bench console` snippets to somebody
— or to a model — so it could tell you which bank transactions did not reconcile,
you have paid for the absence of this app. The AI can reason about a general
ledger perfectly well. It just cannot see one, so a human ends up hand-carrying
every query and every result, and the loop runs at the speed of copy-paste.

This closes that loop without giving anything away:

- **It cannot write by default.** All five mutating tools are off on install.
  Turning one on is a checkbox on a form only System Manager can open.
- **The dangerous verb is a separate switch.** `create_journal_entry` only ever
  produces a draft — `docstatus=0`, affecting no balance. Posting is
  `submit_journal_entry`, a different tool with a different switch. Enable the
  first and not the second and you have given a model a scratchpad in the general
  ledger, which is what you actually wanted.
- **Nothing bypasses ERPNext.** Every write goes through `frappe.get_doc().insert()`
  / `.submit()` / `.cancel()`, so doctype validation, fiscal-year checks,
  period-closing vouchers, account freezing and every `on_submit` hook run exactly
  as they do for a human in the UI. There is no raw SQL in this app.
- **It is one whitelisted method.** No second listener, no sidecar process, no new
  port. Your existing nginx, TLS and access logs already cover it, and the server
  is up whenever the site is.
- **Installing it changes nothing else.** No `doc_events`, no scheduler jobs, no
  overrides, no fixtures. It adds two doctypes and one endpoint. Uninstall and
  your site is exactly as it was.

---

## Install

Tested against Frappe/ERPNext v14, v15 and v16 (`develop`). Requires Python 3.10+
and an ERPNext install — `required_apps` in `hooks.py` means `bench install-app`
will refuse on a Frappe-only site rather than fail later at the first tool call.

```bash
cd ~/frappe-bench

# 1. Fetch the app. A local path works; so does a git URL.
bench get-app /path/to/erpnext_mcp
# or: bench get-app https://github.com/tpolehn/erpnext_mcp

# 2. Install it on your site.
bench --site yoursite.localhost install-app erpnext_mcp

# 3. Migrate and restart.
bench --site yoursite.localhost migrate
bench restart          # in development, `bench start` picks it up on its own
```

`install-app` syncs the two doctypes and seeds their defaults, so after step 3 the
read tools are already switched on — but the server itself is **off**, because no
token exists and `enabled` is unticked. It stays inert until you do the next
part.

### Turn it on

1. In the Desk, open **ERPNext MCP Settings** (search it, or go to
   `/app/erpnext-mcp-settings`).
2. Click **Generate New Token**. Copy the token from the dialog — it is stored
   encrypted and shown exactly once.
3. Check **Allowed CIDRs**. It defaults to loopback plus the RFC1918 private
   ranges; narrow it to the subnet your client is actually on.
4. Optionally set an **MCP System User** (see [Attribution](#attribution) below).
5. Tick **Enabled** and save.
6. Click **Test Configuration** to confirm the server reports itself ready.

Leave every switch in the **Mutating Tools** section alone until you have a reason.

### Smoke test

```bash
TOKEN=paste-your-token-here
SITE=http://localhost:8000     # your site's URL

curl -sS -X POST "$SITE/api/method/erpnext_mcp.mcp.handle" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | python3 -m json.tool
```

You should get ten tools back. Then ask it to describe your site:

```bash
curl -sS -X POST "$SITE/api/method/erpnext_mcp.mcp.handle" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"get_company_topology","arguments":{}}}' \
  | python3 -m json.tool
```

If you get a `404`, either `enabled` is off or no token is stored — those two are
deliberately indistinguishable from the app not being installed. A `401` means
the token is wrong; a `403` means your address is outside **Allowed CIDRs**. The
real reason for any rejection is written to **MCP Action Log**, where you can read
it and a stranger cannot.

---

## Connect a client

The endpoint is MCP Streamable HTTP (JSON-RPC 2.0 over `POST`). It is POST-only:
this server never initiates a message, so there is no SSE stream to open, and a
`GET` returns a 405 that says so.

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
        "Authorization: Bearer YOUR_TOKEN_HERE"
      ]
    }
  }
}
```

Replace `erp.example.com` with your site and `YOUR_TOKEN_HERE` with the generated
token. Two notes:

- **Plain HTTP on the LAN** — add `"--allow-http"` to `args`. `mcp-remote` refuses
  non-HTTPS origins otherwise.
- **Keeping the token out of the config file** — pass the header value from the
  environment instead:
  ```json
  "args": ["-y", "mcp-remote", "https://erp.example.com/api/method/erpnext_mcp.mcp.handle",
           "--transport", "http-only",
           "--header", "Authorization: Bearer ${ERPNEXT_MCP_TOKEN}"],
  "env": { "ERPNEXT_MCP_TOKEN": "YOUR_TOKEN_HERE" }
  ```
  The whole header must stay one argument — the space after `Bearer` is part of
  the value, not a separator.

Restart Claude Desktop. The tools appear under the server name `erpnext`.

### Claude Code

One command, no bridge — Claude Code speaks HTTP MCP directly:

```bash
claude mcp add --transport http erpnext \
  https://erp.example.com/api/method/erpnext_mcp.mcp.handle \
  --header "Authorization: Bearer YOUR_TOKEN_HERE"
```

### Anything else

Point your client at
`POST https://<your-site>/api/method/erpnext_mcp.mcp.handle` with
`Authorization: Bearer <token>`. The server negotiates protocol versions
`2025-06-18`, `2025-03-26` and `2024-11-05`, echoing back whichever the client
asks for.

If your Frappe version ever starts rejecting unknown `Bearer` values in its own
OAuth layer, the header `X-MCP-Token: <token>` is accepted as an equivalent. See
[docs/security.md](docs/security.md).

---

## What it exposes

Full arguments, return shapes and examples: **[docs/tool-catalog.md](docs/tool-catalog.md)**.

### Read-only — all ON by default, each individually switchable

| Tool | What it answers |
| --- | --- |
| `get_company_topology` | What is this ERPNext install? Companies, abbreviations, currencies, default cost centers, fiscal years, chart-of-accounts roots, which optional banking doctypes exist. **Call this first.** |
| `get_account_balance` | Balance of one account as of a date, summed from GL Entry excluding cancelled rows, in both raw and natural sign convention. |
| `get_journal_entries` | Journal Entry headers by date range, company, account-on-any-line and docstatus. |
| `get_journal_entry` | One Journal Entry in full, every line with party, cost center and reference. |
| `list_bank_transactions` | Bank Transactions by account, date range and status, amounts normalised to one signed number. |
| `get_bank_statement` | One Bank Statement, on versions that have the doctype. Degrades politely where it does not. |
| `list_fiscal_years` | Fiscal years and the companies they apply to — check before choosing a `posting_date`. |
| `get_chart_of_accounts` | The chart as a nested tree, optionally one root type. |
| `list_unreconciled_bank_transactions` | The reconciliation worklist: transactions with money still unallocated. |
| `search_accounts` | Turn "cash clearing" into a docname. Ranked best-first. |

### Mutating — all OFF by default

| Tool | What it does | What it cannot do |
| --- | --- | --- |
| `create_journal_entry` | Creates a **draft** JE. Refuses unbalanced entries, negative amounts, group accounts and single-line entries. | Submit. There is no argument for it. |
| `submit_journal_entry` | Submits an existing draft, `0 → 1`. Writes GL Entries. **This moves balances.** | Create anything — it takes a name and nothing else. |
| `cancel_journal_entry` | Cancels a submitted JE, `1 → 2`, writing reversing entries. `reason` is mandatory and recorded on the document and in the audit log. | Delete anything. |
| `create_bank_transaction` | Inserts a **draft** Bank Transaction. Signed amount, mapped onto this version's columns. | Submit it. Drafts are not reconcilable; that step stays human. |
| `reconcile_bank_transaction` | Attaches payment vouchers, refusing to over-allocate. Delegates to ERPNext's own reconciliation method where the version has it. | Allocate more than the transaction's remaining amount. |

---

## Security posture

Full threat model: **[docs/security.md](docs/security.md)**. The short version:

- **Three gates, all mandatory.** Master switch (off ⇒ 404), bearer token
  (constant-time compare; missing or wrong ⇒ 401), CIDR allowlist (outside ⇒ 403).
  An empty allowlist denies everyone rather than allowing everyone.
- **Rejections are opaque.** Every refusal says the same thing. Which gate failed
  goes to the audit log, not to the caller — telling an unauthenticated client
  "your IP is fine, your token is wrong" hands it a free oracle.
- **The token is a Password field.** Encrypted at rest, never logged, never
  returned by any tool, shown to the operator exactly once at generation.
- **Not for the internet.** This is a LAN-facing endpoint behind your existing
  reverse proxy. The default allowlist is loopback plus RFC1918, and widening it
  to `0.0.0.0/0` is something you have to type yourself.
- **The bearer token is the read authorization.** Read tools use
  `frappe.db.get_all`, which does not consult Frappe role permissions. A token
  holder can read everything the enabled read tools cover. Per-tool switches, not
  roles, are the granularity on offer — see docs/security.md.
- **To stop everything right now:** untick **Enabled** and save. Next request is a
  404. No restart, no token rotation.

### Attribution

With **Attribute Actions To An MCP System User** on (the default), set an **MCP
System User** and every document a mutating tool creates carries that `owner`, and
every permission check its `.insert()`/`.submit()` runs is that user's. Create a
dedicated user — `mcp@yourcompany.example` — with only the roles the tools you
enabled need; **Accounts User** is normally enough.

Leave the user blank, or turn the checkbox off, and mutations run as
`Administrator`. That works, and it makes MCP-authored documents indistinguishable
from an admin's own. The first option is better and is why it is the default.

---

## Audit log

Every call gets a row in **MCP Action Log** (`/app/mcp-action-log`) — reads
included, refusals included. The interesting question after the fact is rarely
"what did it change", it is "what did it see", and a log that only records
mutations cannot answer that.

| Field | Example |
| --- | --- |
| `timestamp` | `2026-07-24 14:31:07.482119` |
| `tool_name` | `submit_journal_entry` |
| `caller_ip` | `10.0.4.22` |
| `arguments_json` | `{"name": "ACC-JV-2026-00184"}` |
| `result_status` | `Success` \| `Error` \| `Blocked` \| `Unauthorized` |
| `result_summary` | `submitted Journal Entry ACC-JV-2026-00184 (Example Trading Co, 2026-07-24, 1450.0)` |
| `docstatus_delta` | `0 → 1 (submitted)` |

Rows are **append-only**: the doctype grants System Manager read and delete but
not write, and the controller refuses an update even from a script. Delete is
allowed on purpose so a busy site can be pruned — Frappe records every deletion in
its own Deleted Document doctype, so a pruned row still leaves a trace.

A row written by a *failed* mutation is committed on its own, after the failed
work is rolled back, so the record of the attempt survives even though the attempt
did not.

**Uninstalling drops this table.** Export it first if you need to keep it; the
`before_uninstall` hook will remind you.

---

## Compatibility

This app asks the site what it has instead of pinning a version — see
`erpnext_mcp/compat.py`. Concretely:

- **Bank Transaction amounts** — `deposit`/`withdrawal` on newer ERPNext, a single
  signed `amount` on older. Every tool reports one normalised `amount_signed`
  (positive in, negative out) and says which layout it found.
- **Unallocated amounts** — uses the `unallocated_amount` column when the site has
  one, computes `gross - allocated` when it does not, and tells you which.
- **Company default cost center** — `cost_center` or `default_cost_center`, or
  `null` on a Company created before its chart of accounts.
- **Bank Statement** — absent on older ERPNext. `get_company_topology` reports
  whether this site has it, and `get_bank_statement` says so in words rather than
  raising a schema error.
- **Fields that do not exist are never selected.** Selecting a missing column is a
  hard SQL error, which is exactly the failure mode a reusable app must not have.

---

## Tests

Two suites, for two different kinds of fact.

```bash
# Logic: refusals, arithmetic, switch handling, protocol shape.
# No bench, no database, no dependencies beyond the standard library.
python3 -m unittest discover -s tests_standalone -t .

# Framework contract: migration, real encryption, real ERPNext validation,
# real permission enforcement.
bench --site yoursite.localhost run-tests --app erpnext_mcp
```

The standalone suite installs an in-memory `frappe` double and runs in a fraction
of a second, so the refusal tests get run every time rather than only when a bench
is handy. The in-bench suite covers what only a real site can prove, and skips
rather than fails when the site lacks the accounting setup a case needs. Details
in **[docs/development.md](docs/development.md)**.

---

## Uninstall

```bash
bench --site yoursite.localhost uninstall-app erpnext_mcp
```

This drops **ERPNext MCP Settings** and **MCP Action Log**, including the audit
history. Nothing else on the site is touched — the app installs no hooks,
overrides or fixtures to unwind.

---

## License

MIT. See [LICENSE](LICENSE).
