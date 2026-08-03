# Development

## Running the tests

Two suites, deliberately. They are not alternatives.

### Standalone — logic, no bench

```bash
python3 -m unittest discover -s tests_standalone -t .
```

Runs in a fraction of a second. No bench, no MariaDB, no redis, no site, and no
dependencies beyond the standard library (Python 3.10+). Use it while you work.

It installs an in-memory `frappe` into `sys.modules` before the app is imported —
see `tests_standalone/harness.py`. That double provides a small document store
with real filter semantics (including `like`, `between`, `in` and the aggregate
form `sum(debit) as debit`), the lifecycle hooks the app relies on (`validate`,
`before_save`, `on_update`, `submit`, `cancel`), and doctype meta **loaded from
this app's own shipped DocType JSON** — so a test asserting that read tools default
on is asserting it against the file that ships, not a copy that can drift.

The tests worth having most are the ones about refusal: does a disabled tool stay
invisible, does a bad token get an opaque 401, does an unbalanced Journal Entry get
rejected before anything is written, does a failed mutation still leave an audit
row. All of those are answerable from this app's own logic. Requiring a bench to
run them would mean running them rarely, which for refusal tests is the same as not
having them.

Two properties of the double are worth knowing, because both have already caught
real bugs:

- **Singles store strings.** `tabSingles.value` is a text column, so the fixture
  writes Check defaults as `"0"` and `"1"` — the strings, not integers. `bool("0")`
  is `True` in Python, so reading a switch with a bare `bool()` reports every
  disabled tool as *enabled*, master switch included. `settings.as_bool` is what
  stops that, and the fixture is what would catch its removal.
- **Password fields are extracted on save.** `Store._extract_passwords` moves them
  into a separate table and leaves asterisks in the row, as Frappe does. That is
  what makes "the token is never returned to a caller" a thing the tests can check
  rather than take on trust.
- **Frameworkless tables refuse a default ORDER BY.** `tabSingles` and friends
  have no `modified` column, so `FakeDB` raises the real MariaDB error when a
  query would order by it implicitly. This is the double catching up with a bug
  that reached production in v0.2.0: it had answered the query happily, which is
  why three `seed_defaults` tests passed against code that could not migrate.

- **Account names itself, and refuses to save a root.** `harness.AccountDocument`
  reproduces ERPNext's `autoname` (`"<number> - <name> - <abbr>"`), the "Root
  cannot be edited" throw, and the parent-must-be-a-group check. Without those
  the double would name accounts `A-00001` and happily save roots, which would
  make every rename, every root refusal and every parent link in
  `tools/accounts.py` untestable — and those are the whole of what the module
  does. `account_autoname` in the harness is deliberately a *second*
  implementation of the naming rule rather than an import of the app's
  `charts.account_docname`: two independent copies of a rule that must match
  ERPNext is how a test notices one of them drifting.
- **…and it runs Frappe's mandatory pass on a root.** ERPNext's Account marks
  `parent_account` required, so creating a top-level account raises
  `MandatoryError: [Account, …]: parent_account` unless the insert sets
  `flags.ignore_mandatory`. The double used to create roots quite happily, which
  is exactly why the standalone suite was green against an
  `import_chart_of_accounts` that could not create one on a live site (v0.8.0).
  It now raises, and eleven tests go red without the fix.

The pattern in all of them: when the double is *more permissive* than the
framework, tests pass and sites break. Where a real constraint is cheap to
model, model it.

Reaching for a framework function the double does not implement raises an
`AttributeError` naming it, rather than quietly returning `None`. If you hit that,
add the function to the harness deliberately — or reconsider whether the app should
depend on it.

### In-bench — the framework contract

```bash
bench --site yoursite.localhost run-tests --app erpnext_mcp

# or one module
bench --site yoursite.localhost run-tests --module erpnext_mcp.tests.test_integration
```

Covers what is only true of a real site:

- The DocType JSON migrates, and every switch in `registry.TOOLS` became a real
  `Check` column.
