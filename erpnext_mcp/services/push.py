# SPDX-License-Identifier: MIT
"""APNs, and the break horn that has to reach a crew rather than a foreman.

WHAT THIS IS FOR. `BreakAlarm` on the handset plays a tone the moment a foreman
calls a break — over an `AVAudioSession` in `.playback`, so it rings through the
silent switch, because a break horn that respects a muted phone does not work on
most of a crew's phones. That is exactly one phone: the one the break was called
on. Every other worker on the shift learns about the break when somebody shouts.
This module is the other twenty phones.

────────────────────────────────────────────────────────────────────────────
IT DEGRADES TO NOTHING, AND THAT IS THE DESIGN
────────────────────────────────────────────────────────────────────────────

A p8 signing key is an operator artefact: somebody downloads it from Apple once,
puts it on the bench, and never touches it again. Until that has happened this
module has no way to send anything, and the thing it must not do in the meantime
is take a break log down with it. So:

  * Every entry point returns a REPORT and never raises. `sent`, `skipped`,
    `failed` and a `reason` naming which of those it was.
  * An unconfigured site is `skipped` with `reason: "not_configured"` and one
    Error Log line, not an exception and not a silent zero. The distinction
    matters when a foreman asks why nobody's phone buzzed: "no key on this
    bench" and "sent to nobody because the crew has no tokens" are different
    problems with different people to go and see.
  * A missing HTTP/2 client is the same shape. APNs is HTTP/2-only, `requests`
    speaks HTTP/1.1, and this app does not add a dependency to ship a feature
    the site cannot use yet — so `httpx` is imported defensively and its absence
    is a named skip, exactly as Pillow's is in `wallet.py`.

────────────────────────────────────────────────────────────────────────────
THE KEY LIVES IN site_config.json AND NOT IN A SETTINGS DOCTYPE
────────────────────────────────────────────────────────────────────────────

The same argument `wallet.apple_config` makes, for the same reason. A Single
doctype is editable by anybody who reaches the Desk with the right role and is
dumped in full by a dozen Frappe debug paths; `site_config.json` is a file on
the bench that only the operator who deploys the site can write. An ES256
private key belongs in the second kind of place.

`configured` is FOUR separate facts ANDed rather than one flag an operator could
tick while leaving the key out: without the key there is nothing to sign with,
without the key id Apple cannot tell which key signed, without the team id the
token names no developer account, and without the topic the push is addressed to
no app. A push missing any one of them is rejected by Apple with a 403 that says
nothing useful, which is a worse failure than not sending.

────────────────────────────────────────────────────────────────────────────
WHY THE TRANSPORT IS AN ARGUMENT
────────────────────────────────────────────────────────────────────────────

`send_push` takes a `transport` callable. The default one talks to Apple; the
suite passes a fake. That seam is here rather than a `unittest.mock.patch` on a
module global because the dispatch decisions this module makes — which tokens to
address, what to do with a 410, whether a crew break with no tokens is a failure
— are the part worth testing, and they are unreachable behind a real HTTP client
that no test may call.
"""

from __future__ import annotations

import json
import time

import frappe

from .. import compat

TOKEN_DOCTYPE = "Mobile Push Token"
SHIFT_DOCTYPE = "Farm Shift"

#: Apple's two hosts. `sandbox` is what a development build's tokens are minted
#: against, and a token from one host is meaningless on the other — a push to
#: the wrong one comes back BadDeviceToken, which reads exactly like a stale
#: token and is not one. Hence `apns_environment` being explicit configuration
#: rather than something inferred.
HOSTS = {
	"production": "https://api.push.apple.com",
	"sandbox": "https://api.sandbox.push.apple.com",
}

#: How long a provider JWT is reused. Apple rejects a token older than an hour
#: and rate-limits a provider that mints a fresh one per push, so the window is
#: deliberately in the middle of those two failures rather than at either end.
JWT_LIFETIME_SECONDS = 45 * 60

