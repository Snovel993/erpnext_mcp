# Changelog

All notable changes to this project are documented here. Versions follow
[semantic versioning](https://semver.org).

## 0.12.1 — 2026-07-30

A hotfix. `bench migrate` on v0.12.0 aborted in
`erpnext_mcp.patches.register_custom_party_types` with a
`LinkValidationError`, and the standalone suite passed the whole way — which is
the more important half of this release.

### What actually broke, which is not what the traceback looks like

The error reads `Could not find Party Type: Family` and looks like a
self-referential link. It is not. ERPNext's `Party Type` names itself
`field:party_type`, and **that field is a `Link` to `DocType`** — so a Party
Type's name has to be the name of a real DocType on the site. There was no
DocType called `Family`, so the insert was refused.

The loop registers party types in sorted order, so `Contact` went in first and
**succeeded** — because Frappe ships a core `Contact` DocType — and `Family`
failed immediately after. That asymmetry is the whole diagnosis: the two party
types were not equivalent, and nothing in the release knew it.

It goes deeper than the patch. A Journal Entry line carries `party_type` (a
`Link` to `DocType`) and `party` (a **`Dynamic Link`** resolved through it). So
bypassing the validation with `db_insert()` or `flags.ignore_links` — the
obvious fixes — would have registered a party type that the first posting using
it would then reject. That is worse than the crash: a crash at migrate time is
found by the person running the migrate, and a party type that silently cannot
be posted to is found by whoever is closing the books.

### The fix

**This app now ships a `Family` DocType.** A small register — name,
relationship, an optional link to the related-party entry, an active flag. It
holds no tax id on purpose: a transfer below the IRS annual gift exclusion is not
compensation for services, which is the whole reason the party type is separate
from Supplier. A relative genuinely paid for work is a Contact or a Supplier, and
the posting should be reclassified rather than the exclusion widened.

`Contact` needs nothing: Frappe's own Contact DocType is the register, which is
the correct answer and was already working.

**`ensure_party_types()` checks the target DocType before inserting, and never
raises.** It returns `{"created": [...], "existing": [...], "skipped": {name:
why}}`, and the patch prints the skips. A party type that cannot be registered is
worth saying out loud on the console; it is not worth aborting a migration over —
in v0.12.0 it took down the whole bench's migrate, and because `after_migrate`
never ran, that release's new tool switches were never seeded either. The
operator got a traceback *and* a half-configured app.

The two skip reasons are deliberately different sentences. "Ours, not migrated
yet" is a retry; "nothing on this site ships that DocType" is a dead end.

### The test double had no link validation, and that is why this shipped

The same shape as v0.12.0's `bool("0")` bug: a double that answers a question the
real framework refuses is a double that certifies code which cannot run.

`harness.py` now implements `Document._validate_links` on insert and save, for
`Link`, `Dynamic Link`, and the `Link`-to-`"DocType"` case that caused this. It
walks child rows too, because on a Journal Entry the party fields are on the
**line**, not the header — validating only the header would have left the entire
party mechanism unchecked. The ERPNext fields the app depends on are now modelled
with their real fieldtypes rather than as Data (`ERPNEXT_FIELD_LINKS`), and the
fixture seeds the Family and Contact records its GL rows point at, because a
fixture with postings and no people describes a site that cannot exist.

`test_patches.py` asserts the double genuinely reproduces the production failure
before asserting anything else — otherwise every test under it is theatre.

### Added

- **`Family` DocType.** Required for the `Family` party type to resolve. Named
  `field:family_member_name`, so a posting reads `party = "Alex Bramwell"`.
- **`tests_standalone/test_patches.py`.** Every patch run against an empty store:
  survives, is a no-op the second time, and survives with its target DocType
  missing. Plus a schema audit that every `Link` this app declares points at a
  DocType something ships, every `Dynamic Link` resolves through a field on its
  own doctype, and every party type resolves to a real DocType.
- End-to-end coverage that a Family posting and a Contact posting go through, and
  that a party who is not on the register is refused — which v0.12.0 claimed and
  could not do.

### Fixed

- `register_custom_party_types` no longer aborts `bench migrate`.
- `ensure_party_types()` returns a report instead of a list, and skips rather
  than raising. `install.after_install` / `after_migrate` and the patch all use
  the same path.
- `register_party_types` (the MCP tool) reports `skipped` with the reason and
  `resolves_to_doctype` for each party type, so a client can see the rule rather
  than infer it.
- `list_companies` reports `party_types.resolves_to_doctype`.
- The uninstall warning names the Family register — deleting it orphans every
  journal entry that named those people.

### Notes

- No new tools. The catalogue is unchanged at 121.
- `Family` is a generic DocType name to take, the same caveat as `Field` in
  v0.12.0. It is not optional: the party type cannot resolve without it.
- **Nothing needs re-running by hand.** The next `bench migrate` finds the Family
  DocType synced in `post_model_sync` before the patch executes, registers both
  party types, and `after_migrate` seeds the switches v0.12.0's abort skipped.
- Full suite: 1982 tests, 0 failures. 73 skip without shapely and h3.

## 0.12.0 — 2026-07-30

Three features in one release, because they share a backbone. A field sits on a
parcel; a cabin sits on the same parcel; and both of them belong to a company
that, until this release, this app could read but not create. Shipping them
separately would have meant two releases that each pointed at something the next
one adds.

Twenty-nine tools, four DocTypes, two Party Types, one new field on `Parcel`,
and the app's first two runtime dependencies — `shapely` and `h3`, for field
boundaries, both imported defensively so a bench without them loses five tools
by name rather than failing to load the other hundred and sixteen.

### Multi-Company — `create_company`, `update_company`, `list_companies`

**Every other tool took a company and none of them could make one.** For an
operation whose structure is a holding company, an operating company and a
trust, "add the opco" is not an administrative afterthought — it is the step
everything else waits on, and it meant leaving the model and clicking through the
Desk.

`create_company` hands ERPNext a correct set of arguments and then reports what
it **actually** built, which is not always what was asked for: an account count
of zero means the named chart of accounts does not exist on this site, and the
result says so rather than looking like a success. It also creates the fiscal
year containing today for the start month given — April for a farm year, January
for a calendar one, named for the span it covers rather than for one of the two
years it straddles.

**`update_company` refuses three things and says why each one.** The
abbreviation and the company name, because both are baked into the docname of
every account, cost center, parcel and lease on the books — changing either is a
migration, not an edit. The currency, but only once something is posted: every
one of those entries was measured in the old one, and relabelling it would
restate the whole ledger without touching a single number. A company with no
postings can still have its currency corrected, because the rule is about the
ledger rather than about the field. And the fiscal year start month once any
fiscal year exists, because a year that changes shape mid-cycle produces two
periods claiming the same days and no way to say which one a posting belongs to —
a short year created deliberately with `create_fiscal_year` is how that is done.

`list_companies` reports the GL entry count with the first and last posting
dates, which is how a caller tells a live company from a shell before it tries
anything.

### Two custom Party Types — `Family` and `Contact`

ERPNext ships Customer, Supplier, Employee and Shareholder. A family operation
pays two kinds of people that fit none of them, and recording them as Suppliers
is wrong in two different directions.

**`Family`** is a relative receiving money that is neither payroll nor a
purchase. `generate_1099_prefill` now reads those postings and **excludes** them,
reporting the count, the total and the names — so "nobody looked" and "somebody
looked and excluded them" are different-looking answers, and so a Family posting
that was really a payment for work is visible enough to be reclassified. A
transfer below the IRS annual gift exclusion is not compensation for services: it
needs no W-9 and produces no form. Without this party type those payments end up
recorded as Supplier payments, which puts family money into vendor spend **and**
onto a 1099 the recipient owes no tax on.

**`Contact`** is the consultant who looks at the orchard twice a year, the
neighbour who runs a tractor for a weekend — not a formal Supplier, but paid for
services, which is exactly the shape a 1099 exists for. The pre-fill now reads
those postings too and classifies them **borderline**, naming the W-9, rather
than leaving them unclassified where it has nothing to go on.

Both are seeded on install and on every `bench migrate`, and both are idempotent.
Registering a Party Type changes nothing already recorded: existing rules and
Journal Entries using Shareholder, Employee or Supplier keep working exactly as
they did.

### Field and Irrigation Zone — the structure under a parcel

**This app owns structure; the field apps own events.** A spray, a pick, a water
set and a soil test all happen to a *block*, and every one of them is recorded by
a different system. What none of those systems can be is the place the block
itself is defined, because a block outlives the app that last recorded something
against it — and because a cost centre, a lease and an appraisal all need to
point at the same ground.

**The docname is suffixed with the parcel, at every level.** A field is
`"Yellow Camp Block 3 - MC"` and a zone is `"YC3-Zone2 - MC"` — not
`"YC3-Zone2 - YC3"`, because a zone name already carries its block and repeating
it says the same thing twice while dropping the ground. That needs a short key
per parcel, so `Parcel` gains an `abbr` field. An operator who types one gets
theirs and a collision is refused; one who does not gets initials, and a
*derived* collision is disambiguated rather than refused, because nobody chose
that key. Parcels registered before this release carry no stored abbreviation
until something saves them, and nothing reads the field without falling back to
the same deterministic derivation — so there is no data patch.

**Two arithmetic refusals, both contradictions rather than opinions.** Blocks
summing to more acres than their parcel; zones summing to more area than their
block. Both are the failure a bad import produces every time, and both name both
figures and the excess, because the useful next question is which of the two is
wrong. Blocks summing to *less* than the parcel is left alone: roads, ditches,
headlands and the house are all real, and a controller that complained about that
would complain about every real farm.

**The variety autosuggest comes from the ground.** `list_fields` reports the
varieties already planted on the site. A hardcoded list would be wrong the first
time somebody puts a new one in the ground; what is already there cannot be.

`import_farm_app_fields` is the schema-alignment foundation, not the sync: it
creates Fields carrying each legacy record's Farm App id so a later engine has
something to match on. Dry run by default, the whole batch validated before the
first insert — a half-imported farm is worse than an unimported one, because the
second run has to work out which half — and a block already registered is skipped
with the reason, so the same batch re-runs safely.

#### Boundaries, and the geofence they make possible

Both doctypes now carry a GeoJSON polygon, and `set_field_boundary` /
`set_zone_boundary` derive everything indexable from it: centroid, bounding box,
H3 coverage at resolutions 6-10, and the area the shape actually encloses. None
of those can be set directly — a figure a caller could edit independently of the
polygon is a figure that will disagree with it, and the disagreement surfaces as
a geofence saying no to somebody standing in the right place.

**THE H3 FILL STORES EVERY CELL THE SHAPE TOUCHES, and that is the single most
consequential line in the release.** H3's default polygon fill keeps cells whose
*centre* is inside the shape. An orchard block is smaller than one H3 cell at
resolutions 6, 7 and 8 — so the default returns an **empty set** for a real
field, and a spatial index built on it answers "in no field" for a point plainly
in one. A false negative that reads like a policy decision is exactly what a
geofence must not produce, so the fill uses `contain="overlap"`, which is a true
superset. There is a test asserting no stored resolution is ever empty, because
that empty set is what the obvious implementation silently returns.

For the same reason `find_fields_containing_point` narrows with the **bounding
box** rather than with the H3 cells — a bbox is a guaranteed superset of the
shape it bounds, so a candidate set built from it cannot miss the right answer —
and then tests every candidate exactly. The boundary counts as inside: a pick
recorded on the headland is in the block, and a geofence that excludes its own
edge tells the picker they are nowhere. The result also reports how many blocks
have **no** boundary, because on a half-mapped farm an empty answer means "not
inside any *mapped* block" rather than "not on the farm".

**Area is spherical and says so.** `shapely` computes area in the units of its
coordinates, and these are degrees — so `.area` is degrees squared, which is not
an area of anything. The computed acreage uses the standard spherical-excess
integral; a test checks it against a rectangle whose true size is worked out by
hand, and the two agree to 0.2%. A polygon more than 25% from the recorded
acreage is refused because one of the two figures is then about a different piece
of ground; 5-25% is reported and both figures are kept, since a deed, a GIS trace
and a tape measure routinely disagree.

**Zone containment is reported, never enforced.** A shared water line crosses a
boundary, a pump house sits on the headland, a mainline runs down an easement.
`boundary_contained_in_field` comes back true, false, or **null** when the block
has no boundary to check against — "we could not check" and "we checked and it is
outside" being different answers that a report must not conflate.

`import_field_boundary_geojson` migrates a farm's existing polygons in one go,
and is deliberately the OPPOSITE of `import_farm_app_fields`: per-feature errors
rather than whole-batch refusal, because it only sets a field on records that
already exist. One bad feature in forty is a bad feature, not a reason to refuse
the other thirty-nine. It never creates a Field.

The satellite fields on `Field` — provider, asset reference, last pull date, NDVI
mean and standard deviation — are schema only; nothing fetches imagery in this
release. NDVI is stored on its real range of **-1 to 1** rather than 0 to 1:
water and bare soil read negative, and clamping the floor to zero would make a
flooded block indistinguishable from an unmeasured one. When the pull lands it
should fire on state — a boundary exists AND the last pull is stale AND the block
is in an active crop cycle — not on a calendar tick that would spend imagery
credits on a fallow block in January.

### Housing Unit and Housing Assignment — the labor camp

Employer-provided farm housing sits at the intersection of three regimes that
each want a different fact about the same cabin, and none of them accept "we know
who lives there" as an answer: IRS Section 119, Oregon's ORS 653 and OAR 839-015,
and the FSMA Produce Safety Rule's Subpart L. None of the flags this release adds
is a determination and none of this is legal advice — they record what somebody
decided and when, so the decision can be defended or revisited.

**Overlap is refused by default and allowed on request.** Two people in one cabin
on one night is a data-entry mistake most of the time and the whole point of a
Multi-Unit Building the rest of the time. Refusing outright would make the
barracks unusable; allowing silently would let a typo become a bed somebody does
not have. So it refuses, names the assignment already there, and takes
`allow_multi_occupancy=true` from a caller who means it. Somebody moving out on
the 15th and somebody moving in on the 15th **did** share the cabin that night,
and the comparison is inclusive at both ends for that reason.

**Nothing deletes an assignment.** `end_housing_assignment` writes an end date;
the row stays. An assignment removed when the person leaves cannot defend a
Section 119 classification, cannot answer a wage claim about a housing deduction,
and cannot tell an investigator who was in the camp the week in question — and
those are the three moments the record exists for.

**The employee link is soft until an HR app makes it hard.** `Employee` is a Data
field rather than a Link, because Frappe HR is not a dependency of this app and a
Link would make the whole doctype fail to migrate on a site without it. Where an
HR app *is* installed the refusal is real: an assignment naming somebody not on
file is a roster that has already drifted from payroll.

**The lawful occupancy is computed once and then left alone.** Fifty square feet
of sleeping area per occupant — 29 CFR 1910.142(b)(1), which Oregon's rules
follow — gives a unit with a floor area an answer without anybody typing one. But
it is a default, not a derivation: a cabin with a fixed bunk layout keeps the
number somebody worked out, and changing the square footage recomputes only a
limit that was itself computed. A capacity over 20 outside a Multi-Unit Building
is warned about rather than refused, because a twenty-person cabin is barracks by
another name and some of them really are.

### Compliance is woven into the operational doctypes, not bolted beside them

The food-safety fields are on `Field`, the water-quality fields are on
`Irrigation Zone`, and the habitability and detector dates are on `Housing
Unit`. The test is whether removing a field breaks operations or only breaks
reporting — and each one has a test that asserts **both halves of the same
removal**:

- Remove `last_spray_date` and the Worker Protection Standard report loses a line
  *and* nobody can answer whether the re-entry interval on block 3 has run.
- Remove `worker_hygiene_station_present` and an inspector loses a checkbox *and*
  dispatch loses the fact that decides whether a crew may work that block at all.
- Blank a zone's `water_test_last_date` and it lands on the FSMA Subpart E list
  *and* `get_irrigation_zone` starts saying not to run it before harvest.
- Remove a Field's boundary and the spray record loses the one thing an auditor
  can check a GPS fix against *and* the geofence stops answering for a crew
  standing in the block.
- Mark a Housing Unit uninhabitable and it appears on the register's exception
  list *and* `create_housing_assignment` refuses to put anybody in it.

A separate "Field Compliance Log" that somebody fills in after the fact would
fail that test — nothing about picking would stop if it disappeared — which is
why this release does not have one.

### Fixed

- **`compat.checked`, and every Check field read through it.** `bool("0")` is
  True, and a Check field does not always come back as an integer:
  `frappe.new_doc` copies the DocType's declared default onto the document
  verbatim, and in the DocType JSON that default is the *string* `"0"`. A tool
  describing that document with a bare `bool()` reports every unticked box as
  ticked — which would have said a block with no worker hygiene station had one,
  and a housing unit outside the Produce Safety Rule was inside it. This is the
  same failure `settings.as_bool` exists to prevent for the tool switches, and
  the two are deliberately identical in behaviour.
- **`link_field_to_cost_center`'s cross-company refusal was unreachable.** The
  cost center resolver refused first with a terser message, so the sentence
  explaining *why* a cost allocated across two companies is an intercompany
  transaction rather than a dimension never appeared. Resolution is now scoped
  first and site-wide only as a fallback, so the explanatory refusal is the one a
  caller gets.
- **`create_housing_assignment` reported one occupant too many** in a shared
  unit, because it recounted the overlaps after inserting the row and counted the
  new row as one of its own.
- **`create_housing_unit` never checked an Asset's company.** It read
  `owning_entity` off a document whose controller had not run yet, so the field
  was empty and the cross-company check silently passed everything.
- **`create_housing_assignment` let an end date before the start reach the
  controller**, so the caller got a raw `ValidationError` instead of a sentence
  saying nothing was created.

### Notes

- `Field` is a doctype name with no core Frappe or ERPNext collision today, but
  it is a common enough word to be worth knowing you have taken. If a future app
  wants it, this one has it.
- `Parcel.abbr` is additive and nullable. Existing parcels are unaffected until
  something saves them, and every read falls back to deriving the same key.
- `shapely` and `h3` are declared dependencies but imported defensively, and CI
  runs the whole suite **twice** — once before installing them and once after —
  because a build that only ever saw them present would never check that the
  graceful-degrade path works.
- Full suite: 1951 tests, 0 failures. 73 of them skip on a bench without the
  geospatial libraries.

## 0.11.0 — 2026-07-30

Four features in one release, because they are one feature. A parcel is held by
an entity, an entity is a related party, a related party is a 1099 recipient, and
a quarterly report is the document all of it ends up inside. Shipping them
separately would have meant three releases that each pointed at a doctype the
next one adds.

Fifteen tools, three DocTypes, no child tables, and no new runtime dependency.

### Real Estate — `Parcel` and `Lease`

**The unit is the parcel as the county assessor knows it.** A family's land is
described four different ways by four different documents: an appraisal talks
about "Red Camp", a tax statement about parcel 1N-13E-8-1200, a deed about metes
and bounds, and the balance sheet about a Fixed Asset with a purchase price. Only
one of those is a unit everyone agrees on. So the register is keyed on the
parcel, carries the assessor's number as the identifier a third party will
recognise, and links out to the Asset rather than trying to be one.

**Appraised value is not book value, and they are meant to differ.**
`gross_purchase_amount` on an Asset is what was paid, which is what the balance
sheet must carry; `appraised_value` on a Parcel is what it is worth, which is
what an estate plan turns on. A single field would force one of those two
questions to be answered wrongly. `link_parcel_to_asset` reports the gap between
them — a parcel appraised at 3,100,000 sitting on the books at a 1998 cost of
240,000 is not a discrepancy to be fixed, it is unrealised appreciation, and it
is the single most important number in a succession conversation. Nothing posts
it, because unrealised appreciation is not a journal entry.

**The docname carries the entity**: `"Red Camp - HLD"`, not `"Red Camp"`. Family
land gets reorganised, and two entities in one family end up with a "Home Place"
apiece. A docname keyed on the name alone would make the second impossible to
file and the first impossible to trust.

**A duplicate assessor parcel id inside one entity is refused.** That number is
the county's primary key; two parcels sharing one means a typo in one of them,
and it is the refusal that catches a bad import.

**Direction on a lease is stated, not inferred.** Outbound means the owning
entity is the lessor. The alternative — working out which party is "us" by
matching a legal name against a Company docname — is wrong for every entity whose
legal name is not its ERPNext name, which is most of them ("Highland Ltd
Liability Co." against a Company called "Highland LLC"). So the caller says, and
`create_lease` reports whether the claim looks *consistent* with the parties
named. Reported, never enforced: a refusal built on a string comparison it cannot
win is a refusal nobody could get past.

**Nothing expires a lease.** A lease marked Active whose expiration date has
passed is reported by `list_leases` and left exactly as it was. Farm ground
routinely runs on month to month past its stated term, and a status that flipped
itself on a calendar would erase the difference between "still running" and
"nobody has looked at this in years". The warning says so in capitals, because a
reader who assumes the system tidied up is a reader who has stopped checking.

**The rent roll refuses to treat an unknown as a zero.** Rent is annualised from
amount and frequency for Active leases only. A crop share and a one-time payment
have no annual rate; they are listed under `rent_not_annualisable` rather than
counted as nothing, because a rent roll that quietly zeroed them would understate
the whole portfolio.

New tools: `create_parcel`, `update_parcel`, `list_parcels`, `get_parcel`,
`link_parcel_to_asset`, `create_lease`, `update_lease`, `list_leases`,
`get_lease`. The four read tools default ON, the five mutating ones OFF.

### `Related Party` — the governance register

**This is not the Party field on a Journal Entry.** ERPNext already answers "who
was this transaction with" through Supplier, Customer, Employee and Shareholder
links; those work and nothing here replaces or shadows them. This answers a
different question — "who is related to us, in what capacity, from when, and
under what document" — which no transactional field can, because a transaction is
an event and a relationship is a state. "Was the person we paid $24,000 last year
a manager of this company at the time" is a question the ledger cannot answer and
the IRS asks anyway.

**Four digits, never nine.** `tax_id_last4` takes exactly four digits and refuses
nine — not truncated, not masked, not accepted with a warning. The refusal names
the four digits to send instead, because a validator that says "invalid format"
to somebody who has just pasted a real SSN has told them nothing about why it
matters. The controller enforces the same rule, since the Desk form is a second
door into the same field, and the field is declared four characters long as the
belt to that brace. The full number belongs on the signed W-9, on paper. And
`get_related_party` never returns more than four digits *even from a linked
Supplier* — `supplier_detail.tax_id` says only whether one is on file.

**A person is not one row.** In an LLC the ordinary case is somebody who is both
Manager and Member, under two different instruments, from two different dates.
One row with one Select cannot hold that, and picking a "primary" role would mean
the register quietly disagrees with the operating agreement. So the docname
carries the relationship — `"Tim Polehn - Manager - OML"` beside
`"Tim Polehn - Member - OML"` — and `list_related_parties` reports `count`
(relationships) and `distinct_people` (names) separately.

**Nothing is deleted when a relationship ends.** `end_date` is set and the row
stays: the transactions it explains are still in the ledger, and a prior year's
disclosure schedule still needs to know who was who at the time.

It sits beside the cap table rather than inside it. `Cap Table Entry` maps an
anonymous member id to an ownership percentage — deliberately the only place on
the site where that mapping exists. Related Party holds every other kind of
relationship: the trustee who owns nothing, the estate attorney, the son who is a
beneficiary but not yet a member. Folding those in would mean rows with no
percentage in a register whose whole purpose is that the percentages total 100.
The two link, so a member appears in both without either being copied.

New tools: `create_related_party`, `update_related_party`,
`list_related_parties`, `get_related_party`.

### `generate_quarterly_investment_report` (mutating, default OFF)

**Kairos, not chronos.** A quarterly report is not due on a date; it is due when
the quarter is *actually closed*. Four things must be true, and the refusal names
every one that is not — all of them at once, so a single call answers "am I
ready?" rather than sending the caller round the loop four times:

1. the quarter has ended;
2. the custodian's statement is filed as a **Prior Statement** governance
   document with an effective date inside it — a report written before the
   statement arrived is a report written from a guess;
3. no journal entry touching the investment accounts is still a draft, because an
   account that reconciles today and will not once three drafts are posted is not
   reconciled, it is about to not be;
4. no bank transaction in the period is unreconciled.

A report generated on a calendar date regardless of state is a report whose
numbers may be wrong, signed by somebody who assumed the schedule meant
something. `dry_run=true` runs every precondition and computes every figure
without writing, which is the right first call.

**It invents nothing.** Without `benchmark_rate_percent` the return over
benchmark and the performance fee are NOT computed and say so in words. They are
not zero and not estimated: the 10-year Treasury yield is a market fact this site
does not hold, and a performance fee computed against an assumed benchmark of
nothing overstates what the manager is owed. Same for the high-water mark and for
`net_contributions`, which is reported as an assumption when it is one.

**Holdings come from the caller.** This app reads one ERPNext site and the
custodian's positions are not on it. Pass `holdings` and the report reconciles
the snapshot against the ledger and reports the variance; omit it and assets
under management are the ledger balance of the investment accounts, stated as
such. The accounts themselves are matched by name off the company's own chart and
**listed in the report**, so the reader sees exactly what was included — or named
explicitly, and a chart with no match is refused rather than guessed at.

**Manager and custody fees accrue at 1.00% each** by default — the split the
Investment Management Agreement is actually charged at, inside its 2.00% cap —
computed on average assets for the quarter. It is an accrual and nothing posts
it; the result says which tool does. A combined rate above the cap is flagged,
not refused, because a later agreement may raise it.

**PDF is the primary format and that is a requirement, not a preference.** A
`.docx` handed over on 2026-07-29 could not be opened on the machine it was sent
to. `output_format="docx"` exists for a report that has to be edited before it is
signed, and the default is never it.

### `generate_1099_prefill` (mutating, default OFF)

A calendar year of supplier payments, aggregated into an xlsx worksheet and a
per-recipient 1099-NEC form (Copies A, B and C), filed together in the governance
archive as a **Tax Filing**.

**It is called a pre-fill and it means it.** Recipient taxpayer ids print as
`XXX-XX-nnnn` because this site holds four digits on purpose. Copy A must be the
official scannable red-ink form or an electronic filing; the Copy A page here is
stamped as an information copy and says that printing and mailing it is not a
filing. Copies B and C print on plain paper and are the ones that go out.

**Classification is never silent.** Every recipient comes back `reportable`,
`exempt` or `borderline` with the reason in a sentence:

- an **LLC** is borderline, because a disregarded entity is reportable and one
  taxed as a corporation is not, and only the W-9 says which;
- a **law firm** is borderline even when incorporated, because attorneys are
  reportable regardless — which is precisely why "ends in PC, skip it" is the
  wrong rule, and why the matching is on word tokens rather than substrings
  ("Lawson Supply" is not an attorney);
- a **government-sounding name** is flagged rather than dropped, because a name
  is a hint and not a determination;
- a vendor with **nothing recorded** is borderline with the remedy: register it
  as a Related Party, or read the W-9.

That last one is why these two features are one release: a Supplier row cannot
say "this vendor is the manager's own LLC", and the related-party register can —
through the `supplier` link, which is what turns a payment in the ledger into a
disclosure on the return.

**The arithmetic, which is the part worth arguing with.** Payments are summed
from **GL Entry** rows carrying a Supplier party — so every voucher type, and
only submitted ones, since cancelled vouchers leave no GL row to filter out:

- on a **Payable** account: **debits only**. A debit to accounts payable is a
  bill being paid; a credit is a bill being raised, and a 1099 reports cash paid.
- on **every other account**: **debits minus credits**. A site that books a
  supplier straight from expense to bank puts the party on the expense line, so
  the debit is the payment and a credit is a refund that genuinely reduces it.

That rule is right in both bookkeeping styles, which is why it is a rule and not
a switch. `by_account` shows the debits and credits behind every total so the
reasoning can be checked rather than believed.

**What is excluded is said out loud.** Employees, because that is W-2 territory —
and the count and total of employee-party postings is reported anyway, so "nobody
looked" and "somebody looked and excluded them" are different-looking answers.
Opening entries. Anything under the threshold, listed with its total so a case
near $600 is visible rather than absent.

**It refuses a tax year that has not ended**, naming the earliest date it could
be run.

### Document writers: PDF, XLSX and DOCX in the standard library

`erpnext_mcp/render/` writes all three formats with `zipfile`, byte offsets and
nothing else. This app promises no runtime dependency beyond Frappe/ERPNext, and
that promise is what makes `bench get-app` safe on somebody else's bench. Frappe
ships two routes to a document and both are conditional: `frappe.utils.pdf`
shells out to a **wkhtmltopdf binary** present in some images and absent in
others, and `xlsxutils` imports openpyxl. Either means a tool that works on the
machine it was written on and fails on the one it was deployed to, at the moment
somebody needs the report.

**Courier, and only Courier.** A PDF naming a base-14 font carries no glyph data,
but the writer still has to know how wide each glyph is to wrap a line or
right-align money. For Helvetica that is a 230-entry width table transcribed by
hand, where one wrong number is a column that silently overlaps in the printed
copy. Courier is monospaced at exactly 600/1000 em: the arithmetic is exact
rather than approximately right, and decimal points line up because they cannot
do anything else.

**Money columns never wrap.** A right-aligned column holds a formatted amount,
and an amount broken across two lines reads as two figures. When a table will not
fit, the prose columns give way and the numbers keep their width.

**The same inputs give the same bytes.** Zip members carry a fixed timestamp and
`render()` does not mutate the document, so the archive copy and the printed copy
cannot differ in a way nobody can see.

### `scripts/seed_related_parties.py`

Seeds the related-party register from a JSON file the operator keeps **outside
this repository** — the useful content of that register is people's names, and
this repository is public.

It runs outside `bench execute`, so it configures Frappe itself: `--sites-path`
or auto-detection by looking for `common_site_config.json`, `--site` or
`currentsite.txt`, and the log directories created before `frappe.connect()`
rather than assumed. Dry-run by default; `--apply` writes. The whole plan is
validated before the first insert — including the four-digits-never-nine rule,
refused before Frappe is even started — so a plan of forty records is refused
whole rather than half-applied. The module docstring documents the
`docker cp` sequence for getting both the script and the plan into a container,
and there is a test comparing the flags the docstring names against the flags
`argparse` actually registers.

### Also

- `Governance Document` gained two categories: **Tax Filing** and **Lease**.
- `args.py` gained `select_options` and `as_choice`, which read a Select's
  options off the site's own meta. `governance.py`'s private copies now delegate
  to them rather than being a second implementation of the same rule.
- `output_path` on both generators is confined to the site's own
  `private/files` and `public/files`, checked on the **resolved** real path so a
  symlink cannot step outside, and refusing to overwrite an existing file unless
  told to. A bad path refuses the whole run before the first write rather than
  leaving an archive entry behind.
- `before_uninstall` now warns about Parcel, Lease and Related Party rows too.
- Standalone suite: 1222 → 1536 tests; in-bench suite 255 → 284.

## 0.10.0 — 2026-07-29

One tool, for the gap found the day somebody tried to put a year of brokerage
statements onto the entries that book them and discovered there was no way to.

### `attach_file_to_document` (mutating, default OFF)

Attaches one file to **any** document on the site. A WFA statement onto the
Journal Entry that books it. A receipt onto the Bank Transaction it explains. A
purchase contract onto the Asset.

**Why this was missing and why it was not obvious.** The app already had
`attach_governance_document`, and from the outside it looks like the tool for
this — it takes base64, it makes a private File, it says "attach" in the name.
It is not. It files a *new* Governance Document and attaches the file to
**that**. Correct for a trust instrument or an operating agreement, which are
documents in their own right. Useless for December's statement, which is not a
document in its own right — it is evidence for a posting, and an auditor asking
"what supports this entry" wants the answer *on the entry*. Thirteen statements
and three anchor Journal Entries later, there was no MCP path from one to the
other at all, and the only route left was clicking through the Desk.

**It creates a File and nothing else.** No balance moves, no docstatus changes,
no existing row is touched. That is the whole shape of the tool.

**Every constraint is read off the site, not compiled into the app.** There is
no list of blessed file extensions here and no list of doctypes that may be
attached to. Both would be a snapshot of one ERPNext install frozen into an app
that gets installed on others, and both would refuse things the site itself
permits. So:

| Refusal | Read from |
| --- | --- |
| Unknown `doctype` or `name` | the site's schema and tables |
| Acting user cannot `write` the parent | Frappe's permission model — the permission the Desk's own attach control needs |
| Parent is **cancelled** | the parent's `docstatus`; `allow_cancelled=true` overrides |
| A filename the document already has | that document's existing attachments, with the clashing File named |
| Too many attachments | the parent DocType's `max_attachments` |
| Disallowed extension | whatever allowlist System Settings declares — **nothing**, on a site that declares none, which is Frappe's own answer |
| `company` mismatch | the parent's `company` field |

**A guard that cannot be applied is an error, not a shrug.** Passing `company`
for a doctype that has no company field is refused rather than ignored. A caller
who believes a guard ran when it did not is worse off than one who never asked
for it.

**Cancelled parents are refused by default** because a cancelled document is
history, and quietly growing its evidence file afterwards is how a record stops
meaning what it says. `allow_cancelled=true` says the caller knows — which is a
different thing from not having noticed.

**A second file under the same name is refused, naming the first.** The
anticipated caller is a script walking a year of statements onto their entries;
half of it failing and being re-run is the normal case. "That one is already
done, here is its File docname" is the useful answer. Two files with one name on
one document is a question nobody can answer in 2031.

**`dry_run` defaults to FALSE**, unlike `import_chart_of_accounts` and
`run_depreciation_cycle`. Those write many documents and are hard to unpick;
this writes one File and moves no money. Making the ordinary case cost two round
trips would be safety theatre. `dry_run=true` validates the parent and returns
the proposed action — including the size and sha256 — without writing, which is
what a batch script should do over its target list once before running live.

Files are **private by default**, so reading one back through
`get_attachment_content` requires read permission on the parent. `file_content`
is base64 with the same 8 MB ceiling `attach_governance_document` uses;
`file_url` records an externally hosted file without copying it. The result and
the audit row both carry the sha256 of the stored bytes.

### The audit log stopped losing the interesting half of a row

`MCP Action Log.arguments_json` was truncated at 8000 characters *after*
serialisation, and `json.dumps(..., sort_keys=True)` puts `file_content` ahead
of `file_name`, `is_private` and `name`. A megabyte of base64 would therefore
have produced a row recording that a file was attached and nothing whatever
about which file, or to what.

Oversized *values* are now elided before serialisation —
`"<11184812 characters elided>"` — so the length survives and every other
argument stays in the row. Whole-payload truncation still applies on top, for a
payload that is large because it has many arguments rather than one big one. The
sha256 that identifies the bytes is in `result_summary` either way.

### Also

- `attach_governance_document` and `attach_file_to_document` share one base64
  decoder (`files.decode_base64_content`), so there is one 8 MB ceiling to raise
  rather than two to forget. The refusal wording either tool produces is
  unchanged.
- The standalone harness's `Meta` now carries `max_attachments`, Frappe's
  0-means-unlimited default, so the limit check is testable without a bench.

**77 tools** — 38 read-only, 39 mutating.

## 0.9.0 — 2026-07-28

Three tools for the day you post a year of history, and the fix for the bug that
made that day take twice as long as it should have.

### The bug fix, first, because it is the one that cost a day

**Every Journal Entry this app wrote was missing half of every amount.** A
`Journal Entry Account` row stores each figure twice — `debit` in the company's
currency and `debit_in_account_currency` in the account's — and ERPNext's
`set_amounts_in_account_currency` derives the first FROM the second on every
validate:

```python
d.debit = flt(d.debit_in_account_currency * d.exchange_rate, d.precision("debit"))
```

This app set `debit` and left `debit_in_account_currency` at zero. So the insert
succeeded, the draft was written to the database with its amounts silently
zeroed, and the entry was refused the moment anything validated it again:

```
Row 1: Both Debit and Credit values cannot be zero
```

Four auto-generated opening-balance entries did exactly that on a live site. The
workaround — rekeying every line through `create_journal_entry` with the
`_in_account_currency` fields set by hand — works, and is hours of typing to get
back to what the tool was supposed to have produced.

**The fix is in `validated_journal_lines`, not in the tool that surfaced it.**
`set_opening_balance` was where it was noticed, but every Journal Entry this app
writes — opening balances, member events, depreciation runs, loan payments,
hand-built entries — comes through that one function, and fixing only the tool
that showed the symptom would have left the other five wrong. Every line it
returns now carries both columns. At exchange rate 1 the account-currency figure
is *copied* rather than computed, so no rounding can put a fraction of a cent
between two columns of the same number.

Two things fell out of doing it there:

- **A line given only in the account's currency is now understood.**
  `{"account": "1100", "debit_in_account_currency": 100}` means the same as
  `{"account": "1100", "debit": 100}` and is no longer refused as a line with
  neither a debit nor a credit.
- **A foreign-currency line with no `exchange_rate` is now refused**, naming both
  currencies. Previously it would have been posted at the company-currency figure
  and then converted again by ERPNext. And a line whose `debit` and
  `debit_in_account_currency` disagree is refused rather than one of them being
  chosen: this app's double-entry check would have run on one set of numbers and
  the posting on another.

**Why the standalone suite did not catch it.** The double stored whatever it was
given. `harness.JournalEntryDocument` now models ERPNext's derivation *in the
order ERPNext does it* — zero-check against the values as given, then derive —
which is what reproduces the real failure: a draft that inserts cleanly, reads
0.00, and cannot be submitted. A double that derived first would have failed the
insert instead, and a double that filled the columns in from `debit` (the
intuitive direction) would have let the broken code pass. Fourth time now:
*when the double is more permissive than the framework, tests pass and sites
break.*

### Added — tools

- **`post_opening_balance_journal_entry`** (mutating, default off). A whole
  opening balance sheet as one Journal Entry, every line explicit.

  `set_opening_balance` is the right tool when you know one side of one
  historical event and want the equity plug computed. It is the wrong shape for
  transcribing a trial balance off the previous system, where both sides are
  already in hand: that means one call and one stray equity line per account.
  This takes the lines as given, adds a single balancing line to an
  `offset_account` you name — required exactly when the lines do not balance —
  and flags the entry `is_opening` with the `Opening Entry` voucher type.

  **It can post.** `submit: true` submits the entry after creating it, which is
  why it checks `allow_submit_journal_entry` as well as its own switch, and
  checks it *before* writing anything so a site with posting disabled gets a
  refusal rather than a draft nobody asked for.

  The offset account is not required to be equity, unlike `set_opening_balance`'s
  computed plug. A transcribed trial balance that is out by the retained earnings
  figure belongs against retained earnings, and the caller naming the account is
  making that call on purpose.

- **`bulk_submit_journal_entries`** (mutating, default off). Submit up to 500
  drafts in one call.

  Five hundred drafts posted one MCP round trip at a time is not the same job at
  a different speed. It is the job where somebody loses track at number four
  hundred and stops without knowing which ones went.

  **Each entry is submitted in its own transaction** — committed on success,
  rolled back on failure — and the loop carries on. This is the only place in
  this app that commits mid-call, and it is deliberate: the alternative is a
  batch where number four hundred fails and the request rolls back the three
  hundred and ninety-nine postings that were fine. It is also what Frappe's own
  bulk submit does. Returns a row per document with `ok` and the exact error,
  plus aggregate counts.

  An already-submitted entry comes back `ok` with `skipped: already_submitted`,
  never an error, so a half-finished batch is safe to retry whole. A cancelled
  one is a failure — it cannot be posted again. Checks
  `allow_submit_journal_entry` too, and fails before touching anything.

- **`delete_draft_journal_entry`** (mutating, default off, destructive). Delete a
  draft outright.

  `cancel_journal_entry` refuses a draft, correctly: there is nothing to reverse,
  because a draft has moved no balance. That left an unwanted draft with no MCP
  path at all, and a tool that can produce four hundred drafts and not withdraw
  one makes work rather than doing it.

  **Drafts only, whatever is asked.** A submitted entry has written GL Entries;
  deleting it would take those balances with it and leave nothing saying why, so
  it is refused and pointed at `cancel_journal_entry`. A cancelled entry and its
  reversing rows are the evidence that a posting was made and undone, so that is
  refused too.

  `reason` is mandatory, and the response carries the deleted entry's company,
  date, totals and every line — because once the call returns, the MCP Action Log
  row is the only record that the document ever existed.

### Changed

- `reconcile_bank_transaction` now names `payment_document` when a caller sends
  `payment_doctype`. The field is `payment_document` on ERPNext's Bank
  Transaction Payments table and always has been, in both this app's schema and
  its handler — but `payment_doctype` is what the field is called almost
  everywhere else in Frappe, so it is what a model reaches for first, and
  "payment_entries[1] needs both payment_document and payment_entry" did not say
  which of the two keys was the problem. It does now, quoting the value back in
  the right shape. Accepting both names was the other option and was not taken:
  this would have become the only tool in the app that reads a key it was not
  given.

### Tests

1180 standalone tests, up from 1112. The new ones worth naming:

- `AccountCurrencyAmounts` in `test_mutate_tools.py` — the regression suite for
  the bug above, including *the entry can actually be submitted*, which is the
  assertion whose absence let v0.8.0 ship.
- `TheOpeningEntryCanActuallyBePosted` in `test_opening.py` — the same thing
  through `set_opening_balance`, single-line and multi-line, including the
  computed equity plug, which is the line most likely to be the one nobody filled
  in because this app builds it rather than the caller.
- A round trip in `test_mutate_tools.py` from `create_journal_entry` through
  `submit_journal_entry` to `reconcile_bank_transaction`, and a test of what
  ERPNext's own `add_payment_entries` is handed and what is read back from it.

## 0.8.0 — 2026-07-27

The tooling a company needs on the day it goes live: the bank accounts money
actually arrives in, the balances that were true before day one, the notes it
owes, and a way to get rid of the accounts a bundled chart left behind.

v0.6.0 made the axes a posting is filed under reachable. v0.7.0 added who owns
the company and what the equipment is worth. This is the layer between those and
a first bank sync — eight new tools, one read tool, one new doctype, thirteen
more company defaults, and the fix for a bug that made setting up a real chart of
accounts harder than it should have been.

### The bug fix, first, because it is the one that cost time

**`import_chart_of_accounts` could not create a new root account.** Every live
import that included a top-level account died on the first one with:

```
MandatoryError: [Account, 1000 - Assets - OML]: parent_account
```

ERPNext's Account marks `parent_account` as required. A root account by
definition has none, so the insert never reached any of this app's own logic.
The workaround — renumber the company's existing roots to 91xxx and graft the new
tree under a renamed one — works, and is a lot of moving parts for something the
importer is supposed to do.

**The fix is one flag on one insert**, and it is the same flag ERPNext's own
chart-of-accounts importer sets for its own roots
(`erpnext/accounts/doctype/account/chart_of_accounts/__init__.py`):
`doc.flags.ignore_mandatory = True`, set **per document and only when the account
has no parent**. A child that skipped mandatory validation would be this app
quietly disabling a check the framework meant to run.

The plan reports it too, so dry run and live run still describe the same thing:
`new_root_accounts` lists the accounts that would become new roots, with a note
saying they are added *alongside* the company's existing ones — ERPNext will not
let a root be moved or renamed into an existing tree afterwards.

**Why the standalone suite did not catch it.** The double inserted root accounts
quite happily. `harness.AccountDocument` now models Frappe's mandatory pass and
raises the real `MandatoryError`, which turns eleven previously-green tests red
against the unfixed code. That is the recurring lesson from this project's own
history, third time now: *when the double is more permissive than the framework,
tests pass and sites break.*

Renumber-and-graft is unchanged and has its own test, because a live site is
already set up that way.

### Added — tools

- **`create_fiscal_year`** and **`update_fiscal_year`** (mutating, default off).
  The prerequisite for everything else in this release that touches history.
  ERPNext refuses a posting whose date falls outside a fiscal year, and it
  refuses it *from inside the document being saved* — so on a site whose only
  year is 2026, booking a March 2025 equipment transfer fails with an error about
  a date rather than about a missing year. `set_opening_balance` cannot reach a
  period until the year exists.

  **The overlap check is company-aware**, which is the part worth getting right:
  a fiscal year with no `companies` is global and collides with everything, two
  restricted years collide only where they share a company. Two years covering
  the same day for the same company make ERPNext's own `get_fiscal_year`
  ambiguous, and which year a posting lands in stops being a fact about the
  posting. Disabling a year does not free its range. ERPNext's own
  `validate_overlap` is company-blind on several versions and is stricter; where
  it is, its refusal is passed through unchanged — this never loosens a rule the
  framework enforces.

  **`update_fiscal_year` guards the dangerous half.** Moving a year's dates moves
  no posting; it changes which year — or no year at all — every posting already
  written falls into, retroactively. So the GL entries that would fall *out* of
  the new range are counted before anything is written and any at all is a
  refusal with the count. It cannot rename the year (the name is the docname, and
  is the string every Journal Entry and Budget that names a year holds) and
  cannot change `companies`; both are refused by name.

  Also: ERPNext requires a year to end exactly one year after it starts, less a
  day, unless `is_short_year` is set — and its own message does not say which
  date it wanted. This computes it, clamping leap days the way the calendar does
  (a year starting 29 February ends on the 27th).
- **`set_opening_balance`** (mutating, default off). Books one historical event —
  equipment transferred in, proceeds of a sale that predates this ledger, a
  portfolio's starting value — as a DRAFT journal entry, **computing** the
  offsetting line against Opening Balance Equity rather than trusting the caller
  to work it out. Also flags the entry `is_opening` and, where the site offers
  the voucher type, `Opening Entry`; those are what keep opening amounts out of
  the period's activity in every report that separates the two, and nothing warns
  you when they are missing. The equity account is *found* — account number 3300
  first, then a leaf Equity account named after opening balances — and anything
  other than exactly one match is refused with the candidates listed.
- **`create_bank_account`** (mutating, default off). Creates the `Bank Account`
  record a bank feed writes into, and the `Bank` institution behind it, in one
  transaction. Refuses a GL account that is neither an Asset (a bank account) nor
  a Liability (a credit card), and refuses an Asset account whose `account_type`
  is not Bank or Cash — ERPNext's own account picker and its reconciliation tool
  both filter on that flag, so an untyped account saves fine and then cannot be
  reconciled at all. Warns, rather than refuses, when a second Bank Account would
  post to the same GL account.
- **`delete_account`** (mutating, default off, **irreversible**). Hard-deletes an
  account with no history. The complement to `disable_account`, and almost never
  the right tool — but a disabled account **still holds its account number**, and
  on a company being renumbered onto a real chart that is the entire problem.
  Four checks, all on by default, all refusals, all run before anything is
  deleted so one call reports every reason: GL entries (including journal entry
  lines on unsubmitted drafts, which write no GL row and would otherwise read as
  untouched), child accounts (disabled ones count), Company default fields, and
  Bank Account records.
- **`create_note_payable`**, **`record_loan_payment`**, **`close_note_payable`**
  (mutating, default off) and **`list_notes_payable`** (read-only, default on).
  See below.

### Added — the `Note Payable` doctype

Two doctypes: `Note Payable` and its `Note Payable Event` child table.

**Why not ERPNext's Loan module.** ERPNext's Loan models the company as the
*lender* — an application, a disbursement, a repayment schedule, its own
accounting, half a dozen doctypes. A holding company with four notes outstanding
is on the other side of every one of those.

**What it adds to the liability account that already exists.** Three things a
balance on account 2310 cannot tell you: the terms (rate, maturity, frequency),
the provenance (what was agreed, by whom, where the original is — for a family
note traced back to 2003, that sentence is the whole record), and what it
secures.

`record_loan_payment` is mostly about the split. A payment leaving a bank account
is one number whose two halves land in completely different places: one reduces a
liability, one is an expense of the period. Booked as a single line against the
liability, the year's interest expense reads as nil and the balance sheet says
the note was paid down by more than it was. Pass `principal_split`,
`interest_split`, or one and let the other be derived — they have to add up or
nothing is written.

`close_note_payable` **writes no journal entry, deliberately.** Relieving a
written-off balance is a posting with real tax consequences (forgiven debt is
usually income), and a refinance moves a balance between two liability accounts.
Both belong to somebody who meant them. The response spells out exactly which
entry is still owed and against which account, so the omission is impossible to
miss.

`principal_outstanding` on a note is a **convenience figure**. The authoritative
balance is the linked GL account, and the two diverge by every payment recorded
as a draft nobody has posted — which, in an app where nothing submits, is the
normal state. Every response that reports the field says so.

`link_asset_to_note` now recognises `Note Payable` as a link target, and
`create_note_payable(related_asset=…)` delegates to it: the same tenor check,
from the other direction, refusing by default when an asset's useful life and its
note's term disagree. The note and the link are one transaction — a refused link
leaves no note behind.

### Added — thirteen more company defaults

`set_company_defaults` supported thirteen keys and now supports twenty-six. The
new ones are the fields a module will not save a document without:

`disposal_account`, `capital_work_in_progress_account`,
`expenses_included_in_asset_valuation`, `asset_received_but_not_billed`,
`stock_adjustment_account`, `stock_received_but_not_billed`,
`unrealized_exchange_gain_loss_account`, `unrealized_profit_loss_account`,
`default_advance_received_account`, `default_advance_paid_account`,
`default_operating_cost_account`, `default_selling_cost_center`,
`default_buying_cost_center`.

`disposal_account` is the one that actually bit: ERPNext refuses to scrap or sell
an Asset without it, and reports the refusal *from the Asset*, which is not where
anybody looks. All thirteen are type-checked the same way the original thirteen
are — including `default_advance_received_account`, which looks wrong until you
see why ERPNext filters it to a **Liability** with `account_type = Receivable`:
money held for a customer is a liability, keyed so the party ledger picks it up.

No new tool, no behaviour change to the existing keys, still all-or-nothing and
still idempotent.

### Changed

- `link_asset_to_note` tries `Note Payable` first when guessing which doctype a
  note reference lives in, and its refusal now names `create_note_payable`.
- `import_chart_of_accounts` returns `new_root_accounts` (and `new_root_note`
  when it is non-empty) in both dry and live runs, and each planned root row
  carries `new_root: true`.
- `before_uninstall` warns about `Note Payable` records alongside the other
  doctypes whose contents are the only copy.

### Tests

**1112 standalone tests, all passing** (was 902).

- **`tests_standalone/test_banking.py`** — 29 tests. Every refusal in
  `create_bank_account`, the shared-GL-account warning, and that a failure leaves
  no orphan `Bank` behind.
- **`tests_standalone/test_opening.py`** — 35 tests. The plug arithmetic in both
  directions, the already-balanced case, the flags, finding the equity account by
  number and by name, and both ways of failing to find it.
- **`tests_standalone/test_notes.py`** — 70 tests. The split, the balance, the
  history, the asset tenor check from the note's side, and every disposition.
- **`tests_standalone/test_fiscal.py`** — 44 tests. Every branch of the
  company-aware overlap rule (a date-only check would wrongly refuse the
  per-company years a group structure needs; a company-only one would let a
  global year sit on top of a restricted one), the leap-day clamp, the
  orphaned-postings refusal against real GL rows, and the end-to-end case the
  tool exists for: create the year, then book into it.
- **`test_accounts.ImportCreatesNewRoots`** — the regression above, including a
  test that the flag is set on the root **and only on the root**, and a
  guards-the-guard test asserting the double still refuses a bare root (so the
  others cannot pass for the wrong reason).
- **`test_accounts.DeleteAccount`** — every check, the "report every reason at
  once" behaviour, and that the account number is actually free afterwards.
- **`test_dimensions.SetCompanyDefaultsV8`** — one test per new shape of rule.
- **`erpnext_mcp/tests/test_notes.py`** (in-bench) — that the two doctypes
  migrate and their modules import, that the controller's throws fire on the Desk
  path, that ERPNext accepts an `is_opening` journal entry and a Bank Account
  built here, that a new root account can be created against a real Account
  doctype, and — the one a double cannot show — that ERPNext really does refuse a
  posting outside every fiscal year, and accepts the same one once the year has
  been created.

Harness additions: `MandatoryError` and Frappe's mandatory pass on root accounts;
the `Bank` doctype and ERPNext's `BankAccount.autoname`; the `Note Payable`
doctypes; Fiscal Year's `year` field and its `field:year` naming rule, so a year
is named the way a real insert names it rather than by writing `name` directly;
Journal Entry's real `voucher_type` option list; and six of the thirteen new
Company default fields — the other seven deliberately absent, so the "your
ERPNext has no such field" refusal is exercised against a real absence.

## 0.7.1 — 2026-07-27

**fix: missing Python controllers for child doctypes broke `bench migrate`.**

v0.7.0 shipped `Asset Cost Center Allocation` and `Asset Depreciation Posting`
with a DocType JSON, an `__init__.py`, and no `.py` module. On a live site
`bench migrate` stopped with:

```
ModuleNotFoundError: No module named
'erpnext_mcp.erpnext_mcp.doctype.asset_depreciation_posting.asset_depreciation_posting'
```

Frappe imports `<folder>/<folder>.py` for **every** DocType it loads —
`frappe.modules.utils.load_doctype_module`, reached from `get_controller`, which
migrate calls while syncing the JSON. Child tables are not an exception. Both
tables were left without a module because neither has any server-side logic;
their rules are properties of the whole table and live on the parent,
`AssetCostProfile`. That reasoning was right about where the logic belongs and
wrong about whether the file is optional. **An empty controller is mandatory.**

Nothing else about v0.7.0 changes: no tool, no schema, no behaviour. A site that
never got past the failed migrate loses nothing by upgrading straight to 0.7.1.

### Fixed

- Added `asset_cost_center_allocation.py` and `asset_depreciation_posting.py`,
  each an empty `Document` subclass with a docstring explaining why an empty
  controller is not optional.

### Added — the tests that should have caught it

The in-bench suite asserted `frappe.db.exists("DocType", …)` for all six new
doctypes and passed. That is a different question: a row can exist for a doctype
whose module cannot be imported, and the failure sat exactly in the gap between
"the JSON is there" and "Frappe can load it".

- **`tests_standalone/test_packaging.py`** — walks the app's doctype folders on
  disk and asserts each is a package Frappe could import: `__init__.py` present,
  `<folder>.py` present, the folder name equal to the scrubbed DocType name, a
  controller class named after the DocType that subclasses `Document`, the module
  set to this app, every child table flagged `istable`, and every `Table` field
  pointing at a doctype this app actually ships. No bench needed, so CI runs it
  on every push. Verified by deleting the controller again — it fails.
- **`test_frappe_can_import_every_doctypes_module`** (in-bench) — reproduces the
  regression through the exact frame at the top of the traceback,
  `load_doctype_module`, and additionally checks `get_controller` returns the
  app's class rather than silently falling back to a base `Document`, which would
  disable every validation the controller declares.
- The standalone harness no longer special-cases child tables when resolving a
  controller, so the double now imports a module where Frappe would.

902 standalone tests, all passing.

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
