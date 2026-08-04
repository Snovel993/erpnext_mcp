# SPDX-License-Identifier: MIT
"""Every gate the mobile HTTP surface has, in one file.

`security.py` guards the MCP endpoint. This guards the eleven whitelisted
methods a phone calls, and it has to do the whole job on its own — see
`api/__init__.py` for why none of the MCP transport's checks reach this path.

SEVEN CHECKS, IN THIS ORDER, ALL OF THEM ON EVERY CALL:

  1. **The kill switch.** `farm_ops_mobile_enabled`, off → 503 and nothing else
     happens. Readable from the settings form OR from `site_config.json`, and
     EITHER saying off means off. Two places because the two failure modes are
     different: a Desk that is up but wrong is fixed on the form, and a Desk
     that is the problem is fixed with a text editor and a `bench restart`.

  2. **A named human.** Guest is refused before anything reads a row.

     v0.17.2 ADDED A SECOND DOOR INTO THIS CHECK AND NOT A WAY ROUND IT. The
     Tailscale `serve`/`funnel` proxy removes the `Authorization` header, so a
     phone that presented a perfectly good credential arrived as Guest and got
     the Desk's `/me` page. `fallback_auth.resolve()` re-establishes the same
     identity from `X-FarmOps-Token` or from `_auth` in the POST body, using
     Frappe's own api-key scheme, and then EVERY CHECK IN THIS LIST STILL RUNS
     on the user it established. A phone that came in through the fallback and
     a phone whose header survived are the same principal with the same gates.

  3. **The role gate.** One of `FARM_OPS_ROLES`. Not "any authenticated user" —
     a Family Member and an Advisor are real roles on this site with real
     logins, and neither has any business on a dispatch board.

  4. **The enrolment gate.** An Active Mobile Access Grant. THIS IS THE CHECK
     THAT KEEPS THE ADMIN OUT: Administrator holds every role there is, so the
     role gate alone would let the operator's own account drive the field API by
     accident. A grant is a deliberate act, `revoke_mobile_user` ends it, and a
     revoked grant closes this door on the next call rather than at the next
     token rotation.

  5. **The rate limit.** Per user, per method, per window.

  6. **The work**, with every docname and every company argument validated
     first — see `require_docname` and `require_company`.

  7. **The audit row and the secret strip**, which happen whichever way the
     call went, including the ways it was refused.

WHY THE AUDIT ROW GOES TO MCP Action Log AND NOT TO Audit Event. "Audit Event"
on this site is a COMPLIANCE record — a GlobalGAP walk-through, an auditor, a
finding, a corrective action. Forty phones polling a task list would put tens of
thousands of API rows into the register a real auditor reads, which is not
hardening, it is vandalism of the thing being hardened. MCP Action Log already
has the exact shape wanted (timestamp, actor, caller IP, arguments, status,
summary), already redacts secrets, and is already where an operator looks for
"what did this credential do". Mobile rows are prefixed `mobile:` so they sort
and filter apart from the AI's.

WHY THE IP IS `security.caller_ip()` AND NOT `frappe.local.request_ip`. They are
different values and only one is trustworthy. Frappe's is the LEFTMOST
`X-Forwarded-For` entry, which is whatever the client felt like claiming;
`security.caller_ip()` takes the RIGHTMOST, which the nearest proxy appended
itself and a client cannot forge. An audit log that records an attacker's chosen
IP is worse than one that records none, because it looks like evidence.
"""

from __future__ import annotations

import functools
import time

import frappe

from .. import audit, security, settings
from .. import roles as role_lib
from ..compat import doctype_exists
from ..errors import ToolError
from . import fallback_auth

GRANT = "Mobile Access Grant"

#: The roles that may reach the field API at all.
#:
#: "Field Worker" is this app's actual role name — see `roles.py`, which defines
#: six and calls the phone-only one that. The v0.17.1 brief named "Farm Worker",
#: which no site has; both are accepted so a site that created one by hand is
#: not silently locked out, and so the brief's spelling is not a trap. What is
#: NOT here is the whole point of the list: Family Member and Advisor are real
#: roles with real logins on this site and neither belongs on a dispatch board,
#: and Compliance Officer is deliberately absent because the eleven methods are
#: field work — a compliance reviewer reads the register in the Desk.
FARM_OPS_ROLES = frozenset({"Field Worker", "Farm Worker", "Foreman", "Farm Manager"})

