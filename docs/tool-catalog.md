# Tool catalogue

All 350 tools `erpnext_mcp` exposes, with arguments, return shape and a worked
example. The authoritative definitions live in `erpnext_mcp/registry.py`; this
document explains them.

All examples use the site `erp.example.com`, the company `Example Trading Co`
(abbreviation `ETC`) and a textbook chart of accounts. Nothing here is real.

## Conventions that apply to every tool

**Calling.** One `POST` per call, JSON-RPC 2.0:

```bash
curl -sS -X POST https://erp.example.com/api/method/erpnext_mcp.mcp.handle \
  -H 'Content-Type: application/json' \
  -H "X-MCP-Token: $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"<tool>","arguments":{...}}}'
```

The result is an MCP tool result — the payload is JSON *inside* a text content
block, which is what MCP specifies:

```json
{"jsonrpc": "2.0", "id": 1,
 "result": {"content": [{"type": "text", "text": "{ ...the payload... }"}],
            "isError": false}}
```

**`company` is usually optional.** On a single-company site it is inferred. On a
multi-company site, omitting it raises an error that lists your companies rather
than guessing one. A company **abbreviation** is accepted wherever a name is.

**Accounts resolve three ways.** Anywhere an `account` is taken, you may pass the
docname (`1100 - Cash - ETC`), the account number (`1100`), or the exact account
name (`Cash`). Ambiguity is reported with the candidates listed — it is never
resolved by guessing. `search_accounts` exists to turn a description into a
docname.

**Dates** are `YYYY-MM-DD`. Loose forms like `2026-1-5` are normalised rather than
rejected; anything unparseable gets an error naming the expected format.

**`limit`** defaults to 100 and is hard-capped at 500. A larger value is clamped,
not refused, and the response carries `limit` and `truncated` so a client knows it
did not see everything.

**Some tools are not on every site.** A tool with an unmet site prerequisite —
no `hrms`, no Bank Statement doctype — is not advertised in `tools/list` and
cannot be called. That is different from an operator switching a tool off, and
the refusals say so differently: *"not available on this site: it requires the
Frappe HR (hrms) app... This is not something an operator can switch on here"*
versus *"switched off on this site. An operator must tick allow_&lt;tool&gt;"*.
Which tools carry a prerequisite is noted below.

**Errors are tool results, not JSON-RPC errors.** A bad argument or a missing
document comes back as `isError: true` with a message written to be acted on. A
JSON-RPC `error` object means the *call* was malformed — wrong method, non-object
params — which is a different problem.

**Balance signs.** Every balance is reported twice:

- `balance` — raw ledger convention, `debit - credit`.
- `balance_natural` — sign-flipped for `Liability`, `Income` and `Equity`, so an
  account with a normal credit balance reads positive.

The payload carries `sign_convention` spelling this out, because "the sales
account is at -84,000" is the single most reliable way for a model to misread a
ledger.

---

# Read-only tools

All 162 read tools are **on** by default and can be switched off individually. A
tool that is off does not appear in `tools/list` at all, and neither does one
whose site prerequisite is missing.

The first ten are the accounting surface v0.1.0 shipped; the rest arrived with
later releases and are grouped by what they touch. The numbered sections below
are catalogue order, which interleaves reads and writes by subject — the reads
of the v0.15.0 compliance framework are under Waves 2–4, and the v0.16.0
dispatch surface is under Wave 5.

---

## 1. `get_company_topology`

What kind of ERPNext install is this? Call it first — every other tool takes a
company, account or fiscal year whose names only exist on your site.

**Arguments:** none.

**Returns**

| Field | Meaning |
| --- | --- |
| `companies[]` | One entry per Company |
| `companies[].name`, `.abbr`, `.default_currency`, `.country`, `.chart_of_accounts`, `.parent_company`, `.is_group` | Company header fields present on this site |
| `companies[].default_cost_center` | Under whichever fieldname this version uses; `null` on a Company created before its chart of accounts |
| `companies[].fiscal_years[]` | Fiscal years applying to this company, including company-agnostic ones |
| `companies[].root_accounts[]` | Root accounts with `root_type` |
| `companies[].root_types[]` | Distinct root types present |
| `companies[].account_count` | Accounts belonging to this company |
| `count` | Number of companies |
| `site` | The Frappe site name |
| `optional_doctypes` | `{"Bank Transaction": true, "Bank Statement": false, …}` — check here before calling a tool that needs one |

**Example**

```json
{"name": "get_company_topology", "arguments": {}}
```

```json
{
  "companies": [
    {
      "name": "Example Trading Co",
      "abbr": "ETC",
      "default_currency": "USD",
      "country": "United States",
      "chart_of_accounts": "Standard",
      "is_group": 0,
      "default_cost_center": "Main - ETC",
      "fiscal_years": [
        {"name": "2026", "year_start_date": "2026-01-01", "year_end_date": "2026-12-31", "disabled": 0}
      ],
      "root_accounts": [
        {"name": "Application of Funds (Assets) - ETC", "account_name": "Application of Funds (Assets)", "root_type": "Asset", "is_group": 1}
      ],
      "root_types": ["Asset", "Equity", "Expense", "Income", "Liability"],
      "account_count": 84
    }
  ],
  "count": 1,
  "site": "erp.example.com",
  "optional_doctypes": {"Bank Transaction": true, "Bank Statement": false, "Bank Account": true, "Bank": true}
}
```

---

## 2. `get_account_balance`

Balance of one account as of a date, summed from GL Entry with cancelled entries
excluded — so it matches what ERPNext's own General Ledger report prints.

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `account` | yes | Docname, number or exact name |
| `as_of` | no | `YYYY-MM-DD`; defaults to today |
| `company` | no | Narrows resolution; required if the account name is ambiguous |

**Returns** `account`, `account_name`, `account_number`, `company`, `currency`,
`root_type`, `account_type`, `is_group`, `as_of`, `total_debit`, `total_credit`,
`gl_entry_count`, `balance`, `balance_natural`, `sign_convention`. Group accounts
additionally get a `note` explaining that GL Entries post to leaves.

**Example**

```json
{"name": "get_account_balance",
 "arguments": {"account": "1100", "company": "Example Trading Co", "as_of": "2026-06-30"}}
```

```json
{
  "account": "1100 - Cash - ETC",
  "account_name": "Cash",
  "account_number": "1100",
  "company": "Example Trading Co",
  "currency": "USD",
  "root_type": "Asset",
  "account_type": "Cash",
  "is_group": false,
  "as_of": "2026-06-30",
  "total_debit": 41200.0,
  "total_credit": 33875.5,
  "gl_entry_count": 218,
  "balance": 7324.5,
  "balance_natural": 7324.5,
  "sign_convention": "balance = debit - credit (raw ledger). balance_natural flips the sign for Liability/Income/Equity so a normal balance reads positive."
}
```

For an Income account the same call returns `"balance": -84000.0` and
`"balance_natural": 84000.0`.

---

## 3. `get_journal_entries`

Journal Entry **headers** in a date range, newest first. Headers only — a month of
entries with every line expanded is a lot of tokens for a question that is usually
"which one was it".

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `from_date` | yes | Inclusive |
| `to_date` | yes | Inclusive; must not precede `from_date` |
| `company` | no | |
| `account` | no | Entries with this account on **any** line |
| `docstatus` | no | `0`/`"draft"`, `1`/`"submitted"`, `2`/`"cancelled"`. Omit for all |
| `limit` | no | Default 100, max 500 |

**Returns** `journal_entries[]` (each with `name`, `posting_date`, `company`,
`voucher_type`, `total_debit`, `total_credit`, `user_remark`, `cheque_no`,
`cheque_date`, `bill_no`, `docstatus`, `docstatus_label`, `owner`, `creation`),
plus `count`, `limit`, `truncated` and the `filters` that were applied.

**Example**

```json
{"name": "get_journal_entries",
 "arguments": {"from_date": "2026-06-01", "to_date": "2026-06-30",
               "account": "1190", "docstatus": "submitted", "limit": 50}}
```

```json
{
  "journal_entries": [
    {"name": "ACC-JV-2026-00184", "posting_date": "2026-06-28",
     "company": "Example Trading Co", "voucher_type": "Bank Entry",
     "total_debit": 1450.0, "total_credit": 1450.0,
     "user_remark": "June clearing sweep", "docstatus": 1,
     "docstatus_label": "submitted", "owner": "mcp@example.test"}
  ],
  "count": 1,
  "limit": 50,
  "truncated": false,
  "filters": {"from_date": "2026-06-01", "to_date": "2026-06-30",
              "company": "Example Trading Co", "account": "1190", "docstatus": 1}
}
```

---

## 4. `get_journal_entry`

One entry in full.

**Arguments:** `name` (required) — the Journal Entry docname.

**Returns** every header field this site has, `docstatus_label`, `balanced` (a
boolean, computed), and `accounts[]` with `idx`, `account`, `party_type`, `party`,
`debit`, `credit`, account-currency amounts, `exchange_rate`, `against_account`,
`cost_center`, `project`, `reference_type`, `reference_name`, `user_remark`.

**Example**

```json
{"name": "get_journal_entry", "arguments": {"name": "ACC-JV-2026-00184"}}
```

```json
{
  "name": "ACC-JV-2026-00184",
  "posting_date": "2026-06-28",
  "company": "Example Trading Co",
  "voucher_type": "Bank Entry",
  "total_debit": 1450.0,
  "total_credit": 1450.0,
  "user_remark": "June clearing sweep",
  "docstatus": 1,
  "docstatus_label": "submitted",
  "balanced": true,
  "accounts": [
    {"idx": 1, "account": "1110 - Bank Checking - ETC", "debit": 1450.0, "credit": 0.0,
     "cost_center": "Main - ETC"},
    {"idx": 2, "account": "1190 - Cash Clearing - ETC", "debit": 0.0, "credit": 1450.0,
     "cost_center": "Main - ETC"}
  ]
}
```

---

## 5. `list_bank_transactions`

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `bank_account` | no | Docname or its `account_name`. Omit for all accounts |
| `from_date` | no | Either bound may be given alone |
| `to_date` | no | |
| `status` | no | As this site spells it: `Pending`, `Settled`, `Reconciled`, `Unreconciled` |
| `limit` | no | Default 100, max 500 |

**Returns** `bank_transactions[]` — every field this version has, plus
`amount_signed` (positive money in, negative money out) — with `count`, `limit`,
`truncated`, `amount_layout` (`deposit_withdrawal` or `signed_amount`),
`sign_convention` and `filters`.

**Example**

```json
{"name": "list_bank_transactions",
 "arguments": {"bank_account": "Operating", "from_date": "2026-06-01",
               "to_date": "2026-06-30", "status": "Unreconciled"}}
```

```json
{
  "bank_transactions": [
    {"name": "BT-2026-00412", "date": "2026-06-14",
     "bank_account": "Operating - Example Bank", "company": "Example Trading Co",
     "description": "ACH CREDIT CUSTOMER PMT", "status": "Unreconciled",
     "reference_number": "A7741208", "currency": "USD",
     "deposit": 2400.0, "withdrawal": 0.0,
     "allocated_amount": 0.0, "unallocated_amount": 2400.0,
     "docstatus": 1, "amount_signed": 2400.0}
  ],
  "count": 1,
  "limit": 100,
  "truncated": false,
  "amount_layout": "deposit_withdrawal",
  "sign_convention": "amount_signed is positive for money in, negative for money out.",
  "filters": {"bank_account": "Operating - Example Bank", "from_date": "2026-06-01",
              "to_date": "2026-06-30", "status": "Unreconciled"}
}
```

---

## 6. `get_bank_statement`

**Arguments:** `name` (required).

**Returns** every scalar field the doctype has on this site, plus `child_tables`
keyed by fieldname. Mirrors whatever is there rather than naming columns, because
the field set has changed across versions.

**Not present on every site.** Bank Statement shipped later than Bank Transaction.
Where it is absent this tool returns an error saying so and pointing at
`get_company_topology`, which reports the doctype's presence up front:

```
the 'Bank Statement' DocType is not installed on this site. It is only present on
ERPNext versions that ship the Bank Statement doctype; get_company_topology
reports whether this site has it.
```

---

## 7. `list_fiscal_years`

Worth calling before you choose a `posting_date`: ERPNext rejects dates outside a
fiscal year, which is otherwise a confusing failure.

**Arguments:** `company` (optional).

**Returns** `fiscal_years[]` (`name`, `year_start_date`, `year_end_date`,
`disabled`), newest first; `count`; `company`; `company_agnostic_years[]` — years
with no company links, which apply to every company; and a `note` saying so.

**Example**

```json
{"name": "list_fiscal_years", "arguments": {"company": "ETC"}}
```

```json
{
  "fiscal_years": [
    {"name": "2026", "year_start_date": "2026-01-01", "year_end_date": "2026-12-31", "disabled": 0},
    {"name": "2025", "year_start_date": "2025-01-01", "year_end_date": "2025-12-31", "disabled": 0}
  ],
  "count": 2,
  "company": "Example Trading Co",
  "company_agnostic_years": ["2025"],
  "note": "A Fiscal Year with no company links applies to every company; those are listed in company_agnostic_years."
}
```

---

## 8. `get_chart_of_accounts`

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `company` | yes | |
| `root_type` | no | One of `Asset`, `Liability`, `Income`, `Expense`, `Equity` |

**Returns** `accounts[]` — a nested tree, each node with `account_name`,
`account_number`, `parent_account`, `is_group`, `root_type`, `account_type`,
`account_currency`, `disabled` and `children[]` — plus `company`, `root_type`,
`flat_count` and a `note`.

Filtering by `root_type` can cut a group out from above a node it kept. Those
nodes surface at the top level of the response rather than disappearing.

**Example**

```json
{"name": "get_chart_of_accounts", "arguments": {"company": "ETC", "root_type": "Income"}}
```

```json
{
  "company": "Example Trading Co",
  "root_type": "Income",
  "accounts": [
    {"name": "Income - ETC", "account_name": "Income", "is_group": 1, "root_type": "Income",
     "children": [
       {"name": "4100 - Sales - ETC", "account_name": "Sales", "account_number": "4100",
        "is_group": 0, "root_type": "Income", "children": []}
     ]}
  ],
  "flat_count": 2,
  "note": "children[] is nested; flat_count is every account in the response."
}
```

---

## 9. `list_unreconciled_bank_transactions`

The reconciliation worklist for one bank account, oldest first.

**Arguments:** `bank_account` (required), `limit` (optional).

**Returns** `unreconciled[]` — each row with `amount_signed`, `gross_amount`,
`allocated_amount_effective` and `unallocated_amount_effective` — plus `count`,
`bank_account`, `limit`, `truncated`, `amount_layout`, and `unallocated_source`
(`column` where the site has an `unallocated_amount` field, `computed (gross -
allocated)` where it does not).

**Example**

```json
{"name": "list_unreconciled_bank_transactions",
 "arguments": {"bank_account": "Operating", "limit": 25}}
```

```json
{
  "unreconciled": [
    {"name": "BT-2026-00389", "date": "2026-06-03",
     "bank_account": "Operating - Example Bank", "description": "CHECK 1042",
     "status": "Unreconciled", "deposit": 0.0, "withdrawal": 812.4,
     "amount_signed": -812.4, "gross_amount": 812.4,
     "allocated_amount_effective": 0.0, "unallocated_amount_effective": 812.4}
  ],
  "count": 1,
  "bank_account": "Operating - Example Bank",
  "limit": 25,
  "truncated": false,
  "amount_layout": "deposit_withdrawal",
  "unallocated_source": "column"
}
```

---

## 10. `search_accounts`

The tool that saves a round trip. Ranked exact-number → exact-name → prefix →
substring, so the top hit is usually right.

**Arguments:** `query` (required), `company` (optional), `limit` (optional).

**Returns** `matches[]` (best first), `query`, `company`, `count`,
`total_before_limit`, `note`.

**Example**

```json
{"name": "search_accounts", "arguments": {"query": "cash", "company": "ETC"}}
```

```json
{
  "query": "cash",
  "company": "Example Trading Co",
  "matches": [
    {"name": "1100 - Cash - ETC", "account_name": "Cash", "account_number": "1100",
     "root_type": "Asset", "account_type": "Cash", "is_group": 0, "disabled": 0},
    {"name": "1190 - Cash Clearing - ETC", "account_name": "Cash Clearing",
     "account_number": "1190", "root_type": "Asset", "account_type": "Bank",
     "is_group": 0, "disabled": 0}
  ],
  "count": 2,
  "total_before_limit": 2,
  "note": "Ranked best-first: exact number, exact name, prefix, substring."
}
```
---

# Workflow tools

Four read tools and one write tool over Frappe's Workflow engine. All read-only
unless marked.

The hard part of a workflow question is not "what are the states" — it is
"can *this* user take *this* action on *this* document *right now*", which
depends on the transition's `allowed` role, its `allow_self_approval` flag and a
`condition` expression evaluated against the document. Frappe already answers
that in `frappe.model.workflow.get_transitions`, so `list_available_actions` and
`advance_workflow` call it rather than producing a second opinion. Every response
says which path ran.

---

## 11. `list_workflows`

Every Workflow on the site. Call it to learn a site's approval structure before
asking about any individual document.

**Arguments:** none.

**Returns** `workflows[]` with `name`, `workflow_name`, `document_type`,
`is_active`, `workflow_state_field`, `states[]`, `transitions[]`,
`terminal_states[]` and `roles[]`; plus `count` and `active_count`.

`terminal_states` are states with no outgoing transition — a document in one is
finished, not waiting. That distinction is what `list_pending_approvals` is
built on.

**Example**

```json
{"name": "list_workflows", "arguments": {}}
```

```json
{
  "workflows": [
    {
      "name": "Purchase Order Approval",
      "workflow_name": "Purchase Order Approval",
      "document_type": "Purchase Order",
      "is_active": 1,
      "workflow_state_field": "workflow_state",
      "send_email_alert": 1,
      "states": [
        {"state": "Draft", "doc_status": 0, "allow_edit": "Purchase User"},
        {"state": "Pending Approval", "doc_status": 0, "allow_edit": "Purchase Manager"},
        {"state": "Approved", "doc_status": 1, "allow_edit": "Purchase Manager"},
        {"state": "Rejected", "doc_status": 0, "allow_edit": "Purchase Manager"}
      ],
      "transitions": [
        {"state": "Draft", "action": "Submit for Approval", "next_state": "Pending Approval",
         "allowed": "Purchase User", "allow_self_approval": true,
         "has_condition": false, "condition": null},
        {"state": "Pending Approval", "action": "Approve", "next_state": "Approved",
         "allowed": "Purchase Manager", "allow_self_approval": false,
         "has_condition": false, "condition": null},
        {"state": "Pending Approval", "action": "Reject", "next_state": "Rejected",
         "allowed": "Purchase Manager", "allow_self_approval": true,
         "has_condition": true, "condition": "doc.grand_total > 0"}
      ],
      "terminal_states": ["Approved", "Rejected"],
      "roles": ["Purchase Manager", "Purchase User"]
    }
  ],
  "count": 1,
  "active_count": 1,
  "note": "terminal_states have no outgoing transition — a document there is finished, not waiting."
}
```

---

## 12. `get_workflow_state`

Where one document sits, and where it could go. Answers "who can move this";
`list_available_actions` answers "can I move this".

**Arguments:** `doctype` (required), `name` (required).

**Returns** `workflow`, `workflow_state_field`, `current_state`,
`current_state_detail`, `docstatus`, `docstatus_label`, `next_transitions[]`,
`is_terminal`. A document whose state field is empty — created before the
workflow was added — gets a `note` saying so rather than an error.

**Refused:** a doctype no active workflow governs (the error lists the ones that
are governed), an unknown document, an unknown doctype.

**Example**

```json
{"name": "get_workflow_state",
 "arguments": {"doctype": "Purchase Order", "name": "PUR-ORD-2026-00184"}}
```

```json
{
  "doctype": "Purchase Order",
  "name": "PUR-ORD-2026-00184",
  "workflow": "Purchase Order Approval",
  "workflow_state_field": "workflow_state",
  "current_state": "Pending Approval",
  "current_state_detail": {"state": "Pending Approval", "doc_status": 0,
                           "allow_edit": "Purchase Manager"},
  "docstatus": 0,
  "docstatus_label": "draft",
  "next_transitions": [
    {"state": "Pending Approval", "action": "Approve", "next_state": "Approved",
     "allowed": "Purchase Manager", "allow_self_approval": false,
     "has_condition": false, "condition": null}
  ],
  "is_terminal": false
}
```

---

## 13. `list_pending_approvals`

The worklist. Documents parked in a state that still has an action available,
grouped by workflow and state.

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `user` | no | Only states this user's roles can act on — the "what is waiting on me" question |
| `workflow` | no | Restrict to one Workflow |
| `limit` | no | Documents **per state**. Default 100, max 500 |

**Returns** `pending[]` — one entry per (workflow, state) with `actions[]`,
`allowed_roles[]`, `count`, `truncated` and `documents[]` — plus `group_count`,
`document_count`, `user_roles` and `limit_per_state`.

Terminal states are never listed. Cancelled documents (`docstatus 2`) are
excluded.

**Example**

```json
{"name": "list_pending_approvals", "arguments": {"user": "avi@example.com"}}
```

```json
{
  "pending": [
    {
      "workflow": "Purchase Order Approval",
      "doctype": "Purchase Order",
      "state": "Pending Approval",
      "actions": ["Approve", "Reject"],
      "allowed_roles": ["Purchase Manager"],
      "count": 2,
      "truncated": false,
      "documents": [
        {"name": "PUR-ORD-2026-00184", "owner": "bea@example.com",
         "modified": "2026-07-11 09:14:02", "docstatus": 0}
      ]
    }
  ],
  "group_count": 1,
  "document_count": 2,
  "user": "avi@example.com",
  "user_roles": ["Purchase Manager", "Purchase User"],
  "limit_per_state": 100,
  "note": "Only states with an outgoing transition are listed; terminal states are finished, not pending. Counts are per state and capped at limit_per_state."
}
```

---

## 14. `list_available_actions`

What the acting MCP user can do to this document right now.

"Acting MCP user" is the configured **MCP System User**, not the human at the
other end of the chat. That is the honest answer, because it is the user the
transition would actually run as.

**Arguments:** `doctype` (required), `name` (required).

**Returns** `user`, `user_roles[]`, `current_state`, `available_actions[]`,
`transitions[]`, `resolved_via`, `conditions_evaluated`.

`resolved_via` is `"frappe.model.workflow.get_transitions"` normally. On a
Frappe that does not export it, this app falls back to a role-only check,
`conditions_evaluated` is `false` and a `warning` says the list is a **superset**
— an action in it may still be refused. Transitions carrying a condition are
flagged `has_condition`.

A condition expression that raises is reported, not swallowed: a site bug should
not quietly become a laxer answer.

**Example**

```json
{"name": "list_available_actions",
 "arguments": {"doctype": "Purchase Order", "name": "PUR-ORD-2026-00184"}}
```

```json
{
  "doctype": "Purchase Order",
  "name": "PUR-ORD-2026-00184",
  "workflow": "Purchase Order Approval",
  "user": "mcp@example.com",
  "user_roles": ["Accounts User", "Purchase Manager"],
  "current_state": "Pending Approval",
  "available_actions": ["Approve", "Reject"],
  "transitions": [
    {"state": "Pending Approval", "action": "Approve", "next_state": "Approved",
     "allowed": "Purchase Manager", "allow_self_approval": 0}
  ],
  "resolved_via": "frappe.model.workflow.get_transitions",
  "conditions_evaluated": true
}
```

---

# Report tools

## 15. `list_reports`

**Arguments:** `module` (optional), `is_standard` (optional — `Yes`/`No`, or a
boolean).

**Returns** `reports[]` with `name`, `ref_doctype`, `report_type`, `module`,
`is_standard`, `disabled`, `prepared_report`; plus `count` and `by_report_type`.

`ref_doctype` is what the report reports on, and is what a permission check runs
against when you call `run_report`.

---

## 16. `run_report`

The highest-leverage tool in the catalogue. A site's reports are where its
accounting questions have already been answered correctly by somebody who knew
the schema.

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `name` | yes | Report docname, exactly as `list_reports` gives it |
| `filters` | no | The report's own filter fieldnames, as an object. A JSON string is accepted too |
| `user` | no | Run as this user instead of the configured MCP user |
| `limit` | no | Rows returned. Default 100, max 500; `total_rows` reports how many the report produced |

**Three engines, one tool.** Query and Script Reports run through
`frappe.desk.query_report.run` with `ignore_prepared_report=True` — without that,
a report configured as a Prepared Report queues a background job and hands back a
job id instead of rows. Report Builder reports have no server-side "run", so they
are materialised from their saved column/filter/sort config through
`frappe.desk.reportview.get`, falling back to `frappe.get_list` on versions where
that call has moved. `executed_via` names the path.

**Permissions apply here.** Unlike the ledger read tools, this runs through the
Desk APIs, which check the acting user's permission on `ref_doctype`. A report
the MCP System User may not read fails with a message naming the doctype.

**Returns** `report`, `report_type`, `ref_doctype`, `executed_as`,
`executed_via`, `filters_applied`, `columns`, `columns_normalised`, `rows`,
`row_count`, `total_rows`, `truncated`, `limit`, `row_format`, plus the report's
own `message` / `chart` / `report_summary` where it produced them.

`columns_normalised` parses the old colon-delimited column form
(`"Outstanding:Currency/USD:120"`) into `{fieldname, label, fieldtype, options,
width}` so a model does not have to.

**Example**

```json
{"name": "run_report",
 "arguments": {"name": "Accounts Receivable Summary",
               "filters": {"company": "Example Trading Co", "report_date": "2026-06-30"},
               "limit": 50}}
```

```json
{
  "report": "Accounts Receivable Summary",
  "report_type": "Script Report",
  "ref_doctype": "Sales Invoice",
  "executed_as": "mcp@example.com",
  "executed_via": "frappe.desk.query_report.run",
  "filters_applied": {"company": "Example Trading Co", "report_date": "2026-06-30"},
  "columns": [
    {"fieldname": "party", "label": "Customer", "fieldtype": "Link", "options": "Customer", "width": 200},
    "Outstanding:Currency/USD:120"
  ],
  "columns_normalised": [
    {"fieldname": "party", "label": "Customer", "fieldtype": "Link", "options": "Customer", "width": 200},
    {"fieldname": "outstanding", "label": "Outstanding", "fieldtype": "Currency", "options": "USD", "width": 120}
  ],
  "rows": [{"party": "Northwind Grocers", "outstanding": 2500.0}],
  "row_count": 1,
  "total_rows": 1,
  "truncated": false,
  "limit": 50,
  "row_format": "objects"
}
```

---

# Attachment tools

The three tools in this app that check Frappe permissions on the way in. A File
is whatever somebody uploaded — a signed contract, a passport scan, a payroll
export — and `is_private` is a promise the framework makes about who can see it.
The two read tools are here; `attach_file_to_document`, which writes, is **77**.

---

## 17. `list_attachments`

**Arguments:** `doctype` (required), `name` (required).

**Returns** `attachments[]` with `file_name`, `file_url`, `file_size`,
`size_human`, `mime_type`, `is_private`, `uploaded_by`, `uploaded_on` and
`retrievable` (false for anything over the default size cap); plus `count`,
`total_size` and `total_size_human`.

**Refused** unless the acting user may `read` the parent document. Listing what
is attached to a document you cannot read is itself a leak — filenames alone
often say enough.

---

## 18. `get_attachment_content`

**Arguments:** `name` (required — the **File docname**, not the filename),
`max_bytes` (optional; default 2097152, hard ceiling 8388608).

**Returns** `file_name`, `file_url`, `is_private`, `attached_to_doctype`,
`attached_to_name`, `uploaded_by`, `uploaded_on`, `file_size`, `size_human`,
`mime_type`, `encoding` (`"base64"`) and `content_base64`.

**Authorization**, in three cases: attached to a document → the parent's `read`
permission decides; unattached and private → only its owner or a System Manager;
public and unattached → readable. The File doctype's own `has_permission` is
consulted as well, so a site that has customised file access keeps it.

**Size.** Base64 inflates by a third and a token is roughly four characters, so a
2 MB file is on the order of 700k tokens. The cap is a guard against a hung
request, not a suggestion. A stale `file_size` does not get past it — the bytes
on disk are re-checked after reading.

```
payroll-export.csv is 5.0 MB, over the 2.0 MB cap. Raise max_bytes (hard ceiling
8.0 MB), or fetch it from /private/files/payroll-export.csv. Base64 inflates
content by a third, so anything past a few hundred kilobytes will not fit in a
model's context anyway.
```

---

# Comment and task tools

## 19. `list_comments`

**Arguments:** `doctype` (required), `name` (required), `comment_type`
(optional), `limit` (optional).

**Returns** `comments[]` with `comment_type`, `content`, `author`, `added_on`;
plus `count` and `by_comment_type`.

Frappe keeps framework chatter in the same table as things people typed.
`comment_type: "Comment"` is a human remark; `Info`, `Assigned`, `Workflow`,
`Edit` and friends are generated. Filter on it.

**Refused** unless the acting user may `read` the document.

---

## 20. `list_assigned_todos`

**Arguments:** `user` (optional), `status` (optional — `Open` by default; pass an
empty string for every status), `limit` (optional).

**Returns** `todos[]` with `assigned_to` (normalised), `status`, `priority`,
`date`, `description`, `reference_type`, `reference_name` and `overdue`; plus
`count`, `overdue_count` and `assignee_field`.

**A naming trap, handled.** Frappe's `owner` is whoever *created* the ToDo; the
assignee is `allocated_to` (and was `owner` on versions before that field
existed). The response carries both plus a normalised `assigned_to`, and
`assignee_field` says which column this site actually uses.

---

# HR tools

**Only present where the `hrms` app is installed.** On a site without it these
three are not advertised in `tools/list` and cannot be called.

---

## 21. `list_employees`

**Arguments:** `status` (default `Active`; empty for all), `department`,
`designation`, `company`, `limit`.

**Returns** `employees[]` with `name` (the `HR-EMP-…` docname the other HR tools
want), `employee_name`, `employee_number`, `department`, `designation`, `status`,
`date_of_joining`; plus `count` and `by_department`.

---

## 22. `get_attendance_summary`

**Arguments:** `from_date` (required), `to_date` (required), `employee`
(optional — docname, employee number, name or user id), `department` (optional).

**Returns** `employees[]`, each with `counts` keyed by every status seen on the
site plus the standard five, and `total_marked`; plus site-wide `totals`,
`records_counted` and `statuses[]`.

Aggregated, not day-by-day: a month for a team of forty is 1,200 rows that say
what a count says. Counts **submitted** Attendance only — a draft row is not
evidence anybody turned up. Days with no Attendance record at all are absent from
the counts rather than counted as Absent.

**Example**

```json
{"name": "get_attendance_summary",
 "arguments": {"from_date": "2026-06-01", "to_date": "2026-06-30",
               "department": "Operations"}}
```

```json
{
  "from_date": "2026-06-01",
  "to_date": "2026-06-30",
  "employees": [
    {"employee": "HR-EMP-00001", "employee_name": "Ada Orchard",
     "department": "Operations",
     "counts": {"Absent": 0, "Half Day": 0, "On Leave": 1, "Present": 19,
                "Work From Home": 2},
     "total_marked": 22}
  ],
  "employee_count": 1,
  "totals": {"Absent": 0, "Half Day": 0, "On Leave": 1, "Present": 19, "Work From Home": 2},
  "records_counted": 22,
  "statuses": ["Absent", "Half Day", "On Leave", "Present", "Work From Home"]
}
```

---

## 23. `get_leave_balance`

**Arguments:** `employee` (required), `leave_type` (optional — omit for every
type the employee has an allocation for), `as_of` (optional, defaults to today).

**Returns** `balances[]` of `{leave_type, balance}`, `total_balance`,
`computed_via`, and `failed[]` if one leave type errored.

Computed by HR's own `get_leave_balance_on`, which nets allocations against
applications and handles carry-forward and expiry. Do not reproduce this by
subtracting: those rules are the entire difficulty of the question. If the site's
HR app does not export the function, the tool refuses and points at the Leave
Balance report via `run_report` rather than guessing.

Only leave types with an allocation covering `as_of` are included — a balance for
a type nobody allocated is always zero and only adds noise. One misconfigured
type lands in `failed[]` without losing the others.

---

# Sales and purchasing tools

## 24. `list_sales_orders`

**Arguments:** `status`, `from_date`, `to_date`, `customer`, `company`, `limit`.

**Returns** `orders[]` with `customer`, `transaction_date`, `delivery_date`,
`grand_total`, `currency`, `status`, `per_delivered`, `per_billed`,
`docstatus_label`; plus `count`, `total_value` (of the rows returned — a partial
figure when `truncated`), and `by_status`.

---

## 25. `get_outstanding_invoices`

Submitted Sales Invoices with `outstanding_amount > 0`, aged against a date.

**Arguments:** `customer`, `company`, `as_of` (defaults to today), `limit`.

**Returns** `invoices[]`, each with `days_overdue` and `ageing_bucket`; plus
`total_outstanding` and `buckets` totalling count and outstanding per bucket.

**Buckets:** `current` (not yet due), `0-30`, `31-60`, `61-90`, `90+`, and
`unknown` (no `due_date`). `days_overdue = as_of - due_date`.

`current` exists because folding not-yet-due invoices into `0-30` makes an AR
summary look worse than the business is — an invoice issued yesterday on 30-day
terms is zero days of exposure, not thirty. `unknown` exists because an invoice
nobody put terms on is a real problem, and hiding it in `current` is how it stays
one.

**Example**

```json
{"name": "get_outstanding_invoices",
 "arguments": {"company": "Example Trading Co", "as_of": "2026-07-25"}}
```

```json
{
  "invoices": [
    {"name": "ACC-SINV-2026-00005", "customer": "Westbrook Cafe",
     "posting_date": "2025-12-02", "due_date": "2026-01-02",
     "grand_total": 5000.0, "outstanding_amount": 5000.0, "currency": "USD",
     "days_overdue": 204, "ageing_bucket": "90+"}
  ],
  "count": 1,
  "as_of": "2026-07-25",
  "total_outstanding": 5000.0,
  "buckets": {
    "current": {"count": 0, "outstanding": 0.0},
    "0-30": {"count": 0, "outstanding": 0.0},
    "31-60": {"count": 0, "outstanding": 0.0},
    "61-90": {"count": 0, "outstanding": 0.0},
    "90+": {"count": 1, "outstanding": 5000.0},
    "unknown": {"count": 0, "outstanding": 0.0}
  },
  "bucket_definition": "days_overdue = as_of - due_date. 'current' is not yet due (days_overdue <= 0); '0-30', '31-60', '61-90' and '90+' are days past due; 'unknown' is an invoice with no due_date."
}
```

---

## 26. `list_purchase_orders`

The mirror of `list_sales_orders`, with the nouns swapped: `supplier` instead of
`customer`, `schedule_date` instead of `delivery_date`, `per_received` instead of
`per_delivered`.

**Arguments:** `status`, `from_date`, `to_date`, `supplier`, `company`, `limit`.

---

# Site-customisation tools

## 27. `list_custom_fields`

The "why is my custom field not showing up" tool.

**Arguments:** `doctype` (optional), `limit` (optional).

**Returns** `custom_fields[]` in form order with `dt`, `fieldname`, `label`,
`fieldtype`, `options`, `insert_after`, `idx`, `reqd`, `hidden`, `read_only`,
`depends_on`, `default`; plus `count` and `by_doctype`.

A field that will not appear is usually hidden, gated by `depends_on`, or
inserted after a fieldname that does not exist on this version — all three are
visible in the response.

---

## 28. `list_client_scripts`

**Arguments:** `doctype` (optional), `enabled` (default `true`; `false` for
disabled only, `"any"` for both), `limit` (optional).

**Returns** `client_scripts[]` with `dt`, `view`, `enabled`, `script_preview`
(first 500 characters), `script_length` and `script_truncated`; plus
`source_doctype` (`Client Script`, or `Custom Script` on pre-v13 sites) and
`preview_chars`.

The full body is never returned. Thousands of lines of form JavaScript is
expensive and rarely the question; the useful facts are which doctype, which
view, and whether it is on.


---

# Mutating tools

**All seven are OFF on a fresh install** and stay off until an operator ticks the
matching `allow_<tool>` box in ERPNext MCP Settings. A call to a switched-off tool
is refused by name, before its arguments are looked at, and logged as `Blocked`:

```
the mutating tool 'submit_journal_entry' is switched off on this site. An operator
must tick 'allow_submit_journal_entry' in ERPNext MCP Settings to enable it.
```

Every write goes through ERPNext's own document methods, so doctype validation,
fiscal-year checks, period-closing vouchers, frozen accounts, mandatory
dimensions and `on_submit` hooks all apply. There is no raw SQL.

---

## 29. `create_journal_entry`

Creates a **draft** — `docstatus=0`, affecting no balance. It cannot submit, and
there is no argument that makes it.

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `company` | yes | |
| `posting_date` | yes | Must fall inside a fiscal year, or ERPNext refuses |
| `accounts` | yes | Array, minimum 2 entries (see below) |
| `user_remark` | yes | Why the entry exists. Recorded on the document and in the audit log |
| `cheque_no` | no | |
| `cheque_date` | no | |
| `voucher_type` | no | E.g. `Bank Entry`. Defaults to the doctype's default |

Each `accounts[]` entry takes `account` plus **exactly one** of `debit` or
`credit`, both positive. Optional per line: `party_type`, `party`, `cost_center`,
`project`, `against_account`, `reference_type`, `reference_name`,
`reference_due_date`, `user_remark`, `exchange_rate`, `account_currency`,
`is_advance`, `bank_account`. Any other key is rejected **by name** rather than
silently dropped.

Custom accounting dimensions go in a per-line **`dimensions`** object —
`{"member": "Member-01", "bbch_stage": "BBCH-8"}` — not alongside the fields
above. They get their own door because a dimension's fieldname is invented by
whoever created it, so there is no list this app could ship; and because simply
accepting unknown keys would turn `amount` (which a model will send, meaning
`debit`) from a corrected mistake into a silently dropped one. Every key is
checked against `Journal Entry Account`'s own fields and every Link value
against the records it can point at, so a dimension that does not exist yet is
refused rather than written to nothing. See tools 47 and 48.

**Refused before anything is written:** debits ≠ credits (tolerance half a cent),
fewer than two lines, a line with both debit and credit, a line with neither, a
negative amount, a group account, an account belonging to another company.

**Returns** `name`, `docstatus` (always 0), `docstatus_label`, `company`,
`posting_date`, `total_debit`, `total_credit`, `line_count`, `user_remark`, and
`next_step` stating that nothing has been posted.

**Example**

```json
{"name": "create_journal_entry",
 "arguments": {
   "company": "Example Trading Co",
   "posting_date": "2026-06-30",
   "user_remark": "Reclassify June clearing balance",
   "accounts": [
     {"account": "1190", "debit": 1450.00, "cost_center": "Main - ETC"},
     {"account": "1110", "credit": 1450.00, "cost_center": "Main - ETC"}
   ],
   "cheque_no": "A7741208"}}
```

```json
{
  "name": "ACC-JV-2026-00190",
  "docstatus": 0,
  "docstatus_label": "draft",
  "company": "Example Trading Co",
  "posting_date": "2026-06-30",
  "total_debit": 1450.0,
  "total_credit": 1450.0,
  "line_count": 2,
  "user_remark": "Reclassify June clearing balance",
  "dimension_fields_set": [],
  "next_step": "This is a draft and affects no balance. Submit it in ERPNext, or via submit_journal_entry if that tool is enabled."
}
```

Unbalanced input, on the other hand:

```
debits (1450.0) do not equal credits (1400.0); difference 50.0. Nothing was created.
```

---

## 30. `submit_journal_entry`

`docstatus 0 → 1`. **This writes GL Entries and moves balances.**

It takes a name and nothing else — it cannot create the entry it submits. Enabling
it means "post things a human or an earlier tool call already wrote down", never
"post something new right now".

**Arguments:** `name` (required) — a draft Journal Entry.

**Returns** `name`, `docstatus` (1), `docstatus_label`, `company`, `posting_date`,
`total_debit`, `total_credit`, `gl_entries_created`.

**Refused:** an entry that is already submitted, one that is cancelled, one that
does not exist.

**Example**

```json
{"name": "submit_journal_entry", "arguments": {"name": "ACC-JV-2026-00190"}}
```

```json
{
  "name": "ACC-JV-2026-00190",
  "docstatus": 1,
  "docstatus_label": "submitted",
  "company": "Example Trading Co",
  "posting_date": "2026-06-30",
  "total_debit": 1450.0,
  "total_credit": 1450.0,
  "gl_entries_created": 2
}
```

Audit row: `docstatus_delta = "0 → 1 (submitted)"`.

---

## 31. `cancel_journal_entry`

`docstatus 1 → 2`, writing reversing GL Entries. Nothing is deleted.

**Arguments:** `name` (required), `reason` (required, at least four characters).

`reason` is written twice — to the document's comment thread, where an accountant
looking at the JE will see it, and to the audit log, where it survives even if the
document is later removed.

**Returns** `name`, `docstatus` (2), `docstatus_label`, `company`,
`posting_date`, `reason`, `note`.

**Refused:** a draft (delete it in ERPNext instead — it affects no balance), an
already-cancelled entry, a placeholder reason.

**Example**

```json
{"name": "cancel_journal_entry",
 "arguments": {"name": "ACC-JV-2026-00190",
               "reason": "Duplicate of ACC-JV-2026-00184; clearing swept twice"}}
```

```json
{
  "name": "ACC-JV-2026-00190",
  "docstatus": 2,
  "docstatus_label": "cancelled",
  "company": "Example Trading Co",
  "posting_date": "2026-06-30",
  "reason": "Duplicate of ACC-JV-2026-00184; clearing swept twice",
  "note": "ERPNext keeps cancelled entries and their reversing GL rows; nothing was deleted."
}
```

Annotated `destructiveHint: true` — the only tool in the catalogue that is.

---

## 32. `create_bank_transaction`

Inserts a **draft** Bank Transaction. `amount` is signed as a human reads a
statement: positive money in, negative money out, mapped onto whichever columns
this ERPNext version has.

Left as a draft deliberately: a submitted Bank Transaction is eligible for
reconciliation and starts matching against payments. Submitting is a human step in
ERPNext, and this app ships no tool for it.

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `bank_account` | yes | Docname or `account_name` |
| `date` | yes | |
| `amount` | yes | Non-zero. Positive in, negative out |
| `description` | yes | Statement narrative |
| `reference_no` | no | |
| `company` | no | Taken from the Bank Account when it has one |

`currency` is taken from the linked Account when the site tracks it.

**Returns** `name`, `docstatus` (0), `docstatus_label`, `bank_account`, `date`,
`amount_signed`, `amount_layout`, `description`, `reference_no`, `next_step`.

**Example**

```json
{"name": "create_bank_transaction",
 "arguments": {"bank_account": "Operating", "date": "2026-06-30",
               "amount": -42.50, "description": "MONTHLY SERVICE CHARGE",
               "reference_no": "FEE-202606"}}
```

```json
{
  "name": "BT-2026-00431",
  "docstatus": 0,
  "docstatus_label": "draft",
  "bank_account": "Operating - Example Bank",
  "date": "2026-06-30",
  "amount_signed": -42.5,
  "amount_layout": "deposit_withdrawal",
  "description": "MONTHLY SERVICE CHARGE",
  "reference_no": "FEE-202606",
  "next_step": "Draft Bank Transactions are not reconcilable. Submit it in ERPNext to include it in bank reconciliation."
}
```

---

## 33. `reconcile_bank_transaction`

Attaches payment vouchers to a Bank Transaction.

Hands the work to ERPNext's own `BankTransaction.add_payment_entries` where the
site's version has it, because that method is where clearance dates, allocation
arithmetic and the transaction's status live. Reimplementing it here would mean
guessing at the parts of reconciliation ERPNext does after the child row is
written — and getting one of them wrong leaves a transaction that looks reconciled
and is not. The append-and-save fallback is only for versions predating the
method, and the response says which path ran.

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `name` | yes | Bank Transaction docname |
| `payment_entries` | yes | Array, minimum 1 |

Each `payment_entries[]` entry needs `payment_document` (the voucher doctype, e.g.
`Payment Entry`, `Journal Entry`, `Sales Invoice`), `payment_entry` (its docname)
and `allocated_amount` (positive). Every voucher's existence is checked before
anything is written.

**Refused:** allocating more than the transaction's remaining amount, a voucher
that does not exist, a doctype that does not exist on this site, a non-positive
allocation, a cancelled Bank Transaction.

**Returns** `name`, `bank_account`, `gross_amount`, `allocated_now`,
`allocated_total`, `unallocated_amount`, `status`, `payment_entries[]` as stored,
and `applied_via`.

**Example**

```json
{"name": "reconcile_bank_transaction",
 "arguments": {"name": "BT-2026-00412",
               "payment_entries": [
                 {"payment_document": "Payment Entry", "payment_entry": "ACC-PAY-2026-00088",
                  "allocated_amount": 2400.00}]}}
```

```json
{
  "name": "BT-2026-00412",
  "bank_account": "Operating - Example Bank",
  "gross_amount": 2400.0,
  "allocated_now": 2400.0,
  "allocated_total": 2400.0,
  "unallocated_amount": 0.0,
  "status": "Reconciled",
  "payment_entries": [
    {"payment_document": "Payment Entry", "payment_entry": "ACC-PAY-2026-00088",
     "allocated_amount": 2400.0}
  ],
  "applied_via": "ERPNext add_payment_entries"
}
```

Over-allocation:

```
allocating 3000.0 would exceed Bank Transaction BT-2026-00412's remaining 2400.0
(gross 2400.0, already allocated 0.0). Nothing was changed.
```

---

## 34. `advance_workflow`

**MUTATING (default OFF).** Take a workflow action on a document.

Runs `frappe.model.workflow.apply_workflow`, the same code path the Desk button
uses — so the state change, any `update_field` the target state sets, the
docstatus change and the resulting submit or cancel all happen exactly as they
would for a human. **A transition into a state with `doc_status: 1` submits the
document.** There is no fallback: on a Frappe that does not export
`apply_workflow`, this refuses rather than hand-rolling a state write.

**Arguments:** `doctype` (required), `name` (required), `action` (required — the
transition's action label, exactly as `list_available_actions` reports it),
`dry_run` (optional, default false).

**Returns** `action`, `user`, `state_before`, `state_after`, `docstatus_before`,
`docstatus_after`, `docstatus_label`.

### `dry_run` — do this first

With `dry_run: true` nothing is executed. The tool resolves the transition, reads
the target state's `doc_status` and reports what *would* happen:

```json
{"name": "advance_workflow",
 "arguments": {"doctype": "Purchase Order", "name": "PUR-ORD-2026-00184",
               "action": "Approve", "dry_run": true}}
```

```json
{
  "dry_run": true,
  "executed": false,
  "current_state": "Pending Approval",
  "current_docstatus": 0,
  "would_succeed": true,
  "would_move_to": "Approved",
  "would_set_docstatus": 1,
  "would_submit": true,
  "would_cancel": false,
  "effects": [
    "workflow_state: 'Pending Approval' → 'Approved'",
    "SUBMITS the document (target state has doc_status 1) — for a Journal Entry this writes GL Entries and moves balances"
  ],
  "available_actions": ["Approve", "Reject"],
  "conditions_evaluated": true,
  "refusal_reason": null,
  "next_step": "Call again with dry_run=false to execute."
}
```

**A dry run never raises for an unavailable action.** "It would be refused, and
here is why" is the answer to the question, not a failure to answer it — so that
case comes back as `would_succeed: false` with a `refusal_reason`. A malformed
question (unknown document, no workflow, two active workflows) still errors,
because there is nothing to answer.

**What it cannot tell you.** A dry run resolves the *transition*. It does not run
the document's own validation, so it cannot predict a submit that fails on a
mandatory field, a closed period or a doctype hook. `would_succeed: true` means
"this action is available to you", not "the resulting save will succeed".

**Refused** — with the available actions listed — if the action is not open to
the acting user in the document's current state. The check runs through the same
resolution `list_available_actions` uses, so the self-approval rule and any
condition apply.

**Example**

```json
{"name": "advance_workflow",
 "arguments": {"doctype": "Purchase Order", "name": "PUR-ORD-2026-00184",
               "action": "Approve"}}
```

```json
{
  "doctype": "Purchase Order",
  "name": "PUR-ORD-2026-00184",
  "workflow": "Purchase Order Approval",
  "action": "Approve",
  "user": "mcp@example.com",
  "state_before": "Pending Approval",
  "state_after": "Approved",
  "docstatus_before": 0,
  "docstatus_after": 1,
  "docstatus_label": "submitted"
}
```

Audit row: `docstatus_delta = "0 → 1 (submitted)"`.

Refusal:

```
'Approve' is not available to mcp@example.com on Purchase Order
PUR-ORD-2026-00184 in state 'Pending Approval'. Available: Reject.
```

---

## 35. `create_todo`

**MUTATING (default OFF).** Assign a ToDo to a user, optionally against a
document.

The gentlest write in the catalogue — it touches no ledger and submits nothing —
but it does put an item in somebody's queue and notify them, which is why it is
still off by default.

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `subject` | yes | One-line summary |
| `owner` | yes | The User it is assigned **to**. Must exist and be enabled |
| `description` | no | Longer detail, appended below the subject |
| `priority` | no | `Low`, `Medium` (default), `High` |
| `reference_doctype` | no | Pass with `reference_name` or not at all |
| `reference_name` | no | |
| `date` | no | Due date, `YYYY-MM-DD` |

**Two naming traps, both handled.** `owner` is the argument name because that is
what a caller means by "assign it to them" — it is written to `allocated_to`,
the assignee field, not to Frappe's `owner`, which is the creator. And stock ToDo
has no `subject` field, so `subject` becomes the first line of `description`
(and is set on a real `subject` field on sites that added one). The response's
`assignee_field` and `subject_handling` say what actually happened, so the caller
is never left guessing where its text went.

**Returns** `name`, `assigned_to`, `assignee_field`, `assigned_by`, `subject`,
`status`, `priority`, `date`, `reference_type`, `reference_name`,
`subject_handling`.

**Example**

```json
{"name": "create_todo",
 "arguments": {"subject": "Chase Westbrook Cafe — 204 days overdue",
               "owner": "avi@example.com", "priority": "High",
               "reference_doctype": "Sales Invoice",
               "reference_name": "ACC-SINV-2026-00005",
               "date": "2026-08-01"}}
```

```json
{
  "name": "abc123def4",
  "assigned_to": "avi@example.com",
  "assignee_field": "allocated_to",
  "assigned_by": "mcp@example.com",
  "subject": "Chase Westbrook Cafe — 204 days overdue",
  "status": "Open",
  "priority": "High",
  "date": "2026-08-01",
  "reference_type": "Sales Invoice",
  "reference_name": "ACC-SINV-2026-00005",
  "subject_handling": "folded into description (ToDo has no subject field)"
}
```


---

# Compliance packet tools

A packet is an artefact rather than an answer: a structured JSON document for
somebody who has to sign something off. Both tools are read-only — nothing is
stored, emailed or filed.

Every packet carries the same envelope on top of its own body:

| Field | Meaning |
| --- | --- |
| `packet_type`, `title`, `purpose`, `audience` | What this is and who reads it |
| `filters` | The arguments it was built from, echoed back |
| `flags[]` | `{code, severity, description, detail}`, worst first |
| `flag_summary` | Counts per severity, `worst`, and `signable` (false if any ERROR) |
| `generated_at`, `generated_by`, `site`, `generator`, `generator_version` | Provenance |
| `mcp_action_log_id` | The MCP Action Log row for the call that produced this packet |
| `external_sources[]` | Empty in v0.3.0. Reserved for external reconciliation sources |

**Severity means something.** INFO is context. WARN is "a human should look".
ERROR is "these numbers do not internally agree" — an arithmetic or integrity
failure inside the packet, not merely an unusual business fact. A packet with an
ERROR flag has `signable: false` and should not be signed.

---

## 36. `list_compliance_packets`

**Arguments:** none.

**Returns** `packets[]` (each with `packet_type`, `title`, `purpose`,
`audience`, `filters` schema, `required_filters`, `switch`), plus `disabled[]`
(a switch an operator can tick) and `unavailable[]` (a site prerequisite that
cannot be ticked).

Call it before `generate_compliance_packet`: packet types are site-dependent,
and its `filters` schema is how a client learns to call a type this app's own
MCP schema knows nothing about.

---

## 37. `generate_compliance_packet`

**Arguments:** `packet_type` (required), `filters` (object, per the type).

Unknown filter keys are rejected **by name** rather than ignored. Silently
generating an unscoped packet when the caller thought they had scoped it is the
worst outcome available.

### `reconciliation_packet`

**Filters:** `account` (required), `period_start` (required), `period_end`
(required), `company`.

| Key | Contents |
| --- | --- |
| `account` | `{name, number, type, root_type, company, currency, account_name}` |
| `period` | `{start, end}` |
| `opening_balance` | `{amount, as_of, source, gl_entry_count}` at `period_start - 1` |
| `closing_balance` | Same shape, at `period_end` |
| `movement_summary` | `total_debits`, `total_credits`, `net_change`, `count_transactions` |
| `journal_entries[]` | Submitted JEs touching the account, each with `this_account_debit` / `_credit` / `_net` |
| `unposted_drafts[]` | Same shape, `docstatus 0` — the movement that has not happened yet |
| `cancelled_entries[]` | Same shape, `docstatus 2` — invisible to a balance query, which is why they are here |
| `arithmetic_check` | `opening + net` vs `closing`, and whether it reconciles |

Balances are summed from GL Entry excluding cancelled rows, matching ERPNext's
own General Ledger report. The Journal Entry lists come from the
`Journal Entry Account` child table instead, because that is the only source
that can see drafts and cancellations.

**Flags it raises:** `BALANCE_DOES_NOT_RECONCILE` (ERROR),
`UNBALANCED_JOURNAL_ENTRY` (ERROR), `CANCELLED_ENTRIES` (WARN),
`UNPOSTED_DRAFTS` (WARN), `NEGATIVE_BALANCE` (WARN), `NO_ACTIVITY` (INFO),
`QUIET_PERIOD` (INFO), `FUTURE_DATED` (INFO), `LARGE_ENTRY` (INFO),
`TRUNCATED` (WARN).

**Example**

```json
{"name": "generate_compliance_packet",
 "arguments": {"packet_type": "reconciliation_packet",
               "filters": {"account": "1100", "period_start": "2026-01-01",
                           "period_end": "2026-06-30"}}}
```

```json
{
  "packet_type": "reconciliation_packet",
  "account": {"name": "1100 - Cash - ETC", "number": "1100", "type": "Cash",
              "root_type": "Asset", "company": "Example Trading Co", "currency": "USD"},
  "period": {"start": "2026-01-01", "end": "2026-06-30"},
  "opening_balance": {"amount": 0.0, "as_of": "2025-12-31", "source": "GL Entry", "gl_entry_count": 0},
  "closing_balance": {"amount": 750.0, "as_of": "2026-06-30", "source": "GL Entry", "gl_entry_count": 2},
  "movement_summary": {"total_debits": 1000.0, "total_credits": 250.0,
                       "net_change": 750.0, "count_transactions": 2},
  "journal_entries": [
    {"name": "ACC-JV-2026-00001", "posting_date": "2026-01-15", "docstatus": 1,
     "user_remark": "Opening sale", "total_debit": 1000, "total_credit": 1000,
     "this_account_debit": 1000.0, "this_account_credit": 0.0, "this_account_net": 1000.0}
  ],
  "unposted_drafts": [
    {"name": "ACC-JV-2026-00002", "posting_date": "2026-02-10", "docstatus": 0,
     "user_remark": "Stationery", "this_account_net": -250.0}
  ],
  "cancelled_entries": [
    {"name": "ACC-JV-2025-00009", "posting_date": "2026-03-05", "docstatus": 2,
     "this_account_net": -40.0}
  ],
  "arithmetic_check": {"opening_plus_net": 750.0, "closing": 750.0,
                       "difference": 0.0, "reconciles": true},
  "external_sources": [],
  "flags": [
    {"code": "CANCELLED_ENTRIES", "severity": "WARN",
     "description": "1 Journal Entry/Entries touching this account were cancelled in the period, 40.0 in gross movement. Cancelled entries leave no live GL rows, so they do not appear in the balance — somebody posted these and then unposted them.",
     "detail": {"count": 1, "gross_amount": 40.0, "entries": ["ACC-JV-2025-00009"]}},
    {"code": "UNPOSTED_DRAFTS", "severity": "WARN",
     "description": "1 draft Journal Entry/Entries dated in the period would move this account by -250.0 if submitted. The closing balance in this packet does not include them.",
     "detail": {"count": 1, "net_if_submitted": -250.0, "entries": ["ACC-JV-2026-00002"]}},
    {"code": "LARGE_ENTRY", "severity": "INFO",
     "description": "1 entry/entries account for at least 25% of the period's gross movement each. Materiality is a judgement — this points, it does not conclude.",
     "detail": {"threshold": 312.5, "entries": [{"name": "ACC-JV-2026-00001", "amount": 1000.0, "share_of_period": 0.8}]}}
  ],
  "flag_summary": {"ERROR": 0, "WARN": 2, "INFO": 1, "worst": "WARN", "signable": true},
  "generated_at": "2026-07-26 09:14:22.117045",
  "generated_by": "mcp@example.com",
  "site": "erp.example.com",
  "generator": "erpnext_mcp",
  "generator_version": "0.3.0",
  "mcp_action_log_id": "b7c41f9e2a"
}
```

### `fiscal_year_audit_packet`

**Filters:** `company` (required), `fiscal_year` (required).

| Key | Contents |
| --- | --- |
| `date_range` | `{start, end, disabled}` from the Fiscal Year |
| `trial_balance` | `by_root_type` (grouped rows), `totals_by_root_type`, `row_count` |
| `trial_balance_totals` | One cumulative aggregate over every account — where debits must equal credits |
| `income_statement` | `revenue`, `expenses`, `net_income`, movement within the year |
| `balance_sheet` | `assets`, `liabilities`, `equity`, `liabilities_plus_equity`, cumulative |
| `accounting_identity` | `Assets - (Liabilities + Equity)` vs `net_income`, and whether it holds |
| `top_20_entries_by_amount` | Submitted JEs ranked by absolute amount, for materiality |
| `intercompany_activity[]` | JEs whose account lines span more than one company |
| `document_counts` | Sales/purchase invoices, JEs by docstatus, bank transactions. `null` = doctype not installed |

**Two bases, stated per row.** Balance-sheet accounts carry forward, so their
`basis` is `cumulative`. Profit-and-loss accounts reset each year, so theirs is
`fiscal_year`. Mixing the two silently is how a trial balance stops balancing;
`trial_balance_totals` is computed separately on a single cumulative basis, which
is where debits and credits must agree.

**Flags it raises:** `TRIAL_BALANCE_IMBALANCE` (ERROR),
`ACCOUNTING_IDENTITY_FAILS` (ERROR), `INTERCOMPANY_ACTIVITY` (WARN),
`CANCELLED_ENTRIES` (WARN), `DRAFT_ENTRIES_AT_YEAR_END` (WARN),
`UNNATURAL_BALANCE` (WARN), `FISCAL_YEAR_NOT_LINKED` (WARN),
`FISCAL_YEAR_DISABLED` (INFO), `NO_ACTIVITY` (INFO).

Note on `ACCOUNTING_IDENTITY_FAILS`: for a year already closed to retained
earnings, the two sides legitimately differ. The flag says so.

---

# Chart of accounts

Five write tools and one planner, over the chart itself rather than postings
into it. They are grouped separately here for the same reason they have their
own settings section: a bad journal entry is one wrong number that a reversing
entry fixes, while a bad reparent changes what every balance-sheet subtotal has
meant, retroactively, for every period already reported.

## The docname, which explains most of the design

An Account's primary key is `"<number> - <name> - <abbr>"`, built by ERPNext's
`autoname` at insert and **never rebuilt afterwards**. Two consequences run
through everything below:

- Renaming an account means changing two fields *and* moving the document. Doing
  either half alone leaves an account that is called one thing and reports
  another, permanently. `update_account` delegates both halves to ERPNext's own
  `update_account_number`.
- A dry run has to *predict* the docname, since nothing has been inserted yet.
  `charts.account_docname` reproduces the rule; an in-bench test asserts the
  prediction matches what a real insert produces.

And one rule that is ERPNext's, not this app's: **a root account cannot be
saved at all.** `Account.validate_root_details` throws "Root cannot be edited"
on any save of an account with no parent. So roots cannot be re-typed, disabled,
moved, or created by `create_account` — only renamed, which goes down a
different path, and created by `import_chart_of_accounts` as part of a tree.

---

## 38. `create_account`

**MUTATING. Default OFF.**

**Arguments:** `company`, `account_number`, `account_name`, `root_type`,
`parent_account` (all required), `is_group`, `account_type`, `account_currency`,
`tax_rate`.

Checks, all before anything is written:

| Check | Refusal |
| --- | --- |
| Parent exists, in this company, and is a group | `is a ledger account, not a group` |
| `root_type` matches the parent's | `does not match parent_account …, whose root_type is …` |
| `account_number` free in this company | `already used by '1100 - Cash - ETC'` |
| The computed docname is free | `an Account named … already exists` |
| `account_type` is one this site offers | lists the site's own options |
| `account_type` can sit under this `root_type` | `belongs under root_type Liability, not 'Asset'` |
| `account_currency` exists | `no Currency named 'ZZZ'` |

`root_type` is required even though it is derivable from the parent. That is
deliberate: it makes the caller state its intent, so this tool can check it
rather than infer it.

```json
{"name": "tools/call", "arguments": {
  "company": "Example Trading Co", "account_number": "1150",
  "account_name": "Money Market", "root_type": "Asset",
  "parent_account": "1100", "account_type": "Bank"}}
```

```json
{
  "name": "1150 - Money Market - ETC",
  "account_number": "1150",
  "parent_account": "1100 - Current Assets - ETC",
  "root_type": "Asset",
  "report_type": "Balance Sheet",
  "account_type": "Bank",
  "is_group": false,
  "disabled": false,
  "company": "Example Trading Co",
  "next_step": "Ledger account, ready to post to."
}
```

---

## 39. `update_account`

**MUTATING. Default OFF.**

**Arguments:** `name` (required), `company`, `new_account_name`,
`new_account_number`, `new_account_type`, `disabled`. At least one `new_*` or
`disabled` is required.

Returns the account's new shape plus `previous_name`, `renamed`, `changes` (a
map of `field → [before, after]`) and `rename_method` — which will read
`erpnext update_account_number` on any current ERPNext, and names the legacy
fallback otherwise.

**It cannot reparent.** `new_parent_account` is not in the schema. Renaming is
routine and reversible; moving is neither, and keeping them apart means an
operator can enable one without the other.

Two refusals worth knowing about:

- **Root accounts** cannot have their type or disabled flag changed, because
  ERPNext will not save a root. Renaming and renumbering still work.
- **Crossing the Receivable/Payable boundary** on an account that already has GL
  entries is refused. ERPNext keys party balances off that flag; flipping it on
  an account with history leaves the ledger silently unreconciled.

---

## 40. `move_account`

**MUTATING. Default OFF. Destructive hint.**

**Arguments:** `name`, `new_parent_account` (both required), `company`.

Validates that the new parent is a group, in the same company, with the same
`root_type`, and that the move would not create a cycle — the cycle check walks
the `parent_account` chain rather than comparing `lft`/`rgt`, because a stale
nested set would turn a wrong answer into an infinite loop inside a tree rebuild.

The response carries `gl_entries_on_this_account` and this note, which is the
whole reason the tool is separate:

> Reparenting does not move a single GL Entry — every posting stays exactly
> where it was. What changes is which subtotal on the balance sheet and P&L those
> postings roll up into, for every period, including ones already reported.

---

## 41. `disable_account`

**MUTATING. Default OFF. Destructive hint.**

**Arguments:** `name`, `reason` (both required), `company`.

ERPNext's soft delete. Nothing is removed — the account, its history and its GL
entries all remain, and `update_account(disabled=false)` puts it back.

**Refuses any account with GL entries in the current fiscal year.** That is the
line between tidying the chart and breaking this year's reports: a disabled
account drops out of pickers and out of some period comparisons, and doing that
to an account the year is still posting through produces figures nobody can
reconcile. The current fiscal year is resolved the way ERPNext resolves it —
company-restricted years beat global ones. On a site where no fiscal year covers
today, the window falls back to the trailing 365 days, which is wider and
therefore errs towards refusing; the response says which window was used.

Also refuses a root account, and reports `child_accounts` when disabling a
group, because its children are **not** disabled by this call and stay postable.

`reason` goes onto the account's comment thread and into the audit log.

---

## 42. `import_chart_of_accounts`

**MUTATING. Default OFF.**

**Arguments:** `company`, `accounts_json` (both required), `dry_run`.

**`dry_run` defaults to `true`,** and that default is load-bearing: an
accidental call — a model retrying, a client replaying a message — must not be
able to rearrange a live chart of accounts, and the only way to guarantee that
is for the dangerous behaviour to be the one you ask for. An unparseable
`dry_run` is an error, not a vote for false.

`accounts_json` accepts a list of root accounts, a JSON string of the same, or
the whole `propose_clean_chart` response (its `accounts` key is used). Per node:
`account_number`, `account_name`, `root_type` (required on roots, inherited
below), `account_type`, `account_currency`, `tax_rate`, `is_group`,
`description`, `children`, and on a root node `parent_account` to graft the
subtree onto an existing group instead of adding a new root. Unknown keys are
rejected by name.

**The plan.** Every call returns `accounts[]` in dependency order — parents
before children — each row carrying `action`, the predicted `docname`, its
`parent_account`, `root_type`, `account_type` and `depth`:

| `action` | Meaning |
| --- | --- |
| `create` | Would be created. |
| `created` | Was created (real runs only). |
| `skip` | Already present with the same number *and* the same name. Left exactly as it is — this is what makes re-running an import safe. |
| `error` | Something has to be fixed first. `note` says what. |

Matching an existing account on the number alone would be how a reviewed chart
silently comes to mean something else, so anything beyond a name match — a
group/ledger mismatch, a different root type, a different parent — is an `error`
rather than a `skip`. An import will never reparent or rename an account that
already exists.

**`blocking_problems`** (dry runs only) is the list worth reading. One bad group
takes its whole subtree with it, so five real problems can produce seventy error
rows; `blocking_problems` holds only the causes, and every other error row is a
child of something that cannot be created.

**On a company built from a bundled ERPNext chart, expect collisions.** ERPNext's
"Standard with Numbers" numbers its own roots 1000/2000/3000/4000/5000 and its
groups 1100, 1200, 1700 and so on — the same convention this template uses,
because it is the convention. A company created that way will report thirty-odd
numbers already in use, and the import refuses until they are freed. Renumbering
the bundled accounts out of the way first with `update_account` (prefixing each
with a 9, say) is the straightforward fix, and `propose_clean_chart` lists
exactly which ones. Disabling one does not free its number, and this app has no
delete tool.

**Atomicity.** A real run is one transaction. The first failure rolls the whole
import back; there is no half-built tree to unpick, which matters more here than
anywhere else in this app because a partial tree has orphaned groups in it.

Capped at 400 accounts per call — ERPNext rebuilds the account nested set on
every insert, and refusing with a number beats timing out half way.

---

## 43. `propose_clean_chart`

**Read-only.** On by default.

**Arguments:** `company` (required), `template` (default `us_llc_farm`).

Returns a complete chart in exactly the shape tool 42 takes, plus what a
reviewer needs to judge it:

| Key | What it is |
| --- | --- |
| `accounts` | The tree, ready to pass to `import_chart_of_accounts`. |
| `optional_accounts` | Accounts a small operation will not need, so they can be struck first. |
| `account_type_adjustments` | Every `account_type` swapped for one this ERPNext actually offers, with the reason. |
| `existing_root_accounts` | What the company already has at the top of its chart. |
| `account_numbers_already_in_use` | Template numbers that would collide. |
| `notes` | Caveats from the template author. |
| `warning` | Set when the company already has roots — see below. |

**Importing adds roots, it does not replace them.** ERPNext will not let a root
be edited or moved once created, so a company that already has a chart ends up
with two sets of roots and the old ones have to be retired with
`disable_account`. The warning says so, and `existing_root_accounts` is how you
see what you are in for before running anything.

**Templates are static data.** They live in `erpnext_mcp/charts/` as plain
Python literals and never touch the database, which is what makes a proposal
reviewable, diffable and version-controllable before it runs. The package
auto-discovers them, so a new one is a single file drop.

### `us_llc_farm`

81 accounts — 17 groups, 64 ledgers — for a US farming LLC that also runs an
investment book. Numbering is the convention a US bookkeeper expects: 1000s
assets, 2000s liabilities, 3000s equity, 4000s income, 5000s COGS, 6000s
operating expenses, 7000s non-operating. Gaps are deliberate.

**Compact on purpose.** Nine flat operating-expense buckets, no sub-groups, and
at most two levels of grouping anywhere. A chart with a line for every
conceivable cost is one where nobody finds the right line; the intent is that a
sub-account gets added when a real transaction needs it, not in advance.

Four things it does that a generic chart does not:

- **Crop labour is separated from administrative wages** — `5150 Direct Farm
  Labor` under COGS, `6100 Payroll & Benefits` under operating expenses. A cost
  per bin means nothing if the two are mixed. `6150 Employer Payroll Tax
  Expense` splits out again, so wage cost and true cost of employment read
  apart, and neither is confused with `2140 Payroll Tax Withholdings` — money
  withheld from employees, which is a liability and never an expense.
- **The trading segment is a range set, not a dashboard.** The investment book
  has its own accounts on every side of the ledger:

  | Side | Range | |
  | --- | --- | --- |
  | Asset | `1800-1849` | stocks & ETFs, mutual funds, bonds, brokerage cash, open options |
  | Income | `4200-4249` | interest, dividends, realised gains, options premium |
  | Expense | `7300-7339` | realised capital losses, options losses, advisory fees, custodian & brokerage fees |
  | Equity | `3500` | unrealised gain/loss — mark-to-market, outside the P&L until a position closes |

  Filter a P&L or trial balance to those four and you have the trading business,
  running costs included; exclude them and you have the farm. Nothing else in
  the chart reaches into those numbers, which is what keeps the split true as
  the chart grows — a standalone test walks the whole tree and fails if an
  account outside the ranges starts reading as trading.

  Three splits inside the segment are there because a combined account just
  moves work downstream. `1810` (exchange-traded) and `1815` (mutual funds) are
  apart because a fund prices once daily at NAV and an ETF prices continuously.
  `7300` (equity, fund and bond losses) and `7310` (options losses) are apart
  because loss harvesting separates short-term from long-term anyway, and
  options treatment can differ again — Section 1256 for index options, ordinary
  for most others. `7320` (advisory, time- and asset-based) and `7330`
  (custodian and per-transaction) are apart because read together they tell you
  neither.

  **`1830 Brokerage Cash & Money Market` ships as an empty group.** It takes one
  child per linked brokerage cash-services account, added after the import with
  `create_account` (`account_type=Bank`) once you know which accounts exist.
  Per-account visibility is what makes anchor reconciliation, sweep tracing and
  per-account fee attribution possible — collapsed into one combined ledger, a
  paired-brokerage feed cannot say which account a movement belongs to. The
  template ships no default children because the account numbers are a property
  of the install, not of the chart. An empty group is legal in ERPNext and posts
  to nothing, which is the correct state until the first brokerage account is
  connected.

  **One exception, and it matters for Bank Bridge.**
  `1130 Cash Clearing - Brokerage` carries the word "brokerage" but is NOT part
  of the segment. It is the bridge for paired brokerage/companion transactions —
  the pattern where one movement appears on both a brokerage account and its
  linked cash-services account — and a reconciled pair passes through it on the
  way to its real accounts. The balance is transient and should read zero; a
  standing balance means a pair did not close. Leave it out of segment
  reporting, and do not remove it if a bank feed posts paired investment
  transactions, because that posting has nowhere else to land.
- **`2120 Current Pay Period - Due to Employees`** is a live balance, not an
  accrual. It is meant to be updated continuously as work lands — bucket picks
  as they are recorded, hours as they accumulate — so it reads at any moment as
  real-time wage exposure, and flushes to zero when payroll is processed. Its
  description says exactly that, because a month-end adjusting entry dropped in
  on top double-counts against the continuous postings and destroys the one
  property the account exists for. `2130 Employee Wage Advances` is its
  counterpart for wages paid before payday, and is distinct again from
  `1510 Employee Cash Advances`, which is money the business expects back.
- **Property tax is tracked in all three places it lives** — accrued in
  `2170 Property Tax Payable` (the county bills once or twice a year but the
  obligation accrues monthly), prepaid in `1420 Prepaid Property Tax`, and
  expensed through `6650 Property & Business Taxes` alongside vehicle
  registration, LLC filing fees and business licences.

Four accounts carry no `account_type` on purpose — `1810`, `1815`, `1820` and
`1840`.
ERPNext offers nothing that fits a securities or open-options position; the
nearest, `Stock`, means trading inventory and would pull them into the Stock
module's valuation. An account with no type still posts.

The 5000-series accounts are typed `Expense Account` rather than ERPNext's
`Cost of Goods Sold`. If the Stock module is used against `1300`, the Item or
Item Group default expense account has to be set by hand, because ERPNext looks
for the `Cost of Goods Sold` type when it picks one automatically.

Equity is the part that is entity-specific, which is why the entity type is in
the template key rather than a flag: `us_c_corp`, `us_s_corp` and
`us_partnership` will differ from this almost entirely in the 3000s, and
pretending one chart covers all four would put the wrong equity structure on
somebody's return. It is a starting point, not tax advice.

---

# Cost centers and accounting dimensions

Six tools for the *other* axes a posting is filed under. The chart of accounts
says what kind of money a transaction is; these say which part of the business it
belongs to, and whatever else the operator needs to slice by.

**The thing to understand before tool 47.** An ERPNext Accounting Dimension does
not hold its own values. It **points at a DocType**, and every record of that
DocType is a value. So "a Member dimension with three members" is three ideas: a
DocType to hold members, the dimension record plus the Link field it puts on each
accounting document, and one record per member.

```
create_accounting_dimension(dimension_name="Member", create_master_if_missing=true)
  ↓                                   the DocType, the dimension, the fields
create_dimension_value(dimension_name="Member", value_name="Member-01")
  ↓                                   one value
create_journal_entry(accounts=[{..., "dimensions": {"member": "Member-01"}}])
```

**"Journal Entry" means the line.** ERPNext carries dimensions on `Journal Entry
Account`, never on the Journal Entry header, because one entry books to several.
Ask for `"Journal Entry"` and tool 47 wires the child table and reports the
redirection in `redirected`.

---

## 44. `create_cost_center`

**MUTATING.** Off by default. Requires the Cost Center doctype.

**Arguments:** `cost_center_name` (required), `cost_center_number`,
`parent_cost_center`, `company`, `is_group` (default false).

Docnames follow the same rule accounts do — `"<number> - <name> - <abbr>"`, or
`"<name> - <abbr>"` when unnumbered. Unlike an account number, the cost center
number really is optional.

**Refused before anything is written:** a parent that does not exist, is a leaf
rather than a group, or belongs to another company; a number already used in that
company; a docname already taken.

**Roots.** ERPNext gives every company exactly one root cost center, named
exactly after the company (`CostCenter.validate_mandatory`). Omitting
`parent_cost_center` on a company that already has one is refused and the message
names the existing root. On a company with none — a half-built site — a root can
be created, and `cost_center_name` has to equal the company.

**Example**

```json
{"name": "create_cost_center",
 "arguments": {"company": "Example Trading Co", "cost_center_number": "3200",
               "cost_center_name": "Harvest",
               "parent_cost_center": "3000 - Farm Value Chain - ETC"}}
```

```json
{
  "name": "3200 - Harvest - ETC",
  "cost_center_number": "3200",
  "cost_center_name": "Harvest",
  "parent_cost_center": "3000 - Farm Value Chain - ETC",
  "is_group": false,
  "disabled": false,
  "company": "Example Trading Co",
  "next_step": "Leaf cost center, ready to be filed against on a posting."
}
```

---

## 45. `update_cost_center`

**MUTATING.** Off by default. Requires the Cost Center doctype.

**Arguments:** `name` (required — docname, number or name), `company`,
`new_cost_center_name`, `new_cost_center_number`, `disabled`.

Renaming writes the fields and *then* moves the docname, in that order, for the
reason set out under [The docname](#the-docname-which-explains-most-of-the-design):
a Cost Center's key encodes two of its own fields and is built once, so changing
one without the other leaves the tree showing one thing and reporting another.

Unlike `update_account`, this is hand-rolled rather than delegated to ERPNext.
ERPNext's own helper handles only the *number*, and the compensating behaviour
that makes delegation matter for Account — syncing a rename down into child
companies — has no cost-center equivalent. The docname rule itself is identical,
and an in-bench test asserts a real insert produces what this app predicts.

**Refused:** a value identical to the current one (nothing to change); a number
already in use; renaming the company's root, which ERPNext requires to be named
after the company; and any attempt to reparent — this app ships no
`move_cost_center`, because reparenting moves no posting but changes which
subtotal every existing one rolls up into, for periods already reported.

**Disabling deletes nothing.** The cost center, its history and its GL entries
all remain and still appear in reports covering them; it drops out of pickers.
The response carries `gl_entries_on_this_cost_center` and a `warning` that says
so — and, for a group, that its children were **not** disabled.

---

## 46. `list_cost_centers`

**Read-only.** On by default. Requires the Cost Center doctype.

**Arguments:** `company` (required), `include_disabled` (default false).

The same nested shape `get_chart_of_accounts` returns: `children[]` is the tree,
`flat_count` is every node in the response. Disabled cost centers are left out
and counted in `disabled_count_excluded`, so "the tree looks short" always has an
answer. `default_cost_center` is the company's, under whichever fieldname this
ERPNext uses.

```json
{
  "company": "Example Trading Co",
  "cost_centers": [
    {"name": "Example Trading Co - ETC", "cost_center_name": "Example Trading Co",
     "is_group": 1, "children": [
       {"name": "Main - ETC", "cost_center_number": null, "is_group": 0, "children": []},
       {"name": "3000 - Farm Value Chain - ETC", "is_group": 1, "children": [
         {"name": "3200 - Harvest - ETC", "is_group": 0, "children": []}]}]}],
  "flat_count": 4,
  "disabled_count_excluded": 1,
  "include_disabled": false,
  "default_cost_center": "Main - ETC"
}
```

---

## 47. `create_accounting_dimension`

**MUTATING, and a schema change.** Off by default. Requires the Accounting
Dimension doctype (ERPNext v12+).

**Arguments**

| Name | Required | Notes |
| --- | --- | --- |
| `dimension_name` | yes | The label. Its scrubbed form is the fieldname: `Member` → `member`, `BBCH Stage` → `bbch_stage` |
| `master_doctype` | no | The DocType whose records are the values. Defaults to `dimension_name` |
| `create_master_if_missing` | no | **Default false.** True generates a simple custom DocType |
| `document_types` | no | Default `["Journal Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry"]` |
| `disabled` | no | Create it disabled — field added, dimension ignored |

**What it writes**, in one transaction, so a failure leaves none of it:

1. the master DocType, only when generated — `custom: 1`, so it lives entirely in
   the database, writes no files into an app and needs no developer mode. Named
   `field:dimension_value`, so the record's own name **is** the value and
   `Member-01` reads as `Member-01` everywhere it is linked. Three fields: the
   value, a description, a disabled flag.
2. the Accounting Dimension record;
3. one Link Custom Field per target doctype.

**Why the fields are written here rather than left to ERPNext.** Inserting an
Accounting Dimension makes ERPNext enqueue its own field-creation routine as a
*background job* over its own fixed list. Both halves are wrong for an MCP
caller: the next call is usually a Journal Entry that needs the field to exist
now, and the caller asked for a specific set of doctypes. ERPNext's job still
runs and still creates the rest of its list; both paths check for an existing
field first, so they do not collide.

**Refused before anything is written:** a dimension that already exists for that
label or that DocType (ERPNext allows one per DocType — its values *are* that
DocType's records, so a second would be the same dimension twice); a master that
is a Single, a child table or a core doctype; a target doctype this site does not
have; and any target that already has a field of that name which is not a Link to
this master.

**Not reversible through this app.** Removing a dimension means deleting the
record and its custom fields in the Desk.

**Example**

```json
{"name": "create_accounting_dimension",
 "arguments": {"dimension_name": "Member", "create_master_if_missing": true,
               "document_types": ["Journal Entry"]}}
```

```json
{
  "name": "Member",
  "label": "Member",
  "fieldname": "member",
  "master_doctype": "Member",
  "master_doctype_created": true,
  "disabled": false,
  "document_types_requested": ["Journal Entry"],
  "document_types_applied": ["Journal Entry Account"],
  "custom_fields_created": ["Journal Entry Account"],
  "custom_fields_already_present": [],
  "redirected": {"Journal Entry": ["Journal Entry Account"]},
  "next_step": "Add values with create_dimension_value(dimension_name='Member', value_name=…) — each one is a Member record. Then set 'member' in a journal entry line's `dimensions` object."
}
```

---

## 48. `create_dimension_value`

**MUTATING.** Off by default. Requires the Accounting Dimension doctype.

**Arguments:** `dimension_name` (required — the label, the DocType, or the
dimension's docname), `value_name` (required), `extra_fields`.

Creates one record in the DocType the dimension points at. Where that DocType
names itself from a field — which is how the masters tool 47 generates work, and
how ERPNext's own dimension masters work — `value_name` becomes both the field
and the docname. Where it names itself some other way, the value is created
anyway and the response reports the name it actually got, with a note.

Three ways to name the dimension because the Accounting Dimension record's own
docname is a version detail, and a caller who created it through this app knows
it by the label it asked for.

**Refused:** an unknown dimension (the message lists the ones this site has); a
dimension whose DocType is missing; a record of that name already present; an
`extra_fields` key the master does not have — a typo, not something to ignore.

```json
{"name": "create_dimension_value",
 "arguments": {"dimension_name": "Member", "value_name": "Member-01",
               "extra_fields": {"description": "Active since 2026-01-01"}}}
```

```json
{
  "name": "Member-01",
  "requested_name": "Member-01",
  "dimension": "Member",
  "dimension_record": "Member",
  "master_doctype": "Member",
  "named_by": "field:dimension_value",
  "extra_fields": {"description": "Active since 2026-01-01"},
  "note": "Ready to use: set it on a journal entry line's `dimensions` object."
}
```

---

## 49. `set_company_defaults`

**MUTATING.** Off by default.

**Arguments:** `company` (required), `defaults` (required) — an object of company
field → account. Accounts resolve three ways, as everywhere else. An empty string
clears a field.

**Supported keys**

| Key | Has to be | Because |
| --- | --- | --- |
| `default_receivable_account` | Receivable, Asset | ERPNext keys customer balances off `account_type` |
| `default_payable_account` | Payable, Liability | Same, for suppliers |
| `default_cash_account` | Cash, Asset | |
| `default_bank_account` | Bank, Asset | |
| `default_income_account` | Income | Sales lines with no account of their own |
| `default_expense_account` | Expense | Purchase lines with no account of their own |
| `cost_of_goods_sold_account` | Expense | Stock consumed against a sale |
| `round_off_account` | Expense or Income | The cent a rounded total leaves behind |
| `exchange_gain_loss_account` | Expense or Income | |
| `write_off_account` | Expense or Income | |
| `default_deferred_revenue_account` | Liability or Income | |
| `default_deferred_expense_account` | Asset or Expense | |
| `round_off_cost_center` | a **leaf Cost Center** | Where the rounding difference is filed |
| `disposal_account` | Income or Expense | ERPNext refuses to scrap or sell an Asset without it — and says so *from the Asset* |
| `capital_work_in_progress_account` | Capital Work in Progress, Asset | An asset being built, before it is in service |
| `expenses_included_in_asset_valuation` | Expense | Freight and duty that belong in an asset's cost, not the period |
| `asset_received_but_not_billed` | Asset Received But Not Billed, Liability | An asset delivered before its invoice |
| `stock_adjustment_account` | Expense | The difference a stock count found |
| `stock_received_but_not_billed` | Stock Received But Not Billed, Liability | Stock delivered before its invoice |
| `unrealized_exchange_gain_loss_account` | Income or Expense | Movement on an unsettled foreign-currency balance |
| `unrealized_profit_loss_account` | Income or Expense | Intra-group profit eliminated on consolidation |
| `default_advance_received_account` | Receivable, **Liability** | Money held for a customer is a liability, keyed so the party ledger picks it up |
| `default_advance_paid_account` | Payable, **Asset** | The mirror image, for money paid out early |
| `default_operating_cost_account` | Expense | What a production order adds to what it makes |
| `default_selling_cost_center` | a **leaf Cost Center** | Where a sale is filed when the document does not say |
| `default_buying_cost_center` | a **leaf Cost Center** | Same, for a purchase |

**Type-checked, not merely existence-checked.** ERPNext would accept a
`default_receivable_account` pointed at a plain Asset account and then produce
invoices that post but never age — and the symptom appears a quarter later with
nothing to point at. The check is cheap; the failure it prevents is not.

**Also refused:** a group account, a disabled account, an account belonging to
another company, a group cost center, an unsupported key (by name), and a key
this ERPNext version's Company does not have.

**Nothing is written unless every value validates**, so a partially-correct call
leaves the company exactly as it was.

**Idempotent.** Every field is compared before it is written; the response
separates `changed` from `unchanged`. That matters more than usual here, because
`Company.save` is not a cheap write — ERPNext's `on_update` walks the company
tree — and these are the fields a caller is most likely to set twice while
working out a chart.

```json
{"name": "set_company_defaults",
 "arguments": {"company": "Example Trading Co",
               "defaults": {"default_receivable_account": "1200",
                            "default_payable_account": "2100",
                            "round_off_cost_center": "Main"}}}
```

```json
{
  "company": "Example Trading Co",
  "changed": {
    "default_receivable_account": ["", "1200 - Accounts Receivable - ETC"],
    "round_off_cost_center": ["", "Main - ETC"]
  },
  "unchanged": ["default_payable_account"],
  "defaults_now": {
    "default_receivable_account": "1200 - Accounts Receivable - ETC",
    "default_payable_account": "2100 - Accounts Payable - ETC",
    "round_off_cost_center": "Main - ETC"
  },
  "idempotent": true,
  "note": "Company defaults decide which account a document reaches for when nothing on the document says. They do not touch a single existing posting — every document already written keeps the accounts it was written with."
}
```

A mismatch reads like this, and writes nothing:

```
default_receivable_account has to point at an account whose account_type is Receivable; '1100 - Cash - ETC' is Cash. ERPNext keys customer balances off that flag, not off the account's name or number, so this would post and then fail to reconcile. Fix it with update_account(new_account_type=…) first. Nothing was changed.
```

---

# Cap table, member events and governance

Ten tools for the things a family business holds for a generation: who owns it,
what happened to their interest, and which paper says so.

**The idea everything here rests on.** A chart of accounts and a cost center tree
are read by everyone who touches the books — a bookkeeper, a lender, an auditor,
a model summarising the year. A family name in either one leaks into every export
and cannot be taken out of a statement that has already been sent.

So a posting is tagged with an anonymous **Member accounting dimension** value,
and exactly one doctype says who that is:

```
Journal Entry line   →  3100 Member Capital, member = Member-01
Cap Table Entry      →  Member-01 = The Example Family Trust, admitted 2020-06-15, 60%
```

Read access to the ledger and read access to the mapping are then two different
grants. `list_cap_table` is the tool that joins them, which is why it has its own
switch.

**Members are a dimension, not a cost center.** Cost centers answer "which part
of the operation did this belong to", and a member is not a part of the
operation. `Cap Table Entry` keeps an optional `member_cost_center` for sites
whose convention already gives each member one, but every tool here files by the
dimension and carries the cost center along.

**Order of operations on a fresh site:**

```
create_accounting_dimension(dimension_name="Member", create_master_if_missing=true)
create_dimension_value(dimension_name="Member", value_name="Member-01")
create_cap_table_entry(member_id="Member-01", legal_entity_name="…", …)
record_member_event(event_type="Contribution", member="Member-01", …)
submit_member_event(name=…)          ← needs allow_submit_journal_entry too
```

`create_cap_table_entry` refuses a member id that is not already a dimension
value, so the first two steps cannot be skipped by accident. A site with no
Member dimension at all is allowed to build the register first, and is told so.

---

## 50. `list_cap_table`

Read-only, on by default. Requires the Cap Table Entry doctype (ships with this
app; run `bench migrate`).

**Arguments:** `company` (required), `include_retired` (default **true**).

Retired members are included by default because the postings they are tagged on
do not disappear when they leave.

```json
{
  "company": "Example Trading Co",
  "members": [
    {
      "name": "Member-01 - ETC",
      "member_id": "Member-01",
      "legal_entity_name": "The Example Family Trust",
      "entity_type": "Trust",
      "admission_date": "2020-06-15",
      "withdrawal_date": null,
      "ownership_percentage": 60.0,
      "retired": false,
      "member_cost_center": null
    }
  ],
  "count": 2, "active_count": 2, "retired_count": 0,
  "active_ownership_total": 100.0,
  "ownership_balances": true,
  "member_dimension": "Member"
}
```

`ownership_balances` false adds a `warning` naming the total. It is a warning
rather than a refusal: mid-transition is a real state.

---

## 51. `list_member_events`

Read-only, on by default.

**Arguments:** `company` (required), `member`, `event_type`, `from_date`,
`to_date`, `include_superseded` (default true), `limit`.

`member` takes a Cap Table Entry docname or a bare `member_id`. Legal names are
resolved from the register; the events themselves hold only the anonymous id.
`totals_by_event_type` sums what was returned — an event with `superseded_by`
set has been corrected by a later one and must not be counted twice.

---

## 52. `list_governance_documents`

Read-only, on by default.

**Arguments:** `company` (required), `category`, `include_superseded` (default
true), `limit`.

`operative` is true for a document nothing has superseded. Those are the ones in
force; the rest are history, and the archive keeps both.

---

## 53. `get_governance_document_content`

Read-only, on by default.

**Arguments:** `name` (required), `file`, `max_bytes`.

Returns the document's metadata, its place in the amendment chain, and the
attachment's bytes under `content`, in the same shape `get_attachment_content`
returns. Read permission on the Governance Document is enforced before anything
comes back, and the same 2 MB default / 8 MB hard cap applies. An entry with
several attachments returns the first and says so; an entry with none returns
its metadata with `content: null`.

---

## 54. `create_cap_table_entry`

**MUTATING.** Off by default.

**Arguments:** `company`, `member_id`, `legal_entity_name`, `entity_type`,
`admission_date` (all required), `ownership_percentage`, `member_cost_center`,
`member_dimension`, `notes`.

`entity_type` is checked against the doctype's own option list: Individual,
Trust, LLC, Corporation, Partnership, Other.

**Refuses**, writing nothing:

- a member id already registered for that company, naming the existing entry;
- a percentage outside 0–100;
- a member id that is not a value of the site's Member dimension, naming
  `create_dimension_value` as the remedy;
- `retired` or `withdrawal_date` — a member cannot be created already gone.

The docname is `"<member id> - <company abbr>"`, which is the key
`record_member_event` and everything else resolves.

---

## 55. `update_cap_table_entry`

**MUTATING.** Off by default. Idempotent in the sense that a call which would
change nothing is refused rather than reported as a success.

**Arguments:** `member` (required), `company`, `legal_entity_name`,
`entity_type`, `admission_date`, `ownership_percentage`, `member_cost_center`,
`notes`.

**Deliberately cannot do two things.** It cannot retire a member — that is tool
56, so an exit reaches the event trail rather than appearing only as a changed
checkbox. And it cannot change the `member_id`: that is the key every posting is
tagged with, so changing it would leave journal entry lines pointing at a member
that no longer exists.

---

## 56. `close_cap_table_entry`

**MUTATING.** Off by default.

**Arguments:** `member`, `withdrawal_date`, `notes` (all required), `company`.

Sets the withdrawal date, marks the entry retired, appends the reason to its
notes, and writes a `Withdrawal` Member Event carrying `notes` as the narrative.

**Moves no money.** A member leaving usually involves a final distribution, and
that is a separate `record_member_event` call with its own amount, accounts and
narrative. Bundling them would make the tool that closes a member also a tool
that can pay one.

Refuses a member already retired, a withdrawal date before the admission date,
and a placeholder reason.

---

## 57. `record_member_event`

**MUTATING.** Off by default.

**Arguments:** `company`, `event_type`, `effective_date`, `member`, `narrative`
(all required), `amount`, `counterparty_member`, `offset_je`, `capital_account`,
`counter_account`, `member_dimension`.

Always writes a Member Event. For the five types that book money it also writes
a **DRAFT** Journal Entry, unless `offset_je` names one that already does:

| `event_type` | Debit | Credit |
| --- | --- | --- |
| `Contribution` | the cash side | member capital |
| `Distribution` | member distributions | the cash side |
| `Withdrawal` | member distributions | the cash side |
| `Transfer` | capital of `member` | capital of `counterparty_member` |
| `Reallocation` | capital of `member` | capital of `counterparty_member` |
| `Admission` | — nothing is posted — | |

**Every line carries the member dimension, including the cash side.** Tagging
only the equity line makes a balance sheet filtered by member fail to balance,
and the first person to notice is usually an auditor. A transfer tags its two
lines with the two different members: same account, money never leaving the
company.

**Accounts are shortlisted, never guessed.** With no `capital_account`, the
company's leaf Equity accounts are matched by name — `member capital`,
`partner capital`, `capital contribution` for the capital side; `distribution`,
`draw` for a distribution. Zero matches or more than one is refused with the
candidates listed. The cash side falls back to the company's
`default_bank_account`, then `default_cash_account`.

```json
{
  "name": "8f3c…", "event_type": "Contribution", "amount": 25000.0,
  "member": "Member-01 - ETC", "member_id": "Member-01",
  "offset_je": "ACC-JV-2026-00042",
  "journal_entry_created": true,
  "journal_entry_lines": [
    {"account": "1110 - Bank Checking - ETC", "debit": 25000.0, "credit": 0, "member": "Member-01"},
    {"account": "3100 - Member Capital - ETC", "debit": 0, "credit": 25000.0, "member": "Member-01"}
  ],
  "accounts_used": {
    "capital_account": "3100 - Member Capital - ETC",
    "resolved_by": "name match",
    "counter_account": "1110 - Bank Checking - ETC"
  },
  "next_step": "The Journal Entry ACC-JV-2026-00042 is a DRAFT and has moved no balance. …"
}
```

**Refuses** a narrative too short to be an explanation, a negative amount (a
distribution is its own event type, not a contribution with a minus sign), a
posting event with no amount, a transfer with no counterparty, an `offset_je`
from another company, and a site with no Member dimension on `Journal Entry
Account`.

---

## 58. `submit_member_event`

**MUTATING.** Off by default. **This moves balances.**

**Arguments:** `name` (required).

**Checks two switches.** Its own, and `allow_submit_journal_entry`. That second
switch is where an operator decided whether an AI client may post at all, and a
second door into the same room with a different lock would make the decision
meaningless. With it off:

```
posting this event means submitting Journal Entry ACC-JV-2026-00042, and the submit_journal_entry tool is switched off on this site. That switch is where an operator decides whether an AI client may move a balance, so this tool honours it too. An operator must tick 'allow_submit_journal_entry' in ERPNext MCP Settings. Nothing was changed.
```

An event that books no money — an admission, a reallocation of percentages — has
nothing to post and is refused with that said.

---

## 59. `attach_governance_document`

**MUTATING.** Off by default.

**Arguments:** `company`, `category`, `title` (required), `effective_date`,
`execution_date`, `supersedes`, `file_content`, `file_name`, `file_url`,
`parties`, `notes`.

`category` is one of Operating Agreement, Trust Document, Advisory Agreement,
Board Resolution, Prior Statement, Amendment, Lease, Tax Filing, Other.

**Content.** `file_content` is base64 of the document's bytes (no `data:`
prefix) and needs `file_name`; it is stored as a **private** File attached to
the record, readable back through tool 53. `file_url` instead records where an
externally hosted document lives without copying it. The two together are
refused. There is an 8 MB ceiling on content moved through a tool call.

**The chain.** `supersedes` writes the link in both directions — the older
document's `superseded_by` is filled in — so a reader can follow the chain
forward to whatever is current. Superseding a document that has already been
superseded is refused: an amendment goes on the end of the chain, not into the
middle. The doctype's controller separately refuses a cycle, walking the whole
chain rather than checking one hop.

A second document with the same company, category and title is refused, because
two entries claiming to be the same operating agreement is worse than none.

---

# Assets and depreciation

Five tools for assets that serve more than one part of the business, and assets
that are financed. They **layer on** ERPNext's Asset doctype rather than
replacing it.

**What ERPNext does not have.** A cost split — a tractor is not a Harvest asset
or a Perennial Care asset, it is 40% one and 60% the other. And note-tenor
discipline — when an asset is financed, the month the note is paid off and the
month it is fully depreciated should be the same month, and nothing enforces
that.

**Where this app keeps it.** In an `Asset Cost Profile`, one per Asset, not in
custom fields on ERPNext's Asset. Installing this app must change the behaviour
of nothing already on the site, and uninstalling it must give the site back;
grafting fields onto ERPNext's own doctype breaks both. An asset created here is
an ordinary ERPNext Asset an operator can open, edit and delete without knowing
this app exists.

**The most important line in the feature.** `create_asset` sets
`calculate_depreciation = 0` on the Asset. ERPNext runs a daily scheduled job
that posts depreciation for every asset with that flag set, using its own
schedule and its own single cost center. If it also ran, the asset would
depreciate twice — silently, monthly. This app owns the schedule for the assets
it creates; `run_depreciation_cycle` is the only thing that writes for them.

An asset you created in the Desk is untouched by any of this: it has no profile,
so these tools refuse it and ERPNext keeps depreciating it exactly as before.

---

## 60. `depreciation_note_alignment_check`

Read-only, on by default. Requires ERPNext's Asset doctype.

**Arguments:** `company` (required), `as_of` (default today).

```json
{
  "company": "Example Trading Co", "as_of": "2026-07-01",
  "assets": [
    {
      "asset": "ACC-ASS-2026-00003",
      "linked_note": "NOTE-0007", "linked_note_doctype": "Notes Payable",
      "useful_life_months": 84, "note_tenor_months": 60,
      "months_elapsed": 6,
      "remaining_depreciation_months": 78, "remaining_note_months": 54,
      "delta_months": 24, "aligned": false,
      "reading": "The asset still has 24 month(s) of depreciation left after the note is paid off — book value outlives the financing."
    }
  ],
  "checked": 1, "diverged_count": 1, "assets_without_a_note": ["ACC-ASS-2026-00001"]
}
```

Reports every financed asset, not only the broken ones, because "nothing is
wrong" is an answer somebody has to be able to see. A divergence is not
automatically an error — it is something that needs an explanation, and an
explanation nobody wrote down is what this surfaces.

---

## 61. `create_asset`

**MUTATING.** Off by default. Requires ERPNext's Asset doctype.

**Arguments:** `asset_name`, `item_code`, `asset_category`, `purchase_date`,
`purchase_amount`, `useful_life_months` (required), `company`, `salvage_value`,
`depreciation_frequency_months` (default 1), `depreciation_method` (default
Straight Line), `depreciation_start_date`, `cost_center_allocation`,
`linked_note`, `note_doctype`, `note_tenor_months`, `note_maturity_date`,
`depreciation_expense_account`, `accumulated_depreciation_account`, `location`,
`create_item_if_missing` (default true), `notes`.

```json
{
  "company": "Example Trading Co",
  "asset_name": "Tractor A",
  "item_code": "TRACTOR-A",
  "asset_category": "Farm Equipment",
  "purchase_date": "2026-01-01",
  "purchase_amount": 84000,
  "salvage_value": 12000,
  "useful_life_months": 84,
  "cost_center_allocation": [
    {"cost_center": "Harvest", "percentage": 40, "note": "hour meter, 2025 season"},
    {"cost_center": "Perennial Care", "percentage": 60}
  ],
  "linked_note": "NOTE-0007",
  "note_tenor_months": 84
}
```

**Writes** an ERPNext Asset (a **draft** — submit it in ERPNext when the purchase
is real), an Asset Cost Profile, and a fixed-asset Item when `item_code` does not
exist yet. The Asset's own `cost_center` is set to the largest share, so anything
ERPNext files against it lands somewhere sane.

**Refuses**, writing nothing: an allocation that does not total 100 (a 99% asset
under-depreciates the business for the rest of its life); a group or disabled
cost center; the same cost center twice; a frequency that does not divide the
useful life exactly; a salvage value at or above the cost; an asset category the
site does not have; an existing Item not flagged `is_fixed_asset` (flipping that
on an item with stock movements is an inventory decision, not an asset one); a
`bbch_stage` the site has no dimension for; and a `linked_note` whose tenor
differs from `useful_life_months`.

**Depreciation methods.** Straight Line, Written Down Value, Double Declining
Balance, Manual. The last period absorbs the rounding so the asset lands exactly
on its salvage value. Written Down Value with a salvage value of 0 is refused
rather than fudged — the rate `1 - (salvage/cost)^(1/n)` is undefined, because a
declining balance never reaches nought. `Manual` means this app computes nothing
for the asset.

---

## 62. `update_asset_allocation`

**MUTATING.** Off by default.

**Arguments:** `asset`, `new_cost_center_allocation` (required), `company`.

Replaces the split. **Not retroactive**, and that is correct: depreciation
already written keeps the split it was written with, because that is the
history, and rewriting it would change periods already reported. The response
carries `previous_allocation` and how many periods have already been written.

Refuses a total that is not 100, and refuses a change that would leave the
allocation exactly as it is.

---

## 63. `link_asset_to_note`

**MUTATING.** Off by default.

**Arguments:** `asset`, `note_doc_ref` (required), `note_doctype`,
`note_tenor_months`, `note_maturity_date`, `enforce_tenor` (default **true**),
`company`.

The tenor is taken from `note_tenor_months`, from `note_maturity_date`, or from
the note document's own maturity or term field where its doctype has one — and
`tenor_source` says which. `note_doctype` is worked out from the name where the
note is a Notes Payable, a Loan or a Journal Entry.

With `enforce_tenor` true, a mismatch is refused:

```
Asset ACC-ASS-2026-00003 depreciates over 84 month(s) but the note runs 60 — a 24-month divergence. Held apart, the asset is either fully depreciated while payments continue, or still on the books after the note is paid; either way the matching principle is broken and the mismatch is invisible until the final year. …
```

`enforce_tenor=false` links anyway and records the divergence, which tool 60
will keep reporting.

---

## 64. `run_depreciation_cycle`

**MUTATING.** Off by default. **`dry_run` defaults to TRUE.**

**Arguments:** `company` (required), `period_end` (default today), `asset`,
`dry_run`.

One **DRAFT** Journal Entry per asset per period: debit depreciation expense
split across the asset's cost centers, credit accumulated depreciation in one
line, posted on the period's end date. Accounts come from the profile if set,
otherwise from the Asset Category's row for that company.

```json
{
  "company": "Example Trading Co", "period_end": "2026-03-31", "dry_run": true,
  "periods": [
    {
      "asset": "ACC-ASS-2026-00003", "period_index": 1,
      "period_start": "2026-01-01", "period_end": "2026-01-31", "amount": 857.14,
      "lines": [
        {"account": "5200 - Depreciation - ETC", "debit": 342.86, "cost_center": "Harvest - ETC"},
        {"account": "5200 - Depreciation - ETC", "debit": 514.28, "cost_center": "Perennial Care - ETC"},
        {"account": "1810 - Accumulated Depreciation - ETC", "credit": 857.14, "cost_center": "Harvest - ETC"}
      ]
    }
  ],
  "period_count": 3, "total_depreciation": 2571.42,
  "journal_entries": [], "assets_skipped": [],
  "note": "DRY RUN — nothing was written. …"
}
```

- **Idempotent by record.** Every period written is stored on the profile with
  the entry that carries it, so a second run cannot repeat one. Amounts are
  computed from the profile each time rather than read back from saved rows, so
  a catch-up over eleven missed months produces exactly what month-by-month
  running would have.
- **The split adds up.** The last debit absorbs the rounding: 33.33 / 33.33 /
  33.34 of 1000 is three debits totalling exactly 1000. An entry that does not
  balance is not a rounding problem, it is a refused save.
- **Nothing is posted.** The entries are drafts; `submit_journal_entry` posts
  them.
- One misconfigured asset does not take the run down. Assets on the Manual
  method, assets with nothing due, and assets whose depreciation accounts are
  not configured are listed in `assets_skipped` with the reason.

---

## 65. `list_notes_payable`

**Read-only.** On by default. Needs the `Note Payable` DocType, which ships with
this app — run `bench migrate` after upgrading.

**Arguments:** `company` (or `borrower`, same thing), `status`, `include_closed`
(default true), `limit`.

```json
{
  "company": "Example Trading Co",
  "notes": [
    {
      "name": "Example Bank - Defect Sorter - ETC",
      "note_name": "Example Bank - Defect Sorter",
      "borrower": "Example Trading Co", "lender": "Example Bank",
      "status": "Active",
      "principal_original": 120000.0, "principal_outstanding": 110000.0,
      "interest_rate": 6.5, "interest_type": "Fixed",
      "origination_date": "2026-01-01", "maturity_date": "2027-01-01",
      "payment_frequency": "Monthly", "payment_amount": 10650.0,
      "linked_gl_account": "2310 - Notes Payable - ETC",
      "interest_expense_account": "5300 - Interest Expense - ETC",
      "related_asset": "ACC-ASS-2026-00003",
      "payment_count": 1, "last_payment_date": "2026-02-01",
      "next_payment_date": "2026-03-01", "closed": false
    }
  ],
  "count": 1, "active_count": 1,
  "total_original_principal_active": 120000.0,
  "total_outstanding_active": 110000.0,
  "note": "Outstanding balances are the figure maintained by record_loan_payment, not the balance of the linked GL account. …"
}
```

- **`principal_outstanding` is not the ledger.** It is maintained by
  `record_loan_payment`, and diverges from the account by every payment recorded
  as a draft nobody has posted — which in this app is the normal state.
  `get_account_balance` on `linked_gl_account` is the ledger's answer.
- **`next_payment_date` is a projection**, not a schedule the lender agreed to:
  it is the frequency applied to the last payment recorded, clamped to the
  maturity date. A `Balloon` note projects its maturity and nothing else; a
  `Custom` one projects nothing.
- Closed notes are listed by default. A note that has been paid off is part of
  the history.

---

## 66. `create_note_payable`

**MUTATING.** Off by default. Needs the `Note Payable` DocType.

**Arguments:** `note_name` (required), `lender` (required), `principal_original`
(required), `origination_date` (required), `borrower`/`company`,
`principal_outstanding` (defaults to the original), `interest_rate`,
`interest_type`, `maturity_date`, `payment_frequency`, `payment_amount`,
`linked_gl_account`, `interest_expense_account`, `related_asset`,
`enforce_asset_tenor` (default true), `document_reference`, `notes`.

The docname is `"<note_name> - <company abbr>"`, and `note_name` is unique per
borrower.

```json
{"name": "create_note_payable",
 "arguments": {"borrower": "Example Trading Co",
               "note_name": "Example Bank - Defect Sorter",
               "lender": "Example Bank",
               "principal_original": 120000,
               "origination_date": "2026-01-01",
               "maturity_date": "2027-01-01",
               "interest_rate": 6.5,
               "linked_gl_account": "2310",
               "interest_expense_account": "5300",
               "related_asset": "ACC-ASS-2026-00003"}}
```

- **Not ERPNext's Loan module.** That models the company as the *lender*, with an
  application, a disbursement and half a dozen doctypes. This is the other side.
- **`related_asset` runs the tenor check.** It delegates to `link_asset_to_note`,
  so an asset whose useful life does not equal the note's term is refused by the
  same code that refuses it from the other direction. Pass
  `enforce_asset_tenor=false` when the divergence is deliberate. The note and the
  link are one transaction — a refused link leaves no note behind.
- **Refuses:** a duplicate name for the same borrower, a non-positive principal, a
  negative outstanding balance, a maturity before origination,
  `interest_type: "Zero"` with a non-zero rate, a `linked_gl_account` that is not
  a plain Liability (a Payable- or Receivable-typed one would show the note's
  principal as a party balance that never ages out), an
  `interest_expense_account` that is not an Expense, and any attempt to create a
  note already closed.

---

## 67. `record_loan_payment`

**MUTATING.** Off by default. Needs the `Note Payable` DocType.

**Arguments:** `note` (required), `payment_date` (required), `total_amount`
(required), `offset_bank_account` (required), `principal_split`,
`interest_split`, `company`, `notes_payable_account`,
`interest_expense_account`, `narrative`.

Pass `principal_split`, `interest_split`, or one and let the other be derived.
They have to add up to `total_amount` or nothing is written.

```json
{
  "note": "Example Bank - Defect Sorter - ETC", "payment_date": "2026-02-01",
  "total_amount": 10650.0, "principal_split": 10000.0, "interest_split": 650.0,
  "principal_outstanding_before": 120000.0, "principal_outstanding_after": 110000.0,
  "journal_entry": "ACC-JV-2026-00051",
  "accounts_used": {
    "notes_payable_account": "2310 - Notes Payable - ETC",
    "interest_expense_account": "5300 - Interest Expense - ETC",
    "offset_account": "1110 - Bank Checking - ETC",
    "offset_bank_account": "Operating - Example Bank"
  },
  "lines": [
    {"account": "2310 - Notes Payable - ETC", "debit": 10000.0, "credit": 0},
    {"account": "5300 - Interest Expense - ETC", "debit": 650.0, "credit": 0},
    {"account": "1110 - Bank Checking - ETC", "debit": 0, "credit": 10650.0}
  ],
  "note_text": "Journal Entry ACC-JV-2026-00051 is a DRAFT and has moved no balance. …"
}
```

- **The split is the whole job.** A payment leaving a bank account is one number
  whose halves land in completely different places. Booked as a single line
  against the liability, the year's interest expense reads as nil and the balance
  sheet says the note was paid down by more than it was.
- **`offset_bank_account` takes either** a Bank Account record — preferred, since
  the journal line then carries it, which is what lets a bank reconciliation
  match this entry — or the GL account directly.
- **The entry is a DRAFT.** The note's outstanding figure is decremented
  immediately, so until it is submitted the record and the liability account
  disagree by the principal. The response says so every time.
- **Refuses:** a closed note, a payment dated before origination, a principal
  component larger than the balance outstanding, a negative component, a split
  that does not add up, and an interest component with no expense account to put
  it in.

---

## 68. `close_note_payable`

**MUTATING.** Off by default. Needs the `Note Payable` DocType.

**Arguments:** `note` (required), `disposition` (required — `Paid Off`,
`Refinanced` or `Written Off`), `disposition_date` (required), `narrative`
(required), `company`, `superseded_by`, `zero_remaining_balance`.

```json
{"name": "close_note_payable",
 "arguments": {"note": "Example Bank - Defect Sorter - ETC",
               "disposition": "Written Off",
               "disposition_date": "2026-06-30",
               "narrative": "Forgiven under the 2026 family settlement deed."}}
```

- **Writes NO journal entry, deliberately.** Relieving a written-off balance is a
  posting with real tax consequences — forgiven debt is usually income — and a
  refinance moves a balance between two liability accounts. Both belong to
  somebody who meant them. The response names the account still carrying the
  balance and the entry that is owed, so the omission cannot pass unnoticed.
- **`Paid Off` with a balance still showing is refused.** That means either a
  final payment was never recorded (`record_loan_payment` writes the entry that
  books it) or the balance carried here is stale. If it is stale,
  `zero_remaining_balance=true` writes it down and records an `Adjustment` row in
  the note's history saying exactly that.
- **`superseded_by`** names the note that replaced this one, for a refinance, so a
  reader following the chain forward lands on what is still owed. It is only
  accepted on a `Refinanced` disposition.
- Also refuses a note already closed, a disposition date before origination, and
  a narrative too short to be an explanation.

---

## 69. `set_opening_balance`

**MUTATING.** Off by default.

**Arguments:** `posting_date` (required), `entries` (required), `user_remark`
(required), `company`, `opening_equity_account`.

Each entry is `{account, dr_or_cr, amount, cost_center, dimensions, narrative}`.
`amount` is always positive — the direction lives in `dr_or_cr` (`dr`/`debit`/`d`
or `cr`/`credit`/`c`). **Do not include the equity line; it is computed.**

```json
{"name": "set_opening_balance",
 "arguments": {"company": "Example Trading Co",
               "posting_date": "2026-01-01",
               "user_remark": "Equipment transferred in on dissolution, per the bill of sale",
               "entries": [
                 {"account": "1710", "dr_or_cr": "dr", "amount": 52650,
                  "narrative": "Two forklifts and a sprayer"}]}}
```

```json
{
  "name": "ACC-JV-2026-00060", "docstatus": 0, "docstatus_label": "draft",
  "company": "Example Trading Co", "posting_date": "2026-01-01",
  "opening_equity_account": "3300 - Opening Balance Equity - ETC",
  "opening_equity_resolved_by": "account_number 3300",
  "opening_equity_side": "credit", "opening_equity_amount": 52650.0,
  "entered_debit": 52650.0, "entered_credit": 0.0, "balancing_difference": 52650.0,
  "line_count": 2, "total_debit": 52650.0, "total_credit": 52650.0,
  "flags_set": {"is_opening": "Yes", "voucher_type": "Opening Entry"},
  "note": "The 52650.0 offsetting line against 3300 - Opening Balance Equity - ETC was computed, not supplied …"
}
```

- **The plug is computed, not supplied.** Every historical fact brought onto a set
  of books balances against opening equity; a caller who works that out for
  itself gets it wrong by a few cents on the third event, after which the ledger
  never balances again.
- **The flags matter.** `is_opening` — and `Opening Entry` where the site's
  Journal Entry offers that voucher type — are what keep these amounts out of the
  period's activity in every report that separates the two. Nothing warns you
  when they are missing; the P&L simply reads as though the company earned its
  opening equity in January. Both are set only where this site's own meta has
  them, and `flags_set` reports what was actually written.
- **The equity account is found, not guessed:** account number `3300` first, then
  a leaf Equity account named after opening balances. Zero matches and more than
  one are both refusals, with the company's leaf equity accounts listed.
  `opening_equity_account` overrides it.
- **Entries that already balance get no plug at all**, and the response says the
  equity account was not touched.
- **Refuses** a group, disabled or wrong-company account on any line; a group or
  disabled cost center; a dimension value that does not exist; a non-positive
  amount; an unsupported entry field, by name; and an
  `opening_equity_account` that is not Equity. Nothing is written unless every
  line validates.
- **It is a DRAFT.** `submit_journal_entry` posts it — and an opening balance is
  the entry most worth reading first, because it is the one nobody will ever
  re-derive.

---

## 70. `create_bank_account`

**MUTATING.** Off by default. Needs the `Bank Account` DocType, which ships with
ERPNext's Accounts module.

**Arguments:** `account_name` (required), `bank_name` (required), `account`
(required for a company account), `company`, `account_no` /`bank_account_no`,
`iban`, `is_company_account` (default true), `party_type`, `party`, `disabled`.

ERPNext names the record `"<account_name> - <bank>"`. That string is what goes
into a bank feed's configuration, so it is worth choosing `account_name`
deliberately — the account mask is the usual way to tell two accounts at one bank
apart.

```json
{"name": "create_bank_account",
 "arguments": {"company": "Example Trading Co",
               "account_name": "Advisors Cash - ••3158",
               "bank_name": "Example Bank Advisors",
               "account": "1151",
               "account_no": "••3158"}}
```

- **A Bank Account holds no balance.** It is a mapping: this institution, this
  account number, posts to that GL account. Bank Transactions hang off it,
  reconciliation reads it, a feed writes into it. The money lives in the Account
  it points at.
- **Pre-create it before the first sync.** A feed that runs first makes its own,
  named whatever the feed calls the account and pointed at a GL account the feed
  picked. Renaming that afterwards is fine; *repointing* it is not — once
  transactions have been imported, the GL account named here is where they
  reconcile to.
- **Two doctypes, one transaction.** The `Bank` is created when the institution is
  new, and a failure anywhere after that leaves neither.
- **Refuses:** an unknown company; a GL account that does not exist, belongs to
  another company, is a group or is disabled; a GL account whose `root_type` is
  neither Asset (a bank account) nor Liability (a credit card); an **Asset**
  account whose `account_type` is not `Bank` or `Cash`, because ERPNext's own
  account picker and its reconciliation tool both filter on that flag and an
  untyped account saves here and then cannot be reconciled; an `account_name`
  already used in this company; `party`/`party_type` together with
  `is_company_account`; and a `bank_name` Frappe would refuse as a docname.
- **Warns**, rather than refuses, when the GL account is already another Bank
  Account's — legitimate for a sweep arrangement, a mistake everywhere else.

---

## 71. `delete_account`

**MUTATING, DESTRUCTIVE, IRREVERSIBLE.** Off by default. There is no undo, no
draft and no cancelled state; the record is gone.

**Arguments:** `name` (required), `company`, `force_check_gl_entries`,
`force_check_children`, `force_check_company_defaults`,
`force_check_bank_accounts` — all four checks default to **true**.

```json
{
  "deleted": "1190 - Cash Clearing - ETC",
  "account": {"name": "1190 - Cash Clearing - ETC", "account_number": "1190", "…": "…"},
  "checks_passed": {
    "gl_entries": "no GL entries, ever, and no journal entry line references it",
    "children": "no child accounts, enabled or disabled",
    "company_defaults": "no Company field points at it",
    "bank_accounts": "no Bank Account record posts to it"
  },
  "checks_skipped": [], "was_root": false,
  "note": "Gone. Unlike disable_account there is nothing left: no record, no history, and the account number 1190 is free …"
}
```

- **Prefer `disable_account`.** Almost always. Disabling keeps the postings, the
  reports still balance, and the account drops out of pickers.
- **The one thing disabling cannot do is free the number.** A disabled account
  still holds it, and on a company being renumbered onto a real chart — fifty
  accounts a bundled chart created that nobody ever posted to — that is the
  entire problem. That is what this tool is for.
- **Every check is a refusal, and they are all reported at once.** Four calls each
  naming one reason is how somebody deletes the wrong account trying to satisfy
  the last one.
- **Draft journal entry lines count.** A draft writes no GL row, so an account
  referenced by one reads as untouched; deleting it leaves a draft nobody can
  submit and nobody can fix.
- **Turning a check off does not make a referenced account deletable.** Frappe's
  own link-integrity check still runs on the delete. The flag changes which error
  you get, not the outcome.


---

## 72. `create_fiscal_year`

**MUTATING.** Off by default. Needs the `Fiscal Year` DocType, which ships with
ERPNext's Accounts module.

**Arguments:** `year_name` (required), `year_start_date` (required),
`year_end_date` (required), `companies`, `is_short_year`, `disabled`,
`auto_created`.

**This is the prerequisite for booking anything historical.** ERPNext refuses a
posting whose date falls outside a fiscal year, and it refuses it from inside the
document being saved — so on a site whose only year is 2026, a March 2025
equipment transfer fails with an error about a *date* rather than about a missing
*year*. `set_opening_balance` and `create_journal_entry` cannot reach that period
until this has run.

```json
{"name": "create_fiscal_year",
 "arguments": {"year_name": "2025",
               "year_start_date": "2025-01-01",
               "year_end_date": "2025-12-31"}}
```

```json
{
  "name": "2025", "year": "2025",
  "year_start_date": "2025-01-01", "year_end_date": "2025-12-31",
  "disabled": false, "is_short_year": false, "auto_created": false,
  "companies": [], "scope": "every company on this site",
  "expected_end_date_for_a_full_year": "2025-12-31",
  "note": "A Fiscal Year is a permission for a date, not a posting. Nothing was booked and no balance moved …",
  "next_step": "Historical events for this period can now be booked. …"
}
```

- **`companies` is optional, and omitting it is not an omission.** ERPNext models
  a global fiscal year as one with no company restrictions — the `companies`
  child table is a *restriction* — and that is what almost every site wants.
- **The overlap check is company-aware.** A global year collides with everything;
  two restricted years collide only if they share a company. Two years covering
  the same day for the same company make ERPNext's own `get_fiscal_year`
  ambiguous, and which year a posting lands in stops being a fact about the
  posting. **Disabling a year does not free its range.**
- **The one-year rule.** ERPNext's `FiscalYear.validate_dates` requires the end
  date to be exactly one year after the start, less a day, unless
  `is_short_year` is set — and its own message does not say which date it wanted.
  This computes it and names it. Leap days are clamped the way the calendar
  does: a year starting 29 February ends on the 27th.
- **ERPNext's own overlap check is company-blind on some versions** and refuses
  any date collision at all. Where the framework is stricter than this tool, its
  refusal is what a caller gets, unchanged — this never loosens a rule the
  framework enforces.
- **Also refuses** a `year_name` already on the site (a Fiscal Year names itself,
  so the name is the docname), an end date before the start, a one-day year, and
  a company this site does not have.
- Creating one **disabled** is accepted and warned about: ERPNext still refuses
  postings dated inside a disabled year.

---

## 73. `update_fiscal_year`

**MUTATING.** Off by default. Needs the `Fiscal Year` DocType.

**Arguments:** `year_name` (required), `new_year_start_date`,
`new_year_end_date`, `is_short_year`, `disabled`.

```json
{"name": "update_fiscal_year",
 "arguments": {"year_name": "2025", "disabled": true}}
```

- **RISK: moving the dates moves no posting.** It changes which year — or no year
  at all — every posting already written falls into, retroactively, including
  periods already reported. So the GL entries that would fall *out* of the new
  range are counted first, and any at all is a refusal naming the count. A
  posting in no fiscal year drops out of period comparisons and cannot be
  corrected without reopening a year that no longer covers it. Widening a range
  is fine; shrinking one with history in it is not.
- **Cannot rename.** ERPNext names a Fiscal Year after itself, so the name is the
  docname and is the string every Journal Entry, Budget and Period Closing
  Voucher that names a year holds. Passing `year` or `new_year_name` is refused
  by name.
- **Cannot change `companies`.** Narrowing the scope of a year with postings in it
  takes those postings out of any fiscal year for the companies it drops;
  widening it can create an overlap this tool would have refused at creation.
  Both are Desk decisions.
- Same company-aware overlap check as `create_fiscal_year`, against every other
  year.
- **Disabling deletes nothing.** The entries already in the range remain and still
  appear in reports covering them; ERPNext simply refuses *new* postings dated
  inside a disabled year. It is reversible with `disabled=false`.

---

## 74. `post_opening_balance_journal_entry`

**MUTATING.** Off by default. `submit: true` additionally requires
`allow_submit_journal_entry`.

**Arguments:** `posting_date` (required), `lines` (required), `user_remark`
(required), `company`, `offset_account`, `voucher_type`, `submit`.

Each line is `{account, side, amount, cost_center, dimensions, narrative}`.
`amount` is always positive — the direction lives in `side` (`debit`/`dr` or
`credit`/`cr`). Unlike `set_opening_balance`, **these lines are taken as given**;
the only line this tool adds is the balancing one.

```json
{"name": "post_opening_balance_journal_entry",
 "arguments": {"company": "Example Trading Co",
               "posting_date": "2026-01-01",
               "user_remark": "Trial balance at 2025-12-31 per the prior system, reviewed by TP",
               "offset_account": "3300",
               "submit": false,
               "lines": [
                 {"account": "1100", "side": "debit", "amount": 1700000},
                 {"account": "1710", "side": "debit", "amount": 52650},
                 {"account": "2310", "side": "credit", "amount": 200000}]}}
```

```json
{
  "name": "ACC-JV-2026-00061", "docstatus": 0, "docstatus_label": "draft",
  "company": "Example Trading Co", "posting_date": "2026-01-01",
  "offset_account": "3300 - Opening Balance Equity - ETC",
  "offset_side": "credit", "offset_amount": 1552650.0,
  "entered_debit": 1752650.0, "entered_credit": 200000.0,
  "balancing_difference": 1552650.0,
  "line_count": 4, "total_debit": 1752650.0, "total_credit": 1752650.0,
  "flags_set": {"is_opening": "Yes", "voucher_type": "Opening Entry"},
  "submitted": false, "gl_entries_created": 0,
  "note": "The 1552650.0 balancing line against 3300 - Opening Balance Equity - ETC was written because the 3 line(s) given were out by that much. …"
}
```

- **This or `set_opening_balance`?** Use that one when you know one side of one
  historical event and want the equity plug computed. Use this when you are
  transcribing a whole trial balance off the previous system: both sides are
  already in hand, and a one-event-at-a-time tool means one call and one stray
  equity line per account.
- **The offset is named, not found.** `offset_account` is required exactly when
  the lines do not balance, and the difference is written to it as a single line.
  Normally Opening Balance Equity (`3300`) — but retained earnings or a suspense
  account are legitimate and are not second-guessed, which is the one place this
  is more permissive than `set_opening_balance`. Naming an offset when the lines
  already balance writes no line, and the response says so.
- **`submit: true` posts it**, `0 → 1`, writing GL Entries. That path checks
  `allow_submit_journal_entry` **before anything is written**, so a site with
  posting switched off gets a refusal rather than a draft nobody asked for. The
  default is `false`.
- **`voucher_type` defaults to `Opening Entry`** where the site offers it, and is
  dropped with a note where it does not. A voucher type the caller names and the
  site does not have is a refusal — silently posting it as something else would
  mislabel an entry nobody re-reads.
- **Refuses** a group, disabled or wrong-company account on any line or on the
  offset; a group or disabled cost center; a dimension value that does not exist;
  a non-positive amount; and an unsupported line field, by name — including
  `dr_or_cr`, which is the *other* tool's spelling of `side`.

---

## 75. `bulk_submit_journal_entries`

**MUTATING.** Off by default. Additionally requires
`allow_submit_journal_entry`, checked before anything is touched.

**Arguments:** `names` (required) — up to 500 Journal Entry docnames.

```json
{"name": "bulk_submit_journal_entries",
 "arguments": {"names": ["ACC-JV-2026-00060", "ACC-JV-2026-00061", "ACC-JV-2026-00062"]}}
```

```json
{
  "total": 3, "submitted": 2, "skipped": 1, "failed": 0,
  "submitted_names": ["ACC-JV-2026-00061", "ACC-JV-2026-00062"],
  "failed_names": [],
  "results": [
    {"name": "ACC-JV-2026-00060", "ok": true, "skipped": "already_submitted", "error": null, "docstatus": 1},
    {"name": "ACC-JV-2026-00061", "ok": true, "skipped": "", "error": null, "docstatus": 1},
    {"name": "ACC-JV-2026-00062", "ok": true, "skipped": "", "error": null, "docstatus": 1}],
  "note": "Each entry was submitted in its own transaction …",
  "next_step": "Every entry in the batch is posted or was already posted. Nothing is outstanding."
}
```

- **One document's failure is not the batch's.** Each submit runs in its own
  transaction — committed on success, rolled back on failure — and the loop
  carries on. This is the only place in this app that commits mid-call, and it is
  deliberate: the alternative is a batch of five hundred where number four
  hundred fails and the request rolls back the three hundred and ninety-nine
  postings that were fine. It is what Frappe's own bulk submit does.
- **Already submitted is `ok`, not an error** — `skipped: "already_submitted"` —
  so a half-finished batch is safe to retry whole. **Cancelled is a failure:** it
  cannot be posted again.
- **It does not go round `submit_journal_entry`'s switch.** That switch is where
  an operator decided whether an AI client may move a balance at all, and this
  fails before touching anything if it is off.
- Duplicate names in one call are submitted once. More than 500 is refused before
  anything posts.

---

## 76. `delete_draft_journal_entry`

**MUTATING, destructive.** Off by default.

**Arguments:** `name` (required), `reason` (required).

```json
{"name": "delete_draft_journal_entry",
 "arguments": {"name": "ACC-JV-2026-00062",
               "reason": "duplicate of ACC-JV-2026-00061, keyed twice during the opening-balance load"}}
```

```json
{
  "deleted": {
    "name": "ACC-JV-2026-00062", "company": "Example Trading Co",
    "posting_date": "2026-01-01", "voucher_type": "Opening Entry",
    "user_remark": "Trial balance at 2025-12-31 per the prior system",
    "total_debit": 1752650.0, "total_credit": 1752650.0, "line_count": 4,
    "accounts": [{"account": "1100 - Cash - ETC", "debit": 1700000.0, "credit": 0.0}]},
  "reason": "duplicate of ACC-JV-2026-00061, keyed twice during the opening-balance load",
  "gl_entries_removed": 0,
  "note": "A draft writes no GL Entries … The MCP Action Log row for this call is now the only record that the entry existed."
}
```

- **The gap it fills.** `cancel_journal_entry` refuses a draft, correctly: there
  is nothing to reverse, because a draft has moved no balance. That left an
  unwanted draft with no MCP path at all.
- **Drafts only, whatever is asked.** A **submitted** entry has written GL
  Entries; deleting it would take those balances with it and leave nothing saying
  why — refused, and pointed at `cancel_journal_entry`. A **cancelled** entry and
  its reversing rows are the evidence that a posting was made and undone —
  deleting one leaves an audit trail with a hole in it, so that is refused too.
- **It is a real delete**, `frappe.delete_doc`, nothing left in the table. Which
  is why the response carries the entry's company, date, totals and every line:
  once the call returns, the MCP Action Log row is the only record that the
  document ever existed.

---

# Attaching evidence

## 77. `attach_file_to_document`

**MUTATING**, default OFF (`allow_attach_file_to_document`).

**Arguments:** `doctype` (required), `name` (required), `file_name` (required),
`file_content` (base64) **or** `file_url`, `is_private` (default `true`),
`company` (optional guard), `allow_cancelled` (default `false`), `dry_run`
(default `false`).

**Returns** `file` (the File docname), `file_url`, `file_size`, `size_human`,
`mime_type`, `sha256`, `is_private`, `attached_to_doctype`, `attached_to_name`,
`parent_docstatus`, `parent_company`, `attachments_before` and
`attachments_after`.

**What it is for.** A year of brokerage statements belongs on the Journal
Entries that book them; a receipt belongs on the Bank Transaction it explains; a
purchase contract belongs on the Asset. `attach_governance_document` (**59**)
files a *new* Governance Document and attaches to that, which is right for a
trust instrument and useless for putting December's statement on December's
entry. This one attaches to the record you name, and creates nothing else — no
balance moves, no docstatus changes, no existing row is touched.

**What it refuses, all of it read off the site rather than compiled into the
app:**

| Refusal | Where the rule comes from |
| --- | --- |
| Unknown `doctype` or `name` | the site's own schema and tables |
| Acting user cannot `write` the parent | Frappe's permission model — the same permission the Desk's attach control needs |
| Parent is **cancelled** (docstatus 2) | the parent's own state; `allow_cancelled=true` overrides |
| `file_name` the document already has | that document's existing attachments, with the clashing File named |
| Too many attachments | the parent DocType's `max_attachments` |
| Disallowed extension | whatever allowlist System Settings declares — nothing on a site that declares none, which is Frappe's own answer |
| `company` does not match the parent's | the parent's `company` field. A `company` passed for a doctype with **no** company field is an error, not a shrug: a guard the caller believes ran and did not is worse than no guard |

**Size.** `file_content` is base64 and capped at 8 MB, the same ceiling
`attach_governance_document` uses. Base64 in a JSON call is expensive — a large
statement is better uploaded in the Desk and recorded here with `file_url`.

**`dry_run` defaults to FALSE**, unlike `import_chart_of_accounts` and
`run_depreciation_cycle`. Those write many documents and are hard to unpick;
this adds a single File and changes no balance. Making the common case cost two
round trips would be safety theatre. A batch script should dry-run its target
list once, then run live.

**Audit.** The MCP Action Log row names the parent doctype and docname, the
filename, the size and the sha256 of the stored bytes. The base64 payload itself
is elided to a note of its length — it is logged as
`<11184812 characters elided>` rather than crowding every other argument out of
the row.

```
attached wfa-statement-2025-12-31.pdf (412.0 KB, sha256 9f2c1ab77e04) to
Journal Entry ACC-JV-2026-03369 as File a7f3c9e21b (private)
```

---

## 78. `list_parcels`

Read-only, default ON (`allow_list_parcels`).

**Arguments:** `owning_entity` (or `company` — the same thing), `county`,
`use_type`, `title_holder`, `linked_to_asset` (boolean), `limit`.

**Returns** `parcels`, `count`, `total_in_register`, `total_acreage`,
`total_appraised_value`, `average_per_acre`, `by_use_type`, `oldest_appraisal`,
`newest_appraisal` and `parcels_without_value`.

Totals cover the rows returned, not the whole register, and a `limit` that hides
part of it says so before the totals are trusted. `oldest_appraisal` is how you
find out the valuation is four years stale.

**Appraised value is not book value.** What the balance sheet carries is the
Asset's cost; this is market. They are meant to differ — see **82**.

---

## 79. `get_parcel`

Read-only, default ON (`allow_get_parcel`).

**Arguments:** `parcel` (required — a docname like `Red Camp - HLD`, or just
`Red Camp`), `owning_entity`.

**Returns** the parcel, its `asset` (with the gap between cost and appraised
value), every `lease` over it in either direction, `active_leases` and
`attachments`.

A bare parcel name matching parcels in two entities is refused with both named
rather than resolved to whichever came first.

---

## 80. `create_parcel`

**MUTATING**, default OFF (`allow_create_parcel`).

**Arguments:** `parcel_name` (required), `owning_entity` (or `company`),
`parcel_id`, `county`, `state`, `address`, `acreage`, `use_type`,
`title_holder`, `appraised_value`, `appraised_as_of`, `appraiser`,
`appraisal_document`, `related_asset`, `notes`.

**The docname is `<parcel_name> - <entity abbr>`**, so two entities in one family
may each have a "Home Place".

| Refusal | Why |
| --- | --- |
| A second parcel with the same name for one entity | the docname is built from it |
| A second parcel with the same `parcel_id` for one entity | that number is the county assessor's primary key; two of them means a typo |
| Negative acreage or appraised value | not opinions |
| An unknown `use_type` | the options are read off the DocType, so a customised site's own list is what applies |
| A `title_holder`, `appraisal_document` or `related_asset` on another company's books | a parcel and the records explaining it belong to one entity |

**Warns rather than refusing** when a value arrives with no as-of date, or with
no appraisal document behind it. A figure somebody remembered is worth recording;
it just should not be mistaken for a valuation.

---

## 81. `update_parcel`

**MUTATING**, default OFF (`allow_update_parcel`).

**Arguments:** `parcel` (required), plus any of `parcel_id`, `county`, `state`,
`address`, `acreage`, `use_type`, `appraised_value`, `appraised_as_of`,
`appraiser`, `title_holder`, `appraisal_document`, `notes`. An empty string
clears an optional field.

**Returns** the parcel and `changes`, every one as `[before, after]`.

Cannot rename it (the docname is built from `parcel_name`, and every lease and
asset link points at that docname), cannot move it between entities (a parcel
changing hands is a conveyance, not an edit), and cannot set `related_asset` —
that is **82**, which checks things this does not. A no-op update is refused
rather than reported as a success.

---

## 82. `link_parcel_to_asset`

**MUTATING**, default OFF (`allow_link_parcel_to_asset`).

**Arguments:** `parcel` (required), `asset` (required), `replace` (default
`false`), `dry_run` (default `false`).

**Returns** the parcel, an `asset` block with `gross_purchase_amount`,
`appraised_value` and `appraisal_over_book`, and `unrealised_appreciation` when
both figures exist.

**The gap is the point.** A parcel appraised at 3,100,000 sitting on the books at
a 1998 cost of 240,000 is not a discrepancy to be fixed — it is unrealised
appreciation, it is the single most important number in a succession
conversation, and neither record shows it alone. Nothing here posts it, because
unrealised appreciation is not a journal entry.

Refuses an asset on another company's books, an asset already claimed by a
different parcel, and a parcel that is already linked unless `replace=true`.

---

## 83. `list_leases`

Read-only, default ON (`allow_list_leases`).

**Arguments:** `owning_entity` (or `company`), `status`, `direction`, `parcel`,
`counterparty`, `active_on`, `expiring_within_days` (default 90), `limit`.

**Returns** `leases`, `annual_rent_receivable`, `annual_rent_payable`,
`net_annual_rent`, `rent_not_annualisable`, `expiring_soon`,
`active_past_expiration` and `as_of`.

**Rent is annualised for Active leases only.** A crop share and a one-time
payment have no annual rate: they are listed under `rent_not_annualisable`
rather than counted as zero, because a rent roll that quietly treats an unknown
as nothing understates the whole portfolio.

**Nothing here expires a lease.** A lease marked Active whose expiration date has
passed is reported under `active_past_expiration` and left exactly as it was.
Farm ground routinely runs on month to month past its stated term, and a status
that flipped itself on a calendar would erase the difference between "still
running" and "nobody has looked at this in years".

---

## 84. `get_lease`

Read-only, default ON (`allow_get_lease`).

**Arguments:** `lease` (required), `owning_entity`.

**Returns** the lease, `parcel_detail`, `attachments`, `annualised_rent`,
`past_expiration` and `in_force_today`.

Read `direction` before reading `rent_amount`: Outbound means the owning entity
collects it, Inbound means it pays it.

---

## 85. `create_lease`

**MUTATING**, default OFF (`allow_create_lease`).

**Arguments:** `lease_name`, `direction`, `lessor`, `lessee`, `effective_date`
(all required), `owning_entity` (or `company`), `expiration_date`, `status`
(default `Active`), `termination_date`, `termination_reason`, `parcel`,
`counterparty`, `rent_amount`, `rent_frequency` (default `Annual`), `rent_terms`,
`governance_document`, `lease_document_url` **or** `file_content` +
`file_name`, `notes`.

**Books nothing.** No journal entry, no receivable, no schedule. Recording an
agreement and booking its consequences are separate acts, and this is the first
one.

**Direction is stated, not guessed.** The result carries a `direction_check`
saying whether the party names agree with the stated direction —
`consistent`, `inconsistent` or `unverified`. Reported, never enforced: a legal
name ("Highland Ltd Liability Co.") and a Company docname ("Highland LLC")
routinely differ, and a refusal built on string matching is one nobody could get
past.

Refuses a duplicate lease name for one entity, the same party as both lessor and
lessee, an expiration or termination date before the effective date, `Terminated`
with no termination date, and negative rent (rent flowing the other way is a
lease in the other direction). `file_content` is base64 with the same 8 MB
ceiling every attachment tool uses; a large scan is better uploaded in the Desk
and recorded with `lease_document_url`.

---

## 86. `update_lease`

**MUTATING**, default OFF (`allow_update_lease`).

**Arguments:** `lease` (required), plus any of `status`, `expiration_date`,
`termination_date`, `termination_reason`, `rent_amount`, `rent_frequency`,
`rent_terms`, `lessor`, `lessee`, `parcel`, `counterparty`,
`governance_document`, `notes`.

Cannot rename it — a renewed lease is a **new** lease with its own term — and
cannot move it between entities. Marking one `Terminated` requires a
`termination_date` in the same call: "we ended it" without "when" is not a record
anybody can rely on later.

---

## 87. `list_related_parties`

Read-only, default ON (`allow_list_related_parties`).

**Arguments:** `company`, `party_type`, `relationship_to_company`, `supplier`,
`current_only` (default `false`), `limit`.

**Returns** `parties`, `count`, `distinct_people`, `current_count`,
`ended_count`, `by_relationship`, `by_party_type`, `linked_to_supplier`,
`linked_to_cap_table`, `without_governing_document` and `without_tax_id`.

**One person may appear more than once.** A Manager who is also a Member is two
entries, under two instruments, from two dates — `count` counts relationships and
`distinct_people` counts names. Ended relationships are listed by default: the
transactions they explain are still in the ledger.

`without_governing_document` is the first thing an examiner asks for.

---

## 88. `get_related_party`

Read-only, default ON (`allow_get_related_party`).

**Arguments:** `party` (required — a docname like
`Tim Polehn - Manager - OML`, or just the name), `company`.

**Returns** the relationship, `other_roles`, `cap_table_detail`,
`supplier_detail`, `parcels_titled` and `leases_as_counterparty`.

**Never returns more than four digits of a taxpayer id**, including from a linked
Supplier: `supplier_detail.tax_id` says only whether one is on file. A bare name
held in two capacities is refused with both docnames listed.

---

## 89. `create_related_party`

**MUTATING**, default OFF (`allow_create_related_party`).

**Arguments:** `party_name`, `party_type`, `relationship_to_company`,
`effective_date` (all required), `company`, `end_date`, `tax_id_type`,
`tax_id_last4`, `address`, `cap_table_entry`, `supplier`, `governing_document`,
`notes`.

**The docname is `<name> - <relationship> - <company abbr>`**, because somebody
who is both Manager and Member of an LLC is two entries under two instruments.
The same name and role twice is refused; a second role is expected.

**Four digits, never nine.** `tax_id_last4` takes exactly four digits and refuses
nine, naming the four to send instead. Not truncated, not masked, not accepted
with a warning. The controller enforces the same rule, because the Desk form is a
second door into the same field. The full number belongs on the signed W-9, on
paper.

**This is not the Party field on a Journal Entry.** ERPNext already answers "who
was this transaction with"; this answers "who is related to us, in what capacity,
since when, and under what document", which no transactional field can, because a
transaction is an event and a relationship is a state.

---

## 90. `update_related_party`

**MUTATING**, default OFF (`allow_update_related_party`).

**Arguments:** `party` (required), plus any of `party_type`, `effective_date`,
`end_date`, `tax_id_type`, `tax_id_last4`, `address`, `cap_table_entry`,
`supplier`, `governing_document`, `notes`.

`party_name`, `relationship_to_company` and `company` are the key and cannot
change: a change of role is a **new** relationship, so register it and set an
`end_date` on this one. An entry is never deleted when a relationship ends — the
transactions it explains are still in the ledger, and a prior year's disclosure
schedule still needs to know who was who at the time.

---

## 91. `generate_quarterly_investment_report`

**MUTATING**, default OFF (`allow_generate_quarterly_investment_report`).

**Arguments:** `quarter` (required, as `2026-Q2`), `company`, `output_format`
(`pdf` default, or `docx`), `output_path`, `overwrite`, `investment_accounts`,
`cash_clearing_account`, `holdings`, `benchmark_rate_percent`,
`manager_fee_percent` (default 1.00), `custody_fee_percent` (default 1.00),
`performance_fee_percent` (default 20), `high_water_mark`, `net_contributions`,
`title`, `dry_run`.

**Returns** `aum`, `activity`, `fees`, `performance`, `holdings`,
`cash_clearing`, `reconciliation`, `preconditions`, `governance_document`,
`document` (the attached file's metadata and sha256) and `written_to_disk`.

**It refuses a quarter that is not closed**, and names everything missing in one
reply:

| Precondition | Why it is a precondition |
| --- | --- |
| The quarter has ended | there is no such thing as a report on a quarter that is still happening |
| The custodian's statement is filed as a **Prior Statement** with an effective date inside it | a report written before the statement arrived is a report written from a guess |
| No journal entry touching the investment accounts is still a draft | an account that reconciles today and will not once three drafts post is not reconciled, it is about to not be |
| No bank transaction in the period is unreconciled | the same argument, from the other side of the ledger |

**It invents nothing.** Without `benchmark_rate_percent` the return over
benchmark and the performance fee are **not computed** and say so — they are not
zero and not estimated, because a performance fee against an assumed benchmark of
nothing overstates what the manager is owed. `high_water_mark` caps the
fee-eligible gain, and closing assets at or below it earn nothing however the
quarter went. `net_contributions` defaults to zero and the report says that is an
assumption.

**Holdings come from the caller.** This app reads one ERPNext site; the
custodian's positions are not on it. Pass `holdings` — a list of objects with
`symbol`, `description`, `quantity`, `price`, `market_value`, `cost_basis` — and
the report reconciles the snapshot against the ledger and reports the variance.
Omit it and assets under management are the ledger balance, stated as such.

The investment accounts are matched by name off the company's own chart and
**listed in the report**, or named explicitly; a chart with no match is refused
rather than guessed at.

**PDF is the default and the right answer.** `docx` exists for a report that has
to be edited before signing; a `.docx` is a file the recipient may not be able to
open.

---

## 92. `generate_1099_prefill`

**MUTATING**, default OFF (`allow_generate_1099_prefill`).

**Arguments:** `tax_year` (required), `company`, `threshold` (default 600),
`output_path` (a **directory**), `overwrite`, `payer_address`, `include_forms`
(default `true`), `title`, `dry_run`.

**Returns** `recipients`, `exempt_above_threshold`, `below_threshold`,
`total_box_1`, `related_party_recipients`, `excluded`, `basis`,
`governance_document`, `workbook` and `forms`.

**It is a pre-fill.** Recipient taxpayer ids print as `XXX-XX-nnnn`, because this
site holds four digits on purpose. Copy A must be the official scannable red-ink
form or an electronic filing; the Copy A page here is stamped as an information
copy. Copies B and C print on plain paper and are the ones that go out.

**Classification is never silent.** Every recipient is `reportable`, `exempt` or
`borderline` with the reason in a sentence:

| Signal | Verdict |
| --- | --- |
| Related Party says Individual, Partnership, Family Member or Trust | reportable |
| Related Party says Corporation | exempt — **unless** the name says law firm, which is borderline |
| Related Party says LLC, or the name does | **borderline** — a disregarded entity is reportable and one taxed as a corporation is not, and only the W-9 says which |
| The name looks like a law firm | **borderline** — attorneys are reportable **even when incorporated**, which is why "ends in PC, skip it" is the wrong rule |
| The name looks governmental | **borderline** — a name is a hint, not a determination |
| Supplier type is Individual / Proprietorship / Partnership | reportable |
| The name ends in a corporate suffix | exempt |
| Nothing on the site says | **borderline**, with the remedy: register it as a Related Party, or read the W-9 |

**Where the money comes from.** GL Entry rows carrying a Supplier party — every
voucher type, and only submitted ones, since cancelled vouchers leave no GL row.
Debits **only** on Payable-type accounts (a debit to payables is a bill being
paid; a credit is one being raised). Debits **minus** credits everywhere else
(the party sits on the expense line, so a credit is a refund). That rule is right
in both bookkeeping styles, and `by_account` shows both sides so the arithmetic
can be checked rather than believed.

**Excluded and said so.** Employees, because that is W-2 territory — and the
count and total of employee-party postings is reported anyway, so "nobody looked"
and "somebody looked and excluded them" are different-looking answers. Opening
entries. Anything under the threshold, listed with its total so a case near the
line is visible rather than absent.

Refuses a tax year that has not ended, naming the earliest date it could run.

```
1099-NEC pre-fill for Orchard Meadow LLC 2025: 4 recipient(s), 29,485.00 in Box
1, filed as GD-00214
```

---

## 93. `list_companies`

Read-only, default ON (`allow_list_companies`).

**Arguments:** `limit`.

**Returns** `companies`, `company_count`, `truncated` and `party_types`. Each
company carries `abbr`, `default_currency`, `country`, `parent_company`,
`is_group`, `chart_of_accounts`, `default_cost_center`, `tax_id_on_file`,
`tax_id_last4`, `fiscal_year_start_month`, `fiscal_year_first`,
`fiscal_year_last`, `fiscal_year_count`, `cost_center_count`, `account_count`,
`gl_entry_count`, `first_gl_entry` and `last_gl_entry`.

**The GL counts are the point on a multi-company site.** A company with no
postings can still have its currency changed; one with postings cannot, and this
is where you find out which you are looking at.

`party_types` reports whether this app's `Family` and `Contact` Party Types are
registered on the site, with a hint naming the fix when they are not — because
"can I book a Journal Entry line to a family member" is exactly the question a
client calls this tool to answer.

Never returns more than four digits of a tax id.

---

## 94. `create_company`

**MUTATING**, default OFF (`allow_create_company`).

**Arguments:** `company_name` (required), `abbr` (required), `country` (default
`United States`), `default_currency` (default `USD`), `fiscal_year_start_month`
(1-12 or a month name, default 1), `tax_id`, `parent_company`,
`chart_of_accounts`, `notes`, `dry_run` (default `false`).

**Returns** the plan it worked from, `created`, `name`, `account_count`,
`cost_center_count`, `default_cost_center`, `chart_of_accounts`, the
`fiscal_year` it created or found, and `warnings`.

ERPNext's own Company controller builds the chart, the root cost centers and the
defaults on insert. This tool's job is to hand it correct arguments and then
report **what it actually got** — an `account_count` of zero means the named
chart does not exist on this site, and the result says so rather than looking
like a success.

It creates the fiscal year containing today for the start month given **and the
one before it**. April (4) is a farm year and is named for the span it covers
(`2026-2027`); January (1) is a calendar year and is named `2026`. Two years
because a company stood up in March is one whose first task is often last year's
closing balances, and an opening-balance journal entry with no fiscal year to
land in is refused by ERPNext with a message about a period that does not exist.
Years that already exist are left alone and reported as such.

`chart_of_accounts` defaults to `Standard with Numbers` — numbered because this
app resolves accounts by number as well as by name, and an unnumbered chart makes
`resolve_account("1100")` impossible on a brand-new company.

The result carries the `cost_center_tree`, `fiscal_years_created`, and a
`next_step` pointing at **56** — ERPNext books to a company's default account
fields without asking, and one whose defaults are empty fails at the first
invoice rather than here.

| Refusal | Why |
| --- | --- |
| A duplicate company name | it is the docname |
| A duplicate abbreviation | every account, cost center, parcel and lease docname ends in it; two companies sharing one makes those ambiguous |
| A non-alphanumeric abbreviation | it becomes the tail of a docname |
| A `country` or `default_currency` this site does not have | ERPNext ships the ISO lists, so this is a spelling — `United States`, not `USA` |
| A `parent_company` that is not a group | nothing can consolidate under a non-group company |
| A month outside 1-12, or an unparseable month name | refused rather than defaulted |
| An `abbr` outside 2-5 characters | one is not an abbreviation and collides immediately; past five, every account docname carries it |
| An `abbr` already used by docnames with no company behind them | a chart left behind by a deleted company. A new company reusing it would inherit docnames that look like its own and are not |
| A `chart_of_accounts` template this site does not offer | only checked where ERPNext's own template list is importable. A template it cannot find produces a company with no accounts, which looks like a success and is not |

```
create_company {"company_name": "Constancy Farms LLC", "abbr": "CF",
                "fiscal_year_start_month": 4}
→ created company Constancy Farms LLC (CF), 68 accounts, 3 cost centers,
  fiscal year 2026-2027
```

---

## 95. `update_company`

**MUTATING**, default OFF (`allow_update_company`).

**Arguments:** `company` (required — docname or abbreviation), `country`,
`tax_id`, `notes`, `default_currency`.

**Returns** `changed` (each as `[before, after]`, with `tax_id` redacted to
`…nnnn`), `unchanged`, `tax_id_on_file`, `tax_id_last4` and the GL facts.

| Refusal | Why |
| --- | --- |
| `abbr` | it is the tail of every account, cost center, parcel and lease docname on these books. Changing it renames thousands of documents, which is a migration rather than an edit |
| `company_name` | it **is** the docname, and every document links to it by that name |
| `default_currency` **once anything is posted** | every one of those entries was measured in the old one; relabelling it restates the whole ledger without touching a number. The refusal names the entry count and the date range |
| `fiscal_year_start_month` | a year that changes shape mid-cycle produces two periods claiming the same days. A short year created deliberately with **72** is how that is done |

The currency rule is about the **ledger**, not the field: a company with no
postings can still have its currency corrected, which is the first-day case a
blanket refusal would have blocked.

---

## 96. `register_party_types`

**MUTATING**, default OFF (`allow_register_party_types`). Idempotent.

**Arguments:** `dry_run` (default `false`).

**Returns** `created`, `already_registered`, and `party_types` with the
`account_type` and the reason each one exists.

Registers `Family` and `Contact` as real Party Type records so a Journal Entry
line can carry them. Both settle against a **Payable** account, because both are
payees.

**Why these two.** ERPNext ships Customer, Supplier, Employee and Shareholder,
and a family operation pays two kinds of people that fit none of them:

- **`Family`** — a relative receiving money that is neither payroll nor a
  purchase. **92** excludes those postings and reports the count, total and
  names. A transfer below the IRS annual gift exclusion is not compensation for
  services: no W-9, no form. Recording them as Suppliers puts family money into
  vendor spend *and* onto a 1099 the recipient owes no tax on.
- **`Contact`** — the occasional consultant who is not a formal Supplier but IS
  paid for services. **92** reads those and classifies them **borderline**,
  naming the W-9, rather than dropping them.

**A PARTY TYPE'S NAME HAS TO BE A DOCTYPE.** `Party Type` names itself
`field:party_type`, and that field is a `Link` to `DocType`. A Journal Entry line
then carries `party`, a **`Dynamic Link`** resolved through `party_type`. So
`party_type = "Family"` needs a DocType called `Family`, and the party has to be
a record in it. `Contact` resolves to Frappe's own Contact DocType; `Family`
resolves to the register this app ships.

`resolves_to_doctype` in the result says which, and a party type whose DocType is
missing comes back under `skipped` with the reason rather than taking the call
down — the same behaviour the migrate patch has, and for the same reason.

They are also seeded on install and on every `bench migrate`; this tool is for a
site that cannot be migrated right now. **It changes nothing that already
exists** — every rule and Journal Entry using Shareholder, Employee or Supplier
keeps working exactly as it did.

---

## 97. `list_fields`

Read-only, default ON (`allow_list_fields`).

**Arguments:** `owning_entity` (or `company`), `parcel`, `crop`, `variety`,
`condition`, `food_safety_zone` (boolean), `linked_to_cost_center` (boolean),
`limit`.

**Returns** `fields`, `field_count`, `total_acreage`, `average_acreage`,
`oldest_planting_year`, `newest_planting_year`, `by_variety`,
`known_varieties`, `without_acreage` and
`spray_dates_from_farm_precision_ag`.

**`known_varieties` is the autosuggest.** It is what is already planted on this
site. A hardcoded list would be wrong the first time somebody puts a new variety
in the ground; what is already there cannot be.

**`last_spray_date` comes from two places and says which.** What is recorded on
the Field, and — where `farm_precision_ag` is installed — the newest Spray Log
against it. The later of the two is `last_spray_date`, with `last_spray_source`
naming where it came from, and both raw values are returned as
`last_spray_date_recorded` and `last_spray_date_observed` so they can be compared
rather than believed.

---

## 98. `get_field`

Read-only, default ON (`allow_get_field`).

**Arguments:** `field` (required — a docname like `Yellow Camp Block 3 - MC`, or
just `Yellow Camp Block 3`), `parcel`, `owning_entity`.

**Returns** the block, `parcel_detail`, `zone_count`, `zone_acreage`,
`unzoned_acreage`, `water_rights` and every `zone` over it.

A bare field name matching blocks on two parcels is refused with both named
rather than resolved to whichever came first; `parcel` narrows it.

---

## 99. `create_field`

**MUTATING**, default OFF (`allow_create_field`).

**Arguments:** `parcel` (required), `field_name` (required), `owning_entity` (or
`company`), `acreage`, `crop` (default `Cherry`), `variety`, `rootstock`,
`planting_year`, `planting_density_per_acre`, `condition`, `block_number`,
`external_farm_app_id`, `last_spray_date`, `water_test_last_date`,
`wildlife_intrusion_last_report`, `food_safety_zone`,
`worker_hygiene_station_present`, `notes`.

**The docname is `<field_name> - <parcel abbr>`**, so every parcel may have a
"Block 3". The parcel's abbreviation is its `abbr`, or initials derived from its
name when it has none.

**The food-safety fields are part of the block, not a separate log.**
`last_spray_date` answers the re-entry interval question a crew is waiting at
the gate for before it answers a WPS report;
`worker_hygiene_station_present` decides whether a crew may work the block at
all.

| Refusal | Why |
| --- | --- |
| A second block with the same name on one parcel | the docname is built from it |
| A `external_farm_app_id` already on another block | that id is the other system's primary key; two of them makes the sync bridge ambiguous |
| Negative acreage or planting density | not opinions |
| Blocks whose acreage would sum to **more than the parcel** | two numbers that cannot both be true. Named with the parcel's acreage, the total and the excess |

Blocks summing to *less* than the parcel is the normal case and is left alone —
roads, ditches, headlands and the house are all real.

**Warns rather than refusing** on no acreage, and on a food-safety block with no
hygiene station or no water test. Every one of those is a fact worth recording
precisely because it is a problem.

---

## 100. `update_field`

**MUTATING**, default OFF (`allow_update_field`).

**Arguments:** `field` (required), plus any of `acreage`, `crop`, `variety`,
`rootstock`, `planting_year`, `planting_density_per_acre`, `condition`,
`block_number`, `external_farm_app_id`, `last_spray_date`,
`water_test_last_date`, `wildlife_intrusion_last_report`, `food_safety_zone`,
`worker_hygiene_station_present`, `notes`.

**Returns** the block and `changed`, every one as `[before, after]`.

Cannot rename it (the docname is built from `field_name`, and every zone points
at that docname), cannot move it to another parcel (ground does not move — a
block on the wrong parcel was mis-registered), and cannot set `cost_center` —
that is **101**. The parcel acreage rule applies here too. A no-op update is
refused rather than reported as a success.

---

## 101. `link_field_to_cost_center`

**MUTATING**, default OFF (`allow_link_field_to_cost_center`).

**Arguments:** `field` (required), `cost_center` (required — docname, number or
name), `owning_entity`, `replace` (default `false`), `dry_run` (default
`false`).

**Returns** `field`, `cost_center`, `previous_cost_center`, `acreage`,
`shared_with`, `changed` and — when other blocks book to the same cost center —
a `note`.

| Refusal | Why |
| --- | --- |
| A cost center on another company's books | a cost allocated across two companies is an intercompany transaction, not a dimension |
| A group cost center | ERPNext will not let a posting land on one |
| A disabled cost center | nothing can book to it |
| Repointing a block that is already linked, without `replace=true` | this season's costs and last season's would land in different places |

**Reports rather than refuses** when other blocks already book there. A cost
center per orchard is a legitimate design; it just is not per-block costing, and
the result says which one you have.

---

## 102. `get_parcel_field_summary`

Read-only, default ON (`allow_get_parcel_field_summary`).

**Arguments:** `parcel` (required), `owning_entity`.

**Returns** `field_count`, `planted_acreage`, `parcel_acreage`,
`unassigned_acreage`, `average_field_acreage`, `zone_count`, `zoned_acreage`,
`average_zones_per_field`, `total_flow_gpm`, `oldest_planting_year`,
`newest_planting_year`, `by_condition`, `by_variety`, `water_rights`,
`food_safety_blocks`, `blocks_without_hygiene_station`,
`zones_without_water_test` and a per-block `fields` list.

**`unassigned_acreage` is usually the interesting number.** Blocks summing to
less than the parcel is normal, but a large gap on a parcel somebody thinks is
fully blocked out is a missing Field.

---

## 103. `import_farm_app_fields`

**MUTATING**, default OFF (`allow_import_farm_app_fields`). **Dry run by
default.**

**Arguments:** `records` (required — an array of objects), `parcel` (a default
for records with no `parcel_hint`), `owning_entity`, `apply` (default `false`).

Each record may carry `name` (required), `parcel_hint`, `acreage`, `variety`,
`planting_year`, `block_number` and `farm_app_uuid`. **An unrecognised key is
refused rather than ignored** — a typo silently dropped is a field somebody
thinks they imported.

**Returns** `record_count`, `would_create`, `already_present`, `applied`, the
per-record `plan`, and `created` when applied.

**This is the schema-alignment foundation, not the sync.** It creates ERPNext
Fields carrying `external_farm_app_id` so a later sync engine has something to
match on. It never updates an existing Field, never deletes, and never writes
back to the Farm App.

**The whole batch is validated before the first insert.** A half-imported farm is
worse than an unimported one, because the second run has to work out which half.
A `parcel_hint` matching no Parcel, a batch repeating a name or a `farm_app_uuid`,
a negative acreage — any of those refuses the lot.

A block already registered under that name, or already carrying that Farm App
id, is **skipped** with the reason and the existing docname, so the same batch
re-runs safely.

---

## 104. `list_irrigation_zones`

Read-only, default ON (`allow_list_irrigation_zones`).

**Arguments:** `owning_entity` (or `company`), `field`, `parcel`,
`water_source`, `sprinkler_type`, `water_source_class`, `chlorination_active`
(boolean), `limit`.

**Returns** `zones`, `zone_count`, `total_area_acres`, `total_flow_gpm`,
`by_water_source`, `water_rights`, `without_water_test` and
`surface_water_without_a_right`.

**The last two lists are the report.** A zone with no agricultural water test is
one whose fruit cannot be cleared under FSMA Subpart E; a creek, pond or shared
diversion with no water right is not something Oregon treats as self-evident.

---

## 105. `get_irrigation_zone`

Read-only, default ON (`allow_get_irrigation_zone`).

**Arguments:** `zone` (required — a docname like `YC3-Zone2 - MC`, or just
`YC3-Zone2`), `field`, `owning_entity`.

**Returns** the zone, `field_detail`, `zones_on_this_field`,
`field_acreage_zoned`, `share_of_field` and `compliance_notes` — the gaps in
sentences rather than left to be inferred.

---

## 106. `create_irrigation_zone`

**MUTATING**, default OFF (`allow_create_irrigation_zone`).

**Arguments:** `field` (required), `zone_name` (required), `owning_entity`,
`zone_number`, `water_source`, `water_right_id`, `flow_rate_gpm`,
`sprinkler_type`, `area_sq_ft`, `water_test_last_date`, `water_source_class`,
`chlorination_active`, `notes`.

**The docname is `<zone_name> - <parcel abbr>`** — not the *field's*
abbreviation. A zone name already carries its block (`YC3-Zone2`), and suffixing
it with the block again gives `YC3-Zone2 - YC3`, which says the same thing twice
and drops the ground.

**`area_acres` is computed** from `area_sq_ft` at 43,560 to the acre and cannot
be passed. Two figures a caller sets independently are two figures that will
disagree; passing it is refused with the conversion offered.

| Refusal | Why |
| --- | --- |
| A second zone with the same name on one parcel | the docname is filed under the parcel |
| A `zone_number` already used on that block | that number is what somebody types into the controller at two in the morning; two answers means water goes somewhere nobody chose |
| Negative area or flow | not opinions |
| Zones whose area would sum to **more than the block** | reported in acres and in the square feet you typed |

**Warns rather than refusing** on no area, on surface water with no water right,
and on a zone watering a food-safety block with no water test.

---

## 107. `update_irrigation_zone`

**MUTATING**, default OFF (`allow_update_irrigation_zone`).

**Arguments:** `zone` (required), plus any of `zone_number`, `water_source`,
`water_right_id`, `flow_rate_gpm`, `sprinkler_type`, `area_sq_ft`,
`water_test_last_date`, `water_source_class`, `chlorination_active`, `notes`.

**Returns** the zone and `changed`, every one as `[before, after]`. `area_acres`
is recomputed.

Cannot rename it, cannot move it to another block (pipe does not move), and
cannot set `area_acres`. The block area rule and the zone number rule both apply.

---

## 108. `list_housing_units`

Read-only, default ON (`allow_list_housing_units`).

**Arguments:** `owning_entity` (or `company`), `parcel`, `unit_type`,
`condition`, `or_housing_law_compliant`, `fsma_worker_facility` (boolean),
`limit`.

**Returns** `units` (each with its current `occupants`), `unit_count`,
`residential_unit_count`, `total_capacity`, `currently_assigned`, `open_beds`,
`by_unit_type`, `overdue_inspections`, `uninhabitable`,
`fsma_worker_facilities` and `over_lawful_occupancy`.

**Capacity and lawful occupancy are different questions and both are reported.**
One is how the operation uses the unit; the other is what 50 square feet per
occupant allows. A gap between them is the finding.

Non-residential units — a shower block, a kitchen, a shop — are counted as units
but contribute no capacity. A unit never inspected counts as overdue, which is
the answer that gets somebody to go and look.

---

## 109. `get_housing_unit`

Read-only, default ON (`allow_get_housing_unit`).

**Arguments:** `unit` (required — a docname like `MC-Cabin-01 - MC`, or just
`MC-Cabin-01`), `owning_entity`.

**Returns** the unit, `currently_assigned`, `open_beds`, `assignment_count`,
`current_assignments`, the whole `assignment_history` newest first, and
`compliance_notes`.

The notes name what is missing in sentences: capacity over the lawful occupancy,
no habitability inspection in a year, no smoke or CO detector test on record,
Uninhabitable, subject to FSMA Subpart L.

---

## 110. `create_housing_unit`

**MUTATING**, default OFF (`allow_create_housing_unit`).

**Arguments:** `parcel` (required), `unit_name` (required), `owning_entity`,
`unit_type`, `square_footage`, `capacity`, `year_built`, `condition`,
`related_asset`, `access_card_zone`, `fsma_worker_facility`,
`or_housing_law_compliant`, `max_occupants_per_or_law`,
`last_habitability_inspection`, `smoke_detector_last_test`,
`co_detector_last_test`, `notes`.

**The docname is `<unit_name> - <parcel abbr>`**, so every camp may number its
cabins from one.

**The lawful occupancy is computed** from `square_footage` at 50 sq ft per
occupant — 29 CFR 1910.142(b)(1), which Oregon's agricultural labor housing
rules follow — unless you pass `max_occupants_per_or_law`. It is a **default,
not a derivation**: a cabin with a fixed bunk layout keeps the number somebody
worked out.

| Refusal | Why |
| --- | --- |
| A second unit with the same name on one parcel | every camp numbers its cabins from one, so the name has to be unique inside the parcel |
| An Asset on another company's books | a building and the asset carrying it belong to one set of books |
| An Asset already carrying a different unit | one asset, one building — split the cost first |

**Warns rather than refusing** a `capacity` over 20 outside a Multi-Unit
Building. A twenty-person cabin is barracks by another name, and some of them
really are. Also warns on missing square footage, missing detector tests and a
missing habitability inspection.

`or_housing_law_compliant` defaults to **Unknown**, which is a distinct answer
from No: an operator who has not looked should not be recorded as having found a
violation.

---

## 111. `update_housing_unit`

**MUTATING**, default OFF (`allow_update_housing_unit`).

**Arguments:** `unit` (required), plus any of `unit_type`, `square_footage`,
`capacity`, `year_built`, `condition`, `related_asset`, `access_card_zone`,
`fsma_worker_facility`, `or_housing_law_compliant`, `max_occupants_per_or_law`,
`last_habitability_inspection`, `smoke_detector_last_test`,
`co_detector_last_test`, `notes`.

**Returns** the unit, `changed` as `[before, after]`, and `compliance_notes`.

Changing `square_footage` **recomputes the lawful occupancy only when the stored
limit was itself the computed one**. A figure somebody typed is kept.

Cannot rename it (assignments point at the docname), and cannot move a building
between parcels — even a manufactured home that really was moved should be
re-registered where it stands, so the assignment history stays attached to the
ground it happened on.

---

## 112. `list_housing_assignments`

Read-only, default ON (`allow_list_housing_assignments`).

**Arguments:** `owning_entity` (or `company`), `unit`, `parcel`, `employee`,
`current_only` (**default `true`**), `from_date`, `to_date`, `limit`.

**Returns** `assignments`, `assignment_count`, `currently_assigned`,
`distinct_units`, `distinct_people`, `with_wage_deduction` and
`deposits_outstanding`.

**`with_wage_deduction` is the compliance answer.** ORS 653 and OAR 839-015
constrain deducting housing from wages, and this is where the assignments that
did are named. Pass `current_only=false` with a date range for the historical
roster.

---

## 113. `create_housing_assignment`

**MUTATING**, default OFF (`allow_create_housing_assignment`).

**Arguments:** `unit` (required), `assigned_date` (required), `employee` or
`employee_name` (one of the two required), `end_date`, `owning_entity`,
`deposit_paid`, `deposit_returned`, `housing_deduction_from_wages` (Yes / No /
Unknown), `allow_multi_occupancy` (default `false`), `notes`.

Auto-named `HA-YYYY-MM-<seq>`, sequenced within the month, so a camp's intake
sorts into seasons without a report.

**Returns** the assignment, `unit_capacity`, `occupants_after`,
`multi_occupancy`, `warnings` and a `section_119_note`.

**This record is the audit trail** for an IRS Section 119 exclusion — lodging on
the business premises, for the employer's convenience, required as a condition of
employment. It records the facts; it does not make the determination.

| Refusal | Why |
| --- | --- |
| An overlapping assignment on that unit | usually a typo. Names the assignment already there. Pass `allow_multi_occupancy=true` for a genuine bunk room |
| A unit typed Toilet-Shower, Kitchen, Bath House, Barn or Shop | nobody is assigned to a shower block; if people really sleep there, its type is wrong |
| A unit marked Uninhabitable | change the condition once it has been repaired and inspected, then assign |
| An `employee` not on file, **where an HR app is installed** | a roster naming somebody payroll has never heard of has already drifted |
| An `end_date` before `assigned_date` | nobody moved out before they moved in |
| A `deposit_returned` larger than `deposit_paid` | a refund of money nobody took |

Overlap is **inclusive at both ends**: somebody moving out on the 15th and
somebody moving in on the 15th shared the cabin that night, and a camp manager
told otherwise puts two people in one bed.

**Where no HR app is installed** the employee is stored as text and the tool says
so. A camp roster that cannot be written until an HR module exists is a camp
roster nobody keeps.

---

## 114. `end_housing_assignment`

**MUTATING**, default OFF (`allow_end_housing_assignment`).

**Arguments:** `assignment` (required), `end_date` (required),
`deposit_returned`, `notes` (appended, not replacing), `dry_run` (default
`false`).

**Returns** the assignment, `changed` and `warnings`.

**It never deletes.** An assignment removed when the person leaves cannot defend
a Section 119 classification, cannot answer a wage claim about a housing
deduction, and cannot tell an investigator who was in the camp the week in
question.

| Refusal | Why |
| --- | --- |
| An assignment that has already ended | re-dating a departure is a correction, not a close. The refusal names the date on record |
| An `end_date` before the start | nobody moved out before they moved in |
| A `deposit_returned` larger than the one on record as paid | a refund of money nobody took |

A deposit still held is **reported**, so it is either refunded or explained.

---

## 115. `get_housing_capacity`

Read-only, default ON (`allow_get_housing_capacity`).

**Arguments:** `owning_entity` (or `company`), `parcel`.

**Returns** `parcel_count`, `unit_count`, `total_capacity`,
`total_lawful_capacity`, `currently_assigned`, `open_beds`,
`overdue_inspection_count`, a `by_parcel` breakdown, `inspection_window_days`
and a plain `readout` — one sentence per parcel.

```
get_housing_capacity {}
→ Mill Creek - ETC: 25 residential units, capacity 100, currently 87 assigned,
  13 open. Overdue habitability inspections: 3.
```

Non-residential units are counted but contribute no capacity: a bath house and a
shop are part of the camp, nobody sleeps in them, and adding their zero capacity
to the total would make the register look thinner than it is.

---

## 116. `get_employee_housing_history`

Read-only, default ON (`allow_get_employee_housing_history`).

**Arguments:** `employee` (required — an Employee id, or the person's name as
the roster has it).

**Returns** `assignment_count`, `currently_assigned`, `units_lived_in`,
`first_assigned`, `last_assigned`, `deposits_paid`, `deposits_returned`,
`deposits_outstanding`, `wage_deduction_taken`, every `assignment`, and a plain
`readout`.

```
get_employee_housing_history {"employee": "Antony"}
→ Antony assigned MC-Cabin-12 - MC 2026-06-01 → 2026-07-15
  Antony is currently unassigned.
```

Matches on the employee id first and then on the name, because a site with no HR
app records the name and a site with one records the id.

---

## 117. `set_field_boundary`

**MUTATING**, default OFF (`allow_set_field_boundary`). Needs `shapely` and `h3`.

**Arguments:** `field` (required), `boundary_geojson` (required),
`owning_entity`, `dry_run` (default `false`).

**Returns** `area_computed_acres`, `acreage_recorded`,
`area_disagreement_ratio`, `boundary_centroid`, `boundary_bbox_geojson`,
`h3_cell_counts`, `h3_resolutions`, `zones_outside_boundary`, `warnings` and
`changed`.

The polygon may arrive as a bare geometry, a Feature, or a FeatureCollection
holding exactly one Feature — whichever your export button produced. Coordinates
are `[longitude, latitude]` in degrees, as GeoJSON specifies.

**Everything else is derived and none of it can be set directly.** Centroid,
bounding box, H3 coverage at resolutions 6–10, and the area the polygon
encloses are all functions of the shape. A field a caller could edit
independently is one that will disagree with the polygon, and the disagreement
surfaces as a geofence saying no to somebody standing in the right place.

| Refusal | Why |
| --- | --- |
| Not valid JSON, or not a GeoJSON object | reported with the parser's own message |
| A Point, LineString or GeometryCollection | a boundary has to be an area |
| A ring that is not closed | a boundary that does not come back to itself does not enclose anything |
| A ring with fewer than four positions | a closed ring needs at least four, because the first and last are the same point |
| Coordinates off Earth | a latitude past 90 usually means the pair is the wrong way round, and the refusal says so |
| A self-intersecting polygon | a bow tie has an area a computer will report and a containment test nobody can trust. It is what two swapped vertices produce |
| An area more than **25%** from the recorded acreage | at that point one of the two is about a different piece of ground |

**Warns rather than refusing:** a 5–25% area difference (a deed, a GIS trace and
a tape measure routinely disagree, and both figures are kept); a shape spanning
more than a degree of latitude or longitude — about seventy miles, which is a
county rather than a block; coordinates at `[0, 0]`, which is what an unset
coordinate looks like; zones on this block that now fall outside it; and — from
v0.32.0 — a block that hangs over its **parcel's** boundary.
`boundary_contained_in_parcel` comes back `true`, `false`, or `null` where the
parcel has no shape of its own to check against. From v0.12.0 to v0.31.0 this
warning was unconditional and said a parcel had no boundary at all;
**118a** is the tool that gave it one.

```
set_field_boundary {"field": "Yellow Camp Block 3", "boundary_geojson": "{...}"}
→ Yellow Camp Block 3 - MC: boundary set, 25.7089 acres,
  centroid 45.6015,-121.178
```

---

## 118. `set_zone_boundary`

**MUTATING**, default OFF (`allow_set_zone_boundary`). Needs `shapely` and `h3`.

**Arguments:** `zone` (required), `boundary_geojson` (required),
`owning_entity`, `dry_run` (default `false`).

**Returns** everything **117** returns, plus `boundary_contained_in_field`.

**Containment is reported, never enforced.** The obvious rule is that a zone must
sit inside the field it waters, and it is wrong often enough to matter — a shared
water line crosses a boundary, a pump house sits on the headland, a mainline runs
down a road easement. Refusing those would make them unrecordable, so:

| `boundary_contained_in_field` | Means |
| --- | --- |
| `true` | the zone is wholly inside its block |
| `false` | it is not, which is allowed and warned about |
| `null` | the block has no boundary of its own, so nothing could be checked |

That last row matters: "we could not check" and "we checked and it is outside"
are different answers, and reporting the first as the second is a lie a report
would repeat.

The area comparison is against the zone's own acreage, which is computed from its
square footage — so a polygon and a design drawing disagreeing by a quarter means
one of them is a different zone.

---

## 118a. `set_parcel_boundary`

**MUTATING**, default OFF (`allow_set_parcel_boundary`). Needs `shapely` and
`h3`. v0.32.0.

**Arguments:** `parcel` (required), `boundary_geojson` (required),
`owning_entity`, `dry_run` (default `false`).

**Returns** everything **117** returns, with `outside_boundary` in place of
`zones_outside_boundary`.

**The outer shape — the one the deed and the tax bill both describe.** A parcel
is the unit the county assessor, the deed and the appraisal all agree on, which
is why the register is keyed on it; from v0.32.0 it is also the unit that carries
an outline. Everything registered on the parcel is expected to sit inside it, and
this is the tool that says which things do not.

`outside_boundary` is `{doctype: [names]}` over the three registers that hang off
a parcel:

| Register | Compared how |
| --- | --- |
| `Field` | polygon inside polygon |
| `Irrigation Zone` | polygon inside polygon |
| `Housing Unit` | its `gps_latitude` / `gps_longitude` inside the outline (v0.32.0 gave it coordinates) |

**Only things that have a position are tested.** A block with no polygon and a
cabin with no coordinates are not outside the parcel — they are *unmapped*, which
is a different answer. Listing them as violations would bury the two names that
mean something under fifty that do not.

**Containment is reported, never enforced**, and it matters more here than
anywhere else this app checks it: a planting that predates a deed split really
does straddle the line, and a cabin on the far side of a road easement is a real
cabin. Refusing those would make them unrecordable.

Refuses everything **117** refuses, comparing the polygon's area against the
parcel's own deeded or GIS acreage. On a parcel that recorded figure is usually
the one to trust, and the refusal message says so.

```
set_parcel_boundary {"parcel": "Mill Creek", "boundary_geojson": "{...}"}
→ Mill Creek - ETC: boundary set, 329.9367 acres,
  centroid 45.6005,-121.178
```

---

## 119. `find_fields_containing_point`

Read-only, default ON (`allow_find_fields_containing_point`). Needs `shapely`
and `h3`.

**Arguments:** `lat` (required), `lon` (required), `owning_entity`.

**Returns** `match_count`, the matching `fields` in full, the point's own
`h3_cells` at every stored resolution, `searched`, `candidates_after_bbox`,
`fields_without_a_boundary`, `boundary_inclusive` and a `note`.

**This is the geofence query.** "Is this pick inside an assigned block?" "Is this
worker on ground they are rostered to?"

**Bounding box first, then point-in-polygon exactly.** The prefilter is the
bounding box rather than the H3 index, and that is deliberate: a bbox is a
guaranteed superset of the shape it bounds, so a candidate set built from it
cannot miss the right answer. `candidates_after_bbox` reports how many survived
the cut, and the exact test settles every one of them.

**The boundary counts as inside.** A pick recorded on the edge of a block is in
the block; a geofence that excludes its own boundary tells a picker standing on
the headland that they are nowhere.

**`fields_without_a_boundary` is not decoration.** On a half-mapped farm an empty
result means "not inside any *mapped* block", not "not on the farm", and those
are different things to act on. The failure this guards against is the quiet one:
a geofence saying no because the ground was never traced, read as a policy
decision.

```
find_fields_containing_point {"lat": 45.6015, "lon": -121.1780}
→ [45.6015, -121.178] is inside 1 block(s): Yellow Camp Block 3 - MC
```

---

## 120. `find_fields_by_h3_cell`

Read-only, default ON (`allow_find_fields_by_h3_cell`). Needs `shapely` and `h3`.

**Arguments:** `cell` (required — an H3 index at any resolution),
`owning_entity`.

**Returns** `cell_resolution`, `matched_at_resolution`, `probe_cell`,
`stored_resolutions`, `match_count`, the matching `fields`, `searched` and a
`note`.

The spatial-index query, for joining against anything else keyed on H3 — a bucket
log, a crew track, a weather grid.

**Stored cells are every cell the shape TOUCHES**, not every cell whose centre is
inside it. H3's default polygon fill is centre-based, and an orchard block is
smaller than one cell at resolutions 6 through 8 — so the default returns an
empty set for most fields, and an index built on it would answer "in no field"
for a point plainly in one. The fill uses `contain="overlap"` instead, which is a
true superset.

Resolution handling, and the result says which was used:

| Query resolution | How it matches |
| --- | --- |
| 6–10 | directly against the stored cells at that resolution |
| finer than 10 | rolled up to 10, then matched |
| coarser than 6 | each block's resolution-6 cells are rolled up to the query's resolution and compared there |

**A match means the cell touches the block**, not that everything in the cell is
inside it. Use **119** when the question is about a specific position.

---

## 121. `import_field_boundary_geojson`

**MUTATING**, default OFF (`allow_import_field_boundary_geojson`). **Dry run by
default.** Needs `shapely` and `h3`.

**Arguments:** `feature_collection` (required — a GeoJSON FeatureCollection, as
an object or a JSON string), `parcel` (a default for features with no
`parcel_hint`), `owning_entity`, `apply` (default `false`).

Each Feature's `properties` needs `field_name`, and `parcel_hint` unless a
default `parcel` is given.

**Returns** `feature_count`, `would_set`, `skipped`, the per-feature `results`,
and `set` / `failed` when applied.

**Per-feature, not whole-batch — the opposite of 103, on purpose.**
`import_farm_app_fields` CREATES records, so a half-run leaves a farm somebody
has to reconcile and it refuses the whole batch on the first bad record. This one
only sets a field on records that already exist, so one bad feature in forty is a
bad feature: naming it and applying the other thirty-nine beats refusing the lot.

**It never creates a Field.** A feature naming a block that is not registered is
skipped with that said — register it first with **99** or **103**.

Every per-feature refusal **117** makes applies here too, including the 25% area
rule, and each is reported against its own feature index so a malformed
collection can be fixed one line at a time.

---

## 122. `list_family_members`

Read-only, default ON (`allow_list_family_members`).

**Arguments:** `active` (boolean), `relationship`, `related_to`, `limit`.

**Returns** `members`, `member_count`, `active_count`, `by_relationship`,
`with_related_party`, `without_related_party`, `without_relationship`,
`without_related_to`, `related_to_free_text` and a `note`. Every row carries
`described_as` — `"Alexander Polehn — Son of Tim Polehn"` — which is the sentence
this register exists to be able to say.

**The lists at the end are the point.** A missing related-party entry is not
a gap for most of these — a relative who only receives transfers needs no W-9 and
no disclosure. It IS a gap for one who also holds a role: a member, a lessor, a
trustee. A list that read as forty problems would be a list nobody acts on.

`without_related_to` is a different question: not "do they have a tax identity"
but "whose relative are they". Records written before v0.13.0 have no
`related_to`, **nothing backfilled them and nothing will** — which of two members
somebody is the child of is a fact only the family has — so they are listed and
warned about rather than guessed at.

---

## 123. `get_family_member`

Read-only, default ON (`allow_get_family_member`).

**Arguments:** `family_name` (required — the person's name, which is the
docname).

**Returns** the member, `related_party_detail`, `relationship_chain`,
`relationship_path`, and **every posting that names them**: `posting_count`,
`first_posting`, `last_posting`, `net_amount`, `companies`. Plus
`compliance_notes`.

**The chain crosses two registers to answer one question.** `related_to` goes to
another *person* and is followed as far as it goes; `related_party` goes to the
*same* person's entry in the register that holds roles and entities, and is
followed once, at the top. That is how `relationship_path` reads
`Alex → Son of Tim → Manager of Orchard Meadow, LLC`, which no single record
holds. It terminates on a cycle, on a depth limit, or on free text, and the last
entry says which in `chain_ends_because`.

The postings are read from the GL rather than kept on the record, so the count
cannot drift from what actually happened — which is the entire value of it. "We
moved money to Alex eleven times last year" is the question a family petty-cash
arrangement gets asked, and it has one true answer.

Never returns more than four digits of a taxpayer id, even from the linked
related-party record — the same rule **88** keeps, kept here because this is a
second door onto the same field.

---

## 124. `create_family_member`

**MUTATING**, default OFF (`allow_create_family_member`).

**Arguments:** `family_name` (required), `relationship`, `related_to`,
`related_party`, `active` (default true), `notes`.

`relationship` takes **Son** and **Daughter** as well as Child, Spouse, Parent,
Sibling, Grandchild, Grandparent, In-Law and Other. Son and Daughter arrived in
v0.13.0 *beside* Child rather than instead of it: records already saying Child
are still true, and asking somebody to re-pick a value that has not changed is
work with no answer at the end of it.

**WHY THE REGISTER HAS TO EXIST.** ERPNext resolves a posting's counterparty as a
Dynamic Link THROUGH its party type: `party_type` is a `Link` to `DocType`, so
`Family` only works because this app ships a Family DocType, and `party` only
works if the person is a record in it. Customer, Supplier, Employee and
Shareholder each have one.

**IT HOLDS NO TAX ID, ON PURPOSE.** A transfer below the IRS annual gift
exclusion is not compensation for services: no W-9, no 1099, which is the whole
reason this party type is separate from Supplier. A relative genuinely paid for
work is a Contact or a Supplier, and the posting should say so rather than the
exclusion being widened. Where a relative ALSO holds a role worth disclosing,
`related_party` points at the register that keeps four digits and never more.

**`related_to` ANSWERS "OF WHOM".** A register that says "Alexander Polehn —
Son" and cannot say whose son is ambiguous the moment an entity has two members.
Pass the other person's name: a Family docname, a Related Party docname or party
name, or — for somebody in neither register — their name as plain text. The
result reports which register answered as `related_to_doctype`, and `None` there
means free text rather than a failure.

It is a `Data` field rather than a `Link` on purpose: a Frappe `Link` points at
exactly one doctype, a `Dynamic Link` needs a discriminator column beside it, and
the answer here is one of three kinds of thing. Resolution happens on read.

| Case | What to do |
| --- | --- |
| Simple — Alex is Tim's son | `related_to="Tim Polehn"` |
| Complex — Alex is Tim's son AND Donella's grandson | `related_to="Tim Polehn"`, and `"also grandson of Donella Polehn"` in `notes` |
| Genuine genealogy | not this field. A child table of relationships would turn a register whose job is to make a posting resolve into a genealogy database; if it is ever really needed it is a Family Tree doctype of its own, and this field would point into it |

| Refusal | Why |
| --- | --- |
| A second record for the same name | the name is the docname, and it is what every posting points at |
| A `related_party` that does not exist | register it with **89** first, or leave it blank |
| An unknown `relationship` | the options are read off the DocType |
| Somebody related to themselves | a cycle of length one |

**Warns rather than refusing** when no relationship is given: "why did money go
to this person" is the first question these postings get asked, and a name alone
does not answer it. Same for no `related_to` — the result says "unassigned
parent" and the record is still created.

---

## 125. `update_family_member`

**MUTATING**, default OFF (`allow_update_family_member`).

**Arguments:** `family_name` (required), plus any of `relationship`,
`related_to`, `related_party`, `active`, `notes`. An empty string clears
`related_to`.

**Returns** the member and `changed`, every one as `[before, after]`.

**This is where an existing record acquires `related_to`.** Nothing backfilled it
on upgrade and nothing will: a migration that guessed which member somebody is
the child of would produce a register that looks complete and is wrong.

**Cannot rename them.** The name IS the docname and every journal entry that
named them points at it; renaming would orphan those postings.

**Retiring somebody is `active=false`, not a delete**, and the result reports how
many postings would have been orphaned — which is the argument for the flag
existing.

---

## 126. `update_journal_entry_party`

**MUTATING**, default OFF (`allow_update_journal_entry_party`).

**Arguments:** `journal_entry`, `line_index` (1-based, the way ERPNext numbers
them), `party_type`, `party`, `reason` — all required — plus
`allow_non_party_account` and `dry_run`.

**Returns** `updated`, `before` and `after` as `{party_type, party}`,
`line_index`, `line_name`, `account`, `debit`, `credit`, `gl_entries_updated`,
`comment_added`, `tables`, and a `note`.

**The case it is for.** A payment leaves a shared account and only afterwards
does anybody establish which of two sons it was for. The posting is right — right
account, right amount, right date — and one attribution column is empty or wrong.
The alternatives are cancel-and-repost, which replaces a clerical correction with
a cancelled voucher, a reversing pair and a new number that no statement
reconciles against; or the Desk, which is what an MCP server exists so nobody has
to open.

**It cannot move a balance.** Account, debit, credit, date, cost center and
remark are not arguments to it. The trial balance after the call is
arithmetically identical to the one before, which is what makes editing a
submitted document defensible at all: this is attribution, not restatement. No
journal entry is written and nothing is reversed.

**It writes in both places the party lives.** `tabJournal Entry Account` is what
the voucher shows; `tabGL Entry` is what every ageing report, party ledger and
statement of account reads. Updating one and not the other leaves the voucher and
the reports disagreeing with nothing to say which is right — worse than not
having edited. GL rows are matched on `voucher_detail_no`, the line's own
docname, so an entry with two lines to the same account for the same amount stays
distinguishable, and `gl_entries_updated` reports how many moved.

This is the one field-level exception to "every write goes through the document"
in `tools/mutate.py`, and it is fenced: still the ORM's db layer rather than raw
SQL, still incapable of touching an amount, and there is no supported
alternative — ERPNext marks `party` as not allowed on submit. A **draft** is
saved through the document instead, since it has written no GL Entries and full
validation can still run.

**The reason is written twice**: to the entry's own comment thread, where an
accountant with the voucher open will see it, and to the MCP Action Log, where it
survives whatever happens to the document.

| Refusal | Why |
| --- | --- |
| A cancelled entry | it and its reversing rows are the evidence a posting was made and undone; editing one makes that evidence say something that never happened |
| `line_index` outside the entry | the count is named in the message |
| A rounding or write-off line | ERPNext wrote it itself to absorb a fraction of a cent, and attributing that fraction to a person is not a fact about the person |
| A bank or cash line | that is the operation's own money, and a party there makes every ageing report claim they owe its balance. **No escape hatch** |
| A party type this site has not registered | with the registered ones listed, and `register_custom_party_types` named where it applies |
| A party that is not a record in its register | ERPNext resolves `party` as a Dynamic Link through `party_type` |
| `party_type` without `party`, or either omitted | one without the other is an unresolvable reference, not half an answer. Pass both empty to clear |
| A change that changes nothing | |
| An account type that does not normally carry a party | Receivable, Payable, Equity and blank go through silently; anything else needs `allow_non_party_account=true`, which is the way past rather than a wall. An ordinary expense account carries no `account_type` at all, which is the commonest case and needs nothing |

`dry_run: true` reports the whole plan, including how many GL rows would move,
without writing.

**A Family attribution stays out of the 1099.** `generate_1099_prefill` excludes
Family-party postings and reports the count; attributing a transfer correctly
does not make it reportable.

---

## 127. `convey_parcel`

**MUTATING**, default OFF (`allow_convey_parcel`).

**Arguments:** `parcel`, `target_company`, `effective_date`, `reason` — all
required — plus `owning_entity` / `company` (to narrow a bare parcel name),
`new_title_holder` and `dry_run`.

**Returns** `conveyed`, `from`, `to`, `from_entity`, `to_entity`,
`migrated_attachments`, `migrated_leases`, `migrated_housing_units`,
`migrated_fields`, `migrated_irrigation_zones`,
`migrated_housing_assignments`, `relinked_records`, `relink_detail`,
`title_holder_status`, `appraisal_document_status`, `refusals`, `warnings`, the
whole new parcel, and a `note`.

**This is the door `update_parcel` refuses to be.** Ground changing hands has a
date, an instrument behind it and consequences for two sets of books; a tool that
let it happen by editing a field would record none of them. `reason` is mandatory
and is the narrative — the deed, the assignment, the trust amendment.

**It deletes and recreates, which is the honest shape.** A Parcel's docname
encodes its entity (`Mill Creek - OML` vs `Mill Creek - HLD`), the same way every
Account docname carries a company abbreviation, so there is no field to change
that makes the move true. The order is: create the new record, repoint everything
at it, move the attachments, delete the old one, write the event. Frappe refuses
to delete a document another document links to, so a register missing from
`realestate.PARCEL_REFERRERS` fails the whole call rather than leaving a silent
orphan — and a standalone test checks that tuple against the shipped DocType JSON
so a register added later cannot be forgotten quietly.

**The parcel's own short key is preserved.** Every Field, Irrigation Zone and
Housing Unit is named `<its name> - <PARCEL abbr>` — the parcel's key, not the
company's — so all of a camp's cabins keep the docnames they have always had and
only their `parcel` link moves. `owning_entity` moves with them on the registers
that describe the *ground*; a **Lease's** does not, because a conveyance does not
change who signed a contract. That is a novation, and it is its own document.

**It writes no Journal Entry.** Basis transfer and any gain or loss recognised
are entries with real tax consequences that somebody should write on purpose,
with a narrative of their own — not produce as a side effect of filing a deed.
The result names the entries still owed. Same discipline as `close_note_payable`.

**The trail lives on the survivor.** A conveyance destroys one record and creates
another, so the new parcel's `conveyance_events` child table is the only place
the history can be: it names the entity the ground came from and the docname it
had there, so a reader who finds `Mill Creek - HLD` and remembers
`Mill Creek - OML` can join the two without either record still existing.

| Refusal | Why it is a different document's job |
| --- | --- |
| An **Active**, unterminated lease whose term covers the conveyance date, named | conveying out from under a live lease needs a novation or a termination first. A lease with **no expiration date** counts as running — reading a missing end date as "already over" is the one wrong answer that fails silently |
| A linked Fixed Asset | that is the balance-sheet side and it moves by posting, not by filing |
| A target with no chart of accounts, or no cost centers | a parcel filed against an entity that cannot carry a cost is one somebody finds again in six months |
| A parcel name, assessor id or abbreviation the target already uses | the last one because a silently changed key would file the parcel's future blocks under a different suffix from its existing ones |
| More referring records than the per-register ceiling | no silent caps: a half-conveyed parcel is worse than an unconveyed one |

**Every refusal comes back at once**, not one per round trip. `dry_run: true`
returns the whole plan and the whole refusal list without touching anything.

**The appraisal report does not follow** if it is filed in the old entity's
archive — a Governance Document belongs to a company, and pointing at it across
that boundary is what the archive exists to prevent. That is reported as
`appraisal_document_status: "unlinked_needs_reattach"` in the result *and* in the
conveyance event, never as a silent null. The appraised value and its as-of date
DO come across: they are facts about the ground.

A `title_holder` registered against the entity the ground just left is dropped
with a warning, because one filed under the old entity would read as current and
would be wrong. Pass `new_title_holder` to set the right one in the same call.

**Atomic by construction.** `registry.dispatch` rolls the transaction back before
it logs, so a conveyance that dies half way leaves neither parcel changed rather
than leaving two.

---

# v0.15.0 — the compliance framework

Thirty-two tools. They interlock, and reading them in wave order is the fastest
way to understand what any one of them is for.

The organising idea is one sentence: **compliance is a lens on operational data,
not a duplicate set of records.** Every spray IS an EPA and Worker Protection
Standard record; every hire IS an I-9 record; every bucket IS an FSMA
traceability record. The test for whether a feature is woven in or bolted on:
*does removing it break OPERATIONS, or only break COMPLIANCE REPORTING?*

## 128. `get_compliance_field_map`

Read-only. What compliance requires of an OPERATIONAL record on this site, field
by field: which DocType carries it, which framework wants it, why, and — the
column that matters — what breaks in the day-to-day WORK if it is missing.
Reports which fields are actually present here and which are not.

`docs/compliance_fields.md` is the same content in prose, and a test asserts the
two cannot drift apart.

## 129. `install_compliance_fields`

**MUTATING, and the only tool in this app whose switch ships ON.**

Adds the compliance columns to the DocTypes where the work happens: applicator,
EPA registration number, REI, PHI and weather on **Spray Log**; I-9 status, W-4
status, wage-law jurisdiction and farm labor contractor licensing on
**Employee**; picker, crew, block, bin and shipment on the **BucketLog bridge**.
Verifies (and does not touch) the compliance columns on **Housing Unit** and
**Field**, which this app already ships.

| Argument | Meaning |
| --- | --- |
| `dry_run` | Report what would be added, including the backlog counts, and write nothing |

**This is the one place erpnext_mcp extends a DocType it did not create**, and
`erpnext_mcp/compliance_fields.py` makes the argument at length. The short
version: compliance woven into the operational record is defensible under audit
and a shadow log beside it is not, and you cannot weave anything into a DocType
you refuse to touch. The cost is real and stated — uninstalling this app drops
those columns and everything typed into them, which `before_uninstall` now names
by hand.

Every field is a `Custom Field`, so the target app's repository and migrations
are untouched. Idempotent: the same installer runs on every `bench migrate` and
a second run creates nothing.

**The number worth reading is `backlog`.** Seven fields are required, and Frappe
binds `reqd` on save rather than retroactively — so history stays readable and
stops being re-saveable. The count of rows that would now fail is the operation's
compliance debt, stated in rows.

A DocType not on this site is skipped BY NAME with the app that would bring it.

---

## Wave 2 — the four external-evidence DocTypes

Four kinds of evidence arrive from OUTSIDE the operation and have no operational
act to hang off. Nobody writes a harvest hygiene SOP by harvesting.

## 130. `list_compliance_policies`
## 131. `get_compliance_policy`

Read-only. The SOP library, and one procedure in full with its whole version
chain (walked in BOTH directions) and every audit corrective action that cited
it.

`without_a_document` in the listing is the list worth acting on first: a policy
record with no attached procedure is a claim that a procedure exists, and an
auditor asks to read the procedure.

## 132. `create_compliance_policy`
## 133. `update_compliance_policy`
## 134. `supersede_compliance_policy`

MUTATING (off). **The version is a FIELD, not part of the name** — a policy at v3
is the same record every audit finding already cites — so a second policy under
an existing name is refused and points at `supersede_compliance_policy`.

Superseding writes **both ends of the chain in one act**, because "which
procedure was in force on the day this happened" is asked from whichever end the
auditor starts. Refuses a policy superseding itself, one already superseded (two
successors make the question unanswerable), and a successor whose effective date
PREDATES the one it replaces. The superseded policy is historical rather than
wrong, and audit packets covering the dates it governed still include it.

## 135. `list_certifications`
## 136. `get_certification`

Read-only. The certificate and licence register, **soonest expiry first** — the
order somebody works them in — with what has lapsed and what is inside its
renewal window.

`expired` is read from the DATE, never from the status field. Nothing in this app
rewrites a status when a date passes: a controller that did would only run on
documents somebody saved, so the expired certificates would be exactly the ones
still reading Active.

`get_certification` resolves the holder against the Related Party, Family,
Employee and Company registers and reports which answered. A name in none of them
is not a failure — an applicator licence held by a contractor on nobody's payroll
is exactly what the fallback is for — and it returns the full renewal history
including **every period the certificate was allowed to lapse.**

## 137. `create_certification`
## 138. `update_certification`
## 139. `renew_certification`

MUTATING (off). `renewal_window_days` is a LEAD TIME, not a reminder preference:
90 days by default because that is roughly what an Oregon farm labor contractor
renewal takes once the bond and background check are counted.

**Editing the expiration forward through `update_certification` is refused** and
points at `renew_certification` — editing it in place would produce a certificate
that looks as though it never expired, which is exactly the fact somebody would
want hidden and exactly the fact an auditor asks about. `renew_certification`
appends to a history, requires saying what was actually done to earn the new
term, and **reports any lapse rather than hiding it**: renewing late does not
close a gap that already happened.

## 140. `list_regulatory_filings`
## 141. `get_regulatory_filing`
## 142. `create_regulatory_filing`
## 143. `update_regulatory_filing`

Reads on, writes off. What went to an agency, when, under what docket number, and
what came back.

**A filing marked Submitted with no submission date is refused.** A filing nobody
can prove was made is a filing that was not made — the agency's position in a
dispute is that they have no record — and a half-filled record would be
assembled into an audit packet and read as evidence of something that may not
have happened. A Draft with no dates is exactly what a filing being prepared
looks like and is allowed.

Recording a response auto-dismisses the filing's response alert on the next
sweep. Nobody has to switch it off.

## 144. `list_audit_events`
## 145. `get_audit_event`
## 146. `create_audit_event`
## 147. `update_audit_event`
## 148. `close_audit_event`

Reads on, writes off. Every audit and inspection, its findings, and one row per
thing that has to be fixed — with the deadline the SCHEME set, an owner who is a
PERSON rather than a department, and what actually changed.

**An operation is not judged on having no findings.** Every audit produces some,
and a clean report usually means the auditor did not look hard. It is judged on
closing them.

`update_audit_event`'s `corrective_actions` REPLACES the whole table, which is
the only safe semantics for rows addressed by index — a merge would silently
reorder them and close the wrong finding. `add_corrective_actions` appends and
`close_corrective_action` closes one, so nobody has to resend every row exactly to
change one. Closing requires saying what changed: a tick in a box is what an
auditor is trained to disbelieve, and it is refused.

**`close_audit_event` REFUSES while any corrective action is open**, naming every
one — and the controller refuses it too, so there is no second door. A closure
date over an open finding is the most misleading thing this app could record:
`generate_audit_packet` reads it as "this audit is finished". An audit that raised
no findings at all is closeable; a clean PrimusGFS is a real event.

---

## Wave 3 — the Kairotic Compliance Calendar

**Chronos serves Kairos.** The clock runs the sweep; the sweep decides nothing.
Nine rules ask whether a condition is true RIGHT NOW, and fire on that state
rather than on the date the sweep happened to run.

## 149. `get_compliance_calendar`

Read-only, on by default — the main read of the whole framework. What is due and
what is late, worst first, grouped by category.

| Argument | Meaning |
| --- | --- |
| `severity_min` | `Critical`, `Warning` or `Info` — this severity and worse |
| `days_ahead` | Only alerts due within this many days. **Overdue alerts are always shown**, because they were due in the past |
| `category` | Certifications, Policies, Workforce, Records, Housing, Water and Sanitation, Spray and Pesticides, Filings, Audits, Other |
| `alert_type` | One rule's alerts |
| `regime` | **v0.19.2.** Only alerts that are evidence for ONE audit: FSMA, GAP, GlobalGAP, PrimusGFS, NOP, OTCO, WPS, OR-OSHA, Internal, Other |
| `include_snoozed` / `include_dismissed` | Default false. Snoozed alerts are hidden and COUNTED |
| `as_of` | Read the calendar as of a date |

Categories are chosen so a whole group can be cleared in one afternoon: every
housing item is one walk round the camp, every certificate is one trip to an
agency website.

`regime` is the other axis, and it is the one an inspection is read along:
"everything OR-OSHA will ask about in October" is one afternoon's work and
"everything" is not. Matching is by TAG, never by substring — `GlobalGAP`
contains `GAP`, and a substring match would put another scheme's findings in
front of a USDA GAP auditor. An unrecognised value is **refused**, because an
empty compliance calendar reads as a clean one. `Internal` means the
operation's own standard: real work with a real due date and no outside
auditor.

It reports which rules cannot run on this site at all, because **an empty
category is not the same as a clean one.**

## 150. `list_compliance_rules`

Read-only. Every rule with its `kairotic_gate` — the state that makes it ripe —
plus the framework it serves and whether it can run here. A rule listed as
unavailable raises nothing AND dismisses nothing: an absent DocType is not
evidence that anybody did the work.

**Since v0.22.0 these are records, not code.** Each rule is a Compliance Rule
document whose thresholds, scope, citations, regimes and message an operator can
edit without a release. `editable` says whether this site has migrated yet;
`shape` says how much of each rule is data. Filters: `regime`, `category`,
`target_doctype`, `shape`, `active`, `limit`.

The thirteen, what makes each one fire, and how each one migrated:

| Rule | Shape | Fires when | Silent when |
| --- | --- | --- | --- |
| `certification_expiring` | built-in | inside the lead time the certificate's OWN issuing body takes; Critical inside 30 days | 200 days out; superseded; revoked |
| `policy_review_overdue` | declarative | a procedure IN FORCE is past the review date IT committed to | a draft; a superseded or retired version |
| `water_test_stale` | built-in | a block **in active spray rotation** has no test inside 90 days | fallow ground; a block nobody has sprayed this season |
| `housing_inspection_overdue` | declarative | a cabin somebody can be ASSIGNED to has no walk inside a year | a shower block; a unit already Uninhabitable |
| `housing_detector_test_stale` | built-in | a **FSMA worker facility** has an untested smoke or CO detector | a shed on the same parcel |
| `i9_expired` | declarative | an ACTIVE employee's I-9 has expired | Pending (the lawful 3-day window); a former employee |
| `flc_license_expiring` | declarative | a crew boss's licence is inside 90 days; Critical inside 30 | an employee with no licence |
| `filing_response_due` | declarative | a SUBMITTED filing has no response and the deadline is near | a draft; a filing that was answered |
| `audit_action_overdue` | built-in | an action is past the deadline the SCHEME set | an action with no due date; a closed audit |
| `housing_corrective_action_open` | built-in | a camp finding is open AND unsuperseded | a later CLEAN record for the same unit; a closed action |
| `water_test_contamination` | built-in | a sample came back dirty and is still the latest word on that zone | a later clean sample from the same source |
| `training_expiring` | declarative | a worker's training is inside the 90-day window a retraining takes to arrange; Critical inside 30 | training with no expiry at all |
| `supervisor_review_lapsed` | built-in | an activity record has been on file a fortnight with nobody's §112.161(b) review | a record signed and dated by a supervisor |

**Declarative** means the whole rule is on its record. **Built-in** means every
tunable is on the record and only the SHAPE of the join is shipped code — a
finding superseded by a later clean record, a child table reduced to its worst
row, two date fields that only matter together. There is a third shape,
`custom_python`, and no shipped rule uses it. `docs/configurable_compliance_framework.md`
argues each one and names the primitive that would shrink the list.

## 151. `get_audit_readiness`

Read-only. Resolved alerts over alerts raised, as one percentage — because a
count only means something to somebody who already knows what normal looks like,
and a percentage is comparable to yesterday's.

It also reports **how the score was earned**: `resolved_by_hand_percent` splits
conditions that cleared themselves from dismissals somebody made. An operation at
95% through dismissals is a different operation from one at 95% because the work
got done, and a score that could not tell them apart would be worth gaming. A
single open Critical is called out regardless of the percentage.

## 152. `refresh_compliance_alerts`

MUTATING (off). Runs the whole rule set now instead of waiting for tonight's
scheduled sweep. Creates, refreshes, reopens and auto-dismisses.

**It touches no operational record.** Every rule is a read; the only rows written
are this app's own Compliance Alerts. That is why it is safe at any moment and
why the nightly scheduler calls the same function.

**It cannot duplicate an alert.** Each alert's docname is derived from its rule
and its source record and from nothing that changes daily, so tonight's sweep
finds and refreshes what last night's wrote — and a snooze somebody set last week
survives. A dismissal a PERSON made is never reopened.

**`regime` (v0.19.2) runs only the rules that raise one audit's evidence** — for
the morning before an inspection, when re-scanning every block's water is a
minute nobody has. A rule it skips raises nothing **and dismisses nothing**: a
narrowed sweep that cleared the rules it did not run would empty most of the
calendar and look like progress. `rules_skipped` names each one, `rules_run`
excludes them, and the reported counts are about that regime only.

## 153. `snooze_alert`

MUTATING (off). Hides one alert until a date. **Not a dismissal:** the condition
is still true, the alert still exists, and it reappears on its own — a snooze is
a date rather than a flag somebody has to clear. A date not in the future is
refused.

## 154. `dismiss_alert`

MUTATING (off). Takes one alert off the calendar, with a **mandatory reason**.
The reason is the only part of the record nobody can reconstruct — the alert
itself the sweep can rebuild from the source record — and it is the answer when
the same finding turns up next year. The alert is never deleted: the record that
somebody looked and decided is itself compliance evidence.

Dismissing an alert changes nothing underneath it. Dismissing one about an
expired certificate does not renew the certificate.

## 155. `dismiss_alert_bulk`

MUTATING (off). **Dry run defaults TRUE and the first call writes nothing.**

The whole calendar is one filter away: a `severity` typed where an `alert_type`
was meant matches everything, fails nothing, looks exactly like success, and
leaves an operation reading as compliant while nothing has been fixed. A call
with no filter at all is refused. Capped at 200 per run.

---

## Wave 4 — audit packets and the Command Center

## 156. `list_audit_packet_types`

Read-only. The eight regimes — FSMA, GAP, GlobalGAP, OSHA, DOL, EPA, USDA_NIFA
and an unscoped Other — with the sections each pulls, what it is scoped to, and
**which sections will be empty on this site** because the DocType behind them is
not installed.

## 157. `generate_audit_packet`

MUTATING (off). Assembles every piece of evidence for one audit type over one
period into a PDF and files it as a Governance Document in the company's archive.
Returns the file_url and the counts — never the bytes.

| Argument | Meaning |
| --- | --- |
| `audit_type` | FSMA, GAP, GlobalGAP, OSHA, DOL, EPA, USDA_NIFA, Other |
| `period_start` / `period_end` | A period that has not finished is refused |
| `regime` | Narrow the **training** and **open-items** sections to one scheme. Part of the idempotence key |
| `output_format` | `pdf` (default) or `docx` |
| `output_path` | ALSO write it under the site's private/files |
| `stage_via_chunks` | Checkpoint the assembly. Defaults on above 2 MB |
| `allow_open_actions` | Produce it over open findings, disclosing them at the FRONT |
| `overwrite` | Replace an existing packet for the same type and period |
| `dry_run` | Assemble it, report every count, write nothing |

**It pulls from the operational records, not from a copy.** The spray records ARE
the spray logs; the worker facility records ARE the housing register; the
traceability rows ARE the bucket log. Nothing in a packet is a compliance copy,
which is why nothing in one can have drifted from what was actually done.

**The kairotic gate is a REFUSAL, not a warning.** A packet asserts a compliant
period, and an open corrective action inside that period contradicts the
assertion — a warning at the top of a printed document is not read by the person
the document is handed to. Every open action is named in the refusal.

**Empty sections say why they are empty.** A packet on a site with no BucketLog
bridge says the bridge is not installed and the traceability has to be supplied
separately; a silently omitted section reads as an operation with nothing to
declare.

**It carries the open compliance-calendar items (v0.19.2)**, scoped to the same
regimes as the training section, and that is a disclosure rather than a
confession: the gate above has already refused the packet if any corrective
action from inside the period is open, so what is left is forward-looking work —
an operation demonstrating that it knows what it owes, from a list its own
records generated rather than somebody's memory the night before. It is the one
section NOT scoped to the period, because an expired licence is expired now
whatever quarter the packet covers. Snoozed and dismissed items are excluded:
neither is an open obligation.

Idempotent by (audit_type, company, period, regime).

### The Compliance Command Center

Not a tool — a Frappe Dashboard at `/app/compliance-command-center`, built
idempotently on every migrate. Six Number Cards, four Charts, and
`get_audit_readiness` for the one number somebody acts on.

Deliberately NOT shipped as `fixtures`, which `test_hooks.py` forbids by name: a
fixture cannot look at what is already there, so an operator who reordered their
cards would get it silently put back on every migrate.

---

## Journal Entry attribution drift

## 158. `find_drifted_je_attributions`

Read-only, on by default, **DIAGNOSTIC**. Every submitted Journal Entry in a date
range whose voucher and general ledger disagree about who a line belongs to.

v0.13.0's `update_journal_entry_party` looked its GL rows up by
`voucher_detail_no == line.name` — the Sales Invoice Item convention, not the
Journal Entry one. Every call against a submitted entry matched zero rows, wrote
the voucher, and left the ledger alone. This finds what that left behind.

**Not limited to that.** Drift also arrives from a direct database edit, a
restored backup, or a migration that moved one table and not the other, so
`by_vintage` is reported BESIDE the finding rather than used to filter it.

Lines whose GL rows cannot be identified with certainty are reported separately
as `ambiguous` and are NOT in `repair_input` — reporting a coin toss as a finding
would be worse than reporting nothing.

Three queries whatever the range, matched by the same function the repair writes
through.

## 159. `repair_drifted_je_attributions`

MUTATING (off). **Dry run defaults TRUE.** Takes `find_drifted_je_attributions`'
`repair_input` verbatim and brings each drifted ledger row back into step with its
voucher.

**Moves no balance, ever.** `party` is an attribution column: every debit, credit,
account and date is refused as an argument, so the trial balance after a repair of
two hundred lines is arithmetically identical to the one before it. That property
is what makes a batch write to submitted vouchers defensible at all.

It does not abort on the first failure — each item is a different voucher, and a
run that stopped half way would leave the ledger in a state neither report
describes. Capped at 200. Requires a `reason`, written onto every entry touched.

### `update_journal_entry_party` — what changed in v0.15.0

Its idempotence check now reads **both tables**. v0.14.0 read only the voucher: if
the line already said what was asked for it refused with "nothing to change",
which on a damaged line is precisely wrong — the voucher agreeing is the SIGNATURE
of the damage. Nothing to change now means nothing to change ANYWHERE; a voucher
that agrees over a ledger that does not is a GL-only repair and it proceeds,
reporting `gl_only_update: true`. New `force_gl_sync` writes the GL rows
regardless, which is what the batch tool passes.

---

## Wave 5 — Farm Task Dispatch (v0.16.0)

**Sprint 7 could tell an operation that fifty-four things were wrong. Nothing in
it could send anybody to fix one.** These twenty-three tools are the half that
can, and the loop they close runs:

```
Compliance Alert → Farm Task (with an evidence contract) → claim → start →
complete (photographs, signature, findings) → Housing Inspection / Detector Test
/ Water Test → the register moves → tonight's sweep auto-dismisses the alert
```

**No tool in this wave writes a Compliance Alert.** The only honest way an alert
goes away is to change the world and let the sweep notice.

## 160. `list_dispatch_board`

Read-only, on by default — the main read of the dispatch surface. Every Farm Task
grouped into its state column, worst urgency first.

| Argument | Meaning |
| --- | --- |
| `company` | Whose board |
| `state_filter` | One state, or a comma-separated list of the eight |
| `include_closed` | Include Completed, Rejected and Cancelled. Default false |
| `task_type` / `urgency` / `assigned_to` / `skill_required` | Narrow it |

Reports `in_the_pool`, `open_critical`, and **`generated_from_alerts`** — what
fraction of the board came from the compliance calendar, which is the honest
measure of whether the calendar is driving work or being read and ignored.

The same board renders in the Desk at
`/app/farm-task/view/kanban/Farm Task Dispatch`, where a foreman drags a card
between columns and Frappe writes the state field. There is no custom UI in this
release and none is needed.

## 161. `list_available_tasks`

Read-only. The pool: what a worker could pick up right now, narrowable by
`location`, `skill`, `task_type` and `urgency`. Pass `worker_id` and it also
reports their concurrent-claim count and whether they may take another.

**Only Self-pick and Either tasks appear.** Dispatched work is deliberately
absent from the pool: somebody has to be SENT to it by name, because that is how
this app marks work where the named licence holder matters.

## 162. `list_dispatched_tasks`

Read-only. What one worker is holding — Claimed and In-Progress — with the full
task behind each assignment. `include_finished=true` or an explicit `state` gives
their history.

## 163. `get_farm_task`

Read-only. One task in full: its evidence contract rendered as sentences, every
assignment it has ever had with the evidence filed against each, **every
rejection and the reason given**, the compliance record its completion produced,
and the alert it came from — including whether that alert has since
auto-dismissed, which is the loop visibly closing.

## 164. `create_farm_task`

MUTATING (off). Raises one piece of work.

| Argument | Meaning |
| --- | --- |
| `task_name` | What a foreman calls it out loud. **Required** |
| `task_type` | Inspection / Test / Spray / Repair / Harvest / Training / Compliance-Audit / Housing-Cleanup / Water-Sampling / Other. **Required** |
| `evidence_required` | JSON: `photos`, `signature`, `findings_text`, `witness`. **Required** |
| `location_doctype` + `location` | Housing Unit, Field, Irrigation Zone or Parcel, and the docname |
| `skill_required`, `urgency`, `dispatch_mode`, `estimated_duration_minutes` | The shape of the job |
| `creates_record` + `creates_record_data` | What completing it produces, and a template |
| `source_alert` | The Compliance Alert this answers |
| `assigned_to` | Dispatch it straight away |
| `draft` | Hold it out of the pool |

**`evidence_required` is mandatory and is the point of the whole doctype.** A
task that requires no evidence is a task that gets closed with a tick in a box,
and a tick in a box is what an auditor is trained to disbelieve. Refused: a
missing contract; an empty one; one whose every requirement is false; and a
misspelt key, because `{"photo": true}` asks for nothing, refuses nothing, and
looks exactly like a photograph requirement right up until the audit.

**Also refuses** a `creates_record` naming a DocType this site does not have — a
task promising a record nobody can write is a promise that fails in front of a
worker stood in a cabin — a location that does not exist, and a `source_alert`
that already has a task.

Named `FT-YYYY-MM-<seq>`, so the same annual walk can be raised every year
without colliding with its own history.

## 165. `assign_farm_task`

MUTATING (off). The foreman's half of the dual mode: sends a named person.

Refuses to take work off somebody who already holds it unless you pass
`reassign=true` **and** a `reason`, which is written onto their assignment.
"Taken off them with no explanation" is a record nobody can defend. Refuses a
task that is already Completed, Rejected or Cancelled.

## 166. `claim_farm_task`

MUTATING (off). The worker's half: takes one from the pool, and returns the
evidence they will need to close it.

**Capped at three concurrent claims per worker.** A hoarding limit and not a
productivity one: completing or rejecting one frees a slot in the same instant.
Without it, one worker empties the pool onto their own name and the board looks
worked.

Refuses a Dispatched task outright — self-picking one would put the wrong
person's name on a regulated record — a task somebody else holds, and a Draft.

## 167. `start_farm_task`

MUTATING (off). Clocks in on **this task**, not on the shift. A worker on the
clock all morning did this particular cabin between ten and half past, and that
is what an hour charged to a job has to mean. Starting twice is refused: it would
move the clock-in forward and shorten the hour actually spent.

Takes `assignment`, or `task` and the live assignment is used.

## 168. `complete_farm_task`

MUTATING (off). The tool the release exists for.

| Argument | Meaning |
| --- | --- |
| `worker_id` | **Required.** Must be the worker holding the task |
| `evidence_files` | File docnames, file URLs, or objects with a type and caption. Max 40 |
| `signature_file` | The signature capture |
| `completion_narrative` | What they did |
| `findings_text` | What was **wrong** — pass `""` to record that nothing was |
| `witness` | Somebody else who was there |
| `actual_duration_minutes`, `completed_at` | The clock |
| `record_data` | Extra fields for the compliance record, merged over the task's own template |

It checks the evidence against the contract, files it, and **writes the
compliance record the task promised** — the actual Housing Inspection, Detector
Test or Water Test, with the photographs on it. That record moves the register,
and the alert that asked for the work auto-dismisses on the next sweep.

**REFUSES a submission short of the contract**, naming each requirement that is
missing. The `findings_text` case is the subtle one: passing an **empty string**
satisfies it, because a clean inspection is a positive statement and that is how
it is made — leaving the argument out entirely records that nobody was asked.

**REFUSES a completion filed by anybody other than the worker holding the task.**
A completion by somebody who was not there is not a chain of custody, it is a
rumour, and it is the first thing an auditor pulls on.

**Lands in `Awaiting-Review`** when the record it produced found something. The
work IS done and the register IS updated; what needs a person is the finding. A
clean completion goes straight to `Completed`, because routing clean work through
a review queue is how a review queue stops being read.

## 169. `reject_farm_task`

MUTATING (off). Hands one back with a **mandatory reason** and returns it to the
pool (or cancels it with `cancel=true`).

The reason is the most useful sentence in the doctype: it turns "nobody got to it
and dispatch never followed up" — the answer nobody can defend — into "the ladder
is broken and I could not reach the detector". **The rejected assignment stays on
the record**: it is the proof somebody was sent, went, and could not do it, which
answers an auditor in a way an absence never does.

## 170. `generate_tasks_from_compliance_alerts`

MUTATING (off). **The bridge.** Walks the open Compliance Alerts and raises one
Farm Task apiece, each carrying the evidence its completion must produce.

| Argument | Meaning |
| --- | --- |
| `company` | Whose alerts |
| `dry_run` | Report without writing. **Defaults FALSE** |
| `alert_types` | Only these rules. Omit for all |
| `limit` | Most alerts to consider. Default and maximum 500 |

Each rule maps to the shape of the work it actually is:

| Rule | Becomes | Mode | Evidence |
| --- | --- | --- | --- |
| `housing_inspection_overdue` | Inspection → Housing Inspection | Self-pick | photos, signature, findings |
| `housing_detector_test_stale` | Test → Detector Test | Self-pick | photos, findings |
| `water_test_stale` | Water-Sampling → Water Test | Self-pick | photos, findings |
| `water_test_contamination` | Water-Sampling → Water Test | Dispatched | photos, findings |
| `housing_corrective_action_open` | Repair | Dispatched | photos, findings |
| `certification_expiring`, `policy_review_overdue`, `filing_response_due`, `audit_action_overdue` | Compliance-Audit | Dispatched | findings |
| `i9_expired`, `flc_license_expiring` | Compliance-Audit | Dispatched | findings (+ signature for I-9) |
| `shift_heat_threshold_crossed` | Compliance-Audit | Dispatched **to the shift's own foreman** | findings, signature |

**Since v0.22.5 a rule that is not in this table can still become work.** Where
the table has nothing to say, the recipe is read off the Compliance Rule record
itself — `producer_farm_task_type`, `producer_skill_required`,
`evidence_contract`, and `producer_assigned_to_expression`. The table is still
consulted FIRST, which is the backward-compatibility guarantee: the thirteen
rules above produce exactly the tasks they always did. The record is read only
for rules the table could not cover, because they did not exist when it was
written.

Where a rule carries `producer_assigned_to_expression`, the task is assigned to
the person it names and **carries no skill**: a skill is a pool and an assignee
is a person, and a task that is both is a task whose holder depends on which one
the dispatcher read first. An expression that names nobody, or names somebody
payroll has never heard of, puts the task back in the pool and says so in
`routing_notes` — never `Dispatched` with nobody on it, which is a task sitting
in Available that no worker is allowed to claim.

Urgency follows severity — Critical → **High**, Warning → Normal, Info → Low.
Deliberately not the identity mapping: a board where everything is Critical is a
board nobody reads.

**Idempotent by construction.** A task carries `source_alert`, so a second run
finds the task the first raised and skips the alert. Re-running after fixing half
the camp raises tasks only for the half still outstanding — two people are never
sent to walk the same cabin.

**An alert type with no recipe is reported by name** rather than turned into a
generic task: a task with a made-up evidence contract produces a compliance
record nobody can rely on.

`dry_run` defaults FALSE, unlike `dismiss_alert_bulk`. The asymmetry is
deliberate — a mis-typed filter there *hides* non-compliance and leaves an
operation reading as clean while nothing was fixed. The failure mode here is too
many idempotent tasks on a board, none of which changes an operational record.

---

## The three compliance records (v0.16.0)

Written by a task completion, or directly by somebody who walked a cabin because
they were passing. Both doors are open on purpose: a compliance record that can
only be written by finishing a dispatched task is a compliance record nobody
writes on the day the dispatch board is down, and the walk still happened.

**The workflow branches on what was found, not on who pressed what:**

```
findings blank    →  Recorded
findings present  →  Corrective Action Required
```

The state is recomputed from the text on every save, so **somebody who has typed
"water stain, north wall, spreading" is not offered the option of marking the
walk as passed.** `workflow_state` is the framework's own field name, so a site
that wants Frappe's native Workflow layered on top attaches one and
`advance_workflow` drives it.

**The write-back only ever moves a date forward.** A back-dated record is filed
as evidence and does not drag a register that already knows about something
later — that would re-raise an alert about work which has since been done.

## 171–174. `list_housing_inspections`, `get_housing_inspection`, `create_housing_inspection`, `update_housing_inspection`

The annual habitability walk. OAR 437-004-1120, 29 CFR 1910.142, FSMA Subpart L.

`create_` takes `unit`, `inspection_date`, `inspector`, `findings`,
`corrective_action`, `signature`, `photos`, `source_task` and `keep_as_draft`,
and moves the unit's `last_habitability_inspection` forward — which is the whole
mechanism by which doing the work takes `housing_inspection_overdue` off the
calendar.

A walk that **found something still moves the date**. Doing the work and finding
a problem are two different facts and both are true.

`update_` corrects or closes one. Closing a finding requires a `closure_note`
saying what was actually done: a date with nothing beside it is what an auditor
is trained to disbelieve.

## 175–178. `list_detector_tests`, `get_detector_test`, `create_detector_test`, `update_detector_test`

Smoke and CO detectors. Results are `Pass`, `Fail` or `Not Present`.

**A failed test still writes the date.** The stale-detector alert asks whether
anybody *knows* the detector works, and a Fail answers it. The answer is bad, so
the record routes to Corrective Action Required and raises a Critical alert of
its own — but the ignorance is over, and a blank date would have the calendar
saying "nobody has tested this" about a building somebody tested this morning.

**`Not Present` writes no date**, for the mirror reason: there is nothing to have
tested, so nothing is known. It is also a finding in its own right — a building
somebody sleeps in with no CO detector is the most dangerous state this app
records.

**A replacement raises a Farm Task** to go and fit one. "Replacement needed" as a
checkbox with nobody dispatched against it is a finding that survives until next
year's test rediscovers it.

## 179–182. `list_water_tests`, `get_water_test`, `create_water_test`, `update_water_test`

Agricultural water. FSMA Produce Safety Rule 21 CFR 112 Subpart E.

`test_date` is when the **sample was taken**, which is what Subpart E's ninety
days count back from — not when the laboratory answered. `lab_reported_on` sits
beside it, and the gap between them is the operation's real turnaround.

**It writes two registers.** The sample came out of an Irrigation Zone, but
`water_test_stale` reads the *block* — Subpart E is engaged by water contacting a
crop, and the crop is on the block. A test filed only against the zone would
leave the calendar calling ground untested whose water was tested last week.

**Results are read both ways**, because a laboratory says the same thing eight
ways: words first (`Absent`, `Present`, `<1`, `Not Detected`), then any number,
where anything above zero is a detection and generic E. coli is compared against
the 112.44(b) criterion of **126 CFU/100 mL**.

**AN UNREADABLE RESULT IS NOT A CLEAN RESULT.** Where neither reading works the
record routes to Corrective Action Required and somebody has to go and look at
the report. Treating an uninterpretable result as a pass is how a compliance file
becomes a clean record of nothing.

**`farm_location_gps` (v0.19.1) says where the sample was drawn.** The zone
names which water; §112.161(a)(1)(i) also asks which standpipe somebody stood
at, and a zone that feeds four hydrants does not answer that. Free text, because
a coordinate nobody could take is worth less than a place name somebody can
stand in: `"45.5152,-122.6784"` where the phone had a fix, `"North standpipe"`
where it did not. Optional and additive — samples filed before v0.19.1 have it
blank.

**Draft is the normal first state here**: a sample is taken on Monday and
answered on Thursday. Use `keep_as_draft`, then file the answer with
`update_water_test` — clearing the flag publishes the same record, recomputes the
state and moves both registers. Filing the answer as a second record would
produce two rows about one sample whose only difference is which was typed
second.

---

## Two new alert rules (v0.16.0)

The rule set is now eleven. The two new ones are a different shape from the first
nine: rules 1–9 fire on **ignorance** — nobody has walked this cabin, nobody has
tested this water — and these fire on **knowledge**, because Sprint 8 gave the
operation a way to go and look.

| Rule | Fires when | Goes quiet when |
| --- | --- | --- |
| `housing_corrective_action_open` | a Housing Inspection or Detector Test found something and nobody has closed it | a **later clean record for the same unit** supersedes it, or the corrective action is closed by hand with a note |
| `water_test_contamination` | a Water Test came back dirty — or unreadable — and is still the latest word on that zone | a **later clean sample from the same source** supersedes it, or it is closed by hand |

Both close by being superseded rather than ticked, and that is deliberate: the
work that makes the finding untrue is the work anybody would want done, so it is
the work that silences the alert. Drafts raise nothing — a draft is a note, not
evidence.

---

# v0.17.0 — multi-entity scoping, mobile auth and the public endpoint

**Sprint 8 built a dispatch board. These sixteen tools are what makes it safe to
point forty phones at it from outside the LAN.**

Three facts frame everything below, and each one is a refusal somewhere:

1. **The role says what KIND of work; the User Permission says WHOSE.** No
   company name appears in any role definition. A `User Permission` row with
   `allow=Company, apply_to_all_doctypes=1` scopes every document that links to
   a Company, for that user, across every doctype — including ones this app has
   not written yet.
2. **An empty entity list means EVERY company, not none.** That is Frappe's
   rule, and it is why `create_mobile_user` refuses to make an account without
   entities and why `list_compliance_calendar_for_me` refuses to answer for one.
3. **The credential buys identity, not entry.** A mobile request presents
   `X-MCP-Token` (entry, still CIDR-gated) *and*
   `Authorization: token <api_key>:<api_secret>` (identity). Frappe
   authenticates the second before this app's endpoint runs, and
   `security.capture_calling_user` saves who it was in the one line before the
   MCP System User is assumed.

## The six roles

Created idempotently on every `bench migrate`. `list_mobile_users` returns the
whole catalogue with each role's `cannot` list, so a client needs no second call.

| Role | Reads | Writes | Notably cannot |
| --- | --- | --- | --- |
| Field Worker | Farm Task, the three compliance records, camp and ground registers | Farm Task **Assignment** only | read a Compliance Policy or the calendar; rewrite the job |
| Foreman | compliance registers, camp, ground | Farm Task, assignments, the three records, alerts | touch accounting; edit the certificate or SOP registers |
| Compliance Officer | Farm Task, camp, ground, governance | the compliance registers, the three records, alerts | **dispatch anybody** — Farm Task is read-only, deliberately |
| Farm Manager | governance | dispatch, compliance, ground, camp, leases | see the cap table; edit the governance archive |
| Family Member | cap table, member events, notes payable, ground, leases | governance documents, related parties | see the operating company's task board |
| Advisor | governance documents, related parties, regulatory filings | nothing, anywhere | everything else |

**The Custom DocPerm trap, in one paragraph.** Frappe ignores every *standard*
DocPerm on a doctype the moment ONE Custom DocPerm exists for it — for every role
on the site. So the installer mirrors the standard permissions into custom ones
before writing the first new row (exactly as Frappe's own Role Permission Manager
does), and **refuses outright** to write a permission onto a doctype another app
owns. A Custom DocPerm on `Employee` would have taken HR Manager off the Employee
register during a migration with nothing printed. `roles.py` argues it at length;
`tests_standalone/test_roles.py` asserts both halves.

## 183. `list_mobile_users`

Read-only, on by default. The roster **and everything wrong with it**.

| Argument | Meaning |
| --- | --- |
| `role` | One of the six |
| `company` | Only accounts whose entity access includes this Company |
| `state` | `Active`, `Expired` or `Revoked` |
| `include_revoked` | Default false — revoked accounts are history, not roster |

`entity_access` is read from the **live** User Permission rows, not from the
grant, so a scoping somebody changed in the Desk shows as drift rather than
agreeing with a stale record. Each account carries a `concerns` list; every entry
is a state that looks fine on a list and is not:

- no Company User Permission at all — which in Frappe means **unrestricted**;
- the grant and the live permissions disagree;
- marked Revoked and the token still works;
- marked Revoked and the login is still enabled;
- the review date has passed and the credential is still live;
- the grant names a role the account does not hold.

## 184. `get_current_user_context`

Read-only, on by default. The mobile app's first call after enrolment: the user,
their mobile roles, the entities their User Permissions allow, which entity to
open on, the credential's review date, and plain-language `can` / `cannot` lists
for an account screen.

**The identity comes from the request.** A client that sends
`Authorization: token …` is reported as that user, with
`identity_source: "authenticated request"`. A request that authenticated as one
person and passes `user` naming another is **refused** — an account that can name
somebody else in a request body is not scoped to anything. With no per-user
credential (an operator's desktop client), `user` is accepted, because that
caller already holds the operator's bearer token.

With no identity at all it returns `identified: false` and the header to send,
rather than guessing.

## 185. `validate_public_endpoint`

Read-only, on by default. Reaches this site **from outside** over HTTPS.

| Argument | Meaning |
| --- | --- |
| `url` | Base URL to probe. Defaults to `public_url` from settings. No path, no query |
| `authenticate` | Send this site's own `X-MCP-Token`. Default false |
| `timeout_seconds` | 1–30, default 8 |

Returns a `tls` block (issuer, subject, SANs, `not_after`, `days_until_expiry`,
protocol, latency), an `http` block (status, whether a JSON-RPC body came back,
how many tools were advertised), and a `working` boolean with a `summary` and a
`next_step`.

**A 401 to the default unauthenticated probe is the best possible result.** It
proves three things at once: the path is reachable, the certificate is valid, and
the token gate is holding. That is why the probe is unauthenticated by default.

**The reachable set is a short allowlist, not an argument.** This makes an
outbound request from inside the site's network, which is the shape of every
server-side request forgery there has ever been. So: the configured `public_url`
or a host under `.ts.net`, over HTTPS, base URL only, redirects not followed —
and `authenticate=true` refuses everything except the configured `public_url`,
because a tool that will POST your bearer token to a hostname in its arguments is
a tool that exfiltrates it.

## 186. `get_tailscale_funnel_config`

Read-only, on by default. The same question from the inside: which ports are
published, at which URLs, what the node's tailnet DNS name is, and whether the
configured `public_url` matches any of it.

**It degrades instead of failing.** A containerised Frappe worker normally has
neither the `tailscale` binary nor the host's socket, and on an Umbrel that is the
**expected** state rather than a fault — Funnel forwards to the port nginx already
serves and needs no cooperation from this process. The tool distinguishes "no
Tailscale anywhere" from "a daemon socket with no client", and points at
`validate_public_endpoint`, which needs none of it.

A config it cannot parse is reported as **unparsed**, not as empty: "no funnel
ports" and "I could not tell" are different answers.

**Nothing in this app can turn Funnel on or off**, and nothing will. See the
README section for the commands, and `funnel.py` for the two reasons.

## 187. `create_mobile_user`

**MUTATING, default OFF.** One call for what four Desk forms do in ten minutes.

| Argument | Meaning |
| --- | --- |
| `email` | Required. The address the account signs in with, and its docname |
| `full_name` | Required for a new account |
| `role` | Required. One of the six |
| `entity_access` | **Required, at least one.** Company names or abbreviations |
| `preferred_company` | Which entity the app opens on. Must be in `entity_access` |
| `token_expiry_days` | Review date, in days. Default 120 |
| `generate_token` | Issue a credential now. **True for a new account, false for an update** |
| `update_existing` | Rewrite a live account's roles and scoping. Default false |
| `notes`, `url` | Recorded on the grant |

```json
{"name": "create_mobile_user",
 "arguments": {"email": "ana@constancyfarms.example",
               "full_name": "Ana Ramos",
               "role": "Field Worker",
               "entity_access": ["Constancy Farms LLC"]}}
```

Writes the User, the role (plus the site's own `Employee` role where it has one),
one `User Permission` per entity, the Mobile Access Grant, and returns
`api_key`, `api_secret` and a ready-made `auth_header`. **That is the only time
the secret appears in a result.**

Refuses: an `entity_access` that is empty or names a Company this site does not
have; a `preferred_company` outside it; a User that already exists, unless
`update_existing=true` — re-running this on a live account rewrites its roles and
scoping, which is a decision rather than a retry. To only re-issue a credential,
use `generate_api_token`.

With `update_existing=true` it also **removes** entities no longer granted. A
stale permission is the failure this release exists to prevent: an account moved
between entities that still carries the old one.

**An update leaves the credential alone by default.** A new account with no token
cannot sign in, so issuing one is the only useful default there. An existing
account has a phone in somebody's pocket, and re-scoping them should not silently
knock it offline — so `generate_token` defaults to false on an update. Pass it
explicitly either way.

## 188. `revoke_mobile_user`

**MUTATING, default OFF.** Disables the login, destroys the credential, records
why.

| Argument | Meaning |
| --- | --- |
| `email` | Required |
| `reason` | Required, at least eight characters |
| `keep_user_permissions` | Keep the Company permissions as evidence. Default true |

`reason` is the point. "Left at the end of harvest", "phone lost in the orchard"
and "dismissed for cause" are three different answers to the question an auditor
asks about why somebody's access ended, and the grant is the only place any of
them survives — Frappe keeps the access and none of the story.

**The roles are left on the account, deliberately.** A disabled user with no live
token cannot sign in; keeping the roles means the record still says what this
person *was*, and an account stripped of its roles is one nobody can later be
asked "what could they see" about.

This is *they no longer work here*. For *they lost their phone*, use
`revoke_api_token`.

## 189. `generate_api_token`

**MUTATING, default OFF.** Mints a fresh Frappe API key/secret pair.

| Argument | Meaning |
| --- | --- |
| `user` | Required |
| `expiry_days` | Days until the credential should be **reviewed**. Default 120 |

The `api_key` is reused where one exists — it is the public half and appears in
access logs, so rotating it would orphan every log line naming it. The **secret**
is always new, which is what makes this the answer to a lost phone: issuing one
stops the previous one working.

**`expiry_days` sets a review date, not an expiry, and the result says so.**
Frappe API secrets do not expire on their own, and this app installs no scheduled
job that revokes one — a job rewriting another app's User records at three in the
morning is not a thing this app does. `list_mobile_users` flags an overdue grant;
`revoke_api_token` is what actually ends it. Calling a reminder an expiry would be
a false assurance about a credential, which is worse than none.

Refuses a disabled user: a token minted for a login that cannot sign in is a token
somebody will spend an afternoon debugging.

## 190. `revoke_api_token`

**MUTATING, default OFF.** Destroys one user's credential and leaves the account
enabled.

Both halves go, not just the secret: an `api_key` left on the row reads like a
live credential to anybody scanning the User list, and the whole value of a
revocation is that somebody can tell at a glance it happened.

A call against an account with no live credential says so rather than pretending
it revoked something.

## 191. `generate_mobile_login_qr`

**MUTATING, default OFF.** The enrolment card.

| Argument | Meaning |
| --- | --- |
| `user` | Required |
| `expiry_hours` | 1–168, default 24 |
| `rotate_token` | **Default TRUE.** Mint a fresh secret for the card |
| `url` | Base URL for the card. Defaults to `public_url`. Must be `https://` |
| `archive` | Also file it privately on a Governance Document. Default false |
| `company` | Which entity to file the archived copy under |
| `error_correction` | `L`, `M`, `Q` or `H`. Default `M` |

The payload the QR carries:

```json
{"v": 1,
 "url": "https://umbrel.tail1234.ts.net",
 "endpoint": "https://umbrel.tail1234.ts.net/api/method/erpnext_mcp.mcp.handle",
 "user": "ana@constancyfarms.example",
 "token": "<api_key>:<api_secret>",
 "api_key": "...", "api_secret": "...",
 "expires_at": "2026-08-02 09:00:00"}
```

`token` is the whole pair in the form the header wants, because the app's job at
enrolment is to store a string and put it after the word `token`. `api_key` and
`api_secret` are present separately for a client that wants them apart.

**The image is a live credential.** Anybody who photographs it over somebody's
shoulder has that account until the token is revoked. That is inherent to
enrolment by QR; the mitigations are all time-shaped. `expires_at` is the deadline
for **enrolling** — once stored, the credential works until revoked. With
`rotate_token` true (the default) the previous credential has already stopped
working, so any phone already enrolled must re-scan; that is what makes re-minting
a card a real revocation of every older copy.

Refuses a non-HTTPS endpoint outright: encoding a live credential for `http://`
would put it on the wire in the clear at every call, forever. Also refuses a
disabled user, an `expiry_hours` beyond a week, and a site that does not know its
own public URL.

`archive=true` files the PNG as a **private** attachment on a Governance Document
— the offline path for a camp office at the end of a gravel road — and the result
tells you to delete that document once the phone is enrolled. The durable record
is the Mobile Access Grant, which holds no secret.

**Site prerequisite.** Needs `segno` or `qrcode`. Without either, this one tool is
not advertised and everything else in the flow still works: `generate_api_token`
returns the same credential as text.

## 192–195. The mobile-ergonomic reads

`list_my_tasks`, `list_available_for_me`, `get_task_with_evidence_contract`,
`list_compliance_calendar_for_me`. All read-only, all on by default.

Every one is a thin wrapper over something Sprint 8 already shipped. They add
exactly three things:

1. **The worker, resolved from the authenticated request** through their Employee
   record. `list_dispatched_tasks` needs an Employee docname; a phone knows an
   email and a Keychain credential. That lookup lives here, once, rather than in
   every mobile client that will ever exist.
2. **A mobile-shaped payload.** `get_task_with_evidence_contract` returns the
   contract as a checklist — each requirement with what it means in a worker's
   words, a `satisfied` flag and a capture hint (`camera`, `signature pad`,
   `text field`) — plus a `next` block naming the tool the task is waiting for, so
   a screen draws the right button without owning the rule.
3. **The entity filter** the phone would otherwise have to guess.

**A login with no Employee record is refused by name.** Returning an empty list
would read on a phone as "nothing to do today", which is a different and much
worse answer. Same for a `company` outside the worker's entities: an empty result
is indistinguishable from a quiet day, and a refusal that names the entity is a
fact somebody can act on.

**`list_available_for_me` is honest about skills.** Nothing on a Frappe site
records what skills a worker HAS — there is no register on Employee, in this app
or in Frappe HR. So an explicit `skill` filters, a site that has added its own
skills field to Employee is read, and otherwise the whole pool comes back with
`skill_matching` saying it is unfiltered. Guessing from a job title was the
alternative, and hiding a spraying task from somebody because their title said
"Harvest Crew" would be hiding work with no way to tell.

**`list_compliance_calendar_for_me` asks once per entity.** This app reads through
`frappe.db.get_all`, which does **not** consult User Permissions — so asking the
calendar once with no company would return every entity on the site. An account
with no Company User Permission is refused rather than shown everything under a
name like that one, and an entity whose calendar could not be read is named in
`failed_entities` rather than silently contributing nothing. It forwards
`regime` (v0.19.2) like every other filter, and validates it BEFORE the loop —
inside it, every exception becomes a named per-entity failure, which is right for
a broken register and exactly wrong for a mistyped argument.

## 196–198. `claim_task_via_mobile`, `start_task_via_mobile`, `complete_task_via_mobile`

**MUTATING, all default OFF.** Sprint 8's `claim_farm_task`, `start_farm_task` and
`complete_farm_task` with the worker resolved from the authenticated request
instead of named in the body.

**They add no rule and weaken none.** The three-concurrent-claim limit, the
refusal to self-pick Dispatched work, the refusal of a Draft, the evidence
contract check, the refusal of a completion filed by anybody but the worker
holding the task, and the Awaiting-Review routing when the record found something
— all of it still comes from those tools, because it **is** those tools. A wrapper
with its own copy of the compliance rules would be a second set to keep in step,
and those are exactly the set that must never drift.

`complete_task_via_mobile` takes `evidence` as the mobile spelling of
`evidence_files`; both are accepted and both mean **file references, not bytes**.
Photographs go up first through `stage_file_chunk` / `commit_staged_file`; this
call carries their docnames.

**Note the `findings_text` rule survives the wrapper.** Passing an empty string
records that nothing was wrong — a clean inspection is a positive statement —
while leaving the argument out records that nobody was asked. The wrapper passes
the argument through a presence test rather than a truthiness test, because a
falsy value dropped in transit would silently turn the first into the second.

**`farm_location_gps` (v0.19.1) records where the work was done.** FSMA
§112.161(a)(1)(i) asks an activity record for the farm's name **and** its
location, and `task_name` was only ever the first half. Free text — a coordinate
pair where the handset had a fix, a place name like `"MC-Cabin-01"` where a
metal roof meant it did not. Optional, and written only when given: an empty
value leaves any location already on the assignment alone.

Over the HTTP mobile API the same field is filled from the `latitude`/
`longitude` the app has been sending since v0.18, which until v0.19.1 reached
only the audit row. An explicit `farm_location_gps` wins over the pair, and a
pair that will not parse is dropped rather than raised on — failing a completion
that carries photographs, a signature and a compliance record over a malformed
coordinate would trade the record for its least important field.

---

# Wave 8 — the training register (v0.19.0)

Eleven compliance rules watched certificates, policies, cabins, water, filings
and audits. None watched **training** — which is what WPS asks for every twelve
months (40 CFR 170.401/.501), what Oregon's heat rule asks for annually before
the first shift at 80 °F (OAR 437-004-1131), what FSMA Subpart C asks for on
hiring and periodically (21 CFR 112.21–.30), and what a GAP auditor asks for by
name with the signature attached. All of it lived in a binder, and the way an
operation found out a handler card had lapsed was that somebody looked, or that
an inspector did.

**One record, many regimes.** A single session covering hygiene, pesticide safety
and heat satisfies GAP, WPS and OR-OSHA at once — provided the trainer covered
all three curricula. So `regimes` is a **tag list** on one record rather than
three records, and every audit packet pulls the subset that audit is entitled to
see. Matching is by **tag, never by substring**: `GlobalGAP` contains `GAP`, and a
`LIKE '%GAP%'` filter would hand a USDA auditor evidence from a different scheme.

**Retention is the longest tag.** NOP five years (7 CFR 205.103(b)(4)), OR-OSHA
three, FSMA and WPS two (21 CFR 112.164(a)(1); 40 CFR 170.309). A record tagged
GAP *and* NOP is a five-year record — destroying it at two would destroy the NOP
evidence. `get_training` returns the number with its citation.

## 199. `record_training`

**MUTATING, default OFF.** One training event, tagged with every audit it
answers.

| Argument | Notes |
| --- | --- |
| `employee` | **Required.** Docname, employee number, name, or the linked login. |
| `training_type` | **Required.** 'PSA Grower Training', 'WPS Handler Training', 'Heat Illness Prevention'. |
| `completed_date` | **Required.** YYYY-MM-DD. A future date is refused — §112.161(a)(2). |
| `regimes` | **Required.** One or more of FSMA, GAP, GlobalGAP, PrimusGFS, NOP, WPS, OR-OSHA, Other. |
| `content_topics_covered` | **Required.** What was actually covered. |
| `expires_date` | **Empty means one-time** and the calendar never asks for a renewal. |
| `provider`, `training_source`, `completed_time`, `certificate_file`, `person_performed_signature`, `company`, `notes` | Optional. |

```bash
-d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
      "name":"record_training","arguments":{
        "employee":"HR-EMP-00002",
        "training_type":"WPS Handler Training",
        "completed_date":"2026-07-21","completed_time":"08:15",
        "regimes":["WPS","GAP"],
        "content_topics_covered":"Label reading, PPE, REI, decontamination",
        "expires_date":"2027-07-21",
        "person_performed_signature":"/files/ben-signature.png"}}}'
```

**`content_topics_covered` is required and that is the point.** Oregon's heat rule
names six topics that must be covered annually; a record claiming OR-OSHA without
them is a record an inspector will disallow. "Heat, water, shade, symptoms,
reporting, emergency response" is a curriculum; "safety meeting" is not.

**A near-miss tag is refused, not corrected.** `"OSHA"` where the vocabulary says
`"OR-OSHA"` would file the evidence where no packet looks for it, and nobody finds
that out until an inspector does. Regulator spellings are accepted and
canonicalised (`oregon osha`, `40 CFR 170`, `7 CFR 205`).

**A renewal ADDS a record and never edits one.** Last year's card is the evidence
about last year. The result names every earlier record it supersedes, so nobody
deletes them to tidy up.

## 200. `list_trainings`

**Read-only.** The register, filtered the four ways an audit or a calendar asks:
`regime`, `status`, `expiring_within_days`, `unreviewed_only` — plus `employee`,
`company`, `from_date`/`to_date`.

`status` and `expiring_within_days` are computed **as of today** from the expiry
date rather than read off the stored column: a record last saved in March holds
March's answer, and filtering on it would report the lapsed set as current.

Returns `by_regime` counts, `expired`, `expiring`, `without_supervisor_review`
(the FSMA §112.161(b) gap) and `without_trainee_signature` (§112.161(a)(4)).

## 201. `get_training`

**Read-only.** One record in full, with that person's whole training history, the
retention period the tags demand with its citation, the §112.161 elements the
record **lacks** in the rule's own terms, and `superseded_by`.

The gaps are listed rather than fixed: a signature added now would be a signature
dated now, and a record assembled before an inspection is what an inspector is
trained to spot.

## 202. `sign_training_supervisor_review`

**MUTATING, default OFF.** Records the FSMA §112.161(b) supervisor review.

**This is the gap a GAP-only operation has.** §112.161(b) requires worker training
records to be reviewed, dated and signed by a supervisor or responsible party
within a reasonable time after the record is made. USDA GAP does not ask for it,
so an operation with an immaculate GAP binder fails on it — and FDA writes it up
even where the underlying training was fine.

**A separate call from `record_training`, deliberately.** The rule's phrase is
"after the record is made" — a sequence, not a form field. A tool that took both
signatures at once would make simultaneous timestamps the default, and
simultaneous timestamps are the shape of a record an inspector reads as assembled
rather than kept. The result reports the lag and says so when it is long.

Refuses a self-review, a supervisor from another entity, a review dated before the
training, and — without `replace_reviewer=true` — overwriting a signature already
on the record.

## The twelfth alert rule and the packet section

`training_expiring` fires on the record's own `expires_date`: **Warning** at 90
days (what arranging a retraining actually takes — trainer, crew, language, room),
**Critical** at 30 (the next scheduled course may already be after the lapse) and
**Critical** once lapsed. Training with **no** expiry raises nothing at all,
because a renewal alert nobody can clear is how a calendar stops being read. The
message carries the regimes and what actually stops being lawful — a handler whose
WPS training lapsed cannot legally perform an application.

`generate_audit_packet` gained a **worker training** section scoped to each audit
type's own regimes (GAP → GAP + WPS; OSHA → OR-OSHA + WPS; EPA → WPS; FSMA → FSMA
+ WPS), plus a `regime` argument that narrows it further and is **part of the
idempotence key** so a narrowed packet never silently overwrites a full one.
`generate_compliance_packet` gained `regime`, which staples a training annex to an
accounting packet over that packet's own period.

# Crew shift and heat exposure tools

*v0.19.3. Ten tools that are one workflow with one actor.*

**Compliance anchors to a shift, not to a task.** A task completion carries a
point-in-time reading; a shift carries a timeline. Oregon OSHA does not ask what
the temperature was when one job closed — it asks whether the July 15 shift
complied with OAR 437-004-1131 from start to finish, and only a record spanning
the exposure period can answer.

**The foreman is the sole actor and there is no clock-in tool.** -1131 puts the
water, shade, rest-cycle and observation obligations on a **named** responsible
person, and FSMA §112.161(b) asks that person to sign. A crew of thirty each
clocking themselves in is a shift with thirty people responsible for the record,
which is a shift with nobody responsible for it — and the observable failure is
that nobody logged the water break because everybody assumed somebody else had.

**Per-worker attendance is not lost to this.** Every crew row carries its own
`joined_at` and `left_at`; `remove_worker_from_shift` sets the second rather than
deleting the row; and `end_shift` writes one submitted `Attendance` per person for
**their own** span. The bridge runs one way only — a shift is formed by a foreman
naming a crew, a location and a type, and an attendance row carries none of those,
so deriving shifts from attendance would invent all three on a record an inspector
reads.

## 203. `start_shift`

**MUTATING, default OFF.** Form a crew at a place and start the exposure period.

| Argument | Notes |
| --- | --- |
| `foreman` | **Required.** Docname, employee number, name, or the linked login. |
| `location` | Block, camp or facility. Free text — a shift can be at a place this site has no master for. |
| `shift_type` | Spray / Harvest / Prune / Irrigation / Housing Work / Detector Test Round / Maintenance / General. |
| `farm_location_gps` | `"45.52,-122.68"`. **The weather anchor** — v0.19.4 fetches conditions here every 15 minutes while the shift is open. |
| `crew_employees` | Who is on it at the start. Max 60. Their `joined_at` defaults to the **shift's** start, not to now. |
| `start_datetime`, `company` | Optional. |

```bash
-d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
      "name":"start_shift","arguments":{
        "foreman":"HR-EMP-00001",
        "location":"Block 7 North","shift_type":"Harvest",
        "farm_location_gps":"45.52,-122.68",
        "crew_employees":["HR-EMP-00002","HR-EMP-00004"]}}}'
```

**`joined_at` defaults differ between this and `add_worker_to_shift`, on purpose.**
Everybody rostered at the beginning was there at the beginning, so stamping them
with the moment the API call landed would shave minutes off every one of their
days. A worker added mid-shift arrived when somebody said so, so that one defaults
to now.

## 204. `add_worker_to_shift`

**MUTATING, default OFF.** A late arrival, or a transfer off another block.
`joined_at` defaults to now. Refuses a second row for somebody already on the crew
— two rows look deliberate on the form and become two Attendance days for one
person when the shift closes. Refuses a closed shift: the payroll rows are already
written.

## 205. `remove_worker_from_shift`

**MUTATING, default OFF.** **It sets `left_at`; it does not delete the row.** The
row is the only record that this person was on this shift at all — which is what a
wage claim turns on, and what says who was exposed on a hot afternoon before they
were sent home. Calling it twice without an explicit `left_at` is refused, because
a silent second call would move a departure that has already happened to now.

## 206. `log_shift_event`

**MUTATING, default OFF.** One thing the foreman did about the conditions, at the
moment it happened.

| Argument | Notes |
| --- | --- |
| `shift` | **Required.** SHIFT-2026-0001. |
| `event_type` | **Required.** Water Break / Shade Break / Rest Cycle / Supervisor Observation / Heat Illness Signs Check / Cool-Down / Threshold Crossed / Acclimatization Reminder / Other. |
| `event_datetime` | Defaults to now — the right answer when the call is made as it happens. |
| `logged_by` | Defaults to the foreman. Worth setting when a lead worker called it. |
| `description`, `producer_record_doctype`, `producer_record_name`, `evidence_file_token` | Optional. |
| `weather_snapshot_temp_f`, `weather_snapshot_heat_index_f` | Denormalised for audit convenience. v0.19.4 fills them. |

**The timeline is the evidence.** Oregon's heat rule does not ask whether water
was available in principle; it asks what happened during the shift, and four
water breaks with timestamps answer that in a way an annual policy document never
can. `create_heat_exposure_event` is the claim; this is what the claim rests on,
and an inspector asks for the second.

An event timestamped outside the shift is **kept and reported** rather than
refused: a clock five minutes out is not a false record, and refusing would mean
the break goes unlogged rather than logged approximately.

## 207. `end_shift`

**MUTATING, default OFF.** Close the shift with a signature, and write the crew's
payroll rows.

| Argument | Notes |
| --- | --- |
| `shift` | **Required.** |
| `supervisor_signature_file_token` | **Required.** File docname or file_url. One pointing at nothing is refused. |
| `end_datetime` | Defaults to now. Before the start, or before a crew member's recorded departure, is refused. |
| `foreman_notes`, `reviewed_on` | Optional. |

**The signature is required and it is why this is a tool.** An unsigned close is
an UPDATE setting a timestamp; the signature is what makes it the attestation
§112.161(b) asks for — a review that is dated **and signed**. Without one the
shift stays open and nothing is written.

**One Attendance per crew member, for that person's own span.** A worker who
arrived an hour late and left two hours early worked six hours of a nine-hour
shift, and a row claiming nine is wrong in the employer's favour — which is the
direction that gets litigated. Rows are **submitted**, because
`get_attendance_summary` counts `docstatus 1` only.

**The bridge never blocks the close.** A site without Frappe HR, an employee
archived since the shift ran, a day somebody already keyed in by hand — every one
is reported in `attendance` and none stops a signed shift closing. The signature
is the compliance act; the payroll row is the convenience.

## 208. `create_heat_exposure_event`

**MUTATING, default OFF.** The OAR 437-004-1131 record for one shift, signed and
submitted.

| Argument | Notes |
| --- | --- |
| `farm_shift` | **Required and unique.** One shift has at most one heat record. |
| `supervisor_signature_file_token` | **Required.** Submitting is the attestation. |
| `water_provided`, `shade_provided`, `mandatory_rest_taken` | What the rule asks. Rest **taken**, not offered. |
| `heat_illness_signs_observed`, `worker_reported_symptoms`, `emergency_response_activated` | Signs observed with no response and no notes is refused. |
| `training_verified` | Checked against the training register **as of the day of the shift**. |
| `max_temp_f`, `max_heat_index_f`, `threshold_crossed_at` | Manual until v0.19.4 computes them from the weather timeline. |
| `acclimatization_plan` | Employees with under 14 days in the heat, per -1131(g). Somebody not on the crew is refused. |
| `event_date`, `notes`, `regulation_citation` | Optional. |

```bash
-d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{
      "name":"create_heat_exposure_event","arguments":{
        "farm_shift":"SHIFT-2026-0001",
        "max_temp_f":96,"max_heat_index_f":101,
        "water_provided":true,"shade_provided":true,"mandatory_rest_taken":true,
        "heat_illness_signs_observed":false,"worker_reported_symptoms":false,
        "emergency_response_activated":false,"training_verified":true,
        "supervisor_signature_file_token":"FILE-00042"}}}'
```

**A verified-training claim the register contradicts is refused.** The same audit
packet carries both this record and the training register, and a packet that
contradicts itself is worse than a packet with a gap. Claiming `false` is accepted
and the missing names are reported: a shift that ran with an untrained worker
happened, and the record of it is what the operation needs to have.

**Signs seen and nothing done is the sequence that kills people.** There are
legitimate versions — the worker recovered in shade within minutes and declined
further help — and every one of them is a sentence somebody can write. What is
refused is the silence.

**Everything else is recorded with the gap stated.** A day where the shade trailer
broke down and the crew went home at eleven is a real shift with a real gap, and a
tool that would not let it be recorded would produce either a false record or no
record.

## 209. `list_shifts`

**Read-only.** `company`, `foreman`, `employee` (walks the crew tables),
`status`, `shift_type`, `from_date`/`to_date`, `limit`.

`status` is **computed** from whether the shift has an end time rather than read
off a stored column — an open shift is what the v0.19.4 weather sweep walks, and a
record last saved in March holding March's answer would drop a live shift out of
the fetch.

`closed_without_a_signature` is the one to read. `end_shift` cannot produce one,
so anything on that list was closed in the Desk or by an import.

## 210. `get_shift`

**Read-only.** The crew with each person's own span, the compliance-event
timeline, the weather timeline, and the heat record if one exists. **This is the
evidence chain an inspector is handed.**

Each crew row reports `present_until`, the honest reading of an empty `left_at`:
they were there to the end. Computed rather than written back, because writing it
would destroy the distinction between "left at 13:00" and "stayed to the end" the
moment the end time changed.

From v0.32.0 it also reports `location_log_count` — **a count and not the
track**. A shift with a fix every two minutes carries hundreds of points, and
returning them on every read of every shift would make each one pay for a map
nobody asked to see. **210b** is the tool that draws it.

## 210a. `log_shift_location`

**MUTATING**, default OFF (`allow_log_shift_location`). v0.32.0. What the iOS app
posts periodically while a shift is running.

| Argument | Notes |
| --- | --- |
| `shift` | **Required.** An open shift is *not* required — see below. |
| `latitude`, `longitude` | **Required.** `lat` / `lon` accepted for them. |
| `timestamp` | When the fix was **taken**, not when it arrived. Defaults to now, which is right for a phone posting live and wrong for one catching up. |
| `accuracy_meters` | Kept, never gated on. Past 50 m it is noted. |
| `employee` | Whose device. Optional. |
| `source` | `iOS` (default) or `Manual`. |
| `notes` | Empty for everything the phone posted. |

**It appends and never edits.** A breadcrumb somebody can correct is not a record
of where the phone was, it is a record of where somebody would like it to have
been, and those two documents are indistinguishable afterwards.

**This is the one tool on the shift surface a worker's phone drives rather than
the foreman**, and it does not contradict the sole-actor rule the rest of that
surface keeps. That rule is about who is *answerable* — who forms the crew, calls
the water break, signs the close — and none of it moves here. A breadcrumb
attests to nothing; it records where a device was, which is a measurement rather
than a claim.

**An open shift is not required.** A phone that could not reach the site until the
evening is posting about a shift the foreman has already closed, and refusing
those would throw away the evidence that is hardest to collect. A fix outside the
shift's own span is reported instead — a device still reporting after the crew
went home traces the drive to the shop.

| Refusal | Why |
| --- | --- |
| Coordinates off Earth | a latitude past 90 is the pair the wrong way round, and it is the only version of that mistake a computer can catch |
| A missing latitude or longitude | a breadcrumb with no position is a timestamp |
| An employee from another company | a fix filed against another entity's crew is evidence in the wrong packet |

## 210b. `get_shift_track`

**Read-only**, default ON (`allow_get_shift_track`). v0.32.0.

**Arguments:** `shift` (required), `employee`, `limit` (default and hard maximum
5000).

**Returns** `track` (each point with `lat`, `lon`, `timestamp`,
`accuracy_meters`, `h3_cell`, `employee`, `source`), `count`, `first_fix`,
`last_fix`, `gaps`, `employees_tracked`, `truncated` and the shift's own span.

**In the order the fixes were taken, not the order they arrived.** A phone out of
signal in a canyon posts an hour of breadcrumbs the moment the bars come back, so
a track sorted by insertion draws the crew standing still all morning where the
signal returned and then teleporting across the farm.

**`gaps` is the part a reader misjudges.** Every silence longer than ten minutes
is named with its length, because a straight line drawn between the two ends of
one is a line the crew did not walk. Nothing is interpolated: an invented position
on a record read in a wage dispute or a re-entry-interval question is the worst
thing this app could put on a map.

Empty is the ordinary answer for a shift worked before the phones were logging,
and it is **not** a gap in the compliance record — the shift's own location, crew
spans and event timeline are unaffected.

## 211. `list_heat_exposure_events`

**Read-only.** `company`, `from_date`/`to_date`, `with_gaps_only`, `limit`.
Returns `with_signs_observed` (what an investigation reads first — and a shift on
that list is one where the observation obligation was **working**),
`without_verified_training` (a citation on the first hot morning) and
`without_a_signature` (which should be empty).

## 212. `get_heat_exposure_event`

**Read-only.** One record in full with the shift behind it, and the obligations it
does **not** claim were met, in the rule's own terms. Where the shift's event
timeline is empty it says so — the checkboxes here are the **assertion** and the
shift's logged water breaks are the **evidence** for it.

## The thirteenth alert rule, and the Attendance bridge

`supervisor_review_lapsed` watches a signature that was never put on a record —
the work was done, the record was written, and the second pair of eyes
§112.161(b) asks for never arrived. **Warning** at 14 days (one clear miss of the
weekly review cadence "reasonable time" is read against for records generated
daily), **Critical** past 30 (no reading of "reasonable" covers it, and a batch
signed the week before an inspection is the finding rather than the fix). It
auto-dismisses the moment somebody signs.

The clock runs from when the record was **made**, not from the activity date: a
training delivered in March and recorded yesterday has a one-day-old record, and
reading the activity date would raise a Critical on every season somebody
backfilled. It walks a **table** of doctypes carrying the §112.161(b) columns —
one row in v0.19.3, and Housing Inspection, Water Test, Heat Exposure Event and
Farm Task Assignment are each a one-line addition the day they grow them.

`Attendance` gains a **`farm_shift`** Custom Field, installed alongside the
v0.15.0 compliance fields and reported by `before_uninstall`. Without it a
shift-formed day is indistinguishable from a hand-keyed one, so nobody reading the
register can reach the conditions the person worked in — and the bridge, unable to
tell its own rows from anybody else's, would pay somebody twice for one afternoon.

## v0.19.2 — regimes as records, curricula as records

`Compliance Alert` gained a **`regime`** Table MultiSelect over a new
`Compliance Regime` master, written by the sweep rather than typed: ten rules
carry a constant (an overdue cabin inspection is an OR-OSHA item whoever is
asking) and the two that fire on many kinds of thing — certificates and training
— tag each alert from the RECORD, because an applicator licence is WPS evidence
and a GlobalGAP certificate is not. `get_compliance_calendar`,
`list_compliance_calendar_for_me` and `refresh_compliance_alerts` all take
`regime`.

The vocabulary did not move. `erpnext_mcp/training.py` still holds it, the master
is seeded FROM it on every migrate, and `Employee Training Record.regimes` is
still a delimited tag list — a child table and a comma-separated column that
disagreed about whether a row carries WPS is the failure that module exists to
prevent. Two tokens were added: **`OTCO`** (Oregon Tilth, the certifier that
holds the organic file, as against NOP the rule) and **`Internal`** (the
operation's own standard — real work, real due date, nobody coming to inspect).

`Employee Training Record.training_type` became a **Link to `Training Type`**,
migrated from the free text already in the column: the master names itself from
that text, so the ordinary record is not rewritten at all. It is still not a
Select — `record_training` creates a curriculum from free text the first time
somebody files a course this site has not run, and says so in the result. The
new curriculum takes the regimes its NAME implies rather than the session's,
because the record says what one afternoon covered and the curriculum says what
the course normally answers.

---

# The weather timeline (v0.19.4)

v0.19.3 shipped `Farm Shift Weather Reading` with nothing writing to it, so that
fixing the shape first meant v0.19.4 could wire a fetch instead of migrating a
schema under live compliance records. This is the fetch.

**The mechanism is mostly a schedule.** `services/weather.sweep_open_shifts` runs
on a `*/15 * * * *` cron, walks every Farm Shift with no `end_datetime` and a
`farm_location_gps`, asks Open-Meteo what the conditions are there, and appends a
reading. Fifteen minutes rather than hourly because OAR 437-004-1131 asks what the
conditions were across an exposure period, and nine readings on a nine-hour shift
is a sketch where thirty-six is a timeline.

**The heat index is computed, not read off the API.** Open-Meteo returns
`apparent_temperature`, which folds in wind and radiation and is a wind-chill
figure in winter. The NWS heat index is temperature and humidity, and it is what
the rule turns on: 88 °F at 70 % humidity is a **100 °F heat index**, and a shift
documented against apparent temperature would look compliant while somebody was
being cooked. Both inputs are stored beside the result so a disputed index can be
recomputed from the observation.

**A crossing logs an event. It never files a heat record.** A reading at or above
the threshold writes one `Threshold Crossed` compliance event — once per shift,
not once per reading, or a hot afternoon buries the water breaks under thirty-six
identical rows. It does **not** create a Heat Exposure Event, and that is the line
this release draws: that record says which crew was exposed, what water was
provided, whether the rest cycle was taken and whether anybody showed signs, and
it carries a signature. Those are five judgements by the person who was standing
there. The sweep surfaces the condition; the foreman decides whether it is a
record.

**Three things now fill themselves in.** A compliance event's
`weather_snapshot_temp_f` / `weather_snapshot_heat_index_f` come from the reading
current at its own instant (the last one at or before it, within half an hour —
earlier beats later, because that is the conditions the foreman was standing in).
A Heat Exposure Event's `max_temp_f`, `max_heat_index_f` and `threshold_crossed_at`
compute off the shift's timeline. **Manual entry always wins** in both cases: an
on-site reading beats a modelled figure for a grid square measured in kilometres,
and the computed value fills a blank rather than correcting an answer.

**Open-Meteo needs no API key, which is a reason to be more careful with it.** The
service caches by coordinate rounded to four decimals, skips any shift read within
`fetch_interval_minutes`, and treats a 429 or a 5xx as an instruction — exponential
backoff per coordinate, doubling, capped at an hour, during which no request goes
out for that place at all. Nothing raises: a failed fetch is a missing reading, and
a shift with a gap in its timeline is an infinitely better outcome than a scheduler
that stopped.

## 213. `fetch_weather_now`

**MUTATING (default OFF).** `shift`. Appends one reading to an **open** shift
immediately, bypassing the cache — for the foreman who wants the conditions on the
record now rather than in eleven minutes. Logs a `Threshold Crossed` event where
the reading crosses. Refuses a closed shift (a `current` reading filed against a
crew who went home is true about the place and false about the shift) and one with
no coordinates.

## 214. `backfill_weather_for_shift`

**MUTATING (default OFF).** `shift`. Reconstructs a **closed** shift's timeline
from the archive API, at that API's own **hourly** granularity, filtered to the
shift's own period so a six-hour morning does not acquire a timeline running to
midnight. Idempotent: every reading is matched against the minute already present,
and a reading is never edited — so running it over a shift that was also swept live
keeps the live readings and fills the gaps. Returns `added`, `skipped_as_duplicate`
and `failed`.

It writes **no** compliance events, and that is the one judgement call in the tool:
a `Threshold Crossed` row dated last July on a closed and signed shift would be an
observation nobody made, sitting beside water breaks somebody did. The crossings
are counted and reported instead, which is also the sentence that tells a foreman
whether the shift needed a heat record at all.

## 215. `list_shifts_missing_weather`

**Read-only.** `company`, `from_date`/`to_date`, `limit`. Closed shifts carrying
fewer than **one reading per hour** of their own length — the archive's granularity,
so a fully backfilled shift never appears and a live-swept one clears it four times
over. Shifts with **no `farm_location_gps`** are reported separately: no amount of
backfilling documents one, and the fix is a different action.

## 216. `get_weather_timeline`

**Read-only.** `shift`, optional `from_datetime`/`to_datetime`. The readings, the
extremes, the count at or above threshold and `first_crossing` — the instant
-1131's obligations start running from. Reports the `sources` present, and calls
out a **mixed** timeline by name: live fifteen-minute readings and an hourly
archive reconstruction are both true and are not equally strong.

## 217. `get_weather_settings`

**Read-only.** No arguments. The kill switch, the cadence, the three thresholds and
the per-company overrides — because "the threshold is 80" is false on a site where
one entity set 75. It works even when weather is switched **off**, because it is
the tool somebody calls to find out why nothing is being fetched.

**There is no `update_weather_settings`, and there will not be one.** Those are
three outbound URLs and three numbers that decide whether a hot afternoon is logged
at all. A tool that could write them would be one sentence away from pointing this
site's weather somewhere else, or from raising the heat threshold past anything
Oregon produces — leaving a site that behaves normally and never says anything is
wrong. The Desk form is the write surface, where a person types the number and
Frappe's version trail records who did.

## v0.19.5 — what the year actually earned per acre

**The first release in this run that no regulator asked for.** Everything from
v0.19.0 onward answered somebody with a citation; this answers a lender, a buyer
and the person deciding whether a good year was earned or borrowed.

$$\text{Sustainable CF/Acre} = \frac{\text{Normalized OCF} - \text{Maintenance Capex}}{\text{Productive Acres}}$$

**Headline operating cash flow lies in two directions at once.** It is *flattered*
by money that came in and will not come in again — an insurance recovery, a
settlement, a gain on a tractor sale that landed in the operating section — and it
is flattered *again* by maintenance that was not done. A farm running its
irrigation to failure to make a year look good is destroying the thing the year
was earned with, and the headline number goes **up** while it happens.

**The output is itemized and that is not a convenience.** A single number here is
worthless, because the number's whole claim is that it has been adjusted, and an
adjusted number nobody can inspect is indistinguishable from an arranged one.
`get_sustainable_cf_per_acre` returns every approved adjustment with its
justification and the name behind it, every maintenance-capex asset with its
purchase date and portion, and every productive block with the days it was in
service. The figure is the last key in the payload rather than the only one.

**AI proposes, human approves**, and the two are separate tools with separate
switches. Finding a non-recurring item scattered through a ledger nobody reads
line by line is worth a great deal and is something a model is good at; deciding
that a hailstorm in a region that hails every third year is non-recurring is a
judgement somebody defends across a table.

**Maintenance capex is actual spend, never a percentage of revenue.** The common
shortcut destroys the only interesting signal: an operation that spent nothing on
replacement did not have a cheap year, it borrowed the year from the orchard, and
a formula substituting 3 % of revenue reports a well-maintained farm every time —
including in the years it matters. Assets with no `capex_type` are **excluded and
counted**, in neither direction.

**The denominator is what is productive, not what is owned.** Fallow ground and
pre-yield plantings are out and counted separately, and a block that came into
bearing in February is weighted for the part of the period it was actually
earning — inclusive days at both ends, because `period_end` is an inclusive date
everywhere else in this app.

**Raw OCF is computed from GL Entry by the direct method**, not read off
ERPNext's Cash Flow report. Cash and bank movement per submitted voucher,
apportioned to operating / investing / financing by the accounts on the other
side, with a mixed voucher split proportionally rather than assigned to whichever
line is biggest. A report's output cannot be traced back to rows, and the whole
argument of this KPI is that it has to be.

## 218. `create_normalization_adjustment`

**MUTATING (default OFF).** `company`, `fiscal_year`, `period_start`,
`period_end`, `amount`, `direction`, `category`, `justification`, optional
`supporting_document_file_token`. **Creates a `Draft`, always** — a draft does not
count towards the KPI and nothing in this tool can make it count.

`amount` is **always positive**; the sign lives in `direction`. A negative amount
beside a `Subtract from OCF` is a double negative, and a double negative is how an
adjustment ends up moving the number the wrong way in a pack somebody is borrowing
against.

`justification` has a **forty-character floor**. Not a quality bar — no character
count is one — but a floor under "one-time" and "per Tim", which are what gets
written when the field is merely required and which an auditor reads as an
admission that nobody thought about it.

## 219. `approve_normalization_adjustment`

**MUTATING (default OFF).** `name`, `approver_signature_file_token`, optional
`approver_employee`. Status to `Approved`, signature attached, `approved_on`
**written rather than taken as input** — an approval date somebody can set is one
they can set to before the quarter closed.

**There is no unsigned path through this tool.** The whole argument for the record
is that a normalization is a judgement with somebody's name against it. Refused
where another approved adjustment already covers the same company, period and
category: two approved adjustments are two answers to one question, and the one a
reader finds will be whichever sorted first. A correction **supersedes**.

`approver_employee` defaults to the Employee linked to the acting user, and is
empty where the app runs as a service principal — which is the ordinary
configuration and is not a failure. The signature is the identity that matters.

## 220. `reject_normalization_adjustment`

**MUTATING (default OFF).** `name`, `rejection_reason`. The rejection is **kept**
rather than deleted, for the same reason a rejected insurance claim is kept: a
refusal with a reason teaches the next proposal, and a register with only its
successes in it says nothing about how hard the successes were to get.

An already-approved adjustment **cannot** be rejected. It has been counted, and
rewriting a decision is not the same as recording one — supersede it instead.

## 221. `backfill_asset_capex_type`

**MUTATING (default OFF).** `default_capex_type` (`Maintenance` by default),
optional `cutoff_purchase_date`, `company`, `dry_run` (**default TRUE**).
Classifies Assets that have **no** `capex_type`, never one somebody made, so a
second run finds nothing to do.

The heuristic is one sentence: *everything bought before the operation started
tracking is generally maintenance, because it is the existing productive plant
carrying on.* `Mixed` is refused as a bulk default — a split is a judgement about
one invoice, and applying one to a hundred assets would be inventing a hundred
splits.

**A starting position, not an answer**, and the result says so. The new block
planted in year six was growth and will read as maintenance until somebody fixes
it on the Asset, which *understates* the KPI; a register of nulls *overstates* it,
because unclassified purchases are excluded entirely.

## 222. `list_normalization_adjustments`

**Read-only.** `company`, `fiscal_year`, `status`, `limit`. The register, scoped
to the companies the caller may see. `counted_in_the_kpi` is the list that
matters — only `Approved` rows move the number. `awaiting_a_decision` is the other
one worth reading at quarter end: a proposal nobody has decided is not a neutral
state, it is a figure that will change after the pack goes out.

## 223. `get_sustainable_cf_per_acre`

**Read-only.** `company`, optional `as_of`, `window_type`, `window_months`,
`computation_step`, `historical_lookback_years`, `include_historical_averages` —
and the deprecated `period_start` / `period_end` pair.

**Since v0.19.6 it defaults to a trailing twelve months.** Call it with only a
company and you get the TTM window ending at the last completed month, the month
just finished beside it, and five years of prior TTM values with their mean,
median, spread and the two deltas. The window's `components` carry everything the
single-period payload carried — the summed adjustments with their justifications,
the aggregated maintenance capex per asset, the time-weighted acres per block.
See `docs/reporting_ttm_standard.md` for the shape and the boundary rule.

**Passing `period_start` and `period_end` returns the v0.19.5 payload, exactly**,
with a deprecation sentence at the head of `computation_warnings`: `raw_ocf` with
its sourcing note and the investing and financing sections, the itemized
adjustments, `normalized_ocf`, `maintenance_capex` with the unclassified count
and amount called out, `productive_acres` per block with days in service, the
figure. That path is kept because this number is quoted in packs that were sent
before the window existed, and a release that changed what an unchanged call
returned would silently alter a figure somebody had already given a bank. One of
the two without the other is refused rather than guessed at.

`sustainable_cf_per_acre` is **null, not zero**, where there are no productive
acres: a division nobody performed is not an answer, and a zero would be read as
one. **Read `computation_warnings` before quoting the figure** — undated blocks,
unclassified assets and a period with no approved adjustments at all are each a
sentence there rather than a silence.

## v0.19.6 — the window standard

**Every financial report now defaults to a trailing twelve months.** Not a
feature on one metric: the shape every financial figure in this app takes from
here on. `docs/reporting_ttm_standard.md` is the full argument; the short version
is that agricultural revenue is aggressively seasonal, so Q3 is harvest and Q1 is
pruning, and two single periods set against each other say the operation
collapsed in January and recovered in September — every year, on every farm,
whether or not anything happened.

**Three blocks, and each is the correction for the other two.** `point_in_time`
is the period just finished. `window` (also `ttm` when the type is TTM) is the
same figure over twelve rolling months, so the whole annual cycle is inside it
exactly once however it is read. `historical_averages` is what that window has
been worth for this operation before — which is the only thing that says whether
the current number is good. A TTM figure means one thing above its five-year mean
and the opposite below it, and the first two blocks cannot say which.

**The window ends at the last completed step, never a part-finished one.** Read
on 2026-08-03 with a Monthly step, the window is 2025-08-01 to 2026-07-31. Three
days of August against twelve months of everything else is a figure that falls
every first of the month and recovers by the thirty-first, and an operator
reading it on the fourth will believe the fall.

**Quarterly and Yearly steps follow the company's own fiscal year**, and the
payload reports `fiscal_year_start_month` rather than leaving it to be inferred.
A July-year operation stepping its history by calendar quarters would put every
year-end close in the middle of a bucket.

**Partial history is said out loud and never annualized.** A site with four
months of ledger gets four months of ledger, labelled, because annualizing it
would invent eight months of a season that has not happened.

**The history is cached in `Financial KPI History`**, filled by an overnight
sweep at 02:00. A five-year Monthly history is sixty full computations over
twelve months of GL each; a live query reads the cache and computes at most
twenty-four missing snapshots before stopping and naming the tool that fills the
rest. Approving a normalization adjustment for a period the cache already covers
**deletes** the snapshots whose window contained it — a stale components list is
worse than a missing one, because it is a set of ingredients that does not
produce the number printed above it.

## 224. `get_windowed_report`

**Read-only.** `report_name` (required), `company`, optional `as_of`,
`window_type`, `window_months`, `computation_step`,
`historical_lookback_years`, `include_historical_averages`.

`report_name` selects among the registered computers — `sustainable_cf_per_acre`,
`ocf` (raw and normalized operating cash flow, for a covenant test that needs the
figure without an acreage denominator attached) and `revenue`. The payload lists
what this site has under `available_reports`.

**This is the generic entry point and it is why the standard generalizes.** A
report registered in `erpnext_mcp/services/financial_reports.py` is reachable
through it without another tool, another switch and another section here — a
framework whose every KPI costs a tool is a framework with six KPIs in it.

**It warms a cache, and that is the one thing it writes.** Snapshots it had to
compute are saved to `Financial KPI History` so the next caller does not
recompute them. Nothing in your ledger is touched: no Account, no GL Entry, no
Journal Entry, no Asset, no Field, no adjustment. Deleting every cached row
changes no answer this tool gives — only how long it takes to give it.

## 225. `list_financial_kpi_history`

**Read-only.** Optional `kpi_key`, `company`, `computation_step`, `window_type`,
`from_date`, `to_date`, `limit`. The precomputed cache as a plain series — use it
to draw a line or export one, where `get_windowed_report` would send sixty copies
of the components dict to deliver sixty numbers.

**A gap here is not a gap in the business.** It is a window nobody has computed
yet, or one invalidated by a retroactively approved adjustment and not yet
rebuilt, and plotting it as a continuous line draws a trend that did not happen.
`source_versions` matters on a long series: where a release changed how a figure
is computed, a series spanning the change holds two definitions of one KPI on one
line with nothing marking the join.

## 226. `recompute_kpi_history`

**MUTATING (default OFF).** `kpi_key` (required), optional `company`,
`back_years` (default 5), `force` (default FALSE).

**The mildest mutating tool in this catalogue.** The only thing it can change is
a cache: every row it writes is what the live computation would have produced,
and every row it deletes comes back on the next read or the next overnight sweep.
The worst outcome of running it at the wrong moment is time spent.

**It is the answer to a retroactive approval.** Approving a normalization
adjustment for a period the history already covers invalidates the snapshots
whose window contained it; this rebuilds them *now*, with the result in front of
you, which is what you want when the pack goes out this afternoon. A Field
productive-date backfill is the other case — it moves the denominator of every
window containing the corrected block.

`force=true` **clears and rebuilds** rather than filling gaps. Use it after a
release changes how a figure is computed: an incremental fill leaves the old rows
in place, and a series holding two definitions of one KPI is a line with an
unmarked join in it.

---

## 227. `list_visits`

Read. Optional `company`, `worker`, `location`, `from_date`, `to_date`, `limit`
(default 100, maximum 500).

**A worker does not go to a task, they go somewhere.** They drive to the north
block, walk five cabins, close five task assignments and drive back. The board
records five completions with five timestamps; this reports the trip.

```json
{
  "visits": [
    {
      "visit_id": "5C1F0A64-…",
      "first_completion_datetime": "2026-08-03 09:12:04",
      "last_completion_datetime": "2026-08-03 10:41:55",
      "duration_minutes": 89,
      "location": "MC-Cabin-01",
      "locations": ["MC-Cabin-01", "MC-Cabin-02"],
      "company": "Example Trading Co",
      "completing_user": "HR-EMP-00007",
      "task_assignment_names": ["FTA-2026-00311", "FTA-2026-00312"],
      "total_tasks": 2,
      "total_evidence_files": 5,
      "logged_duration_minutes": 55
    }
  ],
  "count": 1, "single_task_visits": 0, "ungrouped_completions": 14
}
```

**The grouping is the handset's, not a guess from timestamps.** The app mints a
`visit_id` when a worker arrives and reuses it for every task closed before they
leave, because the phone is the only thing that was there. Two cabins forty
minutes apart on one unhurried walk are one trip; two a minute apart from
opposite ends of the property are two — and no threshold gets both right.

**A completion with no `visit_id` is in no visit.** Not a synthetic one-task
visit, not an "unassigned" bucket dressed as a trip. Everything filed before
v0.20.1 has the column blank; `ungrouped_completions` says how many were skipped.

**One-task visits are returned.** Somebody drove out, did one job and drove back
— which is precisely what a question about wasted travel is looking for. Filter
on `total_tasks` if the question is about multi-stop rounds; `single_task_visits`
tells you exactly what you would be dropping.

`duration_minutes` is **first completion to last** and excludes the drive out and
the walk in; a one-task visit measures zero, because one completion is one
instant. `logged_duration_minutes` is the sum of what the workers themselves
recorded per task, which is a different number and deliberately reported beside
it. `total_evidence_files` counts distinct **Files**, not evidence rows: one
signature filed against three cabins is one photograph.

The `location` filter matches a visit **any** of whose tasks is at that place,
and returns the visit whole — reporting a trip with half its work missing would
answer a different question.

There is no `Farm Visit` doctype and there should not be one: a visit has no
facts of its own. Every field above is derived from the completions in it, and a
row that had to be created before them could not be created by a client that was
offline when the trip started.

---

# Templated inspection sessions (v0.21.0)

A worker does not go to a task. They walk into MC-Cabin-01, do everything the
cabin needs, and walk out. Compliance sees three regulated cadences that must
stay separate — a Housing Inspection is annual under 29 CFR 1910.142, a Detector
Test is on the fire code's cycle, a Water Test is Subpart E's ninety days — and a
merged record would be due on three schedules at once.

**The UX is grouped; the records stay separate.** An *Inspection Session* is the
afternoon. The records it produces are the register, produced exactly as they
would have been from three separate trips: same doctypes, same registers
advanced, same alerts dismissed, same audit-packet rows. What changes is that the
photographs and the signature are captured once, and an auditor holding a Housing
Inspection can ask which visit produced it.

## Templates are data

An *Inspection Template* is a Frappe record. It says which sections a visit
consists of, what evidence each section needs, which renderer draws it and which
compliance record it produces. `create_inspection_template` writes one and it is
live: it reaches the handset on the next fetch, the rule engine can match it on
the next sweep, and no code, no DocType JSON and no app build was involved.

Four ship seeded, on install and on every migrate, and an edited one is never
overwritten: **Pre-season Cabin Opening**, **Mid-season Habitability**,
**Post-harvest Cabin Close-down** and **Spray Day Inspection**.

## The runtime is deterministic

`generate_tasks_from_compliance_alerts` bundles by set inclusion and nothing
else. Alerts are grouped by the place they point at; a place with **two or more
alerts of different types** is a candidate; each alert type is translated into
the compliance record it would produce through the same `ALERT_TASK_MAP` the
per-alert path uses; and a template matches when its sections produce a
**superset** of those records. Ties break on (extra sections, total sections,
docname), so the choice is the same on every run and every site.

No match is a first-class answer and the common one. One alert at a place is one
task, unchanged.

## Versions are pinned, and edits supersede

`update_inspection_template` does not edit the row. It writes a **new** row at
version+1, deactivates the old one and points it at the new one. A session links
the row it was worked from, so April's session is still readable in November
against the sections the worker actually saw — and a session started against v1
while v2 is being authored is untouched, because v2 is a different document.

## 228. `list_inspection_templates`

Every template with what it produces, which regimes it answers and which version
is live. Filter by `applies_to_asset_type`, `active` or `regime` — matched by
token, never substring, so a GlobalGAP template never answers a GAP question.

Superseded and inactive templates are listed too: the sessions worked from them
are still readable, and an auditor asking what last October's close-down looked
like is asking about one of those. `live_templates` is the set a new session can
start from.

## 229. `get_inspection_template`

One template in full — every section in working order with its evidence
contract, renderer hint, produced-record doctype and field prompts.

**This is what a client renders a sectioned form from.** `renderer_hint` is a
hint and not a contract: a client that does not know one falls back to a freeform
form and the submission is still valid, which is what lets a template using a
renderer added later reach a handset nobody has updated. The refusal lives in the
evidence contract, never in the renderer.

Takes a docname (one exact version) or a template name (whichever is live).

## 230. `list_inspection_sessions`

Every templated visit — who went, where, from which template and pinned version,
and which compliance records the trip produced. Filter by company, location,
worker, template, state, `visit_id` or date range.

## 231. `get_inspection_session`

One visit in full: the pinned template version with all its sections, every
section submission with what was ticked and measured, the shared evidence tray,
and the compliance record each section produced.

## 232. `create_inspection_template`

**MUTATING.** Authors a template, live immediately.

Each section names `produces_record_doctype` — `Housing Inspection`, `Detector
Test`, `Water Test` — or leaves it empty, which is a real and common answer:
nobody regulates a photograph of an emptied refrigerator as its own document. A
section naming a doctype this app cannot build is refused **here**, at authoring
time, rather than at submission time while somebody is standing in a cabin.

Refuses a template with no sections (that is a name), two sections sharing a name
(the name is the key a submission matches on), a second **live** template with a
name one already holds, and any evidence-contract key outside the vocabulary —
`photos`, `signature`, `findings_text`, `witness`, `checklist_items`,
`measurements` — because `{"photo": true}` asks for nothing and looks like it
asks for something.

```json
{"template_name": "Pre-harvest Block Walk",
 "description": "The walk a block gets before the first pick.",
 "applies_to_asset_type": "Field",
 "skill_required": "food_safety",
 "regimes": ["FSMA", "GAP"],
 "sections": [
   {"section_name": "Animal intrusion check",
    "produces_record_doctype": "",
    "renderer_hint": "multi-photo",
    "required": true,
    "evidence_contract": {"photos": true, "findings_text": true}}]}
```

## 233. `update_inspection_template`

**MUTATING.** Supersedes rather than edits — see above. Arguments left out mean
unchanged; passing `sections` replaces the whole list, because a section list
edited one entry at a time by index is a section list somebody reorders by
accident.

## 234. `deactivate_inspection_template`

**MUTATING.** Stops new sessions starting from a template and records why. It
destroys nothing: every session already worked from it stays readable, every
compliance record those sessions produced stays in the register and in the audit
packet. There is deliberately no delete.

## 235. `start_inspection_session`

**MUTATING.** Opens one visit at one place and pins the template version.

**It writes no compliance record and moves no register.** A started session has
dismissed nothing — the records are created by `submit_inspection_session` and
not before, exactly as a Draft Housing Inspection writes nothing to the camp
register.

## 236. `submit_inspection_session`

**MUTATING, and the one with teeth.** In order: sections are read off the version
the session **pinned**; a submission naming a section that version does not have
is refused; a **required** section that is missing is refused by name; each
submitted section is checked against its own evidence contract and the shortfalls
are named.

**Nothing is written if any of those refuses.** Half a visit is a set of
compliance records that look complete and are not, which is worse than no records
at all — an auditor reading them has no way to know the detector was never
tested.

**Two sections producing the same record for the same subject produce one
record.** A Detector Test carries both a smoke result and a CO result, and both
are required fields, so testing them as two sections — the right shape for a
worker who walks to one detector and then the other — must not file two records
that each assert something they were never told about the other. Both section
submissions link the one record; the trail from either is intact.

An **optional** section may be skipped and its produced-record link stays empty.
That is how a template covering more than is due today stays usable, and the skip
is recorded as something somebody said, because an empty space is not.

```json
{"name": "INSPS-2026-0001",
 "section_submissions": [
   {"section_name": "Habitability walk",
    "evidence_file_tokens": ["1a2b3c"],
    "signature_file": "9f8e7d",
    "notes": ""},
   {"section_name": "Smoke Detector Test",
    "checklist_values": {"smoke_alarm_sounds": true},
    "record_data": {"smoke_detector_result": "Pass"},
    "notes": ""},
   {"section_name": "CO Detector Test",
    "checklist_values": {"co_alarm_sounds": true},
    "record_data": {"co_detector_result": "Pass"},
    "notes": ""}]}
```

`notes` as an **empty string** records that nothing was wrong; leaving it out
records that nobody was asked, and the two are different answers. `record_data`
names fields on the produced compliance record — it is where a Water Test section
names the Irrigation Zone, which a session at a cabin cannot supply, because one
cabin can draw from several sources and this app will not guess.

A record whose findings are alarming is still filed: it routes itself to
Corrective Action Required and raises its own Critical alert, exactly as it would
from a single-task completion.

## 237. `propose_inspection_template_from_regulation`

**MUTATING (default off).** Declared in v0.21.0, **wired in v0.37.0.** Draft an
Inspection Template read off a regulation — its sections, their evidence
contracts, the compliance records they produce. It lands **inactive**, marked
`AI-proposed` with the source it was read from, and no handset fetches it until a
person approves it with `approve_inspection_template`.

**It calls no model.** The AI doing the proposing is the *client*: you read the
regulation, you draft the sections, you pass them here. The tool is the validator
and the gate — it refuses the wrong shape, stamps the provenance you do not get
to choose, lands it off, and flags what needs more than a skim.

Takes every argument `create_inspection_template` takes, plus `regulation_url`,
`regulation_section`, `regulation_text` (a short excerpt is quoted onto the
record) and `read_on`. One of the url, the section or an explicit
`ai_source_citation` is **required**: a draft that does not name the text it read
cannot be checked against it, which is the whole of what the approval does.

It will not write `active`, will not write `authored_by` as anything but
`AI-proposed` (passing `Operator` is refused, not corrected), and will not fill in
the approver or the approval date. A draft for a `template_name` that is already
live is written at version+1 and **touches nothing** — the worker starting a visit
this afternoon gets the form somebody approved.

Flags a section with an **empty evidence contract** (it can be filed empty and
still looks complete) and a draft whose approval will stand a live template down.

## 238. `approve_inspection_template`

**MUTATING (default off).** Accept an inactive template and turn it on, recording
**who** and **when** on the record itself. The counterpart to
`approve_compliance_rule`, and it exists for the same reason: a form a worker is
asked to fill in is a compliance artefact whoever wrote it.

Where a live template already holds the name at a lower version, that row is
deactivated and pointed here — superseded, never edited, so every session already
worked from it stays readable against the sections the worker actually saw.

Works on any inactive template, not only a proposed one: reinstating one somebody
withdrew is the same act and deserves the same name against it.

## 239. `get_compliance_rule`

Read-only. One rule in full: the condition it evaluates, the thresholds and scope
filters it evaluates it against, the regulation it cites, the regimes it answers
to, the kairotic gate saying what makes it ripe, and **who approved it and
when**.

Takes a docname (one exact version) or a `rule_id` such as `training_expiring`,
which resolves to whichever version is live — what somebody asking about a rule
today means. Pass the docname of a **superseded** row to read the definition an
older alert was raised under; those rows are never edited and never deleted,
which is the whole point of versioning by copy.

## 240. `test_compliance_rule`

Read-only. Runs ONE rule against the data as it stands and reports every
observation it WOULD make — with the alert docname each would take — writing
nothing.

**The tool to call between authoring a rule and approving it.** It takes the same
code path the nightly sweep takes, deliberately: a dry run with its own second
implementation is a dry run that can disagree with the real one.

What to look for: a rule that observes four hundred rows is a rule whose
condition is wrong — almost always a field that is empty everywhere rather than
stale on a few. `computation_warnings` names anything the engine worked around,
such as a scope filter on a column this site has not got.

## 241. `create_compliance_rule`

**MUTATING (default off).** Authors a new compliance rule — a condition the
nightly sweep evaluates against this site's records — with no code release. It
arrives as a **Draft** and fires nothing until `approve_compliance_rule` turns it
on.

The runtime stays deterministic and there is no model in it: a rule is a
declarative expression over record state — query `target_doctype`, apply
`scope_filters`, measure `date_field` against `cadence_days` and the thresholds,
render `message_template`. That is what lets every alert be traced to a rule, a
citation, an approver, and the specific field that crossed a threshold.

Refuses a duplicate `rule_id` (two rules sharing one collide on the alert
docname); a `rule_id` with a colon or a space (the docname is
`<rule_id>:<doctype>:<name>`); a `target_doctype` this site has not got; no
kairotic gate; a malformed scope filter or unknown operator; and any
`custom_python` the sandbox would not run.

**Since v0.22.1** the declarative vocabulary also covers a finding superseded by
a later clean record (`superseded_by_later_clean`), a second date used only as a
gate (`gate_date_field` / `gate_within_days`), several anchors of the same kind
with per-field labels (`date_fields`), an ordered lookup reading regimes or a
category off a name (`regime_heuristics`, `category_heuristics`), a date that is
a timestamp rather than a deadline (`date_field_role`), and one rule walking two
kinds of record (`target_doctypes`).

**Since v0.22.5** it also covers a rule that fires on a **data state** rather than
on any distance from a date: `latest_child_field_threshold` folds a child table
to the newest row per record and reads a number off it — against a literal, or
against a per-company setting via `threshold_source`, so the alert layer and the
operational sweep cannot disagree about what "hot" means on the same afternoon.
`date_field_role: "State"` says the rule has no clock, `default_severity` says
what it raises at instead, and `producer_assigned_to_expression` sends the
producer task to one named person (`row.foreman`) rather than into a skill pool.

Twelve of the fourteen shipped rules are now fully declarative and none uses
`custom_python` — `docs/configurable_compliance_framework.md` §4 has the table of
questions that are already fields.

## 242. `approve_compliance_rule`

**MUTATING (default off).** Accepts a rule and turns it on, recording who
accepted it and when **on the record itself**.

This is the gate, and there is no way round it: the DocType refuses `enabled`
without an approver and a date. So no rule — least of all one a model proposed —
starts firing without a person having put their name to it. Also reactivates a
rule that was disabled. Optionally attaches the approver's signature as a File.

**Since v0.37.0 it does two more things, both for AI-proposed drafts.** It refuses
a draft carrying model-written code — `custom_python`, or a producer assignee
expression — until the approver passes `accept_ai_authored_code`, and the refusal
prints the program back at them. And where the draft is a proposed *replacement*
for a rule that is already live, approving it supersedes that rule: disabled,
pointing at the new row, never edited, and every alert it raised left exactly as
it was.

## 243. `update_compliance_rule`

**MUTATING (default off).** Changes a rule by **superseding** it: a new record at
version+1, the old one disabled and pointing at it. The old row is never edited.

That is why an alert from April is still explicable in November, and why a sweep
that started against v1 finishes against v1. Arguments left out mean unchanged.
The new version inherits the old one's approval — a threshold moved is not a new
rule, and forcing re-approval on every tuning edit trains people to click through
approvals. A rule that was off stays off.

The result carries a field-by-field before → after, so the MCP Action Log row
records what the rule said **before** rather than only what was asked for.

**The most common edit this exists for**: when OR-OSHA renumbered heat illness
from -1130 to -1131, `regulation_citations` was the only thing that had to move.

## 244. `deactivate_compliance_rule`

**MUTATING (default off).** Stops a rule firing, and records why.

**It dismisses nothing.** Every alert the rule already raised stays on the
calendar exactly as it was, and the next sweep will not touch it — the same
reading a rule skipped for a missing DocType gets, and for the same reason:
switching a rule off is not evidence that anybody did the work. The result says
how many are left standing.

There is deliberately no delete. The rule stays on the site, disabled, with the
reason on the record — so the operator who asks next season why the calendar
stopped mentioning the thing that then went wrong gets an answer from the record
rather than from somebody's memory.

## 245. `propose_compliance_rule`

**MUTATING (default off).** Declared in v0.22.0, **wired in v0.37.0.** Draft a
compliance rule read off a regulation. It lands **disabled**, marked
`AI-proposed`, with the source on the record, and sits in the review queue until a
person approves it.

**It calls no model, and that is the whole design.** The AI doing the proposing is
the *client*: you read the regulation, you draft the record, you pass it here as
arguments. What the tool does is the part a proposer cannot do for itself —
refuse the wrong shape, stamp the provenance, land it off, and put what needs a
second pair of eyes where the approver will see it. A validator and a gate, not an
author.

Takes every argument `create_compliance_rule` takes, plus `regulation_url`,
`regulation_section`, `regulation_text` and `read_on`. Draft **declaratively** —
`target_doctype`, `date_field`, `cadence_days`, the thresholds, `scope_filters`,
`message_template` — because a rule that is a set of fields is a rule an approver
can check against the regulation in a minute.

**Four things it will not let you do.** Write `enabled`. Write `authored_by` as
anything but `AI-proposed` — passing `Operator` is refused rather than corrected,
because that argument is an attempt to launder provenance. Fill in the approver,
the approval date, the approver's employee or their signature. And there is no
propose-a-delete and no propose-a-disable anywhere in this app: a proposal for a
`rule_id` that already exists is drafted at version+1 and **touches nothing**, so
the live rule goes on running on its own definition until a person approves the
replacement. The result carries the field-by-field diff, because what a reviewer
of an edit needs is what changed.

**`custom_python` is flagged for extra review.** The sandbox refuses what it
refuses at authoring time; what it cannot say is whether the program asks the
right question. A draft carrying one — or carrying a producer assignee expression
— gets `ai_review_flags` on the record, and `approve_compliance_rule` refuses it
until the approver passes `accept_ai_authored_code`. The refusal prints the
program back at them: an acknowledgement of code nobody displayed is not one.

## 246. `list_regulation_feeds`

Read-only. The regulation register: every source this site watches for change,
with the URL, the regime it serves, how often it is checked, when it was last
looked at, and when it last **moved**.

**What the register is for.** v0.22.0 made a compliance rule a record and v0.37.0
let a model draft one from a regulation. Neither of those knows anything about the
regulation six months later, when OR-OSHA renumbers a subsection or a certifier
reissues a handbook. A feed is the pointer that was missing.

Read `status` as a report rather than as a setting. **Error** is what the last
check said, not a decision anybody made — the sweep retries an errored feed and a
successful check clears it back to Active. **Paused** is the decision, and it is
the only state that keeps a feed out of the sweep.

`never_checked` is the list to act on first: a source nothing is known about looks
exactly like a source that has not changed.

| Parameter | Required | Description |
|---|---|---|
| `status` | | `Active`, `Paused` or `Error` |
| `regime` | | One audit: OR-OSHA, FSMA, WPS, GAP, GlobalGAP, PrimusGFS, NOP, OTCO, Internal |
| `company` | | Company name or abbreviation |
| `due_only` | | Only feeds the sweep would check right now |
| `limit` | | Default 100, hard maximum 500 |

## 247. `get_regulation_feed`

Read-only. One source in full, **including its change log** — every change, error
and recovery it has seen, one timestamped entry each, newest first.

The change log is the only account anywhere of what a source has done over time,
and it is append-only: no entry is ever edited, and when it reaches its cap the
*oldest* lines are dropped with a line saying so. A dropped-newest log would be a
detector that had quietly switched itself off.

A `CHANGED` entry carries the hash it moved from, the hash it moved to, the size
of the normalised text, and the `rule_id` of every Compliance Rule derived from
that source. **A rule named in an entry was not touched.** The link is
informational in one direction: nothing in this app edits, disables or supersedes
a rule because a web page changed. It says where to look.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Regulation Feed docname, or part of the feed name |
| `feed` | | Alias for `name` |
| `log_limit` | | Change log entries to return, newest first |

## 248. `list_regulation_changes`

Read-only. Which regulations have moved since a date, and which compliance rules
were written from them.

**The question this whole surface exists to answer** — *what regulations moved
since our last compliance review* — and the tool a quarterly review opens with. It
is a filter on `last_change_detected` and nothing cleverer: the fact was recorded
when it happened, by the sweep, so answering it later costs one query and no
network at all.

`rules_to_review` is a **reading list, not a changelog of your calendar.** Every
rule named there is still running on exactly the definition a person approved.
Where one genuinely needs to change, read the source and use
`propose_compliance_rule` — the draft lands disabled with its citation on it, and
`approve_compliance_rule` is where a name goes on the replacement.

| Parameter | Required | Description |
|---|---|---|
| `since` | | `YYYY-MM-DD`. Default 90 days ago |
| `regime` | | Only sources for one audit |
| `company` | | Company name or abbreviation |
| `limit` | | Default 100, hard maximum 500 |

## 249. `create_regulation_feed`

**MUTATING (default off).** Register a regulatory source — a URL, the regime it
serves, what it covers and how often to look — so the site notices when the
regulation moves. It writes a pointer and fetches nothing until a check runs.

**Point it at the narrowest page that carries the rule**: a division of the
rulebook rather than the rulebook's index, a specific Federal Register document
rather than the search that found it. A broad page changes for reasons that have
nothing to do with this operation, and every one of those is a person asked to
read a regulation for nothing.

`affected_rules` is the link back to the rules this source produced, by docname or
by `rule_id`. Informational in one direction only: a detected change names those
rules in the log so a reader knows where to look, and **nothing in this app edits,
disables or supersedes a rule because a page changed.**

Refuses a feed name already on the site (the name is the docname); a URL that is
not `http(s)`, because that field is handed to an outbound request by a scheduled
job; a description shorter than a sentence, because the description is what
somebody reads when the log says this moved; a regime the vocabulary does not
hold; and an `affected_rules` entry that resolves to no Compliance Rule.

| Parameter | Required | Description |
|---|---|---|
| `feed_name` | yes | The docname. Name it after the regulation, not the website |
| `url` | yes | The `http(s)` URL that is checked |
| `description` | yes | What is at that URL, and what on this operation turns on it |
| `regime` | | The audit this source answers to |
| `check_frequency` | | `Daily`, `Weekly` (default) or `Monthly` |
| `status` | | `Active` (default) or `Paused`. `Error` cannot be set by hand |
| `company` | | Company name or abbreviation |
| `affected_rules` | | Compliance Rules written from this source, by docname or `rule_id` |

## 250. `update_regulation_feed`

**MUTATING (default off).** Edit a source's URL, description, regime, frequency,
status or rule links. Pausing one here is the kill switch: a paused feed is
skipped by the sweep and keeps its whole change log.

**It cannot write the detector's own memory.** `last_content_hash`,
`last_checked`, `last_change_detected` and `change_log` are *refused* as arguments
rather than ignored: a hash somebody typed is a change that will never be
reported, and a change log somebody edited is the one record here whose entire
value is that nobody edited it.

**Changing the URL clears the stored hash**, and logs that it did. A hash taken
over one page says nothing about another, so leaving it would make the next check
report a change that is really a change of subject.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Regulation Feed docname, or part of the feed name |
| `url` | | A new `http(s)` URL. Clears the stored hash |
| `description` | | What this source covers |
| `regime` | | The audit this source answers to |
| `check_frequency` | | `Daily`, `Weekly` or `Monthly` |
| `status` | | `Active` or `Paused` |
| `company` | | Company name or abbreviation |
| `affected_rules` | | Replaces the whole set of linked rules |

## 251. `check_regulation_feed`

**MUTATING (default off), and it makes an outbound request.** Fetch one source now
and say whether its content changed since the last check.

**It detects and it does not remediate,** and that line is the design rather than
a limitation. A changed page is evidence that somebody should read a regulation
again; it is not evidence about what the regulation now says, and it is not
authority to rewrite a rule firing on somebody's compliance calendar. So a change
writes a hash, a timestamp and a log line naming the rules derived from that
source, **and stops.**

**The hash is of normalised text, not of the bytes** — tags, scripts, comments,
entity escapes, ISO and US and month-name dates, clock times and long hex strings
taken out, whitespace collapsed — because a page that stamps itself with the
minute it was served would otherwise report a change on every check, and a
detector that always fires detects nothing. **The cost is real and stated: a
change that is *only* a date is invisible to it.**

The first check is a **baseline** and cannot be a change, because there is nothing
to compare against. A fetch that fails sets the feed to `Error` with the message
and **does not move `last_checked`**, so the next sweep retries rather than
waiting out the whole frequency.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Regulation Feed docname, or part of the feed name |
| `force` | | Check even a Paused feed. Default false |

## 252. `check_all_regulation_feeds`

**MUTATING (default off), and it makes several outbound requests.** Run the sweep
now: every source that is not Paused and is older than its own `check_frequency`.
Returns which ones **moved** and which could not be reached.

The same function the daily scheduler calls, with the same due logic —
deliberately, because a manual sweep with a second implementation is one that can
disagree with the nightly one. One source's failure is one source's failure: an
agency site behind a WAF does not stop the other eleven being checked, and nothing
here raises.

`force` checks every unpaused feed regardless of when it was last looked at —
right before a certification audit, and rude to a public server nobody is paying
for as a habit.

| Parameter | Required | Description |
|---|---|---|
| `company` | | Company name or abbreviation |
| `force` | | Ignore each feed's frequency. Default false |

---

# The Financial KPI Framework (v0.39.0)

**v0.19.6 made the window standard generalize across three *shipped* reports.
This makes the KPI itself a record.**

Before this release, adding a KPI meant a Python function, a registration call, a
test, a review, a release and a deploy — a perfectly good process for a KPI this
app's authors chose, and no process at all for a KPI somebody's lender asked
about on a Tuesday. Every operation has two or three ratios that are genuinely
its own: a packing house watches cost per bin, an operation carrying an equipment
note watches debt service coverage on the *covenant's* definition and not on
anybody else's. None of those belong in a shipped app and all of them belong on
the dashboard of the farm that needs them.

So a KPI is now a `Financial KPI Definition`, and the seven tools below author
and run one.

**The engine does not move.** `formula_type` has exactly two values and both are
deterministic. `Built-in` delegates to a computer that ships with this app,
reviewed like any other code, while the record still owns the window, the step,
the lookback, the thresholds, the dashboard order and the switch. `Expression`
evaluates arithmetic over named inputs in a sandbox that parses to an AST and
checks every node against an allowlist. **There is no third value and there is no
field on the record that holds Python** — which is a deliberate difference from
`Compliance Rule.custom_python`, because a compliance rule can need to express a
shape no set of fields captures, and a financial KPI is a number divided by
another number.

**A definition holds the question and never an answer.** Every figure is computed
from the ledger when somebody asks, cached in `Financial KPI History` *with the
components that produced it*, and derivable again by rerunning the same
computation. Nothing on a definition is a number that came out of the books.

**`kpi_id` is the cache key, so it is unique and it cannot move.** A Compliance
Rule is versioned by copy — an alert raised in April can still be read against
the definition that raised it. A KPI is a *line*, and a line assembled from two
definitions of one number is a chart with an unmarked join in it. Changing the
arithmetic of a live KPI is a new `kpi_id` beside the old one.

**Thresholds go to the compliance calendar, not to a second alerting system.** A
KPI past its critical threshold raises a `Compliance Alert` under the new
`Finance` category, through the same sweep, with the same dismissal, the same
snooze and the same auto-clear when the value comes back inside. An operation
with two alerting systems reads neither. The threshold scan **reads the cache and
never computes**: the alert sweep runs hourly beside somebody's real work, so it
reads the newest cached snapshot — which is the same figure the dashboard is
showing, so an alert and a dashboard can never disagree about the number.

**The overnight refresh is at 03:00**, between the shipped-report sweep at 02:00
and the regulation feed at 04:00. It is *one job that iterates*, which here is
load-bearing rather than tidy: the whole point of the release is that an operator
adds a KPI without a code release, and a KPI that needed its own scheduler entry
would be one they could not add. It shares `enable_kpi_history_sweep` with the
02:00 job rather than getting a second checkbox.

## 253. `create_financial_kpi_definition`

**MUTATING (default OFF).** `kpi_id` and `title` required; everything else
optional.

| Parameter | Required | Description |
|---|---|---|
| `kpi_id` | yes | Lower-case letters, digits and underscores. The cache key — it cannot be changed later |
| `title` | yes | What it is called on a dashboard |
| `description` | | What it means and which direction is good |
| `category` | | Profitability, Liquidity, Leverage, Efficiency, Operational or Custom (default) |
| `unit` | | Currency (default), Percentage, Ratio, Days, Acres or Units |
| `formula_type` | | `Built-in` (default) or `Expression` |
| `builtin_function` | | For Built-in: `sustainable_cf_per_acre`, `ocf` or `revenue` |
| `expression` | | For Expression: the arithmetic over the input variable names |
| `expression_inputs` | | For Expression: a JSON object mapping each variable to its source |
| `company` | | One entity, or **empty for every company** — the ordinary case |
| `enabled` | | Default true |
| `default_window_type` | | Snapshot, TTM (default), MTD, QTD, YTD, Custom |
| `default_window_months` | | Default 12 |
| `default_computation_step` | | Daily, Weekly, Monthly (default), Quarterly, Yearly |
| `historical_averaging_enabled` | | Default true |
| `historical_lookback_years` | | Default 5, maximum 10 |
| `threshold_warning_low` | | Warning at or below. **Omit where low is not bad** |
| `threshold_critical_low` | | Critical at or below. Must be at or below the warning floor |
| `threshold_warning_high` | | Warning at or above |
| `threshold_critical_high` | | Critical at or above. Must be at or above the warning ceiling |
| `dashboard_visible` | | Default true |
| `display_order` | | Position within the category, lowest first |

**`expression_inputs` has four sources.**

```json
{
  "current_assets":      {"source": "gl", "root_type": "Asset", "balance": true},
  "current_liabilities": {"source": "gl", "root_type": "Liability", "balance": true},
  "sales":               {"source": "report", "report_name": "revenue", "path": "total"},
  "cf_per_acre":         {"source": "kpi", "kpi_id": "sustainable_cf_per_acre"},
  "sqft_per_acre":       {"source": "constant", "value": 43560}
}
```

`gl` sums ledger movement over the window; narrow it with `root_type`,
`account_type`, `accounts` or `account_number_prefix`. **`"balance": true` makes
it a position at the window's end rather than a movement across it** — a current
ratio built from twelve months of movement in a cash account is not a current
ratio, it is a cash flow with a ratio's name on it. `report` reads a component
off a shipped computer. `kpi` is another definition's value, with cycles refused
at save time. `constant` is a number with a name, which is what a magic number in
a formula should always have been.

**What the expression grammar allows:** `+ - * / // % **` on numbers and variable
names, unary minus, parentheses, comparisons inside a conditional, and calls to
`min`, `max`, `abs`, `round`. **What it refuses, by name, at save time:** imports,
attribute access, subscripts, lambdas, comprehensions, assignment, string
constants, and any call to anything else. A division by zero is not an error — it
is a null value with a warning naming the variables that were zero, because a
farm with no acres in production has no cash flow per acre and a zero there would
be read as one.

**Enabled on creation**, unlike a Compliance Rule, and the difference is what the
two do when wrong: a rule that fires wrongly accuses somebody of a compliance
failure; a KPI that is wrong reports a number beside its own ingredients and its
own warnings, which a reader can check.

## 254. `update_financial_kpi_definition`

**MUTATING (default OFF).** `kpi_id` required; every field above optional.

**It edits rather than superseding.** See the note above on why a KPI is not
versioned by copy. `kpi_id` cannot be changed at all — renaming it orphans the
whole cached series.

**Changing the arithmetic is reported as a decision**, with the cached row count
in front of you. The usual right move is a new `kpi_id` beside the old one; where
it is genuinely a correction rather than a redefinition,
`refresh_kpi_cache(force=true)` rebuilds the whole series under the new formula
so the line holds one definition again.

The result carries the field-by-field diff **with the previous values**, so the
MCP Action Log row answers "who changed this and what did it say before" without
anybody reading a git history they have no access to.

## 255. `list_financial_kpi_definitions`

**Read-only.** Optional `company`, `category`, `formula_type`, `enabled`,
`dashboard_only`, `limit`.

The register: what this site computes, how, and what it alerts on.
`builtin_functions_available` names the shipped computers a Built-in definition
may point at.

**`thresholded_count` is the number worth reading first.** A KPI with no
thresholds is one nothing is watching for anybody, and it can never appear in
`compute_all_kpis`'s `breached` list however bad it gets.

## 256. `get_financial_kpi_definition`

**Read-only.** `kpi_id` (by kpi_id or docname) required.

One definition in full, with how much history is cached under it and whether it
would compute at all. **`problems` is the field to read:** a Built-in naming a
computer this site has not got, an expression that no longer parses, an input the
expression never reads — each produces nothing at compute time and says so in a
warning rather than reporting a zero, and this is where to see it before somebody
quotes the KPI.

## 257. `compute_kpi`

**Read-only.** `kpi_id` and `company` required; optional `as_of`, `window_type`,
`window_months`, `computation_step`, `historical_lookback_years`,
`include_historical_averages`.

**It goes through the same window standard `get_windowed_report` does**, so a KPI
somebody typed into a form this morning and the one that shipped in v0.19.5
behave identically at the fiscal year boundary, on a partial ledger, and against
the cache. **The window comes from the definition by default**, which is what
keeps a dashboard, its alerts and its cache agreeing without anybody passing
anything.

**Four blocks.** `point_in_time` is the period just finished, which on a farm
flatters harvest and demonizes pruning. `window` is the rolling figure with its
components — for an Expression KPI, every input with what it matched, how many
accounts, how many entries, and whether it was read as a balance or a movement.
`historical_averages` is what that window has been worth before. `threshold_status`
is where the value sits against the lines somebody drew, and **`No thresholds`
means nobody drew any**, which is not the same as being inside them.

**A null value is an answer.** A ratio whose denominator was zero is a division
nobody performed. Read `computation_warnings` before quoting anything.

**It warms a cache and that is the one thing it writes:** no Account, no GL Entry,
no Journal Entry, no Asset, no Field.

## 258. `compute_all_kpis`

**Read-only.** `company` required; optional `dashboard_only`, `category`,
`as_of`, the window overrides, `include_historical_averages`.

The whole financial dashboard in one call. **One call rather than N**, for the
reason `get_windowed_report` is one tool rather than one per report: a framework
whose every KPI costs a round trip is a framework with six KPIs in it.

**One broken definition does not empty the dashboard.** Each KPI is computed
independently and a failure becomes a null value with a warning on that row — the
same promise the compliance sweep makes about one rule that throws.

**Read `breached` first and `unwatched_note` second.** An empty `breached` list is
not a healthy operation: a KPI with no thresholds can never appear there, and the
note says which ones those are.

`include_historical_averages=false` across the board answers much faster on a
dashboard with many KPIs.

## 259. `refresh_kpi_cache`

**MUTATING (default OFF).** Optional `kpi_id` (omit for every enabled
definition), `company`, `back_years` (default 5), `force` (default false).

**The only thing it can change is a cache** — the same promise
`recompute_kpi_history` makes, extended to KPIs that are records rather than
code. Every row it writes is what the live computation would have produced for
that window; every row it deletes comes back on the next read or the next
overnight run.

**It is the answer to a changed formula**, and to a KPI created this morning,
which has no history at all until this runs or until the 03:00 job reaches it —
and a chart with one point on it is not a trend.

`recompute_kpi_history` will also take a `kpi_key` that names a definition and
delegates here, so a caller who already knows that tool does not have to learn
this one.

---

## v0.26.0 — field-initiated task creation from asset scan

Worker scans an asset's QR tag and taps "Flag needs repair" to create a Farm Task
linked to the asset, with skill and location auto-filled from the asset type.

### `report_asset_issue`

**MUTATING (default OFF).** Convenience wrapper: report a problem on a specific
asset. Looks up the asset, auto-fills `skill_required` from the asset type
(Housing Unit → camp_maintenance, Irrigation Valve → irrigation, etc.), then
creates a Farm Task linked to the asset.

Delegates to `report_field_task` under the hood — same anti-spam, same photo
requirement, same urgency cap. The difference is the caller names an asset
instead of manually providing location and skill.

| Parameter | Required | Description |
|---|---|---|
| `asset_name` | yes | Asset Register docname from the QR/NFC tag |
| `reported_by` | yes | Employee id of the reporting worker |
| `photo_file_token` | yes | File docname from `finalize_staged_file` |
| `description` | | What the problem is |
| `urgency` | | Normal or High (Critical restricted to Foreman/Manager) |
| `task_type` | | Default Repair |
| `skill_required` | | Override the auto-mapped skill |
| `gps_lat` / `gps_lon` | | GPS coordinates |
| `company` | | Defaults to the asset's company |

Also in this release:

- `report_field_task` gains an optional `asset` parameter to link a task to an asset
- `scan_asset` response includes `can_report` and `suggested_skill`
- Farm Task doctype gains an `asset` Link field to Asset Register
- Tasks linked to an asset appear in `get_asset_detail`'s history timeline

---

## v0.31.0 — expense receipt capture

A foreman photographs a receipt at the fuel pump or the parts counter, iOS Vision
OCR reads the merchant, the total and the date off it **on the device**, and the
phone posts the extracted fields, the image and the raw OCR text here in one call.

The extraction runs on the phone on purpose. The photograph is the largest thing
in the payload and the extraction is the cheapest part of the job; doing it there
means the foreman sees what the machine read before they put the phone away and
can correct it while the paper is still in their hand. By the time these tools see
a receipt, a person has already looked at the reading.

Two DocTypes: **Expense Receipt** (the register) and **Expense Receipt Item** (the
line detail, a child table).

### `list_expense_receipts`

**READ (default ON).** Receipts filtered by `status`, `employee`,
`company`, `category`, `farm_task` and a `from_date`/`to_date` range on the
receipt date. Returns each receipt's extracted fields, its image URL and the
scanner's confidence, plus `count` and `total_amount` for everything that matched.

**Ordered lowest OCR confidence first.** The receipt nobody can read is the one
somebody has to open the photo for; sorting it last would put it where it is never
looked at.

| Parameter | Required | Description |
|---|---|---|
| `company` | | Company name or abbreviation |
| `status` | | `Draft`, `Submitted`, `Approved` or `Rejected` |
| `employee` | | Who submitted it — docname or employee name (`submitted_by` is an alias) |
| `category` | | `Fuel`, `Equipment Parts`, `Supplies`, `Hardware`, `Feed`, `Seed`, `Fertilizer`, `Other` |
| `farm_task` | | Only the receipts booked against one task |
| `from_date` / `to_date` | | Receipt date range, `YYYY-MM-DD` |
| `limit` | | Default 100, hard maximum 500 |

### `get_expense_receipt`

**READ (default ON).** One receipt in full: the extracted fields, the photograph,
the line items, `ocr_raw_text` — everything the scanner read, unedited — and the
review trail: who approved or rejected it, when, and on what grounds. `items_total`
is the sum of the lines and is **not** expected to equal `amount`.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Expense Receipt docname (`expense_receipt`, `receipt` are aliases) |

### `submit_expense_receipt`

**MUTATING (default OFF).** Capture one expense. Creates the receipt as
`Submitted`; pass `status: "Draft"` for a client holding an offline queue.
Approval and rejection are separate tools with separate switches, so this call
cannot create an already-approved receipt.

| Parameter | Required | Description |
|---|---|---|
| `merchant` | yes | The vendor as it reads on the receipt |
| `amount` | yes | The receipt total, including tax |
| `receipt_date` | yes | `YYYY-MM-DD` |
| `submitted_by` | yes | Employee who photographed it — docname or name (`employee` is an alias) |
| `company` | | Required on a multi-company site |
| `category` | | Defaults to `Other` |
| `farm_task` | | The job the expense was incurred for |
| `status` | | `Draft` or `Submitted`. Defaults to `Submitted` |
| `receipt_image` | | File URL of the photograph |
| `ocr_raw_text` | | Everything the scanner read, kept for audit |
| `ocr_confidence` | | A **fraction from 0 to 1**, not a percentage |
| `items` | | `[{description, quantity, unit_price, line_total}, …]` |
| `notes` | | Anything the person capturing it wants to add |

`ocr_confidence` is range-checked rather than rescaled. A scanner reporting `87`
meant `0.87` and is refused with that sentence — silently dividing by 100 would
put a guessed number in the field an approver uses to decide what to look at, and
`1.4` is genuinely ambiguous between a percentage and a bug.

A line item with a `quantity` and a `unit_price` but no `line_total` gets the
product; one that carries its own total keeps it. OCR reads a bold receipt total
far more reliably than a column of line arithmetic, and a receipt that charges
four at $3 and totals $11.50 after a discount is telling the truth.

### `approve_expense_receipt`

**MUTATING (default OFF).** Sets `status` to `Approved` and records
`approved_by` and `approved_date`.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Expense Receipt docname |
| `approved_by` | yes | Approving Employee — docname or name |
| `approved_date` | | `YYYY-MM-DD`. Defaults to today |

### `reject_expense_receipt`

**MUTATING (default OFF).** Sets `status` to `Rejected` and records
`rejected_by`, `rejected_date` and `rejection_reason`.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Expense Receipt docname |
| `reason` | yes | Why it was refused (`rejection_reason` is an alias) |
| `rejected_by` | yes | Rejecting Employee — docname or name |
| `rejected_date` | | `YYYY-MM-DD`. Defaults to today |

The reason is required and is stored **on the record**, not in a comment, so
`get_expense_receipt` returns it to the phone that submitted the thing. A
rejection with no sentence beside it is the state that generates the next three
messages asking why.

**Neither decision can be taken twice.** Only a `Draft` or `Submitted` receipt can
be approved or rejected — deciding an already-decided one would overwrite the name
and date of whoever decided it first, which is the one thing an approval record
exists to preserve.

---

## v0.32.0 — geo map views and crew tracking

Two halves of one idea: **the geography this app has been storing since v0.12.0
becomes something a person can look at, and a shift stops being a place and
starts being a path.**

### The map

A Leaflet map is injected into seven Desk forms through `doctype_js`, all seven
of them doctypes this app created.

| Form | What it draws |
| --- | --- |
| `Parcel` | its own outline |
| `Field` | its boundary, over its parcel's |
| `Irrigation Zone` | its boundary, over the block it waters |
| `Housing Unit` | a marker, over its parcel's outline |
| `Asset Register` | a marker at `gps_latitude` / `gps_longitude` |
| `Farm Task` | the location resolved through whichever register `location_doctype` names |
| `Farm Shift` | the shift's anchor, plus the crew's track as a coloured polyline |

**Drawing the containing shape underneath is the point of the whole widget.** The
boundary tools *report* containment and never enforce it, and a disagreement
reported in a warning string is a disagreement nobody pictures. Drawn, the
difference between "that is the corner we always farmed across" and "two vertices
are in the wrong order" takes a second to see.

**Nothing on any of these forms writes.** There is no drag-to-move marker, no
draw-a-polygon tool and no save path of any kind. A boundary is compliance
evidence and it is set through the three boundary tools, which validate the shape,
refuse a self-intersection, compare the area against the recorded acreage and
recompute every derived field. *(v0.33.0 added a draw tool to the three
boundary-carrying forms. The sentence after the full stop is what mattered and is
unchanged — see that release's section below.)*

**The library comes from a CDN and the tiles from OpenStreetMap**, so a bench with
no outbound internet gets no map. That is handled rather than left to fail: the
section says the library could not be reached and prints the coordinates
underneath. The record is the coordinates; the map is a reading of them.

`doctype_js` was a **forbidden hook** until this release, spelled "this app adds
no client script to a doctype it does not own" — and the clause after the comma
was always the real rule. `test_hooks.py` now asserts that every doctype named is
one this app shipped, that every file named exists, and that the shared widget is
listed first in every entry (Frappe concatenates them in order, and a widget
listed second is a `ReferenceError` that takes the whole form script down).

### The track

**Shift Location Log** — one GPS fix, taken during a shift, at a time. Standalone
rather than a child table of the shift: a nine-hour shift at a fix every two
minutes is two hundred and seventy rows, and a child table is loaded whole every
time anybody opens the shift form.

- **210a `log_shift_location`** (write, default OFF) — what the phone posts.
- **210b `get_shift_track`** (read, default ON) — what the map draws.

See those sections above for the ordering rule, the gap reporting and why an
open shift is not required.

### The parcel outline

**118a `set_parcel_boundary`** (write, default OFF) closes the gap
`set_field_boundary` had been apologising for on every call since v0.12.0. Parcel
now carries the same derived suite Field and Irrigation Zone do, and
`set_field_boundary` returns `boundary_contained_in_parcel` — `true`, `false`, or
`null` where the parcel has no shape to check against.

**Housing Unit gains `gps_latitude` and `gps_longitude`**, accepted by
`create_housing_unit` and `update_housing_unit`, and returned as a `gps` object
(or `null` — never `{lat: 0, lon: 0}`, because null island is a real place and it
is what an unset Float pair looks like). The pair moves together or not at all:
passing one is filled in from the stored other, and a genuine half-pair is
refused.

---

## v0.33.0 — drawing a boundary, and importing one from the county

**NO NEW MCP TOOLS.** The tool count is unchanged and every number above still
holds. What this release adds is a second *caller* of three tools that already
existed, reached from a Desk form rather than from the model — so it is
documented here rather than in the catalogue proper.

### Two whitelisted methods, and neither is on the MCP surface

| Method | What it does |
| --- | --- |
| `erpnext_mcp.api.gis.save_boundary` | takes a drawn or imported polygon and calls `set_parcel_boundary`, `set_field_boundary` or `set_zone_boundary` |
| `erpnext_mcp.api.gis.query_county_parcels` | asks a county's ArcGIS parcel layer for a shape, by tax lot number or by a point |

They are reached at `/api/method/erpnext_mcp.api.gis.<name>` by a signed-in Desk
user. `security.authorize()` — the master switch, the shared `X-MCP-Token`, the
CIDR allowlist — does not run on that path and cannot, so the gate is rebuilt in
`api/gis.py`: a named user, `frappe.has_permission(doctype, "write", doc=name,
throw=True)` on the **specific** document, and a closed list of three doctypes
with no dispatcher and no method-name argument.

That permission check is the one that would have been easy to skip. The boundary
tools end in `doc.save(ignore_permissions=True)` — correct for them, because the
MCP transport authorised three layers earlier — so a wrapper that trusted the
framework would have handed every signed-in account a write to every parcel.

**The `allow_<tool>` switches are deliberately not consulted**, the same call
`api/__init__.py` made for the phone. Those switches are the model's leash;
`allow_set_parcel_boundary` off means "the model may not redraw the farm", and
reading it here would mean an operator who distrusts the AI also loses the
ability to trace a parcel by hand.

### The draw tool does not go round anything

Parcel, Field and Irrigation Zone get a Leaflet.draw toolbar — polygon, rectangle,
edit, delete — and a **Save Boundary** button. Nothing is written until it is
pressed, and what it presses is the same boundary tool the AI calls: the polygon
is parsed, a self-intersection is refused, the enclosed area is compared against
the recorded acreage and a disagreement past a quarter is **refused outright**,
containment against the shape above is reported, and every derived field is
recomputed. A vertex nudged by accident gets an area disagreement on screen, not
a quiet save.

Two shapes drawn on one record become a `MultiPolygon` rather than a refusal — a
parcel cut in half by a county road is two pieces of one parcel.

**No area is computed in the browser.** Leaflet.draw offers a live acreage readout
while you drag; taking it would put a second area implementation in the app, in a
different language, and the day it disagreed with `geo.area_acres` by three per
cent nobody would know which figure the compliance record was built on. The
server answers with the area it actually stored.

The other four map forms — Housing Unit, Asset Register, Farm Shift, Farm Task —
stay exactly as read-only as they were. None of them carries a shape anybody
should be redrawing from a form.

### Satellite is the default layer

Esri World Imagery (free, no key) with OpenStreetMap one click away in the layer
control. A street map cannot be traced against: an orchard block's corner is a
change in canopy, a headland or a road edge, none of which is on a street map,
which for most of this county draws two roads and a lot of white. Both
attributions are the condition of use rather than a courtesy.

### The county import, on Parcel and no other form

Wasco County publishes its tax lots as an ArcGIS FeatureServer — free, no key,
WGS84 on request (`outSR=4326`; the layer's native grid is Oregon Stateplane North
in feet, WKID 2913). That polygon is what the assessor, the deed and the tax bill
are all describing, which makes it a far better starting point than tracing an
outline off a satellite image by eye.

Two ways to ask: type a tax lot number (`2N11E35BA-01600`), or press **Find Under
a Point** and click the parcel on the satellite map. Either way the result is
**drawn on the map first**, dashed, next to the block the operator already knows —
because a tax lot number typed with one character wrong returns a real parcel
somewhere else and every number on it looks plausible. **Apply** is what commits
it, through `save_boundary` like everything else.

**The request is proxied by the server, never made by the browser.** CORS is not
ours to promise, the URL belongs in one place rather than in a cached JavaScript
file, and the `where` clause is a query language that the browser is the wrong
place to be careful about. The tax lot is checked against an **allowlist** —
letters, digits, dots and hyphens — rather than escaped, because escaping means
implementing somebody else's SQL dialect correctly without being able to test it.

`x` is longitude and `y` is latitude in an ArcGIS point geometry, which is the
opposite order from every other pair in this app. Swapping them asks about a point
in the Southern Ocean, which comes back **empty rather than wrong** — so nothing
would ever say what happened. There is a test that reads the outgoing parameters.

**An ArcGIS error is an HTTP 200.** The service answers `{"error": {...}}` with a
200 and a JSON content type, so a client that checked only the status code would
report a malformed query as "the county has never heard of your parcel". It is
checked by name, first.

### What the import fills in, and what it refuses to

An empty field being filled is not an overwrite; a field that already has a value
is left exactly where it is and the difference is reported.

| Form field | From the county |
| --- | --- |
| `parcel_id` | `MapTaxlot` — filled if blank |
| `acreage` | `CalculatedAcres` — filled if blank |
| `county` | the service's own label, trimmed to "Wasco" |
| `title_holder` | **never**. `Taxpayer` is free text off a tax roll; `title_holder` is a Link to a Related Party on this site, and matching one to the other by string is how a parcel ends up owned by the wrong entity in an accounting system. It is shown and left to a person. |

Both acreages are always reported side by side — the county's, computed on its own
projected grid, and this app's, computed spherically from the same polygon. They
agree to a fraction of a per cent when the import is right, and a reader who can
see both can tell a projection difference from the wrong parcel.

### Degradation

`requests` is imported defensively, like shapely, h3 and segno before it: a bench
without it loses the county lookup **by name**, with the pip command, rather than
failing to import the module and taking `save_boundary` down with it. Drawing a
boundary by hand needs no network at all. Leaflet, Leaflet.draw and both
stylesheets are still fetched from a CDN when a form that needs them is opened —
there is no `app_include_js` or `app_include_css`, so the draw plugin is not
fetched at all on the four read-only map forms.

---

## v0.34.0 — tax form generators

Three releases put the arithmetic in place: federal withholding (v0.28.0), the
Oregon and Washington engines (v0.29.0), salary structures and the payroll engine
(v0.30.0). These turn a year or a quarter of it into the six forms an
agricultural employer in those two states actually files.

| Form | Scope | Agency | Due |
| --- | --- | --- | --- |
| `W-2` | one employee, one calendar year | IRS | 31 January |
| `1099-NEC` | one contractor, one calendar year | IRS | 31 January |
| `941` | one company, one quarter | IRS | 30 Apr / 31 Jul / 31 Oct / **31 Jan** |
| `OR-WR` | one company, one year | Oregon DOR | 31 January |
| `OQ` | one company, one quarter | Oregon DOR / OED | as 941 |
| `WA-ESD` | one company, one quarter | WA Employment Security | as 941 |

**Nothing here files anything.** No transmission, no official scannable Copy A,
no deposit schedule. What comes out is box and line values with the inputs that
made them; a person reads them onto the real form or into the agency's portal.

### The generators are pure

`erpnext_mcp/form_generators.py` reads no database and has no side effects, on
the same contract as `payroll_calc.py`. A W-2 can be computed from a fixture and
checked against a number somebody worked out on paper — which is the only way an
arithmetic claim about somebody's wages is worth making.

### `warnings` is the part to read first

Every form returns one, and it is where the form says which of its numbers is a
floor rather than a figure. Three things a generator cannot work out for itself:

* **Which state a dollar was earned in.** A slip carries one `work_state`, but a
  cross-state pay period splits gross between two. The slip's `state_wages` is
  that allocation where the caller has it; without it the whole gross lands on
  `work_state`. Only a slip that genuinely ran two state engines raises the flag
   — a single-state slip has nothing to get wrong.
* **Year-to-date wages before the first slip in hand.** The Social Security wage
  base and Washington's UI taxable wage base are annual per-employee caps, and a
  quarterly form cannot see the quarters before it. Pass
  `ytd_wages_by_employee`; without it the cap applies to the quarter alone,
  which is exactly right for Q1 and an overstatement after it.
* **Additional Medicare, separately.** A slip's `medicare` is the ordinary 1.45%
  and the 0.9% surcharge added together, because that is what comes out of a
  paycheck. Line 5d needs the surcharge alone, and where the two were never
  stored apart the line is zero and says so.

### The wage bases are consumed per employee

Two workers at $100,000 each are **$200,000** of Social Security wages on a 941,
not $176,100. An annual per-person ceiling applied to a company's grand total
would let one high earner's headroom absorb everybody else's excess — a mistake
that produces a plausible number and an assessment letter.

### What is stored is what was computed, then

`form_data_json` is written once at generation and read back verbatim. A filed
form is a statement about what an employer told an agency, on a date; payroll
gets corrected afterwards, and a `get_tax_form` that recomputed from today's
data would quietly return a different W-2 than the one in the envelope.

### `list_tax_forms`

**READ (default ON).** Forms filtered by `form_type`, `fiscal_year`, `quarter`,
`employee`, `company` and `status`. Returns `by_status` alongside the rows, which
is the answer to "what is still outstanding for this quarter".

| Parameter | Required | Description |
|---|---|---|
| `company` | | Company name or abbreviation |
| `form_type` | | `W-2`, `1099-NEC`, `941`, `OR-WR`, `OQ` or `WA-ESD` |
| `fiscal_year` | | Calendar year as `YYYY` (`year` is an alias) |
| `quarter` | | `Q1`–`Q4` |
| `employee` | | Only the forms for one employee |
| `status` | | `Draft`, `Generated`, `Filed` or `Amended` |
| `limit` | | Default 100, hard maximum 500 |

### `get_tax_form`

**READ (default ON).** One form in full, including every computed box and line
value **as it was calculated at generation time**. `form_data.warnings` first.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Tax Form docname (`tax_form` is an alias) |

### `generate_tax_form`

**MUTATING (default OFF).** Computes a form from the payroll already in the
system and records it as a Tax Form in `Generated` status.

Only **`Calculated` and `Submitted`** payroll entries are counted — a `Draft`
payroll has not been paid and a `Cancelled` one was not.

| Parameter | Required | Description |
|---|---|---|
| `form_type` | yes | `W-2`, `1099-NEC`, `941`, `OR-WR`, `OQ` or `WA-ESD` |
| `fiscal_year` | yes | Calendar year as `YYYY` (`year` is an alias) |
| `company` | | Required on a multi-company site |
| `quarter` | | Required for `941`, `OQ` and `WA-ESD`; refused on the annual forms |
| `employee` | | Required for a `W-2` |
| `related_party` | | Required for a `1099-NEC` |
| `company_address` | | The employer address to print — ERPNext does not store one on Company |
| `state_ids` | | `{"OR": "1234567-8"}`, overriding the State Tax Configuration |
| `ui_rate` | | The state's assigned unemployment-insurance rate, as a percent |
| `deposits` | | Form 941 line 13 — total federal deposits for the quarter |
| `ytd_wages_by_employee` | | `{"HR-EMP-00001": 42000}` — prior-period wages, for the wage bases |
| `oq_reported` | | `OR-WR` only: what was actually filed on each OQ, the thing it reconciles against |
| `notes` | | Stored on the form |

**A quarter on an annual form is refused rather than ignored.** Ignoring it would
produce a year's figures under a quarter's label, which is the one way this tool
could be wrong and look right.

**A second form for the same period and recipient is refused** while the first is
not `Amended`, and the error names the existing docname. The guard is per
recipient, so two employees get their own W-2s and each quarter gets its own 941.

### `regenerate_tax_form`

**MUTATING (default OFF).** Recomputes an existing form from current payroll —
after a slip was corrected, a rate changed, or a missing shift was added. Returns
`changes`: which values moved, `was`, `now` and `delta`.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Tax Form docname (`tax_form` is an alias) |
| `allow_filed` | | Recompute even though the form is `Filed` |
| everything `generate_tax_form` takes bar the identifying ones | | Re-supplied per run |

**A `Filed` form is refused unless `allow_filed` is passed**, because recomputing
one replaces the record of what was actually sent to the agency. An `Amended`
form is refused outright — regenerate its successor.

### `mark_tax_form_filed`

**MUTATING (default OFF).** Records that a form was filed: status to `Filed`, the
filing date, and whatever the agency gave back.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Tax Form docname (`tax_form` is an alias) |
| `filed_date` | | `YYYY-MM-DD`. Defaults to today |
| `confirmation_number` | | An EFTPS trace number, a Frances Online confirmation, an ESD receipt |
| `notes` | | Stored on the form |

**This transmits nothing.** Filing the same form twice is refused: it would
overwrite the date and confirmation number of the filing that actually happened.

### Four digits, never nine

Every form prints `XXX-XX-1234`, read off the I-9 — the one record on this site
that legitimately holds any of a Social Security number, and even there it stores
four digits. The person filing completes the rest from the paper. Same judgement
`generate_1099_prefill` made in v0.11.0, for the same reason: nine stored digits
would trade a real breach risk for a saved minute.

### One field on an existing doctype

`State Tax Configuration` gains `employer_account_number` — Oregon's Business
Identification Number, Washington's ESD account number. It is printed in W-2 box
15 and at the head of every state return. Without it those print blank, and the
alternative — an argument the model must be told each time — is worse than a
field an operator sets once per state.

---

## v0.36.0 — tax form PDF rendering

v0.34.0 computed the boxes. These two draw them: a letter-size portrait page per
form, in the official box and line numbering, attached privately to the Tax Form
record's `generated_pdf` field. Six layouts — **W-2** (Copy B), **1099-NEC**
(Copy B), **Form 941**, **OR-WR**, **OQ** and the **Washington ESD quarterly
report**.

**Nothing is recomputed.** The page is a rendering of `form_data_json` exactly as
it was calculated at generation time, so it cannot disagree with the record it
claims to render — which is the whole reason v0.34.0 stored the values instead of
recomputing them on read. Rendering moves no status and changes no figure.

**Every page is a working copy and says so twice**: a header note on every page,
and a footer block naming the agency and the channel the form is really filed
through. Copy A of a W-2 or a 1099 is red-ink scannable stock or an electronic
filing (BSO, IRIS/FIRE); 941 goes on the official form with deposits through
EFTPS; OR-WR through Revenue Online, OQ through Frances Online, the ESD report
through EAMS. These pages are for the farmer's records, for review before filing,
and for keying into the portal that does the filing.

**The generator's `warnings` print in full** at the foot of every form, under
*Before this form is filed*. They are the only place a form says which of its
figures is a floor rather than a figure, and a page that dropped them would look
more certain than the arithmetic behind it.

### `render_tax_form_pdf`

**MUTATING (default OFF).** Renders one Tax Form and attaches the PDF.

| Parameter | Required | Description |
|---|---|---|
| `name` | yes | Tax Form docname (`tax_form` is an alias) |
| `overwrite` | | Render even though `generated_pdf` is set, repointing the field |
| `company_address` | | The employer address to print, where the stored form data has none |

**A form that already has a PDF is refused** unless `overwrite` is passed: that
field may hold the copy somebody reviewed, or the one the agency issued, which
nothing here can reproduce. Overwriting repoints the field and **leaves the
earlier File attached** — the record gains and never loses.

**A form with no computed values is refused** rather than drawn. A page of zeroes
from an empty record is indistinguishable from a page of real zeroes.

### `bulk_render_tax_form_pdfs`

**MUTATING (default OFF).** Renders a set — every W-2 for a tax year, every 941
for a company.

| Parameter | Required | Description |
|---|---|---|
| `names` | | Explicit Tax Form docnames, instead of filters |
| `company`, `form_type`, `fiscal_year`, `quarter`, `status`, `employee` | | The same filters `list_tax_forms` takes |
| `overwrite` | | Render forms that already have a PDF |
| `company_address` | | The employer address to print |
| `limit` | | Default 100, hard maximum 500 |

**At least one selector is required.** Rendering every form on the site because
nobody said which is not a default this offers.

**A form that already has a PDF is skipped and counted, not refused** — one
rendered form should not stop a batch of ninety. A form that fails to render is
recorded by name with its reason and the run continues, so `rendered`, `skipped`
and `failed` between them account for every form matched.

**A selection larger than `limit` is refused, not truncated.** A bulk render that
silently stopped short would look like it had covered everything.

### One optional dependency

`reportlab` draws the pages. It is a declared dependency and a normal install has
it, but it is imported defensively like `shapely`, `h3` and `segno` before it: a
bench without it loses exactly these two tools — which say so by name, with the
pip command to fix it — and nothing else. Every generator and every other tax
form tool works without it, because the numbers are the deliverable and the page
is a convenience.

---

## v0.35.0 — payroll off the shift register

v0.30.0 built a payroll engine and v0.19.3 built a shift register. The join
between them was a stub: it returned the **crew's** whole span for every worker,
zero overtime and zero piece units, so every figure past "how long was the
shift" had to be keyed in by hand. This release is the join.

`erpnext_mcp/payroll_integration.py` is pure — no database reads, no side
effects — on the same contract as `payroll_calc.py` and `form_generators.py`.

### The unit is a segment, not a shift

A shift is crew-shaped and payroll is person-shaped. Every crew row already
carries its own `joined_at` and `left_at`, so what gets aggregated is one
person's own stretch of one shift. The crew worked 06:00 to 15:00 **and** Ana
joined at 07:10 and left at 13:00; the one payroll reads is Ana's.

### Overtime is weekly, and walked in time order

Oregon HB 4002 and Washington SB 5172 both put agricultural overtime at 40 hours
in a **workweek**, fully phased, at 1.5x. A biweekly period is two workweeks:

| | hours | overtime |
| --- | --- | --- |
| Week 1 | 45 | **5** |
| Week 2 | 35 | 0 |
| Period | 80 | **5**, not 0 |

Weeks are anchored at `pay_period_start` unless `workweek_anchor` says
otherwise. Walking each week chronologically rather than allocating pro rata is
what decides **which state** the overtime happened in: four ten-hour Oregon days
plus a Washington Friday is eight hours of Washington overtime.

### Two kinds of break

| | inside `total_hours`? | counts toward 40? |
| --- | --- | --- |
| `break_hours` — paid rest | yes | yes |
| `unpaid_break_hours` — meal | no, subtracted | no |

Paid rest is handed to the engine separately so the piece-rate path pays it at
the average hourly the period earned (WAC 296-131-020). Five nine-hour shifts
with a half-hour lunch each is 42.5 hours, so 2.5 of overtime rather than five.

### Minimum wage is per state, and reported rather than remedied

Washington's $16.66 and Oregon's $14.70 are different floors, and a single check
against the period total would let a compliant Washington week paper over an
Oregon week that was not. `total_shortfall` prices what it would cost to bring
each state's hours up to its floor — and nothing adds it to gross. A payroll
engine that quietly topped pay up would hide the fact that a piece rate is set
too low to be lawful.

### Piece units, and saying where they did not come from

Bucket Log Entry, a count column on Farm Task Assignment, and a count column a
site has added to the crew row are all looked for and all **summed** rather than
preferred. A bucket log row with no count column is **one bucket** — the row is
the record of the bucket. Piece work matched to no shift is still paid, carries
no hours, and is counted in `piece_rows_without_a_shift`.

Every absent source is named in `sources.notes`, because a piece-rate run that
found nothing has produced zeros, and whether that means nobody picked or means
the bridge is not installed is the difference between a payroll and a mistake.

### `get_employee_timesheet_summary`

**READ (default ON).** One employee's hours in a date range: their own spans, the
weekly overtime breakdown, the state split, breaks by kind, piece units, and
every shift behind the total. **No money** — which is why it needs none of the
payroll switches. "Why is my cheque this?" is answered by the timesheet.

| Parameter | Required | Description |
|---|---|---|
| `employee` | yes | Docname or employee name (`name`, `employee_name` are aliases) |
| `start_date` | yes | `YYYY-MM-DD` (`pay_period_start` is an alias) |
| `end_date` | yes | `YYYY-MM-DD` (`pay_period_end` is an alias) |
| `company` | | Scopes the shift read |
| `overtime_threshold` | | Hours in a workweek before the premium. Default 40 |
| `workweek_anchor` | | First day of the declared workweek |

### `preview_payroll_for_period`

**READ (default ON).** A whole company's period computed and **not written** —
the same arithmetic through the same code path as the run. Read the three lists
before the totals: `employees_missing_structures`, `totals.below_minimum_wage`
and `totals.with_open_shifts`.

| Parameter | Required | Description |
|---|---|---|
| `company` | | Company name or abbreviation |
| `pay_period_start` | yes | `YYYY-MM-DD` |
| `pay_period_end` | yes | `YYYY-MM-DD` |
| `pay_frequency` | | `Weekly`, `Biweekly`, `Semimonthly`, `Monthly`. Default `Biweekly` |
| `employee` | | Limit the run to one person |
| `include_unworked` | | Keep structures with no shift on the run. Default true |
| `overtime_threshold` | | Default 40 |
| `workweek_anchor` | | First day of the declared workweek |
| `detail` | | Add the per-shift timesheet and the tax working. Large; off by default |

### `run_payroll_for_period`

**MUTATING (default OFF).** The identical calculation, stored as a Farm Payroll
Entry in **Calculated** status. Submitting stays `submit_payroll`, behind its own
switch: arithmetic anybody can redo and a statement about what the farm is paying
are two different acts.

| Parameter | Required | Description |
|---|---|---|
| `company` | | Company name or abbreviation |
| `pay_period_start` | yes | `YYYY-MM-DD` |
| `pay_period_end` | yes | `YYYY-MM-DD` |
| `pay_frequency` | | Default `Biweekly` |
| `employee` | | Limit the run to one person |
| `include_unworked` | | Default true |
| `overtime_threshold` | | Default 40 |
| `workweek_anchor` | | First day of the declared workweek |

**A run with problems in it is not refused.** A worker below minimum wage, a
shift nobody ended, a picker with no salary structure — all reported, none of
them a reason to hold up everybody else's pay. The one refusal is a run where
*nobody* can be paid, and it names them.

### What the v0.30.0 tools got out of it

`preview_payroll` and `calculate_payroll` read through the same aggregation, so
the single-employee preview now reports the same hours, the same overtime and
the same piece units as the company-wide run. `_load_shifts` also carried a real
bug — the same filter key twice in a Frappe dict, where only the second
survives, so the start-date bound was silently discarded — and it is fixed.

Year-to-date carries across periods too: the Social Security wage base is an
annual per-person cap, and a period run that could not see the ones before it
would restart it.

---

## v0.40.0 — payroll into the general ledger

Four tools. v0.30.0 computed payroll, v0.35.0 fed it the shift register's hours
and v0.36.0 drew the tax forms — and a completed run produced Farm Payroll Slips
and **no Journal Entries**. Wages are the largest number on a farm's income
statement and they were the one number somebody keyed into the ledger by hand,
off a report, every fortnight.

### No account name is shipped, and that is the design

The mapping from a payroll component to a general ledger account is a **record**,
per company: `Farm Payroll Account Mapping`, one row per component. A shipped
default would be right on the chart of accounts it was written against and
quietly wrong everywhere else — and "quietly wrong" in a chart of accounts means
a year of wages in an expense line nobody notices until the tax preparer asks.

### The eleven components

Six are **employee-side**, and together they are the two sides of gross pay, so
all six are required whatever the amounts are:

| Component | Side | What it is |
|---|---|---|
| `Gross Pay` | debit | Total earnings — the wage expense itself |
| `Federal Tax` | credit | Federal income tax withheld |
| `SS Employee` | credit | The worker's 6.2% share |
| `Medicare Employee` | credit | The worker's 1.45% plus Additional Medicare |
| `State Tax` | credit | Everything a state withholds from the worker |
| `Net Pay` | credit | What is actually paid — clearing, bank or cash |

Five are **employer-side**. Each is an expense *and* a liability, so each takes
both accounts, and each is required only where the run has an amount for it:

| Component | Sides | What it is |
|---|---|---|
| `SS Employer` | debit + credit | The farm's matching 6.2% |
| `Medicare Employer` | debit + credit | The farm's matching 1.45% |
| `FUTA` | debit + credit | Federal unemployment, first $7,000 per worker |
| `SUTA` | debit + credit | State unemployment at this employer's own rate |
| `State Employer Other` | debit + credit | Paid Leave Oregon employer share, OR workers' comp, WA PFML employer share, WA L&I |

They stay five rather than one because they are remitted to five different
places on five different schedules — the 941, the 940, the quarterly state
return, and two more besides. A farm that genuinely wants them in one account
can point five components at one account, which is a decision it made rather
than one this app made for it.

`State Employer Other` is the component the release specification did not name.
It exists because the state engines compute employer amounts that are **not**
unemployment insurance, and a mapping with nowhere to put them would drop real
money out of the books quietly.

### `get_payroll_account_mapping`

**READ (default ON).** Which accounts a company's payroll posts to, which
components are still unmapped, and what each one is for.

| Parameter | Required | Description |
|---|---|---|
| `company` | | Company name or abbreviation |

### `preview_payroll_gl`

**READ (default ON).** Every line of every entry the run would produce, both
totals and the balance check, with nothing written. **It refuses nothing** — an
incomplete mapping, a run already posted, a slip that does not balance are all
reported in `blockers` with `would_post: false`, because an unpostable run is
exactly what somebody calls a preview to find out about.

| Parameter | Required | Description |
|---|---|---|
| `payroll_entry` | yes | The Farm Payroll Entry docname (`name` is an alias) |
| `mode` | | `consolidated` (default) or `per_employee` |
| `posting_date` | | `YYYY-MM-DD`. Defaults to the run's pay period end |
| `cost_center` | | Set on every line. Defaults to the mapping's |
| `include_employer` | | Default true. False books the wage half only |

### `configure_payroll_accounts`

**MUTATING (default OFF).** Sets the mapping. Rows **merge** into what is there
unless `replace=true`, so a mapping is built up a few accounts at a time.

| Parameter | Required | Description |
|---|---|---|
| `company` | | Company name or abbreviation |
| `components` | yes | List of `{component, debit_account, credit_account, notes}` (`accounts`, `mapping` are aliases) |
| `replace` | | Default false. True discards every row not in this call |
| `default_posting_mode` | | `Consolidated` (default) or `Per Employee` |
| `cost_center` | | Set on every payroll line |
| `is_active` | | Default true on creation |
| `notes` | | Who decided this mapping and against what |

**Group accounts are refused here**, not at posting time. A mapping is written
once and posted from every fortnight afterwards, so a group account stored now
is a payroll refused in six weeks by somebody who did not configure it.

### `post_payroll_to_gl`

**MUTATING (default OFF).** Turns a Farm Payroll Entry into **DRAFT** Journal
Entries and stops. Submitting stays `submit_journal_entry`, behind its own
switch, exactly as it does for `create_journal_entry`.

| Parameter | Required | Description |
|---|---|---|
| `payroll_entry` | yes | The Farm Payroll Entry docname |
| `mode` | | `consolidated` (default) or `per_employee` |
| `posting_date` | | Defaults to the run's pay period end |
| `cost_center` | | Defaults to the mapping's |
| `include_employer` | | Default true |

**Five refusals, all reported at once** rather than one per round trip: a
payroll entry that is not Calculated or Submitted, a company with no account
mapping, a mapping with a hole in it, a run that already has live journal
entries against it, and an entry that does not balance.

**The idempotency check is about the ledger, not the link table.** Every draft
is linked back onto the run's `gl_postings`, and a run with live entries against
it is refused by name. A run whose drafts were *deleted*, or whose entries were
*cancelled*, can be posted again — because then there is genuinely nothing in
the books.

### What the payroll engine got out of it

Employer taxes have been computed since v0.28.0 and stored nowhere: the slip
carried the worker's deductions and none of what the farm owed on top. Farm
Payroll Slip now holds `social_security_employer`, `medicare_employer`, `futa`,
`state_unemployment`, `state_employer_other` and `total_employer_taxes`, and
`calculate_full_payroll` returns them plus `total_cost_of_employment`. No total
moved — these are the same figures the engines already returned, lifted to where
they can be kept.

**Slips written before v0.40.0 carry zeros**, so posting such a run books the
wages and leaves the employer's taxes off the ledger. That is reported as a
warning rather than inferred from four zeros: an employer with no employer taxes
is a real thing, and an entry that quietly left them out is not.

**State unemployment is new and defaults to zero.** `State Tax Configuration`
gains `suta_rate` and `suta_wage_base`, both employer-entered for the same
reason workers' compensation is — a SUTA rate is assigned to one employer by one
agency out of that employer's own experience rating, and there is no table
anybody could ship. A site that enters no rate computes exactly what it computed
before this release. The wage base is consumed by year-to-date gross, the way
FUTA's $7,000 is; a base of zero means no cap.

---

# Adding a tool

Everything a tool needs is in two places:

1. A handler in the right module under `erpnext_mcp/tools/` — `read`, `mutate`,
   `workflow`, `accounts`, `banking`, `dimensions`, `fiscal`, `governance`,
   `assets`, `notes`, `opening`, `reports`, `files`, `collab`, `hr`, `trade`,
   `meta`, `packets`, `realestate`, `parties`, `investment_report`, `tax`,
   `company`, `farm`, `housing`, `compliance`, `evidence`, `calendar`,
   `auditpacket`, `dispatch`, `inspections`, `mobile`, `funnel`, `training`,
   `shifts`, `heat`, `kpi`, `kpidefs`, `payroll`, `payroll_gl`, `visits`,
   `sessions`, `rules`,
   `asset_tags`, `feeds` or `fieldwork` —
   returning a `ToolResult(data, summary, docstatus_delta="")`. A new *compliance
   packet type* is not a new tool: it is one file in `erpnext_mcp/packets/`, and
   `docs/development.md` has the recipe.
2. An entry in `TOOLS` in `erpnext_mcp/registry.py`. If the tool needs something
   not every site has, give it an `available` predicate and a `requires`
   sentence; a tool that is advertised and always fails is worse than one that
   is absent.

Then add an `allow_<tool_name>` Check field to the ERPNext MCP Settings doctype —
default `"1"` for a read tool, `"0"` for a mutating one. The standalone test
`ShippedDefaults.test_every_tool_has_a_switch` fails if you forget, because a tool
with no switch is one an operator cannot turn off.

Read the switch, the audit row, the rollback-on-failure and the never-raise
contract come from `registry.dispatch`; a handler gets all four for free and
cannot opt out.