- `install-app` seeded the declared defaults into `tabSingles`.
- A Password field survives a round trip through Frappe's encryption, and the
  stored ciphertext is not the plaintext.
- The endpoint answers over a genuine WSGI request (built with
  `werkzeug.test.EnvironBuilder`), with real status codes.
- ERPNext's own Journal Entry validation accepts what `create_journal_entry`
  builds; a submitted entry really does write GL Entries; a cancellation really
  does attach its reason as a Comment.
- Frappe's permission rows really do keep a non-System-Manager out.
- `frappe.throw` in the settings controller really does abort the save.

These tests **skip rather than fail** when the site lacks the setup a case needs —
no Company, fewer than two postable accounts, no Fiscal Year — so `run-tests` is
meaningful on a bare site rather than only on a fully configured one. They
discover the site's existing accounts instead of creating a Company, because
creating one builds an entire chart of accounts and the whole point of this app is
that it works against whatever chart is already there.

**One isolation caveat.** Frappe wraps each test in a transaction and rolls it
back. The audit log deliberately breaks that for one case: when a tool fails,
`audit.record(..., commit=True)` commits the failure row so it outlives the
rollback — that is the point of it. Those rows therefore survive the test, so
`MCPIntegrationTestCase.setUp` snapshots the log and `tearDown` deletes whatever
appeared. A test that exercises a failure path needs no extra bookkeeping; one
that bypasses the base class does.

### Test layout

```
tests_standalone/
  harness.py              the frappe double + MCPTestCase
  fixtures.py             an invented two-company ERPNext site; SeededTestCase,
                          V2TestCase (adds workflow/report/file/HR data),
                          V7TestCase (adds equity accounts, the Member
                          dimension and asset masters), V8TestCase (adds
                          opening equity, notes payable and bank accounts)
                          and HRTestCase (adds the hrms app)
  test_transport.py       the three gates, HTTP shape, SSE, user context
  test_protocol.py        JSON-RPC, handshake, version negotiation, catalogue
  test_read_tools.py      the ten accounting read tools
  test_mutate_tools.py    the five accounting write tools + default-off posture
  test_workflow_tools.py  states, transitions, the worklist, advance_workflow
  test_reports.py         all three report engines and the permission path
  test_files.py           attachments and the permission checks on them
  test_collab.py          comments and ToDos
  test_hr.py              the HR tools, and their absence without hrms
  test_trade.py           orders and receivables ageing
  test_meta.py            custom fields and client scripts
  test_packets.py         the packet framework and both shipped types
  test_audit.py           what gets logged, rollback survival, immutability
  test_settings.py        shipped defaults, form validation, token generation,
                          and that the settings JS keeps no copy of the catalogue
  test_governance.py      the cap table, the event trail and the archive
  test_assets.py          cost splits, the schedule, and the tenor check
  test_banking.py         create_bank_account: root/account-type refusals, the
                          shared-GL-account warning, no orphan Bank on failure
  test_opening.py         the computed equity plug, the opening flags, and both
                          ways of failing to find the equity account
  test_notes.py           notes payable: the principal/interest split, the
                          running balance, the history, every disposition
  test_fiscal.py          fiscal years: the company-aware overlap check, the
                          one-year rule, and the orphaned-postings refusal
  test_packaging.py       that every shipped doctype folder is one Frappe can
                          import — the v0.7.0 bench-migrate regression

erpnext_mcp/
  tests/test_integration.py                       in-bench, v0.1.0 surface
  tests/test_v2_tools.py                          in-bench, v0.2.0 categories
  tests/test_workflow_scenarios.py                in-bench, a real Workflow on a
                                                  synthetic DocType
  tests/test_packets.py                           in-bench, packet shape and
                                                  integrity against real data
  tests/test_accounts.py                          in-bench, the chart tools
  tests/test_dimensions.py                        in-bench, cost centers and
                                                  accounting dimensions
  tests/test_governance.py                        in-bench, the six v0.7.0
                                                  doctypes, a real File
                                                  round-trip, and whether
                                                  ERPNext accepts the Asset and
                                                  the depreciation entry
  tests/test_notes.py                             in-bench, the two v0.8.0
                                                  doctypes, whether ERPNext
                                                  accepts an is_opening entry
                                                  and a Bank Account, whether a
                                                  new ROOT account can be
                                                  created at all, and that
                                                  ERPNext really does refuse a
                                                  posting outside every fiscal
                                                  year
  erpnext_mcp/doctype/erpnext_mcp_settings/test_erpnext_mcp_settings.py
  erpnext_mcp/doctype/mcp_action_log/test_mcp_action_log.py
```