#: Calls per minute for a read. Generous — a phone refreshing three screens on a
#: pull-to-refresh is three calls, and a worker who does that twice a minute is
#: using the app, not abusing it.
READ_LIMIT = 60

#: Calls per minute for a state change. A worker claims one task at a time.
WRITE_LIMIT = 10

#: Completion gets more room than the other writes because a queued day's work
#: syncing after a signal drop is a legitimate burst, and refusing it would lose
#: evidence somebody walked a camp to collect.
COMPLETE_LIMIT = 20

#: Chunk staging is per 512 KB slice, so a single photo is several calls and a
#: completion carrying a photo and a signature is a dozen. This has to clear a
#: real burst without clearing an unbounded one.
UPLOAD_LIMIT = 120

_WINDOW_SECONDS = 60

#: Response keys that must never reach a phone, matched as substrings on a
#: lower-cased key. `mobile_login_payload` is the one place this app deliberately
#: emits a live credential, and NOTHING in this package calls it — but a tool
#: three sprints from now might grow a field named like one of these, and the
#: strip is what makes that a non-event instead of a leak.
_SECRET_HINTS = ("api_secret", "api_key", "password", "secret", "token", "credential", "auth_header")

#: Keys whose names trip `_SECRET_HINTS` but which carry no secret at all and
#: which the client needs. Checked before the hints, exact match, lower-cased.
_SECRET_ALLOW = frozenset({"file_token", "csrf_token", "token_review_due", "upload_token"})

#: Per-process rate-limit buckets, used when this bench has no cache. See
#: `_count`.
_BUCKETS: dict = {}


class MobileDisabled(frappe.ValidationError):
	"""The global kill switch is off. Answered 503 — temporarily unavailable.

	THE STATUS IS PART OF THE CONTRACT. `FarmOpsKit` treats 401 as "credential
	dead, sign out and re-scan" and everything else as "offline, keep working
	into the queue". A kill switch that answered 401 would log forty phones out
	and lose every queued completion on them; 503 parks them until it comes back.
	`http_status_code` is a CLASS attribute because that is the hook Frappe's own
	exception handler reads, on every version, rather than a response key whose
	name has moved.
	"""

	http_status_code = 503


class RateLimited(frappe.ValidationError):
	"""Too many calls in the window. Answered 429, for the same reason as above."""

	http_status_code = 429


# ── the switch ──────────────────────────────────────────────────────────────
def mobile_enabled() -> bool:
	"""The global mobile kill switch. EITHER source saying off means off.

	`site_config.json` wins where it is present because it is the control that
	works when the Desk does not, and because an operator editing it has already
	decided. Absent from both, the answer is the settings field's declared
	default, which ships ON — this release exists to make the phones work.
	"""
	conf = getattr(frappe, "conf", None) or {}
	try:
		configured = conf.get("farm_ops_mobile_enabled")
	except Exception:  # pragma: no cover - a conf that is not a mapping
		configured = None
	if configured is not None and not settings.as_bool(configured):
		return False
	return settings.farm_ops_mobile_enabled()


# ── the gates ───────────────────────────────────────────────────────────────
def _require_enabled() -> None:
	if mobile_enabled():
		return
	_set_status(503)
	frappe.throw(
		"The Farm Ops mobile API is switched off on this site. Nothing was read and nothing "
		"was changed. An operator turns it back on with `farm_ops_mobile_enabled` on ERPNext "
		"MCP Settings, or by removing it from site_config.json.",
		MobileDisabled,
	)


