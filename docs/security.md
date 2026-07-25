# Security

What this app assumes, what it defends, and what it does not.

## The one-line version

If you need to stop everything right now: open **ERPNext MCP Settings**, untick
**Enabled**, save. The next request is a 404. No restart, no token rotation, no
client reconfiguration.

---

## Threat model

**What is being protected.** Read access to a general ledger, and — where an
operator has enabled it — the ability to create, post and cancel Journal Entries.
Read access alone is serious: a chart of accounts plus balances is a fair
description of a business's finances.

**Who this defends against.**

- Anyone on the network who has not been given the bearer token.
- Anyone with the token who is calling from outside the configured CIDR range.
- A token holder attempting an operation the operator has not enabled.
- A model that misunderstands what it was asked to do and tries to post an
  unbalanced, out-of-period or duplicate entry.
- Someone with database or script access trying to quietly rewrite the audit log.

**Who this does not defend against.**

- **A token holder inside the allowlist.** They can call every enabled tool. That
  is what the token is *for*; the controls on offer are which tools exist and what
  the audit log records, not a per-caller identity model.
- **A System Manager on the site.** They can change every setting here, generate a
  new token and enable every write tool. This app adds no privilege that a System
  Manager did not already have — a System Manager can post a Journal Entry
  directly.
- **A compromised Frappe site.** If an attacker can run code on the site, this
  app's gates are the least of the problem.
- **Traffic interception.** This app does not terminate TLS. It rides on your
  site's existing reverse proxy; if that serves plain HTTP, the bearer token
  crosses the network in the clear.

---

## The three gates

Every request runs all three, in this order, and all three must pass.
`erpnext_mcp/security.py` is the whole of it.

### 1. Master switch → 404

`enabled` off, or no token stored, returns **404**, not 403. A disabled endpoint
should be indistinguishable from an app that was never installed, so a scanner
learns nothing from probing the path. There is no configuration in which this
endpoint answers without a secret.

The settings form refuses to save `enabled` without a token, so the
"enabled-but-tokenless" state only arises from a direct database edit or a
half-finished restore — exactly when you least want the endpoint answering.

### 2. Bearer token → 401

`Authorization: Bearer <token>`, compared with `hmac.compare_digest` so the
comparison takes the same time whatever the guess. 48 hex characters (~192 bits)
from `frappe.generate_hash`.

Stored in a **Password** field, which means Frappe keeps it in the encrypted auth
table rather than in the document row. Consequences worth knowing:

- Nothing reads it back out — not a tool, not the settings form, not the
  `selftest` diagnostic, which reports only *whether* a token exists.
- It is shown to the operator exactly once, in a dialog, at generation. That is
  why the button says **Generate New Token** and not **Show Token**.
- A site restored without its `encryption_key` cannot decrypt it. That fails
  closed: `auth_token()` returns `""`, and the endpoint answers 404.
- Generating is the same operation as rotating. The old token stops working the
  moment the new one saves, which is the behaviour you want when the reason you
  pressed the button is that the old one leaked.

**On `X-MCP-Token`.** `Authorization: Bearer` is the primary header and what every
MCP client sends. It is also the header Frappe's own auth layer inspects, and a
`Bearer` value there is routed into Frappe's OAuth2 validator; an unknown token is
swallowed and the request continues as Guest, which is what this app needs. That is
incidental framework behaviour rather than a promise, so `X-MCP-Token: <token>` is
accepted as an equivalent — the supported escape hatch if a Frappe version ever
starts rejecting unknown bearer tokens outright. Use `Authorization` unless you
have hit that problem.

### 3. Network allowlist → 403

The caller's address must fall inside one of the CIDR blocks in **Allowed CIDRs**.

- Default: `127.0.0.1/32,::1/128,10.0.0.0/8,192.168.0.0/16,172.16.0.0/12` —
  loopback and the RFC1918 private ranges. `::1/128` is in there because on a
  modern host `localhost` resolves to IPv6 first, so a first-run
  `curl http://localhost/...` arrives from `::1`.
- **An empty list denies everyone.** A blank field is far more likely a mistake
  than an intent to publish an accounting API, so it fails closed. The settings
  form refuses to save an empty list while `enabled` is on.