#: The two sounds the app has bundled. Named here because the server chooses
#: which one plays: a rising double blast when a break starts, a descending
#: triple pip when it ends. Deliberately unlike each other and unlike any system
#: sound — a worker in an orchard has to know which one they just heard without
#: taking the phone out.
SOUND_BREAK_START = "break_start.caf"
SOUND_BREAK_END = "break_end.caf"

#: `time-sensitive` is the whole point of the payload. A break horn is worthless
#: if Focus or a Scheduled Summary holds it until lunchtime, and this is the
#: interruption level Apple provides for exactly this case. It requires the
#: entitlement on the app side, which the handset already ships.
INTERRUPTION_LEVEL = "time-sensitive"

#: What Apple answers when the token is dead. BOTH DEACTIVATE THE ROW and
#: nothing else does: `Unregistered` means the app was deleted, `BadDeviceToken`
#: means the string is not a token for this topic and environment. Everything
#: else — `TooManyRequests`, `InternalServerError`, `ServiceUnavailable` — is
#: about Apple or about this farm's configuration, and deactivating a worker's
#: phone because Apple had a bad afternoon would silently unsubscribe a crew.
DEAD_TOKEN_REASONS = frozenset({"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"})

#: The sentence an operator is told when nothing can be sent. Names the keys,
#: not the concept — "configure APNs" is not an actionable instruction.
APNS_REQUIREMENTS = (
	"an Apple Push Notification key. Set `apns_key` (the contents of the .p8, or a path "
	"to it), `apns_key_id` (the ten-character Key ID Apple showed when it was created), "
	"`apns_team_id` (the ten-character Team ID) and `apns_topic` (the app's bundle "
	"identifier) in the site's site_config.json, plus `apns_environment` as production or "
	"sandbox. Until then breaks are logged exactly as before and no push is attempted."
)


# ── logging ─────────────────────────────────────────────────────────────────


def _log(message: str) -> None:
	"""Say what went wrong, without ever being the thing that goes wrong.

	`frappe.log_error` writes an Error Log row, which is a database write, which
	is a thing that can fail on the site where this is already failing. The break
	that could not be pushed is still a break that was logged.
	"""
	try:
		frappe.log_error(title="erpnext_mcp: push", message=message)
	except Exception:  # pragma: no cover - a site that cannot write its own Error Log
		pass


# ── configuration ───────────────────────────────────────────────────────────


def _text(conf, key: str, default: str = "") -> str:
	return str((conf or {}).get(key) or default).strip()


def _key_material(raw: str) -> str:
	"""The p8 contents, whether the operator configured the text or a path to it.

	Both spellings are accepted because both are reasonable and an operator
	should not have to guess which one this app wanted. A path that cannot be
	read comes back empty, which makes `configured` false, which produces the
	named skip rather than a signing failure at push time.
	"""
	if not raw:
		return ""
	if "PRIVATE KEY" in raw:
		return raw
	try:
		with open(raw, encoding="utf-8") as handle:
			return handle.read().strip()
	except OSError:
		_log(f"apns_key points at {raw!r}, which cannot be read. No push will be sent.")
		return ""


def apns_config(conf=None) -> dict:
	"""What this site has been told about APNs. Never raises.

	`conf` defaults to `frappe.conf`, and is an argument so the suite can hand it
	a dict without writing to a global.
	"""
	if conf is None:
		conf = getattr(frappe, "conf", None) or {}

	key = _key_material(_text(conf, "apns_key"))
	key_id = _text(conf, "apns_key_id")
	team_id = _text(conf, "apns_team_id")
	topic = _text(conf, "apns_topic")
	environment = _text(conf, "apns_environment", "production").lower()
	if environment not in HOSTS:
		environment = "production"

	return {
		"key": key,
		"key_id": key_id,
		"team_id": team_id,
		"topic": topic,
		"environment": environment,
		"host": HOSTS[environment],
		# Four facts ANDed rather than one flag. See the module docstring.
		"configured": bool(key and key_id and team_id and topic),
	}