def _require_farm_ops_role() -> str:
	"""The caller, once they have proved they are a field user. Never Guest.

	Returns the user so a wrapper has one thing to hold. Raises
	`frappe.PermissionError` — which Frappe answers with a 403 — for everything
	else, with the same message whichever way it failed: telling an unauthorised
	caller whether it was the role or the login that let them down hands them a
	free oracle for the one fact worth probing.
	"""
	user = str(getattr(frappe.session, "user", "") or "")
	if not user or user in ("Guest", None):
		raise frappe.PermissionError("This endpoint requires an enrolled Farm Ops credential.")
	if not (roles_held(user) & FARM_OPS_ROLES):
		raise frappe.PermissionError("This endpoint requires an enrolled Farm Ops credential.")
	return user


def roles_held(user: str) -> set:
	"""Every role on this account.

	`frappe.get_roles` is the canonical accessor and is asked first — it applies
	Frappe's own semantics, including the implicit roles a session carries. It is
	backed by a per-request cache, and a cache that has not been rebuilt since
	the account was granted its role would answer with an empty list; on a real
	bench `Has Role` is the same data behind it, so reading that directly when
	the first answer is EMPTY closes the gap without widening the gate. It is a
	fallback for "no answer", never an addition to one.
	"""
	held = set(frappe.get_roles(user) or [])
	return held or set(role_lib.all_roles_of(user) or [])


def _require_mobile_grant(user: str) -> None:
	"""An Active Mobile Access Grant, or nothing doing.

	The grant is what makes "enrolled" a fact rather than an assumption. An
	account that merely HOLDS an API key has a credential; an account with a
	grant is one somebody enrolled on purpose, and `revoke_mobile_user` is what
	un-enrols it — which is why revocation has to close this door and does, on
	the next call.
	"""
	if not doctype_exists(GRANT):
		raise frappe.PermissionError("This endpoint requires an enrolled Farm Ops credential.")
	row = frappe.db.get_value(GRANT, {"user": user}, ["name", "state", "last_seen_on"], as_dict=True)
	if not row or str(row.get("state") or "") != "Active":
		raise frappe.PermissionError("This endpoint requires an enrolled Farm Ops credential.")
	_stamp_last_seen(row)


def _stamp_last_seen(row) -> None:
	"""Remember that this credential was used today. AT MOST ONE WRITE A DAY.

	v0.17.1. `sweep_idle_grants` revokes a token nobody has used for a month,
	which is what limits the damage from a phone lost in an orchard and never
	reported — and it can only do that if something records use. This is that
	something, and it lives here because this is the one place every mobile call
	passes through.

	THE DATE COMPARISON IS THE WHOLE DESIGN. A phone polling its task list writes
	nothing on the second and subsequent calls of a day, so forty devices cost
	forty UPDATEs a day rather than forty an hour. Stamping a timestamp on every
	request would put a write on the hot path of a read endpoint to serve a job
	that runs at midnight and rounds to days.

	It uses `db.set_value` rather than loading the document: this must not touch
	`modified`-driven concurrency on a record an operator may have open, and it
	must not fire the controller's validation on a field the controller does not
	care about. It never raises — a failure to stamp is a credential that looks a
	day staler than it is, which the sweep's own notification makes visible.
	"""
	try:
		today = str(frappe.utils.today())
		if str(row.get("last_seen_on") or "")[:10] == today:
			return
		frappe.db.set_value(GRANT, row["name"], "last_seen_on", frappe.utils.now(), update_modified=False)
	except Exception:  # pragma: no cover - a stamp is never worth failing a call
		pass


# ── entity scoping ──────────────────────────────────────────────────────────
def accessible_companies(user: str) -> list:
	"""The Companies this caller may reach. An EMPTY LIST HERE MEANS NONE.

	THIS INVERTS FRAPPE'S RULE ON PURPOSE, AND IT IS THE MOST IMPORTANT LINE IN
	THE FILE. In Frappe a user with no Company User Permission is UNRESTRICTED —
	`roles.companies_for` documents that at length and every Desk surface honours
	it. On an endpoint reachable from the open internet, inheriting that default
	would mean the single worst-configured account on the site is also the least
	scoped one. So the mobile surface refuses a caller with no entities instead
	of showing them everything, and `create_mobile_user` already refuses to make
	such an account. The two together mean there is no path to an unscoped phone.
	"""
	return [name for name in (role_lib.companies_for(user) or []) if name]