`V2TestCase` and `HRTestCase` exist because the HR tools are gated on the `hrms`
app: a test that wants them has to say so, and one that wants to prove they are
*absent* has to not. `fixtures.install_hrms()` adds the app to the fake site's
installed list and registers a stand-in for `get_leave_balance_on`, because the
leave tool delegates to it and a test that skipped the delegation would not be
testing the tool that ships.

The fixture site has **two** companies on purpose. A single-company fixture would
let `resolve_company`'s inference hide every place a tool needs an explicit
company, and "works on my one-company site" is exactly the bug class a reusable app
must avoid. It also gives two accounts with the same `account_name` under different
docnames, which is what makes the ambiguity paths in `args.resolve_account`
testable.

---

## Setting up a dev bench

If you do not already have one:

```bash
# Prerequisites: python3.11+, node 18+, mariadb 10.6+, redis, wkhtmltopdf
pip install frappe-bench
bench init frappe-bench --frappe-branch version-15
cd frappe-bench

bench get-app --branch version-15 erpnext
bench new-site mcp-dev.localhost
bench --site mcp-dev.localhost install-app erpnext

# this app, from a local checkout
bench get-app /path/to/erpnext_mcp
bench --site mcp-dev.localhost install-app erpnext_mcp

bench use mcp-dev.localhost
bench start
```

Then open `/app/erpnext-mcp-settings`, generate a token, tick **Enabled**, and hit
the endpoint at `http://mcp-dev.localhost:8000/api/method/erpnext_mcp.mcp.handle`.

After editing a DocType JSON by hand:

```bash
bench --site mcp-dev.localhost migrate
bench --site mcp-dev.localhost clear-cache
```

After editing the settings form's JS, `bench build --app erpnext_mcp` (or just
reload in development, where assets are served unbundled).

---

## Layout of the app

