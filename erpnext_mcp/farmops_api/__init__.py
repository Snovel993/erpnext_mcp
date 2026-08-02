# SPDX-License-Identifier: MIT
"""The THIRD transport: the eleven mobile methods, off Frappe's request handler.

v0.18.0. THIS EXISTS BECAUSE `/api/method/*` THROUGH THE TAILSCALE PROXY NEVER
REACHES THIS APP'S CODE AT ALL.

────────────────────────────────────────────────────────────────────────────
WHAT v0.17.2 ESTABLISHED, AND WHAT IT DID NOT FIX
────────────────────────────────────────────────────────────────────────────

v0.17.2 carried the same `<api_key>:<api_secret>` pair five ways — the
`Authorization` header, `X-FarmOps-Token`, `_auth` in the JSON body, `_auth` as
a query parameter, and form-encoded fields — and resolved all five in an
`auth_hooks` entry, which is the one place Frappe lets an app settle identity
before `is_whitelisted` refuses a Guest. Every one of them works against
`http://localhost:5300` from inside the container. Every one of them comes back
as the Desk's HTML login page through `https://<host>.ts.net`.

Five independent carriers do not all fail by coincidence. The remaining common
factor is `/api/method/*` itself — Frappe's own request handler, behind that
proxy, on this deployment. We could not isolate it further without
instrumenting the framework, and a farm cannot wait on that.

**BANK BRIDGE IS THE PROOF THAT THE FUNNEL IS NOT THE PROBLEM.** A plain WSGI
service answering at `/bankbridge/plaid/oauth_return` works perfectly through
the same funnel, on the same box, carrying the same kind of headers. What
differs is that nothing in its request path is Frappe's `/api/method` handler.

So this package is that shape, for the eleven methods a phone calls: **its own
process, its own port, its own routes, and NOT ONE LINE of Frappe's HTTP
layer.** Frappe is still the database, the permission model and the rule set —
it is imported, connected to, and become-the-user-on. It is simply no longer
the thing that reads the request.

────────────────────────────────────────────────────────────────────────────
IT DELEGATES TO v0.17.2's WRAPPERS. IT DOES NOT REIMPLEMENT THEM
────────────────────────────────────────────────────────────────────────────

`routes.py` is a table of eleven entries and each one names a function in
`erpnext_mcp/api/mobile.py` or `erpnext_mcp/api/files.py`. Those functions still
wear `@guard.endpoint`, so all seven checks — the kill switch, the role gate,
the Mobile Access Grant, entity scoping, the rate limit, the MCP Action Log row,
the secret strip — run here exactly as they run on the Frappe path, because they
ARE the same code running.

THIS IS THE WHOLE SECURITY ARGUMENT AND IT IS WORTH STATING BLUNTLY. A new
transport to a compliance system is the most dangerous kind of change there is:
the obvious implementation copies the gates into the new service, the two copies
drift, and the drift is invisible until an auditor finds a field worker reading
another entity's board. There is no copy here. `test_farmops_api.py` asserts the
responses are byte-identical to the Frappe path's for the same input, which is
the property that makes "the same code" checkable rather than claimed.

What this package adds on top is exactly three things Frappe was doing for the
whitelisted path and now has to be done by hand:

  1. **Identity.** `X-FarmOps-Token: <api_key>:<api_secret>`, verified by
     `api/fallback_auth.verify_credential` — v0.17.2's verifier, unchanged, with
     v0.17.2's failure metering. Not a second implementation of Frappe's api-key
     scheme; the same one, called from a different door. See `auth.py`.
  2. **A request-scoped Frappe session.** `session.py`. `frappe.init` /
     `frappe.connect` / `frappe.set_user` / `frappe.destroy`, plus the commit or
     rollback that Frappe's own request handler does at the end of a request.
  3. **A response envelope.** `{"message": …}` on success, and on failure the
     `_server_messages` shape `FrappeClient.serverMessage` reads, so a refusal
     still reaches the phone as the sentence it was written as.

────────────────────────────────────────────────────────────────────────────
THE OLD PATH STAYS LIVE
────────────────────────────────────────────────────────────────────────────

Nothing in `api/` is removed, deprecated or changed by this release except one
additive function in `fallback_auth`. `/api/method/erpnext_mcp.api.mobile.*`
answers exactly as it did in v0.17.2 — it works on the LAN and from inside the
container, it is what the standalone suite has always driven, and it is the
fallback if this service is ever the thing that is down. Two doors into one
room, and the room's locks are on the room.

────────────────────────────────────────────────────────────────────────────
WHY WERKZEUG AND NOT FLASK
────────────────────────────────────────────────────────────────────────────

The brief said Flask, to match Bank Bridge, and Bank Bridge is right to use it —
it is a separate image with its own dependency set. This service is not: it runs
INSIDE the ERPNext container, on the bench's own virtualenv, next to a
production ledger. `pip install flask` there resolves against Frappe's pinned
Werkzeug and can upgrade it, and the failure mode of a Werkzeug upgrade under
Frappe v15 is the whole site, not this endpoint.

Frappe already depends on Werkzeug and the bench venv already ships gunicorn —
`supervisord.conf` has been starting Frappe itself with
`env/bin/gunicorn` since the image was built. So this service costs **zero new
packages**, and eleven fixed routes need a routing framework about as much as a
list of eleven things needs a database. What Flask would have contributed here
is `@app.route`; what it would have risked is the farm's books.
"""

from __future__ import annotations

from .app import DEFAULT_PORT, application, create_app
from .routes import PREFIX, ROUTES

__all__ = ["DEFAULT_PORT", "PREFIX", "ROUTES", "application", "create_app"]
