# SPDX-License-Identifier: MIT
"""Frappe app manifest.

Deliberately almost empty. This app adds its own doctypes and one whitelisted
method; it installs no `doc_events`, no overrides and no fixtures, and it adds no
field to any doctype it did not create. Installing it cannot change the behaviour
of anything already on the site, and an operator who removes it gets their site
back exactly as it was, minus this app's own records.

That last promise is why v0.7.0's asset tooling keeps its cost split and its
depreciation history in an `Asset Cost Profile` beside ERPNext's Asset rather
than in custom fields grafted onto it: a doctype of ours goes with the app, a
field on theirs does not.

v0.15.0 IS THE EXCEPTION TO THE PARAGRAPH ABOVE, AND IT IS DELIBERATE. The
compliance framework adds Custom Fields to Spray Log, to Employee, to the
BucketLog bridge and — from v0.19.3 — to Attendance: four doctypes this app did
not create, through the `after_migrate` hook. It is the only such exception, it is behind a switch an
operator can turn off, and `compliance_fields.py` argues the case at length. The
short version: compliance woven into the operational record is defensible under
audit and a shadow log beside it is not, and you cannot weave anything into a
doctype you refuse to touch. `before_uninstall` names every column that goes.

v0.17.2 ADDS THE ONE REQUEST-LIFECYCLE HOOK, and it is the other exception.
`auth_hooks` runs on every request to the site, which is as intrusive as this
file gets. It buys the only thing that makes the mobile app work through a proxy
that eats `Authorization` headers, and it is bounded to the point of dullness:
it acts on `/api/method/erpnext_mcp.api.*` and nothing else, it never overrides
an identity Frappe already established, it grants no permission of any kind, and
it cannot raise. See the note above the declaration, and `api/fallback_auth.py`.

THERE ARE EXACTLY SIX SCHEDULED JOBS, and the count is in this docstring
because it is a number somebody should have to change on purpose. Every one of
them runs on somebody's site with nobody watching, so each has had to clear the
same two bars: it writes only this app's own doctypes (or nothing at all), and
it never raises.

The first arrived in v0.14.0. Chunked uploads stage their pieces in a table so an
upload survives a worker restart, which means an upload nobody finishes leaves
rows behind. `collect_expired_sessions` deletes sessions idle for more than a day,
and it touches nothing but this app's own two staging doctypes — it reads no
ERPNext table and writes to none. It never raises: it runs beside real work, and a
sweeper that took a site's scheduler down would be worse than the litter it
removes.

It is a BACKSTOP rather than the mechanism. The same sweep runs at the top of
every `stage_file_chunk` call, which is the kairotic moment — the right time to
clear out abandoned uploads is when somebody is uploading, not at three in the
morning — and it is what keeps a bench with its scheduler switched off from
quietly accumulating ninety megabytes of a PDF nobody finished sending.

The second arrived in v0.15.0 and is the compliance alert sweep. It READS
certificates, policies, employees, blocks, cabins, filings and audits, and writes
only this app's own Compliance Alert table — so the promise about not changing how
a site behaves still holds. It also never raises.

The third arrived in v0.17.1 and is the Journal Entry drift watch. It WRITES
NOTHING — three queries and, at most, an email — so it clears a higher bar than
the one stated above rather than merely meeting it.

IT REPORTS AND IT DOES NOT REPAIR. `repair_drifted_je_attributions` exists,
works, and is deliberately not called from a schedule: repairing drift rewrites
GL rows on submitted accounting documents, and doing that on a timer with nobody
watching is the single worst thing this app could be talked into. A repair that
got it wrong would be indistinguishable from the bug it was fixing and would
have been applied to a year of ledger before anybody read the email. `drift.py`
makes the argument at length.

It exists because ACC-JV-2026-00073 was a bug whose entire character was that
nothing complained about it, and "somebody remembers to run a scan" is not a
control.

The fourth arrived in v0.17.1 with the mobile API and is the idle credential
sweep. IT IS THE ONE THAT WRITES ANOTHER APP'S TABLE — two fields on Frappe's
User, `api_key` and `api_secret` — and v0.17.0's own docstring said this app
would never install it. That reversal is argued in `tools/mobile.py` and the
short version is that the threat changed: v0.17.1 put forty live credentials in
forty pockets on the open internet, and a phone left on a truck seat and never
mentioned is the ordinary case. A credential that stops working by itself is the
only control that does not depend on somebody admitting to the loss.

It is bounded in every direction that matters. It touches only accounts this app
created and minted a key for, only where a Mobile Access Grant is Active, never
one marked persistent, and never one whose age it cannot establish. It revokes
the TOKEN and never the account — roles and entity access are untouched and the
worker needs one new QR. Every revocation writes an MCP Action Log row and the
run emails a summary, so "with nobody watching" is no longer true of it. Setting
the window to 0 on ERPNext MCP Settings switches it off.

IT IS THE CLOCK SERVING KAIROS, AND THAT IS THE WHOLE POINT OF PUTTING IT ON A
SCHEDULE. The sweep DECIDES NOTHING. Every rule it runs asks whether a condition
is true right now — is this certificate genuinely inside the lead time its
issuing body takes, is this block genuinely in spray rotation with untested
water — and an alert fires on that state rather than on the date the sweep
happened to run. Chronos gathers; kairos acts. A rule that fired because it was
the first of the month would be the other way round, and would be ignored by
March.

v0.17.1 MOVED THE SWEEP FROM NIGHTLY TO HOURLY, and the reason is the phones.
The sweep is also what makes an alert GO AWAY: `complete_farm_task` writes a
compliance record, the register moves forward, and the next sweep notices the
condition has stopped being true. That is the only honest way for an alert to
clear — nothing here dismisses one directly. Nightly meant a worker who walked a
cabin at eight in the morning saw the alert asking them to walk it for the rest
of the day, on the phone, having just done the work. Eleven rules over a farm's
registers is a cheap pass, and running it hourly costs about a second a day of
somebody's worker in exchange for a calendar that tells the truth by lunchtime.

THE SWEEP IS SAFE TO RUN AT ANY CADENCE because it is a full reconciliation
rather than an increment: every run rebuilds the whole picture and every alert is
keyed, so running it twice in a minute produces exactly what running it once
does. That property is what makes the cadence a tuning decision rather than a
correctness one.

THE FIFTH ARRIVED IN v0.19.4 AND IS THE FIRST JOB ON THIS APP'S SCHEDULE THAT
TALKS TO SOMEBODY ELSE'S SERVER. `services.weather.sweep_open_shifts` asks
Open-Meteo what the conditions are at every open shift's coordinates and appends
a `Farm Shift Weather Reading`. It writes only this app's own doctypes — the
shift's two child tables and nothing else — and it never raises, but it carries a
third obligation the other four do not: it must not be a nuisance to a free
service that asks nobody for an API key. So it caches by rounded coordinate,
skips any shift read within `fetch_interval_minutes`, and treats a 429 as an
instruction rather than a suggestion — exponential backoff per coordinate, capped
at an hour, during which no request goes out for that place at all.

IT IS THE ONLY `cron` ENTRY, and the reason it is not `hourly` is the rule it
serves. OAR 437-004-1131 asks what the conditions were across an exposure period;
an hourly reading on a nine-hour shift is nine data points and a fifteen-minute
one is thirty-six, and the difference between those two is the difference between
a sketch and a timeline. A cron expression cannot be changed from a form, which
is why `fetch_interval_minutes` exists on Weather Settings: the schedule is the
ceiling and the setting is the floor, so an operator can ask for LESS often and
gets exactly that.

IT IS ALSO SAFE TO RUN AT ANY CADENCE, for three independent reasons rather than
the alert sweep's one — the interval check skips a shift just read, the cache
answers the request that would have gone out, and `append_readings` refuses a
second row for a minute already on the timeline. A reading is immutable evidence
and is only ever appended; nothing in this app edits one.

THE SIXTH ARRIVED IN v0.19.6 AND IS THE ONE THAT DOES ARITHMETIC RATHER THAN
HOUSEKEEPING. `services.windowed_reports.recompute_kpi_history_incremental`
walks every registered financial report and every company, and fills in the
trailing-twelve-month snapshots the cache is missing. It writes only this app's
own `Financial KPI History` and never raises.

IT IS ONE JOB THAT ITERATES, NOT ONE CRON PER KPI, and that is a deliberate
constraint rather than an implementation detail. The Financial KPI Framework
will add KPIs as data, and a scheduler with an entry per KPI is a scheduler
nobody can read and a bench that wakes fifteen times a night to do fifteen
things that could have been done once. Adding a report adds no line to this
file.

IT IS AT `0 2 * * *` AND IS THE SECOND `cron` ENTRY. `daily` would be tidier and
is wrong: Frappe's `daily` fires whenever the day's first scheduler tick lands,
which on a farm bench is during the morning, and this is the one job here that
can genuinely take minutes on a large ledger. Two in the morning is chosen for
what it is NOT competing with.

IT IS SAFE TO RUN AT ANY CADENCE, for the reason the alert sweep is: it is
incremental against what is already cached, so a run with nothing missing does
nothing at all. And it is the only scheduled job in this app with a kill switch
of its own — `enable_kpi_history_sweep` on ERPNext MCP Settings — because it is
the only one whose cost scales with the size of somebody's books rather than
with the number of their cabins. Turning it off costs speed and nothing else:
every report still answers, from a cold cache, saying how much history it had to
leave out.
"""