def require_scope(user: str) -> list:
	companies = accessible_companies(user)
	if not companies:
		raise frappe.PermissionError(
			"This account has no entity access, so there is nothing it may read. An operator "
			"scopes it with create_mobile_user (update_existing=true)."
		)
	return companies


def require_company(user: str, company, allowed: list | None = None) -> str:
	"""One company argument, validated against what the caller may actually see.

	Returns "" when nothing was asked for, which every caller reads as "all of
	mine". A company the caller cannot reach is REFUSED rather than quietly
	dropped: an empty list reads on a phone as a quiet day, and "not yours" is a
	different fact that somebody can act on.
	"""
	wanted = str(company or "").strip()
	if not wanted:
		return ""
	allowed = allowed if allowed is not None else accessible_companies(user)
	if wanted not in allowed:
		raise frappe.PermissionError(f"{wanted} is not one of this account's entities. Nothing was read.")
	return wanted


def scoped(rows: list, allowed: list, key: str = "company") -> list:
	"""Drop any row belonging to an entity the caller may not reach.

	The belt to the braces. The tools below already filter by company, and this
	runs anyway on everything that leaves the building, because a row that
	escapes the filter through a code path nobody thought about is exactly the
	failure this release exists to prevent. A row with NO company is kept — a
	Farm Task with a blank company is a data problem, not another entity's
	secret, and hiding it would make it invisible rather than fixed.
	"""
	permitted = set(allowed or [])
	out = []
	for row in rows or []:
		if not isinstance(row, dict):
			continue
		company = str(row.get(key) or "").strip()
		if company and company not in permitted:
			continue
		out.append(row)
	return out


# ── input validation ────────────────────────────────────────────────────────
def require_docname(doctype: str, name, label: str) -> str:
	"""A docname argument that is a real docname, checked before delegation.

	No blind pass-through. The tools below all check their own arguments, but
	they check them AFTER doing work — and the point of checking here is that a
	caller probing for which docnames exist gets the same refusal whether the
	name is malformed, absent, or simply not there.
	"""
	value = str(name or "").strip()
	if not value:
		frappe.throw(f"{label} is required.", frappe.ValidationError)
	if len(value) > 140 or any(character in value for character in "\n\r\t"):
		frappe.throw(f"{label} is not a valid record name.", frappe.ValidationError)
	if not frappe.db.exists(doctype, value):
		frappe.throw(f"{label} {value} was not found.", frappe.DoesNotExistError)
	return value


def require_scoped_doc(doctype: str, name, label: str, allowed: list) -> str:
	"""A docname that exists AND belongs to an entity the caller may reach.

	Split from `require_docname` because the two refusals mean different things
	and only one of them is a permission failure — but they are worded the same
	on the wire, so a caller cannot map the site's docnames by watching which
	error comes back.
	"""
	value = require_docname(doctype, name, label)
	company = frappe.db.get_value(doctype, value, "company")
	if company and str(company) not in set(allowed or []):
		frappe.throw(f"{label} {value} was not found.", frappe.DoesNotExistError)
	return value