```
erpnext_mcp/                     repo root
  pyproject.toml                 flit; no runtime deps
  README.md  LICENSE  license.txt
  docs/                          tool catalogue, security, this file
  tests_standalone/              the bench-free suite
  erpnext_mcp/                   the Python module
    hooks.py                     app manifest — almost empty, on purpose
    install.py                   after_install / after_migrate / before_uninstall
    patches.txt  patches/        one idempotent seeding patch
    mcp.py                       the HTTP transport: handle(), selftest()
    protocol.py                  JSON-RPC 2.0 + MCP handshake
    registry.py                  THE TOOL CATALOGUE + dispatch()
    security.py                  master switch, auth token, CIDR allowlist
    settings.py                  reads of ERPNext MCP Settings
    audit.py                     writes to MCP Action Log
    args.py                      argument coercion, company/account resolution
    compat.py                    version tolerance — ask the site, don't assume
    errors.py                    ToolError, AuthError
    result.py                    ToolResult
    tools/read.py                the ten accounting read tools
    tools/mutate.py              the five accounting write tools
    tools/accounts.py            chart of accounts: create/update/move/disable/delete/import
    tools/banking.py             the Bank + Bank Account records a feed writes into
    tools/fiscal.py              fiscal years — the calendar the ledger may post into
    tools/opening.py             opening balances, plugged to Opening Balance Equity
    tools/dimensions.py          cost centers, accounting dimensions, company defaults
    tools/governance.py          cap table, member events, governance archive
    tools/assets.py              asset cost splits, note tenor, depreciation runs
    tools/notes.py               notes payable: terms, payments, dispositions
    tools/workflow.py            workflow states, worklist, advance_workflow
    tools/reports.py             report discovery and execution
    tools/files.py               attachments (checks Frappe permissions)
    tools/collab.py              comments and ToDos
    tools/hr.py                  employees, attendance, leave (needs hrms)
    tools/trade.py               sales/purchase orders, receivables ageing
    tools/meta.py                custom fields, client scripts
    tools/packets.py             the two MCP tools over the packet framework
    tools/realestate.py          the parcel and lease registers
    tools/parties.py             the related-party governance register
    tools/investment_report.py   the quarterly report and its kairotic gate
    tools/tax.py                 the 1099-NEC pre-fill
    tools/artifacts.py           where a generated document goes: onto a record,
                                 and — confined — onto disk
    tools/uploads.py             chunked uploads, and the internal checkpoint
                                 door onto the same pipeline
    tools/compliance.py          the compliance-field installer and its map
    tools/evidence.py            Compliance Policy, Certification, Regulatory
                                 Filing, Audit Event
    tools/calendar.py            the compliance calendar, and the three ways
                                 something comes off it
    tools/auditpacket.py         assembling an audit packet and filing it
    tools/dispatch.py            Farm Task Dispatch: the pool, the board, the
                                 state machine, and the bridge from the calendar
    tools/inspections.py         the three records a completion produces —
                                 Housing Inspection, Detector Test, Water Test
    records.py                   what those three share: the findings branch,
                                 the forward-only register write-back, and how a
                                 laboratory result is read
    compliance_fields.py         WHICH compliance columns go on WHOSE doctype,
                                 and the argument for putting them there at all
    alerts/base.py               Rule, Observation, the idempotent sweep
    alerts/rules.py              the twelve rules, each with its kairotic gate
    audit_packets.py             the eight audit regimes and their sections
    dashboard.py                 the Compliance Command Center installer, the
                                 Farm Task Dispatch Kanban board and its
                                 workspace. Reads another app's Select options
                                 off the site rather than assuming them — see
                                 v0.16.1 for what assuming cost
    packets/base.py              PacketSpec, Flag, provenance envelope
    packets/reconciliation.py    one account, one period
    packets/fiscal_year_audit.py one company, one fiscal year
    render/pdf.py                a PDF writer: base-14 Courier, exact metrics
    render/xlsx.py               an XLSX writer: a zip of XML, inline strings
    render/docx.py               a DOCX writer, for output_format="docx"
    charts/base.py               ChartTemplate, tree validation, account-type rules
    charts/us_llc_farm.py        the one shipped chart template — pure data
    erpnext_mcp/doctype/         ERPNext MCP Settings, MCP Action Log,
                                 Cap Table Entry, Member Event,
                                 Governance Document, Asset Cost Profile
                                 (+ its two child tables), Note Payable
                                 (+ Note Payable Event), Parcel, Lease,
                                 Related Party, Family, Field, Irrigation Zone,
                                 Housing Unit, Housing Assignment, the two
                                 upload-staging doctypes, and the v0.15.0
                                 compliance five — Compliance Policy,
                                 Certification (+ Certification Renewal),
                                 Regulatory Filing, Audit Event (+ Audit
                                 Corrective Action), Compliance Alert
scripts/seed_related_parties.py  seeds the related-party register from a JSON
                                 file kept OUTSIDE this repository
```

**Read `registry.py` first.** It is the only file you edit to add a tool, and it is
where the switch check, the audit row, the rollback-on-failure and the never-raise
contract live.

### Where things must not go

- **No raw SQL.** Every write goes through `frappe.get_doc(...).insert()` /
  `.submit()` / `.cancel()` so doctype validation runs. The standalone harness
  raises on `frappe.db.sql` to keep it that way.