app_name = "erpnext_mcp"
app_title = "ERPNext MCP"
app_publisher = "Tim Polehn"
app_description = "Model Context Protocol (MCP) server for ERPNext."
app_email = "polehntim@gmail.com"
app_license = "MIT"

#: This app reads and writes ERPNext accounting doctypes (Account, GL Entry,
#: Journal Entry, Bank Transaction), so `bench install-app` should refuse on a
#: site that has only Frappe rather than fail later at the first tool call.
required_apps = ["erpnext"]

after_install = "erpnext_mcp.install.after_install"
before_uninstall = "erpnext_mcp.install.before_uninstall"

#: Fill in defaults for any settings field added by a future version, on every
#: migrate. Idempotent, and never overwrites an operator's choice.
after_migrate = "erpnext_mcp.install.after_migrate"

#: See the module docstring. Every job writes ONLY this app's own doctypes — the
#: drift watch writes nothing at all — and none of them ever raises: they run on
#: somebody's scheduler beside their real work.
#:
#: BARE DOTTED PATHS, like every other hook in this file. `scheduler_events`
#: hands each entry to `frappe.get_attr` exactly as `jinja` does, so the colon
#: that took a site down in v0.14.0 would take it down from here too.
scheduler_events = {
	#: v0.19.4. The weather sweep, and the only entry that is not one of Frappe's
	#: named intervals. `hourly` would be the tidier declaration and the wrong
	#: record: -1131 asks what the conditions were across an exposure period, and
	#: nine readings on a nine-hour shift is a sketch where thirty-six is a
	#: timeline. See the docstring above for why the cadence is the ceiling and
	#: `Weather Settings.fetch_interval_minutes` is the floor.
	"cron": {
		"*/15 * * * *": [
			"erpnext_mcp.services.weather.sweep_open_shifts",
		],
		#: v0.19.6. The KPI history sweep, at two in the morning. See the
		#: docstring above for why it is a cron rather than `daily` — Frappe's
		#: `daily` fires on the day's first tick, which on a farm bench is during
		#: the morning, and this is the one job here that can take minutes on a
		#: large ledger. ONE ENTRY THAT ITERATES: it walks every registered
		#: report and every company, so adding a KPI adds no line to this file.
		"0 2 * * *": [
			"erpnext_mcp.services.windowed_reports.recompute_kpi_history_incremental",
		],
	},
	"hourly": [
		"erpnext_mcp.alerts.sweep",
	],
	"daily": [
		"erpnext_mcp.tools.uploads.collect_expired_sessions",
		"erpnext_mcp.tools.mobile.sweep_idle_grants",
	],
	"weekly": [
		"erpnext_mcp.drift.scan",
	],
}

