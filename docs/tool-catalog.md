# Tool catalogue

All 49 tools `erpnext_mcp` exposes, with arguments, return shape and a worked
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

All 30 are **on** by default and can be switched off individually. A tool that is
off does not appear in `tools/list` at all, and neither does one whose site
prerequisite is missing.

The first ten are the accounting surface v0.1.0 shipped; the rest were added in
v0.2.0 and are grouped by what they touch.

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

The two tools in this app that check Frappe permissions on the way in. A File is
whatever somebody uploaded — a signed contract, a passport scan, a payroll export
— and `is_private` is a promise the framework makes about who can see it.

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

# Adding a tool

Everything a tool needs is in two places:

1. A handler in the right module under `erpnext_mcp/tools/` — `read`, `mutate`,
   `workflow`, `accounts`, `dimensions`, `reports`, `files`, `collab`, `hr`,
   `trade`, `meta` or `packets` —
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