- **No hardcoded company, account or fiscal-year names.** Everything is discovered
  at call time. That is the difference between an app anyone can install and a
  script for one site.
- **No field selected without checking it exists.** Selecting a missing column is a
  hard SQL error. Use `compat.existing_fields()` / `compat.first_field()`.
- **No runtime dependency beyond Frappe/ERPNext.** That promise is what makes
  `bench get-app` safe on somebody else's bench, and it is why `render/` writes
  PDF, XLSX and DOCX with `zipfile` and byte offsets rather than importing a
  library. Frappe's own routes are conditional — `frappe.utils.pdf` shells out to
  a wkhtmltopdf **binary**, `xlsxutils` imports openpyxl — so either would mean a
  tool that works where it was written and fails where it was deployed.
- **No unconfined filesystem write.** A generated document's home is a private
  File attached to a record. `output_path` exists for a batch somebody has to
  print, and `tools/artifacts.py` confines it to the site's own `private/files`
  and `public/files`, checked on the **resolved** real path so a symlink cannot
  step outside.
- **Never query `tabSingles` with `frappe.db.get_value` / `get_values` /
  `get_all`.** They default to `order_by="modified"`, and `tabSingles` is not a
  DocType table — three columns, no framework columns — so the query dies with
  `Unknown column 'modified' in 'ORDER BY'`. Use `frappe.db.get_singles_dict`,
  which issues no ORDER BY at all. This shipped as v0.2.0 and broke `bench
  migrate` on a live site; the double now refuses the query, and a
  grep-as-a-test fails if it comes back.
- **Nothing that logs, echoes or returns the auth token.** Not in a tool result,
  not in `selftest`, not in an error message.

---

## Adding a compliance packet type

One file in `erpnext_mcp/packets/`, ending in `register(PacketSpec(...))`. The
package auto-discovers every module there at import time, so there is nothing
else to edit — no list, no handler, no MCP schema.

```python
# erpnext_mcp/packets/payroll.py
from .base import Flag, PacketResult, PacketSpec, SEVERITY_WARN, register


def build(filters: dict) -> PacketResult:
	flags = []
	...
	return PacketResult(data={...}, flags=flags, summary="...")


register(
	PacketSpec(
		packet_type="payroll_packet",
		title="Payroll packet",
		purpose="...",  # what it is for
		audience="...",  # who reads it
		build=build,
		filters={"pay_period": {"type": "string", "description": "..."}},
		required=("pay_period",),
		available=lambda: "hrms" in frappe.get_installed_apps(),
		requires="the Frappe HR (hrms) app",
	)
)
```

Then add `allow_payroll_packet` to the settings doctype JSON, default `"1"`, in
the Compliance Packets section — `ShippedDefaults.test_every_packet_type_has_a_switch`
fails if you forget.

Writing the builder, three rules:

- **Flag severity means something.** INFO is context, WARN is "a human should
  look", ERROR is "these numbers do not internally agree". Only use ERROR for an
  arithmetic or integrity failure *inside the packet*, because it sets
  `signable: false`.
- **Check the packet against itself.** The most valuable line in
  `reconciliation.py` is the one asserting `opening + net == closing` from two
  independent aggregates; the most valuable in `fiscal_year_audit.py` is the
  accounting identity. Find the redundancy in your data and check it.
- **Never truncate silently.** Use `cap(rows, flags, "collection_name")`.

## Adding a chart-of-accounts template

One file in `erpnext_mcp/charts/`, ending in `register(ChartTemplate(...))`. The
package auto-discovers every module there at import time — the same arrangement
as `packets/`, and for the same reason. There is no list to edit and no tool to
touch; `propose_clean_chart` picks it up by key.

```python
# erpnext_mcp/charts/us_s_corp.py
from .base import ChartTemplate, register

register(
	ChartTemplate(
		key="us_s_corp",
		title="US S corporation",
		entity_type="S Corporation",
		jurisdiction="US",
		summary="...",  # one paragraph, shown to the reviewer
		tree=[...],  # list of root nodes
		notes=("...",),  # caveats the author wants read first
	)
)
```