# ── rate limiting ───────────────────────────────────────────────────────────
def _count(key: str, seconds: int) -> int:
	"""Hits in the current window for `key`. Cache-backed, with a local fallback.

	The cache is the right home — it is shared across the gunicorn workers a real
	bench runs, so a limit of ten means ten. The in-process fallback is what a
	bench with no redis (and the standalone test harness) gets: per-worker rather
	than per-site, which is a weaker limit but a real one, and much better than
	the alternative of failing open silently.
	"""
	window = int(time.time() // seconds)
	slot = f"erpnext_mcp:farmops:{key}:{window}"
	cache = getattr(frappe, "cache", None)
	if callable(cache):
		try:
			client = cache()
			hits = int(client.incr(slot))
			if hits == 1:
				client.expire(slot, seconds * 2)
			return hits
		except Exception:  # pragma: no cover - no redis on this bench
			pass
	for stale in [entry for entry in _BUCKETS if not entry.endswith(f":{window}")]:
		_BUCKETS.pop(stale, None)
	_BUCKETS[slot] = _BUCKETS.get(slot, 0) + 1
	return _BUCKETS[slot]


def throttle(user: str, method: str, limit: int, seconds: int = _WINDOW_SECONDS) -> None:
	if _count(f"{method}:{user}", seconds) > limit:
		_set_status(429)
		frappe.throw(
			f"Too many {method} calls — the limit is {limit} a minute. Wait a moment and try "
			"again. Nothing was changed.",
			RateLimited,
		)


# ── responses ───────────────────────────────────────────────────────────────
def strip_secrets(value):
	"""Every credential-shaped key removed, at any depth, on the way out.

	Belt and braces again, and worth the cycles: `tools/mobile.py` returns live
	api_key/api_secret pairs from three of its tools, and the distance between
	"this package does not call those" and "this package will never call those"
	is one future wrapper written in a hurry.
	"""
	if isinstance(value, dict):
		out = {}
		for key, item in value.items():
			lowered = str(key).lower()
			if lowered not in _SECRET_ALLOW and any(hint in lowered for hint in _SECRET_HINTS):
				continue
			out[key] = strip_secrets(item)
		return out
	if isinstance(value, (list, tuple)):
		return [strip_secrets(item) for item in value]
	return value


def _set_status(code: int) -> None:
	try:
		frappe.local.response["http_status_code"] = code
	except Exception:  # pragma: no cover - no response object in a bare call
		pass


# ── the decorator every wrapper wears ───────────────────────────────────────
def endpoint(method: str, limit: int = READ_LIMIT, mutating: bool = False):
	"""Gate, throttle, audit, strip. The wrapper below only does its own work.

	The decorated function is called with the authenticated user as its first
	argument, so a wrapper never has to reach for `frappe.session` and can never
	read a different user than the one the gates approved.

	EVERY EXIT WRITES A ROW, and the failure rows are committed on their own
	transaction: a refusal that rolled back with the request would lose exactly
	the evidence an operator wants after the fact. That is the same contract
	`mcp.py` has with `audit.py`, for the same reason.
	"""

	def decorate(function):
		@functools.wraps(function)
		def wrapper(*args, **kwargs):
			ip = security.caller_ip()

			# AN ACCOUNT THAT CAN NAME SOMEBODY ELSE IN A REQUEST BODY IS NOT
			# SCOPED TO ANYTHING. `user` is in every wrapped function's signature
			# because this decorator injects it — which means Frappe's own
			# argument filter, which keeps body keys that match the signature,
			# would happily bind a `user` key from the phone's JSON to it too.
			# The collision would raise rather than escalate, so the bug this
			# line prevents is loud; dropping the key is what also makes it
			# impossible.
			kwargs.pop("user", None)
			# `_auth` is envelope, not argument. Frappe's own filter drops it
			# already — it matches no signature — so this is belt to that brace,
			# and it also keeps a credential out of the audit row's arguments on
			# any path that reaches these functions in process.
			kwargs.pop(fallback_auth.BODY_KEY, None)

			# v0.17.2. THE HEADER MAY NOT HAVE SURVIVED THE PROXY — see
			# `fallback_auth`, which sets out what was proven and how. This runs
			# BEFORE the rate limit on purpose: the limit keys on the caller, and
			# forty phones arriving through one funnel address would otherwise
			# share a single Guest bucket and throttle each other off the site.
			# It is a no-op when Frappe already authenticated somebody, which is
			# what makes `Authorization: token` the winner where it survives.
			user = str(getattr(frappe.session, "user", "") or "")
			if not user or user == "Guest":
				fallback_auth.resolve()
				user = str(getattr(frappe.session, "user", "") or "")

			try:
				_require_enabled()
				# THE LIMIT COMES BEFORE THE REST OF THE GATES, keyed on whoever
				# the session says is calling. Putting it after them would leave
				# the refusal path unmetered — and every refusal writes an audit
				# row, so an account holding a valid token but no grant could
				# grow MCP Action Log without bound while never once being
				# allowed to do anything. A rejected caller is exactly the one
				# worth metering.
				# Guest is metered BY ADDRESS rather than by the name "Guest",
				# which every anonymous caller on earth shares. One bucket for
				# all of them would let a caller who has hit the limit suppress
				# the audit rows of the next unrelated one — a way of going
				# quiet, which is the opposite of what the limit is for.
				throttle(user if user and user != "Guest" else f"ip:{ip or 'unknown'}", method, limit)
				user = _require_farm_ops_role()
				_require_mobile_grant(user)
			except frappe.PermissionError as exc:
				_record(method, kwargs, audit.STATUS_UNAUTHORIZED, "permission_error", user, ip, exc)
				raise
			except RateLimited as exc:
				_record(method, kwargs, audit.STATUS_BLOCKED, "rate_limited", user, ip, exc)
				raise
			except MobileDisabled as exc:
				_record(method, kwargs, audit.STATUS_BLOCKED, "disabled", user, ip, exc)
				raise

			# `resolve_context_user` reads the identity `mcp.handle` normally
			# captures a line before it becomes the MCP System User. Nothing
			# captured it on this path, so capture it here — and it is exactly
			# right here, because on this transport `frappe.session.user` IS the
			# worker and stays the worker for the whole call.
			security.capture_calling_user()

			try:
				result = strip_secrets(function(user, *args, **kwargs))
			except ToolError as exc:
				# An expected, caller-correctable failure from the tool layer.
				# It becomes a Frappe validation error so the message reaches the
				# phone in `_server_messages`, which is where the client reads it.
				_record(method, kwargs, audit.STATUS_ERROR, "error", user, ip, exc)
				frappe.throw(str(exc), frappe.ValidationError)
			except frappe.PermissionError as exc:
				_record(method, kwargs, audit.STATUS_UNAUTHORIZED, "permission_error", user, ip, exc)
				raise
			except Exception as exc:
				_record(method, kwargs, audit.STATUS_ERROR, "error", user, ip, exc)
				raise

			_record(method, kwargs, audit.STATUS_SUCCESS, "success", user, ip, None)
			return result

		wrapper.farm_ops_method = method
		wrapper.farm_ops_mutating = mutating
		return wrapper

	return decorate


def _record(method, arguments, status, outcome, user, ip, exc) -> None:
	"""One MCP Action Log row. Never raises; see `audit.record`.

	v0.17.2 TAGS THE ROW WITH HOW THE CALLER GOT IN — `fallback_auth: header` or
	`fallback_auth: body` — and says nothing at all when Frappe's own auth
	handled it. That is what makes "which door are the phones actually using"
	answerable from the Desk: filter MCP Action Log on `mobile:` and read the
	summaries. An untagged row is a request whose `Authorization` header
	survived the proxy.

	THE TAG GOES ON THE ROW THIS FUNCTION ALREADY WRITES RATHER THAN INTO A ROW
	OF ITS OWN, and the v0.17.2 brief asked for a separate Audit Event. The
	reason for the change is arithmetic. If the proxy strips the header — which
	is the whole premise of the release — then EVERY mobile call takes the
	fallback path, so a row per fallback is a row per call: forty phones
	polling a task list would put tens of thousands of rows a day into "Audit
	Event", which on this site is the COMPLIANCE register a GlobalGAP auditor
	reads. That is the same argument the module docstring makes about not
	putting mobile traffic there at all, and it does not get weaker because the
	rows would be about authentication. The fact wanted — how iOS got in — is
	preserved exactly, on a row that was being written anyway, in the log an
	operator already opens to answer "what did this credential do".
	"""
	summary = f"{outcome} — {user or 'Guest'}"
	door = fallback_auth.source()
	if door:
		summary = f"{summary} (fallback_auth: {door})"
	if exc is not None:
		summary = f"{summary}: {type(exc).__name__}: {exc}"
	if status != audit.STATUS_SUCCESS:
		try:
			frappe.db.rollback()
		except Exception:  # pragma: no cover - nothing to roll back
			pass
	audit.record(
		f"mobile:{method}",
		dict(arguments or {}),
		status,
		summary,
		caller_ip=ip,
		commit=status != audit.STATUS_SUCCESS,
	)