#: ONE Jinja method: the amount-in-words the check Print Format renders.
#:
#: `create_check_print_format` writes a Print Format that has to say an amount
#: the way a US check says one — "One Thousand Two Hundred Thirty-Four and
#: 56/100", with no currency word, because the stock says DOLLARS already.
#: Frappe's own `money_in_words` says it differently and varies with the site's
#: number format, so the check template calls ours.
#:
#: A BARE DOTTED PATH, AND NOTHING ELSE. THIS LINE TOOK A SITE DOWN IN v0.14.0.
#: Frappe's `jinja` hook hands each entry STRAIGHT to `frappe.get_attr` and takes
#: the Jinja global's name from the callable's own `__name__`. The
#: `"<name>:<path>"` form belongs to the OLDER `jenv` hook, whose reader splits
#: on the colon first. v0.14.0 wrote jenv's syntax under jinja's key, so
#: `frappe.get_attr` was handed the whole string, split it on the first dot
#: looking for an app name, and threw `AppNotInstalledError: App
#: erpnext_mcp_amount_in_words:erpnext_mcp is not installed`.
#:
#: Frappe builds the Jinja environment to render the ERROR page too, so that
#: raised inside the handler for its own exception: **every page on the site
#: returned 500, including the one that would have explained why**. A cosmetic
#: string on a print format took out the whole UI. There is a test —
#: `test_hooks.py` — that now resolves every path in this file and refuses a
#: colon in a `jinja` entry, because reading this line correctly is evidently not
#: something to leave to a reader.
#:
#: `jenv` is NOT declared. It is the deprecated spelling with a DIFFERENT
#: syntax, so a second declaration would be a second chance to get a format
#: wrong for a version this app's compatibility table does not claim (the
#: `jinja` hook has existed since v14, and v14 is the floor).
#:
#: The name is namespaced by naming the FUNCTION that way, since the hook no
#: longer gets a say in it — a Jinja global lands in a namespace shared with
#: Frappe, ERPNext and every other installed app. The check template guards with
#: `is defined` and falls back to `frappe.utils.money_in_words` regardless: a
#: check with no amount in words is not a check, and a valid check with wordier
#: text beats a blank line.
jinja = {"methods": ["erpnext_mcp.render.checks.erpnext_mcp_amount_in_words"]}