Four rules for the tree:

- **It is data, not a query.** Nothing in `charts/` may touch the database.
  That is what makes `propose_clean_chart` genuinely read-only and lets a
  template be reviewed and diffed like any other source file.
- **`root_type` on roots only.** It is inherited by every descendant, and
  `validate_tree` refuses a subtree that switches root type mid-way, because
  ERPNext would not accept one.
- **Roots must be groups**, and a node with `children` must be a group. ERPNext
  refuses a postable root and refuses children under a ledger.
- **Say what an account is for, in `description`,** wherever the name alone
  would let somebody use it wrongly. Stock ERPNext's Account has no description
  field, so `import_chart_of_accounts` writes the text as a comment on the
  created account (or into a `description` custom field where a site has one).
  The clearest example is `us_llc_farm`'s 2120, which only keeps its meaning as
  a live wage balance if nobody books a period-end accrual into it.

Run `tests_standalone/test_accounts.py::TemplateData` — it asserts every
template passes the importer's own validation, has unique numbers, and types
every leaf. The in-bench `test_accounts` additionally checks that every
`account_type` the template asks for resolves to one the operator's ERPNext
actually offers.

## Working on the Account doctype

Read this before touching `tools/accounts.py`.

**The docname encodes two fields and is never rebuilt.** ERPNext's
`Account.autoname` builds `"<number> - <name> - <abbr>"` at insert. Nothing
recomputes it on a later save. So:

| What you do | What you get |
| --- | --- |
| `frappe.rename_doc("Account", old, new)` | The key moves; `account_name` and `account_number` keep their old values. The chart shows one thing and reports another, permanently. |
| `frappe.db.set_value("Account", name, "account_name", ...)` | The fields move; the docname — and every link, report label and GL Entry reference — keeps the old text. |

Both halves are needed, in that order. ERPNext ships exactly that as
`erpnext.accounts.doctype.account.account.update_account_number`, which **also**
propagates the change into child companies in a group structure and returns the
new docname (or `None` when the name did not move). `update_account` delegates
to it; the hand-rolled two-step in `_rename_account` is a fallback for versions
that predate the helper and is not the path anyone should be extending.
Reimplementing the child-company sync here would mean getting it wrong on a
consolidated group, which is not a failure anybody notices quickly.

**A root account cannot be saved at all.** `Account.validate_root_details`
throws "Root cannot be edited" on any save of an account with no parent. This is
ERPNext's rule, not the app's, and it is why `update_account` refuses type and
disabled changes on a root, `move_account` and `disable_account` refuse roots
outright, and `create_account` cannot make one. Renaming a root still works,
because `update_account_number` goes through `db.set_value` and `rename_doc`
rather than `doc.save()`.

**`account_type` is version-dependent; `root_type` is not.** Never hardcode the
valid `account_type` list — `charts.site_account_types()` reads it off
`frappe.get_meta`. `ACCOUNT_TYPES_BY_ROOT` exists only to refuse a pairing that
is positively wrong (a Payable under Income); a type it has never heard of but
the site accepts is allowed through with a note. A validation table baked into
an app is a snapshot of one ERPNext version, and the failure mode of a stale
snapshot should be a note, not a locked door.

**Reparenting relies on NestedSet.** `Account` is a `NestedSet` with
`nsm_parent_field = "parent_account"`, so setting `parent_account` and calling
`doc.save()` is what fixes `lft`/`rgt`. Do not write those columns. The cycle
check in `move_account` deliberately walks the parent chain instead of comparing
them: the nested set is a cached traversal of the tree, and "almost always
correct" is not the standard for a check whose failure mode is an infinite loop
inside a rebuild.

## Adding a tool

