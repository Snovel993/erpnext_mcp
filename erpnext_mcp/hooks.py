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
compliance framework adds Custom Fields to Spray Log, to Employee and to the
BucketLog bridge — three doctypes this app did not create — through the
`after_migrate` hook. It is the only such exception, it is behind a switch an
operator can turn off, and `compliance_fields.py` argues the case at length. The
short version: compliance woven into the operational record is defensible under
audit and a shadow log beside it is not, and you cannot weave anything into a
doctype you refuse to touch. `before_uninstall` names every column that goes.

THERE ARE EXACTLY FOUR SCHEDULED JOBS, and the count is in this docstring
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