def _missing(config: dict) -> list:
	"""Which of the four is absent, for the operator who has to fix it."""
	return [
		name
		for name, key in (
			("apns_key", "key"),
			("apns_key_id", "key_id"),
			("apns_team_id", "team_id"),
			("apns_topic", "topic"),
		)
		if not config.get(key)
	]


# ── the provider token ──────────────────────────────────────────────────────

#: (key_id, team_id) → (jwt, minted_at). Module-level, so per worker process and
#: not surviving a restart — the same trade `weather._CACHE` makes, and right for
#: the same reason: a cold cache costs one signature, and the alternatives are a
#: redis key or a table, each of which is a new failure mode in the path whose
#: entire job is not to have any.
_JWT_CACHE: dict = {}


def _clock() -> float:  # pragma: no cover - trivial, and patched in the suite
	return time.time()


def _b64(raw: bytes) -> str:
	import base64

	return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def provider_token(config: dict):
	"""The ES256 JWT Apple wants in `authorization`, or None with a logged reason.

	`cryptography` is imported here rather than at module scope for the promise
	the rest of this app makes: a bench missing an optional library loses a
	feature BY NAME instead of failing to import. It ships with Frappe, so this
	is belt and braces rather than a likely path.
	"""
	cached = _JWT_CACHE.get((config["key_id"], config["team_id"]))
	if cached and _clock() - cached[1] < JWT_LIFETIME_SECONDS:
		return cached[0]

	try:
		from cryptography.hazmat.primitives import hashes, serialization
		from cryptography.hazmat.primitives.asymmetric import ec
		from cryptography.hazmat.primitives.asymmetric import utils as asym_utils
	except Exception:  # pragma: no cover - a bench without cryptography
		_log(
			"the `cryptography` package is not importable on this bench, so an APNs provider "
			"token cannot be signed. `pip install cryptography` in the bench environment."
		)
		return None

	try:
		private_key = serialization.load_pem_private_key(config["key"].encode("utf-8"), password=None)
	except Exception as error:
		_log(f"apns_key is not a readable PEM private key ({error}). No push will be sent.")
		return None

	issued = int(_clock())
	header = _b64(json.dumps({"alg": "ES256", "kid": config["key_id"]}, separators=(",", ":")).encode())
	claims = _b64(json.dumps({"iss": config["team_id"], "iat": issued}, separators=(",", ":")).encode())
	signing_input = f"{header}.{claims}".encode("ascii")

	try:
		der = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
		# Apple wants the raw 64-byte r||s pair, and `cryptography` signs to DER.
		# Handing it the DER blob is the classic version of this bug: it produces
		# a perfectly well-formed JWT that Apple rejects with InvalidProviderToken.
		r, s = asym_utils.decode_dss_signature(der)
		signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
	except Exception as error:  # pragma: no cover - a key of the wrong curve
		_log(f"could not sign an APNs provider token ({error}). No push will be sent.")
		return None

	token = f"{header}.{claims}.{_b64(signature)}"
	_JWT_CACHE[(config["key_id"], config["team_id"])] = (token, _clock())
	return token


# ── payloads ────────────────────────────────────────────────────────────────


def break_payload(
	break_kind: str,
	phase: str,
	duration_minutes=None,
	shift: str = "",
	event: str = "",
) -> dict:
	"""The APNs payload for a break starting or ending.

	`phase` is "start" or "end", and it decides the sound: the app has both
	`.caf` files bundled and plays whichever the payload names through the same
	`BreakAlarm` code path the local tone uses.

	The custom keys sit BESIDE `aps` rather than inside it. Apple owns `aps` and
	will one day add a key this app has already used for something else; the
	handset reads `break_kind`, `shift` and `event` from the top level.
	"""
	ending = str(phase or "").strip().lower() == "end"
	kind = str(break_kind or "Break").strip()

	if ending:
		title = "Break over"
		body = f"{kind} has ended — back to work."
	else:
		minutes = None
		try:
			minutes = round(float(duration_minutes))
		except (TypeError, ValueError):
			minutes = None
		title = "Break time"
		body = f"{kind} starting now" + (f" — {minutes} minutes." if minutes else ".")

	payload = {
		"aps": {
			"alert": {"title": title, "body": body},
			"sound": SOUND_BREAK_END if ending else SOUND_BREAK_START,
			"interruption-level": INTERRUPTION_LEVEL,
			# A break horn is worth exactly one badge-free alert. `content-available`
			# is deliberately absent: this is a user-facing interruption, not a
			# background fetch, and mixing the two makes Apple throttle it.
			"category": "FARM_BREAK",
		},
		"break_kind": kind,
		"phase": "end" if ending else "start",
	}
	if duration_minutes not in (None, ""):
		payload["duration_minutes"] = duration_minutes
	if shift:
		payload["shift"] = shift
	if event:
		payload["event"] = event
	return payload