1. **Write the handler** in `tools/read.py` or `tools/mutate.py`. Signature is
   `(args: dict) -> ToolResult`. Raise `ToolError` for anything the caller could
   fix; let genuine bugs propagate, and `dispatch` will log a traceback to the
   site's Error Log and return the exception type to the client.

   Write the `summary` for an operator scanning the audit log in a month, not for
   the model. Set `docstatus_delta` on anything that changes a document's state.

2. **Register it** in `TOOLS` in `registry.py`. If it needs something not every
   site has, give it an `available` predicate (`_app_installed("hrms")`,
   `_needs_doctype("Bank Statement")`) and a `requires` sentence — the tool will
   then be absent rather than broken on sites that lack it. The `description` is
   the entire basis on which a model decides to call it — say what it returns, what it is for,
   and for a mutating tool, what it *cannot* do. Spell out "Read-only." or
   "MUTATING (default OFF)." in the text as well as the annotations, because a
   client that ignores annotations still shows the model the description.

3. **Add the switch** to `erpnext_mcp/erpnext_mcp/doctype/erpnext_mcp_settings/erpnext_mcp_settings.json`
   as `allow_<tool_name>`, fieldtype `Check`, default `"1"` for a read tool and
   `"0"` for a mutating one, with a `description` an operator can act on. Add it to
   `field_order` too.

4. **Migrate**: `bench --site <site> migrate`. Existing installs get the field
   seeded by `install.after_migrate`, so no bespoke patch is needed.

5. **Test it.** `ShippedDefaults.test_every_tool_has_a_switch` already fails if you
   skip step 3, and `test_every_switch_has_a_tool` fails if you leave a switch
   behind after deleting a tool. Add coverage for the tool's own refusals.

6. **Document it** in `docs/tool-catalog.md`.

### If the tool is mutating

- Make the destructive verb its own tool with its own switch. `create` and `submit`
  are separate for a reason: the pair lets an operator grant "propose" without
  granting "post", which is what most of them actually want.
- Validate before writing, with a message the model can act on. "debits (1450.0) do
  not equal credits (1400.0); difference 50.0. Nothing was created." beats a
  Frappe traceback.
- Never auto-submit. If a future ERPNext hook were to submit on insert,
  `create_journal_entry`'s post-insert `docstatus` assertion turns that from a
  silent posting into a loud failure. Keep that pattern.

---

## House style

Frappe's, because that is what a reviewer of this app will be reading:

- **Tabs** for indentation in Python and JS. Spaces in JSON, TOML and Markdown.
  `.editorconfig` and `pyproject.toml` both say so.
- `# SPDX-License-Identifier: MIT` as the first line of every source file,
  including tests and the JS.
- Ruff for lint and format: `ruff check .` and `ruff format .`. Config is in
  `pyproject.toml`; line length 110.
- Module docstrings explain *why*, not what. If a decision would look arbitrary to
  somebody reading it cold — gating on the rightmost `X-Forwarded-For` hop, hash
  naming on the log doctype, casting `"0"` to False — the reason belongs next to the
  code.

## Compatibility policy

Supported: Frappe/ERPNext v14, v15, v16 (`develop`); Python 3.10+. Developed and
run in production against v15.115.0.

The rule is **ask the site, do not pin the version**. `compat.py` exists for this:
`has_field`, `first_field`, `existing_fields`, `doctype_exists`. When a new ERPNext
renames something, add a candidate to the relevant `first_field` call rather than
branching on a version number, and note it in the README's compatibility section.

A field this app cannot find is reported as absent in the response — so a model
sees "this install does not track that" instead of a traceback.

## Releasing

1. Bump `__version__` in `erpnext_mcp/__init__.py`. It is what `initialize`
   reports as `serverInfo.version`. v0.2.0 tagged without doing this and shipped
   a handshake claiming 0.1.0, so there is now a test asserting it matches the
   newest heading in `CHANGELOG.md`.
2. Update `CHANGELOG.md`.
3. Run both suites.
4. Tag: `git tag -a vX.Y.Z -m "vX.Y.Z" && git push --tags`.
