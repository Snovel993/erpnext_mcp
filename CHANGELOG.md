# Changelog

All notable changes to this project are documented here. Versions follow
[semantic versioning](https://semver.org).

## 0.7.0 — 2026-07-27

Family-office governance and asset accounting. Fifteen tools and six doctypes,
so the things a farm holds for a generation — who owns it, what happened to their
interest, which paper says so, and what the equipment is worth — live in the
ledger rather than in somebody's filing cabinet.

v0.6.0 made the axes a posting is filed under reachable. This release builds on
top of them: members are an anonymous accounting dimension, cost centers are
value-chain segments, and the register that maps one to a legal name is a
doctype of its own.

### The idea the whole release rests on

**The ledger stays anonymous and the register carries the names.** A chart of
accounts and a cost center tree are read by everyone who touches the books — a
bookkeeper, a lender, an auditor, a model summarising the year. A family name in
either one leaks into every export, and cannot be taken out of a statement that
has already been sent. So a posting is tagged with a Member accounting dimension
value (`Member-01`), and exactly one doctype says who that is.

Anyone who needs the mapping can be given read access to one doctype. Nobody
needs it to read the ledger. `list_cap_table` is the tool that de-anonymises the
site, and it has its own switch for that reason.

### Added — the member register

**`Cap Table Entry`** (new doctype). One row per member per company: the
anonymous id, the legal entity name, entity type, admission date, withdrawal
date, ownership percentage, an optional member cost center for sites whose
convention uses one, and notes. The docname is `"<member id> - <company abbr>"`,
the same shape ERPNext gives an Account, so the register can be found by the
identifier every posting already carries.

**`create_cap_table_entry`** (mutating, default OFF). Refuses a second entry for
the same member in the same company; refuses a percentage outside 0–100; and —
the check worth knowing about — refuses a member id that is not already a value
of the site's Member accounting dimension, naming `create_dimension_value` as
the remedy. The cap table names a member the ledger can already refer to, so the
dimension value comes first. A site with no Member dimension yet is allowed and
told so.

Cannot create a member already retired. Ownership that does not total 100% is a
warning, not a refusal: mid-transition is a real state, and a tool that refused
it would be refusing the truth.

**`update_cap_table_entry`** (mutating, default OFF). Cannot retire a member —
that is `close_cap_table_entry`, so an exit reaches the event trail rather than
appearing only as a changed checkbox. Cannot change the `member_id`: it is the
key every posting is tagged with, and changing it here would leave journal entry
lines pointing at a member that no longer exists.

**`list_cap_table`** (read-only, on by default). Retired members are **included**
by default. The postings they are tagged on do not disappear when they leave, so
neither should the row that explains them. The response totals active ownership
and says whether it comes to 100%.

**`close_cap_table_entry`** (mutating, default OFF). Sets the withdrawal date,
marks the entry retired, and writes a Withdrawal event carrying the narrative.

Deliberately **moves no money**. A member leaving usually involves a final
distribution, and that is a separate `record_member_event` call with its own
amount, accounts and narrative — bundling them would make the tool that closes a
member also a tool that can pay one.

### Added — the event trail

**`Member Event`** (new doctype). Contribution, Distribution, Admission,
Withdrawal, Transfer or Reallocation, with an effective date, an amount, the
member (and counterparty, for a transfer), the Journal Entry that books it where
there is one, a `superseded_by` link for corrections, and a **mandatory
narrative**.

The narrative is mandatory for the same reason `cancel_journal_entry` demands a
reason. A Journal Entry survives on its own; the reason for it does not. "Why
did Member-02 take 40,000 in March 2031" is the question that gets asked once
the people who knew have gone.

**`record_member_event`** (mutating, default OFF). Writes the event, and — for
the five types that book money — a **DRAFT** Journal Entry:

- Contribution: debit the cash side, credit member capital.
- Distribution / Withdrawal: debit member distributions, credit the cash side.
- Transfer / Reallocation: debit the capital of `member`, credit the capital of
  `counterparty_member`. Money never leaves the company.

**Every line carries the member dimension, including the cash side.** Tagging
only the equity line makes a balance sheet filtered by member fail to balance,
and the first person to notice that is usually an auditor.

**Accounts are shortlisted, never guessed.** With no `capital_account` given,
the company's leaf Equity accounts are matched by name; zero matches or more
than one is refused with the candidates listed. Picking the first would post a
member's capital to whichever account happened to sort first, and nobody would
find out until they read an equity statement.

Refuses without a Member dimension on `Journal Entry Account`, because an
untagged equity entry is one nobody can attribute later.

**`submit_member_event`** (mutating, default OFF). Posts the draft the event is
waiting on — and **checks two switches**. Its own, and `submit_journal_entry`'s.
That second switch is where an operator decided whether an AI client may move a
balance at all; a second door into the same room with a different lock would
make the decision meaningless.

**`list_member_events`** (read-only, on by default). Filter by member, type and
date range. Legal names are resolved from the register; the events themselves
hold only the anonymous id.

### Added — the governance archive

**`Governance Document`** (new doctype). Operating agreements, trust documents,
advisory agreements, board resolutions, prior statements and amendments, with
effective and execution dates, parties, notes, and an amendment chain.

**The chain is the point.** An operating agreement amended three times is four
documents, and the question asked in 2050 is "which one was in force in 2031".
Naming `supersedes` writes the link in both directions, so a reader can follow
the chain forward to whatever is current. The controller refuses a cycle by
walking the whole chain rather than checking one hop, and
`attach_governance_document` refuses superseding a document that has already
been superseded — an amendment goes on the end of the chain, not into the
middle.

**`attach_governance_document`** (mutating, default OFF). `file_content` is
base64 of the document's bytes, stored as a **private** File on the record;
`file_url` records where an externally hosted document lives instead. Refuses a
second document with the same company, category and title, because two entries
claiming to be the same operating agreement is worse than none.

**`list_governance_documents`** and **`get_governance_document_content`**
(read-only, on by default). Content goes through the same path
`get_attachment_content` uses, so the same read-permission check on the parent
document and the same size cap apply. A governing document is exactly the kind
of file those checks exist for.

### Added — assets, cost splits and note-tenor discipline

ERPNext already has an Asset doctype, an Asset Category and a depreciation
schedule. It does not have the two things an orchard needs.

**A cost split.** A tractor is not a Harvest asset or a Perennial Care asset; it
is 40% one and 60% the other, and its depreciation should land that way every
period without anyone re-deciding it. ERPNext files an asset under one cost
center.

**Note-tenor discipline.** When an asset is financed, the month the note is paid
off and the month the asset is fully depreciated should be the same month.
Nothing in ERPNext enforces that, and the divergence is invisible until the last
year of the loan, when interest is still being paid on something with no book
value left.

**`Asset Cost Profile`** (new doctype, with the child tables `Asset Cost Center
Allocation` and `Asset Depreciation Posting`). One profile per Asset, holding
the allocation, the schedule, the linked note and every period already written.

*A sidecar rather than custom fields, deliberately.* All of this could have been
ten custom fields and two child tables bolted onto ERPNext's Asset. The app
manifest promises that installing this app changes the behaviour of nothing
already on the site and that uninstalling it gives the site back; grafting
fields onto ERPNext's own Asset would break both halves. An asset created here
is an ordinary ERPNext Asset an operator can open, edit and delete without ever
knowing this app exists.

**`create_asset`** (mutating, default OFF). Writes the Asset (a draft), the
profile, and a fixed-asset Item when the `item_code` does not exist yet.

**`calculate_depreciation` is set to 0 on the asset, and that is the most
important line in the feature.** ERPNext runs a daily scheduled job that posts
depreciation for every asset with that flag set, using its own schedule and its
own single cost center. If it also ran here, the asset would depreciate twice —
silently, monthly, in the background. So this app owns the schedule outright,
and there is a test that reads the flag off the stored Asset for the day
somebody removes the line.

The note tenor is enforced **before anything is written**: an asset whose life
disagrees with its note is refused with both numbers, rather than created and
then found to be wrong.

Also refuses an allocation that does not total 100 (a 99% asset
under-depreciates the business for the rest of its life), a group or disabled
cost center, a frequency that does not divide the useful life exactly, a salvage
value at or above the cost, and an existing Item that is not flagged as a fixed
asset — flipping that flag on an item with stock movements is an inventory
decision, not an asset one.

**`update_asset_allocation`** (mutating, default OFF). Replaces the split. **Not
retroactive**, and that is correct: depreciation already written keeps the split
it was written with, because that is the history, and rewriting it would change
periods already reported.

**`link_asset_to_note`** (mutating, default OFF). Ties an asset to its note and,
by default, refuses the link unless life and remaining tenor agree. The tenor
comes from `note_tenor_months`, from `note_maturity_date`, or from the note
document's own maturity or term field where its doctype has one — and the
response says which. `enforce_tenor=false` links anyway and records the
divergence.

**`run_depreciation_cycle`** (mutating, default OFF). One DRAFT Journal Entry
per asset per period: debit depreciation expense split across the cost centers,
credit accumulated depreciation in one line, each debit optionally carrying a
BBCH Stage dimension value.

- **`dry_run` defaults to TRUE**, like `import_chart_of_accounts`. This is the
  one tool here that writes to many documents at once, and a catch-up over a
  year of missed periods is a page of journal entries somebody should read
  first.
- **Idempotent by record.** Every period written is stored on the profile with
  the entry that carries it, so a second run cannot repeat one. Amounts are
  computed from the profile each time rather than read back from saved rows, so
  a catch-up produces exactly what month-by-month running would have.
- **The split adds up.** The last debit absorbs the rounding, so 33.33 / 33.33 /
  33.34 of 1000 is three debits totalling exactly 1000. A journal entry that does
  not balance is not a rounding problem, it is a refused save.
- **The last period lands on the salvage value to the cent**, for declining
  balance as well as straight line. Written Down Value with a salvage value of 0
  is refused rather than fudged: the rate `1 - (salvage/cost)^(1/n)` is
  undefined, because a declining balance never reaches nought.
- One misconfigured asset does not take the run down. Assets on the Manual
  method, assets with nothing due, and assets whose depreciation accounts are
  not configured are skipped and listed with the reason.

**`depreciation_note_alignment_check`** (read-only, on by default). For every
financed asset: months elapsed, months of depreciation left, months of note
left, the delta, and a sentence saying which way it reads. Reports on every
financed asset rather than only the broken ones, because "nothing is wrong" is
an answer somebody has to be able to see.

### Changed

- `mutate.py` grew two public functions, `insert_draft_journal_entry` and
  `validated_journal_lines` (previously private). Every Journal Entry this app
  writes — from `create_journal_entry`, from a member event, from a depreciation
  run — now goes through the same insert and the same never-submitted
  assertion. A second implementation elsewhere would have been a second chance
  to ship one that posts.
- `before_uninstall` now lists every doctype whose contents go with the app, with
  a row count and an export command for each. The governance three are there for
  a reason the audit log is not: they are the **only** copy. An MCP Action Log
  row records something that also happened somewhere else; a Cap Table Entry is
  the only mapping from a member id to a legal name.

### Notes

- Fifteen new kill switches, ten of them default OFF. The five read tools ship
  on, `list_cap_table` included — an operator who wants the register unreadable
  through MCP should untick that one deliberately.
- 118 new standalone tests (894 in total), plus 13 in-bench tests covering what
  only a real site can show: that the six doctypes migrate, that the controllers'
  refusals fire from the Desk path, that a real File round-trips through Frappe's
  storage, and that ERPNext accepts both the Asset and the depreciation entry.

## 0.6.0 — 2026-07-27

Cost centers and accounting dimensions. Six tools, so the *other* axes a posting
is filed under can be built through the MCP rather than by hand in the Desk.
v0.5.0 made the chart of accounts reachable — what kind of money a transaction
is. This release makes the rest of the classification reachable: which part of
the business it belongs to, whatever else the operator needs to slice by, and
which accounts a document reaches for when nothing on it says.

### Added

**`list_cost_centers`** (read-only, on by default). One company's cost centers as
a nested tree, in the same shape `get_chart_of_accounts` returns. Disabled cost
centers are left out and *counted*, in `disabled_count_excluded`, so "the tree
looks short" always has an answer rather than being a silent omission.

**`create_cost_center`** (mutating, default OFF). One cost center under an
existing group. Refuses before writing if the parent is missing, is a leaf, or
belongs to another company, or if the number is taken in that company.

Cannot casually add a root. ERPNext gives every company exactly one root cost
center and requires it to be named exactly after the company
(`CostCenter.validate_mandatory`), so omitting `parent_cost_center` on a company
that already has one is refused with the existing root named — which is nearly
always what a caller who forgot the parent needs to see. A company with no cost
centers at all can still be given its root.

**`update_cost_center`** (mutating, default OFF). Rename, renumber,
disable/enable. The docname moves with the fields, in that order, for the reason
set out at the top of `tools/accounts.py`: a Cost Center's key encodes two of its
own fields and is built once by `autoname`, so changing one without the other
leaves the tree showing one thing and reporting another, permanently.

Hand-rolled rather than delegated, unlike `update_account`, and that is a
decision rather than an omission. ERPNext's own helper
(`accounts.utils.update_number_field`) handles only the *number*, and the
compensating behaviour that makes delegation matter for Account — syncing a
rename down into child companies — has no cost-center equivalent to reproduce.
The naming rule is identical to Account's, and an in-bench test asserts that a
real insert produces exactly what this app predicts.

Deliberately cannot reparent, and this release ships no `move_cost_center`:
reparenting moves no posting but changes which subtotal every existing one rolls
up into, retroactively, for periods already reported. Also refuses to rename the
company's root. Disabling deletes nothing and says so — the response carries the
GL entry count, and, for a group, that its children were **not** disabled.

**`create_accounting_dimension`** (mutating, default OFF). The one to read the
description of before enabling.

An ERPNext Accounting Dimension does not hold its own values: it **points at a
DocType**, and every record of that DocType is a value. So this tool writes up to
three things, in one transaction so a failure leaves none of them — the master
DocType (only when asked for, via `create_master_if_missing`), the Accounting
Dimension record, and one Link Custom Field per target doctype.

- **A generated master is a custom DocType** (`custom: 1`): it lives entirely in
  the database, writes no files into an app and needs no developer mode, and an
  operator can delete it from the Desk. It is named `field:dimension_value`, so
  the record's own name *is* the value and `Member-01` reads as `Member-01`
  everywhere it is linked rather than as `MEM-00001`.
- **The custom fields are written here rather than left to ERPNext.** Inserting
  an Accounting Dimension makes ERPNext enqueue its own field-creation routine as
  a *background job* over its own fixed hook list. Both halves are wrong for an
  MCP caller: the next call is usually a Journal Entry that needs the field to
  exist now, and the caller asked for a specific set of doctypes. ERPNext's job
  still runs and still creates the rest of its list; both paths check for an
  existing field first, so they do not collide.
- **"Journal Entry" means the line.** ERPNext carries dimensions on `Journal
  Entry Account`, never on the header, because one entry books to several. Asking
  for `"Journal Entry"` wires up the child table and the response reports the
  redirection in `redirected`, rather than putting a field on a header that
  nothing would ever read.

Refuses a dimension that already exists for that label or that DocType (ERPNext
allows one per DocType — its values *are* that DocType's records), a master that
is a Single, a child table or a core doctype, a target doctype this site does not
have, and any target that already has a field of that name which is not a Link to
this master. Every one of those is checked before anything is written: a
half-wired dimension is worse than none, because it looks configured.

**`create_dimension_value`** (mutating, default OFF). One record in the DocType a
dimension points at. Finds the dimension by its label, by its DocType or by its
docname — three ways because the Accounting Dimension record's own docname is a
version detail, and a caller who created it through this app knows it by the
label it asked for. `extra_fields` is applied verbatim, with every key checked
against the master's own fields; an unknown one is a typo and is refused by name.

**`set_company_defaults`** (mutating, default OFF, idempotent). Points a
Company's default account and cost center fields at real accounts, in one call:
receivable, payable, cash, bank, income, expense, COGS, round-off (account and
cost center), exchange gain/loss, write-off, and deferred revenue/expense.

**Type-checked, not merely existence-checked**, and that is the whole point.
ERPNext keys party ledgers and every ageing report off `account_type` rather than
off an account's name or number, so a `default_receivable_account` pointed at a
plain Asset account produces invoices that post but never age — and the symptom
appears a quarter later with nothing to point at. Each field also has to match
the right root type. Group accounts, disabled accounts, accounts belonging to
another company and group cost centers are all refused, as is a key this ERPNext
version's Company does not have.

Nothing is written unless *every* value in the request validates, so a
partially-correct call leaves the company exactly as it was. And every field is
compared before it is written, so a re-run changes nothing and says so — which
matters more than usual because `Company.save` is not a cheap write.

### Changed

**`create_journal_entry` accepts a per-line `dimensions` object.** Custom
accounting dimensions go in `{"member": "Member-01", "bbch_stage": "BBCH-8"}` on
the line, not alongside `debit` and `cost_center`.

The separate door is deliberate. A dimension's fieldname is invented by whoever
created it, so there is no list this app could ship; but simply accepting unknown
per-line keys would turn `amount` — which a model will send, meaning `debit` —
from a corrected mistake into a silently dropped one. Unknown top-level keys stay
refused by name; passing a key through `dimensions` is an assertion that the
caller meant a dimension.

Both halves are then checked against the site itself: the field has to exist on
`Journal Entry Account`, and a Link value has to be a record of what it links to.
Without the first, a dimension nobody created yet would be written to an
attribute that never reaches a column and the entry would look filed and not be.
Without the second, ERPNext's own link validation runs on *submit*, so a bad
value would produce a draft that cannot be posted rather than a call that failed.
The response reports `dimension_fields_set`.

**`args.resolve_cost_center`** joins `resolve_account`: a cost center can be
named by its docname, its number or its name, anywhere one is taken. Unlike the
account resolver it checks that `cost_center_number` exists on the site before
filtering on it — account numbers predate every ERPNext this app supports, cost
center numbers do not, and selecting a missing column is a hard SQL error rather
than an empty result.

**`compat.field_meta`** returns a field's definition rather than only whether it
exists, which is what lets the dimension paths check a value against the DocType
a Link actually points at.

### Notes

Six new switches on the settings form — `list_cost_centers` on by default,
`create_cost_center`, `update_cost_center`, `create_accounting_dimension`,
`create_dimension_value` and `set_company_defaults` off — seeded by the existing
`after_migrate` hook, so no bespoke patch. `create_accounting_dimension` is the
only switch in this app that can add a DocType to a site, and only when a call
asks for it explicitly; it is the narrowest one to leave off.

The catalogue is now 49 tools: 32 read-only, 17 mutating.

The standalone test double gained real schema mutation to cover this: inserting
a DocType makes it creatable, and inserting a Custom Field makes
`frappe.get_meta` report the field, with the schema reset between tests. Without
that, the case the whole feature exists for — create a dimension, create a value,
put it on a journal entry line, read it back off the stored document — could not
have been written at all.

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

The one shipped template is **`us_llc_farm`** — 81 accounts (17 groups, 64
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
- **`1830 Brokerage Cash & Money Market` ships as an empty group**, to be filled
  with one child per linked brokerage cash-services account. Which accounts
  exist is a property of the install rather than of the template, and a single
  combined ledger would leave a paired-brokerage feed no way to say which
  account a movement belongs to.

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
