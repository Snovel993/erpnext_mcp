# Tool catalogue

Every tool `erpnext_mcp` exposes, with arguments, return shape and a worked
example. The authoritative definitions live in `erpnext_mcp/registry.py`; this
document explains them.

All examples use the site `erp.example.com`, the company `Example Trading Co`
(abbreviation `ETC`) and a textbook chart of accounts. Nothing here is real.

## Conventions that apply to every tool

**Calling.** One `POST` per call, JSON-RPC 2.0:

```bash
curl -sS -X POST https://erp.example.com/api/method/erpnext_mcp.mcp.handle \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
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

All ten are **on** by default and can be switched off individually. A tool that is
off does not appear in `tools/list` at all.

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

# Mutating tools

**All five are OFF on a fresh install** and stay off until an operator ticks the
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

## 11. `create_journal_entry`

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
  "next_step": "This is a draft and affects no balance. Submit it in ERPNext, or via submit_journal_entry if that tool is enabled."
}
```

Unbalanced input, on the other hand:

```
debits (1450.0) do not equal credits (1400.0); difference 50.0. Nothing was created.
```

---

## 12. `submit_journal_entry`

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

## 13. `cancel_journal_entry`

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

## 14. `create_bank_transaction`

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

## 15. `reconcile_bank_transaction`

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

# Adding a tool

Everything a tool needs is in two places:

1. A handler in `erpnext_mcp/tools/read.py` or `erpnext_mcp/tools/mutate.py`
   returning a `ToolResult(data, summary, docstatus_delta="")`.
2. An entry in `TOOLS` in `erpnext_mcp/registry.py`.

Then add an `allow_<tool_name>` Check field to the ERPNext MCP Settings doctype —
default `"1"` for a read tool, `"0"` for a mutating one. The standalone test
`ShippedDefaults.test_every_tool_has_a_switch` fails if you forget, because a tool
with no switch is one an operator cannot turn off.

Read the switch, the audit row, the rollback-on-failure and the never-raise
contract come from `registry.dispatch`; a handler gets all four for free and
cannot opt out.
