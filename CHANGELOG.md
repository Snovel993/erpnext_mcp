# Changelog

## 0.1.0 — 2026-07-24

First release.

An MCP server that installs into any Frappe/ERPNext bench as a custom app. One
whitelisted endpoint, two doctypes, no hooks that change existing behaviour.

**Tools — 15, in `erpnext_mcp/registry.py`.**

Read-only, all on by default and individually switchable:
`get_company_topology`, `get_account_balance`, `get_journal_entries`,
`get_journal_entry`, `list_bank_transactions`, `get_bank_statement`,
`list_fiscal_years`, `get_chart_of_accounts`,
`list_unreconciled_bank_transactions`, `search_accounts`.

Mutating, all **off** by default:
`create_journal_entry` (draft only), `submit_journal_entry`,
`cancel_journal_entry`, `create_bank_transaction` (draft only),
`reconcile_bank_transaction`.

**Security.** Master switch (off ⇒ 404), bearer token in a Password field
(constant-time compare), CIDR allowlist defaulting to loopback plus RFC1918.
Rejections are opaque to the caller and specific in the audit log. The CIDR gate
reads the rightmost `X-Forwarded-For` hop, the one a client cannot forge.

**Audit.** New `MCP Action Log` doctype records every call — reads, writes,
refusals and unknown tools — append-only, with a failure row committed after the
failed work is rolled back so the attempt is recorded even though it did not
happen.

**Compatibility.** Frappe/ERPNext v14–v16, Python 3.10+. Field and doctype presence
is read from the site's own schema rather than pinned, so Bank Transaction's
`deposit`/`withdrawal` vs signed `amount`, the presence of `unallocated_amount`,
Company's cost-centre fieldname and the existence of Bank Statement are all handled
without branching on a version number.

**Tests.** 228 standalone tests (`python3 -m unittest discover -s tests_standalone -t .`,
no bench required) plus an in-bench `FrappeTestCase` suite
(`bench run-tests --app erpnext_mcp`) covering migration, encryption, real ERPNext
validation and permission enforcement.
