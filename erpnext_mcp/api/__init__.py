# SPDX-License-Identifier: MIT
"""The SECOND transport, and the only one a phone speaks.

v0.17.1. Sprint 9 shipped two halves of one feature that could not talk to each
other. Wave A published every mobile capability as an MCP tool behind
`/api/method/erpnext_mcp.mcp.handle` — a JSON-RPC envelope, a shared
`X-MCP-Token`, a CIDR allowlist and a per-tool kill switch. Wave B wrote an iOS
client that calls plain Frappe whitelisted methods at
`/api/method/erpnext_mcp.api.mobile.<method>` carrying nothing but
`Authorization: token <api_key>:<api_secret>`. Both are defensible. They are not
the same protocol, so every call after enrolment returned 404 and the app said
"That task no longer exists".

This package is the bridge, and it is deliberately NOT a general one.

────────────────────────────────────────────────────────────────────────────
WHAT MAKES THIS DIFFERENT FROM THE MCP ENDPOINT, AND WHY IT MATTERS
────────────────────────────────────────────────────────────────────────────

`security.authorize()` DOES NOT RUN HERE. It cannot: it is called by
`mcp.handle`, and a `@frappe.whitelist()` method reached directly never passes
through it. So everything that endpoint relies on is absent —

  * no `X-MCP-Token` (the phone has never had one and should not: a shared
    secret distributed to forty devices is a secret in name only),
  * no CIDR allowlist (the whole point is a phone on LTE, outside every
    RFC1918 range the default list contains),
  * no `allow_<tool>` switch (those govern what the AI may do; a mobile worker
    is a different principal and gets a different gate).

That is a real, new attack surface, and pretending otherwise would be the
dangerous move. So the gate is rebuilt HERE, per call, in `guard.py`, and it is
stricter than the MCP one in the ways that matter for an endpoint on the open
internet:

  1. a global kill switch (`farm_ops_mobile_enabled`) honoured on every call,
  2. a role gate — one of the field roles, not merely "a valid login",
  3. a Mobile Access Grant in Active state — an ENROLLED device, not any user
     who happens to hold an API key, which is why an admin cannot call these
     without granting themselves mobile access on purpose,
  4. entity scoping derived from the caller's own Company User Permissions, on
     every argument and every row returned,
  5. a rate limit per user per method,
  6. an MCP Action Log row for every call including the refused ones,
  7. secret stripping on every response.

THE PERIMETER IS NOW THE FUNNEL. With the CIDR gate gone from this path, what
stands between the internet and these methods is Frappe's own token auth plus
the seven checks above. `docs/security.md` and `RELEASES/v0.17.1.md` say so
plainly; the Tailscale Funnel path list is deliberately narrow for the same
reason.

…AND THE FUNNEL TURNED OUT TO BE EATING THE CREDENTIAL. v0.17.2. Frappe's own
token auth is the first item in that sentence, and it never ran: the Tailscale
`serve`/`funnel` proxy removes the `Authorization` header, so every call arrived
as Guest and was refused by `is_whitelisted` before the method — HTTP 200, the
Desk's `/me` page, no JSON. `fallback_auth.py` re-establishes the same identity
from `X-FarmOps-Token` or from `_auth` in the POST body, using Frappe's own
api-key scheme, as an `auth_hooks` entry (which is where it has to be: the
refusal happens in the framework, not in this package). The seven checks are
unchanged and run on whichever door the caller came in through.

────────────────────────────────────────────────────────────────────────────
THE SURFACE IS A CLOSED LIST OF ELEVEN METHODS
────────────────────────────────────────────────────────────────────────────

Nine in `mobile.py`, two in `files.py`, and they are exactly the eleven
`FarmOpsKit/Sources/FarmOpsKit/Networking/MobileAPI.swift` names. The app has
two hundred-odd MCP tools; `convey_parcel`, `create_journal_entry`,
`import_chart_of_accounts` and `disable_je_gate` are among them and NONE of them
is reachable from here. There is no dispatcher, no method-name argument and no
registry lookup in this package — a wrapper exists or the path 404s, which is
the property that makes the list auditable by reading it.

Each wrapper is thin on purpose: validate, delegate, shape, return. The rules
about who may claim what, the concurrent-claim limit and the evidence contract
all stay in `tools/dispatch.py`, because they ARE `tools/dispatch.py`. A wrapper
with its own copy would be a second set of compliance rules to keep in step, and
they would drift.
"""