#: ENTITY SCOPING FOR THE TWO DOCTYPES FRAPPE'S OWN MECHANISM CANNOT REACH.
#: v0.17.1. See `permissions.py`, which argues it at length.
#:
#: The short version: a User Permission on Company restricts every document that
#: LINKS to a Company, and `Housing Assignment` and `Family` are the only two
#: doctypes this app created that do not. `Housing Assignment` is READ-granted to
#: four roles and FULL to a fifth, so before this hook a field worker scoped to
#: one entity could list every camp bed assignment on the site.
#:
#: THESE TWO KEYS WERE FORBIDDEN UNTIL v0.17.1, on the grounds that "this app
#: changes nobody's visibility". The invariant actually worth keeping is about
#: OTHER PEOPLE'S doctypes — the blanket ban was a proxy for it, and the proxy
#: was wrong for a doctype this app ships. `test_permissions.py` now asserts the
#: narrower and stronger rule: EVERY doctype named here is one this app created.
#: Restricting a doctype that would not exist without this app cannot change how
#: a site behaved before it was installed.
#:
#: Both handlers return the unrestricted answer on any error rather than failing
#: closed. A query-conditions hook that raises does not fail one query — it fails
#: every list view of that doctype for everybody, including the Desk page an
#: operator would open to fix it.
permission_query_conditions = {
	"Housing Assignment": "erpnext_mcp.permissions.housing_assignment_query",
	"Family": "erpnext_mcp.permissions.family_query",
}

#: The same two, for the reads a query condition never sees — `frappe.get_doc`
#: and the form view. A doctype filtered out of a list but readable at
#: `/app/housing-assignment/HA-0007` is not scoped, it is merely tidy.
has_permission = {
	"Housing Assignment": "erpnext_mcp.permissions.housing_assignment_has_permission",
	"Family": "erpnext_mcp.permissions.family_has_permission",
}