# ── who to send to ──────────────────────────────────────────────────────────


def active_tokens_for_employees(employees) -> list:
	"""Every active token belonging to any of these Employees.

	An empty list of employees returns an empty list WITHOUT a query. Frappe
	reads `{"in": []}` as no filter at all on some backends, which would turn a
	crew of nobody into a push to the whole farm.
	"""
	names = [str(name).strip() for name in (employees or []) if str(name or "").strip()]
	if not names:
		return []
	if not compat.doctype_exists(TOKEN_DOCTYPE):
		return []
	return (
		frappe.db.get_all(
			TOKEN_DOCTYPE,
			filters={"employee": ["in", names], "is_active": 1},
			fields=["name", "employee", "employee_name", "user", "platform", "device_id", "token"],
			limit_page_length=0,
		)
		or []
	)


def shift_crew_employees(shift_name: str, include_departed: bool = False) -> list:
	"""The Employees on a shift, read THROUGH the shift document.

	Not by filtering the child doctype on `parent`: that works on a bench and
	returns nothing at all under the standalone double, and a crew lookup that
	silently answers "nobody" is a break horn that reaches no one with no error
	to show for it.

	A worker who has clocked out is excluded by default. They are not on this
	break, their phone is elsewhere, and a tone that rings through the silent
	switch is not a thing to send to somebody who went home.
	"""
	if not shift_name or not compat.doctype_exists(SHIFT_DOCTYPE):
		return []
	try:
		doc = frappe.get_doc(SHIFT_DOCTYPE, shift_name)
	except Exception:
		return []

	employees = []
	for row in getattr(doc, "crew", None) or []:
		get = row.get if hasattr(row, "get") else lambda key, _row=row: getattr(_row, key, None)
		employee = get("employee")
		if not employee:
			continue
		if not include_departed and get("left_at"):
			continue
		if employee not in employees:
			employees.append(employee)
	return employees


# ── the transport ───────────────────────────────────────────────────────────


def _httpx():
	"""httpx, or None. APNs is HTTP/2-only and `requests` speaks HTTP/1.1."""
	try:
		import httpx

		return httpx
	except Exception:  # pragma: no cover - a bench without httpx
		return None


def _apns_transport(url: str, headers: dict, body: dict, timeout: float = 10.0) -> dict:
	"""One POST to Apple. Returns `{"status": int, "reason": str}`; never raises.

	The default `transport` for `send_push`, and the only function in this module
	that touches the network — which is what makes every decision above it
	testable without one.
	"""
	httpx = _httpx()
	if httpx is None:
		return {"status": 0, "reason": "no_http2_client"}
	try:
		with httpx.Client(http2=True, timeout=timeout) as client:
			response = client.post(url, headers=headers, json=body)
		reason = ""
		if response.status_code != 200:
			try:
				reason = str((response.json() or {}).get("reason") or "")
			except Exception:
				reason = (response.text or "")[:200]
		return {"status": response.status_code, "reason": reason}
	except Exception as error:
		return {"status": 0, "reason": f"transport_error: {error}"}


def _deactivate(token_name: str, reason: str) -> None:
	"""Retire a row Apple has told us is dead. Never raises, never deletes."""
	try:
		frappe.db.set_value(
			TOKEN_DOCTYPE,
			token_name,
			{"is_active": 0, "last_error": reason[:140]},
			update_modified=False,
		)
	except Exception:  # pragma: no cover - a write that fails is a token retried once more
		pass


