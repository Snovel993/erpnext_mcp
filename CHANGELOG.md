# Changelog

All notable changes to this project are documented here. Versions follow
[semantic versioning](https://semver.org).

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