- A malformed entry is skipped at request time rather than taking the whole gate
  down — but it is *also* refused at save time, which is what makes skipping
  acceptable rather than dangerous.
- IPv4 and IPv6 are matched separately: `0.0.0.0/0` does not admit an IPv6 caller.
  Add `::/0` if that is genuinely what you want.
- Widening this to `0.0.0.0/0` is something you have to type yourself.

**How the caller's address is determined.** Behind a reverse proxy `remote_addr`
is the proxy, so the real caller is only in the `X-Forwarded-For` chain. That
header is client-supplied, and bench's stock nginx *appends* to it rather than
replacing it (`$proxy_add_x_forwarded_for`), so its **leftmost** entry is whatever
the client felt like claiming. This app therefore gates on the **rightmost** entry
— the one the nearest proxy appended itself, which a client cannot forge — falling
back to `remote_addr` when the header is absent.

> **If you run more than one proxy layer**, the rightmost entry is an inner proxy
> rather than the client, and the CIDR gate stops being meaningful. On that
> topology, rely on the bearer token plus a firewall rule (`iptables`, a security
> group, a `deny` in nginx) and treat the allowlist as decoration.

Note that `frappe.local.request_ip`, which the rest of Frappe uses, is the
*leftmost* entry. This app deliberately does not use it for the gate. The IP
recorded in the audit log is the same one the gate evaluated, so a log row and a
gate decision can never disagree about who called.

### The same-origin exception

The CIDR gate is bypassed when **both** of these hold:

1. The request carries an `Origin` header whose host equals this site's host.
2. The session is a signed-in user with the **System Manager** role.

Each half closes the other's hole. `Origin` alone is worthless — any non-browser
client can send any value. The session alone would let a logged-in user's browser
be driven from a third-party page. Together they describe exactly one situation: a
page served by this site, fetched by a signed-in System Manager. That is the
operator using a console on their own Desk, and it is not something the allowlist
should be able to lock them out of.

### Rejections are opaque

Every refusal returns the same body: `unauthorized`, or `not found` for the 404
cases. The *reason* — "bearer token missing or incorrect", "caller ip 203.0.113.7
is outside allowed_cidrs" — goes to **MCP Action Log**, where the operator can read
it and the caller cannot. Telling an unauthenticated caller "your IP is fine, your
token is wrong" hands them a free oracle for exactly the two facts worth probing
for.

---

## Authorization inside the gates

### Read tools: the token is the authorization

Read tools use `frappe.db.get_all` and `frappe.get_doc`, **neither of which
consults Frappe role permissions.** A token holder can read everything the enabled
read tools cover, regardless of what roles the MCP System User has.

This is deliberate, and you should decide whether you agree with it:

- **For it:** a token holder could read the same data through Frappe's own
  `/api/resource` endpoints anyway, given a session. Role-filtered reads on an
  accounting API also mean *silently* hidden rows — a balance that is wrong with no
  indication that it is wrong, which for a ledger is worse than a refusal.
- **Against it:** it means you cannot hand out a token scoped to one company or one
  account tree.

The granularity actually on offer is the per-tool switches. Turning
`get_chart_of_accounts` and `search_accounts` off leaves a client able to answer
questions about accounts it was told about and unable to go looking for others.
If you need per-company scoping, run a second site.

### Mutating tools: Frappe permissions apply in full

Every write goes through `frappe.get_doc(...).insert()` / `.submit()` /
`.cancel()`, which run the acting user's permission checks along with doctype
validation, the fiscal-year check, period-closing vouchers, account freezing,
mandatory dimensions and every `on_submit` hook. There is no raw SQL anywhere in
this app, and there should never be: the day an MCP tool writes a GL Entry
directly is the day it can corrupt a ledger.

So the MCP System User's roles **do** bound what mutations can do. Give it the
narrowest set that works — **Accounts User** is normally enough.

---

## Per-tool switches

Fifteen tools, fifteen switches, on a form only System Manager can open.

**All five mutating tools ship off.** A fresh install cannot change a single
document. A call to a disabled tool is refused *by name*, before its arguments are
looked at, so nothing about the arguments — valid or not — can leak back. A
disabled tool does not appear in `tools/list` either: a model cannot be tempted by
a tool it cannot see.