#: FORM JAVASCRIPT FOR SEVEN DOCTYPES, ALL SEVEN OF THEM THIS APP'S OWN. v0.32.0.
#:
#: THE INVARIANT THIS FILE KEEPS IS ABOUT OTHER PEOPLE'S DOCTYPES, and every key
#: below is a doctype that would not exist if this app were not installed. An
#: operator who removes the app loses these forms entirely, so a script attached
#: to one cannot change how anything they still have behaves. That is the same
#: line `permission_query_conditions` draws and `test_hooks.py` now asserts here
#: too: every doctype named in this hook is one this app created.
#:
#: WHAT THE SCRIPTS DO: draw a Leaflet map of whatever the record knows about
#: where it is. A boundary nobody can look at is a boundary nobody checks, and
#: the failure that produces is specific — a block traced with two vertices
#: transposed passes every validation this app makes and is obviously wrong to
#: anybody who sees it drawn.
#:
#: WHAT THEY DO NOT DO: write. There is no drag-to-move marker, no draw-a-polygon
#: tool and no save path of any kind. A boundary is compliance evidence, it is
#: set through the three boundary tools, and those validate the shape, refuse a
#: self-intersection, compare the area against the recorded acreage and recompute
#: every derived field. A map that could nudge a vertex would be a way round all
#: of that with no validation and no audit row.
#:
#: `geo_map_widget.js` IS LISTED FIRST IN EVERY ENTRY because Frappe concatenates
#: the files in order into the form's script, and the per-doctype file calls into
#: the widget at evaluation time. The widget itself is idempotent — it returns
#: early when it is already installed — which is what makes listing it seven
#: times cost nothing on a session that opens seven forms.
#:
#: THE LIBRARY COMES FROM A CDN AND THE TILES FROM OPENSTREETMAP, so a bench with
#: no outbound internet gets no map. That case is handled rather than left to
#: fail: the section says the library could not be reached and prints the
#: coordinates underneath. The record is the coordinates; the map is a reading of
#: them, and losing the reading must not look like losing the record.
doctype_js = {
	"Parcel": ["public/js/geo_map_widget.js", "public/js/parcel_map.js"],
	"Field": ["public/js/geo_map_widget.js", "public/js/field_map.js"],
	"Irrigation Zone": ["public/js/geo_map_widget.js", "public/js/irrigation_zone_map.js"],
	"Housing Unit": ["public/js/geo_map_widget.js", "public/js/housing_unit_map.js"],
	"Asset Register": ["public/js/geo_map_widget.js", "public/js/asset_register_map.js"],
	"Farm Shift": ["public/js/geo_map_widget.js", "public/js/farm_shift_map.js"],
	"Farm Task": ["public/js/geo_map_widget.js", "public/js/farm_task_map.js"],
}

#: THE ONLY REQUEST-LIFECYCLE HOOK THIS APP INSTALLS. v0.17.2, and it is here
#: because the Tailscale `serve`/`funnel` proxy removes the `Authorization`
#: header — proven three ways on 2026-08-01, including from inside the tailnet,
#: so it is the proxy step and not the public edge. Every Farm Ops call arrived
#: as Guest and Frappe rendered the Desk's `/me` page at it.
#:
#: IT COULD NOT HAVE BEEN DONE IN THE ENDPOINT. `is_whitelisted` refuses a Guest
#: BEFORE the whitelisted method is dispatched, so a fallback check written
#: inside `api/guard.py` would sit behind a door that never opens. `auth_hooks`
#: is Frappe's own extension point for exactly this: `validate_auth()` runs it
#: after the session is established and before the handler decides whether the
#: caller may reach the method, which is the same window Frappe settles identity
#: in for `Authorization: token`.
#:
#: WHAT IT DOES ON A REQUEST THAT IS NOT ONE OF THIS APP'S ELEVEN MOBILE PATHS:
#: reads one attribute and returns. It cannot raise — `validate_auth` runs on
#: every request to the site, so an exception here would be an exception on
#: every Desk page of every installed app rather than a failure of one endpoint.
#: It never overrides an identity Frappe already established, and it grants
#: nothing: `guard.endpoint` still runs all seven of its checks on the user it
#: resolves. See `api/fallback_auth.py`, which argues the whole thing.
#:
#: A BARE DOTTED PATH IN A LIST, which is the shape `frappe.get_hooks` returns
#: and `validate_auth_via_hooks` iterates. Same `frappe.get_attr` rule as every
#: other path in this file: no colon, no name prefix.
auth_hooks = ["erpnext_mcp.api.fallback_auth.authenticate"]
