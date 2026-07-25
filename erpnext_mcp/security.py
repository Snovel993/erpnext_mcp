# SPDX-License-Identifier: MIT
"""The transport gates: master switch, bearer token, network allowlist.

Three checks run before any tool is looked up, in this order, and all three
must pass:

  1. MASTER SWITCH. `enabled` off → HTTP 404. Not 403: a disabled endpoint
     should be indistinguishable from an app that was never installed, so a
     scanner learns nothing from probing the path.

  2. BEARER TOKEN. Compared with `hmac.compare_digest`, so the comparison takes
     the same time whatever the guess. No token configured → 404 as well; there
     is no state in which this endpoint answers without a secret.

  3. NETWORK ALLOWLIST. The caller's IP must fall inside one of the CIDRs in
     settings, OR the request must come from a browser page on this very site
     carrying a valid System Manager session (that is the operator using the
     Test Connection button on the settings form, which is a same-origin
     fetch). An empty allowlist denies everything rather than allowing
     everything — a blank field is much more likely a mistake than an intent to
     publish an accounting API to the internet.

WHY THE CLIENT NEVER LEARNS WHICH GATE IT FAILED. Every rejection is the same
opaque body. The *reason* is written to the MCP Action Log, where the operator
can read it and the caller cannot. Telling an unauthenticated caller "your IP
is fine, your token is wrong" hands them a free oracle for exactly the two
facts worth probing for.

TRUSTING X-Forwarded-For. Behind a reverse proxy `remote_addr` is the proxy, so
the real caller is only in the `X-Forwarded-For` chain. That header is
client-supplied, and bench's stock nginx *appends* to it rather than replacing
it (`$proxy_add_x_forwarded_for`), so its leftmost entry is whatever the client
felt like claiming. This module therefore gates on the RIGHTMOST entry — the
one the nearest proxy appended itself, which a client cannot forge — and falls
back to `remote_addr` when the header is absent. That is correct for the
one-reverse-proxy topology every stock Frappe deployment has. Behind two or
more proxy layers the rightmost entry is an inner proxy rather than the client,
and the CIDR gate stops being meaningful: on that topology rely on the bearer
token plus a firewall rule, per docs/security.md.
"""

import hmac
import ipaddress

import frappe

from . import settings
from .errors import AuthError

#: What every rejection says, whichever gate failed.
_OPAQUE = "unauthorized"


def authorize() -> str:
	"""Run all three gates. Returns the caller IP; raises AuthError otherwise.

	The returned IP is what the audit log records, and is the IP the allowlist
	was evaluated against, so a log row and a gate decision can never disagree.
	"""
	ip = caller_ip()

	if not settings.is_enabled():
		raise AuthError(
			"not found", http_status=404, log_reason="master switch (enabled) is off"
		)

	configured = settings.auth_token()
	if not configured:
		raise AuthError(
			"not found", http_status=404, log_reason="no auth_token configured"
		)

	presented = presented_token()
	if not presented or not hmac.compare_digest(presented, configured):
		raise AuthError(
			_OPAQUE,
			http_status=401,
			log_reason="bearer token missing or incorrect",
		)

	if not _network_allowed(ip):
		raise AuthError(
			_OPAQUE,
			http_status=403,
			log_reason=f"caller ip {ip or '<unknown>'} is outside allowed_cidrs",
		)

	return ip


def presented_token() -> str:
	"""The token the caller presented, from either accepted header.

	`Authorization: Bearer <token>` is the MCP norm and what every client sends
	by default, so it is the primary. `X-MCP-Token: <token>` is accepted as
	well because Frappe's own auth layer inspects `Authorization` first and
	routes a `Bearer` value into its OAuth2 validator: an unknown token is
	swallowed there and the request continues as Guest (which is what we want),
	but that is incidental framework behaviour rather than a promise. The second
	header is the supported escape hatch if a Frappe version ever starts
	rejecting unknown bearer tokens outright. See docs/security.md.
	"""
	header = frappe.get_request_header("Authorization") or ""
	if header[:7].lower() == "bearer ":
		token = header[7:].strip()
		if token:
			return token
	return (frappe.get_request_header("X-MCP-Token") or "").strip()


def caller_ip() -> str:
	"""The caller's IP as the allowlist and the audit log should see it.

	Rightmost `X-Forwarded-For` hop when proxied, else the socket peer. See the
	module docstring for why rightmost and not `frappe.local.request_ip` (which
	is the leftmost, i.e. spoofable, entry).
	"""
	forwarded = frappe.get_request_header("X-Forwarded-For") or ""
	hops = [h.strip() for h in forwarded.split(",") if h.strip()]
	if hops:
		return hops[-1]
	request = getattr(frappe.local, "request", None)
	return getattr(request, "remote_addr", "") or ""


def _network_allowed(ip: str) -> bool:
	if _is_same_origin_operator():
		return True
	cidrs = settings.allowed_cidrs()
	if not cidrs:
		return False
	try:
		address = ipaddress.ip_address(ip)
	except ValueError:
		# No parseable caller IP (a unit-test WSGI environ, an odd proxy). The
		# allowlist cannot be evaluated, so it cannot be satisfied.
		return False
	for entry in cidrs:
		try:
			network = ipaddress.ip_network(entry, strict=False)
		except ValueError:
			# A typo in the operator's list must not take out the whole gate;
			# skip the bad entry and keep checking the good ones.
			continue
		if address.version == network.version and address in network:
			return True
	return False


def _is_same_origin_operator() -> bool:
	"""True when this is an operator's own browser calling from this site.

	Both halves are required, and each closes the other's hole: the `Origin`
	check alone is worthless (any non-browser client can send any Origin), and
	the session check alone would let a logged-in user's browser be driven from
	a third-party page. Together they describe exactly one situation — a page
	served by this site, fetched by a System Manager who is signed in — which is
	the Test Connection button on the settings form.
	"""
	origin = frappe.get_request_header("Origin") or ""
	if not origin:
		return False
	request = getattr(frappe.local, "request", None)
	host = (getattr(request, "host", "") or "").lower()
	if not host or origin.split("//")[-1].strip("/").lower() != host:
		return False
	user = getattr(getattr(frappe.local, "session", None), "user", None) or "Guest"
	if user in ("Guest", None, ""):
		return False
	return "System Manager" in (frappe.get_roles(user) or [])
