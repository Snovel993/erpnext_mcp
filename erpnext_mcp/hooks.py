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

THERE IS EXACTLY ONE SCHEDULED JOB, AND IT ARRIVED IN v0.14.0. Chunked uploads
stage their pieces in a table so an upload survives a worker restart, which means
an upload nobody finishes leaves rows behind. `collect_expired_sessions` deletes
sessions idle for more than a day, and it touches nothing but this app's own two
staging doctypes — it reads no ERPNext table and writes to none, so the promise
above still holds. It never raises: it runs beside real work, and a sweeper that
took a site's scheduler down would be worse than the litter it removes.

It is a BACKSTOP rather than the mechanism. The same sweep runs at the top of
every `stage_file_chunk` call, which is the kairotic moment — the right time to
clear out abandoned uploads is when somebody is uploading, not at three in the
morning — and it is what keeps a bench with its scheduler switched off from
quietly accumulating ninety megabytes of a PDF nobody finished sending.
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

#: See the module docstring. Touches this app's two staging doctypes and nothing
#: else, and never raises.
scheduler_events = {
	"daily": ["erpnext_mcp.tools.uploads.collect_expired_sessions"],
}

#: ONE Jinja method, namespaced so it cannot collide with anything.
#:
#: `create_check_print_format` writes a Print Format that has to render an amount
#: the way a US check writes one — "One Thousand Two Hundred Thirty-Four and
#: 56/100", with no currency word, because the stock says DOLLARS already.
#: Frappe's own `money_in_words` says it differently and varies with the site's
#: number format, so the check template calls ours.
#:
#: DECLARED UNDER BOTH KEYS ON PURPOSE. Frappe renamed this hook from `jenv` to
#: `jinja`, and which one a bench reads depends on its version. An unrecognised
#: hook key is ignored, so declaring both costs nothing and means the method is
#: there on either. The template falls back to `frappe.utils.money_in_words` if
#: it is somehow there on neither — a check with no amount in words is not a
#: check, and a valid check with wordier text beats a blank line.
jinja = {"methods": ["erpnext_mcp_amount_in_words:erpnext_mcp.render.checks.amount_in_words"]}
jenv = jinja
