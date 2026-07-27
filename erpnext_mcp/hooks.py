# SPDX-License-Identifier: MIT
"""Frappe app manifest.

Deliberately almost empty. This app adds its own doctypes and one whitelisted
method; it installs no `doc_events`, no scheduler jobs, no overrides and no
fixtures, and it adds no field to any doctype it did not create. Installing it
cannot change the behaviour of anything already on the site, and an operator who
removes it gets their site back exactly as it was, minus this app's own records.

That last promise is why v0.7.0's asset tooling keeps its cost split and its
depreciation history in an `Asset Cost Profile` beside ERPNext's Asset rather
than in custom fields grafted onto it: a doctype of ours goes with the app, a
field on theirs does not.
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
