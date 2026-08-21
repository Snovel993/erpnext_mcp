# Farm overview — `/app/farm-overview`

Every boundary and every building this app knows about, on one satellite map.

This app has stored polygons since v0.12.0 and drawn them since v0.32.0, always
one record at a time, on the form of that record. Open a Field, see that block;
open the next Field, see that block. What was missing was **any way to see two of
them at once** — which is the same as saying that the whole class of mistake that
only exists *between* records has never once been visible.

A block traced twice under two names. A zone drawn on the neighbour's ground.
Two parcels overlapping by four acres. A cabin whose GPS was typed with the
longitude positive. Every one of those passes every validation this app makes,
is invisible on a form, and is obvious to anybody who sees the farm drawn.

---

## Getting there

`/app/farm-overview`, or type **Farm Overview** in the awesomebar.

The Page record ships with the app and appears after `bench migrate`. Like
`/app/mobile-onboarding`, it is **not** added to a Workspace or a sidebar group
by the installer. Bookmark the route, or add a shortcut to a Workspace of your
own, and it stays yours — a shortcut this app wrote into your Desk is one you
would have to keep deleting.

`bench build` matters here as well as `bench migrate`: the page reads the Leaflet
bootstrap out of this app's own asset path. A page that says *"the map widget
loaded but is an older build"* is a build that has not run — that case has its
own sentence rather than looking like a network failure.

---

## What is on it

| Layer | Drawn as | Comes from |
|---|---|---|
| **Parcels** | purple, dashed, faint | `Parcel.boundary_geojson` |
| **Fields** | green | `Field.boundary_geojson` |
| **Irrigation zones** | blue | `Irrigation Zone.boundary_geojson` |
| **Structures** | orange pins | `Housing Unit.gps_latitude` / `gps_longitude` |

Cabins, bath houses, kitchens, barns and shops are all Housing Units, so they are
all pins.

The layers are drawn largest first so a zone inside a block inside a parcel is
still clickable rather than buried under its own parent. **Colour means a
register here**, which is the opposite of what it means on a form: on a Field
form the layers are one record's shape and its container, so colour has to say
which one you may change. Nothing on this page is editable.

Satellite imagery is the default base layer, with a street map one click away in
the layer control at the top right. An orchard block's corner is a change in
canopy, a headland or a road edge — visible in imagery and not on a street map.

Click any polygon or pin for the record's name, what it is, both acreages, and a
link to open the record.

---

## The numbers under the map

Each one is a gap somebody can close, which is why they are printed rather than
rolled into a total.

**"Fields — 31 (9 without a boundary)".** *Forty blocks* and *forty blocks, nine
of them never traced* are different answers, and only the second one is a job.
A map that showed thirty-one and said nothing would quietly lie about the size of
the farm.

**"N boundaries are stored but could not be read".** A row whose
`boundary_geojson` is not valid GeoJSON is listed by name, with the reason and a
link. It is not dropped: a boundary that silently does not draw looks exactly
like a block that was never traced, and those are opposite problems with opposite
fixes. Usually it is a paste that lost its closing brace, or a file that was
half-written.

**"Structures — 7 (4 without a position)".** A Housing Unit with no GPS is a
number here and not a pin at `0, 0`. Null island is a real place in the Gulf of
Guinea, and a map that flies there looks exactly like a map that works.

**"Field reached the 500-row ceiling".** The map draws at most 500 rows of any
one register. Past that it says so rather than stopping quietly.

**"This login may not read: Housing Unit".** See *Permissions*.

---

## What it does not do

**It does not edit anything.** There is no draw tool on this page and no save
path. That is deliberate: a boundary is set through `set_parcel_boundary`,
`set_field_boundary` or `set_zone_boundary`, and what those do is compare the
polygon against **one record's** recorded acreage and refuse a disagreement past
a quarter. A map of forty blocks has no record in front of it, so a draw tool
here would be a draw tool with nothing to check the shape against.

Trace and edit boundaries on the Parcel, Field and Irrigation Zone forms, which
have had a draw tool since v0.33.0. Every popup on this page links to one.

**It does not need shapely or h3.** The six geospatial *tools* do, and a bench
without them loses those tools by name — correctly, because they compute areas
and containment. This page only reads shapes that are already stored, so it keeps
working on a bench that cannot compute anything about them.

**It does not need the internet to be useful.** Leaflet and the tiles come from a
CDN. Where they cannot be reached the page prints the farm as a table of names,
registers and centroids, every row linking to its record. The records are the
coordinates; the map is a reading of them.

---

## Permissions

The gate is Frappe's own **read permission on each register**, asked one register
at a time.

A register this login may not read is **named at the top of the page and its
layer is left off** — the page still opens. An office manager who may read Fields
and Parcels but not the housing register gets the map with the buildings missing
and a line saying so, rather than a page that refuses to load. The layer is
absent rather than shown as empty on purpose: an entry reading *"Parcels — 0"*
would say the farm has no titles registered, which is a different and much worse
claim.

**Multi-entity sites** get a picker at the top left. It offers exactly the
Companies this login may read — one `has_permission` check per Company, which is
where a User Permission scoping somebody to one entity actually bites, since all
four registers hang off `owning_entity`. An entity that is not in the picker is
not reachable by naming it in the request either.

Nothing on this page is gated on a *role*: the Page record ships with an empty
role list, because a standard Page is rewritten from this app's JSON at every
`bench migrate` and a role list stored there is a decision an operator makes and
then silently loses.

One thing this does **not** do, said here rather than discovered: the four
register tools read with `frappe.db.get_all`, which does not apply User
Permissions to individual rows. The company filter is the gate doing the work.

---

## Recording a boundary from the phone

New in v0.110.0 and the other half of the same release:
`set_field_boundary`, `set_zone_boundary` and `set_parcel_boundary` are on the
mobile API, so a boundary walked with a handset can be saved from the block
rather than typed at a desk.

A walked boundary is a ring of GPS fixes taken by somebody standing on the
corner, which for tree fruit is a better line than one traced off imagery shot in
another season. Every check the Desk gets still runs, including the one that
matters most: **a walk whose enclosed area disagrees with the recorded acreage by
more than a quarter is refused**, with both figures named. That is what catches a
walk that cut a corner, stopped early, or lost fixes in a pocket — the polygon it
produces is perfectly valid and is about a different piece of ground.

The routes are gated on **Farm Manager**. `dry_run` computes everything and
writes nothing, which is the argument to send while the operator is still
standing in the block.

Boundaries saved that way appear on this page on the next load — nothing here is
cached, and the page re-reads every time you come back to it.