**The split that matters.** `create_journal_entry` only ever produces a draft
(`docstatus=0`), and there is no argument that makes it submit. Posting is
`submit_journal_entry`, a separate tool with a separate switch that takes a name
and nothing else — it cannot create the entry it submits. So:

| Enabled | What an AI client can do |
| --- | --- |
| neither | Nothing. Reads only. |
| `create_journal_entry` only | Propose entries all day. Not one touches a balance. A human reviews and submits. |
| both | Post to the general ledger unattended. |

The middle row is the one most operators want, and it is the reason the two are
not one tool.

**Read tools are switchable too**, for surface control rather than security. An
operator running this for bank reconciliation can turn the chart-of-accounts tools
off and stop a client wandering through the whole ledger for context it does not
need.

---

## The audit log

Every call gets a row in **MCP Action Log** — reads, writes, refusals and calls to
tools that do not exist.

**Why reads.** A read tool cannot corrupt the ledger, but the interesting question
after the fact is rarely "what did it change" — it is "what did it see". A log that
only records mutations cannot tell you whether a client enumerated every account
before it was switched off.

**Append-only, and meant.** The doctype grants System Manager read and delete but
not write, and the controller refuses an update even from a script or a console —
a UI-only restriction is not an audit trail. Delete is allowed on purpose so a busy
site can be pruned; Frappe records every deletion in its own Deleted Document
doctype, so a pruned row still leaves a trace.

**It survives a rollback.** A Frappe request is one transaction. If a mutating tool
half-wrote a document and then failed, that write must be rolled back — and a naive
audit row would go with it, losing exactly the record you most want. So the order
is: roll back first, then insert the failure row into a clean transaction and
commit it on its own.

**Redaction.** Arguments are logged verbatim except for keys naming a secret
(`token`, `password`, `secret`, `api_key`, `credential`), which are masked. No
current tool takes one; a future one might, and a log that is read-only forever is
the wrong place to discover that.

**Uninstalling drops it.** `bench uninstall-app` removes the doctype and its table.
Export first if you need the history; `before_uninstall` will remind you.

---

## What the endpoint is not

- **Not a public API.** It is one whitelisted Frappe method, intended to be reached
  over a LAN or a private tunnel. Do not add it to a public reverse-proxy path.
- **Not a second listener.** No new port, no sidecar, no process to supervise. It
  inherits your site's TLS, nginx rate limits and access logs, and it is up
  whenever the site is.
- **Not an SSE stream.** POST-only. This server never initiates a message, so
  there is nothing for a stream to carry, and a `GET` returns a 405 saying so
  rather than an idle connection that looks like it is working.
- **Not rate limited by this app.** If you want that, Frappe ships a decorator —
  add `@rate_limit(key="mcp", limit=120, seconds=60)` from `frappe.rate_limiter`
  above `handle()` in `erpnext_mcp/mcp.py`. It is left off by default because an
  MCP session legitimately makes many calls in quick succession, and a limit tuned
  for a login form would break normal use.

---

## Hardening checklist

- [ ] Narrow **Allowed CIDRs** to the subnet your client is actually on, not the
      whole of `10.0.0.0/8`.
- [ ] Create a dedicated **MCP System User** with **Accounts User** and nothing
      more. Do not leave mutations running as `Administrator`.
- [ ] Leave every mutating switch off until you have a specific need, then enable
      the narrowest one. If `create_journal_entry` is enough, do not enable
      `submit_journal_entry`.
- [ ] Turn off any read tool the client does not need.
- [ ] Serve the site over HTTPS. The bearer token is only as private as the
      transport.
- [ ] Firewall the port as well. The CIDR gate is defence in depth, not a firewall.
- [ ] Watch **MCP Action Log** for `Unauthorized` rows — those are someone probing
      a live endpoint.
- [ ] Rotate the token when a client machine is decommissioned. One button.
- [ ] Export the audit log before uninstalling.

## Reporting a vulnerability

Open a GitHub issue for anything already public. For something not yet public,
email the address in `pyproject.toml` rather than filing publicly.