def send_push(tokens, payload: dict, transport=None, conf=None) -> dict:
	"""Deliver one payload to a list of token rows. Returns a report; never raises.

	A row Apple rejects as `Unregistered` or `BadDeviceToken` is deactivated here
	and not retried — that is the only place in this app where a worker's handset
	is unsubscribed, and it happens on Apple's word rather than on a guess.
	`TopicDisallowed` and the rest deliberately do NOT deactivate: those say
	something about this farm's configuration, and treating them the same way
	would unsubscribe the whole crew over one wrong line in site_config.json.
	"""
	report = {
		"sent": 0,
		"failed": 0,
		"skipped": 0,
		"deactivated": 0,
		"reason": "",
		"attempted": len(tokens or []),
		"failures": [],
	}

	if not tokens:
		report["reason"] = "no_tokens"
		report["skipped"] = 0
		return report

	config = apns_config(conf)
	if not config["configured"]:
		report["skipped"] = len(tokens)
		report["reason"] = "not_configured"
		_log(
			f"{len(tokens)} handset(s) would have been pushed to, and this site has no APNs "
			f"configuration: missing {', '.join(_missing(config))}. It needs {APNS_REQUIREMENTS}"
		)
		return report

	jwt = provider_token(config)
	if not jwt:
		report["skipped"] = len(tokens)
		report["reason"] = "no_provider_token"
		return report

	send = transport or _apns_transport
	body = dict(payload or {})

	for row in tokens:
		device_token = str((row or {}).get("token") or "").strip()
		name = str((row or {}).get("name") or "")
		if not device_token:
			report["skipped"] += 1
			continue

		headers = {
			"authorization": f"bearer {jwt}",
			"apns-topic": config["topic"],
			"apns-push-type": "alert",
			# 10 is "deliver now". A break horn that arrives when the phone next
			# wakes up is not a break horn.
			"apns-priority": "10",
		}
		try:
			answer = send(f"{config['host']}/3/device/{device_token}", headers, body) or {}
		except Exception as error:  # pragma: no cover - a transport that raises anyway
			answer = {"status": 0, "reason": f"transport_error: {error}"}

		status = int(answer.get("status") or 0)
		reason = str(answer.get("reason") or "")
		if status == 200:
			report["sent"] += 1
			continue

		report["failed"] += 1
		report["failures"].append({"token": name, "status": status, "reason": reason})
		if reason in DEAD_TOKEN_REASONS:
			_deactivate(name, reason)
			report["deactivated"] += 1

	if report["sent"]:
		report["reason"] = "sent"
	elif not report["reason"]:
		report["reason"] = "all_failed"
	return report


def send_push_to_shift_crew(shift_name: str, payload: dict, transport=None, conf=None) -> dict:
	"""Push one payload to every phone on a shift. Returns a report; never raises.

	The entry point `log_shift_break` and `end_shift_break` call. It reports
	`crew` and `tokens` separately on purpose: a crew of eight with two tokens is
	a farm where six people never enrolled a handset, and that is a different
	conversation from a crew of eight where the push failed.
	"""
	report = {
		"shift": shift_name,
		"crew": 0,
		"tokens": 0,
		"sent": 0,
		"failed": 0,
		"skipped": 0,
		"deactivated": 0,
		"reason": "",
	}
	try:
		employees = shift_crew_employees(shift_name)
		report["crew"] = len(employees)
		tokens = active_tokens_for_employees(employees)
		report["tokens"] = len(tokens)
		if not tokens:
			report["reason"] = "no_tokens"
			return report
		report.update(
			{
				key: value
				for key, value in send_push(tokens, payload, transport, conf).items()
				if key in ("sent", "failed", "skipped", "deactivated", "reason")
			}
		)
	except Exception as error:  # pragma: no cover - the whole point of the wrapper
		report["reason"] = f"error: {error}"
		_log(f"crew push for shift {shift_name!r} failed: {error}")
	return report
